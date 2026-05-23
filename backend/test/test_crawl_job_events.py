from __future__ import annotations

import unittest
from datetime import UTC, datetime

from app.models import CrawlCandidate, CrawlJob, CrawlJobStatus, CrawlPage, CrawlPageStatus
from app.services.crawl_job_events import (
    build_crawl_job_events,
    normalize_agent_trace_event,
    summarize_agent_trace_event,
)


class CrawlJobEventsTests(unittest.TestCase):
    def test_chunk_tool_events_are_human_readable(self) -> None:
        self.assertEqual(summarize_agent_trace_event({"name": "claim_next_page_chunk"}), "Agent 领取待处理页面片段")
        self.assertEqual(summarize_agent_trace_event({"name": "submit_chunk_candidates"}), "Agent 提交页面片段候选")
        self.assertEqual(summarize_agent_trace_event({"event_type": "chunk_split_required"}), "页面片段候选过密，已触发拆分")

    def test_build_events_includes_status_trace_page_and_candidate(self) -> None:
        job = CrawlJob(
            id=1,
            university="示例大学",
            school="计算机学院",
            start_url="https://example.edu/faculty",
            status=CrawlJobStatus.NEEDS_REVIEW.value,
            progress_current=1,
            progress_total=1,
            agent_trace=[
                {
                    "event_type": "tool_call",
                    "message": "调用 crawl_page 抓取入口页面",
                    "created_at": "2026-04-26T10:01:00+00:00",
                },
            ],
            created_at=datetime(2026, 4, 26, 10, 0, tzinfo=UTC),
            updated_at=datetime(2026, 4, 26, 10, 2, tzinfo=UTC),
        )
        pages = [
            CrawlPage(
                id=10,
                job_id=1,
                url="https://example.edu/faculty",
                parent_url=None,
                fetch_method="http",
                page_type="faculty_list",
                status=CrawlPageStatus.SUCCEEDED.value,
                title="Faculty",
                text_excerpt=None,
                error_message=None,
                created_at=datetime(2026, 4, 26, 10, 3, tzinfo=UTC),
            ),
        ]
        candidates = [
            CrawlCandidate(
                id=20,
                job_id=1,
                name="张教授",
                email="zhang@example.edu",
                title="Professor",
                university="示例大学",
                school="计算机学院",
                department="CS",
                research_direction=None,
                recent_papers=None,
                profile_url=None,
                source_url="https://example.edu/faculty",
                confidence=0.9,
                created_at=datetime(2026, 4, 26, 10, 4, tzinfo=UTC),
                updated_at=datetime(2026, 4, 26, 10, 4, tzinfo=UTC),
            ),
        ]

        events = build_crawl_job_events(job, pages=pages, candidates=candidates)

        messages = [event["message"] for event in events]
        self.assertIn("任务进入待审核", messages)
        self.assertIn("调用 crawl_page 抓取入口页面", messages)
        self.assertIn("已抓取页面：Faculty", messages)
        self.assertIn("发现候选导师：张教授", messages)
        for event in events:
            self.assertIn("id", event)
            self.assertEqual(event["job_id"], 1)
            self.assertIn("event_type", event)
            self.assertIn("created_at", event)
            self.assertIn("raw", event)

    def test_build_events_includes_partially_completed_status_message(self) -> None:
        job = CrawlJob(
            id=5,
            university="示例大学",
            school="计算机学院",
            start_url="https://example.edu/faculty",
            status=CrawlJobStatus.PARTIALLY_COMPLETED.value,
            progress_current=1,
            progress_total=3,
            created_at=datetime(2026, 4, 26, 10, 0, tzinfo=UTC),
            updated_at=datetime(2026, 4, 26, 10, 5, tzinfo=UTC),
        )

        events = build_crawl_job_events(job, pages=[], candidates=[])

        self.assertEqual(events[0]["message"], "任务部分候选已导入")
        self.assertEqual(events[0]["raw"]["status"], "partially_completed")

    def test_trace_without_message_uses_nested_name(self) -> None:
        message = summarize_agent_trace_event(
            {
                "event_type": "tool_call",
                "data": {
                    "tool": {
                        "name": "crawl_page",
                    },
                },
            },
        )

        self.assertEqual(message, "Agent 调用 crawl_page 抓取页面")

    def test_trace_update_with_tool_call_string_uses_specific_tool_message(self) -> None:
        message = summarize_agent_trace_event(
            {
                "type": "updates",
                "data": {
                    "model": {
                        "messages": [
                            "content='' tool_calls=[{'name': 'crawl_page', 'args': {'url': 'https://example.edu'}}]",
                        ],
                    },
                },
            },
        )

        self.assertEqual(message, "Agent 调用 crawl_page 抓取页面")

    def test_trace_with_candidate_name_does_not_treat_name_as_tool(self) -> None:
        message = summarize_agent_trace_event(
            {
                "type": "updates",
                "data": {
                    "tool_result": {
                        "candidate": {
                            "name": "万常选",
                            "email": "example@example.edu",
                        },
                    },
                },
            },
        )

        self.assertEqual(message, "Agent 事件：updates")

    def test_trace_keeps_enrichment_event_message(self) -> None:
        message = summarize_agent_trace_event(
            {
                "event_type": "enrichment",
                "message": "候选导师详情补全成功：张三（院系、研究方向）",
                "raw": {
                    "candidate_id": 1,
                },
            }
        )

        self.assertEqual(message, "候选导师详情补全成功：张三（院系、研究方向）")

    def test_build_events_omits_generic_agent_update_messages(self) -> None:
        job = CrawlJob(
            id=3,
            university="示例大学",
            school="计算机学院",
            start_url="https://example.edu/faculty",
            status=CrawlJobStatus.RUNNING.value,
            progress_current=0,
            progress_total=0,
            agent_trace=[
                {
                    "event_type": "updates",
                    "message": "Agent 事件：updates",
                    "created_at": "2026-04-26T10:01:00+00:00",
                    "raw": {
                        "type": "updates",
                        "data": {"TodoListMiddleware.after_model": None},
                    },
                },
                {
                    "type": "updates",
                    "data": {
                        "model": {
                            "messages": [
                                "tool_calls=[{'name': 'save_professor_candidates', 'args': {}}]",
                            ],
                        },
                    },
                    "created_at": "2026-04-26T10:02:00+00:00",
                },
            ],
            created_at=datetime(2026, 4, 26, 10, 0, tzinfo=UTC),
            updated_at=datetime(2026, 4, 26, 10, 3, tzinfo=UTC),
        )

        messages = [event["message"] for event in build_crawl_job_events(job, pages=[], candidates=[])]

        self.assertIn("Agent 保存候选导师", messages)
        self.assertNotIn("Agent 事件：updates", messages)

    def test_naive_datetime_event_times_are_marked_as_utc(self) -> None:
        job = CrawlJob(
            id=4,
            university="示例大学",
            school="计算机学院",
            start_url="https://example.edu/faculty",
            status=CrawlJobStatus.FAILED.value,
            progress_current=0,
            progress_total=0,
            created_at=datetime(2026, 4, 26, 8, 0),
            updated_at=datetime(2026, 4, 26, 8, 34),
        )

        events = build_crawl_job_events(job, pages=[], candidates=[])

        self.assertEqual(events[0]["created_at"], "2026-04-26T08:34:00+00:00")

    def test_non_dict_and_empty_trace_do_not_raise(self) -> None:
        self.assertEqual(normalize_agent_trace_event({})["message"], "Agent 更新了执行状态")
        self.assertEqual(summarize_agent_trace_event({}), "Agent 更新了执行状态")
        self.assertEqual(summarize_agent_trace_event("not a dict"), "Agent 更新了执行状态")

        job = CrawlJob(
            id=2,
            university="示例大学",
            school="计算机学院",
            start_url="https://example.edu/faculty",
            status=CrawlJobStatus.QUEUED.value,
            progress_current=0,
            progress_total=0,
            agent_trace=[{}, "not a dict"],
            created_at=None,
            updated_at=None,
        )

        events = build_crawl_job_events(job, pages=[], candidates=[])

        self.assertIn("任务已排队", [event["message"] for event in events])


if __name__ == "__main__":
    unittest.main()
