from __future__ import annotations

import os
from typing import Any

import psycopg
from psycopg.errors import UniqueViolation
from psycopg.rows import dict_row

from app.schemas.vehicle import (
    MotorCatalogRecord,
    MotorCatalogUpsertRequest,
    RegisteredMotorSummary,
    VehicleAssignmentRecord,
)


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


def _normalize_motor_payload(payload: MotorCatalogUpsertRequest) -> dict[str, Any]:
    return {
        "technical_number": payload.technical_number.strip(),
        "engine_name": payload.engine_name.strip(),
    }


def _ensure_motor_tables(conn: psycopg.Connection) -> None:
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
            CREATE TABLE IF NOT EXISTS vehicle_motor_assignments (
                plate VARCHAR(10) PRIMARY KEY,
                vin TEXT NULL,
                engine_number TEXT NULL,
                technical_number TEXT NOT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                last_seen_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
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

    return [MotorCatalogRecord(**row) for row in rows]


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


def list_vehicle_assignments(search: str | None = None) -> list[VehicleAssignmentRecord]:
    params: list[Any] = []
    where_clause = ""

    if search:
        normalized_search = f"%{search.strip().upper()}%"
        where_clause = """
            WHERE UPPER(a.plate) LIKE %s
               OR UPPER(COALESCE(a.vin, '')) LIKE %s
               OR UPPER(a.technical_number) LIKE %s
               OR UPPER(COALESCE(m.engine_name, '')) LIKE %s
        """
        params.extend([normalized_search, normalized_search, normalized_search, normalized_search])

    with psycopg.connect(_database_dsn(), row_factory=dict_row) as conn:
        _ensure_motor_tables(conn)
        with conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT
                    a.plate,
                    a.vin,
                    a.engine_number,
                    a.technical_number,
                    m.engine_name,
                    a.created_at,
                    a.updated_at,
                    a.last_seen_at
                FROM vehicle_motor_assignments a
                LEFT JOIN motor_catalog m
                    ON m.technical_number = a.technical_number
                {where_clause}
                ORDER BY a.last_seen_at DESC, a.plate ASC;
                """,
                params,
            )
            rows = cur.fetchall()

    return [VehicleAssignmentRecord(**row) for row in rows]


def register_vehicle_assignment(
    plate: str,
    technical_number: str,
    vin: str | None = None,
    engine_number: str | None = None,
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
                    engine_number,
                    technical_number
                )
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (plate)
                DO UPDATE SET
                    vin = EXCLUDED.vin,
                    engine_number = EXCLUDED.engine_number,
                    technical_number = EXCLUDED.technical_number,
                    updated_at = NOW(),
                    last_seen_at = NOW();
                """,
                (
                    normalized_plate,
                    _normalize_optional_text(vin),
                    _normalize_optional_text(engine_number),
                    normalized_technical_number,
                ),
            )
        conn.commit()
