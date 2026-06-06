from __future__ import annotations

import asyncio
import os
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from test.migrated_database import create_migrated_sqlite_database


class ProfessorTagsApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "professor_tags_api.db"
        os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{self.db_path.as_posix()}"
        os.environ["ENABLE_BACKGROUND_WORKERS"] = "0"
        create_migrated_sqlite_database(self.db_path)

        from app.core.config import get_settings
        from app.core.database import dispose_engine, get_engine, get_session_factory
        from main import create_app

        get_settings.cache_clear()
        if get_engine.cache_info().currsize:
            asyncio.run(dispose_engine())
        get_session_factory.cache_clear()
        get_settings.cache_clear()

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

    def test_default_tags_are_listed(self) -> None:
        response = self.client.get("/api/professors/tags")

        self.assertEqual(response.status_code, 200, msg=response.text)
        self.assertEqual(
            [item["name"] for item in response.json()],
            ["已退休", "高意愿", "低意愿", "羊导", "高强度"],
        )

    def test_create_professor_with_multiple_tags_and_list_them(self) -> None:
        tags = self.client.get("/api/professors/tags").json()
        selected_ids = [tags[0]["id"], tags[1]["id"]]

        response = self.client.post(
            "/api/professors",
            json={
                "name": "张明远",
                "email": "zhang@example.edu",
                "tag_ids": selected_ids,
            },
        )
        dashboard = self.client.get("/api/professors").json()

        self.assertEqual(response.status_code, 201, msg=response.text)
        self.assertEqual([tag["id"] for tag in response.json()["tags"]], selected_ids)
        self.assertEqual([tag["id"] for tag in dashboard[0]["tags"]], selected_ids)

    def test_delete_tag_removes_professor_links(self) -> None:
        tag = self.client.get("/api/professors/tags").json()[0]
        created = self.client.post(
            "/api/professors",
            json={
                "name": "李伟",
                "email": "li@example.edu",
                "tag_ids": [tag["id"]],
            },
        ).json()

        delete_response = self.client.delete(f"/api/professors/tags/{tag['id']}")
        refreshed = self.client.get(f"/api/professors/{created['id']}").json()

        self.assertEqual(delete_response.status_code, 200, msg=delete_response.text)
        self.assertEqual(refreshed["tags"], [])


if __name__ == "__main__":
    unittest.main()
