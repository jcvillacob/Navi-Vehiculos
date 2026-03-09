from fastapi import APIRouter, Query

from app.schemas.vehicle import VehicleAssignmentRecord, VehicleLookupResponse
from app.services.motor_catalog import list_vehicle_assignments
from app.services.vehicle_lookup import lookup_vehicle as lookup_vehicle_service

router = APIRouter(prefix="/vehicle", tags=["vehicle"])


@router.get("/lookup", response_model=VehicleLookupResponse)
def lookup_vehicle(
    identifier: str = Query(..., min_length=3, max_length=32, description="Placa o VIN del vehiculo")
) -> VehicleLookupResponse:
    return lookup_vehicle_service(identifier)


@router.get("", response_model=list[VehicleAssignmentRecord])
def get_vehicle_assignments(
    search: str | None = Query(
        default=None,
        min_length=1,
        max_length=64,
        description="Texto para filtrar por placa, VIN, TEC# o nombre del motor",
    )
) -> list[VehicleAssignmentRecord]:
    return list_vehicle_assignments(search)
