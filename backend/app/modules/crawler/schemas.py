from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from app.schemas.base import ApiSchema
from .pages.tools import (
    UNSAFE_CRAWL_URL_MESSAGE,
    validate_safe_public_crawl_url,
)
from .v2.url_utils import normalize_url
from app.modules.professors.public import normalize_recent_papers


CrawlJobStatusDTO = Literal[
    "queued",
    "running",
    "paused",
    "needs_review",
    "partially_completed",
    "completed",
    "failed",
    "canceled",
]
CrawlJobEntryTypeDTO = Literal["list", "profile"]
CrawlCandidateReviewStatusDTO = Literal["pending", "accepted", "rejected", "merged"]


class CrawlJobCreatePayload(BaseModel):
    university: str
    school: str
    start_url: str
    start_urls: list[str] | None = None
    entry_type: CrawlJobEntryTypeDTO = "list"
    llm_profile_id: int | None = None

    @field_validator("university", "school", "start_url", mode="before")
    @classmethod
    def _strip_required_text(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip()
        return value

    @field_validator("start_urls", mode="before")
    @classmethod
    def _strip_start_urls(cls, value: object) -> object:
        if value is None:
            return []
        if isinstance(value, list):
            return [item.strip() if isinstance(item, str) else item for item in value]
        return value

    @field_validator("university", "school", "start_url")
    @classmethod
    def _required_text(cls, value: str) -> str:
        if not value:
            raise ValueError("不能为空")
        return value

    @field_validator("start_url")
    @classmethod
    def _validate_url(cls, value: str) -> str:
        if not value.startswith(("http://", "https://")):
            raise ValueError("教师列表页面 URL 必须以 http:// 或 https:// 开头")
        try:
            validate_safe_public_crawl_url(value)
        except ValueError as exc:
            raise ValueError(UNSAFE_CRAWL_URL_MESSAGE) from exc
        return value

    @model_validator(mode="after")
    def _normalize_start_urls(self) -> "CrawlJobCreatePayload":
        urls = self.start_urls or [self.start_url]
        normalized: list[str] = []
        seen: set[str] = set()
        for url in urls:
            if not isinstance(url, str) or not url:
                raise ValueError("页面 URL 不能为空")
            if not url.startswith(("http://", "https://")):
                raise ValueError("页面 URL 必须以 http:// 或 https:// 开头")
            try:
                validate_safe_public_crawl_url(url)
            except ValueError as exc:
                raise ValueError(UNSAFE_CRAWL_URL_MESSAGE) from exc
            dedupe_key = normalize_url(url)
            if dedupe_key in seen:
                continue
            seen.add(dedupe_key)
            normalized.append(url)
        if not normalized:
            raise ValueError("页面 URL 不能为空")
        self.start_url = normalized[0]
        self.start_urls = normalized
        return self


class CrawlJobRead(ApiSchema):

    id: int
    university: str
    school: str
    start_url: str
    start_urls: list[str] | None = None
    entry_type: CrawlJobEntryTypeDTO = "list"
    llm_profile_id: int | None
    status: CrawlJobStatusDTO
    progress_current: int
    progress_total: int
    error_message: str | None
    created_at: datetime
    updated_at: datetime
    deleted_at: datetime | None

    @model_validator(mode="after")
    def _normalize_read_start_urls(self) -> "CrawlJobRead":
        self.start_urls = self.start_urls or [self.start_url]
        return self


class CrawlJobRetryPayload(BaseModel):
    clear_existing_data: bool = True
    llm_profile_id: int | None = None


class CrawlJobResumePayload(BaseModel):
    llm_profile_id: int | None = None


class CrawlJobLLMContextRead(ApiSchema):
    profile_source: Literal["explicit", "job", "global_default"]
    profile_id: int
    profile_revision: str
    profile_name: str
    provider: str
    model_name: str
    effective_models: list[str] = Field(default_factory=list)


class CrawlJobSummaryRead(CrawlJobRead):
    page_count: int = 0
    candidate_count: int = 0
    latest_event_message: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    cached_tokens: int = 0
    total_tokens: int = 0
    duration_seconds: int = 0
    llm_context: CrawlJobLLMContextRead | None = None


class CrawlJobDetailsRead(ApiSchema):
    job: CrawlJobSummaryRead
    pages: list["CrawlPageRead"]
    candidates: list["CrawlCandidateRead"]
    events: list["CrawlJobEventRead"]


class CrawlJobEventRead(ApiSchema):
    id: str
    job_id: int
    event_type: str
    message: str
    created_at: str | None
    raw: dict[str, object] | None = None


class CrawlPageRead(ApiSchema):

    id: int
    job_id: int
    url: str
    parent_url: str | None
    fetch_method: str
    page_type: str
    status: str
    title: str | None
    text_excerpt: str | None
    error_message: str | None
    created_at: datetime


class CrawlCandidateRead(ApiSchema):

    id: int
    job_id: int
    professor_id: int | None
    name: str
    email: str | None
    title: str | None
    university: str | None
    school: str | None
    department: str | None
    research_direction: str | None
    recent_papers: list[str]
    profile_url: str | None
    source_url: str | None
    confidence: float
    field_confidence: dict[str, float] | None
    evidence: dict[str, object] | None
    review_status: CrawlCandidateReviewStatusDTO
    created_at: datetime
    updated_at: datetime

    @field_validator("recent_papers", mode="before")
    @classmethod
    def _normalize_recent_papers(cls, value: object) -> list[str]:
        return normalize_recent_papers(value)


class CrawlCandidateUpdatePayload(BaseModel):
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
    review_status: CrawlCandidateReviewStatusDTO = "pending"

    @field_validator(
        "name",
        "email",
        "title",
        "university",
        "school",
        "department",
        "research_direction",
        "profile_url",
        "source_url",
        mode="before",
    )
    @classmethod
    def _strip_string_fields(cls, value: object) -> object:
        if isinstance(value, str):
            stripped = value.strip()
            return stripped or None
        return value

    @field_validator("name")
    @classmethod
    def _validate_name(cls, value: str | None) -> str:
        if not value:
            raise ValueError("姓名不能为空")
        return value

    @field_validator("recent_papers", mode="before")
    @classmethod
    def _normalize_recent_papers(cls, value: object) -> list[str]:
        return normalize_recent_papers(value)


class CrawlJobApprovePayload(BaseModel):
    candidate_ids: list[int]


class CrawlJobApproveResult(ApiSchema):
    inserted_count: int
    updated_count: int
    skipped_count: int
    message: str


class CrawlJobEnrichPayload(BaseModel):
    candidate_ids: list[int]
    llm_profile_id: int | None = None


class CrawlJobEnrichSelectionRead(ApiSchema):
    mode: Literal["ids", "all", "filter"]
    matched_count: int
    eligible_count: int
    excluded_count: int = 0


class CrawlJobEnrichSubmissionRead(ApiSchema):
    queued_count: int
    already_active_count: int
    already_completed_count: int
    rejected_count: int = 0


class CrawlJobReasonCountRead(ApiSchema):
    code: str
    count: int
    message: str
    recoverable: bool
    suggested_action: str | None = None


class CrawlJobSkipSummaryRead(ApiSchema):
    count: int
    by_reason: list[CrawlJobReasonCountRead] = Field(default_factory=list)


class CrawlJobEnrichObservationRead(ApiSchema):
    resource: str = "crawler.jobs"
    id: int
    status: CrawlJobStatusDTO
    settled: bool
    terminal: bool = False


class CrawlJobEnrichResult(ApiSchema):
    phase: Literal["submission"] = "submission"
    selected_count: int
    enriched_count: int
    unchanged_count: int
    failed_count: int
    skipped_count: int = 0
    message: str
    selection: CrawlJobEnrichSelectionRead | None = None
    submission: CrawlJobEnrichSubmissionRead | None = None
    skips: CrawlJobSkipSummaryRead | None = None
    observation: CrawlJobEnrichObservationRead | None = None
