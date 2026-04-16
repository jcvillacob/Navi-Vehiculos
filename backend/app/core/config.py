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
