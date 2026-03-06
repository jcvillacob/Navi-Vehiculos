from fastapi import APIRouter, Query

from app.schemas.vehicle import VehicleLookupResponse
from app.services.vehicle_lookup import lookup_vehicle_by_plate

router = APIRouter(prefix="/vehicle", tags=["vehicle"])


@router.get("/lookup", response_model=VehicleLookupResponse)
def lookup_vehicle(
    plate: str = Query(..., min_length=3, max_length=10, description="Placa del vehiculo")
) -> VehicleLookupResponse:
    return lookup_vehicle_by_plate(plate)