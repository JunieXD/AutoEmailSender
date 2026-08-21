from __future__ import annotations

import struct
import unittest
from unittest.mock import AsyncMock, patch

from app.modules.crawler.pages.tools import CrawlToolContext, PageSnapshot
from app.services.html_text import html_to_text
from app.modules.crawler.runtime.native_ocr import (
    _is_small_horizontal_image,
    extract_ocr_email_evidence,
    image_dimensions,
)
from app.modules.crawler.runtime.profile_fallbacks import (
    extract_email_evidence,
    extract_profile_document_email_evidence,
    extract_profile_link_evidence,
    resolve_profile_image_urls,
)


class CrawlerProfileFallbackTests(unittest.IsolatedAsyncioTestCase):
    def test_profile_document_fallback_reads_visible_fields_after_body(self) -> None:
        snapshot = PageSnapshot(
            url="https://example.edu/zhang/",
            html="""
                <html><body><p>张三的个人简介</p></body>
                <div>姓名：张三</div>
                <div>电子邮件：zhang@example.edu</div>
                <script>hidden@example.edu</script>
                <!-- comment@example.edu -->
                </html>
            """,
            fetch_method="http",
            status="succeeded",
        )

        evidence = extract_profile_document_email_evidence(snapshot)

        self.assertEqual([item.email for item in evidence], ["zhang@example.edu"])
        self.assertEqual(evidence[0].source_kind, "profile_document")
        self.assertIn("姓名：张三", evidence[0].context)
        self.assertNotIn("zhang@example.edu", html_to_text(snapshot.html))

    def test_extracts_every_valid_text_email_with_local_context(self) -> None:
        text = (
            "张三教授的联系方式是 zhang@example.edu，办公室位于主楼。"
            "学院行政事务请联系 office@example.edu，不能视为教师邮箱。"
        )

        evidence = extract_email_evidence(
            text,
            source_url="https://example.edu/zhang",
            source_kind="profile_text",
            context_chars=24,
        )

        self.assertEqual(
            [item.email for item in evidence],
            ["zhang@example.edu", "office@example.edu"],
        )
        self.assertIn("张三教授", evidence[0].context)
        self.assertIn("学院行政", evidence[1].context)

    def test_normalizes_obfuscated_and_spaced_email(self) -> None:
        evidence = extract_email_evidence(
            "Email: alice (at) xmu (dot) edu (dot) cn",
            source_url="https://example.edu/alice",
            source_kind="profile_text",
        )

        self.assertEqual([item.email for item in evidence], ["alice@xmu.edu.cn"])

    def test_link_evidence_contains_only_real_http_links(self) -> None:
        snapshot = PageSnapshot(
            url="https://example.edu/zhang/",
            html="""
                <main>
                  <div>张三的完整资料 <a href="details.html">查看更多</a></div>
                  <a href="mailto:zhang@example.edu">邮件</a>
                  <a href="javascript:void(0)">展开</a>
                  <a href="/news">学院新闻</a>
                </main>
            """,
            fetch_method="http",
            status="succeeded",
        )

        links = extract_profile_link_evidence(snapshot)

        self.assertEqual(links[0].url, "https://example.edu/zhang/details.html")
        self.assertEqual(links[0].label, "查看更多")
        self.assertIn("张三", links[0].context)
        self.assertNotIn("mailto:", {link.url for link in links})
        self.assertNotIn("javascript:", {link.url for link in links})

    def test_embedded_frame_links_use_frame_document_url(self) -> None:
        snapshot = PageSnapshot(
            url="https://example.edu/zhang/",
            html="""
                <section data-crawl-frame-url="https://example.edu/zhang/index.files/sheet001.htm">
                  <p>张三的联系方式 <a href="contact.htm">联系详情</a></p>
                </section>
            """,
            fetch_method="browser",
            status="succeeded",
        )

        links = extract_profile_link_evidence(snapshot)

        self.assertEqual(
            links[0].url,
            "https://example.edu/zhang/index.files/contact.htm",
        )

    def test_image_urls_are_resolved_and_deduplicated(self) -> None:
        snapshot = PageSnapshot(
            url="https://example.edu/zhang/index.html",
            html="""
                <p>邮箱 <img src="images/email.gif" alt="邮箱"></p>
                <img data-src="images/email.gif">
                <img src="data:image/png;base64,AAAA">
            """,
            fetch_method="http",
            status="succeeded",
        )

        self.assertEqual(
            resolve_profile_image_urls(snapshot),
            (("https://example.edu/zhang/images/email.gif", "邮箱 邮箱"),),
        )

    def test_embedded_frame_images_use_frame_document_url(self) -> None:
        snapshot = PageSnapshot(
            url="https://example.edu/zhang/",
            html="""
                <section data-crawl-frame-url="https://example.edu/zhang/index.files/sheet001.htm">
                  <p>个人邮箱</p>
                  <p><img src="image004.png"></p>
                </section>
            """,
            fetch_method="browser",
            status="succeeded",
        )

        self.assertEqual(
            resolve_profile_image_urls(snapshot),
            (("https://example.edu/zhang/index.files/image004.png", "个人邮箱"),),
        )

    def test_reads_common_raster_dimensions_and_filters_large_artwork(self) -> None:
        png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 8 + struct.pack(">II", 155, 20)
        gif = b"GIF89a" + struct.pack("<HH", 159, 25)
        jpeg = bytes.fromhex("ffd8ffc0000b080014009b01011100")

        self.assertEqual(image_dimensions(png), (155, 20))
        self.assertEqual(image_dimensions(gif), (159, 25))
        self.assertEqual(image_dimensions(jpeg), (155, 20))
        self.assertTrue(_is_small_horizontal_image(155, 20))
        self.assertFalse(_is_small_horizontal_image(1200, 900))

    async def test_ocr_is_downloaded_and_executed_again_on_retry(self) -> None:
        snapshot = PageSnapshot(
            url="https://example.edu/zhang/",
            html='<p>邮箱 <img src="email.gif"></p>',
            fetch_method="http",
            status="succeeded",
        )
        ctx = CrawlToolContext(
            job_id=1,
            start_url=snapshot.url,
            university="示例大学",
            school="计算机学院",
            session_factory=object(),  # type: ignore[arg-type]
        )
        image_bytes = b"GIF89a" + struct.pack("<HH", 155, 20)

        with (
            patch(
                "app.modules.crawler.runtime.native_ocr.fetch_binary_resource",
                new=AsyncMock(
                    return_value=(
                        "https://example.edu/zhang/email.gif",
                        "image/gif",
                        image_bytes,
                    )
                ),
            ) as fetch_mock,
            patch(
                "app.modules.crawler.runtime.native_ocr.recognize_image_text",
                new=AsyncMock(return_value="heix.j@hust.edu. cn"),
            ) as ocr_mock,
        ):
            first = await extract_ocr_email_evidence(ctx, snapshot)
            second = await extract_ocr_email_evidence(ctx, snapshot)

        self.assertEqual([item.email for item in first], ["heix.j@hust.edu.cn"])
        self.assertEqual(second, first)
        self.assertEqual(fetch_mock.await_count, 2)
        self.assertEqual(ocr_mock.await_count, 2)


if __name__ == "__main__":
    unittest.main()
