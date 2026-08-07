from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from auto_email_sender_cli.errors import RuntimeProtocolMismatchError, RuntimeUnavailableError
from auto_email_sender_cli.runtime import (
    RuntimeDescriptor,
    ensure_runtime_descriptor,
    get_runtime_file_path,
    load_runtime_descriptor,
    process_is_running,
)
from auto_email_sender_cli.version import get_build_identity


class RuntimeTests(unittest.TestCase):
    def test_current_process_is_reported_running(self) -> None:
        self.assertTrue(process_is_running(os.getpid()))

    def test_exited_process_is_reported_stopped(self) -> None:
        child = subprocess.Popen([sys.executable, "-c", "pass"])
        child.wait(timeout=10)

        self.assertFalse(process_is_running(child.pid))

    def test_build_identity_ignores_blank_explicit_revision_and_uses_embedded(self) -> None:
        with patch.dict(
            os.environ,
            {
                "AUTO_EMAIL_SENDER_BUILD_REVISION": "   ",
                "AUTO_EMAIL_SENDER_EMBEDDED_BUILD_REVISION": "  abc123  ",
                "AUTO_EMAIL_SENDER_EMBEDDED_BUILD_DIRTY": "0",
            },
            clear=True,
        ):
            self.assertEqual(
                get_build_identity(),
                {"revision": "abc123", "kind": "embedded", "dirty": False},
            )

    def test_build_identity_falls_back_to_development_when_all_revisions_are_blank(self) -> None:
        with patch.dict(
            os.environ,
            {
                "AUTO_EMAIL_SENDER_BUILD_REVISION": " ",
                "AUTO_EMAIL_SENDER_EMBEDDED_BUILD_REVISION": "\t",
            },
            clear=True,
        ):
            identity = get_build_identity()
        self.assertEqual(identity["revision"], "development")
        self.assertEqual(identity["kind"], "development")
    def test_runtime_file_uses_electron_user_data_directory_on_macos(self) -> None:
        with (
            patch.dict(os.environ, {}, clear=True),
            patch("auto_email_sender_cli.runtime.sys.platform", "darwin"),
            patch(
                "auto_email_sender_cli.runtime.Path.home",
                return_value=Path("/Users/alice"),
            ),
        ):
            self.assertEqual(
                get_runtime_file_path(),
                Path(
                    "/Users/alice/Library/Application Support/"
                    "auto-email-sender-desktop/agent/runtime.json"
                ),
            )

    def test_runtime_file_uses_electron_user_data_directory_on_windows(self) -> None:
        with (
            patch.dict(os.environ, {"APPDATA": "C:/Users/Alice/AppData/Roaming"}, clear=True),
            patch("auto_email_sender_cli.runtime.sys.platform", "win32"),
        ):
            self.assertEqual(
                get_runtime_file_path(),
                Path(
                    "C:/Users/Alice/AppData/Roaming/"
                    "auto-email-sender-desktop/agent/runtime.json"
                ),
            )

    def test_runtime_file_honors_explicit_override(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "runtime.json"
            with patch.dict(
                os.environ,
                {"AUTO_EMAIL_SENDER_RUNTIME_FILE": path.as_posix()},
                clear=False,
            ):
                self.assertEqual(get_runtime_file_path(), path.resolve())

    def test_load_runtime_descriptor_validates_payload(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "runtime.json"
            path.write_text(
                json.dumps(
                    {
                        "protocol_version": "2",
                        "app_version": "2.4.1",
                        "base_url": "http://127.0.0.1:48120",
                        "access_token": "token",
                        "desktop_pid": os.getpid(),
                        "started_at": "2026-08-03T00:00:00Z",
                    },
                ),
                encoding="utf-8",
            )
            with patch.dict(
                os.environ,
                {"AUTO_EMAIL_SENDER_RUNTIME_FILE": path.as_posix()},
                clear=False,
            ):
                descriptor = load_runtime_descriptor()
            self.assertEqual(descriptor.app_version, "2.4.1")
            self.assertEqual(descriptor.access_token, "token")

    def test_missing_runtime_descriptor_tells_user_to_open_the_desktop_app(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "missing.json"
            with patch.dict(
                os.environ,
                {"AUTO_EMAIL_SENDER_RUNTIME_FILE": path.as_posix()},
                clear=False,
            ):
                with self.assertRaises(RuntimeUnavailableError) as raised:
                    load_runtime_descriptor()
        self.assertIn("手动打开软件", str(raised.exception))

    def test_missing_runtime_requires_manual_desktop_launch(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "missing.json"
            with patch.dict(
                os.environ,
                {"AUTO_EMAIL_SENDER_RUNTIME_FILE": path.as_posix()},
                clear=False,
            ):
                with self.assertRaises(RuntimeUnavailableError) as raised:
                    ensure_runtime_descriptor()

        self.assertIn("当前未运行", str(raised.exception))
        self.assertIn("手动打开软件", str(raised.exception))

    def test_running_desktop_requires_ready_local_service(self) -> None:
        descriptor = _descriptor()
        with (
            patch(
                "auto_email_sender_cli.runtime.load_runtime_descriptor",
                return_value=descriptor,
            ),
            patch("auto_email_sender_cli.runtime.process_is_running", return_value=True),
            patch(
                "auto_email_sender_cli.runtime._runtime_is_ready",
                return_value=False,
            ),
        ):
            with self.assertRaises(RuntimeUnavailableError) as raised:
                ensure_runtime_descriptor()

        self.assertIn("正在启动", str(raised.exception))

    def test_ready_desktop_runtime_is_returned(self) -> None:
        descriptor = _descriptor()
        with (
            patch(
                "auto_email_sender_cli.runtime.load_runtime_descriptor",
                return_value=descriptor,
            ),
            patch("auto_email_sender_cli.runtime.process_is_running", return_value=True),
            patch("auto_email_sender_cli.runtime._runtime_is_ready", return_value=True),
        ):
            resolved = ensure_runtime_descriptor()

        self.assertEqual(resolved, descriptor)

    def test_running_desktop_with_old_protocol_is_rejected_before_any_command_runs(self) -> None:
        descriptor = _descriptor(protocol_version="1")
        with (
            patch(
                "auto_email_sender_cli.runtime.load_runtime_descriptor",
                return_value=descriptor,
            ),
            patch("auto_email_sender_cli.runtime.process_is_running", return_value=True),
            patch("auto_email_sender_cli.runtime._runtime_is_ready", return_value=True),
        ):
            with self.assertRaises(RuntimeProtocolMismatchError) as raised:
                ensure_runtime_descriptor()

        self.assertIn("协议 2", str(raised.exception))

    def test_environment_runtime_does_not_require_desktop_process(self) -> None:
        with (
            patch.dict(
                os.environ,
                {
                    "AUTO_EMAIL_SENDER_BASE_URL": "http://127.0.0.1:9999",
                    "AUTO_EMAIL_SENDER_AGENT_TOKEN": "test-token",
                },
                clear=True,
            ),
        ):
            descriptor = ensure_runtime_descriptor()

        self.assertEqual(descriptor.base_url, "http://127.0.0.1:9999")
        self.assertEqual(descriptor.protocol_version, "2")


def _descriptor(**overrides: object) -> RuntimeDescriptor:
    values: dict[str, object] = {
        "protocol_version": "2",
        "app_version": "2.4.1",
        "base_url": "http://127.0.0.1:48120",
        "access_token": "agent-token",
        "desktop_pid": os.getpid(),
        "started_at": "2026-08-03T00:00:00Z",
    }
    values.update(overrides)
    return RuntimeDescriptor.model_validate(values)


if __name__ == "__main__":
    unittest.main()
