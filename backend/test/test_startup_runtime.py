from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import main


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


class _SessionContext:
    async def __aenter__(self) -> SimpleNamespace:
        return SimpleNamespace(commit=AsyncMock())

    async def __aexit__(self, exc_type, exc, tb) -> None:  # type: ignore[no-untyped-def]
        return None


def _session_factory():
    def factory() -> _SessionContext:
        return _SessionContext()

    return factory


if __name__ == "__main__":
    unittest.main()
