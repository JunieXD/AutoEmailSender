from __future__ import annotations

import asyncio
import json
import sqlite3
from contextlib import closing
from pathlib import Path
from unittest.mock import AsyncMock, patch

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.migrations import get_alembic_config, get_head_revision
from app.modules.llm.runtime import LLMRuntimeAdaptation

BACKEND_DIR = Path(__file__).resolve().parents[1]
HEAD_REVISION = get_head_revision(get_alembic_config())


from test.api_fixture import ApiFixture


class WorkspaceApiTests(ApiFixture):
    def test_identity_update_allows_incomplete_template_defaults(self) -> None:
        cases = [
            {
                "name": "只缺主题",
                "subject": None,
                "body_text": "老师您好，我是{{sender_name}}。",
                "body_html": "<p>老师您好，我是{{sender_name}}。</p>",
            },
            {
                "name": "只缺纯文本正文",
                "subject": "申请与{{name}}老师交流",
                "body_text": None,
                "body_html": "<p>老师您好，我是{{sender_name}}。</p>",
            },
            {
                "name": "主题和纯文本正文都缺",
                "subject": None,
                "body_text": None,
                "body_html": "<p>老师您好，我是{{sender_name}}。</p>",
            },
        ]

        for case in cases:
            with self.subTest(case=case["name"]):
                unique_email = f"sender-update-{case['name']}@example.com"
                create_payload = self._build_identity_payload(
                    with_imap=False,
                    outreach_generation_mode="llm",
                    outreach_template_subject="申请与{{name}}老师交流",
                    outreach_template_body_text="老师您好，我是{{sender_name}}，关注到您在{{research_direction}}方向的工作。",
                )
                create_payload["email_address"] = unique_email
                create_payload["smtp_username"] = unique_email
                create_response = self.client.post(
                    "/api/identities", json=create_payload
                )
                self.assertEqual(
                    create_response.status_code, 201, msg=create_response.text
                )
                identity_id = create_response.json()["id"]
                update_payload = self._build_identity_payload(
                    with_imap=False,
                    outreach_generation_mode="llm",
                    outreach_template_subject=case["subject"],
                    outreach_template_body_text=case["body_text"],
                    outreach_template_body_html=case["body_html"],
                )
                update_payload["email_address"] = unique_email
                update_payload["smtp_username"] = unique_email
                response = self.client.put(
                    f"/api/identities/{identity_id}",
                    json=update_payload,
                )

                self.assertEqual(response.status_code, 200, msg=response.text)
                self.assertEqual(
                    response.json()["outreach_template_subject"], case["subject"]
                )
                self.assertEqual(
                    response.json()["outreach_template_body_text"], case["body_text"]
                )
                self.assertEqual(
                    response.json()["outreach_template_body_html"], case["body_html"]
                )

    def test_outreach_template_library_saves_drafts_without_identity(self) -> None:
        response = self.client.post(
            "/api/outreach-templates",
            json={
                "name": "尚未完成的英文模板",
                "recommended_generation_mode": "llm",
                "subject": None,
                "body_text": "Dear {{name}},",
                "body_html": "<p>Dear {{name}},</p>",
                "is_default": True,
            },
        )

        self.assertEqual(response.status_code, 201, msg=response.text)
        created = response.json()
        self.assertFalse(created["is_ready"])
        self.assertTrue(created["is_default"])
        self.assertEqual(created["body_text"], "Dear {{name}},")

        duplicate = self.client.post(
            f"/api/outreach-templates/{created['id']}/duplicate",
        )
        self.assertEqual(duplicate.status_code, 201, msg=duplicate.text)
        self.assertFalse(duplicate.json()["is_default"])

        templates = self.client.get("/api/outreach-templates").json()
        self.assertEqual(len(templates), 2)
        self.assertEqual(templates[0]["id"], created["id"])

    def test_outreach_template_defaults_switch_safely_and_duplicate_names_fit_limit(
        self,
    ) -> None:
        first_response = self.client.post(
            "/api/outreach-templates",
            json={
                "name": "模" * 120,
                "recommended_generation_mode": "llm",
                "is_default": True,
            },
        )
        self.assertEqual(first_response.status_code, 201, msg=first_response.text)
        first = first_response.json()

        duplicate_response = self.client.post(
            f"/api/outreach-templates/{first['id']}/duplicate",
        )
        self.assertEqual(
            duplicate_response.status_code, 201, msg=duplicate_response.text
        )
        duplicate = duplicate_response.json()
        self.assertEqual(len(duplicate["name"]), 120)
        self.assertTrue(duplicate["name"].endswith("（副本）"))

        second_response = self.client.post(
            "/api/outreach-templates",
            json={
                "name": "第二份全局默认模板",
                "recommended_generation_mode": "template",
                "subject": "主题",
                "body_text": "正文",
                "is_default": True,
            },
        )
        self.assertEqual(second_response.status_code, 201, msg=second_response.text)
        second = second_response.json()
        defaults = [
            template
            for template in self.client.get("/api/outreach-templates").json()
            if template["is_default"]
        ]
        self.assertEqual([template["id"] for template in defaults], [second["id"]])

        reset_response = self.client.post(
            f"/api/outreach-templates/{first['id']}/default",
        )
        self.assertEqual(reset_response.status_code, 200, msg=reset_response.text)
        defaults = [
            template
            for template in self.client.get("/api/outreach-templates").json()
            if template["is_default"]
        ]
        self.assertEqual([template["id"] for template in defaults], [first["id"]])

    def test_identity_retirement_cancels_reversible_draft_and_failed_work(
        self,
    ) -> None:
        identity_id = self._create_identity(
            with_imap=False,
            email_address="retire-reversible-work@example.com",
        )
        llm_id = self._create_llm(name="身份删除可取消工作模型")
        professor_ids = [
            self._create_professor(email="retire-generating@example.edu"),
            self._create_professor(email="retire-send-failed@example.edu"),
        ]
        generating_task_id = self._insert_email_task_with_material(
            identity_id=identity_id,
            llm_id=llm_id,
            professor_id=professor_ids[0],
            status="generating_draft",
            primary_material_id=None,
        )
        failed_task_id = self._insert_email_task_with_material(
            identity_id=identity_id,
            llm_id=llm_id,
            professor_id=professor_ids[1],
            status="send_failed",
            primary_material_id=None,
        )
        with closing(sqlite3.connect(self.db_path)) as connection, connection:
            connection.execute(
                """
                UPDATE email_tasks
                SET draft_generation_previous_status = 'matched',
                    draft_claim_id = 'identity-retirement-claim',
                    draft_claimed_at = CURRENT_TIMESTAMP,
                    draft_lease_expires_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (generating_task_id,),
            )
            connection.commit()

        impact = self.client.get(
            f"/api/identities/{identity_id}/deletion-impact"
        ).json()
        self.assertTrue(impact["can_delete"])
        self.assertEqual(impact["blockers"], [])
        self.assertEqual(
            set(impact["automatic_actions"]["cancel_email_task_ids"]),
            {generating_task_id, failed_task_id},
        )

        retired = self.client.delete(
            f"/api/identities/{identity_id}",
            params={"impact_revision": impact["revision"]},
        )

        self.assertEqual(retired.status_code, 204, msg=retired.text)
        with closing(sqlite3.connect(self.db_path)) as connection, connection:
            rows = connection.execute(
                """
                SELECT id, status, cancellation_reason, draft_claim_id,
                       draft_claimed_at, draft_lease_expires_at
                FROM email_tasks
                WHERE id IN (?, ?)
                ORDER BY id
                """,
                (generating_task_id, failed_task_id),
            ).fetchall()
        self.assertEqual(
            rows,
            [
                (
                    generating_task_id,
                    "canceled",
                    "identity_retired",
                    None,
                    None,
                    None,
                ),
                (
                    failed_task_id,
                    "canceled",
                    "identity_retired",
                    None,
                    None,
                    None,
                ),
            ],
        )

    def test_retired_identity_history_cannot_create_follow_up_task(self) -> None:
        identity_id = self._create_identity(
            with_imap=False,
            email_address="retired-history-follow-up@example.com",
        )
        llm_id = self._create_llm(name="已删除身份历史派生保护模型")
        professor_id = self._create_professor(
            email="retired-history-follow-up@example.edu"
        )
        task_id = self._insert_email_task_with_material(
            identity_id=identity_id,
            llm_id=llm_id,
            professor_id=professor_id,
            status="sent",
            primary_material_id=None,
        )
        impact = self.client.get(
            f"/api/identities/{identity_id}/deletion-impact"
        ).json()
        retired = self.client.delete(
            f"/api/identities/{identity_id}",
            params={"impact_revision": impact["revision"]},
        )
        self.assertEqual(retired.status_code, 204, msg=retired.text)

        followed_up = self.client.post(f"/api/email-tasks/{task_id}/start-follow-up")

        self.assertEqual(followed_up.status_code, 400, msg=followed_up.text)
        self.assertIn(f"发件身份 #{identity_id} 已删除", followed_up.json()["detail"])
        with closing(sqlite3.connect(self.db_path)) as connection, connection:
            child_count = connection.execute(
                "SELECT COUNT(*) FROM email_tasks WHERE parent_task_id = ?",
                (task_id,),
            ).fetchone()[0]
        self.assertEqual(child_count, 0)

    def test_retired_llm_history_cannot_create_follow_up_task(self) -> None:
        identity_id = self._create_identity(
            with_imap=False,
            email_address="retired-llm-history-follow-up@example.com",
        )
        llm_id = self._create_llm(name="历史跟进待删除模型")
        self._create_llm(name="历史跟进活动模型")
        professor_id = self._create_professor(
            email="retired-llm-history-follow-up@example.edu"
        )
        task_id = self._insert_email_task_with_material(
            identity_id=identity_id,
            llm_id=llm_id,
            professor_id=professor_id,
            status="sent",
            primary_material_id=None,
        )
        impact = self.client.get(f"/api/llm-profiles/{llm_id}/deletion-impact").json()
        retired = self.client.delete(
            f"/api/llm-profiles/{llm_id}",
            params={"impact_revision": impact["revision"]},
        )
        self.assertEqual(retired.status_code, 200, msg=retired.text)

        followed_up = self.client.post(f"/api/email-tasks/{task_id}/start-follow-up")

        self.assertEqual(followed_up.status_code, 400, msg=followed_up.text)
        self.assertIn(
            f"模型配置 #{llm_id} 已删除",
            followed_up.json()["detail"],
        )
        with closing(sqlite3.connect(self.db_path)) as connection, connection:
            child_count = connection.execute(
                "SELECT COUNT(*) FROM email_tasks WHERE parent_task_id = ?",
                (task_id,),
            ).fetchone()[0]
        self.assertEqual(child_count, 0)

    def test_global_default_template_is_snapshotted_by_workspace_and_test_compose(
        self,
    ) -> None:
        identity_response = self.client.post(
            "/api/identities",
            json=self._build_identity_payload(
                with_imap=False,
                outreach_template_subject=None,
                outreach_template_body_text=None,
                outreach_template_body_html=None,
            ),
        )
        self.assertEqual(identity_response.status_code, 201, msg=identity_response.text)
        identity_id = identity_response.json()["id"]
        self.assertIsNone(identity_response.json()["default_outreach_template_id"])
        self.assertFalse(
            identity_response.json()["effective_outreach_template_is_ready"],
        )
        llm_id = self._create_llm()
        professor_id = self._create_professor(email="global-template@example.edu")
        template_response = self.client.post(
            "/api/outreach-templates",
            json={
                "name": "全局回退模板",
                "recommended_generation_mode": "template",
                "subject": "全局主题 {{name}}",
                "body_text": "全局正文 {{sender_name}}",
                "body_html": "<p>全局正文 {{sender_name}}</p>",
                "is_default": True,
            },
        )
        self.assertEqual(template_response.status_code, 201, msg=template_response.text)
        template_id = template_response.json()["id"]
        refreshed_identity = next(
            item
            for item in self.client.get("/api/identities").json()
            if item["id"] == identity_id
        )
        self.assertIsNone(refreshed_identity["default_outreach_template_id"])
        self.assertTrue(
            refreshed_identity["effective_outreach_template_is_ready"],
        )

        workspace_response = self.client.post(
            f"/api/workspaces/{professor_id}/ensure-task",
            params={"identity_id": identity_id, "llm_profile_id": llm_id},
        )
        self.assertEqual(
            workspace_response.status_code, 200, msg=workspace_response.text
        )
        workspace_task = workspace_response.json()["current_task"]
        self.assertEqual(workspace_task["outreach_template_id"], template_id)
        self.assertEqual(
            workspace_task["outreach_template_subject"], "全局主题 {{name}}"
        )
        self.assertEqual(
            workspace_task["outreach_template_body_text"], "全局正文 {{sender_name}}"
        )

        compose_response = self.client.get(
            f"/api/test-compose/{identity_id}/{llm_id}",
        )
        self.assertEqual(compose_response.status_code, 200, msg=compose_response.text)
        compose_draft = compose_response.json()["draft"]
        self.assertEqual(compose_draft["outreach_template_id"], template_id)
        self.assertEqual(compose_draft["subject"], "全局主题 {{name}}")
        self.assertEqual(compose_draft["body_text"], "全局正文 {{sender_name}}")

    def test_workspace_can_select_incomplete_template_until_generation_or_send(
        self,
    ) -> None:
        identity_id = self._create_identity(with_imap=False)
        llm_id = self._create_llm()
        professor_id = self._create_professor(email="draft-template@example.edu")
        ensure_response = self.client.post(
            f"/api/workspaces/{professor_id}/ensure-task",
            params={"identity_id": identity_id, "llm_profile_id": llm_id},
        )
        self.assertEqual(ensure_response.status_code, 200, msg=ensure_response.text)
        task_id = ensure_response.json()["current_task"]["id"]
        template_response = self.client.post(
            "/api/outreach-templates",
            json={
                "name": "待补充的工作台模板",
                "recommended_generation_mode": "template",
                "subject": None,
                "body_text": None,
                "body_html": None,
            },
        )
        self.assertEqual(template_response.status_code, 201, msg=template_response.text)
        template_id = template_response.json()["id"]

        select_response = self.client.post(
            f"/api/email-tasks/{task_id}/outreach-config",
            json={
                "outreach_generation_mode": "template",
                "outreach_template_id": template_id,
                "outreach_template_subject": None,
                "outreach_template_body_text": None,
                "outreach_template_body_html": None,
            },
        )
        self.assertEqual(select_response.status_code, 200, msg=select_response.text)
        selected_task = select_response.json()["current_task"]
        self.assertEqual(selected_task["outreach_template_id"], template_id)
        self.assertIsNone(selected_task["outreach_template_subject"])
        self.assertIsNone(selected_task["outreach_template_body_text"])

        generate_response = self.client.post(
            f"/api/email-tasks/{task_id}/generate-draft",
        )
        self.assertEqual(generate_response.status_code, 400, msg=generate_response.text)
        self.assertIn("主题", generate_response.text)

        unlink_response = self.client.post(
            f"/api/email-tasks/{task_id}/outreach-config",
            json={
                "outreach_generation_mode": "template",
                "outreach_template_id": None,
                "outreach_template_subject": None,
                "outreach_template_body_text": None,
                "outreach_template_body_html": None,
            },
        )
        self.assertEqual(unlink_response.status_code, 200, msg=unlink_response.text)
        unlinked = unlink_response.json()["current_task"]
        self.assertIsNone(unlinked["outreach_template_id"])
        self.assertIsNone(unlinked["outreach_template_subject"])
        self.assertIsNone(unlinked["outreach_template_body_text"])

        connection = sqlite3.connect(self.db_path)
        try:
            snapshot_version = connection.execute(
                "SELECT outreach_template_snapshot_version FROM email_tasks WHERE id = ?",
                (task_id,),
            ).fetchone()[0]
        finally:
            connection.close()
        self.assertEqual(snapshot_version, 1)

        generate_unlinked_response = self.client.post(
            f"/api/email-tasks/{task_id}/generate-draft",
        )
        self.assertEqual(
            generate_unlinked_response.status_code,
            400,
            msg=generate_unlinked_response.text,
        )
        self.assertIn("主题", generate_unlinked_response.text)

    def test_workspace_can_unlink_template_without_losing_task_snapshot(self) -> None:
        identity_id = self._create_identity(with_imap=False)
        llm_id = self._create_llm()
        professor_id = self._create_professor(email="unlinked-template@example.edu")
        template_response = self.client.post(
            "/api/outreach-templates",
            json={
                "name": "待解除关联模板",
                "recommended_generation_mode": "template",
                "subject": "独立旧主题 {{name}}",
                "body_text": "独立旧正文 {{sender_name}}",
                "body_html": "<p>独立旧正文 {{sender_name}}</p>",
            },
        )
        self.assertEqual(template_response.status_code, 201, msg=template_response.text)
        template_id = template_response.json()["id"]
        default_response = self.client.put(
            f"/api/identities/{identity_id}/default-template",
            json={"template_id": template_id},
        )
        self.assertEqual(default_response.status_code, 200, msg=default_response.text)

        ensure_response = self.client.post(
            f"/api/workspaces/{professor_id}/ensure-task",
            params={"identity_id": identity_id, "llm_profile_id": llm_id},
        )
        self.assertEqual(ensure_response.status_code, 200, msg=ensure_response.text)
        task = ensure_response.json()["current_task"]
        task_id = task["id"]
        self.assertEqual(task["outreach_template_id"], template_id)

        generate_response = self.client.post(
            f"/api/email-tasks/{task_id}/generate-draft",
        )
        self.assertEqual(generate_response.status_code, 200, msg=generate_response.text)
        generated_before_unlink = generate_response.json()["current_task"]
        self.assertEqual(
            generated_before_unlink["generated_subject"],
            "独立旧主题 材料删除测试导师",
        )

        update_response = self.client.put(
            f"/api/outreach-templates/{template_id}",
            json={
                "subject": "模板库新主题 {{name}}",
                "body_text": "模板库新正文 {{sender_name}}",
                "body_html": "<p>模板库新正文 {{sender_name}}</p>",
            },
        )
        self.assertEqual(update_response.status_code, 200, msg=update_response.text)

        unlink_response = self.client.post(
            f"/api/email-tasks/{task_id}/outreach-config",
            json={
                "outreach_generation_mode": "template",
                "outreach_template_id": None,
                "outreach_template_subject": task["outreach_template_subject"],
                "outreach_template_body_text": task["outreach_template_body_text"],
                "outreach_template_body_html": task["outreach_template_body_html"],
            },
        )
        self.assertEqual(unlink_response.status_code, 200, msg=unlink_response.text)
        unlinked = unlink_response.json()["current_task"]
        self.assertIsNone(unlinked["outreach_template_id"])
        self.assertEqual(unlinked["outreach_template_subject"], "独立旧主题 {{name}}")
        self.assertEqual(
            unlinked["outreach_template_body_text"], "独立旧正文 {{sender_name}}"
        )
        self.assertEqual(unlinked["status"], generated_before_unlink["status"])
        self.assertEqual(
            unlinked["generated_subject"],
            generated_before_unlink["generated_subject"],
        )
        self.assertEqual(
            unlinked["generated_content_text"],
            generated_before_unlink["generated_content_text"],
        )

    def test_switching_workspace_template_discards_stale_generated_draft(self) -> None:
        identity_id = self._create_identity(with_imap=False)
        llm_id = self._create_llm()
        professor_id = self._create_professor(email="switch-template-draft@example.edu")
        task_id = self._insert_email_task_with_material(
            identity_id=identity_id,
            llm_id=llm_id,
            professor_id=professor_id,
            status="review_required",
            primary_material_id=None,
            generated_subject="旧草稿主题",
            generated_content_text="旧草稿正文",
            generated_content_html="<p>旧草稿正文</p>",
        )
        template_response = self.client.post(
            "/api/outreach-templates",
            json={
                "name": "切换后的固定模板",
                "recommended_generation_mode": "template",
                "subject": "新模板主题 {{name}}",
                "body_text": "新模板正文 {{sender_name}}",
                "body_html": "<p>新模板正文 {{sender_name}}</p>",
            },
        )
        self.assertEqual(template_response.status_code, 201, msg=template_response.text)
        template_id = template_response.json()["id"]

        switched_response = self.client.post(
            f"/api/email-tasks/{task_id}/outreach-config",
            json={
                "outreach_generation_mode": "template",
                "outreach_template_id": template_id,
                "outreach_template_subject": "新模板主题 {{name}}",
                "outreach_template_body_text": "新模板正文 {{sender_name}}",
                "outreach_template_body_html": "<p>新模板正文 {{sender_name}}</p>",
            },
        )
        self.assertEqual(switched_response.status_code, 200, msg=switched_response.text)
        switched = switched_response.json()["current_task"]
        self.assertEqual(switched["status"], "discovered")
        self.assertIsNone(switched["generated_subject"])
        self.assertIsNone(switched["generated_content_text"])
        self.assertEqual(switched["draft"]["source"], "template")
        self.assertIn("新模板正文", switched["draft"]["body_text"])

    def test_professor_dashboard_prioritizes_existing_contact_over_follow_up_draft(
        self,
    ) -> None:
        identity_id = self._create_identity(with_imap=False)
        llm_id = self._create_llm()
        professor_response = self.client.post(
            "/api/professors",
            json={
                "name": "已联系后跟进导师",
                "email": "contacted-follow-up@example.edu",
                "title": "Professor",
                "university": "Example University",
                "school": "School of AI",
                "department": "Computer Science",
                "research_direction": "Large language models",
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
        parent_task_id = ensure_response.json()["current_task"]["id"]

        connection = sqlite3.connect(self.db_path)
        try:
            connection.execute(
                """
                UPDATE email_tasks
                SET status = 'sent', sent_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (parent_task_id,),
            )
            connection.execute(
                """
                INSERT INTO email_logs (
                    email_task_id, identity_id, llm_profile_id, professor_id,
                    direction, subject, content, rfc_message_id
                )
                VALUES (?, ?, ?, ?, 'sent', 'hello', 'hello body', '<sent@example.edu>')
                """,
                (parent_task_id, identity_id, llm_id, professor_id),
            )
            connection.execute(
                """
                INSERT INTO email_tasks (
                    source, parent_task_id, identity_id, llm_profile_id,
                    professor_id, status, created_at, updated_at
                )
                VALUES ('manual', ?, ?, ?, ?, 'matched', datetime('now', '+1 minute'), datetime('now', '+1 minute'))
                """,
                (parent_task_id, identity_id, llm_id, professor_id),
            )
            connection.commit()
        finally:
            connection.close()

        response = self.client.get(
            "/api/professors",
            params={"identity_id": identity_id, "llm_profile_id": llm_id},
        )

        self.assertEqual(response.status_code, 200, msg=response.text)
        professor = next(item for item in response.json() if item["id"] == professor_id)
        self.assertEqual(professor["status"], "contacted")

        connection = sqlite3.connect(self.db_path)
        try:
            connection.execute(
                "UPDATE email_tasks SET status = 'reply_detected', is_replied = 1 WHERE id = ?",
                (parent_task_id,),
            )
            connection.commit()
        finally:
            connection.close()

        replied_response = self.client.get(
            "/api/professors",
            params={"identity_id": identity_id, "llm_profile_id": llm_id},
        )

        self.assertEqual(replied_response.status_code, 200, msg=replied_response.text)
        replied_professor = next(
            item for item in replied_response.json() if item["id"] == professor_id
        )
        self.assertEqual(replied_professor["status"], "replied")

    def test_workspace_endpoint_without_existing_task_returns_empty_thread(
        self,
    ) -> None:
        identity_id = self._create_identity(with_imap=False)
        llm_id = self._create_llm()

        professor_response = self.client.post(
            "/api/professors",
            json={
                "name": "空白导师",
                "email": "blank@example.edu",
                "title": "Professor",
                "university": "Example University",
                "school": "School of Computing",
                "department": "Computer Science",
                "research_direction": "Distributed systems",
                "recent_papers": [],
                "profile_url": None,
                "source_url": None,
            },
        )
        self.assertEqual(
            professor_response.status_code, 201, msg=professor_response.text
        )
        professor_id = professor_response.json()["id"]

        workspace_response = self.client.get(
            f"/api/workspaces/{professor_id}",
            params={"identity_id": identity_id, "llm_profile_id": llm_id},
        )
        self.assertEqual(
            workspace_response.status_code, 200, msg=workspace_response.text
        )

        payload = workspace_response.json()
        self.assertEqual(payload["professor"]["id"], professor_id)
        self.assertIsNone(payload["current_task"]["id"])
        self.assertEqual(payload["current_task"]["fit_points"], [])
        self.assertEqual(payload["current_task"]["risk_points"], [])
        self.assertEqual(payload["current_task"]["match_keywords"], [])
        self.assertEqual(payload["messages"], [])

    def test_workspace_communication_history_follows_identity_not_llm_profile(
        self,
    ) -> None:
        identity_id = self._create_identity(with_imap=False)
        first_llm_id = self._create_llm()
        second_llm_response = self.client.post(
            "/api/llm-profiles",
            json={
                "name": "切换后模型",
                "provider": "openai",
                "api_base_url": "https://api.example.com/v1",
                "api_key": "sk-test-key",
                "model_name": "gpt-switch",
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

        other_identity_payload = self._build_identity_payload(with_imap=False)
        other_identity_payload["name"] = "另一个身份"
        other_identity_payload["email_address"] = "other-sender@example.com"
        other_identity_payload["smtp_username"] = "other-sender@example.com"
        other_identity_response = self.client.post(
            "/api/identities", json=other_identity_payload
        )
        self.assertEqual(
            other_identity_response.status_code, 201, msg=other_identity_response.text
        )
        other_identity_id = other_identity_response.json()["id"]

        professor_response = self.client.post(
            "/api/professors",
            json={
                "name": "模型切换历史导师",
                "email": "history-switch@example.edu",
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
            params={"identity_id": identity_id, "llm_profile_id": first_llm_id},
        )
        self.assertEqual(ensure_response.status_code, 200, msg=ensure_response.text)
        task_id = ensure_response.json()["current_task"]["id"]

        connection = sqlite3.connect(self.db_path)
        try:
            connection.execute(
                """
                UPDATE email_tasks
                SET status = 'reply_detected',
                    match_score = 88,
                    match_reason = '身份材料与导师方向高度匹配',
                    fit_points = '["研究方向一致"]',
                    risk_points = '["需要补充项目细节"]',
                    match_keywords = '["agent", "workflow"]',
                    sent_at = CURRENT_TIMESTAMP,
                    is_replied = 1
                WHERE id = ?
                """,
                (task_id,),
            )
            connection.execute(
                """
                INSERT INTO email_logs (
                    email_task_id, identity_id, llm_profile_id, professor_id,
                    direction, subject, content, rfc_message_id
                )
                VALUES (?, ?, ?, ?, 'sent', '首封联系', '老师您好', '<sent-switch@example.edu>')
                """,
                (task_id, identity_id, first_llm_id, professor_id),
            )
            connection.execute(
                """
                INSERT INTO email_logs (
                    email_task_id, identity_id, llm_profile_id, professor_id,
                    direction, subject, content, rfc_message_id
                )
                VALUES (?, ?, ?, ?, 'received', 'Re: 首封联系', '欢迎继续交流', '<reply-switch@example.edu>')
                """,
                (task_id, identity_id, first_llm_id, professor_id),
            )
            connection.execute(
                """
                INSERT INTO email_logs (
                    email_task_id, identity_id, llm_profile_id, professor_id,
                    direction, subject, content, rfc_message_id
                )
                VALUES (?, ?, ?, ?, 'draft', '模型 A 草稿', '不应带到模型 B', NULL)
                """,
                (task_id, identity_id, first_llm_id, professor_id),
            )
            connection.commit()
        finally:
            connection.close()

        switched_model_response = self.client.get(
            f"/api/workspaces/{professor_id}",
            params={"identity_id": identity_id, "llm_profile_id": second_llm_id},
        )
        self.assertEqual(
            switched_model_response.status_code, 200, msg=switched_model_response.text
        )
        switched_messages = switched_model_response.json()["messages"]
        self.assertEqual(
            [message["direction"] for message in switched_messages],
            ["sent", "received", "draft"],
        )
        self.assertEqual(
            [message["subject"] for message in switched_messages],
            ["首封联系", "Re: 首封联系", "模型 A 草稿"],
        )

        switched_ensure_response = self.client.post(
            f"/api/workspaces/{professor_id}/ensure-task",
            params={"identity_id": identity_id, "llm_profile_id": second_llm_id},
        )
        self.assertEqual(
            switched_ensure_response.status_code, 200, msg=switched_ensure_response.text
        )
        switched_task = switched_ensure_response.json()["current_task"]
        self.assertEqual(switched_task["id"], task_id)
        self.assertEqual(switched_task["status"], "reply_detected")
        self.assertEqual(switched_task["match_score"], 88)
        self.assertEqual(switched_task["match_reason"], "身份材料与导师方向高度匹配")
        self.assertEqual(switched_task["fit_points"], ["研究方向一致"])
        self.assertEqual(switched_task["risk_points"], ["需要补充项目细节"])
        self.assertEqual(switched_task["match_keywords"], ["agent", "workflow"])

        dashboard_response = self.client.get(
            "/api/professors",
            params={"identity_id": identity_id, "llm_profile_id": second_llm_id},
        )
        self.assertEqual(
            dashboard_response.status_code, 200, msg=dashboard_response.text
        )
        dashboard_professor = next(
            item for item in dashboard_response.json() if item["id"] == professor_id
        )
        self.assertEqual(dashboard_professor["match_score"], 88)
        self.assertEqual(dashboard_professor["sent_count"], 1)
        self.assertEqual(dashboard_professor["status"], "replied")

        dashboard_without_model_response = self.client.get(
            "/api/professors",
            params={"identity_id": identity_id},
        )
        self.assertEqual(
            dashboard_without_model_response.status_code,
            200,
            msg=dashboard_without_model_response.text,
        )
        dashboard_without_model_professor = next(
            item
            for item in dashboard_without_model_response.json()
            if item["id"] == professor_id
        )
        self.assertEqual(dashboard_without_model_professor["match_score"], 88)
        self.assertEqual(dashboard_without_model_professor["sent_count"], 1)
        self.assertEqual(dashboard_without_model_professor["status"], "replied")

        other_identity_response = self.client.get(
            f"/api/workspaces/{professor_id}",
            params={"identity_id": other_identity_id, "llm_profile_id": second_llm_id},
        )
        self.assertEqual(
            other_identity_response.status_code, 200, msg=other_identity_response.text
        )
        self.assertEqual(other_identity_response.json()["messages"], [])

        other_dashboard_response = self.client.get(
            "/api/professors",
            params={"identity_id": other_identity_id, "llm_profile_id": second_llm_id},
        )
        self.assertEqual(
            other_dashboard_response.status_code, 200, msg=other_dashboard_response.text
        )
        other_dashboard_professor = next(
            item
            for item in other_dashboard_response.json()
            if item["id"] == professor_id
        )
        self.assertIsNone(other_dashboard_professor["match_score"])
        self.assertEqual(other_dashboard_professor["sent_count"], 0)
        self.assertEqual(other_dashboard_professor["status"], "not_contacted")

    def test_workspace_ensure_task_creates_and_reuses_personal_task(self) -> None:
        identity_id = self._create_identity(with_imap=False)
        llm_id = self._create_llm()
        material_id = self._upload_material(
            identity_id,
            filename="resume.txt",
            content=b"My research focuses on agents and information extraction.",
            material_type="resume",
        )

        professor_response = self.client.post(
            "/api/professors",
            json={
                "name": "可直达工作区导师",
                "email": "direct-workspace@example.edu",
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

        first_response = self.client.post(
            f"/api/workspaces/{professor_id}/ensure-task",
            params={"identity_id": identity_id, "llm_profile_id": llm_id},
        )
        self.assertEqual(first_response.status_code, 200, msg=first_response.text)
        first_payload = first_response.json()
        self.assertIsNotNone(first_payload["current_task"]["id"])
        self.assertEqual(first_payload["current_task"]["batch_task_id"], None)
        self.assertEqual(first_payload["current_task"]["status"], "discovered")
        self.assertEqual(
            first_payload["current_task"]["primary_material_id"], material_id
        )

        second_response = self.client.post(
            f"/api/workspaces/{professor_id}/ensure-task",
            params={"identity_id": identity_id, "llm_profile_id": llm_id},
        )
        self.assertEqual(second_response.status_code, 200, msg=second_response.text)
        second_payload = second_response.json()
        self.assertEqual(
            second_payload["current_task"]["id"],
            first_payload["current_task"]["id"],
        )
        self.assertEqual(second_payload["messages"], [])

    def test_workspace_ensure_task_backfills_existing_task_from_identity_primary_material(
        self,
    ) -> None:
        identity_id = self._create_identity(with_imap=False)
        llm_id = self._create_llm()
        material_id = self._upload_material(
            identity_id,
            filename="workspace-default-resume.txt",
            content=b"My research focuses on agents and information extraction.",
            material_type="resume",
        )
        set_primary_response = self.client.post(
            f"/api/materials/{material_id}/set-primary"
        )
        self.assertEqual(
            set_primary_response.status_code, 200, msg=set_primary_response.text
        )

        professor_id = self._create_professor(
            email="workspace-backfill-material@example.edu"
        )
        task_id = self._insert_email_task_with_material(
            identity_id=identity_id,
            llm_id=llm_id,
            professor_id=professor_id,
            status="matched",
            primary_material_id=None,
            selected_material_ids=[],
            match_score=82,
            match_reason="方向匹配",
            outreach_generation_mode="llm",
        )

        response = self.client.post(
            f"/api/workspaces/{professor_id}/ensure-task",
            params={"identity_id": identity_id, "llm_profile_id": llm_id},
        )

        self.assertEqual(response.status_code, 200, msg=response.text)
        current_task = response.json()["current_task"]
        self.assertEqual(current_task["id"], task_id)
        self.assertEqual(current_task["primary_material_id"], material_id)
        self.assertEqual(current_task["primary_material"]["id"], material_id)

        connection = sqlite3.connect(self.db_path)
        try:
            stored_material_id = connection.execute(
                "SELECT primary_material_id FROM email_tasks WHERE id = ?",
                (task_id,),
            ).fetchone()[0]
        finally:
            connection.close()
        self.assertEqual(stored_material_id, material_id)

    def test_workspace_new_task_inherits_latest_successful_manual_attachments(
        self,
    ) -> None:
        identity_id = self._create_identity(with_imap=False)
        llm_id = self._create_llm()
        recent_material_id = self._upload_material(
            identity_id,
            filename="recent-workspace-attachment.pdf",
            content=b"recent workspace attachment",
            material_type="resume",
        )
        ignored_material_id = self._upload_material(
            identity_id,
            filename="ignored-non-workspace-attachment.pdf",
            content=b"ignored attachment",
            material_type="transcript",
        )

        sent_professor_id = self._create_professor(email="recent-sent@example.edu")
        sent_task_id = self._insert_email_task_with_material(
            identity_id=identity_id,
            llm_id=llm_id,
            professor_id=sent_professor_id,
            status="sent",
            primary_material_id=None,
            selected_material_ids=[recent_material_id, 999999, recent_material_id],
            approved_subject="Recent subject",
            approved_body_text="Recent body",
        )
        self._mark_email_task_sent(sent_task_id, minutes_ago=10)

        batch_professor_id = self._create_professor(email="newer-batch@example.edu")
        batch_task_id = self._insert_batch_task_with_material(
            identity_id=identity_id,
            llm_id=llm_id,
            status="completed",
            primary_material_id=None,
        )
        batch_item_id = self._insert_email_task_with_material(
            identity_id=identity_id,
            llm_id=llm_id,
            professor_id=batch_professor_id,
            status="sent",
            primary_material_id=None,
            selected_material_ids=[ignored_material_id],
            batch_task_id=batch_task_id,
            source="batch",
            approved_subject="Batch subject",
            approved_body_text="Batch body",
        )
        self._mark_email_task_sent(batch_item_id, minutes_ago=5)

        failed_professor_id = self._create_professor(email="newer-failed@example.edu")
        self._insert_email_task_with_material(
            identity_id=identity_id,
            llm_id=llm_id,
            professor_id=failed_professor_id,
            status="send_failed",
            primary_material_id=None,
            selected_material_ids=[ignored_material_id],
            approved_subject="Failed subject",
            approved_body_text="Failed body",
        )

        target_professor_id = self._create_professor(
            email="inherits-recent@example.edu"
        )
        response = self.client.post(
            f"/api/workspaces/{target_professor_id}/ensure-task",
            params={"identity_id": identity_id, "llm_profile_id": llm_id},
        )

        self.assertEqual(response.status_code, 200, msg=response.text)
        self.assertEqual(
            response.json()["current_task"]["selected_material_ids"],
            [recent_material_id],
        )

    def test_workspace_attachment_defaults_do_not_skip_latest_attachmentless_send(
        self,
    ) -> None:
        identity_id = self._create_identity(with_imap=False)
        llm_id = self._create_llm()
        material_id = self._upload_material(
            identity_id,
            filename="older-workspace-attachment.pdf",
            content=b"older workspace attachment",
            material_type="resume",
        )

        older_professor_id = self._create_professor(
            email="older-with-attachment@example.edu"
        )
        older_task_id = self._insert_email_task_with_material(
            identity_id=identity_id,
            llm_id=llm_id,
            professor_id=older_professor_id,
            status="sent",
            primary_material_id=None,
            selected_material_ids=[material_id],
            approved_subject="Older subject",
            approved_body_text="Older body",
        )
        self._mark_email_task_sent(older_task_id, minutes_ago=10)

        latest_professor_id = self._create_professor(
            email="latest-without-attachment@example.edu"
        )
        latest_task_id = self._insert_email_task_with_material(
            identity_id=identity_id,
            llm_id=llm_id,
            professor_id=latest_professor_id,
            status="sent",
            primary_material_id=None,
            selected_material_ids=[],
            approved_subject="Latest subject",
            approved_body_text="Latest body",
        )
        self._mark_email_task_sent(latest_task_id, minutes_ago=5)

        target_professor_id = self._create_professor(
            email="no-stale-default@example.edu"
        )
        response = self.client.post(
            f"/api/workspaces/{target_professor_id}/ensure-task",
            params={"identity_id": identity_id, "llm_profile_id": llm_id},
        )

        self.assertEqual(response.status_code, 200, msg=response.text)
        self.assertIsNone(response.json()["current_task"]["selected_material_ids"])

    def test_workspace_get_backfills_only_pristine_unselected_root_task(self) -> None:
        identity_id = self._create_identity(with_imap=False)
        llm_id = self._create_llm()
        material_id = self._upload_material(
            identity_id,
            filename="workspace-get-default.pdf",
            content=b"workspace get default",
            material_type="resume",
        )

        pristine_professor_id = self._create_professor(
            email="pristine-before-send@example.edu"
        )
        pristine_before_send = self.client.post(
            f"/api/workspaces/{pristine_professor_id}/ensure-task",
            params={"identity_id": identity_id, "llm_profile_id": llm_id},
        )
        self.assertEqual(
            pristine_before_send.status_code, 200, msg=pristine_before_send.text
        )
        self.assertIsNone(
            pristine_before_send.json()["current_task"]["selected_material_ids"],
        )

        sent_professor_id = self._create_professor(
            email="sent-after-pristine-created@example.edu"
        )
        sent_task_id = self._insert_email_task_with_material(
            identity_id=identity_id,
            llm_id=llm_id,
            professor_id=sent_professor_id,
            status="sent",
            primary_material_id=None,
            selected_material_ids=[material_id],
            approved_subject="Sent subject",
            approved_body_text="Sent body",
        )
        self._mark_email_task_sent(sent_task_id, minutes_ago=1)

        explicit_empty_professor_id = self._create_professor(
            email="explicit-empty@example.edu"
        )
        explicit_empty_task_id = self._insert_email_task_with_material(
            identity_id=identity_id,
            llm_id=llm_id,
            professor_id=explicit_empty_professor_id,
            status="discovered",
            primary_material_id=None,
            selected_material_ids=[],
        )
        existing_draft_professor_id = self._create_professor(
            email="existing-draft@example.edu"
        )
        existing_draft_task_id = self._insert_email_task_with_material(
            identity_id=identity_id,
            llm_id=llm_id,
            professor_id=existing_draft_professor_id,
            status="review_required",
            primary_material_id=None,
            selected_material_ids=None,
            generated_subject="Existing draft",
            generated_content_text="Existing body",
        )

        pristine_response = self.client.get(
            f"/api/workspaces/{pristine_professor_id}",
            params={"identity_id": identity_id, "llm_profile_id": llm_id},
        )
        explicit_empty_response = self.client.get(
            f"/api/workspaces/{explicit_empty_professor_id}",
            params={"identity_id": identity_id, "llm_profile_id": llm_id},
        )
        existing_draft_response = self.client.get(
            f"/api/workspaces/{existing_draft_professor_id}",
            params={"identity_id": identity_id, "llm_profile_id": llm_id},
        )

        self.assertEqual(pristine_response.status_code, 200, msg=pristine_response.text)
        self.assertEqual(
            pristine_response.json()["current_task"]["selected_material_ids"],
            [material_id],
        )
        self.assertEqual(
            self._get_task_material_references(explicit_empty_task_id)[1],
            [],
        )
        self.assertIsNone(
            self._get_task_material_references(existing_draft_task_id)[1],
        )
        self.assertEqual(
            explicit_empty_response.json()["current_task"]["selected_material_ids"],
            [],
        )
        self.assertIsNone(
            existing_draft_response.json()["current_task"]["selected_material_ids"],
        )

    def test_workspace_attachment_defaults_remain_disabled_for_shared_service_callers(
        self,
    ) -> None:
        identity_id = self._create_identity(with_imap=False)
        llm_id = self._create_llm()
        material_id = self._upload_material(
            identity_id,
            filename="ui-only-default.pdf",
            content=b"ui only default",
            material_type="resume",
        )
        sent_professor_id = self._create_professor(email="ui-only-source@example.edu")
        sent_task_id = self._insert_email_task_with_material(
            identity_id=identity_id,
            llm_id=llm_id,
            professor_id=sent_professor_id,
            status="sent",
            primary_material_id=None,
            selected_material_ids=[material_id],
            approved_subject="Source subject",
            approved_body_text="Source body",
        )
        self._mark_email_task_sent(sent_task_id, minutes_ago=1)
        target_professor_id = self._create_professor(
            email="shared-service-target@example.edu"
        )

        async def load_without_ui_defaults() -> tuple[
            list[int] | None, list[int] | None
        ]:
            from app.core.database import get_session_factory
            from app.modules.workspace.thread import (
                build_workspace_thread,
                ensure_workspace_task,
            )

            async with get_session_factory()() as session:
                task = await ensure_workspace_task(
                    session,
                    professor_id=target_professor_id,
                    identity_id=identity_id,
                    llm_profile_id=llm_id,
                )
                thread = await build_workspace_thread(
                    session,
                    professor_id=target_professor_id,
                    identity_id=identity_id,
                    llm_profile_id=llm_id,
                )
                return (
                    task.selected_material_ids,
                    thread.current_task.selected_material_ids,
                )

        self.assertEqual(asyncio.run(load_without_ui_defaults()), (None, None))

    def test_workspace_ensure_task_creates_new_manual_task_after_schedule_expired_history(
        self,
    ) -> None:
        identity_id = self._create_identity(with_imap=False)
        llm_id = self._create_llm()

        professor_response = self.client.post(
            "/api/professors",
            json={
                "name": "过期工作区导师",
                "email": "expired-workspace@example.edu",
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

        first_response = self.client.post(
            f"/api/workspaces/{professor_id}/ensure-task",
            params={"identity_id": identity_id, "llm_profile_id": llm_id},
        )
        self.assertEqual(first_response.status_code, 200, msg=first_response.text)
        first_task = first_response.json()["current_task"]
        self.assertIsNotNone(first_task["id"])

        connection = sqlite3.connect(self.db_path)
        try:
            connection.execute(
                """
                UPDATE email_tasks
                SET status = ?,
                    cancellation_reason = ?,
                    updated_at = datetime('now')
                WHERE id = ?
                """,
                ("canceled", "schedule_expired", first_task["id"]),
            )
            connection.commit()
        finally:
            connection.close()

        second_response = self.client.post(
            f"/api/workspaces/{professor_id}/ensure-task",
            params={"identity_id": identity_id, "llm_profile_id": llm_id},
        )
        self.assertEqual(second_response.status_code, 200, msg=second_response.text)
        second_task = second_response.json()["current_task"]

        self.assertNotEqual(second_task["id"], first_task["id"])
        self.assertEqual(second_task["source"], "manual")
        self.assertEqual(second_task["parent_task_id"], first_task["id"])
        self.assertEqual(second_task["batch_task_id"], None)
        self.assertNotEqual(second_task["status"], "canceled")

    def test_workspace_thread_keeps_current_task_visible_after_model_switch(
        self,
    ) -> None:
        identity_id = self._create_identity(with_imap=False)
        first_llm_id = self._create_llm(name="旧模型", model_name="gpt-old-visible")
        second_llm_id = self._create_llm(name="新模型", model_name="gpt-new-visible")
        professor_id = self._create_professor(
            email="visible-after-model-switch@example.edu"
        )
        task_id = self._insert_email_task_with_material(
            identity_id=identity_id,
            llm_id=first_llm_id,
            professor_id=professor_id,
            status="review_required",
            primary_material_id=None,
            generated_subject="旧模型创建的任务",
            generated_content_text="这条任务切换模型后仍应显示",
            generated_content_html="<p>这条任务切换模型后仍应显示</p>",
        )

        response = self.client.get(
            f"/api/workspaces/{professor_id}",
            params={"identity_id": identity_id, "llm_profile_id": second_llm_id},
        )

        self.assertEqual(response.status_code, 200, msg=response.text)
        payload = response.json()
        self.assertEqual(payload["llm_profile"]["id"], second_llm_id)
        self.assertEqual(payload["current_task"]["id"], task_id)
        self.assertEqual(
            payload["current_task"]["generated_subject"], "旧模型创建的任务"
        )
        self.assertEqual(self._get_email_task_llm_profile_id(task_id), first_llm_id)

    def test_workspace_llm_actions_use_selected_model_after_model_switch(self) -> None:
        identity_id = self._create_identity(with_imap=False)
        first_llm_id = self._create_llm()
        second_llm_id = self._create_llm(
            name="切换后的执行模型", model_name="gpt-runtime-selected"
        )
        material_id = self._upload_material(
            identity_id,
            filename="runtime-model-resume.txt",
            content=b"My research background is in AI agents and workflow automation.",
            material_type="resume",
        )
        professor_id = self._create_professor(email="runtime-model-switch@example.edu")
        task_id = self._insert_email_task_with_material(
            identity_id=identity_id,
            llm_id=first_llm_id,
            professor_id=professor_id,
            status="matched",
            primary_material_id=material_id,
            match_score=82,
            match_reason="方向匹配",
        )

        captured_profiles: list[tuple[str, int, str]] = []

        async def fake_generate_draft_content(**kwargs):
            llm_profile = kwargs["llm_profile"]
            captured_profiles.append(("draft", llm_profile.id, llm_profile.model_name))
            return self._build_draft_generation_result(
                subject="切换模型生成主题",
                body_text="切换模型生成正文",
                body_html="<p>切换模型生成正文</p>",
                prompt_tokens=11,
                completion_tokens=7,
            )

        async def fake_generate_match_evaluation(**kwargs):
            llm_profile = kwargs["llm_profile"]
            captured_profiles.append(("match", llm_profile.id, llm_profile.model_name))
            return self._build_match_evaluation_result(match_score=91)

        with patch(
            "app.modules.workspace.tasks.runtime.llm_runtime.generate_draft_content",
            AsyncMock(side_effect=fake_generate_draft_content),
        ):
            draft_response = self.client.post(
                f"/api/email-tasks/{task_id}/generate-draft",
                json={"llm_profile_id": second_llm_id},
            )

        self.assertEqual(draft_response.status_code, 200, msg=draft_response.text)
        draft_payload = draft_response.json()
        self.assertEqual(
            captured_profiles, [("draft", second_llm_id, "gpt-runtime-selected")]
        )
        self.assertEqual(draft_payload["llm_profile"]["id"], second_llm_id)
        self.assertEqual(
            draft_payload["current_task"]["generated_subject"], "切换模型生成主题"
        )
        self.assertEqual(self._get_email_task_llm_profile_id(task_id), second_llm_id)

        with patch(
            "app.modules.matching.task_analysis.llm_runtime.generate_match_evaluation",
            AsyncMock(side_effect=fake_generate_match_evaluation),
        ):
            match_response = self.client.post(
                f"/api/email-tasks/{task_id}/calculate-match",
                json={"llm_profile_id": second_llm_id},
            )

        self.assertEqual(match_response.status_code, 200, msg=match_response.text)
        match_payload = match_response.json()
        self.assertEqual(
            captured_profiles,
            [
                ("draft", second_llm_id, "gpt-runtime-selected"),
                ("match", second_llm_id, "gpt-runtime-selected"),
            ],
        )
        self.assertEqual(match_payload["thread"]["llm_profile"]["id"], second_llm_id)
        self.assertEqual(match_payload["thread"]["current_task"]["match_score"], 91)
        self.assertEqual(self._get_email_task_llm_profile_id(task_id), second_llm_id)

    def test_generate_draft_passes_workflow_session_and_runtime_adaptation(
        self,
    ) -> None:
        task_id = self._create_rewrite_ready_task()
        adaptation = LLMRuntimeAdaptation("responses", {"enable_thinking": False})
        workflow_sessions: list[AsyncSession] = []

        async def fake_ensure(
            session: AsyncSession, _profile: object
        ) -> LLMRuntimeAdaptation:
            self.assertIsInstance(session, AsyncSession)
            workflow_sessions.append(session)
            return adaptation

        async def fake_generate_draft_content(**kwargs: object):
            self.assertEqual(len(workflow_sessions), 1)
            self.assertIs(kwargs["session"], workflow_sessions[0])
            self.assertIs(kwargs["adaptation"], adaptation)
            return self._build_draft_generation_result(
                subject="适配后的草稿主题",
                body_text="适配后的草稿正文",
                body_html="<p>适配后的草稿正文</p>",
            )

        with (
            patch(
                "app.modules.workspace.tasks.runtime.llm_runtime.ensure_llm_runtime_adaptation",
                AsyncMock(side_effect=fake_ensure),
            ) as adaptation_mock,
            patch(
                "app.modules.workspace.tasks.runtime.llm_runtime.generate_draft_content",
                AsyncMock(side_effect=fake_generate_draft_content),
            ) as generate_mock,
        ):
            response = self.client.post(
                f"/api/email-tasks/{task_id}/generate-draft", json={}
            )

        self.assertEqual(response.status_code, 200, msg=response.text)
        adaptation_mock.assert_awaited_once()
        generate_mock.assert_awaited_once()

    def test_rewrite_draft_uses_request_body_as_llm_input(self) -> None:
        task_id = self._create_rewrite_ready_task()

        async def fake_generate_draft_content(**kwargs):
            self.assertEqual(kwargs["custom_subject"], "用户改过主题")
            self.assertEqual(kwargs["custom_body"], "用户改过正文")
            self.assertEqual(kwargs["custom_body_html"], "<p>用户改过正文</p>")
            return self._build_draft_generation_result(
                subject="AI 改写主题",
                body_text="AI 改写正文",
                body_html="<p>AI 改写正文</p>",
                prompt_tokens=12,
                completion_tokens=8,
                prompt_hash="c" * 64,
                stable_prefix_hash="d" * 64,
                prompt_cache_key="draft-rewrite:v5:rewrite-test",
            )

        with (
            patch(
                "app.modules.workspace.tasks.runtime.llm_runtime.ensure_llm_runtime_adaptation",
                AsyncMock(return_value=LLMRuntimeAdaptation("chat_completions", None)),
            ),
            patch(
                "app.modules.workspace.tasks.runtime.llm_runtime.generate_draft_content",
                AsyncMock(side_effect=fake_generate_draft_content),
            ),
        ):
            response = self.client.post(
                f"/api/email-tasks/{task_id}/rewrite-draft",
                json={
                    "subject": "用户改过主题",
                    "body_text": "用户改过正文",
                    "body_html": "<p>用户改过正文</p>",
                    "selected_material_ids": [],
                    "llm_profile_id": None,
                },
            )

        self.assertEqual(response.status_code, 200, msg=response.text)
        current_task = response.json()["current_task"]
        self.assertEqual(current_task["draft"]["source"], "ai_rewrite")
        self.assertEqual(current_task["draft"]["subject"], "AI 改写主题")
        self.assertEqual(current_task["draft"]["body_text"], "AI 改写正文")
        provider_payload = self._latest_email_log_provider_payload()
        self.assertEqual(provider_payload["prompt_hash"], "c" * 64)
        self.assertEqual(provider_payload["stable_prefix_hash"], "d" * 64)
        self.assertEqual(
            provider_payload["prompt_cache_key"],
            "draft-rewrite:v5:rewrite-test",
        )
        operation_logs = self.client.get(
            "/api/diagnostics/operation-logs",
            params={"event_name": "email_task.draft_rewritten"},
        )
        self.assertEqual(operation_logs.status_code, 200, msg=operation_logs.text)
        metadata = operation_logs.json()["items"][0]["metadata"]
        self.assertEqual(metadata["prompt_hash"], "c" * 64)
        self.assertEqual(metadata["stable_prefix_hash"], "d" * 64)
        self.assertEqual(metadata["prompt_cache_key"], "draft-rewrite:v5:rewrite-test")

    def test_rewrite_draft_preserves_omitted_attachments_after_failure(self) -> None:
        from app.modules.llm import runtime as llm_runtime

        task_id = self._create_rewrite_ready_task()
        with closing(sqlite3.connect(self.db_path)) as connection, connection:
            material_id = connection.execute(
                "SELECT primary_material_id FROM email_tasks WHERE id = ?",
                (task_id,),
            ).fetchone()[0]
            connection.execute(
                "UPDATE email_tasks SET selected_material_ids = ? WHERE id = ?",
                (json.dumps([material_id]), task_id),
            )

        async def fail_after_claim(**_kwargs: object):
            with closing(sqlite3.connect(self.db_path)) as connection:
                selected_ids, source_ids = connection.execute(
                    """
                    SELECT selected_material_ids,
                           draft_rewrite_source_selected_material_ids
                    FROM email_tasks
                    WHERE id = ?
                    """,
                    (task_id,),
                ).fetchone()
            self.assertEqual(json.loads(selected_ids), [material_id])
            self.assertEqual(json.loads(source_ids), [material_id])
            raise llm_runtime.LLMRuntimeError("模型请求失败: 验证附件回滚")

        with patch(
            "app.modules.workspace.tasks.runtime.llm_runtime.generate_draft_content",
            AsyncMock(side_effect=fail_after_claim),
        ):
            response = self.client.post(
                f"/api/email-tasks/{task_id}/rewrite-draft",
                json={
                    "subject": "保留附件主题",
                    "body_text": "保留附件正文",
                    "body_html": "<p>保留附件正文</p>",
                },
            )

        self.assertEqual(response.status_code, 502, msg=response.text)
        with closing(sqlite3.connect(self.db_path)) as connection:
            status, selected_ids = connection.execute(
                "SELECT status, selected_material_ids FROM email_tasks WHERE id = ?",
                (task_id,),
            ).fetchone()
        self.assertEqual(status, "matched")
        self.assertEqual(json.loads(selected_ids), [material_id])

    def test_rewrite_draft_resolves_thinking_adaptation_once(self) -> None:
        task_id = self._create_rewrite_ready_task()
        extra_body = {"thinking": {"type": "disabled"}}

        async def fake_generate_draft_content(**kwargs):
            self.assertEqual(
                kwargs["adaptation"],
                LLMRuntimeAdaptation("chat_completions", extra_body),
            )
            self.assertIsNotNone(kwargs["session"])
            return self._build_draft_generation_result(
                subject="AI 改写主题",
                body_text="AI 改写正文",
                body_html="<p>AI 改写正文</p>",
                prompt_tokens=12,
                completion_tokens=8,
            )

        with (
            patch(
                "app.modules.workspace.tasks.runtime.llm_runtime.ensure_llm_runtime_adaptation",
                AsyncMock(
                    return_value=LLMRuntimeAdaptation("chat_completions", extra_body)
                ),
            ) as adaptation_mock,
            patch(
                "app.modules.workspace.tasks.runtime.llm_runtime.generate_draft_content",
                AsyncMock(side_effect=fake_generate_draft_content),
            ) as generate_mock,
        ):
            response = self.client.post(
                f"/api/email-tasks/{task_id}/rewrite-draft",
                json={
                    "subject": "用户改过主题",
                    "body_text": "用户改过正文",
                    "body_html": "<p>用户改过正文</p>",
                    "selected_material_ids": [],
                    "llm_profile_id": None,
                },
            )

        self.assertEqual(response.status_code, 200, msg=response.text)
        adaptation_mock.assert_awaited_once()
        generate_mock.assert_awaited_once()

    def test_rewrite_draft_rejects_empty_body_without_calling_llm(self) -> None:
        task_id = self._create_rewrite_ready_task()

        with patch(
            "app.modules.workspace.tasks.runtime.llm_runtime.generate_draft_content",
            AsyncMock(side_effect=AssertionError("空正文不能调用 LLM")),
        ):
            response = self.client.post(
                f"/api/email-tasks/{task_id}/rewrite-draft",
                json={
                    "subject": "主题",
                    "body_text": "",
                    "body_html": "",
                    "selected_material_ids": [],
                    "llm_profile_id": None,
                },
            )

        self.assertEqual(response.status_code, 400, msg=response.text)
        self.assertEqual(
            response.json()["detail"], "先写入正文或配置默认模板后再使用 AI 改写"
        )

    def test_rewrite_draft_persists_source_before_llm_returns(self) -> None:
        task_id = self._create_rewrite_ready_task()

        async def fake_generate_draft_content(**kwargs):
            connection = sqlite3.connect(self.db_path)
            try:
                row = connection.execute(
                    """
                    SELECT status, draft_generation_previous_status, draft_generation_started_at,
                           draft_rewrite_source_subject, draft_rewrite_source_body_text,
                           draft_rewrite_source_body_html, selected_material_ids
                    FROM email_tasks
                    WHERE id = ?
                    """,
                    (task_id,),
                ).fetchone()
            finally:
                connection.close()

            self.assertIsNotNone(row)
            self.assertEqual(row[0], "generating_draft")
            self.assertEqual(row[1], "matched")
            self.assertIsNotNone(row[2])
            self.assertEqual(row[3], "点击瞬间主题")
            self.assertEqual(row[4], "点击瞬间正文")
            self.assertEqual(row[5], "<p>点击瞬间正文</p>")
            self.assertEqual(json.loads(row[6]), [])
            return self._build_draft_generation_result(
                subject="AI 改写主题",
                body_text="AI 改写正文",
                body_html="<p>AI 改写正文</p>",
            )

        with (
            patch(
                "app.modules.workspace.tasks.runtime.llm_runtime.ensure_llm_runtime_adaptation",
                AsyncMock(return_value=LLMRuntimeAdaptation("chat_completions", None)),
            ),
            patch(
                "app.modules.workspace.tasks.runtime.llm_runtime.generate_draft_content",
                AsyncMock(side_effect=fake_generate_draft_content),
            ),
        ):
            response = self.client.post(
                f"/api/email-tasks/{task_id}/rewrite-draft",
                json={
                    "subject": "点击瞬间主题",
                    "body_text": "点击瞬间正文",
                    "body_html": "<p>点击瞬间正文</p>",
                    "selected_material_ids": [],
                    "llm_profile_id": None,
                },
            )

        self.assertEqual(response.status_code, 200, msg=response.text)

    def test_rewrite_draft_normalizes_source_html_before_persisting(self) -> None:
        from app.modules.llm import runtime as llm_runtime

        task_id = self._create_rewrite_ready_task()

        async def fake_generate_draft_content(**kwargs):
            self.assertEqual(kwargs["custom_body"], "用户改过正文")
            self.assertEqual(kwargs["custom_body_html"], "<p>用户改过正文</p>")
            raise llm_runtime.LLMRuntimeError("模型请求失败: 停止在源草稿持久化之后")

        with (
            patch(
                "app.modules.workspace.tasks.runtime.llm_runtime.ensure_llm_runtime_adaptation",
                AsyncMock(return_value=LLMRuntimeAdaptation("chat_completions", None)),
            ),
            patch(
                "app.modules.workspace.tasks.runtime.llm_runtime.generate_draft_content",
                AsyncMock(side_effect=fake_generate_draft_content),
            ),
        ):
            response = self.client.post(
                f"/api/email-tasks/{task_id}/rewrite-draft",
                json={
                    "subject": "点击瞬间主题",
                    "body_text": "用户改过正文",
                    "body_html": "<p>用户改过正文</p><script>alert(1)</script>",
                    "selected_material_ids": [],
                    "llm_profile_id": None,
                },
            )

        self.assertEqual(response.status_code, 502, msg=response.text)
        self.assertTrue(response.json()["detail"].startswith("模型请求失败"))
        self.assertRegex(response.headers["X-Request-ID"], r".+")

    def test_rewrite_draft_rejects_second_request_after_task_is_claimed(self) -> None:
        task_id = self._create_generating_workspace_rewrite_task()

        with patch(
            "app.modules.workspace.tasks.runtime.llm_runtime.generate_draft_content",
            AsyncMock(side_effect=AssertionError("已领取任务不能再次调用 LLM")),
        ):
            response = self.client.post(
                f"/api/email-tasks/{task_id}/rewrite-draft",
                json={
                    "subject": "第二次主题",
                    "body_text": "第二次正文",
                    "body_html": "<p>第二次正文</p>",
                    "selected_material_ids": [],
                    "llm_profile_id": None,
                },
            )

        self.assertEqual(response.status_code, 400, msg=response.text)
        self.assertEqual(response.json()["detail"], "AI 正在改写当前草稿，请稍后刷新")

    def test_template_mode_can_generate_draft_without_primary_material(self) -> None:
        identity_id = self._create_identity(with_imap=False)
        llm_id = self._create_llm()

        update_response = self.client.put(
            f"/api/identities/{identity_id}",
            json={
                "name": "模板身份",
                "email_address": "sender@example.com",
                "smtp_host": "smtp.example.com",
                "smtp_port": 465,
                "smtp_username": "sender@example.com",
                "smtp_password": "secret",
                "imap_host": "imap.example.com",
                "imap_port": 993,
                "imap_username": "sender@example.com",
                "imap_password": "secret",
                "default_language": "zh-CN",
                "outreach_generation_mode": "template",
                "outreach_template_subject": "申请与{{name}}老师交流",
                "outreach_template_body_text": "{{name}}老师您好，我是{{sender_name}}。",
                "outreach_template_body_html": "<p>{{name}}老师您好，我是{{sender_name}}。</p>",
                "match_threshold": None,
                "same_domain_cooldown_minutes": None,
                "is_default": True,
            },
        )
        self.assertEqual(update_response.status_code, 200, msg=update_response.text)

        professor_response = self.client.post(
            "/api/professors",
            json={
                "name": "模板导师",
                "email": "template@example.edu",
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

        ensure_response = self.client.post(
            f"/api/workspaces/{professor_id}/ensure-task",
            params={"identity_id": identity_id, "llm_profile_id": llm_id},
        )
        self.assertEqual(ensure_response.status_code, 200, msg=ensure_response.text)
        task_id = ensure_response.json()["current_task"]["id"]

        generate_response = self.client.post(
            f"/api/email-tasks/{task_id}/generate-draft",
        )
        self.assertEqual(generate_response.status_code, 200, msg=generate_response.text)
        payload = generate_response.json()
        self.assertEqual(payload["current_task"]["status"], "review_required")
        self.assertEqual(
            payload["current_task"]["generated_subject"], "申请与模板导师老师交流"
        )
        self.assertIn(
            "模板导师老师您好", payload["current_task"]["generated_content_text"]
        )

    def test_template_mode_regeneration_keeps_task_template_snapshot_after_default_changes(
        self,
    ) -> None:
        identity_id = self._create_identity(with_imap=False)
        llm_id = self._create_llm()

        update_response = self.client.put(
            f"/api/identities/{identity_id}",
            json=self._build_identity_payload(
                with_imap=False,
                outreach_generation_mode="template",
                outreach_template_subject="旧模板主题 {{name}}",
                outreach_template_body_text="旧模板正文 {{name}}",
                outreach_template_body_html="<p>旧模板正文 {{name}}</p>",
            ),
        )
        self.assertEqual(update_response.status_code, 200, msg=update_response.text)

        professor_response = self.client.post(
            "/api/professors",
            json={
                "name": "模板更新导师",
                "email": "template-update@example.edu",
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

        ensure_response = self.client.post(
            f"/api/workspaces/{professor_id}/ensure-task",
            params={"identity_id": identity_id, "llm_profile_id": llm_id},
        )
        self.assertEqual(ensure_response.status_code, 200, msg=ensure_response.text)
        task_id = ensure_response.json()["current_task"]["id"]

        first_generate = self.client.post(f"/api/email-tasks/{task_id}/generate-draft")
        self.assertEqual(first_generate.status_code, 200, msg=first_generate.text)
        self.assertEqual(
            first_generate.json()["current_task"]["generated_subject"],
            "旧模板主题 模板更新导师",
        )

        update_template_response = self.client.put(
            f"/api/identities/{identity_id}",
            json=self._build_identity_payload(
                with_imap=False,
                outreach_generation_mode="template",
                outreach_template_subject="新模板主题 {{name}}",
                outreach_template_body_text="新模板正文 {{name}}",
                outreach_template_body_html="<p>新模板正文 {{name}}</p>",
            ),
        )
        self.assertEqual(
            update_template_response.status_code, 200, msg=update_template_response.text
        )

        second_generate = self.client.post(f"/api/email-tasks/{task_id}/generate-draft")
        self.assertEqual(second_generate.status_code, 200, msg=second_generate.text)
        payload = second_generate.json()
        self.assertEqual(
            payload["current_task"]["generated_subject"], "旧模板主题 模板更新导师"
        )
        self.assertIn(
            "旧模板正文 模板更新导师", payload["current_task"]["generated_content_text"]
        )
        latest_draft = [
            message
            for message in payload["messages"]
            if message["direction"] == "draft"
        ][-1]
        self.assertEqual(latest_draft["subject"], "旧模板主题 模板更新导师")
        self.assertIn("旧模板正文 模板更新导师", latest_draft["content"])

    def test_workspace_template_summary_returns_backend_rendered_template(self) -> None:
        identity_id = self._create_identity(with_imap=False)
        llm_id = self._create_llm()

        update_response = self.client.put(
            f"/api/identities/{identity_id}",
            json=self._build_identity_payload(
                with_imap=False,
                outreach_generation_mode="template",
                outreach_template_subject="申请与 {{name}} 老师交流",
                outreach_template_body_text=(
                    "{{name}} 老师您好，我是 {{sender_name}}，关注 {{department}} 的 {{research_direction}}。"
                ),
                outreach_template_body_html=(
                    "<p>{{name}} 老师您好，我是 {{sender_name}}，关注 {{department}} 的 {{research_direction}}。</p>"
                ),
            ),
        )
        self.assertEqual(update_response.status_code, 200, msg=update_response.text)

        professor_response = self.client.post(
            "/api/professors",
            json={
                "name": "渲染导师",
                "email": "rendered-template@example.edu",
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

        ensure_response = self.client.post(
            f"/api/workspaces/{professor_id}/ensure-task",
            params={"identity_id": identity_id, "llm_profile_id": llm_id},
        )
        self.assertEqual(ensure_response.status_code, 200, msg=ensure_response.text)
        task = ensure_response.json()["current_task"]

        self.assertEqual(task["rendered_template_subject"], "申请与 渲染导师 老师交流")
        self.assertEqual(
            task["rendered_template_body_text"],
            "渲染导师 老师您好，我是 测试身份，关注 Computer Science 的 Agents。",
        )
        self.assertIn(
            "渲染导师 老师您好，我是 测试身份，关注 Computer Science 的 Agents。",
            task["rendered_template_body_html"],
        )

    def test_child_manual_template_regeneration_keeps_parent_template_snapshot(
        self,
    ) -> None:
        identity_id = self._create_identity(with_imap=False)
        llm_id = self._create_llm()
        update_response = self.client.put(
            f"/api/identities/{identity_id}",
            json=self._build_identity_payload(
                with_imap=False,
                outreach_generation_mode="template",
                outreach_template_subject="父任务旧主题 {{name}}",
                outreach_template_body_text="父任务旧正文 {{name}}",
                outreach_template_body_html="<p>父任务旧正文 {{name}}</p>",
            ),
        )
        self.assertEqual(update_response.status_code, 200, msg=update_response.text)

        professor_response = self.client.post(
            "/api/professors",
            json={
                "name": "子任务模板导师",
                "email": "child-template@example.edu",
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

        ensure_response = self.client.post(
            f"/api/workspaces/{professor_id}/ensure-task",
            params={"identity_id": identity_id, "llm_profile_id": llm_id},
        )
        self.assertEqual(ensure_response.status_code, 200, msg=ensure_response.text)
        parent_task_id = ensure_response.json()["current_task"]["id"]

        connection = sqlite3.connect(self.db_path)
        try:
            connection.execute(
                """
                UPDATE email_tasks
                SET status = 'sent',
                    sent_at = CURRENT_TIMESTAMP,
                    generated_subject = '父任务旧主题 子任务模板导师',
                    generated_content_text = '父任务旧正文 子任务模板导师',
                    generated_content_html = '<p>父任务旧正文 子任务模板导师</p>'
                WHERE id = ?
                """,
                (parent_task_id,),
            )
            connection.commit()
        finally:
            connection.close()

        follow_up_response = self.client.post(
            f"/api/email-tasks/{parent_task_id}/start-follow-up"
        )
        self.assertEqual(
            follow_up_response.status_code, 200, msg=follow_up_response.text
        )
        child_task = follow_up_response.json()["current_task"]
        self.assertEqual(child_task["parent_task_id"], parent_task_id)
        self.assertEqual(child_task["generated_subject"], None)
        child_task_id = child_task["id"]

        update_template_response = self.client.put(
            f"/api/identities/{identity_id}",
            json=self._build_identity_payload(
                with_imap=False,
                outreach_generation_mode="template",
                outreach_template_subject="子任务新主题 {{name}}",
                outreach_template_body_text="子任务新正文 {{name}}",
                outreach_template_body_html="<p>子任务新正文 {{name}}</p>",
            ),
        )
        self.assertEqual(
            update_template_response.status_code, 200, msg=update_template_response.text
        )

        generate_response = self.client.post(
            f"/api/email-tasks/{child_task_id}/generate-draft"
        )
        self.assertEqual(generate_response.status_code, 200, msg=generate_response.text)
        generated = generate_response.json()["current_task"]
        self.assertEqual(generated["generated_subject"], "父任务旧主题 子任务模板导师")
        self.assertIn(
            "父任务旧正文 子任务模板导师", generated["generated_content_text"]
        )

    def test_manual_send_renders_subject_and_body_placeholders(self) -> None:
        identity_id = self._create_identity(with_imap=False)
        llm_id = self._create_llm()
        professor_response = self.client.post(
            "/api/professors",
            json={
                "name": "主题导师",
                "email": "subject@example.edu",
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
        ensure_response = self.client.post(
            f"/api/workspaces/{professor_id}/ensure-task",
            params={"identity_id": identity_id, "llm_profile_id": llm_id},
        )
        self.assertEqual(ensure_response.status_code, 200, msg=ensure_response.text)
        task_id = ensure_response.json()["current_task"]["id"]

        with (
            patch(
                "app.modules.workspace.tasks.delivery.mail_runtime.send_email",
                AsyncMock(
                    return_value=self._build_send_result(
                        message_id="<subject-render@example.com>", provider_payload={}
                    )
                ),
            ) as mocked_send,
            patch(
                "app.modules.campaigns.templates.rendering.datetime",
            ) as mocked_datetime,
        ):
            from datetime import UTC, datetime

            mocked_datetime.now.return_value = datetime(2026, 5, 19, 16, 30, tzinfo=UTC)
            expected_local_date = mocked_datetime.now.return_value.astimezone()
            expected_date = f"{expected_local_date.year}年{expected_local_date.month}月{expected_local_date.day}日"
            response = self.client.post(
                f"/api/email-tasks/{task_id}/approve-and-send",
                json={
                    "subject": "申请与{{name}}老师交流",
                    "body_text": "{{name}}老师您好，我是{{sender_name}}。发送日期：{{year}}年{{month}}月{{day}}日。",
                    "body_html": "<p>{{name}}老师您好，我是{{sender_name}}。发送日期：{{year}}年{{month}}月{{day}}日。</p>",
                    "selected_material_ids": [],
                },
            )

        self.assertEqual(response.status_code, 200, msg=response.text)
        kwargs = mocked_send.await_args.kwargs
        self.assertEqual(kwargs["subject"], "申请与主题导师老师交流")
        self.assertIn("主题导师老师您好", kwargs["body_text"])
        self.assertIn(f"发送日期：{expected_date}", kwargs["body_text"])
        self.assertNotIn("{{name}}", kwargs["body_html"])
        self.assertNotIn("{{year}}", kwargs["body_html"])
        self.assertIn(f"发送日期：{expected_date}", kwargs["body_html"])
        self.assertEqual(
            response.json()["current_task"]["approved_subject"],
            "申请与{{name}}老师交流",
        )
        self.assertIn(
            "{{year}}年{{month}}月{{day}}日",
            response.json()["current_task"]["approved_body_text"],
        )

    def test_approve_draft_snapshots_content_without_immediate_send(self) -> None:
        identity_id = self._create_identity(with_imap=False)
        llm_id = self._create_llm()
        professor_response = self.client.post(
            "/api/professors",
            json={
                "name": "待审核导师",
                "email": "review@example.edu",
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
        ensure_response = self.client.post(
            f"/api/workspaces/{professor_id}/ensure-task",
            params={"identity_id": identity_id, "llm_profile_id": llm_id},
        )
        self.assertEqual(ensure_response.status_code, 200, msg=ensure_response.text)
        task_id = ensure_response.json()["current_task"]["id"]

        with patch(
            "app.modules.workspace.tasks.delivery.mail_runtime.send_email",
            AsyncMock(),
        ) as mocked_send:
            response = self.client.post(
                f"/api/email-tasks/{task_id}/approve",
                json={
                    "subject": "审核后的主题",
                    "body_text": "审核后的正文",
                    "body_html": "<p>审核后的正文</p>",
                    "selected_material_ids": [],
                },
            )

        self.assertEqual(response.status_code, 200, msg=response.text)
        self.assertEqual(response.json()["current_task"]["status"], "approved")
        self.assertEqual(
            response.json()["current_task"]["approved_subject"], "审核后的主题"
        )
        mocked_send.assert_not_awaited()

    def test_save_workspace_draft_persists_edited_content_without_approving(
        self,
    ) -> None:
        identity_id = self._create_identity(with_imap=False)
        llm_id = self._create_llm()
        professor_response = self.client.post(
            "/api/professors",
            json={
                "name": "保存草稿导师",
                "email": "save-draft@example.edu",
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
        material_id = self._upload_material(
            identity_id,
            filename="save-draft-resume.txt",
            content=b"resume",
            material_type="resume",
        )
        task_id = self._insert_email_task_with_material(
            identity_id=identity_id,
            llm_id=llm_id,
            professor_id=professor_id,
            status="review_required",
            primary_material_id=material_id,
            selected_material_ids=[material_id],
            generated_subject="AI 原始主题",
            generated_content_text="AI 原始正文",
            generated_content_html="<p>AI 原始正文</p>",
        )

        response = self.client.post(
            f"/api/email-tasks/{task_id}/save-draft",
            json={
                "subject": "用户编辑主题",
                "body_text": "用户编辑正文",
                "body_html": "<p>用户编辑正文</p>",
                "selected_material_ids": [],
            },
        )

        self.assertEqual(response.status_code, 200, msg=response.text)
        current_task = response.json()["current_task"]
        self.assertEqual(current_task["status"], "review_required")
        self.assertEqual(current_task["approved_subject"], "用户编辑主题")
        self.assertEqual(current_task["approved_body_text"], "用户编辑正文")
        self.assertEqual(current_task["approved_body_html"], "<p>用户编辑正文</p>")
        self.assertEqual(current_task["selected_material_ids"], [])
        stored_task = self._get_email_task_delete_state(task_id)
        self.assertEqual(stored_task["status"], "review_required")
        self.assertEqual(stored_task["approved_subject"], "用户编辑主题")
        self.assertIsNotNone(stored_task["approved_at"])

    def test_save_workspace_draft_preserves_empty_subject(self) -> None:
        identity_id = self._create_identity(with_imap=False)
        llm_id = self._create_llm()
        professor_id = self._create_professor(
            email="empty-subject-save-draft@example.edu"
        )
        task_id = self._insert_email_task_with_material(
            identity_id=identity_id,
            llm_id=llm_id,
            professor_id=professor_id,
            status="review_required",
            primary_material_id=None,
            generated_subject="AI 原始主题",
            generated_content_text="AI 原始正文",
            generated_content_html="<p>AI 原始正文</p>",
        )

        response = self.client.post(
            f"/api/email-tasks/{task_id}/save-draft",
            json={
                "subject": "",
                "body_text": "保留正文",
                "body_html": "<p>保留正文</p>",
                "selected_material_ids": [],
            },
        )

        self.assertEqual(response.status_code, 200, msg=response.text)
        current_task = response.json()["current_task"]
        self.assertEqual(current_task["approved_subject"], "")
        self.assertEqual(current_task["approved_body_text"], "保留正文")
        stored_task = self._get_email_task_delete_state(task_id)
        self.assertEqual(stored_task["approved_subject"], "")

    def test_save_workspace_draft_preserves_empty_body(self) -> None:
        identity_id = self._create_identity(with_imap=False)
        llm_id = self._create_llm()
        professor_id = self._create_professor(email="empty-body-save-draft@example.edu")
        task_id = self._insert_email_task_with_material(
            identity_id=identity_id,
            llm_id=llm_id,
            professor_id=professor_id,
            status="review_required",
            primary_material_id=None,
            generated_subject="AI 原始主题",
            generated_content_text="AI 原始正文",
            generated_content_html="<p>AI 原始正文</p>",
        )

        response = self.client.post(
            f"/api/email-tasks/{task_id}/save-draft",
            json={
                "subject": "只保留主题",
                "body_text": "",
                "body_html": "",
                "selected_material_ids": [],
            },
        )

        self.assertEqual(response.status_code, 200, msg=response.text)
        current_task = response.json()["current_task"]
        self.assertEqual(current_task["approved_subject"], "只保留主题")
        self.assertEqual(current_task["approved_body_text"], "")
        self.assertEqual(current_task["approved_body_html"], "")
        self.assertEqual(current_task["draft"]["source"], "saved")
        self.assertFalse(current_task["draft"]["sendable"])
        stored_task = self._get_email_task_delete_state(task_id)
        self.assertEqual(stored_task["approved_body_text"], "")
        self.assertEqual(stored_task["approved_body_html"], "")

    def test_save_workspace_draft_preserves_empty_tiptap_body(self) -> None:
        identity_id = self._create_identity(with_imap=False)
        llm_id = self._create_llm()
        professor_id = self._create_professor(
            email="empty-tiptap-body-save-draft@example.edu"
        )
        task_id = self._insert_email_task_with_material(
            identity_id=identity_id,
            llm_id=llm_id,
            professor_id=professor_id,
            status="review_required",
            primary_material_id=None,
            generated_subject="AI 原始主题",
            generated_content_text="AI 原始正文",
            generated_content_html="<p>AI 原始正文</p>",
        )

        response = self.client.post(
            f"/api/email-tasks/{task_id}/save-draft",
            json={
                "subject": "只保留主题",
                "body_text": "",
                "body_html": "<p></p>",
                "selected_material_ids": [],
            },
        )

        self.assertEqual(response.status_code, 200, msg=response.text)
        current_task = response.json()["current_task"]
        self.assertEqual(current_task["approved_subject"], "只保留主题")
        self.assertEqual(current_task["approved_body_text"], "")
        self.assertEqual(current_task["approved_body_html"], "")
        self.assertEqual(current_task["draft"]["source"], "saved")
        self.assertFalse(current_task["draft"]["sendable"])
        stored_task = self._get_email_task_delete_state(task_id)
        self.assertEqual(stored_task["approved_body_text"], "")
        self.assertEqual(stored_task["approved_body_html"], "")

    def test_save_workspace_draft_rejects_sent_task_without_mutating_snapshot(
        self,
    ) -> None:
        identity_id = self._create_identity(with_imap=False)
        llm_id = self._create_llm()
        professor_id = self._create_professor(email="sent-save-draft@example.edu")
        task_id = self._insert_email_task_with_material(
            identity_id=identity_id,
            llm_id=llm_id,
            professor_id=professor_id,
            status="sent",
            primary_material_id=None,
            approved_subject="真实发送主题",
            approved_body_text="真实发送正文",
            approved_body_html="<p>真实发送正文</p>",
        )

        response = self.client.post(
            f"/api/email-tasks/{task_id}/save-draft",
            json={
                "subject": "错误覆盖主题",
                "body_text": "错误覆盖正文",
                "body_html": "<p>错误覆盖正文</p>",
                "selected_material_ids": [],
            },
        )

        self.assertEqual(response.status_code, 400, msg=response.text)
        self.assertIn("当前状态不能保存草稿", response.json()["detail"])
        stored_task = self._get_email_task_delete_state(task_id)
        self.assertEqual(stored_task["status"], "sent")
        self.assertEqual(stored_task["approved_subject"], "真实发送主题")
        self.assertEqual(stored_task["approved_body_text"], "真实发送正文")
        self.assertEqual(stored_task["approved_body_html"], "<p>真实发送正文</p>")

    def test_save_workspace_draft_rejects_canceled_task_without_mutating_snapshot(
        self,
    ) -> None:
        identity_id = self._create_identity(with_imap=False)
        llm_id = self._create_llm()
        professor_id = self._create_professor(email="canceled-save-draft@example.edu")
        task_id = self._insert_email_task_with_material(
            identity_id=identity_id,
            llm_id=llm_id,
            professor_id=professor_id,
            status="canceled",
            primary_material_id=None,
            approved_subject="取消前主题",
            approved_body_text="取消前正文",
            approved_body_html="<p>取消前正文</p>",
        )

        response = self.client.post(
            f"/api/email-tasks/{task_id}/save-draft",
            json={
                "subject": "错误覆盖取消主题",
                "body_text": "错误覆盖取消正文",
                "body_html": "<p>错误覆盖取消正文</p>",
                "selected_material_ids": [],
            },
        )

        self.assertEqual(response.status_code, 400, msg=response.text)
        self.assertIn("当前状态不能保存草稿", response.json()["detail"])
        stored_task = self._get_email_task_delete_state(task_id)
        self.assertEqual(stored_task["status"], "canceled")
        self.assertEqual(stored_task["approved_subject"], "取消前主题")
        self.assertEqual(stored_task["approved_body_text"], "取消前正文")
        self.assertEqual(stored_task["approved_body_html"], "<p>取消前正文</p>")

    def test_identity_llm_mode_allows_empty_template_defaults(self) -> None:
        response = self.client.post(
            "/api/identities",
            json=self._build_identity_payload(
                with_imap=False,
                outreach_generation_mode="llm",
                outreach_template_subject=None,
                outreach_template_body_text=None,
                outreach_template_body_html=None,
            ),
        )

        self.assertEqual(response.status_code, 201, msg=response.text)
        self.assertIsNone(response.json()["outreach_template_subject"])
        self.assertIsNone(response.json()["outreach_template_body_text"])
        self.assertIsNone(response.json()["outreach_template_body_html"])

    def test_delete_material_resets_review_required_primary_material_draft(
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
        professor_id = self._create_professor(
            email="review-material-delete@example.edu"
        )
        task_id = self._insert_email_task_with_material(
            identity_id=identity_id,
            llm_id=llm_id,
            professor_id=professor_id,
            status="review_required",
            primary_material_id=material_id,
            generated_subject="旧草稿主题",
            generated_content_text="旧草稿正文",
            generated_content_html="<p>旧草稿正文</p>",
            approved_subject="已审核主题",
            approved_body_text="已审核正文",
            approved_body_html="<p>已审核正文</p>",
            match_score=88,
            match_reason="方向匹配",
        )

        delete_response = self.client.delete(f"/api/materials/{material_id}")

        self.assertEqual(delete_response.status_code, 204, msg=delete_response.text)
        state = self._get_email_task_delete_state(task_id)
        self.assertEqual(state["status"], "matched")
        self.assertIsNone(state["primary_material_id"])
        self.assertIsNone(state["generated_subject"])
        self.assertIsNone(state["generated_content_text"])
        self.assertIsNone(state["generated_content_html"])
        self.assertIsNone(state["approved_subject"])
        self.assertIsNone(state["approved_body_text"])
        self.assertIsNone(state["approved_body_html"])
        self.assertIsNone(state["approved_at"])

    def test_delete_material_clears_draft_failed_primary_material_reference(
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
        professor_id = self._create_professor(
            email="draft-failed-material-delete@example.edu"
        )
        task_id = self._insert_email_task_with_material(
            identity_id=identity_id,
            llm_id=llm_id,
            professor_id=professor_id,
            status="draft_failed",
            primary_material_id=material_id,
        )

        delete_response = self.client.delete(f"/api/materials/{material_id}")

        self.assertEqual(delete_response.status_code, 204, msg=delete_response.text)
        primary_material_id, selected_material_ids = self._get_task_material_references(
            task_id
        )
        self.assertIsNone(primary_material_id)
        self.assertIsNone(selected_material_ids)

    def test_primary_material_text_is_extracted_on_demand_when_generating_draft(
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

        connection = sqlite3.connect(self.db_path)
        try:
            row = connection.execute(
                "SELECT extracted_text FROM identity_materials WHERE id = ?",
                (material_id,),
            ).fetchone()
        finally:
            connection.close()
        self.assertEqual(row[0], None)

        self.client.post("/api/professors/import-sample")
        professor_id = self.client.get("/api/professors").json()[0]["id"]
        task_response = self.client.post(
            "/api/batch-tasks",
            json={
                "identity_id": identity_id,
                "llm_profile_id": llm_id,
                "name": "按需解析默认材料",
                "professor_ids": [professor_id],
                "schedule_type": "immediate",
                "window_start_time": None,
                "window_end_time": None,
                "emails_per_window": None,
                "primary_material_id": material_id,
                "email_subject": "申请与{{name}}老师交流",
                "email_body": "老师您好，我是{{sender_name}}，关注到您在{{research_direction}}方向的工作。",
                "selected_material_ids": None,
            },
        )
        self.assertEqual(task_response.status_code, 201)

        batch_task_id = task_response.json()["id"]
        task_id = self.client.get(f"/api/batch-tasks/{batch_task_id}/items").json()[0][
            "id"
        ]

        with patch(
            "app.modules.workspace.tasks.runtime.llm_runtime.generate_draft_content",
            AsyncMock(
                return_value=self._build_draft_generation_result(
                    subject="测试草稿",
                    body_text="测试正文",
                    body_html="<p>测试正文</p>",
                    prompt_tokens=80,
                    completion_tokens=20,
                    cached_tokens=32,
                    prompt_hash="a" * 64,
                    stable_prefix_hash="b" * 64,
                    prompt_cache_key="draft-rewrite:v5:generation-test",
                ),
            ),
        ):
            regenerate_response = self.client.post(
                f"/api/email-tasks/{task_id}/generate-draft",
            )

        self.assertEqual(regenerate_response.status_code, 200)
        connection = sqlite3.connect(self.db_path)
        try:
            refreshed_row = connection.execute(
                "SELECT extracted_text FROM identity_materials WHERE id = ?",
                (material_id,),
            ).fetchone()
        finally:
            connection.close()
        self.assertIn("information extraction", refreshed_row[0])
        provider_payload = self._latest_email_log_provider_payload()
        self.assertEqual(provider_payload["usage"]["cached_tokens"], 32)
        self.assertEqual(provider_payload["prompt_hash"], "a" * 64)
        self.assertEqual(provider_payload["stable_prefix_hash"], "b" * 64)
        self.assertEqual(
            provider_payload["prompt_cache_key"],
            "draft-rewrite:v5:generation-test",
        )
        operation_logs = self.client.get(
            "/api/diagnostics/operation-logs",
            params={"event_name": "email_task.draft_generated"},
        )
        self.assertEqual(operation_logs.status_code, 200, msg=operation_logs.text)
        metadata = operation_logs.json()["items"][0]["metadata"]
        self.assertEqual(metadata["prompt_hash"], "a" * 64)
        self.assertEqual(metadata["stable_prefix_hash"], "b" * 64)
        self.assertEqual(
            metadata["prompt_cache_key"], "draft-rewrite:v5:generation-test"
        )

    def test_manual_generation_cancellation_restores_previous_status(self) -> None:
        identity_id = self._create_identity(with_imap=False)
        llm_profile_id = self._create_llm()
        material_id = self._upload_material(
            identity_id,
            filename="manual-cancel-resume.txt",
            content=b"AI agents and information extraction",
            material_type="resume",
        )
        professor_id = self._create_professor(
            email="manual-generation-canceled@example.edu"
        )
        ensure_response = self.client.post(
            f"/api/workspaces/{professor_id}/ensure-task",
            params={"identity_id": identity_id, "llm_profile_id": llm_profile_id},
        )
        self.assertEqual(ensure_response.status_code, 200, msg=ensure_response.text)
        task_id = ensure_response.json()["current_task"]["id"]

        connection = sqlite3.connect(self.db_path)
        try:
            connection.execute(
                """
                UPDATE email_tasks
                SET primary_material_id = ?,
                    selected_material_ids = ?,
                    outreach_generation_mode = 'llm',
                    outreach_template_subject = ?,
                    outreach_template_body_text = ?,
                    status = 'matched'
                WHERE id = ?
                """,
                (
                    material_id,
                    json.dumps([material_id]),
                    "Hello {{name}}",
                    "Body {{research_direction}}",
                    task_id,
                ),
            )
            connection.commit()
        finally:
            connection.close()

        async def _cancel_generation(**_kwargs):
            raise asyncio.CancelledError()

        from app.core.database import get_session_factory
        from app.modules.workspace.tasks.runtime import generate_task_draft

        with patch(
            "app.modules.workspace.tasks.runtime.llm_runtime.generate_draft_content",
            AsyncMock(side_effect=_cancel_generation),
        ):
            with self.assertRaises(asyncio.CancelledError):
                self._run_async(
                    generate_task_draft(get_session_factory(), task_id, force=True)
                )

        connection = sqlite3.connect(self.db_path)
        try:
            row = connection.execute(
                """
                SELECT status, draft_generation_previous_status, generated_subject, generated_content_text
                FROM email_tasks
                WHERE id = ?
                """,
                (task_id,),
            ).fetchone()
        finally:
            connection.close()
        self.assertEqual(row, ("matched", None, None, None))

    def test_generate_draft_requires_professor_research_direction(self) -> None:
        identity_id = self._create_identity(with_imap=False)
        llm_id = self._create_llm()
        self._upload_material(
            identity_id,
            filename="resume.txt",
            content=b"My background covers agent systems.",
            material_type="resume",
        )

        professor_response = self.client.post(
            "/api/professors",
            json={
                "name": "李老师",
                "email": "li-missing-research@example.edu",
                "title": "Professor",
                "university": "Example University",
                "school": "School of Computing",
                "department": "Computer Science",
                "research_direction": None,
                "recent_papers": ["Agent paper"],
                "profile_url": None,
                "source_url": None,
            },
        )
        self.assertEqual(
            professor_response.status_code, 201, msg=professor_response.text
        )
        professor_id = professor_response.json()["id"]

        workspace = self.client.post(
            f"/api/workspaces/{professor_id}/ensure-task",
            params={"identity_id": identity_id, "llm_profile_id": llm_id},
        )
        self.assertEqual(workspace.status_code, 200, msg=workspace.text)
        task_id = workspace.json()["current_task"]["id"]

        with patch(
            "app.modules.workspace.tasks.runtime.llm_runtime.generate_draft_content",
            AsyncMock(
                return_value=self._build_draft_generation_result(
                    subject="不应生成的草稿",
                    body_text="这封草稿不应在缺少研究方向时生成。",
                    body_html="<p>这封草稿不应在缺少研究方向时生成。</p>",
                ),
            ),
        ) as mocked_generate:
            response = self.client.post(f"/api/email-tasks/{task_id}/generate-draft")

        self.assertEqual(response.status_code, 400)
        self.assertIn("请先补充导师研究方向", response.json()["detail"])
        mocked_generate.assert_not_awaited()

        refreshed = self.client.get(
            f"/api/workspaces/{professor_id}",
            params={"identity_id": identity_id, "llm_profile_id": llm_id},
        )
        self.assertEqual(refreshed.status_code, 200, msg=refreshed.text)
        self.assertFalse(
            any(
                message["direction"] == "draft"
                for message in refreshed.json()["messages"]
            ),
        )

    def test_template_draft_does_not_require_professor_research_direction(self) -> None:
        identity_response = self.client.post(
            "/api/identities",
            json=self._build_identity_payload(
                with_imap=False,
                outreach_generation_mode="template",
                outreach_template_subject="申请与{{name}}老师交流",
                outreach_template_body_text="{{name}}老师您好，我是{{sender_name}}。",
            ),
        )
        self.assertEqual(identity_response.status_code, 201, msg=identity_response.text)
        identity_id = identity_response.json()["id"]
        llm_id = self._create_llm()

        professor_response = self.client.post(
            "/api/professors",
            json={
                "name": "模板模式导师",
                "email": "template-no-research@example.edu",
                "title": "Professor",
                "university": "Example University",
                "school": "School of Computing",
                "department": "Computer Science",
                "research_direction": None,
                "recent_papers": [],
                "profile_url": None,
                "source_url": None,
            },
        )
        self.assertEqual(
            professor_response.status_code, 201, msg=professor_response.text
        )
        professor_id = professor_response.json()["id"]

        workspace = self.client.post(
            f"/api/workspaces/{professor_id}/ensure-task",
            params={"identity_id": identity_id, "llm_profile_id": llm_id},
        )
        self.assertEqual(workspace.status_code, 200, msg=workspace.text)
        task_id = workspace.json()["current_task"]["id"]

        with patch(
            "app.modules.workspace.tasks.runtime.llm_runtime.generate_draft_content",
            AsyncMock(side_effect=AssertionError("模板模式不应调用 LLM 草稿生成")),
        ) as mocked_generate:
            response = self.client.post(f"/api/email-tasks/{task_id}/generate-draft")

        self.assertEqual(response.status_code, 200, msg=response.text)
        self.assertEqual(response.json()["current_task"]["status"], "review_required")
        self.assertEqual(response.json()["messages"][-1]["direction"], "draft")
        mocked_generate.assert_not_awaited()

    def test_draft_preview_returns_content_without_persisting_changes(self) -> None:
        identity_id = self._create_identity(with_imap=False)
        llm_id = self._create_llm()
        self._upload_material(
            identity_id,
            filename="resume.txt",
            content=b"My background covers agent systems.",
            material_type="resume",
        )

        professor_response = self.client.post(
            "/api/professors",
            json={
                "name": "预览导师",
                "email": "preview-research@example.edu",
                "title": "Professor",
                "university": "Example University",
                "school": "School of Computing",
                "department": "Computer Science",
                "research_direction": "Agent systems",
                "recent_papers": ["Agent paper"],
                "profile_url": None,
                "source_url": None,
            },
        )
        self.assertEqual(
            professor_response.status_code, 201, msg=professor_response.text
        )
        professor_id = professor_response.json()["id"]

        workspace = self.client.post(
            f"/api/workspaces/{professor_id}/ensure-task",
            params={"identity_id": identity_id, "llm_profile_id": llm_id},
        )
        self.assertEqual(workspace.status_code, 200, msg=workspace.text)
        task_id = workspace.json()["current_task"]["id"]

        with patch(
            "app.modules.workspace.tasks.runtime.llm_runtime.generate_draft_content",
            AsyncMock(
                return_value=self._build_draft_generation_result(
                    subject="预览主题",
                    body_text="预览正文",
                    body_html="<p>预览正文</p>",
                    prompt_tokens=120,
                    completion_tokens=30,
                ),
            ),
        ) as mocked_generate:
            response = self.client.post(f"/api/email-tasks/{task_id}/draft-preview")

        self.assertEqual(response.status_code, 200, msg=response.text)
        preview = response.json()
        self.assertEqual(preview["subject"], "预览主题")
        self.assertEqual(preview["body_text"], "预览正文")
        self.assertEqual(preview["usage"]["prompt_tokens"], 120)
        mocked_generate.assert_awaited_once()

        refreshed = self.client.get(
            f"/api/workspaces/{professor_id}",
            params={"identity_id": identity_id, "llm_profile_id": llm_id},
        )
        self.assertEqual(refreshed.status_code, 200, msg=refreshed.text)
        self.assertIsNone(refreshed.json()["current_task"]["generated_subject"])
        self.assertFalse(
            any(
                message["direction"] == "draft"
                for message in refreshed.json()["messages"]
            ),
        )

    def test_generate_draft_returns_bad_gateway_when_llm_fails(self) -> None:
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
                "name": "失败提示导师",
                "email": "draft-failure@example.edu",
                "title": "Professor",
                "university": "Example University",
                "school": "School of Computing",
                "department": "Computer Science",
                "research_direction": "Information Extraction",
                "recent_papers": ["Agent paper"],
                "profile_url": None,
                "source_url": None,
            },
        )
        self.assertEqual(
            professor_response.status_code, 201, msg=professor_response.text
        )
        professor_id = professor_response.json()["id"]

        workspace = self.client.post(
            f"/api/workspaces/{professor_id}/ensure-task",
            params={"identity_id": identity_id, "llm_profile_id": llm_id},
        )
        self.assertEqual(workspace.status_code, 200, msg=workspace.text)
        task_id = workspace.json()["current_task"]["id"]

        from app.modules.llm import runtime as llm_runtime

        with patch(
            "app.modules.workspace.tasks.runtime.llm_runtime.generate_draft_content",
            AsyncMock(
                side_effect=llm_runtime.LLMRuntimeError("模型未返回可用改写内容")
            ),
        ):
            response = self.client.post(f"/api/email-tasks/{task_id}/generate-draft")

        self.assertEqual(response.status_code, 502, msg=response.text)
        self.assertEqual(response.json()["detail"], "模型未返回可用改写内容")

        connection = sqlite3.connect(self.db_path)
        try:
            row = connection.execute(
                "SELECT status, last_error, generated_subject FROM email_tasks WHERE id = ?",
                (task_id,),
            ).fetchone()
        finally:
            connection.close()
        self.assertEqual(row[0], "discovered")
        self.assertEqual(row[1], "模型未返回可用改写内容")
        self.assertIsNone(row[2])

    def test_calculate_match_preserves_sent_task_follow_up_action(self) -> None:
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
                "name": "已发送后分析导师",
                "email": "sent-match@example.edu",
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

        ensure_response = self.client.post(
            f"/api/workspaces/{professor_id}/ensure-task",
            params={"identity_id": identity_id, "llm_profile_id": llm_id},
        )
        self.assertEqual(ensure_response.status_code, 200, msg=ensure_response.text)
        task_id = ensure_response.json()["current_task"]["id"]

        connection = sqlite3.connect(self.db_path)
        try:
            connection.execute(
                """
                UPDATE email_tasks
                SET status = 'sent',
                    sent_at = CURRENT_TIMESTAMP,
                    approved_subject = '已发送主题',
                    approved_body_text = '已发送正文',
                    approved_body_html = '<p>已发送正文</p>',
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (task_id,),
            )
            connection.commit()
        finally:
            connection.close()

        workspace_before = self.client.get(
            f"/api/workspaces/{professor_id}",
            params={"identity_id": identity_id, "llm_profile_id": llm_id},
        )
        self.assertEqual(workspace_before.status_code, 200, msg=workspace_before.text)
        self.assertEqual(workspace_before.json()["current_task"]["status"], "sent")
        self.assertTrue(workspace_before.json()["current_task"]["can_write_follow_up"])

        with patch(
            "app.modules.matching.task_analysis.llm_runtime.generate_match_evaluation",
            AsyncMock(return_value=self._build_match_evaluation_result(match_score=92)),
        ):
            response = self.client.post(f"/api/email-tasks/{task_id}/calculate-match")

        self.assertEqual(response.status_code, 200, msg=response.text)
        current_task = response.json()["thread"]["current_task"]
        self.assertEqual(current_task["match_score"], 92)
        self.assertEqual(current_task["status"], "sent")
        self.assertTrue(current_task["can_write_follow_up"])

    def test_workspace_thread_includes_professor_profile_enrichment_fields(
        self,
    ) -> None:
        identity_id = self._create_identity(with_imap=False)
        llm_id = self._create_llm()

        professor_response = self.client.post(
            "/api/professors",
            json={
                "name": "只有论文导师",
                "email": "paper-only@example.edu",
                "title": "Professor",
                "university": "Example University",
                "school": "School of AI",
                "department": "Computer Science",
                "research_direction": None,
                "recent_papers": ["Paper Evidence"],
                "profile_url": "https://example.edu/faculty/paper-only",
                "source_url": None,
            },
        )
        self.assertEqual(
            professor_response.status_code, 201, msg=professor_response.text
        )
        professor_id = professor_response.json()["id"]

        response = self.client.post(
            f"/api/workspaces/{professor_id}/ensure-task",
            params={"identity_id": identity_id, "llm_profile_id": llm_id},
        )

        self.assertEqual(response.status_code, 200, msg=response.text)
        self.assertEqual(
            response.json()["professor"]["recent_papers"], ["Paper Evidence"]
        )
        self.assertEqual(
            response.json()["professor"]["profile_url"],
            "https://example.edu/faculty/paper-only",
        )

    def test_continue_manually_restores_matched_when_parent_has_match_without_draft(
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
        set_primary_response = self.client.post(
            f"/api/materials/{primary_material_id}/set-primary"
        )
        self.assertEqual(
            set_primary_response.status_code, 200, msg=set_primary_response.text
        )

        professor_response = self.client.post(
            "/api/professors",
            json={
                "name": "无草稿已匹配导师",
                "email": "continue-matched@example.edu",
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
                "name": "继续联系匹配分支",
                "professor_ids": [professor_id],
                "schedule_type": "immediate",
                "window_start_time": None,
                "window_end_time": None,
                "emails_per_window": None,
                "primary_material_id": primary_material_id,
                "email_subject": "联系 {{name}}",
                "email_body": "联系正文 {{name}}",
                "selected_material_ids": None,
            },
        )
        self.assertEqual(create_response.status_code, 201, msg=create_response.text)
        batch_task_id = create_response.json()["id"]

        connection = sqlite3.connect(self.db_path)
        try:
            parent_task_id = connection.execute(
                "SELECT id FROM email_tasks WHERE batch_task_id = ?",
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
                    generated_subject = NULL,
                    generated_content_text = NULL,
                    generated_content_html = NULL,
                    approved_subject = NULL,
                    approved_body_text = NULL,
                    approved_body_html = NULL
                WHERE id = ?
                """,
                (
                    "canceled",
                    "batch_stopped",
                    75,
                    "匹配结果仍可复用",
                    json.dumps(["方向契合"]),
                    json.dumps(["需补充研究计划"]),
                    json.dumps(["match"]),
                    parent_task_id,
                ),
            )
            connection.commit()
        finally:
            connection.close()

        response = self.client.post(
            f"/api/email-tasks/{parent_task_id}/continue-manually"
        )

        self.assertEqual(response.status_code, 200, msg=response.text)
        current_task = response.json()["current_task"]
        self.assertEqual(current_task["status"], "matched")
        self.assertEqual(current_task["parent_task_id"], parent_task_id)
        self.assertIsNone(current_task["generated_subject"])
        self.assertIsNone(current_task["generated_content_text"])
        self.assertIsNone(current_task["generated_content_html"])
        self.assertEqual(current_task["match_score"], 75)
        self.assertEqual(current_task["match_reason"], "匹配结果仍可复用")

    def test_continue_manually_restores_discovered_when_parent_has_no_match(
        self,
    ) -> None:
        identity_id = self._create_identity(with_imap=False)
        llm_id = self._create_llm()

        professor_response = self.client.post(
            "/api/professors",
            json={
                "name": "未匹配导师",
                "email": "continue-discovered@example.edu",
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
                "name": "继续联系 discovered 分支",
                "professor_ids": [professor_id],
                "schedule_type": "immediate",
                "window_start_time": None,
                "window_end_time": None,
                "emails_per_window": None,
                "primary_material_id": None,
                "email_subject": "联系 {{name}}",
                "email_body": "联系正文 {{name}}",
                "selected_material_ids": None,
            },
        )
        self.assertEqual(create_response.status_code, 201, msg=create_response.text)
        batch_task_id = create_response.json()["id"]

        connection = sqlite3.connect(self.db_path)
        try:
            parent_task_id = connection.execute(
                "SELECT id FROM email_tasks WHERE batch_task_id = ?",
                (batch_task_id,),
            ).fetchone()[0]
            connection.execute(
                """
                UPDATE email_tasks
                SET status = ?,
                    cancellation_reason = ?,
                    match_score = NULL,
                    match_reason = NULL,
                    fit_points = NULL,
                    risk_points = NULL,
                    match_keywords = NULL,
                    generated_subject = NULL,
                    generated_content_text = NULL,
                    generated_content_html = NULL,
                    approved_subject = NULL,
                    approved_body_text = NULL,
                    approved_body_html = NULL
                WHERE id = ?
                """,
                ("canceled", "batch_stopped", parent_task_id),
            )
            connection.commit()
        finally:
            connection.close()

        response = self.client.post(
            f"/api/email-tasks/{parent_task_id}/continue-manually"
        )

        self.assertEqual(response.status_code, 200, msg=response.text)
        current_task = response.json()["current_task"]
        self.assertEqual(current_task["status"], "discovered")
        self.assertEqual(current_task["parent_task_id"], parent_task_id)
        self.assertIsNone(current_task["match_score"])
        self.assertIsNone(current_task["match_reason"])
        self.assertIsNone(current_task["generated_subject"])

    def test_continue_manually_rejects_duplicate_manual_child_creation(self) -> None:
        identity_id = self._create_identity(with_imap=False)
        llm_id = self._create_llm()

        professor_response = self.client.post(
            "/api/professors",
            json={
                "name": "继续联系重复派生导师",
                "email": "continue-duplicate@example.edu",
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
                "name": "继续联系重复派生",
                "professor_ids": [professor_id],
                "schedule_type": "immediate",
                "window_start_time": None,
                "window_end_time": None,
                "emails_per_window": None,
                "primary_material_id": None,
                "email_subject": "联系 {{name}}",
                "email_body": "联系正文 {{name}}",
                "selected_material_ids": None,
            },
        )
        self.assertEqual(create_response.status_code, 201, msg=create_response.text)
        batch_task_id = create_response.json()["id"]

        connection = sqlite3.connect(self.db_path)
        try:
            parent_task_id = connection.execute(
                "SELECT id FROM email_tasks WHERE batch_task_id = ?",
                (batch_task_id,),
            ).fetchone()[0]
            connection.execute(
                """
                UPDATE email_tasks
                SET status = ?, cancellation_reason = ?
                WHERE id = ?
                """,
                ("canceled", "batch_stopped", parent_task_id),
            )
            connection.commit()
        finally:
            connection.close()

        first_response = self.client.post(
            f"/api/email-tasks/{parent_task_id}/continue-manually"
        )
        second_response = self.client.post(
            f"/api/email-tasks/{parent_task_id}/continue-manually"
        )

        self.assertEqual(first_response.status_code, 200, msg=first_response.text)
        self.assertEqual(second_response.status_code, 400, msg=second_response.text)

    def test_continue_manually_returns_404_for_missing_task(self) -> None:
        response = self.client.post("/api/email-tasks/9999/continue-manually")

        self.assertEqual(response.status_code, 404, msg=response.text)
        self.assertEqual(response.json()["detail"], "EmailTask 9999 不存在")

    def test_workspace_recovers_legacy_matched_task_with_sent_at(self) -> None:
        identity_id = self._create_identity(with_imap=False)
        llm_id = self._create_llm()
        primary_material_id = self._upload_material(
            identity_id,
            filename="resume.txt",
            content=b"My background covers agent systems and information extraction.",
            material_type="resume",
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
                "name": "旧状态跟进导师",
                "email": "legacy-follow-up@example.edu",
                "title": "Professor",
                "university": "Example University",
                "school": "School of Computing",
                "department": "Computer Science",
                "research_direction": "Information extraction",
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

        connection = sqlite3.connect(self.db_path)
        try:
            connection.execute(
                """
                UPDATE email_tasks
                SET status = 'matched',
                    sent_at = CURRENT_TIMESTAMP,
                    approved_subject = '已发送主题',
                    approved_body_text = '已发送正文',
                    approved_body_html = '<p>已发送正文</p>',
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (task_id,),
            )
            connection.commit()
        finally:
            connection.close()

        workspace_response = self.client.get(
            f"/api/workspaces/{professor_id}",
            params={"identity_id": identity_id, "llm_profile_id": llm_id},
        )

        self.assertEqual(
            workspace_response.status_code, 200, msg=workspace_response.text
        )
        current_task = workspace_response.json()["current_task"]
        self.assertEqual(current_task["id"], task_id)
        self.assertEqual(current_task["status"], "sent")
        self.assertTrue(current_task["can_write_follow_up"])

    def test_start_follow_up_creates_manual_child_task_from_sent_task(self) -> None:
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
            filename="portfolio.pdf",
            content=b"%PDF-1.4 follow up attachment",
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
                "name": "跟进邮件导师",
                "email": "follow-up@example.edu",
                "title": "Professor",
                "university": "Example University",
                "school": "School of Computing",
                "department": "Computer Science",
                "research_direction": "Information extraction",
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
        parent_task_id = ensure_response.json()["current_task"]["id"]

        connection = sqlite3.connect(self.db_path)
        try:
            connection.execute(
                """
                UPDATE email_tasks
                SET status = ?,
                    source = ?,
                    primary_material_id = ?,
                    match_score = ?,
                    match_reason = ?,
                    fit_points = ?,
                    risk_points = ?,
                    match_keywords = ?,
                    generated_subject = ?,
                    generated_content_text = ?,
                    generated_content_html = ?,
                    approved_subject = ?,
                    approved_body_text = ?,
                    approved_body_html = ?,
                    outreach_generation_mode = ?,
                    outreach_template_subject = ?,
                    outreach_template_body_text = ?,
                    outreach_template_body_html = ?,
                    selected_material_ids = ?,
                    sent_at = CURRENT_TIMESTAMP,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (
                    "sent",
                    "manual",
                    primary_material_id,
                    88,
                    "已建立初步联系",
                    json.dumps(["研究主题重合"]),
                    json.dumps(["需要补充最新进展"]),
                    json.dumps(["nlp"]),
                    "历史草稿主题",
                    "历史草稿正文",
                    "<p>历史草稿正文</p>",
                    "已发送主题",
                    "已发送正文",
                    "<p>已发送正文</p>",
                    "template",
                    "跟进主题 {{name}}",
                    "跟进正文 {{name}}",
                    "<p>跟进正文 {{name}}</p>",
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
        self.assertEqual(before_task["id"], parent_task_id)
        self.assertEqual(before_task["source"], "manual")
        self.assertIsNone(before_task["parent_task_id"])
        self.assertIsNone(before_task["cancellation_reason"])
        self.assertFalse(before_task["can_continue_manually"])
        self.assertTrue(before_task["can_write_follow_up"])

        response = self.client.post(
            f"/api/email-tasks/{parent_task_id}/start-follow-up"
        )

        self.assertEqual(response.status_code, 200, msg=response.text)
        payload = response.json()
        current_task = payload["current_task"]
        self.assertNotEqual(current_task["id"], parent_task_id)
        self.assertIsNone(current_task["batch_task_id"])
        self.assertEqual(current_task["source"], "manual")
        self.assertEqual(current_task["parent_task_id"], parent_task_id)
        self.assertEqual(current_task["status"], "matched")
        self.assertIsNone(current_task["cancellation_reason"])
        self.assertEqual(current_task["primary_material_id"], primary_material_id)
        self.assertEqual(
            current_task["selected_material_ids"], [attachment_material_id]
        )
        self.assertEqual(current_task["match_score"], 88)
        self.assertEqual(current_task["match_reason"], "已建立初步联系")
        self.assertEqual(current_task["fit_points"], ["研究主题重合"])
        self.assertEqual(current_task["risk_points"], ["需要补充最新进展"])
        self.assertEqual(current_task["match_keywords"], ["nlp"])
        self.assertIsNone(current_task["generated_subject"])
        self.assertIsNone(current_task["generated_content_text"])
        self.assertIsNone(current_task["generated_content_html"])
        self.assertIsNone(current_task["approved_subject"])
        self.assertIsNone(current_task["approved_body_text"])
        self.assertIsNone(current_task["approved_body_html"])
        self.assertEqual(current_task["outreach_generation_mode"], "template")
        self.assertEqual(current_task["outreach_template_subject"], "跟进主题 {{name}}")
        self.assertEqual(
            current_task["outreach_template_body_text"], "跟进正文 {{name}}"
        )
        self.assertEqual(
            current_task["outreach_template_body_html"], "<p>跟进正文 {{name}}</p>"
        )
        self.assertFalse(current_task["can_continue_manually"])
        self.assertFalse(current_task["can_write_follow_up"])

        connection = sqlite3.connect(self.db_path)
        try:
            rows = connection.execute(
                """
                SELECT id, source, batch_task_id, parent_task_id, status, cancellation_reason,
                       generated_subject, generated_content_text, approved_subject, approved_body_text,
                       selected_material_ids
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
                    "manual",
                    None,
                    None,
                    "sent",
                    None,
                    "历史草稿主题",
                    "历史草稿正文",
                    "已发送主题",
                    "已发送正文",
                    json.dumps([attachment_material_id]),
                ),
                (
                    current_task["id"],
                    "manual",
                    None,
                    parent_task_id,
                    "matched",
                    None,
                    None,
                    None,
                    None,
                    None,
                    json.dumps([attachment_material_id]),
                ),
            ],
        )

    def test_start_follow_up_restores_matched_when_parent_has_no_match_result(
        self,
    ) -> None:
        identity_id = self._create_identity(with_imap=False)
        llm_id = self._create_llm()

        professor_response = self.client.post(
            "/api/professors",
            json={
                "name": "无匹配结果跟进导师",
                "email": "follow-up-minimum-status@example.edu",
                "title": "Professor",
                "university": "Example University",
                "school": "School of Computing",
                "department": "Computer Science",
                "research_direction": "Information extraction",
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
        parent_task_id = ensure_response.json()["current_task"]["id"]

        connection = sqlite3.connect(self.db_path)
        try:
            connection.execute(
                """
                UPDATE email_tasks
                SET status = ?,
                    source = ?,
                    match_score = NULL,
                    match_reason = NULL,
                    fit_points = NULL,
                    risk_points = NULL,
                    match_keywords = NULL,
                    generated_subject = NULL,
                    generated_content_text = NULL,
                    generated_content_html = NULL,
                    approved_subject = NULL,
                    approved_body_text = NULL,
                    approved_body_html = NULL,
                    sent_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                ("sent", "manual", parent_task_id),
            )
            connection.commit()
        finally:
            connection.close()

        response = self.client.post(
            f"/api/email-tasks/{parent_task_id}/start-follow-up"
        )

        self.assertEqual(response.status_code, 200, msg=response.text)
        current_task = response.json()["current_task"]
        self.assertEqual(current_task["status"], "matched")
        self.assertEqual(current_task["parent_task_id"], parent_task_id)
        self.assertIsNone(current_task["match_score"])
        self.assertIsNone(current_task["match_reason"])

    def test_start_follow_up_creates_manual_child_task_from_reply_detected_task(
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
        set_primary_response = self.client.post(
            f"/api/materials/{primary_material_id}/set-primary"
        )
        self.assertEqual(
            set_primary_response.status_code, 200, msg=set_primary_response.text
        )

        professor_response = self.client.post(
            "/api/professors",
            json={
                "name": "回复后跟进导师",
                "email": "follow-up-replied@example.edu",
                "title": "Professor",
                "university": "Example University",
                "school": "School of Computing",
                "department": "Computer Science",
                "research_direction": "Information extraction",
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
        parent_task_id = ensure_response.json()["current_task"]["id"]

        connection = sqlite3.connect(self.db_path)
        try:
            connection.execute(
                """
                UPDATE email_tasks
                SET status = ?,
                    source = ?,
                    primary_material_id = ?,
                    match_score = ?,
                    match_reason = ?,
                    fit_points = ?,
                    risk_points = ?,
                    match_keywords = ?,
                    generated_subject = ?,
                    generated_content_text = ?,
                    generated_content_html = ?,
                    approved_subject = ?,
                    approved_body_text = ?,
                    approved_body_html = ?,
                    is_replied = 1,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (
                    "reply_detected",
                    "manual",
                    primary_material_id,
                    86,
                    "对方已回复，适合继续跟进",
                    json.dumps(["已建立对话"]),
                    json.dumps(["需明确下一步诉求"]),
                    json.dumps(["reply"]),
                    "旧跟进草稿主题",
                    "旧跟进草稿正文",
                    "<p>旧跟进草稿正文</p>",
                    "旧审批主题",
                    "旧审批正文",
                    "<p>旧审批正文</p>",
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
        self.assertTrue(workspace_before.json()["current_task"]["can_write_follow_up"])

        response = self.client.post(
            f"/api/email-tasks/{parent_task_id}/start-follow-up"
        )

        self.assertEqual(response.status_code, 200, msg=response.text)
        current_task = response.json()["current_task"]
        self.assertEqual(current_task["parent_task_id"], parent_task_id)
        self.assertEqual(current_task["source"], "manual")
        self.assertEqual(current_task["status"], "matched")
        self.assertIsNone(current_task["generated_subject"])
        self.assertIsNone(current_task["generated_content_text"])
        self.assertIsNone(current_task["approved_subject"])
        self.assertIsNone(current_task["approved_body_text"])
        self.assertEqual(current_task["match_score"], 86)
        self.assertEqual(current_task["match_reason"], "对方已回复，适合继续跟进")

    def test_start_follow_up_rejects_duplicate_manual_child_creation(self) -> None:
        identity_id = self._create_identity(with_imap=False)
        llm_id = self._create_llm()

        professor_response = self.client.post(
            "/api/professors",
            json={
                "name": "跟进重复派生导师",
                "email": "follow-up-duplicate@example.edu",
                "title": "Professor",
                "university": "Example University",
                "school": "School of Computing",
                "department": "Computer Science",
                "research_direction": "Information extraction",
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
        parent_task_id = ensure_response.json()["current_task"]["id"]

        connection = sqlite3.connect(self.db_path)
        try:
            connection.execute(
                """
                UPDATE email_tasks
                SET status = ?, source = ?, sent_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                ("sent", "manual", parent_task_id),
            )
            connection.commit()
        finally:
            connection.close()

        first_response = self.client.post(
            f"/api/email-tasks/{parent_task_id}/start-follow-up"
        )
        second_response = self.client.post(
            f"/api/email-tasks/{parent_task_id}/start-follow-up"
        )

        self.assertEqual(first_response.status_code, 200, msg=first_response.text)
        self.assertEqual(second_response.status_code, 400, msg=second_response.text)

    def test_start_follow_up_returns_404_for_missing_task(self) -> None:
        response = self.client.post("/api/email-tasks/9999/start-follow-up")

        self.assertEqual(response.status_code, 404, msg=response.text)
        self.assertEqual(response.json()["detail"], "EmailTask 9999 不存在")

    def test_start_follow_up_rejects_task_without_sent_or_reply_detected_guard(
        self,
    ) -> None:
        identity_id = self._create_identity(with_imap=False)
        llm_id = self._create_llm()

        professor_response = self.client.post(
            "/api/professors",
            json={
                "name": "跟进非法状态导师",
                "email": "follow-up-guard@example.edu",
                "title": "Professor",
                "university": "Example University",
                "school": "School of Computing",
                "department": "Computer Science",
                "research_direction": "Information extraction",
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

        connection = sqlite3.connect(self.db_path)
        try:
            connection.execute(
                """
                UPDATE email_tasks
                SET status = ?
                WHERE id = ?
                """,
                ("matched", task_id),
            )
            connection.commit()
        finally:
            connection.close()

        response = self.client.post(f"/api/email-tasks/{task_id}/start-follow-up")

        self.assertEqual(response.status_code, 400, msg=response.text)

    def test_llm_mode_requires_complete_template_before_generating_draft(self) -> None:
        identity_id = self._create_identity(with_imap=False)
        llm_id = self._create_llm()
        self._upload_material(
            identity_id,
            filename="resume.txt",
            content=b"My research focuses on information extraction and agents.",
            material_type="resume",
        )
        self.client.post("/api/professors/import-sample")
        professor_id = self.client.get("/api/professors").json()[0]["id"]

        ensure_response = self.client.post(
            f"/api/workspaces/{professor_id}/ensure-task",
            params={"identity_id": identity_id, "llm_profile_id": llm_id},
        )
        self.assertEqual(ensure_response.status_code, 200, msg=ensure_response.text)
        task_id = ensure_response.json()["current_task"]["id"]
        connection = sqlite3.connect(self.db_path)
        try:
            connection.execute(
                """
                UPDATE identity_profiles
                SET outreach_template_subject = NULL,
                    outreach_template_body_text = NULL,
                    outreach_template_body_html = NULL
                WHERE id = ?
                """,
                (identity_id,),
            )
            connection.execute(
                """
                UPDATE email_tasks
                SET outreach_generation_mode = ?, outreach_template_subject = NULL,
                    outreach_template_body_text = NULL, outreach_template_body_html = NULL
                WHERE id = ?
                """,
                ("llm", task_id),
            )
            connection.commit()
        finally:
            connection.close()

        with patch(
            "app.modules.workspace.tasks.runtime.llm_runtime.generate_draft_content",
            AsyncMock(
                return_value=self._build_draft_generation_result(
                    subject="测试草稿",
                    body_text="测试正文",
                    body_html="<p>测试正文</p>",
                ),
            ),
        ) as mocked_generate:
            generate_response = self.client.post(
                f"/api/email-tasks/{task_id}/generate-draft"
            )

        self.assertEqual(generate_response.status_code, 400)
        self.assertEqual(
            generate_response.json()["detail"], "请先填写默认套磁信主题和纯文本正文"
        )
        mocked_generate.assert_not_awaited()

    def test_test_compose_generate_draft_returns_bad_gateway_when_llm_fails(
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
        set_primary_response = self.client.post(
            f"/api/materials/{material_id}/set-primary"
        )
        self.assertEqual(
            set_primary_response.status_code, 200, msg=set_primary_response.text
        )

        from app.modules.llm import runtime as llm_runtime

        with patch(
            "app.modules.communications.test_compose.runtime.llm_runtime.generate_draft_content",
            AsyncMock(
                side_effect=llm_runtime.LLMRuntimeError(
                    "模型返回的正文无效",
                    endpoint_kind="chat_completions",
                    status_code=500,
                ),
            ),
        ):
            response = self.client.post(
                f"/api/test-compose/{identity_id}/{llm_id}/generate-draft"
            )

        self.assertEqual(response.status_code, 502, msg=response.text)
        self.assertEqual(response.json()["detail"], "模型返回的正文无效")

    def test_test_compose_draft_and_history_are_identity_scoped_not_llm_scoped(
        self,
    ) -> None:
        identity_id = self._create_identity(with_imap=False)
        first_llm_id = self._create_llm()
        second_llm_response = self.client.post(
            "/api/llm-profiles",
            json={
                "name": "测试写信备用模型",
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

        save_response = self.client.post(
            f"/api/test-compose/{identity_id}/{first_llm_id}/draft",
            json={
                "subject": "模型 A 保存的测试主题",
                "body_text": "模型 A 保存的测试正文",
                "body_html": "<p>模型 A 保存的测试正文</p>",
                "selected_material_ids": [],
            },
        )
        self.assertEqual(save_response.status_code, 200, msg=save_response.text)

        connection = sqlite3.connect(self.db_path)
        try:
            session_id = connection.execute(
                """
                SELECT id
                FROM test_compose_sessions
                WHERE identity_id = ? AND llm_profile_id = ?
                """,
                (identity_id, first_llm_id),
            ).fetchone()[0]
            connection.execute(
                """
                INSERT INTO test_compose_messages (
                    session_id, identity_id, llm_profile_id, recipient_email,
                    subject, content, content_html, status, rfc_message_id
                )
                VALUES (?, ?, ?, 'sender@example.com', '测试主题', '测试正文',
                        '<p>测试正文</p>', 'sent', '<identity-compose-switch@example.com>')
                """,
                (session_id, identity_id, first_llm_id),
            )
            connection.commit()
        finally:
            connection.close()

        switched_thread = self.client.get(
            f"/api/test-compose/{identity_id}/{second_llm_id}"
        )

        self.assertEqual(switched_thread.status_code, 200, msg=switched_thread.text)
        payload = switched_thread.json()
        self.assertEqual(payload["draft"]["subject"], "模型 A 保存的测试主题")
        self.assertEqual(payload["draft"]["body_text"], "模型 A 保存的测试正文")
        self.assertEqual(
            payload["history"][0]["rfc_message_id"],
            "<identity-compose-switch@example.com>",
        )

    def test_test_compose_template_generation_preserves_placeholders_in_draft(
        self,
    ) -> None:
        response = self.client.post(
            "/api/identities",
            json=self._build_identity_payload(
                with_imap=False,
                outreach_generation_mode="template",
                outreach_template_subject="测试给{{name}}",
                outreach_template_body_text="{{name}}您好，我是{{sender_name}}。",
                outreach_template_body_html="<p>{{name}}您好，我是{{sender_name}}。</p>",
            ),
        )
        self.assertEqual(response.status_code, 201, msg=response.text)
        identity_id = response.json()["id"]
        llm_id = self._create_llm()

        draft_response = self.client.post(
            f"/api/test-compose/{identity_id}/{llm_id}/generate-draft"
        )

        self.assertEqual(draft_response.status_code, 200, msg=draft_response.text)
        draft = draft_response.json()["draft"]
        self.assertEqual(draft["subject"], "测试给{{name}}")
        self.assertIn("{{name}}您好", draft["body_text"])
        self.assertIn("{{sender_name}}", draft["body_text"])
        self.assertIn("{{name}}您好", draft["body_html"])
        self.assertIn("{{sender_name}}", draft["body_html"])

    def test_workspace_generate_draft_uses_latest_identity_template_defaults(
        self,
    ) -> None:
        identity_id = self._create_identity(with_imap=False)
        llm_id = self._create_llm()

        update_response = self.client.put(
            f"/api/identities/{identity_id}",
            json=self._build_identity_payload(
                with_imap=False,
                outreach_generation_mode="llm",
                outreach_template_subject="申请与{{name}}老师交流",
                outreach_template_body_text="{{name}}老师您好，我是{{sender_name}}。",
                outreach_template_body_html="<p>{{name}}老师您好，我是{{sender_name}}。</p>",
            ),
        )
        self.assertEqual(update_response.status_code, 200, msg=update_response.text)

        professor_response = self.client.post(
            "/api/professors",
            json={
                "name": "工作区切换导师",
                "email": "workspace-mode@example.edu",
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

        ensure_response = self.client.post(
            f"/api/workspaces/{professor_id}/ensure-task",
            params={"identity_id": identity_id, "llm_profile_id": llm_id},
        )
        self.assertEqual(ensure_response.status_code, 200, msg=ensure_response.text)
        task_id = ensure_response.json()["current_task"]["id"]

        clear_identity_response = self.client.put(
            f"/api/identities/{identity_id}",
            json=self._build_identity_payload(
                with_imap=False,
                outreach_generation_mode="template",
                outreach_template_subject="后来切换成新的默认主题",
                outreach_template_body_text="后来切换成新的默认正文 {{name}}",
                outreach_template_body_html="<p>后来切换成新的默认正文 {{name}}</p>",
            ),
        )
        self.assertEqual(
            clear_identity_response.status_code, 200, msg=clear_identity_response.text
        )

        switch_response = self.client.post(
            f"/api/email-tasks/{task_id}/outreach-config",
            json={"outreach_generation_mode": "template"},
        )
        self.assertEqual(switch_response.status_code, 200, msg=switch_response.text)
        switched_task = switch_response.json()["current_task"]
        self.assertEqual(switched_task["outreach_generation_mode"], "template")
        self.assertEqual(
            switched_task["outreach_template_subject"], "后来切换成新的默认主题"
        )
        self.assertEqual(
            switched_task["outreach_template_body_text"],
            "后来切换成新的默认正文 {{name}}",
        )

        generate_response = self.client.post(
            f"/api/email-tasks/{task_id}/generate-draft"
        )
        self.assertEqual(generate_response.status_code, 200, msg=generate_response.text)
        generated = generate_response.json()
        self.assertEqual(generated["current_task"]["status"], "review_required")
        self.assertEqual(
            generated["current_task"]["generated_subject"], "后来切换成新的默认主题"
        )
        self.assertIn(
            "后来切换成新的默认正文 工作区切换导师",
            generated["current_task"]["generated_content_text"],
        )
