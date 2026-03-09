from __future__ import annotations

from app.clients.geotab_client import get_vin_from_plate
from app.clients.quickserve_client import get_technical_config_from_esn
from app.clients.sql_client import get_engine_number_from_vin
from app.core.config import load_geotab_config, load_quickserve_config, load_sql_config
from app.services.motor_catalog import find_registered_motor, register_vehicle_assignment
from app.schemas.vehicle import VehicleLookupResponse


def lookup_vehicle_by_plate(plate: str) -> VehicleLookupResponse:
    normalized_plate = plate.strip().upper()

    try:
        geotab_cfg = load_geotab_config()
        sql_cfg = load_sql_config()
        quickserve_cfg = load_quickserve_config()

        vin = get_vin_from_plate(normalized_plate, geotab_cfg)
        if not vin:
            return VehicleLookupResponse(
                plate=normalized_plate,
                status="not_found",
                message="No se encontro la placa en Geotab.",
            )

        engine_number = get_engine_number_from_vin(vin, sql_cfg)
        if not engine_number:
            return VehicleLookupResponse(
                plate=normalized_plate,
                vin=vin,
                status="partial",
                message="Se encontro VIN, pero no numero de motor en inventario.",
            )

        technical_config = get_technical_config_from_esn(engine_number, quickserve_cfg)
        if not technical_config:
            return VehicleLookupResponse(
                plate=normalized_plate,
                vin=vin,
                engine_number=engine_number,
                status="partial",
                message="Se encontro ESN, pero QuickServe no devolvio configuracion tecnica.",
            )

        register_vehicle_assignment(
            plate=normalized_plate,
            technical_number=technical_config,
            vin=vin,
            engine_number=engine_number,
        )

        return VehicleLookupResponse(
            plate=normalized_plate,
            vin=vin,
            engine_number=engine_number,
            technical_engine_configuration=technical_config,
            registered_motor=find_registered_motor(technical_config),
            status="ok",
            message="Consulta completada.",
        )
    except Exception as exc:
        return VehicleLookupResponse(
            plate=normalized_plate,
            status="error",
            message=f"Error consultando fuentes reales: {exc}",
        )
