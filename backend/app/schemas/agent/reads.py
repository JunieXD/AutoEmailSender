from __future__ import annotations

from datetime import datetime
from typing import Generic, Literal, TypeVar

from pydantic import Field

from app.schemas.base import ApiSchema
from app.schemas.crawl_job import CrawlCandidateRead, CrawlJobEventRead, CrawlPageRead


AgentItem = TypeVar("AgentItem")


class AgentPage(ApiSchema, Generic[AgentItem]):
    items: list[AgentItem]
    next_cursor: str | None = None
    has_more: bool = False


class AgentCrawlPageRead(CrawlPageRead):
    trust_level: Literal["untrusted_external_content"] = "untrusted_external_content"


class AgentCrawlCandidateRead(CrawlCandidateRead):
    revision: str | None = None
    trust_level: Literal["untrusted_external_content"] = "untrusted_external_content"


class AgentCrawlJobEventRead(CrawlJobEventRead):
    trust_level: Literal["untrusted_external_content"] = "untrusted_external_content"


class AgentProfessorTagRead(ApiSchema):
    id: int
    name: str
    text_color: str
    background_color: str


class AgentProfessorTagUsageProfessorRead(ApiSchema):
    id: int
    name: str
    email: str | None = None
    university: str | None = None
    school: str | None = None


class AgentProfessorTagUsageRead(ApiSchema):
    tag: AgentProfessorTagRead
    professors: list[AgentProfessorTagUsageProfessorRead] = Field(default_factory=list)


class AgentProfessorRead(ApiSchema):
    id: int
    revision: str | None = None
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


class AgentCommunicationSyncRead(ApiSchema):
    identity_id: int
    detected_count: int
    completed_at: datetime
    message: str


class AgentMatchAnalysisJobRead(ApiSchema):
    id: int
    name: str
    status: str
    target_count: int
    succeeded_count: int
    failed_count: int
    skipped_count: int
    total_prompt_tokens: int
    total_completion_tokens: int
    total_cached_tokens: int
    total_tokens: int
    identity_id: int
    llm_profile_id: int
    cancel_requested_at: datetime | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    created_at: datetime
    updated_at: datetime
    deleted_at: datetime | None = None
    last_error: str | None = None


class AgentMatchAnalysisJobItemRead(ApiSchema):
    id: int
    job_id: int
    professor_id: int
    professor_name: str
    professor_email: str | None = None
    professor_title: str | None = None
    professor_university: str | None = None
    professor_school: str | None = None
    email_task_id: int | None = None
    status: str
    match_score: int | None = None
    match_analysis_run_id: int | None = None
    error_message: str | None = None
    skip_reason: str | None = None
    prompt_tokens: int
    completion_tokens: int
    cached_tokens: int
    total_tokens: int
    started_at: datetime | None = None
    finished_at: datetime | None = None
    updated_at: datetime


class AgentMatchAnalysisJobActionRead(ApiSchema):
    ok: bool
    job: AgentMatchAnalysisJobRead


class AgentCommunicationGroupDeleteRead(ApiSchema):
    ok: bool
    group_id: int


class AgentIdentityRead(ApiSchema):
    id: int
    revision: str | None = None
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
    revision: str | None = None
    name: str
    provider: str
    model_name: str
    temperature: float | None = None
    max_tokens: int | None = None
    credential_configured: bool
    is_default: bool
    created_at: datetime
    updated_at: datetime


class AgentLLMProfileModelsRead(ApiSchema):
    profile_id: int
    ok: bool
    message: str
    resolved_base_url: str | None = None
    request_url: str | None = None
    attempted_urls: list[str] = Field(default_factory=list)
    endpoint_kind: str | None = None
    status_code: int | None = None
    duration_ms: int | None = None
    consumes_tokens: bool = False
    models: list[str] = Field(default_factory=list)
    selected_model_available: bool | None = None
    trust_level: Literal["untrusted_external_content"] = "untrusted_external_content"


class AgentLLMProfileTestRead(ApiSchema):
    profile_id: int
    ok: bool
    message: str
    resolved_base_url: str | None = None
    request_url: str | None = None
    attempted_urls: list[str] = Field(default_factory=list)
    endpoint_kind: str | None = None
    status_code: int | None = None
    duration_ms: int | None = None
    consumes_tokens: bool = True
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None
    trust_level: Literal["untrusted_external_content"] = "untrusted_external_content"


class AgentMaterialRead(ApiSchema):
    id: int
    revision: str | None = None
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
    revision: str | None = None
    name: str
    recommended_generation_mode: str
    subject: str | None = None
    body_text: str | None = None
    body_html: str | None = None
    is_default: bool
    archived_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class AgentTemplateImportRead(ApiSchema):
    subject: str | None
    body_text: str
    body_html: str
    format_name: str
    trust_level: Literal["untrusted_external_content"] = "untrusted_external_content"


class AgentDraftRead(ApiSchema):
    task_id: int
    revision: str | None = None
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


class AgentCampaignNamedObjectRead(ApiSchema):
    id: int
    name: str


class AgentCampaignRead(ApiSchema):
    id: int
    name: str
    status: str
    identity: AgentCampaignNamedObjectRead
    llm_profile: AgentCampaignNamedObjectRead
    generation_mode: Literal["template", "ai_rewrite"]
    template: AgentCampaignNamedObjectRead | None = None
    reference_material: AgentCampaignNamedObjectRead | None = None
    attachment_material_ids: list[int] = Field(default_factory=list)
    schedule_type: Literal["immediate", "scheduled"]
    window_start_time: str | None = None
    window_end_time: str | None = None
    emails_per_window: int | None = None
    scheduled_dates: list[str] = Field(default_factory=list)
    target_count: int
    pending_generation_count: int
    generating_draft_count: int
    draft_failed_count: int
    review_required_count: int
    approved_count: int
    scheduled_count: int
    sending_count: int
    sent_count: int
    failed_count: int
    canceled_count: int
    canceled_send_count: int
    can_start_draft_generation: bool
    created_at: datetime
    updated_at: datetime


class AgentCampaignItemRead(ApiSchema):
    id: int
    campaign_id: int
    professor_id: int
    professor_name: str
    professor_email: str | None = None
    status: str
    generation_mode: Literal["template", "ai_rewrite"]
    subject: str | None = None
    has_final_content: bool
    attachment_material_ids: list[int] = Field(default_factory=list)
    scheduled_at: datetime | None = None
    send_canceled_at: datetime | None = None
    sent_at: datetime | None = None
    last_error: str | None = None
    can_remove: bool = False
    can_cancel_send: bool = False
    can_restore_send: bool = False
    can_retry_draft: bool = False
    updated_at: datetime


class AgentWorkspaceProfessorRead(ApiSchema):
    id: int
    name: str
    email: str | None = None
    title: str | None = None
    university: str | None = None
    school: str | None = None
    research_direction: str | None = None
    recent_papers: list[str] = Field(default_factory=list)
    profile_url: str | None = None


class AgentWorkspaceIdentityRead(ApiSchema):
    id: int
    name: str
    profile_name: str
    sender_name: str
    email_address: str


class AgentWorkspaceLLMRead(ApiSchema):
    id: int
    name: str
    provider: str
    model_name: str


class AgentWorkspaceMaterialRead(ApiSchema):
    id: int
    display_name: str
    original_filename: str
    mime_type: str | None = None
    size_bytes: int
    material_type: str
    is_primary: bool
    created_at: datetime


class AgentWorkspaceDraftRead(ApiSchema):
    subject: str | None = None
    body_text: str
    body_html: str | None = None
    source: str
    sendable: bool
    editable: bool
    trust_level: Literal["untrusted_external_content"] = "untrusted_external_content"


class AgentWorkspaceTaskRead(ApiSchema):
    id: int | None = None
    source: str | None = None
    batch_task_id: int | None = None
    parent_task_id: int | None = None
    status: str | None = None
    cancellation_reason: str | None = None
    can_continue_manually: bool
    can_write_follow_up: bool
    outreach_template_id: int | None = None
    outreach_generation_mode: str
    outreach_template_subject: str | None = None
    outreach_template_body_text: str | None = None
    outreach_template_body_html: str | None = None
    rendered_template_subject: str | None = None
    rendered_template_body_text: str | None = None
    rendered_template_body_html: str | None = None
    match_score: int | None = None
    match_reason: str | None = None
    fit_points: list[str] = Field(default_factory=list)
    risk_points: list[str] = Field(default_factory=list)
    match_keywords: list[str] = Field(default_factory=list)
    generated_subject: str | None = None
    generated_content_text: str | None = None
    generated_content_html: str | None = None
    approved_subject: str | None = None
    approved_body_text: str | None = None
    approved_body_html: str | None = None
    primary_material_id: int | None = None
    primary_material: AgentWorkspaceMaterialRead | None = None
    selected_material_ids: list[int] | None = None
    approved_at: datetime | None = None
    scheduled_at: datetime | None = None
    last_send_attempt_at: datetime | None = None
    sent_at: datetime | None = None
    last_rfc_message_id: str | None = None
    retry_count: int
    last_error: str | None = None
    is_replied: bool
    estimated_prompt_tokens: int | None = None
    estimated_completion_tokens_upper_bound: int | None = None
    estimated_total_tokens_upper_bound: int | None = None
    last_draft_prompt_tokens: int | None = None
    last_draft_completion_tokens: int | None = None
    last_draft_total_tokens: int | None = None
    draft: AgentWorkspaceDraftRead
    trust_level: Literal["untrusted_external_content"] = "untrusted_external_content"


class AgentWorkspaceMessageRead(ApiSchema):
    id: int
    direction: Literal["sent", "received", "draft"]
    subject: str | None = None
    content: str
    content_html: str | None = None
    rfc_message_id: str | None = None
    failure_summary: str | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None
    created_at: datetime
    source_identities: list[AgentWorkspaceIdentityRead] = Field(default_factory=list)
    trust_level: Literal["untrusted_external_content"] = "untrusted_external_content"


class AgentWorkspaceSyncWarningRead(ApiSchema):
    identity_id: int
    identity_name: str
    message: str
    trust_level: Literal["untrusted_external_content"] = "untrusted_external_content"


class AgentWorkspaceThreadRead(ApiSchema):
    professor: AgentWorkspaceProfessorRead
    identity: AgentWorkspaceIdentityRead
    llm_profile: AgentWorkspaceLLMRead
    material_options: list[AgentWorkspaceMaterialRead] = Field(default_factory=list)
    current_task: AgentWorkspaceTaskRead
    messages: list[AgentWorkspaceMessageRead] = Field(default_factory=list)
    communication_scope: list[AgentWorkspaceIdentityRead] = Field(default_factory=list)
    sync_warnings: list[AgentWorkspaceSyncWarningRead] = Field(default_factory=list)


class AgentTaskTokenUsageRead(ApiSchema):
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None
    cached_tokens: int | None = None


class AgentTaskMatchCalculationRead(ApiSchema):
    task_id: int
    thread: AgentWorkspaceThreadRead
    usage: AgentTaskTokenUsageRead
    run_id: int | None = None


class AgentInfoRead(ApiSchema):
    app_name: str = "Auto Email Sender"
    app_version: str
    protocol_version: str = "2"
    api_version: str = "v1"
    authentication_scope: Literal["agent"] = "agent"
    guide_command: str = "auto-email-sender --format json guide"
