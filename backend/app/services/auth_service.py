from __future__ import annotations

import hashlib
import os
import re
import secrets
import uuid
from datetime import datetime, timedelta, timezone

import psycopg
from psycopg.rows import dict_row
import bcrypt
import redis as redis_lib

from app.core.config import settings


PERMISSIONS_BY_ROLE: dict[str, tuple[str, ...]] = {
    "admin": (
        "dashboard.view",
        "motors.list",
        "motors.create",
        "motors.edit",
        "motors.delete",
        "motors.attachments",
        "vehicles.list",
        "vehicles.edit",
        "vehicles.refresh",
        "customers.list",
        "customers.create",
        "customers.edit",
        "rendimientos.view",
        "rendimientos.refresh",
        "users.list",
        "users.create",
        "users.edit",
        "audit.view",
        "engine_lookup.use",
    ),
    "editor": (
        "dashboard.view",
        "motors.list",
        "motors.create",
        "motors.edit",
        "motors.attachments",
        "vehicles.list",
        "vehicles.edit",
        "vehicles.refresh",
        "customers.list",
        "customers.create",
        "customers.edit",
        "rendimientos.view",
        "rendimientos.refresh",
        "engine_lookup.use",
    ),
    "viewer": (
        "dashboard.view",
        "motors.list",
        "vehicles.list",
        "customers.list",
        "rendimientos.view",
        "engine_lookup.use",
    ),
}

PERMISSION_DESCRIPTIONS: dict[str, str] = {
    "dashboard.view": "Ver dashboard",
    "motors.list": "Listar motores",
    "motors.create": "Crear motor",
    "motors.edit": "Editar motor",
    "motors.delete": "Eliminar motor",
    "motors.attachments": "Gestionar adjuntos",
    "vehicles.list": "Listar vehiculos",
    "vehicles.edit": "Editar vehiculo",
    "vehicles.refresh": "Refrescar datos Geotab",
    "customers.list": "Listar clientes",
    "customers.create": "Crear cliente",
    "customers.edit": "Editar cliente y databases",
    "rendimientos.view": "Ver rendimientos",
    "rendimientos.refresh": "Refrescar rendimientos",
    "users.list": "Listar usuarios",
    "users.create": "Crear usuario",
    "users.edit": "Editar usuario",
    "audit.view": "Ver auditoria",
    "engine_lookup.use": "Consultar motor por placa",
}

_PERMISSION_CACHE_TTL_SECONDS = 300
_MAX_FAILED_LOGIN_ATTEMPTS = 5
_LOGIN_LOCK_MINUTES = 15


def _database_dsn() -> str:
    raw_dsn = os.getenv("DATABASE_URL", "").strip()
    if not raw_dsn:
        raise RuntimeError("Missing required environment variable: DATABASE_URL")
    return raw_dsn.replace("postgresql+psycopg://", "postgresql://", 1)


def _redis_client() -> redis_lib.Redis:
    return redis_lib.from_url(settings.redis_url, decode_responses=True)


def _permissions_cache_key(role: str) -> str:
    return f"perm:{role}"


# ── Password helpers ──────────────────────────────────────────────────────────

def hash_password(plain: str) -> str:
    return bcrypt.hashpw(plain.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))


def validate_password_strength(password: str, username: str | None = None) -> list[str]:
    errors: list[str] = []
    if len(password) < 10:
        errors.append("La contrasena debe tener al menos 10 caracteres.")
    if not re.search(r"[A-Z]", password):
        errors.append("La contrasena debe incluir al menos una mayuscula.")
    if not re.search(r"[a-z]", password):
        errors.append("La contrasena debe incluir al menos una minuscula.")
    if not re.search(r"\d", password):
        errors.append("La contrasena debe incluir al menos un numero.")
    if not re.search(r"[!@#$%^&*()_+\-=\[\]{}|;:,.<>?]", password):
        errors.append("La contrasena debe incluir al menos un caracter especial.")
    if username and password.strip().lower() == username.strip().lower():
        errors.append("La contrasena no puede ser igual al nombre de usuario.")
    return errors


def _utcnow() -> datetime:
    return datetime.now(tz=timezone.utc)


def hash_refresh_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


# ── Redis blacklist ───────────────────────────────────────────────────────────

def blacklist_token(jti: str, expire_seconds: int) -> None:
    _redis_client().setex(f"bl:{jti}", expire_seconds, "1")


def is_token_blacklisted(jti: str) -> bool:
    return _redis_client().exists(f"bl:{jti}") == 1


def clear_role_permissions_cache(role: str | None = None) -> None:
    client = _redis_client()
    if role:
        client.delete(_permissions_cache_key(role))
        return

    keys = client.keys("perm:*")
    if keys:
        client.delete(*keys)


def get_user_permissions(role: str) -> set[str]:
    cache_key = _permissions_cache_key(role)
    client = _redis_client()
    cached = client.get(cache_key)
    if cached:
        return {item for item in cached.split(",") if item}

    with psycopg.connect(_database_dsn(), row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT permission
                FROM role_permissions
                WHERE role = %s
                ORDER BY permission ASC
                """,
                (role,),
            )
            permissions = {row["permission"] for row in cur.fetchall()}

    client.setex(cache_key, _PERMISSION_CACHE_TTL_SECONDS, ",".join(sorted(permissions)))
    return permissions


def build_user_payload(user: dict) -> dict:
    return {
        "id": user["id"],
        "username": user["username"],
        "email": user["email"],
        "role": user["role"],
        "is_active": user["is_active"],
        "created_at": user["created_at"],
        "updated_at": user["updated_at"],
        "permissions": sorted(get_user_permissions(user["role"])),
    }


# ── User CRUD ─────────────────────────────────────────────────────────────────

def get_user_by_username(username: str) -> dict | None:
    with psycopg.connect(_database_dsn(), row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM users WHERE username = %s",
                (username,),
            )
            return cur.fetchone()


def get_user_by_id(user_id: int) -> dict | None:
    with psycopg.connect(_database_dsn(), row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM users WHERE id = %s",
                (user_id,),
            )
            return cur.fetchone()


def list_users() -> list[dict]:
    with psycopg.connect(_database_dsn(), row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, username, email, role, is_active, created_at, updated_at "
                "FROM users ORDER BY created_at ASC"
            )
            return cur.fetchall()


def create_user(username: str, email: str, password: str, role: str = "viewer") -> dict:
    errors = validate_password_strength(password, username)
    if errors:
        raise ValueError(" ".join(errors))
    password_hash = hash_password(password)
    with psycopg.connect(_database_dsn(), row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO users (username, email, password_hash, role)
                VALUES (%s, %s, %s, %s)
                RETURNING id, username, email, role, is_active, created_at, updated_at
                """,
                (username.strip(), email.strip().lower(), password_hash, role),
            )
            row = cur.fetchone()
        conn.commit()
    return row


def update_user(user_id: int, email: str | None, role: str | None, is_active: bool | None) -> dict | None:
    with psycopg.connect(_database_dsn(), row_factory=dict_row) as conn:
        sets = []
        params: list = []
        if email is not None:
            sets.append("email = %s")
            params.append(email.strip().lower())
        if role is not None:
            sets.append("role = %s")
            params.append(role)
        if is_active is not None:
            sets.append("is_active = %s")
            params.append(is_active)
        if not sets:
            return get_user_by_id(user_id)
        sets.append("updated_at = NOW()")
        params.append(user_id)
        with conn.cursor() as cur:
            cur.execute(
                f"UPDATE users SET {', '.join(sets)} WHERE id = %s "
                "RETURNING id, username, email, role, is_active, created_at, updated_at",
                params,
            )
            row = cur.fetchone()
        conn.commit()
    return row


def update_user_password(user_id: int, new_password: str) -> dict | None:
    password_hash = hash_password(new_password)
    with psycopg.connect(_database_dsn(), row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE users
                SET
                    password_hash = %s,
                    password_changed_at = NOW(),
                    updated_at = NOW()
                WHERE id = %s
                RETURNING *
                """,
                (password_hash, user_id),
            )
            row = cur.fetchone()
        conn.commit()
    return row


def record_failed_login(user_id: int) -> None:
    with psycopg.connect(_database_dsn(), row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE users
                SET
                    failed_login_attempts = COALESCE(failed_login_attempts, 0) + 1,
                    locked_until = CASE
                        WHEN COALESCE(failed_login_attempts, 0) + 1 >= %s
                            THEN NOW() + (%s * INTERVAL '1 minute')
                        ELSE locked_until
                    END,
                    updated_at = NOW()
                WHERE id = %s
                """,
                (_MAX_FAILED_LOGIN_ATTEMPTS, _LOGIN_LOCK_MINUTES, user_id),
            )
        conn.commit()


def reset_login_state(user_id: int) -> None:
    with psycopg.connect(_database_dsn(), row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE users
                SET
                    failed_login_attempts = 0,
                    locked_until = NULL,
                    last_login_at = NOW(),
                    updated_at = NOW()
                WHERE id = %s
                """,
                (user_id,),
            )
        conn.commit()


def is_user_locked(user: dict) -> tuple[bool, int]:
    locked_until = user.get("locked_until")
    if not locked_until:
        return (False, 0)

    if locked_until.tzinfo is None:
        locked_until = locked_until.replace(tzinfo=timezone.utc)

    remaining = int((locked_until - _utcnow()).total_seconds())
    if remaining > 0:
        return (True, remaining)
    return (False, 0)


def create_refresh_token(user_id: int, ip_address: str | None = None) -> tuple[str, datetime]:
    token = secrets.token_urlsafe(48)
    token_hash = hash_refresh_token(token)
    expires_at = _utcnow() + timedelta(days=settings.refresh_token_expire_days)

    with psycopg.connect(_database_dsn(), row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO refresh_tokens (id, user_id, token_hash, expires_at, revoked, ip_address)
                VALUES (%s, %s, %s, %s, FALSE, %s)
                """,
                (str(uuid.uuid4()), user_id, token_hash, expires_at, ip_address),
            )
        conn.commit()

    return token, expires_at


def get_refresh_token_record(token: str) -> dict | None:
    token_hash = hash_refresh_token(token)
    with psycopg.connect(_database_dsn(), row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT *
                FROM refresh_tokens
                WHERE token_hash = %s
                LIMIT 1
                """,
                (token_hash,),
            )
            return cur.fetchone()


def revoke_refresh_token(token: str) -> None:
    token_hash = hash_refresh_token(token)
    with psycopg.connect(_database_dsn(), row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE refresh_tokens SET revoked = TRUE WHERE token_hash = %s",
                (token_hash,),
            )
        conn.commit()


def revoke_all_refresh_tokens(user_id: int) -> None:
    with psycopg.connect(_database_dsn(), row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE refresh_tokens SET revoked = TRUE WHERE user_id = %s",
                (user_id,),
            )
        conn.commit()


def list_active_sessions(user_id: int) -> list[dict]:
    with psycopg.connect(_database_dsn(), row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, user_id, created_at, expires_at, ip_address
                FROM refresh_tokens
                WHERE user_id = %s
                  AND revoked = FALSE
                  AND expires_at > NOW()
                ORDER BY created_at DESC
                """,
                (user_id,),
            )
            return cur.fetchall()


def revoke_refresh_session(session_id: str, user_id: int | None = None) -> bool:
    with psycopg.connect(_database_dsn(), row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            if user_id is None:
                cur.execute(
                    """
                    UPDATE refresh_tokens
                    SET revoked = TRUE
                    WHERE id = %s AND revoked = FALSE
                    RETURNING id
                    """,
                    (session_id,),
                )
            else:
                cur.execute(
                    """
                    UPDATE refresh_tokens
                    SET revoked = TRUE
                    WHERE id = %s AND user_id = %s AND revoked = FALSE
                    RETURNING id
                    """,
                    (session_id, user_id),
                )
            row = cur.fetchone()
        conn.commit()
    return row is not None


def revoke_other_refresh_sessions(user_id: int, current_session_id: str | None) -> int:
    with psycopg.connect(_database_dsn(), row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            if current_session_id:
                cur.execute(
                    """
                    UPDATE refresh_tokens
                    SET revoked = TRUE
                    WHERE user_id = %s
                      AND id <> %s
                      AND revoked = FALSE
                    RETURNING id
                    """,
                    (user_id, current_session_id),
                )
            else:
                cur.execute(
                    """
                    UPDATE refresh_tokens
                    SET revoked = TRUE
                    WHERE user_id = %s
                      AND revoked = FALSE
                    RETURNING id
                    """,
                    (user_id,),
                )
            rows = cur.fetchall()
        conn.commit()
    return len(rows)


def cleanup_expired_refresh_tokens() -> int:
    with psycopg.connect(_database_dsn(), row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                DELETE FROM refresh_tokens
                WHERE revoked = TRUE OR expires_at < NOW()
                RETURNING id
                """
            )
            rows = cur.fetchall()
        conn.commit()
    return len(rows)


# ── Audit logs ────────────────────────────────────────────────────────────────

def write_audit_log(
    *,
    user_id: int | None,
    action: str,
    resource_type: str,
    resource_id: str | None,
    detail: dict | None,
    ip_address: str | None,
) -> None:
    import json as _json
    with psycopg.connect(_database_dsn(), row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO audit_logs
                    (user_id, action, resource_type, resource_id, detail, ip_address)
                VALUES (%s, %s, %s, %s, %s, %s)
                """,
                (
                    user_id,
                    action,
                    resource_type,
                    resource_id,
                    _json.dumps(detail) if detail else None,
                    ip_address,
                ),
            )
        conn.commit()


def list_audit_logs(limit: int = 100, offset: int = 0) -> list[dict]:
    with psycopg.connect(_database_dsn(), row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    al.id,
                    al.user_id,
                    u.username,
                    al.action,
                    al.resource_type,
                    al.resource_id,
                    al.detail,
                    al.ip_address,
                    al.created_at
                FROM audit_logs al
                LEFT JOIN users u ON u.id = al.user_id
                ORDER BY al.created_at DESC
                LIMIT %s OFFSET %s
                """,
                (limit, offset),
            )
            return cur.fetchall()
