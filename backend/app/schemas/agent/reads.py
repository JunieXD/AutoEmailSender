from __future__ import annotations

from datetime import datetime
from typing import Generic, Literal, TypeVar

from pydantic import Field

from app.schemas.base import ApiSchema


AgentItem = TypeVar("AgentItem")


class AgentPage(ApiSchema, Generic[AgentItem]):
    items: list[AgentItem]
    next_cursor: str | None = None
    has_more: bool = False


class AgentProfessorTagRead(ApiSchema):
    id: int
    name: str
    text_color: str
    background_color: str


class AgentProfessorRead(ApiSchema):
    id: int
    name: str
    email: str | None = None
    title: str | None = None
    university: str | None = None
    school: str | None = None
    department: str | None = None
    research_direction: str | None = None
    recent_papers: list[str] = Field(default_factory=list)
    profile_url: str | None = None
    source_url: str | None = None
    crawl_status: str
    skip_reason: str | None = None
    personal_note: str | None = None
    archived_at: datetime | None = None
    created_at: datetime
    updated_at: datetime
    tags: list[AgentProfessorTagRead] = Field(default_factory=list)


class AgentMessageRead(ApiSchema):
    id: int
    thread_id: str
    email_task_id: int | None = None
    identity_id: int
    professor_id: int
    direction: Literal["sent", "received", "draft"]
    subject: str | None = None
    content: str | None = None
    content_html: str | None = None
    body_included: bool
    from_email: str | None = None
    to_emails: list[str] = Field(default_factory=list)
    cc_emails: list[str] = Field(default_factory=list)
    bcc_emails: list[str] = Field(default_factory=list)
    rfc_message_id: str | None = None
    failure_summary: str | None = None
    created_at: datetime
    trust_level: Literal["untrusted_external_content"] = "untrusted_external_content"


class AgentCommunicationThreadRead(ApiSchema):
    id: str
    identity_id: int
    identity_name: str
    identity_email_address: str
    professor_id: int
    professor_name: str
    professor_email: str | None = None
    sent_count: int
    received_count: int
    has_sent: bool
    has_reply: bool
    last_message_at: datetime


class AgentCommunicationThreadDetailRead(AgentCommunicationThreadRead):
    messages: list[AgentMessageRead] = Field(default_factory=list)
    messages_next_cursor: str | None = None
    messages_has_more: bool = False


class AgentIdentityRead(ApiSchema):
    id: int
    name: str
    profile_name: str
    sender_name: str
    email_address: str
    default_language: str
    outreach_generation_mode: str
    default_outreach_template_id: int | None = None
    current_primary_material_id: int | None = None
    communication_group_id: int | None = None
    match_threshold: int | None = None
    daily_send_limit: int | None = None
    send_interval_min: int | None = None
    send_interval_max: int | None = None
    same_domain_cooldown_minutes: int | None = None
    smtp_configured: bool
    imap_configured: bool
    is_default: bool
    created_at: datetime
    updated_at: datetime


class AgentLLMProfileRead(ApiSchema):
    id: int
    name: str
    provider: str
    model_name: str
    temperature: float | None = None
    max_tokens: int | None = None
    credential_configured: bool
    is_default: bool
    created_at: datetime
    updated_at: datetime


class AgentMaterialRead(ApiSchema):
    id: int
    identity_id: int
    display_name: str
    original_filename: str
    mime_type: str | None = None
    size_bytes: int
    material_type: str
    is_primary: bool
    has_extracted_text: bool
    extracted_text: str | None = None
    created_at: datetime


class AgentTemplateRead(ApiSchema):
    id: int
    name: str
    recommended_generation_mode: str
    subject: str | None = None
    body_text: str | None = None
    body_html: str | None = None
    is_default: bool
    archived_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class AgentDraftRead(ApiSchema):
    task_id: int
    source: str
    batch_task_id: int | None = None
    parent_task_id: int | None = None
    identity_id: int
    professor_id: int
    professor_name: str
    professor_email: str | None = None
    llm_profile_id: int
    status: str
    generation_mode: Literal["template", "ai_rewrite", "manual"]
    template_id: int | None = None
    reference_material_id: int | None = None
    attachment_material_ids: list[int] = Field(default_factory=list)
    generated_subject: str | None = None
    generated_body_text: str | None = None
    generated_body_html: str | None = None
    approved_subject: str | None = None
    approved_body_text: str | None = None
    approved_body_html: str | None = None
    approved_at: datetime | None = None
    scheduled_at: datetime | None = None
    sent_at: datetime | None = None
    last_error: str | None = None
    created_at: datetime
    updated_at: datetime


class AgentInfoRead(ApiSchema):
    app_name: str = "Auto Email Sender"
    app_version: str
    protocol_version: str = "1"
    api_version: str = "v1"
    authentication_scope: Literal["agent"] = "agent"
    guide_command: str = "auto-email-sender guide --format json"
