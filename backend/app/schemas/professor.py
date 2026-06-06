from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from app.schemas.base import ApiSchema
from app.services.professor_field_normalization import normalize_recent_papers


ProfessorDashboardStatus = Literal[
    "not_contacted",
    "preparing",
    "ready_to_send",
    "contacted",
    "replied",
    "failed",
]


class ProfessorTagRead(ApiSchema):
    id: int
    name: str
    text_color: str
    background_color: str


class ProfessorTagPayload(BaseModel):
    name: str
    text_color: str
    background_color: str


class ProfessorTagUsageProfessorRead(ApiSchema):
    id: int
    name: str
    email: str | None
    university: str | None
    school: str | None


class ProfessorTagUsageRead(ApiSchema):
    tag: ProfessorTagRead
    professors: list[ProfessorTagUsageProfessorRead] = Field(default_factory=list)


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
    sent_count: int
    status: ProfessorDashboardStatus
    last_sent_at: datetime | None = None
    last_replied_at: datetime | None = None
    tags: list[ProfessorTagRead] = Field(default_factory=list)


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


class ProfessorImportFileResult(ApiSchema):
    inserted_count: int
    updated_count: int
    failed_count: int
    message: str


class ProfessorBulkArchivePayload(BaseModel):
    ids: list[int]


class ProfessorActionResult(ApiSchema):
    ok: bool
    affected_count: int
    message: str
