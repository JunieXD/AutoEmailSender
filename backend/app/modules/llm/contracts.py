from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class LLMRuntimeError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        request_url: str | None = None,
        attempted_urls: list[str] | None = None,
        endpoint_kind: str | None = None,
        status_code: int | None = None,
        duration_ms: int | None = None,
        usage: object | None = None,
        raw_content: str | None = None,
    ) -> None:
        super().__init__(message)
        self.request_url = request_url
        self.attempted_urls = attempted_urls or ([request_url] if request_url else [])
        self.endpoint_kind = endpoint_kind
        self.status_code = status_code
        self.duration_ms = duration_ms
        self.usage = usage
        self.raw_content = raw_content


class LLMEndpointProtocolError(LLMRuntimeError):
    def __init__(
        self,
        message: str,
        *,
        failed_endpoint_kind: Literal["chat_completions", "responses"],
        response_envelope: Literal["other_endpoint", "invalid"] | None,
        request_url: str | None = None,
        attempted_urls: list[str] | None = None,
        status_code: int | None = None,
        duration_ms: int | None = None,
    ) -> None:
        super().__init__(
            message,
            request_url=request_url,
            attempted_urls=attempted_urls,
            endpoint_kind=failed_endpoint_kind,
            status_code=status_code,
            duration_ms=duration_ms,
        )
        self.failed_endpoint_kind = failed_endpoint_kind
        self.response_envelope = response_envelope


class LLMEmptyContentError(LLMRuntimeError):
    """HTTP 200 but the response carried no usable text content.

    Thinking models park the answer in ``reasoning_content`` and leave
    ``content`` empty. Callers classify this exception instead of matching
    message wording, so diagnostic phrasing can evolve freely.
    """


@dataclass(slots=True)
class ChatCompletionUsage:
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None
    cached_tokens: int | None = None
    reasoning_tokens: int | None = None


@dataclass(slots=True)
class ChatCompletionResult:
    content: str
    usage: ChatCompletionUsage | None = None
    request_url: str | None = None
    attempted_urls: list[str] = field(default_factory=list)
    endpoint_kind: str | None = None
    status_code: int | None = None
    duration_ms: int | None = None


@dataclass(frozen=True, slots=True)
class LLMRuntimeAdaptation:
    """The endpoint protocol and thinking override learned for one model."""

    endpoint_kind: Literal["chat_completions", "responses"]
    thinking_extra_body: dict[str, object] | None
    endpoint_attempted_urls: tuple[str, ...] = field(
        default_factory=tuple, compare=False
    )


@dataclass(slots=True)
class DraftTokenEstimate:
    estimated_prompt_tokens: int
    estimated_completion_tokens_upper_bound: int
    estimated_total_tokens_upper_bound: int


@dataclass(slots=True)
class MatchPromptParts:
    prompt: str
    stable_prefix: str
    prompt_hash: str
    stable_prefix_hash: str
    prompt_cache_key: str | None = None


@dataclass(slots=True)
class DraftRewritePromptParts:
    prompt: str
    stable_prefix: str
    prompt_hash: str
    stable_prefix_hash: str
    prompt_cache_key: str | None = None


class MatchEvaluationResult(BaseModel):
    match_score: int = Field(ge=0, le=100)
    match_reason: str
    fit_points: list[str] = Field(default_factory=list)
    risk_points: list[str] = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=list)


class MatchEvaluationWireResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    match_score: int = Field(strict=True)
    match_reason: str
    fit_points: list[str]
    risk_points: list[str]
    keywords: list[str]


class DraftGenerationResult(BaseModel):
    subject: str
    body_text: str | None = None
    body_html: str | None = None
    rich_body: dict[str, object] | None = None


class DraftBodyRunWire(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str
    strong: bool
    emphasis: bool
    href: str
    line_break_after: bool


class DraftBodyItemWire(BaseModel):
    model_config = ConfigDict(extra="forbid")

    runs: list[DraftBodyRunWire]


class DraftBodyBlockWire(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["paragraph", "bullet_list", "numbered_list"]
    items: list[DraftBodyItemWire]


class DraftGenerationWireResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    subject: str
    blocks: list[DraftBodyBlockWire]


class DraftRewriteSegmentReplacement(BaseModel):
    model_config = ConfigDict(extra="forbid")

    segment_id: str
    text: str


class DraftRewriteResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    replacements: list[DraftRewriteSegmentReplacement]


@dataclass(slots=True)
class DraftRewritePreferences:
    draft_rewrite_intensity: str = "moderate"
    draft_rewrite_tone: str = "polite"
    draft_rewrite_formality: str = "balanced"
    draft_rewrite_length: str = "default"
    draft_rewrite_specificity: str = "balanced"
    draft_template_preservation: str = "structure_first"
    draft_custom_instruction: str = ""
    intended_research_direction: str = ""


class LLMProbeResult(BaseModel):
    ok: bool
    message: str
    resolved_base_url: str | None = None
    request_url: str | None = None
    attempted_urls: list[str] = Field(default_factory=list)
    endpoint_kind: str | None = None
    status_code: int | None = None
    duration_ms: int | None = None
    consumes_tokens: bool = True
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None
    response_preview: str | None = None


class LLMModelCatalogResult(BaseModel):
    ok: bool
    message: str
    resolved_base_url: str | None = None
    request_url: str | None = None
    attempted_urls: list[str] = Field(default_factory=list)
    endpoint_kind: str | None = None
    status_code: int | None = None
    duration_ms: int | None = None
    consumes_tokens: bool = False
    models: list[str] = Field(default_factory=list)
    selected_model_available: bool | None = None


@dataclass(slots=True)
class GeneratedMatchEvaluation:
    result: MatchEvaluationResult
    usage: ChatCompletionUsage | None = None
    request_url: str | None = None
    attempted_urls: list[str] = field(default_factory=list)
    endpoint_kind: str | None = None
    status_code: int | None = None
    duration_ms: int | None = None
    prompt_hash: str | None = None
    stable_prefix_hash: str | None = None
    prompt_cache_key: str | None = None


@dataclass(slots=True)
class GeneratedDraftContent:
    result: DraftGenerationResult
    usage: ChatCompletionUsage | None = None
    prompt_hash: str | None = None
    stable_prefix_hash: str | None = None
    prompt_cache_key: str | None = None
