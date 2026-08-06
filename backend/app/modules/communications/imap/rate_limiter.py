from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Callable

from app.core.config import get_settings
from app.models import IdentityProfile
from ..addresses import normalize_email_address


@dataclass(slots=True)
class _Bucket:
    tokens: float
    updated_at: float


class HistoryImapRateLimiter:
    def __init__(
        self,
        *,
        rate_per_minute: int,
        burst: int,
        monotonic: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.rate_per_minute = rate_per_minute
        self.burst = max(1, burst)
        self._monotonic = monotonic
        self._sleep = sleep
        self._lock = threading.Lock()
        self._buckets: dict[str, _Bucket] = {}

    def acquire(self, key: str) -> None:
        if self.rate_per_minute <= 0:
            return
        refill_per_second = self.rate_per_minute / 60
        while True:
            with self._lock:
                now = self._monotonic()
                bucket = self._buckets.get(key)
                if bucket is None:
                    bucket = _Bucket(tokens=float(self.burst), updated_at=now)
                    self._buckets[key] = bucket
                elapsed = max(0.0, now - bucket.updated_at)
                bucket.tokens = min(float(self.burst), bucket.tokens + elapsed * refill_per_second)
                bucket.updated_at = now
                if bucket.tokens >= 1:
                    bucket.tokens -= 1
                    return
                wait_seconds = (1 - bucket.tokens) / refill_per_second
            self._sleep(wait_seconds)


_history_rate_limiter_guard = threading.Lock()
_history_rate_limiter: HistoryImapRateLimiter | None = None
_history_rate_limiter_config: tuple[int, int] | None = None


def _identity_limiter_key(identity: IdentityProfile) -> str:
    normalized_username = normalize_email_address(identity.imap_username or "")
    if normalized_username:
        return normalized_username
    normalized_email = normalize_email_address(identity.email_address or "")
    return normalized_email or str(identity.id or id(identity))


def acquire_history_imap_command_slot_sync(
    identity: IdentityProfile,
    _command: str,
) -> None:
    settings = get_settings()
    limiter = _get_history_rate_limiter(
        settings.imap_history_command_rate_per_minute,
        settings.imap_history_command_burst,
    )
    limiter.acquire(_identity_limiter_key(identity))


def _get_history_rate_limiter(
    rate_per_minute: int,
    burst: int,
) -> HistoryImapRateLimiter:
    global _history_rate_limiter, _history_rate_limiter_config
    config = (rate_per_minute, burst)
    with _history_rate_limiter_guard:
        if _history_rate_limiter is None or _history_rate_limiter_config != config:
            _history_rate_limiter = HistoryImapRateLimiter(
                rate_per_minute=rate_per_minute,
                burst=burst,
            )
            _history_rate_limiter_config = config
        return _history_rate_limiter
