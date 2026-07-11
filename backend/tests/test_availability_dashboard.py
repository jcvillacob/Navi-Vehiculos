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
    mttr_hours: float | None = None,
    orders_closed: int = 0,
) -> dict[str, Any]:
    return {
        "plate": plate,
        "calculation_status": status,
        "project_availability_pct": pct,
        "h_total": h_total,
        "h_no_disp": h_no_disp,
        "orders_considered": 0,
        "mttr_hours": mttr_hours,
        "orders_closed": orders_closed,
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


def test_overview_mttr_weighted_aggregation(monkeypatch):
    """MTTR de flota es el promedio ponderado por ordenes cerradas."""
    rows = [
        _row("A01", 1, "Flota A", "calculated", 100.0, 720.0, 0.0, mttr_hours=24.0, orders_closed=1),
        _row("A02", 1, "Flota A", "calculated", 100.0, 720.0, 0.0, mttr_hours=48.0, orders_closed=1),
    ]
    monkeypatch.setattr(
        "app.services.availability_dashboard._fetch_month_rows",
        lambda month, customer_id=None: rows,
    )

    overview = dashboard.get_availability_overview("2026-06")

    overall = overview["overall"]
    assert overall["mttr_hours"] == 36.0
    assert overall["orders_closed"] == 2
    assert overall["mttr_status"] == "warning"

    fleet = overview["fleets"][0]
    assert fleet["mttr_hours"] == 36.0
    assert fleet["orders_closed"] == 2
    assert fleet["mttr_status"] == "warning"


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


@pytest.mark.parametrize(
    "hours,expected",
    [
        (24.0, "good"),
        (30.0, "warning"),
        (50.0, "critical"),
        (None, "no_data"),
    ],
)
def test_classify_mttr_thresholds(hours, expected):
    """Umbrales MTTR good/warning/critical/no_data."""
    assert dashboard._classify_mttr(hours) == expected


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


def test_ranking_include_no_orders(monkeypatch):
    """include_no_orders=True agrega placas no_orders al final del orden 'best'."""
    rows = [
        _row("A01", 1, "Flota A", "calculated", 95.0, 720.0, 36.0),
        _row("A02", 1, "Flota A", "calculated", 98.0, 720.0, 14.4),
        _row("A03", 1, "Flota A", "no_orders", 100.0, 720.0, 0.0),
        _row("A04", 1, "Flota A", "no_orders", 100.0, 720.0, 0.0),
    ]
    monkeypatch.setattr(
        "app.services.availability_dashboard._fetch_month_rows",
        lambda month, customer_id=None: rows,
    )

    best = dashboard.get_vehicle_ranking(
        "2026-06", order="best", limit=10, include_no_orders=True
    )
    plates = [r["plate"] for r in best]
    assert plates == ["A02", "A01", "A03", "A04"]
    for r in best:
        if r["plate"].startswith("A03") or r["plate"].startswith("A04"):
            assert r["orders_considered"] == 0


def test_ranking_exposes_mttr_and_orders_closed(monkeypatch):
    """Los items del ranking exponen mttr_hours y orders_closed."""
    rows = [
        _row("A01", 1, "Flota A", "calculated", 95.0, 720.0, 36.0, mttr_hours=12.5, orders_closed=3),
        _row("A02", 1, "Flota A", "calculated", 98.0, 720.0, 14.4, mttr_hours=None, orders_closed=0),
    ]
    monkeypatch.setattr(
        "app.services.availability_dashboard._fetch_month_rows",
        lambda month, customer_id=None: rows,
    )

    ranking = dashboard.get_vehicle_ranking("2026-06", order="worst", limit=10)
    assert len(ranking) == 2
    assert ranking[0]["plate"] == "A01"
    assert ranking[0]["mttr_hours"] == 12.5
    assert ranking[0]["orders_closed"] == 3
    assert ranking[1]["plate"] == "A02"
    assert ranking[1]["mttr_hours"] is None
    assert ranking[1]["orders_closed"] == 0


def test_ranking_plate_search_filters_by_substring(monkeypatch):
    """plate_search filtra placas por substring de forma case-insensitive."""
    rows = [
        _row("ABC123", 1, "Flota A", "calculated", 95.0, 720.0, 36.0),
        _row("ABC456", 1, "Flota A", "calculated", 98.0, 720.0, 14.4),
        _row("DEF123", 1, "Flota A", "calculated", 92.0, 720.0, 57.6),
        _row("XYZ789", 1, "Flota A", "calculated", 99.0, 720.0, 7.2),
    ]
    monkeypatch.setattr(
        "app.services.availability_dashboard._fetch_month_rows",
        lambda month, customer_id=None: rows,
    )

    search = dashboard.get_vehicle_ranking("2026-06", plate_search="abc", limit=10)
    assert [r["plate"] for r in search] == ["ABC123", "ABC456"]

    search_upper = dashboard.get_vehicle_ranking("2026-06", plate_search="123", limit=10)
    assert [r["plate"] for r in search_upper] == ["DEF123", "ABC123"]

    empty_search = dashboard.get_vehicle_ranking("2026-06", plate_search="", limit=10)
    assert [r["plate"] for r in empty_search] == ["DEF123", "ABC123", "ABC456", "XYZ789"]

    none_search = dashboard.get_vehicle_ranking("2026-06", plate_search=None, limit=10)
    assert [r["plate"] for r in none_search] == ["DEF123", "ABC123", "ABC456", "XYZ789"]

    no_match = dashboard.get_vehicle_ranking("2026-06", plate_search="ZZZ", limit=10)
    assert no_match == []


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


# ── get_cloudfleet_coverage ─────────────────────────────────────────────────


def test_coverage_summary_and_uncovered_lists(monkeypatch):
    """Coverage resume conteos, ordena fleets por uncovered DESC y lista placas."""
    rows = [
        _row("A01", 1, "Flota A", "calculated", 100.0, 720.0, 0.0),
        _row("A02", 1, "Flota A", "not_in_cloudfleet", None, None, None),
        _row("A03", 1, "Flota A", "not_in_cloudfleet", None, None, None),
        _row("B01", 2, "Flota B", "calculated", 90.0, 720.0, 72.0),
        _row("B02", 2, "Flota B", "no_orders", 100.0, 720.0, 0.0),
        _row("B03", 2, "Flota B", "error", None, None, None),
        _row("B04", 2, "Flota B", "not_in_cloudfleet", None, None, None),
    ]
    monkeypatch.setattr(
        "app.services.availability_dashboard._fetch_month_rows",
        lambda month, customer_id=None: rows,
    )
    monkeypatch.setattr(
        "app.services.availability_dashboard.list_unmatched_cloudfleet",
        lambda month: [
            {"code": "QLM001", "cost_center": "Taller Norte", "last_seen_at": None},
            {"code": "QLM002", "cost_center": None, "last_seen_at": None},
        ],
    )

    coverage = dashboard.get_cloudfleet_coverage("2026-06")

    assert coverage["month"] == "2026-06"
    assert "generated_at" in coverage

    summary = coverage["summary"]
    assert summary["total"] == 7
    assert summary["covered"] == 3
    assert summary["uncovered"] == 3
    assert summary["error"] == 1
    assert summary["coverage_pct"] == round(3 / 7 * 100.0, 1)
    assert summary["cloudfleet_only"] == 2

    fleets = coverage["fleets"]
    assert [f["customer_name"] for f in fleets] == ["Flota A", "Flota B"]
    assert fleets[0]["total"] == 3
    assert fleets[0]["uncovered"] == 2
    assert fleets[0]["coverage_pct"] == round(1 / 3 * 100.0, 1)
    assert fleets[1]["total"] == 4
    assert fleets[1]["uncovered"] == 1
    assert fleets[1]["coverage_pct"] == round(3 / 4 * 100.0, 1)

    plates = coverage["uncovered_plates"]
    assert [p["plate"] for p in plates] == ["A02", "A03", "B04"]
    assert all(p["customer_name"] in {"Flota A", "Flota B"} for p in plates)

    unmatched = coverage["cloudfleet_unmatched"]
    assert len(unmatched) == 2
    assert unmatched[0]["code"] == "QLM001"
    assert unmatched[0]["cost_center"] == "Taller Norte"
    assert unmatched[1]["code"] == "QLM002"
    assert unmatched[1]["cost_center"] is None


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
        "app.services.availability_dashboard.db_conn",
        lambda row_factory=None: FakeConn(),
    )

    dashboard._fetch_month_rows("2026-06")

    assert "__navitrans_system__" in captured["params"]
    assert "(c.name IS NULL OR c.name <> %s)" in captured["sql"]
