from __future__ import annotations

import asyncio
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.api.workspace_support import build_workspace_thread, _build_workspace_draft, _serialize_workspace_message
from app.models import (
    EmailDirection,
    EmailLog,
    EmailTask,
    EmailTaskSource,
    EmailTaskStatus,
    IdentityProfile,
    LLMProfile,
    Professor,
)
from app.services.outreach_templates import RenderedOutreachTemplate
from test.schema_database import create_schema_sqlite_database


class WorkspaceSupportTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "workspace_support_test.db"
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

    def test_received_workspace_message_strips_quoted_original_message(self) -> None:
        message = _serialize_workspace_message(
            EmailLog(
                id=1,
                email_task_id=1,
                identity_id=1,
                llm_profile_id=1,
                professor_id=1,
                direction=EmailDirection.RECEIVED.value,
                subject="回复：[推免自荐] 王俊杰",
                content="欢迎报考\n---- 回复的原邮件 ----\n尊敬的老师：原邮件正文",
                content_html="<p>欢迎报考</p><div>---- 回复的原邮件 ----</div><p>原邮件正文</p>",
                created_at=datetime.now(UTC),
            ),
        )

        self.assertEqual(message.content, "欢迎报考")
        self.assertEqual(message.content_html, "<p>欢迎报考</p>")

    def test_workspace_thread_includes_unbound_sent_and_received_logs(self) -> None:
        async def scenario() -> list[tuple[str, str | None]]:
            async with self.session_factory() as session:
                now = datetime.now(UTC)
                identity = self._identity("workspace-sender@example.com")
                llm_profile = self._llm_profile("workspace-llm")
                professor = Professor(name="张老师", email="zhang@example.edu")
                session.add_all([identity, llm_profile, professor])
                await session.flush()
                session.add(
                    EmailTask(
                        identity_id=identity.id,
                        llm_profile_id=llm_profile.id,
                        professor_id=professor.id,
                        status=EmailTaskStatus.MATCHED.value,
                        created_at=now - timedelta(days=2),
                        updated_at=now - timedelta(days=2),
                    ),
                )
                session.add_all(
                    [
                        EmailLog(
                            email_task_id=None,
                            identity_id=identity.id,
                            llm_profile_id=llm_profile.id,
                            professor_id=professor.id,
                            direction=EmailDirection.SENT.value,
                            subject="系统外发送",
                            content="老师您好",
                            created_at=now - timedelta(hours=2),
                        ),
                        EmailLog(
                            email_task_id=None,
                            identity_id=identity.id,
                            llm_profile_id=llm_profile.id,
                            professor_id=professor.id,
                            direction=EmailDirection.RECEIVED.value,
                            subject="Re: 系统外发送",
                            content="欢迎交流",
                            created_at=now - timedelta(hours=1),
                        ),
                    ],
                )
                await session.commit()

                thread = await build_workspace_thread(
                    session,
                    professor_id=professor.id,
                    identity_id=identity.id,
                    llm_profile_id=llm_profile.id,
                )
                return [(message.direction, message.subject) for message in thread.messages]

        self.assertEqual(
            self._run_async(scenario()),
            [
                (EmailDirection.SENT.value, "系统外发送"),
                (EmailDirection.RECEIVED.value, "Re: 系统外发送"),
            ],
        )

    def test_workspace_thread_filters_messages_by_identity(self) -> None:
        async def scenario() -> list[str | None]:
            async with self.session_factory() as session:
                now = datetime.now(UTC)
                identity = self._identity("workspace-current@example.com")
                other_identity = self._identity("workspace-other@example.com")
                llm_profile = self._llm_profile("workspace-filter-llm")
                professor = Professor(name="李老师", email="li@example.edu")
                session.add_all([identity, other_identity, llm_profile, professor])
                await session.flush()
                session.add(
                    EmailTask(
                        identity_id=identity.id,
                        llm_profile_id=llm_profile.id,
                        professor_id=professor.id,
                        status=EmailTaskStatus.MATCHED.value,
                    ),
                )
                session.add_all(
                    [
                        EmailLog(
                            email_task_id=None,
                            identity_id=other_identity.id,
                            llm_profile_id=llm_profile.id,
                            professor_id=professor.id,
                            direction=EmailDirection.SENT.value,
                            subject="其他身份发送",
                            content="不应显示",
                            created_at=now - timedelta(hours=2),
                        ),
                        EmailLog(
                            email_task_id=None,
                            identity_id=identity.id,
                            llm_profile_id=llm_profile.id,
                            professor_id=professor.id,
                            direction=EmailDirection.RECEIVED.value,
                            subject="当前身份收到",
                            content="应显示",
                            created_at=now - timedelta(hours=1),
                        ),
                    ],
                )
                await session.commit()

                thread = await build_workspace_thread(
                    session,
                    professor_id=professor.id,
                    identity_id=identity.id,
                    llm_profile_id=llm_profile.id,
                )
                return [message.subject for message in thread.messages]

        self.assertEqual(self._run_async(scenario()), ["当前身份收到"])

    def test_workspace_thread_excludes_drafts_not_belonging_to_current_task(self) -> None:
        async def scenario() -> list[tuple[str, str | None]]:
            async with self.session_factory() as session:
                now = datetime.now(UTC)
                identity = self._identity("workspace-draft@example.com")
                llm_profile = self._llm_profile("workspace-draft-llm")
                professor = Professor(name="王老师", email="wang@example.edu")
                session.add_all([identity, llm_profile, professor])
                await session.flush()
                old_task = EmailTask(
                    source=EmailTaskSource.BATCH.value,
                    identity_id=identity.id,
                    llm_profile_id=llm_profile.id,
                    professor_id=professor.id,
                    status=EmailTaskStatus.MATCHED.value,
                    created_at=now - timedelta(days=2),
                    updated_at=now - timedelta(days=2),
                )
                current_task = EmailTask(
                    identity_id=identity.id,
                    llm_profile_id=llm_profile.id,
                    professor_id=professor.id,
                    status=EmailTaskStatus.REVIEW_REQUIRED.value,
                    created_at=now - timedelta(days=1),
                    updated_at=now - timedelta(days=1),
                )
                session.add_all([old_task, current_task])
                await session.flush()
                session.add_all(
                    [
                        EmailLog(
                            email_task_id=old_task.id,
                            identity_id=identity.id,
                            llm_profile_id=llm_profile.id,
                            professor_id=professor.id,
                            direction=EmailDirection.DRAFT.value,
                            subject="旧任务草稿",
                            content="不应显示",
                            created_at=now - timedelta(hours=4),
                        ),
                        EmailLog(
                            email_task_id=current_task.id,
                            identity_id=identity.id,
                            llm_profile_id=llm_profile.id,
                            professor_id=professor.id,
                            direction=EmailDirection.DRAFT.value,
                            subject="当前任务草稿",
                            content="应显示",
                            created_at=now - timedelta(hours=3),
                        ),
                        EmailLog(
                            email_task_id=old_task.id,
                            identity_id=identity.id,
                            llm_profile_id=llm_profile.id,
                            professor_id=professor.id,
                            direction=EmailDirection.SENT.value,
                            subject="旧任务已发送",
                            content="通信记录仍显示",
                            created_at=now - timedelta(hours=2),
                        ),
                        EmailLog(
                            email_task_id=None,
                            identity_id=identity.id,
                            llm_profile_id=llm_profile.id,
                            professor_id=professor.id,
                            direction=EmailDirection.RECEIVED.value,
                            subject="无任务回复",
                            content="通信记录仍显示",
                            created_at=now - timedelta(hours=1),
                        ),
                    ],
                )
                await session.commit()

                thread = await build_workspace_thread(
                    session,
                    professor_id=professor.id,
                    identity_id=identity.id,
                    llm_profile_id=llm_profile.id,
                )
                return [(message.direction, message.subject) for message in thread.messages]

        self.assertEqual(
            self._run_async(scenario()),
            [
                (EmailDirection.DRAFT.value, "当前任务草稿"),
                (EmailDirection.SENT.value, "旧任务已发送"),
                (EmailDirection.RECEIVED.value, "无任务回复"),
            ],
        )

    def test_sent_workspace_message_keeps_approved_body_unchanged(self) -> None:
        message = _serialize_workspace_message(
            EmailLog(
                id=1,
                email_task_id=1,
                identity_id=1,
                llm_profile_id=1,
                professor_id=1,
                direction=EmailDirection.SENT.value,
                subject="[推免自荐] 王俊杰",
                content="正文\n---- 回复的原邮件 ----\n这里是用户写入的内容",
                content_html=None,
                created_at=datetime.now(UTC),
            ),
        )

        self.assertIn("回复的原邮件", message.content)

    def test_workspace_draft_uses_rendered_template_without_history(self) -> None:
        draft = _build_workspace_draft(
            task=self._task(),
            rendered_template=self._rendered_template(
                subject="申请加入张老师课题组",
                body_text="老师您好，我是王同学。",
                body_html="<p>老师您好，我是王同学。</p>",
            ),
        )

        self.assertEqual(draft.source, "template")
        self.assertEqual(draft.subject, "申请加入张老师课题组")
        self.assertIn("老师您好", draft.body_text)
        self.assertTrue(draft.sendable)
        self.assertTrue(draft.editable)

    def test_workspace_draft_uses_saved_draft_before_generated_result(self) -> None:
        draft = _build_workspace_draft(
            task=self._task(
                generated_subject="AI 主题",
                generated_content_text="AI 正文",
                generated_content_html="<p>AI 正文</p>",
                approved_subject="保存主题",
                approved_body_text="保存正文",
                approved_body_html="<p>保存正文</p>",
            ),
            rendered_template=self._rendered_template(),
        )

        self.assertEqual(draft.source, "saved")
        self.assertEqual(draft.subject, "保存主题")
        self.assertEqual(draft.body_text, "保存正文")
        self.assertEqual(draft.body_html, "<p>保存正文</p>")

    def test_workspace_draft_uses_saved_empty_body_before_generated_result(self) -> None:
        draft = _build_workspace_draft(
            task=self._task(
                generated_subject="AI 主题",
                generated_content_text="AI 正文",
                generated_content_html="<p>AI 正文</p>",
                approved_subject="只保留主题",
                approved_body_text="",
                approved_body_html="",
            ),
            rendered_template=self._rendered_template(),
        )

        self.assertEqual(draft.source, "saved")
        self.assertEqual(draft.subject, "只保留主题")
        self.assertEqual(draft.body_text, "")
        self.assertEqual(draft.body_html, "")
        self.assertFalse(draft.sendable)

    def test_workspace_draft_uses_ai_rewrite_when_no_saved_draft(self) -> None:
        draft = _build_workspace_draft(
            task=self._task(
                generated_subject="AI 主题",
                generated_content_text="AI 正文",
                generated_content_html="<p>AI 正文</p>",
            ),
            rendered_template=self._rendered_template(),
        )

        self.assertEqual(draft.source, "ai_rewrite")
        self.assertEqual(draft.subject, "AI 主题")
        self.assertEqual(draft.body_text, "AI 正文")

    def test_workspace_draft_uses_rewrite_source_while_generating(self) -> None:
        draft = _build_workspace_draft(
            task=self._task(
                status=EmailTaskStatus.GENERATING_DRAFT.value,
                draft_rewrite_source_subject="源主题",
                draft_rewrite_source_body_text="源正文",
                draft_rewrite_source_body_html="<p>源正文</p>",
                approved_body_text="保存正文",
            ),
            rendered_template=self._rendered_template(),
        )

        self.assertEqual(draft.source, "rewrite_source")
        self.assertEqual(draft.subject, "源主题")
        self.assertEqual(draft.body_text, "源正文")
        self.assertFalse(draft.editable)
        self.assertFalse(draft.sendable)

    def test_workspace_draft_is_empty_without_template_or_history(self) -> None:
        draft = _build_workspace_draft(
            task=self._task(),
            rendered_template=None,
        )

        self.assertEqual(draft.source, "manual_empty")
        self.assertIsNone(draft.subject)
        self.assertEqual(draft.body_text, "")
        self.assertIsNone(draft.body_html)
        self.assertFalse(draft.sendable)
        self.assertTrue(draft.editable)

    def _task(self, **overrides):
        values = {
            "status": EmailTaskStatus.MATCHED.value,
            "sent_at": None,
            "is_replied": False,
            "cancellation_reason": None,
            "can_continue_manually": False,
            "can_write_follow_up": False,
            "approved_subject": None,
            "approved_body_text": None,
            "approved_body_html": None,
            "generated_subject": None,
            "generated_content_text": None,
            "generated_content_html": None,
            "draft_rewrite_source_subject": None,
            "draft_rewrite_source_body_text": None,
            "draft_rewrite_source_body_html": None,
        }
        values.update(overrides)
        return SimpleNamespace(**values)

    def _rendered_template(
        self,
        *,
        subject: str = "模板主题",
        body_text: str = "模板正文",
        body_html: str = "<p>模板正文</p>",
    ) -> RenderedOutreachTemplate:
        return RenderedOutreachTemplate(
            subject=subject,
            body_text=body_text,
            body_html=body_html,
            placeholders={},
        )

    @staticmethod
    def _identity(email_address: str) -> IdentityProfile:
        return IdentityProfile(
            name=email_address,
            profile_name=email_address,
            sender_name="王同学",
            email_address=email_address,
            smtp_host="smtp.example.com",
            smtp_port=465,
            smtp_username=email_address,
            smtp_password="secret",
            default_language="zh-CN",
            outreach_generation_mode="template",
        )

    @staticmethod
    def _llm_profile(name: str) -> LLMProfile:
        return LLMProfile(
            name=name,
            provider="openai",
            api_base_url="https://api.example.com/v1",
            api_key="sk-test-key",
            model_name="gpt-test",
        )


if __name__ == "__main__":
    unittest.main()
