from __future__ import annotations

from typing import Protocol
from dataclasses import dataclass
from datetime import UTC, datetime

from app.core.time import utc_now

from urllib.parse import urlsplit, urlunsplit

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.crawl_job import CrawlPageFetchState, CrawlPageFetchStatus
from app.services.crawler_v2_url_utils import is_spa_route_fragment

TRANSIENT_FETCH_RETRY_LIMIT = 2

_TERMINAL_FAILURE_MARKERS = (
    "anti-bot",
    "blocked",
    "captcha",
    "cloudflare",
    "access denied",
    "security check",
)


class PageSnapshotLike(Protocol):
    url: str
    fetch_method: str
    status: str
    error_message: str | None
    suspicious_empty: bool
    page_id: int | None


@dataclass(frozen=True, slots=True)
class FetchFailureClassification:
    status: str
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class PageFetchDecision:
    action: str
    normalized_url: str
    state_id: int | None = None
    status: str | None = None
    message: str | None = None
    terminal_reason: str | None = None


def normalize_fetch_url(url: str) -> str:
    parsed = urlsplit(url.strip())
    scheme = parsed.scheme.lower()
    netloc = parsed.netloc.lower()
    path = parsed.path or "/"
    fragment = parsed.fragment if is_spa_route_fragment(parsed.fragment) else ""
    return urlunsplit((scheme, netloc, path, parsed.query, fragment))


def fetch_url_host(url: str | None) -> str | None:
    if not url:
        return None
    try:
        return (urlsplit(url.strip()).hostname or "").lower() or None
    except ValueError:
        return None


def classify_page_fetch_failure(snapshot: PageSnapshotLike) -> FetchFailureClassification:
    if snapshot.status != "failed":
        raise ValueError("Only failed snapshots can be classified")
    error_message = (snapshot.error_message or "").lower()
    if snapshot.suspicious_empty or any(marker in error_message for marker in _TERMINAL_FAILURE_MARKERS):
        return FetchFailureClassification(
            status=CrawlPageFetchStatus.TERMINAL_FAILED.value,
            reason="anti_bot_or_empty_response",
        )
    return FetchFailureClassification(status=CrawlPageFetchStatus.TRANSIENT_FAILED.value)


async def get_page_fetch_decision(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    job_id: int,
    url: str,
) -> PageFetchDecision:
    normalized_url = normalize_fetch_url(url)
    if not callable(session_factory):
        return PageFetchDecision(action="allow_first_fetch", normalized_url=normalized_url)
    async with session_factory() as session:
        state = await session.scalar(
            select(CrawlPageFetchState).where(
                CrawlPageFetchState.job_id == job_id,
                CrawlPageFetchState.normalized_url == normalized_url,
            )
        )
        if state is None or not isinstance(state, CrawlPageFetchState):
            return PageFetchDecision(action="allow_first_fetch", normalized_url=normalized_url)
        if state.status == CrawlPageFetchStatus.TERMINAL_FAILED.value:
            return PageFetchDecision(
                action="skip_terminal_failed",
                normalized_url=normalized_url,
                state_id=state.id,
                status=state.status,
                message=state.last_error_message,
                terminal_reason=state.terminal_reason,
            )
        if state.status == CrawlPageFetchStatus.TRANSIENT_FAILED.value:
            if state.transient_failure_count >= TRANSIENT_FETCH_RETRY_LIMIT:
                state.status = CrawlPageFetchStatus.TERMINAL_FAILED.value
                state.terminal_reason = "transient_retry_exhausted"
                state.updated_at = utc_now()
                await session.commit()
                return PageFetchDecision(
                    action="skip_terminal_failed",
                    normalized_url=normalized_url,
                    state_id=state.id,
                    status=state.status,
                    message=state.last_error_message,
                    terminal_reason=state.terminal_reason,
                )
            return PageFetchDecision(
                action="allow_retry",
                normalized_url=normalized_url,
                state_id=state.id,
                status=state.status,
            )
        if state.status == CrawlPageFetchStatus.CHUNKED.value:
            return PageFetchDecision(action="claim_chunk", normalized_url=normalized_url, state_id=state.id, status=state.status)
        if state.status == CrawlPageFetchStatus.PROCESSED.value:
            return PageFetchDecision(action="skip_processed", normalized_url=normalized_url, state_id=state.id, status=state.status)
        if state.status == CrawlPageFetchStatus.SUCCEEDED.value:
            return PageFetchDecision(action="reuse_success", normalized_url=normalized_url, state_id=state.id, status=state.status)
        return PageFetchDecision(action="allow_retry", normalized_url=normalized_url, state_id=state.id, status=state.status)


async def should_prefer_browser_for_fetch_domain(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    job_id: int,
    url: str,
) -> bool:
    target_host = fetch_url_host(url)
    if not target_host or not callable(session_factory):
        return False
    async with session_factory() as session:
        states = list(
            await session.scalars(
                select(CrawlPageFetchState).where(
                    CrawlPageFetchState.job_id == job_id,
                    CrawlPageFetchState.fetch_mode == "browser",
                    CrawlPageFetchState.browser_status == "succeeded",
                    CrawlPageFetchState.fallback_reason.is_not(None),
                )
            )
        )
    return any(fetch_url_host(state.normalized_url or state.original_url) == target_host for state in states)


async def mark_page_fetch_result(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    job_id: int,
    original_url: str,
    snapshot: PageSnapshotLike,
    generated_chunks: bool = False,
    fetch_mode: str | None = None,
    direct_status: str | None = None,
    fallback_reason: str | None = None,
    browser_status: str | None = None,
) -> None:
    if not callable(session_factory):
        return
    normalized_url = normalize_fetch_url(snapshot.url or original_url)
    now = utc_now()
    async with session_factory() as session:
        state = await session.scalar(
            select(CrawlPageFetchState).where(
                CrawlPageFetchState.job_id == job_id,
                CrawlPageFetchState.normalized_url == normalized_url,
            )
        )
        if state is not None and not isinstance(state, CrawlPageFetchState):
            return
        if state is None:
            state = CrawlPageFetchState(
                job_id=job_id,
                normalized_url=normalized_url,
                original_url=original_url,
                status=CrawlPageFetchStatus.SUCCEEDED.value,
            )
            session.add(state)
        state.original_url = original_url
        state.last_fetch_method = snapshot.fetch_method
        state.last_page_id = snapshot.page_id
        state.last_attempted_at = now
        state.updated_at = now
        state.last_error_message = snapshot.error_message
        state.fetch_mode = fetch_mode
        state.direct_status = direct_status
        state.fallback_reason = fallback_reason
        state.browser_status = browser_status
        if snapshot.status == "succeeded":
            state.status = (
                CrawlPageFetchStatus.CHUNKED.value
                if generated_chunks
                else CrawlPageFetchStatus.SUCCEEDED.value
            )
            state.terminal_reason = None
            state.transient_failure_count = 0
        else:
            classification = classify_page_fetch_failure(snapshot)
            state.status = classification.status
            state.terminal_reason = classification.reason
            if classification.status == CrawlPageFetchStatus.TRANSIENT_FAILED.value:
                state.transient_failure_count += 1
        await session.commit()


async def mark_page_chunks_processed(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    job_id: int,
    source_url: str,
) -> None:
    if not callable(session_factory):
        return
    normalized_url = normalize_fetch_url(source_url)
    async with session_factory() as session:
        state = await session.scalar(
            select(CrawlPageFetchState).where(
                CrawlPageFetchState.job_id == job_id,
                CrawlPageFetchState.normalized_url == normalized_url,
            )
        )
        if state is not None:
            state.status = CrawlPageFetchStatus.PROCESSED.value
            state.updated_at = utc_now()
            await session.commit()
