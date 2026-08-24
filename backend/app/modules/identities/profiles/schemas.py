from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel

from app.schemas.base import ApiSchema

from ..materials.public import IdentityMaterialRead


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
    default_outreach_template_id: int | None = None
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
    communication_group_id: int | None
    current_primary_material_id: int | None
    current_primary_material: IdentityMaterialRead | None
    materials: list[IdentityMaterialRead]
    effective_outreach_template_is_ready: bool
    daily_send_limit: int | None
    send_interval_min: int | None
    send_interval_max: int | None
    created_at: datetime
    updated_at: datetime


class IdentityReferenceCounts(ApiSchema):
    email_tasks: int = 0
    email_logs: int = 0
    batch_tasks: int = 0
    test_compose_sessions: int = 0
    test_compose_messages: int = 0
    match_analysis_jobs: int = 0
    match_analysis_runs: int = 0
    match_results: int = 0
    delivery_attempts: int = 0
    email_observations: int = 0
    agent_change_plans: int = 0


class IdentityDeletionBlocker(ApiSchema):
    kind: str
    label: str
    count: int
    entity_ids: list[int | str]
    surface: str


class IdentityDeletionAutomaticActions(ApiSchema):
    cancel_email_task_ids: list[int]
    stop_batch_task_ids: list[int]
    cancel_match_analysis_job_ids: list[int]
    invalidate_agent_change_plan_ids: list[str]


class IdentityDeletionImpact(ApiSchema):
    identity_id: int
    identity_name: str
    email_address: str
    is_default: bool
    can_delete: bool
    revision: str
    references: IdentityReferenceCounts
    blockers: list[IdentityDeletionBlocker]
    automatic_actions: IdentityDeletionAutomaticActions
    preserved_material_count: int
    communication_group_id: int | None
    warnings: list[str]


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
