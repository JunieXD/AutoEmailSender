from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel

from app.schemas.base import ApiSchema
from app.modules.identities.materials.public import IdentityMaterialRead


class TestComposeIdentityRead(ApiSchema):
    id: int
    name: str
    profile_name: str
    sender_name: str
    email_address: str


class TestComposeLLMRead(ApiSchema):
    id: int
    name: str
    provider: str
    model_name: str


class TestComposeDraftRead(ApiSchema):
    outreach_template_id: int | None
    subject: str | None
    body_text: str
    body_html: str | None
    selected_material_ids: list[int]


class TestComposeMessageRead(ApiSchema):
    id: int
    recipient_email: str
    subject: str | None
    content: str
    content_html: str | None
    status: str
    rfc_message_id: str | None
    failure_summary: str | None
    created_at: datetime


class TestComposeThreadRead(ApiSchema):
    identity: TestComposeIdentityRead
    llm_profile: TestComposeLLMRead
    material_options: list[IdentityMaterialRead]
    draft: TestComposeDraftRead
    history: list[TestComposeMessageRead]


class TestComposeStatusRead(ApiSchema):
    completed: bool


class TestComposeDraftUpdateRequest(BaseModel):
    outreach_template_id: int | None = None
    subject: str | None = None
    body_text: str
    body_html: str | None = None
    selected_material_ids: list[int] | None = None


class TestComposeMessageSendRequest(TestComposeDraftUpdateRequest):
    pass


class TestComposeGenerateRequest(BaseModel):
    outreach_template_id: int | None = None
    subject: str | None = None
    body_text: str | None = None
    body_html: str | None = None
