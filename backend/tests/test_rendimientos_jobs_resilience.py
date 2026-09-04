"""
Tests de resiliencia del sistema de jobs de rendimientos.

Cubren:
- Claim atomico: run_job no reejecuta un job que ya corre en otro worker.
- _mark_done / _mark_error solo escriben sobre jobs activos (no pisan cancel).
- reap_stale_jobs: SQL y conteo de jobs huerfanos.
- should_stop cooperativo: throttle de 2 s y deteccion de cancelacion.
- Fase de disponibilidad independiente: falla => done con advertencia.
- JobCancelled: no se marca ni done ni error.
- Cron: JobAlreadyRunning no llama run_job; alertas [ALERTA rendimientos].

Sin DB real: se reemplaza `db_conn` por conexiones falsas que graban el SQL,
igual que en test_availability_jobs.py.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

import pytest

from app.jobs import rendimientos_cron as cron
from app.schemas.vehicle import (
    MonthlyPerformanceSummary,
    PerformanceCalculationJob,
)
from app.services import rendimientos_jobs as jobs
from app.services.job_control import JobCancelled


# ── Helpers ─────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _enable_module_loggers():
    """
    El fixture de sesion del conftest corre `alembic upgrade`, cuyo env.py hace
    `fileConfig(alembic.ini)` con disable_existing_loggers=True: los loggers de
    app.* ya importados quedan `disabled` y caplog no recibe nada. Los
    rehabilitamos para los modulos bajo prueba.
    """
    for name in (jobs.__name__, cron.__name__):
        logging.getLogger(name).disabled = False
    yield


class _RecordingCursor:
    """Cursor falso que graba cada execute y devuelve filas/rowcount fijos."""

    def __init__(self, conn: "_RecordingConn"):
        self._conn = conn
        self.rowcount = conn.rowcount

    def execute(self, sql: str, params=None):
        self._conn.calls.append((" ".join(sql.split()), tuple(params) if params else ()))

    def fetchone(self):
        return self._conn.fetchone_row

    def fetchall(self):
        return list(self._conn.fetchall_rows)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class _RecordingConn:
    def __init__(
        self,
        *,
        fetchone_row: Any = None,
        fetchall_rows: list[Any] | None = None,
        rowcount: int = 1,
    ):
        self.fetchone_row = fetchone_row
        self.fetchall_rows = fetchall_rows or []
        self.rowcount = rowcount
        self.calls: list[tuple[str, tuple]] = []
        self.commits = 0

    def cursor(self, *args, **kwargs):
        return _RecordingCursor(self)

    def commit(self):
        self.commits += 1

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def _job(**overrides: Any) -> PerformanceCalculationJob:
    defaults: dict[str, Any] = {
        "id": 1,
        "status": "running",
        "month": "2026-06",
        "created_at": datetime(2026, 7, 11, 12, 0, 0),
    }
    defaults.update(overrides)
    return PerformanceCalculationJob(**defaults)


_ADHOC_ROW = {
    "include_adhoc": False,
    "adhoc_plates": [],
    "adhoc_filters": {},
    "adhoc_only": False,
    "availability_only": False,
}


def _patch_db_with_adhoc_row(monkeypatch, row: dict[str, Any] = _ADHOC_ROW) -> None:
    monkeypatch.setattr(
        jobs, "db_conn", lambda *args, **kwargs: _RecordingConn(fetchone_row=row)
    )


# ── (a) Claim atomico ───────────────────────────────────────────────────────


def test_run_job_returns_early_when_already_running(monkeypatch, caplog):
    """Si el claim no devuelve fila y el job esta 'running', no se reejecuta."""
    running = _job(status="running")

    monkeypatch.setattr(jobs, "_claim_job", lambda job_id: None)
    monkeypatch.setattr(jobs, "_fetch_job", lambda conn, job_id: running)
    _patch_db_with_adhoc_row(monkeypatch)

    def boom(*args, **kwargs):
        raise AssertionError("calculate_monthly_performance no debe llamarse")

    monkeypatch.setattr(jobs, "calculate_monthly_performance", boom)
    monkeypatch.setattr(jobs, "_mark_done", boom)
    monkeypatch.setattr(jobs, "_mark_error", boom)

    with caplog.at_level(logging.WARNING, logger=jobs.__name__):
        result = jobs.run_job(1)

    assert result is running
    assert any("ya esta corriendo en otro worker" in r.getMessage() for r in caplog.records)


def test_run_job_returns_finished_job_without_warning(monkeypatch, caplog):
    done = _job(status="done")
    monkeypatch.setattr(jobs, "_claim_job", lambda job_id: None)
    monkeypatch.setattr(jobs, "_fetch_job", lambda conn, job_id: done)
    _patch_db_with_adhoc_row(monkeypatch)

    with caplog.at_level(logging.WARNING, logger=jobs.__name__):
        result = jobs.run_job(1)

    assert result is done
    assert not any("otro worker" in r.getMessage() for r in caplog.records)


def test_run_job_raises_not_found_when_row_missing(monkeypatch):
    monkeypatch.setattr(jobs, "_claim_job", lambda job_id: None)
    monkeypatch.setattr(jobs, "_fetch_job", lambda conn, job_id: None)
    _patch_db_with_adhoc_row(monkeypatch)

    with pytest.raises(jobs.JobNotFound):
        jobs.run_job(99)


def test_claim_job_sql_is_conditional_on_queued(monkeypatch):
    conn = _RecordingConn(fetchone_row=None)
    monkeypatch.setattr(jobs, "db_conn", lambda *args, **kwargs: conn)

    assert jobs._claim_job(7) is None
    sql, params = conn.calls[0]
    assert "SET status = 'running'" in sql
    assert "WHERE id = %s AND status = 'queued'" in sql
    assert "RETURNING *" in sql
    assert params == (7,)


# ── (b) _mark_done / _mark_error solo sobre jobs activos ────────────────────


def test_mark_done_only_updates_running_jobs(monkeypatch):
    conn = _RecordingConn(rowcount=1)
    monkeypatch.setattr(jobs, "db_conn", lambda *args, **kwargs: conn)

    jobs._mark_done(3, MonthlyPerformanceSummary(), error_message="Disponibilidad: boom")

    sql, params = conn.calls[0]
    assert "SET status = 'done'" in sql
    assert "WHERE id = %s AND status = 'running'" in sql
    assert params[1] == "Disponibilidad: boom"
    assert params[2] == 3
    assert conn.commits == 1


def test_mark_done_without_warning_clears_error_message(monkeypatch):
    conn = _RecordingConn(rowcount=1)
    monkeypatch.setattr(jobs, "db_conn", lambda *args, **kwargs: conn)

    jobs._mark_done(3, MonthlyPerformanceSummary())

    _, params = conn.calls[0]
    assert params[1] is None


def test_mark_done_logs_warning_when_no_rows(monkeypatch, caplog):
    conn = _RecordingConn(rowcount=0)
    monkeypatch.setattr(jobs, "db_conn", lambda *args, **kwargs: conn)

    with caplog.at_level(logging.WARNING, logger=jobs.__name__):
        jobs._mark_done(3, MonthlyPerformanceSummary())

    assert any("_mark_done no actualizo filas" in r.getMessage() for r in caplog.records)


def test_mark_error_only_updates_active_jobs(monkeypatch, caplog):
    conn = _RecordingConn(rowcount=0)
    monkeypatch.setattr(jobs, "db_conn", lambda *args, **kwargs: conn)

    with caplog.at_level(logging.WARNING, logger=jobs.__name__):
        jobs._mark_error(4, "x" * 3000)

    sql, params = conn.calls[0]
    assert "SET status = 'error'" in sql
    assert "WHERE id = %s AND status IN ('queued', 'running')" in sql
    assert len(params[0]) == 2000
    assert any("_mark_error no actualizo filas" in r.getMessage() for r in caplog.records)


# ── (c) reap_stale_jobs ─────────────────────────────────────────────────────


def test_reap_stale_jobs_sql_and_count(monkeypatch):
    conn = _RecordingConn(fetchall_rows=[{"id": 5}, {"id": 9}])
    monkeypatch.setattr(jobs, "db_conn", lambda *args, **kwargs: conn)

    reaped = jobs.reap_stale_jobs(max_age_minutes=15)

    assert reaped == 2
    sql, params = conn.calls[0]
    assert "SET status = 'error'" in sql
    assert "WHERE status IN ('queued', 'running')" in sql
    assert "updated_at < NOW() - make_interval(mins => %s)" in sql
    assert "RETURNING id" in sql
    assert params[1] == 15
    assert "15 min" in params[0]
    assert conn.commits == 1


def test_reap_stale_jobs_returns_zero_when_nothing_stale(monkeypatch):
    conn = _RecordingConn(fetchall_rows=[])
    monkeypatch.setattr(jobs, "db_conn", lambda *args, **kwargs: conn)

    assert jobs.reap_stale_jobs() == 0


def test_heartbeat_sql_touches_only_running(monkeypatch):
    conn = _RecordingConn(rowcount=1)
    monkeypatch.setattr(jobs, "db_conn", lambda *args, **kwargs: conn)

    assert jobs._heartbeat(11) is True
    sql, params = conn.calls[0]
    assert "SET updated_at = NOW()" in sql
    assert "WHERE id = %s AND status = 'running'" in sql
    assert params == (11,)


def test_update_progress_touches_updated_at(monkeypatch):
    conn = _RecordingConn(rowcount=1)
    monkeypatch.setattr(jobs, "db_conn", lambda *args, **kwargs: conn)

    jobs._update_progress(1, 2, 10)

    sql, _ = conn.calls[0]
    assert "updated_at = NOW()" in sql


# ── (d) should_stop cooperativo ─────────────────────────────────────────────


def test_should_stop_throttles_and_detects_cancel(monkeypatch):
    now = {"t": 100.0}
    db_state = {"running": True, "status": "running"}
    heartbeat_calls: list[int] = []
    status_calls: list[int] = []

    def fake_heartbeat(job_id: int) -> bool:
        heartbeat_calls.append(job_id)
        return db_state["running"]

    def fake_status(job_id: int) -> str | None:
        status_calls.append(job_id)
        return db_state["status"]

    monkeypatch.setattr(jobs, "_heartbeat", fake_heartbeat)
    monkeypatch.setattr(jobs, "_fetch_job_status", fake_status)

    should_stop = jobs._make_should_stop(1, interval_s=2.0, clock=lambda: now["t"])

    # Primera llamada: consulta DB (heartbeat) y sigue vivo.
    assert should_stop() is False
    assert heartbeat_calls == [1]
    assert status_calls == []

    # Dentro de la ventana de 2 s: no toca la DB, devuelve cache.
    now["t"] = 101.0
    assert should_stop() is False
    assert heartbeat_calls == [1]

    # Cancelacion externa: status='error'. Aun en ventana -> cache (False).
    db_state["running"] = False
    db_state["status"] = "error"
    now["t"] = 101.9
    assert should_stop() is False
    assert heartbeat_calls == [1]

    # Pasada la ventana: heartbeat no actualiza, consulta status -> True.
    now["t"] = 102.5
    assert should_stop() is True
    assert heartbeat_calls == [1, 1]
    assert status_calls == [1]

    # Una vez detenido, siempre True sin volver a la DB.
    now["t"] = 200.0
    assert should_stop() is True
    assert heartbeat_calls == [1, 1]
    assert status_calls == [1]


def test_should_stop_assumes_alive_on_db_error(monkeypatch):
    def broken_heartbeat(job_id: int) -> bool:
        raise RuntimeError("db down")

    monkeypatch.setattr(jobs, "_heartbeat", broken_heartbeat)
    monkeypatch.setattr(jobs, "_fetch_job_status", lambda job_id: "error")

    should_stop = jobs._make_should_stop(1, interval_s=0.0, clock=lambda: 0.0)
    assert should_stop() is False


def test_should_stop_treats_queued_as_alive(monkeypatch):
    monkeypatch.setattr(jobs, "_heartbeat", lambda job_id: False)
    monkeypatch.setattr(jobs, "_fetch_job_status", lambda job_id: "queued")

    should_stop = jobs._make_should_stop(1, interval_s=0.0, clock=lambda: 0.0)
    assert should_stop() is False


def test_run_job_passes_should_stop_to_calculate(monkeypatch):
    job = _job(compute_availability=False)
    monkeypatch.setattr(jobs, "_claim_job", lambda job_id: job)
    monkeypatch.setattr(jobs, "_fetch_job", lambda conn, job_id: job)
    monkeypatch.setattr(jobs, "_mark_done", lambda job_id, summary, **kw: None)
    _patch_db_with_adhoc_row(monkeypatch)

    captured: dict[str, Any] = {}

    class FakeResult:
        summary = MonthlyPerformanceSummary()

    def fake_calculate(payload, progress_callback=None, should_stop=None, **_kw):
        captured["should_stop"] = should_stop
        return FakeResult()

    monkeypatch.setattr(jobs, "calculate_monthly_performance", fake_calculate)

    jobs.run_job(1)

    assert callable(captured.get("should_stop"))


# ── JobCancelled ────────────────────────────────────────────────────────────


def test_run_job_cancelled_does_not_mark_done_or_error(monkeypatch, caplog):
    job = _job(compute_availability=True)
    cancelled = _job(status="error", error_message="Cancelado por el usuario")

    monkeypatch.setattr(jobs, "_claim_job", lambda job_id: job)
    monkeypatch.setattr(jobs, "_fetch_job", lambda conn, job_id: cancelled)
    _patch_db_with_adhoc_row(monkeypatch)

    def boom(*args, **kwargs):
        raise AssertionError("no debe marcarse done/error tras cancelacion")

    monkeypatch.setattr(jobs, "_mark_done", boom)
    monkeypatch.setattr(jobs, "_mark_error", boom)
    monkeypatch.setattr(jobs, "_run_availability_for_job", boom)

    def fake_calculate(payload, progress_callback=None, should_stop=None, **_kw):
        raise JobCancelled("Job cancelado por el usuario")

    monkeypatch.setattr(jobs, "calculate_monthly_performance", fake_calculate)

    with caplog.at_level(logging.INFO, logger=jobs.__name__):
        result = jobs.run_job(1)

    assert result is cancelled
    assert any("Job 1 cancelado" in r.getMessage() for r in caplog.records)


def test_run_availability_for_job_propagates_cancel(monkeypatch):
    monkeypatch.setattr(jobs, "_count_availability_targets", lambda payload: 0)
    monkeypatch.setattr(jobs, "_update_progress", lambda *a, **k: None)
    monkeypatch.setattr(jobs, "_bump_total", lambda *a, **k: None)

    def fake_phase(**kwargs):
        raise JobCancelled("cancelado")

    monkeypatch.setattr(jobs, "run_availability_phase", fake_phase)
    payload = jobs.MonthlyPerformanceCalculateRequest(month="2026-06")

    with pytest.raises(JobCancelled):
        jobs._run_availability_for_job(1, payload, rendimientos_total=0)


# ── (e) Fase de disponibilidad independiente ────────────────────────────────


def test_run_job_availability_failure_marks_done_with_warning(monkeypatch, caplog):
    job = _job(compute_availability=True)
    monkeypatch.setattr(jobs, "_claim_job", lambda job_id: job)
    monkeypatch.setattr(jobs, "_fetch_job", lambda conn, job_id: job)
    _patch_db_with_adhoc_row(monkeypatch)

    class FakeResult:
        summary = MonthlyPerformanceSummary(total=4, calculated=4)

    def fake_calculate(payload, progress_callback=None, should_stop=None, **_kw):
        return FakeResult()

    monkeypatch.setattr(jobs, "calculate_monthly_performance", fake_calculate)
    monkeypatch.setattr(
        jobs,
        "_run_availability_for_job",
        lambda job_id, payload, **kw: (None, "Disponibilidad: CloudFleetUnavailableError: 503"),
    )

    def boom(*args, **kwargs):
        raise AssertionError("_mark_error no debe llamarse cuando rendimientos termino OK")

    monkeypatch.setattr(jobs, "_mark_error", boom)

    done_calls: list[dict[str, Any]] = []

    def fake_mark_done(job_id, summary, *, error_message=None):
        done_calls.append({"job_id": job_id, "summary": summary, "error_message": error_message})

    monkeypatch.setattr(jobs, "_mark_done", fake_mark_done)

    with caplog.at_level(logging.WARNING, logger=jobs.__name__):
        jobs.run_job(1)

    assert len(done_calls) == 1
    assert done_calls[0]["summary"].total == 4
    assert done_calls[0]["summary"].availability is None
    assert done_calls[0]["error_message"].startswith("Disponibilidad:")
    assert any("se marca done con advertencia" in r.getMessage() for r in caplog.records)


def test_run_job_availability_only_failure_marks_error(monkeypatch):
    job = _job(compute_availability=True, availability_only=True)
    monkeypatch.setattr(jobs, "_claim_job", lambda job_id: job)
    monkeypatch.setattr(jobs, "_fetch_job", lambda conn, job_id: job)
    _patch_db_with_adhoc_row(monkeypatch, {**_ADHOC_ROW, "availability_only": True})
    monkeypatch.setattr(
        jobs,
        "_run_availability_for_job",
        lambda job_id, payload, **kw: (None, "Disponibilidad: CloudFleetAuthError: 401"),
    )
    monkeypatch.setattr(jobs, "_mark_done", lambda *a, **k: pytest.fail("no debe marcarse done"))

    errors: list[tuple[int, str]] = []
    monkeypatch.setattr(jobs, "_mark_error", lambda job_id, msg: errors.append((job_id, msg)))

    jobs.run_job(1)

    assert errors == [(1, "Disponibilidad: CloudFleetAuthError: 401")]


def test_run_availability_for_job_returns_error_tuple_without_marking(monkeypatch):
    from app.clients.cloudfleet_client import CloudFleetUnavailableError

    monkeypatch.setattr(jobs, "_count_availability_targets", lambda payload: 2)
    monkeypatch.setattr(jobs, "_update_progress", lambda *a, **k: None)
    monkeypatch.setattr(jobs, "_bump_total", lambda *a, **k: None)
    monkeypatch.setattr(jobs, "_mark_error", lambda *a, **k: pytest.fail("no debe marcar error"))

    def fake_phase(**kwargs):
        raise CloudFleetUnavailableError("503")

    monkeypatch.setattr(jobs, "run_availability_phase", fake_phase)
    payload = jobs.MonthlyPerformanceCalculateRequest(month="2026-06")

    summary, error = jobs._run_availability_for_job(1, payload, rendimientos_total=3)

    assert summary is None
    assert error.startswith("Disponibilidad: CloudFleetUnavailableError")


# ── (f) Cron ────────────────────────────────────────────────────────────────


def _patch_cron_side_effects(monkeypatch) -> None:
    monkeypatch.setattr(cron, "save_connection_snapshot", lambda: {})
    monkeypatch.setattr(cron, "reap_stale_jobs", lambda **kw: 0)


def test_cron_skips_month_when_job_already_running(monkeypatch, caplog):
    _patch_cron_side_effects(monkeypatch)
    active = _job(id=42, status="running")

    def fake_create_job(payload, *, triggered_by="ui", user_id=None):
        raise jobs.JobAlreadyRunning(active)

    monkeypatch.setattr(cron, "create_job", fake_create_job)
    monkeypatch.setattr(
        cron, "run_job", lambda job_id: pytest.fail("run_job no debe llamarse con job activo")
    )

    with caplog.at_level(logging.WARNING, logger=cron.__name__):
        cron._run()

    assert any("ya tiene un job activo" in r.getMessage() for r in caplog.records)


def test_cron_calls_reaper_before_running(monkeypatch):
    order: list[str] = []
    monkeypatch.setattr(cron, "save_connection_snapshot", lambda: {})
    monkeypatch.setattr(cron, "reap_stale_jobs", lambda **kw: order.append("reap") or 1)

    def fake_create_job(payload, *, triggered_by="ui", user_id=None):
        order.append("create")
        return _job(id=1, status="queued", month=payload.month)

    monkeypatch.setattr(cron, "create_job", fake_create_job)
    monkeypatch.setattr(
        cron,
        "run_job",
        lambda job_id: order.append("run") or _job(id=job_id, status="done", summary=MonthlyPerformanceSummary()),
    )

    cron._run()

    assert order[0] == "reap"
    assert "create" in order and "run" in order


def test_cron_alert_on_error_status(caplog):
    with caplog.at_level(logging.ERROR, logger=cron.__name__):
        fired = cron._check_month_alert("2026-06", _job(status="error", error_message="boom"))

    assert fired is True
    assert any(cron.ALERT_MARKER in r.getMessage() for r in caplog.records)


def test_cron_alert_on_high_error_ratio(caplog):
    summary = MonthlyPerformanceSummary(total=10, calculated=7, error=3)
    with caplog.at_level(logging.ERROR, logger=cron.__name__):
        fired = cron._check_month_alert("2026-06", _job(status="done", summary=summary))

    assert fired is True
    assert any(cron.ALERT_MARKER in r.getMessage() for r in caplog.records)


def test_cron_no_alert_on_healthy_month(caplog):
    summary = MonthlyPerformanceSummary(total=10, calculated=9, error=1)
    with caplog.at_level(logging.ERROR, logger=cron.__name__):
        fired = cron._check_month_alert("2026-06", _job(status="done", summary=summary))

    assert fired is False
    assert not any(cron.ALERT_MARKER in r.getMessage() for r in caplog.records)
