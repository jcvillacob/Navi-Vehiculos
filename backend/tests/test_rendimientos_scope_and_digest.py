"""
Tests de solape de jobs (R8), digest de jobs (R10) y connection-stats por
rango (F8). Sin DB real ni red: conexiones/cursores falsos que graban el SQL,
igual que en test_rendimientos_jobs_resilience.py.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pytest

from app.schemas.vehicle import (
    MonthlyPerformanceCalculateRequest,
    PerformanceCalculationJob,
)
from app.services import motor_catalog
from app.services import operational_alerts
from app.services import rendimientos_jobs as jobs


# ── Helpers ─────────────────────────────────────────────────────────────────


class _RecordingCursor:
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
        self.rollbacks = 0

    def cursor(self, *args, **kwargs):
        return _RecordingCursor(self)

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def _req(**overrides: Any) -> MonthlyPerformanceCalculateRequest:
    defaults: dict[str, Any] = {"month": "2026-06"}
    defaults.update(overrides)
    return MonthlyPerformanceCalculateRequest(**defaults)


def _job(**overrides: Any) -> PerformanceCalculationJob:
    defaults: dict[str, Any] = {
        "id": 1,
        "status": "running",
        "month": "2026-06",
        "created_at": datetime(2026, 7, 11, 12, 0, 0),
    }
    defaults.update(overrides)
    return PerformanceCalculationJob(**defaults)


def _job_row(**overrides: Any) -> dict[str, Any]:
    """Fila cruda de performance_calculation_jobs como la devuelve dict_row."""
    row: dict[str, Any] = {
        "id": 7,
        "status": "running",
        "month": "2026-06",
        "scope_key": "all|any|av|std",
        "customer_id": None,
        "customer_ids": [],
        "customer_database_id": None,
        "force_recalculate": True,
        "compute_availability": True,
        "include_adhoc": False,
        "adhoc_plates": [],
        "adhoc_filters": {},
        "adhoc_only": False,
        "availability_only": False,
        "total_targets": 10,
        "processed_targets": 3,
        "summary": None,
        "error_message": None,
        "triggered_by": "cron",
        "created_by_user_id": None,
        "created_at": datetime(2026, 6, 1, 5, 0, 0, tzinfo=timezone.utc),
        "started_at": None,
        "finished_at": None,
        "updated_at": datetime(2026, 6, 1, 5, 0, 0, tzinfo=timezone.utc),
    }
    row.update(overrides)
    return row


def _patch_db(monkeypatch, conn: _RecordingConn) -> None:
    monkeypatch.setattr(jobs, "db_conn", lambda *a, **kw: conn)
    monkeypatch.setattr(jobs, "_ensure_jobs_table", lambda *a, **kw: None)


# ── _jobs_overlap: matriz ───────────────────────────────────────────────────


def test_overlap_ui_noav_all_vs_cron_av_all_conflicts():
    """El caso original R8: UI sin disponibilidad y cron con disponibilidad, mismo mes."""
    ui = _req(compute_availability=False)
    cron = _job(compute_availability=True, triggered_by="cron")
    assert jobs._jobs_overlap(ui, cron) is True
    assert jobs._jobs_overlap(cron, ui) is True


def test_overlap_all_customers_vs_subset_conflicts():
    assert jobs._jobs_overlap(_req(), _job(customer_ids=[3])) is True
    assert jobs._jobs_overlap(_req(customer_ids=[3]), _job()) is True
    # customer_id singular tambien cuenta como subconjunto.
    assert jobs._jobs_overlap(_req(customer_id=9), _job()) is True


def test_overlap_disjoint_customers_do_not_conflict():
    assert jobs._jobs_overlap(_req(customer_ids=[1, 2]), _job(customer_ids=[3, 4])) is False
    assert jobs._jobs_overlap(_req(customer_id=1), _job(customer_ids=[3])) is False


def test_overlap_intersecting_customers_conflict():
    assert jobs._jobs_overlap(_req(customer_ids=[1, 2]), _job(customer_ids=[2, 3])) is True
    assert jobs._jobs_overlap(_req(customer_id=2), _job(customer_ids=[2])) is True


def test_overlap_same_customer_database_conflicts_and_different_does_not():
    assert jobs._jobs_overlap(_req(customer_database_id=5), _job(customer_database_id=5)) is True
    assert jobs._jobs_overlap(_req(customer_database_id=5), _job(customer_database_id=6)) is False
    # Database explicita distinta gana aunque los clientes se solapen.
    assert (
        jobs._jobs_overlap(
            _req(customer_ids=[1], customer_database_id=5),
            _job(customer_ids=[1], customer_database_id=6),
        )
        is False
    )


def test_overlap_database_only_vs_customers_only_is_conservative():
    """Relacion database<->cliente desconocida aqui: se asume solape."""
    assert jobs._jobs_overlap(_req(customer_database_id=5), _job(customer_ids=[1])) is True


def test_overlap_availability_only_vs_rendimientos_only_never_conflicts():
    avonly = _req(availability_only=True)
    rend = _job(compute_availability=False)
    assert jobs._jobs_overlap(avonly, rend) is False
    assert jobs._jobs_overlap(rend, avonly) is False


def test_overlap_full_with_availability_vs_availability_only_conflicts():
    full = _req(compute_availability=True)
    avonly = _job(availability_only=True, compute_availability=True)
    assert jobs._jobs_overlap(full, avonly) is True
    assert jobs._jobs_overlap(avonly, full) is True


def test_overlap_two_availability_only_disjoint_customers_do_not_conflict():
    a = _req(availability_only=True, customer_ids=[1])
    b = _job(availability_only=True, compute_availability=True, customer_ids=[2])
    assert jobs._jobs_overlap(a, b) is False


def test_overlap_adhoc_only_vs_standard_does_not_conflict_but_include_adhoc_does():
    std = _job()
    assert jobs._jobs_overlap(_req(adhoc_only=True), std) is False
    assert jobs._jobs_overlap(_req(include_adhoc=True), std) is True
    assert jobs._jobs_overlap(_req(adhoc_only=True), _job(include_adhoc=True)) is True


def test_overlap_different_month_never_conflicts():
    assert jobs._jobs_overlap(_req(month="2026-06"), _job(month="2026-07")) is False


def test_overlap_accepts_two_jobs_or_two_payloads():
    assert jobs._jobs_overlap(_job(), _job(customer_ids=[1])) is True
    assert jobs._jobs_overlap(_req(customer_ids=[1]), _req(customer_ids=[2])) is False


# ── create_job: 409 por solape ──────────────────────────────────────────────


def test_create_job_raises_when_active_job_overlaps(monkeypatch):
    """Cron activo (todos, av) bloquea un job UI (todos, noav) del mismo mes."""
    active_row = _job_row(id=7, compute_availability=True, triggered_by="cron")
    conn = _RecordingConn(fetchall_rows=[active_row])
    _patch_db(monkeypatch, conn)

    with pytest.raises(jobs.JobAlreadyRunning) as excinfo:
        jobs.create_job(_req(compute_availability=False), triggered_by="ui", user_id=3)

    assert excinfo.value.job.id == 7
    assert excinfo.value.job.triggered_by == "cron"
    # No se intento el INSERT.
    assert not any(sql.startswith("INSERT INTO performance_calculation_jobs") for sql, _ in conn.calls)
    # La consulta de activos es por mes (no por scope_key exacto).
    month_sql, month_params = conn.calls[0]
    assert "WHERE month = %s AND status IN ('queued','running')" in month_sql
    assert month_params == ("2026-06",)


def test_create_job_raises_for_subset_customer_when_all_customers_active(monkeypatch):
    active_row = _job_row(id=8, scope_key="all|any|noav|std", compute_availability=False)
    conn = _RecordingConn(fetchall_rows=[active_row])
    _patch_db(monkeypatch, conn)

    with pytest.raises(jobs.JobAlreadyRunning) as excinfo:
        jobs.create_job(_req(customer_ids=[42]))
    assert excinfo.value.job.id == 8


def test_create_job_inserts_when_active_job_is_disjoint(monkeypatch):
    """Job activo de clientes {1} y nuevo de {2}: no hay conflicto, se inserta."""
    active_row = _job_row(id=9, customer_ids=[1], scope_key="1|any|noav|std", compute_availability=False)
    inserted = _job_row(id=10, status="queued", customer_ids=[2], scope_key="2|any|noav|std")
    conn = _RecordingConn(fetchall_rows=[active_row], fetchone_row=inserted)
    _patch_db(monkeypatch, conn)

    job = jobs.create_job(_req(customer_ids=[2]))

    assert job.id == 10
    assert job.status == "queued"
    insert_calls = [c for c in conn.calls if c[0].startswith("INSERT INTO performance_calculation_jobs")]
    assert len(insert_calls) == 1
    assert conn.commits == 1


def test_create_job_availability_only_does_not_block_on_rendimientos_job(monkeypatch):
    active_row = _job_row(id=11, scope_key="all|any|noav|std", compute_availability=False)
    inserted = _job_row(id=12, status="queued", availability_only=True, scope_key="all|any|avonly|std")
    conn = _RecordingConn(fetchall_rows=[active_row], fetchone_row=inserted)
    _patch_db(monkeypatch, conn)

    job = jobs.create_job(_req(availability_only=True))

    assert job.id == 12
    assert job.availability_only is True


# ── list_recent_job_alerts / summarize_recent_jobs ──────────────────────────


def _digest_rows() -> list[dict[str, Any]]:
    return [
        {
            "id": 1,
            "status": "error",
            "month": "2026-06",
            "triggered_by": "cron",
            "error_message": "GeotabUnavailable: " + ("x" * 300),
            "summary": None,
            "created_at": datetime(2026, 6, 2, 5, 0, tzinfo=timezone.utc),
            "finished_at": None,
        },
        {
            "id": 2,
            "status": "done",
            "month": "2026-06",
            "triggered_by": "ui",
            "error_message": "Disponibilidad: CloudFleetUnavailableError: timeout",
            "summary": {"total": 10, "calculated": 10, "error": 0},
            "created_at": datetime(2026, 6, 2, 6, 0, tzinfo=timezone.utc),
            "finished_at": None,
        },
        {
            "id": 3,
            "status": "done",
            "month": "2026-05",
            "triggered_by": "ui",
            "error_message": None,
            "summary": {"total": 10, "calculated": 7, "error": 3},
            "created_at": datetime(2026, 6, 2, 7, 0, tzinfo=timezone.utc),
            "finished_at": None,
        },
        {
            "id": 4,
            "status": "done",
            "month": "2026-06",
            "triggered_by": "ui",
            "error_message": None,
            "summary": {"total": 10, "calculated": 9, "error": 1},
            "created_at": datetime(2026, 6, 2, 8, 0, tzinfo=timezone.utc),
            "finished_at": None,
        },
        {
            "id": 5,
            "status": "running",
            "month": "2026-06",
            "triggered_by": "ui",
            "error_message": None,
            "summary": None,
            "created_at": datetime(2026, 6, 2, 9, 0, tzinfo=timezone.utc),
            "finished_at": None,
        },
    ]


def test_list_recent_job_alerts_sql_and_mapping(monkeypatch):
    conn = _RecordingConn(fetchall_rows=_digest_rows())
    _patch_db(monkeypatch, conn)

    alerts = jobs.list_recent_job_alerts(hours=24)

    sql, params = conn.calls[0]
    assert "FROM performance_calculation_jobs" in sql
    assert "created_at >= NOW() - make_interval(hours => %s)" in sql
    assert params == (24,)

    by_id = {a["id"]: a for a in alerts}
    assert set(by_id) == {1, 2, 3}

    assert by_id[1]["kind"] == "error"
    assert by_id[1]["triggered_by"] == "cron"
    assert by_id[1]["month"] == "2026-06"
    assert len(by_id[1]["detail"]) <= 120
    assert by_id[1]["detail"].startswith("GeotabUnavailable:")

    assert by_id[2]["kind"] == "availability_warning"
    assert "CloudFleetUnavailableError" in by_id[2]["detail"]

    assert by_id[3]["kind"] == "high_error_ratio"
    assert by_id[3]["error_ratio"] == 0.3
    assert "3/10" in by_id[3]["detail"]


def test_list_recent_job_alerts_hours_is_clamped_to_min_one(monkeypatch):
    conn = _RecordingConn(fetchall_rows=[])
    _patch_db(monkeypatch, conn)
    assert jobs.list_recent_job_alerts(hours=0) == []
    assert conn.calls[0][1] == (1,)


def test_summarize_recent_jobs_counts_and_alerts(monkeypatch):
    conn = _RecordingConn(fetchall_rows=_digest_rows())
    _patch_db(monkeypatch, conn)

    summary = jobs.summarize_recent_jobs(hours=24)

    assert summary["hours"] == 24
    assert summary["total"] == 5
    assert summary["counts"] == {"queued": 0, "running": 1, "done": 3, "error": 1}
    assert [a["kind"] for a in summary["alerts"]] == ["error", "availability_warning", "high_error_ratio"]
    # Una sola consulta para counts + alertas.
    assert len(conn.calls) == 1


# ── Digest: formato de la seccion ───────────────────────────────────────────


def test_format_rendimientos_digest_lines_full_section(monkeypatch):
    conn = _RecordingConn(fetchall_rows=_digest_rows())
    _patch_db(monkeypatch, conn)
    summary = jobs.summarize_recent_jobs(hours=24)

    lines = operational_alerts.format_rendimientos_digest_lines(summary)

    assert lines[0] == "Rendimientos — últimas 24 h: 5 job(s)"
    assert lines[1] == " - Por status: queued=0, running=1, done=3, error=1"
    joined = "\n".join(lines)
    assert "Jobs en error (1):" in joined
    assert "job=1 mes=2026-06 origen=cron: GeotabUnavailable:" in joined
    assert "Jobs done con advertencia de disponibilidad (1):" in joined
    assert "job=2 mes=2026-06 origen=ui: Disponibilidad: CloudFleetUnavailableError: timeout" in joined
    assert "Jobs done con >20% de placas en error (1):" in joined
    assert "job=3 mes=2026-05 origen=ui: 3/10 placas en error (30.0%)" in joined
    # El job sano (id=4) y el running (id=5) no aparecen como alerta.
    assert "job=4" not in joined
    assert "job=5" not in joined


def test_format_rendimientos_digest_lines_empty_period():
    lines = operational_alerts.format_rendimientos_digest_lines(
        {"hours": 24, "total": 0, "counts": {}, "alerts": []}
    )
    assert lines == ["Rendimientos — últimas 24 h: 0 job(s)", " - Sin jobs en el periodo."]


def test_format_rendimientos_digest_lines_healthy_jobs():
    lines = operational_alerts.format_rendimientos_digest_lines(
        {"hours": 24, "total": 2, "counts": {"done": 2}, "alerts": []}
    )
    assert lines[-1] == " - Sin jobs con problemas."


def test_get_rendimientos_jobs_digest_uses_summary(monkeypatch):
    monkeypatch.setattr(
        operational_alerts,
        "summarize_recent_jobs",
        lambda hours: {"hours": hours, "total": 0, "counts": {}, "alerts": []},
    )
    digest = operational_alerts.get_rendimientos_jobs_digest(hours=12)
    assert digest["hours"] == 12
    assert digest["lines"][0].startswith("Rendimientos — últimas 12 h")


# ── connection-stats por rango ──────────────────────────────────────────────


def test_connection_stats_month_range_inclusive_and_swapped():
    assert motor_catalog._connection_stats_month_range("2026-06", "2026-08") == [
        "2026-06",
        "2026-07",
        "2026-08",
    ]
    assert motor_catalog._connection_stats_month_range("2026-08", "2026-06") == [
        "2026-06",
        "2026-07",
        "2026-08",
    ]
    assert motor_catalog._connection_stats_month_range("2025-11", "2026-02") == [
        "2025-11",
        "2025-12",
        "2026-01",
        "2026-02",
    ]
    assert motor_catalog._connection_stats_month_range("2026-06", "2026-06") == ["2026-06"]


def test_connection_stats_month_range_accepts_exactly_12_and_rejects_13():
    twelve = motor_catalog._connection_stats_month_range("2025-07", "2026-06")
    assert len(twelve) == 12
    with pytest.raises(ValueError, match="12 meses"):
        motor_catalog._connection_stats_month_range("2025-06", "2026-06")


def test_connection_stats_month_range_rejects_bad_format():
    with pytest.raises(ValueError):
        motor_catalog._connection_stats_month_range("2026-13", "2026-06")
    with pytest.raises(ValueError):
        motor_catalog._connection_stats_month_range("junio", "2026-06")


class _StatsCursor:
    """
    Cursor falso para _connection_stats_for_month: responde segun el SQL
    (agregado por placa, ultimo status por placa, racha por placa).
    """

    def __init__(self, data: dict[str, dict[str, Any]]):
        # data: month_start -> {"agg": [...], "latest": [...], "streak": {plate: [statuses]}}
        self._data = data
        self._pending: list[Any] = []
        self.calls: list[tuple[str, tuple]] = []

    def execute(self, sql: str, params=None):
        sql_norm = " ".join(sql.split())
        params = tuple(params or ())
        self.calls.append((sql_norm, params))
        if sql_norm.startswith("SELECT plate, COUNT(*)"):
            self._pending = list(self._data.get(params[0], {}).get("agg", []))
        elif sql_norm.startswith("SELECT DISTINCT ON (plate)"):
            self._pending = list(self._data.get(params[0], {}).get("latest", []))
        elif sql_norm.startswith("SELECT status FROM vehicle_connection_log WHERE plate = %s"):
            plate, month_start = params[0], params[1]
            statuses = self._data.get(month_start, {}).get("streak", {}).get(plate, [])
            self._pending = [{"status": s} for s in statuses]
        else:  # pragma: no cover
            raise AssertionError(f"SQL inesperado: {sql_norm}")

    def fetchall(self):
        rows, self._pending = self._pending, []
        return rows

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class _StatsConn:
    def __init__(self, cursor: _StatsCursor):
        self._cursor = cursor

    def cursor(self, *a, **kw):
        return self._cursor

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def _agg(plate: str, connected: int, disconnected: int, not_found: int = 0, error: int = 0) -> dict[str, Any]:
    return {
        "plate": plate,
        "days_checked": connected + disconnected,
        "days_connected": connected,
        "days_disconnected": disconnected,
        "days_not_found": not_found,
        "days_error": error,
    }


def _patch_stats_db(monkeypatch, cursor: _StatsCursor) -> None:
    class _FakePsycopg:
        @staticmethod
        def connect(*a, **kw):
            return _StatsConn(cursor)

    monkeypatch.setattr(motor_catalog, "psycopg", _FakePsycopg)
    monkeypatch.setattr(motor_catalog, "_database_dsn", lambda: "postgresql://fake")
    monkeypatch.setattr(motor_catalog, "_ensure_motor_tables", lambda conn: None)


def test_get_connection_stats_range_shape_and_single_connection(monkeypatch):
    cursor = _StatsCursor(
        {
            "2026-06-01": {
                "agg": [_agg("ABC123", 20, 5, not_found=1)],
                "latest": [{"plate": "ABC123", "check_date": "2026-06-30", "status": "disconnected"}],
                "streak": {"ABC123": ["disconnected", "disconnected", "connected"]},
            },
            "2026-07-01": {
                "agg": [_agg("ABC123", 10, 0), _agg("XYZ789", 0, 3)],
                "latest": [
                    {"plate": "ABC123", "check_date": "2026-07-31", "status": "connected"},
                    {"plate": "XYZ789", "check_date": "2026-07-03", "status": "disconnected"},
                ],
                "streak": {"XYZ789": ["disconnected", "disconnected", "disconnected"]},
            },
        }
    )
    _patch_stats_db(monkeypatch, cursor)

    result = motor_catalog.get_connection_stats_range("2026-06", "2026-07")

    assert list(result.keys()) == ["2026-06", "2026-07"]
    june = {r["plate"]: r for r in result["2026-06"]}
    july = {r["plate"]: r for r in result["2026-07"]}

    assert june["ABC123"]["days_checked"] == 25
    assert june["ABC123"]["connection_pct"] == 80.0
    assert june["ABC123"]["consecutive_disconnected"] == 2
    assert june["ABC123"]["latest_status"] == "disconnected"
    assert june["ABC123"]["days_not_found"] == 1

    assert july["ABC123"]["connection_pct"] == 100.0
    assert july["ABC123"]["consecutive_disconnected"] == 0
    assert july["XYZ789"]["connection_pct"] == 0
    assert july["XYZ789"]["consecutive_disconnected"] == 3

    # Misma forma que get_connection_stats (un mes).
    single = {r["plate"]: r for r in motor_catalog.get_connection_stats("2026-06")}
    assert single["ABC123"] == june["ABC123"]
    assert set(single["ABC123"].keys()) == {
        "plate",
        "days_checked",
        "days_connected",
        "days_disconnected",
        "days_not_found",
        "days_error",
        "connection_pct",
        "consecutive_disconnected",
        "latest_status",
        "latest_check_date",
    }


def test_get_connection_stats_range_rejects_more_than_12_months_before_connecting(monkeypatch):
    def _boom(*a, **kw):  # pragma: no cover
        raise AssertionError("no debe abrir conexion con rango invalido")

    class _FakePsycopg:
        connect = staticmethod(_boom)

    monkeypatch.setattr(motor_catalog, "psycopg", _FakePsycopg)
    with pytest.raises(ValueError, match="12 meses"):
        motor_catalog.get_connection_stats_range("2025-01", "2026-06")


def test_get_connection_stats_range_empty_month_returns_empty_list(monkeypatch):
    cursor = _StatsCursor({})
    _patch_stats_db(monkeypatch, cursor)
    result = motor_catalog.get_connection_stats_range("2026-06", "2026-06")
    assert result == {"2026-06": []}
