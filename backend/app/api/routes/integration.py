from fastapi import APIRouter, Depends, HTTPException, Query

from app.core.dependencies import require_integration_key
from app.services.integration_export import (
    CloudFleetAuthError,
    CloudFleetUnavailableError,
    build_snapshot,
    export_availability,
    export_customers,
    export_taller_ordenes,
    export_vehicles,
)

router = APIRouter(
    prefix="/integration",
    tags=["integration"],
    dependencies=[Depends(require_integration_key)],
)


@router.get(
    "/snapshot",
    description=(
        "Snapshot completo o incremental (since) de clientes, databases, "
        "credenciales, reglas y vehiculos para Portal Clientes"
    ),
)
def get_snapshot(
    since: str | None = Query(
        default=None, description="ISO-8601: solo registros con updated_at posterior"
    ),
    include_credentials: bool = Query(
        default=False,
        description="Incluir username/password reales (solo para jobs server-side)",
    ),
) -> dict:
    try:
        return build_snapshot(since=since, include_credentials=include_credentials)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/vehicles", description="Vehiculos paginados para Portal Clientes")
def get_vehicles(
    since: str | None = Query(default=None, description="ISO-8601 incremental"),
    limit: int = Query(default=500, ge=1, le=2000),
    offset: int = Query(default=0, ge=0),
) -> dict:
    try:
        return export_vehicles(since=since, limit=limit, offset=offset)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get(
    "/availability",
    description=(
        "Disponibilidad mensual y MTTR por vehiculo para Portal Clientes"
    ),
)
def get_availability(
    month_from: str = Query(description="Mes inicial YYYY-MM (inclusive)"),
    month_to: str = Query(description="Mes final YYYY-MM (inclusive)"),
    since: str | None = Query(default=None, description="ISO-8601 incremental"),
    limit: int = Query(default=500, ge=1, le=2000),
    offset: int = Query(default=0, ge=0),
) -> dict:
    try:
        return export_availability(
            month_from=month_from,
            month_to=month_to,
            since=since,
            limit=limit,
            offset=offset,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get(
    "/customers",
    description="Clientes con databases, credenciales y reglas para Portal Clientes",
)
def get_customers(
    since: str | None = Query(default=None, description="ISO-8601 incremental"),
    include_credentials: bool = Query(default=False),
) -> dict:
    try:
        return export_customers(since=since, include_credentials=include_credentials)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get(
    "/taller-ordenes",
    description="Ordenes de taller activas en CloudFleet para Portal Clientes",
)
def get_taller_ordenes(
    customer_id: int | None = Query(
        default=None, gt=0, description="Filtrar por id de cliente"
    ),
    force_refresh: bool = Query(
        default=False, description="Forzar descarga desde CloudFleet"
    ),
) -> dict:
    try:
        return export_taller_ordenes(
            customer_id=customer_id, force_refresh=force_refresh
        )
    except (CloudFleetAuthError, CloudFleetUnavailableError, RuntimeError) as exc:
        raise HTTPException(
            status_code=503,
            detail=f"CloudFleet no disponible: {exc}",
        ) from exc
