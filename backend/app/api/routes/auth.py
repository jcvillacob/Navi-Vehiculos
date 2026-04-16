from __future__ import annotations

import uuid
from datetime import datetime, timezone, timedelta

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from jose import jwt, JWTError

from app.core.config import settings
from app.core.dependencies import get_current_user
from app.schemas.auth import LoginRequest, PermissionListResponse, UserRecord
from app.services.auth_service import (
    blacklist_token,
    build_user_payload,
    get_user_by_username,
    get_user_permissions,
    verify_password,
)

router = APIRouter(prefix="/auth", tags=["auth"])

_COOKIE_NAME = "access_token"


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
def login(payload: LoginRequest, response: Response) -> dict:
    user = get_user_by_username(payload.username)
    if not user or not verify_password(payload.password, user["password_hash"]):
        raise HTTPException(status_code=401, detail="Credenciales incorrectas")
    if not user["is_active"]:
        raise HTTPException(status_code=403, detail="Usuario inactivo")

    token = _build_token(user)
    response.set_cookie(
        key=_COOKIE_NAME,
        value=token,
        httponly=True,
        samesite="lax",
        secure=False,  # True en produccion con HTTPS
        max_age=settings.jwt_expire_minutes * 60,
        path="/",
    )
    return build_user_payload(user)


@router.post("/logout")
def logout(request: Request, response: Response, _user: dict = Depends(get_current_user)) -> dict:
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

    response.delete_cookie(key=_COOKIE_NAME, path="/")
    return {"ok": True}


@router.get("/me", response_model=UserRecord)
def me(user: dict = Depends(get_current_user)) -> dict:
    return build_user_payload(user)


@router.get("/permissions", response_model=PermissionListResponse)
def permissions(user: dict = Depends(get_current_user)) -> dict:
    return {"permissions": sorted(get_user_permissions(user["role"]))}
