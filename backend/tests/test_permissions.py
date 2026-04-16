from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.api.routes import customer as customer_routes
from app.services.auth_service import get_user_permissions


pytestmark = pytest.mark.asyncio


async def test_viewer_cannot_access_write_endpoints(client, viewer_user, auth_helpers):
    await auth_helpers["login"](client, "viewer", "ViewerPass1!")
    response = await client.post(
        "/api/v1/users",
        json={
            "username": "nuevo",
            "email": "nuevo@example.com",
            "password": "StrongPass1!",
            "role": "viewer",
        },
    )

    assert response.status_code == 403


async def test_editor_can_create_customer_but_not_manage_users(client, editor_user, auth_helpers, monkeypatch):
    await auth_helpers["login"](client, "editor", "EditorPass1!")

    monkeypatch.setattr(
        customer_routes,
        "create_customer",
        lambda payload: {
            "id": 1,
            "name": payload.name,
            "database_count": 0,
            "databases": [],
            "created_at": datetime.now(tz=timezone.utc),
            "updated_at": datetime.now(tz=timezone.utc),
        },
    )

    create_response = await client.post(
        "/api/v1/customers",
        json={"name": "Cliente Demo"},
    )
    users_response = await client.get("/api/v1/users")

    assert create_response.status_code == 201
    assert users_response.status_code == 403


async def test_admin_has_full_access_to_user_management(client, admin_user, auth_helpers):
    await auth_helpers["login"](client, "admin", "AdminPass1!")

    list_response = await client.get("/api/v1/users")
    create_response = await client.post(
        "/api/v1/users",
        json={
            "username": "qauser",
            "email": "qauser@example.com",
            "password": "StrongPass1!",
            "role": "viewer",
        },
    )

    assert list_response.status_code == 200
    assert create_response.status_code == 201


async def test_permissions_are_cached_in_redis(client, viewer_user, auth_helpers, redis_client):
    assert redis_client.exists("perm:viewer") == 0

    await auth_helpers["login"](client, "viewer", "ViewerPass1!")
    permissions = get_user_permissions("viewer")

    assert "motors.list" in permissions
    assert redis_client.exists("perm:viewer") == 1
