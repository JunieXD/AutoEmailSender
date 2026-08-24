from __future__ import annotations

import hashlib
import json

from datetime import datetime
from typing import Any

from sqlalchemy import case, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.time import utc_now
from app.models import (
    AgentChangePlan,
    BatchTask,
    BatchTaskStatus,
    CrawlJob,
    CrawlJobKind,
    CrawlJobRun,
    CrawlJobStatus,
    CrawlCandidate,
    CrawlPage,
    CrawlWorkerTokenUsage,
    EmailLog,
    EmailTask,
    EmailTaskCancellationReason,
    EmailTaskStatus,
    IdentityProfessorMatchResult,
    LLMEndpointAdaptationCache,
    LLMProfile,
    LLMStructuredOutputAdaptationCache,
    MatchAnalysisJob,
    MatchAnalysisJobItem,
    MatchAnalysisJobStatus,
    MatchAnalysisRun,
    OperationLog,
    TestComposeMessage,
    TestComposeSession,
    ThinkingAdaptationCache,
)
from app.modules.campaigns.public import batch_item_uses_llm_generation_column
from app.modules.llm.runtime import resolve_base_url
from app.modules.llm.usage import (
    begin_llm_profile_retirement,
    end_llm_profile_retirement,
    get_llm_profile_usage_counts,
    llm_profile_retirement_in_progress,
)

from .schemas import (
    LLMProfileDeletionBlocker,
    LLMProfileDeletionAutomaticActions,
    LLMProfileDeletionImpact,
    LLMProfileDeletionResult,
    LLMProfileReferenceCounts,
)


MAX_BLOCKER_IDS = 20
INTERACTIVE_USAGE_LABELS = {
    "connectivity_test": "正在进行的模型连接测试",
    "default_replacement": "正被另一项删除操作选作默认模型",
    "draft_generation_startup": "正在启动的草稿生成",
    "draft_preview": "正在生成的草稿预览",
    "draft_rewrite_startup": "正在启动的草稿改写",
    "match_analysis_startup": "正在启动的匹配分析",
    "match_analysis": "正在执行的匹配分析",
    "model_listing": "正在读取的模型列表",
    "test_compose": "正在生成的测试写信草稿",
}
SENSITIVE_CRAWL_SNAPSHOT_FIELDS = (
    "api_base_url",
    "matcher_prompt_template",
    "writer_prompt_template",
)


async def build_llm_profile_deletion_impact(
    session: AsyncSession,
    profile: LLMProfile,
    *,
    include_retirement_in_progress: bool = True,
) -> LLMProfileDeletionImpact:
    references = await _reference_counts(session, profile.id)
    automatic_actions = await _automatic_actions(session, profile.id)
    blockers = await _deletion_blockers(
        session,
        profile.id,
        include_retirement_in_progress=include_retirement_in_progress,
    )
    revision = _deletion_revision(profile, references, automatic_actions, blockers)
    return LLMProfileDeletionImpact(
        profile_id=profile.id,
        profile_name=profile.name,
        model_name=profile.model_name,
        is_default=profile.is_default,
        can_delete=not blockers,
        revision=revision,
        references=references,
        automatic_actions=automatic_actions,
        blockers=blockers,
        warnings=[
            "批量活动、邮件、通信、匹配分析、智能抓取和 Token 用量记录都会保留。",
            "暂停、失败或已取消的任务再次运行前，需要重新选择模型。系统不会自动使用默认模型。",
            "等待生成草稿、匹配分析、智能抓取和信息补全的任务会自动取消。正在执行的模型请求结束前，暂时无法删除配置。",
            "本地保存的 API Key 会被清除。如需彻底停用该密钥，还需前往服务商平台删除或更换。",
        ],
    )


async def retire_llm_profile(
    session: AsyncSession,
    profile: LLMProfile,
    *,
    expected_revision: str,
    replacement_default_profile: LLMProfile | None,
) -> LLMProfileDeletionResult:
    impact = await build_llm_profile_deletion_impact(session, profile)
    was_default = profile.is_default
    if impact.revision != expected_revision:
        raise LLMProfileDeletionError(
            code="LLM_PROFILE_DELETE_PLAN_STALE",
            message="模型配置的关联状态已变化，请重新查看删除影响后再确认。",
            impact=impact,
        )
    if impact.blockers:
        raise LLMProfileDeletionError(
            code="LLM_PROFILE_IN_USE",
            message=_blocker_message(profile.name, impact.blockers),
            impact=impact,
        )
    if replacement_default_profile is not None:
        if not profile.is_default:
            raise LLMProfileDeletionError(
                code="LLM_PROFILE_DEFAULT_REPLACEMENT_NOT_NEEDED",
                message="仅删除当前默认模型时可以指定默认替代项。",
                impact=impact,
            )
        if replacement_default_profile.id == profile.id:
            raise LLMProfileDeletionError(
                code="LLM_PROFILE_DEFAULT_REPLACEMENT_INVALID",
                message="默认模型替代项不能是正在删除的配置。",
                impact=impact,
            )
        if replacement_default_profile.deleted_at is not None:
            raise LLMProfileDeletionError(
                code="LLM_PROFILE_DEFAULT_REPLACEMENT_INVALID",
                message="默认模型替代项已删除，请重新选择。",
                impact=impact,
            )

    if not begin_llm_profile_retirement(profile.id):
        refreshed_impact = await build_llm_profile_deletion_impact(session, profile)
        raise LLMProfileDeletionError(
            code="LLM_PROFILE_IN_USE",
            message=_blocker_message(profile.name, refreshed_impact.blockers),
            impact=refreshed_impact,
        )

    try:
        locked_impact = await build_llm_profile_deletion_impact(
            session,
            profile,
            include_retirement_in_progress=False,
        )
        if locked_impact.revision != impact.revision:
            if locked_impact.blockers:
                raise LLMProfileDeletionError(
                    code="LLM_PROFILE_IN_USE",
                    message=_blocker_message(profile.name, locked_impact.blockers),
                    impact=locked_impact,
                )
            raise LLMProfileDeletionError(
                code="LLM_PROFILE_DELETE_PLAN_STALE",
                message="模型配置的关联状态已变化，请重新查看删除影响后再确认。",
                impact=locked_impact,
            )
        now = utc_now()
        actions = locked_impact.automatic_actions
        await _cancel_waiting_draft_tasks(
            session,
            actions.cancel_email_task_ids,
            now=now,
        )
        if actions.cancel_match_analysis_job_ids:
            from app.modules.matching.public import (
                request_match_analysis_job_cancel_record,
            )

            for job_id in actions.cancel_match_analysis_job_ids:
                await request_match_analysis_job_cancel_record(
                    session,
                    job_id,
                    event_name="match_analysis_job.llm_profile_retired",
                    actor="desktop_ui",
                )
        if actions.cancel_crawl_job_ids:
            from app.modules.crawler.public import cancel_faculty_crawl_job_record
            from app.modules.professors.public import (
                request_professor_information_enrichment_cancel,
            )

            for job_id in actions.cancel_crawl_job_ids:
                job = await session.get(CrawlJob, job_id)
                if job is None:
                    continue
                if job.job_kind == CrawlJobKind.PROFESSOR_ENRICHMENT.value:
                    await request_professor_information_enrichment_cancel(
                        session,
                        job,
                        event_name=(
                            "professor_information_enrichment.llm_profile_retired"
                        ),
                        actor="desktop_ui",
                    )
                else:
                    await cancel_faculty_crawl_job_record(
                        session,
                        job_id,
                        event_name="crawl_job.llm_profile_retired",
                        actor="desktop_ui",
                    )
        invalidated_plan_count = await _invalidate_pending_change_plans(
            session,
            profile_id=profile.id,
            now=now,
        )
        await _sanitize_crawl_runtime_snapshots(session, profile.id)
        await _delete_unshared_adaptation_caches(session, profile)

        profile.api_key = ""
        profile.api_base_url = None
        profile.matcher_prompt_template = None
        profile.writer_prompt_template = None
        profile.is_default = False
        profile.deleted_at = now
        profile.updated_at = now

        if replacement_default_profile is not None:
            active_profiles = list(
                await session.scalars(
                    select(LLMProfile).where(LLMProfile.deleted_at.is_(None))
                )
            )
            for candidate in active_profiles:
                candidate.is_default = candidate.id == replacement_default_profile.id
                candidate.updated_at = now

        current_default_profile_id = (
            replacement_default_profile.id if replacement_default_profile else None
        )
        if not was_default:
            current_default_profile_id = await session.scalar(
                select(LLMProfile.id)
                .where(
                    LLMProfile.deleted_at.is_(None),
                    LLMProfile.is_default.is_(True),
                    LLMProfile.id != profile.id,
                )
                .order_by(LLMProfile.created_at.asc(), LLMProfile.id.asc())
                .limit(1)
            )

        return LLMProfileDeletionResult(
            profile_id=profile.id,
            profile_name=profile.name,
            references_preserved=impact.references,
            invalidated_plan_count=invalidated_plan_count,
            default_profile_id=current_default_profile_id,
            canceled_email_task_ids=actions.cancel_email_task_ids,
            canceled_match_analysis_job_ids=actions.cancel_match_analysis_job_ids,
            canceled_crawl_job_ids=actions.cancel_crawl_job_ids,
        )
    except BaseException:
        end_llm_profile_retirement(profile.id)
        raise


class LLMProfileDeletionError(RuntimeError):
    def __init__(
        self,
        *,
        code: str,
        message: str,
        impact: LLMProfileDeletionImpact,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.impact = impact


async def _reference_counts(
    session: AsyncSession,
    profile_id: int,
) -> LLMProfileReferenceCounts:
    async def count(model: Any, predicate: Any) -> int:
        value = await session.scalar(
            select(func.count()).select_from(model).where(predicate)
        )
        return int(value or 0)

    direct_crawl_job_ids = set(
        await session.scalars(
            select(CrawlJob.id).where(CrawlJob.llm_profile_id == profile_id)
        )
    )
    crawl_run_rows = list(
        await session.execute(
            select(
                CrawlJobRun.id,
                CrawlJobRun.job_id,
                CrawlJobRun.llm_runtime_snapshot,
            )
        )
    )
    crawl_run_ids = {
        run_id
        for run_id, job_id, snapshot in crawl_run_rows
        if _crawl_snapshot_profile_id(snapshot) == profile_id
        or (
            _crawl_snapshot_profile_id(snapshot) is None
            and job_id in direct_crawl_job_ids
        )
    }
    crawl_job_ids = direct_crawl_job_ids | {
        job_id
        for run_id, job_id, _snapshot in crawl_run_rows
        if run_id in crawl_run_ids
    }
    match_analysis_job_items = await session.scalar(
        select(func.count())
        .select_from(MatchAnalysisJobItem)
        .join(MatchAnalysisJob, MatchAnalysisJob.id == MatchAnalysisJobItem.job_id)
        .where(MatchAnalysisJob.llm_profile_id == profile_id)
    )

    async def count_crawl_records(model: Any) -> int:
        if not crawl_job_ids:
            return 0
        value = await session.scalar(
            select(func.count())
            .select_from(model)
            .where(model.job_id.in_(crawl_job_ids))
        )
        return int(value or 0)

    crawl_token_usages = await session.scalar(
        select(func.count())
        .select_from(CrawlWorkerTokenUsage)
        .where(
            or_(
                CrawlWorkerTokenUsage.run_id.in_(crawl_run_ids),
                (
                    CrawlWorkerTokenUsage.run_id.is_(None)
                    & CrawlWorkerTokenUsage.job_id.in_(direct_crawl_job_ids)
                ),
            )
        )
    )

    plans = list(await session.scalars(select(AgentChangePlan)))
    agent_change_plan_count = sum(
        1 for plan in plans if _snapshot_references_profile(plan.snapshot, profile_id)
    )
    return LLMProfileReferenceCounts(
        batch_tasks=await count(BatchTask, BatchTask.llm_profile_id == profile_id),
        email_tasks=await count(EmailTask, EmailTask.llm_profile_id == profile_id),
        email_logs=await count(EmailLog, EmailLog.llm_profile_id == profile_id),
        match_analysis_jobs=await count(
            MatchAnalysisJob,
            MatchAnalysisJob.llm_profile_id == profile_id,
        ),
        match_analysis_job_items=int(match_analysis_job_items or 0),
        match_analysis_runs=await count(
            MatchAnalysisRun,
            MatchAnalysisRun.llm_profile_id == profile_id,
        ),
        test_compose_sessions=await count(
            TestComposeSession,
            TestComposeSession.llm_profile_id == profile_id,
        ),
        test_compose_messages=await count(
            TestComposeMessage,
            TestComposeMessage.llm_profile_id == profile_id,
        ),
        crawl_jobs=len(crawl_job_ids),
        crawl_runs=len(crawl_run_ids),
        crawl_pages=await count_crawl_records(CrawlPage),
        crawl_candidates=await count_crawl_records(CrawlCandidate),
        crawl_token_usages=int(crawl_token_usages or 0),
        match_results=await count(
            IdentityProfessorMatchResult,
            IdentityProfessorMatchResult.llm_profile_id == profile_id,
        ),
        agent_change_plans=agent_change_plan_count,
        operation_logs=await count(
            OperationLog,
            (OperationLog.entity_type == "llm_profile")
            & (OperationLog.entity_id == str(profile_id)),
        ),
    )


async def _deletion_blockers(
    session: AsyncSession,
    profile_id: int,
    *,
    include_retirement_in_progress: bool = True,
) -> list[LLMProfileDeletionBlocker]:
    blockers: list[LLMProfileDeletionBlocker] = []
    active_draft_ids = list(
        await session.scalars(
            select(EmailTask.id)
            .where(
                EmailTask.llm_profile_id == profile_id,
                EmailTask.batch_send_canceled_at.is_(None),
                EmailTask.status == EmailTaskStatus.GENERATING_DRAFT.value,
            )
            .order_by(EmailTask.id.asc())
        )
    )
    _append_blocker(
        blockers,
        kind="draft_generation",
        label="正在生成的 AI 草稿",
        ids=active_draft_ids,
        surface="任务中心 > 发送计划或批量任务详情",
    )
    active_match_job_ids = list(
        await session.scalars(
            select(MatchAnalysisJob.id)
            .where(
                MatchAnalysisJob.llm_profile_id == profile_id,
                MatchAnalysisJob.deleted_at.is_(None),
                MatchAnalysisJob.status.in_(
                    [
                        MatchAnalysisJobStatus.RUNNING.value,
                    ]
                ),
            )
            .order_by(MatchAnalysisJob.id.asc())
        )
    )
    _append_blocker(
        blockers,
        kind="match_analysis",
        label="运行中的匹配分析",
        ids=active_match_job_ids,
        surface="任务中心 > 匹配任务",
    )
    active_match_run_ids = list(
        await session.scalars(
            select(MatchAnalysisRun.id)
            .where(
                MatchAnalysisRun.llm_profile_id == profile_id,
                MatchAnalysisRun.status == "running",
            )
            .order_by(MatchAnalysisRun.id.asc())
        )
    )
    _append_blocker(
        blockers,
        kind="match_analysis_run",
        label="正在执行的单次匹配分析",
        ids=active_match_run_ids,
        surface="任务中心 > 匹配任务",
    )
    active_crawl_rows = list(
        await session.execute(
            select(
                CrawlJob.id,
                CrawlJob.llm_profile_id,
                CrawlJobRun.llm_runtime_snapshot,
            )
            .outerjoin(CrawlJobRun, CrawlJobRun.id == CrawlJob.current_run_id)
            .where(
                CrawlJob.deleted_at.is_(None),
                CrawlJob.status.in_(
                    [CrawlJobStatus.RUNNING.value]
                ),
            )
            .order_by(CrawlJob.id.asc())
        )
    )
    active_crawl_job_ids = [
        job_id
        for job_id, job_profile_id, snapshot in active_crawl_rows
        if job_profile_id == profile_id
        or _crawl_snapshot_profile_id(snapshot) == profile_id
    ]
    _append_blocker(
        blockers,
        kind="crawl_job",
        label="正在运行的智能抓取或信息补全任务",
        ids=active_crawl_job_ids,
        surface="任务中心 > 智能抓取或信息补全",
    )
    for kind, count in sorted(get_llm_profile_usage_counts(profile_id).items()):
        if count <= 0:
            continue
        blockers.append(
            LLMProfileDeletionBlocker(
                kind=f"interactive_{kind}",
                label=INTERACTIVE_USAGE_LABELS.get(kind, "正在进行的模型请求"),
                count=count,
                entity_ids=[],
                surface="当前操作，无需另行定位；等待操作结束后重试",
            )
        )
    if (
        include_retirement_in_progress
        and llm_profile_retirement_in_progress(profile_id)
    ):
        blockers.append(
            LLMProfileDeletionBlocker(
                kind="retirement_in_progress",
                label="另一个删除请求正在处理此模型配置",
                count=1,
                entity_ids=[],
                surface="模型配置页面；等待当前删除操作完成后刷新",
            )
        )
    return blockers


async def _automatic_actions(
    session: AsyncSession,
    profile_id: int,
) -> LLMProfileDeletionAutomaticActions:
    cancel_email_task_ids = list(
        await session.scalars(
            select(EmailTask.id)
            .join(BatchTask, BatchTask.id == EmailTask.batch_task_id)
            .where(
                EmailTask.llm_profile_id == profile_id,
                EmailTask.batch_send_canceled_at.is_(None),
                BatchTask.status == BatchTaskStatus.RUNNING.value,
                EmailTask.status.in_(
                    [
                        EmailTaskStatus.DISCOVERED.value,
                        EmailTaskStatus.MATCHED.value,
                    ]
                ),
                batch_item_uses_llm_generation_column(
                    EmailTask.outreach_generation_mode
                ),
            )
            .order_by(EmailTask.id.asc())
        )
    )
    cancel_match_analysis_job_ids = list(
        await session.scalars(
            select(MatchAnalysisJob.id)
            .where(
                MatchAnalysisJob.llm_profile_id == profile_id,
                MatchAnalysisJob.deleted_at.is_(None),
                MatchAnalysisJob.status == MatchAnalysisJobStatus.QUEUED.value,
            )
            .order_by(MatchAnalysisJob.id.asc())
        )
    )
    queued_crawl_rows = list(
        await session.execute(
            select(
                CrawlJob.id,
                CrawlJob.llm_profile_id,
                CrawlJobRun.llm_runtime_snapshot,
            )
            .outerjoin(CrawlJobRun, CrawlJobRun.id == CrawlJob.current_run_id)
            .where(
                CrawlJob.deleted_at.is_(None),
                CrawlJob.status == CrawlJobStatus.QUEUED.value,
            )
            .order_by(CrawlJob.id.asc())
        )
    )
    cancel_crawl_job_ids = [
        int(job_id)
        for job_id, job_profile_id, snapshot in queued_crawl_rows
        if job_profile_id == profile_id
        or _crawl_snapshot_profile_id(snapshot) == profile_id
    ]
    return LLMProfileDeletionAutomaticActions(
        cancel_email_task_ids=[int(item) for item in cancel_email_task_ids],
        cancel_match_analysis_job_ids=[
            int(item) for item in cancel_match_analysis_job_ids
        ],
        cancel_crawl_job_ids=cancel_crawl_job_ids,
    )


async def _cancel_waiting_draft_tasks(
    session: AsyncSession,
    task_ids: list[int],
    *,
    now: datetime,
) -> None:
    if not task_ids:
        return
    canceled_rows = list(
        await session.execute(
            update(EmailTask)
            .where(
                EmailTask.id.in_(task_ids),
                EmailTask.status.in_(
                    [
                        EmailTaskStatus.DISCOVERED.value,
                        EmailTaskStatus.MATCHED.value,
                    ]
                ),
            )
            .values(
                status=EmailTaskStatus.CANCELED.value,
                cancellation_reason=(
                    EmailTaskCancellationReason.LLM_PROFILE_RETIRED.value
                ),
                draft_generation_previous_status=None,
                draft_generation_started_at=None,
                draft_claim_id=None,
                draft_claimed_at=None,
                draft_lease_expires_at=None,
                updated_at=now,
            )
            .returning(EmailTask.id, EmailTask.batch_task_id)
            .execution_options(synchronize_session=False)
        )
    )
    canceled_by_batch: dict[int, int] = {}
    for _task_id, batch_task_id in canceled_rows:
        if batch_task_id is None:
            continue
        canceled_by_batch[int(batch_task_id)] = (
            canceled_by_batch.get(int(batch_task_id), 0) + 1
        )
    for batch_task_id, canceled_count in canceled_by_batch.items():
        await session.execute(
            update(BatchTask)
            .where(BatchTask.id == batch_task_id)
            .values(
                target_count=case(
                    (
                        BatchTask.target_count > canceled_count,
                        BatchTask.target_count - canceled_count,
                    ),
                    else_=0,
                ),
                status=case(
                    (
                        BatchTask.target_count <= canceled_count,
                        BatchTaskStatus.COMPLETED.value,
                    ),
                    else_=BatchTask.status,
                ),
                updated_at=now,
            )
            .execution_options(synchronize_session=False)
        )


def _append_blocker(
    blockers: list[LLMProfileDeletionBlocker],
    *,
    kind: str,
    label: str,
    ids: list[int],
    surface: str,
) -> None:
    if not ids:
        return
    blockers.append(
        LLMProfileDeletionBlocker(
            kind=kind,
            label=label,
            count=len(ids),
            entity_ids=ids[:MAX_BLOCKER_IDS],
            surface=surface,
        )
    )


def _deletion_revision(
    profile: LLMProfile,
    references: LLMProfileReferenceCounts,
    automatic_actions: LLMProfileDeletionAutomaticActions,
    blockers: list[LLMProfileDeletionBlocker],
) -> str:
    payload = {
        "profile_id": profile.id,
        "updated_at": _serialize_datetime(profile.updated_at),
        "is_default": profile.is_default,
        "references": references.model_dump(mode="json"),
        "automatic_actions": automatic_actions.model_dump(mode="json"),
        "blockers": [item.model_dump(mode="json") for item in blockers],
    }
    encoded = json.dumps(payload, ensure_ascii=True, sort_keys=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _serialize_datetime(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _blocker_message(
    profile_name: str,
    blockers: list[LLMProfileDeletionBlocker],
) -> str:
    summary = "、".join(f"{item.label} {item.count} 项" for item in blockers)
    return (
        f"模型配置“{profile_name}”仍被以下操作使用：{summary}。"
        "请等待短时操作结束，或暂停/取消对应后台任务后再删除。"
    )


async def _sanitize_crawl_runtime_snapshots(
    session: AsyncSession,
    profile_id: int,
) -> None:
    runs = list(await session.scalars(select(CrawlJobRun)))
    for run in runs:
        if not isinstance(run.llm_runtime_snapshot, dict):
            continue
        if _crawl_snapshot_profile_id(run.llm_runtime_snapshot) != profile_id:
            continue
        snapshot = dict(run.llm_runtime_snapshot)
        changed = False
        for field in SENSITIVE_CRAWL_SNAPSHOT_FIELDS:
            value = snapshot.pop(field, None)
            if value is not None:
                changed = True
        if changed:
            run.llm_runtime_snapshot = snapshot


def _crawl_snapshot_profile_id(snapshot: object) -> int | None:
    if not isinstance(snapshot, dict):
        return None
    profile_id = snapshot.get("profile_id")
    return profile_id if isinstance(profile_id, int) else None


async def _delete_unshared_adaptation_caches(
    session: AsyncSession,
    profile: LLMProfile,
) -> None:
    normalized_base_url = resolve_base_url(profile.api_base_url)
    active_profiles = list(
        await session.scalars(
            select(LLMProfile).where(
                LLMProfile.id != profile.id,
                LLMProfile.deleted_at.is_(None),
                LLMProfile.model_name == profile.model_name,
            )
        )
    )
    if any(
        resolve_base_url(candidate.api_base_url) == normalized_base_url
        for candidate in active_profiles
    ):
        return
    for model in (
        LLMEndpointAdaptationCache,
        ThinkingAdaptationCache,
        LLMStructuredOutputAdaptationCache,
    ):
        rows = list(
            await session.scalars(
                select(model).where(
                    model.api_base_url == normalized_base_url,
                    model.model_name == profile.model_name,
                )
            )
        )
        for row in rows:
            await session.delete(row)


async def _invalidate_pending_change_plans(
    session: AsyncSession,
    *,
    profile_id: int,
    now: datetime,
) -> int:
    plans = list(
        await session.scalars(
            select(AgentChangePlan).where(
                AgentChangePlan.status == "awaiting_confirmation"
            )
        )
    )
    invalidated = 0
    for plan in plans:
        if not _snapshot_references_profile(plan.snapshot, profile_id):
            continue
        plan.status = "canceled"
        plan.canceled_at = now
        plan.failure_message = "关联模型配置已删除，请重新发起操作"
        plan.updated_at = now
        invalidated += 1
    return invalidated


def _snapshot_references_profile(value: object, profile_id: int) -> bool:
    if isinstance(value, dict):
        for key, child in value.items():
            if key == "llm_profile_id" and child == profile_id:
                return True
            if _snapshot_references_profile(child, profile_id):
                return True
    elif isinstance(value, list):
        return any(_snapshot_references_profile(child, profile_id) for child in value)
    return False
