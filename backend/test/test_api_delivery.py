from __future__ import annotations

import asyncio
import os
import sqlite3
import subprocess
import sys
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient
from sqlalchemy import inspect

from app.core.migrations import get_alembic_config, get_head_revision

BACKEND_DIR = Path(__file__).resolve().parents[1]
HEAD_REVISION = get_head_revision(get_alembic_config())


from test.api_fixture import ApiFixture


class DeliveryApiTests(ApiFixture):
    def test_legacy_mode_only_task_can_fall_back_to_global_default_template(
        self,
    ) -> None:
        identity_payload = self._build_identity_payload(
            with_imap=False,
            outreach_template_subject=None,
            outreach_template_body_text=None,
            outreach_template_body_html=None,
        )
        identity_payload["default_outreach_template_id"] = None
        identity_response = self.client.post("/api/identities", json=identity_payload)
        self.assertEqual(identity_response.status_code, 201, msg=identity_response.text)
        identity_id = identity_response.json()["id"]
        llm_id = self._create_llm()
        professor_id = self._create_professor(
            email="legacy-global-template@example.edu"
        )
        template_response = self.client.post(
            "/api/outreach-templates",
            json={
                "name": "旧任务全局回退模板",
                "recommended_generation_mode": "template",
                "subject": "旧任务全局主题 {{name}}",
                "body_text": "旧任务全局正文 {{sender_name}}",
                "body_html": "<p>旧任务全局正文 {{sender_name}}</p>",
                "is_default": True,
            },
        )
        self.assertEqual(template_response.status_code, 201, msg=template_response.text)
        template_id = template_response.json()["id"]
        task_id = self._insert_email_task_with_material(
            identity_id=identity_id,
            llm_id=llm_id,
            professor_id=professor_id,
            status="matched",
            primary_material_id=None,
            outreach_generation_mode="template",
        )

        workspace_response = self.client.get(
            f"/api/workspaces/{professor_id}",
            params={"identity_id": identity_id, "llm_profile_id": llm_id},
        )
        self.assertEqual(
            workspace_response.status_code, 200, msg=workspace_response.text
        )
        workspace_task = workspace_response.json()["current_task"]
        self.assertEqual(workspace_task["id"], task_id)
        self.assertIsNone(workspace_task["outreach_template_id"])
        self.assertEqual(
            workspace_task["outreach_template_subject"],
            "旧任务全局主题 {{name}}",
        )

        generate_response = self.client.post(
            f"/api/email-tasks/{task_id}/generate-draft",
        )
        self.assertEqual(generate_response.status_code, 200, msg=generate_response.text)
        generated = generate_response.json()["current_task"]
        self.assertEqual(
            generated["generated_subject"],
            "旧任务全局主题 材料删除测试导师",
        )
        self.assertEqual(generated["generated_content_text"], "旧任务全局正文 测试身份")

        connection = sqlite3.connect(self.db_path)
        try:
            frozen_snapshot = connection.execute(
                """
                SELECT outreach_template_id, outreach_template_snapshot_version,
                       outreach_template_subject, outreach_template_body_text
                FROM email_tasks
                WHERE id = ?
                """,
                (task_id,),
            ).fetchone()
        finally:
            connection.close()
        self.assertEqual(
            frozen_snapshot,
            (
                template_id,
                1,
                "旧任务全局主题 {{name}}",
                "旧任务全局正文 {{sender_name}}",
            ),
        )

    def test_template_generation_does_not_rebind_task_to_selected_model(self) -> None:
        identity_id = self._create_identity(with_imap=False)
        first_llm_id = self._create_llm(
            name="原任务模型", model_name="gpt-original-template"
        )
        second_llm_id = self._create_llm(
            name="当前选择模型", model_name="gpt-selected-template"
        )
        professor_id = self._create_professor(email="template-model-switch@example.edu")
        task_id = self._insert_email_task_with_material(
            identity_id=identity_id,
            llm_id=first_llm_id,
            professor_id=professor_id,
            status="matched",
            primary_material_id=None,
            outreach_generation_mode="template",
        )

        response = self.client.post(
            f"/api/email-tasks/{task_id}/generate-draft",
            json={"llm_profile_id": second_llm_id},
        )

        self.assertEqual(response.status_code, 200, msg=response.text)
        payload = response.json()
        self.assertEqual(payload["llm_profile"]["id"], first_llm_id)
        self.assertEqual(payload["current_task"]["id"], task_id)
        self.assertEqual(payload["current_task"]["status"], "review_required")
        self.assertEqual(self._get_email_task_llm_profile_id(task_id), first_llm_id)

    def test_save_send_and_schedule_reject_generating_rewrite(self) -> None:
        task_id = self._create_generating_workspace_rewrite_task()
        payload = {
            "subject": "主题",
            "body_text": "正文",
            "body_html": "<p>正文</p>",
            "selected_material_ids": [],
        }

        save_response = self.client.post(
            f"/api/email-tasks/{task_id}/save-draft", json=payload
        )
        send_response = self.client.post(
            f"/api/email-tasks/{task_id}/approve-and-send", json=payload
        )
        schedule_response = self.client.post(
            f"/api/email-tasks/{task_id}/approve-and-schedule",
            json={**payload, "scheduled_at": "2030-01-01T10:00:00+00:00"},
        )

        self.assertEqual(save_response.status_code, 400, msg=save_response.text)
        self.assertEqual(send_response.status_code, 400, msg=send_response.text)
        self.assertEqual(schedule_response.status_code, 400, msg=schedule_response.text)
        self.assertEqual(
            save_response.json()["detail"], "AI 正在改写当前草稿，请等待完成后再保存。"
        )
        self.assertEqual(
            send_response.json()["detail"], "AI 正在改写当前草稿，请等待完成后再发送。"
        )
        self.assertEqual(
            schedule_response.json()["detail"],
            "AI 正在改写当前草稿，请等待完成后再发送。",
        )

    def test_app_startup_auto_upgrades_stale_database(self) -> None:
        stale_dir = tempfile.TemporaryDirectory()
        stale_db_path = Path(stale_dir.name) / "stale.db"
        stale_env = os.environ.copy()
        stale_env["DATABASE_URL"] = f"sqlite+aiosqlite:///{stale_db_path.as_posix()}"
        stale_env["ENABLE_BACKGROUND_WORKERS"] = "0"

        result = subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", "c52f8b7d1f43"],
            cwd=BACKEND_DIR,
            env=stale_env,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, msg=result.stderr)

        from app.core.config import get_settings
        from app.core.database import dispose_engine, get_engine, get_session_factory
        from main import create_app

        if get_engine.cache_info().currsize:
            asyncio.run(dispose_engine())
        get_session_factory.cache_clear()
        get_settings.cache_clear()
        os.environ["DATABASE_URL"] = stale_env["DATABASE_URL"]

        with TestClient(create_app()) as client:
            response = client.get("/api/ping")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["status"], "ok")

        connection = sqlite3.connect(stale_db_path)
        try:
            version = connection.execute(
                "SELECT version_num FROM alembic_version",
            ).fetchone()[0]
        finally:
            connection.close()

        self.assertEqual(version, HEAD_REVISION)

        if get_engine.cache_info().currsize:
            asyncio.run(dispose_engine())
        get_session_factory.cache_clear()
        get_settings.cache_clear()
        stale_dir.cleanup()

    def test_canceled_scheduled_item_cannot_be_restored_after_original_time(
        self,
    ) -> None:
        batch_task_id = self._create_scheduled_template_batch()
        item = self.client.get(f"/api/batch-tasks/{batch_task_id}/items").json()[0]
        canceled = self.client.post(
            f"/api/batch-tasks/{batch_task_id}/items/{item['id']}/cancel-send",
        )
        self.assertEqual(canceled.status_code, 200, msg=canceled.text)

        connection = sqlite3.connect(self.db_path)
        try:
            connection.execute(
                "UPDATE email_tasks SET scheduled_at = ? WHERE id = ?",
                ((datetime.now(UTC) - timedelta(minutes=1)).isoformat(), item["id"]),
            )
            connection.commit()
        finally:
            connection.close()

        restored = self.client.post(
            f"/api/batch-tasks/{batch_task_id}/items/{item['id']}/restore-send",
        )

        self.assertEqual(restored.status_code, 400, msg=restored.text)
        self.assertEqual(restored.json()["detail"], "原定发送时间已过，无法恢复发送")
        expired_item = self.client.get(
            f"/api/batch-tasks/{batch_task_id}/items"
        ).json()[0]
        self.assertIsNotNone(expired_item["batch_send_canceled_at"])
        self.assertFalse(expired_item["can_restore_send"])
        task = next(
            item
            for item in self.client.get("/api/batch-tasks").json()
            if item["id"] == batch_task_id
        )
        self.assertEqual(task["status"], "completed")
        self.assertEqual(task["completed_count"], 1)
        self.assertEqual(task["canceled_send_count"], 1)

    def test_create_and_list_match_analysis_jobs(self) -> None:
        identity_id = self._create_identity(with_imap=False)
        llm_id = self._create_llm()
        self._upload_material(
            identity_id,
            filename="resume.txt",
            content=b"AI systems",
            material_type="resume",
        )
        professor_response = self.client.post(
            "/api/professors",
            json={
                "name": "王老师",
                "email": "wang-match@example.edu",
                "title": "Professor",
                "university": "Example University",
                "school": "School of Computing",
                "department": "Computer Science",
                "research_direction": "AI agents",
                "recent_papers": [],
                "profile_url": None,
                "source_url": None,
            },
        )
        self.assertEqual(
            professor_response.status_code, 201, msg=professor_response.text
        )
        professor_id = professor_response.json()["id"]

        created = self.client.post(
            "/api/match-analysis-jobs",
            json={
                "identity_id": identity_id,
                "llm_profile_id": llm_id,
                "professor_ids": [professor_id],
            },
        )
        self.assertEqual(created.status_code, 201, msg=created.text)
        self.assertEqual(created.json()["target_count"], 1)

        listed = self.client.get(
            "/api/match-analysis-jobs",
            params={"identity_id": identity_id, "llm_profile_id": llm_id},
        )
        self.assertEqual(listed.status_code, 200)
        self.assertEqual(len(listed.json()), 1)

        from app.modules.matching.public import (
            serialize_match_analysis_job_item,
        )

        unloaded_professor_columns: list[set[str]] = []

        def capture_match_item_projection(item):
            unloaded_professor_columns.append(inspect(item.professor).unloaded)
            return serialize_match_analysis_job_item(item)

        with patch(
            "app.modules.matching.api.serialize_match_analysis_job_item",
            side_effect=capture_match_item_projection,
        ):
            items = self.client.get(
                f"/api/match-analysis-jobs/{created.json()['id']}/items",
            )
        self.assertEqual(items.status_code, 200, msg=items.text)
        self.assertEqual(items.json()["total_count"], 1)
        self.assertFalse(items.json()["has_more"])
        self.assertEqual(
            items.json()["items"][0]["professor_university"], "Example University"
        )
        self.assertEqual(
            items.json()["items"][0]["professor_school"], "School of Computing"
        )
        self.assertIn("recent_papers", unloaded_professor_columns[0])

        filtered_items = self.client.get(
            f"/api/match-analysis-jobs/{created.json()['id']}/items",
            params={"cursor": 0, "limit": 1, "status": "succeeded"},
        )
        self.assertEqual(filtered_items.status_code, 200, msg=filtered_items.text)
        self.assertEqual(filtered_items.json()["total_count"], 0)
        self.assertEqual(filtered_items.json()["items"], [])

    def test_match_analysis_job_delete_restore_and_trash_view(self) -> None:
        identity_id = self._create_identity(with_imap=False)
        llm_id = self._create_llm()
        self._upload_material(
            identity_id,
            filename="resume.txt",
            content=b"AI systems",
            material_type="resume",
        )
        professor_response = self.client.post(
            "/api/professors",
            json={
                "name": "回收站导师",
                "email": "trash-match@example.edu",
                "title": "Professor",
                "university": "Example University",
                "school": "School of Computing",
                "department": "Computer Science",
                "research_direction": "AI agents",
                "recent_papers": ["Agent paper"],
                "profile_url": None,
                "source_url": None,
            },
        )
        self.assertEqual(
            professor_response.status_code, 201, msg=professor_response.text
        )
        created = self.client.post(
            "/api/match-analysis-jobs",
            json={
                "identity_id": identity_id,
                "llm_profile_id": llm_id,
                "professor_ids": [professor_response.json()["id"]],
            },
        )
        self.assertEqual(created.status_code, 201, msg=created.text)
        job_id = created.json()["id"]

        deleted = self.client.post(f"/api/match-analysis-jobs/{job_id}/delete")
        self.assertEqual(deleted.status_code, 200, msg=deleted.text)
        self.assertEqual(deleted.json()["job"]["status"], "canceled")
        self.assertIsNotNone(deleted.json()["job"]["cancel_requested_at"])
        self.assertIsNotNone(deleted.json()["job"]["deleted_at"])

        repeated_delete = self.client.post(f"/api/match-analysis-jobs/{job_id}/delete")
        self.assertEqual(repeated_delete.status_code, 200, msg=repeated_delete.text)

        current = self.client.get(
            "/api/match-analysis-jobs",
            params={"identity_id": identity_id, "llm_profile_id": llm_id},
        )
        self.assertEqual(current.status_code, 200)
        self.assertEqual(current.json(), [])

        trash = self.client.get(
            "/api/match-analysis-jobs",
            params={
                "identity_id": identity_id,
                "llm_profile_id": llm_id,
                "view": "trash",
            },
        )
        self.assertEqual(trash.status_code, 200)
        self.assertEqual([item["id"] for item in trash.json()], [job_id])

        restored = self.client.post(f"/api/match-analysis-jobs/{job_id}/restore")
        self.assertEqual(restored.status_code, 200, msg=restored.text)
        self.assertIsNone(restored.json()["job"]["deleted_at"])
        self.assertEqual(restored.json()["job"]["status"], "canceled")

        repeated_restore = self.client.post(
            f"/api/match-analysis-jobs/{job_id}/restore"
        )
        self.assertEqual(repeated_restore.status_code, 200, msg=repeated_restore.text)

    def test_cancel_match_analysis_job(self) -> None:
        identity_id = self._create_identity(with_imap=False)
        llm_id = self._create_llm()
        self._upload_material(
            identity_id,
            filename="resume.txt",
            content=b"AI systems",
            material_type="resume",
        )
        professor_response = self.client.post(
            "/api/professors",
            json={
                "name": "取消任务导师",
                "email": "cancel-job@example.edu",
                "title": "Professor",
                "university": "Example University",
                "school": "School of Computing",
                "department": "Computer Science",
                "research_direction": "AI agents",
                "recent_papers": [],
                "profile_url": None,
                "source_url": None,
            },
        )
        self.assertEqual(
            professor_response.status_code, 201, msg=professor_response.text
        )
        professor_id = professor_response.json()["id"]
        created = self.client.post(
            "/api/match-analysis-jobs",
            json={
                "identity_id": identity_id,
                "llm_profile_id": llm_id,
                "professor_ids": [professor_id],
            },
        )
        self.assertEqual(created.status_code, 201, msg=created.text)
        job_id = created.json()["id"]

        canceled = self.client.post(f"/api/match-analysis-jobs/{job_id}/cancel")
        self.assertEqual(canceled.status_code, 200, msg=canceled.text)
        self.assertTrue(canceled.json()["ok"])
        self.assertEqual(canceled.json()["job"]["status"], "canceled")

    def test_retry_failed_match_analysis_job_returns_400_when_no_failed_items(
        self,
    ) -> None:
        identity_id = self._create_identity(with_imap=False)
        llm_id = self._create_llm()
        self._upload_material(
            identity_id,
            filename="resume.txt",
            content=b"AI systems",
            material_type="resume",
        )
        professor_response = self.client.post(
            "/api/professors",
            json={
                "name": "重试任务导师",
                "email": "retry-job@example.edu",
                "title": "Professor",
                "university": "Example University",
                "school": "School of Computing",
                "department": "Computer Science",
                "research_direction": "AI agents",
                "recent_papers": [],
                "profile_url": None,
                "source_url": None,
            },
        )
        self.assertEqual(
            professor_response.status_code, 201, msg=professor_response.text
        )
        professor_id = professor_response.json()["id"]
        created = self.client.post(
            "/api/match-analysis-jobs",
            json={
                "identity_id": identity_id,
                "llm_profile_id": llm_id,
                "professor_ids": [professor_id],
            },
        )
        self.assertEqual(created.status_code, 201, msg=created.text)
        job_id = created.json()["id"]

        retried = self.client.post(f"/api/match-analysis-jobs/{job_id}/retry-failed")
        self.assertEqual(retried.status_code, 400)
        self.assertIn("没有可重试的失败项", retried.json()["detail"])

    def test_calculate_match_keeps_low_score_task_in_matched_state(self) -> None:
        identity_id = self._create_identity(with_imap=False)
        llm_id = self._create_llm()
        material_id = self._upload_material(
            identity_id,
            filename="resume.txt",
            content=b"My background covers information extraction and agents.",
            material_type="resume",
        )
        set_primary_response = self.client.post(
            f"/api/materials/{material_id}/set-primary"
        )
        self.assertEqual(
            set_primary_response.status_code, 200, msg=set_primary_response.text
        )

        professor_response = self.client.post(
            "/api/professors",
            json={
                "name": "低分匹配导师",
                "email": "low-score-match@example.edu",
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

        connection = sqlite3.connect(self.db_path)
        try:
            connection.execute(
                """
                UPDATE identity_profiles
                SET match_threshold = ?
                WHERE id = ?
                """,
                (95, identity_id),
            )
            connection.commit()
        finally:
            connection.close()

        ensure_response = self.client.post(
            f"/api/workspaces/{professor_id}/ensure-task",
            params={"identity_id": identity_id, "llm_profile_id": llm_id},
        )
        self.assertEqual(ensure_response.status_code, 200, msg=ensure_response.text)
        task_id = ensure_response.json()["current_task"]["id"]

        with patch(
            "app.modules.matching.task_analysis.llm_runtime.generate_match_evaluation",
            AsyncMock(return_value=self._build_match_evaluation_result(match_score=18)),
        ):
            response = self.client.post(f"/api/email-tasks/{task_id}/calculate-match")

        self.assertEqual(response.status_code, 200, msg=response.text)
        self.assertEqual(response.json()["thread"]["current_task"]["match_score"], 18)
        self.assertEqual(response.json()["thread"]["current_task"]["status"], "matched")

    def test_calculate_match_returns_409_when_run_is_already_running(self) -> None:
        from app.modules.matching.public import MatchAnalysisAlreadyRunningError

        with patch(
            "app.modules.workspace.tasks.api.calculate_task_match_once",
            AsyncMock(side_effect=MatchAnalysisAlreadyRunningError("该任务正在分析中")),
        ):
            response = self.client.post("/api/email-tasks/1/calculate-match")

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["detail"], "该任务正在分析中")

    def test_schedule_and_reply_detection_use_sent_message_id(self) -> None:
        identity_id = self._create_identity(with_imap=True)
        llm_id = self._create_llm()
        self.client.post("/api/professors/import-sample")
        professor = self.client.get("/api/professors").json()[0]
        professor_id = professor["id"]
        professor_email = professor["email"]
        response = self.client.post(
            "/api/batch-tasks",
            json={
                "identity_id": identity_id,
                "llm_profile_id": llm_id,
                "name": "回信跟踪任务",
                "professor_ids": [professor_id],
                "schedule_type": "immediate",
                "window_start_time": None,
                "window_end_time": None,
                "emails_per_window": None,
                "primary_material_id": None,
                "email_subject": "申请与{{name}}老师交流",
                "email_body": "老师您好，我是{{sender_name}}，后续会手动整理并发送这封邮件。",
                "selected_material_ids": None,
            },
        )

        self.assertEqual(response.status_code, 201, msg=response.text)
        batch_task_id = response.json()["id"]
        task_id = self.client.get(f"/api/batch-tasks/{batch_task_id}/items").json()[0][
            "id"
        ]

        schedule_time = datetime.now(UTC) + timedelta(hours=1)
        with patch(
            "app.modules.workspace.tasks.delivery.mail_runtime.send_email",
            AsyncMock(
                return_value=self._build_send_result(
                    message_id="<msg-1@example.com>",
                    provider_payload={"smtp_host": "smtp.example.com"},
                ),
            ),
        ):
            schedule_response = self.client.post(
                f"/api/email-tasks/{task_id}/approve-and-schedule",
                json={
                    "subject": "套磁申请",
                    "body_text": "老师您好，我计划在稍后发送这封邮件。",
                    "body_html": None,
                    "selected_material_ids": [],
                    "scheduled_at": schedule_time.isoformat(),
                },
            )
            self.assertEqual(schedule_response.status_code, 200)

            self._run_async(self._force_task_due(task_id))
            self._run_async(self._dispatch_due_tasks())

        sent_workspace = self.client.get(
            f"/api/batch-tasks/{batch_task_id}/items/{task_id}/thread"
        ).json()
        self.assertEqual(sent_workspace["current_task"]["status"], "sent")
        self.assertEqual(
            sent_workspace["current_task"]["last_rfc_message_id"], "<msg-1@example.com>"
        )

        reply_sent_at = datetime(2026, 5, 1, 8, 0, tzinfo=UTC)
        reply_received_at = datetime(2026, 5, 1, 8, 30, tzinfo=UTC)
        with patch(
            "app.modules.communications.imap.sync.mail_runtime.fetch_professor_history_inbox_messages",
            AsyncMock(
                return_value=[
                    self._build_imap_fetched_message(
                        from_email=professor_email,
                        subject="Re: 套磁申请",
                        content="谢谢来信，我们可以进一步聊聊。",
                        message_id="<reply-1@example.com>",
                        in_reply_to="<msg-1@example.com>",
                        sent_at=reply_sent_at,
                        received_at=reply_received_at,
                    ),
                ],
            ),
        ):
            refresh_response = self.client.post(
                f"/api/workspaces/{professor_id}/refresh-replies",
                params={"identity_id": identity_id, "llm_profile_id": llm_id},
            )
            self.assertEqual(
                refresh_response.status_code, 200, msg=refresh_response.text
            )

        replied_workspace = self.client.get(f"/api/email-tasks/{task_id}/thread").json()
        self.assertEqual(replied_workspace["current_task"]["status"], "reply_detected")
        self.assertTrue(replied_workspace["current_task"]["is_replied"])
        received_message = next(
            message
            for message in replied_workspace["messages"]
            if message["direction"] == "received"
        )
        received_created_at = datetime.fromisoformat(
            received_message["created_at"].replace("Z", "+00:00"),
        )
        if received_created_at.tzinfo is None:
            received_created_at = received_created_at.replace(tzinfo=UTC)
        self.assertEqual(received_created_at, reply_received_at)

        wrong_refresh_time = datetime(2026, 5, 9, 0, 2, tzinfo=UTC)
        connection = sqlite3.connect(self.db_path)
        try:
            connection.execute(
                "UPDATE email_logs SET created_at = ? WHERE rfc_message_id = ?",
                (wrong_refresh_time.isoformat(), "<reply-1@example.com>"),
            )
            connection.commit()
        finally:
            connection.close()

        with patch(
            "app.modules.communications.imap.sync.mail_runtime.fetch_professor_history_inbox_messages",
            AsyncMock(
                return_value=[
                    self._build_imap_fetched_message(
                        from_email=professor_email,
                        subject="Re: 套磁申请",
                        content="谢谢来信，我们可以进一步聊聊。",
                        message_id="<reply-1@example.com>",
                        in_reply_to="<msg-1@example.com>",
                        sent_at=reply_sent_at,
                        received_at=reply_received_at,
                    ),
                ],
            ),
        ):
            repair_response = self.client.post(
                f"/api/workspaces/{professor_id}/refresh-replies",
                params={"identity_id": identity_id, "llm_profile_id": llm_id},
            )
            self.assertEqual(repair_response.status_code, 200, msg=repair_response.text)

        repaired_workspace = self.client.get(
            f"/api/workspaces/{professor_id}",
            params={"identity_id": identity_id, "llm_profile_id": llm_id},
        ).json()
        repaired_received_message = next(
            message
            for message in repaired_workspace["messages"]
            if message["direction"] == "received"
        )
        repaired_created_at = datetime.fromisoformat(
            repaired_received_message["created_at"].replace("Z", "+00:00"),
        )
        if repaired_created_at.tzinfo is None:
            repaired_created_at = repaired_created_at.replace(tzinfo=UTC)
        self.assertEqual(repaired_created_at, reply_received_at)
