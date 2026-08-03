from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from auto_email_sender_cli.errors import RuntimeUnavailableError
from auto_email_sender_cli.runtime import (
    RuntimeDescriptor,
    ensure_runtime_descriptor,
    get_runtime_file_path,
    launch_desktop_app,
    load_runtime_descriptor,
    locate_desktop_executable,
)


class RuntimeTests(unittest.TestCase):
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
                        "protocol_version": "1",
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

    def test_missing_runtime_descriptor_returns_scoped_error(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "missing.json"
            with patch.dict(
                os.environ,
                {"AUTO_EMAIL_SENDER_RUNTIME_FILE": path.as_posix()},
                clear=False,
            ):
                with self.assertRaises(RuntimeUnavailableError):
                    load_runtime_descriptor()

    def test_missing_runtime_launches_desktop_and_waits_for_ready_descriptor(self) -> None:
        descriptor = _descriptor()
        with (
            patch(
                "auto_email_sender_cli.runtime.load_runtime_descriptor",
                side_effect=[RuntimeUnavailableError(), descriptor],
            ),
            patch("auto_email_sender_cli.runtime.launch_desktop_app") as launch,
            patch("auto_email_sender_cli.runtime.process_is_running", return_value=True),
            patch("auto_email_sender_cli.runtime._runtime_is_ready", return_value=True),
        ):
            resolved = ensure_runtime_descriptor(
                timeout_seconds=1,
                poll_interval_seconds=0,
            )

        self.assertEqual(resolved, descriptor)
        launch.assert_called_once_with()

    def test_running_desktop_is_not_launched_again_while_backend_becomes_ready(self) -> None:
        descriptor = _descriptor()
        with (
            patch(
                "auto_email_sender_cli.runtime.load_runtime_descriptor",
                return_value=descriptor,
            ),
            patch("auto_email_sender_cli.runtime.launch_desktop_app") as launch,
            patch("auto_email_sender_cli.runtime.process_is_running", return_value=True),
            patch(
                "auto_email_sender_cli.runtime._runtime_is_ready",
                side_effect=[False, True],
            ),
        ):
            resolved = ensure_runtime_descriptor(
                timeout_seconds=1,
                poll_interval_seconds=0,
            )

        self.assertEqual(resolved, descriptor)
        launch.assert_not_called()

    def test_environment_runtime_does_not_launch_desktop(self) -> None:
        with (
            patch.dict(
                os.environ,
                {
                    "AUTO_EMAIL_SENDER_BASE_URL": "http://127.0.0.1:9999",
                    "AUTO_EMAIL_SENDER_AGENT_TOKEN": "test-token",
                },
                clear=True,
            ),
            patch("auto_email_sender_cli.runtime.launch_desktop_app") as launch,
        ):
            descriptor = ensure_runtime_descriptor(timeout_seconds=0.01)

        self.assertEqual(descriptor.base_url, "http://127.0.0.1:9999")
        launch.assert_not_called()

    def test_desktop_executable_override_is_used_for_background_launch(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            executable = Path(temp_dir) / "Auto Email Sender"
            executable.write_text("test", encoding="utf-8")
            with (
                patch.dict(
                    os.environ,
                    {"AUTO_EMAIL_SENDER_DESKTOP_EXECUTABLE": executable.as_posix()},
                    clear=True,
                ),
                patch("auto_email_sender_cli.runtime.sys.platform", "darwin"),
                patch("auto_email_sender_cli.runtime.subprocess.Popen") as popen,
            ):
                self.assertEqual(locate_desktop_executable(), executable.resolve())
                self.assertEqual(launch_desktop_app(), executable.resolve())

            args, kwargs = popen.call_args
            self.assertEqual(args[0], [str(executable.resolve()), "--agent-background"])
            self.assertTrue(kwargs["start_new_session"])


def _descriptor(**overrides: object) -> RuntimeDescriptor:
    values: dict[str, object] = {
        "protocol_version": "1",
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
