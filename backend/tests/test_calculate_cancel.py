"""
Cancelacion cooperativa del calculo de Rendimientos.

- ``calculate_monthly_performance(should_stop=...)`` levanta ``JobCancelled``
  con granularidad por placa (via on_target_done -> _emit_progress) y hace
  rollback del grupo parcial.
- Los providers NO se tragan ``JobCancelled`` en sus ``except Exception``.

Sin base de datos: se falsifica ``psycopg.connect`` y los helpers de carga.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from app.schemas.vehicle import MonthlyPerformanceCalculateRequest, MonthlyPerformanceRecord
from app.services import rendimientos
from app.services.job_control import JobCancelled, check_stop
from app.services.performance_providers import GeotabMonthlyPerformanceProvider
from app.services.performance_types import BindingSnapshot, PerformanceTarget, ProviderCalculationResult


def _make_target(plate: str, provider_key: str = "geotab", customer_database_id: int = 1) -> PerformanceTarget:
    return PerformanceTarget(
        provider_key=provider_key,
        customer_id=1,
        customer_database_id=customer_database_id,
        client_name="Cliente A",
        database_name="db_a",
        plate=plate,
        technical_number=None,
        engine_name=None,
        username="user",
        password="pass",
        provider_config={},
    )


def _make_record(target: PerformanceTarget, month: str = "2026-01") -> MonthlyPerformanceRecord:
    return MonthlyPerformanceRecord(
        customer_id=target.customer_id,
        customer_database_id=target.customer_database_id,
        client_name=target.client_name,
        database_name=target.database_name,
        source_provider=target.provider_key,
        plate=target.plate,
        provider_vehicle_id="dev",
        period_month=month,
        calculation_status="calculated",
        warnings=[],
    )


class _StopAfter:
    """should_stop que devuelve True una vez que ``done`` llega a ``limit``."""

    def __init__(self, limit: int):
        self.limit = limit
        self.done = 0
        self.calls = 0

    def __call__(self) -> bool:
        self.calls += 1
        return self.done >= self.limit


# ── check_stop / contrato ────────────────────────────────────────────────────


def test_check_stop_none_is_noop_and_true_raises():
    check_stop(None)
    check_stop(lambda: False)
    with pytest.raises(JobCancelled):
        check_stop(lambda: True)


# ── calculate_monthly_performance ────────────────────────────────────────────


class _FakeCursor:
    def execute(self, *args, **kwargs):
        pass

    def fetchall(self):
        return []

    def fetchone(self):
        return None

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class _FakeConn:
    def __init__(self):
        self.commits = 0
        self.rollbacks = 0
        self.closed = False

    def cursor(self, *args, **kwargs):
        return _FakeCursor()

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.closed = True
        return False


class _FakeProvider:
    """Provider que emite on_target_done por placa y respeta should_stop."""

    key = "geotab"

    def __init__(self):
        self.processed: list[str] = []

    def calculate_database_rows(self, *, month, targets, on_target_done=None, should_stop=None, **_kwargs):
        rows = []
        for target in targets:
            check_stop(should_stop)
            rows.append(_make_record(target, month))
            self.processed.append(target.plate)
            if on_target_done is not None:
                try:
                    on_target_done()
                except JobCancelled:
                    raise
                except Exception:
                    pass
        return ProviderCalculationResult(records=rows, binding_updates=[])


def _wire_calculate(monkeypatch, targets: list[PerformanceTarget], provider: Any) -> _FakeConn:
    conn = _FakeConn()
    fake_psycopg = MagicMock()
    fake_psycopg.connect = MagicMock(return_value=conn)
    monkeypatch.setattr(rendimientos, "psycopg", fake_psycopg)
    monkeypatch.setattr(rendimientos, "_database_dsn", lambda: "postgresql://fake")
    monkeypatch.setattr(rendimientos, "_ensure_performance_tables", lambda conn: None)
    monkeypatch.setattr(rendimientos, "_fetch_targets", lambda conn, **kwargs: list(targets))
    monkeypatch.setattr(rendimientos, "_load_existing_records", lambda conn, month, targets: {})
    monkeypatch.setattr(rendimientos, "_load_binding_map", lambda conn, targets: {})
    monkeypatch.setattr(rendimientos, "get_monthly_performance_provider", lambda key: provider)
    monkeypatch.setattr(rendimientos, "_upsert_monthly_record", lambda conn, record: record)
    monkeypatch.setattr(rendimientos, "_upsert_binding", lambda conn, **kwargs: None)
    return conn


def _payload() -> MonthlyPerformanceCalculateRequest:
    return MonthlyPerformanceCalculateRequest(month="2026-01", include_adhoc=False, force_recalculate=True)


def test_calculate_raises_job_cancelled_after_two_targets_and_rolls_back(monkeypatch):
    targets = [_make_target(f"PLT00{i}") for i in range(1, 6)]
    provider = _FakeProvider()
    conn = _wire_calculate(monkeypatch, targets, provider)

    stop = _StopAfter(limit=2)
    progress: list[tuple[int, int]] = []

    def _progress(processed: int, total: int) -> None:
        progress.append((processed, total))
        # El callback cuenta placas terminadas; should_stop se dispara al llegar a 2.
        stop.done = processed

    with pytest.raises(JobCancelled):
        rendimientos.calculate_monthly_performance(
            _payload(), progress_callback=_progress, should_stop=stop, job_id=42
        )

    # Solo se procesaron 2 placas: la cancelacion cae en on_target_done -> _emit_progress.
    assert provider.processed == ["PLT001", "PLT002"]
    assert progress[-1] == (2, 5)
    # El grupo parcial se descarta (rollback) y no se commitea nada.
    assert conn.rollbacks == 1
    assert conn.commits == 0
    assert conn.closed is True


def test_calculate_stops_before_starting_when_should_stop_is_true(monkeypatch):
    targets = [_make_target("PLT001")]
    provider = _FakeProvider()
    _wire_calculate(monkeypatch, targets, provider)

    with pytest.raises(JobCancelled):
        rendimientos.calculate_monthly_performance(_payload(), should_stop=lambda: True)

    assert provider.processed == []


def test_calculate_without_should_stop_completes_and_commits(monkeypatch):
    targets = [_make_target(f"PLT00{i}") for i in range(1, 4)]
    provider = _FakeProvider()
    conn = _wire_calculate(monkeypatch, targets, provider)

    response = rendimientos.calculate_monthly_performance(_payload())

    assert [row.plate for row in response.rows] == ["PLT001", "PLT002", "PLT003"]
    assert response.summary.calculated == 3
    assert conn.rollbacks == 0
    assert conn.commits >= 1


def test_progress_callback_generic_error_is_swallowed_but_job_cancelled_propagates(monkeypatch):
    targets = [_make_target("PLT001"), _make_target("PLT002")]
    provider = _FakeProvider()
    _wire_calculate(monkeypatch, targets, provider)

    def _noisy_progress(processed: int, total: int) -> None:
        raise RuntimeError("callback roto")

    # Un fallo generico del callback no rompe el calculo.
    response = rendimientos.calculate_monthly_performance(_payload(), progress_callback=_noisy_progress)
    assert len(response.rows) == 2

    def _cancelling_progress(processed: int, total: int) -> None:
        if processed >= 1:
            raise JobCancelled("cancelado desde el callback")

    provider2 = _FakeProvider()
    _wire_calculate(monkeypatch, targets, provider2)
    with pytest.raises(JobCancelled):
        rendimientos.calculate_monthly_performance(_payload(), progress_callback=_cancelling_progress)
    assert provider2.processed == ["PLT001"]


def test_calculate_signature_accepts_should_stop_and_job_id():
    import inspect

    params = inspect.signature(rendimientos.calculate_monthly_performance).parameters
    assert "should_stop" in params and params["should_stop"].default is None
    assert "job_id" in params and params["job_id"].default is None


# ── provider Geotab: no se traga JobCancelled ────────────────────────────────


def _run_geotab(targets, *, should_stop=None, on_target_done=None, calculate_side_effect=None):
    provider = GeotabMonthlyPerformanceProvider()
    bindings = {
        ("geotab", t.customer_database_id, t.plate): BindingSnapshot("M1", "resolved", is_manual=True)
        for t in targets
    }
    calculate = MagicMock(side_effect=calculate_side_effect or (lambda **kw: _make_record(kw["target"])))
    with patch("app.services.performance_providers.get_cached_devices", MagicMock(return_value=[])), patch(
        "app.services.performance_providers.get_authenticated_client", return_value=MagicMock()
    ), patch("app.services.performance_providers._calculate_geotab_vehicle_record", calculate):
        result = provider.calculate_database_rows(
            month="2026-01",
            year=2026,
            month_number=1,
            previous_month="2025-12",
            targets=targets,
            previous_records={},
            bindings=bindings,
            on_target_done=on_target_done,
            should_stop=should_stop,
        )
    return result, calculate


def test_geotab_provider_checks_should_stop_per_target():
    targets = [_make_target(f"PLT00{i}") for i in range(1, 5)]
    stop = _StopAfter(limit=2)

    def _done():
        stop.done += 1

    with pytest.raises(JobCancelled):
        _run_geotab(targets, should_stop=stop, on_target_done=_done)
    assert stop.done == 2


def test_geotab_provider_propagates_job_cancelled_from_on_target_done():
    targets = [_make_target("PLT001"), _make_target("PLT002")]

    def _done():
        raise JobCancelled("cancelado")

    with pytest.raises(JobCancelled):
        _run_geotab(targets, on_target_done=_done)


def test_geotab_provider_propagates_job_cancelled_raised_inside_calculation():
    """El `except Exception` por placa no debe capturar JobCancelled."""
    targets = [_make_target("PLT001")]

    def _boom(**kwargs):
        raise JobCancelled("cancelado en medio de la placa")

    with pytest.raises(JobCancelled):
        _run_geotab(targets, calculate_side_effect=_boom)


def test_geotab_provider_still_converts_generic_errors_to_error_rows():
    targets = [_make_target("PLT001")]

    def _boom(**kwargs):
        raise RuntimeError("api caida")

    result, _ = _run_geotab(targets, calculate_side_effect=_boom)
    assert len(result.records) == 1
    assert result.records[0].calculation_status == "error"
    assert "api caida" in result.records[0].warnings[0]
