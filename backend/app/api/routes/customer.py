from fastapi import APIRouter, HTTPException, Path, status

from app.schemas.vehicle import (
    CustomerCreateRequest,
    CustomerDatabaseCreateRequest,
    CustomerDatabaseRecord,
    CustomerRecord,
)
from app.services.motor_catalog import create_customer, create_customer_database, list_customers

router = APIRouter(prefix="/customers", tags=["customers"])


@router.get("", response_model=list[CustomerRecord])
def get_customers() -> list[CustomerRecord]:
    return list_customers()


@router.post("", response_model=CustomerRecord, status_code=status.HTTP_201_CREATED)
def create_customer_record(payload: CustomerCreateRequest) -> CustomerRecord:
    try:
        return create_customer(payload)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/{customer_id}/databases", response_model=CustomerDatabaseRecord, status_code=status.HTTP_201_CREATED)
def create_database_record(
    payload: CustomerDatabaseCreateRequest,
    customer_id: int = Path(..., gt=0, description="ID del cliente"),
) -> CustomerDatabaseRecord:
    try:
        return create_customer_database(customer_id, payload)
    except ValueError as exc:
        status_code = 404 if "no existe" in str(exc).lower() else 409
        raise HTTPException(status_code=status_code, detail=str(exc)) from exc
