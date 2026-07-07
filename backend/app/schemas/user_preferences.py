from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class UserPreferenceResponse(BaseModel):
    key: str
    value: Any
    updated_at: datetime


class UserPreferenceUpdate(BaseModel):
    value: Any = Field(..., description="Valor arbitrario serializable en JSON")
