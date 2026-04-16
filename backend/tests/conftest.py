from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import urlparse, urlunparse

import httpx
import psycopg
import pytest
import redis
from alembic import command
from alembic.config import Config
from psycopg.rows import dict_row


def _derive_test_database_url() -> str:
    raw_url = os.environ["DATABASE_URL"].replace("postgresql+psycopg://", "postgresql://", 1)
    parsed = urlparse(raw_url)
    db_name = parsed.path.lstrip("/") or "postgres"
    test_name = f"{db_name}_test"
    return urlunparse(parsed._replace(path=f"/{test_name}"))


def _derive_test_redis_url() -> str:
    raw_url = os.environ.get("REDIS_URL", "redis://redis:6379/0")
    parsed = urlparse(raw_url)
    return urlunparse(parsed._replace(path="/15"))


TEST_DATABASE_URL = os.environ.get("DATABASE_URL_TEST", "").strip() or _derive_test_database_url()
TEST_REDIS_URL = os.environ.get("REDIS_URL_TEST", "").strip() or _derive_test_redis_url()
os.environ["DATABASE_URL"] = TEST_DATABASE_URL
os.environ["REDIS_URL"] = TEST_REDIS_URL

from app.core.rate_limit import limiter
from app.services.auth_service import (  # noqa: E402
    create_user,
    get_refresh_token_record,
    get_user_by_id,
    get_user_permissions,
    update_user,
)


ADMIN_PASSWORD = "AdminPass1!"
EDITOR_PASSWORD = "EditorPass1!"
VIEWER_PASSWORD = "ViewerPass1!"


def _admin_database_url(database_url: str) -> str:
    parsed = urlparse(database_url.replace("postgresql+psycopg://", "postgresql://", 1))
    return urlunparse(parsed._replace(path="/postgres"))


def _database_name(database_url: str) -> str:
    return urlparse(database_url.replace("postgresql+psycopg://", "postgresql://", 1)).path.lstrip("/")


@pytest.fixture(scope="session", autouse=True)
def setup_test_database() -> None:
    admin_url = _admin_database_url(TEST_DATABASE_URL)
    test_db_name = _database_name(TEST_DATABASE_URL)
    with psycopg.connect(admin_url, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute(f"DROP DATABASE IF EXISTS {test_db_name}")
            cur.execute(f"CREATE DATABASE {test_db_name}")

    alembic_ini = Path(__file__).resolve().parents[1] / "alembic.ini"
    config = Config(str(alembic_ini))
    config.set_main_option("script_location", str(Path(__file__).resolve().parents[1] / "app" / "migrations"))
    command.upgrade(config, "head")
    yield
    with psycopg.connect(admin_url, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT pg_terminate_backend(pid)
                FROM pg_stat_activity
                WHERE datname = %s AND pid <> pg_backend_pid()
                """,
                (test_db_name,),
            )
            cur.execute(f"DROP DATABASE IF EXISTS {test_db_name}")


@pytest.fixture
def redis_client():
    client = redis.from_url(TEST_REDIS_URL, decode_responses=True)
    client.flushdb()
    yield client
    client.flushdb()


@pytest.fixture(autouse=True)
def clean_state(redis_client) -> None:
    with psycopg.connect(TEST_DATABASE_URL, row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            cur.execute("TRUNCATE TABLE refresh_tokens, audit_logs, users RESTART IDENTITY CASCADE")
        conn.commit()
    storage = getattr(limiter, "_storage", None)
    if storage and hasattr(storage, "reset"):
        storage.reset()


@pytest.fixture
def admin_user() -> dict:
    create_user("admin", "admin@example.com", ADMIN_PASSWORD, "admin")
    return get_user_by_id(1)


@pytest.fixture
def editor_user() -> dict:
    create_user("editor", "editor@example.com", EDITOR_PASSWORD, "editor")
    return get_user_by_id(1)


@pytest.fixture
def viewer_user() -> dict:
    create_user("viewer", "viewer@example.com", VIEWER_PASSWORD, "viewer")
    return get_user_by_id(1)


@pytest.fixture
def seeded_users(admin_user, editor_user, viewer_user) -> dict:
    return {
        "admin": get_user_by_id(1),
        "editor": get_user_by_id(2),
        "viewer": get_user_by_id(3),
    }


@pytest.fixture
def inactive_user() -> dict:
    create_user("inactive", "inactive@example.com", "Inactive1!", "viewer")
    update_user(1, None, None, False)
    return get_user_by_id(1)


@pytest.fixture
def test_app(setup_test_database):
    from app.main import app

    return app


@pytest.fixture
def client_factory(test_app):
    def _factory(ip_address: str = "127.0.0.1") -> httpx.AsyncClient:
        transport = httpx.ASGITransport(app=test_app, client=(ip_address, 12345))
        return httpx.AsyncClient(transport=transport, base_url="http://testserver")

    return _factory


@pytest.fixture
async def client(client_factory):
    async with client_factory() as async_client:
        yield async_client


@pytest.fixture
def auth_helpers():
    async def login(async_client: httpx.AsyncClient, username: str, password: str) -> httpx.Response:
        return await async_client.post(
            "/api/v1/auth/login",
            json={"username": username, "password": password},
        )

    return {
        "login": login,
        "get_refresh_record": get_refresh_token_record,
        "get_permissions": get_user_permissions,
    }
