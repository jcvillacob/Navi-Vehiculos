"""
Alertas operativas del dashboard.

Combina disponibilidad mensual, cobertura CloudFleet y el estado del monitor de
ordenes de taller para generar alertas críticas/warning. La funcion pura
`evaluate_alerts` es testable sin red; `get_operational_alerts` orquesta las
lecturas reales.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from app.services.availability_dashboard import (
    get_availability_overview,
    get_cloudfleet_coverage,
)
from app.services.taller_ordenes import peek_cached_orders

_logger = logging.getLogger(__name__)

_BOGOTA = ZoneInfo("America/Bogota")


def _current_month_bogota() -> str:
    return datetime.now(_BOGOTA).strftime("%Y-%m")


def _fmt_hours(value: float | None) -> str:
    if value is None:
        return "—"
    return f"{value:.1f}h"


def evaluate_alerts(
    *,
    overview: dict[str, Any],
    coverage: dict[str, Any],
    taller_summary: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    """
    Funcion pura. Recibe el resumen de disponibilidad del mes, la cobertura
    CloudFleet y el summary del monitor de taller (o None) y devuelve una lista
    de alertas ordenadas por severidad (critical primero).
    """
    alerts: list[dict[str, Any]] = []

    overall = overview.get("overall") or {}
    mttr = overall.get("mttr_hours")
    if isinstance(mttr, (int, float)):
        if mttr > 48:
            alerts.append(
                {
                    "key": "mttr_critical",
                    "severity": "critical",
                    "title": "MTTR crítico",
                    "detail": f"El MTTR global del mes es {_fmt_hours(mttr)}, supera el umbral de 48h.",
                    "value": round(float(mttr), 3),
                }
            )
        elif mttr > 24:
            alerts.append(
                {
                    "key": "mttr_warning",
                    "severity": "warning",
                    "title": "MTTR elevado",
                    "detail": f"El MTTR global del mes es {_fmt_hours(mttr)} (entre 24h y 48h).",
                    "value": round(float(mttr), 3),
                }
            )

    for fleet in overview.get("fleets") or []:
        if fleet.get("status") == "critical":
            pct = fleet.get("availability_pct")
            pct_text = f"{pct:.2f}%" if isinstance(pct, (int, float)) else "—"
            alerts.append(
                {
                    "key": f"fleet_availability:{fleet.get('customer_id')}",
                    "severity": "critical",
                    "title": f"Flota crítica: {fleet.get('customer_name') or 'Sin nombre'}",
                    "detail": f"Disponibilidad {pct_text}, por debajo del umbral crítico.",
                    "value": pct,
                }
            )

    if taller_summary is not None:
        pending_30d = int(taller_summary.get("pending_closure_30d") or 0)
        pending_7d = int(taller_summary.get("pending_closure_7d") or 0)
        overdue = int(taller_summary.get("overdue") or 0)

        if pending_30d > 0:
            alerts.append(
                {
                    "key": "pending_closure_backlog_critical",
                    "severity": "critical",
                    "title": "Backlog de cierre administrativo >30 días",
                    "detail": f"Hay {pending_30d} orden(es) pendientes de cierre por más de 30 días.",
                    "value": pending_30d,
                }
            )
        elif pending_7d > 0:
            alerts.append(
                {
                    "key": "pending_closure_backlog_warning",
                    "severity": "warning",
                    "title": "Backlog de cierre administrativo >7 días",
                    "detail": f"Hay {pending_7d} orden(es) pendientes de cierre por más de 7 días.",
                    "value": pending_7d,
                }
            )

        if overdue > 0:
            alerts.append(
                {
                    "key": "overdue_orders",
                    "severity": "warning",
                    "title": "Órdenes de taller vencidas",
                    "detail": f"Hay {overdue} orden(es) activa(s) excedida(s) en tiempo de entrega.",
                    "value": overdue,
                }
            )

    # coverage no dispara alertas en v1, pero queda disponible para futuras reglas.
    _ = coverage

    severity_rank = {"critical": 0, "warning": 1}
    alerts.sort(key=lambda a: severity_rank.get(a.get("severity"), 99))
    return alerts


def get_operational_alerts() -> dict[str, Any]:
    """
    Orquesta la generacion de alertas operativas del mes actual en Bogota.

    - overview y coverage se consultan directamente (lecturas rapidas de DB).
    - taller: solo usa el cache vigente de ordenes activas; si esta frio se
      pasa None para no penalizar el tiempo de respuesta del dashboard.
    """
    month = _current_month_bogota()
    overview = get_availability_overview(month)
    coverage = get_cloudfleet_coverage(month)

    cached_taller = peek_cached_orders()
    taller_summary = cached_taller.get("summary") if cached_taller else None

    alerts = evaluate_alerts(
        overview=overview,
        coverage=coverage,
        taller_summary=taller_summary,
    )

    counts = {"critical": 0, "warning": 0}
    for alert in alerts:
        sev = alert.get("severity")
        if sev in counts:
            counts[sev] += 1

    return {
        "generated_at": datetime.now(_BOGOTA).isoformat(),
        "month": month,
        "alerts": alerts,
        "counts": counts,
    }


__all__ = [
    "evaluate_alerts",
    "get_operational_alerts",
]
