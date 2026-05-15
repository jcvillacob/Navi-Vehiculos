from __future__ import annotations

from datetime import datetime, timedelta, timezone
import re
import threading
import time as _time
from typing import Any

import mygeotab

from app.core.config import GeotabConfig

_PLATE_PATTERN = re.compile(r"^[A-Z]{3}[0-9]{3}$")


def build_client(cfg: GeotabConfig):
    client = mygeotab.API(
        username=cfg.username,
        password=cfg.password,
        database=cfg.database,
    )
    client.authenticate()
    return client


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
    response = client.call("Get", typeName=type_name, search=search or {})
    return response or []


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


def _find_device_in_collection(
    devices: list[dict], *, plate: str | None = None, vin: str | None = None, plate_prefix: str | None = None
) -> dict | None:
    for device in devices:
        if plate and _device_matches_plate(device, plate, plate_prefix=plate_prefix):
            return device
        if vin and _device_matches_vin(device, vin):
            return device
    return None


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

    api = mygeotab.API(username=username, password=password, database=database)
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

    with _DEVICE_CACHE_LOCK:
        cached = _DEVICE_CACHE.get(cache_key)
        if cached is not None:
            expires_at, devices = cached
            if expires_at > _time.time():
                return devices
            _DEVICE_CACHE.pop(cache_key, None)

        client = get_authenticated_client(username, password, database)
        devices = _get_all_devices(client)
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
NET_MAX_RETRIES = 3
NET_RETRY_WAITS = (10, 20, 30)


def _is_network_error(exc: Exception) -> bool:
    msg = str(exc)
    return any(fragment in msg for fragment in _NETWORK_ERROR_FRAGMENTS)


def _api_call_with_retry(api: mygeotab.API, method: str, **kwargs) -> list[dict]:
    """
    Calls api.call(method, **kwargs) with retries on transient network errors.
    Logic errors (auth, permissions, bad data) propagate immediately.
    """
    last_exc: Exception | None = None
    for attempt in range(NET_MAX_RETRIES):
        try:
            result = api.call(method, **kwargs)
            return result or []
        except Exception as exc:
            if _is_network_error(exc) and attempt < NET_MAX_RETRIES - 1:
                last_exc = exc
                _time.sleep(NET_RETRY_WAITS[attempt])
                continue
            raise
    raise last_exc  # type: ignore[misc]


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
    return sorted(results, key=lambda r: str(r.get("dateTime") or ""))


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
