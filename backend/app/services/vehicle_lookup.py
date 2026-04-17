from __future__ import annotations

import logging
import re

from app.clients.geotab_client import (
    extract_vin as extract_geotab_vin,
    get_device_from_plate,
    get_device_from_vin,
)
from app.clients.quickserve_client import (
    extract_cpl,
    extract_technical_engine_configuration,
    get_engine_dataplate,
)
from app.clients.sql_client import get_vehicle_by_plate, get_vehicle_by_vin
from app.core.config import load_geotab_config, load_quickserve_config, load_sql_config
from app.schemas.vehicle import VehicleLookupResponse
from app.services.motor_catalog import (
    find_assignment_by_engine_number,
    find_registered_motor,
    get_cached_vehicle_lookup,
    get_vehicle_database_assignment,
    get_vehicle_geotab_customer_status,
    register_vehicle_assignment,
    update_vehicle_metadata,
)

_VIN_PATTERN = re.compile(r"^[A-HJ-NPR-Z0-9]{17}$")
_logger = logging.getLogger(__name__)


def _is_vin(value: str) -> bool:
    return bool(_VIN_PATTERN.fullmatch(value.strip().upper()))


def _normalize_fenix_details(row: dict | None) -> dict[str, str | None]:
    if not row:
        return {}
    return {
        "vin": str(row.get("VIN")).strip().upper() if row.get("VIN") else None,
        "plate": str(row.get("plate")).strip().upper() if row.get("plate") else None,
        "engine_number": str(row.get("numero_motor")).strip() if row.get("numero_motor") else None,
        "marca": str(row.get("Marca")).strip() if row.get("Marca") else None,
        "linea": str(row.get("Linea")).strip() if row.get("Linea") else None,
        "modelo": str(row.get("Modelo")).strip() if row.get("Modelo") else None,
        "configuracion": str(row.get("Configuracion")).strip() if row.get("Configuracion") else None,
        "ano_modelo": str(row.get("AñoModelo")).strip() if row.get("AñoModelo") else None,
        "tipo_combustible": str(row.get("Tipo de Combustible")).strip() if row.get("Tipo de Combustible") else None,
    }


def _not_found_response(
    lookup_value: str,
    lookup_type: str,
    *,
    plate: str | None = None,
    vin: str | None = None,
    geotab_status: str = "unknown",
    geotab_customer_status: str = "not_applicable",
    fenix_details: dict[str, str | None] | None = None,
    cummins_details: dict[str, str] | None = None,
    warnings: list[str] | None = None,
) -> VehicleLookupResponse:
    return VehicleLookupResponse(
        plate=plate,
        lookup_value=lookup_value,
        lookup_type=lookup_type,
        vin=vin,
        geotab_status=geotab_status,
        geotab_customer_status=geotab_customer_status,
        source_details={
            "fenix": fenix_details or {},
            "cummins": cummins_details or {},
        },
        warnings=warnings or [],
        status="not_found",
        message="Vehiculo no encontrado.",
    )


def _error_response(
    lookup_value: str,
    lookup_type: str,
    *,
    plate: str | None = None,
    vin: str | None = None,
    geotab_status: str = "unknown",
    geotab_customer_status: str = "not_applicable",
    fenix_details: dict[str, str | None] | None = None,
    cummins_details: dict[str, str] | None = None,
    warnings: list[str] | None = None,
) -> VehicleLookupResponse:
    return VehicleLookupResponse(
        plate=plate,
        lookup_value=lookup_value,
        lookup_type=lookup_type,
        vin=vin,
        geotab_status=geotab_status,
        geotab_customer_status=geotab_customer_status,
        source_details={
            "fenix": fenix_details or {},
            "cummins": cummins_details or {},
        },
        warnings=warnings or [],
        status="error",
        message="No fue posible completar la consulta del vehiculo.",
    )


def _resolve_vin_from_plate(
    plate: str, warnings: list[str]
) -> tuple[str | None, str, dict[str, str | None]]:
    geotab_status = "unknown"

    try:
        geotab_cfg = load_geotab_config()
        device = get_device_from_plate(plate, geotab_cfg)
        if device:
            geotab_status = "found"
            vin = extract_geotab_vin(device)
            if vin:
                return vin.strip().upper(), "found", {}
            # Device exists in Geotab but has no VIN stored — fall through to SQL
            warnings.append("Vehiculo encontrado en Geotab pero sin VIN registrado. Se intentara obtener el VIN desde SQL.")
        else:
            geotab_status = "not_found"
            warnings.append("Vehiculo no encontrado en Geotab. Se intentara completar la consulta con SQL.")
    except Exception:
        _logger.exception("Geotab error durante _resolve_vin_from_plate para plate=%s", plate)
        warnings.append("No se pudo validar el vehiculo en Geotab.")

    fallback_row = get_vehicle_by_plate(plate, load_sql_config())
    if fallback_row and fallback_row.get("VIN"):
        warnings.append("VIN resuelto desde SQL por placa.")
        return (
            str(fallback_row["VIN"]).strip().upper(),
            geotab_status,
            _normalize_fenix_details(fallback_row),
        )

    return None, geotab_status, {}


def _resolve_geotab_status(plate: str | None, vin: str | None, warnings: list[str]) -> str:
    try:
        geotab_cfg = load_geotab_config()
        if plate and get_device_from_plate(plate, geotab_cfg):
            return "found"
        if vin and get_device_from_vin(vin, geotab_cfg):
            return "found"
        warnings.append("El vehiculo no existe en Geotab.")
        return "not_found"
    except Exception:
        _logger.exception("Geotab error durante _resolve_geotab_status para plate=%s vin=%s", plate, vin)
        warnings.append("No se pudo validar la existencia del vehiculo en Geotab.")
        return "unknown"


def lookup_vehicle(identifier: str, *, force: bool = False) -> VehicleLookupResponse:
    normalized_identifier = identifier.strip().upper()
    lookup_type = "vin" if _is_vin(normalized_identifier) else "plate"

    if not force and lookup_type == "plate":
        cached = get_cached_vehicle_lookup(normalized_identifier)
        if cached is not None:
            return cached
    warnings: list[str] = []
    geotab_status = "unknown"
    fenix_details: dict[str, str | None] = {}
    cummins_details: dict[str, str] = {}

    plate = normalized_identifier if lookup_type == "plate" else None
    vin = normalized_identifier if lookup_type == "vin" else None

    try:
        sql_cfg = load_sql_config()
        quickserve_cfg = load_quickserve_config()

        if lookup_type == "plate":
            vin, geotab_status, fallback_fenix_details = _resolve_vin_from_plate(
                normalized_identifier, warnings
            )
            fenix_details.update(fallback_fenix_details)
            if not vin:
                return _not_found_response(
                    normalized_identifier,
                    lookup_type,
                    plate=plate,
                    geotab_status=geotab_status,
                    fenix_details=fenix_details,
                    warnings=warnings,
                )

        vehicle_row = get_vehicle_by_vin(vin, sql_cfg) if vin else None
        if not vehicle_row:
            return _not_found_response(
                normalized_identifier,
                lookup_type,
                plate=plate,
                vin=vin,
                geotab_status=geotab_status,
                fenix_details=fenix_details,
                warnings=warnings,
            )

        fenix_details = _normalize_fenix_details(vehicle_row)
        if not plate and fenix_details.get("plate"):
            plate = fenix_details["plate"]

        if lookup_type == "vin":
            geotab_status = _resolve_geotab_status(plate, vin, warnings)

        engine_number = fenix_details.get("engine_number")
        if not engine_number:
            return _not_found_response(
                normalized_identifier,
                lookup_type,
                plate=plate,
                vin=vin,
                geotab_status=geotab_status,
                fenix_details=fenix_details,
                warnings=warnings,
            )

        cummins_details = get_engine_dataplate(engine_number, quickserve_cfg)
        cummins_vin = str(cummins_details.get("VIN") or "").strip().upper() or None
        if vin and cummins_vin and cummins_vin != vin:
            warnings.append(
                "QuickServe devolvio un VIN distinto al de Fenix/Geotab. No se registraron datos de Cummins para evitar una asignacion incorrecta."
            )
            _logger.warning(
                "VIN mismatch en lookup de vehiculo: plate=%s vin_fenix=%s vin_cummins=%s esn=%s",
                plate,
                vin,
                cummins_vin,
                engine_number,
            )
            if plate:
                update_vehicle_metadata(
                    plate,
                    geotab_status=geotab_status,
                    vin=vin,
                    engine_number=engine_number,
                    marca=fenix_details.get("marca"),
                    linea=fenix_details.get("linea"),
                    ano_modelo=fenix_details.get("ano_modelo"),
                    tipo_combustible=fenix_details.get("tipo_combustible"),
                )
            geotab_customer_info = get_vehicle_geotab_customer_status(plate)
            return VehicleLookupResponse(
                plate=plate,
                lookup_value=normalized_identifier,
                lookup_type=lookup_type,
                vin=vin,
                geotab_status=geotab_status,
                geotab_customer_status=geotab_customer_info.get(
                    "geotab_customer_status", "not_applicable"
                ),
                marca=fenix_details.get("marca"),
                linea=fenix_details.get("linea"),
                ano_modelo=fenix_details.get("ano_modelo"),
                tipo_combustible=fenix_details.get("tipo_combustible"),
                engine_number=engine_number,
                technical_engine_configuration=None,
                cpl=None,
                registered_motor=None,
                assigned_database=get_vehicle_database_assignment(plate),
                source_details={
                    "fenix": fenix_details,
                    "cummins": cummins_details,
                },
                warnings=warnings,
                status="partial",
                message="Consulta completada con advertencias: el dataplate de Cummins no coincide con el VIN del vehiculo.",
            )
        technical_config = extract_technical_engine_configuration(cummins_details)
        cpl = extract_cpl(cummins_details)
        if not technical_config:
            warnings.append(
                "Motor no encontrado en Cummins. Puedes asignar un motor manualmente."
            )
            # Update Fenix/Geotab metadata without touching technical_number
            if plate:
                update_vehicle_metadata(
                    plate,
                    geotab_status=geotab_status,
                    vin=vin,
                    engine_number=engine_number,
                    marca=fenix_details.get("marca"),
                    linea=fenix_details.get("linea"),
                    ano_modelo=fenix_details.get("ano_modelo"),
                    tipo_combustible=fenix_details.get("tipo_combustible"),
                )
            geotab_customer_info = get_vehicle_geotab_customer_status(plate)
            return VehicleLookupResponse(
                plate=plate,
                lookup_value=normalized_identifier,
                lookup_type=lookup_type,
                vin=vin,
                geotab_status=geotab_status,
                geotab_customer_status=geotab_customer_info.get(
                    "geotab_customer_status", "not_applicable"
                ),
                marca=fenix_details.get("marca"),
                linea=fenix_details.get("linea"),
                ano_modelo=fenix_details.get("ano_modelo"),
                tipo_combustible=fenix_details.get("tipo_combustible"),
                engine_number=engine_number,
                technical_engine_configuration=None,
                cpl=None,
                registered_motor=None,
                assigned_database=get_vehicle_database_assignment(plate),
                source_details={
                    "fenix": fenix_details,
                    "cummins": cummins_details,
                },
                warnings=warnings,
                status="partial",
                message="Consulta parcial: el motor no existe en Cummins.",
            )

        if plate:
            register_vehicle_assignment(
                plate=plate,
                technical_number=technical_config,
                cpl=cpl,
                geotab_status=geotab_status,
                vin=vin,
                engine_number=engine_number,
                marca=fenix_details.get("marca"),
                linea=fenix_details.get("linea"),
                ano_modelo=fenix_details.get("ano_modelo"),
                tipo_combustible=fenix_details.get("tipo_combustible"),
            )

        message = "Consulta completada."
        if warnings:
            message = "Consulta completada con advertencias."

        geotab_customer_info = get_vehicle_geotab_customer_status(plate)

        return VehicleLookupResponse(
            plate=plate,
            lookup_value=normalized_identifier,
            lookup_type=lookup_type,
            vin=vin,
            geotab_status=geotab_status,
            geotab_customer_status=geotab_customer_info.get(
                "geotab_customer_status", "not_applicable"
            ),
            marca=fenix_details.get("marca"),
            linea=fenix_details.get("linea"),
            modelo=fenix_details.get("modelo"),
            engine_number=engine_number,
            technical_engine_configuration=technical_config,
            cpl=cpl,
            registered_motor=find_registered_motor(technical_config),
            assigned_database=get_vehicle_database_assignment(plate),
            source_details={
                "fenix": fenix_details,
                "cummins": cummins_details,
            },
            warnings=warnings,
            status="ok",
            message=message,
        )
    except Exception:
        return _error_response(
            normalized_identifier,
            lookup_type,
            plate=plate,
            vin=vin,
            geotab_status=geotab_status,
            fenix_details=fenix_details,
            cummins_details=cummins_details,
            warnings=warnings,
        )
