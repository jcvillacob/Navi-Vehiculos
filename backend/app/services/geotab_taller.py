"""
Webhook Geotab "En taller" + estado vivo en Redis + snapshot del mapa.

Ver docs/plan-mapa-taller-geotab.md.

Modulos:
- Limpieza del payload crudo de Geotab (consciente del campo).
- Parseo del timestamp (date + time + timezone) -> UTC.
- DDL idempotente de `geotab_taller_events` (mismo patron que
  `_ensure_motor_tables` para no bloquear la transaccion del caller).
- Resolucion del vehiculo por VIN (fallback placa) + filtro de categoria
  efectiva (Flota Administrada o Experiencia Superior; "Ninguna" se ignora).
- Maquina de estados en Redis por placa: in -> grace -> fuera.
- Persistencia del evento raw (auditoria) con flag `ignored`.
- Snapshot cacheado en Redis para el endpoint del mapa (TTL configurable).
- Limpieza lazy + sweep periodico de la gracia vencida.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import unicodedata
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import unquote_plus
from zoneinfo import ZoneInfo

import psycopg
import redis as redis_lib
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from app.core.config import settings


_logger = logging.getLogger(__name__)
_webhook_logger = logging.getLogger("app.geotab_taller.webhook")


# ─── Redis ────────────────────────────────────────────────────────────────


_REDIS_CLIENT: redis_lib.Redis | None = None


def _redis_client() -> redis_lib.Redis:
    """Singleton Redis siguiendo el patron de auth_service.py."""
    global _REDIS_CLIENT
    if _REDIS_CLIENT is None:
        _REDIS_CLIENT = redis_lib.from_url(settings.redis_url, decode_responses=True)
    return _REDIS_CLIENT


def _reset_redis_client_for_tests() -> None:
    global _REDIS_CLIENT
    _REDIS_CLIENT = None


# Claves de Redis
_KEY_STATE = "taller:state:{plate}"
_KEY_ACTIVE_SET = "taller:active"
_KEY_SNAPSHOT = "mapa:snapshot"
_KEY_SNAPSHOT_HASH = "mapa:snapshot:hash"
_KEY_SEEN = "taller:seen:{exception_id}"


# Estados validos para `taller:state:{plate}:status`
_STATUS_IN = "in"
_STATUS_GRACE = "grace"


# Razones de ignore (persistidas en PG y devueltas en la respuesta del webhook)
_IGNORE_VEHICLE_NOT_FOUND = "vehicle_not_found"
_IGNORE_CATEGORY_NINGUNA = "category_ninguna"
_IGNORE_EXIT_WITHOUT_STATE = "exit_without_state"
_IGNORE_UNKNOWN_RULE = "unknown_rule"
_IGNORE_NO_IDENTIFIER = "no_identifier"
_IGNORE_DUPLICATE = "duplicate"


# ─── Postgres DSN ─────────────────────────────────────────────────────────


def _database_dsn() -> str:
    raw = os.getenv("DATABASE_URL", "").strip()
    if not raw:
        raise RuntimeError("Missing required environment variable: DATABASE_URL")
    return raw.replace("postgresql+psycopg://", "postgresql://", 1)


_TABLE_BOOTSTRAPPED = False


def _ensure_taller_events_table() -> None:
    """
    DDL idempotente — igual patron que `_ensure_motor_tables`:
    corre la primera vez por proceso en una conexion propia para no
    retener locks dentro de la transaccion del caller.
    """
    global _TABLE_BOOTSTRAPPED
    if _TABLE_BOOTSTRAPPED:
        return
    own_conn = psycopg.connect(_database_dsn())
    try:
        with own_conn.cursor() as cur:
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS geotab_taller_events (
                    id              BIGSERIAL PRIMARY KEY,
                    plate           VARCHAR(10) NULL,
                    vin             TEXT NULL,
                    device_id       TEXT NULL,
                    rule_triggered  TEXT NOT NULL,
                    event_kind      TEXT NOT NULL
                        CHECK (event_kind IN ('enter', 'exit', 'unknown')),
                    zone_id         TEXT NULL,
                    zone_name       TEXT NULL,
                    latitude        DOUBLE PRECISION NULL,
                    longitude       DOUBLE PRECISION NULL,
                    odometer        TEXT NULL,
                    date_raw        TEXT NULL,
                    time_raw        TEXT NULL,
                    timezone_raw    TEXT NULL,
                    event_ts        TIMESTAMPTZ NOT NULL,
                    received_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    ignored         BOOLEAN NOT NULL DEFAULT FALSE,
                    ignore_reason   TEXT NULL,
                    raw_payload     JSONB NOT NULL
                );
                """
            )
            cur.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_geotab_taller_events_plate_ts
                    ON geotab_taller_events (plate, event_ts DESC);
                """
            )
            cur.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_geotab_taller_events_ts
                    ON geotab_taller_events (event_ts DESC);
                """
            )
        own_conn.commit()
    finally:
        own_conn.close()
    _TABLE_BOOTSTRAPPED = True


# ─── Limpieza del payload ─────────────────────────────────────────────────


_TEXT_FIELDS = {"rule_triggered", "zone_name", "date", "time", "timezone"}
# Campos donde `_` representa el separador decimal (lat, lng)
_DECIMAL_FIELDS = {"latitude", "longitude"}
# Campos donde `_` representa un espacio (texto con separadores)
_UNDERSCORE_AS_SPACE_FIELDS = {"odometer"}


def _underscore_to_space(value: str) -> str:
    """`_` -> ' ' y colapsa espacios repetidos, conservando guiones/puntos."""
    s = value.replace("_", " ")
    s = re.sub(r"\s+", " ", s)
    return s.strip()


def _clean_text(value: Any) -> str | None:
    if value is None:
        return None
    s = unquote_plus(str(value))
    return _underscore_to_space(s)


def _clean_decimal(value: Any) -> float | None:
    if value is None or value == "":
        return None
    s = str(value).replace("_", ".")
    s = s.replace(",", ".")
    try:
        return float(s)
    except (TypeError, ValueError):
        return None


def _clean_id(value: Any) -> str | None:
    """Ids internos (device_id, zone_id, exception_id): sin transformacion."""
    if value is None:
        return None
    return str(value).strip() or None


def _clean_payload(raw: dict[str, Any]) -> dict[str, Any]:
    """
    Barredora recursiva consciente del campo. Devuelve un dict con la misma
    estructura jerarquica del payload original (event_info / asset_info /
    telemetry_info) pero con valores limpios.
    """
    ev = raw.get("event_info") or {}
    asst = raw.get("asset_info") or {}
    tel = raw.get("telemetry_info") or {}

    cleaned: dict[str, Any] = {
        "event_info": {
            "exception_id": _clean_id(ev.get("exception_id")),
            "rule_triggered": _clean_text(ev.get("rule_triggered")) or "",
            "date": _clean_text(ev.get("date")) or "",
            "time": _clean_text(ev.get("time")) or "",
            "timezone": _clean_text(ev.get("timezone")) or "UTC",
        },
        "asset_info": {
            "device_id": _clean_id(asst.get("device_id")),
            "device_name": _clean_text(asst.get("device_name")),
            "vin": _clean_text(asst.get("vin")),
        },
        "telemetry_info": {
            "zone_id": _clean_id(tel.get("zone_id")),
            "zone_name": _clean_text(tel.get("zone_name")),
            "latitude": _clean_decimal(tel.get("latitude")),
            "longitude": _clean_decimal(tel.get("longitude")),
            "odometer": _clean_text(tel.get("odometer")),
        },
    }
    return cleaned


def _log_webhook_event(event: str, **fields: Any) -> None:
    payload = {
        "timestamp": datetime.now(tz=_UTC).isoformat(),
        "event": event,
        **fields,
    }
    _webhook_logger.info(
        json.dumps(payload, ensure_ascii=True, default=str, sort_keys=True)
    )


def _webhook_body_sha256(raw_body: bytes) -> str:
    return hashlib.sha256(raw_body).hexdigest()


def _webhook_payload_summary(
    *,
    cleaned: dict[str, Any],
    raw_body: bytes,
    body_sha256: str,
) -> dict[str, Any]:
    ev = cleaned["event_info"]
    asst = cleaned["asset_info"]
    tel = cleaned["telemetry_info"]
    return {
        "body_size": len(raw_body),
        "body_sha256": body_sha256,
        "exception_id": ev.get("exception_id"),
        "rule_triggered": ev.get("rule_triggered"),
        "date": ev.get("date"),
        "time": ev.get("time"),
        "timezone": ev.get("timezone"),
        "device_id": asst.get("device_id"),
        "device_name": asst.get("device_name"),
        "vin": asst.get("vin"),
        "zone_id": tel.get("zone_id"),
        "zone_name": tel.get("zone_name"),
        "latitude": tel.get("latitude"),
        "longitude": tel.get("longitude"),
    }


# ─── Timestamp ────────────────────────────────────────────────────────────


_BOGOTA = ZoneInfo("America/Bogota")
_UTC = timezone.utc

_DATE_FORMATS = (
    "%b, %d, %Y %I:%M:%S %p",
    "%B, %d, %Y %I:%M:%S %p",
    "%b %d, %Y %I:%M:%S %p",
    "%B %d, %Y %I:%M:%S %p",
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%dT%H:%M:%S",
)


def _parse_event_ts_utc(date_str: str, time_str: str, tz_str: str) -> datetime:
    """
    Combina date + time y devuelve un datetime tz-aware en UTC.
    Geotab reporta 'timezone: UTC' pero conservamos el campo por si cambia.
    """
    if not date_str or not time_str:
        raise ValueError("date/time vacios")
    ts = f"{date_str} {time_str}"
    last_err: Exception | None = None
    for fmt in _DATE_FORMATS:
        try:
            naive = datetime.strptime(ts, fmt)
            break
        except ValueError as exc:
            last_err = exc
    else:
        raise ValueError(f"Formato de fecha no reconocido: {ts!r}") from last_err
    # Geotab declara UTC por convencion. Si en el futuro llega otra tz, se
    # podria mapear con ZoneInfo(tz_str) y convertir.
    return naive.replace(tzinfo=_UTC)


# ─── Clasificacion enter / exit / unknown ─────────────────────────────────


def classify_rule(rule_triggered: str) -> str:
    """Devuelve 'enter' / 'exit' / 'unknown' segun la regla (limpio)."""
    rule = (rule_triggered or "").strip()
    if rule and rule == settings.geotab_taller_rule_enter:
        return "enter"
    if rule and rule == settings.geotab_taller_rule_exit:
        return "exit"
    return "unknown"


# ─── Resolucion del vehiculo ─────────────────────────────────────────────


def _normalize_identifier(identifier: str | None) -> str:
    if not identifier:
        return ""
    return unicodedata.normalize("NFKC", identifier).strip()


def resolve_vehicle(
    *,
    device_name: str | None,
    vin: str | None,
    database_ids: tuple[int, ...] | None = None,
) -> dict[str, Any] | None:
    """
    Resuelve el vehiculo en `vehicle_motor_assignments`:

    1. Por VIN (`a.vin` ILIKE al device_name o vin del payload).
    2. Fallback por placa (`a.plate` = UPPER(identifier)).

    Retorna dict con `plate`, `vin`, `geotab_device_id`, `category`
    (categoria efectiva: COALESCE(a.category, c.category, 'Ninguna')),
    `client_name`, `motor` (engine_name). None si no hay match.

    device_id NO se usa para matchear: se repite entre DBs de Geotab y por
    eso mismo el matching se hace por VIN/placa. Cuando `database_ids` se
    indica (la reconciliacion por polling), el match queda acotado a esas
    databases Geotab. El webhook no conoce la database origen y conserva el
    comportamiento global.
    """
    identifier = _normalize_identifier(device_name or vin)
    if not identifier:
        return None

    _ensure_motor_tables_safe()

    scope_sql = ""
    scope_params: tuple[Any, ...] = ()
    if database_ids:
        scope_sql = """
                  AND (
                      a.customer_database_id = ANY(%s)
                      OR a.geotab_customer_database_id = ANY(%s)
                  )
        """
        scope_params = (list(database_ids), list(database_ids))

    with psycopg.connect(_database_dsn(), row_factory=dict_row) as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                """
                SELECT a.plate, a.vin, a.geotab_device_id,
                       COALESCE(a.category, c.category, 'Ninguna') AS category,
                       c.name AS client_name,
                       m.engine_name AS motor
                FROM vehicle_motor_assignments a
                LEFT JOIN customers c ON c.id = a.customer_id
                LEFT JOIN motor_catalog m ON m.technical_number = a.technical_number
                WHERE a.vin ILIKE %s
                {scope_sql}
                LIMIT 1;
                """.format(scope_sql=scope_sql),
                (identifier, *scope_params),
            )
            row = cur.fetchone()
            if row is not None:
                return _normalize_vehicle_row(row)

            cur.execute(
                """
                SELECT a.plate, a.vin, a.geotab_device_id,
                       COALESCE(a.category, c.category, 'Ninguna') AS category,
                       c.name AS client_name,
                       m.engine_name AS motor
                FROM vehicle_motor_assignments a
                LEFT JOIN customers c ON c.id = a.customer_id
                LEFT JOIN motor_catalog m ON m.technical_number = a.technical_number
                WHERE a.plate = UPPER(%s)
                {scope_sql}
                LIMIT 1;
                """.format(scope_sql=scope_sql),
                (identifier, *scope_params),
            )
            row = cur.fetchone()
            if row is not None:
                return _normalize_vehicle_row(row)
    return None


def _normalize_vehicle_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "plate": str(row["plate"]).strip().upper(),
        "vin": row.get("vin"),
        "geotab_device_id": row.get("geotab_device_id"),
        "category": (row.get("category") or "Ninguna").strip() or "Ninguna",
        "client_name": row.get("client_name"),
        "motor": row.get("motor"),
    }


def _ensure_motor_tables_safe() -> None:
    """Wrapper perezoso para garantizar las tablas de motor sin locks largos."""
    from app.services.motor_catalog import _ensure_motor_tables  # noqa: WPS433

    with psycopg.connect(_database_dsn()) as conn:
        _ensure_motor_tables(conn)
        conn.commit()


# ─── Persistencia del evento raw (auditoria) ─────────────────────────────


def persist_event(
    *,
    cleaned: dict[str, Any],
    event_kind: str,
    event_ts_utc: datetime,
    plate: str | None,
    ignored: bool,
    ignore_reason: str | None,
    raw_payload: dict[str, Any],
) -> None:
    """Inserta una fila en `geotab_taller_events` (auditoria)."""
    _ensure_taller_events_table()
    ev = cleaned["event_info"]
    asst = cleaned["asset_info"]
    tel = cleaned["telemetry_info"]
    with psycopg.connect(_database_dsn()) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO geotab_taller_events (
                    plate, vin, device_id, rule_triggered, event_kind,
                    zone_id, zone_name, latitude, longitude, odometer,
                    date_raw, time_raw, timezone_raw, event_ts,
                    ignored, ignore_reason, raw_payload
                ) VALUES (
                    %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s,
                    %s, %s, %s, %s,
                    %s, %s, %s
                );
                """,
                (
                    plate,
                    asst.get("vin"),
                    asst.get("device_id"),
                    ev.get("rule_triggered"),
                    event_kind,
                    tel.get("zone_id"),
                    tel.get("zone_name"),
                    tel.get("latitude"),
                    tel.get("longitude"),
                    tel.get("odometer"),
                    ev.get("date"),
                    ev.get("time"),
                    ev.get("timezone"),
                    event_ts_utc,
                    ignored,
                    ignore_reason,
                    Jsonb(raw_payload),
                ),
            )
        conn.commit()


def _is_duplicate_exception(exception_id: str | None) -> bool:
    """
    Idempotencia por `exception_id` de Geotab (puede reenviar el mismo).

    Solo consulta: NO marca. El marcado ocurre al final del procesamiento
    exitoso con `_mark_exception_seen`, asi si el procesamiento falla
    a mitad de camino un reintento real de Geotab no se descarta.
    """
    if not exception_id:
        return False
    return _redis_client().exists(_KEY_SEEN.format(exception_id=exception_id)) == 1


def _mark_exception_seen(exception_id: str | None) -> None:
    """Marca el `exception_id` como visto (TTL 24h). Llamar tras exito."""
    if not exception_id:
        return
    try:
        _redis_client().setex(
            _KEY_SEEN.format(exception_id=exception_id), 24 * 3600, "1"
        )
    except Exception:
        _logger.exception("No se pudo marcar exception_id como visto")


# ─── Maquina de estados en Redis ──────────────────────────────────────────


def _state_key(plate: str) -> str:
    return _KEY_STATE.format(plate=plate.upper())


def _read_state(plate: str) -> dict[str, Any] | None:
    raw = _redis_client().hgetall(_state_key(plate))
    if not raw:
        return None
    return {k: v for k, v in raw.items()}


def _write_state(plate: str, fields: dict[str, Any], ttl_seconds: int | None) -> None:
    r = _redis_client()
    key = _state_key(plate)
    r.hset(key, mapping={k: ("" if v is None else str(v)) for k, v in fields.items()})
    if ttl_seconds and ttl_seconds > 0:
        r.expire(key, ttl_seconds)


def _delete_state(plate: str) -> None:
    r = _redis_client()
    r.delete(_state_key(plate))
    r.srem(_KEY_ACTIVE_SET, plate.upper())


def _add_to_active(plate: str) -> None:
    _redis_client().sadd(_KEY_ACTIVE_SET, plate.upper())


def _grace_ttl_seconds() -> int:
    return settings.taller_grace_hours * 3600 + 600  # +10min buffer


def _in_ttl_seconds() -> int:
    # Safety net: el webhook de Geotab NO dispara eventos periodicos mientras
    # el vehiculo esta dentro — solo un enter al cruzar y un exit al salir.
    # Por eso el TTL debe ser largo (30 dias por defecto). Solo limpia estados
    # abandonados si se pierde el exit Y nunca llega otro enter.
    return settings.taller_in_ttl_days * 24 * 3600


def apply_enter(
    *,
    plate: str,
    event_ts_utc: datetime,
    cleaned: dict[str, Any],
    vehicle: dict[str, Any],
) -> None:
    """
    Estado "in". Geotab solo dispara un enter al cruzar la geocerca — NO repite
    enters mientras el vehiculo esta dentro. Por eso un segundo enter significa
    que el vehiculo salio y volvio a entrar (missed exit):

    - Sin estado previo → fresh: enter_ts = event_ts.
    - Estado previo `in` → fresh start (missed exit): enter_ts = event_ts.
    - Estado previo `grace`:
      - Reingreso < TALLER_GRACE_HOURS tras exit_ts → conserva enter_ts
        (req #4: "sigue contando").
      - Gracia expirada (>= TALLER_GRACE_HOURS) → fresh: enter_ts = event_ts
        (req #8: "se quita del todo").
    """
    plate = plate.upper()
    tel = cleaned["telemetry_info"]
    r = _redis_client()
    existing = _read_state(plate)
    fields: dict[str, Any] = {
        "plate": plate,
        "vin": cleaned["asset_info"].get("vin") or vehicle.get("vin"),
        "device_id": cleaned["asset_info"].get("device_id"),
        "zone_id": tel.get("zone_id"),
        "zone_name": tel.get("zone_name"),
        "lat": tel.get("latitude"),
        "lng": tel.get("longitude"),
        "odometer": tel.get("odometer"),
        "category": vehicle["category"],
        "client_name": vehicle.get("client_name"),
        "motor": vehicle.get("motor"),
        "status": _STATUS_IN,
        "manual": "false",
        "hidden": "false",
        "last_event_ts": event_ts_utc.isoformat(),
    }

    is_reingreso = _is_reingreso_within_grace(existing, event_ts_utc)
    if is_reingreso:
        # Reingreso desde grace dentro de la ventana: conserva enter_ts.
        fields["enter_ts"] = existing["enter_ts"]
        fields["exit_ts"] = ""
    else:
        # Fresh: sin estado previo, estado `in` (missed exit), o grace expirada.
        fields["enter_ts"] = event_ts_utc.isoformat()
        fields["exit_ts"] = ""

    _write_state(plate, fields, ttl_seconds=_in_ttl_seconds())
    _add_to_active(plate)
    r.delete(_KEY_SNAPSHOT, _KEY_SNAPSHOT_HASH)


def _is_reingreso_within_grace(
    existing: dict[str, Any] | None, event_ts_utc: datetime
) -> bool:
    """
    Devuelve True solo si hay un reingreso legitimo desde `grace` dentro de la
    ventana (< TALLER_GRACE_HOURS). En ese caso conservamos el enter_ts original.

    - Sin estado previo → False (fresh).
    - Estado `in` → False (missed exit → fresh). Geotab no repite enters
      mientras el vehiculo esta dentro, asi que un segundo enter significa
      que salio y volvio a entrar.
    - Estado `grace` con reingreso < ventana → True (conserva enter_ts).
    - Estado `grace` con gracia expirada → False (fresh).
    """
    if not existing or not existing.get("enter_ts"):
        return False
    if existing.get("status") != _STATUS_GRACE:
        return False  # `in` → missed exit → fresh
    exit_ts_raw = existing.get("exit_ts")
    if not exit_ts_raw:
        return True  # grace sin exit_ts (inconsistente): conservar por seguridad
    try:
        exit_ts = datetime.fromisoformat(exit_ts_raw)
    except ValueError:
        return False
    if exit_ts.tzinfo is None:
        exit_ts = exit_ts.replace(tzinfo=_UTC)
    return (event_ts_utc - exit_ts) < timedelta(hours=settings.taller_grace_hours)


def apply_exit(*, plate: str, event_ts_utc: datetime) -> None:
    """
    Estado "grace" (sale del mapa pero se conserva 1h). Actualiza `exit_ts`.
    Si no existia estado previo -> registrar el ignore upstream; aqui
    solo actualizamos si hay estado.
    """
    plate = plate.upper()
    r = _redis_client()
    existing = _read_state(plate)
    if not existing:
        return
    fields = {
        "status": _STATUS_GRACE,
        "exit_ts": event_ts_utc.isoformat(),
        "last_event_ts": event_ts_utc.isoformat(),
    }
    _write_state(plate, fields, ttl_seconds=_grace_ttl_seconds())
    _add_to_active(plate)  # sigue activo (en gracia) para que la limpieza lo vea
    r.delete(_KEY_SNAPSHOT, _KEY_SNAPSHOT_HASH)


# ─── Operaciones manuales ─────────────────────────────────────────────────


_MANUAL_ACTIONS = ("add", "hide", "unhide", "close")


def manual_add(*, plate: str, enter_ts_utc: datetime | None = None) -> dict[str, Any]:
    """
    Agrega un vehiculo manualmente al mapa. Crea estado `in` con flag
    `manual=true`. Si ya existia estado, lo reemplaza (fresh manual).
    Un enter real posterior lo reemplaza (manual=false, fresh).
    """
    plate = plate.upper().strip()
    if not plate:
        raise ValueError("La placa es obligatoria")
    ts = enter_ts_utc or datetime.now(tz=_UTC)
    r = _redis_client()
    existing = _read_state(plate)
    # Coordenadas: si hay estado previo (Geotab las envio), las conservamos.
    # Si no, usamos un default (centro de Colombia) para que aparezca en el mapa.
    lat = existing.get("lat") if existing else ""
    lng = existing.get("lng") if existing else ""
    if not lat:
        lat = "4.5"
    if not lng:
        lng = "-75.0"
    fields: dict[str, Any] = {
        "plate": plate,
        "vin": existing.get("vin") if existing else "",
        "device_id": existing.get("device_id") if existing else "",
        "zone_id": existing.get("zone_id") if existing else "",
        "zone_name": existing.get("zone_name") if existing else "Manual",
        "lat": lat,
        "lng": lng,
        "odometer": existing.get("odometer") if existing else "",
        "category": existing.get("category") if existing else "",
        "client_name": existing.get("client_name") if existing else "",
        "motor": existing.get("motor") if existing else "",
        "status": _STATUS_IN,
        "manual": "true",
        "hidden": "false",
        "enter_ts": ts.isoformat(),
        "exit_ts": "",
        "last_event_ts": ts.isoformat(),
    }
    _write_state(plate, fields, ttl_seconds=_in_ttl_seconds())
    _add_to_active(plate)
    r.delete(_KEY_SNAPSHOT, _KEY_SNAPSHOT_HASH)
    return {"plate": plate, "action": "add", "manual": True}


def manual_hide(*, plate: str) -> dict[str, Any]:
    """Oculta un vehiculo del mapa sin eliminar su estado."""
    plate = plate.upper().strip()
    existing = _read_state(plate)
    if not existing:
        raise ValueError("El vehiculo no tiene estado activo")
    r = _redis_client()
    r.hset(_state_key(plate), mapping={"hidden": "true"})
    r.delete(_KEY_SNAPSHOT, _KEY_SNAPSHOT_HASH)
    return {"plate": plate, "action": "hide", "hidden": True}


def manual_unhide(*, plate: str) -> dict[str, Any]:
    """Quita el flag hidden de un vehiculo."""
    plate = plate.upper().strip()
    existing = _read_state(plate)
    if not existing:
        raise ValueError("El vehiculo no tiene estado activo")
    r = _redis_client()
    r.hset(_state_key(plate), mapping={"hidden": "false"})
    r.delete(_KEY_SNAPSHOT, _KEY_SNAPSHOT_HASH)
    return {"plate": plate, "action": "unhide", "hidden": False}


def manual_close(*, plate: str) -> dict[str, Any]:
    """Cierra/elimina el estado de un vehiculo por completo."""
    plate = plate.upper().strip()
    existing = _read_state(plate)
    if not existing:
        raise ValueError("El vehiculo no tiene estado activo")
    _delete_state(plate)
    _redis_client().delete(_KEY_SNAPSHOT, _KEY_SNAPSHOT_HASH)
    return {"plate": plate, "action": "close", "closed": True}


def apply_manual_action(*, plate: str, action: str, enter_ts_utc: datetime | None = None) -> dict[str, Any]:
    """Despachador de operaciones manuales."""
    if action not in _MANUAL_ACTIONS:
        raise ValueError(f"Acción inválida. Usa: {', '.join(_MANUAL_ACTIONS)}")
    if action == "add":
        return manual_add(plate=plate, enter_ts_utc=enter_ts_utc)
    if action == "hide":
        return manual_hide(plate=plate)
    if action == "unhide":
        return manual_unhide(plate=plate)
    if action == "close":
        return manual_close(plate=plate)
    raise ValueError(f"Acción no implementada: {action}")


# ─── Limpieza de gracia vencida ───────────────────────────────────────────


def _is_grace_expired(state: dict[str, Any], now_utc: datetime) -> bool:
    if state.get("status") != _STATUS_GRACE:
        return False
    exit_ts_raw = state.get("exit_ts")
    if not exit_ts_raw:
        return False
    try:
        exit_ts = datetime.fromisoformat(exit_ts_raw)
    except ValueError:
        return True
    if exit_ts.tzinfo is None:
        exit_ts = exit_ts.replace(tzinfo=_UTC)
    return (now_utc - exit_ts) >= timedelta(hours=settings.taller_grace_hours)


def sweep_expired_grace(now_utc: datetime | None = None) -> int:
    """
    Purga los estados `grace` cuya `exit_ts` supere la ventana. Devuelve el
    numero de placas purgadas. Llamado por el scheduler periodicamente y
    tambien de forma lazy antes de construir el snapshot.
    """
    now = now_utc or datetime.now(tz=_UTC)
    r = _redis_client()
    active = r.smembers(_KEY_ACTIVE_SET)
    if not active:
        return 0
    pipe = r.pipeline()
    for plate in active:
        pipe.hgetall(_state_key(plate))
    raw_states = pipe.execute()
    purged = 0
    orphans: list[str] = []
    for plate, state in zip(active, raw_states):
        if not state:
            orphans.append(plate)
            continue
        if _is_grace_expired(state, now):
            _delete_state(plate)
            purged += 1
    if orphans:
        r.srem(_KEY_ACTIVE_SET, *orphans)
    if purged:
        r.delete(_KEY_SNAPSHOT, _KEY_SNAPSHOT_HASH)
    return purged


# ─── Snapshot del mapa (con cache + ETag) ─────────────────────────────────


def _serialize_for_etag(payload: dict[str, Any]) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def _build_snapshot(now_utc: datetime) -> dict[str, Any]:
    """
    Construye el snapshot completo: vehiculos in >= 30 min + zonas derivadas.
    NO usa el cache — siempre recalcula. `get_mapa_snapshot_cacheable` aplica
    el cache.
    """
    # Limpieza lazy de gracia vencida antes de enumerar.
    sweep_expired_grace(now_utc)

    r = _redis_client()
    active = r.smembers(_KEY_ACTIVE_SET)
    if not active:
        # Aun sin vehiculos "in" puede haber salidas recientes que mostrar.
        exited = get_recent_exits(now_utc, in_plates=set())
        snap = _empty_snapshot(now_utc)
        snap["exited"] = exited
        snap["zones"] = _zones_from_exited(exited)
        return snap

    # Pipeline: una sola round-trip para todos los HGETALL.
    pipe = r.pipeline()
    for plate in active:
        pipe.hgetall(_state_key(plate))
    raw_states = pipe.execute()

    states: list[dict[str, Any]] = []
    for state in raw_states:
        if not state or state.get("status") != _STATUS_IN:
            continue
        if state.get("hidden") == "true":
            continue  # oculto manualmente: no aparece en el mapa
        enter_raw = state.get("enter_ts")
        if not enter_raw:
            continue
        try:
            enter_ts = datetime.fromisoformat(enter_raw)
        except ValueError:
            continue
        if enter_ts.tzinfo is None:
            enter_ts = enter_ts.replace(tzinfo=_UTC)
        minutes_inside = int((now_utc - enter_ts).total_seconds() // 60)
        if minutes_inside < settings.taller_min_minutes:
            continue
        try:
            lat = float(state["lat"]) if state.get("lat") else None
            lng = float(state["lng"]) if state.get("lng") else None
        except (TypeError, ValueError):
            lat = None
            lng = None
        if lat is None or lng is None:
            continue
        states.append(
            {
                "plate": state["plate"],
                "lat": lat,
                "lng": lng,
                "zone_id": state.get("zone_id"),
                "zone_name": state.get("zone_name"),
                "category": state.get("category") or "Ninguna",
                "client_name": state.get("client_name"),
                "motor": state.get("motor"),
                "enter_ts": enter_ts,
                "minutes_inside": minutes_inside,
                "odometer": state.get("odometer"),
                "manual": state.get("manual") == "true",
                "hidden": state.get("hidden") == "true",
            }
        )

    vehicles = [
        {
            "plate": s["plate"],
            "lat": s["lat"],
            "lng": s["lng"],
            "zone_id": s["zone_id"],
            "zone_name": s["zone_name"],
            "category": s["category"],
            "client_name": s["client_name"],
            "motor": s["motor"],
            "enter_ts_local": s["enter_ts"].astimezone(_BOGOTA).isoformat(),
            "minutes_inside": s["minutes_inside"],
            "odometer": s["odometer"],
            "manual": s["manual"],
        }
        for s in states
    ]

    # Salidas recientes (marcador atenuado en el mapa), excluyendo placas que
    # ahora mismo estan "in" para no duplicar.
    in_plates = {s["plate"] for s in states}
    exited = get_recent_exits(now_utc, in_plates=in_plates)

    # Zonas derivadas: una entrada por zone_id (coords del primer vehiculo).
    zones_map: dict[str, dict[str, Any]] = {}
    for s in states:
        zid = s["zone_id"]
        if not zid or zid in zones_map:
            continue
        zones_map[zid] = {
            "id": zid,
            "name": s["zone_name"] or zid,
            "lat": s["lat"],
            "lng": s["lng"],
        }
    # Incluir zonas de vehiculos salidos que no aparezcan ya (para que el mapa
    # las encuadre aunque el taller solo tenga salidas recientes).
    for e in exited:
        zid = e.get("zone_id")
        if not zid or zid in zones_map:
            continue
        zones_map[zid] = {
            "id": zid,
            "name": e.get("zone_name") or zid,
            "lat": e["lat"],
            "lng": e["lng"],
        }

    return {
        "generated_at": now_utc.astimezone(_BOGOTA).isoformat(),
        "vehicles": vehicles,
        "exited": exited,
        "zones": list(zones_map.values()),
    }


def _empty_snapshot(now_utc: datetime) -> dict[str, Any]:
    return {
        "generated_at": now_utc.astimezone(_BOGOTA).isoformat(),
        "vehicles": [],
        "exited": [],
        "zones": [],
    }


def _zones_from_exited(exited: list[dict[str, Any]]) -> list[dict[str, Any]]:
    zones_map: dict[str, dict[str, Any]] = {}
    for e in exited:
        zid = e.get("zone_id")
        if not zid or zid in zones_map:
            continue
        zones_map[zid] = {
            "id": zid,
            "name": e.get("zone_name") or zid,
            "lat": e["lat"],
            "lng": e["lng"],
        }
    return list(zones_map.values())


# ─── Historico enter/exit desde Postgres ─────────────────────────────────


def get_recent_exits(
    now_utc: datetime, *, in_plates: set[str], hours: int | None = None
) -> list[dict[str, Any]]:
    """
    Vehiculos cuyo ultimo evento en la ventana (`TALLER_EXITED_MAP_HOURS`) fue
    un `exit`: ya salieron pero se muestran atenuados en el mapa.

    Toma el evento mas reciente por placa (DISTINCT ON) y conserva solo los que
    son `exit` con coordenadas. Excluye las placas que ahora mismo estan `in`
    (`in_plates`) para no duplicar el marcador. Enriquecido con motor/cliente.
    """
    window_hours = hours if hours is not None else settings.taller_exited_map_hours
    since = now_utc - timedelta(hours=max(1, window_hours))
    _ensure_taller_events_table()
    with psycopg.connect(_database_dsn(), row_factory=dict_row) as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                """
                SELECT DISTINCT ON (e.plate)
                    e.plate, e.event_kind, e.event_ts,
                    e.zone_id, e.zone_name,
                    e.latitude AS lat, e.longitude AS lng,
                    COALESCE(a.category, c.category, 'Ninguna') AS category,
                    c.name AS client_name,
                    m.engine_name AS motor
                FROM geotab_taller_events e
                LEFT JOIN vehicle_motor_assignments a ON a.plate = e.plate
                LEFT JOIN customers c ON c.id = a.customer_id
                LEFT JOIN motor_catalog m
                       ON m.technical_number = a.technical_number
                WHERE e.ignored = FALSE
                  AND e.event_kind IN ('enter', 'exit')
                  AND e.plate IS NOT NULL
                  AND e.event_ts >= %s
                ORDER BY e.plate, e.event_ts DESC
                """,
                (since,),
            )
            rows = cur.fetchall()

    exited: list[dict[str, Any]] = []
    for row in rows:
        if row["event_kind"] != "exit":
            continue  # su ultimo evento fue enter -> sigue dentro
        plate = str(row["plate"]).strip().upper()
        if plate in in_plates:
            continue
        lat, lng = row.get("lat"), row.get("lng")
        if lat is None or lng is None:
            continue
        exit_ts = row["event_ts"]
        if exit_ts.tzinfo is None:
            exit_ts = exit_ts.replace(tzinfo=_UTC)
        exited.append(
            {
                "plate": plate,
                "lat": float(lat),
                "lng": float(lng),
                "zone_id": row.get("zone_id"),
                "zone_name": row.get("zone_name"),
                "category": (row.get("category") or "Ninguna"),
                "client_name": row.get("client_name"),
                "motor": row.get("motor"),
                "exit_ts_local": exit_ts.astimezone(_BOGOTA).isoformat(),
                "minutes_ago": int((now_utc - exit_ts).total_seconds() // 60),
            }
        )
    return exited


def get_taller_history(
    *,
    days: int | None = None,
    plate: str | None = None,
    zone_id: str | None = None,
    limit: int = 1000,
) -> dict[str, Any]:
    """
    Empareja eventos enter->exit en visitas para el modal de historico
    (ultimos `TALLER_HISTORY_DAYS`). Cada visita: placa, taller, entrada,
    salida, duracion en minutos.

    El emparejamiento es por placa en orden cronologico: un `enter` abre una
    visita; el siguiente `exit` la cierra. Un segundo `enter` sin `exit`
    intermedio cierra la anterior como abierta (missed exit) y abre otra.

    Solo se devuelven las visitas que cumplieron las condiciones de ingreso al
    mapa: categoria valida (ya garantizada por `ignored = FALSE`, que excluye
    los eventos de categoria "Ninguna") y permanencia completa >=
    `TALLER_MIN_MINUTES`. Visitas abiertas/huerfanas o mas cortas se descartan.
    """
    window_days = days if days is not None else settings.taller_history_days
    since = datetime.now(tz=_UTC) - timedelta(days=max(1, window_days))
    _ensure_taller_events_table()

    filters = ["e.ignored = FALSE", "e.event_kind IN ('enter', 'exit')",
               "e.plate IS NOT NULL", "e.event_ts >= %s"]
    params: list[Any] = [since]
    if plate:
        filters.append("e.plate = UPPER(%s)")
        params.append(plate.strip())
    if zone_id:
        filters.append("e.zone_id = %s")
        params.append(zone_id)

    with psycopg.connect(_database_dsn(), row_factory=dict_row) as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                f"""
                SELECT e.plate, e.event_kind, e.event_ts,
                       e.zone_id, e.zone_name,
                       COALESCE(a.category, c.category, 'Ninguna') AS category,
                       c.name AS client_name,
                       m.engine_name AS motor
                FROM geotab_taller_events e
                LEFT JOIN vehicle_motor_assignments a ON a.plate = e.plate
                LEFT JOIN customers c ON c.id = a.customer_id
                LEFT JOIN motor_catalog m
                       ON m.technical_number = a.technical_number
                WHERE {' AND '.join(filters)}
                ORDER BY e.plate, e.event_ts ASC
                """,
                params,
            )
            rows = cur.fetchall()

    def _aware(ts: datetime) -> datetime:
        return ts.replace(tzinfo=_UTC) if ts.tzinfo is None else ts

    visits: list[dict[str, Any]] = []
    open_enter: dict[str, Any] | None = None
    current_plate: str | None = None

    def _close(enter_row: dict[str, Any], exit_row: dict[str, Any] | None) -> None:
        enter_ts = _aware(enter_row["event_ts"])
        exit_ts = _aware(exit_row["event_ts"]) if exit_row else None
        minutes = (
            int((exit_ts - enter_ts).total_seconds() // 60)
            if exit_ts is not None
            else None
        )
        visits.append(
            {
                "plate": enter_row["plate"],
                "zone_id": enter_row.get("zone_id"),
                "zone_name": enter_row.get("zone_name")
                or (exit_row.get("zone_name") if exit_row else None),
                "client_name": enter_row.get("client_name"),
                "motor": enter_row.get("motor"),
                "category": (enter_row.get("category") or "Ninguna"),
                "enter_ts_local": enter_ts.astimezone(_BOGOTA).isoformat(),
                "exit_ts_local": (
                    exit_ts.astimezone(_BOGOTA).isoformat() if exit_ts else None
                ),
                "minutes_inside": minutes,
            }
        )

    for row in rows:
        if row["plate"] != current_plate:
            # Cambio de placa: cerrar visita abierta de la placa anterior.
            if open_enter is not None:
                _close(open_enter, None)
            open_enter = None
            current_plate = row["plate"]
        if row["event_kind"] == "enter":
            if open_enter is not None:
                _close(open_enter, None)  # missed exit
            open_enter = row
        else:  # exit
            if open_enter is not None:
                _close(open_enter, row)
                open_enter = None
            else:
                # exit huerfano: visita sin entrada conocida.
                exit_ts = _aware(row["event_ts"])
                visits.append(
                    {
                        "plate": row["plate"],
                        "zone_id": row.get("zone_id"),
                        "zone_name": row.get("zone_name"),
                        "client_name": row.get("client_name"),
                        "motor": row.get("motor"),
                        "category": (row.get("category") or "Ninguna"),
                        "enter_ts_local": None,
                        "exit_ts_local": exit_ts.astimezone(_BOGOTA).isoformat(),
                        "minutes_inside": None,
                    }
                )
    if open_enter is not None:
        _close(open_enter, None)

    # Solo visitas completas que cumplieron el umbral de permanencia del mapa.
    min_minutes = settings.taller_min_minutes
    qualified = [
        v
        for v in visits
        if v["exit_ts_local"] is not None
        and v["minutes_inside"] is not None
        and v["minutes_inside"] >= min_minutes
    ]

    # Orden global: mas reciente primero (por salida, luego entrada).
    def _sort_key(v: dict[str, Any]) -> str:
        return v.get("exit_ts_local") or v.get("enter_ts_local") or ""

    qualified.sort(key=_sort_key, reverse=True)
    return {
        "days": window_days,
        "count": len(qualified),
        "visits": qualified[:limit],
    }


def get_mapa_snapshot_cacheable() -> tuple[dict[str, Any], str]:
    """
    Devuelve (snapshot, etag). Usa cache Redis (TTL configurable) para que
    multiples viewers concurrentes no recomputen.
    """
    r = _redis_client()
    cached = r.get(_KEY_SNAPSHOT)
    cached_hash = r.get(_KEY_SNAPSHOT_HASH)
    if cached and cached_hash:
        try:
            return json.loads(cached), cached_hash
        except json.JSONDecodeError:
            pass

    snapshot = _build_snapshot(datetime.now(tz=_UTC))
    etag = hashlib.md5(_serialize_for_etag(snapshot).encode("utf-8")).hexdigest()
    ttl = max(1, settings.mapa_snapshot_ttl_seconds)
    r.setex(_KEY_SNAPSHOT, ttl, json.dumps(snapshot, separators=(",", ":")))
    r.setex(_KEY_SNAPSHOT_HASH, ttl, etag)
    return snapshot, etag


def invalidate_snapshot_cache() -> None:
    _redis_client().delete(_KEY_SNAPSHOT, _KEY_SNAPSHOT_HASH)


def _persist_raw_failure(raw_body: bytes, reason: str) -> None:
    """Guarda el body crudo en PG aunque el parseo falle, para no perder nada."""
    try:
        _ensure_taller_events_table()
        with psycopg.connect(_database_dsn()) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO geotab_taller_events (
                        rule_triggered, event_kind, event_ts,
                        ignored, ignore_reason, raw_payload
                    ) VALUES (
                        %s, %s, %s,
                        %s, %s, %s
                    );
                    """,
                    (
                        "(parse_failure)",
                        "unknown",
                        datetime.now(tz=_UTC),
                        True,
                        reason,
                        Jsonb({"_raw": raw_body.decode("utf-8", errors="replace")}),
                    ),
                )
            conn.commit()
    except Exception:
        _logger.exception("No se pudo persistir raw failure")


# ─── Orquestacion del webhook ────────────────────────────────────────────


def process_webhook(
    raw_body: bytes,
) -> dict[str, Any]:
    """
    Punto de entrada unico para el webhook de Geotab. Realiza:

    1. Parseo + limpieza + timestamp.
    2. Clasificacion de la regla.
    3. Idempotencia por `exception_id`.
    4. Resolucion del vehiculo + filtro de categoria.
    5. Persistencia del evento raw.
    6. Transicion de estado (enter/exit) o registro de ignore.

    Devuelve siempre un dict listo para serializar como respuesta del endpoint.
    Nunca lanza hacia afuera; cualquier error se traduce a `status=error`.
    """
    body_sha256 = _webhook_body_sha256(raw_body)
    log_context: dict[str, Any] = {
        "body_size": len(raw_body),
        "body_sha256": body_sha256,
    }

    def _finish(result: dict[str, Any]) -> dict[str, Any]:
        _log_webhook_event(
            "geotab_taller_webhook_processed",
            **log_context,
            result_status=result.get("status"),
            result_event_kind=result.get("event_kind"),
            result_plate=result.get("plate"),
            result_ignored=result.get("ignored"),
            result_reason=result.get("reason"),
            result_message=result.get("message"),
        )
        return result

    try:
        try:
            raw_payload = json.loads(raw_body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            _log_webhook_event(
                "geotab_taller_webhook_parse_failed",
                **log_context,
                reason=f"json_decode: {exc}",
            )
            _persist_raw_failure(raw_body, f"json_decode: {exc}")
            return _finish({
                "status": "error",
                "message": f"Payload no es JSON valido: {exc}",
            })

        cleaned = _clean_payload(raw_payload)
        log_context.update(
            _webhook_payload_summary(
                cleaned=cleaned,
                raw_body=raw_body,
                body_sha256=body_sha256,
            )
        )
        _log_webhook_event("geotab_taller_webhook_received", **log_context)
        ev = cleaned["event_info"]
        ts = _parse_event_ts_utc(ev["date"], ev["time"], ev["timezone"])
    except Exception as exc:  # noqa: BLE001
        _log_webhook_event(
            "geotab_taller_webhook_parse_failed",
            **log_context,
            reason=f"parse: {exc}",
        )
        _persist_raw_failure(raw_body, f"parse: {exc}")
        return _finish({"status": "error", "message": f"Payload invalido: {exc}"})

    try:
        event_kind = classify_rule(ev["rule_triggered"])

        # Idempotencia por exception_id: si ya vimos este id, lo ignoramos.
        # `_is_duplicate_exception` ademas marca el id en Redis (set NX, TTL 24h).
        exception_id = ev.get("exception_id")
        if exception_id and _is_duplicate_exception(exception_id):
            _persist_ignored(
                cleaned=cleaned,
                event_kind=event_kind,
                event_ts_utc=ts,
                plate=None,
                raw_payload=raw_payload,
                reason=_IGNORE_DUPLICATE,
            )
            return _finish({
                "status": "ok",
                "event_kind": event_kind,
                "ignored": True,
                "reason": _IGNORE_DUPLICATE,
            })

        if event_kind == "unknown":
            _persist_ignored(
                cleaned=cleaned,
                event_kind=event_kind,
                event_ts_utc=ts,
                plate=None,
                raw_payload=raw_payload,
                reason=_IGNORE_UNKNOWN_RULE,
            )
            _mark_exception_seen(exception_id)
            return _finish({
                "status": "ok",
                "event_kind": event_kind,
                "ignored": True,
                "reason": _IGNORE_UNKNOWN_RULE,
            })

        # Resolucion del vehiculo.
        asst = cleaned["asset_info"]
        identifier = _normalize_identifier(asst.get("device_name") or asst.get("vin"))
        if not identifier:
            _persist_ignored(
                cleaned=cleaned,
                event_kind=event_kind,
                event_ts_utc=ts,
                plate=None,
                raw_payload=raw_payload,
                reason=_IGNORE_NO_IDENTIFIER,
            )
            _mark_exception_seen(exception_id)
            return _finish({
                "status": "ok",
                "event_kind": event_kind,
                "ignored": True,
                "reason": _IGNORE_NO_IDENTIFIER,
            })

        vehicle = resolve_vehicle(device_name=asst.get("device_name"), vin=asst.get("vin"))
        if vehicle is None:
            _persist_ignored(
                cleaned=cleaned,
                event_kind=event_kind,
                event_ts_utc=ts,
                plate=None,
                raw_payload=raw_payload,
                reason=_IGNORE_VEHICLE_NOT_FOUND,
            )
            _mark_exception_seen(exception_id)
            return _finish({
                "status": "ok",
                "event_kind": event_kind,
                "ignored": True,
                "reason": _IGNORE_VEHICLE_NOT_FOUND,
            })

        if vehicle["category"] not in ("Flota Administrada", "Experiencia Superior"):
            _persist_ignored(
                cleaned=cleaned,
                event_kind=event_kind,
                event_ts_utc=ts,
                plate=vehicle["plate"],
                raw_payload=raw_payload,
                reason=_IGNORE_CATEGORY_NINGUNA,
            )
            _mark_exception_seen(exception_id)
            return _finish({
                "status": "ok",
                "event_kind": event_kind,
                "plate": vehicle["plate"],
                "ignored": True,
                "reason": _IGNORE_CATEGORY_NINGUNA,
            })

        # Aplica transicion.
        if event_kind == "enter":
            apply_enter(
                plate=vehicle["plate"],
                event_ts_utc=ts,
                cleaned=cleaned,
                vehicle=vehicle,
            )
        else:  # exit
            existing = _read_state(vehicle["plate"])
            if not existing:
                _persist_ignored(
                    cleaned=cleaned,
                    event_kind=event_kind,
                    event_ts_utc=ts,
                    plate=vehicle["plate"],
                    raw_payload=raw_payload,
                    reason=_IGNORE_EXIT_WITHOUT_STATE,
                )
                _mark_exception_seen(exception_id)
                return _finish({
                    "status": "ok",
                    "event_kind": event_kind,
                    "plate": vehicle["plate"],
                    "ignored": True,
                    "reason": _IGNORE_EXIT_WITHOUT_STATE,
                })
            apply_exit(plate=vehicle["plate"], event_ts_utc=ts)

        # Persistimos el evento como NO ignorado.
        persist_event(
            cleaned=cleaned,
            event_kind=event_kind,
            event_ts_utc=ts,
            plate=vehicle["plate"],
            ignored=False,
            ignore_reason=None,
            raw_payload=raw_payload,
        )
        _mark_exception_seen(exception_id)
        return _finish({
            "status": "ok",
            "event_kind": event_kind,
            "plate": vehicle["plate"],
            "ignored": False,
        })
    except Exception as exc:  # noqa: BLE001
        _logger.exception("Error procesando webhook de Geotab")
        return _finish({"status": "error", "message": str(exc)})


def _persist_ignored(
    *,
    cleaned: dict[str, Any],
    event_kind: str,
    event_ts_utc: datetime,
    plate: str | None,
    raw_payload: dict[str, Any],
    reason: str,
) -> None:
    try:
        persist_event(
            cleaned=cleaned,
            event_kind=event_kind,
            event_ts_utc=event_ts_utc,
            plate=plate,
            ignored=True,
            ignore_reason=reason,
            raw_payload=raw_payload,
        )
    except Exception:
        _logger.exception("Fallo persistiendo evento ignorado (reason=%s)", reason)


__all__ = [
    "process_webhook",
    "get_mapa_snapshot_cacheable",
    "get_recent_exits",
    "get_taller_history",
    "invalidate_snapshot_cache",
    "sweep_expired_grace",
    "resolve_vehicle",
    "classify_rule",
    "apply_manual_action",
    "_clean_payload",
    "_parse_event_ts_utc",
]
