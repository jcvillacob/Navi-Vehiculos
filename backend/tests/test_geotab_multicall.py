from __future__ import annotations

from datetime import timedelta
from typing import Any
from unittest.mock import patch

import pytest

from app.clients.geotab_client import (
    get_month_data_bundle,
    multi_call_with_retry,
)
from app.services.performance_providers import _calculate_geotab_vehicle_record
from app.services.performance_types import PerformanceTarget


class FakeAPI:
    def __init__(self, results=None, multi_side_effect=None, call_side_effect=None):
        self.results = results
        self.multi_side_effect = multi_side_effect
        self.call_side_effect = call_side_effect
        self.authenticated = False
        self.calls: list[tuple[Any, ...]] = []
        self.call_count = 0

    def multi_call(self, calls):
        self.calls.append(("multi_call", calls))
        if self.multi_side_effect is not None:
            return self.multi_side_effect(calls)
        return self.results

    def call(self, method, **kwargs):
        self.calls.append(("call", method, kwargs))
        self.call_count += 1
        if self.call_side_effect is not None:
            return self.call_side_effect(method, kwargs)
        return []

    def authenticate(self):
        self.authenticated = True


def _make_target(**overrides) -> PerformanceTarget:
    defaults = dict(
        provider_key="geotab",
        customer_id=1,
        customer_database_id=1,
        client_name="Test",
        database_name="test_db",
        plate="PWY730",
        technical_number="TEC001",
        engine_name="Test Motor",
        username="user",
        password="pass",
        provider_config={},
    )
    defaults.update(overrides)
    return PerformanceTarget(**defaults)


class TestGetMonthDataBundle:
    def test_builds_calls_in_order_and_sorts_status_data(self):
        api = FakeAPI(
            results=[
                # odometer: desordenado; debe quedar ordenado por dateTime
                [
                    {"dateTime": "2026-06-15T00:00:00Z", "data": 2000.0},
                    {"dateTime": "2026-06-01T00:00:00Z", "data": 1000.0},
                ],
                # engine_hours
                [{"dateTime": "2026-06-01T00:00:00Z", "data": 3600.0}],
                # total_fuel
                [{"dateTime": "2026-06-01T00:00:00Z", "data": 100.0}],
                # device_fuel
                [{"dateTime": "2026-06-01T00:00:00Z", "data": 50.0}],
                # trips
                [{"distance": 100.0, "drivingDuration": timedelta(hours=1)}],
            ]
        )

        bundle = get_month_data_bundle(
            api,
            device_id="dev1",
            from_date="2026-06-01T05:00:00.000Z",
            to_date="2026-07-01T05:00:00.000Z",
            status_diagnostics={
                "odometer": "DiagnosticOdometerId",
                "engine_hours": "DiagnosticEngineHoursId",
                "total_fuel": "DiagnosticTotalFuelUsedId",
                "device_fuel": "DiagnosticDeviceTotalFuelId",
            },
        )

        assert len(api.calls) == 1
        method, calls = api.calls[0]
        assert method == "multi_call"
        assert len(calls) == 5

        expected_keys = ["odometer", "engine_hours", "total_fuel", "device_fuel", "trips"]
        assert list(bundle.keys()) == expected_keys

        # Calls orden: 4 StatusData + 1 Trip
        for idx, (expected_key, (call_method, call_payload)) in enumerate(zip(expected_keys, calls)):
            assert call_method == "Get"
            if expected_key == "trips":
                assert call_payload["typeName"] == "Trip"
            else:
                assert call_payload["typeName"] == "StatusData"
                assert call_payload["search"]["diagnosticSearch"]["id"] == {
                    "odometer": "DiagnosticOdometerId",
                    "engine_hours": "DiagnosticEngineHoursId",
                    "total_fuel": "DiagnosticTotalFuelUsedId",
                    "device_fuel": "DiagnosticDeviceTotalFuelId",
                }[expected_key]
            assert call_payload["search"]["deviceSearch"] == {"id": "dev1"}

        # StatusData ordenado por dateTime
        assert [r["dateTime"] for r in bundle["odometer"]] == [
            "2026-06-01T00:00:00Z",
            "2026-06-15T00:00:00Z",
        ]
        # Trips sin ordenar
        assert bundle["trips"] == [{"distance": 100.0, "drivingDuration": timedelta(hours=1)}]

    def test_fallback_to_individual_calls_when_multicall_fails(self):
        def call_side_effect(method, kwargs):
            search = kwargs.get("search", {})
            diag = search.get("diagnosticSearch", {}).get("id")
            if kwargs.get("typeName") == "StatusData":
                return [{"dateTime": "2026-06-01T00:00:00Z", "data": 123.0, "diag": diag}]
            if kwargs.get("typeName") == "Trip":
                return [{"distance": 50.0, "drivingDuration": timedelta(minutes=30)}]
            return []

        api = FakeAPI(
            multi_side_effect=lambda _calls: (_ for _ in ()).throw(RuntimeError("multi_call boom")),
            call_side_effect=call_side_effect,
        )

        bundle = get_month_data_bundle(
            api,
            device_id="dev1",
            from_date="2026-06-01T05:00:00.000Z",
            to_date="2026-07-01T05:00:00.000Z",
            status_diagnostics={
                "odometer": "DiagnosticOdometerId",
                "engine_hours": "DiagnosticEngineHoursId",
                "total_fuel": "DiagnosticTotalFuelUsedId",
                "device_fuel": "DiagnosticDeviceTotalFuelId",
            },
        )

        # Una llamada multi_call fallida + 5 llamadas individuales
        assert len([c for c in api.calls if c[0] == "multi_call"]) == 1
        assert api.call_count == 5

        assert set(bundle.keys()) == {"odometer", "engine_hours", "total_fuel", "device_fuel", "trips"}
        assert bundle["odometer"][0]["diag"] == "DiagnosticOdometerId"
        assert bundle["engine_hours"][0]["diag"] == "DiagnosticEngineHoursId"
        assert bundle["total_fuel"][0]["diag"] == "DiagnosticTotalFuelUsedId"
        assert bundle["device_fuel"][0]["diag"] == "DiagnosticDeviceTotalFuelId"
        assert bundle["trips"][0]["distance"] == 50.0


class FakeAuthenticationError(Exception):
    """Nombre contiene 'Authentication', por lo que _is_auth_error la detecta."""


class TestMultiCallWithRetry:
    def test_reauthenticates_on_auth_error_then_succeeds(self):
        results = [[{"id": 1}], [{"id": 2}]]
        attempts = []

        def multi_side_effect(calls):
            attempts.append(calls)
            if len(attempts) == 1:
                raise FakeAuthenticationError("session has expired")
            return results

        api = FakeAPI(multi_side_effect=multi_side_effect)

        out = multi_call_with_retry(api, [("Get", {"typeName": "Device"})])

        assert api.authenticated is True
        assert len(attempts) == 2
        assert out == results

    def test_retries_on_network_error(self, monkeypatch):
        attempts = []

        def multi_side_effect(calls):
            attempts.append(calls)
            if len(attempts) < 3:
                raise RuntimeError("connection timeout")
            return [[{"id": 1}]]

        api = FakeAPI(multi_side_effect=multi_side_effect)
        monkeypatch.setattr(
            "app.clients.geotab_client._is_network_error", lambda exc: "timeout" in str(exc).lower()
        )
        monkeypatch.setattr("app.clients.geotab_client.NET_RETRY_WAITS", (0, 0))

        out = multi_call_with_retry(api, [("Get", {"typeName": "Device"})])

        assert len(attempts) == 3
        assert out == [[{"id": 1}]]


class TestCalculateGeotabVehicleRecord:
    def test_bundle_produces_expected_record(self):
        target = _make_target()
        bundle = {
            "odometer": [
                {"dateTime": "2026-06-01T05:00:00Z", "data": 10000000.0},
                {"dateTime": "2026-06-30T23:59:59Z", "data": 11000000.0},
            ],
            "engine_hours": [
                {"dateTime": "2026-06-01T05:00:00Z", "data": 360000.0},
                {"dateTime": "2026-06-30T23:59:59Z", "data": 374400.0},
            ],
            "total_fuel": [
                {"dateTime": "2026-06-01T05:00:00Z", "data": 1000.0},
                {"dateTime": "2026-06-30T23:59:59Z", "data": 1200.0},
            ],
            "device_fuel": [],
            "trips": [
                {
                    "distance": 500.0,
                    "drivingDuration": timedelta(hours=3),
                    "idlingDuration": timedelta(minutes=30),
                }
            ],
        }

        with patch(
            "app.services.performance_providers.get_month_data_bundle",
            return_value=bundle,
        ):
            record = _calculate_geotab_vehicle_record(
                target=target,
                month="2026-06",
                device_id="dev1",
                api=object(),  # no se usa gracias al patch
                from_date="2026-06-01T05:00:00.000Z",
                to_date="2026-07-01T05:00:00.000Z",
                previous_record=None,
                cutoff_mode=False,
            )

        assert record.calculation_status == "calculated"
        assert record.odo_start == 10000.0  # metros -> km
        assert record.odo_end == 11000.0
        assert record.kms_ecm == 1000.0
        assert record.horo_start == 100.0  # segundos -> horas
        assert record.horo_end == 104.0
        assert record.hours_ecm == 4.0
        assert record.fuel_gallons == pytest.approx(200.0 / 3.7854118)
        assert record.kms_gps == 500.0
        assert record.hours_gps == 3.5
        assert record.provider_vehicle_id == "dev1"
        # Sin warnings inesperados (solo los dos de primera lectura + posiblemente granular)
        assert any("primera lectura del mes" in w for w in record.warnings)
        assert not any("Combustible calculado usando DiagnosticDeviceTotalFuelId" in w for w in record.warnings)
