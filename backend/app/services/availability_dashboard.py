"""
Agregacion de disponibilidad para el dashboard del modulo de Disponibilidad.

Solo LEE de `monthly_vehicle_availability` (poblada por la fase de disponibilidad
del job de rendimientos) y la cruza con el maestro local de placas/clientes.
No recalcula ni consulta CloudFleet.

Agrupacion: por Cliente (`vehicle_motor_assignments.customer_id` -> `customers.name`).
Cada cliente equivale a su costCenter en CloudFleet, por lo que el JOIN local es
suficiente.

Formula de flota (doc CALCULO_DISPONIBILIDAD.md, secciones 8.3/8.4):
  pct_flota = (SUM(h_total) - SUM(h_no_disp)) / SUM(h_total) * 100
Solo se suman las filas con `h_total` no nulo (estados 'calculated' / 'no_orders').
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

import psycopg
from psycopg.rows import dict_row

from app.services.availability_store import (
    AVAILABILITY_CATEGORIES,
    _SOURCE_CLOUDFLEET,
    _SYSTEM_CUSTOMER_NAME,
    _ensure_availability_table,
    list_monthly_availability,
)
from app.core.db import db_conn

_logger = logging.getLogger(__name__)

# Umbrales de clasificacion por flota (doc seccion 12 / 17). Ajustables.
FLEET_GOOD = 97.0
FLEET_WARNING = 96.0

# Umbrales de clasificacion de MTTR (doc seccion 9 / 17).
MTTR_GOOD = 24.0
MTTR_WARNING = 48.0

# Estados cuyo h_total cuenta para el denominador de disponibilidad.
_HOURS_STATUSES = ("calculated", "no_orders")
_ALL_STATUSES = ("calculated", "no_orders", "not_in_cloudfleet", "error")

def _classify(pct: float | None) -> str:
    """good / warning / critical segun los umbrales de flota."""
    if pct is None:
        return "no_data"
    if pct >= FLEET_GOOD:
        return "good"
    if pct >= FLEET_WARNING:
        return "warning"
    return "critical"


def _classify_mttr(hours: float | None) -> str:
    """good / warning / critical / no_data segun los umbrales de MTTR."""
    if hours is None:
        return "no_data"
    if hours <= MTTR_GOOD:
        return "good"
    if hours <= MTTR_WARNING:
        return "warning"
    return "critical"


def _empty_breakdown() -> dict[str, int]:
    return {status: 0 for status in _ALL_STATUSES}


def _empty_availability_breakdown() -> dict[str, int]:
    return {status: 0 for status in ("good", "warning", "critical", "no_data")}


def _pct_from_hours(h_total: float, h_no_disp: float) -> float | None:
    if h_total <= 0:
        return None
    pct = (h_total - h_no_disp) / h_total * 100.0
    return max(0.0, min(100.0, round(pct, 3)))


def _aggregate_rows(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """
    Agrega una lista de filas (cada una de una placa) en metricas de conjunto.
    Espera columnas: calculation_status, h_total, h_no_disp, mttr_hours,
    orders_closed.
    """
    h_total = 0.0
    h_no_disp = 0.0
    vehicle_count = 0
    mttr_weighted_numerator = 0.0
    mttr_closed_orders = 0
    breakdown = _empty_breakdown()
    availability_breakdown = _empty_availability_breakdown()
    for row in rows:
        vehicle_count += 1
        status = row.get("calculation_status")
        if status in breakdown:
            breakdown[status] += 1
        row_pct = row.get("project_availability_pct")
        availability_status = (
            _classify(float(row_pct))
            if status in _HOURS_STATUSES and row_pct is not None
            else "no_data"
        )
        availability_breakdown[availability_status] += 1
        if status in _HOURS_STATUSES and row.get("h_total") is not None:
            h_total += float(row["h_total"])
            h_no_disp += float(row.get("h_no_disp") or 0.0)

        row_mttr = row.get("mttr_hours")
        row_closed = int(row.get("orders_closed") or 0)
        if row_mttr is not None and row_closed > 0:
            mttr_weighted_numerator += float(row_mttr) * row_closed
            mttr_closed_orders += row_closed

    pct = _pct_from_hours(h_total, h_no_disp)
    if mttr_closed_orders > 0:
        mttr_hours = round(mttr_weighted_numerator / mttr_closed_orders, 3)
    else:
        mttr_hours = None

    return {
        "vehicle_count": vehicle_count,
        "h_total": round(h_total, 3),
        "h_no_disp": round(h_no_disp, 3),
        "availability_pct": pct,
        "status": _classify(pct),
        "status_breakdown": breakdown,
        "availability_breakdown": availability_breakdown,
        "mttr_hours": mttr_hours,
        "orders_closed": mttr_closed_orders,
        "mttr_status": _classify_mttr(mttr_hours),
    }


def _fetch_month_rows(month: str, *, customer_id: int | None = None) -> list[dict[str, Any]]:
    """
    Filas del mes con cliente y categoria efectiva elegible para este modulo.
    """
    _ensure_availability_table()
    where = [
        "mva.period_month = %s",
        "mva.source = %s",
        "c.name <> %s",
        "COALESCE(a.category, c.category, 'Ninguna') = ANY(%s)",
    ]
    params: list[Any] = [
        month,
        _SOURCE_CLOUDFLEET,
        _SYSTEM_CUSTOMER_NAME,
        list(AVAILABILITY_CATEGORIES),
    ]
    if customer_id is not None:
        where.append("a.customer_id = %s")
        params.append(customer_id)
    with db_conn(row_factory=dict_row) as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                f"""
                SELECT mva.plate,
                       mva.calculation_status,
                       mva.project_availability_pct,
                       mva.h_total,
                       mva.h_no_disp,
                       mva.orders_considered,
                       mva.mttr_hours,
                       mva.orders_closed,
                       a.customer_id,
                       c.name AS customer_name
                FROM monthly_vehicle_availability mva
                JOIN vehicle_motor_assignments a ON a.plate = mva.plate
                JOIN customers c ON c.id = a.customer_id
                WHERE {" AND ".join(where)}
                ORDER BY mva.plate ASC;
                """,
                params,
            )
            return [dict(row) for row in cur.fetchall()]


def get_availability_overview(month: str, *, customer_id: int | None = None) -> dict[str, Any]:
    """
    Resumen del mes: agregado global + por flota (cliente).
    Las flotas se devuelven ordenadas por disponibilidad ascendente (peores
    primero); las flotas sin pct calculable van al final.
    """
    rows = _fetch_month_rows(month, customer_id=customer_id)

    fleets_rows: dict[Any, dict[str, Any]] = {}
    for row in rows:
        key = row.get("customer_id")
        bucket = fleets_rows.setdefault(
            key,
            {"customer_id": key, "customer_name": row.get("customer_name"), "rows": []},
        )
        bucket["rows"].append(row)

    fleets: list[dict[str, Any]] = []
    for bucket in fleets_rows.values():
        agg = _aggregate_rows(bucket["rows"])
        fleets.append(
            {
                "customer_id": bucket["customer_id"],
                "customer_name": bucket["customer_name"],
                **agg,
            }
        )

    # Peores primero; None (sin pct) al final.
    fleets.sort(key=lambda f: (f["availability_pct"] is None, f["availability_pct"] if f["availability_pct"] is not None else 0.0))

    overall = _aggregate_rows(rows)
    overall["critical_fleets"] = sum(1 for f in fleets if f["status"] == "critical")
    overall["fleet_count"] = len(fleets)

    return {
        "month": month,
        "generated_at": datetime.now().isoformat(),
        "overall": overall,
        "fleets": fleets,
    }


def get_vehicle_ranking(
    month: str,
    *,
    customer_id: int | None = None,
    limit: int = 20,
    order: str = "worst",
    include_no_orders: bool = False,
    plate_search: str | None = None,
    availability_status: str | None = None,
) -> list[dict[str, Any]]:
    """
    Ranking de vehiculos por disponibilidad.

    Por defecto solo incluye placas con estado 'calculated' (las unicas con un
    pct comparable). `order='worst'` => peor disponibilidad primero.

    Si `include_no_orders=True`, tambien se incluyen las placas con estado
    'no_orders' (100% de disponibilidad almacenado), ubicandose al final del
    orden 'best' o al inicio del orden 'worst'.

    Si `plate_search` tiene texto, filtra las placas cuyo valor contenga ese
    substring (case-insensitive). Vacio o None no aplica filtro de placa.
    """
    if availability_status == "no_data":
        allowed_statuses = {"not_in_cloudfleet", "error"}
    else:
        allowed_statuses = {"calculated"}
        if include_no_orders:
            allowed_statuses.add("no_orders")

    rows = [
        row
        for row in _fetch_month_rows(month, customer_id=customer_id)
        if row.get("calculation_status") in allowed_statuses
    ]

    if availability_status and availability_status != "no_data":
        rows = [
            row
            for row in rows
            if row.get("project_availability_pct") is not None
            and _classify(float(row["project_availability_pct"])) == availability_status
        ]
    elif availability_status != "no_data":
        rows = [row for row in rows if row.get("project_availability_pct") is not None]

    normalized_search = (plate_search or "").strip().upper()
    if normalized_search:
        rows = [row for row in rows if normalized_search in str(row.get("plate", "")).upper()]

    def _rank(row: dict[str, Any]) -> tuple[int, float]:
        raw_pct = row.get("project_availability_pct")
        pct = float(raw_pct) if raw_pct is not None else 0.0
        # 'calculated' siempre tiene prioridad sobre 'no_orders' dentro del
        # mismo sentido de ordenamiento; en 'best' las no_orders quedan al final.
        status_rank = 0 if row.get("calculation_status") == "calculated" else 1
        if order == "best":
            return (status_rank, -pct)
        return (status_rank, pct)

    rows.sort(key=_rank)
    limited = rows[: max(1, min(limit, 5000))]
    return [
        {
            "plate": row["plate"],
            "customer_id": row.get("customer_id"),
            "customer_name": row.get("customer_name"),
            "availability_pct": round(float(row["project_availability_pct"]), 3)
            if row.get("project_availability_pct") is not None
            else None,
            "h_no_disp": round(float(row.get("h_no_disp") or 0.0), 3),
            "h_total": round(float(row.get("h_total") or 0.0), 3),
            "orders_considered": int(row.get("orders_considered") or 0),
            "calculation_status": row.get("calculation_status") or "calculated",
            "status": _classify(
                float(row["project_availability_pct"])
                if row.get("project_availability_pct") is not None
                else None
            ),
            "mttr_hours": round(float(row["mttr_hours"]), 3) if row.get("mttr_hours") is not None else None,
            "orders_closed": int(row.get("orders_closed") or 0),
        }
        for row in limited
    ]


def get_cloudfleet_coverage(month: str, *, customer_id: int | None = None) -> dict[str, Any]:
    """
    Reconciliacion de cobertura CloudFleet para el mes: cuantas placas tienen
    datos calculados vs cuantas no estan en CloudFleet.
    """
    rows = _fetch_month_rows(month, customer_id=customer_id)

    total = len(rows)
    covered = 0
    uncovered = 0
    error = 0

    fleets_rows: dict[Any, dict[str, Any]] = {}
    uncovered_plates: list[dict[str, Any]] = []

    for row in rows:
        status = row.get("calculation_status")
        if status in _HOURS_STATUSES:
            covered += 1
        elif status == "not_in_cloudfleet":
            uncovered += 1
            uncovered_plates.append(
                {
                    "plate": row["plate"],
                    "customer_id": row.get("customer_id"),
                    "customer_name": row.get("customer_name"),
                }
            )
        elif status == "error":
            error += 1

        key = row.get("customer_id")
        bucket = fleets_rows.setdefault(
            key,
            {
                "customer_id": key,
                "customer_name": row.get("customer_name"),
                "total": 0,
                "covered": 0,
                "uncovered": 0,
                "error": 0,
            },
        )
        bucket["total"] += 1
        if status in _HOURS_STATUSES:
            bucket["covered"] += 1
        if status == "not_in_cloudfleet":
            bucket["uncovered"] += 1
        elif status == "error":
            bucket["error"] += 1

    coverage_pct = round(covered / total * 100.0, 1) if total > 0 else None

    # Los vehiculos que solo existen en CloudFleet no tienen cliente/categoria
    # local verificable y, por definicion, quedan fuera de este modulo.
    cloudfleet_unmatched_payload: list[dict[str, Any]] = []

    fleets = [
        {
            "customer_id": bucket["customer_id"],
            "customer_name": bucket["customer_name"],
            "total": bucket["total"],
            "covered": bucket["covered"],
            "uncovered": bucket["uncovered"],
            "error": bucket["error"],
            "coverage_pct": round(bucket["covered"] / bucket["total"] * 100.0, 1)
            if bucket["total"] > 0
            else None,
        }
        for bucket in fleets_rows.values()
    ]
    fleets.sort(key=lambda f: f["uncovered"], reverse=True)

    uncovered_plates.sort(key=lambda p: (p["customer_name"] or "", p["plate"]))

    return {
        "month": month,
        "generated_at": datetime.now().isoformat(),
        "summary": {
            "total": total,
            "covered": covered,
            "uncovered": uncovered,
            "error": error,
            "coverage_pct": coverage_pct,
            "cloudfleet_only": len(cloudfleet_unmatched_payload),
        },
        "fleets": fleets,
        "uncovered_plates": uncovered_plates,
        "cloudfleet_unmatched": cloudfleet_unmatched_payload,
    }


def _add_months(month: str, delta: int) -> str:
    year, mon = (int(p) for p in month.split("-"))
    index = (year * 12 + (mon - 1)) + delta
    new_year, new_mon = divmod(index, 12)
    return f"{new_year:04d}-{new_mon + 1:02d}"


def get_availability_trend(
    month_to: str,
    *,
    months: int = 6,
    customer_id: int | None = None,
) -> dict[str, Any]:
    """
    Serie temporal de disponibilidad (global o de una flota) para los ultimos
    `months` meses terminando en `month_to`. Reutiliza la lectura por rango de
    availability_store y agrega por mes.
    """
    months = max(1, min(months, 24))
    month_from = _add_months(month_to, -(months - 1))
    raw = list_monthly_availability(month_from=month_from, month_to=month_to)

    eligible_plates = _eligible_plates(customer_id)

    by_month: dict[str, list[dict[str, Any]]] = {}
    for row in raw:
        if row.get("plate") not in eligible_plates:
            continue
        by_month.setdefault(row["period_month"], []).append(row)

    labels: list[str] = []
    series: list[float | None] = []
    for offset in range(months):
        month = _add_months(month_from, offset)
        labels.append(month)
        agg = _aggregate_rows(by_month.get(month, []))
        series.append(agg["availability_pct"])

    return {
        "month_from": month_from,
        "month_to": month_to,
        "customer_id": customer_id,
        "labels": labels,
        "availability_pct": series,
    }


def _eligible_plates(customer_id: int | None = None) -> set[str]:
    where = [
        "c.name <> %s",
        "COALESCE(a.category, c.category, 'Ninguna') = ANY(%s)",
    ]
    params: list[Any] = [_SYSTEM_CUSTOMER_NAME, list(AVAILABILITY_CATEGORIES)]
    if customer_id is not None:
        where.append("a.customer_id = %s")
        params.append(customer_id)
    with db_conn(row_factory=dict_row) as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                f"""
                SELECT a.plate
                FROM vehicle_motor_assignments a
                JOIN customers c ON c.id = a.customer_id
                WHERE {" AND ".join(where)};
                """,
                params,
            )
            return {str(row["plate"]) for row in cur.fetchall() if row.get("plate")}


__all__ = [
    "get_availability_overview",
    "get_vehicle_ranking",
    "get_availability_trend",
    "get_cloudfleet_coverage",
    "FLEET_GOOD",
    "FLEET_WARNING",
]
