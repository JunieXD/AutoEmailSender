from __future__ import annotations

import hashlib
import json

from datetime import datetime
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.time import utc_now
from app.models import (
    AgentChangePlan,
    BatchTask,
    BatchTaskStatus,
    CrawlJob,
    CrawlJobRun,
    CrawlJobStatus,
    CrawlCandidate,
    CrawlPage,
    CrawlWorkerTokenUsage,
    EmailLog,
    EmailTask,
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
    blockers = await _deletion_blockers(
        session,
        profile.id,
        include_retirement_in_progress=include_retirement_in_progress,
    )
    revision = _deletion_revision(profile, references, blockers)
    return LLMProfileDeletionImpact(
        profile_id=profile.id,
        profile_name=profile.name,
        model_name=profile.model_name,
        is_default=profile.is_default,
        can_delete=not blockers,
        revision=revision,
        references=references,
        blockers=blockers,
        warnings=[
            "活动、邮件、通信、匹配、抓取和 Token 用量历史都会保留。",
            "暂停、失败或已取消任务再次运行时必须选择可用模型，不会自动改用默认模型。",
            "应用会清除本地可执行凭据；服务商侧密钥仍建议同步撤销或轮换。",
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
            .join(BatchTask, BatchTask.id == EmailTask.batch_task_id, isouter=True)
            .where(
                EmailTask.llm_profile_id == profile_id,
                EmailTask.batch_send_canceled_at.is_(None),
                (
                    (EmailTask.status == EmailTaskStatus.GENERATING_DRAFT.value)
                    | (
                        BatchTask.status == BatchTaskStatus.RUNNING.value
                    )
                    & EmailTask.status.in_(
                        [
                            EmailTaskStatus.DISCOVERED.value,
                            EmailTaskStatus.MATCHED.value,
                        ]
                    )
                    & batch_item_uses_llm_generation_column(
                        EmailTask.outreach_generation_mode
                    )
                ),
            )
            .order_by(EmailTask.id.asc())
        )
    )
    _append_blocker(
        blockers,
        kind="draft_generation",
        label="正在生成或等待生成的 AI 草稿",
        ids=active_draft_ids,
    )
    active_match_job_ids = list(
        await session.scalars(
            select(MatchAnalysisJob.id)
            .where(
                MatchAnalysisJob.llm_profile_id == profile_id,
                MatchAnalysisJob.deleted_at.is_(None),
                MatchAnalysisJob.status.in_(
                    [
                        MatchAnalysisJobStatus.QUEUED.value,
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
        label="排队或运行中的匹配分析",
        ids=active_match_job_ids,
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
                    [CrawlJobStatus.QUEUED.value, CrawlJobStatus.RUNNING.value]
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
        label="排队或运行中的抓取/信息补全任务",
        ids=active_crawl_job_ids,
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
            )
        )
    return blockers


def _append_blocker(
    blockers: list[LLMProfileDeletionBlocker],
    *,
    kind: str,
    label: str,
    ids: list[int],
) -> None:
    if not ids:
        return
    blockers.append(
        LLMProfileDeletionBlocker(
            kind=kind,
            label=label,
            count=len(ids),
            entity_ids=ids[:MAX_BLOCKER_IDS],
        )
    )


def _deletion_revision(
    profile: LLMProfile,
    references: LLMProfileReferenceCounts,
    blockers: list[LLMProfileDeletionBlocker],
) -> str:
    payload = {
        "profile_id": profile.id,
        "updated_at": _serialize_datetime(profile.updated_at),
        "is_default": profile.is_default,
        "references": references.model_dump(mode="json"),
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
