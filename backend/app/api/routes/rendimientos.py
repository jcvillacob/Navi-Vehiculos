from fastapi import APIRouter, Depends, HTTPException, Query

from app.core.dependencies import get_current_user, require_role
from app.schemas.vehicle import (
    MonthlyPerformanceCalculateRequest,
    MonthlyPerformanceResponse,
)
from app.services.rendimientos import calculate_monthly_performance, list_monthly_performance

router = APIRouter(prefix="/rendimientos", tags=["rendimientos"])


@router.get("", response_model=MonthlyPerformanceResponse)
def get_monthly_performance(
    month: str | None = Query(default=None, pattern=r"^\d{4}-\d{2}$", description="Mes unico (legacy)"),
    month_from: str | None = Query(default=None, pattern=r"^\d{4}-\d{2}$", description="Inicio del rango"),
    month_to: str | None = Query(default=None, pattern=r"^\d{4}-\d{2}$", description="Fin del rango"),
    customer_id: int | None = Query(default=None, gt=0, description="Filtro opcional por cliente"),
    customer_ids: list[int] | None = Query(
        default=None,
        description="Filtro opcional por varios clientes",
    ),
    customer_database_id: int | None = Query(
        default=None, gt=0, description="Filtro opcional por database"
    ),
    plate_search: str | None = Query(default=None, min_length=1, max_length=32),
    motor_group: str | None = Query(default=None, min_length=1, max_length=120),
    _user: dict = Depends(get_current_user),
) -> MonthlyPerformanceResponse:
    effective_from = month_from or month
    effective_to = month_to or month
    if not effective_from:
        raise HTTPException(status_code=400, detail="Se requiere month o month_from.")
    if not effective_to:
        effective_to = effective_from
    try:
        return list_monthly_performance(
            month_from=effective_from,
            month_to=effective_to,
            customer_id=customer_id,
            customer_ids=customer_ids,
            customer_database_id=customer_database_id,
            plate_search=plate_search,
            motor_group=motor_group,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/calculate", response_model=MonthlyPerformanceResponse)
def calculate_monthly_performance_route(
    payload: MonthlyPerformanceCalculateRequest,
    _user: dict = Depends(require_role("admin", "editor")),
) -> MonthlyPerformanceResponse:
    try:
        return calculate_monthly_performance(payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
