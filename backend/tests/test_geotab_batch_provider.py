"""Geotab por lotes (G1/G2/G5/G7/R6), sin red ni DB.

- `get_month_data_bundles`: chunks cross-vehicle, rebanado por device, ventanas
  borde de combustible, fallback por device solo ante error fatal.
- Provider: pool acotado de workers, on_target_done en el hilo principal,
  cancelacion entre futures, circuit breaker por database.
- Rate limiter: acquire con cost=len(calls) y penalize ante OverLimit.
"""

from __future__ import annotations

import threading
from datetime import timedelta
from typing import Any
from unittest.mock import patch

import pytest
import requests
from mygeotab.exceptions import MyGeotabException

from app.clients import geotab_client
from app.clients.geotab_client import (
    get_month_data_bundle,
    get_month_data_bundles,
    multi_call_with_retry,
)
from app.schemas.vehicle import MonthlyPerformanceRecord
from app.services import performance_providers
from app.services.job_control import JobCancelled
from app.services.performance_providers import GeotabMonthlyPerformanceProvider
from app.services.performance_types import BindingSnapshot, PerformanceTarget

FROM_DATE = "2026-08-01T05:00:00.000Z"
TO_DATE = "2026-09-01T05:00:00.000Z"
DIAGS = {
    "odometer": "DiagnosticOdometerId",
    "engine_hours": "DiagnosticEngineHoursId",
    "total_fuel": "DiagnosticTotalFuelUsedId",
    "device_fuel": "DiagnosticDeviceTotalFuelId",
}
FUEL_KEYS = frozenset({"total_fuel", "device_fuel"})


class NoopLimiter:
    capacity = 900

    def __init__(self):
        self.acquires: list[tuple[str, int, float | None]] = []
        self.penalties: list[tuple[str, float]] = []

    def acquire(self, key, cost=1, *, timeout=None):
        self.acquires.append((key, cost, timeout))
        return 0.0

    def penalize(self, key, seconds=60.0):
        self.penalties.append((key, seconds))


@pytest.fixture
def limiter(monkeypatch) -> NoopLimiter:
    fake = NoopLimiter()
    monkeypatch.setattr(geotab_client, "_rate_limiter", lambda: fake)
    return fake


def _record_for(call: tuple[str, dict[str, Any]]) -> list[dict]:
    """Respuesta determinista por (device, diagnostico/trip, ventana)."""
    _, payload = call
    search = payload.get("search") or {}
    if "deviceSearch" not in search:
        return [{"id": 1}]
    device_id = search["deviceSearch"]["id"]
    if payload["typeName"] == "Trip":
        return [{"start": "2026-08-10T10:00:00Z", "stop": "2026-08-10T12:00:00Z", "distance": 10.0,
                 "drivingDuration": timedelta(hours=1), "device": device_id}]
    diag = search["diagnosticSearch"]["id"]
    return [
        {"dateTime": f"{search['fromDate'][:19]}Z", "data": 100.0, "device": device_id, "diag": diag},
        {"dateTime": f"{search['toDate'][:19]}Z", "data": 200.0, "device": device_id, "diag": diag},
    ]


class BatchAPI:
    """multi_call scripted: responde por llamada con `_record_for` salvo que
    `fail` decida lanzar para ese lote."""

    def __init__(self, fail=None, responder=None):
        self.fail = fail
        self.responder = responder or _record_for
        self.batches: list[list[tuple[str, dict]]] = []
        self.individual: list[tuple[str, dict]] = []
        self.credentials = type("Creds", (), {"username": "user", "database": "test_db"})()

    def multi_call(self, calls):
        self.batches.append(list(calls))
        if self.fail is not None:
            exc = self.fail(calls)
            if exc is not None:
                raise exc
        return [self.responder(call) for call in calls]

    def call(self, method, **kwargs):
        self.individual.append((method, kwargs))
        return self.responder((method, kwargs))

    def authenticate(self):
        pass


def _fatal() -> MyGeotabException:
    return MyGeotabException({"errors": [{"name": "ArgumentException", "message": "Invalid search"}]})


def _over_limit() -> MyGeotabException:
    return MyGeotabException({"errors": [{"name": "OverLimitException", "message": "quota"}]})


# ── get_month_data_bundles ───────────────────────────────────────────────────


class TestGetMonthDataBundles:
    def test_slices_two_chunks_of_two_devices(self, limiter):
        api = BatchAPI()
        bundles = get_month_data_bundles(
            api, ["d1", "d2", "d3", "d4"], from_date=FROM_DATE, to_date=TO_DATE,
            status_diagnostics=DIAGS, edge_only=(), chunk_size=2,
        )

        assert len(api.batches) == 2
        assert [len(batch) for batch in api.batches] == [10, 10]
        assert [c[1]["search"]["deviceSearch"]["id"] for c in api.batches[0]] == ["d1"] * 5 + ["d2"] * 5
        assert set(bundles) == {"d1", "d2", "d3", "d4"}
        for device_id, bundle in bundles.items():
            assert list(bundle) == ["odometer", "engine_hours", "total_fuel", "device_fuel", "trips"]
            for key, diag in DIAGS.items():
                assert [r["device"] for r in bundle[key]] == [device_id, device_id]
                assert {r["diag"] for r in bundle[key]} == {diag}
                assert [r["dateTime"] for r in bundle[key]] == sorted(r["dateTime"] for r in bundle[key])
            assert bundle["trips"][0]["device"] == device_id
        # acquire cobra len(calls) por multi_call
        assert [cost for _, cost, _ in limiter.acquires] == [10, 10]

    def test_matches_single_device_bundle_shape(self, limiter):
        api = BatchAPI()
        single = get_month_data_bundle(api, "d1", FROM_DATE, TO_DATE, status_diagnostics=DIAGS, edge_only=FUEL_KEYS)
        batched = get_month_data_bundles(
            api, ["d1"], from_date=FROM_DATE, to_date=TO_DATE, status_diagnostics=DIAGS, edge_only=FUEL_KEYS
        )["d1"]
        assert single == batched

    def test_fuel_edge_windows_replace_full_month(self, limiter):
        api = BatchAPI()
        bundles = get_month_data_bundles(
            api, ["d1"], from_date=FROM_DATE, to_date=TO_DATE, status_diagnostics=DIAGS, edge_only=FUEL_KEYS
        )
        # 7 Gets: odo, horas, 2x total_fuel, 2x device_fuel, trips
        calls = api.batches[0]
        assert len(calls) == 7
        fuel_calls = [c for c in calls if c[1]["search"].get("diagnosticSearch", {}).get("id") == DIAGS["total_fuel"]]
        assert [(c[1]["search"]["fromDate"], c[1]["search"]["toDate"]) for c in fuel_calls] == [
            (FROM_DATE, "2026-08-03T05:00:00.000Z"),
            ("2026-08-30T05:00:00.000Z", TO_DATE),
        ]
        fuel = bundles["d1"]["total_fuel"]
        assert len(fuel) == 2
        assert fuel[0]["dateTime"] < fuel[1]["dateTime"]
        assert fuel[0]["dateTime"].startswith("2026-08-01") and fuel[1]["dateTime"].startswith("2026-09-01")
        # sin segunda pasada: ambas ventanas trajeron datos
        assert len(api.batches) == 1

    def test_empty_edge_window_triggers_full_series_pass(self, limiter):
        def responder(call):
            _, payload = call
            search = payload["search"]
            diag = search.get("diagnosticSearch", {}).get("id")
            if diag == DIAGS["total_fuel"] and search["fromDate"] == FROM_DATE and search["toDate"] != TO_DATE:
                return []  # vehiculo quieto los primeros dias
            if diag == DIAGS["total_fuel"] and search["fromDate"] == FROM_DATE and search["toDate"] == TO_DATE:
                return [{"dateTime": "2026-08-20T00:00:00Z", "data": 5.0}, {"dateTime": "2026-08-05T00:00:00Z", "data": 1.0}]
            return _record_for(call)

        api = BatchAPI(responder=responder)
        bundles = get_month_data_bundles(
            api, ["d1", "d2"], from_date=FROM_DATE, to_date=TO_DATE, status_diagnostics=DIAGS, edge_only=FUEL_KEYS
        )
        assert len(api.batches) == 2
        second = api.batches[1]
        assert [(c[1]["search"]["deviceSearch"]["id"], c[1]["search"]["fromDate"], c[1]["search"]["toDate"]) for c in second] == [
            ("d1", FROM_DATE, TO_DATE),
            ("d2", FROM_DATE, TO_DATE),
        ]
        assert [r["data"] for r in bundles["d1"]["total_fuel"]] == [1.0, 5.0]  # serie completa ordenada
        assert len(bundles["d1"]["device_fuel"]) == 2  # la otra clave no se toco

    def test_full_series_pass_is_batched(self, limiter, monkeypatch):
        monkeypatch.setattr(geotab_client, "BUNDLE_FULL_SERIES_PER_MULTICALL", 2)

        def responder(call):
            _, payload = call
            search = payload["search"]
            diag = search.get("diagnosticSearch", {}).get("id")
            if diag in (DIAGS["total_fuel"], DIAGS["device_fuel"]) and search["toDate"] != TO_DATE:
                return []  # ventana inicial vacia para ambos combustibles
            return _record_for(call)

        api = BatchAPI(responder=responder)
        bundles = get_month_data_bundles(
            api, ["d1", "d2", "d3"], from_date=FROM_DATE, to_date=TO_DATE, status_diagnostics=DIAGS, edge_only=FUEL_KEYS
        )
        # 1 multi_call principal + 6 series completas en grupos de 2 -> 3 multi_calls mas
        assert [len(b) for b in api.batches] == [21, 2, 2, 2]
        assert all(len(bundles[d]["total_fuel"]) == 2 for d in ("d1", "d2", "d3"))

    def test_short_range_disables_edge_windows(self, limiter):
        api = BatchAPI()
        get_month_data_bundles(
            api, ["d1"], from_date="2026-08-01T05:00:00.000Z", to_date="2026-08-04T05:00:00.000Z",
            status_diagnostics=DIAGS, edge_only=FUEL_KEYS,
        )
        assert len(api.batches[0]) == 5

    def test_fatal_chunk_falls_back_per_device_only_for_that_chunk(self, limiter):
        def fail(calls):
            devices = {c[1]["search"]["deviceSearch"]["id"] for c in calls}
            if devices == {"d1", "d2"}:
                return _fatal()
            return None

        api = BatchAPI(fail=fail)
        bundles = get_month_data_bundles(
            api, ["d1", "d2", "d3", "d4"], from_date=FROM_DATE, to_date=TO_DATE,
            status_diagnostics=DIAGS, edge_only=(), chunk_size=2,
        )
        devices_per_batch = [sorted({c[1]["search"]["deviceSearch"]["id"] for c in b}) for b in api.batches]
        # chunk 1 fallido -> bundle individual de d1 y d2; chunk 2 intacto
        assert devices_per_batch == [["d1", "d2"], ["d1"], ["d2"], ["d3", "d4"]]
        assert api.individual == []
        assert set(bundles) == {"d1", "d2", "d3", "d4"}
        assert bundles["d1"]["odometer"][0]["device"] == "d1"

    def test_transient_exhaustion_is_reraised(self, limiter, monkeypatch):
        monkeypatch.setattr(geotab_client._time, "sleep", lambda s: None)
        api = BatchAPI(fail=lambda calls: requests.exceptions.ConnectionError("down"))
        with pytest.raises(requests.exceptions.ConnectionError):
            get_month_data_bundles(api, ["d1", "d2"], from_date=FROM_DATE, to_date=TO_DATE, status_diagnostics=DIAGS)
        assert api.individual == []


# ── Provider ─────────────────────────────────────────────────────────────────


def _target(plate: str) -> PerformanceTarget:
    return PerformanceTarget(
        provider_key="geotab", customer_id=1, customer_database_id=1, client_name="Cliente A",
        database_name="db_a", plate=plate, technical_number=None, engine_name=None,
        username="user", password="pass", provider_config={},
    )


def _bundle(device_id: str) -> dict:
    return {
        "odometer": [{"dateTime": "2026-08-01T05:00:00Z", "data": 1_000_000.0}, {"dateTime": "2026-08-31T05:00:00Z", "data": 2_000_000.0}],
        "engine_hours": [{"dateTime": "2026-08-01T05:00:00Z", "data": 3600.0}, {"dateTime": "2026-08-31T05:00:00Z", "data": 7200.0}],
        "total_fuel": [{"dateTime": "2026-08-01T05:00:00Z", "data": 100.0}, {"dateTime": "2026-08-31T05:00:00Z", "data": 200.0}],
        "device_fuel": [],
        "trips": [{"stop": "2026-08-10T12:00:00Z", "distance": 500.0, "drivingDuration": timedelta(hours=2), "idlingDuration": timedelta(0)}],
    }


class _Env:
    def __init__(self, monkeypatch, *, workers: int, chunk: int, breaker: int = 3):
        monkeypatch.setenv("GEOTAB_MAX_WORKERS", str(workers))
        monkeypatch.setenv("GEOTAB_BUNDLE_CHUNK_DEVICES", str(chunk))
        monkeypatch.setenv("GEOTAB_BREAKER_THRESHOLD", str(breaker))


def _run_provider(targets, *, fetch_bundles, should_stop=None, on_target_done=None):
    provider = GeotabMonthlyPerformanceProvider()
    bindings = {("geotab", t.customer_database_id, t.plate): BindingSnapshot(f"dev-{t.plate}", "resolved", is_manual=True) for t in targets}
    with (
        patch("app.services.performance_providers.get_cached_devices", return_value=[]),
        patch("app.services.performance_providers.get_authenticated_client", return_value=object()),
        patch("app.services.performance_providers.get_month_data_bundles", side_effect=fetch_bundles) as fetch,
    ):
        result = provider.calculate_database_rows(
            month="2026-08", year=2026, month_number=8, previous_month="2026-07",
            targets=targets, previous_records={}, bindings=bindings,
            on_target_done=on_target_done, should_stop=should_stop,
        )
    return result, fetch


def _fake_fetch(api, device_ids, **kwargs):
    return {device_id: _bundle(device_id) for device_id in device_ids}


class TestGeotabProviderParallel:
    def test_five_devices_two_workers_gives_five_rows_and_main_thread_callbacks(self, monkeypatch):
        _Env(monkeypatch, workers=2, chunk=2)
        targets = [_target(f"PLT00{i}") for i in range(1, 6)]
        done_threads: list[int] = []
        worker_threads: set[int] = set()

        def fetch(api, device_ids, **kwargs):
            worker_threads.add(threading.get_ident())
            return _fake_fetch(api, device_ids, **kwargs)

        result, fetch_mock = _run_provider(targets, fetch_bundles=fetch, on_target_done=lambda: done_threads.append(threading.get_ident()))

        assert [row.plate for row in result.records] == [t.plate for t in targets]  # orden original
        assert all(row.calculation_status == "calculated" for row in result.records)
        assert all(row.kms_ecm == 1000.0 for row in result.records)
        assert len(done_threads) == 5
        assert set(done_threads) == {threading.get_ident()}
        # 5 devices / chunk 2 -> 3 multi_calls, ejecutados en hilos del pool
        assert fetch_mock.call_count == 3
        assert sorted(len(c.args[1]) for c in fetch_mock.call_args_list) == [1, 2, 2]
        assert threading.get_ident() not in worker_threads
        assert len(result.binding_updates) == 5

    def test_cancel_between_futures_raises_job_cancelled(self, monkeypatch):
        _Env(monkeypatch, workers=1, chunk=1)
        targets = [_target(f"PLT00{i}") for i in range(1, 6)]
        done = {"n": 0}

        def _done():
            done["n"] += 1

        with pytest.raises(JobCancelled):
            _run_provider(targets, fetch_bundles=_fake_fetch, should_stop=lambda: done["n"] >= 2, on_target_done=_done)
        assert done["n"] == 2

    def test_job_cancelled_inside_worker_propagates(self, monkeypatch):
        _Env(monkeypatch, workers=2, chunk=1)
        targets = [_target("PLT001"), _target("PLT002")]

        def fetch(api, device_ids, **kwargs):
            raise JobCancelled("cancelado en el worker")

        with pytest.raises(JobCancelled):
            _run_provider(targets, fetch_bundles=fetch)

    def test_per_plate_failure_becomes_error_row_without_aborting(self, monkeypatch):
        _Env(monkeypatch, workers=2, chunk=5)
        targets = [_target(f"PLT00{i}") for i in range(1, 4)]

        def fetch(api, device_ids, **kwargs):
            bundles = _fake_fetch(api, device_ids, **kwargs)
            bundles["dev-PLT002"] = {"odometer": [{"dateTime": "2026-08-01T05:00:00Z", "data": 1.0}]}  # sin engine_hours -> KeyError
            return bundles

        result, _ = _run_provider(targets, fetch_bundles=fetch)
        statuses = {row.plate: row.calculation_status for row in result.records}
        assert statuses == {"PLT001": "calculated", "PLT002": "error", "PLT003": "calculated"}
        bad = next(row for row in result.records if row.plate == "PLT002")
        assert "Error calculando la placa en Geotab" in bad.warnings[0]
        assert bad.provider_vehicle_id == "dev-PLT002"


class TestGeotabCircuitBreaker:
    def test_trips_after_three_transient_failures(self, monkeypatch):
        _Env(monkeypatch, workers=1, chunk=1, breaker=3)
        targets = [_target(f"PLT00{i}") for i in range(1, 6)]
        calls = {"n": 0}

        def fetch(api, device_ids, **kwargs):
            calls["n"] += 1
            raise requests.exceptions.ConnectionError(f"caida {calls['n']}")

        result, _ = _run_provider(targets, fetch_bundles=fetch)

        assert calls["n"] == 3  # tras el tercer fallo no se vuelve a llamar a Geotab
        assert [row.calculation_status for row in result.records] == ["error"] * 5
        direct = [row for row in result.records if "Error calculando la placa en Geotab" in row.warnings[0]]
        tripped = [row for row in result.records if "circuit breaker tras 3 fallos consecutivos" in row.warnings[0]]
        assert len(direct) == 3 and len(tripped) == 2
        assert "caida 3" in tripped[0].warnings[0]
        assert {row.plate for row in tripped} == {"PLT004", "PLT005"}

    def test_success_resets_counter_and_fatal_does_not_count(self, monkeypatch):
        _Env(monkeypatch, workers=1, chunk=1, breaker=3)
        targets = [_target(f"PLT00{i}") for i in range(1, 7)]
        script = iter(["t", "t", "ok", "t", "t", "ok"])

        def fetch(api, device_ids, **kwargs):
            step = next(script)
            if step == "t":
                raise requests.exceptions.ConnectionError("caida")
            return _fake_fetch(api, device_ids, **kwargs)

        result, fetch_mock = _run_provider(targets, fetch_bundles=fetch)
        assert fetch_mock.call_count == 6
        assert not any("circuit breaker" in " ".join(row.warnings) for row in result.records)

        breaker = performance_providers._GeotabCircuitBreaker(2, database="db")
        breaker.record_failure(ValueError("fatal"))
        breaker.record_failure(ValueError("fatal"))
        assert breaker.is_open() is False
        breaker.record_failure(requests.exceptions.ConnectionError("x"))
        breaker.record_success()
        breaker.record_failure(requests.exceptions.ConnectionError("y"))
        assert breaker.is_open() is False
        breaker.record_failure(requests.exceptions.ConnectionError("z"))
        assert breaker.is_open() is True
        assert "2 fallos consecutivos" in breaker.warning()


# ── Rate limiter (G7) ────────────────────────────────────────────────────────


class TestRateLimiterWiring:
    def test_multi_call_acquires_len_calls_and_single_call_acquires_one(self, limiter):
        api = BatchAPI()
        calls = [("Get", {"typeName": "StatusData", "search": {"deviceSearch": {"id": "d1"}, "diagnosticSearch": {"id": "x"}, "fromDate": FROM_DATE, "toDate": TO_DATE}})] * 3
        multi_call_with_retry(api, calls)
        geotab_client._api_call_with_retry(api, "Get", typeName="Device")

        assert [(key, cost) for key, cost, _ in limiter.acquires] == [("test_db", 3), ("test_db", 1)]
        assert all(timeout == geotab_client.RATE_LIMIT_ACQUIRE_TIMEOUT_SECONDS for _, _, timeout in limiter.acquires)

    def test_penalize_called_on_over_limit(self, limiter, monkeypatch):
        sleeps: list[float] = []
        monkeypatch.setattr(geotab_client._time, "sleep", lambda s: sleeps.append(s))
        attempts = {"n": 0}

        def fail(calls):
            attempts["n"] += 1
            return _over_limit() if attempts["n"] == 1 else None

        api = BatchAPI(fail=fail)
        multi_call_with_retry(api, [("Get", {"typeName": "Device"})])

        assert limiter.penalties == [("test_db", geotab_client.RATE_LIMIT_WAIT_SECONDS)]
        assert len(limiter.acquires) == 2  # uno por intento
        assert sleeps and sleeps[0] >= geotab_client.RATE_LIMIT_WAIT_SECONDS

    def test_acquire_timeout_does_not_block_the_call(self, monkeypatch):
        class TimeoutLimiter(NoopLimiter):
            def acquire(self, key, cost=1, *, timeout=None):
                raise geotab_client.RateLimitTimeout("lleno")

        monkeypatch.setattr(geotab_client, "_rate_limiter", lambda: TimeoutLimiter())
        api = BatchAPI()
        assert geotab_client._api_call_with_retry(api, "Get", typeName="Device")

    def test_unknown_key_without_credentials(self, limiter):
        api = BatchAPI()
        del api.credentials
        geotab_client._api_call_with_retry(api, "Get", typeName="Device")
        assert limiter.acquires[0][0] == "unknown"

    def test_virtual_clock_expires_penalty_with_fake_sleep(self, monkeypatch):
        """Con sleeps falsos, la penalizacion del limitador real expira en tiempo virtual."""
        monkeypatch.setattr(geotab_client, "_LIMITER", None)
        monkeypatch.setattr(geotab_client, "_VIRTUAL_CLOCK_OFFSET", 0.0)
        monkeypatch.setattr(geotab_client._time, "sleep", lambda s: None)
        real = geotab_client._rate_limiter()
        api = BatchAPI(fail=lambda calls: _over_limit() if len(api.batches) == 1 else None, responder=lambda call: [{"id": 1}])
        out = multi_call_with_retry(api, [("Get", {"typeName": "Device"})])
        assert out == [[{"id": 1}]]
        assert len(api.batches) == 2
        snap = real.snapshot()["test_db"]
        assert snap["penalties_total"] == 1
        assert snap["blocked_for"] == 0.0
        assert real.try_acquire("test_db") is True
