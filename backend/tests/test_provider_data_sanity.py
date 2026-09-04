"""Sanidad de datos en los proveedores de Rendimientos (hardening sep 2026).

Sin DB ni red: los clientes se parchean como en test_geotab_multicall /
test_frotcom_monthly_record / test_artimo_trip_window / test_logitracs_triton_provider.

Cubre:
- D1  `_positive_delta`: retroceso -> None + warning (antes `max(0, ...)`).
- D13 `_td_to_hours`: TimeSpan .NET con dias y fraccion de segundo.
- Geotab: odo_end < odo_start -> kms_ecm None, partial, fuentes; viajes que
  terminan fuera del mes excluidos (D10); combustible encadenado con
  previous.fuel_end cuando la fuente coincide (D9); retrocesos > 5% -> partial.
- LogiTracs (D4): combustible omitido por defecto, convertido con config.
- Artimo (D8): viajes sin consumo > 10% -> fuel None + partial.
- Geotab (G8): indice de placas resuelve duplicados igual que antes.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from app.clients.artimo_client import select_trips_in_window
from app.clients.geotab_client import (
    _find_device_in_collection,
    build_plate_index,
    find_matching_devices,
    lookup_plate_index,
)
from app.schemas.vehicle import MonthlyPerformanceRecord
from app.services.performance_providers import (
    GeotabMonthlyPerformanceProvider,
    LogitracsTritonMonthlyPerformanceProvider,
    SOURCE_ESTIMATED,
    SOURCE_FIRST_READING,
    SOURCE_LAST_READING,
    SOURCE_PREVIOUS,
    SOURCE_TRIPS,
    _calculate_geotab_vehicle_record,
    _calculate_logitracs_vehicle_record,
    _calculate_vehicle_record,
    _filter_geotab_trips_in_window,
    _positive_delta,
    _td_to_hours,
)
from app.services.performance_types import BindingSnapshot, PerformanceTarget


def _target(provider_key: str = "geotab", plate: str = "PWY730", **overrides) -> PerformanceTarget:
    defaults = dict(
        provider_key=provider_key,
        customer_id=1,
        customer_database_id=1,
        client_name="Test",
        database_name="test_db",
        plate=plate,
        technical_number="TEC001",
        engine_name="Test Motor",
        username="user",
        password="pass",
        provider_config={},
    )
    defaults.update(overrides)
    return PerformanceTarget(**defaults)


def _previous(**overrides) -> MonthlyPerformanceRecord:
    defaults = dict(
        customer_database_id=1,
        source_provider="geotab",
        plate="PWY730",
        period_month="2026-05",
        calculation_status="calculated",
    )
    defaults.update(overrides)
    return MonthlyPerformanceRecord(**defaults)


# ── D1: _positive_delta ───────────────────────────────────────────────────────


class TestPositiveDelta:
    def test_regression_returns_none_and_warns(self):
        warnings: list[str] = []
        result = _positive_delta(1500.0, 1200.0, label="Odómetro", unit="km", warnings=warnings)
        assert result is None
        assert len(warnings) == 1
        assert warnings[0] == "Odómetro retrocede: 1500 → 1200 (-300 km); kilometraje ECM no calculado."

    def test_hours_regression_uses_hours_metric_label(self):
        warnings: list[str] = []
        assert _positive_delta(100.5, 90.25, label="Horómetro", unit="h", warnings=warnings) is None
        assert warnings == ["Horómetro retrocede: 100.5 → 90.25 (-10.25 h); horas ECM no calculadas."]

    def test_positive_and_zero_deltas_pass_through(self):
        warnings: list[str] = []
        assert _positive_delta(100.0, 250.5, label="Odómetro", unit="km", warnings=warnings) == 150.5
        assert _positive_delta(100.0, 100.0, label="Odómetro", unit="km", warnings=warnings) == 0.0
        assert warnings == []

    def test_missing_values_return_none_silently(self):
        warnings: list[str] = []
        assert _positive_delta(None, 10.0, label="Odómetro", unit="km", warnings=warnings) is None
        assert _positive_delta(10.0, None, label="Odómetro", unit="km", warnings=warnings) is None
        assert warnings == []


# ── D13: _td_to_hours ─────────────────────────────────────────────────────────


class TestTdToHours:
    @pytest.mark.parametrize(
        "value, expected_hours",
        [
            ("00:15:30.1230000", (15 * 60 + 30.123) / 3600.0),
            ("1.02:03:04.5000000", (86400 + 2 * 3600 + 3 * 60 + 4.5) / 3600.0),
            ("02:03:04", (2 * 3600 + 3 * 60 + 4) / 3600.0),
            (timedelta(hours=1), 1.0),
            (3600, 1.0),
            ("3600", 1.0),
            (None, 0.0),
            ("garbage", 0.0),
        ],
    )
    def test_conversions(self, value, expected_hours):
        assert _td_to_hours(value) == pytest.approx(expected_hours)


# ── Geotab ────────────────────────────────────────────────────────────────────

FROM_DATE = "2026-06-01T05:00:00.000Z"
TO_DATE = "2026-07-01T05:00:00.000Z"


def _geotab_bundle(**overrides) -> dict:
    bundle = {
        "odometer": [
            {"dateTime": "2026-06-01T05:00:00Z", "data": 10_000_000.0},
            {"dateTime": "2026-06-30T23:59:59Z", "data": 11_000_000.0},
        ],
        "engine_hours": [
            {"dateTime": "2026-06-01T05:00:00Z", "data": 360_000.0},
            {"dateTime": "2026-06-30T23:59:59Z", "data": 374_400.0},
        ],
        "total_fuel": [
            {"dateTime": "2026-06-01T05:00:00Z", "data": 1000.0},
            {"dateTime": "2026-06-30T23:59:59Z", "data": 1200.0},
        ],
        "device_fuel": [],
        "trips": [
            {
                "start": "2026-06-10T10:00:00Z",
                "stop": "2026-06-10T12:00:00Z",
                "distance": 500.0,
                "drivingDuration": timedelta(hours=3),
                "idlingDuration": timedelta(minutes=30),
            }
        ],
    }
    bundle.update(overrides)
    return bundle


def _geotab_record(bundle: dict, previous_record=None, **kwargs) -> MonthlyPerformanceRecord:
    with patch("app.services.performance_providers.get_month_data_bundle", return_value=bundle):
        return _calculate_geotab_vehicle_record(
            target=_target(),
            month="2026-06",
            device_id="dev1",
            api=object(),
            from_date=FROM_DATE,
            to_date=TO_DATE,
            previous_record=previous_record,
            **kwargs,
        )


class TestGeotabRegressionAndSources:
    def test_odometer_regression_nulls_kms_and_degrades_to_partial(self):
        previous = _previous(odo_end=12_000.0, horo_end=100.0)
        record = _geotab_record(_geotab_bundle(), previous_record=previous)

        assert record.odo_start == 12_000.0
        assert record.odo_end == 11_000.0
        assert record.kms_ecm is None
        assert record.calculation_status == "partial"
        assert any(w.startswith("Odómetro retrocede: 12000 → 11000 (-1000 km)") for w in record.warnings)
        assert record.odo_start_source == SOURCE_PREVIOUS
        assert record.odo_end_source == SOURCE_LAST_READING
        assert record.horo_start_source == SOURCE_PREVIOUS
        assert record.horo_end_source == SOURCE_LAST_READING
        # Las horas si avanzaron: no se tocan.
        assert record.hours_ecm == pytest.approx(4.0)

    def test_first_month_sources_are_first_and_last_reading(self):
        record = _geotab_record(_geotab_bundle())
        assert record.calculation_status == "calculated"
        assert record.kms_ecm == 1000.0
        assert record.odo_start_source == SOURCE_FIRST_READING
        assert record.odo_end_source == SOURCE_LAST_READING
        assert record.horo_start_source == SOURCE_FIRST_READING
        assert record.source_meta["device_id"] == "dev1"
        assert record.source_meta["odo_first_at"] == "2026-06-01T05:00:00Z"

    def test_significant_accumulated_regressions_degrade_to_partial(self):
        # Neto positivo (10.000 -> 11.000 km) pero con una caida intermedia de
        # 800 km (> 5% de 1.000 km): el registro no es confiable.
        bundle = _geotab_bundle(
            odometer=[
                {"dateTime": "2026-06-01T05:00:00Z", "data": 10_000_000.0},
                {"dateTime": "2026-06-15T05:00:00Z", "data": 10_900_000.0},
                {"dateTime": "2026-06-16T05:00:00Z", "data": 10_100_000.0},
                {"dateTime": "2026-06-30T23:59:59Z", "data": 11_000_000.0},
            ]
        )
        record = _geotab_record(bundle)
        assert record.kms_ecm == 1000.0
        assert record.geotab_regression_total_km == pytest.approx(800.0)
        assert record.calculation_status == "partial"
        assert any("registro marcado parcial" in w for w in record.warnings)

    def test_small_regressions_only_warn(self):
        bundle = _geotab_bundle(
            odometer=[
                {"dateTime": "2026-06-01T05:00:00Z", "data": 10_000_000.0},
                {"dateTime": "2026-06-15T05:00:00Z", "data": 10_500_000.0},
                {"dateTime": "2026-06-16T05:00:00Z", "data": 10_490_000.0},
                {"dateTime": "2026-06-30T23:59:59Z", "data": 11_000_000.0},
            ]
        )
        record = _geotab_record(bundle)
        assert record.calculation_status == "calculated"
        assert record.geotab_regression_total_km == pytest.approx(10.0)


class TestGeotabTripsBoundary:
    def test_filter_keeps_only_trips_that_stop_inside_window(self):
        trips = [
            {"stop": "2026-06-01T04:30:00Z", "distance": 10.0},  # termina antes del corte -> mes anterior
            {"stop": "2026-06-01T05:00:00Z", "distance": 20.0},  # exactamente el inicio: dentro
            {"stop": datetime(2026, 6, 20, tzinfo=timezone.utc), "distance": 30.0},
            {"stop": "2026-07-01T05:00:00Z", "distance": 40.0},  # exactamente el fin: fuera
            {"stop": "2026-07-01T06:00:00Z", "distance": 50.0},
            {"distance": 60.0},  # sin stop: se conserva
        ]
        kept, dropped = _filter_geotab_trips_in_window(trips, FROM_DATE, TO_DATE)
        assert [t["distance"] for t in kept] == [20.0, 30.0, 60.0]
        assert dropped == 3

    def test_record_excludes_boundary_trips_and_reports_it(self):
        bundle = _geotab_bundle(
            trips=[
                {
                    "start": "2026-05-31T22:00:00Z",
                    "stop": "2026-06-01T04:00:00Z",  # cruza el dia 1: cuenta en mayo
                    "distance": 300.0,
                    "drivingDuration": timedelta(hours=6),
                },
                {
                    "start": "2026-06-10T10:00:00Z",
                    "stop": "2026-06-10T12:00:00Z",
                    "distance": 500.0,
                    "drivingDuration": timedelta(hours=2),
                },
                {
                    "start": "2026-06-30T23:00:00Z",
                    "stop": "2026-07-01T06:00:00Z",  # termina en julio
                    "distance": 200.0,
                    "drivingDuration": timedelta(hours=7),
                },
            ]
        )
        record = _geotab_record(bundle)
        assert record.kms_gps == 500.0
        assert record.hours_gps == 2.0
        assert record.source_meta["trips_dropped_boundary"] == 2
        assert record.source_meta["trips_count"] == 1
        assert any("2 viaje(s) que terminan fuera del mes fueron excluidos" in w for w in record.warnings)

    def test_trips_without_stop_are_kept_for_backwards_compatibility(self):
        record = _geotab_record(_geotab_bundle(trips=[{"distance": 100.0, "drivingDuration": timedelta(hours=1)}]))
        assert record.kms_gps == 100.0
        assert "trips_dropped_boundary" not in record.source_meta


class TestGeotabFuelChain:
    def test_first_month_uses_first_reading_and_records_fuel_end(self):
        record = _geotab_record(_geotab_bundle())
        assert record.fuel_gallons == pytest.approx(200.0 / 3.7854118)
        assert record.fuel_end == 1200.0
        assert record.source_meta["fuel_source"] == "TotalFuelUsed"
        assert record.source_meta["fuel_start_source"] == SOURCE_FIRST_READING
        assert any("Combustible inicial tomado de la primera lectura del mes" in w for w in record.warnings)

    def test_uses_previous_fuel_end_when_source_matches(self):
        previous = _previous(
            odo_end=10_000.0,
            horo_end=100.0,
            fuel_end=950.0,
            source_meta={"fuel_source": "TotalFuelUsed"},
        )
        record = _geotab_record(_geotab_bundle(), previous_record=previous)
        # 1200 - 950 (cierre previo), no 1200 - 1000 (primera lectura)
        assert record.fuel_gallons == pytest.approx(250.0 / 3.7854118)
        assert record.fuel_end == 1200.0
        assert record.source_meta["fuel_start_source"] == SOURCE_PREVIOUS
        assert not any("Combustible inicial tomado de la primera lectura" in w for w in record.warnings)

    def test_previous_fuel_end_ignored_when_source_differs(self):
        previous = _previous(
            odo_end=10_000.0,
            horo_end=100.0,
            fuel_end=950.0,
            source_meta={"fuel_source": "DeviceTotalFuel"},
        )
        record = _geotab_record(_geotab_bundle(), previous_record=previous)
        assert record.fuel_gallons == pytest.approx(200.0 / 3.7854118)
        assert record.source_meta["fuel_source"] == "TotalFuelUsed"
        assert record.source_meta["fuel_start_source"] == SOURCE_FIRST_READING
        assert any("Combustible inicial tomado de la primera lectura del mes" in w for w in record.warnings)

    def test_fuel_regression_nulls_fuel_and_keeps_new_baseline(self):
        previous = _previous(
            odo_end=10_000.0,
            horo_end=100.0,
            fuel_end=5000.0,
            source_meta={"fuel_source": "TotalFuelUsed"},
        )
        record = _geotab_record(_geotab_bundle(), previous_record=previous)
        assert record.fuel_gallons is None
        assert record.fuel_end == 1200.0  # el mes siguiente arranca de la nueva base
        assert record.calculation_status == "partial"
        assert any(w.startswith("Combustible acumulado retrocede (TotalFuelUsed): 5000 → 1200") for w in record.warnings)

    def test_device_fuel_fallback_is_labelled(self):
        bundle = _geotab_bundle(
            total_fuel=[],
            device_fuel=[
                {"dateTime": "2026-06-01T05:00:00Z", "data": 100.0},
                {"dateTime": "2026-06-30T23:59:59Z", "data": 137.85},
            ],
        )
        record = _geotab_record(bundle)
        assert record.fuel_gallons == pytest.approx(37.85 / 3.7854118)
        assert record.source_meta["fuel_source"] == "DeviceTotalFuel"
        assert any("DiagnosticDeviceTotalFuelId" in w for w in record.warnings)


class TestGeotabPlateIndex:
    @staticmethod
    def _dev(device_id: str, plate: str, active: bool = True, **extra) -> dict:
        return {
            "id": device_id,
            "name": plate,
            "licensePlate": plate,
            "activeTo": "2050-01-01T00:00:00.000Z" if active else "2020-01-01T00:00:00.000Z",
            **extra,
        }

    def test_lookup_matches_find_matching_devices_including_order(self):
        devices = [
            self._dev("b33B", "LQK264"),
            self._dev("aaa", "XYZ999"),
            self._dev("b35D", "lqk-264"),
            {"id": "pref", "name": "NAV-ABC123"},  # sin licensePlate ni activeTo
            {"id": "noname"},
        ]
        index = build_plate_index(devices)
        for plate, prefix in (("LQK264", None), ("ABC123", "NAV"), ("ABC123", None), ("ZZZ000", None)):
            expected = find_matching_devices(devices, plate=plate, plate_prefix=prefix)
            assert lookup_plate_index(index, plate=plate, plate_prefix=prefix) == expected

    def test_duplicate_resolution_is_unchanged(self):
        # Mismos escenarios que test_geotab_duplicate_device, ahora via indice.
        devices = [self._dev("b33B", "LQK264", active=False), self._dev("b35D", "LQK264", active=True)]
        index = build_plate_index(devices)
        candidates = lookup_plate_index(index, plate="LQK264")
        assert _find_device_in_collection(candidates, plate="LQK264", preferred_id="b33B")["id"] == "b35D"

        both_active = [self._dev("b33B", "LQK264"), self._dev("b35D", "LQK264")]
        candidates = lookup_plate_index(build_plate_index(both_active), plate="LQK264")
        assert _find_device_in_collection(candidates, plate="LQK264", preferred_id="b35D")["id"] == "b35D"
        assert _find_device_in_collection(candidates, plate="LQK264")["id"] == "b33B"

    def test_provider_resolves_through_index_and_warns_on_active_duplicates(self):
        provider = GeotabMonthlyPerformanceProvider()
        devices = [self._dev("b33B", "LQK264"), self._dev("b35D", "LQK264"), self._dev("zzz", "OTHER1")]
        target = _target(plate="LQK264")
        bindings = {("geotab", 1, "LQK264"): BindingSnapshot("b35D", "resolved", is_manual=False)}

        def fake_calculate(**kwargs):
            return _previous(plate="LQK264", period_month="2026-06", provider_vehicle_id=kwargs["device_id"])

        with (
            patch("app.services.performance_providers.get_cached_devices", return_value=devices),
            patch("app.services.performance_providers.get_authenticated_client", return_value=object()),
            patch("app.services.performance_providers._calculate_geotab_vehicle_record", side_effect=fake_calculate),
        ):
            result = provider.calculate_database_rows(
                month="2026-06",
                year=2026,
                month_number=6,
                previous_month="2026-05",
                targets=[target],
                previous_records={},
                bindings=bindings,
            )

        assert result.records[0].provider_vehicle_id == "b35D"
        assert result.binding_updates[0].provider_vehicle_id == "b35D"
        assert any("2 dispositivos activos" in w for w in result.records[0].warnings)


# ── LogiTracs (D4) ────────────────────────────────────────────────────────────

LOGITRACS_ROW = {
    "Placa": "AAA111",
    "Odometro final": "1500",
    "Kilometraje": "200",
    "Tiempo Encendido(h)": "10",
    "Combustible": "100",
}


class TestLogitracsFuelUnit:
    def _record(self, provider_config: dict | None = None, monkeypatch=None, env: str | None = None):
        if monkeypatch is not None:
            if env is None:
                monkeypatch.delenv("LOGITRACS_FUEL_UNIT", raising=False)
            else:
                monkeypatch.setenv("LOGITRACS_FUEL_UNIT", env)
        return _calculate_logitracs_vehicle_record(
            target=_target(provider_key="logitracs_triton", plate="AAA111", provider_config=provider_config or {}),
            month="2026-01",
            provider_vehicle_id="AAA111",
            current_row=dict(LOGITRACS_ROW),
            previous_row={"Placa": "AAA111", "Odometro final": "1300"},
            previous_record=None,
        )

    def test_default_omits_fuel_and_degrades_to_partial(self, monkeypatch):
        record = self._record(monkeypatch=monkeypatch)
        assert record.fuel_gallons is None
        assert record.calculation_status == "partial"
        assert record.source_meta["fuel_raw"] == 100.0
        assert record.source_meta["fuel_unit"] == "desconocida"
        assert "Combustible LogiTracs omitido: unidad no confirmada (valor crudo en source_meta)." in record.warnings
        assert not any("unidad por confirmar" in w for w in record.warnings)
        # Odometro: fuentes trazadas
        assert record.odo_start == 1300.0 and record.odo_start_source == SOURCE_PREVIOUS
        assert record.odo_end == 1500.0 and record.odo_end_source == SOURCE_LAST_READING
        assert record.kms_ecm == 200.0

    def test_config_gal_keeps_value(self, monkeypatch):
        record = self._record({"logitracs_fuel_unit": "gal"}, monkeypatch=monkeypatch)
        assert record.fuel_gallons == 100.0
        assert record.source_meta["fuel_unit"] == "gal"
        assert record.calculation_status == "calculated"

    def test_config_liters_converts(self, monkeypatch):
        record = self._record({"logitracs_fuel_unit": "L"}, monkeypatch=monkeypatch)
        assert record.fuel_gallons == pytest.approx(100.0 / 3.785411784)
        assert record.source_meta["fuel_unit"] == "l"
        assert record.calculation_status == "calculated"

    def test_env_fallback_and_unknown_values(self, monkeypatch):
        assert self._record(monkeypatch=monkeypatch, env="l").fuel_gallons == pytest.approx(100.0 / 3.785411784)
        assert self._record(monkeypatch=monkeypatch, env="barrels").fuel_gallons is None
        # provider_config manda sobre el entorno
        assert self._record({"logitracs_fuel_unit": "gal"}, monkeypatch=monkeypatch, env="l").fuel_gallons == 100.0

    def test_odometer_regression_is_not_clamped(self, monkeypatch):
        monkeypatch.delenv("LOGITRACS_FUEL_UNIT", raising=False)
        record = _calculate_logitracs_vehicle_record(
            target=_target(provider_key="logitracs_triton", plate="AAA111", provider_config={"logitracs_fuel_unit": "gal"}),
            month="2026-01",
            provider_vehicle_id="AAA111",
            current_row=dict(LOGITRACS_ROW),
            previous_row=None,
            previous_record=_previous(source_provider="logitracs_triton", plate="AAA111", odo_end=1800.0),
        )
        assert record.kms_ecm is None
        assert record.calculation_status == "partial"
        assert any(w.startswith("Odómetro retrocede: 1800 → 1500 (-300 km)") for w in record.warnings)

    def test_provider_end_to_end_default_is_partial(self, monkeypatch):
        monkeypatch.delenv("LOGITRACS_FUEL_UNIT", raising=False)
        provider = LogitracsTritonMonthlyPerformanceProvider()
        target = _target(
            provider_key="logitracs_triton",
            plate="AAA111",
            provider_config={"codigo_empresa": "GRUPOK"},
        )
        with patch(
            "app.services.performance_providers.LogitracsTritonClient.get_fleet_operational_report",
            side_effect=[[dict(LOGITRACS_ROW)], [{"Placa": "AAA111", "Odometro final": "1300"}]],
        ):
            result = provider.calculate_database_rows(
                month="2026-01",
                year=2026,
                month_number=1,
                previous_month="2025-12",
                targets=[target],
                previous_records={},
                bindings={},
            )
        record = result.records[0]
        assert record.calculation_status == "partial"
        assert record.fuel_gallons is None
        assert record.source_meta["fuel_raw"] == 100.0


# ── Artimo (D8 + D1) ──────────────────────────────────────────────────────────


def _artimo_trip(start: str, end: str, *, odometer, horometer, distance, hours, liters) -> dict:
    return {
        "plate": "TLK520",
        "startdate": start,
        "enddate": end,
        "odometer": str(odometer),
        "horometer": str(horometer),
        "distance": None if distance is None else str(distance),
        "enginetime": None if hours is None else str(hours),
        "consumption": None if liters is None else str(liters),
    }


def _artimo_window(rows):
    return select_trips_in_window(
        rows,
        window_start_local=datetime(2026, 6, 1),
        window_end_local=datetime(2026, 6, 30, 23, 59, 59, 999000),
    )


def _artimo_record(window, previous_trip=None, previous_record=None, gps_rows=None):
    return _calculate_vehicle_record(
        target=_target(provider_key="artimo", plate="TLK520"),
        month="2026-06",
        current_trip=window.close_trip,
        previous_trip=previous_trip,
        previous_record=previous_record,
        provider_vehicle_id="resource-1",
        gps_rows=gps_rows or [],
        trip_window=window,
    )


class TestArtimoMissingFields:
    def test_window_counts_missing_fields(self):
        rows = [
            _artimo_trip("2026-06-01 08:00:00", "2026-06-01 12:00:00", odometer=47500, horometer=1510, distance=300, hours=4, liters=None),
            _artimo_trip("2026-06-02 08:00:00", "2026-06-02 12:00:00", odometer=47800, horometer=1514, distance=None, hours=None, liters=80),
        ]
        window = _artimo_window(rows)
        assert window.trip_count == 2
        assert window.missing_consumption == 1
        assert window.missing_distance == 1
        assert window.missing_hours == 1
        assert window.fuel_liters == 80.0

    def test_missing_consumption_over_threshold_nulls_fuel(self):
        # 2 de 10 viajes (20%) sin consumo -> combustible omitido, partial.
        rows = [
            _artimo_trip(
                f"2026-06-{day:02d} 08:00:00",
                f"2026-06-{day:02d} 12:00:00",
                odometer=47000 + day * 100,
                horometer=1500 + day,
                distance=100,
                hours=1,
                liters=None if day <= 2 else 40,
            )
            for day in range(1, 11)
        ]
        window = _artimo_window(rows)
        previous_close = _artimo_trip("2026-05-31 10:00:00", "2026-05-31 18:00:00", odometer=47050, horometer=1500.5, distance=200, hours=8, liters=80)
        record = _artimo_record(window, previous_trip=previous_close)

        assert record.fuel_gallons is None
        assert record.calculation_status == "partial"
        assert any("2 de 10 viaje(s) sin consumo" in w and "se omite" in w for w in record.warnings)
        assert record.source_meta["artimo_trips_missing"] == {"total": 10, "distance": 0, "hours": 0, "consumption": 2}
        # Odometro/horometro intactos y con fuente
        assert record.kms_ecm == pytest.approx(48000 - 47050)
        assert record.odo_start_source == SOURCE_TRIPS
        assert record.odo_end_source == SOURCE_TRIPS
        assert record.horo_end_source == SOURCE_TRIPS

    def test_missing_consumption_under_threshold_only_warns(self):
        # 1 de 20 viajes (5%) sin consumo -> warning, combustible se mantiene.
        rows = [
            _artimo_trip(
                f"2026-06-{day:02d} 08:00:00",
                f"2026-06-{day:02d} 12:00:00",
                odometer=47000 + day * 100,
                horometer=1500 + day,
                distance=100,
                hours=1,
                liters=None if day == 1 else 40,
            )
            for day in range(1, 21)
        ]
        window = _artimo_window(rows)
        previous_close = _artimo_trip("2026-05-31 10:00:00", "2026-05-31 18:00:00", odometer=47050, horometer=1500.5, distance=200, hours=8, liters=80)
        record = _artimo_record(window, previous_trip=previous_close)

        assert record.fuel_gallons == pytest.approx(19 * 40 / 3.78541)
        assert record.calculation_status == "calculated"
        assert any("1 de 20 viaje(s) sin consumo" in w and "subestimado" in w for w in record.warnings)

    def test_missing_hours_over_threshold_nulls_hours_gps(self):
        rows = [
            _artimo_trip("2026-06-01 08:00:00", "2026-06-01 12:00:00", odometer=47500, horometer=1510, distance=300, hours=None, liters=120),
            _artimo_trip("2026-06-02 08:00:00", "2026-06-02 12:00:00", odometer=47800, horometer=1514, distance=300, hours=4, liters=120),
        ]
        window = _artimo_window(rows)
        previous_close = _artimo_trip("2026-05-31 10:00:00", "2026-05-31 18:00:00", odometer=47200, horometer=1506, distance=200, hours=8, liters=80)
        record = _artimo_record(window, previous_trip=previous_close)
        assert record.hours_gps is None
        assert record.calculation_status == "partial"


class TestArtimoRegressionAndSources:
    def test_odometer_regression_is_not_clamped(self):
        rows = [
            _artimo_trip("2026-06-01 08:00:00", "2026-06-01 12:00:00", odometer=40000, horometer=1510, distance=300, hours=4, liters=120),
        ]
        window = _artimo_window(rows)
        record = _artimo_record(window, previous_record=_previous(source_provider="artimo", plate="TLK520", odo_end=47671.637, horo_end=1500.0))
        assert record.odo_start == 47671.637
        assert record.odo_start_source == SOURCE_PREVIOUS
        assert record.kms_ecm is None
        assert record.hours_ecm == pytest.approx(10.0)
        assert record.calculation_status == "partial"
        assert any(w.startswith("Odómetro retrocede: 47671.64 → 40000 (-7671.64 km)") for w in record.warnings)

    def test_estimated_start_is_flagged_and_negative_estimate_is_dropped(self):
        rows = [
            _artimo_trip("2026-06-01 08:00:00", "2026-06-01 12:00:00", odometer=47500, horometer=1510, distance=300, hours=4, liters=120),
        ]
        window = _artimo_window(rows)
        record = _artimo_record(window)
        assert record.odo_start == pytest.approx(47200)
        assert record.odo_start_source == SOURCE_ESTIMATED
        assert record.horo_start_source == SOURCE_ESTIMATED

        # Distancia mayor al odometro: antes quedaba en 0.0, ahora None.
        rows = [
            _artimo_trip("2026-06-01 08:00:00", "2026-06-01 12:00:00", odometer=100, horometer=1510, distance=300, hours=4, liters=120),
        ]
        record = _artimo_record(_artimo_window(rows))
        assert record.odo_start is None
        assert record.odo_start_source is None
        assert any("Odometro inicial no estimable" in w for w in record.warnings)

    def test_gps_only_month_mixes_bases_and_warns(self):
        gps_rows = [
            {"date": "2026-06-01 06:00:00", "odometer": "50000"},
            {"date": "2026-06-30 20:00:00", "odometer": "50400"},
        ]
        window = _artimo_window([])
        record = _artimo_record(
            window,
            previous_record=_previous(source_provider="artimo", plate="TLK520", odo_end=49900.0),
            gps_rows=gps_rows,
        )
        assert record.odo_start_source == SOURCE_PREVIOUS
        assert record.odo_end_source == "gps"
        assert record.kms_gps == 400.0
        assert record.calculation_status == "partial"
        assert any("bases distintas" in w for w in record.warnings)
