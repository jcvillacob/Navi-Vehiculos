from fastapi import APIRouter, HTTPException, status

from app.schemas.vehicle import MotorCatalogRecord, MotorCatalogUpsertRequest
from app.services.motor_catalog import create_motor, list_motors

router = APIRouter(prefix="/motors", tags=["motors"])


@router.get("", response_model=list[MotorCatalogRecord])
def get_motors() -> list[MotorCatalogRecord]:
    return list_motors()


@router.post("", response_model=MotorCatalogRecord, status_code=status.HTTP_201_CREATED)
def create_motor_record(payload: MotorCatalogUpsertRequest) -> MotorCatalogRecord:
    try:
        return create_motor(payload)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
