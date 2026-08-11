from __future__ import annotations

import asyncio
import base64
import json
import sqlite3
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models import (
    EmailTask,
    EmailTaskStatus,
    IdentityProfessorMatchResult,
    IdentityProfile,
    LLMProfile,
    Professor,
    ProfessorTag,
)
from app.modules.professors.query import (
    list_dashboard_professor_ids,
    list_dashboard_professor_page,
    list_management_professor_ids,
    list_management_professor_page,
)
from app.modules.professors.schemas import (
    ProfessorDashboardPageRequest,
    ProfessorManagementPageRequest,
)
from test.migrated_database import create_migrated_sqlite_database
from test.schema_database import create_schema_sqlite_database


class ProfessorPaginationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "professor-pagination.db"
        create_migrated_sqlite_database(self.db_path)
        self.engine = create_async_engine(
            f"sqlite+aiosqlite:///{self.db_path.as_posix()}",
        )
        self.session_factory = async_sessionmaker(
            self.engine,
            autoflush=False,
            expire_on_commit=False,
        )

    def tearDown(self) -> None:
        asyncio.run(self.engine.dispose())
        self.temp_dir.cleanup()

    def test_empty_page_has_stable_pagination_metadata(self) -> None:
        async def run() -> None:
            async with self.session_factory() as session:
                page = await list_management_professor_page(
                    session,
                    ProfessorManagementPageRequest(page=99, page_size=20),
                )

            self.assertEqual(page.items, [])
            self.assertEqual(page.total_count, 0)
            self.assertFalse(page.has_any_professors)
            self.assertEqual(page.page, 1)
            self.assertEqual(page.total_pages, 1)
            self.assertIsNone(page.next_cursor)
            self.assertEqual(page.filter_options.universities, [])

        asyncio.run(run())

    def test_management_cursor_matches_offset_and_clamps_out_of_range_page(
        self,
    ) -> None:
        async def run() -> None:
            professor_ids = await self._seed_management_professors()
            async with self.session_factory() as session:
                first = await list_management_professor_page(
                    session,
                    ProfessorManagementPageRequest(page=1, page_size=2),
                )
                by_cursor = await list_management_professor_page(
                    session,
                    ProfessorManagementPageRequest(
                        page=2,
                        page_size=2,
                        cursor=first.next_cursor,
                    ),
                )
                by_offset = await list_management_professor_page(
                    session,
                    ProfessorManagementPageRequest(page=2, page_size=2),
                )
                last = await list_management_professor_page(
                    session,
                    ProfessorManagementPageRequest(page=999, page_size=2),
                )

            self.assertEqual(first.total_count, 6)
            self.assertEqual(first.total_pages, 3)
            self.assertIsNotNone(first.next_cursor)
            self.assertEqual(
                [item.id for item in by_cursor.items],
                [item.id for item in by_offset.items],
            )
            self.assertTrue(
                set(item.id for item in first.items).isdisjoint(
                    item.id for item in by_cursor.items
                ),
            )
            self.assertEqual(last.page, 3)
            self.assertEqual([item.id for item in last.items], professor_ids[:2][::-1])

        asyncio.run(run())

    def test_cursor_rejects_changed_sort_contract(self) -> None:
        async def run() -> None:
            await self._seed_management_professors()
            async with self.session_factory() as session:
                first = await list_management_professor_page(
                    session,
                    ProfessorManagementPageRequest(page_size=2),
                )
                with self.assertRaisesRegex(ValueError, "分页游标无效"):
                    await list_management_professor_page(
                        session,
                        ProfessorManagementPageRequest(
                            page=2,
                            page_size=2,
                            cursor=first.next_cursor,
                            sort_key="nameAsc",
                            sort_direction="asc",
                        ),
                    )

        asyncio.run(run())

    def test_cursor_rejects_malformed_payload_and_sort_values(self) -> None:
        def encode(payload: object) -> str:
            serialized = json.dumps(payload, separators=(",", ":")).encode("utf-8")
            return base64.urlsafe_b64encode(serialized).decode("ascii").rstrip("=")

        invalid_cursors = [
            encode([]),
            encode({"k": "nameAsc", "d": "asc", "v": [], "i": 1}),
            encode({"k": "nameAsc", "d": "asc", "v": "导师", "i": True}),
        ]

        async def run() -> None:
            async with self.session_factory() as session:
                for cursor in invalid_cursors:
                    with self.subTest(cursor=cursor):
                        with self.assertRaisesRegex(ValueError, "分页游标无效"):
                            await list_management_professor_page(
                                session,
                                ProfessorManagementPageRequest(
                                    page=2,
                                    page_size=2,
                                    cursor=cursor,
                                    sort_key="nameAsc",
                                    sort_direction="asc",
                                ),
                            )

        asyncio.run(run())

    def test_management_university_sort_uses_stable_compound_cursor(self) -> None:
        async def run() -> None:
            await self._seed_management_professors()
            async with self.session_factory() as session:
                first = await list_management_professor_page(
                    session,
                    ProfessorManagementPageRequest(
                        page=1,
                        page_size=2,
                        sort_key="universityAsc",
                        sort_direction="asc",
                    ),
                )
                second = await list_management_professor_page(
                    session,
                    ProfessorManagementPageRequest(
                        page=2,
                        page_size=2,
                        cursor=first.next_cursor,
                        sort_key="universityAsc",
                        sort_direction="asc",
                    ),
                )
                by_offset = await list_management_professor_page(
                    session,
                    ProfessorManagementPageRequest(
                        page=2,
                        page_size=2,
                        sort_key="universityAsc",
                        sort_direction="asc",
                    ),
                )

            self.assertIsNotNone(first.next_cursor)
            self.assertEqual(
                [item.id for item in second.items],
                [item.id for item in by_offset.items],
            )
            self.assertTrue(
                {item.id for item in first.items}.isdisjoint(
                    item.id for item in second.items
                ),
            )

        asyncio.run(run())

    def test_archive_filters_and_all_selection_only_include_actionable_rows(
        self,
    ) -> None:
        async def run() -> None:
            await self._seed_management_professors()
            async with self.session_factory() as session:
                active = await list_management_professor_page(
                    session,
                    ProfessorManagementPageRequest(archived="active"),
                )
                archived = await list_management_professor_page(
                    session,
                    ProfessorManagementPageRequest(archived="archived"),
                )
                all_rows = await list_management_professor_page(
                    session,
                    ProfessorManagementPageRequest(archived="all"),
                )
                selected = await list_management_professor_ids(
                    session,
                    ProfessorManagementPageRequest(archived="all"),
                )

            self.assertEqual(active.total_count, 6)
            self.assertEqual(archived.total_count, 1)
            self.assertEqual(all_rows.total_count, 7)
            self.assertEqual(selected.total_count, 6)
            self.assertTrue(all(item.archived_at is None for item in active.items))
            self.assertTrue(
                all(item.archived_at is not None for item in archived.items)
            )

        asyncio.run(run())

    def test_fts_scopes_literals_short_terms_and_triggers(self) -> None:
        async def search(
            keyword: str,
            scopes: list[str],
        ) -> list[str]:
            async with self.session_factory() as session:
                page = await list_management_professor_page(
                    session,
                    ProfessorManagementPageRequest(
                        page_size=100,
                        keyword=keyword,
                        keyword_search_scopes=scopes,
                    ),
                )
                return [item.name for item in page.items]

        async def run() -> None:
            await self._seed_management_professors()
            self.assertEqual(
                await search("机器学习", ["researchDirection"]), ["王短词"]
            )
            self.assertEqual(await search("机器学习", ["name"]), [])
            self.assertEqual(
                await search('量子"算法', ["researchDirection"]), ["引号导师"]
            )
            self.assertEqual(await search("%_精", ["researchDirection"]), ["符号导师"])
            self.assertEqual(await search("王", ["name"]), ["王短词"])

            async with self.session_factory() as session:
                professor = await session.scalar(
                    select(Professor).where(Professor.name == "符号导师"),
                )
                assert professor is not None
                professor.research_direction = "图神经网络更新词"
                await session.commit()

            self.assertEqual(
                await search("图神经网络", ["researchDirection"]), ["符号导师"]
            )
            self.assertEqual(await search("%_精", ["researchDirection"]), [])

            async with self.session_factory() as session:
                await session.execute(
                    delete(Professor).where(Professor.name == "符号导师")
                )
                await session.commit()

            self.assertEqual(await search("图神经网络", ["researchDirection"]), [])

        asyncio.run(run())

    def test_filters_facets_missing_fields_composite_titles_and_tags(self) -> None:
        async def run() -> None:
            await self._seed_management_professors()
            async with self.session_factory() as session:
                hierarchy = await list_management_professor_page(
                    session,
                    ProfessorManagementPageRequest(
                        page_size=100,
                        universities=["测试大学"],
                    ),
                )
                missing = await list_management_professor_page(
                    session,
                    ProfessorManagementPageRequest(
                        page_size=100,
                        universities=["__no_field__"],
                        tag_ids=["__no_tag__"],
                    ),
                )
                composite_title = await list_management_professor_page(
                    session,
                    ProfessorManagementPageRequest(
                        page_size=100,
                        titles=["博导"],
                    ),
                )
                tagged = await list_management_professor_page(
                    session,
                    ProfessorManagementPageRequest(
                        page_size=100,
                        tag_ids=[str(hierarchy.filter_options.tags[0].id)],
                    ),
                )
                no_matches = await list_management_professor_page(
                    session,
                    ProfessorManagementPageRequest(keyword="不存在的导师"),
                )

            self.assertEqual(
                set(hierarchy.filter_options.schools),
                {"计算机学院", "医学院"},
            )
            self.assertIn("教授", hierarchy.filter_options.titles)
            self.assertIn("博导", hierarchy.filter_options.titles)
            self.assertEqual([item.name for item in missing.items], ["缺失字段"])
            self.assertEqual([item.name for item in composite_title.items], ["王短词"])
            self.assertEqual([item.name for item in tagged.items], ["王短词"])
            self.assertEqual(no_matches.total_count, 0)
            self.assertTrue(no_matches.has_any_professors)

        asyncio.run(run())

    def test_dashboard_filters_status_match_schedule_sort_and_ids(self) -> None:
        async def run() -> None:
            identity_id, expected = await self._seed_dashboard_data()
            async with self.session_factory() as session:
                by_score = await list_dashboard_professor_page(
                    session,
                    ProfessorDashboardPageRequest(
                        identity_id=identity_id,
                        page_size=100,
                        sort_key="matchScoreDesc",
                        sort_direction="desc",
                    ),
                )
                scheduled = await list_dashboard_professor_page(
                    session,
                    ProfessorDashboardPageRequest(
                        identity_id=identity_id,
                        page_size=100,
                        statuses=["scheduled"],
                    ),
                )
                replied = await list_dashboard_professor_page(
                    session,
                    ProfessorDashboardPageRequest(
                        identity_id=identity_id,
                        page_size=100,
                        statuses=["replied"],
                    ),
                )
                no_matches = await list_dashboard_professor_page(
                    session,
                    ProfessorDashboardPageRequest(
                        identity_id=identity_id,
                        keyword="不存在的导师",
                    ),
                )
                score_range = await list_dashboard_professor_ids(
                    session,
                    ProfessorDashboardPageRequest(
                        identity_id=identity_id,
                        min_match_score=80,
                        max_match_score=90,
                    ),
                )
                missing_score = await list_dashboard_professor_ids(
                    session,
                    ProfessorDashboardPageRequest(
                        identity_id=identity_id,
                        match_score_missing=True,
                    ),
                )

            self.assertEqual(
                [item.name for item in by_score.items[:2]], ["高分导师", "回复导师"]
            )
            self.assertEqual([item.name for item in scheduled.items], ["排程导师"])
            self.assertTrue(scheduled.items[0].has_active_schedule)
            self.assertEqual([item.name for item in replied.items], ["回复导师"])
            self.assertEqual(no_matches.total_count, 0)
            self.assertTrue(no_matches.has_any_professors)
            self.assertEqual(score_range.ids, [expected["replied"]])
            self.assertEqual(
                set(missing_score.ids), {expected["scheduled"], expected["plain"]}
            )

        asyncio.run(run())

    def test_orm_only_sqlite_schema_falls_back_when_fts_is_absent(self) -> None:
        async def run() -> None:
            schema_path = Path(self.temp_dir.name) / "schema-only.db"
            create_schema_sqlite_database(schema_path)
            engine = create_async_engine(
                f"sqlite+aiosqlite:///{schema_path.as_posix()}"
            )
            factory = async_sessionmaker(engine, expire_on_commit=False)
            try:
                async with factory() as session:
                    session.add(
                        Professor(
                            name="回退搜索导师",
                            email="fallback@example.edu",
                            research_direction="数据库系统优化",
                        ),
                    )
                    await session.commit()
                    page = await list_management_professor_page(
                        session,
                        ProfessorManagementPageRequest(
                            keyword="数据库系统",
                            keyword_search_scopes=["researchDirection"],
                        ),
                    )
                self.assertEqual([item.name for item in page.items], ["回退搜索导师"])
            finally:
                await engine.dispose()

        asyncio.run(run())

    def test_migration_creates_indexes_fts_and_query_plans(self) -> None:
        awaitable = self._seed_management_professors()
        asyncio.run(awaitable)
        connection = sqlite3.connect(self.db_path)
        try:
            indexes = {
                row[1] for row in connection.execute("PRAGMA index_list('professors')")
            }
            indexes_by_table = {
                table_name: {
                    row[1]
                    for row in connection.execute(
                        f"PRAGMA index_list('{table_name}')",
                    )
                }
                for table_name in (
                    "professor_tag_links",
                    "email_tasks",
                    "email_logs",
                    "identity_professor_match_results",
                )
            }
            indexed_columns = {
                index_name: [
                    row[2]
                    for row in connection.execute(
                        f"PRAGMA index_info('{index_name}')",
                    )
                ]
                for index_name in (
                    "ix_professors_archived_updated_id",
                    "ix_professors_archived_name_id",
                    "ix_professors_archived_university_name_id",
                    "ix_professor_tag_links_tag_professor",
                    "ix_email_tasks_identity_root_active_status",
                    "ix_email_tasks_identity_status_scheduled_professor",
                    "ix_email_logs_identity_direction_professor_created",
                    "ix_identity_professor_match_results_identity_score_professor",
                )
            }
            triggers = {
                row[0]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'trigger'",
                )
            }
            name_plan = " ".join(
                row[3]
                for row in connection.execute(
                    "EXPLAIN QUERY PLAN "
                    "SELECT id FROM professors WHERE archived_at IS NULL "
                    "ORDER BY name ASC, id ASC LIMIT 20",
                )
            )
            fts_plan = " ".join(
                row[3]
                for row in connection.execute(
                    "EXPLAIN QUERY PLAN "
                    "SELECT rowid FROM professors_fts "
                    "WHERE professors_fts MATCH ?",
                    ('"机器学习"',),
                )
            )
        finally:
            connection.close()

        self.assertIn("ix_professors_archived_updated_id", indexes)
        self.assertIn("ix_professors_archived_name_id", indexes)
        self.assertIn("ix_professors_archived_university_name_id", indexes)
        self.assertIn("ix_professors_archived_trimmed_hierarchy", indexes)
        self.assertIn("ix_professors_archived_trimmed_title", indexes)
        self.assertIn(
            "ix_professor_tag_links_tag_professor",
            indexes_by_table["professor_tag_links"],
        )
        self.assertIn(
            "ix_email_tasks_identity_root_active_status",
            indexes_by_table["email_tasks"],
        )
        self.assertIn(
            "ix_email_tasks_identity_status_scheduled_professor",
            indexes_by_table["email_tasks"],
        )
        self.assertIn(
            "ix_email_logs_identity_direction_professor_created",
            indexes_by_table["email_logs"],
        )
        self.assertIn(
            "ix_identity_professor_match_results_identity_score_professor",
            indexes_by_table["identity_professor_match_results"],
        )
        self.assertEqual(
            indexed_columns["ix_professors_archived_updated_id"],
            ["archived_at", "updated_at", "id"],
        )
        self.assertEqual(
            indexed_columns["ix_professors_archived_name_id"],
            ["archived_at", "name", "id"],
        )
        self.assertEqual(
            indexed_columns["ix_professors_archived_university_name_id"],
            ["archived_at", "university", "name", "id"],
        )
        self.assertEqual(
            indexed_columns["ix_professor_tag_links_tag_professor"],
            ["tag_id", "professor_id"],
        )
        self.assertEqual(
            indexed_columns["ix_email_tasks_identity_root_active_status"],
            [
                "identity_id",
                "parent_task_id",
                "batch_send_canceled_at",
                "status",
                "professor_id",
            ],
        )
        self.assertEqual(
            indexed_columns["ix_email_tasks_identity_status_scheduled_professor"],
            ["identity_id", "status", "scheduled_at", "professor_id"],
        )
        self.assertEqual(
            indexed_columns["ix_email_logs_identity_direction_professor_created"],
            ["identity_id", "direction", "professor_id", "created_at", "id"],
        )
        self.assertEqual(
            indexed_columns[
                "ix_identity_professor_match_results_identity_score_professor"
            ],
            ["identity_id", "match_score", "professor_id"],
        )
        self.assertEqual(
            {"professors_fts_ai", "professors_fts_ad", "professors_fts_au"},
            {name for name in triggers if name.startswith("professors_fts_")},
        )
        self.assertIn("ix_professors_archived_name_id", name_plan)
        self.assertIn("VIRTUAL TABLE INDEX", fts_plan)

    async def _seed_management_professors(self) -> list[int]:
        base = datetime(2026, 8, 1, tzinfo=UTC)
        async with self.session_factory() as session:
            tag = ProfessorTag(
                name="重点",
                text_color="#111111",
                background_color="#eeeeee",
            )
            professors = [
                Professor(
                    name="普通导师",
                    email="normal@example.edu",
                    university="测试大学",
                    school="计算机学院",
                    department="软件系",
                    title="副教授",
                    research_direction="软件工程",
                    created_at=base,
                    updated_at=base,
                ),
                Professor(
                    name="王短词",
                    email="wang@example.edu",
                    university="测试大学",
                    school="计算机学院",
                    department="人工智能系",
                    title="教授、博导",
                    research_direction="机器学习与视觉",
                    tags=[tag],
                    created_at=base + timedelta(minutes=1),
                    updated_at=base + timedelta(minutes=1),
                ),
                Professor(
                    name="引号导师",
                    email="quote@example.edu",
                    university="测试大学",
                    school="医学院",
                    title="研究员",
                    research_direction='量子"算法与医学',
                    created_at=base + timedelta(minutes=2),
                    updated_at=base + timedelta(minutes=2),
                ),
                Professor(
                    name="符号导师",
                    email="symbol@example.edu",
                    university="另一大学",
                    school="工程学院",
                    research_direction="100%_精准检索",
                    created_at=base + timedelta(minutes=3),
                    updated_at=base + timedelta(minutes=3),
                ),
                Professor(
                    name="缺失字段",
                    email="missing@example.edu",
                    university=None,
                    school=None,
                    department=None,
                    title=None,
                    research_direction=None,
                    created_at=base + timedelta(minutes=4),
                    updated_at=base + timedelta(minutes=4),
                ),
                Professor(
                    name="末页导师",
                    email="last@example.edu",
                    university="另一大学",
                    school="理学院",
                    title="讲师",
                    research_direction="编译技术",
                    created_at=base + timedelta(minutes=5),
                    updated_at=base + timedelta(minutes=5),
                ),
                Professor(
                    name="归档导师",
                    email="archived@example.edu",
                    university="归档大学",
                    archived_at=base + timedelta(days=1),
                    created_at=base + timedelta(minutes=6),
                    updated_at=base + timedelta(minutes=6),
                ),
            ]
            session.add_all(professors)
            await session.commit()
            return [professor.id for professor in professors[:6]]

    async def _seed_dashboard_data(self) -> tuple[int, dict[str, int]]:
        now = datetime(2026, 8, 8, 12, tzinfo=UTC)
        async with self.session_factory() as session:
            identity = IdentityProfile(
                name="分页身份",
                profile_name="分页身份",
                sender_name="测试用户",
                email_address="pagination@example.com",
                smtp_host="smtp.example.com",
                smtp_port=465,
                smtp_username="pagination@example.com",
                smtp_password="secret",
            )
            llm = LLMProfile(
                name="分页模型",
                provider="openai",
                api_key="key",
                model_name="test-model",
            )
            professors = [
                Professor(
                    name="高分导师", email="high@example.edu", research_direction="AI"
                ),
                Professor(
                    name="回复导师", email="reply@example.edu", research_direction="NLP"
                ),
                Professor(
                    name="排程导师",
                    email="scheduled@example.edu",
                    research_direction="DB",
                ),
                Professor(
                    name="普通导师",
                    email="plain-dashboard@example.edu",
                    research_direction="OS",
                ),
            ]
            session.add_all([identity, llm, *professors])
            await session.flush()
            session.add_all(
                [
                    IdentityProfessorMatchResult(
                        identity_id=identity.id,
                        professor_id=professors[0].id,
                        match_score=95,
                        match_reason="高度匹配",
                        fit_points=[],
                        risk_points=[],
                        match_keywords=[],
                    ),
                    IdentityProfessorMatchResult(
                        identity_id=identity.id,
                        professor_id=professors[1].id,
                        match_score=85,
                        match_reason="匹配",
                        fit_points=[],
                        risk_points=[],
                        match_keywords=[],
                    ),
                    EmailTask(
                        identity_id=identity.id,
                        llm_profile_id=llm.id,
                        professor_id=professors[1].id,
                        status=EmailTaskStatus.REPLY_DETECTED.value,
                        is_replied=True,
                        sent_at=now - timedelta(days=1),
                        created_at=now - timedelta(days=2),
                        updated_at=now,
                    ),
                    EmailTask(
                        identity_id=identity.id,
                        llm_profile_id=llm.id,
                        professor_id=professors[2].id,
                        status=EmailTaskStatus.SCHEDULED.value,
                        scheduled_at=now + timedelta(days=1),
                        created_at=now,
                        updated_at=now,
                    ),
                ],
            )
            await session.commit()
            return identity.id, {
                "high": professors[0].id,
                "replied": professors[1].id,
                "scheduled": professors[2].id,
                "plain": professors[3].id,
            }


if __name__ == "__main__":
    unittest.main()
