from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from typing import Literal, TypeVar

from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.agent_api_errors import AgentApiError
from app.core.agent_revisions import ensure_revision, revision_for
from app.core.database import get_session_factory
from app.models import EmailTask, IdentityProfile
from app.modules.community.public import CommunityMentorDataService
from app.modules.llm.public import LLMRuntimeError
from app.modules.workspace.public import (
    WorkspaceThreadRead,
    build_workspace_thread,
    build_workspace_thread_for_task,
)
from app.schemas.agent import AgentDraftRead, AgentWorkspaceThreadRead
from app.services.operation_logs import (
    record_operation_log,
    sanitize_user_visible_error,
)

PageItem = TypeVar("PageItem")


def get_agent_community_mentor_data_service() -> CommunityMentorDataService:
    return CommunityMentorDataService()


async def _run_agent_task_workspace_action(
    session: AsyncSession,
    *,
    task_id: int,
    command: str,
    workspace_task_id: int | None = None,
    action: Callable[[], Awaitable[tuple[int, int, int]]],
) -> AgentWorkspaceThreadRead:
    try:
        professor_id, identity_id, llm_profile_id = await action()
    except LLMRuntimeError as exc:
        raise AgentApiError(
            status_code=502,
            code="TASK_LLM_OPERATION_FAILED",
            message=sanitize_user_visible_error(exc),
            retryable=True,
            external_execution_unknown=True,
        ) from exc
    except ValueError as exc:
        raise _agent_task_error(exc) from exc

    session.expire_all()
    workspace = (
        await build_workspace_thread_for_task(session, task_id=workspace_task_id)
        if workspace_task_id is not None
        else await build_workspace_thread(
            session,
            professor_id=professor_id,
            identity_id=identity_id,
            llm_profile_id=llm_profile_id,
        )
    )
    await record_operation_log(
        session,
        category="agent_action",
        event_name=f"agent_cli.{command.replace('-', '_')}",
        entity_type="email_task",
        entity_id=str(task_id),
        metadata={
            "actor": "agent_cli",
            "command": command,
            "task_id": task_id,
            "professor_id": professor_id,
            "identity_id": identity_id,
            "llm_profile_id": llm_profile_id,
        },
    )
    return _serialize_agent_workspace_thread(workspace)


def _agent_task_error(error: ValueError) -> AgentApiError:
    message = str(error)
    return AgentApiError(
        status_code=404 if "不存在" in message else 409,
        code="TASK_OPERATION_REJECTED",
        message=message,
    )


def _slice_page(
    items: Sequence[PageItem],
    *,
    cursor: int,
    limit: int,
) -> tuple[Sequence[PageItem], str | None, bool]:
    has_more = len(items) > limit
    page = items[:limit]
    next_cursor = str(cursor + len(page)) if has_more else None
    return page, next_cursor, has_more


def _project_agent_collection_response(
    response: BaseModel,
    fields: str | None,
) -> BaseModel | Response:
    """Apply an additive DTO-only projection for Agent collection reads."""

    if fields is None:
        return response
    selected = list(
        dict.fromkeys(field.strip() for field in fields.split(",") if field.strip()),
    )
    if not selected or any(
        len(field) > 100 or not field.replace("_", "").isalnum() for field in selected
    ):
        raise AgentApiError(
            status_code=422,
            code="INVALID_FIELD_SELECTION",
            message="fields 必须是非空、逗号分隔的 DTO 字段名。",
        )
    payload = response.model_dump(mode="json")
    collection_key = next(
        (key for key in ("items", "records") if isinstance(payload.get(key), list)),
        None,
    )
    if collection_key is None:
        raise AgentApiError(
            status_code=422,
            code="FIELD_SELECTION_NOT_SUPPORTED",
            message="当前响应不是可投影集合。",
        )
    payload[collection_key] = [
        {
            field: item[field]
            for field in selected
            if isinstance(item, dict) and field in item
        }
        if isinstance(item, dict)
        else item
        for item in payload[collection_key]
    ]
    return JSONResponse(content=payload)


def _serialize_agent_workspace_thread(
    workspace: WorkspaceThreadRead,
) -> AgentWorkspaceThreadRead:
    def serialize_identity(identity: object) -> dict[str, object]:
        return {
            "id": getattr(identity, "id"),
            "name": getattr(identity, "name"),
            "profile_name": getattr(identity, "profile_name"),
            "sender_name": getattr(identity, "sender_name"),
            "email_address": getattr(identity, "email_address"),
        }

    def serialize_material(material: object | None) -> dict[str, object] | None:
        if material is None:
            return None
        return {
            "id": getattr(material, "id"),
            "display_name": getattr(material, "display_name"),
            "original_filename": getattr(material, "original_filename"),
            "mime_type": getattr(material, "mime_type"),
            "size_bytes": getattr(material, "size_bytes"),
            "material_type": getattr(material, "material_type"),
            "is_primary": getattr(material, "is_primary"),
            "created_at": getattr(material, "created_at"),
        }

    def sanitize_optional_error(value: object | None) -> str | None:
        if value is None or not str(value).strip():
            return None
        return sanitize_user_visible_error(value)

    task = workspace.current_task
    draft = task.draft
    return AgentWorkspaceThreadRead.model_validate(
        {
            "professor": {
                "id": workspace.professor.id,
                "name": workspace.professor.name,
                "email": workspace.professor.email,
                "title": workspace.professor.title,
                "university": workspace.professor.university,
                "school": workspace.professor.school,
                "research_direction": workspace.professor.research_direction,
                "recent_papers": workspace.professor.recent_papers,
                "profile_url": workspace.professor.profile_url,
            },
            "identity": serialize_identity(workspace.identity),
            "llm_profile": {
                "id": workspace.llm_profile.id,
                "name": workspace.llm_profile.name,
                "provider": workspace.llm_profile.provider,
                "model_name": workspace.llm_profile.model_name,
            },
            "material_options": [
                serialize_material(material) for material in workspace.material_options
            ],
            "current_task": {
                "id": task.id,
                "source": task.source,
                "batch_task_id": task.batch_task_id,
                "parent_task_id": task.parent_task_id,
                "status": task.status,
                "cancellation_reason": task.cancellation_reason,
                "can_continue_manually": task.can_continue_manually,
                "can_write_follow_up": task.can_write_follow_up,
                "outreach_template_id": task.outreach_template_id,
                "outreach_generation_mode": task.outreach_generation_mode,
                "outreach_template_subject": task.outreach_template_subject,
                "outreach_template_body_text": task.outreach_template_body_text,
                "outreach_template_body_html": task.outreach_template_body_html,
                "rendered_template_subject": task.rendered_template_subject,
                "rendered_template_body_text": task.rendered_template_body_text,
                "rendered_template_body_html": task.rendered_template_body_html,
                "match_score": task.match_score,
                "match_reason": task.match_reason,
                "fit_points": task.fit_points,
                "risk_points": task.risk_points,
                "match_keywords": task.match_keywords,
                "generated_subject": task.generated_subject,
                "generated_content_text": task.generated_content_text,
                "generated_content_html": task.generated_content_html,
                "approved_subject": task.approved_subject,
                "approved_body_text": task.approved_body_text,
                "approved_body_html": task.approved_body_html,
                "primary_material_id": task.primary_material_id,
                "primary_material": serialize_material(task.primary_material),
                "selected_material_ids": task.selected_material_ids,
                "approved_at": task.approved_at,
                "scheduled_at": task.scheduled_at,
                "last_send_attempt_at": task.last_send_attempt_at,
                "sent_at": task.sent_at,
                "last_rfc_message_id": task.last_rfc_message_id,
                "retry_count": task.retry_count,
                "last_error": sanitize_optional_error(task.last_error),
                "is_replied": task.is_replied,
                "estimated_prompt_tokens": task.estimated_prompt_tokens,
                "estimated_completion_tokens_upper_bound": (
                    task.estimated_completion_tokens_upper_bound
                ),
                "estimated_total_tokens_upper_bound": task.estimated_total_tokens_upper_bound,
                "last_draft_prompt_tokens": task.last_draft_prompt_tokens,
                "last_draft_completion_tokens": task.last_draft_completion_tokens,
                "last_draft_total_tokens": task.last_draft_total_tokens,
                "draft": {
                    "subject": draft.subject,
                    "body_text": draft.body_text,
                    "body_html": draft.body_html,
                    "source": draft.source,
                    "sendable": draft.sendable,
                    "editable": draft.editable,
                },
            },
            "match_source_identity": serialize_identity(
                workspace.match_source_identity,
            ),
            "match_source_material_id": workspace.match_source_material_id,
            "match_source_material_name": workspace.match_source_material_name,
            "match_result_id": workspace.match_result_id,
            "match_analyzed_at": workspace.match_analyzed_at,
            "match_uses_group_source": workspace.match_uses_group_source,
            "match_is_stale": workspace.match_is_stale,
            "messages": [
                {
                    "id": message.id,
                    "direction": message.direction,
                    "subject": message.subject,
                    "content": message.content,
                    "content_html": message.content_html,
                    "rfc_message_id": message.rfc_message_id,
                    "failure_summary": sanitize_optional_error(message.failure_summary),
                    "delivery_status": message.delivery_status,
                    "prompt_tokens": message.prompt_tokens,
                    "completion_tokens": message.completion_tokens,
                    "total_tokens": message.total_tokens,
                    "created_at": message.created_at,
                    "source_identities": [
                        serialize_identity(identity)
                        for identity in message.source_identities
                    ],
                }
                for message in workspace.messages
            ],
            "communication_scope": [
                serialize_identity(identity)
                for identity in workspace.communication_scope
            ],
            "sync_warnings": [
                {
                    "identity_id": warning.identity_id,
                    "identity_name": warning.identity_name,
                    "message": sanitize_user_visible_error(warning.message),
                }
                for warning in workspace.sync_warnings
            ],
        },
    )


def _identity_has_imap_config(identity: IdentityProfile) -> bool:
    return bool(
        identity.imap_host
        and str(identity.imap_host).strip()
        and identity.imap_port
        and identity.imap_username
        and str(identity.imap_username).strip()
        and identity.imap_password
    )


def _serialize_draft(task: EmailTask) -> AgentDraftRead:
    raw_mode = (task.outreach_generation_mode or "llm").lower()
    generation_mode: Literal["template", "ai_rewrite", "manual"]
    if raw_mode == "template":
        generation_mode = "template"
    elif raw_mode == "manual":
        generation_mode = "manual"
    else:
        generation_mode = "ai_rewrite"
    result = AgentDraftRead(
        task_id=task.id,
        source=task.source,
        batch_task_id=task.batch_task_id,
        parent_task_id=task.parent_task_id,
        identity_id=task.identity_id,
        professor_id=task.professor_id,
        professor_name=task.professor.name,
        professor_email=task.professor.email,
        llm_profile_id=task.llm_profile_id,
        status=task.status,
        generation_mode=generation_mode,
        template_id=task.outreach_template_id,
        reference_material_id=task.primary_material_id,
        attachment_material_ids=task.selected_material_ids or [],
        generated_subject=task.generated_subject,
        generated_body_text=task.generated_content_text,
        generated_body_html=task.generated_content_html,
        approved_subject=task.approved_subject,
        approved_body_text=task.approved_body_text,
        approved_body_html=task.approved_body_html,
        approved_at=task.approved_at,
        scheduled_at=task.scheduled_at,
        sent_at=task.sent_at,
        last_error=task.last_error,
        created_at=task.created_at,
        updated_at=task.updated_at,
    )
    return result.model_copy(update={"revision": revision_for(result)})


async def _ensure_draft_revision(task_id: int, if_revision: str | None) -> None:
    if not if_revision:
        return
    session_factory = get_session_factory()
    async with session_factory() as session:
        task = await session.scalar(
            select(EmailTask)
            .options(selectinload(EmailTask.professor))
            .where(EmailTask.id == task_id),
        )
        if task is None:
            raise AgentApiError(
                status_code=404,
                code="DRAFT_NOT_FOUND",
                message="未找到邮件任务。",
            )
        current = _serialize_draft(task)
    ensure_revision(
        if_revision,
        current.revision,
        resource="drafts",
        resource_id=task_id,
        latest=current.model_dump(mode="json"),
    )
