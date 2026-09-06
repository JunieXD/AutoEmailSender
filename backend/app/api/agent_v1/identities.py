from __future__ import annotations

from time import perf_counter

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from fastapi.responses import Response
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.agent_api_errors import AgentApiError
from app.core.agent_revisions import ensure_revision, revision_for
from app.core.database import get_async_session, get_session_factory
from app.core.time import utc_now
from app.models import IdentityProfile
from app.modules.campaigns.public import (
    IdentityDefaultOutreachTemplateUpdate,
    apply_template_to_identity_legacy_fields,
    clear_identity_default_template,
    get_outreach_template,
)
from app.modules.communications.public import (
    explain_smtp_error,
    test_imap_connection,
    test_smtp_connection,
)
from app.modules.identities.public import (
    ConnectionTestResult,
    set_default_identity_record,
)
from app.schemas.agent import (
    AgentIdentityRead,
    AgentIdentitySettingsUpdateRequest,
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
    _identity_has_imap_config,
    _project_agent_collection_response,
    _slice_page,
)

router = APIRouter()


@router.get("/identities", response_model=AgentPage[AgentIdentityRead])
async def list_agent_identities(
    identity_id: int | None = Query(default=None, ge=1),
    is_default: bool | None = Query(default=None),
    smtp_configured: bool | None = Query(default=None),
    imap_configured: bool | None = Query(default=None),
    fields: str | None = Query(default=None, max_length=4_000),
    cursor: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=500),
    session: AsyncSession = Depends(get_async_session),
) -> AgentPage[AgentIdentityRead] | Response:
    statement = select(IdentityProfile).where(IdentityProfile.deleted_at.is_(None))
    if identity_id is not None:
        statement = statement.where(IdentityProfile.id == identity_id)
    if is_default is not None:
        statement = statement.where(IdentityProfile.is_default.is_(is_default))
    smtp_predicate = IdentityProfile.smtp_host != ""
    smtp_predicate = smtp_predicate & (IdentityProfile.smtp_username != "")
    smtp_predicate = smtp_predicate & (IdentityProfile.smtp_password != "")
    if smtp_configured is not None:
        statement = statement.where(
            smtp_predicate if smtp_configured else ~smtp_predicate
        )
    if imap_configured is not None:
        predicate = (
            func.coalesce(
                func.trim(IdentityProfile.imap_host),
                "",
            )
            != ""
        )
        predicate = predicate & (IdentityProfile.imap_port > 0)
        predicate = predicate & (
            func.coalesce(func.trim(IdentityProfile.imap_username), "") != ""
        )
        predicate = predicate & (func.coalesce(IdentityProfile.imap_password, "") != "")
        statement = statement.where(predicate if imap_configured else ~predicate)
    identities = list(
        await session.scalars(
            statement.order_by(
                IdentityProfile.is_default.desc(), IdentityProfile.id.asc()
            )
            .offset(cursor)
            .limit(limit + 1),
        ),
    )
    page, next_cursor, has_more = _slice_page(identities, cursor=cursor, limit=limit)
    response = AgentPage(
        items=[_serialize_identity(identity) for identity in page],
        next_cursor=next_cursor,
        has_more=has_more,
    )
    return _project_agent_collection_response(response, fields)


@router.get("/identities/{identity_id}", response_model=AgentIdentityRead)
async def read_agent_identity(
    identity_id: int,
    session: AsyncSession = Depends(get_async_session),
) -> AgentIdentityRead:
    identity = await session.scalar(
        select(IdentityProfile).where(
            IdentityProfile.id == identity_id,
            IdentityProfile.deleted_at.is_(None),
        )
    )
    if identity is None:
        raise HTTPException(status_code=404, detail="未找到身份配置")
    return _serialize_identity(identity)


@router.put(
    "/identities/{identity_id}/settings",
    response_model=AgentIdentityRead,
)
async def update_agent_identity_settings(
    identity_id: int,
    payload: AgentIdentitySettingsUpdateRequest,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    if_revision: str | None = Header(default=None, alias="If-Revision"),
    session: AsyncSession = Depends(get_async_session),
) -> AgentIdentityRead:
    request_data = {
        "identity_id": identity_id,
        "if_revision": if_revision,
        **payload.model_dump(mode="json", exclude_unset=True),
    }
    try:
        return await execute_agent_mutation(
            session,
            command="identities.update-settings",
            request_data=request_data,
            idempotency_key=idempotency_key,
            response_type=AgentIdentityRead,
            mutation=lambda: _update_agent_identity_settings_with_revision(
                session,
                identity_id,
                payload,
                if_revision=if_revision,
            ),
        )
    except ValueError as exc:
        raise _agent_identity_error(exc) from exc


@router.post("/identities/{identity_id}/default", response_model=AgentIdentityRead)
async def set_agent_default_identity(
    identity_id: int,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    if_revision: str | None = Header(default=None, alias="If-Revision"),
    session: AsyncSession = Depends(get_async_session),
) -> AgentIdentityRead:
    try:
        return await execute_agent_mutation(
            session,
            command="identities.set-default",
            request_data={"identity_id": identity_id, "if_revision": if_revision},
            idempotency_key=idempotency_key,
            response_type=AgentIdentityRead,
            mutation=lambda: _set_agent_default_identity_with_revision(
                session,
                identity_id,
                if_revision=if_revision,
            ),
        )
    except ValueError as exc:
        raise _agent_identity_error(exc) from exc


@router.post(
    "/identities/{identity_id}/default-template",
    response_model=AgentIdentityRead,
)
async def set_agent_identity_default_template(
    identity_id: int,
    payload: IdentityDefaultOutreachTemplateUpdate,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    if_revision: str | None = Header(default=None, alias="If-Revision"),
    session: AsyncSession = Depends(get_async_session),
) -> AgentIdentityRead:
    try:
        return await execute_agent_mutation(
            session,
            command="identities.set-default-template",
            request_data={
                "identity_id": identity_id,
                "if_revision": if_revision,
                **payload.model_dump(mode="json"),
            },
            idempotency_key=idempotency_key,
            response_type=AgentIdentityRead,
            mutation=lambda: _set_agent_identity_default_template_with_revision(
                session,
                identity_id,
                payload,
                if_revision=if_revision,
            ),
        )
    except ValueError as exc:
        raise _agent_identity_error(exc) from exc


@router.post("/identities/{identity_id}/smtp-test", response_model=ConnectionTestResult)
async def test_agent_identity_smtp(
    identity_id: int,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    session: AsyncSession = Depends(get_async_session),
) -> ConnectionTestResult:
    try:

        async def mutation() -> ConnectionTestResult:
            async with get_session_factory()() as mutation_session:
                result = await _test_agent_identity_smtp(mutation_session, identity_id)
                await mutation_session.commit()
                return result

        return await execute_agent_factory_mutation(
            get_session_factory(),
            command="identities.test-smtp",
            request_data={"identity_id": identity_id},
            idempotency_key=idempotency_key,
            response_type=ConnectionTestResult,
            mutation=mutation,
            external_execution=True,
        )
    except ValueError as exc:
        raise _agent_identity_error(exc) from exc


@router.post("/identities/{identity_id}/imap-test", response_model=ConnectionTestResult)
async def test_agent_identity_imap(
    identity_id: int,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    session: AsyncSession = Depends(get_async_session),
) -> ConnectionTestResult:
    try:

        async def mutation() -> ConnectionTestResult:
            async with get_session_factory()() as mutation_session:
                result = await _test_agent_identity_imap(mutation_session, identity_id)
                await mutation_session.commit()
                return result

        return await execute_agent_factory_mutation(
            get_session_factory(),
            command="identities.test-imap",
            request_data={"identity_id": identity_id},
            idempotency_key=idempotency_key,
            response_type=ConnectionTestResult,
            mutation=mutation,
            external_execution=True,
        )
    except ValueError as exc:
        raise _agent_identity_error(exc) from exc


async def _set_agent_default_identity(
    session: AsyncSession,
    identity_id: int,
) -> AgentIdentityRead:
    identity = await _get_agent_identity_or_raise(session, identity_id)
    await set_default_identity_record(session, identity)
    await _record_agent_identity_event(
        session,
        identity,
        "agent_cli.identity.default_set",
    )
    return _serialize_identity(identity)


async def _set_agent_default_identity_with_revision(
    session: AsyncSession,
    identity_id: int,
    *,
    if_revision: str | None,
) -> AgentIdentityRead:
    await _ensure_identity_revision(session, identity_id, if_revision)
    return await _set_agent_default_identity(session, identity_id)


async def _update_agent_identity_settings(
    session: AsyncSession,
    identity_id: int,
    payload: AgentIdentitySettingsUpdateRequest,
) -> AgentIdentityRead:
    identity = await _get_agent_identity_or_raise(session, identity_id)
    updates = payload.model_dump(exclude_unset=True)
    send_interval_min = updates.get("send_interval_min", identity.send_interval_min)
    send_interval_max = updates.get("send_interval_max", identity.send_interval_max)
    if (
        send_interval_min is not None
        and send_interval_max is not None
        and send_interval_min > send_interval_max
    ):
        raise ValueError("send_interval_min 不能大于 send_interval_max")

    if "profile_name" in updates:
        profile_name = str(updates["profile_name"]).strip()
        identity.profile_name = profile_name
        identity.name = profile_name
    if "sender_name" in updates:
        identity.sender_name = str(updates["sender_name"]).strip()
    if "default_language" in updates:
        identity.default_language = str(updates["default_language"]).strip()
    if "outreach_generation_mode" in updates:
        identity.outreach_generation_mode = str(updates["outreach_generation_mode"])
    for field_name in (
        "match_threshold",
        "daily_send_limit",
        "send_interval_min",
        "send_interval_max",
        "same_domain_cooldown_minutes",
    ):
        if field_name in updates:
            setattr(identity, field_name, updates[field_name])
    identity.updated_at = utc_now()
    await record_operation_log(
        session,
        category="user_action",
        event_name="agent_cli.identity.settings_updated",
        level="info",
        entity_type="identity",
        entity_id=str(identity.id),
        metadata={
            "changed_fields": sorted(updates),
            "actor": "agent_cli",
        },
    )
    return _serialize_identity(identity)


async def _update_agent_identity_settings_with_revision(
    session: AsyncSession,
    identity_id: int,
    payload: AgentIdentitySettingsUpdateRequest,
    *,
    if_revision: str | None,
) -> AgentIdentityRead:
    await _ensure_identity_revision(session, identity_id, if_revision)
    return await _update_agent_identity_settings(session, identity_id, payload)


async def _set_agent_identity_default_template(
    session: AsyncSession,
    identity_id: int,
    payload: IdentityDefaultOutreachTemplateUpdate,
) -> AgentIdentityRead:
    identity = await _get_agent_identity_or_raise(session, identity_id)
    if payload.template_id is None:
        clear_identity_default_template(identity)
    else:
        try:
            template = await get_outreach_template(session, payload.template_id)
        except ValueError as exc:
            raise ValueError(str(exc)) from exc
        apply_template_to_identity_legacy_fields(identity, template)
    identity.updated_at = utc_now()
    await _record_agent_identity_event(
        session,
        identity,
        "agent_cli.identity.default_outreach_template_updated",
        metadata={
            "default_outreach_template_id": identity.default_outreach_template_id
        },
    )
    return _serialize_identity(identity)


async def _set_agent_identity_default_template_with_revision(
    session: AsyncSession,
    identity_id: int,
    payload: IdentityDefaultOutreachTemplateUpdate,
    *,
    if_revision: str | None,
) -> AgentIdentityRead:
    await _ensure_identity_revision(session, identity_id, if_revision)
    return await _set_agent_identity_default_template(session, identity_id, payload)


async def _test_agent_identity_smtp(
    session: AsyncSession,
    identity_id: int,
) -> ConnectionTestResult:
    identity = await _get_agent_identity_or_raise(session, identity_id)
    started_at = perf_counter()
    ok, message = await test_smtp_connection(identity)
    await _record_agent_identity_event(
        session,
        identity,
        "agent_cli.identity.smtp_tested",
        level="info" if ok else "warning",
        metadata={
            "ok": ok,
            "result": "ok" if ok else "failed",
            "duration_ms": int((perf_counter() - started_at) * 1000),
            "host": identity.smtp_host,
        },
    )
    safe_message = sanitize_user_visible_error(message)
    return ConnectionTestResult(
        ok=ok,
        message=safe_message,
        host=identity.smtp_host,
        possible_cause=explain_smtp_error(safe_message) if not ok else None,
    )


async def _test_agent_identity_imap(
    session: AsyncSession,
    identity_id: int,
) -> ConnectionTestResult:
    identity = await _get_agent_identity_or_raise(session, identity_id)
    started_at = perf_counter()
    ok, message = await test_imap_connection(identity)
    await _record_agent_identity_event(
        session,
        identity,
        "agent_cli.identity.imap_tested",
        level="info" if ok else "warning",
        metadata={
            "ok": ok,
            "result": "ok" if ok else "failed",
            "duration_ms": int((perf_counter() - started_at) * 1000),
            "host": identity.imap_host,
        },
    )
    return ConnectionTestResult(
        ok=ok,
        message=sanitize_user_visible_error(message),
        host=identity.imap_host,
    )


async def _get_agent_identity_or_raise(
    session: AsyncSession,
    identity_id: int,
) -> IdentityProfile:
    identity = await session.scalar(
        select(IdentityProfile).where(
            IdentityProfile.id == identity_id,
            IdentityProfile.deleted_at.is_(None),
        )
    )
    if identity is None:
        raise ValueError("未找到身份配置")
    return identity


async def _record_agent_identity_event(
    session: AsyncSession,
    identity: IdentityProfile,
    event_name: str,
    *,
    level: str = "info",
    metadata: dict[str, object] | None = None,
) -> None:
    event_metadata: dict[str, object] = {
        "id": identity.id,
        "name": identity.name,
        "profile_name": identity.profile_name,
        "sender_name": identity.sender_name,
        "email_address": identity.email_address,
        "smtp_host": identity.smtp_host,
        "imap_host": identity.imap_host,
        "is_default": identity.is_default,
        "actor": "agent_cli",
    }
    if metadata:
        event_metadata.update(metadata)
    await record_operation_log(
        session,
        category="user_action",
        event_name=event_name,
        level=level,
        entity_type="identity",
        entity_id=str(identity.id),
        metadata=event_metadata,
    )


def _agent_identity_error(error: ValueError) -> AgentApiError:
    message = str(error)
    return AgentApiError(
        status_code=404 if "未找到" in message or "不存在" in message else 422,
        code="IDENTITY_OPERATION_REJECTED",
        message=message,
    )


def _serialize_identity(identity: IdentityProfile) -> AgentIdentityRead:
    result = AgentIdentityRead(
        id=identity.id,
        name=identity.name,
        profile_name=identity.profile_name,
        sender_name=identity.sender_name,
        email_address=identity.email_address,
        default_language=identity.default_language,
        outreach_generation_mode=identity.outreach_generation_mode,
        default_outreach_template_id=identity.default_outreach_template_id,
        current_primary_material_id=identity.current_primary_material_id,
        communication_group_id=identity.communication_group_id,
        match_threshold=identity.match_threshold,
        daily_send_limit=identity.daily_send_limit,
        send_interval_min=identity.send_interval_min,
        send_interval_max=identity.send_interval_max,
        same_domain_cooldown_minutes=identity.same_domain_cooldown_minutes,
        smtp_configured=bool(
            identity.smtp_host and identity.smtp_username and identity.smtp_password
        ),
        imap_configured=_identity_has_imap_config(identity),
        is_default=identity.is_default,
        created_at=identity.created_at,
        updated_at=identity.updated_at,
    )
    return result.model_copy(update={"revision": revision_for(result)})


async def _ensure_identity_revision(
    session: AsyncSession,
    identity_id: int,
    if_revision: str | None,
) -> None:
    if not if_revision:
        return
    identity = await _get_agent_identity_or_raise(session, identity_id)
    current = _serialize_identity(identity)
    ensure_revision(
        if_revision,
        current.revision,
        resource="identities",
        resource_id=identity_id,
        latest=current.model_dump(mode="json"),
    )
