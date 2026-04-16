from __future__ import annotations

import uuid
from datetime import datetime, timezone, timedelta

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from jose import jwt, JWTError

from app.core.config import settings
from app.core.dependencies import get_current_user, get_current_user_optional
from app.core.rate_limit import limiter
from app.schemas.auth import ChangePasswordRequest, LoginRequest, PermissionListResponse, UserRecord
from app.services.auth_service import (
    blacklist_token,
    build_user_payload,
    create_refresh_token,
    get_refresh_token_record,
    get_user_by_id,
    get_user_by_username,
    get_user_permissions,
    is_user_locked,
    record_failed_login,
    reset_login_state,
    revoke_all_refresh_tokens,
    revoke_refresh_token,
    update_user_password,
    validate_password_strength,
    verify_password,
    write_audit_log,
)

router = APIRouter(prefix="/auth", tags=["auth"])

_COOKIE_NAME = "access_token"
_REFRESH_COOKIE_NAME = "refresh_token"
_REFRESH_COOKIE_PATH = "/api/v1/auth/refresh"


def _cookie_settings() -> dict:
    is_production = settings.environment == "production"
    return {
        "httponly": True,
        "secure": is_production,
        "samesite": "strict" if is_production else "lax",
    }


def _set_access_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        key=_COOKIE_NAME,
        value=token,
        max_age=settings.jwt_expire_minutes * 60,
        path="/",
        **_cookie_settings(),
    )


def _set_refresh_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        key=_REFRESH_COOKIE_NAME,
        value=token,
        max_age=settings.refresh_token_expire_days * 24 * 60 * 60,
        path=_REFRESH_COOKIE_PATH,
        **_cookie_settings(),
    )


def _build_token(user: dict) -> str:
    now = datetime.now(tz=timezone.utc)
    expire = now + timedelta(minutes=settings.jwt_expire_minutes)
    payload = {
        "sub": str(user["id"]),
        "role": user["role"],
        "jti": str(uuid.uuid4()),
        "iat": int(now.timestamp()),
        "exp": int(expire.timestamp()),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


@router.post("/login")
@limiter.limit("5/minute")
def login(request: Request, payload: LoginRequest, response: Response) -> dict:
    user = get_user_by_username(payload.username)
    if user:
        locked, remaining = is_user_locked(user)
        if locked:
            minutes = max(1, remaining // 60)
            raise HTTPException(
                status_code=423,
                detail=f"Usuario bloqueado temporalmente. Intenta de nuevo en {minutes} minuto(s).",
            )

    if not user or not verify_password(payload.password, user["password_hash"]):
        if user:
            record_failed_login(user["id"])
        raise HTTPException(status_code=401, detail="Credenciales incorrectas")
    if not user["is_active"]:
        raise HTTPException(status_code=403, detail="Usuario inactivo")

    reset_login_state(user["id"])
    user = get_user_by_id(user["id"]) or user
    token = _build_token(user)
    refresh_token, _expires_at = create_refresh_token(user["id"])
    _set_access_cookie(response, token)
    _set_refresh_cookie(response, refresh_token)
    return build_user_payload(user)


@router.post("/refresh")
def refresh(request: Request, response: Response) -> dict:
    refresh_token = request.cookies.get(_REFRESH_COOKIE_NAME)
    if not refresh_token:
        raise HTTPException(status_code=401, detail="Refresh token ausente")

    record = get_refresh_token_record(refresh_token)
    if not record:
        raise HTTPException(status_code=401, detail="Refresh token invalido")
    if record["revoked"] or record["expires_at"] <= datetime.now(tz=timezone.utc):
        raise HTTPException(status_code=401, detail="Refresh token expirado o revocado")

    user = get_user_by_id(record["user_id"])
    if not user or not user["is_active"]:
        raise HTTPException(status_code=401, detail="Usuario inactivo o no encontrado")

    revoke_refresh_token(refresh_token)
    new_refresh_token, _expires_at = create_refresh_token(user["id"])
    access_token = _build_token(user)
    _set_access_cookie(response, access_token)
    _set_refresh_cookie(response, new_refresh_token)
    return build_user_payload(user)


@router.post("/logout")
def logout(request: Request, response: Response, _user: dict | None = Depends(get_current_user_optional)) -> dict:
    token = request.cookies.get(_COOKIE_NAME)
    if token:
        try:
            payload = jwt.decode(
                token,
                settings.jwt_secret,
                algorithms=[settings.jwt_algorithm],
            )
            jti = payload.get("jti")
            exp = payload.get("exp")
            if jti and exp:
                remaining = max(int(exp) - int(datetime.now(tz=timezone.utc).timestamp()), 1)
                blacklist_token(jti, remaining)
        except (JWTError, Exception):
            pass

    refresh_token = request.cookies.get(_REFRESH_COOKIE_NAME)
    if refresh_token:
        record = get_refresh_token_record(refresh_token)
        if record:
            revoke_all_refresh_tokens(record["user_id"])
    elif _user:
        revoke_all_refresh_tokens(_user["id"])

    response.delete_cookie(key=_COOKIE_NAME, path="/")
    response.delete_cookie(key=_REFRESH_COOKIE_NAME, path=_REFRESH_COOKIE_PATH)
    return {"ok": True}


@router.get("/me", response_model=UserRecord)
def me(user: dict = Depends(get_current_user)) -> dict:
    return build_user_payload(user)


@router.get("/permissions", response_model=PermissionListResponse)
def permissions(user: dict = Depends(get_current_user)) -> dict:
    return {"permissions": sorted(get_user_permissions(user["role"]))}


@router.put("/password")
def change_password(
    payload: ChangePasswordRequest,
    request: Request,
    response: Response,
    user: dict = Depends(get_current_user),
) -> dict:
    if not verify_password(payload.current_password, user["password_hash"]):
        raise HTTPException(status_code=401, detail="La contrasena actual es incorrecta")

    errors = validate_password_strength(payload.new_password, user["username"])
    if errors:
        raise HTTPException(status_code=422, detail=errors)

    updated_user = update_user_password(user["id"], payload.new_password)
    if not updated_user:
        raise HTTPException(status_code=404, detail="Usuario no encontrado")

    revoke_all_refresh_tokens(user["id"])
    refresh_token, _expires_at = create_refresh_token(user["id"])
    access_token = _build_token(updated_user)
    _set_access_cookie(response, access_token)
    _set_refresh_cookie(response, refresh_token)
    write_audit_log(
        user_id=user["id"],
        action="PASSWORD_CHANGE",
        resource_type="auth",
        resource_id=str(user["id"]),
        detail={"path": request.url.path},
        ip_address=request.client.host if request.client else None,
    )
    return {"ok": True}
