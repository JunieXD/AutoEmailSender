from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field, field_validator

from app.schemas.base import ApiSchema

class CreateBatchTaskRequest(BaseModel):
    identity_id: int
    llm_profile_id: int
    name: str
    professor_ids: list[int]
    schedule_type: str = "immediate"
    window_start_time: str | None = None
    window_end_time: str | None = None
    emails_per_window: int | None = None
    scheduled_dates: list[str] | None = None
    primary_material_id: int | None = None
    email_subject: str | None = None
    email_body: str | None = None
    selected_material_ids: list[int] | None = None
    outreach_generation_mode: str | None = None
    outreach_template_subject: str | None = None
    outreach_template_body_text: str | None = None
    outreach_template_body_html: str | None = None
    outreach_template_id: int | None = None
    resend_source_batch_task_id: int | None = Field(default=None, gt=0)


class BatchTaskCardRead(ApiSchema):
    id: int
    name: str
    status: str
    schedule_type: str
    window_start_time: str | None
    window_end_time: str | None
    emails_per_window: int | None
    scheduled_dates: list[str] | None
    email_subject: str | None
    outreach_template_id: int | None
    outreach_template_name_snapshot: str | None
    outreach_template_snapshot_version: int | None
    outreach_generation_mode: str | None
    target_count: int
    completed_count: int
    identity_id: int
    llm_profile_id: int
    pending_generation_count: int
    queued_generation_count: int
    blocked_generation_count: int
    generating_draft_count: int
    draft_failed_count: int
    review_required_count: int
    approved_count: int
    scheduled_count: int
    sent_count: int
    failed_count: int
    replied_count: int
    canceled_send_count: int
    created_at: datetime
    updated_at: datetime
    deleted_at: datetime | None


class BatchTaskItemRead(ApiSchema):
    id: int
    professor_id: int
    professor_name: str
    professor_email: str | None
    professor_title: str | None
    professor_school: str | None
    professor_research_direction: str | None
    status: str
    cancellation_reason: str | None
    batch_send_canceled_at: datetime | None
    can_cancel_send: bool
    can_restore_send: bool
    match_score: int | None
    scheduled_at: datetime | None
    sent_at: datetime | None
    last_send_attempt_at: datetime | None
    last_error: str | None
    possible_cause: str | None
    draft_generation_source: str | None
    draft_fallback_reason: str | None
    is_replied: bool
    updated_at: datetime
    next_action: str | None
    selected_attachment_size_bytes: int = 0


class BatchTaskActionResponse(ApiSchema):
    ok: bool
    task: BatchTaskCardRead


class BatchTaskBulkApproveDraftsRequest(BaseModel):
    item_ids: list[int] = Field(min_length=1)

    @field_validator("item_ids")
    @classmethod
    def validate_unique_item_ids(cls, item_ids: list[int]) -> list[int]:
        if len(item_ids) != len(set(item_ids)):
            raise ValueError("待审核草稿不能重复")
        return item_ids


class BatchTaskBulkApproveDraftsResponse(ApiSchema):
    ok: bool
    approved_count: int
    task: BatchTaskCardRead

class BatchTaskResendContextTaskRead(ApiSchema):
    id: int
    name: str
    identity_id: int
    schedule_type: str

class BatchTaskResendDefaultsRead(ApiSchema):
    identity_id: int
    outreach_template_id: int | None
    outreach_template_name_snapshot: str | None
    outreach_generation_mode: str | None
    outreach_template_subject: str | None
    outreach_template_body_text: str | None
    outreach_template_body_html: str | None
    primary_material_id: int | None
    selected_material_ids: list[int]

class BatchTaskResendItemRead(ApiSchema):
    email_task_id: int
    professor_id: int | None
    professor_name: str
    professor_email: str | None
    status: str
    cancellation_reason: str | None
    reason_label: str
    default_selected: bool
    selectable: bool
    unavailable_reason: str | None
    content_reuse_kind: str
    content_requires_review: bool
    updated_at: datetime

class BatchTaskResendSummaryRead(ApiSchema):
    candidate_count: int
    default_selected_count: int
    unavailable_count: int

class BatchTaskResendContextRead(ApiSchema):
    task: BatchTaskResendContextTaskRead
    defaults: BatchTaskResendDefaultsRead
    items: list[BatchTaskResendItemRead]
    summary: BatchTaskResendSummaryRead
    warnings: list[str]
