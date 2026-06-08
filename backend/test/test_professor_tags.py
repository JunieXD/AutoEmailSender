from __future__ import annotations

import asyncio
import csv
import io
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from test.migrated_database import create_migrated_sqlite_database
from app.services.professor_management import PROFESSOR_TEMPLATE_COLUMNS


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

        self.client = TestClient(create_app(), raise_server_exceptions=False)

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

    def test_professor_tags_keep_payload_order(self) -> None:
        tags = self.client.get("/api/professors/tags").json()
        selected_ids = [tags[2]["id"], tags[0]["id"], tags[1]["id"]]

        created_response = self.client.post(
            "/api/professors",
            json={
                "name": "排序导师",
                "email": "ordered@example.edu",
                "tag_ids": selected_ids,
            },
        )
        created = created_response.json()
        reordered_ids = [selected_ids[1], selected_ids[2], selected_ids[0]]
        updated_response = self.client.patch(
            f"/api/professors/{created['id']}",
            json={
                "name": "排序导师",
                "email": "ordered@example.edu",
                "tag_ids": reordered_ids,
            },
        )
        updated = updated_response.json()

        self.assertEqual(created_response.status_code, 201, msg=created_response.text)
        self.assertEqual(updated_response.status_code, 200, msg=updated_response.text)
        self.assertEqual([tag["id"] for tag in created["tags"]], selected_ids)
        self.assertEqual([tag["id"] for tag in updated["tags"]], reordered_ids)

    def test_update_professor_tags_allows_professor_without_email(self) -> None:
        tag = self.client.get("/api/professors/tags").json()[0]
        created = self.client.post(
            "/api/professors",
            json={
                "name": "无邮箱导师",
                "email": "missing-email@example.edu",
            },
        ).json()
        with sqlite3.connect(self.db_path) as connection:
            connection.execute(
                "UPDATE professors SET email = NULL WHERE id = ?",
                (created["id"],),
            )

        response = self.client.patch(
            f"/api/professors/{created['id']}/tags",
            json={"tag_ids": [tag["id"]]},
        )
        refreshed = self.client.get(f"/api/professors/{created['id']}").json()

        self.assertEqual(response.status_code, 200, msg=response.text)
        self.assertEqual(response.json()["tags"][0]["id"], tag["id"])
        self.assertEqual(refreshed["tags"][0]["id"], tag["id"])

    def test_create_duplicate_tag_constraint_returns_conflict(self) -> None:
        async def miss_preflight_duplicate_check(*args: object, **kwargs: object) -> None:
            return None

        with patch(
            "sqlalchemy.ext.asyncio.AsyncSession.scalar",
            new=miss_preflight_duplicate_check,
        ):
            response = self.client.post(
                "/api/professors/tags",
                json={
                    "name": "高意愿",
                    "text_color": "#166534",
                    "background_color": "#dcfce7",
                },
            )

        self.assertEqual(response.status_code, 409, msg=response.text)
        self.assertEqual(response.json()["detail"], "标签已存在")

    def test_tag_usage_lists_professors_using_tag(self) -> None:
        tag = self.client.get("/api/professors/tags").json()[0]
        create_response = self.client.post(
            "/api/professors",
            json={
                "name": "使用标签导师",
                "email": "usage@example.edu",
                "university": "示例大学",
                "school": "计算机学院",
                "tag_ids": [tag["id"]],
            },
        )
        usage_response = self.client.get(f"/api/professors/tags/{tag['id']}/usage")

        self.assertEqual(create_response.status_code, 201, msg=create_response.text)
        self.assertEqual(usage_response.status_code, 200, msg=usage_response.text)
        usage = usage_response.json()
        self.assertEqual(usage["tag"]["id"], tag["id"])
        self.assertEqual([item["name"] for item in usage["professors"]], ["使用标签导师"])

    def test_tag_usage_route_is_not_shadowed_by_professor_detail_route(self) -> None:
        tag = self.client.get("/api/professors/tags").json()[0]

        usage_response = self.client.get(f"/api/professors/tags/{tag['id']}/usage")

        self.assertEqual(usage_response.status_code, 200, msg=usage_response.text)
        self.assertIn("professors", usage_response.json())

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

    def test_import_file_creates_missing_tags_and_preserves_tag_order(self) -> None:
        buffer = io.StringIO()
        writer = csv.writer(buffer)
        writer.writerow(PROFESSOR_TEMPLATE_COLUMNS)
        writer.writerow(
            [
                "导入标签导师",
                "import-tags@example.edu",
                "教授",
                "示例大学",
                "计算机学院",
                "",
                "大语言模型",
                "",
                "",
                "",
                "高意愿；已联系；羊导",
            ],
        )

        response = self.client.post(
            "/api/professors/import-file",
            files={
                "file": (
                    "professors.csv",
                    buffer.getvalue().encode("utf-8-sig"),
                    "text/csv",
                ),
            },
        )
        professors = self.client.get("/api/professors/management").json()
        imported = next(
            professor
            for professor in professors
            if professor["email"] == "import-tags@example.edu"
        )

        self.assertEqual(response.status_code, 200, msg=response.text)
        self.assertEqual(response.json()["inserted_count"], 1)
        self.assertIn("创建标签 1 个", response.json()["message"])
        self.assertEqual(
            [tag["name"] for tag in imported["tags"]],
            ["高意愿", "已联系", "羊导"],
        )
        self.assertIn(
            "已联系",
            [tag["name"] for tag in self.client.get("/api/professors/tags").json()],
        )

    def test_import_file_blank_tags_keeps_existing_professor_tags(self) -> None:
        tag = self.client.get("/api/professors/tags").json()[0]
        created = self.client.post(
            "/api/professors",
            json={
                "name": "保留标签导师",
                "email": "keep-tags@example.edu",
                "tag_ids": [tag["id"]],
            },
        ).json()
        buffer = io.StringIO()
        writer = csv.writer(buffer)
        writer.writerow(PROFESSOR_TEMPLATE_COLUMNS)
        writer.writerow(
            [
                "保留标签导师更新",
                "keep-tags@example.edu",
                "教授",
                "示例大学",
                "",
                "",
                "智能体",
                "",
                "",
                "",
                "",
            ],
        )

        response = self.client.post(
            "/api/professors/import-file",
            files={
                "file": (
                    "professors.csv",
                    buffer.getvalue().encode("utf-8-sig"),
                    "text/csv",
                ),
            },
        )
        refreshed = self.client.get(f"/api/professors/{created['id']}").json()

        self.assertEqual(response.status_code, 200, msg=response.text)
        self.assertEqual(response.json()["updated_count"], 1)
        self.assertEqual([item["id"] for item in refreshed["tags"]], [tag["id"]])


if __name__ == "__main__":
    unittest.main()
