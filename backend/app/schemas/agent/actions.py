from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field, model_validator

from app.schemas.base import ApiSchema


AgentDraftGenerationMode = Literal["template", "ai_rewrite", "manual"]
AgentPlanDelivery = Literal["immediate", "scheduled"]
AgentPlanStatus = Literal[
    "awaiting_confirmation",
    "executing",
    "executed",
    "canceled",
    "expired",
]


class AgentDraftGenerateRequest(ApiSchema):
    professor_id: int = Field(ge=1)
    identity_id: int = Field(ge=1)
    llm_profile_id: int = Field(ge=1)
    generation_mode: AgentDraftGenerationMode
    template_id: int | None = Field(default=None, ge=1)
    reference_material_id: int | None = Field(default=None, ge=1)
    attachment_material_ids: list[int] = Field(default_factory=list)
    subject: str | None = None
    body_text: str | None = None
    body_html: str | None = None

    @model_validator(mode="after")
    def validate_mode_requirements(self) -> "AgentDraftGenerateRequest":
        if self.generation_mode == "ai_rewrite" and self.reference_material_id is None:
            raise ValueError("AI 改写必须明确指定 reference_material_id")
        if self.generation_mode == "manual" and not (
            (self.body_text or "").strip() or (self.body_html or "").strip()
        ):
            raise ValueError("manual 模式必须提供正文")
        return self


class AgentDraftSaveRequest(ApiSchema):
    subject: str | None = None
    body_text: str = ""
    body_html: str | None = None
    attachment_material_ids: list[int] = Field(default_factory=list)


class AgentDraftRegenerateRequest(ApiSchema):
    llm_profile_id: int | None = Field(default=None, ge=1)


class AgentPrepareSendRequest(ApiSchema):
    delivery: AgentPlanDelivery = "immediate"
    scheduled_at: datetime | None = None

    @model_validator(mode="after")
    def validate_schedule(self) -> "AgentPrepareSendRequest":
        if self.delivery == "scheduled" and self.scheduled_at is None:
            raise ValueError("scheduled 计划必须提供 scheduled_at")
        if self.delivery == "immediate" and self.scheduled_at is not None:
            raise ValueError("immediate 计划不能提供 scheduled_at")
        return self


class AgentPlanExecuteRequest(ApiSchema):
    confirm: bool = False


class AgentPlanNamedObjectRead(ApiSchema):
    id: int
    name: str


class AgentPlanRecipientRead(AgentPlanNamedObjectRead):
    email: str


class AgentPlanIdentityRead(AgentPlanNamedObjectRead):
    email_address: str


class AgentPlanAttachmentRead(AgentPlanNamedObjectRead):
    size_bytes: int = 0


class AgentPlanSummaryRead(ApiSchema):
    recipient_count: int = 1
    recipient: AgentPlanRecipientRead
    identity: AgentPlanIdentityRead
    generation_mode: AgentDraftGenerationMode
    template: AgentPlanNamedObjectRead | None = None
    reference_material: AgentPlanNamedObjectRead | None = None
    attachments: list[AgentPlanAttachmentRead] = Field(default_factory=list)
    attachment_total_size_bytes: int = 0
    delivery: AgentPlanDelivery
    scheduled_at: datetime | None = None
    subject: str
    body_text: str
    body_html: str | None = None


class AgentActionPlanRead(ApiSchema):
    plan_id: str
    action: Literal["email.send", "email.schedule"]
    status: AgentPlanStatus
    task_id: int
    content_fingerprint: str
    expires_at: datetime
    confirmed_at: datetime | None = None
    executed_at: datetime | None = None
    canceled_at: datetime | None = None
    summary: AgentPlanSummaryRead
    warnings: list[str] = Field(default_factory=list)
    result: dict[str, object] | None = None
    idempotent_replay: bool = False
    confirmation_message: str | None = None
