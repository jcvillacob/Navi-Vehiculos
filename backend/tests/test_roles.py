from __future__ import annotations

import pytest

from app.services.auth_service import (
    create_role,
    delete_role,
    get_role,
    get_role_permissions,
    list_roles,
    replace_role_permissions,
    update_role,
    clear_role_permissions_cache,
    get_user_permissions,
)
from app.services.module_registry import ADMIN_PROTECTED_CODENAMES


pytestmark = pytest.mark.asyncio


# ── Roles CRUD ────────────────────────────────────────────────────────────


async def test_list_roles_includes_seeded_system_roles(seeded_users):
    roles = list_roles()
    keys = {r["key"] for r in roles}
    assert {"admin", "editor", "viewer"}.issubset(keys)
    for r in roles:
        if r["key"] in {"admin", "editor", "viewer"}:
            assert r["is_system"] is True
            assert r["user_count"] >= 1


async def test_create_role_and_list_it(seeded_users):
    new_role = create_role("supervisor", "Supervisor", "Rol custom de pruebas")
    assert new_role["key"] == "supervisor"
    assert new_role["is_system"] is False

    roles = list_roles()
    assert any(r["key"] == "supervisor" for r in roles)


async def test_create_role_with_blank_label_raises(seeded_users):
    with pytest.raises(ValueError):
        create_role("ops", "   ", None)


async def test_create_role_with_duplicate_key_raises(seeded_users):
    create_role("supervisor", "Supervisor")
    with pytest.raises(Exception) as exc_info:
        create_role("supervisor", "Otro")
    assert "Ya existe" in str(exc_info.value)


async def test_update_role_renames_label(seeded_users):
    update_role("viewer", "Solo Lectura", None)
    role = get_role("viewer")
    assert role["label"] == "Solo Lectura"
    # Restauro para no afectar otros tests.
    update_role("viewer", "Visualizador", None)


async def test_update_nonexistent_role_raises(seeded_users):
    with pytest.raises(Exception):
        update_role("nope", "label", None)


async def test_delete_custom_role(seeded_users):
    create_role("temporal", "Temporal")
    delete_role("temporal")
    assert get_role("temporal") is None


async def test_delete_system_role_raises(seeded_users):
    from app.services.auth_service import SystemRoleError

    with pytest.raises(SystemRoleError):
        delete_role("admin")


async def test_delete_role_in_use_raises(seeded_users):
    from app.services.auth_service import RoleInUseError

    with pytest.raises(RoleInUseError):
        delete_role("viewer")


# ── Permission matrix ─────────────────────────────────────────────────────


async def test_replace_matrix_writes_codenames(seeded_users):
    create_role("supervisor", "Supervisor")
    codenames = replace_role_permissions(
        "supervisor",
        {"vehiculos": "escritura", "motores": "lectura"},
    )
    assert "vehicles.list" in codenames
    assert "vehicles.edit" in codenames
    assert "vehicles.refresh" in codenames
    assert "motors.list" in codenames
    # motores=lectura NO debe crear permisos de escritura
    assert "motors.create" not in codenames


async def test_replace_matrix_clears_old_codenames(seeded_users):
    create_role("ops", "Ops")
    replace_role_permissions("ops", {"vehiculos": "escritura"})
    replace_role_permissions("ops", {"dashboard": "lectura"})

    perms = get_role_permissions("ops")
    assert "vehicles.list" not in perms
    assert "dashboard.view" in perms


async def test_replace_matrix_ninguno_removes_all(seeded_users):
    create_role("ops", "Ops")
    replace_role_permissions("ops", {"dashboard": "lectura"})
    codenames = replace_role_permissions("ops", {"dashboard": "ninguno"})
    assert codenames == set()
    assert get_role_permissions("ops") == set()


async def test_admin_cannot_lose_protected_codenames(seeded_users):
    with pytest.raises(ValueError) as exc_info:
        replace_role_permissions(
            "admin",
            {"vehiculos": "escritura", "usuarios": "ninguno"},
        )
    assert "admin" in str(exc_info.value).lower() or "criticos" in str(exc_info.value).lower()


async def test_admin_can_update_non_protected_matrix(seeded_users):
    codenames = replace_role_permissions(
        "admin",
        {
            "dashboard": "lectura",
            "vehiculos": "escritura",
            "usuarios": "escritura",
            "roles": "escritura",
            "auditoria": "lectura",
        },
    )
    # Admin debe mantener roles.manage, users.*
    assert ADMIN_PROTECTED_CODENAMES.issubset(codenames)
    assert "audit.view" in codenames


async def test_replace_matrix_invalidates_cache(seeded_users, redis_client):
    create_role("ops", "Ops")
    replace_role_permissions("ops", {"dashboard": "lectura"})

    # Calienta el cache
    perms_before = get_user_permissions("ops")
    assert "dashboard.view" in perms_before
    assert redis_client.exists("perm:ops") == 1

    # Reemplaza con "ninguno"
    replace_role_permissions("ops", {"dashboard": "ninguno"})

    # El cache debe estar invalidado: el siguiente get_user_permissions refleja el cambio
    perms_after = get_user_permissions("ops")
    assert "dashboard.view" not in perms_after


async def test_replace_matrix_unknown_module_ignored(seeded_users):
    """El backend solo traduce modulos conocidos; el resto se ignora silenciosamente
    en `permissions_for_matrix`. La validacion estricta la hace el endpoint HTTP."""
    create_role("ops", "Ops")
    codenames = replace_role_permissions(
        "ops",
        {"dashboard": "lectura", "modulo_inexistente": "escritura"},
    )
    assert "dashboard.view" in codenames


# ── API endpoints ────────────────────────────────────────────────────────


async def test_get_modules_endpoint_returns_catalog(client, viewer_user, auth_helpers):
    await auth_helpers["login"](client, "viewer", "ViewerPass1!")
    response = await client.get("/api/v1/roles/modules")
    assert response.status_code == 200
    data = response.json()
    modules = data["modules"]
    keys = {m["key"] for m in modules}
    assert {"dashboard", "vehiculos", "motores", "usuarios", "auditoria", "roles"}.issubset(keys)


async def test_list_roles_endpoint_requires_users_list(client, viewer_user, auth_helpers):
    await auth_helpers["login"](client, "viewer", "ViewerPass1!")
    response = await client.get("/api/v1/roles")
    assert response.status_code == 200


async def test_create_role_via_api(client, admin_user, auth_helpers):
    await auth_helpers["login"](client, "admin", "AdminPass1!")
    response = await client.post(
        "/api/v1/roles",
        json={"key": "supervisor", "label": "Supervisor", "description": "Test"},
    )
    assert response.status_code == 201
    assert response.json()["key"] == "supervisor"


async def test_create_role_requires_roles_manage(client, editor_user, auth_helpers):
    await auth_helpers["login"](client, "editor", "EditorPass1!")
    response = await client.post(
        "/api/v1/roles",
        json={"key": "supervisor", "label": "Supervisor"},
    )
    assert response.status_code == 403


async def test_put_role_permissions_translates_matrix(client, admin_user, auth_helpers):
    await auth_helpers["login"](client, "admin", "AdminPass1!")
    response = await client.put(
        "/api/v1/roles/viewer/permissions",
        json={"modules": {"vehiculos": "escritura", "dashboard": "lectura"}},
    )
    assert response.status_code == 200
    body = response.json()
    assert "vehicles.list" in body["codenames"]
    assert "vehicles.edit" in body["codenames"]
    assert "vehicles.refresh" in body["codenames"]
    assert "dashboard.view" in body["codenames"]
    assert body["modules"]["vehiculos"] == "escritura"
    assert body["modules"]["dashboard"] == "lectura"


async def test_put_role_permissions_rejects_invalid_module(client, admin_user, auth_helpers):
    await auth_helpers["login"](client, "admin", "AdminPass1!")
    response = await client.put(
        "/api/v1/roles/viewer/permissions",
        json={"modules": {"modulo_fake": "escritura"}},
    )
    assert response.status_code == 422


async def test_put_role_permissions_rejects_invalid_level(client, admin_user, auth_helpers):
    await auth_helpers["login"](client, "admin", "AdminPass1!")
    response = await client.put(
        "/api/v1/roles/viewer/permissions",
        json={"modules": {"vehiculos": "super-admin"}},
    )
    assert response.status_code == 422


async def test_delete_role_in_use_returns_409(client, admin_user, auth_helpers):
    await auth_helpers["login"](client, "admin", "AdminPass1!")
    response = await client.delete("/api/v1/roles/viewer")
    assert response.status_code == 409


async def test_delete_system_role_returns_409(client, admin_user, auth_helpers):
    await auth_helpers["login"](client, "admin", "AdminPass1!")
    response = await client.delete("/api/v1/roles/admin")
    assert response.status_code == 409


async def test_delete_custom_role_via_api(client, admin_user, auth_helpers):
    await auth_helpers["login"](client, "admin", "AdminPass1!")
    # Crea uno
    create_response = await client.post(
        "/api/v1/roles",
        json={"key": "ops", "label": "Ops"},
    )
    assert create_response.status_code == 201
    # Borralo
    delete_response = await client.delete("/api/v1/roles/ops")
    assert delete_response.status_code == 200


# ── Regresion: roles dinamicos en users ──────────────────────────────────


async def test_create_user_with_new_custom_role(client, admin_user, auth_helpers):
    await auth_helpers["login"](client, "admin", "AdminPass1!")
    create_role_resp = await client.post(
        "/api/v1/roles",
        json={"key": "ops", "label": "Ops"},
    )
    assert create_role_resp.status_code == 201

    response = await client.post(
        "/api/v1/users",
        json={
            "username": "opsuser",
            "email": "ops@example.com",
            "password": "OpsPass1!",
            "role": "ops",
        },
    )
    assert response.status_code == 201
    assert response.json()["role"] == "ops"


async def test_create_user_with_unknown_role_returns_422(client, admin_user, auth_helpers):
    await auth_helpers["login"](client, "admin", "AdminPass1!")
    response = await client.post(
        "/api/v1/users",
        json={
            "username": "opsuser2",
            "email": "ops2@example.com",
            "password": "OpsPass1!",
            "role": "rol_inexistente",
        },
    )
    assert response.status_code == 422


async def test_user_record_includes_last_login_at(client, admin_user, auth_helpers):
    await auth_helpers["login"](client, "admin", "AdminPass1!")
    response = await client.get("/api/v1/users")
    assert response.status_code == 200
    data = response.json()
    admin = next(u for u in data if u["username"] == "admin")
    # Acaba de hacer login, debe haber last_login_at
    assert admin.get("last_login_at") is not None


# ── Sesiones: max_concurrent_sessions ─────────────────────────────────────


async def test_max_concurrent_sessions_revokes_oldest(seeded_users):
    """Con limite=2, el 3er login revoca la sesion mas antigua."""
    from app.services.auth_service import (
        create_refresh_token,
        enforce_max_concurrent_sessions,
        list_active_sessions,
        list_users,
    )

    # Necesita un usuario con rol valido y DB disponible
    user = list_users()[0]
    create_refresh_token(user["id"], "127.0.0.1", "ua-1")
    create_refresh_token(user["id"], "127.0.0.1", "ua-2")
    assert len(list_active_sessions(user["id"])) == 2

    # Al forzar el limite a 2, al pedir "otro login" revocamos el mas antiguo
    revoked = enforce_max_concurrent_sessions(user["id"], 2)
    assert revoked == 0  # todavia hay 2, no sobra

    # Un tercer login (el de un nuevo cliente) tiene que dejar libre un slot
    # Simulamos que llega un nuevo login: enforce con 2 deberia revocar 1
    create_refresh_token(user["id"], "127.0.0.1", "ua-3")
    # Ahora hay 3, pero al hacer enforce debe matar los excedentes
    revoked = enforce_max_concurrent_sessions(user["id"], 2)
    assert revoked == 1

    active = list_active_sessions(user["id"])
    assert len(active) == 2


async def test_max_concurrent_sessions_zero_means_unlimited(seeded_users):
    from app.services.auth_service import (
        create_refresh_token,
        enforce_max_concurrent_sessions,
        list_active_sessions,
        list_users,
    )

    user = list_users()[0]
    for i in range(5):
        create_refresh_token(user["id"], "127.0.0.1", f"ua-{i}")
    assert len(list_active_sessions(user["id"])) == 5

    revoked = enforce_max_concurrent_sessions(user["id"], 0)
    assert revoked == 0
    assert len(list_active_sessions(user["id"])) == 5


async def test_login_captures_user_agent(client, viewer_user, auth_helpers, redis_client):
    response = await client.post(
        "/api/v1/auth/login",
        json={"username": "viewer", "password": "ViewerPass1!"},
        headers={"User-Agent": "MiApp/1.2.3 (test)"},
    )
    assert response.status_code == 200

    sessions = await client.get("/api/v1/auth/sessions")
    assert sessions.status_code == 200
    data = sessions.json()
    assert any(s["user_agent"] and "MiApp/1.2.3" in s["user_agent"] for s in data)


async def test_refresh_token_record_has_user_agent_column(seeded_users, redis_client):
    from app.services.auth_service import (
        create_refresh_token,
        list_active_sessions,
        list_users,
    )

    u = list_users()[0]
    create_refresh_token(u["id"], "127.0.0.1", "TestAgent/9.9")
    sessions = list_active_sessions(u["id"])
    assert len(sessions) == 1
    assert sessions[0].get("user_agent") == "TestAgent/9.9"
