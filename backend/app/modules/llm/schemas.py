from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from app.schemas.base import ApiSchema


class LLMProfileBase(BaseModel):
    name: str
    provider: str = "openai"
    api_base_url: str | None = None
    api_key: str
    model_name: str
    matcher_prompt_template: str | None = None
    writer_prompt_template: str | None = None
    temperature: float | None = None
    max_tokens: int | None = None
    is_default: bool = False


class LLMProfileCreate(LLMProfileBase):
    pass


class LLMProfileUpdate(LLMProfileBase):
    pass


class LLMProfileRead(LLMProfileBase):
    id: int
    created_at: datetime
    updated_at: datetime


class LLMProfileReferenceCounts(ApiSchema):
    batch_tasks: int = 0
    email_tasks: int = 0
    email_logs: int = 0
    match_analysis_jobs: int = 0
    match_analysis_job_items: int = 0
    match_analysis_runs: int = 0
    test_compose_sessions: int = 0
    test_compose_messages: int = 0
    crawl_jobs: int = 0
    crawl_runs: int = 0
    crawl_pages: int = 0
    crawl_candidates: int = 0
    crawl_token_usages: int = 0
    match_results: int = 0
    agent_change_plans: int = 0
    operation_logs: int = 0


class LLMProfileDeletionBlocker(ApiSchema):
    kind: str
    label: str
    count: int
    entity_ids: list[int] = Field(default_factory=list)


class LLMProfileDeletionImpact(ApiSchema):
    profile_id: int
    profile_name: str
    model_name: str
    is_default: bool
    can_delete: bool
    revision: str
    references: LLMProfileReferenceCounts
    blockers: list[LLMProfileDeletionBlocker] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class LLMProfileDeletionResult(ApiSchema):
    ok: bool = True
    profile_id: int
    profile_name: str
    references_preserved: LLMProfileReferenceCounts
    invalidated_plan_count: int = 0
    default_profile_id: int | None = None


class LLMProfileTestResult(ApiSchema):
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


class LLMProfileModelsResult(ApiSchema):
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


__all__ = [
    "LLMProfileBase",
    "LLMProfileCreate",
    "LLMProfileDeletionBlocker",
    "LLMProfileDeletionImpact",
    "LLMProfileDeletionResult",
    "LLMProfileModelsResult",
    "LLMProfileRead",
    "LLMProfileReferenceCounts",
    "LLMProfileTestResult",
    "LLMProfileUpdate",
]
