from __future__ import annotations

from typing import Any

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from app.schemas.cpk_cph import (
    CpkCphInputRow,
    CpkCphPreviewRequest,
    CpkCphPreviewResponse,
    CpkCphPreviewRow,
    CpkCphReportDetail,
    CpkCphReportRow,
    CpkCphReportSaveRequest,
    CpkCphReportSummary,
    CpkCphReportVersion,
    CpkCphRowPatchRequest,
)
from app.schemas.vehicle import CpkCutoffInputRow, CpkCutoffPreviewRequest
from app.services.motor_catalog import _database_dsn
from app.services.rendimientos import preview_cpk_cutoffs


class CpkCphError(Exception):
    pass


class CpkCphNotFound(CpkCphError):
    pass


class CpkCphConflict(CpkCphError):
    pass


_CPK_TABLES_DONE = False


def _ensure_cpk_tables(conn: psycopg.Connection) -> None:
    global _CPK_TABLES_DONE
    if _CPK_TABLES_DONE:
        return
    with conn.cursor() as cur:
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS cpk_cph_reports (
                id BIGSERIAL PRIMARY KEY,
                customer_id BIGINT NOT NULL REFERENCES customers(id) ON DELETE CASCADE,
                period_month TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'draft',
                current_version INTEGER NOT NULL DEFAULT 0,
                created_by BIGINT NULL REFERENCES users(id) ON DELETE SET NULL,
                updated_by BIGINT NULL REFERENCES users(id) ON DELETE SET NULL,
                approved_by BIGINT NULL REFERENCES users(id) ON DELETE SET NULL,
                approved_at TIMESTAMPTZ NULL,
                reopened_from_version INTEGER NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                UNIQUE (customer_id, period_month)
            );
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS cpk_cph_report_versions (
                id BIGSERIAL PRIMARY KEY,
                report_id BIGINT NOT NULL REFERENCES cpk_cph_reports(id) ON DELETE CASCADE,
                version_number INTEGER NOT NULL,
                row_count INTEGER NOT NULL DEFAULT 0,
                created_by BIGINT NULL REFERENCES users(id) ON DELETE SET NULL,
                approved_by BIGINT NULL REFERENCES users(id) ON DELETE SET NULL,
                approved_at TIMESTAMPTZ NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                UNIQUE (report_id, version_number)
            );
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS cpk_cph_report_rows (
                id BIGSERIAL PRIMARY KEY,
                report_id BIGINT NOT NULL REFERENCES cpk_cph_reports(id) ON DELETE CASCADE,
                version_number INTEGER NOT NULL DEFAULT 0,
                plate VARCHAR(32) NOT NULL,
                cutoff_start_at TEXT NOT NULL,
                cutoff_end_at TEXT NOT NULL,
                cutoff_start_utc TEXT NULL,
                cutoff_end_utc TEXT NULL,
                client_name TEXT NULL,
                database_name TEXT NULL,
                source_provider TEXT NULL,
                provider_vehicle_id TEXT NULL,
                km_client DOUBLE PRECISION NULL,
                odo_start DOUBLE PRECISION NULL,
                odo_end DOUBLE PRECISION NULL,
                horo_start DOUBLE PRECISION NULL,
                horo_end DOUBLE PRECISION NULL,
                kms_ecm_geotab DOUBLE PRECISION NULL,
                kms_gps DOUBLE PRECISION NULL,
                hours_ecm DOUBLE PRECISION NULL,
                hours_gps DOUBLE PRECISION NULL,
                fuel_gallons DOUBLE PRECISION NULL,
                km_adjustment DOUBLE PRECISION NULL DEFAULT 0,
                hour_adjustment DOUBLE PRECISION NULL DEFAULT 0,
                kms_ecm_approved DOUBLE PRECISION NULL,
                hours_ecm_approved DOUBLE PRECISION NULL,
                km_difference DOUBLE PRECISION NULL,
                km_difference_pct DOUBLE PRECISION NULL,
                calculation_status TEXT NOT NULL DEFAULT 'pending',
                warnings JSONB NOT NULL DEFAULT '[]'::jsonb,
                correction_note TEXT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
            """
        )
        cur.execute("ALTER TABLE cpk_cph_report_rows ADD COLUMN IF NOT EXISTS km_adjustment DOUBLE PRECISION NULL DEFAULT 0;")
        cur.execute("ALTER TABLE cpk_cph_report_rows ADD COLUMN IF NOT EXISTS hour_adjustment DOUBLE PRECISION NULL DEFAULT 0;")
        cur.execute("ALTER TABLE cpk_cph_report_rows ADD COLUMN IF NOT EXISTS hours_ecm_approved DOUBLE PRECISION NULL;")
        cur.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_cpk_cph_report_rows_report_version
            ON cpk_cph_report_rows (report_id, version_number);
            """
        )
        cur.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_cpk_cph_reports_month_customer
            ON cpk_cph_reports (period_month, customer_id);
            """
        )
    conn.commit()
    _CPK_TABLES_DONE = True


def _normalize_plate(value: str | None) -> str:
    return "".join(ch for ch in str(value or "").strip().upper() if ch.isalnum())


def _customer_name(conn: psycopg.Connection, customer_id: int) -> str:
    with conn.cursor() as cur:
        cur.execute("SELECT name FROM customers WHERE id = %s", (customer_id,))
        row = cur.fetchone()
    if not row:
        raise CpkCphNotFound("Cliente no encontrado.")
    return str(row["name"])


def _num(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _metrics(
    *,
    kms_ecm_approved: float | None,
    km_client: float | None,
    kms_gps: float | None = None,
) -> tuple[float | None, float | None]:
    comparison_km = km_client if km_client is not None else kms_gps
    if kms_ecm_approved is None or comparison_km is None:
        return None, None
    diff = kms_ecm_approved - comparison_km
    pct = (diff / comparison_km * 100.0) if comparison_km else None
    return diff, pct


def _row_from_preview(row: CpkCphPreviewRow | dict[str, Any]) -> dict[str, Any]:
    data = row.model_dump() if hasattr(row, "model_dump") else dict(row)
    km_adjustment = _num(data.get("km_adjustment")) or 0.0
    hour_adjustment = _num(data.get("hour_adjustment")) or 0.0
    kms_approved = _num(data.get("kms_ecm_approved"))
    if kms_approved is None:
        kms_geotab = _num(data.get("kms_ecm_geotab"))
        kms_approved = kms_geotab + km_adjustment if kms_geotab is not None else None
    hours_ecm = _num(data.get("hours_ecm"))
    hours_approved = _num(data.get("hours_ecm_approved"))
    if hours_approved is None:
        hours_approved = hours_ecm + hour_adjustment if hours_ecm is not None else None
    km_client = _num(data.get("km_client"))
    kms_gps = _num(data.get("kms_gps"))
    diff, pct = _metrics(kms_ecm_approved=kms_approved, km_client=km_client, kms_gps=kms_gps)
    return {
        "plate": _normalize_plate(data.get("plate")),
        "cutoff_start_at": str(data.get("cutoff_start_at") or ""),
        "cutoff_end_at": str(data.get("cutoff_end_at") or ""),
        "cutoff_start_utc": data.get("cutoff_start_utc"),
        "cutoff_end_utc": data.get("cutoff_end_utc"),
        "client_name": data.get("client_name"),
        "database_name": data.get("database_name"),
        "source_provider": data.get("source_provider"),
        "provider_vehicle_id": data.get("provider_vehicle_id"),
        "km_client": km_client,
        "odo_start": _num(data.get("odo_start")),
        "odo_end": _num(data.get("odo_end")),
        "horo_start": _num(data.get("horo_start")),
        "horo_end": _num(data.get("horo_end")),
        "kms_ecm_geotab": _num(data.get("kms_ecm_geotab")),
        "kms_gps": kms_gps,
        "hours_ecm": hours_ecm,
        "hours_gps": _num(data.get("hours_gps")),
        "fuel_gallons": _num(data.get("fuel_gallons")),
        "km_adjustment": km_adjustment,
        "hour_adjustment": hour_adjustment,
        "kms_ecm_approved": kms_approved,
        "hours_ecm_approved": hours_approved,
        "km_difference": diff,
        "km_difference_pct": pct,
        "calculation_status": str(data.get("calculation_status") or "pending"),
        "warnings": list(data.get("warnings") or []),
        "correction_note": (str(data.get("correction_note")).strip() if data.get("correction_note") else None),
    }


def _insert_rows(
    conn: psycopg.Connection,
    *,
    report_id: int,
    version_number: int,
    rows: list[CpkCphPreviewRow | dict[str, Any]],
) -> None:
    with conn.cursor() as cur:
        for raw_row in rows:
            row = _row_from_preview(raw_row)
            cur.execute(
                """
                INSERT INTO cpk_cph_report_rows (
                    report_id, version_number, plate, cutoff_start_at, cutoff_end_at,
                    cutoff_start_utc, cutoff_end_utc, client_name, database_name,
                    source_provider, provider_vehicle_id, km_client, odo_start, odo_end,
                    horo_start, horo_end, kms_ecm_geotab, kms_gps, hours_ecm, hours_gps,
                    fuel_gallons, km_adjustment, hour_adjustment, kms_ecm_approved,
                    hours_ecm_approved, km_difference, km_difference_pct,
                    calculation_status, warnings, correction_note
                )
                VALUES (
                    %(report_id)s, %(version_number)s, %(plate)s, %(cutoff_start_at)s, %(cutoff_end_at)s,
                    %(cutoff_start_utc)s, %(cutoff_end_utc)s, %(client_name)s, %(database_name)s,
                    %(source_provider)s, %(provider_vehicle_id)s, %(km_client)s, %(odo_start)s, %(odo_end)s,
                    %(horo_start)s, %(horo_end)s, %(kms_ecm_geotab)s, %(kms_gps)s, %(hours_ecm)s, %(hours_gps)s,
                    %(fuel_gallons)s, %(km_adjustment)s, %(hour_adjustment)s, %(kms_ecm_approved)s,
                    %(hours_ecm_approved)s, %(km_difference)s, %(km_difference_pct)s,
                    %(calculation_status)s, %(warnings)s::jsonb, %(correction_note)s
                );
                """,
                {
                    **row,
                    "report_id": report_id,
                    "version_number": version_number,
                    "warnings": Jsonb(row["warnings"]),
                },
            )


def _summary_from_row(row: dict[str, Any]) -> CpkCphReportSummary:
    return CpkCphReportSummary(
        id=int(row["id"]),
        customer_id=int(row["customer_id"]),
        customer_name=str(row.get("customer_name") or ""),
        period_month=str(row["period_month"]),
        status=str(row["status"]),
        current_version=int(row.get("current_version") or 0),
        row_count=int(row.get("row_count") or 0),
        approved_by=row.get("approved_by"),
        approved_by_username=row.get("approved_by_username"),
        approved_at=row.get("approved_at"),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _row_record(row: dict[str, Any]) -> CpkCphReportRow:
    return CpkCphReportRow(
        id=int(row["id"]),
        report_id=int(row["report_id"]),
        version_number=int(row["version_number"]),
        plate=str(row["plate"]),
        cutoff_start_at=str(row["cutoff_start_at"]),
        cutoff_end_at=str(row["cutoff_end_at"]),
        cutoff_start_utc=row.get("cutoff_start_utc"),
        cutoff_end_utc=row.get("cutoff_end_utc"),
        client_name=row.get("client_name"),
        database_name=row.get("database_name"),
        source_provider=row.get("source_provider"),
        provider_vehicle_id=row.get("provider_vehicle_id"),
        km_client=row.get("km_client"),
        odo_start=row.get("odo_start"),
        odo_end=row.get("odo_end"),
        horo_start=row.get("horo_start"),
        horo_end=row.get("horo_end"),
        kms_ecm_geotab=row.get("kms_ecm_geotab"),
        kms_gps=row.get("kms_gps"),
        hours_ecm=row.get("hours_ecm"),
        hours_gps=row.get("hours_gps"),
        fuel_gallons=row.get("fuel_gallons"),
        km_adjustment=row.get("km_adjustment") or 0,
        hour_adjustment=row.get("hour_adjustment") or 0,
        kms_ecm_approved=row.get("kms_ecm_approved"),
        hours_ecm_approved=row.get("hours_ecm_approved"),
        km_difference=row.get("km_difference"),
        km_difference_pct=row.get("km_difference_pct"),
        calculation_status=str(row.get("calculation_status") or "pending"),
        warnings=list(row.get("warnings") or []),
        correction_note=row.get("correction_note"),
        created_at=row.get("created_at"),
        updated_at=row.get("updated_at"),
    )


def _active_version_for(status: str, current_version: int) -> int:
    return current_version if status == "approved" and current_version > 0 else 0


def _get_report_summary(conn: psycopg.Connection, report_id: int) -> CpkCphReportSummary:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT
                r.*,
                c.name AS customer_name,
                u.username AS approved_by_username,
                (
                    SELECT COUNT(*)
                    FROM cpk_cph_report_rows rr
                    WHERE rr.report_id = r.id
                      AND rr.version_number = CASE
                        WHEN r.status = 'approved' AND r.current_version > 0 THEN r.current_version
                        ELSE 0
                      END
                ) AS row_count
            FROM cpk_cph_reports r
            INNER JOIN customers c ON c.id = r.customer_id
            LEFT JOIN users u ON u.id = r.approved_by
            WHERE r.id = %s;
            """,
            (report_id,),
        )
        row = cur.fetchone()
    if not row:
        raise CpkCphNotFound("Reporte CPK/CPH no encontrado.")
    return _summary_from_row(row)


def list_reports(*, month: str | None = None, customer_id: int | None = None) -> list[CpkCphReportSummary]:
    params: list[Any] = []
    where: list[str] = []
    if month:
        where.append("r.period_month = %s")
        params.append(month)
    if customer_id:
        where.append("r.customer_id = %s")
        params.append(customer_id)
    where_sql = f"WHERE {' AND '.join(where)}" if where else ""
    with psycopg.connect(_database_dsn(), row_factory=dict_row) as conn:
        _ensure_cpk_tables(conn)
        with conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT
                    r.*,
                    c.name AS customer_name,
                    u.username AS approved_by_username,
                    (
                        SELECT COUNT(*)
                        FROM cpk_cph_report_rows rr
                        WHERE rr.report_id = r.id
                          AND rr.version_number = CASE
                            WHEN r.status = 'approved' AND r.current_version > 0 THEN r.current_version
                            ELSE 0
                          END
                    ) AS row_count
                FROM cpk_cph_reports r
                INNER JOIN customers c ON c.id = r.customer_id
                LEFT JOIN users u ON u.id = r.approved_by
                {where_sql}
                ORDER BY r.period_month DESC, c.name ASC;
                """,
                params,
            )
            return [_summary_from_row(row) for row in cur.fetchall()]


def preview_report(payload: CpkCphPreviewRequest) -> CpkCphPreviewResponse:
    with psycopg.connect(_database_dsn(), row_factory=dict_row) as conn:
        _ensure_cpk_tables(conn)
        customer_name = _customer_name(conn, payload.customer_id)

    seen: set[str] = set()
    duplicate_rows: dict[int, CpkCphPreviewRow] = {}
    valid_rows: list[tuple[int, CpkCphInputRow]] = []
    for index, row in enumerate(payload.rows, start=1):
        plate = _normalize_plate(row.plate)
        if plate in seen:
            duplicate_rows[index] = CpkCphPreviewRow(
                row_number=index,
                plate=plate,
                cutoff_start_at=row.cutoff_start_at,
                cutoff_end_at=row.cutoff_end_at,
                km_client=row.km_client,
                calculation_status="duplicate",
                warnings=["Placa duplicada en el pegado."],
            )
            continue
        seen.add(plate)
        valid_rows.append((index, row))

    cutoff_payload = CpkCutoffPreviewRequest(
        month=payload.month,
        client_names=[customer_name],
        rows=[
            CpkCutoffInputRow(
                plate=row.plate,
                cutoff_start_at=row.cutoff_start_at,
                cutoff_end_at=row.cutoff_end_at,
            )
            for _, row in valid_rows
        ],
    )
    cutoff_response = preview_cpk_cutoffs(cutoff_payload)
    rows_by_order = list(cutoff_response.rows)
    out: list[CpkCphPreviewRow] = []
    valid_idx = 0
    for index, input_row in enumerate(payload.rows, start=1):
        if index in duplicate_rows:
            out.append(duplicate_rows[index])
            continue
        base = rows_by_order[valid_idx]
        valid_idx += 1
        km_client = input_row.km_client
        kms_geotab = base.kms_ecm
        kms_approved = kms_geotab
        diff, pct = _metrics(kms_ecm_approved=kms_approved, km_client=km_client, kms_gps=base.kms_gps)
        out.append(
            CpkCphPreviewRow(
                row_number=index,
                plate=base.plate,
                cutoff_start_at=base.cutoff_start_at,
                cutoff_end_at=base.cutoff_end_at,
                cutoff_start_utc=base.cutoff_start_utc,
                cutoff_end_utc=base.cutoff_end_utc,
                client_name=base.client_name,
                database_name=base.database_name,
                source_provider=base.source_provider,
                provider_vehicle_id=base.provider_vehicle_id,
                km_client=km_client,
                odo_start=base.odo_start,
                odo_end=base.odo_end,
                horo_start=base.horo_start,
                horo_end=base.horo_end,
                kms_ecm_geotab=kms_geotab,
                kms_gps=base.kms_gps,
                hours_ecm=base.hours_ecm,
                hours_gps=base.hours_gps,
                fuel_gallons=base.fuel_gallons,
                km_adjustment=0,
                hour_adjustment=0,
                kms_ecm_approved=kms_approved,
                hours_ecm_approved=base.hours_ecm,
                km_difference=diff,
                km_difference_pct=pct,
                calculation_status=base.status,
                warnings=list(base.warnings or []),
            )
        )
    return CpkCphPreviewResponse(month=payload.month, customer_id=payload.customer_id, rows=out)


def save_report(payload: CpkCphReportSaveRequest, *, user_id: int | None) -> CpkCphReportDetail:
    with psycopg.connect(_database_dsn(), row_factory=dict_row) as conn:
        _ensure_cpk_tables(conn)
        _customer_name(conn, payload.customer_id)
        for raw_row in payload.rows:
            row = _row_from_preview(raw_row)
            has_adjustment = bool(row.get("km_adjustment") or row.get("hour_adjustment"))
            if has_adjustment and not row.get("correction_note"):
                raise CpkCphConflict("Cada ajuste de CPK/CPH requiere nota.")
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO cpk_cph_reports (customer_id, period_month, status, created_by, updated_by)
                VALUES (%s, %s, 'draft', %s, %s)
                ON CONFLICT (customer_id, period_month)
                DO UPDATE SET
                    updated_by = EXCLUDED.updated_by,
                    updated_at = NOW(),
                    status = CASE
                        WHEN cpk_cph_reports.status = 'approved' THEN cpk_cph_reports.status
                        ELSE cpk_cph_reports.status
                    END
                RETURNING id, status;
                """,
                (payload.customer_id, payload.month, user_id, user_id),
            )
            report = cur.fetchone()
            report_id = int(report["id"])
            cur.execute(
                "DELETE FROM cpk_cph_report_rows WHERE report_id = %s AND version_number = 0;",
                (report_id,),
            )
            _insert_rows(conn, report_id=report_id, version_number=0, rows=payload.rows)
            cur.execute(
                """
                UPDATE cpk_cph_reports
                SET status = CASE WHEN current_version > 0 THEN 'reopened' ELSE 'draft' END,
                    updated_by = %s,
                    updated_at = NOW()
                WHERE id = %s;
                """,
                (user_id, report_id),
            )
        conn.commit()
        return get_report(report_id, conn=conn)


def _versions(conn: psycopg.Connection, report_id: int) -> list[CpkCphReportVersion]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT
                v.*,
                cu.username AS created_by_username,
                au.username AS approved_by_username
            FROM cpk_cph_report_versions v
            LEFT JOIN users cu ON cu.id = v.created_by
            LEFT JOIN users au ON au.id = v.approved_by
            WHERE v.report_id = %s
            ORDER BY v.version_number DESC;
            """,
            (report_id,),
        )
        rows = cur.fetchall()
    return [
        CpkCphReportVersion(
            id=int(row["id"]),
            report_id=int(row["report_id"]),
            version_number=int(row["version_number"]),
            row_count=int(row.get("row_count") or 0),
            created_by=row.get("created_by"),
            created_by_username=row.get("created_by_username"),
            approved_by=row.get("approved_by"),
            approved_by_username=row.get("approved_by_username"),
            approved_at=row.get("approved_at"),
            created_at=row["created_at"],
        )
        for row in rows
    ]


def get_report(report_id: int, *, conn: psycopg.Connection | None = None) -> CpkCphReportDetail:
    own_conn = conn is None
    active_conn = conn or psycopg.connect(_database_dsn(), row_factory=dict_row)
    try:
        _ensure_cpk_tables(active_conn)
        summary = _get_report_summary(active_conn, report_id)
        visible_version = _active_version_for(summary.status, summary.current_version)
        with active_conn.cursor() as cur:
            cur.execute(
                """
                SELECT *
                FROM cpk_cph_report_rows
                WHERE report_id = %s AND version_number = %s
                ORDER BY plate ASC, id ASC;
                """,
                (report_id, visible_version),
            )
            rows = [_row_record(row) for row in cur.fetchall()]
        return CpkCphReportDetail(
            **summary.model_dump(),
            visible_version=visible_version,
            rows=rows,
            versions=_versions(active_conn, report_id),
        )
    finally:
        if own_conn:
            active_conn.close()


def update_row(report_id: int, row_id: int, payload: CpkCphRowPatchRequest, *, user_id: int | None) -> CpkCphReportDetail:
    with psycopg.connect(_database_dsn(), row_factory=dict_row) as conn:
        _ensure_cpk_tables(conn)
        summary = _get_report_summary(conn, report_id)
        if summary.status == "approved":
            raise CpkCphConflict("El reporte esta aprobado. Reabrelo antes de editar.")
        with conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM cpk_cph_report_rows WHERE id = %s AND report_id = %s AND version_number = 0;",
                (row_id, report_id),
            )
            row = cur.fetchone()
        if not row:
            raise CpkCphNotFound("Fila no encontrada en el borrador.")

        next_data = _row_record(row).model_dump()
        provided_fields = getattr(payload, "model_fields_set", set())
        recalc = False
        if payload.cutoff_start_at is not None:
            next_data["cutoff_start_at"] = payload.cutoff_start_at
            recalc = True
        if payload.cutoff_end_at is not None:
            next_data["cutoff_end_at"] = payload.cutoff_end_at
            recalc = True
        if "km_client" in provided_fields:
            next_data["km_client"] = payload.km_client
        if "km_adjustment" in provided_fields:
            next_data["km_adjustment"] = payload.km_adjustment
        if "hour_adjustment" in provided_fields:
            next_data["hour_adjustment"] = payload.hour_adjustment
        if "kms_ecm_approved" in provided_fields:
            next_data["kms_ecm_approved"] = payload.kms_ecm_approved
        if "hours_ecm_approved" in provided_fields:
            next_data["hours_ecm_approved"] = payload.hours_ecm_approved
        if payload.correction_note is not None:
            next_data["correction_note"] = payload.correction_note.strip() or None

        if recalc:
            preview = preview_report(
                CpkCphPreviewRequest(
                    month=summary.period_month,
                    customer_id=summary.customer_id,
                    rows=[
                        CpkCphInputRow(
                            plate=next_data["plate"],
                            cutoff_start_at=next_data["cutoff_start_at"],
                            cutoff_end_at=next_data["cutoff_end_at"],
                            km_client=next_data.get("km_client"),
                        )
                    ],
                )
            )
            recalculated = preview.rows[0].model_dump()
            recalculated["id"] = row_id
            recalculated["correction_note"] = next_data.get("correction_note")
            recalculated["km_adjustment"] = next_data.get("km_adjustment") or 0
            recalculated["hour_adjustment"] = next_data.get("hour_adjustment") or 0
            next_data.update(recalculated)

        km_adjustment = _num(next_data.get("km_adjustment")) or 0.0
        hour_adjustment = _num(next_data.get("hour_adjustment")) or 0.0
        kms_raw = _num(next_data.get("kms_ecm_geotab"))
        kms_approved = _num(next_data.get("kms_ecm_approved"))
        if "kms_ecm_approved" not in provided_fields:
            kms_approved = kms_raw + km_adjustment if kms_raw is not None else None
        hours_raw = _num(next_data.get("hours_ecm"))
        hours_approved = _num(next_data.get("hours_ecm_approved"))
        if "hours_ecm_approved" not in provided_fields:
            hours_approved = hours_raw + hour_adjustment if hours_raw is not None else None
        derived_kms = kms_raw + km_adjustment if kms_raw is not None else None
        derived_hours = hours_raw + hour_adjustment if hours_raw is not None else None
        manual_kms = (
            kms_approved is not None
            and derived_kms is not None
            and abs(kms_approved - derived_kms) > 0.0001
        )
        manual_hours = (
            hours_approved is not None
            and derived_hours is not None
            and abs(hours_approved - derived_hours) > 0.0001
        )
        if (km_adjustment or hour_adjustment or manual_kms or manual_hours) and not (next_data.get("correction_note") or "").strip():
            raise CpkCphConflict("Cada correccion de CPK/CPH requiere nota.")
        diff, pct = _metrics(
            kms_ecm_approved=kms_approved,
            km_client=_num(next_data.get("km_client")),
            kms_gps=_num(next_data.get("kms_gps")),
        )
        next_data["km_adjustment"] = km_adjustment
        next_data["hour_adjustment"] = hour_adjustment
        next_data["kms_ecm_approved"] = kms_approved
        next_data["hours_ecm_approved"] = hours_approved
        next_data["km_difference"] = diff
        next_data["km_difference_pct"] = pct

        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE cpk_cph_report_rows
                SET cutoff_start_at = %(cutoff_start_at)s,
                    cutoff_end_at = %(cutoff_end_at)s,
                    cutoff_start_utc = %(cutoff_start_utc)s,
                    cutoff_end_utc = %(cutoff_end_utc)s,
                    client_name = %(client_name)s,
                    database_name = %(database_name)s,
                    source_provider = %(source_provider)s,
                    provider_vehicle_id = %(provider_vehicle_id)s,
                    km_client = %(km_client)s,
                    odo_start = %(odo_start)s,
                    odo_end = %(odo_end)s,
                    horo_start = %(horo_start)s,
                    horo_end = %(horo_end)s,
                    kms_ecm_geotab = %(kms_ecm_geotab)s,
                    kms_gps = %(kms_gps)s,
                    hours_ecm = %(hours_ecm)s,
                    hours_gps = %(hours_gps)s,
                    fuel_gallons = %(fuel_gallons)s,
                    km_adjustment = %(km_adjustment)s,
                    hour_adjustment = %(hour_adjustment)s,
                    kms_ecm_approved = %(kms_ecm_approved)s,
                    hours_ecm_approved = %(hours_ecm_approved)s,
                    km_difference = %(km_difference)s,
                    km_difference_pct = %(km_difference_pct)s,
                    calculation_status = %(calculation_status)s,
                    warnings = %(warnings)s::jsonb,
                    correction_note = %(correction_note)s,
                    updated_at = NOW()
                WHERE id = %(id)s AND report_id = %(report_id)s AND version_number = 0;
                """,
                {
                    **next_data,
                    "id": row_id,
                    "report_id": report_id,
                    "warnings": Jsonb(next_data.get("warnings") or []),
                },
            )
            cur.execute(
                "UPDATE cpk_cph_reports SET updated_by = %s, updated_at = NOW() WHERE id = %s;",
                (user_id, report_id),
            )
        conn.commit()
        return get_report(report_id, conn=conn)


def approve_report(report_id: int, *, user_id: int | None) -> CpkCphReportDetail:
    with psycopg.connect(_database_dsn(), row_factory=dict_row) as conn:
        _ensure_cpk_tables(conn)
        summary = _get_report_summary(conn, report_id)
        if summary.status == "approved":
            raise CpkCphConflict("El reporte ya esta aprobado.")
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT COUNT(*) AS total,
                       COUNT(*) FILTER (WHERE calculation_status = 'valid') AS valid_count
                FROM cpk_cph_report_rows
                WHERE report_id = %s AND version_number = 0;
                """,
                (report_id,),
            )
            counts = cur.fetchone()
            total = int(counts["total"] or 0)
            valid_count = int(counts["valid_count"] or 0)
            if total == 0:
                raise CpkCphConflict("No hay filas para aprobar.")
            if valid_count != total:
                raise CpkCphConflict("Todas las filas deben estar validas antes de aprobar.")

            next_version = summary.current_version + 1
            cur.execute(
                "DELETE FROM cpk_cph_report_rows WHERE report_id = %s AND version_number = %s;",
                (report_id, next_version),
            )
            cur.execute(
                """
                INSERT INTO cpk_cph_report_rows (
                    report_id, version_number, plate, cutoff_start_at, cutoff_end_at,
                    cutoff_start_utc, cutoff_end_utc, client_name, database_name,
                    source_provider, provider_vehicle_id, km_client, odo_start, odo_end,
                    horo_start, horo_end, kms_ecm_geotab, kms_gps, hours_ecm, hours_gps,
                    fuel_gallons, km_adjustment, hour_adjustment, kms_ecm_approved,
                    hours_ecm_approved, km_difference, km_difference_pct,
                    calculation_status, warnings, correction_note
                )
                SELECT
                    report_id, %s, plate, cutoff_start_at, cutoff_end_at,
                    cutoff_start_utc, cutoff_end_utc, client_name, database_name,
                    source_provider, provider_vehicle_id, km_client, odo_start, odo_end,
                    horo_start, horo_end, kms_ecm_geotab, kms_gps, hours_ecm, hours_gps,
                    fuel_gallons, km_adjustment, hour_adjustment, kms_ecm_approved,
                    hours_ecm_approved, km_difference, km_difference_pct,
                    calculation_status, warnings, correction_note
                FROM cpk_cph_report_rows
                WHERE report_id = %s AND version_number = 0;
                """,
                (next_version, report_id),
            )
            cur.execute(
                """
                INSERT INTO cpk_cph_report_versions (
                    report_id, version_number, row_count, created_by, approved_by, approved_at
                )
                VALUES (%s, %s, %s, %s, %s, NOW())
                ON CONFLICT (report_id, version_number)
                DO UPDATE SET row_count = EXCLUDED.row_count,
                              approved_by = EXCLUDED.approved_by,
                              approved_at = EXCLUDED.approved_at;
                """,
                (report_id, next_version, total, user_id, user_id),
            )
            cur.execute(
                """
                UPDATE cpk_cph_reports
                SET status = 'approved',
                    current_version = %s,
                    approved_by = %s,
                    approved_at = NOW(),
                    updated_by = %s,
                    updated_at = NOW()
                WHERE id = %s;
                """,
                (next_version, user_id, user_id, report_id),
            )
        conn.commit()
        return get_report(report_id, conn=conn)


def reopen_report(report_id: int, *, user_id: int | None) -> CpkCphReportDetail:
    with psycopg.connect(_database_dsn(), row_factory=dict_row) as conn:
        _ensure_cpk_tables(conn)
        summary = _get_report_summary(conn, report_id)
        if summary.current_version <= 0:
            raise CpkCphConflict("El reporte aun no tiene una version aprobada.")
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM cpk_cph_report_rows WHERE report_id = %s AND version_number = 0;",
                (report_id,),
            )
            cur.execute(
                """
                INSERT INTO cpk_cph_report_rows (
                    report_id, version_number, plate, cutoff_start_at, cutoff_end_at,
                    cutoff_start_utc, cutoff_end_utc, client_name, database_name,
                    source_provider, provider_vehicle_id, km_client, odo_start, odo_end,
                    horo_start, horo_end, kms_ecm_geotab, kms_gps, hours_ecm, hours_gps,
                    fuel_gallons, km_adjustment, hour_adjustment, kms_ecm_approved,
                    hours_ecm_approved, km_difference, km_difference_pct,
                    calculation_status, warnings, correction_note
                )
                SELECT
                    report_id, 0, plate, cutoff_start_at, cutoff_end_at,
                    cutoff_start_utc, cutoff_end_utc, client_name, database_name,
                    source_provider, provider_vehicle_id, km_client, odo_start, odo_end,
                    horo_start, horo_end, kms_ecm_geotab, kms_gps, hours_ecm, hours_gps,
                    fuel_gallons, km_adjustment, hour_adjustment, kms_ecm_approved,
                    hours_ecm_approved, km_difference, km_difference_pct,
                    calculation_status, warnings, correction_note
                FROM cpk_cph_report_rows
                WHERE report_id = %s AND version_number = %s;
                """,
                (report_id, summary.current_version),
            )
            cur.execute(
                """
                UPDATE cpk_cph_reports
                SET status = 'reopened',
                    reopened_from_version = %s,
                    updated_by = %s,
                    updated_at = NOW()
                WHERE id = %s;
                """,
                (summary.current_version, user_id, report_id),
            )
        conn.commit()
        return get_report(report_id, conn=conn)
