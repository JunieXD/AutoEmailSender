from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal, TypedDict

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, field_validator

from app.models.crawl_job import CrawlCandidate
from app.modules.professors.public import (
    normalize_professor_title,
    normalize_recent_papers,
    normalize_research_direction,
)


class PageSnapshot(BaseModel):
    page_id: int | None = None
    url: str
    title: str | None = None
    text: str = ""
    html: str = ""
    links: list[str] = Field(default_factory=list)
    fetch_method: str
    status: Literal["succeeded", "failed"]
    http_status_code: int | None = None
    error_message: str | None = None
    suspicious_empty: bool = False
    has_client_encrypted_profile_fields: bool = False
    has_dynamic_teacher_directory_markers: bool = False
    has_invalid_profile_page_markers: bool = False


@dataclass(frozen=True, slots=True)
class BrowserPaginationExpansion:
    status: Literal["succeeded", "failed"]
    snapshots: tuple[PageSnapshot, ...] = ()
    stopped_reason: str | None = None
    error_message: str | None = None


@dataclass(frozen=True, slots=True)
class BrowserSamePageExpansion:
    status: Literal["succeeded", "failed"]
    snapshots: tuple[PageSnapshot, ...] = ()
    stopped_reason: str | None = None
    error_message: str | None = None


class ProfessorCandidatePayload(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    name: str = Field(validation_alias=AliasChoices("name", "姓名"))
    email: str | None = Field(
        default=None,
        validation_alias=AliasChoices("email", "邮箱", "邮箱地址"),
    )
    title: str | None = Field(
        default=None,
        validation_alias=AliasChoices("title", "职称", "岗位"),
    )
    university: str | None = Field(
        default=None,
        validation_alias=AliasChoices("university", "学校", "院校"),
    )
    school: str | None = Field(
        default=None,
        validation_alias=AliasChoices("school", "学院", "院系", "学院/单位", "单位"),
    )
    department: str | None = Field(
        default=None,
        validation_alias=AliasChoices("department", "部门", "系别"),
    )
    research_direction: str | None = Field(
        default=None,
        validation_alias=AliasChoices("research_direction", "研究方向", "研究领域"),
    )
    recent_papers: list[str] = Field(
        default_factory=list,
        validation_alias=AliasChoices("recent_papers", "近期论文", "代表论文"),
    )
    profile_url: str | None = Field(
        default=None,
        validation_alias=AliasChoices("profile_url", "主页URL", "主页链接", "个人主页"),
    )
    source_url: str | None = Field(
        default=None,
        validation_alias=AliasChoices("source_url", "证据来源", "来源页面", "页面URL"),
    )
    confidence: float = Field(
        default=0.0,
        validation_alias=AliasChoices("confidence", "置信度"),
    )
    field_confidence: dict[str, float] | None = Field(
        default=None,
        validation_alias=AliasChoices("field_confidence", "字段置信度"),
    )
    evidence: dict[str, object] | None = Field(
        default=None,
        validation_alias=AliasChoices("evidence", "证据"),
    )
    source_chunk_id: str | None = None
    source_kind: str | None = None
    boundary_risk: bool = False
    identity_key: str | None = None
    merge_history: list[dict[str, object]] | None = None
    field_sources: dict[str, object] | None = None
    conflicts: dict[str, object] | None = None

    @field_validator("research_direction", mode="before")
    @classmethod
    def _normalize_research_direction(cls, value: object) -> object:
        return normalize_research_direction(value)

    @field_validator("recent_papers", mode="before")
    @classmethod
    def _normalize_recent_papers(cls, value: object) -> list[str]:
        return normalize_recent_papers(value)

    @field_validator("confidence", mode="before")
    @classmethod
    def _normalize_confidence(cls, value: object) -> float:
        return _clamp_confidence(value)

    @field_validator("field_confidence", mode="before")
    @classmethod
    def _normalize_field_confidence(cls, value: object) -> dict[str, float] | None:
        if value is None:
            return None
        if isinstance(value, str):
            stripped = value.strip()
            if not stripped:
                return None
            numeric = _try_float(stripped)
            return {"overall": numeric} if numeric is not None else None
        if isinstance(value, (int, float)):
            return {"overall": float(value)}
        if not isinstance(value, dict):
            return None

        normalized: dict[str, float] = {}
        for key, item in value.items():
            if str(key) == "fields" and isinstance(item, dict):
                for nested_key, nested_item in item.items():
                    numeric = _normalize_confidence_value(nested_item)
                    if numeric is not None and str(nested_key).strip():
                        normalized[str(nested_key).strip()] = numeric
                continue
            numeric = _normalize_confidence_value(item)
            if numeric is not None and str(key).strip():
                normalized[str(key).strip()] = numeric
        return normalized or None

    @field_validator("evidence", mode="before")
    @classmethod
    def _normalize_evidence(cls, value: object) -> dict[str, object] | None:
        if value is None:
            return None
        if isinstance(value, dict):
            return value
        if isinstance(value, str):
            stripped = value.strip()
            return {"summary": stripped} if stripped else None
        return None


class CandidateEnrichmentPayload(BaseModel):
    page_relation: Literal["matched", "mismatched", "uncertain"] = "uncertain"
    email: str | None = None
    title: str | None = None
    department: str | None = None
    research_direction: str | None = None
    recent_papers: list[str] = Field(default_factory=list)

    @field_validator("title", mode="before")
    @classmethod
    def _normalize_title(cls, value: object) -> str | None:
        return normalize_professor_title(_clean_optional(value))

    @field_validator("recent_papers", mode="before")
    @classmethod
    def _normalize_recent_papers(cls, value: object) -> list[str]:
        return normalize_recent_papers(value)


class CandidateBatchFailure(TypedDict):
    index: int
    name: str | None
    reason: str


class SharedCandidateSaveResult(TypedDict):
    attempted_count: int
    saved_count: int
    merged_count: int
    skipped_duplicate_count: int
    rejected_count: int
    rejected_items: list[CandidateBatchFailure]
    saved: list[CrawlCandidate]


@dataclass(frozen=True)
class CandidatePersistenceResult:
    saved: list[CrawlCandidate]
    merged_count: int = 0
    skipped_duplicate_count: int = 0


def _clean_required(value: object) -> str:
    cleaned = str(value).strip() if value is not None else ""
    if not cleaned:
        raise ValueError("必填文本不能为空")
    return cleaned


def _clean_optional(value: object) -> str | None:
    if value is None:
        return None
    cleaned = str(value).strip()
    return cleaned or None


def _try_float(value: object) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


_CONFIDENCE_LABEL_MAP = {
    "very high": 1.0,
    "high": 0.9,
    "medium": 0.6,
    "moderate": 0.6,
    "low": 0.3,
    "very low": 0.1,
    "高": 0.9,
    "较高": 0.8,
    "中": 0.6,
    "中等": 0.6,
    "一般": 0.5,
    "低": 0.3,
    "较低": 0.2,
}


def _normalize_confidence_value(value: object) -> float | None:
    if value is None:
        return None

    if isinstance(value, (int, float)):
        return float(value)

    if not isinstance(value, str):
        return None

    stripped = value.strip()
    if not stripped:
        return None

    if stripped.endswith("%"):
        numeric = _try_float(stripped[:-1].strip())
        if numeric is not None:
            return numeric / 100

    numeric = _try_float(stripped)
    if numeric is not None:
        return numeric

    normalized = re.sub(r"[\s_-]+", " ", stripped.casefold())
    return _CONFIDENCE_LABEL_MAP.get(normalized)


def _clamp_confidence(value: object) -> float:
    number = _normalize_confidence_value(value)
    if number is None:
        return 0.0
    return min(1.0, max(0.0, number))
