from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from psycopg.errors import UniqueViolation

from app.core.dependencies import require_permission
from app.schemas.auth import ResetPasswordRequest, UserCreateRequest, UserRecord, UserUpdateRequest
from app.services.auth_service import (
    build_user_payload,
    create_user,
    get_user_by_id,
    list_users,
    list_valid_role_keys,
    revoke_all_refresh_tokens,
    update_user,
    update_user_password,
    validate_password_strength,
    write_audit_log,
)

router = APIRouter(prefix="/users", tags=["users"])


def _ensure_valid_role(role: str) -> None:
    valid = list_valid_role_keys()
    if role not in valid:
        raise HTTPException(
            status_code=422,
            detail=f"Rol invalido. Debe ser uno de: {', '.join(sorted(valid))}",
        )


@router.get("", response_model=list[UserRecord])
def list_users_route(
    _user: dict = Depends(require_permission("users.list")),
) -> list[dict]:
    return [build_user_payload(user) for user in list_users()]


@router.post("", response_model=UserRecord, status_code=201)
def create_user_route(
    payload: UserCreateRequest,
    _user: dict = Depends(require_permission("users.create")),
) -> dict:
    _ensure_valid_role(payload.role)
    try:
        user = create_user(
            username=payload.username,
            email=payload.email,
            password=payload.password,
            role=payload.role,
        )
        return build_user_payload(user)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except UniqueViolation:
        raise HTTPException(status_code=409, detail="El usuario o email ya existe")
    except Exception as exc:
        if "unique" in str(exc).lower():
            raise HTTPException(status_code=409, detail="El usuario o email ya existe")
        raise


@router.put("/{user_id}", response_model=UserRecord)
def update_user_route(
    user_id: int,
    payload: UserUpdateRequest,
    _user: dict = Depends(require_permission("users.edit")),
) -> dict:
    if payload.role is not None:
        _ensure_valid_role(payload.role)
    row = update_user(user_id, payload.email, payload.role, payload.is_active)
    if not row:
        raise HTTPException(status_code=404, detail="Usuario no encontrado")
    return build_user_payload(row)


@router.post("/{user_id}/reset-password")
def reset_password_route(
    request: Request,
    user_id: int,
    payload: ResetPasswordRequest,
    admin_user: dict = Depends(require_permission("users.edit")),
) -> dict:
    target_user = get_user_by_id(user_id)
    if not target_user:
        raise HTTPException(status_code=404, detail="Usuario no encontrado")

    errors = validate_password_strength(payload.new_password, target_user["username"])
    if errors:
        raise HTTPException(status_code=422, detail=errors)

    updated_user = update_user_password(user_id, payload.new_password)
    if not updated_user:
        raise HTTPException(status_code=404, detail="Usuario no encontrado")

    revoke_all_refresh_tokens(user_id)
    write_audit_log(
        user_id=admin_user["id"],
        action="PASSWORD_RESET",
        resource_type="user",
        resource_id=str(user_id),
        detail={"target_username": target_user["username"]},
        ip_address=request.client.host if request.client else None,
    )
    return {"ok": True}
