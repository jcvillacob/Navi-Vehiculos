from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.core.dependencies import require_permission
from app.schemas.cpk_cph import (
    CpkCphPreviewRequest,
    CpkCphPreviewResponse,
    CpkCphReportDetail,
    CpkCphReportListResponse,
    CpkCphReportSaveRequest,
    CpkCphRowPatchRequest,
)
from app.services.cpk_cph import (
    CpkCphConflict,
    CpkCphNotFound,
    approve_report,
    get_report,
    list_reports,
    preview_report,
    reopen_report,
    save_report,
    update_row,
)


router = APIRouter(prefix="/cpk-cph", tags=["cpk-cph"])


def _handle_service_error(exc: Exception) -> HTTPException:
    if isinstance(exc, CpkCphNotFound):
        return HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, CpkCphConflict):
        return HTTPException(status_code=409, detail=str(exc))
    return HTTPException(status_code=400, detail=str(exc))


@router.get("/reports", response_model=CpkCphReportListResponse)
def list_cpk_cph_reports(
    month: str | None = Query(default=None, pattern=r"^\d{4}-\d{2}$"),
    customer_id: int | None = Query(default=None, gt=0),
    _user: dict = Depends(require_permission("cpk_cph.view", "rendimientos.view")),
) -> CpkCphReportListResponse:
    return CpkCphReportListResponse(reports=list_reports(month=month, customer_id=customer_id))


@router.post("/reports/preview", response_model=CpkCphPreviewResponse)
def preview_cpk_cph_report(
    payload: CpkCphPreviewRequest,
    _user: dict = Depends(require_permission("cpk_cph.view", "rendimientos.view")),
) -> CpkCphPreviewResponse:
    try:
        return preview_report(payload)
    except Exception as exc:
        raise _handle_service_error(exc) from exc


@router.post("/reports", response_model=CpkCphReportDetail, status_code=status.HTTP_201_CREATED)
def save_cpk_cph_report(
    payload: CpkCphReportSaveRequest,
    user: dict = Depends(require_permission("cpk_cph.manage", "rendimientos.refresh")),
) -> CpkCphReportDetail:
    try:
        return save_report(payload, user_id=user.get("id"))
    except Exception as exc:
        raise _handle_service_error(exc) from exc


@router.get("/reports/{report_id}", response_model=CpkCphReportDetail)
def get_cpk_cph_report(
    report_id: int,
    _user: dict = Depends(require_permission("cpk_cph.view", "rendimientos.view")),
) -> CpkCphReportDetail:
    try:
        return get_report(report_id)
    except Exception as exc:
        raise _handle_service_error(exc) from exc


@router.patch("/reports/{report_id}/rows/{row_id}", response_model=CpkCphReportDetail)
def patch_cpk_cph_report_row(
    report_id: int,
    row_id: int,
    payload: CpkCphRowPatchRequest,
    user: dict = Depends(require_permission("cpk_cph.manage", "rendimientos.refresh")),
) -> CpkCphReportDetail:
    try:
        return update_row(report_id, row_id, payload, user_id=user.get("id"))
    except Exception as exc:
        raise _handle_service_error(exc) from exc


@router.post("/reports/{report_id}/approve", response_model=CpkCphReportDetail)
def approve_cpk_cph_report(
    report_id: int,
    user: dict = Depends(require_permission("cpk_cph.manage", "rendimientos.refresh")),
) -> CpkCphReportDetail:
    try:
        return approve_report(report_id, user_id=user.get("id"))
    except Exception as exc:
        raise _handle_service_error(exc) from exc


@router.post("/reports/{report_id}/reopen", response_model=CpkCphReportDetail)
def reopen_cpk_cph_report(
    report_id: int,
    user: dict = Depends(require_permission("cpk_cph.manage", "rendimientos.refresh")),
) -> CpkCphReportDetail:
    try:
        return reopen_report(report_id, user_id=user.get("id"))
    except Exception as exc:
        raise _handle_service_error(exc) from exc
