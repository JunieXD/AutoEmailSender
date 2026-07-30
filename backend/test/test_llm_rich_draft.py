import unittest
from unittest.mock import AsyncMock, patch

from app.models import IdentityProfile, LLMProfile, Professor
from app.services.llm_runtime import (
    ChatCompletionResult,
    DraftBodyBlockWire,
    DraftBodyItemWire,
    DraftBodyRunWire,
    DraftGenerationResult,
    DraftGenerationWireResult,
    _draft_generation_wire_to_result,
    generate_draft_content,
    parse_structured_result,
)


class LLMRichDraftTest(unittest.TestCase):
    def test_draft_wire_renders_deterministic_styles_and_lists(self) -> None:
        result = _draft_generation_wire_to_result(
            DraftGenerationWireResult(
                subject="申请交流",
                blocks=[
                    DraftBodyBlockWire(
                        type="paragraph",
                        items=[
                            DraftBodyItemWire(
                                runs=[
                                    DraftBodyRunWire(
                                        text="重点内容",
                                        strong=True,
                                        emphasis=True,
                                        href="https://example.edu/profile",
                                        line_break_after=False,
                                    )
                                ]
                            )
                        ],
                    ),
                    DraftBodyBlockWire(
                        type="bullet_list",
                        items=[
                            DraftBodyItemWire(
                                runs=[
                                    DraftBodyRunWire(
                                        text="研究经历",
                                        strong=False,
                                        emphasis=False,
                                        href="",
                                        line_break_after=False,
                                    )
                                ]
                            )
                        ],
                    ),
                ],
            )
        )

        self.assertEqual(result.subject, "申请交流")
        self.assertIn("<strong><em><a href=", result.body_html)
        self.assertIn("<ul><li>研究经历</li></ul>", result.body_html)

    def test_draft_generation_parses_rich_body_json(self) -> None:
        result = parse_structured_result(
            """
            {
              "subject": "申请交流科研方向",
              "rich_body": {
                "type": "doc",
                "blocks": [
                  {
                    "type": "paragraph",
                    "children": [
                      {"type": "text", "text": "王老师您好，"},
                      {
                        "type": "strong",
                        "children": [{"type": "text", "text": "我很关注您的工作"}]
                      }
                    ]
                  }
                ]
              }
            }
            """,
            DraftGenerationResult,
        )

        self.assertEqual(result.body_text, "王老师您好，我很关注您的工作")
        self.assertEqual(
            result.body_html,
            "<p>王老师您好，<strong>我很关注您的工作</strong></p>",
        )


class LLMRichDraftAsyncTest(unittest.IsolatedAsyncioTestCase):
    async def test_no_template_draft_uses_structured_wire_result(self) -> None:
        identity = IdentityProfile(
            name="张三",
            email_address="sender@example.com",
            smtp_host="smtp.example.com",
            smtp_port=465,
            smtp_username="sender@example.com",
            smtp_password="secret",
            default_language="zh-CN",
            outreach_generation_mode="llm",
        )
        profile = LLMProfile(
            name="test",
            provider="openai",
            api_key="test-key",
            model_name="test-model",
        )
        professor = Professor(
            name="李老师",
            email="prof@example.edu",
            research_direction="信息抽取",
        )
        wire = DraftGenerationWireResult(
            subject="申请交流",
            blocks=[
                DraftBodyBlockWire(
                    type="paragraph",
                    items=[
                        DraftBodyItemWire(
                            runs=[
                                DraftBodyRunWire(
                                    text="李老师，您好。",
                                    strong=False,
                                    emphasis=False,
                                    href="",
                                    line_break_after=False,
                                )
                            ]
                        )
                    ],
                )
            ],
        )

        with patch(
            "app.services.llm_runtime.request_structured_completion",
            new=AsyncMock(
                return_value=(
                    ChatCompletionResult(content="{}"),
                    wire,
                    "json_schema_strict",
                )
            ),
        ) as request_mock:
            generated = await generate_draft_content(
                identity=identity,
                primary_material=None,
                llm_profile=profile,
                professor=professor,
                available_materials=[],
                session=object(),  # type: ignore[arg-type]
            )

        self.assertEqual(generated.result.subject, "申请交流")
        self.assertEqual(generated.result.body_html, "<p>李老师，您好。</p>")
        self.assertIs(request_mock.await_args.args[2], DraftGenerationWireResult)
