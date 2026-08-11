from __future__ import annotations

import asyncio
from contextlib import closing
import io
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from app.modules.llm.runtime import LLMRuntimeAdaptation


class OperationLogIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.temp_dir = tempfile.TemporaryDirectory()
        cls.db_path = Path(cls.temp_dir.name) / "operation_log_integration.db"
        os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{cls.db_path.as_posix()}"
        os.environ["ENABLE_BACKGROUND_WORKERS"] = "0"
        cls.getaddrinfo_patcher = patch(
            "app.modules.crawler.pages.tools.socket.getaddrinfo",
            return_value=[(0, 0, 0, "", ("93.184.216.34", 443))],
        )
        cls.getaddrinfo_patcher.start()

        from app.core.config import get_settings
        from app.core.database import dispose_engine, get_engine, get_session_factory
        from main import create_app

        get_settings.cache_clear()
        if get_engine.cache_info().currsize:
            asyncio.run(dispose_engine())
        get_session_factory.cache_clear()
        get_settings.cache_clear()

        asyncio.run(cls._create_schema())
        cls.client = TestClient(create_app())

    @classmethod
    def tearDownClass(cls) -> None:
        cls.client.close()

        from app.core.config import get_settings
        from app.core.database import dispose_engine, get_engine, get_session_factory

        if get_engine.cache_info().currsize:
            asyncio.run(dispose_engine())
        get_session_factory.cache_clear()
        get_settings.cache_clear()
        os.environ.pop("DATABASE_URL", None)
        os.environ.pop("ENABLE_BACKGROUND_WORKERS", None)
        cls.getaddrinfo_patcher.stop()
        cls.temp_dir.cleanup()

    def setUp(self) -> None:
        asyncio.run(self._clear_database())

    def test_create_crawl_job_records_operation_log_with_response_request_id(self) -> None:
        response = self.client.post(
            "/api/crawl-jobs",
            headers={"X-Request-ID": "oplog.crawl-1"},
            json={
                "university": "示例大学",
                "school": "计算机学院",
                "start_url": "https://example.edu/faculty",
                "llm_profile_id": None,
            },
        )

        self.assertEqual(response.status_code, 201, msg=response.text)
        request_id = response.headers["X-Request-ID"]
        logs = self._list_logs("crawl_job.created", request_id=request_id)

        self.assertEqual(len(logs), 1)
        log = logs[0]
        self.assertEqual(log["category"], "crawler")
        self.assertEqual(log["entity_type"], "crawl_job")
        self.assertEqual(log["entity_id"], str(response.json()["id"]))
        self.assertEqual(log["metadata"]["university"], "示例大学")
        self.assertEqual(log["metadata"]["school"], "计算机学院")
        self.assertEqual(log["metadata"]["start_url"], "https://example.edu/faculty")
        self.assertIsNone(log["metadata"]["llm_profile_id"])

    def test_create_batch_task_records_target_count_metadata(self) -> None:
        identity_id = self._create_identity("batch-sender@example.com")
        llm_profile_id = self._create_llm_profile("批量任务模型")
        first_professor_id = self._create_professor("一号导师", "batch-one@example.edu")
        second_professor_id = self._create_professor("二号导师", "batch-two@example.edu")

        response = self.client.post(
            "/api/batch-tasks",
            json={
                "identity_id": identity_id,
                "llm_profile_id": llm_profile_id,
                "name": "测试批量任务",
                "professor_ids": [first_professor_id, second_professor_id],
                "schedule_type": "immediate",
                "window_start_time": None,
                "window_end_time": None,
                "emails_per_window": None,
                "primary_material_id": None,
                "email_subject": "联系 {{name}}",
                "email_body": "正文 {{name}}",
                "selected_material_ids": None,
            },
        )

        self.assertEqual(response.status_code, 201, msg=response.text)
        logs = self._list_logs("batch_task.created", entity_id=str(response.json()["id"]))

        self.assertEqual(len(logs), 1)
        self.assertEqual(logs[0]["category"], "email")
        self.assertEqual(logs[0]["entity_type"], "batch_task")
        self.assertEqual(logs[0]["metadata"]["target_count"], 2)
        self.assertEqual(logs[0]["metadata"]["identity_id"], identity_id)
        self.assertEqual(logs[0]["metadata"]["llm_profile_id"], llm_profile_id)
        self.assertEqual(logs[0]["metadata"]["schedule_type"], "immediate")

    def test_professor_create_and_archive_record_operation_logs(self) -> None:
        create_response = self.client.post(
            "/api/professors",
            json=self._professor_payload("归档导师", "archive-me@example.edu"),
        )
        self.assertEqual(create_response.status_code, 201, msg=create_response.text)
        professor_id = create_response.json()["id"]

        archive_response = self.client.post(f"/api/professors/{professor_id}/archive")

        self.assertEqual(archive_response.status_code, 200, msg=archive_response.text)
        create_logs = self._list_logs("professor.created", entity_id=str(professor_id))
        archive_logs = self._list_logs("professor.archived", entity_id=str(professor_id))
        self.assertEqual(len(create_logs), 1)
        self.assertEqual(len(archive_logs), 1)
        self.assertEqual(create_logs[0]["category"], "user_action")
        self.assertEqual(archive_logs[0]["metadata"]["affected_count"], 1)

    def test_smtp_test_records_result_without_sensitive_fields(self) -> None:
        identity_id = self._create_identity("smtp-log@example.com")

        with patch(
            "app.modules.identities.profiles.api.test_smtp_connection",
            AsyncMock(return_value=(True, "SMTP 连接测试成功")),
        ):
            response = self.client.post(f"/api/identities/{identity_id}/smtp-test")

        self.assertEqual(response.status_code, 200, msg=response.text)
        logs = self._list_logs("identity.smtp_tested", entity_id=str(identity_id))

        self.assertEqual(len(logs), 1)
        metadata = logs[0]["metadata"]
        self.assertTrue(metadata["ok"])
        self.assertEqual(metadata["result"], "ok")
        self.assertEqual(metadata["host"], "smtp.example.com")
        self.assertNotIn("smtp_password", metadata)
        self.assertNotIn("smtp-secret", str(metadata))

    def test_llm_profile_test_records_failed_result_without_sensitive_fields(self) -> None:
        llm_profile_id = self._create_llm_profile(
            "测试模型",
            api_key="sk-sensitive-test-key",
            api_base_url="https://api.example.com/v1?api_key=secret#x",
        )

        with patch(
            "app.modules.llm.api.probe_llm_profile",
            AsyncMock(return_value=self._build_probe_result()),
        ), patch(
            "app.modules.llm.api.ensure_llm_runtime_adaptation",
            AsyncMock(return_value=LLMRuntimeAdaptation("chat_completions", None)),
        ):
            response = self.client.post(f"/api/llm-profiles/{llm_profile_id}/test")

        self.assertEqual(response.status_code, 200, msg=response.text)
        logs = self._list_logs("llm_profile.tested", entity_id=str(llm_profile_id))

        self.assertEqual(len(logs), 1)
        metadata = logs[0]["metadata"]
        self.assertFalse(metadata["ok"])
        self.assertEqual(metadata["status_code"], 401)
        self.assertEqual(metadata["duration_ms"], 12)
        self.assertEqual(metadata["provider"], "openai")
        self.assertNotIn("api_key", metadata)
        self.assertNotIn("sk-sensitive-test-key", str(metadata))
        self.assertNotIn("secret", str(metadata))
        self.assertNotIn("?api_key=", str(metadata))
        self.assertNotIn("#x", str(metadata))

    def test_llm_profile_test_records_sanitized_endpoint_protocol_switch(self) -> None:
        llm_profile_id = self._create_llm_profile(
            "端点切换模型",
            api_key="sk-endpoint-switch-secret",
            api_base_url="https://llm-user:llm-password@switch.example.com/v1",
        )
        with closing(sqlite3.connect(self.db_path)) as connection, connection:
            connection.execute(
                """
                INSERT INTO llm_endpoint_adaptation_cache
                    (api_base_url, model_name, learned_endpoint_kind)
                VALUES (?, ?, ?)
                """,
                ("https://llm-user:llm-password@switch.example.com/v1", "gpt-4o-mini", "chat_completions"),
            )
            connection.execute(
                """
                INSERT INTO thinking_adaptation_cache
                    (api_base_url, model_name, endpoint_kind, learned_extra_body)
                VALUES (?, ?, ?, ?)
                """,
                ("https://llm-user:llm-password@switch.example.com/v1", "gpt-4o-mini", "chat_completions", None),
            )
            connection.commit()

        class FakeResponse:
            status_code = 200
            text = "response body that must not appear in operation logs"

            def json(self) -> dict[str, object]:
                return {"output_text": "response body that must not appear in operation logs"}

        class FakeAsyncClient:
            def __init__(self, *args: object, **kwargs: object) -> None:
                pass

            async def __aenter__(self) -> "FakeAsyncClient":
                return self

            async def __aexit__(self, *args: object) -> None:
                return None

            async def post(self, url: str, **kwargs: object) -> FakeResponse:
                return FakeResponse()

        with patch("app.modules.llm.runtime.httpx.AsyncClient", FakeAsyncClient):
            response = self.client.post(f"/api/llm-profiles/{llm_profile_id}/test")

        self.assertEqual(response.status_code, 200, msg=response.text)
        self.assertEqual(response.json()["endpoint_kind"], "responses")
        self.assertEqual(
            response.json()["request_url"],
            "https://llm-user:llm-password@switch.example.com/v1/responses",
        )
        self.assertEqual(
            response.json()["attempted_urls"],
            [
                "https://llm-user:llm-password@switch.example.com/v1/chat/completions",
                "https://llm-user:llm-password@switch.example.com/v1/responses",
            ],
        )

        logs = self._list_logs("llm.endpoint_protocol_switched", entity_id=str(llm_profile_id))
        self.assertEqual(len(logs), 1)
        metadata = logs[0]["metadata"]
        self.assertEqual(metadata["old_endpoint_kind"], "chat_completions")
        self.assertEqual(metadata["new_endpoint_kind"], "responses")
        self.assertEqual(metadata["endpoint_kind"], "responses")
        self.assertEqual(metadata["request_url"], "https://switch.example.com/v1/responses")
        self.assertEqual(
            metadata["attempted_urls"],
            [
                "https://switch.example.com/v1/chat/completions",
                "https://switch.example.com/v1/responses",
            ],
        )
        self.assertEqual(metadata["reason"], "other_endpoint")
        self.assertTrue(metadata["retried"])
        self.assertNotIn("sk-endpoint-switch-secret", str(metadata))
        self.assertNotIn("llm-user", str(metadata))
        self.assertNotIn("llm-password", str(metadata))
        self.assertNotIn("response body that must not appear in operation logs", str(metadata))

        request_logs_response = self.client.get(
            "/api/diagnostics/operation-logs",
            params={"request_id": response.headers["X-Request-ID"]},
        )
        self.assertEqual(request_logs_response.status_code, 200, msg=request_logs_response.text)
        request_logs = request_logs_response.json()["items"]
        self.assertGreaterEqual(len(request_logs), 2)
        self.assertTrue(all("llm-user" not in str(log) for log in request_logs))
        self.assertTrue(all("llm-password" not in str(log) for log in request_logs))

    def test_llm_profile_models_fetch_records_result_without_sensitive_urls(self) -> None:
        llm_profile_id = self._create_llm_profile(
            "模型列表配置",
            api_base_url="https://api.example.com/v1?api_key=secret#x",
        )

        with patch(
            "app.modules.llm.api.fetch_llm_profile_models",
            AsyncMock(return_value=self._build_model_catalog_result()),
        ):
            response = self.client.get(f"/api/llm-profiles/{llm_profile_id}/models")

        self.assertEqual(response.status_code, 200, msg=response.text)
        logs = self._list_logs("llm_profile.models_fetched", entity_id=str(llm_profile_id))

        self.assertEqual(len(logs), 1)
        metadata = logs[0]["metadata"]
        self.assertFalse(metadata["ok"])
        self.assertEqual(metadata["status_code"], 503)
        self.assertEqual(metadata["duration_ms"], 34)
        self.assertEqual(metadata["model_count"], 2)
        self.assertEqual(metadata["endpoint_kind"], "models")
        self.assertNotIn("secret", str(metadata))
        self.assertNotIn("token=", str(metadata))
        self.assertNotIn("?api_key=", str(metadata))
        self.assertNotIn("#x", str(metadata))

    def test_material_actions_record_operation_logs(self) -> None:
        identity_id = self._create_identity("material-log@example.com")

        upload_response = self.client.post(
            f"/api/identities/{identity_id}/materials",
            files={"file": ("resume.txt", io.BytesIO(b"resume body"), "text/plain")},
            data={"material_type": "resume"},
        )
        self.assertEqual(upload_response.status_code, 201, msg=upload_response.text)
        material_id = upload_response.json()["id"]

        set_primary_response = self.client.post(f"/api/materials/{material_id}/set-primary")
        delete_response = self.client.delete(f"/api/materials/{material_id}")

        self.assertEqual(set_primary_response.status_code, 200, msg=set_primary_response.text)
        self.assertEqual(delete_response.status_code, 204, msg=delete_response.text)
        for event_name in (
            "identity_material.uploaded",
            "identity_material.primary_set",
            "identity_material.deleted",
        ):
            logs = self._list_logs(event_name, entity_id=str(material_id))
            self.assertEqual(len(logs), 1)
            self.assertEqual(logs[0]["metadata"]["source_identity_id"], identity_id)
            self.assertEqual(logs[0]["metadata"]["identity_id"], identity_id)

    def test_workspace_and_email_task_actions_record_operation_logs(self) -> None:
        identity_id = self._create_identity("workspace-log@example.com", outreach_generation_mode="template")
        llm_profile_id = self._create_llm_profile("工作区日志模型")
        professor_id = self._create_professor("工作区导师", "workspace-log@example.edu")

        ensure_response = self.client.post(
            f"/api/workspaces/{professor_id}/ensure-task",
            params={"identity_id": identity_id, "llm_profile_id": llm_profile_id},
        )
        self.assertEqual(ensure_response.status_code, 200, msg=ensure_response.text)
        task_id = ensure_response.json()["current_task"]["id"]

        draft_response = self.client.post(f"/api/email-tasks/{task_id}/generate-draft")
        schedule_response = self.client.post(
            f"/api/email-tasks/{task_id}/approve-and-schedule",
            json={
                "subject": "联系 {{name}}",
                "body_text": "{{name}} 老师您好",
                "body_html": "<p>{{name}} 老师您好</p>",
                "selected_material_ids": [],
                "scheduled_at": "2026-05-05T10:00:00Z",
            },
        )
        cancel_response = self.client.post(f"/api/email-tasks/{task_id}/cancel-schedule")
        with patch(
            "app.modules.workspace.tasks.delivery.mail_runtime.send_prepared_email",
            AsyncMock(return_value=self._build_send_result()),
        ):
            send_response = self.client.post(
                f"/api/email-tasks/{task_id}/approve-and-send",
                json={
                    "subject": "联系 {{name}}",
                    "body_text": "{{name}} 老师您好",
                    "body_html": "<p>{{name}} 老师您好</p>",
                    "selected_material_ids": [],
                },
            )

        self.assertEqual(draft_response.status_code, 200, msg=draft_response.text)
        self.assertEqual(schedule_response.status_code, 200, msg=schedule_response.text)
        self.assertEqual(cancel_response.status_code, 200, msg=cancel_response.text)
        self.assertEqual(send_response.status_code, 200, msg=send_response.text)
        self.assertEqual(len(self._list_logs("email_task.created", entity_id=str(task_id))), 1)
        self.assertEqual(len(self._list_logs("email_task.draft_generated", entity_id=str(task_id))), 1)
        self.assertEqual(len(self._list_logs("email_task.approved_and_scheduled", entity_id=str(task_id))), 1)
        self.assertEqual(len(self._list_logs("email_task.schedule_canceled", entity_id=str(task_id))), 1)
        self.assertEqual(len(self._list_logs("email_task.sent", entity_id=str(task_id))), 1)

    def test_test_compose_actions_record_operation_logs(self) -> None:
        identity_id = self._create_identity("test-compose-log@example.com", outreach_generation_mode="template")
        llm_profile_id = self._create_llm_profile("测试写信日志模型")

        draft_response = self.client.post(f"/api/test-compose/{identity_id}/{llm_profile_id}/generate-draft")
        save_response = self.client.post(
            f"/api/test-compose/{identity_id}/{llm_profile_id}/draft",
            json={
                "subject": "测试主题",
                "body_text": "测试正文",
                "body_html": "<p>测试正文</p>",
                "selected_material_ids": [],
            },
        )
        with patch(
            "app.modules.communications.test_compose.runtime.mail_runtime.send_email_to_recipient",
            AsyncMock(return_value=self._build_send_result()),
        ):
            send_response = self.client.post(
                f"/api/test-compose/{identity_id}/{llm_profile_id}/send",
                json={
                    "subject": "测试主题",
                    "body_text": "测试正文",
                    "body_html": "<p>测试正文</p>",
                    "selected_material_ids": [],
                },
            )

        self.assertEqual(draft_response.status_code, 200, msg=draft_response.text)
        self.assertEqual(save_response.status_code, 200, msg=save_response.text)
        self.assertEqual(send_response.status_code, 200, msg=send_response.text)
        self.assertEqual(len(self._list_logs("test_compose.draft_generated", entity_id=str(identity_id))), 1)
        self.assertEqual(len(self._list_logs("test_compose.draft_saved", entity_id=str(identity_id))), 1)
        self.assertEqual(len(self._list_logs("test_compose.sent", entity_id=str(identity_id))), 1)

    def test_settings_and_misc_actions_record_operation_logs(self) -> None:
        sample_response = self.client.post("/api/professors/import-sample")
        crawler_response = self.client.post("/api/professors/trigger-crawler")

        self.assertEqual(sample_response.status_code, 200, msg=sample_response.text)
        self.assertEqual(crawler_response.status_code, 200, msg=crawler_response.text)
        self.assertEqual(len(self._list_logs("professor.import_sample")), 1)
        self.assertEqual(len(self._list_logs("crawler.trigger_requested")), 1)

    def _list_logs(
        self,
        event_name: str,
        *,
        request_id: str | None = None,
        entity_id: str | None = None,
    ) -> list[dict[str, object]]:
        params: dict[str, str] = {"event_name": event_name}
        if request_id is not None:
            params["request_id"] = request_id
        if entity_id is not None:
            params["entity_id"] = entity_id
        response = self.client.get("/api/diagnostics/operation-logs", params=params)
        self.assertEqual(response.status_code, 200, msg=response.text)
        return response.json()["items"]

    def _create_identity(self, email: str, *, outreach_generation_mode: str = "llm") -> int:
        response = self.client.post(
            "/api/identities",
            json={
                "name": "测试身份",
                "email_address": email,
                "smtp_host": "smtp.example.com",
                "smtp_port": 465,
                "smtp_username": email,
                "smtp_password": "smtp-secret",
                "imap_host": None,
                "imap_port": None,
                "imap_username": None,
                "imap_password": None,
                "default_language": "zh-CN",
                "outreach_generation_mode": outreach_generation_mode,
                "outreach_template_subject": "申请与{{name}}老师交流",
                "outreach_template_body_text": "老师您好，我是{{sender_name}}。",
                "outreach_template_body_html": None,
                "match_threshold": None,
                "daily_send_limit": None,
                "send_interval_min": None,
                "send_interval_max": None,
                "same_domain_cooldown_minutes": None,
                "is_default": True,
            },
        )
        self.assertEqual(response.status_code, 201, msg=response.text)
        return response.json()["id"]

    def _create_llm_profile(
        self,
        name: str,
        *,
        api_key: str = "sk-test-key",
        api_base_url: str = "https://api.example.com/v1",
    ) -> int:
        response = self.client.post(
            "/api/llm-profiles",
            json={
                "name": name,
                "provider": "openai",
                "api_base_url": api_base_url,
                "api_key": api_key,
                "model_name": "gpt-4o-mini",
                "matcher_prompt_template": "matcher",
                "writer_prompt_template": "writer",
                "temperature": 0.2,
                "max_tokens": 2048,
                "is_default": True,
            },
        )
        self.assertEqual(response.status_code, 201, msg=response.text)
        return response.json()["id"]

    def _create_professor(self, name: str, email: str) -> int:
        response = self.client.post("/api/professors", json=self._professor_payload(name, email))
        self.assertEqual(response.status_code, 201, msg=response.text)
        return response.json()["id"]

    @staticmethod
    def _build_send_result():
        from app.modules.communications.transport import SendMailResult

        return SendMailResult(message_id="<operation-log-test@example.com>", provider_payload={})

    @staticmethod
    def _professor_payload(name: str, email: str) -> dict[str, object]:
        return {
            "name": name,
            "email": email,
            "title": "Professor",
            "university": "Example University",
            "school": "School of Computing",
            "department": "Computer Science",
            "research_direction": "Agent systems",
            "recent_papers": [],
            "profile_url": None,
            "source_url": None,
        }

    @staticmethod
    def _build_probe_result():
        from app.modules.llm.runtime import LLMProbeResult

        return LLMProbeResult(
            ok=False,
            message="认证失败",
            resolved_base_url="https://api.example.com/v1?token=secret#frag",
            request_url="https://api.example.com/v1/chat/completions?api_key=secret#frag",
            attempted_urls=["https://api.example.com/v1/chat/completions?api_key=secret#frag"],
            endpoint_kind="chat",
            status_code=401,
            duration_ms=12,
            consumes_tokens=True,
            prompt_tokens=None,
            completion_tokens=None,
            total_tokens=None,
            response_preview=None,
        )

    @staticmethod
    def _build_model_catalog_result():
        from app.modules.llm.runtime import LLMModelCatalogResult

        return LLMModelCatalogResult(
            ok=False,
            message="模型列表获取失败",
            resolved_base_url="https://api.example.com/v1?token=secret#frag",
            request_url="https://api.example.com/v1/models?api_key=secret#frag",
            attempted_urls=["https://api.example.com/v1/models?api_key=secret#frag"],
            endpoint_kind="models",
            status_code=503,
            duration_ms=34,
            consumes_tokens=False,
            models=["gpt-a", "gpt-b"],
            selected_model_available=False,
        )

    async def _clear_database(self) -> None:
        from sqlalchemy import delete

        from app.core.database import get_session_factory
        from app.models import (
            BatchTask,
            EmailLog,
            EmailTask,
            IdentityMaterial,
            IdentityProfile,
            LLMProfile,
            OperationLog,
            OutreachTemplate,
            Professor,
            TestComposeMessage,
            TestComposeSession,
        )
        from app.models.crawl_job import CrawlCandidate, CrawlJob, CrawlPage

        async with get_session_factory()() as session:
            for model in [
                OperationLog,
                TestComposeMessage,
                TestComposeSession,
                EmailLog,
                EmailTask,
                BatchTask,
                CrawlCandidate,
                CrawlPage,
                CrawlJob,
                IdentityMaterial,
                IdentityProfile,
                OutreachTemplate,
                LLMProfile,
                Professor,
            ]:
                await session.execute(delete(model))
            await session.commit()

    @classmethod
    async def _create_schema(cls) -> None:
        from app.core.database import get_engine
        from app.models import Base

        async with get_engine().begin() as connection:
            await connection.run_sync(Base.metadata.create_all)


if __name__ == "__main__":
    unittest.main()
