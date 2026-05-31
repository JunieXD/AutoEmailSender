from __future__ import annotations

import unittest
from datetime import UTC, datetime

from app.models import CrawlJobRun
from app.services.crawl_job_runs import _settle_active_segment, extract_token_usage, extract_token_usage_from_llm_response



class CrawlJobRunDurationTests(unittest.TestCase):
    def test_settle_active_segment_treats_naive_sqlite_timestamp_as_utc(self) -> None:
        run = CrawlJobRun(
            job_id=1,
            attempt_number=1,
            status="running",
            active_started_at=datetime(2026, 5, 31, 6, 0, 0),
            active_seconds=30,
        )

        _settle_active_segment(run, now=datetime(2026, 5, 31, 6, 10, 0, tzinfo=UTC))

        self.assertEqual(run.active_seconds, 630)
        self.assertIsNone(run.active_started_at)
class _FakeLLMResponse:
    def __init__(self) -> None:
        self.response_metadata = {
            "token_usage": {
                "prompt_tokens": 11,
                "completion_tokens": 7,
                "total_tokens": 18,
            },
        }


class CrawlJobRunTokenUsageTests(unittest.TestCase):
    def test_extract_token_usage_from_llm_response_metadata(self) -> None:
        usage = extract_token_usage_from_llm_response(_FakeLLMResponse())

        self.assertEqual(
            usage,
            {
                "input_tokens": 11,
                "output_tokens": 7,
                "total_tokens": 18,
                "cached_tokens": None,
            },
        )

    def test_extracts_cached_tokens_from_usage_metadata(self) -> None:
        usage = extract_token_usage(
            {
                "raw": {
                    "message": (
                        "usage_metadata={'input_tokens': 100, 'output_tokens': 20, "
                        "'total_tokens': 120, 'input_token_details': {'cache_read': 64}}"
                    )
                }
            }
        )

        self.assertIsNotNone(usage)
        self.assertEqual(usage["input_tokens"], 100)
        self.assertEqual(usage["output_tokens"], 20)
        self.assertEqual(usage["total_tokens"], 120)
        self.assertEqual(usage["cached_tokens"], 64)

    def test_extracts_cached_tokens_from_response_metadata(self) -> None:
        usage = extract_token_usage(
            {
                "raw": {
                    "message": (
                        "response_metadata={'token_usage': {'completion_tokens': 12, "
                        "'prompt_tokens': 34, 'total_tokens': 46, "
                        "'prompt_tokens_details': {'cached_tokens': 16}}}"
                    )
                }
            }
        )

        self.assertIsNotNone(usage)
        self.assertEqual(usage["input_tokens"], 34)
        self.assertEqual(usage["output_tokens"], 12)
        self.assertEqual(usage["total_tokens"], 46)
        self.assertEqual(usage["cached_tokens"], 16)


    def test_extracts_deepseek_prompt_cache_hit_tokens_from_trace(self) -> None:
        usage = extract_token_usage(
            {
                "raw": {
                    "message": (
                        "response_metadata={'token_usage': {'completion_tokens': 12, "
                        "'prompt_tokens': 34, 'total_tokens': 46, "
                        "'prompt_cache_hit_tokens': 18, "
                        "'prompt_cache_miss_tokens': 16}}"
                    )
                }
            }
        )

        self.assertIsNotNone(usage)
        self.assertEqual(usage["input_tokens"], 34)
        self.assertEqual(usage["output_tokens"], 12)
        self.assertEqual(usage["total_tokens"], 46)
        self.assertEqual(usage["cached_tokens"], 18)

if __name__ == "__main__":
    unittest.main()
