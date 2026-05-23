from __future__ import annotations

import asyncio
import os
import sqlite3
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import select
from test.migrated_database import create_migrated_sqlite_database


class OperationLogTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "operation_logs_test.db"
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
        os.environ.pop("OPERATION_LOG_RETENTION_DAYS", None)
        self.temp_dir.cleanup()

    def test_record_operation_log_uses_request_context_id(self) -> None:
        from app.core.database import get_session_factory
        from app.services.operation_logs import record_operation_log
        from main import create_app

        app = create_app()

        @app.post("/test/operation-log")
        async def create_operation_log() -> dict[str, object | None]:
            async with get_session_factory()() as session:
                log = await record_operation_log(
                    session,
                    category="api",
                    event_name="diagnostic.export_requested",
                    entity_type="diagnostic_bundle",
                    entity_id="bundle-1",
                )
                await session.commit()
                return {"id": log.id, "request_id": log.request_id}

        client = TestClient(app)
        try:
            response = client.post(
                "/test/operation-log",
                headers={"X-Request-ID": "audit.request-1"},
            )
        finally:
            client.close()

        self.assertEqual(response.status_code, 200, msg=response.text)
        self.assertEqual(response.json()["request_id"], "audit.request-1")

        connection = sqlite3.connect(self.db_path)
        try:
            row = connection.execute(
                """
                SELECT request_id, category, event_name, level, entity_type, entity_id
                FROM operation_logs
                """
            ).fetchone()
        finally:
            connection.close()

        self.assertEqual(
            row,
            (
                "audit.request-1",
                "api",
                "diagnostic.export_requested",
                "info",
                "diagnostic_bundle",
                "bundle-1",
            ),
        )

    def test_record_operation_log_sanitizes_metadata_without_committing(self) -> None:
        from app.core.database import get_session_factory
        from app.models import OperationLog
        from app.services.operation_logs import record_operation_log

        async def scenario() -> dict[str, object]:
            recursive: dict[str, object] = {"name": "root"}
            recursive["self"] = recursive

            async with get_session_factory()() as session:
                log = await record_operation_log(
                    session,
                    category="backend",
                    event_name="email.send_failed",
                    level="error",
                    message="发送失败",
                    metadata={
                        "token": "secret-token",
                        "smtpPassword": "smtp-secret",
                        "body": "private body",
                        "body_text": "private body text",
                        "bodyHtml": "<p>private body html</p>",
                        "email_body": "private email body",
                        "generated_content_text": "private generated content",
                        "content": "private content",
                        "requestBody": {"nested": "private request"},
                        "error": RuntimeError("smtp timeout"),
                        "callback": lambda: None,
                        "recursive": recursive,
                        "long_text": "x" * 5000,
                        "status": "failed",
                        "school": "School of Computing",
                    },
                    request_id="manual-request",
                )

                flushed = await session.scalar(
                    select(OperationLog).where(OperationLog.id == log.id)
                )
                self.assertIsNotNone(flushed)
                metadata = flushed.event_metadata

            connection = sqlite3.connect(self.db_path)
            try:
                persisted_count = connection.execute(
                    "SELECT COUNT(*) FROM operation_logs"
                ).fetchone()[0]
            finally:
                connection.close()

            return {"metadata": metadata, "persisted_count": persisted_count}

        result = asyncio.run(scenario())
        metadata = result["metadata"]

        self.assertEqual(result["persisted_count"], 0)
        self.assertEqual(metadata["token"], "[REDACTED]")
        self.assertEqual(metadata["smtpPassword"], "[REDACTED]")
        self.assertEqual(metadata["body"], "[REDACTED]")
        self.assertEqual(metadata["body_text"], "[REDACTED]")
        self.assertEqual(metadata["bodyHtml"], "[REDACTED]")
        self.assertEqual(metadata["email_body"], "[REDACTED]")
        self.assertEqual(metadata["generated_content_text"], "[REDACTED]")
        self.assertEqual(metadata["content"], "[REDACTED]")
        self.assertEqual(metadata["requestBody"], "[REDACTED]")
        self.assertIn("RuntimeError", metadata["error"])
        self.assertIn("function", metadata["callback"])
        self.assertEqual(metadata["recursive"]["self"], "[Circular]")
        self.assertLessEqual(len(metadata["long_text"]), 1200)
        self.assertEqual(metadata["status"], "failed")
        self.assertEqual(metadata["school"], "School of Computing")

    def test_record_operation_log_sanitizes_message(self) -> None:
        from app.core.database import get_session_factory
        from app.models import OperationLog
        from app.services.operation_logs import record_operation_log

        async def scenario() -> str | None:
            async with get_session_factory()() as session:
                log = await record_operation_log(
                    session,
                    category="backend",
                    event_name="smtp.probe_failed",
                    message=(
                        "failed url=https://api.example.test/send?token=url-secret#debug "
                        "token=message-secret Authorization: Bearer auth-secret "
                        "api_key=sk-secret smtpPassword=smtp-secret status=failed"
                    ),
                )
                flushed = await session.scalar(
                    select(OperationLog).where(OperationLog.id == log.id)
                )
                self.assertIsNotNone(flushed)
                return flushed.message

        message = asyncio.run(scenario())

        self.assertIsNotNone(message)
        assert message is not None
        self.assertIn("https://api.example.test/send", message)
        self.assertNotIn("url-secret", message)
        self.assertNotIn("#debug", message)
        self.assertNotIn("message-secret", message)
        self.assertNotIn("auth-secret", message)
        self.assertNotIn("sk-secret", message)
        self.assertNotIn("smtp-secret", message)
        self.assertIn("status=failed", message)

    def test_record_operation_log_removes_logs_older_than_retention_days(self) -> None:
        from app.core.database import get_session_factory
        from app.models import OperationLog
        from app.services.operation_logs import record_operation_log

        now = datetime(2026, 4, 26, 12, 0, tzinfo=UTC)

        async def scenario() -> list[str]:
            async with get_session_factory()() as session:
                session.add_all(
                    [
                        OperationLog(
                            category="backend",
                            event_name="old.event",
                            created_at=now - timedelta(days=31),
                        ),
                        OperationLog(
                            category="backend",
                            event_name="recent.event",
                            created_at=now - timedelta(days=5),
                        ),
                    ],
                )
                await session.commit()

            async with get_session_factory()() as session:
                await record_operation_log(
                    session,
                    category="backend",
                    event_name="new.event",
                    now=now,
                )
                await session.commit()

            async with get_session_factory()() as session:
                return list(
                    (
                        await session.execute(
                            select(OperationLog.event_name).order_by(OperationLog.event_name),
                        )
                    ).scalars(),
                )

        self.assertEqual(asyncio.run(scenario()), ["new.event", "recent.event"])


    def test_cleanup_old_operation_logs_handles_loaded_sqlite_naive_datetimes(self) -> None:
        from app.core.database import get_session_factory
        from app.models import OperationLog
        from app.services.operation_logs import cleanup_old_operation_logs

        now = datetime(2026, 4, 26, 12, 0, tzinfo=UTC)

        async def scenario() -> int:
            async with get_session_factory()() as session:
                session.add(
                    OperationLog(
                        category="backend",
                        event_name="old.event",
                        created_at=now - timedelta(days=31),
                    ),
                )
                await session.commit()

            async with get_session_factory()() as session:
                loaded = (await session.scalars(select(OperationLog))).one()
                self.assertIsNone(loaded.created_at.tzinfo)
                deleted = await cleanup_old_operation_logs(session, retention_days=30, now=now)
                await session.commit()
                return deleted

        self.assertEqual(asyncio.run(scenario()), 1)

    def test_operation_log_retention_can_be_disabled_by_env(self) -> None:
        os.environ["OPERATION_LOG_RETENTION_DAYS"] = "0"

        from app.core.config import get_settings
        from app.core.database import get_session_factory
        from app.models import OperationLog
        from app.services.operation_logs import record_operation_log

        get_settings.cache_clear()
        now = datetime(2026, 4, 26, 12, 0, tzinfo=UTC)

        async def scenario() -> list[str]:
            async with get_session_factory()() as session:
                session.add(
                    OperationLog(
                        category="backend",
                        event_name="old.event",
                        created_at=now - timedelta(days=365),
                    ),
                )
                await session.commit()

            async with get_session_factory()() as session:
                await record_operation_log(
                    session,
                    category="backend",
                    event_name="new.event",
                    now=now,
                )
                await session.commit()

            async with get_session_factory()() as session:
                return list(
                    (
                        await session.execute(
                            select(OperationLog.event_name).order_by(OperationLog.event_name),
                        )
                    ).scalars(),
                )

        self.assertEqual(asyncio.run(scenario()), ["new.event", "old.event"])

if __name__ == "__main__":
    unittest.main()
