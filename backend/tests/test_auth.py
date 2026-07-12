from __future__ import annotations

import pytest


pytestmark = pytest.mark.asyncio


async def test_login_success_sets_cookies_and_security_headers(client, viewer_user, auth_helpers):
    response = await auth_helpers["login"](client, "viewer", "ViewerPass1!")

    assert response.status_code == 200
    assert response.json()["username"] == "viewer"
    assert response.cookies.get("access_token")
    assert response.cookies.get("refresh_token")
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["X-Frame-Options"] == "DENY"


async def test_login_with_bad_credentials_returns_401(client, viewer_user, auth_helpers):
    response = await auth_helpers["login"](client, "viewer", "wrong-password")

    assert response.status_code == 401


async def test_login_inactive_user_returns_403(client, inactive_user, auth_helpers):
    response = await auth_helpers["login"](client, "inactive", "Inactive1!")

    assert response.status_code == 403


async def test_lockout_after_five_failed_attempts(client_factory, viewer_user, auth_helpers):
    for attempt in range(5):
        async with client_factory(f"10.0.0.{attempt + 1}") as current_client:
            response = await auth_helpers["login"](current_client, "viewer", "wrong-password")
            assert response.status_code == 401

    async with client_factory("10.0.0.99") as locked_client:
        locked_response = await auth_helpers["login"](locked_client, "viewer", "ViewerPass1!")

    assert locked_response.status_code == 423


async def test_rate_limit_on_login_returns_429(client_factory, auth_helpers):
    async with client_factory("10.10.10.10") as rate_limited_client:
        for _ in range(5):
            response = await auth_helpers["login"](rate_limited_client, "missing-user", "wrong-password")
            assert response.status_code == 401

        response = await auth_helpers["login"](rate_limited_client, "missing-user", "wrong-password")

    assert response.status_code == 429
    assert "Retry-After" in response.headers


async def test_me_without_cookie_returns_401(client):
    response = await client.get("/api/v1/auth/me")

    assert response.status_code == 401


async def test_me_with_cookie_returns_user(client, viewer_user, auth_helpers):
    await auth_helpers["login"](client, "viewer", "ViewerPass1!")
    response = await client.get("/api/v1/auth/me")

    assert response.status_code == 200
    # Comparar contra la fuente de verdad en vez de hardcodear la lista:
    # el rol viewer gana permisos cada vez que se agregan modulos nuevos.
    from app.services.auth_service import get_user_permissions

    assert response.json()["permissions"] == sorted(get_user_permissions("viewer"))
    assert "dashboard.view" in response.json()["permissions"]


async def test_refresh_rotates_tokens(client, viewer_user, auth_helpers):
    login_response = await auth_helpers["login"](client, "viewer", "ViewerPass1!")
    old_refresh_token = login_response.cookies.get("refresh_token")

    refresh_response = await client.post("/api/v1/auth/refresh")
    new_refresh_token = refresh_response.cookies.get("refresh_token")
    old_record = auth_helpers["get_refresh_record"](old_refresh_token)
    new_record = auth_helpers["get_refresh_record"](new_refresh_token)

    assert refresh_response.status_code == 200
    assert new_refresh_token
    assert new_refresh_token != old_refresh_token
    assert old_record is not None and old_record["revoked"] is True
    assert new_record is not None and new_record["revoked"] is False


async def test_logout_revokes_refresh_tokens(client, viewer_user, auth_helpers):
    login_response = await auth_helpers["login"](client, "viewer", "ViewerPass1!")
    refresh_token = login_response.cookies.get("refresh_token")

    response = await client.post("/api/v1/auth/logout")
    record = auth_helpers["get_refresh_record"](refresh_token)

    assert response.status_code == 200
    assert record is not None and record["revoked"] is True
