from __future__ import annotations

from datetime import datetime, timedelta
from typing import Protocol

from app.core.time import utc_now

MAX_CRAWLER_V2_ATTEMPTS = 4

class RetryableCrawlerWorkItem(Protocol):
    attempt_count: int | None
    status: str
    last_error: str | None
    worker_id: str | None
    claimed_at: datetime | None
    lease_expires_at: datetime | None

def retry_backoff_seconds(attempt_count: int | None) -> int:
    attempt = max(1, int(attempt_count or 1))
    return min(60, 5 * (2 ** (attempt - 1)))

def mark_crawler_v2_failed(
    item: RetryableCrawlerWorkItem,
    *,
    message: str,
    retryable_status: str,
    terminal_status: str,
    max_attempts: int = MAX_CRAWLER_V2_ATTEMPTS,
) -> None:
    item.last_error = message
    if int(item.attempt_count or 0) >= max_attempts:
        item.status = terminal_status
        item.lease_expires_at = None
    else:
        item.status = retryable_status
        item.lease_expires_at = utc_now() + timedelta(seconds=retry_backoff_seconds(item.attempt_count))
    item.worker_id = None
    item.claimed_at = None