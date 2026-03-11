from __future__ import annotations

import os
from typing import Any

import psycopg
from psycopg.errors import UniqueViolation
from psycopg.rows import dict_row

from app.schemas.vehicle import (
    AssignedDatabaseSummary,
    CustomerCreateRequest,
    CustomerDatabaseCreateRequest,
    CustomerDatabaseRecord,
    CustomerRecord,
    MotorCatalogRecord,
    MotorCatalogUpsertRequest,
    RegisteredMotorSummary,
    VehicleDatabaseAssignmentRequest,
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
            CREATE TABLE IF NOT EXISTS customers (
                id BIGSERIAL PRIMARY KEY,
                name TEXT NOT NULL UNIQUE,
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
                engine_number TEXT NULL,
                technical_number TEXT NOT NULL,
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
               OR UPPER(COALESCE(c.name, '')) LIKE %s
               OR UPPER(COALESCE(cd.database_name, '')) LIKE %s
               OR UPPER(COALESCE(cd.username, '')) LIKE %s
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
            ]
        )

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
                    c.name AS client_name,
                    cd.database_name,
                    cd.username AS database_username,
                    (cd.password IS NOT NULL AND cd.password <> '') AS has_database_password,
                    a.created_at,
                    a.updated_at,
                    a.last_seen_at
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


def list_customers() -> list[CustomerRecord]:
    with psycopg.connect(_database_dsn(), row_factory=dict_row) as conn:
        _ensure_motor_tables(conn)
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    c.id,
                    c.name,
                    COUNT(cd.id)::INT AS database_count,
                    c.created_at,
                    c.updated_at
                FROM customers c
                LEFT JOIN customer_databases cd
                    ON cd.customer_id = c.id
                GROUP BY c.id, c.name, c.created_at, c.updated_at
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
                    created_at,
                    updated_at
                FROM customer_databases
                ORDER BY customer_id ASC, database_name ASC, username ASC;
                """
            )
            database_rows = cur.fetchall()

    databases_by_customer: dict[int, list[CustomerDatabaseRecord]] = {}
    for row in database_rows:
        record = CustomerDatabaseRecord(**row)
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
    with psycopg.connect(_database_dsn(), row_factory=dict_row) as conn:
        _ensure_motor_tables(conn)
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO customers (name)
                    VALUES (%s)
                    RETURNING id, name, 0::INT AS database_count, created_at, updated_at;
                    """,
                    (normalized_name,),
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
                        password
                    )
                    VALUES (%s, %s, %s, %s)
                    RETURNING
                        id,
                        customer_id,
                        database_name,
                        username,
                        (password IS NOT NULL AND password <> '') AS has_password,
                        created_at,
                        updated_at;
                    """,
                    (
                        customer_id,
                        normalized_database_name,
                        normalized_username,
                        normalized_password,
                    ),
                )
                row = cur.fetchone()
            conn.commit()
        except UniqueViolation:
            conn.rollback()
            raise ValueError("Esa database ya existe para el cliente con ese usuario.") from None

    if row is None:
        raise RuntimeError("No se pudo crear la database del cliente.")
    return CustomerDatabaseRecord(**row)


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
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO customer_databases (
                customer_id,
                database_name,
                username,
                password
            )
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (customer_id, database_name, username)
            DO UPDATE SET
                password = EXCLUDED.password,
                updated_at = NOW()
            RETURNING id;
            """,
            (
                customer_id,
                payload.database_name.strip(),
                payload.username.strip(),
                normalized_password,
            ),
        )
        row = cur.fetchone()

    if row is None:
        raise RuntimeError("No se pudo resolver la database del cliente.")
    return int(row["id"])


def assign_vehicle_database(
    plate: str, payload: VehicleDatabaseAssignmentRequest
) -> AssignedDatabaseSummary:
    normalized_plate = plate.strip().upper()
    if not normalized_plate:
        raise ValueError("La placa es obligatoria.")

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

        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    cd.id,
                    cd.customer_id,
                    c.name AS client_name,
                    cd.database_name,
                    cd.username AS database_username,
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
                UPDATE vehicle_motor_assignments
                SET
                    customer_id = %s,
                    customer_database_id = %s,
                    updated_at = NOW()
                WHERE plate = %s;
                """,
                (
                    selected_database["customer_id"],
                    selected_database["id"],
                    normalized_plate,
                ),
            )
        conn.commit()

    return AssignedDatabaseSummary(**selected_database)
