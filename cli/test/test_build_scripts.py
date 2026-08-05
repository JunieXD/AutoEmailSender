from __future__ import annotations

import json
import os
import runpy
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


class CliBuildScriptTests(unittest.TestCase):
    def test_posix_build_creates_arm64_macos_one_file_cli_and_self_checks(self) -> None:
        script = _read_script("build-cli.sh")

        self.assertIn("uv run pyinstaller", script)
        self.assertIn("--onefile", script)
        self.assertIn("--target-arch arm64", script)
        self.assertIn("--copy-metadata auto-email-sender-cli", script)
        self.assertIn("generate_cli_build_identity.py", script)
        self.assertIn('--runtime-hook "$BuildIdentityHook"', script)
        self.assertIn("verify_cli_binary.py", script)
        self.assertIn('--executable "$CliExecutable"', script)

    def test_windows_build_creates_one_file_cli_and_self_checks(self) -> None:
        script = _read_script("build-cli.ps1")

        self.assertIn("uv run pyinstaller", script)
        self.assertIn("--onefile", script)
        self.assertIn("auto-email-sender.exe", script)
        self.assertIn("generate_cli_build_identity.py", script)
        self.assertIn("--runtime-hook $BuildIdentityHook", script)
        self.assertIn("verify_cli_binary.py", script)
        self.assertIn("--executable $CliExecutable", script)

    def test_frozen_binary_verifier_requires_embedded_identity_and_matching_catalog(self) -> None:
        repo_root = Path(__file__).resolve().parents[2]
        namespace = runpy.run_path((repo_root / "scripts" / "verify_cli_binary.py").as_posix())
        validate_payloads = namespace["validate_payloads"]
        revision = "a" * 40
        version = {
            "ok": True,
            "data": {
                "cli_version": "2.4.1",
                "protocol_version": "2",
                "schema_version": "3",
                "contract_version": "3",
                "catalog_version": "3",
                "build_revision": revision,
                "build_kind": "embedded",
            },
            "_meta": {"build_revision": revision, "build_kind": "embedded"},
        }
        capabilities = {
            "ok": True,
            "data": {
                "build": {"revision": revision, "kind": "embedded"},
                "scope_revision": "scope-1",
            },
            "_meta": {"build_revision": revision, "build_kind": "embedded"},
        }
        validate_payloads(version, capabilities)

        version["data"]["build_revision"] = "development"
        with self.assertRaisesRegex(RuntimeError, "embedded build revision"):
            validate_payloads(version, capabilities)

        version["data"]["build_revision"] = revision
        version["data"]["build_kind"] = "override"
        version["_meta"]["build_kind"] = "override"
        capabilities["data"]["build"]["kind"] = "override"
        capabilities["_meta"]["build_kind"] = "override"
        with self.assertRaisesRegex(RuntimeError, "unexpected frozen CLI build kind"):
            validate_payloads(version, capabilities)

    def test_generated_build_identity_hook_uses_string_environment_values(self) -> None:
        repo_root = Path(__file__).resolve().parents[2]
        generator = repo_root / "scripts" / "generate_cli_build_identity.py"
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
                        "'dirty': os.environ['AUTO_EMAIL_SENDER_EMBEDDED_BUILD_DIRTY']"
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


def _read_script(name: str) -> str:
    return (
        Path(__file__).resolve().parents[2] / "scripts" / name
    ).read_text(encoding="utf-8")


if __name__ == "__main__":
    unittest.main()
