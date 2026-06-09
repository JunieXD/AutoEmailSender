from __future__ import annotations

import unittest
from datetime import UTC, datetime
from types import SimpleNamespace

from app.api.workspace_support import _build_workspace_draft, _serialize_workspace_message
from app.models import EmailDirection, EmailLog, EmailTaskStatus
from app.services.outreach_templates import RenderedOutreachTemplate


class WorkspaceSupportTest(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
