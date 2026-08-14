from __future__ import annotations

import asyncio
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models import CrawlCandidate, CrawlJob, CrawlJobStatus, CrawlPage, CrawlPageFetchState
from app.modules.crawler.pages.tools import (
    CrawlJobCanceled,
    CrawlJobPaused,
    CrawlToolContext,
    CandidateEnrichmentPayload,
    PageSnapshot,
    build_candidate_enrichment_prompt,
    ProfessorCandidatePayload,
    extract_first_email_from_text,
    normalize_obfuscated_email_tokens,
    crawl_page_with_browser_fallback,
    crawl_page_with_http,
    is_allowed_crawl_url,
    is_safe_public_crawl_url,
    normalize_candidate_payload,
    normalize_candidate_profile_url,
    record_page_snapshot,
    save_candidate_payloads_shared,
    _body_content_changed_substantially,
    _crawl_page_with_browser,
    _is_resolved_allowed_crawl_url,
    _resolve_safe_public_crawl_url,
)
from app.modules.crawler.pages import tools as crawler_tools
from test.schema_database import create_schema_sqlite_database


class CrawlerToolTests(unittest.TestCase):
    def _test_ctx(self) -> CrawlToolContext:
        return CrawlToolContext(
            job_id=1,
            start_url="https://cs.example.edu/faculty",
            university="示例大学",
            school="计算机学院",
            session_factory=_FakeSessionFactory(),  # type: ignore[arg-type]
        )

    def test_html_to_snapshot_caps_untrusted_html_before_parsing(self) -> None:
        oversized_html = "<main>教师名单</main>" + "x" * crawler_tools.MAX_CRAWL_HTML_CHARS

        snapshot = crawler_tools.html_to_snapshot(
            "https://example.edu/faculty",
            oversized_html,
            "http",
        )

        self.assertEqual(len(snapshot.html), crawler_tools.MAX_CRAWL_HTML_CHARS)
        self.assertIn("教师名单", snapshot.text)

    def test_html_to_snapshot_ignores_commented_markup(self) -> None:
        commented_navigation = "".join(
            f"<li>隐藏导航 {index} fake{index}@example.edu</li>"
            for index in range(500)
        )
        html = (
            f"<html><body><!--{commented_navigation}-->"
            "<main><h1>黄豪彩</h1><p>hchuang@zju.edu.cn</p></main>"
            "</body></html>"
        )

        snapshot = crawler_tools.html_to_snapshot(
            "https://example.edu/teacher/huang",
            html,
            "browser",
        )

        self.assertIn("黄豪彩", snapshot.text)
        self.assertIn("hchuang@zju.edu.cn", snapshot.text)
        self.assertNotIn("隐藏导航", snapshot.text)
        self.assertNotIn("fake0@example.edu", snapshot.text)

    def test_browser_snapshot_rejects_empty_document_shell(self) -> None:
        snapshot = crawler_tools._snapshot_from_browser_html(
            html="<html><head></head><body></body></html>",
            final_url="http://packaged-qa.test.invalid:59999/profile/repro",
            absolute_url="http://packaged-qa.test.invalid:59999/profile/repro",
        )

        self.assertEqual(snapshot.status, "failed")
        self.assertTrue(snapshot.suspicious_empty)
        self.assertIn("no readable page content", snapshot.error_message or "")
        self.assertEqual(snapshot.html, "<html><head></head><body></body></html>")

    def test_browser_pagination_wait_ignores_only_active_page_number_change(self) -> None:
        shared = "教师名单 " + ("张三 教授 李四 副教授 " * 80)
        self.assertFalse(
            _body_content_changed_substantially(
                f"{shared} 当前页 1",
                f"{shared} 当前页 2",
            )
        )
        self.assertTrue(
            _body_content_changed_substantially(
                f"{shared} 张三 李四",
                f"{shared[:300]} 王五 赵六 新一页内容",
            )
        )

    def test_crawl_tool_context_tracks_denied_urls_by_normalized_exact_url(self) -> None:
        ctx = CrawlToolContext(
            job_id=1,
            start_url="https://cs.example.edu/faculty/index.htm",
            university="测试大学",
            school="计算机学院",
            session_factory=_FakeSessionFactory(),  # type: ignore[arg-type]
        )

        ctx.mark_denied_url("https://cs.example.edu/news/a.htm#section", "无关新闻页")

        self.assertTrue(ctx.is_denied_url("https://cs.example.edu/news/a.htm"))
        self.assertEqual(
            ctx.denied_url_reason("https://cs.example.edu/news/a.htm#other"),
            "无关新闻页",
        )
        self.assertFalse(ctx.is_denied_url("https://cs.example.edu/news/b.htm"))
        self.assertFalse(ctx.is_denied_url("https://cs.example.edu/news/"))

    def test_normalize_candidate_profile_url_preserves_spa_hash_route(self) -> None:
        self.assertEqual(
            normalize_candidate_profile_url("http://sim.jxufe.edu.cn/#/staff/detail/5"),
            "http://sim.jxufe.edu.cn/#/staff/detail/5",
        )

    def test_normalize_candidate_profile_url_drops_plain_document_anchor(self) -> None:
        self.assertEqual(
            normalize_candidate_profile_url("https://cs.example.edu/teachers/zhang#bio"),
            "https://cs.example.edu/teachers/zhang",
        )

    def test_page_snapshot_cache_distinguishes_spa_hash_routes(self) -> None:
        ctx = self._test_ctx()
        staff = PageSnapshot(
            url="http://sim.jxufe.edu.cn/#/staff/detail/5",
            title="万常选",
            text="万常选",
            html="<html>staff</html>",
            links=[],
            fetch_method="browser",
            status="succeeded",
        )
        home = PageSnapshot(
            url="http://sim.jxufe.edu.cn/#/home",
            title="首页",
            text="首页",
            html="<html>home</html>",
            links=[],
            fetch_method="browser",
            status="succeeded",
        )

        ctx.remember_page_snapshot(staff)
        ctx.remember_page_snapshot(home)

        self.assertIs(ctx.get_cached_page_snapshot(staff.url), staff)
        self.assertIs(ctx.get_cached_page_snapshot(home.url), home)

    def test_page_snapshot_cache_evicts_lru_entries(self) -> None:
        ctx = self._test_ctx()
        first = PageSnapshot(
            url="https://example.edu/a",
            title="A",
            text="alpha",
            html="<html>a</html>",
            links=[],
            fetch_method="http",
            status="succeeded",
        )
        second = PageSnapshot(
            url="https://example.edu/b",
            title="B",
            text="beta",
            html="<html>b</html>",
            links=[],
            fetch_method="http",
            status="succeeded",
        )
        third = PageSnapshot(
            url="https://example.edu/c",
            title="C",
            text="gamma",
            html="<html>c</html>",
            links=[],
            fetch_method="http",
            status="succeeded",
        )

        with patch("app.modules.crawler.pages.tools.MAX_PAGE_SNAPSHOT_CACHE_ENTRIES", 2, create=True):
            ctx.remember_page_snapshot(first)
            ctx.remember_page_snapshot(second)
            self.assertIs(ctx.get_cached_page_snapshot(first.url), first)
            ctx.remember_page_snapshot(third)

        self.assertIs(ctx.get_cached_page_snapshot(first.url), first)
        self.assertIsNone(ctx.get_cached_page_snapshot(second.url))
        self.assertIs(ctx.get_cached_page_snapshot(third.url), third)

    def test_record_page_snapshot_sets_snapshot_page_id(self) -> None:
        async def run() -> None:
            async with _RealCrawlerSessionHarness() as harness:
                job_id = await harness.create_job()
                ctx = CrawlToolContext(
                    job_id=job_id,
                    start_url="https://cs.example.edu/faculty",
                    university="示例大学",
                    school="计算机学院",
                    session_factory=harness.session_factory,
                )
                snapshot = PageSnapshot(
                    url="https://cs.example.edu/faculty",
                    title="师资队伍",
                    text="张三",
                    html="<p>张三</p>",
                    links=[],
                    fetch_method="http",
                    status="succeeded",
                )

                row = await record_page_snapshot(ctx, snapshot)

                self.assertIsNotNone(row)
                self.assertEqual(snapshot.page_id, row.id if row is not None else None)

        asyncio.run(run())

    def test_browser_fetch_options_for_profile_uses_load_and_waits_for_body(self) -> None:
        options = crawler_tools._browser_fetch_options_for_intent("profile")

        self.assertEqual(options.wait_for, "css:body")
        self.assertEqual(options.wait_until, "load")
        self.assertEqual(options.wait_for_timeout_ms, 15000)
        self.assertEqual(options.page_timeout_ms, 30000)
        self.assertEqual(options.delay_before_return_html_seconds, 0)
        self.assertTrue(options.wait_for_dynamic_profile)
        self.assertEqual(options.dynamic_profile_ready_timeout_ms, 10000)
        self.assertIn("Chrome/124.0.0.0", options.user_agent)

    def test_browser_fetch_options_for_generic_and_directory_use_load_and_wait_for_body(self) -> None:
        generic_options = crawler_tools._browser_fetch_options_for_intent("generic")
        directory_options = crawler_tools._browser_fetch_options_for_intent("directory")

        self.assertEqual(generic_options.wait_for, "css:body")
        self.assertEqual(directory_options.wait_for, "css:body")
        self.assertEqual(generic_options.wait_until, "load")
        self.assertEqual(directory_options.wait_until, "load")
        self.assertFalse(generic_options.wait_for_dynamic_directory)
        self.assertTrue(directory_options.wait_for_dynamic_directory)
        self.assertEqual(directory_options.delay_before_return_html_seconds, 0)
        self.assertEqual(directory_options.dynamic_directory_ready_timeout_ms, 5000)
        self.assertEqual(directory_options.max_retries, 1)

    def test_playwright_launch_options_disable_chromium_https_upgrades_and_automation_controlled(self) -> None:
        options = crawler_tools._playwright_launch_options()

        args = options["args"]
        self.assertIn("--disable-features=HttpsUpgrades", args)
        self.assertIn("--disable-blink-features=AutomationControlled", args)
        self.assertTrue(options["headless"])
        self.assertNotIn("channel", options)

    def test_playwright_launch_options_map_only_explicit_test_hosts(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir, patch.dict(
            os.environ,
            {
                "AUTO_EMAIL_SENDER_TEST_FAULTS": "enabled-for-tests-only",
                "AUTO_EMAIL_SENDER_TEST_FAULT_DIR": temp_dir,
                "AUTO_EMAIL_SENDER_TEST_CRAWL_LOOPBACK_HOSTS": (
                    "browser.test.invalid"
                ),
            },
            clear=False,
        ):
            args = crawler_tools._playwright_launch_options()["args"]

        self.assertIn(
            "--host-resolver-rules=MAP browser.test.invalid 127.0.0.1",
            args,
        )

    def test_certificate_compatibility_only_accepts_date_errors(self) -> None:
        self.assertTrue(
            crawler_tools._is_certificate_date_error(
                "Page.goto: net::ERR_CERT_DATE_INVALID"
            )
        )
        self.assertTrue(
            crawler_tools._is_certificate_date_error(
                "certificate verify failed: certificate has expired"
            )
        )
        self.assertFalse(
            crawler_tools._is_certificate_date_error(
                "Page.goto: net::ERR_CERT_COMMON_NAME_INVALID"
            )
        )
        self.assertFalse(
            crawler_tools._is_certificate_date_error(
                "certificate verify failed: self-signed certificate"
            )
        )

    def test_playwright_browser_fetch_retries_date_error_once_in_compatibility_mode(self) -> None:
        async def run() -> None:
            failed = PageSnapshot(
                url="https://example.edu/faculty",
                text="",
                html="",
                links=[],
                fetch_method="browser",
                status="failed",
                error_message="Page.goto: net::ERR_CERT_DATE_INVALID",
            )
            succeeded = PageSnapshot(
                url="https://example.edu/faculty",
                text="张三",
                html="<p>张三</p>",
                links=[],
                fetch_method="browser",
                status="succeeded",
            )

            with patch(
                "app.modules.crawler.pages.tools._try_playwright_browser_fetch_once",
                new=AsyncMock(side_effect=[failed, succeeded]),
            ) as fetch_once:
                actual = await crawler_tools._try_playwright_browser_fetch(
                    "https://example.edu/faculty",
                    crawler_tools.BrowserFetchOptions(max_retries=2),
                )

            self.assertEqual(actual, succeeded)
            self.assertEqual(fetch_once.await_count, 2)
            first_options = fetch_once.await_args_list[0].args[1]
            compatibility_options = fetch_once.await_args_list[1].args[1]
            self.assertFalse(first_options.ignore_https_errors)
            self.assertTrue(compatibility_options.ignore_https_errors)
            self.assertEqual(compatibility_options.max_retries, 0)

        asyncio.run(run())

    def test_browser_pagination_retries_date_error_once_in_compatibility_mode(self) -> None:
        async def run() -> None:
            failed = crawler_tools.BrowserPaginationExpansion(
                status="failed",
                stopped_reason="browser_error",
                error_message="Page.goto: net::ERR_CERT_DATE_INVALID",
            )
            succeeded = crawler_tools.BrowserPaginationExpansion(
                status="succeeded",
                stopped_reason="control_disabled",
            )

            with patch(
                "app.modules.crawler.pages.tools._try_fetch_browser_pagination_once",
                new=AsyncMock(side_effect=[failed, succeeded]),
            ) as fetch_once:
                actual = await crawler_tools._fetch_browser_pagination_direct(
                    "https://example.edu/faculty",
                    {"tag": "a"},
                    intent="directory",
                    max_pages=10,
                )

            self.assertEqual(actual, succeeded)
            self.assertEqual(fetch_once.await_count, 2)
            self.assertNotIn(
                "ignore_https_errors",
                fetch_once.await_args_list[0].kwargs,
            )
            self.assertTrue(
                fetch_once.await_args_list[1].kwargs["ignore_https_errors"]
            )

        asyncio.run(run())

    def test_is_allowed_crawl_url_allows_same_host(self) -> None:
        with patch(
            "app.modules.crawler.pages.tools.socket.getaddrinfo",
            return_value=[
                (0, 0, 0, "", ("93.184.216.34", 443)),
            ],
        ):
            self.assertTrue(
                is_allowed_crawl_url(
                    "https://cs.example.edu/faculty",
                    "https://cs.example.edu/people/a",
                )
            )

    def test_is_allowed_crawl_url_rejects_other_host(self) -> None:
        with patch(
            "app.modules.crawler.pages.tools.socket.getaddrinfo",
            return_value=[
                (0, 0, 0, "", ("93.184.216.34", 443)),
            ],
        ):
            self.assertFalse(
                is_allowed_crawl_url(
                    "https://cs.example.edu/faculty",
                    "https://evil.example.net/people/a",
                )
            )

    def test_is_allowed_crawl_url_allows_same_registrable_domain_subdomains(self) -> None:
        with patch(
            "app.modules.crawler.pages.tools.socket.getaddrinfo",
            return_value=[
                (0, 0, 0, "", ("93.184.216.34", 443)),
            ],
        ):
            self.assertTrue(
                is_allowed_crawl_url(
                    "https://cai.jxufe.edu.cn/lists/26.html",
                    "https://cta.jxufe.edu.cn/home/teacherInfo/detail?uid=1",
                )
            )

    def test_is_allowed_crawl_url_rejects_different_registrable_domain(self) -> None:
        with patch(
            "app.modules.crawler.pages.tools.socket.getaddrinfo",
            return_value=[
                (0, 0, 0, "", ("93.184.216.34", 80)),
            ],
        ):
            self.assertFalse(
                is_allowed_crawl_url(
                    "https://cai.jxufe.edu.cn/lists/26.html",
                    "http://sim.jxufe.cn/static/JDMKL/ymfang.html",
                )
            )

    def test_resolved_crawl_url_policy_rejects_private_dns_address(self) -> None:
        with patch(
            "app.modules.crawler.pages.tools.socket.getaddrinfo",
            return_value=[
                (0, 0, 0, "", ("127.0.0.1", 443)),
            ],
        ):
            self.assertFalse(
                _is_resolved_allowed_crawl_url(
                    "https://cs.example.edu/faculty",
                    "https://faculty.example.edu/people/a",
                )
            )

    def test_is_safe_public_crawl_url_rejects_unsafe_ip_literals_and_localhost(self) -> None:
        for url in (
            "http://127.0.0.1/faculty",
            "http://localhost/faculty",
            "http://faculty.localhost/faculty",
            "http://10.0.0.1/faculty",
            "http://169.254.169.254/latest/meta-data",
            "http://224.0.0.1/faculty",
            "http://0.0.0.0/faculty",
            "http://198.18.0.105/faculty",
            "http://192.0.2.1/faculty",
        ):
            with self.subTest(url=url):
                self.assertFalse(is_safe_public_crawl_url(url))

    def test_is_safe_public_crawl_url_allows_domain_without_dns_resolution(self) -> None:
        with patch(
            "app.modules.crawler.pages.tools.socket.getaddrinfo",
            side_effect=AssertionError("URL validation should not resolve domain names"),
        ):
            self.assertTrue(is_safe_public_crawl_url("https://faculty.example.edu"))

    def test_resolve_safe_public_crawl_url_uses_system_dns_for_fetching_domains(self) -> None:
        with patch(
            "app.modules.crawler.pages.tools.socket.getaddrinfo",
            return_value=[
                (0, 0, 0, "", ("93.184.216.34", 443)),
            ],
        ):
            resolved = _resolve_safe_public_crawl_url("https://faculty.example.edu")
            self.assertEqual(resolved.resolved_ips, ("93.184.216.34",))

    def test_resolve_safe_public_crawl_url_can_recheck_explicit_profile_with_public_dns(self) -> None:
        crawler_tools._resolve_public_dns_host_ips.cache_clear()
        responses = [
            SimpleNamespace(
                raise_for_status=lambda: None,
                json=lambda: {
                    "Status": 0,
                    "Answer": [{"type": 1, "data": "142.251.210.142"}],
                },
            ),
            SimpleNamespace(
                raise_for_status=lambda: None,
                json=lambda: {
                    "Status": 0,
                    "Answer": [{"type": 28, "data": "2607:f8b0:4007:805::200e"}],
                },
            ),
        ]
        with patch(
            "app.modules.crawler.pages.tools.socket.getaddrinfo",
            return_value=[(0, 0, 0, "", ("2001::1", 443))],
        ), patch(
            "app.modules.crawler.pages.tools.httpx.get",
            side_effect=responses,
        ) as public_dns_mock:
            resolved = _resolve_safe_public_crawl_url(
                "https://sites.google.com/view/example",
                allow_public_dns_fallback=True,
            )

        self.assertEqual(
            resolved.resolved_ips,
            ("142.251.210.142", "2607:f8b0:4007:805::200e"),
        )
        self.assertEqual(public_dns_mock.call_count, 2)

    def test_public_dns_recheck_still_rejects_private_answers(self) -> None:
        crawler_tools._resolve_public_dns_host_ips.cache_clear()
        response = SimpleNamespace(
            raise_for_status=lambda: None,
            json=lambda: {
                "Status": 0,
                "Answer": [{"type": 1, "data": "127.0.0.1"}],
            },
        )
        with patch(
            "app.modules.crawler.pages.tools.socket.getaddrinfo",
            return_value=[(0, 0, 0, "", ("127.0.0.1", 443))],
        ), patch(
            "app.modules.crawler.pages.tools.httpx.get",
            return_value=response,
        ):
            with self.assertRaises(ValueError):
                _resolve_safe_public_crawl_url(
                    "https://profiles.example.net/person",
                    allow_public_dns_fallback=True,
                )

    def test_public_dns_recheck_never_applies_to_private_ip_literal(self) -> None:
        with patch(
            "app.modules.crawler.pages.tools.httpx.get",
        ) as public_dns_mock:
            with self.assertRaises(ValueError):
                _resolve_safe_public_crawl_url(
                    "http://127.0.0.1/private",
                    allow_public_dns_fallback=True,
                )

        public_dns_mock.assert_not_called()

    def test_is_safe_public_crawl_url_allows_domain_even_if_system_dns_would_be_private(self) -> None:
        with patch(
            "app.modules.crawler.pages.tools.socket.getaddrinfo",
            side_effect=AssertionError("URL validation should not resolve domain names"),
        ):
            self.assertTrue(is_safe_public_crawl_url("https://faculty.example.edu"))

    def test_is_safe_public_crawl_url_allows_unresolvable_domain_at_validation_time(self) -> None:
        with patch(
            "app.modules.crawler.pages.tools.socket.getaddrinfo",
            side_effect=AssertionError("URL validation should not resolve domain names"),
        ):
            self.assertTrue(is_safe_public_crawl_url("https://faculty.example.edu"))

    def test_normalize_candidate_payload_fills_school_context(self) -> None:
        payload = normalize_candidate_payload(
            ProfessorCandidatePayload(
                name=" 张三 ",
                email=" zhang@example.edu ",
                title="教授",
                university=None,
                school=None,
                department=None,
                research_direction=" 信息检索 ",
                recent_papers=[" Paper A ", ""],
                profile_url="https://cs.example.edu/zhang",
                source_url="https://cs.example.edu/zhang",
                confidence=1.5,
                field_confidence={"email": 1.2},
                evidence={"name": "张三"},
            ),
            university="示例大学",
            school="计算机学院",
        )

        self.assertEqual(payload["name"], "张三")
        self.assertEqual(payload["email"], "zhang@example.edu")
        self.assertEqual(payload["university"], "示例大学")
        self.assertEqual(payload["school"], "计算机学院")
        self.assertEqual(payload["recent_papers"], ["Paper A"])
        self.assertEqual(payload["confidence"], 1.0)
        self.assertEqual(payload["field_confidence"], {"email": 1.0})

    def test_normalize_candidate_payload_caps_recent_papers_to_first_8(self) -> None:
        payload = normalize_candidate_payload(
            ProfessorCandidatePayload(
                name="张三",
                recent_papers=[f"Paper {index}" for index in range(1, 12)],
            ),
            university="示例大学",
            school="计算机学院",
        )

        self.assertEqual(payload["recent_papers"], [f"Paper {index}" for index in range(1, 9)])

    def test_normalize_candidate_payload_keeps_first_valid_email(self) -> None:
        payload = normalize_candidate_payload(
            ProfessorCandidatePayload(
                name="张三",
                email="zhang@example.edu, zhang.work@example.edu",
            ),
            university="示例大学",
            school="计算机学院",
        )

        self.assertEqual(payload["email"], "zhang@example.edu")

    def test_normalize_candidate_payload_uses_later_valid_email_when_first_segment_invalid(self) -> None:
        payload = normalize_candidate_payload(
            ProfessorCandidatePayload(
                name="张三",
                email="办公室邮箱：暂无；zhang (AT) example DOT edu",
            ),
            university="示例大学",
            school="计算机学院",
        )

        self.assertEqual(payload["email"], "zhang@example.edu")

    def test_normalize_candidate_payload_repairs_obfuscated_domain_dots(self) -> None:
        payload = normalize_candidate_payload(
            ProfessorCandidatePayload(
                name="陈老师",
                email="wjchen@sei.ecnu...cn",
            ),
            university="示例大学",
            school="计算机学院",
        )

        self.assertEqual(payload["email"], "wjchen@sei.ecnu.cn")

    def test_professor_candidate_payload_accepts_chinese_aliases(self) -> None:
        candidate = ProfessorCandidatePayload.model_validate(
            {
                "姓名": "张三",
                "邮箱": "zhang@example.edu",
                "职称": "教授",
                "学校": "示例大学",
                "院系": "计算机学院",
                "主页URL": "https://example.edu/faculty/zhang",
                "证据来源": "https://example.edu/faculty",
                "置信度": 0.92,
            }
        )

        self.assertEqual(candidate.name, "张三")
        self.assertEqual(candidate.email, "zhang@example.edu")
        self.assertEqual(candidate.title, "教授")
        self.assertEqual(candidate.university, "示例大学")
        self.assertEqual(candidate.school, "计算机学院")
        self.assertEqual(candidate.profile_url, "https://example.edu/faculty/zhang")
        self.assertEqual(candidate.source_url, "https://example.edu/faculty")
        self.assertEqual(candidate.confidence, 0.92)

    def test_professor_candidate_payload_normalizes_common_model_type_drift(self) -> None:
        candidate = ProfessorCandidatePayload.model_validate(
            {
                "name": "张三",
                "recent_papers": "",
                "field_confidence": {
                    "overall": 0.9,
                    "fields": {"name": 1.0, "email": 0.95},
                },
                "evidence": "从导师列表页提取",
            }
        )

        self.assertEqual(candidate.recent_papers, [])
        self.assertEqual(
            candidate.field_confidence,
            {"overall": 0.9, "name": 1.0, "email": 0.95},
        )
        self.assertEqual(candidate.evidence, {"summary": "从导师列表页提取"})

    def test_professor_candidate_payload_normalizes_recent_papers_string_with_multi_separators(self) -> None:
        candidate = ProfessorCandidatePayload.model_validate(
            {"name": "张三", "recent_papers": "Paper A；Paper B|Paper C\nPaper D"}
        )
        self.assertEqual(candidate.recent_papers, ["Paper A", "Paper B", "Paper C", "Paper D"])

    def test_professor_candidate_payload_normalizes_semantic_confidence_labels(self) -> None:
        candidate = ProfessorCandidatePayload.model_validate(
            {
                "name": "zhangsan",
                "confidence": "high",
            }
        )

        self.assertEqual(candidate.confidence, 0.9)

    def test_build_candidate_enrichment_prompt_contains_saved_candidate_context(self) -> None:
        candidate = CrawlCandidate(
            id=1,
            job_id=1,
            name="张三",
            email="zhang@example.edu",
            title="教授",
            university="示例大学",
            school="计算机学院",
            department=None,
            research_direction=None,
            recent_papers=[],
            profile_url="https://example.edu/faculty/zhang",
            source_url=None,
            confidence=0.0,
        )

        prompt = build_candidate_enrichment_prompt(candidate, "研究方向：大语言模型")

        self.assertIn("张三", prompt)
        self.assertIn("zhang@example.edu", prompt)
        self.assertIn("https://example.edu/faculty/zhang", prompt)
        self.assertIn("只补全缺失字段：email, title, department, research_direction, recent_papers", prompt)
        self.assertIn("如果正文出现该导师的邮箱，必须补全 email 字段", prompt)
        self.assertIn("必须补全 title 字段", prompt)
        self.assertIn("不要把院长、主任、教师等行政职务或普通岗位当作职称", prompt)
        self.assertIn("多个邮箱", prompt)
        self.assertIn("最可能属于该导师", prompt)
        self.assertIn("[@]", prompt)
        self.assertIn("字段值尽量保持页面原文", prompt)
        self.assertIn("输出示例", prompt)
        self.assertIn('"email"', prompt)
        self.assertIn('"title": "教授"', prompt)
        self.assertIn('"recent_papers": []', prompt)
        self.assertLess(
            prompt.index("只补全缺失字段"),
            prompt.index("- 姓名：张三"),
        )
        self.assertLess(prompt.index("输出示例："), prompt.index("已知基础信息："))

    def test_candidate_enrichment_payload_defaults(self) -> None:
        payload = CandidateEnrichmentPayload.model_validate({})
        self.assertIsNone(payload.email)
        self.assertIsNone(payload.department)
        self.assertIsNone(payload.research_direction)
        self.assertEqual(payload.recent_papers, [])

    def test_candidate_enrichment_payload_caps_recent_papers_to_first_8(self) -> None:
        papers = [f"Paper {index}" for index in range(1, 13)]

        payload = CandidateEnrichmentPayload.model_validate({"recent_papers": papers})

        self.assertEqual(payload.recent_papers, papers[:8])

    def test_normalize_obfuscated_email_tokens(self) -> None:
        self.assertEqual(
            normalize_obfuscated_email_tokens(
                "name (AT) example DOT edu, another[at]school[dot]cn, third AT example DOT edu.cn"
            ),
            "name@example.edu, another@school.cn, third@example.edu.cn",
        )
        self.assertEqual(
            normalize_obfuscated_email_tokens(
                "name（AT）example（DOT）edu, another 邮箱符号 school 点 cn, third＠example．edu"
            ),
            "name@example.edu, another@school.cn, third@example.edu",
        )

    def test_extract_first_email_from_text(self) -> None:
        extracted = extract_first_email_from_text(
            "联系人：zhang(AT)example(DOT)edu，lisi AT bupt DOT edu DOT cn，请联系"
        )
        self.assertEqual(extracted, "zhang@example.edu")

    def test_extract_first_email_from_text_handles_simple_obfuscations(self) -> None:
        cases = {
            "联系人：wjchen&#64;sei.ecnu.edu.cn": "wjchen@sei.ecnu.edu.cn",
            "联系人：wjchen＠sei．ecnu．edu．cn": "wjchen@sei.ecnu.edu.cn",
            "联系人：wjchen\u200b@sei.ecnu.edu.cn": "wjchen@sei.ecnu.edu.cn",
            "联系人：wjchen @ sei . ecnu . edu . cn": "wjchen@sei.ecnu.edu.cn",
            "联系人：wjchen 邮箱符号 sei 点 ecnu 点 edu 点 cn": "wjchen@sei.ecnu.edu.cn",
        }

        for value, expected in cases.items():
            with self.subTest(value=value):
                self.assertEqual(extract_first_email_from_text(value), expected)


class CrawlerHttpToolTests(unittest.IsolatedAsyncioTestCase):
    async def test_crawl_page_reuses_cached_snapshot_for_duplicate_url(self) -> None:
        ctx = CrawlToolContext(
            job_id=1,
            start_url="https://example.edu/faculty",
            university="示例大学",
            school="计算机学院",
            session_factory=_FakeSessionFactory(),  # type: ignore[arg-type]
        )
        snapshot = PageSnapshot(
            url="https://example.edu/faculty",
            title="faculty",
            text="ok",
            html="<html></html>",
            links=[],
            fetch_method="http",
            status="succeeded",
        )

        with patch(
            "app.modules.crawler.pages.tools.crawl_page_with_http",
            new=AsyncMock(return_value=snapshot),
        ) as crawl_http:
            first = await crawl_page_with_browser_fallback(ctx, "https://example.edu/faculty")
            second = await crawl_page_with_browser_fallback(ctx, "https://example.edu/faculty")

        self.assertEqual(first, snapshot)
        self.assertEqual(second, snapshot)
        self.assertEqual(crawl_http.await_count, 1)


    async def test_crawl_page_with_browser_fallback_skips_terminal_failed_url_without_network(self) -> None:
        async with _RealCrawlerSessionHarness() as harness:
            job_id = await harness.create_job()
            async with harness.session_factory() as session:
                session.add(
                    CrawlPageFetchState(
                        job_id=job_id,
                        normalized_url="https://cs.example.edu/faculty",
                        original_url="https://cs.example.edu/faculty",
                        status="terminal_failed",
                        last_fetch_method="browser",
                        terminal_reason="anti_bot_or_empty_response",
                        last_error_message="Blocked by anti-bot protection",
                    )
                )
                await session.commit()
            ctx = CrawlToolContext(
                job_id=job_id,
                start_url="https://cs.example.edu/faculty",
                university="示例大学",
                school="计算机学院",
                session_factory=harness.session_factory,
            )

            with patch("app.modules.crawler.pages.tools.crawl_page_with_http", AsyncMock()) as http_mock, patch(
                "app.modules.crawler.pages.tools._crawl_page_with_browser", AsyncMock()
            ) as browser_mock:
                snapshot = await crawl_page_with_browser_fallback(ctx, "https://cs.example.edu/faculty#ignored")

        self.assertEqual(snapshot.status, "failed")
        self.assertEqual(snapshot.fetch_method, "ledger")
        self.assertIn("此前已明确抓取失败", snapshot.error_message or "")
        http_mock.assert_not_awaited()
        browser_mock.assert_not_awaited()


    async def test_crawl_page_with_browser_fallback_skips_terminal_failed_url_with_new_context(self) -> None:
        async with _RealCrawlerSessionHarness() as harness:
            job_id = await harness.create_job()
            async with harness.session_factory() as session:
                session.add(
                    CrawlPageFetchState(
                        job_id=job_id,
                        normalized_url="https://cs.example.edu/faculty",
                        original_url="https://cs.example.edu/faculty",
                        status="terminal_failed",
                        last_fetch_method="browser",
                        terminal_reason="anti_bot_or_empty_response",
                        last_error_message="Blocked by anti-bot protection",
                    )
                )
                await session.commit()
            first_ctx = CrawlToolContext(
                job_id=job_id,
                start_url="https://cs.example.edu/faculty",
                university="示例大学",
                school="计算机学院",
                session_factory=harness.session_factory,
            )
            restarted_ctx = CrawlToolContext(
                job_id=job_id,
                start_url="https://cs.example.edu/faculty",
                university="示例大学",
                school="计算机学院",
                session_factory=harness.session_factory,
            )

            with patch("app.modules.crawler.pages.tools.crawl_page_with_http", AsyncMock()) as http_mock, patch(
                "app.modules.crawler.pages.tools._crawl_page_with_browser", AsyncMock()
            ) as browser_mock:
                first = await crawl_page_with_browser_fallback(first_ctx, "https://cs.example.edu/faculty")
                second = await crawl_page_with_browser_fallback(restarted_ctx, "https://cs.example.edu/faculty")

        self.assertEqual(first.fetch_method, "ledger")
        self.assertEqual(second.fetch_method, "ledger")
        http_mock.assert_not_awaited()
        browser_mock.assert_not_awaited()

    async def test_crawl_page_with_browser_fallback_skips_previously_denied_url(self) -> None:
        ctx = CrawlToolContext(
            job_id=1,
            start_url="https://cs.example.edu/faculty/index.htm",
            university="测试大学",
            school="计算机学院",
            session_factory=_FakeSessionFactory(),  # type: ignore[arg-type]
        )
        ctx.mark_denied_url("https://cs.example.edu/news/a.htm", "无关新闻页")

        with patch("app.modules.crawler.pages.tools.crawl_page_with_http") as mocked_http, patch(
            "app.modules.crawler.pages.tools.browser_investigate"
        ) as mocked_browser:
            snapshot = await crawl_page_with_browser_fallback(ctx, "https://cs.example.edu/news/a.htm")

        mocked_http.assert_not_called()
        mocked_browser.assert_not_called()
        self.assertEqual(snapshot.status, "failed")
        self.assertEqual(snapshot.links, [])
        self.assertIn("已在本轮抓取中判定为无关页面", snapshot.error_message or "")

    async def test_crawl_page_with_browser_fallback_returns_succeeded_page_for_agent_classification(self) -> None:
        ctx = CrawlToolContext(
            job_id=1,
            start_url="https://cs.example.edu/faculty/index.htm",
            university="测试大学",
            school="计算机学院",
            session_factory=_FakeSessionFactory(),  # type: ignore[arg-type]
        )
        http_snapshot = PageSnapshot(
            url="https://cs.example.edu/news/a.htm",
            title="学院新闻",
            text="学院召开本科招生宣传会议，欢迎考生报考。",
            html="<html><body><a href='/news/b.htm'>下一篇</a></body></html>",
            links=["https://cs.example.edu/news/b.htm"],
            fetch_method="http",
            status="succeeded",
        )

        with patch(
            "app.modules.crawler.pages.tools.crawl_page_with_http",
            return_value=http_snapshot,
        ):
            snapshot = await crawl_page_with_browser_fallback(ctx, "https://cs.example.edu/news/a.htm")

        self.assertEqual(snapshot.status, "succeeded")
        self.assertEqual(snapshot.links, ["https://cs.example.edu/news/b.htm"])
        self.assertFalse(ctx.is_denied_url("https://cs.example.edu/news/a.htm"))
        self.assertIsNone(snapshot.error_message)

    async def test_crawl_page_with_browser_fallback_keeps_faculty_directory_and_profile_pages_allowed(self) -> None:
        ctx = CrawlToolContext(
            job_id=1,
            start_url="https://cs.example.edu/faculty/index.htm",
            university="测试大学",
            school="计算机学院",
            session_factory=_FakeSessionFactory(),  # type: ignore[arg-type]
        )
        directory_snapshot = PageSnapshot(
            url="https://cs.example.edu/faculty/index.htm",
            title="教师名录",
            text="教师名录 教授 张三 李四 副教授 王五",
            html="<html><body><a href='/faculty/zhang.htm'>张三</a></body></html>",
            links=["https://cs.example.edu/faculty/zhang.htm"],
            fetch_method="http",
            status="succeeded",
        )
        profile_snapshot = PageSnapshot(
            url="https://cs.example.edu/faculty/zhang.htm",
            title="张三 教授",
            text="张三 教授 邮箱 zhang@example.edu 研究方向 人工智能",
            html="<html><body>张三 教授 邮箱 zhang@example.edu</body></html>",
            links=[],
            fetch_method="http",
            status="succeeded",
        )

        with patch(
            "app.modules.crawler.pages.tools.crawl_page_with_http",
            side_effect=[directory_snapshot, profile_snapshot],
        ):
            directory = await crawl_page_with_browser_fallback(ctx, "https://cs.example.edu/faculty/index.htm")
            profile = await crawl_page_with_browser_fallback(ctx, "https://cs.example.edu/faculty/zhang.htm")

        self.assertEqual(directory.status, "succeeded")
        self.assertEqual(profile.status, "succeeded")
        self.assertFalse(ctx.is_denied_url("https://cs.example.edu/faculty/index.htm"))
        self.assertFalse(ctx.is_denied_url("https://cs.example.edu/faculty/zhang.htm"))

    async def test_redirected_page_is_returned_for_agent_classification(self) -> None:
        ctx = CrawlToolContext(
            job_id=1,
            start_url="https://cs.example.edu/faculty/index.htm",
            university="测试大学",
            school="计算机学院",
            session_factory=_FakeSessionFactory(),  # type: ignore[arg-type]
        )
        redirected_snapshot = PageSnapshot(
            url="https://cs.example.edu/news/a.htm",
            title="学院新闻",
            text="学院新闻 本科招生 宣传会议",
            html="<html><body>学院新闻</body></html>",
            links=[],
            fetch_method="http",
            status="succeeded",
        )

        with patch(
            "app.modules.crawler.pages.tools.crawl_page_with_http",
            return_value=redirected_snapshot,
        ):
            snapshot = await crawl_page_with_browser_fallback(ctx, "https://cs.example.edu/go-news")

        self.assertEqual(snapshot.status, "succeeded")
        self.assertFalse(ctx.is_denied_url("https://cs.example.edu/go-news"))
        self.assertFalse(ctx.is_denied_url("https://cs.example.edu/news/a.htm"))

    async def test_crawl_page_with_browser_fallback_skips_direct_after_same_domain_browser_fallback(self) -> None:
        async with _RealCrawlerSessionHarness() as harness:
            job_id = await harness.create_job()
            async with harness.session_factory() as session:
                session.add(
                    CrawlPageFetchState(
                        job_id=job_id,
                        normalized_url="https://teacher.example.edu/zhang",
                        original_url="https://teacher.example.edu/zhang",
                        status="succeeded",
                        last_fetch_method="browser",
                        fetch_mode="browser",
                        direct_status="failed",
                        fallback_reason="HTTP 412 blocked, browser fallback advised",
                        browser_status="succeeded",
                    )
                )
                await session.commit()
            ctx = CrawlToolContext(
                job_id=job_id,
                start_url="https://teacher.example.edu/zhang",
                university="示例大学",
                school="计算机学院",
                session_factory=harness.session_factory,
            )
            browser_snapshot = PageSnapshot(
                url="https://teacher.example.edu/li",
                title="李四",
                text="李四 邮箱 li@example.edu",
                html="<html>李四</html>",
                links=[],
                fetch_method="browser",
                status="succeeded",
            )

            http_snapshot = PageSnapshot(
                url="https://teacher.example.edu/li",
                title="李四",
                text="李四 HTTP",
                html="<html>HTTP</html>",
                links=[],
                fetch_method="http",
                status="succeeded",
            )


            with patch(
                "app.modules.crawler.pages.tools.crawl_page_with_http",
                new=AsyncMock(return_value=http_snapshot),
            ) as http_fetch, patch(
                "app.modules.crawler.pages.tools.browser_investigate",
                new=AsyncMock(return_value=browser_snapshot),
            ) as browser:
                actual = await crawl_page_with_browser_fallback(ctx, "https://teacher.example.edu/li", intent="profile")

            self.assertEqual(actual.fetch_method, "browser")
            http_fetch.assert_not_awaited()
            browser.assert_awaited_once()
            async with harness.session_factory() as session:
                state = await session.scalar(
                    select(CrawlPageFetchState).where(
                        CrawlPageFetchState.job_id == job_id,
                        CrawlPageFetchState.normalized_url == "https://teacher.example.edu/li",
                    )
                )
            assert state is not None
            self.assertEqual(state.fetch_mode, "browser")
            self.assertEqual(state.direct_status, "skipped_by_domain_browser_preference")
            self.assertEqual(state.fallback_reason, "same_domain_previously_required_browser")
            self.assertEqual(state.browser_status, "succeeded")

    async def test_crawl_page_with_browser_fallback_retries_browser_for_template_placeholders(self) -> None:
        ctx = CrawlToolContext(
            job_id=1,
            start_url="http://cta.jxufe.edu.cn/home/teacherInfo/detail?fid=1",
            university="江西财经大学",
            school="计算机与人工智能学院",
            session_factory=_FakeSessionFactory(),  # type: ignore[arg-type]
        )
        http_snapshot = PageSnapshot(
            url=ctx.start_url,
            title="教师详情",
            text="{{name}}\n{{email}}\n{{data}}",
            html="<html>{{name}}</html>",
            links=[],
            fetch_method="http",
            status="succeeded",
        )
        browser_snapshot = PageSnapshot(
            url=ctx.start_url,
            title="教师详情",
            text="张三\n教授\n邮箱：zhang@example.edu",
            html="<html>张三</html>",
            links=[],
            fetch_method="browser",
            status="succeeded",
        )

        with patch(
            "app.modules.crawler.pages.tools.crawl_page_with_http",
            new=AsyncMock(return_value=http_snapshot),
        ), patch(
            "app.modules.crawler.pages.tools.browser_investigate",
            new=AsyncMock(return_value=browser_snapshot),
        ) as browser:
            actual = await crawl_page_with_browser_fallback(ctx, ctx.start_url, intent="profile")

        self.assertEqual(actual, browser_snapshot)
        browser.assert_awaited_once()

    async def test_crawl_page_with_browser_fallback_renders_client_encrypted_profile_fields(self) -> None:
        ctx = CrawlToolContext(
            job_id=1,
            start_url="https://ic.sdu.edu.cn/jcdlxy/szdw1/xysz.htm",
            university="山东大学",
            school="集成电路学院",
            session_factory=_FakeSessionFactory(),  # type: ignore[arg-type]
        )
        profile_url = "https://faculty.sdu.edu.cn/wanglingyun1/zh_CN/index.htm"
        encrypted_email = "72dafd1db91b8976288f94160a5e2779" * 8
        http_snapshot = crawler_tools.html_to_snapshot(
            profile_url,
            (
                "<html><head><title>山东大学教师主页 王凌云 首页 中文主页</title></head>"
                f"<!--{'x' * 2500}--><body>王凌云 研究员 电子邮箱："
                '<span _tsites_encrypt_field="_tsites_encrypt_field" '
                'id="_tsites_encryp_tsteacher_tsemail" style="display:none;">'
                f"{encrypted_email}</span><p>个人简介</p></body></html>"
            ),
            "http",
        )
        self.assertNotIn("_tsites_encrypt_field", http_snapshot.text)
        self.assertNotIn("_tsites_encrypt_field", http_snapshot.html[:2000])
        browser_snapshot = PageSnapshot(
            url=profile_url,
            title="山东大学教师主页 王凌云 首页 中文主页",
            text="王凌云\n研究员\n电子邮箱：lingyunwang@sdu.edu.cn\n个人简介",
            html="<html><body>王凌云 lingyunwang@sdu.edu.cn</body></html>",
            links=[],
            fetch_method="browser",
            status="succeeded",
        )

        with patch(
            "app.modules.crawler.pages.tools.crawl_page_with_http",
            new=AsyncMock(return_value=http_snapshot),
        ), patch(
            "app.modules.crawler.pages.tools.browser_investigate",
            new=AsyncMock(return_value=browser_snapshot),
        ) as browser:
            actual = await crawl_page_with_browser_fallback(ctx, profile_url, intent="profile")

        self.assertEqual(actual, browser_snapshot)
        browser.assert_awaited_once()

    async def test_crawl_page_with_browser_fallback_keeps_http_snapshot_when_browser_fails(self) -> None:
        ctx = CrawlToolContext(
            job_id=1,
            start_url="https://faculty.sdu.edu.cn/wanglingyun1/zh_CN/index.htm",
            university="山东大学",
            school="集成电路学院",
            session_factory=_FakeSessionFactory(),  # type: ignore[arg-type]
        )
        http_snapshot = PageSnapshot(
            url=ctx.start_url,
            title="山东大学教师主页 王凌云 首页 中文主页",
            text="王凌云 研究员 个人简介",
            html='<span _tsites_encrypt_field="_tsites_encrypt_field">ciphertext</span>',
            links=[],
            fetch_method="http",
            status="succeeded",
        )
        browser_snapshot = PageSnapshot(
            url=ctx.start_url,
            links=[],
            fetch_method="browser",
            status="failed",
            error_message="Playwright browser fetch failed: timeout",
            suspicious_empty=True,
        )

        async def fail_browser(*_args: object, **_kwargs: object) -> PageSnapshot:
            ctx.remember_page_snapshot(browser_snapshot)
            return browser_snapshot

        with patch(
            "app.modules.crawler.pages.tools.crawl_page_with_http",
            new=AsyncMock(return_value=http_snapshot),
        ), patch(
            "app.modules.crawler.pages.tools.browser_investigate",
            new=AsyncMock(side_effect=fail_browser),
        ) as browser:
            actual = await crawl_page_with_browser_fallback(ctx, ctx.start_url, intent="profile")
            cached = await crawl_page_with_browser_fallback(ctx, ctx.start_url, intent="profile")

        self.assertEqual(actual, http_snapshot)
        self.assertEqual(cached, http_snapshot)
        browser.assert_awaited_once()

    async def test_browser_failure_after_http_redirect_does_not_poison_requested_url(self) -> None:
        request_url = "https://faculty.example.edu/go"
        final_url = "https://faculty.example.edu/wang"
        async with _RealCrawlerSessionHarness() as harness:
            job_id = await harness.create_job()
            direct = PageSnapshot(
                url=final_url,
                title="王老师",
                text="王老师 研究员 个人简介",
                html='<span _tsites_encrypt_field="_tsites_encrypt_field">ciphertext</span>',
                links=[],
                fetch_method="http",
                status="succeeded",
            )
            browser = PageSnapshot(
                url=request_url,
                links=[],
                fetch_method="browser",
                status="failed",
                error_message="Blocked by anti-bot protection",
                suspicious_empty=True,
            )
            first_ctx = CrawlToolContext(
                job_id=job_id,
                start_url=request_url,
                university="示例大学",
                school="计算机学院",
                session_factory=harness.session_factory,
            )
            restarted_ctx = CrawlToolContext(
                job_id=job_id,
                start_url=request_url,
                university="示例大学",
                school="计算机学院",
                session_factory=harness.session_factory,
            )

            with patch(
                "app.modules.crawler.pages.tools.crawl_page_with_http",
                new=AsyncMock(return_value=direct),
            ), patch(
                "app.modules.crawler.pages.tools._crawl_page_with_browser",
                new=AsyncMock(return_value=browser),
            ):
                first = await crawl_page_with_browser_fallback(first_ctx, request_url, intent="profile")
                cached = await crawl_page_with_browser_fallback(first_ctx, request_url, intent="profile")
                second = await crawl_page_with_browser_fallback(restarted_ctx, request_url, intent="profile")

            async with harness.session_factory() as session:
                requested_state = await session.scalar(
                    select(CrawlPageFetchState).where(
                        CrawlPageFetchState.job_id == job_id,
                        CrawlPageFetchState.normalized_url == request_url,
                    )
                )

        self.assertEqual(first, direct)
        self.assertEqual(cached, direct)
        self.assertEqual(second, direct)
        assert requested_state is not None
        self.assertEqual(requested_state.status, "succeeded")
        self.assertEqual(requested_state.fetch_mode, "direct")
        self.assertEqual(requested_state.browser_status, "failed")

    async def test_crawl_page_with_browser_fallback_retries_browser_for_dynamic_teacher_directory(self) -> None:
        ctx = CrawlToolContext(
            job_id=1,
            start_url="https://software.fudan.edu.cn/zzjs/list.htm",
            university="复旦大学",
            school="软件学院",
            session_factory=_FakeSessionFactory(),  # type: ignore[arg-type]
        )
        http_snapshot = PageSnapshot(
            url=ctx.start_url,
            title="在职教师",
            text="师资队伍 在职教师 教授",
            html="""
            <html><body class="teacher" id="zzjs">
              <div class="teachers-list">
                <ul class="teacher_list career_list">
                  <li><div class="title zc">教授</div><div class="type_info clearfix"></div></li>
                </ul>
              </div>
              <script src="/_upload/tpl/0d/27/3367/template3367/js/search_teacher.js"></script>
            </body></html>
            """,
            links=[],
            fetch_method="http",
            status="succeeded",
        )
        browser_snapshot = PageSnapshot(
            url=ctx.start_url,
            title="在职教师",
            text="在职教师 教授 赵文耘",
            html="<html><body><a href='/b5/cd/c29336a308685/page.htm'>赵文耘</a></body></html>",
            links=["https://software.fudan.edu.cn/b5/cd/c29336a308685/page.htm"],
            fetch_method="browser",
            status="succeeded",
        )

        with patch(
            "app.modules.crawler.pages.tools.crawl_page_with_http",
            new=AsyncMock(return_value=http_snapshot),
        ), patch(
            "app.modules.crawler.pages.tools.browser_investigate",
            new=AsyncMock(return_value=browser_snapshot),
        ) as browser:
            actual = await crawl_page_with_browser_fallback(ctx, ctx.start_url)

        self.assertEqual(actual, browser_snapshot)
        browser.assert_awaited_once()

    def test_dynamic_directory_detection_recognizes_empty_main_content_list(self) -> None:
        snapshot = PageSnapshot(
            url="https://ioip.nankai.edu.cn/qtjs/list.htm",
            title="全体教师",
            text="页面导航和栏目标题仍然存在",
            html="""
            <html><body class="list">
              <div id="l-container" class="wrapper">
                <div class="col_news_con" id="zxj">
                  <div class="col_news_list listcon">
                    <ul class="news_list list2 clearfix"></ul>
                  </div>
                </div>
              </div>
            </body></html>
            """,
            links=[],
            fetch_method="http",
            status="succeeded",
        )

        self.assertTrue(
            crawler_tools.looks_like_unrendered_dynamic_teacher_directory(snapshot)
        )

    def test_dynamic_directory_detection_ignores_decorative_empty_lists(self) -> None:
        snapshot = PageSnapshot(
            url="https://example.edu/page",
            title="普通内容页",
            text="普通内容",
            html="""
            <html><body>
              <main id="content">
                <p>普通内容</p>
                <div class="carousel slider"><ul class="dots-list"></ul></div>
              </main>
              <nav><ul class="menu-list"></ul></nav>
            </body></html>
            """,
            links=[],
            fetch_method="http",
            status="succeeded",
        )

        self.assertFalse(
            crawler_tools.looks_like_unrendered_dynamic_teacher_directory(snapshot)
        )

    def test_dynamic_directory_detection_ignores_empty_optional_groups_with_populated_peers(self) -> None:
        snapshot = PageSnapshot(
            url="https://sice.bupt.edu.cn/szdw1.htm",
            title="师资队伍",
            text="泛网无线教研中心 张三 李四",
            html="""
            <html><body><main class="article-con">
              <div class="list_li">
                <ul class="fr ul_list"><li><a href="/zhang">张三</a></li></ul>
              </div>
              <div class="list_li">
                <ul class="fr ul_list"></ul>
              </div>
            </main></body></html>
            """,
            links=["https://sice.bupt.edu.cn/zhang"],
            fetch_method="browser",
            status="succeeded",
        )

        self.assertFalse(
            crawler_tools.looks_like_unrendered_dynamic_teacher_directory(snapshot)
        )

    async def test_dynamic_directory_browser_waits_until_content_is_stable(self) -> None:
        empty_html = """
        <html><body><main class="content">
          <ul class="teacher-list"></ul>
        </main></body></html>
        """
        ready_html = """
        <html><body><main class="content">
          <ul class="teacher-list"><li><a href="/zhang">张三</a></li></ul>
        </main></body></html>
        """
        page = SimpleNamespace(
            url="https://example.edu/faculty",
            content=AsyncMock(
                side_effect=[empty_html, ready_html, ready_html, ready_html]
            ),
            wait_for_timeout=AsyncMock(),
        )
        options = crawler_tools.BrowserFetchOptions(
            wait_for_dynamic_directory=True,
            dynamic_directory_ready_timeout_ms=1000,
            dynamic_directory_ready_poll_ms=100,
            dynamic_directory_stable_ms=200,
        )

        html, ready = await crawler_tools._wait_for_dynamic_directory_html(
            page,
            absolute_url=page.url,
            options=options,
        )

        self.assertTrue(ready)
        self.assertEqual(html, ready_html)
        self.assertEqual(page.wait_for_timeout.await_count, 3)

    async def test_dynamic_directory_browser_wait_times_out_while_list_is_empty(self) -> None:
        empty_html = """
        <html><body><main class="content">
          <ul class="teacher-list"></ul>
        </main></body></html>
        """
        page = SimpleNamespace(
            url="https://example.edu/faculty",
            content=AsyncMock(return_value=empty_html),
            wait_for_timeout=AsyncMock(),
        )
        options = crawler_tools.BrowserFetchOptions(
            wait_for_dynamic_directory=True,
            dynamic_directory_ready_timeout_ms=200,
            dynamic_directory_ready_poll_ms=100,
            dynamic_directory_stable_ms=100,
        )

        html, ready = await crawler_tools._wait_for_dynamic_directory_html(
            page,
            absolute_url=page.url,
            options=options,
        )

        self.assertFalse(ready)
        self.assertEqual(html, empty_html)
        self.assertEqual(page.wait_for_timeout.await_count, 2)

    async def test_dynamic_directory_browser_keeps_richest_rendered_html(self) -> None:
        rich_html = """
        <html><body><main>
          <a href="/zhang">张三</a><a href="/li">李四</a>
        </main></body></html>
        """
        sparse_html = "<html><body><main></main></body></html>"
        page = SimpleNamespace(
            url="https://example.edu/faculty",
            content=AsyncMock(side_effect=[rich_html, sparse_html, sparse_html]),
            wait_for_timeout=AsyncMock(),
        )
        options = crawler_tools.BrowserFetchOptions(
            wait_for_dynamic_directory=True,
            dynamic_directory_ready_timeout_ms=200,
            dynamic_directory_ready_poll_ms=100,
            dynamic_directory_stable_ms=300,
        )

        html, ready = await crawler_tools._wait_for_dynamic_directory_html(
            page,
            absolute_url=page.url,
            options=options,
        )

        self.assertFalse(ready)
        self.assertEqual(html, rich_html)

    def test_dynamic_profile_browser_waits_for_meaningful_stable_content(self) -> None:
        async def run() -> None:
            shell_html = "<html><body><main id='profile'></main></body></html>"
            ready_html = """
            <html><body><main id="profile">
              <h1>张吉良</h1><p>zhangjiliang@hnu.edu.cn</p>
            </main></body></html>
            """
            page = SimpleNamespace(
                url="https://example.edu/profile/zhang",
                content=AsyncMock(
                    side_effect=[shell_html, ready_html, ready_html, ready_html]
                ),
                wait_for_timeout=AsyncMock(),
            )
            options = crawler_tools.BrowserFetchOptions(
                wait_for_dynamic_profile=True,
                dynamic_profile_ready_timeout_ms=1000,
                dynamic_profile_ready_poll_ms=200,
                dynamic_profile_stable_ms=400,
            )

            html, ready = await crawler_tools._wait_for_dynamic_profile_html(
                page,
                absolute_url=page.url,
                options=options,
            )

            self.assertTrue(ready)
            self.assertEqual(html, ready_html)
            self.assertEqual(page.wait_for_timeout.await_count, 3)

        asyncio.run(run())

    def test_dynamic_profile_timeout_returns_richest_observed_html(self) -> None:
        async def run() -> None:
            rich_html = "<html><body><main>张三的个人简介与研究方向</main></body></html>"
            empty_html = "<html><body><main></main></body></html>"
            page = SimpleNamespace(
                url="https://example.edu/profile/zhang",
                content=AsyncMock(side_effect=[rich_html, empty_html, empty_html]),
                wait_for_timeout=AsyncMock(),
            )
            options = crawler_tools.BrowserFetchOptions(
                wait_for_dynamic_profile=True,
                dynamic_profile_ready_timeout_ms=200,
                dynamic_profile_ready_poll_ms=100,
                dynamic_profile_stable_ms=100,
            )

            html, ready = await crawler_tools._wait_for_dynamic_profile_html(
                page,
                absolute_url=page.url,
                options=options,
            )

            self.assertFalse(ready)
            self.assertEqual(html, rich_html)
            self.assertEqual(page.wait_for_timeout.await_count, 2)

        asyncio.run(run())

    def test_profile_meaningful_content_accepts_email_or_substantial_text(self) -> None:
        self.assertFalse(
            crawler_tools.profile_text_has_meaningful_content("首页 导航 版权所有")
        )
        self.assertTrue(
            crawler_tools.profile_text_has_meaningful_content(
                "张三 zhang@example.edu"
            )
        )
        self.assertTrue(
            crawler_tools.profile_text_has_meaningful_content("个人资料" * 80)
        )

    def test_playwright_dynamic_directory_timeout_keeps_available_page_succeeded(self) -> None:
        async def run() -> None:
            html = """
            <html><head><title>Faculty directory</title></head><body>
              <main class="content">
                <p>Directory navigation remains available.</p>
                <ul class="teacher-list"></ul>
              </main>
            </body></html>
            """
            content_calls = 0

            class _Page:
                url = "https://example.edu/faculty"

                async def goto(self, url: str, *, wait_until: str, timeout: int) -> None:
                    self.url = url

                async def wait_for_selector(self, selector: str, *, timeout: int) -> None:
                    return None

                async def wait_for_timeout(self, timeout: float) -> None:
                    return None

                async def content(self) -> str:
                    nonlocal content_calls
                    content_calls += 1
                    return html

            class _Context:
                async def new_page(self) -> _Page:
                    return _Page()

            class _Browser:
                async def new_context(self, **kwargs: object) -> _Context:
                    return _Context()

                async def close(self) -> None:
                    return None

            class _Chromium:
                async def launch(self, **kwargs: object) -> _Browser:
                    return _Browser()

            class _Playwright:
                chromium = _Chromium()

                async def __aenter__(self) -> "_Playwright":
                    return self

                async def __aexit__(self, *args: object) -> None:
                    return None

            options = crawler_tools.BrowserFetchOptions(
                wait_for_dynamic_directory=True,
                dynamic_directory_ready_timeout_ms=200,
                dynamic_directory_ready_poll_ms=100,
                dynamic_directory_stable_ms=100,
            )
            with patch("app.modules.crawler.pages.tools.async_playwright", return_value=_Playwright()):
                snapshot = await crawler_tools._try_playwright_browser_fetch_once(
                    "https://example.edu/faculty",
                    options,
                )

            self.assertEqual(snapshot.status, "succeeded")
            self.assertIsNone(snapshot.error_message)
            self.assertIn("Directory navigation remains available", snapshot.text)
            self.assertEqual(content_calls, 3)

        asyncio.run(run())

    async def test_crawl_page_with_browser_fallback_retries_browser_for_site_error_page(self) -> None:
        ctx = CrawlToolContext(
            job_id=1,
            start_url="http://sim.jxufe.edu.cn/#/staff/detail/5",
            university="江西财经大学",
            school="计算机与人工智能学院",
            session_factory=_FakeSessionFactory(),  # type: ignore[arg-type]
        )
        http_snapshot = PageSnapshot(
            url=ctx.start_url,
            title="江西财经大学",
            text="FineCMS error\nError Number: 1064\nSQL syntax",
            html="<html>FineCMS error</html>",
            links=[],
            fetch_method="http",
            status="succeeded",
        )
        browser_snapshot = PageSnapshot(
            url=ctx.start_url,
            title="教师详情",
            text="李四\n副教授\n邮箱：li@example.edu",
            html="<html>李四</html>",
            links=[],
            fetch_method="browser",
            status="succeeded",
        )

        with patch(
            "app.modules.crawler.pages.tools.crawl_page_with_http",
            new=AsyncMock(return_value=http_snapshot),
        ), patch(
            "app.modules.crawler.pages.tools.browser_investigate",
            new=AsyncMock(return_value=browser_snapshot),
        ) as browser:
            actual = await crawl_page_with_browser_fallback(ctx, ctx.start_url, intent="profile")

        self.assertEqual(actual, browser_snapshot)
        browser.assert_awaited_once()

    async def test_crawl_page_with_browser_fallback_does_not_use_site_specific_profile_api(self) -> None:
        ctx = CrawlToolContext(
            job_id=1,
            start_url="http://sim.jxufe.edu.cn/#/staff/detail/5",
            university="江西财经大学",
            school="计算机与人工智能学院",
            session_factory=_FakeSessionFactory(),  # type: ignore[arg-type]
        )
        requested_urls: list[str] = []

        class FakeAsyncClient:
            def __init__(self, *args: object, **kwargs: object) -> None:
                _ = args, kwargs

            async def __aenter__(self) -> "FakeAsyncClient":
                return self

            async def __aexit__(self, *args: object) -> None:
                _ = args

            async def get(self, url: str, *args: object, **kwargs: object) -> httpx.Response:
                _ = args, kwargs
                requested_urls.append(url)
                return httpx.Response(
                    200,
                    json={
                        "code": 200,
                        "data": {
                            "name": "万常选",
                            "birthday": "1962-07",
                            "researchDirection": "数据挖掘与知识工程、Web数据管理与信息检索",
                            "content": "<p>E-mail：wanchangxuan@263.net</p>",
                        },
                    },
                    request=httpx.Request("GET", url),
                )

        with patch(
            "app.modules.crawler.pages.tools.httpx.AsyncClient",
            new=FakeAsyncClient,
        ), patch(
            "app.modules.crawler.pages.tools.crawl_page_with_http",
            new=AsyncMock(
                return_value=PageSnapshot(
                    url=ctx.start_url,
                    title="信息管理与数学学院",
                    text="",
                    html="<html><div id='app'></div></html>",
                    links=[],
                    fetch_method="http",
                    status="succeeded",
                    suspicious_empty=True,
                ),
            ),
        ) as http_fetch, patch(
            "app.modules.crawler.pages.tools.browser_investigate",
            new=AsyncMock(
                return_value=PageSnapshot(
                    url=ctx.start_url,
                    title="江西财经大学",
                    text="FineCMS error",
                    html="<html>FineCMS error</html>",
                    links=[],
                    fetch_method="browser",
                    status="succeeded",
                ),
            ),
        ) as browser:
            actual = await crawl_page_with_browser_fallback(ctx, ctx.start_url, intent="profile")

        self.assertEqual(requested_urls, [])
        self.assertEqual(actual.fetch_method, "browser")
        self.assertEqual(actual.text, "FineCMS error")
        http_fetch.assert_awaited_once()
        browser.assert_awaited_once()

    async def test_playwright_browser_fetch_offloads_to_thread_on_windows_selector_loop(self) -> None:
        ctx = CrawlToolContext(
            job_id=1,
            start_url="https://example.edu/faculty",
            university="示例大学",
            school="计算机学院",
            session_factory=_FakeSessionFactory(),  # type: ignore[arg-type]
        )
        expected = PageSnapshot(
            url="https://example.edu/faculty",
            title="faculty",
            text="ok",
            html="<html></html>",
            links=[],
            fetch_method="browser",
            status="succeeded",
        )

        with (
            patch(
                "app.modules.crawler.pages.tools._is_resolved_allowed_crawl_url",
                return_value=True,
            ),
            patch(
                "app.modules.crawler.pages.tools._should_offload_browser_fetch_to_thread",
                return_value=True,
            ),
            patch(
                "app.modules.crawler.pages.tools.asyncio.to_thread",
                new=AsyncMock(return_value=expected),
            ) as to_thread,
        ):
            actual = await _crawl_page_with_browser(
                ctx,
                "https://example.edu/faculty",
                "提取导师信息",
            )

        self.assertEqual(actual, expected)
        self.assertEqual(to_thread.await_count, 1)

    async def test_playwright_browser_fetch_runs_inline_without_thread_on_supported_loop(self) -> None:
        ctx = CrawlToolContext(
            job_id=1,
            start_url="https://example.edu/faculty",
            university="示例大学",
            school="计算机学院",
            session_factory=_FakeSessionFactory(),  # type: ignore[arg-type]
        )
        expected = PageSnapshot(
            url="https://example.edu/faculty",
            title="faculty",
            text="ok",
            html="<html></html>",
            links=[],
            fetch_method="browser",
            status="succeeded",
        )

        async def fake_direct(absolute_url: str, goal: str, intent: str = "generic") -> PageSnapshot:
            self.assertEqual(absolute_url, "https://example.edu/faculty")
            self.assertEqual(goal, "提取导师信息")
            self.assertEqual(intent, "generic")
            return expected

        with (
            patch(
                "app.modules.crawler.pages.tools._is_resolved_allowed_crawl_url",
                return_value=True,
            ),
            patch(
                "app.modules.crawler.pages.tools._should_offload_browser_fetch_to_thread",
                return_value=False,
            ),
            patch(
                "app.modules.crawler.pages.tools._fetch_page_with_playwright_direct",
                new=fake_direct,
            ),
            patch("app.modules.crawler.pages.tools.asyncio.to_thread", new=AsyncMock()) as to_thread,
        ):
            actual = await _crawl_page_with_browser(
                ctx,
                "https://example.edu/faculty",
                "提取导师信息",
            )

        self.assertEqual(actual, expected)
        self.assertEqual(to_thread.await_count, 0)

    async def test_shared_save_rejects_listing_page_url_without_email(self) -> None:
        async with _RealCrawlerSessionHarness() as harness:
            job_id = await harness.create_job()
            ctx = CrawlToolContext(
                job_id=job_id,
                start_url="https://cs.example.edu/faculty",
                university="示例大学",
                school="计算机学院",
                session_factory=harness.session_factory,
            )

            result = await save_candidate_payloads_shared(
                ctx,
                [
                    ProfessorCandidatePayload(
                        name="张三",
                        profile_url="https://cs.example.edu/faculty#teachers",
                    )
                ],
            )

            self.assertEqual(result["saved_count"], 0)
            self.assertEqual(result["rejected_count"], 1)
            self.assertIn("缺少邮箱和详情页链接", result["rejected_items"][0]["reason"])
            self.assertEqual(await harness.count_rows(CrawlCandidate), 0)

    async def test_crawl_page_with_browser_fallback_raises_when_job_is_canceled_before_fetch(self) -> None:
        session_factory = _FakeSessionFactory(job_status="canceled")
        ctx = CrawlToolContext(
            job_id=1,
            start_url="https://cs.example.edu/faculty",
            university="示例大学",
            school="计算机学院",
            session_factory=session_factory,  # type: ignore[arg-type]
        )
        snapshot = PageSnapshot(
            url="https://cs.example.edu/faculty",
            title="Faculty",
            text="教师名录 张三 教授",
            html="<html></html>",
            links=[],
            fetch_method="http",
            status="succeeded",
        )

        with patch(
            "app.modules.crawler.pages.tools.crawl_page_with_http",
            new=AsyncMock(return_value=snapshot),
        ) as mocked_http, patch(
            "app.modules.crawler.pages.tools.browser_investigate",
            new=AsyncMock(return_value=snapshot),
        ) as mocked_browser:
            with self.assertRaises(CrawlJobCanceled):
                await crawl_page_with_browser_fallback(ctx, "https://cs.example.edu/faculty")

        mocked_http.assert_not_called()
        mocked_browser.assert_not_called()

    async def test_browser_investigate_raises_when_job_is_canceled_before_fetch(self) -> None:
        session_factory = _FakeSessionFactory(job_status="canceled")
        ctx = CrawlToolContext(
            job_id=1,
            start_url="https://cs.example.edu/faculty",
            university="示例大学",
            school="计算机学院",
            session_factory=session_factory,  # type: ignore[arg-type]
        )
        snapshot = PageSnapshot(
            url="https://cs.example.edu/faculty/zhang",
            title="张三",
            text="张三 教授",
            html="<html></html>",
            links=[],
            fetch_method="browser",
            status="succeeded",
        )

        with patch(
            "app.modules.crawler.pages.tools._crawl_page_with_browser",
            new=AsyncMock(return_value=snapshot),
        ) as mocked_browser:
            with self.assertRaises(CrawlJobCanceled):
                await crawler_tools.browser_investigate(
                    ctx,
                    "https://cs.example.edu/faculty/zhang",
                    goal="提取导师主页",
                )

        mocked_browser.assert_not_called()

    async def test_record_page_snapshot_skips_canceled_job(self) -> None:
        session_factory = _FakeSessionFactory(job_status="canceled")
        ctx = CrawlToolContext(
            job_id=1,
            start_url="https://cs.example.edu/faculty",
            university="示例大学",
            school="计算机学院",
            session_factory=session_factory,  # type: ignore[arg-type]
        )

        row = await record_page_snapshot(
            ctx,
            PageSnapshot(
                url="https://cs.example.edu/faculty",
                title="Faculty",
                text="Faculty page",
                fetch_method="http",
                status="succeeded",
            ),
        )

        self.assertIsNone(row)
        self.assertEqual(session_factory.added, [])

    async def test_record_page_snapshot_skips_paused_job(self) -> None:
        session_factory = _FakeSessionFactory(job_status="paused")
        ctx = CrawlToolContext(
            job_id=1,
            start_url="https://cs.example.edu/faculty",
            university="示例大学",
            school="计算机学院",
            session_factory=session_factory,  # type: ignore[arg-type]
        )

        row = await record_page_snapshot(
            ctx,
            PageSnapshot(
                url="https://cs.example.edu/faculty",
                title="Faculty",
                text="Faculty page",
                fetch_method="http",
                status="succeeded",
            ),
        )

        self.assertIsNone(row)
        self.assertEqual(session_factory.added, [])

    async def test_record_page_snapshot_rolls_back_when_job_is_canceled_before_commit(self) -> None:
        session_factory = _FakeSessionFactory(job_statuses=["running", "canceled"])
        ctx = CrawlToolContext(
            job_id=1,
            start_url="https://cs.example.edu/faculty",
            university="示例大学",
            school="计算机学院",
            session_factory=session_factory,  # type: ignore[arg-type]
        )

        row = await record_page_snapshot(
            ctx,
            PageSnapshot(
                url="https://cs.example.edu/faculty",
                title="Faculty",
                text="Faculty page",
                fetch_method="http",
                status="succeeded",
            ),
        )

        self.assertIsNone(row)
        self.assertEqual(session_factory.added, [])
        self.assertEqual(session_factory.rollback_count, 1)

    async def test_record_page_snapshot_sees_canceled_status_changed_by_other_session(self) -> None:
        async with _RealCrawlerSessionHarness() as harness:
            job_id = await harness.create_job()
            ctx = CrawlToolContext(
                job_id=job_id,
                start_url="https://cs.example.edu/faculty",
                university="示例大学",
                school="计算机学院",
                session_factory=harness.cancel_on_second_status_factory(job_id),  # type: ignore[arg-type]
            )

            row = await record_page_snapshot(
                ctx,
                PageSnapshot(
                    url="https://cs.example.edu/faculty",
                    title="Faculty",
                    text="Faculty page",
                    fetch_method="http",
                    status="succeeded",
                ),
            )

            self.assertIsNone(row)
            self.assertEqual(await harness.count_rows(CrawlPage), 0)

    async def test_crawl_page_with_http_rejects_cross_host_final_url(self) -> None:
        session_factory = _FakeSessionFactory()
        ctx = CrawlToolContext(
            job_id=1,
            start_url="https://cs.example.edu/faculty",
            university="示例大学",
            school="计算机学院",
            session_factory=session_factory,  # type: ignore[arg-type]
        )
        response = _FakeHttpResponse(
            url="https://evil.example.net/people/a",
            text="<html><body>外域正文</body></html>",
        )

        with patch(
            "app.modules.crawler.pages.tools.socket.getaddrinfo",
            return_value=[
                (0, 0, 0, "", ("93.184.216.34", 443)),
            ],
        ), patch("app.modules.crawler.pages.tools.httpx.AsyncClient") as client_class:
            client = client_class.return_value.__aenter__.return_value
            client.get.return_value = response

            snapshot = await crawl_page_with_http(ctx, "https://cs.example.edu/faculty")

        self.assertEqual(snapshot.status, "failed")
        self.assertIn("最终 URL 不在允许范围内", snapshot.error_message or "")
        self.assertNotIn("外域正文", snapshot.text)

        self.assertEqual(len(session_factory.added), 1)
        recorded = session_factory.added[0]
        self.assertEqual(recorded.status, "failed")
        self.assertIsNone(recorded.text_excerpt)

    async def test_crawl_page_with_http_rejects_unsafe_final_url(self) -> None:
        session_factory = _FakeSessionFactory()
        ctx = CrawlToolContext(
            job_id=1,
            start_url="https://faculty.example.edu/faculty",
            university="示例大学",
            school="计算机学院",
            session_factory=session_factory,  # type: ignore[arg-type]
        )
        response = _FakeHttpResponse(
            url="http://127.0.0.1/admin",
            text="<html><body>本机正文</body></html>",
        )

        with patch(
            "app.modules.crawler.pages.tools.socket.getaddrinfo",
            return_value=[
                (0, 0, 0, "", ("93.184.216.34", 443)),
            ],
        ), patch("app.modules.crawler.pages.tools.httpx.AsyncClient") as client_class:
            client = client_class.return_value.__aenter__.return_value
            client.get.return_value = response

            snapshot = await crawl_page_with_http(ctx, "https://faculty.example.edu/faculty")

        self.assertEqual(snapshot.status, "failed")
        self.assertIn("URL 不允许指向本机、内网或不可解析地址", snapshot.error_message or "")
        self.assertNotIn("本机正文", snapshot.text)

    async def test_crawl_page_with_http_allows_same_host_redirect_even_if_system_dns_is_private(self) -> None:
        session_factory = _FakeSessionFactory()
        ctx = CrawlToolContext(
            job_id=1,
            start_url="https://faculty.example.edu/faculty",
            university="示例大学",
            school="计算机学院",
            session_factory=session_factory,  # type: ignore[arg-type]
        )
        response = _FakeHttpResponse(
            url="https://faculty.example.edu/private",
            text="<html><body>内网正文</body></html>",
        )
        public_dns = [(0, 0, 0, "", ("93.184.216.34", 443))]
        private_dns = [(0, 0, 0, "", ("10.0.0.1", 443))]

        with patch(
            "app.modules.crawler.pages.tools.socket.getaddrinfo",
            side_effect=[public_dns, public_dns, public_dns, public_dns, private_dns],
        ), patch("app.modules.crawler.pages.tools.httpx.AsyncClient") as client_class:
            client = client_class.return_value.__aenter__.return_value
            client.get.return_value = response

            snapshot = await crawl_page_with_http(ctx, "https://faculty.example.edu/faculty")

        self.assertEqual(snapshot.status, "succeeded")
        self.assertIn("内网正文", snapshot.text)

    async def test_crawl_page_with_http_does_not_request_unsafe_redirect_target(self) -> None:
        session_factory = _FakeSessionFactory()
        ctx = CrawlToolContext(
            job_id=1,
            start_url="https://faculty.example.edu/faculty",
            university="示例大学",
            school="计算机学院",
            session_factory=session_factory,  # type: ignore[arg-type]
        )
        requested_urls: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requested_urls.append(str(request.url))
            if str(request.url) == "https://faculty.example.edu/faculty":
                return httpx.Response(
                    302,
                    headers={"Location": "http://127.0.0.1/admin"},
                    request=request,
                )
            return httpx.Response(200, text="unsafe target was requested", request=request)

        transport = httpx.MockTransport(handler)
        async_client = httpx.AsyncClient

        def client_factory(**kwargs: object) -> httpx.AsyncClient:
            kwargs.pop("transport", None)
            return async_client(transport=transport, **kwargs)

        with patch(
            "app.modules.crawler.pages.tools.socket.getaddrinfo",
            return_value=[
                (0, 0, 0, "", ("93.184.216.34", 443)),
            ],
        ), patch("app.modules.crawler.pages.tools.httpx.AsyncClient", side_effect=client_factory):
            snapshot = await crawl_page_with_http(ctx, "https://faculty.example.edu/faculty")

        self.assertEqual(snapshot.status, "failed")
        self.assertIn("URL 不允许指向本机、内网或不可解析地址", snapshot.error_message or "")
        self.assertEqual(requested_urls, ["https://faculty.example.edu/faculty"])

    async def test_crawl_page_with_http_uses_validated_transport_without_env_proxy(self) -> None:
        session_factory = _FakeSessionFactory()
        ctx = CrawlToolContext(
            job_id=1,
            start_url="https://faculty.example.edu/faculty",
            university="示例大学",
            school="计算机学院",
            session_factory=session_factory,  # type: ignore[arg-type]
        )
        client_kwargs: list[dict[str, object]] = []
        response = _FakeHttpResponse(
            url="https://faculty.example.edu/faculty",
            text="<html><body>Faculty page</body></html>",
        )

        def client_factory(**kwargs: object) -> "_FakeAsyncHttpClient":
            client_kwargs.append(kwargs)
            return _FakeAsyncHttpClient(response)

        with patch(
            "app.modules.crawler.pages.tools.socket.getaddrinfo",
            return_value=[
                (0, 0, 0, "", ("93.184.216.34", 443)),
            ],
        ), patch("app.modules.crawler.pages.tools.httpx.AsyncClient", side_effect=client_factory):
            snapshot = await crawl_page_with_http(ctx, "https://faculty.example.edu/faculty")

        self.assertEqual(snapshot.status, "succeeded")
        self.assertGreaterEqual(len(client_kwargs), 1)
        for kwargs in client_kwargs:
            self.assertIs(kwargs.get("trust_env"), False)
            self.assertIn("transport", kwargs)

    async def test_crawl_page_with_http_connects_to_validated_ip_not_rebound_hostname(
        self,
    ) -> None:
        session_factory = _FakeSessionFactory()
        ctx = CrawlToolContext(
            job_id=1,
            start_url="https://faculty.example.edu/faculty",
            university="示例大学",
            school="计算机学院",
            session_factory=session_factory,  # type: ignore[arg-type]
        )
        backend = _RecordingNetworkBackend(
            response_bytes=(
                b"HTTP/1.1 200 OK\r\n"
                b"Content-Type: text/html; charset=utf-8\r\n"
                b"Content-Length: 38\r\n"
                b"\r\n"
                b"<html><body>Faculty page</body></html>"
            )
        )

        with patch(
            "app.modules.crawler.pages.tools.socket.getaddrinfo",
            return_value=[
                (0, 0, 0, "", ("93.184.216.34", 443)),
            ],
        ), patch(
            "app.modules.crawler.pages.tools._default_async_network_backend",
            return_value=backend,
        ):
            snapshot = await crawl_page_with_http(ctx, "https://faculty.example.edu/faculty")

        self.assertEqual(snapshot.status, "succeeded")
        self.assertEqual(backend.connect_calls, [("93.184.216.34", 443)])
        self.assertNotIn(("faculty.example.edu", 443), backend.connect_calls)
        self.assertEqual(backend.streams[0].tls_server_hostnames, ["faculty.example.edu"])

    async def test_crawl_page_with_http_uses_system_resolved_public_ip_for_domain_fetching(
        self,
    ) -> None:
        session_factory = _FakeSessionFactory()
        ctx = CrawlToolContext(
            job_id=1,
            start_url="https://faculty.example.edu/faculty",
            university="示例大学",
            school="计算机学院",
            session_factory=session_factory,  # type: ignore[arg-type]
        )
        backend = _RecordingNetworkBackend(
            response_bytes=(
                b"HTTP/1.1 200 OK\r\n"
                b"Content-Type: text/html; charset=utf-8\r\n"
                b"Content-Length: 38\r\n"
                b"\r\n"
                b"<html><body>Faculty page</body></html>"
            )
        )

        with patch(
            "app.modules.crawler.pages.tools.socket.getaddrinfo",
            return_value=[
                (0, 0, 0, "", ("93.184.216.34", 443)),
            ],
        ), patch(
            "app.modules.crawler.pages.tools._default_async_network_backend",
            return_value=backend,
        ):
            snapshot = await crawl_page_with_http(ctx, "https://faculty.example.edu/faculty")

        self.assertEqual(snapshot.status, "succeeded")
        self.assertEqual(backend.connect_calls, [("93.184.216.34", 443)])
        self.assertEqual(backend.streams[0].tls_server_hostnames, ["faculty.example.edu"])

    async def test_crawl_page_with_http_filters_same_host_links_without_dns_per_link(
        self,
    ) -> None:
        session_factory = _FakeSessionFactory()
        ctx = CrawlToolContext(
            job_id=1,
            start_url="https://faculty.example.edu/faculty",
            university="示例大学",
            school="计算机学院",
            session_factory=session_factory,  # type: ignore[arg-type]
        )
        links_html = "".join(
            f'<a href="/people/{index}">导师 {index}</a>'
            for index in range(20)
        )
        response = _FakeHttpResponse(
            url="https://faculty.example.edu/faculty",
            text=f"<html><body>{links_html}</body></html>",
        )
        dns_call_count = 0

        def getaddrinfo(*args: object, **kwargs: object) -> list[tuple[int, int, int, str, tuple[str, int]]]:
            nonlocal dns_call_count
            _ = args, kwargs
            dns_call_count += 1
            return [(0, 0, 0, "", ("93.184.216.34", 443))]

        with patch(
            "app.modules.crawler.pages.tools.socket.getaddrinfo",
            side_effect=getaddrinfo,
        ), patch("app.modules.crawler.pages.tools.httpx.AsyncClient") as client_class:
            client = client_class.return_value.__aenter__.return_value
            client.get.return_value = response

            snapshot = await crawl_page_with_http(ctx, "https://faculty.example.edu/faculty")

        self.assertEqual(snapshot.status, "succeeded")
        self.assertEqual(len(snapshot.links), 20)
        self.assertEqual(dns_call_count, 1)

    async def test_crawl_page_with_http_re_resolves_and_rebinds_each_redirect_hop(
        self,
    ) -> None:
        session_factory = _FakeSessionFactory()
        ctx = CrawlToolContext(
            job_id=1,
            start_url="https://faculty.example.edu/faculty",
            university="示例大学",
            school="计算机学院",
            session_factory=session_factory,  # type: ignore[arg-type]
        )
        backend = _RecordingNetworkBackend(
            response_bytes=[
                b"HTTP/1.1 302 Found\r\n"
                b"Location: /people\r\n"
                b"Content-Length: 0\r\n"
                b"\r\n",
                b"HTTP/1.1 200 OK\r\n"
                b"Content-Type: text/html; charset=utf-8\r\n"
                b"Content-Length: 38\r\n"
                b"\r\n"
                b"<html><body>Faculty page</body></html>",
            ]
        )

        def resolve_current_public_ip(
            *args: object,
            **kwargs: object,
        ) -> list[tuple[int, int, int, str, tuple[str, int]]]:
            _ = args, kwargs
            if len(backend.connect_calls) == 0:
                return [(0, 0, 0, "", ("93.184.216.34", 443))]
            return [(0, 0, 0, "", ("93.184.216.35", 443))]

        with patch(
            "app.modules.crawler.pages.tools.socket.getaddrinfo",
            side_effect=resolve_current_public_ip,
        ), patch(
            "app.modules.crawler.pages.tools._default_async_network_backend",
            return_value=backend,
        ):
            snapshot = await crawl_page_with_http(ctx, "https://faculty.example.edu/faculty")

        self.assertEqual(snapshot.status, "succeeded")
        self.assertEqual(
            backend.connect_calls,
            [("93.184.216.34", 443), ("93.184.216.35", 443)],
        )

    async def test_safe_crawl_transport_connects_to_validated_ip_and_preserves_https_host_semantics(
        self,
    ) -> None:
        backend = _RecordingNetworkBackend(
            response_bytes=b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\nOK"
        )
        transport = crawler_tools._build_safe_crawl_transport(
            hostname="faculty.example.edu",
            resolved_ip="93.184.216.34",
            network_backend=backend,
        )

        async with httpx.AsyncClient(transport=transport, trust_env=False) as client:
            response = await client.get("https://faculty.example.edu/faculty")

        self.assertEqual(response.text, "OK")
        self.assertEqual(backend.connect_calls, [("93.184.216.34", 443)])
        self.assertEqual(backend.streams[0].tls_server_hostnames, ["faculty.example.edu"])
        request_bytes = b"".join(backend.streams[0].writes)
        self.assertIn(b"GET /faculty HTTP/1.1", request_bytes)
        self.assertIn(b"Host: faculty.example.edu", request_bytes)

    async def test_crawl_page_with_browser_fallback_delegates_to_safe_http_path(self) -> None:
        session_factory = _FakeSessionFactory()
        ctx = CrawlToolContext(
            job_id=1,
            start_url="https://faculty.example.edu/faculty",
            university="示例大学",
            school="计算机学院",
            session_factory=session_factory,  # type: ignore[arg-type]
        )
        expected_snapshot = PageSnapshot(
            url="https://faculty.example.edu/faculty",
            text="Faculty page",
            fetch_method="http",
            status="succeeded",
        )

        async def safe_http_path(
            delegated_ctx: CrawlToolContext,
            delegated_url: str,
        ) -> PageSnapshot:
            self.assertIs(delegated_ctx, ctx)
            self.assertEqual(delegated_url, "https://faculty.example.edu/faculty")
            return expected_snapshot

        with patch(
            "app.modules.crawler.pages.tools.socket.getaddrinfo",
            return_value=[
                (0, 0, 0, "", ("93.184.216.34", 443)),
            ],
        ), patch(
            "app.modules.crawler.pages.tools.crawl_page_with_http",
            side_effect=safe_http_path,
        ) as http_path:
            snapshot = await crawl_page_with_browser_fallback(ctx, "https://faculty.example.edu/faculty")

        self.assertIs(snapshot, expected_snapshot)
        self.assertEqual(http_path.call_count, 1)

    async def test_crawl_page_with_browser_fallback_falls_back_to_browser_on_empty_content(self) -> None:
        session_factory = _FakeSessionFactory()
        ctx = CrawlToolContext(
            job_id=1,
            start_url="https://faculty.example.edu/faculty",
            university="University",
            school="School",
            session_factory=session_factory,  # type: ignore[arg-type]
        )
        empty_http_snapshot = PageSnapshot(
            url="https://faculty.example.edu/faculty",
            text="",
            html="",
            fetch_method="http",
            status="succeeded",
            suspicious_empty=True,
        )
        browser_snapshot = PageSnapshot(
            url="https://faculty.example.edu/faculty",
            text="Faculty page",
            html="<html><body><table><tr><td>mock</td></tr></table></body></html>",
            fetch_method="browser",
            status="succeeded",
        )

        with patch(
            "app.modules.crawler.pages.tools.crawl_page_with_http",
            return_value=empty_http_snapshot,
        ) as http_path, patch(
            "app.modules.crawler.pages.tools._crawl_page_with_browser",
            return_value=browser_snapshot,
        ) as browser_path:
            snapshot = await crawl_page_with_browser_fallback(ctx, "https://faculty.example.edu/faculty")

        self.assertIs(snapshot, browser_snapshot)
        self.assertEqual(http_path.call_count, 1)
        self.assertEqual(browser_path.call_count, 1)

    async def test_crawl_page_with_browser_fallback_falls_back_to_browser_on_blocked_http_status(self) -> None:
        session_factory = _FakeSessionFactory()
        ctx = CrawlToolContext(
            job_id=1,
            start_url="https://faculty.example.edu/faculty",
            university="University",
            school="School",
            session_factory=session_factory,  # type: ignore[arg-type]
        )
        blocked_http_snapshot = PageSnapshot(
            url="https://faculty.example.edu/faculty",
            text="blocked marker",
            html="<html><body>blocked</body></html>",
            fetch_method="http",
            status="succeeded",
            suspicious_empty=True,
            error_message="HTTP 412 blocked, browser fallback advised",
        )
        browser_snapshot = PageSnapshot(
            url="https://faculty.example.edu/faculty",
            text="Faculty page",
            html="<html><body><table><tr><td>mock</td></tr></table></body></html>",
            fetch_method="browser",
            status="succeeded",
        )

        with patch(
            "app.modules.crawler.pages.tools.crawl_page_with_http",
            return_value=blocked_http_snapshot,
        ) as http_path, patch(
            "app.modules.crawler.pages.tools._crawl_page_with_browser",
            return_value=browser_snapshot,
        ) as browser_path:
            snapshot = await crawl_page_with_browser_fallback(ctx, "https://faculty.example.edu/faculty")

        self.assertIs(snapshot, browser_snapshot)
        self.assertEqual(http_path.call_count, 1)
        self.assertEqual(browser_path.call_count, 1)

    async def test_crawl_page_with_browser_fallback_skips_http_for_host_after_blocked_status(self) -> None:
        session_factory = _FakeSessionFactory()
        ctx = CrawlToolContext(
            job_id=1,
            start_url="https://teacher.example.edu/list",
            university="University",
            school="School",
            session_factory=session_factory,  # type: ignore[arg-type]
        )
        blocked_http_snapshot = PageSnapshot(
            url="https://teacher.example.edu/a",
            text="blocked",
            html="<html><body>blocked</body></html>",
            fetch_method="http",
            status="failed",
            suspicious_empty=True,
            error_message="HTTP 412 blocked, browser fallback advised",
        )
        first_browser_snapshot = PageSnapshot(
            url="https://teacher.example.edu/a",
            text="Profile A",
            html="<html><body>Profile A</body></html>",
            fetch_method="browser",
            status="succeeded",
        )
        second_browser_snapshot = PageSnapshot(
            url="https://teacher.example.edu/b",
            text="Profile B",
            html="<html><body>Profile B</body></html>",
            fetch_method="browser",
            status="succeeded",
        )

        with patch(
            "app.modules.crawler.pages.tools.crawl_page_with_http",
            return_value=blocked_http_snapshot,
        ) as http_path, patch(
            "app.modules.crawler.pages.tools._crawl_page_with_browser",
            side_effect=[first_browser_snapshot, second_browser_snapshot],
        ) as browser_path:
            first = await crawl_page_with_browser_fallback(ctx, "https://teacher.example.edu/a")
            second = await crawl_page_with_browser_fallback(ctx, "https://teacher.example.edu/b")

        self.assertIs(first, first_browser_snapshot)
        self.assertIs(second, second_browser_snapshot)
        self.assertEqual(http_path.call_count, 1)
        self.assertEqual(browser_path.call_count, 2)

    async def test_crawl_page_with_browser_fallback_keeps_blocked_hosts_scoped_by_host(self) -> None:
        session_factory = _FakeSessionFactory()
        ctx = CrawlToolContext(
            job_id=1,
            start_url="https://teacher.example.edu/list",
            university="University",
            school="School",
            session_factory=session_factory,  # type: ignore[arg-type]
        )
        blocked_http_snapshot = PageSnapshot(
            url="https://teacher.example.edu/a",
            text="blocked",
            html="<html><body>blocked</body></html>",
            fetch_method="http",
            status="failed",
            suspicious_empty=True,
            error_message="HTTP 412 blocked, browser fallback advised",
        )
        other_http_snapshot = PageSnapshot(
            url="https://profile.example.edu/b",
            text="Profile B",
            html="<html><body>Profile B</body></html>",
            fetch_method="http",
            status="succeeded",
        )
        browser_snapshot = PageSnapshot(
            url="https://teacher.example.edu/a",
            text="Profile A",
            html="<html><body>Profile A</body></html>",
            fetch_method="browser",
            status="succeeded",
        )

        with patch(
            "app.modules.crawler.pages.tools.crawl_page_with_http",
            side_effect=[blocked_http_snapshot, other_http_snapshot],
        ) as http_path, patch(
            "app.modules.crawler.pages.tools._crawl_page_with_browser",
            return_value=browser_snapshot,
        ) as browser_path:
            first = await crawl_page_with_browser_fallback(ctx, "https://teacher.example.edu/a")
            second = await crawl_page_with_browser_fallback(ctx, "https://profile.example.edu/b")

        self.assertIs(first, browser_snapshot)
        self.assertIs(second, other_http_snapshot)
        self.assertEqual(http_path.call_count, 2)
        self.assertEqual(browser_path.call_count, 1)

    async def test_browser_investigate_uses_playwright_browser(self) -> None:
        session_factory = _FakeSessionFactory()
        ctx = CrawlToolContext(
            job_id=1,
            start_url="https://faculty.example.edu/faculty",
            university="University",
            school="School",
            session_factory=session_factory,  # type: ignore[arg-type]
        )
        browser_snapshot = PageSnapshot(
            url="https://faculty.example.edu/faculty",
            text="Faculty page",
            html="<html></html>",
            fetch_method="browser",
            status="succeeded",
        )

        with patch(
            "app.modules.crawler.pages.tools._crawl_page_with_browser",
            return_value=browser_snapshot,
        ) as browser_path:
            snapshot = await crawler_tools.browser_investigate(
                ctx,
                "https://faculty.example.edu/faculty",
                "table",
            )

        self.assertIs(snapshot, browser_snapshot)
        self.assertEqual(browser_path.call_count, 1)

    async def test_browser_investigate_force_fetch_bypasses_processed_ledger(self) -> None:
        async with _RealCrawlerSessionHarness() as harness:
            job_id = await harness.create_job()
            url = "https://cs.example.edu/faculty"
            async with harness.session_factory() as session:
                session.add(
                    CrawlPageFetchState(
                        job_id=job_id,
                        normalized_url=url,
                        original_url=url,
                        status="processed",
                        fetch_mode="direct",
                        direct_status="succeeded",
                    )
                )
                await session.commit()
            ctx = CrawlToolContext(
                job_id=job_id,
                start_url=url,
                university="示例大学",
                school="计算机学院",
                session_factory=harness.session_factory,
            )
            browser_snapshot = PageSnapshot(
                url=url,
                text="张三",
                html="<html><body>张三</body></html>",
                fetch_method="browser",
                status="succeeded",
            )

            with patch(
                "app.modules.crawler.pages.tools._crawl_page_with_browser",
                new=AsyncMock(return_value=browser_snapshot),
            ) as browser_path, patch(
                "app.modules.crawler.pages.tools._has_unsafe_public_crawl_url",
                return_value=False,
            ), patch(
                "app.modules.crawler.pages.tools.is_allowed_crawl_url",
                return_value=True,
            ):
                snapshot = await crawler_tools.browser_investigate(
                    ctx,
                    url,
                    goal="",
                    force_fetch=True,
                )

        self.assertEqual(snapshot.fetch_method, "browser")
        browser_path.assert_awaited_once()

    async def test_browser_investigate_skips_previously_denied_url(self) -> None:
        ctx = CrawlToolContext(
            job_id=1,
            start_url="https://cs.example.edu/faculty/index.htm",
            university="测试大学",
            school="计算机学院",
            session_factory=_FakeSessionFactory(),  # type: ignore[arg-type]
        )
        ctx.mark_denied_url("https://cs.example.edu/news/a.htm", "无关新闻页")

        with patch("app.modules.crawler.pages.tools._crawl_page_with_browser") as mocked_browser:
            snapshot = await crawler_tools.browser_investigate(
                ctx,
                "https://cs.example.edu/news/a.htm",
                "查找导师邮箱",
            )

        mocked_browser.assert_not_called()
        self.assertEqual(snapshot.status, "failed")
        self.assertEqual(snapshot.links, [])
        self.assertIn("已在本轮抓取中判定为无关页面", snapshot.error_message or "")

    async def test_browser_investigate_returns_succeeded_page_for_agent_classification(self) -> None:
        ctx = CrawlToolContext(
            job_id=1,
            start_url="https://cs.example.edu/faculty/index.htm",
            university="测试大学",
            school="计算机学院",
            session_factory=_FakeSessionFactory(),  # type: ignore[arg-type]
        )
        browser_snapshot = PageSnapshot(
            url="https://cs.example.edu/news/a.htm",
            title="通知公告",
            text="关于本科招生宣传会议的通知",
            html="<html><body><a href='/news/b.htm'>下一篇</a></body></html>",
            links=["https://cs.example.edu/news/b.htm"],
            fetch_method="browser",
            status="succeeded",
        )

        with patch(
            "app.modules.crawler.pages.tools._crawl_page_with_browser",
            return_value=browser_snapshot,
        ):
            snapshot = await crawler_tools.browser_investigate(
                ctx,
                "https://cs.example.edu/news/a.htm",
                "查找导师邮箱",
            )

        self.assertEqual(snapshot.status, "succeeded")
        self.assertEqual(snapshot.links, ["https://cs.example.edu/news/b.htm"])
        self.assertFalse(ctx.is_denied_url("https://cs.example.edu/news/a.htm"))
        self.assertIsNone(snapshot.error_message)

    async def test_save_candidate_payloads_preserves_profile_entry_start_url(self) -> None:
        profile_url = "https://example.edu/teacher/zhang.html"
        async with _RealCrawlerSessionHarness() as harness:
            async with harness.session_factory() as session:
                job = CrawlJob(
                    university="示例大学",
                    school="计算机学院",
                    start_url=profile_url,
                    start_urls=[profile_url],
                    status=CrawlJobStatus.RUNNING.value,
                    entry_type="profile",
                )
                session.add(job)
                await session.commit()
                await session.refresh(job)
                job_id = job.id

            ctx = CrawlToolContext(
                job_id=job_id,
                start_url=profile_url,
                university="示例大学",
                school="计算机学院",
                session_factory=harness.session_factory,
                entry_type="profile",
            )
            result = await save_candidate_payloads_shared(
                ctx,
                [
                    ProfessorCandidatePayload(
                        name="张三",
                        profile_url=profile_url,
                        source_url=profile_url,
                        source_kind="profile_page",
                        confidence=0.9,
                    )
                ],
            )

            self.assertEqual(result["saved_count"], 1)
            async with harness.session_factory() as session:
                saved = await session.scalar(select(CrawlCandidate).where(CrawlCandidate.job_id == job_id))
            assert saved is not None
            self.assertEqual(saved.profile_url, profile_url)

    async def test_concurrent_candidate_saves_are_deduplicated(self) -> None:
        async with _RealCrawlerSessionHarness() as harness:
            job_id = await harness.create_job()
            ctx = CrawlToolContext(
                job_id=job_id,
                start_url="https://cs.example.edu/faculty",
                university="示例大学",
                school="计算机学院",
                session_factory=harness.session_factory,
            )

            results = await asyncio.gather(
                save_candidate_payloads_shared(
                    ctx,
                    [
                        ProfessorCandidatePayload(
                            name="张三",
                            email="ZHANG@example.edu",
                            title="教授",
                        )
                    ],
                ),
                save_candidate_payloads_shared(
                    ctx,
                    [
                        ProfessorCandidatePayload(
                            name="张三",
                            email="zhang@example.edu",
                            department="计算机系",
                        )
                    ],
                ),
            )

            self.assertEqual(await harness.count_rows(CrawlCandidate), 2)
            async with harness.session_factory() as session:
                candidates = list(
                    await session.scalars(
                        select(CrawlCandidate)
                        .where(CrawlCandidate.job_id == job_id)
                        .order_by(CrawlCandidate.id),
                    )
                )
            canonical = [
                candidate
                for candidate in candidates
                if candidate.merged_into_candidate_id is None
            ]
            self.assertEqual(len(canonical), 1)
            self.assertEqual(canonical[0].email.lower(), "zhang@example.edu")
            self.assertEqual(
                sum(
                    result["saved_count"]
                    + result["merged_count"]
                    + result["skipped_duplicate_count"]
                    for result in results
                ),
                2,
            )

    async def test_related_internal_and_external_profile_payloads_use_existing_merge(self) -> None:
        listing_url = "https://school.example.edu/faculty"
        external_url = "https://sites.example.net/view/guo"
        internal_url = (
            "https://school.example.edu/detail?name=guo&"
            "redirect=https%3A%2F%2Fsites.example.net%2Fview%2Fguo"
        )
        async with _RealCrawlerSessionHarness() as harness:
            job_id = await harness.create_job()
            ctx = CrawlToolContext(
                job_id=job_id,
                start_url=listing_url,
                university="示例大学",
                school="计算机学院",
                session_factory=harness.session_factory,
            )

            first = await save_candidate_payloads_shared(
                ctx,
                [
                    ProfessorCandidatePayload(
                        name="郭晓杰",
                        profile_url=external_url,
                        source_url=listing_url,
                    )
                ],
            )
            second = await save_candidate_payloads_shared(
                ctx,
                [
                    ProfessorCandidatePayload(
                        name="郭晓杰",
                        profile_url=internal_url,
                        source_url=listing_url,
                    )
                ],
            )

            async with harness.session_factory() as session:
                rows = list(
                    await session.scalars(
                        select(CrawlCandidate)
                        .where(CrawlCandidate.job_id == job_id)
                        .order_by(CrawlCandidate.id)
                    )
                )

        canonical = [row for row in rows if row.merged_into_candidate_id is None]
        self.assertEqual(first["saved_count"], 1)
        self.assertEqual(second["saved_count"], 0)
        self.assertEqual(second["merged_count"], 1)
        self.assertEqual(len(canonical), 1)
        self.assertEqual(canonical[0].profile_url, internal_url)

    async def test_save_candidate_payloads_rejects_listing_entry_start_url_without_email(self) -> None:
        listing_url = "https://example.edu/faculty"
        async with _RealCrawlerSessionHarness() as harness:
            async with harness.session_factory() as session:
                job = CrawlJob(
                    university="示例大学",
                    school="计算机学院",
                    start_url=listing_url,
                    start_urls=[listing_url],
                    status=CrawlJobStatus.RUNNING.value,
                    entry_type="list",
                )
                session.add(job)
                await session.commit()
                await session.refresh(job)
                job_id = job.id

            ctx = CrawlToolContext(
                job_id=job_id,
                start_url=listing_url,
                university="示例大学",
                school="计算机学院",
                session_factory=harness.session_factory,
                entry_type="list",
            )
            result = await save_candidate_payloads_shared(
                ctx,
                [
                    ProfessorCandidatePayload(
                        name="张三",
                        profile_url=listing_url,
                        source_url=listing_url,
                        confidence=0.9,
                    )
                ],
            )

            self.assertEqual(result["saved_count"], 0)
            self.assertEqual(result["rejected_count"], 1)
            self.assertIn("缺少邮箱和详情页链接", result["rejected_items"][0]["reason"])
            async with harness.session_factory() as session:
                saved = await session.scalar(select(CrawlCandidate).where(CrawlCandidate.job_id == job_id))
            self.assertIsNone(saved)

    async def test_playwright_browser_fetch_disables_chromium_https_upgrades_and_automation_controlled(self) -> None:
        launches: list[dict[str, object]] = []
        context_kwargs: list[dict[str, object]] = []

        class _Page:
            url = "http://teacher.example.edu/zhoufeng"

            async def goto(self, url: str, *, wait_until: str, timeout: int) -> None:
                self.url = url
                self.wait_until = wait_until
                self.timeout = timeout

            async def wait_for_selector(self, selector: str, *, timeout: int) -> None:
                self.selector = selector
                self.selector_timeout = timeout

            async def wait_for_timeout(self, timeout: float) -> None:
                self.delay = timeout

            async def content(self) -> str:
                return "<html><body>周锋 电子邮箱：zfeng@bupt.edu.cn</body></html>"

        class _Context:
            async def new_page(self) -> _Page:
                return _Page()

        class _Browser:
            async def new_context(self, **kwargs: object) -> _Context:
                context_kwargs.append(kwargs)
                return _Context()

            async def close(self) -> None:
                return None

        class _Chromium:
            async def launch(self, **kwargs: object) -> _Browser:
                launches.append(kwargs)
                return _Browser()

        class _Playwright:
            chromium = _Chromium()

            async def __aenter__(self) -> "_Playwright":
                return self

            async def __aexit__(self, *args: object) -> None:
                return None

        with patch(
            "app.modules.crawler.pages.tools.async_playwright",
            return_value=_Playwright(),
        ):
            snapshot = await crawler_tools._fetch_page_with_playwright_direct(
                "http://teacher.example.edu/zhoufeng",
                "",
                "profile",
            )

        self.assertEqual(snapshot.status, "succeeded")
        self.assertIn("--disable-features=HttpsUpgrades", launches[0]["args"])
        self.assertIn("--disable-blink-features=AutomationControlled", launches[0]["args"])
        self.assertIn("Chrome/124.0.0.0", context_kwargs[0]["user_agent"])
        self.assertIn("zfeng@bupt.edu.cn", snapshot.text)

    async def test_playwright_browser_fetch_retries_without_wait_selector_after_wait_failure(self) -> None:
        calls: list[str | None] = []

        class _Page:
            url = "https://teacher.example.edu/zhoufeng"

            async def goto(self, url: str, *, wait_until: str, timeout: int) -> None:
                self.url = url

            async def wait_for_selector(self, selector: str, *, timeout: int) -> None:
                calls.append(selector)
                if len(calls) == 1:
                    raise TimeoutError("Wait condition failed: Timeout after 15000ms waiting for selector 'body'")

            async def wait_for_timeout(self, timeout: float) -> None:
                return None

            async def content(self) -> str:
                return "<html><body>周锋 电子邮箱：zfeng@bupt.edu.cn</body></html>"

        class _Context:
            async def new_page(self) -> _Page:
                return _Page()

        class _Browser:
            async def new_context(self, **kwargs: object) -> _Context:
                return _Context()

            async def close(self) -> None:
                return None

        class _Chromium:
            async def launch(self, **kwargs: object) -> _Browser:
                return _Browser()

        class _Playwright:
            chromium = _Chromium()

            async def __aenter__(self) -> "_Playwright":
                return self

            async def __aexit__(self, *args: object) -> None:
                return None

        with patch("app.modules.crawler.pages.tools.async_playwright", return_value=_Playwright()):
            snapshot = await crawler_tools._fetch_page_with_playwright_direct(
                "https://teacher.example.edu/zhoufeng",
                "",
                "profile",
            )

        self.assertEqual(snapshot.status, "succeeded")
        self.assertEqual(calls, ["body"])
        self.assertIn("zfeng@bupt.edu.cn", snapshot.text)

    async def test_playwright_browser_fetch_retries_without_wait_selector_after_playwright_selector_timeout(self) -> None:
        calls: list[str | None] = []

        class _Page:
            url = "https://teacher.example.edu/zhoufeng"

            async def goto(self, url: str, *, wait_until: str, timeout: int) -> None:
                self.url = url

            async def wait_for_selector(self, selector: str, *, timeout: int) -> None:
                calls.append(selector)
                if len(calls) == 1:
                    raise TimeoutError("Page.wait_for_selector: Timeout 50ms exceeded.")

            async def wait_for_timeout(self, timeout: float) -> None:
                return None

            async def content(self) -> str:
                return "<html><body>周锋 电子邮箱：zfeng@bupt.edu.cn</body></html>"

        class _Context:
            async def new_page(self) -> _Page:
                return _Page()

        class _Browser:
            async def new_context(self, **kwargs: object) -> _Context:
                return _Context()

            async def close(self) -> None:
                return None

        class _Chromium:
            async def launch(self, **kwargs: object) -> _Browser:
                return _Browser()

        class _Playwright:
            chromium = _Chromium()

            async def __aenter__(self) -> "_Playwright":
                return self

            async def __aexit__(self, *args: object) -> None:
                return None

        with patch("app.modules.crawler.pages.tools.async_playwright", return_value=_Playwright()):
            snapshot = await crawler_tools._fetch_page_with_playwright_direct(
                "https://teacher.example.edu/zhoufeng",
                "",
                "profile",
            )

        self.assertEqual(snapshot.status, "succeeded")
        self.assertEqual(calls, ["body"])
        self.assertIn("zfeng@bupt.edu.cn", snapshot.text)

    async def test_playwright_browser_fetch_retries_transient_browser_failure(self) -> None:
        attempts = 0

        class _Page:
            url = "https://teacher.example.edu/zhoufeng"

            async def goto(self, url: str, *, wait_until: str, timeout: int) -> None:
                nonlocal attempts
                attempts += 1
                self.url = url
                if attempts == 1:
                    raise TimeoutError("Page.goto: Timeout 50ms exceeded.")

            async def wait_for_selector(self, selector: str, *, timeout: int) -> None:
                return None

            async def wait_for_timeout(self, timeout: float) -> None:
                return None

            async def content(self) -> str:
                return "<html><body>周锋 电子邮箱：zfeng@bupt.edu.cn</body></html>"

        class _Context:
            async def new_page(self) -> _Page:
                return _Page()

        class _Browser:
            async def new_context(self, **kwargs: object) -> _Context:
                return _Context()

            async def close(self) -> None:
                return None

        class _Chromium:
            async def launch(self, **kwargs: object) -> _Browser:
                return _Browser()

        class _Playwright:
            chromium = _Chromium()

            async def __aenter__(self) -> "_Playwright":
                return self

            async def __aexit__(self, *args: object) -> None:
                return None

        with patch("app.modules.crawler.pages.tools.async_playwright", return_value=_Playwright()):
            snapshot = await crawler_tools._fetch_page_with_playwright_direct(
                "https://teacher.example.edu/zhoufeng",
                "",
                "profile",
            )

        self.assertEqual(snapshot.status, "succeeded")
        self.assertEqual(attempts, 2)
        self.assertIn("zfeng@bupt.edu.cn", snapshot.text)

    async def test_playwright_browser_pagination_retries_transient_browser_failure(self) -> None:
        failed = crawler_tools.BrowserPaginationExpansion(
            status="failed",
            stopped_reason="browser_error",
            error_message="Page.goto: net::ERR_CONNECTION_CLOSED",
        )
        succeeded = crawler_tools.BrowserPaginationExpansion(
            status="succeeded",
            stopped_reason="control_disabled",
        )

        with patch(
            "app.modules.crawler.pages.tools._try_fetch_browser_pagination_once",
            new=AsyncMock(side_effect=[failed, succeeded]),
        ) as attempt_mock:
            result = await crawler_tools._fetch_browser_pagination_direct(
                "https://example.edu/directory",
                {
                    "tag": "a",
                    "text": "下一页",
                    "title": "",
                    "ariaLabel": "",
                    "classTokens": ["Next"],
                    "matchIndex": 0,
                },
                intent="directory",
                max_pages=20,
            )

        self.assertEqual(result.status, "succeeded")
        self.assertEqual(attempt_mock.await_count, 2)

    async def test_playwright_browser_pagination_waits_for_dynamic_directory_before_matching_control(self) -> None:
        events: list[str] = []

        class _Page:
            url = "https://example.edu/directory"

            async def goto(self, url: str, *, wait_until: str, timeout: int) -> None:
                self.url = url
                events.append("goto")

            async def wait_for_selector(self, selector: str, *, timeout: int) -> None:
                events.append("selector")

            async def content(self) -> str:
                events.append("content")
                return '<html><body><a href="/profile">张三</a></body></html>'

            async def evaluate(self, script: str, argument: object = None) -> object:
                events.append("match")
                return {"index": 0, "disabled": True}

        class _Context:
            async def new_page(self) -> _Page:
                return _Page()

        class _Browser:
            async def new_context(self, **kwargs: object) -> _Context:
                return _Context()

            async def close(self) -> None:
                return None

        class _Chromium:
            async def launch(self, **kwargs: object) -> _Browser:
                return _Browser()

        class _Playwright:
            chromium = _Chromium()

            async def __aenter__(self) -> "_Playwright":
                return self

            async def __aexit__(self, *args: object) -> None:
                return None

        async def wait_for_directory(page: _Page, **kwargs: object) -> tuple[str, bool]:
            events.append("directory_ready")
            return await page.content(), True

        with (
            patch("app.modules.crawler.pages.tools.async_playwright", return_value=_Playwright()),
            patch(
                "app.modules.crawler.pages.tools._wait_for_dynamic_directory_html",
                new=wait_for_directory,
            ),
        ):
            result = await crawler_tools._try_fetch_browser_pagination_once(
                "https://example.edu/directory",
                {
                    "tag": "li",
                    "text": "",
                    "title": "下一页",
                    "ariaLabel": "图标: right",
                    "classTokens": ["pagination-next"],
                    "matchIndex": 0,
                },
                intent="directory",
                max_pages=2,
            )

        self.assertEqual(result.status, "succeeded")
        self.assertEqual(result.stopped_reason, "control_disabled")
        self.assertLess(events.index("directory_ready"), events.index("match"))

    async def test_playwright_browser_pagination_retries_unchanged_click(self) -> None:
        click_count = 0

        class _Locator:
            def __init__(self, page: "_Page", selector: str) -> None:
                self.page = page
                self.selector = selector

            def nth(self, index: int) -> "_Locator":
                self.index = index
                return self

            async def inner_text(self) -> str:
                return f"page-{self.page.state}"

            async def click(self, *, timeout: int) -> None:
                nonlocal click_count
                click_count += 1
                if click_count >= 2:
                    self.page.state = 1

        class _Page:
            url = "https://example.edu/directory"
            state = 0

            async def goto(self, url: str, *, wait_until: str, timeout: int) -> None:
                self.url = url

            async def wait_for_selector(self, selector: str, *, timeout: int) -> None:
                return None

            async def wait_for_timeout(self, timeout: float) -> None:
                return None

            async def wait_for_load_state(self, state: str, *, timeout: int) -> None:
                return None

            async def content(self) -> str:
                return (
                    f'<html><body>page-{self.state}'
                    f'<a href="/profile-{self.state}">person-{self.state}</a>'
                    "</body></html>"
                )

            async def evaluate(self, script: str, argument: object = None) -> object:
                if argument is not None:
                    return {"index": 0, "disabled": False}
                return [f"https://example.edu/profile-{self.state} person-{self.state}"]

            def locator(self, selector: str) -> _Locator:
                return _Locator(self, selector)

        class _Context:
            async def new_page(self) -> _Page:
                return _Page()

        class _Browser:
            async def new_context(self, **kwargs: object) -> _Context:
                return _Context()

            async def close(self) -> None:
                return None

        class _Chromium:
            async def launch(self, **kwargs: object) -> _Browser:
                return _Browser()

        class _Playwright:
            chromium = _Chromium()

            async def __aenter__(self) -> "_Playwright":
                return self

            async def __aexit__(self, *args: object) -> None:
                return None

        with patch("app.modules.crawler.pages.tools.async_playwright", return_value=_Playwright()):
            result = await crawler_tools._try_fetch_browser_pagination_once(
                "https://example.edu/directory",
                {
                    "tag": "a",
                    "text": "下一页",
                    "title": "",
                    "ariaLabel": "",
                    "classTokens": ["Next"],
                    "matchIndex": 0,
                },
                intent="directory",
                max_pages=2,
            )

        self.assertEqual(result.status, "succeeded")
        self.assertEqual(len(result.snapshots), 1)
        self.assertEqual(click_count, 2)

    async def test_playwright_browser_fetch_uses_load_without_networkidle_retry(self) -> None:
        calls: list[str] = []

        class _Page:
            url = "https://scs.bupt.edu.cn/szjs1/jsyl.htm"

            async def goto(self, url: str, *, wait_until: str, timeout: int) -> None:
                calls.append(wait_until)
                if wait_until == "networkidle":
                    raise TimeoutError(
                        'Page.goto: Timeout 30000ms exceeded.\n'
                        'Call log:\n  - navigating to "https://scs.bupt.edu.cn/szjs1/jsyl.htm", waiting until "networkidle"'
                    )

            async def wait_for_selector(self, selector: str, *, timeout: int) -> None:
                return None

            async def wait_for_timeout(self, timeout: float) -> None:
                return None

            async def content(self) -> str:
                return "<html><body>教师一览 周锋 电子邮箱：zfeng@bupt.edu.cn</body></html>"

        class _Context:
            async def new_page(self) -> _Page:
                return _Page()

        class _Browser:
            async def new_context(self, **kwargs: object) -> _Context:
                return _Context()

            async def close(self) -> None:
                return None

        class _Chromium:
            async def launch(self, **kwargs: object) -> _Browser:
                return _Browser()

        class _Playwright:
            chromium = _Chromium()

            async def __aenter__(self) -> "_Playwright":
                return self

            async def __aexit__(self, *args: object) -> None:
                return None

        with patch("app.modules.crawler.pages.tools.async_playwright", return_value=_Playwright()):
            snapshot = await crawler_tools._fetch_page_with_playwright_direct(
                "https://scs.bupt.edu.cn/szjs1/jsyl.htm",
                "",
                "profile",
            )

        self.assertEqual(snapshot.status, "succeeded")
        self.assertEqual(calls, ["load"])
        self.assertIn("zfeng@bupt.edu.cn", snapshot.text)

class _FakeSessionFactory:
    def __init__(self, *, job_status: str = "running", job_statuses: list[str] | None = None) -> None:
        self.added: list[object] = []
        self._job_statuses = list(job_statuses or [job_status])
        self.rollback_count = 0

    def __call__(self) -> "_FakeSession":
        return _FakeSession(self)

    def next_job_status(self) -> str:
        if len(self._job_statuses) > 1:
            return self._job_statuses.pop(0)
        return self._job_statuses[0]


class _FakeSession:
    def __init__(self, factory: _FakeSessionFactory) -> None:
        self._factory = factory
        self._staged: list[object] = []

    async def __aenter__(self) -> "_FakeSession":
        return self

    async def __aexit__(self, *args: object) -> None:
        return None

    def add(self, row: object) -> None:
        self._staged.append(row)

    async def get(self, model: object, key: object) -> object:
        _ = model, key
        return _FakeJob(status=self._factory.next_job_status())

    async def scalar(self, statement: object) -> str:
        _ = statement
        return self._factory.next_job_status()

    async def scalars(self, statement: object) -> "_FakeScalarResult":
        _ = statement
        return _FakeScalarResult([])

    async def commit(self) -> None:
        self._factory.added.extend(self._staged)
        self._staged.clear()
        return None

    async def rollback(self) -> None:
        self._staged.clear()
        self._factory.rollback_count += 1

    async def refresh(self, row: object) -> None:
        return None


class _FakeScalarResult:
    def __init__(self, items: list[object]) -> None:
        self._items = items

    def __iter__(self):
        return iter(self._items)


class _FakeJob:
    def __init__(self, *, status: str) -> None:
        self.status = status
        self.start_url = "https://cs.example.edu/faculty"
        self.start_urls = [self.start_url]


class _RealCrawlerSessionHarness:
    def __init__(self) -> None:
        self._temp_dir: tempfile.TemporaryDirectory[str] | None = None
        self._session_factory: async_sessionmaker[AsyncSession] | None = None
        self._engine = None

    async def __aenter__(self) -> "_RealCrawlerSessionHarness":
        asyncio.get_running_loop().slow_callback_duration = 1.0
        self._temp_dir = tempfile.TemporaryDirectory()
        db_path = Path(self._temp_dir.name) / "crawler_tools.db"
        create_schema_sqlite_database(db_path)
        self._engine = create_async_engine(f"sqlite+aiosqlite:///{db_path.as_posix()}")
        self._session_factory = async_sessionmaker(
            bind=self._engine,
            autoflush=False,
            expire_on_commit=False,
        )
        return self

    async def __aexit__(self, *args: object) -> None:
        if self._engine is not None:
            await self._engine.dispose()
        if self._temp_dir is not None:
            self._temp_dir.cleanup()

    async def create_job(self) -> int:
        async with self._session_factory() as session:
            job = CrawlJob(
                university="示例大学",
                school="计算机学院",
                start_url="https://cs.example.edu/faculty",
                status=CrawlJobStatus.RUNNING.value,
                progress_current=0,
                progress_total=0,
            )
            session.add(job)
            await session.commit()
            await session.refresh(job)
            return job.id

    @property
    def session_factory(self) -> async_sessionmaker[AsyncSession]:
        assert self._session_factory is not None
        return self._session_factory

    def cancel_on_second_status_factory(self, job_id: int) -> "_CancelOnSecondStatusSessionFactory":
        return _CancelOnSecondStatusSessionFactory(self._session_factory, job_id)

    async def count_rows(self, model: object) -> int:
        async with self._session_factory() as session:
            rows = await session.scalars(model.__table__.select())
            return len(list(rows))


class _CancelOnSecondStatusSessionFactory:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession], job_id: int) -> None:
        self._session_factory = session_factory
        self._job_id = job_id

    def __call__(self) -> "_CancelOnSecondStatusSession":
        return _CancelOnSecondStatusSession(self._session_factory, self._job_id)


class _CancelOnSecondStatusSession:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession], job_id: int) -> None:
        self._session_factory = session_factory
        self._job_id = job_id
        self._session: AsyncSession | None = None
        self._status_read_count = 0
        self._cached_job: CrawlJob | None = None

    async def __aenter__(self) -> "_CancelOnSecondStatusSession":
        self._session = self._session_factory()
        await self._session.__aenter__()
        self._cached_job = await self._session.get(CrawlJob, self._job_id)
        return self

    async def __aexit__(self, *args: object) -> None:
        await self._session.__aexit__(*args)

    def add(self, row: object) -> None:
        self._session.add(row)

    def begin_nested(self):
        return self._session.begin_nested()

    async def get(self, model: object, key: object) -> object:
        if model is CrawlJob and key == self._job_id:
            await self._maybe_cancel_job()
        return await self._session.get(model, key)

    async def scalar(self, statement: object) -> object:
        await self._maybe_cancel_job()
        return await self._session.scalar(statement)

    async def scalars(self, statement: object) -> object:
        return await self._session.scalars(statement)

    async def commit(self) -> None:
        await self._session.commit()

    async def rollback(self) -> None:
        await self._session.rollback()

    async def refresh(self, row: object) -> None:
        await self._session.refresh(row)

    async def flush(self) -> None:
        await self._session.flush()

    async def _maybe_cancel_job(self) -> None:
        self._status_read_count += 1
        if self._status_read_count != 2:
            return
        async with self._session_factory() as session:
            job = await session.get(CrawlJob, self._job_id)
            job.status = CrawlJobStatus.CANCELED.value
            await session.commit()


class _FakeHttpResponse:
    def __init__(
        self,
        *,
        url: str,
        text: str,
        status_code: int = 200,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.url = url
        self.text = text
        self.status_code = status_code
        self.headers = headers or {}

    @property
    def is_redirect(self) -> bool:
        return 300 <= self.status_code < 400 and "location" in {
            key.lower() for key in self.headers
        }

    def raise_for_status(self) -> None:
        return None


class _FakeAsyncHttpClient:
    def __init__(self, response: _FakeHttpResponse) -> None:
        self._response = response

    async def __aenter__(self) -> "_FakeAsyncHttpClient":
        return self

    async def __aexit__(self, *args: object) -> None:
        return None

    async def get(self, url: str, *, headers: dict[str, str]) -> _FakeHttpResponse:
        _ = url, headers
        return self._response


class _RecordingNetworkBackend:
    def __init__(self, *, response_bytes: bytes | list[bytes]) -> None:
        self.connect_calls: list[tuple[str, int]] = []
        responses = response_bytes if isinstance(response_bytes, list) else [response_bytes]
        self.streams = [_RecordingNetworkStream(response) for response in responses]
        self._next_stream_index = 0

    async def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options: object | None = None,
    ) -> "_RecordingNetworkStream":
        _ = timeout, local_address, socket_options
        self.connect_calls.append((host, port))
        stream = self.streams[self._next_stream_index]
        if self._next_stream_index < len(self.streams) - 1:
            self._next_stream_index += 1
        return stream

    async def connect_unix_socket(
        self,
        path: str,
        timeout: float | None = None,
        socket_options: object | None = None,
    ) -> "_RecordingNetworkStream":
        _ = path, timeout, socket_options
        raise AssertionError("crawl transport must not use Unix sockets")

    async def sleep(self, seconds: float) -> None:
        _ = seconds
        return None


class _RecordingNetworkStream:
    def __init__(self, response_bytes: bytes) -> None:
        self._response_bytes = response_bytes
        self._read_offset = 0
        self.writes: list[bytes] = []
        self.tls_server_hostnames: list[str | None] = []

    async def read(self, max_bytes: int, timeout: float | None = None) -> bytes:
        _ = timeout
        if self._read_offset >= len(self._response_bytes):
            return b""
        chunk = self._response_bytes[self._read_offset : self._read_offset + max_bytes]
        self._read_offset += len(chunk)
        return chunk

    async def write(self, buffer: bytes, timeout: float | None = None) -> None:
        _ = timeout
        self.writes.append(buffer)

    async def aclose(self) -> None:
        return None

    async def start_tls(
        self,
        ssl_context: object,
        server_hostname: str | None = None,
        timeout: float | None = None,
    ) -> "_RecordingNetworkStream":
        _ = ssl_context, timeout
        self.tls_server_hostnames.append(server_hostname)
        return self

    def get_extra_info(self, info: str) -> object | None:
        _ = info
        return None


if __name__ == "__main__":
    unittest.main()
