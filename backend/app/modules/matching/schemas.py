from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from app.schemas.base import ApiSchema

class CreateMatchAnalysisJobRequest(BaseModel):
    identity_id: int = Field(ge=1)
    llm_profile_id: int = Field(ge=1)
    professor_ids: list[int] = Field(min_length=1)
    name: str | None = None
    skip_existing: bool = False


class MatchAnalysisSelectionSummaryRequest(BaseModel):
    identity_id: int = Field(ge=1)
    professor_ids: list[int] = Field(min_length=1)


class MatchAnalysisSelectionSummaryRead(ApiSchema):
    selected_count: int
    analyzable_count: int
    missing_evidence_count: int
    already_scored_count: int
    unscored_analyzable_count: int


class MatchAnalysisJobRead(ApiSchema):
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
    match_source_identity_id: int | None
    llm_profile_id: int
    cancel_requested_at: datetime | None
    started_at: datetime | None
    finished_at: datetime | None
    created_at: datetime
    updated_at: datetime
    deleted_at: datetime | None
    last_error: str | None


class MatchAnalysisJobItemRead(ApiSchema):
    id: int
    job_id: int
    professor_id: int
    professor_name: str
    professor_email: str | None
    professor_title: str | None
    professor_university: str | None
    professor_school: str | None
    email_task_id: int | None
    status: str
    match_score: int | None
    match_analysis_run_id: int | None
    error_message: str | None
    skip_reason: str | None
    prompt_tokens: int
    completion_tokens: int
    cached_tokens: int
    total_tokens: int
    started_at: datetime | None
    finished_at: datetime | None
    updated_at: datetime


class MatchAnalysisJobItemsPageRead(ApiSchema):
    items: list[MatchAnalysisJobItemRead]
    total_count: int
    next_cursor: int | None
    has_more: bool


class MatchAnalysisJobActionResponse(ApiSchema):
    ok: bool
    job: MatchAnalysisJobRead
