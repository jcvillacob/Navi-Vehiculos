from __future__ import annotations

import os
from datetime import datetime

import psycopg
from psycopg.rows import dict_row
import bcrypt
import redis as redis_lib

from app.core.config import settings


def _database_dsn() -> str:
    raw_dsn = os.getenv("DATABASE_URL", "").strip()
    if not raw_dsn:
        raise RuntimeError("Missing required environment variable: DATABASE_URL")
    return raw_dsn.replace("postgresql+psycopg://", "postgresql://", 1)


def _redis_client() -> redis_lib.Redis:
    return redis_lib.from_url(settings.redis_url, decode_responses=True)


def _ensure_auth_tables(conn: psycopg.Connection) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                id            SERIAL PRIMARY KEY,
                username      TEXT NOT NULL UNIQUE,
                email         TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                role          TEXT NOT NULL DEFAULT 'viewer'
                                  CHECK (role IN ('admin', 'editor', 'viewer')),
                is_active     BOOLEAN NOT NULL DEFAULT TRUE,
                created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS audit_logs (
                id            SERIAL PRIMARY KEY,
                user_id       INTEGER REFERENCES users(id) ON DELETE SET NULL,
                action        TEXT NOT NULL,
                resource_type TEXT NOT NULL,
                resource_id   TEXT,
                detail        JSONB,
                ip_address    TEXT,
                created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
            """
        )
        cur.execute(
            """
            CREATE INDEX IF NOT EXISTS audit_logs_user_id_idx
            ON audit_logs (user_id);
            """
        )
        cur.execute(
            """
            CREATE INDEX IF NOT EXISTS audit_logs_created_idx
            ON audit_logs (created_at DESC);
            """
        )


def ensure_auth_tables() -> None:
    with psycopg.connect(_database_dsn(), row_factory=dict_row) as conn:
        _ensure_auth_tables(conn)
        conn.commit()


# ── Password helpers ──────────────────────────────────────────────────────────

def hash_password(plain: str) -> str:
    return bcrypt.hashpw(plain.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))


# ── Redis blacklist ───────────────────────────────────────────────────────────

def blacklist_token(jti: str, expire_seconds: int) -> None:
    _redis_client().setex(f"bl:{jti}", expire_seconds, "1")


def is_token_blacklisted(jti: str) -> bool:
    return _redis_client().exists(f"bl:{jti}") == 1


# ── User CRUD ─────────────────────────────────────────────────────────────────

def get_user_by_username(username: str) -> dict | None:
    with psycopg.connect(_database_dsn(), row_factory=dict_row) as conn:
        _ensure_auth_tables(conn)
        conn.commit()
        with conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM users WHERE username = %s",
                (username,),
            )
            return cur.fetchone()


def get_user_by_id(user_id: int) -> dict | None:
    with psycopg.connect(_database_dsn(), row_factory=dict_row) as conn:
        _ensure_auth_tables(conn)
        conn.commit()
        with conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM users WHERE id = %s",
                (user_id,),
            )
            return cur.fetchone()


def list_users() -> list[dict]:
    with psycopg.connect(_database_dsn(), row_factory=dict_row) as conn:
        _ensure_auth_tables(conn)
        conn.commit()
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, username, email, role, is_active, created_at, updated_at "
                "FROM users ORDER BY created_at ASC"
            )
            return cur.fetchall()


def create_user(username: str, email: str, password: str, role: str = "viewer") -> dict:
    password_hash = hash_password(password)
    with psycopg.connect(_database_dsn(), row_factory=dict_row) as conn:
        _ensure_auth_tables(conn)
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
        _ensure_auth_tables(conn)
        conn.commit()
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
        _ensure_auth_tables(conn)
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
        _ensure_auth_tables(conn)
        conn.commit()
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
