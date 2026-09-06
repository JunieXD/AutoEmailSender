from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.agent_api_errors import AgentApiError
from app.core.database import get_async_session, get_session_factory
from app.modules.identities.public import resolve_identity_communication_scope
from app.modules.llm.public import LLMRuntimeError
from app.modules.matching.public import (
    MatchAnalysisAlreadyRunningError,
    calculate_task_match_once,
)
from app.modules.workspace.deliveries.schemas import (
    EmailDeliveryActionRead,
    EmailDeliveryRescheduleRequest,
    EmailDeliverySort,
    EmailDeliverySource,
    EmailDeliveryViewQuery,
)
from app.modules.workspace.deliveries.service import (
    list_email_deliveries,
    reschedule_email_delivery,
)
from app.modules.workspace.public import (
    EmailTaskApprovalRequest,
    WorkspaceSyncWarningRead,
    approve_draft_task,
    build_workspace_thread,
    build_workspace_thread_for_task,
    cancel_scheduled_task,
    continue_task_manually,
    ensure_workspace_task,
    start_follow_up_task,
    update_task_outreach_config,
    update_task_primary_material,
)
from app.schemas.agent import (
    AgentDraftSaveRequest,
    AgentEmailDeliveryPageRead,
    AgentTaskMatchCalculationRead,
    AgentTaskOutreachConfigRequest,
    AgentTaskPrimaryMaterialRequest,
    AgentTaskRuntimeProfileRequest,
    AgentTaskTokenUsageRead,
    AgentUiHandoffRead,
    AgentWorkspaceThreadRead,
)
from app.services.agent_mutations import (
    execute_agent_factory_mutation,
    execute_agent_mutation,
)
from app.services.agent_ui_handoffs import create_task_center_ui_handoff
from app.services.operation_logs import (
    record_operation_log,
    sanitize_user_visible_error,
)

from .support import (
    _agent_task_error,
    _ensure_draft_revision,
    _identity_has_imap_config,
    _run_agent_task_workspace_action,
    _serialize_agent_workspace_thread,
)

router = APIRouter()


async def sync_workspace_professor_replies(
    session_factory: async_sessionmaker[AsyncSession],
    identity_id: int,
    professor_id: int,
) -> int:
    from app.modules.communications.public import (
        sync_workspace_professor_replies as sync_replies,
    )

    return await sync_replies(session_factory, identity_id, professor_id)


@router.post(
    "/tasks/{task_id}/present",
    response_model=AgentUiHandoffRead,
    status_code=status.HTTP_201_CREATED,
)
async def present_agent_task(
    task_id: int,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> AgentUiHandoffRead:
    return await create_task_center_ui_handoff(
        get_session_factory(),
        task_id,
        idempotency_key=idempotency_key,
    )


@router.get("/workspaces/{professor_id}", response_model=AgentWorkspaceThreadRead)
async def read_agent_workspace(
    professor_id: int,
    identity_id: int = Query(..., ge=1),
    llm_profile_id: int = Query(..., ge=1),
    session: AsyncSession = Depends(get_async_session),
) -> AgentWorkspaceThreadRead:
    workspace = await build_workspace_thread(
        session,
        professor_id=professor_id,
        identity_id=identity_id,
        llm_profile_id=llm_profile_id,
    )
    return _serialize_agent_workspace_thread(workspace)


@router.post(
    "/workspaces/{professor_id}/ensure-task",
    response_model=AgentWorkspaceThreadRead,
)
async def ensure_agent_workspace_task(
    professor_id: int,
    identity_id: int = Query(..., ge=1),
    llm_profile_id: int = Query(..., ge=1),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    session: AsyncSession = Depends(get_async_session),
) -> AgentWorkspaceThreadRead:
    return await execute_agent_mutation(
        session,
        command="workspaces.ensure-task",
        request_data={
            "professor_id": professor_id,
            "identity_id": identity_id,
            "llm_profile_id": llm_profile_id,
        },
        idempotency_key=idempotency_key,
        response_type=AgentWorkspaceThreadRead,
        mutation=lambda: _ensure_agent_workspace_task(
            session,
            professor_id=professor_id,
            identity_id=identity_id,
            llm_profile_id=llm_profile_id,
        ),
    )


@router.post(
    "/workspaces/{professor_id}/refresh-replies",
    response_model=AgentWorkspaceThreadRead,
)
async def refresh_agent_workspace_replies(
    professor_id: int,
    identity_id: int = Query(..., ge=1),
    llm_profile_id: int = Query(..., ge=1),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> AgentWorkspaceThreadRead:
    async def mutation() -> AgentWorkspaceThreadRead:
        async with get_session_factory()() as session:
            # Validate the requested workspace before opening any configured mailbox.
            await build_workspace_thread(
                session,
                professor_id=professor_id,
                identity_id=identity_id,
                llm_profile_id=llm_profile_id,
            )
            communication_scope = await resolve_identity_communication_scope(
                session,
                active_identity_id=identity_id,
            )
            sync_identities = [
                identity
                for identity in communication_scope.identities
                if _identity_has_imap_config(identity)
            ]
            if not sync_identities:
                raise AgentApiError(
                    status_code=409,
                    code="IMAP_NOT_CONFIGURED",
                    message="当前通信范围内没有已配置 IMAP 的发件身份，无法同步回信。",
                )

            results = await asyncio.gather(
                *[
                    sync_workspace_professor_replies(
                        get_session_factory(),
                        identity.id,
                        professor_id,
                    )
                    for identity in sync_identities
                ],
                return_exceptions=True,
            )
            warnings = [
                WorkspaceSyncWarningRead(
                    identity_id=identity.id,
                    identity_name=identity.profile_name or identity.name,
                    message=sanitize_user_visible_error(result),
                )
                for identity, result in zip(sync_identities, results, strict=True)
                if isinstance(result, BaseException)
            ]
            detected_count = sum(
                result
                for result in results
                if isinstance(result, int) and not isinstance(result, bool)
            )
            await record_operation_log(
                session,
                category="agent_action",
                event_name="agent_cli.workspace_replies_refreshed",
                level="warning" if warnings else "info",
                entity_type="professor",
                entity_id=str(professor_id),
                metadata={
                    "actor": "agent_cli",
                    "professor_id": professor_id,
                    "identity_id": identity_id,
                    "llm_profile_id": llm_profile_id,
                    "sync_identity_ids": [identity.id for identity in sync_identities],
                    "detected_count": detected_count,
                    "warning_count": len(warnings),
                },
            )
            await session.commit()
            workspace = await build_workspace_thread(
                session,
                professor_id=professor_id,
                identity_id=identity_id,
                llm_profile_id=llm_profile_id,
                sync_warnings=warnings,
            )
            return _serialize_agent_workspace_thread(workspace)

    return await execute_agent_factory_mutation(
        get_session_factory(),
        command="workspaces.refresh-replies",
        request_data={
            "professor_id": professor_id,
            "identity_id": identity_id,
            "llm_profile_id": llm_profile_id,
        },
        idempotency_key=idempotency_key,
        response_type=AgentWorkspaceThreadRead,
        mutation=mutation,
        external_execution=True,
    )


@router.get("/deliveries", response_model=AgentEmailDeliveryPageRead)
async def list_agent_email_deliveries(
    view: EmailDeliveryViewQuery = Query(default="upcoming"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=25, ge=1, le=100),
    identity_id: int | None = Query(default=None, ge=1),
    source: EmailDeliverySource = Query(default="all"),
    status_filter: str | None = Query(default=None, alias="status"),
    sort: EmailDeliverySort | None = Query(default=None),
    search_fields: str | None = Query(default=None, max_length=100),
    query: str | None = Query(default=None, max_length=200),
    task_id: int | None = Query(default=None, ge=1),
    session: AsyncSession = Depends(get_async_session),
) -> AgentEmailDeliveryPageRead:
    try:
        result = await list_email_deliveries(
            session,
            view=view,
            page=page,
            page_size=page_size,
            identity_id=identity_id,
            source=source,
            status=status_filter,
            sort=sort,
            search_fields=(
                tuple(field.strip() for field in search_fields.split(","))
                if search_fields is not None
                else None
            ),
            query=query,
            task_id=task_id,
        )
    except ValueError as exc:
        raise AgentApiError(
            status_code=422,
            code="INVALID_DELIVERY_FILTER",
            message=str(exc),
        ) from exc
    return AgentEmailDeliveryPageRead(
        items=result.items,
        next_cursor=str(result.page + 1) if result.page < result.total_pages else None,
        has_more=result.page < result.total_pages,
        page=result.page,
        page_size=result.page_size,
        total=result.total_count,
        total_pages=result.total_pages,
        counts=result.counts,
    )


@router.patch("/deliveries/{task_id}/schedule", response_model=EmailDeliveryActionRead)
async def reschedule_agent_email_delivery(
    task_id: int,
    payload: EmailDeliveryRescheduleRequest,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> EmailDeliveryActionRead:
    async def mutation() -> EmailDeliveryActionRead:
        async with get_session_factory()() as mutation_session:
            try:
                return await reschedule_email_delivery(
                    mutation_session,
                    task_id=task_id,
                    scheduled_at=payload.scheduled_at,
                    expected_updated_at=payload.expected_updated_at,
                )
            except HTTPException as exc:
                raise AgentApiError(
                    status_code=exc.status_code,
                    code="DELIVERY_RESCHEDULE_REJECTED",
                    message=str(exc.detail),
                    retryable=exc.status_code == 409,
                ) from exc

    return await execute_agent_factory_mutation(
        get_session_factory(),
        command="deliveries.reschedule",
        request_data={"task_id": task_id, **payload.model_dump(mode="json")},
        idempotency_key=idempotency_key,
        response_type=EmailDeliveryActionRead,
        mutation=mutation,
    )


@router.post("/tasks/{task_id}/approve-draft", response_model=AgentWorkspaceThreadRead)
async def approve_agent_task_draft(
    task_id: int,
    payload: AgentDraftSaveRequest,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    if_revision: str | None = Header(default=None, alias="If-Revision"),
    session: AsyncSession = Depends(get_async_session),
) -> AgentWorkspaceThreadRead:
    async def mutation() -> AgentWorkspaceThreadRead:
        await _ensure_draft_revision(task_id, if_revision)
        return await _run_agent_task_workspace_action(
            session,
            task_id=task_id,
            command="drafts.approve",
            workspace_task_id=task_id,
            action=lambda: approve_draft_task(
                get_session_factory(),
                task_id,
                EmailTaskApprovalRequest(
                    subject=payload.subject,
                    body_text=payload.body_text,
                    body_html=payload.body_html,
                    selected_material_ids=payload.attachment_material_ids,
                ),
            ),
        )

    return await execute_agent_mutation(
        session,
        command="drafts.approve",
        request_data={
            "task_id": task_id,
            "if_revision": if_revision,
            **payload.model_dump(mode="json"),
        },
        idempotency_key=idempotency_key,
        response_type=AgentWorkspaceThreadRead,
        mutation=mutation,
    )


@router.post(
    "/tasks/{task_id}/cancel-schedule", response_model=AgentWorkspaceThreadRead
)
async def cancel_agent_task_schedule(
    task_id: int,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    session: AsyncSession = Depends(get_async_session),
) -> AgentWorkspaceThreadRead:
    return await execute_agent_mutation(
        session,
        command="tasks.cancel-schedule",
        request_data={"task_id": task_id},
        idempotency_key=idempotency_key,
        response_type=AgentWorkspaceThreadRead,
        mutation=lambda: _run_agent_task_workspace_action(
            session,
            task_id=task_id,
            command="tasks.cancel-schedule",
            workspace_task_id=task_id,
            action=lambda: cancel_scheduled_task(get_session_factory(), task_id),
        ),
    )


@router.post(
    "/tasks/{task_id}/continue-manually", response_model=AgentWorkspaceThreadRead
)
async def continue_agent_task_manually(
    task_id: int,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    session: AsyncSession = Depends(get_async_session),
) -> AgentWorkspaceThreadRead:
    return await execute_agent_mutation(
        session,
        command="tasks.continue-manually",
        request_data={"task_id": task_id},
        idempotency_key=idempotency_key,
        response_type=AgentWorkspaceThreadRead,
        mutation=lambda: _run_agent_task_workspace_action(
            session,
            task_id=task_id,
            command="tasks.continue-manually",
            action=lambda: continue_task_manually(get_session_factory(), task_id),
        ),
    )


@router.post(
    "/tasks/{task_id}/start-follow-up", response_model=AgentWorkspaceThreadRead
)
async def start_agent_task_follow_up(
    task_id: int,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    session: AsyncSession = Depends(get_async_session),
) -> AgentWorkspaceThreadRead:
    return await execute_agent_mutation(
        session,
        command="tasks.start-follow-up",
        request_data={"task_id": task_id},
        idempotency_key=idempotency_key,
        response_type=AgentWorkspaceThreadRead,
        mutation=lambda: _run_agent_task_workspace_action(
            session,
            task_id=task_id,
            command="tasks.start-follow-up",
            action=lambda: start_follow_up_task(get_session_factory(), task_id),
        ),
    )


@router.post(
    "/tasks/{task_id}/primary-material", response_model=AgentWorkspaceThreadRead
)
async def update_agent_task_primary_material(
    task_id: int,
    payload: AgentTaskPrimaryMaterialRequest,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    session: AsyncSession = Depends(get_async_session),
) -> AgentWorkspaceThreadRead:
    async def mutation() -> AgentWorkspaceThreadRead:
        async with get_session_factory()() as mutation_session:
            result = await _run_agent_task_workspace_action(
                mutation_session,
                task_id=task_id,
                command="tasks.set-primary-material",
                workspace_task_id=task_id,
                action=lambda: update_task_primary_material(
                    get_session_factory(),
                    task_id,
                    payload.primary_material_id,
                ),
            )
            await mutation_session.commit()
            return result

    return await execute_agent_factory_mutation(
        get_session_factory(),
        command="tasks.set-primary-material",
        request_data={"task_id": task_id, **payload.model_dump(mode="json")},
        idempotency_key=idempotency_key,
        response_type=AgentWorkspaceThreadRead,
        mutation=mutation,
        external_execution=True,
    )


@router.post(
    "/tasks/{task_id}/outreach-config", response_model=AgentWorkspaceThreadRead
)
async def update_agent_task_outreach_config(
    task_id: int,
    payload: AgentTaskOutreachConfigRequest,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    session: AsyncSession = Depends(get_async_session),
) -> AgentWorkspaceThreadRead:
    request_payload = payload.model_dump(mode="json", exclude_unset=True)
    return await execute_agent_mutation(
        session,
        command="tasks.set-outreach-config",
        request_data={"task_id": task_id, **request_payload},
        idempotency_key=idempotency_key,
        response_type=AgentWorkspaceThreadRead,
        mutation=lambda: _run_agent_task_workspace_action(
            session,
            task_id=task_id,
            command="tasks.set-outreach-config",
            workspace_task_id=task_id,
            action=lambda: update_task_outreach_config(
                get_session_factory(),
                task_id,
                outreach_generation_mode=payload.outreach_generation_mode,
                outreach_template_id=payload.outreach_template_id,
                template_selection_explicit=(
                    "outreach_template_id" in payload.model_fields_set
                ),
                outreach_template_subject=payload.outreach_template_subject,
                outreach_template_body_text=payload.outreach_template_body_text,
                outreach_template_body_html=payload.outreach_template_body_html,
            ),
        ),
    )


@router.post(
    "/tasks/{task_id}/calculate-match", response_model=AgentTaskMatchCalculationRead
)
async def calculate_agent_task_match(
    task_id: int,
    payload: AgentTaskRuntimeProfileRequest | None = None,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    session: AsyncSession = Depends(get_async_session),
) -> AgentTaskMatchCalculationRead:
    request_payload = payload.model_dump(mode="json") if payload is not None else None

    async def mutation() -> AgentTaskMatchCalculationRead:
        async with get_session_factory()() as mutation_session:
            result = await _calculate_agent_task_match(
                mutation_session,
                task_id=task_id,
                llm_profile_id=payload.llm_profile_id if payload is not None else None,
            )
            await mutation_session.commit()
            return result

    return await execute_agent_factory_mutation(
        get_session_factory(),
        command="tasks.calculate-match",
        request_data={"task_id": task_id, "payload": request_payload},
        idempotency_key=idempotency_key,
        response_type=AgentTaskMatchCalculationRead,
        mutation=mutation,
        external_execution=True,
    )


async def _calculate_agent_task_match(
    session: AsyncSession,
    *,
    task_id: int,
    llm_profile_id: int | None,
) -> AgentTaskMatchCalculationRead:
    try:
        result = await calculate_task_match_once(
            get_session_factory(),
            task_id,
            llm_profile_id=llm_profile_id,
        )
    except MatchAnalysisAlreadyRunningError as exc:
        raise AgentApiError(
            status_code=409,
            code="TASK_MATCH_ANALYSIS_RUNNING",
            message=str(exc),
            retryable=True,
        ) from exc
    except LLMRuntimeError as exc:
        raise AgentApiError(
            status_code=502,
            code="TASK_MATCH_ANALYSIS_FAILED",
            message=sanitize_user_visible_error(exc),
            retryable=True,
            external_execution_unknown=True,
        ) from exc
    except ValueError as exc:
        raise _agent_task_error(exc) from exc

    session.expire_all()
    workspace = await build_workspace_thread_for_task(session, task_id=task_id)
    await record_operation_log(
        session,
        category="agent_action",
        event_name="agent_cli.tasks.calculate_match",
        entity_type="email_task",
        entity_id=str(task_id),
        metadata={
            "actor": "agent_cli",
            "task_id": task_id,
            "professor_id": result.professor_id,
            "identity_id": result.identity_id,
            "match_source_identity_id": result.match_source_identity_id,
            "llm_profile_id": result.llm_profile_id,
            "match_analysis_run_id": result.run_id,
            "total_tokens": result.usage.total_tokens,
        },
    )
    return AgentTaskMatchCalculationRead(
        task_id=task_id,
        thread=_serialize_agent_workspace_thread(workspace),
        usage=AgentTaskTokenUsageRead(
            prompt_tokens=result.usage.prompt_tokens,
            completion_tokens=result.usage.completion_tokens,
            total_tokens=result.usage.total_tokens,
            cached_tokens=result.usage.cached_tokens,
        ),
        run_id=result.run_id,
    )


async def _ensure_agent_workspace_task(
    session: AsyncSession,
    *,
    professor_id: int,
    identity_id: int,
    llm_profile_id: int,
) -> AgentWorkspaceThreadRead:
    task = await ensure_workspace_task(
        session,
        professor_id=professor_id,
        identity_id=identity_id,
        llm_profile_id=llm_profile_id,
        commit=False,
    )
    workspace = await build_workspace_thread(
        session,
        professor_id=professor_id,
        identity_id=identity_id,
        llm_profile_id=llm_profile_id,
    )
    await record_operation_log(
        session,
        category="agent_action",
        event_name="agent_cli.workspace_task_ensured",
        entity_type="email_task",
        entity_id=str(task.id),
        metadata={
            "actor": "agent_cli",
            "task_id": task.id,
            "professor_id": professor_id,
            "identity_id": identity_id,
            "llm_profile_id": llm_profile_id,
            "task_status": task.status,
        },
    )
    return _serialize_agent_workspace_thread(workspace)
