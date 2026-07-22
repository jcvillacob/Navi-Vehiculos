from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import psycopg
from psycopg.errors import UniqueViolation
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from app.clients.geotab_client import build_rule_inspection
from app.core.config import GeotabConfig
from app.core.crypto import decrypt_secret, encrypt_secret
from app.core.db import db_conn
from app.schemas.vehicle import (
    AssignedDatabaseSummary,
    CUSTOMER_CATEGORIES,
    CustomerCreateRequest,
    CustomerDatabaseCreateRequest,
    CustomerDatabaseCredentialCreateRequest,
    CustomerDatabaseCredentialRecord,
    CustomerDatabaseCredentialUpdateRequest,
    CustomerDatabaseRecord,
    CustomerDatabaseUpdateRequest,
    CustomerRecord,
    CustomerUpdateRequest,
    DashboardSummary,
    GeotabRuleGroupCreateRequest,
    GeotabRuleGroupRecord,
    GeotabRuleGroupRuleRecord,
    GeotabRuleApplicationRecord,
    GeotabRuleCreateRequest,
    GeotabRuleInspection,
    GeotabRuleRecord,
    MotorAttachmentRecord,
    MotorCatalogRecord,
    MotorCatalogUpsertRequest,
    MotorUpdateRequest,
    RecentMotorRecord,
    RecentVehicleRecord,
    RegisteredMotorSummary,
    VehicleDatabaseAssignmentRequest,
    VehicleAssignmentRecord,
    VehicleAssignmentSummary,
    VehicleLookupResponse,
)
from app.services.storage import (
    delete_file,
    download_file,
    upload_file,
)
from app.services.provider_registry import (
    infer_provider_key,
    normalize_provider_config,
    normalize_provider_key,
    public_provider_config,
    uses_access_url,
)

_logger = logging.getLogger(__name__)


def _database_dsn() -> str:
    raw_dsn = os.getenv("DATABASE_URL", "").strip()
    if not raw_dsn:
        raise RuntimeError("Missing required environment variable: DATABASE_URL")
    return raw_dsn.replace("postgresql+psycopg://", "postgresql://", 1)


def _normalize_optional_text(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = value.strip()
    return cleaned or None


def _normalize_cpl(value: str | None) -> str | None:
    return _normalize_optional_text(value)


def _attachment_matches_vehicle_cpl(attachment_cpl: str | None, vehicle_cpl: str | None) -> bool:
    normalized_attachment_cpl = _normalize_cpl(attachment_cpl)
    return normalized_attachment_cpl is None or normalized_attachment_cpl == vehicle_cpl


def _normalize_category(value: str | None, *, default: str = "Ninguna") -> str:
    """Valida una categoria de cliente/vehiculo. Vacio -> default."""
    cleaned = (value or "").strip()
    if not cleaned:
        return default
    if cleaned not in CUSTOMER_CATEGORIES:
        raise ValueError(
            "Categoria invalida. Usa: " + ", ".join(CUSTOMER_CATEGORIES) + "."
        )
    return cleaned


def _normalize_match_mode(value: str | None) -> str:
    normalized = (value or "all").strip().lower()
    if normalized not in ("all", "any"):
        raise ValueError("El modo de coincidencia debe ser 'all' o 'any'.")
    return normalized


def _normalize_connection_type(value: str | None) -> str:
    return normalize_provider_key(value)


RULE_CATEGORIES = ("operacion", "habito_seguro")


def _normalize_rule_category(value: str | None) -> str:
    normalized = (value or "operacion").strip().lower()
    if normalized not in RULE_CATEGORIES:
        raise ValueError("La categoria de la regla debe ser 'operacion' o 'habito_seguro'.")
    return normalized


def _normalize_rule_event_type(value: str | None) -> str | None:
    cleaned = (value or "").strip().lower().replace("-", "_").replace(" ", "_")
    return cleaned or None


def _normalize_motor_payload(payload: MotorCatalogUpsertRequest) -> dict[str, Any]:
    return {
        "technical_number": payload.technical_number.strip(),
        "engine_name": payload.engine_name.strip(),
    }


def _attachment_download_url(attachment_id: int) -> str:
    return f"/api/v1/motors/attachments/{attachment_id}/download"


def _sync_inferred_provider_types(conn: psycopg.Connection) -> None:
    with conn.cursor() as cur:
        cur.execute("SAVEPOINT provider_inference_sync;")

    try:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                """
                SELECT id, database_name, username, password, connection_type, access_url, provider_config
                FROM customer_databases;
                """
            )
            rows = cur.fetchall()

        for row in rows:
            current_provider = normalize_provider_key(row.get("connection_type"))
            inferred_provider = infer_provider_key(
                connection_type=row.get("connection_type"),
                database_name=row.get("database_name"),
                access_url=row.get("access_url"),
                provider_config=row.get("provider_config"),
            )
            if inferred_provider == current_provider or inferred_provider == "database":
                continue

            normalized_provider_config = normalize_provider_config(
                inferred_provider,
                row.get("provider_config"),
                username=row.get("username"),
                password=row.get("password"),
                existing_provider_config=row.get("provider_config"),
            )
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE customer_databases
                    SET connection_type = %s,
                        provider_config = %s,
                        updated_at = NOW()
                    WHERE id = %s;
                    """,
                    (
                        inferred_provider,
                        Jsonb(normalized_provider_config),
                        row["id"],
                    ),
                )

        with conn.cursor() as cur:
            cur.execute("RELEASE SAVEPOINT provider_inference_sync;")
    except Exception:
        with conn.cursor() as cur:
            cur.execute("ROLLBACK TO SAVEPOINT provider_inference_sync;")
            cur.execute("RELEASE SAVEPOINT provider_inference_sync;")
        _logger.exception("No fue posible sincronizar automaticamente los providers legacy.")


def _build_attachment_record(row: dict[str, Any]) -> MotorAttachmentRecord:
    payload = dict(row)
    payload["download_url"] = _attachment_download_url(int(payload["id"]))
    return MotorAttachmentRecord(**payload)


def _build_rule_records(
    rule_rows: list[dict[str, Any]],
    application_rows: list[dict[str, Any]],
) -> list[GeotabRuleRecord]:
    applications_by_rule_id: dict[int, list[GeotabRuleApplicationRecord]] = {}
    for row in application_rows:
        rule_record_id = int(row["geotab_rule_id"])
        applications_by_rule_id.setdefault(rule_record_id, []).append(
            GeotabRuleApplicationRecord(
                id=int(row["id"]),
                category=str(row["category"]),
                motor_id=int(row["motor_id"]) if row.get("motor_id") is not None else None,
                motor_name=row.get("motor_name"),
                technical_number=row.get("technical_number"),
                event_type=row.get("event_type"),
                created_at=row["created_at"],
            )
        )

    records: list[GeotabRuleRecord] = []
    for row in rule_rows:
        applications = applications_by_rule_id.get(int(row["id"]), [])
        payload = dict(row)
        payload["applications"] = applications
        records.append(
            GeotabRuleRecord(
                **payload,
            )
        )
    return records


def _build_rule_group_records(
    group_rows: list[dict[str, Any]],
    group_rule_rows: list[dict[str, Any]],
) -> dict[int, list[GeotabRuleGroupRecord]]:
    rules_by_group_id: dict[int, list[GeotabRuleGroupRuleRecord]] = {}
    for row in group_rule_rows:
        group_id = int(row["group_id"])
        rules_by_group_id.setdefault(group_id, []).append(
            GeotabRuleGroupRuleRecord(
                rule_record_id=int(row["rule_record_id"]),
                name=str(row["name"]),
                rule_id=str(row["rule_id"]),
            )
        )

    groups_by_database_id: dict[int, list[GeotabRuleGroupRecord]] = {}
    for row in group_rows:
        group_id = int(row["id"])
        record = GeotabRuleGroupRecord(
            id=group_id,
            database_id=int(row["database_id"]),
            motor_id=int(row["motor_id"]),
            motor_name=str(row["motor_name"]),
            technical_number=str(row["technical_number"]),
            name=str(row["name"]),
            match_mode=str(row["match_mode"]),
            rules=rules_by_group_id.get(group_id, []),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )
        groups_by_database_id.setdefault(record.database_id, []).append(record)

    for database_id, records in groups_by_database_id.items():
        groups_by_database_id[database_id] = sorted(
            records,
            key=lambda record: (record.motor_name.lower(), record.name.lower(), record.id),
        )

    return groups_by_database_id


def _merge_rule_group_records_by_motor_identity(
    records: list[GeotabRuleGroupRecord],
) -> list[GeotabRuleGroupRecord]:
    merged_by_motor: dict[tuple[str, str], GeotabRuleGroupRecord] = {}
    seen_rules_by_motor: dict[tuple[str, str], set[int]] = {}

    for group in sorted(records, key=lambda g: g.id):
        key = (group.motor_name.strip().lower(), group.technical_number.strip())
        if key not in merged_by_motor:
            merged_by_motor[key] = group.model_copy(update={"rules": []})
            seen_rules_by_motor[key] = set()

        merged_group = merged_by_motor[key]
        seen_rules = seen_rules_by_motor[key]
        merged_rules = list(merged_group.rules)
        for rule in group.rules:
            if rule.rule_record_id in seen_rules:
                continue
            seen_rules.add(rule.rule_record_id)
            merged_rules.append(rule)
        merged_by_motor[key] = merged_group.model_copy(
            update={
                "rules": sorted(merged_rules, key=lambda r: r.name.lower()),
                "updated_at": max(merged_group.updated_at, group.updated_at),
            }
        )

    return sorted(
        merged_by_motor.values(),
        key=lambda group: (group.motor_name.lower(), group.name.lower(), group.id),
    )


def _list_attachments_by_motor_ids(
    conn: psycopg.Connection, motor_ids: list[int]
) -> dict[int, list[MotorAttachmentRecord]]:
    if not motor_ids:
        return {}

    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT
                id,
                motor_id,
                cpl,
                original_filename,
                content_type,
                file_size,
                created_at,
                updated_at
            FROM motor_attachments
            WHERE motor_id = ANY(%s)
            ORDER BY updated_at DESC, id DESC;
            """,
            (motor_ids,),
        )
        rows = cur.fetchall()

    attachments_by_motor_id: dict[int, list[MotorAttachmentRecord]] = {}
    for row in rows:
        attachment = _build_attachment_record(row)
        attachments_by_motor_id.setdefault(attachment.motor_id, []).append(attachment)
    return attachments_by_motor_id


def _list_attachments_by_technical_numbers(
    conn: psycopg.Connection, technical_numbers: list[str]
) -> dict[str, list[MotorAttachmentRecord]]:
    if not technical_numbers:
        return {}

    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT
                m.technical_number,
                ma.id,
                ma.motor_id,
                ma.cpl,
                ma.original_filename,
                ma.content_type,
                ma.file_size,
                ma.created_at,
                ma.updated_at
            FROM motor_catalog m
            INNER JOIN motor_attachments ma
                ON ma.motor_id = m.id
            WHERE m.technical_number = ANY(%s)
            ORDER BY ma.updated_at DESC, ma.id DESC;
            """,
            (technical_numbers,),
        )
        rows = cur.fetchall()

    attachments_by_technical_number: dict[str, list[MotorAttachmentRecord]] = {}
    for row in rows:
        technical_number = str(row["technical_number"])
        attachment = _build_attachment_record(
            {
                "id": row["id"],
                "motor_id": row["motor_id"],
                "cpl": row["cpl"],
                "original_filename": row["original_filename"],
                "content_type": row["content_type"],
                "file_size": row["file_size"],
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
            }
        )
        attachments_by_technical_number.setdefault(technical_number, []).append(attachment)
    return attachments_by_technical_number


def _list_available_cpls_by_technical_numbers(
    conn: psycopg.Connection, technical_numbers: list[str]
) -> dict[str, list[str]]:
    if not technical_numbers:
        return {}

    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT technical_number, cpl
            FROM vehicle_motor_assignments
            WHERE technical_number = ANY(%s)
              AND cpl IS NOT NULL

            UNION

            SELECT m.technical_number, ma.cpl
            FROM motor_catalog m
            INNER JOIN motor_attachments ma
                ON ma.motor_id = m.id
            WHERE m.technical_number = ANY(%s)
              AND ma.cpl IS NOT NULL;
            """,
            (technical_numbers, technical_numbers),
        )
        rows = cur.fetchall()

    cpls_by_technical_number: dict[str, set[str]] = {}
    for row in rows:
        technical_number = str(row["technical_number"])
        cpl = _normalize_cpl(row["cpl"])
        if cpl:
            cpls_by_technical_number.setdefault(technical_number, set()).add(cpl)

    return {
        technical_number: sorted(cpls)
        for technical_number, cpls in cpls_by_technical_number.items()
    }


_MOTOR_TABLES_DDL_DONE = False


def _ensure_motor_tables(conn: psycopg.Connection) -> None:
    """
    Garantiza las tablas necesarias + sincroniza tipos de proveedor.

    La DDL pesada (CREATE/ALTER) solo corre la primera vez por proceso y en una
    conexion propia (no la del caller). Asi los locks (ACCESS EXCLUSIVE de los
    ALTER TABLE / CREATE TABLE IF NOT EXISTS) no se quedan colgados dentro de
    la transaccion larga del caller — el calc de rendimientos puede durar 17
    min y, antes, bloqueaba todas las demas consultas sobre estas tablas.
    """
    global _MOTOR_TABLES_DDL_DONE
    if not _MOTOR_TABLES_DDL_DONE:
        own_conn = psycopg.connect(_database_dsn())
        try:
            _run_motor_tables_ddl_inner(own_conn)
            own_conn.commit()
        finally:
            own_conn.close()
        _MOTOR_TABLES_DDL_DONE = True
    _sync_inferred_provider_types(conn)


def _run_motor_tables_ddl_inner(conn: psycopg.Connection) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS motor_catalog (
                id BIGSERIAL PRIMARY KEY,
                technical_number TEXT NOT NULL UNIQUE,
                engine_name TEXT NOT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS customers (
                id BIGSERIAL PRIMARY KEY,
                name TEXT NOT NULL UNIQUE,
                category TEXT NOT NULL DEFAULT 'Ninguna',
                is_active BOOLEAN NOT NULL DEFAULT TRUE,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS customer_databases (
                id BIGSERIAL PRIMARY KEY,
                customer_id BIGINT NOT NULL REFERENCES customers(id) ON DELETE CASCADE,
                database_name TEXT NOT NULL,
                username TEXT NOT NULL,
                password TEXT NOT NULL,
                connection_type TEXT NOT NULL DEFAULT 'database',
                provider_config JSONB NOT NULL DEFAULT '{}'::jsonb,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                UNIQUE (customer_id, database_name, username)
            );
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS vehicle_motor_assignments (
                plate VARCHAR(10) PRIMARY KEY,
                vin TEXT NULL,
                geotab_status TEXT NOT NULL DEFAULT 'unknown',
                geotab_customer_status TEXT NOT NULL DEFAULT 'not_applicable',
                geotab_customer_database_id BIGINT NULL REFERENCES customer_databases(id),
                engine_number TEXT NULL,
                technical_number TEXT NOT NULL,
                cpl TEXT NULL,
                marketing_model_name TEXT NULL,
                service_model_name TEXT NULL,
                customer_id BIGINT NULL REFERENCES customers(id),
                customer_database_id BIGINT NULL REFERENCES customer_databases(id),
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                last_seen_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
            """
        )
        cur.execute(
            """
            ALTER TABLE vehicle_motor_assignments
            ADD COLUMN IF NOT EXISTS customer_id BIGINT NULL REFERENCES customers(id);
            """
        )
        cur.execute(
            """
            ALTER TABLE vehicle_motor_assignments
            ADD COLUMN IF NOT EXISTS customer_database_id BIGINT NULL REFERENCES customer_databases(id);
            """
        )
        cur.execute(
            """
            ALTER TABLE vehicle_motor_assignments
            ADD COLUMN IF NOT EXISTS cpl TEXT NULL;
            """
        )
        cur.execute(
            """
            ALTER TABLE vehicle_motor_assignments
            ADD COLUMN IF NOT EXISTS marketing_model_name TEXT NULL;
            """
        )
        cur.execute(
            """
            ALTER TABLE vehicle_motor_assignments
            ADD COLUMN IF NOT EXISTS service_model_name TEXT NULL;
            """
        )
        cur.execute(
            """
            ALTER TABLE vehicle_motor_assignments
            ADD COLUMN IF NOT EXISTS geotab_status TEXT NOT NULL DEFAULT 'unknown';
            """
        )
        cur.execute(
            """
            ALTER TABLE customer_databases
            ADD COLUMN IF NOT EXISTS connection_type TEXT NOT NULL DEFAULT 'database';
            """
        )
        cur.execute(
            """
            ALTER TABLE customer_databases
            ADD COLUMN IF NOT EXISTS provider_config JSONB NOT NULL DEFAULT '{}'::jsonb;
            """
        )
        cur.execute(
            """
            ALTER TABLE vehicle_motor_assignments
            ADD COLUMN IF NOT EXISTS geotab_customer_status TEXT NOT NULL DEFAULT 'not_applicable';
            """
        )
        cur.execute(
            """
            ALTER TABLE vehicle_motor_assignments
            ADD COLUMN IF NOT EXISTS geotab_customer_database_id BIGINT NULL REFERENCES customer_databases(id);
            """
        )
        cur.execute(
            """
            ALTER TABLE vehicle_motor_assignments
            ADD COLUMN IF NOT EXISTS marca TEXT NULL;
            """
        )
        cur.execute(
            """
            ALTER TABLE vehicle_motor_assignments
            ADD COLUMN IF NOT EXISTS linea TEXT NULL;
            """
        )
        cur.execute(
            """
            ALTER TABLE vehicle_motor_assignments
            ADD COLUMN IF NOT EXISTS ano_modelo TEXT NULL;
            """
        )
        cur.execute(
            """
            ALTER TABLE vehicle_motor_assignments
            ADD COLUMN IF NOT EXISTS tipo_combustible TEXT NULL;
            """
        )
        cur.execute(
            """
            ALTER TABLE vehicle_motor_assignments
            ADD COLUMN IF NOT EXISTS nombre_vehiculo TEXT NULL;
            """
        )
        cur.execute(
            """
            ALTER TABLE vehicle_motor_assignments
            ADD COLUMN IF NOT EXISTS geotab_device_id TEXT NULL;
            """
        )
        cur.execute(
            """
            ALTER TABLE vehicle_motor_assignments
            ADD COLUMN IF NOT EXISTS geotab_device_synced_at TIMESTAMPTZ NULL;
            """
        )
        cur.execute(
            """
            ALTER TABLE customers
            ADD COLUMN IF NOT EXISTS category TEXT NOT NULL DEFAULT 'Ninguna';
            """
        )
        cur.execute(
            """
            ALTER TABLE customers
            ADD COLUMN IF NOT EXISTS is_active BOOLEAN NOT NULL DEFAULT TRUE;
            """
        )
        cur.execute(
            """
            ALTER TABLE vehicle_motor_assignments
            ADD COLUMN IF NOT EXISTS category TEXT NULL;
            """
        )
        cur.execute(
            """
            ALTER TABLE vehicle_motor_assignments
            ADD COLUMN IF NOT EXISTS vocacional BOOLEAN NOT NULL DEFAULT FALSE;
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS motor_attachments (
                id BIGSERIAL PRIMARY KEY,
                motor_id BIGINT NOT NULL REFERENCES motor_catalog(id) ON DELETE CASCADE,
                cpl TEXT NULL,
                original_filename TEXT NOT NULL,
                stored_filename TEXT NOT NULL UNIQUE,
                storage_path TEXT NOT NULL UNIQUE,
                content_type TEXT NOT NULL,
                file_size BIGINT NOT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
            """
        )
        cur.execute(
            """
            ALTER TABLE motor_attachments
            ADD COLUMN IF NOT EXISTS cpl TEXT NULL;
            """
        )
        cur.execute(
            """
            ALTER TABLE motor_attachments
            ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW();
            """
        )
        cur.execute(
            """
            ALTER TABLE vehicle_motor_assignments
            ADD COLUMN IF NOT EXISTS access_url TEXT NULL;
            """
        )
        cur.execute(
            """
            ALTER TABLE customer_databases
            ADD COLUMN IF NOT EXISTS access_url TEXT NULL;
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS geotab_rules (
                id BIGSERIAL PRIMARY KEY,
                database_id BIGINT NOT NULL REFERENCES customer_databases(id) ON DELETE CASCADE,
                name TEXT NOT NULL,
                rule_id TEXT NOT NULL,
                category TEXT NOT NULL DEFAULT 'operacion',
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                UNIQUE (database_id, rule_id)
            );
            """
        )
        cur.execute(
            """
            ALTER TABLE geotab_rules
            ADD COLUMN IF NOT EXISTS category TEXT NOT NULL DEFAULT 'operacion';
            """
        )
        cur.execute(
            """
            DO $$
            BEGIN
                IF NOT EXISTS (
                    SELECT 1 FROM pg_constraint
                    WHERE conname = 'ck_geotab_rules_category'
                ) THEN
                    ALTER TABLE geotab_rules
                    ADD CONSTRAINT ck_geotab_rules_category
                    CHECK (category IN ('operacion', 'habito_seguro'));
                END IF;
            END $$;
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS geotab_rule_applications (
                id BIGSERIAL PRIMARY KEY,
                geotab_rule_id BIGINT NOT NULL REFERENCES geotab_rules(id) ON DELETE CASCADE,
                category TEXT NOT NULL DEFAULT 'operacion',
                motor_id BIGINT NULL REFERENCES motor_catalog(id) ON DELETE CASCADE,
                event_type TEXT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
            """
        )
        cur.execute(
            """
            DO $$
            BEGIN
                IF NOT EXISTS (
                    SELECT 1 FROM pg_constraint
                    WHERE conname = 'ck_geotab_rule_applications_category'
                ) THEN
                    ALTER TABLE geotab_rule_applications
                    ADD CONSTRAINT ck_geotab_rule_applications_category
                    CHECK (category IN ('operacion', 'habito_seguro'));
                END IF;
            END $$;
            """
        )
        cur.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS uq_geotab_rule_applications_scope
            ON geotab_rule_applications (
                geotab_rule_id,
                category,
                COALESCE(motor_id, 0),
                COALESCE(event_type, '')
            );
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS customer_database_credentials (
                id BIGSERIAL PRIMARY KEY,
                customer_database_id BIGINT NOT NULL
                    REFERENCES customer_databases(id) ON DELETE CASCADE,
                username TEXT NOT NULL,
                password TEXT NOT NULL,
                label TEXT NULL,
                is_active BOOLEAN NOT NULL DEFAULT TRUE,
                last_used_at TIMESTAMPTZ NULL,
                last_auth_error_at TIMESTAMPTZ NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                UNIQUE (customer_database_id, username)
            );
            """
        )
        # Migra credenciales legacy de customer_databases al pool cifrado.
        cur.execute(
            """
            SELECT id, username, password
            FROM customer_databases
            WHERE username IS NOT NULL AND username <> ''
              AND password IS NOT NULL AND password <> '';
            """
        )
        for legacy_row in cur.fetchall():
            cur.execute(
                """
                INSERT INTO customer_database_credentials (customer_database_id, username, password)
                VALUES (%s, %s, %s)
                ON CONFLICT (customer_database_id, username) DO NOTHING;
                """,
                (
                    # Cursor sin row_factory: las filas son tuplas (id, username, password).
                    legacy_row[0],
                    legacy_row[1],
                    encrypt_secret(legacy_row[2]),
                ),
            )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS geotab_rule_groups (
                id BIGSERIAL PRIMARY KEY,
                database_id BIGINT NOT NULL REFERENCES customer_databases(id) ON DELETE CASCADE,
                motor_id BIGINT NOT NULL REFERENCES motor_catalog(id) ON DELETE CASCADE,
                name TEXT NOT NULL,
                match_mode TEXT NOT NULL DEFAULT 'all',
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS geotab_rule_group_rules (
                group_id BIGINT NOT NULL REFERENCES geotab_rule_groups(id) ON DELETE CASCADE,
                geotab_rule_id BIGINT NOT NULL REFERENCES geotab_rules(id) ON DELETE CASCADE,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                PRIMARY KEY (group_id, geotab_rule_id)
            );
            """
        )
        cur.execute(
            """
            DO $$
            BEGIN
                IF NOT EXISTS (
                    SELECT 1 FROM pg_constraint
                    WHERE conname = 'uq_rule_one_group'
                ) THEN
                    ALTER TABLE geotab_rule_group_rules
                    ADD CONSTRAINT uq_rule_one_group UNIQUE (geotab_rule_id);
                END IF;
            END $$;
            """
        )
        cur.execute(
            """
            INSERT INTO geotab_rule_applications (geotab_rule_id, category, motor_id)
            SELECT gr.id, gr.category, grg.motor_id
            FROM geotab_rules gr
            LEFT JOIN geotab_rule_group_rules grgr
                ON grgr.geotab_rule_id = gr.id
            LEFT JOIN geotab_rule_groups grg
                ON grg.id = grgr.group_id
            WHERE NOT EXISTS (
                SELECT 1
                FROM geotab_rule_applications gra
                WHERE gra.geotab_rule_id = gr.id
                  AND gra.category = gr.category
                  AND gra.motor_id IS NOT DISTINCT FROM grg.motor_id
                  AND gra.event_type IS NULL
            );
            """
        )
        cur.execute(
            """
            DELETE FROM geotab_rule_group_rules grgr
            USING geotab_rule_groups grg, motor_catalog group_motor
            WHERE grgr.group_id = grg.id
              AND group_motor.id = grg.motor_id
              AND EXISTS (
                  SELECT 1
                  FROM geotab_rule_applications app
                  INNER JOIN motor_catalog app_motor
                      ON app_motor.id = app.motor_id
                  WHERE app.geotab_rule_id = grgr.geotab_rule_id
                    AND app.category = 'habito_seguro'
                    AND app.event_type = 'exceso_rpm'
                    AND LOWER(app_motor.engine_name) = LOWER(group_motor.engine_name)
                    AND app_motor.technical_number = group_motor.technical_number
              );
            """
        )
        cur.execute(
            """
            DELETE FROM geotab_rule_applications op_app
            USING motor_catalog op_motor
            WHERE op_motor.id = op_app.motor_id
              AND op_app.category = 'operacion'
              AND op_app.event_type IS NULL
              AND EXISTS (
                  SELECT 1
                  FROM geotab_rule_applications rpm_app
                  INNER JOIN motor_catalog rpm_motor
                      ON rpm_motor.id = rpm_app.motor_id
                  WHERE rpm_app.geotab_rule_id = op_app.geotab_rule_id
                    AND rpm_app.category = 'habito_seguro'
                    AND rpm_app.event_type = 'exceso_rpm'
                    AND LOWER(rpm_motor.engine_name) = LOWER(op_motor.engine_name)
                    AND rpm_motor.technical_number = op_motor.technical_number
              );
            """
        )
        cur.execute(
            """
            DELETE FROM geotab_rule_applications op_app
            WHERE op_app.category = 'operacion'
              AND op_app.motor_id IS NULL
              AND op_app.event_type IS NULL
              AND EXISTS (
                  SELECT 1
                  FROM geotab_rule_applications rpm_app
                  WHERE rpm_app.geotab_rule_id = op_app.geotab_rule_id
                    AND rpm_app.category = 'habito_seguro'
                    AND rpm_app.event_type = 'exceso_rpm'
                    AND rpm_app.motor_id IS NOT NULL
              );
            """
        )
        cur.execute(
            """
            DELETE FROM geotab_rule_groups grg
            WHERE NOT EXISTS (
                SELECT 1
                FROM geotab_rule_group_rules grgr
                WHERE grgr.group_id = grg.id
            );
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS vehicle_connection_log (
                id BIGSERIAL PRIMARY KEY,
                plate VARCHAR(10) NOT NULL REFERENCES vehicle_motor_assignments(plate),
                check_date DATE NOT NULL,
                status TEXT NOT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                UNIQUE (plate, check_date)
            );
            """
        )


def list_motors() -> list[MotorCatalogRecord]:
    with psycopg.connect(_database_dsn(), row_factory=dict_row) as conn:
        _ensure_motor_tables(conn)
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    m.id,
                    m.technical_number,
                    m.engine_name,
                    COUNT(a.plate)::INT AS vehicle_count,
                    MAX(a.last_seen_at) AS last_seen_at,
                    m.created_at,
                    m.updated_at
                FROM motor_catalog m
                LEFT JOIN vehicle_motor_assignments a
                    ON a.technical_number = m.technical_number
                GROUP BY
                    m.id,
                    m.technical_number,
                    m.engine_name,
                    m.created_at,
                    m.updated_at
                ORDER BY COUNT(a.plate) DESC, m.engine_name ASC;
                """
            )
            rows = cur.fetchall()
        attachments_by_motor_id = _list_attachments_by_motor_ids(
            conn, [int(row["id"]) for row in rows]
        )
        available_cpls_by_technical_number = _list_available_cpls_by_technical_numbers(
            conn, [str(row["technical_number"]) for row in rows]
        )

    return [
        MotorCatalogRecord(
            **row,
            attachments=attachments_by_motor_id.get(int(row["id"]), []),
            available_cpls=available_cpls_by_technical_number.get(str(row["technical_number"]), []),
        )
        for row in rows
    ]


def create_motor(payload: MotorCatalogUpsertRequest) -> MotorCatalogRecord:
    normalized = _normalize_motor_payload(payload)
    with psycopg.connect(_database_dsn(), row_factory=dict_row) as conn:
        _ensure_motor_tables(conn)
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO motor_catalog (
                        technical_number,
                        engine_name
                    )
                    VALUES (
                        %(technical_number)s,
                        %(engine_name)s
                    )
                    RETURNING
                        id,
                        technical_number,
                        engine_name,
                        0::INT AS vehicle_count,
                        NULL::TIMESTAMPTZ AS last_seen_at,
                        created_at,
                        updated_at;
                    """,
                    normalized,
                )
                row = cur.fetchone()
            conn.commit()
        except UniqueViolation:
            conn.rollback()
            raise ValueError("Ese Technical Engine Configuration # ya esta registrado.") from None

    if row is None:
        raise RuntimeError("No se pudo crear el motor.")
    return MotorCatalogRecord(**row)


def update_motor(motor_id: int, payload: MotorUpdateRequest) -> MotorCatalogRecord:
    normalized_name = payload.engine_name.strip()
    normalized_technical = payload.technical_number.strip() if payload.technical_number else None
    with psycopg.connect(_database_dsn(), row_factory=dict_row) as conn:
        _ensure_motor_tables(conn)
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, technical_number FROM motor_catalog WHERE id = %s;",
                (motor_id,),
            )
            existing = cur.fetchone()
            if existing is None:
                raise ValueError("El motor no existe.")

            old_technical = str(existing["technical_number"])

            if normalized_technical and normalized_technical != old_technical:
                # Check uniqueness of new technical_number
                cur.execute(
                    "SELECT id FROM motor_catalog WHERE technical_number = %s AND id <> %s;",
                    (normalized_technical, motor_id),
                )
                if cur.fetchone() is not None:
                    raise ValueError("Ese Technical Engine Configuration # ya esta registrado en otro motor.")

                cur.execute(
                    """
                    UPDATE motor_catalog
                    SET engine_name = %s, technical_number = %s, updated_at = NOW()
                    WHERE id = %s
                    RETURNING id, technical_number, engine_name, created_at, updated_at;
                    """,
                    (normalized_name, normalized_technical, motor_id),
                )
                # Cascade: update vehicle_motor_assignments
                cur.execute(
                    """
                    UPDATE vehicle_motor_assignments
                    SET technical_number = %s, updated_at = NOW()
                    WHERE technical_number = %s;
                    """,
                    (normalized_technical, old_technical),
                )
            else:
                cur.execute(
                    """
                    UPDATE motor_catalog
                    SET engine_name = %s, updated_at = NOW()
                    WHERE id = %s
                    RETURNING id, technical_number, engine_name, created_at, updated_at;
                    """,
                    (normalized_name, motor_id),
                )
            row = cur.fetchone()
        conn.commit()

    if row is None:
        raise RuntimeError("No se pudo actualizar el motor.")

    # Reload with full data (vehicle_count, attachments, cpls)
    motors = list_motors()
    return next(m for m in motors if m.id == motor_id)


def delete_motor(motor_id: int) -> int:
    """Delete a motor and return the count of vehicles that were unlinked."""
    with psycopg.connect(_database_dsn(), row_factory=dict_row) as conn:
        _ensure_motor_tables(conn)
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, technical_number FROM motor_catalog WHERE id = %s;",
                (motor_id,),
            )
            existing = cur.fetchone()
            if existing is None:
                raise ValueError("El motor no existe.")

            technical_number = str(existing["technical_number"])

            # Count affected vehicles
            cur.execute(
                "SELECT COUNT(*) AS cnt FROM vehicle_motor_assignments WHERE technical_number = %s;",
                (technical_number,),
            )
            vehicle_count = int(cur.fetchone()["cnt"])

            # Delete motor (cascades to attachments and rule_groups via FK)
            cur.execute("DELETE FROM motor_catalog WHERE id = %s;", (motor_id,))
        conn.commit()

    return vehicle_count


def list_motor_attachments(motor_id: int) -> list[MotorAttachmentRecord]:
    with psycopg.connect(_database_dsn(), row_factory=dict_row) as conn:
        _ensure_motor_tables(conn)
        attachments_by_motor_id = _list_attachments_by_motor_ids(conn, [motor_id])
    return attachments_by_motor_id.get(motor_id, [])


def create_motor_attachment(
    motor_id: int,
    *,
    cpl: str | None,
    filename: str,
    content_type: str | None,
    fileobj: Any,
) -> MotorAttachmentRecord:
    normalized_cpl = _normalize_cpl(cpl)
    if not normalized_cpl:
        raise ValueError("Debes indicar el CPL del adjunto.")

    row = None
    normalized_filename, object_name, normalized_content_type, file_size = upload_file(
        filename=filename,
        content_type=content_type,
        fileobj=fileobj,
    )

    try:
        with psycopg.connect(_database_dsn(), row_factory=dict_row) as conn:
            _ensure_motor_tables(conn)
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT id
                    FROM motor_catalog
                    WHERE id = %s;
                    """,
                    (motor_id,),
                )
                motor_row = cur.fetchone()
                if motor_row is None:
                    raise ValueError("El motor indicado no existe.")

                cur.execute(
                    """
                    INSERT INTO motor_attachments (
                        motor_id,
                        cpl,
                        original_filename,
                        stored_filename,
                        storage_path,
                        content_type,
                        file_size
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    RETURNING
                        id,
                        motor_id,
                        cpl,
                        original_filename,
                        content_type,
                        file_size,
                        created_at,
                        updated_at;
                    """,
                    (
                        motor_id,
                        normalized_cpl,
                        normalized_filename,
                        object_name,
                        object_name,
                        normalized_content_type,
                        file_size,
                    ),
                )
                row = cur.fetchone()
            conn.commit()
    except Exception:
        delete_file(object_name)
        raise
    finally:
        try:
            fileobj.close()
        except Exception:
            pass

    if row is None:
        raise RuntimeError("No se pudo registrar el adjunto del motor.")
    return _build_attachment_record(row)


def update_motor_attachment(
    attachment_id: int,
    *,
    cpl: str | None,
    filename: str | None = None,
    content_type: str | None = None,
    fileobj: Any | None = None,
) -> MotorAttachmentRecord:
    normalized_cpl = _normalize_cpl(cpl)
    if not normalized_cpl:
        raise ValueError("Debes indicar el CPL del adjunto.")

    row = None
    replacement_file: tuple[str, str, str, int] | None = None
    old_object_name: str | None = None

    try:
        if fileobj is not None:
            replacement_file = upload_file(
                filename=filename or "",
                content_type=content_type,
                fileobj=fileobj,
            )

        with psycopg.connect(_database_dsn(), row_factory=dict_row) as conn:
            _ensure_motor_tables(conn)
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT
                        id,
                        motor_id,
                        cpl,
                        original_filename,
                        stored_filename,
                        storage_path,
                        content_type,
                        file_size,
                        created_at,
                        updated_at
                    FROM motor_attachments
                    WHERE id = %s;
                    """,
                    (attachment_id,),
                )
                current_row = cur.fetchone()
                if current_row is None:
                    raise ValueError("El adjunto indicado no existe.")

                if replacement_file is None:
                    cur.execute(
                        """
                        UPDATE motor_attachments
                        SET cpl = %s,
                            updated_at = NOW()
                        WHERE id = %s
                        RETURNING
                            id,
                            motor_id,
                            cpl,
                            original_filename,
                            content_type,
                            file_size,
                            created_at,
                            updated_at;
                        """,
                        (normalized_cpl, attachment_id),
                    )
                else:
                    normalized_filename, object_name, normalized_content_type, file_size = replacement_file
                    old_object_name = str(current_row["stored_filename"])
                    cur.execute(
                        """
                        UPDATE motor_attachments
                        SET cpl = %s,
                            original_filename = %s,
                            stored_filename = %s,
                            storage_path = %s,
                            content_type = %s,
                            file_size = %s,
                            updated_at = NOW()
                        WHERE id = %s
                        RETURNING
                            id,
                            motor_id,
                            cpl,
                            original_filename,
                            content_type,
                            file_size,
                            created_at,
                            updated_at;
                        """,
                        (
                            normalized_cpl,
                            normalized_filename,
                            object_name,
                            object_name,
                            normalized_content_type,
                            file_size,
                            attachment_id,
                        ),
                    )

                row = cur.fetchone()
            conn.commit()
    except Exception:
        if replacement_file is not None:
            _, new_object_name, _, _ = replacement_file
            delete_file(new_object_name)
        raise
    finally:
        if fileobj is not None:
            try:
                fileobj.close()
            except Exception:
                pass

    if replacement_file is not None and old_object_name:
        delete_file(old_object_name)

    if row is None:
        raise RuntimeError("No se pudo actualizar el adjunto.")
    return _build_attachment_record(row)


def delete_motor_attachment(attachment_id: int) -> None:
    with psycopg.connect(_database_dsn(), row_factory=dict_row) as conn:
        _ensure_motor_tables(conn)
        with conn.cursor() as cur:
            cur.execute(
                """
                DELETE FROM motor_attachments
                WHERE id = %s
                RETURNING stored_filename;
                """,
                (attachment_id,),
            )
            row = cur.fetchone()
        conn.commit()

    if row is None:
        raise ValueError("El adjunto indicado no existe.")

    delete_file(str(row["stored_filename"]))


def get_motor_attachment_file(attachment_id: int) -> tuple[MotorAttachmentRecord, Any]:
    """Returns (attachment_record, file_stream) where file_stream is a BytesIO."""
    with psycopg.connect(_database_dsn(), row_factory=dict_row) as conn:
        _ensure_motor_tables(conn)
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    id,
                    motor_id,
                    cpl,
                    original_filename,
                    content_type,
                    file_size,
                    stored_filename,
                    created_at,
                    updated_at
                FROM motor_attachments
                WHERE id = %s;
                """,
                (attachment_id,),
            )
            row = cur.fetchone()

    if row is None:
        raise ValueError("El adjunto indicado no existe.")

    object_name = str(row["stored_filename"])
    file_stream, _ = download_file(object_name)

    attachment = _build_attachment_record(
        {
            "id": row["id"],
            "motor_id": row["motor_id"],
            "cpl": row["cpl"],
            "original_filename": row["original_filename"],
            "content_type": row["content_type"],
            "file_size": row["file_size"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }
    )
    return attachment, file_stream


def migrate_local_files_to_minio() -> dict[str, Any]:
    """Migra archivos locales existentes (en /app/uploads/) al bucket de MinIO.

    Lee cada registro de motor_attachments cuyo storage_path apunta a un archivo
    local, lo sube a MinIO y actualiza la BD con el nuevo object_name.
    """
    from app.services.storage import ensure_bucket, file_exists, _get_client, _get_bucket
    import io

    ensure_bucket()
    client = _get_client()
    bucket = _get_bucket()

    migrated = 0
    skipped = 0
    errors: list[str] = []

    with psycopg.connect(_database_dsn(), row_factory=dict_row) as conn:
        _ensure_motor_tables(conn)
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, stored_filename, storage_path, content_type, file_size
                FROM motor_attachments
                ORDER BY id;
                """
            )
            rows = cur.fetchall()

    for row in rows:
        stored_filename = str(row["stored_filename"])
        storage_path_str = str(row["storage_path"])

        # Si ya es un object name de MinIO (no es una ruta con /)
        # y ya existe en MinIO, saltar
        if "/" not in storage_path_str and file_exists(stored_filename):
            skipped += 1
            continue

        # Intentar leer desde el path local
        local_path = Path(storage_path_str)
        if not local_path.exists():
            errors.append(f"ID {row['id']}: archivo local no encontrado en {storage_path_str}")
            continue

        try:
            file_data = local_path.read_bytes()
            object_name = stored_filename  # Mantener el mismo nombre de archivo

            client.put_object(
                bucket_name=bucket,
                object_name=object_name,
                data=io.BytesIO(file_data),
                length=len(file_data),
                content_type=str(row["content_type"]),
            )

            # Actualizar BD: storage_path y stored_filename apuntan al object name
            with psycopg.connect(_database_dsn(), row_factory=dict_row) as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        UPDATE motor_attachments
                        SET storage_path = %s,
                            stored_filename = %s,
                            updated_at = NOW()
                        WHERE id = %s;
                        """,
                        (object_name, object_name, row["id"]),
                    )
                conn.commit()

            migrated += 1
            _logger.info("Migrado adjunto ID %s a MinIO: %s", row["id"], object_name)

        except Exception as exc:
            errors.append(f"ID {row['id']}: {exc}")
            _logger.error("Error migrando adjunto ID %s: %s", row["id"], exc, exc_info=True)

    return {
        "migrated": migrated,
        "skipped": skipped,
        "errors": errors,
        "total": len(rows),
    }


def find_assignment_by_engine_number(engine_number: str) -> dict[str, str | None] | None:
    """Search existing assignments for a vehicle with the same ESN.

    Returns {"technical_number": ..., "cpl": ...} if found, else None.
    """
    normalized = engine_number.strip()
    if not normalized:
        return None

    with psycopg.connect(_database_dsn(), row_factory=dict_row) as conn:
        _ensure_motor_tables(conn)
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT technical_number, cpl
                FROM vehicle_motor_assignments
                WHERE engine_number = %s
                ORDER BY last_seen_at DESC
                LIMIT 1;
                """,
                (normalized,),
            )
            row = cur.fetchone()

    if row is None:
        return None
    return {"technical_number": row["technical_number"], "cpl": row.get("cpl")}


def find_registered_motor(technical_number: str) -> RegisteredMotorSummary | None:
    normalized_technical_number = technical_number.strip()
    if not normalized_technical_number:
        return None

    with psycopg.connect(_database_dsn(), row_factory=dict_row) as conn:
        _ensure_motor_tables(conn)
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, technical_number, engine_name
                FROM motor_catalog
                WHERE technical_number = %s;
                """,
                (normalized_technical_number,),
            )
            row = cur.fetchone()

    if row is None:
        return None
    return RegisteredMotorSummary(**row)


def list_vehicle_assignment_summaries(search: str | None = None) -> list[VehicleAssignmentSummary]:
    """Listado ligero de vehiculos para la tabla: omite adjuntos completos
    y campos exclusivos del modal de detalles. Solo trae el conteo de
    adjuntos matching CPL por vehiculo."""
    params: list[Any] = []
    where_clause = ""

    if search:
        normalized_search = f"%{search.strip().upper()}%"
        where_clause = """
            WHERE UPPER(a.plate) LIKE %s
               OR UPPER(COALESCE(a.vin, '')) LIKE %s
               OR UPPER(a.technical_number) LIKE %s
               OR UPPER(COALESCE(a.cpl, '')) LIKE %s
               OR UPPER(COALESCE(a.marketing_model_name, '')) LIKE %s
               OR UPPER(COALESCE(a.service_model_name, '')) LIKE %s
               OR UPPER(COALESCE(m.engine_name, '')) LIKE %s
               OR UPPER(COALESCE(c.name, '')) LIKE %s
               OR UPPER(COALESCE(cd.database_name, '')) LIKE %s
               OR UPPER(COALESCE(cd.username, '')) LIKE %s
               OR UPPER(COALESCE(a.nombre_vehiculo, '')) LIKE %s
               OR UPPER(COALESCE(a.marca, '')) LIKE %s
               OR UPPER(COALESCE(a.linea, '')) LIKE %s
        """
        params.extend([normalized_search] * 13)

    with psycopg.connect(_database_dsn(), row_factory=dict_row) as conn:
        _ensure_motor_tables(conn)
        with conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT
                    a.plate,
                    a.customer_id,
                    a.customer_database_id,
                    a.vin,
                    a.technical_number,
                    a.cpl,
                    a.marca,
                    a.linea,
                    a.ano_modelo,
                    a.tipo_combustible,
                    a.nombre_vehiculo,
                    a.engine_number,
                    m.engine_name,
                    c.name AS client_name,
                    COALESCE(a.category, c.category, 'Ninguna') AS category,
                    (a.category IS NULL) AS category_is_inherited,
                    COALESCE(c.category, 'Ninguna') AS customer_category,
                    cd.database_name,
                    cd.username AS database_username,
                    cd.connection_type AS database_connection_type,
                    COALESCE(a.access_url, cd.access_url) AS access_url,
                    EXISTS (
                        SELECT 1
                        FROM geotab_rule_groups grg
                        INNER JOIN motor_catalog mc
                            ON mc.id = grg.motor_id
                           AND mc.technical_number = a.technical_number
                        INNER JOIN customer_databases sibling_db
                            ON sibling_db.id = grg.database_id
                        WHERE cd.connection_type = 'geotab'
                          AND sibling_db.connection_type = 'geotab'
                          AND LOWER(sibling_db.database_name) = LOWER(cd.database_name)
                    ) AS has_motor_rules,
                    a.vocacional,
                    a.last_seen_at,
                    COALESCE((
                        SELECT COUNT(*)
                        FROM motor_catalog mc2
                        INNER JOIN motor_attachments ma2
                            ON ma2.motor_id = mc2.id
                        WHERE mc2.technical_number = a.technical_number
                          AND (ma2.cpl IS NULL OR ma2.cpl = '' OR ma2.cpl = a.cpl)
                    ), 0) AS attachments_count
                FROM vehicle_motor_assignments a
                LEFT JOIN motor_catalog m
                    ON m.technical_number = a.technical_number
                LEFT JOIN customers c
                    ON c.id = a.customer_id
                LEFT JOIN customer_databases cd
                    ON cd.id = a.customer_database_id
                {where_clause}
                ORDER BY a.last_seen_at DESC, a.plate ASC;
                """,
                params,
            )
            rows = cur.fetchall()

    summaries: list[VehicleAssignmentSummary] = []
    for row in rows:
        effective_provider = infer_provider_key(
            connection_type=row.get("database_connection_type"),
            database_name=row.get("database_name"),
            access_url=row.get("access_url"),
        )
        summaries.append(
            VehicleAssignmentSummary(
                plate=row["plate"],
                customer_id=row.get("customer_id"),
                customer_database_id=row.get("customer_database_id"),
                vin=row.get("vin"),
                technical_number=str(row["technical_number"]),
                cpl=_normalize_cpl(row.get("cpl")),
                marca=row.get("marca"),
                linea=row.get("linea"),
                ano_modelo=row.get("ano_modelo"),
                tipo_combustible=row.get("tipo_combustible"),
                nombre_vehiculo=row.get("nombre_vehiculo"),
                engine_name=row.get("engine_name"),
                engine_number=row.get("engine_number"),
                client_name=row.get("client_name"),
                category=row.get("category") or "Ninguna",
                category_is_inherited=bool(row.get("category_is_inherited")),
                customer_category=row.get("customer_category") or "Ninguna",
                database_name=row.get("database_name"),
                database_username=row.get("database_username"),
                database_connection_type=effective_provider if row.get("database_name") else None,
                has_motor_rules=bool(row.get("has_motor_rules")),
                vocacional=bool(row.get("vocacional")),
                attachments_count=int(row.get("attachments_count") or 0),
                last_seen_at=row["last_seen_at"],
            )
        )
    return summaries


def list_vehicle_assignments(search: str | None = None) -> list[VehicleAssignmentRecord]:
    params: list[Any] = []
    where_clause = ""

    if search:
        normalized_search = f"%{search.strip().upper()}%"
        where_clause = """
            WHERE UPPER(a.plate) LIKE %s
               OR UPPER(COALESCE(a.vin, '')) LIKE %s
               OR UPPER(a.technical_number) LIKE %s
               OR UPPER(COALESCE(a.cpl, '')) LIKE %s
               OR UPPER(COALESCE(a.marketing_model_name, '')) LIKE %s
               OR UPPER(COALESCE(a.service_model_name, '')) LIKE %s
               OR UPPER(COALESCE(m.engine_name, '')) LIKE %s
               OR UPPER(COALESCE(c.name, '')) LIKE %s
               OR UPPER(COALESCE(cd.database_name, '')) LIKE %s
               OR UPPER(COALESCE(cd.username, '')) LIKE %s
               OR UPPER(COALESCE(a.nombre_vehiculo, '')) LIKE %s
               OR UPPER(COALESCE(a.marca, '')) LIKE %s
               OR UPPER(COALESCE(a.linea, '')) LIKE %s
        """
        params.extend(
            [
                normalized_search,
                normalized_search,
                normalized_search,
                normalized_search,
                normalized_search,
                normalized_search,
                normalized_search,
                normalized_search,
                normalized_search,
                normalized_search,
                normalized_search,
                normalized_search,
                normalized_search,
                normalized_search,
            ]
        )

    with db_conn(row_factory=dict_row) as conn:
        _ensure_motor_tables(conn)
        with conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT
                    a.plate,
                    a.customer_id,
                    a.customer_database_id,
                    a.vin,
                    a.geotab_status,
                    a.geotab_customer_status,
                    a.geotab_customer_database_id,
                    a.geotab_device_id,
                    a.geotab_device_synced_at,
                    a.engine_number,
                    a.technical_number,
                    a.cpl,
                    a.marketing_model_name,
                    a.service_model_name,
                    a.marca,
                    a.linea,
                    a.ano_modelo,
                    a.tipo_combustible,
                    a.nombre_vehiculo,
                    m.engine_name,
                    c.name AS client_name,
                    COALESCE(a.category, c.category, 'Ninguna') AS category,
                    (a.category IS NULL) AS category_is_inherited,
                    COALESCE(c.category, 'Ninguna') AS customer_category,
                    cd.database_name,
                    cd.username AS database_username,
                    cd.connection_type AS database_connection_type,
                    (cd.password IS NOT NULL AND cd.password <> '') AS has_database_password,
                    COALESCE(a.access_url, cd.access_url) AS access_url,
                    EXISTS (
                        SELECT 1
                        FROM geotab_rule_groups grg
                        INNER JOIN motor_catalog mc
                            ON mc.id = grg.motor_id
                           AND mc.technical_number = a.technical_number
                        INNER JOIN customer_databases sibling_db
                            ON sibling_db.id = grg.database_id
                        WHERE cd.connection_type = 'geotab'
                          AND sibling_db.connection_type = 'geotab'
                          AND LOWER(sibling_db.database_name) = LOWER(cd.database_name)
                    ) AS has_motor_rules,
                    a.created_at,
                    a.updated_at,
                    a.last_seen_at,
                    a.vocacional,
                    vpb.provider_vehicle_id,
                    vpb.is_manual AS provider_vehicle_id_is_manual
                FROM vehicle_motor_assignments a
                LEFT JOIN motor_catalog m
                    ON m.technical_number = a.technical_number
                LEFT JOIN customers c
                    ON c.id = a.customer_id
                LEFT JOIN customer_databases cd
                    ON cd.id = a.customer_database_id
                LEFT JOIN LATERAL (
                    SELECT vpb.provider_vehicle_id, vpb.is_manual, vpb.binding_status
                    FROM vehicle_provider_bindings vpb
                    WHERE vpb.plate = a.plate
                      AND vpb.customer_database_id = a.customer_database_id
                    ORDER BY vpb.is_manual DESC, vpb.updated_at DESC
                    LIMIT 1
                ) vpb ON TRUE
                {where_clause}
                ORDER BY a.last_seen_at DESC, a.plate ASC;
                """,
                params,
            )
            rows = cur.fetchall()
        attachments_by_technical_number = _list_attachments_by_technical_numbers(
            conn, [str(row["technical_number"]) for row in rows]
        )

    records: list[VehicleAssignmentRecord] = []
    for row in rows:
        row_cpl = _normalize_cpl(row.get("cpl"))
        effective_provider = infer_provider_key(
            connection_type=row.get("database_connection_type"),
            database_name=row.get("database_name"),
            access_url=row.get("access_url"),
        )
        candidate_attachments = attachments_by_technical_number.get(str(row["technical_number"]), [])
        matching_attachments = [
            attachment
            for attachment in candidate_attachments
            if _attachment_matches_vehicle_cpl(attachment.cpl, row_cpl)
        ]
        payload = dict(row)
        payload["database_connection_type"] = effective_provider if payload.get("database_name") else None
        payload["provider_vehicle_id"] = row.get("provider_vehicle_id")
        payload["is_provider_vehicle_id_manual"] = bool(row.get("provider_vehicle_id_is_manual"))
        records.append(
            VehicleAssignmentRecord(
                **payload,
                attachments=matching_attachments,
            )
        )
    return records


def get_vehicle_assignment(plate: str) -> VehicleAssignmentRecord | None:
    """Devuelve el registro completo (con adjuntos) de un vehiculo por placa.
    Pensado para el modal de Detalles; el listado usa el summary ligero."""
    normalized_plate = plate.strip().upper()
    if not normalized_plate:
        return None

    with psycopg.connect(_database_dsn(), row_factory=dict_row) as conn:
        _ensure_motor_tables(conn)
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    a.plate,
                    a.customer_id,
                    a.customer_database_id,
                    a.vin,
                    a.geotab_status,
                    a.geotab_customer_status,
                    a.geotab_customer_database_id,
                    a.geotab_device_id,
                    a.geotab_device_synced_at,
                    a.engine_number,
                    a.technical_number,
                    a.cpl,
                    a.marketing_model_name,
                    a.service_model_name,
                    a.marca,
                    a.linea,
                    a.ano_modelo,
                    a.tipo_combustible,
                    a.nombre_vehiculo,
                    m.engine_name,
                    c.name AS client_name,
                    COALESCE(a.category, c.category, 'Ninguna') AS category,
                    (a.category IS NULL) AS category_is_inherited,
                    COALESCE(c.category, 'Ninguna') AS customer_category,
                    cd.database_name,
                    cd.username AS database_username,
                    cd.connection_type AS database_connection_type,
                    (cd.password IS NOT NULL AND cd.password <> '') AS has_database_password,
                    COALESCE(a.access_url, cd.access_url) AS access_url,
                    EXISTS (
                        SELECT 1
                        FROM geotab_rule_groups grg
                        INNER JOIN motor_catalog mc
                            ON mc.id = grg.motor_id
                           AND mc.technical_number = a.technical_number
                        INNER JOIN customer_databases sibling_db
                            ON sibling_db.id = grg.database_id
                        WHERE cd.connection_type = 'geotab'
                          AND sibling_db.connection_type = 'geotab'
                          AND LOWER(sibling_db.database_name) = LOWER(cd.database_name)
                    ) AS has_motor_rules,
                    a.created_at,
                    a.updated_at,
                    a.last_seen_at,
                    a.vocacional,
                    vpb.provider_vehicle_id,
                    vpb.is_manual AS provider_vehicle_id_is_manual
                FROM vehicle_motor_assignments a
                LEFT JOIN motor_catalog m
                    ON m.technical_number = a.technical_number
                LEFT JOIN customers c
                    ON c.id = a.customer_id
                LEFT JOIN customer_databases cd
                    ON cd.id = a.customer_database_id
                LEFT JOIN LATERAL (
                    SELECT vpb.provider_vehicle_id, vpb.is_manual, vpb.binding_status
                    FROM vehicle_provider_bindings vpb
                    WHERE vpb.plate = a.plate
                      AND vpb.customer_database_id = a.customer_database_id
                    ORDER BY vpb.is_manual DESC, vpb.updated_at DESC
                    LIMIT 1
                ) vpb ON TRUE
                WHERE a.plate = %s
                ORDER BY a.last_seen_at DESC, a.plate ASC
                LIMIT 1;
                """,
                (normalized_plate,),
            )
            row = cur.fetchone()
        if row is None:
            return None
        attachments_by_technical_number = _list_attachments_by_technical_numbers(
            conn, [str(row["technical_number"])]
        )

    row_cpl = _normalize_cpl(row.get("cpl"))
    effective_provider = infer_provider_key(
        connection_type=row.get("database_connection_type"),
        database_name=row.get("database_name"),
        access_url=row.get("access_url"),
    )
    candidate_attachments = attachments_by_technical_number.get(str(row["technical_number"]), [])
    matching_attachments = [
        attachment
        for attachment in candidate_attachments
        if _attachment_matches_vehicle_cpl(attachment.cpl, row_cpl)
    ]
    payload = dict(row)
    payload["database_connection_type"] = effective_provider if payload.get("database_name") else None
    payload["provider_vehicle_id"] = row.get("provider_vehicle_id")
    payload["is_provider_vehicle_id_manual"] = bool(row.get("provider_vehicle_id_is_manual"))
    return VehicleAssignmentRecord(**payload, attachments=matching_attachments)


def register_vehicle_assignment(
    plate: str,
    technical_number: str,
    cpl: str | None = None,
    marketing_model_name: str | None = None,
    service_model_name: str | None = None,
    geotab_status: str = "unknown",
    vin: str | None = None,
    engine_number: str | None = None,
    marca: str | None = None,
    linea: str | None = None,
    ano_modelo: str | None = None,
    tipo_combustible: str | None = None,
    nombre_vehiculo: str | None = None,
) -> None:
    normalized_plate = plate.strip().upper()
    normalized_technical_number = technical_number.strip()
    if not normalized_plate or not normalized_technical_number:
        return

    with psycopg.connect(_database_dsn(), row_factory=dict_row) as conn:
        _ensure_motor_tables(conn)
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO vehicle_motor_assignments (
                    plate,
                    vin,
                    geotab_status,
                    engine_number,
                    technical_number,
                    cpl,
                    marketing_model_name,
                    service_model_name,
                    marca,
                    linea,
                    ano_modelo,
                    tipo_combustible,
                    nombre_vehiculo
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (plate)
                DO UPDATE SET
                    vin = EXCLUDED.vin,
                    geotab_status = EXCLUDED.geotab_status,
                    engine_number = EXCLUDED.engine_number,
                    technical_number = EXCLUDED.technical_number,
                    cpl = EXCLUDED.cpl,
                    marketing_model_name = EXCLUDED.marketing_model_name,
                    service_model_name = EXCLUDED.service_model_name,
                    marca = EXCLUDED.marca,
                    linea = EXCLUDED.linea,
                    ano_modelo = EXCLUDED.ano_modelo,
                    tipo_combustible = EXCLUDED.tipo_combustible,
                    nombre_vehiculo = EXCLUDED.nombre_vehiculo,
                    updated_at = NOW(),
                    last_seen_at = NOW();
                """,
                (
                    normalized_plate,
                    _normalize_optional_text(vin),
                    _normalize_optional_text(geotab_status) or "unknown",
                    _normalize_optional_text(engine_number),
                    normalized_technical_number,
                    _normalize_cpl(cpl),
                    _normalize_optional_text(marketing_model_name),
                    _normalize_optional_text(service_model_name),
                    _normalize_optional_text(marca),
                    _normalize_optional_text(linea),
                    _normalize_optional_text(ano_modelo),
                    _normalize_optional_text(tipo_combustible),
                    _normalize_optional_text(nombre_vehiculo),
                ),
            )
        conn.commit()


def update_vehicle_metadata(
    plate: str,
    *,
    geotab_status: str = "unknown",
    vin: str | None = None,
    engine_number: str | None = None,
    marca: str | None = None,
    linea: str | None = None,
    ano_modelo: str | None = None,
    tipo_combustible: str | None = None,
    nombre_vehiculo: str | None = None,
    marketing_model_name: str | None = None,
    service_model_name: str | None = None,
) -> None:
    """Update Fenix/Geotab fields on an existing vehicle WITHOUT touching technical_number."""
    normalized_plate = plate.strip().upper()
    if not normalized_plate:
        return

    with psycopg.connect(_database_dsn(), row_factory=dict_row) as conn:
        _ensure_motor_tables(conn)
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE vehicle_motor_assignments
                SET
                    vin = COALESCE(%s, vin),
                    geotab_status = %s,
                    engine_number = COALESCE(%s, engine_number),
                    marca = COALESCE(%s, marca),
                    linea = COALESCE(%s, linea),
                    ano_modelo = COALESCE(%s, ano_modelo),
                    tipo_combustible = COALESCE(%s, tipo_combustible),
                    nombre_vehiculo = COALESCE(%s, nombre_vehiculo),
                    marketing_model_name = COALESCE(%s, marketing_model_name),
                    service_model_name = COALESCE(%s, service_model_name),
                    updated_at = NOW(),
                    last_seen_at = NOW()
                WHERE plate = %s;
                """,
                (
                    _normalize_optional_text(vin),
                    _normalize_optional_text(geotab_status) or "unknown",
                    _normalize_optional_text(engine_number),
                    _normalize_optional_text(marca),
                    _normalize_optional_text(linea),
                    _normalize_optional_text(ano_modelo),
                    _normalize_optional_text(tipo_combustible),
                    _normalize_optional_text(nombre_vehiculo),
                    _normalize_optional_text(marketing_model_name),
                    _normalize_optional_text(service_model_name),
                    normalized_plate,
                ),
            )
        conn.commit()


def set_vehicle_category(plate: str, category: str | None) -> dict[str, Any]:
    """Fija (o limpia) el override de categoria de un vehiculo.

    category=None limpia el override y el vehiculo vuelve a heredar la categoria
    del cliente. Un string valido fija un override propio.
    Devuelve la categoria efectiva resultante.
    """
    normalized_plate = plate.strip().upper()
    if not normalized_plate:
        raise ValueError("La placa es obligatoria.")
    override = None if category is None else _normalize_category(category)

    with psycopg.connect(_database_dsn(), row_factory=dict_row) as conn:
        _ensure_motor_tables(conn)
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE vehicle_motor_assignments
                SET category = %s, updated_at = NOW()
                WHERE plate = %s
                RETURNING plate, category;
                """,
                (override, normalized_plate),
            )
            row = cur.fetchone()
            if row is None:
                raise ValueError("El vehiculo no existe.")
            cur.execute(
                """
                SELECT
                    COALESCE(a.category, c.category, 'Ninguna') AS category,
                    (a.category IS NULL) AS category_is_inherited,
                    COALESCE(c.category, 'Ninguna') AS customer_category
                FROM vehicle_motor_assignments a
                LEFT JOIN customers c ON c.id = a.customer_id
                WHERE a.plate = %s;
                """,
                (normalized_plate,),
            )
            effective = cur.fetchone()
        conn.commit()

    return {
        "plate": normalized_plate,
        "category": effective["category"],
        "category_is_inherited": bool(effective["category_is_inherited"]),
        "customer_category": effective["customer_category"],
    }


def set_vehicle_vocacional(plate: str, vocacional: bool) -> dict[str, Any]:
    """Marca o desmarca un vehiculo como vocacional."""
    normalized_plate = plate.strip().upper()
    if not normalized_plate:
        raise ValueError("La placa es obligatoria.")

    with psycopg.connect(_database_dsn(), row_factory=dict_row) as conn:
        _ensure_motor_tables(conn)
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE vehicle_motor_assignments
                SET vocacional = %s, updated_at = NOW()
                WHERE plate = %s
                RETURNING plate, vocacional;
                """,
                (bool(vocacional), normalized_plate),
            )
            row = cur.fetchone()
            if row is None:
                raise ValueError("El vehiculo no existe.")
        conn.commit()

    return {
        "plate": normalized_plate,
        "vocacional": bool(row["vocacional"]),
    }


def get_vehicle_database_assignment(plate: str | None) -> AssignedDatabaseSummary:
    normalized_plate = (plate or "").strip().upper()
    if not normalized_plate:
        return AssignedDatabaseSummary()

    with psycopg.connect(_database_dsn(), row_factory=dict_row) as conn:
        _ensure_motor_tables(conn)
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    c.name AS client_name,
                    cd.database_name,
                    cd.username AS database_username,
                    (cd.password IS NOT NULL AND cd.password <> '') AS has_database_password
                FROM vehicle_motor_assignments a
                LEFT JOIN customers c
                    ON c.id = a.customer_id
                LEFT JOIN customer_databases cd
                    ON cd.id = a.customer_database_id
                WHERE a.plate = %s;
                """,
                (normalized_plate,),
            )
            row = cur.fetchone()

    if row is None:
        return AssignedDatabaseSummary()
    return AssignedDatabaseSummary(**row)


def get_cached_vehicle_lookup(plate: str) -> VehicleLookupResponse | None:
    """Return a VehicleLookupResponse built from locally stored data, or None."""
    normalized_plate = plate.strip().upper()
    if not normalized_plate:
        return None

    with psycopg.connect(_database_dsn(), row_factory=dict_row) as conn:
        _ensure_motor_tables(conn)
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    a.plate,
                    a.vin,
                    a.geotab_status,
                    a.geotab_customer_status,
                    a.engine_number,
                    a.technical_number,
                    a.cpl,
                    a.marketing_model_name,
                    a.service_model_name,
                    a.marca,
                    a.linea,
                    a.ano_modelo,
                    a.tipo_combustible,
                    a.nombre_vehiculo,
                    m.engine_name,
                    m.id AS motor_id,
                    c.name AS client_name,
                    cd.database_name,
                    cd.username AS database_username,
                    (cd.password IS NOT NULL AND cd.password <> '') AS has_database_password
                FROM vehicle_motor_assignments a
                LEFT JOIN motor_catalog m
                    ON m.technical_number = a.technical_number
                LEFT JOIN customers c
                    ON c.id = a.customer_id
                LEFT JOIN customer_databases cd
                    ON cd.id = a.customer_database_id
                WHERE a.plate = %s;
                """,
                (normalized_plate,),
            )
            row = cur.fetchone()

    if row is None:
        return None

    registered_motor = None
    if row.get("engine_name") and row.get("motor_id"):
        registered_motor = RegisteredMotorSummary(
            id=row["motor_id"],
            technical_number=row["technical_number"],
            engine_name=row["engine_name"],
        )

    return VehicleLookupResponse(
        plate=row["plate"],
        lookup_value=normalized_plate,
        lookup_type="plate",
        vin=row.get("vin"),
        geotab_status=row.get("geotab_status", "unknown"),
        geotab_customer_status=row.get("geotab_customer_status", "not_applicable"),
        marca=row.get("marca"),
        linea=row.get("linea"),
        ano_modelo=row.get("ano_modelo"),
        tipo_combustible=row.get("tipo_combustible"),
        nombre_vehiculo=row.get("nombre_vehiculo"),
        engine_number=row.get("engine_number"),
        technical_engine_configuration=row.get("technical_number"),
        cpl=row.get("cpl"),
        marketing_model_name=row.get("marketing_model_name"),
        service_model_name=row.get("service_model_name"),
        registered_motor=registered_motor,
        assigned_database=AssignedDatabaseSummary(
            client_name=row.get("client_name"),
            database_name=row.get("database_name"),
            database_username=row.get("database_username"),
            has_database_password=row.get("has_database_password", False),
        ),
        source_details={"fenix": {}, "cummins": {}},
        warnings=[],
        status="ok",
        message="Datos cargados desde cache local.",
        cached=True,
    )


def get_vehicle_geotab_customer_status(plate: str | None) -> dict[str, Any]:
    normalized_plate = (plate or "").strip().upper()
    if not normalized_plate:
        return {"geotab_customer_status": "not_applicable", "geotab_customer_database_id": None}

    with psycopg.connect(_database_dsn(), row_factory=dict_row) as conn:
        _ensure_motor_tables(conn)
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT geotab_customer_status, geotab_customer_database_id
                FROM vehicle_motor_assignments
                WHERE plate = %s;
                """,
                (normalized_plate,),
            )
            row = cur.fetchone()

    if row is None:
        return {"geotab_customer_status": "not_applicable", "geotab_customer_database_id": None}
    return dict(row)


def list_customers() -> list[CustomerRecord]:
    with db_conn(row_factory=dict_row) as conn:
        _ensure_motor_tables(conn)
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    c.id,
                    c.name,
                    COALESCE(c.category, 'Ninguna') AS category,
                    c.is_active,
                    COUNT(cd.id)::INT AS database_count,
                    c.created_at,
                    c.updated_at
                FROM customers c
                LEFT JOIN customer_databases cd
                    ON cd.customer_id = c.id
                GROUP BY c.id, c.name, c.category, c.is_active, c.created_at, c.updated_at
                ORDER BY c.name ASC;
                """
            )
            customer_rows = cur.fetchall()

            cur.execute(
                """
                SELECT
                    id,
                    customer_id,
                    database_name,
                    username,
                    (password IS NOT NULL AND password <> '') AS has_password,
                    connection_type,
                    access_url,
                    provider_config,
                    created_at,
                    updated_at
                FROM customer_databases
                ORDER BY customer_id ASC, database_name ASC, username ASC;
                """
            )
            database_rows = cur.fetchall()

            cur.execute(
                """
                SELECT id, database_id, name, rule_id, category, created_at
                FROM geotab_rules
                ORDER BY database_id ASC, name ASC;
                """
            )
            rule_rows = cur.fetchall()

            cur.execute(
                """
                SELECT
                    gra.id,
                    gra.geotab_rule_id,
                    gra.category,
                    gra.motor_id,
                    mc.engine_name AS motor_name,
                    mc.technical_number,
                    gra.event_type,
                    gra.created_at
                FROM geotab_rule_applications gra
                LEFT JOIN motor_catalog mc
                    ON mc.id = gra.motor_id
                ORDER BY gra.geotab_rule_id ASC, gra.category ASC, mc.engine_name ASC;
                """
            )
            application_rows = cur.fetchall()

            cur.execute(
                """
                SELECT
                    grg.id,
                    grg.database_id,
                    grg.motor_id,
                    mc.engine_name AS motor_name,
                    mc.technical_number,
                    grg.name,
                    grg.match_mode,
                    grg.created_at,
                    grg.updated_at
                FROM geotab_rule_groups grg
                INNER JOIN motor_catalog mc
                    ON mc.id = grg.motor_id
                ORDER BY grg.database_id ASC, mc.engine_name ASC, grg.name ASC;
                """
            )
            group_rows = cur.fetchall()

            cur.execute(
                """
                SELECT
                    grgr.group_id,
                    gr.id AS rule_record_id,
                    gr.name,
                    gr.rule_id
                FROM geotab_rule_group_rules grgr
                INNER JOIN geotab_rules gr
                    ON gr.id = grgr.geotab_rule_id
                ORDER BY grgr.group_id ASC, gr.name ASC;
                """
            )
            group_rule_rows = cur.fetchall()

    rules_by_database: dict[int, list[GeotabRuleRecord]] = {}
    for record in _build_rule_records(rule_rows, application_rows):
        rules_by_database.setdefault(record.database_id, []).append(record)

    groups_by_database = _build_rule_group_records(group_rows, group_rule_rows)

    # Filas que apuntan a la misma db fisica de Geotab (mismo database_name)
    # comparten reglas y grupos, sin importar el cliente o la credencial.
    database_name_to_db_ids: dict[str, list[int]] = {}
    geotab_db_ids: set[int] = set()
    for row in database_rows:
        effective_provider = infer_provider_key(
            connection_type=row.get("connection_type"),
            database_name=row.get("database_name"),
            access_url=row.get("access_url"),
            provider_config=row.get("provider_config"),
        )
        if effective_provider == "geotab":
            key = str(row["database_name"]).lower()
            database_name_to_db_ids.setdefault(key, []).append(int(row["id"]))
            geotab_db_ids.add(int(row["id"]))

    db_id_to_siblings: dict[int, list[int]] = {}
    for sibling_ids in database_name_to_db_ids.values():
        for db_id in sibling_ids:
            db_id_to_siblings[db_id] = sibling_ids

    def _merged_rules(db_id: int) -> list[GeotabRuleRecord]:
        if db_id not in geotab_db_ids:
            return rules_by_database.get(db_id, [])
        seen_rule_ids: set[str] = set()
        merged: list[GeotabRuleRecord] = []
        for sibling_id in db_id_to_siblings.get(db_id, [db_id]):
            for rule in rules_by_database.get(sibling_id, []):
                if rule.rule_id not in seen_rule_ids:
                    seen_rule_ids.add(rule.rule_id)
                    merged.append(rule)
        return sorted(merged, key=lambda r: r.name.lower())

    def _merged_groups(db_id: int) -> list[GeotabRuleGroupRecord]:
        if db_id not in geotab_db_ids:
            return groups_by_database.get(db_id, [])
        seen_group_ids: set[int] = set()
        merged: list[GeotabRuleGroupRecord] = []
        for sibling_id in db_id_to_siblings.get(db_id, [db_id]):
            for group in groups_by_database.get(sibling_id, []):
                if group.id not in seen_group_ids:
                    seen_group_ids.add(group.id)
                    merged.append(group)
        return _merge_rule_group_records_by_motor_identity(merged)

    databases_by_customer: dict[int, list[CustomerDatabaseRecord]] = {}
    for row in database_rows:
        db_id = int(row["id"])
        payload = dict(row)
        payload["connection_type"] = infer_provider_key(
            connection_type=payload.get("connection_type"),
            database_name=payload.get("database_name"),
            access_url=payload.get("access_url"),
            provider_config=payload.get("provider_config"),
        )
        payload["provider_config"] = public_provider_config(
            str(payload.get("connection_type") or "database"),
            payload.get("provider_config"),
        )
        record = CustomerDatabaseRecord(
            **payload,
            rules=_merged_rules(db_id),
            rule_groups=_merged_groups(db_id),
        )
        databases_by_customer.setdefault(record.customer_id, []).append(record)

    return [
        CustomerRecord(
            **row,
            databases=databases_by_customer.get(int(row["id"]), []),
        )
        for row in customer_rows
    ]


def create_customer(payload: CustomerCreateRequest) -> CustomerRecord:
    normalized_name = payload.name.strip()
    normalized_category = _normalize_category(payload.category)
    with psycopg.connect(_database_dsn(), row_factory=dict_row) as conn:
        _ensure_motor_tables(conn)
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO customers (name, category)
                    VALUES (%s, %s)
                    RETURNING id, name, category, is_active, 0::INT AS database_count, created_at, updated_at;
                    """,
                    (normalized_name, normalized_category),
                )
                row = cur.fetchone()
            conn.commit()
        except UniqueViolation:
            conn.rollback()
            raise ValueError("Ese cliente ya existe.") from None

    if row is None:
        raise RuntimeError("No se pudo crear el cliente.")
    return CustomerRecord(**row, databases=[])


def create_customer_database(
    customer_id: int, payload: CustomerDatabaseCreateRequest
) -> CustomerDatabaseRecord:
    normalized_database_name = payload.database_name.strip()
    normalized_username = payload.username.strip()
    normalized_password = payload.password.strip()
    normalized_connection_type = _normalize_connection_type(payload.connection_type)
    normalized_access_url = (payload.access_url or "").strip() or None
    if not uses_access_url(normalized_connection_type):
        normalized_access_url = None
    normalized_provider_config = normalize_provider_config(
        normalized_connection_type,
        payload.provider_config,
        username=normalized_username,
        password=normalized_password,
    )

    with psycopg.connect(_database_dsn(), row_factory=dict_row) as conn:
        _ensure_motor_tables(conn)
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM customers WHERE id = %s;", (customer_id,))
            customer_row = cur.fetchone()
            if customer_row is None:
                raise ValueError("El cliente no existe.")

        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO customer_databases (
                        customer_id,
                        database_name,
                        username,
                        password,
                        connection_type,
                        access_url,
                        provider_config
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    RETURNING
                        id,
                        customer_id,
                        database_name,
                        username,
                        (password IS NOT NULL AND password <> '') AS has_password,
                        connection_type,
                        access_url,
                        provider_config,
                        created_at,
                        updated_at;
                    """,
                    (
                        customer_id,
                        normalized_database_name,
                        normalized_username,
                        normalized_password,
                        normalized_connection_type,
                        normalized_access_url,
                        Jsonb(normalized_provider_config),
                    ),
                )
                row = cur.fetchone()
            if row is not None:
                _sync_primary_credential(
                    conn, int(row["id"]), normalized_username, normalized_password
                )
            conn.commit()
        except UniqueViolation:
            conn.rollback()
            raise ValueError("Esa database ya existe para el cliente con ese usuario.") from None

    if row is None:
        raise RuntimeError("No se pudo crear la database del cliente.")
    payload = dict(row)
    payload["provider_config"] = public_provider_config(
        str(payload.get("connection_type") or "database"),
        payload.get("provider_config"),
    )
    return CustomerDatabaseRecord(**payload, rules=[], rule_groups=[])


def update_customer(customer_id: int, payload: CustomerUpdateRequest) -> CustomerRecord:
    normalized_name = payload.name.strip()
    normalized_category = _normalize_category(payload.category)
    with psycopg.connect(_database_dsn(), row_factory=dict_row) as conn:
        _ensure_motor_tables(conn)
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM customers WHERE id = %s;", (customer_id,))
            if cur.fetchone() is None:
                raise ValueError("El cliente no existe.")

        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE customers
                    SET name = %s, category = %s, updated_at = NOW()
                    WHERE id = %s
                    RETURNING id, name, category, created_at, updated_at;
                    """,
                    (normalized_name, normalized_category, customer_id),
                )
                row = cur.fetchone()
            conn.commit()
        except UniqueViolation:
            conn.rollback()
            raise ValueError("Ya existe un cliente con ese nombre.") from None

    if row is None:
        raise RuntimeError("No se pudo actualizar el cliente.")

    customers = list_customers()
    return next(c for c in customers if c.id == customer_id)


def set_customer_active(customer_id: int, is_active: bool) -> CustomerRecord:
    """Archiva (is_active=False) o reactiva (True) un cliente. Soft, conserva histórico."""
    with psycopg.connect(_database_dsn(), row_factory=dict_row) as conn:
        _ensure_motor_tables(conn)
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE customers
                SET is_active = %s, updated_at = NOW()
                WHERE id = %s
                RETURNING id;
                """,
                (is_active, customer_id),
            )
            if cur.fetchone() is None:
                raise ValueError("El cliente no existe.")
        conn.commit()

    customers = list_customers()
    return next(c for c in customers if c.id == customer_id)


def update_customer_database(
    database_id: int, payload: CustomerDatabaseUpdateRequest
) -> CustomerDatabaseRecord:
    normalized_database_name = payload.database_name.strip()
    normalized_username = payload.username.strip()
    normalized_connection_type = _normalize_connection_type(payload.connection_type)
    normalized_access_url = (payload.access_url or "").strip() or None
    if not uses_access_url(normalized_connection_type):
        normalized_access_url = None

    with psycopg.connect(_database_dsn(), row_factory=dict_row) as conn:
        _ensure_motor_tables(conn)
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, provider_config, password
                FROM customer_databases
                WHERE id = %s;
                """,
                (database_id,),
            )
            existing_row = cur.fetchone()
            if existing_row is None:
                raise ValueError("La database no existe.")

        effective_password = payload.password.strip() if payload.password is not None else str(
            existing_row.get("password") or ""
        ).strip()
        normalized_provider_config = normalize_provider_config(
            normalized_connection_type,
            payload.provider_config,
            username=normalized_username,
            password=effective_password,
            existing_provider_config=existing_row.get("provider_config"),
        )

        try:
            with conn.cursor() as cur:
                if payload.password is not None:
                    cur.execute(
                        """
                        UPDATE customer_databases
                        SET database_name = %s,
                            username = %s,
                            password = %s,
                            connection_type = %s,
                            access_url = %s,
                            provider_config = %s,
                            updated_at = NOW()
                        WHERE id = %s
                        RETURNING id, customer_id, database_name, username,
                                  (password IS NOT NULL AND password <> '') AS has_password,
                                  connection_type, access_url, provider_config, created_at, updated_at;
                        """,
                        (
                            normalized_database_name,
                            normalized_username,
                            effective_password,
                            normalized_connection_type,
                            normalized_access_url,
                            Jsonb(normalized_provider_config),
                            database_id,
                        ),
                    )
                else:
                    cur.execute(
                        """
                        UPDATE customer_databases
                        SET database_name = %s,
                            username = %s,
                            connection_type = %s,
                            access_url = %s,
                            provider_config = %s,
                            updated_at = NOW()
                        WHERE id = %s
                        RETURNING id, customer_id, database_name, username,
                                  (password IS NOT NULL AND password <> '') AS has_password,
                                  connection_type, access_url, provider_config, created_at, updated_at;
                        """,
                        (
                            normalized_database_name,
                            normalized_username,
                            normalized_connection_type,
                            normalized_access_url,
                            Jsonb(normalized_provider_config),
                            database_id,
                        ),
                    )
                row = cur.fetchone()
            if row is not None:
                _sync_primary_credential(conn, database_id, normalized_username, effective_password)
            conn.commit()
        except UniqueViolation:
            conn.rollback()
            raise ValueError(
                "Ya existe esa combinacion de database y usuario para el cliente."
            ) from None

    if row is None:
        raise RuntimeError("No se pudo actualizar la database.")
    rules = _list_rules_for_database(database_id)
    rule_groups = _list_rule_groups_for_database(database_id)
    result_payload = dict(row)
    result_payload["provider_config"] = public_provider_config(
        str(result_payload.get("connection_type") or "database"),
        result_payload.get("provider_config"),
    )
    return CustomerDatabaseRecord(**result_payload, rules=rules, rule_groups=rule_groups)


# ── Pool de credenciales por database ──────────────────────────────


_CREDENTIAL_COLUMNS = """
    id,
    customer_database_id,
    username,
    label,
    is_active,
    last_used_at,
    last_auth_error_at,
    created_at,
    updated_at
"""


def _sync_primary_credential(
    conn: psycopg.Connection, database_id: int, username: str, password: str
) -> None:
    """Mantiene la credencial legacy de customer_databases dentro del pool."""
    normalized_username = (username or "").strip()
    normalized_password = (password or "").strip()
    if not normalized_username or not normalized_password:
        return
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO customer_database_credentials (customer_database_id, username, password)
            VALUES (%s, %s, %s)
            ON CONFLICT (customer_database_id, username)
            DO UPDATE SET
                password = EXCLUDED.password,
                is_active = TRUE,
                updated_at = NOW();
            """,
            (database_id, normalized_username, encrypt_secret(normalized_password)),
        )


def list_database_credentials(database_id: int) -> list[CustomerDatabaseCredentialRecord]:
    with psycopg.connect(_database_dsn(), row_factory=dict_row) as conn:
        _ensure_motor_tables(conn)
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM customer_databases WHERE id = %s;", (database_id,))
            if cur.fetchone() is None:
                raise ValueError("La database no existe.")
            cur.execute(
                f"""
                SELECT {_CREDENTIAL_COLUMNS}
                FROM customer_database_credentials
                WHERE customer_database_id = %s
                ORDER BY is_active DESC, username ASC;
                """,
                (database_id,),
            )
            rows = cur.fetchall()
    return [CustomerDatabaseCredentialRecord(**row) for row in rows]


def create_database_credential(
    database_id: int, payload: CustomerDatabaseCredentialCreateRequest
) -> CustomerDatabaseCredentialRecord:
    normalized_username = payload.username.strip()
    normalized_password = payload.password.strip()
    if not normalized_username or not normalized_password:
        raise ValueError("Usuario y contraseña son obligatorios.")

    with psycopg.connect(_database_dsn(), row_factory=dict_row) as conn:
        _ensure_motor_tables(conn)
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM customer_databases WHERE id = %s;", (database_id,))
            if cur.fetchone() is None:
                raise ValueError("La database no existe.")
        try:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    INSERT INTO customer_database_credentials (
                        customer_database_id, username, password, label, is_active
                    )
                    VALUES (%s, %s, %s, %s, %s)
                    RETURNING {_CREDENTIAL_COLUMNS};
                    """,
                    (
                        database_id,
                        normalized_username,
                        encrypt_secret(normalized_password),
                        _normalize_optional_text(payload.label),
                        bool(payload.is_active),
                    ),
                )
                row = cur.fetchone()
            conn.commit()
        except UniqueViolation:
            conn.rollback()
            raise ValueError("Ya existe una credencial con ese usuario para esta database.") from None

    if row is None:
        raise RuntimeError("No se pudo crear la credencial.")
    return CustomerDatabaseCredentialRecord(**row)


def update_database_credential(
    credential_id: int, payload: CustomerDatabaseCredentialUpdateRequest
) -> CustomerDatabaseCredentialRecord:
    set_clauses: list[str] = []
    params: list[Any] = []

    if payload.username is not None:
        normalized_username = payload.username.strip()
        if not normalized_username:
            raise ValueError("El usuario no puede quedar vacio.")
        set_clauses.append("username = %s")
        params.append(normalized_username)
    if payload.password is not None:
        normalized_password = payload.password.strip()
        if not normalized_password:
            raise ValueError("La contraseña no puede quedar vacia.")
        set_clauses.append("password = %s")
        params.append(encrypt_secret(normalized_password))
    if payload.label is not None:
        set_clauses.append("label = %s")
        params.append(_normalize_optional_text(payload.label))
    if payload.is_active is not None:
        set_clauses.append("is_active = %s")
        params.append(bool(payload.is_active))

    with psycopg.connect(_database_dsn(), row_factory=dict_row) as conn:
        _ensure_motor_tables(conn)
        if not set_clauses:
            with conn.cursor() as cur:
                cur.execute(
                    f"SELECT {_CREDENTIAL_COLUMNS} FROM customer_database_credentials WHERE id = %s;",
                    (credential_id,),
                )
                row = cur.fetchone()
            if row is None:
                raise ValueError("La credencial no existe.")
            return CustomerDatabaseCredentialRecord(**row)

        if payload.is_active is False:
            _ensure_not_last_active_credential(conn, credential_id)

        try:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    UPDATE customer_database_credentials
                    SET {", ".join(set_clauses)}, updated_at = NOW()
                    WHERE id = %s
                    RETURNING {_CREDENTIAL_COLUMNS};
                    """,
                    (*params, credential_id),
                )
                row = cur.fetchone()
            conn.commit()
        except UniqueViolation:
            conn.rollback()
            raise ValueError("Ya existe una credencial con ese usuario para esta database.") from None

    if row is None:
        raise ValueError("La credencial no existe.")
    return CustomerDatabaseCredentialRecord(**row)


def _ensure_not_last_active_credential(conn: psycopg.Connection, credential_id: int) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT customer_database_id, is_active
            FROM customer_database_credentials
            WHERE id = %s;
            """,
            (credential_id,),
        )
        credential_row = cur.fetchone()
        if credential_row is None:
            raise ValueError("La credencial no existe.")
        if not credential_row["is_active"]:
            return
        # El pool abarca la db fisica completa: si otra fila hermana todavia
        # tiene credenciales activas, esta se puede eliminar sin dejar la
        # database sin acceso.
        sibling_ids = _sibling_database_ids(conn, int(credential_row["customer_database_id"]))
        cur.execute(
            """
            SELECT COUNT(*) AS active_count
            FROM customer_database_credentials
            WHERE customer_database_id = ANY(%s)
              AND is_active
              AND id <> %s;
            """,
            (sibling_ids, credential_id),
        )
        remaining = cur.fetchone()
    if int(remaining["active_count"]) == 0:
        raise ValueError(
            "No se puede eliminar o desactivar la ultima credencial activa de la database."
        )


def delete_database_credential(credential_id: int) -> None:
    with psycopg.connect(_database_dsn(), row_factory=dict_row) as conn:
        _ensure_motor_tables(conn)
        _ensure_not_last_active_credential(conn, credential_id)
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM customer_database_credentials WHERE id = %s RETURNING id;",
                (credential_id,),
            )
            row = cur.fetchone()
        conn.commit()
    if row is None:
        raise ValueError("La credencial no existe.")


def report_credential_auth_failure(credential_id: int) -> None:
    with psycopg.connect(_database_dsn(), row_factory=dict_row) as conn:
        _ensure_motor_tables(conn)
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE customer_database_credentials
                SET last_auth_error_at = NOW(), updated_at = NOW()
                WHERE id = %s;
                """,
                (credential_id,),
            )
        conn.commit()


def get_geotab_config_for_database(
    database_id: int, *, exclude_credential_ids: tuple[int, ...] = ()
) -> tuple[GeotabConfig, int | None]:
    """Selecciona la credencial activa menos usada (LRU) de la db fisica.

    El pool abarca todas las filas hermanas (mismo database_name): las
    credenciales de cualquier cliente con esa database sirven y rotarlas
    entre todas reparte mejor la carga sobre Geotab.

    Retorna (config, credential_id). credential_id es None cuando se uso la
    credencial legacy de customer_databases (pool vacio o agotado).
    """
    with psycopg.connect(_database_dsn(), row_factory=dict_row) as conn:
        _ensure_motor_tables(conn)
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, database_name, username, password, connection_type
                FROM customer_databases
                WHERE id = %s;
                """,
                (database_id,),
            )
            db_row = cur.fetchone()
            if db_row is None:
                raise ValueError("La database no existe.")
            if db_row["connection_type"] != "geotab":
                raise ValueError("La database no es de tipo Geotab.")

        sibling_ids = _sibling_database_ids(conn, database_id)
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE customer_database_credentials
                SET last_used_at = NOW(), updated_at = NOW()
                WHERE id = (
                    SELECT id
                    FROM customer_database_credentials
                    WHERE customer_database_id = ANY(%s)
                      AND is_active
                      AND NOT (id = ANY(%s))
                    ORDER BY last_used_at ASC NULLS FIRST, id ASC
                    FOR UPDATE SKIP LOCKED
                    LIMIT 1
                )
                RETURNING id, username, password;
                """,
                (sibling_ids, list(exclude_credential_ids)),
            )
            credential_row = cur.fetchone()
        conn.commit()

    database_name = str(db_row["database_name"]).strip()
    if credential_row is not None:
        return (
            GeotabConfig(
                username=str(credential_row["username"]).strip(),
                password=str(decrypt_secret(credential_row["password"])).strip(),
                database=database_name,
            ),
            int(credential_row["id"]),
        )

    legacy_password = str(db_row.get("password") or "").strip()
    legacy_username = str(db_row.get("username") or "").strip()
    if not legacy_username or not legacy_password:
        raise ValueError("La database Geotab no tiene credenciales configuradas.")
    return (
        GeotabConfig(username=legacy_username, password=legacy_password, database=database_name),
        None,
    )


def call_with_geotab_credentials(database_id: int, fn, *, max_attempts: int = 3):
    """Ejecuta fn(cfg) rotando credenciales del pool ante errores de autenticacion.

    Los errores que no son de autenticacion se propagan de inmediato.
    """
    from app.clients.geotab_client import _invalidate_session, _is_auth_error

    excluded: list[int] = []
    last_exc: Exception | None = None
    for _ in range(max_attempts):
        cfg, credential_id = get_geotab_config_for_database(
            database_id, exclude_credential_ids=tuple(excluded)
        )
        try:
            return fn(cfg)
        except Exception as exc:
            if not _is_auth_error(exc):
                raise
            last_exc = exc
            _invalidate_session(cfg.username, cfg.database)
            if credential_id is None:
                raise
            _logger.warning(
                "Credencial %s fallo autenticacion en db %s; rotando a la siguiente.",
                credential_id,
                cfg.database,
            )
            report_credential_auth_failure(credential_id)
            excluded.append(credential_id)
    raise last_exc  # type: ignore[misc]


def _sibling_database_ids(conn: psycopg.Connection, database_id: int) -> list[int]:
    """IDs de todas las filas que apuntan a la misma db fisica de Geotab.

    El nombre de database es unico globalmente en MyGeotab, asi que dos filas
    con el mismo database_name (sin importar cliente ni credencial) son la
    misma db: comparten reglas, grupos y pool de credenciales.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT s.id
            FROM customer_databases s
            INNER JOIN customer_databases origin
                ON origin.id = %s
            WHERE LOWER(s.database_name) = LOWER(origin.database_name)
              AND s.connection_type = 'geotab'
              AND origin.connection_type = 'geotab';
            """,
            (database_id,),
        )
        rows = cur.fetchall()
    return [int(r["id"]) for r in rows] if rows else [database_id]


def get_geotab_database_scope(database_id: int) -> tuple[int, ...]:
    """IDs locales que representan la misma database fisica de Geotab."""
    with psycopg.connect(_database_dsn(), row_factory=dict_row) as conn:
        _ensure_motor_tables(conn)
        with conn.cursor() as cur:
            cur.execute(
                "SELECT connection_type FROM customer_databases WHERE id = %s;",
                (database_id,),
            )
            row = cur.fetchone()
        if row is None:
            raise ValueError("La database no existe.")
        if row["connection_type"] != "geotab":
            raise ValueError("La database no es de tipo Geotab.")
        return tuple(_sibling_database_ids(conn, database_id))


def list_geotab_taller_sync_targets() -> list[dict[str, Any]]:
    """Una fila por database fisica Geotab habilitada para el scheduler."""
    with psycopg.connect(_database_dsn(), row_factory=dict_row) as conn:
        _ensure_motor_tables(conn)
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT DISTINCT ON (LOWER(cd.database_name))
                       cd.id, cd.database_name
                FROM customer_databases cd
                INNER JOIN customers c ON c.id = cd.customer_id
                WHERE cd.connection_type = 'geotab'
                  AND c.is_active
                ORDER BY LOWER(cd.database_name), cd.id;
                """
            )
            return list(cur.fetchall())


def _list_rules_for_database(database_id: int) -> list[GeotabRuleRecord]:
    with psycopg.connect(_database_dsn(), row_factory=dict_row) as conn:
        _ensure_motor_tables(conn)
        sibling_ids = _sibling_database_ids(conn, database_id)
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, database_id, name, rule_id, category, created_at FROM geotab_rules WHERE database_id = ANY(%s) ORDER BY name ASC;",
                (sibling_ids,),
            )
            rule_rows = cur.fetchall()
            rule_record_ids = [int(row["id"]) for row in rule_rows]
            application_rows: list[dict[str, Any]] = []
            if rule_record_ids:
                cur.execute(
                    """
                    SELECT
                        gra.id,
                        gra.geotab_rule_id,
                        gra.category,
                        gra.motor_id,
                        mc.engine_name AS motor_name,
                        mc.technical_number,
                        gra.event_type,
                        gra.created_at
                    FROM geotab_rule_applications gra
                    LEFT JOIN motor_catalog mc
                        ON mc.id = gra.motor_id
                    WHERE gra.geotab_rule_id = ANY(%s)
                    ORDER BY gra.geotab_rule_id ASC, gra.category ASC, mc.engine_name ASC;
                    """,
                    (rule_record_ids,),
                )
                application_rows = cur.fetchall()

            seen: set[str] = set()
            results: list[GeotabRuleRecord] = []
            for record in _build_rule_records(rule_rows, application_rows):
                if record.rule_id not in seen:
                    seen.add(record.rule_id)
                    results.append(record)
            return results


def _list_rule_groups_for_database(database_id: int) -> list[GeotabRuleGroupRecord]:
    with psycopg.connect(_database_dsn(), row_factory=dict_row) as conn:
        _ensure_motor_tables(conn)
        sibling_ids = _sibling_database_ids(conn, database_id)
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    grg.id,
                    grg.database_id,
                    grg.motor_id,
                    mc.engine_name AS motor_name,
                    mc.technical_number,
                    grg.name,
                    grg.match_mode,
                    grg.created_at,
                    grg.updated_at
                FROM geotab_rule_groups grg
                INNER JOIN motor_catalog mc
                    ON mc.id = grg.motor_id
                WHERE grg.database_id = ANY(%s)
                ORDER BY mc.engine_name ASC, grg.name ASC;
                """,
                (sibling_ids,),
            )
            group_rows = cur.fetchall()

            group_ids = [int(row["id"]) for row in group_rows]
            if not group_ids:
                return []

            cur.execute(
                """
                SELECT
                    grgr.group_id,
                    gr.id AS rule_record_id,
                    gr.name,
                    gr.rule_id
                FROM geotab_rule_group_rules grgr
                INNER JOIN geotab_rules gr
                    ON gr.id = grgr.geotab_rule_id
                WHERE grgr.group_id = ANY(%s)
                ORDER BY grgr.group_id ASC, gr.name ASC;
                """,
                (group_ids,),
            )
            group_rule_rows = cur.fetchall()

    groups_by_database = _build_rule_group_records(group_rows, group_rule_rows)
    merged: list[GeotabRuleGroupRecord] = []
    for db_id in sibling_ids:
        merged.extend(groups_by_database.get(db_id, []))
    return _merge_rule_group_records_by_motor_identity(merged)


def _get_rule_group_record(group_id: int) -> GeotabRuleGroupRecord:
    with psycopg.connect(_database_dsn(), row_factory=dict_row) as conn:
        _ensure_motor_tables(conn)
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    grg.id,
                    grg.database_id,
                    grg.motor_id,
                    mc.engine_name AS motor_name,
                    mc.technical_number,
                    grg.name,
                    grg.match_mode,
                    grg.created_at,
                    grg.updated_at
                FROM geotab_rule_groups grg
                INNER JOIN motor_catalog mc
                    ON mc.id = grg.motor_id
                WHERE grg.id = %s;
                """,
                (group_id,),
            )
            group_row = cur.fetchone()

            if group_row is None:
                raise ValueError("El grupo de reglas no existe.")

            cur.execute(
                """
                SELECT
                    grgr.group_id,
                    gr.id AS rule_record_id,
                    gr.name,
                    gr.rule_id
                FROM geotab_rule_group_rules grgr
                INNER JOIN geotab_rules gr
                    ON gr.id = grgr.geotab_rule_id
                WHERE grgr.group_id = %s
                ORDER BY gr.name ASC;
                """,
                (group_id,),
            )
            group_rule_rows = cur.fetchall()

    groups_by_database = _build_rule_group_records([group_row], group_rule_rows)
    return groups_by_database[int(group_row["database_id"])][0]


def _get_geotab_database_config(database_id: int) -> GeotabConfig:
    """Config Geotab de la database usando el pool de credenciales (LRU)."""
    cfg, _credential_id = get_geotab_config_for_database(database_id)
    return cfg


def resolve_geotab_rule(database_id: int, rule_id: str) -> GeotabRuleInspection:
    normalized_rule_id = rule_id.strip()
    if not normalized_rule_id:
        raise ValueError("El ID de la regla es obligatorio.")

    try:
        inspection = call_with_geotab_credentials(
            database_id,
            lambda cfg: build_rule_inspection(cfg, normalized_rule_id),
        )
    except ValueError:
        raise
    except Exception as exc:
        _logger.warning(
            "No se pudo resolver la regla %s en Geotab (db=%s)",
            normalized_rule_id,
            database_id,
            exc_info=True,
        )
        raise RuntimeError("No fue posible consultar la regla en Geotab.") from exc

    return GeotabRuleInspection(**inspection)


def create_geotab_rule(
    database_id: int, payload: GeotabRuleCreateRequest
) -> GeotabRuleRecord:
    normalized_rule_id = payload.rule_id.strip()
    normalized_category = _normalize_rule_category(payload.category)
    normalized_event_type = _normalize_rule_event_type(payload.event_type)
    requested_motor_id = int(payload.motor_id) if payload.motor_id is not None else None
    if normalized_event_type == "exceso_rpm" and requested_motor_id is None:
        raise ValueError("Las reglas de exceso de RPM deben asociarse a un motor.")

    inspection = resolve_geotab_rule(database_id, normalized_rule_id)
    if not inspection.exists or not inspection.name:
        raise ValueError("La regla no existe en Geotab.")

    with psycopg.connect(_database_dsn(), row_factory=dict_row) as conn:
        _ensure_motor_tables(conn)
        # La regla pertenece a la db fisica: si ya esta registrada bajo otra
        # fila de la misma database (otro cliente), no se vuelve a registrar.
        sibling_ids = _sibling_database_ids(conn, database_id)
        canonical_motor_id = requested_motor_id
        with conn.cursor() as cur:
            if requested_motor_id is not None:
                cur.execute(
                    """
                    SELECT id, engine_name, technical_number
                    FROM motor_catalog
                    WHERE id = %s;
                    """,
                    (requested_motor_id,),
                )
                motor_row = cur.fetchone()
                if motor_row is None:
                    raise ValueError("El motor seleccionado no existe.")
                cur.execute(
                    """
                    SELECT id
                    FROM motor_catalog
                    WHERE LOWER(engine_name) = LOWER(%s)
                      AND technical_number = %s
                    ORDER BY id ASC
                    LIMIT 1;
                    """,
                    (motor_row["engine_name"], motor_row["technical_number"]),
                )
                canonical_row = cur.fetchone()
                canonical_motor_id = int(canonical_row["id"]) if canonical_row else requested_motor_id

            cur.execute(
                """
                SELECT id
                FROM geotab_rules
                WHERE database_id = ANY(%s)
                  AND rule_id = %s
                LIMIT 1;
                """,
                (sibling_ids, normalized_rule_id),
            )
            existing_rule = cur.fetchone()
        try:
            with conn.cursor() as cur:
                if existing_rule is None:
                    cur.execute(
                        """
                        INSERT INTO geotab_rules (database_id, name, rule_id, category)
                        VALUES (%s, %s, %s, %s)
                        RETURNING id;
                        """,
                        (database_id, inspection.name, normalized_rule_id, normalized_category),
                    )
                    inserted_rule = cur.fetchone()
                    if inserted_rule is None:
                        raise RuntimeError("No se pudo crear la regla.")
                    rule_record_id = int(inserted_rule["id"])
                else:
                    rule_record_id = int(existing_rule["id"])

                cur.execute(
                    """
                    INSERT INTO geotab_rule_applications (
                        geotab_rule_id,
                        category,
                        motor_id,
                        event_type
                    )
                    VALUES (%s, %s, %s, %s)
                    ON CONFLICT DO NOTHING;
                    """,
                    (
                        rule_record_id,
                        normalized_category,
                        canonical_motor_id,
                        normalized_event_type,
                    ),
                )
                if (
                    normalized_category == "habito_seguro"
                    and normalized_event_type == "exceso_rpm"
                    and canonical_motor_id is not None
                ):
                    cur.execute(
                        """
                        DELETE FROM geotab_rule_group_rules grgr
                        USING geotab_rule_groups grg, motor_catalog mc
                        WHERE grgr.group_id = grg.id
                          AND mc.id = grg.motor_id
                          AND grgr.geotab_rule_id = %s
                          AND (LOWER(mc.engine_name), mc.technical_number) = (
                              SELECT LOWER(engine_name), technical_number
                              FROM motor_catalog
                              WHERE id = %s
                          );
                        """,
                        (rule_record_id, canonical_motor_id),
                    )
                    cur.execute(
                        """
                        DELETE FROM geotab_rule_applications gra
                        WHERE gra.geotab_rule_id = %s
                          AND gra.category = 'operacion'
                          AND gra.event_type IS NULL
                          AND (
                              gra.motor_id IS NULL
                              OR gra.motor_id IN (
                                  SELECT same_motor.id
                                  FROM motor_catalog same_motor
                                  WHERE (
                                      LOWER(same_motor.engine_name),
                                      same_motor.technical_number
                                  ) = (
                                      SELECT LOWER(engine_name), technical_number
                                      FROM motor_catalog
                                      WHERE id = %s
                                  )
                              )
                          );
                        """,
                        (rule_record_id, canonical_motor_id),
                    )
                    cur.execute(
                        """
                        DELETE FROM geotab_rule_groups grg
                        WHERE NOT EXISTS (
                            SELECT 1
                            FROM geotab_rule_group_rules grgr
                            WHERE grgr.group_id = grg.id
                        );
                        """
                    )
            conn.commit()
        except UniqueViolation:
            conn.rollback()
            raise ValueError("Ya existe una regla con ese ID para esta database.") from None

    for record in _list_rules_for_database(database_id):
        if record.rule_id == normalized_rule_id:
            return record
    raise RuntimeError("No se pudo crear la regla.")


def create_geotab_rule_group(
    database_id: int, payload: GeotabRuleGroupCreateRequest
) -> GeotabRuleGroupRecord:
    normalized_match_mode = _normalize_match_mode(payload.match_mode)
    normalized_name = (payload.name or "").strip()

    unique_rule_record_ids: list[int] = []
    seen_rule_record_ids: set[int] = set()
    for raw_rule_record_id in payload.rule_record_ids:
        rule_record_id = int(raw_rule_record_id)
        if rule_record_id <= 0:
            continue
        if rule_record_id in seen_rule_record_ids:
            continue
        unique_rule_record_ids.append(rule_record_id)
        seen_rule_record_ids.add(rule_record_id)

    if not unique_rule_record_ids:
        raise ValueError("Debes seleccionar al menos una regla para el grupo.")

    with psycopg.connect(_database_dsn(), row_factory=dict_row) as conn:
        _ensure_motor_tables(conn)
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, connection_type
                FROM customer_databases
                WHERE id = %s;
                """,
                (database_id,),
            )
            database_row = cur.fetchone()
            if database_row is None:
                raise ValueError("La database no existe.")
            if database_row["connection_type"] != "geotab":
                raise ValueError("Solo se pueden crear grupos en databases de tipo Geotab.")

            cur.execute(
                """
                SELECT id, engine_name, technical_number
                FROM motor_catalog
                WHERE id = %s;
                """,
                (payload.motor_id,),
            )
            motor_row = cur.fetchone()
            if motor_row is None:
                raise ValueError("El motor seleccionado no existe.")
            cur.execute(
                """
                SELECT id
                FROM motor_catalog
                WHERE LOWER(engine_name) = LOWER(%s)
                  AND technical_number = %s
                ORDER BY id ASC
                LIMIT 1;
                """,
                (motor_row["engine_name"], motor_row["technical_number"]),
            )
            canonical_motor_row = cur.fetchone()
            canonical_motor_id = (
                int(canonical_motor_row["id"]) if canonical_motor_row else int(payload.motor_id)
            )

            sibling_ids_for_rules = _sibling_database_ids(conn, database_id)
            cur.execute(
                """
                SELECT id, name, rule_id, category
                FROM geotab_rules
                WHERE database_id = ANY(%s)
                  AND id = ANY(%s)
                ORDER BY name ASC;
                """,
                (sibling_ids_for_rules, unique_rule_record_ids),
            )
            rule_rows = cur.fetchall()
            if len(rule_rows) != len(unique_rule_record_ids):
                raise ValueError("Todas las reglas del grupo deben pertenecer a esa database.")

            non_operation_rules = [
                str(row["name"]) for row in rule_rows if row.get("category") != "operacion"
            ]
            if non_operation_rules:
                raise ValueError(
                    "Los grupos de motor solo aceptan reglas de operacion. "
                    f"Reglas de habito seguro: {', '.join(non_operation_rules)}."
                )

            cur.execute(
                """
                SELECT
                    grr.geotab_rule_id,
                    gr.name AS group_name,
                    mc.engine_name AS motor_name,
                    mc.technical_number
                FROM geotab_rule_group_rules grr
                JOIN geotab_rule_groups gr ON gr.id = grr.group_id
                JOIN motor_catalog mc ON mc.id = gr.motor_id
                WHERE grr.geotab_rule_id = ANY(%s);
                """,
                (unique_rule_record_ids,),
            )
            already_assigned = cur.fetchall()
            conflicting_assignments = [
                row
                for row in already_assigned
                if str(row["technical_number"]) != str(motor_row["technical_number"])
                or str(row["motor_name"]).lower() != str(motor_row["engine_name"]).lower()
            ]
            if conflicting_assignments:
                names = ", ".join(
                    row["group_name"] for row in conflicting_assignments
                )
                raise ValueError(
                    f"Algunas reglas ya estan asignadas a otro grupo: {names}. "
                    "Una regla solo puede pertenecer a un motor."
                )

            group_name = normalized_name or str(motor_row["engine_name"]).strip()

            sibling_ids = _sibling_database_ids(conn, database_id)
            cur.execute(
                """
                SELECT grg.id
                FROM geotab_rule_groups grg
                INNER JOIN motor_catalog mc
                    ON mc.id = grg.motor_id
                WHERE LOWER(mc.engine_name) = LOWER(%s)
                  AND mc.technical_number = %s
                  AND grg.database_id = ANY(%s)
                ORDER BY grg.created_at ASC;
                """,
                (motor_row["engine_name"], motor_row["technical_number"], sibling_ids),
            )
            existing_groups = cur.fetchall()

        with conn.cursor() as cur:
            if existing_groups:
                group_id = int(existing_groups[0]["id"])
                cur.execute(
                    """
                    UPDATE geotab_rule_groups
                    SET motor_id = %s, match_mode = %s, updated_at = NOW()
                    WHERE id = %s;
                    """,
                    (canonical_motor_id, normalized_match_mode, group_id),
                )
                # Consolidate duplicate groups for same motor into the first one
                for duplicate in existing_groups[1:]:
                    dup_id = int(duplicate["id"])
                    cur.execute(
                        """
                        UPDATE geotab_rule_group_rules
                        SET group_id = %s
                        WHERE group_id = %s
                          AND geotab_rule_id NOT IN (
                              SELECT geotab_rule_id FROM geotab_rule_group_rules WHERE group_id = %s
                          );
                        """,
                        (group_id, dup_id, group_id),
                    )
                    cur.execute("DELETE FROM geotab_rule_groups WHERE id = %s;", (dup_id,))
            else:
                cur.execute(
                    """
                    INSERT INTO geotab_rule_groups (database_id, motor_id, name, match_mode)
                    VALUES (%s, %s, %s, %s)
                    RETURNING id;
                    """,
                    (database_id, canonical_motor_id, group_name, normalized_match_mode),
                )
                inserted = cur.fetchone()
                if inserted is None:
                    raise RuntimeError("No se pudo crear el grupo de reglas.")
                group_id = int(inserted["id"])

            for rule_record_id in unique_rule_record_ids:
                cur.execute(
                    """
                    DELETE FROM geotab_rule_applications
                    WHERE geotab_rule_id = %s
                      AND category = 'operacion'
                      AND motor_id IS NULL
                      AND event_type IS NULL;
                    """,
                    (rule_record_id,),
                )
                cur.execute(
                    """
                    INSERT INTO geotab_rule_applications (
                        geotab_rule_id,
                        category,
                        motor_id,
                        event_type
                    )
                    VALUES (%s, 'operacion', %s, NULL)
                    ON CONFLICT DO NOTHING;
                    """,
                    (rule_record_id, canonical_motor_id),
                )
                cur.execute(
                    """
                    INSERT INTO geotab_rule_group_rules (group_id, geotab_rule_id)
                    VALUES (%s, %s)
                    ON CONFLICT DO NOTHING;
                    """,
                    (group_id, rule_record_id),
                )
        conn.commit()

    return _get_rule_group_record(group_id)


def inspect_geotab_rule_record(rule_record_id: int) -> GeotabRuleInspection:
    with psycopg.connect(_database_dsn(), row_factory=dict_row) as conn:
        _ensure_motor_tables(conn)
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    gr.id,
                    gr.name,
                    gr.rule_id,
                    gr.database_id,
                    cd.database_name,
                    cd.connection_type
                FROM geotab_rules gr
                INNER JOIN customer_databases cd
                    ON cd.id = gr.database_id
                WHERE gr.id = %s;
                """,
                (rule_record_id,),
            )
            row = cur.fetchone()

    if row is None:
        raise ValueError("La regla no existe.")
    if row["connection_type"] != "geotab":
        raise ValueError("La regla no pertenece a una database Geotab.")

    try:
        inspection = call_with_geotab_credentials(
            int(row["database_id"]),
            lambda cfg: build_rule_inspection(
                cfg,
                str(row["rule_id"]).strip(),
                fallback_name=str(row["name"]).strip(),
            ),
        )
        if not inspection.get("exists") and not inspection.get("message"):
            inspection["message"] = "La regla ya no existe en Geotab; se muestra el nombre guardado."
        return GeotabRuleInspection(**inspection)
    except Exception:
        _logger.warning(
            "No se pudo inspeccionar la regla %s en Geotab (db=%s)",
            row["rule_id"],
            row["database_name"],
            exc_info=True,
        )
        return GeotabRuleInspection(
            exists=False,
            rule_id=str(row["rule_id"]).strip(),
            name=str(row["name"]).strip(),
            status="Inexistente",
            type=None,
            groups_count=0,
            comment=None,
            headline="No se pudo validar la regla en Geotab.",
            facts=[],
            tree=None,
            raw_condition=None,
            message="Se muestra el nombre guardado como referencia mientras se recupera la conexion.",
        )


def delete_geotab_rule(rule_id: int) -> None:
    with psycopg.connect(_database_dsn(), row_factory=dict_row) as conn:
        _ensure_motor_tables(conn)
        with conn.cursor() as cur:
            cur.execute("DELETE FROM geotab_rules WHERE id = %s RETURNING id;", (rule_id,))
            row = cur.fetchone()
            if row is not None:
                cur.execute(
                    """
                    DELETE FROM geotab_rule_groups grg
                    WHERE NOT EXISTS (
                        SELECT 1
                        FROM geotab_rule_group_rules grgr
                        WHERE grgr.group_id = grg.id
                    );
                    """
                )
        conn.commit()
    if row is None:
        raise ValueError("La regla no existe.")


def delete_geotab_rule_group(group_id: int) -> None:
    with psycopg.connect(_database_dsn(), row_factory=dict_row) as conn:
        _ensure_motor_tables(conn)
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT motor_id
                FROM geotab_rule_groups
                WHERE id = %s;
                """,
                (group_id,),
            )
            group_row = cur.fetchone()
            if group_row is None:
                row = None
            else:
                cur.execute(
                    """
                    SELECT geotab_rule_id
                    FROM geotab_rule_group_rules
                    WHERE group_id = %s;
                    """,
                    (group_id,),
                )
                rule_ids = [int(rule_row["geotab_rule_id"]) for rule_row in cur.fetchall()]
                if rule_ids:
                    cur.execute(
                        """
                        DELETE FROM geotab_rule_applications
                        WHERE geotab_rule_id = ANY(%s)
                          AND category = 'operacion'
                          AND motor_id = %s
                          AND event_type IS NULL;
                        """,
                        (rule_ids, int(group_row["motor_id"])),
                    )
                    cur.execute(
                        """
                        INSERT INTO geotab_rule_applications (
                            geotab_rule_id,
                            category,
                            motor_id,
                            event_type
                        )
                        SELECT gr.id, 'operacion', NULL, NULL
                        FROM geotab_rules gr
                        WHERE gr.id = ANY(%s)
                          AND gr.category = 'operacion'
                          AND NOT EXISTS (
                              SELECT 1
                              FROM geotab_rule_applications gra
                              WHERE gra.geotab_rule_id = gr.id
                                AND gra.category = 'operacion'
                          )
                        ON CONFLICT DO NOTHING;
                        """,
                        (rule_ids,),
                    )
            cur.execute(
                "DELETE FROM geotab_rule_groups WHERE id = %s RETURNING id;",
                (group_id,),
            )
            row = cur.fetchone()
        conn.commit()

    if row is None:
        raise ValueError("El grupo de reglas no existe.")


def _get_or_create_customer(
    conn: psycopg.Connection, customer_name: str
) -> int:
    normalized_customer_name = customer_name.strip()
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO customers (name)
            VALUES (%s)
            ON CONFLICT (name)
            DO UPDATE SET
                updated_at = NOW()
            RETURNING id;
            """,
            (normalized_customer_name,),
        )
        row = cur.fetchone()

    if row is None:
        raise RuntimeError("No se pudo resolver el cliente.")
    return int(row["id"])


def _get_or_create_customer_database(
    conn: psycopg.Connection,
    customer_id: int,
    payload: CustomerDatabaseCreateRequest,
) -> int:
    normalized_password = payload.password.strip()
    normalized_connection_type = _normalize_connection_type(payload.connection_type)
    normalized_access_url = (payload.access_url or "").strip() or None
    if not uses_access_url(normalized_connection_type):
        normalized_access_url = None
    normalized_provider_config = normalize_provider_config(
        normalized_connection_type,
        payload.provider_config,
        username=payload.username.strip(),
        password=normalized_password,
    )
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO customer_databases (
                customer_id,
                database_name,
                username,
                password,
                connection_type,
                access_url,
                provider_config
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (customer_id, database_name, username)
            DO UPDATE SET
                password = EXCLUDED.password,
                connection_type = EXCLUDED.connection_type,
                access_url = EXCLUDED.access_url,
                provider_config = EXCLUDED.provider_config,
                updated_at = NOW()
            RETURNING id;
            """,
            (
                customer_id,
                payload.database_name.strip(),
                payload.username.strip(),
                normalized_password,
                normalized_connection_type,
                normalized_access_url,
                Jsonb(normalized_provider_config),
            ),
        )
        row = cur.fetchone()

    if row is None:
        raise RuntimeError("No se pudo resolver la database del cliente.")
    _sync_primary_credential(conn, int(row["id"]), payload.username.strip(), normalized_password)
    return int(row["id"])


def _validate_vehicle_in_customer_geotab(
    plate: str,
    vin: str | None,
    database_id: int,
    plate_prefix: str | None = None,
) -> tuple[str, str | None]:
    """Valida el vehiculo en la db Geotab del cliente usando el pool de credenciales.

    Retorna (status, geotab_device_id). El device id es el codigo interno de
    Geotab del vehiculo EN ESA database (los ids cambian entre databases).
    """
    from app.clients.geotab_client import get_cached_device_from_plate, get_cached_device_from_vin

    def _check(cfg: GeotabConfig) -> tuple[str, str | None]:
        device = None
        if plate:
            device = get_cached_device_from_plate(plate, cfg, plate_prefix=plate_prefix)
        if device is None and vin:
            device = get_cached_device_from_vin(vin, cfg)
        if device is not None:
            device_id = str(device.get("id") or "").strip() or None
            return "found", device_id
        return "not_found", None

    try:
        return call_with_geotab_credentials(database_id, _check)
    except Exception:
        _logger.warning(
            "No se pudo validar el vehiculo %s en Geotab del cliente (db=%s)",
            plate,
            database_id,
            exc_info=True,
        )
        return "unknown", None


def _update_geotab_customer_status(
    plate: str,
    geotab_customer_status: str,
    geotab_customer_database_id: int | None,
    geotab_device_id: str | None = None,
) -> None:
    normalized_plate = plate.strip().upper()
    with psycopg.connect(_database_dsn(), row_factory=dict_row) as conn:
        _ensure_motor_tables(conn)
        with conn.cursor() as cur:
            if geotab_customer_status == "found":
                cur.execute(
                    """
                    UPDATE vehicle_motor_assignments
                    SET
                        geotab_customer_status = %s,
                        geotab_customer_database_id = %s,
                        geotab_device_id = COALESCE(%s::text, geotab_device_id),
                        geotab_device_synced_at = CASE WHEN %s::text IS NOT NULL THEN NOW() ELSE geotab_device_synced_at END,
                        updated_at = NOW()
                    WHERE plate = %s;
                    """,
                    (
                        geotab_customer_status,
                        geotab_customer_database_id,
                        geotab_device_id,
                        geotab_device_id,
                        normalized_plate,
                    ),
                )
            else:
                # not_found / unknown / not_applicable: limpiar el device id para
                # no dejar ids huerfanos de una db anterior (salvo unknown, que
                # conserva el ultimo valor conocido).
                clear_device = geotab_customer_status in ("not_found", "not_applicable")
                cur.execute(
                    """
                    UPDATE vehicle_motor_assignments
                    SET
                        geotab_customer_status = %s,
                        geotab_customer_database_id = %s,
                        geotab_device_id = CASE WHEN %s::boolean THEN NULL ELSE geotab_device_id END,
                        geotab_device_synced_at = CASE WHEN %s::boolean THEN NULL ELSE geotab_device_synced_at END,
                        updated_at = NOW()
                    WHERE plate = %s;
                    """,
                    (
                        geotab_customer_status,
                        geotab_customer_database_id,
                        clear_device,
                        clear_device,
                        normalized_plate,
                    ),
                )
        conn.commit()


def _validate_and_store_customer_geotab(
    plate: str,
    vin: str | None,
    customer_database_id: int,
    plate_prefix: str | None = None,
) -> None:
    status, device_id = _validate_vehicle_in_customer_geotab(
        plate, vin, customer_database_id, plate_prefix=plate_prefix
    )
    _update_geotab_customer_status(plate, status, customer_database_id, device_id)


def revalidate_vehicle_customer_geotab(plate: str) -> dict[str, Any]:
    normalized_plate = plate.strip().upper()
    if not normalized_plate:
        raise ValueError("La placa es obligatoria.")

    with psycopg.connect(_database_dsn(), row_factory=dict_row) as conn:
        _ensure_motor_tables(conn)
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    a.plate,
                    a.vin,
                    a.customer_database_id,
                    cd.database_name,
                    cd.username,
                    cd.password,
                    cd.connection_type,
                    cd.provider_config
                FROM vehicle_motor_assignments a
                LEFT JOIN customer_databases cd
                    ON cd.id = a.customer_database_id
                WHERE a.plate = %s;
                """,
                (normalized_plate,),
            )
            row = cur.fetchone()

    if row is None:
        raise ValueError("El vehiculo no existe en la base de asociaciones.")

    if not row.get("customer_database_id"):
        return {
            "geotab_customer_status": "not_applicable",
            "geotab_customer_database_id": None,
            "message": "El vehiculo no tiene database asignada.",
        }

    if row.get("connection_type") != "geotab":
        _update_geotab_customer_status(normalized_plate, "not_applicable", None)
        return {
            "geotab_customer_status": "not_applicable",
            "geotab_customer_database_id": None,
            "message": "La database asignada no es de tipo Geotab.",
        }

    prov_cfg = row.get("provider_config") or {}
    if isinstance(prov_cfg, str):
        import json as _json
        prov_cfg = _json.loads(prov_cfg)
    db_id = int(row["customer_database_id"])
    status, device_id = _validate_vehicle_in_customer_geotab(
        normalized_plate,
        row.get("vin"),
        db_id,
        plate_prefix=prov_cfg.get("plate_prefix"),
    )
    _update_geotab_customer_status(normalized_plate, status, db_id, device_id)
    return {
        "geotab_customer_status": status,
        "geotab_customer_database_id": db_id,
        "geotab_device_id": device_id,
        "message": f"Validacion Geotab cliente completada: {status}.",
    }


def backfill_geotab_device_ids() -> dict[str, Any]:
    """Rellena geotab_device_id para vehiculos ya validados en la db del cliente.

    Recorre cada database Geotab una sola vez (1 llamada de devices por db,
    cacheada) y matchea los vehiculos asignados por placa/VIN.
    """
    from app.clients.geotab_client import _find_device_in_collection, get_cached_devices

    with psycopg.connect(_database_dsn(), row_factory=dict_row) as conn:
        _ensure_motor_tables(conn)
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    a.plate,
                    a.vin,
                    a.geotab_customer_database_id,
                    cd.database_name,
                    cd.username,
                    cd.password,
                    cd.provider_config
                FROM vehicle_motor_assignments a
                INNER JOIN customer_databases cd
                    ON cd.id = a.geotab_customer_database_id
                WHERE a.geotab_customer_database_id IS NOT NULL
                  AND cd.connection_type = 'geotab'
                ORDER BY a.geotab_customer_database_id ASC, a.plate ASC;
                """
            )
            vehicles = cur.fetchall()

    summary = {"total": len(vehicles), "updated": 0, "not_found": 0, "errors": 0}
    devices_by_db: dict[int, list[dict[str, Any]] | None] = {}

    for vehicle in vehicles:
        db_id = int(vehicle["geotab_customer_database_id"])
        if db_id not in devices_by_db:
            try:
                devices_by_db[db_id] = get_cached_devices(
                    str(vehicle["username"]).strip(),
                    str(vehicle["password"]).strip(),
                    str(vehicle["database_name"]).strip(),
                )
            except Exception:
                _logger.warning(
                    "Backfill: no se pudo listar devices de la db %s", db_id, exc_info=True
                )
                devices_by_db[db_id] = None

        devices = devices_by_db[db_id]
        if devices is None:
            summary["errors"] += 1
            continue

        prov_cfg = vehicle.get("provider_config") or {}
        if isinstance(prov_cfg, str):
            import json as _json
            prov_cfg = _json.loads(prov_cfg)

        device = _find_device_in_collection(
            devices,
            plate=vehicle["plate"],
            vin=vehicle.get("vin"),
            plate_prefix=prov_cfg.get("plate_prefix"),
        )
        if device is None:
            summary["not_found"] += 1
            _update_geotab_customer_status(vehicle["plate"], "not_found", db_id)
            continue

        device_id = str(device.get("id") or "").strip() or None
        _update_geotab_customer_status(vehicle["plate"], "found", db_id, device_id)
        summary["updated"] += 1

    return summary


def assign_vehicle_database(
    plate: str, payload: VehicleDatabaseAssignmentRequest
) -> AssignedDatabaseSummary:
    normalized_plate = plate.strip().upper()
    if not normalized_plate:
        raise ValueError("La placa es obligatoria.")

    normalized_access_url = _normalize_optional_text(payload.access_url)

    with psycopg.connect(_database_dsn(), row_factory=dict_row) as conn:
        _ensure_motor_tables(conn)
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT plate
                FROM vehicle_motor_assignments
                WHERE plate = %s;
                """,
                (normalized_plate,),
            )
            existing_vehicle = cur.fetchone()

        if existing_vehicle is None:
            raise ValueError("El vehiculo no existe en la base de asociaciones.")

        if not payload.customer_database_id:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE vehicle_motor_assignments
                    SET access_url = %s,
                        updated_at = NOW()
                    WHERE plate = %s;
                    """,
                    (normalized_access_url, normalized_plate),
                )
            # Sin database: ignoramos provider_vehicle_id (el binding requiere customer_database_id).
            conn.commit()
            return AssignedDatabaseSummary()

        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    cd.id,
                    cd.customer_id,
                    c.name AS client_name,
                    cd.database_name,
                    cd.username AS database_username,
                    cd.access_url,
                    cd.password,
                    cd.connection_type,
                    cd.provider_config,
                    (cd.password IS NOT NULL AND cd.password <> '') AS has_database_password
                FROM customer_databases cd
                INNER JOIN customers c
                    ON c.id = cd.customer_id
                WHERE cd.id = %s;
                """,
                (payload.customer_database_id,),
            )
            selected_database = cur.fetchone()

            if selected_database is None:
                raise ValueError("La database seleccionada no existe.")

            cur.execute(
                """
                SELECT vin FROM vehicle_motor_assignments WHERE plate = %s;
                """,
                (normalized_plate,),
            )
            vehicle_row = cur.fetchone()
            vehicle_vin = vehicle_row["vin"] if vehicle_row else None

            is_geotab_db = selected_database.get("connection_type") == "geotab"

            cur.execute(
                """
                UPDATE vehicle_motor_assignments
                SET
                    customer_id = %s,
                    customer_database_id = %s,
                    access_url = %s,
                    geotab_customer_status = CASE WHEN %s THEN 'unknown' ELSE 'not_applicable' END,
                    geotab_customer_database_id = CASE WHEN %s THEN %s ELSE NULL END,
                    updated_at = NOW()
                WHERE plate = %s;
                """,
                (
                    selected_database["customer_id"],
                    selected_database["id"],
                    selected_database["access_url"],
                    is_geotab_db,
                    is_geotab_db,
                    selected_database["id"],
                    normalized_plate,
                ),
            )

            from app.services.rendimientos import _ensure_performance_tables

            _ensure_performance_tables(conn)

            provider_key = infer_provider_key(
                connection_type=selected_database.get("connection_type"),
                database_name=selected_database.get("database_name"),
                access_url=selected_database.get("access_url"),
                provider_config=None,
            )

            raw_id = (payload.provider_vehicle_id or "").strip() if payload.provider_vehicle_id is not None else None

            if payload.provider_vehicle_id is None:
                pass
            elif raw_id == "":
                cur.execute(
                    """
                    DELETE FROM vehicle_provider_bindings
                    WHERE plate = %s
                      AND customer_database_id = %s
                      AND provider = %s
                      AND is_manual = TRUE;
                    """,
                    (normalized_plate, int(selected_database["id"]), provider_key),
                )
            else:
                cur.execute(
                    """
                    INSERT INTO vehicle_provider_bindings (
                        plate, customer_database_id, provider,
                        provider_vehicle_id, binding_status,
                        last_resolved_at, is_manual, updated_at
                    )
                    VALUES (%s, %s, %s, %s, 'resolved', NOW(), TRUE, NOW())
                    ON CONFLICT (plate, customer_database_id, provider)
                    DO UPDATE SET
                        provider_vehicle_id = EXCLUDED.provider_vehicle_id,
                        binding_status = 'resolved',
                        is_manual = TRUE,
                        last_resolved_at = NOW(),
                        updated_at = NOW();
                    """,
                    (normalized_plate, int(selected_database["id"]), provider_key, raw_id),
                )
        conn.commit()

    result = AssignedDatabaseSummary(
        client_name=selected_database["client_name"],
        database_name=selected_database["database_name"],
        database_username=selected_database["database_username"],
        has_database_password=selected_database["has_database_password"],
    )

    if is_geotab_db:
        db_prov_cfg = selected_database.get("provider_config") or {}
        if isinstance(db_prov_cfg, str):
            import json as _json
            db_prov_cfg = _json.loads(db_prov_cfg)
        try:
            _validate_and_store_customer_geotab(
                normalized_plate,
                vehicle_vin,
                int(selected_database["id"]),
                plate_prefix=db_prov_cfg.get("plate_prefix"),
            )
        except Exception:
            _logger.warning(
                "Fallo la validacion automatica de Geotab cliente para %s",
                normalized_plate,
                exc_info=True,
            )

    return result


def get_dashboard_summary() -> DashboardSummary:
    with psycopg.connect(_database_dsn(), row_factory=dict_row) as conn:
        _ensure_motor_tables(conn)
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    (SELECT COUNT(*) FROM motor_catalog) AS motors_count,
                    (SELECT COUNT(*) FROM vehicle_motor_assignments) AS vehicles_count,
                    (SELECT COUNT(*) FROM vehicle_motor_assignments
                     WHERE technical_number NOT IN (SELECT technical_number FROM motor_catalog)
                    ) AS vehicles_without_motor,
                    (SELECT COUNT(*) FROM customers) AS customers_count,
                    (SELECT COUNT(*) FROM customer_databases) AS databases_count;
                """
            )
            counts = cur.fetchone()

            cur.execute(
                """
                SELECT id, engine_name, technical_number, created_at
                FROM motor_catalog
                ORDER BY created_at DESC
                LIMIT 5;
                """
            )
            recent_motors = [RecentMotorRecord(**row) for row in cur.fetchall()]

            cur.execute(
                """
                SELECT
                    v.plate,
                    c.name AS client_name,
                    mc.engine_name,
                    v.updated_at
                FROM vehicle_motor_assignments v
                LEFT JOIN customers c ON c.id = v.customer_id
                LEFT JOIN motor_catalog mc ON mc.technical_number = v.technical_number
                ORDER BY v.updated_at DESC
                LIMIT 5;
                """
            )
            recent_vehicles = [RecentVehicleRecord(**row) for row in cur.fetchall()]

    return DashboardSummary(
        motors_count=counts["motors_count"],
        vehicles_count=counts["vehicles_count"],
        vehicles_without_motor=counts["vehicles_without_motor"],
        customers_count=counts["customers_count"],
        databases_count=counts["databases_count"],
        recent_motors=recent_motors,
        recent_vehicles=recent_vehicles,
    )


def check_all_geotab_connections() -> dict[str, Any]:
    from app.clients.geotab_client import (
        _device_matches_plate,
        _get_all_devices,
        _parse_geotab_datetime,
        _search_entities,
        build_client,
    )
    from app.core.config import load_geotab_config

    now = datetime.now(timezone.utc)
    recently_threshold = timedelta(hours=24)

    with psycopg.connect(_database_dsn(), row_factory=dict_row) as conn:
        _ensure_motor_tables(conn)
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    a.plate,
                    a.customer_database_id,
                    cd.database_name,
                    cd.username,
                    cd.password,
                    cd.connection_type,
                    cd.provider_config
                FROM vehicle_motor_assignments a
                LEFT JOIN customer_databases cd
                    ON cd.id = a.customer_database_id;
                """
            )
            vehicles = cur.fetchall()

    counters = {
        "connected": 0,
        "disconnected": 0,
        "not_found": 0,
        "not_applicable": 0,
        "errors": 0,
    }
    results_by_plate: dict[str, dict[str, Any]] = {}

    main_geotab_cfg: GeotabConfig | None = None
    try:
        main_geotab_cfg = load_geotab_config()
    except Exception:
        _logger.warning("No fue posible cargar la configuracion de Geotab global", exc_info=True)

    snapshot_cache: dict[tuple[str, str, str], tuple[list[dict], dict[str, dict]] | None] = {}

    def _snapshot_for(cfg: GeotabConfig) -> tuple[list[dict], dict[str, dict]] | None:
        key = (cfg.database, cfg.username, cfg.password)
        if key in snapshot_cache:
            return snapshot_cache[key]
        try:
            client = build_client(cfg)
            devices = _get_all_devices(client)
            status_rows = _search_entities(client, "DeviceStatusInfo", None)
        except Exception:
            _logger.warning(
                "No fue posible obtener Devices/DeviceStatusInfo de Geotab (db=%s, user=%s)",
                cfg.database,
                cfg.username,
                exc_info=True,
            )
            snapshot_cache[key] = None
            return None
        status_by_device_id: dict[str, dict] = {}
        for status in status_rows:
            device_ref = status.get("device")
            if isinstance(device_ref, dict):
                device_id = device_ref.get("id")
                if device_id:
                    status_by_device_id[str(device_id)] = status
        snapshot_cache[key] = (devices, status_by_device_id)
        return snapshot_cache[key]

    for vehicle in vehicles:
        plate = str(vehicle["plate"]).strip().upper()
        connection_type = vehicle["connection_type"]
        customer_db_id = vehicle["customer_database_id"]
        prov_config = vehicle["provider_config"] if vehicle["provider_config"] else {}
        if isinstance(prov_config, str):
            import json as _json
            prov_config = _json.loads(prov_config)
        plate_prefix = prov_config.get("plate_prefix")

        if customer_db_id and connection_type != "geotab":
            counters["not_applicable"] += 1
            results_by_plate[plate] = {"status": "not_applicable"}
            continue

        if customer_db_id and connection_type == "geotab":
            password = str(vehicle["password"] or "").strip()
            username = str(vehicle["username"] or "").strip()
            database_name = str(vehicle["database_name"] or "").strip()
            if not password or not username or not database_name:
                counters["errors"] += 1
                results_by_plate[plate] = {"status": "error"}
                continue
            cfg = GeotabConfig(username=username, password=password, database=database_name)
        else:
            if main_geotab_cfg is None:
                counters["errors"] += 1
                results_by_plate[plate] = {"status": "error"}
                continue
            cfg = main_geotab_cfg

        snapshot = _snapshot_for(cfg)
        if snapshot is None:
            counters["errors"] += 1
            results_by_plate[plate] = {"status": "error"}
            continue

        devices, status_by_device_id = snapshot

        device = next((d for d in devices if _device_matches_plate(d, plate, plate_prefix=plate_prefix)), None)
        if device is None:
            counters["not_found"] += 1
            results_by_plate[plate] = {"status": "not_found"}
            continue

        device_id = device.get("id")
        status_info = status_by_device_id.get(str(device_id)) if device_id else None
        last_comm_raw = (status_info or {}).get("dateTime")

        if last_comm_raw:
            try:
                last_comm = _parse_geotab_datetime(last_comm_raw)
            except Exception:
                counters["errors"] += 1
                results_by_plate[plate] = {"status": "error"}
                _logger.warning("No fue posible parsear dateTime para %s", plate, exc_info=True)
                continue
            is_recent = (now - last_comm) <= recently_threshold
        else:
            is_recent = bool((status_info or {}).get("isDeviceCommunicating"))

        if is_recent:
            counters["connected"] += 1
            results_by_plate[plate] = {"status": "connected"}
        else:
            counters["disconnected"] += 1
            results_by_plate[plate] = {"status": "disconnected"}

    return {
        "total": len(vehicles),
        **counters,
        "results": results_by_plate,
    }


def save_connection_snapshot() -> dict[str, Any]:
    """Run connection check and persist results to vehicle_connection_log."""
    from datetime import timedelta, timezone as tz

    col_tz = tz(timedelta(hours=-5))
    today_col = datetime.now(col_tz).date()

    result = check_all_geotab_connections()
    results_by_plate: dict[str, dict] = result.get("results", {})

    with psycopg.connect(_database_dsn(), row_factory=dict_row) as conn:
        _ensure_motor_tables(conn)
        with conn.cursor() as cur:
            for plate, info in results_by_plate.items():
                if info["status"] == "not_applicable":
                    continue
                cur.execute(
                    """
                    INSERT INTO vehicle_connection_log (plate, check_date, status)
                    VALUES (%s, %s, %s)
                    ON CONFLICT (plate, check_date)
                    DO UPDATE SET status = EXCLUDED.status;
                    """,
                    (plate, today_col, info["status"]),
                )
        conn.commit()

    return result


def get_connection_stats(month: str) -> list[dict[str, Any]]:
    """Return per-vehicle connection stats for a given month (YYYY-MM)."""
    month_start = f"{month}-01"

    with psycopg.connect(_database_dsn(), row_factory=dict_row) as conn:
        _ensure_motor_tables(conn)
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    plate,
                    COUNT(*) FILTER (WHERE status IN ('connected', 'disconnected')) AS days_checked,
                    COUNT(*) FILTER (WHERE status = 'connected') AS days_connected,
                    COUNT(*) FILTER (WHERE status = 'disconnected') AS days_disconnected,
                    COUNT(*) FILTER (WHERE status = 'not_found') AS days_not_found,
                    COUNT(*) FILTER (WHERE status = 'error') AS days_error
                FROM vehicle_connection_log
                WHERE check_date >= %s::date
                  AND check_date < (%s::date + INTERVAL '1 month')
                GROUP BY plate;
                """,
                (month_start, month_start),
            )
            agg_rows = cur.fetchall()

            cur.execute(
                """
                SELECT DISTINCT ON (plate) plate, check_date, status
                FROM vehicle_connection_log
                WHERE check_date >= %s::date
                  AND check_date < (%s::date + INTERVAL '1 month')
                ORDER BY plate, check_date DESC;
                """,
                (month_start, month_start),
            )
            latest_by_plate = {row["plate"]: row for row in cur.fetchall()}

            streak_plates = [
                p for p, row in latest_by_plate.items() if row["status"] == "disconnected"
            ]
            streaks: dict[str, int] = {}
            for plate in streak_plates:
                cur.execute(
                    """
                    SELECT status
                    FROM vehicle_connection_log
                    WHERE plate = %s
                      AND check_date >= %s::date
                      AND check_date < (%s::date + INTERVAL '1 month')
                    ORDER BY check_date DESC;
                    """,
                    (plate, month_start, month_start),
                )
                count = 0
                for row in cur.fetchall():
                    if row["status"] == "disconnected":
                        count += 1
                    else:
                        break
                streaks[plate] = count

    stats = []
    for row in agg_rows:
        plate = row["plate"]
        days_checked = row["days_checked"]
        days_connected = row["days_connected"]
        days_disconnected = row["days_disconnected"]
        pct = round(days_connected / days_checked * 100, 1) if days_checked > 0 else 0
        stats.append({
            "plate": plate,
            "days_checked": days_checked,
            "days_connected": days_connected,
            "days_disconnected": days_disconnected,
            "days_not_found": row["days_not_found"],
            "days_error": row["days_error"],
            "connection_pct": pct,
            "consecutive_disconnected": streaks.get(plate, 0),
            "latest_status": latest_by_plate.get(plate, {}).get("status"),
            "latest_check_date": latest_by_plate.get(plate, {}).get("check_date"),
        })

    return stats


def get_connection_calendar(
    plate: str, month_from: str, month_to: str
) -> dict[str, Any]:
    """Return per-day connection status for a plate across an inclusive month range.

    month_from / month_to son 'YYYY-MM'. Devuelve una fila por dia registrado en
    vehicle_connection_log. Usa el indice UNIQUE(plate, check_date) para el rango.
    """
    if month_from > month_to:
        month_from, month_to = month_to, month_from
    start = f"{month_from}-01"
    end = f"{month_to}-01"  # exclusivo: +1 mes en el WHERE

    with psycopg.connect(_database_dsn(), row_factory=dict_row) as conn:
        _ensure_motor_tables(conn)
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT check_date, status
                FROM vehicle_connection_log
                WHERE plate = %s
                  AND check_date >= %s::date
                  AND check_date < (%s::date + INTERVAL '1 month')
                ORDER BY check_date;
                """,
                (plate, start, end),
            )
            days = [
                {"date": row["check_date"].isoformat(), "status": row["status"]}
                for row in cur.fetchall()
            ]

    return {"plate": plate, "days": days}
