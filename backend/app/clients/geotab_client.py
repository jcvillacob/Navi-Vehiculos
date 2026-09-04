from __future__ import annotations

from datetime import datetime, timedelta, timezone
import logging
import math
import os
import random
import re
import threading
import time as _time
from typing import Any, Callable, Literal, TypeVar

import mygeotab
import requests
from mygeotab.exceptions import MyGeotabException

from app.clients.geotab_rate_limiter import (
    GeotabRateLimiter,
    RateLimitTimeout,
    build_rate_limiter_from_env,
    limiter_key,
)
from app.core.config import GeotabConfig

_logger = logging.getLogger(__name__)

_PLATE_PATTERN = re.compile(r"^[A-Z]{3}[0-9]{3}$")

# Sincronizados con app.services.performance_providers._DIAG_ODOMETER / _DIAG_ENGINE_HOURS
_DIAG_ODOMETER = "DiagnosticOdometerId"
_DIAG_ENGINE_HOURS = "DiagnosticEngineHoursId"


def build_client(cfg: GeotabConfig):
    return get_authenticated_client(cfg.username, cfg.password, cfg.database)


def _normalize_rule_id(value: str | None) -> str | None:
    if not value:
        return None
    normalized = str(value).strip()
    return normalized or None


def _normalize_plate(value: str | None) -> str | None:
    if not value:
        return None
    normalized = "".join(char for char in str(value).strip().upper() if char.isalnum())
    if _PLATE_PATTERN.fullmatch(normalized):
        return normalized
    return normalized or None


def _normalize_vin(value: str | None) -> str | None:
    if not value:
        return None
    normalized = "".join(char for char in str(value).strip().upper() if char.isalnum())
    return normalized or None


def _search_entities(client, type_name: str, search: dict | None = None) -> list[dict]:
    return _api_call_with_retry(client, "Get", typeName=type_name, search=search or {})


def _search_devices(client, search: dict | None = None) -> list[dict]:
    return _search_entities(client, "Device", search)


def _search_rules(client, search: dict | None = None) -> list[dict]:
    return _search_entities(client, "Rule", search)


def _search_diagnostics(client, search: dict | None = None) -> list[dict]:
    return _search_entities(client, "Diagnostic", search)


def _device_matches_plate(device: dict, plate: str, plate_prefix: str | None = None) -> bool:
    normalized_plate = _normalize_plate(plate)
    if not normalized_plate:
        return False

    candidates = [normalized_plate]
    if plate_prefix:
        candidates.append(plate_prefix.upper() + normalized_plate)

    for key in ("licensePlate", "name"):
        device_value = _normalize_plate(device.get(key))
        if device_value and device_value in candidates:
            return True
    return False


def _device_matches_vin(device: dict, vin: str) -> bool:
    normalized_vin = _normalize_vin(vin)
    if not normalized_vin:
        return False
    return _normalize_vin(extract_vin(device)) == normalized_vin


def _device_is_active(device: dict) -> bool:
    """True si el device no esta archivado en Geotab (activeTo en el futuro)."""
    active_to = device.get("activeTo")
    if not active_to:
        return True
    return _parse_geotab_datetime(active_to) > datetime.now(timezone.utc)


def find_matching_devices(
    devices: list[dict], *, plate: str | None = None, vin: str | None = None, plate_prefix: str | None = None
) -> list[dict]:
    """Devuelve TODOS los devices que matchean la placa/VIN.

    Geotab permite placas duplicadas (dos devices con la misma licensePlate/name),
    por eso el caller necesita ver el conjunto completo para desempatar de forma
    estable en vez de quedarse con el primero que aparezca en el inventario.
    """
    matches: list[dict] = []
    for device in devices:
        if plate and _device_matches_plate(device, plate, plate_prefix=plate_prefix):
            matches.append(device)
        elif vin and _device_matches_vin(device, vin):
            matches.append(device)
    return matches


def _find_device_in_collection(
    devices: list[dict],
    *,
    plate: str | None = None,
    vin: str | None = None,
    plate_prefix: str | None = None,
    preferred_id: str | None = None,
) -> dict | None:
    """Resuelve un device por placa/VIN de forma estable ante duplicados.

    Prioridad de desempate cuando hay mas de un match:
      1. Device activo (no archivado) por encima de archivados. Un binding auto
         que quedo apuntando a un device archivado se auto-sana: el activo gana
         aunque sea el `preferred_id`.
      2. Dentro del grupo elegido, `preferred_id`: el device que ya venia
         usandose (evita saltar entre duplicados del mismo estado).
      3. Orden del inventario (deterministico dentro del cache).
    """
    matches = find_matching_devices(devices, plate=plate, vin=vin, plate_prefix=plate_prefix)
    if not matches:
        return None
    if len(matches) == 1:
        return matches[0]

    active_matches = [device for device in matches if _device_is_active(device)]
    pool = active_matches or matches

    normalized_preferred = str(preferred_id or "").strip()
    if normalized_preferred:
        for device in pool:
            if str(device.get("id") or "").strip() == normalized_preferred:
                return device

    return pool[0]


def build_plate_index(devices: list[dict]) -> dict[str, list[tuple[int, dict]]]:
    """Indice placa normalizada -> [(posicion en inventario, device)].

    Se construye UNA vez por database y reemplaza el recorrido completo del
    inventario por placa (`find_matching_devices`) cuando hay cientos de
    targets. Indexa `licensePlate` y `name` con el mismo normalizador que
    `_device_matches_plate`, asi el resultado de `lookup_plate_index` es
    identico al de `find_matching_devices` (incluido el orden de inventario).
    """
    index: dict[str, list[tuple[int, dict]]] = {}
    for position, device in enumerate(devices):
        seen: set[str] = set()
        for key in ("licensePlate", "name"):
            normalized = _normalize_plate(device.get(key))
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            index.setdefault(normalized, []).append((position, device))
    return index


def lookup_plate_index(
    index: dict[str, list[tuple[int, dict]]],
    *,
    plate: str | None,
    plate_prefix: str | None = None,
) -> list[dict]:
    """Devices que matchean la placa (con y sin `plate_prefix`), en orden de
    inventario. Equivalente a `find_matching_devices(devices, plate=...)`."""
    normalized_plate = _normalize_plate(plate)
    if not normalized_plate:
        return []
    candidates = [normalized_plate]
    if plate_prefix:
        candidates.append(plate_prefix.upper() + normalized_plate)
    found: dict[int, dict] = {}
    for candidate in candidates:
        for position, device in index.get(candidate, ()):
            found.setdefault(position, device)
    return [found[position] for position in sorted(found)]


def _get_all_devices(client) -> list[dict]:
    return _search_devices(client)


def find_device(client, *, plate: str | None = None, vin: str | None = None, plate_prefix: str | None = None) -> dict | None:
    normalized_plate = _normalize_plate(plate)
    normalized_vin = _normalize_vin(vin)

    if normalized_plate:
        search_values = [normalized_plate]
        if plate_prefix:
            search_values.append(plate_prefix.upper() + normalized_plate)

        for search_plate in search_values:
            for field in ("licensePlate", "name"):
                devices = _search_devices(client, {field: search_plate})
                match = _find_device_in_collection(devices, plate=normalized_plate, plate_prefix=plate_prefix)
                if match:
                    return match

    all_devices: list[dict] | None = None

    if normalized_vin:
        all_devices = _get_all_devices(client)
        match = _find_device_in_collection(all_devices, vin=normalized_vin)
        if match:
            return match

    if normalized_plate:
        if all_devices is None:
            all_devices = _get_all_devices(client)
        match = _find_device_in_collection(all_devices, plate=normalized_plate, plate_prefix=plate_prefix)
        if match:
            return match

    return None


def find_device_by_plate(client, plate: str, plate_prefix: str | None = None) -> dict | None:
    return find_device(client, plate=plate, plate_prefix=plate_prefix)


def extract_vin(device: dict) -> str | None:
    for key in ("vehicleIdentificationNumber", "vin", "VIN"):
        value = device.get(key)
        if value:
            return str(value).strip()
    return None


def get_rule_by_id_with_client(client, rule_id: str) -> dict | None:
    normalized_rule_id = _normalize_rule_id(rule_id)
    if not normalized_rule_id:
        return None
    rules = _search_rules(client, {"id": normalized_rule_id})
    if not rules:
        return None
    return rules[0]


def get_rule_by_id(cfg: GeotabConfig, rule_id: str) -> dict | None:
    client = build_client(cfg)
    return get_rule_by_id_with_client(client, rule_id)


def _parse_geotab_datetime(value: Any) -> datetime:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    if isinstance(value, str):
        normalized = value.strip()
        if normalized.endswith("Z"):
            normalized = normalized[:-1] + "+00:00"
        try:
            parsed = datetime.fromisoformat(normalized)
        except ValueError:
            parsed = datetime.strptime(normalized[:19], "%Y-%m-%dT%H:%M:%S")
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)

    return datetime(2050, 1, 1, tzinfo=timezone.utc)


def _rule_status(rule: dict[str, Any]) -> str:
    active_to = _parse_geotab_datetime(rule.get("activeTo"))
    return "Activa" if active_to > datetime.now(timezone.utc) else "Archivada/Desactivada"


def _humanize_token(token: str | None) -> str:
    if not token:
        return "Condicion"
    cleaned = str(token).replace("_", " ").strip()
    spaced = re.sub(r"(?<!^)(?=[A-Z])", " ", cleaned).strip()
    normalized = re.sub(r"\bUnit Of Measure\b", "", spaced, flags=re.IGNORECASE)
    normalized = re.sub(r"\bId\b", "", normalized)
    normalized = re.sub(r"\s+", " ", normalized).strip(" -/")
    if not normalized:
        return "Condicion"
    lowered = normalized.lower()
    if lowered == "revolutions per minute":
        return "RPM"
    if lowered == "km h":
        return "km/h"
    return normalized[:1].upper() + normalized[1:]


def _friendly_unit(unit: str | None) -> str | None:
    if not unit:
        return None
    normalized = str(unit).strip()
    if not normalized:
        return None
    if normalized == "km/h":
        return normalized
    return _humanize_token(normalized)


def _comparison_symbol(condition_type: str | None) -> str | None:
    return {
        "IsValueMoreThan": ">",
        "IsValueLessThan": "<",
        "IsValueEqualTo": "=",
        "IsValueMoreThanOrEqualTo": ">=",
        "IsValueLessThanOrEqualTo": "<=",
        "IsValueNotEqualTo": "!=",
    }.get(condition_type or "")


def _diagnostic_label(
    client,
    diagnostic_payload: dict[str, Any] | None,
    diagnostic_cache: dict[str, str],
) -> str:
    if not isinstance(diagnostic_payload, dict):
        return "Diagnostico"

    diagnostic_id = _normalize_rule_id(diagnostic_payload.get("id"))
    if diagnostic_id and diagnostic_id in diagnostic_cache:
        return diagnostic_cache[diagnostic_id]

    label: str | None = None
    if diagnostic_id:
        diagnostics = _search_diagnostics(client, {"id": diagnostic_id})
        diagnostic = diagnostics[0] if diagnostics else None
        if diagnostic:
            for key in ("name", "title", "description"):
                value = diagnostic.get(key)
                if value:
                    label = str(value).strip()
                    break

    if not label:
        unit_of_measure = diagnostic_payload.get("unitOfMeasure")
        if unit_of_measure:
            label = _humanize_token(str(unit_of_measure).replace("UnitOfMeasure", ""))
        elif diagnostic_id:
            label = f"Diagnostico {diagnostic_id}"
        else:
            label = "Diagnostico"

    if diagnostic_id:
        diagnostic_cache[diagnostic_id] = label
    return label


def _condition_subject_label(
    condition: dict[str, Any] | None,
    client,
    diagnostic_cache: dict[str, str],
) -> str:
    if not isinstance(condition, dict):
        return "Condicion"

    condition_type = condition.get("conditionType")
    if condition_type == "Speed":
        return "Velocidad"
    if condition_type == "IsDriving":
        return "Conduccion"
    if condition_type == "FilterStatusDataByDiagnostic":
        return _diagnostic_label(client, condition.get("diagnostic"), diagnostic_cache)
    if condition_type in {"And", "Or"}:
        return " / ".join(
            _condition_subject_label(child, client, diagnostic_cache)
            for child in condition.get("children", []) or []
            if isinstance(child, dict)
        ) or _humanize_token(condition_type)
    return _humanize_token(condition_type)


def _format_threshold(
    subject_label: str,
    symbol: str,
    value: Any,
    unit: str | None,
) -> str:
    if subject_label == "Conduccion" and symbol == ">" and value == 0:
        return "Vehiculo conduciendo"

    unit_suffix = f" {unit}" if unit else ""
    return f"{subject_label} {symbol} {value}{unit_suffix}".strip()


def _append_unique(target: list[str], value: str | None) -> None:
    if not value:
        return
    if value not in target:
        target.append(value)


def _build_condition_node(
    condition: dict[str, Any] | None,
    *,
    client,
    diagnostic_cache: dict[str, str],
) -> tuple[dict[str, Any] | None, list[str]]:
    if not isinstance(condition, dict):
        return None, []

    condition_type = str(condition.get("conditionType") or "").strip()
    raw_children = condition.get("children", []) or []
    parsed_children: list[dict[str, Any]] = []
    collected_facts: list[str] = []

    for child in raw_children:
        child_node, child_facts = _build_condition_node(
            child,
            client=client,
            diagnostic_cache=diagnostic_cache,
        )
        if child_node is not None:
            parsed_children.append(child_node)
        for fact in child_facts:
            _append_unique(collected_facts, fact)

    if condition_type == "And":
        return {
            "kind": "group",
            "label": "Todas las condiciones",
            "children": parsed_children,
        }, collected_facts

    if condition_type == "Or":
        return {
            "kind": "group",
            "label": "Cualquiera de estas condiciones",
            "children": parsed_children,
        }, collected_facts

    symbol = _comparison_symbol(condition_type)
    if symbol:
        subject_condition = raw_children[0] if raw_children else None
        subject_label = _condition_subject_label(subject_condition, client, diagnostic_cache)
        unit = _friendly_unit(condition.get("unit"))
        fact = _format_threshold(subject_label, symbol, condition.get("value"), unit)
        _append_unique(collected_facts, fact)
        return {
            "kind": "comparison",
            "label": fact,
            "children": parsed_children,
        }, collected_facts

    if condition_type == "DurationLongerThan":
        unit = _friendly_unit(condition.get("unit"))
        label = f"Durante mas de {condition.get('value')}" + (f" {unit}" if unit else "")
        _append_unique(collected_facts, label)
        return {
            "kind": "duration",
            "label": label,
            "children": parsed_children,
        }, collected_facts

    label = _condition_subject_label(condition, client, diagnostic_cache)
    return {
        "kind": "leaf",
        "label": label,
        "children": parsed_children,
    }, collected_facts


def _build_rule_headline(condition: dict[str, Any] | None, facts: list[str]) -> str:
    if not facts:
        return "Sin condicion visible para resumir."

    if isinstance(condition, dict) and condition.get("conditionType") == "Or":
        if len(facts) == 1:
            return f"Se activa cuando se cumple {facts[0]}."
        return "Se activa cuando se cumple cualquiera de estas condiciones: " + ", ".join(facts) + "."

    if len(facts) == 1:
        return f"Se activa cuando {facts[0]}."

    if len(facts) == 2:
        return f"Se activa cuando {facts[0]} y {facts[1]}."

    return "Se activa cuando se cumplen estas condiciones: " + ", ".join(facts) + "."


def build_rule_inspection_with_client(
    client,
    rule_id: str,
    *,
    fallback_name: str | None = None,
    missing_message: str | None = None,
) -> dict[str, Any]:
    normalized_rule_id = _normalize_rule_id(rule_id) or ""
    rule = get_rule_by_id_with_client(client, normalized_rule_id)
    if not rule:
        return {
            "exists": False,
            "rule_id": normalized_rule_id,
            "name": fallback_name,
            "status": "Inexistente",
            "type": None,
            "groups_count": 0,
            "comment": None,
            "headline": "No se encontro la regla en Geotab.",
            "facts": [],
            "tree": None,
            "raw_condition": None,
            "message": missing_message or "La regla no existe o ya no esta disponible en Geotab.",
        }

    diagnostic_cache: dict[str, str] = {}
    raw_condition = rule.get("condition")
    tree, facts = _build_condition_node(
        raw_condition,
        client=client,
        diagnostic_cache=diagnostic_cache,
    )
    return {
        "exists": True,
        "rule_id": normalized_rule_id,
        "name": rule.get("name"),
        "status": _rule_status(rule),
        "type": "Predefinida" if rule.get("baseType") == "Stock" else "Personalizada",
        "groups_count": len(rule.get("groups", []) or []),
        "comment": (rule.get("comment") or "").strip() or None,
        "headline": _build_rule_headline(raw_condition, facts),
        "facts": facts,
        "tree": tree,
        "raw_condition": raw_condition,
        "message": None,
    }


def build_rule_inspection(cfg: GeotabConfig, rule_id: str, *, fallback_name: str | None = None) -> dict[str, Any]:
    client = build_client(cfg)
    return build_rule_inspection_with_client(client, rule_id, fallback_name=fallback_name)


def get_vin_from_plate(plate: str, cfg: GeotabConfig, plate_prefix: str | None = None) -> str | None:
    client = build_client(cfg)
    device = find_device_by_plate(client, plate, plate_prefix=plate_prefix)
    if not device:
        return None
    return extract_vin(device)


def get_device_from_plate(plate: str, cfg: GeotabConfig, plate_prefix: str | None = None) -> dict | None:
    client = build_client(cfg)
    return find_device_by_plate(client, plate, plate_prefix=plate_prefix)


def get_device_from_vin(vin: str, cfg: GeotabConfig) -> dict | None:
    client = build_client(cfg)
    return find_device(client, vin=vin)


def vehicle_exists_for_plate(plate: str, cfg: GeotabConfig) -> bool:
    return get_device_from_plate(plate, cfg) is not None


# ==============================================================================
# SESSION CACHE (per-process, thread-safe)
# ==============================================================================

_SESSION_CACHE: dict[str, mygeotab.API] = {}
_SESSION_LOCK = threading.Lock()
_DEVICE_CACHE_TTL_SECONDS = 300
_DEVICE_CACHE: dict[str, tuple[float, list[dict[str, Any]]]] = {}
_DEVICE_CACHE_LOCK = threading.Lock()
_DEVICE_KEY_LOCKS: dict[str, threading.Lock] = {}


def get_authenticated_client(username: str, password: str, database: str) -> mygeotab.API:
    """
    Returns a cached, authenticated mygeotab.API client for the given database.
    Authenticates on first call; re-authenticates if the cached session is stale.
    """
    cache_key = f"{database}:{username}"
    with _SESSION_LOCK:
        cached = _SESSION_CACHE.get(cache_key)

    if cached is not None:
        return cached

    api = mygeotab.API(
        username=username,
        password=password,
        database=database,
        timeout=GEOTAB_HTTP_TIMEOUT_SECONDS,
    )
    try:
        api.authenticate()
    except Exception:
        with _SESSION_LOCK:
            _SESSION_CACHE.pop(cache_key, None)
        raise

    with _SESSION_LOCK:
        _SESSION_CACHE[cache_key] = api
    return api


def _invalidate_session(username: str, database: str) -> None:
    """Remove a stale cached session so the next call re-authenticates."""
    with _SESSION_LOCK:
        _SESSION_CACHE.pop(f"{database}:{username}", None)


def _device_cache_key(username: str, database: str) -> str:
    return f"{database}:{username}:devices"


def invalidate_device_cache(username: str, database: str) -> None:
    with _DEVICE_CACHE_LOCK:
        _DEVICE_CACHE.pop(_device_cache_key(username, database), None)


def get_cached_devices(username: str, password: str, database: str) -> list[dict[str, Any]]:
    cache_key = _device_cache_key(username, database)
    now = _time.time()

    with _DEVICE_CACHE_LOCK:
        cached = _DEVICE_CACHE.get(cache_key)
        if cached is not None:
            expires_at, devices = cached
            if expires_at > now:
                return devices
            _DEVICE_CACHE.pop(cache_key, None)

    # Lock por database: el fetch (red + reintentos + rate limiter) puede tardar
    # minutos y no debe bloquear a los consumidores de OTRAS databases.
    with _DEVICE_CACHE_LOCK:
        key_lock = _DEVICE_KEY_LOCKS.setdefault(cache_key, threading.Lock())

    with key_lock:
        with _DEVICE_CACHE_LOCK:
            cached = _DEVICE_CACHE.get(cache_key)
        if cached is not None:
            expires_at, devices = cached
            if expires_at > _time.time():
                return devices

        client = get_authenticated_client(username, password, database)
        devices = _get_all_devices(client)
        with _DEVICE_CACHE_LOCK:
            _DEVICE_CACHE[cache_key] = (_time.time() + _DEVICE_CACHE_TTL_SECONDS, devices)
        return devices


def get_cached_device_from_plate(plate: str, cfg: GeotabConfig, plate_prefix: str | None = None) -> dict | None:
    devices = get_cached_devices(cfg.username, cfg.password, cfg.database)
    return _find_device_in_collection(devices, plate=plate, plate_prefix=plate_prefix)


def get_cached_device_from_vin(vin: str, cfg: GeotabConfig) -> dict | None:
    devices = get_cached_devices(cfg.username, cfg.password, cfg.database)
    return _find_device_in_collection(devices, vin=vin)


# ==============================================================================
# NETWORK RETRY WRAPPER
# ==============================================================================


def _env_int(name: str, default: int, *, minimum: int = 1) -> int:
    raw = os.getenv(name)
    if raw is None or not str(raw).strip():
        return default
    try:
        value = int(str(raw).strip())
    except ValueError:
        _logger.warning("Valor invalido para %s=%r; usando %s", name, raw, default)
        return default
    return max(minimum, value)


GEOTAB_HTTP_TIMEOUT_SECONDS = _env_int("GEOTAB_HTTP_TIMEOUT_SECONDS", 60)

_NETWORK_ERROR_FRAGMENTS = (
    "SSL",
    "EOF",
    "Max retries",
    "ConnectionError",
    "timeout",
    "RemoteDisconnected",
    "BrokenPipe",
    "timed out",
    "Connection reset",
    "ConnectTimeout",
    "ReadTimeout",
    "Service Unavailable",
    "Bad Gateway",
    "Gateway Timeout",
)
_AUTH_ERROR_FRAGMENTS = (
    "Incorrect MyGeotab login credentials",
    "InvalidUserException",
    "AuthenticationException",
    "session has expired",
    "session expired",
)
# Nombres de error del servidor Geotab (MyGeotabException.name) por categoria.
_RATE_LIMIT_ERROR_NAMES = ("OverLimitException",)
_TRANSIENT_ERROR_NAMES = ("DbUnavailableException", "ServerException", "TimeoutException")
_TRANSIENT_HTTP_STATUSES = frozenset({500, 502, 503, 504})
_RATE_LIMIT_HTTP_STATUSES = frozenset({429})
_TRANSIENT_REQUESTS_EXCEPTIONS: tuple[type[BaseException], ...] = (
    requests.exceptions.ConnectionError,
    requests.exceptions.Timeout,
    requests.exceptions.ChunkedEncodingError,
)

# Intentos totales (incluye el primero) ante errores transitorios.
NET_MAX_RETRIES = _env_int("GEOTAB_RETRY_MAX_ATTEMPTS", 4)
# Backoff exponencial con full jitter: wait = uniform(0, min(cap, base * factor**n)).
NET_BACKOFF_BASE_SECONDS = 2.0
NET_BACKOFF_FACTOR = 2.0
NET_BACKOFF_CAP_SECONDS = 30.0
# Override legacy: si se define una tupla, se usan esas esperas fijas en lugar del
# backoff (lo usan tests antiguos; en produccion debe quedar en None).
NET_RETRY_WAITS: tuple[float, ...] | None = None

# La cuota de Geotab es por minuto: ante OverLimitException esperamos >= 60 s.
RATE_LIMIT_WAIT_SECONDS = _env_int("GEOTAB_RATE_LIMIT_WAIT_SECONDS", 60)
RATE_LIMIT_MAX_ATTEMPTS = _env_int("GEOTAB_RATE_LIMIT_MAX_ATTEMPTS", 3)
RATE_LIMIT_JITTER_MAX_SECONDS = 10.0

# Espera maxima en el limitador local antes de seguir "a ciegas" (el limitador es
# preventivo: si algo lo deja bloqueado mas de esto, dejamos que Geotab responda).
RATE_LIMIT_ACQUIRE_TIMEOUT_SECONDS = _env_int("GEOTAB_RATE_LIMIT_ACQUIRE_TIMEOUT_SECONDS", 180)

ErrorKind = Literal["auth", "rate_limit", "transient", "fatal"]
_T = TypeVar("_T")


# ------------------------------------------------------------------------------
# Rate limiter local (G7)
# ------------------------------------------------------------------------------
# Un solo token bucket por database para todo el trafico que pasa por
# `_call_with_retry` (rendimientos multi-hilo, CPK on-demand, taller sync via
# multi_call_with_retry). Se construye con un reloj "virtual": monotonic real +
# el tiempo que este modulo *quiso* dormir pero no durmio (tests que parchean
# `_time.sleep`). Asi una penalizacion por OverLimit expira igual en tests con
# sleeps falsos y en produccion, sin que el limitador tenga que dormir por su
# cuenta. `_rate_limiter()` es el punto de inyeccion: los tests lo parchean.
_VIRTUAL_CLOCK_OFFSET = 0.0
_VIRTUAL_CLOCK_LOCK = threading.Lock()
_LIMITER: GeotabRateLimiter | None = None
_LIMITER_LOCK = threading.Lock()


def _limiter_clock() -> float:
    return _time.monotonic() + _VIRTUAL_CLOCK_OFFSET


def _sleep_tracked(seconds: float) -> None:
    """Duerme via `_time.sleep`; lo que no se durmio de verdad avanza el reloj virtual."""
    global _VIRTUAL_CLOCK_OFFSET
    started = _time.monotonic()
    _time.sleep(seconds)
    unslept = seconds - (_time.monotonic() - started)
    if unslept > 0:
        with _VIRTUAL_CLOCK_LOCK:
            _VIRTUAL_CLOCK_OFFSET += unslept


def _rate_limiter() -> GeotabRateLimiter:
    """Limitador compartido del proceso para la API de Geotab (lazy, monkeypatchable)."""
    global _LIMITER
    if _LIMITER is None:
        with _LIMITER_LOCK:
            if _LIMITER is None:
                _LIMITER = build_rate_limiter_from_env(clock=_limiter_clock)
    return _LIMITER


def _limiter_key_for(api) -> str:
    key = _credentials_key(api)
    if key is None:
        return "unknown"
    username, database = key
    return limiter_key(database, username)


def _throttle(api, cost: int) -> None:
    """Reserva `cost` llamadas en el limitador antes de golpear a Geotab.

    Nunca aborta la llamada: si el limitador supera su timeout se avisa y se
    sigue (Geotab respondera OverLimit y entrara la politica de reintentos).
    """
    limiter = _rate_limiter()
    key = _limiter_key_for(api)
    capacity = int(getattr(limiter, "capacity", 0) or 0)
    effective_cost = max(1, int(cost))
    if capacity > 0:
        effective_cost = min(effective_cost, capacity)
    try:
        limiter.acquire(key, effective_cost, timeout=RATE_LIMIT_ACQUIRE_TIMEOUT_SECONDS)
    except RateLimitTimeout as exc:
        _logger.warning("Geotab rate limiter: %s; continuando sin reserva", exc)


def _penalize(api, seconds: float) -> None:
    try:
        _rate_limiter().penalize(_limiter_key_for(api), seconds)
    except Exception:  # pragma: no cover - el limitador nunca debe romper la llamada
        _logger.exception("Geotab rate limiter: fallo al penalizar")


def _is_network_error(exc: Exception) -> bool:
    """Heuristica por mensaje (legacy). Ver `_classify_error` para la version completa."""
    msg = str(exc)
    return any(fragment in msg for fragment in _NETWORK_ERROR_FRAGMENTS)


def _is_auth_error(exc: Exception) -> bool:
    name = type(exc).__name__
    if "Authentication" in name or "InvalidUser" in name:
        return True
    msg = str(exc)
    return any(fragment in msg for fragment in _AUTH_ERROR_FRAGMENTS)


def _geotab_error_names(exc: Exception) -> tuple[str, ...]:
    """Nombres con los que identificar el error: `.name` de MyGeotabException y el tipo Python."""
    names = [type(exc).__name__]
    server_name = getattr(exc, "name", None)
    if isinstance(exc, MyGeotabException) and isinstance(server_name, str) and server_name:
        names.append(server_name)
    return tuple(names)


def _http_status(exc: Exception) -> int | None:
    if not isinstance(exc, requests.exceptions.HTTPError):
        return None
    response = getattr(exc, "response", None)
    status = getattr(response, "status_code", None)
    return int(status) if isinstance(status, int) else None


def _classify_error(exc: Exception) -> ErrorKind:
    """
    Clasifica una excepcion de mygeotab/requests para decidir la politica de retry:

      - auth:       sesion expirada / credenciales -> una re-autenticacion transparente.
      - rate_limit: OverLimitException o HTTP 429 -> esperar >= 60 s (cuota por minuto).
      - transient:  red/SSL/timeout, DbUnavailable/Server/Timeout del servidor, HTTP 5xx
                    -> backoff exponencial.
      - fatal:      todo lo demas (permisos, parametros invalidos, etc.) -> propagar.
    """
    if _is_auth_error(exc):
        return "auth"

    names = _geotab_error_names(exc)
    status = _http_status(exc)
    text = str(exc)

    if status in _RATE_LIMIT_HTTP_STATUSES:
        return "rate_limit"
    if isinstance(exc, MyGeotabException) and any(
        fragment in name or fragment in text for name in names for fragment in _RATE_LIMIT_ERROR_NAMES
    ):
        return "rate_limit"

    if status in _TRANSIENT_HTTP_STATUSES:
        return "transient"
    if isinstance(exc, _TRANSIENT_REQUESTS_EXCEPTIONS):
        return "transient"
    if any(name in _TRANSIENT_ERROR_NAMES for name in names):
        return "transient"
    if _is_network_error(exc):
        return "transient"

    return "fatal"


def _credentials_key(api: mygeotab.API) -> tuple[str, str] | None:
    credentials = getattr(api, "credentials", None)
    if credentials is None:
        return None
    username = getattr(credentials, "username", None)
    database = getattr(credentials, "database", None)
    if not username or not database:
        return None
    return str(username), str(database)


def _database_label(api: mygeotab.API) -> str:
    key = _credentials_key(api)
    return key[1] if key is not None else "?"


def _transient_wait(retry_index: int) -> float:
    """Espera antes del reintento numero `retry_index` (0-based) para errores transitorios."""
    legacy = NET_RETRY_WAITS
    if legacy:
        return float(legacy[min(retry_index, len(legacy) - 1)])
    ceiling = min(NET_BACKOFF_CAP_SECONDS, NET_BACKOFF_BASE_SECONDS * (NET_BACKOFF_FACTOR ** retry_index))
    return random.uniform(0.0, ceiling)


def _rate_limit_wait(retry_index: int) -> float:
    """Espera antes del reintento por cuota: nunca menor que RATE_LIMIT_WAIT_SECONDS."""
    base = max(RATE_LIMIT_WAIT_SECONDS, NET_BACKOFF_BASE_SECONDS)
    return base + random.uniform(0.0, RATE_LIMIT_JITTER_MAX_SECONDS)


def _call_with_retry(api: mygeotab.API, fn: Callable[[], _T], *, describe: str, cost: int = 1) -> _T:
    """
    Ejecuta `fn()` aplicando la politica de reintentos segun `_classify_error`:

      - auth:       una sola re-autenticacion transparente; si vuelve a fallar, propaga.
      - rate_limit: hasta RATE_LIMIT_MAX_ATTEMPTS intentos esperando >= 60 s entre ellos.
      - transient:  hasta NET_MAX_RETRIES intentos con backoff exponencial + jitter.
      - fatal:      propaga de inmediato.

    Antes de cada intento reserva `cost` llamadas en el rate limiter local (G7):
    un multi_call de N Gets cuesta N porque Geotab cuenta cada llamada interna.
    Ante OverLimit se penaliza la clave (bloquea a los demas hilos) y se espera.

    Al agotar reintentos se propaga la ultima excepcion recibida.
    """
    auth_retry_used = False
    transient_attempts = 0
    rate_limit_attempts = 0
    while True:
        _throttle(api, cost)
        try:
            return fn()
        except Exception as exc:
            kind = _classify_error(exc)

            if kind == "auth":
                if auth_retry_used:
                    raise
                auth_retry_used = True
                try:
                    api.authenticate()
                except Exception:
                    key = _credentials_key(api)
                    if key is not None:
                        _invalidate_session(key[0], key[1])
                    raise
                continue

            if kind == "rate_limit":
                rate_limit_attempts += 1
                if rate_limit_attempts >= RATE_LIMIT_MAX_ATTEMPTS:
                    raise
                attempt, max_attempts = rate_limit_attempts, RATE_LIMIT_MAX_ATTEMPTS
                wait = _rate_limit_wait(rate_limit_attempts - 1)
                # La penalizacion dura lo mismo que nuestra propia espera: al
                # reintentar, el bucket ya esta libre para este hilo.
                _penalize(api, RATE_LIMIT_WAIT_SECONDS)
            elif kind == "transient":
                transient_attempts += 1
                if transient_attempts >= NET_MAX_RETRIES:
                    raise
                attempt, max_attempts = transient_attempts, NET_MAX_RETRIES
                wait = _transient_wait(transient_attempts - 1)
            else:
                raise

            _logger.warning(
                "Geotab %s fallo en db=%s (%s, intento %d/%d): %s; reintentando en %.1fs",
                describe,
                _database_label(api),
                kind,
                attempt,
                max_attempts,
                type(exc).__name__,
                wait,
            )
            _sleep_tracked(wait)


def _api_call_with_retry(api: mygeotab.API, method: str, **kwargs) -> list[dict]:
    """
    Calls api.call(method, **kwargs) applying `_call_with_retry` (transient backoff,
    rate-limit wait, single transparent re-authentication). Fatal errors propagate.
    """
    describe = f"call {method}/{kwargs.get('typeName') or '-'}"
    result = _call_with_retry(api, lambda: api.call(method, **kwargs), describe=describe, cost=1)
    return result or []


def multi_call_with_retry(api: mygeotab.API, calls: list[tuple[str, dict[str, Any]]]) -> list[list[dict]]:
    """
    Calls api.multi_call(calls) applying `_call_with_retry`. Normalizes each call
    result with ``or []``. Cobra ``len(calls)`` tokens al rate limiter.
    """
    results = _call_with_retry(
        api,
        lambda: api.multi_call(calls),
        describe=f"multi_call x{len(calls)}",
        cost=max(1, len(calls)),
    )
    return [result or [] for result in results]


# ==============================================================================
# LIVE TELEMETRY
# ==============================================================================

def _safe_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        n = float(value)
    except (TypeError, ValueError):
        return None
    return n if math.isfinite(n) else None


def _sort_by_datetime(records: list[dict]) -> list[dict]:
    """Geotab no garantiza orden en Get; ordenamos una sola vez por dateTime."""
    return sorted(records, key=lambda r: str(r.get("dateTime") or ""))


def _last_status_data_value(records: list[dict]) -> float | None:
    if not records:
        return None
    return _safe_float(_sort_by_datetime(records)[-1].get("data"))


def get_device_live_status(api: mygeotab.API, device_id: str) -> dict[str, Any]:
    """
    Consulta en vivo la telemetria de un dispositivo Geotab.

    Realiza un multi_call con:
      1. DeviceStatusInfo (estado actual / ultima comunicacion).
      2. StatusData de odometro de los ultimos 7 dias.
      3. StatusData de horometro de los ultimos 7 dias.

    Devuelve un dict con los valores formateados; cualquier campo faltante
    se expresa como None. Nunca explota por shape inesperado de la respuesta.
    """
    now = datetime.now(timezone.utc)
    from_date = (now - timedelta(days=7)).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    to_date = (now + timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%S.000Z")

    calls: list[tuple[str, dict[str, Any]]] = [
        (
            "Get",
            {
                "typeName": "DeviceStatusInfo",
                "search": {"deviceSearch": {"id": device_id}},
            },
        ),
        (
            "Get",
            {
                "typeName": "StatusData",
                "search": {
                    "deviceSearch": {"id": device_id},
                    "diagnosticSearch": {"id": _DIAG_ODOMETER},
                    "fromDate": from_date,
                    "toDate": to_date,
                },
            },
        ),
        (
            "Get",
            {
                "typeName": "StatusData",
                "search": {
                    "deviceSearch": {"id": device_id},
                    "diagnosticSearch": {"id": _DIAG_ENGINE_HOURS},
                    "fromDate": from_date,
                    "toDate": to_date,
                },
            },
        ),
    ]

    try:
        results = multi_call_with_retry(api, calls)
    except Exception:
        _logger.exception("Error consultando telemetria en vivo del dispositivo %s", device_id)
        raise

    status_infos = results[0] if results else []
    odo_records = results[1] if len(results) > 1 else []
    hours_records = results[2] if len(results) > 2 else []

    status_info: dict[str, Any] = status_infos[0] if isinstance(status_infos, list) and status_infos else {}

    last_communication = status_info.get("dateTime")
    if isinstance(last_communication, datetime):
        last_communication = last_communication.isoformat()
    elif not isinstance(last_communication, str):
        last_communication = None

    is_driving_raw = status_info.get("isDriving")
    is_driving: bool | None = None
    if isinstance(is_driving_raw, bool):
        is_driving = is_driving_raw
    elif isinstance(is_driving_raw, str):
        is_driving = is_driving_raw.strip().lower() in {"true", "1", "yes"}

    latitude = _safe_float(status_info.get("latitude"))
    longitude = _safe_float(status_info.get("longitude"))
    speed = _safe_float(status_info.get("speed"))

    odo_meters = _last_status_data_value(odo_records)
    hours_seconds = _last_status_data_value(hours_records)

    return {
        "last_communication": last_communication,
        "is_driving": is_driving,
        "latitude": latitude,
        "longitude": longitude,
        "speed": speed,
        "odometer_km": round(odo_meters / 1000, 3) if odo_meters is not None else None,
        "engine_hours": round(hours_seconds / 3600, 3) if hours_seconds is not None else None,
        "readings_window_days": 7,
    }


# ==============================================================================
# MONTH RANGE (Colombia UTC-5)
# ==============================================================================

def get_geotab_month_range(year: int, month_number: int) -> tuple[str, str]:
    """
    Returns (from_date, to_date) in UTC ISO format for a calendar month
    bounded by Colombia midnight (UTC-5 = 05:00 UTC).
    """
    from_dt = datetime(year, month_number, 1, 5, 0, 0, tzinfo=timezone.utc)
    if month_number == 12:
        to_dt = datetime(year + 1, 1, 1, 5, 0, 0, tzinfo=timezone.utc)
    else:
        to_dt = datetime(year, month_number + 1, 1, 5, 0, 0, tzinfo=timezone.utc)
    fmt = "%Y-%m-%dT%H:%M:%S.000Z"
    return from_dt.strftime(fmt), to_dt.strftime(fmt)


# ==============================================================================
# PERFORMANCE DATA QUERIES
# ==============================================================================

def get_status_data_for_month(
    api: mygeotab.API,
    device_id: str,
    diagnostic_id: str,
    from_date: str,
    to_date: str,
) -> list[dict]:
    """
    Fetches StatusData for a specific diagnostic and device over a date range.
    Returns records sorted chronologically by dateTime.
    """
    results = _api_call_with_retry(
        api,
        "Get",
        typeName="StatusData",
        search={
            "deviceSearch": {"id": device_id},
            "diagnosticSearch": {"id": diagnostic_id},
            "fromDate": from_date,
            "toDate": to_date,
        },
    )
    return _sort_by_datetime(results)


def get_trips_for_month(
    api: mygeotab.API,
    device_id: str,
    from_date: str,
    to_date: str,
) -> list[dict]:
    """
    Fetches all Trip records for a device over a date range.
    """
    return _api_call_with_retry(
        api,
        "Get",
        typeName="Trip",
        search={
            "deviceSearch": {"id": device_id},
            "fromDate": from_date,
            "toDate": to_date,
        },
    )


# ------------------------------------------------------------------------------
# Bundles por dispositivo (G1 / G5)
# ------------------------------------------------------------------------------
# Tope de Gets por multi_call cuando no se fija GEOTAB_BUNDLE_CHUNK_DEVICES: el
# tamano de chunk se deriva como max_calls // gets_por_device (7 Gets con las
# ventanas de combustible -> 14 devices; 5 Gets -> 20).
BUNDLE_MAX_CALLS_PER_MULTICALL = _env_int("GEOTAB_BUNDLE_MAX_CALLS", 100)
# Dias de la ventana "borde" para diagnosticos de los que solo se usa la primera
# y la ultima lectura (combustible acumulado): [from, from+N] y [to-N, to].
BUNDLE_EDGE_WINDOW_DAYS = _env_int("GEOTAB_FUEL_EDGE_WINDOW_DAYS", 2)
# Series completas por multi_call en la segunda pasada (ventana borde vacia).
# Un acumulado de combustible del mes son ~6-7k filas por device: 20+ series
# en un solo multi_call superan el timeout HTTP (medido 2026-09-03).
BUNDLE_FULL_SERIES_PER_MULTICALL = _env_int("GEOTAB_BUNDLE_FULL_SERIES_PER_MULTICALL", 4)
_GEOTAB_DATE_FMT = "%Y-%m-%dT%H:%M:%S.000Z"


class GeotabBundleShapeError(RuntimeError):
    """multi_call devolvio un numero de resultados distinto al de llamadas (fatal)."""


def _bundle_chunk_size(calls_per_device: int, explicit: int | None = None) -> int:
    if explicit is not None:
        return max(1, int(explicit))
    raw = os.getenv("GEOTAB_BUNDLE_CHUNK_DEVICES")
    if raw is not None and str(raw).strip():
        return _env_int("GEOTAB_BUNDLE_CHUNK_DEVICES", 14)
    return max(1, BUNDLE_MAX_CALLS_PER_MULTICALL // max(1, calls_per_device))


def bundle_chunk_size_for(
    *,
    from_date: str,
    to_date: str,
    status_diagnostics: dict[str, str],
    include_trips: bool = True,
    edge_only: frozenset[str] | set[str] | tuple[str, ...] = frozenset(),
    chunk_size: int | None = None,
) -> int:
    """Devices por multi_call que usara `get_month_data_bundles` con estos parametros."""
    _, calls = _build_bundle_calls(
        "_",
        from_date,
        to_date,
        status_diagnostics=status_diagnostics,
        include_trips=include_trips,
        edge_only=frozenset(edge_only),
        edge_days=BUNDLE_EDGE_WINDOW_DAYS,
    )
    return _bundle_chunk_size(len(calls), chunk_size)


def _status_data_call(device_id: str, diagnostic_id: str, from_date: str, to_date: str) -> tuple[str, dict[str, Any]]:
    return (
        "Get",
        {
            "typeName": "StatusData",
            "search": {
                "deviceSearch": {"id": device_id},
                "diagnosticSearch": {"id": diagnostic_id},
                "fromDate": from_date,
                "toDate": to_date,
            },
        },
    )


def _trips_call(device_id: str, from_date: str, to_date: str) -> tuple[str, dict[str, Any]]:
    return (
        "Get",
        {
            "typeName": "Trip",
            "search": {
                "deviceSearch": {"id": device_id},
                "fromDate": from_date,
                "toDate": to_date,
            },
        },
    )


def _edge_windows(from_date: str, to_date: str, days: int) -> tuple[tuple[str, str], tuple[str, str]] | None:
    """Ventanas [from, from+days] y [to-days, to]; None si el rango es tan corto que se solapan."""
    if days <= 0:
        return None
    try:
        start = _parse_geotab_datetime(from_date)
        end = _parse_geotab_datetime(to_date)
    except (TypeError, ValueError):
        return None
    span = end - start
    if span <= timedelta(days=2 * days):
        return None
    head_end = (start + timedelta(days=days)).strftime(_GEOTAB_DATE_FMT)
    tail_start = (end - timedelta(days=days)).strftime(_GEOTAB_DATE_FMT)
    return (from_date, head_end), (tail_start, to_date)


def _build_bundle_calls(
    device_id: str,
    from_date: str,
    to_date: str,
    *,
    status_diagnostics: dict[str, str],
    include_trips: bool,
    edge_only: frozenset[str],
    edge_days: int,
) -> tuple[list[tuple[str, str]], list[tuple[str, dict[str, Any]]]]:
    """Construye los Gets de un device.

    Devuelve ``(slots, calls)`` con un slot ``(key, kind)`` por llamada, donde
    kind es ``status`` (serie completa), ``trips``, ``edge_head`` o ``edge_tail``
    (ventanas borde de un diagnostico del que solo interesa primera/ultima lectura).
    """
    slots: list[tuple[str, str]] = []
    calls: list[tuple[str, dict[str, Any]]] = []
    windows = _edge_windows(from_date, to_date, edge_days) if edge_only else None
    for key, diagnostic_id in status_diagnostics.items():
        if windows is not None and key in edge_only:
            (head_from, head_to), (tail_from, tail_to) = windows
            slots.append((key, "edge_head"))
            calls.append(_status_data_call(device_id, diagnostic_id, head_from, head_to))
            slots.append((key, "edge_tail"))
            calls.append(_status_data_call(device_id, diagnostic_id, tail_from, tail_to))
        else:
            slots.append((key, "status"))
            calls.append(_status_data_call(device_id, diagnostic_id, from_date, to_date))
    if include_trips:
        slots.append(("trips", "trips"))
        calls.append(_trips_call(device_id, from_date, to_date))
    return slots, calls


def _assemble_bundle(
    slots: list[tuple[str, str]],
    results: list[list[dict]],
    *,
    include_trips: bool,
) -> tuple[dict[str, list[dict]], list[str]]:
    """Reconstruye el bundle a partir de los resultados en el orden de ``slots``.

    Devuelve ``(bundle, needs_full)``: ``needs_full`` son las claves borde cuya
    ventana inicial o final vino vacia y requieren la serie completa para no
    perder la primera/ultima lectura real del periodo.
    """
    if len(results) != len(slots):
        raise GeotabBundleShapeError(
            f"multi_call devolvio {len(results)} resultados para {len(slots)} llamadas"
        )
    bundle: dict[str, list[dict]] = {}
    edges: dict[str, dict[str, list[dict]]] = {}
    for (key, kind), result in zip(slots, results):
        records = list(result or [])
        if kind == "trips":
            bundle[key] = records
        elif kind == "status":
            bundle[key] = _sort_by_datetime(records)
        else:
            edges.setdefault(key, {})[kind] = _sort_by_datetime(records)
    needs_full: list[str] = []
    for key, parts in edges.items():
        head = parts.get("edge_head") or []
        tail = parts.get("edge_tail") or []
        if head and tail:
            bundle[key] = [head[0], tail[-1]]
        else:
            bundle[key] = []
            needs_full.append(key)
    if include_trips:
        bundle.setdefault("trips", [])
    return bundle, needs_full


def _fetch_bundle_individually(
    api: mygeotab.API,
    device_id: str,
    from_date: str,
    to_date: str,
    *,
    status_diagnostics: dict[str, str],
    include_trips: bool,
) -> dict[str, list[dict]]:
    return {
        **{
            key: get_status_data_for_month(api, device_id, diagnostic_id, from_date, to_date)
            for key, diagnostic_id in status_diagnostics.items()
        },
        "trips": get_trips_for_month(api, device_id, from_date, to_date) if include_trips else [],
    }


def _fill_full_series(
    api: mygeotab.API,
    bundles: dict[str, dict[str, list[dict]]],
    pending: list[tuple[str, str]],
    from_date: str,
    to_date: str,
    *,
    status_diagnostics: dict[str, str],
) -> None:
    """Segunda pasada para las claves borde con ventana vacia: series completas en
    multi_calls de a ``BUNDLE_FULL_SERIES_PER_MULTICALL`` (payloads grandes)."""
    if not pending:
        return
    size = max(1, BUNDLE_FULL_SERIES_PER_MULTICALL)
    for start in range(0, len(pending), size):
        group = pending[start : start + size]
        calls = [_status_data_call(device_id, status_diagnostics[key], from_date, to_date) for device_id, key in group]
        try:
            results = multi_call_with_retry(api, calls)
            if len(results) != len(calls):
                raise GeotabBundleShapeError(
                    f"multi_call devolvio {len(results)} resultados para {len(calls)} llamadas"
                )
        except Exception as exc:
            if _classify_error(exc) != "fatal":
                raise
            _logger.warning("Geotab multi_call de series completas fallo (%s); usando llamadas individuales.", exc)
            for device_id, key in group:
                bundles[device_id][key] = get_status_data_for_month(
                    api, device_id, status_diagnostics[key], from_date, to_date
                )
            continue
        for (device_id, key), result in zip(group, results):
            bundles[device_id][key] = _sort_by_datetime(list(result or []))


def get_month_data_bundle(
    api: mygeotab.API,
    device_id: str,
    from_date: str,
    to_date: str,
    *,
    status_diagnostics: dict[str, str],
    include_trips: bool = True,
    edge_only: frozenset[str] | set[str] | tuple[str, ...] = frozenset(),
    edge_days: int | None = None,
) -> dict[str, list[dict]]:
    """
    Fetches a bundle of StatusData diagnostics plus optional Trips for a device
    in a single multi_call batch. StatusData results are sorted by dateTime;
    trips are returned as-is.

    ``edge_only`` (G5): claves de ``status_diagnostics`` de las que solo se
    necesita la primera y la ultima lectura (combustible acumulado). Para ellas
    se piden dos ventanas cortas en vez del mes completo y el bundle trae <= 2
    lecturas ordenadas; si alguna ventana viene vacia se pide la serie completa
    en una segunda pasada, asi la semantica (primera/ultima del periodo) no cambia.

    Falls back to individual calls ONLY if the multi_call fails with a fatal
    (logic) error, e.g. one of the Gets is invalid. Auth, rate-limit and
    transient exhaustion are re-raised immediately so retries are not multiplied.
    """
    edge_keys = frozenset(edge_only)
    slots, calls = _build_bundle_calls(
        device_id,
        from_date,
        to_date,
        status_diagnostics=status_diagnostics,
        include_trips=include_trips,
        edge_only=edge_keys,
        edge_days=BUNDLE_EDGE_WINDOW_DAYS if edge_days is None else edge_days,
    )

    try:
        results = multi_call_with_retry(api, calls)
        bundle, needs_full = _assemble_bundle(slots, results, include_trips=include_trips)
    except Exception as exc:
        if _classify_error(exc) != "fatal":
            raise
        _logger.warning("Geotab multi_call fallo (%s); usando llamadas individuales.", exc)
        return _fetch_bundle_individually(
            api, device_id, from_date, to_date, status_diagnostics=status_diagnostics, include_trips=include_trips
        )

    bundles = {device_id: bundle}
    _fill_full_series(
        api,
        bundles,
        [(device_id, key) for key in needs_full],
        from_date,
        to_date,
        status_diagnostics=status_diagnostics,
    )
    return bundle


def get_month_data_bundles(
    api: mygeotab.API,
    device_ids: list[str],
    *,
    from_date: str,
    to_date: str,
    status_diagnostics: dict[str, str],
    include_trips: bool = True,
    edge_only: frozenset[str] | set[str] | tuple[str, ...] = frozenset(),
    edge_days: int | None = None,
    chunk_size: int | None = None,
) -> dict[str, dict[str, list[dict]]]:
    """Bundles de varios devices en multi_calls por chunk (G1).

    Construye exactamente los mismos Gets que `get_month_data_bundle` para cada
    device, los concatena de a ``chunk_size`` devices (env
    ``GEOTAB_BUNDLE_CHUNK_DEVICES``; por defecto ``GEOTAB_BUNDLE_MAX_CALLS`` //
    Gets por device) y hace UN multi_call por chunk. Los resultados se rebanan
    por device y pasan por el mismo post-proceso, asi el shape es identico.

    Si un chunk falla con error ``fatal`` se cae a `get_month_data_bundle` por
    device solo para ese chunk; auth / rate_limit / transient agotados se
    re-lanzan (la politica de reintentos ya corrio dentro de `_call_with_retry`).
    """
    edge_keys = frozenset(edge_only)
    effective_edge_days = BUNDLE_EDGE_WINDOW_DAYS if edge_days is None else edge_days
    ordered_ids = list(dict.fromkeys(str(device_id) for device_id in device_ids if device_id))
    bundles: dict[str, dict[str, list[dict]]] = {}
    if not ordered_ids:
        return bundles

    per_device: dict[str, tuple[list[tuple[str, str]], list[tuple[str, dict[str, Any]]]]] = {
        device_id: _build_bundle_calls(
            device_id,
            from_date,
            to_date,
            status_diagnostics=status_diagnostics,
            include_trips=include_trips,
            edge_only=edge_keys,
            edge_days=effective_edge_days,
        )
        for device_id in ordered_ids
    }
    calls_per_device = len(per_device[ordered_ids[0]][1])
    size = _bundle_chunk_size(calls_per_device, chunk_size)

    for start in range(0, len(ordered_ids), size):
        chunk_ids = ordered_ids[start : start + size]
        calls: list[tuple[str, dict[str, Any]]] = []
        for device_id in chunk_ids:
            calls.extend(per_device[device_id][1])

        try:
            results = multi_call_with_retry(api, calls)
            if len(results) != len(calls):
                raise GeotabBundleShapeError(
                    f"multi_call devolvio {len(results)} resultados para {len(calls)} llamadas"
                )
        except Exception as exc:
            if _classify_error(exc) != "fatal":
                raise
            _logger.warning(
                "Geotab multi_call de %d devices fallo (%s); usando bundles individuales para el chunk.",
                len(chunk_ids),
                exc,
            )
            for device_id in chunk_ids:
                bundles[device_id] = get_month_data_bundle(
                    api,
                    device_id,
                    from_date,
                    to_date,
                    status_diagnostics=status_diagnostics,
                    include_trips=include_trips,
                    edge_only=edge_keys,
                    edge_days=effective_edge_days,
                )
            continue

        pending_full: list[tuple[str, str]] = []
        offset = 0
        for device_id in chunk_ids:
            slots = per_device[device_id][0]
            bundle, needs_full = _assemble_bundle(
                slots, results[offset : offset + len(slots)], include_trips=include_trips
            )
            offset += len(slots)
            bundles[device_id] = bundle
            pending_full.extend((device_id, key) for key in needs_full)
        _fill_full_series(api, bundles, pending_full, from_date, to_date, status_diagnostics=status_diagnostics)

    return bundles
