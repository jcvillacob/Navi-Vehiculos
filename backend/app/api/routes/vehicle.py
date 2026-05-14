from fastapi import APIRouter, Depends, HTTPException, Path, Query

from app.core.dependencies import require_permission
from app.schemas.vehicle import (
    AssignedDatabaseSummary,
    ManualVehicleAssignmentRequest,
    VehicleAssignmentRecord,
    VehicleDatabaseAssignmentRequest,
    VehicleLookupResponse,
)
from app.services.motor_catalog import (
    assign_vehicle_database,
    check_all_geotab_connections,
    get_connection_stats,
    list_vehicle_assignments,
    register_vehicle_assignment,
    revalidate_vehicle_customer_geotab,
)
from app.services.vehicle_lookup import lookup_vehicle as lookup_vehicle_service

router = APIRouter(prefix="/vehicle", tags=["vehicle"])


@router.get("/lookup", response_model=VehicleLookupResponse)
def lookup_vehicle(
    identifier: str = Query(..., min_length=3, max_length=32, description="Placa o VIN del vehiculo"),
    force: bool = Query(default=False, description="Forzar consulta externa ignorando cache local"),
    _user: dict = Depends(require_permission("engine_lookup.use")),
) -> VehicleLookupResponse:
    return lookup_vehicle_service(identifier, force=force)


@router.get("", response_model=list[VehicleAssignmentRecord])
def get_vehicle_assignments(
    search: str | None = Query(
        default=None,
        min_length=1,
        max_length=64,
        description="Texto para filtrar por placa, VIN, TEC# o nombre del motor",
    ),
    _user: dict = Depends(require_permission("vehicles.list")),
) -> list[VehicleAssignmentRecord]:
    return list_vehicle_assignments(search)


@router.put("/{plate}/database", response_model=AssignedDatabaseSummary)
def assign_database_to_vehicle(
    payload: VehicleDatabaseAssignmentRequest,
    plate: str = Path(..., min_length=1, max_length=10, description="Placa del vehiculo"),
    _user: dict = Depends(require_permission("vehicles.edit")),
) -> AssignedDatabaseSummary:
    try:
        return assign_vehicle_database(plate, payload)
    except ValueError as exc:
        status_code = 404 if "no existe" in str(exc).lower() else 400
        raise HTTPException(status_code=status_code, detail=str(exc)) from exc


@router.post("/{plate}/manual-assign", status_code=201)
def manual_assign_vehicle(
    payload: ManualVehicleAssignmentRequest,
    plate: str = Path(..., min_length=1, max_length=10, description="Placa del vehiculo"),
    _user: dict = Depends(require_permission("vehicles.edit")),
) -> dict:
    try:
        register_vehicle_assignment(
            plate=plate,
            technical_number=payload.technical_number,
            cpl=payload.cpl,
            geotab_status=payload.geotab_status,
            vin=payload.vin,
            engine_number=payload.engine_number,
            marca=payload.marca,
            linea=payload.linea,
            ano_modelo=payload.ano_modelo,
            tipo_combustible=payload.tipo_combustible,
        )
        return {"registered": True, "plate": plate.strip().upper()}
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/{plate}/refresh", response_model=VehicleLookupResponse)
def refresh_vehicle_assignment(
    plate: str = Path(..., min_length=1, max_length=10, description="Placa del vehiculo"),
    _user: dict = Depends(require_permission("vehicles.refresh")),
) -> VehicleLookupResponse:
    return lookup_vehicle_service(plate, force=True)


@router.post("/{plate}/revalidate-customer-geotab")
def revalidate_customer_geotab(
    plate: str = Path(..., min_length=1, max_length=10, description="Placa del vehiculo"),
    _user: dict = Depends(require_permission("vehicles.refresh")),
) -> dict:
    try:
        return revalidate_vehicle_customer_geotab(plate)
    except ValueError as exc:
        status_code = 404 if "no existe" in str(exc).lower() else 400
        raise HTTPException(status_code=status_code, detail=str(exc)) from exc


@router.post(
    "/check-connections",
    description="Revisa el estado de conexion en Geotab para todos los vehiculos elegibles",
)
def check_vehicle_connections(
    _user: dict = Depends(require_permission("vehicles.refresh")),
) -> dict:
    return check_all_geotab_connections()


@router.get(
    "/connection-stats",
    description="Estadisticas de conexion por vehiculo para un mes dado",
)
def vehicle_connection_stats(
    month: str = Query(..., pattern=r"^\d{4}-\d{2}$", description="Mes en formato YYYY-MM"),
    _user: dict = Depends(require_permission("rendimientos.view")),
) -> list[dict]:
    return get_connection_stats(month)
