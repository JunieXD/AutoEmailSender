from __future__ import annotations

from datetime import datetime

from app.core.time import as_utc_aware, utc_now

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models import CrawlJob, CrawlPage, CrawlPageFetchState, CrawlPageFetchStatus, CrawlPageTask, CrawlPageTaskStatus, CrawlWorkerKind, LLMProfile
from ..pages.chunking import ChunkingConfig, build_page_chunks
from ..jobs.llm_context import resolve_crawl_job_runtime_profile
from ..pages.chunk_runtime import create_chunks_for_page
from ..pages.fetch_ledger import should_prefer_browser_for_fetch_domain
from ..pages.debug import append_crawler_v2_debug_event
from ..pages.tools import (
    CrawlToolContext,
    PageSnapshot,
    ProfessorCandidatePayload,
    browser_investigate,
    crawl_page_with_http,
    expand_browser_pagination,
    looks_like_client_encrypted_profile_fields,
    looks_like_unrendered_dynamic_teacher_directory,
    save_candidate_payloads_shared,
)
from .profile_extraction import invoke_v2_profile_extraction_agent
from .routing import (
    ENTRY_DISCOVERY_REASON,
    ENTRY_EXPANSION_MODE,
    NO_EXPANSION_MODE,
    PAGINATION_DISCOVERY_REASON,
    PAGINATION_EXPANSION_MODE,
    V2PageRoutingResult,
    invoke_v2_page_routing_agent,
)
from .retry import mark_crawler_v2_failed
from .token_usage import record_crawler_v2_token_usage
from .scheduler import ZERO_CANDIDATE_BROWSER_RETRY_REASON, ensure_job_active
from .lease import CrawlerV2ClaimFence, fence_crawler_v2_claim
from .models import CrawlerV2WorkKind
from .url_utils import has_spa_route_fragment
from app.modules.llm.public import ensure_llm_runtime_adaptation, format_llm_runtime_error_for_user


MAX_PAGE_TASKS_PER_JOB = 5000

async def run_crawler_v2_page_worker_once(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    task_id: int,
    worker_id: str,
) -> int:
    async with session_factory() as session:
        task = await session.get(CrawlPageTask, task_id)
        if task is None or not _page_task_owned_by_worker(task, worker_id):
            return 0
        job = await session.get(CrawlJob, task.job_id)
        if job is None or not await ensure_job_active(session, task.job_id):
            return 0
        force_browser = (
            task.fallback_reason == ZERO_CANDIDATE_BROWSER_RETRY_REASON
            and task.browser_status is None
        )
        if not force_browser and await _skip_page_task_from_ledger(session, task):
            await session.commit()
            return 1
        target_url = task.original_url
        fetch_intent = "profile" if job.entry_type == "profile" else "directory"
        routing_profile = None
        if job.entry_type != "profile":
            routing_profile = await _resolve_llm_profile(session, job)
            if routing_profile is None:
                _mark_page_failed(task, "缺少可用的 LLM Profile")
                await session.commit()
                return 1
        ctx = CrawlToolContext(
            session_factory=session_factory,
            job_id=job.id,
            university=job.university,
            school=job.school,
            start_url=job.start_url,
        )

    try:
        if force_browser:
            direct_status = "skipped_for_zero_candidate_retry"
            fallback_reason = ZERO_CANDIDATE_BROWSER_RETRY_REASON
            browser_snapshot = await fetch_page_browser(
                ctx,
                target_url,
                intent=fetch_intent,
                force_fetch=True,
            )
            browser_status = browser_snapshot.status
            snapshot = browser_snapshot
            fetch_mode = "browser"
        elif has_spa_route_fragment(target_url):
            direct_status = "skipped_for_spa_route"
            fallback_reason = "spa_route_requires_browser"
            browser_snapshot = await fetch_page_browser(ctx, target_url, intent=fetch_intent)
            browser_status = browser_snapshot.status
            snapshot = browser_snapshot
            fetch_mode = "browser"
        elif await _prefer_browser_for_task_domain(session_factory, task.job_id, target_url):
            direct_status = "skipped_by_domain_browser_preference"
            fallback_reason = "same_domain_previously_required_browser"
            browser_snapshot = await fetch_page_browser(ctx, target_url, intent=fetch_intent)
            browser_status = browser_snapshot.status
            snapshot = browser_snapshot
            fetch_mode = "browser"
        elif fetch_intent == "directory":
            direct_status = "skipped_for_directory_browser_preference"
            fallback_reason = "directory_prefers_rendered_browser"
            browser_snapshot = await fetch_page_browser(ctx, target_url, intent=fetch_intent)
            browser_status = browser_snapshot.status
            snapshot = browser_snapshot
            fetch_mode = "browser"
            if browser_snapshot.status != "succeeded":
                direct_snapshot = await fetch_page_direct(ctx, target_url)
                direct_status = direct_snapshot.status
                if direct_snapshot.status == "succeeded":
                    snapshot = direct_snapshot
                    fetch_mode = "direct"
        else:
            direct_snapshot = await fetch_page_direct(ctx, target_url)
            snapshot = direct_snapshot
            fetch_mode = "direct"
            direct_status = direct_snapshot.status
            fallback_reason = None
            browser_status = None
            if _should_use_browser_fallback(direct_snapshot):
                fallback_reason = _fallback_reason(direct_snapshot)
                browser_snapshot = await fetch_page_browser(ctx, target_url, intent=fetch_intent)
                browser_status = browser_snapshot.status
                if browser_snapshot.status == "succeeded" or direct_snapshot.status != "succeeded":
                    snapshot = browser_snapshot
                    fetch_mode = "browser"
        async with session_factory() as session:
            if not await fence_crawler_v2_claim(
                session,
                CrawlerV2ClaimFence(
                    kind=CrawlerV2WorkKind.PAGE,
                    work_item_id=task_id,
                    worker_id=worker_id,
                ),
            ):
                await session.rollback()
                return 0
            if not await ensure_job_active(session, task.job_id):
                return 0
            task = await session.get(CrawlPageTask, task_id)
            if task is None or not _page_task_owned_by_worker(task, worker_id):
                return 0
            page_id = await _record_page_and_state(
                session,
                task=task,
                snapshot=snapshot,
                fetch_mode=fetch_mode,
                direct_status=direct_status,
                fallback_reason=fallback_reason,
                browser_status=browser_status,
            )
            if snapshot.status != "succeeded":
                if force_browser:
                    task.status = CrawlPageTaskStatus.FAILED_TERMINAL.value
                    task.last_error = snapshot.error_message or "浏览器兜底抓取失败"
                    task.worker_id = None
                    task.claimed_at = None
                    task.lease_expires_at = None
                else:
                    _mark_page_failed(task, snapshot.error_message or "页面抓取失败")
            await session.commit()
        append_crawler_v2_debug_event(
            job.id,
            worker_kind="page",
            event_name="page_fetched",
            work_item_id=task_id,
            payload={
                "target_url": target_url,
                "fetch_mode": fetch_mode,
                "direct_status": direct_status,
                "fallback_reason": fallback_reason,
                "browser_status": browser_status,
                "snapshot": _snapshot_debug_payload(snapshot),
            },
        )
        if snapshot.status == "succeeded":
            if job.entry_type == "profile":
                await _extract_profile_for_page_snapshot(
                    session_factory,
                    task_id=task_id,
                    worker_id=worker_id,
                    page_id=page_id,
                    snapshot=snapshot,
                )
            else:
                assert routing_profile is not None
                routing_failure: str | None = None
                try:
                    async with session_factory() as adaptation_session:
                        routing_adaptation = await ensure_llm_runtime_adaptation(
                            adaptation_session,
                            routing_profile,
                        )
                        await adaptation_session.commit()
                    routing_result = await invoke_v2_page_routing_agent(
                        routing_profile,
                        session_factory=session_factory,
                        university=job.university,
                        school=job.school,
                        start_url=job.start_url,
                        source_url=snapshot.url,
                        title=snapshot.title,
                        page_text=snapshot.text,
                        page_html=snapshot.html,
                        expansion_mode=task.expansion_mode or ENTRY_EXPANSION_MODE,
                        adaptation=routing_adaptation,
                    )
                except Exception as routing_exc:
                    routing_failure = format_llm_runtime_error_for_user(routing_exc)
                    append_crawler_v2_debug_event(
                        job.id,
                        worker_kind="page",
                        event_name="page_routing_failed",
                        work_item_id=task_id,
                        payload={
                            "source_url": snapshot.url,
                            "error": routing_failure,
                        },
                    )
                    routing_result = V2PageRoutingResult(
                        discovered_urls=[],
                        entry_discovery_reasons={},
                        allow_expansion=False,
                        pagination_urls=[],
                        usage=None,
                        attempts=[],
                    )
                if not await _page_task_can_commit(
                    session_factory,
                    task_id=task_id,
                    worker_id=worker_id,
                ):
                    return 0
                if routing_result.usage is not None:
                    await record_crawler_v2_token_usage(
                        session_factory,
                        job_id=job.id,
                        worker_kind=CrawlWorkerKind.PAGE,
                        work_item_id=task_id,
                        model_name=routing_profile.model_name,
                        input_tokens=routing_result.usage.get("input_tokens") or 0,
                        output_tokens=routing_result.usage.get("output_tokens") or 0,
                        cached_tokens=routing_result.usage.get("cached_tokens") or 0,
                        raw_usage=dict(routing_result.usage),
                        claim=CrawlerV2ClaimFence(
                            kind=CrawlerV2WorkKind.PAGE,
                            work_item_id=task_id,
                            worker_id=worker_id,
                        ),
                    )
                for attempt in routing_result.attempts:
                    append_crawler_v2_debug_event(
                        job.id,
                        worker_kind="page",
                        event_name="page_routing_llm_response",
                        work_item_id=task_id,
                        payload={
                            "source_url": snapshot.url,
                            "phase": attempt.phase,
                            "attempt_number": attempt.attempt_number,
                            "raw_model_text": attempt.raw_model_text,
                            "raw_payload": attempt.raw_payload,
                            "error": attempt.error,
                            "token_usage": dict(attempt.usage) if attempt.usage is not None else None,
                        },
                    )
                interactive_snapshots: tuple[PageSnapshot, ...] = ()
                interactive_stopped_reason: str | None = None
                if routing_failure is None and routing_result.pagination_control is not None:
                    control = routing_result.pagination_control
                    try:
                        interactive_result = await expand_browser_pagination(
                            ctx,
                            snapshot.url,
                            tag=control.tag,
                            text=control.text,
                            title=control.title,
                            aria_label=control.aria_label,
                            class_tokens=control.class_tokens,
                            match_index=control.match_index,
                            intent=fetch_intent,
                        )
                    except Exception as interactive_exc:
                        routing_failure = format_llm_runtime_error_for_user(interactive_exc)
                    else:
                        interactive_snapshots = interactive_result.snapshots
                        interactive_stopped_reason = interactive_result.stopped_reason
                        if interactive_result.status != "succeeded":
                            routing_failure = (
                                interactive_result.error_message
                                or "交互式分页执行失败"
                            )
                    append_crawler_v2_debug_event(
                        job.id,
                        worker_kind="page",
                        event_name="browser_pagination_expanded",
                        work_item_id=task_id,
                        payload={
                            "source_url": snapshot.url,
                            "control_id": control.control_id,
                            "additional_page_count": len(interactive_snapshots),
                            "stopped_reason": interactive_stopped_reason,
                            "error": routing_failure,
                        },
                    )
                if not await _page_task_can_commit(
                    session_factory,
                    task_id=task_id,
                    worker_id=worker_id,
                ):
                    return 0
                chunk_result = await _create_chunks_for_page_snapshot(
                    session_factory,
                    task_id=task_id,
                    worker_id=worker_id,
                    page_id=page_id,
                    snapshot=snapshot,
                )
                for interactive_snapshot in interactive_snapshots:
                    chunk_result += await _create_chunks_for_page_snapshot(
                        session_factory,
                        task_id=task_id,
                        worker_id=worker_id,
                        page_id=page_id,
                        snapshot=interactive_snapshot,
                    )
                if routing_failure is None:
                    expansion_result = await _complete_list_page_routing(
                        session_factory,
                        task_id=task_id,
                        worker_id=worker_id,
                        source_url=snapshot.url,
                        routing_result=routing_result,
                    )
                else:
                    expansion_result = await _mark_list_page_routing_failed(
                        session_factory,
                        task_id=task_id,
                        worker_id=worker_id,
                        error_message=routing_failure,
                    )
                append_crawler_v2_debug_event(
                    job.id,
                    worker_kind="page",
                    event_name="page_chunked",
                    work_item_id=task_id,
                    payload={
                        "page_id": page_id,
                        "target_url": target_url,
                        "chunk_result": chunk_result,
                        "interactive_page_count": len(interactive_snapshots),
                        "interactive_stopped_reason": interactive_stopped_reason,
                        "expansion_result": expansion_result,
                    },
                )
        return 1
    except Exception as exc:
        async with session_factory() as session:
            task = await session.get(CrawlPageTask, task_id)
            if task is not None and _page_task_owned_by_worker(task, worker_id):
                _mark_page_failed(task, format_llm_runtime_error_for_user(exc))
            await session.commit()
        return 1


async def fetch_page_direct(ctx: CrawlToolContext, url: str) -> PageSnapshot:
    return await crawl_page_with_http(ctx, url)


async def fetch_page_browser(
    ctx: CrawlToolContext,
    url: str,
    *,
    intent: str = "generic",
    force_fetch: bool = False,
) -> PageSnapshot:
    if force_fetch:
        return await browser_investigate(
            ctx,
            url,
            goal="",
            intent=intent,
            force_fetch=True,
        )
    return await browser_investigate(ctx, url, goal="", intent=intent)


async def _prefer_browser_for_task_domain(
    session_factory: async_sessionmaker[AsyncSession],
    job_id: int,
    target_url: str,
) -> bool:
    return await should_prefer_browser_for_fetch_domain(
        session_factory,
        job_id=job_id,
        url=target_url,
    )


def _should_use_browser_fallback(snapshot: PageSnapshot) -> bool:
    if snapshot.status != "succeeded":
        return True
    text = (snapshot.text or "").strip()
    html = (snapshot.html or "").strip()
    if snapshot.suspicious_empty:
        return True
    if looks_like_unrendered_dynamic_teacher_directory(snapshot):
        return True
    if looks_like_client_encrypted_profile_fields(snapshot):
        return True
    if not text and len(html) < 80:
        return True
    lowered = f"{text}\n{html}".lower()
    return any(marker in lowered for marker in ("captcha", "403 forbidden", "access denied", "enable javascript"))


def _fallback_reason(snapshot: PageSnapshot) -> str:
    if snapshot.status != "succeeded":
        return snapshot.error_message or "direct_fetch_failed"
    return "direct_fetch_unusable"


def _lease_valid(lease_expires_at: datetime | None) -> bool:
    if lease_expires_at is None:
        return True
    return as_utc_aware(lease_expires_at) > utc_now()


def _page_task_owned_by_worker(task: CrawlPageTask, worker_id: str) -> bool:
    return task.status == CrawlPageTaskStatus.PROCESSING.value and task.worker_id == worker_id and _lease_valid(task.lease_expires_at)


async def _skip_page_task_from_ledger(session: AsyncSession, task: CrawlPageTask) -> bool:
    state = await session.scalar(
        select(CrawlPageFetchState).where(
            CrawlPageFetchState.job_id == task.job_id,
            CrawlPageFetchState.normalized_url == task.normalized_url,
        )
    )
    if state is None:
        return False
    if state.status == CrawlPageFetchStatus.PROCESSED.value:
        if task.expansion_mode != NO_EXPANSION_MODE and task.allow_expansion is None:
            return False
        task.status = CrawlPageTaskStatus.SKIPPED_DUPLICATE.value
        task.last_error = "页面已处理完成，跳过重复抓取"
        task.worker_id = None
        task.claimed_at = None
        task.lease_expires_at = None
        return True
    if state.status == CrawlPageFetchStatus.TERMINAL_FAILED.value:
        task.status = CrawlPageTaskStatus.FAILED_TERMINAL.value
        task.last_error = state.last_error_message or "页面此前已终止失败，跳过重复抓取"
        task.worker_id = None
        task.claimed_at = None
        task.lease_expires_at = None
        return True
    return False

def _mark_page_failed(task: CrawlPageTask, message: str) -> None:
    mark_crawler_v2_failed(
        task,
        message=message,
        retryable_status=CrawlPageTaskStatus.FAILED_RETRYABLE.value,
        terminal_status=CrawlPageTaskStatus.FAILED_TERMINAL.value,
    )


async def _record_page_and_state(
    session: AsyncSession,
    *,
    task: CrawlPageTask,
    snapshot: PageSnapshot,
    fetch_mode: str,
    direct_status: str | None,
    fallback_reason: str | None,
    browser_status: str | None,
) -> int | None:
    task.fetch_mode = fetch_mode
    task.direct_status = direct_status
    task.fallback_reason = fallback_reason
    task.browser_status = browser_status
    page = None
    if snapshot.page_id is not None:
        page = await session.get(CrawlPage, snapshot.page_id)
        if page is not None and page.job_id != task.job_id:
            page = None
    if page is None:
        page = CrawlPage(
            job_id=task.job_id,
            url=snapshot.url,
            parent_url=task.parent_url,
            fetch_method=snapshot.fetch_method,
            page_type=task.expansion_mode or "unknown",
            status=snapshot.status,
            title=snapshot.title,
            text_excerpt=(snapshot.text or "")[:2000],
            error_message=snapshot.error_message,
        )
        session.add(page)
        await session.flush()
    else:
        page.parent_url = task.parent_url
        page.page_type = task.expansion_mode or page.page_type
    state = await session.scalar(
        select(CrawlPageFetchState).where(
            CrawlPageFetchState.job_id == task.job_id,
            CrawlPageFetchState.normalized_url == task.normalized_url,
        )
    )
    if state is None:
        state = CrawlPageFetchState(
            job_id=task.job_id,
            normalized_url=task.normalized_url,
            original_url=task.original_url,
            status=CrawlPageFetchStatus.SUCCEEDED.value if snapshot.status == "succeeded" else CrawlPageFetchStatus.TRANSIENT_FAILED.value,
        )
        session.add(state)
    state.status = CrawlPageFetchStatus.SUCCEEDED.value if snapshot.status == "succeeded" else CrawlPageFetchStatus.TRANSIENT_FAILED.value
    state.last_fetch_method = snapshot.fetch_method
    state.fetch_mode = fetch_mode
    state.direct_status = direct_status
    state.fallback_reason = fallback_reason
    state.browser_status = browser_status
    state.last_page_id = page.id
    state.last_error_message = snapshot.error_message
    return page.id


async def _complete_list_page_routing(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    task_id: int,
    worker_id: str,
    source_url: str,
    routing_result: V2PageRoutingResult,
) -> dict[str, int | bool | str]:
    async with session_factory() as session:
        if not await fence_crawler_v2_claim(
            session,
            CrawlerV2ClaimFence(
                kind=CrawlerV2WorkKind.PAGE,
                work_item_id=task_id,
                worker_id=worker_id,
            ),
        ):
            await session.rollback()
            return {"status": "not_claimed", "queued_count": 0}
        task = await session.get(CrawlPageTask, task_id)
        if task is None or not _page_task_owned_by_worker(task, worker_id):
            return {"status": "not_claimed", "queued_count": 0}
        if not await ensure_job_active(session, task.job_id):
            return {"status": "inactive", "queued_count": 0}

        proposals: list[tuple[str, str]] = []
        proposals.extend(
            (url, routing_result.entry_discovery_reasons.get(url, ENTRY_DISCOVERY_REASON))
            for url in routing_result.discovered_urls
        )
        proposals.extend(
            (url, PAGINATION_DISCOVERY_REASON)
            for url in routing_result.pagination_urls
        )

        task_count = int(
            await session.scalar(
                select(func.count()).select_from(CrawlPageTask).where(
                    CrawlPageTask.job_id == task.job_id,
                )
            )
            or 0
        )
        queued_count = 0
        seen_urls: set[str] = set()
        limit_reached = False
        for normalized_url, discovery_reason in proposals:
            if normalized_url in seen_urls:
                continue
            seen_urls.add(normalized_url)
            if task_count >= MAX_PAGE_TASKS_PER_JOB:
                limit_reached = True
                break
            exists = await session.scalar(
                select(CrawlPageTask.id).where(
                    CrawlPageTask.job_id == task.job_id,
                    CrawlPageTask.normalized_url == normalized_url,
                )
            )
            if exists is not None:
                continue
            try:
                async with session.begin_nested():
                    session.add(
                        CrawlPageTask(
                            job_id=task.job_id,
                            normalized_url=normalized_url,
                            original_url=normalized_url,
                            parent_url=source_url,
                            discovery_reason=discovery_reason,
                            expansion_mode=PAGINATION_EXPANSION_MODE,
                            allow_expansion=None,
                            depth=task.depth + 1,
                            priority=task.priority,
                            status=CrawlPageTaskStatus.PENDING.value,
                        )
                    )
                    await session.flush()
            except IntegrityError:
                continue
            queued_count += 1
            task_count += 1

        effective_allow_expansion = bool(
            routing_result.discovered_urls
            or routing_result.pagination_urls
            or routing_result.pagination_control
        )
        task.allow_expansion = effective_allow_expansion
        task.status = CrawlPageTaskStatus.SUCCEEDED.value
        task.worker_id = None
        task.claimed_at = None
        task.lease_expires_at = None
        await session.commit()
        result: dict[str, int | bool | str] = {
            "status": "completed",
            "queued_count": queued_count,
            "entry_url_count": len(routing_result.discovered_urls),
            "pagination_url_count": len(routing_result.pagination_urls),
            "allow_expansion": effective_allow_expansion,
            "task_limit_reached": limit_reached,
        }
        if routing_result.pagination_control is not None:
            result["pagination_control_id"] = (
                routing_result.pagination_control.control_id
            )
        return result


async def _mark_list_page_routing_failed(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    task_id: int,
    worker_id: str,
    error_message: str,
) -> dict[str, int | str]:
    async with session_factory() as session:
        if not await fence_crawler_v2_claim(
            session,
            CrawlerV2ClaimFence(
                kind=CrawlerV2WorkKind.PAGE,
                work_item_id=task_id,
                worker_id=worker_id,
            ),
        ):
            await session.rollback()
            return {"status": "not_claimed", "queued_count": 0}
        task = await session.get(CrawlPageTask, task_id)
        if task is None or not _page_task_owned_by_worker(task, worker_id):
            return {"status": "not_claimed", "queued_count": 0}
        if not await ensure_job_active(session, task.job_id):
            return {"status": "inactive", "queued_count": 0}
        task.allow_expansion = None
        _mark_page_failed(task, f"页面扩展决策失败：{error_message}")
        status = task.status
        await session.commit()
        return {"status": status, "queued_count": 0}


async def _extract_profile_for_page_snapshot(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    task_id: int,
    worker_id: str,
    page_id: int | None,
    snapshot: PageSnapshot,
) -> None:
    async with session_factory() as session:
        if not await fence_crawler_v2_claim(
            session,
            CrawlerV2ClaimFence(
                kind=CrawlerV2WorkKind.PAGE,
                work_item_id=task_id,
                worker_id=worker_id,
            ),
        ):
            await session.rollback()
            return
        task = await session.get(CrawlPageTask, task_id)
        if task is None or not _page_task_owned_by_worker(task, worker_id):
            return
        if not await ensure_job_active(session, task.job_id):
            return
        job = await session.get(CrawlJob, task.job_id)
        if job is None:
            return
        llm_profile = await _resolve_llm_profile(session, job)
        if llm_profile is None:
            _mark_page_failed(task, "缺少可用的 LLM Profile")
            await session.commit()
            return
        adaptation = await ensure_llm_runtime_adaptation(session, llm_profile)
        await session.commit()
        job_id = job.id
        university = job.university
        school = job.school
        start_url = job.start_url
        model_name = llm_profile.model_name
        original_url = task.original_url

    source_url = snapshot.url or original_url
    append_crawler_v2_debug_event(
        job_id,
        worker_kind="page",
        event_name="profile_extract_requested",
        work_item_id=task_id,
        payload={"source_url": source_url, "page_id": page_id, "title": snapshot.title, "page_text_length": len(snapshot.text or "")},
    )
    result = await invoke_v2_profile_extraction_agent(
        llm_profile,
        session_factory=session_factory,
        university=university,
        school=school,
        source_url=source_url,
        title=snapshot.title,
        page_text=snapshot.text,
        page_html_excerpt=snapshot.html,
        adaptation=adaptation,
    )
    for attempt in result.attempts:
        append_crawler_v2_debug_event(
            job_id,
            worker_kind="page",
            event_name="profile_extract_llm_response",
            work_item_id=task_id,
            payload={
                "source_url": source_url,
                "attempt_number": attempt.attempt_number,
                "raw_model_text": attempt.raw_model_text,
                "raw_payload": attempt.raw_payload,
                "error": attempt.error,
                "token_usage": dict(attempt.usage) if attempt.usage is not None else None,
                "page_text_hash": result.page_text_hash,
                "page_text_length": result.page_text_length,
            },
        )
    if not await _page_task_can_commit(session_factory, task_id=task_id, worker_id=worker_id):
        append_crawler_v2_debug_event(
            job_id,
            worker_kind="page",
            event_name="profile_extract_skipped_inactive",
            work_item_id=task_id,
            payload={"source_url": source_url},
        )
        return
    if result.usage is not None:
        await record_crawler_v2_token_usage(
            session_factory,
            job_id=job_id,
            worker_kind=CrawlWorkerKind.PAGE,
            work_item_id=task_id,
            model_name=model_name,
            input_tokens=result.usage.get("input_tokens") or 0,
            output_tokens=result.usage.get("output_tokens") or 0,
            cached_tokens=result.usage.get("cached_tokens") or 0,
            raw_usage=dict(result.usage),
            claim=CrawlerV2ClaimFence(
                kind=CrawlerV2WorkKind.PAGE,
                work_item_id=task_id,
                worker_id=worker_id,
            ),
        )
    await _complete_profile_page_extraction(
        session_factory,
        task_id=task_id,
        worker_id=worker_id,
        source_url=source_url,
        payload=result.payload,
        university=university,
        school=school,
        start_url=start_url,
    )


async def _page_task_can_commit(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    task_id: int,
    worker_id: str,
) -> bool:
    async with session_factory() as session:
        task = await session.get(CrawlPageTask, task_id)
        if task is None or not _page_task_owned_by_worker(task, worker_id):
            return False
        return await ensure_job_active(session, task.job_id)


async def _resolve_llm_profile(session: AsyncSession, job: CrawlJob) -> LLMProfile | None:
    return await resolve_crawl_job_runtime_profile(session, job)  # type: ignore[return-value]


async def _complete_profile_page_extraction(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    task_id: int,
    worker_id: str,
    source_url: str,
    payload: dict[str, object],
    university: str,
    school: str,
    start_url: str,
) -> None:
    async with session_factory() as session:
        if not await fence_crawler_v2_claim(
            session,
            CrawlerV2ClaimFence(
                kind=CrawlerV2WorkKind.PAGE,
                work_item_id=task_id,
                worker_id=worker_id,
            ),
        ):
            await session.rollback()
            return
        task = await session.get(CrawlPageTask, task_id)
        if task is None or not _page_task_owned_by_worker(task, worker_id):
            return
        if not await ensure_job_active(session, task.job_id):
            return
        candidate_payload = payload.get("candidate") if isinstance(payload, dict) else None
        status = str(payload.get("status") or "") if isinstance(payload, dict) else ""
        if status != "candidate" or not isinstance(candidate_payload, dict) or not str(candidate_payload.get("name") or "").strip():
            task.status = CrawlPageTaskStatus.FAILED_TERMINAL.value
            task.last_error = "详情页未识别到导师候选"
            task.worker_id = None
            task.claimed_at = None
            task.lease_expires_at = None
            await session.commit()
            append_crawler_v2_debug_event(
                task.job_id,
                worker_kind="page",
                event_name="profile_extract_no_candidate",
                work_item_id=task_id,
                payload={"source_url": source_url, "raw_payload": payload},
            )
            return
        candidate_data = dict(candidate_payload)
        candidate_data["university"] = candidate_data.get("university") or university
        candidate_data["school"] = candidate_data.get("school") or school
        candidate_data["profile_url"] = source_url
        candidate_data["source_url"] = source_url
        candidate_data["source_kind"] = "profile_page"
        candidate_data["boundary_risk"] = bool(candidate_data.get("boundary_risk") or False)
        job_id = task.job_id

    ctx = CrawlToolContext(
        job_id=job_id,
        start_url=start_url,
        university=university,
        school=school,
        session_factory=session_factory,
        entry_type="profile",
        claim_fence=CrawlerV2ClaimFence(
            kind=CrawlerV2WorkKind.PAGE,
            work_item_id=task_id,
            worker_id=worker_id,
        ),
    )
    save_result = await save_candidate_payloads_shared(
        ctx,
        [ProfessorCandidatePayload.model_validate(candidate_data)],
    )
    async with session_factory() as session:
        if not await fence_crawler_v2_claim(
            session,
            CrawlerV2ClaimFence(
                kind=CrawlerV2WorkKind.PAGE,
                work_item_id=task_id,
                worker_id=worker_id,
            ),
        ):
            await session.rollback()
            return
        task = await session.get(CrawlPageTask, task_id)
        if task is None or not _page_task_owned_by_worker(task, worker_id):
            return
        if not await ensure_job_active(session, task.job_id):
            return
        task.status = CrawlPageTaskStatus.SUCCEEDED.value
        task.worker_id = None
        task.claimed_at = None
        task.lease_expires_at = None
        await session.commit()
    append_crawler_v2_debug_event(
        job_id,
        worker_kind="page",
        event_name="profile_extract_completed",
        work_item_id=task_id,
        payload={"source_url": source_url, "raw_payload": payload, "save_result": {key: value for key, value in save_result.items() if key != "saved"}},
    )

async def _create_chunks_for_page_snapshot(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    task_id: int,
    worker_id: str,
    page_id: int | None,
    snapshot: PageSnapshot,
) -> int:
    async with session_factory() as session:
        task = await session.get(CrawlPageTask, task_id)
        if task is None:
            return 0
        job = await session.get(CrawlJob, task.job_id)
        if job is None or not await ensure_job_active(session, task.job_id):
            return 0
        drafts = build_page_chunks(
            source_url=snapshot.url,
            html=snapshot.html,
            text=snapshot.text,
            config=ChunkingConfig(),
        )
        job_id = task.job_id
    return await create_chunks_for_page(
        session_factory,
        job_id=job_id,
        page_id=page_id,
        drafts=drafts,
        claim_fence=CrawlerV2ClaimFence(
            kind=CrawlerV2WorkKind.PAGE,
            work_item_id=task_id,
            worker_id=worker_id,
        ),
    )


def _snapshot_debug_payload(snapshot: PageSnapshot) -> dict[str, object]:
    return {
        "url": snapshot.url,
        "status": snapshot.status,
        "title": getattr(snapshot, "title", None),
        "fetch_method": snapshot.fetch_method,
        "error_message": snapshot.error_message,
        "suspicious_empty": snapshot.suspicious_empty,
        "text": snapshot.text or "",
        "html": snapshot.html or "",
        "markdown": getattr(snapshot, "markdown", "") or "",
        "links_count": len(snapshot.links or []),
    }
