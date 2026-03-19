from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class LoginRequest(BaseModel):
    username: str = Field(..., min_length=1)
    password: str = Field(..., min_length=1)


class UserRecord(BaseModel):
    id: int
    username: str
    email: str
    role: str = Field(..., description="admin | editor | viewer")
    is_active: bool
    created_at: datetime
    updated_at: datetime


class UserCreateRequest(BaseModel):
    username: str = Field(..., min_length=3, description="Nombre de usuario")
    email: str = Field(..., min_length=5, description="Correo electronico")
    password: str = Field(..., min_length=8, description="Contrasena (min 8 caracteres)")
    role: str = Field(default="viewer", description="admin | editor | viewer")


class UserUpdateRequest(BaseModel):
    email: str | None = Field(default=None, description="Nuevo correo electronico")
    role: str | None = Field(default=None, description="Nuevo rol: admin | editor | viewer")
    is_active: bool | None = Field(default=None, description="Estado activo/inactivo")


class AuditLogRecord(BaseModel):
    id: int
    user_id: int | None = None
    username: str | None = None
    action: str = Field(..., description="CREATE | UPDATE | DELETE")
    resource_type: str = Field(..., description="motor | customer | vehicle | etc.")
    resource_id: str | None = None
    detail: dict[str, Any] | None = None
    ip_address: str | None = None
    created_at: datetime
