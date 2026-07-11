from __future__ import annotations

import pytest
from psycopg.rows import dict_row

from app.core.db import close_pool, db_conn


@pytest.fixture(scope="session", autouse=True)
def _cerrar_pool_al_finalizar() -> None:
    """Asegura que el pool global se cierre al terminar la sesion de tests."""
    yield
    close_pool()


@pytest.fixture
def tabla_prueba() -> str:
    """Crea una tabla propia para los tests de commit/rollback y la limpia."""
    nombre = "test_db_pool_check"
    with db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(f"DROP TABLE IF EXISTS {nombre}")
            cur.execute(f"CREATE TABLE {nombre} (id int PRIMARY KEY)")
    yield nombre
    with db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(f"DROP TABLE IF EXISTS {nombre}")


def test_db_conn_entrega_conexion_funcional() -> None:
    """db_conn() debe devolver una conexion sobre la que se puede ejecutar SQL."""
    with db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT 1 AS n")
            row = cur.fetchone()
    assert row == (1,)


def test_db_conn_reusa_conexion_fisica(monkeypatch) -> None:
    """
    Con un pool de min_size=1, max_size=1 y uso secuencial, dos checkouts
    deben reutilizar la misma conexion fisica de PostgreSQL.
    """
    close_pool()
    monkeypatch.setenv("DB_POOL_MAX_SIZE", "1")
    try:
        with db_conn() as conn:
            pid1 = conn.info.backend_pid
        with db_conn() as conn:
            pid2 = conn.info.backend_pid
        assert pid1 == pid2
    finally:
        close_pool()
        monkeypatch.delenv("DB_POOL_MAX_SIZE", raising=False)


def test_db_conn_row_factory_no_filtra() -> None:
    """
    El row_factory de un checkout no debe arrastrarse al siguiente uso de la
    misma conexion fisica del pool.
    """
    with db_conn(row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT 1 AS n")
            row = cur.fetchone()
        assert isinstance(row, dict)

    with db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT 1 AS n")
            row = cur.fetchone()
        assert isinstance(row, tuple)


def test_db_conn_commit_al_salir(tabla_prueba: str) -> None:
    """Al salir sin excepcion, db_conn() debe commitear los cambios."""
    with db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(f"INSERT INTO {tabla_prueba} (id) VALUES (42)")

    with db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(f"SELECT id FROM {tabla_prueba} WHERE id = 42")
            row = cur.fetchone()

    assert row == (42,)


def test_db_conn_rollback_al_fallar(tabla_prueba: str) -> None:
    """Al salir con excepcion, db_conn() debe hacer rollback."""
    try:
        with db_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(f"INSERT INTO {tabla_prueba} (id) VALUES (99)")
            raise RuntimeError("forzar rollback")
    except RuntimeError:
        pass

    with db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(f"SELECT id FROM {tabla_prueba} WHERE id = 99")
            row = cur.fetchone()

    assert row is None
