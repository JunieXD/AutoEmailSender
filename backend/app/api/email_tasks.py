from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.workspace_support import build_workspace_thread, build_workspace_thread_for_task
from app.core.database import get_async_session, get_session_factory
from app.schemas.email_task import (
    DraftPreviewRead,
    EmailTaskApprovalRequest,
    EmailTaskOutreachConfigRequest,
    EmailTaskPrimaryMaterialRequest,
    EmailTaskScheduleRequest,
    MatchCalculationResultRead,
    TokenUsageRead,
)
from app.schemas.workspace import WorkspaceThreadRead
from app.services import llm_runtime
from app.services.task_runtime import (
    approve_draft_task,
    approve_and_schedule_task,
    approve_and_send_task,
    calculate_task_match_once,
    cancel_scheduled_task,
    continue_task_manually,
    MatchAnalysisAlreadyRunningError,
    regenerate_task_draft,
    preview_task_draft,
    start_follow_up_task,
    update_task_outreach_config,
    update_task_primary_material,
)


router = APIRouter(prefix="/api/email-tasks", tags=["email-tasks"])


@router.post("/{task_id}/regenerate-draft", response_model=WorkspaceThreadRead)
async def regenerate_draft(
    task_id: int,
    session: AsyncSession = Depends(get_async_session),
) -> WorkspaceThreadRead:
    return await _run_workspace_action(
        session,
        task_id,
        lambda: regenerate_task_draft(get_session_factory(), task_id),
    )


@router.post("/{task_id}/calculate-match", response_model=MatchCalculationResultRead)
async def calculate_match(
    task_id: int,
    session: AsyncSession = Depends(get_async_session),
) -> MatchCalculationResultRead:
    try:
        result = await calculate_task_match_once(get_session_factory(), task_id)
    except MatchAnalysisAlreadyRunningError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        detail = str(exc)
        status_code = 404 if "不存在" in detail else 400
        raise HTTPException(status_code=status_code, detail=detail) from exc

    thread = await build_workspace_thread(
        session,
        professor_id=result.professor_id,
        identity_id=result.identity_id,
        llm_profile_id=result.llm_profile_id,
    )
    return MatchCalculationResultRead(
        thread=thread,
        usage=TokenUsageRead(
            prompt_tokens=result.usage.prompt_tokens,
            completion_tokens=result.usage.completion_tokens,
            total_tokens=result.usage.total_tokens,
            cached_tokens=result.usage.cached_tokens,
        ),
        run_id=result.run_id,
    )


@router.post("/{task_id}/generate-draft", response_model=WorkspaceThreadRead)
async def generate_draft(
    task_id: int,
    session: AsyncSession = Depends(get_async_session),
) -> WorkspaceThreadRead:
    return await _run_workspace_action(
        session,
        task_id,
        lambda: regenerate_task_draft(get_session_factory(), task_id),
    )


@router.post("/{task_id}/draft-preview", response_model=DraftPreviewRead)
async def draft_preview(
    task_id: int,
) -> DraftPreviewRead:
    try:
        generation = await preview_task_draft(get_session_factory(), task_id)
    except ValueError as exc:
        detail = str(exc)
        status_code = 404 if "不存在" in detail else 400
        raise HTTPException(status_code=status_code, detail=detail) from exc

    return DraftPreviewRead(
        subject=generation.result.subject,
        body_text=generation.result.body_text,
        body_html=generation.result.body_html,
        rich_body=generation.result.rich_body,
        usage=TokenUsageRead(
            prompt_tokens=generation.usage.prompt_tokens if generation.usage else None,
            completion_tokens=generation.usage.completion_tokens if generation.usage else None,
            total_tokens=generation.usage.total_tokens if generation.usage else None,
            cached_tokens=generation.usage.cached_tokens if generation.usage else None,
        )
        if generation.usage is not None
        else None,
    )


@router.post("/{task_id}/primary-material", response_model=WorkspaceThreadRead)
async def change_primary_material(
    task_id: int,
    payload: EmailTaskPrimaryMaterialRequest,
    session: AsyncSession = Depends(get_async_session),
) -> WorkspaceThreadRead:
    return await _run_workspace_action(
        session,
        task_id,
        lambda: update_task_primary_material(
            get_session_factory(),
            task_id,
            payload.primary_material_id,
        ),
    )


@router.post("/{task_id}/outreach-config", response_model=WorkspaceThreadRead)
async def change_outreach_config(
    task_id: int,
    payload: EmailTaskOutreachConfigRequest,
    session: AsyncSession = Depends(get_async_session),
) -> WorkspaceThreadRead:
    return await _run_workspace_action(
        session,
        task_id,
        lambda: update_task_outreach_config(
            get_session_factory(),
            task_id,
            outreach_generation_mode=payload.outreach_generation_mode,
            outreach_template_subject=payload.outreach_template_subject,
            outreach_template_body_text=payload.outreach_template_body_text,
            outreach_template_body_html=payload.outreach_template_body_html,
        ),
    )


@router.post("/{task_id}/approve", response_model=WorkspaceThreadRead)
async def approve_draft(
    task_id: int,
    payload: EmailTaskApprovalRequest,
    session: AsyncSession = Depends(get_async_session),
) -> WorkspaceThreadRead:
    return await _run_workspace_action(
        session,
        task_id,
        lambda: approve_draft_task(get_session_factory(), task_id, payload),
    )


@router.post("/{task_id}/approve-and-send", response_model=WorkspaceThreadRead)
async def approve_and_send(
    task_id: int,
    payload: EmailTaskApprovalRequest,
    session: AsyncSession = Depends(get_async_session),
) -> WorkspaceThreadRead:
    return await _run_workspace_action(
        session,
        task_id,
        lambda: approve_and_send_task(get_session_factory(), task_id, payload),
    )


@router.post("/{task_id}/approve-and-schedule", response_model=WorkspaceThreadRead)
async def approve_and_schedule(
    task_id: int,
    payload: EmailTaskScheduleRequest,
    session: AsyncSession = Depends(get_async_session),
) -> WorkspaceThreadRead:
    return await _run_workspace_action(
        session,
        task_id,
        lambda: approve_and_schedule_task(get_session_factory(), task_id, payload),
    )


@router.post("/{task_id}/cancel-schedule", response_model=WorkspaceThreadRead)
async def cancel_schedule(
    task_id: int,
    session: AsyncSession = Depends(get_async_session),
) -> WorkspaceThreadRead:
    return await _run_workspace_action(
        session,
        task_id,
        lambda: cancel_scheduled_task(get_session_factory(), task_id),
    )


@router.post("/{task_id}/continue-manually", response_model=WorkspaceThreadRead)
async def continue_manually(
    task_id: int,
    session: AsyncSession = Depends(get_async_session),
) -> WorkspaceThreadRead:
    return await _run_workspace_action(
        session,
        task_id,
        lambda: continue_task_manually(get_session_factory(), task_id),
        response_scope="workspace",
    )


@router.post("/{task_id}/start-follow-up", response_model=WorkspaceThreadRead)
async def start_follow_up(
    task_id: int,
    session: AsyncSession = Depends(get_async_session),
) -> WorkspaceThreadRead:
    return await _run_workspace_action(
        session,
        task_id,
        lambda: start_follow_up_task(get_session_factory(), task_id),
        response_scope="workspace",
    )


async def _run_workspace_action(
    session: AsyncSession,
    task_id: int,
    action,
    *,
    response_scope: str = "task",
) -> WorkspaceThreadRead:
    try:
        professor_id, identity_id, llm_profile_id = await action()
    except llm_runtime.LLMRuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except ValueError as exc:
        detail = str(exc)
        status_code = 404 if "不存在" in detail else 400
        raise HTTPException(status_code=status_code, detail=detail) from exc

    session.expire_all()
    if response_scope == "workspace":
        return await build_workspace_thread(
            session,
            professor_id=professor_id,
            identity_id=identity_id,
            llm_profile_id=llm_profile_id,
        )
    return await build_workspace_thread_for_task(session, task_id=task_id)

