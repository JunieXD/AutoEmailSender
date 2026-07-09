from __future__ import annotations

import asyncio
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import main
from app.models import EmailTask, EmailTaskSource, EmailTaskStatus, IdentityProfile, LLMProfile, Professor
from test.schema_database import create_schema_sqlite_database


class StartupRuntimeTest(unittest.TestCase):
    def test_startup_phase_log_writes_without_full_settings(self) -> None:
        from app.core.startup_logging import write_startup_phase_log

        with tempfile.TemporaryDirectory() as temp_dir:
            with patch.dict("os.environ", {"AUTO_EMAIL_SENDER_DATA_DIR": temp_dir}):
                write_startup_phase_log("desktop_entry.start", detail="port=48120")

            log_text = (Path(temp_dir) / "logs" / "startup.log").read_text(encoding="utf-8")

        self.assertIn("desktop_entry.start", log_text)
        self.assertIn("port=48120", log_text)

    def test_initialize_runtime_retries_transient_database_lock(self) -> None:
        attempts = 0

        async def flaky_schema_check() -> None:
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise RuntimeError("database is locked")

        async def run_test() -> None:
            app = SimpleNamespace(state=SimpleNamespace())
            with tempfile.TemporaryDirectory() as temp_dir:
                with (
                    patch.object(main, "ensure_database_schema", flaky_schema_check),
                    patch.object(main, "cleanup_old_operation_logs", AsyncMock()),
                    patch.object(main, "recover_interrupted_crawl_jobs", AsyncMock()),
                    patch.object(main, "recover_interrupted_match_analysis_runs", AsyncMock()),
                    patch.object(main, "recover_interrupted_workspace_draft_rewrites", AsyncMock()),
                    patch.object(main, "recover_stale_generating_drafts", AsyncMock()),
                    patch.object(main, "get_session_factory", return_value=_session_factory()),
                    patch.object(main, "get_settings", return_value=SimpleNamespace(enable_background_workers=False, data_dir=Path(temp_dir))),
                    patch.object(main.asyncio, "sleep", AsyncMock()),
                ):
                    main.initialize_startup_status(app)  # type: ignore[arg-type]
                    await main.initialize_runtime(app)  # type: ignore[arg-type]

                log_text = (Path(temp_dir) / "logs" / "startup.log").read_text(encoding="utf-8")

            self.assertEqual(attempts, 2)
            self.assertTrue(app.state.runtime_ready)
            self.assertEqual(app.state.startup_status.state, "ready")
            self.assertIn("启动步骤遇到 SQLite 数据库锁", log_text)
            self.assertIn("migrating_database", log_text)

        asyncio.run(run_test())

    def test_cleanup_runtime_state_recovers_generating_drafts_immediately(self) -> None:
        async def run_test() -> None:
            session_factory = _session_factory()
            cleanup_logs = AsyncMock()
            recover_workspace_rewrites = AsyncMock(return_value=1)
            recover_generating_drafts = AsyncMock(return_value=1)
            with (
                patch.object(main, "cleanup_old_operation_logs", cleanup_logs),
                patch.object(main, "recover_interrupted_crawl_jobs", AsyncMock()),
                patch.object(main, "recover_interrupted_match_analysis_runs", AsyncMock()),
                patch.object(main, "recover_interrupted_workspace_draft_rewrites", recover_workspace_rewrites),
                patch.object(main, "recover_stale_generating_drafts", recover_generating_drafts),
                patch.object(main, "get_session_factory", return_value=session_factory),
            ):
                await main.cleanup_runtime_state()

            cleanup_logs.assert_awaited_once()
            recover_workspace_rewrites.assert_awaited_once_with(session_factory)
            recover_generating_drafts.assert_awaited_once_with(
                session_factory,
                stale_after=timedelta(seconds=0),
            )

        asyncio.run(run_test())

    def test_cleanup_runtime_state_restores_stuck_manual_generating_draft(self) -> None:
        async def run_test() -> None:
            with tempfile.TemporaryDirectory() as temp_dir:
                db_path = Path(temp_dir) / "startup-generating-draft.db"
                create_schema_sqlite_database(db_path)
                engine = create_async_engine(f"sqlite+aiosqlite:///{db_path.as_posix()}")
                session_factory = async_sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
                try:
                    task_id = await _create_manual_generating_draft_task(session_factory)
                    with (
                        patch.object(main, "cleanup_old_operation_logs", AsyncMock()),
                        patch.object(main, "recover_interrupted_crawl_jobs", AsyncMock()),
                        patch.object(main, "recover_interrupted_match_analysis_runs", AsyncMock()),
                        patch.object(main, "recover_interrupted_workspace_draft_rewrites", AsyncMock()),
                        patch.object(main, "get_session_factory", return_value=session_factory),
                    ):
                        await main.cleanup_runtime_state()

                    async with session_factory() as session:
                        task = await session.get(EmailTask, task_id)
                        assert task is not None
                        self.assertEqual(task.status, EmailTaskStatus.MATCHED.value)
                        self.assertIsNone(task.draft_generation_previous_status)
                finally:
                    await engine.dispose()

        asyncio.run(run_test())

    def test_initialize_runtime_logs_startup_failure_detail(self) -> None:
        async def fail_schema_check() -> None:
            raise ValueError("broken migration")

        async def run_test() -> None:
            app = SimpleNamespace(state=SimpleNamespace())
            with tempfile.TemporaryDirectory() as temp_dir:
                with (
                    patch.object(main, "ensure_database_schema", fail_schema_check),
                    patch.object(main, "get_settings", return_value=SimpleNamespace(enable_background_workers=False, data_dir=Path(temp_dir))),
                ):
                    main.initialize_startup_status(app)  # type: ignore[arg-type]
                    with self.assertRaises(ValueError):
                        await main.initialize_runtime(app)  # type: ignore[arg-type]

                log_text = (Path(temp_dir) / "logs" / "startup.log").read_text(encoding="utf-8")

            self.assertEqual(app.state.startup_status.state, "error")
            self.assertIn("桌面后端启动初始化失败", log_text)
            self.assertIn("broken migration", log_text)
            self.assertIn("Traceback", log_text)

        asyncio.run(run_test())

    def test_initialize_runtime_handles_exception_with_broken_string(self) -> None:
        class BadStringError(Exception):
            def __str__(self) -> str:
                raise RuntimeError("broken __str__")

        async def fail_schema_check() -> None:
            raise BadStringError()

        async def run_test() -> None:
            app = SimpleNamespace(state=SimpleNamespace())
            with tempfile.TemporaryDirectory() as temp_dir:
                with (
                    patch.object(main, "ensure_database_schema", fail_schema_check),
                    patch.object(main, "get_settings", return_value=SimpleNamespace(enable_background_workers=False, data_dir=Path(temp_dir))),
                ):
                    main.initialize_startup_status(app)  # type: ignore[arg-type]
                    with self.assertRaises(BadStringError):
                        await main.initialize_runtime(app)  # type: ignore[arg-type]

                log_text = (Path(temp_dir) / "logs" / "startup.log").read_text(encoding="utf-8")

            self.assertEqual(app.state.startup_status.state, "error")
            self.assertIn("BadStringError", app.state.runtime_error)
            self.assertIn("raised while formatting exception", app.state.runtime_error)
            self.assertIn("BadStringError", app.state.startup_status.error)
            self.assertIn("raised while formatting exception", app.state.startup_status.error)
            self.assertIn("桌面后端启动初始化失败", log_text)
            self.assertIn("BadStringError", log_text)
            self.assertIn("raised while formatting exception", log_text)

        asyncio.run(run_test())


class _SessionContext:
    async def __aenter__(self) -> SimpleNamespace:
        return SimpleNamespace(commit=AsyncMock())

    async def __aexit__(self, exc_type, exc, tb) -> None:  # type: ignore[no-untyped-def]
        return None


def _session_factory():
    def factory() -> _SessionContext:
        return _SessionContext()

    return factory


async def _create_manual_generating_draft_task(session_factory) -> int:
    async with session_factory() as session:
        identity = IdentityProfile(
            name="启动恢复身份",
            profile_name="启动恢复身份",
            sender_name="王同学",
            email_address=f"startup-{datetime.now(UTC).timestamp()}@example.com",
            smtp_host="smtp.example.com",
            smtp_port=465,
            smtp_username="sender@example.com",
            smtp_password="secret",
            default_language="zh-CN",
            outreach_generation_mode="llm",
            outreach_template_subject="申请与{{name}}老师交流",
            outreach_template_body_text="老师您好，我是{{sender_name}}。",
            is_default=True,
        )
        llm_profile = LLMProfile(
            name=f"启动恢复模型-{datetime.now(UTC).timestamp()}",
            provider="openai",
            api_base_url="https://api.example.com/v1",
            api_key="sk-test-key",
            model_name="gpt-test",
            is_default=True,
        )
        professor = Professor(
            name="启动恢复导师",
            email=f"startup-professor-{datetime.now(UTC).timestamp()}@example.edu",
            title="Professor",
            university="Example University",
            school="School of AI",
            department="Computer Science",
            research_direction="Large language models",
            recent_papers=[],
        )
        task = EmailTask(
            source=EmailTaskSource.MANUAL.value,
            identity=identity,
            llm_profile=llm_profile,
            professor=professor,
            status=EmailTaskStatus.GENERATING_DRAFT.value,
            draft_generation_previous_status=EmailTaskStatus.MATCHED.value,
            updated_at=datetime.now(UTC),
        )
        session.add(task)
        await session.commit()
        return task.id


if __name__ == "__main__":
    unittest.main()
