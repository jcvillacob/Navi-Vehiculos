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

# Estados cuyo h_total cuenta para el denominador de disponibilidad.
_HOURS_STATUSES = ("calculated", "no_orders")
_ALL_STATUSES = ("calculated", "no_orders", "not_in_cloudfleet", "error")

_UNASSIGNED_LABEL = "Sin cliente"


def _classify(pct: float | None) -> str:
    """good / warning / critical segun los umbrales de flota."""
    if pct is None:
        return "no_data"
    if pct >= FLEET_GOOD:
        return "good"
    if pct >= FLEET_WARNING:
        return "warning"
    return "critical"


def _empty_breakdown() -> dict[str, int]:
    return {status: 0 for status in _ALL_STATUSES}


def _pct_from_hours(h_total: float, h_no_disp: float) -> float | None:
    if h_total <= 0:
        return None
    pct = (h_total - h_no_disp) / h_total * 100.0
    return max(0.0, min(100.0, round(pct, 3)))


def _aggregate_rows(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """
    Agrega una lista de filas (cada una de una placa) en metricas de conjunto.
    Espera columnas: calculation_status, h_total, h_no_disp.
    """
    h_total = 0.0
    h_no_disp = 0.0
    vehicle_count = 0
    breakdown = _empty_breakdown()
    for row in rows:
        vehicle_count += 1
        status = row.get("calculation_status")
        if status in breakdown:
            breakdown[status] += 1
        if status in _HOURS_STATUSES and row.get("h_total") is not None:
            h_total += float(row["h_total"])
            h_no_disp += float(row.get("h_no_disp") or 0.0)
    pct = _pct_from_hours(h_total, h_no_disp)
    return {
        "vehicle_count": vehicle_count,
        "h_total": round(h_total, 3),
        "h_no_disp": round(h_no_disp, 3),
        "availability_pct": pct,
        "status": _classify(pct),
        "status_breakdown": breakdown,
    }


def _fetch_month_rows(month: str, *, customer_id: int | None = None) -> list[dict[str, Any]]:
    """
    Filas de disponibilidad del mes cruzadas con cliente. Una fila por placa con
    asignacion conocida (INNER JOIN con vehicle_motor_assignments).
    """
    _ensure_availability_table()
    where = [
        "mva.period_month = %s",
        "mva.source = %s",
        "(c.name IS NULL OR c.name <> %s)",
    ]
    params: list[Any] = [month, _SOURCE_CLOUDFLEET, _SYSTEM_CUSTOMER_NAME]
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
                       a.customer_id,
                       COALESCE(c.name, %s) AS customer_name
                FROM monthly_vehicle_availability mva
                JOIN vehicle_motor_assignments a ON a.plate = mva.plate
                LEFT JOIN customers c ON c.id = a.customer_id
                WHERE {" AND ".join(where)}
                ORDER BY mva.plate ASC;
                """,
                [_UNASSIGNED_LABEL, *params],
            )
            return [dict(row) for row in cur.fetchall()]


def get_availability_overview(month: str) -> dict[str, Any]:
    """
    Resumen del mes: agregado global + por flota (cliente).
    Las flotas se devuelven ordenadas por disponibilidad ascendente (peores
    primero); las flotas sin pct calculable van al final.
    """
    rows = _fetch_month_rows(month)

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
) -> list[dict[str, Any]]:
    """
    Ranking de vehiculos por disponibilidad. Solo placas con estado 'calculated'
    (las unicas con un pct comparable). `order='worst'` => peor disponibilidad
    primero.
    """
    rows = [
        row
        for row in _fetch_month_rows(month, customer_id=customer_id)
        if row.get("calculation_status") == "calculated"
        and row.get("project_availability_pct") is not None
    ]
    reverse = order == "best"
    rows.sort(key=lambda r: float(r["project_availability_pct"]), reverse=reverse)
    limited = rows[: max(1, min(limit, 200))]
    return [
        {
            "plate": row["plate"],
            "customer_id": row.get("customer_id"),
            "customer_name": row.get("customer_name"),
            "availability_pct": round(float(row["project_availability_pct"]), 3),
            "h_no_disp": round(float(row.get("h_no_disp") or 0.0), 3),
            "h_total": round(float(row.get("h_total") or 0.0), 3),
            "orders_considered": int(row.get("orders_considered") or 0),
            "status": _classify(float(row["project_availability_pct"])),
        }
        for row in limited
    ]


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

    customer_plates: set[str] | None = None
    if customer_id is not None:
        customer_plates = _plates_for_customer(customer_id)

    by_month: dict[str, list[dict[str, Any]]] = {}
    for row in raw:
        if customer_plates is not None and row.get("plate") not in customer_plates:
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


def _plates_for_customer(customer_id: int) -> set[str]:
    with db_conn(row_factory=dict_row) as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                "SELECT plate FROM vehicle_motor_assignments WHERE customer_id = %s;",
                (customer_id,),
            )
            return {str(row["plate"]) for row in cur.fetchall() if row.get("plate")}


__all__ = [
    "get_availability_overview",
    "get_vehicle_ranking",
    "get_availability_trend",
    "FLEET_GOOD",
    "FLEET_WARNING",
]
