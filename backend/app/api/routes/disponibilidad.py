from fastapi import APIRouter, Depends, Query

from app.core.dependencies import require_permission
from app.schemas.vehicle import (
    AvailabilityCoverageResponse,
    AvailabilityOverviewResponse,
    AvailabilityRankingItem,
    AvailabilityRankingResponse,
    AvailabilityTrendResponse,
)
from app.services.availability_dashboard import (
    get_availability_overview,
    get_availability_trend,
    get_cloudfleet_coverage,
    get_vehicle_ranking,
)

router = APIRouter(prefix="/disponibilidad", tags=["disponibilidad"])

_MONTH_PATTERN = r"^\d{4}-\d{2}$"


@router.get("/overview", response_model=AvailabilityOverviewResponse)
def get_overview(
    month: str = Query(..., pattern=_MONTH_PATTERN, description="Mes YYYY-MM"),
    _user: dict = Depends(require_permission("rendimientos.view")),
) -> AvailabilityOverviewResponse:
    """Resumen agregado del mes: disponibilidad global + por flota (cliente)."""
    data = get_availability_overview(month)
    return AvailabilityOverviewResponse(**data)


@router.get("/ranking", response_model=AvailabilityRankingResponse)
def get_ranking(
    month: str = Query(..., pattern=_MONTH_PATTERN, description="Mes YYYY-MM"),
    customer_id: int | None = Query(default=None, gt=0, description="Filtrar por flota"),
    limit: int = Query(default=20, ge=1, le=200),
    order: str = Query(default="worst", pattern=r"^(worst|best)$"),
    include_no_orders: bool = Query(default=False, description="Incluir placas sin ordenes (100%)"),
    plate_search: str | None = Query(default=None, max_length=32, description="Filtra el ranking por placa (substring)"),
    _user: dict = Depends(require_permission("rendimientos.view")),
) -> AvailabilityRankingResponse:
    """Ranking de vehiculos por disponibilidad (peor o mejor estado)."""
    items = get_vehicle_ranking(
        month,
        customer_id=customer_id,
        limit=limit,
        order=order,
        include_no_orders=include_no_orders,
        plate_search=plate_search,
    )
    return AvailabilityRankingResponse(
        month=month,
        customer_id=customer_id,
        order=order,
        items=[AvailabilityRankingItem(**item) for item in items],
    )


@router.get("/trend", response_model=AvailabilityTrendResponse)
def get_trend(
    month_to: str = Query(..., pattern=_MONTH_PATTERN, description="Mes final YYYY-MM"),
    months: int = Query(default=6, ge=1, le=24),
    customer_id: int | None = Query(default=None, gt=0, description="Filtrar por flota"),
    _user: dict = Depends(require_permission("rendimientos.view")),
) -> AvailabilityTrendResponse:
    """Serie temporal de disponibilidad (global o de una flota)."""
    data = get_availability_trend(month_to, months=months, customer_id=customer_id)
    return AvailabilityTrendResponse(**data)


@router.get("/coverage", response_model=AvailabilityCoverageResponse)
def get_coverage(
    month: str = Query(..., pattern=_MONTH_PATTERN, description="Mes YYYY-MM"),
    _user: dict = Depends(require_permission("rendimientos.view")),
) -> AvailabilityCoverageResponse:
    """Reconciliacion de cobertura CloudFleet: placas cubiertas vs no cubiertas."""
    data = get_cloudfleet_coverage(month)
    return AvailabilityCoverageResponse(**data)
