from __future__ import annotations

from contextlib import nullcontext
from urllib.parse import urlsplit, urlunsplit

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_async_session
from app.core.time import utc_now
from app.models import LLMProfile
from .schemas import (
    LLMProfileCreate,
    LLMProfileDeletionImpact,
    LLMProfileDeletionResult,
    LLMProfileModelsResult,
    LLMProfileRead,
    LLMProfileTestResult,
    LLMProfileUpdate,
)
from .availability import get_active_llm_profile
from .deletion import (
    LLMProfileDeletionError,
    build_llm_profile_deletion_impact,
    retire_llm_profile,
)
from .usage import (
    LLMProfileRetiringError,
    end_llm_profile_retirement,
    track_llm_profile_usage,
)
from .runtime import (
    LLMProbeResult,
    LLMRuntimeError,
    ensure_llm_runtime_adaptation,
    fetch_llm_profile_models,
    probe_llm_profile,
    resolve_base_url,
)
from app.services.operation_logs import record_operation_log
from .adaptation.thinking import ThinkingAdaptationFailed


router = APIRouter(prefix="/api/llm-profiles", tags=["llm-profiles"])


@router.get("", response_model=list[LLMProfileRead])
async def list_llm_profiles(
    session: AsyncSession = Depends(get_async_session),
) -> list[LLMProfile]:
    result = await session.execute(
        select(LLMProfile)
        .where(LLMProfile.deleted_at.is_(None))
        .order_by(
            LLMProfile.is_default.desc(), LLMProfile.created_at.desc()
        ),
    )
    return list(result.scalars())


@router.post("", response_model=LLMProfileRead, status_code=status.HTTP_201_CREATED)
async def create_llm_profile(
    payload: LLMProfileCreate,
    session: AsyncSession = Depends(get_async_session),
) -> LLMProfile:
    existing_count = await session.scalar(
        select(func.count(LLMProfile.id)).where(LLMProfile.deleted_at.is_(None))
    )
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


@router.get(
    "/{profile_id}/deletion-impact",
    response_model=LLMProfileDeletionImpact,
)
async def get_llm_profile_deletion_impact(
    profile_id: int,
    session: AsyncSession = Depends(get_async_session),
) -> LLMProfileDeletionImpact:
    profile = await _get_profile(session, profile_id)
    return await build_llm_profile_deletion_impact(session, profile)


@router.delete("/{profile_id}", response_model=LLMProfileDeletionResult)
async def delete_llm_profile(
    profile_id: int,
    impact_revision: str = Query(..., min_length=64, max_length=64),
    replacement_default_profile_id: int | None = Query(default=None, ge=1),
    session: AsyncSession = Depends(get_async_session),
) -> LLMProfileDeletionResult:
    profile = await _get_profile(session, profile_id)
    replacement_default_profile = None
    if replacement_default_profile_id is not None:
        replacement_default_profile = await get_active_llm_profile(
            session,
            replacement_default_profile_id,
        )
        if replacement_default_profile is None:
            raise HTTPException(
                status_code=422,
                detail={
                    "code": "LLM_PROFILE_DEFAULT_REPLACEMENT_INVALID",
                    "message": "默认模型替代项不存在或已删除，请重新选择。",
                },
            )
        if not profile.is_default:
            raise HTTPException(
                status_code=422,
                detail={
                    "code": "LLM_PROFILE_DEFAULT_REPLACEMENT_NOT_NEEDED",
                    "message": "仅删除当前默认模型时可以指定默认替代项。",
                },
            )
    retirement_started = False
    try:
        replacement_usage = (
            track_llm_profile_usage(
                replacement_default_profile.id,
                "default_replacement",
            )
            if replacement_default_profile is not None
            else nullcontext()
        )
        with replacement_usage:
            try:
                result = await retire_llm_profile(
                    session,
                    profile,
                    expected_revision=impact_revision,
                    replacement_default_profile=replacement_default_profile,
                )
                retirement_started = True
            except LLMProfileDeletionError as exc:
                await session.rollback()
                raise HTTPException(
                    status_code=409,
                    detail={
                        "code": exc.code,
                        "message": exc.message,
                        "impact": exc.impact.model_dump(mode="json"),
                    },
                ) from exc
            await _record_llm_profile_log(
                session,
                profile,
                "llm_profile.deleted",
                metadata={
                    "references_preserved": result.references_preserved.model_dump(
                        mode="json"
                    ),
                    "invalidated_plan_count": result.invalidated_plan_count,
                    "default_profile_id": result.default_profile_id,
                },
            )
            await session.commit()
            return result
    except LLMProfileRetiringError as exc:
        await session.rollback()
        raise HTTPException(
            status_code=409,
            detail={
                "code": "LLM_PROFILE_DEFAULT_REPLACEMENT_UNAVAILABLE",
                "message": "所选默认替代模型正在被删除，请重新查看并选择可用模型。",
            },
        ) from exc
    except Exception:
        await session.rollback()
        raise
    finally:
        if retirement_started:
            end_llm_profile_retirement(profile.id)


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
    try:
        adaptation = await ensure_llm_runtime_adaptation(session, profile)
    except (LLMRuntimeError, ThinkingAdaptationFailed) as exc:
        result = _build_adaptation_failure_probe_result(profile, exc)
    else:
        result = await probe_llm_profile(
            profile,
            session=session,
            adaptation=adaptation,
        )
    if result.ok:
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


@router.get("/{profile_id}/models", response_model=LLMProfileModelsResult)
async def fetch_models_for_llm_profile(
    profile_id: int,
    session: AsyncSession = Depends(get_async_session),
) -> LLMProfileModelsResult:
    profile = await _get_profile(session, profile_id)
    try:
        with track_llm_profile_usage(profile.id, "model_listing"):
            result = await fetch_llm_profile_models(profile)
    except LLMProfileRetiringError as exc:
        raise _llm_profile_retiring_http_error() from exc
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
            "resolved_base_url": _strip_url_query_and_fragment(
                result.resolved_base_url
            ),
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
    try:
        with track_llm_profile_usage(profile.id, "connectivity_test"):
            try:
                adaptation = await ensure_llm_runtime_adaptation(session, profile)
            except (LLMRuntimeError, ThinkingAdaptationFailed) as exc:
                result = _build_adaptation_failure_probe_result(profile, exc)
            else:
                result = await probe_llm_profile(
                    profile,
                    session=session,
                    adaptation=adaptation,
                )
    except LLMProfileRetiringError as exc:
        raise _llm_profile_retiring_http_error() from exc
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
            "resolved_base_url": _strip_url_query_and_fragment(
                result.resolved_base_url
            ),
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
    profile = await get_active_llm_profile(session, profile_id)
    if not profile:
        raise HTTPException(status_code=404, detail="未找到 LLM 配置")
    return profile


def _llm_profile_retiring_http_error() -> HTTPException:
    return HTTPException(
        status_code=409,
        detail={
            "code": "LLM_PROFILE_RETIRING",
            "message": "模型配置正在删除，请刷新后选择其他模型。",
        },
    )


async def _clear_default_profiles(
    session: AsyncSession,
    exclude_id: int | None = None,
) -> None:
    result = await session.execute(
        select(LLMProfile).where(LLMProfile.deleted_at.is_(None))
    )
    for profile in result.scalars():
        if exclude_id is not None and profile.id == exclude_id:
            continue
        profile.is_default = False
        profile.updated_at = utc_now()


def _build_adaptation_failure_probe_result(
    profile: LLMProfile,
    exc: LLMRuntimeError | ThinkingAdaptationFailed,
) -> LLMProbeResult:
    runtime_error = exc.last_error if isinstance(exc, ThinkingAdaptationFailed) else exc
    if runtime_error is not None:
        return LLMProbeResult(
            ok=False,
            message=str(runtime_error),
            resolved_base_url=resolve_base_url(profile.api_base_url),
            request_url=runtime_error.request_url,
            attempted_urls=runtime_error.attempted_urls,
            endpoint_kind=runtime_error.endpoint_kind,
            status_code=runtime_error.status_code,
            duration_ms=runtime_error.duration_ms,
            consumes_tokens=True,
            response_preview=None,
        )
    return LLMProbeResult(
        ok=False,
        message=str(exc),
        resolved_base_url=resolve_base_url(profile.api_base_url),
        consumes_tokens=True,
        response_preview=None,
    )


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
    hostname = parsed.hostname
    if hostname is None:
        netloc = parsed.netloc.rsplit("@", 1)[-1]
    else:
        netloc = f"[{hostname}]" if ":" in hostname else hostname
        try:
            port = parsed.port
        except ValueError:
            port = None
        if port is not None:
            netloc = f"{netloc}:{port}"
    return urlunsplit((parsed.scheme, netloc, parsed.path, "", ""))


def _strip_url_list_query_and_fragment(urls: list[str]) -> list[str]:
    return [
        sanitized
        for url in urls
        if (sanitized := _strip_url_query_and_fragment(url)) is not None
    ]


__all__ = [
    "create_llm_profile",
    "delete_llm_profile",
    "fetch_models_for_llm_profile",
    "list_llm_profiles",
    "preview_llm_profile_models",
    "preview_llm_profile_test",
    "router",
    "set_default_llm_profile",
    "test_llm_profile",
    "update_llm_profile",
]
