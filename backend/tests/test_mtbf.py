"""
Tests de la logica pura del MTBF anual.

Cubre compute_mtbf sin red ni DB: filtrado de Programado, exclusion de placas
con una sola falla, intervalos consecutivos, media global, agrupacion por flota,
ordenamiento peores-primero y clasificacion por umbrales.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

import pytest

from app.services.mtbf import _classify_mtbf, compute_mtbf


# ── Helpers ─────────────────────────────────────────────────────────────────


_ORDER_SEQ = 0


def _reset_order_seq() -> None:
    global _ORDER_SEQ
    _ORDER_SEQ = 0


def _order(number: str | None = None, **overrides: Any) -> dict[str, Any]:
    global _ORDER_SEQ
    _ORDER_SEQ += 1
    defaults: dict[str, Any] = {
        "number": f"ORD-{_ORDER_SEQ:04d}" if number is None else number,
        "vehicleCode": "ABC123",
        "type": "No Programado",
        "status": "closed",
    }
    defaults.update(overrides)
    return defaults


def _dt_local(iso: str) -> datetime:
    return datetime.fromisoformat(iso)


def _dt_utc(iso_local: str) -> str:
    """Convierte un datetime local Colombia a string UTC con 7 decimales."""
    local = datetime.fromisoformat(iso_local)
    utc = local + timedelta(hours=5)
    return f"{utc.isoformat()}.0000000Z"


# ── _classify_mtbf ──────────────────────────────────────────────────────────


def test_classify_mtbf_thresholds():
    assert _classify_mtbf(500.0) == "good"
    assert _classify_mtbf(600.0) == "good"
    assert _classify_mtbf(350.0) == "warning"
    assert _classify_mtbf(300.0) == "warning"
    assert _classify_mtbf(299.9) == "critical"
    assert _classify_mtbf(200.0) == "critical"
    assert _classify_mtbf(None) == "no_data"


# ── compute_mtbf ────────────────────────────────────────────────────────────


def test_programmed_orders_excluded():
    _reset_order_seq()
    orders = [
        _order(type="Programado", startDate=_dt_utc("2026-01-10T10:00:00")),
        _order(type="  Programado ", startDate=_dt_utc("2026-01-20T10:00:00")),
        _order(type="PROGRAMADO", startDate=_dt_utc("2026-02-01T10:00:00")),
        _order(type="No Programado", startDate=_dt_utc("2026-01-15T10:00:00")),
    ]
    result = compute_mtbf(orders, {}, year=2026, now=_dt_local("2026-07-11T12:00:00"))
    assert result["mtbf_hours"] is None
    assert result["status"] == "no_data"
    assert result["intervals_count"] == 0
    assert result["vehicles_considered"] == 0


def test_single_failure_does_not_count():
    _reset_order_seq()
    orders = [
        _order(vehicleCode="ABC123", startDate=_dt_utc("2026-03-01T08:00:00")),
    ]
    result = compute_mtbf(orders, {}, year=2026, now=_dt_local("2026-07-11T12:00:00"))
    assert result["mtbf_hours"] is None
    assert result["intervals_count"] == 0
    assert result["vehicles_considered"] == 0


def test_intervals_and_global_average():
    _reset_order_seq()
    # Placa AAA: 3 fallas -> 2 intervalos de 72h y 48h.
    # Placa BBB: 2 fallas -> 1 intervalo de 24h.
    # Media global = (72 + 48 + 24) / 3 = 48h.
    orders = [
        _order(vehicleCode="AAA111", startDate=_dt_utc("2026-01-01T00:00:00")),
        _order(vehicleCode="AAA111", startDate=_dt_utc("2026-01-04T00:00:00")),
        _order(vehicleCode="AAA111", startDate=_dt_utc("2026-01-06T00:00:00")),
        _order(vehicleCode="BBB222", startDate=_dt_utc("2026-02-01T00:00:00")),
        _order(vehicleCode="BBB222", startDate=_dt_utc("2026-02-02T00:00:00")),
    ]
    result = compute_mtbf(orders, {}, year=2026, now=_dt_local("2026-07-11T12:00:00"))
    assert result["intervals_count"] == 3
    assert result["vehicles_considered"] == 2
    assert result["mtbf_hours"] == pytest.approx(48.0)
    assert result["status"] == "critical"


def test_fleet_grouping_and_worst_first_ordering():
    _reset_order_seq()
    # Flota A (cliente 1): placa AAA intervalo 24h -> mtbf 24 (critical).
    # Flota B (cliente 2): placa BBB intervalo 1000h -> mtbf 1000 (good).
    # Sin cliente: placa CCC intervalo 500h -> mtbf 500 (good).
    plate_map = {
        "AAA111": {"customer_id": 1, "customer_name": "Flota A"},
        "BBB222": {"customer_id": 2, "customer_name": "Flota B"},
    }
    orders = [
        _order(vehicleCode="AAA111", startDate=_dt_utc("2026-01-01T00:00:00")),
        _order(vehicleCode="AAA111", startDate=_dt_utc("2026-01-02T00:00:00")),
        _order(vehicleCode="BBB222", startDate=_dt_utc("2026-01-01T00:00:00")),
        _order(vehicleCode="BBB222", startDate=_dt_utc("2026-03-12T00:00:00")),
        _order(vehicleCode="CCC333", startDate=_dt_utc("2026-01-01T00:00:00")),
        _order(vehicleCode="CCC333", startDate=_dt_utc("2026-01-22T20:00:00")),
    ]
    result = compute_mtbf(
        orders, plate_map, year=2026, now=_dt_local("2026-07-11T12:00:00")
    )
    fleets = result["fleets"]
    assert len(fleets) == 3

    # Peores primero.
    assert fleets[0]["customer_name"] == "Flota A"
    assert fleets[0]["mtbf_hours"] == pytest.approx(24.0)
    assert fleets[0]["status"] == "critical"
    assert fleets[0]["vehicles_with_failures"] == 1
    assert fleets[0]["failures"] == 2

    # Flotas con mtbf alto.
    names = [f["customer_name"] for f in fleets[1:]]
    assert "Flota B" in names
    assert "Sin cliente" in names

    # Sin cliente tiene mtbf 500 (good), peor que Flota B (1000), asi que va
    # antes; solo los mtbf None van al final.
    assert fleets[1]["customer_name"] == "Sin cliente"
    assert fleets[2]["customer_name"] == "Flota B"


def test_other_year_start_date_excluded():
    _reset_order_seq()
    orders = [
        _order(vehicleCode="AAA111", startDate=_dt_utc("2025-12-31T23:59:59")),
        _order(vehicleCode="AAA111", startDate=_dt_utc("2026-01-01T00:00:00")),
        _order(vehicleCode="AAA111", startDate=_dt_utc("2026-01-02T00:00:00")),
    ]
    result = compute_mtbf(orders, {}, year=2026, now=_dt_local("2026-07-11T12:00:00"))
    # Una sola falla del 2026 -> no genera intervalos.
    assert result["intervals_count"] == 1
    assert result["mtbf_hours"] == pytest.approx(24.0)


def test_dedupe_work_orders_uses_most_recent_updated_at():
    _reset_order_seq()
    orders = [
        _order(
            number="DUP-001",
            vehicleCode="AAA111",
            startDate=_dt_utc("2026-01-01T00:00:00"),
            updatedAt="2026-01-01T00:00:00.0000000Z",
        ),
        _order(
            number="DUP-001",
            vehicleCode="AAA111",
            startDate=_dt_utc("2026-01-05T00:00:00"),
            updatedAt="2026-01-10T00:00:00.0000000Z",
        ),
        _order(
            number="ORD-SECOND",
            vehicleCode="AAA111",
            startDate=_dt_utc("2026-01-09T00:00:00"),
        ),
    ]
    result = compute_mtbf(orders, {}, year=2026, now=_dt_local("2026-07-11T12:00:00"))
    # Despues de dedupe la duplicada DUP-001 conserva la version con startDate
    # 5-ene; junto con la tercera orden (9-ene) queda un intervalo de 96h.
    assert result["intervals_count"] == 1
    assert result["mtbf_hours"] == pytest.approx(96.0)


def test_workshop_date_fallback():
    _reset_order_seq()
    orders = [
        _order(
            vehicleCode="AAA111",
            startDate=None,
            workshopDate=_dt_utc("2026-01-01T00:00:00"),
        ),
        _order(
            vehicleCode="AAA111",
            startDate=None,
            workshopDate=_dt_utc("2026-01-03T00:00:00"),
        ),
    ]
    result = compute_mtbf(orders, {}, year=2026, now=_dt_local("2026-07-11T12:00:00"))
    assert result["intervals_count"] == 1
    assert result["mtbf_hours"] == pytest.approx(48.0)
