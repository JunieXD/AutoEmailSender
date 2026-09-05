from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import selectinload

import app.modules.llm.public as llm_runtime
from app.core.query_chunks import chunked_values, unique_positive_ids
from app.core.time import utc_now
from app.models import (
    BatchTask,
    BatchTaskStatus,
    EmailDirection,
    EmailLog,
    EmailTaskCancellationReason,
    EmailTaskSource,
    EmailTask,
    EmailTaskStatus,
    IdentityMaterial,
    IdentityProfile,
    LLMProfile,
    OutreachTemplate,
    Professor,
)
from app.modules.llm.public import (
    DELETED_LLM_PROFILE_MESSAGE,
    get_active_llm_profile,
    llm_profile_is_active,
    track_llm_profile_usage,
)
from app.modules.campaigns.public import (
    DRAFT_GENERATION_SOURCE_LLM,
    DRAFT_GENERATION_SOURCE_TEMPLATE,
    OUTREACH_GENERATION_MODE_TEMPLATE,
    build_outreach_template_snapshot_config,
    get_default_outreach_template_for_identity,
    get_outreach_template,
    get_outreach_template_defaults_validation_error,
    has_outreach_template_snapshot,
    render_outreach_template,
    resolve_outreach_template_config,
)
from app.modules.communications.public import (
    load_email_task as _load_email_task,
    record_email_task_log as _record_email_task_log,
)
from app.modules.identities.public import (
    ensure_material_extracted_text,
    material_can_be_primary,
)
from app.modules.system.public import get_runtime_settings
from app.services.operation_logs import record_operation_log
from app.services.rich_text import normalize_email_html, text_to_email_html

from .delivery import (
    _ensure_batch_task_has_future_window,
    _is_scheduled_batch_task,
    _is_user_removed_batch_item,
    dispatch_email_task,
)
from .schemas import (
    EmailTaskApprovalRequest,
    EmailTaskRewriteDraftRequest,
    EmailTaskScheduleRequest,
)

__all__ = [
    "BatchDraftApprovalConflictError",
    "WORKSPACE_DRAFT_REWRITE_INTERRUPTED_MESSAGE",
    "WORKSPACE_DRAFT_REWRITE_TIMEOUT",
    "WORKSPACE_DRAFT_REWRITE_TIMEOUT_MESSAGE",
    "WORKSPACE_DRAFT_REWRITE_TIMEOUT_SECONDS",
    "approve_and_schedule_task",
    "approve_and_send_task",
    "approve_draft_task",
    "approve_generated_batch_drafts",
    "cancel_scheduled_task",
    "continue_task_manually",
    "generate_task_draft",
    "preview_task_draft",
    "regenerate_task_draft",
    "restore_workspace_rewrite_source",
    "rewrite_task_draft",
    "save_task_draft",
    "start_follow_up_task",
    "update_task_outreach_config",
    "update_task_primary_material",
]


class BatchDraftApprovalConflictError(ValueError):
    """Raised when the confirmed batch review snapshot is no longer current."""


WORKSPACE_DRAFT_REWRITE_TIMEOUT = timedelta(minutes=5)

WORKSPACE_DRAFT_REWRITE_TIMEOUT_SECONDS = int(
    WORKSPACE_DRAFT_REWRITE_TIMEOUT.total_seconds()
)

WORKSPACE_DRAFT_REWRITE_TIMEOUT_MESSAGE = "AI 改写超时，请稍后重试"

WORKSPACE_DRAFT_REWRITE_INTERRUPTED_MESSAGE = "AI 改写已中断，请重试"

SAVE_DRAFT_ALLOWED_STATUSES = {
    EmailTaskStatus.DISCOVERED.value,
    EmailTaskStatus.MATCHED.value,
    EmailTaskStatus.DRAFT_FAILED.value,
    EmailTaskStatus.REVIEW_REQUIRED.value,
    EmailTaskStatus.APPROVED.value,
    EmailTaskStatus.SCHEDULED.value,
    EmailTaskStatus.SEND_FAILED.value,
}

MANUAL_DRAFT_CLAIMABLE_STATUSES = {
    EmailTaskStatus.DISCOVERED.value,
    EmailTaskStatus.MATCHED.value,
    EmailTaskStatus.DRAFT_FAILED.value,
    EmailTaskStatus.REVIEW_REQUIRED.value,
    EmailTaskStatus.APPROVED.value,
    EmailTaskStatus.SCHEDULED.value,
}


def _has_professor_research_direction(professor: Professor) -> bool:
    return bool((professor.research_direction or "").strip())


async def generate_task_draft(
    session_factory: async_sessionmaker[AsyncSession],
    task_id: int,
    *,
    force: bool,
    ignore_batch_status: bool = False,
    automatic_batch: bool = False,
    require_running_batch: bool = False,
    llm_profile_id: int | None = None,
    draft_claim_id: str | None = None,
) -> tuple[int, int, int]:
    async with session_factory() as session:
        task = await _load_email_task(session, task_id)
        if not task:
            raise ValueError(f"EmailTask {task_id} 不存在")
        task_identity = (task.professor_id, task.identity_id, task.llm_profile_id)
        runtime_llm_profile: LLMProfile | None = None
        if draft_claim_id is not None and task.draft_claim_id != draft_claim_id:
            return task_identity
        if (
            task.status == EmailTaskStatus.GENERATING_DRAFT.value
            and not automatic_batch
        ):
            raise ValueError("草稿正在后台生成，请稍后刷新")
        if (
            task.batch_task
            and task.batch_task.status != BatchTaskStatus.RUNNING.value
            and not ignore_batch_status
        ):
            if automatic_batch or require_running_batch:
                if not await _lock_current_batch_draft_claim(
                    session,
                    task,
                    draft_claim_id,
                ):
                    return task_identity
                _restore_or_cancel_interrupted_draft_generation(task)
                _clear_batch_draft_claim(task)
                await session.commit()
            return task_identity

        if not automatic_batch:
            claim_result = await session.execute(
                update(EmailTask)
                .where(
                    EmailTask.id == task_id,
                    EmailTask.status.in_(MANUAL_DRAFT_CLAIMABLE_STATUSES),
                )
                .values(
                    status=EmailTaskStatus.GENERATING_DRAFT.value,
                    draft_generation_previous_status=task.status,
                    last_error=None,
                    updated_at=utc_now(),
                ),
            )
            if claim_result.rowcount != 1:
                await session.rollback()
                current_status = await session.scalar(
                    select(EmailTask.status).where(EmailTask.id == task_id),
                )
                if current_status == EmailTaskStatus.GENERATING_DRAFT.value:
                    raise ValueError("草稿正在后台生成，请稍后刷新")
                return task_identity
            await session.commit()
            task = await _load_email_task(session, task_id)
            if not task:
                raise ValueError(f"EmailTask {task_id} 不存在")
            task_identity = (task.professor_id, task.identity_id, task.llm_profile_id)

        batch_task = task.batch_task

        try:
            fallback_template = (
                None
                if _task_has_outreach_template_snapshot(task)
                else await get_default_outreach_template_for_identity(
                    session,
                    task.identity,
                )
            )
            outreach_config = _resolve_draft_generation_outreach_config(
                task,
                fallback_template=fallback_template,
            )
            if outreach_config.generation_mode == OUTREACH_GENERATION_MODE_TEMPLATE:
                template_subject = _normalize_nullable_text(
                    outreach_config.subject_template
                )
                template_body = _normalize_nullable_text(
                    outreach_config.body_text_template
                )
                detail = get_outreach_template_defaults_validation_error(
                    template_subject,
                    template_body,
                )
                if detail:
                    raise ValueError(detail)
                rendered = render_outreach_template(
                    task.identity,
                    task.professor,
                    subject_template=template_subject,
                    body_text_template=template_body,
                    body_html_template=outreach_config.body_html_template,
                )
                subject = rendered.subject
                body_text = rendered.body_text
                body_html = rendered.body_html
                usage = None
                provider_payload = {
                    "source": OUTREACH_GENERATION_MODE_TEMPLATE,
                    "placeholders": rendered.placeholders,
                    "usage": None,
                }
            else:
                if task.primary_material is None:
                    if force:
                        raise ValueError("请选择 AI 写信参考材料后再生成草稿")
                    return task.professor_id, task.identity_id, task.llm_profile_id
                if not _has_professor_research_direction(task.professor):
                    raise ValueError("请先补充导师研究方向，再使用 AI 生成草稿")
                ensure_material_extracted_text(task.primary_material)
                template_subject = _normalize_nullable_text(
                    outreach_config.subject_template
                ) or (
                    _normalize_nullable_text(batch_task.email_subject)
                    if batch_task
                    else None
                )
                template_body = _normalize_nullable_text(
                    outreach_config.body_text_template
                ) or (
                    _normalize_nullable_text(batch_task.email_body)
                    if batch_task
                    else None
                )
                template_body_html = _normalize_nullable_text(
                    outreach_config.body_html_template
                )
                detail = get_outreach_template_defaults_validation_error(
                    template_subject,
                    template_body,
                )
                if detail:
                    raise ValueError(detail)

                runtime_llm_profile = await _resolve_runtime_llm_profile(
                    session, task, llm_profile_id
                )
                task_identity = (
                    task.professor_id,
                    task.identity_id,
                    runtime_llm_profile.id,
                )
                runtime_settings = await get_runtime_settings(session)
                with track_llm_profile_usage(
                    runtime_llm_profile.id,
                    "draft_generation_startup",
                ):
                    adaptation = await llm_runtime.ensure_llm_runtime_adaptation(
                        session, runtime_llm_profile
                    )
                rewrite_preferences = llm_runtime.DraftRewritePreferences(
                    draft_rewrite_intensity=runtime_settings.draft_rewrite_intensity,
                    draft_rewrite_tone=runtime_settings.draft_rewrite_tone,
                    draft_rewrite_formality=runtime_settings.draft_rewrite_formality,
                    draft_rewrite_length=runtime_settings.draft_rewrite_length,
                    draft_rewrite_specificity=runtime_settings.draft_rewrite_specificity,
                    draft_template_preservation=runtime_settings.draft_template_preservation,
                    draft_custom_instruction=runtime_settings.draft_custom_instruction,
                    intended_research_direction=runtime_settings.intended_research_direction,
                )
                generation = await llm_runtime.generate_draft_content(
                    identity=task.identity,
                    primary_material=task.primary_material,
                    llm_profile=runtime_llm_profile,
                    professor=task.professor,
                    available_materials=[],
                    custom_subject=template_subject,
                    custom_body=template_body,
                    custom_body_html=template_body_html,
                    max_tokens=runtime_settings.draft_max_tokens,
                    rewrite_preferences=rewrite_preferences,
                    session=session,
                    adaptation=adaptation,
                )
                subject = generation.result.subject
                body_text = generation.result.body_text
                body_html = generation.result.body_html
                usage = generation.usage
                provider_payload = {
                    "source": "llm",
                    "primary_material_id": task.primary_material_id,
                    "prompt_hash": generation.prompt_hash,
                    "stable_prefix_hash": generation.stable_prefix_hash,
                    "prompt_cache_key": generation.prompt_cache_key,
                    "usage": (
                        {
                            "prompt_tokens": usage.prompt_tokens,
                            "completion_tokens": usage.completion_tokens,
                            "cached_tokens": usage.cached_tokens,
                            "total_tokens": usage.total_tokens,
                        }
                        if usage is not None
                        else None
                    ),
                }
                if require_running_batch and task.batch_task_id is not None:
                    batch_status = await session.scalar(
                        select(BatchTask.status).where(
                            BatchTask.id == task.batch_task_id
                        ),
                    )
                    if batch_status != BatchTaskStatus.RUNNING.value:
                        if not await _lock_current_batch_draft_claim(
                            session,
                            task,
                            draft_claim_id,
                        ):
                            return task_identity
                        _restore_or_cancel_interrupted_draft_generation(
                            task, batch_status=batch_status
                        )
                        _clear_batch_draft_claim(task)
                        await session.commit()
                        return task.professor_id, task.identity_id, task.llm_profile_id
        except asyncio.CancelledError:
            if automatic_batch and draft_claim_id is not None:
                # The batch scheduler owns claim failure/release after it cancels
                # this worker. Roll back this session first so its read transaction
                # cannot block the scheduler's cleanup write on SQLite.
                await session.rollback()
                raise
            await session.refresh(task)
            if draft_claim_id is not None and task.draft_claim_id != draft_claim_id:
                raise
            if _is_user_removed_batch_item(task):
                raise
            if not await _lock_current_batch_draft_claim(
                session,
                task,
                draft_claim_id,
            ):
                raise
            batch_status = (
                await session.scalar(
                    select(BatchTask.status).where(BatchTask.id == task.batch_task_id)
                )
                if task.batch_task_id is not None
                else None
            )
            _restore_or_cancel_interrupted_draft_generation(
                task, batch_status=batch_status
            )
            await session.commit()
            raise
        except llm_runtime.LLMRuntimeError as exc:
            await session.refresh(task)
            if _is_user_removed_batch_item(task):
                raise
            if draft_claim_id is not None and (
                task.status != EmailTaskStatus.GENERATING_DRAFT.value
                or task.draft_claim_id != draft_claim_id
            ):
                return task_identity
            if not await _lock_current_batch_draft_claim(
                session,
                task,
                draft_claim_id,
            ):
                return task_identity
            task.last_error = str(exc)
            if automatic_batch:
                task.status = EmailTaskStatus.DRAFT_FAILED.value
                task.draft_generation_previous_status = None
                _clear_batch_draft_claim(task)
            else:
                task.status = (
                    task.draft_generation_previous_status
                    or EmailTaskStatus.DISCOVERED.value
                )
                task.draft_generation_previous_status = None
            task.updated_at = utc_now()
            await session.commit()
            if automatic_batch:
                return task.professor_id, task.identity_id, task.llm_profile_id
            raise
        except ValueError as exc:
            await session.refresh(task)
            if _is_user_removed_batch_item(task):
                raise
            if draft_claim_id is not None and (
                task.status != EmailTaskStatus.GENERATING_DRAFT.value
                or task.draft_claim_id != draft_claim_id
            ):
                return task_identity
            if not await _lock_current_batch_draft_claim(
                session,
                task,
                draft_claim_id,
            ):
                return task_identity
            task.last_error = str(exc)
            if automatic_batch:
                task.status = EmailTaskStatus.DRAFT_FAILED.value
                task.draft_generation_previous_status = None
                _clear_batch_draft_claim(task)
            else:
                task.status = (
                    task.draft_generation_previous_status
                    or EmailTaskStatus.DISCOVERED.value
                )
                task.draft_generation_previous_status = None
            task.updated_at = utc_now()
            await session.commit()
            if automatic_batch:
                return task.professor_id, task.identity_id, task.llm_profile_id
            raise

        await session.refresh(task)
        if (
            task.status != EmailTaskStatus.GENERATING_DRAFT.value
            or (draft_claim_id is not None and task.draft_claim_id != draft_claim_id)
            or task.cancellation_reason
            == EmailTaskCancellationReason.USER_REMOVED.value
        ):
            return task_identity
        if not await _lock_current_batch_draft_claim(
            session,
            task,
            draft_claim_id,
        ):
            return task_identity

        if runtime_llm_profile is not None:
            task.llm_profile_id = runtime_llm_profile.id
        if not _task_has_outreach_template_snapshot(task):
            task.outreach_template_id = (
                fallback_template.id if fallback_template is not None else None
            )
            task.outreach_template_snapshot_version = 1
            task.outreach_generation_mode = outreach_config.generation_mode
            task.outreach_template_subject = _normalize_nullable_text(
                outreach_config.subject_template,
            )
            task.outreach_template_body_text = _normalize_nullable_text(
                outreach_config.body_text_template,
            )
            task.outreach_template_body_html = _normalize_nullable_text(
                outreach_config.body_html_template,
            )
        task.generated_subject = subject
        task.generated_content_text = body_text
        task.generated_content_html = body_html
        task.draft_generation_source = (
            DRAFT_GENERATION_SOURCE_TEMPLATE
            if outreach_config.generation_mode == OUTREACH_GENERATION_MODE_TEMPLATE
            else DRAFT_GENERATION_SOURCE_LLM
        )
        task.draft_fallback_reason = None
        task.status = EmailTaskStatus.REVIEW_REQUIRED.value
        task.draft_generation_previous_status = None
        if automatic_batch:
            _clear_batch_draft_claim(task)
        task.updated_at = utc_now()
        task.last_error = None

        session.add(
            EmailLog(
                email_task_id=task.id,
                identity_id=task.identity_id,
                llm_profile_id=task.llm_profile_id,
                professor_id=task.professor_id,
                direction=EmailDirection.DRAFT.value,
                subject=subject,
                content=body_text,
                content_html=body_html,
                provider_payload=provider_payload,
            ),
        )
        await _record_email_task_log(
            session,
            task,
            "email_task.draft_generated",
            metadata={
                "generation_mode": outreach_config.generation_mode,
                "has_usage": usage is not None,
                "prompt_tokens": usage.prompt_tokens if usage is not None else None,
                "completion_tokens": usage.completion_tokens
                if usage is not None
                else None,
                "cached_tokens": usage.cached_tokens if usage is not None else None,
                "total_tokens": usage.total_tokens if usage is not None else None,
                "prompt_hash": provider_payload.get("prompt_hash"),
                "stable_prefix_hash": provider_payload.get("stable_prefix_hash"),
                "prompt_cache_key": provider_payload.get("prompt_cache_key"),
                "selected_material_ids": task.selected_material_ids,
            },
        )
        await session.commit()
        return task_identity


async def regenerate_task_draft(
    session_factory: async_sessionmaker[AsyncSession],
    task_id: int,
    *,
    llm_profile_id: int | None = None,
) -> tuple[int, int, int]:
    return await generate_task_draft(
        session_factory,
        task_id,
        force=True,
        llm_profile_id=llm_profile_id,
    )


async def rewrite_task_draft(
    session_factory: async_sessionmaker[AsyncSession],
    task_id: int,
    payload: EmailTaskRewriteDraftRequest,
) -> tuple[int, int, int]:
    source_subject = (payload.subject or "").strip() or None
    source_body_text = payload.body_text.strip()
    source_body_html = (payload.body_html or "").strip()
    if source_body_html:
        rendered_source_html = normalize_email_html(source_body_html)
        source_body_html = rendered_source_html.html
        if not source_body_text:
            source_body_text = rendered_source_html.text
    if not source_body_text and not source_body_html:
        raise ValueError("先写入正文或配置默认模板后再使用 AI 改写")

    async with session_factory() as session:
        task = await _load_email_task(session, task_id)
        if not task:
            raise ValueError(f"EmailTask {task_id} 不存在")
        _ensure_task_allows_legacy_manual_actions(task)
        if task.status == EmailTaskStatus.GENERATING_DRAFT.value:
            raise ValueError("AI 正在改写当前草稿，请稍后刷新")
        if task.primary_material is None:
            raise ValueError("请选择 AI 写信参考材料后再使用 AI 改写")
        if not _has_professor_research_direction(task.professor):
            raise ValueError("请先补充导师研究方向，再使用 AI 改写")
        if payload.selected_material_ids is None:
            selected_material_ids = (
                list(task.selected_material_ids)
                if task.selected_material_ids is not None
                else None
            )
        else:
            selected_material_ids = list(payload.selected_material_ids)
        await _validate_selected_material_ids(
            session, selected_material_ids
        )
        ensure_material_extracted_text(task.primary_material)

        runtime_llm_profile = await _resolve_runtime_llm_profile(
            session, task, payload.llm_profile_id
        )
        with track_llm_profile_usage(
            runtime_llm_profile.id,
            "draft_rewrite_startup",
        ):
            adaptation = await llm_runtime.ensure_llm_runtime_adaptation(
                session, runtime_llm_profile
            )
            runtime_settings = await get_runtime_settings(session)
            rewrite_preferences = llm_runtime.DraftRewritePreferences(
                draft_rewrite_intensity=runtime_settings.draft_rewrite_intensity,
                draft_rewrite_tone=runtime_settings.draft_rewrite_tone,
                draft_rewrite_formality=runtime_settings.draft_rewrite_formality,
                draft_rewrite_length=runtime_settings.draft_rewrite_length,
                draft_rewrite_specificity=runtime_settings.draft_rewrite_specificity,
                draft_template_preservation=(
                    runtime_settings.draft_template_preservation
                ),
                draft_custom_instruction=runtime_settings.draft_custom_instruction,
                intended_research_direction=(
                    runtime_settings.intended_research_direction
                ),
            )
            identity = task.identity
            primary_material = task.primary_material
            professor = task.professor
            task_identity = (task.professor_id, task.identity_id, runtime_llm_profile.id)

            now = utc_now()
            previous_status = task.status or EmailTaskStatus.REVIEW_REQUIRED.value
            claim_result = await session.execute(
                update(EmailTask)
                .where(
                    EmailTask.id == task.id,
                    EmailTask.status != EmailTaskStatus.GENERATING_DRAFT.value,
                )
                .values(
                    llm_profile_id=runtime_llm_profile.id,
                    draft_generation_previous_status=previous_status,
                    draft_generation_started_at=now,
                    draft_rewrite_source_subject=source_subject,
                    draft_rewrite_source_body_text=source_body_text,
                    draft_rewrite_source_body_html=source_body_html or None,
                    draft_rewrite_source_selected_material_ids=selected_material_ids,
                    selected_material_ids=selected_material_ids,
                    status=EmailTaskStatus.GENERATING_DRAFT.value,
                    last_error=None,
                    updated_at=now,
                )
            )
            if claim_result.rowcount != 1:
                await session.rollback()
                raise ValueError("AI 正在改写当前草稿，请稍后刷新")
            await session.commit()
            await session.refresh(task)

        try:
            generation = await asyncio.wait_for(
                llm_runtime.generate_draft_content(
                    identity=identity,
                    primary_material=primary_material,
                    llm_profile=runtime_llm_profile,
                    professor=professor,
                    available_materials=[],
                    custom_subject=source_subject,
                    custom_body=source_body_text,
                    custom_body_html=source_body_html or None,
                    max_tokens=runtime_settings.draft_max_tokens,
                    rewrite_preferences=rewrite_preferences,
                    session=session,
                    adaptation=adaptation,
                ),
                timeout=WORKSPACE_DRAFT_REWRITE_TIMEOUT_SECONDS,
            )
        except asyncio.TimeoutError as exc:
            restore_workspace_rewrite_source(
                task, WORKSPACE_DRAFT_REWRITE_TIMEOUT_MESSAGE
            )
            await session.commit()
            raise ValueError(WORKSPACE_DRAFT_REWRITE_TIMEOUT_MESSAGE) from exc
        except llm_runtime.LLMRuntimeError as exc:
            await session.refresh(task)
            restore_workspace_rewrite_source(task, str(exc))
            await session.commit()
            raise
        except ValueError as exc:
            await session.refresh(task)
            restore_workspace_rewrite_source(task, str(exc))
            await session.commit()
            raise

        await session.refresh(task)
        if task.status != EmailTaskStatus.GENERATING_DRAFT.value:
            return task_identity

        result = generation.result
        usage = generation.usage
        task.generated_subject = result.subject
        task.generated_content_text = result.body_text
        task.generated_content_html = result.body_html
        task.draft_generation_source = DRAFT_GENERATION_SOURCE_LLM
        task.draft_fallback_reason = None
        task.approved_subject = None
        task.approved_body_text = None
        task.approved_body_html = None
        task.approved_at = None
        task.status = EmailTaskStatus.REVIEW_REQUIRED.value
        task.draft_generation_previous_status = None
        task.draft_generation_started_at = None
        task.updated_at = utc_now()
        task.last_error = None
        provider_payload = {
            "source": "workspace_rewrite",
            "primary_material_id": task.primary_material_id,
            "prompt_hash": generation.prompt_hash,
            "stable_prefix_hash": generation.stable_prefix_hash,
            "prompt_cache_key": generation.prompt_cache_key,
            "usage": (
                {
                    "prompt_tokens": usage.prompt_tokens,
                    "completion_tokens": usage.completion_tokens,
                    "cached_tokens": usage.cached_tokens,
                    "total_tokens": usage.total_tokens,
                }
                if usage is not None
                else None
            ),
        }
        session.add(
            EmailLog(
                email_task_id=task.id,
                identity_id=task.identity_id,
                llm_profile_id=task.llm_profile_id,
                professor_id=task.professor_id,
                direction=EmailDirection.DRAFT.value,
                subject=result.subject,
                content=result.body_text or "",
                content_html=result.body_html,
                provider_payload=provider_payload,
            ),
        )
        await _record_email_task_log(
            session,
            task,
            "email_task.draft_rewritten",
            metadata={
                "has_usage": usage is not None,
                "prompt_tokens": usage.prompt_tokens if usage is not None else None,
                "completion_tokens": usage.completion_tokens
                if usage is not None
                else None,
                "cached_tokens": usage.cached_tokens if usage is not None else None,
                "total_tokens": usage.total_tokens if usage is not None else None,
                "prompt_hash": generation.prompt_hash,
                "stable_prefix_hash": generation.stable_prefix_hash,
                "prompt_cache_key": generation.prompt_cache_key,
                "selected_material_ids": task.selected_material_ids,
            },
        )
        await session.commit()
        return task_identity


async def preview_task_draft(
    session_factory: async_sessionmaker[AsyncSession],
    task_id: int,
    *,
    llm_profile_id: int | None = None,
) -> llm_runtime.GeneratedDraftContent:
    async with session_factory() as session:
        task = await _load_email_task(session, task_id)
        if not task:
            raise ValueError(f"EmailTask {task_id} 不存在")
        runtime_llm_profile = await _resolve_runtime_llm_profile(
            session, task, llm_profile_id
        )

        fallback_template = (
            None
            if _task_has_outreach_template_snapshot(task)
            else await get_default_outreach_template_for_identity(
                session,
                task.identity,
            )
        )
        outreach_config = _resolve_draft_generation_outreach_config(
            task,
            fallback_template=fallback_template,
        )
        if outreach_config.generation_mode == OUTREACH_GENERATION_MODE_TEMPLATE:
            raise ValueError("模板模式不需要 AI 草稿预览")
        if task.primary_material is None:
            raise ValueError("请选择 AI 写信参考材料后再预览草稿")
        if not _has_professor_research_direction(task.professor):
            raise ValueError("请先补充导师研究方向，再使用 AI 生成草稿")

        ensure_material_extracted_text(task.primary_material)
        template_subject = _normalize_nullable_text(
            outreach_config.subject_template
        ) or (
            _normalize_nullable_text(task.batch_task.email_subject)
            if task.batch_task
            else None
        )
        template_body = _normalize_nullable_text(
            outreach_config.body_text_template
        ) or (
            _normalize_nullable_text(task.batch_task.email_body)
            if task.batch_task
            else None
        )
        template_body_html = _normalize_nullable_text(
            outreach_config.body_html_template
        )
        detail = get_outreach_template_defaults_validation_error(
            template_subject,
            template_body,
        )
        if detail:
            raise ValueError(detail)

        with track_llm_profile_usage(runtime_llm_profile.id, "draft_preview"):
            runtime_settings = await get_runtime_settings(session)
            adaptation = await llm_runtime.ensure_llm_runtime_adaptation(
                session, runtime_llm_profile
            )
            rewrite_preferences = llm_runtime.DraftRewritePreferences(
                draft_rewrite_intensity=runtime_settings.draft_rewrite_intensity,
                draft_rewrite_tone=runtime_settings.draft_rewrite_tone,
                draft_rewrite_formality=runtime_settings.draft_rewrite_formality,
                draft_rewrite_length=runtime_settings.draft_rewrite_length,
                draft_rewrite_specificity=runtime_settings.draft_rewrite_specificity,
                draft_template_preservation=(
                    runtime_settings.draft_template_preservation
                ),
                draft_custom_instruction=runtime_settings.draft_custom_instruction,
                intended_research_direction=(
                    runtime_settings.intended_research_direction
                ),
            )
            return await llm_runtime.generate_draft_content(
                identity=task.identity,
                primary_material=task.primary_material,
                llm_profile=runtime_llm_profile,
                professor=task.professor,
                available_materials=[],
                custom_subject=template_subject,
                custom_body=template_body,
                custom_body_html=template_body_html,
                rewrite_preferences=rewrite_preferences,
                session=session,
                adaptation=adaptation,
            )


async def update_task_primary_material(
    session_factory: async_sessionmaker[AsyncSession],
    task_id: int,
    primary_material_id: int,
) -> tuple[int, int, int]:
    async with session_factory() as session:
        task = await _load_email_task(session, task_id)
        if not task:
            raise ValueError(f"EmailTask {task_id} 不存在")
        _ensure_task_allows_legacy_manual_actions(task)
        _ensure_task_not_generating_for_workspace_change(task)
        if task.status in {
            EmailTaskStatus.SENT.value,
            EmailTaskStatus.REPLY_DETECTED.value,
        }:
            raise ValueError("已发送或已回信任务不能再切换 AI 写信参考材料")

        material = await _validate_primary_material_id(
            session, primary_material_id
        )
        task.primary_material_id = material.id
        task.approved_subject = None
        task.approved_body_text = None
        task.approved_body_html = None
        task.approved_at = None
        task.scheduled_at = None
        task.last_error = None
        task.updated_at = utc_now()
        await _record_email_task_log(
            session,
            task,
            "email_task.primary_material_updated",
            metadata={"primary_material_id": task.primary_material_id},
        )
        await session.commit()

    return await generate_task_draft(
        session_factory,
        task_id,
        force=True,
        ignore_batch_status=True,
    )


async def update_task_outreach_config(
    session_factory: async_sessionmaker[AsyncSession],
    task_id: int,
    *,
    outreach_generation_mode: str,
    outreach_template_id: int | None = None,
    template_selection_explicit: bool = False,
    outreach_template_subject: str | None = None,
    outreach_template_body_text: str | None = None,
    outreach_template_body_html: str | None = None,
) -> tuple[int, int, int]:
    async with session_factory() as session:
        task = await _load_email_task(session, task_id)
        if not task:
            raise ValueError(f"EmailTask {task_id} 不存在")
        _ensure_task_allows_legacy_manual_actions(task)
        _ensure_task_not_generating_for_workspace_change(task)
        if task.status in {
            EmailTaskStatus.SENDING.value,
            EmailTaskStatus.SENT.value,
            EmailTaskStatus.REPLY_DETECTED.value,
        }:
            raise ValueError("正在发送、已发送或已回信任务不能再切换本次发信模式")

        if template_selection_explicit:
            selected_template = (
                await get_outreach_template(session, outreach_template_id)
                if outreach_template_id is not None
                else None
            )
        else:
            selected_template = await get_default_outreach_template_for_identity(
                session,
                task.identity,
            )
        previous_snapshot = (
            task.outreach_generation_mode,
            _normalize_nullable_text(task.outreach_template_subject),
            _normalize_nullable_text(task.outreach_template_body_text),
            _normalize_nullable_text(task.outreach_template_body_html),
        )
        if template_selection_explicit and selected_template is None:
            unlinked_snapshot = build_outreach_template_snapshot_config(
                generation_mode=outreach_generation_mode
                or task.outreach_generation_mode,
                subject_template=(
                    outreach_template_subject
                    if outreach_template_subject is not None
                    else task.outreach_template_subject
                ),
                body_text_template=(
                    outreach_template_body_text
                    if outreach_template_body_text is not None
                    else task.outreach_template_body_text
                ),
                body_html_template=(
                    outreach_template_body_html
                    if outreach_template_body_html is not None
                    else task.outreach_template_body_html
                ),
            )
            snapshot = {
                "outreach_generation_mode": unlinked_snapshot.generation_mode,
                "outreach_template_subject": _normalize_nullable_text(
                    unlinked_snapshot.subject_template,
                ),
                "outreach_template_body_text": _normalize_nullable_text(
                    unlinked_snapshot.body_text_template,
                ),
                "outreach_template_body_html": _normalize_nullable_text(
                    unlinked_snapshot.body_html_template,
                ),
            }
        else:
            snapshot = _build_task_outreach_snapshot(
                task.identity,
                template=selected_template,
                outreach_generation_mode=outreach_generation_mode,
                outreach_template_subject=outreach_template_subject,
                outreach_template_body_text=outreach_template_body_text,
                outreach_template_body_html=outreach_template_body_html,
                validate_ready=False,
            )
        next_snapshot = (
            snapshot["outreach_generation_mode"],
            snapshot["outreach_template_subject"],
            snapshot["outreach_template_body_text"],
            snapshot["outreach_template_body_html"],
        )
        provenance_only_unlink = bool(
            template_selection_explicit
            and selected_template is None
            and previous_snapshot == next_snapshot
        )
        task.outreach_generation_mode = next_snapshot[0]
        task.outreach_template_subject = next_snapshot[1]
        task.outreach_template_body_text = next_snapshot[2]
        task.outreach_template_body_html = next_snapshot[3]
        task.outreach_template_id = (
            selected_template.id if selected_template is not None else None
        )
        task.outreach_template_snapshot_version = 1
        if not provenance_only_unlink:
            task.generated_subject = None
            task.generated_content_text = None
            task.generated_content_html = None
            task.draft_generation_source = None
            task.draft_fallback_reason = None
            task.approved_subject = None
            task.approved_body_text = None
            task.approved_body_html = None
            task.approved_at = None
            task.scheduled_at = None
            task.draft_rewrite_source_subject = None
            task.draft_rewrite_source_body_text = None
            task.draft_rewrite_source_body_html = None
            task.draft_rewrite_source_selected_material_ids = None
            task.draft_generation_previous_status = None
            task.draft_generation_started_at = None
            if task.status != EmailTaskStatus.CANCELED.value:
                task.status = (
                    EmailTaskStatus.MATCHED.value
                    if _task_has_match_result(task)
                    else EmailTaskStatus.DISCOVERED.value
                )
            task.last_error = None
        task.updated_at = utc_now()
        await _record_email_task_log(
            session,
            task,
            "email_task.outreach_config_updated",
            metadata={"outreach_generation_mode": task.outreach_generation_mode},
        )
        await session.commit()
        return task.professor_id, task.identity_id, task.llm_profile_id


async def approve_and_send_task(
    session_factory: async_sessionmaker[AsyncSession],
    task_id: int,
    payload: EmailTaskApprovalRequest,
) -> tuple[int, int, int]:
    async with session_factory() as session:
        task = await _load_email_task(session, task_id)
        if not task:
            raise ValueError(f"EmailTask {task_id} 不存在")
        _ensure_task_allows_legacy_manual_actions(task)
        _ensure_task_allows_approval(task)
        _ensure_batch_task_has_future_window(task)
        await _snapshot_approval(session, task, payload)
        if task.scheduled_at is not None:
            task.last_scheduled_at = task.scheduled_at
        task.status = EmailTaskStatus.APPROVED.value
        task.scheduled_at = None
        task.schedule_canceled_at = None
        await _record_email_task_log(
            session,
            task,
            "email_task.approved",
            metadata={"selected_material_ids": task.selected_material_ids},
        )
        await session.commit()
        professor_id = task.professor_id
        identity_id = task.identity_id
        llm_profile_id = task.llm_profile_id

    await dispatch_email_task(
        session_factory,
        task_id,
        respect_identity_send_window=False,
    )
    return professor_id, identity_id, llm_profile_id


async def approve_generated_batch_drafts(
    session_factory: async_sessionmaker[AsyncSession],
    batch_task_id: int,
    item_ids: list[int],
) -> int:
    if not item_ids:
        raise BatchDraftApprovalConflictError("请至少选择一封待审核草稿。")

    async with session_factory() as session:
        requested_item_ids = unique_positive_ids(item_ids)
        tasks: list[EmailTask] = []
        for item_id_chunk in chunked_values(requested_item_ids):
            tasks.extend(
                (
                    await session.execute(
                        select(EmailTask)
                        .options(selectinload(EmailTask.batch_task))
                        .where(
                            EmailTask.id.in_(item_id_chunk),
                            EmailTask.batch_task_id == batch_task_id,
                            EmailTask.source == EmailTaskSource.BATCH.value,
                        )
                        .order_by(EmailTask.id.asc())
                        .with_for_update(),
                    )
                )
                .scalars()
                .unique(),
            )
        tasks.sort(key=lambda task: task.id)
        if len(tasks) != len(item_ids):
            raise BatchDraftApprovalConflictError(
                "待审核草稿列表已发生变化，请刷新后重新确认。",
            )

        batch_task = tasks[0].batch_task
        if (
            batch_task is None
            or batch_task.deleted_at is not None
            or batch_task.status
            not in {
                BatchTaskStatus.RUNNING.value,
                BatchTaskStatus.PAUSED.value,
            }
        ):
            raise BatchDraftApprovalConflictError(
                "批量任务状态已发生变化，请刷新后重新确认。",
            )

        for task in tasks:
            if (
                task.status != EmailTaskStatus.REVIEW_REQUIRED.value
                or task.batch_send_canceled_at is not None
                or not (
                    (task.generated_content_text or "").strip()
                    or (task.generated_content_html or "").strip()
                )
            ):
                raise BatchDraftApprovalConflictError(
                    "待审核草稿列表已发生变化，请刷新后重新确认。",
                )
            _ensure_task_allows_legacy_manual_actions(task)
            _ensure_task_allows_approval(task)
            _ensure_batch_task_has_future_window(task)

        for task in tasks:
            await _snapshot_approval(
                session,
                task,
                EmailTaskApprovalRequest(
                    subject=task.generated_subject,
                    body_text=task.generated_content_text or "",
                    body_html=task.generated_content_html,
                    selected_material_ids=task.selected_material_ids,
                ),
            )
            if _is_scheduled_batch_task(task) and task.scheduled_at is not None:
                task.status = EmailTaskStatus.SCHEDULED.value
            else:
                task.status = EmailTaskStatus.APPROVED.value
                task.scheduled_at = None
            await _record_email_task_log(
                session,
                task,
                "email_task.approved",
                metadata={
                    "selected_material_ids": task.selected_material_ids,
                    "approval_method": "bulk",
                },
            )

        await record_operation_log(
            session,
            category="email",
            event_name="batch_task.drafts_bulk_approved",
            entity_type="batch_task",
            entity_id=str(batch_task_id),
            metadata={"approved_count": len(tasks)},
        )
        await session.commit()
        return len(tasks)


async def approve_draft_task(
    session_factory: async_sessionmaker[AsyncSession],
    task_id: int,
    payload: EmailTaskApprovalRequest,
) -> tuple[int, int, int]:
    async with session_factory() as session:
        task = await _load_email_task(session, task_id)
        if not task:
            raise ValueError(f"EmailTask {task_id} 不存在")
        _ensure_task_allows_legacy_manual_actions(task)
        _ensure_task_allows_approval(task)
        _ensure_batch_task_has_future_window(task)
        await _snapshot_approval(session, task, payload)
        if _is_scheduled_batch_task(task) and task.scheduled_at is not None:
            task.status = EmailTaskStatus.SCHEDULED.value
        else:
            task.status = EmailTaskStatus.APPROVED.value
            task.scheduled_at = None
        await _record_email_task_log(
            session,
            task,
            "email_task.approved",
            metadata={"selected_material_ids": task.selected_material_ids},
        )
        await session.commit()
        return task.professor_id, task.identity_id, task.llm_profile_id


async def save_task_draft(
    session_factory: async_sessionmaker[AsyncSession],
    task_id: int,
    payload: EmailTaskApprovalRequest,
) -> tuple[int, int, int]:
    async with session_factory() as session:
        task = await _load_email_task(session, task_id)
        if not task:
            raise ValueError(f"EmailTask {task_id} 不存在")
        _ensure_task_allows_legacy_manual_actions(task)
        _ensure_task_allows_draft_save(task)
        await _snapshot_saved_draft(session, task, payload)
        await _record_email_task_log(
            session,
            task,
            "email_task.draft_saved",
            metadata={"selected_material_ids": task.selected_material_ids},
        )
        await session.commit()
        return task.professor_id, task.identity_id, task.llm_profile_id


async def approve_and_schedule_task(
    session_factory: async_sessionmaker[AsyncSession],
    task_id: int,
    payload: EmailTaskScheduleRequest,
) -> tuple[int, int, int]:
    async with session_factory() as session:
        task = await _load_email_task(session, task_id)
        if not task:
            raise ValueError(f"EmailTask {task_id} 不存在")
        _ensure_task_allows_legacy_manual_actions(task)
        _ensure_task_allows_approval(task)
        _ensure_batch_task_has_future_window(task)
        await _snapshot_approval(session, task, payload)
        if task.scheduled_at is not None:
            task.last_scheduled_at = task.scheduled_at
        task.status = EmailTaskStatus.SCHEDULED.value
        task.scheduled_at = payload.scheduled_at.astimezone(UTC)
        task.schedule_canceled_at = None
        task.cancellation_reason = None
        task.updated_at = utc_now()
        await _record_email_task_log(
            session,
            task,
            "email_task.approved_and_scheduled",
            metadata={
                "scheduled_at": task.scheduled_at.isoformat()
                if task.scheduled_at
                else None,
                "selected_material_ids": task.selected_material_ids,
            },
        )
        await session.commit()
        return task.professor_id, task.identity_id, task.llm_profile_id


async def cancel_scheduled_task(
    session_factory: async_sessionmaker[AsyncSession],
    task_id: int,
) -> tuple[int, int, int]:
    async with session_factory() as session:
        task = await _load_email_task(session, task_id)
        if not task:
            raise ValueError(f"EmailTask {task_id} 不存在")
        _ensure_task_allows_legacy_manual_actions(task)
        if task.status not in {
            EmailTaskStatus.SCHEDULED.value,
            EmailTaskStatus.SCHEDULE_MISSED.value,
            EmailTaskStatus.SEND_FAILED.value,
        }:
            raise ValueError("当前邮件状态不能取消定时")
        now = utc_now()
        task.last_scheduled_at = task.scheduled_at or task.last_scheduled_at
        task.status = EmailTaskStatus.REVIEW_REQUIRED.value
        task.scheduled_at = None
        task.schedule_canceled_at = now
        task.cancellation_reason = None
        task.updated_at = now
        await _record_email_task_log(session, task, "email_task.schedule_canceled")
        await session.commit()
        return task.professor_id, task.identity_id, task.llm_profile_id


async def continue_task_manually(
    session_factory: async_sessionmaker[AsyncSession],
    task_id: int,
) -> tuple[int, int, int]:
    async with session_factory() as session:
        task = await _load_email_task(session, task_id)
        if not task:
            raise ValueError(f"EmailTask {task_id} 不存在")
        _ensure_task_allows_new_contact(task)
        await _ensure_no_manual_child_exists(session, task.id)
        if (
            task.status != EmailTaskStatus.CANCELED.value
            or task.cancellation_reason
            != EmailTaskCancellationReason.BATCH_STOPPED.value
        ):
            raise ValueError(
                "只有 canceled 且 cancellation_reason 为 batch_stopped 的任务支持继续联系"
            )

        professor_id = task.professor_id
        identity_id = task.identity_id
        llm_profile_id = task.llm_profile_id
        parent_task_id = task.id
        fallback_template = (
            None
            if _task_has_outreach_template_snapshot(task)
            else await get_default_outreach_template_for_identity(
                session,
                task.identity,
            )
        )
        child_task = _create_manual_child_task(
            task,
            reuse_existing_draft=True,
            fallback_template=fallback_template,
        )
        session.add(child_task)
        try:
            await session.flush()
        except IntegrityError:
            await session.rollback()
            existing_child_id = await _get_manual_child_task_id(session, parent_task_id)
            if existing_child_id is not None:
                return professor_id, identity_id, llm_profile_id
            raise
        await _record_email_task_log(
            session,
            child_task,
            "email_task.continued_manually",
            metadata={"parent_task_id": parent_task_id},
        )
        await _commit_manual_child_task(session)
        return professor_id, identity_id, llm_profile_id


async def start_follow_up_task(
    session_factory: async_sessionmaker[AsyncSession],
    task_id: int,
) -> tuple[int, int, int]:
    async with session_factory() as session:
        task = await _load_email_task(session, task_id)
        if not task:
            raise ValueError(f"EmailTask {task_id} 不存在")
        _ensure_task_allows_new_contact(task)
        await _ensure_no_manual_child_exists(session, task.id)
        if task.status not in {
            EmailTaskStatus.SENT.value,
            EmailTaskStatus.REPLY_DETECTED.value,
        }:
            raise ValueError("只有 sent 或 reply_detected 的任务支持发起跟进")

        professor_id = task.professor_id
        identity_id = task.identity_id
        llm_profile_id = task.llm_profile_id
        parent_task_id = task.id
        fallback_template = (
            None
            if _task_has_outreach_template_snapshot(task)
            else await get_default_outreach_template_for_identity(
                session,
                task.identity,
            )
        )
        child_task = _create_manual_child_task(
            task,
            reuse_existing_draft=False,
            minimum_status=EmailTaskStatus.MATCHED.value,
            fallback_template=fallback_template,
        )
        session.add(child_task)
        try:
            await session.flush()
        except IntegrityError:
            await session.rollback()
            existing_child_id = await _get_manual_child_task_id(session, parent_task_id)
            if existing_child_id is not None:
                return professor_id, identity_id, llm_profile_id
            raise
        await _record_email_task_log(
            session,
            child_task,
            "email_task.follow_up_started",
            metadata={"parent_task_id": parent_task_id},
        )
        await _commit_manual_child_task(session)
        return professor_id, identity_id, llm_profile_id


async def _snapshot_approval(
    session: AsyncSession,
    task: EmailTask,
    payload: EmailTaskApprovalRequest,
) -> None:
    await _validate_selected_material_ids(
        session, payload.selected_material_ids
    )

    task.approved_subject = (payload.subject or task.generated_subject or "").strip()
    if payload.body_html:
        rendered = normalize_email_html(payload.body_html)
    else:
        rendered = text_to_email_html(payload.body_text)
    task.approved_body_text = rendered.text
    task.approved_body_html = rendered.html
    if payload.selected_material_ids is not None:
        task.selected_material_ids = payload.selected_material_ids
    task.approved_at = utc_now()
    task.updated_at = utc_now()
    task.last_error = None


async def _snapshot_saved_draft(
    session: AsyncSession,
    task: EmailTask,
    payload: EmailTaskApprovalRequest,
) -> None:
    await _validate_selected_material_ids(
        session, payload.selected_material_ids
    )

    body_text = payload.body_text.strip()
    body_html = payload.body_html or ""
    if not body_text:
        normalized_body_html = ""
    elif body_html.strip():
        rendered = normalize_email_html(body_html)
        body_text = rendered.text
        normalized_body_html = rendered.html
    else:
        normalized_body_html = text_to_email_html(body_text).html
    task.approved_subject = (payload.subject or "").strip()
    task.approved_body_text = body_text
    task.approved_body_html = normalized_body_html
    if payload.selected_material_ids is not None:
        task.selected_material_ids = payload.selected_material_ids
    task.approved_at = utc_now()
    task.updated_at = utc_now()
    task.last_error = None


def restore_workspace_rewrite_source(
    task: EmailTask,
    error_message: str,
    *,
    now: datetime | None = None,
) -> None:
    source_body_text = task.draft_rewrite_source_body_text or ""
    source_body_html = task.draft_rewrite_source_body_html
    if source_body_text and not source_body_html:
        source_body_html = text_to_email_html(source_body_text).html
    task.approved_subject = task.draft_rewrite_source_subject
    task.approved_body_text = source_body_text
    task.approved_body_html = source_body_html
    task.selected_material_ids = task.draft_rewrite_source_selected_material_ids
    task.status = (
        task.draft_generation_previous_status or EmailTaskStatus.REVIEW_REQUIRED.value
    )
    task.draft_generation_previous_status = None
    task.draft_generation_started_at = None
    task.updated_at = now or utc_now()
    task.last_error = error_message


async def _validate_primary_material_id(
    session: AsyncSession,
    primary_material_id: int,
) -> IdentityMaterial:
    material = await session.scalar(
        select(IdentityMaterial).where(
            IdentityMaterial.id == primary_material_id,
        ),
    )
    if not material:
        raise ValueError("未找到 AI 写信参考材料")
    if not material_can_be_primary(material):
        raise ValueError("当前材料不支持作为 AI 写信参考材料")
    return material


async def _validate_selected_material_ids(
    session: AsyncSession,
    material_ids: list[int] | None,
) -> None:
    if material_ids is None:
        return
    if any(material_id < 1 for material_id in material_ids):
        raise ValueError("随信材料 ID 必须是正整数")
    if len(material_ids) != len(set(material_ids)):
        raise ValueError("随信材料 ID 不能重复")
    if not material_ids:
        return
    materials: list[int] = []
    for material_id_chunk in chunked_values(material_ids):
        materials.extend(
            await session.scalars(
                select(IdentityMaterial.id).where(
                    IdentityMaterial.id.in_(material_id_chunk),
                ),
            ),
        )
    if len(set(materials)) != len(set(material_ids)):
        raise ValueError("存在已删除或不存在的随信材料")


async def _resolve_runtime_llm_profile(
    session: AsyncSession,
    task: EmailTask,
    llm_profile_id: int | None,
) -> LLMProfile:
    if llm_profile_id is None or llm_profile_id == task.llm_profile_id:
        if not llm_profile_is_active(task.llm_profile):
            raise ValueError(DELETED_LLM_PROFILE_MESSAGE)
        return task.llm_profile
    profile = await get_active_llm_profile(session, llm_profile_id)
    if profile is None:
        raise ValueError(DELETED_LLM_PROFILE_MESSAGE)
    return profile


async def _ensure_no_manual_child_exists(
    session: AsyncSession, parent_task_id: int
) -> None:
    existing_child_id = await session.scalar(
        select(EmailTask.id).where(EmailTask.parent_task_id == parent_task_id).limit(1),
    )
    if existing_child_id is not None:
        raise ValueError("该任务已创建过手动子任务，不能重复派生")


def _ensure_task_allows_new_contact(task: EmailTask) -> None:
    if task.professor.archived_at is not None:
        raise ValueError(
            f"导师 #{task.professor_id} 已移入回收站，不能创建新的联系任务"
        )
    if task.identity.deleted_at is not None:
        raise ValueError(
            f"发件身份 #{task.identity_id} 已删除，不能创建新的联系任务；"
            "请选择其他发件身份新建任务"
        )
    if task.llm_profile.deleted_at is not None:
        raise ValueError(
            f"模型配置 #{task.llm_profile_id} 已删除，不能创建新的联系任务；"
            "历史记录仍会保留，请选择其他模型新建任务"
        )


async def _get_manual_child_task_id(
    session: AsyncSession, parent_task_id: int
) -> int | None:
    return await session.scalar(
        select(EmailTask.id).where(EmailTask.parent_task_id == parent_task_id).limit(1),
    )


async def _commit_manual_child_task(session: AsyncSession) -> None:
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise ValueError("该任务已创建过手动子任务，不能重复派生") from exc


def _restore_or_cancel_interrupted_draft_generation(
    task: EmailTask,
    *,
    batch_status: str | None = None,
) -> None:
    resolved_batch_status = batch_status or (
        task.batch_task.status if task.batch_task else None
    )
    if task.batch_task is None:
        task.status = (
            task.draft_generation_previous_status or EmailTaskStatus.DISCOVERED.value
        )
        task.cancellation_reason = None
    elif resolved_batch_status == BatchTaskStatus.PAUSED.value:
        task.status = (
            task.draft_generation_previous_status or EmailTaskStatus.DISCOVERED.value
        )
    elif resolved_batch_status == BatchTaskStatus.EXPIRED.value:
        task.status = EmailTaskStatus.CANCELED.value
        task.cancellation_reason = EmailTaskCancellationReason.SCHEDULE_EXPIRED.value
    else:
        task.status = EmailTaskStatus.CANCELED.value
        task.cancellation_reason = EmailTaskCancellationReason.BATCH_STOPPED.value
    task.draft_generation_previous_status = None
    task.updated_at = utc_now()


def _clear_batch_draft_claim(task: EmailTask) -> None:
    task.draft_generation_started_at = None
    task.draft_claim_id = None
    task.draft_claimed_at = None
    task.draft_lease_expires_at = None


async def _lock_current_batch_draft_claim(
    session: AsyncSession,
    task: EmailTask,
    draft_claim_id: str | None,
) -> bool:
    if draft_claim_id is None:
        return True
    result = await session.execute(
        update(EmailTask)
        .where(
            EmailTask.id == task.id,
            EmailTask.status == EmailTaskStatus.GENERATING_DRAFT.value,
            EmailTask.draft_claim_id == draft_claim_id,
        )
        .values(draft_claim_id=draft_claim_id)
        .execution_options(synchronize_session=False)
    )
    return result.rowcount == 1


def _resolve_task_outreach_config(task: EmailTask):
    return build_outreach_template_snapshot_config(
        generation_mode=task.outreach_generation_mode,
        subject_template=task.outreach_template_subject,
        body_text_template=task.outreach_template_body_text,
        body_html_template=task.outreach_template_body_html,
    )


def _task_has_outreach_template_snapshot(task: EmailTask) -> bool:
    return has_outreach_template_snapshot(
        snapshot_version=task.outreach_template_snapshot_version,
        template_id=task.outreach_template_id,
        subject_template=task.outreach_template_subject,
        body_text_template=task.outreach_template_body_text,
        body_html_template=task.outreach_template_body_html,
    )


def _resolve_draft_generation_outreach_config(
    task: EmailTask,
    *,
    fallback_template: OutreachTemplate | None = None,
):
    if _task_has_outreach_template_snapshot(task):
        return _resolve_task_outreach_config(task)

    return resolve_outreach_template_config(
        task.identity,
        template=fallback_template,
        generation_mode=task.outreach_generation_mode,
    )


def _build_task_outreach_snapshot(
    identity: IdentityProfile,
    *,
    template: OutreachTemplate | None = None,
    outreach_generation_mode: str | None = None,
    outreach_template_subject: str | None = None,
    outreach_template_body_text: str | None = None,
    outreach_template_body_html: str | None = None,
    fallback_task: EmailTask | None = None,
    validate_ready: bool = True,
) -> dict[str, str | None]:
    resolved = resolve_outreach_template_config(
        identity,
        template=template,
        generation_mode=(
            outreach_generation_mode
            if outreach_generation_mode is not None
            else fallback_task.outreach_generation_mode
            if fallback_task is not None
            else None
        ),
        subject_template=(
            outreach_template_subject
            if outreach_template_subject is not None
            else (
                fallback_task.outreach_template_subject
                if fallback_task is not None and template is None
                else None
            )
        ),
        body_text_template=(
            outreach_template_body_text
            if outreach_template_body_text is not None
            else (
                fallback_task.outreach_template_body_text
                if fallback_task is not None and template is None
                else None
            )
        ),
        body_html_template=(
            outreach_template_body_html
            if outreach_template_body_html is not None
            else (
                fallback_task.outreach_template_body_html
                if fallback_task is not None and template is None
                else None
            )
        ),
    )
    body_text = _normalize_nullable_text(resolved.body_text_template)
    body_html = _normalize_nullable_text(resolved.body_html_template)
    if validate_ready:
        detail = get_outreach_template_defaults_validation_error(
            resolved.subject_template,
            resolved.body_text_template,
        )
        if detail:
            raise ValueError(detail)
    return {
        "outreach_generation_mode": resolved.generation_mode,
        "outreach_template_subject": _normalize_nullable_text(
            resolved.subject_template
        ),
        "outreach_template_body_text": body_text,
        "outreach_template_body_html": body_html,
    }


def _normalize_nullable_text(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    return normalized or None


def _derive_manual_child_status(
    task: EmailTask,
    *,
    reuse_existing_draft: bool,
    minimum_status: str | None = None,
) -> str:
    if reuse_existing_draft and _task_has_reusable_draft(task):
        return EmailTaskStatus.REVIEW_REQUIRED.value

    status = (
        EmailTaskStatus.MATCHED.value
        if _task_has_match_result(task)
        else EmailTaskStatus.DISCOVERED.value
    )
    if (
        minimum_status == EmailTaskStatus.MATCHED.value
        and status == EmailTaskStatus.DISCOVERED.value
    ):
        return EmailTaskStatus.MATCHED.value
    return status


def _create_manual_child_task(
    task: EmailTask,
    *,
    reuse_existing_draft: bool,
    minimum_status: str | None = None,
    fallback_template: OutreachTemplate | None = None,
) -> EmailTask:
    now = utc_now()
    outreach_config = _resolve_draft_generation_outreach_config(
        task,
        fallback_template=fallback_template,
    )
    return EmailTask(
        source=EmailTaskSource.MANUAL.value,
        batch_task_id=None,
        parent_task_id=task.id,
        identity_id=task.identity_id,
        llm_profile_id=task.llm_profile_id,
        professor_id=task.professor_id,
        primary_material_id=task.primary_material_id,
        status=_derive_manual_child_status(
            task,
            reuse_existing_draft=reuse_existing_draft,
            minimum_status=minimum_status,
        ),
        cancellation_reason=None,
        match_source_identity_id=task.match_source_identity_id,
        match_score=task.match_score,
        match_reason=task.match_reason,
        generated_subject=task.generated_subject if reuse_existing_draft else None,
        generated_content_text=task.generated_content_text
        if reuse_existing_draft
        else None,
        generated_content_html=task.generated_content_html
        if reuse_existing_draft
        else None,
        outreach_generation_mode=outreach_config.generation_mode,
        outreach_template_subject=outreach_config.subject_template,
        outreach_template_body_text=outreach_config.body_text_template,
        outreach_template_body_html=outreach_config.body_html_template,
        outreach_template_id=(
            task.outreach_template_id
            if _task_has_outreach_template_snapshot(task)
            else fallback_template.id
            if fallback_template is not None
            else None
        ),
        outreach_template_snapshot_version=1,
        selected_material_ids=(
            list(task.selected_material_ids)
            if task.selected_material_ids is not None
            else None
        ),
        approved_at=None,
        fit_points=list(task.fit_points) if task.fit_points else [],
        risk_points=list(task.risk_points) if task.risk_points else [],
        match_keywords=list(task.match_keywords) if task.match_keywords else [],
        approved_subject=task.approved_subject if reuse_existing_draft else None,
        approved_body_text=task.approved_body_text if reuse_existing_draft else None,
        approved_body_html=task.approved_body_html if reuse_existing_draft else None,
        scheduled_at=None,
        last_send_attempt_at=None,
        sent_at=None,
        last_rfc_message_id=None,
        retry_count=0,
        is_read=False,
        is_replied=False,
        last_error=None,
        created_at=now,
        updated_at=now,
    )


def _task_has_reusable_draft(task: EmailTask) -> bool:
    return any(
        _normalize_nullable_text(value) is not None
        for value in [
            task.generated_subject,
            task.generated_content_text,
            task.generated_content_html,
            task.approved_subject,
            task.approved_body_text,
            task.approved_body_html,
        ]
    )


def _task_has_match_result(task: EmailTask) -> bool:
    return task.match_score is not None and bool(task.match_reason)


def _ensure_task_allows_legacy_manual_actions(task: EmailTask) -> None:
    if task.batch_send_canceled_at is not None:
        raise ValueError("该导师已取消发送，请先恢复发送")
    if (
        task.status == EmailTaskStatus.CANCELED.value
        and task.cancellation_reason == EmailTaskCancellationReason.BATCH_STOPPED.value
    ):
        raise ValueError(
            "该任务已因批量任务停止而取消，请先“作为单独联系继续”后再执行此操作"
        )


def _ensure_task_not_generating_for_workspace_change(task: EmailTask) -> None:
    if task.status == EmailTaskStatus.GENERATING_DRAFT.value:
        raise ValueError("AI 正在改写当前草稿，请等待完成后再修改。")


def _ensure_task_allows_approval(task: EmailTask) -> None:
    if task.batch_send_canceled_at is not None:
        raise ValueError("该导师已取消发送，请先恢复发送")
    if (
        task.status == EmailTaskStatus.CANCELED.value
        and task.cancellation_reason == EmailTaskCancellationReason.USER_REMOVED.value
    ):
        raise ValueError("该草稿已从批量任务中移除，不能再审核或发送")
    if task.status == EmailTaskStatus.GENERATING_DRAFT.value:
        raise ValueError("AI 正在改写当前草稿，请等待完成后再发送。")


def _ensure_task_allows_draft_save(task: EmailTask) -> None:
    if task.status == EmailTaskStatus.GENERATING_DRAFT.value:
        raise ValueError("AI 正在改写当前草稿，请等待完成后再保存。")
    _ensure_task_allows_approval(task)
    if task.status not in SAVE_DRAFT_ALLOWED_STATUSES:
        raise ValueError("当前状态不能保存草稿")
