from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ProviderDefinition:
    key: str
    label: str
    description: str
    supports_monthly_performance: bool = False
    uses_access_url: bool = True


_PROVIDERS: dict[str, ProviderDefinition] = {
    "database": ProviderDefinition(
        key="database",
        label="Database",
        description="Conexion generica o acceso manual.",
        supports_monthly_performance=False,
        uses_access_url=True,
    ),
    "geotab": ProviderDefinition(
        key="geotab",
        label="Geotab",
        description="Telematica Geotab con reglas por motor.",
        supports_monthly_performance=True,
        uses_access_url=False,
    ),
    "artimo": ProviderDefinition(
        key="artimo",
        label="Artimo",
        description="Telematica Artimo para rendimientos mensuales.",
        supports_monthly_performance=True,
        uses_access_url=False,
    ),
    "frotcom": ProviderDefinition(
        key="frotcom",
        label="Frotcom",
        description="Telematica Frotcom para rendimientos mensuales (credenciales por database).",
        supports_monthly_performance=True,
        uses_access_url=True,
    ),
    "logitracs_triton": ProviderDefinition(
        key="logitracs_triton",
        label="LogiTracs Triton",
        description="Telematica LogiTracs Triton (informe operacional de flota mensual).",
        supports_monthly_performance=True,
        uses_access_url=False,
    ),
}


def list_supported_providers() -> list[ProviderDefinition]:
    return list(_PROVIDERS.values())


def get_provider_definition(value: str | None) -> ProviderDefinition:
    return _PROVIDERS[normalize_provider_key(value)]


def normalize_provider_key(value: str | None) -> str:
    normalized = (value or "database").strip().lower()
    if normalized not in _PROVIDERS:
        return "database"
    return normalized


def supports_monthly_performance(value: str | None) -> bool:
    return get_provider_definition(value).supports_monthly_performance


def uses_access_url(value: str | None) -> bool:
    return get_provider_definition(value).uses_access_url


def _normalize_optional_text(value: Any) -> str | None:
    if value is None:
        return None
    cleaned = str(value).strip()
    return cleaned or None


def _normalize_optional_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _normalize_token(value: str | None) -> str:
    raw = (value or "").strip().lower()
    if not raw:
        return ""
    normalized = unicodedata.normalize("NFKD", raw)
    return "".join(char for char in normalized if not unicodedata.combining(char))


def _looks_like_artimo(
    *,
    database_name: str | None = None,
    access_url: str | None = None,
    provider_config: dict[str, Any] | None = None,
) -> bool:
    config = provider_config if isinstance(provider_config, dict) else {}
    normalized_name = _normalize_token(database_name)
    normalized_access_url = _normalize_token(access_url)

    if "artimo" in normalized_name:
        return True
    if "artimo" in normalized_access_url:
        return True

    customer_id = _normalize_optional_text(config.get("customer_id"))
    group_name = _normalize_optional_text(config.get("group_name"))
    api_base_url = _normalize_token(config.get("api_base_url"))
    auth_base_url = _normalize_token(config.get("auth_base_url"))

    if customer_id or group_name:
        return True
    return "artimo" in api_base_url or "artimo" in auth_base_url


def _looks_like_frotcom(
    *,
    database_name: str | None = None,
    access_url: str | None = None,
) -> bool:
    if "frotcom" in _normalize_token(database_name):
        return True
    if "frotcom" in _normalize_token(access_url):
        return True
    return False


def _looks_like_logitracs_triton(
    *,
    database_name: str | None = None,
    access_url: str | None = None,
    provider_config: dict[str, Any] | None = None,
) -> bool:
    config = provider_config if isinstance(provider_config, dict) else {}
    if "logitracs" in _normalize_token(database_name):
        return True
    if "triton" in _normalize_token(database_name):
        return True
    if "logitracs" in _normalize_token(access_url):
        return True
    if "triton" in _normalize_token(access_url):
        return True
    if _normalize_optional_text(config.get("codigo_empresa")):
        return True
    return False


def infer_provider_key(
    *,
    connection_type: str | None,
    database_name: str | None = None,
    access_url: str | None = None,
    provider_config: dict[str, Any] | None = None,
) -> str:
    normalized = normalize_provider_key(connection_type)
    if normalized != "database":
        return normalized
    if _looks_like_artimo(
        database_name=database_name,
        access_url=access_url,
        provider_config=provider_config,
    ):
        return "artimo"
    if _looks_like_frotcom(database_name=database_name, access_url=access_url):
        return "frotcom"
    if _looks_like_logitracs_triton(
        database_name=database_name,
        access_url=access_url,
        provider_config=provider_config,
    ):
        return "logitracs_triton"
    return normalized


def normalize_provider_config(
    provider_key: str,
    provider_config: dict[str, Any] | None,
    *,
    username: str | None = None,
    password: str | None = None,
    existing_provider_config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    raw = provider_config if isinstance(provider_config, dict) else {}
    existing = existing_provider_config if isinstance(existing_provider_config, dict) else {}
    normalized_provider_key = normalize_provider_key(provider_key)

    if normalized_provider_key == "artimo":
        normalized = {
            "api_base_url": _normalize_optional_text(
                raw.get("api_base_url") or existing.get("api_base_url")
            )
            or "https://api.artimo.com.co",
            "auth_base_url": _normalize_optional_text(
                raw.get("auth_base_url") or existing.get("auth_base_url")
            )
            or "https://apifront.artimo.com.co",
            "customer_id": _normalize_optional_text(
                raw.get("customer_id") or existing.get("customer_id")
            ),
            "group_name": _normalize_optional_text(
                raw.get("group_name") or existing.get("group_name")
            ),
            "month_start_hour_utc": _normalize_optional_int(
                raw.get("month_start_hour_utc", existing.get("month_start_hour_utc")),
                5,
            ),
            "month_end_hour_utc": _normalize_optional_int(
                raw.get("month_end_hour_utc", existing.get("month_end_hour_utc")),
                16,
            ),
        }
        if username:
            normalized["username"] = username
        elif existing.get("username"):
            normalized["username"] = existing["username"]
        if password:
            normalized["password"] = password
        elif existing.get("password"):
            normalized["password"] = existing["password"]
        return normalized

    if normalized_provider_key == "logitracs_triton":
        normalized = {
            "codigo_empresa": _normalize_optional_text(raw.get("codigo_empresa")) or "",
            "triton_login_url": _normalize_optional_text(
                raw.get("triton_login_url") or existing.get("triton_login_url")
            )
            or "https://triton.logitracs.com/Logitracs.Triton/api/Usuarios/Login",
            "logivim_base_url": _normalize_optional_text(
                raw.get("logivim_base_url") or existing.get("logivim_base_url")
            )
            or "https://triton.logitracs.com/LogiVIMwebTriton/public",
        }
        pw_web = _normalize_optional_text(raw.get("password_web")) or _normalize_optional_text(
            existing.get("password_web")
        )
        if pw_web:
            normalized["password_web"] = pw_web
        return normalized

    return {}


def public_provider_config(provider_key: str, provider_config: dict[str, Any] | None) -> dict[str, Any]:
    normalized_provider_key = normalize_provider_key(provider_key)
    if not isinstance(provider_config, dict):
        return {}
    if normalized_provider_key == "artimo":
        return {
            "api_base_url": provider_config.get("api_base_url"),
            "auth_base_url": provider_config.get("auth_base_url"),
            "customer_id": provider_config.get("customer_id"),
            "group_name": provider_config.get("group_name"),
            "month_start_hour_utc": provider_config.get("month_start_hour_utc"),
            "month_end_hour_utc": provider_config.get("month_end_hour_utc"),
            "username": provider_config.get("username"),
        }
    if normalized_provider_key == "logitracs_triton":
        return {
            "codigo_empresa": provider_config.get("codigo_empresa"),
            "triton_login_url": provider_config.get("triton_login_url"),
            "logivim_base_url": provider_config.get("logivim_base_url"),
        }
    return {}
