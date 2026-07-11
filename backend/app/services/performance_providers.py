from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import time as dt_time, timedelta
from typing import Any, Callable, Protocol

from app.clients.artimo_client import (
    ArtimoAuthError,
    ArtimoClient,
    ArtimoConfig,
    extract_consumption_liters,
    extract_distance_km,
    extract_engine_time_hours,
    extract_horometer,
    extract_odometer,
    extract_plate,
    extract_provider_vehicle_id,
    gallons_from_liters,
    sort_rows_by_timestamp,
)
from app.schemas.vehicle import MonthlyPerformanceRecord
from app.clients.frotcom_client import (
    FrotcomAuthError,
    FrotcomConfig,
    FrotcomTripOdometers,
    find_vehicle_id_by_plate as find_frotcom_vehicle_id_by_plate,
    find_first_reading as find_frotcom_first_reading,
    find_last_reading as find_frotcom_last_reading,
    get_frotcom_month_range,
    get_frotcom_month_range_utc_bounds,
    get_mileage_and_time as get_frotcom_mileage_and_time,
    get_trip_based_odometers as get_frotcom_trip_odometers,
    hours_from_seconds,
    liters_to_gallons,
    list_vehicles as list_frotcom_vehicles,
)
from app.clients.geotab_client import (
    find_device_by_plate,
    get_authenticated_client,
    get_geotab_month_range,
    get_status_data_for_month,
    get_trips_for_month,
)
from app.clients.logitracs_triton_client import (
    LogitracsTritonAuthError,
    LogitracsTritonClient,
    LogitracsTritonConfig,
    extract_engine_hours as extract_triton_engine_hours,
    extract_fuel_liters as extract_triton_fuel_liters,
    extract_kms_period,
    extract_odometer_end,
    extract_plate as extract_triton_plate,
)
from app.services.performance_types import (
    BindingSnapshot,
    BindingUpsert,
    PerformanceTarget,
    ProviderCalculationResult,
)


class MonthlyPerformanceProvider(Protocol):
    key: str

    def calculate_database_rows(
        self,
        *,
        month: str,
        year: int,
        month_number: int,
        previous_month: str,
        targets: list[PerformanceTarget],
        previous_records: dict[tuple[int, str], MonthlyPerformanceRecord],
        bindings: dict[tuple[str, int, str], BindingSnapshot],
        on_target_done: "Callable[[], None] | None" = None,
    ) -> ProviderCalculationResult:
        ...


def _build_status_record(
    *,
    target: PerformanceTarget,
    month: str,
    status: str,
    warnings: list[str],
    provider_vehicle_id: str | None = None,
) -> MonthlyPerformanceRecord:
    return MonthlyPerformanceRecord(
        customer_id=target.customer_id,
        customer_database_id=target.customer_database_id,
        client_name=target.client_name,
        database_name=target.database_name,
        source_provider=target.provider_key,
        plate=target.plate,
        provider_vehicle_id=provider_vehicle_id,
        technical_number=target.technical_number,
        engine_name=target.engine_name,
        period_month=month,
        calculation_status=status,
        warnings=warnings,
    )


def _derive_start_values(
    *,
    previous_record: MonthlyPerformanceRecord | None,
    previous_trip: dict | None,
    current_trip: dict | None,
    gps_rows: list[dict],
    warnings: list[str],
) -> tuple[float | None, float | None]:
    odo_start = None
    horo_start = None

    if previous_record and previous_record.odo_end is not None:
        odo_start = previous_record.odo_end
    elif previous_trip is not None:
        odo_start = extract_odometer(previous_trip)
        if odo_start is not None:
            warnings.append("Odometro inicial tomado del cierre de Artimo del mes anterior.")
    elif current_trip is not None:
        current_odo_end = extract_odometer(current_trip)
        current_distance = extract_distance_km(current_trip)
        if current_odo_end is not None and current_distance is not None:
            odo_start = max(0.0, current_odo_end - current_distance)
            warnings.append("Odometro inicial estimado con el acumulado del mes actual.")
        elif gps_rows:
            gps_odo = extract_odometer(gps_rows[0])
            if gps_odo is not None:
                odo_start = gps_odo
                warnings.append("Odometro inicial estimado con la primera lectura GPS del mes.")

    if previous_record and previous_record.horo_end is not None:
        horo_start = previous_record.horo_end
    elif previous_trip is not None:
        horo_start = extract_horometer(previous_trip)
        if horo_start is not None:
            warnings.append("Horometro inicial tomado del cierre de Artimo del mes anterior.")
    elif current_trip is not None:
        current_horo_end = extract_horometer(current_trip)
        current_engine_time = extract_engine_time_hours(current_trip)
        if current_horo_end is not None and current_engine_time is not None:
            horo_start = max(0.0, current_horo_end - current_engine_time)
            warnings.append("Horometro inicial estimado con las horas del mes actual.")

    return odo_start, horo_start


def _calculate_vehicle_record(
    *,
    target: PerformanceTarget,
    month: str,
    current_trip: dict | None,
    previous_trip: dict | None,
    previous_record: MonthlyPerformanceRecord | None,
    provider_vehicle_id: str,
    gps_rows: list[dict],
) -> MonthlyPerformanceRecord:
    warnings: list[str] = []
    if current_trip is None and not gps_rows:
        return _build_status_record(
            target=target,
            month=month,
            status="no_data",
            warnings=["No se encontraron datos de viajes ni GPS para el mes solicitado."],
            provider_vehicle_id=provider_vehicle_id,
        )

    gps_rows = sort_rows_by_timestamp(gps_rows)
    gps_odo_start = extract_odometer(gps_rows[0]) if gps_rows else None
    gps_odo_end = extract_odometer(gps_rows[-1]) if gps_rows else None
    kms_gps = None
    if gps_odo_start is not None and gps_odo_end is not None:
        kms_gps = max(0.0, gps_odo_end - gps_odo_start)

    odo_start, horo_start = _derive_start_values(
        previous_record=previous_record,
        previous_trip=previous_trip,
        current_trip=current_trip,
        gps_rows=gps_rows,
        warnings=warnings,
    )

    odo_end = extract_odometer(current_trip) if current_trip is not None else gps_odo_end
    horo_end = extract_horometer(current_trip) if current_trip is not None else None
    fuel_gallons = gallons_from_liters(extract_consumption_liters(current_trip))
    hours_gps = extract_engine_time_hours(current_trip)

    kms_ecm = None
    if odo_start is not None and odo_end is not None:
        kms_ecm = max(0.0, odo_end - odo_start)

    hours_ecm = None
    if horo_start is not None and horo_end is not None:
        hours_ecm = max(0.0, horo_end - horo_start)

    status = "calculated"
    if current_trip is None:
        status = "partial"
        warnings.append("No hubo resumen de viajes; se completo solo con datos GPS disponibles.")
    elif any(value is None for value in (odo_start, odo_end, horo_start, horo_end, fuel_gallons)):
        status = "partial"
        warnings.append("No fue posible completar todos los campos base del corte mensual.")

    return MonthlyPerformanceRecord(
        customer_id=target.customer_id,
        customer_database_id=target.customer_database_id,
        client_name=target.client_name,
        database_name=target.database_name,
        source_provider=target.provider_key,
        plate=target.plate,
        provider_vehicle_id=provider_vehicle_id,
        technical_number=target.technical_number,
        engine_name=target.engine_name,
        period_month=month,
        odo_start=odo_start,
        odo_end=odo_end,
        horo_start=horo_start,
        horo_end=horo_end,
        kms_ecm=kms_ecm,
        kms_gps=kms_gps,
        hours_ecm=hours_ecm,
        hours_gps=hours_gps,
        fuel_gallons=fuel_gallons,
        calculation_status=status,
        warnings=warnings,
    )


def _select_binding(
    *,
    bindings: dict[tuple[str, int, str], "BindingSnapshot"],
    target: "PerformanceTarget",
) -> tuple[str | None, bool]:
    """Devuelve (provider_vehicle_id, is_manual) desde el mapa de bindings.

    - Si no hay binding para el target, devuelve (None, False).
    - is_manual solo es True cuando el binding vino marcado como manual.
    """
    snapshot = bindings.get((target.provider_key, target.customer_database_id, target.plate))
    if not snapshot:
        return None, False
    return snapshot.provider_vehicle_id, getattr(snapshot, "is_manual", False)


class ArtimoMonthlyPerformanceProvider:
    key = "artimo"

    def _build_config(self, target: PerformanceTarget) -> ArtimoConfig:
        provider_config = target.provider_config if isinstance(target.provider_config, dict) else {}
        customer_id = str(provider_config.get("customer_id") or "").strip()
        group_name = str(provider_config.get("group_name") or "").strip()
        api_base_url = str(provider_config.get("api_base_url") or "https://api.artimo.com.co").strip()
        auth_base_url = str(
            provider_config.get("auth_base_url") or "https://apifront.artimo.com.co"
        ).strip()

        if not target.username or not target.password:
            raise ValueError(
                f"La database {target.database_name or target.customer_database_id} no tiene credenciales Artimo completas."
            )
        if not customer_id or not group_name:
            raise ValueError(
                f"La database {target.database_name or target.customer_database_id} no tiene customer_id o group_name de Artimo."
            )

        return ArtimoConfig(
            username=target.username,
            password=target.password,
            customer_id=customer_id,
            group_name=group_name,
            api_base_url=api_base_url,
            auth_base_url=auth_base_url,
            month_start_hour_utc=int(provider_config.get("month_start_hour_utc") or 5),
            month_end_hour_utc=int(provider_config.get("month_end_hour_utc") or 16),
        )

    def calculate_database_rows(
        self,
        *,
        month: str,
        year: int,
        month_number: int,
        previous_month: str,
        targets: list[PerformanceTarget],
        previous_records: dict[tuple[int, str], MonthlyPerformanceRecord],
        bindings: dict[tuple[str, int, str], BindingSnapshot],
        on_target_done: Callable[[], None] | None = None,
    ) -> ProviderCalculationResult:
        if not targets:
            return ProviderCalculationResult(records=[], binding_updates=[])

        sample_target = targets[0]
        rows: list[MonthlyPerformanceRecord] = []
        binding_updates: list[BindingUpsert] = []

        artimo = ArtimoClient(self._build_config(sample_target))
        current_start, current_end = artimo.get_month_range(year, month_number)
        previous_year = int(previous_month[:4])
        previous_month_number = int(previous_month[-2:])
        previous_start, previous_end = artimo.get_month_range(previous_year, previous_month_number)

        try:
            current_trips = {
                plate: row
                for row in artimo.get_report("trips", current_start, current_end)
                if (plate := extract_plate(row))
            }
            previous_trips = {
                plate: row
                for row in artimo.get_report("trips", previous_start, previous_end)
                if (plate := extract_plate(row))
            }
        except ArtimoAuthError as exc:
            message = str(exc)
            for target in targets:
                binding_updates.append(
                    BindingUpsert(
                        target=target,
                        provider_vehicle_id=None,
                        binding_status="error",
                        last_error=message,
                    )
                )
                if on_target_done is not None:
                    try:
                        on_target_done()
                    except Exception:
                        pass
                rows.append(
                    _build_status_record(
                        target=target,
                        month=month,
                        status="error",
                        warnings=[message],
                    )
                )
            return ProviderCalculationResult(records=rows, binding_updates=binding_updates)

        for target in targets:
            try:
                current_trip = current_trips.get(target.plate)
                previous_trip = previous_trips.get(target.plate)
                bound_id, is_manual = _select_binding(bindings=bindings, target=target)
                if is_manual:
                    provider_vehicle_id = bound_id
                else:
                    provider_vehicle_id = (
                        bound_id
                        or extract_provider_vehicle_id(current_trip)
                        or extract_provider_vehicle_id(previous_trip)
                    )

                if not provider_vehicle_id:
                    binding_updates.append(
                        BindingUpsert(
                            target=target,
                            provider_vehicle_id=None,
                            binding_status="unbound",
                            last_error="No fue posible resolver el ID externo del GPS en Artimo.",
                        )
                    )
                    rows.append(
                        _build_status_record(
                            target=target,
                            month=month,
                            status="unbound",
                            warnings=["No fue posible resolver el ID externo del GPS en Artimo."],
                        )
                    )
                    continue

                binding_updates.append(
                    BindingUpsert(
                        target=target,
                        provider_vehicle_id=provider_vehicle_id,
                        binding_status="resolved",
                        last_error=None,
                    )
                )

                try:
                    gps_rows = artimo.get_report(
                        "gps",
                        current_start,
                        current_end,
                        resource_id=provider_vehicle_id,
                    )
                    rows.append(
                        _calculate_vehicle_record(
                            target=target,
                            month=month,
                            current_trip=current_trip,
                            previous_trip=previous_trip,
                            previous_record=previous_records.get((target.customer_database_id, target.plate)),
                            provider_vehicle_id=provider_vehicle_id,
                            gps_rows=gps_rows,
                        )
                    )
                except Exception as exc:
                    rows.append(
                        _build_status_record(
                            target=target,
                            month=month,
                            status="error",
                            provider_vehicle_id=provider_vehicle_id,
                            warnings=[f"Error calculando la placa en Artimo: {exc}"],
                        )
                    )
            finally:
                if on_target_done is not None:
                    try:
                        on_target_done()
                    except Exception:
                        pass

        return ProviderCalculationResult(records=rows, binding_updates=binding_updates)


# ==============================================================================
# GEOTAB MONTHLY PERFORMANCE PROVIDER
# ==============================================================================

_DIAG_ODOMETER = "DiagnosticOdometerId"
_DIAG_ENGINE_HOURS = "DiagnosticEngineHoursId"
_DIAG_TOTAL_FUEL = "DiagnosticTotalFuelUsedId"
_DIAG_DEVICE_FUEL = "DiagnosticDeviceTotalFuelId"
_LITERS_PER_GALLON = 3.7854118
_GEOTAB_REGRESSION_WARNING_PREFIX = "Retroceso Geotab detectado"
_GEOTAB_ACCUMULATED_REGRESSION_MARKER = "[acumulado]"
_MAX_GEOTAB_REGRESSION_WARNINGS = 5


@dataclass(frozen=True)
class _GeotabRegressionAnalysis:
    count: int
    total: float
    warnings: list[str]


def _analyze_geotab_regressions(
    readings: list[dict],
    *,
    label: str,
    divisor: float,
    unit: str,
    initial_value: float | None = None,
    initial_timestamp: str | None = None,
) -> _GeotabRegressionAnalysis:
    """Detecta y acumula cada caída entre StatusData consecutivos de Geotab."""
    parsed: list[tuple[float, str]] = []
    if initial_value is not None:
        parsed.append((initial_value, initial_timestamp or "registro anterior"))
    for reading in sorted(readings, key=lambda item: str(item.get("dateTime") or "")):
        try:
            value = float(reading.get("data"))
        except (TypeError, ValueError):
            continue
        parsed.append((value, str(reading.get("dateTime") or "fecha desconocida")))

    regressions: list[tuple[float, float, str, str, bool]] = []
    total = 0.0
    for index in range(1, len(parsed)):
        before, before_at = parsed[index - 1]
        after, after_at = parsed[index]
        if after >= before:
            continue
        drop = (before - after) / divisor
        total += drop
        regressions.append((before, after, before_at, after_at))

    detailed = regressions[:_MAX_GEOTAB_REGRESSION_WARNINGS]
    warnings = [
        (
            f"{_GEOTAB_REGRESSION_WARNING_PREFIX} "
            f"{_GEOTAB_ACCUMULATED_REGRESSION_MARKER} ({label}): "
            f"{before / divisor:g} → {after / divisor:g} {unit} "
            f"(caída de {(before - after) / divisor:g} {unit}) entre {before_at} y {after_at}."
        )
        for before, after, before_at, after_at in detailed
    ]
    hidden = regressions[len(detailed):]
    if hidden:
        warnings.append(
            f"{_GEOTAB_REGRESSION_WARNING_PREFIX} {_GEOTAB_ACCUMULATED_REGRESSION_MARKER}: "
            f"{len(hidden)} retroceso(s) adicional(es) no detallado(s)."
        )
    return _GeotabRegressionAnalysis(
        count=len(regressions),
        total=total,
        warnings=warnings,
    )


def _geotab_regression_warnings(
    readings: list[dict],
    *,
    label: str,
    divisor: float,
    unit: str,
    initial_value: float | None = None,
    initial_timestamp: str | None = None,
) -> list[str]:
    """Compara cada StatusData Geotab con el registro cronológico anterior."""
    return _analyze_geotab_regressions(
        readings,
        label=label,
        divisor=divisor,
        unit=unit,
        initial_value=initial_value,
        initial_timestamp=initial_timestamp,
    ).warnings


def _geotab_first_value(readings: list[dict]) -> float | None:
    if not readings:
        return None
    value = readings[0].get("data")
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _geotab_last_value(readings: list[dict]) -> float | None:
    if not readings:
        return None
    value = readings[-1].get("data")
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _td_to_hours(value) -> float:
    """Convert a timedelta, datetime.time, string duration, or None to hours."""
    if value is None:
        return 0.0
    if isinstance(value, timedelta):
        return value.total_seconds() / 3600.0
    if isinstance(value, dt_time):
        return (value.hour * 3600 + value.minute * 60 + value.second + value.microsecond / 1e6) / 3600.0
    if hasattr(value, "total_seconds"):
        return value.total_seconds() / 3600.0
    if isinstance(value, str):
        # Handle "D.HH:MM:SS" or "HH:MM:SS" format from Geotab
        try:
            days = 0
            time_part = value
            if "." in value:
                parts = value.split(".", 1)
                days = int(parts[0])
                time_part = parts[1]
            h, m, s = map(int, time_part.split(":"))
            return (days * 86400 + h * 3600 + m * 60 + s) / 3600.0
        except (ValueError, IndexError):
            pass
    try:
        return float(value) / 3600.0
    except (TypeError, ValueError):
        return 0.0


def _calculate_geotab_vehicle_record(
    *,
    target: PerformanceTarget,
    month: str,
    device_id: str,
    api,
    from_date: str,
    to_date: str,
    previous_record: MonthlyPerformanceRecord | None,
    cutoff_mode: bool = False,
) -> MonthlyPerformanceRecord:
    warnings: list[str] = []

    odo_readings = get_status_data_for_month(api, device_id, _DIAG_ODOMETER, from_date, to_date)
    hours_readings = get_status_data_for_month(api, device_id, _DIAG_ENGINE_HOURS, from_date, to_date)
    fuel_readings = get_status_data_for_month(api, device_id, _DIAG_TOTAL_FUEL, from_date, to_date)
    trips = get_trips_for_month(api, device_id, from_date, to_date)

    if not odo_readings and not hours_readings:
        return _build_status_record(
            target=target,
            month=month,
            status="no_data",
            warnings=["No se encontraron datos de odometro ni horas de motor en Geotab para el mes solicitado."],
            provider_vehicle_id=device_id,
        )

    previous_odo_raw = (
        previous_record.odo_end * 1000.0
        if previous_record and previous_record.odo_end is not None
        else None
    )
    previous_horo_raw = (
        previous_record.horo_end * 3600.0
        if previous_record and previous_record.horo_end is not None
        else None
    )
    previous_period = (
        f"cierre {previous_record.period_month}"
        if previous_record
        else None
    )
    odometer_regressions = _analyze_geotab_regressions(
        odo_readings,
        label="odómetro",
        divisor=1000.0,
        unit="km",
        initial_value=previous_odo_raw,
        initial_timestamp=previous_period,
    )
    hourmeter_regressions = _analyze_geotab_regressions(
        hours_readings,
        label="horómetro",
        divisor=3600.0,
        unit="h",
        initial_value=previous_horo_raw,
        initial_timestamp=previous_period,
    )
    warnings.extend(odometer_regressions.warnings)
    warnings.extend(hourmeter_regressions.warnings)

    # Odometer (meters → km)
    odo_end_raw = _geotab_last_value(odo_readings)
    odo_end = odo_end_raw / 1000.0 if odo_end_raw is not None else None

    if previous_record and previous_record.odo_end is not None:
        odo_start = previous_record.odo_end
    else:
        odo_start_raw = _geotab_first_value(odo_readings)
        odo_start = odo_start_raw / 1000.0 if odo_start_raw is not None else None
        if odo_start is not None:
            warnings.append(
                "Odometro inicial tomado de la lectura en el tanqueo anterior (punto exacto del corte)."
                if cutoff_mode
                else "Odometro inicial tomado de la primera lectura del mes (sin registro previo)."
            )

    kms_ecm = max(0.0, odo_end - odo_start) if odo_start is not None and odo_end is not None else None

    # Engine hours (seconds → hours)
    horo_end_raw = _geotab_last_value(hours_readings)
    horo_end = horo_end_raw / 3600.0 if horo_end_raw is not None else None

    if previous_record and previous_record.horo_end is not None:
        horo_start = previous_record.horo_end
    else:
        horo_start_raw = _geotab_first_value(hours_readings)
        horo_start = horo_start_raw / 3600.0 if horo_start_raw is not None else None
        if horo_start is not None:
            warnings.append(
                "Horometro inicial tomado de la lectura en el tanqueo anterior (punto exacto del corte)."
                if cutoff_mode
                else "Horometro inicial tomado de la primera lectura del mes (sin registro previo)."
            )

    hours_ecm = max(0.0, horo_end - horo_start) if horo_start is not None and horo_end is not None else None

    # Fuel (liters → gallons), fallback to DeviceTotalFuel
    fuel_gallons: float | None = None
    fuel_first = _geotab_first_value(fuel_readings)
    fuel_last = _geotab_last_value(fuel_readings)
    if fuel_first is not None and fuel_last is not None and (fuel_last - fuel_first) > 0:
        fuel_gallons = max(0.0, fuel_last - fuel_first) / _LITERS_PER_GALLON
    else:
        device_fuel_readings = get_status_data_for_month(api, device_id, _DIAG_DEVICE_FUEL, from_date, to_date)
        dv_first = _geotab_first_value(device_fuel_readings)
        dv_last = _geotab_last_value(device_fuel_readings)
        if dv_first is not None and dv_last is not None and (dv_last - dv_first) > 0:
            fuel_gallons = max(0.0, dv_last - dv_first) / _LITERS_PER_GALLON
            warnings.append("Combustible calculado usando DiagnosticDeviceTotalFuelId (fuente alternativa).")
        else:
            warnings.append("No se pudo determinar el consumo de combustible para el mes.")

    # GPS data from Trips
    kms_gps: float | None = None
    hours_gps: float | None = None
    if trips:
        kms_gps = sum(float(t.get("distance") or 0) for t in trips)
        total_hours = sum(
            _td_to_hours(t.get("drivingDuration")) + _td_to_hours(t.get("idlingDuration"))
            for t in trips
        )
        hours_gps = total_hours if total_hours > 0 else None

    all_present = all(v is not None for v in (odo_start, odo_end, horo_start, horo_end, fuel_gallons, kms_gps, hours_gps))
    status = "calculated" if all_present else "partial"

    return MonthlyPerformanceRecord(
        customer_id=target.customer_id,
        customer_database_id=target.customer_database_id,
        client_name=target.client_name,
        database_name=target.database_name,
        source_provider=target.provider_key,
        plate=target.plate,
        provider_vehicle_id=device_id,
        technical_number=target.technical_number,
        engine_name=target.engine_name,
        period_month=month,
        odo_start=odo_start,
        odo_end=odo_end,
        horo_start=horo_start,
        horo_end=horo_end,
        kms_ecm=kms_ecm,
        kms_gps=kms_gps,
        hours_ecm=hours_ecm,
        hours_gps=hours_gps,
        fuel_gallons=fuel_gallons,
        vocacional=target.vocacional,
        geotab_regression_count=(
            hourmeter_regressions.count
            if target.vocacional
            else odometer_regressions.count
        ),
        geotab_regression_total_km=odometer_regressions.total,
        geotab_regression_total_hours=hourmeter_regressions.total,
        calculation_status=status,
        warnings=warnings,
    )


class GeotabMonthlyPerformanceProvider:
    key = "geotab"

    def calculate_database_rows(
        self,
        *,
        month: str,
        year: int,
        month_number: int,
        previous_month: str,
        targets: list[PerformanceTarget],
        previous_records: dict[tuple[int, str], MonthlyPerformanceRecord],
        bindings: dict[tuple[str, int, str], BindingSnapshot],
        on_target_done: Callable[[], None] | None = None,
    ) -> ProviderCalculationResult:
        if not targets:
            return ProviderCalculationResult(records=[], binding_updates=[])

        sample = targets[0]
        if not sample.username or not sample.password:
            raise ValueError(
                f"La database {sample.database_name or sample.customer_database_id} no tiene credenciales Geotab completas."
            )

        api = get_authenticated_client(sample.username, sample.password, sample.database_name or "")
        from_date, to_date = get_geotab_month_range(year, month_number)

        rows: list[MonthlyPerformanceRecord] = []
        binding_updates: list[BindingUpsert] = []

        for target in targets:
            try:
                bound_id, is_manual = _select_binding(bindings=bindings, target=target)
                if is_manual:
                    device_id = bound_id
                else:
                    device = find_device_by_plate(api, target.plate, plate_prefix=target.provider_config.get("plate_prefix"))
                    resolved_id = str(device.get("id") or "").strip() if device else None
                    device_id = resolved_id or bound_id

                if not device_id:
                    binding_updates.append(
                        BindingUpsert(
                            target=target,
                            provider_vehicle_id=None,
                            binding_status="unbound",
                            last_error="No fue posible resolver el dispositivo en Geotab para esta placa.",
                        )
                    )
                    rows.append(
                        _build_status_record(
                            target=target,
                            month=month,
                            status="unbound",
                            warnings=["No fue posible resolver el dispositivo en Geotab para esta placa."],
                        )
                    )
                    continue

                binding_updates.append(
                    BindingUpsert(
                        target=target,
                        provider_vehicle_id=device_id,
                        binding_status="resolved",
                        last_error=None,
                    )
                )

                try:
                    record = _calculate_geotab_vehicle_record(
                        target=target,
                        month=month,
                        device_id=device_id,
                        api=api,
                        from_date=from_date,
                        to_date=to_date,
                        previous_record=previous_records.get((target.customer_database_id, target.plate)),
                    )
                    rows.append(record)
                except Exception as exc:
                    rows.append(
                        _build_status_record(
                            target=target,
                            month=month,
                            status="error",
                            provider_vehicle_id=device_id,
                            warnings=[f"Error calculando la placa en Geotab: {exc}"],
                        )
                    )
            finally:
                if on_target_done is not None:
                    try:
                        on_target_done()
                    except Exception:
                        pass

        return ProviderCalculationResult(records=rows, binding_updates=binding_updates)


# ==============================================================================
# FROTCOM MONTHLY PERFORMANCE PROVIDER
# ==============================================================================


def _calculate_frotcom_vehicle_record(
    *,
    target: PerformanceTarget,
    config: FrotcomConfig,
    month: str,
    vehicle_id: str,
    df_iso: str,
    dt_iso: str,
    month_start,
    month_end,
    range_start_utc,
    range_end_utc,
    previous_record: MonthlyPerformanceRecord | None,
) -> MonthlyPerformanceRecord:
    warnings: list[str] = []

    summary = get_frotcom_mileage_and_time(config, vehicle_id, df_iso, dt_iso) or {}

    def _summary_float(field: str) -> float | None:
        try:
            value = summary.get(field) if isinstance(summary, dict) else None
            return float(value) if value is not None else None
        except (TypeError, ValueError):
            return None

    summary_can_kms = _summary_float("mileageCanKms")
    summary_fuel_liters = _summary_float("totalFuelUsed")

    try:
        trip_odos = get_frotcom_trip_odometers(config, vehicle_id, range_start_utc, range_end_utc)
    except FrotcomAuthError:
        raise
    except Exception as exc:
        trip_odos = FrotcomTripOdometers(
            odo_start=None,
            odo_end=None,
            reconstructed_start=False,
            reconstruction_incomplete=False,
            trip_count=0,
            warnings=[f"No fue posible obtener los viajes de Frotcom: {exc}"],
        )
    warnings.extend(trip_odos.warnings)

    if previous_record and previous_record.odo_end is not None:
        odo_start = previous_record.odo_end
    elif trip_odos.odo_start is not None:
        odo_start = trip_odos.odo_start
    else:
        odo_start = None

    odo_end = trip_odos.odo_end
    if odo_end is None and odo_start is not None and summary_can_kms is not None:
        odo_end = odo_start + max(0.0, summary_can_kms)
        warnings.append(
            "Odometro final reconstruido con mileageCanKms de Frotcom (viajes sin odometro)."
        )

    # Frotcom expone el horometro acumulado CAN como engineHours en
    # vehicleCanInfo. El resumen mensual solo trae tiempo de conduccion GPS,
    # por lo que no se debe usar como sustituto de horas ECM.
    horo_start = previous_record.horo_end if previous_record and previous_record.horo_end is not None else None
    horo_end = None

    # vehicleCanInfo no esta habilitado para todos los tenants. Se consulta para
    # completar odometros, combustible y el horometro CAN (engineHours).
    first_reading = None
    last_reading = None
    needs_can_fuel = summary_fuel_liters is None
    needs_first_can = odo_start is None or horo_start is None or needs_can_fuel
    needs_last_can = odo_end is None or horo_end is None or needs_can_fuel
    can_available = True
    can_daily_cache: dict[str, list[dict[str, Any]]] = {}
    if needs_first_can:
        try:
            first_reading = find_frotcom_first_reading(
                config, vehicle_id, month_start, month_end, can_daily_cache
            )
        except FrotcomAuthError:
            raise
        except Exception as exc:
            can_available = False
            warnings.append(f"Lecturas CAN de Frotcom no disponibles; se usaron los resumenes mensuales: {exc}")
    if needs_last_can and can_available:
        try:
            last_reading = find_frotcom_last_reading(
                config, vehicle_id, month_start, month_end, can_daily_cache
            )
        except FrotcomAuthError:
            raise
        except Exception as exc:
            warnings.append(f"Lecturas CAN de Frotcom no disponibles; se usaron los resumenes mensuales: {exc}")

    if odo_start is None and first_reading is not None:
        odo_start = first_reading.odometer
        if odo_start is not None:
            warnings.append("Odometro inicial tomado de la primera lectura CAN (viajes sin odometro).")
    if odo_end is None and last_reading is not None:
        odo_end = last_reading.odometer
        if odo_end is not None:
            warnings.append("Odometro final tomado de la ultima lectura CAN (viajes sin odometro).")

    if horo_start is None and first_reading is not None:
        horo_start = first_reading.engine_hours
        if horo_start is not None:
            warnings.append("Horometro inicial tomado de la primera lectura CAN de Frotcom.")
    if last_reading is not None:
        horo_end = last_reading.engine_hours
        if horo_end is not None:
            warnings.append("Horometro final tomado de la ultima lectura CAN de Frotcom.")

    if odo_end is None and odo_start is not None and summary_can_kms is not None:
        odo_end = odo_start + max(0.0, summary_can_kms)
        warnings.append(
            "Odometro final reconstruido con mileageCanKms de Frotcom (CAN no disponible)."
        )

    if not summary and trip_odos.odo_start is None and trip_odos.odo_end is None and first_reading is None and last_reading is None:
        return _build_status_record(
            target=target,
            month=month,
            status="no_data",
            warnings=[*warnings, "No se encontraron datos de Frotcom para el mes solicitado."],
            provider_vehicle_id=vehicle_id,
        )

    kms_ecm = (
        max(0.0, odo_end - odo_start)
        if odo_start is not None and odo_end is not None
        else summary_can_kms
    )

    kms_gps_raw = summary.get("mileageGpsKms") if isinstance(summary, dict) else None
    try:
        kms_gps = float(kms_gps_raw) if kms_gps_raw is not None else None
    except (TypeError, ValueError):
        kms_gps = None

    hours_gps = hours_from_seconds(summary.get("drivingTimeSeconds") if isinstance(summary, dict) else None)
    hours_ecm = (
        max(0.0, horo_end - horo_start)
        if horo_start is not None and horo_end is not None
        else None
    )
    if hours_ecm is None:
        warnings.append("No se pudo determinar el horometro CAN de Frotcom para el mes.")

    fuel_start = first_reading.total_fuel_used if first_reading else None
    fuel_end = last_reading.total_fuel_used if last_reading else None
    if summary_fuel_liters is not None:
        fuel_gallons = liters_to_gallons(max(0.0, summary_fuel_liters))
    elif fuel_start is not None and fuel_end is not None and (fuel_end - fuel_start) >= 0:
        fuel_gallons = liters_to_gallons(max(0.0, fuel_end - fuel_start))
    else:
        fuel_gallons = None
        warnings.append("No se pudo determinar el consumo de combustible en Frotcom para el mes.")

    all_present = all(
        value is not None
        for value in (odo_start, odo_end, horo_start, horo_end, fuel_gallons, kms_gps, hours_gps)
    )
    status = "calculated" if all_present else "partial"

    return MonthlyPerformanceRecord(
        customer_id=target.customer_id,
        customer_database_id=target.customer_database_id,
        client_name=target.client_name,
        database_name=target.database_name,
        source_provider=target.provider_key,
        plate=target.plate,
        provider_vehicle_id=vehicle_id,
        technical_number=target.technical_number,
        engine_name=target.engine_name,
        period_month=month,
        odo_start=odo_start,
        odo_end=odo_end,
        horo_start=horo_start,
        horo_end=horo_end,
        kms_ecm=kms_ecm,
        kms_gps=kms_gps,
        hours_ecm=hours_ecm,
        hours_gps=hours_gps,
        fuel_gallons=fuel_gallons,
        calculation_status=status,
        warnings=warnings,
    )


def _build_frotcom_config(target: PerformanceTarget) -> FrotcomConfig:
    if not target.username or not target.password:
        raise ValueError(
            f"La database {target.database_name or target.customer_database_id} no tiene credenciales Frotcom completas."
        )
    return FrotcomConfig(username=target.username, password=target.password)


class FrotcomMonthlyPerformanceProvider:
    key = "frotcom"

    def calculate_database_rows(
        self,
        *,
        month: str,
        year: int,
        month_number: int,
        previous_month: str,
        targets: list[PerformanceTarget],
        previous_records: dict[tuple[int, str], MonthlyPerformanceRecord],
        bindings: dict[tuple[str, int, str], BindingSnapshot],
        on_target_done: Callable[[], None] | None = None,
    ) -> ProviderCalculationResult:
        if not targets:
            return ProviderCalculationResult(records=[], binding_updates=[])

        df_iso, dt_iso, month_start, month_end = get_frotcom_month_range(year, month_number)
        range_start_utc, range_end_utc = get_frotcom_month_range_utc_bounds(year, month_number)

        rows: list[MonthlyPerformanceRecord] = []
        binding_updates: list[BindingUpsert] = []
        prepared_targets: list[tuple[PerformanceTarget, FrotcomConfig, str]] = []

        vehicles_cache: dict[tuple[str, str, str], list[dict]] = {}
        vehicles_list_failures: dict[tuple[str, str, str], str] = {}

        def _notify_target_done():
            if on_target_done is not None:
                try:
                    on_target_done()
                except Exception:
                    pass

        for target in targets:
            try:
                config = _build_frotcom_config(target)
            except ValueError as exc:
                binding_updates.append(
                    BindingUpsert(
                        target=target,
                        provider_vehicle_id=None,
                        binding_status="error",
                        last_error=str(exc),
                    )
                )
                rows.append(
                    _build_status_record(
                        target=target,
                        month=month,
                        status="error",
                        warnings=[str(exc)],
                    )
                )
                _notify_target_done()
                continue

            cache_key = config.cache_key()
            if cache_key in vehicles_list_failures:
                message = vehicles_list_failures[cache_key]
                binding_updates.append(
                    BindingUpsert(
                        target=target,
                        provider_vehicle_id=None,
                        binding_status="error",
                        last_error=message,
                    )
                )
                rows.append(
                    _build_status_record(
                        target=target,
                        month=month,
                        status="error",
                        warnings=[message],
                    )
                )
                _notify_target_done()
                continue

            bound_id, is_manual = _select_binding(bindings=bindings, target=target)

            if is_manual:
                vehicle_id = bound_id
            else:
                vehicle_id = bound_id
                if not vehicle_id:
                    cached = vehicles_cache.get(cache_key)
                    if cached is None and cache_key not in vehicles_list_failures:
                        try:
                            cached = list_frotcom_vehicles(config)
                            vehicles_cache[cache_key] = cached
                        except FrotcomAuthError as exc:
                            vehicles_list_failures[cache_key] = str(exc)
                            cached = []
                        except Exception as exc:
                            vehicles_list_failures[cache_key] = (
                                f"No fue posible listar vehiculos en Frotcom: {exc}"
                            )
                            cached = []
                    if cache_key in vehicles_list_failures:
                        message = vehicles_list_failures[cache_key]
                        binding_updates.append(
                            BindingUpsert(
                                target=target,
                                provider_vehicle_id=None,
                                binding_status="error",
                                last_error=message,
                            )
                        )
                        rows.append(
                            _build_status_record(
                                target=target,
                                month=month,
                                status="error",
                                warnings=[message],
                            )
                        )
                        _notify_target_done()
                        continue
                    vehicle_id = find_frotcom_vehicle_id_by_plate(target.plate, config, cached)

            if not vehicle_id:
                binding_updates.append(
                    BindingUpsert(
                        target=target,
                        provider_vehicle_id=None,
                        binding_status="unbound",
                        last_error="No fue posible resolver el ID del vehiculo en Frotcom para esta placa.",
                    )
                )
                rows.append(
                    _build_status_record(
                        target=target,
                        month=month,
                        status="unbound",
                        warnings=["No fue posible resolver el ID del vehiculo en Frotcom para esta placa."],
                    )
                )
                _notify_target_done()
                continue

            binding_updates.append(
                BindingUpsert(
                    target=target,
                    provider_vehicle_id=vehicle_id,
                    binding_status="resolved",
                    last_error=None,
                )
            )

            prepared_targets.append((target, config, vehicle_id))

        try:
            configured_workers = int(os.getenv("FROTCOM_MAX_WORKERS", "4"))
        except ValueError:
            configured_workers = 4
        max_workers = max(1, min(configured_workers, 8, len(prepared_targets) or 1))

        def _calculate_prepared(
            item: tuple[PerformanceTarget, FrotcomConfig, str],
        ) -> MonthlyPerformanceRecord:
            target, config, vehicle_id = item
            return _calculate_frotcom_vehicle_record(
                target=target,
                config=config,
                month=month,
                vehicle_id=vehicle_id,
                df_iso=df_iso,
                dt_iso=dt_iso,
                month_start=month_start,
                month_end=month_end,
                range_start_utc=range_start_utc,
                range_end_utc=range_end_utc,
                previous_record=previous_records.get((target.customer_database_id, target.plate)),
            )

        # Ejecutamos una placa de cada juego de credenciales como preflight. Si
        # la autenticacion falla, evitamos disparar el resto del grupo. Las
        # placas restantes si se procesan concurrentemente.
        prepared_by_credentials: dict[
            tuple[str, str, str], list[tuple[PerformanceTarget, FrotcomConfig, str]]
        ] = {}
        for item in prepared_targets:
            prepared_by_credentials.setdefault(item[1].cache_key(), []).append(item)

        parallel_targets: list[tuple[PerformanceTarget, FrotcomConfig, str]] = []
        for credential_targets in prepared_by_credentials.values():
            first_item, *remaining_items = credential_targets
            first_target, _first_config, first_vehicle_id = first_item
            try:
                rows.append(_calculate_prepared(first_item))
                parallel_targets.extend(remaining_items)
                _notify_target_done()
            except FrotcomAuthError as exc:
                for failed_target, _failed_config, failed_vehicle_id in credential_targets:
                    rows.append(
                        _build_status_record(
                            target=failed_target,
                            month=month,
                            status="error",
                            provider_vehicle_id=failed_vehicle_id,
                            warnings=[str(exc)],
                        )
                    )
                    _notify_target_done()
            except Exception as exc:
                rows.append(
                    _build_status_record(
                        target=first_target,
                        month=month,
                        status="error",
                        provider_vehicle_id=first_vehicle_id,
                        warnings=[f"Error calculando la placa en Frotcom: {exc}"],
                    )
                )
                parallel_targets.extend(remaining_items)
                _notify_target_done()

        with ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="frotcom") as executor:
            futures = {
                executor.submit(_calculate_prepared, item): item
                for item in parallel_targets
            }
            for future in as_completed(futures):
                target, config, vehicle_id = futures[future]
                try:
                    rows.append(future.result())
                except FrotcomAuthError as exc:
                    rows.append(
                        _build_status_record(
                            target=target,
                            month=month,
                            status="error",
                            provider_vehicle_id=vehicle_id,
                            warnings=[str(exc)],
                        )
                    )
                except Exception as exc:
                    rows.append(
                        _build_status_record(
                            target=target,
                            month=month,
                            status="error",
                            provider_vehicle_id=vehicle_id,
                            warnings=[f"Error calculando la placa en Frotcom: {exc}"],
                        )
                    )
                _notify_target_done()

        return ProviderCalculationResult(records=rows, binding_updates=binding_updates)


# ==============================================================================
# LOGITRACS TRITON MONTHLY PERFORMANCE PROVIDER
# ==============================================================================


def _normalize_logitracs_plate(value: str | None) -> str:
    return str(value or "").strip().upper()


def _build_logitracs_error_result(
    *,
    month: str,
    targets: list[PerformanceTarget],
    message: str,
) -> ProviderCalculationResult:
    return ProviderCalculationResult(
        records=[
            _build_status_record(
                target=target,
                month=month,
                status="error",
                warnings=[message],
            )
            for target in targets
        ],
        binding_updates=[
            BindingUpsert(
                target=target,
                provider_vehicle_id=None,
                binding_status="error",
                last_error=message,
            )
            for target in targets
        ],
    )


def _calculate_logitracs_vehicle_record(
    *,
    target: PerformanceTarget,
    month: str,
    provider_vehicle_id: str,
    current_row: dict[str, Any] | None,
    previous_row: dict[str, Any] | None,
    previous_record: MonthlyPerformanceRecord | None,
) -> MonthlyPerformanceRecord:
    warnings: list[str] = []

    if current_row is None and previous_row is None:
        return _build_status_record(
            target=target,
            month=month,
            status="no_data",
            warnings=["No hay datos LogiTracs para el mes solicitado."],
            provider_vehicle_id=provider_vehicle_id,
        )

    kms_period = extract_kms_period(current_row)
    odo_end = extract_odometer_end(current_row)
    if odo_end is not None and odo_end <= 0 and (kms_period or 0) > 0:
        warnings.append(
            "Odometro final reportado en 0 por LogiTracs; se estimara usando el kilometraje del periodo."
        )
        odo_end = None

    if previous_record and previous_record.odo_end is not None:
        odo_start = previous_record.odo_end
    elif previous_row is not None:
        odo_start = extract_odometer_end(previous_row)
        if odo_start is not None:
            warnings.append("Odometro inicial tomado del cierre LogiTracs del mes anterior.")
    elif odo_end is not None:
        if kms_period is not None:
            odo_start = max(0.0, odo_end - kms_period)
            warnings.append("Odometro inicial estimado a partir del kilometraje del mes actual.")
        else:
            odo_start = None
    else:
        odo_start = None

    if odo_end is None and odo_start is not None and kms_period is not None:
        odo_end = odo_start + kms_period
        warnings.append("Odometro final estimado a partir del cierre previo mas el kilometraje del periodo.")

    if odo_start is not None and odo_end is not None:
        kms_ecm = max(0.0, odo_end - odo_start)
    else:
        kms_ecm = kms_period
        if kms_ecm is not None:
            warnings.append("Kilometraje mensual tomado del reporte LogiTracs al no poder derivar ambos odometros.")

    hours_gps = extract_triton_engine_hours(current_row)
    fuel_gallons = extract_triton_fuel_liters(current_row)

    if fuel_gallons is not None:
        warnings.append("LogiTracs 'Combustible' interpretado como galones (unidad por confirmar).")

    all_present = all(v is not None for v in (odo_start, odo_end, kms_ecm, hours_gps, fuel_gallons))
    status = "calculated" if all_present else "partial"

    return MonthlyPerformanceRecord(
        customer_id=target.customer_id,
        customer_database_id=target.customer_database_id,
        client_name=target.client_name,
        database_name=target.database_name,
        source_provider=target.provider_key,
        plate=target.plate,
        provider_vehicle_id=provider_vehicle_id,
        technical_number=target.technical_number,
        engine_name=target.engine_name,
        period_month=month,
        odo_start=odo_start,
        odo_end=odo_end,
        horo_start=None,
        horo_end=None,
        kms_ecm=kms_ecm,
        kms_gps=None,
        hours_ecm=None,
        hours_gps=hours_gps,
        fuel_gallons=fuel_gallons,
        calculation_status=status,
        warnings=warnings,
    )


class LogitracsTritonMonthlyPerformanceProvider:
    key = "logitracs_triton"

    def _build_config(self, target: PerformanceTarget) -> LogitracsTritonConfig:
        provider_config = target.provider_config if isinstance(target.provider_config, dict) else {}
        if not target.username or not target.password:
            raise ValueError(
                f"La database {target.database_name or target.customer_database_id} no tiene credenciales LogiTracs Triton completas."
            )
        codigo_empresa = str(provider_config.get("codigo_empresa") or "").strip()
        if not codigo_empresa:
            raise ValueError(
                f"La database {target.database_name or target.customer_database_id} no tiene codigo_empresa de LogiTracs Triton."
            )
        password_web = str(provider_config.get("password_web") or "").strip() or target.password
        return LogitracsTritonConfig(
            username=target.username,
            password=target.password,
            password_web=password_web,
            codigo_empresa=codigo_empresa,
            triton_base_url=str(provider_config.get("triton_login_url") or "https://triton.logitracs.com/Logitracs.Triton").strip().rstrip("/"),
            logivim_base_url=str(provider_config.get("logivim_base_url") or "https://triton.logitracs.com/LogiVIMwebTriton/public").strip().rstrip("/"),
        )

    def calculate_database_rows(
        self,
        *,
        month: str,
        year: int,
        month_number: int,
        previous_month: str,
        targets: list[PerformanceTarget],
        previous_records: dict[tuple[int, str], MonthlyPerformanceRecord],
        bindings: dict[tuple[str, int, str], BindingSnapshot],
        on_target_done: Callable[[], None] | None = None,
    ) -> ProviderCalculationResult:
        if not targets:
            return ProviderCalculationResult(records=[], binding_updates=[])

        def _notify_target_done():
            if on_target_done is not None:
                try:
                    on_target_done()
                except Exception:
                    pass

        try:
            client = LogitracsTritonClient(self._build_config(targets[0]))
        except ValueError as exc:
            return _build_logitracs_error_result(month=month, targets=targets, message=str(exc))

        prev_year = int(previous_month[:4])
        prev_month_num = int(previous_month[-2:])

        def flat_month(year: int, month: int) -> tuple[str, str]:
            import calendar
            last_day = calendar.monthrange(year, month)[1]
            start = f"{year:04d}-{month:02d}-01"
            end = f"{year:04d}-{month:02d}-{last_day:02d}"
            return start, end

        start_date, end_date = flat_month(year, month_number)
        prev_start, prev_end = flat_month(prev_year, prev_month_num)

        try:
            current_rows_raw = client.get_fleet_operational_report(start_date, end_date)
            previous_rows_raw = client.get_fleet_operational_report(prev_start, prev_end)
        except LogitracsTritonAuthError as exc:
            return _build_logitracs_error_result(month=month, targets=targets, message=str(exc))
        except RuntimeError as exc:
            logger.exception("Error fetching LogiTracs Triton report")
            return _build_logitracs_error_result(month=month, targets=targets, message=str(exc))

        current_rows = {
            plate: row
            for row in current_rows_raw
            if (plate := _normalize_logitracs_plate(extract_triton_plate(row)))
        }
        previous_rows = {
            plate: row
            for row in previous_rows_raw
            if (plate := _normalize_logitracs_plate(extract_triton_plate(row)))
        }

        rows: list[MonthlyPerformanceRecord] = []
        binding_updates: list[BindingUpsert] = []

        for target in targets:
            plate_key = _normalize_logitracs_plate(target.plate)
            current_row = current_rows.get(plate_key)
            previous_row = previous_rows.get(plate_key)

            bound_id, is_manual = _select_binding(bindings=bindings, target=target)
            if is_manual and bound_id:
                provider_vehicle_id = bound_id
            elif current_row or previous_row:
                provider_vehicle_id = plate_key
            else:
                provider_vehicle_id = None

            if provider_vehicle_id is None:
                binding_updates.append(
                    BindingUpsert(
                        target=target,
                        provider_vehicle_id=None,
                        binding_status="unbound",
                        last_error="La placa no aparece en el informe operacional de LogiTracs para el mes.",
                    )
                )
                rows.append(
                    _build_status_record(
                        target=target,
                        month=month,
                        status="unbound",
                        warnings=["La placa no aparece en el informe operacional de LogiTracs para el mes."],
                    )
                )
                _notify_target_done()
                continue

            if current_row or previous_row:
                binding_updates.append(
                    BindingUpsert(
                        target=target,
                        provider_vehicle_id=provider_vehicle_id,
                        binding_status="resolved",
                        last_error=None,
                    )
                )

            try:
                prev_rec = previous_records.get((target.customer_database_id, target.plate))
                rows.append(
                    _calculate_logitracs_vehicle_record(
                        target=target,
                        month=month,
                        provider_vehicle_id=provider_vehicle_id,
                        current_row=current_row,
                        previous_row=previous_row,
                        previous_record=prev_rec,
                    )
                )
            except LogitracsTritonAuthError as exc:
                rows.append(
                    _build_status_record(
                        target=target,
                        month=month,
                        status="error",
                        provider_vehicle_id=provider_vehicle_id,
                        warnings=[str(exc)],
                    )
                )
            except Exception as exc:
                logger.exception("Error calculating LogiTracs Triton record")
                rows.append(
                    _build_status_record(
                        target=target,
                        month=month,
                        status="error",
                        provider_vehicle_id=provider_vehicle_id,
                        warnings=[f"Error calculando la placa en LogiTracs Triton: {exc}"],
                    )
                )

            _notify_target_done()

        return ProviderCalculationResult(records=rows, binding_updates=binding_updates)


_MONTHLY_PERFORMANCE_PROVIDERS: dict[str, MonthlyPerformanceProvider] = {
    "artimo": ArtimoMonthlyPerformanceProvider(),
    "geotab": GeotabMonthlyPerformanceProvider(),
    "frotcom": FrotcomMonthlyPerformanceProvider(),
    "logitracs_triton": LogitracsTritonMonthlyPerformanceProvider(),
}


def get_monthly_performance_provider(provider_key: str) -> MonthlyPerformanceProvider | None:
    return _MONTHLY_PERFORMANCE_PROVIDERS.get(provider_key)
