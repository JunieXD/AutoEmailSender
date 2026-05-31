from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models import CrawlJob, CrawlPage, CrawlPageFetchState, CrawlPageFetchStatus, CrawlPageTask, CrawlPageTaskStatus
from app.services.crawler_chunking import ChunkingConfig, build_page_chunks
from app.services.crawler_chunk_runtime import create_chunks_for_page
from app.services.crawler_tools import CrawlToolContext, PageSnapshot, browser_investigate, crawl_page_with_http
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
        if snapshot.status == "succeeded":
            await _create_chunks_and_enqueue_links(session_factory, task_id=task_id, page_id=page_id, snapshot=snapshot)
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
    now = datetime.now(lease_expires_at.tzinfo) if lease_expires_at.tzinfo else datetime.now()
    return lease_expires_at > now


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
        job_id = task.job_id
        start_url = job.start_url
        depth = int(task.depth or 0) + 1
    await _enqueue_page_links(session_factory, job_id=job_id, start_url=start_url, source_url=snapshot.url, links=snapshot.links, depth=depth)

async def _enqueue_page_links(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    job_id: int,
    start_url: str,
    source_url: str,
    links: list[str],
    depth: int,
) -> None:
    seen: set[str] = set()
    for link in links:
        normalized = normalize_url(link, base_url=source_url)
        if normalized in seen or not is_same_domain(normalized, start_url):
            continue
        seen.add(normalized)
        async with session_factory() as session:
            exists = await session.scalar(
                select(CrawlPageTask.id).where(
                    CrawlPageTask.job_id == job_id,
                    CrawlPageTask.normalized_url == normalized,
                )
            )
            if exists is not None:
                continue
            session.add(
                CrawlPageTask(
                    job_id=job_id,
                    normalized_url=normalized,
                    original_url=link,
                    depth=depth,
                    priority=0,
                    status=CrawlPageTaskStatus.PENDING.value,
                )
            )
            try:
                await session.commit()
            except IntegrityError:
                await session.rollback()
