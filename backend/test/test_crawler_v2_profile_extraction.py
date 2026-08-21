from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, patch

from app.modules.crawler.v2.profile_extraction import (
    V2ProfileExtractionPayload,
    build_v2_profile_extraction_prompt,
    invoke_v2_profile_extraction_agent,
)
from app.modules.crawler.llm.structured_output import (
    ProfessorCandidateWirePayload,
    V2ProfileExtractionWirePayload,
)
from app.modules.llm.runtime import (
    ChatCompletionResult,
    ChatCompletionUsage,
    LLMRuntimeAdaptation,
    LLMRuntimeError,
)


def _empty_candidate_wire() -> ProfessorCandidateWirePayload:
    return ProfessorCandidateWirePayload(
        name="",
        email="",
        title="",
        university="",
        school="",
        department="",
        research_direction="",
        recent_papers=[],
        profile_url="",
        source_url="",
        confidence=0,
        field_confidence=[],
        evidence_summary="",
    )


class CrawlerV2ProfileExtractionTests(unittest.IsolatedAsyncioTestCase):
    async def test_prompt_contains_university_school_url_and_whole_page_text(
        self,
    ) -> None:
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

    async def test_invoke_does_not_issue_repair_call_for_invalid_output(self) -> None:
        llm_profile = object()
        adaptation = LLMRuntimeAdaptation(
            "responses",
            {"thinking": {"type": "disabled"}},
        )

        with patch(
            "app.modules.crawler.v2.profile_extraction.request_crawler_structured_completion",
            new=AsyncMock(side_effect=LLMRuntimeError("模型返回的 JSON 结构无效")),
        ) as request_mock:
            with self.assertRaisesRegex(LLMRuntimeError, "JSON 结构无效"):
                await invoke_v2_profile_extraction_agent(
                    llm_profile,
                    session_factory=object(),  # type: ignore[arg-type]
                    university="示例大学",
                    school="计算机学院",
                    source_url="https://example.edu/teacher/zhang.html",
                    title="张三",
                    page_text="张三 教授",
                    adaptation=adaptation,
                )

        request_mock.assert_awaited_once()

    async def test_invoke_uses_shared_structured_output_request(self) -> None:
        completion = ChatCompletionResult(
            content='{"status":"no_candidate"}',
            usage=ChatCompletionUsage(
                prompt_tokens=1,
                completion_tokens=1,
                total_tokens=2,
                cached_tokens=0,
            ),
        )
        wire_payload = V2ProfileExtractionWirePayload(
            status="no_candidate",
            candidate=_empty_candidate_wire(),
        )
        llm_profile = object()
        adaptation = LLMRuntimeAdaptation("chat_completions", None)
        session_factory = object()

        with patch(
            "app.modules.crawler.v2.profile_extraction.request_crawler_structured_completion",
            new=AsyncMock(
                return_value=(completion, wire_payload, "json_schema_strict")
            ),
        ) as invoke_mock:
            result = await invoke_v2_profile_extraction_agent(
                llm_profile,
                session_factory=session_factory,  # type: ignore[arg-type]
                university="示例大学",
                school="计算机学院",
                source_url="https://example.edu/teacher/zhang.html",
                title="张三",
                page_text="张三 教授",
                adaptation=adaptation,
            )

        self.assertEqual(result.payload["status"], "no_candidate")
        invoke_mock.assert_awaited_once()
        self.assertIs(invoke_mock.await_args.args[0], session_factory)
        self.assertIs(invoke_mock.await_args.args[1], llm_profile)
        self.assertIs(invoke_mock.await_args.args[2], adaptation)
        self.assertIs(
            invoke_mock.await_args.kwargs["result_model"],
            V2ProfileExtractionWirePayload,
        )
        self.assertEqual(result.usage["input_tokens"], 1)
        self.assertEqual(len(result.attempts), 1)

    async def test_payload_accepts_no_candidate(self) -> None:
        payload = V2ProfileExtractionPayload.model_validate(
            {"status": "no_candidate", "candidate": None}
        )

        self.assertEqual(payload.status, "no_candidate")
        self.assertIsNone(payload.candidate)


if __name__ == "__main__":
    unittest.main()
