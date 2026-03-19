from __future__ import annotations

from fastapi import Cookie, Depends, HTTPException
from jose import JWTError, jwt

from app.core.config import settings
from app.services.auth_service import get_user_by_id, is_token_blacklisted


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


def require_role(*roles: str):
    """
    Factory que retorna un Depends verificando que el usuario tenga uno de los roles dados.

    Uso: Depends(require_role("admin", "editor"))
    """
    def check(user: dict = Depends(get_current_user)) -> dict:
        if user["role"] not in roles:
            raise HTTPException(status_code=403, detail="Sin permisos suficientes")
        return user
    return check
