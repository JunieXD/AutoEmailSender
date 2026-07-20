from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel

from app.schemas.base import ApiSchema

class IdentityMaterialTypeRead(StrEnum):
    RESUME = "resume"
    TRANSCRIPT = "transcript"
    PUBLICATION = "publication"
    PORTFOLIO = "portfolio"
    OTHER = "other"


class IdentityMaterialRead(ApiSchema):

    id: int
    display_name: str
    original_filename: str
    mime_type: str | None
    size_bytes: int
    material_type: str
    is_primary: bool = False
    created_at: datetime


class OutreachGenerationMode(StrEnum):
    LLM = "llm"
    TEMPLATE = "template"


class IdentityProfileBase(BaseModel):
    name: str | None = None
    profile_name: str | None = None
    sender_name: str | None = None
    email_address: str
    smtp_host: str
    smtp_port: int = 465
    smtp_username: str
    smtp_password: str
    imap_host: str | None = None
    imap_port: int | None = None
    imap_username: str | None = None
    imap_password: str | None = None
    default_language: str = "zh-CN"
    outreach_generation_mode: str = OutreachGenerationMode.LLM.value
    outreach_template_subject: str | None = None
    outreach_template_body_text: str | None = None
    outreach_template_body_html: str | None = None
    match_threshold: int | None = None
    same_domain_cooldown_minutes: int | None = None
    is_default: bool = False


class IdentityProfileCreate(IdentityProfileBase):
    pass


class IdentityProfileUpdate(IdentityProfileBase):
    pass


class IdentityProfileRead(IdentityProfileBase):

    id: int
    name: str
    profile_name: str
    sender_name: str
    current_primary_material_id: int | None
    current_primary_material: IdentityMaterialRead | None
    materials: list[IdentityMaterialRead]
    daily_send_limit: int | None
    send_interval_min: int | None
    send_interval_max: int | None
    created_at: datetime
    updated_at: datetime


class ConnectionTestResult(ApiSchema):
    ok: bool
    message: str
    host: str | None = None
    possible_cause: str | None = None


class IdentityTemplateImportResult(ApiSchema):
    subject: str | None
    body_text: str
    body_html: str
    format_name: str
