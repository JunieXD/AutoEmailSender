from __future__ import annotations

import asyncio
import unittest
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models.base import Base
from app.models.crawl_job import CrawlJob, CrawlPageFetchState
from app.modules.crawler.pages.fetch_ledger import (
    TRANSIENT_FETCH_RETRY_LIMIT,
    classify_page_fetch_failure,
    get_page_fetch_decision,
    mark_page_fetch_result,
    normalize_fetch_url,
    should_prefer_browser_for_fetch_domain,
)
from app.modules.crawler.pages.tools import PageSnapshot


@asynccontextmanager
async def _create_test_session_factory() -> AsyncIterator[
    async_sessionmaker[AsyncSession]
]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        factory._test_engine = engine  # type: ignore[attr-defined]
        yield factory
    finally:
        await engine.dispose()


class CrawlerPageFetchLedgerPureTests(unittest.TestCase):
    def test_normalize_fetch_url_lowercases_scheme_host_and_removes_fragment(
        self,
    ) -> None:
        self.assertEqual(
            normalize_fetch_url("HTTPS://CS.EXAMPLE.EDU/faculty?page=1#section"),
            "https://cs.example.edu/faculty?page=1",
        )

    def test_normalize_fetch_url_preserves_spa_route_fragment(self) -> None:
        self.assertEqual(
            normalize_fetch_url("HTTPS://CS.EXAMPLE.EDU/#/teachers?page=2"),
            "https://cs.example.edu/#/teachers?page=2",
        )

    def test_classifies_antibot_empty_response_as_terminal(self) -> None:
        snapshot = PageSnapshot(
            url="https://cs.example.edu/faculty",
            title=None,
            text="",
            html="",
            links=[],
            fetch_method="browser",
            status="failed",
            error_message="Blocked by anti-bot protection",
            suspicious_empty=True,
        )

        result = classify_page_fetch_failure(snapshot)

        self.assertEqual(result.status, "terminal_failed")
        self.assertEqual(result.reason, "anti_bot_or_empty_response")

    def test_classifies_wait_condition_failure_as_transient(self) -> None:
        snapshot = PageSnapshot(
            url="https://cs.example.edu/faculty",
            title=None,
            text="",
            html="",
            links=[],
            fetch_method="browser",
            status="failed",
            error_message="wait condition failed",
            suspicious_empty=False,
        )

        result = classify_page_fetch_failure(snapshot)

        self.assertEqual(result.status, "transient_failed")
        self.assertIsNone(result.reason)

    def test_classifies_network_failure_as_transient_even_when_empty(self) -> None:
        snapshot = PageSnapshot(
            url="https://cs.example.edu/faculty",
            title=None,
            text="",
            html="",
            links=[],
            fetch_method="browser",
            status="failed",
            error_message="Playwright browser fetch failed: net::ERR_CONNECTION_CLOSED",
            suspicious_empty=True,
        )

        result = classify_page_fetch_failure(snapshot)

        self.assertEqual(result.status, "transient_failed")
        self.assertIsNone(result.reason)

    def test_classifies_http_502_empty_shell_as_transient(self) -> None:
        snapshot = PageSnapshot(
            url="https://cs.example.edu/faculty",
            title=None,
            text="",
            html="<html><head></head><body></body></html>",
            links=[],
            fetch_method="browser",
            status="failed",
            http_status_code=502,
            error_message="Playwright browser fetch returned temporary HTTP 502",
            suspicious_empty=True,
        )

        result = classify_page_fetch_failure(snapshot)

        self.assertEqual(result.status, "transient_failed")
        self.assertIsNone(result.reason)

    def test_classifies_temporary_dns_resolution_as_transient(self) -> None:
        snapshot = PageSnapshot(
            url="https://cs.example.edu/faculty",
            title=None,
            text="",
            html="",
            links=[],
            fetch_method="browser",
            status="failed",
            error_message="页面地址暂时无法解析，稍后将自动重试",
            suspicious_empty=True,
        )

        result = classify_page_fetch_failure(snapshot)

        self.assertEqual(result.status, "transient_failed")
        self.assertIsNone(result.reason)

    def test_classifies_url_policy_failure_as_terminal(self) -> None:
        snapshot = PageSnapshot(
            url="https://other.example.edu/faculty",
            title=None,
            text="",
            html="",
            links=[],
            fetch_method="browser",
            status="failed",
            error_message="URL 不在入口页面同域范围内，已拒绝浏览器调查",
            suspicious_empty=False,
        )

        result = classify_page_fetch_failure(snapshot)

        self.assertEqual(result.status, "terminal_failed")
        self.assertEqual(result.reason, "policy_rejected")


class CrawlerPageFetchLedgerDatabaseTests(unittest.TestCase):
    def test_old_terminal_network_failure_is_allowed_a_bounded_recovery_retry(
        self,
    ) -> None:
        async def run() -> tuple[str, str | None]:
            async with _create_test_session_factory() as session_factory:
                async with session_factory() as session:
                    job = CrawlJob(
                        university="示例大学",
                        school="计算机学院",
                        start_url="https://cs.example.edu/faculty",
                    )
                    session.add(job)
                    await session.flush()
                    session.add(
                        CrawlPageFetchState(
                            job_id=job.id,
                            normalized_url="https://cs.example.edu/faculty",
                            original_url="https://cs.example.edu/faculty",
                            status="terminal_failed",
                            terminal_reason="anti_bot_or_empty_response",
                            last_error_message="Playwright browser fetch failed: net::ERR_CONNECTION_CLOSED",
                            transient_failure_count=0,
                        )
                    )
                    await session.commit()
                    job_id = job.id

                decision = await get_page_fetch_decision(
                    session_factory,
                    job_id=job_id,
                    url="https://cs.example.edu/faculty",
                )
                return decision.action, decision.status

        self.assertEqual(asyncio.run(run()), ("allow_retry", "transient_failed"))

    def test_terminal_failed_decision_skips_fetch_after_restart(self) -> None:
        async def run() -> str:
            async with _create_test_session_factory() as session_factory:
                async with session_factory() as session:
                    job = CrawlJob(
                        university="示例大学",
                        school="计算机学院",
                        start_url="https://cs.example.edu/faculty",
                    )
                    session.add(job)
                    await session.flush()
                    session.add(
                        CrawlPageFetchState(
                            job_id=job.id,
                            normalized_url="https://cs.example.edu/faculty",
                            original_url="https://cs.example.edu/faculty",
                            status="terminal_failed",
                            last_fetch_method="browser",
                            terminal_reason="anti_bot_or_empty_response",
                            last_error_message="Blocked by anti-bot protection",
                        )
                    )
                    await session.commit()
                    job_id = job.id

                decision = await get_page_fetch_decision(
                    session_factory,
                    job_id=job_id,
                    url="https://cs.example.edu/faculty#ignored",
                )
                return decision.action

        self.assertEqual(asyncio.run(run()), "skip_terminal_failed")

    def test_transient_failed_allows_retry_before_limit(self) -> None:
        async def run() -> str:
            async with _create_test_session_factory() as session_factory:
                async with session_factory() as session:
                    job = CrawlJob(
                        university="示例大学",
                        school="计算机学院",
                        start_url="https://cs.example.edu/faculty",
                    )
                    session.add(job)
                    await session.flush()
                    session.add(
                        CrawlPageFetchState(
                            job_id=job.id,
                            normalized_url="https://cs.example.edu/faculty",
                            original_url="https://cs.example.edu/faculty",
                            status="transient_failed",
                            transient_failure_count=1,
                        )
                    )
                    await session.commit()
                    job_id = job.id

                decision = await get_page_fetch_decision(
                    session_factory, job_id=job_id, url="https://cs.example.edu/faculty"
                )
                return decision.action

        self.assertEqual(asyncio.run(run()), "allow_retry")

    def test_transient_failed_exhaustion_becomes_terminal(self) -> None:
        async def run() -> tuple[str, str]:
            async with _create_test_session_factory() as session_factory:
                async with session_factory() as session:
                    job = CrawlJob(
                        university="示例大学",
                        school="计算机学院",
                        start_url="https://cs.example.edu/faculty",
                    )
                    session.add(job)
                    await session.flush()
                    session.add(
                        CrawlPageFetchState(
                            job_id=job.id,
                            normalized_url="https://cs.example.edu/faculty",
                            original_url="https://cs.example.edu/faculty",
                            status="transient_failed",
                            transient_failure_count=TRANSIENT_FETCH_RETRY_LIMIT,
                            last_error_message="timeout",
                        )
                    )
                    await session.commit()
                    job_id = job.id

                decision = await get_page_fetch_decision(
                    session_factory, job_id=job_id, url="https://cs.example.edu/faculty"
                )
                return decision.action, decision.terminal_reason or ""

        self.assertEqual(
            asyncio.run(run()), ("skip_terminal_failed", "transient_retry_exhausted")
        )

    def test_mark_page_fetch_result_records_terminal_failure(self) -> None:
        async def run() -> tuple[str, str | None]:
            async with _create_test_session_factory() as session_factory:
                async with session_factory() as session:
                    job = CrawlJob(
                        university="示例大学",
                        school="计算机学院",
                        start_url="https://cs.example.edu/faculty",
                    )
                    session.add(job)
                    await session.commit()
                    job_id = job.id

                snapshot = PageSnapshot(
                    url="https://cs.example.edu/faculty",
                    title=None,
                    text="",
                    html="",
                    links=[],
                    fetch_method="browser",
                    status="failed",
                    error_message="Blocked by anti-bot protection",
                    suspicious_empty=True,
                )
                await mark_page_fetch_result(
                    session_factory,
                    job_id=job_id,
                    original_url="https://cs.example.edu/faculty",
                    snapshot=snapshot,
                )
                async with session_factory() as session:
                    state = await session.get(CrawlPageFetchState, 1)
                    return state.status, state.terminal_reason

        self.assertEqual(
            asyncio.run(run()), ("terminal_failed", "anti_bot_or_empty_response")
        )

    def test_mark_page_fetch_result_handles_null_transient_counter(self) -> None:
        async def run() -> tuple[str, int | None]:
            async with _create_test_session_factory() as session_factory:
                async with session_factory() as session:
                    job = CrawlJob(
                        university="示例大学",
                        school="计算机学院",
                        start_url="https://cs.example.edu/faculty",
                    )
                    session.add(job)
                    await session.flush()
                    session.add(
                        CrawlPageFetchState(
                            job_id=job.id,
                            normalized_url="https://cs.example.edu/faculty",
                            original_url="https://cs.example.edu/faculty",
                            status="transient_failed",
                            transient_failure_count=None,
                        )
                    )
                    await session.commit()
                    job_id = job.id

                await mark_page_fetch_result(
                    session_factory,
                    job_id=job_id,
                    original_url="https://cs.example.edu/faculty",
                    snapshot=PageSnapshot(
                        url="https://cs.example.edu/faculty",
                        title=None,
                        text="",
                        html="",
                        links=[],
                        fetch_method="browser",
                        status="failed",
                        error_message="Playwright browser fetch failed: timeout",
                        suspicious_empty=True,
                    ),
                )
                async with session_factory() as session:
                    state = await session.scalar(
                        select(CrawlPageFetchState).where(
                            CrawlPageFetchState.job_id == job_id,
                        )
                    )
                    assert state is not None
                    return state.status, state.transient_failure_count

        self.assertEqual(asyncio.run(run()), ("transient_failed", 1))

    def test_browser_preference_excludes_previous_domain_preference_skip(self) -> None:
        async def run() -> bool:
            async with _create_test_session_factory() as session_factory:
                async with session_factory() as session:
                    job = CrawlJob(
                        university="示例大学",
                        school="计算机学院",
                        start_url="https://cs.example.edu/faculty",
                    )
                    session.add(job)
                    await session.flush()
                    session.add(
                        CrawlPageFetchState(
                            job_id=job.id,
                            normalized_url="https://teacher.example.edu/a",
                            original_url="https://teacher.example.edu/a",
                            status="succeeded",
                            fetch_mode="browser",
                            direct_status="skipped_by_domain_browser_preference",
                            fallback_reason="same_domain_previously_required_browser",
                            browser_status="succeeded",
                        )
                    )
                    await session.commit()
                    job_id = job.id

                return await should_prefer_browser_for_fetch_domain(
                    session_factory,
                    job_id=job_id,
                    url="https://teacher.example.edu/b",
                )

        self.assertFalse(asyncio.run(run()))

    def test_browser_preference_requires_a_real_direct_attempt(self) -> None:
        async def run() -> bool:
            async with _create_test_session_factory() as session_factory:
                async with session_factory() as session:
                    job = CrawlJob(
                        university="示例大学",
                        school="计算机学院",
                        start_url="https://cs.example.edu/faculty",
                    )
                    session.add(job)
                    await session.flush()
                    session.add(
                        CrawlPageFetchState(
                            job_id=job.id,
                            normalized_url="https://teacher.example.edu/a",
                            original_url="https://teacher.example.edu/a",
                            status="succeeded",
                            fetch_mode="browser",
                            direct_status="succeeded",
                            fallback_reason="direct_fetch_unusable",
                            browser_status="succeeded",
                        )
                    )
                    await session.commit()
                    job_id = job.id

                return await should_prefer_browser_for_fetch_domain(
                    session_factory,
                    job_id=job_id,
                    url="https://teacher.example.edu/b",
                )

        self.assertTrue(asyncio.run(run()))


if __name__ == "__main__":
    unittest.main()
