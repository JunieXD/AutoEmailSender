from __future__ import annotations

import asyncio
import os
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path

from fastapi.testclient import TestClient


class DiagnosticsApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.temp_dir = tempfile.TemporaryDirectory()
        cls.db_path = Path(cls.temp_dir.name) / "diagnostics_api_test.db"
        cls.crawler_debug_dir = Path(cls.temp_dir.name) / "crawler-debug"
        os.environ["AUTO_EMAIL_SENDER_DATA_DIR"] = cls.temp_dir.name
        os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{cls.db_path.as_posix()}"
        os.environ["ENABLE_BACKGROUND_WORKERS"] = "0"
        os.environ["CRAWLER_DEBUG_DIR"] = cls.crawler_debug_dir.as_posix()

        from app.core.config import get_settings
        from app.core.database import dispose_engine, get_engine, get_session_factory

        get_settings.cache_clear()
        if get_engine.cache_info().currsize:
            asyncio.run(dispose_engine())
        get_session_factory.cache_clear()
        get_settings.cache_clear()
        asyncio.run(cls._create_operation_logs_schema())

        from main import create_app
        cls.client = TestClient(create_app())

    @classmethod
    async def _create_operation_logs_schema(cls) -> None:
        from app.core.database import get_engine
        from app.models.operation_log import OperationLog

        async with get_engine().begin() as connection:
            await connection.run_sync(OperationLog.metadata.create_all)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.client.close()

        from app.core.database import dispose_engine, get_engine, get_session_factory

        if get_engine.cache_info().currsize:
            asyncio.run(dispose_engine())
        get_session_factory.cache_clear()

        from app.core.config import get_settings

        get_settings.cache_clear()
        os.environ.pop("AUTO_EMAIL_SENDER_DATA_DIR", None)
        os.environ.pop("DATABASE_URL", None)
        os.environ.pop("ENABLE_BACKGROUND_WORKERS", None)
        os.environ.pop("CRAWLER_DEBUG_DIR", None)
        cls.temp_dir.cleanup()

    def setUp(self) -> None:
        asyncio.run(self._clear_operation_logs())
        for log_name in ["startup.log", "backend-errors.log"]:
            log_file = Path(self.temp_dir.name) / "logs" / log_name
            if log_file.exists():
                log_file.unlink()

    async def _clear_operation_logs(self) -> None:
        from sqlalchemy import delete

        from app.core.database import get_session_factory
        from app.models import OperationLog

        async with get_session_factory()() as session:
            await session.execute(delete(OperationLog))
            await session.commit()

    def test_list_operation_logs_orders_by_created_at_and_id_with_total(self) -> None:
        base_time = datetime(2026, 4, 26, 8, 30, tzinfo=UTC)
        older_id = self._seed_log(
            category="mail",
            event_name="email.started",
            created_at=base_time - timedelta(minutes=1),
        )
        middle_id = self._seed_log(
            category="mail",
            event_name="email.finished",
            created_at=base_time,
        )
        latest_id = self._seed_log(
            category="llm",
            event_name="draft.generated",
            created_at=base_time,
        )

        response = self.client.get("/api/diagnostics/operation-logs?limit=2&offset=1")

        self.assertEqual(response.status_code, 200, msg=response.text)
        payload = response.json()
        self.assertEqual(payload["total"], 3)
        self.assertEqual(payload["limit"], 2)
        self.assertEqual(payload["offset"], 1)
        self.assertEqual([item["id"] for item in payload["items"]], [middle_id, older_id])
        all_response = self.client.get("/api/diagnostics/operation-logs")
        self.assertEqual(
            [item["id"] for item in all_response.json()["items"]],
            [latest_id, middle_id, older_id],
        )

    def test_list_operation_logs_filters_common_dimensions(self) -> None:
        expected_id = self._seed_log(
            request_id="req-expected",
            category="mail",
            event_name="email.failed",
            level="error",
            entity_type="email_task",
            entity_id="task-1",
        )
        self._seed_log(
            request_id="req-other",
            category="mail",
            event_name="email.failed",
            level="error",
            entity_type="email_task",
            entity_id="task-1",
        )
        self._seed_log(
            request_id="req-expected",
            category="llm",
            event_name="email.failed",
            level="error",
            entity_type="email_task",
            entity_id="task-1",
        )

        response = self.client.get(
            "/api/diagnostics/operation-logs",
            params={
                "level": "error",
                "category": "mail",
                "event_name": "email.failed",
                "request_id": "req-expected",
                "entity_type": "email_task",
                "entity_id": "task-1",
            },
        )

        self.assertEqual(response.status_code, 200, msg=response.text)
        payload = response.json()
        self.assertEqual(payload["total"], 1)
        self.assertEqual([item["id"] for item in payload["items"]], [expected_id])

    def test_list_operation_logs_validates_limit_bounds(self) -> None:
        lower_response = self.client.get("/api/diagnostics/operation-logs?limit=0")
        upper_response = self.client.get("/api/diagnostics/operation-logs?limit=501")

        self.assertEqual(lower_response.status_code, 422)
        self.assertEqual(upper_response.status_code, 422)

    def test_list_operation_logs_filters_by_created_at_range(self) -> None:
        expected_id = self._seed_log(
            category="crawler",
            event_name="crawl.today",
            created_at=datetime(2026, 4, 26, 10, 0, tzinfo=UTC),
        )
        self._seed_log(
            category="crawler",
            event_name="crawl.yesterday",
            created_at=datetime(2026, 4, 25, 23, 59, tzinfo=UTC),
        )
        self._seed_log(
            category="crawler",
            event_name="crawl.tomorrow",
            created_at=datetime(2026, 4, 27, 0, 0, tzinfo=UTC),
        )

        response = self.client.get(
            "/api/diagnostics/operation-logs",
            params={
                "start_at": "2026-04-26T00:00:00Z",
                "end_at": "2026-04-27T00:00:00Z",
            },
        )

        self.assertEqual(response.status_code, 200, msg=response.text)
        payload = response.json()
        self.assertEqual(payload["total"], 1)
        self.assertEqual([item["id"] for item in payload["items"]], [expected_id])

    def test_export_operation_logs_returns_timestamp_filters_and_default_limit(self) -> None:
        self._seed_logs(
            [
                {
                    "request_id": "export-req" if index % 2 == 0 else "other-req",
                    "category": "mail",
                    "event_name": "email.exported",
                    "level": "info",
                    "entity_type": "email_task",
                    "entity_id": "task-1",
                    "created_at": datetime(2026, 4, 26, 9, 0, tzinfo=UTC)
                    + timedelta(seconds=index),
                }
                for index in range(501)
            ],
        )

        response = self.client.get(
            "/api/diagnostics/export",
            params={
                "category": "mail",
                "entity_type": "email_task",
                "entity_id": "task-1",
            },
        )

        self.assertEqual(response.status_code, 200, msg=response.text)
        payload = response.json()
        self.assertIsInstance(payload["exported_at"], str)
        self.assertEqual(payload["total"], 501)
        self.assertEqual(len(payload["items"]), 500)
        self.assertEqual(
            payload["filters"],
            {
                "level": None,
                "category": "mail",
                "event_name": None,
                "request_id": None,
                "entity_type": "email_task",
                "entity_id": "task-1",
                "start_at": None,
                "end_at": None,
            },
        )

    def test_export_operation_logs_filters_by_created_at_range(self) -> None:
        expected_id = self._seed_log(
            category="mail",
            event_name="email.today",
            created_at=datetime(2026, 4, 26, 12, 0, tzinfo=UTC),
        )
        self._seed_log(
            category="mail",
            event_name="email.other_day",
            created_at=datetime(2026, 4, 27, 0, 0, tzinfo=UTC),
        )

        response = self.client.get(
            "/api/diagnostics/export",
            params={
                "start_at": "2026-04-26T00:00:00Z",
                "end_at": "2026-04-27T00:00:00Z",
            },
        )

        self.assertEqual(response.status_code, 200, msg=response.text)
        payload = response.json()
        self.assertEqual(payload["total"], 1)
        self.assertEqual([item["id"] for item in payload["items"]], [expected_id])
        self.assertEqual(payload["filters"]["start_at"], "2026-04-26T00:00:00+00:00")
        self.assertEqual(payload["filters"]["end_at"], "2026-04-27T00:00:00+00:00")

    def test_export_operation_logs_includes_startup_log_file(self) -> None:
        startup_log = Path(self.temp_dir.name) / "logs" / "startup.log"
        startup_log.parent.mkdir(parents=True, exist_ok=True)
        startup_log.write_text(
            "[2026-05-18T10:00:00+00:00] 桌面后端启动初始化失败\n"
            "error=RuntimeError: database is locked\n",
            encoding="utf-8",
            newline="\n",
        )

        response = self.client.get("/api/diagnostics/export")

        self.assertEqual(response.status_code, 200, msg=response.text)
        startup_logs = response.json()["startup_logs"]
        self.assertEqual(len(startup_logs), 1)
        self.assertEqual(startup_logs[0]["name"], "startup.log")
        self.assertEqual(startup_logs[0]["relative_path"], "logs/startup.log")
        self.assertIn("database is locked", startup_logs[0]["content"])

    def test_export_operation_logs_includes_backend_error_log_file(self) -> None:
        error_log = Path(self.temp_dir.name) / "logs" / "backend-errors.log"
        error_log.parent.mkdir(parents=True, exist_ok=True)
        error_log.write_text(
            "[2026-06-04T10:00:00+00:00] request_id=req-1 GET /boom\\n"
            "RuntimeError: boom\\n",
            encoding="utf-8",
            newline="\n",
        )

        response = self.client.get("/api/diagnostics/export")

        self.assertEqual(response.status_code, 200, msg=response.text)
        startup_logs = response.json()["startup_logs"]
        error_files = [item for item in startup_logs if item["name"] == "backend-errors.log"]
        self.assertEqual(len(error_files), 1)
        self.assertEqual(error_files[0]["relative_path"], "logs/backend-errors.log")
        self.assertIn("RuntimeError: boom", error_files[0]["content"])

    def test_operation_log_metadata_field_maps_from_event_metadata(self) -> None:
        log_id = self._seed_log(
            category="llm",
            event_name="draft.generated",
            event_metadata={"status": "ok", "usage": {"prompt_tokens": 10}},
        )

        response = self.client.get("/api/diagnostics/operation-logs")

        self.assertEqual(response.status_code, 200, msg=response.text)
        item = response.json()["items"][0]
        self.assertEqual(item["id"], log_id)
        self.assertEqual(item["metadata"], {"status": "ok", "usage": {"prompt_tokens": 10}})
        self.assertNotIn("event_metadata", item)

    def test_export_crawler_debug_jsonl_returns_job_file(self) -> None:
        self.crawler_debug_dir.mkdir(parents=True, exist_ok=True)
        debug_file = self.crawler_debug_dir / "crawl-job-42.jsonl"
        debug_file.write_text(
            '{"job_id": 42, "event_type": "started"}\n'
            '{"job_id": 42, "event_type": "finished"}\n',
            encoding="utf-8",
            newline="\n",
        )

        response = self.client.get("/api/diagnostics/crawler-debug/42/export")

        self.assertEqual(response.status_code, 200, msg=response.text)
        self.assertEqual(response.headers["content-type"], "application/jsonl; charset=utf-8")
        self.assertEqual(
            response.headers["content-disposition"],
            'attachment; filename="crawl-job-42.jsonl"',
        )
        self.assertEqual(response.text.count("\n"), 2)
        self.assertIn('"event_type": "started"', response.text)

    def test_export_crawler_debug_jsonl_returns_404_when_missing(self) -> None:
        response = self.client.get("/api/diagnostics/crawler-debug/404/export")

        self.assertEqual(response.status_code, 404)

    def _seed_log(
        self,
        *,
        category: str = "backend",
        event_name: str = "operation.completed",
        level: str = "info",
        message: str | None = "operation completed",
        request_id: str | None = None,
        entity_type: str | None = None,
        entity_id: str | None = None,
        event_metadata: dict[str, object] | None = None,
        created_at: datetime | None = None,
    ) -> int:
        async def _seed() -> int:
            from app.core.database import get_session_factory
            from app.models import OperationLog

            async with get_session_factory()() as session:
                log = OperationLog(
                    request_id=request_id,
                    category=category,
                    event_name=event_name,
                    level=level,
                    message=message,
                    entity_type=entity_type,
                    entity_id=entity_id,
                    event_metadata=event_metadata,
                    created_at=created_at or datetime.now(UTC),
                )
                session.add(log)
                await session.commit()
                await session.refresh(log)
                return log.id

        return asyncio.run(_seed())

    def _seed_logs(self, rows: list[dict[str, object]]) -> None:
        async def _seed() -> None:
            from app.core.database import get_session_factory
            from app.models import OperationLog

            async with get_session_factory()() as session:
                session.add_all(
                    [
                        OperationLog(
                            request_id=row.get("request_id"),
                            category=str(row.get("category", "backend")),
                            event_name=str(row.get("event_name", "operation.completed")),
                            level=str(row.get("level", "info")),
                            message=row.get("message", "operation completed"),
                            entity_type=row.get("entity_type"),
                            entity_id=row.get("entity_id"),
                            event_metadata=row.get("event_metadata"),
                            created_at=row.get("created_at", datetime.now(UTC)),
                        )
                        for row in rows
                    ],
                )
                await session.commit()

        asyncio.run(_seed())

if __name__ == "__main__":
    unittest.main()
