"""Jobs persistentes para el reprocesamiento masivo del maestro de vehiculos."""
from __future__ import annotations

import logging
from typing import Any

from psycopg.errors import UniqueViolation
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from app.core.db import db_conn
from app.schemas.vehicle import VehicleReprocessJob, VehicleReprocessJobRequest
from app.services.vehicle_lookup import batch_lookup_vehicles_stream

_logger = logging.getLogger(__name__)
_TABLE_BOOTSTRAPPED = False


class ReprocessJobAlreadyRunning(Exception):
    def __init__(self, job: VehicleReprocessJob):
        super().__init__(f"Job activo existente id={job.id}")
        self.job = job


class ReprocessJobNotFound(Exception):
    pass


def _ensure_table() -> None:
    global _TABLE_BOOTSTRAPPED
    if _TABLE_BOOTSTRAPPED:
        return
    with db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS vehicle_reprocess_jobs (
                    id BIGSERIAL PRIMARY KEY,
                    status TEXT NOT NULL DEFAULT 'queued'
                        CHECK (status IN ('queued','running','done','error','cancelled')),
                    identifiers JSONB NOT NULL DEFAULT '[]'::jsonb,
                    scope TEXT NOT NULL DEFAULT 'all'
                        CHECK (scope IN ('all','fenix','cummins')),
                    skip_geotab BOOLEAN NOT NULL DEFAULT FALSE,
                    total_targets INTEGER NOT NULL DEFAULT 0,
                    processed_targets INTEGER NOT NULL DEFAULT 0,
                    current_identifier TEXT NULL,
                    errors JSONB NOT NULL DEFAULT '[]'::jsonb,
                    error_message TEXT NULL,
                    created_by_user_id BIGINT NOT NULL,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    started_at TIMESTAMPTZ NULL,
                    finished_at TIMESTAMPTZ NULL,
                    acknowledged_at TIMESTAMPTZ NULL,
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                );
                """
            )
            cur.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS vehicle_reprocess_jobs_user_active_unique
                    ON vehicle_reprocess_jobs (created_by_user_id)
                    WHERE status IN ('queued','running');
                """
            )
            cur.execute(
                """
                CREATE INDEX IF NOT EXISTS vehicle_reprocess_jobs_user_recent_idx
                    ON vehicle_reprocess_jobs (created_by_user_id, created_at DESC);
                """
            )
        conn.commit()
    _TABLE_BOOTSTRAPPED = True


def _row_to_job(row: dict[str, Any]) -> VehicleReprocessJob:
    total = int(row.get("total_targets") or 0)
    processed = int(row.get("processed_targets") or 0)
    return VehicleReprocessJob(
        id=int(row["id"]),
        status=str(row["status"]),
        scope=str(row.get("scope") or "all"),
        skip_geotab=bool(row.get("skip_geotab")),
        total_targets=total,
        processed_targets=processed,
        progress_pct=0.0 if total <= 0 else round(min(100.0, processed / total * 100.0), 2),
        current_identifier=row.get("current_identifier"),
        errors=list(row.get("errors") or []),
        error_message=row.get("error_message"),
        created_by_user_id=int(row["created_by_user_id"]),
        created_at=row["created_at"],
        started_at=row.get("started_at"),
        finished_at=row.get("finished_at"),
    )


def _fetch_row(job_id: int, *, user_id: int | None = None) -> dict[str, Any] | None:
    _ensure_table()
    where = ["id = %s"]
    params: list[Any] = [job_id]
    if user_id is not None:
        where.append("created_by_user_id = %s")
        params.append(user_id)
    with db_conn(row_factory=dict_row) as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(f"SELECT * FROM vehicle_reprocess_jobs WHERE {' AND '.join(where)};", params)
            row = cur.fetchone()
    return dict(row) if row else None


def create_job(payload: VehicleReprocessJobRequest, *, user_id: int) -> VehicleReprocessJob:
    _ensure_table()
    identifiers = list(dict.fromkeys(value.strip().upper() for value in payload.identifiers if value.strip()))
    if not identifiers:
        raise ValueError("Se requiere al menos una placa o VIN.")

    with db_conn(row_factory=dict_row) as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                """SELECT * FROM vehicle_reprocess_jobs
                   WHERE created_by_user_id = %s AND status IN ('queued','running')
                   ORDER BY created_at DESC LIMIT 1;""",
                (user_id,),
            )
            existing = cur.fetchone()
            if existing:
                raise ReprocessJobAlreadyRunning(_row_to_job(dict(existing)))
            cur.execute(
                """UPDATE vehicle_reprocess_jobs SET acknowledged_at = COALESCE(acknowledged_at, NOW())
                   WHERE created_by_user_id = %s AND status IN ('done','error','cancelled');""",
                (user_id,),
            )
            try:
                cur.execute(
                    """INSERT INTO vehicle_reprocess_jobs (
                           identifiers, scope, skip_geotab, total_targets,
                           current_identifier, created_by_user_id
                       ) VALUES (%s::jsonb, %s, %s, %s, %s, %s)
                       RETURNING *;""",
                    (Jsonb(identifiers), payload.scope, payload.skip_geotab, len(identifiers), identifiers[0], user_id),
                )
                row = cur.fetchone()
                conn.commit()
            except UniqueViolation:
                conn.rollback()
                cur.execute(
                    """SELECT * FROM vehicle_reprocess_jobs
                       WHERE created_by_user_id = %s AND status IN ('queued','running')
                       ORDER BY created_at DESC LIMIT 1;""",
                    (user_id,),
                )
                existing = cur.fetchone()
                if existing:
                    raise ReprocessJobAlreadyRunning(_row_to_job(dict(existing))) from None
                raise
    if not row:
        raise RuntimeError("No fue posible crear el job de reprocesamiento.")
    return _row_to_job(dict(row))


def get_job(job_id: int, *, user_id: int) -> VehicleReprocessJob:
    row = _fetch_row(job_id, user_id=user_id)
    if not row:
        raise ReprocessJobNotFound(f"Job {job_id} no existe.")
    return _row_to_job(row)


def get_current_job(*, user_id: int) -> VehicleReprocessJob | None:
    _ensure_table()
    with db_conn(row_factory=dict_row) as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                """SELECT * FROM vehicle_reprocess_jobs
                   WHERE created_by_user_id = %s AND acknowledged_at IS NULL
                   ORDER BY CASE WHEN status IN ('queued','running') THEN 0 ELSE 1 END,
                            created_at DESC
                   LIMIT 1;""",
                (user_id,),
            )
            row = cur.fetchone()
    return _row_to_job(dict(row)) if row else None


def _mark_running(job_id: int) -> bool:
    with db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """UPDATE vehicle_reprocess_jobs
                   SET status = 'running', started_at = COALESCE(started_at, NOW()), updated_at = NOW()
                   WHERE id = %s AND status = 'queued';""",
                (job_id,),
            )
            changed = cur.rowcount > 0
        conn.commit()
    return changed


def _update_progress(job_id: int, processed: int, current_identifier: str | None, errors: list[str]) -> bool:
    with db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """UPDATE vehicle_reprocess_jobs
                   SET processed_targets = %s, current_identifier = %s,
                       errors = %s::jsonb, updated_at = NOW()
                   WHERE id = %s AND status = 'running';""",
                (processed, current_identifier, Jsonb(errors), job_id),
            )
            changed = cur.rowcount > 0
        conn.commit()
    return changed


def _mark_done(job_id: int, errors: list[str]) -> None:
    with db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """UPDATE vehicle_reprocess_jobs
                   SET status = 'done', processed_targets = total_targets,
                       current_identifier = NULL, errors = %s::jsonb,
                       finished_at = NOW(), updated_at = NOW()
                   WHERE id = %s AND status = 'running';""",
                (Jsonb(errors), job_id),
            )
        conn.commit()


def _mark_error(job_id: int, message: str, errors: list[str]) -> None:
    with db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """UPDATE vehicle_reprocess_jobs
                   SET status = 'error', error_message = %s, errors = %s::jsonb,
                       current_identifier = NULL, finished_at = NOW(), updated_at = NOW()
                   WHERE id = %s AND status IN ('queued','running');""",
                (message[:2000], Jsonb(errors), job_id),
            )
        conn.commit()


def run_job(job_id: int) -> None:
    row = _fetch_row(job_id)
    if not row or not _mark_running(job_id):
        return
    identifiers = list(row.get("identifiers") or [])
    errors: list[str] = []
    processed = 0
    try:
        for result in batch_lookup_vehicles_stream(
            identifiers,
            force=True,
            scope=str(row.get("scope") or "all"),
            skip_geotab=bool(row.get("skip_geotab")),
        ):
            identifier = identifiers[processed] if processed < len(identifiers) else ""
            if getattr(result, "status", None) == "error":
                errors.append(identifier)
            processed += 1
            next_identifier = identifiers[processed] if processed < len(identifiers) else None
            if not _update_progress(job_id, processed, next_identifier, errors):
                return
        if processed < len(identifiers):
            remaining = identifiers[processed:]
            _mark_error(
                job_id,
                "El reprocesamiento termino antes de recibir todos los resultados.",
                [*errors, *remaining],
            )
            return
        _mark_done(job_id, errors)
    except Exception as exc:
        _logger.exception("Job de reprocesamiento %s fallo", job_id)
        _mark_error(job_id, f"{type(exc).__name__}: {exc}", errors)


def cancel_job(job_id: int, *, user_id: int) -> VehicleReprocessJob:
    _ensure_table()
    with db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """UPDATE vehicle_reprocess_jobs
                   SET status = 'cancelled', current_identifier = NULL,
                       finished_at = NOW(), updated_at = NOW()
                   WHERE id = %s AND created_by_user_id = %s
                     AND status IN ('queued','running');""",
                (job_id, user_id),
            )
        conn.commit()
    return get_job(job_id, user_id=user_id)


def acknowledge_job(job_id: int, *, user_id: int) -> VehicleReprocessJob:
    _ensure_table()
    with db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """UPDATE vehicle_reprocess_jobs SET acknowledged_at = NOW(), updated_at = NOW()
                   WHERE id = %s AND created_by_user_id = %s
                     AND status IN ('done','error','cancelled');""",
                (job_id, user_id),
            )
        conn.commit()
    return get_job(job_id, user_id=user_id)
