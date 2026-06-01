from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.api.routes import customer as customer_routes
from app.services.auth_service import (
    create_role,
    get_user_permissions,
    replace_role_permissions,
    clear_role_permissions_cache as clear_role_permissions_cache_local,
)


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


# ── Tests extendidos para el modelo Módulo × nivel ──────────────────────


async def test_custom_role_with_vehicles_read_only_cannot_edit(client, admin_user, auth_helpers, seeded_users, redis_client):
    """Un rol custom con vehiculos=lectura puede listar vehiculos pero no modificarlos."""
    create_role("solo_lectura_vehiculos", "Solo lectura vehiculos")
    replace_role_permissions(
        "solo_lectura_vehiculos",
        {"vehiculos": "lectura"},
    )
    clear_role_permissions_cache_local("solo_lectura_vehiculos")

    # Crear un usuario con ese rol para poder autenticar y pedir permisos
    from app.services.auth_service import create_user
    create_user("flota_viewer", "fv@example.com", "FlotaPass1!", "solo_lectura_vehiculos")
    await auth_helpers["login"](client, "flota_viewer", "FlotaPass1!")

    perms = get_user_permissions("solo_lectura_vehiculos")
    assert "vehicles.list" in perms
    assert "vehicles.edit" not in perms
    assert "vehicles.refresh" not in perms

    # Endpoints protegidos
    list_resp = await client.get("/api/v1/vehicle")
    assert list_resp.status_code == 200

    # Intentar editar debe dar 403
    edit_resp = await client.put(
        "/api/v1/vehicle/ABC123/database",
        json={"customer_database_id": 1, "provider_vehicle_id": "x"},
    )
    assert edit_resp.status_code == 403


async def test_escritura_implica_lectura(client, admin_user, auth_helpers, seeded_users, redis_client):
    """Asignar vehiculos=escritura otorga automaticamente los permisos de lectura."""
    create_role("editor_vehiculos", "Editor vehiculos")
    replace_role_permissions(
        "editor_vehiculos",
        {"vehiculos": "escritura"},
    )
    clear_role_permissions_cache_local("editor_vehiculos")

    perms = get_user_permissions("editor_vehiculos")
    # Escritura incluye lectura
    assert "vehicles.list" in perms
    assert "vehicles.edit" in perms
    assert "vehicles.refresh" in perms


async def test_ninguno_no_otorga_permisos(client, admin_user, auth_helpers, seeded_users, redis_client):
    """Un rol con todos los modulos en 'ninguno' no tiene permisos asignados."""
    create_role("sin_permisos", "Sin permisos")
    replace_role_permissions(
        "sin_permisos",
        {"dashboard": "ninguno", "vehiculos": "ninguno", "motores": "ninguno"},
    )
    clear_role_permissions_cache_local("sin_permisos")

    perms = get_user_permissions("sin_permisos")
    assert perms == set()
