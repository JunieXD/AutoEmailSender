from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from app.modules.crawler.pages.tools import PageSnapshot
from app.modules.crawler.runtime.profile_documents import (
    discover_embedded_profile_pdf_urls,
    extract_primary_embedded_profile_pdf_text,
    merge_profile_text_with_embedded_pdf,
)


FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures" / "document_extraction"


class CrawlerProfileDocumentTests(unittest.IsolatedAsyncioTestCase):
    def test_discovers_pdfjs_file_from_primary_iframe(self) -> None:
        snapshot = PageSnapshot(
            url="https://faculty.example.edu/people/zhang",
            html=(
                '<main>张三</main><iframe src="/_js/pdfjs/web/viewer.html?'
                'file=%252F_upload%252Fzhang.pdf"></iframe>'
            ),
            fetch_method="browser",
            status="succeeded",
        )

        self.assertEqual(
            discover_embedded_profile_pdf_urls(snapshot),
            ("https://faculty.example.edu/_upload/zhang.pdf",),
        )

    def test_discovers_direct_pdf_object_but_ignores_ordinary_pdf_link(self) -> None:
        snapshot = PageSnapshot(
            url="https://faculty.example.edu/people/zhang",
            html=(
                '<object data="/profiles/zhang.PDF#page=1"></object>'
                '<a href="/papers/publication.pdf">论文</a>'
            ),
            fetch_method="http",
            status="succeeded",
        )

        self.assertEqual(
            discover_embedded_profile_pdf_urls(snapshot),
            ("https://faculty.example.edu/profiles/zhang.PDF",),
        )

    async def test_extracts_text_from_a_fetched_embedded_pdf(self) -> None:
        pdf_bytes = (FIXTURE_DIR / "plain_resume.pdf").read_bytes()
        snapshot = PageSnapshot(
            url="https://faculty.example.edu/people/zhang",
            html='<iframe src="/viewer.html?file=/profiles/zhang.pdf"></iframe>',
            fetch_method="browser",
            status="succeeded",
        )

        with patch(
            "app.modules.crawler.runtime.profile_documents.fetch_binary_resource",
            new=AsyncMock(
                return_value=(
                    "https://faculty.example.edu/profiles/zhang.pdf",
                    "application/pdf",
                    pdf_bytes,
                )
            ),
        ) as fetch_mock:
            result = await extract_primary_embedded_profile_pdf_text(  # type: ignore[arg-type]
                object(),
                snapshot,
            )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(
            result.source_url,
            "https://faculty.example.edu/profiles/zhang.pdf",
        )
        self.assertIn("研究生申请简历", result.text)
        fetch_mock.assert_awaited_once()

    async def test_pdf_failure_leaves_the_existing_profile_flow_available(self) -> None:
        snapshot = PageSnapshot(
            url="https://faculty.example.edu/people/zhang",
            html='<iframe src="/viewer.html?file=/profiles/zhang.pdf"></iframe>',
            fetch_method="browser",
            status="succeeded",
        )
        with patch(
            "app.modules.crawler.runtime.profile_documents.fetch_binary_resource",
            new=AsyncMock(side_effect=ValueError("too large")),
        ):
            result = await extract_primary_embedded_profile_pdf_text(  # type: ignore[arg-type]
                object(),
                snapshot,
            )

        self.assertIsNone(result)

    def test_merge_preserves_profile_identity_and_pdf_tail_within_budget(self) -> None:
        page_text = "张三个人主页\n" + "导航" * 4000
        pdf_text = "履历开头\n" + "研究内容" * 5000 + "\n联系方式 tail@example.edu"

        merged = merge_profile_text_with_embedded_pdf(
            page_text,
            pdf_text,
            max_chars=12000,
        )

        self.assertLessEqual(len(merged), 12000)
        self.assertIn("张三个人主页", merged)
        self.assertIn("嵌入的个人资料 PDF 正文", merged)
        self.assertIn("履历开头", merged)
        self.assertIn("tail@example.edu", merged)


if __name__ == "__main__":
    unittest.main()
