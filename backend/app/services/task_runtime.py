from __future__ import annotations

import asyncio
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, tzinfo

from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import selectinload

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
    MatchAnalysisRun,
    Professor,
)
from app.schemas.email_task import EmailTaskApprovalRequest, EmailTaskScheduleRequest
from app.services import llm_runtime, mail_runtime
from app.services.batch_schedule import (
    has_future_batch_window,
    is_batch_window_expired,
    is_datetime_in_batch_window,
    normalize_scheduled_dates,
)
from app.services.mail_runtime import MailAttachment, ReceivedEmail
from app.services.materials import (
    build_material_download_name,
    ensure_material_extracted_text,
    material_can_be_primary,
)
from app.services.operation_logs import record_operation_log
from app.services.outreach_templates import (
    OUTREACH_GENERATION_MODE_TEMPLATE,
    build_template_context,
    get_outreach_template_defaults_validation_error,
    render_outreach_template,
    render_template_with_context,
    resolve_outreach_template_config,
)
from app.services.rich_text import normalize_email_html, text_to_email_html
from app.services.runtime_settings import get_runtime_settings


TASK_RELATION_OPTIONS = (
    selectinload(EmailTask.batch_task),
    selectinload(EmailTask.identity).selectinload(IdentityProfile.materials),
    selectinload(EmailTask.identity).selectinload(IdentityProfile.current_primary_material),
    selectinload(EmailTask.llm_profile),
    selectinload(EmailTask.professor),
    selectinload(EmailTask.primary_material),
)

DISPATCHABLE_EMAIL_TASK_STATUSES = (
    EmailTaskStatus.APPROVED.value,
    EmailTaskStatus.SCHEDULED.value,
)

STALE_SENDING_TASK_AFTER = timedelta(minutes=30)
INTERRUPTED_MATCH_ANALYSIS_RUN_ERROR = "匹配分析因桌面端进程中断而停止"

MANUAL_DRAFT_CLAIMABLE_STATUSES = {
    EmailTaskStatus.DISCOVERED.value,
    EmailTaskStatus.MATCHED.value,
    EmailTaskStatus.DRAFT_FAILED.value,
    EmailTaskStatus.REVIEW_REQUIRED.value,
    EmailTaskStatus.APPROVED.value,
    EmailTaskStatus.SCHEDULED.value,
}


def _has_professor_match_evidence(professor: Professor) -> bool:
    return bool((professor.research_direction or "").strip()) or any(
        str(paper).strip() for paper in professor.recent_papers or []
    )


def _has_professor_research_direction(professor: Professor) -> bool:
    return bool((professor.research_direction or "").strip())


@dataclass(slots=True)
class MatchUsageSummary:
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None
    cached_tokens: int | None = None


@dataclass(slots=True)
class MatchCalculationActionResult:
    professor_id: int
    identity_id: int
    llm_profile_id: int
    usage: MatchUsageSummary
    run_id: int | None = None


class MatchAnalysisAlreadyRunningError(RuntimeError):
    pass


class MatchCalculationCanceledError(RuntimeError):
    pass


async def process_pending_drafts_once(
    session_factory: async_sessionmaker[AsyncSession],
    limit: int = 5,
) -> int:
    return 0


async def dispatch_due_tasks_once(
    session_factory: async_sessionmaker[AsyncSession],
    limit: int = 10,
    *,
    now: datetime | None = None,
    local_timezone: tzinfo | None = None,
) -> int:
    now_utc, local_now = _resolve_dispatch_clocks(now, local_timezone)
    if limit <= 0:
        return 0

    await recover_stale_sending_tasks(session_factory, now=now_utc)

    async with session_factory() as session:
        await _expire_overdue_scheduled_batch_tasks(session, local_now)
        sent_counts: dict[int, int] = {}
        task_ids: list[int] = []
        page_size = max(limit, 10)
        offset = 0
        while len(task_ids) < limit:
            candidates = list(
                (
                    await session.execute(
                        select(EmailTask)
                        .options(selectinload(EmailTask.batch_task).selectinload(BatchTask.email_tasks))
                        .join(BatchTask, EmailTask.batch_task_id == BatchTask.id, isouter=True)
                        .where(
                            EmailTask.status.in_(
                                [
                                    EmailTaskStatus.APPROVED.value,
                                    EmailTaskStatus.SCHEDULED.value,
                                ],
                            ),
                            or_(
                                EmailTask.scheduled_at.is_(None),
                                EmailTask.scheduled_at <= now_utc,
                            ),
                            or_(
                                BatchTask.id.is_(None),
                                and_(
                                    BatchTask.status == BatchTaskStatus.RUNNING.value,
                                    BatchTask.deleted_at.is_(None),
                                ),
                            ),
                        )
                        .order_by(
                            EmailTask.approved_at.asc(),
                            EmailTask.created_at.asc(),
                            EmailTask.id.asc(),
                        )
                        .offset(offset)
                        .limit(page_size),
                    )
                ).scalars()
            )
            if not candidates:
                break
            offset += len(candidates)

            for task in candidates:
                if len(task_ids) >= limit:
                    break
                batch_task = task.batch_task
                if not _batch_task_allows_dispatch(batch_task, local_now):
                    continue
                if (
                    batch_task is not None
                    and batch_task.schedule_type == "scheduled"
                    and batch_task.emails_per_window is not None
                ):
                    count = sent_counts.get(batch_task.id)
                    if count is None:
                        count = await _batch_task_sent_count_on_date(session, batch_task.id, local_now)
                    if count >= batch_task.emails_per_window:
                        sent_counts[batch_task.id] = count
                        continue
                    sent_counts[batch_task.id] = count + 1
                task_ids.append(task.id)

            if len(candidates) < page_size:
                break

    processed = 0
    for task_id in task_ids:
        await dispatch_email_task(session_factory, task_id)
        processed += 1
    return processed


async def _expire_overdue_scheduled_batch_tasks(
    session: AsyncSession,
    local_now: datetime,
) -> int:
    batch_tasks = list(
        (
            await session.execute(
                select(BatchTask)
                .options(selectinload(BatchTask.email_tasks))
                .where(
                    BatchTask.status == BatchTaskStatus.RUNNING.value,
                    BatchTask.schedule_type == "scheduled",
                    BatchTask.deleted_at.is_(None),
                ),
            )
        ).scalars().unique()
    )
    expired_count = 0
    for batch_task in batch_tasks:
        if await expire_batch_task_if_needed(session, batch_task, local_now):
            expired_count += 1
    if expired_count > 0:
        await session.commit()
    return expired_count


def _resolve_dispatch_clocks(
    now: datetime | None,
    local_timezone: tzinfo | None,
) -> tuple[datetime, datetime]:
    now_utc = now.astimezone(UTC) if now is not None else datetime.now(UTC)
    resolved_timezone = local_timezone or datetime.now().astimezone().tzinfo or UTC
    return now_utc, now_utc.astimezone(resolved_timezone)


def _batch_task_allows_dispatch(batch_task: BatchTask | None, now: datetime) -> bool:
    if batch_task is None:
        return True
    if batch_task.status != BatchTaskStatus.RUNNING.value:
        return False
    if batch_task.schedule_type != "scheduled":
        return True
    return is_datetime_in_batch_window(
        now,
        scheduled_dates=batch_task.scheduled_dates,
        window_start_time=batch_task.window_start_time,
        window_end_time=batch_task.window_end_time,
    )


async def expire_batch_task_if_needed(
    session: AsyncSession,
    batch_task: BatchTask,
    local_now: datetime,
) -> bool:
    if batch_task.schedule_type != "scheduled":
        return False
    if batch_task.status != BatchTaskStatus.RUNNING.value:
        return False
    if not is_batch_window_expired(
        local_now,
        scheduled_dates=batch_task.scheduled_dates,
        window_end_time=batch_task.window_end_time,
    ):
        return False

    canceled_count = 0
    now_utc = local_now.astimezone(UTC)
    if any(
        _has_future_scheduled_at(
            email_task.scheduled_at,
            now_utc,
            scheduled_dates=batch_task.scheduled_dates,
            local_timezone=local_now.tzinfo,
        )
        for email_task in batch_task.email_tasks
        if email_task.status
        in {
            EmailTaskStatus.DISCOVERED.value,
            EmailTaskStatus.MATCHED.value,
            EmailTaskStatus.GENERATING_DRAFT.value,
            EmailTaskStatus.DRAFT_FAILED.value,
            EmailTaskStatus.REVIEW_REQUIRED.value,
            EmailTaskStatus.APPROVED.value,
            EmailTaskStatus.SCHEDULED.value,
        }
    ):
        return False

    for email_task in batch_task.email_tasks:
        if email_task.status in {
            EmailTaskStatus.DISCOVERED.value,
            EmailTaskStatus.MATCHED.value,
            EmailTaskStatus.GENERATING_DRAFT.value,
            EmailTaskStatus.DRAFT_FAILED.value,
            EmailTaskStatus.REVIEW_REQUIRED.value,
            EmailTaskStatus.APPROVED.value,
            EmailTaskStatus.SCHEDULED.value,
        }:
            email_task.status = EmailTaskStatus.CANCELED.value
            email_task.cancellation_reason = EmailTaskCancellationReason.SCHEDULE_EXPIRED.value
            email_task.draft_generation_previous_status = None
            email_task.updated_at = now_utc
            canceled_count += 1

    if canceled_count == 0:
        return False

    batch_task.status = BatchTaskStatus.EXPIRED.value
    batch_task.updated_at = now_utc
    await record_operation_log(
        session,
        category="email",
        event_name="batch_task.expired",
        entity_type="batch_task",
        entity_id=str(batch_task.id),
        metadata={
            "canceled_count": canceled_count,
            "scheduled_dates": batch_task.scheduled_dates,
            "window_end_time": batch_task.window_end_time,
        },
    )
    return True


def _has_future_scheduled_at(
    scheduled_at: datetime | None,
    now_utc: datetime,
    *,
    scheduled_dates: list[str] | None,
    local_timezone: tzinfo | None,
) -> bool:
    if scheduled_at is None:
        return False
    if scheduled_at.tzinfo is None:
        scheduled_at_utc = scheduled_at.replace(tzinfo=UTC)
    else:
        scheduled_at_utc = scheduled_at.astimezone(UTC)
    timezone = local_timezone or UTC
    scheduled_date = scheduled_at_utc.astimezone(timezone).date().isoformat()
    if scheduled_date not in set(normalize_scheduled_dates(scheduled_dates)):
        return False
    return scheduled_at_utc > now_utc


async def _batch_task_sent_count_on_date(
    session: AsyncSession,
    batch_task_id: int,
    local_now: datetime,
) -> int:
    start_local = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
    end_local = start_local + timedelta(days=1)
    start_utc = start_local.astimezone(UTC)
    end_utc = end_local.astimezone(UTC)
    return int(
        await session.scalar(
            select(func.count(EmailTask.id)).where(
                EmailTask.batch_task_id == batch_task_id,
                EmailTask.status.in_(
                    [
                        EmailTaskStatus.SENT.value,
                        EmailTaskStatus.REPLY_DETECTED.value,
                    ],
                ),
                EmailTask.sent_at >= start_utc,
                EmailTask.sent_at < end_utc,
            ),
        )
        or 0
    )


async def poll_for_replies_once(
    session_factory: async_sessionmaker[AsyncSession],
) -> int:
    async with session_factory() as session:
        identity_ids = list(
            (
                await session.execute(
                    select(IdentityProfile.id).where(
                        IdentityProfile.imap_host.is_not(None),
                        IdentityProfile.imap_username.is_not(None),
                        IdentityProfile.imap_password.is_not(None),
                    ),
                )
            ).scalars()
        )

    detected = 0
    for identity_id in identity_ids:
        detected += await poll_identity_replies(session_factory, identity_id)
    return detected

async def recover_stale_sending_tasks(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    stale_after: timedelta = STALE_SENDING_TASK_AFTER,
    now: datetime | None = None,
) -> int:
    resolved_now = now or datetime.now(UTC)
    cutoff = resolved_now - stale_after
    async with session_factory() as session:
        tasks = list(
            await session.scalars(
                select(EmailTask)
                .options(selectinload(EmailTask.batch_task))
                .where(
                    EmailTask.status == EmailTaskStatus.SENDING.value,
                    or_(
                        and_(
                            EmailTask.last_send_attempt_at.is_not(None),
                            EmailTask.last_send_attempt_at < cutoff,
                        ),
                        and_(
                            EmailTask.last_send_attempt_at.is_(None),
                            EmailTask.updated_at < cutoff,
                        ),
                    ),
                ),
            ),
        )
        for task in tasks:
            _restore_or_cancel_interrupted_send(task)
            task.updated_at = resolved_now
        await session.commit()
        return len(tasks)


async def recover_interrupted_match_analysis_runs(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    now: datetime | None = None,
) -> int:
    resolved_now = now or datetime.now(UTC)
    async with session_factory() as session:
        runs = list(
            await session.scalars(
                select(MatchAnalysisRun).where(MatchAnalysisRun.status == "running"),
            ),
        )
        for run in runs:
            run.status = "failed"
            run.success = False
            run.error_kind = "interrupted"
            run.error_message = INTERRUPTED_MATCH_ANALYSIS_RUN_ERROR
            run.finished_at = resolved_now
        await session.commit()
        return len(runs)


async def generate_task_draft(
    session_factory: async_sessionmaker[AsyncSession],
    task_id: int,
    *,
    force: bool,
    ignore_batch_status: bool = False,
    automatic_batch: bool = False,
    require_running_batch: bool = False,
) -> tuple[int, int, int]:
    async with session_factory() as session:
        task = await _load_email_task(session, task_id)
        if not task:
            raise ValueError(f"EmailTask {task_id} 不存在")
        task_identity = (task.professor_id, task.identity_id, task.llm_profile_id)
        if task.status == EmailTaskStatus.GENERATING_DRAFT.value and not automatic_batch:
            raise ValueError("草稿正在后台生成，请稍后刷新")
        if (
            task.batch_task
            and task.batch_task.status != BatchTaskStatus.RUNNING.value
            and not ignore_batch_status
        ):
            if automatic_batch or require_running_batch:
                _restore_or_cancel_interrupted_draft_generation(task)
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
                    updated_at=datetime.now(UTC),
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
            outreach_config = _resolve_task_outreach_config(task)
            if outreach_config.generation_mode == OUTREACH_GENERATION_MODE_TEMPLATE:
                template_subject = _normalize_nullable_text(outreach_config.subject_template)
                template_body = _normalize_nullable_text(outreach_config.body_text_template)
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
                suggested_material_ids = (
                    batch_task.selected_material_ids if batch_task else None
                )
                provider_payload = {
                    "source": OUTREACH_GENERATION_MODE_TEMPLATE,
                    "placeholders": rendered.placeholders,
                    "usage": None,
                }
            else:
                if task.primary_material is None:
                    if force:
                        raise ValueError("请先选择用于匹配的默认材料")
                    return task.professor_id, task.identity_id, task.llm_profile_id
                if not _has_professor_research_direction(task.professor):
                    raise ValueError("请先补充导师研究方向，再使用 AI 生成草稿")
                ensure_material_extracted_text(task.primary_material)
                template_subject = _normalize_nullable_text(outreach_config.subject_template) or (
                    _normalize_nullable_text(batch_task.email_subject) if batch_task else None
                )
                template_body = _normalize_nullable_text(outreach_config.body_text_template) or (
                    _normalize_nullable_text(batch_task.email_body) if batch_task else None
                )
                template_body_html = _normalize_nullable_text(outreach_config.body_html_template)
                detail = get_outreach_template_defaults_validation_error(
                    template_subject,
                    template_body,
                )
                if detail:
                    raise ValueError(detail)

                current_match = _build_match_result_from_task(task)
                runtime_settings = await get_runtime_settings(session)
                rewrite_preferences = llm_runtime.DraftRewritePreferences(
                    draft_rewrite_intensity=runtime_settings.draft_rewrite_intensity,
                    draft_rewrite_tone=runtime_settings.draft_rewrite_tone,
                    draft_rewrite_formality=runtime_settings.draft_rewrite_formality,
                    draft_rewrite_length=runtime_settings.draft_rewrite_length,
                    draft_rewrite_specificity=runtime_settings.draft_rewrite_specificity,
                    draft_template_preservation=runtime_settings.draft_template_preservation,
                )
                generation = await llm_runtime.generate_draft_content(
                    identity=task.identity,
                    primary_material=task.primary_material,
                    llm_profile=task.llm_profile,
                    professor=task.professor,
                    available_materials=list(task.identity.materials),
                    custom_subject=template_subject,
                    custom_body=template_body,
                    custom_body_html=template_body_html,
                    current_match=current_match,
                    max_tokens=runtime_settings.draft_max_tokens,
                    rewrite_preferences=rewrite_preferences,
                )
                subject = generation.result.subject
                body_text = generation.result.body_text
                body_html = generation.result.body_html
                usage = generation.usage
                suggested_material_ids = (
                    generation.result.suggested_material_ids
                    or (batch_task.selected_material_ids if batch_task else None)
                )
                provider_payload = {
                    "source": "llm",
                    "primary_material_id": task.primary_material_id,
                    "suggested_material_ids": generation.result.suggested_material_ids,
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
                        select(BatchTask.status).where(BatchTask.id == task.batch_task_id),
                    )
                    if batch_status != BatchTaskStatus.RUNNING.value:
                        _restore_or_cancel_interrupted_draft_generation(task, batch_status=batch_status)
                        await session.commit()
                        return task.professor_id, task.identity_id, task.llm_profile_id
        except asyncio.CancelledError:
            if automatic_batch:
                batch_status = (
                    await session.scalar(select(BatchTask.status).where(BatchTask.id == task.batch_task_id))
                    if task.batch_task_id is not None
                    else None
                )
                _restore_or_cancel_interrupted_draft_generation(task, batch_status=batch_status)
                await session.commit()
            raise
        except llm_runtime.LLMRuntimeError as exc:
            task.last_error = str(exc)
            if automatic_batch:
                task.status = EmailTaskStatus.DRAFT_FAILED.value
                task.draft_generation_previous_status = None
            else:
                task.status = task.draft_generation_previous_status or EmailTaskStatus.DISCOVERED.value
                task.draft_generation_previous_status = None
            task.updated_at = datetime.now(UTC)
            await session.commit()
            if automatic_batch:
                return task.professor_id, task.identity_id, task.llm_profile_id
            raise
        except ValueError as exc:
            task.last_error = str(exc)
            if automatic_batch:
                task.status = EmailTaskStatus.DRAFT_FAILED.value
                task.draft_generation_previous_status = None
            else:
                task.status = task.draft_generation_previous_status or EmailTaskStatus.DISCOVERED.value
                task.draft_generation_previous_status = None
            task.updated_at = datetime.now(UTC)
            await session.commit()
            if automatic_batch:
                return task.professor_id, task.identity_id, task.llm_profile_id
            raise

        task.generated_subject = subject
        task.generated_content_text = body_text
        task.generated_content_html = body_html
        if suggested_material_ids is not None:
            task.selected_material_ids = suggested_material_ids
        task.status = EmailTaskStatus.REVIEW_REQUIRED.value
        task.draft_generation_previous_status = None
        task.updated_at = datetime.now(UTC)
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
                "completion_tokens": usage.completion_tokens if usage is not None else None,
                "cached_tokens": usage.cached_tokens if usage is not None else None,
                "total_tokens": usage.total_tokens if usage is not None else None,
                "selected_material_ids": task.selected_material_ids,
            },
        )
        await session.commit()
        return task_identity


async def calculate_task_match(
    session_factory: async_sessionmaker[AsyncSession],
    task_id: int,
    *,
    force: bool,
    ignore_batch_status: bool = False,
    cancel_requested: Callable[[], Awaitable[bool]] | None = None,
) -> MatchCalculationActionResult:
    async with session_factory() as session:
        task = await _load_email_task(session, task_id)
        if not task:
            raise ValueError(f"EmailTask {task_id} 不存在")
        if (
            task.batch_task
            and task.batch_task.status != BatchTaskStatus.RUNNING.value
            and not ignore_batch_status
        ):
            return _match_action_result(task)
        if task.primary_material is None:
            if force:
                raise ValueError("请先选择用于匹配的默认材料")
            return _match_action_result(task)
        ensure_material_extracted_text(task.primary_material)
        if not _has_professor_match_evidence(task.professor):
            raise ValueError("缺少研究方向或近期论文，暂不能分析匹配度")
        if not force and task.status in {
            EmailTaskStatus.MATCHED.value,
            EmailTaskStatus.REVIEW_REQUIRED.value,
            EmailTaskStatus.APPROVED.value,
            EmailTaskStatus.SCHEDULED.value,
            EmailTaskStatus.SENT.value,
            EmailTaskStatus.REPLY_DETECTED.value,
        }:
            return _match_action_result(task)

        run = await _create_running_match_analysis_run(session, task)
        await session.commit()
        try:
            generation = await llm_runtime.generate_match_evaluation(
                identity=task.identity,
                primary_material=task.primary_material,
                llm_profile=task.llm_profile,
                professor=task.professor,
                available_materials=list(task.identity.materials),
            )
        except asyncio.CancelledError:
            _mark_match_analysis_run_failed(
                run,
                error_kind="canceled",
                error_message="匹配分析任务已取消",
            )
            task.updated_at = datetime.now(UTC)
            await session.commit()
            raise
        except llm_runtime.LLMRuntimeError as exc:
            _mark_match_analysis_run_failed(
                run,
                error_kind="llm_runtime",
                error_message=str(exc),
                duration_ms=exc.duration_ms,
                endpoint_kind=exc.endpoint_kind,
                status_code=exc.status_code,
            )
            task.last_error = str(exc)
            task.updated_at = datetime.now(UTC)
            await session.commit()
            return _match_action_result(task, run_id=run.id)
        except Exception as exc:
            _mark_match_analysis_run_failed(
                run,
                error_kind="unexpected",
                error_message=str(exc),
            )
            task.last_error = str(exc)
            task.updated_at = datetime.now(UTC)
            await session.commit()
            raise

        if cancel_requested is not None and await cancel_requested():
            _mark_match_analysis_run_failed(
                run,
                error_kind="canceled",
                error_message="匹配分析任务已取消",
            )
            task.updated_at = datetime.now(UTC)
            await session.commit()
            raise MatchCalculationCanceledError("匹配分析任务已取消")

        result = generation.result
        run.status = "succeeded"
        run.success = True
        run.match_score = result.match_score
        run.prompt_tokens = generation.usage.prompt_tokens if generation.usage else None
        run.completion_tokens = generation.usage.completion_tokens if generation.usage else None
        run.total_tokens = generation.usage.total_tokens if generation.usage else None
        run.cached_tokens = generation.usage.cached_tokens if generation.usage else None
        run.duration_ms = generation.duration_ms
        run.endpoint_kind = generation.endpoint_kind
        run.status_code = generation.status_code
        run.prompt_hash = generation.prompt_hash
        run.stable_prefix_hash = generation.stable_prefix_hash
        run.error_kind = None
        run.error_message = None
        run.finished_at = datetime.now(UTC)
        task.match_score = result.match_score
        task.match_reason = result.match_reason
        task.fit_points = result.fit_points
        task.risk_points = result.risk_points
        task.match_keywords = result.keywords
        task.status = EmailTaskStatus.MATCHED.value
        task.updated_at = datetime.now(UTC)
        task.last_error = None
        await _record_email_task_log(
            session,
            task,
            "email_task.match_calculated",
            metadata={
                "match_analysis_run_id": run.id,
                "match_score": task.match_score,
                "force": force,
            },
        )
        await session.commit()
        return _match_action_result(
            task,
            usage=_match_usage_summary(generation.usage),
            run_id=run.id,
        )


async def regenerate_task_draft(
    session_factory: async_sessionmaker[AsyncSession],
    task_id: int,
) -> tuple[int, int, int]:
    return await generate_task_draft(session_factory, task_id, force=True)


async def preview_task_draft(
    session_factory: async_sessionmaker[AsyncSession],
    task_id: int,
) -> llm_runtime.GeneratedDraftContent:
    async with session_factory() as session:
        task = await _load_email_task(session, task_id)
        if not task:
            raise ValueError(f"EmailTask {task_id} 不存在")

        outreach_config = _resolve_task_outreach_config(task)
        if outreach_config.generation_mode == OUTREACH_GENERATION_MODE_TEMPLATE:
            raise ValueError("模板模式不需要 AI 草稿预览")
        if task.primary_material is None:
            raise ValueError("请先选择用于匹配的默认材料")
        if not _has_professor_research_direction(task.professor):
            raise ValueError("请先补充导师研究方向，再使用 AI 生成草稿")

        ensure_material_extracted_text(task.primary_material)
        template_subject = _normalize_nullable_text(outreach_config.subject_template) or (
            _normalize_nullable_text(task.batch_task.email_subject) if task.batch_task else None
        )
        template_body = _normalize_nullable_text(outreach_config.body_text_template) or (
            _normalize_nullable_text(task.batch_task.email_body) if task.batch_task else None
        )
        template_body_html = _normalize_nullable_text(outreach_config.body_html_template)
        detail = get_outreach_template_defaults_validation_error(
            template_subject,
            template_body,
        )
        if detail:
            raise ValueError(detail)

        current_match = _build_match_result_from_task(task)
        runtime_settings = await get_runtime_settings(session)
        rewrite_preferences = llm_runtime.DraftRewritePreferences(
            draft_rewrite_intensity=runtime_settings.draft_rewrite_intensity,
            draft_rewrite_tone=runtime_settings.draft_rewrite_tone,
            draft_rewrite_formality=runtime_settings.draft_rewrite_formality,
            draft_rewrite_length=runtime_settings.draft_rewrite_length,
            draft_rewrite_specificity=runtime_settings.draft_rewrite_specificity,
            draft_template_preservation=runtime_settings.draft_template_preservation,
        )
        return await llm_runtime.generate_draft_content(
            identity=task.identity,
            primary_material=task.primary_material,
            llm_profile=task.llm_profile,
            professor=task.professor,
            available_materials=list(task.identity.materials),
            custom_subject=template_subject,
            custom_body=template_body,
            custom_body_html=template_body_html,
            current_match=current_match,
            rewrite_preferences=rewrite_preferences,
        )


def _match_usage_summary(
    usage: llm_runtime.ChatCompletionUsage | None,
) -> MatchUsageSummary:
    if usage is None:
        return MatchUsageSummary()
    return MatchUsageSummary(
        prompt_tokens=usage.prompt_tokens,
        completion_tokens=usage.completion_tokens,
        total_tokens=usage.total_tokens,
        cached_tokens=usage.cached_tokens,
    )


def _match_action_result(
    task: EmailTask,
    *,
    usage: MatchUsageSummary | None = None,
    run_id: int | None = None,
) -> MatchCalculationActionResult:
    return MatchCalculationActionResult(
        professor_id=task.professor_id,
        identity_id=task.identity_id,
        llm_profile_id=task.llm_profile_id,
        usage=usage or MatchUsageSummary(),
        run_id=run_id,
    )


async def _create_running_match_analysis_run(
    session: AsyncSession,
    task: EmailTask,
) -> MatchAnalysisRun:
    run = MatchAnalysisRun(
        email_task_id=task.id,
        professor_id=task.professor_id,
        identity_id=task.identity_id,
        llm_profile_id=task.llm_profile_id,
        status="running",
        success=False,
        started_at=datetime.now(UTC),
    )
    session.add(run)
    try:
        await session.flush()
    except IntegrityError as exc:
        await session.rollback()
        raise MatchAnalysisAlreadyRunningError("该任务正在分析中") from exc
    return run


def _mark_match_analysis_run_failed(
    run: MatchAnalysisRun,
    *,
    error_kind: str,
    error_message: str,
    duration_ms: int | None = None,
    endpoint_kind: str | None = None,
    status_code: int | None = None,
) -> None:
    run.status = "failed"
    run.success = False
    run.error_kind = error_kind
    run.error_message = error_message
    run.duration_ms = duration_ms
    run.endpoint_kind = endpoint_kind
    run.status_code = status_code
    run.finished_at = datetime.now(UTC)


async def calculate_task_match_once(
    session_factory: async_sessionmaker[AsyncSession],
    task_id: int,
) -> MatchCalculationActionResult:
    return await calculate_task_match(session_factory, task_id, force=True)


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
        if task.status in {
            EmailTaskStatus.SENT.value,
            EmailTaskStatus.REPLY_DETECTED.value,
        }:
            raise ValueError("已发送或已回信任务不能再切换默认材料")

        material = await _validate_primary_material_id(session, task.identity_id, primary_material_id)
        task.primary_material_id = material.id
        task.approved_subject = None
        task.approved_body_text = None
        task.approved_body_html = None
        task.approved_at = None
        task.scheduled_at = None
        task.last_error = None
        task.updated_at = datetime.now(UTC)
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
    outreach_template_subject: str | None = None,
    outreach_template_body_text: str | None = None,
    outreach_template_body_html: str | None = None,
) -> tuple[int, int, int]:
    async with session_factory() as session:
        task = await _load_email_task(session, task_id)
        if not task:
            raise ValueError(f"EmailTask {task_id} 不存在")
        _ensure_task_allows_legacy_manual_actions(task)
        if task.status in {
            EmailTaskStatus.SENT.value,
            EmailTaskStatus.REPLY_DETECTED.value,
        }:
            raise ValueError("已发送或已回信任务不能再切换本次发信模式")

        snapshot = _build_task_outreach_snapshot(
            task.identity,
            outreach_generation_mode=outreach_generation_mode,
            outreach_template_subject=outreach_template_subject,
            outreach_template_body_text=outreach_template_body_text,
            outreach_template_body_html=outreach_template_body_html,
            fallback_task=task,
        )
        task.outreach_generation_mode = snapshot["outreach_generation_mode"]
        task.outreach_template_subject = snapshot["outreach_template_subject"]
        task.outreach_template_body_text = snapshot["outreach_template_body_text"]
        task.outreach_template_body_html = snapshot["outreach_template_body_html"]
        task.approved_subject = None
        task.approved_body_text = None
        task.approved_body_html = None
        task.approved_at = None
        task.scheduled_at = None
        task.last_error = None
        task.updated_at = datetime.now(UTC)
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
        _ensure_batch_task_has_future_window(task)
        await _snapshot_approval(session, task, payload)
        task.status = EmailTaskStatus.APPROVED.value
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

    await dispatch_email_task(session_factory, task_id)
    return professor_id, identity_id, llm_profile_id


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
        _ensure_batch_task_has_future_window(task)
        await _snapshot_approval(session, task, payload)
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
        _ensure_batch_task_has_future_window(task)
        await _snapshot_approval(session, task, payload)
        task.status = EmailTaskStatus.SCHEDULED.value
        task.scheduled_at = payload.scheduled_at.astimezone(UTC)
        task.updated_at = datetime.now(UTC)
        await _record_email_task_log(
            session,
            task,
            "email_task.approved_and_scheduled",
            metadata={
                "scheduled_at": task.scheduled_at.isoformat() if task.scheduled_at else None,
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
        task.status = EmailTaskStatus.REVIEW_REQUIRED.value
        task.scheduled_at = None
        task.updated_at = datetime.now(UTC)
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
        await _ensure_no_manual_child_exists(session, task.id)
        if (
            task.status != EmailTaskStatus.CANCELED.value
            or task.cancellation_reason != EmailTaskCancellationReason.BATCH_STOPPED.value
        ):
            raise ValueError("只有 canceled 且 cancellation_reason 为 batch_stopped 的任务支持继续联系")

        professor_id = task.professor_id
        identity_id = task.identity_id
        llm_profile_id = task.llm_profile_id
        parent_task_id = task.id
        child_task = _create_manual_child_task(task, reuse_existing_draft=True)
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
        child_task = _create_manual_child_task(
            task,
            reuse_existing_draft=False,
            minimum_status=EmailTaskStatus.MATCHED.value,
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


async def dispatch_email_task(

    session_factory: async_sessionmaker[AsyncSession],
    task_id: int,
) -> tuple[int, int, int]:
    async with session_factory() as session:
        task = await _load_email_task(session, task_id)
        if not task:
            raise ValueError(f"EmailTask {task_id} 不存在")
        task_identity = (task.professor_id, task.identity_id, task.llm_profile_id)
        if task.status not in DISPATCHABLE_EMAIL_TASK_STATUSES:
            return task_identity
        if task.batch_task and task.batch_task.status != BatchTaskStatus.RUNNING.value:
            return task_identity

        claimed_at = datetime.now(UTC)
        claim_result = await session.execute(
            update(EmailTask)
            .where(
                EmailTask.id == task_id,
                EmailTask.status.in_(DISPATCHABLE_EMAIL_TASK_STATUSES),
            )
            .values(
                status=EmailTaskStatus.SENDING.value,
                last_send_attempt_at=claimed_at,
                retry_count=func.coalesce(EmailTask.retry_count, 0) + 1,
                updated_at=claimed_at,
            ),
        )
        if claim_result.rowcount != 1:
            await session.rollback()
            return task_identity
        await session.commit()
        task = await _load_email_task(session, task_id)
        if not task:
            raise ValueError(f"EmailTask {task_id} 不存在")
        task_identity = (task.professor_id, task.identity_id, task.llm_profile_id)
        if task.batch_task and task.batch_task.status != BatchTaskStatus.RUNNING.value:
            if task.batch_task.status == BatchTaskStatus.PAUSED.value:
                task.status = EmailTaskStatus.APPROVED.value
            elif task.batch_task.status == BatchTaskStatus.EXPIRED.value:
                task.status = EmailTaskStatus.CANCELED.value
                task.cancellation_reason = EmailTaskCancellationReason.SCHEDULE_EXPIRED.value
            else:
                task.status = EmailTaskStatus.CANCELED.value
                task.cancellation_reason = EmailTaskCancellationReason.BATCH_STOPPED.value
            task.updated_at = datetime.now(UTC)
            await session.commit()
            return task_identity

        subject_template = task.approved_subject or task.generated_subject
        body_text_template = task.approved_body_text or task.generated_content_text
        body_html_template = task.approved_body_html or task.generated_content_html
        context = build_template_context(task.identity, task.professor)
        subject = render_template_with_context(subject_template, context).strip()
        body_text = render_template_with_context(body_text_template, context).strip()
        body_html = (
            render_template_with_context(body_html_template, context)
            if body_html_template
            else None
        )
        if not subject or not body_text:
            task.status = EmailTaskStatus.SEND_FAILED.value
            task.last_error = "任务缺少可发送的主题或正文"
            task.updated_at = datetime.now(UTC)
            await session.commit()
            return task.professor_id, task.identity_id, task.llm_profile_id

        attachments = await _resolve_selected_materials(
            session,
            task.identity_id,
            task.selected_material_ids,
        )

        try:
            result = await mail_runtime.send_email(
                identity=task.identity,
                professor=task.professor,
                subject=subject,
                body_text=body_text,
                body_html=body_html,
                attachments=attachments,
            )
            rfc_message_id = result.message_id
            provider_payload = result.provider_payload

            task.status = EmailTaskStatus.SENT.value
            task.sent_at = datetime.now(UTC)
            task.last_rfc_message_id = rfc_message_id
            task.last_error = None
            task.updated_at = datetime.now(UTC)
            session.add(
                EmailLog(
                    email_task_id=task.id,
                    identity_id=task.identity_id,
                    llm_profile_id=task.llm_profile_id,
                    professor_id=task.professor_id,
                    direction=EmailDirection.SENT.value,
                    subject=subject,
                    content=body_text,
                    content_html=body_html,
                    rfc_message_id=rfc_message_id,
                    provider_payload=provider_payload,
                ),
            )
            await _record_email_task_log(
                session,
                task,
                "email_task.sent",
                metadata={
                    "rfc_message_id": rfc_message_id,
                    "retry_count": task.retry_count,
                    "attachment_count": len(attachments),
                },
            )
        except mail_runtime.MailRuntimeError as exc:
            task.status = EmailTaskStatus.SEND_FAILED.value
            task.last_error = str(exc)
            task.updated_at = datetime.now(UTC)
            session.add(
                EmailLog(
                    email_task_id=task.id,
                    identity_id=task.identity_id,
                    llm_profile_id=task.llm_profile_id,
                    professor_id=task.professor_id,
                    direction=EmailDirection.SENT.value,
                    subject=subject,
                    content=body_text,
                    content_html=body_html,
                    failure_summary=str(exc),
                ),
            )
            await _record_email_task_log(
                session,
                task,
                "email_task.send_failed",
                level="warning",
                message=str(exc),
                metadata={
                    "retry_count": task.retry_count,
                    "attachment_count": len(attachments),
                },
            )

        await session.commit()
        return task.professor_id, task.identity_id, task.llm_profile_id


async def poll_identity_replies(
    session_factory: async_sessionmaker[AsyncSession],
    identity_id: int,
) -> int:
    async with session_factory() as session:
        identity = await session.get(IdentityProfile, identity_id)
    if not identity:
        return 0

    messages = await mail_runtime.fetch_recent_inbox_messages(identity)
    return await _process_incoming_reply_messages(session_factory, identity_id, messages)


async def repair_identity_replies(
    session_factory: async_sessionmaker[AsyncSession],
    identity_id: int,
    *,
    professor_email: str | None = None,
) -> int:
    async with session_factory() as session:
        identity = await session.get(IdentityProfile, identity_id)
    if not identity:
        return 0

    messages: list[ReceivedEmail] = []
    if professor_email and professor_email.strip():
        messages = await mail_runtime.fetch_inbox_messages_from_sender(identity, professor_email)
    if not messages:
        messages = await mail_runtime.fetch_recent_inbox_messages(identity)
    return await _process_incoming_reply_messages(session_factory, identity_id, messages)


async def _process_incoming_reply_messages(
    session_factory: async_sessionmaker[AsyncSession],
    identity_id: int,
    messages: list[ReceivedEmail],
) -> int:
    detected = 0
    for message in messages:
        async with session_factory() as session:
            reply_created_at = _get_reply_created_at(message)
            if message.message_id:
                existing = await session.scalar(
                    select(EmailLog).where(EmailLog.rfc_message_id == message.message_id),
                )
                if existing:
                    if (
                        existing.direction == EmailDirection.RECEIVED.value
                        and not _datetimes_match(existing.created_at, reply_created_at)
                    ):
                        existing.created_at = reply_created_at
                        existing.reply_headers = message.headers
                        session.add(existing)
                        await session.commit()
                    continue

            task = await _find_reply_target(session, identity_id, message)
            if not task:
                continue

            task.is_replied = True
            task.status = EmailTaskStatus.REPLY_DETECTED.value
            task.updated_at = datetime.now(UTC)
            try:
                session.add(
                    EmailLog(
                        email_task_id=task.id,
                        identity_id=task.identity_id,
                        llm_profile_id=task.llm_profile_id,
                        professor_id=task.professor_id,
                        direction=EmailDirection.RECEIVED.value,
                        subject=message.subject,
                        content=message.content,
                        content_html=message.content_html,
                        rfc_message_id=message.message_id,
                        reply_headers=message.headers,
                        created_at=reply_created_at,
                    ),
                )
                await _record_email_task_log(
                    session,
                    task,
                    "email_task.reply_detected",
                    metadata={"message_id": message.message_id},
                )
                await session.commit()
            except IntegrityError:
                await session.rollback()
                continue
            detected += 1
    return detected


def _get_reply_created_at(message: mail_runtime.ReceivedEmail) -> datetime:
    return message.received_at or message.sent_at


def _datetimes_match(left: datetime, right: datetime) -> bool:
    def normalize(value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)

    return normalize(left) == normalize(right)


async def _snapshot_approval(
    session: AsyncSession,
    task: EmailTask,
    payload: EmailTaskApprovalRequest,
) -> None:
    await _validate_selected_material_ids(session, task.identity_id, payload.selected_material_ids)

    task.approved_subject = (payload.subject or task.generated_subject or "").strip()
    if payload.body_html:
        rendered = normalize_email_html(payload.body_html)
    else:
        rendered = text_to_email_html(payload.body_text)
    task.approved_body_text = rendered.text
    task.approved_body_html = rendered.html
    if payload.selected_material_ids is not None:
        task.selected_material_ids = payload.selected_material_ids
    task.approved_at = datetime.now(UTC)
    task.updated_at = datetime.now(UTC)
    task.last_error = None


async def _validate_primary_material_id(
    session: AsyncSession,
    identity_id: int,
    primary_material_id: int,
) -> IdentityMaterial:
    material = await session.scalar(
        select(IdentityMaterial).where(
            IdentityMaterial.identity_id == identity_id,
            IdentityMaterial.id == primary_material_id,
        ),
    )
    if not material:
        raise ValueError("默认材料不属于当前身份")
    if not material_can_be_primary(material):
        raise ValueError("当前材料不支持作为默认材料")
    return material


async def _validate_selected_material_ids(
    session: AsyncSession,
    identity_id: int,
    material_ids: list[int] | None,
) -> None:
    if not material_ids:
        return
    materials = list(
        (
            await session.execute(
                select(IdentityMaterial.id).where(
                    IdentityMaterial.identity_id == identity_id,
                    IdentityMaterial.id.in_(material_ids),
                ),
            )
        ).scalars()
    )
    if len(set(materials)) != len(set(material_ids)):
        raise ValueError("存在不属于当前身份的随信材料")


async def _resolve_selected_materials(
    session: AsyncSession,
    identity_id: int,
    material_ids: list[int] | None,
) -> list[MailAttachment]:
    if not material_ids:
        return []

    result = await session.execute(
        select(IdentityMaterial).where(
            IdentityMaterial.identity_id == identity_id,
            IdentityMaterial.id.in_(material_ids),
        ),
    )
    materials = {material.id: material for material in result.scalars()}
    attachments: list[MailAttachment] = []
    for material_id in material_ids:
        material = materials.get(material_id)
        if material is None:
            continue
        attachments.append(
            MailAttachment(
                file_path=material.file_path,
                download_name=build_material_download_name(material),
            ),
        )
    return attachments


async def _find_reply_target(
    session: AsyncSession,
    identity_id: int,
    message: ReceivedEmail,
) -> EmailTask | None:
    reference_ids = extract_message_ids(message.in_reply_to, message.references)
    if reference_ids:
        matched_log = await session.scalar(
            select(EmailLog)
            .where(
                EmailLog.identity_id == identity_id,
                EmailLog.direction == EmailDirection.SENT.value,
                EmailLog.rfc_message_id.in_(reference_ids),
            )
            .order_by(EmailLog.created_at.desc()),
        )
        if matched_log and matched_log.email_task_id:
            return await _load_email_task(session, matched_log.email_task_id)

    if not message.from_email:
        return None

    candidate_tasks = list(
        (
            await session.execute(
                select(EmailTask)
                .options(*TASK_RELATION_OPTIONS)
                .join(Professor, EmailTask.professor_id == Professor.id)
                .where(
                    EmailTask.identity_id == identity_id,
                    Professor.email == message.from_email,
                    EmailTask.status.in_(
                        [
                            EmailTaskStatus.SENT.value,
                            EmailTaskStatus.REPLY_DETECTED.value,
                        ],
                    ),
                )
                .order_by(EmailTask.sent_at.desc(), EmailTask.updated_at.desc()),
            )
        ).scalars()
    )
    if not candidate_tasks:
        return None

    normalized_incoming_subject = normalize_subject(message.subject)
    if normalized_incoming_subject:
        for task in candidate_tasks:
            if normalize_subject(task.approved_subject or task.generated_subject) == normalized_incoming_subject:
                return task
    return candidate_tasks[0]


async def _load_email_task(session: AsyncSession, task_id: int) -> EmailTask | None:
    return await session.scalar(
        select(EmailTask)
        .options(*TASK_RELATION_OPTIONS)
        .where(EmailTask.id == task_id),
    )


async def _ensure_no_manual_child_exists(session: AsyncSession, parent_task_id: int) -> None:
    existing_child_id = await session.scalar(
        select(EmailTask.id).where(EmailTask.parent_task_id == parent_task_id).limit(1),
    )
    if existing_child_id is not None:
        raise ValueError("该任务已创建过手动子任务，不能重复派生")


async def _get_manual_child_task_id(session: AsyncSession, parent_task_id: int) -> int | None:
    return await session.scalar(
        select(EmailTask.id).where(EmailTask.parent_task_id == parent_task_id).limit(1),
    )

async def _commit_manual_child_task(session: AsyncSession) -> None:
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise ValueError("该任务已创建过手动子任务，不能重复派生") from exc


async def _record_email_task_log(
    session: AsyncSession,
    task: EmailTask,
    event_name: str,
    *,
    level: str = "info",
    message: str | None = None,
    metadata: dict[str, object] | None = None,
) -> None:
    base_metadata: dict[str, object] = {
        "task_id": task.id,
        "source": task.source,
        "status": task.status,
        "batch_task_id": task.batch_task_id,
        "parent_task_id": task.parent_task_id,
        "professor_id": task.professor_id,
        "identity_id": task.identity_id,
        "llm_profile_id": task.llm_profile_id,
    }
    if metadata:
        base_metadata.update(metadata)
    await record_operation_log(
        session,
        category="email",
        event_name=event_name,
        level=level,
        message=message,
        entity_type="email_task",
        entity_id=str(task.id),
        metadata=base_metadata,
    )


def _restore_or_cancel_interrupted_draft_generation(
    task: EmailTask,
    *,
    batch_status: str | None = None,
) -> None:
    resolved_batch_status = batch_status or (task.batch_task.status if task.batch_task else None)
    if resolved_batch_status == BatchTaskStatus.PAUSED.value:
        task.status = task.draft_generation_previous_status or EmailTaskStatus.DISCOVERED.value
    elif resolved_batch_status == BatchTaskStatus.EXPIRED.value:
        task.status = EmailTaskStatus.CANCELED.value
        task.cancellation_reason = EmailTaskCancellationReason.SCHEDULE_EXPIRED.value
    else:
        task.status = EmailTaskStatus.CANCELED.value
        task.cancellation_reason = EmailTaskCancellationReason.BATCH_STOPPED.value
    task.draft_generation_previous_status = None
    task.updated_at = datetime.now(UTC)


def _restore_or_cancel_interrupted_send(task: EmailTask) -> None:
    batch_status = task.batch_task.status if task.batch_task else None
    if batch_status == BatchTaskStatus.EXPIRED.value:
        task.status = EmailTaskStatus.CANCELED.value
        task.cancellation_reason = EmailTaskCancellationReason.SCHEDULE_EXPIRED.value
    elif batch_status == BatchTaskStatus.STOPPED.value:
        task.status = EmailTaskStatus.CANCELED.value
        task.cancellation_reason = EmailTaskCancellationReason.BATCH_STOPPED.value
    elif batch_status == BatchTaskStatus.PAUSED.value:
        task.status = EmailTaskStatus.APPROVED.value
        task.cancellation_reason = None
    else:
        task.status = (
            EmailTaskStatus.SCHEDULED.value
            if task.scheduled_at is not None
            else EmailTaskStatus.APPROVED.value
        )
        task.cancellation_reason = None


def _ensure_batch_task_has_future_window(task: EmailTask) -> None:
    batch_task = task.batch_task
    if batch_task is None or batch_task.schedule_type != "scheduled":
        return

    local_now = datetime.now().astimezone()
    if batch_task.status == BatchTaskStatus.EXPIRED.value or not has_future_batch_window(
        local_now,
        scheduled_dates=batch_task.scheduled_dates,
        window_end_time=batch_task.window_end_time,
    ):
        raise ValueError("当前批量任务的发送窗口已全部过期，请重新安排发送时间后再审核发送。")


def _resolve_task_outreach_config(task: EmailTask):
    return resolve_outreach_template_config(
        task.identity,
        generation_mode=task.outreach_generation_mode,
        subject_template=task.outreach_template_subject,
        body_text_template=task.outreach_template_body_text,
        body_html_template=task.outreach_template_body_html,
    )


def _build_task_outreach_snapshot(
    identity: IdentityProfile,
    *,
    outreach_generation_mode: str | None = None,
    outreach_template_subject: str | None = None,
    outreach_template_body_text: str | None = None,
    outreach_template_body_html: str | None = None,
    fallback_task: EmailTask | None = None,
) -> dict[str, str | None]:
    resolved = resolve_outreach_template_config(
        identity,
        generation_mode=(
            outreach_generation_mode
            if outreach_generation_mode is not None
            else fallback_task.outreach_generation_mode if fallback_task is not None else None
        ),
        subject_template=(
            outreach_template_subject
            if outreach_template_subject is not None
            else fallback_task.outreach_template_subject if fallback_task is not None else None
        ),
        body_text_template=(
            outreach_template_body_text
            if outreach_template_body_text is not None
            else fallback_task.outreach_template_body_text if fallback_task is not None else None
        ),
        body_html_template=(
            outreach_template_body_html
            if outreach_template_body_html is not None
            else fallback_task.outreach_template_body_html if fallback_task is not None else None
        ),
    )
    body_text = _normalize_nullable_text(resolved.body_text_template)
    body_html = _normalize_nullable_text(resolved.body_html_template)
    detail = get_outreach_template_defaults_validation_error(
        resolved.subject_template,
        resolved.body_text_template,
    )
    if detail:
        raise ValueError(detail)
    return {
        "outreach_generation_mode": resolved.generation_mode,
        "outreach_template_subject": _normalize_nullable_text(resolved.subject_template),
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
        if _build_match_result_from_task(task) is not None
        else EmailTaskStatus.DISCOVERED.value
    )
    if minimum_status == EmailTaskStatus.MATCHED.value and status == EmailTaskStatus.DISCOVERED.value:
        return EmailTaskStatus.MATCHED.value
    return status


def _create_manual_child_task(
    task: EmailTask,
    *,
    reuse_existing_draft: bool,
    minimum_status: str | None = None,
) -> EmailTask:
    now = datetime.now(UTC)
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
        match_score=task.match_score,
        match_reason=task.match_reason,
        generated_subject=task.generated_subject if reuse_existing_draft else None,
        generated_content_text=task.generated_content_text if reuse_existing_draft else None,
        generated_content_html=task.generated_content_html if reuse_existing_draft else None,
        outreach_generation_mode=task.outreach_generation_mode,
        outreach_template_subject=task.outreach_template_subject,
        outreach_template_body_text=task.outreach_template_body_text,
        outreach_template_body_html=task.outreach_template_body_html,
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


def _build_match_result_from_task(task: EmailTask) -> llm_runtime.MatchEvaluationResult | None:
    if task.match_score is None or not task.match_reason:
        return None
    return llm_runtime.MatchEvaluationResult(
        match_score=task.match_score,
        match_reason=task.match_reason,
        fit_points=task.fit_points or [],
        risk_points=task.risk_points or [],
        keywords=task.match_keywords or [],
    )


def _ensure_task_allows_legacy_manual_actions(task: EmailTask) -> None:
    if (
        task.status == EmailTaskStatus.CANCELED.value
        and task.cancellation_reason == EmailTaskCancellationReason.BATCH_STOPPED.value
    ):
        raise ValueError("该任务已因批量任务停止而取消，请先“作为单独联系继续”后再执行此操作")


def extract_message_ids(*headers: str | None) -> set[str]:
    values: set[str] = set()
    for header in headers:
        if not header:
            continue
        values.update(re.findall(r"<[^>]+>", header))
    return values


def normalize_subject(subject: str | None) -> str:
    if not subject:
        return ""
    normalized = subject.strip().lower()
    normalized = re.sub(r"^(re|fw|fwd)\s*:\s*", "", normalized)
    normalized = re.sub(r"\s+", " ", normalized)
    return normalized
