from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from app.agents.faculty_crawler_agent import (
    CONTROLLED_CRAWLER_TOOL_NAMES,
    FACULTY_CRAWLER_SYSTEM_PROMPT,
    SaveHistoryCompactionMiddleware,
    build_faculty_crawler_model,
    build_trace_event,
    compact_save_tool_history,
    _format_save_batch_result_for_model,
    _validate_professor_candidate_batch,
)
from app.models import LLMProfile


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


class FacultyCrawlerAgentCompactionTests(unittest.TestCase):
    def test_system_prompt_requires_structured_numeric_constraints(self) -> None:
        self.assertIn("每个候选对象都必须使用英文键", FACULTY_CRAWLER_SYSTEM_PROMPT)
        self.assertIn("字段值尽量保持页面原文", FACULTY_CRAWLER_SYSTEM_PROMPT)
        self.assertIn("不要翻译、音译或拼音化", FACULTY_CRAWLER_SYSTEM_PROMPT)
        self.assertIn("confidence 必须是 0 到 1 的数字", FACULTY_CRAWLER_SYSTEM_PROMPT)
        self.assertIn("evidence 保持简短", FACULTY_CRAWLER_SYSTEM_PROMPT)

    def test_compact_save_tool_history_keeps_saved_candidate_identities(self) -> None:
        messages = [
            HumanMessage(content="入口任务"),
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "save_professor_candidates",
                        "args": {
                            "candidates": [
                                {
                                    "name": "张三",
                                    "profile_url": "https://example.edu/zhang",
                                },
                                {"name": "李四"},
                            ]
                        },
                        "id": "call_1",
                    }
                ],
            ),
            ToolMessage(
                content='{"batch_status":"saved","attempted_count":2,"saved_count":2,"failed_count":0,"failed_items":[],"total_saved_count":2}',
                tool_call_id="call_1",
            ),
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "save_professor_candidates",
                        "args": {"candidates": [{"name": "王五"}]},
                        "id": "call_2",
                    }
                ],
            ),
            ToolMessage(
                content='{"batch_status":"saved","attempted_count":1,"saved_count":1,"failed_count":0,"failed_items":[],"total_saved_count":3}',
                tool_call_id="call_2",
            ),
        ]

        compacted = compact_save_tool_history(messages)
        serialized = "\n".join(str(message.content) for message in compacted)

        self.assertEqual(len(compacted), 2)
        self.assertIsInstance(compacted[0], HumanMessage)
        self.assertIsInstance(compacted[1], HumanMessage)
        self.assertIn("3", serialized)
        self.assertIn("张三 (https://example.edu/zhang)", serialized)
        self.assertIn("李四", serialized)
        self.assertIn("王五", serialized)

    def test_compact_save_tool_history_keeps_rejected_batch_failures(self) -> None:
        messages = [
            HumanMessage(content="入口任务"),
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "save_professor_candidates",
                        "args": {"candidates": [{"name": ""}]},
                        "id": "call_bad",
                    }
                ],
            ),
            ToolMessage(
                content='{"batch_status":"rejected","attempted_count":1,"saved_count":0,"failed_count":1,"failed_items":[{"index":0,"name":null,"reason":"name 不能为空"}],"total_saved_count":20}',
                tool_call_id="call_bad",
            ),
        ]

        compacted = compact_save_tool_history(messages)
        serialized = "\n".join(str(message.content) for message in compacted)

        self.assertIn("20", serialized)
        self.assertIn("index=0", serialized)
        self.assertIn("name 不能为空", serialized)

    def test_compaction_does_not_leave_orphan_save_tool_messages(self) -> None:
        messages = [
            HumanMessage(content="入口任务"),
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "save_professor_candidates",
                        "args": {"candidates": [{"name": "张三"}]},
                        "id": "call_1",
                    }
                ],
            ),
            ToolMessage(
                content='{"batch_status":"saved","attempted_count":1,"saved_count":1,"failed_count":0,"failed_items":[],"total_saved_count":1}',
                tool_call_id="call_1",
            ),
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "crawl_page",
                        "args": {"url": "https://example.edu"},
                        "id": "crawl_1",
                    }
                ],
            ),
            ToolMessage(
                content='{"status":"succeeded","text":"页面内容"}',
                tool_call_id="crawl_1",
            ),
        ]

        compacted = compact_save_tool_history(messages)
        tool_ids = {
            getattr(message, "tool_call_id", None)
            for message in compacted
            if getattr(message, "tool_call_id", None)
        }
        ai_tool_ids = {
            tool_call["id"]
            for message in compacted
            for tool_call in (getattr(message, "tool_calls", []) or [])
            if isinstance(tool_call, dict) and "id" in tool_call
        }

        self.assertNotIn("call_1", tool_ids)
        self.assertNotIn("call_1", ai_tool_ids)
        self.assertIn("crawl_1", tool_ids)
        self.assertIn("crawl_1", ai_tool_ids)


class FacultyCrawlerAgentMiddlewareTests(unittest.TestCase):
    def test_build_trace_event_truncates_large_chunk_content(self) -> None:
        event = {"data": {"tools": {"messages": [{"content": "x" * 2000}]}}}
        trace = build_trace_event(event)
        self.assertNotIn("x" * 1500, str(trace))
        self.assertIn("chunk 内容已截断", str(trace))

    def test_controlled_tool_names_include_chunk_tools(self) -> None:
        self.assertIn("claim_next_page_chunk", CONTROLLED_CRAWLER_TOOL_NAMES)
        self.assertIn("submit_chunk_candidates", CONTROLLED_CRAWLER_TOOL_NAMES)

    def test_save_history_compaction_middleware_overrides_messages(self) -> None:
        original_messages = [
            HumanMessage(content="入口任务"),
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "save_professor_candidates",
                        "args": {"candidates": [{"name": "张三"}]},
                        "id": "call_1",
                    }
                ],
            ),
            ToolMessage(
                content='{"batch_status":"saved","attempted_count":1,"saved_count":1,"failed_count":0,"failed_items":[],"total_saved_count":1}',
                tool_call_id="call_1",
            ),
        ]
        captured: dict[str, object] = {}

        class Request:
            messages = original_messages
            tools: list[object] = []

            def override(self, **kwargs: object) -> object:
                captured.update(kwargs)
                return SimpleNamespace(
                    messages=kwargs.get("messages", self.messages),
                    tools=self.tools,
                )

        def handler(request: object) -> object:
            return request.messages

        result = SaveHistoryCompactionMiddleware().wrap_model_call(Request(), handler)

        self.assertEqual(result, captured["messages"])
        assert isinstance(result, list)
        self.assertEqual(len(result), 2)
        self.assertIn("1", result[1].content)


class FacultyCrawlerAgentModelTests(unittest.TestCase):
    def test_crawler_model_passes_extra_body_when_provided(self) -> None:
        profile = LLMProfile(
            name="acme",
            provider="openai",
            api_base_url="https://api.acme.ai/v1",
            api_key="sk-test",
            model_name="acme-think-v1",
        )

        with patch("app.agents.faculty_crawler_agent.ChatOpenAI") as chat_openai:
            build_faculty_crawler_model(
                profile,
                extra_body={"thinking": {"type": "disabled"}},
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

        with patch("app.agents.faculty_crawler_agent.ChatOpenAI") as chat_openai:
            build_faculty_crawler_model(profile, extra_body=None)

        self.assertNotIn("extra_body", chat_openai.call_args.kwargs)

    def test_crawler_model_no_longer_relies_on_is_deepseek_profile(self) -> None:
        # Even an unmistakeably DeepSeek profile must not implicitly enable any extra_body
        # when the caller did not pass one. Adaptation now happens upstream.
        profile = LLMProfile(
            name="DeepSeek",
            provider="deepseek",
            api_base_url="https://api.deepseek.com/v1",
            api_key="sk-test",
            model_name="deepseek-chat",
        )

        with patch("app.agents.faculty_crawler_agent.ChatOpenAI") as chat_openai:
            build_faculty_crawler_model(profile)

        self.assertNotIn("extra_body", chat_openai.call_args.kwargs)


if __name__ == "__main__":
    unittest.main()
