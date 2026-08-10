from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, field_validator

from app.schemas.base import ApiSchema
from app.modules.identities.public import IdentityMaterialRead


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

    @field_validator("selected_material_ids")
    @classmethod
    def validate_selected_material_ids(
        cls,
        value: list[int] | None,
    ) -> list[int] | None:
        if value is None:
            return None
        if any(material_id < 1 for material_id in value):
            raise ValueError("selected_material_ids 必须是正整数")
        if len(value) != len(set(value)):
            raise ValueError("selected_material_ids 不能包含重复的材料 ID")
        return value


class TestComposeMessageSendRequest(TestComposeDraftUpdateRequest):
    pass


class TestComposeGenerateRequest(BaseModel):
    outreach_template_id: int | None = None
    subject: str | None = None
    body_text: str | None = None
    body_html: str | None = None
