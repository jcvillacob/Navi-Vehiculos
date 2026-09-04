from __future__ import annotations

import logging
import os
import re
import threading
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, as_completed, wait

# Cada cuanto despierta el hilo principal del provider Geotab mientras espera chunks
# (heartbeat del job + chequeo de cancelacion).
_GEOTAB_WAIT_HEARTBEAT_SECONDS = 30.0
from dataclasses import dataclass
from datetime import datetime, time as dt_time, timedelta, timezone
from typing import Any, Callable, Protocol

from app.clients.artimo_client import (
    ArtimoAuthError,
    ArtimoClient,
    ArtimoConfig,
    ArtimoTripWindow,
    extract_consumption_liters,
    extract_distance_km,
    extract_engine_time_hours,
    extract_horometer,
    extract_odometer,
    extract_plate,
    extract_provider_vehicle_id,
    gallons_from_liters,
    select_trips_in_window,
    sort_rows_by_timestamp,
)
from app.schemas.vehicle import MonthlyPerformanceRecord
from app.clients.frotcom_client import (
    FrotcomAuthError,
    FrotcomConfig,
    FrotcomTripOdometers,
    build_chronometer_map as build_frotcom_chronometer_map,
    estimate_hourmeter_end_from_chronometer,
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
    _classify_error as _classify_geotab_error,
    _device_is_active,
    _find_device_in_collection,
    _parse_geotab_datetime,
    build_plate_index,
    bundle_chunk_size_for,
    get_authenticated_client,
    get_cached_devices,
    get_geotab_month_range,
    get_month_data_bundle,
    get_month_data_bundles,
    lookup_plate_index,
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
from app.services.job_control import JobCancelled, check_stop
from app.services.performance_types import (
    BindingSnapshot,
    BindingUpsert,
    PerformanceTarget,
    ProviderCalculationResult,
)


_logger = logging.getLogger(__name__)


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
        should_stop: "Callable[[], bool] | None" = None,
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


# ------------------------------------------------------------------------------
# Trazabilidad de fuentes (hardening rendimientos, sep 2026)
# ------------------------------------------------------------------------------
# Valores permitidos en MonthlyPerformanceRecord.*_source. El validador de
# plausibilidad (performance_validation.py) compara inicio vs fin y marca
# `source_mix` cuando difieren.
SOURCE_PREVIOUS = "previous"          # cierre del mes anterior ya persistido
SOURCE_FIRST_READING = "first_reading"  # primera lectura del mes en el proveedor
SOURCE_LAST_READING = "last_reading"    # ultima lectura del mes en el proveedor
SOURCE_GPS = "gps"                    # odometro GPS (Artimo)
SOURCE_CAN = "can"                    # lectura CAN (Frotcom vehicleCanInfo)
SOURCE_TRIPS = "trips"                # odometro/horometro tomado de un viaje
SOURCE_ESTIMATED = "estimated"        # reconstruido (resta/suma de recorridos, chronometer)
SOURCE_DIAGNOSTIC = "diagnostic"      # StatusData de un diagnostico Geotab

# D8: fraccion de viajes Artimo con un campo en null a partir de la cual la
# metrica derivada se anula en vez de solo avisar.
_ARTIMO_MISSING_FIELD_RATIO = 0.10
# Retrocesos Geotab acumulados por encima de esta fraccion de la metrica del mes
# degradan el registro a `partial` (el validador aplica el mismo umbral).
_GEOTAB_REGRESSION_SIGNIFICANT_RATIO = 0.05


def _num(value: float) -> str:
    """Numero legible en warnings: hasta 2 decimales, sin ceros de relleno."""
    text = f"{value:.2f}".rstrip("0").rstrip(".")
    return text if text not in ("", "-0") else "0"


def _cap_partial(status: str) -> str:
    """Degrada `calculated` a `partial`; nunca sube ni toca otros estados."""
    return "partial" if status == "calculated" else status


def _positive_delta(
    start: float | None,
    end: float | None,
    *,
    label: str,
    unit: str,
    warnings: list[str],
    metric: str | None = None,
) -> float | None:
    """`end - start` sin recortar a cero (D1).

    Antes se hacia `max(0.0, end - start)`: un contador que retrocedia (cambio
    de equipo, reset del ECM, lectura corrupta) se registraba como "0 km" y
    contaminaba promedios. Ahora, si `end < start`, devuelve None y deja un
    warning con ambas lecturas; el llamador degrada el estado a `partial`.
    Con start o end faltantes devuelve None sin avisar.
    """
    if start is None or end is None:
        return None
    if end < start:
        if metric is None:
            metric = "horas ECM no calculadas" if unit == "h" else "kilometraje ECM no calculado"
        warnings.append(
            f"{label} retrocede: {_num(start)} → {_num(end)} ({_num(end - start)} {unit}); {metric}."
        )
        return None
    return end - start


def _derive_start_values(
    *,
    previous_record: MonthlyPerformanceRecord | None,
    previous_trip: dict | None,
    current_trip: dict | None,
    gps_rows: list[dict],
    warnings: list[str],
    window_distance_km: float | None = None,
    window_engine_hours: float | None = None,
) -> tuple[float | None, float | None, str | None, str | None]:
    """Odometro/horometro inicial de Artimo con su procedencia.

    Devuelve ``(odo_start, horo_start, odo_start_source, horo_start_source)``;
    las fuentes usan las constantes ``SOURCE_*`` (``previous``, ``trips``,
    ``estimated``, ``gps``).
    """
    odo_start: float | None = None
    horo_start: float | None = None
    odo_source: str | None = None
    horo_source: str | None = None

    if previous_record and previous_record.odo_end is not None:
        odo_start = previous_record.odo_end
        odo_source = SOURCE_PREVIOUS
    elif previous_trip is not None:
        odo_start = extract_odometer(previous_trip)
        if odo_start is not None:
            odo_source = SOURCE_TRIPS
            warnings.append("Odometro inicial tomado del cierre de Artimo del mes anterior.")
    elif current_trip is not None:
        current_odo_end = extract_odometer(current_trip)
        current_distance = (
            window_distance_km if window_distance_km is not None else extract_distance_km(current_trip)
        )
        if current_odo_end is not None and current_distance is not None:
            estimated = current_odo_end - current_distance
            if estimated < 0:
                # Antes se recortaba a 0 y el mes "arrancaba" en cero km.
                warnings.append(
                    "Odometro inicial no estimable: el recorrido del mes "
                    f"({_num(current_distance)} km) supera el odometro final ({_num(current_odo_end)} km)."
                )
            else:
                odo_start = estimated
                odo_source = SOURCE_ESTIMATED
                warnings.append("Odometro inicial estimado con el acumulado del mes actual.")
        if odo_start is None and gps_rows:
            gps_odo = extract_odometer(gps_rows[0])
            if gps_odo is not None:
                odo_start = gps_odo
                odo_source = SOURCE_GPS
                warnings.append("Odometro inicial estimado con la primera lectura GPS del mes.")

    if previous_record and previous_record.horo_end is not None:
        horo_start = previous_record.horo_end
        horo_source = SOURCE_PREVIOUS
    elif previous_trip is not None:
        horo_start = extract_horometer(previous_trip)
        if horo_start is not None:
            horo_source = SOURCE_TRIPS
            warnings.append("Horometro inicial tomado del cierre de Artimo del mes anterior.")
    elif current_trip is not None:
        current_horo_end = extract_horometer(current_trip)
        current_engine_time = (
            window_engine_hours if window_engine_hours is not None else extract_engine_time_hours(current_trip)
        )
        if current_horo_end is not None and current_engine_time is not None:
            estimated = current_horo_end - current_engine_time
            if estimated < 0:
                warnings.append(
                    "Horometro inicial no estimable: las horas del mes "
                    f"({_num(current_engine_time)} h) superan el horometro final ({_num(current_horo_end)} h)."
                )
            else:
                horo_start = estimated
                horo_source = SOURCE_ESTIMATED
                warnings.append("Horometro inicial estimado con las horas del mes actual.")

    return odo_start, horo_start, odo_source, horo_source


def _apply_artimo_missing_fields(
    trip_window: ArtimoTripWindow,
    *,
    warnings: list[str],
    source_meta: dict[str, Any],
) -> dict[str, bool]:
    """D8: viajes Artimo con distancia/horas/consumo en null.

    El agregado los sumaba como 0.0 en silencio. Ahora se avisa siempre y, si
    faltan en mas del umbral (10% de los viajes), la metrica derivada se anula.
    Devuelve que metricas quedaron invalidadas: ``{"distance": bool, ...}``.
    """
    total = trip_window.trip_count
    invalidated = {"distance": False, "hours": False, "consumption": False}
    if not total:
        return invalidated
    missing = {
        "distance": trip_window.missing_distance,
        "hours": trip_window.missing_hours,
        "consumption": trip_window.missing_consumption,
    }
    if any(missing.values()):
        source_meta["artimo_trips_missing"] = {"total": total, **missing}
    labels = {
        "distance": ("distancia", "el recorrido"),
        "hours": ("horas de motor", "las horas GPS"),
        "consumption": ("consumo", "el combustible"),
    }
    threshold = _ARTIMO_MISSING_FIELD_RATIO * total
    for field_name, count in missing.items():
        if not count:
            continue
        field_label, metric_label = labels[field_name]
        if count > threshold:
            invalidated[field_name] = True
            warnings.append(
                f"{count} de {total} viaje(s) sin {field_label} reportada por Artimo "
                f"(> {_ARTIMO_MISSING_FIELD_RATIO:.0%}): {metric_label} del mes se omite."
            )
        else:
            warnings.append(
                f"{count} de {total} viaje(s) sin {field_label} reportada por Artimo; "
                f"{metric_label} del mes puede estar subestimado."
            )
    return invalidated


def _calculate_vehicle_record(
    *,
    target: PerformanceTarget,
    month: str,
    current_trip: dict | None,
    previous_trip: dict | None,
    previous_record: MonthlyPerformanceRecord | None,
    provider_vehicle_id: str,
    gps_rows: list[dict],
    trip_window: ArtimoTripWindow | None = None,
    gps_truncated: bool = False,
) -> MonthlyPerformanceRecord:
    warnings: list[str] = []
    source_meta: dict[str, Any] = {}
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
    kms_gps = _positive_delta(
        gps_odo_start,
        gps_odo_end,
        label="Odómetro GPS",
        unit="km",
        warnings=warnings,
        metric="kilometraje GPS no calculado",
    )
    gps_regressed = kms_gps is None and gps_odo_start is not None and gps_odo_end is not None

    odo_start, horo_start, odo_start_source, horo_start_source = _derive_start_values(
        previous_record=previous_record,
        previous_trip=previous_trip,
        current_trip=current_trip,
        gps_rows=gps_rows,
        warnings=warnings,
        window_distance_km=trip_window.distance_km if trip_window else None,
        window_engine_hours=trip_window.engine_hours if trip_window else None,
    )

    if current_trip is not None:
        odo_end = extract_odometer(current_trip)
        odo_end_source = SOURCE_TRIPS if odo_end is not None else None
        horo_end = extract_horometer(current_trip)
        horo_end_source = SOURCE_TRIPS if horo_end is not None else None
    else:
        odo_end = gps_odo_end
        odo_end_source = SOURCE_GPS if odo_end is not None else None
        horo_end = None
        horo_end_source = None

    if trip_window is not None:
        fuel_gallons = gallons_from_liters(trip_window.fuel_liters)
        hours_gps = trip_window.engine_hours
    else:
        fuel_gallons = gallons_from_liters(extract_consumption_liters(current_trip))
        hours_gps = extract_engine_time_hours(current_trip)

    status = "calculated"
    if trip_window is not None:
        invalidated = _apply_artimo_missing_fields(trip_window, warnings=warnings, source_meta=source_meta)
        if invalidated["consumption"]:
            fuel_gallons = None
            status = _cap_partial(status)
        if invalidated["hours"]:
            hours_gps = None
            status = _cap_partial(status)
            if horo_start_source == SOURCE_ESTIMATED:
                horo_start, horo_start_source = None, None
        if invalidated["distance"] and odo_start_source == SOURCE_ESTIMATED:
            odo_start, odo_start_source = None, None
            status = _cap_partial(status)

    # Bases distintas (viajes vs GPS) no son comparables entre si.
    if odo_start_source and odo_end_source and (odo_start_source == SOURCE_GPS) != (odo_end_source == SOURCE_GPS):
        warnings.append(
            f"Odometro inicial ({odo_start_source}) y final ({odo_end_source}) vienen de bases distintas "
            "(viajes Artimo vs GPS); el kilometraje ECM puede no ser comparable."
        )

    kms_ecm = _positive_delta(odo_start, odo_end, label="Odómetro", unit="km", warnings=warnings)
    if kms_ecm is None and odo_start is not None and odo_end is not None:
        status = _cap_partial(status)

    hours_ecm = _positive_delta(horo_start, horo_end, label="Horómetro", unit="h", warnings=warnings)
    if hours_ecm is None and horo_start is not None and horo_end is not None:
        status = _cap_partial(status)
    if gps_regressed:
        status = _cap_partial(status)

    if trip_window is not None and trip_window.trips_after_window:
        warnings.append(
            f"{trip_window.trips_after_window} viaje(s) terminan despues del corte del mes: "
            "su recorrido queda contado en el mes siguiente."
        )
    if gps_truncated:
        warnings.append(
            "El reporte GPS llego al tope de filas incluso partiendo la ventana; "
            "los kilometros GPS pueden estar incompletos."
        )
    if current_trip is None:
        status = "partial"
        warnings.append("No hubo viajes en el mes; se completo solo con datos GPS disponibles.")
    elif any(value is None for value in (odo_start, odo_end, horo_start, horo_end, fuel_gallons)):
        status = "partial"
        warnings.append("No fue posible completar todos los campos base del corte mensual.")

    if trip_window is not None:
        source_meta["artimo_trip_count"] = trip_window.trip_count
    if gps_rows:
        source_meta["gps_rows"] = len(gps_rows)

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
        odo_start_source=odo_start_source,
        odo_end_source=odo_end_source,
        horo_start_source=horo_start_source,
        horo_end_source=horo_end_source,
        source_meta=source_meta,
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
        should_stop: Callable[[], bool] | None = None,
    ) -> ProviderCalculationResult:
        if not targets:
            return ProviderCalculationResult(records=[], binding_updates=[])

        sample_target = targets[0]
        rows: list[MonthlyPerformanceRecord] = []
        binding_updates: list[BindingUpsert] = []

        artimo = ArtimoClient(self._build_config(sample_target))
        current_start, current_end = artimo.get_month_range(year, month_number)
        current_bounds = artimo.get_local_month_bounds(year, month_number)
        # Los viajes se piden con arranque adelantado: Artimo filtra por inicio,
        # así que sin ese margen se perderían los que cruzan la medianoche.
        detail_start, detail_end = artimo.get_trip_lookback_range(year, month_number)
        previous_year = int(previous_month[:4])
        previous_month_number = int(previous_month[-2:])
        previous_start, previous_end = artimo.get_month_range(previous_year, previous_month_number)
        previous_bounds = artimo.get_local_month_bounds(previous_year, previous_month_number)
        previous_detail_start, previous_detail_end = artimo.get_trip_lookback_range(
            previous_year, previous_month_number
        )

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
                    except JobCancelled:
                        raise
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
            check_stop(should_stop)
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
                    gps_page = artimo.get_report_paged(
                        "gps",
                        current_start,
                        current_end,
                        resource_id=provider_vehicle_id,
                    )
                    trip_window = select_trips_in_window(
                        artimo.get_trip_details(
                            detail_start, detail_end, resource_id=provider_vehicle_id
                        ),
                        window_start_local=current_bounds[0],
                        window_end_local=current_bounds[1],
                    )
                    previous_record = previous_records.get((target.customer_database_id, target.plate))
                    if previous_record is None or previous_record.odo_end is None:
                        # Sin cierre guardado hay que reconstruirlo con la misma
                        # regla, no con el agregado (que se pasa del corte).
                        previous_trip = select_trips_in_window(
                            artimo.get_trip_details(
                                previous_detail_start,
                                previous_detail_end,
                                resource_id=provider_vehicle_id,
                            ),
                            window_start_local=previous_bounds[0],
                            window_end_local=previous_bounds[1],
                        ).close_trip or previous_trip
                    rows.append(
                        _calculate_vehicle_record(
                            target=target,
                            month=month,
                            current_trip=trip_window.close_trip,
                            previous_trip=previous_trip,
                            previous_record=previous_record,
                            provider_vehicle_id=provider_vehicle_id,
                            gps_rows=gps_page.rows,
                            trip_window=trip_window,
                            gps_truncated=gps_page.truncated,
                        )
                    )
                except JobCancelled:
                    raise
                except Exception as exc:
                    _logger.exception(
                        "Rendimientos placa=%s provider=%s database_id=%s",
                        target.plate,
                        "artimo",
                        target.customer_database_id,
                    )
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
                    except JobCancelled:
                        raise
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


# .NET TimeSpan tal como lo serializa Geotab: "[d.]hh:mm:ss[.fffffff]".
_TIMESPAN_PATTERN = re.compile(r"^(?:(\d+)\.)?(\d{1,2}):(\d{2}):(\d{2})(?:\.(\d+))?$")


def _td_to_hours(value) -> float:
    """Convert a timedelta, datetime.time, TimeSpan string, or seconds to hours.

    Strings admitidos (D13): "02:03:04", "00:15:30.1230000" (fraccion de
    segundo), "1.02:03:04.5000000" (dias + fraccion). Numeros se interpretan
    como segundos. Lo no reconocible devuelve 0.0.
    """
    if value is None:
        return 0.0
    if isinstance(value, timedelta):
        return value.total_seconds() / 3600.0
    if isinstance(value, dt_time):
        return (value.hour * 3600 + value.minute * 60 + value.second + value.microsecond / 1e6) / 3600.0
    if hasattr(value, "total_seconds"):
        return value.total_seconds() / 3600.0
    if isinstance(value, str):
        match = _TIMESPAN_PATTERN.match(value.strip())
        if match:
            days, hours, minutes, seconds, fraction = match.groups()
            total = int(days or 0) * 86400 + int(hours) * 3600 + int(minutes) * 60 + int(seconds)
            if fraction:
                total += float(f"0.{fraction}")
            return total / 3600.0
    try:
        return float(value) / 3600.0
    except (TypeError, ValueError):
        return 0.0


def _filter_geotab_trips_in_window(
    trips: list[dict],
    from_date: str,
    to_date: str,
) -> tuple[list[dict], int]:
    """D10: deja solo los viajes cuyo `stop` cae en [from, to).

    Geotab devuelve los Trip que se SOLAPAN con el rango, asi que un viaje que
    cruza la medianoche del dia 1 aparece en los dos meses. Cada viaje cuenta
    en el mes donde termina (mismo criterio que Artimo). Los viajes sin `stop`
    legible se conservan. Devuelve (viajes, excluidos).
    """
    if not trips:
        return [], 0
    try:
        window_start = _parse_geotab_datetime(from_date)
        window_end = _parse_geotab_datetime(to_date)
    except (TypeError, ValueError):
        return list(trips), 0
    kept: list[dict] = []
    dropped = 0
    for trip in trips:
        stop = trip.get("stop") if isinstance(trip, dict) else None
        if not isinstance(stop, (str, datetime)):
            kept.append(trip)
            continue
        try:
            stop_at = _parse_geotab_datetime(stop)
        except (TypeError, ValueError):
            kept.append(trip)
            continue
        if window_start <= stop_at < window_end:
            kept.append(trip)
        else:
            dropped += 1
    return kept, dropped


_GEOTAB_FUEL_SOURCES: tuple[tuple[str, str], ...] = (
    ("TotalFuelUsed", "total_fuel"),
    ("DeviceTotalFuel", "device_fuel"),
)


def _geotab_fuel_chain(
    bundle: dict[str, list[dict]],
    *,
    previous_record: MonthlyPerformanceRecord | None,
    warnings: list[str],
    source_meta: dict[str, Any],
) -> tuple[float | None, float | None]:
    """D9: combustible Geotab encadenado como el odometro.

    Devuelve ``(fuel_gallons, fuel_end)`` donde ``fuel_end`` es la ultima lectura
    acumulada (litros) de la fuente usada, para que el mes siguiente arranque de
    ahi. El arranque es ``previous_record.fuel_end`` solo si el mes anterior uso
    la MISMA fuente (``source_meta["fuel_source"]``); si no, la primera lectura
    del mes con warning. Un retroceso del acumulado deja el combustible en None.
    Se prefiere TotalFuelUsed; DeviceTotalFuel es la fuente alternativa.
    """
    prev_source: str | None = None
    prev_end: float | None = None
    if previous_record is not None and previous_record.fuel_end is not None:
        prev_meta = previous_record.source_meta if isinstance(previous_record.source_meta, dict) else {}
        prev_source = prev_meta.get("fuel_source")
        prev_end = previous_record.fuel_end

    fallback: tuple[str, float, str, float, float] | None = None
    for source, bundle_key in _GEOTAB_FUEL_SOURCES:
        readings = bundle.get(bundle_key) or []
        first = _geotab_first_value(readings)
        last = _geotab_last_value(readings)
        if first is None or last is None:
            continue
        if prev_end is not None and prev_source == source:
            start, start_source = prev_end, SOURCE_PREVIOUS
        else:
            start, start_source = first, SOURCE_FIRST_READING
        delta = last - start
        if delta > 0:
            source_meta.update(fuel_source=source, fuel_start_source=start_source, fuel_unit="l")
            if source == "DeviceTotalFuel":
                warnings.append("Combustible calculado usando DiagnosticDeviceTotalFuelId (fuente alternativa).")
            if start_source == SOURCE_FIRST_READING:
                warnings.append("Combustible inicial tomado de la primera lectura del mes.")
            return delta / _LITERS_PER_GALLON, last
        if fallback is None:
            fallback = (source, start, start_source, last, delta)

    if fallback is None:
        warnings.append("No se pudo determinar el consumo de combustible para el mes.")
        return None, None

    source, start, start_source, last, delta = fallback
    source_meta.update(fuel_source=source, fuel_start_source=start_source, fuel_unit="l")
    if delta < 0:
        warnings.append(
            f"Combustible acumulado retrocede ({source}): {_num(start)} → {_num(last)} L "
            f"({_num(delta)} L); combustible no calculado."
        )
    else:
        warnings.append(
            "No se pudo determinar el consumo de combustible para el mes "
            f"(lectura acumulada {source} sin variacion)."
        )
    return None, last


# Diagnosticos que trae cada bundle Geotab. device_fuel va siempre en el mismo
# batch (antes era un roundtrip condicional de fallback).
_GEOTAB_STATUS_DIAGNOSTICS: dict[str, str] = {
    "odometer": _DIAG_ODOMETER,
    "engine_hours": _DIAG_ENGINE_HOURS,
    "total_fuel": _DIAG_TOTAL_FUEL,
    "device_fuel": _DIAG_DEVICE_FUEL,
}
# G5: de los acumulados de combustible solo se usa primera/ultima lectura
# (`_geotab_fuel_chain`); el cliente pide dos ventanas borde en vez del mes.
_GEOTAB_EDGE_ONLY_KEYS: frozenset[str] = frozenset({"total_fuel", "device_fuel"})


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
    bundle: dict[str, list[dict]] | None = None,
) -> MonthlyPerformanceRecord:
    warnings: list[str] = []
    source_meta: dict[str, Any] = {"device_id": device_id}

    if bundle is None:
        bundle = get_month_data_bundle(
            api, device_id, from_date, to_date,
            status_diagnostics=_GEOTAB_STATUS_DIAGNOSTICS,
            edge_only=_GEOTAB_EDGE_ONLY_KEYS,
        )
    odo_readings = bundle["odometer"]
    hours_readings = bundle["engine_hours"]
    trips, trips_dropped = _filter_geotab_trips_in_window(bundle.get("trips") or [], from_date, to_date)
    if trips_dropped:
        source_meta["trips_dropped_boundary"] = trips_dropped
        warnings.append(
            f"{trips_dropped} viaje(s) que terminan fuera del mes fueron excluidos "
            "(cada viaje cuenta en el mes donde termina)."
        )

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
    odo_end_source = SOURCE_LAST_READING if odo_end is not None else None

    odo_start_source: str | None = None
    if previous_record and previous_record.odo_end is not None:
        odo_start = previous_record.odo_end
        odo_start_source = SOURCE_PREVIOUS
    else:
        odo_start_raw = _geotab_first_value(odo_readings)
        odo_start = odo_start_raw / 1000.0 if odo_start_raw is not None else None
        if odo_start is not None:
            odo_start_source = SOURCE_FIRST_READING
            warnings.append(
                "Odometro inicial tomado de la lectura en el tanqueo anterior (punto exacto del corte)."
                if cutoff_mode
                else "Odometro inicial tomado de la primera lectura del mes (sin registro previo)."
            )

    kms_ecm = _positive_delta(odo_start, odo_end, label="Odómetro", unit="km", warnings=warnings)
    odo_regressed = kms_ecm is None and odo_start is not None and odo_end is not None

    # Engine hours (seconds → hours)
    horo_end_raw = _geotab_last_value(hours_readings)
    horo_end = horo_end_raw / 3600.0 if horo_end_raw is not None else None
    horo_end_source = SOURCE_LAST_READING if horo_end is not None else None

    horo_start_source: str | None = None
    if previous_record and previous_record.horo_end is not None:
        horo_start = previous_record.horo_end
        horo_start_source = SOURCE_PREVIOUS
    else:
        horo_start_raw = _geotab_first_value(hours_readings)
        horo_start = horo_start_raw / 3600.0 if horo_start_raw is not None else None
        if horo_start is not None:
            horo_start_source = SOURCE_FIRST_READING
            warnings.append(
                "Horometro inicial tomado de la lectura en el tanqueo anterior (punto exacto del corte)."
                if cutoff_mode
                else "Horometro inicial tomado de la primera lectura del mes (sin registro previo)."
            )

    hours_ecm = _positive_delta(horo_start, horo_end, label="Horómetro", unit="h", warnings=warnings)
    horo_regressed = hours_ecm is None and horo_start is not None and horo_end is not None

    # Fuel (liters → gallons), encadenado con el cierre previo; fallback DeviceTotalFuel.
    fuel_gallons, fuel_end = _geotab_fuel_chain(
        bundle,
        previous_record=previous_record,
        warnings=warnings,
        source_meta=source_meta,
    )

    if odo_readings:
        source_meta["odo_first_at"] = str(odo_readings[0].get("dateTime") or "")
        source_meta["odo_last_at"] = str(odo_readings[-1].get("dateTime") or "")
    if hours_readings:
        source_meta["horo_first_at"] = str(hours_readings[0].get("dateTime") or "")
        source_meta["horo_last_at"] = str(hours_readings[-1].get("dateTime") or "")

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
        source_meta["trips_count"] = len(trips)

    all_present = all(v is not None for v in (odo_start, odo_end, horo_start, horo_end, fuel_gallons, kms_gps, hours_gps))
    status = "calculated" if all_present else "partial"
    if odo_regressed or horo_regressed:
        status = _cap_partial(status)

    # Retrocesos acumulados significativos: el registro no es confiable aunque
    # el neto inicio→fin haya salido positivo.
    if kms_ecm is not None and kms_ecm > 0 and odometer_regressions.total > _GEOTAB_REGRESSION_SIGNIFICANT_RATIO * kms_ecm:
        status = _cap_partial(status)
        warnings.append(
            f"Retrocesos de odómetro acumulan {_num(odometer_regressions.total)} km "
            f"(> {_GEOTAB_REGRESSION_SIGNIFICANT_RATIO:.0%} de {_num(kms_ecm)} km); registro marcado parcial."
        )
    if hours_ecm is not None and hours_ecm > 0 and hourmeter_regressions.total > _GEOTAB_REGRESSION_SIGNIFICANT_RATIO * hours_ecm:
        status = _cap_partial(status)
        warnings.append(
            f"Retrocesos de horómetro acumulan {_num(hourmeter_regressions.total)} h "
            f"(> {_GEOTAB_REGRESSION_SIGNIFICANT_RATIO:.0%} de {_num(hours_ecm)} h); registro marcado parcial."
        )

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
        odo_start_source=odo_start_source,
        odo_end_source=odo_end_source,
        horo_start_source=horo_start_source,
        horo_end_source=horo_end_source,
        fuel_end=fuel_end,
        source_meta=source_meta,
    )


def _geotab_env_int(name: str, default: int, *, minimum: int = 1) -> int:
    raw = os.getenv(name)
    if raw is None or not str(raw).strip():
        return default
    try:
        return max(minimum, int(str(raw).strip()))
    except ValueError:
        _logger.warning("Valor invalido para %s=%r; usando %s", name, raw, default)
        return default


@dataclass
class _GeotabPendingTarget:
    index: int
    target: PerformanceTarget
    device_id: str
    duplicate_warning: str | None


@dataclass
class _GeotabOutcome:
    index: int
    record: MonthlyPerformanceRecord


class _GeotabCircuitBreaker:
    """R6: corta las llamadas a Geotab de una database tras N fallos consecutivos.

    Cuentan los fallos clasificados ``transient`` o ``auth`` (por chunk o por
    placa); un exito reinicia el contador. Thread-safe: lo comparten los workers.
    """

    def __init__(self, threshold: int, *, database: str) -> None:
        self.threshold = max(1, threshold)
        self.database = database
        self._lock = threading.Lock()
        self.consecutive = 0
        self.last_error: str | None = None
        self.tripped = False

    def is_open(self) -> bool:
        with self._lock:
            return self.tripped

    def record_success(self) -> None:
        with self._lock:
            self.consecutive = 0

    def record_failure(self, exc: BaseException) -> None:
        kind = _classify_geotab_error(exc) if isinstance(exc, Exception) else "fatal"
        if kind not in {"transient", "auth"}:
            return
        with self._lock:
            self.consecutive += 1
            self.last_error = f"{type(exc).__name__}: {exc}"
            if self.tripped or self.consecutive < self.threshold:
                return
            self.tripped = True
        _logger.error(
            "Geotab circuit breaker abierto para db=%s tras %d fallos consecutivos: %s",
            self.database,
            self.consecutive,
            self.last_error,
        )

    def warning(self) -> str:
        with self._lock:
            return (
                f"Geotab no disponible (circuit breaker tras {self.consecutive} fallos consecutivos): "
                f"{self.last_error or 'sin detalle'}"
            )


class GeotabMonthlyPerformanceProvider:
    key = "geotab"

    # ------------------------------------------------------------------ chunk
    @staticmethod
    def _process_chunk(
        *,
        api,
        month: str,
        from_date: str,
        to_date: str,
        chunk: list[_GeotabPendingTarget],
        previous_records: dict[tuple[int, str], MonthlyPerformanceRecord],
        breaker: _GeotabCircuitBreaker,
    ) -> list[_GeotabOutcome]:
        """Worker: trae los bundles del chunk en un multi_call (G1) y calcula cada placa.

        Nunca aborta el grupo: cada excepcion por placa se convierte en fila
        ``error``. `JobCancelled` si se propaga (cancelacion cooperativa).
        """
        outcomes: list[_GeotabOutcome] = []

        def _error_row(item: _GeotabPendingTarget, message: str) -> _GeotabOutcome:
            return _GeotabOutcome(
                index=item.index,
                record=_build_status_record(
                    target=item.target,
                    month=month,
                    status="error",
                    provider_vehicle_id=item.device_id,
                    warnings=[message],
                ),
            )

        if breaker.is_open():
            return [_error_row(item, breaker.warning()) for item in chunk]

        bundles: dict[str, dict[str, list[dict]]] = {}
        fetch_error: Exception | None = None
        try:
            bundles = get_month_data_bundles(
                api,
                [item.device_id for item in chunk],
                from_date=from_date,
                to_date=to_date,
                status_diagnostics=_GEOTAB_STATUS_DIAGNOSTICS,
                edge_only=_GEOTAB_EDGE_ONLY_KEYS,
            )
        except JobCancelled:
            raise
        except Exception as exc:
            fetch_error = exc
            breaker.record_failure(exc)
            if _classify_geotab_error(exc) != "fatal":
                _logger.exception(
                    "Rendimientos Geotab: fallo el multi_call del chunk (%d placas, database_id=%s)",
                    len(chunk),
                    chunk[0].target.customer_database_id if chunk else None,
                )
                return [_error_row(item, f"Error calculando la placa en Geotab: {exc}") for item in chunk]
            # Fatal (shape inesperado, parametros...): el calculo por placa vuelve a
            # pedir su bundle individual y reporta el error real de cada una.
            _logger.warning(
                "Rendimientos Geotab: multi_call del chunk fallo con error fatal (%s); calculo por placa.",
                exc,
            )

        for item in chunk:
            target = item.target
            try:
                record = _calculate_geotab_vehicle_record(
                    target=target,
                    month=month,
                    device_id=item.device_id,
                    api=api,
                    from_date=from_date,
                    to_date=to_date,
                    previous_record=previous_records.get((target.customer_database_id, target.plate)),
                    bundle=bundles.get(item.device_id) if fetch_error is None else None,
                )
                if item.duplicate_warning:
                    record.warnings = [item.duplicate_warning, *record.warnings]
                breaker.record_success()
                outcomes.append(_GeotabOutcome(index=item.index, record=record))
            except JobCancelled:
                raise
            except Exception as exc:
                breaker.record_failure(exc)
                _logger.exception(
                    "Rendimientos placa=%s provider=%s database_id=%s",
                    target.plate,
                    "geotab",
                    target.customer_database_id,
                )
                outcomes.append(_error_row(item, f"Error calculando la placa en Geotab: {exc}"))
        return outcomes

    # --------------------------------------------------------------- database
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
        should_stop: Callable[[], bool] | None = None,
    ) -> ProviderCalculationResult:
        if not targets:
            return ProviderCalculationResult(records=[], binding_updates=[])

        sample = targets[0]
        if not sample.username or not sample.password:
            raise ValueError(
                f"La database {sample.database_name or sample.customer_database_id} no tiene credenciales Geotab completas."
            )
        database_name = sample.database_name or ""

        auth_start = time.perf_counter()
        api = get_authenticated_client(sample.username, sample.password, database_name)
        auth_s = time.perf_counter() - auth_start
        from_date, to_date = get_geotab_month_range(year, month_number)

        records_by_index: dict[int, MonthlyPerformanceRecord] = {}
        binding_updates: list[BindingUpsert] = []
        resolved_via_binding = 0
        api_resolved_count = 0

        def _notify_done() -> None:
            if on_target_done is None:
                return
            try:
                on_target_done()
            except JobCancelled:
                raise
            except Exception:
                pass

        # Fase 1 (hilo principal): inventario UNA vez por database + indice de
        # placas (G8); resolver todos los devices antes de tocar datos.
        resolve_start = time.perf_counter()
        devices = get_cached_devices(sample.username, sample.password, database_name)
        plate_index = build_plate_index(devices)

        pending: list[_GeotabPendingTarget] = []
        for index, target in enumerate(targets):
            check_stop(should_stop)
            duplicate_warning: str | None = None
            bound_id, is_manual = _select_binding(bindings=bindings, target=target)
            if is_manual:
                device_id = bound_id
                resolved_via_binding += 1
            else:
                plate_prefix = target.provider_config.get("plate_prefix")
                matches = lookup_plate_index(plate_index, plate=target.plate, plate_prefix=plate_prefix)
                # Geotab conserva los devices archivados (cada reemplazo de
                # equipo crea uno nuevo y archiva el viejo, misma placa). El
                # desempate prefiere el device activo y, ante empate, el que
                # ya venia usandose (bound_id) para no saltar a un duplicado.
                device = _find_device_in_collection(
                    matches,
                    plate=target.plate,
                    plate_prefix=plate_prefix,
                    preferred_id=bound_id,
                )
                resolved_id = str(device.get("id") or "").strip() if device else None
                device_id = resolved_id or bound_id
                if device is not None:
                    api_resolved_count += 1
                # Solo avisamos si queda ambiguedad REAL: 2+ devices activos
                # con la misma placa. Un activo + archivados se resuelve solo.
                active_matches = [d for d in matches if _device_is_active(d)]
                if len(active_matches) > 1:
                    ids = ", ".join(sorted(str(d.get("id") or "").strip() for d in active_matches))
                    duplicate_warning = (
                        f"Geotab tiene {len(active_matches)} dispositivos activos con la placa "
                        f"{target.plate} ({ids}); se uso {device_id}. Fija el binding manual si es el equivocado."
                    )
                    _logger.warning(
                        "Geotab placa con multiples activos %s [%s]: devices=%s usado=%s",
                        target.plate,
                        database_name,
                        ids,
                        device_id,
                    )

            if not device_id:
                binding_updates.append(
                    BindingUpsert(
                        target=target,
                        provider_vehicle_id=None,
                        binding_status="unbound",
                        last_error="No fue posible resolver el dispositivo en Geotab para esta placa.",
                    )
                )
                records_by_index[index] = _build_status_record(
                    target=target,
                    month=month,
                    status="unbound",
                    warnings=["No fue posible resolver el dispositivo en Geotab para esta placa."],
                )
                _notify_done()
                continue

            binding_updates.append(
                BindingUpsert(
                    target=target,
                    provider_vehicle_id=device_id,
                    binding_status="resolved",
                    last_error=None,
                )
            )
            pending.append(
                _GeotabPendingTarget(
                    index=index, target=target, device_id=device_id, duplicate_warning=duplicate_warning
                )
            )
        resolve_s = time.perf_counter() - resolve_start

        # Fase 2: chunks de devices en paralelo acotado (G2). Los workers solo
        # hablan con Geotab y calculan; on_target_done / check_stop viven en el
        # hilo principal (progreso y DB no son thread-safe).
        max_workers = _geotab_env_int("GEOTAB_MAX_WORKERS", 3)
        breaker = _GeotabCircuitBreaker(
            _geotab_env_int("GEOTAB_BREAKER_THRESHOLD", 3), database=database_name
        )
        # Mismo tamano que usara el cliente: un chunk de targets == un multi_call.
        chunk_size = bundle_chunk_size_for(
            from_date=from_date,
            to_date=to_date,
            status_diagnostics=_GEOTAB_STATUS_DIAGNOSTICS,
            edge_only=_GEOTAB_EDGE_ONLY_KEYS,
        )
        chunks = [pending[i : i + chunk_size] for i in range(0, len(pending), chunk_size)]

        fetch_start = time.perf_counter()
        if chunks:
            executor = ThreadPoolExecutor(
                max_workers=max(1, min(max_workers, len(chunks))),
                thread_name_prefix=f"geotab-{database_name or 'db'}",
            )
            try:
                futures = [
                    executor.submit(
                        self._process_chunk,
                        api=api,
                        month=month,
                        from_date=from_date,
                        to_date=to_date,
                        chunk=chunk,
                        previous_records=previous_records,
                        breaker=breaker,
                    )
                    for chunk in chunks
                ]
                # wait() con timeout en vez de as_completed(): mientras un chunk
                # lento sigue en vuelo, el hilo principal despierta cada 30 s y
                # llama check_stop -> heartbeat del job (evita que el reaper de
                # 15 min lo de por muerto) y cancelacion oportuna.
                pending = set(futures)
                while pending:
                    done, pending = wait(
                        pending, timeout=_GEOTAB_WAIT_HEARTBEAT_SECONDS, return_when=FIRST_COMPLETED
                    )
                    check_stop(should_stop)
                    for future in done:
                        outcomes = future.result()  # re-lanza JobCancelled del worker
                        for outcome in outcomes:
                            check_stop(should_stop)
                            records_by_index[outcome.index] = outcome.record
                            _notify_done()
            except JobCancelled:
                executor.shutdown(wait=False, cancel_futures=True)
                raise
            except BaseException:
                executor.shutdown(wait=False, cancel_futures=True)
                raise
            else:
                executor.shutdown(wait=True)
        fetch_s = time.perf_counter() - fetch_start

        rows = [records_by_index[index] for index in sorted(records_by_index)]
        _logger.info(
            "Geotab %s [%s]: %d placas | auth=%.1fs resolve_devices=%.1fs (%d por API, %d por binding) "
            "fetch_datos=%.1fs (workers=%d chunks=%d chunk_size=%d breaker=%s)",
            month,
            database_name,
            len(targets),
            auth_s,
            resolve_s,
            api_resolved_count,
            resolved_via_binding,
            fetch_s,
            max(1, min(max_workers, len(chunks))) if chunks else 0,
            len(chunks),
            chunk_size,
            "abierto" if breaker.is_open() else "cerrado",
        )
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
    chronometer_lookup: Callable[[], float | None] | None = None,
    now_utc: datetime | None = None,
) -> MonthlyPerformanceRecord:
    warnings: list[str] = []
    source_meta: dict[str, Any] = {"vehicle_id": vehicle_id}
    odo_start_source: str | None = None
    odo_end_source: str | None = None
    horo_start_source: str | None = None
    horo_end_source: str | None = None

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
        odo_start_source = SOURCE_PREVIOUS
    elif trip_odos.odo_start is not None:
        odo_start = trip_odos.odo_start
        odo_start_source = SOURCE_ESTIMATED if trip_odos.reconstructed_start else SOURCE_TRIPS
    else:
        odo_start = None

    odo_end = trip_odos.odo_end
    if odo_end is not None:
        odo_end_source = SOURCE_TRIPS

    def _reconstruct_odo_end(reason: str) -> None:
        """odo_end = odo_start + mileageCanKms. Un resumen negativo no se recorta a 0: se omite."""
        nonlocal odo_end, odo_end_source
        if odo_end is not None or odo_start is None or summary_can_kms is None:
            return
        if summary_can_kms < 0:
            warnings.append(
                f"mileageCanKms de Frotcom es negativo ({_num(summary_can_kms)} km); "
                "no se reconstruye el odometro final."
            )
            return
        odo_end = odo_start + summary_can_kms
        odo_end_source = SOURCE_ESTIMATED
        warnings.append(f"Odometro final reconstruido con mileageCanKms de Frotcom ({reason}).")

    _reconstruct_odo_end("viajes sin odometro")

    # Frotcom expone el horometro acumulado CAN como engineHours en
    # vehicleCanInfo. El resumen mensual solo trae tiempo de conduccion GPS,
    # por lo que no se debe usar como sustituto de horas ECM.
    horo_start = previous_record.horo_end if previous_record and previous_record.horo_end is not None else None
    if horo_start is not None:
        horo_start_source = SOURCE_PREVIOUS
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
            odo_start_source = SOURCE_CAN
            warnings.append("Odometro inicial tomado de la primera lectura CAN (viajes sin odometro).")
    if odo_end is None and last_reading is not None:
        odo_end = last_reading.odometer
        if odo_end is not None:
            odo_end_source = SOURCE_CAN
            warnings.append("Odometro final tomado de la ultima lectura CAN (viajes sin odometro).")

    if horo_start is None and first_reading is not None:
        horo_start = first_reading.engine_hours
        if horo_start is not None:
            horo_start_source = SOURCE_CAN
            warnings.append("Horometro inicial tomado de la primera lectura CAN de Frotcom.")
    if last_reading is not None:
        horo_end = last_reading.engine_hours
        if horo_end is not None:
            horo_end_source = SOURCE_CAN
            warnings.append("Horometro final tomado de la ultima lectura CAN de Frotcom.")

    _reconstruct_odo_end("CAN no disponible")

    if not summary and trip_odos.odo_start is None and trip_odos.odo_end is None and first_reading is None and last_reading is None:
        return _build_status_record(
            target=target,
            month=month,
            status="no_data",
            warnings=[*warnings, "No se encontraron datos de Frotcom para el mes solicitado."],
            provider_vehicle_id=vehicle_id,
        )

    odo_regressed = False
    if odo_start is not None and odo_end is not None:
        kms_ecm = _positive_delta(odo_start, odo_end, label="Odómetro", unit="km", warnings=warnings)
        odo_regressed = kms_ecm is None
    else:
        kms_ecm = summary_can_kms
        if kms_ecm is not None:
            source_meta["kms_ecm_source"] = "summary_mileageCanKms"

    kms_gps_raw = summary.get("mileageGpsKms") if isinstance(summary, dict) else None
    try:
        kms_gps = float(kms_gps_raw) if kms_gps_raw is not None else None
    except (TypeError, ValueError):
        kms_gps = None

    # Fallback de horometro: reconstruccion desde el chronometer del vehiculo
    # (/v2/vehicles, en horas) restando las horas de motor de los viajes
    # posteriores al mes. Solo aplica cuando vehicleCanInfo no dio horometro;
    # es un estimado y sus warnings lo dejan explicito.
    if horo_end is None and chronometer_lookup is not None:
        chronometer = None
        try:
            chronometer = chronometer_lookup()
        except FrotcomAuthError:
            raise
        except Exception as exc:
            warnings.append(f"No fue posible leer el chronometer del vehiculo en Frotcom: {exc}")
        if chronometer is not None:
            reference_now = now_utc or datetime.now(timezone.utc)
            try:
                estimated_end, chrono_warnings = estimate_hourmeter_end_from_chronometer(
                    config, vehicle_id, chronometer, range_end_utc, reference_now
                )
            except FrotcomAuthError:
                raise
            except Exception as exc:
                estimated_end, chrono_warnings = None, [
                    f"No fue posible estimar el horometro con el chronometer de Frotcom: {exc}"
                ]
            warnings.extend(chrono_warnings)
            if estimated_end is not None:
                horo_end = estimated_end
                horo_end_source = SOURCE_ESTIMATED
                warnings.append(
                    "Horometro final estimado: chronometer actual del vehiculo menos horas de viajes posteriores al mes."
                )
                if horo_start is None:
                    if trip_odos.engine_hours is not None:
                        horo_start = horo_end - trip_odos.engine_hours
                        horo_start_source = SOURCE_ESTIMATED
                        warnings.append(
                            "Horometro inicial estimado restando las horas de motor de los viajes del mes."
                        )
                    elif trip_odos.trip_count == 0:
                        horo_start = horo_end
                        horo_start_source = SOURCE_ESTIMATED
                        warnings.append("Sin viajes en el mes: horometro inicial igual al final.")

    hours_gps = hours_from_seconds(summary.get("drivingTimeSeconds") if isinstance(summary, dict) else None)
    horo_regressed = False
    if horo_start is not None and horo_end is not None:
        hours_ecm = _positive_delta(horo_start, horo_end, label="Horómetro", unit="h", warnings=warnings)
        horo_regressed = hours_ecm is None
    else:
        hours_ecm = None
    if hours_ecm is None and not horo_regressed and trip_odos.engine_hours is not None:
        hours_ecm = trip_odos.engine_hours
        source_meta["hours_ecm_source"] = "trips_engine_hours"
        warnings.append(
            "Horas de motor del mes tomadas de los viajes de Frotcom (conduccion + ralenti)."
        )
    if hours_ecm is None and not horo_regressed:
        warnings.append("No se pudo determinar el horometro CAN de Frotcom para el mes.")

    fuel_start = first_reading.total_fuel_used if first_reading else None
    fuel_end = last_reading.total_fuel_used if last_reading else None
    fuel_gallons: float | None
    if summary_fuel_liters is not None:
        if summary_fuel_liters < 0:
            fuel_gallons = None
            warnings.append(
                f"totalFuelUsed de Frotcom es negativo ({_num(summary_fuel_liters)} L); combustible no calculado."
            )
        else:
            fuel_gallons = liters_to_gallons(summary_fuel_liters)
            source_meta["fuel_source"] = "summary_totalFuelUsed"
    elif fuel_start is not None and fuel_end is not None and fuel_end >= fuel_start:
        fuel_gallons = liters_to_gallons(fuel_end - fuel_start)
        source_meta["fuel_source"] = "can_totalFuelUsed"
    elif fuel_start is not None and fuel_end is not None:
        fuel_gallons = None
        warnings.append(
            f"Combustible CAN acumulado retrocede: {_num(fuel_start)} → {_num(fuel_end)} L "
            f"({_num(fuel_end - fuel_start)} L); combustible no calculado."
        )
    else:
        fuel_gallons = None
        warnings.append("No se pudo determinar el consumo de combustible en Frotcom para el mes.")
    if fuel_end is not None:
        source_meta["fuel_unit"] = "l"

    all_present = all(
        value is not None
        for value in (odo_start, odo_end, horo_start, horo_end, fuel_gallons, kms_gps, hours_gps)
    )
    status = "calculated" if all_present else "partial"
    if odo_regressed or horo_regressed:
        status = _cap_partial(status)

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
        odo_start_source=odo_start_source,
        odo_end_source=odo_end_source,
        horo_start_source=horo_start_source,
        horo_end_source=horo_end_source,
        fuel_end=fuel_end,
        source_meta=source_meta,
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
        should_stop: Callable[[], bool] | None = None,
    ) -> ProviderCalculationResult:
        if not targets:
            return ProviderCalculationResult(records=[], binding_updates=[])

        df_iso, dt_iso, month_start, month_end = get_frotcom_month_range(year, month_number)
        range_start_utc, range_end_utc = get_frotcom_month_range_utc_bounds(year, month_number)
        now_utc = datetime.now(timezone.utc)

        rows: list[MonthlyPerformanceRecord] = []
        binding_updates: list[BindingUpsert] = []
        prepared_targets: list[tuple[PerformanceTarget, FrotcomConfig, str, Callable[[], float | None]]] = []

        vehicles_cache: dict[tuple[str, str, str], list[dict]] = {}
        vehicles_list_failures: dict[tuple[str, str, str], str] = {}

        # Chronometer por vehiculo, resuelto de forma perezosa: solo se consulta
        # /v2/vehicles cuando el calculo necesita el fallback de horometro y la
        # lista no se descargo ya para resolver placas. Cache propio para no
        # contaminar vehicles_list_failures (que bloquea la resolucion de placas).
        chronometer_maps: dict[tuple[str, str, str], dict[str, float]] = {}
        chronometer_lock = threading.Lock()

        def _make_chronometer_lookup(
            config: FrotcomConfig, vehicle_id: str
        ) -> Callable[[], float | None]:
            def _lookup() -> float | None:
                key = config.cache_key()
                with chronometer_lock:
                    mapping = chronometer_maps.get(key)
                    if mapping is None:
                        vehicles = vehicles_cache.get(key)
                        if vehicles is None:
                            try:
                                vehicles = list_frotcom_vehicles(config)
                                vehicles_cache[key] = vehicles
                            except Exception:
                                vehicles = []
                        mapping = build_frotcom_chronometer_map(vehicles)
                        chronometer_maps[key] = mapping
                return mapping.get(str(vehicle_id).strip())

            return _lookup

        def _notify_target_done():
            if on_target_done is not None:
                try:
                    on_target_done()
                except JobCancelled:
                    raise
                except Exception:
                    pass

        for target in targets:
            check_stop(should_stop)
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

            prepared_targets.append(
                (target, config, vehicle_id, _make_chronometer_lookup(config, vehicle_id))
            )

        try:
            configured_workers = int(os.getenv("FROTCOM_MAX_WORKERS", "4"))
        except ValueError:
            configured_workers = 4
        max_workers = max(1, min(configured_workers, 8, len(prepared_targets) or 1))

        def _calculate_prepared(
            item: tuple[PerformanceTarget, FrotcomConfig, str, Callable[[], float | None]],
        ) -> MonthlyPerformanceRecord:
            target, config, vehicle_id, chronometer_lookup = item
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
                chronometer_lookup=chronometer_lookup,
                now_utc=now_utc,
            )

        # Ejecutamos una placa de cada juego de credenciales como preflight. Si
        # la autenticacion falla, evitamos disparar el resto del grupo. Las
        # placas restantes si se procesan concurrentemente.
        prepared_by_credentials: dict[
            tuple[str, str, str],
            list[tuple[PerformanceTarget, FrotcomConfig, str, Callable[[], float | None]]],
        ] = {}
        for item in prepared_targets:
            prepared_by_credentials.setdefault(item[1].cache_key(), []).append(item)

        parallel_targets: list[
            tuple[PerformanceTarget, FrotcomConfig, str, Callable[[], float | None]]
        ] = []
        for credential_targets in prepared_by_credentials.values():
            check_stop(should_stop)
            first_item, *remaining_items = credential_targets
            first_target, _first_config, first_vehicle_id, _first_lookup = first_item
            try:
                rows.append(_calculate_prepared(first_item))
                parallel_targets.extend(remaining_items)
                _notify_target_done()
            except JobCancelled:
                raise
            except FrotcomAuthError as exc:
                for failed_target, _failed_config, failed_vehicle_id, _failed_lookup in credential_targets:
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
                _logger.exception(
                    "Rendimientos placa=%s provider=%s database_id=%s",
                    first_target.plate,
                    "frotcom",
                    first_target.customer_database_id,
                )
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

        check_stop(should_stop)
        with ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="frotcom") as executor:
            futures = {
                executor.submit(_calculate_prepared, item): item
                for item in parallel_targets
            }
            try:
                for future in as_completed(futures):
                    # Cancelacion cooperativa entre placas ya resueltas: los
                    # futures pendientes se descartan, los en vuelo terminan.
                    check_stop(should_stop)
                    target, config, vehicle_id, _lookup = futures[future]
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
                    except JobCancelled:
                        raise
                    except Exception as exc:
                        _logger.exception(
                            "Rendimientos placa=%s provider=%s database_id=%s",
                            target.plate,
                            "frotcom",
                            target.customer_database_id,
                        )
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
            except JobCancelled:
                executor.shutdown(wait=False, cancel_futures=True)
                raise

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


# Galon US exacto; la constante Geotab `_LITERS_PER_GALLON` se mantiene por compatibilidad.
_LITERS_PER_US_GALLON = 3.785411784
_LOGITRACS_FUEL_UNITS = ("gal", "l")


def _resolve_logitracs_fuel_unit(target: PerformanceTarget) -> str | None:
    """Unidad del campo 'Combustible' de LogiTracs (D4).

    `provider_config["logitracs_fuel_unit"]` manda; si no esta, la variable de
    entorno `LOGITRACS_FUEL_UNIT`. Solo se aceptan "gal" o "l"; cualquier otra
    cosa se trata como desconocida y el combustible se omite.
    """
    provider_config = target.provider_config if isinstance(target.provider_config, dict) else {}
    raw = provider_config.get("logitracs_fuel_unit")
    if raw is None or not str(raw).strip():
        raw = os.getenv("LOGITRACS_FUEL_UNIT", "")
    unit = str(raw or "").strip().lower()
    return unit if unit in _LOGITRACS_FUEL_UNITS else None


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
    source_meta: dict[str, Any] = {}
    odo_start_source: str | None = None
    odo_end_source: str | None = None

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
    if odo_end is not None:
        odo_end_source = SOURCE_LAST_READING

    if previous_record and previous_record.odo_end is not None:
        odo_start = previous_record.odo_end
        odo_start_source = SOURCE_PREVIOUS
    elif previous_row is not None:
        odo_start = extract_odometer_end(previous_row)
        if odo_start is not None:
            odo_start_source = SOURCE_PREVIOUS
            source_meta["odo_start_from"] = "previous_month_report"
            warnings.append("Odometro inicial tomado del cierre LogiTracs del mes anterior.")
    elif odo_end is not None:
        if kms_period is not None:
            estimated = odo_end - kms_period
            if estimated < 0:
                odo_start = None
                warnings.append(
                    "Odometro inicial no estimable: el kilometraje del periodo "
                    f"({_num(kms_period)} km) supera el odometro final ({_num(odo_end)} km)."
                )
            else:
                odo_start = estimated
                odo_start_source = SOURCE_ESTIMATED
                warnings.append("Odometro inicial estimado a partir del kilometraje del mes actual.")
        else:
            odo_start = None
    else:
        odo_start = None

    if odo_end is None and odo_start is not None and kms_period is not None:
        odo_end = odo_start + kms_period
        odo_end_source = SOURCE_ESTIMATED
        warnings.append("Odometro final estimado a partir del cierre previo mas el kilometraje del periodo.")

    odo_regressed = False
    if odo_start is not None and odo_end is not None:
        kms_ecm = _positive_delta(odo_start, odo_end, label="Odómetro", unit="km", warnings=warnings)
        odo_regressed = kms_ecm is None
    else:
        kms_ecm = kms_period
        if kms_ecm is not None:
            source_meta["kms_ecm_source"] = "report_kilometraje"
            warnings.append("Kilometraje mensual tomado del reporte LogiTracs al no poder derivar ambos odometros.")

    hours_gps = extract_triton_engine_hours(current_row)

    # D4: la unidad de 'Combustible' no esta confirmada por LogiTracs. Sin
    # configuracion explicita se guarda el crudo y se omite el galonaje.
    fuel_raw = extract_triton_fuel_liters(current_row)
    fuel_gallons: float | None = None
    fuel_unit = _resolve_logitracs_fuel_unit(target)
    if fuel_raw is not None:
        source_meta["fuel_raw"] = fuel_raw
        source_meta["fuel_source"] = "report_combustible"
        if fuel_unit == "gal":
            fuel_gallons = fuel_raw
            source_meta["fuel_unit"] = "gal"
        elif fuel_unit == "l":
            fuel_gallons = fuel_raw / _LITERS_PER_US_GALLON
            source_meta["fuel_unit"] = "l"
        else:
            source_meta["fuel_unit"] = "desconocida"
            warnings.append(
                "Combustible LogiTracs omitido: unidad no confirmada (valor crudo en source_meta)."
            )

    all_present = all(v is not None for v in (odo_start, odo_end, kms_ecm, hours_gps, fuel_gallons))
    status = "calculated" if all_present else "partial"
    if odo_regressed:
        status = _cap_partial(status)

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
        odo_start_source=odo_start_source,
        odo_end_source=odo_end_source,
        source_meta=source_meta,
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
        should_stop: Callable[[], bool] | None = None,
    ) -> ProviderCalculationResult:
        if not targets:
            return ProviderCalculationResult(records=[], binding_updates=[])

        def _notify_target_done():
            if on_target_done is not None:
                try:
                    on_target_done()
                except JobCancelled:
                    raise
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
            _logger.exception("Error fetching LogiTracs Triton report")
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
            check_stop(should_stop)
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
            except JobCancelled:
                raise
            except Exception as exc:
                # Antes usaba `logger` (no definido en este modulo -> NameError).
                _logger.exception(
                    "Rendimientos placa=%s provider=%s database_id=%s",
                    target.plate,
                    "logitracs_triton",
                    target.customer_database_id,
                )
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
