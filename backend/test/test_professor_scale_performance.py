from __future__ import annotations

import asyncio
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models import IdentityProfile
from app.modules.professors.query import (
    list_dashboard_professor_page,
    list_management_professor_ids,
    list_management_professor_page,
)
from app.modules.professors.schemas import (
    ProfessorDashboardPageRequest,
    ProfessorManagementPageRequest,
)
from app.services.dashboard_stats import build_dashboard_overview
from test.migrated_database import create_migrated_sqlite_database


SCALE_ROW_COUNT = 100_000
INTERACTIVE_QUERY_BUDGET_SECONDS = 1.5
DASHBOARD_QUERY_BUDGET_SECONDS = 2.5


class ProfessorScalePerformanceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.temp_dir = tempfile.TemporaryDirectory()
        cls.db_path = Path(cls.temp_dir.name) / "professor-scale.db"
        create_migrated_sqlite_database(cls.db_path)
        cls._seed_professors()
        cls.engine = create_async_engine(
            f"sqlite+aiosqlite:///{cls.db_path.as_posix()}",
        )
        cls.session_factory = async_sessionmaker(
            cls.engine,
            autoflush=False,
            expire_on_commit=False,
        )
        cls.identity_id = asyncio.run(cls._seed_identity())

    @classmethod
    def tearDownClass(cls) -> None:
        asyncio.run(cls.engine.dispose())
        cls.temp_dir.cleanup()

    @classmethod
    def _seed_professors(cls) -> None:
        connection = sqlite3.connect(cls.db_path)
        try:
            rows = (
                (
                    f"规模导师{index:06d}",
                    f"scale-{index:06d}@example.edu",
                    "教授" if index % 3 == 0 else "副教授",
                    f"规模大学{index % 100:03d}",
                    f"学院{index % 20:02d}",
                    f"系所{index % 50:02d}",
                    (
                        "数据库系统与独特关键词检索"
                        if index == 54_321
                        else f"研究方向 {index % 500:03d}"
                    ),
                    "[]",
                    "discovered",
                    1,
                    "2026-08-09 00:00:00.000000",
                    "2026-08-09 00:00:00.000000",
                )
                for index in range(SCALE_ROW_COUNT)
            )
            connection.executemany(
                """
                INSERT INTO professors(
                    name, email, title, university, school, department,
                    research_direction, recent_papers, crawl_status,
                    communication_sync_version, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                rows,
            )
            connection.execute("ANALYZE")
            connection.execute("PRAGMA optimize")
            connection.commit()
        finally:
            connection.close()

    @classmethod
    async def _seed_identity(cls) -> int:
        async with cls.session_factory() as session:
            identity = IdentityProfile(
                name="规模测试身份",
                profile_name="规模测试身份",
                sender_name="规模测试用户",
                email_address="scale-sender@example.com",
                smtp_host="smtp.example.com",
                smtp_port=465,
                smtp_username="scale-sender@example.com",
                smtp_password="secret",
            )
            session.add(identity)
            await session.commit()
            return identity.id

    def test_management_first_page_and_cursor_are_interactive_at_100k(self) -> None:
        async def scenario():
            async with self.session_factory() as session:
                started = time.perf_counter()
                first = await list_management_professor_page(
                    session,
                    ProfessorManagementPageRequest(page_size=50),
                )
                first_elapsed = time.perf_counter() - started
                started = time.perf_counter()
                second = await list_management_professor_page(
                    session,
                    ProfessorManagementPageRequest(
                        page=2,
                        page_size=50,
                        cursor=first.next_cursor,
                    ),
                )
                second_elapsed = time.perf_counter() - started
                return first, second, first_elapsed, second_elapsed

        first, second, first_elapsed, second_elapsed = asyncio.run(scenario())
        self.assertEqual(first.total_count, SCALE_ROW_COUNT)
        self.assertEqual(len(first.items), 50)
        self.assertEqual(len(second.items), 50)
        self.assertTrue(
            {item.id for item in first.items}.isdisjoint(
                item.id for item in second.items
            ),
        )
        self.assertLess(first_elapsed, INTERACTIVE_QUERY_BUDGET_SECONDS)
        self.assertLess(second_elapsed, INTERACTIVE_QUERY_BUDGET_SECONDS)

    def test_fts_search_is_interactive_at_100k(self) -> None:
        async def scenario():
            async with self.session_factory() as session:
                started = time.perf_counter()
                page = await list_management_professor_page(
                    session,
                    ProfessorManagementPageRequest(
                        keyword="独特关键词",
                        keyword_search_scopes=["researchDirection"],
                        page_size=20,
                    ),
                )
                return page, time.perf_counter() - started

        page, elapsed = asyncio.run(scenario())
        self.assertEqual(page.total_count, 1)
        self.assertEqual(
            [item.email for item in page.items], ["scale-054321@example.edu"]
        )
        self.assertLess(elapsed, INTERACTIVE_QUERY_BUDGET_SECONDS)

    def test_dashboard_page_is_interactive_at_100k(self) -> None:
        async def scenario():
            async with self.session_factory() as session:
                started = time.perf_counter()
                page = await list_dashboard_professor_page(
                    session,
                    ProfessorDashboardPageRequest(
                        identity_id=self.identity_id,
                        page_size=50,
                    ),
                )
                return page, time.perf_counter() - started

        page, elapsed = asyncio.run(scenario())
        self.assertEqual(page.total_count, SCALE_ROW_COUNT)
        self.assertEqual(len(page.items), 50)
        self.assertLess(elapsed, DASHBOARD_QUERY_BUDGET_SECONDS)

    def test_select_all_ids_is_bounded_at_100k(self) -> None:
        async def scenario():
            async with self.session_factory() as session:
                started = time.perf_counter()
                selection = await list_management_professor_ids(
                    session,
                    ProfessorManagementPageRequest(),
                )
                return selection, time.perf_counter() - started

        selection, elapsed = asyncio.run(scenario())
        self.assertEqual(selection.total_count, SCALE_ROW_COUNT)
        self.assertEqual(len(selection.ids), SCALE_ROW_COUNT)
        self.assertLess(elapsed, INTERACTIVE_QUERY_BUDGET_SECONDS)

    def test_dashboard_overview_uses_aggregates_at_100k(self) -> None:
        async def scenario():
            async with self.session_factory() as session:
                started = time.perf_counter()
                overview = await build_dashboard_overview(
                    session,
                    identity_id=self.identity_id,
                )
                return overview, time.perf_counter() - started

        overview, elapsed = asyncio.run(scenario())
        self.assertEqual(overview.mentor.summary.total_professors, SCALE_ROW_COUNT)
        self.assertEqual(overview.email.summary.total_professor_count, SCALE_ROW_COUNT)
        self.assertLess(elapsed, DASHBOARD_QUERY_BUDGET_SECONDS)


if __name__ == "__main__":
    unittest.main()
