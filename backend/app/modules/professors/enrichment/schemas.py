from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from app.schemas.base import ApiSchema


ProfessorInformationEnrichmentJobStatus = Literal[
    "queued",
    "running",
    "partially_completed",
    "completed",
    "failed",
    "canceled",
]
ProfessorInformationEnrichmentItemStatus = Literal[
    "queued",
    "running",
    "succeeded",
    "failed",
    "skipped",
    "canceled",
]


class CreateProfessorInformationEnrichmentRequest(BaseModel):
    llm_profile_id: int = Field(ge=1)


class CreateProfessorInformationEnrichmentJobRequest(BaseModel):
    professor_ids: list[int] = Field(min_length=1)
    llm_profile_id: int = Field(ge=1)
    name: str | None = Field(default=None, max_length=255)

    @field_validator("name", mode="before")
    @classmethod
    def _normalize_name(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip() or None
        return value


class ProfessorInformationEnrichmentJobRead(ApiSchema):
    id: int
    name: str
    trigger_mode: Literal["single", "batch"]
    status: ProfessorInformationEnrichmentJobStatus
    target_count: int
    completed_count: int
    queued_count: int
    running_count: int
    succeeded_count: int
    failed_count: int
    skipped_count: int
    canceled_count: int
    input_tokens: int
    output_tokens: int
    cached_tokens: int
    total_tokens: int
    llm_profile_id: int | None
    started_at: datetime | None
    finished_at: datetime | None
    duration_seconds: int
    created_at: datetime
    updated_at: datetime
    deleted_at: datetime | None
    last_error: str | None


class ProfessorInformationEnrichmentItemRead(ApiSchema):
    id: int
    job_id: int
    professor_id: int | None
    professor_name: str
    professor_email: str | None
    professor_title: str | None
    professor_university: str | None
    professor_school: str | None
    professor_department: str | None
    profile_url: str | None
    status: ProfessorInformationEnrichmentItemStatus
    enriched_fields: list[str] = Field(default_factory=list)
    error_message: str | None
    skip_reason: str | None
    input_tokens: int
    output_tokens: int
    cached_tokens: int
    total_tokens: int
    attempt_count: int
    started_at: datetime | None
    finished_at: datetime | None
    created_at: datetime
    updated_at: datetime


class ProfessorInformationEnrichmentItemsPageRead(ApiSchema):
    items: list[ProfessorInformationEnrichmentItemRead]
    total_count: int
    next_cursor: int | None
    has_more: bool


class ProfessorInformationEnrichmentActiveRead(ApiSchema):
    active: bool
    job: ProfessorInformationEnrichmentJobRead | None = None


class ProfessorInformationEnrichmentJobActionRead(ApiSchema):
    ok: bool
    job: ProfessorInformationEnrichmentJobRead
