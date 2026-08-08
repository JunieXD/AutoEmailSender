from __future__ import annotations

import asyncio
import tempfile
import unittest
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient
from sqlalchemy import inspect, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.time import local_now
from app.models import (
    EmailDirection,
    EmailLog,
    EmailTask,
    EmailTaskStatus,
    IdentityProfile,
    LLMProfile,
    Professor,
)
from app.services.dashboard_stats import (
    _build_email_section,
    _build_email_trend,
    _datetime_in_range,
    _end_of_day,
    _parse_date_filter,
    build_dashboard_overview,
)
from test.schema_database import create_schema_sqlite_database


class DashboardStatsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "dashboard_stats_test.db"
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

    async def _seed_dashboard_data(self) -> tuple[int, int, int]:
        now = datetime.now(UTC)
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
                match_threshold=85,
            )
            llm_profile = LLMProfile(
                name="OpenAI",
                provider="openai",
                api_key="test-key",
                model_name="gpt-test",
            )
            alternate_llm_profile = LLMProfile(
                name="备用模型",
                provider="openai",
                api_key="test-key-2",
                model_name="gpt-test-2",
            )
            session.add_all([identity, llm_profile, alternate_llm_profile])
            await session.flush()

            professors = [
                Professor(
                    name="张老师",
                    email="zhang@example.edu",
                    university="示例大学",
                    school="计算机学院",
                    research_direction="信息抽取",
                    recent_papers=["Paper A"],
                    profile_url="https://example.edu/zhang",
                    created_at=now - timedelta(days=7),
                    updated_at=now - timedelta(days=7),
                ),
                Professor(
                    name="李老师",
                    email="li@example.edu",
                    university="示例大学",
                    school="计算机学院",
                    research_direction="智能体",
                    recent_papers=[],
                    profile_url="https://example.edu/li",
                    created_at=now - timedelta(days=6),
                    updated_at=now - timedelta(days=6),
                ),
                Professor(
                    name="王老师",
                    email="wang@example.edu",
                    university="第二大学",
                    school="工程学院",
                    research_direction="数据挖掘",
                    recent_papers=["Paper B"],
                    created_at=now - timedelta(days=5),
                    updated_at=now - timedelta(days=5),
                ),
                Professor(
                    name="赵老师",
                    email=None,
                    university="第二大学",
                    school="工程学院",
                    research_direction="机器学习",
                    recent_papers=[],
                    created_at=now - timedelta(days=4),
                    updated_at=now - timedelta(days=4),
                ),
                Professor(
                    name="孙老师",
                    email="sun@example.edu",
                    university="示例大学",
                    school="医学院",
                    research_direction=None,
                    recent_papers=[],
                    created_at=now - timedelta(days=3),
                    updated_at=now - timedelta(days=3),
                ),
                Professor(
                    name="周老师",
                    email="zhou@example.edu",
                    university=None,
                    school=None,
                    research_direction=None,
                    recent_papers=[],
                    created_at=now - timedelta(days=2),
                    updated_at=now - timedelta(days=2),
                ),
                Professor(
                    name="吴老师",
                    email="wu@example.edu",
                    university="第三大学",
                    school="理学院",
                    research_direction="理论计算机",
                    recent_papers=[],
                    created_at=now - timedelta(days=1),
                    updated_at=now - timedelta(days=1),
                ),
                Professor(
                    name="归档导师",
                    email="archived@example.edu",
                    university="归档大学",
                    school="旧学院",
                    research_direction="历史数据",
                    archived_at=now,
                    created_at=now,
                    updated_at=now,
                ),
            ]
            session.add_all(professors)
            await session.flush()

            tasks = [
                EmailTask(
                    identity_id=identity.id,
                    llm_profile_id=llm_profile.id,
                    professor_id=professors[0].id,
                    status=EmailTaskStatus.MATCHED.value,
                    match_score=92,
                    created_at=now - timedelta(days=6, minutes=4),
                    updated_at=now - timedelta(days=6, minutes=4),
                ),
                EmailTask(
                    identity_id=identity.id,
                    llm_profile_id=llm_profile.id,
                    professor_id=professors[1].id,
                    status=EmailTaskStatus.SENT.value,
                    match_score=88,
                    sent_at=now - timedelta(days=2),
                    created_at=now - timedelta(days=5),
                    updated_at=now - timedelta(days=2),
                ),
                EmailTask(
                    identity_id=identity.id,
                    llm_profile_id=llm_profile.id,
                    professor_id=professors[2].id,
                    status=EmailTaskStatus.REPLY_DETECTED.value,
                    match_score=82,
                    is_replied=True,
                    sent_at=now - timedelta(days=3),
                    created_at=now - timedelta(days=4),
                    updated_at=now - timedelta(days=1),
                ),
                EmailTask(
                    identity_id=identity.id,
                    llm_profile_id=llm_profile.id,
                    professor_id=professors[3].id,
                    status=EmailTaskStatus.SEND_FAILED.value,
                    match_score=95,
                    last_send_attempt_at=now - timedelta(days=1),
                    created_at=now - timedelta(days=3),
                    updated_at=now - timedelta(days=1),
                ),
                EmailTask(
                    identity_id=identity.id,
                    llm_profile_id=llm_profile.id,
                    professor_id=professors[4].id,
                    status=EmailTaskStatus.REVIEW_REQUIRED.value,
                    match_score=85,
                    created_at=now - timedelta(days=2),
                    updated_at=now - timedelta(days=2),
                ),
                EmailTask(
                    identity_id=identity.id,
                    llm_profile_id=llm_profile.id,
                    professor_id=professors[5].id,
                    status=EmailTaskStatus.SCHEDULED.value,
                    match_score=70,
                    scheduled_at=now + timedelta(days=1),
                    created_at=now - timedelta(days=1),
                    updated_at=now - timedelta(days=1),
                ),
            ]
            session.add_all(tasks)
            await session.flush()

            session.add_all(
                [
                    EmailLog(
                        email_task_id=tasks[1].id,
                        identity_id=identity.id,
                        llm_profile_id=llm_profile.id,
                        professor_id=professors[1].id,
                        direction=EmailDirection.SENT.value,
                        subject="申请交流",
                        content="李老师您好",
                        created_at=now - timedelta(days=2),
                    ),
                    EmailLog(
                        email_task_id=tasks[2].id,
                        identity_id=identity.id,
                        llm_profile_id=llm_profile.id,
                        professor_id=professors[2].id,
                        direction=EmailDirection.SENT.value,
                        subject="申请交流",
                        content="王老师您好",
                        created_at=now - timedelta(days=3),
                    ),
                    EmailLog(
                        email_task_id=tasks[2].id,
                        identity_id=identity.id,
                        llm_profile_id=llm_profile.id,
                        professor_id=professors[2].id,
                        direction=EmailDirection.RECEIVED.value,
                        subject="Re: 申请交流",
                        content="欢迎交流",
                        created_at=now - timedelta(days=1),
                    ),
                    EmailLog(
                        email_task_id=tasks[1].id,
                        identity_id=identity.id,
                        llm_profile_id=llm_profile.id,
                        professor_id=professors[1].id,
                        direction=EmailDirection.SENT.value,
                        subject="再次申请交流",
                        content="李老师您好，再次打扰",
                        created_at=now - timedelta(days=1, hours=12),
                    ),
                    EmailLog(
                        email_task_id=tasks[2].id,
                        identity_id=identity.id,
                        llm_profile_id=llm_profile.id,
                        professor_id=professors[2].id,
                        direction=EmailDirection.RECEIVED.value,
                        subject="Re: 申请交流",
                        content="补充回复",
                        created_at=now - timedelta(hours=12),
                    ),
                ]
            )
            await session.commit()
            return identity.id, llm_profile.id, alternate_llm_profile.id

    def test_dashboard_service_builds_mentor_and_email_sections(self) -> None:
        identity_id, llm_profile_id, _ = self._run_async(self._seed_dashboard_data())

        async def run_query():
            async with self.session_factory() as session:
                return await build_dashboard_overview(
                    session,
                    identity_id=identity_id,
                    llm_profile_id=llm_profile_id,
                )

        result = self._run_async(run_query())

        self.assertEqual(result.mentor.summary.total_professors, 7)
        self.assertEqual(result.mentor.summary.matched_professors, 6)
        self.assertEqual(result.mentor.summary.high_match_professors, 4)
        self.assertEqual(result.mentor.summary.high_score_uncontacted_count, 2)
        self.assertEqual(result.mentor.summary.high_score_threshold, 85)
        distribution = {item.bucket: item.count for item in result.mentor.match_score_distribution}
        self.assertEqual(distribution["unmatched"], 1)
        self.assertEqual(distribution["70_79"], 1)
        self.assertEqual(distribution["80_89"], 3)
        self.assertEqual(distribution["90_100"], 2)
        completeness = {item.key: item for item in result.mentor.profile_completeness}
        self.assertEqual(completeness["email"].count, 6)
        self.assertEqual(completeness["complete"].count, 3)
        self.assertEqual(result.mentor.school_distribution[0].school_name, "示例大学")
        self.assertEqual(result.mentor.school_distribution[0].count, 3)
        filter_by_university = {item.university: item for item in result.mentor.school_filters}
        self.assertIn("示例大学", filter_by_university)
        self.assertEqual(filter_by_university["示例大学"].count, 3)
        self.assertEqual(
            {item.school_name: item.count for item in filter_by_university["示例大学"].schools},
            {"计算机学院": 2, "医学院": 1},
        )
        self.assertIn("张老师", {item.name for item in result.mentor.high_score_uncontacted})
        self.assertIn("孙老师", {item.name for item in result.mentor.high_score_uncontacted})
        incomplete_by_name = {item.name: item for item in result.mentor.incomplete_professors}
        self.assertIn("邮箱", incomplete_by_name["赵老师"].missing_fields)

        self.assertEqual(result.email.summary.sent_count, 3)
        self.assertEqual(result.email.summary.sent_professor_count, 2)
        self.assertEqual(result.email.summary.total_professor_count, 7)
        self.assertAlmostEqual(result.email.summary.sent_professor_rate, 2 / 7)
        self.assertEqual(result.email.summary.contacted_professor_count, 2)
        self.assertEqual(result.email.summary.replied_count, 1)
        self.assertEqual(result.email.summary.reply_rate, 0.5)
        self.assertEqual(result.email.summary.send_failed_count, 1)
        self.assertEqual(result.email.summary.review_required_count, 1)
        self.assertEqual(result.email.summary.scheduled_count, 1)
        university_coverage = {
            item.university: item
            for item in result.email.outreach_coverage.universities
        }
        self.assertEqual(university_coverage["示例大学"].sent_professor_count, 1)
        self.assertEqual(university_coverage["示例大学"].total_professor_count, 3)
        self.assertAlmostEqual(university_coverage["示例大学"].sent_professor_rate, 1 / 3)
        self.assertEqual(university_coverage["示例大学"].contacted_professor_count, 1)
        self.assertEqual(university_coverage["示例大学"].replied_professor_count, 0)
        self.assertEqual(university_coverage["示例大学"].reply_rate, 0.0)
        self.assertEqual(university_coverage["第二大学"].sent_professor_count, 1)
        self.assertEqual(university_coverage["第二大学"].total_professor_count, 2)
        self.assertEqual(university_coverage["第二大学"].sent_professor_rate, 0.5)
        self.assertEqual(university_coverage["第二大学"].contacted_professor_count, 1)
        self.assertEqual(university_coverage["第二大学"].replied_professor_count, 1)
        self.assertEqual(university_coverage["第二大学"].reply_rate, 1.0)
        school_coverage = {
            (item.university, item.school): item
            for item in result.email.outreach_coverage.schools
        }
        computer_school = school_coverage[("示例大学", "计算机学院")]
        self.assertEqual(computer_school.sent_professor_count, 1)
        self.assertEqual(computer_school.total_professor_count, 2)
        self.assertEqual(computer_school.unsent_professor_count, 1)
        self.assertEqual(computer_school.sent_professor_rate, 0.5)
        self.assertEqual(computer_school.contacted_professor_count, 1)
        self.assertEqual(computer_school.replied_professor_count, 0)
        self.assertEqual(computer_school.reply_rate, 0.0)
        self.assertEqual(result.email.reply_wait.sample_count, 1)
        self.assertEqual(result.email.reply_wait.median_hours, 48.0)
        self.assertEqual(result.email.reply_wait.p75_hours, 48.0)
        reply_wait_distribution = {
            item.key: item
            for item in result.email.reply_wait.distribution
        }
        self.assertEqual(reply_wait_distribution["1_3_days"].count, 1)
        self.assertEqual(reply_wait_distribution["1_3_days"].rate, 1.0)
        status_distribution = {item.status: item.count for item in result.email.status_distribution}
        self.assertEqual(status_distribution["send_failed"], 1)
        self.assertEqual(status_distribution["review_required"], 1)
        self.assertEqual(status_distribution["scheduled"], 1)
        self.assertEqual(len(result.email.trend_30_days), 30)
        self.assertEqual(result.email.follow_ups[0].name, "赵老师")
        self.assertEqual(result.email.follow_ups[0].reason, "发送失败")

    def test_dashboard_keeps_large_task_columns_unloaded(self) -> None:
        async def scenario():
            identity_id, llm_profile_id, _ = await self._seed_dashboard_data()
            async with self.session_factory() as session:
                with patch(
                    "app.services.dashboard_stats._build_email_section",
                    new_callable=AsyncMock,
                    wraps=_build_email_section,
                ) as build_email_section:
                    await build_dashboard_overview(
                        session,
                        identity_id=identity_id,
                        llm_profile_id=llm_profile_id,
                    )

                loaded_task = build_email_section.await_args.kwargs["tasks"][0]
                return build_email_section.await_count, inspect(loaded_task).unloaded

        call_count, unloaded = self._run_async(scenario())

        self.assertEqual(call_count, 1)
        self.assertIn("match_reason", unloaded)
        self.assertIn("generated_content_text", unloaded)
        self.assertIn("generated_content_html", unloaded)

    def test_dashboard_service_ignores_failed_send_logs_for_sent_metrics(self) -> None:
        identity_id, llm_profile_id, _ = self._run_async(self._seed_dashboard_data())

        async def seed_failed_log() -> None:
            async with self.session_factory() as session:
                task = await session.scalar(
                    select(EmailTask).where(EmailTask.status == EmailTaskStatus.SEND_FAILED.value)
                )
                assert task is not None
                session.add(
                    EmailLog(
                        email_task_id=task.id,
                        identity_id=identity_id,
                        llm_profile_id=llm_profile_id,
                        professor_id=task.professor_id,
                        direction=EmailDirection.SENT.value,
                        subject="申请交流",
                        content="赵老师您好",
                        failure_summary="网络不可达",
                        created_at=datetime.now(UTC) - timedelta(hours=1),
                    )
                )
                await session.commit()

        self._run_async(seed_failed_log())

        async def run_query():
            async with self.session_factory() as session:
                return await build_dashboard_overview(
                    session,
                    identity_id=identity_id,
                    llm_profile_id=llm_profile_id,
                )

        result = self._run_async(run_query())

        self.assertEqual(result.email.summary.sent_count, 3)
        self.assertEqual(result.email.summary.sent_professor_count, 2)
        self.assertEqual(result.email.summary.contacted_professor_count, 2)
        self.assertEqual(result.email.summary.send_failed_count, 1)
        self.assertEqual(result.email.summary.send_failed_rate, 0.25)

    def test_dashboard_service_counts_workspace_visible_sent_logs_without_task_binding(self) -> None:
        identity_id, llm_profile_id, _ = self._run_async(self._seed_dashboard_data())

        async def seed_unbound_workspace_logs() -> None:
            async with self.session_factory() as session:
                zhang_task = await session.scalar(
                    select(EmailTask).where(EmailTask.match_score == 92)
                )
                sun_task = await session.scalar(
                    select(EmailTask).where(EmailTask.match_score == 85)
                )
                assert zhang_task is not None
                assert sun_task is not None
                session.add_all(
                    [
                        EmailLog(
                            email_task_id=None,
                            identity_id=identity_id,
                            llm_profile_id=llm_profile_id,
                            professor_id=zhang_task.professor_id,
                            direction=EmailDirection.SENT.value,
                            subject="申请交流",
                            content="张老师您好",
                            created_at=datetime.now(UTC) - timedelta(hours=2),
                        ),
                        EmailLog(
                            email_task_id=None,
                            identity_id=identity_id,
                            llm_profile_id=llm_profile_id,
                            professor_id=sun_task.professor_id,
                            direction=EmailDirection.SENT.value,
                            subject="申请交流失败",
                            content="孙老师您好",
                            failure_summary="SMTP 失败",
                            created_at=datetime.now(UTC) - timedelta(hours=1),
                        ),
                    ]
                )
                await session.commit()

        self._run_async(seed_unbound_workspace_logs())

        async def run_query():
            async with self.session_factory() as session:
                return await build_dashboard_overview(
                    session,
                    identity_id=identity_id,
                    llm_profile_id=llm_profile_id,
                )

        result = self._run_async(run_query())

        self.assertEqual(result.mentor.summary.high_score_uncontacted_count, 1)
        self.assertNotIn("张老师", {item.name for item in result.mentor.high_score_uncontacted})
        self.assertIn("孙老师", {item.name for item in result.mentor.high_score_uncontacted})
        self.assertEqual(result.email.summary.sent_count, 4)
        self.assertEqual(result.email.summary.sent_professor_count, 3)
        self.assertEqual(result.email.summary.contacted_professor_count, 3)

    def test_dashboard_service_counts_received_log_without_task_as_contacted_and_replied(self) -> None:
        identity_id, llm_profile_id, _ = self._run_async(self._seed_dashboard_data())
        reply_time = datetime.now(UTC) - timedelta(hours=3)

        async def seed_received_only_log() -> tuple[int, str]:
            async with self.session_factory() as session:
                professor = Professor(
                    name="仅回复老师",
                    email="reply-only-dashboard@example.edu",
                    university="第四大学",
                    school="通信学院",
                    research_direction="网络系统",
                    recent_papers=["Reply Paper"],
                    profile_url="https://example.edu/reply-only",
                    created_at=reply_time - timedelta(days=1),
                    updated_at=reply_time - timedelta(days=1),
                )
                session.add(professor)
                await session.flush()
                session.add(
                    EmailLog(
                        email_task_id=None,
                        identity_id=identity_id,
                        llm_profile_id=llm_profile_id,
                        professor_id=professor.id,
                        direction=EmailDirection.RECEIVED.value,
                        subject="Re: 邮箱同步",
                        content="欢迎交流",
                        created_at=reply_time,
                    ),
                )
                await session.commit()
                return professor.id, reply_time.astimezone().date().isoformat()

        professor_id, reply_date = self._run_async(seed_received_only_log())

        async def run_query():
            async with self.session_factory() as session:
                return await build_dashboard_overview(
                    session,
                    identity_id=identity_id,
                    llm_profile_id=llm_profile_id,
                    email_university="第四大学",
                    email_school="通信学院",
                )

        result = self._run_async(run_query())

        self.assertEqual(result.email.summary.sent_count, 0)
        self.assertEqual(result.email.summary.sent_professor_count, 0)
        self.assertEqual(result.email.summary.total_professor_count, 1)
        self.assertEqual(result.email.summary.sent_professor_rate, 0.0)
        self.assertEqual(result.email.summary.contacted_professor_count, 1)
        self.assertEqual(result.email.summary.replied_count, 1)
        self.assertEqual(result.email.summary.reply_rate, 1.0)
        university_coverage = {
            item.university: item
            for item in result.email.outreach_coverage.universities
        }
        self.assertEqual(university_coverage["第四大学"].contacted_professor_count, 1)
        self.assertEqual(university_coverage["第四大学"].replied_professor_count, 1)
        self.assertEqual(university_coverage["第四大学"].reply_rate, 1.0)
        self.assertEqual(result.email.reply_wait.sample_count, 0)
        self.assertIsNone(result.email.reply_wait.median_hours)
        self.assertTrue(all(item.count == 0 for item in result.email.reply_wait.distribution))
        trend_by_date = {bucket.date: bucket for bucket in result.email.trend_30_days}
        self.assertEqual(trend_by_date[reply_date].replied_count, 1)
        self.assertEqual(
            result.mentor.summary.total_professors,
            8,
            msg=f"sanity: received-only professor {professor_id} participates in mentor universe",
        )

    def test_dashboard_service_counts_multiple_unbound_received_logs_once_per_professor(self) -> None:
        identity_id, llm_profile_id, _ = self._run_async(self._seed_dashboard_data())
        reply_day = (datetime.now(UTC) - timedelta(days=1)).replace(
            hour=12,
            minute=0,
            second=0,
            microsecond=0,
        )

        async def seed_duplicate_received_logs() -> str:
            async with self.session_factory() as session:
                professor = Professor(
                    name="多封回复老师",
                    email="multi-reply-dashboard@example.edu",
                    university="第五大学",
                    school="统计学院",
                    research_direction="统计学习",
                    recent_papers=["Stats Paper"],
                    profile_url="https://example.edu/multi-reply",
                    created_at=reply_day - timedelta(days=1),
                    updated_at=reply_day - timedelta(days=1),
                )
                session.add(professor)
                await session.flush()
                session.add_all(
                    [
                        EmailLog(
                            email_task_id=None,
                            identity_id=identity_id,
                            llm_profile_id=llm_profile_id,
                            professor_id=professor.id,
                            direction=EmailDirection.RECEIVED.value,
                            subject="Re: 第一封",
                            content="第一封回复",
                            created_at=reply_day - timedelta(hours=4),
                        ),
                        EmailLog(
                            email_task_id=None,
                            identity_id=identity_id,
                            llm_profile_id=llm_profile_id,
                            professor_id=professor.id,
                            direction=EmailDirection.RECEIVED.value,
                            subject="Re: 第二封",
                            content="第二封回复",
                            created_at=reply_day - timedelta(hours=2),
                        ),
                    ],
                )
                await session.commit()
                return reply_day.date().isoformat()

        reply_date = self._run_async(seed_duplicate_received_logs())

        async def run_query():
            async with self.session_factory() as session:
                return await build_dashboard_overview(
                    session,
                    identity_id=identity_id,
                    llm_profile_id=llm_profile_id,
                    email_university="第五大学",
                    email_school="统计学院",
                )

        result = self._run_async(run_query())

        self.assertEqual(result.email.summary.contacted_professor_count, 1)
        self.assertEqual(result.email.summary.replied_count, 1)
        trend_by_date = {bucket.date: bucket for bucket in result.email.trend_30_days}
        self.assertEqual(trend_by_date[reply_date].replied_count, 1)

    def test_dashboard_service_counts_unbound_sent_log_in_summary_and_trend(self) -> None:
        identity_id, llm_profile_id, _ = self._run_async(self._seed_dashboard_data())
        sent_time = datetime.now(UTC) - timedelta(hours=5)

        async def seed_unbound_sent_log() -> str:
            async with self.session_factory() as session:
                professor = Professor(
                    name="无任务发送老师",
                    email="unbound-sent-dashboard@example.edu",
                    university="第六大学",
                    school="软件学院",
                    research_direction="软件工程",
                    recent_papers=["Software Paper"],
                    profile_url="https://example.edu/unbound-sent",
                    created_at=sent_time - timedelta(days=1),
                    updated_at=sent_time - timedelta(days=1),
                )
                session.add(professor)
                await session.flush()
                session.add(
                    EmailLog(
                        email_task_id=None,
                        identity_id=identity_id,
                        llm_profile_id=llm_profile_id,
                        professor_id=professor.id,
                        direction=EmailDirection.SENT.value,
                        subject="邮箱同步发送",
                        content="老师您好",
                        created_at=sent_time,
                    ),
                )
                await session.commit()
                return sent_time.astimezone().date().isoformat()

        sent_date = self._run_async(seed_unbound_sent_log())

        async def run_query():
            async with self.session_factory() as session:
                return await build_dashboard_overview(
                    session,
                    identity_id=identity_id,
                    llm_profile_id=llm_profile_id,
                    email_university="第六大学",
                    email_school="软件学院",
                )

        result = self._run_async(run_query())

        self.assertEqual(result.email.summary.sent_count, 1)
        self.assertEqual(result.email.summary.sent_professor_count, 1)
        self.assertEqual(result.email.summary.total_professor_count, 1)
        self.assertEqual(result.email.summary.sent_professor_rate, 1.0)
        self.assertEqual(result.email.summary.contacted_professor_count, 1)
        self.assertEqual(result.email.summary.replied_count, 0)
        trend_by_date = {bucket.date: bucket for bucket in result.email.trend_30_days}
        self.assertEqual(trend_by_date[sent_date].sent_count, 1)

    def test_dashboard_service_is_identity_scoped_not_llm_scoped(self) -> None:
        identity_id, _, alternate_llm_profile_id = self._run_async(self._seed_dashboard_data())

        async def run_query():
            async with self.session_factory() as session:
                return await build_dashboard_overview(
                    session,
                    identity_id=identity_id,
                    llm_profile_id=alternate_llm_profile_id,
                )

        result = self._run_async(run_query())

        self.assertEqual(result.mentor.summary.matched_professors, 6)
        self.assertEqual(result.email.summary.sent_count, 3)

    def test_dashboard_service_filters_mentor_analysis_by_university_and_school(self) -> None:
        identity_id, llm_profile_id, _ = self._run_async(self._seed_dashboard_data())

        async def run_query():
            async with self.session_factory() as session:
                return await build_dashboard_overview(
                    session,
                    identity_id=identity_id,
                    llm_profile_id=llm_profile_id,
                    university="示例大学",
                    school="计算机学院",
                )

        result = self._run_async(run_query())

        self.assertEqual(result.mentor.summary.total_professors, 2)
        self.assertEqual(result.mentor.summary.matched_professors, 2)
        self.assertEqual(result.mentor.summary.high_match_professors, 2)

        distribution = {item.bucket: item.count for item in result.mentor.match_score_distribution}
        self.assertEqual(distribution["unmatched"], 0)
        self.assertEqual(distribution["80_89"], 1)
        self.assertEqual(distribution["90_100"], 1)

        profile_distribution = {item.key: item.count for item in result.mentor.profile_completeness_distribution}
        self.assertEqual(sum(profile_distribution.values()), 2)
        self.assertEqual(profile_distribution["complete"], 1)
        self.assertEqual(profile_distribution["missing_recent_papers"], 1)

        school_distribution = {item.school_name: item.count for item in result.mentor.school_distribution}
        self.assertEqual(school_distribution["示例大学"], 3)
        self.assertEqual(school_distribution["第二大学"], 2)
        self.assertEqual(school_distribution["学校未填写"], 1)

        self.assertEqual(result.mentor.active_filter.university, "示例大学")
        self.assertEqual(result.mentor.active_filter.school, "计算机学院")

    def test_dashboard_service_filters_email_metrics_by_university_and_school(self) -> None:
        identity_id, llm_profile_id, _ = self._run_async(self._seed_dashboard_data())

        async def run_query():
            async with self.session_factory() as session:
                return await build_dashboard_overview(
                    session,
                    identity_id=identity_id,
                    llm_profile_id=llm_profile_id,
                    email_university="示例大学",
                    email_school="计算机学院",
                )

        result = self._run_async(run_query())

        self.assertEqual(result.mentor.active_filter.university, None)
        self.assertEqual(result.mentor.active_filter.school, None)
        self.assertEqual(result.email.summary.sent_count, 2)
        self.assertEqual(result.email.summary.sent_professor_count, 1)
        self.assertEqual(result.email.summary.total_professor_count, 2)
        self.assertEqual(result.email.summary.sent_professor_rate, 0.5)
        self.assertEqual(result.email.summary.contacted_professor_count, 1)
        self.assertEqual(result.email.summary.replied_count, 0)
        self.assertEqual(result.email.summary.reply_rate, 0.0)
        self.assertTrue(all(item.failed_count == 0 for item in result.email.trend_30_days))

    def test_dashboard_service_filters_email_metrics_by_sent_date_range(self) -> None:
        identity_id, llm_profile_id, _ = self._run_async(self._seed_dashboard_data())
        today = local_now().date()
        start_date = (today - timedelta(days=3)).isoformat()
        end_date = (today - timedelta(days=3)).isoformat()

        async def run_query():
            async with self.session_factory() as session:
                return await build_dashboard_overview(
                    session,
                    identity_id=identity_id,
                    llm_profile_id=llm_profile_id,
                    start_date=start_date,
                    end_date=end_date,
                )

        result = self._run_async(run_query())

        self.assertEqual(result.email.summary.sent_count, 1)
        self.assertEqual(result.email.summary.sent_professor_count, 1)
        self.assertEqual(result.email.summary.total_professor_count, 7)
        self.assertAlmostEqual(result.email.summary.sent_professor_rate, 1 / 7)
        self.assertEqual(result.email.summary.contacted_professor_count, 1)
        self.assertEqual(result.email.summary.replied_count, 0)
        self.assertEqual(result.email.summary.reply_rate, 0.0)
        coverage_by_university = {
            item.university: item
            for item in result.email.outreach_coverage.universities
        }
        self.assertEqual(coverage_by_university["示例大学"].sent_professor_count, 0)
        self.assertEqual(coverage_by_university["第二大学"].sent_professor_count, 1)
        self.assertEqual(coverage_by_university["第二大学"].contacted_professor_count, 1)
        self.assertEqual(coverage_by_university["第二大学"].replied_professor_count, 0)
        self.assertEqual(coverage_by_university["第二大学"].reply_rate, 0.0)

    def test_dashboard_email_trend_uses_local_calendar_days(self) -> None:
        shanghai = timezone(timedelta(hours=8))
        event_at = datetime(2026, 8, 6, 16, 30, tzinfo=UTC)
        start_at = _parse_date_filter(
            "2026-08-07",
            field_name="start_date",
            local_timezone=shanghai,
        )
        end_at = _end_of_day(
            _parse_date_filter(
                "2026-08-07",
                field_name="end_date",
                local_timezone=shanghai,
            ),
            local_timezone=shanghai,
        )

        trend = _build_email_trend(
            [(1, 1, event_at)],
            [],
            replied_fallback_tasks=[],
            start_at=start_at,
            end_at=end_at,
            local_timezone=shanghai,
        )

        self.assertTrue(_datetime_in_range(event_at, start_at=start_at, end_at=end_at))
        self.assertEqual(
            [(bucket.date, bucket.sent_count) for bucket in trend],
            [("2026-08-07", 1)],
        )

    def test_dashboard_service_filters_reply_wait_by_first_reply_date(self) -> None:
        identity_id, llm_profile_id, _ = self._run_async(self._seed_dashboard_data())
        reply_date = (local_now().date() - timedelta(days=1)).isoformat()

        async def run_query():
            async with self.session_factory() as session:
                return await build_dashboard_overview(
                    session,
                    identity_id=identity_id,
                    llm_profile_id=llm_profile_id,
                    email_university="第二大学",
                    email_school="工程学院",
                    start_date=reply_date,
                    end_date=reply_date,
                )

        result = self._run_async(run_query())

        self.assertEqual(result.email.summary.sent_count, 0)
        self.assertEqual(result.email.reply_wait.sample_count, 1)
        self.assertEqual(result.email.reply_wait.median_hours, 48.0)
        self.assertEqual(result.email.reply_wait.p75_hours, 48.0)

    def test_dashboard_service_reply_wait_does_not_reset_after_follow_up(self) -> None:
        identity_id, llm_profile_id, _ = self._run_async(self._seed_dashboard_data())

        async def seed_follow_up() -> None:
            async with self.session_factory() as session:
                task = await session.scalar(
                    select(EmailTask).where(EmailTask.match_score == 82)
                )
                assert task is not None
                session.add(
                    EmailLog(
                        email_task_id=task.id,
                        identity_id=identity_id,
                        llm_profile_id=llm_profile_id,
                        professor_id=task.professor_id,
                        direction=EmailDirection.SENT.value,
                        subject="再次申请交流",
                        content="王老师您好，再次打扰",
                        created_at=datetime.now(UTC) - timedelta(days=2),
                    ),
                )
                await session.commit()

        self._run_async(seed_follow_up())

        async def run_query():
            async with self.session_factory() as session:
                return await build_dashboard_overview(
                    session,
                    identity_id=identity_id,
                    llm_profile_id=llm_profile_id,
                    email_university="第二大学",
                    email_school="工程学院",
                )

        result = self._run_async(run_query())

        self.assertEqual(result.email.reply_wait.sample_count, 1)
        self.assertEqual(result.email.reply_wait.median_hours, 48.0)

    def test_dashboard_service_uses_first_successful_send_after_failed_copy(self) -> None:
        identity_id, llm_profile_id, _ = self._run_async(self._seed_dashboard_data())
        now = datetime.now(UTC)
        anchor = now.replace(hour=12, minute=0, second=0, microsecond=0)
        today = now.date().isoformat()

        async def seed_retry_logs() -> None:
            async with self.session_factory() as session:
                professor = Professor(
                    name="重试成功老师",
                    email="retry-success@example.edu",
                    university="第七大学",
                    school="自动化学院",
                    research_direction="机器人",
                    recent_papers=["Robotics Paper"],
                    profile_url="https://example.edu/retry-success",
                    created_at=anchor - timedelta(days=2),
                    updated_at=anchor - timedelta(days=2),
                )
                session.add(professor)
                await session.flush()
                session.add_all(
                    [
                        EmailLog(
                            email_task_id=None,
                            identity_id=identity_id,
                            llm_profile_id=llm_profile_id,
                            professor_id=professor.id,
                            direction=EmailDirection.SENT.value,
                            subject="申请交流",
                            content="首次发送失败",
                            normalized_message_id="retry-success@example.com",
                            failure_summary="SMTP 暂时不可用",
                            created_at=anchor - timedelta(days=1),
                        ),
                        EmailLog(
                            email_task_id=None,
                            identity_id=identity_id,
                            llm_profile_id=llm_profile_id,
                            professor_id=professor.id,
                            direction=EmailDirection.SENT.value,
                            subject="申请交流",
                            content="重试发送成功",
                            rfc_message_id="<retry-success@example.com>",
                            created_at=anchor - timedelta(hours=2),
                        ),
                        EmailLog(
                            email_task_id=None,
                            identity_id=identity_id,
                            llm_profile_id=llm_profile_id,
                            professor_id=professor.id,
                            direction=EmailDirection.RECEIVED.value,
                            subject="Re: 申请交流",
                            content="收到",
                            normalized_message_id="retry-success-reply@example.com",
                            created_at=anchor - timedelta(hours=1),
                        ),
                    ],
                )
                await session.commit()

        self._run_async(seed_retry_logs())

        async def run_query():
            async with self.session_factory() as session:
                return await build_dashboard_overview(
                    session,
                    identity_id=identity_id,
                    llm_profile_id=llm_profile_id,
                    email_university="第七大学",
                    email_school="自动化学院",
                    start_date=today,
                    end_date=today,
                )

        result = self._run_async(run_query())

        self.assertEqual(result.email.summary.sent_count, 1)
        self.assertEqual(result.email.summary.sent_professor_count, 1)
        self.assertEqual(result.email.reply_wait.sample_count, 1)
        self.assertEqual(result.email.reply_wait.median_hours, 1.0)

    def test_dashboard_service_returns_zero_sent_professor_rate_for_empty_email_scope(self) -> None:
        identity_id, llm_profile_id, _ = self._run_async(self._seed_dashboard_data())

        async def run_query():
            async with self.session_factory() as session:
                return await build_dashboard_overview(
                    session,
                    identity_id=identity_id,
                    llm_profile_id=llm_profile_id,
                    email_university="不存在的大学",
                )

        result = self._run_async(run_query())

        self.assertEqual(result.email.summary.sent_professor_count, 0)
        self.assertEqual(result.email.summary.total_professor_count, 0)
        self.assertEqual(result.email.summary.sent_professor_rate, 0.0)
        self.assertGreater(len(result.email.outreach_coverage.universities), 0)
        self.assertGreater(len(result.email.outreach_coverage.schools), 0)
        self.assertEqual(result.email.reply_wait.sample_count, 0)
        self.assertIsNone(result.email.reply_wait.median_hours)


    def test_dashboard_service_excludes_replies_outside_date_range(self) -> None:
        identity_id, llm_profile_id, _ = self._run_async(self._seed_dashboard_data())
        today = local_now().date()
        start_date = (today - timedelta(days=3)).isoformat()
        end_date = (today - timedelta(days=3)).isoformat()

        async def run_query():
            async with self.session_factory() as session:
                return await build_dashboard_overview(
                    session,
                    identity_id=identity_id,
                    llm_profile_id=llm_profile_id,
                    start_date=start_date,
                    end_date=end_date,
                )

        result = self._run_async(run_query())

        self.assertEqual(result.email.summary.sent_count, 1)
        self.assertEqual(result.email.summary.contacted_professor_count, 1)
        self.assertEqual(result.email.summary.replied_count, 0)
        self.assertEqual(result.email.summary.reply_rate, 0.0)
        self.assertTrue(all(item.replied_count == 0 for item in result.email.trend_30_days))

    def test_dashboard_endpoint_returns_overview(self) -> None:
        identity_id, llm_profile_id, _ = self._run_async(self._seed_dashboard_data())

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
                "/api/dashboard/overview",
                params={"identity_id": identity_id, "llm_profile_id": llm_profile_id},
            )
        finally:
            client.close()

        self.assertEqual(response.status_code, 200, msg=response.text)
        payload = response.json()
        self.assertEqual(payload["mentor"]["summary"]["total_professors"], 7)
        self.assertEqual(payload["email"]["summary"]["sent_count"], 3)
        self.assertEqual(payload["email"]["summary"]["sent_professor_count"], 2)
        self.assertEqual(payload["email"]["summary"]["total_professor_count"], 7)
        self.assertAlmostEqual(payload["email"]["summary"]["sent_professor_rate"], 2 / 7)
        self.assertEqual(payload["email"]["reply_wait"]["sample_count"], 1)
        coverage_by_university = {
            item["university"]: item
            for item in payload["email"]["outreach_coverage"]["universities"]
        }
        self.assertEqual(coverage_by_university["示例大学"]["sent_professor_count"], 1)
        self.assertEqual(coverage_by_university["示例大学"]["total_professor_count"], 3)
        self.assertEqual(coverage_by_university["示例大学"]["contacted_professor_count"], 1)
        self.assertEqual(coverage_by_university["示例大学"]["replied_professor_count"], 0)
        self.assertEqual(coverage_by_university["第二大学"]["reply_rate"], 1.0)
        self.assertEqual(payload["email"]["follow_ups"][0]["task_id"], 4)

    def test_dashboard_endpoint_does_not_require_llm_profile(self) -> None:
        identity_id, _, _ = self._run_async(self._seed_dashboard_data())

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
                "/api/dashboard/overview",
                params={"identity_id": identity_id},
            )
        finally:
            client.close()

        self.assertEqual(response.status_code, 200, msg=response.text)
        payload = response.json()
        self.assertEqual(payload["mentor"]["summary"]["matched_professors"], 6)
        self.assertEqual(payload["email"]["summary"]["sent_count"], 3)

    def test_dashboard_endpoint_accepts_mentor_filters(self) -> None:
        identity_id, llm_profile_id, _ = self._run_async(self._seed_dashboard_data())

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
                "/api/dashboard/overview",
                params={
                    "identity_id": identity_id,
                    "llm_profile_id": llm_profile_id,
                    "university": "示例大学",
                    "school": "计算机学院",
                },
            )
        finally:
            client.close()

        self.assertEqual(response.status_code, 200, msg=response.text)
        payload = response.json()
        distribution = {
            item["bucket"]: item["count"]
            for item in payload["mentor"]["match_score_distribution"]
        }
        self.assertEqual(distribution["80_89"], 1)
        self.assertEqual(distribution["90_100"], 1)
        self.assertEqual(payload["mentor"]["active_filter"]["university"], "示例大学")
        self.assertEqual(payload["mentor"]["active_filter"]["school"], "计算机学院")

    def test_dashboard_endpoint_accepts_email_date_filters(self) -> None:
        identity_id, llm_profile_id, _ = self._run_async(self._seed_dashboard_data())

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
                "/api/dashboard/overview",
                params={
                    "identity_id": identity_id,
                    "llm_profile_id": llm_profile_id,
                    "start_date": (local_now().date() - timedelta(days=3)).isoformat(),
                    "end_date": (local_now().date() - timedelta(days=3)).isoformat(),
                },
            )
        finally:
            client.close()

        self.assertEqual(response.status_code, 200, msg=response.text)
        payload = response.json()
        self.assertEqual(payload["email"]["summary"]["contacted_professor_count"], 1)
        self.assertEqual(payload["email"]["summary"]["replied_count"], 0)

    def test_dashboard_endpoint_accepts_email_school_filters(self) -> None:
        identity_id, llm_profile_id, _ = self._run_async(self._seed_dashboard_data())

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
                "/api/dashboard/overview",
                params={
                    "identity_id": identity_id,
                    "llm_profile_id": llm_profile_id,
                    "email_university": "示例大学",
                    "email_school": "计算机学院",
                },
            )
        finally:
            client.close()

        self.assertEqual(response.status_code, 200, msg=response.text)
        payload = response.json()
        self.assertEqual(payload["mentor"]["active_filter"]["university"], None)
        self.assertEqual(payload["mentor"]["active_filter"]["school"], None)
        self.assertEqual(payload["email"]["summary"]["sent_count"], 2)
        self.assertEqual(payload["email"]["summary"]["sent_professor_count"], 1)
        self.assertEqual(payload["email"]["summary"]["total_professor_count"], 2)
        self.assertEqual(payload["email"]["summary"]["sent_professor_rate"], 0.5)
        self.assertEqual(payload["email"]["summary"]["contacted_professor_count"], 1)

    def test_dashboard_endpoint_rejects_missing_identity(self) -> None:
        _, llm_profile_id, _ = self._run_async(self._seed_dashboard_data())

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
                "/api/dashboard/overview",
                params={"identity_id": 999, "llm_profile_id": llm_profile_id},
            )
        finally:
            client.close()

        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["detail"], "未找到身份")


if __name__ == "__main__":
    unittest.main()
