from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models import CrawlCandidate, CrawlJob, CrawlJobStatus, CrawlPage, CrawlPageFetchState
from app.services.crawler_tools import (
    CrawlJobSaveBudgetExceeded,
    CrawlJobCanceled,
    CrawlJobPaused,
    CrawlToolContext,
    CandidateEnrichmentPayload,
    PageSnapshot,
    build_candidate_enrichment_prompt,
    build_profile_candidate_prompt,
    ProfessorCandidatePayload,
    extract_first_email_from_text,
    normalize_obfuscated_email_tokens,
    crawl_page_with_browser_fallback,
    crawl_page_with_http,
    is_allowed_crawl_url,
    is_safe_public_crawl_url,
    normalize_candidate_payload,
    normalize_candidate_profile_url,
    record_save_batch_failure,
    record_save_batch_success,
    record_page_snapshot,
    save_candidate_batch,
    save_candidate_payloads_shared,
    save_candidate_batch_fingerprint,
    save_candidates,
    _crawl_page_with_browser,
    _resolve_safe_public_crawl_url,
)
from app.services import crawler_tools
from test.schema_database import create_schema_sqlite_database


class CrawlerToolTests(unittest.TestCase):
    def _budget_test_ctx(self) -> CrawlToolContext:
        return CrawlToolContext(
            job_id=1,
            start_url="https://cs.example.edu/faculty",
            university="示例大学",
            school="计算机学院",
            session_factory=_FakeSessionFactory(),  # type: ignore[arg-type]
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
        ctx = self._budget_test_ctx()
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

    def test_save_candidate_batch_fingerprint_ignores_order_and_non_identity_fields(self) -> None:
        first = save_candidate_batch_fingerprint(
            [
                {
                    "name": " 张三 ",
                    "email": "ZHANG@EXAMPLE.EDU",
                    "profile_url": "https://example.edu/zhang",
                    "field_confidence": {"name": 0.2},
                    "evidence": {"summary": "第一次"},
                },
                ProfessorCandidatePayload(
                    name="李四",
                    email="li@example.edu",
                    profile_url="https://example.edu/li",
                    evidence={"summary": "页面"},
                ),
            ]
        )
        second = save_candidate_batch_fingerprint(
            [
                {
                    "name": "李四",
                    "email": "li@example.edu",
                    "profile_url": "https://example.edu/li",
                    "recent_papers": ["Paper A"],
                },
                {
                    "name": "张三",
                    "email": "zhang@example.edu",
                    "profile_url": "https://example.edu/zhang",
                    "field_confidence": {"name": 0.9},
                    "evidence": {"summary": "第二次"},
                },
            ]
        )

        self.assertEqual(first, second)
        self.assertEqual(len(first), 12)

    def test_page_snapshot_cache_evicts_lru_entries(self) -> None:
        ctx = self._budget_test_ctx()
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

        with patch("app.services.crawler_tools.MAX_PAGE_SNAPSHOT_CACHE_ENTRIES", 2, create=True):
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

    def test_record_save_batch_failure_trips_same_batch_limit_on_second_failure(self) -> None:
        ctx = self._budget_test_ctx()
        candidates = [{"name": "张三", "email": "zhang@example.edu"}]
        failed_items = [{"index": 0, "name": "张三", "reason": "name: Field required"}]

        first = record_save_batch_failure(ctx, candidates, failed_items)

        self.assertTrue(first["retry_allowed"])
        self.assertEqual(first["consecutive_same_batch_failures"], 1)
        self.assertEqual(first["total_save_failures"], 1)
        self.assertIsNone(first["terminal_reason"])

        with self.assertRaises(CrawlJobSaveBudgetExceeded) as raised:
            record_save_batch_failure(ctx, candidates, failed_items)

        self.assertIn("同一候选批次连续保存失败 2 次", str(raised.exception))
        self.assertEqual(raised.exception.same_batch_save_failures, 2)
        self.assertEqual(raised.exception.total_save_failures, 2)
        self.assertIn("name: Field required", raised.exception.latest_failure_summary)

    def test_record_save_batch_failure_trips_total_limit_on_fourth_distinct_batch(self) -> None:
        ctx = self._budget_test_ctx()

        for index in range(3):
            result = record_save_batch_failure(
                ctx,
                [{"name": f"老师{index}", "email": f"teacher{index}@example.edu"}],
                [{"index": 0, "name": f"老师{index}", "reason": "字段类型错误"}],
            )
            self.assertTrue(result["retry_allowed"])

        with self.assertRaises(CrawlJobSaveBudgetExceeded) as raised:
            record_save_batch_failure(
                ctx,
                [{"name": "老师4", "email": "teacher4@example.edu"}],
                [{"index": 0, "name": "老师4", "reason": "字段类型错误"}],
            )

        self.assertIn("候选保存失败累计达到 4 次", str(raised.exception))
        self.assertEqual(raised.exception.same_batch_save_failures, 1)
        self.assertEqual(raised.exception.total_save_failures, 4)

    def test_record_save_batch_success_clears_same_batch_counter_without_resetting_total(self) -> None:
        ctx = self._budget_test_ctx()
        record_save_batch_failure(
            ctx,
            [{"name": "张三", "email": "zhang@example.edu"}],
            [{"index": 0, "name": "张三", "reason": "字段类型错误"}],
        )

        record_save_batch_success(ctx)

        self.assertIsNone(ctx.save_failure_budget.last_failed_save_fingerprint)
        self.assertEqual(ctx.save_failure_budget.same_batch_save_failures, 0)
        self.assertEqual(ctx.save_failure_budget.total_save_failures, 1)
        self.assertIsNone(ctx.save_failure_budget.last_save_failure_summary)

    def test_browser_fetch_options_for_profile_uses_load_and_waits_for_body(self) -> None:
        options = crawler_tools._browser_fetch_options_for_intent("profile")

        self.assertEqual(options.wait_for, "css:body")
        self.assertEqual(options.wait_until, "load")
        self.assertEqual(options.wait_for_timeout_ms, 15000)
        self.assertEqual(options.page_timeout_ms, 30000)
        self.assertEqual(options.delay_before_return_html_seconds, 1.5)
        self.assertIn("Chrome/124.0.0.0", options.user_agent)

    def test_browser_fetch_options_for_generic_and_directory_use_load_and_wait_for_body(self) -> None:
        generic_options = crawler_tools._browser_fetch_options_for_intent("generic")
        directory_options = crawler_tools._browser_fetch_options_for_intent("directory")

        self.assertEqual(generic_options.wait_for, "css:body")
        self.assertEqual(directory_options.wait_for, "css:body")
        self.assertEqual(generic_options.wait_until, "load")
        self.assertEqual(directory_options.wait_until, "load")

    def test_playwright_launch_options_disable_chromium_https_upgrades_and_automation_controlled(self) -> None:
        options = crawler_tools._playwright_launch_options()

        args = options["args"]
        self.assertIn("--disable-features=HttpsUpgrades", args)
        self.assertIn("--disable-blink-features=AutomationControlled", args)
        self.assertTrue(options["headless"])
        self.assertNotIn("channel", options)

    def test_crawler_runtime_code_no_longer_mentions_legacy_fetch_backend(self) -> None:
        source = Path(crawler_tools.__file__).read_text(encoding="utf-8")
        legacy_name = "crawl" + "4ai"
        legacy_display_name = "Crawl" + "4AI"

        self.assertNotIn(legacy_name, source.lower())
        self.assertNotIn(legacy_display_name, source)

    def test_is_allowed_crawl_url_allows_same_host(self) -> None:
        with patch(
            "app.services.crawler_tools.socket.getaddrinfo",
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
            "app.services.crawler_tools.socket.getaddrinfo",
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
            "app.services.crawler_tools.socket.getaddrinfo",
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

    def test_is_allowed_crawl_url_allows_same_chinese_university_cn_domain(self) -> None:
        with patch(
            "app.services.crawler_tools.socket.getaddrinfo",
            return_value=[
                (0, 0, 0, "", ("93.184.216.34", 80)),
            ],
        ):
            self.assertTrue(
                is_allowed_crawl_url(
                    "https://cai.jxufe.edu.cn/lists/26.html",
                    "http://sim.jxufe.cn/static/JDMKL/ymfang.html",
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
            "app.services.crawler_tools.socket.getaddrinfo",
            side_effect=AssertionError("URL validation should not resolve domain names"),
        ):
            self.assertTrue(is_safe_public_crawl_url("https://faculty.example.edu"))

    def test_resolve_safe_public_crawl_url_uses_system_dns_for_fetching_domains(self) -> None:
        with patch(
            "app.services.crawler_tools.socket.getaddrinfo",
            return_value=[
                (0, 0, 0, "", ("100.64.0.42", 443)),
            ],
        ):
            resolved = _resolve_safe_public_crawl_url("https://faculty.example.edu")
            self.assertEqual(resolved.resolved_ips, ("100.64.0.42",))

    def test_is_safe_public_crawl_url_allows_domain_even_if_system_dns_would_be_private(self) -> None:
        with patch(
            "app.services.crawler_tools.socket.getaddrinfo",
            side_effect=AssertionError("URL validation should not resolve domain names"),
        ):
            self.assertTrue(is_safe_public_crawl_url("https://faculty.example.edu"))

    def test_is_safe_public_crawl_url_allows_unresolvable_domain_at_validation_time(self) -> None:
        with patch(
            "app.services.crawler_tools.socket.getaddrinfo",
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

    def test_build_profile_candidate_prompt_requires_preserving_source_language_values(self) -> None:
        prompt = build_profile_candidate_prompt(
            university="江西财经大学",
            school="计算机与人工智能学院",
            profile_url="https://example.edu/faculty/zhang",
            page_text="方玉明，江西财经大学教授，研究方向：计算机视觉。",
        )

        self.assertIn("必须使用英文键", prompt)
        self.assertIn("字段值尽量保持页面原文", prompt)
        self.assertIn("不要翻译、音译或拼音化", prompt)
        self.assertIn("如果正文出现该导师的邮箱", prompt)
        self.assertIn("多个邮箱", prompt)
        self.assertIn("最可能属于该导师", prompt)
        self.assertIn("[@]", prompt)
        self.assertIn("recent_papers 必须是 JSON 数组", prompt)
        self.assertIn("输出示例", prompt)
        self.assertIn('"field_confidence"', prompt)
        self.assertIn('"recent_papers": []', prompt)
        self.assertIn('"evidence"', prompt)

    def test_candidate_enrichment_payload_defaults(self) -> None:
        payload = CandidateEnrichmentPayload.model_validate({})
        self.assertIsNone(payload.email)
        self.assertIsNone(payload.department)
        self.assertIsNone(payload.research_direction)
        self.assertEqual(payload.recent_papers, [])

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
            "app.services.crawler_tools.crawl_page_with_http",
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

            with patch("app.services.crawler_tools.crawl_page_with_http", AsyncMock()) as http_mock, patch(
                "app.services.crawler_tools._crawl_page_with_browser", AsyncMock()
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

            with patch("app.services.crawler_tools.crawl_page_with_http", AsyncMock()) as http_mock, patch(
                "app.services.crawler_tools._crawl_page_with_browser", AsyncMock()
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

        with patch("app.services.crawler_tools.crawl_page_with_http") as mocked_http, patch(
            "app.services.crawler_tools.browser_investigate"
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
            "app.services.crawler_tools.crawl_page_with_http",
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
            "app.services.crawler_tools.crawl_page_with_http",
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
            "app.services.crawler_tools.crawl_page_with_http",
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
                "app.services.crawler_tools.crawl_page_with_http",
                new=AsyncMock(return_value=http_snapshot),
            ) as http_fetch, patch(
                "app.services.crawler_tools.browser_investigate",
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
            "app.services.crawler_tools.crawl_page_with_http",
            new=AsyncMock(return_value=http_snapshot),
        ), patch(
            "app.services.crawler_tools.browser_investigate",
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
            "app.services.crawler_tools.crawl_page_with_http",
            new=AsyncMock(return_value=http_snapshot),
        ), patch(
            "app.services.crawler_tools.browser_investigate",
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
            "app.services.crawler_tools.crawl_page_with_http",
            new=AsyncMock(return_value=http_snapshot),
        ), patch(
            "app.services.crawler_tools.browser_investigate",
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
                "app.services.crawler_tools.crawl_page_with_http",
                new=AsyncMock(return_value=direct),
            ), patch(
                "app.services.crawler_tools._crawl_page_with_browser",
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
            "app.services.crawler_tools.crawl_page_with_http",
            new=AsyncMock(return_value=http_snapshot),
        ), patch(
            "app.services.crawler_tools.browser_investigate",
            new=AsyncMock(return_value=browser_snapshot),
        ) as browser:
            actual = await crawl_page_with_browser_fallback(ctx, ctx.start_url)

        self.assertEqual(actual, browser_snapshot)
        browser.assert_awaited_once()

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
            "app.services.crawler_tools.crawl_page_with_http",
            new=AsyncMock(return_value=http_snapshot),
        ), patch(
            "app.services.crawler_tools.browser_investigate",
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
            "app.services.crawler_tools.httpx.AsyncClient",
            new=FakeAsyncClient,
        ), patch(
            "app.services.crawler_tools.crawl_page_with_http",
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
            "app.services.crawler_tools.browser_investigate",
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
                "app.services.crawler_tools._should_offload_browser_fetch_to_thread",
                return_value=True,
            ),
            patch(
                "app.services.crawler_tools.asyncio.to_thread",
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
                "app.services.crawler_tools._should_offload_browser_fetch_to_thread",
                return_value=False,
            ),
            patch(
                "app.services.crawler_tools._fetch_page_with_playwright_direct",
                new=fake_direct,
            ),
            patch("app.services.crawler_tools.asyncio.to_thread", new=AsyncMock()) as to_thread,
        ):
            actual = await _crawl_page_with_browser(
                ctx,
                "https://example.edu/faculty",
                "提取导师信息",
            )

        self.assertEqual(actual, expected)
        self.assertEqual(to_thread.await_count, 0)

    async def test_save_candidates_skips_canceled_job(self) -> None:
        session_factory = _FakeSessionFactory(job_status="canceled")
        ctx = CrawlToolContext(
            job_id=1,
            start_url="https://cs.example.edu/faculty",
            university="示例大学",
            school="计算机学院",
            session_factory=session_factory,  # type: ignore[arg-type]
        )

        with self.assertRaises(CrawlJobCanceled):
            await save_candidates(
                ctx,
                [
                    ProfessorCandidatePayload(
                        name="张三",
                        email="zhang@example.edu",
                    ),
                ],
            )

        self.assertEqual(session_factory.added, [])

    async def test_save_candidates_skips_paused_job(self) -> None:
        session_factory = _FakeSessionFactory(job_status="paused")
        ctx = CrawlToolContext(
            job_id=1,
            start_url="https://cs.example.edu/faculty",
            university="示例大学",
            school="计算机学院",
            session_factory=session_factory,  # type: ignore[arg-type]
        )

        with self.assertRaises(CrawlJobPaused):
            await save_candidates(
                ctx,
                [
                    ProfessorCandidatePayload(
                        name="张三",
                        email="zhang@example.edu",
                    ),
                ],
            )

        self.assertEqual(session_factory.added, [])

    async def test_save_candidates_rolls_back_when_job_is_canceled_before_commit(self) -> None:
        session_factory = _FakeSessionFactory(job_statuses=["running", "canceled"])
        ctx = CrawlToolContext(
            job_id=1,
            start_url="https://cs.example.edu/faculty",
            university="示例大学",
            school="计算机学院",
            session_factory=session_factory,  # type: ignore[arg-type]
        )

        with self.assertRaises(CrawlJobCanceled):
            await save_candidates(
                ctx,
                [
                    ProfessorCandidatePayload(
                        name="张三",
                        email="zhang@example.edu",
                    ),
                ],
            )

        self.assertEqual(session_factory.added, [])
        self.assertEqual(session_factory.rollback_count, 0)

    async def test_save_candidate_batch_returns_counts_without_candidate_details(self) -> None:
        async with _RealCrawlerSessionHarness() as harness:
            job_id = await harness.create_job()
            ctx = CrawlToolContext(
                job_id=job_id,
                start_url="https://cs.example.edu/faculty",
                university="示例大学",
                school="计算机学院",
                session_factory=harness.session_factory,
            )

            result = await save_candidate_batch(
                ctx,
                [
                    ProfessorCandidatePayload(name="张三", email="zhang@example.edu"),
                    ProfessorCandidatePayload(name="李四", email="li@example.edu"),
                ],
            )

            self.assertEqual(result["batch_status"], "saved")
            self.assertEqual(result["attempted_count"], 2)
            self.assertEqual(result["saved_count"], 2)
            self.assertEqual(result["failed_count"], 0)
            self.assertEqual(result["failed_items"], [])
            self.assertEqual(result["total_saved_count"], 2)
            self.assertTrue(result["retry_allowed"])
            self.assertIsNone(result["failure_fingerprint"])
            self.assertEqual(result["consecutive_same_batch_failures"], 0)
            self.assertEqual(result["total_save_failures"], 0)
            self.assertIsNone(result["terminal_reason"])
            self.assertNotIn("candidates", result)

    async def test_save_candidate_batch_rejects_candidate_without_email_or_profile_url(self) -> None:
        async with _RealCrawlerSessionHarness() as harness:
            job_id = await harness.create_job()
            ctx = CrawlToolContext(
                job_id=job_id,
                start_url="https://cs.example.edu/faculty",
                university="示例大学",
                school="计算机学院",
                session_factory=harness.session_factory,
            )

            result = await save_candidate_batch(
                ctx,
                [
                    ProfessorCandidatePayload(
                        name="张三",
                        email=None,
                        profile_url=None,
                        source_url="https://cs.example.edu/faculty",
                    )
                ],
            )

            self.assertEqual(result["saved_count"], 0)
            self.assertEqual(result["rejected_count"], 1)
            self.assertIn("缺少邮箱和详情页链接", result["rejected_items"][0]["reason"])
            self.assertEqual(await harness.count_rows(CrawlCandidate), 0)

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

    async def test_save_candidates_rejects_listing_page_url_without_email(self) -> None:
        async with _RealCrawlerSessionHarness() as harness:
            job_id = await harness.create_job()
            ctx = CrawlToolContext(
                job_id=job_id,
                start_url="https://cs.example.edu/faculty",
                university="示例大学",
                school="计算机学院",
                session_factory=harness.session_factory,
            )

            saved = await save_candidates(
                ctx,
                [
                    ProfessorCandidatePayload(
                        name="张三",
                        profile_url="https://cs.example.edu/faculty",
                    )
                ],
            )

            self.assertEqual(saved, [])
            self.assertEqual(await harness.count_rows(CrawlCandidate), 0)

    async def test_save_candidate_batch_skips_duplicate_profile_url_without_email(self) -> None:
        async with _RealCrawlerSessionHarness() as harness:
            job_id = await harness.create_job()
            ctx = CrawlToolContext(
                job_id=job_id,
                start_url="https://cs.example.edu/faculty",
                university="示例大学",
                school="计算机学院",
                session_factory=harness.session_factory,
            )

            first_result = await save_candidate_batch(
                ctx,
                [
                    ProfessorCandidatePayload(
                        name="张三",
                        profile_url="https://cs.example.edu/teachers/zhang#bio",
                        source_url="https://cs.example.edu/faculty",
                    )
                ],
            )
            second_result = await save_candidate_batch(
                ctx,
                [
                    ProfessorCandidatePayload(
                        name="张三",
                        profile_url="https://cs.example.edu/teachers/zhang",
                        source_url="https://cs.example.edu/faculty?page=1",
                    )
                ],
            )

            self.assertEqual(first_result["saved_count"], 1)
            self.assertEqual(second_result["saved_count"], 0)
            self.assertEqual(second_result["skipped_duplicate_count"], 1)
            self.assertEqual(await harness.count_rows(CrawlCandidate), 1)

    async def test_save_candidate_batch_merges_more_complete_duplicate_profile(self) -> None:
        async with _RealCrawlerSessionHarness() as harness:
            job_id = await harness.create_job()
            ctx = CrawlToolContext(
                job_id=job_id,
                start_url="https://cs.example.edu/faculty",
                university="示例大学",
                school="计算机学院",
                session_factory=harness.session_factory,
            )

            await save_candidate_batch(
                ctx,
                [
                    ProfessorCandidatePayload(
                        name="张三",
                        profile_url="https://cs.example.edu/teachers/zhang",
                        source_url="https://cs.example.edu/faculty",
                    )
                ],
            )
            result = await save_candidate_batch(
                ctx,
                [
                    ProfessorCandidatePayload(
                        name="张三",
                        profile_url="https://cs.example.edu/teachers/zhang",
                        source_url="https://cs.example.edu/faculty#chunk2",
                        research_direction="数据库与大数据管理",
                        evidence={"summary": "后续 chunk 提供研究方向"},
                    )
                ],
            )

            self.assertEqual(result["saved_count"], 0)
            self.assertEqual(result["merged_count"], 1)
            async with harness.session_factory() as session:
                row = (await session.scalars(select(CrawlCandidate))).one()
                self.assertEqual(row.research_direction, "数据库与大数据管理")
                self.assertIn("后续 chunk", str(row.evidence))

    async def test_repeated_same_merge_returns_duplicate_loop_after_two_batches(self) -> None:
        async with _RealCrawlerSessionHarness() as harness:
            job_id = await harness.create_job()
            ctx = CrawlToolContext(
                job_id=job_id,
                start_url="https://cs.example.edu/faculty",
                university="示例大学",
                school="计算机学院",
                session_factory=harness.session_factory,
            )
            await save_candidate_batch(
                ctx,
                [
                    ProfessorCandidatePayload(
                        name="张三",
                        email="zhang@example.edu",
                        profile_url="https://cs.example.edu/teachers/zhang",
                        source_url="https://cs.example.edu/faculty",
                    )
                ],
            )
            first_merge = await save_candidate_batch(
                ctx,
                [
                    ProfessorCandidatePayload(
                        name="张三",
                        email="zhang@example.edu",
                        profile_url="https://cs.example.edu/teachers/zhang",
                        source_url="https://cs.example.edu/faculty?page=2",
                        evidence={"summary": "第一次重复补充"},
                    )
                ],
            )
            second_merge = await save_candidate_batch(
                ctx,
                [
                    ProfessorCandidatePayload(
                        name="张三",
                        email="zhang@example.edu",
                        profile_url="https://cs.example.edu/teachers/zhang",
                        source_url="https://cs.example.edu/faculty?page=2",
                        evidence={"summary": "第二次重复补充"},
                    )
                ],
            )

            self.assertEqual(first_merge["batch_status"], "saved")
            self.assertEqual(first_merge["merged_count"], 1)
            self.assertEqual(second_merge["batch_status"], "duplicate_loop")
            self.assertIn("重复合并", second_merge["next_instruction"])
            self.assertIn("claim_next_page_chunk", second_merge["next_instruction"])
            self.assertIn("返回 empty", second_merge["next_instruction"])

    async def test_save_candidate_batch_does_not_replace_existing_email_with_empty_value(self) -> None:
        async with _RealCrawlerSessionHarness() as harness:
            job_id = await harness.create_job()
            ctx = CrawlToolContext(
                job_id=job_id,
                start_url="https://cs.example.edu/faculty",
                university="示例大学",
                school="计算机学院",
                session_factory=harness.session_factory,
            )

            await save_candidate_batch(
                ctx,
                [
                    ProfessorCandidatePayload(
                        name="李四",
                        email="li@example.edu",
                        profile_url="https://cs.example.edu/li",
                    )
                ],
            )
            await save_candidate_batch(
                ctx,
                [ProfessorCandidatePayload(name="李四", email=None, profile_url="https://cs.example.edu/li")],
            )

            async with harness.session_factory() as session:
                row = (await session.scalars(select(CrawlCandidate).where(CrawlCandidate.name == "李四"))).one()
                self.assertEqual(row.email, "li@example.edu")

    async def test_repeated_duplicate_submissions_return_duplicate_loop(self) -> None:
        async with _RealCrawlerSessionHarness() as harness:
            job_id = await harness.create_job()
            ctx = CrawlToolContext(
                job_id=job_id,
                start_url="https://cs.example.edu",
                university="示例大学",
                school="计算机学院",
                session_factory=harness.session_factory,
            )
            candidate = ProfessorCandidatePayload(
                name="张三",
                profile_url="https://cs.example.edu/zhang",
                source_url="https://cs.example.edu/faculty",
            )

            await save_candidate_batch(ctx, [candidate])
            await save_candidate_batch(ctx, [candidate])
            await save_candidate_batch(ctx, [candidate])
            third = await save_candidate_batch(ctx, [candidate])

            self.assertEqual(third["batch_status"], "duplicate_loop")
            self.assertIn("claim_next_page_chunk", third["next_instruction"])
            self.assertIn("返回 empty", third["next_instruction"])

    async def test_profile_page_email_overrides_list_boundary_email(self) -> None:
        async with _RealCrawlerSessionHarness() as harness:
            job_id = await harness.create_job()
            ctx = CrawlToolContext(
                job_id=job_id,
                start_url="https://cs.example.edu",
                university="示例大学",
                school="计算机学院",
                session_factory=harness.session_factory,
            )
            await save_candidate_batch(
                ctx,
                [
                    ProfessorCandidatePayload(
                        name="张三",
                        email="zhang@example.com",
                        profile_url="https://cs.example.edu/zhang",
                        source_url="https://cs.example.edu/faculty",
                        field_confidence={"email": 0.4},
                    )
                ],
            )
            async with harness.session_factory() as session:
                row = (await session.scalars(select(CrawlCandidate))).one()
                row.boundary_risk = True
                row.source_kind = "list_chunk"
                await session.commit()

            result = await save_candidate_batch(
                ctx,
                [
                    ProfessorCandidatePayload(
                        name="张三",
                        email="zhang@example.com.cn",
                        profile_url="https://cs.example.edu/zhang",
                        source_url="https://cs.example.edu/zhang",
                        field_confidence={"email": 0.95},
                    )
                ],
            )

            self.assertEqual(result["merged_count"], 1)
            async with harness.session_factory() as session:
                row = (await session.scalars(select(CrawlCandidate))).one()
                self.assertEqual(row.email, "zhang@example.com.cn")


    async def test_profile_page_research_direction_overrides_list_boundary_value(self) -> None:
        async with _RealCrawlerSessionHarness() as harness:
            job_id = await harness.create_job()
            ctx = CrawlToolContext(
                job_id=job_id,
                start_url="https://cs.example.edu",
                university="示例大学",
                school="计算机学院",
                session_factory=harness.session_factory,
            )
            await save_candidate_batch(
                ctx,
                [
                    ProfessorCandidatePayload(
                        name="张三",
                        profile_url="https://cs.example.edu/zhang",
                        source_url="https://cs.example.edu/faculty",
                        research_direction="人工智能",
                        field_confidence={"research_direction": 0.4},
                        source_kind="list_chunk",
                        boundary_risk=True,
                    )
                ],
            )

            result = await save_candidate_batch(
                ctx,
                [
                    ProfessorCandidatePayload(
                        name="张三",
                        profile_url="https://cs.example.edu/zhang",
                        source_url="https://cs.example.edu/zhang",
                        research_direction="自然语言处理与知识图谱",
                        field_confidence={"research_direction": 0.95},
                        source_kind="profile_page",
                    )
                ],
            )

            self.assertEqual(result["merged_count"], 1)
            async with harness.session_factory() as session:
                row = (await session.scalars(select(CrawlCandidate))).one()
                self.assertEqual(row.research_direction, "自然语言处理与知识图谱")
                self.assertFalse(row.boundary_risk)
                self.assertEqual(row.source_kind, "profile_page")
                self.assertIn("research_direction", row.field_sources)
                self.assertTrue(row.merge_history)

    async def test_save_candidate_batch_rejects_entire_batch_when_one_item_fails(self) -> None:
        async with _RealCrawlerSessionHarness() as harness:
            job_id = await harness.create_job()
            ctx = CrawlToolContext(
                job_id=job_id,
                start_url="https://cs.example.edu/faculty",
                university="示例大学",
                school="计算机学院",
                session_factory=harness.session_factory,
            )

            result = await save_candidate_batch(
                ctx,
                [
                    ProfessorCandidatePayload(name="张三", email="zhang@example.edu"),
                    ProfessorCandidatePayload(name="", email="bad@example.edu"),
                ],
            )

            self.assertEqual(result["batch_status"], "rejected")
            self.assertEqual(result["attempted_count"], 2)
            self.assertEqual(result["saved_count"], 0)
            self.assertEqual(result["failed_count"], 1)
            self.assertEqual(result["failed_items"][0]["index"], 1)
            self.assertIn("必填文本不能为空", result["failed_items"][0]["reason"])
            self.assertEqual(result["total_saved_count"], 0)
            self.assertTrue(result["retry_allowed"])
            self.assertIsNotNone(result["failure_fingerprint"])
            self.assertEqual(result["consecutive_same_batch_failures"], 1)
            self.assertEqual(result["total_save_failures"], 1)
            self.assertIsNone(result["terminal_reason"])
            self.assertEqual(await harness.count_rows(CrawlCandidate), 0)

    async def test_save_candidate_batch_trips_same_batch_failure_budget(self) -> None:
        async with _RealCrawlerSessionHarness() as harness:
            job_id = await harness.create_job()
            ctx = CrawlToolContext(
                job_id=job_id,
                start_url="https://cs.example.edu/faculty",
                university="示例大学",
                school="计算机学院",
                session_factory=harness.session_factory,
            )
            candidates = [ProfessorCandidatePayload(name="", email="bad@example.edu")]

            result = await save_candidate_batch(ctx, candidates)

            self.assertEqual(result["batch_status"], "rejected")
            self.assertTrue(result["retry_allowed"])
            self.assertIsNotNone(result["failure_fingerprint"])
            self.assertEqual(result["consecutive_same_batch_failures"], 1)
            self.assertEqual(result["total_save_failures"], 1)
            self.assertIsNone(result["terminal_reason"])

            with self.assertRaises(CrawlJobSaveBudgetExceeded) as raised:
                await save_candidate_batch(ctx, candidates)

            self.assertIn("同一候选批次连续保存失败 2 次", str(raised.exception))
            self.assertEqual(await harness.count_rows(CrawlCandidate), 0)

    async def test_save_candidate_batch_does_not_count_stopped_job_as_failure(self) -> None:
        async with _RealCrawlerSessionHarness() as harness:
            job_id = await harness.create_job()
            async with harness.session_factory() as session:
                job = await session.get(CrawlJob, job_id)
                assert job is not None
                job.status = CrawlJobStatus.CANCELED.value
                await session.commit()

            ctx = CrawlToolContext(
                job_id=job_id,
                start_url="https://cs.example.edu/faculty",
                university="示例大学",
                school="计算机学院",
                session_factory=harness.session_factory,
            )

            with self.assertRaises(CrawlJobCanceled):
                await save_candidate_batch(
                    ctx,
                    [ProfessorCandidatePayload(name="张三", email="zhang@example.edu")],
                )

            self.assertEqual(ctx.save_failure_budget.total_save_failures, 0)
            self.assertEqual(ctx.save_failure_budget.same_batch_save_failures, 0)
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
            "app.services.crawler_tools.crawl_page_with_http",
            new=AsyncMock(return_value=snapshot),
        ) as mocked_http, patch(
            "app.services.crawler_tools.browser_investigate",
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
            "app.services.crawler_tools._crawl_page_with_browser",
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

    async def test_save_candidates_sees_canceled_status_changed_by_other_session(self) -> None:
        async with _RealCrawlerSessionHarness() as harness:
            job_id = await harness.create_job()
            ctx = CrawlToolContext(
                job_id=job_id,
                start_url="https://cs.example.edu/faculty",
                university="示例大学",
                school="计算机学院",
                session_factory=harness.cancel_on_second_status_factory(job_id),  # type: ignore[arg-type]
            )

            with self.assertRaises(CrawlJobCanceled):
                await save_candidates(
                    ctx,
                    [
                        ProfessorCandidatePayload(
                            name="张三",
                            email="zhang@example.edu",
                        ),
                    ],
                )

            self.assertEqual(await harness.count_rows(CrawlCandidate), 0)

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
            "app.services.crawler_tools.socket.getaddrinfo",
            return_value=[
                (0, 0, 0, "", ("93.184.216.34", 443)),
            ],
        ), patch("app.services.crawler_tools.httpx.AsyncClient") as client_class:
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
            "app.services.crawler_tools.socket.getaddrinfo",
            return_value=[
                (0, 0, 0, "", ("93.184.216.34", 443)),
            ],
        ), patch("app.services.crawler_tools.httpx.AsyncClient") as client_class:
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
            "app.services.crawler_tools.socket.getaddrinfo",
            side_effect=[public_dns, public_dns, public_dns, public_dns, private_dns],
        ), patch("app.services.crawler_tools.httpx.AsyncClient") as client_class:
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
            "app.services.crawler_tools.socket.getaddrinfo",
            return_value=[
                (0, 0, 0, "", ("93.184.216.34", 443)),
            ],
        ), patch("app.services.crawler_tools.httpx.AsyncClient", side_effect=client_factory):
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
            "app.services.crawler_tools.socket.getaddrinfo",
            return_value=[
                (0, 0, 0, "", ("93.184.216.34", 443)),
            ],
        ), patch("app.services.crawler_tools.httpx.AsyncClient", side_effect=client_factory):
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
            "app.services.crawler_tools.socket.getaddrinfo",
            return_value=[
                (0, 0, 0, "", ("93.184.216.34", 443)),
            ],
        ), patch(
            "app.services.crawler_tools._default_async_network_backend",
            return_value=backend,
        ):
            snapshot = await crawl_page_with_http(ctx, "https://faculty.example.edu/faculty")

        self.assertEqual(snapshot.status, "succeeded")
        self.assertEqual(backend.connect_calls, [("93.184.216.34", 443)])
        self.assertNotIn(("faculty.example.edu", 443), backend.connect_calls)
        self.assertEqual(backend.streams[0].tls_server_hostnames, ["faculty.example.edu"])

    async def test_crawl_page_with_http_uses_system_proxy_ip_for_domain_fetching(
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
            "app.services.crawler_tools.socket.getaddrinfo",
            return_value=[
                (0, 0, 0, "", ("100.64.0.42", 443)),
            ],
        ), patch(
            "app.services.crawler_tools._default_async_network_backend",
            return_value=backend,
        ):
            snapshot = await crawl_page_with_http(ctx, "https://faculty.example.edu/faculty")

        self.assertEqual(snapshot.status, "succeeded")
        self.assertEqual(backend.connect_calls, [("100.64.0.42", 443)])
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
            return [(0, 0, 0, "", ("100.64.0.42", 443))]

        with patch(
            "app.services.crawler_tools.socket.getaddrinfo",
            side_effect=getaddrinfo,
        ), patch("app.services.crawler_tools.httpx.AsyncClient") as client_class:
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
            "app.services.crawler_tools.socket.getaddrinfo",
            side_effect=resolve_current_public_ip,
        ), patch(
            "app.services.crawler_tools._default_async_network_backend",
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
            "app.services.crawler_tools.socket.getaddrinfo",
            return_value=[
                (0, 0, 0, "", ("93.184.216.34", 443)),
            ],
        ), patch(
            "app.services.crawler_tools.crawl_page_with_http",
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
            "app.services.crawler_tools.crawl_page_with_http",
            return_value=empty_http_snapshot,
        ) as http_path, patch(
            "app.services.crawler_tools._crawl_page_with_browser",
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
            "app.services.crawler_tools.crawl_page_with_http",
            return_value=blocked_http_snapshot,
        ) as http_path, patch(
            "app.services.crawler_tools._crawl_page_with_browser",
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
            "app.services.crawler_tools.crawl_page_with_http",
            return_value=blocked_http_snapshot,
        ) as http_path, patch(
            "app.services.crawler_tools._crawl_page_with_browser",
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
            "app.services.crawler_tools.crawl_page_with_http",
            side_effect=[blocked_http_snapshot, other_http_snapshot],
        ) as http_path, patch(
            "app.services.crawler_tools._crawl_page_with_browser",
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
            "app.services.crawler_tools._crawl_page_with_browser",
            return_value=browser_snapshot,
        ) as browser_path:
            snapshot = await crawler_tools.browser_investigate(
                ctx,
                "https://faculty.example.edu/faculty",
                "table",
            )

        self.assertIs(snapshot, browser_snapshot)
        self.assertEqual(browser_path.call_count, 1)

    async def test_browser_investigate_skips_previously_denied_url(self) -> None:
        ctx = CrawlToolContext(
            job_id=1,
            start_url="https://cs.example.edu/faculty/index.htm",
            university="测试大学",
            school="计算机学院",
            session_factory=_FakeSessionFactory(),  # type: ignore[arg-type]
        )
        ctx.mark_denied_url("https://cs.example.edu/news/a.htm", "无关新闻页")

        with patch("app.services.crawler_tools._crawl_page_with_browser") as mocked_browser:
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
            "app.services.crawler_tools._crawl_page_with_browser",
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
                    runtime_version="v2",
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
                    runtime_version="v2",
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
            "app.services.crawler_tools.async_playwright",
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

        with patch("app.services.crawler_tools.async_playwright", return_value=_Playwright()):
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

        with patch("app.services.crawler_tools.async_playwright", return_value=_Playwright()):
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

        with patch("app.services.crawler_tools.async_playwright", return_value=_Playwright()):
            snapshot = await crawler_tools._fetch_page_with_playwright_direct(
                "https://teacher.example.edu/zhoufeng",
                "",
                "profile",
            )

        self.assertEqual(snapshot.status, "succeeded")
        self.assertEqual(attempts, 2)
        self.assertIn("zfeng@bupt.edu.cn", snapshot.text)

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

        with patch("app.services.crawler_tools.async_playwright", return_value=_Playwright()):
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
