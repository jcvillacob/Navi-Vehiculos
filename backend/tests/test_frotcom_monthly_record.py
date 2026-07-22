from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import patch

import pytest

from app.clients.frotcom_client import (
    FrotcomConfig,
    FrotcomReading,
    FrotcomTripOdometers,
    _row_to_reading,
    get_can_day_records,
)
from app.schemas.vehicle import MonthlyPerformanceRecord
from app.services.performance_providers import _calculate_frotcom_vehicle_record
from app.services.performance_types import PerformanceTarget


def _make_target(**overrides) -> PerformanceTarget:
    defaults = dict(
        provider_key="frotcom",
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


def _trip_odos(**overrides) -> FrotcomTripOdometers:
    defaults = dict(
        odo_start=None,
        odo_end=None,
        reconstructed_start=False,
        reconstruction_incomplete=False,
        trip_count=0,
        warnings=[],
    )
    defaults.update(overrides)
    return FrotcomTripOdometers(**defaults)


def _previous_record(odo_end: float | None) -> MonthlyPerformanceRecord:
    return MonthlyPerformanceRecord(
        customer_database_id=1,
        source_provider="frotcom",
        plate="PWY730",
        period_month="2026-05",
        odo_end=odo_end,
        calculation_status="calculated",
    )


def _calculate(
    *,
    trip_odos,
    first_reading,
    last_reading,
    summary=None,
    previous_record=None,
    chronometer_lookup=None,
    estimate_return=None,
):
    with (
        patch(
            "app.services.performance_providers.get_frotcom_mileage_and_time",
            return_value=summary or {},
        ),
        patch(
            "app.services.performance_providers.get_frotcom_trip_odometers",
            return_value=trip_odos,
        ),
        patch(
            "app.services.performance_providers.find_frotcom_first_reading",
            return_value=first_reading,
        ),
        patch(
            "app.services.performance_providers.find_frotcom_last_reading",
            return_value=last_reading,
        ),
        patch(
            "app.services.performance_providers.estimate_hourmeter_end_from_chronometer",
            return_value=estimate_return or (None, []),
        ),
    ):
        return _calculate_frotcom_vehicle_record(
            target=_make_target(),
            config=FrotcomConfig(username="user", password="pass"),
            month="2026-06",
            vehicle_id="v1",
            df_iso="2026-06-01T05:00:00Z",
            dt_iso="2026-07-01T04:59:59Z",
            month_start=datetime(2026, 6, 1),
            month_end=datetime(2026, 6, 30, 23, 59, 59),
            range_start_utc=datetime(2026, 6, 1, 5, tzinfo=timezone.utc),
            range_end_utc=datetime(2026, 7, 1, 4, 59, 59, tzinfo=timezone.utc),
            previous_record=previous_record,
            chronometer_lookup=chronometer_lookup,
            now_utc=datetime(2026, 7, 10, tzinfo=timezone.utc),
        )


SUMMARY = {"mileageGpsKms": 1200.0, "drivingTimeSeconds": 36000}
FIRST_CAN = FrotcomReading(odometer=13950.0, total_fuel_used=100.0, date="2026-06-01", engine_hours=8100.5)
LAST_CAN = FrotcomReading(odometer=15980.0, total_fuel_used=480.0, date="2026-06-30", engine_hours=8325.75)


class TestFrotcomOdometerPriority:
    def test_can_day_keeps_engine_hours_when_odometer_is_zero(self):
        with patch(
            "app.clients.frotcom_client._frotcom_get",
            return_value=[
                {"odometer": 0, "totalFuelUsed": 0, "engineHours": 8100, "date": "2026-07-01"}
            ],
        ):
            records = get_can_day_records(
                FrotcomConfig(username="user", password="pass"), "v1", "2026-07-01"
            )
        assert len(records) == 1

    def test_can_reading_parses_accumulated_engine_hours(self):
        reading = _row_to_reading(
            {"odometer": 13950, "totalFuelUsed": 100, "engineHours": 8100.5, "date": "2026-06-01"}
        )
        assert reading.engine_hours == pytest.approx(8100.5 / 3600.0)

    def test_previous_record_takes_priority_over_trips(self):
        record = _calculate(
            trip_odos=_trip_odos(odo_start=14000.0, odo_end=16000.0),
            first_reading=FIRST_CAN,
            last_reading=LAST_CAN,
            summary=SUMMARY,
            previous_record=_previous_record(13900.0),
        )
        assert record.odo_start == 13900.0
        assert record.odo_end == 16000.0
        assert record.kms_ecm == 2100.0

    def test_trips_take_priority_over_can_without_previous(self):
        record = _calculate(
            trip_odos=_trip_odos(odo_start=14000.0, odo_end=16000.0),
            first_reading=FIRST_CAN,
            last_reading=LAST_CAN,
            summary=SUMMARY,
        )
        assert record.odo_start == 14000.0
        assert record.odo_end == 16000.0
        assert record.kms_ecm == 2000.0
        assert record.calculation_status == "calculated"

    def test_fallback_to_can_when_trips_have_no_odometers(self):
        record = _calculate(
            trip_odos=_trip_odos(trip_count=5),
            first_reading=FIRST_CAN,
            last_reading=LAST_CAN,
            summary=SUMMARY,
        )
        assert record.odo_start == 13950.0
        assert record.odo_end == 15980.0
        assert any("primera lectura CAN" in w for w in record.warnings)
        assert any("ultima lectura CAN" in w for w in record.warnings)

    def test_engine_hours_come_from_can_readings(self):
        record = _calculate(
            trip_odos=_trip_odos(odo_start=14000.0, odo_end=16000.0),
            first_reading=FIRST_CAN,
            last_reading=LAST_CAN,
            summary=SUMMARY,
        )
        assert record.horo_start == 8100.5
        assert record.horo_end == 8325.75
        assert record.hours_ecm == pytest.approx(225.25)

    def test_trip_warnings_are_propagated(self):
        record = _calculate(
            trip_odos=_trip_odos(
                odo_start=985.0,
                odo_end=1150.0,
                reconstructed_start=True,
                trip_count=4,
                warnings=["Odometro inicial reconstruido restando distancias de 2 viaje(s) previo(s) sin odometro."],
            ),
            first_reading=FIRST_CAN,
            last_reading=LAST_CAN,
            summary=SUMMARY,
        )
        assert any("reconstruido" in w for w in record.warnings)

    def test_fuel_comes_only_from_can_readings(self):
        record = _calculate(
            trip_odos=_trip_odos(odo_start=14000.0, odo_end=16000.0),
            first_reading=FIRST_CAN,
            last_reading=LAST_CAN,
            summary=SUMMARY,
        )
        expected_gallons = (480.0 - 100.0) / 3.7854118
        assert record.fuel_gallons == pytest.approx(expected_gallons)

    def test_monthly_summary_replaces_unavailable_trips_but_can_is_needed_for_hours(self):
        summary = {
            "mileageCanKms": 5761.235,
            "mileageGpsKms": 5843.032,
            "totalFuelUsed": 2950.5,
            "drivingTimeSeconds": 490227,
        }
        with (
            patch(
                "app.services.performance_providers.get_frotcom_mileage_and_time",
                return_value=summary,
            ),
            patch(
                "app.services.performance_providers.get_frotcom_trip_odometers",
                return_value=_trip_odos(warnings=["/trips HTTP 404"]),
            ),
            patch(
                "app.services.performance_providers.find_frotcom_first_reading",
                side_effect=RuntimeError("vehicleCanInfo HTTP 403"),
            ) as first_can,
            patch(
                "app.services.performance_providers.find_frotcom_last_reading",
            ) as last_can,
        ):
            record = _calculate_frotcom_vehicle_record(
                target=_make_target(),
                config=FrotcomConfig(username="user", password="pass"),
                month="2026-06",
                vehicle_id="v1",
                df_iso="2026-06-01T05:00:00Z",
                dt_iso="2026-07-01T04:59:59Z",
                month_start=datetime(2026, 6, 1),
                month_end=datetime(2026, 6, 30, 23, 59, 59),
                range_start_utc=datetime(2026, 6, 1, 5, tzinfo=timezone.utc),
                range_end_utc=datetime(2026, 7, 1, 4, 59, 59, tzinfo=timezone.utc),
                previous_record=_previous_record(31476.325),
            )

        assert record.calculation_status == "partial"
        assert record.odo_start == pytest.approx(31476.325)
        assert record.odo_end == pytest.approx(37237.56)
        assert record.kms_ecm == pytest.approx(5761.235)
        assert record.fuel_gallons == pytest.approx(2950.5 / 3.7854118)
        # El resumen cubre km y combustible, pero no trae horometro ECM.
        first_can.assert_called_once()
        last_can.assert_not_called()

    def test_chronometer_fallback_when_can_unavailable(self):
        record = _calculate(
            trip_odos=_trip_odos(
                odo_start=14000.0,
                odo_end=16000.0,
                trip_count=40,
                engine_hours=225.25,
                engine_hours_trip_count=40,
            ),
            first_reading=None,
            last_reading=None,
            summary=SUMMARY,
            chronometer_lookup=lambda: 8330.0,
            estimate_return=(8325.75, []),
        )
        assert record.horo_end == pytest.approx(8325.75)
        assert record.horo_start == pytest.approx(8325.75 - 225.25)
        assert record.hours_ecm == pytest.approx(225.25)
        assert any("estimado" in w for w in record.warnings)

    def test_chronometer_not_used_when_can_gives_hourmeter(self):
        lookup_calls = []

        def _lookup():
            lookup_calls.append(1)
            return 9999.0

        record = _calculate(
            trip_odos=_trip_odos(odo_start=14000.0, odo_end=16000.0),
            first_reading=FIRST_CAN,
            last_reading=LAST_CAN,
            summary=SUMMARY,
            chronometer_lookup=_lookup,
        )
        assert record.horo_end == 8325.75
        assert lookup_calls == []

    def test_chronometer_without_month_trips_keeps_hourmeter_flat(self):
        record = _calculate(
            trip_odos=_trip_odos(trip_count=0),
            first_reading=None,
            last_reading=None,
            summary=SUMMARY,
            chronometer_lookup=lambda: 500.0,
            estimate_return=(500.0, []),
        )
        assert record.horo_start == 500.0
        assert record.horo_end == 500.0
        assert record.hours_ecm == 0.0

    def test_chronometer_lookup_failure_only_warns(self):
        def _lookup():
            raise RuntimeError("HTTP 500 en /v2/vehicles")

        record = _calculate(
            trip_odos=_trip_odos(odo_start=14000.0, odo_end=16000.0),
            first_reading=None,
            last_reading=None,
            summary=SUMMARY,
            chronometer_lookup=_lookup,
        )
        assert record.horo_end is None
        assert any("chronometer" in w for w in record.warnings)

    def test_hours_ecm_falls_back_to_trip_engine_hours(self):
        record = _calculate(
            trip_odos=_trip_odos(
                odo_start=14000.0,
                odo_end=16000.0,
                trip_count=12,
                engine_hours=98.5,
                engine_hours_trip_count=12,
            ),
            first_reading=None,
            last_reading=None,
            summary=SUMMARY,
        )
        assert record.horo_start is None
        assert record.horo_end is None
        assert record.hours_ecm == pytest.approx(98.5)
        assert any("conduccion + ralenti" in w for w in record.warnings)

    def test_no_data_when_all_sources_empty(self):
        record = _calculate(
            trip_odos=_trip_odos(),
            first_reading=None,
            last_reading=None,
            summary={},
        )
        assert record.calculation_status == "no_data"

    def test_trips_with_odometers_but_no_summary_is_not_no_data(self):
        record = _calculate(
            trip_odos=_trip_odos(odo_start=14000.0, odo_end=16000.0),
            first_reading=None,
            last_reading=None,
            summary={},
        )
        assert record.calculation_status == "partial"
        assert record.kms_ecm == 2000.0

    def test_trip_fetch_failure_falls_back_to_can(self):
        with (
            patch(
                "app.services.performance_providers.get_frotcom_mileage_and_time",
                return_value=SUMMARY,
            ),
            patch(
                "app.services.performance_providers.get_frotcom_trip_odometers",
                side_effect=RuntimeError("HTTP 500"),
            ),
            patch(
                "app.services.performance_providers.find_frotcom_first_reading",
                return_value=FIRST_CAN,
            ),
            patch(
                "app.services.performance_providers.find_frotcom_last_reading",
                return_value=LAST_CAN,
            ),
        ):
            record = _calculate_frotcom_vehicle_record(
                target=_make_target(),
                config=FrotcomConfig(username="user", password="pass"),
                month="2026-06",
                vehicle_id="v1",
                df_iso="2026-06-01T05:00:00Z",
                dt_iso="2026-07-01T04:59:59Z",
                month_start=datetime(2026, 6, 1),
                month_end=datetime(2026, 6, 30, 23, 59, 59),
                range_start_utc=datetime(2026, 6, 1, 5, tzinfo=timezone.utc),
                range_end_utc=datetime(2026, 7, 1, 4, 59, 59, tzinfo=timezone.utc),
                previous_record=None,
            )
        assert record.odo_start == 13950.0
        assert record.odo_end == 15980.0
        assert any("viajes de Frotcom" in w for w in record.warnings)
