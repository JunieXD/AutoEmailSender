from __future__ import annotations

import io
import json
import os
import runpy
import subprocess
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path
from unittest.mock import patch


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
BUILD_SCRIPTS_ROOT = REPOSITORY_ROOT / "scripts" / "build"
QUALITY_SCRIPTS_ROOT = REPOSITORY_ROOT / "scripts" / "quality"


class CliBuildScriptTests(unittest.TestCase):
    def test_agent_cli_benchmark_measures_cold_commands_and_intent_accuracy(
        self,
    ) -> None:
        namespace = runpy.run_path(
            (QUALITY_SCRIPTS_ROOT / "benchmark_agent_cli.py").as_posix(),
        )
        run_benchmark = namespace["run_benchmark"]
        intent_cases = dict(namespace["INTENT_CASES"])

        def fake_invoke(_executable: Path, arguments: list[str]):
            query = (
                arguments[arguments.index("--intent") + 1]
                if "--intent" in arguments
                else None
            )
            data = (
                {"items": [{"command": intent_cases[query]}]}
                if query is not None
                else {}
            )
            return 12.5, {"ok": True, "data": data}, 256

        with tempfile.TemporaryDirectory() as temporary_directory:
            executable = Path(temporary_directory) / "auto-email-sender"
            executable.touch()
            with patch.dict(run_benchmark.__globals__, {"_invoke": fake_invoke}):
                result = run_benchmark(executable, samples=2, warmup=1)

        measurements = result["measurements"]
        self.assertEqual(measurements["capabilities"]["p95_ms"], 12.5)
        self.assertEqual(measurements["describe"]["p95_ms"], 12.5)
        self.assertEqual(measurements["intent_routing"]["accuracy"], 1.0)
        self.assertTrue(
            all(item["correct"] for item in measurements["intent_routing"]["cases"])
        )

    def test_agent_cli_benchmark_forces_utf8_for_redirected_json(self) -> None:
        namespace = runpy.run_path(
            (QUALITY_SCRIPTS_ROOT / "benchmark_agent_cli.py").as_posix(),
        )
        main = namespace["main"]
        result = {
            "schema_version": "1",
            "measurements": {
                "capabilities": {"p95_ms": 1.0},
                "describe": {"p95_ms": 1.0},
                "intent_routing": {
                    "p95_ms": 1.0,
                    "accuracy": 1.0,
                    "cases": [{"query": "导入导师", "correct": True}],
                },
            },
        }
        raw_output = io.BytesIO()
        redirected_stdout = io.TextIOWrapper(raw_output, encoding="cp1252")

        with (
            patch.object(sys, "stdout", redirected_stdout),
            patch.object(
                sys,
                "argv",
                ["benchmark_agent_cli.py", "--executable", "auto-email-sender.exe"],
            ),
            patch.dict(
                main.__globals__,
                {"run_benchmark": lambda *_args, **_kwargs: result},
            ),
        ):
            return_code = main()
            redirected_stdout.flush()
            encoded_output = raw_output.getvalue()

        self.assertEqual(return_code, 0)
        payload = json.loads(encoded_output.decode("utf-8"))
        self.assertEqual(
            payload["measurements"]["intent_routing"]["cases"][0]["query"],
            "导入导师",
        )

    def test_frozen_binary_verifier_requires_embedded_identity_and_matching_catalog(
        self,
    ) -> None:
        namespace = runpy.run_path(
            (BUILD_SCRIPTS_ROOT / "verify_cli_binary.py").as_posix()
        )
        validate_payloads = namespace["validate_payloads"]
        revision = "a" * 40
        version = {
            "ok": True,
            "data": {
                "cli_version": "2.4.1",
                "protocol_version": "3",
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
        namespace = runpy.run_path(
            (BUILD_SCRIPTS_ROOT / "verify_cli_binary.py").as_posix()
        )
        validate_bundle_layout = namespace["validate_bundle_layout"]

        with tempfile.TemporaryDirectory() as temporary_directory:
            bundle = Path(temporary_directory) / "auto-email-sender"
            bundle.mkdir()
            executable = bundle / (
                "auto-email-sender.exe" if os.name == "nt" else "auto-email-sender"
            )
            executable.write_bytes(b"binary")
            with self.assertRaisesRegex(RuntimeError, "onedir layout"):
                validate_bundle_layout(executable)

            (bundle / "_internal").mkdir()
            validate_bundle_layout(executable)

    def test_frozen_binary_verifier_checks_every_supported_agent_manifest(self) -> None:
        namespace = runpy.run_path(
            (BUILD_SCRIPTS_ROOT / "verify_cli_binary.py").as_posix()
        )
        validate_contract = namespace["validate_agent_installation_contract"]
        observed_manifests: list[dict[str, object]] = []

        def fake_run_json(
            _executable: Path,
            command: str,
            *,
            environment_overrides: dict[str, str] | None = None,
        ) -> dict[str, object]:
            self.assertEqual(command, "doctor")
            assert environment_overrides is not None
            manifest = json.loads(
                Path(
                    environment_overrides["AUTO_EMAIL_SENDER_AGENT_MANIFEST_FILE"]
                ).read_text(encoding="utf-8"),
            )
            observed_manifests.append(manifest)
            details: dict[str, object] = {
                "state": "installed",
                "expected_sha256": manifest["cli_sha256"],
            }
            if manifest["schema_version"] == 5:
                details["hash_kind"] = "canonical_directory_v1"
                expected_binding = "windows_launcher" if os.name == "nt" else "symlink"
                details["checks"] = [
                    {
                        "id": "cli_target_binding",
                        "ok": True,
                        "binding_type": expected_binding,
                    },
                ]
            target = Path(manifest["cli_target"])
            self.assertNotEqual(target, _executable)
            if manifest["schema_version"] == 4:
                self.assertEqual(target.read_bytes(), _executable.read_bytes())
            elif os.name == "nt":
                self.assertEqual(target.suffix, ".cmd")
                self.assertIn(
                    str(_executable.resolve()), target.read_text(encoding="utf-8")
                )
            else:
                self.assertTrue(target.is_symlink())
                self.assertEqual(target.resolve(), _executable.resolve())
            return {
                "ok": True,
                "data": {
                    "checks": [
                        {
                            "id": "cli_installation",
                            "ok": True,
                            "details": details,
                        },
                    ],
                },
            }

        with tempfile.TemporaryDirectory() as temporary_directory:
            bundle = Path(temporary_directory) / "auto-email-sender"
            (bundle / "_internal").mkdir(parents=True)
            executable = bundle / (
                "auto-email-sender.exe" if os.name == "nt" else "auto-email-sender"
            )
            executable.write_bytes(b"binary")
            (bundle / "_internal" / "runtime").write_bytes(b"runtime")
            with patch.dict(
                validate_contract.__globals__, {"_run_json": fake_run_json}
            ):
                versions = validate_contract(executable)

        self.assertEqual(versions, [4, 5])
        self.assertEqual(
            [manifest["schema_version"] for manifest in observed_manifests],
            [4, 5],
        )
        self.assertNotEqual(
            observed_manifests[0]["cli_sha256"],
            observed_manifests[1]["cli_sha256"],
        )

    def test_frozen_binary_verifier_rejects_wrong_schema_v5_target_binding(
        self,
    ) -> None:
        namespace = runpy.run_path(
            (BUILD_SCRIPTS_ROOT / "verify_cli_binary.py").as_posix()
        )
        validate_check = namespace["_validate_agent_installation_check"]
        expected_binding = "windows_launcher" if os.name == "nt" else "symlink"
        doctor = {
            "data": {
                "checks": [
                    {
                        "id": "cli_installation",
                        "ok": True,
                        "details": {
                            "state": "installed",
                            "expected_sha256": "a" * 64,
                            "hash_kind": "canonical_directory_v1",
                            "checks": [
                                {
                                    "id": "cli_target_binding",
                                    "ok": True,
                                    "binding_type": "same_file",
                                },
                            ],
                        },
                    },
                ],
            },
        }

        with self.assertRaisesRegex(RuntimeError, expected_binding):
            validate_check(
                doctor,
                schema_version=5,
                expected_hash="a" * 64,
                expected_binding=expected_binding,
            )

    def test_frozen_binary_verifier_rejects_stale_cli_manifest_contract(self) -> None:
        namespace = runpy.run_path(
            (BUILD_SCRIPTS_ROOT / "verify_cli_binary.py").as_posix()
        )
        validate_contract = namespace["validate_agent_installation_contract"]

        def fake_run_json(
            _executable: Path,
            _command: str,
            *,
            environment_overrides: dict[str, str] | None = None,
        ) -> dict[str, object]:
            assert environment_overrides is not None
            manifest = json.loads(
                Path(
                    environment_overrides["AUTO_EMAIL_SENDER_AGENT_MANIFEST_FILE"]
                ).read_text(encoding="utf-8"),
            )
            schema_version = manifest["schema_version"]
            details: dict[str, object] = {
                "state": "installed",
                "expected_sha256": manifest["cli_sha256"],
            }
            if schema_version == 5:
                return {
                    "ok": True,
                    "data": {
                        "checks": [
                            {
                                "id": "cli_installation",
                                "ok": False,
                                "message": "安装清单版本过旧，无法验证 CLI 文件。",
                                "details": {"state": "needs_update"},
                            },
                        ],
                    },
                }
            return {
                "ok": True,
                "data": {
                    "checks": [
                        {
                            "id": "cli_installation",
                            "ok": True,
                            "details": details,
                        },
                    ],
                },
            }

        with tempfile.TemporaryDirectory() as temporary_directory:
            bundle = Path(temporary_directory) / "auto-email-sender"
            (bundle / "_internal").mkdir(parents=True)
            executable = bundle / (
                "auto-email-sender.exe" if os.name == "nt" else "auto-email-sender"
            )
            executable.write_bytes(b"binary")
            with (
                patch.dict(validate_contract.__globals__, {"_run_json": fake_run_json}),
                self.assertRaisesRegex(RuntimeError, "schema 5"),
            ):
                validate_contract(executable)

    def test_frozen_binary_verifier_reports_failed_process_output(self) -> None:
        namespace = runpy.run_path(
            (BUILD_SCRIPTS_ROOT / "verify_cli_binary.py").as_posix()
        )
        run_json = namespace["_run_json"]
        completed = subprocess.CompletedProcess(
            args=["auto-email-sender.exe", "--format", "json", "capabilities"],
            returncode=1,
            stdout="partial stdout\n",
            stderr="real Windows failure\n",
        )

        with patch.object(
            namespace["subprocess"], "run", return_value=completed
        ) as run_process:
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
        with (repo_root / "cli" / "pyproject.toml").open("rb") as source:
            expected_version = tomllib.load(source)["project"]["version"]
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
            self.assertEqual(embedded["version"], expected_version)




if __name__ == "__main__":
    unittest.main()
