from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from app.modules.crawler.agent import (
    CONTROLLED_CRAWLER_TOOL_NAMES,
    CrawlerAgentRunBudget,
    FACULTY_CRAWLER_SYSTEM_PROMPT,
    build_faculty_crawler_model,
    build_trace_event,
    run_faculty_crawler_agent,
    _format_chunked_crawl_page_response,
    _format_save_batch_result_for_model,
    _validate_professor_candidate_batch,
)
from app.models import LLMProfile
from app.modules.crawler.pages.tools import CrawlToolContext
from app.modules.crawler.pages.tools import PageSnapshot
from app.services.llm_runtime import LLMRuntimeAdaptation


class FacultyCrawlerAgentSaveResultTests(unittest.TestCase):
    def test_format_save_batch_result_for_model_is_compact(self) -> None:
        result = _format_save_batch_result_for_model(
            {
                "batch_status": "saved",
                "attempted_count": 10,
                "saved_count": 10,
                "merged_count": 0,
                "skipped_duplicate_count": 0,
                "rejected_count": 0,
                "failed_count": 0,
                "failed_items": [],
                "rejected_items": [],
                "total_saved_count": 50,
            }
        )

        self.assertEqual(
            result,
            {
                "batch_status": "saved",
                "attempted_count": 10,
                "saved_count": 10,
                "merged_count": 0,
                "skipped_duplicate_count": 0,
                "rejected_count": 0,
                "failed_count": 0,
                "failed_items": [],
                "rejected_items": [],
                "total_saved_count": 50,
            },
        )
        self.assertNotIn("name", str(result))
        self.assertNotIn("profile_url", str(result))

    def test_format_save_batch_result_for_model_includes_budget_metadata_when_present(self) -> None:
        result = _format_save_batch_result_for_model(
            {
                "batch_status": "rejected",
                "attempted_count": 1,
                "saved_count": 0,
                "failed_count": 1,
                "failed_items": [{"index": 0, "name": "张三", "reason": "name: Field required"}],
                "total_saved_count": 0,
                "retry_allowed": True,
                "failure_fingerprint": "abc123def456",
                "consecutive_same_batch_failures": 1,
                "total_save_failures": 1,
                "terminal_reason": None,
            }
        )

        self.assertEqual(result["retry_allowed"], True)
        self.assertEqual(result["failure_fingerprint"], "abc123def456")
        self.assertEqual(result["consecutive_same_batch_failures"], 1)
        self.assertEqual(result["total_save_failures"], 1)
        self.assertIsNone(result["terminal_reason"])


    def test_format_save_batch_result_for_model_includes_duplicate_feedback(self) -> None:
        result = _format_save_batch_result_for_model(
            {
                "batch_status": "duplicate_loop",
                "attempted_count": 10,
                "saved_count": 0,
                "merged_count": 0,
                "skipped_duplicate_count": 10,
                "rejected_count": 0,
                "failed_count": 0,
                "failed_items": [],
                "rejected_items": [],
                "total_saved_count": 20,
                "next_instruction": "连续多个批次均为重复候选，请停止保存当前内容，获取下一个 chunk 或结束任务。",
            }
        )

        self.assertEqual(result["merged_count"], 0)
        self.assertEqual(result["skipped_duplicate_count"], 10)
        self.assertEqual(result["rejected_count"], 0)
        self.assertIn("获取下一个 chunk", result["next_instruction"])

    def test_format_save_batch_result_for_model_excludes_candidate_payload_echo(self) -> None:
        result = _format_save_batch_result_for_model(
            {
                "batch_status": "saved",
                "attempted_count": 1,
                "saved_count": 1,
                "merged_count": 0,
                "skipped_duplicate_count": 0,
                "rejected_count": 0,
                "failed_count": 0,
                "failed_items": [],
                "rejected_items": [],
                "total_saved_count": 1,
                "candidates": [
                    {
                        "name": "张三",
                        "profile_url": "https://example.edu/zhang",
                        "evidence": "很长的页面证据正文",
                    }
                ],
            }
        )

        serialized = str(result)
        self.assertNotIn("candidates", result)
        self.assertNotIn("很长的页面证据正文", serialized)
        self.assertNotIn("https://example.edu/zhang", serialized)

    def test_validate_professor_candidate_batch_collects_schema_failures(self) -> None:
        payloads, failed_items = _validate_professor_candidate_batch(
            [
                {
                    "name": "张三",
                    "recent_papers": "",
                    "field_confidence": 0.8,
                    "evidence": "页面",
                },
                {
                    "recent_papers": [],
                },
            ]
        )

        self.assertEqual([payload.name for payload in payloads], ["张三"])
        self.assertEqual(payloads[0].recent_papers, [])
        self.assertEqual(payloads[0].field_confidence, {"overall": 0.8})
        self.assertEqual(payloads[0].evidence, {"summary": "页面"})
        self.assertEqual(len(failed_items), 1)
        self.assertEqual(failed_items[0]["index"], 1)
        self.assertIsNone(failed_items[0]["name"])
        self.assertIn("name", failed_items[0]["reason"])


class FacultyCrawlerAgentPromptTests(unittest.TestCase):
    def test_system_prompt_requires_structured_numeric_constraints(self) -> None:
        self.assertIn("每个候选对象都必须使用英文键", FACULTY_CRAWLER_SYSTEM_PROMPT)
        self.assertIn("字段值尽量保持页面原文", FACULTY_CRAWLER_SYSTEM_PROMPT)
        self.assertIn("不要翻译、音译或拼音化", FACULTY_CRAWLER_SYSTEM_PROMPT)
        self.assertIn("confidence 必须是 0 到 1 的数字", FACULTY_CRAWLER_SYSTEM_PROMPT)
        self.assertIn("evidence 保持简短", FACULTY_CRAWLER_SYSTEM_PROMPT)

    def test_system_prompt_makes_chunk_save_path_mandatory(self) -> None:
        self.assertIn("页面正文中的候选必须通过 submit_page_chunk_candidates 提交", FACULTY_CRAWLER_SYSTEM_PROMPT)
        self.assertIn("不要尝试使用其他保存入口", FACULTY_CRAWLER_SYSTEM_PROMPT)
        self.assertIn("返回 status=chunked", FACULTY_CRAWLER_SYSTEM_PROMPT)
        self.assertIn("必须立即调用 claim_next_page_chunk", FACULTY_CRAWLER_SYSTEM_PROMPT)

    def test_system_prompt_keeps_discovery_round_out_of_profile_detail_pages(self) -> None:
        self.assertIn("当前是第一轮候选发现模式，不是详情页补全模式", FACULTY_CRAWLER_SYSTEM_PROMPT)
        self.assertIn("只从列表页、目录页、分页页中发现候选导师", FACULTY_CRAWLER_SYSTEM_PROMPT)
        self.assertIn("只把它保存为 profile_url", FACULTY_CRAWLER_SYSTEM_PROMPT)
        self.assertIn("不要调用 crawl_page 或 investigate_with_browser 进入个人详情页", FACULTY_CRAWLER_SYSTEM_PROMPT)
        self.assertIn("详情字段可以留空", FACULTY_CRAWLER_SYSTEM_PROMPT)
        self.assertIn("用户手动选择候选后进入详情页补全模式", FACULTY_CRAWLER_SYSTEM_PROMPT)

    def test_system_prompt_requires_finishing_current_chunk_before_new_page(self) -> None:
        self.assertIn("领取 chunk 后必须先完成当前 chunk", FACULTY_CRAWLER_SYSTEM_PROMPT)
        self.assertIn("发现新的候选列表页或分页页链接时，先记住该 URL", FACULTY_CRAWLER_SYSTEM_PROMPT)
        self.assertIn("当前 chunk 完成后再调用 crawl_page 探索新列表/分页页面", FACULTY_CRAWLER_SYSTEM_PROMPT)
        self.assertIn("不要在同一轮同时调用 submit_page_chunk_candidates 和 crawl_page", FACULTY_CRAWLER_SYSTEM_PROMPT)
        self.assertIn("has_unsubmitted_candidates_in_current_chunk", FACULTY_CRAWLER_SYSTEM_PROMPT)
        self.assertIn("刚好提交 10 个候选不代表需要拆分", FACULTY_CRAWLER_SYSTEM_PROMPT)
        self.assertIn("investigate_with_browser 不能用于绕过 chunk", FACULTY_CRAWLER_SYSTEM_PROMPT)
        self.assertIn("当前存在待处理 chunk 时，必须先 claim_next_page_chunk", FACULTY_CRAWLER_SYSTEM_PROMPT)
        self.assertIn("只有当前 chunk 正文中明确还有超过 10 个已看见候选", FACULTY_CRAWLER_SYSTEM_PROMPT)

    def test_system_prompt_keeps_stable_sections_before_dynamic_input(self) -> None:
        prompt = FACULTY_CRAWLER_SYSTEM_PROMPT
        expected_headings = [
            "角色与目标：",
            "页面与 chunk 处理流程：",
            "工具使用边界：",
            "候选字段与提交约束：",
            "禁止事项与错误恢复：",
        ]
        positions = [prompt.index(heading) for heading in expected_headings]
        self.assertEqual(positions, sorted(positions))
        self.assertNotIn("入口 URL", prompt)
        self.assertNotIn("学校:", prompt)
        self.assertNotIn("学院/单位:", prompt)

class FacultyCrawlerAgentMiddlewareTests(unittest.TestCase):
    def test_chunked_crawl_page_response_omits_full_page_text(self) -> None:
        snapshot = PageSnapshot(
            page_id=123,
            url="https://cs.example.edu/faculty",
            title="师资队伍",
            text="张三\n李四" * 100,
            html="<main>大量页面正文</main>",
            links=["https://cs.example.edu/zhang"],
            fetch_method="http",
            status="succeeded",
        )

        result = _format_chunked_crawl_page_response(snapshot, created_chunks=2)

        self.assertEqual(result["status"], "chunked")
        self.assertEqual(result["created_chunks"], 2)
        self.assertNotIn("text", result)
        self.assertNotIn("html", result)
        self.assertIn("claim_next_page_chunk", result["message"])

    def test_build_trace_event_truncates_large_chunk_content(self) -> None:
        event = {"data": {"tools": {"messages": [{"content": "x" * 2000}]}}}
        trace = build_trace_event(event)
        self.assertNotIn("x" * 1500, str(trace))
        self.assertIn("chunk 内容已截断", str(trace))

    def test_controlled_tool_names_include_chunk_tools(self) -> None:
        self.assertIn("claim_next_page_chunk", CONTROLLED_CRAWLER_TOOL_NAMES)
        self.assertIn("submit_page_chunk_candidates", CONTROLLED_CRAWLER_TOOL_NAMES)

    def test_run_agent_starts_with_claim_prompt_when_chunks_are_pending(self) -> None:
        async def run() -> str:
            captured: dict[str, object] = {}

            class FakeAgent:
                async def astream(self, input_payload: object, **kwargs: object):
                    captured["input_payload"] = input_payload
                    captured["kwargs"] = kwargs
                    yield {"event_type": "done"}

            ctx = CrawlToolContext(
                job_id=1,
                start_url="https://cs.example.edu/faculty",
                university="示例大学",
                school="计算机学院",
                session_factory=object(),  # type: ignore[arg-type]
            )
            profile = LLMProfile(name="test", provider="openai", api_key="sk-test", model_name="gpt-test")

            with (
                patch("app.modules.crawler.agent.crawl_job_has_pending_work", AsyncMock(return_value=True)),
                patch("app.modules.crawler.agent.create_faculty_crawler_agent", return_value=FakeAgent()),
                patch("app.modules.crawler.agent._ensure_agent_job_can_continue", AsyncMock()),
            ):
                await run_faculty_crawler_agent(ctx, profile)

            payload = captured["input_payload"]
            assert isinstance(payload, dict)
            return str(payload["messages"][0]["content"])

        prompt = __import__("asyncio").run(run())

        self.assertIn("已有待处理页面片段", prompt)
        self.assertIn("立即调用 claim_next_page_chunk", prompt)
        self.assertIn("不要重新抓取入口页", prompt)

    def test_run_agent_stops_after_completed_chunk_budget_event(self) -> None:
        async def run() -> tuple[dict[str, object], list[dict[str, object]]]:
            events: list[dict[str, object]] = []

            class FakeAgent:
                async def astream(self, input_payload: object, **kwargs: object):
                    _ = input_payload, kwargs
                    yield {
                        "event": "on_tool_end",
                        "name": "submit_page_chunk_candidates",
                        "data": {"output": {"chunk_status": "completed", "saved_count": 2}},
                    }
                    yield {"event": "should_not_be_seen"}

            async def trace_callback(event: object) -> None:
                assert isinstance(event, dict)
                events.append(event)

            ctx = CrawlToolContext(
                job_id=1,
                start_url="https://cs.example.edu/faculty",
                university="示例大学",
                school="计算机学院",
                session_factory=object(),  # type: ignore[arg-type]
            )
            profile = LLMProfile(name="test", provider="openai", api_key="sk-test", model_name="gpt-test")

            with (
                patch("app.modules.crawler.agent.crawl_job_has_pending_work", AsyncMock(return_value=True)),
                patch("app.modules.crawler.agent.create_faculty_crawler_agent", return_value=FakeAgent()),
                patch("app.modules.crawler.agent._ensure_agent_job_can_continue", AsyncMock()),
            ):
                result = await run_faculty_crawler_agent(
                    ctx,
                    profile,
                    trace_callback=trace_callback,
                    run_budget=CrawlerAgentRunBudget(max_completed_chunks=1),
                )
            assert isinstance(result, dict)
            return result, events

        result, events = __import__("asyncio").run(run())

        self.assertEqual(result["event_type"], "agent_context_budget_reached")
        self.assertEqual(result["reason"], "max_completed_chunks")
        self.assertEqual(result["completed_chunks"], 1)
        self.assertTrue(any(event.get("event_type") == "agent_context_budget_reached" for event in events))
        self.assertFalse(any(event.get("event") == "should_not_be_seen" for event in events))

    def test_chunk_budget_does_not_stop_when_no_pending_work_remains(self) -> None:
        async def run() -> tuple[dict[str, object], list[dict[str, object]]]:
            events: list[dict[str, object]] = []

            class FakeAgent:
                async def astream(self, input_payload: object, **kwargs: object):
                    _ = input_payload, kwargs
                    yield {
                        "event": "on_tool_end",
                        "name": "submit_page_chunk_candidates",
                        "data": {"output": {"chunk_status": "completed", "saved_count": 1}},
                    }
                    yield {"event": "agent_continued_after_completed_chunk"}

            async def trace_callback(event: object) -> None:
                assert isinstance(event, dict)
                events.append(event)

            ctx = CrawlToolContext(
                job_id=1,
                start_url="https://cs.example.edu/faculty",
                university="示例大学",
                school="计算机学院",
                session_factory=object(),  # type: ignore[arg-type]
            )
            profile = LLMProfile(name="test", provider="openai", api_key="sk-test", model_name="gpt-test")

            with (
                patch("app.modules.crawler.agent.crawl_job_has_pending_work", AsyncMock(side_effect=[True, False, False])),
                patch("app.modules.crawler.agent.create_faculty_crawler_agent", return_value=FakeAgent()),
                patch("app.modules.crawler.agent._ensure_agent_job_can_continue", AsyncMock()),
            ):
                result = await run_faculty_crawler_agent(
                    ctx,
                    profile,
                    trace_callback=trace_callback,
                    run_budget=CrawlerAgentRunBudget(max_completed_chunks=1),
                )
            assert isinstance(result, dict)
            return result, events

        result, events = __import__("asyncio").run(run())

        self.assertEqual(result["event"], "agent_continued_after_completed_chunk")
        self.assertFalse(any(event.get("event_type") == "agent_context_budget_reached" for event in events))

    def test_no_candidates_counts_as_completed_chunk(self) -> None:
        from app.modules.crawler.agent import _is_completed_chunk_submit_event

        self.assertTrue(
            _is_completed_chunk_submit_event(
                {
                    "event": "on_tool_end",
                    "name": "submit_page_chunk_candidates",
                    "data": {"output": {"chunk_status": "no_candidates"}},
                }
            )
        )

    def test_agent_exposes_only_supported_tools(self) -> None:
        captured_tools: dict[str, object] = {}

        def fake_create_deep_agent(**kwargs: object) -> object:
            captured_tools["tools"] = kwargs["tools"]
            return SimpleNamespace()

        ctx = CrawlToolContext(
            job_id=1,
            start_url="https://cs.example.edu/faculty",
            university="示例大学",
            school="计算机学院",
            session_factory=object(),  # type: ignore[arg-type]
        )
        profile = LLMProfile(name="test", provider="openai", api_key="sk-test", model_name="gpt-test")

        with (
            patch("app.modules.crawler.agent.create_deep_agent", side_effect=fake_create_deep_agent),
            patch("app.modules.crawler.agent.build_faculty_crawler_model", return_value=object()),
        ):
            from app.modules.crawler.agent import create_faculty_crawler_agent

            create_faculty_crawler_agent(ctx, profile)

        tool_names = {getattr(tool, "name", "") for tool in captured_tools["tools"]}
        self.assertEqual(
            tool_names,
            {
                "crawl_page",
                "investigate_with_browser",
                "claim_next_page_chunk",
                "submit_page_chunk_candidates",
            },
        )

    def test_browser_investigate_chunks_successful_page_snapshot(self) -> None:
        async def run() -> dict[str, object]:
            captured_tools: dict[str, object] = {}

            def fake_create_deep_agent(**kwargs: object) -> object:
                captured_tools["tools"] = kwargs["tools"]
                return SimpleNamespace()

            snapshot = PageSnapshot(
                page_id=123,
                url="https://cs.example.edu/faculty",
                title="师资队伍",
                text="张三\n教授\n机器学习",
                html="<main>张三</main>",
                links=[],
                fetch_method="browser",
                status="succeeded",
            )
            ctx = CrawlToolContext(
                job_id=1,
                start_url="https://cs.example.edu/faculty",
                university="示例大学",
                school="计算机学院",
                session_factory=object(),  # type: ignore[arg-type]
            )
            profile = LLMProfile(name="test", provider="openai", api_key="sk-test", model_name="gpt-test")

            with (
                patch("app.modules.crawler.agent.crawl_job_has_pending_work", AsyncMock(return_value=False)),
                patch("app.modules.crawler.agent.get_source_url_chunk_state", AsyncMock(return_value=None)),
                patch("app.modules.crawler.agent.browser_investigate", AsyncMock(return_value=snapshot)),
                patch("app.modules.crawler.agent.create_chunks_for_successful_page_snapshot", AsyncMock(return_value=1)),
                patch("app.modules.crawler.agent.create_deep_agent", side_effect=fake_create_deep_agent),
                patch("app.modules.crawler.agent.build_faculty_crawler_model", return_value=object()),
            ):
                from app.modules.crawler.agent import create_faculty_crawler_agent

                create_faculty_crawler_agent(ctx, profile)
                browser_tool = next(tool for tool in captured_tools["tools"] if getattr(tool, "name", "") == "investigate_with_browser")
                return await browser_tool.ainvoke({"url": "https://cs.example.edu/faculty", "goal": "查看导师列表"})

        result = __import__("asyncio").run(run())

        self.assertEqual(result["status"], "chunked")
        self.assertEqual(result["created_chunks"], 1)
        self.assertNotIn("text", result)
        self.assertNotIn("html", result)

    def test_browser_investigate_reuses_failed_snapshot_for_same_url(self) -> None:
        async def run() -> tuple[dict[str, object], dict[str, object], int]:
            captured_tools: dict[str, object] = {}

            def fake_create_deep_agent(**kwargs: object) -> object:
                captured_tools["tools"] = kwargs["tools"]
                return SimpleNamespace()

            snapshot = PageSnapshot(
                url="https://cs.example.edu/faculty",
                title=None,
                text="",
                html="",
                links=[],
                fetch_method="browser",
                status="failed",
                error_message="Blocked by anti-bot protection",
                suspicious_empty=True,
            )
            ctx = CrawlToolContext(
                job_id=1,
                start_url="https://cs.example.edu/faculty",
                university="示例大学",
                school="计算机学院",
                session_factory=object(),  # type: ignore[arg-type]
            )
            profile = LLMProfile(name="test", provider="openai", api_key="sk-test", model_name="gpt-test")

            with (
                patch("app.modules.crawler.agent.crawl_job_has_pending_work", AsyncMock(return_value=False)),
                patch("app.modules.crawler.agent.get_source_url_chunk_state", AsyncMock(return_value=None)),
                patch("app.modules.crawler.pages.tools._crawl_page_with_browser", AsyncMock(return_value=snapshot)) as browser_mock,
                patch("app.modules.crawler.pages.tools._ensure_crawl_job_can_continue_for_context", AsyncMock()),
                patch("app.modules.crawler.pages.tools.record_page_snapshot", AsyncMock()),
                patch("app.modules.crawler.agent.create_deep_agent", side_effect=fake_create_deep_agent),
                patch("app.modules.crawler.agent.build_faculty_crawler_model", return_value=object()),
            ):
                from app.modules.crawler.agent import create_faculty_crawler_agent

                create_faculty_crawler_agent(ctx, profile)
                browser_tool = next(tool for tool in captured_tools["tools"] if getattr(tool, "name", "") == "investigate_with_browser")
                first = await browser_tool.ainvoke({"url": "https://cs.example.edu/faculty", "goal": "查看导师列表"})
                second = await browser_tool.ainvoke({"url": "https://cs.example.edu/faculty", "goal": "再次查看导师列表"})

            return first, second, browser_mock.await_count

        first, second, await_count = __import__("asyncio").run(run())

        self.assertEqual(first["status"], "failed")
        self.assertEqual(second["status"], "failed")
        self.assertEqual(await_count, 1)

    def test_browser_investigate_returns_chunk_instruction_when_chunks_are_pending(self) -> None:
        async def run() -> dict[str, object]:
            captured_tools: dict[str, object] = {}

            def fake_create_deep_agent(**kwargs: object) -> object:
                captured_tools["tools"] = kwargs["tools"]
                return SimpleNamespace()

            ctx = CrawlToolContext(
                job_id=1,
                start_url="https://cs.example.edu/faculty",
                university="示例大学",
                school="计算机学院",
                session_factory=object(),  # type: ignore[arg-type]
            )
            profile = LLMProfile(name="test", provider="openai", api_key="sk-test", model_name="gpt-test")

            with (
                patch("app.modules.crawler.agent.crawl_job_has_pending_work", AsyncMock(return_value=True)),
                patch("app.modules.crawler.agent.browser_investigate", AsyncMock()) as browser_mock,
                patch("app.modules.crawler.agent.create_deep_agent", side_effect=fake_create_deep_agent),
                patch("app.modules.crawler.agent.build_faculty_crawler_model", return_value=object()),
            ):
                from app.modules.crawler.agent import create_faculty_crawler_agent

                create_faculty_crawler_agent(ctx, profile)
                browser_tool = next(tool for tool in captured_tools["tools"] if getattr(tool, "name", "") == "investigate_with_browser")
                result = await browser_tool.ainvoke({"url": "https://cs.example.edu/faculty", "goal": "查看导师列表"})

            browser_mock.assert_not_awaited()
            return result

        result = __import__("asyncio").run(run())

        self.assertEqual(result["status"], "chunk_required")
        self.assertIn("claim_next_page_chunk", result["next_instruction"])
        self.assertNotIn("content", result)

    def test_submit_page_chunk_candidates_requires_chunk_id(self) -> None:
        captured_tools: dict[str, object] = {}

        def fake_create_deep_agent(**kwargs: object) -> object:
            captured_tools["tools"] = kwargs["tools"]
            return SimpleNamespace()

        ctx = CrawlToolContext(
            job_id=1,
            start_url="https://cs.example.edu/faculty",
            university="示例大学",
            school="计算机学院",
            session_factory=object(),  # type: ignore[arg-type]
        )
        profile = LLMProfile(name="test", provider="openai", api_key="sk-test", model_name="gpt-test")

        with (
            patch("app.modules.crawler.agent.create_deep_agent", side_effect=fake_create_deep_agent),
            patch("app.modules.crawler.agent.build_faculty_crawler_model", return_value=object()),
        ):
            from app.modules.crawler.agent import create_faculty_crawler_agent

            create_faculty_crawler_agent(ctx, profile)

        submit_tool = next(tool for tool in captured_tools["tools"] if getattr(tool, "name", "") == "submit_page_chunk_candidates")
        schema = submit_tool.args_schema.model_json_schema()
        self.assertIn("chunk_id", schema["required"])
        self.assertIn("chunk_status", schema["required"])
        self.assertIn("candidates", schema["required"])

    def test_crawl_page_returns_already_completed_for_finished_chunk_page(self) -> None:
        async def run() -> dict[str, object]:
            captured_tools: dict[str, object] = {}

            def fake_create_deep_agent(**kwargs: object) -> object:
                captured_tools["tools"] = kwargs["tools"]
                return SimpleNamespace()

            ctx = CrawlToolContext(
                job_id=1,
                start_url="https://cs.example.edu/faculty",
                university="示例大学",
                school="计算机学院",
                session_factory=object(),  # type: ignore[arg-type]
            )
            profile = LLMProfile(name="test", provider="openai", api_key="sk-test", model_name="gpt-test")

            with (
                patch("app.modules.crawler.agent.get_source_url_chunk_state", AsyncMock(return_value="completed")),
                patch("app.modules.crawler.agent.crawl_page_with_browser_fallback", AsyncMock()) as crawl_page_mock,
                patch("app.modules.crawler.agent.create_deep_agent", side_effect=fake_create_deep_agent),
                patch("app.modules.crawler.agent.build_faculty_crawler_model", return_value=object()),
            ):
                from app.modules.crawler.agent import create_faculty_crawler_agent

                create_faculty_crawler_agent(ctx, profile)
                tools = captured_tools["tools"]
                crawl_tool = next(tool for tool in tools if getattr(tool, "name", "") == "crawl_page")
                result = await crawl_tool.ainvoke({"url": "https://cs.example.edu/faculty"})

            crawl_page_mock.assert_not_awaited()
            return result

        result = __import__("asyncio").run(run())

        self.assertEqual(result["status"], "already_completed")
        self.assertIn("不返回页面内容", result["message"])
        self.assertNotIn("content", result)


class FacultyCrawlerAgentModelTests(unittest.TestCase):
    def test_crawler_model_uses_responses_endpoint_from_runtime_adaptation(self) -> None:
        profile = LLMProfile(
            name="Responses relay",
            provider="openai",
            api_base_url="https://relay.example/v1",
            api_key="sk-test",
            model_name="relay-model",
        )
        adaptation = LLMRuntimeAdaptation(
            endpoint_kind="responses",
            thinking_extra_body={"thinking": {"type": "disabled"}},
        )

        with patch("app.modules.crawler.agent.ChatOpenAI") as chat_openai:
            build_faculty_crawler_model(profile, adaptation=adaptation)

        kwargs = chat_openai.call_args.kwargs
        self.assertTrue(kwargs["use_responses_api"])
        self.assertEqual(kwargs["extra_body"], adaptation.thinking_extra_body)

    def test_crawler_model_passes_thinking_override_from_runtime_adaptation(self) -> None:
        profile = LLMProfile(
            name="acme",
            provider="openai",
            api_base_url="https://api.acme.ai/v1",
            api_key="sk-test",
            model_name="acme-think-v1",
        )

        with patch("app.modules.crawler.agent.ChatOpenAI") as chat_openai:
            build_faculty_crawler_model(
                profile,
                adaptation=LLMRuntimeAdaptation(
                    "chat_completions",
                    {"thinking": {"type": "disabled"}},
                ),
            )

        kwargs = chat_openai.call_args.kwargs
        self.assertEqual(kwargs["extra_body"], {"thinking": {"type": "disabled"}})

    def test_crawler_model_omits_extra_body_when_none(self) -> None:
        profile = LLMProfile(
            name="OpenAI",
            provider="openai",
            api_base_url="https://api.openai.com/v1",
            api_key="sk-test",
            model_name="gpt-4o-mini",
        )

        with patch("app.modules.crawler.agent.ChatOpenAI") as chat_openai:
            build_faculty_crawler_model(
                profile,
                adaptation=LLMRuntimeAdaptation("chat_completions", None),
            )

        self.assertNotIn("extra_body", chat_openai.call_args.kwargs)
        self.assertFalse(chat_openai.call_args.kwargs["use_responses_api"])

    def test_deepseek_profile_does_not_implicitly_add_extra_body(self) -> None:
        profile = LLMProfile(
            name="DeepSeek",
            provider="deepseek",
            api_base_url="https://api.deepseek.com/v1",
            api_key="sk-test",
            model_name="deepseek-chat",
        )

        with patch("app.modules.crawler.agent.ChatOpenAI") as chat_openai:
            build_faculty_crawler_model(
                profile,
                adaptation=LLMRuntimeAdaptation("chat_completions", None),
            )

        self.assertNotIn("extra_body", chat_openai.call_args.kwargs)


if __name__ == "__main__":
    unittest.main()
