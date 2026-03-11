from __future__ import annotations

import unicodedata

import pymssql

from app.core.config import SqlConfig

_PLATE_COLUMN_CANDIDATES = (
    "placa",
    "licenseplate",
    "license_plate",
    "placavehiculo",
    "placa_vehiculo",
    "numeroderegistro",
    "vehiculo",
    "matricula",
    "numberplaca",
)


def _normalize_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value)
    normalized = "".join(char for char in normalized if not unicodedata.combining(char))
    return normalized.lower().replace(" ", "").replace("_", "").replace("-", "")


def _normalize_plate_value(value) -> str | None:
    if value is None:
        return None
    cleaned = "".join(char for char in str(value).strip().upper() if char.isalnum())
    if len(cleaned) == 6 and cleaned[:3].isalpha() and cleaned[3:].isdigit():
        return cleaned
    return None


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


def _find_plate_column(conn) -> str | None:
    query = """
    SELECT c.name
    FROM sys.columns c
    INNER JOIN sys.objects o ON c.object_id = o.object_id
    INNER JOIN sys.schemas s ON o.schema_id = s.schema_id
    WHERE s.name = 'dbo'
      AND o.name = 'T_DIM_VEHICULO'
    """
    with conn.cursor(as_dict=True) as cursor:
        cursor.execute(query)
        rows = cursor.fetchall() or []

    for row in rows:
        column_name = str(row.get("name") or "").strip()
        normalized = _normalize_text(column_name)
        if normalized in _PLATE_COLUMN_CANDIDATES:
            return column_name
    return None


def _extract_plate_from_row(row: dict | None) -> str | None:
    if not row:
        return None

    prioritized_matches: list[tuple[int, str]] = []
    fallback_matches: list[str] = []

    for key, value in row.items():
        normalized_key = _normalize_text(str(key))
        normalized_plate = _normalize_plate_value(value)
        if not normalized_plate:
            continue

        if normalized_key in _PLATE_COLUMN_CANDIDATES:
            priority = _PLATE_COLUMN_CANDIDATES.index(normalized_key)
            prioritized_matches.append((priority, normalized_plate))
        else:
            fallback_matches.append(normalized_plate)

    if prioritized_matches:
        prioritized_matches.sort(key=lambda item: item[0])
        return prioritized_matches[0][1]

    if fallback_matches:
        return fallback_matches[0]

    return None


def _build_vehicle_select(plate_column: str | None) -> str:
    return """
    SELECT TOP 1 *
    FROM dbo.T_DIM_VEHICULO
    """


def _build_vehicle_select_all() -> str:
    return _build_vehicle_select(None)


def find_vehicle_by_vin(conn, vin: str) -> dict | None:
    query = _build_vehicle_select(_find_plate_column(conn)) + "WHERE VIN = %s"
    with conn.cursor(as_dict=True) as cursor:
        cursor.execute(query, (vin,))
        row = cursor.fetchone()
    if row:
        row["plate"] = _extract_plate_from_row(row)
        if "Número de motor" in row and "numero_motor" not in row:
            row["numero_motor"] = row.get("Número de motor")
    return row


def find_vehicle_by_plate(conn, plate: str) -> dict | None:
    plate_column = _find_plate_column(conn)
    if not plate_column:
        return None

    escaped_column = plate_column.replace("]", "]]")
    query = (
        _build_vehicle_select(plate_column)
        + f"WHERE UPPER(LTRIM(RTRIM(CAST([{escaped_column}] AS NVARCHAR(100))))) = %s"
    )
    params = (plate.upper(),)

    with conn.cursor(as_dict=True) as cursor:
        cursor.execute(query, params)
        row = cursor.fetchone()
    if row:
        row["plate"] = _extract_plate_from_row(row)
        if "Número de motor" in row and "numero_motor" not in row:
            row["numero_motor"] = row.get("Número de motor")
    return row


def find_vehicle_by_vin_full(conn, vin: str) -> dict | None:
    query = _build_vehicle_select_all() + "WHERE VIN = %s"
    with conn.cursor(as_dict=True) as cursor:
        cursor.execute(query, (vin,))
        row = cursor.fetchone()
    if row:
        row["plate"] = _extract_plate_from_row(row)
    return row


def find_vehicle_by_plate_full(conn, plate: str) -> dict | None:
    plate_column = _find_plate_column(conn)
    if not plate_column:
        return None

    escaped_column = plate_column.replace("]", "]]")
    query = (
        _build_vehicle_select_all()
        + f"WHERE UPPER(LTRIM(RTRIM(CAST([{escaped_column}] AS NVARCHAR(100))))) = %s"
    )
    params = (plate.upper(),)

    with conn.cursor(as_dict=True) as cursor:
        cursor.execute(query, params)
        row = cursor.fetchone()
    if row:
        row["plate"] = _extract_plate_from_row(row)
    return row


def get_vehicle_by_vin(vin: str, cfg: SqlConfig) -> dict | None:
    conn = None
    try:
        conn = open_connection(cfg)
        return find_vehicle_by_vin(conn, vin)
    finally:
        if conn:
            conn.close()


def get_vehicle_by_plate(plate: str, cfg: SqlConfig) -> dict | None:
    conn = None
    try:
        conn = open_connection(cfg)
        return find_vehicle_by_plate(conn, plate)
    finally:
        if conn:
            conn.close()


def get_vehicle_by_vin_full(vin: str, cfg: SqlConfig) -> dict | None:
    conn = None
    try:
        conn = open_connection(cfg)
        return find_vehicle_by_vin_full(conn, vin)
    finally:
        if conn:
            conn.close()


def get_vehicle_by_plate_full(plate: str, cfg: SqlConfig) -> dict | None:
    conn = None
    try:
        conn = open_connection(cfg)
        return find_vehicle_by_plate_full(conn, plate)
    finally:
        if conn:
            conn.close()


def get_engine_number_from_vin(vin: str, cfg: SqlConfig) -> str | None:
    row = get_vehicle_by_vin(vin, cfg)
    if not row:
        return None

    engine_number = row.get("numero_motor")
    if not engine_number:
        return None

    return str(engine_number).strip()


def get_vin_from_plate(plate: str, cfg: SqlConfig) -> str | None:
    row = get_vehicle_by_plate(plate, cfg)
    if not row:
        return None

    vin = row.get("VIN")
    if not vin:
        return None

    return str(vin).strip().upper()
