from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.agent_api_errors import AgentApiError
from app.core.agent_revisions import ensure_revision, revision_for
from app.core.database import get_async_session, get_session_factory
from app.core.time import utc_now
from app.models import LLMProfile
from app.modules.llm.public import (
    LLMModelCatalogResult,
    LLMProbeResult,
    LLMProfileRetiringError,
    LLMRuntimeError,
    ThinkingAdaptationFailed,
    ensure_llm_runtime_adaptation,
    fetch_llm_profile_models,
    get_active_llm_profile,
    probe_llm_profile,
    resolve_base_url,
    sanitize_llm_url,
    set_default_llm_profile_record,
    track_llm_profile_usage,
)
from app.schemas.agent import (
    AgentLLMProfileModelsRead,
    AgentLLMProfileRead,
    AgentLLMProfileSettingsUpdateRequest,
    AgentLLMProfileTestRead,
    AgentPage,
)
from app.services.agent_mutations import (
    execute_agent_factory_mutation,
    execute_agent_mutation,
)
from app.services.operation_logs import (
    record_operation_log,
    sanitize_user_visible_error,
)

from .support import (
    _project_agent_collection_response,
    _slice_page,
)

router = APIRouter()


@router.get("/llm-profiles", response_model=AgentPage[AgentLLMProfileRead])
async def list_agent_llm_profiles(
    profile_id: int | None = Query(default=None, ge=1),
    provider: str | None = Query(default=None, max_length=100),
    model_name: str | None = Query(default=None, max_length=200),
    is_default: bool | None = Query(default=None),
    fields: str | None = Query(default=None, max_length=4_000),
    cursor: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=500),
    session: AsyncSession = Depends(get_async_session),
) -> AgentPage[AgentLLMProfileRead] | Response:
    statement = select(LLMProfile).where(LLMProfile.deleted_at.is_(None))
    if profile_id is not None:
        statement = statement.where(LLMProfile.id == profile_id)
    if provider is not None:
        statement = statement.where(LLMProfile.provider == provider)
    if model_name is not None:
        statement = statement.where(LLMProfile.model_name == model_name)
    if is_default is not None:
        statement = statement.where(LLMProfile.is_default.is_(is_default))
    profiles = list(
        await session.scalars(
            statement.order_by(LLMProfile.is_default.desc(), LLMProfile.id.asc())
            .offset(cursor)
            .limit(limit + 1),
        ),
    )
    page, next_cursor, has_more = _slice_page(profiles, cursor=cursor, limit=limit)
    response = AgentPage(
        items=[_serialize_llm_profile(profile) for profile in page],
        next_cursor=next_cursor,
        has_more=has_more,
    )
    return _project_agent_collection_response(response, fields)


@router.get("/llm-profiles/{profile_id}", response_model=AgentLLMProfileRead)
async def read_agent_llm_profile(
    profile_id: int,
    session: AsyncSession = Depends(get_async_session),
) -> AgentLLMProfileRead:
    profile = await get_active_llm_profile(session, profile_id)
    if profile is None:
        raise HTTPException(status_code=404, detail="未找到 LLM 配置")
    return _serialize_llm_profile(profile)


@router.put(
    "/llm-profiles/{profile_id}/settings",
    response_model=AgentLLMProfileRead,
)
async def update_agent_llm_profile_settings(
    profile_id: int,
    payload: AgentLLMProfileSettingsUpdateRequest,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    if_revision: str | None = Header(default=None, alias="If-Revision"),
    session: AsyncSession = Depends(get_async_session),
) -> AgentLLMProfileRead:
    request_data = {
        "profile_id": profile_id,
        "if_revision": if_revision,
        **payload.model_dump(mode="json", exclude_unset=True),
    }
    try:
        return await execute_agent_mutation(
            session,
            command="llm-profiles.update-settings",
            request_data=request_data,
            idempotency_key=idempotency_key,
            response_type=AgentLLMProfileRead,
            mutation=lambda: _update_agent_llm_profile_settings_with_revision(
                session,
                profile_id,
                payload,
                if_revision=if_revision,
            ),
        )
    except ValueError as exc:
        raise _agent_llm_profile_error(exc) from exc


@router.post(
    "/llm-profiles/{profile_id}/default",
    response_model=AgentLLMProfileRead,
)
async def set_agent_default_llm_profile(
    profile_id: int,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    if_revision: str | None = Header(default=None, alias="If-Revision"),
    session: AsyncSession = Depends(get_async_session),
) -> AgentLLMProfileRead:
    try:
        return await execute_agent_mutation(
            session,
            command="llm-profiles.set-default",
            request_data={"profile_id": profile_id, "if_revision": if_revision},
            idempotency_key=idempotency_key,
            response_type=AgentLLMProfileRead,
            mutation=lambda: _set_agent_default_llm_profile_with_revision(
                session,
                profile_id,
                if_revision=if_revision,
            ),
        )
    except ValueError as exc:
        raise _agent_llm_profile_error(exc) from exc


@router.get(
    "/llm-profiles/{profile_id}/models",
    response_model=AgentLLMProfileModelsRead,
)
async def fetch_agent_llm_profile_models(
    profile_id: int,
    session: AsyncSession = Depends(get_async_session),
) -> AgentLLMProfileModelsRead:
    try:
        profile = await _get_agent_llm_profile_or_raise(session, profile_id)
    except ValueError as exc:
        raise _agent_llm_profile_error(exc) from exc
    try:
        with track_llm_profile_usage(profile.id, "model_listing"):
            result = await fetch_llm_profile_models(profile)
    except LLMProfileRetiringError as exc:
        raise _agent_llm_profile_retiring_error() from exc
    await _record_agent_llm_profile_event(
        session,
        profile,
        "agent_cli.llm_profile.models_fetched",
        level="info" if result.ok else "warning",
        metadata={
            "ok": result.ok,
            "result": "ok" if result.ok else "failed",
            "status_code": result.status_code,
            "duration_ms": result.duration_ms,
            "endpoint_kind": result.endpoint_kind,
            "model_count": len(result.models),
            "selected_model_available": result.selected_model_available,
        },
    )
    await session.commit()
    return _serialize_agent_llm_profile_models(profile.id, result)


@router.post(
    "/llm-profiles/{profile_id}/test",
    response_model=AgentLLMProfileTestRead,
)
async def test_agent_llm_profile(
    profile_id: int,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    session: AsyncSession = Depends(get_async_session),
) -> AgentLLMProfileTestRead:
    try:

        async def mutation() -> AgentLLMProfileTestRead:
            async with get_session_factory()() as mutation_session:
                result = await _test_agent_llm_profile(mutation_session, profile_id)
                await mutation_session.commit()
                return result

        return await execute_agent_factory_mutation(
            get_session_factory(),
            command="llm-profiles.test",
            request_data={"profile_id": profile_id},
            idempotency_key=idempotency_key,
            response_type=AgentLLMProfileTestRead,
            mutation=mutation,
            external_execution=True,
        )
    except LLMProfileRetiringError as exc:
        raise _agent_llm_profile_retiring_error() from exc
    except ValueError as exc:
        raise _agent_llm_profile_error(exc) from exc


async def _set_agent_default_llm_profile(
    session: AsyncSession,
    profile_id: int,
) -> AgentLLMProfileRead:
    profile = await _get_agent_llm_profile_or_raise(session, profile_id)
    await set_default_llm_profile_record(session, profile)
    await _record_agent_llm_profile_event(
        session,
        profile,
        "agent_cli.llm_profile.default_set",
    )
    return _serialize_llm_profile(profile)


async def _set_agent_default_llm_profile_with_revision(
    session: AsyncSession,
    profile_id: int,
    *,
    if_revision: str | None,
) -> AgentLLMProfileRead:
    await _ensure_llm_profile_revision(session, profile_id, if_revision)
    return await _set_agent_default_llm_profile(session, profile_id)


async def _update_agent_llm_profile_settings(
    session: AsyncSession,
    profile_id: int,
    payload: AgentLLMProfileSettingsUpdateRequest,
) -> AgentLLMProfileRead:
    profile = await _get_agent_llm_profile_or_raise(session, profile_id)
    updates = payload.model_dump(exclude_unset=True)
    if "name" in updates:
        profile.name = str(updates["name"]).strip()
    if "model_name" in updates:
        profile.model_name = str(updates["model_name"]).strip()
    if "temperature" in updates:
        profile.temperature = updates["temperature"]
    if "max_tokens" in updates:
        profile.max_tokens = updates["max_tokens"]
    profile.updated_at = utc_now()
    await record_operation_log(
        session,
        category="user_action",
        event_name="agent_cli.llm_profile.settings_updated",
        level="info",
        entity_type="llm_profile",
        entity_id=str(profile.id),
        metadata={
            "changed_fields": sorted(updates),
            "actor": "agent_cli",
        },
    )
    return _serialize_llm_profile(profile)


async def _update_agent_llm_profile_settings_with_revision(
    session: AsyncSession,
    profile_id: int,
    payload: AgentLLMProfileSettingsUpdateRequest,
    *,
    if_revision: str | None,
) -> AgentLLMProfileRead:
    await _ensure_llm_profile_revision(session, profile_id, if_revision)
    return await _update_agent_llm_profile_settings(session, profile_id, payload)


async def _test_agent_llm_profile(
    session: AsyncSession,
    profile_id: int,
) -> AgentLLMProfileTestRead:
    profile = await _get_agent_llm_profile_or_raise(session, profile_id)
    with track_llm_profile_usage(profile.id, "connectivity_test"):
        try:
            adaptation = await ensure_llm_runtime_adaptation(session, profile)
        except (LLMRuntimeError, ThinkingAdaptationFailed) as exc:
            result = _build_agent_llm_adaptation_failure_probe_result(profile, exc)
        else:
            result = await probe_llm_profile(
                profile,
                session=session,
                adaptation=adaptation,
            )
    await _record_agent_llm_profile_event(
        session,
        profile,
        "agent_cli.llm_profile.tested",
        level="info" if result.ok else "warning",
        metadata={
            "ok": result.ok,
            "result": "ok" if result.ok else "failed",
            "status_code": result.status_code,
            "duration_ms": result.duration_ms,
            "endpoint_kind": result.endpoint_kind,
            "consumes_tokens": result.consumes_tokens,
        },
    )
    return _serialize_agent_llm_profile_test(profile.id, result)


async def _get_agent_llm_profile_or_raise(
    session: AsyncSession,
    profile_id: int,
) -> LLMProfile:
    profile = await get_active_llm_profile(session, profile_id)
    if profile is None:
        raise ValueError("未找到 LLM 配置")
    return profile


async def _record_agent_llm_profile_event(
    session: AsyncSession,
    profile: LLMProfile,
    event_name: str,
    *,
    level: str = "info",
    metadata: dict[str, object] | None = None,
) -> None:
    event_metadata: dict[str, object] = {
        "id": profile.id,
        "name": profile.name,
        "provider": profile.provider,
        "model_name": profile.model_name,
        "is_default": profile.is_default,
        "actor": "agent_cli",
    }
    if metadata:
        event_metadata.update(metadata)
    await record_operation_log(
        session,
        category="user_action",
        event_name=event_name,
        level=level,
        entity_type="llm_profile",
        entity_id=str(profile.id),
        metadata=event_metadata,
    )


def _build_agent_llm_adaptation_failure_probe_result(
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
        )
    return LLMProbeResult(
        ok=False,
        message=str(exc),
        resolved_base_url=resolve_base_url(profile.api_base_url),
        consumes_tokens=True,
    )


def _serialize_agent_llm_profile_models(
    profile_id: int,
    result: LLMModelCatalogResult,
) -> AgentLLMProfileModelsRead:
    return AgentLLMProfileModelsRead(
        profile_id=profile_id,
        ok=result.ok,
        message=sanitize_user_visible_error(result.message),
        resolved_base_url=sanitize_llm_url(result.resolved_base_url),
        request_url=sanitize_llm_url(result.request_url),
        attempted_urls=_sanitize_agent_llm_urls(result.attempted_urls),
        endpoint_kind=result.endpoint_kind,
        status_code=result.status_code,
        duration_ms=result.duration_ms,
        consumes_tokens=result.consumes_tokens,
        models=result.models,
        selected_model_available=result.selected_model_available,
    )


def _serialize_agent_llm_profile_test(
    profile_id: int,
    result: LLMProbeResult,
) -> AgentLLMProfileTestRead:
    return AgentLLMProfileTestRead(
        profile_id=profile_id,
        ok=result.ok,
        message=sanitize_user_visible_error(result.message),
        resolved_base_url=sanitize_llm_url(result.resolved_base_url),
        request_url=sanitize_llm_url(result.request_url),
        attempted_urls=_sanitize_agent_llm_urls(result.attempted_urls),
        endpoint_kind=result.endpoint_kind,
        status_code=result.status_code,
        duration_ms=result.duration_ms,
        consumes_tokens=result.consumes_tokens,
        prompt_tokens=result.prompt_tokens,
        completion_tokens=result.completion_tokens,
        total_tokens=result.total_tokens,
    )


def _sanitize_agent_llm_urls(urls: list[str]) -> list[str]:
    return [
        sanitized for url in urls if (sanitized := sanitize_llm_url(url)) is not None
    ]


def _agent_llm_profile_error(error: ValueError) -> AgentApiError:
    return AgentApiError(
        status_code=404 if "未找到" in str(error) else 422,
        code="LLM_PROFILE_OPERATION_REJECTED",
        message=str(error),
    )


def _agent_llm_profile_retiring_error() -> AgentApiError:
    return AgentApiError(
        status_code=409,
        code="LLM_PROFILE_RETIRING",
        message="模型配置正在删除，请刷新后选择其他模型。",
        retryable=True,
    )


def _serialize_llm_profile(profile: LLMProfile) -> AgentLLMProfileRead:
    result = AgentLLMProfileRead(
        id=profile.id,
        name=profile.name,
        provider=profile.provider,
        model_name=profile.model_name,
        temperature=profile.temperature,
        max_tokens=profile.max_tokens,
        credential_configured=bool(profile.api_key),
        is_default=profile.is_default,
        created_at=profile.created_at,
        updated_at=profile.updated_at,
    )
    return result.model_copy(update={"revision": revision_for(result)})


async def _ensure_llm_profile_revision(
    session: AsyncSession,
    profile_id: int,
    if_revision: str | None,
) -> None:
    if not if_revision:
        return
    profile = await _get_agent_llm_profile_or_raise(session, profile_id)
    current = _serialize_llm_profile(profile)
    ensure_revision(
        if_revision,
        current.revision,
        resource="llm-profiles",
        resource_id=profile_id,
        latest=current.model_dump(mode="json"),
    )
