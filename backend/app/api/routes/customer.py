from fastapi import APIRouter, Depends, HTTPException, Path, Query, status

from app.core.dependencies import require_permission
from app.schemas.vehicle import (
    CustomerActiveUpdateRequest,
    CustomerCreateRequest,
    CustomerDatabaseCreateRequest,
    CustomerDatabaseCredentialCreateRequest,
    CustomerDatabaseCredentialRecord,
    CustomerDatabaseCredentialUpdateRequest,
    CustomerDatabaseRecord,
    CustomerDatabaseUpdateRequest,
    CustomerRecord,
    CustomerUpdateRequest,
    GeotabRuleApplicationUpdateRequest,
    GeotabRuleGroupCreateRequest,
    GeotabRuleGroupRecord,
    GeotabRuleCreateRequest,
    GeotabRuleInspection,
    GeotabRuleRecord,
)
from app.services.motor_catalog import (
    create_customer,
    create_customer_database,
    create_database_credential,
    create_geotab_rule_group,
    create_geotab_rule,
    delete_database_credential,
    delete_geotab_rule_group,
    delete_geotab_rule,
    inspect_geotab_rule_record,
    list_customers,
    list_database_credentials,
    resolve_geotab_rule,
    set_customer_active,
    update_customer,
    update_customer_database,
    update_database_credential,
    update_geotab_rule_application,
)

router = APIRouter(prefix="/customers", tags=["customers"])


@router.get("", response_model=list[CustomerRecord])
def get_customers(_user: dict = Depends(require_permission("customers.list"))) -> list[CustomerRecord]:
    return list_customers()


@router.post("", response_model=CustomerRecord, status_code=status.HTTP_201_CREATED)
def create_customer_record(
    payload: CustomerCreateRequest,
    _user: dict = Depends(require_permission("customers.create")),
) -> CustomerRecord:
    try:
        return create_customer(payload)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.put("/{customer_id}/active", response_model=CustomerRecord)
def set_customer_active_record(
    payload: CustomerActiveUpdateRequest,
    customer_id: int = Path(..., gt=0, description="ID del cliente"),
    _user: dict = Depends(require_permission("customers.edit")),
) -> CustomerRecord:
    try:
        return set_customer_active(customer_id, payload.is_active)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/{customer_id}/databases", response_model=CustomerDatabaseRecord, status_code=status.HTTP_201_CREATED)
def create_database_record(
    payload: CustomerDatabaseCreateRequest,
    customer_id: int = Path(..., gt=0, description="ID del cliente"),
    _user: dict = Depends(require_permission("customers.edit")),
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
    _user: dict = Depends(require_permission("customers.edit")),
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
    _user: dict = Depends(require_permission("customers.edit")),
) -> CustomerDatabaseRecord:
    try:
        return update_customer_database(database_id, payload)
    except ValueError as exc:
        status_code = 404 if "no existe" in str(exc).lower() else 409
        raise HTTPException(status_code=status_code, detail=str(exc)) from exc


@router.get(
    "/databases/{database_id}/credentials",
    response_model=list[CustomerDatabaseCredentialRecord],
)
def list_credentials(
    database_id: int = Path(..., gt=0, description="ID de la database"),
    _user: dict = Depends(require_permission("customers.list")),
) -> list[CustomerDatabaseCredentialRecord]:
    try:
        return list_database_credentials(database_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post(
    "/databases/{database_id}/credentials",
    response_model=CustomerDatabaseCredentialRecord,
    status_code=status.HTTP_201_CREATED,
)
def create_credential_record(
    payload: CustomerDatabaseCredentialCreateRequest,
    database_id: int = Path(..., gt=0, description="ID de la database"),
    _user: dict = Depends(require_permission("customers.edit")),
) -> CustomerDatabaseCredentialRecord:
    try:
        return create_database_credential(database_id, payload)
    except ValueError as exc:
        status_code = 404 if "no existe" in str(exc).lower() else 409
        raise HTTPException(status_code=status_code, detail=str(exc)) from exc


@router.put(
    "/databases/credentials/{credential_id}",
    response_model=CustomerDatabaseCredentialRecord,
)
def update_credential_record(
    payload: CustomerDatabaseCredentialUpdateRequest,
    credential_id: int = Path(..., gt=0, description="ID de la credencial"),
    _user: dict = Depends(require_permission("customers.edit")),
) -> CustomerDatabaseCredentialRecord:
    try:
        return update_database_credential(credential_id, payload)
    except ValueError as exc:
        status_code = 404 if "no existe" in str(exc).lower() else 409
        raise HTTPException(status_code=status_code, detail=str(exc)) from exc


@router.delete(
    "/databases/credentials/{credential_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def delete_credential_record(
    credential_id: int = Path(..., gt=0, description="ID de la credencial"),
    _user: dict = Depends(require_permission("customers.edit")),
) -> None:
    try:
        delete_database_credential(credential_id)
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
    _user: dict = Depends(require_permission("customers.edit")),
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
    _user: dict = Depends(require_permission("customers.edit")),
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
    _user: dict = Depends(require_permission("customers.list")),
) -> GeotabRuleInspection:
    try:
        return resolve_geotab_rule(database_id, rule_id)
    except ValueError as exc:
        status_code = 404 if "no existe" in str(exc).lower() else 409
        raise HTTPException(status_code=status_code, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.patch(
    "/rules/applications/{application_id}",
    response_model=GeotabRuleRecord,
)
def update_rule_application_record(
    payload: GeotabRuleApplicationUpdateRequest,
    application_id: int = Path(..., gt=0, description="ID de la aplicacion de la regla"),
    _user: dict = Depends(require_permission("customers.edit")),
) -> GeotabRuleRecord:
    try:
        return update_geotab_rule_application(application_id, payload)
    except ValueError as exc:
        status_code = 404 if "no existe" in str(exc).lower() else 409
        raise HTTPException(status_code=status_code, detail=str(exc)) from exc


@router.get("/rules/{rule_id}/inspection", response_model=GeotabRuleInspection)
def inspect_rule_record(
    rule_id: int = Path(..., gt=0, description="ID del registro local de la regla"),
    _user: dict = Depends(require_permission("customers.list")),
) -> GeotabRuleInspection:
    try:
        return inspect_geotab_rule_record(rule_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.delete("/rules/{rule_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_rule_record(
    rule_id: int = Path(..., gt=0, description="ID de la regla"),
    _user: dict = Depends(require_permission("customers.edit")),
) -> None:
    try:
        delete_geotab_rule(rule_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.delete("/rule-groups/{group_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_rule_group_record(
    group_id: int = Path(..., gt=0, description="ID del grupo de reglas"),
    _user: dict = Depends(require_permission("customers.edit")),
) -> None:
    try:
        delete_geotab_rule_group(group_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
