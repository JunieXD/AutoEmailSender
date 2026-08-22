from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path

from test.migrated_database import create_migrated_sqlite_database


class BatchTaskSummaryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        from main import create_app
        from fastapi.testclient import TestClient

        cls.client = TestClient(create_app())

    @classmethod
    def tearDownClass(cls) -> None:
        cls.client.close()

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "batch_summary.db"
        os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{self.db_path.as_posix()}"
        os.environ["ENABLE_BACKGROUND_WORKERS"] = "0"
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
        os.environ.pop("ENABLE_BACKGROUND_WORKERS", None)
        self.temp_dir.cleanup()

    def _create_professor(self, email: str) -> int:
        response = self.client.post(
            "/api/professors",
            json={
                "name": f"导师{email}",
                "email": email,
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
        self.assertEqual(response.status_code, 201, msg=response.text)
        return response.json()["id"]

    def _seed_batch_task(self, identity_id: int, llm_id: int, items: int) -> int:
        professor_ids = [self._create_professor(f"s{index}@example.edu") for index in range(items)]
        connection = sqlite3.connect(self.db_path)
        try:
            batch_task_id = connection.execute(
                """
                INSERT INTO batch_tasks (
                    identity_id, llm_profile_id, name, schedule_type, status,
                    target_count, selected_material_ids, outreach_generation_mode,
                    created_at, updated_at
                ) VALUES (?, ?, '摘要任务', 'immediate', 'running', ?, '[]', 'template',
                          datetime('now'), datetime('now'))
                RETURNING id
                """,
                (identity_id, llm_id, items),
            ).fetchone()[0]
            for professor_id in professor_ids:
                connection.execute(
                    """
                    INSERT INTO email_tasks (
                        source, batch_task_id, identity_id, llm_profile_id,
                        professor_id, status, selected_material_ids,
                        generated_subject, generated_content_text,
                        outreach_generation_mode, created_at, updated_at
                    ) VALUES (
                        'batch', ?, ?, ?, ?, 'review_required', '[]', '主题', '正文',
                        'template', datetime('now'), datetime('now')
                    )
                    """,
                    (batch_task_id, identity_id, llm_id, professor_id),
                )
            connection.commit()
            return batch_task_id
        finally:
            connection.close()

    def test_summary_reports_counters_without_item_rows(self) -> None:
        identity_id = self.client.post(
            "/api/identities",
            json={
                "name": "测试身份",
                "email_address": "sender@example.com",
                "smtp_host": "smtp.example.com",
                "smtp_port": 465,
                "smtp_username": "sender@example.com",
                "smtp_password": "secret",
                "imap_host": None,
                "imap_port": None,
                "imap_username": None,
                "imap_password": None,
                "default_language": "zh-CN",
                "outreach_generation_mode": "template",
                "outreach_template_subject": "申请与{{name}}老师交流",
                "outreach_template_body_text": "老师您好，我是{{sender_name}}。",
                "match_threshold": None,
                "same_domain_cooldown_minutes": None,
                "is_default": True,
            },
        ).json()["id"]
        llm_id = self.client.post(
            "/api/llm-profiles",
            json={
                "name": "默认模型",
                "provider": "openai",
                "api_base_url": "https://api.example.com/v1",
                "api_key": "sk-test-key",
                "model_name": "gpt-4o-mini",
                "temperature": 0.2,
                "max_tokens": 2048,
                "is_default": True,
            },
        ).json()["id"]

        batch_task_id = self._seed_batch_task(identity_id, llm_id, items=3)
        item_ids = [
            row[0]
            for row in sqlite3.connect(self.db_path)
            .execute("SELECT id FROM email_tasks WHERE batch_task_id = ? ORDER BY id", (batch_task_id,))
            .fetchall()
        ]

        summary = self.client.get(f"/api/batch-tasks/{batch_task_id}/summary")
        self.assertEqual(summary.status_code, 200, msg=summary.text)
        card = summary.json()
        self.assertEqual(card["target_count"], 3)
        self.assertEqual(card["review_required_count"], 3)
        self.assertEqual(card["approved_count"], 0)

        approve = self.client.post(
            f"/api/batch-tasks/{batch_task_id}/items/{item_ids[0]}/approve",
            json={"subject": "s", "body_text": "b", "body_html": None, "selected_material_ids": []},
        )
        self.assertEqual(approve.status_code, 200, msg=approve.text)

        refreshed = self.client.get(f"/api/batch-tasks/{batch_task_id}/summary").json()
        self.assertEqual(refreshed["review_required_count"], 2)
        self.assertEqual(refreshed["approved_count"], 1)

    def test_summary_returns_404_for_missing_task(self) -> None:
        response = self.client.get("/api/batch-tasks/999999/summary")
        self.assertEqual(response.status_code, 404)


if __name__ == "__main__":
    unittest.main()
