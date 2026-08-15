from __future__ import annotations

"""Strict wire contracts and the shared structured-output request for crawlers.

The crawler persistence models intentionally accept legacy aliases and loosely
shaped metadata.  They are therefore unsuitable as strict JSON Schema wire
contracts.  The models in this module describe only what an LLM may emit; the
conversion helpers restore the existing persistence-facing shape afterwards.
"""

from typing import Literal, TypeVar

from pydantic import BaseModel, ConfigDict, ValidationInfo, field_validator, model_validator
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models import LLMProfile
from app.modules.llm.public import (
    DEFAULT_LLM_TEMPERATURE,
    ChatCompletionResult,
    LLMRuntimeAdaptation,
    LLMRuntimeError,
    request_structured_completion,
)


CrawlerStructuredResultT = TypeVar("CrawlerStructuredResultT", bound=BaseModel)

CANDIDATE_WIRE_PROMPT_CONTRACT = (
    "候选对象必须完整包含英文键：name、email、title、university、school、department、"
    "research_direction、recent_papers、profile_url、source_url、confidence、"
    "field_confidence、evidence_summary。\n"
    "没有证据的字符串字段必须使用空字符串，recent_papers 和 field_confidence 必须使用空数组。\n"
    "confidence 必须是 0 到 1 的数字。field_confidence 必须是数组；每项只能包含 field 和 "
    "confidence，例如 [{\"field\":\"name\",\"confidence\":0.95}]，只列有明确证据的字段且字段不得重复。\n"
    "evidence_summary 必须是简短字符串；没有必要摘要时使用空字符串，不能返回 evidence 对象。"
)

EMPTY_CANDIDATE_WIRE_JSON = (
    '{"name":"","email":"","title":"","university":"","school":"",'
    '"department":"","research_direction":"","recent_papers":[],'
    '"profile_url":"","source_url":"","confidence":0,'
    '"field_confidence":[],"evidence_summary":""}'
)

_CANDIDATE_CONFIDENCE_FIELDS = {
    "name",
    "email",
    "title",
    "university",
    "school",
    "department",
    "research_direction",
    "recent_papers",
    "profile_url",
    "source_url",
}


class CandidateFieldConfidenceWire(BaseModel):
    model_config = ConfigDict(extra="forbid")

    field: Literal[
        "name",
        "email",
        "title",
        "university",
        "school",
        "department",
        "research_direction",
        "recent_papers",
        "profile_url",
        "source_url",
    ]
    confidence: float

    @field_validator("confidence", mode="before")
    @classmethod
    def _validate_confidence(cls, value: object) -> object:
        return _require_confidence(value)


class ProfessorCandidateWirePayload(BaseModel):
    """LLM-facing candidate shape with no aliases or open-ended objects."""

    model_config = ConfigDict(extra="forbid")

    name: str
    email: str
    title: str
    university: str
    school: str
    department: str
    research_direction: str
    recent_papers: list[str]
    profile_url: str
    source_url: str
    confidence: float
    field_confidence: list[CandidateFieldConfidenceWire]
    evidence_summary: str

    @field_validator("confidence", mode="before")
    @classmethod
    def _validate_confidence(cls, value: object) -> object:
        return _require_confidence(value)

    @field_validator("field_confidence")
    @classmethod
    def _validate_unique_confidence_fields(
        cls,
        value: list[CandidateFieldConfidenceWire],
    ) -> list[CandidateFieldConfidenceWire]:
        fields = [item.field for item in value]
        if len(fields) != len(set(fields)):
            raise ValueError("field_confidence 不能包含重复字段")
        return value


class CandidateEnrichmentWirePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    page_relation: Literal["matched", "mismatched", "uncertain"]
    email: str
    title: str
    department: str
    research_direction: str
    recent_papers: list[str]

    @model_validator(mode="before")
    @classmethod
    def _default_legacy_page_relation(cls, value: object) -> object:
        if not isinstance(value, dict) or "page_relation" in value:
            return value
        return {**value, "page_relation": "uncertain"}


class CandidateEmailSelectionWirePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: str


class ProfileLinkSelectionWirePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    link_ids: list[int]


class V2ChunkWirePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    candidate_count: int
    candidates: list[ProfessorCandidateWirePayload]

    @model_validator(mode="before")
    @classmethod
    def _preserve_safe_legacy_fallback_behavior(
        cls,
        value: object,
        info: ValidationInfo,
    ) -> object:
        mode = (info.context or {}).get("structured_output_mode")
        if mode == "json_schema_strict" or not isinstance(value, dict):
            return value
        normalized = dict(value)
        normalized.pop("chunk_status", None)
        normalized.pop("discovered_urls", None)
        candidate_count = normalized.get("candidate_count")
        if (
            isinstance(candidate_count, int)
            and not isinstance(candidate_count, bool)
            and candidate_count > 10
        ):
            # Candidate bodies are ignored whenever the chunk must be split.
            # Clearing them retains the former count-first behavior without
            # weakening the strict schema used by capable providers.
            normalized["candidates"] = []
        return normalized

    @field_validator("candidate_count", mode="before")
    @classmethod
    def _validate_candidate_count(cls, value: object) -> object:
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValueError("candidate_count 必须是大于等于 0 的整数")
        return value


class V2ProfileExtractionWirePayload(BaseModel):
    """Always carries a candidate object to avoid a nullable strict schema.

    ``candidate`` is ignored when ``status`` is ``no_candidate``.  The prompt
    asks the model to fill it with the documented empty candidate object.
    """

    model_config = ConfigDict(extra="forbid")

    status: Literal["candidate", "no_candidate"]
    candidate: ProfessorCandidateWirePayload


async def request_crawler_structured_completion(
    session_factory: async_sessionmaker[AsyncSession],
    llm_profile: LLMProfile,
    adaptation: LLMRuntimeAdaptation,
    *,
    prompt: str,
    result_model: type[CrawlerStructuredResultT],
) -> tuple[ChatCompletionResult, CrawlerStructuredResultT, str]:
    """Use the central endpoint/thinking/structured-output adaptations.

    The session is committed on both success and model/runtime failure so a
    learned capability or a conditional cache invalidation is not rolled back
    when the surrounding crawler worker uses short-lived sessions.
    """

    temperature = getattr(llm_profile, "temperature", None)
    payload: dict[str, object] = {
        "model": llm_profile.model_name,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": (
            temperature
            if temperature is not None
            else DEFAULT_LLM_TEMPERATURE
        ),
    }
    async with session_factory() as session:
        try:
            result = await request_structured_completion(
                llm_profile,
                payload,
                result_model,
                session=session,
                adaptation=adaptation,
            )
        except (LLMRuntimeError, ValueError):
            await session.commit()
            raise
        await session.commit()
        return result


def professor_candidate_wire_to_dict(
    value: ProfessorCandidateWirePayload,
) -> dict[str, object]:
    payload = value.model_dump(
        exclude={"field_confidence", "evidence_summary"},
    )
    field_confidence = {
        item.field: item.confidence
        for item in value.field_confidence
        if item.field in _CANDIDATE_CONFIDENCE_FIELDS
    }
    evidence_summary = value.evidence_summary.strip()
    payload["field_confidence"] = field_confidence or None
    payload["evidence"] = (
        {"summary": evidence_summary}
        if evidence_summary
        else None
    )
    return payload


def v2_profile_wire_to_dict(
    value: V2ProfileExtractionWirePayload,
) -> dict[str, object]:
    if value.status == "no_candidate":
        return {"status": "no_candidate", "candidate": None}
    candidate = professor_candidate_wire_to_dict(value.candidate)
    if not str(candidate.get("name") or "").strip():
        raise ValueError("status=candidate 时 candidate.name 不能为空")
    return {"status": "candidate", "candidate": candidate}


def _require_confidence(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("confidence 必须是 0 到 1 的数字")
    parsed = float(value)
    if not 0 <= parsed <= 1:
        raise ValueError("confidence 必须是 0 到 1 的数字")
    return parsed
