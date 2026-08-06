from __future__ import annotations

import time
import unittest

from app.modules.communications.imap.rate_limiter import HistoryImapRateLimiter


class HistoryImapRateLimiterTest(unittest.TestCase):
    def test_acquire_smooths_commands_after_burst_is_exhausted(self) -> None:
        now = 1000.0
        sleeps: list[float] = []

        def monotonic() -> float:
            return now

        def sleep(seconds: float) -> None:
            nonlocal now
            sleeps.append(seconds)
            now += seconds

        limiter = HistoryImapRateLimiter(
            rate_per_minute=30,
            burst=2,
            monotonic=monotonic,
            sleep=sleep,
        )

        limiter.acquire("account@example.com")
        limiter.acquire("account@example.com")
        limiter.acquire("account@example.com")
        limiter.acquire("account@example.com")

        self.assertEqual(sleeps, [2.0, 2.0])

    def test_accounts_have_independent_buckets(self) -> None:
        now = 1000.0
        sleeps: list[float] = []

        def monotonic() -> float:
            return now

        def sleep(seconds: float) -> None:
            nonlocal now
            sleeps.append(seconds)
            now += seconds

        limiter = HistoryImapRateLimiter(
            rate_per_minute=60,
            burst=1,
            monotonic=monotonic,
            sleep=sleep,
        )

        limiter.acquire("first@example.com")
        limiter.acquire("second@example.com")

        self.assertEqual(sleeps, [])

    def test_non_positive_rate_disables_waiting(self) -> None:
        limiter = HistoryImapRateLimiter(
            rate_per_minute=0,
            burst=1,
            monotonic=time.monotonic,
            sleep=lambda _seconds: (_ for _ in ()).throw(AssertionError("should not sleep")),
        )

        limiter.acquire("account@example.com")
        limiter.acquire("account@example.com")
