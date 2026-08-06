from __future__ import annotations

import os
import unittest

from app.modules.crawler.pages import tools as crawler_tools


LIVE_URLS = [
    (
        "http://www.sei.ecnu.edu.cn/33189/list.htm",
        ("教师", "导师", "学院", "软件"),
    ),
    (
        "https://informatics.xmu.edu.cn/list_teacher.jsp?urltype=tp.TpCollegeZWTeachers&wbtreeid=2171&collegeid=1532&postdutyid=1123&language=zh_CN&faggregatequeryid=&checkaggregatequeryid=1123",
        ("教师", "导师", "信息"),
    ),
    (
        "https://scs.bupt.edu.cn/szjs1/jsyl.htm",
        ("教师", "导师", "计算机", "周锋"),
    ),
]


@unittest.skipUnless(
    os.environ.get("AUTO_EMAIL_SENDER_LIVE_CRAWLER_TESTS") == "1",
    "set AUTO_EMAIL_SENDER_LIVE_CRAWLER_TESTS=1 to run live crawler tests",
)
class LivePlaywrightCrawlerTests(unittest.IsolatedAsyncioTestCase):
    async def test_required_teacher_pages_return_useful_snapshots(self) -> None:
        for url, markers in LIVE_URLS:
            with self.subTest(url=url):
                snapshot = await crawler_tools._fetch_page_with_playwright_direct(
                    url,
                    "",
                    "directory",
                )

                self.assertEqual(snapshot.status, "succeeded", snapshot.error_message)
                self.assertEqual(snapshot.fetch_method, "browser")
                self.assertGreater(len(snapshot.text.strip()), 50)
                self.assertTrue(
                    any(marker in snapshot.text for marker in markers),
                    snapshot.text[:500],
                )
