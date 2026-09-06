from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, Header, Query, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.agent_api_errors import AgentApiError
from app.core.database import get_async_session, get_session_factory
from app.models import EmailTask
from app.modules.campaigns.public import (
    BatchTaskResendContextError,
    BatchTaskResendContextRead,
    archive_agent_campaign,
    build_batch_task_resend_context,
    cancel_agent_campaign_item_send,
    get_agent_campaign,
    list_agent_campaign_items,
    list_agent_campaigns,
    pause_agent_campaign,
    remove_agent_campaign_item,
    restore_agent_campaign,
    retry_agent_campaign_item_draft,
    start_agent_campaign_draft_generation,
    stop_agent_campaign,
)
from app.modules.workspace.public import (
    BatchDraftApprovalConflictError,
    EmailTaskApprovalRequest,
    approve_draft_task,
    approve_generated_batch_drafts,
    build_workspace_thread_for_task,
)
from app.schemas.agent import (
    AgentCampaignApproveDraftsRequest,
    AgentCampaignBulkApproveRead,
    AgentCampaignCreateRequest,
    AgentCampaignItemRead,
    AgentCampaignRead,
    AgentCampaignSendRequest,
    AgentChangePlanRead,
    AgentDraftSaveRequest,
    AgentPage,
    AgentWorkspaceThreadRead,
)
from app.services.agent_change_plans import (
    create_campaign_create_change_plan,
    create_campaign_restore_send_change_plan,
    create_campaign_resume_change_plan,
    create_campaign_send_change_plan,
)
from app.services.agent_mutations import (
    execute_agent_factory_mutation,
    execute_agent_mutation,
)

from .support import (
    _ensure_draft_revision,
    _run_agent_task_workspace_action,
    _serialize_agent_workspace_thread,
)

router = APIRouter()


def _cancel_agent_campaign_draft_generation(request: Request, campaign_id: int) -> None:
    runtime_manager = getattr(request.app.state, "runtime_manager", None)
    if runtime_manager is not None:
        runtime_manager.cancel_batch_draft_generation(campaign_id)


@router.get("/campaigns", response_model=AgentPage[AgentCampaignRead])
async def list_agent_email_campaigns(
    view: Literal["current", "trash"] = Query(default="current"),
    identity_id: int | None = Query(default=None, ge=1),
    status: Literal["running", "paused", "stopped", "completed", "expired"]
    | None = Query(
        default=None,
    ),
    cursor: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=500),
    session: AsyncSession = Depends(get_async_session),
) -> AgentPage[AgentCampaignRead]:
    campaigns, next_cursor, has_more = await list_agent_campaigns(
        session,
        view=view,
        identity_id=identity_id,
        status=status,
        cursor=cursor,
        limit=limit,
    )
    return AgentPage(items=campaigns, next_cursor=next_cursor, has_more=has_more)


@router.post(
    "/campaigns/prepare-create",
    response_model=AgentChangePlanRead,
    status_code=status.HTTP_201_CREATED,
)
async def prepare_agent_email_campaign_create(
    payload: AgentCampaignCreateRequest,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> AgentChangePlanRead:
    return await create_campaign_create_change_plan(
        get_session_factory(),
        payload,
        idempotency_key=idempotency_key,
    )


@router.get("/campaigns/{campaign_id}", response_model=AgentCampaignRead)
async def read_agent_email_campaign(
    campaign_id: int,
    session: AsyncSession = Depends(get_async_session),
) -> AgentCampaignRead:
    return await get_agent_campaign(session, campaign_id)


@router.get(
    "/campaigns/{campaign_id}/resend-context",
    response_model=BatchTaskResendContextRead,
)
async def read_agent_email_campaign_resend_context(
    campaign_id: int,
    session: AsyncSession = Depends(get_async_session),
) -> BatchTaskResendContextRead:
    try:
        return await build_batch_task_resend_context(session, campaign_id)
    except BatchTaskResendContextError as exc:
        raise AgentApiError(
            status_code=exc.status_code,
            code="CAMPAIGN_RESEND_CONTEXT_UNAVAILABLE",
            message=str(exc),
        ) from exc


@router.get(
    "/campaigns/{campaign_id}/items",
    response_model=AgentPage[AgentCampaignItemRead],
)
async def list_agent_email_campaign_items(
    campaign_id: int,
    cursor: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=500),
    session: AsyncSession = Depends(get_async_session),
) -> AgentPage[AgentCampaignItemRead]:
    items, next_cursor, has_more = await list_agent_campaign_items(
        session,
        campaign_id,
        cursor=cursor,
        limit=limit,
    )
    return AgentPage(items=items, next_cursor=next_cursor, has_more=has_more)


@router.get(
    "/campaigns/{campaign_id}/items/{item_id}/thread",
    response_model=AgentWorkspaceThreadRead,
)
async def read_agent_email_campaign_item_thread(
    campaign_id: int,
    item_id: int,
    session: AsyncSession = Depends(get_async_session),
) -> AgentWorkspaceThreadRead:
    await _ensure_agent_campaign_item(session, campaign_id=campaign_id, item_id=item_id)
    workspace = await build_workspace_thread_for_task(session, task_id=item_id)
    return _serialize_agent_workspace_thread(workspace)


@router.post(
    "/campaigns/{campaign_id}/items/{item_id}/approve-draft",
    response_model=AgentWorkspaceThreadRead,
)
async def approve_agent_email_campaign_item_draft(
    campaign_id: int,
    item_id: int,
    payload: AgentDraftSaveRequest,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    if_revision: str | None = Header(default=None, alias="If-Revision"),
    session: AsyncSession = Depends(get_async_session),
) -> AgentWorkspaceThreadRead:
    async def mutation() -> AgentWorkspaceThreadRead:
        await _ensure_agent_campaign_item(
            session,
            campaign_id=campaign_id,
            item_id=item_id,
        )
        await _ensure_draft_revision(item_id, if_revision)
        return await _run_agent_task_workspace_action(
            session,
            task_id=item_id,
            command="campaigns.approve-item-draft",
            workspace_task_id=item_id,
            action=lambda: approve_draft_task(
                get_session_factory(),
                item_id,
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
        command="campaigns.approve-item-draft",
        request_data={
            "campaign_id": campaign_id,
            "item_id": item_id,
            "if_revision": if_revision,
            **payload.model_dump(mode="json"),
        },
        idempotency_key=idempotency_key,
        response_type=AgentWorkspaceThreadRead,
        mutation=mutation,
    )


@router.post(
    "/campaigns/{campaign_id}/approve-drafts",
    response_model=AgentCampaignBulkApproveRead,
)
async def approve_agent_email_campaign_drafts(
    campaign_id: int,
    payload: AgentCampaignApproveDraftsRequest,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> AgentCampaignBulkApproveRead:
    async def mutation() -> AgentCampaignBulkApproveRead:
        try:
            approved_count = await approve_generated_batch_drafts(
                get_session_factory(),
                campaign_id,
                payload.item_ids,
            )
        except BatchDraftApprovalConflictError as exc:
            raise AgentApiError(
                status_code=409,
                code="CAMPAIGN_DRAFT_APPROVAL_CONFLICT",
                message=str(exc),
                retryable=True,
            ) from exc
        except ValueError as exc:
            raise AgentApiError(
                status_code=400,
                code="CAMPAIGN_DRAFT_APPROVAL_REJECTED",
                message=str(exc),
            ) from exc
        async with get_session_factory()() as read_session:
            campaign = await get_agent_campaign(read_session, campaign_id)
        return AgentCampaignBulkApproveRead(
            approved_count=approved_count,
            campaign=campaign,
        )

    return await execute_agent_factory_mutation(
        get_session_factory(),
        command="campaigns.approve-drafts",
        request_data={"campaign_id": campaign_id, **payload.model_dump(mode="json")},
        idempotency_key=idempotency_key,
        response_type=AgentCampaignBulkApproveRead,
        mutation=mutation,
    )


@router.post(
    "/campaigns/{campaign_id}/start-drafts",
    response_model=AgentCampaignRead,
)
async def start_agent_email_campaign_drafts(
    campaign_id: int,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    session: AsyncSession = Depends(get_async_session),
) -> AgentCampaignRead:
    return await execute_agent_mutation(
        session,
        command="campaigns.start-drafts",
        request_data={"campaign_id": campaign_id},
        idempotency_key=idempotency_key,
        response_type=AgentCampaignRead,
        mutation=lambda: start_agent_campaign_draft_generation(session, campaign_id),
    )


@router.post("/campaigns/{campaign_id}/pause", response_model=AgentCampaignRead)
async def pause_agent_email_campaign(
    campaign_id: int,
    request: Request,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    session: AsyncSession = Depends(get_async_session),
) -> AgentCampaignRead:
    campaign = await execute_agent_mutation(
        session,
        command="campaigns.pause",
        request_data={"campaign_id": campaign_id},
        idempotency_key=idempotency_key,
        response_type=AgentCampaignRead,
        mutation=lambda: pause_agent_campaign(session, campaign_id),
    )
    _cancel_agent_campaign_draft_generation(request, campaign_id)
    return campaign


@router.post("/campaigns/{campaign_id}/stop", response_model=AgentCampaignRead)
async def stop_agent_email_campaign(
    campaign_id: int,
    request: Request,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    session: AsyncSession = Depends(get_async_session),
) -> AgentCampaignRead:
    campaign = await execute_agent_mutation(
        session,
        command="campaigns.stop",
        request_data={"campaign_id": campaign_id},
        idempotency_key=idempotency_key,
        response_type=AgentCampaignRead,
        mutation=lambda: stop_agent_campaign(session, campaign_id),
    )
    _cancel_agent_campaign_draft_generation(request, campaign_id)
    return campaign


@router.post("/campaigns/{campaign_id}/archive", response_model=AgentCampaignRead)
async def archive_agent_email_campaign(
    campaign_id: int,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    session: AsyncSession = Depends(get_async_session),
) -> AgentCampaignRead:
    return await execute_agent_mutation(
        session,
        command="campaigns.archive",
        request_data={"campaign_id": campaign_id},
        idempotency_key=idempotency_key,
        response_type=AgentCampaignRead,
        mutation=lambda: archive_agent_campaign(session, campaign_id),
    )


@router.post("/campaigns/{campaign_id}/restore", response_model=AgentCampaignRead)
async def restore_agent_email_campaign(
    campaign_id: int,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    session: AsyncSession = Depends(get_async_session),
) -> AgentCampaignRead:
    return await execute_agent_mutation(
        session,
        command="campaigns.restore",
        request_data={"campaign_id": campaign_id},
        idempotency_key=idempotency_key,
        response_type=AgentCampaignRead,
        mutation=lambda: restore_agent_campaign(session, campaign_id),
    )


@router.post(
    "/campaigns/{campaign_id}/items/{item_id}/remove",
    response_model=AgentCampaignRead,
)
async def remove_agent_email_campaign_item(
    campaign_id: int,
    item_id: int,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    session: AsyncSession = Depends(get_async_session),
) -> AgentCampaignRead:
    return await execute_agent_mutation(
        session,
        command="campaigns.items.remove",
        request_data={"campaign_id": campaign_id, "item_id": item_id},
        idempotency_key=idempotency_key,
        response_type=AgentCampaignRead,
        mutation=lambda: remove_agent_campaign_item(session, campaign_id, item_id),
    )


@router.post(
    "/campaigns/{campaign_id}/items/{item_id}/cancel-send",
    response_model=AgentCampaignRead,
)
async def cancel_agent_email_campaign_item_send(
    campaign_id: int,
    item_id: int,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    session: AsyncSession = Depends(get_async_session),
) -> AgentCampaignRead:
    return await execute_agent_mutation(
        session,
        command="campaigns.items.cancel-send",
        request_data={"campaign_id": campaign_id, "item_id": item_id},
        idempotency_key=idempotency_key,
        response_type=AgentCampaignRead,
        mutation=lambda: cancel_agent_campaign_item_send(session, campaign_id, item_id),
    )


@router.post(
    "/campaigns/{campaign_id}/items/{item_id}/retry-draft",
    response_model=AgentCampaignRead,
)
async def retry_agent_email_campaign_item_draft(
    campaign_id: int,
    item_id: int,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    session: AsyncSession = Depends(get_async_session),
) -> AgentCampaignRead:
    return await execute_agent_mutation(
        session,
        command="campaigns.items.retry-draft",
        request_data={"campaign_id": campaign_id, "item_id": item_id},
        idempotency_key=idempotency_key,
        response_type=AgentCampaignRead,
        mutation=lambda: retry_agent_campaign_item_draft(session, campaign_id, item_id),
    )


@router.post(
    "/campaigns/{campaign_id}/prepare-send",
    response_model=AgentChangePlanRead,
    status_code=status.HTTP_201_CREATED,
)
async def prepare_agent_email_campaign_send(
    campaign_id: int,
    payload: AgentCampaignSendRequest,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> AgentChangePlanRead:
    return await create_campaign_send_change_plan(
        get_session_factory(),
        campaign_id,
        payload,
        idempotency_key=idempotency_key,
    )


@router.post(
    "/campaigns/{campaign_id}/prepare-resume",
    response_model=AgentChangePlanRead,
    status_code=status.HTTP_201_CREATED,
)
async def prepare_agent_email_campaign_resume(
    campaign_id: int,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> AgentChangePlanRead:
    return await create_campaign_resume_change_plan(
        get_session_factory(),
        campaign_id,
        idempotency_key=idempotency_key,
    )


@router.post(
    "/campaigns/{campaign_id}/items/{item_id}/prepare-restore-send",
    response_model=AgentChangePlanRead,
    status_code=status.HTTP_201_CREATED,
)
async def prepare_agent_email_campaign_item_send_restore(
    campaign_id: int,
    item_id: int,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> AgentChangePlanRead:
    return await create_campaign_restore_send_change_plan(
        get_session_factory(),
        campaign_id,
        item_id,
        idempotency_key=idempotency_key,
    )


async def _ensure_agent_campaign_item(
    session: AsyncSession,
    *,
    campaign_id: int,
    item_id: int,
) -> None:
    matched_item_id = await session.scalar(
        select(EmailTask.id).where(
            EmailTask.id == item_id,
            EmailTask.batch_task_id == campaign_id,
        ),
    )
    if matched_item_id is None:
        raise AgentApiError(
            status_code=404,
            code="CAMPAIGN_ITEM_NOT_FOUND",
            message="未找到属于该活动的邮件项。",
        )
