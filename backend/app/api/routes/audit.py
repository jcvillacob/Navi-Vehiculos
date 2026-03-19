from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from app.core.dependencies import require_role
from app.schemas.auth import AuditLogRecord
from app.services.auth_service import list_audit_logs

router = APIRouter(prefix="/audit", tags=["audit"])


@router.get("", response_model=list[AuditLogRecord])
def get_audit_logs(
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    _user: dict = Depends(require_role("admin")),
) -> list[dict]:
    return list_audit_logs(limit=limit, offset=offset)
