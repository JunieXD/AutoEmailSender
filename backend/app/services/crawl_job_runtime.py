from __future__ import annotations

import asyncio
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlparse

import httpx
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import get_settings
from app.models import CrawlCandidate, CrawlJob, CrawlJobRun, CrawlJobStatus, CrawlPage, CrawlPageChunk, CrawlPageChunkStatus, LLMProfile
from app.services.crawler_debug import append_crawler_debug_event
from app.services.crawler_chunking import ChunkingConfig, build_page_chunks
from app.services.crawler_chunk_runtime import create_chunks_for_page
from app.services.crawl_job_events import normalize_agent_trace_event
from app.services.crawl_job_runs import (
    accumulate_crawl_job_run_tokens,
    extract_token_usage_from_llm_response,
    get_or_create_current_crawl_job_run,
    mark_crawl_job_run_finished,
    mark_crawl_job_run_paused,
    mark_crawl_job_run_running,
)
from app.services.runtime_settings import get_runtime_settings
from app.services.llm_runtime import LLMRuntimeError, parse_structured_result
from app.services.thinking_adaptation import (
    ThinkingAdaptationFailed,
    adapt_failure_message_for_thinking_error,
    ensure_thinking_adaptation,
)
from app.services.crawler_tools import (
    CrawlJobCanceled,
    CrawlJobPaused,
    CrawlJobSaveBudgetExceeded,
    CrawlToolContext,
    CandidateEnrichmentPayload,
    ProfessorCandidatePayload,
    PageSnapshot,
    build_candidate_enrichment_prompt,
    build_profile_candidate_prompt,
    crawl_page_with_crawl4ai,
    ensure_crawl_job_can_continue,
    save_candidates,
)


NO_LLM_PROFILE_ERROR = "请先配置可用的 LLM Profile"
WORKER_CANCELLED_ERROR = "抓取任务被后台 worker 取消"
INTERRUPTED_JOB_ERROR = "抓取任务因桌面端进程中断而停止"
NO_CANDIDATES_SAVED_ERROR = "抓取结束但未成功保存任何候选导师"


async def crawl_job_has_pending_work(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    job_id: int,
) -> bool:
    async with session_factory() as session:
        return await _crawl_job_has_pending_work_in_session(session, job_id=job_id)


async def _crawl_job_has_pending_work_in_session(session: AsyncSession, *, job_id: int) -> bool:
    pending_chunk = await session.scalar(
        select(CrawlPageChunk.id)
        .where(
            CrawlPageChunk.job_id == job_id,
            CrawlPageChunk.status.in_(
                [
                    CrawlPageChunkStatus.PENDING.value,
                    CrawlPageChunkStatus.PROCESSING.value,
                    CrawlPageChunkStatus.SPLIT_REQUIRED.value,
                ]
            ),
        )
        .limit(1)
    )
    return pending_chunk is not None


async def create_chunks_for_successful_page_snapshot(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    job_id: int,
    page_id: int | None,
    snapshot: PageSnapshot,
) -> int:
    if snapshot.status != "succeeded":
        return 0
    if not snapshot.text.strip() and not snapshot.html.strip():
        return 0
    drafts = build_page_chunks(
        source_url=snapshot.url,
        html=snapshot.html,
        text=snapshot.text,
        config=ChunkingConfig(),
    )
    return await create_chunks_for_page(session_factory, job_id=job_id, page_id=page_id, drafts=drafts)
PROFILE_EXTRACTION_FAILED_ERROR = "未能从详情页识别导师信息"
INVALID_SAVE_TOOL_CALL_ERROR = (
    "抓取结果未成功保存：Agent 生成了无效的 save_professor_candidates 调用"
)
TRUNCATED_SAVE_TOOL_CALL_ERROR = (
    "抓取结果未成功保存：模型在调用 save_professor_candidates 时输出被截断"
)
MAX_AGENT_TRACE_EVENTS = 100
DIRECT_LLM_STRUCTURED_MAX_ATTEMPTS = 2
_ACTIVE_CRAWL_JOB_IDS: set[int] = set()


@dataclass(slots=True)
class CandidateEnrichmentWorkItem:
    candidate_id: int
    candidate_name: str
    profile_url: str


@dataclass(slots=True)
class CandidateEnrichmentResult:
    candidate_id: int
    candidate_name: str
    profile_url: str
    status: str
    enrichment: CandidateEnrichmentPayload | None = None
    updated_fields: list[str] | None = None
    error_message: str | None = None
    retry_count: int = 0
    host_limited: bool = False


@dataclass(slots=True)
class SelectedCandidateEnrichmentSummary:
    selected_count: int
    enriched_count: int
    unchanged_count: int
    failed_count: int


@dataclass(slots=True)
class CrawlRuntimeConcurrency:
    profile_enrichment_concurrency: int
    host_concurrency: int


def resolve_crawl_runtime_concurrency(settings: Any) -> CrawlRuntimeConcurrency:
    return CrawlRuntimeConcurrency(
        profile_enrichment_concurrency=max(1, settings.crawler_profile_enrichment_concurrency),
        host_concurrency=max(1, settings.crawler_host_concurrency),
    )


async def run_queued_crawl_jobs_once(
    session_factory: async_sessionmaker[AsyncSession],
) -> int:
    async with session_factory() as session:
        job_id = await session.scalar(
            select(CrawlJob.id)
            .where(
                CrawlJob.status == CrawlJobStatus.QUEUED.value,
                CrawlJob.deleted_at.is_(None),
            )
            .order_by(CrawlJob.created_at.asc(), CrawlJob.id.asc())
            .limit(1),
        )
        if job_id is None:
            return 0

        now = datetime.now(UTC)
        claim_result = await session.execute(
            update(CrawlJob)
            .where(
                CrawlJob.id == job_id,
                CrawlJob.status == CrawlJobStatus.QUEUED.value,
                CrawlJob.deleted_at.is_(None),
            )
            .values(
                status=CrawlJobStatus.RUNNING.value,
                error_message=None,
                updated_at=now,
            ),
        )
        if claim_result.rowcount != 1:
            await session.rollback()
            return 0
        _ACTIVE_CRAWL_JOB_IDS.add(job_id)

        job = await session.scalar(
            select(CrawlJob)
            .where(CrawlJob.id == job_id)
            .limit(1),
        )
        if job is None:
            _ACTIVE_CRAWL_JOB_IDS.discard(job_id)
            await session.rollback()
            return 0

        await mark_crawl_job_run_running(session, job, now=now)
        llm_profile = await _resolve_llm_profile(session, job)
        if llm_profile is None:
            failed_at = datetime.now(UTC)
            job.status = CrawlJobStatus.FAILED.value
            job.error_message = NO_LLM_PROFILE_ERROR
            job.updated_at = failed_at
            await mark_crawl_job_run_finished(
                session,
                job,
                status=CrawlJobStatus.FAILED.value,
                error_message=NO_LLM_PROFILE_ERROR,
                now=failed_at,
            )
            await session.commit()
            _ACTIVE_CRAWL_JOB_IDS.discard(job_id)
            return 1

        try:
            thinking_extra_body = await ensure_thinking_adaptation(session, llm_profile)
        except ThinkingAdaptationFailed as exc:
            failed_at = datetime.now(UTC)
            job.status = CrawlJobStatus.FAILED.value
            job.error_message = (
                "思考模式自适应失败：已尝试全部候选 extra_body 仍无法绕开协议错。"
                "请在 LLM Profile 设置中确认模型是否支持，或联系开发者扩展候选列表。"
            )
            job.updated_at = failed_at
            await mark_crawl_job_run_finished(
                session,
                job,
                status=CrawlJobStatus.FAILED.value,
                error_message=job.error_message,
                now=failed_at,
            )
            await session.commit()
            return 1
        except LLMRuntimeError as exc:
            failed_at = datetime.now(UTC)
            job.status = CrawlJobStatus.FAILED.value
            job.error_message = f"思考模式探活失败：{exc}"
            job.updated_at = failed_at
            await mark_crawl_job_run_finished(
                session,
                job,
                status=CrawlJobStatus.FAILED.value,
                error_message=job.error_message,
                now=failed_at,
            )
            await session.commit()
            return 1

        await session.commit()

        job_id = job.id
        start_urls = _get_crawl_job_start_urls(job)
        ctx = CrawlToolContext(
            job_id=job.id,
            start_url=start_urls[0],
            university=job.university,
            school=job.school,
            session_factory=session_factory,
            thinking_extra_body=thinking_extra_body,
        )

    async def trace_callback(event: Any) -> None:
        append_crawler_debug_event(job_id, event)
        await _append_agent_trace(session_factory, job_id, event)

    try:
        for start_url in start_urls:
            entry_ctx = replace(ctx, start_url=start_url)
            try:
                if job.entry_type == "profile":
                    await _run_profile_crawl_job(
                        session_factory,
                        entry_ctx,
                        llm_profile=llm_profile,
                        trace_callback=trace_callback,
                    )
                else:
                    await run_faculty_crawler_agent(
                        entry_ctx,
                        llm_profile,
                        trace_callback=trace_callback,
                        extra_body=entry_ctx.thinking_extra_body,
                    )
            except (CrawlJobPaused, CrawlJobCanceled, CrawlJobSaveBudgetExceeded, asyncio.CancelledError):
                raise
            except Exception as exc:
                if len(start_urls) == 1:
                    raise
                await _emit_trace_event(
                    trace_callback,
                    {
                        "event_type": "start_url_failed",
                        "message": f"入口 URL 抓取失败：{exc}",
                        "created_at": datetime.now(UTC).isoformat(),
                        "raw": {"url": start_url},
                    },
                )
                continue
        await _complete_running_job(session_factory, job_id)
    except CrawlJobPaused:
        await _emit_trace_event(
            trace_callback,
            {
                "event_type": "job_control",
                "message": "任务已暂停，已保留当前抓取结果",
                "created_at": datetime.now(UTC).isoformat(),
            },
        )
        await _mark_job_paused(session_factory, job_id)
    except CrawlJobCanceled:
        await _emit_trace_event(
            trace_callback,
            {
                "event_type": "job_control",
                "message": "任务已取消",
                "created_at": datetime.now(UTC).isoformat(),
            },
        )
        await _mark_job_canceled(session_factory, job_id)
    except CrawlJobSaveBudgetExceeded as exc:
        await _emit_trace_event(
            trace_callback,
            {
                "event_type": "save_failure_circuit_breaker",
                "message": str(exc),
                "failure_fingerprint": exc.failure_fingerprint,
                "consecutive_same_batch_failures": exc.same_batch_save_failures,
                "total_save_failures": exc.total_save_failures,
                "terminal_reason": exc.terminal_reason,
                "latest_failure_summary": exc.latest_failure_summary,
                "created_at": datetime.now(UTC).isoformat(),
            },
        )
        await _mark_job_failed(session_factory, job_id, str(exc))
    except asyncio.CancelledError:
        await _mark_job_failed(session_factory, job_id, WORKER_CANCELLED_ERROR)
        raise
    except Exception as exc:
        await _mark_job_failed(session_factory, job_id, str(exc))
    finally:
        _ACTIVE_CRAWL_JOB_IDS.discard(job_id)

    return 1


async def recover_interrupted_crawl_jobs(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        running_job_ids = list(
            await session.scalars(
                select(CrawlJob.id)
                .where(
                    CrawlJob.status == CrawlJobStatus.RUNNING.value,
                    CrawlJob.deleted_at.is_(None),
                )
                .order_by(CrawlJob.created_at.asc(), CrawlJob.id.asc()),
            )
        )

    for job_id in running_job_ids:
        if job_id in _ACTIVE_CRAWL_JOB_IDS:
            continue
        await _recover_interrupted_crawl_job(session_factory, job_id)


async def _recover_interrupted_crawl_job(
    session_factory: async_sessionmaker[AsyncSession],
    job_id: int,
) -> None:
    async with session_factory() as session:
        job = await session.get(CrawlJob, job_id)
        if job is None or job.status != CrawlJobStatus.RUNNING.value:
            return

        if await _crawl_job_has_pending_work_in_session(session, job_id=job_id):
            now = datetime.now(UTC)
            job.updated_at = now
            if job.current_run_id is not None:
                run = await session.get(CrawlJobRun, job.current_run_id)
                if run is not None and run.status == CrawlJobStatus.RUNNING.value:
                    run.updated_at = now
            await session.commit()
            return

        candidate_count = await session.scalar(
            select(func.count()).select_from(CrawlCandidate).where(CrawlCandidate.job_id == job_id)
        )
        now = datetime.now(UTC)
        if int(candidate_count or 0) > 0:
            job.status = CrawlJobStatus.NEEDS_REVIEW.value
            job.error_message = None
            error_message = None
        else:
            job.status = CrawlJobStatus.FAILED.value
            job.error_message = INTERRUPTED_JOB_ERROR
            error_message = INTERRUPTED_JOB_ERROR

        trace = list(_normalize_trace(job.agent_trace))
        trace.append(
            {
                "event_type": "job_recovered",
                "message": INTERRUPTED_JOB_ERROR,
                "created_at": now.isoformat(),
            }
        )
        job.agent_trace = trace[-MAX_AGENT_TRACE_EVENTS:]
        job.updated_at = now
        await mark_crawl_job_run_finished(
            session,
            job,
            status=job.status,
            error_message=error_message,
            now=now,
        )
        await session.commit()


def _get_crawl_job_start_urls(job: CrawlJob) -> list[str]:
    urls = job.start_urls or [job.start_url]
    normalized: list[str] = []
    seen: set[str] = set()
    for url in urls:
        if not isinstance(url, str):
            continue
        stripped = url.strip()
        if not stripped or stripped in seen:
            continue
        seen.add(stripped)
        normalized.append(stripped)
    return normalized or [job.start_url]


async def _resolve_llm_profile(
    session: AsyncSession,
    job: CrawlJob,
) -> LLMProfile | None:
    if job.llm_profile_id is not None:
        return await session.get(LLMProfile, job.llm_profile_id)

    return await session.scalar(
        select(LLMProfile)
        .where(LLMProfile.is_default.is_(True))
        .order_by(LLMProfile.created_at.asc(), LLMProfile.id.asc())
        .limit(1),
    )


async def _append_agent_trace(
    session_factory: async_sessionmaker[AsyncSession],
    job_id: int,
    event: dict[str, object],
) -> None:
    async with session_factory() as session:
        job = await session.get(CrawlJob, job_id)
        if job is None:
            return

        normalized_event = normalize_agent_trace_event(event)
        if not normalized_event.get("created_at"):
            normalized_event["created_at"] = datetime.now(UTC).isoformat()

        trace = list(_normalize_trace(job.agent_trace))
        trace.append(normalized_event)
        job.agent_trace = trace[-MAX_AGENT_TRACE_EVENTS:]
        job.updated_at = datetime.now(UTC)
        await accumulate_crawl_job_run_tokens(session, job_id, normalized_event)
        await session.commit()


async def _accumulate_direct_llm_response_tokens(
    session_factory: async_sessionmaker[AsyncSession],
    job_id: int,
    response: object,
) -> None:
    usage = extract_token_usage_from_llm_response(response)
    if usage is None:
        return

    async with session_factory() as session:
        job = await session.get(CrawlJob, job_id)
        if job is None:
            return

        run = await get_or_create_current_crawl_job_run(session, job)
        run.input_tokens += usage["input_tokens"] or 0
        run.output_tokens += usage["output_tokens"] or 0
        run.total_tokens += usage["total_tokens"] or 0
        cached_tokens = usage.get("cached_tokens")
        if cached_tokens is not None:
            run.cached_tokens = (run.cached_tokens or 0) + cached_tokens
        run.updated_at = datetime.now(UTC)
        await session.commit()


async def _complete_running_job(
    session_factory: async_sessionmaker[AsyncSession],
    job_id: int,
) -> None:
    async with session_factory() as session:
        job = await session.get(CrawlJob, job_id)
        if job is None or job.status != CrawlJobStatus.RUNNING.value:
            return

        if await _crawl_job_has_pending_work_in_session(session, job_id=job_id):
            now = datetime.now(UTC)
            job.updated_at = now
            if job.current_run_id is not None:
                run = await session.get(CrawlJobRun, job.current_run_id)
                if run is not None and run.status == CrawlJobStatus.RUNNING.value:
                    run.updated_at = now
            await session.commit()
            return

        candidate_count = await session.scalar(
            select(func.count()).select_from(CrawlCandidate).where(CrawlCandidate.job_id == job_id)
        )
        if int(candidate_count or 0) > 0:
            job.status = CrawlJobStatus.NEEDS_REVIEW.value
            job.error_message = None
        else:
            job.status = CrawlJobStatus.FAILED.value
            derived = await _derive_job_failure_message(
                session,
                job_id,
                job.agent_trace,
            )
            job.error_message = adapt_failure_message_for_thinking_error(derived)
        now = datetime.now(UTC)
        job.updated_at = now
        await mark_crawl_job_run_finished(
            session,
            job,
            status=job.status,
            error_message=job.error_message,
            now=now,
        )
        await session.commit()


def _build_structured_retry_prompt(
    *,
    original_prompt: str,
    parse_error: str,
) -> str:
    return (
        f"{original_prompt}\n\n"
        "上一次回复未通过系统结构化校验，请立刻重试，并严格遵守以下要求：\n"
        "- 只输出一个合法 JSON 对象，不要输出 Markdown、解释或前后缀文本\n"
        "- 不要省略必填键\n"
        "- confidence 必须是 0 到 1 的数字\n"
        "- field_confidence 中每个值都必须是 0 到 1 的数字\n"
        "- evidence 必须保持简短，只保留必要摘要\n"
        f"- 上一次解析失败原因：{parse_error}"
    )


async def _invoke_direct_structured_llm(
    ctx: CrawlToolContext,
    *,
    llm_profile: LLMProfile,
    prompt: str,
    result_model: type[Any],
    empty_response_error: str,
) -> Any:
    model = build_faculty_crawler_model(
        llm_profile,
        extra_body=ctx.thinking_extra_body,
    )
    current_prompt = prompt
    last_error: Exception | None = None

    for attempt in range(DIRECT_LLM_STRUCTURED_MAX_ATTEMPTS):
        response = await model.ainvoke(current_prompt)
        await _accumulate_direct_llm_response_tokens(ctx.session_factory, ctx.job_id, response)
        content = _extract_model_message_content(response)
        if not content:
            last_error = ValueError(empty_response_error)
        else:
            try:
                return parse_structured_result(content, result_model)
            except LLMRuntimeError as exc:
                last_error = exc

        if attempt + 1 >= DIRECT_LLM_STRUCTURED_MAX_ATTEMPTS:
            break

        current_prompt = _build_structured_retry_prompt(
            original_prompt=prompt,
            parse_error=str(last_error),
        )

    if last_error is None:
        raise ValueError(empty_response_error)
    raise ValueError(f"{empty_response_error}: {last_error}")


async def _run_profile_crawl_job(
    session_factory: async_sessionmaker[AsyncSession],
    ctx: CrawlToolContext,
    *,
    llm_profile: LLMProfile,
    trace_callback: Any | None = None,
) -> None:
    async with session_factory() as session:
        await ensure_crawl_job_can_continue(session, ctx.job_id)

    await _emit_trace_event(
        trace_callback,
        {
            "event_type": "profile_entry",
            "message": "开始抓取单个导师详情页",
            "created_at": datetime.now(UTC).isoformat(),
            "raw": {"url": ctx.start_url},
        },
    )
    snapshot = await crawl_page_with_crawl4ai(ctx, ctx.start_url, intent="profile")
    if snapshot.status != "succeeded" or not snapshot.text.strip():
        raise ValueError(snapshot.error_message or "详情页抓取失败")

    async with session_factory() as session:
        await ensure_crawl_job_can_continue(session, ctx.job_id)

    candidate = await extract_profile_candidate_with_llm(ctx, llm_profile, snapshot.text)
    if not candidate.name.strip():
        raise ValueError(PROFILE_EXTRACTION_FAILED_ERROR)

    candidate = candidate.model_copy(
        update={
            "university": candidate.university or ctx.university,
            "school": candidate.school or ctx.school,
            "profile_url": candidate.profile_url or ctx.start_url,
            "source_url": candidate.source_url or ctx.start_url,
        },
    )
    saved = await save_candidates(ctx, [candidate])
    if not saved:
        raise ValueError(PROFILE_EXTRACTION_FAILED_ERROR)

    await _emit_trace_event(
        trace_callback,
        {
            "event_type": "profile_entry",
            "message": f"详情页导师候选提取成功：{saved[0].name}",
            "created_at": datetime.now(UTC).isoformat(),
            "raw": {"candidate_id": saved[0].id, "url": ctx.start_url},
        },
    )


async def _enrich_saved_candidates(
    session_factory: async_sessionmaker[AsyncSession],
    ctx: CrawlToolContext,
    *,
    llm_profile: LLMProfile,
    trace_callback: Any | None = None,
) -> int:
    return await _enrich_saved_candidates_concurrent(
        session_factory,
        ctx,
        llm_profile=llm_profile,
        trace_callback=trace_callback,
    )


async def enrich_selected_crawl_candidates(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    job_id: int,
    candidate_ids: list[int],
    llm_profile: LLMProfile,
    trace_callback: Any | None = None,
) -> SelectedCandidateEnrichmentSummary:
    async with session_factory() as session:
        job = await session.get(CrawlJob, job_id)
        if job is None:
            raise ValueError("未找到抓取任务")
        ctx = CrawlToolContext(
            job_id=job.id,
            start_url=job.start_url,
            university=job.university,
            school=job.school,
            session_factory=session_factory,
        )

    return await _enrich_selected_candidates_concurrent(
        session_factory,
        ctx,
        candidate_ids=candidate_ids,
        llm_profile=llm_profile,
        trace_callback=trace_callback,
    )


async def _enrich_saved_candidates_concurrent(
    session_factory: async_sessionmaker[AsyncSession],
    ctx: CrawlToolContext,
    *,
    llm_profile: LLMProfile,
    trace_callback: Any | None = None,
) -> int:
    async with session_factory() as session:
        candidates = list(
            (
                await session.execute(
                    select(CrawlCandidate)
                    .where(CrawlCandidate.job_id == ctx.job_id)
                    .order_by(CrawlCandidate.created_at.asc(), CrawlCandidate.id.asc())
                )
            ).scalars()
        )

    pending_candidates = [candidate for candidate in candidates if _needs_profile_enrichment(candidate)]
    if not pending_candidates:
        return 0

    enriched, _unchanged, _failed = await _enrich_candidate_collection_concurrent(
        session_factory,
        ctx,
        pending_candidates,
        llm_profile=llm_profile,
        trace_callback=trace_callback,
    )
    return enriched


async def _enrich_selected_candidates_concurrent(
    session_factory: async_sessionmaker[AsyncSession],
    ctx: CrawlToolContext,
    *,
    candidate_ids: list[int],
    llm_profile: LLMProfile,
    trace_callback: Any | None = None,
) -> SelectedCandidateEnrichmentSummary:
    unique_ids = list(dict.fromkeys(candidate_ids))
    if not unique_ids:
        return SelectedCandidateEnrichmentSummary(0, 0, 0, 0)

    async with session_factory() as session:
        candidates = list(
            (
                await session.execute(
                    select(CrawlCandidate)
                    .where(
                        CrawlCandidate.job_id == ctx.job_id,
                        CrawlCandidate.id.in_(unique_ids),
                    )
                    .order_by(CrawlCandidate.created_at.asc(), CrawlCandidate.id.asc())
                )
            ).scalars()
        )

    if not candidates:
        return SelectedCandidateEnrichmentSummary(
            0,
            0,
            0,
            0,
        )

    enriched, unchanged, failed = await _enrich_candidate_collection_concurrent(
        session_factory,
        ctx,
        candidates,
        llm_profile=llm_profile,
        trace_callback=trace_callback,
    )
    return SelectedCandidateEnrichmentSummary(
        selected_count=len(candidates),
        enriched_count=enriched,
        unchanged_count=unchanged,
        failed_count=failed,
    )


async def _enrich_candidate_collection_concurrent(
    session_factory: async_sessionmaker[AsyncSession],
    ctx: CrawlToolContext,
    pending_candidates: list[CrawlCandidate],
    *,
    llm_profile: LLMProfile,
    trace_callback: Any | None = None,
) -> tuple[int, int, int]:
    async with session_factory() as session:
        runtime_settings = await get_runtime_settings(session)

    settings = get_settings()
    concurrency = resolve_crawl_runtime_concurrency(runtime_settings)
    await _emit_trace_event(
        trace_callback,
        {
            "event_type": "enrichment",
            "message": f"开始统一补全候选导师详情，共 {len(pending_candidates)} 位待补全",
            "created_at": datetime.now(UTC).isoformat(),
            "raw": {"candidate_count": len(pending_candidates)},
        },
    )

    work_queue: asyncio.Queue[CandidateEnrichmentWorkItem | None] = asyncio.Queue()
    result_queue: asyncio.Queue[CandidateEnrichmentResult] = asyncio.Queue()
    host_limiters: dict[str, asyncio.Semaphore] = {}
    for candidate in pending_candidates:
        work_queue.put_nowait(
            CandidateEnrichmentWorkItem(
                candidate_id=candidate.id,
                candidate_name=candidate.name,
                profile_url=candidate.profile_url or "",
            )
        )

    worker_count = concurrency.profile_enrichment_concurrency
    for _ in range(worker_count):
        work_queue.put_nowait(None)

    workers = [
        asyncio.create_task(
            _run_candidate_enrichment_worker(
                session_factory,
                ctx,
                llm_profile,
                work_queue,
                result_queue,
                host_limiters,
                trace_callback=trace_callback,
                host_concurrency=concurrency.host_concurrency,
                max_retries=max(0, settings.crawler_profile_fetch_max_retries),
            )
        )
        for _ in range(worker_count)
    ]
    try:
        enriched, unchanged, failed = await _consume_candidate_enrichment_results(
            session_factory,
            ctx.job_id,
            result_queue,
            expected_count=len(pending_candidates),
            trace_callback=trace_callback,
        )
    finally:
        for worker in workers:
            worker.cancel()
        await asyncio.gather(*workers, return_exceptions=True)

    await _emit_trace_event(
        trace_callback,
        {
            "event_type": "enrichment",
            "message": f"候选导师详情补全完成：成功 {enriched} 位，未变化 {unchanged} 位，失败 {failed} 位",
            "created_at": datetime.now(UTC).isoformat(),
            "raw": {
                "candidate_count": len(pending_candidates),
                "enriched_count": enriched,
                "unchanged_count": unchanged,
                "failed_count": failed,
            },
        },
    )
    return enriched, unchanged, failed


async def _run_candidate_enrichment_worker(
    session_factory: async_sessionmaker[AsyncSession],
    ctx: CrawlToolContext,
    llm_profile: LLMProfile,
    work_queue: asyncio.Queue[CandidateEnrichmentWorkItem | None],
    result_queue: asyncio.Queue[CandidateEnrichmentResult],
    host_limiters: dict[str, asyncio.Semaphore],
    *,
    trace_callback: Any | None,
    host_concurrency: int,
    max_retries: int,
) -> None:
    while True:
        item = await work_queue.get()
        if item is None:
            return
        try:
            result = await _enrich_candidate_work_item(
                session_factory,
                ctx,
                llm_profile,
                item,
                host_limiters,
                trace_callback=trace_callback,
                host_concurrency=host_concurrency,
                max_retries=max_retries,
            )
        except asyncio.CancelledError:
            raise
        except (CrawlJobPaused, CrawlJobCanceled):
            result = CandidateEnrichmentResult(
                candidate_id=item.candidate_id,
                candidate_name=item.candidate_name,
                profile_url=item.profile_url,
                status="stopped",
            )
        except Exception as exc:
            result = CandidateEnrichmentResult(
                candidate_id=item.candidate_id,
                candidate_name=item.candidate_name,
                profile_url=item.profile_url,
                status="failed",
                error_message=str(exc),
            )
        await result_queue.put(result)


async def _enrich_candidate_work_item(
    session_factory: async_sessionmaker[AsyncSession],
    ctx: CrawlToolContext,
    llm_profile: LLMProfile,
    item: CandidateEnrichmentWorkItem,
    host_limiters: dict[str, asyncio.Semaphore],
    *,
    trace_callback: Any | None,
    host_concurrency: int,
    max_retries: int,
) -> CandidateEnrichmentResult:
    async with session_factory() as session:
        try:
            await ensure_crawl_job_can_continue(session, ctx.job_id)
        except (CrawlJobPaused, CrawlJobCanceled):
            return CandidateEnrichmentResult(
                candidate_id=item.candidate_id,
                candidate_name=item.candidate_name,
                profile_url=item.profile_url,
                status="stopped",
            )

    await _emit_trace_event(
        trace_callback,
        {
            "event_type": "enrichment",
            "message": f"开始补全候选导师详情：{item.candidate_name}",
            "created_at": datetime.now(UTC).isoformat(),
            "raw": {
                "candidate_id": item.candidate_id,
                "profile_url": item.profile_url,
            },
        },
    )

    if not item.profile_url.strip():
        return CandidateEnrichmentResult(
            candidate_id=item.candidate_id,
            candidate_name=item.candidate_name,
            profile_url=item.profile_url,
            status="failed",
            error_message="候选导师缺少详情页 URL，无法补全",
        )

    hostname = urlparse(item.profile_url).hostname or item.profile_url
    limiter = host_limiters.setdefault(hostname, asyncio.Semaphore(host_concurrency))
    host_limited = limiter.locked()

    async with limiter:
        snapshot, retry_count = await _crawl_candidate_profile_with_retries(
            ctx,
            item,
            max_retries=max_retries,
        )

    if snapshot.status != "succeeded" or not snapshot.text.strip():
        return CandidateEnrichmentResult(
            candidate_id=item.candidate_id,
            candidate_name=item.candidate_name,
            profile_url=item.profile_url,
            status="failed",
            error_message=snapshot.error_message,
            retry_count=retry_count,
            host_limited=host_limited,
        )

    candidate = CrawlCandidate(
        id=item.candidate_id,
        job_id=ctx.job_id,
        name=item.candidate_name,
        profile_url=item.profile_url,
    )
    enrichment = await enrich_candidate_profile_with_llm(
        ctx,
        llm_profile,
        candidate,
        snapshot.text,
    )

    changes = enrichment.model_dump()
    changed_fields = [field for field, value in changes.items() if value]
    return CandidateEnrichmentResult(
        candidate_id=item.candidate_id,
        candidate_name=item.candidate_name,
        profile_url=item.profile_url,
        status="succeeded",
        enrichment=enrichment,
        updated_fields=changed_fields,
        retry_count=retry_count,
        host_limited=host_limited,
    )


async def _crawl_candidate_profile_with_retries(
    ctx: CrawlToolContext,
    item: CandidateEnrichmentWorkItem,
    *,
    max_retries: int,
) -> tuple[PageSnapshot, int]:
    retry_count = 0
    for attempt in range(max_retries + 1):
        try:
            snapshot = await crawl_page_with_crawl4ai(
                ctx,
                item.profile_url,
                intent="profile",
            )
            return snapshot, retry_count
        except httpx.TimeoutException:
            if attempt >= max_retries:
                return (
                    PageSnapshot(
                        url=item.profile_url,
                        title=None,
                        text="",
                        html="",
                        links=[],
                        fetch_method="http",
                        status="failed",
                        error_message="详情页抓取超时",
                    ),
                    retry_count,
                )
            retry_count += 1
            await asyncio.sleep(0)

    return (
        PageSnapshot(
            url=item.profile_url,
            title=None,
            text="",
            html="",
            links=[],
            fetch_method="http",
            status="failed",
            error_message="详情页抓取失败",
        ),
        retry_count,
    )


async def _consume_candidate_enrichment_results(
    session_factory: async_sessionmaker[AsyncSession],
    job_id: int,
    result_queue: asyncio.Queue[CandidateEnrichmentResult],
    *,
    expected_count: int,
    trace_callback: Any | None,
) -> tuple[int, int, int]:
    enriched = 0
    unchanged = 0
    failed = 0
    retry_count = 0
    host_limited_count = 0
    stop_writes = False

    for _ in range(expected_count):
        result = await result_queue.get()
        retry_count += result.retry_count
        host_limited_count += 1 if result.host_limited else 0

        if result.status == "stopped":
            stop_writes = True
            continue

        if result.status == "failed":
            failed += 1
            await _emit_trace_event(
                trace_callback,
                {
                    "event_type": "enrichment",
                    "message": f"候选导师详情补全失败：{result.candidate_name}",
                    "created_at": datetime.now(UTC).isoformat(),
                    "raw": {
                        "candidate_id": result.candidate_id,
                        "profile_url": result.profile_url,
                        "status": result.status,
                        "error_message": result.error_message,
                    },
                },
            )
            continue

        if stop_writes or result.enrichment is None:
            continue

        try:
            changed = await _apply_candidate_enrichment(
                session_factory,
                result.candidate_id,
                result.enrichment,
            )
        except (CrawlJobPaused, CrawlJobCanceled):
            stop_writes = True
            continue

        if changed:
            enriched += 1
            await _emit_trace_event(
                trace_callback,
                {
                    "event_type": "enrichment",
                    "message": f"候选导师详情补全成功：{result.candidate_name}（{_format_enrichment_fields(result.updated_fields or [])}）",
                    "created_at": datetime.now(UTC).isoformat(),
                    "raw": {
                        "candidate_id": result.candidate_id,
                        "profile_url": result.profile_url,
                        "updated_fields": result.updated_fields or [],
                    },
                },
            )
        else:
            unchanged += 1
            await _emit_trace_event(
                trace_callback,
                {
                    "event_type": "enrichment",
                    "message": f"候选导师详情无新增信息：{result.candidate_name}",
                    "created_at": datetime.now(UTC).isoformat(),
                    "raw": {
                        "candidate_id": result.candidate_id,
                        "profile_url": result.profile_url,
                        "detected_fields": result.updated_fields or [],
                    },
                },
            )

    await _update_crawl_job_run_enrichment_metrics(
        session_factory,
        job_id,
        retry_count=retry_count,
        host_limited_count=host_limited_count,
        failed_candidate_count=failed,
        unchanged_candidate_count=unchanged,
    )
    return enriched, unchanged, failed


async def _mark_job_failed(
    session_factory: async_sessionmaker[AsyncSession],
    job_id: int,
    error_message: str,
) -> None:
    async with session_factory() as session:
        job = await session.get(CrawlJob, job_id)
        if job is None or job.status != CrawlJobStatus.RUNNING.value:
            return

        adapted_message = adapt_failure_message_for_thinking_error(error_message)
        job.status = CrawlJobStatus.FAILED.value
        job.error_message = adapted_message
        now = datetime.now(UTC)
        job.updated_at = now
        await mark_crawl_job_run_finished(
            session,
            job,
            status=CrawlJobStatus.FAILED.value,
            error_message=adapted_message,
            now=now,
        )
        await session.commit()


async def _mark_job_paused(
    session_factory: async_sessionmaker[AsyncSession],
    job_id: int,
) -> None:
    async with session_factory() as session:
        job = await session.get(CrawlJob, job_id)
        if job is None:
            return

        now = datetime.now(UTC)
        job.status = CrawlJobStatus.PAUSED.value
        job.updated_at = now
        await mark_crawl_job_run_paused(session, job, now=now)
        await session.commit()


async def _mark_job_canceled(
    session_factory: async_sessionmaker[AsyncSession],
    job_id: int,
) -> None:
    async with session_factory() as session:
        job = await session.get(CrawlJob, job_id)
        if job is None:
            return

        now = datetime.now(UTC)
        job.status = CrawlJobStatus.CANCELED.value
        job.updated_at = now
        await mark_crawl_job_run_finished(
            session,
            job,
            status=CrawlJobStatus.CANCELED.value,
            now=now,
        )
        await session.commit()


async def _derive_job_failure_message(
    session: AsyncSession,
    job_id: int,
    agent_trace: Any,
) -> str:
    trace_error = _derive_candidate_save_failure(agent_trace)
    if trace_error != NO_CANDIDATES_SAVED_ERROR:
        return trace_error

    page_error = await session.scalar(
        select(CrawlPage.error_message)
        .where(
            CrawlPage.job_id == job_id,
            CrawlPage.status == "failed",
            CrawlPage.error_message.is_not(None),
        )
        .order_by(CrawlPage.id.desc())
        .limit(1),
    )
    if isinstance(page_error, str) and page_error.strip():
        return page_error.strip()

    return NO_CANDIDATES_SAVED_ERROR


async def _apply_candidate_enrichment(
    session_factory: async_sessionmaker[AsyncSession],
    candidate_id: int,
    updates: CandidateEnrichmentPayload,
) -> bool:
    async with session_factory() as session:
        candidate = await session.get(CrawlCandidate, candidate_id)
        if candidate is None:
            return False

        update_payload = updates.model_dump()

        changed = False
        email = update_payload.get("email")
        if email and not candidate.email:
            candidate.email = email
            changed = True

        department = update_payload.get("department")
        if department and not candidate.department:
            candidate.department = department
            changed = True

        research_direction = update_payload.get("research_direction")
        if research_direction and not candidate.research_direction:
            candidate.research_direction = research_direction
            changed = True

        recent_papers = update_payload.get("recent_papers") or []
        if recent_papers and not (candidate.recent_papers or []):
            candidate.recent_papers = recent_papers
            changed = True

        if not changed:
            return False

        await ensure_crawl_job_can_continue(session, candidate.job_id)
        candidate.updated_at = datetime.now(UTC)
        await session.commit()
        return True


async def _update_crawl_job_run_enrichment_metrics(
    session_factory: async_sessionmaker[AsyncSession],
    job_id: int,
    *,
    retry_count: int,
    host_limited_count: int,
    failed_candidate_count: int,
    unchanged_candidate_count: int,
) -> None:
    if not any(
        (
            retry_count,
            host_limited_count,
            failed_candidate_count,
            unchanged_candidate_count,
        )
    ):
        return

    async with session_factory() as session:
        job = await session.get(CrawlJob, job_id)
        if job is None or job.current_run_id is None:
            return

        run = await get_or_create_current_crawl_job_run(session, job)
        run.retry_count += retry_count
        run.host_limited_count += host_limited_count
        run.failed_candidate_count += failed_candidate_count
        run.unchanged_candidate_count += unchanged_candidate_count
        run.updated_at = datetime.now(UTC)
        await session.commit()


async def _emit_trace_event(trace_callback: Any | None, event: dict[str, object]) -> None:
    if trace_callback is None:
        return
    result = trace_callback(event)
    if asyncio.iscoroutine(result):
        await result


def _normalize_trace(value: Any) -> list[dict[str, object]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _needs_profile_enrichment(candidate: CrawlCandidate) -> bool:
    if not candidate.profile_url:
        return False
    return any(
        (
            not (candidate.email or "").strip(),
            not (candidate.department or "").strip(),
            not (candidate.research_direction or "").strip(),
            not any(str(item).strip() for item in candidate.recent_papers or []),
        )
    )


def _format_enrichment_fields(fields: list[str]) -> str:
    labels = {
        "email": "邮箱",
        "department": "院系",
        "research_direction": "研究方向",
        "recent_papers": "近期论文",
    }
    if not fields:
        return "无新字段"
    return "、".join(labels.get(field, field) for field in fields)


async def enrich_candidate_profile_with_llm(
    ctx: CrawlToolContext,
    llm_profile: LLMProfile,
    candidate: CrawlCandidate,
    page_text: str,
) -> CandidateEnrichmentPayload:
    prompt = build_candidate_enrichment_prompt(candidate, page_text)
    return await _invoke_direct_structured_llm(
        ctx,
        llm_profile=llm_profile,
        prompt=prompt,
        result_model=CandidateEnrichmentPayload,
        empty_response_error="模型补全返回空响应",
    )


async def extract_profile_candidate_with_llm(
    ctx: CrawlToolContext,
    llm_profile: LLMProfile,
    page_text: str,
) -> ProfessorCandidatePayload:
    prompt = build_profile_candidate_prompt(
        university=ctx.university,
        school=ctx.school,
        profile_url=ctx.start_url,
        page_text=page_text,
    )
    candidate = await _invoke_direct_structured_llm(
        ctx,
        llm_profile=llm_profile,
        prompt=prompt,
        result_model=ProfessorCandidatePayload,
        empty_response_error=PROFILE_EXTRACTION_FAILED_ERROR,
    )
    if not candidate.name.strip():
        raise ValueError(PROFILE_EXTRACTION_FAILED_ERROR)
    return candidate




def _extract_model_message_content(response: object) -> str:
    if isinstance(response, str):
        return response.strip()

    if isinstance(response, dict):
        candidate = response.get("content")
        if candidate is None:
            return ""
        response = candidate

    content = getattr(response, "content", None)
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        pieces: list[str] = []
        for item in content:
            if isinstance(item, str):
                pieces.append(item)
                continue
            if not isinstance(item, dict):
                continue
            value = item.get("text") or item.get("content")
            if isinstance(value, str):
                pieces.append(value)
        joined = "".join(pieces).strip()
        return joined
    return ""


def build_faculty_crawler_model(*args: Any, **kwargs: Any) -> Any:
    factory = _get_build_faculty_crawler_model()
    return factory(*args, **kwargs)


async def run_faculty_crawler_agent(*args: Any, **kwargs: Any) -> Any:
    runner = _get_run_faculty_crawler_agent()
    return await runner(*args, **kwargs)


def _get_build_faculty_crawler_model():
    from app.agents.faculty_crawler_agent import build_faculty_crawler_model

    return build_faculty_crawler_model


def _get_run_faculty_crawler_agent():
    from app.agents.faculty_crawler_agent import run_faculty_crawler_agent

    return run_faculty_crawler_agent


def _derive_candidate_save_failure(agent_trace: Any) -> str:
    trace_events = _normalize_trace(agent_trace)
    for event in reversed(trace_events):
        haystack = _stringify_trace_payload(event)
        if "save_professor_candidates" not in haystack:
            continue
        if "invalid_tool_call" in haystack or "invalid_tool_calls" in haystack:
            if "finish_reason='length'" in haystack or '"finish_reason": "length"' in haystack:
                return TRUNCATED_SAVE_TOOL_CALL_ERROR
            return INVALID_SAVE_TOOL_CALL_ERROR
    return NO_CANDIDATES_SAVED_ERROR


def _stringify_trace_payload(event: dict[str, object]) -> str:
    parts: list[str] = []
    for key in ("message", "summary"):
        value = event.get(key)
        if isinstance(value, str):
            parts.append(value)
    raw = event.get("raw")
    if raw is not None:
        parts.append(str(raw))
    return "\n".join(parts)
