from __future__ import annotations

import asyncio
import io
import json
import os
import sqlite3
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from test.migrated_database import create_migrated_sqlite_database


UI_TOKEN = "ui-token-for-tests"
AGENT_TOKEN = "agent-token-for-tests"


class AgentApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        os.environ["AUTO_EMAIL_SENDER_UI_TOKEN"] = UI_TOKEN
        os.environ["AUTO_EMAIL_SENDER_AGENT_TOKEN"] = AGENT_TOKEN
        os.environ["ENABLE_BACKGROUND_WORKERS"] = "0"

        from main import create_app

        cls.client = TestClient(create_app(), raise_server_exceptions=False)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.client.close()
        os.environ.pop("AUTO_EMAIL_SENDER_UI_TOKEN", None)
        os.environ.pop("AUTO_EMAIL_SENDER_AGENT_TOKEN", None)
        os.environ.pop("ENABLE_BACKGROUND_WORKERS", None)

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "agent_api.db"
        os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{self.db_path.as_posix()}"
        os.environ["AUTO_EMAIL_SENDER_DATA_DIR"] = self.temp_dir.name
        create_migrated_sqlite_database(self.db_path)

        from app.core.config import get_settings
        from app.core.database import dispose_engine, get_engine, get_session_factory

        get_settings.cache_clear()
        if get_engine.cache_info().currsize:
            asyncio.run(dispose_engine())
        get_session_factory.cache_clear()
        get_settings.cache_clear()

    def tearDown(self) -> None:
        from app.core.config import get_settings
        from app.core.database import dispose_engine, get_engine, get_session_factory

        if get_engine.cache_info().currsize:
            asyncio.run(dispose_engine())
        get_session_factory.cache_clear()
        get_settings.cache_clear()
        os.environ.pop("DATABASE_URL", None)
        os.environ.pop("AUTO_EMAIL_SENDER_DATA_DIR", None)
        self.temp_dir.cleanup()

    def test_public_endpoints_do_not_require_a_token(self) -> None:
        self.assertEqual(self.client.get("/health").status_code, 200)
        self.assertNotEqual(self.client.get("/ready").status_code, 401)
        self.assertEqual(self.client.get("/startup-status").status_code, 200)

    def test_missing_and_invalid_tokens_are_rejected(self) -> None:
        missing = self.client.get("/api/agent/v1/info")
        invalid = self.client.get(
            "/api/agent/v1/info",
            headers={"Authorization": "Bearer wrong"},
        )

        self.assertEqual(missing.status_code, 401)
        self.assertEqual(missing.json()["error"]["code"], "AUTH_REQUIRED")
        self.assertEqual(invalid.status_code, 401)
        self.assertEqual(invalid.json()["error"]["code"], "INVALID_ACCESS_TOKEN")

    def test_tokens_cannot_cross_api_scopes(self) -> None:
        ui_to_agent = self.client.get(
            "/api/agent/v1/info",
            headers=self._ui_headers(),
        )
        agent_to_ui = self.client.get(
            "/api/ping",
            headers=self._agent_headers(),
        )

        self.assertEqual(ui_to_agent.status_code, 403)
        self.assertEqual(agent_to_ui.status_code, 403)
        self.assertEqual(
            ui_to_agent.json()["error"]["code"],
            "TOKEN_SCOPE_FORBIDDEN",
        )

    def test_ui_and_agent_tokens_work_in_their_own_scopes(self) -> None:
        self.assertEqual(
            self.client.get("/api/ping", headers=self._ui_headers()).status_code,
            200,
        )
        info = self.client.get("/api/agent/v1/info", headers=self._agent_headers())
        self.assertEqual(info.status_code, 200, msg=info.text)
        self.assertEqual(info.json()["authentication_scope"], "agent")

    def test_options_and_allowed_local_cors_origin_work_without_a_token(self) -> None:
        response = self.client.options(
            "/api/agent/v1/info",
            headers={
                "Origin": "http://127.0.0.1:5173",
                "Access-Control-Request-Method": "GET",
                "Access-Control-Request-Headers": "authorization",
            },
        )

        self.assertEqual(response.status_code, 200, msg=response.text)
        self.assertEqual(
            response.headers["access-control-allow-origin"],
            "http://127.0.0.1:5173",
        )

    def test_untrusted_cors_origin_is_not_allowed(self) -> None:
        response = self.client.options(
            "/api/agent/v1/info",
            headers={
                "Origin": "https://attacker.example",
                "Access-Control-Request-Method": "GET",
            },
        )

        self.assertEqual(response.status_code, 400)
        self.assertNotIn("access-control-allow-origin", response.headers)

    def test_development_compatibility_mode_is_open_when_both_tokens_are_absent(self) -> None:
        from main import create_app

        old_ui = os.environ.pop("AUTO_EMAIL_SENDER_UI_TOKEN", None)
        old_agent = os.environ.pop("AUTO_EMAIL_SENDER_AGENT_TOKEN", None)
        try:
            with TestClient(create_app(), raise_server_exceptions=False) as client:
                self.assertEqual(client.get("/api/ping").status_code, 200)
                self.assertEqual(client.get("/api/agent/v1/info").status_code, 200)
        finally:
            if old_ui is not None:
                os.environ["AUTO_EMAIL_SENDER_UI_TOKEN"] = old_ui
            if old_agent is not None:
                os.environ["AUTO_EMAIL_SENDER_AGENT_TOKEN"] = old_agent

    def test_agent_views_return_full_mail_bodies_without_secrets_or_internal_fields(self) -> None:
        identity_id = self._create_identity()
        llm_profile_id = self._create_llm_profile()
        professor_id = self._create_professor()
        material_id = self._upload_material(identity_id)
        template_id = self._create_template()
        task_id, received_message_id = self._insert_thread(
            identity_id=identity_id,
            llm_profile_id=llm_profile_id,
            professor_id=professor_id,
            material_id=material_id,
            template_id=template_id,
        )

        identities = self._agent_get("/api/agent/v1/identities").json()
        llm_profiles = self._agent_get("/api/agent/v1/llm-profiles").json()
        materials = self._agent_get("/api/agent/v1/materials").json()
        messages_without_body = self._agent_get(
            "/api/agent/v1/communications/messages",
            params={"direction": "received"},
        ).json()
        messages_with_body = self._agent_get(
            "/api/agent/v1/communications/messages",
            params={"direction": "received", "include_body": True},
        ).json()
        message_detail = self._agent_get(
            f"/api/agent/v1/communications/messages/{received_message_id}",
        ).json()
        threads = self._agent_get(
            "/api/agent/v1/communications/threads",
            params={"sent": True, "replied": True},
        ).json()
        draft = self._agent_get(f"/api/agent/v1/drafts/{task_id}").json()

        serialized = json.dumps(
            {
                "identities": identities,
                "llm_profiles": llm_profiles,
                "materials": materials,
                "messages": messages_with_body,
            },
            ensure_ascii=False,
        )
        for secret in ("smtp-secret-value", "imap-secret-value", "llm-secret-value"):
            self.assertNotIn(secret, serialized)
        for internal_field in (
            "file_path",
            "provider_payload",
            "message_fingerprint",
            "normalized_message_id",
            "imap_uid",
            "uidvalidity",
        ):
            self.assertNotIn(internal_field, serialized)

        self.assertIsNone(messages_without_body["items"][0]["content"])
        received = messages_with_body["items"][0]
        self.assertEqual(received["content"], "今年实验室没有招生名额。")
        self.assertEqual(received["trust_level"], "untrusted_external_content")
        self.assertEqual(message_detail["content"], "今年实验室没有招生名额。")
        self.assertEqual(len(threads["items"]), 1)
        self.assertTrue(threads["items"][0]["has_sent"])
        self.assertTrue(threads["items"][0]["has_reply"])
        self.assertEqual(draft["reference_material_id"], material_id)
        self.assertEqual(draft["attachment_material_ids"], [material_id])

    def test_agent_lists_paginate_and_professor_detail_includes_tags(self) -> None:
        first_id = self._create_professor(email="first@example.edu")
        self._create_professor(email="second@example.edu")
        page = self._agent_get(
            "/api/agent/v1/professors",
            params={"limit": 1},
        ).json()
        second_page = self._agent_get(
            "/api/agent/v1/professors",
            params={"limit": 1, "cursor": page["next_cursor"]},
        ).json()
        detail = self._agent_get(f"/api/agent/v1/professors/{first_id}").json()

        self.assertEqual(len(page["items"]), 1)
        self.assertTrue(page["has_more"])
        self.assertEqual(len(second_page["items"]), 1)
        self.assertIn("tags", detail)

    def test_template_draft_is_draft_only_and_keeps_reference_separate_from_attachments(self) -> None:
        identity_id = self._create_identity()
        llm_profile_id = self._create_llm_profile()
        professor_id = self._create_professor()
        material_id = self._upload_material(identity_id)
        template_id = self._create_template()

        response = self.client.post(
            "/api/agent/v1/drafts",
            headers=self._agent_headers(),
            json={
                "professor_id": professor_id,
                "identity_id": identity_id,
                "llm_profile_id": llm_profile_id,
                "generation_mode": "template",
                "template_id": template_id,
                "reference_material_id": None,
                "attachment_material_ids": [material_id],
            },
        )

        self.assertEqual(response.status_code, 201, msg=response.text)
        draft = response.json()
        self.assertEqual(draft["status"], "review_required")
        self.assertEqual(draft["generation_mode"], "template")
        self.assertIsNone(draft["reference_material_id"])
        self.assertEqual(draft["attachment_material_ids"], [material_id])
        self.assertIn("再次联系", draft["generated_subject"])
        with sqlite3.connect(self.db_path) as connection:
            directions = [
                row[0]
                for row in connection.execute(
                    "SELECT direction FROM email_logs WHERE email_task_id = ?",
                    (draft["task_id"],),
                )
            ]
        self.assertEqual(directions, ["draft"])

    def test_send_plan_requires_confirmation_detects_stale_content_and_expires(self) -> None:
        draft = self._create_template_draft()
        task_id = draft["task_id"]
        create_plan = self.client.post(
            f"/api/agent/v1/drafts/{task_id}/prepare-send",
            headers={**self._agent_headers(), "Idempotency-Key": "plan-stale-test"},
            json={"delivery": "immediate"},
        )
        self.assertEqual(create_plan.status_code, 201, msg=create_plan.text)
        plan = create_plan.json()
        self.assertEqual(plan["status"], "awaiting_confirmation")
        self.assertIn("尚未发送", plan["confirmation_message"])

        missing_confirmation = self.client.post(
            f"/api/agent/v1/plans/{plan['plan_id']}/execute",
            headers=self._agent_headers(),
            json={"confirm": False},
        )
        self.assertEqual(missing_confirmation.status_code, 409)
        self.assertEqual(
            missing_confirmation.json()["error"]["code"],
            "PLAN_CONFIRMATION_REQUIRED",
        )

        changed = self.client.put(
            f"/api/agent/v1/drafts/{task_id}",
            headers=self._agent_headers(),
            json={
                "subject": "修改后的主题",
                "body_text": "修改后的正文",
                "attachment_material_ids": [],
            },
        )
        self.assertEqual(changed.status_code, 200, msg=changed.text)
        stale = self.client.post(
            f"/api/agent/v1/plans/{plan['plan_id']}/execute",
            headers=self._agent_headers(),
            json={"confirm": True},
        )
        self.assertEqual(stale.status_code, 409, msg=stale.text)
        self.assertEqual(stale.json()["error"]["code"], "PLAN_STALE")

        fresh_plan = self.client.post(
            f"/api/agent/v1/drafts/{task_id}/prepare-send",
            headers=self._agent_headers(),
            json={"delivery": "immediate"},
        ).json()
        with sqlite3.connect(self.db_path) as connection:
            connection.execute(
                "UPDATE agent_action_plans SET expires_at = ? WHERE id = ?",
                (
                    (datetime.now(UTC) - timedelta(minutes=1)).isoformat(),
                    fresh_plan["plan_id"],
                ),
            )
            connection.commit()
        expired = self.client.post(
            f"/api/agent/v1/plans/{fresh_plan['plan_id']}/execute",
            headers=self._agent_headers(),
            json={"confirm": True},
        )
        self.assertEqual(expired.status_code, 409, msg=expired.text)
        self.assertEqual(expired.json()["error"]["code"], "PLAN_EXPIRED")

    def test_send_plan_warns_only_above_the_attachment_recommendation(self) -> None:
        draft = self._create_template_draft()
        task_id = draft["task_id"]
        material_id = draft["attachment_material_ids"][0]

        with sqlite3.connect(self.db_path) as connection:
            connection.execute(
                "UPDATE identity_materials SET size_bytes = ? WHERE id = ?",
                (1024 * 1024, material_id),
            )
            connection.commit()
        at_limit = self.client.post(
            f"/api/agent/v1/drafts/{task_id}/prepare-send",
            headers=self._agent_headers(),
            json={"delivery": "immediate"},
        )
        self.assertEqual(at_limit.status_code, 201, msg=at_limit.text)
        self.assertEqual(at_limit.json()["warnings"], [])
        self.assertEqual(
            at_limit.json()["summary"]["attachment_total_size_bytes"],
            1024 * 1024,
        )

        with sqlite3.connect(self.db_path) as connection:
            connection.execute(
                "UPDATE identity_materials SET size_bytes = ? WHERE id = ?",
                (1024 * 1024 + 1, material_id),
            )
            connection.commit()
        over_limit = self.client.post(
            f"/api/agent/v1/drafts/{task_id}/prepare-send",
            headers=self._agent_headers(),
            json={"delivery": "immediate"},
        )
        self.assertEqual(over_limit.status_code, 201, msg=over_limit.text)
        plan = over_limit.json()
        self.assertEqual(
            plan["summary"]["attachments"][0]["size_bytes"],
            1024 * 1024 + 1,
        )
        self.assertEqual(
            plan["summary"]["attachment_total_size_bytes"],
            1024 * 1024 + 1,
        )
        self.assertEqual(len(plan["warnings"]), 1)
        self.assertIn("建议不超过 1 MB", plan["warnings"][0])
        self.assertIn("减少被邮箱提供商限流的概率", plan["warnings"][0])
        self.assertIn(plan["warnings"][0], plan["confirmation_message"])
        self.assertNotIn("云盘", plan["confirmation_message"])

    def test_confirmed_send_plan_executes_once_and_replays_original_result(self) -> None:
        draft = self._create_template_draft()
        task_id = draft["task_id"]
        plan_response = self.client.post(
            f"/api/agent/v1/drafts/{task_id}/prepare-send",
            headers={**self._agent_headers(), "Idempotency-Key": "execute-once-test"},
            json={"delivery": "immediate"},
        )
        self.assertEqual(plan_response.status_code, 201, msg=plan_response.text)
        plan_id = plan_response.json()["plan_id"]
        send_mock = AsyncMock(
            return_value=SimpleNamespace(
                message_id="<agent-plan@example.com>",
                provider_payload={"accepted": True},
            ),
        )
        with patch("app.services.mail_runtime.send_email", send_mock):
            first = self.client.post(
                f"/api/agent/v1/plans/{plan_id}/execute",
                headers=self._agent_headers(),
                json={"confirm": True},
            )
            second = self.client.post(
                f"/api/agent/v1/plans/{plan_id}/execute",
                headers=self._agent_headers(),
                json={"confirm": True},
            )

        self.assertEqual(first.status_code, 200, msg=first.text)
        self.assertEqual(first.json()["status"], "executed")
        self.assertEqual(first.json()["result"]["outcome"], "sent")
        self.assertEqual(second.status_code, 200, msg=second.text)
        self.assertTrue(second.json()["idempotent_replay"])
        self.assertEqual(second.json()["result"], first.json()["result"])
        self.assertEqual(send_mock.await_count, 1)

    def _create_template_draft(self) -> dict[str, object]:
        identity_id = self._create_identity()
        llm_profile_id = self._create_llm_profile()
        professor_id = self._create_professor()
        material_id = self._upload_material(identity_id)
        template_id = self._create_template()
        response = self.client.post(
            "/api/agent/v1/drafts",
            headers=self._agent_headers(),
            json={
                "professor_id": professor_id,
                "identity_id": identity_id,
                "llm_profile_id": llm_profile_id,
                "generation_mode": "template",
                "template_id": template_id,
                "attachment_material_ids": [material_id],
            },
        )
        self.assertEqual(response.status_code, 201, msg=response.text)
        return response.json()

    def _create_identity(self) -> int:
        response = self.client.post(
            "/api/identities",
            headers=self._ui_headers(),
            json={
                "name": "Agent 测试身份",
                "profile_name": "Agent 测试身份",
                "sender_name": "测试同学",
                "email_address": "sender@example.com",
                "smtp_host": "smtp.example.com",
                "smtp_port": 465,
                "smtp_username": "sender@example.com",
                "smtp_password": "smtp-secret-value",
                "imap_host": "imap.example.com",
                "imap_port": 993,
                "imap_username": "sender@example.com",
                "imap_password": "imap-secret-value",
                "default_language": "zh-CN",
                "is_default": True,
            },
        )
        self.assertEqual(response.status_code, 201, msg=response.text)
        return response.json()["id"]

    def _create_llm_profile(self) -> int:
        response = self.client.post(
            "/api/llm-profiles",
            headers=self._ui_headers(),
            json={
                "name": "Agent 测试模型",
                "provider": "openai",
                "api_base_url": "https://api.example.com/v1",
                "api_key": "llm-secret-value",
                "model_name": "test-model",
                "is_default": True,
            },
        )
        self.assertEqual(response.status_code, 201, msg=response.text)
        return response.json()["id"]

    def _create_professor(self, *, email: str = "professor@example.edu") -> int:
        response = self.client.post(
            "/api/professors",
            headers=self._ui_headers(),
            json={
                "name": f"导师 {email}",
                "email": email,
                "university": "示例大学",
                "research_direction": "智能体",
            },
        )
        self.assertEqual(response.status_code, 201, msg=response.text)
        return response.json()["id"]

    def _upload_material(self, identity_id: int) -> int:
        response = self.client.post(
            f"/api/identities/{identity_id}/materials",
            headers=self._ui_headers(),
            files={"file": ("plan.txt", io.BytesIO(b"research plan"), "text/plain")},
            data={"material_type": "other"},
        )
        self.assertEqual(response.status_code, 201, msg=response.text)
        return response.json()["id"]

    def _create_template(self) -> int:
        response = self.client.post(
            "/api/outreach-templates",
            headers=self._ui_headers(),
            json={
                "name": "二次联系",
                "recommended_generation_mode": "llm",
                "subject": "再次联系 {{name}}",
                "body_text": "老师您好",
                "body_html": "<p>老师您好</p>",
                "is_default": True,
            },
        )
        self.assertEqual(response.status_code, 201, msg=response.text)
        return response.json()["id"]

    def _insert_thread(
        self,
        *,
        identity_id: int,
        llm_profile_id: int,
        professor_id: int,
        material_id: int,
        template_id: int,
    ) -> tuple[int, int]:
        with sqlite3.connect(self.db_path) as connection:
            task_id = connection.execute(
                """
                INSERT INTO email_tasks (
                    source, identity_id, llm_profile_id, professor_id, status,
                    primary_material_id, selected_material_ids,
                    outreach_generation_mode, outreach_template_id,
                    generated_subject, generated_content_text, generated_content_html
                )
                VALUES ('manual', ?, ?, ?, 'review_required', ?, ?, 'llm', ?,
                        '再次联系', '老师您好', '<p>老师您好</p>')
                RETURNING id
                """,
                (
                    identity_id,
                    llm_profile_id,
                    professor_id,
                    material_id,
                    json.dumps([material_id]),
                    template_id,
                ),
            ).fetchone()[0]
            connection.execute(
                """
                INSERT INTO email_logs (
                    email_task_id, identity_id, llm_profile_id, professor_id,
                    direction, subject, content, rfc_message_id,
                    normalized_message_id, message_fingerprint, provider_payload
                )
                VALUES (?, ?, ?, ?, 'sent', '申请', '申请正文', '<sent@example.com>',
                        '<sent@example.com>', 'secret-fingerprint-1', '{"secret": true}')
                """,
                (task_id, identity_id, llm_profile_id, professor_id),
            )
            received_id = connection.execute(
                """
                INSERT INTO email_logs (
                    email_task_id, identity_id, llm_profile_id, professor_id,
                    direction, subject, content, content_html, from_email,
                    rfc_message_id, normalized_message_id, message_fingerprint,
                    folder_role, folder, uidvalidity, imap_uid, provider_payload
                )
                VALUES (?, ?, ?, ?, 'received', 'Re: 申请', '今年实验室没有招生名额。',
                        '<p>今年实验室没有招生名额。</p>', 'professor@example.edu',
                        '<received@example.com>', '<received@example.com>',
                        'secret-fingerprint-2', 'inbox', 'INBOX', 1, 7, '{"secret": true}')
                RETURNING id
                """,
                (task_id, identity_id, llm_profile_id, professor_id),
            ).fetchone()[0]
            connection.commit()
        return task_id, received_id

    def _agent_get(self, path: str, *, params: dict[str, object] | None = None):
        response = self.client.get(
            path,
            headers=self._agent_headers(),
            params=params,
        )
        self.assertEqual(response.status_code, 200, msg=response.text)
        return response

    @staticmethod
    def _ui_headers() -> dict[str, str]:
        return {"Authorization": f"Bearer {UI_TOKEN}"}

    @staticmethod
    def _agent_headers() -> dict[str, str]:
        return {"Authorization": f"Bearer {AGENT_TOKEN}"}


if __name__ == "__main__":
    unittest.main()
