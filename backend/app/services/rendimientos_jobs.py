"""
Sistema de jobs asincronos para calculo de rendimientos.

El POST /calculate crea una fila en performance_calculation_jobs y dispara
run_job() como BackgroundTask. El cron CLI usa el mismo create_job+run_job
para que las corridas automaticas tambien queden registradas.

Solo un job activo (queued/running) por (month, scope_key). Si llega un POST
para un scope ya activo, devolvemos el job existente con 409.
"""
from __future__ import annotations

import logging
from typing import Any

import psycopg
from psycopg.errors import UniqueViolation
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from app.schemas.vehicle import (
    MonthlyPerformanceCalculateRequest,
    MonthlyPerformanceSummary,
    PerformanceCalculationJob,
)
from app.services.motor_catalog import _database_dsn
from app.services.rendimientos import calculate_monthly_performance

_logger = logging.getLogger(__name__)

_TABLE_BOOTSTRAPPED = False


def _ensure_jobs_table(conn: psycopg.Connection | None = None) -> None:
    """
    Bootstrap idempotente — corre la DDL una sola vez por proceso, en una
    conexion propia que commitea inmediatamente. Asi los locks de CREATE/INDEX
    no quedan dentro de la transaccion larga del caller.

    El parametro `conn` se mantiene por compatibilidad con el patron de los
    otros _ensure_* pero ya no se usa: la DDL siempre va a una conexion propia.
    """
    global _TABLE_BOOTSTRAPPED
    if _TABLE_BOOTSTRAPPED:
        return
    own_conn = psycopg.connect(_database_dsn())
    try:
        with own_conn.cursor() as cur:
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS performance_calculation_jobs (
                    id BIGSERIAL PRIMARY KEY,
                    status TEXT NOT NULL DEFAULT 'queued'
                        CHECK (status IN ('queued','running','done','error')),
                    month TEXT NOT NULL,
                    scope_key TEXT NOT NULL,
                    customer_id BIGINT NULL,
                    customer_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
                    customer_database_id BIGINT NULL,
                    force_recalculate BOOLEAN NOT NULL DEFAULT TRUE,
                    total_targets INTEGER NOT NULL DEFAULT 0,
                    processed_targets INTEGER NOT NULL DEFAULT 0,
                    summary JSONB NULL,
                    error_message TEXT NULL,
                    triggered_by TEXT NOT NULL DEFAULT 'ui',
                    created_by_user_id BIGINT NULL,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    started_at TIMESTAMPTZ NULL,
                    finished_at TIMESTAMPTZ NULL,
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                );
                """
            )
            cur.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS performance_calculation_jobs_active_unique
                    ON performance_calculation_jobs (month, scope_key)
                    WHERE status IN ('queued','running');
                """
            )
            cur.execute(
                """
                CREATE INDEX IF NOT EXISTS performance_calculation_jobs_status_idx
                    ON performance_calculation_jobs (status, updated_at DESC);
                """
            )
            cur.execute(
                """
                CREATE INDEX IF NOT EXISTS performance_calculation_jobs_user_idx
                    ON performance_calculation_jobs (created_by_user_id, created_at DESC);
                """
            )
        own_conn.commit()
    finally:
        own_conn.close()
    _TABLE_BOOTSTRAPPED = True


class JobAlreadyRunning(Exception):
    """Hay un job activo para el mismo (month, scope). Trae el job existente."""

    def __init__(self, job: PerformanceCalculationJob):
        super().__init__(f"Job activo existente id={job.id}")
        self.job = job


class JobNotFound(Exception):
    pass


def _compute_scope_key(payload: MonthlyPerformanceCalculateRequest) -> str:
    cids: set[int] = set(payload.customer_ids or [])
    if payload.customer_id is not None:
        cids.add(int(payload.customer_id))
    cids_part = ",".join(str(c) for c in sorted(cids)) if cids else "all"
    db_part = str(payload.customer_database_id) if payload.customer_database_id is not None else "any"
    return f"{cids_part}|{db_part}"


def _row_to_job(row: dict[str, Any]) -> PerformanceCalculationJob:
    summary_data = row.get("summary")
    summary = None
    if isinstance(summary_data, dict):
        try:
            summary = MonthlyPerformanceSummary(**summary_data)
        except Exception:
            summary = None
    total = int(row.get("total_targets") or 0)
    processed = int(row.get("processed_targets") or 0)
    progress = 0.0 if total <= 0 else round(min(100.0, (processed / total) * 100.0), 2)
    return PerformanceCalculationJob(
        id=int(row["id"]),
        status=str(row["status"]),
        month=str(row["month"]),
        customer_id=row.get("customer_id"),
        customer_ids=list(row.get("customer_ids") or []),
        customer_database_id=row.get("customer_database_id"),
        force_recalculate=bool(row.get("force_recalculate")),
        total_targets=total,
        processed_targets=processed,
        progress_pct=progress,
        summary=summary,
        error_message=row.get("error_message"),
        triggered_by=str(row.get("triggered_by") or "ui"),
        created_by_user_id=row.get("created_by_user_id"),
        created_at=row["created_at"],
        started_at=row.get("started_at"),
        finished_at=row.get("finished_at"),
    )


def _fetch_job(conn: psycopg.Connection, job_id: int) -> PerformanceCalculationJob | None:
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            "SELECT * FROM performance_calculation_jobs WHERE id = %s;",
            (job_id,),
        )
        row = cur.fetchone()
    return _row_to_job(row) if row else None


def _fetch_active_job_for_scope(
    conn: psycopg.Connection, *, month: str, scope_key: str
) -> PerformanceCalculationJob | None:
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            SELECT *
            FROM performance_calculation_jobs
            WHERE month = %s
              AND scope_key = %s
              AND status IN ('queued','running')
            ORDER BY created_at DESC
            LIMIT 1;
            """,
            (month, scope_key),
        )
        row = cur.fetchone()
    return _row_to_job(row) if row else None


def create_job(
    payload: MonthlyPerformanceCalculateRequest,
    *,
    triggered_by: str = "ui",
    user_id: int | None = None,
) -> PerformanceCalculationJob:
    """
    Inserta un job en estado 'queued'. Si ya hay uno activo para el mismo scope,
    levanta JobAlreadyRunning con el job existente.
    """
    scope_key = _compute_scope_key(payload)
    customer_ids = sorted({int(c) for c in (payload.customer_ids or [])})

    with psycopg.connect(_database_dsn(), row_factory=dict_row) as conn:
        _ensure_jobs_table(conn)
        existing = _fetch_active_job_for_scope(conn, month=payload.month, scope_key=scope_key)
        if existing is not None:
            raise JobAlreadyRunning(existing)

        try:
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute(
                    """
                    INSERT INTO performance_calculation_jobs (
                        status, month, scope_key,
                        customer_id, customer_ids, customer_database_id,
                        force_recalculate, triggered_by, created_by_user_id
                    )
                    VALUES ('queued', %s, %s, %s, %s::jsonb, %s, %s, %s, %s)
                    RETURNING *;
                    """,
                    (
                        payload.month,
                        scope_key,
                        payload.customer_id,
                        Jsonb(customer_ids),
                        payload.customer_database_id,
                        payload.force_recalculate,
                        triggered_by,
                        user_id,
                    ),
                )
                row = cur.fetchone()
            conn.commit()
        except UniqueViolation:
            # Carrera: alguien creo el job entre nuestro SELECT y el INSERT.
            conn.rollback()
            existing = _fetch_active_job_for_scope(
                conn, month=payload.month, scope_key=scope_key
            )
            if existing is not None:
                raise JobAlreadyRunning(existing) from None
            raise

    if row is None:
        raise RuntimeError("No fue posible crear el job de rendimientos.")
    return _row_to_job(row)


def _update_progress(job_id: int, processed: int, total: int) -> None:
    try:
        with psycopg.connect(_database_dsn()) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE performance_calculation_jobs
                    SET processed_targets = %s,
                        total_targets = %s,
                        updated_at = NOW()
                    WHERE id = %s;
                    """,
                    (processed, total, job_id),
                )
            conn.commit()
    except Exception:
        _logger.exception("No fue posible actualizar progreso del job %s", job_id)


def _mark_running(job_id: int) -> None:
    with psycopg.connect(_database_dsn()) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE performance_calculation_jobs
                SET status = 'running',
                    started_at = COALESCE(started_at, NOW()),
                    updated_at = NOW()
                WHERE id = %s;
                """,
                (job_id,),
            )
        conn.commit()


def _mark_done(job_id: int, summary: MonthlyPerformanceSummary) -> None:
    with psycopg.connect(_database_dsn()) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE performance_calculation_jobs
                SET status = 'done',
                    summary = %s::jsonb,
                    finished_at = NOW(),
                    updated_at = NOW(),
                    processed_targets = GREATEST(processed_targets, total_targets)
                WHERE id = %s;
                """,
                (Jsonb(summary.model_dump()), job_id),
            )
        conn.commit()


def _mark_error(job_id: int, message: str) -> None:
    with psycopg.connect(_database_dsn()) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE performance_calculation_jobs
                SET status = 'error',
                    error_message = %s,
                    finished_at = NOW(),
                    updated_at = NOW()
                WHERE id = %s;
                """,
                (message[:2000], job_id),
            )
        conn.commit()


def run_job(job_id: int) -> PerformanceCalculationJob:
    """
    Ejecuta el calculo asociado al job_id. Actualiza estado y progreso en la fila.
    Diseñado para correr en thread aparte (FastAPI BackgroundTasks) o sincrono
    desde el cron CLI.
    """
    with psycopg.connect(_database_dsn(), row_factory=dict_row) as conn:
        job = _fetch_job(conn, job_id)
    if job is None:
        raise JobNotFound(f"Job {job_id} no existe")

    if job.status not in ("queued", "running"):
        # Ya termino o esta en estado raro; no reejecutar.
        return job

    payload = MonthlyPerformanceCalculateRequest(
        month=job.month,
        customer_id=job.customer_id,
        customer_ids=list(job.customer_ids or []),
        customer_database_id=job.customer_database_id,
        force_recalculate=job.force_recalculate,
    )

    _mark_running(job_id)
    _logger.info("Job %s: running (month=%s, scope_key=%s)", job_id, job.month, _compute_scope_key(payload))

    try:
        result = calculate_monthly_performance(
            payload,
            progress_callback=lambda processed, total: _update_progress(job_id, processed, total),
        )
    except Exception as exc:
        _logger.exception("Job %s fallo", job_id)
        _mark_error(job_id, f"{type(exc).__name__}: {exc}")
        with psycopg.connect(_database_dsn(), row_factory=dict_row) as conn:
            return _fetch_job(conn, job_id) or job

    _mark_done(job_id, result.summary)
    _logger.info(
        "Job %s: done — calculated=%d partial=%d unbound=%d no_data=%d error=%d",
        job_id,
        result.summary.calculated,
        result.summary.partial,
        result.summary.unbound,
        result.summary.no_data,
        result.summary.error,
    )

    with psycopg.connect(_database_dsn(), row_factory=dict_row) as conn:
        final = _fetch_job(conn, job_id)
    return final or job


def get_job(job_id: int) -> PerformanceCalculationJob:
    with psycopg.connect(_database_dsn(), row_factory=dict_row) as conn:
        _ensure_jobs_table(conn)
        job = _fetch_job(conn, job_id)
    if job is None:
        raise JobNotFound(f"Job {job_id} no existe")
    return job


def list_active_jobs(*, user_id: int | None = None) -> list[PerformanceCalculationJob]:
    """
    Lista jobs en estado queued/running. Si se pasa user_id, filtra los creados
    por ese usuario; si no, retorna todos los activos (util para el cron/CLI).
    """
    params: list[Any] = []
    where = ["status IN ('queued','running')"]
    if user_id is not None:
        where.append("created_by_user_id = %s")
        params.append(user_id)
    sql = f"""
        SELECT *
        FROM performance_calculation_jobs
        WHERE {" AND ".join(where)}
        ORDER BY created_at DESC;
    """
    with psycopg.connect(_database_dsn(), row_factory=dict_row) as conn:
        _ensure_jobs_table(conn)
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(sql, params)
            rows = cur.fetchall()
    return [_row_to_job(row) for row in rows]


def list_recent_jobs(*, limit: int = 50) -> list[PerformanceCalculationJob]:
    """
    Lista los ultimos N jobs de cualquier estado, ordenados por created_at DESC.
    Sirve como historial / "logs" del calculo (UI y cron).
    """
    limit = max(1, min(int(limit), 200))
    with psycopg.connect(_database_dsn(), row_factory=dict_row) as conn:
        _ensure_jobs_table(conn)
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                """
                SELECT *
                FROM performance_calculation_jobs
                ORDER BY created_at DESC
                LIMIT %s;
                """,
                (limit,),
            )
            rows = cur.fetchall()
    return [_row_to_job(row) for row in rows]
