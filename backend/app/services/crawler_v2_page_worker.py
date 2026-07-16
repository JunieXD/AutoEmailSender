from __future__ import annotations

from datetime import datetime

from app.core.time import as_utc_aware, utc_now

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models import CrawlJob, CrawlPage, CrawlPageFetchState, CrawlPageFetchStatus, CrawlPageTask, CrawlPageTaskStatus, CrawlWorkerKind, LLMProfile
from app.services.crawler_chunking import ChunkingConfig, build_page_chunks
from app.services.crawler_chunk_runtime import create_chunks_for_page
from app.services.crawler_page_fetch_ledger import should_prefer_browser_for_fetch_domain
from app.services.crawler_debug import append_crawler_v2_debug_event
from app.services.crawler_tools import (
    CrawlToolContext,
    PageSnapshot,
    ProfessorCandidatePayload,
    browser_investigate,
    crawl_page_with_http,
    looks_like_client_encrypted_profile_fields,
    looks_like_unrendered_dynamic_teacher_directory,
    save_candidate_payloads_shared,
)
from app.services.crawler_v2_profile_extraction import invoke_v2_profile_extraction_agent
from app.services.crawler_v2_retry import mark_crawler_v2_failed
from app.services.crawler_v2_token_usage import record_crawler_v2_token_usage
from app.services.crawler_v2_scheduler import ensure_job_active
from app.services.thinking_adaptation import ensure_thinking_adaptation



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
        if await _skip_page_task_from_ledger(session, task):
            await session.commit()
            return 1
        target_url = task.original_url
        fetch_intent = "profile" if job.entry_type == "profile" else "generic"
        ctx = CrawlToolContext(
            session_factory=session_factory,
            job_id=job.id,
            university=job.university,
            school=job.school,
            start_url=job.start_url,
        )

    try:
        if await _prefer_browser_for_task_domain(session_factory, task.job_id, target_url):
            direct_status = "skipped_by_domain_browser_preference"
            fallback_reason = "same_domain_previously_required_browser"
            browser_snapshot = await fetch_page_browser(ctx, target_url, intent=fetch_intent)
            browser_status = browser_snapshot.status
            snapshot = browser_snapshot
            fetch_mode = "browser"
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
            if snapshot.status == "succeeded" and job.entry_type != "profile":
                task.status = CrawlPageTaskStatus.SUCCEEDED.value
            elif snapshot.status != "succeeded":
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
                chunk_result = await _create_chunks_for_page_snapshot(session_factory, task_id=task_id, page_id=page_id, snapshot=snapshot)
                append_crawler_v2_debug_event(
                    job.id,
                    worker_kind="page",
                    event_name="page_chunked",
                    work_item_id=task_id,
                    payload={"page_id": page_id, "target_url": target_url, "chunk_result": chunk_result},
                )
        return 1
    except Exception as exc:
        async with session_factory() as session:
            task = await session.get(CrawlPageTask, task_id)
            if task is not None and _page_task_owned_by_worker(task, worker_id):
                _mark_page_failed(task, str(exc))
            await session.commit()
        return 1


async def fetch_page_direct(ctx: CrawlToolContext, url: str) -> PageSnapshot:
    return await crawl_page_with_http(ctx, url)


async def fetch_page_browser(ctx: CrawlToolContext, url: str, *, intent: str = "generic") -> PageSnapshot:
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
            parent_url=None,
            fetch_method=snapshot.fetch_method,
            status=snapshot.status,
            title=snapshot.title,
            text_excerpt=(snapshot.text or "")[:2000],
            error_message=snapshot.error_message,
        )
        session.add(page)
        await session.flush()
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


async def _extract_profile_for_page_snapshot(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    task_id: int,
    worker_id: str,
    page_id: int | None,
    snapshot: PageSnapshot,
) -> None:
    async with session_factory() as session:
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
        thinking_extra_body = await ensure_thinking_adaptation(session, llm_profile)
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
        university=university,
        school=school,
        source_url=source_url,
        title=snapshot.title,
        page_text=snapshot.text,
        page_html_excerpt=snapshot.html,
        thinking_extra_body=thinking_extra_body,
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
    if job.llm_profile_id is not None:
        return await session.get(LLMProfile, job.llm_profile_id)
    return await session.scalar(
        select(LLMProfile)
        .where(LLMProfile.is_default.is_(True))
        .order_by(LLMProfile.id.asc())
        .limit(1)
    )


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
    )
    save_result = await save_candidate_payloads_shared(
        ctx,
        [ProfessorCandidatePayload.model_validate(candidate_data)],
    )
    async with session_factory() as session:
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
    page_id: int | None,
    snapshot: PageSnapshot,
) -> None:
    async with session_factory() as session:
        task = await session.get(CrawlPageTask, task_id)
        if task is None:
            return
        job = await session.get(CrawlJob, task.job_id)
        if job is None or not await ensure_job_active(session, task.job_id):
            return
        drafts = build_page_chunks(
            source_url=snapshot.url,
            html=snapshot.html,
            text=snapshot.text,
            config=ChunkingConfig(),
        )
        job_id = task.job_id
    await create_chunks_for_page(session_factory, job_id=job_id, page_id=page_id, drafts=drafts)


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
