from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from app.schemas.base import ApiSchema
from app.schemas.identity import IdentityMaterialRead


class WorkspaceProfessorRead(ApiSchema):
    id: int
    name: str
    email: str | None
    title: str | None
    university: str | None
    school: str | None
    department: str | None
    research_direction: str | None
    recent_papers: list[str]
    profile_url: str | None


class WorkspaceIdentityRead(ApiSchema):
    id: int
    name: str
    profile_name: str
    sender_name: str
    email_address: str


class WorkspaceLLMRead(ApiSchema):
    id: int
    name: str
    provider: str
    model_name: str


class WorkspaceDraftRead(ApiSchema):
    subject: str | None
    body_text: str
    body_html: str | None
    source: str
    sendable: bool
    editable: bool


class WorkspaceTaskSummaryRead(ApiSchema):
    id: int | None
    source: str | None
    batch_task_id: int | None
    parent_task_id: int | None
    status: str | None
    cancellation_reason: str | None
    can_continue_manually: bool
    can_write_follow_up: bool
    outreach_template_id: int | None
    outreach_generation_mode: str
    outreach_template_subject: str | None
    outreach_template_body_text: str | None
    outreach_template_body_html: str | None
    rendered_template_subject: str | None
    rendered_template_body_text: str | None
    rendered_template_body_html: str | None
    match_score: int | None
    match_reason: str | None
    fit_points: list[str]
    risk_points: list[str]
    match_keywords: list[str]
    generated_subject: str | None
    generated_content_text: str | None
    generated_content_html: str | None
    draft_generation_source: str | None
    draft_fallback_reason: str | None
    approved_subject: str | None
    approved_body_text: str | None
    approved_body_html: str | None
    primary_material_id: int | None
    primary_material: IdentityMaterialRead | None
    selected_material_ids: list[int] | None
    approved_at: datetime | None
    scheduled_at: datetime | None
    last_send_attempt_at: datetime | None
    sent_at: datetime | None
    last_rfc_message_id: str | None
    retry_count: int
    last_error: str | None
    is_replied: bool
    estimated_prompt_tokens: int | None = None
    estimated_completion_tokens_upper_bound: int | None = None
    estimated_total_tokens_upper_bound: int | None = None
    last_draft_prompt_tokens: int | None = None
    last_draft_completion_tokens: int | None = None
    last_draft_total_tokens: int | None = None
    draft: WorkspaceDraftRead


class WorkspaceMessageRead(ApiSchema):
    id: int
    direction: str
    subject: str | None
    content: str
    content_html: str | None
    rfc_message_id: str | None
    failure_summary: str | None
    reply_headers: dict[str, object] | None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None
    created_at: datetime
    source_identities: list[WorkspaceIdentityRead] = Field(default_factory=list)


class WorkspaceSyncWarningRead(ApiSchema):
    identity_id: int
    identity_name: str
    message: str


class WorkspaceThreadRead(ApiSchema):
    professor: WorkspaceProfessorRead
    identity: WorkspaceIdentityRead
    llm_profile: WorkspaceLLMRead
    material_options: list[IdentityMaterialRead]
    current_task: WorkspaceTaskSummaryRead
    match_source_identity: WorkspaceIdentityRead
    match_source_material_id: int | None = None
    match_source_material_name: str | None = None
    match_result_id: int | None = None
    match_analyzed_at: datetime | None = None
    match_uses_group_source: bool = False
    match_is_stale: bool = False
    messages: list[WorkspaceMessageRead]
    communication_scope: list[WorkspaceIdentityRead] = Field(default_factory=list)
    sync_warnings: list[WorkspaceSyncWarningRead] = Field(default_factory=list)
