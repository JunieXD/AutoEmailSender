from __future__ import annotations

import asyncio
import csv
import io
import os
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from openpyxl import Workbook

from test.migrated_database import create_migrated_sqlite_database
from app.modules.professors.public import (
    PROFESSOR_LEGACY_TEMPLATE_COLUMNS,
    PROFESSOR_TEMPLATE_COLUMNS,
)


class ProfessorTagsApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        from main import create_app

        cls.client = TestClient(create_app(), raise_server_exceptions=False)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.client.close()

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "professor_tags_api.db"
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
        with closing(sqlite3.connect(self.db_path)) as connection:
            connection.execute(
                "UPDATE professors SET email = NULL WHERE id = ?",
                (created["id"],),
            )
            connection.commit()

        response = self.client.patch(
            f"/api/professors/{created['id']}/tags",
            json={"tag_ids": [tag["id"]]},
        )
        refreshed = self.client.get(f"/api/professors/{created['id']}").json()

        self.assertEqual(response.status_code, 200, msg=response.text)
        self.assertEqual(response.json()["tags"][0]["id"], tag["id"])
        self.assertEqual(refreshed["tags"][0]["id"], tag["id"])

    def test_bulk_add_professor_tags_preserves_existing_tags(self) -> None:
        tags = self.client.get("/api/professors/tags").json()
        first_tag_id = tags[0]["id"]
        second_tag_id = tags[1]["id"]
        first = self.client.post(
            "/api/professors",
            json={
                "name": "批量追加一",
                "email": "bulk-add-1@example.edu",
                "tag_ids": [first_tag_id],
            },
        ).json()
        second = self.client.post(
            "/api/professors",
            json={
                "name": "批量追加二",
                "email": "bulk-add-2@example.edu",
                "tag_ids": [],
            },
        ).json()

        response = self.client.post(
            "/api/professors/bulk-tags",
            json={
                "professor_ids": [first["id"], second["id"]],
                "mode": "add",
                "tag_ids": [second_tag_id],
            },
        )

        self.assertEqual(response.status_code, 200, msg=response.text)
        payload = response.json()
        self.assertEqual(payload["affected_count"], 2)
        self.assertNotIn("professors", payload)
        tags_by_id = {
            professor_id: [
                tag["id"]
                for tag in self.client.get(f"/api/professors/{professor_id}").json()["tags"]
            ]
            for professor_id in (first["id"], second["id"])
        }
        self.assertEqual(tags_by_id[first["id"]], [first_tag_id, second_tag_id])
        self.assertEqual(tags_by_id[second["id"]], [second_tag_id])

    def test_bulk_remove_professor_tags_preserves_other_tags(self) -> None:
        tags = self.client.get("/api/professors/tags").json()
        first_tag_id = tags[0]["id"]
        second_tag_id = tags[1]["id"]
        professor = self.client.post(
            "/api/professors",
            json={
                "name": "批量移除",
                "email": "bulk-remove@example.edu",
                "tag_ids": [first_tag_id, second_tag_id],
            },
        ).json()

        response = self.client.post(
            "/api/professors/bulk-tags",
            json={
                "professor_ids": [professor["id"]],
                "mode": "remove",
                "tag_ids": [first_tag_id],
            },
        )

        self.assertEqual(response.status_code, 200, msg=response.text)
        refreshed = self.client.get(f"/api/professors/{professor['id']}").json()
        self.assertEqual(
            [tag["id"] for tag in refreshed["tags"]],
            [second_tag_id],
        )

    def test_bulk_replace_professor_tags_allows_empty_tags(self) -> None:
        tags = self.client.get("/api/professors/tags").json()
        tag_id = tags[0]["id"]
        professor = self.client.post(
            "/api/professors",
            json={
                "name": "批量覆盖",
                "email": "bulk-replace@example.edu",
                "tag_ids": [tag_id],
            },
        ).json()

        response = self.client.post(
            "/api/professors/bulk-tags",
            json={
                "professor_ids": [professor["id"]],
                "mode": "replace",
                "tag_ids": [],
            },
        )

        self.assertEqual(response.status_code, 200, msg=response.text)
        refreshed = self.client.get(f"/api/professors/{professor['id']}").json()
        self.assertEqual(refreshed["tags"], [])

    def test_bulk_archive_supports_more_than_sqlite_parameter_limit(self) -> None:
        professor_ids = self._seed_scale_professors(1_005, prefix="bulk-archive")
        with closing(sqlite3.connect(self.db_path)) as connection:
            connection.execute(
                "UPDATE professors SET archived_at = updated_at WHERE id = ?",
                (professor_ids[-1],),
            )
            connection.commit()

        response = self.client.post(
            "/api/professors/bulk-archive",
            json={"ids": professor_ids},
        )
        repeated = self.client.post(
            "/api/professors/bulk-archive",
            json={"ids": professor_ids},
        )

        self.assertEqual(response.status_code, 200, msg=response.text)
        self.assertEqual(response.json()["affected_count"], 1_004)
        self.assertEqual(repeated.status_code, 200, msg=repeated.text)
        self.assertEqual(repeated.json()["affected_count"], 0)
        with closing(sqlite3.connect(self.db_path)) as connection:
            archived_count = connection.execute(
                "SELECT count(*) FROM professors WHERE archived_at IS NOT NULL",
            ).fetchone()[0]
            log_metadata = connection.execute(
                """
                SELECT metadata
                FROM operation_logs
                WHERE event_name = 'professor.bulk_archived'
                ORDER BY id ASC
                LIMIT 1
                """,
            ).fetchone()[0]
        self.assertEqual(archived_count, 1_005)
        self.assertIn('"ids_truncated": true', log_metadata)

    def test_bulk_tags_supports_more_than_sqlite_parameter_limit(self) -> None:
        professor_ids = self._seed_scale_professors(1_005, prefix="bulk-tags")
        with closing(sqlite3.connect(self.db_path)) as connection:
            tag_ids = [
                row[0]
                for row in connection.execute(
                    "SELECT id FROM professor_tags ORDER BY id ASC LIMIT 2",
                )
            ]
            connection.executemany(
                """
                INSERT INTO professor_tag_links(professor_id, tag_id, sort_order)
                VALUES (?, ?, 0)
                """,
                ((professor_id, tag_ids[0]) for professor_id in professor_ids[:501]),
            )
            connection.commit()

        response = self.client.post(
            "/api/professors/bulk-tags",
            json={
                "professor_ids": professor_ids,
                "mode": "add",
                "tag_ids": [tag_ids[1]],
            },
        )

        self.assertEqual(response.status_code, 200, msg=response.text)
        self.assertEqual(response.json()["affected_count"], 1_005)
        self.assertNotIn("professors", response.json())
        self.assertLess(len(response.content), 512)
        with closing(sqlite3.connect(self.db_path)) as connection:
            first_tag_count = connection.execute(
                "SELECT count(*) FROM professor_tag_links WHERE tag_id = ?",
                (tag_ids[0],),
            ).fetchone()[0]
            second_tag_count = connection.execute(
                "SELECT count(*) FROM professor_tag_links WHERE tag_id = ?",
                (tag_ids[1],),
            ).fetchone()[0]
            existing_sort_order = connection.execute(
                """
                SELECT sort_order FROM professor_tag_links
                WHERE professor_id = ? AND tag_id = ?
                """,
                (professor_ids[0], tag_ids[1]),
            ).fetchone()[0]
            new_sort_order = connection.execute(
                """
                SELECT sort_order FROM professor_tag_links
                WHERE professor_id = ? AND tag_id = ?
                """,
                (professor_ids[-1], tag_ids[1]),
            ).fetchone()[0]
        self.assertEqual(first_tag_count, 501)
        self.assertEqual(second_tag_count, 1_005)
        self.assertEqual(existing_sort_order, 1)
        self.assertEqual(new_sort_order, 0)

    def test_bulk_tags_rejects_empty_add_without_partial_update(self) -> None:
        tags = self.client.get("/api/professors/tags").json()
        tag_id = tags[0]["id"]
        professor = self.client.post(
            "/api/professors",
            json={
                "name": "批量错误",
                "email": "bulk-error@example.edu",
                "tag_ids": [tag_id],
            },
        ).json()

        response = self.client.post(
            "/api/professors/bulk-tags",
            json={
                "professor_ids": [professor["id"]],
                "mode": "add",
                "tag_ids": [],
            },
        )
        refreshed = self.client.get(f"/api/professors/{professor['id']}").json()

        self.assertEqual(response.status_code, 400, msg=response.text)
        self.assertEqual(response.json()["detail"], "请选择要追加或移除的标签")
        self.assertEqual([tag["id"] for tag in refreshed["tags"]], [tag_id])

    def test_bulk_tags_rejects_missing_professor_without_partial_update(self) -> None:
        tags = self.client.get("/api/professors/tags").json()
        first_tag_id = tags[0]["id"]
        second_tag_id = tags[1]["id"]
        professor = self.client.post(
            "/api/professors",
            json={
                "name": "批量缺失导师",
                "email": "bulk-missing-professor@example.edu",
                "tag_ids": [first_tag_id],
            },
        ).json()

        response = self.client.post(
            "/api/professors/bulk-tags",
            json={
                "professor_ids": [professor["id"], 999999],
                "mode": "replace",
                "tag_ids": [second_tag_id],
            },
        )
        refreshed = self.client.get(f"/api/professors/{professor['id']}").json()

        self.assertEqual(response.status_code, 404, msg=response.text)
        self.assertEqual(response.json()["detail"], "导师不存在")
        self.assertEqual([tag["id"] for tag in refreshed["tags"]], [first_tag_id])

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

    def test_import_safe_xlsx_preserves_existing_tags_and_personal_note(self) -> None:
        tag = self.client.get("/api/professors/tags").json()[0]
        created = self.client.post(
            "/api/professors",
            json={
                "name": "安全导入导师",
                "email": "safe-xlsx@example.edu",
                "tag_ids": [tag["id"]],
                "personal_note": "用户已有备注",
            },
        ).json()

        workbook = Workbook()
        sheet = workbook.active
        sheet.title = "Professors"
        sheet.append(PROFESSOR_LEGACY_TEMPLATE_COLUMNS)
        sheet.append(
            [
                "安全导入导师（更新）",
                "safe-xlsx@example.edu",
                "副教授",
                "示例大学",
                "计算机学院",
                "人工智能系",
                "智能体",
                "Paper A",
                "https://example.edu/safe-xlsx",
                "https://example.edu/faculty",
            ],
        )
        sheet.append(
            [
                "安全导入新导师",
                "safe-xlsx-new@example.edu",
                "教授",
                "示例大学",
                "计算机学院",
                "计算机系",
                "机器学习",
                "Paper B",
                "https://example.edu/safe-xlsx-new",
                "https://example.edu/faculty",
            ],
        )
        content = io.BytesIO()
        workbook.save(content)

        response = self.client.post(
            "/api/professors/import-file",
            files={
                "file": (
                    "professors_import.xlsx",
                    content.getvalue(),
                    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                ),
            },
        )
        refreshed = self.client.get(f"/api/professors/{created['id']}").json()
        professors = self.client.get("/api/professors/management").json()
        inserted = next(
            item for item in professors if item["email"] == "safe-xlsx-new@example.edu"
        )

        self.assertEqual(response.status_code, 200, msg=response.text)
        self.assertEqual(response.json()["inserted_count"], 1)
        self.assertEqual(response.json()["updated_count"], 1)
        self.assertEqual(refreshed["name"], "安全导入导师（更新）")
        self.assertEqual(refreshed["personal_note"], "用户已有备注")
        self.assertEqual([item["id"] for item in refreshed["tags"]], [tag["id"]])
        self.assertIsNone(inserted["personal_note"])
        self.assertEqual(inserted["tags"], [])

    def test_import_upsert_supports_more_than_sqlite_parameter_limit(self) -> None:
        existing_ids = self._seed_scale_professors(505, prefix="bulk-import")
        with closing(sqlite3.connect(self.db_path)) as connection:
            connection.execute(
                "UPDATE professors SET archived_at = updated_at WHERE id = ?",
                (existing_ids[0],),
            )
            connection.commit()

        buffer = io.StringIO()
        writer = csv.writer(buffer)
        writer.writerow(PROFESSOR_TEMPLATE_COLUMNS)
        for index in range(1_005):
            writer.writerow(
                [
                    f"更新后导师 {index}",
                    f"bulk-import-{index:04d}@example.edu",
                    "教授",
                    "规模大学",
                    "计算机学院",
                    "软件系",
                    f"数据库系统 {index}",
                    f"Paper {index}",
                    f"https://example.edu/professors/{index}",
                    "https://example.edu/faculty",
                    "规模标签",
                    f"导入备注 {index}",
                ],
            )

        response = self.client.post(
            "/api/professors/import-file",
            files={
                "file": (
                    "bulk-professors.csv",
                    buffer.getvalue().encode("utf-8-sig"),
                    "text/csv",
                ),
            },
        )

        self.assertEqual(response.status_code, 200, msg=response.text)
        self.assertEqual(response.json()["inserted_count"], 500)
        self.assertEqual(response.json()["updated_count"], 505)
        self.assertEqual(response.json()["failed_count"], 0)
        with closing(sqlite3.connect(self.db_path)) as connection:
            professor_count = connection.execute(
                "SELECT count(*) FROM professors",
            ).fetchone()[0]
            archived_count = connection.execute(
                "SELECT count(*) FROM professors WHERE archived_at IS NOT NULL",
            ).fetchone()[0]
            first_row = connection.execute(
                """
                SELECT name, personal_note FROM professors
                WHERE email = 'bulk-import-0000@example.edu'
                """,
            ).fetchone()
            tagged_count = connection.execute(
                """
                SELECT count(*)
                FROM professor_tag_links AS links
                JOIN professor_tags AS tags ON tags.id = links.tag_id
                WHERE tags.name = '规模标签'
                """,
            ).fetchone()[0]
        self.assertEqual(professor_count, 1_005)
        self.assertEqual(archived_count, 0)
        self.assertEqual(first_row, ("更新后导师 0", "导入备注 0"))
        self.assertEqual(tagged_count, 1_005)

    def _seed_scale_professors(self, count: int, *, prefix: str) -> list[int]:
        with closing(sqlite3.connect(self.db_path)) as connection:
            connection.executemany(
                """
                INSERT INTO professors(
                    name, email, research_direction, recent_papers,
                    crawl_status, communication_sync_version, created_at, updated_at
                ) VALUES (?, ?, ?, '[]', 'discovered', 1, ?, ?)
                """,
                (
                    (
                        f"规模导师 {index}",
                        f"{prefix}-{index:04d}@example.edu",
                        f"研究方向 {index}",
                        "2026-08-09 00:00:00.000000",
                        "2026-08-09 00:00:00.000000",
                    )
                    for index in range(count)
                ),
            )
            connection.commit()
            return [
                row[0]
                for row in connection.execute(
                    "SELECT id FROM professors ORDER BY id ASC",
                )
            ]


if __name__ == "__main__":
    unittest.main()
