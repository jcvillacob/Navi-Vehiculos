"""
Tests de la logica pura de disponibilidad de proyectos.

Cubren:
- Match y normalizacion de placas contra el inventario CloudFleet.
- Estados sin ordenes, sin match y con ordenes que no afectan disponibilidad.
- Interseccion de ordenes con el periodo del mes (pasado y mes actual).
- Precedencia de technicalCompletionDate sobre finalCompletionDate.
- Fallback de startDate a workshopDate.
- Estados inactivos (cancelled, closed) y su aporte de horas.
- Deduplicacion de ordenes de trabajo.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

import pytest

from app.services.availability import (
    AvailabilityTarget,
    calculate_availability_for_targets,
    dedupe_work_orders,
)


# ── Helpers ─────────────────────────────────────────────────────────────────


_ORDER_SEQ = 0


def _reset_order_seq() -> None:
    global _ORDER_SEQ
    _ORDER_SEQ = 0


def _vehicle(code: str) -> dict[str, Any]:
    return {"code": code}


def _order(vehicle_code: str, **overrides: Any) -> dict[str, Any]:
    global _ORDER_SEQ
    _ORDER_SEQ += 1
    defaults: dict[str, Any] = {
        "number": f"ORD-{_ORDER_SEQ:04d}",
        "vehicleCode": vehicle_code,
        "affectsVehicleAvailability": True,
        "status": "opened",
    }
    defaults.update(overrides)
    return defaults


# ── calculate_availability_for_targets ──────────────────────────────────────


def test_plate_not_in_cloudfleet():
    """Placa local sin match en el inventario de vehiculos."""
    _reset_order_seq()
    targets = [AvailabilityTarget(plate="ABC123")]
    vehicles = [_vehicle("OTHER")]
    results = calculate_availability_for_targets(
        targets,
        month="2026-06",
        cloudfleet_vehicles=vehicles,
        cloudfleet_work_orders=[],
        now=datetime(2026, 7, 11, 12, 0, 0),
    )
    assert len(results) == 1
    assert results[0].plate == "ABC123"
    assert results[0].status == "not_in_cloudfleet"
    assert results[0].h_total is None


def test_vehicle_without_orders():
    """Placa en CloudFleet sin ordenes -> 100% de disponibilidad."""
    _reset_order_seq()
    targets = [AvailabilityTarget(plate="ABC123")]
    vehicles = [_vehicle("ABC123")]
    results = calculate_availability_for_targets(
        targets,
        month="2026-06",
        cloudfleet_vehicles=vehicles,
        cloudfleet_work_orders=[],
        now=datetime(2026, 7, 11, 12, 0, 0),
    )
    assert results[0].status == "no_orders"
    assert results[0].h_total == 720.0
    assert results[0].h_no_disp == 0.0
    assert results[0].project_availability_pct == 100.0


@pytest.mark.parametrize("flag_value", [False, "false", "False", "FALSE", "0", "no"])
def test_order_does_not_affect_availability(flag_value):
    """Ordenes con affectsVehicleAvailability=False se ignoran."""
    _reset_order_seq()
    targets = [AvailabilityTarget(plate="ABC123")]
    vehicles = [_vehicle("ABC123")]
    orders = [
        _order(
            "ABC123",
            affectsVehicleAvailability=flag_value,
            startDate="2026-06-10T13:00:00.0000000Z",
            technicalCompletionDate="2026-06-12T13:00:00.0000000Z",
        )
    ]
    results = calculate_availability_for_targets(
        targets,
        month="2026-06",
        cloudfleet_vehicles=vehicles,
        cloudfleet_work_orders=orders,
        now=datetime(2026, 7, 11, 12, 0, 0),
    )
    assert results[0].status == "no_orders"
    assert results[0].project_availability_pct == 100.0


def test_order_contained_in_month():
    """Orden completamente contenida en el mes."""
    _reset_order_seq()
    targets = [AvailabilityTarget(plate="ABC123")]
    vehicles = [_vehicle("ABC123")]
    orders = [
        _order(
            "ABC123",
            startDate="2026-06-10T13:00:00.0000000Z",
            technicalCompletionDate="2026-06-12T13:00:00.0000000Z",
        )
    ]
    results = calculate_availability_for_targets(
        targets,
        month="2026-06",
        cloudfleet_vehicles=vehicles,
        cloudfleet_work_orders=orders,
        now=datetime(2026, 7, 11, 12, 0, 0),
    )
    result = results[0]
    assert result.status == "calculated"
    assert result.h_total == 720.0
    assert result.h_no_disp == 48.0
    assert result.project_availability_pct == round((720.0 - 48.0) / 720.0 * 100.0, 3)
    assert result.project_availability_pct == 93.333
    assert result.orders_considered == 1


def test_order_clamped_to_month_edges():
    """Orden que excede el mes se recorta a los bordes del periodo."""
    _reset_order_seq()
    targets = [AvailabilityTarget(plate="ABC123")]
    vehicles = [_vehicle("ABC123")]
    orders = [
        _order(
            "ABC123",
            startDate="2026-05-31T08:00:00.0000000Z",
            technicalCompletionDate="2026-07-01T08:00:00.0000000Z",
        )
    ]
    results = calculate_availability_for_targets(
        targets,
        month="2026-06",
        cloudfleet_vehicles=vehicles,
        cloudfleet_work_orders=orders,
        now=datetime(2026, 7, 11, 12, 0, 0),
    )
    result = results[0]
    assert result.status == "calculated"
    # Periodo local: [2026-06-01 00:00:00, 2026-06-30 23:59:59] -> 719.99972... h
    assert result.h_no_disp == pytest.approx(719.999722, abs=0.001)
    assert result.project_availability_pct == pytest.approx(0.0, abs=0.001)
    assert result.project_availability_pct >= 0.0


def test_current_month_open_order():
    """Mes actual: orden abierta cuenta horas hasta `now`."""
    _reset_order_seq()
    targets = [AvailabilityTarget(plate="ABC123")]
    vehicles = [_vehicle("ABC123")]
    orders = [
        _order(
            "ABC123",
            startDate="2026-07-01T05:00:00.0000000Z",
            status="opened",
        )
    ]
    now = datetime(2026, 7, 11, 12, 0, 0)
    results = calculate_availability_for_targets(
        targets,
        month="2026-07",
        cloudfleet_vehicles=vehicles,
        cloudfleet_work_orders=orders,
        now=now,
    )
    result = results[0]
    assert result.status == "calculated"
    assert result.h_total == 744.0  # julio tiene 31 dias
    assert result.h_no_disp == 252.0
    expected_pct = round((744.0 - 252.0) / 744.0 * 100.0, 3)
    assert result.project_availability_pct == expected_pct
    assert result.project_availability_pct == 66.129


def test_cancelled_order_without_completion_dates():
    """Orden cancelada sin fechas de cierre no aporta horas."""
    _reset_order_seq()
    targets = [AvailabilityTarget(plate="ABC123")]
    vehicles = [_vehicle("ABC123")]
    orders = [
        _order(
            "ABC123",
            status="cancelled",
            startDate="2026-06-10T13:00:00.0000000Z",
        )
    ]
    results = calculate_availability_for_targets(
        targets,
        month="2026-06",
        cloudfleet_vehicles=vehicles,
        cloudfleet_work_orders=orders,
        now=datetime(2026, 7, 11, 12, 0, 0),
    )
    result = results[0]
    assert result.status == "calculated"
    assert result.h_no_disp == 0.0
    assert result.project_availability_pct == 100.0
    assert result.orders_considered == 1


def test_closed_order_with_final_completion_only():
    """Orden cerrada sin technicalCompletionDate usa finalCompletionDate como fin."""
    _reset_order_seq()
    targets = [AvailabilityTarget(plate="ABC123")]
    vehicles = [_vehicle("ABC123")]
    orders = [
        _order(
            "ABC123",
            status="closed",
            startDate="2026-06-10T13:00:00.0000000Z",
            finalCompletionDate="2026-06-12T13:00:00.0000000Z",
        )
    ]
    results = calculate_availability_for_targets(
        targets,
        month="2026-06",
        cloudfleet_vehicles=vehicles,
        cloudfleet_work_orders=orders,
        now=datetime(2026, 7, 11, 12, 0, 0),
    )
    result = results[0]
    assert result.status == "calculated"
    assert result.h_no_disp == 48.0
    assert result.project_availability_pct == 93.333


def test_technical_completion_takes_precedence():
    """technicalCompletionDate gana sobre finalCompletionDate."""
    _reset_order_seq()
    targets = [AvailabilityTarget(plate="ABC123")]
    vehicles = [_vehicle("ABC123")]
    orders = [
        _order(
            "ABC123",
            startDate="2026-06-10T13:00:00.0000000Z",
            technicalCompletionDate="2026-06-11T13:00:00.0000000Z",
            finalCompletionDate="2026-06-12T13:00:00.0000000Z",
        )
    ]
    results = calculate_availability_for_targets(
        targets,
        month="2026-06",
        cloudfleet_vehicles=vehicles,
        cloudfleet_work_orders=orders,
        now=datetime(2026, 7, 11, 12, 0, 0),
    )
    result = results[0]
    assert result.status == "calculated"
    assert result.h_no_disp == 24.0


def test_start_date_null_uses_workshop_date():
    """Si startDate es nulo se usa workshopDate como inicio."""
    _reset_order_seq()
    targets = [AvailabilityTarget(plate="ABC123")]
    vehicles = [_vehicle("ABC123")]
    orders = [
        _order(
            "ABC123",
            startDate=None,
            workshopDate="2026-06-10T13:00:00.0000000Z",
            technicalCompletionDate="2026-06-12T13:00:00.0000000Z",
        )
    ]
    results = calculate_availability_for_targets(
        targets,
        month="2026-06",
        cloudfleet_vehicles=vehicles,
        cloudfleet_work_orders=orders,
        now=datetime(2026, 7, 11, 12, 0, 0),
    )
    result = results[0]
    assert result.status == "calculated"
    assert result.h_no_disp == 48.0


def test_plate_normalization_matches_vehicle_code():
    """La placa local 'abc-123' matchea el codigo CloudFleet 'ABC123'."""
    _reset_order_seq()
    targets = [AvailabilityTarget(plate="abc-123")]
    vehicles = [_vehicle("ABC123")]
    orders = [
        _order(
            "ABC123",
            startDate="2026-06-10T13:00:00.0000000Z",
            technicalCompletionDate="2026-06-12T13:00:00.0000000Z",
        )
    ]
    results = calculate_availability_for_targets(
        targets,
        month="2026-06",
        cloudfleet_vehicles=vehicles,
        cloudfleet_work_orders=orders,
        now=datetime(2026, 7, 11, 12, 0, 0),
    )
    assert results[0].status == "calculated"
    assert results[0].h_no_disp == 48.0


# ── dedupe_work_orders ──────────────────────────────────────────────────────


def test_dedupe_keeps_newest_update_for_same_number():
    """Con mismo number conserva la orden con updatedAt mas reciente."""
    orders = [
        {"number": "ORD-0001", "updatedAt": "2026-06-01T10:00:00.0000000Z", "priority": 1},
        {"number": "ORD-0001", "updatedAt": "2026-06-02T10:00:00.0000000Z", "priority": 2},
    ]
    result = dedupe_work_orders(orders)
    assert len(result) == 1
    assert result[0]["priority"] == 2


def test_dedupe_stable_order_first_occurrence():
    """El orden de salida sigue la primera aparicion de cada clave."""
    orders = [
        {"number": "B", "updatedAt": "2026-06-01T10:00:00.0000000Z"},
        {"number": "A", "updatedAt": "2026-06-01T10:00:00.0000000Z"},
        {"number": "B", "updatedAt": "2026-06-02T10:00:00.0000000Z"},
    ]
    result = dedupe_work_orders(orders)
    assert [o["number"] for o in result] == ["B", "A"]


def test_dedupe_uses_id_when_number_missing():
    """Sin number pero con id se usa id como clave."""
    orders = [
        {"id": "uuid-1", "updatedAt": "2026-06-01T10:00:00.0000000Z", "value": 1},
        {"id": "uuid-1", "updatedAt": "2026-06-02T10:00:00.0000000Z", "value": 2},
    ]
    result = dedupe_work_orders(orders)
    assert len(result) == 1
    assert result[0]["value"] == 2


def test_dedupe_preserves_orders_without_key():
    """Ordenes sin number ni id se conservan."""
    orders = [
        {"value": 1},
        {"value": 2},
    ]
    result = dedupe_work_orders(orders)
    assert len(result) == 2
    assert [o["value"] for o in result] == [1, 2]


def test_dedupe_unparseable_updated_at_keeps_first():
    """updatedAt no parseable en el nuevo registro se trata como mas viejo."""
    orders = [
        {"number": "ORD-0001", "updatedAt": "2026-06-02T10:00:00.0000000Z", "value": 1},
        {"number": "ORD-0001", "updatedAt": "no-es-fecha", "value": 2},
    ]
    result = dedupe_work_orders(orders)
    assert len(result) == 1
    assert result[0]["value"] == 1
