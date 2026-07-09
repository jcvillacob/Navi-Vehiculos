from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
import re
from typing import Any, Callable

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from app.clients.geotab_client import find_device_by_plate, get_authenticated_client
from app.schemas.vehicle import (
    CpkCutoffPreviewRequest,
    CpkCutoffPreviewResponse,
    CpkCutoffPreviewRow,
    MonthlyPerformanceCalculateRequest,
    MonthlyPerformanceRecord,
    MonthlyPerformanceResponse,
    MonthlyPerformanceSummary,
)
from app.core.config import load_geotab_config
from app.services.motor_catalog import _database_dsn, _ensure_motor_tables
from app.services.performance_providers import _calculate_geotab_vehicle_record, get_monthly_performance_provider
from app.services.performance_types import BindingSnapshot, PerformanceTarget
from app.services.provider_registry import infer_provider_key, supports_monthly_performance


_PERF_TABLES_DDL_DONE = False


def _ensure_performance_tables(conn: psycopg.Connection) -> None:
    """
    Garantiza tablas de rendimientos. La DDL pesada solo corre una vez por
    proceso, en conexion propia, para no atrapar locks en la transaccion
    larga del caller. Ver la nota en _ensure_motor_tables.
    """
    global _PERF_TABLES_DDL_DONE
    # _ensure_motor_tables ya cachea internamente y usa conexion propia para DDL.
    _ensure_motor_tables(conn)
    if _PERF_TABLES_DDL_DONE:
        return
    own_conn = psycopg.connect(_database_dsn())
    try:
        _run_performance_tables_ddl_inner(own_conn)
        own_conn.commit()
    finally:
        own_conn.close()
    _PERF_TABLES_DDL_DONE = True


def _run_performance_tables_ddl_inner(conn: psycopg.Connection) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS vehicle_provider_bindings (
                id BIGSERIAL PRIMARY KEY,
                plate VARCHAR(10) NOT NULL REFERENCES vehicle_motor_assignments(plate) ON DELETE CASCADE,
                customer_database_id BIGINT NOT NULL REFERENCES customer_databases(id) ON DELETE CASCADE,
                provider TEXT NOT NULL,
                provider_vehicle_id TEXT NULL,
                provider_plate TEXT NULL,
                provider_customer_id TEXT NULL,
                binding_status TEXT NOT NULL DEFAULT 'unknown',
                last_resolved_at TIMESTAMPTZ NULL,
                last_error TEXT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                UNIQUE (plate, customer_database_id, provider)
            );
            """
        )
        cur.execute(
            """
            DO $$
            BEGIN
                IF NOT EXISTS (
                    SELECT 1
                    FROM pg_constraint
                    WHERE conname = 'vehicle_provider_bindings_plate_fkey'
                ) THEN
                    ALTER TABLE vehicle_provider_bindings
                    ADD CONSTRAINT vehicle_provider_bindings_plate_fkey
                    FOREIGN KEY (plate) REFERENCES vehicle_motor_assignments(plate) ON DELETE CASCADE;
                END IF;
            END $$;
            """
        )
        cur.execute(
            """
            ALTER TABLE vehicle_provider_bindings
            ADD COLUMN IF NOT EXISTS is_manual BOOLEAN NOT NULL DEFAULT FALSE;
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS monthly_vehicle_performance (
                id BIGSERIAL PRIMARY KEY,
                customer_id BIGINT NULL REFERENCES customers(id),
                customer_database_id BIGINT NOT NULL REFERENCES customer_databases(id) ON DELETE CASCADE,
                plate VARCHAR(10) NOT NULL REFERENCES vehicle_motor_assignments(plate) ON DELETE CASCADE,
                period_month TEXT NOT NULL,
                source_provider TEXT NOT NULL,
                provider_vehicle_id TEXT NULL,
                technical_number TEXT NULL,
                engine_name TEXT NULL,
                odo_start DOUBLE PRECISION NULL,
                odo_end DOUBLE PRECISION NULL,
                horo_start DOUBLE PRECISION NULL,
                horo_end DOUBLE PRECISION NULL,
                kms_ecm DOUBLE PRECISION NULL,
                kms_gps DOUBLE PRECISION NULL,
                hours_ecm DOUBLE PRECISION NULL,
                hours_gps DOUBLE PRECISION NULL,
                fuel_gallons DOUBLE PRECISION NULL,
                calculation_status TEXT NOT NULL DEFAULT 'partial',
                warnings JSONB NOT NULL DEFAULT '[]'::jsonb,
                calculated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                UNIQUE (customer_database_id, plate, period_month)
            );
            """
        )
        cur.execute(
            """
            ALTER TABLE monthly_vehicle_performance
            ADD COLUMN IF NOT EXISTS is_adhoc BOOLEAN NOT NULL DEFAULT FALSE;
            """
        )


def _normalize_month(value: str) -> tuple[str, int, int]:
    normalized = (value or "").strip()
    try:
        parsed = datetime.strptime(normalized, "%Y-%m")
    except ValueError as exc:
        raise ValueError("El mes debe venir en formato YYYY-MM.") from exc
    return normalized, parsed.year, parsed.month


def _previous_month(month: str) -> str:
    _, year, value = _normalize_month(month)
    if value == 1:
        return f"{year - 1}-12"
    return f"{year}-{value - 1:02d}"


_SYSTEM_DB_CACHE: tuple[int, int] | None = None


def _ensure_system_database(conn: psycopg.Connection) -> tuple[int, int]:
    """
    Garantiza que exista un customer '__navitrans_system__' y una database
    Geotab asociada con las credenciales globales de Navitrans. Retorna
    (customer_id, customer_database_id). Se cachea en memoria por proceso.
    """
    global _SYSTEM_DB_CACHE
    if _SYSTEM_DB_CACHE is not None:
        return _SYSTEM_DB_CACHE

    geotab_cfg = load_geotab_config()
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            INSERT INTO customers (name) VALUES ('__navitrans_system__')
            ON CONFLICT (name) DO UPDATE SET name = EXCLUDED.name
            RETURNING id;
            """,
        )
        customer_id = int(cur.fetchone()["id"])

        cur.execute(
            """
            INSERT INTO customer_databases (customer_id, database_name, username, password, connection_type)
            VALUES (%s, %s, %s, %s, 'geotab')
            ON CONFLICT (customer_id, database_name, username)
            DO UPDATE SET password = EXCLUDED.password
            RETURNING id;
            """,
            (customer_id, geotab_cfg.database, geotab_cfg.username, geotab_cfg.password),
        )
        database_id = int(cur.fetchone()["id"])
    conn.commit()

    _SYSTEM_DB_CACHE = (customer_id, database_id)
    return _SYSTEM_DB_CACHE


def _fetch_adhoc_targets(
    conn: psycopg.Connection,
    *,
    system_customer_id: int,
    system_database_id: int,
    geotab_username: str,
    geotab_password: str,
    geotab_database: str,
    adhoc_plates: list[str] | None = None,
    adhoc_filters: dict[str, list[str]] | None = None,
) -> list[PerformanceTarget]:
    """
    Obtiene vehiculos sin customer_database_id que coincidan con los filtros.
    Retorna PerformanceTarget con credenciales Navitrans Geotab.
    """
    clean_plates = sorted({p.strip().upper() for p in (adhoc_plates or []) if p.strip()})
    filters = adhoc_filters or {}
    clean_marcas = sorted({v.strip() for v in filters.get("marca", []) if v.strip()})
    clean_lineas = sorted({v.strip() for v in filters.get("linea", []) if v.strip()})
    clean_nombres = sorted({v.strip() for v in filters.get("nombre_vehiculo", []) if v.strip()})

    if not clean_plates and not clean_marcas and not clean_lineas and not clean_nombres:
        return []

    where_clauses = ["a.customer_database_id IS NULL"]
    params: list[Any] = []

    if clean_plates:
        where_clauses.append("UPPER(a.plate) = ANY(%s)")
        params.append(clean_plates)
    if clean_marcas:
        where_clauses.append("a.marca = ANY(%s)")
        params.append(clean_marcas)
    if clean_lineas:
        where_clauses.append("a.linea = ANY(%s)")
        params.append(clean_lineas)
    if clean_nombres:
        where_clauses.append("a.nombre_vehiculo = ANY(%s)")
        params.append(clean_nombres)

    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            f"""
            SELECT
                a.plate,
                a.technical_number,
                mc.engine_name
            FROM vehicle_motor_assignments a
            LEFT JOIN motor_catalog mc
                ON mc.technical_number = a.technical_number
            WHERE {" AND ".join(where_clauses)}
            ORDER BY a.plate ASC;
            """,
            params,
        )
        rows = cur.fetchall()

    return [
        PerformanceTarget(
            provider_key="geotab",
            customer_id=system_customer_id,
            customer_database_id=system_database_id,
            client_name="Navitrans",
            database_name=geotab_database,
            plate=str(row["plate"]),
            technical_number=row.get("technical_number"),
            engine_name=row.get("engine_name"),
            username=geotab_username,
            password=geotab_password,
            provider_config={},
        )
        for row in rows
    ]


def _build_record(row: dict[str, Any]) -> MonthlyPerformanceRecord:
    is_adhoc = bool(row.get("is_adhoc", False))
    return MonthlyPerformanceRecord(
        customer_id=row.get("customer_id"),
        customer_database_id=int(row["customer_database_id"]),
        client_name="Navitrans" if is_adhoc else row.get("client_name"),
        database_name="Geotab Global" if is_adhoc else row.get("database_name"),
        source_provider=str(row.get("source_provider") or "artimo"),
        plate=str(row["plate"]),
        provider_vehicle_id=row.get("provider_vehicle_id"),
        technical_number=row.get("technical_number"),
        engine_name=row.get("engine_name"),
        category=row.get("category") or "Ninguna",
        period_month=str(row["period_month"]),
        odo_start=row.get("odo_start"),
        odo_end=row.get("odo_end"),
        horo_start=row.get("horo_start"),
        horo_end=row.get("horo_end"),
        kms_ecm=row.get("kms_ecm"),
        kms_gps=row.get("kms_gps"),
        hours_ecm=row.get("hours_ecm"),
        hours_gps=row.get("hours_gps"),
        fuel_gallons=row.get("fuel_gallons"),
        vocacional=bool(row.get("vocacional")),
        calculation_status=str(row["calculation_status"]),
        warnings=list(row.get("warnings") or []),
        calculated_at=row.get("calculated_at"),
        vin=row.get("vin"),
        cpl=row.get("cpl"),
        marca=row.get("marca"),
        linea=row.get("linea"),
        ano_modelo=row.get("ano_modelo"),
        tipo_combustible=row.get("tipo_combustible"),
        nombre_vehiculo=row.get("nombre_vehiculo"),
        is_adhoc=bool(row.get("is_adhoc", False)),
    )


def _fetch_targets(
    conn: psycopg.Connection,
    *,
    customer_id: int | None,
    customer_ids: list[int] | None,
    customer_database_id: int | None,
) -> list[PerformanceTarget]:
    params: list[Any] = []
    where_clauses = ["a.customer_database_id IS NOT NULL"]
    effective_customer_ids = sorted(
        {
            int(value)
            for value in ([customer_id] if customer_id is not None else []) + (customer_ids or [])
            if value is not None
        }
    )
    if effective_customer_ids:
        where_clauses.append("a.customer_id = ANY(%s)")
        params.append(effective_customer_ids)
    if customer_database_id is not None:
        where_clauses.append("a.customer_database_id = %s")
        params.append(customer_database_id)

    with conn.cursor() as cur:
        cur.execute(
            f"""
            SELECT
                a.customer_id,
                a.customer_database_id,
                c.name AS client_name,
                cd.database_name,
                a.plate,
                a.technical_number,
                mc.engine_name,
                cd.username,
                cd.password,
                cd.connection_type,
                cd.access_url,
                cd.provider_config
            FROM vehicle_motor_assignments a
            INNER JOIN customer_databases cd
                ON cd.id = a.customer_database_id
            LEFT JOIN customers c
                ON c.id = a.customer_id
            LEFT JOIN motor_catalog mc
                ON mc.technical_number = a.technical_number
            WHERE {" AND ".join(where_clauses)}
            ORDER BY c.name ASC NULLS LAST, cd.database_name ASC, a.plate ASC;
            """,
            params,
        )
        rows = cur.fetchall()

    targets: list[PerformanceTarget] = []
    for row in rows:
        provider_key = infer_provider_key(
            connection_type=row.get("connection_type"),
            database_name=row.get("database_name"),
            access_url=row.get("access_url"),
            provider_config=row.get("provider_config"),
        )
        if not supports_monthly_performance(provider_key):
            continue
        targets.append(
            PerformanceTarget(
                provider_key=provider_key,
                customer_id=row.get("customer_id"),
                customer_database_id=int(row["customer_database_id"]),
                client_name=row.get("client_name"),
                database_name=row.get("database_name"),
                plate=str(row["plate"]),
                technical_number=row.get("technical_number"),
                engine_name=row.get("engine_name"),
                username=str(row.get("username") or "").strip(),
                password=str(row.get("password") or "").strip(),
                provider_config=row.get("provider_config") if isinstance(row.get("provider_config"), dict) else {},
            )
        )
    return targets


_BOGOTA_TZ = timezone(timedelta(hours=-5), name="America/Bogota")


def _normalize_cpk_plate(value: str | None) -> str:
    return "".join(char for char in str(value or "").strip().upper() if char.isalnum())


def _normalize_cpk_datetime_text(value: str) -> str:
    normalized = (
        str(value or "")
        .replace("\u00a0", " ")
        .replace("\u202f", " ")
        .strip()
    )
    normalized = " ".join(normalized.split())
    normalized = normalized.replace("/", "-")
    normalized = re.sub(r"\ba\s*\.?\s*m\.?\b", "AM", normalized, flags=re.IGNORECASE)
    normalized = re.sub(r"\bp\s*\.?\s*m\.?\b", "PM", normalized, flags=re.IGNORECASE)
    return normalized


def _parse_cpk_cutoff_datetime(value: str) -> datetime:
    normalized = _normalize_cpk_datetime_text(value)
    if not normalized:
        raise ValueError("Fecha vacia")

    candidate = normalized
    if candidate.endswith("Z"):
        candidate = candidate[:-1] + "+00:00"

    parsed: datetime | None = None
    parse_formats = (
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%Y-%m-%d %I:%M:%S %p",
        "%Y-%m-%d %I:%M %p",
        "%Y-%m-%d",
        "%d-%m-%Y %H:%M:%S",
        "%d-%m-%Y %H:%M",
        "%d-%m-%Y %I:%M:%S %p",
        "%d-%m-%Y %I:%M %p",
        "%d-%m-%Y",
    )
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError:
        for fmt in parse_formats:
            try:
                parsed = datetime.strptime(candidate, fmt)
                break
            except ValueError:
                continue

    if parsed is None:
        raise ValueError("Fecha invalida")

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=_BOGOTA_TZ)
    return parsed.astimezone(timezone.utc)


def _format_geotab_utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _fetch_cpk_targets_by_plate(
    conn: psycopg.Connection,
    *,
    plates: list[str],
) -> dict[str, PerformanceTarget]:
    if not plates:
        return {}
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT
                a.customer_id,
                a.customer_database_id,
                c.name AS client_name,
                cd.database_name,
                a.plate,
                a.technical_number,
                mc.engine_name,
                cd.username,
                cd.password,
                cd.connection_type,
                cd.access_url,
                cd.provider_config
            FROM vehicle_motor_assignments a
            LEFT JOIN customer_databases cd
                ON cd.id = a.customer_database_id
            LEFT JOIN customers c
                ON c.id = a.customer_id
            LEFT JOIN motor_catalog mc
                ON mc.technical_number = a.technical_number
            WHERE UPPER(a.plate) = ANY(%s);
            """,
            (plates,),
        )
        rows = cur.fetchall()

    targets: dict[str, PerformanceTarget] = {}
    for row in rows:
        plate = _normalize_cpk_plate(row.get("plate"))
        provider_key = infer_provider_key(
            connection_type=row.get("connection_type"),
            database_name=row.get("database_name"),
            access_url=row.get("access_url"),
            provider_config=row.get("provider_config"),
        )
        targets[plate] = PerformanceTarget(
            provider_key=provider_key,
            customer_id=row.get("customer_id"),
            customer_database_id=int(row["customer_database_id"]) if row.get("customer_database_id") is not None else 0,
            client_name=row.get("client_name"),
            database_name=row.get("database_name"),
            plate=plate,
            technical_number=row.get("technical_number"),
            engine_name=row.get("engine_name"),
            username=str(row.get("username") or "").strip(),
            password=str(row.get("password") or "").strip(),
            provider_config=row.get("provider_config") if isinstance(row.get("provider_config"), dict) else {},
        )
    return targets


def preview_cpk_cutoffs(payload: CpkCutoffPreviewRequest) -> CpkCutoffPreviewResponse:
    """
    Calcula una previsualizacion de cortes CPK/CPH por tanqueo sin persistir
    registros mensuales. Las fechas sin zona horaria se interpretan en Colombia.
    """
    selected_clients = {str(name).strip() for name in payload.client_names if str(name).strip()}
    normalized_rows = [
        (
            _normalize_cpk_plate(row.plate),
            row.cutoff_start_at,
            row.cutoff_end_at,
        )
        for row in payload.rows
    ]
    plates = sorted({plate for plate, _, _ in normalized_rows if plate})

    with psycopg.connect(_database_dsn(), row_factory=dict_row) as conn:
        _ensure_performance_tables(conn)
        targets_by_plate = _fetch_cpk_targets_by_plate(conn, plates=plates)
        bindings = _load_binding_map(conn, [target for target in targets_by_plate.values() if target.customer_database_id])

    api_cache: dict[tuple[str, str, str], Any] = {}
    response_rows: list[CpkCutoffPreviewRow] = []

    for plate, raw_start, raw_end in normalized_rows:
        base = {
            "plate": plate,
            "cutoff_start_at": raw_start,
            "cutoff_end_at": raw_end,
        }

        try:
            start_dt = _parse_cpk_cutoff_datetime(raw_start)
            end_dt = _parse_cpk_cutoff_datetime(raw_end)
        except ValueError as exc:
            response_rows.append(
                CpkCutoffPreviewRow(**base, status="invalid_date", warnings=[str(exc)])
            )
            continue

        start_utc = _format_geotab_utc(start_dt)
        end_utc = _format_geotab_utc(end_dt)
        base["cutoff_start_utc"] = start_utc
        base["cutoff_end_utc"] = end_utc

        if end_dt <= start_dt:
            response_rows.append(
                CpkCutoffPreviewRow(
                    **base,
                    status="invalid_range",
                    warnings=["La fecha de tanqueo actual debe ser posterior al tanqueo anterior."],
                )
            )
            continue

        target = targets_by_plate.get(plate)
        if target is None or not target.customer_database_id:
            response_rows.append(
                CpkCutoffPreviewRow(**base, status="not_found", warnings=["Placa no encontrada con cliente/database asignado."])
            )
            continue

        target_info = {
            "client_name": target.client_name,
            "database_name": target.database_name,
            "source_provider": target.provider_key,
        }
        if selected_clients and (target.client_name or "Sin cliente") not in selected_clients:
            response_rows.append(
                CpkCutoffPreviewRow(
                    **base,
                    **target_info,
                    status="client_not_selected",
                    warnings=["La placa pertenece a un cliente no seleccionado para el export."],
                )
            )
            continue

        if target.provider_key != "geotab":
            response_rows.append(
                CpkCutoffPreviewRow(
                    **base,
                    **target_info,
                    status="not_geotab",
                    warnings=["El corte por tanqueo solo esta disponible para databases Geotab."],
                )
            )
            continue

        if not target.username or not target.password or not target.database_name:
            response_rows.append(
                CpkCutoffPreviewRow(
                    **base,
                    **target_info,
                    status="error",
                    warnings=["La database Geotab no tiene credenciales completas."],
                )
            )
            continue

        try:
            api_key = (target.username, target.password, target.database_name)
            api = api_cache.get(api_key)
            if api is None:
                api = get_authenticated_client(target.username, target.password, target.database_name)
                api_cache[api_key] = api

            binding = bindings.get((target.provider_key, target.customer_database_id, target.plate))
            device_id = binding.provider_vehicle_id if binding and binding.is_manual else None
            if not device_id:
                device = find_device_by_plate(api, target.plate, plate_prefix=target.provider_config.get("plate_prefix"))
                device_id = str(device.get("id") or "").strip() if device else None
            if not device_id:
                response_rows.append(
                    CpkCutoffPreviewRow(
                        **base,
                        **target_info,
                        status="not_found",
                        warnings=["No fue posible resolver el dispositivo en Geotab para esta placa."],
                    )
                )
                continue

            record = _calculate_geotab_vehicle_record(
                target=target,
                month=payload.month,
                device_id=device_id,
                api=api,
                from_date=start_utc,
                to_date=end_utc,
                previous_record=None,
            )
            status = "valid" if record.calculation_status in {"calculated", "partial"} else "error"
            warnings = list(record.warnings or [])
            if record.calculation_status not in {"calculated", "partial"}:
                warnings.append(f"Geotab retorno estado {record.calculation_status}.")
            response_rows.append(
                CpkCutoffPreviewRow(
                    **base,
                    **target_info,
                    provider_vehicle_id=device_id,
                    status=status,
                    warnings=warnings,
                    odo_start=record.odo_start,
                    odo_end=record.odo_end,
                    horo_start=record.horo_start,
                    horo_end=record.horo_end,
                    kms_ecm=record.kms_ecm,
                    kms_gps=record.kms_gps,
                    hours_ecm=record.hours_ecm,
                    hours_gps=record.hours_gps,
                    fuel_gallons=record.fuel_gallons,
                )
            )
        except Exception as exc:
            response_rows.append(
                CpkCutoffPreviewRow(
                    **base,
                    **target_info,
                    status="error",
                    warnings=[f"Error consultando Geotab: {exc}"],
                )
            )

    return CpkCutoffPreviewResponse(month=payload.month, rows=response_rows)


def _load_existing_records(
    conn: psycopg.Connection,
    month: str,
    targets: list[PerformanceTarget],
) -> dict[tuple[int, str], MonthlyPerformanceRecord]:
    if not targets:
        return {}
    database_ids = sorted({target.customer_database_id for target in targets})
    plates = sorted({target.plate for target in targets})
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT
                mp.customer_id,
                mp.customer_database_id,
                c.name AS client_name,
                cd.database_name,
                mp.source_provider,
                mp.plate,
                mp.provider_vehicle_id,
                mp.technical_number,
                mp.engine_name,
                mp.period_month,
                mp.odo_start,
                mp.odo_end,
                mp.horo_start,
                mp.horo_end,
                mp.kms_ecm,
                mp.kms_gps,
                mp.hours_ecm,
                mp.hours_gps,
                mp.fuel_gallons,
                mp.calculation_status,
                mp.warnings,
                mp.calculated_at
            FROM monthly_vehicle_performance mp
            LEFT JOIN customers c
                ON c.id = mp.customer_id
            LEFT JOIN customer_databases cd
                ON cd.id = mp.customer_database_id
            WHERE mp.period_month = %s
              AND mp.customer_database_id = ANY(%s)
              AND mp.plate = ANY(%s);
            """,
            (month, database_ids, plates),
        )
        rows = cur.fetchall()

    return {
        (int(row["customer_database_id"]), str(row["plate"])): _build_record(row)
        for row in rows
    }


def _load_binding_map(
    conn: psycopg.Connection,
    targets: list[PerformanceTarget],
) -> dict[tuple[str, int, str], BindingSnapshot]:
    if not targets:
        return {}
    database_ids = sorted({target.customer_database_id for target in targets})
    plates = sorted({target.plate for target in targets})
    providers = sorted({target.provider_key for target in targets})
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT provider, customer_database_id, plate, provider_vehicle_id, binding_status, is_manual
            FROM vehicle_provider_bindings
            WHERE provider = ANY(%s)
              AND customer_database_id = ANY(%s)
              AND plate = ANY(%s);
            """,
            (providers, database_ids, plates),
        )
        rows = cur.fetchall()
    return {
        (str(row["provider"]), int(row["customer_database_id"]), str(row["plate"])): BindingSnapshot(
            provider_vehicle_id=row.get("provider_vehicle_id"),
            binding_status=str(row.get("binding_status") or "unknown"),
            is_manual=bool(row.get("is_manual")),
        )
        for row in rows
    }

def _upsert_binding(
    conn: psycopg.Connection,
    *,
    target: PerformanceTarget,
    provider_vehicle_id: str | None,
    binding_status: str,
    last_error: str | None,
) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO vehicle_provider_bindings (
                plate,
                customer_database_id,
                provider,
                provider_vehicle_id,
                provider_plate,
                provider_customer_id,
                binding_status,
                last_resolved_at,
                last_error,
                updated_at
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, NOW(), %s, NOW())
            ON CONFLICT (plate, customer_database_id, provider)
            DO UPDATE SET
                provider_vehicle_id = CASE
                    WHEN vehicle_provider_bindings.is_manual THEN vehicle_provider_bindings.provider_vehicle_id
                    ELSE EXCLUDED.provider_vehicle_id
                END,
                provider_plate = EXCLUDED.provider_plate,
                provider_customer_id = EXCLUDED.provider_customer_id,
                binding_status = CASE
                    WHEN vehicle_provider_bindings.is_manual THEN 'resolved'
                    ELSE EXCLUDED.binding_status
                END,
                last_resolved_at = EXCLUDED.last_resolved_at,
                last_error = CASE
                    WHEN vehicle_provider_bindings.is_manual THEN NULL
                    ELSE EXCLUDED.last_error
                END,
                updated_at = NOW();
            """,
            (
                target.plate,
                target.customer_database_id,
                target.provider_key,
                provider_vehicle_id,
                target.plate,
                target.provider_config.get("customer_id"),
                binding_status,
                last_error,
            ),
        )


def _upsert_monthly_record(
    conn: psycopg.Connection,
    record: MonthlyPerformanceRecord,
) -> MonthlyPerformanceRecord:
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            INSERT INTO monthly_vehicle_performance (
                customer_id,
                customer_database_id,
                plate,
                period_month,
                source_provider,
                provider_vehicle_id,
                technical_number,
                engine_name,
                odo_start,
                odo_end,
                horo_start,
                horo_end,
                kms_ecm,
                kms_gps,
                hours_ecm,
                hours_gps,
                fuel_gallons,
                calculation_status,
                warnings,
                is_adhoc,
                calculated_at,
                updated_at
            )
            VALUES (
                %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s, NOW(), NOW()
            )
            ON CONFLICT (customer_database_id, plate, period_month)
            DO UPDATE SET
                customer_id = EXCLUDED.customer_id,
                source_provider = EXCLUDED.source_provider,
                provider_vehicle_id = EXCLUDED.provider_vehicle_id,
                technical_number = EXCLUDED.technical_number,
                engine_name = EXCLUDED.engine_name,
                odo_start = EXCLUDED.odo_start,
                odo_end = EXCLUDED.odo_end,
                horo_start = EXCLUDED.horo_start,
                horo_end = EXCLUDED.horo_end,
                kms_ecm = EXCLUDED.kms_ecm,
                kms_gps = EXCLUDED.kms_gps,
                hours_ecm = EXCLUDED.hours_ecm,
                hours_gps = EXCLUDED.hours_gps,
                fuel_gallons = EXCLUDED.fuel_gallons,
                calculation_status = EXCLUDED.calculation_status,
                warnings = EXCLUDED.warnings,
                is_adhoc = EXCLUDED.is_adhoc,
                calculated_at = NOW(),
                updated_at = NOW()
            RETURNING
                customer_id,
                customer_database_id,
                %s AS client_name,
                %s AS database_name,
                source_provider,
                plate,
                provider_vehicle_id,
                technical_number,
                engine_name,
                period_month,
                odo_start,
                odo_end,
                horo_start,
                horo_end,
                kms_ecm,
                kms_gps,
                hours_ecm,
                hours_gps,
                fuel_gallons,
                calculation_status,
                warnings,
                is_adhoc,
                calculated_at;
            """,
            (
                record.customer_id,
                record.customer_database_id,
                record.plate,
                record.period_month,
                record.source_provider,
                record.provider_vehicle_id,
                record.technical_number,
                record.engine_name,
                record.odo_start,
                record.odo_end,
                record.horo_start,
                record.horo_end,
                record.kms_ecm,
                record.kms_gps,
                record.hours_ecm,
                record.hours_gps,
                record.fuel_gallons,
                record.calculation_status,
                Jsonb(record.warnings),
                record.is_adhoc,
                record.client_name,
                record.database_name,
            ),
        )
        row = cur.fetchone()

    if row is None:
        raise RuntimeError("No fue posible guardar el corte mensual.")
    return _build_record(row)

def _build_summary(rows: list[MonthlyPerformanceRecord]) -> MonthlyPerformanceSummary:
    counter = Counter(row.calculation_status for row in rows)
    return MonthlyPerformanceSummary(
        total=len(rows),
        calculated=counter.get("calculated", 0),
        partial=counter.get("partial", 0),
        unbound=counter.get("unbound", 0),
        no_data=counter.get("no_data", 0),
        error=counter.get("error", 0),
    )


def calculate_monthly_performance(
    payload: MonthlyPerformanceCalculateRequest,
    progress_callback: Callable[[int, int], None] | None = None,
) -> MonthlyPerformanceResponse:
    month, year, month_number = _normalize_month(payload.month)
    previous_month = _previous_month(month)

    def _emit_progress(processed: int, total: int) -> None:
        if progress_callback is None:
            return
        try:
            progress_callback(processed, total)
        except Exception:
            # No queremos que un fallo del callback rompa el calculo
            pass

    with psycopg.connect(_database_dsn(), row_factory=dict_row) as conn:
        _ensure_performance_tables(conn)
        targets: list[PerformanceTarget] = []
        if not payload.adhoc_only:
            targets = _fetch_targets(
                conn,
                customer_id=payload.customer_id,
                customer_ids=payload.customer_ids,
                customer_database_id=payload.customer_database_id,
            )

        adhoc_db_id: int | None = None
        if payload.include_adhoc:
            geotab_cfg = load_geotab_config()
            sys_cust_id, sys_db_id = _ensure_system_database(conn)
            adhoc_db_id = sys_db_id
            adhoc_targets = _fetch_adhoc_targets(
                conn,
                system_customer_id=sys_cust_id,
                system_database_id=sys_db_id,
                geotab_username=geotab_cfg.username,
                geotab_password=geotab_cfg.password,
                geotab_database=geotab_cfg.database,
                adhoc_plates=payload.adhoc_plates or None,
                adhoc_filters=payload.adhoc_filters or None,
            )
            targets = targets + adhoc_targets

        if not targets:
            _emit_progress(0, 0)
            return MonthlyPerformanceResponse(month=month, summary=MonthlyPerformanceSummary(), rows=[])

        existing_records = _load_existing_records(conn, month, targets)
        previous_records = _load_existing_records(conn, previous_month, targets)
        bindings = _load_binding_map(conn, targets)

        grouped_targets: dict[tuple[str, int], list[PerformanceTarget]] = defaultdict(list)
        rows: list[MonthlyPerformanceRecord] = []

        total_targets = len(targets)
        processed_targets = 0
        _emit_progress(processed_targets, total_targets)

        for target in targets:
            key = (target.customer_database_id, target.plate)
            if not payload.force_recalculate and key in existing_records:
                rows.append(existing_records[key])
                processed_targets += 1
                continue
            grouped_targets[(target.provider_key, target.customer_database_id)].append(target)

        _emit_progress(processed_targets, total_targets)

        for (provider_key, _database_id), database_targets in grouped_targets.items():
            sample_target = database_targets[0]
            is_adhoc_group = adhoc_db_id is not None and _database_id == adhoc_db_id
            provider = get_monthly_performance_provider(provider_key)
            if provider is None:
                for target in database_targets:
                    saved = _upsert_monthly_record(
                        conn,
                        MonthlyPerformanceRecord(
                            customer_id=target.customer_id,
                            customer_database_id=target.customer_database_id,
                            client_name=target.client_name,
                            database_name=target.database_name,
                            source_provider=target.provider_key,
                            plate=target.plate,
                            technical_number=target.technical_number,
                            engine_name=target.engine_name,
                            period_month=month,
                            calculation_status="error",
                            warnings=[f"El proveedor {target.provider_key} aun no tiene adapter de rendimientos."],
                            is_adhoc=is_adhoc_group,
                        ),
                    )
                    rows.append(saved)
                    processed_targets += 1
                _emit_progress(processed_targets, total_targets)
                continue

            # Contador en vivo: el provider llama on_target_done() despues de
            # cada placa procesada. Asi el front ve avance fino.
            in_flight_processed = {"value": 0}

            def _bump_target_done() -> None:
                in_flight_processed["value"] += 1
                _emit_progress(processed_targets + in_flight_processed["value"], total_targets)

            try:
                provider_result = provider.calculate_database_rows(
                    month=month,
                    year=year,
                    month_number=month_number,
                    previous_month=previous_month,
                    targets=database_targets,
                    previous_records=previous_records,
                    bindings=bindings,
                    on_target_done=_bump_target_done,
                )
            except Exception as exc:
                for target in database_targets:
                    saved = _upsert_monthly_record(
                        conn,
                        MonthlyPerformanceRecord(
                            customer_id=target.customer_id,
                            customer_database_id=target.customer_database_id,
                            client_name=target.client_name,
                            database_name=target.database_name,
                            source_provider=target.provider_key,
                            plate=target.plate,
                            technical_number=target.technical_number,
                            engine_name=target.engine_name,
                            period_month=month,
                            calculation_status="error",
                            warnings=[f"No fue posible consultar {sample_target.provider_key}: {exc}"],
                            is_adhoc=is_adhoc_group,
                        ),
                    )
                    rows.append(saved)
                    processed_targets += 1
                conn.commit()
                _emit_progress(processed_targets, total_targets)
                continue

            for binding_update in provider_result.binding_updates:
                _upsert_binding(
                    conn,
                    target=binding_update.target,
                    provider_vehicle_id=binding_update.provider_vehicle_id,
                    binding_status=binding_update.binding_status,
                    last_error=binding_update.last_error,
                )

            for record in provider_result.records:
                if is_adhoc_group:
                    record = record.model_copy(update={"is_adhoc": True})
                rows.append(_upsert_monthly_record(conn, record))

            # Reconciliacion: el provider pudo haber bumpeado per-placa (in_flight)
            # o haber retornado temprano sin bumpear (early-return de error). Usamos
            # el numero real de records para no contar dos veces ni undercount.
            processed_targets += max(len(provider_result.records), in_flight_processed["value"])
            _emit_progress(processed_targets, total_targets)

            # Commit per-database: libera los row locks de monthly_vehicle_performance
            # y vehicle_provider_bindings entre databases para que otras consultas
            # (UI listando rendimientos, vehiculos, etc.) no se queden esperando.
            conn.commit()

        conn.commit()

    rows.sort(key=lambda row: ((row.client_name or "").lower(), (row.database_name or "").lower(), row.plate))
    return MonthlyPerformanceResponse(month=month, summary=_build_summary(rows), rows=rows)


_STATUS_PRIORITY = {"error": 0, "unbound": 1, "no_data": 2, "partial": 3, "calculated": 4}


def list_monthly_performance(
    *,
    month_from: str,
    month_to: str,
    customer_id: int | None = None,
    customer_ids: list[int] | None = None,
    customer_database_id: int | None = None,
    plate_search: str | None = None,
    motor_group: str | None = None,
) -> MonthlyPerformanceResponse:
    norm_from, _, _ = _normalize_month(month_from)
    norm_to, _, _ = _normalize_month(month_to)
    if norm_to < norm_from:
        norm_from, norm_to = norm_to, norm_from
    is_range = norm_from != norm_to
    normalized_plate = (plate_search or "").strip().upper()
    normalized_motor_group = (motor_group or "").strip()

    params: list[Any] = [norm_from, norm_to]
    where_clauses = ["mp.period_month >= %s", "mp.period_month <= %s"]
    effective_customer_ids = sorted(
        {
            int(value)
            for value in ([customer_id] if customer_id is not None else []) + (customer_ids or [])
            if value is not None
        }
    )
    if effective_customer_ids:
        where_clauses.append("mp.customer_id = ANY(%s)")
        params.append(effective_customer_ids)
    if customer_database_id is not None:
        where_clauses.append("mp.customer_database_id = %s")
        params.append(customer_database_id)
    if normalized_plate:
        where_clauses.append("UPPER(mp.plate) LIKE %s")
        params.append(f"%{normalized_plate}%")
    if normalized_motor_group:
        where_clauses.append("COALESCE(mp.engine_name, '') = %s")
        params.append(normalized_motor_group)

    with psycopg.connect(_database_dsn(), row_factory=dict_row) as conn:
        _ensure_performance_tables(conn)
        with conn.cursor() as cur:
            if not is_range:
                cur.execute(
                    f"""
                    SELECT
                        mp.customer_id,
                        mp.customer_database_id,
                        c.name AS client_name,
                        cd.database_name,
                        mp.source_provider,
                        mp.plate,
                        mp.provider_vehicle_id,
                        mp.technical_number,
                        mp.engine_name,
                        mp.period_month,
                        mp.odo_start,
                        mp.odo_end,
                        mp.horo_start,
                        mp.horo_end,
                        mp.kms_ecm,
                        mp.kms_gps,
                        mp.hours_ecm,
                        mp.hours_gps,
                        mp.fuel_gallons,
                        mp.calculation_status,
                        mp.warnings,
                        mp.is_adhoc,
                        mp.calculated_at,
                        a.vin,
                        a.cpl,
                        a.marca,
                        a.linea,
                        a.ano_modelo,
                        a.tipo_combustible,
                        a.nombre_vehiculo,
                        a.vocacional,
                        COALESCE(a.category, c.category, 'Ninguna') AS category
                    FROM monthly_vehicle_performance mp
                    LEFT JOIN customers c
                        ON c.id = mp.customer_id
                    LEFT JOIN customer_databases cd
                        ON cd.id = mp.customer_database_id
                    LEFT JOIN vehicle_motor_assignments a
                        ON a.plate = mp.plate
                    WHERE {" AND ".join(where_clauses)}
                    ORDER BY c.name ASC NULLS LAST, cd.database_name ASC NULLS LAST, mp.plate ASC;
                    """,
                    params,
                )
                rows = [_build_record(row) for row in cur.fetchall()]
            else:
                cur.execute(
                    f"""
                    SELECT
                        mp.customer_id,
                        mp.customer_database_id,
                        c.name AS client_name,
                        cd.database_name,
                        (ARRAY_AGG(mp.source_provider ORDER BY mp.period_month DESC))[1] AS source_provider,
                        mp.plate,
                        (ARRAY_AGG(mp.provider_vehicle_id ORDER BY mp.period_month DESC))[1] AS provider_vehicle_id,
                        mp.technical_number,
                        mp.engine_name,
                        MIN(mp.period_month) AS period_month,
                        (ARRAY_AGG(mp.odo_start ORDER BY mp.period_month ASC)
                            FILTER (WHERE mp.odo_start IS NOT NULL))[1] AS odo_start,
                        (ARRAY_AGG(mp.odo_end ORDER BY mp.period_month DESC)
                            FILTER (WHERE mp.odo_end IS NOT NULL))[1] AS odo_end,
                        (ARRAY_AGG(mp.horo_start ORDER BY mp.period_month ASC)
                            FILTER (WHERE mp.horo_start IS NOT NULL))[1] AS horo_start,
                        (ARRAY_AGG(mp.horo_end ORDER BY mp.period_month DESC)
                            FILTER (WHERE mp.horo_end IS NOT NULL))[1] AS horo_end,
                        SUM(mp.kms_ecm) AS kms_ecm,
                        SUM(mp.kms_gps) AS kms_gps,
                        SUM(mp.hours_ecm) AS hours_ecm,
                        SUM(mp.hours_gps) AS hours_gps,
                        SUM(mp.fuel_gallons) AS fuel_gallons,
                        CASE
                            WHEN BOOL_OR(mp.calculation_status = 'error') THEN 'error'
                            WHEN BOOL_OR(mp.calculation_status = 'unbound') THEN 'unbound'
                            WHEN BOOL_OR(mp.calculation_status = 'no_data') THEN 'no_data'
                            WHEN BOOL_OR(mp.calculation_status = 'partial') THEN 'partial'
                            ELSE 'calculated'
                        END AS calculation_status,
                        MAX(mp.calculated_at) AS calculated_at,
                        BOOL_OR(mp.is_adhoc) AS is_adhoc,
                        a.vin,
                        a.cpl,
                        a.marca,
                        a.linea,
                        a.ano_modelo,
                        a.tipo_combustible,
                        a.nombre_vehiculo,
                        COALESCE(a.category, c.category, 'Ninguna') AS category
                    FROM monthly_vehicle_performance mp
                    LEFT JOIN customers c
                        ON c.id = mp.customer_id
                    LEFT JOIN customer_databases cd
                        ON cd.id = mp.customer_database_id
                    LEFT JOIN vehicle_motor_assignments a
                        ON a.plate = mp.plate
                    WHERE {" AND ".join(where_clauses)}
                    GROUP BY mp.customer_id, mp.customer_database_id, c.name, cd.database_name,
                             mp.plate, mp.technical_number, mp.engine_name,
                             a.vin, a.cpl, a.marca, a.linea, a.ano_modelo, a.tipo_combustible, a.nombre_vehiculo,
                             a.category, c.category
                    ORDER BY c.name ASC NULLS LAST, cd.database_name ASC NULLS LAST, mp.plate ASC;
                    """,
                    params,
                )
                rows = []
                for row in cur.fetchall():
                    row["warnings"] = []
                    rows.append(_build_record(row))

    return MonthlyPerformanceResponse(
        month=norm_from,
        month_from=norm_from,
        month_to=norm_to,
        summary=_build_summary(rows),
        rows=rows,
    )


def list_adhoc_filter_options() -> dict[str, Any]:
    """
    Retorna los valores unicos de filtro para vehiculos sin customer_database_id.
    """
    with psycopg.connect(_database_dsn(), row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    COUNT(*) AS total,
                    ARRAY_AGG(DISTINCT marca ORDER BY marca) FILTER (WHERE marca IS NOT NULL) AS marcas,
                    ARRAY_AGG(DISTINCT linea ORDER BY linea) FILTER (WHERE linea IS NOT NULL) AS lineas,
                    ARRAY_AGG(DISTINCT nombre_vehiculo ORDER BY nombre_vehiculo) FILTER (WHERE nombre_vehiculo IS NOT NULL) AS nombres,
                    ARRAY_AGG(DISTINCT tipo_combustible ORDER BY tipo_combustible) FILTER (WHERE tipo_combustible IS NOT NULL) AS tipos_combustible
                FROM vehicle_motor_assignments
                WHERE customer_database_id IS NULL;
                """,
            )
            row = cur.fetchone()

    return {
        "total": int(row["total"]) if row else 0,
        "marcas": list(row.get("marcas") or []) if row else [],
        "lineas": list(row.get("lineas") or []) if row else [],
        "nombres": list(row.get("nombres") or []) if row else [],
        "tipos_combustible": list(row.get("tipos_combustible") or []) if row else [],
    }
