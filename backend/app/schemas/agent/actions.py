from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import ConfigDict, Field, model_validator

from app.schemas.base import ApiSchema
from app.modules.crawler.public import CrawlCandidateReviewStatusDTO


AgentDraftGenerationMode = Literal["template", "ai_rewrite", "manual"]
AgentCampaignGenerationMode = Literal["template", "ai_rewrite"]
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


class AgentDraftRewriteRequest(AgentDraftSaveRequest):
    llm_profile_id: int | None = Field(default=None, ge=1)


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


class AgentPlanEffectsRead(ApiSchema):
    """Effects resolved from the concrete action behind a confirmation plan."""

    resolution: Literal["delegated"] = "delegated"
    action: str
    mutates: bool
    external_services: list[str] = Field(default_factory=list)
    cost_may_apply: bool
    reversible: bool
    impact_scope: str
    confirmation_required_before_invocation: bool = True
    confirmation_rule: str
    unknown_external_result_protection: bool


class AgentProfessorUpsertRequest(ApiSchema):
    name: str
    email: str
    title: str | None = None
    university: str | None = None
    school: str | None = None
    department: str | None = None
    research_direction: str | None = None
    recent_papers: list[str] = Field(default_factory=list)
    profile_url: str | None = None
    source_url: str | None = None
    personal_note: str | None = Field(default=None, max_length=10_000)
    tag_ids: list[int] = Field(default_factory=list)


class AgentProfessorUpdateRequest(ApiSchema):
    name: str | None = None
    email: str | None = None
    title: str | None = None
    university: str | None = None
    school: str | None = None
    department: str | None = None
    research_direction: str | None = None
    recent_papers: list[str] | None = None
    profile_url: str | None = None
    source_url: str | None = None
    personal_note: str | None = Field(default=None, max_length=10_000)


class AgentProfessorTagCreateRequest(ApiSchema):
    name: str
    text_color: str
    background_color: str


class AgentProfessorTagSetRequest(ApiSchema):
    tag_ids: list[int] = Field(default_factory=list)


class AgentProfessorBulkTagsRequest(ApiSchema):
    professor_ids: list[int] = Field(min_length=1)
    mode: Literal["add", "remove", "replace"]
    tag_ids: list[int] = Field(default_factory=list)


class AgentProfessorBulkArchiveRequest(ApiSchema):
    professor_ids: list[int] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_professor_ids(self) -> "AgentProfessorBulkArchiveRequest":
        if any(professor_id < 1 for professor_id in self.professor_ids):
            raise ValueError("professor_ids 必须是正整数")
        if len(set(self.professor_ids)) != len(self.professor_ids):
            raise ValueError("professor_ids 不能包含重复的导师 ID")
        return self


class AgentTaskRuntimeProfileRequest(ApiSchema):
    llm_profile_id: int | None = Field(default=None, ge=1)


class AgentTaskPrimaryMaterialRequest(ApiSchema):
    primary_material_id: int = Field(ge=1)


class AgentTaskOutreachConfigRequest(ApiSchema):
    outreach_generation_mode: str = Field(min_length=1)
    outreach_template_id: int | None = Field(default=None, ge=1)
    outreach_template_subject: str | None = None
    outreach_template_body_text: str | None = None
    outreach_template_body_html: str | None = None


class AgentIdentitySettingsUpdateRequest(ApiSchema):
    """The identity settings that are safe to expose through an Agent CLI."""

    model_config = ConfigDict(from_attributes=True, extra="forbid")

    profile_name: str | None = Field(default=None, min_length=1, max_length=100)
    sender_name: str | None = Field(default=None, min_length=1, max_length=100)
    default_language: str | None = Field(default=None, min_length=1, max_length=32)
    outreach_generation_mode: Literal["llm", "template"] | None = None
    match_threshold: int | None = Field(default=None, ge=0, le=100)
    daily_send_limit: int | None = Field(default=None, ge=0)
    send_interval_min: int | None = Field(default=None, ge=0)
    send_interval_max: int | None = Field(default=None, ge=0)
    same_domain_cooldown_minutes: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def require_at_least_one_field(self) -> "AgentIdentitySettingsUpdateRequest":
        if not self.model_fields_set:
            raise ValueError("请至少提供一个需要修改的身份设置字段")
        for field_name in (
            "profile_name",
            "sender_name",
            "default_language",
            "outreach_generation_mode",
        ):
            if field_name in self.model_fields_set:
                value = getattr(self, field_name)
                if value is None or not str(value).strip():
                    raise ValueError(f"{field_name} 不能清空")
        return self


class AgentLLMProfileSettingsUpdateRequest(ApiSchema):
    """The model profile settings that do not disclose or redirect credentials."""

    model_config = ConfigDict(from_attributes=True, extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=100)
    model_name: str | None = Field(default=None, min_length=1, max_length=255)
    temperature: float | None = Field(default=None, ge=0, le=2)
    max_tokens: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def require_at_least_one_field(self) -> "AgentLLMProfileSettingsUpdateRequest":
        if not self.model_fields_set:
            raise ValueError("请至少提供一个需要修改的模型设置字段")
        for field_name in ("name", "model_name"):
            if field_name in self.model_fields_set:
                value = getattr(self, field_name)
                if value is None or not str(value).strip():
                    raise ValueError(f"{field_name} 不能清空")
        return self


class AgentTemplateCreateRequest(ApiSchema):
    name: str
    recommended_generation_mode: str = "llm"
    subject: str | None = None
    body_text: str | None = None
    body_html: str | None = None
    is_default: bool = False


class AgentTemplateUpdateRequest(ApiSchema):
    name: str | None = None
    recommended_generation_mode: str | None = None
    subject: str | None = None
    body_text: str | None = None
    body_html: str | None = None
    is_default: bool | None = None


class AgentCommunicationSyncRequest(ApiSchema):
    identity_id: int = Field(ge=1)


class AgentMatchAnalysisJobCreateRequest(ApiSchema):
    identity_id: int = Field(ge=1)
    llm_profile_id: int = Field(ge=1)
    professor_ids: list[int] = Field(min_length=1)
    name: str | None = Field(default=None, max_length=255)


class AgentCrawlCandidateUpdateRequest(ApiSchema):
    """Partial candidate update used by Agents after they have reviewed crawl output."""

    name: str | None = None
    email: str | None = None
    title: str | None = None
    university: str | None = None
    school: str | None = None
    department: str | None = None
    research_direction: str | None = None
    recent_papers: list[str] | None = None
    profile_url: str | None = None
    source_url: str | None = None
    review_status: CrawlCandidateReviewStatusDTO | None = None

    @model_validator(mode="after")
    def require_at_least_one_field(self) -> "AgentCrawlCandidateUpdateRequest":
        if not self.model_fields_set:
            raise ValueError("请至少提供一个需要修改的字段")
        return self


class AgentCrawlJobApproveRequest(ApiSchema):
    """Candidate IDs to import after a separately confirmed impact preview."""

    candidate_ids: list[int] = Field(min_length=1)

    @model_validator(mode="after")
    def reject_duplicate_candidate_ids(self) -> "AgentCrawlJobApproveRequest":
        if any(candidate_id < 1 for candidate_id in self.candidate_ids):
            raise ValueError("candidate_ids 必须是正整数")
        if len(set(self.candidate_ids)) != len(self.candidate_ids):
            raise ValueError("candidate_ids 不能包含重复的候选导师 ID")
        return self


class AgentCrawlJobRetryRequest(ApiSchema):
    clear_existing_data: bool = True
    llm_profile_id: int | None = Field(default=None, ge=1)


class AgentCrawlJobEnrichRequest(ApiSchema):
    candidate_ids: list[int] = Field(min_length=1)
    llm_profile_id: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def validate_candidate_ids(self) -> "AgentCrawlJobEnrichRequest":
        if any(candidate_id < 1 for candidate_id in self.candidate_ids):
            raise ValueError("candidate_ids 必须是正整数")
        if len(set(self.candidate_ids)) != len(self.candidate_ids):
            raise ValueError("candidate_ids 不能包含重复的候选导师 ID")
        return self


class AgentCampaignCreateRequest(ApiSchema):
    name: str = Field(min_length=1, max_length=255)
    identity_id: int = Field(ge=1)
    llm_profile_id: int = Field(ge=1)
    professor_ids: list[int] = Field(min_length=1)
    generation_mode: AgentCampaignGenerationMode
    template_id: int | None = Field(default=None, ge=1)
    reference_material_id: int | None = Field(default=None, ge=1)
    attachment_material_ids: list[int] = Field(default_factory=list)
    subject: str | None = None
    body_text: str | None = None
    body_html: str | None = None
    schedule_type: AgentPlanDelivery = "immediate"
    window_start_time: str | None = None
    window_end_time: str | None = None
    emails_per_window: int | None = Field(default=None, ge=1)
    scheduled_dates: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_campaign_requirements(self) -> "AgentCampaignCreateRequest":
        if len(set(self.professor_ids)) != len(self.professor_ids):
            raise ValueError("professor_ids 不能包含重复的导师 ID")
        if any(professor_id < 1 for professor_id in self.professor_ids):
            raise ValueError("professor_ids 必须是正整数")
        if len(set(self.attachment_material_ids)) != len(self.attachment_material_ids):
            raise ValueError("attachment_material_ids 不能包含重复的材料 ID")
        if any(material_id < 1 for material_id in self.attachment_material_ids):
            raise ValueError("attachment_material_ids 必须是正整数")
        if self.generation_mode == "ai_rewrite" and self.reference_material_id is None:
            raise ValueError("AI 改写必须明确指定 reference_material_id")
        if self.schedule_type == "scheduled":
            if not self.scheduled_dates:
                raise ValueError("定时发送必须至少提供一个发送日期")
            if not self.window_start_time or not self.window_end_time:
                raise ValueError("定时发送必须提供发送时间窗口")
            if self.emails_per_window is None:
                raise ValueError("定时发送必须提供每天发送数量")
        return self


class AgentCampaignSendRequest(ApiSchema):
    item_ids: list[int] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_item_ids(self) -> "AgentCampaignSendRequest":
        if any(item_id < 1 for item_id in self.item_ids):
            raise ValueError("item_ids 必须是正整数")
        if len(set(self.item_ids)) != len(self.item_ids):
            raise ValueError("item_ids 不能包含重复的活动项 ID")
        return self


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
    effects: AgentPlanEffectsRead
    warnings: list[str] = Field(default_factory=list)
    result: dict[str, object] | None = None
    idempotent_replay: bool = False
    confirmation_message: str | None = None


class AgentChangePlanRead(ApiSchema):
    plan_id: str
    action: str
    status: AgentPlanStatus
    expires_at: datetime
    confirmed_at: datetime | None = None
    executed_at: datetime | None = None
    canceled_at: datetime | None = None
    summary: dict[str, object]
    effects: AgentPlanEffectsRead
    warnings: list[str] = Field(default_factory=list)
    result: dict[str, object] | None = None
    idempotent_replay: bool = False
    confirmation_message: str | None = None
