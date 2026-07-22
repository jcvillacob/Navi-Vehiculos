"""
Calculo de MTBF (Mean Time Between Failures) del año en curso.

Documentacion de negocio: docs/CALCULO_DISPONIBILIDAD.md seccion 9, gauge 4.
  - MTBF = promedio de horas entre fallas consecutivas del mismo vehiculo.
  - "Falla" = orden con type != 'Programado' y startDate en el año.
  - Solo vehiculos con >= 2 fallas aportan intervalos.
  - Umbrales (seccion 17): good >= 500 h, warning >= 300 h, critical < 300 h.

El modulo expone:
  - compute_mtbf(orders, plate_customer_map, *, year, now) -> dict
    Logica pura, testable sin red ni DB.
  - get_mtbf_summary(force_refresh=False) -> dict
    Orquestador on-demand con cache en memoria de 1 hora.
"""
from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timedelta
from typing import Any

from psycopg.rows import dict_row

from app.clients.cloudfleet_client import (
    CloudFleetAuthError,
    CloudFleetUnavailableError,
    list_work_orders,
)
from app.core.config import load_cloudfleet_config
from app.core.db import db_conn
from app.services.availability import (
    _normalize_plate,
    _parse_utc_to_local,
    dedupe_work_orders,
)
from app.services.availability_store import AVAILABILITY_CATEGORIES, _SYSTEM_CUSTOMER_NAME

_logger = logging.getLogger(__name__)

# Cache en memoria del resumen anual (patron _ACTIVE_ORDERS_CACHE).
_CACHE_TTL_SECONDS = 3600
_MTBF_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}
_CACHE_LOCK = threading.Lock()

# Ventana maxima de consulta CloudFleet por updatedAt (< 180 dias).
_WINDOW_DAYS = 170

# Umbrales de clasificacion MTBF (doc seccion 17).
_MTBF_GOOD = 500.0
_MTBF_WARNING = 300.0


def _classify_mtbf(hours: float | None) -> str:
    """good / warning / critical / no_data segun umbrales del doc."""
    if hours is None:
        return "no_data"
    if hours >= _MTBF_GOOD:
        return "good"
    if hours >= _MTBF_WARNING:
        return "warning"
    return "critical"


def _is_failure(order: dict[str, Any]) -> bool:
    """True si la orden es una falla (type normalizado != 'programado')."""
    raw_type = str(order.get("type") or "").strip().lower()
    return raw_type != "programado"


def _start_date_in_year(order: dict[str, Any], year: int) -> datetime | None:
    """
    Devuelve el startDate local de la orden si cae dentro del año pedido.
    Usa el fallback workshopDate de availability si startDate falta.
    """
    start = _parse_utc_to_local(order.get("startDate"))
    if start is None:
        start = _parse_utc_to_local(order.get("workshopDate"))
    if start is None or start.year != year:
        return None
    return start


def compute_mtbf(
    orders: list[dict[str, Any]],
    plate_customer_map: dict[str, dict[str, Any]],
    *,
    year: int,
    now: datetime,
) -> dict[str, Any]:
    """
    Logica pura del MTBF anual.

    Entradas:
      - orders: lista de work-orders crudos de CloudFleet.
      - plate_customer_map: dict placa_normalizada -> {customer_id, customer_name}.
      - year: año de referencia (local Colombia).
      - now: momento de referencia (naive, hora local Colombia).

    Devuelve {"year", "generated_at", "mtbf_hours", "status",
              "intervals_count", "vehicles_considered", "fleets": [...]}.
    """
    orders = dedupe_work_orders(orders)

    # Filtrar fallas del año con vehicleCode valido.
    failure_dates_by_plate: dict[str, list[datetime]] = {}
    fleet_failure_counts: dict[Any, dict[str, Any]] = {}

    for order in orders:
        if not isinstance(order, dict):
            continue

        if not _is_failure(order):
            continue

        plate_raw = order.get("vehicleCode")
        if not plate_raw:
            continue
        plate = _normalize_plate(plate_raw)
        if not plate:
            continue

        start_local = _start_date_in_year(order, year)
        if start_local is None:
            continue

        failure_dates_by_plate.setdefault(plate, []).append(start_local)

        # Agrupacion por flota para conteo total de fallas.
        customer = plate_customer_map.get(plate, {}) if plate_customer_map else {}
        customer_id = customer.get("customer_id")
        customer_name = str(customer.get("customer_name") or "Sin cliente").strip() or "Sin cliente"

        bucket = fleet_failure_counts.setdefault(
            customer_id,
            {
                "customer_id": customer_id,
                "customer_name": customer_name,
                "failures": 0,
                "plates": set(),
                "intervals": [],
            },
        )
        bucket["failures"] += 1
        bucket["plates"].add(plate)

    # Calcular intervalos por placa (solo placas con >= 2 fallas).
    all_intervals: list[float] = []
    vehicles_considered = 0

    for plate, dates in failure_dates_by_plate.items():
        if len(dates) < 2:
            continue
        dates_sorted = sorted(set(dates))
        if len(dates_sorted) < 2:
            continue

        plate_intervals = [
            (dates_sorted[i + 1] - dates_sorted[i]).total_seconds() / 3600.0
            for i in range(len(dates_sorted) - 1)
        ]

        all_intervals.extend(plate_intervals)
        vehicles_considered += 1

        customer = plate_customer_map.get(plate, {}) if plate_customer_map else {}
        customer_id = customer.get("customer_id")
        bucket = fleet_failure_counts.get(customer_id)
        if bucket is not None:
            bucket["intervals"].extend(plate_intervals)

    # MTBF global.
    mtbf_hours = round(sum(all_intervals) / len(all_intervals), 3) if all_intervals else None

    # Construir lista de flotas.
    fleets: list[dict[str, Any]] = []
    for bucket in fleet_failure_counts.values():
        intervals = bucket["intervals"]
        fleet_mtbf = round(sum(intervals) / len(intervals), 3) if intervals else None
        fleets.append(
            {
                "customer_id": bucket["customer_id"],
                "customer_name": bucket["customer_name"],
                "mtbf_hours": fleet_mtbf,
                "status": _classify_mtbf(fleet_mtbf),
                "vehicles_with_failures": sum(
                    1 for p in bucket["plates"] if len(failure_dates_by_plate.get(p, [])) >= 2
                ),
                "failures": bucket["failures"],
            }
        )

    # Peores primero; flotas sin intervalos (mtbf None) al final.
    fleets.sort(
        key=lambda f: (
            f["mtbf_hours"] is None,
            f["mtbf_hours"] if f["mtbf_hours"] is not None else 0.0,
        )
    )

    return {
        "year": year,
        "generated_at": now.isoformat(),
        "mtbf_hours": mtbf_hours,
        "status": _classify_mtbf(mtbf_hours),
        "intervals_count": len(all_intervals),
        "vehicles_considered": vehicles_considered,
        "fleets": fleets,
    }


def _cache_key() -> str:
    return "mtbf_summary"


def _get_cached() -> dict[str, Any] | None:
    now = time.time()
    with _CACHE_LOCK:
        cached = _MTBF_CACHE.get(_cache_key())
        if cached is not None:
            expires_at, payload = cached
            if expires_at > now:
                return payload
            _MTBF_CACHE.pop(_cache_key(), None)
    return None


def _set_cached(payload: dict[str, Any]) -> None:
    with _CACHE_LOCK:
        _MTBF_CACHE[_cache_key()] = (time.time() + _CACHE_TTL_SECONDS, payload)


def _load_eligible_plate_customer_map() -> dict[str, dict[str, Any]]:
    """Mapa local limitado al alcance comercial del modulo Disponibilidad."""
    with db_conn(row_factory=dict_row) as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                """
                SELECT a.plate, a.customer_id, c.name AS customer_name
                FROM vehicle_motor_assignments a
                JOIN customers c ON c.id = a.customer_id
                WHERE c.name <> %s
                  AND COALESCE(a.category, c.category, 'Ninguna') = ANY(%s);
                """,
                (_SYSTEM_CUSTOMER_NAME, list(AVAILABILITY_CATEGORIES)),
            )
            rows = cur.fetchall()

    return {
        normalized: {
            "customer_id": row.get("customer_id"),
            "customer_name": row.get("customer_name"),
        }
        for row in rows
        if (normalized := _normalize_plate(row.get("plate")))
    }


def _fetch_year_work_orders(config: Any, *, year: int, now: datetime) -> list[dict[str, Any]]:
    """
    Descarga las work-orders del año troceando por updatedAt en ventanas de
    170 dias. La API de CloudFleet limita rangos updatedAt a < 180 dias.

    Nota: updatedAt != startDate. Una orden de enero sin updates recientes
    igual aparece porque su updatedAt cae en la primera ventana o posteriores;
    una orden creada y jamas tocada tiene updatedAt = createdAt, que cae en
    alguna ventana del año. Fallas de años anteriores no interesan.
    """
    # 1 de enero a las 05:00 UTC equivale a 00:00 hora Colombia.
    start = datetime(year, 1, 1, 5, 0, 0)
    end = now + timedelta(days=1)

    all_orders: list[dict[str, Any]] = []
    window_start = start

    while window_start < end:
        window_end = min(window_start + timedelta(days=_WINDOW_DAYS), end)
        _logger.info(
            "MTBF año %d: descargando work-orders de CloudFleet "
            "(ventana updatedAt %s -> %s).",
            year,
            window_start.isoformat(),
            window_end.isoformat(),
        )
        orders = list_work_orders(
            config,
            updated_at_from=window_start,
            updated_at_to=window_end,
        )
        all_orders.extend(orders)
        window_start = window_end

    return all_orders


def get_mtbf_summary(*, force_refresh: bool = False) -> dict[str, Any]:
    """
    Orquestador on-demand del MTBF anual.

    - Cache en memoria de 1 hora (thread-safe).
    - Descarga las ordenes del año desde CloudFleet troceando el rango.
    - Enriquece con el mapa placa -> cliente local.

    Levanta RuntimeError claro si CloudFleet no esta configurado, o
    CloudFleetAuthError / CloudFleetUnavailableError si la API falla.
    """
    if not force_refresh:
        cached = _get_cached()
        if cached is not None:
            return cached

    config = load_cloudfleet_config()
    if config is None:
        raise RuntimeError(
            "CloudFleet no esta configurado: falta CLOUDFLEET_API_KEY en el .env."
        )

    now = datetime.now()
    year = now.year

    orders = _fetch_year_work_orders(config, year=year, now=now)
    deduped_orders = dedupe_work_orders(orders)
    dropped = len(orders) - len(deduped_orders)
    if dropped:
        _logger.info(
            "MTBF año %d: se descartaron %d ordenes duplicadas (de %d a %d).",
            year,
            dropped,
            len(orders),
            len(deduped_orders),
        )

    plate_customer_map = _load_eligible_plate_customer_map()
    deduped_orders = [
        order
        for order in deduped_orders
        if _normalize_plate(order.get("vehicleCode")) in plate_customer_map
    ]

    result = compute_mtbf(deduped_orders, plate_customer_map, year=year, now=now)
    _set_cached(result)
    return result


__all__ = [
    "compute_mtbf",
    "get_mtbf_summary",
]
