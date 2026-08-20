from __future__ import annotations

import unittest

from app.modules.crawler.v2.retry import (
    MAX_CRAWLER_V2_ATTEMPTS,
    MAX_CRAWLER_V2_CONNECTIVITY_ATTEMPTS,
    mark_crawler_v2_failed,
    max_attempts_for_crawler_error,
    retry_backoff_seconds,
)

class FakeWorkItem:
    def __init__(
        self,
        attempt_count: int | None,
        *,
        failure_count: int | None = 0,
    ) -> None:
        self.attempt_count = attempt_count
        self.failure_count = failure_count
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
        self.assertEqual(item.failure_count, 1)

    def test_fourth_failed_attempt_becomes_terminal_without_backoff(self) -> None:
        item = FakeWorkItem(
            attempt_count=MAX_CRAWLER_V2_ATTEMPTS,
            failure_count=MAX_CRAWLER_V2_ATTEMPTS - 1,
        )

        mark_crawler_v2_failed(
            item,
            message="still rate limited",
            retryable_status="failed_retryable",
            terminal_status="failed_terminal",
        )

        self.assertEqual(item.status, "failed_terminal")
        self.assertIsNone(item.lease_expires_at)
        self.assertIsNone(item.worker_id)

    def test_reclaims_do_not_consume_failure_budget(self) -> None:
        item = FakeWorkItem(attempt_count=20, failure_count=0)

        mark_crawler_v2_failed(
            item,
            message="rate limited",
            retryable_status="failed_retryable",
            terminal_status="failed_terminal",
        )

        self.assertEqual(item.status, "failed_retryable")
        self.assertEqual(item.failure_count, 1)

    def test_connectivity_errors_receive_extended_failure_budget(self) -> None:
        self.assertEqual(
            max_attempts_for_crawler_error("temporary failure in name resolution"),
            MAX_CRAWLER_V2_CONNECTIVITY_ATTEMPTS,
        )
        self.assertEqual(
            max_attempts_for_crawler_error(
                "Playwright browser fetch failed: net::ERR_CONNECTION_CLOSED"
            ),
            MAX_CRAWLER_V2_CONNECTIVITY_ATTEMPTS,
        )
        self.assertEqual(
            max_attempts_for_crawler_error(
                "Playwright browser fetch returned temporary HTTP 502"
            ),
            MAX_CRAWLER_V2_CONNECTIVITY_ATTEMPTS,
        )
        self.assertEqual(
            max_attempts_for_crawler_error("页面地址暂时无法解析，稍后将自动重试"),
            MAX_CRAWLER_V2_CONNECTIVITY_ATTEMPTS,
        )
        item = FakeWorkItem(
            attempt_count=30,
            failure_count=MAX_CRAWLER_V2_CONNECTIVITY_ATTEMPTS - 2,
        )

        mark_crawler_v2_failed(
            item,
            message="network is unreachable",
            retryable_status="failed_retryable",
            terminal_status="failed_terminal",
        )

        self.assertEqual(item.status, "failed_retryable")
        self.assertEqual(
            item.failure_count,
            MAX_CRAWLER_V2_CONNECTIVITY_ATTEMPTS - 1,
        )

    def test_generic_model_error_does_not_receive_connectivity_budget(self) -> None:
        self.assertEqual(
            max_attempts_for_crawler_error("模型请求失败：API key 无效"),
            MAX_CRAWLER_V2_ATTEMPTS,
        )

    def test_authentication_error_wins_over_timeout_text_in_trace(self) -> None:
        self.assertEqual(
            max_attempts_for_crawler_error(
                "HTTP 401 Authorization failed; trace=upstream-timeout"
            ),
            MAX_CRAWLER_V2_ATTEMPTS,
        )

if __name__ == "__main__":
    unittest.main()
