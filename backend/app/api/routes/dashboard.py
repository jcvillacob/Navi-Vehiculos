from fastapi import APIRouter

from app.schemas.vehicle import DashboardSummary
from app.services.motor_catalog import get_dashboard_summary

router = APIRouter(prefix="/dashboard", tags=["dashboard"])


@router.get("/summary", response_model=DashboardSummary)
def dashboard_summary() -> DashboardSummary:
    return get_dashboard_summary()
