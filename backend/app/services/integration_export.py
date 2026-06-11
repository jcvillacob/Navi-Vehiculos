"""Exportacion de datos hacia Portal Clientes (ver docs/contrato-integracion-portal-clientes.md).

Navi Vehiculos es la fuente de verdad de clientes, databases Geotab, credenciales,
reglas y vehiculos. Este modulo arma el snapshot (completo o incremental por
updated_at) que la otra app replica localmente.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import psycopg
from psycopg.rows import dict_row

from app.services.motor_catalog import _database_dsn, _ensure_motor_tables
from app.services.provider_registry import public_provider_config

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


def _iso(value: Any) -> str | None:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc).isoformat()
    return None


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

        cur.execute(
            """
            SELECT id, database_id, name, rule_id, category, created_at
            FROM geotab_rules
            ORDER BY database_id ASC, name ASC;
            """
        )
        rule_rows = cur.fetchall()

    credentials_by_db: dict[int, list[dict[str, Any]]] = {}
    for row in credential_rows:
        credentials_by_db.setdefault(int(row["customer_database_id"]), []).append(
            {
                "id": int(row["id"]),
                "username": row["username"],
                "password": row["password"] if include_credentials else _PASSWORD_MASK,
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
                "rule_id": row["rule_id"],
                "name": row["name"],
                "category": row["category"],
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
    where_clause = ""
    params: list[Any] = []
    if since is not None:
        where_clause = "WHERE a.updated_at > %s"
        params.append(since)

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
                a.cpl,
                a.marca,
                a.linea,
                a.ano_modelo,
                a.tipo_combustible,
                a.nombre_vehiculo,
                a.updated_at
            FROM vehicle_motor_assignments a
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
            "geotab_device_id": row.get("geotab_device_id"),
            "geotab_device_synced_at": _iso(row.get("geotab_device_synced_at")),
            "customer_id": row.get("customer_id"),
            "customer_database_id": row.get("customer_database_id"),
            "geotab_customer_database_id": row.get("geotab_customer_database_id"),
            "geotab_customer_status": row.get("geotab_customer_status"),
            "engine_number": row.get("engine_number"),
            "technical_number": row.get("technical_number"),
            "cpl": row.get("cpl"),
            "marca": row.get("marca"),
            "linea": row.get("linea"),
            "ano_modelo": row.get("ano_modelo"),
            "tipo_combustible": row.get("tipo_combustible"),
            "nombre_vehiculo": row.get("nombre_vehiculo"),
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
