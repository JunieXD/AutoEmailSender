from __future__ import annotations

import unittest

from app.models import IdentityProfile, Professor
from app.services.outreach_templates import build_template_context
from app.services.template_draft_rewrite import (
    DRAFT_RESEARCH_PERSONALIZATION_ERROR,
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
        self.assertEqual(document.blocks[0].rewrite_text, "[[P1]]年[[P2]]月[[P3]]日")

    def test_build_draft_rewrite_document_protects_literal_dates_and_times(self) -> None:
        document = build_draft_rewrite_document(
            "<p>计划于2026年5月21日 09:30联系，参考2024-2025年的经历。</p>",
            {},
        )

        self.assertEqual(
            document.blocks[0].rewrite_text,
            "计划于[[P1]] [[P2]]联系，参考[[P3]]年的经历。",
        )
        self.assertEqual(
            [token.value for token in document.protected_tokens],
            ["2026年5月21日", "09:30", "2024-2025"],
        )

    def test_build_draft_rewrite_document_excludes_paragraphs_inside_table(self) -> None:
        document = build_draft_rewrite_document(
            "<p>正文。</p><table><tr><td><p>表格内部。</p></td></tr></table><p>结尾。</p>",
            {"research_direction": "Agent"},
        )

        self.assertEqual(
            [(block.segment_id, block.type, block.text) for block in document.blocks],
            [
                ("seg_1", "paragraph", "正文。"),
                ("seg_2", "table", "表格内部。"),
                ("seg_3", "paragraph", "结尾。"),
            ],
        )

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
                "<p>李老师，您好：<u>欢迎</u></p>"
                '<table style="border-collapse:collapse"><tbody><tr><td>原表格</td></tr></tbody></table>'
            ),
            build_template_context(identity, professor),
        )

        result = apply_draft_rewrite_replacements(
            document,
            [
                {
                    "segment_id": "seg_1",
                    "text": "李老师，您好：[[S1]]欢迎[[/S1]]了解[[F1]]方向。",
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
                    "segment_id": "seg_2",
                    "text": "改写后的正文，关注[[F1]]方向。",
                },
            ],
        )

        self.assertIn("尊敬的王俊杰教授：", result.text)
        self.assertIn("改写后的正文，关注Agent方向。", result.text)
        self.assertIn("研一", result.html)
        self.assertNotIn("王教授", result.text)
        self.assertIn("Agent方向", result.html)

    def test_apply_replacements_preserves_font_region_and_block_style(self) -> None:
        document = build_draft_rewrite_document(
            (
                '<p style="font-family:宋体;font-size:12pt;line-height:1.5">'
                '我是学生，<span style="font-family:楷体;font-size:12pt">（学校说明）</span>'
                '<strong>成绩优秀</strong>。</p>'
            ),
            {"research_direction": "Agent"},
        )
        self.assertEqual(len(document.blocks[0].style_regions), 2)

        result = apply_draft_rewrite_replacements(
            document,
            [
                {
                    "segment_id": "seg_1",
                    "text": (
                        "我是学生，[[S1]]（学校背景）[[/S1]]"
                        "并且[[S2]]专业排名第一[[/S2]]，希望研究[[F1]]方向。"
                    ),
                },
            ],
        )

        self.assertIn("font-family:楷体", result.html)
        self.assertIn("<strong>", result.html)
        self.assertIn("line-height:1.5", result.html)
        self.assertNotIn("[[S", result.html)
        self.assertNotIn("[[F", result.html)

    def test_apply_replacements_preserves_all_supported_inline_styles(self) -> None:
        document = build_draft_rewrite_document(
            (
                '<p style="font-family:Arial;font-size:12pt;color:#111">普通'
                "<u>下划线</u><em>斜体</em>"
                '<a href="https://example.com/profile">链接</a>'
                '<span style="font-family:KaiTi;font-size:14pt;color:#f00">彩色</span>'
                "</p>"
            ),
            {"research_direction": "Agent"},
        )
        self.assertEqual(len(document.blocks[0].style_regions), 4)

        result = apply_draft_rewrite_replacements(
            document,
            [
                {
                    "segment_id": "seg_1",
                    "text": (
                        "普通[[S1]]新下划线[[/S1]][[S2]]新斜体[[/S2]]"
                        "[[S3]]新链接[[/S3]][[S4]]新彩色[[/S4]]，研究[[F1]]。"
                    ),
                },
            ],
        )

        self.assertIn("<u>", result.html)
        self.assertIn("<em>", result.html)
        self.assertIn('href="https://example.com/profile"', result.html)
        self.assertIn("font-family:KaiTi", result.html)
        self.assertIn("font-size:14pt", result.html)
        self.assertIn("color:#f00", result.html)
        self.assertIn("Agent", result.text)

    def test_apply_replacements_rejects_missing_style_region(self) -> None:
        document = build_draft_rewrite_document(
            "<p>普通文本<strong>重点</strong>。</p>",
            {"research_direction": "Agent"},
        )

        with self.assertRaisesRegex(ValueError, "样式区域"):
            apply_draft_rewrite_replacements(
                document,
                [{"segment_id": "seg_1", "text": "普通文本重点，关注[[F1]]。"}],
            )

    def test_apply_replacements_does_not_duplicate_fact_already_in_table(self) -> None:
        document = build_draft_rewrite_document(
            "<table><tr><td>{{research_direction}}</td></tr></table><p>正文。</p>",
            {"research_direction": "Agent"},
        )

        result = apply_draft_rewrite_replacements(
            document,
            [{"segment_id": "seg_2", "text": "改写后的正文。"}],
        )

        self.assertEqual(result.text.count("Agent"), 1)
        self.assertNotIn("[[F1]]", result.html)

    def test_apply_replacements_uses_friendly_research_direction_error(self) -> None:
        document = build_draft_rewrite_document(
            "<p>正文。</p>",
            {"research_direction": "Agent"},
        )

        for text in ("没有结合导师方向。", "重复[[F1]]与[[F1]]。"):
            with self.subTest(text=text):
                with self.assertRaises(ValueError) as raised:
                    apply_draft_rewrite_replacements(
                        document,
                        [{"segment_id": "seg_1", "text": text}],
                    )
                self.assertEqual(
                    str(raised.exception),
                    DRAFT_RESEARCH_PERSONALIZATION_ERROR,
                )
                self.assertNotIn("令牌", str(raised.exception))

    def test_apply_replacements_preserves_existing_literal_research_direction_once(self) -> None:
        document = build_draft_rewrite_document(
            "<p>我关注您在 Agent 方向的工作。</p>",
            {"research_direction": "Agent"},
        )

        result = apply_draft_rewrite_replacements(
            document,
            [
                {
                    "segment_id": "seg_1",
                    "text": "我认真了解了您在 Agent 方向的工作。",
                },
            ],
        )

        self.assertEqual(result.text.count("Agent"), 1)


if __name__ == "__main__":
    unittest.main()
