from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from auto_email_sender_cli.errors import (
    RuntimeProtocolMismatchError,
    RuntimeUnavailableError,
)
from auto_email_sender_cli.runtime import (
    RuntimeDescriptor,
    RuntimeProbe,
    create_runtime_http_client,
    ensure_runtime_descriptor,
    get_runtime_file_path,
    load_runtime_descriptor,
    probe_runtime_descriptor,
    process_is_running,
)
from auto_email_sender_cli.version import get_build_identity, get_cli_version


class RuntimeTests(unittest.TestCase):
    def test_runtime_http_client_bypasses_environment_proxies_for_local_urls(
        self,
    ) -> None:
        local_urls: tuple[str | None, ...] = (
            None,
            "http://127.0.0.1:48120",
            "http://127.1.2.3:48120",
            "http://localhost:48120",
            "http://backend.localhost:48120",
            "http://[::1]:48120",
        )
        for base_url in local_urls:
            with (
                self.subTest(base_url=base_url),
                patch.dict(
                    os.environ,
                    {"HTTP_PROXY": "http://127.0.0.1:17891"},
                    clear=True,
                ),
                patch("auto_email_sender_cli.runtime.httpx.Client") as constructor,
            ):
                create_runtime_http_client(base_url=base_url, timeout=1.5)

            constructor.assert_called_once_with(timeout=1.5, trust_env=False)

    def test_runtime_http_client_preserves_proxies_for_explicit_remote_development_url(
        self,
    ) -> None:
        with patch("auto_email_sender_cli.runtime.httpx.Client") as constructor:
            create_runtime_http_client(
                base_url="https://development.example.test:8443",
                timeout=2.0,
            )

        constructor.assert_called_once_with(timeout=2.0, trust_env=True)

    def test_runtime_http_client_honors_non_loopback_environment_override(self) -> None:
        with (
            patch.dict(
                os.environ,
                {
                    "AUTO_EMAIL_SENDER_BASE_URL": "https://development.example.test:8443",
                    "AUTO_EMAIL_SENDER_AGENT_TOKEN": "development-token",
                },
                clear=True,
            ),
            patch("auto_email_sender_cli.runtime.httpx.Client") as constructor,
        ):
            create_runtime_http_client(base_url=None, timeout=2.5)

        constructor.assert_called_once_with(timeout=2.5, trust_env=True)

    def test_runtime_http_client_ignores_incomplete_remote_environment_override(
        self,
    ) -> None:
        with (
            patch.dict(
                os.environ,
                {"AUTO_EMAIL_SENDER_BASE_URL": "https://development.example.test:8443"},
                clear=True,
            ),
            patch("auto_email_sender_cli.runtime.httpx.Client") as constructor,
        ):
            create_runtime_http_client(base_url=None, timeout=2.5)

        constructor.assert_called_once_with(timeout=2.5, trust_env=False)

    def test_current_process_is_reported_running(self) -> None:
        self.assertTrue(process_is_running(os.getpid()))

    def test_exited_process_is_reported_stopped(self) -> None:
        child = subprocess.Popen([sys.executable, "-c", "pass"])
        child.wait(timeout=10)

        self.assertFalse(process_is_running(child.pid))

    def test_windows_liveness_probe_never_calls_os_kill(self) -> None:
        with (
            patch("auto_email_sender_cli.runtime.sys.platform", "win32"),
            patch(
                "auto_email_sender_cli.runtime._windows_process_is_running",
                return_value=True,
            ) as windows_probe,
            patch("auto_email_sender_cli.runtime.os.kill") as destructive_probe,
        ):
            self.assertTrue(process_is_running(12345))

        windows_probe.assert_called_once_with(12345)
        destructive_probe.assert_not_called()

    def test_build_identity_ignores_blank_explicit_revision_and_uses_embedded(
        self,
    ) -> None:
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

    def test_build_identity_falls_back_to_development_when_all_revisions_are_blank(
        self,
    ) -> None:
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

    def test_cli_version_uses_embedded_constant_without_package_metadata(self) -> None:
        with patch.dict(
            os.environ,
            {"AUTO_EMAIL_SENDER_EMBEDDED_CLI_VERSION": " 9.8.7 "},
            clear=True,
        ):
            self.assertEqual(get_cli_version(), "9.8.7")

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
            patch.dict(
                os.environ, {"APPDATA": "C:/Users/Alice/AppData/Roaming"}, clear=True
            ),
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
                        "protocol_version": "3",
                        "app_version": "2.4.1",
                        "runtime_id": "runtime-test",
                        "base_url": "http://127.0.0.1:48120",
                        "access_token": "token",
                        "desktop": {
                            "pid": os.getpid(),
                            "started_at": "2026-08-03T00:00:00Z",
                        },
                        "backend": {
                            "pid": os.getpid(),
                            "started_at": "2026-08-03T00:00:01Z",
                        },
                        "published_at": "2026-08-03T00:00:01Z",
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

    def test_runtime_file_rejects_nonlocal_or_ambiguous_base_urls(self) -> None:
        invalid_urls = (
            "https://127.0.0.1:48120",
            "http://example.test:48120",
            "http://127.0.0.1",
            "http://127.0.0.1:0",
            "http://127.0.0.1:48120/api",
            "http://127.0.0.1:48120?",
            "http://127.0.0.1:48120#",
            "http://user@127.0.0.1:48120",
            "http://127.0.0.1:99999",
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "runtime.json"
            for base_url in invalid_urls:
                with self.subTest(base_url=base_url):
                    path.write_text(
                        json.dumps(
                            {
                                "protocol_version": "3",
                                "app_version": "2.5.4",
                                "runtime_id": "runtime-test",
                                "base_url": base_url,
                                "access_token": "token",
                                "desktop": {"pid": os.getpid(), "started_at": "now"},
                                "backend": {"pid": os.getpid(), "started_at": "now"},
                                "published_at": "now",
                            },
                        ),
                        encoding="utf-8",
                    )
                    with (
                        patch.dict(
                            os.environ,
                            {"AUTO_EMAIL_SENDER_RUNTIME_FILE": path.as_posix()},
                            clear=True,
                        ),
                        self.assertRaises(RuntimeUnavailableError) as raised,
                    ):
                        load_runtime_descriptor()

                    self.assertIn("运行信息无效", raised.exception.message)

    def test_explicit_remote_environment_runtime_remains_available_for_development(
        self,
    ) -> None:
        with patch.dict(
            os.environ,
            {
                "AUTO_EMAIL_SENDER_BASE_URL": "https://development.example.test:8443/api",
                "AUTO_EMAIL_SENDER_AGENT_TOKEN": "test-token",
            },
            clear=True,
        ):
            descriptor = load_runtime_descriptor()

        self.assertEqual(
            descriptor.base_url,
            "https://development.example.test:8443/api",
        )

    def test_malformed_runtime_descriptors_fail_as_app_unavailable(self) -> None:
        cases: tuple[object, ...] = (
            "{broken",
            [],
            {"protocol_version": "3"},
            {
                "protocol_version": "3",
                "app_version": "2.4.1",
                "runtime_id": "runtime-test",
                "base_url": "http://127.0.0.1:48120",
                "access_token": "",
                "desktop": {"pid": os.getpid(), "started_at": "now"},
                "backend": {"pid": os.getpid(), "started_at": "now"},
                "published_at": "now",
            },
            {
                "protocol_version": "3",
                "app_version": "2.4.1",
                "runtime_id": "runtime-test",
                "base_url": "http://127.0.0.1:48120",
                "access_token": "token",
                "desktop": {"pid": True, "started_at": "now"},
                "backend": {"pid": os.getpid(), "started_at": "now"},
                "published_at": "now",
            },
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "runtime.json"
            for index, payload in enumerate(cases):
                with self.subTest(index=index):
                    path.write_text(
                        payload if isinstance(payload, str) else json.dumps(payload),
                        encoding="utf-8",
                    )
                    with patch.dict(
                        os.environ,
                        {"AUTO_EMAIL_SENDER_RUNTIME_FILE": path.as_posix()},
                        clear=False,
                    ):
                        with self.assertRaises(RuntimeUnavailableError) as raised:
                            load_runtime_descriptor()
                    self.assertIn("运行信息无效", raised.exception.message)

    def test_missing_runtime_descriptor_tells_user_to_open_the_desktop_app(
        self,
    ) -> None:
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

    def test_old_runtime_protocol_is_rejected_before_any_process_probe(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "runtime.json"
            path.write_text(
                json.dumps(
                    {
                        "protocol_version": "2",
                        "app_version": "2.5.3",
                        "base_url": "http://127.0.0.1:48120",
                        "access_token": "old-token",
                        "desktop_pid": os.getpid(),
                        "started_at": "now",
                    },
                ),
                encoding="utf-8",
            )
            with (
                patch.dict(
                    os.environ, {"AUTO_EMAIL_SENDER_RUNTIME_FILE": path.as_posix()}
                ),
                patch(
                    "auto_email_sender_cli.runtime.process_is_running"
                ) as process_probe,
                self.assertRaises(RuntimeProtocolMismatchError),
            ):
                load_runtime_descriptor()

        process_probe.assert_not_called()

    def test_runtime_probe_authenticates_and_matches_the_backend_instance(self) -> None:
        descriptor = _descriptor()
        response = SimpleNamespace(
            is_success=True,
            status_code=200,
            json=lambda: {
                "runtime_id": descriptor.runtime_id,
                "protocol_version": descriptor.protocol_version,
                "app_version": descriptor.app_version,
                "backend_pid": descriptor.backend_pid,
                "desktop_pid": descriptor.desktop_pid,
                "state": "ready",
            },
        )
        http_client = Mock()
        http_client.get.return_value = response
        with (
            patch(
                "auto_email_sender_cli.runtime.process_is_running", return_value=True
            ),
        ):
            probe = probe_runtime_descriptor(descriptor, http_client=http_client)

        self.assertTrue(probe.runtime_matches)
        self.assertTrue(probe.backend_ready)
        http_client.get.assert_called_once_with(
            "http://127.0.0.1:48120/api/agent/v1/runtime",
            headers={"Authorization": "Bearer agent-token"},
            timeout=1.0,
        )

    def test_runtime_probe_closes_its_direct_loopback_client(self) -> None:
        descriptor = _descriptor()
        response = SimpleNamespace(
            is_success=False,
            status_code=503,
        )
        http_client = Mock()
        http_client.get.return_value = response
        with (
            patch(
                "auto_email_sender_cli.runtime.process_is_running", return_value=True
            ),
            patch(
                "auto_email_sender_cli.runtime.create_runtime_http_client",
                return_value=http_client,
            ) as create_client,
        ):
            probe = probe_runtime_descriptor(descriptor)

        self.assertTrue(probe.backend_reachable)
        self.assertFalse(probe.backend_ready)
        create_client.assert_called_once_with(
            base_url="http://127.0.0.1:48120",
            timeout=1.0,
        )
        http_client.close.assert_called_once_with()

    def test_runtime_probe_rejects_a_reused_port_with_another_runtime_id(self) -> None:
        descriptor = _descriptor()
        response = SimpleNamespace(
            is_success=True,
            status_code=200,
            json=lambda: {
                "runtime_id": "different-runtime",
                "protocol_version": descriptor.protocol_version,
                "app_version": descriptor.app_version,
                "backend_pid": descriptor.backend_pid,
                "desktop_pid": descriptor.desktop_pid,
                "state": "ready",
            },
        )
        http_client = Mock()
        http_client.get.return_value = response
        with (
            patch(
                "auto_email_sender_cli.runtime.process_is_running", return_value=True
            ),
        ):
            probe = probe_runtime_descriptor(descriptor, http_client=http_client)

        self.assertFalse(probe.runtime_matches)
        self.assertFalse(probe.backend_ready)

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

    def test_stale_runtime_pid_is_rejected_without_network_preflight(self) -> None:
        descriptor = _descriptor(desktop_pid=999_999_999)
        with (
            patch(
                "auto_email_sender_cli.runtime.load_runtime_descriptor",
                return_value=descriptor,
            ),
            patch(
                "auto_email_sender_cli.runtime.probe_runtime_descriptor",
                return_value=_probe(
                    desktop_process_running=False, backend_process_running=False
                ),
            ),
        ):
            with self.assertRaises(RuntimeUnavailableError) as raised:
                ensure_runtime_descriptor()

        self.assertIn("桌面进程已停止", raised.exception.message)

    def test_authenticated_ready_runtime_is_returned(self) -> None:
        descriptor = _descriptor()
        with (
            patch(
                "auto_email_sender_cli.runtime.load_runtime_descriptor",
                return_value=descriptor,
            ),
            patch(
                "auto_email_sender_cli.runtime.probe_runtime_descriptor",
                return_value=_probe(),
            ),
        ):
            resolved = ensure_runtime_descriptor()

        self.assertEqual(resolved, descriptor)

    def test_running_desktop_with_old_protocol_is_rejected_before_any_command_runs(
        self,
    ) -> None:
        descriptor = _descriptor(protocol_version="1")
        with (
            patch(
                "auto_email_sender_cli.runtime.load_runtime_descriptor",
                return_value=descriptor,
            ),
            patch("auto_email_sender_cli.runtime.probe_runtime_descriptor") as probe,
        ):
            with self.assertRaises(RuntimeProtocolMismatchError) as raised:
                ensure_runtime_descriptor()

        self.assertIn("协议 3", str(raised.exception))
        probe.assert_not_called()

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
        self.assertEqual(descriptor.protocol_version, "3")


def _descriptor(**overrides: object) -> RuntimeDescriptor:
    desktop_pid = int(overrides.pop("desktop_pid", os.getpid()))
    backend_pid = int(overrides.pop("backend_pid", os.getpid()))
    values: dict[str, object] = {
        "protocol_version": "3",
        "app_version": "2.4.1",
        "runtime_id": "runtime-test",
        "base_url": "http://127.0.0.1:48120",
        "access_token": "agent-token",
        "desktop": {"pid": desktop_pid, "started_at": "2026-08-03T00:00:00Z"},
        "backend": {"pid": backend_pid, "started_at": "2026-08-03T00:00:01Z"},
        "published_at": "2026-08-03T00:00:01Z",
    }
    values.update(overrides)
    return RuntimeDescriptor.from_mapping(values)


def _probe(**overrides: object) -> RuntimeProbe:
    values: dict[str, object] = {
        "desktop_process_running": True,
        "backend_process_running": True,
        "backend_reachable": True,
        "runtime_matches": True,
        "backend_ready": True,
        "backend_state": "ready",
    }
    values.update(overrides)
    return RuntimeProbe(**values)  # type: ignore[arg-type]


if __name__ == "__main__":
    unittest.main()
