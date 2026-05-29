from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models import CrawlJob, CrawlPage, CrawlPageFetchState, CrawlPageFetchStatus, CrawlPageTask, CrawlPageTaskStatus
from app.services.crawler_chunking import ChunkingConfig, build_page_chunks
from app.services.crawler_chunk_runtime import create_chunks_for_page
from app.services.crawler_tools import CrawlToolContext, PageSnapshot, crawl_page_with_crawl4ai
from app.services.crawler_v2_scheduler import ensure_job_active
from app.services.crawler_v2_url_utils import is_same_domain, normalize_url

MAX_PAGE_ATTEMPTS = 3


async def run_crawler_v2_page_worker_once(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    task_id: int,
    worker_id: str,
) -> int:
    async with session_factory() as session:
        task = await session.get(CrawlPageTask, task_id)
        if task is None or task.status != CrawlPageTaskStatus.PROCESSING.value or task.worker_id != worker_id:
            return 0
        job = await session.get(CrawlJob, task.job_id)
        if job is None or not await ensure_job_active(session, task.job_id):
            return 0
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
            if task is None:
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
        if snapshot.status == "succeeded":
            await _create_chunks_and_enqueue_links(session_factory, task_id=task_id, page_id=page_id, snapshot=snapshot)
        return 1
    except Exception as exc:
        async with session_factory() as session:
            task = await session.get(CrawlPageTask, task_id)
            if task is not None:
                _mark_page_failed(task, str(exc))
            await session.commit()
        return 1


async def fetch_page_direct(ctx: CrawlToolContext, url: str) -> PageSnapshot:
    return await crawl_page_with_crawl4ai(ctx, url)


async def fetch_page_browser(ctx: CrawlToolContext, url: str) -> PageSnapshot:
    return await crawl_page_with_crawl4ai(ctx, url)


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


def _mark_page_failed(task: CrawlPageTask, message: str) -> None:
    task.last_error = message
    if int(task.attempt_count or 0) >= MAX_PAGE_ATTEMPTS:
        task.status = CrawlPageTaskStatus.FAILED_TERMINAL.value
    else:
        task.status = CrawlPageTaskStatus.FAILED_RETRYABLE.value


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


async def _create_chunks_and_enqueue_links(
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
    await create_chunks_for_page(session_factory, job_id=task.job_id, page_id=page_id, drafts=drafts)
    async with session_factory() as session:
        task = await session.get(CrawlPageTask, task_id)
        job = await session.get(CrawlJob, task.job_id) if task is not None else None
        if task is None or job is None or not await ensure_job_active(session, task.job_id):
            return
        for link in snapshot.links:
            normalized = normalize_url(link, base_url=snapshot.url)
            if not is_same_domain(normalized, job.start_url):
                continue
            exists = await session.scalar(
                select(CrawlPageTask.id).where(
                    CrawlPageTask.job_id == task.job_id,
                    CrawlPageTask.normalized_url == normalized,
                )
            )
            if exists is not None:
                continue
            session.add(
                CrawlPageTask(
                    job_id=task.job_id,
                    normalized_url=normalized,
                    original_url=link,
                    depth=int(task.depth or 0) + 1,
                    priority=0,
                    status=CrawlPageTaskStatus.PENDING.value,
                )
            )
        await session.commit()
