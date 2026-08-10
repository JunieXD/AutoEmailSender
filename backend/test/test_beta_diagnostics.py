from __future__ import annotations

import asyncio
import json
import os
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path


class BetaDiagnosticsRecorderTests(unittest.TestCase):
    def test_prerelease_and_test_gate_enable_without_enabling_stable_versions(self) -> None:
        from app.core.beta_diagnostics import beta_diagnostics_enabled

        self.assertTrue(beta_diagnostics_enabled("2.6.0-alpha.1", ""))
        self.assertTrue(beta_diagnostics_enabled("2.6.0-beta.2", ""))
        self.assertTrue(beta_diagnostics_enabled("2.6.0-rc.3", ""))
        self.assertTrue(beta_diagnostics_enabled("2.5.4", "enabled-for-tests-only"))
        self.assertFalse(beta_diagnostics_enabled("2.5.4", ""))
        self.assertFalse(beta_diagnostics_enabled("development", "true"))

    def test_records_role_specific_private_bounded_metrics_with_allowlisted_details(self) -> None:
        from app.core.beta_diagnostics import BetaDiagnosticsRecorder

        with tempfile.TemporaryDirectory() as temp_dir:
            recorder = BetaDiagnosticsRecorder(
                data_dir=Path(temp_dir),
                role="worker",
                app_version="2.6.0-beta.1",
                enabled=True,
                sample_interval_seconds=60,
            )

            async def exercise() -> None:
                await recorder.start()
                recorder.record_timeline(
                    "worker_test_event",
                    {
                        "state": "ready",
                        "worker_pid": os.getpid(),
                        "email": "private@example.test",
                        "body": "CANARY_BODY",
                        "error_code": "OperationalError",
                    },
                )
                recorder.record_resource_sample()
                await recorder.stop()

            asyncio.run(exercise())

            component_path = (
                Path(temp_dir) / "beta-diagnostics" / "segments" / "worker"
            )
            segment_paths = sorted(component_path.glob("*.jsonl"))
            self.assertGreaterEqual(len(segment_paths), 2)
            combined = "\n".join(
                path.read_text(encoding="utf-8") for path in segment_paths
            )
            self.assertIn('"component":"worker"', combined)
            self.assertIn('"event":"worker_test_event"', combined)
            self.assertIn('"stream":"resource-samples"', combined)
            self.assertIn('"error_code":"OperationalError"', combined)
            self.assertNotIn("private@example.test", combined)
            self.assertNotIn("CANARY_BODY", combined)
            self.assertFalse(any(path.name.endswith(".active.jsonl") for path in segment_paths))
            if os.name != "nt":
                self.assertEqual(component_path.stat().st_mode & 0o777, 0o700)
                for segment_path in segment_paths:
                    self.assertEqual(segment_path.stat().st_mode & 0o777, 0o600)

    def test_recording_failure_never_raises_into_the_product(self) -> None:
        from app.core.beta_diagnostics import BetaDiagnosticsRecorder

        with tempfile.TemporaryDirectory() as temp_dir:
            root_path = Path(temp_dir) / "beta-diagnostics"
            root_path.write_text("not-a-directory", encoding="utf-8")
            recorder = BetaDiagnosticsRecorder(
                data_dir=Path(temp_dir),
                role="api",
                app_version="2.6.0-beta.1",
                enabled=True,
            )

            async def exercise() -> None:
                await recorder.start()
                recorder.record_timeline("must_not_raise")
                recorder.record_resource_sample()
                await recorder.stop()

            asyncio.run(exercise())
            self.assertIsNotNone(recorder.last_error)

    def test_rotating_writer_finalizes_stale_active_segments(self) -> None:
        from app.core.beta_diagnostics import RotatingJsonlWriter

        with tempfile.TemporaryDirectory() as temp_dir:
            root_path = Path(temp_dir) / "beta-diagnostics"
            component_path = root_path / "segments" / "api"
            component_path.mkdir(parents=True)
            stale_path = component_path / "timeline-stale.active.jsonl"
            stale_path.write_text('{"event":"stale"}\n', encoding="utf-8")
            writer = RotatingJsonlWriter(
                root_path=root_path,
                component="api",
                stream="timeline",
            )

            writer.append({"event": "current"})
            self.assertFalse(stale_path.exists())
            self.assertTrue((component_path / "timeline-stale.jsonl").exists())
            writer.close()
            self.assertFalse(any(component_path.glob("*.active.jsonl")))

    def test_pruning_counts_protected_active_segments_toward_the_total_limit(self) -> None:
        from app.core.beta_diagnostics import prune_beta_diagnostics

        with tempfile.TemporaryDirectory() as temp_dir:
            root_path = Path(temp_dir) / "beta-diagnostics"
            component_path = root_path / "segments" / "api"
            component_path.mkdir(parents=True)
            active_path = component_path / "timeline-current.active.jsonl"
            active_path.write_bytes(b"a" * 60)
            for index in range(2):
                (component_path / f"timeline-{index}.jsonl").write_bytes(b"b" * 50)

            prune_beta_diagnostics(root_path, max_total_bytes=80)

            self.assertTrue(active_path.exists())
            remaining = list(component_path.glob("*.jsonl"))
            self.assertLessEqual(sum(path.stat().st_size for path in remaining), 80)
            self.assertEqual(remaining, [active_path])


class BetaDiagnosticsSummaryTests(unittest.TestCase):
    @unittest.skipIf(os.name == "nt", "Windows symlink creation requires privileges")
    def test_sqlite_metric_scan_does_not_follow_symlinked_segments(self) -> None:
        from app.services.beta_diagnostics_summary import _scan_sqlite_event_metrics

        with tempfile.TemporaryDirectory() as temp_dir:
            root_path = Path(temp_dir) / "beta-diagnostics"
            component_path = root_path / "segments" / "api"
            component_path.mkdir(parents=True)
            outside_path = Path(temp_dir) / "outside.jsonl"
            outside_path.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "wall_time": datetime.now(UTC).isoformat(),
                        "event": "sqlite_lock_error",
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            (component_path / "timeline-symlink.jsonl").symlink_to(outside_path)

            metrics = _scan_sqlite_event_metrics(
                root_path,
                cutoff=datetime.now(UTC) - timedelta(hours=1),
            )

            self.assertEqual(metrics["lock_errors"], 0)
            self.assertEqual(metrics["busy_errors"], 0)

    def test_builds_six_workload_database_and_aggregated_operation_summaries(self) -> None:
        from test.migrated_database import create_migrated_sqlite_database

        with tempfile.TemporaryDirectory() as temp_dir:
            data_dir = Path(temp_dir)
            database_path = data_dir / "auto_email_sender.db"
            create_migrated_sqlite_database(database_path)
            backup_dir = data_dir / "backups" / "schema"
            backup_dir.mkdir(parents=True)
            (backup_dir / "auto_email_sender.before-test.db").write_bytes(b"backup")
            previous_environment = {
                key: os.environ.get(key)
                for key in (
                    "AUTO_EMAIL_SENDER_DATA_DIR",
                    "DATABASE_URL",
                    "AUTO_EMAIL_SENDER_APP_VERSION",
                )
            }
            os.environ["AUTO_EMAIL_SENDER_DATA_DIR"] = temp_dir
            os.environ["DATABASE_URL"] = (
                f"sqlite+aiosqlite:///{database_path.as_posix()}"
            )
            os.environ["AUTO_EMAIL_SENDER_APP_VERSION"] = "2.6.0-beta.1"
            try:
                payload = asyncio.run(self._build_summary(data_dir))
            finally:
                asyncio.run(self._reset_database_state())
                for key, value in previous_environment.items():
                    if value is None:
                        os.environ.pop(key, None)
                    else:
                        os.environ[key] = value

            self.assertEqual(payload["schema_version"], 1)
            self.assertEqual(
                [item["kind"] for item in payload["workload_summary"]["workloads"]],
                [
                    "dispatcher",
                    "imap_sync",
                    "imap_history",
                    "batch_draft",
                    "matching",
                    "crawler",
                ],
            )
            database_health = payload["database_health"]
            self.assertEqual(database_health["integrity_check"], "ok")
            self.assertEqual(database_health["foreign_key_violation_count"], 0)
            self.assertEqual(database_health["backup_count"], 1)
            self.assertEqual(database_health["lock_errors_1h"], 1)
            self.assertEqual(database_health["busy_errors_1h"], 1)
            self.assertEqual(database_health["slow_queries_1h"], 1)
            self.assertEqual(database_health["maximum_query_ms_1h"], 750.0)
            operation_summary = payload["operation_log_summary"]
            self.assertEqual(operation_summary["total_1h"], 2)
            self.assertEqual(operation_summary["total_24h"], 2)
            self.assertEqual(operation_summary["levels_24h"]["error"], 1)
            self.assertEqual(
                operation_summary["categories_24h"],
                [{"category": "mail", "event_count": 2, "error_count": 1}],
            )
            serialized = json.dumps(payload, ensure_ascii=False)
            self.assertNotIn("private@example.test", serialized)
            self.assertNotIn("CANARY_MESSAGE", serialized)

    async def _build_summary(self, data_dir: Path) -> dict[str, object]:
        from app.core.beta_diagnostics import BetaDiagnosticsRecorder
        from app.core.config import get_settings
        from app.core.database import get_session_factory
        from app.models import OperationLog
        from app.services.beta_diagnostics_summary import build_beta_diagnostics_summary

        await self._reset_database_state()
        get_settings.cache_clear()
        now = datetime.now(UTC)
        async with get_session_factory()() as session:
            session.add_all(
                [
                    OperationLog(
                        category="email",
                        event_name="email.sent",
                        level="info",
                        message="CANARY_MESSAGE private@example.test",
                        created_at=now,
                    ),
                    OperationLog(
                        category="email",
                        event_name="email.failed",
                        level="error",
                        message="CANARY_MESSAGE private@example.test",
                        created_at=now,
                    ),
                ]
            )
            await session.commit()

        recorder = BetaDiagnosticsRecorder(
            data_dir=data_dir,
            role="worker",
            app_version="2.6.0-beta.1",
            enabled=True,
            sample_interval_seconds=60,
        )
        await recorder.start()
        recorder.record_timeline("sqlite_lock_error", {"source": "worker"}, "warning")
        recorder.record_timeline(
            "sqlite_slow_query",
            {"source": "sqlalchemy", "elapsed_seconds": 0.75},
            "warning",
        )
        await recorder.stop()

        async with get_session_factory()() as session:
            summary = await build_beta_diagnostics_summary(session, now=now)
        return summary.model_dump(mode="json")

    async def _reset_database_state(self) -> None:
        from app.core.config import get_settings
        from app.core.database import dispose_engine, get_engine, get_session_factory

        if get_engine.cache_info().currsize:
            await dispose_engine()
        get_session_factory.cache_clear()
        get_settings.cache_clear()


if __name__ == "__main__":
    unittest.main()
