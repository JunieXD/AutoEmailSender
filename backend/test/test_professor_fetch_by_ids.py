from __future__ import annotations

import asyncio
import os
import tempfile
import unittest
from pathlib import Path

from test.migrated_database import create_migrated_sqlite_database


class ProfessorFetchByIdsTests(unittest.TestCase):
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
        self.db_path = Path(self.temp_dir.name) / "professor_fetch.db"
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

    def test_fetch_by_ids_matches_get_ids_query(self) -> None:
        professor_ids = [self._create_professor(f"p{index}@example.edu") for index in range(3)]

        post_response = self.client.post(
            "/api/professors/fetch-by-ids",
            json={"identity_id": None, "ids": professor_ids},
        )
        get_response = self.client.get(
            "/api/professors",
            params={"ids": ",".join(str(item) for item in professor_ids)},
        )
        self.assertEqual(post_response.status_code, 200, msg=post_response.text)
        self.assertEqual(get_response.status_code, 200, msg=get_response.text)
        self.assertEqual(post_response.json(), get_response.json())

    def test_fetch_by_ids_with_empty_ids_returns_empty_list(self) -> None:
        response = self.client.post(
            "/api/professors/fetch-by-ids",
            json={"identity_id": None, "ids": []},
        )
        self.assertEqual(response.status_code, 200, msg=response.text)
        self.assertEqual(response.json(), [])

    def test_fetch_by_ids_resolves_dashboard_fields_for_identity(self) -> None:
        identity_response = self.client.post(
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
        )
        self.assertEqual(identity_response.status_code, 201, msg=identity_response.text)
        identity_id = identity_response.json()["id"]

        professor_id = self._create_professor("identity-field@example.edu")
        response = self.client.post(
            "/api/professors/fetch-by-ids",
            json={"identity_id": identity_id, "ids": [professor_id]},
        )
        self.assertEqual(response.status_code, 200, msg=response.text)
        items = response.json()
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["id"], professor_id)
        self.assertEqual(items[0]["status"], "not_contacted")
        self.assertEqual(items[0]["sent_count"], 0)


if __name__ == "__main__":
    unittest.main()
