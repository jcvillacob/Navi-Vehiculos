from __future__ import annotations

from typing import Protocol

from app.clients.artimo_client import (
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
from app.services.legacy_provider_bootstrap import get_legacy_provider_vehicle_id
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

        for target in targets:
            key = (target.provider_key, target.customer_database_id, target.plate)
            current_trip = current_trips.get(target.plate)
            previous_trip = previous_trips.get(target.plate)
            existing_binding = bindings.get(key)
            legacy_bindings = (
                target.provider_config.get("legacy_bindings")
                if isinstance(target.provider_config.get("legacy_bindings"), dict)
                else {}
            )
            legacy_provider_vehicle_id = str(legacy_bindings.get(target.plate) or "").strip() or None
            provider_vehicle_id = (
                (existing_binding.provider_vehicle_id if existing_binding else None)
                or legacy_provider_vehicle_id
                or get_legacy_provider_vehicle_id(target)
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

        return ProviderCalculationResult(records=rows, binding_updates=binding_updates)


_MONTHLY_PERFORMANCE_PROVIDERS: dict[str, MonthlyPerformanceProvider] = {
    "artimo": ArtimoMonthlyPerformanceProvider(),
}


def get_monthly_performance_provider(provider_key: str) -> MonthlyPerformanceProvider | None:
    return _MONTHLY_PERFORMANCE_PROVIDERS.get(provider_key)
