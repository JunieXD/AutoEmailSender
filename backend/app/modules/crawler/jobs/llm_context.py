from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.agent_revisions import revision_for
from app.models import CrawlJob, LLMProfile
from app.modules.llm.public import (
    get_active_llm_profile,
    get_default_active_llm_profile,
)
from .runs import get_or_create_current_crawl_job_run


LLMProfileSource = Literal["explicit", "job", "global_default"]


@dataclass(frozen=True)
class CrawlLLMRuntimeProfile:
    id: int
    name: str
    provider: str
    api_base_url: str | None
    api_key: str
    model_name: str
    matcher_prompt_template: str | None
    writer_prompt_template: str | None
    temperature: float | None
    max_tokens: int | None


async def snapshot_crawl_job_llm_profile(
    session: AsyncSession,
    job: CrawlJob,
    profile: LLMProfile,
    *,
    source: LLMProfileSource,
) -> dict[str, object]:
    run = await get_or_create_current_crawl_job_run(session, job)
    snapshot = _snapshot_for_profile(profile, source=source)
    run.llm_runtime_snapshot = snapshot
    return snapshot


async def resolve_crawl_job_runtime_profile(
    session: AsyncSession,
    job: CrawlJob,
) -> CrawlLLMRuntimeProfile | None:
    run = await get_or_create_current_crawl_job_run(session, job)
    snapshot = (
        run.llm_runtime_snapshot if isinstance(run.llm_runtime_snapshot, dict) else None
    )
    snapshot_profile_id = snapshot.get("profile_id") if snapshot is not None else None
    profile: LLMProfile | None = None
    if isinstance(snapshot_profile_id, int):
        profile = await get_active_llm_profile(session, snapshot_profile_id)
    if profile is None and job.llm_profile_id is not None:
        profile = await get_active_llm_profile(session, job.llm_profile_id)
        if profile is None:
            return None
    if profile is None:
        profile = await get_default_active_llm_profile(session)
    if profile is None:
        return None
    if snapshot is None or snapshot.get("profile_id") != profile.id:
        source: LLMProfileSource = (
            "job" if job.llm_profile_id is not None else "global_default"
        )
        if job.llm_profile_id is None:
            job.llm_profile_id = profile.id
        snapshot = await snapshot_crawl_job_llm_profile(
            session, job, profile, source=source
        )
    return _runtime_profile(profile, snapshot)


def public_llm_context(
    snapshot: object,
    *,
    effective_models: list[str],
) -> dict[str, object] | None:
    if not isinstance(snapshot, dict):
        return None
    return {
        "profile_source": snapshot.get("profile_source"),
        "profile_id": snapshot.get("profile_id"),
        "profile_revision": snapshot.get("profile_revision"),
        "profile_name": snapshot.get("profile_name"),
        "provider": snapshot.get("provider"),
        "model_name": snapshot.get("model_name"),
        "effective_models": effective_models,
    }


def _snapshot_for_profile(
    profile: LLMProfile,
    *,
    source: LLMProfileSource,
) -> dict[str, object]:
    runtime_values = {
        "profile_id": profile.id,
        "profile_name": profile.name,
        "provider": profile.provider,
        "api_base_url": profile.api_base_url,
        "model_name": profile.model_name,
        "matcher_prompt_template": profile.matcher_prompt_template,
        "writer_prompt_template": profile.writer_prompt_template,
        "temperature": profile.temperature,
        "max_tokens": profile.max_tokens,
    }
    return {
        **runtime_values,
        "profile_source": source,
        "profile_revision": revision_for(runtime_values),
    }


def _runtime_profile(
    profile: LLMProfile,
    snapshot: dict[str, object],
) -> CrawlLLMRuntimeProfile:
    return CrawlLLMRuntimeProfile(
        id=profile.id,
        name=str(snapshot.get("profile_name") or profile.name),
        provider=str(snapshot.get("provider") or profile.provider),
        api_base_url=_optional_string(snapshot.get("api_base_url")),
        api_key=profile.api_key,
        model_name=str(snapshot.get("model_name") or profile.model_name),
        matcher_prompt_template=_optional_string(
            snapshot.get("matcher_prompt_template")
        ),
        writer_prompt_template=_optional_string(snapshot.get("writer_prompt_template")),
        temperature=_optional_float(snapshot.get("temperature")),
        max_tokens=_optional_int(snapshot.get("max_tokens")),
    )


def _optional_string(value: object) -> str | None:
    return value if isinstance(value, str) else None


def _optional_float(value: object) -> float | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    return None


def _optional_int(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None
