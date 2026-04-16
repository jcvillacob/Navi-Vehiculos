from __future__ import annotations

import pytest

from app.services.auth_service import validate_password_strength


pytestmark = pytest.mark.asyncio


def test_password_strength_rejects_weak_passwords():
    errors = validate_password_strength("12345678", "viewer")

    assert errors
    assert any("10 caracteres" in error for error in errors)


async def test_change_password_invalidates_previous_sessions(
    client_factory,
    viewer_user,
    auth_helpers,
):
    async with client_factory("10.1.0.1") as current_client, client_factory("10.1.0.2") as other_client:
        login_current = await auth_helpers["login"](current_client, "viewer", "ViewerPass1!")
        old_current_refresh = login_current.cookies.get("refresh_token")
        await auth_helpers["login"](other_client, "viewer", "ViewerPass1!")

        change_response = await current_client.put(
            "/api/v1/auth/password",
            json={"current_password": "ViewerPass1!", "new_password": "ViewerPass2!"},
        )
        other_refresh_response = await other_client.post("/api/v1/auth/refresh")
        old_record = auth_helpers["get_refresh_record"](old_current_refresh)
        relogin_old_password = await auth_helpers["login"](current_client, "viewer", "ViewerPass1!")
        relogin_new_password = await auth_helpers["login"](current_client, "viewer", "ViewerPass2!")

    assert change_response.status_code == 200
    assert other_refresh_response.status_code == 401
    assert old_record is not None and old_record["revoked"] is True
    assert relogin_old_password.status_code == 401
    assert relogin_new_password.status_code == 200


async def test_admin_can_reset_another_user_password(client, client_factory, seeded_users, auth_helpers):
    await auth_helpers["login"](client, "admin", "AdminPass1!")

    response = await client.post(
        f"/api/v1/users/{seeded_users['viewer']['id']}/reset-password",
        json={"new_password": "ViewerPass2!"},
    )

    assert response.status_code == 200

    async with client_factory("10.1.0.3") as fresh_client:
        old_login = await auth_helpers["login"](fresh_client, "viewer", "ViewerPass1!")
        new_login = await auth_helpers["login"](fresh_client, "viewer", "ViewerPass2!")

    assert old_login.status_code == 401
    assert new_login.status_code == 200
