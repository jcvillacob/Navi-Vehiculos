from __future__ import annotations

import re

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from app.core.dependencies import require_permission
from app.services.auth_service import (
    DuplicateRoleError,
    RoleInUseError,
    RoleNotFoundError,
    SystemRoleError,
    clear_role_permissions_cache,
    create_role,
    delete_role,
    get_role,
    get_role_permissions,
    list_roles,
    replace_role_permissions,
    update_role,
    write_audit_log,
)
from app.services.module_registry import (
    is_valid_level,
    is_valid_module,
    level_for_role,
    modules_catalog,
)


router = APIRouter(prefix="/roles", tags=["roles"])


# ── Schemas ──────────────────────────────────────────────────────────────────


class RoleRecord(BaseModel):
    key: str
    label: str
    description: str | None = None
    is_system: bool
    user_count: int = 0
    created_at: str | None = None
    updated_at: str | None = None


class RoleCreateRequest(BaseModel):
    key: str | None = Field(
        default=None,
        description="Slug estable (letras/numeros/_-). Si vacio, se genera del label.",
    )
    label: str = Field(..., min_length=1)
    description: str | None = None


class RoleUpdateRequest(BaseModel):
    label: str | None = None
    description: str | None = None


class RolePermissionsResponse(BaseModel):
    key: str
    is_system: bool
    modules: dict[str, str] = Field(
        default_factory=dict,
        description="Mapa {modulo: nivel} con valores 'ninguno' | 'lectura' | 'escritura'",
    )
    codenames: list[str] = Field(default_factory=list)


class RolePermissionsUpdateRequest(BaseModel):
    modules: dict[str, str] = Field(
        default_factory=dict,
        description="Mapa {modulo: nivel} a aplicar.",
    )


# ── Helpers ──────────────────────────────────────────────────────────────────


_SLUG_RE = re.compile(r"[^a-z0-9_-]+")


def _slugify(value: str) -> str:
    base = (value or "").strip().lower()
    slug = _SLUG_RE.sub("_", base).strip("_")
    return slug


def _role_to_dict(role: dict | None) -> dict | None:
    if not role:
        return None
    return {
        "key": role["key"],
        "label": role["label"],
        "description": role.get("description"),
        "is_system": role["is_system"],
        "user_count": int(role.get("user_count") or 0),
        "created_at": role["created_at"].isoformat() if role.get("created_at") else None,
        "updated_at": role["updated_at"].isoformat() if role.get("updated_at") else None,
    }


def _validate_matrix(matrix: dict[str, str]) -> None:
    """Valida que todos los modulos/niveles de la matriz sean validos."""
    for module, level in matrix.items():
        if not is_valid_module(module):
            raise HTTPException(
                status_code=422,
                detail=f"Modulo invalido: '{module}'.",
            )
        if not is_valid_level(module, level):
            raise HTTPException(
                status_code=422,
                detail=f"Nivel invalido '{level}' para el modulo '{module}'.",
            )


# ── Endpoints ────────────────────────────────────────────────────────────────


@router.get("/modules")
def list_modules(
    _user: dict = Depends(require_permission("users.list")),
) -> dict:
    """Catalogo de modulos y niveles para construir la UI de la matriz."""
    return {"modules": modules_catalog()}


@router.get("", response_model=list[RoleRecord])
def list_roles_route(
    _user: dict = Depends(require_permission("users.list")),
) -> list[dict]:
    return [_role_to_dict(r) for r in list_roles() if r is not None]


@router.post("", response_model=RoleRecord, status_code=201)
def create_role_route(
    request: Request,
    payload: RoleCreateRequest,
    user: dict = Depends(require_permission("roles.manage")),
) -> dict:
    key = (payload.key or _slugify(payload.label)).strip().lower()
    if not key:
        raise HTTPException(status_code=422, detail="La key del rol es obligatoria.")
    try:
        role = create_role(key=key, label=payload.label, description=payload.description)
    except DuplicateRoleError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    write_audit_log(
        user_id=user["id"],
        action="ROLE_CREATE",
        resource_type="role",
        resource_id=role["key"],
        detail={"label": role["label"]},
        ip_address=request.client.host if request.client else None,
    )
    return _role_to_dict({**role, "user_count": 0})


@router.get("/{key}", response_model=RoleRecord)
def get_role_route(
    key: str,
    _user: dict = Depends(require_permission("users.list")),
) -> dict:
    role = get_role(key)
    if not role:
        raise HTTPException(status_code=404, detail="Rol no encontrado")
    role = {**role, "user_count": 0}
    for r in list_roles():
        if r["key"] == key:
            role["user_count"] = int(r.get("user_count") or 0)
            break
    return _role_to_dict(role)


@router.put("/{key}", response_model=RoleRecord)
def update_role_route(
    request: Request,
    key: str,
    payload: RoleUpdateRequest,
    user: dict = Depends(require_permission("roles.manage")),
) -> dict:
    try:
        role = update_role(key, payload.label, payload.description)
    except RoleNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    write_audit_log(
        user_id=user["id"],
        action="ROLE_UPDATE",
        resource_type="role",
        resource_id=key,
        detail={
            "label": payload.label,
            "description_changed": payload.description is not None,
        },
        ip_address=request.client.host if request.client else None,
    )

    user_count = 0
    for r in list_roles():
        if r["key"] == key:
            user_count = int(r.get("user_count") or 0)
            break
    return _role_to_dict({**role, "user_count": user_count})


@router.delete("/{key}")
def delete_role_route(
    request: Request,
    key: str,
    user: dict = Depends(require_permission("roles.manage")),
) -> dict:
    try:
        delete_role(key)
    except RoleNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except SystemRoleError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except RoleInUseError as exc:
        raise HTTPException(status_code=409, detail=str(exc))

    write_audit_log(
        user_id=user["id"],
        action="ROLE_DELETE",
        resource_type="role",
        resource_id=key,
        detail={},
        ip_address=request.client.host if request.client else None,
    )
    return {"ok": True}


@router.get("/{key}/permissions", response_model=RolePermissionsResponse)
def get_role_permissions_route(
    key: str,
    _user: dict = Depends(require_permission("users.list")),
) -> dict:
    role = get_role(key)
    if not role:
        raise HTTPException(status_code=404, detail="Rol no encontrado")
    codenames = get_role_permissions(key)
    return {
        "key": role["key"],
        "is_system": role["is_system"],
        "modules": level_for_role(codenames),
        "codenames": sorted(codenames),
    }


@router.put("/{key}/permissions", response_model=RolePermissionsResponse)
def update_role_permissions_route(
    request: Request,
    key: str,
    payload: RolePermissionsUpdateRequest,
    user: dict = Depends(require_permission("roles.manage")),
) -> dict:
    _validate_matrix(payload.modules)
    try:
        codenames = replace_role_permissions(key, payload.modules)
    except RoleNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    write_audit_log(
        user_id=user["id"],
        action="ROLE_PERMISSIONS_UPDATE",
        resource_type="role",
        resource_id=key,
        detail={"matrix": payload.modules, "codenames": sorted(codenames)},
        ip_address=request.client.host if request.client else None,
    )

    # Re-leemos para devolver el estado canónico.
    role = get_role(key)
    return {
        "key": role["key"],
        "is_system": role["is_system"],
        "modules": level_for_role(codenames),
        "codenames": sorted(codenames),
    }


# Re-export para que `clear_role_permissions_cache` se pueda usar desde
# el modulo (util en tests).
__all__ = [
    "router",
    "clear_role_permissions_cache",
]
