from __future__ import annotations

import asyncio
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from math import ceil
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import event, insert
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.query_chunks import DEFAULT_SQL_IN_CHUNK_SIZE
from app.modules.workspace.api import refresh_workspace_replies
from app.modules.workspace.thread import build_workspace_thread
from app.models import (
    EmailDirection,
    EmailLog,
    EmailTask,
    EmailTaskStatus,
    IdentityCommunicationGroup,
    IdentityProfile,
    LLMProfile,
    Professor,
)
from app.services.contact_status import build_contact_status_by_professor
from app.services.dashboard_stats import build_dashboard_overview
from test.schema_database import create_schema_sqlite_database


class SharedIdentityCommunicationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "shared_communication.db"
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

    def tearDown(self) -> None:
        self._run_async(self.engine.dispose())
        self.temp_dir.cleanup()

    def _run_async(self, awaitable):
        return asyncio.run(awaitable)

    def test_workspace_merges_group_history_deduplicates_and_keeps_drafts_isolated(
        self,
    ) -> None:
        async def scenario():
            async with self.session_factory() as session:
                now = datetime.now(UTC)
                group = IdentityCommunicationGroup()
                identity_a = self._identity("身份 A", "workspace-a@example.com")
                identity_b = self._identity("身份 B", "workspace-b@example.com")
                identity_c = self._identity("身份 C", "workspace-c@example.com")
                llm_profile = self._llm_profile()
                professor = Professor(
                    name="共享导师", email="shared-professor@example.edu"
                )
                history_only_professor = Professor(
                    name="仅有历史导师",
                    email="history-only@example.edu",
                )
                session.add_all(
                    [
                        group,
                        identity_a,
                        identity_b,
                        identity_c,
                        llm_profile,
                        professor,
                        history_only_professor,
                    ],
                )
                await session.flush()
                identity_a.communication_group_id = group.id
                identity_b.communication_group_id = group.id

                task_a = EmailTask(
                    identity_id=identity_a.id,
                    llm_profile_id=llm_profile.id,
                    professor_id=professor.id,
                    status=EmailTaskStatus.REVIEW_REQUIRED.value,
                    created_at=now - timedelta(days=2),
                    updated_at=now - timedelta(days=2),
                )
                task_b = EmailTask(
                    identity_id=identity_b.id,
                    llm_profile_id=llm_profile.id,
                    professor_id=professor.id,
                    status=EmailTaskStatus.MATCHED.value,
                    created_at=now - timedelta(days=1),
                    updated_at=now - timedelta(days=1),
                )
                session.add_all([task_a, task_b])
                await session.flush()

                session.add_all(
                    [
                        EmailLog(
                            email_task_id=task_a.id,
                            identity_id=identity_a.id,
                            llm_profile_id=llm_profile.id,
                            professor_id=professor.id,
                            direction=EmailDirection.DRAFT.value,
                            subject="A 的当前草稿",
                            content="只在 A 下显示",
                            created_at=now - timedelta(hours=8),
                        ),
                        EmailLog(
                            email_task_id=task_b.id,
                            identity_id=identity_b.id,
                            llm_profile_id=llm_profile.id,
                            professor_id=professor.id,
                            direction=EmailDirection.DRAFT.value,
                            subject="B 的当前草稿",
                            content="只在 B 下显示",
                            created_at=now - timedelta(hours=7),
                        ),
                        EmailLog(
                            email_task_id=task_a.id,
                            identity_id=identity_a.id,
                            llm_profile_id=llm_profile.id,
                            professor_id=professor.id,
                            direction=EmailDirection.SENT.value,
                            subject="共享主题",
                            content="纯文本副本",
                            normalized_message_id="<shared-message@example.com>",
                            reply_headers={"references": []},
                            created_at=now - timedelta(hours=6),
                        ),
                        EmailLog(
                            email_task_id=task_b.id,
                            identity_id=identity_b.id,
                            llm_profile_id=llm_profile.id,
                            professor_id=professor.id,
                            direction=EmailDirection.SENT.value,
                            subject=None,
                            content="HTML 副本",
                            content_html="<p>HTML 副本</p>",
                            rfc_message_id="shared-message@example.com",
                            created_at=now - timedelta(hours=5),
                        ),
                        EmailLog(
                            email_task_id=task_b.id,
                            identity_id=identity_b.id,
                            llm_profile_id=llm_profile.id,
                            professor_id=professor.id,
                            direction=EmailDirection.RECEIVED.value,
                            subject="Re: 共享主题",
                            content="欢迎联系",
                            normalized_message_id="reply-message@example.com",
                            created_at=now - timedelta(hours=4),
                        ),
                        EmailLog(
                            email_task_id=None,
                            identity_id=identity_c.id,
                            llm_profile_id=llm_profile.id,
                            professor_id=professor.id,
                            direction=EmailDirection.SENT.value,
                            subject="C 的独立记录",
                            content="不得出现在共享组中",
                            normalized_message_id="outside@example.com",
                            created_at=now - timedelta(hours=3),
                        ),
                        EmailLog(
                            email_task_id=None,
                            identity_id=identity_b.id,
                            llm_profile_id=llm_profile.id,
                            professor_id=history_only_professor.id,
                            direction=EmailDirection.RECEIVED.value,
                            subject="B 收到的历史",
                            content="A 没有任务时也应显示",
                            normalized_message_id="history-only@example.com",
                            created_at=now - timedelta(hours=2),
                        ),
                    ],
                )
                await session.commit()

                thread_a = await build_workspace_thread(
                    session,
                    professor_id=professor.id,
                    identity_id=identity_a.id,
                    llm_profile_id=llm_profile.id,
                )
                thread_b = await build_workspace_thread(
                    session,
                    professor_id=professor.id,
                    identity_id=identity_b.id,
                    llm_profile_id=llm_profile.id,
                )
                thread_c = await build_workspace_thread(
                    session,
                    professor_id=professor.id,
                    identity_id=identity_c.id,
                    llm_profile_id=llm_profile.id,
                )
                history_only = await build_workspace_thread(
                    session,
                    professor_id=history_only_professor.id,
                    identity_id=identity_a.id,
                    llm_profile_id=llm_profile.id,
                )
                return thread_a, thread_b, thread_c, history_only, task_a.id, task_b.id

        thread_a, thread_b, thread_c, history_only, task_a_id, task_b_id = (
            self._run_async(
                scenario(),
            )
        )

        self.assertEqual(thread_a.current_task.id, task_a_id)
        self.assertEqual(
            [identity.id for identity in thread_a.communication_scope], [1, 2]
        )
        self.assertEqual(
            [message.subject for message in thread_a.messages],
            ["A 的当前草稿", "共享主题", "Re: 共享主题"],
        )
        shared_sent = next(
            message
            for message in thread_a.messages
            if message.direction == EmailDirection.SENT.value
        )
        self.assertEqual(shared_sent.content_html, "<p>HTML 副本</p>")
        self.assertEqual(shared_sent.rfc_message_id, "shared-message@example.com")
        self.assertEqual(shared_sent.reply_headers, {"references": []})
        self.assertEqual(
            [identity.id for identity in shared_sent.source_identities],
            [1, 2],
        )

        self.assertEqual(thread_b.current_task.id, task_b_id)
        self.assertEqual(
            [identity.id for identity in thread_b.communication_scope], [2, 1]
        )
        self.assertEqual(
            [message.subject for message in thread_b.messages],
            ["B 的当前草稿", "共享主题", "Re: 共享主题"],
        )
        self.assertEqual(
            [message.subject for message in thread_c.messages],
            ["C 的独立记录"],
        )
        self.assertIsNone(history_only.current_task.id)
        self.assertEqual(
            [message.subject for message in history_only.messages],
            ["B 收到的历史"],
        )

    def test_home_and_dashboard_use_shared_communication_but_current_identity_tasks(
        self,
    ) -> None:
        async def scenario():
            async with self.session_factory() as session:
                now = datetime.now(UTC)
                group = IdentityCommunicationGroup()
                identity_a = self._identity("身份 A", "stats-a@example.com")
                identity_b = self._identity("身份 B", "stats-b@example.com")
                identity_c = self._identity("身份 C", "stats-c@example.com")
                llm_profile = self._llm_profile()
                professor_x = Professor(
                    name="导师 X",
                    email="stats-x@example.edu",
                    research_direction="自然语言处理",
                )
                professor_y = Professor(
                    name="导师 Y",
                    email="stats-y@example.edu",
                    research_direction="机器学习",
                )
                session.add_all(
                    [
                        group,
                        identity_a,
                        identity_b,
                        identity_c,
                        llm_profile,
                        professor_x,
                        professor_y,
                    ],
                )
                await session.flush()
                identity_a.communication_group_id = group.id
                identity_b.communication_group_id = group.id

                review_task_a = EmailTask(
                    identity_id=identity_a.id,
                    llm_profile_id=llm_profile.id,
                    professor_id=professor_x.id,
                    status=EmailTaskStatus.REVIEW_REQUIRED.value,
                    match_score=95,
                    created_at=now - timedelta(days=3),
                    updated_at=now - timedelta(days=3),
                )
                failed_task_a = EmailTask(
                    identity_id=identity_a.id,
                    llm_profile_id=llm_profile.id,
                    professor_id=professor_y.id,
                    status=EmailTaskStatus.SEND_FAILED.value,
                    match_score=90,
                    created_at=now - timedelta(days=2),
                    updated_at=now - timedelta(days=2),
                )
                scheduled_task_b = EmailTask(
                    identity_id=identity_b.id,
                    llm_profile_id=llm_profile.id,
                    professor_id=professor_x.id,
                    status=EmailTaskStatus.SCHEDULED.value,
                    match_score=99,
                    created_at=now - timedelta(days=1),
                    updated_at=now - timedelta(days=1),
                )
                session.add_all([review_task_a, failed_task_a, scheduled_task_b])
                await session.flush()

                session.add_all(
                    [
                        EmailLog(
                            email_task_id=review_task_a.id,
                            identity_id=identity_a.id,
                            llm_profile_id=llm_profile.id,
                            professor_id=professor_x.id,
                            direction=EmailDirection.SENT.value,
                            subject="共享发送",
                            content="A 副本",
                            normalized_message_id="stats-shared@example.com",
                            created_at=now - timedelta(hours=5),
                        ),
                        EmailLog(
                            email_task_id=scheduled_task_b.id,
                            identity_id=identity_b.id,
                            llm_profile_id=llm_profile.id,
                            professor_id=professor_x.id,
                            direction=EmailDirection.SENT.value,
                            subject="共享发送",
                            content="B 副本",
                            rfc_message_id="<stats-shared@example.com>",
                            created_at=now - timedelta(hours=4),
                        ),
                        EmailLog(
                            email_task_id=scheduled_task_b.id,
                            identity_id=identity_b.id,
                            llm_profile_id=llm_profile.id,
                            professor_id=professor_x.id,
                            direction=EmailDirection.RECEIVED.value,
                            subject="Re: 共享发送",
                            content="收到",
                            normalized_message_id="stats-reply@example.com",
                            created_at=now - timedelta(hours=3),
                        ),
                        EmailLog(
                            email_task_id=None,
                            identity_id=identity_b.id,
                            llm_profile_id=llm_profile.id,
                            professor_id=professor_y.id,
                            direction=EmailDirection.SENT.value,
                            subject="B 的独立发送",
                            content="共享统计但不是 A 的任务",
                            normalized_message_id="stats-b-only@example.com",
                            created_at=now - timedelta(hours=2),
                        ),
                        EmailLog(
                            email_task_id=None,
                            identity_id=identity_c.id,
                            llm_profile_id=llm_profile.id,
                            professor_id=professor_x.id,
                            direction=EmailDirection.SENT.value,
                            subject="C 的发送",
                            content="不得统计",
                            normalized_message_id="stats-c-only@example.com",
                            created_at=now - timedelta(hours=1),
                        ),
                    ],
                )
                await session.commit()

                statuses = await build_contact_status_by_professor(
                    session,
                    identity_id=identity_a.id,
                    communication_identity_ids=(identity_a.id, identity_b.id),
                    professor_ids=[professor_x.id, professor_y.id],
                )
                dashboard = await build_dashboard_overview(
                    session,
                    identity_id=identity_a.id,
                    llm_profile_id=llm_profile.id,
                )
                return statuses, dashboard, professor_x.id, professor_y.id

        statuses, dashboard, professor_x_id, professor_y_id = self._run_async(
            scenario()
        )

        self.assertEqual(statuses[professor_x_id].status, "replied")
        self.assertEqual(statuses[professor_x_id].sent_count, 1)
        self.assertEqual(statuses[professor_y_id].status, "contacted")
        self.assertEqual(statuses[professor_y_id].sent_count, 1)

        summary = dashboard.email.summary
        self.assertEqual(summary.sent_count, 2)
        self.assertEqual(summary.sent_professor_count, 2)
        self.assertEqual(summary.total_professor_count, 2)
        self.assertEqual(summary.sent_professor_rate, 1.0)
        self.assertEqual(summary.contacted_professor_count, 2)
        self.assertEqual(summary.replied_count, 1)
        self.assertEqual(summary.reply_rate, 0.5)
        self.assertEqual(summary.review_required_count, 1)
        self.assertEqual(summary.scheduled_count, 0)
        self.assertEqual(summary.send_failed_count, 1)
        self.assertEqual(summary.send_failed_rate, 0.5)
        self.assertEqual(dashboard.email.reply_wait.sample_count, 1)
        self.assertEqual(dashboard.email.reply_wait.median_hours, 2.0)
        self.assertEqual(dashboard.email.reply_wait.p75_hours, 2.0)
        self.assertEqual(
            dashboard.email.outreach_coverage.universities[0].sent_professor_count,
            2,
        )
        self.assertEqual(
            dashboard.email.outreach_coverage.universities[0].total_professor_count,
            2,
        )
        self.assertEqual(dashboard.mentor.summary.high_score_uncontacted_count, 0)
        self.assertEqual(
            [item.professor_id for item in dashboard.email.follow_ups],
            [professor_y_id],
        )
        status_distribution = {
            item.status: item.count for item in dashboard.email.status_distribution
        }
        self.assertEqual(status_distribution[EmailTaskStatus.REVIEW_REQUIRED.value], 1)
        self.assertEqual(status_distribution[EmailTaskStatus.SEND_FAILED.value], 1)
        self.assertEqual(status_distribution[EmailTaskStatus.SCHEDULED.value], 0)

    def test_manual_refresh_syncs_group_members_and_reports_partial_failure(
        self,
    ) -> None:
        async def scenario():
            async with self.session_factory() as session:
                group = IdentityCommunicationGroup()
                identity_a = self._identity(
                    "身份 A", "refresh-a@example.com", with_imap=True
                )
                identity_b = self._identity(
                    "身份 B", "refresh-b@example.com", with_imap=True
                )
                identity_c = self._identity(
                    "身份 C", "refresh-c@example.com", with_imap=True
                )
                session.add_all([group, identity_a, identity_b, identity_c])
                await session.flush()
                identity_a.communication_group_id = group.id
                identity_b.communication_group_id = group.id
                await session.commit()

                calls: list[int] = []

                async def fake_sync(
                    _session_factory, identity_id: int, professor_id: int
                ):
                    self.assertEqual(professor_id, 88)
                    calls.append(identity_id)
                    if identity_id == identity_b.id:
                        raise RuntimeError("B 邮箱同步失败")
                    return None

                async def fake_build_thread(*_args, **kwargs):
                    return kwargs["sync_warnings"]

                with (
                    patch(
                        "app.modules.workspace.api.sync_workspace_professor_replies",
                        side_effect=fake_sync,
                    ),
                    patch(
                        "app.modules.workspace.api.get_session_factory",
                        return_value=object(),
                    ),
                    patch(
                        "app.modules.workspace.api.build_workspace_thread",
                        side_effect=fake_build_thread,
                    ),
                ):
                    warnings = await refresh_workspace_replies(
                        88,
                        identity_id=identity_a.id,
                        llm_profile_id=99,
                        session=session,
                    )
                return calls, warnings, identity_a.id, identity_b.id, identity_c.id

        calls, warnings, identity_a_id, identity_b_id, identity_c_id = self._run_async(
            scenario(),
        )

        self.assertEqual(sorted(calls), [identity_a_id, identity_b_id])
        self.assertNotIn(identity_c_id, calls)
        self.assertEqual(len(warnings), 1)
        self.assertEqual(warnings[0].identity_id, identity_b_id)
        self.assertIn("同步失败", warnings[0].message)

    def test_shared_status_scales_without_identity_or_professor_n_plus_one_queries(
        self,
    ) -> None:
        async def scenario():
            async with self.session_factory() as session:
                group = IdentityCommunicationGroup()
                identities = [
                    self._identity(
                        f"规模身份 {index}",
                        f"scale-{index}@example.com",
                    )
                    for index in range(10)
                ]
                session.add_all([group, *identities])
                await session.flush()
                for identity in identities:
                    identity.communication_group_id = group.id

                await session.execute(
                    insert(Professor),
                    [
                        {
                            "name": f"规模导师 {index}",
                            "email": f"scale-professor-{index}@example.edu",
                        }
                        for index in range(1000)
                    ],
                )
                professor_ids = list(
                    await session.scalars(
                        Professor.__table__.select()
                        .with_only_columns(Professor.id)
                        .order_by(Professor.id.asc()),
                    ),
                )
                await session.execute(
                    insert(EmailLog),
                    [
                        {
                            "identity_id": identity.id,
                            "professor_id": professor_id,
                            "direction": EmailDirection.SENT.value,
                            "subject": "规模测试",
                            "content": "同一封物理邮件的跨身份副本",
                            "normalized_message_id": f"scale-{professor_id}@example.com",
                        }
                        for professor_id in professor_ids
                        for identity in identities
                    ],
                )
                await session.commit()

                statements: list[str] = []

                def capture_statement(
                    _connection,
                    _cursor,
                    statement,
                    _parameters,
                    _context,
                    _executemany,
                ) -> None:
                    statements.append(statement)

                event.listen(
                    self.engine.sync_engine,
                    "before_cursor_execute",
                    capture_statement,
                )
                try:
                    statuses = await build_contact_status_by_professor(
                        session,
                        identity_id=identities[0].id,
                        communication_identity_ids=tuple(
                            identity.id for identity in identities
                        ),
                        professor_ids=professor_ids,
                        tasks_by_professor={},
                    )
                finally:
                    event.remove(
                        self.engine.sync_engine,
                        "before_cursor_execute",
                        capture_statement,
                    )

                email_log_queries = [
                    statement
                    for statement in statements
                    if "FROM email_logs" in statement
                ]
                return statuses, email_log_queries

        statuses, email_log_queries = self._run_async(scenario())

        self.assertEqual(len(statuses), 1000)
        self.assertTrue(all(status.sent_count == 1 for status in statuses.values()))
        self.assertEqual(
            len(email_log_queries),
            ceil(len(statuses) / DEFAULT_SQL_IN_CHUNK_SIZE),
        )

    @staticmethod
    def _identity(
        profile_name: str,
        email_address: str,
        *,
        with_imap: bool = False,
    ) -> IdentityProfile:
        return IdentityProfile(
            name=profile_name,
            profile_name=profile_name,
            sender_name=profile_name,
            email_address=email_address,
            smtp_host="smtp.example.com",
            smtp_port=465,
            smtp_username=email_address,
            smtp_password="secret",
            imap_host="imap.example.com" if with_imap else None,
            imap_port=993 if with_imap else None,
            imap_username=email_address if with_imap else None,
            imap_password="secret" if with_imap else None,
            default_language="zh-CN",
            outreach_generation_mode="template",
        )

    @staticmethod
    def _llm_profile() -> LLMProfile:
        return LLMProfile(
            name="共享测试模型",
            provider="openai",
            api_key="test-key",
            model_name="gpt-test",
        )


if __name__ == "__main__":
    unittest.main()
