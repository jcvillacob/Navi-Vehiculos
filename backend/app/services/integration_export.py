"""Exportacion de datos hacia Portal Clientes (ver docs/contrato-integracion-portal-clientes.md).

Navi Vehiculos es la fuente de verdad de clientes, databases Geotab, credenciales,
reglas y vehiculos. Este modulo arma el snapshot (completo o incremental por
updated_at) que la otra app replica localmente.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

import psycopg
from psycopg.rows import dict_row

from app.core.crypto import decrypt_secret
from app.core.db import db_conn
from app.services.availability_store import (
    _SOURCE_CLOUDFLEET,
    _SYSTEM_CUSTOMER_NAME,
    _ensure_availability_table,
)
from app.services.motor_catalog import _database_dsn, _ensure_motor_tables
from app.services.provider_registry import public_provider_config
from app.services.rendimientos import _ensure_performance_tables

_PASSWORD_MASK = "********"


def _parse_since(since: str | None) -> datetime | None:
    if not since:
        return None
    normalized = since.strip()
    if not normalized:
        return None
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise ValueError("El parametro 'since' debe ser una fecha ISO-8601.") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _normalize_motor_type(value: Any) -> str | None:
    """engine_name (familia de motor) usado como motor_type en el snapshot.

    Se normaliza (trim) en ambos lados —reglas y vehiculos— para que el cruce
    r.motor_type = v.motor_type de Portal Clientes no se rompa por espacios.
    """
    if value is None:
        return None
    cleaned = str(value).strip()
    return cleaned or None


def _iso(value: Any) -> str | None:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc).isoformat()
    return None


def _float(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, Decimal):
        return float(value)
    return float(value)


_MONTH_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")


def _validate_month(value: str | None, name: str) -> None:
    if not value or not _MONTH_RE.match(str(value)):
        raise ValueError(f"El parametro '{name}' debe tener formato YYYY-MM.")


def _export_customers(
    conn: psycopg.Connection,
    since: datetime | None,
    include_credentials: bool,
) -> list[dict[str, Any]]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT id, name, created_at, updated_at
            FROM customers
            ORDER BY name ASC;
            """
        )
        customer_rows = cur.fetchall()

        cur.execute(
            """
            SELECT
                id,
                customer_id,
                database_name,
                connection_type,
                access_url,
                provider_config,
                created_at,
                updated_at
            FROM customer_databases
            ORDER BY customer_id ASC, database_name ASC;
            """
        )
        database_rows = cur.fetchall()

        cur.execute(
            """
            SELECT
                id,
                customer_database_id,
                username,
                password,
                label,
                is_active,
                created_at,
                updated_at
            FROM customer_database_credentials
            ORDER BY customer_database_id ASC, username ASC;
            """
        )
        credential_rows = cur.fetchall()

        # Una regla fisica puede tener varias aplicaciones: operacion por motor,
        # habito seguro global o habito seguro por motor (ej. exceso_rpm X11).
        # Portal Clientes conserva la regla fisica por rule_id y crea las
        # aplicaciones por category/motor_type/event_type.
        cur.execute(
            """
            SELECT DISTINCT ON (
                gr.database_id,
                gr.rule_id,
                COALESCE(gra.category, gr.category),
                COALESCE(mc.engine_name, ''),
                COALESCE(gra.event_type, '')
            )
                gr.id,
                gr.id AS rule_source_id,
                gra.id AS application_id,
                gr.database_id,
                gr.name,
                gr.rule_id,
                COALESCE(gra.category, gr.category) AS category,
                gra.event_type,
                gr.created_at,
                mc.engine_name AS motor_type
            FROM geotab_rules gr
            LEFT JOIN geotab_rule_applications gra
                ON gra.geotab_rule_id = gr.id
            LEFT JOIN motor_catalog mc
                ON mc.id = gra.motor_id
            ORDER BY
                gr.database_id ASC,
                gr.rule_id ASC,
                COALESCE(gra.category, gr.category) ASC,
                COALESCE(mc.engine_name, '') ASC,
                COALESCE(gra.event_type, '') ASC,
                gr.id ASC;
            """
        )
        rule_rows = cur.fetchall()

    credentials_by_db: dict[int, list[dict[str, Any]]] = {}
    for row in credential_rows:
        credentials_by_db.setdefault(int(row["customer_database_id"]), []).append(
            {
                "id": int(row["id"]),
                "username": row["username"],
                "password": (
                    decrypt_secret(row["password"]) if include_credentials else _PASSWORD_MASK
                ),
                "label": row.get("label"),
                "is_active": bool(row["is_active"]),
                "updated_at": _iso(row["updated_at"]),
            }
        )

    rules_by_db: dict[int, list[dict[str, Any]]] = {}
    for row in rule_rows:
        rules_by_db.setdefault(int(row["database_id"]), []).append(
            {
                "id": int(row["id"]),
                "rule_source_id": int(row["rule_source_id"]),
                "application_id": (
                    int(row["application_id"])
                    if row.get("application_id") is not None
                    else None
                ),
                "rule_id": row["rule_id"],
                "name": row["name"],
                "category": row["category"],
                "motor_type": _normalize_motor_type(row.get("motor_type")),
                "event_type": row.get("event_type"),
                "created_at": _iso(row["created_at"]),
            }
        )

    databases_by_customer: dict[int, list[dict[str, Any]]] = {}
    for row in database_rows:
        db_id = int(row["id"])
        connection_type = str(row.get("connection_type") or "database")
        raw_provider_config = row.get("provider_config")
        if connection_type == "geotab":
            # public_provider_config no expone nada para geotab; Portal Clientes
            # necesita el plate_prefix para matchear devices por placa.
            plate_prefix = None
            if isinstance(raw_provider_config, dict):
                plate_prefix = raw_provider_config.get("plate_prefix")
            exported_provider_config = {"plate_prefix": plate_prefix}
        else:
            exported_provider_config = public_provider_config(
                connection_type, raw_provider_config
            )
        databases_by_customer.setdefault(int(row["customer_id"]), []).append(
            {
                "id": db_id,
                "database_name": row["database_name"],
                # Clave de la db FISICA de Geotab: filas de distintos clientes
                # con el mismo database_key comparten reglas y credenciales.
                "database_key": str(row["database_name"]).strip().lower(),
                "connection_type": connection_type,
                "access_url": row.get("access_url"),
                "provider_config": exported_provider_config,
                "updated_at": _iso(row["updated_at"]),
                "credentials": credentials_by_db.get(db_id, []),
                "rules": rules_by_db.get(db_id, []),
            }
        )

    customers: list[dict[str, Any]] = []
    for row in customer_rows:
        customer_id = int(row["id"])
        databases = databases_by_customer.get(customer_id, [])
        customer_updated_at: datetime = row["updated_at"]
        if since is not None:
            # Incremental: incluir el cliente si el o cualquiera de sus piezas cambio.
            db_changed = any(
                db_row["updated_at"] > since
                for db_row in database_rows
                if int(db_row["customer_id"]) == customer_id
            )
            cred_changed = any(
                cred_row["updated_at"] > since
                for cred_row in credential_rows
                if any(
                    int(cred_row["customer_database_id"]) == db["id"] for db in databases
                )
            )
            if customer_updated_at <= since and not db_changed and not cred_changed:
                continue
        customers.append(
            {
                "id": customer_id,
                "name": row["name"],
                "updated_at": _iso(customer_updated_at),
                "databases": databases,
            }
        )
    return customers


def _export_vehicles(
    conn: psycopg.Connection,
    since: datetime | None,
    *,
    limit: int | None = None,
    offset: int = 0,
) -> list[dict[str, Any]]:
    # El snapshot resuelve el device geotab desde vehicle_provider_bindings;
    # garantiza esa tabla (idempotente/cacheada) por si el bootstrap de
    # rendimientos aun no corrio en este entorno.
    _ensure_performance_tables(conn)

    where_clause = ""
    params: list[Any] = []
    if since is not None:
        where_clause = "WHERE a.updated_at > %s OR geotab_binding.updated_at > %s"
        params.extend([since, since])

    pagination_clause = ""
    if limit is not None:
        pagination_clause = "LIMIT %s OFFSET %s"
        params.extend([limit, offset])

    with conn.cursor() as cur:
        cur.execute(
            f"""
            SELECT
                a.plate,
                a.vin,
                a.geotab_device_id,
                a.geotab_device_synced_at,
                a.customer_id,
                a.customer_database_id,
                a.geotab_customer_database_id,
                a.geotab_customer_status,
                a.engine_number,
                a.technical_number,
                mc.engine_name AS motor_type,
                a.cpl,
                a.marketing_model_name,
                a.service_model_name,
                a.marca,
                a.linea,
                a.ano_modelo,
                a.tipo_combustible,
                a.nombre_vehiculo,
                a.vocacional,
                GREATEST(
                    a.updated_at,
                    COALESCE(geotab_binding.updated_at, a.updated_at)
                ) AS updated_at,
                -- Categoria EFECTIVA del vehiculo (override propio > cliente >
                -- 'Ninguna'), mismo criterio que la lista de vehiculos. Portal
                -- Clientes la usa para is_active: 'Ninguna' = inactivo, las
                -- gestionadas (Flota Administrada / Experiencia Superior) = activo.
                COALESCE(a.category, c.category, 'Ninguna') AS category,
                -- "ID externo" del binding geotab: para una database geotab este
                -- provider_vehicle_id ES el id del device (lo que ve la UI y usan
                -- los calculos). a.geotab_device_id solo lo llena la validacion y
                -- suele venir NULL, por eso Portal Clientes prefiere el binding.
                -- Mismo criterio que la lista de vehiculos (manual primero, luego
                -- el mas reciente).
                geotab_binding.provider_vehicle_id AS geotab_binding_device_id
            FROM vehicle_motor_assignments a
            LEFT JOIN motor_catalog mc
                ON mc.technical_number = a.technical_number
            LEFT JOIN customers c
                ON c.id = a.customer_id
            LEFT JOIN LATERAL (
                SELECT vpb.provider_vehicle_id, vpb.updated_at
                FROM vehicle_provider_bindings vpb
                WHERE vpb.plate = a.plate
                  AND vpb.customer_database_id = a.customer_database_id
                  AND vpb.provider = 'geotab'
                  AND vpb.provider_vehicle_id IS NOT NULL
                ORDER BY vpb.is_manual DESC, vpb.updated_at DESC
                LIMIT 1
            ) geotab_binding ON TRUE
            {where_clause}
            ORDER BY a.plate ASC
            {pagination_clause};
            """,
            params,
        )
        rows = cur.fetchall()

    return [
        {
            "plate": row["plate"],
            "vin": row.get("vin"),
            # Binding geotab primero (ID externo), fallback a la columna validada.
            "geotab_device_id": row.get("geotab_binding_device_id")
            or row.get("geotab_device_id"),
            "geotab_device_synced_at": _iso(row.get("geotab_device_synced_at")),
            "customer_id": row.get("customer_id"),
            "customer_database_id": row.get("customer_database_id"),
            "geotab_customer_database_id": row.get("geotab_customer_database_id"),
            "geotab_customer_status": row.get("geotab_customer_status"),
            "engine_number": row.get("engine_number"),
            "technical_number": row.get("technical_number"),
            "motor_type": _normalize_motor_type(row.get("motor_type")),
            "cpl": row.get("cpl"),
            "marketing_model_name": row.get("marketing_model_name"),
            "service_model_name": row.get("service_model_name"),
            "marca": row.get("marca"),
            "linea": row.get("linea"),
            "ano_modelo": row.get("ano_modelo"),
            "tipo_combustible": row.get("tipo_combustible"),
            "nombre_vehiculo": row.get("nombre_vehiculo"),
            "vocacional": bool(row.get("vocacional")),
            "category": row.get("category") or "Ninguna",
            "updated_at": _iso(row["updated_at"]),
        }
        for row in rows
    ]


def build_snapshot(
    *, since: str | None = None, include_credentials: bool = False
) -> dict[str, Any]:
    since_dt = _parse_since(since)
    with psycopg.connect(_database_dsn(), row_factory=dict_row) as conn:
        _ensure_motor_tables(conn)
        customers = _export_customers(conn, since_dt, include_credentials)
        vehicles = _export_vehicles(conn, since_dt)
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "since": since_dt.isoformat() if since_dt else None,
        "customers": customers,
        "vehicles": vehicles,
    }


def export_vehicles(
    *, since: str | None = None, limit: int = 500, offset: int = 0
) -> dict[str, Any]:
    since_dt = _parse_since(since)
    bounded_limit = max(1, min(int(limit), 2000))
    bounded_offset = max(0, int(offset))
    with psycopg.connect(_database_dsn(), row_factory=dict_row) as conn:
        _ensure_motor_tables(conn)
        vehicles = _export_vehicles(
            conn, since_dt, limit=bounded_limit, offset=bounded_offset
        )
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "since": since_dt.isoformat() if since_dt else None,
        "limit": bounded_limit,
        "offset": bounded_offset,
        "count": len(vehicles),
        "vehicles": vehicles,
    }


def export_customers(
    *, since: str | None = None, include_credentials: bool = False
) -> dict[str, Any]:
    since_dt = _parse_since(since)
    with psycopg.connect(_database_dsn(), row_factory=dict_row) as conn:
        _ensure_motor_tables(conn)
        customers = _export_customers(conn, since_dt, include_credentials)
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "since": since_dt.isoformat() if since_dt else None,
        "customers": customers,
    }


def export_availability(
    *,
    month_from: str,
    month_to: str,
    since: str | None = None,
    limit: int = 500,
    offset: int = 0,
) -> dict[str, Any]:
    """
    Exporta filas de disponibilidad mensual + MTTR para Portal Clientes.

    Filtros:
      - period_month en [month_from, month_to] (formato YYYY-MM).
      - source = 'cloudfleet'.
      - Excluye placas asignadas al customer interno '__navitrans_system__'.
      - since opcional sobre last_calculated_at (incremental).
    """
    _validate_month(month_from, "month_from")
    _validate_month(month_to, "month_to")
    since_dt = _parse_since(since)
    bounded_limit = max(1, min(int(limit), 2000))
    bounded_offset = max(0, int(offset))

    _ensure_availability_table()

    where = [
        "mva.period_month BETWEEN %s AND %s",
        "mva.source = %s",
        "(c.name IS NULL OR c.name <> %s)",
    ]
    params: list[Any] = [month_from, month_to, _SOURCE_CLOUDFLEET, _SYSTEM_CUSTOMER_NAME]
    if since_dt is not None:
        where.append("mva.last_calculated_at > %s")
        params.append(since_dt)
    where_sql = " AND ".join(where)

    with db_conn(row_factory=dict_row) as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                f"""
                SELECT COUNT(*) AS total
                FROM monthly_vehicle_availability mva
                JOIN vehicle_motor_assignments a ON a.plate = mva.plate
                LEFT JOIN customers c ON c.id = a.customer_id
                WHERE {where_sql};
                """,
                params,
            )
            total = int(cur.fetchone()["total"])

            cur.execute(
                f"""
                SELECT
                    mva.plate,
                    mva.period_month,
                    mva.calculation_status,
                    mva.project_availability_pct,
                    mva.h_total,
                    mva.h_no_disp,
                    mva.orders_considered,
                    mva.mttr_hours,
                    mva.orders_closed,
                    mva.last_calculated_at,
                    a.customer_id,
                    c.name AS customer_name
                FROM monthly_vehicle_availability mva
                JOIN vehicle_motor_assignments a ON a.plate = mva.plate
                LEFT JOIN customers c ON c.id = a.customer_id
                WHERE {where_sql}
                ORDER BY mva.period_month ASC, mva.plate ASC
                LIMIT %s OFFSET %s;
                """,
                [*params, bounded_limit, bounded_offset],
            )
            rows = cur.fetchall()

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "month_from": month_from,
        "month_to": month_to,
        "since": since_dt.isoformat() if since_dt else None,
        "total": total,
        "limit": bounded_limit,
        "offset": bounded_offset,
        "rows": [
            {
                "plate": row["plate"],
                "period_month": row["period_month"],
                "calculation_status": row["calculation_status"],
                "project_availability_pct": _float(row["project_availability_pct"]),
                "h_total": _float(row["h_total"]),
                "h_no_disp": _float(row["h_no_disp"]),
                "orders_considered": int(row["orders_considered"]),
                "mttr_hours": _float(row["mttr_hours"]),
                "orders_closed": int(row["orders_closed"]),
                "customer_id": int(row["customer_id"]) if row["customer_id"] is not None else None,
                "customer_name": row["customer_name"],
                "last_calculated_at": _iso(row["last_calculated_at"]),
            }
            for row in rows
        ],
    }
