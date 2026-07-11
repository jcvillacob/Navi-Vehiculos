"""
Tests del job de rendimientos en modo availability_only.

Cubren:
- Scope key distinto para availability_only vs compute_availability estandar.
- Schema request/job expone availability_only con default False.
- run_job en modo availability_only NO llama calculate_monthly_performance.
- run_job en modo normal SI llama calculate_monthly_performance.

Todas las funciones que leen/escriben PostgreSQL se reemplazan con monkeypatch
sobre el namespace de `app.services.rendimientos_jobs`.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

import pytest

from app.schemas.vehicle import (
    MonthlyPerformanceCalculateRequest,
    MonthlyPerformanceSummary,
    PerformanceCalculationJob,
)
from app.services import rendimientos_jobs as jobs


# ── Helpers ─────────────────────────────────────────────────────────────────


class _FakeCursor:
    """Cursor falso que devuelve la fila ad-hoc/availability_only del job."""

    def __init__(self, row: dict[str, Any] | None = None):
        self._row = row

    def execute(self, sql: str, params=None):
        pass

    def fetchone(self):
        return self._row

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class _FakeConn:
    """Conexion falsa para los `with db_conn(...)` de run_job."""

    def __init__(self, row: dict[str, Any] | None = None):
        self._row = row

    def cursor(self, *args, **kwargs):
        return _FakeCursor(self._row)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def _queued_job(**overrides: Any) -> PerformanceCalculationJob:
    defaults: dict[str, Any] = {
        "id": 1,
        "status": "queued",
        "month": "2026-06",
        "created_at": datetime(2026, 7, 11, 12, 0, 0),
    }
    defaults.update(overrides)
    return PerformanceCalculationJob(**defaults)


# ── _compute_scope_key ──────────────────────────────────────────────────────


def test_compute_scope_key_availability_only_suffix():
    """availability_only produce el sufijo 'avonly' y difiere de 'av'."""
    base = MonthlyPerformanceCalculateRequest(month="2026-06", customer_ids=[1])
    av = base.model_copy(update={"compute_availability": True})
    avonly = base.model_copy(update={"availability_only": True})

    av_key = jobs._compute_scope_key(av)
    avonly_key = jobs._compute_scope_key(avonly)

    assert avonly_key.endswith("avonly|std")
    assert av_key.endswith("av|std")
    assert av_key != avonly_key


# ── MonthlyPerformanceCalculateRequest ──────────────────────────────────────


def test_request_availability_only_defaults():
    """availability_only es False por defecto y se expone cuando es True."""
    req = MonthlyPerformanceCalculateRequest(month="2026-06")
    assert req.availability_only is False

    req_true = MonthlyPerformanceCalculateRequest(month="2026-06", availability_only=True)
    assert req_true.availability_only is True


# ── run_job availability_only ───────────────────────────────────────────────


def test_run_job_availability_only_skips_performance(monkeypatch):
    """En modo availability_only no se llama calculate_monthly_performance."""
    job = _queued_job(
        customer_ids=[7],
        compute_availability=True,
        availability_only=True,
    )

    monkeypatch.setattr(jobs, "_fetch_job", lambda conn, job_id: job)
    monkeypatch.setattr(jobs, "_mark_running", lambda job_id: None)
    monkeypatch.setattr(jobs, "_bump_total", lambda job_id, *, extra_total: None)
    monkeypatch.setattr(jobs, "_count_availability_targets", lambda payload: 3)

    progress_calls: list[tuple[int, int, int]] = []
    monkeypatch.setattr(
        jobs, "_update_progress", lambda job_id, processed, total: progress_calls.append((job_id, processed, total))
    )

    captured_phase: dict[str, Any] = {}

    def fake_run_availability_phase(*, month: str, customer_ids: list[int], progress_callback):
        captured_phase["month"] = month
        captured_phase["customer_ids"] = customer_ids
        progress_callback(2)
        return {"total": 3, "calculated": 2, "no_orders": 1, "not_in_cloudfleet": 0, "error": 0}

    monkeypatch.setattr(jobs, "run_availability_phase", fake_run_availability_phase)

    def fake_calculate_monthly_performance(payload, progress_callback=None):
        raise AssertionError("no debe llamarse")

    monkeypatch.setattr(jobs, "calculate_monthly_performance", fake_calculate_monthly_performance)

    done_summary: MonthlyPerformanceSummary | None = None

    def fake_mark_done(job_id: int, summary: MonthlyPerformanceSummary):
        nonlocal done_summary
        done_summary = summary

    monkeypatch.setattr(jobs, "_mark_done", fake_mark_done)

    adhoc_row = {
        "include_adhoc": False,
        "adhoc_plates": [],
        "adhoc_filters": {},
        "adhoc_only": False,
        "availability_only": True,
    }
    monkeypatch.setattr(jobs, "db_conn", lambda row_factory=None: _FakeConn(adhoc_row))

    jobs.run_job(1)

    assert captured_phase.get("month") == "2026-06"
    assert captured_phase.get("customer_ids") == [7]
    assert progress_calls == [(1, 0, 3), (1, 2, 3)]
    assert done_summary is not None
    assert done_summary.availability is not None
    assert done_summary.availability.total == 3


# ── run_job modo normal ─────────────────────────────────────────────────────


def test_run_job_normal_calls_performance(monkeypatch):
    """En modo normal run_job delega en calculate_monthly_performance."""
    job = _queued_job(
        customer_ids=[7],
        compute_availability=False,
        availability_only=False,
    )

    monkeypatch.setattr(jobs, "_fetch_job", lambda conn, job_id: job)
    monkeypatch.setattr(jobs, "_mark_running", lambda job_id: None)
    monkeypatch.setattr(jobs, "_mark_done", lambda job_id, summary: None)

    called: dict[str, bool] = {}

    class FakeResult:
        summary = MonthlyPerformanceSummary()

    def fake_calculate_monthly_performance(payload, progress_callback=None):
        called["performance"] = True
        return FakeResult()

    monkeypatch.setattr(jobs, "calculate_monthly_performance", fake_calculate_monthly_performance)

    adhoc_row = {
        "include_adhoc": False,
        "adhoc_plates": [],
        "adhoc_filters": {},
        "adhoc_only": False,
        "availability_only": False,
    }
    monkeypatch.setattr(jobs, "db_conn", lambda row_factory=None: _FakeConn(adhoc_row))

    jobs.run_job(2)

    assert called.get("performance") is True
