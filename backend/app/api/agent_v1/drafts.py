from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.agent_api_errors import AgentApiError
from app.core.database import get_async_session, get_session_factory
from app.models import EmailTask
from app.schemas.agent import (
    AgentActionPlanRead,
    AgentDraftGenerateRequest,
    AgentDraftRead,
    AgentDraftRegenerateRequest,
    AgentDraftRewriteRequest,
    AgentDraftSaveRequest,
    AgentPrepareSendRequest,
    AgentUiHandoffRead,
)
from app.services.agent_action_plans import create_email_action_plan
from app.services.agent_drafts import (
    generate_agent_draft,
    regenerate_agent_draft,
    rewrite_agent_draft,
    save_agent_draft,
)
from app.services.agent_mutations import execute_agent_factory_mutation
from app.services.agent_ui_handoffs import create_draft_workspace_ui_handoff

from .support import (
    _ensure_draft_revision,
    _serialize_draft,
)

router = APIRouter()


@router.post(
    "/drafts/{task_id}/present",
    response_model=AgentUiHandoffRead,
    status_code=status.HTTP_201_CREATED,
)
async def present_agent_draft(
    task_id: int,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> AgentUiHandoffRead:
    return await create_draft_workspace_ui_handoff(
        get_session_factory(),
        task_id,
        idempotency_key=idempotency_key,
    )


@router.get("/drafts/{task_id}", response_model=AgentDraftRead)
async def read_agent_draft(
    task_id: int,
    session: AsyncSession = Depends(get_async_session),
) -> AgentDraftRead:
    task = await session.scalar(
        select(EmailTask)
        .options(selectinload(EmailTask.professor))
        .where(EmailTask.id == task_id),
    )
    if task is None:
        raise HTTPException(status_code=404, detail="未找到邮件任务")
    return _serialize_draft(task)


@router.post(
    "/drafts",
    response_model=AgentDraftRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_agent_draft(
    payload: AgentDraftGenerateRequest,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> AgentDraftRead:
    try:

        async def mutation() -> AgentDraftRead:
            task = await generate_agent_draft(get_session_factory(), payload)
            return _serialize_draft(task)

        return await execute_agent_factory_mutation(
            get_session_factory(),
            command="drafts.generate",
            request_data=payload.model_dump(mode="json"),
            idempotency_key=idempotency_key,
            response_type=AgentDraftRead,
            mutation=mutation,
            external_execution=True,
        )
    except HTTPException as exc:
        raise AgentApiError(
            status_code=exc.status_code,
            code="DRAFT_OPERATION_REJECTED",
            message=str(exc.detail),
        ) from exc
    except ValueError as exc:
        raise AgentApiError(
            status_code=409,
            code="DRAFT_OPERATION_REJECTED",
            message=str(exc),
        ) from exc


@router.put("/drafts/{task_id}", response_model=AgentDraftRead)
async def save_agent_draft_content(
    task_id: int,
    payload: AgentDraftSaveRequest,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    if_revision: str | None = Header(default=None, alias="If-Revision"),
) -> AgentDraftRead:
    try:

        async def mutation() -> AgentDraftRead:
            await _ensure_draft_revision(task_id, if_revision)
            task = await save_agent_draft(get_session_factory(), task_id, payload)
            return _serialize_draft(task)

        return await execute_agent_factory_mutation(
            get_session_factory(),
            command="drafts.save",
            request_data={
                "task_id": task_id,
                "if_revision": if_revision,
                **payload.model_dump(mode="json", exclude_unset=True),
            },
            idempotency_key=idempotency_key,
            response_type=AgentDraftRead,
            mutation=mutation,
        )
    except ValueError as exc:
        raise AgentApiError(
            status_code=409,
            code="DRAFT_OPERATION_REJECTED",
            message=str(exc),
        ) from exc


@router.post("/drafts/{task_id}/regenerate", response_model=AgentDraftRead)
async def regenerate_agent_draft_content(
    task_id: int,
    payload: AgentDraftRegenerateRequest,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    if_revision: str | None = Header(default=None, alias="If-Revision"),
) -> AgentDraftRead:
    try:

        async def mutation() -> AgentDraftRead:
            await _ensure_draft_revision(task_id, if_revision)
            task = await regenerate_agent_draft(get_session_factory(), task_id, payload)
            return _serialize_draft(task)

        return await execute_agent_factory_mutation(
            get_session_factory(),
            command="drafts.regenerate",
            request_data={
                "task_id": task_id,
                "if_revision": if_revision,
                **payload.model_dump(mode="json", exclude_unset=True),
            },
            idempotency_key=idempotency_key,
            response_type=AgentDraftRead,
            mutation=mutation,
            external_execution=True,
        )
    except ValueError as exc:
        raise AgentApiError(
            status_code=409,
            code="DRAFT_OPERATION_REJECTED",
            message=str(exc),
        ) from exc


@router.post("/drafts/{task_id}/rewrite", response_model=AgentDraftRead)
async def rewrite_agent_draft_content(
    task_id: int,
    payload: AgentDraftRewriteRequest,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    if_revision: str | None = Header(default=None, alias="If-Revision"),
) -> AgentDraftRead:
    try:

        async def mutation() -> AgentDraftRead:
            await _ensure_draft_revision(task_id, if_revision)
            task = await rewrite_agent_draft(get_session_factory(), task_id, payload)
            return _serialize_draft(task)

        return await execute_agent_factory_mutation(
            get_session_factory(),
            command="drafts.rewrite",
            request_data={
                "task_id": task_id,
                "if_revision": if_revision,
                **payload.model_dump(mode="json", exclude_unset=True),
            },
            idempotency_key=idempotency_key,
            response_type=AgentDraftRead,
            mutation=mutation,
            external_execution=True,
        )
    except ValueError as exc:
        raise AgentApiError(
            status_code=409,
            code="DRAFT_OPERATION_REJECTED",
            message=str(exc),
        ) from exc


@router.post(
    "/drafts/{task_id}/prepare-send",
    response_model=AgentActionPlanRead,
    status_code=status.HTTP_201_CREATED,
)
async def prepare_agent_draft_send(
    task_id: int,
    payload: AgentPrepareSendRequest,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> AgentActionPlanRead:
    try:
        return await create_email_action_plan(
            get_session_factory(),
            task_id,
            payload,
            idempotency_key=idempotency_key,
        )
    except ValueError as exc:
        raise AgentApiError(
            status_code=409,
            code="DRAFT_NOT_SENDABLE",
            message=str(exc),
            suggested_command=f"auto-email-sender drafts get {task_id}",
        ) from exc
