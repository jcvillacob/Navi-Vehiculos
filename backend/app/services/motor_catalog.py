from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import psycopg
from psycopg.errors import UniqueViolation
from psycopg.rows import dict_row

from app.schemas.vehicle import (
    AssignedDatabaseSummary,
    CustomerCreateRequest,
    CustomerDatabaseCreateRequest,
    CustomerDatabaseRecord,
    CustomerDatabaseUpdateRequest,
    CustomerRecord,
    CustomerUpdateRequest,
    MotorAttachmentRecord,
    MotorCatalogRecord,
    MotorCatalogUpsertRequest,
    RegisteredMotorSummary,
    VehicleDatabaseAssignmentRequest,
    VehicleAssignmentRecord,
)
from app.services.storage import (
    delete_file,
    download_file,
    upload_file,
)

import logging

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


def _normalize_motor_payload(payload: MotorCatalogUpsertRequest) -> dict[str, Any]:
    return {
        "technical_number": payload.technical_number.strip(),
        "engine_name": payload.engine_name.strip(),
    }


def _attachment_download_url(attachment_id: int) -> str:
    return f"/api/v1/motors/attachments/{attachment_id}/download"


def _build_attachment_record(row: dict[str, Any]) -> MotorAttachmentRecord:
    payload = dict(row)
    payload["download_url"] = _attachment_download_url(int(payload["id"]))
    return MotorAttachmentRecord(**payload)


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
                connection_type TEXT NOT NULL DEFAULT 'database',
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
               OR UPPER(COALESCE(a.cpl, '')) LIKE %s
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
                    a.geotab_status,
                    a.geotab_customer_status,
                    a.geotab_customer_database_id,
                    a.engine_number,
                    a.technical_number,
                    a.cpl,
                    m.engine_name,
                    c.name AS client_name,
                    cd.database_name,
                    cd.username AS database_username,
                    cd.connection_type AS database_connection_type,
                    (cd.password IS NOT NULL AND cd.password <> '') AS has_database_password,
                    COALESCE(a.access_url, cd.access_url) AS access_url,
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
        attachments_by_technical_number = _list_attachments_by_technical_numbers(
            conn, [str(row["technical_number"]) for row in rows]
        )

    records: list[VehicleAssignmentRecord] = []
    for row in rows:
        row_cpl = _normalize_cpl(row.get("cpl"))
        candidate_attachments = attachments_by_technical_number.get(str(row["technical_number"]), [])
        matching_attachments = [
            attachment for attachment in candidate_attachments if attachment.cpl == row_cpl
        ]
        records.append(
            VehicleAssignmentRecord(
                **row,
                attachments=matching_attachments,
            )
        )
    return records


def register_vehicle_assignment(
    plate: str,
    technical_number: str,
    cpl: str | None = None,
    geotab_status: str = "unknown",
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
                    geotab_status,
                    engine_number,
                    technical_number,
                    cpl
                )
                VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT (plate)
                DO UPDATE SET
                    vin = EXCLUDED.vin,
                    geotab_status = EXCLUDED.geotab_status,
                    engine_number = EXCLUDED.engine_number,
                    technical_number = EXCLUDED.technical_number,
                    cpl = EXCLUDED.cpl,
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
                    connection_type,
                    access_url,
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
    normalized_connection_type = (payload.connection_type or "database").strip().lower()
    if normalized_connection_type not in ("database", "geotab"):
        normalized_connection_type = "database"
    normalized_access_url = (payload.access_url or "").strip() or None
    if normalized_connection_type == "geotab":
        normalized_access_url = None

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
                        access_url
                    )
                    VALUES (%s, %s, %s, %s, %s, %s)
                    RETURNING
                        id,
                        customer_id,
                        database_name,
                        username,
                        (password IS NOT NULL AND password <> '') AS has_password,
                        connection_type,
                        access_url,
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


def update_customer(customer_id: int, payload: CustomerUpdateRequest) -> CustomerRecord:
    normalized_name = payload.name.strip()
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
                    SET name = %s, updated_at = NOW()
                    WHERE id = %s
                    RETURNING id, name, created_at, updated_at;
                    """,
                    (normalized_name, customer_id),
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


def update_customer_database(
    database_id: int, payload: CustomerDatabaseUpdateRequest
) -> CustomerDatabaseRecord:
    normalized_database_name = payload.database_name.strip()
    normalized_username = payload.username.strip()
    normalized_connection_type = (payload.connection_type or "database").strip().lower()
    if normalized_connection_type not in ("database", "geotab"):
        normalized_connection_type = "database"
    normalized_access_url = (payload.access_url or "").strip() or None
    if normalized_connection_type == "geotab":
        normalized_access_url = None

    with psycopg.connect(_database_dsn(), row_factory=dict_row) as conn:
        _ensure_motor_tables(conn)
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM customer_databases WHERE id = %s;", (database_id,))
            if cur.fetchone() is None:
                raise ValueError("La database no existe.")

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
                            updated_at = NOW()
                        WHERE id = %s
                        RETURNING id, customer_id, database_name, username,
                                  (password IS NOT NULL AND password <> '') AS has_password,
                                  connection_type, access_url, created_at, updated_at;
                        """,
                        (
                            normalized_database_name,
                            normalized_username,
                            payload.password.strip(),
                            normalized_connection_type,
                            normalized_access_url,
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
                            updated_at = NOW()
                        WHERE id = %s
                        RETURNING id, customer_id, database_name, username,
                                  (password IS NOT NULL AND password <> '') AS has_password,
                                  connection_type, access_url, created_at, updated_at;
                        """,
                        (
                            normalized_database_name,
                            normalized_username,
                            normalized_connection_type,
                            normalized_access_url,
                            database_id,
                        ),
                    )
                row = cur.fetchone()
            conn.commit()
        except UniqueViolation:
            conn.rollback()
            raise ValueError(
                "Ya existe esa combinacion de database y usuario para el cliente."
            ) from None

    if row is None:
        raise RuntimeError("No se pudo actualizar la database.")
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
    normalized_connection_type = (payload.connection_type or "database").strip().lower()
    if normalized_connection_type not in ("database", "geotab"):
        normalized_connection_type = "database"
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO customer_databases (
                customer_id,
                database_name,
                username,
                password,
                connection_type
            )
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (customer_id, database_name, username)
            DO UPDATE SET
                password = EXCLUDED.password,
                connection_type = EXCLUDED.connection_type,
                updated_at = NOW()
            RETURNING id;
            """,
            (
                customer_id,
                payload.database_name.strip(),
                payload.username.strip(),
                normalized_password,
                normalized_connection_type,
            ),
        )
        row = cur.fetchone()

    if row is None:
        raise RuntimeError("No se pudo resolver la database del cliente.")
    return int(row["id"])


def _validate_vehicle_in_customer_geotab(
    plate: str,
    vin: str | None,
    database_name: str,
    username: str,
    password: str,
) -> str:
    try:
        from app.clients.geotab_client import get_device_from_plate, get_device_from_vin
        from app.core.config import GeotabConfig

        customer_geotab_cfg = GeotabConfig(
            username=username,
            password=password,
            database=database_name,
        )
        if plate and get_device_from_plate(plate, customer_geotab_cfg):
            return "found"
        if vin and get_device_from_vin(vin, customer_geotab_cfg):
            return "found"
        return "not_found"
    except Exception:
        _logger.warning(
            "No se pudo validar el vehiculo %s en Geotab del cliente (db=%s, user=%s)",
            plate,
            database_name,
            username,
            exc_info=True,
        )
        return "unknown"


def _update_geotab_customer_status(
    plate: str, geotab_customer_status: str, geotab_customer_database_id: int | None
) -> None:
    normalized_plate = plate.strip().upper()
    with psycopg.connect(_database_dsn(), row_factory=dict_row) as conn:
        _ensure_motor_tables(conn)
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE vehicle_motor_assignments
                SET
                    geotab_customer_status = %s,
                    geotab_customer_database_id = %s,
                    updated_at = NOW()
                WHERE plate = %s;
                """,
                (geotab_customer_status, geotab_customer_database_id, normalized_plate),
            )
        conn.commit()


def _validate_and_store_customer_geotab(
    plate: str,
    vin: str | None,
    database_name: str,
    username: str,
    password: str,
    customer_database_id: int,
) -> None:
    status = _validate_vehicle_in_customer_geotab(
        plate, vin, database_name, username, password
    )
    _update_geotab_customer_status(plate, status, customer_database_id)


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
                    cd.connection_type
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

    status = _validate_vehicle_in_customer_geotab(
        normalized_plate,
        row.get("vin"),
        row["database_name"],
        row["username"],
        row["password"],
    )
    db_id = int(row["customer_database_id"])
    _update_geotab_customer_status(normalized_plate, status, db_id)
    return {
        "geotab_customer_status": status,
        "geotab_customer_database_id": db_id,
        "message": f"Validacion Geotab cliente completada: {status}.",
    }


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

        # Si no se selecciona database, solo actualizar access_url
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
        conn.commit()

    result = AssignedDatabaseSummary(
        client_name=selected_database["client_name"],
        database_name=selected_database["database_name"],
        database_username=selected_database["database_username"],
        has_database_password=selected_database["has_database_password"],
    )

    if is_geotab_db:
        try:
            _validate_and_store_customer_geotab(
                normalized_plate,
                vehicle_vin,
                selected_database["database_name"],
                selected_database["database_username"],
                selected_database["password"],
                int(selected_database["id"]),
            )
        except Exception:
            _logger.warning(
                "Fallo la validacion automatica de Geotab cliente para %s",
                normalized_plate,
                exc_info=True,
            )

    return result
