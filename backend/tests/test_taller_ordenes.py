"""
Tests de la logica pura del monitor de ordenes de taller activas.

Cubre build_active_orders sin red ni DB: filtrado, deduplicacion,
indicadores temporales, ordenamiento, summary y fallback de flota.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

import pytest

from app.services import taller_ordenes
from app.services.taller_ordenes import build_active_orders


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
        "status": "opened",
        "type": "No Programado",
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


# ── build_active_orders ─────────────────────────────────────────────────────


def test_filters_only_active_statuses_and_requires_number():
    _reset_order_seq()
    orders = [
        _order(status="opened"),
        _order(status="onTechnicalCompletion"),
        _order(status="closed"),
        _order(status="opened", number=""),
        _order(status="cancelled"),
        _order(status="OPENED"),  # case-insensitive
    ]
    result = build_active_orders(orders, {}, now=datetime(2026, 7, 11, 12, 0, 0))
    numbers = {o["order_number"] for o in result["orders"]}
    assert result["summary"]["total_active"] == 3
    assert any("ORD-" in n for n in numbers)


def test_dedupe_keeps_most_recent_updated_at():
    _reset_order_seq()
    orders = [
        _order(
            number="ORD-DUP-001",
            vehicleCode="ABC123",
            status="opened",
            updatedAt="2026-07-10T10:00:00.0000000Z",
            estimatedFinishDate="2026-07-15T10:00:00.0000000Z",
        ),
        _order(
            number="ORD-DUP-001",
            vehicleCode="ABC123",
            status="opened",
            updatedAt="2026-07-10T12:00:00.0000000Z",
            estimatedFinishDate="2026-07-12T10:00:00.0000000Z",
        ),
    ]
    result = build_active_orders(orders, {}, now=_dt_local("2026-07-11T12:00:00"))
    assert result["summary"]["total_active"] == 1
    assert result["orders"][0]["order_number"] == "ORD-DUP-001"
    assert result["orders"][0]["status_indicator"] == "about_to_expire"


def test_plate_without_customer_defaults_to_sin_cliente():
    _reset_order_seq()
    orders = [_order(vehicleCode="XYZ999")]
    result = build_active_orders(orders, {}, now=datetime(2026, 7, 11, 12, 0, 0))
    assert result["orders"][0]["customer_name"] == "Sin cliente"
    assert result["orders"][0]["customer_id"] is None
    assert result["orders"][0]["fleet"] == "Sin cliente"


def test_customer_mapping_by_normalized_plate():
    _reset_order_seq()
    orders = [_order(vehicleCode=" abc-123 ")]
    plate_map = {
        "ABC123": {"customer_id": 7, "customer_name": "Viacargo"},
    }
    result = build_active_orders(orders, plate_map, now=datetime(2026, 7, 11, 12, 0, 0))
    order = result["orders"][0]
    assert order["plate"] == "ABC123"
    assert order["customer_id"] == 7
    assert order["customer_name"] == "Viacargo"
    assert order["fleet"] == "Viacargo"


def test_refresh_cache_keeps_only_availability_eligible_vehicles(monkeypatch):
    orders = [
        _order(number="ELIGIBLE", vehicleCode="ABC-123"),
        _order(number="OUTSIDE", vehicleCode="XYZ999"),
    ]
    monkeypatch.setattr(
        taller_ordenes,
        "_load_plate_customer_map",
        lambda: {"ABC123": {"customer_id": 7, "customer_name": "Viacargo"}},
    )
    cached: dict[str, Any] = {}
    monkeypatch.setattr(taller_ordenes, "_set_cached", lambda payload: cached.update(payload))

    result = taller_ordenes.refresh_cache_from_orders(
        orders,
        now=datetime(2026, 7, 11, 12, 0, 0),
    )

    assert [order["order_number"] for order in result["orders"]] == ["ELIGIBLE"]
    assert result["summary"]["total_active"] == 1
    assert cached["summary"]["total_active"] == 1


def test_days_elapsed_from_start_date_with_workshop_fallback():
    _reset_order_seq()
    orders = [
        _order(
            number="O1",
            startDate=_dt_utc("2026-07-01T12:00:00"),
            workshopDate=_dt_utc("2026-07-05T12:00:00"),
        ),
        _order(
            number="O2",
            startDate=None,
            workshopDate=_dt_utc("2026-07-03T12:00:00"),
        ),
        _order(number="O3", startDate=None, workshopDate=None),
    ]
    now = _dt_local("2026-07-11T12:00:00")
    result = build_active_orders(orders, {}, now=now)
    by_number = {o["order_number"]: o for o in result["orders"]}
    assert by_number["O1"]["days_elapsed"] == 10
    assert by_number["O2"]["days_elapsed"] == 8
    assert by_number["O3"]["days_elapsed"] is None


def test_status_indicator_overdue():
    _reset_order_seq()
    orders = [
        _order(
            number="O1",
            status="opened",
            estimatedFinishDate=_dt_utc("2026-07-10T10:00:00"),
        )
    ]
    result = build_active_orders(orders, {}, now=_dt_local("2026-07-11T12:00:00"))
    assert result["orders"][0]["status_indicator"] == "overdue"
    assert result["orders"][0]["time_status_text"] == "Excedido"
    assert result["summary"]["overdue"] == 1


def test_status_indicator_about_to_expire():
    _reset_order_seq()
    orders = [
        _order(
            number="O1",
            status="opened",
            estimatedFinishDate=_dt_utc("2026-07-12T08:00:00"),
        )
    ]
    result = build_active_orders(orders, {}, now=_dt_local("2026-07-11T12:00:00"))
    assert result["orders"][0]["status_indicator"] == "about_to_expire"
    assert result["orders"][0]["time_status_text"] == "Por vencer"
    assert result["summary"]["about_to_expire"] == 1


def test_status_indicator_on_time():
    _reset_order_seq()
    orders = [
        _order(
            number="O1",
            status="opened",
            estimatedFinishDate=_dt_utc("2026-07-15T12:00:00"),
        )
    ]
    result = build_active_orders(orders, {}, now=_dt_local("2026-07-11T12:00:00"))
    assert result["orders"][0]["status_indicator"] == "on_time"
    assert result["orders"][0]["time_status_text"] == "En tiempo"
    assert result["summary"]["on_time"] == 1


def test_status_indicator_pending_closure_takes_precedence():
    _reset_order_seq()
    orders = [
        _order(
            number="O1",
            status="onTechnicalCompletion",
            estimatedFinishDate=_dt_utc("2026-07-10T10:00:00"),  # vencida
            technicalCompletionDate=_dt_utc("2026-07-09T10:00:00"),
            finalCompletionDate=None,
        )
    ]
    result = build_active_orders(orders, {}, now=_dt_local("2026-07-11T12:00:00"))
    assert result["orders"][0]["status_indicator"] == "pending_closure"
    assert result["orders"][0]["pending_closure_days"] == 2
    assert result["summary"]["pending_closure"] == 1
    assert result["summary"]["overdue"] == 0


def test_pending_closure_days_exact_when_technical_completion_without_final():
    _reset_order_seq()
    orders = [
        _order(
            number="O1",
            status="onTechnicalCompletion",
            technicalCompletionDate=_dt_utc("2026-07-01T12:00:00"),
            finalCompletionDate=None,
        )
    ]
    result = build_active_orders(orders, {}, now=_dt_local("2026-07-11T12:00:00"))
    assert result["orders"][0]["pending_closure_days"] == 10


def test_pending_closure_days_none_when_final_completion_present():
    _reset_order_seq()
    orders = [
        _order(
            number="O1",
            status="onTechnicalCompletion",
            technicalCompletionDate=_dt_utc("2026-07-01T12:00:00"),
            finalCompletionDate=_dt_utc("2026-07-05T12:00:00"),
        )
    ]
    result = build_active_orders(orders, {}, now=_dt_local("2026-07-11T12:00:00"))
    assert result["orders"][0]["pending_closure_days"] is None


def test_pending_closure_days_none_without_technical_completion():
    _reset_order_seq()
    orders = [
        _order(
            number="O1",
            status="opened",
            technicalCompletionDate=None,
            finalCompletionDate=None,
        )
    ]
    result = build_active_orders(orders, {}, now=_dt_local("2026-07-11T12:00:00"))
    assert result["orders"][0]["pending_closure_days"] is None


def test_no_estimated_finish_date_is_on_time():
    _reset_order_seq()
    orders = [_order(number="O1", status="opened", estimatedFinishDate=None)]
    result = build_active_orders(orders, {}, now=datetime(2026, 7, 11, 12, 0, 0))
    assert result["orders"][0]["status_indicator"] == "on_time"


def test_sort_labels_first_then_days_desc():
    _reset_order_seq()
    orders = [
        _order(
            number="OLD",
            startDate=_dt_utc("2026-07-01T12:00:00"),
            maintenanceLabels=[],
        ),
        _order(
            number="NEW-LABEL",
            startDate=_dt_utc("2026-07-09T12:00:00"),
            maintenanceLabels=["Backup en préstamo"],
        ),
        _order(
            number="MID",
            startDate=_dt_utc("2026-07-05T12:00:00"),
            maintenanceLabels=[],
        ),
        _order(
            number="NO-DAYS",
            startDate=None,
            maintenanceLabels=[],
        ),
    ]
    result = build_active_orders(orders, {}, now=_dt_local("2026-07-11T12:00:00"))
    numbers = [o["order_number"] for o in result["orders"]]
    assert numbers[0] == "NEW-LABEL"
    assert numbers[1:] == ["OLD", "MID", "NO-DAYS"]


def test_summary_counts():
    _reset_order_seq()
    now = _dt_local("2026-07-11T12:00:00")
    orders = [
        _order(number="O1", estimatedFinishDate=_dt_utc("2026-07-15T12:00:00")),
        _order(number="O2", estimatedFinishDate=_dt_utc("2026-07-11T14:00:00")),
        _order(number="O3", estimatedFinishDate=_dt_utc("2026-07-10T10:00:00")),
        _order(
            number="O4",
            technicalCompletionDate=_dt_utc("2026-07-09T10:00:00"),
            finalCompletionDate=None,
        ),
        _order(
            number="O5",
            maintenanceLabels=["Backup en préstamo"],
            estimatedFinishDate=_dt_utc("2026-07-15T12:00:00"),
        ),
    ]
    result = build_active_orders(orders, {}, now=now)
    summary = result["summary"]
    assert summary["total_active"] == 5
    assert summary["on_time"] == 2
    assert summary["about_to_expire"] == 1
    assert summary["overdue"] == 1
    assert summary["pending_closure"] == 1
    assert summary["pending_closure_7d"] == 0
    assert summary["pending_closure_30d"] == 0
    assert summary["con_etiquetas"] == 1


def test_summary_pending_closure_aging_counters():
    _reset_order_seq()
    now = _dt_local("2026-07-11T12:00:00")
    orders = [
        _order(
            number="O1",
            technicalCompletionDate=_dt_utc("2026-07-01T12:00:00"),
            finalCompletionDate=None,
        ),
        _order(
            number="O2",
            technicalCompletionDate=_dt_utc("2026-06-01T12:00:00"),
            finalCompletionDate=None,
        ),
        _order(
            number="O3",
            technicalCompletionDate=_dt_utc("2026-07-10T12:00:00"),
            finalCompletionDate=None,
        ),
    ]
    result = build_active_orders(orders, {}, now=now)
    summary = result["summary"]
    assert summary["pending_closure"] == 3
    assert summary["pending_closure_7d"] == 2
    assert summary["pending_closure_30d"] == 1


def test_reason_none_when_empty():
    _reset_order_seq()
    orders = [
        _order(number="O1", reason="  "),
        _order(number="O2", reason=None),
        _order(number="O3", reason="Falla motor"),
    ]
    result = build_active_orders(orders, {}, now=datetime(2026, 7, 11, 12, 0, 0))
    by_number = {o["order_number"]: o for o in result["orders"]}
    assert by_number["O1"]["reason"] is None
    assert by_number["O2"]["reason"] is None
    assert by_number["O3"]["reason"] == "Falla motor"


def test_returns_generated_at_and_orders_shape():
    _reset_order_seq()
    now = datetime(2026, 7, 11, 12, 0, 0)
    result = build_active_orders([_order()], {}, now=now)
    assert result["generated_at"] == now.isoformat()
    assert "summary" in result
    assert isinstance(result["orders"], list)
    order = result["orders"][0]
    assert set(order.keys()) >= {
        "order_number",
        "plate",
        "customer_id",
        "customer_name",
        "fleet",
        "type",
        "status",
        "reason",
        "days_elapsed",
        "pending_closure_days",
        "status_indicator",
        "time_status_text",
        "maintenance_labels",
        "has_labels",
    }
