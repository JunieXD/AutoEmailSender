from __future__ import annotations

import unittest

from app.services.crawler_v2_retry import MAX_CRAWLER_V2_ATTEMPTS, retry_backoff_seconds, mark_crawler_v2_failed

class FakeWorkItem:
    def __init__(self, attempt_count: int | None) -> None:
        self.attempt_count = attempt_count
        self.status = "processing"
        self.last_error = None
        self.worker_id = "w1"
        self.claimed_at = object()
        self.lease_expires_at = None

class CrawlerV2RetryTests(unittest.TestCase):
    def test_backoff_uses_exponential_seconds_capped_at_sixty(self) -> None:
        self.assertEqual(retry_backoff_seconds(1), 5)
        self.assertEqual(retry_backoff_seconds(2), 10)
        self.assertEqual(retry_backoff_seconds(3), 20)
        self.assertEqual(retry_backoff_seconds(4), 40)
        self.assertEqual(retry_backoff_seconds(5), 60)

    def test_retryable_failure_sets_next_claim_time_and_clears_worker(self) -> None:
        item = FakeWorkItem(attempt_count=2)

        mark_crawler_v2_failed(
            item,
            message="rate limited",
            retryable_status="failed_retryable",
            terminal_status="failed_terminal",
        )

        self.assertEqual(item.status, "failed_retryable")
        self.assertEqual(item.last_error, "rate limited")
        self.assertIsNone(item.worker_id)
        self.assertIsNone(item.claimed_at)
        self.assertIsNotNone(item.lease_expires_at)

    def test_fourth_failed_attempt_becomes_terminal_without_backoff(self) -> None:
        item = FakeWorkItem(attempt_count=MAX_CRAWLER_V2_ATTEMPTS)

        mark_crawler_v2_failed(
            item,
            message="still rate limited",
            retryable_status="failed_retryable",
            terminal_status="failed_terminal",
        )

        self.assertEqual(item.status, "failed_terminal")
        self.assertIsNone(item.lease_expires_at)
        self.assertIsNone(item.worker_id)

if __name__ == "__main__":
    unittest.main()