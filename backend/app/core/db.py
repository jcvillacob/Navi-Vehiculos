"""
Pool de conexiones psycopg compartido por el backend.

- Lazy: el pool se crea en el primer uso (asi conftest.py puede reescribir
  DATABASE_URL antes de que se abra ninguna conexion).
- `db_conn()` reproduce la semantica de `with psycopg.connect(...)`:
  commit al salir sin excepcion, rollback si hay excepcion, y devuelve la
  conexion al pool en vez de cerrarla.
- row_factory se setea explicitamente en CADA checkout para que no se
  filtre estado entre usos de la misma conexion fisica.
"""
from __future__ import annotations

import logging
import os
import threading
from collections.abc import Generator
from contextlib import contextmanager
from typing import Any

import psycopg
from psycopg.rows import tuple_row
from psycopg_pool import ConnectionPool

_logger = logging.getLogger(__name__)

_pool: ConnectionPool | None = None
_pool_lock = threading.Lock()


def _database_dsn() -> str:
    """Devuelve el DSN limpio para psycopg (sin el +psycopg de SQLAlchemy)."""
    raw_dsn = os.getenv("DATABASE_URL", "").strip()
    if not raw_dsn:
        raise RuntimeError("Missing required environment variable: DATABASE_URL")
    return raw_dsn.replace("postgresql+psycopg://", "postgresql://", 1)


def get_pool() -> ConnectionPool:
    """Singleton lazy del pool. Seguro para threads."""
    global _pool
    if _pool is None:
        with _pool_lock:
            if _pool is None:
                max_size = int(os.getenv("DB_POOL_MAX_SIZE", "10"))
                _pool = ConnectionPool(
                    _database_dsn(),
                    min_size=1,
                    max_size=max_size,
                    open=True,
                    name="navi-pool",
                    timeout=30,
                )
                _logger.info("Pool de conexiones creado (max_size=%s).", max_size)
    return _pool


@contextmanager
def db_conn(
    row_factory: psycopg.rows.RowFactory[Any] | None = None,
) -> Generator[psycopg.Connection, None, None]:
    """
    Context manager que chequea una conexion del pool, limpia su row_factory
    y la devuelve al salir.

    El context manager de `psycopg_pool` ya se encarga de:
      - hacer commit si se sale sin excepcion,
      - hacer rollback si se sale con excepcion,
      - devolver la conexion al pool en vez de cerrarla.

    Aqui solo forzamos un row_factory por defecto en cada checkout para evitar
    que un cursor anterior deje la conexion fisica con un factory inesperado.
    """
    with get_pool().connection() as conn:
        conn.row_factory = row_factory if row_factory is not None else tuple_row
        yield conn


def close_pool() -> None:
    """Cierra el pool de forma idempotente. Usar en shutdown de la app."""
    global _pool
    with _pool_lock:
        if _pool is not None:
            try:
                _pool.close()
                _logger.info("Pool de conexiones cerrado.")
            finally:
                _pool = None
