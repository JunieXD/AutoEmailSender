from __future__ import annotations

import json
import os
import runpy
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
BUILD_SCRIPTS_ROOT = REPOSITORY_ROOT / "scripts" / "build"


class CliBuildScriptTests(unittest.TestCase):
    def test_posix_build_creates_arm64_macos_one_directory_cli_and_self_checks(self) -> None:
        script = _read_script("build-cli.sh")

        self.assertIn("uv run pyinstaller", script)
        self.assertIn("--onedir", script)
        self.assertNotIn("--onefile", script)
        self.assertIn("--target-arch arm64", script)
        self.assertNotIn("--copy-metadata", script)
        self.assertIn("generate_cli_build_identity.py", script)
        self.assertIn('--runtime-hook "$BuildIdentityHook"', script)
        self.assertIn("verify_cli_binary.py", script)
        self.assertIn('--executable "$CliExecutable"', script)

    def test_windows_build_creates_one_directory_cli_and_self_checks(self) -> None:
        script = _read_script("build-cli.ps1")

        self.assertIn("uv run pyinstaller", script)
        self.assertIn("--onedir", script)
        self.assertNotIn("--onefile", script)
        self.assertNotIn("--copy-metadata", script)
        self.assertIn('dist\\auto-email-sender\\auto-email-sender.exe', script)
        self.assertIn("auto-email-sender.exe", script)
        self.assertIn("generate_cli_build_identity.py", script)
        self.assertIn("--runtime-hook $BuildIdentityHook", script)
        self.assertIn("verify_cli_binary.py", script)
        self.assertIn("--executable $CliExecutable", script)

    def test_frozen_binary_verifier_requires_embedded_identity_and_matching_catalog(self) -> None:
        namespace = runpy.run_path((BUILD_SCRIPTS_ROOT / "verify_cli_binary.py").as_posix())
        validate_payloads = namespace["validate_payloads"]
        revision = "a" * 40
        version = {
            "ok": True,
            "data": {
                "cli_version": "2.4.1",
                "protocol_version": "2",
                "schema_version": "4",
                "contract_version": "4",
                "catalog_version": "4",
                "build_revision": revision,
                "build_kind": "embedded",
            },
            "_meta": {"schema_version": "4", "command": "version"},
        }
        capabilities = {
            "ok": True,
            "data": {
                "build": {"revision": revision, "kind": "embedded"},
                "scope_revision": "scope-1",
            },
            "_meta": {"schema_version": "4", "command": "capabilities"},
        }
        validate_payloads(version, capabilities)

        version["data"]["build_revision"] = "development"
        with self.assertRaisesRegex(RuntimeError, "embedded build revision"):
            validate_payloads(version, capabilities)

        version["data"]["build_revision"] = revision
        version["data"]["build_kind"] = "override"
        capabilities["data"]["build"]["kind"] = "override"
        with self.assertRaisesRegex(RuntimeError, "unexpected frozen CLI build kind"):
            validate_payloads(version, capabilities)

    def test_frozen_binary_verifier_requires_complete_onedir_layout(self) -> None:
        namespace = runpy.run_path((BUILD_SCRIPTS_ROOT / "verify_cli_binary.py").as_posix())
        validate_bundle_layout = namespace["validate_bundle_layout"]

        with tempfile.TemporaryDirectory() as temporary_directory:
            bundle = Path(temporary_directory) / "auto-email-sender"
            bundle.mkdir()
            executable = bundle / ("auto-email-sender.exe" if os.name == "nt" else "auto-email-sender")
            executable.write_bytes(b"binary")
            with self.assertRaisesRegex(RuntimeError, "onedir layout"):
                validate_bundle_layout(executable)

            (bundle / "_internal").mkdir()
            validate_bundle_layout(executable)

    def test_frozen_binary_verifier_reports_failed_process_output(self) -> None:
        namespace = runpy.run_path((BUILD_SCRIPTS_ROOT / "verify_cli_binary.py").as_posix())
        run_json = namespace["_run_json"]
        completed = subprocess.CompletedProcess(
            args=["auto-email-sender.exe", "--format", "json", "capabilities"],
            returncode=1,
            stdout="partial stdout\n",
            stderr="real Windows failure\n",
        )

        with patch.object(namespace["subprocess"], "run", return_value=completed) as run_process:
            with self.assertRaises(RuntimeError) as raised:
                run_json(Path("auto-email-sender.exe"), "capabilities")

        message = str(raised.exception)
        self.assertIn("exit code 1", message)
        self.assertIn("partial stdout", message)
        self.assertIn("real Windows failure", message)
        self.assertIn("stderr:", message)
        self.assertFalse(run_process.call_args.kwargs["check"])

    def test_cli_entrypoint_forces_utf8_for_redirected_machine_output(self) -> None:
        environment = os.environ.copy()
        environment["PYTHONIOENCODING"] = "cp1252"

        completed = subprocess.run(
            [
                sys.executable,
                "-m",
                "auto_email_sender_cli",
                "--format",
                "json",
                "capabilities",
            ],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=environment,
        )

        self.assertEqual(completed.returncode, 0, msg=completed.stderr)
        payload = json.loads(completed.stdout)
        summaries = [item["summary"] for item in payload["data"]["items"]]
        self.assertTrue(any("导师" in summary for summary in summaries))

    def test_generated_build_identity_hook_uses_string_environment_values(self) -> None:
        repo_root = REPOSITORY_ROOT
        generator = BUILD_SCRIPTS_ROOT / "generate_cli_build_identity.py"
        revision = "a" * 40
        environment = os.environ.copy()
        environment["AUTO_EMAIL_SENDER_BUILD_REVISION"] = revision

        with tempfile.TemporaryDirectory() as temporary_directory:
            hook = Path(temporary_directory) / "cli_build_identity_hook.py"
            generated = subprocess.run(
                [
                    sys.executable,
                    generator.as_posix(),
                    "--repo-root",
                    repo_root.as_posix(),
                    "--output",
                    hook.as_posix(),
                ],
                check=True,
                capture_output=True,
                text=True,
                env=environment,
            )
            metadata = json.loads(generated.stdout)
            self.assertEqual(metadata["revision"], revision)

            probe = subprocess.run(
                [
                    sys.executable,
                    "-c",
                    (
                        "import json, os, runpy, sys; "
                        "runpy.run_path(sys.argv[1]); "
                        "print(json.dumps({"
                        "'revision': os.environ['AUTO_EMAIL_SENDER_EMBEDDED_BUILD_REVISION'], "
                        "'dirty': os.environ['AUTO_EMAIL_SENDER_EMBEDDED_BUILD_DIRTY'], "
                        "'version': os.environ['AUTO_EMAIL_SENDER_EMBEDDED_CLI_VERSION']"
                        "}))"
                    ),
                    hook.as_posix(),
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            embedded = json.loads(probe.stdout)
            self.assertEqual(embedded["revision"], revision)
            self.assertIn(embedded["dirty"], {"0", "1"})
            self.assertEqual(embedded["version"], "2.4.1")


def _read_script(name: str) -> str:
    return (
        BUILD_SCRIPTS_ROOT / name
    ).read_text(encoding="utf-8")


if __name__ == "__main__":
    unittest.main()
