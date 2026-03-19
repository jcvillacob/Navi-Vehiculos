#!/usr/bin/env python3
"""
Script de bootstrap para crear el primer usuario administrador.

Uso:
    docker compose exec backend python scripts/create_admin.py

Variables de entorno requeridas (o prompts interactivos si no están definidas):
    ADMIN_USERNAME  - Nombre de usuario
    ADMIN_EMAIL     - Correo electronico
    ADMIN_PASSWORD  - Contrasena (min 8 caracteres)
"""
from __future__ import annotations

import os
import sys

# Asegurar que el directorio raiz del proyecto esté en el path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.services.auth_service import create_user, ensure_auth_tables, get_user_by_username


def _prompt(name: str, env_var: str, secret: bool = False) -> str:
    value = os.getenv(env_var, "").strip()
    if value:
        masked = "*" * len(value) if secret else value
        print(f"  {name}: {masked} (desde env {env_var})")
        return value
    if secret:
        import getpass
        return getpass.getpass(f"  {name}: ")
    return input(f"  {name}: ").strip()


def main() -> None:
    print("\n=== Navi Vehiculos — Crear usuario Admin ===\n")

    username = _prompt("Usuario", "ADMIN_USERNAME")
    email = _prompt("Email", "ADMIN_EMAIL")
    password = _prompt("Contrasena", "ADMIN_PASSWORD", secret=True)

    if not username:
        print("Error: el nombre de usuario no puede estar vacio.")
        sys.exit(1)
    if not email or "@" not in email:
        print("Error: ingresa un email valido.")
        sys.exit(1)
    if len(password) < 8:
        print("Error: la contrasena debe tener al menos 8 caracteres.")
        sys.exit(1)

    print("\nCreando tablas de auth si no existen...")
    ensure_auth_tables()

    existing = get_user_by_username(username)
    if existing:
        print(f"El usuario '{username}' ya existe (id={existing['id']}, rol={existing['role']}).")
        sys.exit(0)

    user = create_user(username=username, email=email, password=password, role="admin")
    print(f"\nUsuario creado exitosamente:")
    print(f"  ID       : {user['id']}")
    print(f"  Usuario  : {user['username']}")
    print(f"  Email    : {user['email']}")
    print(f"  Rol      : {user['role']}")
    print(f"  Activo   : {user['is_active']}")
    print()


if __name__ == "__main__":
    main()
