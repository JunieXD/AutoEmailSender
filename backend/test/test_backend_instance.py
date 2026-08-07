from __future__ import annotations

import asyncio
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
