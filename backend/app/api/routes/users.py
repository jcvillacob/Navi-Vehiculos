from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from psycopg.errors import UniqueViolation

from app.core.dependencies import require_permission
from app.schemas.auth import UserCreateRequest, UserRecord, UserUpdateRequest
from app.services.auth_service import build_user_payload, create_user, list_users, update_user

router = APIRouter(prefix="/users", tags=["users"])


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
    valid_roles = {"admin", "editor", "viewer"}
    if payload.role not in valid_roles:
        raise HTTPException(status_code=422, detail=f"Rol invalido. Debe ser uno de: {', '.join(valid_roles)}")
    try:
        user = create_user(
            username=payload.username,
            email=payload.email,
            password=payload.password,
            role=payload.role,
        )
        return build_user_payload(user)
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
    if payload.role is not None and payload.role not in {"admin", "editor", "viewer"}:
        raise HTTPException(status_code=422, detail="Rol invalido")
    row = update_user(user_id, payload.email, payload.role, payload.is_active)
    if not row:
        raise HTTPException(status_code=404, detail="Usuario no encontrado")
    return build_user_payload(row)
