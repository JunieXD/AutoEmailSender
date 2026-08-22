from __future__ import annotations

import asyncio
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models import (
    EmailDirection,
    EmailLog,
    EmailTask,
    EmailTaskStatus,
    IdentityProfile,
    LLMProfile,
    Professor,
)
from app.modules.workspace.thread import build_workspace_thread_for_task
from test.schema_database import create_schema_sqlite_database


class BatchTaskThreadPayloadTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "batch_thread_payload.db"
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
        asyncio.run(self.engine.dispose())
        self.temp_dir.cleanup()

    def test_batch_thread_payload_omits_communication_events_but_keeps_drafts(
        self,
    ) -> None:
        async def scenario():
            async with self.session_factory() as session:
                now = datetime.now(UTC)
                identity = IdentityProfile(
                    name="测试身份",
                    profile_name="测试身份",
                    sender_name="测试身份",
                    email_address="sender@example.com",
                    smtp_host="smtp.example.com",
                    smtp_port=465,
                    smtp_username="sender@example.com",
                    smtp_password="secret",
                    default_language="zh-CN",
                    outreach_generation_mode="template",
                )
                llm_profile = LLMProfile(
                    name="测试模型",
                    provider="openai",
                    api_key="test-key",
                    model_name="gpt-test",
                )
                professor = Professor(
                    name="往来导师", email="history@example.edu"
                )
                session.add_all([identity, llm_profile, professor])
                await session.flush()

                task = EmailTask(
                    identity_id=identity.id,
                    llm_profile_id=llm_profile.id,
                    professor_id=professor.id,
                    status=EmailTaskStatus.REVIEW_REQUIRED.value,
                    created_at=now - timedelta(days=1),
                    updated_at=now - timedelta(days=1),
                )
                session.add(task)
                await session.flush()

                session.add_all(
                    [
                        EmailLog(
                            email_task_id=task.id,
                            identity_id=identity.id,
                            llm_profile_id=llm_profile.id,
                            professor_id=professor.id,
                            direction=EmailDirection.DRAFT.value,
                            subject="当前草稿",
                            content="草稿正文",
                            created_at=now - timedelta(hours=3),
                        ),
                        EmailLog(
                            email_task_id=task.id,
                            identity_id=identity.id,
                            llm_profile_id=llm_profile.id,
                            professor_id=professor.id,
                            direction=EmailDirection.SENT.value,
                            subject="已发送主题",
                            content="已发送正文" * 1000,
                            normalized_message_id="<sent@example.com>",
                            created_at=now - timedelta(hours=2),
                        ),
                        EmailLog(
                            email_task_id=None,
                            identity_id=identity.id,
                            llm_profile_id=llm_profile.id,
                            professor_id=professor.id,
                            direction=EmailDirection.RECEIVED.value,
                            subject="Re: 已发送主题",
                            content="收到的回复正文" * 1000,
                            normalized_message_id="<reply@example.com>",
                            created_at=now - timedelta(hours=1),
                        ),
                    ],
                )
                await session.commit()

                full_thread = await build_workspace_thread_for_task(
                    session,
                    task_id=task.id,
                )
                trimmed_thread = await build_workspace_thread_for_task(
                    session,
                    task_id=task.id,
                    include_communication_events=False,
                )
                return full_thread, trimmed_thread, task.id

        full_thread, trimmed_thread, task_id = asyncio.run(scenario())

        full_directions = {
            message.direction for message in full_thread.messages
        }
        self.assertIn("draft", full_directions)
        self.assertIn("sent", full_directions)
        self.assertIn("received", full_directions)

        trimmed_directions = {
            message.direction for message in trimmed_thread.messages
        }
        self.assertEqual(trimmed_directions, {"draft"})
        for message in trimmed_thread.messages:
            self.assertNotIn("已发送正文", message.content)
            self.assertNotIn("收到的回复正文", message.content)

        self.assertEqual(trimmed_thread.current_task.id, task_id)
        self.assertIsNotNone(trimmed_thread.professor.id)
        self.assertIsNotNone(trimmed_thread.identity.id)
        self.assertIsNotNone(trimmed_thread.llm_profile.id)
        self.assertIsInstance(trimmed_thread.material_options, list)


if __name__ == "__main__":
    unittest.main()
