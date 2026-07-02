from __future__ import annotations

import asyncio
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient


STARTUP_STATE_TIMEOUT_SECONDS = 30.0
STARTUP_STATE_POLL_INTERVAL_SECONDS = 0.1


class DesktopRuntimeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        os.environ["AUTO_EMAIL_SENDER_DATA_DIR"] = str(
            Path(self.temp_dir.name) / "AutoEmailSender",
        )
        os.environ.pop("DATABASE_URL", None)

    def tearDown(self) -> None:
        from app.core.config import get_settings
        from app.core.database import dispose_engine, get_engine, get_session_factory

        if get_engine.cache_info().currsize:
            asyncio.run(dispose_engine())
        get_session_factory.cache_clear()
        get_settings.cache_clear()
        os.environ.pop("AUTO_EMAIL_SENDER_DATA_DIR", None)
        os.environ.pop("DATABASE_URL", None)
        os.environ.pop("ENABLE_BACKGROUND_WORKERS", None)
        self.temp_dir.cleanup()

    def _wait_for_startup_state(
        self,
        client: TestClient,
        expected_state: str,
        *,
        timeout_seconds: float = STARTUP_STATE_TIMEOUT_SECONDS,
    ):
        deadline = time.monotonic() + timeout_seconds
        response = client.get("/startup-status")
        data = response.json()
        while data["state"] != expected_state:
            if time.monotonic() >= deadline:
                self.fail(
                    f"Timed out waiting for startup state {expected_state!r}; "
                    f"last response: {data!r}",
                )
            time.sleep(STARTUP_STATE_POLL_INTERVAL_SECONDS)
            response = client.get("/startup-status")
            data = response.json()
        return response

    def test_health_endpoint_returns_ok(self) -> None:
        os.environ["ENABLE_BACKGROUND_WORKERS"] = "0"

        from app.core.config import get_settings
        from main import create_app

        get_settings.cache_clear()
        with TestClient(create_app()) as client:
            response = client.get("/health")

        self.assertEqual(response.status_code, 200, msg=response.text)
        self.assertEqual(response.json(), {"status": "ok"})

    def test_health_endpoint_is_available_before_runtime_initialization_finishes(self) -> None:
        os.environ["ENABLE_BACKGROUND_WORKERS"] = "1"

        from app.core.config import get_settings
        import main as main_module

        get_settings.cache_clear()
        schema_started = False

        async def slow_schema() -> None:
            nonlocal schema_started
            schema_started = True
            await asyncio.Event().wait()

        with (
            patch.object(main_module, "ensure_database_schema", slow_schema),
            patch.object(main_module.RuntimeManager, "start", new_callable=AsyncMock) as runtime_start,
        ):
            with TestClient(main_module.create_app()) as client:
                response = client.get("/health")
                ready_response = client.get("/ready")

        self.assertTrue(schema_started)
        self.assertEqual(response.status_code, 200, msg=response.text)
        self.assertEqual(response.json(), {"status": "ok"})
        self.assertEqual(ready_response.status_code, 503)
        runtime_start.assert_not_called()

    def test_startup_status_reports_database_migration_phase(self) -> None:
        os.environ["ENABLE_BACKGROUND_WORKERS"] = "1"

        from app.core.config import get_settings
        import main as main_module

        get_settings.cache_clear()
        schema_started = False

        async def slow_schema() -> None:
            nonlocal schema_started
            schema_started = True
            await asyncio.Event().wait()

        with (
            patch.object(main_module, "ensure_database_schema", slow_schema),
            patch.object(main_module.RuntimeManager, "start", new_callable=AsyncMock),
        ):
            with TestClient(main_module.create_app()) as client:
                response = client.get("/startup-status")

        self.assertTrue(schema_started)
        self.assertEqual(response.status_code, 200, msg=response.text)
        data = response.json()
        self.assertEqual(data["state"], "starting")
        self.assertEqual(data["phase"], "migrating_database")
        self.assertEqual(data["message"], "正在检查和升级本地数据")
        self.assertIsNone(data["error"])
        self.assertIsInstance(data["elapsed_seconds"], int)

    def test_startup_status_reports_ready_after_runtime_initialization(self) -> None:
        os.environ["ENABLE_BACKGROUND_WORKERS"] = "0"

        from app.core.config import get_settings
        from main import create_app

        get_settings.cache_clear()
        with TestClient(create_app()) as client:
            response = self._wait_for_startup_state(client, "ready")

        self.assertEqual(response.status_code, 200, msg=response.text)
        data = response.json()
        self.assertEqual(data["state"], "ready")
        self.assertEqual(data["phase"], "ready")
        self.assertEqual(data["message"], "系统已准备就绪")
        self.assertIsNone(data["error"])

    def test_runtime_initialization_recovers_interrupted_crawl_jobs_before_ready(self) -> None:
        os.environ["ENABLE_BACKGROUND_WORKERS"] = "0"

        from app.core.config import get_settings
        import main as main_module

        get_settings.cache_clear()

        with patch.object(
            main_module,
            "recover_interrupted_crawl_jobs",
            new_callable=AsyncMock,
        ) as recover_interrupted:
            with TestClient(main_module.create_app()) as client:
                response = self._wait_for_startup_state(client, "ready")

        self.assertEqual(response.status_code, 200, msg=response.text)
        self.assertEqual(response.json()["state"], "ready")
        recover_interrupted.assert_awaited_once()

    def test_runtime_initialization_recovers_interrupted_match_analysis_runs_before_ready(self) -> None:
        os.environ["ENABLE_BACKGROUND_WORKERS"] = "0"

        from app.core.config import get_settings
        import main as main_module

        get_settings.cache_clear()

        with patch.object(
            main_module,
            "recover_interrupted_match_analysis_runs",
            new_callable=AsyncMock,
        ) as recover_interrupted:
            with TestClient(main_module.create_app()) as client:
                response = self._wait_for_startup_state(client, "ready")

        self.assertEqual(response.status_code, 200, msg=response.text)
        self.assertEqual(response.json()["state"], "ready")
        recover_interrupted.assert_awaited_once()

    def test_startup_status_reports_error_without_http_500(self) -> None:
        os.environ["ENABLE_BACKGROUND_WORKERS"] = "1"

        from app.core.config import get_settings
        import main as main_module

        get_settings.cache_clear()

        async def failing_schema() -> None:
            raise RuntimeError("migration failed")

        with patch.object(main_module, "ensure_database_schema", failing_schema):
            with TestClient(main_module.create_app()) as client:
                response = client.get("/startup-status")
                ready_response = client.get("/ready")

        self.assertEqual(response.status_code, 200, msg=response.text)
        data = response.json()
        self.assertEqual(data["state"], "error")
        self.assertEqual(data["phase"], "error")
        self.assertEqual(data["message"], "系统准备失败")
        self.assertEqual(data["error"], "migration failed")
        self.assertEqual(ready_response.status_code, 500)

    def test_startup_status_reports_database_requires_newer_app_detail(self) -> None:
        os.environ["ENABLE_BACKGROUND_WORKERS"] = "1"

        from app.core.config import get_settings
        from app.core.schema_metadata import DatabaseRequiresNewerAppError
        import main as main_module

        get_settings.cache_clear()
        backup_dir = Path(self.temp_dir.name) / "AutoEmailSender" / "backups" / "schema"

        async def failing_schema() -> None:
            raise DatabaseRequiresNewerAppError(
                current_app_version="2.3.0",
                minimum_supported_app_version="2.4.0",
                backup_directory=backup_dir,
            )

        with patch.object(main_module, "ensure_database_schema", failing_schema):
            with TestClient(main_module.create_app()) as client:
                response = client.get("/startup-status")
                ready_response = client.get("/ready")

        self.assertEqual(response.status_code, 200, msg=response.text)
        data = response.json()
        self.assertEqual(data["state"], "error")
        self.assertEqual(data["error_detail"]["code"], "DATABASE_REQUIRES_NEWER_APP")
        self.assertEqual(data["error_detail"]["minimum_supported_app_version"], "2.4.0")
        self.assertEqual(data["error_detail"]["backup_directory"], str(backup_dir))
        self.assertEqual(ready_response.status_code, 500)
        self.assertEqual(ready_response.json()["detail"]["code"], "DATABASE_REQUIRES_NEWER_APP")
    def test_desktop_data_dir_controls_default_storage_paths(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            data_dir = Path(temp_dir) / "AutoEmailSender"
            expected_data_dir = data_dir.resolve()
            os.environ["AUTO_EMAIL_SENDER_DATA_DIR"] = str(data_dir)
            os.environ.pop("DATABASE_URL", None)

            from app.core.config import get_settings

            get_settings.cache_clear()
            settings = get_settings()

            self.assertEqual(settings.data_dir, expected_data_dir)
            self.assertEqual(settings.uploads_dir, expected_data_dir / "uploads")
            self.assertEqual(
                settings.database_url,
                f"sqlite+aiosqlite:///{(expected_data_dir / 'auto_email_sender.db').as_posix()}",
            )
            self.assertTrue(settings.uploads_dir.exists())
            self.assertTrue((expected_data_dir / "logs" / "crawler").exists())

    def test_desktop_entry_builds_uvicorn_options_from_args(self) -> None:
        from desktop_entry import build_uvicorn_options

        options = build_uvicorn_options(["--host", "127.0.0.1", "--port", "48123"])

        self.assertEqual(options["app"], "main:app")
        self.assertEqual(options["host"], "127.0.0.1")
        self.assertEqual(options["port"], 48123)
        self.assertIs(options["reload"], False)

    def test_desktop_entry_parses_self_check_without_uvicorn_options(self) -> None:
        from desktop_entry import parse_desktop_args

        args = parse_desktop_args(["--self-check"])

        self.assertTrue(args.self_check)

    def test_packaged_runtime_self_check_imports_dynamic_dependencies(self) -> None:
        from desktop_entry import run_packaged_runtime_self_check

        result = run_packaged_runtime_self_check()

        self.assertEqual(result, 0)

    def test_packaged_runtime_self_check_covers_high_risk_dynamic_dependencies(self) -> None:
        from desktop_entry import PACKAGED_RUNTIME_SELF_CHECK_MODULES

        for module_name in [
            "tiktoken",
            "tiktoken_ext.openai_public",
            "socksio",
            "langchain_openai",
            "openai",
            "httpx",
            "markitdown",
            "mammoth",
            "pdfminer",
            "pdfplumber",
            "pypdf",
            "playwright.async_api",
            "docx",
            "openpyxl",
        ]:
            self.assertIn(module_name, PACKAGED_RUNTIME_SELF_CHECK_MODULES)

    def test_dev_entry_uses_port_8010_by_default(self) -> None:
        from dev_entry import build_uvicorn_options

        options = build_uvicorn_options([])

        self.assertEqual(options["host"], "127.0.0.1")
        self.assertEqual(options["port"], 8010)
        self.assertIs(options["reload"], True)


if __name__ == "__main__":
    unittest.main()
