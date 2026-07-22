"""
Monitor de ordenes de taller activas (CloudFleet).

Expone la logica pura `build_active_orders` (testable sin red) y el orquestador
`get_active_orders` que consulta CloudFleet on-demand con cache en memoria de
10 minutos.

Documentacion de negocio: docs/CALCULO_DISPONIBILIDAD.md
  - Seccion 6 (estados activos).
  - Seccion 13 (monitor de ordenes activas).
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

# Estados que el monitor considera "activos" (doc seccion 6).
_ACTIVE_STATUSES = frozenset({"opened", "ontechnicalcompletion"})

# Cache en memoria del monitor (patron _DEVICE_CACHE de geotab_client).
_CACHE_TTL_SECONDS = 600
_ACTIVE_ORDERS_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}
_CACHE_LOCK = threading.Lock()

# Ventana de consulta de work-orders en dias hacia atras. Captura ordenes
# abiertas viejas que no fueron tocadas recientemente. La API de CloudFleet
# rechaza rangos de updatedAt mayores a 180 dias (HTTP 409), y la ventana
# total incluye +1 dia hacia adelante: 178 + 1 = 179 dias < 180.
_ORDER_LOOKBACK_DAYS = 178


def _indicator_label(indicator: str) -> str:
    return {
        "on_time": "En tiempo",
        "about_to_expire": "Por vencer",
        "overdue": "Excedido",
        "pending_closure": "Pendiente cierre",
    }.get(indicator, indicator)


def build_active_orders(
    orders: list[dict[str, Any]],
    plate_customer_map: dict[str, dict[str, Any]],
    *,
    now: datetime,
) -> dict[str, Any]:
    """
    Logica pura del monitor de ordenes activas.

    Entradas:
      - orders: lista de work-orders crudos de CloudFleet.
      - plate_customer_map: dict placa_normalizada -> {customer_id, customer_name}.
      - now: momento de referencia (naive, hora local Colombia).

    Devuelve {"generated_at": ISO, "summary": {...}, "orders": [...]}.
    """
    active_orders: list[dict[str, Any]] = []

    for order in orders:
        if not isinstance(order, dict):
            continue

        raw_status = str(order.get("status") or "").strip().lower()
        if raw_status not in _ACTIVE_STATUSES:
            continue

        number = str(order.get("number") or "").strip()
        if not number:
            continue

        plate = _normalize_plate(order.get("vehicleCode"))
        customer = plate_customer_map.get(plate, {}) if plate else {}
        customer_name = customer.get("customer_name") or "Sin cliente"
        customer_id = customer.get("customer_id")

        start_local = _parse_utc_to_local(order.get("startDate"))
        if start_local is None:
            start_local = _parse_utc_to_local(order.get("workshopDate"))

        if start_local is not None:
            days_elapsed = (now - start_local).days
        else:
            days_elapsed = None

        estimated_finish_local = _parse_utc_to_local(order.get("estimatedFinishDate"))
        technical_completion_local = _parse_utc_to_local(
            order.get("technicalCompletionDate")
        )
        final_completion_local = _parse_utc_to_local(order.get("finalCompletionDate"))

        # Aging de cierre administrativo pendiente.
        if technical_completion_local is not None and final_completion_local is None:
            pending_closure_days = (now - technical_completion_local).days
        else:
            pending_closure_days = None

        # Indicador temporal (doc seccion 13 / 17).
        if technical_completion_local is not None and final_completion_local is None:
            status_indicator = "pending_closure"
        elif estimated_finish_local is not None:
            if estimated_finish_local < now:
                status_indicator = "overdue"
            elif (estimated_finish_local - now) < timedelta(hours=24):
                status_indicator = "about_to_expire"
            else:
                status_indicator = "on_time"
        else:
            status_indicator = "on_time"

        maintenance_labels = order.get("maintenanceLabels") or []
        if not isinstance(maintenance_labels, list):
            maintenance_labels = []
        maintenance_labels = [str(label) for label in maintenance_labels]

        reason = order.get("reason")
        if reason is not None:
            reason = str(reason).strip() or None

        active_orders.append(
            {
                "number": number,
                "plate": plate,
                "customer_id": customer_id,
                "customer_name": customer_name,
                "fleet": customer_name,
                "type": order.get("type"),
                "status": order.get("status"),
                "reason": reason,
                "days_elapsed": days_elapsed,
                "pending_closure_days": pending_closure_days,
                "status_indicator": status_indicator,
                "time_status_text": _indicator_label(status_indicator),
                "maintenance_labels": maintenance_labels,
                "has_labels": len(maintenance_labels) > 0,
                "updatedAt": order.get("updatedAt"),
            }
        )

    # Deduplicacion por number (conserva updatedAt mas reciente).
    active_orders = dedupe_work_orders(active_orders)

    # Renombrar la clave de negocio a order_number para la respuesta.
    for item in active_orders:
        item["order_number"] = item.pop("number", None)

    # Orden: con etiquetas primero, luego dias transcurridos DESC (None al final).
    def _sort_key(item: dict[str, Any]) -> tuple[int, int, int]:
        has_labels = 1 if item.get("has_labels") else 0
        days = item.get("days_elapsed")
        if days is None:
            days_sort = -1
        else:
            days_sort = days
        return (has_labels, days_sort, 0)

    active_orders.sort(key=_sort_key, reverse=True)

    summary = {
        "total_active": len(active_orders),
        "on_time": 0,
        "about_to_expire": 0,
        "overdue": 0,
        "pending_closure": 0,
        "pending_closure_7d": 0,
        "pending_closure_30d": 0,
        "con_etiquetas": 0,
    }
    for order in active_orders:
        indicator = order["status_indicator"]
        if indicator in summary:
            summary[indicator] += 1
        pcd = order.get("pending_closure_days")
        if isinstance(pcd, int):
            if pcd > 7:
                summary["pending_closure_7d"] += 1
            if pcd > 30:
                summary["pending_closure_30d"] += 1
        if order.get("has_labels"):
            summary["con_etiquetas"] += 1

    return {
        "generated_at": now.isoformat(),
        "summary": summary,
        "orders": active_orders,
    }


def _load_plate_customer_map() -> dict[str, dict[str, Any]]:
    """
    Carga un mapa placa_normalizada -> {customer_id, customer_name} desde la DB.

    Usa el mismo universo de Disponibilidad: solo vehiculos cuya categoria
    efectiva sea Flota Administrada o Experiencia Superior.
    """
    result: dict[str, dict[str, Any]] = {}
    with db_conn(row_factory=dict_row) as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                """
                SELECT a.plate,
                       a.customer_id,
                       c.name AS customer_name
                FROM vehicle_motor_assignments a
                JOIN customers c ON c.id = a.customer_id
                WHERE c.name <> %s
                  AND COALESCE(a.category, c.category, 'Ninguna') = ANY(%s)
                ORDER BY a.plate;
                """,
                (_SYSTEM_CUSTOMER_NAME, list(AVAILABILITY_CATEGORIES)),
            )
            rows = cur.fetchall()

    for row in rows:
        plate = str(row.get("plate") or "").strip().upper()
        if not plate:
            continue
        norm = _normalize_plate(plate)
        if not norm:
            continue
        result[norm] = {
            "customer_id": row.get("customer_id"),
            "customer_name": str(row.get("customer_name") or "Sin cliente").strip()
            or "Sin cliente",
        }

    return result


def _filter_eligible_orders(
    orders: list[dict[str, Any]],
    plate_customer_map: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    """Conserva unicamente ordenes de placas elegibles para Disponibilidad."""
    return [
        order
        for order in orders
        if _normalize_plate(order.get("vehicleCode")) in plate_customer_map
    ]


def _cache_key() -> str:
    return "active_taller_orders"


def _get_cached() -> dict[str, Any] | None:
    now = time.time()
    with _CACHE_LOCK:
        cached = _ACTIVE_ORDERS_CACHE.get(_cache_key())
        if cached is not None:
            expires_at, payload = cached
            if expires_at > now:
                return payload
            _ACTIVE_ORDERS_CACHE.pop(_cache_key(), None)
    return None


def _set_cached(payload: dict[str, Any]) -> None:
    with _CACHE_LOCK:
        _ACTIVE_ORDERS_CACHE[_cache_key()] = (
            time.time() + _CACHE_TTL_SECONDS,
            payload,
        )


def peek_cached_orders() -> dict[str, Any] | None:
    """
    Devuelve el cache vigente de ordenes activas SIN forzar una llamada a
    CloudFleet. Util para el dashboard, que no debe disparar una consulta de
    ~55s cuando el cache esta frio.
    """
    return _get_cached()


def refresh_cache_from_orders(
    orders: list[dict[str, Any]],
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """
    Renueva el monitor usando un lote ya descargado de CloudFleet.

    Disponibilidad usa esta entrada para mantener ambos modulos sincronizados
    sin realizar una segunda consulta de work-orders.
    """
    reference_now = now or datetime.now()
    plate_customer_map = _load_plate_customer_map()
    deduped_orders = dedupe_work_orders(orders)
    eligible_orders = _filter_eligible_orders(deduped_orders, plate_customer_map)
    dropped = len(deduped_orders) - len(eligible_orders)
    if dropped:
        _logger.info(
            "Monitor ordenes activas: se excluyeron %d ordenes de vehiculos "
            "fuera de Flota Administrada/Experiencia Superior.",
            dropped,
        )
    result = build_active_orders(eligible_orders, plate_customer_map, now=reference_now)
    _set_cached(result)
    return result


def get_active_orders(*, force_refresh: bool = False) -> dict[str, Any]:
    """
    Orquestador on-demand del monitor de ordenes activas.

    - Cache en memoria de 10 minutos (thread-safe).
    - Consulta CloudFleet con una ventana de 180 dias hacia atras.
    - Enriquece cada orden con flota/cliente local y estado temporal.

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
    updated_at_from = now - timedelta(days=_ORDER_LOOKBACK_DAYS)
    updated_at_to = now + timedelta(days=1)

    _logger.info(
        "Monitor ordenes activas: descargando work-orders de CloudFleet "
        "(ventana %s -> %s).",
        updated_at_from.isoformat(),
        updated_at_to.isoformat(),
    )

    try:
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
        raise CloudFleetUnavailableError(
            f"Error inesperado consultando CloudFleet: {exc}"
        ) from exc

    deduped_orders = dedupe_work_orders(orders)
    dropped = len(orders) - len(deduped_orders)
    if dropped:
        _logger.info(
            "Monitor ordenes activas: se descartaron %d ordenes duplicadas "
            "(de %d a %d).",
            dropped,
            len(orders),
            len(deduped_orders),
        )

    return refresh_cache_from_orders(deduped_orders, now=now)


__all__ = [
    "build_active_orders",
    "get_active_orders",
    "peek_cached_orders",
    "refresh_cache_from_orders",
]
