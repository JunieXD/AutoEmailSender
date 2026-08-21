from __future__ import annotations

import re
from typing import Protocol
from dataclasses import dataclass

from app.core.time import utc_now

from urllib.parse import urlsplit, urlunsplit

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.crawl_job import CrawlPageFetchState, CrawlPageFetchStatus
from ..v2.url_utils import is_spa_route_fragment

# Keep the ledger budget aligned with the crawler's existing connectivity retry
# budget so it cannot prematurely turn a network outage into a terminal skip.
TRANSIENT_FETCH_RETRY_LIMIT = 12

_TERMINAL_FAILURE_MARKERS = (
    "anti-bot",
    "blocked",
    "captcha",
    "cloudflare",
    "access denied",
    "security check",
)
_POLICY_FAILURE_MARKERS = (
    "url 不在",
    "最终 url",
    "不允许指向",
    "内网",
    "本机",
    "不可解析",
    "已拒绝",
    "无关页面",
    "同域范围",
    "重定向次数过多",
    "缺少 location",
)
_TRANSIENT_FAILURE_MARKERS = (
    "err_",
    "connection",
    "connection closed",
    "connection aborted",
    "protocol",
    "fetch failed",
    "timed out",
    "timeout",
    "temporarily",
    "详情页未提供可见正文",
    "returned empty html",
    "empty response",
    "playwright browser fetch failed",
    "temporary server response",
    "http 5",
    "temporary dns",
    "name resolution",
    "暂时无法解析",
)
_TRANSIENT_HTTP_STATUS_PATTERN = re.compile(r"\b(?:408|425|429|5\d{2})\b")


class PageSnapshotLike(Protocol):
    url: str
    fetch_method: str
    status: str
    error_message: str | None
    suspicious_empty: bool
    http_status_code: int | None
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


def classify_page_fetch_failure(
    snapshot: PageSnapshotLike,
) -> FetchFailureClassification:
    if snapshot.status != "failed":
        raise ValueError("Only failed snapshots can be classified")
    error_message = (snapshot.error_message or "").lower()
    if any(marker in error_message for marker in _POLICY_FAILURE_MARKERS):
        return FetchFailureClassification(
            status=CrawlPageFetchStatus.TERMINAL_FAILED.value,
            reason="policy_rejected",
        )
    if any(marker in error_message for marker in _TERMINAL_FAILURE_MARKERS):
        return FetchFailureClassification(
            status=CrawlPageFetchStatus.TERMINAL_FAILED.value,
            reason="anti_bot_or_empty_response",
        )
    if _is_transient_http_status(getattr(snapshot, "http_status_code", None)):
        return FetchFailureClassification(
            status=CrawlPageFetchStatus.TRANSIENT_FAILED.value
        )
    if any(marker in error_message for marker in _TRANSIENT_FAILURE_MARKERS):
        return FetchFailureClassification(
            status=CrawlPageFetchStatus.TRANSIENT_FAILED.value
        )
    if snapshot.suspicious_empty:
        return FetchFailureClassification(
            status=CrawlPageFetchStatus.TERMINAL_FAILED.value,
            reason="anti_bot_or_empty_response",
        )
    return FetchFailureClassification(
        status=CrawlPageFetchStatus.TRANSIENT_FAILED.value
    )


def _is_retryable_failure_message(message: str | None) -> bool:
    normalized = (message or "").lower()
    return bool(normalized) and (
        any(marker in normalized for marker in _TRANSIENT_FAILURE_MARKERS)
        or bool(_TRANSIENT_HTTP_STATUS_PATTERN.search(normalized))
    )


def _is_transient_http_status(status_code: int | None) -> bool:
    return bool(
        status_code is not None
        and (status_code in {408, 425, 429} or 500 <= status_code <= 599)
    )


async def get_page_fetch_decision(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    job_id: int,
    url: str,
) -> PageFetchDecision:
    normalized_url = normalize_fetch_url(url)
    if not callable(session_factory):
        return PageFetchDecision(
            action="allow_first_fetch", normalized_url=normalized_url
        )
    async with session_factory() as session:
        state = await session.scalar(
            select(CrawlPageFetchState).where(
                CrawlPageFetchState.job_id == job_id,
                CrawlPageFetchState.normalized_url == normalized_url,
            )
        )
        if state is None or not isinstance(state, CrawlPageFetchState):
            return PageFetchDecision(
                action="allow_first_fetch", normalized_url=normalized_url
            )
        if state.status == CrawlPageFetchStatus.TERMINAL_FAILED.value:
            if (
                _is_retryable_failure_message(state.last_error_message)
                and int(state.transient_failure_count or 0)
                < TRANSIENT_FETCH_RETRY_LIMIT
            ):
                state.status = CrawlPageFetchStatus.TRANSIENT_FAILED.value
                state.terminal_reason = None
                state.updated_at = utc_now()
                await session.commit()
                return PageFetchDecision(
                    action="allow_retry",
                    normalized_url=normalized_url,
                    state_id=state.id,
                    status=state.status,
                )
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
            return PageFetchDecision(
                action="claim_chunk",
                normalized_url=normalized_url,
                state_id=state.id,
                status=state.status,
            )
        if state.status == CrawlPageFetchStatus.PROCESSED.value:
            return PageFetchDecision(
                action="skip_processed",
                normalized_url=normalized_url,
                state_id=state.id,
                status=state.status,
            )
        if state.status == CrawlPageFetchStatus.SUCCEEDED.value:
            return PageFetchDecision(
                action="reuse_success",
                normalized_url=normalized_url,
                state_id=state.id,
                status=state.status,
            )
        return PageFetchDecision(
            action="allow_retry",
            normalized_url=normalized_url,
            state_id=state.id,
            status=state.status,
        )


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
                    CrawlPageFetchState.direct_status.in_(("failed", "succeeded")),
                    CrawlPageFetchState.fallback_reason.is_not(None),
                )
            )
        )
    return any(
        fetch_url_host(state.normalized_url or state.original_url) == target_host
        for state in states
    )


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
                state.transient_failure_count = (
                    int(state.transient_failure_count or 0) + 1
                )
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
