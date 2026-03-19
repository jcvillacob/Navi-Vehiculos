from fastapi import APIRouter, Depends

from app.core.dependencies import get_current_user
from app.schemas.vehicle import DashboardSummary
from app.services.motor_catalog import get_dashboard_summary

router = APIRouter(prefix="/dashboard", tags=["dashboard"])


@router.get("/summary", response_model=DashboardSummary)
def dashboard_summary(_user: dict = Depends(get_current_user)) -> DashboardSummary:
    return get_dashboard_summary()
