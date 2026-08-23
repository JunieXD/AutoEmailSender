from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from app.schemas.base import ApiSchema
from .normalization import normalize_recent_papers


ProfessorDashboardStatus = Literal[
    "not_contacted",
    "preparing",
    "ready_to_send",
    "contacted",
    "replied",
    "failed",
]
ProfessorDashboardFilterStatus = Literal[
    "not_contacted",
    "preparing",
    "ready_to_send",
    "contacted",
    "replied",
    "failed",
    "scheduled",
]
ProfessorKeywordSearchScope = Literal[
    "name",
    "email",
    "university",
    "school",
    "department",
    "title",
    "researchDirection",
    "personalNote",
    "tag",
]
ProfessorSortDirection = Literal["asc", "desc"]
ProfessorDashboardSortKey = Literal[
    "latest",
    "matchScoreDesc",
    "sentCountDesc",
    "nameAsc",
    "lastSentAt",
    "lastRepliedAt",
]
ProfessorManagementSortKey = Literal[
    "latest",
    "updatedAtDesc",
    "nameAsc",
    "universityAsc",
]
MAX_PERSONAL_NOTE_LENGTH = 10_000


class ProfessorTagRead(ApiSchema):
    id: int
    name: str
    text_color: str
    background_color: str


class ProfessorTagPayload(BaseModel):
    name: str
    text_color: str
    background_color: str


class ProfessorTagUpdatePayload(BaseModel):
    tag_ids: list[int] = Field(default_factory=list)


class ProfessorTagUsageProfessorRead(ApiSchema):
    id: int
    name: str
    email: str | None
    university: str | None
    school: str | None


class ProfessorTagUsageRead(ApiSchema):
    tag: ProfessorTagRead
    professors: list[ProfessorTagUsageProfessorRead] = Field(default_factory=list)
    revision: str


class ProfessorRead(ApiSchema):
    id: int
    name: str
    email: str | None
    title: str | None
    university: str | None
    school: str | None
    department: str | None
    research_direction: str | None
    recent_papers: list[str] | None
    profile_url: str | None
    source_url: str | None
    crawl_status: str
    skip_reason: str | None
    personal_note: str | None
    archived_at: datetime | None
    created_at: datetime
    updated_at: datetime
    tags: list[ProfessorTagRead] = Field(default_factory=list)


class ProfessorDashboardItemRead(ApiSchema):
    id: int
    name: str
    email: str | None
    title: str | None
    university: str | None
    school: str | None
    department: str | None
    research_direction: str | None
    recent_papers: list[str]
    match_score: int | None
    match_source_identity_id: int | None = None
    match_source_identity_name: str | None = None
    match_is_shared: bool = False
    match_is_stale: bool = False
    match_analyzed_at: datetime | None = None
    sent_count: int
    status: ProfessorDashboardStatus
    has_active_schedule: bool = False
    last_sent_at: datetime | None = None
    last_replied_at: datetime | None = None
    personal_note: str | None = None
    tags: list[ProfessorTagRead] = Field(default_factory=list)


class ProfessorFilterOptionTagRead(ApiSchema):
    id: int
    name: str


class ProfessorFilterOptionsRead(ApiSchema):
    universities: list[str] = Field(default_factory=list)
    schools: list[str] = Field(default_factory=list)
    departments: list[str] = Field(default_factory=list)
    titles: list[str] = Field(default_factory=list)
    tags: list[ProfessorFilterOptionTagRead] = Field(default_factory=list)


class ProfessorPageRequestBase(BaseModel):
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=10, ge=1, le=100)
    cursor: str | None = Field(default=None, max_length=2_000)
    keyword: str = Field(default="", max_length=500)
    keyword_search_scopes: list[ProfessorKeywordSearchScope] = Field(
        default_factory=list,
    )
    universities: list[str] = Field(default_factory=list, max_length=200)
    schools: list[str] = Field(default_factory=list, max_length=200)
    departments: list[str] = Field(default_factory=list, max_length=200)
    titles: list[str] = Field(default_factory=list, max_length=200)
    tag_ids: list[str] = Field(default_factory=list, max_length=200)
    sort_direction: ProfessorSortDirection = "desc"
    ui_handoff_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=64,
        pattern=r"^uih_[A-Za-z0-9_-]+$",
    )

    @field_validator(
        "universities",
        "schools",
        "departments",
        "titles",
        "tag_ids",
        mode="after",
    )
    @classmethod
    def _deduplicate_filter_values(cls, values: list[str]) -> list[str]:
        return list(dict.fromkeys(value.strip() for value in values if value.strip()))

    @field_validator("keyword", mode="after")
    @classmethod
    def _strip_keyword(cls, value: str) -> str:
        return value.strip()


class ProfessorDashboardPageRequest(ProfessorPageRequestBase):
    identity_id: int = Field(ge=1)
    keyword_search_scopes: list[ProfessorKeywordSearchScope] = Field(
        default_factory=lambda: [
            "name",
            "email",
            "university",
            "school",
            "department",
            "title",
            "researchDirection",
            "personalNote",
            "tag",
        ],
    )
    statuses: list[ProfessorDashboardFilterStatus] = Field(
        default_factory=list,
        max_length=7,
    )
    min_match_score: int | None = Field(default=None, ge=0, le=100)
    max_match_score: int | None = Field(default=None, ge=0, le=100)
    match_score_missing: bool = False
    sort_key: ProfessorDashboardSortKey = "latest"


class ProfessorManagementPageRequest(ProfessorPageRequestBase):
    archived: Literal["active", "archived", "all"] = "active"
    keyword_search_scopes: list[ProfessorKeywordSearchScope] = Field(
        default_factory=lambda: [
            "name",
            "email",
            "university",
            "school",
            "department",
            "title",
            "researchDirection",
            "personalNote",
            "tag",
        ],
    )
    sort_key: ProfessorManagementSortKey = "latest"


class ProfessorDashboardPageRead(ApiSchema):
    items: list[ProfessorDashboardItemRead] = Field(default_factory=list)
    total_count: int
    has_any_professors: bool
    page: int
    page_size: int
    total_pages: int
    next_cursor: str | None = None
    filter_options: ProfessorFilterOptionsRead


class ProfessorManagementPageRead(ApiSchema):
    items: list[ProfessorManagementItemRead] = Field(default_factory=list)
    total_count: int
    has_any_professors: bool
    page: int
    page_size: int
    total_pages: int
    next_cursor: str | None = None
    filter_options: ProfessorFilterOptionsRead


class ProfessorIdSelectionRead(ApiSchema):
    ids: list[int] = Field(default_factory=list)
    total_count: int


class ProfessorImportResult(ApiSchema):
    inserted_count: int
    total_count: int
    message: str


class ProfessorManagementItemRead(ApiSchema):
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
    source_url: str | None
    crawl_status: str
    skip_reason: str | None
    personal_note: str | None
    archived_at: datetime | None
    created_at: datetime
    updated_at: datetime
    tags: list[ProfessorTagRead] = Field(default_factory=list)


class ProfessorUpsertPayload(BaseModel):
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
    personal_note: str | None = Field(default=None, max_length=MAX_PERSONAL_NOTE_LENGTH)
    tag_ids: list[int] = Field(default_factory=list)

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
        "personal_note",
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

    @field_validator("email")
    @classmethod
    def _validate_email(cls, value: str | None) -> str:
        if not value:
            raise ValueError("邮箱不能为空")
        return value

    @field_validator("recent_papers", mode="before")
    @classmethod
    def _normalize_recent_papers(cls, value: object) -> list[str]:
        return normalize_recent_papers(value)


class ProfessorNoteUpdatePayload(BaseModel):
    personal_note: str | None = Field(default=None, max_length=MAX_PERSONAL_NOTE_LENGTH)

    @field_validator("personal_note", mode="before")
    @classmethod
    def _strip_personal_note(cls, value: object) -> object:
        if isinstance(value, str):
            stripped = value.strip()
            return stripped or None
        return value


class ProfessorNoteUpdateRead(ApiSchema):
    id: int
    personal_note: str | None
    updated_at: datetime


class ProfessorImportFileResult(ApiSchema):
    inserted_count: int
    updated_count: int
    failed_count: int
    message: str


class ProfessorBulkArchivePayload(BaseModel):
    ids: list[int]


class ProfessorFetchByIdsPayload(BaseModel):
    identity_id: int | None = None
    ids: list[int] = Field(default_factory=list, max_length=10_000)
    page: int = Field(default=1, ge=1)
    # None returns the whole selection in one response (legacy behavior).
    page_size: int | None = Field(default=None, ge=1, le=100)


class ProfessorFetchByIdsRead(ApiSchema):
    items: list[ProfessorDashboardItemRead] = Field(default_factory=list)
    total_count: int
    page: int
    page_size: int
    total_pages: int


ProfessorBulkTagMode = Literal["add", "remove", "replace"]


class ProfessorBulkTagsPayload(BaseModel):
    professor_ids: list[int]
    mode: ProfessorBulkTagMode
    tag_ids: list[int]


class ProfessorBulkTagsResult(ApiSchema):
    ok: bool
    affected_count: int
    message: str


class ProfessorActionResult(ApiSchema):
    ok: bool
    affected_count: int
    message: str
