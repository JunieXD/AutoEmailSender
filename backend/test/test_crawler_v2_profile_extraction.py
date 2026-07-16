from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.services.crawler_v2_profile_extraction import (
    V2ProfileExtractionPayload,
    build_v2_profile_extraction_prompt,
    invoke_v2_profile_extraction_agent,
)
from app.services.llm_runtime import LLMRuntimeAdaptation


class CrawlerV2ProfileExtractionTests(unittest.IsolatedAsyncioTestCase):
    async def test_prompt_contains_university_school_url_and_whole_page_text(self) -> None:
        prompt = build_v2_profile_extraction_prompt(
            university="示例大学",
            school="计算机学院",
            source_url="https://example.edu/teacher/zhang.html",
            title="张三",
            page_text="张三 教授 邮箱 zhang@example.edu",
            page_html_excerpt="<h1>张三</h1>",
        )

        self.assertIn("示例大学", prompt)
        self.assertIn("计算机学院", prompt)
        self.assertIn("https://example.edu/teacher/zhang.html", prompt)
        self.assertIn("张三 教授", prompt)
        self.assertIn('"status"', prompt)

    async def test_invoke_retries_invalid_json_and_accumulates_usage(self) -> None:
        responses = [
            SimpleNamespace(content="不是 JSON", usage_metadata={"input_tokens": 10, "output_tokens": 2, "cached_tokens": 1, "total_tokens": 12}),
            SimpleNamespace(content='{"status":"candidate","candidate":{"name":"张三","profile_url":"","source_url":""}}', usage_metadata={"input_tokens": 11, "output_tokens": 3, "cached_tokens": 2, "total_tokens": 14}),
        ]
        model = SimpleNamespace(ainvoke=AsyncMock(side_effect=responses))
        llm_profile = SimpleNamespace(model_name="test-model")

        with patch("app.services.crawler_v2_profile_extraction.build_faculty_crawler_model", return_value=model):
            result = await invoke_v2_profile_extraction_agent(
                llm_profile,
                university="示例大学",
                school="计算机学院",
                source_url="https://example.edu/teacher/zhang.html",
                title="张三",
                page_text="张三 教授",
                page_html_excerpt="<h1>张三</h1>",
                adaptation=LLMRuntimeAdaptation(
                    "responses",
                    {"thinking": {"type": "disabled"}},
                ),
            )

        self.assertEqual(result.payload["status"], "candidate")
        self.assertEqual(len(result.attempts), 2)
        self.assertEqual(result.usage["input_tokens"], 21)
        self.assertEqual(result.usage["output_tokens"], 5)
        self.assertEqual(result.usage["cached_tokens"], 3)
        self.assertEqual(result.attempts[0].error is not None, True)

    async def test_payload_accepts_no_candidate(self) -> None:
        payload = V2ProfileExtractionPayload.model_validate({"status": "no_candidate", "candidate": None})

        self.assertEqual(payload.status, "no_candidate")
        self.assertIsNone(payload.candidate)


if __name__ == "__main__":
    unittest.main()
