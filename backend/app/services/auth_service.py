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


# Re-export para compatibilidad con imports existentes.
from app.services.module_registry import PERMISSION_DESCRIPTIONS  # noqa: E402,F401


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
        errors.append("La contraseña debe tener al menos 10 caracteres.")
    if not re.search(r"[A-Z]", password):
        errors.append("La contraseña debe incluir al menos una mayuscula.")
    if not re.search(r"[a-z]", password):
        errors.append("La contraseña debe incluir al menos una minuscula.")
    if not re.search(r"\d", password):
        errors.append("La contraseña debe incluir al menos un numero.")
    if not re.search(r"[!@#$%^&*()_+\-=\[\]{}|;:,.<>?]", password):
        errors.append("La contraseña debe incluir al menos un caracter especial.")
    if username and password.strip().lower() == username.strip().lower():
        errors.append("La contraseña no puede ser igual al nombre de usuario.")
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


# ── Roles CRUD ────────────────────────────────────────────────────────────────

class RoleInUseError(Exception):
    """El rol no se puede borrar porque hay usuarios asignados."""


class SystemRoleError(Exception):
    """Operación no permitida sobre un rol de sistema."""


class RoleNotFoundError(Exception):
    """El rol no existe."""


class DuplicateRoleError(Exception):
    """Ya existe un rol con esa key."""


def list_roles() -> list[dict]:
    with psycopg.connect(_database_dsn(), row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    r.key, r.label, r.description, r.is_system,
                    r.created_at, r.updated_at,
                    (SELECT COUNT(*) FROM users u WHERE u.role = r.key) AS user_count
                FROM roles r
                ORDER BY r.is_system DESC, r.label ASC
                """
            )
            return cur.fetchall()


def get_role(key: str) -> dict | None:
    with psycopg.connect(_database_dsn(), row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT key, label, description, is_system, created_at, updated_at "
                "FROM roles WHERE key = %s",
                (key,),
            )
            return cur.fetchone()


def role_exists(key: str) -> bool:
    return get_role(key) is not None


def list_valid_role_keys() -> set[str]:
    with psycopg.connect(_database_dsn(), row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT key FROM roles")
            return {row["key"] for row in cur.fetchall()}


def create_role(key: str, label: str, description: str | None = None) -> dict:
    normalized = (key or "").strip().lower()
    if not normalized:
        raise ValueError("La key del rol es obligatoria.")
    if not (label or "").strip():
        raise ValueError("El label del rol es obligatorio.")
    if role_exists(normalized):
        raise DuplicateRoleError(f"Ya existe un rol con la key '{normalized}'.")
    with psycopg.connect(_database_dsn(), row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO roles (key, label, description, is_system)
                VALUES (%s, %s, %s, FALSE)
                RETURNING key, label, description, is_system, created_at, updated_at
                """,
                (normalized, label.strip(), (description or "").strip() or None),
            )
            row = cur.fetchone()
        conn.commit()
    return row


def update_role(key: str, label: str | None, description: str | None) -> dict:
    role = get_role(key)
    if not role:
        raise RoleNotFoundError(f"No existe el rol '{key}'.")

    sets: list[str] = []
    params: list = []
    if label is not None:
        if not label.strip():
            raise ValueError("El label del rol no puede estar vacio.")
        sets.append("label = %s")
        params.append(label.strip())
    if description is not None:
        sets.append("description = %s")
        params.append((description or "").strip() or None)
    if not sets:
        return role
    sets.append("updated_at = NOW()")
    params.append(key)
    with psycopg.connect(_database_dsn(), row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"UPDATE roles SET {', '.join(sets)} WHERE key = %s "
                "RETURNING key, label, description, is_system, created_at, updated_at",
                params,
            )
            row = cur.fetchone()
        conn.commit()
    return row


def delete_role(key: str) -> None:
    role = get_role(key)
    if not role:
        raise RoleNotFoundError(f"No existe el rol '{key}'.")
    if role["is_system"]:
        raise SystemRoleError("Los roles de sistema no se pueden eliminar.")
    with psycopg.connect(_database_dsn(), row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) AS c FROM users WHERE role = %s", (key,))
            count = cur.fetchone()["c"]
            if count > 0:
                raise RoleInUseError(
                    f"No se puede eliminar: hay {count} usuario(s) con este rol."
                )
            cur.execute("DELETE FROM roles WHERE key = %s", (key,))
        conn.commit()
    clear_role_permissions_cache(key)


def get_role_permissions(key: str) -> set[str]:
    """Devuelve el set de codenames que el rol tiene asignados."""
    with psycopg.connect(_database_dsn(), row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT permission FROM role_permissions WHERE role = %s",
                (key,),
            )
            return {row["permission"] for row in cur.fetchall()}


def replace_role_permissions(
    key: str,
    matrix: dict[str, str],
) -> set[str]:
    """
    Reescribe la matriz de permisos del rol en una sola transacción.

    `matrix` es `{modulo: nivel}` y se traduce a codenames via
    `permissions_for_matrix`. Devuelve el set final de codenames.

    - Verifica que el rol exista.
    - Aplica guard de anti-lockout para el rol `admin` (no puede perder
      `roles.manage`/`users.*`).
    - Invalida el cache de Redis para el rol.
    """
    from app.services.module_registry import (
        ADMIN_PROTECTED_CODENAMES,
        permissions_for_matrix,
    )

    role = get_role(key)
    if not role:
        raise RoleNotFoundError(f"No existe el rol '{key}'.")

    codenames = permissions_for_matrix(matrix)

    if role["is_system"] and key == "admin":
        missing = ADMIN_PROTECTED_CODENAMES - codenames
        if missing:
            raise ValueError(
                "El rol admin no puede perder permisos criticos: "
                + ", ".join(sorted(missing))
            )

    with psycopg.connect(_database_dsn(), row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM role_permissions WHERE role = %s", (key,))
            for codename in codenames:
                cur.execute(
                    "INSERT INTO role_permissions (role, permission) VALUES (%s, %s) "
                    "ON CONFLICT (role, permission) DO NOTHING",
                    (key, codename),
                )
        conn.commit()

    clear_role_permissions_cache(key)
    return codenames


def list_users() -> list[dict]:
    with psycopg.connect(_database_dsn(), row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, username, email, role, is_active, created_at, updated_at, last_login_at "
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


def create_refresh_token(
    user_id: int,
    ip_address: str | None = None,
    user_agent: str | None = None,
) -> tuple[str, datetime]:
    token = secrets.token_urlsafe(48)
    token_hash = hash_refresh_token(token)
    expires_at = _utcnow() + timedelta(days=settings.refresh_token_expire_days)

    with psycopg.connect(_database_dsn(), row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO refresh_tokens (id, user_id, token_hash, expires_at, revoked, ip_address, user_agent)
                VALUES (%s, %s, %s, %s, FALSE, %s, %s)
                """,
                (str(uuid.uuid4()), user_id, token_hash, expires_at, ip_address, user_agent),
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
                SELECT id, user_id, created_at, expires_at, ip_address, user_agent
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


def enforce_max_concurrent_sessions(user_id: int, max_sessions: int) -> int:
    """
    Si `max_sessions > 0` y el usuario tiene más sesiones activas que el límite,
    revoca las más antiguas (las creadas antes) hasta quedar en `max_sessions - 1`,
    dejando hueco para el nuevo login que está a punto de crearse.

    Devuelve cuántas sesiones se revocaron. Si `max_sessions <= 0`, no hace nada.
    """
    if max_sessions <= 0:
        return 0
    with psycopg.connect(_database_dsn(), row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id
                FROM refresh_tokens
                WHERE user_id = %s
                  AND revoked = FALSE
                  AND expires_at > NOW()
                ORDER BY created_at DESC
                """,
                (user_id,),
            )
            active = [row["id"] for row in cur.fetchall()]
            if len(active) < max_sessions:
                conn.commit()
                return 0
            # Necesitamos `max_sessions - 1` activas (la nueva no se ha creado aún).
            to_revoke = active[max_sessions - 1:]
            if not to_revoke:
                conn.commit()
                return 0
            cur.execute(
                "UPDATE refresh_tokens SET revoked = TRUE WHERE id = ANY(%s::uuid[])",
                (to_revoke,),
            )
        conn.commit()
    return len(to_revoke)


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
