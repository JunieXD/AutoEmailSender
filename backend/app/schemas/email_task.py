from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel

from app.schemas.base import ApiSchema
from app.schemas.workspace import WorkspaceThreadRead


class EmailTaskApprovalRequest(BaseModel):
    subject: str | None = None
    body_text: str
    body_html: str | None = None
    selected_material_ids: list[int] | None = None


class EmailTaskScheduleRequest(EmailTaskApprovalRequest):
    scheduled_at: datetime


class EmailTaskPrimaryMaterialRequest(BaseModel):
    primary_material_id: int


class EmailTaskOutreachConfigRequest(BaseModel):
    outreach_generation_mode: str
    outreach_template_subject: str | None = None
    outreach_template_body_text: str | None = None
    outreach_template_body_html: str | None = None


class TokenUsageRead(ApiSchema):
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None
    cached_tokens: int | None = None


class MatchCalculationResultRead(ApiSchema):
    thread: WorkspaceThreadRead
    usage: TokenUsageRead
    run_id: int | None = None


class DraftPreviewRead(ApiSchema):
    subject: str
    body_text: str | None = None
    body_html: str | None = None
    rich_body: dict[str, object] | None = None
    usage: TokenUsageRead | None = None

