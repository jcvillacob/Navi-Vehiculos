from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Response, status

from app.core.dependencies import require_permission
from app.schemas.vehicle import (
    MonthlyPerformanceCalculateRequest,
    MonthlyPerformanceResponse,
    MonthlyVehicleAvailabilityResponse,
    MonthlyVehicleAvailabilityRow,
    PerformanceCalculationJob,
    PerformanceJobListResponse,
)
from app.services.availability_store import list_monthly_availability
from app.services.rendimientos import list_adhoc_filter_options, list_monthly_performance
from app.services.rendimientos_jobs import (
    JobAlreadyRunning,
    JobNotFound,
    cancel_job,
    create_job,
    get_job,
    list_active_jobs,
    list_recent_jobs,
    run_job,
)

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
    _user: dict = Depends(require_permission("rendimientos.view")),
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


@router.post(
    "/calculate",
    response_model=PerformanceCalculationJob,
    status_code=status.HTTP_202_ACCEPTED,
)
def calculate_monthly_performance_route(
    payload: MonthlyPerformanceCalculateRequest,
    background_tasks: BackgroundTasks,
    response: Response,
    user: dict = Depends(require_permission("rendimientos.refresh")),
) -> PerformanceCalculationJob:
    """
    Crea un job y lo dispara en background. Retorna 202 con los datos del job.
    Si ya hay un job activo para el mismo mes y conjunto de clientes, retorna 409
    con el job existente (mismo schema) para que el cliente lo siga.
    """
    try:
        job = create_job(payload, triggered_by="ui", user_id=user.get("id"))
    except JobAlreadyRunning as exc:
        response.status_code = status.HTTP_409_CONFLICT
        return exc.job
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    background_tasks.add_task(run_job, job.id)
    return job


@router.get("/adhoc-filters")
def get_adhoc_filter_options(
    _user: dict = Depends(require_permission("rendimientos.refresh")),
) -> dict:
    """
    Retorna las opciones de filtro disponibles para vehiculos sin cliente asignado.
    """
    return list_adhoc_filter_options()


@router.get("/jobs", response_model=PerformanceJobListResponse)
def list_performance_jobs(
    active: bool = Query(default=False, description="Solo jobs queued/running del usuario actual"),
    limit: int = Query(default=50, ge=1, le=200, description="Historial: ultimos N jobs"),
    user: dict = Depends(require_permission("rendimientos.view")),
) -> PerformanceJobListResponse:
    if active:
        jobs = list_active_jobs(user_id=user.get("id"))
    else:
        jobs = list_recent_jobs(limit=limit)
    return PerformanceJobListResponse(jobs=jobs)


@router.get("/jobs/{job_id}", response_model=PerformanceCalculationJob)
def get_performance_job(
    job_id: int,
    _user: dict = Depends(require_permission("rendimientos.view")),
) -> PerformanceCalculationJob:
    try:
        return get_job(job_id)
    except JobNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/jobs/{job_id}/cancel", response_model=PerformanceCalculationJob)
def cancel_performance_job(
    job_id: int,
    _user: dict = Depends(require_permission("rendimientos.refresh")),
) -> PerformanceCalculationJob:
    try:
        return cancel_job(job_id)
    except JobNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/availability", response_model=MonthlyVehicleAvailabilityResponse)
def get_monthly_availability(
    month_from: str = Query(..., pattern=r"^\d{4}-\d{2}$", description="Inicio del rango"),
    month_to: str = Query(..., pattern=r"^\d{4}-\d{2}$", description="Fin del rango"),
    _user: dict = Depends(require_permission("rendimientos.view")),
) -> MonthlyVehicleAvailabilityResponse:
    """
    Devuelve los registros persistidos de disponibilidad (CloudFleet) en el
    rango pedido. El front llama este endpoint despues de cargar los
    rendimientos para hacer merge en memoria por placa.
    """
    rows = list_monthly_availability(month_from=month_from, month_to=month_to)
    return MonthlyVehicleAvailabilityResponse(
        month_from=month_from,
        month_to=month_to,
        rows=[MonthlyVehicleAvailabilityRow(**row) for row in rows],
    )
