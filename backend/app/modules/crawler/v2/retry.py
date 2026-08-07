from __future__ import annotations

from datetime import datetime, timedelta
from typing import Protocol

from app.core.time import utc_now

MAX_CRAWLER_V2_ATTEMPTS = 4
MAX_CRAWLER_V2_CONNECTIVITY_ATTEMPTS = 12

_CONNECTIVITY_ERROR_MARKERS = (
    "all connection attempts failed",
    "connect error",
    "connection refused",
    "connection reset",
    "failed to establish a new connection",
    "getaddrinfo failed",
    "name or service not known",
    "network is unreachable",
    "nodename nor servname",
    "temporary failure in name resolution",
    "timed out",
    "timeout",
    "请求超时",
    "网络不可达",
    "模型请求超时",
)

_NON_CONNECTIVITY_ERROR_MARKERS = (
    "401",
    "403",
    "api key",
    "authentication",
    "authorization",
    "forbidden",
    "unauthorized",
    "鉴权",
    "密钥",
)

class RetryableCrawlerWorkItem(Protocol):
    attempt_count: int | None
    failure_count: int | None
    status: str
    last_error: str | None
    worker_id: str | None
    claimed_at: datetime | None
    lease_expires_at: datetime | None

def retry_backoff_seconds(failure_count: int | None) -> int:
    attempt = max(1, int(failure_count or 1))
    return min(60, 5 * (2 ** (attempt - 1)))


def max_attempts_for_crawler_error(message: object) -> int:
    normalized = str(message or "").strip().lower()
    if any(marker in normalized for marker in _NON_CONNECTIVITY_ERROR_MARKERS):
        return MAX_CRAWLER_V2_ATTEMPTS
    if any(marker in normalized for marker in _CONNECTIVITY_ERROR_MARKERS):
        return MAX_CRAWLER_V2_CONNECTIVITY_ATTEMPTS
    return MAX_CRAWLER_V2_ATTEMPTS

def mark_crawler_v2_failed(
    item: RetryableCrawlerWorkItem,
    *,
    message: str,
    retryable_status: str,
    terminal_status: str,
    max_attempts: int | None = None,
) -> None:
    item.last_error = message
    failure_count = int(getattr(item, "failure_count", 0) or 0) + 1
    item.failure_count = failure_count
    resolved_max_attempts = (
        max(1, int(max_attempts))
        if max_attempts is not None
        else max_attempts_for_crawler_error(message)
    )
    if failure_count >= resolved_max_attempts:
        item.status = terminal_status
        item.lease_expires_at = None
    else:
        item.status = retryable_status
        item.lease_expires_at = utc_now() + timedelta(
            seconds=retry_backoff_seconds(failure_count)
        )
    item.worker_id = None
    item.claimed_at = None
