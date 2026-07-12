from __future__ import annotations

import logging
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any

from psycopg.rows import dict_row

from app.core.db import db_conn
from app.services.availability_store import _ensure_availability_table
from app.services.motor_catalog import _ensure_motor_tables
from app.services.rendimientos import _ensure_performance_tables
from app.services.taller_ordenes import peek_cached_orders
from app.clients.geotab_client import (
    get_authenticated_client,
    get_cached_devices,
    get_device_live_status,
    _find_device_in_collection,
)

_logger = logging.getLogger(__name__)


def _serialize_value(value: Any) -> Any:
    """Convierte valores no serializables nativamente (Decimal, datetime, date)."""
    if value is None:
        return None
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return value


def _serialize_row(row: dict[str, Any]) -> dict[str, Any]:
    return {key: _serialize_value(value) for key, value in row.items()}


def _master_query(cur, plate: str) -> dict[str, Any] | None:
    cur.execute(
        """
        SELECT
            a.plate,
            a.vin,
            a.technical_number,
            a.engine_number,
            m.engine_name,
            a.marca,
            a.linea,
            a.ano_modelo,
            a.tipo_combustible,
            a.nombre_vehiculo,
            a.vocacional,
            COALESCE(a.category, c.category, 'Ninguna') AS category,
            c.name AS client_name,
            cd.database_name,
            cd.connection_type AS database_connection_type,
            a.geotab_status,
            a.last_seen_at
        FROM vehicle_motor_assignments a
        LEFT JOIN motor_catalog m
            ON m.technical_number = a.technical_number
        LEFT JOIN customers c
            ON c.id = a.customer_id
        LEFT JOIN customer_databases cd
            ON cd.id = a.customer_database_id
        WHERE a.plate = %s
        LIMIT 1;
        """,
        (plate,),
    )
    row = cur.fetchone()
    return _serialize_row(row) if row else None


def _rendimientos_query(cur, plate: str) -> list[dict[str, Any]]:
    cur.execute(
        """
        SELECT
            period_month,
            kms_ecm,
            kms_gps,
            hours_ecm,
            hours_gps,
            fuel_gallons,
            calculation_status,
            source_provider,
            warnings
        FROM monthly_vehicle_performance
        WHERE plate = %s
        ORDER BY period_month DESC
        LIMIT 12;
        """,
        (plate,),
    )
    return [_serialize_row(row) for row in cur.fetchall()]


def _disponibilidad_query(cur, plate: str) -> list[dict[str, Any]]:
    cur.execute(
        """
        SELECT
            period_month,
            calculation_status,
            project_availability_pct,
            h_no_disp,
            orders_considered,
            mttr_hours,
            orders_closed,
            last_calculated_at
        FROM monthly_vehicle_availability
        WHERE plate = %s
        ORDER BY period_month DESC
        LIMIT 12;
        """,
        (plate,),
    )
    return [_serialize_row(row) for row in cur.fetchall()]


def _bindings_query(cur, plate: str) -> list[dict[str, Any]]:
    cur.execute(
        """
        SELECT
            provider,
            provider_vehicle_id,
            binding_status,
            last_error,
            updated_at
        FROM vehicle_provider_bindings
        WHERE plate = %s
        ORDER BY provider, updated_at DESC;
        """,
        (plate,),
    )
    return [_serialize_row(row) for row in cur.fetchall()]


_TALLER_ORDER_KEYS = (
    "order_number",
    "type",
    "status",
    "status_indicator",
    "time_status_text",
    "days_elapsed",
    "pending_closure_days",
    "maintenance_labels",
)


def _build_taller_section(plate: str) -> dict[str, Any]:
    """
    Lee el cache del monitor de ordenes activas SIN disparar llamadas a
    CloudFleet y devuelve solo las ordenes que correspondan a la placa.
    """
    cached = peek_cached_orders()
    if cached is None:
        return {"available": False, "orders": []}

    orders = cached.get("orders") or []
    filtered = [
        {key: order.get(key) for key in _TALLER_ORDER_KEYS}
        for order in orders
        if isinstance(order, dict) and order.get("plate") == plate
    ]

    return {
        "available": True,
        "generated_at": cached.get("generated_at"),
        "orders": filtered,
    }


def _telemetry_assignment(cur, plate: str) -> dict[str, Any] | None:
    cur.execute(
        """
        SELECT
            a.plate,
            a.customer_database_id,
            cd.database_name,
            cd.username,
            cd.password,
            cd.connection_type
        FROM vehicle_motor_assignments a
        LEFT JOIN customer_databases cd
            ON cd.id = a.customer_database_id
        WHERE a.plate = %s
        LIMIT 1;
        """,
        (plate,),
    )
    row = cur.fetchone()
    return _serialize_row(row) if row else None


def _resolve_geotab_device_id(
    cur,
    plate: str,
    customer_database_id: Any,
    username: str,
    password: str,
    database_name: str,
) -> str | None:
    cur.execute(
        """
        SELECT provider_vehicle_id, is_manual, binding_status
        FROM vehicle_provider_bindings
        WHERE plate = %s
          AND customer_database_id = %s
          AND provider = 'geotab'
          AND provider_vehicle_id IS NOT NULL
          AND provider_vehicle_id <> ''
          AND (binding_status = 'resolved' OR is_manual = TRUE)
        ORDER BY is_manual DESC, updated_at DESC
        LIMIT 1;
        """,
        (plate, customer_database_id),
    )
    row = cur.fetchone()
    if row:
        provider_vehicle_id = row.get("provider_vehicle_id")
        if provider_vehicle_id:
            return str(provider_vehicle_id).strip()

    devices = get_cached_devices(username, password, database_name)
    device = _find_device_in_collection(devices, plate=plate)
    if device:
        device_id = device.get("id")
        if device_id:
            return str(device_id).strip()
    return None


def get_vehicle_telemetry(plate: str) -> dict[str, Any]:
    """
    Devuelve la telemetria en vivo de Geotab para una placa.

    Si la placa no existe en assignments devuelve None (la ruta traduce a 404).
    Cualquier otra situacion devuelve un dict con available=True/False y la razon.
    """
    normalized_plate = plate.strip().upper()
    if not normalized_plate:
        return {"available": False, "reason": "placa_invalida"}

    _ensure_availability_table()

    with db_conn(row_factory=dict_row) as conn:
        _ensure_motor_tables(conn)
        _ensure_performance_tables(conn)

        with conn.cursor() as cur:
            assignment = _telemetry_assignment(cur, normalized_plate)
            if assignment is None:
                return None

    if not assignment.get("customer_database_id"):
        return {"available": False, "reason": "sin_database_geotab"}

    connection_type = (assignment.get("connection_type") or "").lower()
    database_name = assignment.get("database_name")
    username = assignment.get("username")
    password = assignment.get("password")

    if connection_type != "geotab" or not database_name or not username or not password:
        return {"available": False, "reason": "sin_database_geotab"}

    with db_conn(row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            device_id = _resolve_geotab_device_id(
                cur,
                normalized_plate,
                assignment["customer_database_id"],
                username,
                password,
                database_name,
            )

    if not device_id:
        return {"available": False, "reason": "device_no_encontrado"}

    try:
        api = get_authenticated_client(username, password, database_name)
        live = get_device_live_status(api, device_id)
    except Exception as exc:
        _logger.warning("Error consultando telemetria Geotab para %s: %s", normalized_plate, exc)
        return {"available": False, "reason": "geotab_error", "detail": str(exc)}

    return {
        "available": True,
        "plate": normalized_plate,
        "device_id": device_id,
        **live,
    }


def get_vehicle_ficha(plate: str) -> dict[str, Any] | None:
    """Devuelve la ficha 360° de un vehiculo por placa, o None si no existe."""
    normalized_plate = plate.strip().upper()
    if not normalized_plate:
        return None

    _ensure_availability_table()

    with db_conn(row_factory=dict_row) as conn:
        _ensure_motor_tables(conn)
        _ensure_performance_tables(conn)

        with conn.cursor() as cur:
            master = _master_query(cur, normalized_plate)
            if master is None:
                return None

            rendimientos = _rendimientos_query(cur, normalized_plate)
            disponibilidad = _disponibilidad_query(cur, normalized_plate)
            bindings = _bindings_query(cur, normalized_plate)

    taller = _build_taller_section(normalized_plate)

    return {
        "plate": normalized_plate,
        "master": master,
        "rendimientos": rendimientos,
        "disponibilidad": disponibilidad,
        "bindings": bindings,
        "taller": taller,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
