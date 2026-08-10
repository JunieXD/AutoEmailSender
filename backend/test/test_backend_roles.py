from __future__ import annotations

import asyncio
import json
import os
import signal
import socket
import tempfile
import time
import unittest
import urllib.error
import urllib.request
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models import (
    IdentityProfile,
    LLMProfile,
    MatchAnalysisJob,
    MatchAnalysisJobItem,
    MatchAnalysisJobItemStatus,
    MatchAnalysisRun,
    Professor,
)
from test.process_harness import (
    DesktopBackendProcess,
    FaultController,
    TestClockController,
    fetch_json,
    wait_until,
)


class BackendRoleUnitTests(unittest.TestCase):
    def tearDown(self) -> None:
        from app.core.config import get_settings

        get_settings.cache_clear()
        for name in (
            "AUTO_EMAIL_SENDER_BACKEND_ROLE",
            "AUTO_EMAIL_SENDER_DATA_DIR",
            "AUTO_EMAIL_SENDER_RUNTIME_ID",
            "DATABASE_URL",
            "ENABLE_BACKGROUND_WORKERS",
        ):
            os.environ.pop(name, None)

    def test_role_parser_defaults_to_combined_and_accepts_split_roles(self) -> None:
        from desktop_entry import parse_desktop_args

        self.assertEqual(parse_desktop_args([]).role, "combined")
        self.assertEqual(parse_desktop_args(["--role", "api"]).role, "api")
        self.assertEqual(parse_desktop_args(["--role", "worker"]).role, "worker")

    def test_worker_stop_signals_include_the_native_windows_break_event(self) -> None:
        from app.services.worker_process import _worker_stop_signals

        expected = {signal.SIGINT, signal.SIGTERM}
        windows_break = getattr(signal, "SIGBREAK", None)
        if windows_break is not None:
            expected.add(windows_break)

        self.assertEqual(set(_worker_stop_signals()), expected)

    def test_api_role_never_starts_runtime_manager(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            env = {
                "AUTO_EMAIL_SENDER_BACKEND_ROLE": "api",
                "AUTO_EMAIL_SENDER_DATA_DIR": temp_dir,
                "AUTO_EMAIL_SENDER_RUNTIME_ID": "unit-api-runtime",
                "DATABASE_URL": (
                    "sqlite+aiosqlite:///"
                    f"{(Path(temp_dir) / 'unit.db').as_posix()}"
                ),
                "ENABLE_BACKGROUND_WORKERS": "1",
            }
            with patch.dict(os.environ, env):
                from app.core.config import get_settings
                import main as main_module

                get_settings.cache_clear()
                with (
                    patch.object(
                        main_module,
                        "ensure_database_schema",
                        new_callable=AsyncMock,
                    ),
                    patch.object(
                        main_module,
                        "cleanup_runtime_state",
                        new_callable=AsyncMock,
                    ),
                    patch.object(
                        main_module.RuntimeManager,
                        "start",
                        new_callable=AsyncMock,
                    ) as start_workers,
                ):
                    with TestClient(main_module.create_app()) as client:
                        self._wait_ready(client)

                start_workers.assert_not_awaited()

    @staticmethod
    def _wait_ready(client: TestClient) -> None:
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            response = client.get("/startup-status")
            if response.json()["state"] == "ready":
                return
            time.sleep(0.02)
        raise AssertionError("API role did not become ready")


class BackendRoleRealProcessTests(unittest.TestCase):
    def test_real_api_and_worker_start_as_distinct_sibling_roles(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            data_dir = Path(temp_dir) / "split-data"
            runtime_id = "real-split-runtime"
            api = DesktopBackendProcess(
                data_dir=data_dir,
                role="api",
                runtime_id=runtime_id,
                name="split-api",
            )
            worker: DesktopBackendProcess | None = None
            try:
                api.start()
                api.wait_ready()
                worker = DesktopBackendProcess(
                    data_dir=data_dir,
                    role="worker",
                    runtime_id=runtime_id,
                    api_pid=api.process.pid,
                    worker_generation="worker-generation-1",
                    name="split-worker",
                )
                worker.start()
                worker_status = worker.wait_worker_ready()

                self.assertNotEqual(api.process.pid, worker.process.pid)
                self.assertEqual(worker_status["state"], "ready")
                self.assertEqual(worker_status["protocol_version"], "2")
                self.assertIn(worker_status["health"], {"healthy", "degraded"})
                self.assertFalse(worker_status["draining"])
                self.assertIn("dispatcher", worker_status["subsystems"])
                self.assertIn("batch-drafts", worker_status["subsystems"])
                self.assertIsNone(api.process.poll())
                self.assertIsNone(worker.process.poll())
                with self.assertRaises(OSError):
                    socket.create_connection(("127.0.0.1", worker.port), timeout=0.2)
            finally:
                if worker is not None:
                    worker.stop()
                api.stop()

            self.assertFalse((data_dir / "runtime" / "api.json").exists())
            self.assertFalse((data_dir / "runtime" / "worker.json").exists())

    def test_real_worker_heartbeat_advances_while_api_remains_available(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            data_dir = Path(temp_dir) / "worker-heartbeat"
            runtime_id = "worker-heartbeat-runtime"
            api = DesktopBackendProcess(
                data_dir=data_dir,
                role="api",
                runtime_id=runtime_id,
                name="worker-heartbeat-api",
            )
            worker: DesktopBackendProcess | None = None
            try:
                api.start()
                api.wait_ready()
                worker = DesktopBackendProcess(
                    data_dir=data_dir,
                    role="worker",
                    runtime_id=runtime_id,
                    api_pid=api.process.pid,
                    worker_generation="heartbeat-generation",
                    name="worker-heartbeat-worker",
                )
                worker.start()
                initial = worker.wait_worker_ready()
                initial_heartbeat = initial["heartbeat_at"]
                status_path = data_dir / "runtime" / "worker.json"

                def read_new_heartbeat() -> dict[str, object] | None:
                    try:
                        current = json.loads(status_path.read_text(encoding="utf-8"))
                    except (FileNotFoundError, OSError, json.JSONDecodeError):
                        return None
                    if (
                        current.get("runtime_id") == runtime_id
                        and current.get("generation") == "heartbeat-generation"
                        and current.get("heartbeat_at") != initial_heartbeat
                    ):
                        return current
                    return None

                updated = wait_until(
                    read_new_heartbeat,
                    timeout_seconds=6,
                    description="Worker heartbeat update",
                )
                self.assertGreater(
                    datetime.fromisoformat(str(updated["heartbeat_at"])),
                    datetime.fromisoformat(str(initial_heartbeat)),
                )
                self.assertEqual(api.wait_ready()["state"], "ready")
                self.assertIsNone(api.process.poll())
                self.assertIsNone(worker.process.poll())
            finally:
                if worker is not None:
                    worker.stop()
                api.stop()

    def test_second_api_role_is_rejected_without_disturbing_first(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            data_dir = Path(temp_dir) / "duplicate-api"
            runtime_id = "duplicate-api-runtime"
            first = DesktopBackendProcess(
                data_dir=data_dir,
                role="api",
                runtime_id=runtime_id,
                name="first-api",
            )
            second = DesktopBackendProcess(
                data_dir=data_dir,
                role="api",
                runtime_id="different-api-runtime",
                name="second-api",
            )
            try:
                first.start()
                first.wait_ready()
                second.start()
                exit_code = second.managed.wait(timeout_seconds=10)

                self.assertNotEqual(exit_code, 0)
                self.assertIn("另一个 Auto Email Sender 后端", second.managed.read_stderr())
                self.assertIsNone(first.process.poll())
                self.assertEqual(first.wait_ready()["state"], "ready")
            finally:
                second.stop()
                first.stop()

    def test_worker_rejects_mismatched_runtime_group(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            data_dir = Path(temp_dir) / "mismatched-runtime"
            api = DesktopBackendProcess(
                data_dir=data_dir,
                role="api",
                runtime_id="api-runtime",
                name="mismatched-runtime-api",
            )
            worker: DesktopBackendProcess | None = None
            try:
                api.start()
                api.wait_ready()
                worker = DesktopBackendProcess(
                    data_dir=data_dir,
                    role="worker",
                    runtime_id="worker-runtime",
                    api_pid=api.process.pid,
                    name="mismatched-runtime-worker",
                )
                worker.start()
                exit_code = worker.managed.wait(timeout_seconds=10)

                self.assertNotEqual(exit_code, 0)
                self.assertIn("runtime id does not match", worker.managed.read_stderr())
            finally:
                if worker is not None:
                    worker.stop()
                api.stop()

            self.assertFalse((data_dir / "runtime" / "api.json").exists())
            self.assertFalse((data_dir / "runtime" / "worker.json").exists())

    def test_worker_cannot_start_or_migrate_before_api(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            data_dir = Path(temp_dir) / "worker-first"
            worker = DesktopBackendProcess(
                data_dir=data_dir,
                role="worker",
                runtime_id="worker-first-runtime",
                api_pid=os.getpid(),
                name="worker-before-api",
            )
            try:
                worker.start()
                exit_code = worker.managed.wait(timeout_seconds=10)
                self.assertNotEqual(exit_code, 0)
                self.assertIn("start the API before Worker", worker.managed.read_stderr())
                self.assertFalse((data_dir / "auto_email_sender.db").exists())
            finally:
                worker.stop()

    def test_worker_refuses_start_while_api_holds_migration_barrier(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            data_dir = root / "migration-barrier"
            runtime_id = "migration-barrier-runtime"
            controller = FaultController(root / "faults")
            api = DesktopBackendProcess(
                data_dir=data_dir,
                role="api",
                runtime_id=runtime_id,
                name="migration-barrier-api",
                extra_env=controller.environment(
                    "migration.lock_acquired",
                    process_id="migration-api",
                ),
            )
            worker: DesktopBackendProcess | None = None
            reached: Path | None = None
            try:
                api.start()
                reached = controller.wait_for_reached("migration.lock_acquired")
                self.assertEqual(
                    wait_until(
                        lambda: fetch_json(f"{api.base_url}/health"),
                        description="API startup health endpoint",
                    ),
                    {"status": "ok"},
                )
                with self.assertRaises(urllib.error.HTTPError) as business_error:
                    urllib.request.urlopen(
                        f"{api.base_url}/api/ping",
                        timeout=1,
                    )
                self.assertEqual(business_error.exception.code, 503)
                worker = DesktopBackendProcess(
                    data_dir=data_dir,
                    role="worker",
                    runtime_id=runtime_id,
                    api_pid=api.process.pid,
                    name="migration-barrier-worker",
                )
                worker.start()
                exit_code = worker.managed.wait(timeout_seconds=10)

                self.assertNotEqual(exit_code, 0)
                self.assertFalse((data_dir / "auto_email_sender.db").exists())
                controller.release(reached)
                reached = None
                self.assertEqual(api.wait_ready()["state"], "ready")
                self.assertEqual(
                    fetch_json(f"{api.base_url}/api/ping")["status"],
                    "ok",
                )
                self.assertTrue((data_dir / "auto_email_sender.db").is_file())
            finally:
                if reached is not None and reached.exists():
                    controller.release(reached)
                if worker is not None:
                    worker.stop()
                api.stop()

    def test_second_worker_role_is_rejected_without_disturbing_first(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            data_dir = Path(temp_dir) / "duplicate-worker"
            runtime_id = "duplicate-worker-runtime"
            api = DesktopBackendProcess(
                data_dir=data_dir,
                role="api",
                runtime_id=runtime_id,
                name="duplicate-worker-api",
            )
            first: DesktopBackendProcess | None = None
            second: DesktopBackendProcess | None = None
            try:
                api.start()
                api.wait_ready()
                first = DesktopBackendProcess(
                    data_dir=data_dir,
                    role="worker",
                    runtime_id=runtime_id,
                    api_pid=api.process.pid,
                    worker_generation="first-generation",
                    name="first-worker",
                )
                first.start()
                first.wait_worker_ready()
                second = DesktopBackendProcess(
                    data_dir=data_dir,
                    role="worker",
                    runtime_id=runtime_id,
                    api_pid=api.process.pid,
                    worker_generation="second-generation",
                    name="second-worker",
                )
                second.start()
                exit_code = second.managed.wait(timeout_seconds=10)

                self.assertNotEqual(exit_code, 0)
                self.assertIn("另一个 Auto Email Sender Worker", second.managed.read_stderr())
                self.assertIsNone(first.process.poll())
                status = first.wait_worker_ready()
                self.assertEqual(status["generation"], "first-generation")
            finally:
                if second is not None:
                    second.stop()
                if first is not None:
                    first.stop()
                api.stop()

    def test_worker_self_terminates_when_api_leader_stops(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            data_dir = Path(temp_dir) / "leader-watch"
            runtime_id = "leader-watch-runtime"
            api = DesktopBackendProcess(
                data_dir=data_dir,
                role="api",
                runtime_id=runtime_id,
                name="leader-watch-api",
            )
            worker: DesktopBackendProcess | None = None
            try:
                api.start()
                api.wait_ready()
                worker = DesktopBackendProcess(
                    data_dir=data_dir,
                    role="worker",
                    runtime_id=runtime_id,
                    api_pid=api.process.pid,
                    name="leader-watch-worker",
                )
                worker.start()
                worker.wait_worker_ready()
                api.stop()
                self.assertEqual(worker.managed.wait(timeout_seconds=5), 0)
            finally:
                if worker is not None:
                    worker.stop()
                api.stop()

    def test_worker_restart_after_clock_rollback_preserves_api_manual_run(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            data_dir = root / "worker-local-recovery"
            test_clock = TestClockController(root / "test-clock")
            clock_environment = test_clock.environment()
            runtime_id = "worker-local-recovery-runtime"
            api = DesktopBackendProcess(
                data_dir=data_dir,
                role="api",
                runtime_id=runtime_id,
                name="worker-local-recovery-api",
                extra_env=clock_environment,
            )
            first_worker: DesktopBackendProcess | None = None
            replacement_worker: DesktopBackendProcess | None = None
            try:
                api.start()
                api.wait_ready()
                first_worker = DesktopBackendProcess(
                    data_dir=data_dir,
                    role="worker",
                    runtime_id=runtime_id,
                    api_pid=api.process.pid,
                    worker_generation="recovery-generation-1",
                    name="worker-local-recovery-first",
                    extra_env=clock_environment,
                )
                first_worker.start()
                first_worker.wait_worker_ready()

                manual_run_id, worker_item_id = asyncio.run(
                    _seed_manual_run_and_worker_item(
                        data_dir / "auto_email_sender.db"
                    )
                )
                first_worker.stop()
                asyncio.run(
                    _move_match_worker_item_far_into_future(
                        data_dir / "auto_email_sender.db",
                        worker_item_id,
                    )
                )
                test_clock.set_offset_seconds(-365 * 24 * 60 * 60)

                replacement_worker = DesktopBackendProcess(
                    data_dir=data_dir,
                    role="worker",
                    runtime_id=runtime_id,
                    api_pid=api.process.pid,
                    worker_generation="recovery-generation-2",
                    name="worker-local-recovery-replacement",
                    extra_env=clock_environment,
                )
                replacement_worker.start()
                replacement_worker.wait_worker_ready()

                def recovered_state() -> tuple[str, str] | None:
                    state = asyncio.run(
                        _read_recovery_state(
                            data_dir / "auto_email_sender.db",
                            manual_run_id,
                            worker_item_id,
                        )
                    )
                    return (
                        state
                        if state[0] == "running"
                        and state[1] == MatchAnalysisJobItemStatus.CANCELED.value
                        else None
                    )

                manual_status, item_status = wait_until(
                    recovered_state,
                    timeout_seconds=10,
                    description="Worker-local match item recovery",
                )
                self.assertEqual(manual_status, "running")
                self.assertEqual(
                    item_status,
                    MatchAnalysisJobItemStatus.CANCELED.value,
                )
                self.assertIsNone(api.process.poll())
            finally:
                if replacement_worker is not None:
                    replacement_worker.stop()
                if first_worker is not None:
                    first_worker.stop()
                api.stop()


async def _seed_manual_run_and_worker_item(database_path: Path) -> tuple[int, int]:
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{database_path.as_posix()}"
    )
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with session_factory() as session:
            identity = IdentityProfile(
                name="恢复边界身份",
                profile_name="恢复边界身份",
                sender_name="测试用户",
                email_address="recovery-boundary@example.com",
                smtp_host="smtp.example.com",
                smtp_port=465,
                smtp_username="recovery-boundary@example.com",
                smtp_password="secret",
                default_language="zh-CN",
                outreach_generation_mode="llm",
            )
            llm_profile = LLMProfile(
                name="恢复边界模型",
                provider="openai",
                api_key="test-key",
                model_name="test-model",
            )
            manual_professor = Professor(
                name="API 手动匹配导师",
                email="manual-match@example.edu",
                university="Example University",
                school="Computer Science",
                research_direction="Reliable systems",
                recent_papers=[],
            )
            worker_professor = Professor(
                name="Worker 队列导师",
                email="worker-match@example.edu",
                university="Example University",
                school="Computer Science",
                research_direction="Distributed systems",
                recent_papers=[],
            )
            session.add_all(
                [identity, llm_profile, manual_professor, worker_professor]
            )
            await session.flush()
            manual_run = MatchAnalysisRun(
                professor_id=manual_professor.id,
                identity_id=identity.id,
                llm_profile_id=llm_profile.id,
                status="running",
                success=False,
                started_at=datetime.now(UTC),
            )
            job = MatchAnalysisJob(
                name="Worker 局部恢复任务",
                identity_id=identity.id,
                match_source_identity_id=identity.id,
                llm_profile_id=llm_profile.id,
                status="running",
                target_count=1,
                cancel_requested_at=datetime.now(UTC),
                started_at=datetime.now(UTC),
            )
            session.add_all([manual_run, job])
            await session.flush()
            item = MatchAnalysisJobItem(
                job_id=job.id,
                professor_id=worker_professor.id,
                status=MatchAnalysisJobItemStatus.RUNNING.value,
                claim_id="recovery-generation-1-claim",
                claimed_at=datetime.now(UTC),
                lease_expires_at=datetime.now(UTC) + timedelta(minutes=5),
                attempt_count=1,
                started_at=datetime.now(UTC),
            )
            session.add(item)
            await session.commit()
            return manual_run.id, item.id
    finally:
        await engine.dispose()


async def _move_match_worker_item_far_into_future(
    database_path: Path,
    item_id: int,
) -> None:
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{database_path.as_posix()}"
    )
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with session_factory() as session:
            item = await session.get(MatchAnalysisJobItem, item_id)
            assert item is not None
            item.lease_expires_at = datetime.now(UTC) + timedelta(days=365)
            await session.commit()
    finally:
        await engine.dispose()


async def _read_recovery_state(
    database_path: Path,
    manual_run_id: int,
    item_id: int,
) -> tuple[str, str]:
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{database_path.as_posix()}"
    )
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with session_factory() as session:
            manual_run = await session.get(MatchAnalysisRun, manual_run_id)
            item = await session.get(MatchAnalysisJobItem, item_id)
            assert manual_run is not None and item is not None
            return manual_run.status, item.status
    finally:
        await engine.dispose()


if __name__ == "__main__":
    unittest.main()
