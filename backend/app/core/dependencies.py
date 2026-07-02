from __future__ import annotations

import os
import secrets

from fastapi import Cookie, Depends, Header, HTTPException, Query, Request
from jose import JWTError, jwt

from app.core.config import settings
from app.core.security_logging import log_security_event
from app.services.auth_service import get_user_by_id, get_user_permissions, is_token_blacklisted


def get_current_user(access_token: str | None = Cookie(default=None)) -> dict:
    """
    Lee el JWT desde la cookie httpOnly 'access_token'.
    Retorna el usuario de la DB. Lanza 401 ante cualquier falla.
    """
    if not access_token:
        raise HTTPException(status_code=401, detail="No autenticado")
    try:
        payload = jwt.decode(
            access_token,
            settings.jwt_secret,
            algorithms=[settings.jwt_algorithm],
        )
        user_id: int = int(payload["sub"])
        jti: str = payload["jti"]
    except (JWTError, KeyError, ValueError):
        raise HTTPException(status_code=401, detail="Token invalido")

    if is_token_blacklisted(jti):
        raise HTTPException(status_code=401, detail="Sesion cerrada")

    user = get_user_by_id(user_id)
    if not user or not user["is_active"]:
        raise HTTPException(status_code=401, detail="Usuario inactivo o no encontrado")

    return user


def get_current_user_optional(access_token: str | None = Cookie(default=None)) -> dict | None:
    if not access_token:
        return None
    try:
        return get_current_user(access_token)
    except HTTPException:
        return None


def _integration_api_keys() -> tuple[str, ...]:
    raw = os.getenv("INTEGRATION_API_KEYS", "")
    return tuple(key.strip() for key in raw.split(",") if key.strip())


def _geotab_webhook_api_keys() -> tuple[str, ...]:
    raw = os.getenv("GEOTAB_WEBHOOK_API_KEYS", "")
    return tuple(key.strip() for key in raw.split(",") if key.strip())


def require_geotab_webhook_key(
    request: Request,
    api_key_header: str | None = Header(default=None, alias="X-API-Key"),
    api_key: str | None = Query(default=None, description="API key del webhook (?api_key=...)"),
) -> None:
    """
    Autenticacion del webhook de Geotab por API key.

    Geotab envia la credencial como `?api_key=...` (el header no es configurable
    en su UI), pero aceptamos ambos canales (header y query) para que tests
    internos y reenvios proxied puedan usar el header.

    La lista de claves validas se lee de `GEOTAB_WEBHOOK_API_KEYS` (comma-
    separated, rotacion). Comparacion con `secrets.compare_digest`.
    """
    valid_keys = _geotab_webhook_api_keys()
    if not valid_keys:
        raise HTTPException(status_code=503, detail="Webhook Geotab no configurado")
    provided = (api_key or api_key_header or "").strip()
    if not provided or not any(
        secrets.compare_digest(provided, valid_key) for valid_key in valid_keys
    ):
        log_security_event(
            "geotab_webhook_key_denied",
            user_id=None,
            endpoint=request.url.path,
        )
        raise HTTPException(status_code=401, detail="API key invalida")


def require_integration_key(
    request: Request,
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
) -> None:
    """
    Autenticacion servicio-a-servicio por header X-API-Key contra
    INTEGRATION_API_KEYS (lista separada por comas, permite rotacion).
    """
    valid_keys = _integration_api_keys()
    if not valid_keys:
        raise HTTPException(status_code=503, detail="Integracion no configurada")
    provided = (x_api_key or "").strip()
    if not provided or not any(
        secrets.compare_digest(provided, valid_key) for valid_key in valid_keys
    ):
        log_security_event(
            "integration_key_denied",
            user_id=None,
            endpoint=request.url.path,
        )
        raise HTTPException(status_code=401, detail="API key invalida")


def require_permission(*perms: str):
    """
    Factory que valida que el usuario tenga al menos uno de los permisos dados.
    """
    def check(request: Request, user: dict = Depends(get_current_user)) -> dict:
        user_permissions = get_user_permissions(user["role"])
        if not any(perm in user_permissions for perm in perms):
            log_security_event(
                "permission_denied",
                user_id=user["id"],
                endpoint=request.url.path,
                required_permissions=list(perms),
            )
            raise HTTPException(status_code=403, detail="Sin permisos suficientes")
        return user
    return check
