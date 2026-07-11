"""
Tests del dashboard de disponibilidad sin tocar la base de datos.

Todas las funciones que leen de PostgreSQL se reemplazan con monkeypatch
sobre el namespace de `app.services.availability_dashboard`.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest

from app.services import availability_dashboard as dashboard


# ── Helpers ─────────────────────────────────────────────────────────────────


def _row(
    plate: str,
    customer_id: int,
    customer_name: str,
    status: str = "calculated",
    pct: float | None = 100.0,
    h_total: float | None = 720.0,
    h_no_disp: float | None = 0.0,
) -> dict[str, Any]:
    return {
        "plate": plate,
        "calculation_status": status,
        "project_availability_pct": pct,
        "h_total": h_total,
        "h_no_disp": h_no_disp,
        "orders_considered": 0,
        "customer_id": customer_id,
        "customer_name": customer_name,
    }


# ── get_availability_overview ───────────────────────────────────────────────


def test_overview_grouping_and_global_metrics(monkeypatch):
    """Agrupacion por cliente, pct global y conteo incluyen not_in_cloudfleet."""
    rows = [
        _row("A01", 1, "Flota A", "calculated", 100.0, 720.0, 0.0),
        _row("A02", 1, "Flota A", "calculated", 100.0, 720.0, 0.0),
        _row("B01", 2, "Flota B", "calculated", 90.0, 720.0, 72.0),
        _row("B02", 2, "Flota B", "calculated", 90.0, 720.0, 72.0),
        _row("B03", 2, "Flota B", "not_in_cloudfleet", None, None, None),
    ]
    monkeypatch.setattr(
        "app.services.availability_dashboard._fetch_month_rows",
        lambda month, customer_id=None: rows,
    )

    overview = dashboard.get_availability_overview("2026-06")

    overall = overview["overall"]
    assert overall["vehicle_count"] == 5
    assert overall["fleet_count"] == 2
    assert overall["h_total"] == 2880.0
    assert overall["h_no_disp"] == 144.0
    assert overall["availability_pct"] == round((2880.0 - 144.0) / 2880.0 * 100.0, 3)
    assert overall["status"] == "critical"
    assert overall["status_breakdown"]["calculated"] == 4
    assert overall["status_breakdown"]["not_in_cloudfleet"] == 1
    assert overall["critical_fleets"] == 1

    fleets = overview["fleets"]
    assert len(fleets) == 2
    # Peor pct primero.
    assert fleets[0]["customer_name"] == "Flota B"
    assert fleets[0]["vehicle_count"] == 3
    assert fleets[0]["availability_pct"] == 90.0
    assert fleets[0]["status"] == "critical"
    assert fleets[1]["customer_name"] == "Flota A"
    assert fleets[1]["availability_pct"] == 100.0
    assert fleets[1]["status"] == "good"


# ── _classify ────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "pct,expected",
    [
        (97.0, "good"),
        (96.5, "warning"),
        (95.9, "critical"),
        (None, "no_data"),
    ],
)
def test_classify_thresholds(pct, expected):
    """Umbrales good/warning/critical/no_data."""
    assert dashboard._classify(pct) == expected


# ── get_vehicle_ranking ─────────────────────────────────────────────────────


def test_ranking_filters_and_ordering(monkeypatch):
    """Solo filas calculated con pct; orden worst ascendente / best descendente."""
    rows = [
        _row("A01", 1, "Flota A", "calculated", 95.0, 720.0, 36.0),
        _row("A02", 1, "Flota A", "calculated", 98.0, 720.0, 14.4),
        _row("A03", 1, "Flota A", "no_orders", 100.0, 720.0, 0.0),
        _row("A04", 1, "Flota A", "not_in_cloudfleet", None, None, None),
        _row("A05", 1, "Flota A", "calculated", None, 720.0, 0.0),
        _row("A06", 1, "Flota A", "error", 90.0, 720.0, 72.0),
    ]
    monkeypatch.setattr(
        "app.services.availability_dashboard._fetch_month_rows",
        lambda month, customer_id=None: rows,
    )

    worst = dashboard.get_vehicle_ranking("2026-06", order="worst", limit=10)
    assert [r["plate"] for r in worst] == ["A01", "A02"]
    assert worst[0]["availability_pct"] == 95.0
    assert worst[1]["availability_pct"] == 98.0

    best = dashboard.get_vehicle_ranking("2026-06", order="best", limit=10)
    assert [r["plate"] for r in best] == ["A02", "A01"]

    limited = dashboard.get_vehicle_ranking("2026-06", order="worst", limit=1)
    assert len(limited) == 1
    assert limited[0]["plate"] == "A01"


# ── _add_months ──────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "month,delta,expected",
    [
        ("2026-01", -1, "2025-12"),
        ("2026-07", -6, "2026-01"),
        ("2025-11", 3, "2026-02"),
    ],
)
def test_add_months(month, delta, expected):
    """Aritmetica de meses con _add_months."""
    assert dashboard._add_months(month, delta) == expected


# ── get_availability_trend ──────────────────────────────────────────────────


def test_trend_three_months_with_middle_data(monkeypatch):
    """Trend de 3 meses con datos solo en el mes central."""
    rows = [
        {
            "plate": "T01",
            "period_month": "2026-05",
            "calculation_status": "calculated",
            "project_availability_pct": 100.0,
            "h_total": 720.0,
            "h_no_disp": 0.0,
            "orders_considered": 0,
        }
    ]
    monkeypatch.setattr(
        "app.services.availability_dashboard.list_monthly_availability",
        lambda *, month_from, month_to: rows,
    )
    monkeypatch.setattr(
        "app.services.availability_dashboard._plates_for_customer",
        lambda customer_id: {"T01"},
    )

    trend = dashboard.get_availability_trend("2026-06", months=3)
    assert trend["month_from"] == "2026-04"
    assert trend["month_to"] == "2026-06"
    assert trend["labels"] == ["2026-04", "2026-05", "2026-06"]
    assert trend["availability_pct"] == [None, 100.0, None]


def test_trend_filters_by_customer_plates(monkeypatch):
    """Con customer_id solo se agregan placas pertenecientes al cliente."""
    rows = [
        {
            "plate": "OWN01",
            "period_month": "2026-05",
            "calculation_status": "calculated",
            "project_availability_pct": 100.0,
            "h_total": 720.0,
            "h_no_disp": 0.0,
            "orders_considered": 0,
        },
        {
            "plate": "OTHER01",
            "period_month": "2026-05",
            "calculation_status": "calculated",
            "project_availability_pct": 50.0,
            "h_total": 720.0,
            "h_no_disp": 360.0,
            "orders_considered": 0,
        },
    ]
    monkeypatch.setattr(
        "app.services.availability_dashboard.list_monthly_availability",
        lambda *, month_from, month_to: rows,
    )
    monkeypatch.setattr(
        "app.services.availability_dashboard._plates_for_customer",
        lambda customer_id: {"OWN01"},
    )

    trend = dashboard.get_availability_trend("2026-06", months=3, customer_id=7)
    assert trend["customer_id"] == 7
    assert trend["availability_pct"] == [None, 100.0, None]


# ── agregacion con Decimal ──────────────────────────────────────────────────


def test_overview_aggregation_handles_decimal(monkeypatch):
    """La DB devuelve NUMERIC como Decimal; la agregacion sigue funcionando."""
    rows = [
        _row(
            "D01",
            1,
            "Flota Decimal",
            "calculated",
            Decimal("90.000"),
            Decimal("720.000"),
            Decimal("72.000"),
        ),
        _row(
            "D02",
            1,
            "Flota Decimal",
            "calculated",
            Decimal("100.000"),
            Decimal("720.000"),
            Decimal("0.000"),
        ),
    ]
    monkeypatch.setattr(
        "app.services.availability_dashboard._fetch_month_rows",
        lambda month, customer_id=None: rows,
    )

    overview = dashboard.get_availability_overview("2026-06")
    overall = overview["overall"]
    assert overall["h_total"] == 1440.0
    assert overall["h_no_disp"] == 72.0
    expected_pct = round((1440.0 - 72.0) / 1440.0 * 100.0, 3)
    assert overall["availability_pct"] == expected_pct
    assert overall["availability_pct"] == 95.0


# ── _fetch_month_rows query ─────────────────────────────────────────────────


def test_fetch_month_rows_query_excludes_system_customer(monkeypatch):
    """El SQL generado por _fetch_month_rows excluye al customer sistema."""
    captured: dict[str, Any] = {}

    class FakeCursor:
        def execute(self, sql: str, params=None):
            captured["sql"] = sql
            captured["params"] = list(params or [])

        def fetchall(self):
            return []

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    class FakeConn:
        def cursor(self, *args, **kwargs):
            return FakeCursor()

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    monkeypatch.setattr(
        "app.services.availability_dashboard._ensure_availability_table",
        lambda: None,
    )
    monkeypatch.setattr(
        "app.services.availability_dashboard.psycopg.connect",
        lambda *args, **kwargs: FakeConn(),
    )

    dashboard._fetch_month_rows("2026-06")

    assert "__navitrans_system__" in captured["params"]
    assert "(c.name IS NULL OR c.name <> %s)" in captured["sql"]
