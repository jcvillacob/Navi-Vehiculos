"""
Persistencia + orquestacion del calculo de disponibilidad por vehiculo.

- DDL idempotente para `monthly_vehicle_availability`.
- UPSERT por (plate, period_month, source).
- Orquestador `run_availability_phase()` que el job de rendimientos llama
  cuando el flag `compute_availability=true` esta presente.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any, Callable

import psycopg
from psycopg.rows import dict_row

from app.clients.cloudfleet_client import (
    CloudFleetAuthError,
    CloudFleetUnavailableError,
    list_vehicles,
    list_work_orders,
)
from app.core.config import load_cloudfleet_config
from app.core.db import db_conn
from app.services.availability import (
    AvailabilityResult,
    AvailabilityTarget,
    _month_bounds_utc,
    _normalize_plate,
    calculate_availability_for_targets,
    dedupe_work_orders,
    find_unmatched_cloudfleet_vehicles,
)

_logger = logging.getLogger(__name__)

_TABLE_BOOTSTRAPPED = False
_SOURCE_CLOUDFLEET = "cloudfleet"

# Cliente interno de rendimientos ad-hoc (Geotab). La disponibilidad es solo
# para clientes reales, por lo que se excluye de todas las queries del modulo.
_SYSTEM_CUSTOMER_NAME = "__navitrans_system__"

# Dias hacia atras que miramos en `updatedAt` al consultar work-orders.
# Captura ordenes abiertas viejas que no fueron tocadas durante el mes pero
# siguen restando disponibilidad, sin depender de que updatedAt caiga dentro
# del rango del mes.
_ORDER_LOOKBACK_DAYS = 90


def _ensure_availability_table() -> None:
    """
    Bootstrap idempotente — corre la DDL una sola vez por proceso. Igual patron
    que `_ensure_jobs_table` en rendimientos_jobs.py.
    """
    global _TABLE_BOOTSTRAPPED
    if _TABLE_BOOTSTRAPPED:
        return
    with db_conn() as own_conn:
        with own_conn.cursor() as cur:
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS monthly_vehicle_availability (
                    id BIGSERIAL PRIMARY KEY,
                    plate TEXT NOT NULL,
                    period_month TEXT NOT NULL,
                    calculation_status TEXT NOT NULL
                        CHECK (calculation_status IN
                            ('calculated','no_orders','not_in_cloudfleet','error')),
                    project_availability_pct NUMERIC(6,3) NULL,
                    h_total NUMERIC(10,3) NULL,
                    h_no_disp NUMERIC(10,3) NULL,
                    orders_considered INTEGER NOT NULL DEFAULT 0,
                    error_message TEXT NULL,
                    last_calculated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    source TEXT NOT NULL DEFAULT 'cloudfleet'
                );
                """
            )
            cur.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS monthly_vehicle_availability_unique
                    ON monthly_vehicle_availability (plate, period_month, source);
                """
            )
            cur.execute(
                """
                CREATE INDEX IF NOT EXISTS monthly_vehicle_availability_month_idx
                    ON monthly_vehicle_availability (period_month);
                """
            )
            cur.execute(
                """
                ALTER TABLE monthly_vehicle_availability
                    ADD COLUMN IF NOT EXISTS mttr_hours NUMERIC(10,3) NULL;
                """
            )
            cur.execute(
                """
                ALTER TABLE monthly_vehicle_availability
                    ADD COLUMN IF NOT EXISTS orders_closed INTEGER NOT NULL DEFAULT 0;
                """
            )
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS cloudfleet_unmatched_vehicles (
                    id BIGSERIAL PRIMARY KEY,
                    period_month TEXT NOT NULL,
                    code TEXT NOT NULL,
                    cost_center TEXT NULL,
                    last_seen_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    UNIQUE (period_month, code)
                );
                """
            )
            cur.execute(
                """
                CREATE INDEX IF NOT EXISTS cloudfleet_unmatched_vehicles_month_idx
                    ON cloudfleet_unmatched_vehicles (period_month);
                """
            )
        own_conn.commit()
    _TABLE_BOOTSTRAPPED = True


# ─────────────────────────────────────────────────────────────────────────────
# Persistencia
# ─────────────────────────────────────────────────────────────────────────────


def _upsert_result(
    conn: psycopg.Connection,
    *,
    period_month: str,
    result: AvailabilityResult,
) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO monthly_vehicle_availability (
                plate, period_month, calculation_status,
                project_availability_pct, h_total, h_no_disp,
                orders_considered, mttr_hours, orders_closed,
                error_message,
                last_calculated_at, source
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW(), %s)
            ON CONFLICT (plate, period_month, source) DO UPDATE SET
                calculation_status = EXCLUDED.calculation_status,
                project_availability_pct = EXCLUDED.project_availability_pct,
                h_total = EXCLUDED.h_total,
                h_no_disp = EXCLUDED.h_no_disp,
                orders_considered = EXCLUDED.orders_considered,
                mttr_hours = EXCLUDED.mttr_hours,
                orders_closed = EXCLUDED.orders_closed,
                error_message = EXCLUDED.error_message,
                last_calculated_at = NOW();
            """,
            (
                result.plate,
                period_month,
                result.status,
                result.project_availability_pct,
                result.h_total,
                result.h_no_disp,
                result.orders_considered,
                result.mttr_hours,
                result.orders_closed,
                result.error_message,
                _SOURCE_CLOUDFLEET,
            ),
        )


def _sync_cloudfleet_unmatched(
    conn: psycopg.Connection,
    *,
    period_month: str,
    unmatched: list[dict[str, Any]],
) -> None:
    """
    Reemplaza los registros de vehiculos CloudFleet no registrados localmente
    para el mes dado. `unmatched` viene de `find_unmatched_cloudfleet_vehicles`.
    """
    with conn.cursor() as cur:
        cur.execute(
            "DELETE FROM cloudfleet_unmatched_vehicles WHERE period_month = %s",
            (period_month,),
        )
        if unmatched:
            cur.executemany(
                """
                INSERT INTO cloudfleet_unmatched_vehicles
                    (period_month, code, cost_center, last_seen_at)
                VALUES (%s, %s, %s, NOW())
                ON CONFLICT (period_month, code) DO UPDATE SET
                    cost_center = EXCLUDED.cost_center,
                    last_seen_at = NOW();
                """,
                [
                    (period_month, item["code"], item["cost_center"])
                    for item in unmatched
                ],
            )


def list_unmatched_cloudfleet(month: str) -> list[dict[str, Any]]:
    """
    Devuelve los codigos CloudFleet del mes que no tienen placa local asociada.
    """
    _ensure_availability_table()
    with db_conn(row_factory=dict_row) as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                """
                SELECT code, cost_center, last_seen_at
                FROM cloudfleet_unmatched_vehicles
                WHERE period_month = %s
                ORDER BY code ASC;
                """,
                (month,),
            )
            return [dict(row) for row in cur.fetchall()]


def list_monthly_availability(
    *,
    month_from: str,
    month_to: str,
) -> list[dict[str, Any]]:
    """
    Devuelve las filas persistidas en el rango pedido.
    """
    if month_to < month_from:
        month_from, month_to = month_to, month_from
    _ensure_availability_table()
    with db_conn(row_factory=dict_row) as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                """
                SELECT plate,
                       period_month,
                       calculation_status,
                       project_availability_pct,
                       h_total,
                       h_no_disp,
                       orders_considered,
                       mttr_hours,
                       orders_closed,
                       error_message,
                       last_calculated_at,
                       source
                FROM monthly_vehicle_availability
                WHERE period_month BETWEEN %s AND %s
                  AND source = %s
                ORDER BY period_month ASC, plate ASC;
                """,
                (month_from, month_to, _SOURCE_CLOUDFLEET),
            )
            return [dict(row) for row in cur.fetchall()]


# ─────────────────────────────────────────────────────────────────────────────
# Targets — placas a evaluar segun customers seleccionados
# ─────────────────────────────────────────────────────────────────────────────


def _load_plates_for_customers(
    customer_ids: list[int],
) -> list[AvailabilityTarget]:
    """
    Devuelve TODAS las placas asignadas a los customers indicados
    (sin filtro por provider, a diferencia de la fase de rendimientos).
    Si la lista es vacia, devuelve todas las placas.

    Excluye placas asignadas al customer interno `__navitrans_system__`
    (rendimientos ad-hoc). Las placas con `customer_id` NULL siguen entrando.
    """
    params: list[Any] = []
    where = ["1 = 1", "(c.name IS NULL OR c.name <> %s)"]
    params.append(_SYSTEM_CUSTOMER_NAME)
    if customer_ids:
        where.append("a.customer_id = ANY(%s)")
        params.append(sorted({int(c) for c in customer_ids}))

    with db_conn(row_factory=dict_row) as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                f"""
                SELECT DISTINCT a.plate
                FROM vehicle_motor_assignments a
                LEFT JOIN customers c ON c.id = a.customer_id
                WHERE {" AND ".join(where)}
                ORDER BY a.plate ASC;
                """,
                params,
            )
            rows = cur.fetchall()

    return [AvailabilityTarget(plate=str(row["plate"])) for row in rows if row.get("plate")]


def _load_all_local_plates_normalized() -> set[str]:
    """
    Todas las placas locales normalizadas, SIN filtro de customer.

    Se excluyen las placas asignadas al customer interno `__navitrans_system__`
    (rendimientos ad-hoc). Esto garantiza que la deteccion de vehiculos CloudFleet
    "sin match local" sea consistente aunque `run_availability_phase` se invoque
    con un subconjunto de customers: los filtros no deben hacer que placas locales
    de otros clientes parezcan "fantasmas" en CloudFleet.
    """
    with db_conn(row_factory=dict_row) as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                """
                SELECT DISTINCT a.plate
                FROM vehicle_motor_assignments a
                LEFT JOIN customers c ON c.id = a.customer_id
                WHERE c.name IS NULL OR c.name <> %s;
                """,
                (_SYSTEM_CUSTOMER_NAME,),
            )
            rows = cur.fetchall()

    plates: set[str] = set()
    for row in rows:
        plate = row.get("plate")
        if plate:
            plates.add(_normalize_plate(plate))
    return plates


# ─────────────────────────────────────────────────────────────────────────────
# Orquestacion — la llama rendimientos_jobs.run_job
# ─────────────────────────────────────────────────────────────────────────────


def _summary_from_results(results: list[AvailabilityResult]) -> dict[str, int]:
    summary = {
        "total": len(results),
        "calculated": 0,
        "no_orders": 0,
        "not_in_cloudfleet": 0,
        "error": 0,
    }
    for r in results:
        if r.status in summary:
            summary[r.status] += 1
    return summary


def run_availability_phase(
    *,
    month: str,
    customer_ids: list[int],
    progress_callback: Callable[[int], None] | None = None,
) -> dict[str, Any]:
    """
    Ejecuta el calculo de disponibilidad para todos los vehiculos de los
    customers seleccionados.

    - Consulta CloudFleet una sola vez (vehicles + work-orders del mes).
    - Itera placa por placa, persistiendo cada resultado (UPSERT).
    - `progress_callback(processed)` se llama despues de cada placa procesada
      para que el job principal pueda actualizar processed_targets.

    Levanta excepciones explicitas si CloudFleet no esta configurado o si la
    API falla — el caller (rendimientos_jobs.run_job) las debe atrapar y
    marcar el job como `error`.
    """
    config = load_cloudfleet_config()
    if config is None:
        raise RuntimeError(
            "CloudFleet no esta configurado: falta CLOUDFLEET_API_KEY en el .env."
        )

    _ensure_availability_table()

    targets = _load_plates_for_customers(customer_ids)
    if not targets:
        _logger.info("Disponibilidad %s: no hay placas para los customers seleccionados.", month)
        return {
            "total": 0,
            "calculated": 0,
            "no_orders": 0,
            "not_in_cloudfleet": 0,
            "error": 0,
        }

    start_utc, end_utc = _month_bounds_utc(month)
    updated_at_from = start_utc - timedelta(days=_ORDER_LOOKBACK_DAYS)
    updated_at_to = max(end_utc, datetime.now() + timedelta(days=1))

    _logger.info(
        "Disponibilidad %s: descargando inventario CloudFleet (placas locales=%d).",
        month,
        len(targets),
    )
    try:
        vehicles = list_vehicles(config)
        orders = list_work_orders(
            config,
            updated_at_from=updated_at_from,
            updated_at_to=updated_at_to,
        )
    except CloudFleetAuthError:
        raise
    except CloudFleetUnavailableError:
        raise
    except Exception as exc:
        # Cualquier otra excepcion del cliente se traduce a Unavailable para que
        # el job marque error sin colapsar el proceso.
        raise CloudFleetUnavailableError(
            f"Error inesperado consultando CloudFleet: {exc}"
        ) from exc

    deduped_orders = dedupe_work_orders(orders)
    dropped = len(orders) - len(deduped_orders)
    if dropped:
        _logger.info(
            "Disponibilidad %s: se descartaron %d ordenes duplicadas (de %d a %d).",
            month,
            dropped,
            len(orders),
            len(deduped_orders),
        )

    # Deteccion bidireccional: placas CloudFleet que no existen localmente.
    # Se usa el universo COMPLETO de placas locales (sin filtro de customer) para
    # evitar falsos positivos cuando el calculo se filtra a un subconjunto.
    local_plates = _load_all_local_plates_normalized()
    cloudfleet_unmatched = find_unmatched_cloudfleet_vehicles(vehicles, local_plates)
    _logger.info(
        "Disponibilidad %s: %d vehiculos CloudFleet no registrados localmente.",
        month,
        len(cloudfleet_unmatched),
    )

    results = calculate_availability_for_targets(
        targets,
        month=month,
        cloudfleet_vehicles=vehicles,
        cloudfleet_work_orders=deduped_orders,
        now=datetime.now(),
    )

    processed = 0
    with db_conn() as conn:
        _sync_cloudfleet_unmatched(
            conn, period_month=month, unmatched=cloudfleet_unmatched
        )
        for result in results:
            _upsert_result(conn, period_month=month, result=result)
            processed += 1
            if progress_callback is not None:
                try:
                    progress_callback(processed)
                except Exception:
                    pass
            # Commit per-row para que la barra de progreso del front vea avance
            # real y para no atrapar locks largos.
            conn.commit()

    summary = _summary_from_results(results)
    _logger.info(
        "Disponibilidad %s lista: calc=%d no_orders=%d not_in_cf=%d err=%d",
        month,
        summary["calculated"],
        summary["no_orders"],
        summary["not_in_cloudfleet"],
        summary["error"],
    )
    return summary


__all__ = [
    "run_availability_phase",
    "list_monthly_availability",
    "list_unmatched_cloudfleet",
]
