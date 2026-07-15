from __future__ import annotations

import os
from dataclasses import dataclass


def _required_env(name: str) -> str:
    value = os.getenv(name)
    if value is None or not value.strip():
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value.strip()


@dataclass(frozen=True)
class Settings:
    app_name: str = os.getenv("APP_NAME", "Navi Vehiculos API")
    api_prefix: str = os.getenv("API_PREFIX", "/api/v1")
    frontend_upstream: str = os.getenv("FRONTEND_UPSTREAM", "http://frontend:5173")
    cors_origins: tuple[str, ...] = tuple(
        origin.strip()
        for origin in os.getenv("CORS_ORIGINS", "http://localhost:8090").split(",")
        if origin.strip()
    )
    jwt_secret: str = _required_env("JWT_SECRET")
    jwt_algorithm: str = os.getenv("JWT_ALGORITHM", "HS256")
    jwt_expire_minutes: int = int(os.getenv("JWT_EXPIRE_MINUTES", "15"))
    refresh_token_expire_days: int = int(os.getenv("REFRESH_TOKEN_EXPIRE_DAYS", "7"))
    redis_url: str = os.getenv("REDIS_URL", "redis://redis:6379/0")
    environment: str = os.getenv("ENVIRONMENT", "development").strip().lower()
    max_concurrent_sessions: int = int(os.getenv("MAX_CONCURRENT_SESSIONS", "0"))

    # Webhook Geotab "taller" (consultar docs/plan-mapa-taller-geotab.md).
    geotab_webhook_api_keys: tuple[str, ...] = tuple(
        key.strip()
        for key in os.getenv("GEOTAB_WEBHOOK_API_KEYS", "").split(",")
        if key.strip()
    )
    geotab_taller_rule_enter: str = os.getenv(
        "GEOTAB_TALLER_RULE_ENTER", "En taller - Proyecto"
    ).strip()
    geotab_taller_rule_exit: str = os.getenv(
        "GEOTAB_TALLER_RULE_EXIT", "Salida taller - Proyecto"
    ).strip()
    taller_min_minutes: int = int(os.getenv("TALLER_MIN_MINUTES", "10"))
    taller_grace_hours: int = int(os.getenv("TALLER_GRACE_HOURS", "1"))
    taller_in_ttl_days: int = int(os.getenv("TALLER_IN_TTL_DAYS", "30"))
    mapa_snapshot_ttl_seconds: int = int(os.getenv("MAPA_SNAPSHOT_TTL_SECONDS", "60"))
    # Ventana (horas) para mostrar en el mapa vehiculos que ya salieron (marcador
    # atenuado) y dias de historico enter->exit consultable desde el modal.
    taller_exited_map_hours: int = int(os.getenv("TALLER_EXITED_MAP_HOURS", "24"))
    taller_history_days: int = int(os.getenv("TALLER_HISTORY_DAYS", "45"))

    @property
    def is_production(self) -> bool:
        return self.environment == "production"


@dataclass(frozen=True)
class GeotabConfig:
    username: str
    password: str
    database: str


@dataclass(frozen=True)
class SqlConfig:
    server: str
    user: str
    password: str
    database: str
    port: int


@dataclass(frozen=True)
class QuickServeConfig:
    username: str
    password: str
    app_id: str
    sf_base: str
    base_url: str


@dataclass(frozen=True)
class CloudFleetConfig:
    api_key: str
    base_url: str
    timeout: float
    request_delay: float
    rate_limit_delay: float
    max_retries: int


settings = Settings()


def load_geotab_config() -> GeotabConfig:
    return GeotabConfig(
        username=_required_env("GEOTAB_USERNAME"),
        password=_required_env("GEOTAB_PASSWORD"),
        database=_required_env("GEOTAB_DATABASE"),
    )


def load_sql_config() -> SqlConfig:
    return SqlConfig(
        server=_required_env("INVENTORY_DB_SERVER"),
        user=_required_env("INVENTORY_DB_USER"),
        password=_required_env("INVENTORY_DB_PASSWORD"),
        database=_required_env("INVENTORY_DB_NAME"),
        port=int(os.getenv("INVENTORY_DB_PORT", "1433")),
    )


def load_quickserve_config() -> QuickServeConfig:
    return QuickServeConfig(
        username=_required_env("QUICKSERVE_USERNAME"),
        password=_required_env("QUICKSERVE_PASSWORD"),
        app_id=_required_env("QUICKSERVE_APP_ID"),
        sf_base=os.getenv("QUICKSERVE_SF_BASE", "https://mylogin.cummins.com").rstrip("/"),
        base_url=os.getenv("QUICKSERVE_BASE_URL", "https://quickserve.cummins.com").rstrip("/"),
    )


def load_cloudfleet_config() -> CloudFleetConfig | None:
    """
    Carga config CloudFleet desde env. Devuelve None si CLOUDFLEET_API_KEY
    no esta definido — el caller debe interpretar eso como "feature deshabilitada".
    """
    api_key = (os.getenv("CLOUDFLEET_API_KEY") or "").strip()
    if not api_key:
        return None
    return CloudFleetConfig(
        api_key=api_key,
        base_url=os.getenv("CLOUDFLEET_API_URL", "https://fleet.cloudfleet.com/api/v1").rstrip("/"),
        timeout=float(os.getenv("CLOUDFLEET_HTTP_TIMEOUT", "45")),
        request_delay=float(os.getenv("CLOUDFLEET_REQUEST_DELAY", "2.0")),
        rate_limit_delay=float(os.getenv("CLOUDFLEET_RATE_LIMIT_DELAY", "10.0")),
        max_retries=int(os.getenv("CLOUDFLEET_MAX_RETRIES", "3")),
    )
