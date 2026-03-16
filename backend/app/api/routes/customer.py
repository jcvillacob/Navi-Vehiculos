from fastapi import APIRouter, HTTPException, Path, Query, status

from app.schemas.vehicle import (
    CustomerCreateRequest,
    CustomerDatabaseCreateRequest,
    CustomerDatabaseRecord,
    CustomerDatabaseUpdateRequest,
    CustomerRecord,
    CustomerUpdateRequest,
    GeotabRuleGroupCreateRequest,
    GeotabRuleGroupRecord,
    GeotabRuleCreateRequest,
    GeotabRuleInspection,
    GeotabRuleRecord,
)
from app.services.motor_catalog import (
    create_customer,
    create_customer_database,
    create_geotab_rule_group,
    create_geotab_rule,
    delete_geotab_rule_group,
    delete_geotab_rule,
    inspect_geotab_rule_record,
    list_customers,
    resolve_geotab_rule,
    update_customer,
    update_customer_database,
)

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


@router.put("/{customer_id}", response_model=CustomerRecord)
def update_customer_record(
    payload: CustomerUpdateRequest,
    customer_id: int = Path(..., gt=0, description="ID del cliente"),
) -> CustomerRecord:
    try:
        return update_customer(customer_id, payload)
    except ValueError as exc:
        status_code = 404 if "no existe" in str(exc).lower() else 409
        raise HTTPException(status_code=status_code, detail=str(exc)) from exc


@router.put("/databases/{database_id}", response_model=CustomerDatabaseRecord)
def update_database_record(
    payload: CustomerDatabaseUpdateRequest,
    database_id: int = Path(..., gt=0, description="ID de la database"),
) -> CustomerDatabaseRecord:
    try:
        return update_customer_database(database_id, payload)
    except ValueError as exc:
        status_code = 404 if "no existe" in str(exc).lower() else 409
        raise HTTPException(status_code=status_code, detail=str(exc)) from exc


@router.post(
    "/databases/{database_id}/rules",
    response_model=GeotabRuleRecord,
    status_code=status.HTTP_201_CREATED,
)
def create_rule_record(
    payload: GeotabRuleCreateRequest,
    database_id: int = Path(..., gt=0, description="ID de la database Geotab"),
) -> GeotabRuleRecord:
    try:
        return create_geotab_rule(database_id, payload)
    except ValueError as exc:
        status_code = 404 if "no existe" in str(exc).lower() else 409
        raise HTTPException(status_code=status_code, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.post(
    "/databases/{database_id}/rule-groups",
    response_model=GeotabRuleGroupRecord,
    status_code=status.HTTP_201_CREATED,
)
def create_rule_group_record(
    payload: GeotabRuleGroupCreateRequest,
    database_id: int = Path(..., gt=0, description="ID de la database Geotab"),
) -> GeotabRuleGroupRecord:
    try:
        return create_geotab_rule_group(database_id, payload)
    except ValueError as exc:
        status_code = 404 if "no existe" in str(exc).lower() else 409
        raise HTTPException(status_code=status_code, detail=str(exc)) from exc


@router.get(
    "/databases/{database_id}/rules/resolve",
    response_model=GeotabRuleInspection,
)
def resolve_rule_record(
    database_id: int = Path(..., gt=0, description="ID de la database Geotab"),
    rule_id: str = Query(..., min_length=1, description="ID alfanumerico de la regla en Geotab"),
) -> GeotabRuleInspection:
    try:
        return resolve_geotab_rule(database_id, rule_id)
    except ValueError as exc:
        status_code = 404 if "no existe" in str(exc).lower() else 409
        raise HTTPException(status_code=status_code, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.get("/rules/{rule_id}/inspection", response_model=GeotabRuleInspection)
def inspect_rule_record(
    rule_id: int = Path(..., gt=0, description="ID del registro local de la regla"),
) -> GeotabRuleInspection:
    try:
        return inspect_geotab_rule_record(rule_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.delete("/rules/{rule_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_rule_record(
    rule_id: int = Path(..., gt=0, description="ID de la regla"),
) -> None:
    try:
        delete_geotab_rule(rule_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.delete("/rule-groups/{group_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_rule_group_record(
    group_id: int = Path(..., gt=0, description="ID del grupo de reglas"),
) -> None:
    try:
        delete_geotab_rule_group(group_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
