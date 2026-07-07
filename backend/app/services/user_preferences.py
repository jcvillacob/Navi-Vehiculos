from __future__ import annotations

import logging
import os
from datetime import datetime
from typing import Any

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

_logger = logging.getLogger(__name__)


def _database_dsn() -> str:
    raw_dsn = os.getenv("DATABASE_URL", "").strip()
    if not raw_dsn:
        raise RuntimeError("Missing required environment variable: DATABASE_URL")
    return raw_dsn.replace("postgresql+psycopg://", "postgresql://", 1)


def list_user_preferences(user_id: int) -> list[dict[str, Any]]:
    with psycopg.connect(_database_dsn(), row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT key, value, updated_at
                FROM user_preferences
                WHERE user_id = %s
                ORDER BY key ASC
                """,
                (user_id,),
            )
            return list(cur.fetchall())


def get_user_preference(user_id: int, key: str) -> dict[str, Any] | None:
    with psycopg.connect(_database_dsn(), row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT key, value, updated_at
                FROM user_preferences
                WHERE user_id = %s AND key = %s
                """,
                (user_id, key),
            )
            return cur.fetchone()


def set_user_preference(user_id: int, key: str, value: Any) -> dict[str, Any]:
    with psycopg.connect(_database_dsn(), row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO user_preferences (user_id, key, value, created_at, updated_at)
                VALUES (%s, %s, %s, NOW(), NOW())
                ON CONFLICT (user_id, key) DO UPDATE
                SET value = EXCLUDED.value,
                    updated_at = NOW()
                RETURNING key, value, updated_at
                """,
                (user_id, key, Jsonb(value)),
            )
            row = cur.fetchone()
        conn.commit()
    return row


def delete_user_preference(user_id: int, key: str) -> bool:
    with psycopg.connect(_database_dsn(), row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                DELETE FROM user_preferences
                WHERE user_id = %s AND key = %s
                """,
                (user_id, key),
            )
            deleted = cur.rowcount > 0
        conn.commit()
    return deleted
