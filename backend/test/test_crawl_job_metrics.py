from __future__ import annotations

import unittest
from datetime import UTC, datetime, timedelta

from app.models import CrawlJob, CrawlJobRun, CrawlJobStatus
from app.modules.crawler.jobs.metrics import build_crawl_job_metrics


class CrawlJobMetricsTests(unittest.TestCase):
    def test_build_metrics_prefers_current_run_values(self) -> None:
        now = datetime(2026, 4, 29, 10, 0, 0, tzinfo=UTC)
        run = CrawlJobRun(
            id=10,
            job_id=1,
            attempt_number=2,
            status=CrawlJobStatus.PAUSED.value,
            active_seconds=125,
            input_tokens=1000,
            output_tokens=80,
            total_tokens=1080,
        )
        job = CrawlJob(
            id=1,
            university="示例大学",
            school="计算机学院",
            start_url="https://example.edu/faculty",
            status=CrawlJobStatus.PAUSED.value,
            progress_current=0,
            progress_total=0,
            agent_trace=[
                {
                    "raw": {
                        "type": "updates",
                        "data": {
                            "model": {
                                "messages": [
                                    "usage_metadata={'input_tokens': 1, 'output_tokens': 2, 'total_tokens': 3}",
                                ]
                            }
                        },
                    }
                }
            ],
            created_at=now - timedelta(hours=4),
            updated_at=now,
        )
        job.current_run = run

        metrics = build_crawl_job_metrics(job, now=now)

        self.assertEqual(metrics.input_tokens, 1000)
        self.assertEqual(metrics.output_tokens, 80)
        self.assertEqual(metrics.total_tokens, 1080)
        self.assertEqual(metrics.duration_seconds, 125)

    def test_build_metrics_adds_open_active_segment_for_running_run(self) -> None:
        now = datetime(2026, 4, 29, 10, 5, 0, tzinfo=UTC)
        run = CrawlJobRun(
            id=11,
            job_id=2,
            attempt_number=1,
            status=CrawlJobStatus.RUNNING.value,
            started_at=datetime(2026, 4, 29, 10, 0, 0, tzinfo=UTC),
            active_started_at=datetime(2026, 4, 29, 10, 3, 0, tzinfo=UTC),
            active_seconds=60,
            input_tokens=34,
            output_tokens=12,
            total_tokens=46,
        )
        job = CrawlJob(
            id=2,
            university="示例大学",
            school="计算机学院",
            start_url="https://example.edu/faculty",
            status=CrawlJobStatus.RUNNING.value,
            progress_current=0,
            progress_total=0,
            agent_trace=[],
            created_at=datetime(2026, 4, 29, 9, 0, 0, tzinfo=UTC),
            updated_at=now,
        )
        job.current_run = run

        metrics = build_crawl_job_metrics(job, now=now)

        self.assertEqual(metrics.duration_seconds, 180)
        self.assertEqual(metrics.total_tokens, 46)

    def test_build_metrics_aggregates_token_usage_and_duration(self) -> None:
        job = CrawlJob(
            id=1,
            university="示例大学",
            school="计算机学院",
            start_url="https://example.edu/faculty",
            status=CrawlJobStatus.NEEDS_REVIEW.value,
            progress_current=0,
            progress_total=0,
            agent_trace=[
                {
                    "raw": {
                        "type": "updates",
                        "data": {
                            "model": {
                                "messages": [
                                    "usage_metadata={'input_tokens': 100, 'output_tokens': 20, 'cached_tokens': 30, 'total_tokens': 120}",
                                ]
                            }
                        },
                    }
                },
                {
                    "raw": {
                        "type": "updates",
                        "data": {
                            "model": {
                                "messages": [
                                    "usage_metadata={'input_tokens': 80, 'output_tokens': 15, 'cached_tokens': 10, 'total_tokens': 95}",
                                ]
                            }
                        },
                    }
                },
            ],
            created_at=datetime(2026, 4, 27, 10, 0, 0, tzinfo=UTC),
            updated_at=datetime(2026, 4, 27, 10, 1, 30, tzinfo=UTC),
        )

        metrics = build_crawl_job_metrics(job)

        self.assertEqual(metrics.input_tokens, 180)
        self.assertEqual(metrics.output_tokens, 35)
        self.assertEqual(metrics.cached_tokens, 40)
        self.assertEqual(metrics.total_tokens, 215)
        self.assertEqual(metrics.duration_seconds, 90)

    def test_build_metrics_falls_back_to_response_metadata_token_usage(self) -> None:
        job = CrawlJob(
            id=2,
            university="示例大学",
            school="计算机学院",
            start_url="https://example.edu/faculty",
            status=CrawlJobStatus.RUNNING.value,
            progress_current=0,
            progress_total=0,
            agent_trace=[
                {
                    "raw": {
                        "type": "updates",
                        "data": {
                            "model": {
                                "messages": [
                                    "response_metadata={'token_usage': {'completion_tokens': 12, 'prompt_tokens': 34, 'total_tokens': 46}}",
                                ]
                            }
                        },
                    }
                },
            ],
            created_at=datetime(2026, 4, 27, 10, 0, 0, tzinfo=UTC),
            updated_at=datetime(2026, 4, 27, 10, 0, 5, tzinfo=UTC),
        )

        metrics = build_crawl_job_metrics(job)

        self.assertEqual(metrics.input_tokens, 34)
        self.assertEqual(metrics.output_tokens, 12)
        self.assertEqual(metrics.total_tokens, 46)
        self.assertEqual(metrics.duration_seconds, 5)

    def test_build_metrics_handles_missing_trace(self) -> None:
        job = CrawlJob(
            id=3,
            university="示例大学",
            school="计算机学院",
            start_url="https://example.edu/faculty",
            status=CrawlJobStatus.QUEUED.value,
            progress_current=0,
            progress_total=0,
            agent_trace=None,
            created_at=datetime(2026, 4, 27, 10, 0, 0, tzinfo=UTC),
            updated_at=datetime(2026, 4, 27, 10, 0, 0, tzinfo=UTC),
        )

        metrics = build_crawl_job_metrics(job)

        self.assertEqual(metrics.input_tokens, 0)
        self.assertEqual(metrics.output_tokens, 0)
        self.assertEqual(metrics.total_tokens, 0)
        self.assertEqual(metrics.duration_seconds, 0)


if __name__ == "__main__":
    unittest.main()
