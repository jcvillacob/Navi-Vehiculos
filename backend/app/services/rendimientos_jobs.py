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
    AvailabilitySummary,
    MonthlyPerformanceCalculateRequest,
    MonthlyPerformanceSummary,
    PerformanceCalculationJob,
)
from app.services.availability_store import (
    _SYSTEM_CUSTOMER_NAME,
    run_availability_phase,
)
from app.clients.cloudfleet_client import CloudFleetAuthError, CloudFleetUnavailableError
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
            cur.execute(
                """
                ALTER TABLE performance_calculation_jobs
                ADD COLUMN IF NOT EXISTS compute_availability BOOLEAN NOT NULL DEFAULT FALSE;
                """
            )
            cur.execute(
                """
                ALTER TABLE performance_calculation_jobs
                ADD COLUMN IF NOT EXISTS include_adhoc BOOLEAN NOT NULL DEFAULT FALSE;
                """
            )
            cur.execute(
                """
                ALTER TABLE performance_calculation_jobs
                ADD COLUMN IF NOT EXISTS adhoc_plates JSONB NOT NULL DEFAULT '[]'::jsonb;
                """
            )
            cur.execute(
                """
                ALTER TABLE performance_calculation_jobs
                ADD COLUMN IF NOT EXISTS adhoc_filters JSONB NOT NULL DEFAULT '{}'::jsonb;
                """
            )
            cur.execute(
                """
                ALTER TABLE performance_calculation_jobs
                ADD COLUMN IF NOT EXISTS adhoc_only BOOLEAN NOT NULL DEFAULT FALSE;
                """
            )
            cur.execute(
                """
                ALTER TABLE performance_calculation_jobs
                ADD COLUMN IF NOT EXISTS availability_only BOOLEAN NOT NULL DEFAULT FALSE;
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
    if payload.availability_only:
        # availability_only implica compute_availability pero debe tener su
        # propio scope para no colisionar con un job estandar del mismo mes.
        avail_part = "avonly"
    else:
        avail_part = "av" if payload.compute_availability else "noav"
    adhoc_part = "adhoconly" if payload.adhoc_only else ("adhoc" if payload.include_adhoc else "std")
    return f"{cids_part}|{db_part}|{avail_part}|{adhoc_part}"


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
        compute_availability=bool(row.get("compute_availability")),
        include_adhoc=bool(row.get("include_adhoc")),
        adhoc_only=bool(row.get("adhoc_only")),
        availability_only=bool(row.get("availability_only")),
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
    # Normalizacion: availability_only implica compute_availability=true.
    # Se aplica tanto al scope como al valor persistido.
    compute_availability = payload.compute_availability or payload.availability_only
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
                        force_recalculate, compute_availability,
                        include_adhoc, adhoc_plates, adhoc_filters, adhoc_only,
                        availability_only,
                        triggered_by, created_by_user_id
                    )
                    VALUES ('queued', %s, %s, %s, %s::jsonb, %s, %s, %s, %s, %s::jsonb, %s::jsonb, %s, %s, %s, %s)
                    RETURNING *;
                    """,
                    (
                        payload.month,
                        scope_key,
                        payload.customer_id,
                        Jsonb(customer_ids),
                        payload.customer_database_id,
                        payload.force_recalculate,
                        compute_availability,
                        payload.include_adhoc,
                        Jsonb(list(payload.adhoc_plates or [])),
                        Jsonb(dict(payload.adhoc_filters or {})),
                        payload.adhoc_only,
                        payload.availability_only,
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


def _run_availability_for_job(
    job_id: int,
    payload: MonthlyPerformanceCalculateRequest,
    *,
    rendimientos_total: int = 0,
) -> AvailabilitySummary | None:
    """
    Ejecuta la fase de disponibilidad CloudFleet para un job.

    En modo availability_only `rendimientos_total` es 0 y se inicializa la
    barra de progreso en 0/total. En el flujo normal se pasa el total de
    placas procesadas en rendimientos para mantener la barra monotona.
    Devuelve None si la fase fallo (y ya marco el job como error).
    """
    try:
        availability_total = _count_availability_targets(payload)
        if rendimientos_total == 0:
            # availability_only: inicializamos la barra de progreso en 0/total.
            _update_progress(job_id, 0, availability_total)
            grand_total = availability_total
        else:
            # Flujo normal: extendemos el total para que el front anticipe
            # el segundo tramo y la fraccion overall se mantenga monotona.
            _bump_total(job_id, extra_total=availability_total)
            grand_total = rendimientos_total + availability_total

        availability_summary = run_availability_phase(
            month=payload.month,
            customer_ids=list(payload.customer_ids or []),
            progress_callback=lambda processed: _update_progress(
                job_id,
                rendimientos_total + processed,
                grand_total,
            ),
        )
        return AvailabilitySummary(**availability_summary)
    except (CloudFleetAuthError, CloudFleetUnavailableError) as exc:
        _logger.exception("Job %s: fase de disponibilidad fallo", job_id)
        _mark_error(job_id, f"Disponibilidad: {type(exc).__name__}: {exc}")
    except Exception as exc:
        _logger.exception("Job %s: fase de disponibilidad fallo (inesperado)", job_id)
        _mark_error(job_id, f"Disponibilidad: {type(exc).__name__}: {exc}")
    return None


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

    # Recuperar campos ad-hoc y availability_only desde la fila del job.
    adhoc_plates: list[str] = []
    adhoc_filters: dict[str, list[str]] = {}
    adhoc_only: bool = False
    availability_only: bool = False
    with psycopg.connect(_database_dsn(), row_factory=dict_row) as _rc:
        with _rc.cursor() as _rcur:
            _rcur.execute(
                "SELECT include_adhoc, adhoc_plates, adhoc_filters, adhoc_only, availability_only FROM performance_calculation_jobs WHERE id = %s;",
                (job_id,),
            )
            _adhoc_row = _rcur.fetchone()
            if _adhoc_row:
                adhoc_plates = list(_adhoc_row.get("adhoc_plates") or [])
                adhoc_filters = dict(_adhoc_row.get("adhoc_filters") or {})
                adhoc_only = bool(_adhoc_row.get("adhoc_only"))
                availability_only = bool(_adhoc_row.get("availability_only"))

    payload = MonthlyPerformanceCalculateRequest(
        month=job.month,
        customer_id=job.customer_id,
        customer_ids=list(job.customer_ids or []),
        customer_database_id=job.customer_database_id,
        force_recalculate=job.force_recalculate,
        compute_availability=job.compute_availability,
        include_adhoc=job.include_adhoc,
        adhoc_only=adhoc_only,
        availability_only=availability_only,
        adhoc_plates=adhoc_plates,
        adhoc_filters=adhoc_filters,
    )

    _mark_running(job_id)
    _logger.info(
        "Job %s: running (month=%s, scope_key=%s, availability=%s, availability_only=%s)",
        job_id,
        job.month,
        _compute_scope_key(payload),
        job.compute_availability,
        availability_only,
    )

    if availability_only:
        # Modo availability_only: saltamos rendimientos y corremos SOLO disponibilidad.
        result_summary = MonthlyPerformanceSummary()
        availability_summary = _run_availability_for_job(job_id, payload, rendimientos_total=0)
        if availability_summary is None:
            with psycopg.connect(_database_dsn(), row_factory=dict_row) as conn:
                return _fetch_job(conn, job_id) or job
        summary_with_availability = result_summary.model_copy(
            update={"availability": availability_summary}
        )
    else:
        try:
            result = calculate_monthly_performance(
                payload,
                progress_callback=lambda processed, total: _update_progress(job_id, processed, total),
            )
        except Exception as exc:
            _logger.exception("Job %s fallo en fase de rendimientos", job_id)
            _mark_error(job_id, f"{type(exc).__name__}: {exc}")
            with psycopg.connect(_database_dsn(), row_factory=dict_row) as conn:
                return _fetch_job(conn, job_id) or job

        summary_with_availability = result.summary

        if job.compute_availability:
            rendimientos_total = max(result.summary.total, 0)
            availability_summary = _run_availability_for_job(
                job_id, payload, rendimientos_total=rendimientos_total
            )
            if availability_summary is None:
                with psycopg.connect(_database_dsn(), row_factory=dict_row) as conn:
                    return _fetch_job(conn, job_id) or job
            summary_with_availability = result.summary.model_copy(
                update={"availability": availability_summary}
            )

    _mark_done(job_id, summary_with_availability)
    avail_log = ""
    if summary_with_availability.availability is not None:
        a = summary_with_availability.availability
        avail_log = (
            f" | availability: calc={a.calculated} no_orders={a.no_orders} "
            f"not_in_cf={a.not_in_cloudfleet} err={a.error}"
        )
    _logger.info(
        "Job %s: done — calculated=%d partial=%d unbound=%d no_data=%d error=%d%s",
        job_id,
        summary_with_availability.calculated,
        summary_with_availability.partial,
        summary_with_availability.unbound,
        summary_with_availability.no_data,
        summary_with_availability.error,
        avail_log,
    )

    with psycopg.connect(_database_dsn(), row_factory=dict_row) as conn:
        final = _fetch_job(conn, job_id)
    return final or job


def _bump_total(job_id: int, *, extra_total: int) -> None:
    """Suma `extra_total` al total_targets sin tocar processed_targets."""
    if extra_total <= 0:
        return
    try:
        with psycopg.connect(_database_dsn()) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE performance_calculation_jobs
                    SET total_targets = total_targets + %s,
                        updated_at = NOW()
                    WHERE id = %s;
                    """,
                    (extra_total, job_id),
                )
            conn.commit()
    except Exception:
        _logger.exception("No fue posible aumentar total_targets del job %s", job_id)


def _count_availability_targets(payload: MonthlyPerformanceCalculateRequest) -> int:
    """
    Cantidad de placas que se van a procesar en la fase de disponibilidad.
    Misma query que `_load_plates_for_customers` pero solo cuenta, para que
    el front pueda mostrar el progreso completo desde el inicio.
    """
    params: list[Any] = []
    where = ["1 = 1", "(c.name IS NULL OR c.name <> %s)"]
    params.append(_SYSTEM_CUSTOMER_NAME)
    customer_ids = sorted({int(c) for c in (payload.customer_ids or [])})
    if customer_ids:
        where.append("a.customer_id = ANY(%s)")
        params.append(customer_ids)
    try:
        with psycopg.connect(_database_dsn()) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    SELECT COUNT(DISTINCT a.plate)
                    FROM vehicle_motor_assignments a
                    LEFT JOIN customers c ON c.id = a.customer_id
                    WHERE {" AND ".join(where)};
                    """,
                    params,
                )
                row = cur.fetchone()
                return int(row[0]) if row and row[0] is not None else 0
    except Exception:
        _logger.exception("No fue posible contar targets de disponibilidad")
        return 0


def cancel_job(job_id: int) -> PerformanceCalculationJob:
    """Marca un job activo como cancelado (status='error')."""
    with psycopg.connect(_database_dsn(), row_factory=dict_row) as conn:
        _ensure_jobs_table(conn)
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                """
                UPDATE performance_calculation_jobs
                SET status = 'error',
                    error_message = 'Cancelado por el usuario',
                    finished_at = NOW(),
                    updated_at = NOW()
                WHERE id = %s AND status IN ('queued', 'running')
                RETURNING *;
                """,
                (job_id,),
            )
            row = cur.fetchone()
        conn.commit()

    if row is None:
        job = get_job(job_id)  # exists but not active
        return job
    return _row_to_job(row)


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
