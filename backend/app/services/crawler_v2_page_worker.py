from __future__ import annotations

from datetime import datetime

from app.core.time import as_utc_aware, utc_now

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models import CrawlJob, CrawlPage, CrawlPageFetchState, CrawlPageFetchStatus, CrawlPageTask, CrawlPageTaskStatus
from app.services.crawler_chunking import ChunkingConfig, build_page_chunks
from app.services.crawler_chunk_runtime import create_chunks_for_page
from app.services.crawler_debug import append_crawler_v2_debug_event
from app.services.crawler_tools import CrawlToolContext, PageSnapshot, browser_investigate, crawl_page_with_http
from app.services.crawler_v2_retry import mark_crawler_v2_failed
from app.services.crawler_v2_scheduler import ensure_job_active



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
        ctx = CrawlToolContext(
            session_factory=session_factory,
            job_id=job.id,
            university=job.university,
            school=job.school,
            start_url=job.start_url,
        )

    try:
        direct_snapshot = await fetch_page_direct(ctx, target_url)
        snapshot = direct_snapshot
        fetch_mode = "direct"
        direct_status = direct_snapshot.status
        fallback_reason = None
        browser_status = None
        if _should_use_browser_fallback(direct_snapshot):
            fallback_reason = _fallback_reason(direct_snapshot)
            browser_snapshot = await fetch_page_browser(ctx, target_url)
            browser_status = browser_snapshot.status
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
            if snapshot.status == "succeeded":
                task.status = CrawlPageTaskStatus.SUCCEEDED.value
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


async def fetch_page_browser(ctx: CrawlToolContext, url: str) -> PageSnapshot:
    return await browser_investigate(ctx, url, goal="", intent="generic")


def _should_use_browser_fallback(snapshot: PageSnapshot) -> bool:
    if snapshot.status != "succeeded":
        return True
    text = (snapshot.text or "").strip()
    html = (snapshot.html or "").strip()
    if snapshot.suspicious_empty:
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
