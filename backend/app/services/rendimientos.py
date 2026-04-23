from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime
from typing import Any

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from app.schemas.vehicle import (
    MonthlyPerformanceCalculateRequest,
    MonthlyPerformanceRecord,
    MonthlyPerformanceResponse,
    MonthlyPerformanceSummary,
)
from app.services.motor_catalog import _database_dsn, _ensure_motor_tables
from app.services.performance_providers import get_monthly_performance_provider
from app.services.performance_types import BindingSnapshot, PerformanceTarget
from app.services.provider_registry import infer_provider_key, supports_monthly_performance


def _ensure_performance_tables(conn: psycopg.Connection) -> None:
    _ensure_motor_tables(conn)
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


def _build_record(row: dict[str, Any]) -> MonthlyPerformanceRecord:
    return MonthlyPerformanceRecord(
        customer_id=row.get("customer_id"),
        customer_database_id=int(row["customer_database_id"]),
        client_name=row.get("client_name"),
        database_name=row.get("database_name"),
        source_provider=str(row.get("source_provider") or "artimo"),
        plate=str(row["plate"]),
        provider_vehicle_id=row.get("provider_vehicle_id"),
        technical_number=row.get("technical_number"),
        engine_name=row.get("engine_name"),
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
        calculation_status=str(row["calculation_status"]),
        warnings=list(row.get("warnings") or []),
        calculated_at=row.get("calculated_at"),
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
                calculated_at,
                updated_at
            )
            VALUES (
                %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, NOW(), NOW()
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
) -> MonthlyPerformanceResponse:
    month, year, month_number = _normalize_month(payload.month)
    previous_month = _previous_month(month)

    with psycopg.connect(_database_dsn(), row_factory=dict_row) as conn:
        _ensure_performance_tables(conn)
        targets = _fetch_targets(
            conn,
            customer_id=payload.customer_id,
            customer_ids=payload.customer_ids,
            customer_database_id=payload.customer_database_id,
        )
        if not targets:
            return MonthlyPerformanceResponse(month=month, summary=MonthlyPerformanceSummary(), rows=[])

        existing_records = _load_existing_records(conn, month, targets)
        previous_records = _load_existing_records(conn, previous_month, targets)
        bindings = _load_binding_map(conn, targets)

        grouped_targets: dict[tuple[str, int], list[PerformanceTarget]] = defaultdict(list)
        rows: list[MonthlyPerformanceRecord] = []

        for target in targets:
            key = (target.customer_database_id, target.plate)
            if not payload.force_recalculate and key in existing_records:
                rows.append(existing_records[key])
                continue
            grouped_targets[(target.provider_key, target.customer_database_id)].append(target)

        for (provider_key, _database_id), database_targets in grouped_targets.items():
            sample_target = database_targets[0]
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
                        ),
                    )
                    rows.append(saved)
                continue
            try:
                provider_result = provider.calculate_database_rows(
                    month=month,
                    year=year,
                    month_number=month_number,
                    previous_month=previous_month,
                    targets=database_targets,
                    previous_records=previous_records,
                    bindings=bindings,
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
                        ),
                    )
                    rows.append(saved)
                conn.commit()
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
                rows.append(_upsert_monthly_record(conn, record))

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
                        mp.calculated_at
                    FROM monthly_vehicle_performance mp
                    LEFT JOIN customers c
                        ON c.id = mp.customer_id
                    LEFT JOIN customer_databases cd
                        ON cd.id = mp.customer_database_id
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
                        MAX(mp.calculated_at) AS calculated_at
                    FROM monthly_vehicle_performance mp
                    LEFT JOIN customers c
                        ON c.id = mp.customer_id
                    LEFT JOIN customer_databases cd
                        ON cd.id = mp.customer_database_id
                    WHERE {" AND ".join(where_clauses)}
                    GROUP BY mp.customer_id, mp.customer_database_id, c.name, cd.database_name,
                             mp.plate, mp.technical_number, mp.engine_name
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
