from __future__ import annotations

import importlib.util
import json
import shutil
import sqlite3
import sys
import tempfile
import uuid
import zipfile
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest import mock


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
RUNNER_PATH = REPOSITORY_ROOT / "scripts" / "quality" / "packaged-runtime-qa.py"
SEED_RUNNER_PATH = (
    REPOSITORY_ROOT / "scripts" / "quality" / "seed-previous-packaged-upgrade.py"
)


def _load_runner(path: Path, module_name: str):
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


runner = _load_runner(RUNNER_PATH, "packaged_runtime_qa")
seed_runner = _load_runner(SEED_RUNNER_PATH, "previous_packaged_upgrade_seed")


class PackagedRuntimeQaContractTests(unittest.TestCase):
    def test_evidence_recorder_check_records_named_trace_event(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = runner._create_paths(Path(temp_dir))
            report: dict[str, object] = {"checks": []}
            recorder = runner.EvidenceRecorder(paths, report)

            recorder.check("candidate_digest", passed=True)

            trace = json.loads(paths.trace_path.read_text(encoding="utf-8"))
            self.assertEqual(trace["event"], "check")
            self.assertEqual(trace["details"]["name"], "candidate_digest")
            self.assertTrue(trace["details"]["passed"])

    def test_packaged_diagnostics_export_is_bounded_and_rejects_runtime_token(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            export_path = Path(temp_dir) / "diagnostics.zip"

            def write_export(extra: bytes = b"") -> None:
                with zipfile.ZipFile(export_path, "w") as archive:
                    archive.writestr(
                        "manifest.json",
                        json.dumps({"report_id": "qa-report"}),
                    )
                    archive.writestr("summary.json", "{}")
                    archive.writestr("checksums.sha256", "abc  summary.json\n")
                    archive.writestr("README.txt", b"local diagnostics" + extra)

            write_export()
            evidence = runner._verify_packaged_diagnostics_export(
                export_path,
                forbidden_values=("runtime-secret",),
            )
            self.assertEqual(evidence["report_id"], "qa-report")
            self.assertEqual(evidence["entry_count"], 4)

            write_export(b" runtime-secret")
            with self.assertRaisesRegex(runner.QaFailure, "credential"):
                runner._verify_packaged_diagnostics_export(
                    export_path,
                    forbidden_values=("runtime-secret",),
                )

    def test_previous_artifact_hash_uses_windows_extended_length_paths(self) -> None:
        with mock.patch.object(seed_runner.sys, "platform", "win32"):
            drive_path = seed_runner._extended_length_path(
                Path(r"C:\release evidence\previous app")
            )
            unc_path = seed_runner._extended_length_path(
                Path(r"\\server\release evidence\previous app")
            )
            already_extended = seed_runner._extended_length_path(
                Path(r"\\?\C:\release evidence\previous app")
            )

        self.assertEqual(
            str(drive_path),
            r"\\?\C:\release evidence\previous app",
        )
        self.assertEqual(
            str(unc_path),
            r"\\?\UNC\server\release evidence\previous app",
        )
        self.assertEqual(
            str(already_extended),
            r"\\?\C:\release evidence\previous app",
        )

    def test_packaged_artifact_hash_uses_windows_extended_length_paths(self) -> None:
        with mock.patch.object(runner.sys, "platform", "win32"):
            drive_path = runner._extended_length_path(
                Path(r"C:\release evidence\current app")
            )
            unc_path = runner._extended_length_path(
                Path(r"\\server\release evidence\current app")
            )
            already_extended = runner._extended_length_path(
                Path(r"\\?\C:\release evidence\current app")
            )

        self.assertEqual(str(drive_path), r"\\?\C:\release evidence\current app")
        self.assertEqual(
            str(unc_path),
            r"\\?\UNC\server\release evidence\current app",
        )
        self.assertEqual(
            str(already_extended),
            r"\\?\C:\release evidence\current app",
        )

    @unittest.skipUnless(sys.platform == "win32", "requires Windows long paths")
    def test_packaged_artifact_hash_reads_a_real_extended_length_tree(self) -> None:
        temp_root = Path(tempfile.mkdtemp())
        try:
            root = temp_root / "artifact"
            deep = root
            for index in range(4):
                deep /= f"segment-{index}-" + ("x" * 60)
            file_path = deep / "manifest.json"
            extended_file = runner._extended_length_path(file_path)
            extended_file.parent.mkdir(parents=True)
            extended_file.write_text('{"version": 1}', encoding="utf-8")
            self.assertGreater(len(str(file_path)), 260)

            first = runner._sha256_tree(root)
            second = runner._sha256_tree(root)
            self.assertEqual(first, second)
            self.assertEqual(first["file_count"], 1)

            extended_file.write_text('{"version": 2}', encoding="utf-8")
            self.assertNotEqual(first["sha256"], runner._sha256_tree(root)["sha256"])
        finally:
            shutil.rmtree(runner._extended_length_path(temp_root))

    @unittest.skipUnless(sys.platform == "win32", "requires Windows long paths")
    def test_atomic_json_write_supports_a_real_extended_length_path(self) -> None:
        temp_root = Path(tempfile.mkdtemp())
        try:
            target = (
                temp_root
                / ("a" * 80)
                / ("b" * 80)
                / ("c" * 80)
                / "report.json"
            )
            self.assertGreater(len(str(target)), 260)

            runner._write_json_atomic(target, {"status": "ok"})

            extended_target = runner._extended_length_path(target)
            self.assertEqual(
                json.loads(extended_target.read_text(encoding="utf-8")),
                {"status": "ok"},
            )
            self.assertEqual(list(extended_target.parent.glob(".tmp-*")), [])
        finally:
            shutil.rmtree(runner._extended_length_path(temp_root))

    def test_previous_artifact_identity_is_captured_before_app_launch(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            artifact_root = root / "previous-app"
            artifact_root.mkdir()
            app_executable = artifact_root / "Auto Email Sender.exe"
            app_executable.write_bytes(b"previous app")
            package_file = root / "previous-installer.exe"
            package_file.write_bytes(b"previous installer")
            user_data = root / seed_runner.QA_PATH_MARKER / "seed-user-data"
            manifest = root / "evidence" / "manifest.json"
            args = SimpleNamespace(
                app_executable=app_executable,
                artifact_root=artifact_root,
                package_file=package_file,
                user_data=user_data,
                manifest=manifest,
                timeout_seconds=1,
            )
            events: list[str] = []

            def capture_identity(_args: object) -> dict[str, str]:
                events.append("artifact-identity")
                return {
                    "previous_artifact_sha256": "a" * 64,
                    "previous_executable_sha256": "b" * 64,
                    "previous_package_sha256": "c" * 64,
                }

            def launch_previous_app(*_args: object, **_kwargs: object) -> object:
                events.append("app-launch")
                raise RuntimeError("stop after launch ordering check")

            with (
                mock.patch.object(seed_runner, "parse_args", return_value=args),
                mock.patch.object(
                    seed_runner,
                    "_capture_previous_artifact_identity",
                    side_effect=capture_identity,
                ),
                mock.patch.object(
                    seed_runner.subprocess,
                    "Popen",
                    side_effect=launch_previous_app,
                ),
                self.assertRaisesRegex(RuntimeError, "launch ordering check"),
            ):
                seed_runner.main([])

            self.assertEqual(events, ["artifact-identity", "app-launch"])

    def test_previous_settings_update_round_trips_v2_5_4_required_fields(self) -> None:
        required_fields = {
            "match_analysis_job_worker_count",
            "match_analysis_job_item_concurrency",
            "match_analysis_job_interval_seconds",
            "crawler_worker_count",
            "crawler_profile_enrichment_concurrency",
            "crawler_host_concurrency",
            "draft_max_tokens",
            "batch_draft_generation_concurrency",
            "draft_rewrite_intensity",
            "draft_rewrite_tone",
            "draft_rewrite_formality",
            "draft_rewrite_length",
            "draft_rewrite_specificity",
            "draft_template_preservation",
        }
        settings = {
            "revision": "previous-revision",
            "match_analysis_job_worker_count": 1,
            "match_analysis_job_item_concurrency": 2,
            "match_analysis_job_interval_seconds": 3,
            "crawler_worker_count": 4,
            "crawler_profile_enrichment_concurrency": 5,
            "crawler_host_concurrency": 6,
            "draft_max_tokens": 4096,
            "batch_draft_generation_concurrency": 7,
            "draft_rewrite_intensity": "moderate",
            "draft_rewrite_tone": "professional",
            "draft_rewrite_formality": "balanced",
            "draft_rewrite_length": "default",
            "draft_rewrite_specificity": "balanced",
            "draft_template_preservation": "structure_first",
            "draft_custom_instruction": "previous value",
            "intended_research_direction": "reliable systems",
            "updated_at": "2026-08-11T00:00:00Z",
        }

        payload = seed_runner._build_settings_update_payload(
            settings,
            "packaged-upgrade:test",
        )

        self.assertEqual(len(required_fields), 14)
        self.assertTrue(required_fields.issubset(payload))
        self.assertEqual(payload["draft_custom_instruction"], "packaged-upgrade:test")
        self.assertEqual(payload["intended_research_direction"], "reliable systems")
        self.assertNotIn("revision", payload)
        self.assertNotIn("updated_at", payload)
        self.assertEqual(set(payload), set(settings) - {"revision", "updated_at"})

    def test_imap_terminal_state_waits_for_identity_claim_release(self) -> None:
        harness = runner.WorkloadHarness.__new__(runner.WorkloadHarness)
        state = {
            "email_log_count": 1,
            "distinct_imap_location_count": 1,
            "identity_claim_id": "still-owned",
            "incremental_cursor": 11,
            "history_status": None,
            "history_cursor": None,
            "history_claim_id": None,
            "history_lease_expires_at": None,
        }
        harness.database_path = Path("ignored.db")
        harness.support = mock.Mock()
        harness.support.imap_tests._read_state.return_value = state
        cycle = mock.Mock(
            imap_incremental_identity_id=1,
            imap_incremental_professor_id=2,
            imap_history_identity_id=3,
            imap_history_professor_id=4,
        )

        self.assertIsNone(
            harness._imap_terminal_state(cycle, workload="incremental")
        )
        state["identity_claim_id"] = None
        self.assertIs(
            harness._imap_terminal_state(cycle, workload="incremental"),
            state,
        )

    def test_workload_harness_completes_all_six_real_worker_workloads(self) -> None:
        runner._load_workload_support()
        process_harness = sys.modules["test.process_harness"]
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = runner._create_paths(Path(temp_dir))
            report: dict[str, object] = {"checks": []}
            recorder = runner.EvidenceRecorder(paths, report)
            runtime_id = f"packaged-workload-{uuid.uuid4()}"
            environment = runner._qa_backend_environment(paths)
            api = process_harness.DesktopBackendProcess(
                data_dir=paths.user_data,
                role="api",
                runtime_id=runtime_id,
                extra_env=environment,
                name="packaged-workload-api",
            )
            worker = None
            workloads = None
            try:
                api.start()
                api.wait_ready()
                worker = process_harness.DesktopBackendProcess(
                    data_dir=paths.user_data,
                    role="worker",
                    runtime_id=runtime_id,
                    api_pid=api.process.pid,
                    extra_env=environment,
                    name="packaged-workload-worker",
                ).start()
                worker.wait_worker_ready()
                workloads = runner.WorkloadHarness(paths, recorder)

                evidence = workloads.run_cycle(
                    index=0,
                    chaos=False,
                    network_flap=False,
                )
                network_evidence = workloads.run_cycle(
                    index=1,
                    chaos=True,
                    network_flap=True,
                )

                self.assertEqual(evidence["delivery_outcome"], "smtp_accepted")
                self.assertEqual(evidence["smtp_data_delta"], 1)
                self.assertGreaterEqual(evidence["llm_request_delta"], 3)
                self.assertGreaterEqual(evidence["imap_fetches"], 2)
                self.assertTrue(network_evidence["network_flap"])
                self.assertGreaterEqual(network_evidence["crawler_failure_count"], 1)
                self.assertEqual(workloads.summary["cycles_completed"], 2)
                self.assertEqual(workloads.summary["network_flaps_observed"], 1)
                self.assertEqual(
                    set(workloads.summary["workloads_completed"]),
                    set(runner.WorkloadHarness.REQUIRED_WORKLOADS),
                )
                material = seed_runner._seed_identity_material(
                    paths.user_data / runner.DATABASE_NAME,
                    paths.user_data,
                )
                material_path = paths.user_data / material["relative_path"]
                self.assertEqual(seed_runner._sha256_file(material_path), material["sha256"])
                self.assertTrue(runner.audit_database(paths.user_data / runner.DATABASE_NAME).passed)
            finally:
                if worker is not None:
                    worker.stop()
                api.stop()
                if workloads is not None:
                    workloads.close()

    def test_creates_a_canonical_non_ascii_isolated_user_data_path(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = runner._create_paths(Path(temp_dir))
            runner._authorize_user_data(paths.user_data, "qa_nonce_1234567890")

            self.assertIn(runner.QA_PATH_MARKER, paths.user_data.parts)
            self.assertIn("用户 数据 Ω", paths.user_data.parts)
            sentinel = json.loads(
                (paths.user_data / runner.QA_SENTINEL_NAME).read_text(encoding="utf-8")
            )
            self.assertEqual(sentinel["protocol_version"], "1")
            self.assertEqual(sentinel["purpose"], "packaged-release-qa")
            self.assertEqual(sentinel["nonce"], "qa_nonce_1234567890")
            self.assertEqual(sentinel["user_data_path"], str(paths.user_data))

    def test_reuses_only_private_nonsymlink_upgrade_user_data_under_qa_marker(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            existing = root / runner.QA_PATH_MARKER / "previous" / "用户 数据 Ω"
            existing.mkdir(parents=True, mode=0o700)
            existing.chmod(0o700)

            paths = runner._create_paths(
                root / "evidence",
                existing_user_data=existing,
            )

            self.assertEqual(paths.user_data, existing.resolve())
            outside = root / "ordinary-user-data"
            outside.mkdir()
            with self.assertRaisesRegex(runner.QaFailure, "exact"):
                runner._resolve_existing_qa_user_data(outside)

    def test_runtime_evidence_never_contains_the_access_token(self) -> None:
        identity = runner.RuntimeIdentity.from_payload(
            {
                "protocol_version": "3",
                "app_version": "9.9.9",
                "runtime_id": "runtime-id",
                "base_url": "http://127.0.0.1:48120",
                "access_token": "super-secret-agent-token",
                "desktop": {"pid": 101, "started_at": "now"},
                "backend": {"pid": 102, "started_at": "now"},
                "worker": {"pid": 103, "started_at": "now"},
                "published_at": "now",
            }
        )

        encoded = json.dumps(identity.evidence_payload(), sort_keys=True)
        self.assertNotIn("access_token", encoded)
        self.assertNotIn("super-secret-agent-token", encoded)
        self.assertNotIn(
            "super-secret-agent-token",
            json.dumps(runner._redact_payload({"access_token": "super-secret-agent-token"})),
        )

    def test_linear_trend_reports_slope_per_hour_and_fit(self) -> None:
        trend = runner._linear_trend(
            [0.0, 1800.0, 3600.0],
            [100.0, 150.0, 200.0],
        )

        self.assertAlmostEqual(trend["slope_per_hour"], 100.0)
        self.assertAlmostEqual(trend["r_squared"], 1.0)

    def test_artifact_tree_hash_is_stable_and_content_sensitive(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "Auto Email Sender.app"
            executable = root / "Contents" / "MacOS" / "Auto Email Sender"
            executable.parent.mkdir(parents=True)
            executable.write_bytes(b"first")

            first = runner._sha256_tree(root)
            second = runner._sha256_tree(root)
            self.assertEqual(first, second)
            self.assertEqual(first["file_count"], 1)

            executable.write_bytes(b"second")
            third = runner._sha256_tree(root)
            self.assertNotEqual(first["sha256"], third["sha256"])

    def test_candidate_manifest_binds_run_revision_version_and_package_bytes(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            package = root / "current-candidate.exe"
            package.write_bytes(b"candidate installer")
            digest = runner._sha256_file(package)
            manifest = {
                "schemaVersion": 1,
                "kind": "auto-email-sender-release-candidate",
                "repository": "JunieXD/AutoEmailSender",
                "releaseTag": "v9.9.9",
                "version": "9.9.9",
                "releaseSha": "a" * 40,
                "candidateRunId": 123456,
                "platforms": {
                    "windows": {
                        "schemaVersion": 1,
                        "kind": "auto-email-sender-platform-evidence",
                        "platform": "windows",
                        "releaseTag": "v9.9.9",
                        "version": "9.9.9",
                        "releaseSha": "a" * 40,
                        "candidateRunId": 123456,
                        "artifacts": [
                            {
                                "name": "AutoEmailSender-Setup-9.9.9.exe",
                                "size": package.stat().st_size,
                                "sha256": digest,
                            }
                        ],
                    }
                },
            }
            manifest_path = root / "release-candidate.json"
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            with mock.patch.object(runner.sys, "platform", "win32"):
                evidence = runner._verify_candidate_asset_manifest(
                    manifest_path,
                    package_file=package,
                    expected_package_sha256=digest,
                    expected_revision="a" * 40,
                    expected_app_version="9.9.9",
                    expected_run_id=123456,
                )
            self.assertEqual(evidence["candidate_run_id"], 123456)
            self.assertEqual(evidence["asset_sha256"], digest)

            manifest["releaseSha"] = "b" * 40
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            with (
                mock.patch.object(runner.sys, "platform", "win32"),
                self.assertRaisesRegex(runner.QaFailure, "releaseSha"),
            ):
                runner._verify_candidate_asset_manifest(
                    manifest_path,
                    package_file=package,
                    expected_package_sha256=digest,
                    expected_revision="a" * 40,
                    expected_app_version="9.9.9",
                    expected_run_id=123456,
                )

    def test_prerelease_candidate_manifest_binds_packaged_qa_identity(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            version = "9.9.9-beta.1"
            package = root / f"AutoEmailSender-Setup-{version}.exe"
            package.write_bytes(b"prerelease candidate installer")
            digest = runner._sha256_file(package)
            source_branch = "release/generic-topic"
            release_sha = "a" * 40
            run_id = 123456
            asset = {
                "name": package.name,
                "size": package.stat().st_size,
                "sha256": digest,
            }
            build_identity = {
                "schema_version": 1,
                "release_kind": "prerelease",
                "version": version,
                "channel": "beta",
                "source_branch": source_branch,
                "release_sha": release_sha,
                "candidate_run_id": str(run_id),
                "candidate_asset_name": package.name,
                "candidate_asset_sha256": None,
                "default_backend_mode": "split",
                "diagnostics_schema_version": 1,
            }
            platform_evidence = {
                "schemaVersion": 1,
                "kind": "auto-email-sender-prerelease-platform-evidence",
                "platform": "windows",
                "releaseTag": f"v{version}",
                "version": version,
                "channel": "beta",
                "sourceBranch": source_branch,
                "releaseSha": release_sha,
                "candidateRunId": run_id,
                "defaultBackendMode": "split",
                "diagnosticsSchemaVersion": 1,
                "buildIdentity": build_identity,
                "artifact": asset,
            }
            manifest = {
                "schemaVersion": 1,
                "kind": "auto-email-sender-prerelease-candidate",
                "repository": "JunieXD/AutoEmailSender",
                "releaseTag": f"v{version}",
                "version": version,
                "channel": "beta",
                "sourceBranch": source_branch,
                "releaseSha": release_sha,
                "candidateRunId": run_id,
                "defaultBackendMode": "split",
                "diagnosticsSchemaVersion": 1,
                "stableIsolation": {
                    "kind": "auto-email-sender-stable-isolation-snapshot",
                    "repository": "JunieXD/AutoEmailSender",
                },
                "platforms": {"windows": platform_evidence},
            }
            manifest_path = root / "prerelease-candidate.json"
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            with mock.patch.object(runner.sys, "platform", "win32"):
                evidence = runner._verify_candidate_asset_manifest(
                    manifest_path,
                    package_file=package,
                    expected_package_sha256=digest,
                    expected_revision=release_sha,
                    expected_app_version=version,
                    expected_run_id=run_id,
                )
            self.assertEqual(
                evidence["candidate_kind"],
                "auto-email-sender-prerelease-candidate",
            )
            self.assertEqual(evidence["source_branch"], source_branch)

            platform_evidence["channel"] = "rc"
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            with (
                mock.patch.object(runner.sys, "platform", "win32"),
                self.assertRaisesRegex(runner.QaFailure, "evidence channel"),
            ):
                runner._verify_candidate_asset_manifest(
                    manifest_path,
                    package_file=package,
                    expected_package_sha256=digest,
                    expected_revision=release_sha,
                    expected_app_version=version,
                    expected_run_id=run_id,
                )

    def test_resource_summary_flags_large_statistically_monotonic_growth(self) -> None:
        samples = [
            {
                "elapsed_seconds": float(index * 60),
                "roles": {
                    "worker": {
                        "rss_bytes": index * 8 * 1024 * 1024,
                        "handles": 20,
                        "inet_connections": 0,
                        "database_open_files": 1,
                    }
                },
                "log_bytes": 1024,
                "status_bytes": 1024,
            }
            for index in range(30)
        ]

        summary = runner._summarize_resource_samples(samples)

        self.assertTrue(
            any("worker.rss_bytes" in violation for violation in summary["violations"])
        )

    def test_resource_summary_rejects_browser_and_runtime_status_accumulation(self) -> None:
        samples = [
            {
                "elapsed_seconds": float(index * 60),
                "roles": {
                    "worker": {
                        "rss_bytes": 100,
                        "handles": 20,
                        "inet_connections": 0,
                        "database_open_files": 1,
                        "child_count": 0,
                    }
                },
                "browser_pids": [9001] if index == 29 else [],
                "runtime_file_count": index + 1,
                "log_bytes": 1024,
                "status_bytes": 1024,
            }
            for index in range(30)
        ]

        summary = runner._summarize_resource_samples(samples)

        self.assertIn(
            "runtime status file count accumulated across the soak",
            summary["violations"],
        )
        self.assertIn(
            "Playwright browser descendants remained at a resource sample",
            summary["violations"],
        )

    def test_database_audit_accepts_preflight_retries_but_rejects_two_irreversible_claims(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            database_path = Path(temp_dir) / "auto_email_sender.db"
            connection = sqlite3.connect(database_path)
            connection.executescript(
                """
                PRAGMA foreign_keys = ON;
                CREATE TABLE email_tasks (id INTEGER PRIMARY KEY);
                CREATE TABLE email_delivery_attempts (
                    attempt_id TEXT PRIMARY KEY,
                    email_task_id INTEGER NOT NULL REFERENCES email_tasks(id),
                    outcome TEXT NOT NULL
                );
                INSERT INTO email_tasks(id) VALUES (1);
                INSERT INTO email_delivery_attempts VALUES ('a', 1, 'pre_submission_failed');
                INSERT INTO email_delivery_attempts VALUES ('b', 1, 'smtp_accepted');
                """
            )
            connection.commit()
            connection.close()

            accepted = runner.audit_database(database_path)
            self.assertTrue(accepted.passed)

            connection = sqlite3.connect(database_path)
            connection.execute(
                "INSERT INTO email_delivery_attempts VALUES (?, ?, ?)",
                ("c", 1, "assumed_sent_after_interruption"),
            )
            connection.commit()
            connection.close()

            rejected = runner.audit_database(database_path)
            self.assertFalse(rejected.passed)
            self.assertTrue(
                any("irreversible claims" in value for value in rejected.invariant_violations)
            )

    def test_development_smoke_accepts_short_soak_but_certification_rejects_it(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            executable = Path(temp_dir) / "app"
            executable.write_bytes(b"fixture")
            smoke = runner.parse_args(
                [
                    "--scenario",
                    "normal-soak",
                    "--app-executable",
                    str(executable),
                    "--artifacts-dir",
                    temp_dir,
                    "--duration-seconds",
                    "1",
                    "--development-smoke",
                ]
            )
            self.assertEqual(smoke.duration_seconds, 1)

            with self.assertRaises(SystemExit):
                runner.parse_args(
                    [
                        "--scenario",
                        "normal-soak",
                        "--app-executable",
                        str(executable),
                        "--artifacts-dir",
                        temp_dir,
                        "--duration-seconds",
                        "1",
                        "--certification",
                        "--expected-revision",
                        "a" * 40,
                        "--repository-root",
                        temp_dir,
                    ]
                )

    def test_prerelease_certification_uses_ten_minute_intensive_gate(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            executable = root / "app"
            executable.write_bytes(b"fixture")
            package_file = root / "current-package.dmg"
            package_file.write_bytes(b"current package")
            candidate_manifest = root / "prerelease-candidate.json"
            candidate_manifest.write_text("{}", encoding="utf-8")
            base = [
                "--scenario",
                "normal-soak",
                "--app-executable",
                str(executable),
                "--artifacts-dir",
                str(root / "evidence"),
                "--prerelease-certification",
                "--expected-revision",
                "a" * 40,
                "--repository-root",
                str(root),
                "--package-file",
                str(package_file),
                "--expected-app-version",
                "2.6.0-beta.1",
                "--expected-package-sha256",
                runner._sha256_file(package_file),
                "--candidate-manifest-file",
                str(candidate_manifest),
                "--expected-candidate-run-id",
                "123456",
            ]
            accepted = runner.parse_args([*base, "--duration-seconds", "300"])
            self.assertTrue(accepted.prerelease_certification)
            self.assertEqual(accepted.sample_interval_seconds, 10.0)
            self.assertEqual(accepted.action_interval_seconds, 5.0)
            with self.assertRaises(SystemExit):
                runner.parse_args([*base, "--duration-seconds", "299"])
            with self.assertRaises(SystemExit):
                runner.parse_args(
                    [
                        *base,
                        "--duration-seconds",
                        "300",
                        "--sample-interval-seconds",
                        "11",
                    ]
                )
            with self.assertRaises(SystemExit):
                runner.parse_args(
                    [
                        *base,
                        "--duration-seconds",
                        "300",
                        "--action-interval-seconds",
                        "6",
                    ]
                )

            chaos_base = [*base]
            chaos_base[1] = "seeded-chaos"
            chaos_base.extend(["--seed", "20260810", "--system-sleep-wake"])
            accepted_chaos = runner.parse_args(
                [*chaos_base, "--duration-seconds", "300"]
            )
            self.assertTrue(accepted_chaos.prerelease_certification)
            with self.assertRaises(SystemExit):
                runner.parse_args([*chaos_base, "--duration-seconds", "299"])

    def test_lifecycle_certification_requires_native_sleep_and_previous_package_data(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            executable = root / "app"
            executable.write_bytes(b"fixture")
            package_file = root / "current-package.dmg"
            package_file.write_bytes(b"current package")
            previous_package_file = root / "previous-package.dmg"
            previous_package_file.write_bytes(b"previous package")
            existing = root / runner.QA_PATH_MARKER / "previous" / "用户 数据 Ω"
            existing.mkdir(parents=True, mode=0o700)
            existing.chmod(0o700)
            manifest = root / "manifest.json"
            manifest.write_text("{}", encoding="utf-8")
            candidate_manifest = root / "release-candidate.json"
            candidate_manifest.write_text("{}", encoding="utf-8")
            base = [
                "--scenario",
                "lifecycle",
                "--app-executable",
                str(executable),
                "--artifacts-dir",
                str(root / "evidence"),
                "--certification",
                "--expected-revision",
                "a" * 40,
                "--repository-root",
                str(root),
                "--package-file",
                str(package_file),
                "--expected-app-version",
                "2.5.4",
                "--expected-package-sha256",
                runner._sha256_file(package_file),
                "--candidate-manifest-file",
                str(candidate_manifest),
                "--expected-candidate-run-id",
                "123456",
            ]
            with self.assertRaises(SystemExit):
                runner.parse_args(base)
            with self.assertRaises(SystemExit):
                runner.parse_args([*base, "--system-sleep-wake"])

            accepted = runner.parse_args(
                [
                    *base,
                    "--system-sleep-wake",
                    "--existing-user-data",
                    str(existing),
                    "--upgrade-manifest",
                    str(manifest),
                    "--expected-previous-version",
                    "2.5.3",
                    "--previous-package-file",
                    str(previous_package_file),
                    "--expected-previous-package-sha256",
                    runner._sha256_file(previous_package_file),
                ]
            )

            self.assertTrue(accepted.system_sleep_wake)
            self.assertEqual(accepted.existing_user_data, existing.resolve())
            self.assertEqual(accepted.upgrade_manifest, manifest.resolve())

    def test_rehearsal_is_non_certifying_and_rejects_candidate_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            executable = root / "app"
            executable.write_bytes(b"fixture")
            package_file = root / "current-package.dmg"
            package_file.write_bytes(b"current package")
            previous_package_file = root / "previous-package.dmg"
            previous_package_file.write_bytes(b"previous package")
            existing = root / runner.QA_PATH_MARKER / "previous" / "user-data"
            existing.mkdir(parents=True, mode=0o700)
            existing.chmod(0o700)
            upgrade_manifest = root / "upgrade-manifest.json"
            upgrade_manifest.write_text("{}", encoding="utf-8")
            candidate_manifest = root / "invalidated-candidate.json"
            candidate_manifest.write_text("{}", encoding="utf-8")
            base = [
                "--scenario",
                "lifecycle",
                "--app-executable",
                str(executable),
                "--artifacts-dir",
                str(root / "evidence"),
                "--harness-rehearsal",
                "--package-file",
                str(package_file),
                "--expected-package-sha256",
                runner._sha256_file(package_file),
                "--existing-user-data",
                str(existing),
                "--upgrade-manifest",
                str(upgrade_manifest),
                "--expected-previous-version",
                "2.5.4",
                "--previous-package-file",
                str(previous_package_file),
                "--expected-previous-package-sha256",
                runner._sha256_file(previous_package_file),
                "--repository-root",
                str(root),
            ]
            accepted = runner.parse_args(base)
            self.assertTrue(accepted.harness_rehearsal)
            self.assertFalse(accepted.certification)
            self.assertFalse(accepted.prerelease_certification)

            with self.assertRaises(SystemExit):
                runner.parse_args(
                    [
                        *base,
                        "--candidate-manifest-file",
                        str(candidate_manifest),
                        "--expected-candidate-run-id",
                        "123456",
                    ]
                )

    def test_candidate_admission_requires_exact_binding_and_native_sleep(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            executable = root / "app"
            executable.write_bytes(b"fixture")
            package_file = root / "current-package.dmg"
            package_file.write_bytes(b"current package")
            previous_package_file = root / "previous-package.dmg"
            previous_package_file.write_bytes(b"previous package")
            existing = root / runner.QA_PATH_MARKER / "previous" / "user-data"
            existing.mkdir(parents=True, mode=0o700)
            existing.chmod(0o700)
            upgrade_manifest = root / "upgrade-manifest.json"
            upgrade_manifest.write_text("{}", encoding="utf-8")
            candidate_manifest = root / "candidate.json"
            candidate_manifest.write_text("{}", encoding="utf-8")
            base = [
                "--scenario",
                "lifecycle",
                "--app-executable",
                str(executable),
                "--artifacts-dir",
                str(root / "evidence"),
                "--candidate-admission",
                "--expected-revision",
                "a" * 40,
                "--repository-root",
                str(root),
                "--package-file",
                str(package_file),
                "--expected-app-version",
                "2.6.0-beta.1",
                "--expected-package-sha256",
                runner._sha256_file(package_file),
                "--candidate-manifest-file",
                str(candidate_manifest),
                "--expected-candidate-run-id",
                "123456",
                "--existing-user-data",
                str(existing),
                "--upgrade-manifest",
                str(upgrade_manifest),
                "--expected-previous-version",
                "2.5.4",
                "--previous-package-file",
                str(previous_package_file),
                "--expected-previous-package-sha256",
                runner._sha256_file(previous_package_file),
            ]
            with self.assertRaises(SystemExit):
                runner.parse_args(base)

            accepted = runner.parse_args([*base, "--system-sleep-wake"])
            self.assertTrue(accepted.candidate_admission)
            self.assertFalse(accepted.certification)
            self.assertFalse(accepted.prerelease_certification)

    def test_upgrade_verification_requires_repository_head_and_a_new_previous_revision_backup(
        self,
    ) -> None:
        expected_head = runner._repository_alembic_head(REPOSITORY_ROOT)
        self.assertEqual(expected_head, "20260810_merge_agent_ui_delivery")
        previous_revision = "20260808_crawl_llm_snapshot"
        with tempfile.TemporaryDirectory() as temp_dir:
            user_data = (
                Path(temp_dir) / runner.QA_PATH_MARKER / "upgrade" / "用户 数据 Ω"
            ).resolve()
            material_path = user_data / "materials" / "上一稳定版 简历 Ω.txt"
            material_path.parent.mkdir(parents=True)
            material_path.write_text("upgrade material", encoding="utf-8")
            material_sha256 = runner._sha256_file(material_path)

            database_path = user_data / runner.DATABASE_NAME
            connection = sqlite3.connect(database_path)
            connection.executescript(
                """
                CREATE TABLE alembic_version (version_num TEXT NOT NULL);
                CREATE TABLE identity_materials (
                    id INTEGER PRIMARY KEY,
                    identity_id INTEGER NOT NULL,
                    file_path TEXT NOT NULL,
                    sha256 TEXT NOT NULL,
                    size_bytes INTEGER NOT NULL
                );
                """
            )
            connection.execute("INSERT INTO alembic_version VALUES (?)", (expected_head,))
            connection.execute(
                "INSERT INTO identity_materials VALUES (?, ?, ?, ?, ?)",
                (9, 4, str(material_path), material_sha256, material_path.stat().st_size),
            )
            connection.commit()
            connection.close()

            backup_path = user_data / "backups" / "schema" / "before-current.db"
            backup_path.parent.mkdir(parents=True)
            backup = sqlite3.connect(backup_path)
            backup.execute("CREATE TABLE alembic_version (version_num TEXT NOT NULL)")
            backup.execute("INSERT INTO alembic_version VALUES (?)", (previous_revision,))
            backup.commit()
            backup.close()

            manifest_path = Path(temp_dir) / "manifest.json"
            manifest = {
                "protocol_version": "1",
                "purpose": "previous-stable-packaged-upgrade",
                "user_data_path": str(user_data),
                "previous_app_version": "2.5.3",
                "previous_runtime_id": "previous-runtime",
                "previous_artifact_sha256": "a" * 64,
                "previous_package_sha256": "c" * 64,
                "database_sha256": "b" * 64,
                "alembic_revision": previous_revision,
                "pre_upgrade_schema_backups": [],
                "draft_custom_instruction": "packaged-upgrade:test",
                "professor": {
                    "id": 7,
                    "name": "升级验证导师 Ω",
                    "email": "upgrade@example.edu",
                },
                "material": {
                    "id": 9,
                    "identity_id": 4,
                    "relative_path": material_path.relative_to(user_data).as_posix(),
                    "sha256": material_sha256,
                    "bytes": material_path.stat().st_size,
                },
            }
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            identity = runner.RuntimeIdentity.from_payload(
                {
                    "protocol_version": "3",
                    "app_version": "2.5.4",
                    "runtime_id": "current-runtime",
                    "base_url": "http://127.0.0.1:48120",
                    "access_token": "test-token",
                    "desktop": {"pid": 101, "started_at": "now"},
                    "backend": {"pid": 102, "started_at": "now"},
                    "worker": {"pid": 103, "started_at": "now"},
                    "published_at": "now",
                }
            )

            def request_json(method: str, url: str, **kwargs: object) -> object:
                self.assertEqual(method, "GET")
                self.assertEqual(kwargs["token"], "test-token")
                if url.endswith("/settings"):
                    return {"draft_custom_instruction": "packaged-upgrade:test"}
                if url.endswith("/professors/7"):
                    return {
                        "id": 7,
                        "name": "升级验证导师 Ω",
                        "email": "upgrade@example.edu",
                    }
                self.fail(f"unexpected upgrade request: {url}")

            passing_audit = runner.DatabaseAudit(
                at="now",
                integrity_check=["ok"],
                quick_check=["ok"],
                foreign_key_violations=[],
                invariant_violations=[],
                wal_bytes=0,
                shm_bytes=0,
            )
            with (
                mock.patch.object(runner, "_request_json", side_effect=request_json),
                mock.patch.object(runner, "audit_database", return_value=passing_audit),
            ):
                evidence = runner._verify_upgrade_manifest(
                    manifest_path,
                    identity,
                    user_data,
                    repository_root=REPOSITORY_ROOT,
                    expected_previous_version="2.5.3",
                    expected_previous_package_sha256="c" * 64,
                )

            self.assertEqual(evidence["current_alembic_revision"], expected_head)
            self.assertEqual(evidence["expected_alembic_revision"], expected_head)
            self.assertEqual(
                evidence["migration_backup"]["alembic_revision"],
                previous_revision,
            )
            self.assertEqual(evidence["migration_backup"]["new_backup_count"], 1)

            manifest["pre_upgrade_schema_backups"] = [
                {
                    "relative_path": backup_path.relative_to(user_data).as_posix(),
                    "sha256": runner._sha256_file(backup_path),
                    "bytes": backup_path.stat().st_size,
                }
            ]
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            with (
                mock.patch.object(runner, "_request_json", side_effect=request_json),
                self.assertRaisesRegex(runner.QaFailure, "no new migration backup"),
            ):
                runner._verify_upgrade_manifest(
                    manifest_path,
                    identity,
                    user_data,
                    repository_root=REPOSITORY_ROOT,
                    expected_previous_version="2.5.3",
                    expected_previous_package_sha256="c" * 64,
                )

    @unittest.skipUnless(sys.platform == "darwin", "macOS power counters are Darwin-only")
    def test_macos_native_power_counter_probe_is_read_only_and_complete(self) -> None:
        counts = runner._read_macos_power_counts()

        self.assertEqual(
            set(counts),
            {"sleep_count", "wake_count", "dark_wake_count", "user_wake_count"},
        )
        self.assertTrue(all(isinstance(value, int) and value >= 0 for value in counts.values()))

    def test_qa_http_client_refuses_external_hosts_before_connecting(self) -> None:
        with self.assertRaisesRegex(runner.QaFailure, "non-loopback"):
            runner._request_json("GET", "https://example.com/api")


if __name__ == "__main__":
    unittest.main()
