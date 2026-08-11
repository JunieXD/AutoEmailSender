from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import Field

from app.schemas.base import ApiSchema


class IdentityMaterialTypeRead(StrEnum):
    RESUME = "resume"
    TRANSCRIPT = "transcript"
    PUBLICATION = "publication"
    PORTFOLIO = "portfolio"
    OTHER = "other"


class IdentityMaterialRead(ApiSchema):
    id: int
    source_identity_id: int | None = None
    display_name: str
    original_filename: str
    mime_type: str | None
    size_bytes: int
    material_type: str
    is_primary: bool = False
    default_for_identity_ids: list[int] = Field(default_factory=list)
    created_at: datetime
