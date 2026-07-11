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

    return {
        "plate": normalized_plate,
        "master": master,
        "rendimientos": rendimientos,
        "disponibilidad": disponibilidad,
        "bindings": bindings,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
