"""
Sistema de jobs asincronos para calculo de rendimientos.

El POST /calculate crea una fila en performance_calculation_jobs y dispara
run_job() como BackgroundTask. El cron CLI usa el mismo create_job+run_job
para que las corridas automaticas tambien queden registradas.

Solo un job activo (queued/running) por (month, scope_key). Si llega un POST
para un scope ya activo, devolvemos el job existente con 409.

Ademas del scope exacto, create_job rechaza (409) cualquier job nuevo cuyo
conjunto de targets se SOLAPE con un job activo del mismo mes (ver
`_jobs_overlap`): p.ej. un job UI "todos los clientes" sin disponibilidad y
el cron con disponibilidad escribirian las mismas filas al mismo tiempo.
"""
from __future__ import annotations

import logging
import time
from typing import Any, Callable

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
    AVAILABILITY_CATEGORIES,
    _SYSTEM_CUSTOMER_NAME,
    run_availability_phase,
)
from app.clients.cloudfleet_client import CloudFleetAuthError, CloudFleetUnavailableError
from app.core.db import db_conn
from app.services.job_control import JobCancelled, check_stop
from app.services.rendimientos import calculate_monthly_performance

_logger = logging.getLogger(__name__)

_TABLE_BOOTSTRAPPED = False

# Cada cuanto (segundos) el closure should_stop consulta la DB. Entre consultas
# devuelve el ultimo resultado cacheado, asi el chequeo por placa es gratis.
_SHOULD_STOP_INTERVAL_S = 2.0

# Un job queued/running sin updated_at por mas de este tiempo se considera
# huerfano (proceso reiniciado a mitad de corrida) y se marca como error.
_STALE_JOB_MAX_AGE_MINUTES = 15

_ACTIVE_STATUSES = ("queued", "running")


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
    with db_conn() as own_conn:
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


_ALL_CUSTOMERS = "all"

# Fraccion de placas en error (summary.error / summary.total) a partir de la
# cual un job 'done' se reporta como degradado en el digest diario.
_DIGEST_ERROR_RATIO = 0.2
_DIGEST_MESSAGE_MAX_CHARS = 120


def _customer_scope(job_like: Any) -> tuple[frozenset[int] | str, int | None]:
    """
    Normaliza el filtro de clientes de un payload o job:
    (set de customer_ids ∪ customer_id, o "all" si no hay filtro; customer_database_id).
    """
    cids: set[int] = {int(c) for c in (getattr(job_like, "customer_ids", None) or [])}
    single = getattr(job_like, "customer_id", None)
    if single is not None:
        cids.add(int(single))
    db_id = getattr(job_like, "customer_database_id", None)
    customers: frozenset[int] | str = frozenset(cids) if cids else _ALL_CUSTOMERS
    return customers, (int(db_id) if db_id is not None else None)


def _customer_scopes_overlap(
    a: tuple[frozenset[int] | str, int | None],
    b: tuple[frozenset[int] | str, int | None],
) -> bool:
    """
    True si dos filtros (clientes, database) pueden apuntar a las mismas placas.

    - Dos databases distintas explicitas nunca se solapan (una database
      pertenece a un solo cliente y sus placas son disjuntas).
    - Sin filtro de clientes ni database en alguno de los dos => "todos" => solapa.
    - Ambos con clientes explicitos => solapa solo si la interseccion no es vacia.
    - Uno con clientes y el otro solo con database => relacion desconocida =>
      se asume solape (conservador).
    """
    a_customers, a_db = a
    b_customers, b_db = b
    if a_db is not None and b_db is not None and a_db != b_db:
        return False
    a_all = a_customers == _ALL_CUSTOMERS and a_db is None
    b_all = b_customers == _ALL_CUSTOMERS and b_db is None
    if a_all or b_all:
        return True
    if isinstance(a_customers, frozenset) and isinstance(b_customers, frozenset):
        return bool(a_customers & b_customers)
    return True


def _jobs_overlap(a: Any, b: Any) -> bool:
    """
    True si dos jobs (payload MonthlyPerformanceCalculateRequest o fila
    PerformanceCalculationJob; cualquier objeto con los mismos atributos)
    escribirian sobre las mismas filas y por tanto no deben correr a la vez.

    Se comparan dos "tablas" por separado:
    - rendimientos (monthly_vehicle_performance): la tocan todos los jobs
      salvo availability_only. Dentro de ella, un job adhoc_only solo escribe
      placas sin cliente, asi que no choca con un job estandar sin adhoc.
    - disponibilidad (monthly_vehicle_availability): la tocan los jobs con
      compute_availability o availability_only.
    Hay conflicto si en alguna de las dos tablas los filtros de clientes se
    solapan (ver `_customer_scopes_overlap`). Si ambos traen `month` y
    difieren, nunca hay conflicto.
    """
    a_month = getattr(a, "month", None)
    b_month = getattr(b, "month", None)
    if a_month is not None and b_month is not None and a_month != b_month:
        return False

    a_scope = _customer_scope(a)
    b_scope = _customer_scope(b)

    a_avail_only = bool(getattr(a, "availability_only", False))
    b_avail_only = bool(getattr(b, "availability_only", False))

    # ── Tabla rendimientos ──
    if not a_avail_only and not b_avail_only:
        a_adhoc_only = bool(getattr(a, "adhoc_only", False))
        b_adhoc_only = bool(getattr(b, "adhoc_only", False))
        a_adhoc = a_adhoc_only or bool(getattr(a, "include_adhoc", False))
        b_adhoc = b_adhoc_only or bool(getattr(b, "include_adhoc", False))
        if a_adhoc and b_adhoc:
            return True
        a_customers_rows = not a_adhoc_only
        b_customers_rows = not b_adhoc_only
        if a_customers_rows and b_customers_rows and _customer_scopes_overlap(a_scope, b_scope):
            return True

    # ── Tabla disponibilidad ──
    a_avail = a_avail_only or bool(getattr(a, "compute_availability", False))
    b_avail = b_avail_only or bool(getattr(b, "compute_availability", False))
    if a_avail and b_avail and _customer_scopes_overlap(a_scope, b_scope):
        return True

    return False


def _fetch_active_jobs_for_month(
    conn: psycopg.Connection, *, month: str
) -> list[PerformanceCalculationJob]:
    """Jobs queued/running del mes, del mas antiguo al mas reciente."""
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            SELECT *
            FROM performance_calculation_jobs
            WHERE month = %s
              AND status IN ('queued','running')
            ORDER BY created_at ASC;
            """,
            (month,),
        )
        rows = cur.fetchall() or []
    return [_row_to_job(row) for row in rows]


def _find_conflicting_active_job(
    conn: psycopg.Connection,
    payload: MonthlyPerformanceCalculateRequest,
) -> PerformanceCalculationJob | None:
    """
    Primer job activo del mes cuyos targets se solapan con `payload`
    (`_jobs_overlap`). Un scope_key identico siempre solapa, asi que esto
    cubre tambien el duplicado exacto que protege el indice unico.
    """
    for job in _fetch_active_jobs_for_month(conn, month=payload.month):
        if _jobs_overlap(payload, job):
            return job
    return None


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
    Inserta un job en estado 'queued'. Si ya hay uno activo para el mismo mes
    cuyos targets se solapan con este (mismo scope exacto, "todos los
    clientes" contra cualquier subconjunto, clientes/database en comun, o
    ambos escribiendo disponibilidad), levanta JobAlreadyRunning con el job
    existente. availability_only y un job solo-rendimientos nunca chocan.
    """
    # Normalizacion: availability_only implica compute_availability=true.
    # Se aplica tanto al scope como al valor persistido.
    compute_availability = payload.compute_availability or payload.availability_only
    scope_key = _compute_scope_key(payload)
    customer_ids = sorted({int(c) for c in (payload.customer_ids or [])})

    with db_conn(row_factory=dict_row) as conn:
        _ensure_jobs_table(conn)
        existing = _find_conflicting_active_job(conn, payload)
        if existing is not None:
            _logger.info(
                "create_job: mes %s ya tiene job activo id=%s (status=%s, triggered_by=%s) "
                "cuyos targets solapan con el nuevo (scope=%s); se devuelve 409",
                payload.month,
                existing.id,
                existing.status,
                existing.triggered_by,
                scope_key,
            )
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
            existing = _find_conflicting_active_job(conn, payload)
            if existing is not None:
                raise JobAlreadyRunning(existing) from None
            raise

    if row is None:
        raise RuntimeError("No fue posible crear el job de rendimientos.")
    return _row_to_job(row)


def _update_progress(job_id: int, processed: int, total: int) -> None:
    try:
        with db_conn() as conn:
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


def _claim_job(job_id: int) -> PerformanceCalculationJob | None:
    """
    Claim atomico queued -> running. Un solo UPDATE condicionado a
    status='queued', asi dos workers (cron + UI, o dos procesos) nunca
    ejecutan el mismo job: solo uno ve la fila en RETURNING.

    Devuelve el job ya en 'running' si lo reclamamos; None si no habia fila
    en 'queued' (ya corre en otro worker, termino, o fue cancelado).
    """
    with db_conn(row_factory=dict_row) as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                """
                UPDATE performance_calculation_jobs
                SET status = 'running',
                    started_at = COALESCE(started_at, NOW()),
                    updated_at = NOW()
                WHERE id = %s AND status = 'queued'
                RETURNING *;
                """,
                (job_id,),
            )
            row = cur.fetchone()
        conn.commit()
    return _row_to_job(row) if row else None


def _mark_done(
    job_id: int,
    summary: MonthlyPerformanceSummary,
    *,
    error_message: str | None = None,
) -> None:
    """
    Cierra el job como 'done'. Solo aplica si sigue en 'running': un job
    cancelado (status='error' puesto por cancel_job) o reapeado nunca se
    sobreescribe con 'done'.

    `error_message` opcional permite dejar una advertencia no fatal (p.ej.
    "Disponibilidad: ...") manteniendo status='done'.
    """
    with db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE performance_calculation_jobs
                SET status = 'done',
                    summary = %s::jsonb,
                    error_message = %s,
                    finished_at = NOW(),
                    updated_at = NOW(),
                    processed_targets = GREATEST(processed_targets, total_targets)
                WHERE id = %s AND status = 'running';
                """,
                (
                    Jsonb(summary.model_dump()),
                    error_message[:2000] if error_message else None,
                    job_id,
                ),
            )
            updated = cur.rowcount
        conn.commit()
    if updated == 0:
        _logger.warning(
            "Job %s: _mark_done no actualizo filas (ya no estaba en 'running'; cancelado o reapeado)",
            job_id,
        )


def _mark_error(job_id: int, message: str) -> None:
    """Marca 'error' solo desde queued/running; no pisa un done/error previo."""
    with db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE performance_calculation_jobs
                SET status = 'error',
                    error_message = %s,
                    finished_at = NOW(),
                    updated_at = NOW()
                WHERE id = %s AND status IN ('queued', 'running');
                """,
                (message[:2000], job_id),
            )
            updated = cur.rowcount
        conn.commit()
    if updated == 0:
        _logger.warning(
            "Job %s: _mark_error no actualizo filas (ya no estaba activo)", job_id
        )


def _heartbeat(job_id: int) -> bool:
    """
    Toca updated_at para que el reaper no considere huerfano un job que sigue
    vivo aunque no reporte progreso (p.ej. un grupo de database largo).
    Devuelve True si el job sigue en 'running'.
    """
    with db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE performance_calculation_jobs
                SET updated_at = NOW()
                WHERE id = %s AND status = 'running';
                """,
                (job_id,),
            )
            updated = cur.rowcount
        conn.commit()
    return updated > 0


def _fetch_job_status(job_id: int) -> str | None:
    with db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT status FROM performance_calculation_jobs WHERE id = %s;",
                (job_id,),
            )
            row = cur.fetchone()
    if row is None:
        return None
    return str(row[0]) if not isinstance(row, dict) else str(row.get("status"))


def _make_should_stop(
    job_id: int,
    *,
    interval_s: float = _SHOULD_STOP_INTERVAL_S,
    clock: Callable[[], float] = time.monotonic,
) -> Callable[[], bool]:
    """
    Closure cooperativo para calculate_monthly_performance.

    Consulta la DB como maximo cada `interval_s` segundos (cachea el ultimo
    resultado entre consultas) y devuelve True cuando el job dejo de estar
    en queued/running (cancelado por el usuario o reapeado). Cada consulta
    real ademas hace heartbeat (updated_at=NOW()), asi el reaper no mata
    jobs vivos aunque no reporten progreso.

    Si la DB falla, asume que el job sigue vivo (False) y reintenta en el
    proximo intervalo: nunca cancelamos un calculo por un hiccup de red.
    """
    state: dict[str, Any] = {"last_check": None, "stopped": False}

    def should_stop() -> bool:
        if state["stopped"]:
            return True
        now = clock()
        last = state["last_check"]
        if last is not None and (now - last) < interval_s:
            return False
        state["last_check"] = now
        try:
            if _heartbeat(job_id):
                return False
            status = _fetch_job_status(job_id)
        except Exception:
            _logger.warning(
                "Job %s: no fue posible verificar estado/heartbeat; se asume vivo",
                job_id,
                exc_info=True,
            )
            return False
        if status in _ACTIVE_STATUSES:
            return False
        state["stopped"] = True
        _logger.info("Job %s: se detecto status=%s; solicitando parada cooperativa", job_id, status)
        return True

    return should_stop


def reap_stale_jobs(*, max_age_minutes: int = _STALE_JOB_MAX_AGE_MINUTES) -> int:
    """
    Marca como 'error' los jobs queued/running sin actividad (updated_at) por
    mas de `max_age_minutes`. Cubre el caso del proceso reiniciado a mitad de
    corrida (uvicorn --reload, deploy, OOM): el BackgroundTask muere pero la
    fila quedaba 'running' para siempre y bloqueaba el scope (indice unico).

    Devuelve la cantidad de jobs reapeados.
    """
    max_age_minutes = max(1, int(max_age_minutes))
    message = (
        f"Proceso reiniciado o job sin progreso por más de {max_age_minutes} min"
    )
    with db_conn(row_factory=dict_row) as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                """
                UPDATE performance_calculation_jobs
                SET status = 'error',
                    error_message = %s,
                    finished_at = NOW(),
                    updated_at = NOW()
                WHERE status IN ('queued', 'running')
                  AND updated_at < NOW() - make_interval(mins => %s)
                RETURNING id;
                """,
                (message, max_age_minutes),
            )
            rows = cur.fetchall() or []
        conn.commit()
    ids = [int(r["id"]) if isinstance(r, dict) else int(r[0]) for r in rows]
    if ids:
        _logger.warning(
            "Reaper: %d job(s) de rendimientos huerfanos marcados como error (>%d min sin actividad): %s",
            len(ids),
            max_age_minutes,
            ids,
        )
    return len(ids)


def _run_availability_for_job(
    job_id: int,
    payload: MonthlyPerformanceCalculateRequest,
    *,
    rendimientos_total: int = 0,
    should_stop: Callable[[], bool] | None = None,
) -> tuple[AvailabilitySummary | None, str | None]:
    """
    Ejecuta la fase de disponibilidad CloudFleet para un job.

    En modo availability_only `rendimientos_total` es 0 y se inicializa la
    barra de progreso en 0/total. En el flujo normal se pasa el total de
    placas procesadas en rendimientos para mantener la barra monotona.

    Devuelve (summary, None) si salio bien o (None, mensaje) si fallo. NO
    marca el job como error: eso lo decide run_job, porque en el flujo normal
    la fase de rendimientos ya termino y el job debe quedar 'done' con
    advertencia, no 'error'.

    JobCancelled se propaga siempre (nunca se traga como fallo de fase).
    """
    try:
        check_stop(should_stop)
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
        return AvailabilitySummary(**availability_summary), None
    except JobCancelled:
        raise
    except (CloudFleetAuthError, CloudFleetUnavailableError) as exc:
        _logger.exception("Job %s: fase de disponibilidad fallo", job_id)
        return None, f"Disponibilidad: {type(exc).__name__}: {exc}"
    except Exception as exc:
        _logger.exception("Job %s: fase de disponibilidad fallo (inesperado)", job_id)
        return None, f"Disponibilidad: {type(exc).__name__}: {exc}"


def _refetch(job_id: int, fallback: PerformanceCalculationJob) -> PerformanceCalculationJob:
    with db_conn(row_factory=dict_row) as conn:
        return _fetch_job(conn, job_id) or fallback


def run_job(job_id: int) -> PerformanceCalculationJob:
    """
    Ejecuta el calculo asociado al job_id. Actualiza estado y progreso en la fila.
    Diseñado para correr en thread aparte (FastAPI BackgroundTasks) o sincrono
    desde el cron CLI.

    El claim queued->running es atomico: si otro worker ya lo reclamo (o el
    job termino/fue cancelado) devolvemos la fila tal cual sin reejecutar.
    """
    job = _claim_job(job_id)
    if job is None:
        with db_conn(row_factory=dict_row) as conn:
            existing = _fetch_job(conn, job_id)
        if existing is None:
            raise JobNotFound(f"Job {job_id} no existe")
        if existing.status == "running":
            _logger.warning(
                "Job %s ya esta corriendo en otro worker; no se reejecuta", job_id
            )
        return existing

    # Recuperar campos ad-hoc y availability_only desde la fila del job.
    adhoc_plates: list[str] = []
    adhoc_filters: dict[str, list[str]] = {}
    adhoc_only: bool = False
    availability_only: bool = False
    with db_conn(row_factory=dict_row) as _rc:
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

    _logger.info(
        "Job %s: running (month=%s, scope_key=%s, availability=%s, availability_only=%s)",
        job_id,
        job.month,
        _compute_scope_key(payload),
        job.compute_availability,
        availability_only,
    )

    should_stop = _make_should_stop(job_id)
    # Advertencia no fatal (fase de disponibilidad fallida con rendimientos OK).
    done_warning: str | None = None

    try:
        if availability_only:
            # Modo availability_only: saltamos rendimientos y corremos SOLO disponibilidad.
            availability_summary, avail_error = _run_availability_for_job(
                job_id, payload, rendimientos_total=0, should_stop=should_stop
            )
            if availability_summary is None:
                # Nada mas se calculo: la falla de disponibilidad es fatal.
                _mark_error(job_id, avail_error or "Disponibilidad: fallo desconocido")
                return _refetch(job_id, job)
            summary_with_availability = MonthlyPerformanceSummary().model_copy(
                update={"availability": availability_summary}
            )
        else:
            try:
                result = calculate_monthly_performance(
                    payload,
                    progress_callback=lambda processed, total: _update_progress(job_id, processed, total),
                    should_stop=should_stop,
                    job_id=job_id,
                )
            except JobCancelled:
                raise
            except Exception as exc:
                _logger.exception("Job %s fallo en fase de rendimientos", job_id)
                _mark_error(job_id, f"{type(exc).__name__}: {exc}")
                return _refetch(job_id, job)

            summary_with_availability = result.summary

            if job.compute_availability:
                rendimientos_total = max(result.summary.total, 0)
                availability_summary, avail_error = _run_availability_for_job(
                    job_id,
                    payload,
                    rendimientos_total=rendimientos_total,
                    should_stop=should_stop,
                )
                if availability_summary is None:
                    # Rendimientos termino bien: el job queda 'done' con advertencia,
                    # no 'error'. La UI muestra error_message como warning.
                    done_warning = avail_error or "Disponibilidad: fallo desconocido"
                    _logger.warning(
                        "Job %s: rendimientos OK pero disponibilidad fallo; se marca done con advertencia (%s)",
                        job_id,
                        done_warning,
                    )
                else:
                    summary_with_availability = result.summary.model_copy(
                        update={"availability": availability_summary}
                    )
    except JobCancelled:
        # cancel_job (o el reaper) ya dejo status/error_message; no pisar nada.
        _logger.info("Job %s cancelado", job_id)
        return _refetch(job_id, job)

    if done_warning:
        _mark_done(job_id, summary_with_availability, error_message=done_warning)
    else:
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

    with db_conn(row_factory=dict_row) as conn:
        final = _fetch_job(conn, job_id)
    return final or job


def _bump_total(job_id: int, *, extra_total: int) -> None:
    """Suma `extra_total` al total_targets sin tocar processed_targets."""
    if extra_total <= 0:
        return
    try:
        with db_conn() as conn:
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
    where = [
        "c.name <> %s",
        "COALESCE(a.category, c.category, 'Ninguna') = ANY(%s)",
    ]
    params.extend([_SYSTEM_CUSTOMER_NAME, list(AVAILABILITY_CATEGORIES)])
    customer_ids = sorted({int(c) for c in (payload.customer_ids or [])})
    if customer_ids:
        where.append("a.customer_id = ANY(%s)")
        params.append(customer_ids)
    try:
        with db_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    SELECT COUNT(DISTINCT a.plate)
                    FROM vehicle_motor_assignments a
                    JOIN customers c ON c.id = a.customer_id
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
    with db_conn(row_factory=dict_row) as conn:
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
    with db_conn(row_factory=dict_row) as conn:
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
    with db_conn(row_factory=dict_row) as conn:
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
    with db_conn(row_factory=dict_row) as conn:
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


def _truncate(text: str | None, limit: int = _DIGEST_MESSAGE_MAX_CHARS) -> str | None:
    if not text:
        return None
    text = " ".join(str(text).split())
    if len(text) <= limit:
        return text
    return text[: max(1, limit - 1)] + "…"


def _fetch_recent_job_rows(hours: int) -> list[dict[str, Any]]:
    """Filas crudas de performance_calculation_jobs creadas en las ultimas `hours` horas."""
    hours = max(1, int(hours))
    with db_conn(row_factory=dict_row) as conn:
        _ensure_jobs_table(conn)
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                """
                SELECT id, status, month, triggered_by, error_message, summary,
                       created_at, finished_at
                FROM performance_calculation_jobs
                WHERE created_at >= NOW() - make_interval(hours => %s)
                ORDER BY created_at DESC;
                """,
                (hours,),
            )
            rows = cur.fetchall() or []
    return [dict(r) for r in rows]


def _job_alerts_from_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    Mapea filas de jobs a alertas del digest. Un job puede producir a lo sumo
    una alerta, en este orden de prioridad:
    - kind="error": status='error' (fallo fatal o cancelado/reapeado).
    - kind="availability_warning": status='done' con error_message (la fase de
      disponibilidad fallo pero rendimientos quedo OK).
    - kind="high_error_ratio": status='done' con summary.error/summary.total > 20%.
    """
    alerts: list[dict[str, Any]] = []
    for row in rows:
        status = str(row.get("status") or "")
        summary = row.get("summary") if isinstance(row.get("summary"), dict) else {}
        base = {
            "id": int(row["id"]),
            "month": str(row.get("month") or ""),
            "status": status,
            "triggered_by": str(row.get("triggered_by") or "ui"),
            "created_at": row.get("created_at"),
        }
        message = _truncate(row.get("error_message"))
        if status == "error":
            alerts.append({**base, "kind": "error", "detail": message or "sin mensaje"})
            continue
        if status != "done":
            continue
        if message:
            alerts.append({**base, "kind": "availability_warning", "detail": message})
            continue
        total = int(summary.get("total") or 0)
        errors = int(summary.get("error") or 0)
        if total > 0 and errors / total > _DIGEST_ERROR_RATIO:
            ratio = round(errors / total * 100.0, 1)
            alerts.append(
                {
                    **base,
                    "kind": "high_error_ratio",
                    "detail": f"{errors}/{total} placas en error ({ratio}%)",
                    "error_ratio": round(errors / total, 4),
                }
            )
    return alerts


def list_recent_job_alerts(hours: int = 24) -> list[dict[str, Any]]:
    """
    Alertas de jobs de rendimientos creados en las ultimas `hours` horas
    (ver `_job_alerts_from_rows`). Cada dict trae: id, month, status,
    triggered_by, created_at, kind, detail (error_message truncado a 120 chars).
    """
    return _job_alerts_from_rows(_fetch_recent_job_rows(hours))


def summarize_recent_jobs(hours: int = 24) -> dict[str, Any]:
    """
    Resumen para el digest diario: conteo de jobs por status + alertas, con
    una sola consulta a la tabla.
    """
    rows = _fetch_recent_job_rows(hours)
    counts: dict[str, int] = {"queued": 0, "running": 0, "done": 0, "error": 0}
    for row in rows:
        status = str(row.get("status") or "")
        counts[status] = counts.get(status, 0) + 1
    return {
        "hours": max(1, int(hours)),
        "total": len(rows),
        "counts": counts,
        "alerts": _job_alerts_from_rows(rows),
    }
