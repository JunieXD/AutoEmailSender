from __future__ import annotations

import asyncio
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from test.migrated_database import create_migrated_sqlite_database

from app.modules.system.restart_safety.service import (
    RestartSafetyCounts,
    summarize_restart_safety,
)


class RestartSafetyTests(unittest.TestCase):
    def test_no_active_work_allows_restart_without_confirmation(self) -> None:
        result = summarize_restart_safety(RestartSafetyCounts())

        self.assertTrue(result.safe_to_restart)
        self.assertFalse(result.confirmation_required)
        self.assertEqual(result.active_work_count, 0)
        self.assertEqual(result.sending_count, 0)

    def test_recoverable_background_work_requires_confirmation(self) -> None:
        result = summarize_restart_safety(
            RestartSafetyCounts(
                draft_generation=2,
                match_analysis=3,
                crawler_pages=5,
                crawler_chunks=7,
                crawler_enrichment=11,
                imap_sync=13,
            )
        )

        self.assertTrue(result.safe_to_restart)
        self.assertTrue(result.confirmation_required)
        self.assertEqual(result.active_work_count, 41)
        self.assertEqual(
            result.work_counts.model_dump(),
            {
                "draft_generation": 2,
                "match_analysis": 3,
                "crawler": 23,
                "imap_sync": 13,
            },
        )

    def test_sending_window_blocks_restart_even_with_confirmation(self) -> None:
        result = summarize_restart_safety(
            RestartSafetyCounts(sending=2, draft_generation=3)
        )

        self.assertFalse(result.safe_to_restart)
        self.assertFalse(result.confirmation_required)
        self.assertEqual(result.active_work_count, 5)
        self.assertEqual(result.sending_count, 2)
        self.assertIn("避免重复发送", result.message)


class RestartSafetyApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "restart-safety.db"
        os.environ["DATABASE_URL"] = (
            f"sqlite+aiosqlite:///{self.db_path.as_posix()}"
        )
        os.environ["ENABLE_BACKGROUND_WORKERS"] = "0"
        create_migrated_sqlite_database(self.db_path)

        from app.core.config import get_settings
        from app.core.database import dispose_engine, get_engine, get_session_factory

        get_settings.cache_clear()
        if get_engine.cache_info().currsize:
            asyncio.run(dispose_engine())
        get_session_factory.cache_clear()

        from main import create_app

        self.client = TestClient(create_app())

    def tearDown(self) -> None:
        self.client.close()

        from app.core.config import get_settings
        from app.core.database import dispose_engine, get_engine, get_session_factory

        if get_engine.cache_info().currsize:
            asyncio.run(dispose_engine())
        get_session_factory.cache_clear()
        get_settings.cache_clear()
        os.environ.pop("DATABASE_URL", None)
        os.environ.pop("ENABLE_BACKGROUND_WORKERS", None)
        self.temp_dir.cleanup()

    def test_restart_safety_endpoint_blocks_a_sending_row(self) -> None:
        connection = sqlite3.connect(self.db_path)
        try:
            connection.execute(
                """
                INSERT INTO email_tasks (
                    identity_id, llm_profile_id, professor_id, status
                ) VALUES (1, 1, 1, 'sending')
                """
            )
            connection.commit()
        finally:
            connection.close()

        response = self.client.get("/api/desktop/restart-safety")

        self.assertEqual(response.status_code, 200, msg=response.text)
        self.assertEqual(
            response.json(),
            {
                "safe_to_restart": False,
                "confirmation_required": False,
                "active_work_count": 1,
                "sending_count": 1,
                "work_counts": {
                    "draft_generation": 0,
                    "match_analysis": 0,
                    "crawler": 0,
                    "imap_sync": 0,
                },
                "message": (
                    "有 1 封邮件正处于发送与本地提交窗口，"
                    "为避免重复发送，请等待发送结束后再重启。"
                ),
            },
        )


if __name__ == "__main__":
    unittest.main()
