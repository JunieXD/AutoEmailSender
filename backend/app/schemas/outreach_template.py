from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel

from app.schemas.base import ApiSchema


class OutreachTemplateCreate(BaseModel):
    name: str
    recommended_generation_mode: str = "llm"
    subject: str | None = None
    body_text: str | None = None
    body_html: str | None = None
    is_default: bool = False


class OutreachTemplateUpdate(BaseModel):
    name: str | None = None
    recommended_generation_mode: str | None = None
    subject: str | None = None
    body_text: str | None = None
    body_html: str | None = None
    is_default: bool | None = None


class OutreachTemplateRead(ApiSchema):
    id: int
    name: str
    recommended_generation_mode: str
    subject: str | None
    body_text: str | None
    body_html: str | None
    is_ready: bool
    is_default: bool
    archived_at: datetime | None
    created_at: datetime
    updated_at: datetime


class IdentityDefaultOutreachTemplateUpdate(BaseModel):
    template_id: int | None
