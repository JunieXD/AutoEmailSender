from __future__ import annotations

import asyncio
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from test.migrated_database import create_migrated_sqlite_database


class ProfessorInformationEnrichmentApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        from main import create_app

        cls.client = TestClient(create_app())

    @classmethod
    def tearDownClass(cls) -> None:
        cls.client.close()

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "professor_information_enrichment_api.db"
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
        self.llm_profile_id = self._create_llm_profile()

    def tearDown(self) -> None:
        self.client.cookies.clear()
        from app.core.config import get_settings
        from app.core.database import dispose_engine, get_engine, get_session_factory

        if get_engine.cache_info().currsize:
            asyncio.run(dispose_engine())
        get_session_factory.cache_clear()
        get_settings.cache_clear()
        os.environ.pop("DATABASE_URL", None)
        os.environ.pop("ENABLE_BACKGROUND_WORKERS", None)
        self.temp_dir.cleanup()

    def test_single_job_is_hidden_from_task_lists_and_reports_active_state(self) -> None:
        professor_id = self._create_professor(
            name="单次补全导师",
            email="single@example.edu",
            profile_url="https://example.edu/single",
        )

        created = self.client.post(
            f"/api/professors/{professor_id}/information-enrichment",
            json={"llm_profile_id": self.llm_profile_id},
        )
        self.assertEqual(created.status_code, 201, msg=created.text)
        self.assertEqual(created.json()["trigger_mode"], "single")
        self.assertEqual(created.json()["status"], "queued")

        active = self.client.get(
            f"/api/professors/{professor_id}/information-enrichment/active",
        )
        self.assertEqual(active.status_code, 200, msg=active.text)
        self.assertTrue(active.json()["active"])
        self.assertEqual(active.json()["job"]["id"], created.json()["id"])

        information_jobs = self.client.get("/api/professor-information-enrichment-jobs")
        self.assertEqual(information_jobs.status_code, 200)
        self.assertEqual(information_jobs.json(), [])
        crawl_jobs = self.client.get("/api/crawl-jobs")
        self.assertEqual(crawl_jobs.status_code, 200)
        self.assertEqual(crawl_jobs.json(), [])

        with sqlite3.connect(self.db_path) as connection:
            candidate_id = int(
                connection.execute(
                    "SELECT id FROM crawl_candidates WHERE job_id = ?",
                    (created.json()["id"],),
                ).fetchone()[0]
            )
        candidate_update = self.client.patch(
            f"/api/crawl-jobs/candidates/{candidate_id}",
            json={
                "name": "不应被普通抓取接口修改",
                "email": "mutated@example.edu",
                "recent_papers": [],
                "review_status": "pending",
            },
        )
        self.assertEqual(candidate_update.status_code, 404, msg=candidate_update.text)

    def test_batch_job_retains_conflicts_as_skipped_and_supports_trash_actions(self) -> None:
        professor_id = self._create_professor(
            name="批量补全导师",
            email="batch@example.edu",
            profile_url="https://example.edu/batch",
        )
        first = self.client.post(
            f"/api/professors/{professor_id}/information-enrichment",
            json={"llm_profile_id": self.llm_profile_id},
        )
        self.assertEqual(first.status_code, 201, msg=first.text)

        batch = self.client.post(
            "/api/professor-information-enrichment-jobs",
            json={
                "professor_ids": [professor_id],
                "llm_profile_id": self.llm_profile_id,
            },
        )
        self.assertEqual(batch.status_code, 201, msg=batch.text)
        payload = batch.json()
        self.assertEqual(payload["status"], "completed")
        self.assertEqual(payload["skipped_count"], 1)
        job_id = payload["id"]

        items = self.client.get(
            f"/api/professor-information-enrichment-jobs/{job_id}/items",
        )
        self.assertEqual(items.status_code, 200, msg=items.text)
        self.assertEqual(items.json()[0]["status"], "skipped")
        self.assertEqual(items.json()[0]["skip_reason"], "已有信息补全正在进行")

        deleted = self.client.delete(
            f"/api/professor-information-enrichment-jobs/{job_id}",
        )
        self.assertEqual(deleted.status_code, 200, msg=deleted.text)
        self.assertIsNotNone(deleted.json()["job"]["deleted_at"])
        self.assertEqual(
            self.client.get("/api/professor-information-enrichment-jobs").json(),
            [],
        )
        trash = self.client.get(
            "/api/professor-information-enrichment-jobs",
            params={"view": "trash"},
        )
        self.assertEqual([item["id"] for item in trash.json()], [job_id])

        restored = self.client.post(
            f"/api/professor-information-enrichment-jobs/{job_id}/restore",
        )
        self.assertEqual(restored.status_code, 200, msg=restored.text)
        self.assertIsNone(restored.json()["job"]["deleted_at"])

    def _create_llm_profile(self) -> int:
        response = self.client.post(
            "/api/llm-profiles",
            json={
                "name": "测试模型",
                "provider": "openai",
                "api_base_url": "https://api.example.com/v1",
                "api_key": "sk-test",
                "model_name": "gpt-test",
                "is_default": True,
            },
        )
        self.assertEqual(response.status_code, 201, msg=response.text)
        return int(response.json()["id"])

    def _create_professor(self, *, name: str, email: str, profile_url: str) -> int:
        response = self.client.post(
            "/api/professors",
            json={
                "name": name,
                "email": email,
                "title": None,
                "university": "示例大学",
                "school": "计算机学院",
                "department": None,
                "research_direction": None,
                "recent_papers": [],
                "profile_url": profile_url,
                "source_url": None,
                "personal_note": None,
                "tag_ids": [],
            },
        )
        self.assertEqual(response.status_code, 201, msg=response.text)
        return int(response.json()["id"])


if __name__ == "__main__":
    unittest.main()
