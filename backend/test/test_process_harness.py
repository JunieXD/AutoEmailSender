from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from app.models import IdentityProfile
from app.modules.communications.transport import (
    _fetch_incremental_mailbox_messages_sync,
)
from test.process_harness import (
    BACKEND_ROOT,
    DesktopBackendProcess,
    FakeHTTPServer,
    FakeIMAPServer,
    FakeImapMessage,
    FaultController,
    TestClockController,
    _prepare_managed_process_launch,
    fetch_json,
    open_loopback_url,
    reserve_tcp_port,
    spawn_managed_process,
)


class FaultInjectionInfrastructureTests(unittest.TestCase):
    def test_windows_managed_python_launch_tracks_the_real_venv_runtime(self) -> None:
        original_env = {"EXISTING": "preserved"}
        command, environment = _prepare_managed_process_launch(
            ["C:/qa/.venv/Scripts/python.exe", "desktop_entry.py"],
            original_env,
            platform_name="nt",
            python_executable="C:/qa/.venv/Scripts/python.exe",
            base_python_executable="C:/python/python.exe",
        )

        self.assertEqual(command[0], "C:/python/python.exe")
        self.assertEqual(command[1:], ["desktop_entry.py"])
        self.assertEqual(
            environment,
            {
                "EXISTING": "preserved",
                "__PYVENV_LAUNCHER__": "C:/qa/.venv/Scripts/python.exe",
            },
        )
        self.assertEqual(original_env, {"EXISTING": "preserved"})

    def test_clock_override_requires_test_gate_and_sandboxed_file(self) -> None:
        from app.core.fault_injection import resolve_test_clock_offset_seconds

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            controller = TestClockController(root / "faults")
            controller.set_offset_seconds(7200)
            environment = controller.environment()
            with patch.dict(os.environ, environment, clear=False):
                self.assertEqual(resolve_test_clock_offset_seconds(), 7200)

            with patch.dict(
                os.environ,
                {**environment, "AUTO_EMAIL_SENDER_TEST_FAULTS": ""},
                clear=False,
            ):
                self.assertEqual(resolve_test_clock_offset_seconds(), 0)

            outside_path = root / "outside-clock.txt"
            outside_path.write_text("1", encoding="utf-8")
            with patch.dict(
                os.environ,
                {
                    **environment,
                    "AUTO_EMAIL_SENDER_TEST_CLOCK_OFFSET_FILE": str(outside_path),
                },
                clear=False,
            ):
                with self.assertRaisesRegex(RuntimeError, "inside the test fault"):
                    resolve_test_clock_offset_seconds()

    def test_fault_point_is_inert_without_explicit_test_environment(self) -> None:
        from app.core.fault_injection import wait_at_fault_point

        with patch.dict(
            os.environ,
            {
                "AUTO_EMAIL_SENDER_TEST_FAULTS": "",
                "AUTO_EMAIL_SENDER_TEST_FAULT_POINTS": "inert",
            },
        ):
            triggered = asyncio.run(wait_at_fault_point("inert"))

        self.assertFalse(triggered)

    def test_fault_controller_pauses_and_releases_a_real_process(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            controller = FaultController(root / "faults")
            env = os.environ.copy()
            env.update(controller.environment("claim_committed"))
            env["PYTHONPATH"] = os.pathsep.join(
                filter(None, [str(BACKEND_ROOT), env.get("PYTHONPATH", "")])
            )
            child = spawn_managed_process(
                [
                    sys.executable,
                    str(BACKEND_ROOT / "test" / "fixtures" / "fault_point_child.py"),
                    "claim_committed",
                ],
                cwd=BACKEND_ROOT,
                env=env,
                log_dir=root / "logs",
                name="fault-child",
            )
            try:
                reached = controller.wait_for_reached("claim_committed")
                self.assertIsNone(child.process.poll())
                controller.release(reached)
                self.assertEqual(child.wait(), 0, msg=child.read_stderr())
                self.assertIn("fault point released", child.read_stdout())
                self.assertTrue(reached.with_suffix(".completed").is_file())
            finally:
                child.stop()

    def test_crawl_loopback_override_requires_the_explicit_test_gate(self) -> None:
        from app.core.fault_injection import (
            get_test_browser_host_resolver_args,
            resolve_test_crawl_loopback_host,
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            environment = {
                "AUTO_EMAIL_SENDER_TEST_FAULTS": "enabled-for-tests-only",
                "AUTO_EMAIL_SENDER_TEST_FAULT_DIR": temp_dir,
                "AUTO_EMAIL_SENDER_TEST_CRAWL_LOOPBACK_HOSTS": (
                    "crawler.test.invalid"
                ),
            }
            with patch.dict(os.environ, environment, clear=False):
                self.assertEqual(
                    resolve_test_crawl_loopback_host("Crawler.Test.Invalid."),
                    "127.0.0.1",
                )
                self.assertIsNone(
                    resolve_test_crawl_loopback_host("unlisted.test.invalid")
                )
                self.assertEqual(
                    get_test_browser_host_resolver_args(),
                    (
                        "--host-resolver-rules="
                        "MAP crawler.test.invalid 127.0.0.1",
                    ),
                )

            with patch.dict(
                os.environ,
                {
                    **environment,
                    "AUTO_EMAIL_SENDER_TEST_FAULTS": "",
                },
                clear=False,
            ):
                self.assertIsNone(
                    resolve_test_crawl_loopback_host("crawler.test.invalid")
                )
                self.assertEqual(get_test_browser_host_resolver_args(), ())

    def test_crawl_loopback_override_rejects_non_reserved_test_hosts(self) -> None:
        from app.core.fault_injection import resolve_test_crawl_loopback_host

        with tempfile.TemporaryDirectory() as temp_dir, patch.dict(
            os.environ,
            {
                "AUTO_EMAIL_SENDER_TEST_FAULTS": "enabled-for-tests-only",
                "AUTO_EMAIL_SENDER_TEST_FAULT_DIR": temp_dir,
                "AUTO_EMAIL_SENDER_TEST_CRAWL_LOOPBACK_HOSTS": "example.edu",
            },
            clear=False,
        ):
            with self.assertRaisesRegex(RuntimeError, r"\*\.test\.invalid"):
                resolve_test_crawl_loopback_host("example.edu")


class DesktopProcessHarnessTests(unittest.TestCase):
    def test_fake_http_server_serves_mutable_pages_and_counts_requests(self) -> None:
        with FakeHTTPServer({"/profile": "<h1>first</h1>"}) as server:
            url = server.url("/profile", hostname="127.0.0.1")
            with patch.dict(
                os.environ,
                {
                    "HTTP_PROXY": "http://127.0.0.1:1",
                    "HTTPS_PROXY": "http://127.0.0.1:1",
                    "NO_PROXY": "",
                },
                clear=False,
            ):
                with open_loopback_url(url, timeout_seconds=2) as response:
                    self.assertEqual(
                        response.read().decode("utf-8"),
                        "<h1>first</h1>",
                    )
                server.set_page("/profile", "<h1>second</h1>")
                with open_loopback_url(url, timeout_seconds=2) as response:
                    self.assertEqual(
                        response.read().decode("utf-8"),
                        "<h1>second</h1>",
                    )

            self.assertEqual(server.requests, ("/profile", "/profile"))

    def test_loopback_http_helper_rejects_remote_urls(self) -> None:
        with self.assertRaisesRegex(ValueError, "not loopback"):
            open_loopback_url("https://example.com", timeout_seconds=1)

    def test_fake_imap_exercises_the_production_incremental_client(self) -> None:
        raw_message = (
            b"From: Professor <professor@example.edu>\r\n"
            b"To: Student <student@example.com>\r\n"
            b"Subject: Re: Hello\r\n"
            b"Message-ID: <reply-11@example.edu>\r\n"
            b"In-Reply-To: <sent@example.com>\r\n"
            b"References: <sent@example.com>\r\n"
            b"Date: Sun, 09 Aug 2026 12:00:00 +0000\r\n"
            b"Content-Type: text/plain; charset=utf-8\r\n"
            b"\r\n"
            b"Deterministic reply body.\r\n"
        )
        with FakeIMAPServer([FakeImapMessage(11, raw_message)]) as server:
            identity = IdentityProfile(
                name="IMAP test",
                profile_name="IMAP test",
                sender_name="Student",
                email_address="student@example.com",
                smtp_host="smtp.example.com",
                smtp_port=465,
                smtp_username="student@example.com",
                smtp_password="secret",
                imap_host="127.0.0.1",
                imap_port=server.port,
                imap_username="student@example.com",
                imap_password="secret",
            )
            with patch(
                "app.modules.communications.transport.get_settings",
                return_value=SimpleNamespace(smtp_send_timeout_seconds=5),
            ):
                max_seen_uid, messages, uidvalidity = (
                    _fetch_incremental_mailbox_messages_sync(
                        identity,
                        "INBOX",
                        10,
                        expected_uidvalidity=server.uidvalidity,
                    )
                )

        self.assertEqual(max_seen_uid, 11)
        self.assertEqual(uidvalidity, server.uidvalidity)
        self.assertEqual(len(messages), 1)
        self.assertEqual(messages[0].uid, 11)
        self.assertEqual(messages[0].message_id, "<reply-11@example.edu>")
        self.assertEqual(messages[0].body_text, "Deterministic reply body.")
        self.assertGreaterEqual(server.search_count, 1)
        self.assertGreaterEqual(server.fetch_count, 2)

    def test_real_desktop_backend_migrates_and_becomes_ready(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            data_dir = Path(temp_dir) / "含 空格 data"
            backend = DesktopBackendProcess(data_dir=data_dir)
            with backend:
                status = backend.wait_ready()
                self.assertEqual(status["state"], "ready")
                self.assertEqual(
                    fetch_json(f"{backend.base_url}/health"),
                    {"status": "ok"},
                )
                self.assertTrue((data_dir / "auto_email_sender.db").is_file())
                self.assertIsNone(backend.process.poll())

            self.assertIsNotNone(backend.process.returncode)

    def test_second_real_backend_for_same_data_dir_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            data_dir = Path(temp_dir) / "shared-data"
            first = DesktopBackendProcess(data_dir=data_dir, name="first-backend")
            second = DesktopBackendProcess(
                data_dir=data_dir,
                port=reserve_tcp_port(),
                name="second-backend",
            )
            try:
                first.start()
                first.wait_ready()
                second.start()
                deadline = time.monotonic() + 10
                while second.process.poll() is None and time.monotonic() < deadline:
                    time.sleep(0.05)
                self.assertIsNotNone(second.process.returncode)
                self.assertNotEqual(second.process.returncode, 0)
                self.assertIn("另一个 Auto Email Sender 后端", second.managed.read_stderr())
                self.assertEqual(
                    fetch_json(f"{first.base_url}/health"),
                    {"status": "ok"},
                )
            finally:
                second.stop()
                first.stop()


if __name__ == "__main__":
    unittest.main()
