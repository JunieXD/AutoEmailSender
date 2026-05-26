from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


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


class BatchTaskCardRead(BaseModel):
    id: int
    name: str
    status: str
    schedule_type: str
    window_start_time: str | None
    window_end_time: str | None
    emails_per_window: int | None
    scheduled_dates: list[str] | None
    email_subject: str | None
    target_count: int
    completed_count: int
    identity_id: int
    llm_profile_id: int
    pending_generation_count: int
    generating_draft_count: int
    draft_failed_count: int
    review_required_count: int
    approved_count: int
    scheduled_count: int
    sent_count: int
    failed_count: int
    replied_count: int
    created_at: datetime
    updated_at: datetime
    deleted_at: datetime | None


class BatchTaskItemRead(BaseModel):
    id: int
    professor_id: int
    professor_name: str
    professor_email: str | None
    professor_title: str | None
    professor_school: str | None
    status: str
    cancellation_reason: str | None
    match_score: int | None
    scheduled_at: datetime | None
    sent_at: datetime | None
    last_send_attempt_at: datetime | None
    last_error: str | None
    is_replied: bool
    updated_at: datetime
    next_action: str | None


class BatchTaskActionResponse(BaseModel):
    ok: bool
    task: BatchTaskCardRead
