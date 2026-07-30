from __future__ import annotations

import asyncio
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models import (
    BatchTask,
    BatchTaskStatus,
    CrawlJob,
    CrawlJobRun,
    CrawlJobStatus,
    EmailDirection,
    EmailLog,
    EmailTask,
    IdentityProfile,
    LLMProfile,
    MatchAnalysisJob,
    MatchAnalysisJobItem,
    MatchAnalysisJobItemStatus,
    MatchAnalysisJobStatus,
    MatchAnalysisRun,
    Professor,
)
from app.schemas.token_usage import TokenUsageRecordRead
from app.services.token_usage_records import (
    build_token_usage_visualization,
    list_token_usage_records,
)
from test.schema_database import create_schema_sqlite_database


class TokenUsageRecordsServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "token_usage_records_test.db"
        create_schema_sqlite_database(self.db_path)
        self.engine = create_async_engine(
            f"sqlite+aiosqlite:///{self.db_path.as_posix()}",
            future=True,
        )
        self.session_factory = async_sessionmaker(
            bind=self.engine,
            autoflush=False,
            expire_on_commit=False,
        )
        self._run_async(self._create_schema())

    def tearDown(self) -> None:
        self._run_async(self.engine.dispose())
        self.temp_dir.cleanup()

    def _run_async(self, awaitable):
        return asyncio.run(awaitable)

    async def _create_schema(self) -> None:
        return None

    async def _seed_records(self) -> None:
        now = datetime(2026, 4, 29, 10, 0, 0, tzinfo=UTC)
        async with self.session_factory() as session:
            identity = IdentityProfile(
                name="博士申请邮箱",
                profile_name="博士申请邮箱",
                sender_name="王同学",
                email_address="sender@example.com",
                smtp_host="smtp.example.com",
                smtp_port=465,
                smtp_username="sender@example.com",
                smtp_password="secret",
                default_language="zh-CN",
                outreach_generation_mode="llm",
            )
            llm_profile = LLMProfile(
                name="OpenAI",
                provider="openai",
                api_key="test-key",
                model_name="gpt-test",
            )
            professor = Professor(
                name="李老师",
                email="li@example.edu",
                university="示例大学",
                school="计算机学院",
                research_direction="信息抽取",
            )
            session.add_all([identity, llm_profile, professor])
            await session.flush()

            task = EmailTask(
                identity_id=identity.id,
                llm_profile_id=llm_profile.id,
                professor_id=professor.id,
                selected_material_ids=[],
            )
            session.add(task)
            await session.flush()

            crawl_job = CrawlJob(
                university="示例大学",
                school="计算机学院",
                start_url="https://example.edu/faculty",
                status=CrawlJobStatus.RUNNING.value,
                progress_current=1,
                progress_total=3,
                llm_profile_id=llm_profile.id,
                created_at=now - timedelta(minutes=4),
                updated_at=now - timedelta(minutes=4),
            )
            session.add(crawl_job)
            await session.flush()
            session.add(
                CrawlJobRun(
                    job_id=crawl_job.id,
                    attempt_number=1,
                    status=CrawlJobStatus.RUNNING.value,
                    input_tokens=100,
                    output_tokens=20,
                    cached_tokens=12,
                    total_tokens=120,
                    created_at=now - timedelta(minutes=4),
                    updated_at=now - timedelta(minutes=1),
                ),
            )

            session.add(
                MatchAnalysisRun(
                    email_task_id=task.id,
                    professor_id=professor.id,
                    identity_id=identity.id,
                    llm_profile_id=llm_profile.id,
                    success=True,
                    match_score=91,
                    prompt_tokens=200,
                    completion_tokens=30,
                    cached_tokens=80,
                    total_tokens=230,
                    created_at=now - timedelta(minutes=2),
                ),
            )

            session.add(
                EmailLog(
                    email_task_id=task.id,
                    identity_id=identity.id,
                    llm_profile_id=llm_profile.id,
                    professor_id=professor.id,
                    direction=EmailDirection.DRAFT.value,
                    subject="申请交流",
                    content="李老师您好",
                    provider_payload={
                        "source": "llm",
                        "usage": {
                            "prompt_tokens": 300,
                            "completion_tokens": 40,
                            "cached_tokens": 24,
                            "total_tokens": 340,
                        },
                    },
                    created_at=now - timedelta(minutes=3),
                ),
            )

            session.add(
                EmailLog(
                    email_task_id=task.id,
                    identity_id=identity.id,
                    llm_profile_id=llm_profile.id,
                    professor_id=professor.id,
                    direction=EmailDirection.DRAFT.value,
                    subject="模板草稿",
                    content="模板正文",
                    provider_payload={"source": "template", "usage": None},
                    created_at=now,
                ),
            )

            await session.commit()

    async def _seed_history_records(self) -> None:
        base = datetime(2026, 4, 30, 10, 0, 0, tzinfo=UTC)
        async with self.session_factory() as session:
            identity = IdentityProfile(
                name="博士申请邮箱",
                profile_name="博士申请邮箱",
                sender_name="王同学",
                email_address="sender@example.com",
                smtp_host="smtp.example.com",
                smtp_port=465,
                smtp_username="sender@example.com",
                smtp_password="secret",
                default_language="zh-CN",
                outreach_generation_mode="llm",
            )
            llm_profile = LLMProfile(
                name="OpenAI",
                provider="openai",
                api_key="test-key",
                model_name="gpt-test",
            )
            professor = Professor(
                name="李老师",
                email="li@example.edu",
                university="示例大学",
                school="计算机学院",
                research_direction="信息抽取",
            )
            session.add_all([identity, llm_profile, professor])
            await session.flush()

            task = EmailTask(
                identity_id=identity.id,
                llm_profile_id=llm_profile.id,
                professor_id=professor.id,
                selected_material_ids=[],
            )
            session.add(task)
            await session.flush()

            for index in range(7):
                created_at = base - timedelta(hours=index)
                session.add(
                    MatchAnalysisRun(
                        email_task_id=task.id,
                        professor_id=professor.id,
                        identity_id=identity.id,
                        llm_profile_id=llm_profile.id,
                        success=True,
                        match_score=80 + index,
                        prompt_tokens=100 + index,
                        completion_tokens=10 + index,
                        cached_tokens=5 * index,
                        total_tokens=110 + index * 2,
                        created_at=created_at,
                    )
                )

            crawl_job = CrawlJob(
                university="示例大学",
                school="计算机学院",
                start_url="https://example.edu/faculty",
                status=CrawlJobStatus.COMPLETED.value,
                progress_current=3,
                progress_total=3,
                llm_profile_id=llm_profile.id,
                created_at=base - timedelta(hours=8),
                updated_at=base - timedelta(hours=8),
            )
            session.add(crawl_job)
            await session.flush()
            session.add(
                CrawlJobRun(
                    job_id=crawl_job.id,
                    attempt_number=1,
                    status=CrawlJobStatus.COMPLETED.value,
                    input_tokens=800,
                    output_tokens=120,
                    total_tokens=920,
                    created_at=base - timedelta(hours=8),
                    updated_at=base - timedelta(hours=8),
                )
            )

            session.add(
                EmailLog(
                    email_task_id=task.id,
                    identity_id=identity.id,
                    llm_profile_id=llm_profile.id,
                    professor_id=professor.id,
                    direction=EmailDirection.DRAFT.value,
                    subject="申请交流",
                    content="李老师您好",
                    provider_payload={
                        "source": "llm",
                        "usage": {
                            "prompt_tokens": 500,
                            "completion_tokens": 60,
                            "total_tokens": 560,
                        },
                    },
                    created_at=base - timedelta(hours=9),
                )
            )

            await session.commit()

    async def _seed_alternate_model_record(self) -> None:
        now = datetime(2026, 4, 29, 10, 30, 0, tzinfo=UTC)
        async with self.session_factory() as session:
            identity = (await session.scalars(select(IdentityProfile))).first()
            existing_professor = (await session.scalars(select(Professor))).first()
            if identity is None or existing_professor is None:
                raise AssertionError("seed_records must run before adding alternate model")

            professor = Professor(
                name="备用模型导师",
                email="alt-model-professor@example.edu",
                university="示例大学",
                school="计算机学院",
                research_direction="信息抽取",
            )
            session.add(professor)
            await session.flush()

            llm_profile = LLMProfile(
                name="OpenAI Backup",
                provider="openai",
                api_key="test-key",
                model_name="gpt-alt",
            )
            session.add(llm_profile)
            await session.flush()

            task = EmailTask(
                identity_id=identity.id,
                llm_profile_id=llm_profile.id,
                professor_id=professor.id,
                selected_material_ids=[],
            )
            session.add(task)
            await session.flush()

            session.add(
                MatchAnalysisRun(
                    email_task_id=task.id,
                    professor_id=professor.id,
                    identity_id=identity.id,
                    llm_profile_id=llm_profile.id,
                    success=True,
                    match_score=88,
                    prompt_tokens=700,
                    completion_tokens=70,
                    cached_tokens=7,
                    total_tokens=770,
                    created_at=now,
                ),
            )
            await session.commit()

    async def _seed_batch_match_analysis_job(self) -> None:
        now = datetime(2026, 5, 3, 10, 0, 0, tzinfo=UTC)
        async with self.session_factory() as session:
            identity = IdentityProfile(
                name="博士申请邮箱",
                profile_name="博士申请邮箱",
                sender_name="王同学",
                email_address="sender@example.com",
                smtp_host="smtp.example.com",
                smtp_port=465,
                smtp_username="sender@example.com",
                smtp_password="secret",
                default_language="zh-CN",
                outreach_generation_mode="llm",
            )
            llm_profile = LLMProfile(
                name="OpenAI",
                provider="openai",
                api_key="test-key",
                model_name="gpt-test",
            )
            professors = [
                Professor(
                    name="李老师",
                    email="li@example.edu",
                    university="示例大学",
                    school="计算机学院",
                    research_direction="信息抽取",
                ),
                Professor(
                    name="张老师",
                    email="zhang@example.edu",
                    university="示例大学",
                    school="计算机学院",
                    research_direction="数据挖掘",
                ),
            ]
            session.add_all([identity, llm_profile, *professors])
            await session.flush()

            job = MatchAnalysisJob(
                name="批量匹配分析 2026-05-03 10:00",
                identity_id=identity.id,
                llm_profile_id=llm_profile.id,
                status=MatchAnalysisJobStatus.COMPLETED.value,
                target_count=2,
                succeeded_count=2,
                failed_count=0,
                skipped_count=0,
                total_prompt_tokens=300,
                total_completion_tokens=30,
                total_cached_tokens=11,
                total_tokens=330,
                started_at=now - timedelta(minutes=5),
                finished_at=now,
                created_at=now - timedelta(minutes=5),
                updated_at=now,
            )
            session.add(job)
            await session.flush()

            for index, professor in enumerate(professors):
                task = EmailTask(
                    identity_id=identity.id,
                    llm_profile_id=llm_profile.id,
                    professor_id=professor.id,
                    selected_material_ids=[],
                )
                session.add(task)
                await session.flush()
                run = MatchAnalysisRun(
                    email_task_id=task.id,
                    professor_id=professor.id,
                    identity_id=identity.id,
                    llm_profile_id=llm_profile.id,
                    success=True,
                    match_score=90 - index,
                    prompt_tokens=100 + index * 100,
                    completion_tokens=10 + index * 10,
                    cached_tokens=5 + index,
                    total_tokens=110 + index * 110,
                    created_at=now - timedelta(minutes=4 - index),
                )
                session.add(run)
                await session.flush()
                session.add(
                    MatchAnalysisJobItem(
                        job_id=job.id,
                        professor_id=professor.id,
                        email_task_id=task.id,
                        status=MatchAnalysisJobItemStatus.SUCCEEDED.value,
                        match_analysis_run_id=run.id,
                        prompt_tokens=run.prompt_tokens or 0,
                        completion_tokens=run.completion_tokens or 0,
                        cached_tokens=run.cached_tokens or 0,
                        total_tokens=run.total_tokens or 0,
                        started_at=run.created_at,
                        finished_at=run.created_at,
                        created_at=run.created_at,
                        updated_at=run.created_at,
                    )
                )

            await session.commit()

    async def _seed_batch_draft_generation_record(self) -> None:
        now = datetime(2026, 5, 4, 10, 0, 0, tzinfo=UTC)
        async with self.session_factory() as session:
            identity = IdentityProfile(
                name="博士申请邮箱",
                profile_name="博士申请邮箱",
                sender_name="王同学",
                email_address="batch-draft-sender@example.com",
                smtp_host="smtp.example.com",
                smtp_port=465,
                smtp_username="sender@example.com",
                smtp_password="secret",
                default_language="zh-CN",
                outreach_generation_mode="llm",
            )
            llm_profile = LLMProfile(
                name="OpenAI",
                provider="openai",
                api_key="test-key",
                model_name="gpt-test",
            )
            professor = Professor(
                name="批量导师",
                email="batch-draft@example.edu",
                university="示例大学",
                school="计算机学院",
                research_direction="信息抽取",
            )
            batch_task = BatchTask(
                identity=identity,
                llm_profile=llm_profile,
                name="批量草稿任务",
                status=BatchTaskStatus.RUNNING.value,
                target_count=1,
            )
            task = EmailTask(
                batch_task=batch_task,
                identity=identity,
                llm_profile=llm_profile,
                professor=professor,
                selected_material_ids=[],
            )
            session.add_all([batch_task, task])
            await session.flush()
            session.add(
                EmailLog(
                    email_task_id=task.id,
                    identity_id=identity.id,
                    llm_profile_id=llm_profile.id,
                    professor_id=professor.id,
                    direction=EmailDirection.DRAFT.value,
                    subject="批量草稿",
                    content="批量导师您好",
                    provider_payload={
                        "source": "llm",
                        "usage": {
                            "prompt_tokens": 612,
                            "completion_tokens": 248,
                            "total_tokens": 860,
                        },
                    },
                    created_at=now,
                ),
            )
            await session.commit()

    def test_lists_recent_function_level_token_records(self) -> None:
        self._run_async(self._seed_records())

        async def run_query():
            async with self.session_factory() as session:
                return await list_token_usage_records(session, page=1, page_size=20)

        result = self._run_async(run_query())

        self.assertEqual(
            [item.feature_type for item in result.records],
            [
                "match_analysis",
                "draft_generation",
                "crawl",
            ],
        )
        self.assertEqual(result.records[0].input_tokens, 200)
        self.assertEqual(result.records[0].output_tokens, 30)
        self.assertEqual(result.records[0].cached_tokens, 80)
        self.assertEqual(result.records[0].status, "success")
        self.assertEqual(result.records[1].total_tokens, 340)
        self.assertEqual(result.records[1].cached_tokens, 24)
        self.assertEqual(result.records[2].status, "running")
        self.assertEqual(result.records[2].cached_tokens, 12)
        self.assertEqual(result.records[2].title, "智能爬取 - 示例大学 · 计算机学院")
        self.assertEqual(result.summary.record_count, 3)
        self.assertEqual(result.summary.input_tokens, 600)
        self.assertEqual(result.summary.output_tokens, 90)
        self.assertEqual(result.summary.cached_tokens, 116)
        self.assertEqual(result.summary.total_tokens, 690)

    def test_groups_batch_match_analysis_job_as_one_token_record(self) -> None:
        self._run_async(self._seed_batch_match_analysis_job())

        async def run_query():
            async with self.session_factory() as session:
                return await list_token_usage_records(
                    session,
                    page=1,
                    page_size=20,
                    feature_type="match_analysis",
                )

        result = self._run_async(run_query())

        self.assertEqual(result.pagination.total_records, 1)
        self.assertEqual(result.summary.record_count, 1)
        self.assertEqual(result.summary.input_tokens, 300)
        self.assertEqual(result.summary.output_tokens, 30)
        self.assertEqual(result.summary.cached_tokens, 11)
        self.assertEqual(result.summary.total_tokens, 330)
        self.assertEqual(result.records[0].id, "match_analysis_job:1")
        self.assertEqual(result.records[0].title, "批量匹配分析 2026-05-03 10:00")

    def test_batch_draft_generation_email_log_enters_token_records(self) -> None:
        self._run_async(self._seed_batch_draft_generation_record())

        async def run_query():
            async with self.session_factory() as session:
                return await list_token_usage_records(session, page=1, page_size=20)

        result = self._run_async(run_query())

        self.assertEqual(result.summary.record_count, 1)
        self.assertEqual(result.summary.input_tokens, 612)
        self.assertEqual(result.summary.output_tokens, 248)
        self.assertEqual(result.summary.total_tokens, 860)
        self.assertEqual(result.records[0].feature_type, "draft_generation")

    def test_api_groups_batch_match_analysis_job_as_one_token_record(self) -> None:
        self._run_async(self._seed_batch_match_analysis_job())

        from app.core.database import get_async_session
        from main import create_app

        async def override_session():
            async with self.session_factory() as session:
                yield session

        app = create_app()
        app.dependency_overrides[get_async_session] = override_session
        client = TestClient(app)
        try:
            response = client.get(
                "/api/token-usage/records",
                params={
                    "page": 1,
                    "page_size": 20,
                    "feature_type": "match_analysis",
                },
            )
        finally:
            client.close()

        self.assertEqual(response.status_code, 200, msg=response.text)
        payload = response.json()
        self.assertEqual(payload["pagination"]["total_records"], 1)
        self.assertEqual(payload["summary"]["record_count"], 1)
        self.assertEqual(payload["records"][0]["id"], "match_analysis_job:1")
        self.assertEqual(payload["records"][0]["total_tokens"], 330)

    def test_api_returns_token_usage_records(self) -> None:
        self._run_async(self._seed_records())

        from app.core.database import get_async_session
        from main import create_app

        async def override_session():
            async with self.session_factory() as session:
                yield session

        app = create_app()
        app.dependency_overrides[get_async_session] = override_session
        client = TestClient(app)
        try:
            response = client.get("/api/token-usage/records?page=1&page_size=2")
        finally:
            client.close()

        self.assertEqual(response.status_code, 200, msg=response.text)
        payload = response.json()
        self.assertEqual(len(payload["records"]), 2)
        self.assertEqual(payload["records"][0]["feature_type"], "match_analysis")
        self.assertEqual(payload["summary"]["record_count"], 3)
        self.assertEqual(payload["pagination"]["total_records"], 3)

    def test_lists_records_with_pagination(self) -> None:
        self._run_async(self._seed_history_records())

        async def run_query():
            async with self.session_factory() as session:
                return await list_token_usage_records(session, page=2, page_size=5)

        result = self._run_async(run_query())

        self.assertEqual(result.pagination.page, 2)
        self.assertEqual(result.pagination.page_size, 5)
        self.assertEqual(result.pagination.total_records, 9)
        self.assertEqual(result.pagination.total_pages, 2)
        self.assertEqual(len(result.records), 4)
        self.assertEqual(result.records[0].feature_type, "match_analysis")
        self.assertEqual(result.summary.record_count, 9)

    def test_filters_records_by_feature_and_time_range(self) -> None:
        self._run_async(self._seed_history_records())
        start_at = datetime(2026, 4, 30, 6, 0, 0, tzinfo=UTC)
        end_at = datetime(2026, 4, 30, 8, 0, 0, tzinfo=UTC)

        async def run_query():
            async with self.session_factory() as session:
                return await list_token_usage_records(
                    session,
                    page=1,
                    page_size=5,
                    feature_type="match_analysis",
                    start_at=start_at,
                    end_at=end_at,
                )

        result = self._run_async(run_query())

        self.assertEqual(result.pagination.total_records, 3)
        self.assertEqual(
            [item.feature_type for item in result.records],
            ["match_analysis"] * 3,
        )
        self.assertEqual(
            [item.created_at.hour for item in result.records],
            [8, 7, 6],
        )

    def test_filters_records_by_model_name_and_returns_model_options(self) -> None:
        self._run_async(self._seed_records())
        self._run_async(self._seed_alternate_model_record())

        async def run_query():
            async with self.session_factory() as session:
                return await list_token_usage_records(
                    session,
                    page=1,
                    page_size=5,
                    model_name="gpt-alt",
                )

        result = self._run_async(run_query())

        self.assertEqual(result.model_options, ["gpt-alt", "gpt-test"])
        self.assertEqual(result.pagination.total_records, 1)
        self.assertEqual([item.model_name for item in result.records], ["gpt-alt"])
        self.assertEqual(result.summary.input_tokens, 700)

    def test_model_options_come_from_configured_llm_profiles(self) -> None:
        self._run_async(self._seed_records())
        self._run_async(self._seed_alternate_model_record())

        async def run_query():
            async with self.session_factory() as session:
                return await list_token_usage_records(
                    session,
                    page=1,
                    page_size=5,
                    feature_type="crawl",
                )

        result = self._run_async(run_query())

        self.assertEqual(result.model_options, ["gpt-alt", "gpt-test"])

    def test_api_returns_paginated_filtered_records(self) -> None:
        self._run_async(self._seed_history_records())

        from app.core.database import get_async_session
        from main import create_app

        async def override_session():
            async with self.session_factory() as session:
                yield session

        app = create_app()
        app.dependency_overrides[get_async_session] = override_session
        client = TestClient(app)
        try:
            response = client.get(
                "/api/token-usage/records",
                params={
                    "page": 2,
                    "page_size": 5,
                    "feature_type": "match_analysis",
                },
            )
        finally:
            client.close()

        self.assertEqual(response.status_code, 200, msg=response.text)
        payload = response.json()
        self.assertEqual(payload["pagination"]["page"], 2)
        self.assertEqual(payload["pagination"]["page_size"], 5)
        self.assertEqual(payload["pagination"]["total_records"], 7)
        self.assertEqual(len(payload["records"]), 2)

    def test_visualization_scans_candidate_records_once(self) -> None:
        now = datetime(2026, 4, 30, 10, 0, 0, tzinfo=UTC)
        record = TokenUsageRecordRead(
            id="match_analysis:1",
            feature_type="match_analysis",
            feature_label="匹配分析",
            title="李老师 - 匹配分析",
            input_tokens=100,
            output_tokens=10,
            cached_tokens=5,
            total_tokens=110,
            model_name="gpt-test",
            identity_name="博士申请邮箱",
            created_at=now - timedelta(hours=1),
            status="success",
        )

        async def run_query():
            async with self.session_factory() as session:
                loader = AsyncMock(return_value=[record])
                with patch(
                    "app.services.token_usage_records._list_all_candidate_records",
                    loader,
                ):
                    result = await build_token_usage_visualization(
                        session,
                        preset="last_6_hours",
                        now=now,
                    )
                return result, loader.await_count

        result, scan_count = self._run_async(run_query())

        self.assertEqual(scan_count, 1)
        self.assertEqual(result.summary.record_count, 1)
        self.assertEqual(result.summary.total_tokens, 110)
        self.assertEqual(result.chart.buckets[-2].total_tokens, 110)

    def test_builds_hourly_chart_for_recent_6_hours(self) -> None:
        self._run_async(self._seed_history_records())
        now = datetime(2026, 4, 30, 10, 0, 0, tzinfo=UTC)

        async def run_query():
            async with self.session_factory() as session:
                from app.services.token_usage_records import build_token_usage_chart

                return await build_token_usage_chart(
                    session,
                    feature_type="match_analysis",
                    preset="last_6_hours",
                    now=now,
                )

        result = self._run_async(run_query())

        self.assertEqual(result.granularity, "hour")
        self.assertEqual(len(result.buckets), 6)
        self.assertEqual(result.buckets[-1].bucket_label, "10:00")
        self.assertEqual(result.buckets[-1].input_tokens, 100)
        self.assertEqual(result.buckets[-1].output_tokens, 10)

    def test_builds_chart_filtered_by_model_name(self) -> None:
        self._run_async(self._seed_records())
        self._run_async(self._seed_alternate_model_record())
        start_at = datetime(2026, 4, 29, 10, 0, 0, tzinfo=UTC)
        end_at = datetime(2026, 4, 29, 10, 59, 0, tzinfo=UTC)

        async def run_query():
            async with self.session_factory() as session:
                from app.services.token_usage_records import build_token_usage_chart

                return await build_token_usage_chart(
                    session,
                    feature_type="all",
                    model_name="gpt-alt",
                    preset="custom",
                    start_at=start_at,
                    end_at=end_at,
                    now=end_at,
                )

        result = self._run_async(run_query())

        self.assertEqual(result.buckets[-1].input_tokens, 700)
        self.assertEqual(result.buckets[-1].output_tokens, 70)

    def test_custom_chart_uses_daily_granularity_for_long_ranges(self) -> None:
        self._run_async(self._seed_history_records())
        start_at = datetime(2026, 4, 27, 0, 0, 0, tzinfo=UTC)
        end_at = datetime(2026, 4, 30, 10, 0, 0, tzinfo=UTC)

        async def run_query():
            async with self.session_factory() as session:
                from app.services.token_usage_records import build_token_usage_chart

                return await build_token_usage_chart(
                    session,
                    feature_type="all",
                    preset="custom",
                    start_at=start_at,
                    end_at=end_at,
                    now=end_at,
                )

        result = self._run_async(run_query())

        self.assertEqual(result.granularity, "day")
        self.assertGreaterEqual(len(result.buckets), 4)
        self.assertEqual(result.buckets[-1].bucket_label, "04-30")
        self.assertGreater(result.buckets[-1].input_tokens, 0)


    def test_custom_chart_filters_by_exact_datetime_range(self) -> None:
        self._run_async(self._seed_history_records())
        start_at = datetime(2026, 4, 30, 10, 15, 0, tzinfo=UTC)
        end_at = datetime(2026, 4, 30, 10, 45, 0, tzinfo=UTC)

        async def run_query():
            async with self.session_factory() as session:
                from app.services.token_usage_records import build_token_usage_chart

                return await build_token_usage_chart(
                    session,
                    feature_type="match_analysis",
                    preset="custom",
                    start_at=start_at,
                    end_at=end_at,
                    now=end_at,
                )

        result = self._run_async(run_query())

        self.assertEqual(result.granularity, "hour")
        self.assertEqual(len(result.buckets), 1)
        self.assertEqual(result.buckets[0].bucket_label, "10:00")
        self.assertEqual(result.buckets[0].total_tokens, 0)

    def test_api_returns_chart_buckets(self) -> None:
        self._run_async(self._seed_history_records())

        from app.core.database import get_async_session
        from main import create_app

        async def override_session():
            async with self.session_factory() as session:
                yield session

        app = create_app()
        app.dependency_overrides[get_async_session] = override_session
        client = TestClient(app)
        try:
            response = client.get(
                "/api/token-usage/chart",
                params={
                    "preset": "custom",
                    "feature_type": "match_analysis",
                    "start_at": "2026-04-30T08:00:00+00:00",
                    "end_at": "2026-04-30T10:00:00+00:00",
                },
            )
        finally:
            client.close()

        self.assertEqual(response.status_code, 200, msg=response.text)
        payload = response.json()
        self.assertEqual(payload["granularity"], "hour")
        self.assertEqual(len(payload["buckets"]), 3)
        self.assertEqual(payload["buckets"][-1]["bucket_label"], "10:00")


if __name__ == "__main__":
    unittest.main()
