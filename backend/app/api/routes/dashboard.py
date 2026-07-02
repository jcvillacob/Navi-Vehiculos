from datetime import datetime
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends

from app.core.dependencies import require_permission
from app.schemas.vehicle import (
    DashboardAvailabilityBlock,
    DashboardAvailabilityTrendBlock,
    DashboardLastJobBlock,
    DashboardSummary,
    DashboardSummaryV2,
    DashboardTallerBlock,
)
from app.services import geotab_taller, rendimientos_jobs
from app.services.availability_dashboard import (
    get_availability_overview,
    get_availability_trend,
)
from app.services.motor_catalog import get_dashboard_summary

router = APIRouter(prefix="/dashboard", tags=["dashboard"])

_BOGOTA = ZoneInfo("America/Bogota")


def _current_month_bogota() -> str:
    return datetime.now(_BOGOTA).strftime("%Y-%m")


def _build_availability_block(month: str) -> DashboardAvailabilityBlock:
    try:
        data = get_availability_overview(month)
    except Exception:
        return DashboardAvailabilityBlock(month=month)
    overall = data.get("overall") or {}
    pct = overall.get("availability_pct")
    return DashboardAvailabilityBlock(
        month=month,
        availability_pct=pct,
        fleet_count=int(overall.get("fleet_count") or 0),
        critical_fleets=int(overall.get("critical_fleets") or 0),
        vehicle_count=int(overall.get("vehicle_count") or 0),
        has_data=bool(overall.get("vehicle_count") and pct is not None),
    )


def _build_taller_block() -> DashboardTallerBlock:
    try:
        snapshot, _etag = geotab_taller.get_mapa_snapshot_cacheable()
    except Exception:
        return DashboardTallerBlock()
    vehicles = snapshot.get("vehicles") or []
    if not vehicles:
        return DashboardTallerBlock(generated_at=snapshot.get("generated_at"))
    sorted_v = sorted(vehicles, key=lambda v: v.get("minutes_inside") or 0, reverse=True)
    return DashboardTallerBlock(
        vehicles_in_taller=len(sorted_v),
        oldest_minutes=int(sorted_v[0].get("minutes_inside") or 0),
        top_plates=[v["plate"] for v in sorted_v[:3] if v.get("plate")],
        generated_at=snapshot.get("generated_at"),
    )


def _build_last_job_block() -> DashboardLastJobBlock:
    try:
        jobs = rendimientos_jobs.list_recent_jobs(limit=1)
    except Exception:
        return DashboardLastJobBlock()
    if not jobs:
        return DashboardLastJobBlock()
    job = jobs[0]
    return DashboardLastJobBlock(
        job_id=getattr(job, "id", None),
        month=getattr(job, "month", None),
        status=getattr(job, "status", None),
        created_at=getattr(job, "created_at", None),
        finished_at=getattr(job, "finished_at", None),
    )


def _build_trend_block(month_to: str, months: int = 6) -> DashboardAvailabilityTrendBlock:
    try:
        data = get_availability_trend(month_to, months=months)
    except Exception:
        return DashboardAvailabilityTrendBlock(
            month_from="", month_to=month_to, labels=[], availability_pct=[]
        )
    return DashboardAvailabilityTrendBlock(
        month_from=data.get("month_from") or "",
        month_to=data.get("month_to") or month_to,
        labels=list(data.get("labels") or []),
        availability_pct=list(data.get("availability_pct") or []),
    )


@router.get("/summary", response_model=DashboardSummary)
def dashboard_summary(_user: dict = Depends(require_permission("dashboard.view"))) -> DashboardSummary:
    return get_dashboard_summary()


@router.get("/summary/v2", response_model=DashboardSummaryV2)
def dashboard_summary_v2(
    _user: dict = Depends(require_permission("dashboard.view")),
) -> DashboardSummaryV2:
    base = get_dashboard_summary()
    current_month = _current_month_bogota()
    trend_month_to = current_month
    trend = _build_trend_block(trend_month_to, months=6)
    return DashboardSummaryV2(
        motors_count=base.motors_count,
        vehicles_count=base.vehicles_count,
        vehicles_without_motor=base.vehicles_without_motor,
        customers_count=base.customers_count,
        databases_count=base.databases_count,
        recent_motors=base.recent_motors,
        recent_vehicles=base.recent_vehicles,
        current_month=current_month,
        availability=_build_availability_block(current_month),
        taller=_build_taller_block(),
        last_rendimientos_job=_build_last_job_block(),
        availability_trend=trend,
    )
