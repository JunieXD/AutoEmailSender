import unittest

from app.services.rich_text import normalize_email_html, render_rich_text_document


class RichTextRenderingTest(unittest.TestCase):
    def test_renders_rich_text_json_to_safe_html_and_text(self) -> None:
        result = render_rich_text_document(
            {
                "type": "doc",
                "blocks": [
                    {
                        "type": "paragraph",
                        "children": [
                            {"type": "text", "text": "王老师您好，"},
                            {
                                "type": "strong",
                                "children": [
                                    {"type": "text", "text": "我很关注您的研究"}
                                ],
                            },
                        ],
                    },
                    {
                        "type": "bullet_list",
                        "items": [
                            [{"type": "text", "text": "信息抽取方向"}],
                            [
                                {
                                    "type": "emphasis",
                                    "children": [
                                        {"type": "text", "text": "医学 NLP 应用"}
                                    ],
                                }
                            ],
                        ],
                    },
                ],
            }
        )

        self.assertIn(
            "<p>王老师您好，<strong>我很关注您的研究</strong></p>",
            result.html,
        )
        self.assertIn("<ul>", result.html)
        self.assertEqual(
            result.text,
            "王老师您好，我很关注您的研究\n- 信息抽取方向\n- 医学 NLP 应用",
        )

    def test_rejects_unsafe_link_protocol(self) -> None:
        with self.assertRaisesRegex(ValueError, "不支持的链接协议"):
            render_rich_text_document(
                {
                    "type": "doc",
                    "blocks": [
                        {
                            "type": "paragraph",
                            "children": [
                                {
                                    "type": "link",
                                    "href": "javascript:alert(1)",
                                    "children": [
                                        {"type": "text", "text": "危险链接"}
                                    ],
                                }
                            ],
                        }
                    ],
                }
            )

    def test_ignores_empty_list_blocks_when_visible_text_exists(self) -> None:
        result = render_rich_text_document(
            {
                "type": "doc",
                "blocks": [
                    {
                        "type": "paragraph",
                        "children": [{"type": "text", "text": "李老师您好："}],
                    },
                    {
                        "type": "bullet_list",
                        "items": [],
                    },
                    {
                        "type": "paragraph",
                        "children": [{"type": "text", "text": "期待与您交流。"}],
                    },
                ],
            }
        )

        self.assertNotIn("<ul>", result.html)
        self.assertEqual(result.text, "李老师您好：\n期待与您交流。")

    def test_preserves_table_and_font_styles_in_email_html(self) -> None:
        result = normalize_email_html(
            '<table style="font-family:SimSun;border-collapse:collapse"><tbody><tr><td style="font-family:SimSun">老师您好</td></tr></tbody></table>'
        )

        self.assertIn("<table", result.html)
        self.assertIn("<tbody>", result.html)
        self.assertIn("<tr>", result.html)
        self.assertIn("<td", result.html)
        self.assertIn(
            'style="font-family:SimSun;border-collapse:collapse"',
            result.html,
        )
        self.assertIn('style="font-family:SimSun"', result.html)
        self.assertIn("老师您好", result.text)

    def test_html_to_text_keeps_inline_font_spans_in_same_sentence(self) -> None:
        result = normalize_email_html(
            '<p style="font-family:宋体">我是<span style="font-family:黑体">【江西财经大学计算机与人工智能学院】</span>的学生。</p>'
        )

        self.assertEqual(result.text, "我是【江西财经大学计算机与人工智能学院】的学生。")

    def test_html_to_text_preserves_authored_inline_spacing(self) -> None:
        result = normalize_email_html("<p>Hello <span>world</span>.</p>")

        self.assertEqual(result.text, "Hello world.")

    def test_html_to_text_keeps_break_between_inline_runs(self) -> None:
        result = normalize_email_html("<p>Hello<br>world.</p>")

        self.assertEqual(result.text, "Hello world.")
