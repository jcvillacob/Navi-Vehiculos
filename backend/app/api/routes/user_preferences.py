from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request

from app.core.dependencies import get_current_user
from app.schemas.user_preferences import UserPreferenceResponse, UserPreferenceUpdate
from app.services import user_preferences
from app.services.auth_service import write_audit_log

router = APIRouter(prefix="/me/preferences", tags=["me"])


def _serialize(row: dict) -> dict:
    return {
        "key": row["key"],
        "value": row["value"],
        "updated_at": row["updated_at"],
    }


@router.get("", response_model=list[UserPreferenceResponse])
def list_my_preferences(
    user: dict = Depends(get_current_user),
) -> list[dict]:
    return [_serialize(row) for row in user_preferences.list_user_preferences(user["id"])]


@router.get("/{key}", response_model=UserPreferenceResponse)
def get_my_preference(
    key: str,
    user: dict = Depends(get_current_user),
) -> dict:
    row = user_preferences.get_user_preference(user["id"], key)
    if not row:
        raise HTTPException(status_code=404, detail="Preferencia no encontrada")
    return _serialize(row)


@router.put("/{key}", response_model=UserPreferenceResponse)
def upsert_my_preference(
    request: Request,
    key: str,
    payload: UserPreferenceUpdate,
    user: dict = Depends(get_current_user),
) -> dict:
    row = user_preferences.set_user_preference(user["id"], key, payload.value)
    write_audit_log(
        user_id=user["id"],
        action="PREFERENCE_UPDATE",
        resource_type="user_preference",
        resource_id=key,
        detail={"key": key},
        ip_address=request.client.host if request.client else None,
    )
    return _serialize(row)


@router.delete("/{key}")
def delete_my_preference(
    request: Request,
    key: str,
    user: dict = Depends(get_current_user),
) -> dict:
    deleted = user_preferences.delete_user_preference(user["id"], key)
    if not deleted:
        raise HTTPException(status_code=404, detail="Preferencia no encontrada")
    write_audit_log(
        user_id=user["id"],
        action="PREFERENCE_DELETE",
        resource_type="user_preference",
        resource_id=key,
        detail={"key": key},
        ip_address=request.client.host if request.client else None,
    )
    return {"ok": True}
