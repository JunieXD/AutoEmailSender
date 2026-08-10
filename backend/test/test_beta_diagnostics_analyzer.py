from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import stat
import sys
import tempfile
import unittest
from contextlib import redirect_stderr
from io import StringIO
from pathlib import Path
from uuid import UUID
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
ANALYZER_PATH = REPOSITORY_ROOT / "scripts" / "quality" / "analyze_beta_diagnostics.py"


def _load_analyzer():
    spec = importlib.util.spec_from_file_location("beta_diagnostics_analyzer", ANALYZER_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load {ANALYZER_PATH}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


analyzer = _load_analyzer()


class BetaDiagnosticsAnalyzerTests(unittest.TestCase):
    def test_valid_bundle_reports_mode_resources_restarts_locks_backlog_and_invariants(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            bundle_path = Path(temp_dir) / "split-diagnostics.zip"
            contents = _valid_contents(
                effective_mode="split",
                platform="win32",
                queued_crawler=2,
                duplicate_groups=1,
            )
            _write_bundle(bundle_path, contents)

            report = analyzer.analyze_bundles([bundle_path])

            self.assertEqual(report["bundle_count"], 1)
            bundle = report["bundles"][0]
            self.assertEqual(bundle["effective_mode"], "split")
            self.assertEqual(bundle["platform"], "win32")
            self.assertEqual(bundle["lifecycle"]["restart_events"], 1)
            self.assertEqual(bundle["sqlite"]["timeline_lock_events"], 1)
            self.assertEqual(bundle["sqlite"]["lock_errors_1h"], 2)
            self.assertEqual(bundle["workloads"][-1]["queued"], 2)
            self.assertEqual(bundle["invariants"]["duplicate_delivery_attempt_groups"], 1)
            self.assertEqual(
                bundle["resource_trends"]["overall"]["rss_bytes"]["change"],
                128.0 * 1024 * 1024,
            )
            self.assertEqual(report["aggregate"]["effective_modes"], {"split": 1})
            alert_codes = {item["code"] for item in report["alerts"]}
            self.assertIn("sqlite_contention", alert_codes)
            self.assertIn("old_queue_backlog", alert_codes)
            self.assertIn("duplicate_delivery_attempt", alert_codes)

    def test_multiple_bundles_aggregate_modes_platforms_versions_and_installations(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            combined_path = root / "combined.zip"
            split_path = root / "split.zip"
            _write_bundle(
                combined_path,
                _valid_contents(
                    report_id="00000000-0000-4000-8000-000000000001",
                    installation_id="00000000-0000-4000-8000-000000000011",
                    version="2.6.0-beta.1",
                    requested_mode="combined",
                    effective_mode="combined",
                    platform="darwin",
                ),
            )
            _write_bundle(
                split_path,
                _valid_contents(
                    report_id="00000000-0000-4000-8000-000000000002",
                    installation_id="00000000-0000-4000-8000-000000000022",
                    version="2.6.0-beta.2",
                    requested_mode="split",
                    effective_mode="split",
                    platform="win32",
                ),
            )

            report = analyzer.analyze_bundles([combined_path, split_path])

            aggregate = report["aggregate"]
            self.assertEqual(aggregate["installation_count"], 2)
            self.assertEqual(aggregate["effective_modes"], {"combined": 1, "split": 1})
            self.assertEqual(aggregate["platforms"], {"darwin": 1, "win32": 1})
            self.assertEqual(
                aggregate["versions"],
                {"2.6.0-beta.1": 1, "2.6.0-beta.2": 1},
            )

    def test_rejects_path_traversal_without_extracting_anything(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            bundle_path = root / "traversal.zip"
            _write_bundle(
                bundle_path,
                _valid_contents(),
                renamed_entries={"manifest.json": "../manifest.json"},
            )

            with self.assertRaises(analyzer.BundleValidationError) as caught:
                analyzer.validate_bundle(bundle_path)

            self.assertEqual(caught.exception.code, "unsafe_entry_path")
            self.assertFalse((root.parent / "manifest.json").exists())

    def test_rejects_symlink_entries(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            bundle_path = Path(temp_dir) / "symlink.zip"
            _write_bundle(
                bundle_path,
                _valid_contents(),
                symlink_entry="manifest.json",
            )

            with self.assertRaises(analyzer.BundleValidationError) as caught:
                analyzer.validate_bundle(bundle_path)

            self.assertEqual(caught.exception.code, "special_entry")

    @unittest.skipIf(os.name == "nt", "Windows cannot create a POSIX symlink without privileges")
    def test_rejects_a_symlink_archive_path(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            real_path = root / "real.zip"
            link_path = root / "link.zip"
            _write_bundle(real_path, _valid_contents())
            link_path.symlink_to(real_path)

            with self.assertRaises(analyzer.BundleValidationError) as caught:
                analyzer.validate_bundle(link_path)

            self.assertEqual(caught.exception.code, "archive_open_failed")

    def test_rejects_high_ratio_zip_bombs_before_json_parsing(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            bundle_path = Path(temp_dir) / "bomb.zip"
            contents = _valid_contents()
            contents["README.txt"] = b"0" * (8 * 1024 * 1024)
            _refresh_checksums(contents)
            _write_bundle(bundle_path, contents)

            with self.assertRaises(analyzer.BundleValidationError) as caught:
                analyzer.validate_bundle(bundle_path)

            self.assertEqual(caught.exception.code, "zip_bomb")

    def test_rejects_unknown_manifest_schema(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            bundle_path = Path(temp_dir) / "unknown-schema.zip"
            contents = _valid_contents()
            manifest = json.loads(contents["manifest.json"])
            manifest["schema_version"] = 2
            contents["manifest.json"] = _json_bytes(manifest)
            _refresh_checksums(contents)
            _write_bundle(bundle_path, contents)

            with self.assertRaises(analyzer.BundleValidationError) as caught:
                analyzer.validate_bundle(bundle_path)

            self.assertEqual(caught.exception.code, "unknown_schema")

    def test_rejects_a_checksum_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            bundle_path = Path(temp_dir) / "bad-checksum.zip"
            contents = _valid_contents()
            contents["timeline.jsonl"] += b"\n"
            _write_bundle(bundle_path, contents)

            with self.assertRaises(analyzer.BundleValidationError) as caught:
                analyzer.validate_bundle(bundle_path)

            self.assertEqual(caught.exception.code, "checksum_mismatch")

    def test_rejects_canary_tokens_after_validating_the_final_zip(self) -> None:
        canary = "CANARY_邮件正文_7cd83b9f"
        with tempfile.TemporaryDirectory() as temp_dir:
            bundle_path = Path(temp_dir) / "canary.zip"
            contents = _valid_contents()
            contents["README.txt"] += canary.encode("utf-8")
            _refresh_checksums(contents)
            _write_bundle(bundle_path, contents)

            with self.assertRaises(analyzer.BundleValidationError) as caught:
                analyzer.validate_bundle(bundle_path, forbidden_tokens=[canary.encode("utf-8")])

            self.assertEqual(caught.exception.code, "forbidden_token_found")
            self.assertNotIn(canary, str(caught.exception))

    def test_cli_writes_a_private_report_and_refuses_to_overwrite_it(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            bundle_path = root / "valid.zip"
            report_path = root / "report.json"
            _write_bundle(bundle_path, _valid_contents())

            first_exit = analyzer.main([str(bundle_path), "--output", str(report_path)])
            with redirect_stderr(StringIO()) as second_error:
                second_exit = analyzer.main([str(bundle_path), "--output", str(report_path)])

            self.assertEqual(first_exit, 0)
            self.assertEqual(second_exit, 2)
            self.assertIn("output_exists", second_error.getvalue())
            self.assertEqual(json.loads(report_path.read_text(encoding="utf-8"))["bundle_count"], 1)
            if os.name != "nt":
                self.assertEqual(report_path.stat().st_mode & 0o777, 0o600)


def _valid_contents(
    *,
    report_id: str = "00000000-0000-4000-8000-000000000001",
    installation_id: str = "00000000-0000-4000-8000-000000000011",
    version: str = "2.6.0-beta.1",
    requested_mode: str = "split",
    effective_mode: str = "split",
    platform: str = "win32",
    queued_crawler: int = 0,
    duplicate_groups: int = 0,
) -> dict[str, bytes]:
    UUID(report_id)
    UUID(installation_id)
    generated_at = "2026-08-10T08:00:00.000Z"
    timeline = [
        {
            "schema_version": 1,
            "stream": "timeline",
            "wall_time": "2026-08-10T07:58:00.000Z",
            "monotonic_ms": 1000,
            "component": "electron",
            "session_id": "session-1",
            "event": "backend_restart",
            "severity": "warning",
            "details": {"state": "started"},
        },
        {
            "schema_version": 1,
            "stream": "timeline",
            "wall_time": "2026-08-10T07:59:00.000Z",
            "monotonic_ms": 2000,
            "component": "api",
            "session_id": "session-1",
            "event": "sqlite_lock_error",
            "severity": "warning",
            "details": {"source": "sqlalchemy"},
        },
    ]
    resources = [
        {
            "schema_version": 1,
            "stream": "resource-samples",
            "wall_time": "2026-08-10T07:58:00.000Z",
            "monotonic_ms": 1000,
            "component": "api",
            "session_id": "session-1",
            "cpu_percent": 10.0,
            "rss_bytes": 128 * 1024 * 1024,
            "handles_or_fds": 50,
        },
        {
            "schema_version": 1,
            "stream": "resource-samples",
            "wall_time": "2026-08-10T07:59:00.000Z",
            "monotonic_ms": 2000,
            "component": "api",
            "session_id": "session-1",
            "cpu_percent": 20.0,
            "rss_bytes": 256 * 1024 * 1024,
            "handles_or_fds": 55,
        },
    ]
    workload_items = []
    for kind in (
        "dispatcher",
        "imap_sync",
        "imap_history",
        "batch_draft",
        "matching",
        "crawler",
    ):
        queued = queued_crawler if kind == "crawler" else 0
        workload_items.append(
            {
                "kind": kind,
                "queued": queued,
                "running": 0,
                "succeeded": 1,
                "failed": 0,
                "interrupted": 0,
                "recovered": 0,
                "oldest_queue_age_seconds": 600.0 if queued else None,
                "oldest_running_age_seconds": None,
                "average_duration_seconds": 1.0,
                "maximum_duration_seconds": 1.0,
            }
        )
    workload_summary = {
        "schema_version": 1,
        "generated_at": generated_at,
        "workloads": workload_items,
        "invariants": {
            "sending_count": 0,
            "duplicate_delivery_attempt_groups": duplicate_groups,
            "orphaned_claim_count": 0,
        },
    }
    database_health = {
        "schema_version": 1,
        "generated_at": generated_at,
        "available": True,
        "alembic_revision": "20260810_merge_delivery_scale",
        "integrity_check": "ok",
        "foreign_key_violation_count": 0,
        "journal_mode": "wal",
        "busy_timeout_ms": 5000,
        "database_bytes": 4096,
        "wal_bytes": 1024,
        "shm_bytes": 1024,
        "backup_count": 1,
        "newest_backup_age_seconds": 60.0,
        "lock_errors_1h": 2,
        "busy_errors_1h": 1,
        "slow_queries_1h": 1,
        "maximum_query_ms_1h": 750.0,
    }
    operation_summary = {
        "schema_version": 1,
        "generated_at": generated_at,
        "total_1h": 2,
        "total_24h": 3,
        "levels_24h": {"debug": 0, "info": 2, "warning": 1, "error": 0},
        "categories_24h": [],
    }
    manifest = {
        "schema_version": 1,
        "report_id": report_id,
        "installation_id": installation_id,
        "exported_at": generated_at,
        "range": "24h",
        "range_start": "2026-08-09T08:00:00.000Z",
        "partial": False,
        "missing_sections": [],
        "app": {
            "name": "Auto Email Sender",
            "version": version,
            "channel": "beta",
            "source_branch": "beta/topic",
            "release_sha": "a" * 40,
            "candidate_run_id": "123",
            "candidate_asset_name": "AutoEmailSender.dmg",
            "candidate_asset_sha256": "b" * 64,
        },
        "system": {"platform": platform, "arch": "arm64", "os_release": "test"},
        "backend": {
            "requested_mode": requested_mode,
            "effective_mode": effective_mode,
        },
        "record_counts": {
            "timeline": len(timeline),
            "resource_samples": len(resources),
            "source_log_summaries": 0,
        },
    }
    summary = {
        "schema_version": 1,
        "generated_at": generated_at,
        "partial": False,
        "missing_sections": [],
        "timeline_records": len(timeline),
        "resource_samples": len(resources),
        "component_event_counts": {"api": 1, "electron": 1},
        "lifecycle_event_counts": {"backend_restart": 1, "sqlite_lock_error": 1},
        "resource_peaks": {
            "cpu_percent": 20.0,
            "rss_bytes": 256 * 1024 * 1024,
            "handles_or_fds": 55,
            "playwright_processes": None,
            "wal_bytes": None,
        },
    }
    contents = {
        "manifest.json": _json_bytes(manifest),
        "timeline.jsonl": _jsonl_bytes(timeline),
        "resource-samples.jsonl": _jsonl_bytes(resources),
        "workload-summary.json": _json_bytes(workload_summary),
        "database-health.json": _json_bytes(database_health),
        "logs/operation-summary.json": _json_bytes(operation_summary),
        "logs/electron.jsonl": _jsonl_bytes(
            [record for record in timeline if record["component"] == "electron"]
        ),
        "logs/api.jsonl": _jsonl_bytes(
            [record for record in timeline if record["component"] == "api"]
        ),
        "logs/worker.jsonl": b"",
        "logs/combined.jsonl": b"",
        "logs/startup-summary.jsonl": b"",
        "logs/backend-errors-summary.jsonl": b"",
        "summary.json": _json_bytes(summary),
        "README.txt": "Auto Email Sender Beta 本地诊断包\n".encode(),
    }
    _refresh_checksums(contents)
    return contents


def _json_bytes(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8")


def _jsonl_bytes(values: list[dict[str, object]]) -> bytes:
    if not values:
        return b""
    return ("\n".join(json.dumps(value, ensure_ascii=False, sort_keys=True) for value in values) + "\n").encode(
        "utf-8"
    )


def _refresh_checksums(contents: dict[str, bytes]) -> None:
    contents.pop("checksums.sha256", None)
    lines = [
        f"{hashlib.sha256(content).hexdigest()}  {name}"
        for name, content in sorted(contents.items())
    ]
    contents["checksums.sha256"] = ("\n".join(lines) + "\n").encode("ascii")


def _write_bundle(
    path: Path,
    contents: dict[str, bytes],
    *,
    renamed_entries: dict[str, str] | None = None,
    symlink_entry: str | None = None,
) -> None:
    renamed_entries = renamed_entries or {}
    with ZipFile(path, mode="w", compression=ZIP_DEFLATED) as archive:
        for name, content in sorted(contents.items()):
            output_name = renamed_entries.get(name, name)
            if name == symlink_entry:
                info = ZipInfo(output_name)
                info.create_system = 3
                info.compress_type = ZIP_DEFLATED
                info.external_attr = (stat.S_IFLNK | 0o777) << 16
                archive.writestr(info, content)
            else:
                archive.writestr(output_name, content)


if __name__ == "__main__":
    unittest.main()
