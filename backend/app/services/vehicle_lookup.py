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
from app.clients.sql_client import (
    find_vehicles_by_plates,
    find_vehicles_by_vins,
    get_vehicle_by_plate,
    get_vehicle_by_vin,
    open_connection,
)
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
_SENTINEL = object()


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
        "nombre_vehiculo": str(row.get("Nombre Vehiculo")).strip() if row.get("Nombre Vehiculo") else None,
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
    plate: str, warnings: list[str], on_step=None
) -> tuple[str | None, str, dict[str, str | None]]:
    geotab_status = "unknown"

    try:
        if on_step:
            on_step({"step": "geotab_lookup", "status": "running", "source": "geotab", "message": f"Buscando {plate} en Geotab..."})
        geotab_cfg = load_geotab_config()
        device = get_device_from_plate(plate, geotab_cfg)
        if device:
            geotab_status = "found"
            vin = extract_geotab_vin(device)
            if vin:
                if on_step:
                    on_step({"step": "geotab_lookup", "status": "ok", "source": "geotab", "message": f"Encontrado en Geotab (VIN {vin.strip().upper()})"})
                return vin.strip().upper(), "found", {}
            if on_step:
                on_step({"step": "geotab_lookup", "status": "warning", "source": "geotab", "message": "Encontrado en Geotab sin VIN; consultando Fenix..."})
            warnings.append("Vehiculo encontrado en Geotab pero sin VIN registrado. Se intentara obtener el VIN desde SQL.")
        else:
            geotab_status = "not_found"
            if on_step:
                on_step({"step": "geotab_lookup", "status": "warning", "source": "geotab", "message": "No encontrado en Geotab; consultando Fenix..."})
            warnings.append("Vehiculo no encontrado en Geotab. Se intentara completar la consulta con SQL.")
    except Exception:
        _logger.exception("Geotab error durante _resolve_vin_from_plate para plate=%s", plate)
        if on_step:
            on_step({"step": "geotab_lookup", "status": "error", "source": "geotab", "message": "Error consultando Geotab"})
        warnings.append("No se pudo validar el vehiculo en Geotab.")

    if on_step:
        on_step({"step": "fenix_lookup", "status": "running", "source": "fenix", "message": f"Buscando {plate} en Fenix..."})
    fallback_row = get_vehicle_by_plate(plate, load_sql_config())
    if fallback_row and fallback_row.get("VIN"):
        if on_step:
            on_step({"step": "fenix_lookup", "status": "ok", "source": "fenix", "message": "Encontrado en Fenix"})
        warnings.append("VIN resuelto desde SQL por placa.")
        return (
            str(fallback_row["VIN"]).strip().upper(),
            geotab_status,
            _normalize_fenix_details(fallback_row),
        )

    if on_step:
        on_step({"step": "fenix_lookup", "status": "warning", "source": "fenix", "message": "No encontrado en Fenix"})
    return None, geotab_status, {}


def _resolve_geotab_status(plate: str | None, vin: str | None, warnings: list[str], on_step=None) -> str:
    try:
        if on_step:
            on_step({"step": "geotab_lookup", "status": "running", "source": "geotab", "message": "Validando en Geotab..."})
        geotab_cfg = load_geotab_config()
        if plate and get_device_from_plate(plate, geotab_cfg):
            if on_step:
                on_step({"step": "geotab_lookup", "status": "ok", "source": "geotab", "message": "Encontrado en Geotab"})
            return "found"
        if vin and get_device_from_vin(vin, geotab_cfg):
            if on_step:
                on_step({"step": "geotab_lookup", "status": "ok", "source": "geotab", "message": "Encontrado en Geotab"})
            return "found"
        if on_step:
            on_step({"step": "geotab_lookup", "status": "warning", "source": "geotab", "message": "No encontrado en Geotab"})
        warnings.append("El vehiculo no existe en Geotab.")
        return "not_found"
    except Exception:
        _logger.exception("Geotab error durante _resolve_geotab_status para plate=%s vin=%s", plate, vin)
        if on_step:
            on_step({"step": "geotab_lookup", "status": "error", "source": "geotab", "message": "Error consultando Geotab"})
        warnings.append("No se pudo validar la existencia del vehiculo en Geotab.")
        return "unknown"


def _get_existing_motor(plate: str):
    """Return the registered motor from the existing assignment, if any."""
    if not plate:
        return None
    cached = get_cached_vehicle_lookup(plate)
    if cached and cached.technical_engine_configuration:
        return find_registered_motor(cached.technical_engine_configuration)
    return None


def _lookup_cummins_only(plate: str) -> VehicleLookupResponse:
    """Re-query Cummins using the engine_number already stored locally."""
    cached = get_cached_vehicle_lookup(plate)
    if not cached or not cached.engine_number:
        return _error_response(
            plate,
            "plate",
            plate=plate,
            warnings=["No hay numero de motor almacenado para re-consultar Cummins."],
        )
    engine_number = cached.engine_number
    vin = cached.vin
    geotab_status = cached.geotab_status or "unknown"
    warnings: list[str] = []
    try:
        quickserve_cfg = load_quickserve_config()
        cummins_details = get_engine_dataplate(engine_number, quickserve_cfg)
        cummins_vin = str(cummins_details.get("VIN") or "").strip().upper() or None
        if vin and cummins_vin and cummins_vin != vin:
            warnings.append(
                "QuickServe devolvio un VIN distinto al almacenado."
            )
            return VehicleLookupResponse(
                plate=plate,
                lookup_value=plate,
                lookup_type="plate",
                vin=vin,
                geotab_status=geotab_status,
                geotab_customer_status=cached.geotab_customer_status or "not_applicable",
                marca=cached.marca,
                linea=cached.linea,
                ano_modelo=cached.ano_modelo,
                tipo_combustible=cached.tipo_combustible,
                engine_number=engine_number,
                technical_engine_configuration=cached.technical_engine_configuration,
                cpl=cached.cpl,
                registered_motor=cached.registered_motor,
                assigned_database=get_vehicle_database_assignment(plate),
                source_details={"fenix": {}, "cummins": cummins_details},
                warnings=warnings,
                status="partial",
                message="VIN mismatch entre datos locales y Cummins.",
            )
        technical_config = extract_technical_engine_configuration(cummins_details)
        cpl = extract_cpl(cummins_details)
        if not technical_config:
            warnings.append("Motor no encontrado en Cummins.")
            return VehicleLookupResponse(
                plate=plate,
                lookup_value=plate,
                lookup_type="plate",
                vin=vin,
                geotab_status=geotab_status,
                geotab_customer_status=cached.geotab_customer_status or "not_applicable",
                marca=cached.marca,
                linea=cached.linea,
                ano_modelo=cached.ano_modelo,
                tipo_combustible=cached.tipo_combustible,
                engine_number=engine_number,
                technical_engine_configuration=None,
                cpl=None,
                registered_motor=None,
                assigned_database=get_vehicle_database_assignment(plate),
                source_details={"fenix": {}, "cummins": cummins_details},
                warnings=warnings,
                status="partial",
                message="Motor no existe en Cummins.",
            )
        register_vehicle_assignment(
            plate=plate,
            technical_number=technical_config,
            cpl=cpl,
            geotab_status=geotab_status,
            vin=vin,
            engine_number=engine_number,
            marca=cached.marca,
            linea=cached.linea,
            ano_modelo=cached.ano_modelo,
            tipo_combustible=cached.tipo_combustible,
            nombre_vehiculo=cached.nombre_vehiculo,
        )
        return VehicleLookupResponse(
            plate=plate,
            lookup_value=plate,
            lookup_type="plate",
            vin=vin,
            geotab_status=geotab_status,
            geotab_customer_status=cached.geotab_customer_status or "not_applicable",
            marca=cached.marca,
            linea=cached.linea,
            ano_modelo=cached.ano_modelo,
            tipo_combustible=cached.tipo_combustible,
            engine_number=engine_number,
            technical_engine_configuration=technical_config,
            cpl=cpl,
            registered_motor=find_registered_motor(technical_config),
            assigned_database=get_vehicle_database_assignment(plate),
            source_details={"fenix": {}, "cummins": cummins_details},
            warnings=warnings,
            status="ok",
            message="Datos Cummins actualizados.",
        )
    except Exception:
        _logger.exception("Error en _lookup_cummins_only para plate=%s", plate)
        return _error_response(plate, "plate", plate=plate, vin=vin)


def lookup_vehicle(
    identifier: str,
    *,
    force: bool = False,
    _prefetched_fenix_row: dict | None | object = _SENTINEL,
    scope: str = "all",
    skip_geotab: bool = False,
    on_step=None,
) -> VehicleLookupResponse:
    normalized_identifier = identifier.strip().upper()
    lookup_type = "vin" if _is_vin(normalized_identifier) else "plate"

    if not force and lookup_type == "plate":
        cached = get_cached_vehicle_lookup(normalized_identifier)
        if cached is not None:
            if on_step:
                on_step({"step": "cache_hit", "status": "ok", "source": "cache", "message": "Resultado cargado desde cache local"})
            return cached
    warnings: list[str] = []
    geotab_status = "unknown"
    fenix_details: dict[str, str | None] = {}
    cummins_details: dict[str, str] = {}

    plate = normalized_identifier if lookup_type == "plate" else None
    vin = normalized_identifier if lookup_type == "vin" else None

    has_prefetch = _prefetched_fenix_row is not _SENTINEL

    # ── scope="cummins": skip Fenix/Geotab, re-query only Cummins ──
    if scope == "cummins" and lookup_type == "plate":
        if on_step:
            on_step({"step": "cummins_lookup", "status": "running", "source": "cummins", "message": "Re-consultando QuickServe/Cummins..."})
        result = _lookup_cummins_only(normalized_identifier)
        if on_step:
            on_step({"step": "cummins_lookup", "status": result.status, "source": "cummins", "message": result.message})
        return result

    try:
        sql_cfg = load_sql_config()
        quickserve_cfg = load_quickserve_config()

        if lookup_type == "plate":
            if has_prefetch and _prefetched_fenix_row:
                # VIN already resolved from batch pre-fetch
                fenix_details = _normalize_fenix_details(_prefetched_fenix_row)
                vin = fenix_details.get("vin")
                if not skip_geotab:
                    geotab_status = _resolve_geotab_status(plate, vin, warnings, on_step=on_step)
            elif has_prefetch and not _prefetched_fenix_row:
                # Batch pre-fetch ran but plate was not found in Fenix
                if not skip_geotab:
                    geotab_status = _resolve_geotab_status(plate, None, warnings, on_step=on_step)
            else:
                if skip_geotab:
                    # Skip Geotab — resolve VIN from SQL only
                    if on_step:
                        on_step({"step": "fenix_lookup", "status": "running", "source": "fenix", "message": f"Buscando {normalized_identifier} en Fenix..."})
                    fallback_row = get_vehicle_by_plate(normalized_identifier, sql_cfg)
                    if fallback_row and fallback_row.get("VIN"):
                        vin = str(fallback_row["VIN"]).strip().upper()
                        fenix_details.update(_normalize_fenix_details(fallback_row))
                        if on_step:
                            on_step({"step": "fenix_lookup", "status": "ok", "source": "fenix", "message": "Encontrado en Fenix"})
                else:
                    vin, geotab_status, fallback_fenix_details = _resolve_vin_from_plate(
                        normalized_identifier, warnings, on_step=on_step
                    )
                    fenix_details.update(fallback_fenix_details)
            if not vin:
                if on_step:
                    on_step({"step": "fenix_lookup", "status": "warning", "source": "fenix", "message": "No se resolvio VIN; vehiculo no encontrado"})
                return _not_found_response(
                    normalized_identifier,
                    lookup_type,
                    plate=plate,
                    geotab_status=geotab_status,
                    fenix_details=fenix_details,
                    warnings=warnings,
                )

        if has_prefetch:
            # Use pre-fetched data (may be the plate row or a separate VIN lookup)
            vehicle_row = _prefetched_fenix_row if _prefetched_fenix_row else None
        else:
            if on_step and lookup_type == "vin":
                on_step({"step": "fenix_lookup", "status": "running", "source": "fenix", "message": f"Buscando VIN {vin} en Fenix..."})
            vehicle_row = get_vehicle_by_vin(vin, sql_cfg) if vin else None
            if on_step and lookup_type == "vin":
                if vehicle_row:
                    on_step({"step": "fenix_lookup", "status": "ok", "source": "fenix", "message": "Encontrado en Fenix"})
                else:
                    on_step({"step": "fenix_lookup", "status": "warning", "source": "fenix", "message": "No encontrado en Fenix"})
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

        if lookup_type == "vin" and not skip_geotab:
            geotab_status = _resolve_geotab_status(plate, vin, warnings, on_step=on_step)

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

        # ── scope="fenix": update metadata only, skip Cummins ──
        if scope == "fenix":
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
                    nombre_vehiculo=fenix_details.get("nombre_vehiculo"),
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
                registered_motor=_get_existing_motor(plate),
                assigned_database=get_vehicle_database_assignment(plate),
                source_details={"fenix": fenix_details, "cummins": {}},
                warnings=warnings,
                status="ok",
                message="Datos Fenix/Geotab actualizados.",
            )

        cummins_details = get_engine_dataplate(engine_number, quickserve_cfg)
        if on_step:
            cummins_ok = bool(
                cummins_details
                and (cummins_details.get("Engine Configuration Number") or cummins_details.get("CPL Number"))
            )
            on_step({
                "step": "cummins_lookup",
                "status": "ok" if cummins_ok else "warning",
                "source": "cummins",
                "message": "Encontrado en Cummins/QuickServe" if cummins_ok else "Motor no encontrado en Cummins/QuickServe",
            })
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
                    nombre_vehiculo=fenix_details.get("nombre_vehiculo"),
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
                    nombre_vehiculo=fenix_details.get("nombre_vehiculo"),
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
                nombre_vehiculo=fenix_details.get("nombre_vehiculo"),
            )

        message = "Consulta completada."
        if warnings:
            message = "Consulta completada con advertencias."

        geotab_customer_info = get_vehicle_geotab_customer_status(plate)

        if on_step:
            motor = find_registered_motor(technical_config)
            on_step({
                "step": "motor_registered",
                "status": "ok" if motor else "info",
                "source": "local",
                "message": (
                    f"Motor registrado: {motor.engine_name} (TEC# {motor.technical_number})"
                    if motor
                    else "Motor no registrado en catalogo local"
                ),
            })
            on_step({
                "step": "done",
                "status": "ok",
                "source": "local",
                "message": "Consulta completada",
            })

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
        _logger.exception(
            "Vehicle lookup failed para identifier=%s lookup_type=%s scope=%s skip_geotab=%s",
            normalized_identifier,
            lookup_type,
            scope,
            skip_geotab,
        )
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


def batch_lookup_vehicles(
    identifiers: list[str], *, force: bool = False
) -> list[VehicleLookupResponse]:
    """Pre-fetch all Fenix data in a single batch query, then process each vehicle."""
    if not identifiers:
        return []

    normalized = [i.strip().upper() for i in identifiers]

    # Separate VINs from plates
    vin_identifiers: list[str] = []
    plate_identifiers: list[str] = []
    for ident in normalized:
        if _is_vin(ident):
            vin_identifiers.append(ident)
        else:
            plate_identifiers.append(ident)

    # Check cache for plates (when not forcing)
    cached_results: dict[str, VehicleLookupResponse] = {}
    plates_to_fetch: list[str] = []
    if not force:
        for plate in plate_identifiers:
            cached = get_cached_vehicle_lookup(plate)
            if cached is not None:
                cached_results[plate] = cached
            else:
                plates_to_fetch.append(plate)
    else:
        plates_to_fetch = list(plate_identifiers)

    # Batch pre-fetch from Fenix (single connection)
    fenix_by_vin: dict[str, dict] = {}
    fenix_by_plate: dict[str, dict] = {}
    try:
        sql_cfg = load_sql_config()
        conn = open_connection(sql_cfg)
        try:
            # 1) Resolve plates → get rows (which contain VINs)
            if plates_to_fetch:
                fenix_by_plate = find_vehicles_by_plates(conn, plates_to_fetch)

            # 2) Collect all VINs (direct + resolved from plates) and batch-fetch
            resolved_vins = set(vin_identifiers)
            for plate, row in fenix_by_plate.items():
                row_vin = str(row.get("VIN") or "").strip().upper()
                if row_vin:
                    resolved_vins.add(row_vin)
            all_vins = list(resolved_vins)

            if all_vins:
                fenix_by_vin = find_vehicles_by_vins(conn, all_vins)
        finally:
            conn.close()
    except Exception:
        _logger.exception("Fenix batch pre-fetch failed — falling back to individual lookups")
        # fenix_by_vin and fenix_by_plate remain empty; lookup_vehicle will call Fenix individually

    # Process each identifier
    results: list[VehicleLookupResponse] = []
    for ident in normalized:
        results.append(_resolve_single(
            ident, force, cached_results, fenix_by_vin, fenix_by_plate, plates_to_fetch
        ))

    return results


def _resolve_single(
    ident: str,
    force: bool,
    cached_results: dict[str, VehicleLookupResponse],
    fenix_by_vin: dict[str, dict],
    fenix_by_plate: dict[str, dict],
    plates_to_fetch: list[str],
    scope: str = "all",
    skip_geotab: bool = False,
) -> VehicleLookupResponse:
    if ident in cached_results:
        return cached_results[ident]

    if _is_vin(ident):
        prefetched = fenix_by_vin.get(ident) if fenix_by_vin else _SENTINEL
    else:
        plate_row = fenix_by_plate.get(ident) if fenix_by_plate else None
        if plate_row:
            row_vin = str(plate_row.get("VIN") or "").strip().upper()
            prefetched = fenix_by_vin.get(row_vin, plate_row) if row_vin and fenix_by_vin else plate_row
        elif fenix_by_plate is not None and ident in plates_to_fetch:
            prefetched = None
        else:
            prefetched = _SENTINEL

    return lookup_vehicle(ident, force=force, _prefetched_fenix_row=prefetched, scope=scope, skip_geotab=skip_geotab)


def batch_lookup_vehicles_stream(
    identifiers: list[str], *, force: bool = False, scope: str = "all", skip_geotab: bool = False
):
    """Same as batch_lookup_vehicles but yields each result as it completes."""
    if not identifiers:
        return

    normalized = [i.strip().upper() for i in identifiers]

    vin_identifiers: list[str] = []
    plate_identifiers: list[str] = []
    for ident in normalized:
        if _is_vin(ident):
            vin_identifiers.append(ident)
        else:
            plate_identifiers.append(ident)

    cached_results: dict[str, VehicleLookupResponse] = {}
    plates_to_fetch: list[str] = []
    if not force:
        for plate in plate_identifiers:
            cached = get_cached_vehicle_lookup(plate)
            if cached is not None:
                cached_results[plate] = cached
            else:
                plates_to_fetch.append(plate)
    else:
        plates_to_fetch = list(plate_identifiers)

    fenix_by_vin: dict[str, dict] = {}
    fenix_by_plate: dict[str, dict] = {}
    if scope != "cummins":
        try:
            sql_cfg = load_sql_config()
            conn = open_connection(sql_cfg)
            try:
                if plates_to_fetch:
                    fenix_by_plate = find_vehicles_by_plates(conn, plates_to_fetch)
                resolved_vins = set(vin_identifiers)
                for _plate, row in fenix_by_plate.items():
                    row_vin = str(row.get("VIN") or "").strip().upper()
                    if row_vin:
                        resolved_vins.add(row_vin)
                if resolved_vins:
                    fenix_by_vin = find_vehicles_by_vins(conn, list(resolved_vins))
            finally:
                conn.close()
        except Exception:
            _logger.exception("Fenix batch pre-fetch failed — falling back to individual lookups")

    for ident in normalized:
        try:
            yield _resolve_single(
                ident, force, cached_results, fenix_by_vin, fenix_by_plate, plates_to_fetch, scope, skip_geotab
            )
        except Exception:
            _logger.exception("Error processing identifier %s in batch stream", ident)
            yield _error_response(ident, "vin" if _is_vin(ident) else "plate")
