from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

import app.modules.llm.public as llm_runtime
from app.core.time import as_utc_aware, utc_now
from app.models import (
    BatchTaskStatus,
    EmailTask,
    EmailTaskStatus,
    IdentityMaterial,
    IdentityProfile,
    LLMProfile,
    MatchAnalysisRun,
    Professor,
)
from app.modules.llm.public import (
    DELETED_LLM_PROFILE_MESSAGE,
    get_active_llm_profile,
    llm_profile_is_active,
    track_llm_profile_usage,
)
from app.modules.communications.public import (
    load_email_task as _load_email_task,
)
from app.modules.communications.public import (
    record_email_task_log as _record_email_task_log,
)
from app.modules.identities.public import (
    ensure_material_extracted_text,
    material_can_be_primary,
)
from app.modules.system.public import get_runtime_settings
from app.services.match_results import (
    apply_match_result_snapshot_to_task,
    resolve_identity_match_scope,
    upsert_identity_professor_match_result,
)

INTERRUPTED_MATCH_ANALYSIS_RUN_ERROR = "匹配分析因桌面端进程中断而停止"


def _has_professor_match_evidence(professor: Professor) -> bool:
    return bool((professor.research_direction or "").strip()) or any(
        str(paper).strip() for paper in professor.recent_papers or []
    )


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
    match_source_identity_id: int
    llm_profile_id: int
    usage: MatchUsageSummary
    run_id: int | None = None


class MatchAnalysisAlreadyRunningError(RuntimeError):
    pass


class MatchCalculationCanceledError(RuntimeError):
    pass


async def recover_interrupted_match_analysis_runs(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    now: datetime | None = None,
) -> int:
    resolved_now = as_utc_aware(now) if now is not None else utc_now()
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


async def calculate_task_match(
    session_factory: async_sessionmaker[AsyncSession],
    task_id: int,
    *,
    force: bool,
    ignore_batch_status: bool = False,
    cancel_requested: Callable[[], Awaitable[bool]] | None = None,
    llm_profile_id: int | None = None,
    match_source_identity_id: int | None = None,
) -> MatchCalculationActionResult:
    async with session_factory() as session:
        task = await _load_email_task(session, task_id)
        if not task:
            raise ValueError(f"EmailTask {task_id} 不存在")
        match_scope = await resolve_identity_match_scope(
            session,
            active_identity_id=task.identity_id,
            match_source_identity_id=match_source_identity_id,
        )
        runtime_llm_profile = await _resolve_runtime_llm_profile(
            session, task, llm_profile_id
        )
        if (
            task.batch_task
            and task.batch_task.status != BatchTaskStatus.RUNNING.value
            and not ignore_batch_status
        ):
            return _match_action_result(
                active_identity_id=task.identity_id,
                professor_id=task.professor_id,
                llm_profile_id=runtime_llm_profile.id,
                match_source_identity_id=match_scope.source_identity_id,
            )
        return await _calculate_identity_professor_match(
            session,
            active_identity_id=task.identity_id,
            match_identity=match_scope.source_identity,
            professor=task.professor,
            llm_profile=runtime_llm_profile,
            source_task=task,
            force=force,
            cancel_requested=cancel_requested,
        )


async def calculate_identity_professor_match(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    active_identity_id: int,
    professor_id: int,
    llm_profile_id: int,
    match_source_identity_id: int | None = None,
    cancel_requested: Callable[[], Awaitable[bool]] | None = None,
) -> MatchCalculationActionResult:
    """Calculate a canonical mentor match without creating an email task."""

    async with session_factory() as session:
        match_scope = await resolve_identity_match_scope(
            session,
            active_identity_id=active_identity_id,
            match_source_identity_id=match_source_identity_id,
        )
        professor = await session.get(Professor, professor_id)
        if professor is None:
            raise ValueError("导师不存在")
        llm_profile = await get_active_llm_profile(session, llm_profile_id)
        if llm_profile is None:
            raise ValueError(DELETED_LLM_PROFILE_MESSAGE)

        return await _calculate_identity_professor_match(
            session,
            active_identity_id=active_identity_id,
            match_identity=match_scope.source_identity,
            professor=professor,
            llm_profile=llm_profile,
            source_task=None,
            force=True,
            cancel_requested=cancel_requested,
        )


async def _calculate_identity_professor_match(
    session: AsyncSession,
    *,
    active_identity_id: int,
    match_identity: IdentityProfile,
    professor: Professor,
    llm_profile: LLMProfile,
    source_task: EmailTask | None,
    force: bool,
    cancel_requested: Callable[[], Awaitable[bool]] | None,
) -> MatchCalculationActionResult:
    try:
        match_material = await _resolve_match_primary_material(session, match_identity)
    except ValueError:
        if force:
            raise
        return _match_action_result(
            active_identity_id=active_identity_id,
            professor_id=professor.id,
            llm_profile_id=llm_profile.id,
            match_source_identity_id=match_identity.id,
        )
    ensure_material_extracted_text(match_material)
    if not _has_professor_match_evidence(professor):
        raise ValueError("缺少研究方向或近期论文，暂不能分析匹配度")
    if (
        source_task is not None
        and not force
        and source_task.status
        in {
            EmailTaskStatus.MATCHED.value,
            EmailTaskStatus.REVIEW_REQUIRED.value,
            EmailTaskStatus.APPROVED.value,
            EmailTaskStatus.SCHEDULED.value,
            EmailTaskStatus.SENT.value,
            EmailTaskStatus.REPLY_DETECTED.value,
        }
    ):
        return _match_action_result(
            active_identity_id=active_identity_id,
            professor_id=professor.id,
            llm_profile_id=llm_profile.id,
            match_source_identity_id=match_identity.id,
        )

    if source_task is not None:
        source_task.llm_profile_id = llm_profile.id
    runtime_settings = await get_runtime_settings(session)
    with track_llm_profile_usage(llm_profile.id, "match_analysis_startup"):
        adaptation = await llm_runtime.ensure_llm_runtime_adaptation(
            session,
            llm_profile,
        )
        run = await _create_running_match_analysis_run(
            session,
            email_task_id=source_task.id if source_task is not None else None,
            professor_id=professor.id,
            match_identity=match_identity,
            llm_profile_id=llm_profile.id,
            primary_material=match_material,
        )
        await session.commit()
    try:
        generation = await llm_runtime.generate_match_evaluation(
            identity=match_identity,
            primary_material=match_material,
            llm_profile=llm_profile,
            professor=professor,
            available_materials=[],
            intended_research_direction=runtime_settings.intended_research_direction,
            session=session,
            adaptation=adaptation,
        )
    except asyncio.CancelledError:
        _mark_match_analysis_run_failed(
            run,
            error_kind="canceled",
            error_message="匹配分析任务已取消",
        )
        if source_task is not None:
            source_task.updated_at = utc_now()
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
        if source_task is not None:
            source_task.last_error = str(exc)
            source_task.updated_at = utc_now()
        await session.commit()
        return _match_action_result(
            active_identity_id=active_identity_id,
            professor_id=professor.id,
            llm_profile_id=llm_profile.id,
            match_source_identity_id=match_identity.id,
            run_id=run.id,
        )
    except Exception as exc:
        _mark_match_analysis_run_failed(
            run,
            error_kind="unexpected",
            error_message=str(exc),
        )
        if source_task is not None:
            source_task.last_error = str(exc)
            source_task.updated_at = utc_now()
        await session.commit()
        raise

    if cancel_requested is not None and await cancel_requested():
        _mark_match_analysis_run_failed(
            run,
            error_kind="canceled",
            error_message="匹配分析任务已取消",
        )
        if source_task is not None:
            source_task.updated_at = utc_now()
        await session.commit()
        raise MatchCalculationCanceledError("匹配分析任务已取消")

    result = generation.result
    run.status = "succeeded"
    run.success = True
    run.match_score = result.match_score
    run.match_reason = result.match_reason
    run.fit_points = list(result.fit_points)
    run.risk_points = list(result.risk_points)
    run.match_keywords = list(result.keywords)
    run.prompt_tokens = generation.usage.prompt_tokens if generation.usage else None
    run.completion_tokens = (
        generation.usage.completion_tokens if generation.usage else None
    )
    run.total_tokens = generation.usage.total_tokens if generation.usage else None
    run.cached_tokens = generation.usage.cached_tokens if generation.usage else None
    run.duration_ms = generation.duration_ms
    run.endpoint_kind = generation.endpoint_kind
    run.status_code = generation.status_code
    run.prompt_hash = generation.prompt_hash
    run.stable_prefix_hash = generation.stable_prefix_hash
    run.error_kind = None
    run.error_message = None
    run.finished_at = utc_now()
    canonical_result = await upsert_identity_professor_match_result(
        session,
        identity_id=match_identity.id,
        professor_id=professor.id,
        llm_profile_id=llm_profile.id,
        primary_material_id=match_material.id,
        source_email_task_id=source_task.id if source_task is not None else None,
        analysis_run=run,
        match_score=result.match_score,
        match_reason=result.match_reason,
        fit_points=result.fit_points,
        risk_points=result.risk_points,
        match_keywords=result.keywords,
    )
    if source_task is not None:
        apply_match_result_snapshot_to_task(
            source_task,
            match_source_identity_id=match_identity.id,
            match_score=result.match_score,
            match_reason=result.match_reason,
            fit_points=result.fit_points,
            risk_points=result.risk_points,
            match_keywords=result.keywords,
        )
        if source_task.status in {
            EmailTaskStatus.DISCOVERED.value,
            EmailTaskStatus.MATCHED.value,
            EmailTaskStatus.DRAFT_FAILED.value,
        }:
            source_task.status = EmailTaskStatus.MATCHED.value
        source_task.updated_at = utc_now()
        source_task.last_error = None
        await _record_email_task_log(
            session,
            source_task,
            "email_task.match_calculated",
            metadata={
                "match_analysis_run_id": run.id,
                "match_result_id": canonical_result.id,
                "match_source_identity_id": match_identity.id,
                "match_score": result.match_score,
                "force": force,
            },
        )
    await session.commit()
    return _match_action_result(
        active_identity_id=active_identity_id,
        professor_id=professor.id,
        llm_profile_id=llm_profile.id,
        match_source_identity_id=match_identity.id,
        usage=_match_usage_summary(generation.usage),
        run_id=run.id,
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
    *,
    active_identity_id: int,
    professor_id: int,
    llm_profile_id: int,
    match_source_identity_id: int,
    usage: MatchUsageSummary | None = None,
    run_id: int | None = None,
) -> MatchCalculationActionResult:
    return MatchCalculationActionResult(
        professor_id=professor_id,
        identity_id=active_identity_id,
        match_source_identity_id=match_source_identity_id,
        llm_profile_id=llm_profile_id,
        usage=usage or MatchUsageSummary(),
        run_id=run_id,
    )


async def _create_running_match_analysis_run(
    session: AsyncSession,
    *,
    email_task_id: int | None,
    professor_id: int,
    match_identity: IdentityProfile,
    llm_profile_id: int,
    primary_material: IdentityMaterial,
) -> MatchAnalysisRun:
    run = MatchAnalysisRun(
        email_task_id=email_task_id,
        professor_id=professor_id,
        identity_id=match_identity.id,
        llm_profile_id=llm_profile_id,
        primary_material_id=primary_material.id,
        status="running",
        success=False,
        started_at=utc_now(),
    )
    session.add(run)
    try:
        await session.flush()
    except IntegrityError as exc:
        await session.rollback()
        raise MatchAnalysisAlreadyRunningError("该任务正在分析中") from exc
    return run


async def _resolve_match_primary_material(
    session: AsyncSession,
    identity: IdentityProfile,
) -> IdentityMaterial:
    material = identity.current_primary_material
    if material is None:
        material_id = identity.current_primary_material_id
        if material_id is not None:
            material = await session.get(IdentityMaterial, material_id)
    if material is None:
        raise ValueError("请到个人页设置默认材料")
    if not material_can_be_primary(material):
        raise ValueError("个人页默认材料不支持匹配分析")
    return material


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
    run.finished_at = utc_now()


async def calculate_task_match_once(
    session_factory: async_sessionmaker[AsyncSession],
    task_id: int,
    *,
    llm_profile_id: int | None = None,
) -> MatchCalculationActionResult:
    return await calculate_task_match(
        session_factory,
        task_id,
        force=True,
        llm_profile_id=llm_profile_id,
    )


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
