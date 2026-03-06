from __future__ import annotations

import pymssql

from app.core.config import SqlConfig


def open_connection(cfg: SqlConfig):
    return pymssql.connect(
        server=cfg.server,
        user=cfg.user,
        password=cfg.password,
        database=cfg.database,
        port=cfg.port,
        as_dict=True,
        timeout=30,
        login_timeout=30,
    )


def find_engine_number_by_vin(conn, vin: str) -> dict | None:
    query = """
    SELECT TOP 1
        VIN,
        [N\u00famero de motor] AS numero_motor
    FROM dbo.T_DIM_VEHICULO
    WHERE VIN = %s
    """
    with conn.cursor(as_dict=True) as cursor:
        cursor.execute(query, (vin,))
        return cursor.fetchone()


def get_engine_number_from_vin(vin: str, cfg: SqlConfig) -> str | None:
    conn = None
    try:
        conn = open_connection(cfg)
        row = find_engine_number_by_vin(conn, vin)
    finally:
        if conn:
            conn.close()

    if not row:
        return None

    engine_number = row.get("numero_motor")
    if not engine_number:
        return None

    return str(engine_number).strip()