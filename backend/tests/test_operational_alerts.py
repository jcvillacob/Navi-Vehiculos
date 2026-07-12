"""
Tests de la logica pura de alertas operativas.

Evalua `evaluate_alerts` sin red ni DB: MTTR critico/warning/normal,
flotas criticas, backlog de cierre administrativo, ordenes vencidas,
ordenamiento y counts.
"""
from __future__ import annotations

from typing import Any

import pytest

from app.services.operational_alerts import evaluate_alerts


# ── Helpers ─────────────────────────────────────────────────────────────────


def _overview(overall: dict[str, Any] | None = None, fleets: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    return {
        "month": "2026-07",
        "overall": overall or {},
        "fleets": fleets or [],
    }


def _coverage() -> dict[str, Any]:
    return {
        "month": "2026-07",
        "summary": {"total": 0, "covered": 0, "uncovered": 0, "error": 0, "coverage_pct": None},
        "fleets": [],
        "uncovered_plates": [],
        "cloudfleet_unmatched": [],
    }


def _alert_keys(alerts: list[dict[str, Any]]) -> list[str]:
    return [a["key"] for a in alerts]


def _severities(alerts: list[dict[str, Any]]) -> list[str]:
    return [a["severity"] for a in alerts]


# ── MTTR ────────────────────────────────────────────────────────────────────


def test_mttr_critical_when_above_48():
    alerts = evaluate_alerts(
        overview=_overview(overall={"mttr_hours": 52.5}),
        coverage=_coverage(),
        taller_summary=None,
    )
    assert "mttr_critical" in _alert_keys(alerts)
    assert alerts[0]["severity"] == "critical"
    assert "52.5h" in alerts[0]["detail"]


def test_mttr_warning_between_24_and_48():
    alerts = evaluate_alerts(
        overview=_overview(overall={"mttr_hours": 30.0}),
        coverage=_coverage(),
        taller_summary=None,
    )
    assert "mttr_warning" in _alert_keys(alerts)
    assert alerts[0]["severity"] == "warning"


def test_mttr_good_no_alert():
    alerts = evaluate_alerts(
        overview=_overview(overall={"mttr_hours": 12.0}),
        coverage=_coverage(),
        taller_summary=None,
    )
    assert "mttr_critical" not in _alert_keys(alerts)
    assert "mttr_warning" not in _alert_keys(alerts)


def test_mttr_none_no_alert():
    alerts = evaluate_alerts(
        overview=_overview(overall={"mttr_hours": None}),
        coverage=_coverage(),
        taller_summary=None,
    )
    assert alerts == []


# ── Flotas criticas ─────────────────────────────────────────────────────────


def test_critical_fleet_generates_alert():
    alerts = evaluate_alerts(
        overview=_overview(
            overall={"mttr_hours": None},
            fleets=[
                {"customer_id": 1, "customer_name": "Flota A", "status": "critical", "availability_pct": 92.5},
                {"customer_id": 2, "customer_name": "Flota B", "status": "good", "availability_pct": 98.0},
            ],
        ),
        coverage=_coverage(),
        taller_summary=None,
    )
    assert len(alerts) == 1
    assert alerts[0]["key"] == "fleet_availability:1"
    assert alerts[0]["severity"] == "critical"
    assert "Flota A" in alerts[0]["title"]
    assert alerts[0]["value"] == 92.5


def test_multiple_critical_fleets_generate_one_alert_each():
    alerts = evaluate_alerts(
        overview=_overview(
            overall={"mttr_hours": None},
            fleets=[
                {"customer_id": 1, "customer_name": "F1", "status": "critical", "availability_pct": 90.0},
                {"customer_id": 2, "customer_name": "F2", "status": "critical", "availability_pct": 91.0},
            ],
        ),
        coverage=_coverage(),
        taller_summary=None,
    )
    assert len(alerts) == 2
    assert _severities(alerts) == ["critical", "critical"]


# ── Backlog de cierre administrativo ────────────────────────────────────────


def test_pending_closure_30d_is_critical():
    alerts = evaluate_alerts(
        overview=_overview(),
        coverage=_coverage(),
        taller_summary={"pending_closure_7d": 3, "pending_closure_30d": 2, "overdue": 0},
    )
    assert "pending_closure_backlog_critical" in _alert_keys(alerts)
    assert all(a["severity"] != "warning" or a["key"] != "pending_closure_backlog_warning" for a in alerts)
    critical = next(a for a in alerts if a["key"] == "pending_closure_backlog_critical")
    assert critical["value"] == 2


def test_pending_closure_only_7d_is_warning():
    alerts = evaluate_alerts(
        overview=_overview(),
        coverage=_coverage(),
        taller_summary={"pending_closure_7d": 3, "pending_closure_30d": 0, "overdue": 0},
    )
    assert "pending_closure_backlog_warning" in _alert_keys(alerts)
    assert "pending_closure_backlog_critical" not in _alert_keys(alerts)


def test_no_pending_closure_no_alert():
    alerts = evaluate_alerts(
        overview=_overview(),
        coverage=_coverage(),
        taller_summary={"pending_closure_7d": 0, "pending_closure_30d": 0, "overdue": 0},
    )
    assert "pending_closure_backlog_critical" not in _alert_keys(alerts)
    assert "pending_closure_backlog_warning" not in _alert_keys(alerts)


# ── Órdenes vencidas ────────────────────────────────────────────────────────


def test_overdue_orders_warning():
    alerts = evaluate_alerts(
        overview=_overview(),
        coverage=_coverage(),
        taller_summary={"pending_closure_7d": 0, "pending_closure_30d": 0, "overdue": 5},
    )
    assert "overdue_orders" in _alert_keys(alerts)
    assert next(a for a in alerts if a["key"] == "overdue_orders")["value"] == 5


# ── Taller summary None ─────────────────────────────────────────────────────


def test_taller_summary_none_does_not_explode_and_skips_taller_alerts():
    alerts = evaluate_alerts(
        overview=_overview(),
        coverage=_coverage(),
        taller_summary=None,
    )
    assert "pending_closure_backlog_critical" not in _alert_keys(alerts)
    assert "pending_closure_backlog_warning" not in _alert_keys(alerts)
    assert "overdue_orders" not in _alert_keys(alerts)


# ── Ordenamiento y counts ───────────────────────────────────────────────────


def test_critical_alerts_come_first():
    alerts = evaluate_alerts(
        overview=_overview(
            overall={"mttr_hours": 25.0},
            fleets=[
                {"customer_id": 1, "customer_name": "F1", "status": "critical", "availability_pct": 90.0},
            ],
        ),
        coverage=_coverage(),
        taller_summary={"pending_closure_7d": 0, "pending_closure_30d": 1, "overdue": 0},
    )
    severities = _severities(alerts)
    assert severities.count("critical") == 2
    assert severities.count("warning") == 1
    # Los criticos deben ir antes que el warning.
    assert severities[0] == "critical"
    assert severities[1] == "critical"
    assert severities[2] == "warning"


def test_counts_match_alert_severities():
    alerts = evaluate_alerts(
        overview=_overview(
            overall={"mttr_hours": 50.0},
            fleets=[
                {"customer_id": 1, "customer_name": "F1", "status": "critical", "availability_pct": 90.0},
            ],
        ),
        coverage=_coverage(),
        taller_summary={"pending_closure_7d": 3, "pending_closure_30d": 0, "overdue": 2},
    )
    critical = sum(1 for a in alerts if a["severity"] == "critical")
    warning = sum(1 for a in alerts if a["severity"] == "warning")
    assert critical == 2  # mttr + fleet
    assert warning == 2  # backlog 7d + overdue
