from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel

from app.core.dependencies import require_permission
from app.services.auth_service import write_audit_log
from app.services.backup_service import BackupError, create_postgres_backup, list_backups


router = APIRouter(prefix="/backups", tags=["backups"])


class BackupRecord(BaseModel):
    filename: str
    size_bytes: int
    created_at: str
    sha256: str
    trigger: str


@router.get("", response_model=list[BackupRecord])
def list_backups_route(
    _user: dict = Depends(require_permission("backups.list")),
) -> list[dict]:
    return list_backups()


@router.post("", response_model=BackupRecord, status_code=status.HTTP_201_CREATED)
def create_backup_route(
    request: Request,
    user: dict = Depends(require_permission("backups.create")),
) -> dict:
    try:
        record = create_postgres_backup(trigger="manual")
    except BackupError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    write_audit_log(
        user_id=user["id"],
        action="BACKUP_CREATE",
        resource_type="postgres_backup",
        resource_id=record["filename"],
        detail={
            "filename": record["filename"],
            "size_bytes": record["size_bytes"],
            "trigger": "manual",
        },
        ip_address=request.client.host if request.client else None,
    )
    return record
