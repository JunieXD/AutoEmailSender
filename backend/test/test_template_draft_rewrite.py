from __future__ import annotations

import unittest

from app.models import IdentityProfile, Professor
from app.services.outreach_templates import build_template_context
from app.services.template_draft_rewrite import (
    apply_draft_rewrite_replacements,
    build_draft_rewrite_document,
    select_dominant_font_and_size,
)


class TemplateDraftRewriteTests(unittest.TestCase):
    def test_build_draft_rewrite_document_extracts_blocks_and_style_spans(self) -> None:
        identity = IdentityProfile(
            id=1,
            name="张三",
            profile_name="张三",
            sender_name="张三",
            email_address="sender@example.com",
            smtp_host="smtp.example.com",
            smtp_port=465,
            smtp_username="sender@example.com",
            smtp_password="secret",
            default_language="zh-CN",
            outreach_generation_mode="llm",
        )
        professor = Professor(
            id=1,
            name="李老师",
            email="prof@example.edu",
            research_direction="Information Extraction",
        )

        html = (
            '<p><strong>{{name}}</strong>老师，您好，<u>欢迎</u>您。</p>'
            '<table><tbody><tr><td>原表格</td></tr></tbody></table>'
        )

        document = build_draft_rewrite_document(html, build_template_context(identity, professor))

        self.assertEqual(len(document.blocks), 2)
        self.assertEqual(document.blocks[0].segment_id, "seg_1")
        self.assertEqual(document.blocks[0].type, "paragraph")
        self.assertEqual(document.blocks[0].text, "李老师老师，您好，欢迎您。")
        self.assertEqual(
            [
                {"text": span.text, "marks": span.marks}
                for span in document.blocks[0].style_spans
            ],
            [
                {"text": "李老师", "marks": ["strong"]},
                {"text": "欢迎", "marks": ["underline"]},
            ],
        )

    def test_build_draft_rewrite_document_keeps_table_fragment_untouched(self) -> None:
        identity = IdentityProfile(
            id=1,
            name="张三",
            profile_name="张三",
            sender_name="张三",
            email_address="sender@example.com",
            smtp_host="smtp.example.com",
            smtp_port=465,
            smtp_username="sender@example.com",
            smtp_password="secret",
            default_language="zh-CN",
            outreach_generation_mode="llm",
        )
        professor = Professor(
            id=1,
            name="李老师",
            email="prof@example.edu",
            research_direction="Information Extraction",
        )

        html = (
            "<p>李老师，您好。</p>"
            '<table style="border-collapse:collapse"><tbody><tr><td>原表格</td></tr></tbody></table>'
        )

        document = build_draft_rewrite_document(html, build_template_context(identity, professor))

        self.assertEqual(document.blocks[1].type, "table")
        self.assertEqual(
            document.blocks[1].html_fragment,
            '<table style="border-collapse:collapse"><tbody><tr><td>原表格</td></tr></tbody></table>',
        )

    def test_build_draft_rewrite_document_locks_salutation_block(self) -> None:
        identity = IdentityProfile(
            id=1,
            name="张三",
            profile_name="张三",
            sender_name="张三",
            email_address="sender@example.com",
            smtp_host="smtp.example.com",
            smtp_port=465,
            smtp_username="sender@example.com",
            smtp_password="secret",
            default_language="zh-CN",
            outreach_generation_mode="llm",
        )
        professor = Professor(
            id=1,
            name="王俊杰",
            email="prof@example.edu",
            research_direction="Agent",
        )

        html = (
            "<p>尊敬的{{name}}教授：</p>"
            "<p>正文内容。</p>"
        )

        document = build_draft_rewrite_document(html, build_template_context(identity, professor))

        self.assertTrue(document.blocks[0].locked)
        self.assertFalse(document.blocks[1].locked)

    def test_build_draft_rewrite_document_preserves_send_date_tokens(self) -> None:
        identity = IdentityProfile(
            id=1,
            name="张三",
            profile_name="张三",
            sender_name="张三",
            email_address="sender@example.com",
            smtp_host="smtp.example.com",
            smtp_port=465,
            smtp_username="sender@example.com",
            smtp_password="secret",
            default_language="zh-CN",
            outreach_generation_mode="llm",
        )
        professor = Professor(
            id=1,
            name="李老师",
            email="prof@example.edu",
            research_direction="Agent",
        )

        document = build_draft_rewrite_document(
            "<p>{{year}}年{{month}}月{{day}}日</p>",
            build_template_context(identity, professor),
        )

        self.assertEqual(document.blocks[0].text, "{{year}}年{{month}}月{{day}}日")

    def test_select_dominant_font_and_size_uses_visible_char_count(self) -> None:
        html = (
            '<p style="font-family:SimSun;font-size:12pt">短句。</p>'
            '<p style="font-family:Arial;font-size:14pt">这是一段明显更长的正文文本。</p>'
        )

        style = select_dominant_font_and_size(html)

        self.assertEqual(style.font_family, "Arial")
        self.assertEqual(style.font_size, "14pt")

    def test_select_dominant_font_and_size_prefers_chinese_family_in_mixed_stack(self) -> None:
        html = (
            '<p style="font-family:\'Times New Roman\',\'宋体\',SimSun,serif;font-size:12pt">'
            "这是一段更长的中文正文文本，用来确认中文主字体不会被误判成英文衬线字体。"
            "</p>"
            '<p style="font-family:Arial;font-size:14pt">Short</p>'
        )

        style = select_dominant_font_and_size(html)

        self.assertEqual(style.font_family, "宋体")
        self.assertEqual(style.font_size, "12pt")

    def test_select_dominant_font_and_size_prefers_mso_fareast_family_for_chinese_text(self) -> None:
        html = (
            '<p style="font-family:\'Times New Roman\';mso-fareast-font-family:宋体;font-size:12pt">'
            "这是一段来自 Word 模板的中文正文文本，用来确认改写后仍然沿用中文字体。"
            "</p>"
            '<p style="font-family:Arial;font-size:14pt">Short</p>'
        )

        style = select_dominant_font_and_size(html)

        self.assertEqual(style.font_family, "宋体")
        self.assertEqual(style.font_size, "12pt")

    def test_select_dominant_font_and_size_trusts_explicit_word_fareast_family(self) -> None:
        for font_family in ("等线 Light", "微软雅黑"):
            with self.subTest(font_family=font_family):
                html = (
                    '<p style="font-family:\'Times New Roman\';'
                    f'mso-fareast-font-family:\'{font_family}\';font-size:12pt">'
                    "这是一段来自 Word 模板的中文正文。"
                    "</p>"
                )

                style = select_dominant_font_and_size(html)

                self.assertEqual(style.font_family, font_family)
                self.assertEqual(style.font_size, "12pt")

    def test_apply_draft_rewrite_replacements_renders_runs_and_keeps_table(self) -> None:
        identity = IdentityProfile(
            id=1,
            name="张三",
            profile_name="张三",
            sender_name="张三",
            email_address="sender@example.com",
            smtp_host="smtp.example.com",
            smtp_port=465,
            smtp_username="sender@example.com",
            smtp_password="secret",
            default_language="zh-CN",
            outreach_generation_mode="llm",
        )
        professor = Professor(
            id=1,
            name="李老师",
            email="prof@example.edu",
            research_direction="Information Extraction",
        )

        document = build_draft_rewrite_document(
            (
                "<p>李老师，您好：</p>"
                '<table style="border-collapse:collapse"><tbody><tr><td>原表格</td></tr></tbody></table>'
            ),
            build_template_context(identity, professor),
        )

        result = apply_draft_rewrite_replacements(
            document,
            [
                {
                    "segment_id": "seg_1",
                    "runs": [
                        {"text": "李老师，您好："},
                        {"text": "欢迎", "marks": ["underline"]},
                    ],
                }
            ],
        )

        self.assertIn("<u>欢迎</u>", result.html)
        self.assertIn("<table", result.html)
        self.assertIn("原表格", result.text)

    def test_apply_draft_rewrite_replacements_restores_locked_salutation_and_table(self) -> None:
        identity = IdentityProfile(
            id=1,
            name="张三",
            profile_name="张三",
            sender_name="张三",
            email_address="sender@example.com",
            smtp_host="smtp.example.com",
            smtp_port=465,
            smtp_username="sender@example.com",
            smtp_password="secret",
            default_language="zh-CN",
            outreach_generation_mode="llm",
        )
        professor = Professor(
            id=1,
            name="王俊杰",
            email="prof@example.edu",
            research_direction="Agent",
        )

        document = build_draft_rewrite_document(
            (
                "<p>尊敬的{{name}}教授：</p>"
                "<p>正文内容。</p>"
                '<table style="border-collapse:collapse"><tbody><tr><td>研一</td></tr></tbody></table>'
            ),
            build_template_context(identity, professor),
        )

        result = apply_draft_rewrite_replacements(
            document,
            [
                {
                    "segment_id": "seg_1",
                    "runs": [{"text": "尊敬的王教授："}],
                },
                {
                    "segment_id": "seg_2",
                    "runs": [{"text": "改写后的正文。"}],
                },
                {
                    "segment_id": "seg_3",
                    "runs": [{"text": "研一（Agent方向）"}],
                },
            ],
        )

        self.assertIn("尊敬的王俊杰教授：", result.text)
        self.assertIn("改写后的正文。", result.text)
        self.assertIn("研一", result.html)
        self.assertNotIn("王教授", result.text)
        self.assertNotIn("Agent方向", result.html)

    def test_apply_draft_rewrite_replacements_keeps_mso_fareast_font_for_rewritten_text(self) -> None:
        identity = IdentityProfile(
            id=1,
            name="张三",
            profile_name="张三",
            sender_name="张三",
            email_address="sender@example.com",
            smtp_host="smtp.example.com",
            smtp_port=465,
            smtp_username="sender@example.com",
            smtp_password="secret",
            default_language="zh-CN",
            outreach_generation_mode="llm",
        )
        professor = Professor(
            id=1,
            name="李老师",
            email="prof@example.edu",
            research_direction="Agent",
        )
        document = build_draft_rewrite_document(
            (
                '<p style="font-family:\'Times New Roman\';mso-fareast-font-family:宋体;font-size:12pt">'
                "原正文内容。"
                "</p>"
            ),
            build_template_context(identity, professor),
        )

        result = apply_draft_rewrite_replacements(
            document,
            [
                {
                    "segment_id": "seg_1",
                    "runs": [{"text": "改写后的中文正文。"}],
                }
            ],
        )

        self.assertIn("改写后的中文正文。", result.text)
        self.assertIn("font-family:宋体", result.html)
        self.assertIn("font-size:12pt", result.html)

    def test_apply_draft_rewrite_replacements_keeps_block_base_font_when_local_font_is_longer(self) -> None:
        identity = IdentityProfile(
            id=1,
            name="张三",
            profile_name="张三",
            sender_name="张三",
            email_address="sender@example.com",
            smtp_host="smtp.example.com",
            smtp_port=465,
            smtp_username="sender@example.com",
            smtp_password="secret",
            default_language="zh-CN",
            outreach_generation_mode="llm",
        )
        professor = Professor(
            id=1,
            name="李老师",
            email="prof@example.edu",
            research_direction="Agent",
        )
        document = build_draft_rewrite_document(
            (
                '<p style="font-family:宋体;font-size:12pt">'
                '我是<span style="font-family:黑体">【江西财经大学计算机与人工智能学院】</span>的学生。'
                "</p>"
            ),
            build_template_context(identity, professor),
        )

        result = apply_draft_rewrite_replacements(
            document,
            [
                {
                    "segment_id": "seg_1",
                    "runs": [
                        {
                            "text": "我是【江西财经大学计算机与人工智能学院】的学生，关注您的 Agent 研究。"
                        }
                    ],
                }
            ],
        )

        self.assertIn('style="font-family:宋体;font-size:12pt"', result.html)
        self.assertIn('<span style="font-family:黑体">【江西财经大学计算机与人工智能学院】</span>', result.html)
        self.assertNotIn('style="font-family:黑体;font-size:12pt"', result.html)

    def test_apply_draft_rewrite_replacements_infers_base_font_from_matching_edges(self) -> None:
        identity = IdentityProfile(
            id=1,
            name="张三",
            profile_name="张三",
            sender_name="张三",
            email_address="sender@example.com",
            smtp_host="smtp.example.com",
            smtp_port=465,
            smtp_username="sender@example.com",
            smtp_password="secret",
            default_language="zh-CN",
            outreach_generation_mode="llm",
        )
        professor = Professor(
            id=1,
            name="李老师",
            email="prof@example.edu",
            research_direction="Agent",
        )
        document = build_draft_rewrite_document(
            (
                "<p>"
                '<span style="font-family:宋体;font-size:12pt">我是</span>'
                '<span style="font-family:黑体;font-size:12pt">【江西财经大学计算机与人工智能学院】</span>'
                '<span style="font-family:宋体;font-size:12pt">的学生。</span>'
                "</p>"
            ),
            build_template_context(identity, professor),
        )

        result = apply_draft_rewrite_replacements(
            document,
            [
                {
                    "segment_id": "seg_1",
                    "runs": [
                        {
                            "text": "我是【江西财经大学计算机与人工智能学院】的学生，关注您的 Agent 研究。"
                        }
                    ],
                }
            ],
        )

        self.assertIn('style="font-family:宋体;font-size:12pt"', result.html)
        self.assertIn('<span style="font-family:黑体">【江西财经大学计算机与人工智能学院】</span>', result.html)

    def test_apply_draft_rewrite_replacements_fills_partial_block_font_from_inline_style(self) -> None:
        cases = (
            (
                '<p style="font-size:12pt"><span style="font-family:宋体">原正文。</span></p>',
                '<p style="font-family:宋体;font-size:12pt">改写后的正文。</p>',
            ),
            (
                '<p style="font-family:宋体"><span style="font-size:12pt">原正文。</span></p>',
                '<p style="font-family:宋体;font-size:12pt">改写后的正文。</p>',
            ),
        )

        for source_html, expected_html in cases:
            with self.subTest(source_html=source_html):
                document = build_draft_rewrite_document(source_html, {})

                result = apply_draft_rewrite_replacements(
                    document,
                    [{"segment_id": "seg_1", "runs": [{"text": "改写后的正文。"}]}],
                )

                self.assertEqual(result.html, expected_html)

    def test_apply_draft_rewrite_replacements_keeps_per_paragraph_base_font(self) -> None:
        identity = IdentityProfile(
            id=1,
            name="张三",
            profile_name="张三",
            sender_name="张三",
            email_address="sender@example.com",
            smtp_host="smtp.example.com",
            smtp_port=465,
            smtp_username="sender@example.com",
            smtp_password="secret",
            default_language="zh-CN",
            outreach_generation_mode="llm",
        )
        professor = Professor(
            id=1,
            name="李老师",
            email="prof@example.edu",
            research_direction="Agent",
        )
        document = build_draft_rewrite_document(
            (
                '<p style="font-family:宋体;font-size:12pt">第一段。</p>'
                '<p style="font-family:楷体;font-size:11pt">第二段。</p>'
            ),
            build_template_context(identity, professor),
        )

        result = apply_draft_rewrite_replacements(
            document,
            [
                {"segment_id": "seg_1", "runs": [{"text": "改写后的第一段。"}]},
                {"segment_id": "seg_2", "runs": [{"text": "改写后的第二段。"}]},
            ],
        )

        self.assertIn('<p style="font-family:宋体;font-size:12pt">改写后的第一段。</p>', result.html)
        self.assertIn('<p style="font-family:楷体;font-size:11pt">改写后的第二段。</p>', result.html)


if __name__ == "__main__":
    unittest.main()
