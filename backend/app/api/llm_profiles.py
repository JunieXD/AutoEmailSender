from __future__ import annotations

from datetime import UTC, datetime

from app.core.time import utc_now

from urllib.parse import urlsplit, urlunsplit

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_async_session
from app.models import LLMProfile
from app.schemas.llm_profile import (
    LLMProfileCreate,
    LLMProfileModelsResult,
    LLMProfileRead,
    LLMProfileTestResult,
    LLMProfileUpdate,
)
from app.services.llm_runtime import fetch_llm_profile_models, probe_llm_profile
from app.services.operation_logs import record_operation_log
from app.services.thinking_adaptation import ensure_thinking_adaptation


router = APIRouter(prefix="/api/llm-profiles", tags=["llm-profiles"])


@router.get("", response_model=list[LLMProfileRead])
async def list_llm_profiles(
    session: AsyncSession = Depends(get_async_session),
) -> list[LLMProfile]:
    result = await session.execute(
        select(LLMProfile).order_by(LLMProfile.is_default.desc(), LLMProfile.created_at.desc()),
    )
    return list(result.scalars())


@router.post("", response_model=LLMProfileRead, status_code=status.HTTP_201_CREATED)
async def create_llm_profile(
    payload: LLMProfileCreate,
    session: AsyncSession = Depends(get_async_session),
) -> LLMProfile:
    existing_count = await session.scalar(select(func.count(LLMProfile.id)))
    profile = LLMProfile(**payload.model_dump())
    if not existing_count:
        profile.is_default = True
    elif payload.is_default:
        await _clear_default_profiles(session)

    session.add(profile)
    await session.flush()
    await _record_llm_profile_log(session, profile, "llm_profile.created")
    await session.commit()
    await session.refresh(profile)
    return profile


@router.put("/{profile_id}", response_model=LLMProfileRead)
async def update_llm_profile(
    profile_id: int,
    payload: LLMProfileUpdate,
    session: AsyncSession = Depends(get_async_session),
) -> LLMProfile:
    profile = await _get_profile(session, profile_id)
    data = payload.model_dump()
    if data["is_default"]:
        await _clear_default_profiles(session, exclude_id=profile_id)

    for key, value in data.items():
        setattr(profile, key, value)
    profile.updated_at = utc_now()

    await _record_llm_profile_log(session, profile, "llm_profile.updated")
    await session.commit()
    await session.refresh(profile)
    return profile


@router.delete("/{profile_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_llm_profile(
    profile_id: int,
    session: AsyncSession = Depends(get_async_session),
) -> None:
    profile = await _get_profile(session, profile_id)
    was_default = profile.is_default
    await _record_llm_profile_log(
        session,
        profile,
        "llm_profile.deleted",
        metadata={"was_default": was_default},
    )
    await session.delete(profile)
    await session.commit()

    if was_default:
        remaining = await session.scalar(
            select(LLMProfile).order_by(LLMProfile.created_at.asc()).limit(1),
        )
        if remaining:
            remaining.is_default = True
            remaining.updated_at = utc_now()
            await session.commit()


@router.post("/{profile_id}/default", response_model=LLMProfileRead)
async def set_default_llm_profile(
    profile_id: int,
    session: AsyncSession = Depends(get_async_session),
) -> LLMProfile:
    profile = await _get_profile(session, profile_id)
    await _clear_default_profiles(session, exclude_id=profile_id)
    profile.is_default = True
    profile.updated_at = utc_now()
    await _record_llm_profile_log(session, profile, "llm_profile.default_set")
    await session.commit()
    await session.refresh(profile)
    return profile


@router.post("/preview/models", response_model=LLMProfileModelsResult)
async def preview_llm_profile_models(
    payload: LLMProfileCreate,
) -> LLMProfileModelsResult:
    profile = LLMProfile(**payload.model_dump())
    result = await fetch_llm_profile_models(profile)
    return LLMProfileModelsResult(
        ok=result.ok,
        message=result.message,
        resolved_base_url=result.resolved_base_url,
        request_url=result.request_url,
        attempted_urls=result.attempted_urls,
        endpoint_kind=result.endpoint_kind,
        status_code=result.status_code,
        duration_ms=result.duration_ms,
        consumes_tokens=result.consumes_tokens,
        models=result.models,
        selected_model_available=result.selected_model_available,
    )


@router.post("/preview/test", response_model=LLMProfileTestResult)
async def preview_llm_profile_test(
    payload: LLMProfileCreate,
    session: AsyncSession = Depends(get_async_session),
) -> LLMProfileTestResult:
    profile = LLMProfile(**payload.model_dump())
    thinking_extra_body = await ensure_thinking_adaptation(session, profile)
    result = await probe_llm_profile(
        profile,
        thinking_extra_body=thinking_extra_body,
    )
    return LLMProfileTestResult(
        ok=result.ok,
        message=result.message,
        resolved_base_url=result.resolved_base_url,
        request_url=result.request_url,
        attempted_urls=result.attempted_urls,
        endpoint_kind=result.endpoint_kind,
        status_code=result.status_code,
        duration_ms=result.duration_ms,
        consumes_tokens=result.consumes_tokens,
        prompt_tokens=result.prompt_tokens,
        completion_tokens=result.completion_tokens,
        total_tokens=result.total_tokens,
        response_preview=result.response_preview,
    )


@router.get("/{profile_id}/models", response_model=LLMProfileModelsResult)
async def fetch_models_for_llm_profile(
    profile_id: int,
    session: AsyncSession = Depends(get_async_session),
) -> LLMProfileModelsResult:
    profile = await _get_profile(session, profile_id)
    result = await fetch_llm_profile_models(profile)
    await _record_llm_profile_log(
        session,
        profile,
        "llm_profile.models_fetched",
        level="info" if result.ok else "warning",
        metadata={
            "ok": result.ok,
            "result": "ok" if result.ok else "failed",
            "status_code": result.status_code,
            "duration_ms": result.duration_ms,
            "endpoint_kind": result.endpoint_kind,
            "resolved_base_url": _strip_url_query_and_fragment(result.resolved_base_url),
            "request_url": _strip_url_query_and_fragment(result.request_url),
            "attempted_urls": _strip_url_list_query_and_fragment(result.attempted_urls),
            "model_count": len(result.models),
            "selected_model_available": result.selected_model_available,
        },
    )
    await session.commit()
    return LLMProfileModelsResult(
        ok=result.ok,
        message=result.message,
        resolved_base_url=result.resolved_base_url,
        request_url=result.request_url,
        attempted_urls=result.attempted_urls,
        endpoint_kind=result.endpoint_kind,
        status_code=result.status_code,
        duration_ms=result.duration_ms,
        consumes_tokens=result.consumes_tokens,
        models=result.models,
        selected_model_available=result.selected_model_available,
    )


@router.post("/{profile_id}/test", response_model=LLMProfileTestResult)
async def test_llm_profile(
    profile_id: int,
    session: AsyncSession = Depends(get_async_session),
) -> LLMProfileTestResult:
    profile = await _get_profile(session, profile_id)
    thinking_extra_body = await ensure_thinking_adaptation(session, profile)
    result = await probe_llm_profile(
        profile,
        session=session,
        thinking_extra_body=thinking_extra_body,
    )
    await _record_llm_profile_log(
        session,
        profile,
        "llm_profile.tested",
        level="info" if result.ok else "warning",
        metadata={
            "ok": result.ok,
            "result": "ok" if result.ok else "failed",
            "status_code": result.status_code,
            "duration_ms": result.duration_ms,
            "endpoint_kind": result.endpoint_kind,
            "resolved_base_url": _strip_url_query_and_fragment(result.resolved_base_url),
            "request_url": _strip_url_query_and_fragment(result.request_url),
            "attempted_urls": _strip_url_list_query_and_fragment(result.attempted_urls),
            "consumes_tokens": result.consumes_tokens,
        },
    )
    await session.commit()
    return LLMProfileTestResult(
        ok=result.ok,
        message=result.message,
        resolved_base_url=result.resolved_base_url,
        request_url=result.request_url,
        attempted_urls=result.attempted_urls,
        endpoint_kind=result.endpoint_kind,
        status_code=result.status_code,
        duration_ms=result.duration_ms,
        consumes_tokens=result.consumes_tokens,
        prompt_tokens=result.prompt_tokens,
        completion_tokens=result.completion_tokens,
        total_tokens=result.total_tokens,
        response_preview=result.response_preview,
    )


async def _get_profile(session: AsyncSession, profile_id: int) -> LLMProfile:
    profile = await session.get(LLMProfile, profile_id)
    if not profile:
        raise HTTPException(status_code=404, detail="未找到 LLM 配置")
    return profile


async def _clear_default_profiles(
    session: AsyncSession,
    exclude_id: int | None = None,
) -> None:
    result = await session.execute(select(LLMProfile))
    for profile in result.scalars():
        if exclude_id is not None and profile.id == exclude_id:
            continue
        profile.is_default = False
        profile.updated_at = utc_now()


async def _record_llm_profile_log(
    session: AsyncSession,
    profile: LLMProfile,
    event_name: str,
    *,
    level: str = "info",
    metadata: dict[str, object] | None = None,
) -> None:
    base_metadata: dict[str, object] = {
        "id": profile.id,
        "name": profile.name,
        "provider": profile.provider,
        "model_name": profile.model_name,
        "api_base_url": _strip_url_query_and_fragment(profile.api_base_url),
        "is_default": profile.is_default,
    }
    if metadata:
        base_metadata.update(metadata)
    await record_operation_log(
        session,
        category="user_action",
        event_name=event_name,
        level=level,
        entity_type="llm_profile",
        entity_id=str(profile.id),
        metadata=base_metadata,
    )


def _strip_url_query_and_fragment(url: str | None) -> str | None:
    if url is None:
        return None
    parsed = urlsplit(url)
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))


def _strip_url_list_query_and_fragment(urls: list[str]) -> list[str]:
    return [
        sanitized
        for url in urls
        if (sanitized := _strip_url_query_and_fragment(url)) is not None
    ]
