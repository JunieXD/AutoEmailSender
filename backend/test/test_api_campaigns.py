from __future__ import annotations

import asyncio
import json
import sqlite3
from contextlib import closing
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, patch

from sqlalchemy import event, inspect

from app.core.migrations import get_alembic_config, get_head_revision

BACKEND_DIR = Path(__file__).resolve().parents[1]
HEAD_REVISION = get_head_revision(get_alembic_config())


from test.api_fixture import ApiFixture


class CampaignsApiTests(ApiFixture):
    def test_retired_identity_batch_task_cannot_be_resumed(self) -> None:
        identity_id = self._create_identity(
            with_imap=False,
            email_address="retired-batch-identity@example.com",
        )
        llm_id = self._create_llm(name="身份删除批量任务模型")
        professor_id = self._create_professor(
            email="retired-batch-professor@example.edu"
        )
        batch_task_id = self._insert_batch_task_with_material(
            identity_id=identity_id,
            llm_id=llm_id,
            status="paused",
            primary_material_id=None,
        )
        self._insert_email_task_with_material(
            identity_id=identity_id,
            llm_id=llm_id,
            professor_id=professor_id,
            status="discovered",
            primary_material_id=None,
            batch_task_id=batch_task_id,
            source="batch",
        )
        impact = self.client.get(
            f"/api/identities/{identity_id}/deletion-impact"
        ).json()
        retired = self.client.delete(
            f"/api/identities/{identity_id}",
            params={"impact_revision": impact["revision"]},
        )
        self.assertEqual(retired.status_code, 204, msg=retired.text)

        resumed = self.client.post(f"/api/batch-tasks/{batch_task_id}/resume")

        self.assertEqual(resumed.status_code, 409, msg=resumed.text)
        detail = resumed.json()["detail"]
        self.assertEqual(detail["code"], "CAMPAIGN_IDENTITY_RETIRED")
        self.assertEqual(detail["batch_task_id"], batch_task_id)
        self.assertEqual(detail["identity_id"], identity_id)
        with closing(sqlite3.connect(self.db_path)) as connection, connection:
            status_row = connection.execute(
                "SELECT status FROM batch_tasks WHERE id = ?",
                (batch_task_id,),
            ).fetchone()
        self.assertEqual(status_row, ("stopped",))

    def test_batch_task_keeps_selected_template_snapshot_after_library_edit(
        self,
    ) -> None:
        identity_id = self._create_identity(with_imap=False)
        llm_id = self._create_llm()
        professor_id = self._create_professor(email="template-snapshot@example.edu")
        template_response = self.client.post(
            "/api/outreach-templates",
            json={
                "name": "独立批量模板",
                "recommended_generation_mode": "template",
                "subject": "原始主题 {{name}}",
                "body_text": "原始正文 {{sender_name}}",
                "body_html": "<p>原始正文 {{sender_name}}</p>",
            },
        )
        self.assertEqual(template_response.status_code, 201, msg=template_response.text)
        template_id = template_response.json()["id"]

        batch_response = self.client.post(
            "/api/batch-tasks",
            json={
                "identity_id": identity_id,
                "llm_profile_id": llm_id,
                "name": "模板快照任务",
                "professor_ids": [professor_id],
                "schedule_type": "immediate",
                "selected_material_ids": [],
                "outreach_template_id": template_id,
                "outreach_generation_mode": "template",
            },
        )
        self.assertEqual(batch_response.status_code, 201, msg=batch_response.text)
        batch_payload = batch_response.json()
        self.assertEqual(batch_payload["outreach_template_id"], template_id)
        self.assertEqual(
            batch_payload["outreach_template_name_snapshot"],
            "独立批量模板",
        )
        self.assertEqual(batch_payload["outreach_template_snapshot_version"], 1)
        self.assertEqual(batch_payload["outreach_generation_mode"], "template")

        connection = sqlite3.connect(self.db_path)
        try:
            before_update = connection.execute(
                """
                SELECT
                    outreach_template_id,
                    outreach_template_subject,
                    outreach_template_body_text,
                    outreach_template_body_html
                FROM email_tasks
                WHERE batch_task_id = ?
                """,
                (batch_response.json()["id"],),
            ).fetchone()
            batch_before_update = connection.execute(
                """
                SELECT
                    outreach_template_id,
                    outreach_template_name_snapshot,
                    outreach_template_snapshot_version,
                    outreach_generation_mode,
                    outreach_template_subject,
                    outreach_template_body_text,
                    outreach_template_body_html
                FROM batch_tasks
                WHERE id = ?
                """,
                (batch_response.json()["id"],),
            ).fetchone()
        finally:
            connection.close()
        self.assertEqual(
            before_update,
            (
                template_id,
                "原始主题 {{name}}",
                "原始正文 {{sender_name}}",
                "<p>原始正文 {{sender_name}}</p>",
            ),
        )
        self.assertEqual(
            batch_before_update,
            (
                template_id,
                "独立批量模板",
                1,
                "template",
                "原始主题 {{name}}",
                "原始正文 {{sender_name}}",
                "<p>原始正文 {{sender_name}}</p>",
            ),
        )

        update_response = self.client.put(
            f"/api/outreach-templates/{template_id}",
            json={
                "subject": "修改后的主题",
                "body_text": "修改后的正文",
                "body_html": "<p>修改后的正文</p>",
            },
        )
        self.assertEqual(update_response.status_code, 200, msg=update_response.text)

        connection = sqlite3.connect(self.db_path)
        try:
            after_update = connection.execute(
                """
                SELECT
                    outreach_template_id,
                    outreach_template_subject,
                    outreach_template_body_text,
                    outreach_template_body_html
                FROM email_tasks
                WHERE batch_task_id = ?
                """,
                (batch_response.json()["id"],),
            ).fetchone()
            batch_after_update = connection.execute(
                """
                SELECT
                    outreach_template_id,
                    outreach_template_name_snapshot,
                    outreach_template_snapshot_version,
                    outreach_generation_mode,
                    outreach_template_subject,
                    outreach_template_body_text,
                    outreach_template_body_html
                FROM batch_tasks
                WHERE id = ?
                """,
                (batch_response.json()["id"],),
            ).fetchone()
        finally:
            connection.close()
        self.assertEqual(after_update, before_update)
        self.assertEqual(batch_after_update, batch_before_update)

    def test_batch_resend_uses_batch_snapshot_after_item_template_change(self) -> None:
        identity_id = self._create_identity(with_imap=False)
        llm_id = self._create_llm()
        professor_id = self._create_professor(email="batch-snapshot-resend@example.edu")

        original_template_response = self.client.post(
            "/api/outreach-templates",
            json={
                "name": "原批次模板",
                "recommended_generation_mode": "template",
                "subject": "模板库原主题 {{name}}",
                "body_text": "模板库原正文 {{sender_name}}",
                "body_html": "<p>模板库原正文 {{sender_name}}</p>",
            },
        )
        self.assertEqual(
            original_template_response.status_code,
            201,
            msg=original_template_response.text,
        )
        original_template_id = original_template_response.json()["id"]

        replacement_template_response = self.client.post(
            "/api/outreach-templates",
            json={
                "name": "单封后来改用的模板",
                "recommended_generation_mode": "template",
                "subject": "单封新主题 {{name}}",
                "body_text": "单封新正文 {{sender_name}}",
                "body_html": "<p>单封新正文 {{sender_name}}</p>",
            },
        )
        self.assertEqual(
            replacement_template_response.status_code,
            201,
            msg=replacement_template_response.text,
        )
        replacement_template_id = replacement_template_response.json()["id"]

        batch_response = self.client.post(
            "/api/batch-tasks",
            json={
                "identity_id": identity_id,
                "llm_profile_id": llm_id,
                "name": "需要保持原快照的批次",
                "professor_ids": [professor_id],
                "schedule_type": "immediate",
                "selected_material_ids": [],
                "outreach_template_id": original_template_id,
                "outreach_generation_mode": "template",
                "outreach_template_subject": "创建页最终主题 {{name}}",
                "outreach_template_body_text": "创建页最终正文 {{sender_name}}",
                "outreach_template_body_html": "<p>创建页最终正文 {{sender_name}}</p>",
            },
        )
        self.assertEqual(batch_response.status_code, 201, msg=batch_response.text)
        batch_task_id = batch_response.json()["id"]
        batch_items = self.client.get(f"/api/batch-tasks/{batch_task_id}/items")
        self.assertEqual(batch_items.status_code, 200, msg=batch_items.text)
        email_task_id = batch_items.json()[0]["id"]

        switched_response = self.client.post(
            f"/api/email-tasks/{email_task_id}/outreach-config",
            json={
                "outreach_generation_mode": "template",
                "outreach_template_id": replacement_template_id,
                "outreach_template_subject": "单封新主题 {{name}}",
                "outreach_template_body_text": "单封新正文 {{sender_name}}",
                "outreach_template_body_html": "<p>单封新正文 {{sender_name}}</p>",
            },
        )
        self.assertEqual(switched_response.status_code, 200, msg=switched_response.text)

        resend_context_response = self.client.get(
            f"/api/batch-tasks/{batch_task_id}/resend-context",
        )
        self.assertEqual(
            resend_context_response.status_code,
            200,
            msg=resend_context_response.text,
        )
        defaults = resend_context_response.json()["defaults"]
        self.assertEqual(defaults["outreach_template_id"], original_template_id)
        self.assertEqual(
            defaults["outreach_template_name_snapshot"],
            "原批次模板",
        )
        self.assertEqual(defaults["outreach_generation_mode"], "template")
        self.assertEqual(
            defaults["outreach_template_subject"],
            "创建页最终主题 {{name}}",
        )
        self.assertEqual(
            defaults["outreach_template_body_text"],
            "创建页最终正文 {{sender_name}}",
        )
        self.assertEqual(
            defaults["outreach_template_body_html"],
            "<p>创建页最终正文 {{sender_name}}</p>",
        )

    def test_batch_resend_keeps_archived_template_provenance_with_explicit_snapshot(
        self,
    ) -> None:
        identity_id = self._create_identity(with_imap=False)
        llm_id = self._create_llm()
        professor_id = self._create_professor(
            email="archived-resend-template@example.edu"
        )
        template_response = self.client.post(
            "/api/outreach-templates",
            json={
                "name": "之后会归档的来源模板",
                "recommended_generation_mode": "template",
                "subject": "原任务主题 {{name}}",
                "body_text": "原任务正文 {{sender_name}}",
                "body_html": "<p>原任务正文 {{sender_name}}</p>",
            },
        )
        self.assertEqual(template_response.status_code, 201, msg=template_response.text)
        template_id = template_response.json()["id"]
        archive_response = self.client.delete(f"/api/outreach-templates/{template_id}")
        self.assertEqual(archive_response.status_code, 200, msg=archive_response.text)

        rejected = self.client.post(
            "/api/batch-tasks",
            json={
                "identity_id": identity_id,
                "llm_profile_id": llm_id,
                "name": "不能重新选归档模板",
                "professor_ids": [professor_id],
                "schedule_type": "immediate",
                "outreach_template_id": template_id,
                "outreach_generation_mode": "template",
            },
        )
        self.assertEqual(rejected.status_code, 400, msg=rejected.text)

        resend = self.client.post(
            "/api/batch-tasks",
            json={
                "identity_id": identity_id,
                "llm_profile_id": llm_id,
                "name": "保留归档来源的重发任务",
                "professor_ids": [professor_id],
                "schedule_type": "immediate",
                "selected_material_ids": [],
                "outreach_template_id": template_id,
                "outreach_generation_mode": "template",
                "outreach_template_subject": "原任务主题 {{name}}",
                "outreach_template_body_text": "原任务正文 {{sender_name}}",
                "outreach_template_body_html": "<p>原任务正文 {{sender_name}}</p>",
            },
        )
        self.assertEqual(resend.status_code, 201, msg=resend.text)

        connection = sqlite3.connect(self.db_path)
        try:
            task_row = connection.execute(
                """
                SELECT outreach_template_id, outreach_template_snapshot_version,
                       outreach_template_subject, outreach_template_body_text
                FROM email_tasks
                WHERE batch_task_id = ?
                """,
                (resend.json()["id"],),
            ).fetchone()
        finally:
            connection.close()
        self.assertEqual(
            task_row,
            (
                template_id,
                1,
                "原任务主题 {{name}}",
                "原任务正文 {{sender_name}}",
            ),
        )

    def test_workspace_ensure_task_creates_manual_task_after_expired_batch_send_failure(
        self,
    ) -> None:
        identity_id = self._create_identity(with_imap=False)
        llm_profile_id = self._create_llm()
        professor_id = self._create_professor(
            email="expired-batch-send-failed@example.edu"
        )

        created = self.client.post(
            "/api/batch-tasks",
            json={
                "identity_id": identity_id,
                "llm_profile_id": llm_profile_id,
                "name": "过期定时发送失败任务",
                "professor_ids": [professor_id],
                "schedule_type": "scheduled",
                "window_start_time": "09:00",
                "window_end_time": "10:00",
                "emails_per_window": 1,
                "scheduled_dates": [
                    (datetime.now(UTC) + timedelta(days=1)).date().isoformat()
                ],
                "primary_material_id": None,
                "email_subject": "Hello {{name}}",
                "email_body": "Body",
                "selected_material_ids": None,
                "outreach_generation_mode": "template",
                "outreach_template_subject": "Hello {{name}}",
                "outreach_template_body_text": "Body",
                "outreach_template_body_html": None,
            },
        )
        self.assertEqual(created.status_code, 201, msg=created.text)
        batch_task_id = created.json()["id"]
        batch_item_id = self.client.get(
            f"/api/batch-tasks/{batch_task_id}/items"
        ).json()[0]["id"]

        connection = sqlite3.connect(self.db_path)
        try:
            connection.execute(
                """
                UPDATE batch_tasks
                SET status = 'expired',
                    scheduled_dates = ?,
                    window_end_time = '09:00',
                    updated_at = datetime('now')
                WHERE id = ?
                """,
                (json.dumps([datetime.now(UTC).date().isoformat()]), batch_task_id),
            )
            connection.execute(
                """
                UPDATE email_tasks
                SET status = 'send_failed',
                    last_error = 'SMTP 发信失败: flow over limit',
                    updated_at = datetime('now')
                WHERE id = ?
                """,
                (batch_item_id,),
            )
            connection.commit()
        finally:
            connection.close()

        workspace = self.client.post(
            f"/api/workspaces/{professor_id}/ensure-task",
            params={"identity_id": identity_id, "llm_profile_id": llm_profile_id},
        )

        self.assertEqual(workspace.status_code, 200, msg=workspace.text)
        current_task = workspace.json()["current_task"]
        self.assertNotEqual(current_task["id"], batch_item_id)
        self.assertEqual(current_task["source"], "manual")
        self.assertIsNone(current_task["parent_task_id"])
        self.assertIsNone(current_task["batch_task_id"])

    def test_workspace_ensure_task_creates_independent_manual_task_when_batch_task_exists(
        self,
    ) -> None:
        identity_id = self._create_identity(with_imap=False)
        llm_profile_id = self._create_llm()
        cases = [
            ("review_required", "待审核批次邮件"),
            ("approved", "已审核批次邮件"),
            ("scheduled", "已定时批次邮件"),
            ("sent", "已发送批次邮件"),
            ("send_failed", "发送失败批次邮件"),
        ]
        for task_status, subject in cases:
            with self.subTest(task_status=task_status):
                professor_id = self._create_professor(
                    email=f"{task_status.replace('_', '-')}-stopped-batch@example.edu",
                )
                batch_task_id = self._insert_batch_task_with_material(
                    identity_id=identity_id,
                    llm_id=llm_profile_id,
                    status="stopped",
                    primary_material_id=None,
                )
                batch_item_id = self._insert_email_task_with_material(
                    identity_id=identity_id,
                    llm_id=llm_profile_id,
                    professor_id=professor_id,
                    status=task_status,
                    primary_material_id=None,
                    batch_task_id=batch_task_id,
                    source="batch",
                    generated_subject=subject,
                    generated_content_text="批次正文",
                    generated_content_html="<p>批次正文</p>",
                )

                workspace = self.client.post(
                    f"/api/workspaces/{professor_id}/ensure-task",
                    params={
                        "identity_id": identity_id,
                        "llm_profile_id": llm_profile_id,
                    },
                )

                self.assertEqual(workspace.status_code, 200, msg=workspace.text)
                current_task = workspace.json()["current_task"]
                self.assertNotEqual(current_task["id"], batch_item_id)
                self.assertEqual(current_task["source"], "manual")
                self.assertIsNone(current_task["batch_task_id"])
                self.assertIsNone(current_task["parent_task_id"])
                self.assertIn(current_task["status"], {"discovered", "matched"})

                workspace_after_ensure = self.client.get(
                    f"/api/workspaces/{professor_id}",
                    params={
                        "identity_id": identity_id,
                        "llm_profile_id": llm_profile_id,
                    },
                )
                self.assertEqual(
                    workspace_after_ensure.status_code,
                    200,
                    msg=workspace_after_ensure.text,
                )
                self.assertEqual(
                    workspace_after_ensure.json()["current_task"]["id"],
                    current_task["id"],
                )

    def test_delete_material_clears_stopped_batch_task_material_reference(self) -> None:
        identity_id = self._create_identity(with_imap=False)
        llm_id = self._create_llm()
        deleted_material_id = self._upload_material(
            identity_id,
            filename="resume.txt",
            content=b"My research background is in information extraction.",
            material_type="resume",
        )
        remaining_material_id = self._upload_material(
            identity_id,
            filename="portfolio.pdf",
            content=b"Portfolio content",
            material_type="portfolio",
        )
        batch_task_id = self._insert_batch_task_with_material(
            identity_id=identity_id,
            llm_id=llm_id,
            status="stopped",
            primary_material_id=deleted_material_id,
            selected_material_ids=[deleted_material_id, remaining_material_id],
        )

        delete_response = self.client.delete(f"/api/materials/{deleted_material_id}")

        self.assertEqual(delete_response.status_code, 204, msg=delete_response.text)
        primary_material_id, selected_material_ids = (
            self._get_batch_task_material_references(batch_task_id)
        )
        self.assertIsNone(primary_material_id)
        self.assertEqual(selected_material_ids, [remaining_material_id])

    def test_delete_material_still_blocks_running_batch_task_reference(self) -> None:
        identity_id = self._create_identity(with_imap=False)
        llm_id = self._create_llm()
        material_id = self._upload_material(
            identity_id,
            filename="resume.txt",
            content=b"My research background is in information extraction.",
            material_type="resume",
        )
        batch_task_id = self._insert_batch_task_with_material(
            identity_id=identity_id,
            llm_id=llm_id,
            status="running",
            primary_material_id=material_id,
            selected_material_ids=[material_id],
        )

        delete_response = self.client.delete(f"/api/materials/{material_id}")

        self.assertEqual(delete_response.status_code, 409)
        detail = delete_response.json()["detail"]
        self.assertEqual(detail["code"], "MATERIAL_DELETION_BLOCKED")
        self.assertEqual(detail["details"]["blockers"][0]["id"], batch_task_id)
        primary_material_id, selected_material_ids = (
            self._get_batch_task_material_references(batch_task_id)
        )
        self.assertEqual(primary_material_id, material_id)
        self.assertEqual(selected_material_ids, [material_id])

    def test_delete_material_allows_completed_batch_task_with_stale_running_status(
        self,
    ) -> None:
        identity_id = self._create_identity(with_imap=False)
        llm_id = self._create_llm()
        material_id = self._upload_material(
            identity_id,
            filename="resume.txt",
            content=b"My research background is in information extraction.",
            material_type="resume",
        )
        remaining_material_id = self._upload_material(
            identity_id,
            filename="portfolio.pdf",
            content=b"Portfolio content",
            material_type="portfolio",
        )
        batch_task_id = self._insert_batch_task_with_material(
            identity_id=identity_id,
            llm_id=llm_id,
            status="running",
            primary_material_id=material_id,
            selected_material_ids=[material_id, remaining_material_id],
        )
        professor_id = self._create_professor(
            email="completed-batch-material-delete@example.edu"
        )
        self._insert_email_task_with_material(
            identity_id=identity_id,
            llm_id=llm_id,
            professor_id=professor_id,
            status="sent",
            primary_material_id=material_id,
            selected_material_ids=[material_id, remaining_material_id],
            batch_task_id=batch_task_id,
            source="batch",
        )

        delete_response = self.client.delete(f"/api/materials/{material_id}")

        self.assertEqual(delete_response.status_code, 204, msg=delete_response.text)
        self.assertEqual(self._get_batch_task_status(batch_task_id), "completed")
        primary_material_id, selected_material_ids = (
            self._get_batch_task_material_references(batch_task_id)
        )
        self.assertIsNone(primary_material_id)
        self.assertEqual(selected_material_ids, [remaining_material_id])

    def test_delete_material_detaches_soft_deleted_running_batch_task_reference(
        self,
    ) -> None:
        identity_id = self._create_identity(with_imap=False)
        llm_id = self._create_llm()
        deleted_material_id = self._upload_material(
            identity_id,
            filename="resume.txt",
            content=b"My research background is in information extraction.",
            material_type="resume",
        )
        remaining_material_id = self._upload_material(
            identity_id,
            filename="portfolio.pdf",
            content=b"Portfolio content",
            material_type="portfolio",
        )
        batch_task_id = self._insert_batch_task_with_material(
            identity_id=identity_id,
            llm_id=llm_id,
            status="running",
            primary_material_id=deleted_material_id,
            selected_material_ids=[deleted_material_id, remaining_material_id],
            deleted=True,
        )

        delete_response = self.client.delete(f"/api/materials/{deleted_material_id}")

        self.assertEqual(delete_response.status_code, 204, msg=delete_response.text)
        primary_material_id, selected_material_ids = (
            self._get_batch_task_material_references(batch_task_id)
        )
        self.assertIsNone(primary_material_id)
        self.assertEqual(selected_material_ids, [remaining_material_id])

        restore_response = self.client.post(f"/api/batch-tasks/{batch_task_id}/restore")
        self.assertEqual(restore_response.status_code, 200, msg=restore_response.text)
        restored_task = restore_response.json()["task"]
        self.assertEqual(restored_task["status"], "stopped")
        primary_material_id, selected_material_ids = (
            self._get_batch_task_material_references(batch_task_id)
        )
        self.assertIsNone(primary_material_id)
        self.assertEqual(selected_material_ids, [remaining_material_id])

    def test_delete_material_detaches_soft_deleted_stopped_batch_approved_item_reference(
        self,
    ) -> None:
        identity_id = self._create_identity(with_imap=False)
        llm_id = self._create_llm()
        deleted_material_id = self._upload_material(
            identity_id,
            filename="resume.txt",
            content=b"My research background is in information extraction.",
            material_type="resume",
        )
        remaining_material_id = self._upload_material(
            identity_id,
            filename="portfolio.pdf",
            content=b"Portfolio content",
            material_type="portfolio",
        )
        batch_task_id = self._insert_batch_task_with_material(
            identity_id=identity_id,
            llm_id=llm_id,
            status="stopped",
            primary_material_id=deleted_material_id,
            selected_material_ids=[deleted_material_id, remaining_material_id],
            deleted=True,
        )
        professor_id = self._create_professor(
            email="soft-deleted-approved-material-delete@example.edu"
        )
        email_task_id = self._insert_email_task_with_material(
            identity_id=identity_id,
            llm_id=llm_id,
            professor_id=professor_id,
            status="approved",
            primary_material_id=deleted_material_id,
            selected_material_ids=[deleted_material_id, remaining_material_id],
            batch_task_id=batch_task_id,
            source="batch",
            approved_subject="已批准旧主题",
            approved_body_text="已批准旧正文",
            approved_body_html="<p>已批准旧正文</p>",
        )

        delete_response = self.client.delete(f"/api/materials/{deleted_material_id}")

        self.assertEqual(delete_response.status_code, 204, msg=delete_response.text)
        task_state = self._get_email_task_delete_state(email_task_id)
        self.assertEqual(task_state["status"], "canceled")
        self.assertEqual(task_state["cancellation_reason"], "batch_stopped")
        self.assertIsNone(task_state["primary_material_id"])
        self.assertEqual(task_state["selected_material_ids"], [remaining_material_id])
        self.assertIsNone(task_state["approved_subject"])
        self.assertIsNone(task_state["approved_body_text"])
        self.assertIsNone(task_state["approved_body_html"])
        self.assertIsNone(task_state["approved_at"])
        primary_material_id, selected_material_ids = (
            self._get_batch_task_material_references(batch_task_id)
        )
        self.assertIsNone(primary_material_id)
        self.assertEqual(selected_material_ids, [remaining_material_id])

    def test_immediate_template_batch_task_queues_without_synchronous_send(
        self,
    ) -> None:
        identity_id = self._create_identity(with_imap=False)
        update_response = self.client.put(
            f"/api/identities/{identity_id}",
            json=self._build_identity_payload(
                with_imap=False,
                outreach_generation_mode="template",
                outreach_template_subject="申请与{{name}}老师交流",
                outreach_template_body_text="老师您好，我是{{sender_name}}。",
            ),
        )
        self.assertEqual(update_response.status_code, 200, msg=update_response.text)
        llm_id = self._create_llm()
        professor_id = self._create_professor(email="queued-template@example.edu")

        with patch(
            "app.modules.workspace.tasks.delivery.mail_runtime.send_email",
            AsyncMock(
                return_value=self._build_send_result(
                    message_id="<queued-template@example.com>",
                    provider_payload={},
                ),
            ),
        ) as mocked_send:
            response = self.client.post(
                "/api/batch-tasks",
                json={
                    "identity_id": identity_id,
                    "llm_profile_id": llm_id,
                    "name": "立即模板批量入队",
                    "professor_ids": [professor_id],
                    "schedule_type": "immediate",
                    "window_start_time": None,
                    "window_end_time": None,
                    "emails_per_window": None,
                    "primary_material_id": None,
                    "email_subject": "申请与{{name}}老师交流",
                    "email_body": "老师您好，我是{{sender_name}}。",
                    "selected_material_ids": None,
                    "outreach_generation_mode": "template",
                    "outreach_template_subject": "申请与{{name}}老师交流",
                    "outreach_template_body_text": "老师您好，我是{{sender_name}}。",
                    "outreach_template_body_html": None,
                },
            )

        self.assertEqual(response.status_code, 201, msg=response.text)
        mocked_send.assert_not_awaited()
        batch_task_id = response.json()["id"]
        item = self.client.get(f"/api/batch-tasks/{batch_task_id}/items").json()[0]
        self.assertEqual(item["status"], "approved")

    def test_create_scheduled_batch_task_requires_scheduled_dates(self) -> None:
        identity_id = self._create_identity(with_imap=False)
        llm_profile_id = self._create_llm()
        self.client.post("/api/professors/import-sample")
        professor_id = self.client.get("/api/professors").json()[0]["id"]

        response = self.client.post(
            "/api/batch-tasks",
            json={
                "identity_id": identity_id,
                "llm_profile_id": llm_profile_id,
                "name": "定时发送测试",
                "professor_ids": [professor_id],
                "schedule_type": "scheduled",
                "scheduled_dates": [],
                "window_start_time": "09:00",
                "window_end_time": "18:00",
                "emails_per_window": 20,
                "primary_material_id": None,
                "email_subject": "Hello {{导师姓名}}",
                "email_body": "Body",
                "selected_material_ids": None,
                "outreach_generation_mode": "template",
                "outreach_template_subject": "Hello {{导师姓名}}",
                "outreach_template_body_text": "Body",
                "outreach_template_body_html": None,
            },
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("发送日期", response.json()["detail"])

    def test_cancel_and_restore_scheduled_batch_item_preserves_original_plan(
        self,
    ) -> None:
        batch_task_id = self._create_scheduled_template_batch(professor_count=2)
        before_items = self.client.get(f"/api/batch-tasks/{batch_task_id}/items")
        self.assertEqual(before_items.status_code, 200, msg=before_items.text)
        original_items = before_items.json()
        item_id = original_items[0]["id"]
        original_status = original_items[0]["status"]
        original_scheduled_at = original_items[0]["scheduled_at"]

        canceled = self.client.post(
            f"/api/batch-tasks/{batch_task_id}/items/{item_id}/cancel-send",
        )

        self.assertEqual(canceled.status_code, 200, msg=canceled.text)
        canceled_task = canceled.json()["task"]
        self.assertEqual(canceled_task["status"], "running")
        self.assertEqual(canceled_task["completed_count"], 0)
        self.assertEqual(canceled_task["approved_count"], 0)
        self.assertEqual(canceled_task["scheduled_count"], 1)
        self.assertEqual(canceled_task["canceled_send_count"], 1)

        canceled_items = self.client.get(f"/api/batch-tasks/{batch_task_id}/items")
        self.assertEqual(canceled_items.status_code, 200, msg=canceled_items.text)
        self.assertEqual(
            [item["id"] for item in canceled_items.json()],
            [item["id"] for item in original_items],
        )
        canceled_item = canceled_items.json()[0]
        self.assertEqual(canceled_item["status"], original_status)
        self.assertEqual(canceled_item["scheduled_at"], original_scheduled_at)
        self.assertIsNotNone(canceled_item["batch_send_canceled_at"])
        self.assertFalse(canceled_item["can_cancel_send"])
        self.assertTrue(canceled_item["can_restore_send"])
        self.assertIsNone(canceled_item["next_action"])

        repeated_cancel = self.client.post(
            f"/api/batch-tasks/{batch_task_id}/items/{item_id}/cancel-send",
        )
        self.assertEqual(repeated_cancel.status_code, 200, msg=repeated_cancel.text)
        self.assertEqual(repeated_cancel.json()["task"]["canceled_send_count"], 1)

        restored = self.client.post(
            f"/api/batch-tasks/{batch_task_id}/items/{item_id}/restore-send",
        )

        self.assertEqual(restored.status_code, 200, msg=restored.text)
        restored_task = restored.json()["task"]
        self.assertEqual(restored_task["status"], "running")
        self.assertEqual(restored_task["approved_count"], 0)
        self.assertEqual(restored_task["scheduled_count"], 2)
        self.assertEqual(restored_task["canceled_send_count"], 0)
        restored_items = self.client.get(f"/api/batch-tasks/{batch_task_id}/items")
        self.assertEqual(restored_items.status_code, 200, msg=restored_items.text)
        restored_item = restored_items.json()[0]
        self.assertEqual(restored_item["status"], original_status)
        self.assertEqual(restored_item["scheduled_at"], original_scheduled_at)
        self.assertIsNone(restored_item["batch_send_canceled_at"])
        self.assertTrue(restored_item["can_cancel_send"])
        self.assertFalse(restored_item["can_restore_send"])

    def test_cancel_scheduled_batch_item_rejects_sent_and_sending_states(self) -> None:
        batch_task_id = self._create_scheduled_template_batch()
        item_id = self.client.get(f"/api/batch-tasks/{batch_task_id}/items").json()[0][
            "id"
        ]

        for task_status in ("sending", "sent"):
            with self.subTest(task_status=task_status):
                connection = sqlite3.connect(self.db_path)
                try:
                    connection.execute(
                        "UPDATE email_tasks SET status = ?, batch_send_canceled_at = NULL WHERE id = ?",
                        (task_status, item_id),
                    )
                    connection.commit()
                finally:
                    connection.close()

                item = self.client.get(
                    f"/api/batch-tasks/{batch_task_id}/items"
                ).json()[0]
                self.assertFalse(item["can_cancel_send"])
                canceled = self.client.post(
                    f"/api/batch-tasks/{batch_task_id}/items/{item_id}/cancel-send",
                )
                self.assertEqual(canceled.status_code, 400, msg=canceled.text)
                self.assertIn("不能取消发送", canceled.json()["detail"])

        connection = sqlite3.connect(self.db_path)
        try:
            row = connection.execute(
                "SELECT status, batch_send_canceled_at FROM email_tasks WHERE id = ?",
                (item_id,),
            ).fetchone()
        finally:
            connection.close()
        self.assertEqual(row, ("sent", None))

    def test_cancel_scheduled_batch_item_loses_cleanly_to_concurrent_send_claim(
        self,
    ) -> None:
        batch_task_id = self._create_scheduled_template_batch()
        item_id = self.client.get(f"/api/batch-tasks/{batch_task_id}/items").json()[0][
            "id"
        ]

        from app.core.database import get_engine

        claim_once = True

        def claim_before_cancel(
            conn, _cursor, statement, _parameters, _context, _executemany
        ):
            nonlocal claim_once
            normalized_statement = statement.lstrip().upper()
            if not claim_once or not normalized_statement.startswith(
                "UPDATE EMAIL_TASKS"
            ):
                return
            if "BATCH_SEND_CANCELED_AT" not in normalized_statement:
                return
            claim_once = False
            connection = sqlite3.connect(self.db_path)
            try:
                connection.execute(
                    "UPDATE email_tasks SET status = 'sending' WHERE id = ?",
                    (item_id,),
                )
                connection.commit()
            finally:
                connection.close()

        engine = get_engine()
        event.listen(engine.sync_engine, "before_cursor_execute", claim_before_cancel)
        try:
            canceled = self.client.post(
                f"/api/batch-tasks/{batch_task_id}/items/{item_id}/cancel-send",
            )
        finally:
            event.remove(
                engine.sync_engine, "before_cursor_execute", claim_before_cancel
            )

        self.assertEqual(canceled.status_code, 400, msg=canceled.text)
        self.assertIn("已进入发送流程", canceled.json()["detail"])
        connection = sqlite3.connect(self.db_path)
        try:
            row = connection.execute(
                "SELECT status, batch_send_canceled_at FROM email_tasks WHERE id = ?",
                (item_id,),
            ).fetchone()
        finally:
            connection.close()
        self.assertEqual(row, ("sending", None))

    def test_batch_task_delete_restore_and_trash_view(self) -> None:
        identity_id = self._create_identity(with_imap=False)
        llm_profile_id = self._create_llm()
        self.client.post("/api/professors/import-sample")
        professor_id = self.client.get("/api/professors").json()[0]["id"]
        created = self.client.post(
            "/api/batch-tasks",
            json={
                "identity_id": identity_id,
                "llm_profile_id": llm_profile_id,
                "name": "可删除批量任务",
                "professor_ids": [professor_id],
                "schedule_type": "immediate",
                "primary_material_id": None,
                "email_subject": "Hello {{导师姓名}}",
                "email_body": "Body",
                "selected_material_ids": None,
                "outreach_generation_mode": "template",
                "outreach_template_subject": "Hello {{导师姓名}}",
                "outreach_template_body_text": "Body",
                "outreach_template_body_html": None,
            },
        )
        self.assertEqual(created.status_code, 201, msg=created.text)
        task_id = created.json()["id"]

        deleted = self.client.post(f"/api/batch-tasks/{task_id}/delete")
        self.assertEqual(deleted.status_code, 200, msg=deleted.text)
        self.assertEqual(deleted.json()["task"]["status"], "stopped")
        self.assertIsNotNone(deleted.json()["task"]["deleted_at"])

        repeated_delete = self.client.post(f"/api/batch-tasks/{task_id}/delete")
        self.assertEqual(repeated_delete.status_code, 200, msg=repeated_delete.text)

        current = self.client.get(
            "/api/batch-tasks",
            params={"identity_id": identity_id, "llm_profile_id": llm_profile_id},
        )
        self.assertEqual(current.status_code, 200)
        self.assertEqual(current.json(), [])

        trash = self.client.get(
            "/api/batch-tasks",
            params={
                "identity_id": identity_id,
                "llm_profile_id": llm_profile_id,
                "view": "trash",
            },
        )
        self.assertEqual(trash.status_code, 200)
        self.assertEqual([item["id"] for item in trash.json()], [task_id])

        restored = self.client.post(f"/api/batch-tasks/{task_id}/restore")
        self.assertEqual(restored.status_code, 200, msg=restored.text)
        self.assertIsNone(restored.json()["task"]["deleted_at"])
        self.assertEqual(restored.json()["task"]["status"], "stopped")

        repeated_restore = self.client.post(f"/api/batch-tasks/{task_id}/restore")
        self.assertEqual(repeated_restore.status_code, 200, msg=repeated_restore.text)

    def test_batch_task_delete_reports_sending_item_without_partial_changes(
        self,
    ) -> None:
        identity_id = self._create_identity(with_imap=False)
        llm_profile_id = self._create_llm()
        self.client.post("/api/professors/import-sample")
        professor_id = self.client.get("/api/professors").json()[0]["id"]
        created = self.client.post(
            "/api/batch-tasks",
            json={
                "identity_id": identity_id,
                "llm_profile_id": llm_profile_id,
                "name": "发送中删除保护",
                "professor_ids": [professor_id],
                "schedule_type": "immediate",
                "primary_material_id": None,
                "email_subject": "Hello {{name}}",
                "email_body": "Body",
                "selected_material_ids": None,
                "outreach_generation_mode": "template",
                "outreach_template_subject": "Hello {{name}}",
                "outreach_template_body_text": "Body",
                "outreach_template_body_html": None,
            },
        )
        self.assertEqual(created.status_code, 201, msg=created.text)
        task_id = created.json()["id"]
        email_task_id = self.client.get(f"/api/batch-tasks/{task_id}/items").json()[0][
            "id"
        ]
        with closing(sqlite3.connect(self.db_path)) as connection, connection:
            connection.execute(
                "UPDATE email_tasks SET status = 'sending' WHERE id = ?",
                (email_task_id,),
            )
            connection.commit()

        blocked = self.client.post(f"/api/batch-tasks/{task_id}/delete")

        self.assertEqual(blocked.status_code, 409, msg=blocked.text)
        self.assertEqual(blocked.json()["detail"]["code"], "BATCH_TASK_TRASH_SENDING")
        self.assertEqual(
            blocked.json()["detail"]["details"],
            {
                "batch_task_id": task_id,
                "email_task_ids": [email_task_id],
                "status": "sending",
            },
        )
        with closing(sqlite3.connect(self.db_path)) as connection, connection:
            row = connection.execute(
                """
                SELECT batch_tasks.status, batch_tasks.deleted_at, email_tasks.status
                FROM batch_tasks
                JOIN email_tasks ON email_tasks.batch_task_id = batch_tasks.id
                WHERE batch_tasks.id = ? AND email_tasks.id = ?
                """,
                (task_id, email_task_id),
            ).fetchone()
        self.assertEqual(row, ("running", None, "sending"))

    def test_batch_attachment_defaults_use_latest_current_task_per_identity(
        self,
    ) -> None:
        first_identity_id = self._create_identity(
            with_imap=False,
            email_address="first-batch-defaults@example.com",
        )
        second_identity_id = self._create_identity(
            with_imap=False,
            email_address="second-batch-defaults@example.com",
        )
        llm_id = self._create_llm()
        recent_material_id = self._upload_material(
            first_identity_id,
            filename="recent-batch-default.pdf",
            content=b"recent batch default",
            material_type="portfolio",
        )
        older_material_id = self._upload_material(
            first_identity_id,
            filename="older-batch-default.pdf",
            content=b"older batch default",
            material_type="transcript",
        )

        self._insert_batch_task_with_material(
            identity_id=first_identity_id,
            llm_id=llm_id,
            status="completed",
            primary_material_id=None,
            selected_material_ids=[older_material_id],
        )
        self._insert_batch_task_with_material(
            identity_id=first_identity_id,
            llm_id=llm_id,
            status="completed",
            primary_material_id=None,
            selected_material_ids=[
                recent_material_id,
                999999,
                recent_material_id,
            ],
        )
        self._insert_batch_task_with_material(
            identity_id=first_identity_id,
            llm_id=llm_id,
            status="completed",
            primary_material_id=None,
            selected_material_ids=[older_material_id],
            deleted=True,
        )
        self._insert_batch_task_with_material(
            identity_id=second_identity_id,
            llm_id=llm_id,
            status="completed",
            primary_material_id=None,
            selected_material_ids=[older_material_id],
        )

        first_response = self.client.get(
            "/api/batch-tasks/attachment-defaults",
            params={"identity_id": first_identity_id},
        )
        second_response = self.client.get(
            "/api/batch-tasks/attachment-defaults",
            params={"identity_id": second_identity_id},
        )

        self.assertEqual(first_response.status_code, 200, msg=first_response.text)
        self.assertEqual(
            first_response.json(),
            {
                "identity_id": first_identity_id,
                "selected_material_ids": [recent_material_id],
            },
        )
        self.assertEqual(second_response.status_code, 200, msg=second_response.text)
        self.assertEqual(
            second_response.json()["selected_material_ids"],
            [older_material_id],
        )

    def test_batch_attachment_defaults_do_not_skip_latest_empty_selection(
        self,
    ) -> None:
        identity_id = self._create_identity(with_imap=False)
        llm_id = self._create_llm()
        material_id = self._upload_material(
            identity_id,
            filename="older-selected-batch-default.pdf",
            content=b"older selected batch default",
            material_type="portfolio",
        )
        self._insert_batch_task_with_material(
            identity_id=identity_id,
            llm_id=llm_id,
            status="completed",
            primary_material_id=None,
            selected_material_ids=[material_id],
        )
        self._insert_batch_task_with_material(
            identity_id=identity_id,
            llm_id=llm_id,
            status="completed",
            primary_material_id=None,
            selected_material_ids=[],
        )

        response = self.client.get(
            "/api/batch-tasks/attachment-defaults",
            params={"identity_id": identity_id},
        )

        self.assertEqual(response.status_code, 200, msg=response.text)
        self.assertEqual(response.json()["selected_material_ids"], [])

    def test_batch_attachment_defaults_are_empty_without_history_and_validate_identity(
        self,
    ) -> None:
        identity_id = self._create_identity(with_imap=False)

        response = self.client.get(
            "/api/batch-tasks/attachment-defaults",
            params={"identity_id": identity_id},
        )
        missing_response = self.client.get(
            "/api/batch-tasks/attachment-defaults",
            params={"identity_id": 999999},
        )

        self.assertEqual(response.status_code, 200, msg=response.text)
        self.assertEqual(response.json()["selected_material_ids"], [])
        self.assertEqual(missing_response.status_code, 404, msg=missing_response.text)

    def test_batch_tasks_list_is_identity_scoped_not_llm_scoped(self) -> None:
        identity_id = self._create_identity(with_imap=False)
        first_llm_id = self._create_llm()
        second_llm_response = self.client.post(
            "/api/llm-profiles",
            json={
                "name": "批量任务备用模型",
                "provider": "openai",
                "api_base_url": "https://api-backup.example.com/v1",
                "api_key": "sk-test-backup",
                "model_name": "gpt-backup",
                "matcher_prompt_template": "matcher",
                "writer_prompt_template": "writer",
                "temperature": 0.2,
                "max_tokens": 2048,
                "is_default": False,
            },
        )
        self.assertEqual(
            second_llm_response.status_code, 201, msg=second_llm_response.text
        )
        second_llm_id = second_llm_response.json()["id"]
        self.client.post("/api/professors/import-sample")
        professor_id = self.client.get("/api/professors").json()[0]["id"]

        created = self.client.post(
            "/api/batch-tasks",
            json={
                "identity_id": identity_id,
                "llm_profile_id": first_llm_id,
                "name": "模型 A 创建的批量任务",
                "professor_ids": [professor_id],
                "schedule_type": "immediate",
                "primary_material_id": None,
                "email_subject": "Hello {{导师姓名}}",
                "email_body": "Body",
                "selected_material_ids": None,
                "outreach_generation_mode": "template",
                "outreach_template_subject": "Hello {{导师姓名}}",
                "outreach_template_body_text": "Body",
                "outreach_template_body_html": None,
            },
        )
        self.assertEqual(created.status_code, 201, msg=created.text)

        from app.modules.campaigns.batch_tasks.api import _serialize_batch_task

        unloaded_task_relationships: list[set[str]] = []

        def capture_batch_task_projection(task, *, metrics=None):
            unloaded_task_relationships.append(inspect(task).unloaded)
            return _serialize_batch_task(task, metrics=metrics)

        with patch(
            "app.modules.campaigns.batch_tasks.api._serialize_batch_task",
            side_effect=capture_batch_task_projection,
        ):
            listed = self.client.get(
                "/api/batch-tasks",
                params={"identity_id": identity_id, "llm_profile_id": second_llm_id},
            )

        self.assertEqual(listed.status_code, 200, msg=listed.text)
        self.assertEqual([item["id"] for item in listed.json()], [created.json()["id"]])
        self.assertTrue(unloaded_task_relationships)
        self.assertIn("email_tasks", unloaded_task_relationships[0])

    def test_remove_batch_task_item_soft_deletes_single_draft_and_updates_target_count(
        self,
    ) -> None:
        identity_id = self._create_identity(with_imap=False)
        llm_profile_id = self._create_llm()
        self.client.post("/api/professors/import-sample")
        professors = self.client.get("/api/professors").json()
        professor_ids = [item["id"] for item in professors[:2]]

        created = self.client.post(
            "/api/batch-tasks",
            json={
                "identity_id": identity_id,
                "llm_profile_id": llm_profile_id,
                "name": "逐项移除批量草稿",
                "professor_ids": professor_ids,
                "schedule_type": "immediate",
                "primary_material_id": None,
                "email_subject": "Hello {{导师姓名}}",
                "email_body": "Body",
                "selected_material_ids": None,
                "outreach_generation_mode": "template",
                "outreach_template_subject": "Hello {{导师姓名}}",
                "outreach_template_body_text": "Body",
                "outreach_template_body_html": None,
            },
        )
        self.assertEqual(created.status_code, 201, msg=created.text)
        task_id = created.json()["id"]
        self.assertEqual(created.json()["target_count"], 2)

        items = self.client.get(f"/api/batch-tasks/{task_id}/items")
        self.assertEqual(items.status_code, 200, msg=items.text)
        removed_item_id = items.json()[0]["id"]
        connection = sqlite3.connect(self.db_path)
        try:
            connection.execute(
                """
                UPDATE email_tasks
                SET status = 'review_required',
                    generated_subject = '待审核主题',
                    generated_content_text = '待审核正文'
                WHERE id = ?
                """,
                (removed_item_id,),
            )
            connection.commit()
        finally:
            connection.close()

        removed = self.client.post(
            f"/api/batch-tasks/{task_id}/items/{removed_item_id}/delete"
        )

        self.assertEqual(removed.status_code, 200, msg=removed.text)
        self.assertEqual(removed.json()["task"]["target_count"], 1)
        refreshed_items = self.client.get(f"/api/batch-tasks/{task_id}/items")
        self.assertEqual(refreshed_items.status_code, 200, msg=refreshed_items.text)
        self.assertEqual(
            [item["id"] for item in refreshed_items.json()], [items.json()[1]["id"]]
        )
        connection = sqlite3.connect(self.db_path)
        try:
            row = connection.execute(
                """
                SELECT status, cancellation_reason, batch_task_id, scheduled_at, draft_generation_previous_status
                FROM email_tasks
                WHERE id = ?
                """,
                (removed_item_id,),
            ).fetchone()
        finally:
            connection.close()
        self.assertIsNotNone(row)
        self.assertEqual(row[0], "canceled")
        self.assertEqual(row[1], "user_removed")
        self.assertEqual(row[2], task_id)
        self.assertIsNone(row[3])
        self.assertIsNone(row[4])

    def test_batch_task_items_keep_legacy_canceled_items_without_reason_visible(
        self,
    ) -> None:
        identity_id = self._create_identity(with_imap=False)
        llm_profile_id = self._create_llm()
        self.client.post("/api/professors/import-sample")
        professor_ids = [
            item["id"] for item in self.client.get("/api/professors").json()[:2]
        ]
        created = self.client.post(
            "/api/batch-tasks",
            json={
                "identity_id": identity_id,
                "llm_profile_id": llm_profile_id,
                "name": "历史取消任务",
                "professor_ids": professor_ids,
                "schedule_type": "immediate",
                "primary_material_id": None,
                "email_subject": "Hello {{导师姓名}}",
                "email_body": "Body",
                "selected_material_ids": None,
                "outreach_generation_mode": "template",
                "outreach_template_subject": "Hello {{导师姓名}}",
                "outreach_template_body_text": "Body",
                "outreach_template_body_html": None,
            },
        )
        self.assertEqual(created.status_code, 201, msg=created.text)
        task_id = created.json()["id"]

        connection = sqlite3.connect(self.db_path)
        try:
            connection.execute(
                """
                UPDATE email_tasks
                SET status = 'canceled',
                    cancellation_reason = NULL,
                    batch_send_canceled_at = CURRENT_TIMESTAMP
                WHERE batch_task_id = ? AND professor_id = ?
                """,
                (task_id, professor_ids[0]),
            )
            connection.commit()
        finally:
            connection.close()

        items = self.client.get(f"/api/batch-tasks/{task_id}/items")
        listed = self.client.get(
            "/api/batch-tasks",
            params={"identity_id": identity_id},
        )

        self.assertEqual(items.status_code, 200, msg=items.text)
        self.assertEqual(len(items.json()), 2)
        self.assertEqual(listed.status_code, 200, msg=listed.text)
        self.assertEqual(listed.json()[0]["canceled_send_count"], 1)

    def test_remove_batch_task_item_rejects_sent_item(self) -> None:
        identity_id = self._create_identity(with_imap=False)
        llm_profile_id = self._create_llm()
        self.client.post("/api/professors/import-sample")
        professor_id = self.client.get("/api/professors").json()[0]["id"]

        created = self.client.post(
            "/api/batch-tasks",
            json={
                "identity_id": identity_id,
                "llm_profile_id": llm_profile_id,
                "name": "禁止移除已发送项",
                "professor_ids": [professor_id],
                "schedule_type": "immediate",
                "primary_material_id": None,
                "email_subject": "Hello {{导师姓名}}",
                "email_body": "Body",
                "selected_material_ids": None,
                "outreach_generation_mode": "template",
                "outreach_template_subject": "Hello {{导师姓名}}",
                "outreach_template_body_text": "Body",
                "outreach_template_body_html": None,
            },
        )
        self.assertEqual(created.status_code, 201, msg=created.text)
        task_id = created.json()["id"]
        item_id = self.client.get(f"/api/batch-tasks/{task_id}/items").json()[0]["id"]

        connection = sqlite3.connect(self.db_path)
        try:
            connection.execute(
                "UPDATE email_tasks SET status = 'sent' WHERE id = ?", (item_id,)
            )
            connection.commit()
        finally:
            connection.close()

        removed = self.client.post(f"/api/batch-tasks/{task_id}/items/{item_id}/delete")

        self.assertEqual(removed.status_code, 409, msg=removed.text)
        self.assertEqual(
            removed.json()["detail"],
            {
                "code": "BATCH_TASK_ITEM_REMOVE_BLOCKED",
                "message": (
                    f"批量任务 #{task_id} 的邮件任务 #{item_id} 当前状态为 sent，"
                    "已进入发送流程或已有发送结果，不能从任务中移除。"
                    "请在任务详情中查看该邮件。"
                ),
                "details": {
                    "batch_task_id": task_id,
                    "email_task_id": item_id,
                    "status": "sent",
                    "surface": "任务中心 > 批量任务详情",
                },
            },
        )

    def test_remove_batch_task_item_cancels_scheduled_item_even_if_send_was_canceled(
        self,
    ) -> None:
        identity_id = self._create_identity(with_imap=False)
        llm_profile_id = self._create_llm()
        self.client.post("/api/professors/import-sample")
        professor_id = self.client.get("/api/professors").json()[0]["id"]

        created = self.client.post(
            "/api/batch-tasks",
            json={
                "identity_id": identity_id,
                "llm_profile_id": llm_profile_id,
                "name": "reject scheduled removal",
                "professor_ids": [professor_id],
                "schedule_type": "immediate",
                "primary_material_id": None,
                "email_subject": "Hello {{name}}",
                "email_body": "Body",
                "selected_material_ids": None,
                "outreach_generation_mode": "template",
                "outreach_template_subject": "Hello {{name}}",
                "outreach_template_body_text": "Body",
                "outreach_template_body_html": None,
            },
        )
        self.assertEqual(created.status_code, 201, msg=created.text)
        task_id = created.json()["id"]
        item_id = self.client.get(f"/api/batch-tasks/{task_id}/items").json()[0]["id"]

        connection = sqlite3.connect(self.db_path)
        try:
            connection.execute(
                """
                UPDATE email_tasks
                SET status = 'scheduled',
                    scheduled_at = datetime('now', '+1 day'),
                    batch_send_canceled_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (item_id,),
            )
            connection.commit()
        finally:
            connection.close()

        removed = self.client.post(f"/api/batch-tasks/{task_id}/items/{item_id}/delete")

        self.assertEqual(removed.status_code, 200, msg=removed.text)
        self.assertEqual(removed.json()["task"]["target_count"], 0)
        with closing(sqlite3.connect(self.db_path)) as connection, connection:
            row = connection.execute(
                """
                SELECT status, cancellation_reason, scheduled_at
                FROM email_tasks WHERE id = ?
                """,
                (item_id,),
            ).fetchone()
        self.assertEqual(row, ("canceled", "user_removed", None))

    def test_remove_batch_task_item_cancels_concurrent_schedule_before_send(
        self,
    ) -> None:
        identity_id = self._create_identity(with_imap=False)
        llm_profile_id = self._create_llm()
        self.client.post("/api/professors/import-sample")
        professor_id = self.client.get("/api/professors").json()[0]["id"]

        created = self.client.post(
            "/api/batch-tasks",
            json={
                "identity_id": identity_id,
                "llm_profile_id": llm_profile_id,
                "name": "reject stale delete after schedule",
                "professor_ids": [professor_id],
                "schedule_type": "immediate",
                "primary_material_id": None,
                "email_subject": "Hello {{name}}",
                "email_body": "Body",
                "selected_material_ids": None,
                "outreach_generation_mode": "template",
                "outreach_template_subject": "Hello {{name}}",
                "outreach_template_body_text": "Body",
                "outreach_template_body_html": None,
            },
        )
        self.assertEqual(created.status_code, 201, msg=created.text)
        task_id = created.json()["id"]
        item_id = self.client.get(f"/api/batch-tasks/{task_id}/items").json()[0]["id"]

        connection = sqlite3.connect(self.db_path)
        try:
            connection.execute(
                "UPDATE email_tasks SET status = 'review_required' WHERE id = ?",
                (item_id,),
            )
            connection.commit()
        finally:
            connection.close()

        from app.core.database import get_engine

        schedule_once = True

        def schedule_before_delete(
            conn, _cursor, statement, _parameters, _context, _executemany
        ):
            nonlocal schedule_once
            if not schedule_once:
                return
            if not statement.lstrip().upper().startswith("UPDATE EMAIL_TASKS"):
                return
            if "CANCELLATION_REASON" not in statement.upper():
                return
            schedule_once = False
            connection = sqlite3.connect(self.db_path)
            try:
                connection.execute(
                    "UPDATE email_tasks SET status = 'scheduled', scheduled_at = datetime('now', '+1 day') WHERE id = ?",
                    (item_id,),
                )
                connection.commit()
            finally:
                connection.close()

        engine = get_engine()
        event.listen(
            engine.sync_engine, "before_cursor_execute", schedule_before_delete
        )
        try:
            removed = self.client.post(
                f"/api/batch-tasks/{task_id}/items/{item_id}/delete"
            )
        finally:
            event.remove(
                engine.sync_engine, "before_cursor_execute", schedule_before_delete
            )

        self.assertEqual(removed.status_code, 200, msg=removed.text)
        connection = sqlite3.connect(self.db_path)
        try:
            row = connection.execute(
                """
                SELECT email_tasks.status, email_tasks.cancellation_reason, batch_tasks.target_count
                FROM email_tasks
                JOIN batch_tasks ON batch_tasks.id = email_tasks.batch_task_id
                WHERE email_tasks.id = ?
                """,
                (item_id,),
            ).fetchone()
        finally:
            connection.close()
        self.assertEqual(row, ("canceled", "user_removed", 0))

    def test_remove_batch_task_item_cancels_active_draft_claim(self) -> None:
        identity_id = self._create_identity(with_imap=False)
        llm_profile_id = self._create_llm()
        self.client.post("/api/professors/import-sample")
        professor_id = self.client.get("/api/professors").json()[0]["id"]
        created = self.client.post(
            "/api/batch-tasks",
            json={
                "identity_id": identity_id,
                "llm_profile_id": llm_profile_id,
                "name": "remove active draft claim",
                "professor_ids": [professor_id],
                "schedule_type": "immediate",
                "primary_material_id": None,
                "email_subject": "Hello {{name}}",
                "email_body": "Body",
                "selected_material_ids": None,
                "outreach_generation_mode": "template",
                "outreach_template_subject": "Hello {{name}}",
                "outreach_template_body_text": "Body",
                "outreach_template_body_html": None,
            },
        )
        self.assertEqual(created.status_code, 201, msg=created.text)
        task_id = created.json()["id"]
        item_id = self.client.get(f"/api/batch-tasks/{task_id}/items").json()[0]["id"]
        with closing(sqlite3.connect(self.db_path)) as connection, connection:
            connection.execute(
                """
                UPDATE email_tasks
                SET status = 'generating_draft',
                    draft_generation_previous_status = 'discovered',
                    draft_generation_started_at = CURRENT_TIMESTAMP,
                    draft_claim_id = 'claim-to-cancel',
                    draft_claimed_at = CURRENT_TIMESTAMP,
                    draft_lease_expires_at = datetime('now', '+5 minutes')
                WHERE id = ?
                """,
                (item_id,),
            )
            connection.commit()

        removed = self.client.post(f"/api/batch-tasks/{task_id}/items/{item_id}/delete")

        self.assertEqual(removed.status_code, 200, msg=removed.text)
        with closing(sqlite3.connect(self.db_path)) as connection, connection:
            row = connection.execute(
                """
                SELECT status, cancellation_reason, draft_claim_id,
                       draft_claimed_at, draft_lease_expires_at
                FROM email_tasks WHERE id = ?
                """,
                (item_id,),
            ).fetchone()
        self.assertEqual(row, ("canceled", "user_removed", None, None, None))

    def test_stop_batch_task_keeps_user_removed_item_hidden(self) -> None:
        identity_id = self._create_identity(with_imap=False)
        llm_profile_id = self._create_llm()
        self.client.post("/api/professors/import-sample")
        professors = self.client.get("/api/professors").json()
        professor_ids = [item["id"] for item in professors[:2]]

        created = self.client.post(
            "/api/batch-tasks",
            json={
                "identity_id": identity_id,
                "llm_profile_id": llm_profile_id,
                "name": "keep removed item hidden",
                "professor_ids": professor_ids,
                "schedule_type": "immediate",
                "primary_material_id": None,
                "email_subject": "Hello {{name}}",
                "email_body": "Body",
                "selected_material_ids": None,
                "outreach_generation_mode": "template",
                "outreach_template_subject": "Hello {{name}}",
                "outreach_template_body_text": "Body",
                "outreach_template_body_html": None,
            },
        )
        self.assertEqual(created.status_code, 201, msg=created.text)
        task_id = created.json()["id"]
        items = self.client.get(f"/api/batch-tasks/{task_id}/items").json()
        removed_item_id = items[0]["id"]
        kept_item_id = items[1]["id"]

        connection = sqlite3.connect(self.db_path)
        try:
            connection.execute(
                "UPDATE email_tasks SET status = 'review_required' WHERE id = ?",
                (removed_item_id,),
            )
            connection.commit()
        finally:
            connection.close()
        removed = self.client.post(
            f"/api/batch-tasks/{task_id}/items/{removed_item_id}/delete"
        )
        self.assertEqual(removed.status_code, 200, msg=removed.text)

        stopped = self.client.post(f"/api/batch-tasks/{task_id}/stop")
        self.assertEqual(stopped.status_code, 200, msg=stopped.text)

        refreshed_items = self.client.get(f"/api/batch-tasks/{task_id}/items")
        self.assertEqual(refreshed_items.status_code, 200, msg=refreshed_items.text)
        self.assertEqual(
            [item["id"] for item in refreshed_items.json()], [kept_item_id]
        )
        connection = sqlite3.connect(self.db_path)
        try:
            row = connection.execute(
                "SELECT status, cancellation_reason FROM email_tasks WHERE id = ?",
                (removed_item_id,),
            ).fetchone()
        finally:
            connection.close()
        self.assertEqual(row, ("canceled", "user_removed"))

    def test_workspace_ignores_user_removed_batch_item(self) -> None:
        identity_id = self._create_identity(with_imap=False)
        llm_profile_id = self._create_llm()
        self.client.post("/api/professors/import-sample")
        professor_id = self.client.get("/api/professors").json()[0]["id"]

        created = self.client.post(
            "/api/batch-tasks",
            json={
                "identity_id": identity_id,
                "llm_profile_id": llm_profile_id,
                "name": "workspace ignores removed",
                "professor_ids": [professor_id],
                "schedule_type": "immediate",
                "primary_material_id": None,
                "email_subject": "Hello {{name}}",
                "email_body": "Body",
                "selected_material_ids": None,
                "outreach_generation_mode": "template",
                "outreach_template_subject": "Hello {{name}}",
                "outreach_template_body_text": "Body",
                "outreach_template_body_html": None,
            },
        )
        self.assertEqual(created.status_code, 201, msg=created.text)
        task_id = created.json()["id"]
        item_id = self.client.get(f"/api/batch-tasks/{task_id}/items").json()[0]["id"]

        connection = sqlite3.connect(self.db_path)
        try:
            connection.execute(
                "UPDATE email_tasks SET status = 'review_required' WHERE id = ?",
                (item_id,),
            )
            connection.commit()
        finally:
            connection.close()
        removed = self.client.post(f"/api/batch-tasks/{task_id}/items/{item_id}/delete")
        self.assertEqual(removed.status_code, 200, msg=removed.text)

        workspace = self.client.get(
            f"/api/workspaces/{professor_id}",
            params={"identity_id": identity_id, "llm_profile_id": llm_profile_id},
        )

        self.assertEqual(workspace.status_code, 200, msg=workspace.text)
        payload = workspace.json()
        self.assertNotEqual(payload["current_task"]["id"], item_id)
        if payload["current_task"]["id"] is not None:
            self.assertIsNone(payload["current_task"]["batch_task_id"])

    def test_professor_dashboard_ignores_user_removed_batch_item(self) -> None:
        identity_id = self._create_identity(with_imap=False)
        llm_profile_id = self._create_llm()
        material_id = self._upload_material(
            identity_id,
            filename="resume.txt",
            content=b"AI agents and information extraction",
            material_type="resume",
        )
        professor_id = self._create_professor(
            email="dashboard-user-removed@example.edu"
        )

        created = self.client.post(
            "/api/batch-tasks",
            json={
                "identity_id": identity_id,
                "llm_profile_id": llm_profile_id,
                "name": "dashboard ignores removed",
                "professor_ids": [professor_id],
                "schedule_type": "immediate",
                "primary_material_id": material_id,
                "email_subject": "Hello {{name}}",
                "email_body": "Body",
                "selected_material_ids": None,
                "outreach_generation_mode": "llm",
                "outreach_template_subject": "Hello {{name}}",
                "outreach_template_body_text": "Body",
                "outreach_template_body_html": None,
            },
        )
        self.assertEqual(created.status_code, 201, msg=created.text)
        task_id = created.json()["id"]
        item_id = self.client.get(f"/api/batch-tasks/{task_id}/items").json()[0]["id"]

        connection = sqlite3.connect(self.db_path)
        try:
            connection.execute(
                """
                UPDATE email_tasks
                SET status = 'review_required',
                    match_score = 91,
                    match_reason = 'removed item should not count'
                WHERE id = ?
                """,
                (item_id,),
            )
            connection.commit()
        finally:
            connection.close()
        removed = self.client.post(f"/api/batch-tasks/{task_id}/items/{item_id}/delete")
        self.assertEqual(removed.status_code, 200, msg=removed.text)

        dashboard = self.client.get(
            "/api/professors",
            params={
                "identity_id": identity_id,
                "llm_profile_id": llm_profile_id,
                "ids": str(professor_id),
            },
        )

        self.assertEqual(dashboard.status_code, 200, msg=dashboard.text)
        professor = dashboard.json()[0]
        self.assertEqual(professor["status"], "not_contacted")
        self.assertIsNone(professor["match_score"])

    def test_removed_batch_item_does_not_reappear_when_generation_finishes(
        self,
    ) -> None:
        identity_id = self._create_identity(with_imap=False)
        llm_profile_id = self._create_llm()
        material_id = self._upload_material(
            identity_id,
            filename="resume.txt",
            content=b"AI agents and information extraction",
            material_type="resume",
        )
        professor_id = self._create_professor(
            email="removed-generation-race@example.edu"
        )

        created = self.client.post(
            "/api/batch-tasks",
            json={
                "identity_id": identity_id,
                "llm_profile_id": llm_profile_id,
                "name": "removed generation race",
                "professor_ids": [professor_id],
                "schedule_type": "immediate",
                "window_start_time": None,
                "window_end_time": None,
                "emails_per_window": None,
                "primary_material_id": material_id,
                "email_subject": None,
                "email_body": None,
                "selected_material_ids": None,
                "outreach_generation_mode": "llm",
                "outreach_template_subject": "Hello {{name}}",
                "outreach_template_body_text": "Body {{research_direction}}",
                "outreach_template_body_html": None,
            },
        )
        self.assertEqual(created.status_code, 201, msg=created.text)
        task_id = created.json()["id"]
        item_id = self.client.get(f"/api/batch-tasks/{task_id}/items").json()[0]["id"]

        async def _remove_item_before_generation_returns(**_kwargs):
            connection = sqlite3.connect(self.db_path)
            try:
                connection.execute(
                    """
                    UPDATE email_tasks
                    SET status = 'canceled',
                        cancellation_reason = 'user_removed',
                        scheduled_at = NULL,
                        draft_generation_previous_status = NULL
                    WHERE id = ?
                    """,
                    (item_id,),
                )
                connection.commit()
            finally:
                connection.close()
            return self._build_draft_generation_result(
                subject="Should not reappear",
                body_text="This generated draft should be discarded.",
                body_html="<p>This generated draft should be discarded.</p>",
            )

        from app.core.database import get_session_factory
        from app.modules.workspace.tasks.runtime import generate_task_draft

        with patch(
            "app.modules.workspace.tasks.runtime.llm_runtime.generate_draft_content",
            AsyncMock(side_effect=_remove_item_before_generation_returns),
        ):
            self._run_async(
                generate_task_draft(get_session_factory(), item_id, force=True)
            )

        connection = sqlite3.connect(self.db_path)
        try:
            row = connection.execute(
                """
                SELECT status, cancellation_reason, generated_subject, generated_content_text
                FROM email_tasks
                WHERE id = ?
                """,
                (item_id,),
            ).fetchone()
        finally:
            connection.close()
        self.assertEqual(row[0], "canceled")
        self.assertEqual(row[1], "user_removed")
        self.assertNotEqual(row[2], "Should not reappear")
        self.assertNotEqual(row[3], "This generated draft should be discarded.")

        items = self.client.get(f"/api/batch-tasks/{task_id}/items")
        self.assertEqual(items.status_code, 200, msg=items.text)
        self.assertEqual(items.json(), [])

    def test_removed_batch_item_stays_removed_when_generation_fails(self) -> None:
        from app.modules.llm import runtime as llm_runtime

        identity_id = self._create_identity(with_imap=False)
        llm_profile_id = self._create_llm()
        material_id = self._upload_material(
            identity_id,
            filename="resume.txt",
            content=b"AI agents and information extraction",
            material_type="resume",
        )
        professor_id = self._create_professor(
            email="removed-generation-failure@example.edu"
        )

        created = self.client.post(
            "/api/batch-tasks",
            json={
                "identity_id": identity_id,
                "llm_profile_id": llm_profile_id,
                "name": "removed generation failure",
                "professor_ids": [professor_id],
                "schedule_type": "immediate",
                "window_start_time": None,
                "window_end_time": None,
                "emails_per_window": None,
                "primary_material_id": material_id,
                "email_subject": None,
                "email_body": None,
                "selected_material_ids": None,
                "outreach_generation_mode": "llm",
                "outreach_template_subject": "Hello {{name}}",
                "outreach_template_body_text": "Body {{research_direction}}",
                "outreach_template_body_html": None,
            },
        )
        self.assertEqual(created.status_code, 201, msg=created.text)
        item_id = self.client.get(
            f"/api/batch-tasks/{created.json()['id']}/items"
        ).json()[0]["id"]

        async def _remove_item_then_fail(**_kwargs):
            connection = sqlite3.connect(self.db_path)
            try:
                connection.execute(
                    """
                    UPDATE email_tasks
                    SET status = 'canceled',
                        cancellation_reason = 'user_removed',
                        scheduled_at = NULL,
                        draft_generation_previous_status = NULL
                    WHERE id = ?
                    """,
                    (item_id,),
                )
                connection.commit()
            finally:
                connection.close()
            raise llm_runtime.LLMRuntimeError("generation failed after removal")

        from app.core.database import get_session_factory
        from app.modules.workspace.tasks.runtime import generate_task_draft

        with patch(
            "app.modules.workspace.tasks.runtime.llm_runtime.generate_draft_content",
            AsyncMock(side_effect=_remove_item_then_fail),
        ):
            with self.assertRaises(llm_runtime.LLMRuntimeError):
                self._run_async(
                    generate_task_draft(get_session_factory(), item_id, force=True)
                )

        connection = sqlite3.connect(self.db_path)
        try:
            row = connection.execute(
                "SELECT status, cancellation_reason FROM email_tasks WHERE id = ?",
                (item_id,),
            ).fetchone()
        finally:
            connection.close()
        self.assertEqual(row, ("canceled", "user_removed"))

    def test_removed_batch_item_stays_removed_when_automatic_generation_is_canceled(
        self,
    ) -> None:
        identity_id = self._create_identity(with_imap=False)
        llm_profile_id = self._create_llm()
        material_id = self._upload_material(
            identity_id,
            filename="resume.txt",
            content=b"AI agents and information extraction",
            material_type="resume",
        )
        professor_id = self._create_professor(
            email="removed-generation-canceled@example.edu"
        )

        created = self.client.post(
            "/api/batch-tasks",
            json={
                "identity_id": identity_id,
                "llm_profile_id": llm_profile_id,
                "name": "removed generation canceled",
                "professor_ids": [professor_id],
                "schedule_type": "immediate",
                "window_start_time": None,
                "window_end_time": None,
                "emails_per_window": None,
                "primary_material_id": material_id,
                "email_subject": None,
                "email_body": None,
                "selected_material_ids": None,
                "outreach_generation_mode": "llm",
                "outreach_template_subject": "Hello {{name}}",
                "outreach_template_body_text": "Body {{research_direction}}",
                "outreach_template_body_html": None,
            },
        )
        self.assertEqual(created.status_code, 201, msg=created.text)
        item_id = self.client.get(
            f"/api/batch-tasks/{created.json()['id']}/items"
        ).json()[0]["id"]

        connection = sqlite3.connect(self.db_path)
        try:
            connection.execute(
                """
                UPDATE email_tasks
                SET status = 'generating_draft',
                    draft_generation_previous_status = 'matched'
                WHERE id = ?
                """,
                (item_id,),
            )
            connection.commit()
        finally:
            connection.close()

        async def _remove_item_then_cancel(**_kwargs):
            connection = sqlite3.connect(self.db_path)
            try:
                connection.execute(
                    """
                    UPDATE email_tasks
                    SET status = 'canceled',
                        cancellation_reason = 'user_removed',
                        scheduled_at = NULL,
                        draft_generation_previous_status = NULL
                    WHERE id = ?
                    """,
                    (item_id,),
                )
                connection.commit()
            finally:
                connection.close()
            raise asyncio.CancelledError()

        from app.core.database import get_session_factory
        from app.modules.workspace.tasks.runtime import generate_task_draft

        with patch(
            "app.modules.workspace.tasks.runtime.llm_runtime.generate_draft_content",
            AsyncMock(side_effect=_remove_item_then_cancel),
        ):
            with self.assertRaises(asyncio.CancelledError):
                self._run_async(
                    generate_task_draft(
                        get_session_factory(),
                        item_id,
                        force=True,
                        automatic_batch=True,
                        require_running_batch=True,
                    ),
                )

        connection = sqlite3.connect(self.db_path)
        try:
            row = connection.execute(
                "SELECT status, cancellation_reason FROM email_tasks WHERE id = ?",
                (item_id,),
            ).fetchone()
        finally:
            connection.close()
        self.assertEqual(row, ("canceled", "user_removed"))

    def test_remove_last_batch_task_item_marks_task_completed(self) -> None:
        identity_id = self._create_identity(with_imap=False)
        llm_profile_id = self._create_llm()
        self.client.post("/api/professors/import-sample")
        professor_id = self.client.get("/api/professors").json()[0]["id"]

        created = self.client.post(
            "/api/batch-tasks",
            json={
                "identity_id": identity_id,
                "llm_profile_id": llm_profile_id,
                "name": "删除最后一封草稿",
                "professor_ids": [professor_id],
                "schedule_type": "immediate",
                "primary_material_id": None,
                "email_subject": "Hello {{导师姓名}}",
                "email_body": "Body",
                "selected_material_ids": None,
                "outreach_generation_mode": "template",
                "outreach_template_subject": "Hello {{导师姓名}}",
                "outreach_template_body_text": "Body",
                "outreach_template_body_html": None,
            },
        )
        self.assertEqual(created.status_code, 201, msg=created.text)
        task_id = created.json()["id"]
        item_id = self.client.get(f"/api/batch-tasks/{task_id}/items").json()[0]["id"]

        connection = sqlite3.connect(self.db_path)
        try:
            connection.execute(
                "UPDATE email_tasks SET status = 'review_required' WHERE id = ?",
                (item_id,),
            )
            connection.commit()
        finally:
            connection.close()

        removed = self.client.post(f"/api/batch-tasks/{task_id}/items/{item_id}/delete")

        self.assertEqual(removed.status_code, 200, msg=removed.text)
        self.assertEqual(removed.json()["task"]["target_count"], 0)
        self.assertEqual(removed.json()["task"]["status"], "completed")

    def test_create_scheduled_batch_task_returns_normalized_scheduled_dates(
        self,
    ) -> None:
        identity_id = self._create_identity(with_imap=False)
        llm_profile_id = self._create_llm()
        self.client.post("/api/professors/import-sample")
        professor_id = self.client.get("/api/professors").json()[0]["id"]
        tomorrow = (datetime.now().date() + timedelta(days=1)).isoformat()
        day_after_tomorrow = (datetime.now().date() + timedelta(days=2)).isoformat()

        response = self.client.post(
            "/api/batch-tasks",
            json={
                "identity_id": identity_id,
                "llm_profile_id": llm_profile_id,
                "name": "日历定时发送",
                "professor_ids": [professor_id],
                "schedule_type": "scheduled",
                "scheduled_dates": [day_after_tomorrow, tomorrow, day_after_tomorrow],
                "window_start_time": "09:00",
                "window_end_time": "18:00",
                "emails_per_window": 20,
                "primary_material_id": None,
                "email_subject": "Hello {{导师姓名}}",
                "email_body": "Body",
                "selected_material_ids": None,
                "outreach_generation_mode": "template",
                "outreach_template_subject": "Hello {{导师姓名}}",
                "outreach_template_body_text": "Body",
                "outreach_template_body_html": None,
            },
        )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(
            response.json()["scheduled_dates"], [tomorrow, day_after_tomorrow]
        )

    def test_create_scheduled_batch_task_assigns_jittered_scheduled_at(self) -> None:
        identity_id = self._create_identity(with_imap=False)
        llm_profile_id = self._create_llm()
        self.client.post("/api/professors/import-sample")
        professor_ids = [
            item["id"] for item in self.client.get("/api/professors").json()[:3]
        ]
        tomorrow = (datetime.now().date() + timedelta(days=1)).isoformat()

        response = self.client.post(
            "/api/batch-tasks",
            json={
                "identity_id": identity_id,
                "llm_profile_id": llm_profile_id,
                "name": "随机均匀定时发送",
                "professor_ids": professor_ids,
                "schedule_type": "scheduled",
                "scheduled_dates": [tomorrow],
                "window_start_time": "09:00",
                "window_end_time": "18:00",
                "emails_per_window": 20,
                "primary_material_id": None,
                "email_subject": "Hello {{导师姓名}}",
                "email_body": "Body",
                "selected_material_ids": None,
                "outreach_generation_mode": "template",
                "outreach_template_subject": "Hello {{导师姓名}}",
                "outreach_template_body_text": "Body",
                "outreach_template_body_html": None,
            },
        )

        self.assertEqual(response.status_code, 201, msg=response.text)
        task_id = response.json()["id"]
        items_response = self.client.get(f"/api/batch-tasks/{task_id}/items")
        self.assertEqual(items_response.status_code, 200)
        scheduled_values = [item["scheduled_at"] for item in items_response.json()]
        self.assertEqual(len(scheduled_values), len(professor_ids))
        self.assertTrue(all(value is not None for value in scheduled_values))
        self.assertEqual(scheduled_values, sorted(scheduled_values))

    def test_create_scheduled_batch_task_rejects_insufficient_schedule_capacity(
        self,
    ) -> None:
        identity_id = self._create_identity(with_imap=False)
        llm_profile_id = self._create_llm()
        self.client.post("/api/professors/import-sample")
        professor_ids = [
            item["id"] for item in self.client.get("/api/professors").json()[:2]
        ]
        tomorrow = (datetime.now().date() + timedelta(days=1)).isoformat()

        response = self.client.post(
            "/api/batch-tasks",
            json={
                "identity_id": identity_id,
                "llm_profile_id": llm_profile_id,
                "name": "容量不足定时发送",
                "professor_ids": professor_ids,
                "schedule_type": "scheduled",
                "scheduled_dates": [tomorrow],
                "window_start_time": "09:00",
                "window_end_time": "18:00",
                "emails_per_window": 1,
                "primary_material_id": None,
                "email_subject": "Hello {{导师姓名}}",
                "email_body": "Body",
                "selected_material_ids": None,
                "outreach_generation_mode": "template",
                "outreach_template_subject": "Hello {{导师姓名}}",
                "outreach_template_body_text": "Body",
                "outreach_template_body_html": None,
            },
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("不足以覆盖全部任务", response.json()["detail"])

    def test_create_scheduled_batch_task_rejects_expired_windows(self) -> None:
        identity_id = self._create_identity(with_imap=False)
        llm_profile_id = self._create_llm()
        self.client.post("/api/professors/import-sample")
        professor_id = self.client.get("/api/professors").json()[0]["id"]
        expired_date = (datetime.now().date() - timedelta(days=1)).isoformat()

        response = self.client.post(
            "/api/batch-tasks",
            json={
                "identity_id": identity_id,
                "llm_profile_id": llm_profile_id,
                "name": "过期定时发送",
                "professor_ids": [professor_id],
                "schedule_type": "scheduled",
                "scheduled_dates": [expired_date],
                "window_start_time": "09:00",
                "window_end_time": "18:00",
                "emails_per_window": 20,
                "primary_material_id": None,
                "email_subject": "Hello {{导师姓名}}",
                "email_body": "Body",
                "selected_material_ids": None,
                "outreach_generation_mode": "template",
                "outreach_template_subject": "Hello {{导师姓名}}",
                "outreach_template_body_text": "Body",
                "outreach_template_body_html": None,
            },
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("发送窗口已全部过期", response.json()["detail"])

    def test_create_scheduled_batch_task_rejects_invalid_window_time_format(
        self,
    ) -> None:
        identity_id = self._create_identity(with_imap=False)
        llm_profile_id = self._create_llm()
        self.client.post("/api/professors/import-sample")
        professor_id = self.client.get("/api/professors").json()[0]["id"]

        response = self.client.post(
            "/api/batch-tasks",
            json={
                "identity_id": identity_id,
                "llm_profile_id": llm_profile_id,
                "name": "时间格式校验",
                "professor_ids": [professor_id],
                "schedule_type": "scheduled",
                "scheduled_dates": ["2026-05-04"],
                "window_start_time": "9:00",
                "window_end_time": "18:00",
                "emails_per_window": 20,
                "primary_material_id": None,
                "email_subject": "Hello {{导师姓名}}",
                "email_body": "Body",
                "selected_material_ids": None,
                "outreach_generation_mode": "template",
                "outreach_template_subject": "Hello {{导师姓名}}",
                "outreach_template_body_text": "Body",
                "outreach_template_body_html": None,
            },
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("HH:mm", response.json()["detail"])

    def test_batch_task_worker_and_workspace_flow(self) -> None:
        identity_id = self._create_identity(with_imap=False)
        llm_id = self._create_llm()
        resume_material_id = self._upload_material(
            identity_id,
            filename="resume.txt",
            content=b"My background covers large language models and IE.",
            material_type="resume",
        )
        publication_material_id = self._upload_material(
            identity_id,
            filename="paper.md",
            content=b"# Publication\nA paper about extraction and LLMs.",
            material_type="publication",
        )

        import_response = self.client.post("/api/professors/import-sample")
        self.assertEqual(import_response.status_code, 200)

        professor_list = self.client.get(
            "/api/professors",
            params={"identity_id": identity_id, "llm_profile_id": llm_id},
        )
        professors = professor_list.json()
        selected_professor_ids = [item["id"] for item in professors[:2]]

        task_response = self.client.post(
            "/api/batch-tasks",
            json={
                "identity_id": identity_id,
                "llm_profile_id": llm_id,
                "name": "首轮联系任务",
                "professor_ids": selected_professor_ids,
                "schedule_type": "immediate",
                "window_start_time": None,
                "window_end_time": None,
                "emails_per_window": None,
                "primary_material_id": resume_material_id,
                "email_subject": "科研交流申请",
                "email_body": "老师您好，这是自定义模板。",
                "selected_material_ids": [publication_material_id],
            },
        )
        self.assertEqual(task_response.status_code, 201)
        self.assertEqual(task_response.json()["pending_generation_count"], 2)

        updated_professors = self.client.get(
            "/api/professors",
            params={"identity_id": identity_id, "llm_profile_id": llm_id},
        ).json()
        selected_professor = next(
            item
            for item in updated_professors
            if item["id"] == selected_professor_ids[0]
        )
        self.assertEqual(selected_professor["status"], "preparing")
        self.assertIsNone(selected_professor["match_score"])

        batch_task_id = task_response.json()["id"]
        batch_items = self.client.get(f"/api/batch-tasks/{batch_task_id}/items").json()
        task_id = next(
            item["id"]
            for item in batch_items
            if item["professor_id"] == selected_professor_ids[0]
        )
        workspace_before = self.client.get(
            f"/api/batch-tasks/{batch_task_id}/items/{task_id}/thread"
        )
        self.assertEqual(workspace_before.status_code, 200)
        self.assertEqual(
            workspace_before.json()["current_task"]["primary_material_id"],
            resume_material_id,
        )
        self.assertEqual(
            workspace_before.json()["current_task"]["selected_material_ids"],
            [publication_material_id],
        )

        with patch(
            "app.modules.matching.task_analysis.llm_runtime.generate_match_evaluation",
            AsyncMock(
                return_value=self._build_match_evaluation_result(
                    match_score=93,
                ),
            ),
        ):
            match_workspace = self.client.post(
                f"/api/email-tasks/{task_id}/calculate-match",
            )

        with patch(
            "app.modules.workspace.tasks.runtime.llm_runtime.generate_draft_content",
            AsyncMock(
                return_value=self._build_draft_generation_result(
                    subject="更新后的套磁申请",
                    body_text="老师您好，这是切换默认材料后的草稿。",
                    body_html="<p>老师您好，这是切换默认材料后的草稿。</p>",
                    prompt_tokens=612,
                    completion_tokens=248,
                ),
            ),
        ):
            generated_workspace = self.client.post(
                f"/api/email-tasks/{task_id}/generate-draft",
            )

            switched_workspace = self.client.post(
                f"/api/email-tasks/{task_id}/primary-material",
                json={"primary_material_id": publication_material_id},
            )

        self.assertEqual(match_workspace.status_code, 200)
        matched_thread = self.client.get(
            f"/api/batch-tasks/{batch_task_id}/items/{task_id}/thread"
        ).json()
        self.assertEqual(matched_thread["current_task"]["match_score"], 93)
        self.assertEqual(generated_workspace.status_code, 200)
        generated_thread = self.client.get(
            f"/api/batch-tasks/{batch_task_id}/items/{task_id}/thread"
        ).json()
        self.assertEqual(generated_thread["current_task"]["status"], "review_required")
        self.assertEqual(
            generated_thread["current_task"]["generated_subject"], "更新后的套磁申请"
        )
        self.assertEqual(
            generated_thread["current_task"]["last_draft_prompt_tokens"], 612
        )
        self.assertEqual(
            generated_thread["current_task"]["last_draft_completion_tokens"], 248
        )
        self.assertEqual(
            generated_thread["current_task"]["last_draft_total_tokens"], 860
        )
        self.assertGreater(
            generated_thread["current_task"]["estimated_prompt_tokens"], 0
        )
        self.assertEqual(generated_thread["messages"][-1]["prompt_tokens"], 612)
        self.assertEqual(generated_thread["messages"][-1]["completion_tokens"], 248)
        self.assertEqual(generated_thread["messages"][-1]["total_tokens"], 860)
        operation_logs = self.client.get(
            "/api/diagnostics/operation-logs",
            params={"event_name": "email_task.draft_generated"},
        )
        self.assertEqual(operation_logs.status_code, 200, msg=operation_logs.text)
        draft_generated_metadata = operation_logs.json()["items"][0]["metadata"]
        self.assertEqual(draft_generated_metadata["prompt_tokens"], 612)
        self.assertEqual(draft_generated_metadata["completion_tokens"], 248)
        self.assertEqual(draft_generated_metadata["total_tokens"], 860)
        self.assertEqual(switched_workspace.status_code, 200)
        switched_thread = self.client.get(
            f"/api/batch-tasks/{batch_task_id}/items/{task_id}/thread"
        ).json()
        self.assertEqual(
            switched_thread["current_task"]["primary_material_id"],
            publication_material_id,
        )
        self.assertEqual(switched_thread["current_task"]["status"], "review_required")

        with patch(
            "app.modules.workspace.tasks.delivery.mail_runtime.send_email",
            AsyncMock(
                return_value=self._build_send_result(
                    message_id="<manual-send@example.com>",
                    provider_payload={"smtp_host": "smtp.example.com"},
                ),
            ),
        ) as mocked_send:
            workspace_after = self.client.post(
                f"/api/email-tasks/{task_id}/approve-and-send",
                json={
                    "subject": "科研交流申请",
                    "body_text": "老师您好，我希望进一步交流。",
                    "body_html": None,
                    "selected_material_ids": [],
                },
            )
        payload = self.client.get(f"/api/email-tasks/{task_id}/thread").json()
        self.assertEqual(workspace_after.status_code, 200)
        self.assertEqual(payload["current_task"]["status"], "sent")
        self.assertNotIn("delivery_mode", payload["current_task"])
        self.assertEqual(payload["current_task"]["selected_material_ids"], [])
        self.assertGreaterEqual(len(payload["messages"]), 2)
        sent_message = next(
            message
            for message in payload["messages"]
            if message["rfc_message_id"] == "<manual-send@example.com>"
        )
        self.assertEqual(sent_message["direction"], "sent")
        self.assertEqual(sent_message["subject"], "科研交流申请")
        mocked_send.assert_awaited_once()

    def test_email_task_actions_return_requested_batch_item(self) -> None:
        identity_id = self._create_identity(with_imap=False)
        llm_id = self._create_llm()
        material_id = self._upload_material(
            identity_id,
            filename="exact-task-response-resume.txt",
            content=b"My background covers information extraction and agents.",
            material_type="resume",
        )
        professor_id = self._create_professor(email="exact-task-response@example.edu")
        requested_batch_id = self._insert_batch_task_with_material(
            identity_id=identity_id,
            llm_id=llm_id,
            status="running",
            primary_material_id=material_id,
        )
        newer_batch_id = self._insert_batch_task_with_material(
            identity_id=identity_id,
            llm_id=llm_id,
            status="running",
            primary_material_id=material_id,
        )
        requested_task_id = self._insert_email_task_with_material(
            identity_id=identity_id,
            llm_id=llm_id,
            professor_id=professor_id,
            status="review_required",
            primary_material_id=material_id,
            selected_material_ids=[],
            batch_task_id=requested_batch_id,
            source="batch",
            generated_subject="待操作旧任务",
            generated_content_text="待操作旧正文",
            generated_content_html="<p>待操作旧正文</p>",
        )
        newer_task_id = self._insert_email_task_with_material(
            identity_id=identity_id,
            llm_id=llm_id,
            professor_id=professor_id,
            status="review_required",
            primary_material_id=material_id,
            selected_material_ids=[],
            batch_task_id=newer_batch_id,
            source="batch",
            generated_subject="不应返回的新任务",
            generated_content_text="不应修改的新正文",
            generated_content_html="<p>不应修改的新正文</p>",
        )

        save_response = self.client.post(
            f"/api/email-tasks/{requested_task_id}/save-draft",
            json={
                "subject": "明确保存到旧任务",
                "body_text": "明确保存到旧正文",
                "body_html": "<p>明确保存到旧正文</p>",
                "selected_material_ids": [],
            },
        )

        self.assertEqual(save_response.status_code, 200, msg=save_response.text)
        saved_task = save_response.json()["current_task"]
        self.assertEqual(saved_task["id"], requested_task_id)
        self.assertEqual(saved_task["approved_subject"], "明确保存到旧任务")

        with patch(
            "app.modules.matching.task_analysis.llm_runtime.generate_match_evaluation",
            AsyncMock(return_value=self._build_match_evaluation_result(match_score=81)),
        ):
            match_response = self.client.post(
                f"/api/email-tasks/{requested_task_id}/calculate-match",
            )

        self.assertEqual(match_response.status_code, 200, msg=match_response.text)
        matched_task = match_response.json()["thread"]["current_task"]
        self.assertEqual(matched_task["id"], requested_task_id)
        self.assertEqual(matched_task["match_score"], 81)

        newer_thread = self.client.get(f"/api/email-tasks/{newer_task_id}/thread")
        self.assertEqual(newer_thread.status_code, 200, msg=newer_thread.text)
        newer_task = newer_thread.json()["current_task"]
        self.assertEqual(newer_task["id"], newer_task_id)
        self.assertEqual(newer_task["generated_subject"], "不应返回的新任务")
        self.assertIsNone(newer_task["approved_subject"])

    def test_stop_batch_task_marks_pending_items_as_canceled(self) -> None:
        identity_id = self._create_identity(with_imap=False)
        llm_id = self._create_llm()

        self.client.post("/api/professors/import-sample")
        professor_ids = [
            item["id"] for item in self.client.get("/api/professors").json()[:3]
        ]

        create_response = self.client.post(
            "/api/batch-tasks",
            json={
                "identity_id": identity_id,
                "llm_profile_id": llm_id,
                "name": "停止后取消未完成任务",
                "professor_ids": professor_ids,
                "schedule_type": "immediate",
                "window_start_time": None,
                "window_end_time": None,
                "emails_per_window": None,
                "primary_material_id": None,
                "email_subject": "联系 {{name}}",
                "email_body": "老师您好，我是{{sender_name}}。",
                "selected_material_ids": None,
            },
        )
        self.assertEqual(create_response.status_code, 201, msg=create_response.text)
        batch_task_id = create_response.json()["id"]

        connection = sqlite3.connect(self.db_path)
        try:
            task_ids = [
                row[0]
                for row in connection.execute(
                    """
                    SELECT id
                    FROM email_tasks
                    WHERE batch_task_id = ?
                    ORDER BY id
                    """,
                    (batch_task_id,),
                ).fetchall()
            ]
            self.assertEqual(len(task_ids), 3)
            connection.execute(
                """
                UPDATE email_tasks
                SET status = 'generating_draft',
                    draft_generation_previous_status = 'matched',
                    draft_claim_id = 'stop-claim',
                    draft_claimed_at = CURRENT_TIMESTAMP,
                    draft_lease_expires_at = datetime(CURRENT_TIMESTAMP, '+90 seconds')
                WHERE id = ?
                """,
                (task_ids[0],),
            )
            connection.execute(
                "UPDATE email_tasks SET status = ? WHERE id = ?",
                ("review_required", task_ids[1]),
            )
            connection.execute(
                """
                UPDATE email_tasks
                SET status = ?, sent_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                ("sent", task_ids[2]),
            )
            connection.commit()
        finally:
            connection.close()

        stop_response = self.client.post(f"/api/batch-tasks/{batch_task_id}/stop")
        self.assertEqual(stop_response.status_code, 200, msg=stop_response.text)

        items_response = self.client.get(f"/api/batch-tasks/{batch_task_id}/items")
        self.assertEqual(items_response.status_code, 200, msg=items_response.text)
        self.assertEqual(
            [
                (item["id"], item["status"], item["cancellation_reason"])
                for item in items_response.json()
            ],
            [
                (task_ids[0], "canceled", "batch_stopped"),
                (task_ids[1], "canceled", "batch_stopped"),
                (task_ids[2], "sent", None),
            ],
        )

        connection = sqlite3.connect(self.db_path)
        try:
            rows = connection.execute(
                """
                SELECT id, status, cancellation_reason,
                       draft_claim_id, draft_claimed_at, draft_lease_expires_at
                FROM email_tasks
                WHERE batch_task_id = ?
                ORDER BY id
                """,
                (batch_task_id,),
            ).fetchall()
        finally:
            connection.close()

        self.assertEqual(
            rows,
            [
                (task_ids[0], "canceled", "batch_stopped", None, None, None),
                (task_ids[1], "canceled", "batch_stopped", None, None, None),
                (task_ids[2], "sent", None, None, None, None),
            ],
        )

    def test_stop_batch_task_keeps_send_failed_items_failed(self) -> None:
        identity_id = self._create_identity(with_imap=False)
        llm_id = self._create_llm()

        self.client.post("/api/professors/import-sample")
        professor_ids = [
            item["id"] for item in self.client.get("/api/professors").json()[:2]
        ]

        create_response = self.client.post(
            "/api/batch-tasks",
            json={
                "identity_id": identity_id,
                "llm_profile_id": llm_id,
                "name": "停止时保留失败任务",
                "professor_ids": professor_ids,
                "schedule_type": "immediate",
                "window_start_time": None,
                "window_end_time": None,
                "emails_per_window": None,
                "primary_material_id": None,
                "email_subject": "联系 {{name}}",
                "email_body": "老师您好，我是{{sender_name}}。",
                "selected_material_ids": None,
            },
        )
        self.assertEqual(create_response.status_code, 201, msg=create_response.text)
        batch_task_id = create_response.json()["id"]

        connection = sqlite3.connect(self.db_path)
        try:
            task_ids = [
                row[0]
                for row in connection.execute(
                    """
                    SELECT id
                    FROM email_tasks
                    WHERE batch_task_id = ?
                    ORDER BY id
                    """,
                    (batch_task_id,),
                ).fetchall()
            ]
            self.assertEqual(len(task_ids), 2)
            connection.execute(
                """
                UPDATE email_tasks
                SET status = ?, last_error = ?
                WHERE id = ?
                """,
                ("send_failed", "smtp timeout", task_ids[0]),
            )
            connection.execute(
                "UPDATE email_tasks SET status = ? WHERE id = ?",
                ("matched", task_ids[1]),
            )
            connection.commit()
        finally:
            connection.close()

        stop_response = self.client.post(f"/api/batch-tasks/{batch_task_id}/stop")
        self.assertEqual(stop_response.status_code, 200, msg=stop_response.text)

        connection = sqlite3.connect(self.db_path)
        try:
            rows = connection.execute(
                """
                SELECT id, status, cancellation_reason, last_error
                FROM email_tasks
                WHERE batch_task_id = ?
                ORDER BY id
                """,
                (batch_task_id,),
            ).fetchall()
        finally:
            connection.close()

        self.assertEqual(
            rows,
            [
                (task_ids[0], "send_failed", None, "smtp timeout"),
                (task_ids[1], "canceled", "batch_stopped", None),
            ],
        )

    def test_continue_manually_creates_manual_child_task_from_batch_stopped_task(
        self,
    ) -> None:
        identity_id = self._create_identity(with_imap=False)
        llm_id = self._create_llm()
        primary_material_id = self._upload_material(
            identity_id,
            filename="resume.txt",
            content=b"My background covers agent systems and information extraction.",
            material_type="resume",
        )
        attachment_material_id = self._upload_material(
            identity_id,
            filename="paper.pdf",
            content=b"%PDF-1.4 test attachment",
            material_type="portfolio",
        )
        set_primary_response = self.client.post(
            f"/api/materials/{primary_material_id}/set-primary"
        )
        self.assertEqual(
            set_primary_response.status_code, 200, msg=set_primary_response.text
        )

        professor_response = self.client.post(
            "/api/professors",
            json={
                "name": "手动继续联系导师",
                "email": "continue-manually@example.edu",
                "title": "Professor",
                "university": "Example University",
                "school": "School of Computing",
                "department": "Computer Science",
                "research_direction": "Agent systems",
                "recent_papers": [],
                "profile_url": None,
                "source_url": None,
            },
        )
        self.assertEqual(
            professor_response.status_code, 201, msg=professor_response.text
        )
        professor_id = professor_response.json()["id"]

        create_response = self.client.post(
            "/api/batch-tasks",
            json={
                "identity_id": identity_id,
                "llm_profile_id": llm_id,
                "name": "继续联系批量任务",
                "professor_ids": [professor_id],
                "schedule_type": "immediate",
                "window_start_time": None,
                "window_end_time": None,
                "emails_per_window": None,
                "primary_material_id": primary_material_id,
                "email_subject": None,
                "email_body": None,
                "selected_material_ids": [attachment_material_id],
                "outreach_generation_mode": "template",
                "outreach_template_subject": "继续联系 {{name}}",
                "outreach_template_body_text": "继续联系正文 {{name}}",
                "outreach_template_body_html": "<p>继续联系正文 {{name}}</p>",
            },
        )
        self.assertEqual(create_response.status_code, 201, msg=create_response.text)
        batch_task_id = create_response.json()["id"]

        connection = sqlite3.connect(self.db_path)
        try:
            parent_task_id = connection.execute(
                """
                SELECT id
                FROM email_tasks
                WHERE batch_task_id = ?
                """,
                (batch_task_id,),
            ).fetchone()[0]
            connection.execute(
                """
                UPDATE email_tasks
                SET status = ?,
                    cancellation_reason = ?,
                    match_score = ?,
                    match_reason = ?,
                    fit_points = ?,
                    risk_points = ?,
                    match_keywords = ?,
                    generated_subject = ?,
                    generated_content_text = ?,
                    generated_content_html = ?,
                    selected_material_ids = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (
                    "canceled",
                    "batch_stopped",
                    91,
                    "研究方向与材料高度匹配",
                    json.dumps(["研究方向契合"]),
                    json.dumps(["需要补充近期成果"]),
                    json.dumps(["agent"]),
                    "旧草稿主题",
                    "旧草稿正文",
                    "<p>旧草稿正文</p>",
                    json.dumps([attachment_material_id]),
                    parent_task_id,
                ),
            )
            connection.commit()
        finally:
            connection.close()

        workspace_before = self.client.get(
            f"/api/workspaces/{professor_id}",
            params={"identity_id": identity_id, "llm_profile_id": llm_id},
        )
        self.assertEqual(workspace_before.status_code, 200, msg=workspace_before.text)
        before_task = workspace_before.json()["current_task"]
        self.assertIsNone(before_task["id"])
        self.assertFalse(before_task["can_write_follow_up"])

        response = self.client.post(
            f"/api/email-tasks/{parent_task_id}/continue-manually"
        )

        self.assertEqual(response.status_code, 200, msg=response.text)
        payload = response.json()
        current_task = payload["current_task"]
        self.assertNotEqual(current_task["id"], parent_task_id)
        self.assertIsNone(current_task["batch_task_id"])
        self.assertEqual(current_task["source"], "manual")
        self.assertEqual(current_task["parent_task_id"], parent_task_id)
        self.assertEqual(current_task["status"], "review_required")
        self.assertIsNone(current_task["cancellation_reason"])
        self.assertEqual(current_task["primary_material_id"], primary_material_id)
        self.assertEqual(
            current_task["selected_material_ids"], [attachment_material_id]
        )
        self.assertEqual(current_task["match_score"], 91)
        self.assertEqual(current_task["match_reason"], "研究方向与材料高度匹配")
        self.assertEqual(current_task["fit_points"], ["研究方向契合"])
        self.assertEqual(current_task["risk_points"], ["需要补充近期成果"])
        self.assertEqual(current_task["match_keywords"], ["agent"])
        self.assertEqual(current_task["generated_subject"], "旧草稿主题")
        self.assertEqual(current_task["generated_content_text"], "旧草稿正文")
        self.assertEqual(current_task["generated_content_html"], "<p>旧草稿正文</p>")
        self.assertEqual(current_task["outreach_generation_mode"], "template")
        self.assertEqual(current_task["outreach_template_subject"], "继续联系 {{name}}")
        self.assertEqual(
            current_task["outreach_template_body_text"], "继续联系正文 {{name}}"
        )
        self.assertEqual(
            current_task["outreach_template_body_html"], "<p>继续联系正文 {{name}}</p>"
        )
        self.assertFalse(current_task["can_continue_manually"])
        self.assertFalse(current_task["can_write_follow_up"])

        connection = sqlite3.connect(self.db_path)
        try:
            rows = connection.execute(
                """
                SELECT id, source, batch_task_id, parent_task_id, status, cancellation_reason,
                       generated_subject, generated_content_text, selected_material_ids
                FROM email_tasks
                WHERE professor_id = ?
                ORDER BY id
                """,
                (professor_id,),
            ).fetchall()
        finally:
            connection.close()

        self.assertEqual(
            rows,
            [
                (
                    parent_task_id,
                    "batch",
                    batch_task_id,
                    None,
                    "canceled",
                    "batch_stopped",
                    "旧草稿主题",
                    "旧草稿正文",
                    json.dumps([attachment_material_id]),
                ),
                (
                    current_task["id"],
                    "manual",
                    None,
                    parent_task_id,
                    "review_required",
                    None,
                    "旧草稿主题",
                    "旧草稿正文",
                    json.dumps([attachment_material_id]),
                ),
            ],
        )

    def test_continue_manually_rejects_task_without_canceled_batch_stopped_guard(
        self,
    ) -> None:
        identity_id = self._create_identity(with_imap=False)
        llm_id = self._create_llm()

        professor_response = self.client.post(
            "/api/professors",
            json={
                "name": "继续联系非法状态导师",
                "email": "continue-guard@example.edu",
                "title": "Professor",
                "university": "Example University",
                "school": "School of Computing",
                "department": "Computer Science",
                "research_direction": "Agent systems",
                "recent_papers": [],
                "profile_url": None,
                "source_url": None,
            },
        )
        self.assertEqual(
            professor_response.status_code, 201, msg=professor_response.text
        )
        professor_id = professor_response.json()["id"]

        ensure_response = self.client.post(
            f"/api/workspaces/{professor_id}/ensure-task",
            params={"identity_id": identity_id, "llm_profile_id": llm_id},
        )
        self.assertEqual(ensure_response.status_code, 200, msg=ensure_response.text)
        task_id = ensure_response.json()["current_task"]["id"]

        cases = [
            ("matched", None),
            ("canceled", None),
        ]
        for status_value, cancellation_reason in cases:
            with self.subTest(
                status=status_value, cancellation_reason=cancellation_reason
            ):
                connection = sqlite3.connect(self.db_path)
                try:
                    connection.execute(
                        """
                        UPDATE email_tasks
                        SET status = ?, cancellation_reason = ?
                        WHERE id = ?
                        """,
                        (status_value, cancellation_reason, task_id),
                    )
                    connection.commit()
                finally:
                    connection.close()

                response = self.client.post(
                    f"/api/email-tasks/{task_id}/continue-manually"
                )

                self.assertEqual(response.status_code, 400, msg=response.text)

    def test_approve_and_send_rejects_canceled_batch_stopped_parent_task(self) -> None:
        task_id = self._create_canceled_batch_stopped_parent_task(
            email="approve-send-guard@example.edu",
        )

        with patch(
            "app.modules.workspace.tasks.delivery.mail_runtime.send_email",
            AsyncMock(
                return_value=self._build_send_result(
                    message_id="<guarded-send@example.com>",
                    provider_payload={"smtp_host": "smtp.example.com"},
                ),
            ),
        ) as mocked_send:
            response = self.client.post(
                f"/api/email-tasks/{task_id}/approve-and-send",
                json={
                    "subject": "直接发送",
                    "body_text": "老师您好，这里尝试直接发送。",
                    "body_html": None,
                    "selected_material_ids": [],
                },
            )

        self.assertEqual(response.status_code, 400, msg=response.text)
        self.assertEqual(
            response.json()["detail"],
            "该任务已因批量任务停止而取消，请先“作为单独联系继续”后再执行此操作",
        )
        mocked_send.assert_not_awaited()

    def test_approve_and_schedule_rejects_canceled_batch_stopped_parent_task(
        self,
    ) -> None:
        task_id = self._create_canceled_batch_stopped_parent_task(
            email="approve-schedule-guard@example.edu",
        )

        response = self.client.post(
            f"/api/email-tasks/{task_id}/approve-and-schedule",
            json={
                "subject": "稍后发送",
                "body_text": "老师您好，这里尝试直接定时发送。",
                "body_html": None,
                "selected_material_ids": [],
                "scheduled_at": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
            },
        )

        self.assertEqual(response.status_code, 400, msg=response.text)
        self.assertEqual(
            response.json()["detail"],
            "该任务已因批量任务停止而取消，请先“作为单独联系继续”后再执行此操作",
        )

    def test_batch_task_without_default_material_can_still_send_manually(self) -> None:
        identity_id = self._create_identity(with_imap=False)
        llm_id = self._create_llm()

        self.client.post("/api/professors/import-sample")
        professor_id = self.client.get("/api/professors").json()[0]["id"]

        response = self.client.post(
            "/api/batch-tasks",
            json={
                "identity_id": identity_id,
                "llm_profile_id": llm_id,
                "name": "无默认材料也可创建",
                "professor_ids": [professor_id],
                "schedule_type": "immediate",
                "window_start_time": None,
                "window_end_time": None,
                "emails_per_window": None,
                "primary_material_id": None,
                "email_subject": "申请与{{name}}老师交流",
                "email_body": "老师您好，我是{{sender_name}}，希望与您进一步交流。",
                "selected_material_ids": None,
            },
        )

        self.assertEqual(response.status_code, 201)

        batch_task_id = response.json()["id"]
        task_id = self.client.get(f"/api/batch-tasks/{batch_task_id}/items").json()[0][
            "id"
        ]
        task_thread = self.client.get(
            f"/api/batch-tasks/{batch_task_id}/items/{task_id}/thread"
        )
        self.assertIsNone(task_thread.json()["current_task"]["primary_material_id"])

        regenerate_response = self.client.post(
            f"/api/email-tasks/{task_id}/generate-draft"
        )
        self.assertEqual(regenerate_response.status_code, 400)
        self.assertEqual(
            regenerate_response.json()["detail"], "请选择 AI 写信参考材料后再生成草稿"
        )

        with patch(
            "app.modules.workspace.tasks.delivery.mail_runtime.send_email",
            AsyncMock(
                return_value=self._build_send_result(
                    message_id="<manual-no-primary@example.com>",
                    provider_payload={"smtp_host": "smtp.example.com"},
                ),
            ),
        ):
            send_response = self.client.post(
                f"/api/email-tasks/{task_id}/approve-and-send",
                json={
                    "subject": "手动邮件",
                    "body_text": "老师您好，这是一封手动编写的邮件。",
                    "body_html": None,
                    "selected_material_ids": [],
                },
            )
        self.assertEqual(send_response.status_code, 200)
        sent_thread = self.client.get(
            f"/api/batch-tasks/{batch_task_id}/items/{task_id}/thread"
        ).json()
        self.assertEqual(sent_thread["current_task"]["status"], "sent")

    def test_batch_task_generation_requires_selected_template_to_be_complete(
        self,
    ) -> None:
        identity_id = self._create_identity(with_imap=False)
        llm_id = self._create_llm()
        material_id = self._upload_material(
            identity_id,
            filename="resume.txt",
            content=b"My background covers information extraction.",
            material_type="resume",
        )
        template_response = self.client.post(
            "/api/outreach-templates",
            json={
                "name": "待补充的批量模板",
                "recommended_generation_mode": "llm",
                "subject": None,
                "body_text": None,
                "body_html": None,
            },
        )
        self.assertEqual(template_response.status_code, 201, msg=template_response.text)
        template_id = template_response.json()["id"]
        self.client.post("/api/professors/import-sample")
        professor_id = self.client.get("/api/professors").json()[0]["id"]

        response = self.client.post(
            "/api/batch-tasks",
            json={
                "identity_id": identity_id,
                "llm_profile_id": llm_id,
                "name": "模板润色缺模板",
                "professor_ids": [professor_id],
                "schedule_type": "immediate",
                "window_start_time": None,
                "window_end_time": None,
                "emails_per_window": None,
                "primary_material_id": material_id,
                "email_subject": None,
                "email_body": None,
                "selected_material_ids": None,
                "outreach_generation_mode": "llm",
                "outreach_template_id": template_id,
                "outreach_template_subject": None,
                "outreach_template_body_text": None,
                "outreach_template_body_html": None,
            },
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            response.json()["detail"], "请先填写默认套磁信主题和纯文本正文"
        )

    def test_batch_task_resend_context_selects_unsuccessful_items(self) -> None:
        identity_id = self._create_identity(with_imap=False)
        llm_id = self._create_llm()
        professor_ids = [
            self._create_professor(email="expired-resend@example.edu"),
            self._create_professor(email="stopped-resend@example.edu"),
            self._create_professor(email="failed-resend@example.edu"),
            self._create_professor(email="sent-resend@example.edu"),
            self._create_professor(email="removed-resend@example.edu"),
        ]
        batch_task_id = self._insert_batch_task_with_material(
            identity_id=identity_id,
            llm_id=llm_id,
            status="expired",
            primary_material_id=None,
        )
        rows = [
            (professor_ids[0], "canceled", "schedule_expired"),
            (professor_ids[1], "canceled", "batch_stopped"),
            (professor_ids[2], "send_failed", None),
            (professor_ids[3], "sent", None),
            (professor_ids[4], "canceled", "user_removed"),
        ]
        task_ids: list[int] = []
        for professor_id, task_status, cancellation_reason in rows:
            task_ids.append(
                self._insert_email_task_with_material(
                    identity_id=identity_id,
                    llm_id=llm_id,
                    professor_id=professor_id,
                    status=task_status,
                    primary_material_id=None,
                    batch_task_id=batch_task_id,
                    source="batch",
                    outreach_generation_mode="llm",
                ),
            )
            connection = sqlite3.connect(self.db_path)
            try:
                connection.execute(
                    """
                    UPDATE email_tasks
                    SET cancellation_reason = ?,
                        outreach_template_subject = ?,
                        outreach_template_body_text = ?,
                        outreach_template_body_html = ?
                    WHERE id = ?
                    """,
                    (
                        cancellation_reason,
                        "原主题 {{name}}",
                        "原正文 {{sender_name}}",
                        "<p>原正文 {{sender_name}}</p>",
                        task_ids[-1],
                    ),
                )
                connection.commit()
            finally:
                connection.close()

        response = self.client.get(f"/api/batch-tasks/{batch_task_id}/resend-context")

        self.assertEqual(response.status_code, 200, msg=response.text)
        payload = response.json()
        self.assertEqual(payload["task"]["identity_id"], identity_id)
        self.assertEqual(payload["defaults"]["identity_id"], identity_id)
        self.assertEqual(
            payload["defaults"]["outreach_template_subject"], "原主题 {{name}}"
        )
        self.assertNotIn("llm_profile_id", payload["defaults"])
        self.assertNotIn("scheduled_dates", payload["defaults"])
        selectable_items = [item for item in payload["items"] if item["selectable"]]
        self.assertEqual(
            [item["professor_id"] for item in selectable_items], professor_ids[:3]
        )
        self.assertEqual(
            [item["reason_label"] for item in selectable_items],
            ["发送窗口已过期", "任务中止后未发送", "发送失败"],
        )
        self.assertTrue(all(item["default_selected"] for item in selectable_items))
        self.assertEqual(payload["summary"]["candidate_count"], 3)
        self.assertEqual(payload["summary"]["default_selected_count"], 3)

    def test_batch_resend_creation_reuses_each_items_best_available_content(
        self,
    ) -> None:
        identity_id = self._create_identity(with_imap=False)
        llm_id = self._create_llm()
        approved_professor_id = self._create_professor(
            email="approved-resend@example.edu"
        )
        generated_professor_id = self._create_professor(
            email="generated-resend@example.edu"
        )
        saved_professor_id = self._create_professor(email="saved-resend@example.edu")
        regenerate_professor_id = self._create_professor(
            email="regenerate-resend@example.edu"
        )
        source_batch_task_id = self._insert_batch_task_with_material(
            identity_id=identity_id,
            llm_id=llm_id,
            status="expired",
            primary_material_id=None,
        )
        self._insert_email_task_with_material(
            identity_id=identity_id,
            llm_id=llm_id,
            professor_id=saved_professor_id,
            status="review_required",
            primary_material_id=None,
            batch_task_id=source_batch_task_id,
            source="batch",
            approved_subject="用户保存主题",
            approved_body_text="用户保存正文",
            approved_body_html="<p>用户保存正文</p>",
            outreach_generation_mode="llm",
        )
        self._insert_email_task_with_material(
            identity_id=identity_id,
            llm_id=llm_id,
            professor_id=approved_professor_id,
            status="send_failed",
            primary_material_id=None,
            batch_task_id=source_batch_task_id,
            source="batch",
            generated_subject="AI 原主题",
            generated_content_text="AI 原正文",
            generated_content_html="<p>AI 原正文</p>",
            approved_subject="用户最终主题",
            approved_body_text="用户最终正文",
            approved_body_html="<p>用户最终正文</p>",
            outreach_generation_mode="llm",
        )
        self._insert_email_task_with_material(
            identity_id=identity_id,
            llm_id=llm_id,
            professor_id=generated_professor_id,
            status="review_required",
            primary_material_id=None,
            batch_task_id=source_batch_task_id,
            source="batch",
            generated_subject="待审核 AI 主题",
            generated_content_text="待审核 AI 正文",
            generated_content_html="<p>待审核 AI 正文</p>",
            outreach_generation_mode="llm",
        )
        self._insert_email_task_with_material(
            identity_id=identity_id,
            llm_id=llm_id,
            professor_id=regenerate_professor_id,
            status="draft_failed",
            primary_material_id=None,
            batch_task_id=source_batch_task_id,
            source="batch",
            outreach_generation_mode="llm",
        )

        context = self.client.get(
            f"/api/batch-tasks/{source_batch_task_id}/resend-context"
        )
        self.assertEqual(context.status_code, 200, msg=context.text)
        reuse_kinds = {
            item["professor_id"]: item["content_reuse_kind"]
            for item in context.json()["items"]
        }
        self.assertEqual(reuse_kinds[approved_professor_id], "approved")
        self.assertEqual(reuse_kinds[generated_professor_id], "generated")
        self.assertEqual(reuse_kinds[saved_professor_id], "approved")
        self.assertEqual(reuse_kinds[regenerate_professor_id], "regenerate")
        saved_context_item = next(
            item
            for item in context.json()["items"]
            if item["professor_id"] == saved_professor_id
        )
        self.assertTrue(saved_context_item["content_requires_review"])

        response = self.client.post(
            "/api/batch-tasks",
            json={
                "identity_id": identity_id,
                "llm_profile_id": llm_id,
                "name": "优先复用内容的重发任务",
                "professor_ids": [
                    approved_professor_id,
                    generated_professor_id,
                    saved_professor_id,
                    regenerate_professor_id,
                ],
                "schedule_type": "immediate",
                "selected_material_ids": [],
                "outreach_template_id": None,
                "outreach_generation_mode": "llm",
                "outreach_template_subject": "仅供缺失草稿使用的主题 {{name}}",
                "outreach_template_body_text": "仅供缺失草稿使用的正文",
                "outreach_template_body_html": "<p>仅供缺失草稿使用的正文</p>",
                "resend_source_batch_task_id": source_batch_task_id,
            },
        )

        self.assertEqual(response.status_code, 201, msg=response.text)
        connection = sqlite3.connect(self.db_path)
        try:
            rows = connection.execute(
                """
                SELECT professor_id, status,
                       generated_subject, generated_content_text, generated_content_html,
                       approved_subject, approved_body_text, approved_body_html,
                       last_error, retry_count
                FROM email_tasks
                WHERE batch_task_id = ?
                """,
                (response.json()["id"],),
            ).fetchall()
        finally:
            connection.close()
        state_by_professor = {row[0]: row[1:] for row in rows}
        self.assertEqual(
            state_by_professor[approved_professor_id],
            (
                "approved",
                "AI 原主题",
                "AI 原正文",
                "<p>AI 原正文</p>",
                "用户最终主题",
                "用户最终正文",
                "<p>用户最终正文</p>",
                None,
                0,
            ),
        )
        self.assertEqual(
            state_by_professor[generated_professor_id],
            (
                "review_required",
                "待审核 AI 主题",
                "待审核 AI 正文",
                "<p>待审核 AI 正文</p>",
                None,
                None,
                None,
                None,
                0,
            ),
        )
        self.assertEqual(
            state_by_professor[saved_professor_id],
            (
                "review_required",
                "用户保存主题",
                "用户保存正文",
                "<p>用户保存正文</p>",
                "用户保存主题",
                "用户保存正文",
                "<p>用户保存正文</p>",
                None,
                0,
            ),
        )
        self.assertEqual(
            state_by_professor[regenerate_professor_id],
            ("discovered", None, None, None, None, None, None, None, 0),
        )

    def test_batch_resend_template_strategy_replaces_old_ai_content_without_review(
        self,
    ) -> None:
        identity_id = self._create_identity(with_imap=False)
        llm_id = self._create_llm()
        professor_id = self._create_professor(email="template-resend@example.edu")
        source_batch_task_id = self._insert_batch_task_with_material(
            identity_id=identity_id,
            llm_id=llm_id,
            status="expired",
            primary_material_id=None,
        )
        source_task_id = self._insert_email_task_with_material(
            identity_id=identity_id,
            llm_id=llm_id,
            professor_id=professor_id,
            status="canceled",
            primary_material_id=None,
            batch_task_id=source_batch_task_id,
            source="batch",
            generated_subject="不应沿用的 AI 主题",
            generated_content_text="不应沿用的 AI 正文",
            generated_content_html="<p>不应沿用的 AI 正文</p>",
            approved_subject="不应沿用的批准主题",
            approved_body_text="不应沿用的批准正文",
            approved_body_html="<p>不应沿用的批准正文</p>",
            match_score=91,
            match_reason="原任务匹配依据",
            outreach_generation_mode="llm",
        )
        connection = sqlite3.connect(self.db_path)
        try:
            connection.execute(
                "UPDATE email_tasks SET cancellation_reason = 'schedule_expired' WHERE id = ?",
                (source_task_id,),
            )
            connection.commit()
        finally:
            connection.close()

        response = self.client.post(
            "/api/batch-tasks",
            json={
                "identity_id": identity_id,
                "llm_profile_id": llm_id,
                "name": "重新套用模板",
                "professor_ids": [professor_id],
                "schedule_type": "immediate",
                "primary_material_id": None,
                "selected_material_ids": [],
                "outreach_template_id": None,
                "outreach_generation_mode": "llm",
                "outreach_template_subject": "新模板 {{name}}",
                "outreach_template_body_text": "新模板正文 {{name}}",
                "outreach_template_body_html": "<p>新模板正文 {{name}}</p>",
                "resend_source_batch_task_id": source_batch_task_id,
                "resend_content_strategy": "template",
            },
        )

        self.assertEqual(response.status_code, 201, msg=response.text)
        self.assertEqual(response.json()["outreach_generation_mode"], "template")
        self.assertEqual(response.json()["review_required_count"], 0)
        self.assertEqual(response.json()["approved_count"], 1)
        connection = sqlite3.connect(self.db_path)
        try:
            row = connection.execute(
                """
                SELECT status, generated_subject, generated_content_text,
                       approved_subject, approved_body_text, approved_at,
                       draft_generation_source, match_score, match_reason
                FROM email_tasks
                WHERE batch_task_id = ?
                """,
                (response.json()["id"],),
            ).fetchone()
        finally:
            connection.close()
        self.assertEqual(row[0], "approved")
        self.assertEqual(row[1], "新模板 材料删除测试导师")
        self.assertEqual(row[2], "新模板正文 材料删除测试导师")
        self.assertEqual(row[3], row[1])
        self.assertEqual(row[4], row[2])
        self.assertIsNotNone(row[5])
        self.assertEqual(row[6], "template")
        self.assertEqual(row[7], 91)
        self.assertEqual(row[8], "原任务匹配依据")
        self.assertNotIn("不应沿用", " ".join(str(value or "") for value in row))

    def test_batch_resend_template_strategy_schedules_archived_template_snapshot(
        self,
    ) -> None:
        identity_id = self._create_identity(with_imap=False)
        llm_id = self._create_llm()
        professor_id = self._create_professor(
            email="archived-template-resend@example.edu"
        )
        template_response = self.client.post(
            "/api/outreach-templates",
            json={
                "name": "已归档历史模板",
                "recommended_generation_mode": "template",
                "subject": "归档主题 {{name}}",
                "body_text": "归档正文 {{sender_name}}",
                "body_html": "<p>归档正文 {{sender_name}}</p>",
            },
        )
        self.assertEqual(template_response.status_code, 201, msg=template_response.text)
        template_id = template_response.json()["id"]
        archive_response = self.client.delete(f"/api/outreach-templates/{template_id}")
        self.assertEqual(archive_response.status_code, 200, msg=archive_response.text)

        source_batch_task_id = self._insert_batch_task_with_material(
            identity_id=identity_id,
            llm_id=llm_id,
            status="expired",
            primary_material_id=None,
        )
        source_task_id = self._insert_email_task_with_material(
            identity_id=identity_id,
            llm_id=llm_id,
            professor_id=professor_id,
            status="canceled",
            primary_material_id=None,
            batch_task_id=source_batch_task_id,
            source="batch",
            outreach_generation_mode="template",
        )
        connection = sqlite3.connect(self.db_path)
        try:
            connection.execute(
                "UPDATE email_tasks SET cancellation_reason = 'schedule_expired' WHERE id = ?",
                (source_task_id,),
            )
            connection.commit()
        finally:
            connection.close()

        scheduled_date = (datetime.now(UTC) + timedelta(days=7)).date().isoformat()
        response = self.client.post(
            "/api/batch-tasks",
            json={
                "identity_id": identity_id,
                "llm_profile_id": llm_id,
                "name": "归档模板定时重发",
                "professor_ids": [professor_id],
                "schedule_type": "scheduled",
                "scheduled_dates": [scheduled_date],
                "window_start_time": "09:00",
                "window_end_time": "18:00",
                "emails_per_window": 5,
                "primary_material_id": None,
                "selected_material_ids": [],
                "outreach_template_id": template_id,
                "outreach_generation_mode": "template",
                "outreach_template_subject": "归档主题 {{name}}",
                "outreach_template_body_text": "归档正文 {{sender_name}}",
                "outreach_template_body_html": "<p>归档正文 {{sender_name}}</p>",
                "resend_source_batch_task_id": source_batch_task_id,
                "resend_content_strategy": "template",
            },
        )

        self.assertEqual(response.status_code, 201, msg=response.text)
        self.assertEqual(response.json()["outreach_template_id"], template_id)
        self.assertEqual(
            response.json()["outreach_template_name_snapshot"],
            "已归档历史模板",
        )
        self.assertEqual(response.json()["review_required_count"], 0)
        self.assertEqual(response.json()["approved_count"], 0)
        self.assertEqual(response.json()["scheduled_count"], 1)
        connection = sqlite3.connect(self.db_path)
        try:
            row = connection.execute(
                """
                SELECT status, scheduled_at, approved_at, outreach_template_id,
                       generated_subject
                FROM email_tasks
                WHERE batch_task_id = ?
                """,
                (response.json()["id"],),
            ).fetchone()
        finally:
            connection.close()
        self.assertEqual(row[0], "scheduled")
        self.assertIsNotNone(row[1])
        self.assertIsNotNone(row[2])
        self.assertEqual(row[3], template_id)
        self.assertEqual(row[4], "归档主题 材料删除测试导师")

    def test_batch_resend_llm_strategy_discards_all_old_content(self) -> None:
        identity_id = self._create_identity(with_imap=False)
        llm_id = self._create_llm()
        professor_id = self._create_professor(email="llm-regenerate-resend@example.edu")
        primary_material_id = self._upload_material(
            identity_id,
            filename="llm-resend-resume.txt",
            content=b"new resume",
            material_type="resume",
        )
        source_batch_task_id = self._insert_batch_task_with_material(
            identity_id=identity_id,
            llm_id=llm_id,
            status="expired",
            primary_material_id=None,
        )
        source_task_id = self._insert_email_task_with_material(
            identity_id=identity_id,
            llm_id=llm_id,
            professor_id=professor_id,
            status="canceled",
            primary_material_id=None,
            batch_task_id=source_batch_task_id,
            source="batch",
            generated_subject="旧 AI 主题",
            generated_content_text="旧 AI 正文",
            generated_content_html="<p>旧 AI 正文</p>",
            approved_subject="旧批准主题",
            approved_body_text="旧批准正文",
            approved_body_html="<p>旧批准正文</p>",
            match_score=88,
            match_reason="保留的匹配信息",
            outreach_generation_mode="template",
        )
        connection = sqlite3.connect(self.db_path)
        try:
            connection.execute(
                "UPDATE email_tasks SET cancellation_reason = 'schedule_expired' WHERE id = ?",
                (source_task_id,),
            )
            connection.execute(
                "UPDATE identity_profiles SET current_primary_material_id = NULL WHERE id = ?",
                (identity_id,),
            )
            connection.commit()
        finally:
            connection.close()

        payload = {
            "identity_id": identity_id,
            "llm_profile_id": llm_id,
            "name": "AI 全部重新改写",
            "professor_ids": [professor_id],
            "schedule_type": "immediate",
            "primary_material_id": primary_material_id,
            "selected_material_ids": [],
            "outreach_template_id": None,
            "outreach_generation_mode": "template",
            "outreach_template_subject": "新的 AI 基础主题 {{name}}",
            "outreach_template_body_text": "新的 AI 基础正文",
            "outreach_template_body_html": "<p>新的 AI 基础正文</p>",
            "resend_source_batch_task_id": source_batch_task_id,
            "resend_content_strategy": "llm",
        }
        missing_material = self.client.post(
            "/api/batch-tasks",
            json={**payload, "primary_material_id": None},
        )
        self.assertEqual(missing_material.status_code, 400, msg=missing_material.text)
        self.assertEqual(
            missing_material.json()["detail"],
            "AI 写信参考材料为必选项",
        )

        response = self.client.post("/api/batch-tasks", json=payload)

        self.assertEqual(response.status_code, 201, msg=response.text)
        self.assertEqual(response.json()["outreach_generation_mode"], "llm")
        self.assertEqual(response.json()["pending_generation_count"], 1)
        connection = sqlite3.connect(self.db_path)
        try:
            row = connection.execute(
                """
                SELECT status, primary_material_id, outreach_generation_mode,
                       generated_subject, generated_content_text,
                       approved_subject, approved_body_text, approved_at,
                       match_score, match_reason
                FROM email_tasks
                WHERE batch_task_id = ?
                """,
                (response.json()["id"],),
            ).fetchone()
        finally:
            connection.close()
        self.assertEqual(row[0], "discovered")
        self.assertEqual(row[1], primary_material_id)
        self.assertEqual(row[2], "llm")
        self.assertEqual(row[3:8], (None, None, None, None, None))
        self.assertEqual(row[8], 88)
        self.assertEqual(row[9], "保留的匹配信息")

    def test_batch_resend_legacy_reuse_keeps_expired_auto_approved_template_ready(
        self,
    ) -> None:
        identity_id = self._create_identity(with_imap=False)
        llm_id = self._create_llm()
        professor_id = self._create_professor(email="legacy-template-reuse@example.edu")
        source_batch_task_id = self._insert_batch_task_with_material(
            identity_id=identity_id,
            llm_id=llm_id,
            status="expired",
            primary_material_id=None,
        )
        source_task_id = self._insert_email_task_with_material(
            identity_id=identity_id,
            llm_id=llm_id,
            professor_id=professor_id,
            status="canceled",
            primary_material_id=None,
            batch_task_id=source_batch_task_id,
            source="batch",
            generated_subject="已批准模板主题",
            generated_content_text="已批准模板正文",
            generated_content_html="<p>已批准模板正文</p>",
            approved_subject="已批准模板主题",
            approved_body_text="已批准模板正文",
            approved_body_html="<p>已批准模板正文</p>",
            outreach_generation_mode="template",
        )
        connection = sqlite3.connect(self.db_path)
        try:
            connection.execute(
                """
                UPDATE email_tasks
                SET cancellation_reason = 'schedule_expired',
                    draft_generation_source = 'template'
                WHERE id = ?
                """,
                (source_task_id,),
            )
            connection.commit()
        finally:
            connection.close()

        context = self.client.get(
            f"/api/batch-tasks/{source_batch_task_id}/resend-context",
        )
        self.assertEqual(context.status_code, 200, msg=context.text)
        self.assertFalse(context.json()["items"][0]["content_requires_review"])

        response = self.client.post(
            "/api/batch-tasks",
            json={
                "identity_id": identity_id,
                "llm_profile_id": llm_id,
                "name": "兼容旧客户端沿用模板",
                "professor_ids": [professor_id],
                "schedule_type": "immediate",
                "primary_material_id": None,
                "selected_material_ids": [],
                "outreach_template_id": None,
                "outreach_generation_mode": "template",
                "outreach_template_subject": None,
                "outreach_template_body_text": None,
                "outreach_template_body_html": None,
                "resend_source_batch_task_id": source_batch_task_id,
            },
        )

        self.assertEqual(response.status_code, 201, msg=response.text)
        self.assertEqual(response.json()["review_required_count"], 0)
        self.assertEqual(response.json()["approved_count"], 1)

    def test_batch_resend_content_strategy_requires_source_and_known_value(
        self,
    ) -> None:
        payload = {
            "identity_id": 1,
            "llm_profile_id": 1,
            "name": "非法重发策略",
            "professor_ids": [1],
            "schedule_type": "immediate",
            "scheduled_dates": None,
            "window_start_time": None,
            "window_end_time": None,
            "emails_per_window": None,
            "primary_material_id": None,
            "email_subject": None,
            "email_body": None,
            "selected_material_ids": None,
            "outreach_generation_mode": "template",
            "outreach_template_subject": "主题",
            "outreach_template_body_text": "正文",
            "outreach_template_body_html": "<p>正文</p>",
            "outreach_template_id": None,
            "resend_content_strategy": "template",
        }

        without_source = self.client.post("/api/batch-tasks", json=payload)
        self.assertEqual(without_source.status_code, 400, msg=without_source.text)
        self.assertEqual(
            without_source.json()["detail"],
            "重发内容策略只能用于重新发起任务",
        )

        unknown_strategy = self.client.post(
            "/api/batch-tasks",
            json={**payload, "resend_content_strategy": "unknown"},
        )
        self.assertEqual(unknown_strategy.status_code, 422, msg=unknown_strategy.text)

    def test_batch_resend_creation_rejects_successful_source_item(self) -> None:
        identity_id = self._create_identity(with_imap=False)
        llm_id = self._create_llm()
        professor_id = self._create_professor(email="sent-resend-rejected@example.edu")
        source_batch_task_id = self._insert_batch_task_with_material(
            identity_id=identity_id,
            llm_id=llm_id,
            status="completed",
            primary_material_id=None,
        )
        self._insert_email_task_with_material(
            identity_id=identity_id,
            llm_id=llm_id,
            professor_id=professor_id,
            status="sent",
            primary_material_id=None,
            batch_task_id=source_batch_task_id,
            source="batch",
            approved_subject="已发送主题",
            approved_body_text="已发送正文",
        )

        response = self.client.post(
            "/api/batch-tasks",
            json={
                "identity_id": identity_id,
                "llm_profile_id": llm_id,
                "name": "不应创建的重发任务",
                "professor_ids": [professor_id],
                "schedule_type": "immediate",
                "outreach_template_id": None,
                "outreach_generation_mode": "template",
                "outreach_template_subject": "主题",
                "outreach_template_body_text": "正文",
                "resend_source_batch_task_id": source_batch_task_id,
            },
        )

        self.assertEqual(response.status_code, 400, msg=response.text)
        self.assertIn("已成功触达", response.json()["detail"])

    def test_batch_resend_normalizes_html_requires_review_and_uses_new_materials(
        self,
    ) -> None:
        identity_response = self.client.post(
            "/api/identities",
            json=self._build_identity_payload(with_imap=False),
        )
        self.assertEqual(identity_response.status_code, 201, msg=identity_response.text)
        identity_id = identity_response.json()["id"]
        llm_id = self._create_llm()
        professor_id = self._create_professor(email="safe-resend@example.edu")
        old_primary_id = self._upload_material(
            identity_id,
            filename="old-resume.txt",
            content=b"old resume",
            material_type="resume",
        )
        new_primary_id = self._upload_material(
            identity_id,
            filename="new-resume.txt",
            content=b"new resume",
            material_type="resume",
        )
        old_attachment_id = self._upload_material(
            identity_id,
            filename="old-paper.pdf",
            content=b"old paper",
            material_type="publication",
        )
        new_attachment_id = self._upload_material(
            identity_id,
            filename="new-paper.pdf",
            content=b"new paper",
            material_type="publication",
        )
        source_batch_task_id = self._insert_batch_task_with_material(
            identity_id=identity_id,
            llm_id=llm_id,
            status="expired",
            primary_material_id=old_primary_id,
            selected_material_ids=[old_attachment_id],
        )
        source_task_id = self._insert_email_task_with_material(
            identity_id=identity_id,
            llm_id=llm_id,
            professor_id=professor_id,
            status="canceled",
            primary_material_id=old_primary_id,
            selected_material_ids=[old_attachment_id],
            batch_task_id=source_batch_task_id,
            source="batch",
            approved_subject="历史 HTML 主题",
            approved_body_html="<p>历史 HTML 正文</p>",
            outreach_generation_mode="llm",
        )
        connection = sqlite3.connect(self.db_path)
        try:
            connection.execute(
                """
                UPDATE email_tasks
                SET cancellation_reason = 'schedule_expired',
                    scheduled_at = datetime('now', '-1 day')
                WHERE id = ?
                """,
                (source_task_id,),
            )
            connection.commit()
        finally:
            connection.close()

        context = self.client.get(
            f"/api/batch-tasks/{source_batch_task_id}/resend-context",
        )
        self.assertEqual(context.status_code, 200, msg=context.text)
        self.assertEqual(context.json()["items"][0]["content_reuse_kind"], "approved")
        self.assertTrue(context.json()["items"][0]["content_requires_review"])

        response = self.client.post(
            "/api/batch-tasks",
            json={
                "identity_id": identity_id,
                "llm_profile_id": llm_id,
                "name": "安全复用历史 HTML",
                "professor_ids": [professor_id],
                "schedule_type": "immediate",
                "primary_material_id": new_primary_id,
                "selected_material_ids": [new_attachment_id],
                "outreach_template_id": None,
                "outreach_generation_mode": "llm",
                "outreach_template_subject": None,
                "outreach_template_body_text": None,
                "outreach_template_body_html": None,
                "resend_source_batch_task_id": source_batch_task_id,
            },
        )

        self.assertEqual(response.status_code, 201, msg=response.text)
        connection = sqlite3.connect(self.db_path)
        try:
            row = connection.execute(
                """
                SELECT status, primary_material_id, selected_material_ids,
                       approved_subject, approved_body_text, approved_body_html
                FROM email_tasks
                WHERE batch_task_id = ?
                """,
                (response.json()["id"],),
            ).fetchone()
        finally:
            connection.close()
        self.assertEqual(row[0], "review_required")
        self.assertEqual(row[1], new_primary_id)
        self.assertEqual(json.loads(row[2]), [new_attachment_id])
        self.assertEqual(row[3], "历史 HTML 主题")
        self.assertEqual(row[4], "历史 HTML 正文")
        self.assertEqual(row[5], "<p>历史 HTML 正文</p>")

    def test_batch_resend_requires_fallback_template_when_content_must_regenerate(
        self,
    ) -> None:
        identity_response = self.client.post(
            "/api/identities",
            json=self._build_identity_payload(with_imap=False),
        )
        self.assertEqual(identity_response.status_code, 201, msg=identity_response.text)
        identity_id = identity_response.json()["id"]
        llm_id = self._create_llm()
        professor_id = self._create_professor(email="regenerate-resend@example.edu")
        source_batch_task_id = self._insert_batch_task_with_material(
            identity_id=identity_id,
            llm_id=llm_id,
            status="expired",
            primary_material_id=None,
        )
        self._insert_email_task_with_material(
            identity_id=identity_id,
            llm_id=llm_id,
            professor_id=professor_id,
            status="draft_failed",
            primary_material_id=None,
            batch_task_id=source_batch_task_id,
            source="batch",
            outreach_generation_mode="llm",
        )

        response = self.client.post(
            "/api/batch-tasks",
            json={
                "identity_id": identity_id,
                "llm_profile_id": llm_id,
                "name": "仍需重新生成",
                "professor_ids": [professor_id],
                "schedule_type": "immediate",
                "primary_material_id": None,
                "selected_material_ids": None,
                "outreach_template_id": None,
                "outreach_generation_mode": "llm",
                "outreach_template_subject": None,
                "outreach_template_body_text": None,
                "outreach_template_body_html": None,
                "resend_source_batch_task_id": source_batch_task_id,
            },
        )

        self.assertEqual(response.status_code, 400, msg=response.text)
        self.assertIn("主题和纯文本正文", response.json()["detail"])

    def test_batch_task_resend_context_filters_deleted_material_defaults(self) -> None:
        identity_id = self._create_identity(with_imap=False)
        llm_id = self._create_llm()
        primary_id = self._upload_material(
            identity_id,
            filename="resume.txt",
            content=b"resume",
            material_type="resume",
        )
        attachment_id = self._upload_material(
            identity_id,
            filename="paper.pdf",
            content=b"paper",
            material_type="publication",
        )
        professor_id = self._create_professor(email="material-resend@example.edu")
        batch_task_id = self._insert_batch_task_with_material(
            identity_id=identity_id,
            llm_id=llm_id,
            status="expired",
            primary_material_id=primary_id,
            selected_material_ids=[attachment_id, 999999],
        )
        self._insert_email_task_with_material(
            identity_id=identity_id,
            llm_id=llm_id,
            professor_id=professor_id,
            status="canceled",
            primary_material_id=primary_id,
            batch_task_id=batch_task_id,
            source="batch",
        )
        connection = sqlite3.connect(self.db_path)
        try:
            connection.execute(
                "UPDATE email_tasks SET cancellation_reason = ? WHERE batch_task_id = ?",
                ("schedule_expired", batch_task_id),
            )
            connection.execute(
                "DELETE FROM identity_materials WHERE id = ?", (primary_id,)
            )
            connection.commit()
        finally:
            connection.close()

        response = self.client.get(f"/api/batch-tasks/{batch_task_id}/resend-context")

        self.assertEqual(response.status_code, 200, msg=response.text)
        payload = response.json()
        self.assertIsNone(payload["defaults"]["primary_material_id"])
        self.assertEqual(payload["defaults"]["selected_material_ids"], [attachment_id])
        self.assertTrue(any("材料" in warning for warning in payload["warnings"]))

    def test_batch_task_resend_context_rejects_missing_identity(self) -> None:
        identity_id = self._create_identity(with_imap=False)
        llm_id = self._create_llm()
        batch_task_id = self._insert_batch_task_with_material(
            identity_id=identity_id,
            llm_id=llm_id,
            status="expired",
            primary_material_id=None,
        )
        connection = sqlite3.connect(self.db_path)
        try:
            connection.execute(
                "UPDATE batch_tasks SET identity_id = ? WHERE id = ?",
                (999999, batch_task_id),
            )
            connection.commit()
        finally:
            connection.close()

        response = self.client.get(f"/api/batch-tasks/{batch_task_id}/resend-context")

        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            response.json()["detail"], "原任务身份已不存在，无法直接重新发起。"
        )

    def test_batch_task_card_hides_delivery_mode_snapshot(self) -> None:
        identity_id = self._create_identity(with_imap=False)
        llm_id = self._create_llm()
        self.client.post("/api/professors/import-sample")
        professor_id = self.client.get("/api/professors").json()[0]["id"]

        create_response = self.client.post(
            "/api/batch-tasks",
            json={
                "identity_id": identity_id,
                "llm_profile_id": llm_id,
                "name": "批量列表不再显示模式",
                "professor_ids": [professor_id],
                "schedule_type": "immediate",
                "window_start_time": None,
                "window_end_time": None,
                "emails_per_window": None,
                "primary_material_id": None,
                "email_subject": "申请与{{name}}老师交流",
                "email_body": "老师您好，我是{{sender_name}}。",
                "selected_material_ids": None,
            },
        )

        self.assertEqual(create_response.status_code, 201, msg=create_response.text)

        task_payload = self.client.get(
            "/api/batch-tasks",
            params={"identity_id": identity_id, "llm_profile_id": llm_id},
        ).json()[0]
        self.assertNotIn("delivery_mode", task_payload)

    def test_batch_task_card_counts_draft_generation_statuses(self) -> None:
        identity_id = self._create_identity(with_imap=False)
        llm_id = self._create_llm()
        material_id = self._upload_material(
            identity_id,
            filename="queue-ready-resume.txt",
            content=b"Research experience in reliable agent systems.",
            material_type="resume",
        )
        self.client.post("/api/professors/import-sample")
        professors = self.client.get("/api/professors").json()[:4]

        create_response = self.client.post(
            "/api/batch-tasks",
            json={
                "identity_id": identity_id,
                "llm_profile_id": llm_id,
                "name": "草稿统计任务",
                "professor_ids": [item["id"] for item in professors],
                "schedule_type": "immediate",
                "window_start_time": None,
                "window_end_time": None,
                "emails_per_window": None,
                "primary_material_id": material_id,
                "email_subject": "申请与{{name}}老师交流",
                "email_body": "老师您好，我是{{sender_name}}。",
                "selected_material_ids": None,
            },
        )
        self.assertEqual(create_response.status_code, 201, msg=create_response.text)
        batch_task_id = create_response.json()["id"]

        connection = sqlite3.connect(self.db_path)
        try:
            task_ids = [
                row[0]
                for row in connection.execute(
                    """
                    SELECT id
                    FROM email_tasks
                    WHERE batch_task_id = ?
                    ORDER BY id
                    """,
                    (batch_task_id,),
                ).fetchall()
            ]
            self.assertEqual(len(task_ids), 4)
            connection.execute(
                "UPDATE email_tasks SET status = 'generating_draft' WHERE id = ?",
                (task_ids[0],),
            )
            connection.execute(
                "UPDATE email_tasks SET status = 'draft_failed' WHERE id = ?",
                (task_ids[1],),
            )
            connection.execute(
                "UPDATE email_tasks SET status = 'review_required' WHERE id = ?",
                (task_ids[2],),
            )
            connection.execute(
                "UPDATE email_tasks SET primary_material_id = NULL WHERE id = ?",
                (task_ids[3],),
            )
            connection.commit()
        finally:
            connection.close()

        response = self.client.get("/api/batch-tasks")
        self.assertEqual(response.status_code, 200, msg=response.text)
        task_payload = next(
            item for item in response.json() if item["id"] == batch_task_id
        )
        self.assertEqual(task_payload["generating_draft_count"], 1)
        self.assertEqual(task_payload["draft_failed_count"], 1)
        self.assertEqual(task_payload["pending_generation_count"], 1)
        self.assertEqual(task_payload["queued_generation_count"], 0)
        self.assertEqual(task_payload["blocked_generation_count"], 1)

        connection = sqlite3.connect(self.db_path)
        try:
            connection.execute(
                "UPDATE email_tasks SET primary_material_id = ? WHERE id = ?",
                (material_id, task_ids[3]),
            )
            connection.commit()
        finally:
            connection.close()

        refreshed = self.client.get("/api/batch-tasks")
        self.assertEqual(refreshed.status_code, 200, msg=refreshed.text)
        refreshed_payload = next(
            item for item in refreshed.json() if item["id"] == batch_task_id
        )
        self.assertEqual(refreshed_payload["pending_generation_count"], 1)
        self.assertEqual(refreshed_payload["queued_generation_count"], 1)
        self.assertEqual(refreshed_payload["blocked_generation_count"], 0)

    def test_list_batch_tasks_syncs_all_stale_completed_tasks(self) -> None:
        identity_id = self._create_identity(with_imap=False)
        llm_id = self._create_llm()
        first_batch_task_id = self._insert_batch_task_with_material(
            identity_id=identity_id,
            llm_id=llm_id,
            status="running",
            primary_material_id=None,
        )
        second_batch_task_id = self._insert_batch_task_with_material(
            identity_id=identity_id,
            llm_id=llm_id,
            status="running",
            primary_material_id=None,
        )
        for index, batch_task_id in enumerate(
            [first_batch_task_id, second_batch_task_id], start=1
        ):
            professor_id = self._create_professor(
                email=f"stale-completed-batch-{index}@example.edu"
            )
            self._insert_email_task_with_material(
                identity_id=identity_id,
                llm_id=llm_id,
                professor_id=professor_id,
                status="sent",
                primary_material_id=None,
                batch_task_id=batch_task_id,
                source="batch",
            )

        response = self.client.get("/api/batch-tasks")

        self.assertEqual(response.status_code, 200, msg=response.text)
        payload_by_id = {item["id"]: item for item in response.json()}
        self.assertEqual(payload_by_id[first_batch_task_id]["status"], "completed")
        self.assertEqual(payload_by_id[second_batch_task_id]["status"], "completed")
        self.assertEqual(self._get_batch_task_status(first_batch_task_id), "completed")
        self.assertEqual(self._get_batch_task_status(second_batch_task_id), "completed")

    def test_template_scheduled_batch_task_creates_scheduled_items_without_review(
        self,
    ) -> None:
        identity_id = self._create_identity(with_imap=False)
        llm_id = self._create_llm()
        professor_response = self.client.post(
            "/api/professors",
            json={
                "name": "模板直通导师",
                "email": "template-direct@example.edu",
                "title": "Professor",
                "university": "Example University",
                "school": "School of Computing",
                "department": "Computer Science",
                "research_direction": "Agents",
                "recent_papers": [],
                "profile_url": None,
                "source_url": None,
            },
        )
        self.assertEqual(
            professor_response.status_code, 201, msg=professor_response.text
        )
        professor_id = professor_response.json()["id"]
        scheduled_date = (datetime.now().date() + timedelta(days=1)).isoformat()

        response = self.client.post(
            "/api/batch-tasks",
            json={
                "identity_id": identity_id,
                "llm_profile_id": llm_id,
                "name": "模板批量任务",
                "professor_ids": [professor_id],
                "schedule_type": "scheduled",
                "scheduled_dates": [scheduled_date],
                "window_start_time": "09:00",
                "window_end_time": "18:00",
                "emails_per_window": 10,
                "primary_material_id": None,
                "email_subject": None,
                "email_body": None,
                "selected_material_ids": None,
                "outreach_generation_mode": "template",
                "outreach_template_subject": "发送给{{name}}",
                "outreach_template_body_text": "{{name}}老师您好，我是{{sender_name}}。",
                "outreach_template_body_html": "<p>{{name}}老师您好，我是{{sender_name}}。</p>",
            },
        )

        self.assertEqual(response.status_code, 201, msg=response.text)
        self.assertEqual(response.json()["approved_count"], 0)
        self.assertEqual(response.json()["review_required_count"], 0)
        self.assertEqual(response.json()["scheduled_count"], 1)
        task_id = response.json()["id"]
        items = self.client.get(f"/api/batch-tasks/{task_id}/items")
        self.assertEqual(items.status_code, 200, msg=items.text)
        self.assertEqual(items.json()[0]["status"], "scheduled")
        self.assertIsNotNone(items.json()[0]["scheduled_at"])

    def test_batch_task_item_workspace_actions_are_scoped_to_batch_item(self) -> None:
        identity_id = self._create_identity(with_imap=False)
        llm_id = self._create_llm()
        material_id = self._upload_material(
            identity_id,
            filename="scoped-batch-review-resume.txt",
            content=b"My background covers AI agents and research workflows.",
            material_type="resume",
        )
        professor_id = self._create_professor(email="scoped-batch-review@example.edu")
        template_response = self.client.post(
            "/api/outreach-templates",
            json={
                "name": "批量审核指定模板",
                "recommended_generation_mode": "template",
                "subject": "第一批模板主题",
                "body_text": "第一批模板正文",
                "body_html": "<p>第一批模板正文</p>",
                "is_default": False,
            },
        )
        self.assertEqual(template_response.status_code, 201, msg=template_response.text)
        template_id = template_response.json()["id"]
        first_batch_id = self._insert_batch_task_with_material(
            identity_id=identity_id,
            llm_id=llm_id,
            status="running",
            primary_material_id=material_id,
        )
        second_batch_id = self._insert_batch_task_with_material(
            identity_id=identity_id,
            llm_id=llm_id,
            status="running",
            primary_material_id=material_id,
        )
        first_task_id = self._insert_email_task_with_material(
            identity_id=identity_id,
            llm_id=llm_id,
            professor_id=professor_id,
            status="review_required",
            primary_material_id=material_id,
            selected_material_ids=[],
            batch_task_id=first_batch_id,
            source="batch",
            generated_subject="第一批草稿",
            generated_content_text="第一批正文",
            generated_content_html="<p>第一批正文</p>",
            match_score=82,
            match_reason="第一批方向匹配",
        )
        second_task_id = self._insert_email_task_with_material(
            identity_id=identity_id,
            llm_id=llm_id,
            professor_id=professor_id,
            status="review_required",
            primary_material_id=material_id,
            selected_material_ids=[],
            batch_task_id=second_batch_id,
            source="batch",
            generated_subject="第二批草稿",
            generated_content_text="第二批正文",
            generated_content_html="<p>第二批正文</p>",
            match_score=82,
            match_reason="第二批方向匹配",
        )
        connection = sqlite3.connect(self.db_path)
        try:
            scheduled_at = (datetime.now(UTC) + timedelta(days=1)).isoformat()
            connection.execute(
                "UPDATE email_tasks SET scheduled_at = ? WHERE id = ?",
                (scheduled_at, first_task_id),
            )
            connection.commit()
        finally:
            connection.close()

        thread_response = self.client.get(
            f"/api/batch-tasks/{first_batch_id}/items/{first_task_id}/thread",
        )

        self.assertEqual(thread_response.status_code, 200, msg=thread_response.text)
        self.assertEqual(thread_response.json()["current_task"]["id"], first_task_id)
        self.assertEqual(
            thread_response.json()["current_task"]["batch_task_id"], first_batch_id
        )
        self.assertEqual(
            thread_response.json()["current_task"]["generated_subject"], "第一批草稿"
        )
        self.assertEqual(
            thread_response.json()["professor"]["department"], "Computer Science"
        )

        mismatch_response = self.client.get(
            f"/api/batch-tasks/{first_batch_id}/items/{second_task_id}/thread",
        )
        self.assertEqual(mismatch_response.status_code, 404, msg=mismatch_response.text)

        outreach_payload = {
            "outreach_generation_mode": "template",
            "outreach_template_id": template_id,
            "outreach_template_subject": "第一批模板主题",
            "outreach_template_body_text": "第一批模板正文",
            "outreach_template_body_html": "<p>第一批模板正文</p>",
        }
        mismatch_outreach_response = self.client.post(
            f"/api/batch-tasks/{first_batch_id}/items/{second_task_id}/outreach-config",
            json=outreach_payload,
        )
        self.assertEqual(
            mismatch_outreach_response.status_code,
            404,
            msg=mismatch_outreach_response.text,
        )

        outreach_response = self.client.post(
            f"/api/batch-tasks/{first_batch_id}/items/{first_task_id}/outreach-config",
            json=outreach_payload,
        )

        self.assertEqual(outreach_response.status_code, 200, msg=outreach_response.text)
        outreach_task = outreach_response.json()["current_task"]
        self.assertEqual(outreach_task["id"], first_task_id)
        self.assertEqual(outreach_task["batch_task_id"], first_batch_id)
        self.assertEqual(outreach_task["status"], "review_required")
        self.assertEqual(outreach_task["outreach_template_id"], template_id)
        self.assertEqual(outreach_task["draft_generation_source"], "template")
        self.assertIsNone(outreach_task["draft_fallback_reason"])
        self.assertEqual(outreach_task["draft"]["source"], "template")
        self.assertEqual(outreach_task["draft"]["subject"], "第一批模板主题")
        self.assertEqual(outreach_task["draft"]["body_text"], "第一批模板正文")
        self.assertEqual(outreach_task["generated_subject"], "第一批模板主题")
        self.assertEqual(outreach_task["generated_content_text"], "第一批模板正文")
        self.assertIsNotNone(outreach_task["scheduled_at"])

        reopened_first_thread = self.client.get(
            f"/api/batch-tasks/{first_batch_id}/items/{first_task_id}/thread",
        )
        self.assertEqual(
            reopened_first_thread.status_code,
            200,
            msg=reopened_first_thread.text,
        )
        reopened_first_task = reopened_first_thread.json()["current_task"]
        self.assertEqual(reopened_first_task["status"], "review_required")
        self.assertEqual(reopened_first_task["draft"]["source"], "template")
        self.assertEqual(reopened_first_task["draft"]["subject"], "第一批模板主题")
        self.assertIsNotNone(reopened_first_task["scheduled_at"])

        first_batch_items = self.client.get(
            f"/api/batch-tasks/{first_batch_id}/items",
        )
        self.assertEqual(
            first_batch_items.status_code,
            200,
            msg=first_batch_items.text,
        )
        first_batch_item = first_batch_items.json()[0]
        self.assertEqual(first_batch_item["status"], "review_required")
        self.assertEqual(first_batch_item["next_action"], "review_draft")
        self.assertEqual(first_batch_item["draft_generation_source"], "template")
        batch_cards = self.client.get("/api/batch-tasks")
        self.assertEqual(batch_cards.status_code, 200, msg=batch_cards.text)
        first_batch_card = next(
            card for card in batch_cards.json() if card["id"] == first_batch_id
        )
        self.assertEqual(first_batch_card["review_required_count"], 1)

        second_thread_after_outreach = self.client.get(
            f"/api/batch-tasks/{second_batch_id}/items/{second_task_id}/thread",
        )
        self.assertEqual(
            second_thread_after_outreach.status_code,
            200,
            msg=second_thread_after_outreach.text,
        )
        second_task_after_outreach = second_thread_after_outreach.json()["current_task"]
        self.assertEqual(second_task_after_outreach["id"], second_task_id)
        self.assertIsNone(second_task_after_outreach["outreach_template_id"])
        self.assertEqual(second_task_after_outreach["generated_subject"], "第二批草稿")

        async def _fake_generate_draft_content(**kwargs):
            self.assertEqual(kwargs["custom_subject"], "第一批模板主题")
            self.assertEqual(kwargs["custom_body"], "第一批模板正文")
            self.assertEqual(kwargs["custom_body_html"], "<p>第一批模板正文</p>")
            return self._build_draft_generation_result(
                subject="第一批 AI 改写主题",
                body_text="第一批 AI 改写正文",
                body_html="<p>第一批 AI 改写正文</p>",
            )

        with patch(
            "app.modules.workspace.tasks.runtime.llm_runtime.generate_draft_content",
            AsyncMock(side_effect=_fake_generate_draft_content),
        ):
            rewrite_response = self.client.post(
                f"/api/batch-tasks/{first_batch_id}/items/{first_task_id}/rewrite-draft",
                json={
                    "subject": "第一批模板主题",
                    "body_text": "第一批模板正文",
                    "body_html": "<p>第一批模板正文</p>",
                    "selected_material_ids": [],
                    "llm_profile_id": llm_id,
                },
            )

        self.assertEqual(rewrite_response.status_code, 200, msg=rewrite_response.text)
        rewritten_task = rewrite_response.json()["current_task"]
        self.assertEqual(rewritten_task["id"], first_task_id)
        self.assertEqual(rewritten_task["batch_task_id"], first_batch_id)
        self.assertEqual(rewritten_task["draft"]["source"], "ai_rewrite")
        self.assertEqual(rewritten_task["draft"]["subject"], "第一批 AI 改写主题")

        second_thread_after_rewrite = self.client.get(
            f"/api/batch-tasks/{second_batch_id}/items/{second_task_id}/thread",
        )
        self.assertEqual(
            second_thread_after_rewrite.status_code,
            200,
            msg=second_thread_after_rewrite.text,
        )
        self.assertEqual(
            second_thread_after_rewrite.json()["current_task"]["generated_subject"],
            "第二批草稿",
        )

        approve_response = self.client.post(
            f"/api/batch-tasks/{first_batch_id}/items/{first_task_id}/approve",
            json={
                "subject": "第一批已审核",
                "body_text": "第一批审核正文",
                "body_html": "<p>第一批审核正文</p>",
                "selected_material_ids": [],
            },
        )

        self.assertEqual(approve_response.status_code, 200, msg=approve_response.text)
        self.assertEqual(approve_response.json()["current_task"]["id"], first_task_id)
        self.assertEqual(approve_response.json()["current_task"]["status"], "approved")
        self.assertEqual(
            approve_response.json()["current_task"]["approved_subject"],
            "第一批已审核",
        )
        first_state = self._get_email_task_delete_state(first_task_id)
        second_state = self._get_email_task_delete_state(second_task_id)
        self.assertEqual(first_state["status"], "approved")
        self.assertEqual(first_state["approved_subject"], "第一批已审核")
        self.assertEqual(second_state["status"], "review_required")
        self.assertIsNone(second_state["approved_subject"])

        stale_apply = self.client.post(
            f"/api/batch-tasks/{first_batch_id}/items/{first_task_id}/outreach-config",
            json=outreach_payload,
        )
        self.assertEqual(stale_apply.status_code, 400, msg=stale_apply.text)
        self.assertIn("草稿状态已发生变化", stale_apply.json()["detail"])
        approved_state = self._get_email_task_delete_state(first_task_id)
        self.assertEqual(approved_state["status"], "approved")
        self.assertEqual(approved_state["approved_subject"], "第一批已审核")

    def test_bulk_approve_batch_drafts_snapshots_all_confirmed_items(self) -> None:
        identity_id = self._create_identity(with_imap=False)
        llm_id = self._create_llm()
        first_professor_id = self._create_professor(
            email="bulk-review-first@example.edu",
        )
        second_professor_id = self._create_professor(
            email="bulk-review-second@example.edu",
        )
        batch_task_id = self._insert_batch_task_with_material(
            identity_id=identity_id,
            llm_id=llm_id,
            status="running",
            primary_material_id=None,
        )
        first_task_id = self._insert_email_task_with_material(
            identity_id=identity_id,
            llm_id=llm_id,
            professor_id=first_professor_id,
            status="review_required",
            primary_material_id=None,
            batch_task_id=batch_task_id,
            source="batch",
            generated_subject="第一封 AI 主题",
            generated_content_text="第一封 AI 正文",
            generated_content_html="<p>第一封 AI 正文</p>",
        )
        second_task_id = self._insert_email_task_with_material(
            identity_id=identity_id,
            llm_id=llm_id,
            professor_id=second_professor_id,
            status="review_required",
            primary_material_id=None,
            batch_task_id=batch_task_id,
            source="batch",
            generated_subject="第二封 AI 主题",
            generated_content_text="第二封 AI 正文",
            generated_content_html="<p>第二封 AI 正文</p>",
        )

        response = self.client.post(
            f"/api/batch-tasks/{batch_task_id}/approve-all-drafts",
            json={"item_ids": [first_task_id, second_task_id]},
        )

        self.assertEqual(response.status_code, 200, msg=response.text)
        self.assertTrue(response.json()["ok"])
        self.assertEqual(response.json()["approved_count"], 2)
        self.assertEqual(response.json()["task"]["review_required_count"], 0)
        self.assertEqual(response.json()["task"]["approved_count"], 2)
        first_state = self._get_email_task_delete_state(first_task_id)
        second_state = self._get_email_task_delete_state(second_task_id)
        self.assertEqual(first_state["status"], "approved")
        self.assertEqual(first_state["approved_subject"], "第一封 AI 主题")
        self.assertEqual(first_state["approved_body_text"], "第一封 AI 正文")
        self.assertEqual(first_state["approved_body_html"], "<p>第一封 AI 正文</p>")
        self.assertIsNotNone(first_state["approved_at"])
        self.assertEqual(second_state["status"], "approved")
        self.assertEqual(second_state["approved_subject"], "第二封 AI 主题")
        self.assertEqual(second_state["approved_body_text"], "第二封 AI 正文")
        self.assertIsNotNone(second_state["approved_at"])

    def test_bulk_approve_batch_drafts_preserves_scheduled_delivery(self) -> None:
        identity_id = self._create_identity(with_imap=False)
        llm_id = self._create_llm()
        professor_id = self._create_professor(
            email="bulk-review-scheduled@example.edu",
        )
        batch_task_id = self._insert_batch_task_with_material(
            identity_id=identity_id,
            llm_id=llm_id,
            status="running",
            primary_material_id=None,
        )
        task_id = self._insert_email_task_with_material(
            identity_id=identity_id,
            llm_id=llm_id,
            professor_id=professor_id,
            status="review_required",
            primary_material_id=None,
            batch_task_id=batch_task_id,
            source="batch",
            generated_subject="定时 AI 主题",
            generated_content_text="定时 AI 正文",
            generated_content_html="<p>定时 AI 正文</p>",
        )
        scheduled_at = datetime.now(UTC) + timedelta(days=1)
        connection = sqlite3.connect(self.db_path)
        try:
            connection.execute(
                """
                UPDATE batch_tasks
                SET schedule_type = 'scheduled', scheduled_dates = ?,
                    window_start_time = '09:00', window_end_time = '18:00',
                    emails_per_window = 10
                WHERE id = ?
                """,
                (json.dumps([scheduled_at.date().isoformat()]), batch_task_id),
            )
            connection.execute(
                "UPDATE email_tasks SET scheduled_at = ? WHERE id = ?",
                (scheduled_at.isoformat(), task_id),
            )
            connection.commit()
        finally:
            connection.close()

        response = self.client.post(
            f"/api/batch-tasks/{batch_task_id}/approve-all-drafts",
            json={"item_ids": [task_id]},
        )

        self.assertEqual(response.status_code, 200, msg=response.text)
        self.assertEqual(response.json()["approved_count"], 1)
        self.assertEqual(response.json()["task"]["approved_count"], 0)
        self.assertEqual(response.json()["task"]["scheduled_count"], 1)
        items = self.client.get(f"/api/batch-tasks/{batch_task_id}/items")
        self.assertEqual(items.status_code, 200, msg=items.text)
        self.assertEqual(items.json()[0]["status"], "scheduled")
        self.assertIsNotNone(items.json()[0]["scheduled_at"])

    def test_bulk_approve_batch_drafts_is_atomic_when_review_snapshot_changes(
        self,
    ) -> None:
        identity_id = self._create_identity(with_imap=False)
        llm_id = self._create_llm()
        first_professor_id = self._create_professor(
            email="bulk-review-conflict-first@example.edu",
        )
        second_professor_id = self._create_professor(
            email="bulk-review-conflict-second@example.edu",
        )
        batch_task_id = self._insert_batch_task_with_material(
            identity_id=identity_id,
            llm_id=llm_id,
            status="running",
            primary_material_id=None,
        )
        pending_task_id = self._insert_email_task_with_material(
            identity_id=identity_id,
            llm_id=llm_id,
            professor_id=first_professor_id,
            status="review_required",
            primary_material_id=None,
            batch_task_id=batch_task_id,
            source="batch",
            generated_subject="仍待审核主题",
            generated_content_text="仍待审核正文",
            generated_content_html="<p>仍待审核正文</p>",
        )
        changed_task_id = self._insert_email_task_with_material(
            identity_id=identity_id,
            llm_id=llm_id,
            professor_id=second_professor_id,
            status="approved",
            primary_material_id=None,
            batch_task_id=batch_task_id,
            source="batch",
            generated_subject="已变化主题",
            generated_content_text="已变化正文",
            generated_content_html="<p>已变化正文</p>",
            approved_subject="已经审核",
            approved_body_text="已经审核正文",
            approved_body_html="<p>已经审核正文</p>",
        )

        response = self.client.post(
            f"/api/batch-tasks/{batch_task_id}/approve-all-drafts",
            json={"item_ids": [pending_task_id, changed_task_id]},
        )

        self.assertEqual(response.status_code, 409, msg=response.text)
        self.assertIn("待审核草稿列表已发生变化", response.json()["detail"])
        pending_state = self._get_email_task_delete_state(pending_task_id)
        self.assertEqual(pending_state["status"], "review_required")
        self.assertIsNone(pending_state["approved_subject"])
        self.assertIsNone(pending_state["approved_at"])

    def test_approve_batch_draft_rejects_expired_scheduled_batch(self) -> None:
        batch_task_id, task_id = self._create_expired_scheduled_batch_review_task()

        response = self.client.post(
            f"/api/email-tasks/{task_id}/approve",
            json={
                "subject": "申请与导师交流",
                "body_text": "老师您好，我是申请人。",
                "body_html": "<p>老师您好，我是申请人。</p>",
                "selected_material_ids": [],
            },
        )

        self.assertEqual(response.status_code, 400, msg=response.text)
        self.assertIn("发送窗口已全部过期", response.json()["detail"])
        items = self.client.get(f"/api/batch-tasks/{batch_task_id}/items")
        self.assertEqual(items.status_code, 200, msg=items.text)
        self.assertEqual(items.json()[0]["status"], "review_required")

    def test_resume_scheduled_batch_task_expires_when_window_has_passed(self) -> None:
        batch_task_id, task_id = self._create_expired_scheduled_batch_review_task(
            batch_status="paused"
        )

        response = self.client.post(f"/api/batch-tasks/{batch_task_id}/resume")

        self.assertEqual(response.status_code, 200, msg=response.text)
        self.assertEqual(response.json()["task"]["status"], "expired")
        items = self.client.get(f"/api/batch-tasks/{batch_task_id}/items")
        self.assertEqual(items.status_code, 200, msg=items.text)
        self.assertEqual(items.json()[0]["status"], "canceled")
        self.assertEqual(items.json()[0]["cancellation_reason"], "schedule_expired")
        self.assertEqual(task_id, items.json()[0]["id"])

    def test_template_immediate_batch_task_queues_items_on_create(self) -> None:
        identity_id = self._create_identity(with_imap=False)
        llm_id = self._create_llm()
        professor_response = self.client.post(
            "/api/professors",
            json={
                "name": "立即发送导师",
                "email": "template-immediate@example.edu",
                "title": "Professor",
                "university": "Example University",
                "school": "School of Computing",
                "department": "Computer Science",
                "research_direction": "Agents",
                "recent_papers": [],
                "profile_url": None,
                "source_url": None,
            },
        )
        self.assertEqual(
            professor_response.status_code, 201, msg=professor_response.text
        )
        professor_id = professor_response.json()["id"]

        with patch(
            "app.modules.workspace.tasks.delivery.mail_runtime.send_email",
            AsyncMock(
                return_value=self._build_send_result(
                    message_id="<template-immediate@example.com>",
                    provider_payload={},
                ),
            ),
        ) as mocked_send:
            response = self.client.post(
                "/api/batch-tasks",
                json={
                    "identity_id": identity_id,
                    "llm_profile_id": llm_id,
                    "name": "模板立即发送批量任务",
                    "professor_ids": [professor_id],
                    "schedule_type": "immediate",
                    "window_start_time": None,
                    "window_end_time": None,
                    "emails_per_window": None,
                    "primary_material_id": None,
                    "email_subject": None,
                    "email_body": None,
                    "selected_material_ids": None,
                    "outreach_generation_mode": "template",
                    "outreach_template_subject": "发送给{{name}}",
                    "outreach_template_body_text": "{{name}}老师您好，我是{{sender_name}}。",
                    "outreach_template_body_html": "<p>{{name}}老师您好，我是{{sender_name}}。</p>",
                },
            )

        self.assertEqual(response.status_code, 201, msg=response.text)
        task_id = response.json()["id"]
        self.assertEqual(response.json()["sent_count"], 0)
        mocked_send.assert_not_awaited()

        items = self.client.get(f"/api/batch-tasks/{task_id}/items")
        self.assertEqual(items.status_code, 200, msg=items.text)
        self.assertEqual(items.json()[0]["status"], "approved")

    def test_ai_batch_missing_research_uses_reviewable_template_fallback(self) -> None:
        identity_id = self._create_identity(with_imap=False)
        llm_id = self._create_llm()
        material_id = self._upload_material(
            identity_id,
            filename="resume.txt",
            content=b"My research focuses on agent systems.",
            material_type="resume",
        )
        professor_response = self.client.post(
            "/api/professors",
            json={
                "name": "缺研究方向导师",
                "email": "template-fallback@example.edu",
                "title": "Professor",
                "university": "Example University",
                "school": "School of Computing",
                "department": "Computer Science",
                "research_direction": "",
                "recent_papers": [],
                "profile_url": None,
                "source_url": None,
            },
        )
        self.assertEqual(
            professor_response.status_code, 201, msg=professor_response.text
        )

        response = self.client.post(
            "/api/batch-tasks",
            json={
                "identity_id": identity_id,
                "llm_profile_id": llm_id,
                "name": "AI 模板降级任务",
                "professor_ids": [professor_response.json()["id"]],
                "schedule_type": "immediate",
                "primary_material_id": material_id,
                "selected_material_ids": [],
                "outreach_generation_mode": "llm",
                "outreach_template_subject": "申请与{{name}}老师交流",
                "outreach_template_body_text": "{{name}}老师您好，我是{{sender_name}}。",
                "outreach_template_body_html": "<p>{{name}}老师您好，我是{{sender_name}}。</p>",
            },
        )

        self.assertEqual(response.status_code, 201, msg=response.text)
        batch_task_id = response.json()["id"]
        self.assertEqual(response.json()["pending_generation_count"], 0)
        self.assertEqual(response.json()["review_required_count"], 1)

        items = self.client.get(f"/api/batch-tasks/{batch_task_id}/items")
        self.assertEqual(items.status_code, 200, msg=items.text)
        item = items.json()[0]
        self.assertEqual(item["status"], "review_required")
        self.assertEqual(item["next_action"], "review_draft")
        self.assertIsNone(item["professor_research_direction"])
        self.assertEqual(item["draft_generation_source"], "template_fallback")
        self.assertEqual(item["draft_fallback_reason"], "missing_research_direction")

        thread = self.client.get(
            f"/api/batch-tasks/{batch_task_id}/items/{item['id']}/thread",
        )
        self.assertEqual(thread.status_code, 200, msg=thread.text)
        self.assertEqual(
            thread.json()["current_task"]["outreach_generation_mode"], "llm"
        )
        self.assertEqual(
            thread.json()["current_task"]["generated_subject"],
            "申请与缺研究方向导师老师交流",
        )
        self.assertEqual(
            thread.json()["current_task"]["generated_content_text"],
            "缺研究方向导师老师您好，我是测试身份。",
        )
        self.assertEqual(
            thread.json()["current_task"]["draft_generation_source"],
            "template_fallback",
        )

        replacement_template = self.client.post(
            "/api/outreach-templates",
            json={
                "name": "缺方向审核替换模板",
                "recommended_generation_mode": "template",
                "subject": "重新申请与{{name}}老师交流",
                "body_text": "{{name}}老师您好，这是重新套用的模板。",
                "body_html": "<p>{{name}}老师您好，这是重新套用的模板。</p>",
                "is_default": False,
            },
        )
        self.assertEqual(
            replacement_template.status_code,
            201,
            msg=replacement_template.text,
        )
        replacement_template_id = replacement_template.json()["id"]
        applied = self.client.post(
            f"/api/batch-tasks/{batch_task_id}/items/{item['id']}/outreach-config",
            json={
                "outreach_generation_mode": "llm",
                "outreach_template_id": replacement_template_id,
                "outreach_template_subject": "客户端过期主题",
                "outreach_template_body_text": "客户端过期正文",
                "outreach_template_body_html": "<p>客户端过期正文</p>",
            },
        )
        self.assertEqual(applied.status_code, 200, msg=applied.text)
        applied_task = applied.json()["current_task"]
        self.assertEqual(applied_task["status"], "review_required")
        self.assertEqual(applied_task["outreach_generation_mode"], "template")
        self.assertEqual(
            applied_task["outreach_template_id"],
            replacement_template_id,
        )
        self.assertEqual(
            applied_task["generated_subject"],
            "重新申请与缺研究方向导师老师交流",
        )
        self.assertEqual(
            applied_task["generated_content_text"],
            "缺研究方向导师老师您好，这是重新套用的模板。",
        )
        self.assertEqual(applied_task["draft"]["source"], "template")
        self.assertEqual(
            applied_task["draft_generation_source"],
            "template_fallback",
        )
        self.assertEqual(
            applied_task["draft_fallback_reason"],
            "missing_research_direction",
        )

        reopened_after_apply = self.client.get(
            f"/api/batch-tasks/{batch_task_id}/items/{item['id']}/thread",
        )
        self.assertEqual(
            reopened_after_apply.status_code,
            200,
            msg=reopened_after_apply.text,
        )
        reopened_task = reopened_after_apply.json()["current_task"]
        self.assertEqual(reopened_task["status"], "review_required")
        self.assertEqual(
            reopened_task["generated_subject"],
            "重新申请与缺研究方向导师老师交流",
        )
        self.assertEqual(
            reopened_task["draft_generation_source"],
            "template_fallback",
        )
        refreshed_items = self.client.get(
            f"/api/batch-tasks/{batch_task_id}/items",
        )
        self.assertEqual(
            refreshed_items.status_code,
            200,
            msg=refreshed_items.text,
        )
        refreshed_item = refreshed_items.json()[0]
        self.assertEqual(refreshed_item["status"], "review_required")
        self.assertEqual(refreshed_item["next_action"], "review_draft")
        self.assertEqual(
            refreshed_item["draft_generation_source"],
            "template_fallback",
        )
        refreshed_cards = self.client.get("/api/batch-tasks")
        self.assertEqual(refreshed_cards.status_code, 200, msg=refreshed_cards.text)
        refreshed_card = next(
            card for card in refreshed_cards.json() if card["id"] == batch_task_id
        )
        self.assertEqual(refreshed_card["review_required_count"], 1)

        with patch(
            "app.modules.workspace.tasks.runtime.llm_runtime.generate_draft_content",
            AsyncMock(side_effect=AssertionError("缺研究方向时不应调用模型")),
        ) as mocked_generate:
            rewrite = self.client.post(
                f"/api/batch-tasks/{batch_task_id}/items/{item['id']}/rewrite-draft",
                json={
                    "subject": applied_task["generated_subject"],
                    "body_text": applied_task["generated_content_text"],
                    "body_html": applied_task["generated_content_html"],
                    "selected_material_ids": [],
                    "llm_profile_id": llm_id,
                },
            )
        self.assertEqual(rewrite.status_code, 400, msg=rewrite.text)
        self.assertIn("请先补充导师研究方向", rewrite.json()["detail"])
        mocked_generate.assert_not_awaited()

        preserved = self.client.get(
            f"/api/batch-tasks/{batch_task_id}/items/{item['id']}/thread",
        ).json()
        self.assertEqual(preserved["current_task"]["status"], "review_required")
        self.assertEqual(
            preserved["current_task"]["generated_content_text"],
            "缺研究方向导师老师您好，这是重新套用的模板。",
        )
        self.assertEqual(
            preserved["current_task"]["draft_generation_source"],
            "template_fallback",
        )

        approved = self.client.post(
            f"/api/batch-tasks/{batch_task_id}/items/{item['id']}/approve",
            json={
                "subject": preserved["current_task"]["generated_subject"],
                "body_text": preserved["current_task"]["generated_content_text"],
                "body_html": preserved["current_task"]["generated_content_html"],
                "selected_material_ids": [],
            },
        )
        self.assertEqual(approved.status_code, 200, msg=approved.text)
        self.assertEqual(approved.json()["current_task"]["status"], "approved")

    def test_batch_task_items_show_professor_delivery_progress(self) -> None:
        identity_id = self._create_identity(with_imap=False)
        llm_id = self._create_llm()
        attachment_content = b"attachment-size-test"
        attachment_id = self._upload_material(
            identity_id,
            filename="attachment.txt",
            content=attachment_content,
            material_type="other",
        )
        self.client.post("/api/professors/import-sample")
        professors = self.client.get("/api/professors").json()[:2]

        create_response = self.client.post(
            "/api/batch-tasks",
            json={
                "identity_id": identity_id,
                "llm_profile_id": llm_id,
                "name": "明细任务",
                "professor_ids": [item["id"] for item in professors],
                "schedule_type": "immediate",
                "window_start_time": None,
                "window_end_time": None,
                "emails_per_window": None,
                "primary_material_id": None,
                "email_subject": "申请与{{name}}老师交流",
                "email_body": "老师您好，我是{{sender_name}}。",
                "selected_material_ids": [attachment_id],
            },
        )
        self.assertEqual(create_response.status_code, 201, msg=create_response.text)
        batch_task_id = create_response.json()["id"]

        connection = sqlite3.connect(self.db_path)
        try:
            task_ids = [
                row[0]
                for row in connection.execute(
                    """
                    SELECT id
                    FROM email_tasks
                    WHERE batch_task_id = ?
                    ORDER BY id
                    """,
                    (batch_task_id,),
                ).fetchall()
            ]
            self.assertEqual(len(task_ids), 2)
            connection.execute(
                """
                UPDATE email_tasks
                SET status = 'sent', sent_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (task_ids[0],),
            )
            connection.execute(
                """
                UPDATE email_tasks
                SET status = 'send_failed',
                    last_error = 'SMTP 发信失败: (550, b''Requested action aborted: flow over limit'')'
                WHERE id = ?
                """,
                (task_ids[1],),
            )
            connection.commit()
        finally:
            connection.close()

        from app.modules.campaigns.batch_tasks.api import _serialize_batch_task_item

        unloaded_task_columns: list[set[str]] = []
        unloaded_professor_columns: list[set[str]] = []

        def capture_batch_item_projection(email_task, **kwargs):
            unloaded_task_columns.append(inspect(email_task).unloaded)
            unloaded_professor_columns.append(inspect(email_task.professor).unloaded)
            return _serialize_batch_task_item(email_task, **kwargs)

        with patch(
            "app.modules.campaigns.batch_tasks.api._serialize_batch_task_item",
            side_effect=capture_batch_item_projection,
        ):
            response = self.client.get(f"/api/batch-tasks/{batch_task_id}/items")
        self.assertEqual(response.status_code, 200, msg=response.text)
        payload = response.json()
        self.assertEqual(len(payload), 2)
        self.assertEqual(payload[0]["professor_id"], professors[0]["id"])
        self.assertEqual(payload[0]["professor_name"], professors[0]["name"])
        self.assertEqual(payload[0]["status"], "sent")
        self.assertIsNotNone(payload[0]["sent_at"])
        self.assertEqual(
            payload[0]["selected_attachment_size_bytes"],
            len(attachment_content),
        )
        self.assertEqual(
            payload[1]["selected_attachment_size_bytes"],
            len(attachment_content),
        )
        self.assertEqual(payload[1]["status"], "send_failed")
        self.assertEqual(
            payload[1]["last_error"],
            "SMTP 发信失败: (550, b'Requested action aborted: flow over limit')",
        )
        self.assertIn("发送限流", payload[1]["possible_cause"])
        self.assertIn("generated_content_text", unloaded_task_columns[0])
        self.assertIn("generated_content_html", unloaded_task_columns[0])
        self.assertIn("recent_papers", unloaded_professor_columns[0])

    def test_batch_task_items_include_next_action_for_blocked_draft_generation(
        self,
    ) -> None:
        identity_id = self._create_identity(with_imap=False)
        llm_id = self._create_llm()
        material_id = self._upload_material(
            identity_id,
            filename="resume.txt",
            content=b"My research focuses on information extraction and agents.",
            material_type="resume",
        )
        professor_response = self.client.post(
            "/api/professors",
            json={
                "name": "缺资料导师",
                "email": "missing-profile@example.edu",
                "title": "Professor",
                "university": "Example University",
                "school": "School of Computing",
                "department": "Computer Science",
                "research_direction": "",
                "recent_papers": [],
                "profile_url": None,
                "source_url": None,
            },
        )
        self.assertEqual(
            professor_response.status_code, 201, msg=professor_response.text
        )
        batch_task_id = self._insert_batch_task_with_material(
            identity_id=identity_id,
            llm_id=llm_id,
            status="running",
            primary_material_id=material_id,
        )
        self._insert_email_task_with_material(
            identity_id=identity_id,
            llm_id=llm_id,
            professor_id=professor_response.json()["id"],
            status="discovered",
            primary_material_id=material_id,
            batch_task_id=batch_task_id,
            source="batch",
            outreach_generation_mode="llm",
        )

        response = self.client.get(f"/api/batch-tasks/{batch_task_id}/items")

        self.assertEqual(response.status_code, 200, msg=response.text)
        self.assertEqual(
            response.json()[0]["next_action"], "complete_professor_profile"
        )

    def test_batch_task_items_retry_draft_after_profile_is_completed(self) -> None:
        identity_id = self._create_identity(with_imap=False)
        llm_id = self._create_llm()
        material_id = self._upload_material(
            identity_id,
            filename="resume.txt",
            content=b"My research focuses on information extraction and agents.",
            material_type="resume",
        )
        professor_id = self._create_professor(email="completed-profile@example.edu")
        batch_task_id = self._insert_batch_task_with_material(
            identity_id=identity_id,
            llm_id=llm_id,
            status="running",
            primary_material_id=material_id,
        )
        task_id = self._insert_email_task_with_material(
            identity_id=identity_id,
            llm_id=llm_id,
            professor_id=professor_id,
            status="draft_failed",
            primary_material_id=material_id,
            batch_task_id=batch_task_id,
            source="batch",
            outreach_generation_mode="llm",
        )
        connection = sqlite3.connect(self.db_path)
        try:
            connection.execute(
                """
                UPDATE email_tasks
                SET last_error = '请先补充导师研究方向，再使用 AI 生成草稿'
                WHERE id = ?
                """,
                (task_id,),
            )
            connection.commit()
        finally:
            connection.close()

        response = self.client.get(f"/api/batch-tasks/{batch_task_id}/items")

        self.assertEqual(response.status_code, 200, msg=response.text)
        self.assertEqual(response.json()[0]["next_action"], "retry_draft_generation")

    def test_setting_primary_material_unblocks_batch_task_items_missing_material(
        self,
    ) -> None:
        identity_id = self._create_identity(with_imap=False)
        llm_id = self._create_llm()
        material_id = self._upload_material(
            identity_id,
            filename="resume.txt",
            content=b"My research focuses on information extraction and agents.",
            material_type="resume",
        )
        professor_id = self._create_professor(email="missing-material@example.edu")
        batch_task_id = self._insert_batch_task_with_material(
            identity_id=identity_id,
            llm_id=llm_id,
            status="running",
            primary_material_id=None,
        )
        self._insert_email_task_with_material(
            identity_id=identity_id,
            llm_id=llm_id,
            professor_id=professor_id,
            status="discovered",
            primary_material_id=None,
            batch_task_id=batch_task_id,
            source="batch",
            outreach_generation_mode="llm",
        )

        before_response = self.client.get(f"/api/batch-tasks/{batch_task_id}/items")
        self.assertEqual(before_response.status_code, 200, msg=before_response.text)
        self.assertEqual(
            before_response.json()[0]["next_action"], "select_primary_material"
        )

        set_primary_response = self.client.post(
            f"/api/materials/{material_id}/set-primary"
        )

        self.assertEqual(
            set_primary_response.status_code, 200, msg=set_primary_response.text
        )
        after_response = self.client.get(f"/api/batch-tasks/{batch_task_id}/items")
        self.assertEqual(after_response.status_code, 200, msg=after_response.text)
        self.assertEqual(
            after_response.json()[0]["next_action"], "waiting_draft_generation"
        )

    def test_null_generation_mode_batch_item_uses_llm_next_action_contract(
        self,
    ) -> None:
        identity_id = self._create_identity(with_imap=False)
        llm_id = self._create_llm()
        professor_id = self._create_professor(
            email="null-mode-missing-material@example.edu"
        )
        batch_task_id = self._insert_batch_task_with_material(
            identity_id=identity_id,
            llm_id=llm_id,
            status="running",
            primary_material_id=None,
        )
        self._insert_email_task_with_material(
            identity_id=identity_id,
            llm_id=llm_id,
            professor_id=professor_id,
            status="discovered",
            primary_material_id=None,
            batch_task_id=batch_task_id,
            source="batch",
            outreach_generation_mode=None,
        )

        response = self.client.get(f"/api/batch-tasks/{batch_task_id}/items")

        self.assertEqual(response.status_code, 200, msg=response.text)
        self.assertEqual(response.json()[0]["next_action"], "select_primary_material")

    def test_setting_primary_material_unblocks_null_generation_mode_batch_items(
        self,
    ) -> None:
        identity_id = self._create_identity(with_imap=False)
        llm_id = self._create_llm()
        material_id = self._upload_material(
            identity_id,
            filename="resume.txt",
            content=b"My research focuses on information extraction and agents.",
            material_type="resume",
        )
        professor_id = self._create_professor(email="null-mode-unblocks@example.edu")
        batch_task_id = self._insert_batch_task_with_material(
            identity_id=identity_id,
            llm_id=llm_id,
            status="running",
            primary_material_id=None,
        )
        self._insert_email_task_with_material(
            identity_id=identity_id,
            llm_id=llm_id,
            professor_id=professor_id,
            status="discovered",
            primary_material_id=None,
            batch_task_id=batch_task_id,
            source="batch",
            outreach_generation_mode=None,
        )

        set_primary_response = self.client.post(
            f"/api/materials/{material_id}/set-primary"
        )

        self.assertEqual(
            set_primary_response.status_code, 200, msg=set_primary_response.text
        )
        after_response = self.client.get(f"/api/batch-tasks/{batch_task_id}/items")
        self.assertEqual(after_response.status_code, 200, msg=after_response.text)
        self.assertEqual(
            after_response.json()[0]["next_action"], "waiting_draft_generation"
        )
        connection = sqlite3.connect(self.db_path)
        try:
            primary_material_id, generation_mode = connection.execute(
                "SELECT primary_material_id, outreach_generation_mode FROM email_tasks WHERE batch_task_id = ?",
                (batch_task_id,),
            ).fetchone()
        finally:
            connection.close()
        self.assertEqual(primary_material_id, material_id)
        self.assertEqual(generation_mode, "llm")

    def test_uploading_first_primary_material_unblocks_batch_task_items_missing_material(
        self,
    ) -> None:
        identity_id = self._create_identity(with_imap=False)
        llm_id = self._create_llm()
        professor_id = self._create_professor(email="upload-unblocks@example.edu")
        batch_task_id = self._insert_batch_task_with_material(
            identity_id=identity_id,
            llm_id=llm_id,
            status="running",
            primary_material_id=None,
        )
        self._insert_email_task_with_material(
            identity_id=identity_id,
            llm_id=llm_id,
            professor_id=professor_id,
            status="discovered",
            primary_material_id=None,
            batch_task_id=batch_task_id,
            source="batch",
            outreach_generation_mode="llm",
        )

        before_response = self.client.get(f"/api/batch-tasks/{batch_task_id}/items")
        self.assertEqual(before_response.status_code, 200, msg=before_response.text)
        self.assertEqual(
            before_response.json()[0]["next_action"], "select_primary_material"
        )

        material_id = self._upload_material(
            identity_id,
            filename="first-resume.txt",
            content=b"My research focuses on information extraction and agents.",
            material_type="resume",
        )

        after_response = self.client.get(f"/api/batch-tasks/{batch_task_id}/items")
        self.assertEqual(after_response.status_code, 200, msg=after_response.text)
        self.assertEqual(
            after_response.json()[0]["next_action"], "waiting_draft_generation"
        )
        connection = sqlite3.connect(self.db_path)
        try:
            primary_material_id = connection.execute(
                "SELECT primary_material_id FROM email_tasks WHERE batch_task_id = ?",
                (batch_task_id,),
            ).fetchone()[0]
        finally:
            connection.close()
        self.assertEqual(primary_material_id, material_id)

    def test_retry_batch_task_item_draft_moves_failed_item_back_to_generation_queue(
        self,
    ) -> None:
        identity_id = self._create_identity(with_imap=False)
        llm_id = self._create_llm()
        material_id = self._upload_material(
            identity_id,
            filename="resume.txt",
            content=b"My research focuses on information extraction and agents.",
            material_type="resume",
        )
        professor_id = self._create_professor(email="retry-batch-draft@example.edu")
        batch_task_id = self._insert_batch_task_with_material(
            identity_id=identity_id,
            llm_id=llm_id,
            status="running",
            primary_material_id=material_id,
        )
        task_id = self._insert_email_task_with_material(
            identity_id=identity_id,
            llm_id=llm_id,
            professor_id=professor_id,
            status="draft_failed",
            primary_material_id=material_id,
            batch_task_id=batch_task_id,
            source="batch",
            outreach_generation_mode="llm",
        )

        response = self.client.post(
            f"/api/batch-tasks/{batch_task_id}/items/{task_id}/retry-draft"
        )

        self.assertEqual(response.status_code, 200, msg=response.text)
        self.assertTrue(response.json()["ok"])
        items_response = self.client.get(f"/api/batch-tasks/{batch_task_id}/items")
        self.assertEqual(items_response.status_code, 200, msg=items_response.text)
        item = items_response.json()[0]
        self.assertEqual(item["status"], "discovered")
        self.assertEqual(item["last_error"], None)
        self.assertEqual(item["next_action"], "waiting_draft_generation")

    def test_retry_null_generation_mode_batch_item_persists_llm_mode(self) -> None:
        identity_id = self._create_identity(with_imap=False)
        llm_id = self._create_llm()
        material_id = self._upload_material(
            identity_id,
            filename="resume.txt",
            content=b"My research focuses on information extraction and agents.",
            material_type="resume",
        )
        professor_id = self._create_professor(email="retry-null-mode@example.edu")
        batch_task_id = self._insert_batch_task_with_material(
            identity_id=identity_id,
            llm_id=llm_id,
            status="running",
            primary_material_id=material_id,
        )
        task_id = self._insert_email_task_with_material(
            identity_id=identity_id,
            llm_id=llm_id,
            professor_id=professor_id,
            status="draft_failed",
            primary_material_id=material_id,
            batch_task_id=batch_task_id,
            source="batch",
            outreach_generation_mode=None,
        )

        response = self.client.post(
            f"/api/batch-tasks/{batch_task_id}/items/{task_id}/retry-draft"
        )

        self.assertEqual(response.status_code, 200, msg=response.text)
        items_response = self.client.get(f"/api/batch-tasks/{batch_task_id}/items")
        self.assertEqual(items_response.status_code, 200, msg=items_response.text)
        item = items_response.json()[0]
        self.assertEqual(item["status"], "discovered")
        self.assertEqual(item["next_action"], "waiting_draft_generation")
        connection = sqlite3.connect(self.db_path)
        try:
            generation_mode = connection.execute(
                "SELECT outreach_generation_mode FROM email_tasks WHERE id = ?",
                (task_id,),
            ).fetchone()[0]
        finally:
            connection.close()
        self.assertEqual(generation_mode, "llm")

    def test_template_draft_failed_batch_item_does_not_expose_ai_retry_action(
        self,
    ) -> None:
        identity_id = self._create_identity(with_imap=False)
        llm_id = self._create_llm()
        professor_id = self._create_professor(email="template-draft-failed@example.edu")
        batch_task_id = self._insert_batch_task_with_material(
            identity_id=identity_id,
            llm_id=llm_id,
            status="running",
            primary_material_id=None,
        )
        task_id = self._insert_email_task_with_material(
            identity_id=identity_id,
            llm_id=llm_id,
            professor_id=professor_id,
            status="draft_failed",
            primary_material_id=None,
            batch_task_id=batch_task_id,
            source="batch",
            outreach_generation_mode="template",
        )

        items_response = self.client.get(f"/api/batch-tasks/{batch_task_id}/items")
        retry_response = self.client.post(
            f"/api/batch-tasks/{batch_task_id}/items/{task_id}/retry-draft"
        )

        self.assertEqual(items_response.status_code, 200, msg=items_response.text)
        self.assertEqual(items_response.json()[0]["next_action"], None)
        self.assertEqual(retry_response.status_code, 400)

    def test_batch_task_workspace_keeps_created_template_snapshot(self) -> None:
        identity_id = self._create_identity(with_imap=False)
        llm_id = self._create_llm()

        self.client.put(
            f"/api/identities/{identity_id}",
            json=self._build_identity_payload(
                with_imap=False,
                outreach_generation_mode="llm",
                outreach_template_subject="默认主题 {{name}}",
                outreach_template_body_text="默认正文 {{name}}",
                outreach_template_body_html="<p>默认正文 {{name}}</p>",
            ),
        )

        professor_response = self.client.post(
            "/api/professors",
            json={
                "name": "批量快照导师",
                "email": "batch-snapshot@example.edu",
                "title": "Professor",
                "university": "Example University",
                "school": "School of Computing",
                "department": "Computer Science",
                "research_direction": "Agents",
                "recent_papers": [],
                "profile_url": None,
                "source_url": None,
            },
        )
        professor_id = professor_response.json()["id"]

        response = self.client.post(
            "/api/batch-tasks",
            json={
                "identity_id": identity_id,
                "llm_profile_id": llm_id,
                "name": "批量模板快照",
                "professor_ids": [professor_id],
                "schedule_type": "immediate",
                "window_start_time": None,
                "window_end_time": None,
                "emails_per_window": None,
                "primary_material_id": None,
                "email_subject": None,
                "email_body": None,
                "selected_material_ids": None,
                "outreach_generation_mode": "template",
                "outreach_template_subject": "批量主题 {{name}}",
                "outreach_template_body_text": "批量正文 {{name}}",
                "outreach_template_body_html": "<p>批量正文 {{name}}</p>",
            },
        )
        self.assertEqual(response.status_code, 201, msg=response.text)

        self.client.put(
            f"/api/identities/{identity_id}",
            json=self._build_identity_payload(
                with_imap=False,
                outreach_generation_mode="llm",
                outreach_template_subject="后来改掉的主题",
                outreach_template_body_text="后来改掉的正文 {{name}}",
                outreach_template_body_html="<p>后来改掉的正文 {{name}}</p>",
            ),
        )

        batch_task_id = response.json()["id"]
        batch_items = self.client.get(f"/api/batch-tasks/{batch_task_id}/items").json()
        task_id = next(
            item["id"] for item in batch_items if item["professor_id"] == professor_id
        )
        workspace = self.client.get(
            f"/api/batch-tasks/{batch_task_id}/items/{task_id}/thread"
        )
        self.assertEqual(workspace.status_code, 200, msg=workspace.text)
        payload = workspace.json()
        self.assertEqual(
            payload["current_task"]["outreach_generation_mode"], "template"
        )
        self.assertEqual(
            payload["current_task"]["outreach_template_subject"], "批量主题 {{name}}"
        )
        self.assertEqual(
            payload["current_task"]["outreach_template_body_text"], "批量正文 {{name}}"
        )

    def test_llm_batch_task_prefers_outreach_template_fields_for_snapshot_and_draft(
        self,
    ) -> None:
        identity_id = self._create_identity(with_imap=False)
        llm_id = self._create_llm()
        material_id = self._upload_material(
            identity_id,
            filename="resume.txt",
            content=b"My background covers agent systems and information extraction.",
            material_type="resume",
        )

        update_response = self.client.put(
            f"/api/identities/{identity_id}",
            json=self._build_identity_payload(
                with_imap=False,
                outreach_generation_mode="llm",
                outreach_template_subject="身份默认主题 {{name}}",
                outreach_template_body_text="身份默认正文 {{name}}",
                outreach_template_body_html="<p>身份默认正文 {{name}}</p>",
            ),
        )
        self.assertEqual(update_response.status_code, 200, msg=update_response.text)

        professor_response = self.client.post(
            "/api/professors",
            json={
                "name": "LLM 批量模板导师",
                "email": "llm-batch-template@example.edu",
                "title": "Professor",
                "university": "Example University",
                "school": "School of Computing",
                "department": "Computer Science",
                "research_direction": "Agents",
                "recent_papers": [],
                "profile_url": None,
                "source_url": None,
            },
        )
        self.assertEqual(
            professor_response.status_code, 201, msg=professor_response.text
        )
        professor_id = professor_response.json()["id"]

        batch_subject = "批量润色主题 {{name}}"
        batch_body_text = "批量润色正文 {{name}}"
        batch_body_html = "<p>批量润色正文 {{name}}</p>"

        create_response = self.client.post(
            "/api/batch-tasks",
            json={
                "identity_id": identity_id,
                "llm_profile_id": llm_id,
                "name": "LLM 批量模板快照",
                "professor_ids": [professor_id],
                "schedule_type": "immediate",
                "window_start_time": None,
                "window_end_time": None,
                "emails_per_window": None,
                "primary_material_id": material_id,
                "email_subject": None,
                "email_body": None,
                "selected_material_ids": None,
                "outreach_generation_mode": "llm",
                "outreach_template_subject": batch_subject,
                "outreach_template_body_text": batch_body_text,
                "outreach_template_body_html": batch_body_html,
            },
        )
        self.assertEqual(create_response.status_code, 201, msg=create_response.text)
        self.assertEqual(create_response.json()["email_subject"], batch_subject)

        batch_task_id = create_response.json()["id"]
        batch_items = self.client.get(f"/api/batch-tasks/{batch_task_id}/items").json()
        task_id = next(
            item["id"] for item in batch_items if item["professor_id"] == professor_id
        )
        workspace_before_generate = self.client.get(
            f"/api/batch-tasks/{batch_task_id}/items/{task_id}/thread"
        )
        self.assertEqual(
            workspace_before_generate.status_code,
            200,
            msg=workspace_before_generate.text,
        )
        task_before_generate = workspace_before_generate.json()["current_task"]
        self.assertEqual(
            task_before_generate["outreach_template_subject"], batch_subject
        )
        self.assertEqual(
            task_before_generate["outreach_template_body_text"], batch_body_text
        )
        self.assertEqual(
            task_before_generate["outreach_template_body_html"], batch_body_html
        )

        async def _fake_generate_draft_content(**kwargs):
            self.assertEqual(kwargs["custom_subject"], batch_subject)
            self.assertEqual(kwargs["custom_body"], batch_body_text)
            self.assertEqual(kwargs["custom_body_html"], batch_body_html)
            self.assertEqual(kwargs["max_tokens"], 6000)
            return self._build_draft_generation_result(
                subject=f"润色后: {kwargs['custom_subject']}",
                body_text=f"润色后正文: {kwargs['custom_body']}",
                body_html=f"<p>润色后正文: {kwargs['custom_body']}</p>",
            )

        with patch(
            "app.modules.workspace.tasks.runtime.llm_runtime.generate_draft_content",
            AsyncMock(side_effect=_fake_generate_draft_content),
        ) as mocked_generate:
            generate_response = self.client.post(
                f"/api/email-tasks/{task_before_generate['id']}/generate-draft",
            )

        self.assertEqual(generate_response.status_code, 200, msg=generate_response.text)
        generated_thread = self.client.get(
            f"/api/batch-tasks/{batch_task_id}/items/{task_before_generate['id']}/thread"
        ).json()
        generated_task = generated_thread["current_task"]
        self.assertEqual(
            generated_task["generated_subject"], f"润色后: {batch_subject}"
        )
        self.assertEqual(
            generated_task["generated_content_text"], f"润色后正文: {batch_body_text}"
        )
        mocked_generate.assert_awaited_once()

    def test_paused_batch_requires_explicit_replacement_after_model_retirement(
        self,
    ) -> None:
        identity_id = self._create_identity(with_imap=False)
        retired_llm_id = self._create_llm(name="活动旧模型")
        replacement_id = self._create_llm(name="活动替代模型")
        professor_id = self._create_professor(email="retired-batch@example.edu")
        with closing(sqlite3.connect(self.db_path)) as connection, connection:
            batch_id = connection.execute(
                """
                INSERT INTO batch_tasks (
                    identity_id, llm_profile_id, name, status, target_count,
                    outreach_generation_mode, selected_material_ids
                )
                VALUES (?, ?, '暂停活动', 'paused', 1, 'llm', '[]')
                RETURNING id
                """,
                (identity_id, retired_llm_id),
            ).fetchone()[0]
            connection.commit()
        email_task_id = self._insert_email_task_with_material(
            identity_id=identity_id,
            llm_id=retired_llm_id,
            professor_id=professor_id,
            status="draft_failed",
            primary_material_id=None,
            batch_task_id=batch_id,
            source="batch",
            outreach_generation_mode="llm",
        )

        impact = self.client.get(
            f"/api/llm-profiles/{retired_llm_id}/deletion-impact"
        ).json()
        self.assertTrue(impact["can_delete"])
        retired = self.client.delete(
            f"/api/llm-profiles/{retired_llm_id}",
            params={"impact_revision": impact["revision"]},
        )
        self.assertEqual(retired.status_code, 200, msg=retired.text)

        blocked = self.client.post(f"/api/batch-tasks/{batch_id}/resume")
        self.assertEqual(blocked.status_code, 409, msg=blocked.text)
        self.assertEqual(
            blocked.json()["detail"]["code"],
            "CAMPAIGN_LLM_PROFILE_REPLACEMENT_REQUIRED",
        )
        resumed = self.client.post(
            f"/api/batch-tasks/{batch_id}/resume",
            params={"replacement_llm_profile_id": replacement_id},
        )
        self.assertEqual(resumed.status_code, 200, msg=resumed.text)
        with closing(sqlite3.connect(self.db_path)) as connection, connection:
            batch_profile_id = connection.execute(
                "SELECT llm_profile_id FROM batch_tasks WHERE id = ?",
                (batch_id,),
            ).fetchone()[0]
            item_profile_id = connection.execute(
                "SELECT llm_profile_id FROM email_tasks WHERE id = ?",
                (email_task_id,),
            ).fetchone()[0]
        self.assertEqual(
            (batch_profile_id, item_profile_id), (replacement_id, replacement_id)
        )
