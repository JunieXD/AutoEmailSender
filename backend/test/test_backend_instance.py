from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


class ProcessLivenessTests(unittest.TestCase):
    def test_current_process_is_running(self) -> None:
        from app.core.process_liveness import process_is_running

        self.assertTrue(process_is_running(os.getpid()))

    def test_exited_process_is_stopped(self) -> None:
        from app.core.process_liveness import process_is_running

        child = subprocess.Popen([sys.executable, "-c", "pass"])
        child.wait(timeout=10)

        self.assertFalse(process_is_running(child.pid))

    def test_windows_liveness_probe_never_calls_os_kill(self) -> None:
        from app.core.process_liveness import process_is_running

        with (
            patch("app.core.process_liveness.sys.platform", "win32"),
            patch(
                "app.core.process_liveness._windows_process_is_running",
                return_value=True,
            ) as windows_probe,
            patch("app.core.process_liveness.os.kill") as destructive_probe,
        ):
            self.assertTrue(process_is_running(12345))

        windows_probe.assert_called_once_with(12345)
        destructive_probe.assert_not_called()


class BackendInstanceLockTests(unittest.TestCase):
    def test_second_lock_for_same_data_dir_is_rejected_until_release(self) -> None:
        from app.core.instance_lock import (
            BackendInstanceAlreadyRunningError,
            BackendInstanceLock,
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            first = BackendInstanceLock(Path(temp_dir))
            second = BackendInstanceLock(Path(temp_dir))
            first.acquire()
            try:
                with self.assertRaises(BackendInstanceAlreadyRunningError):
                    second.acquire()
            finally:
                first.release()

            second.acquire()
            second.release()

    def test_api_and_worker_role_locks_can_coexist_but_each_role_is_unique(self) -> None:
        from app.core.instance_lock import (
            BackendInstanceLock,
            BackendWorkerAlreadyRunningError,
            BackendWorkerLock,
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            data_dir = Path(temp_dir)
            api_lock = BackendInstanceLock(data_dir)
            worker_lock = BackendWorkerLock(data_dir)
            duplicate_worker = BackendWorkerLock(data_dir)
            api_lock.acquire()
            worker_lock.acquire()
            try:
                with self.assertRaises(BackendWorkerAlreadyRunningError):
                    duplicate_worker.acquire()
            finally:
                worker_lock.release()
                api_lock.release()

    def test_migration_lock_is_independent_and_exclusive(self) -> None:
        from app.core.instance_lock import (
            DatabaseMigrationAlreadyRunningError,
            DatabaseMigrationLock,
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            data_dir = Path(temp_dir)
            first = DatabaseMigrationLock(data_dir)
            second = DatabaseMigrationLock(data_dir)
            first.acquire()
            try:
                with self.assertRaises(DatabaseMigrationAlreadyRunningError):
                    second.acquire()
            finally:
                first.release()

            second.acquire()
            second.release()


class AgentRuntimeDescriptorCleanupTests(unittest.TestCase):
    def test_cleanup_removes_only_the_owned_runtime_descriptor(self) -> None:
        from app.core.agent_runtime_descriptor import cleanup_owned_runtime_descriptor

        with tempfile.TemporaryDirectory() as temp_dir:
            data_dir = Path(temp_dir)
            runtime_path = data_dir / "agent" / "runtime.json"
            runtime_path.parent.mkdir(parents=True)
            runtime_path.write_text(
                json.dumps({"protocol_version": "3", "runtime_id": "runtime-new"}),
                encoding="utf-8",
            )

            self.assertFalse(cleanup_owned_runtime_descriptor(data_dir, "runtime-old"))
            self.assertTrue(runtime_path.is_file())
            self.assertTrue(cleanup_owned_runtime_descriptor(data_dir, "runtime-new"))
            self.assertFalse(runtime_path.exists())


class DesktopParentWatchdogTests(unittest.IsolatedAsyncioTestCase):
    async def test_watchdog_requests_graceful_exit_when_desktop_stops(self) -> None:
        from desktop_entry import watch_desktop_parent

        server = SimpleNamespace(should_exit=False)
        with patch("desktop_entry.process_is_running", return_value=False):
            await watch_desktop_parent(server, 12345, poll_seconds=0)

        self.assertTrue(server.should_exit)

    async def test_watchdog_waits_while_desktop_is_running(self) -> None:
        from desktop_entry import watch_desktop_parent

        server = SimpleNamespace(should_exit=False)
        liveness = [True, False]
        with patch("desktop_entry.process_is_running", side_effect=liveness):
            await watch_desktop_parent(server, 12345, poll_seconds=0)

        self.assertTrue(server.should_exit)


if __name__ == "__main__":
    unittest.main()
