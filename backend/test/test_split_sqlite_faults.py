from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import tempfile
import time
import unittest
import urllib.error
import urllib.request
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from test.migrated_database import create_migrated_sqlite_database
from test.process_harness import (
    DesktopBackendProcess,
    fetch_json,
    open_loopback_url,
    wait_until,
)


class SplitSQLiteFaultTests(unittest.TestCase):
    def test_worker_refuses_ready_api_database_when_wal_is_not_active(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            data_dir = Path(temp_dir) / "data"
            api = DesktopBackendProcess(
                data_dir=data_dir,
                role="api",
                extra_env={"SQLITE_ENABLE_WAL": "0"},
            )
            worker: DesktopBackendProcess | None = None
            try:
                api.start()
                api.wait_ready()
                database_path = data_dir / "auto_email_sender.db"
                connection = sqlite3.connect(database_path)
                try:
                    self.assertNotEqual(
                        connection.execute("PRAGMA journal_mode").fetchone()[0].lower(),
                        "wal",
                    )
                finally:
                    connection.close()

                worker = DesktopBackendProcess(
                    data_dir=data_dir,
                    role="worker",
                    runtime_id=api.runtime_id,
                    api_pid=api.process.pid,
                    extra_env={"SQLITE_ENABLE_WAL": "1"},
                ).start()
                self.assertNotEqual(worker.process.wait(timeout=10), 0)
                assert worker.managed is not None
                self.assertIn("WAL", worker.managed.read_stderr())
                self.assertFalse((data_dir / "runtime" / "worker.json").exists())
                self.assertEqual(
                    fetch_json(f"{api.base_url}/health"),
                    {"status": "ok"},
                )
            finally:
                if worker is not None:
                    worker.stop()
                api.stop()

    def test_api_worker_lock_contention_degrades_and_recovers_without_corruption(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            data_dir = Path(temp_dir) / "data"
            common_env = {
                "SQLITE_BUSY_TIMEOUT_MS": "200",
                "DISPATCHER_INTERVAL_SECONDS": "2",
                "IMAP_POLL_INTERVAL_SECONDS": "1",
            }
            api = DesktopBackendProcess(
                data_dir=data_dir,
                role="api",
                extra_env=common_env,
            )
            worker: DesktopBackendProcess | None = None
            lock_connection: sqlite3.Connection | None = None
            try:
                api.start()
                api.wait_ready()
                database_path = data_dir / "auto_email_sender.db"
                worker = DesktopBackendProcess(
                    data_dir=data_dir,
                    role="worker",
                    runtime_id=api.runtime_id,
                    api_pid=api.process.pid,
                    extra_env={
                        **common_env,
                        "ENABLE_BACKGROUND_WORKERS": "1",
                    },
                ).start()
                worker.wait_worker_ready()

                # Generation recovery itself must finish before the Worker is
                # advertised ready.  Introduce contention only after that
                # startup barrier so this test exercises subsystem degradation
                # and recovery rather than a deliberately failed join attempt.
                _seed_overdue_manual_task(database_path)
                lock_connection = sqlite3.connect(database_path, isolation_level=None)
                lock_connection.execute("PRAGMA busy_timeout=0")
                lock_connection.execute("BEGIN IMMEDIATE")

                settings = _request_json(api.base_url, "/api/runtime-settings")
                with self.assertRaises(urllib.error.HTTPError) as raised:
                    _patch_runtime_instruction(
                        api.base_url,
                        settings,
                        "must-fail-while-write-locked",
                    )
                self.assertEqual(raised.exception.code, 500)
                degraded = wait_until(
                    lambda: _worker_status(data_dir, health="degraded"),
                    timeout_seconds=10,
                    description="Worker SQLite lock degradation",
                )
                self.assertTrue(
                    any(
                        subsystem.get("consecutive_failures", 0) > 0
                        and "locked" in str(subsystem.get("error", "")).lower()
                        for subsystem in degraded["subsystems"].values()
                    )
                )

                lock_connection.execute("ROLLBACK")
                lock_connection.close()
                lock_connection = None
                wait_until(
                    lambda: _worker_status(data_dir, health="healthy"),
                    timeout_seconds=10,
                    description="Worker recovery after SQLite lock release",
                )

                recovered = _patch_runtime_instruction(
                    api.base_url,
                    _request_json(api.base_url, "/api/runtime-settings"),
                    "recovered-after-lock",
                )
                self.assertEqual(
                    recovered["draft_custom_instruction"],
                    "recovered-after-lock",
                )

                def exercise_api(worker_index: int) -> None:
                    for iteration in range(12):
                        current = _request_json(
                            api.base_url,
                            "/api/runtime-settings",
                        )
                        if iteration % 4 == 0:
                            updated = _patch_runtime_instruction(
                                api.base_url,
                                current,
                                f"contention-{worker_index}-{iteration}",
                            )
                            self.assertTrue(
                                str(updated["draft_custom_instruction"]).startswith(
                                    "contention-"
                                )
                            )

                with ThreadPoolExecutor(max_workers=8) as executor:
                    futures = [executor.submit(exercise_api, index) for index in range(8)]
                    for future in futures:
                        future.result(timeout=30)

                wait_until(
                    lambda: _worker_status(data_dir, health="healthy"),
                    timeout_seconds=10,
                    description="healthy Worker after concurrent API traffic",
                )
                connection = sqlite3.connect(database_path)
                try:
                    self.assertEqual(
                        connection.execute("PRAGMA journal_mode").fetchone()[0].lower(),
                        "wal",
                    )
                    self.assertEqual(
                        connection.execute("PRAGMA integrity_check").fetchone()[0],
                        "ok",
                    )
                    self.assertEqual(
                        connection.execute("PRAGMA foreign_key_check").fetchall(),
                        [],
                    )
                finally:
                    connection.close()
            finally:
                if lock_connection is not None:
                    lock_connection.execute("ROLLBACK")
                    lock_connection.close()
                if worker is not None:
                    worker.stop()
                api.stop()

    def test_corrupt_database_is_preserved_and_worker_never_becomes_ready(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            runtime_data_dir = root / "runtime-data"
            database_path = root / "database" / "auto_email_sender.db"
            database_path.parent.mkdir(parents=True)
            database_path.write_bytes(
                b"not-a-sqlite-database\x00"
                + bytes(range(256))
                + b"preserve-this-corrupt-source"
            )
            original_digest = _sha256(database_path)
            database_url = _database_url(database_path)
            api = DesktopBackendProcess(
                data_dir=runtime_data_dir,
                role="api",
                extra_env={"DATABASE_URL": database_url},
            )
            worker: DesktopBackendProcess | None = None
            try:
                api.start()
                status = _wait_for_startup_error(api)
                self.assertRegex(
                    str(status.get("error", "")).lower(),
                    r"not a database|malformed|database",
                )
                self.assertEqual(_sha256(database_path), original_digest)
                self.assertFalse((runtime_data_dir / "runtime" / "worker.json").exists())

                worker = DesktopBackendProcess(
                    data_dir=runtime_data_dir,
                    role="worker",
                    runtime_id=api.runtime_id,
                    api_pid=api.process.pid,
                    extra_env={"DATABASE_URL": database_url},
                ).start()
                self.assertNotEqual(worker.process.wait(timeout=10), 0)
                self.assertEqual(_sha256(database_path), original_digest)
            finally:
                if worker is not None:
                    worker.stop()
                api.stop()

            self.assertEqual(_sha256(database_path), original_digest)
            self.assertEqual(
                list((runtime_data_dir / "backups" / "schema").glob("*.db")),
                [],
            )

    @unittest.skipIf(os.name == "nt", "POSIX permission fault; Windows is covered by packaged QA")
    def test_read_only_database_cannot_start_partial_split_runtime(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            runtime_data_dir = root / "runtime-data"
            database_dir = root / "read-only-database"
            database_path = database_dir / "auto_email_sender.db"
            create_migrated_sqlite_database(database_path)
            original_digest = _sha256(database_path)
            database_url = _database_url(database_path)
            database_path.chmod(0o444)
            database_dir.chmod(0o555)

            api = DesktopBackendProcess(
                data_dir=runtime_data_dir,
                role="api",
                extra_env={"DATABASE_URL": database_url},
            )
            worker: DesktopBackendProcess | None = None
            try:
                api.start()
                status = _wait_for_startup_error(api)
                self.assertTrue(str(status.get("error", "")).strip())
                self.assertEqual(_sha256(database_path), original_digest)
                self.assertFalse(database_path.with_name(f"{database_path.name}-wal").exists())
                self.assertFalse(database_path.with_name(f"{database_path.name}-shm").exists())

                worker = DesktopBackendProcess(
                    data_dir=runtime_data_dir,
                    role="worker",
                    runtime_id=api.runtime_id,
                    api_pid=api.process.pid,
                    extra_env={"DATABASE_URL": database_url},
                ).start()
                self.assertNotEqual(worker.process.wait(timeout=10), 0)
                self.assertEqual(_sha256(database_path), original_digest)
            finally:
                if worker is not None:
                    worker.stop()
                api.stop()
                database_dir.chmod(0o755)
                database_path.chmod(0o644)

            startup_log = (runtime_data_dir / "logs" / "startup.log").read_text(
                encoding="utf-8",
                errors="replace",
            )
            self.assertRegex(
                startup_log.lower(),
                r"readonly|read-only|unable to open|disk i/o|attempt to write",
            )
            self.assertEqual(_sha256(database_path), original_digest)

    @unittest.skipIf(os.name == "nt", "RLIMIT_FSIZE disk-full fault is POSIX-only")
    def test_disk_full_during_cold_recovery_preserves_database_and_never_starts_worker(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            runtime_data_dir = root / "runtime-data"
            database_path = root / "database" / "auto_email_sender.db"
            _prepare_wal_database_with_expired_operation_log(database_path)
            original_digest = _sha256(database_path)
            database_url = _database_url(database_path)
            limited_entry = (
                Path(__file__).parent
                / "fixtures"
                / "file_size_limited_desktop_entry.py"
            )
            api = DesktopBackendProcess(
                data_dir=runtime_data_dir,
                role="api",
                extra_env={
                    "DATABASE_URL": database_url,
                    "AUTO_EMAIL_SENDER_TEST_FILE_SIZE_LIMIT_BYTES": "4096",
                },
                entry_script=limited_entry,
            )
            worker: DesktopBackendProcess | None = None
            recovery_api: DesktopBackendProcess | None = None
            recovery_worker: DesktopBackendProcess | None = None
            try:
                api.start()
                status = _wait_for_startup_error(api)
                self.assertRegex(
                    str(status.get("error", "")).lower(),
                    r"database or disk is full|disk full|disk i/o|file too large",
                )
                self.assertEqual(_sha256(database_path), original_digest)
                failed_updated_at = status["updated_at"]

                worker = DesktopBackendProcess(
                    data_dir=runtime_data_dir,
                    role="worker",
                    runtime_id=api.runtime_id,
                    api_pid=api.process.pid,
                    extra_env={"DATABASE_URL": database_url},
                ).start()
                self.assertNotEqual(worker.process.wait(timeout=10), 0)
                self.assertFalse((runtime_data_dir / "runtime" / "worker.json").exists())

                time.sleep(1.0)
                stable_status = fetch_json(f"{api.base_url}/startup-status")
                self.assertEqual(stable_status["updated_at"], failed_updated_at)
                self.assertEqual(_sha256(database_path), original_digest)

                worker.stop()
                worker = None
                api.stop()

                recovery_api = DesktopBackendProcess(
                    data_dir=runtime_data_dir,
                    role="api",
                    extra_env={"DATABASE_URL": database_url},
                ).start()
                recovery_api.wait_ready()
                recovery_worker = DesktopBackendProcess(
                    data_dir=runtime_data_dir,
                    role="worker",
                    runtime_id=recovery_api.runtime_id,
                    api_pid=recovery_api.process.pid,
                    extra_env={"DATABASE_URL": database_url},
                ).start()
                recovery_worker.wait_worker_ready()
                _assert_database_integrity(database_path)
            finally:
                if recovery_worker is not None:
                    recovery_worker.stop()
                if recovery_api is not None:
                    recovery_api.stop()
                if worker is not None:
                    worker.stop()
                api.stop()

    def test_wal_and_shm_creation_failures_preserve_database_and_block_worker(
        self,
    ) -> None:
        for sidecar_suffix in ("-wal", "-shm"):
            with self.subTest(sidecar=sidecar_suffix), tempfile.TemporaryDirectory() as temp_dir:
                root = Path(temp_dir)
                runtime_data_dir = root / "runtime-data"
                database_path = root / "database" / "auto_email_sender.db"
                _prepare_wal_database_with_expired_operation_log(database_path)
                original_digest = _sha256(database_path)
                database_url = _database_url(database_path)
                blocked_sidecar = database_path.with_name(
                    f"{database_path.name}{sidecar_suffix}"
                )
                blocked_sidecar.mkdir()
                api = DesktopBackendProcess(
                    data_dir=runtime_data_dir,
                    role="api",
                    extra_env={"DATABASE_URL": database_url},
                )
                worker: DesktopBackendProcess | None = None
                recovery_api: DesktopBackendProcess | None = None
                recovery_worker: DesktopBackendProcess | None = None
                try:
                    api.start()
                    status = _wait_for_startup_error(api)
                    self.assertRegex(
                        str(status.get("error", "")).lower(),
                        r"disk i/o|unable to open|wal|shm|database",
                    )
                    self.assertEqual(_sha256(database_path), original_digest)
                    failed_updated_at = status["updated_at"]

                    worker = DesktopBackendProcess(
                        data_dir=runtime_data_dir,
                        role="worker",
                        runtime_id=api.runtime_id,
                        api_pid=api.process.pid,
                        extra_env={"DATABASE_URL": database_url},
                    ).start()
                    self.assertNotEqual(worker.process.wait(timeout=10), 0)
                    self.assertFalse(
                        (runtime_data_dir / "runtime" / "worker.json").exists()
                    )

                    time.sleep(1.0)
                    stable_status = fetch_json(f"{api.base_url}/startup-status")
                    self.assertEqual(stable_status["updated_at"], failed_updated_at)
                    self.assertEqual(_sha256(database_path), original_digest)

                    worker.stop()
                    worker = None
                    api.stop()
                    blocked_sidecar.rmdir()

                    recovery_api = DesktopBackendProcess(
                        data_dir=runtime_data_dir,
                        role="api",
                        extra_env={"DATABASE_URL": database_url},
                    ).start()
                    recovery_api.wait_ready()
                    recovery_worker = DesktopBackendProcess(
                        data_dir=runtime_data_dir,
                        role="worker",
                        runtime_id=recovery_api.runtime_id,
                        api_pid=recovery_api.process.pid,
                        extra_env={"DATABASE_URL": database_url},
                    ).start()
                    recovery_worker.wait_worker_ready()
                    _assert_database_integrity(database_path)
                finally:
                    if recovery_worker is not None:
                        recovery_worker.stop()
                    if recovery_api is not None:
                        recovery_api.stop()
                    if worker is not None:
                        worker.stop()
                    api.stop()
                    if blocked_sidecar.is_dir():
                        blocked_sidecar.rmdir()


def _wait_for_startup_error(api: DesktopBackendProcess) -> dict[str, Any]:
    def probe() -> dict[str, Any] | None:
        if api.process.poll() is not None:
            stderr = api.managed.read_stderr() if api.managed is not None else ""
            raise RuntimeError(
                f"API exited before publishing startup error: {stderr[-2000:]}"
            )
        try:
            status = fetch_json(f"{api.base_url}/startup-status")
        except Exception:
            return None
        return status if status.get("state") == "error" else None

    return wait_until(
        probe,
        timeout_seconds=30,
        description="API startup error",
    )


def _database_url(database_path: Path) -> str:
    return f"sqlite+aiosqlite:///{database_path.as_posix()}"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _prepare_wal_database_with_expired_operation_log(database_path: Path) -> None:
    create_migrated_sqlite_database(database_path)
    connection = sqlite3.connect(database_path)
    try:
        self_mode = connection.execute("PRAGMA journal_mode=WAL").fetchone()
        if self_mode is None or str(self_mode[0]).lower() != "wal":
            raise AssertionError(f"Failed to prepare WAL database: {self_mode!r}")
        connection.execute(
            """
            INSERT INTO operation_logs (
                request_id, category, event_name, level, message, created_at
            )
            VALUES (NULL, 'test', 'expired.for.disk.fault', 'info', 'expired', ?)
            """,
            ("2000-01-01 00:00:00.000000",),
        )
        connection.commit()
        connection.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
    finally:
        connection.close()
    for suffix in ("-wal", "-shm"):
        database_path.with_name(f"{database_path.name}{suffix}").unlink(
            missing_ok=True
        )


def _assert_database_integrity(database_path: Path) -> None:
    connection = sqlite3.connect(database_path)
    try:
        assert connection.execute("PRAGMA integrity_check").fetchone() == ("ok",)
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
    finally:
        connection.close()


def _seed_overdue_manual_task(database_path: Path) -> int:
    connection = sqlite3.connect(database_path, timeout=10)
    try:
        connection.execute("PRAGMA foreign_keys = ON")
        suffix = uuid.uuid4().hex
        identity_id = int(
            connection.execute(
                """
                INSERT INTO identity_profiles (
                    name, profile_name, sender_name, email_address,
                    smtp_host, smtp_port, smtp_username, smtp_password,
                    send_interval_min, send_interval_max
                )
                VALUES (?, ?, ?, ?, '127.0.0.1', 1, ?, 'unused', 1, 1)
                """,
                (
                    "Lock identity",
                    "Lock identity",
                    "Lock sender",
                    f"sender-{suffix}@example.com",
                    f"sender-{suffix}@example.com",
                ),
            ).lastrowid
        )
        llm_profile_id = int(
            connection.execute(
                """
                INSERT INTO llm_profiles (name, provider, api_key, model_name)
                VALUES (?, 'openai', 'unused', 'unused')
                """,
                (f"Lock model {suffix}",),
            ).lastrowid
        )
        professor_id = int(
            connection.execute(
                """
                INSERT INTO professors (name, email, research_direction, crawl_status)
                VALUES ('Lock professor', ?, 'Testing', 'discovered')
                """,
                (f"professor-{suffix}@example.edu",),
            ).lastrowid
        )
        task_id = int(
            connection.execute(
                """
                INSERT INTO email_tasks (
                    source, identity_id, llm_profile_id, professor_id,
                    status, approved_subject, approved_body_text,
                    selected_material_ids, approved_at, scheduled_at
                )
                VALUES ('manual', ?, ?, ?, 'scheduled', 'Lock subject',
                        'Lock body', '[]', ?, ?)
                """,
                (
                    identity_id,
                    llm_profile_id,
                    professor_id,
                    datetime.now(UTC).replace(tzinfo=None).isoformat(sep=" "),
                    (datetime.now(UTC) - timedelta(hours=2))
                    .replace(tzinfo=None)
                    .isoformat(sep=" "),
                ),
            ).lastrowid
        )
        connection.commit()
        return task_id
    finally:
        connection.close()


def _worker_status(data_dir: Path, *, health: str) -> dict[str, Any] | None:
    try:
        status = json.loads(
            (data_dir / "runtime" / "worker.json").read_text(encoding="utf-8")
        )
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return None
    if not isinstance(status, dict) or status.get("health") != health:
        return None
    return status


def _request_json(
    base_url: str,
    path: str,
    *,
    method: str = "GET",
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        f"{base_url}{path}",
        data=data,
        method=method,
        headers={"Content-Type": "application/json"} if data is not None else {},
    )
    with open_loopback_url(request, timeout_seconds=5) as response:
        body = json.loads(response.read().decode("utf-8"))
    if not isinstance(body, dict):
        raise TypeError(f"Expected object response from {path}")
    return body


def _patch_runtime_instruction(
    base_url: str,
    settings: dict[str, Any],
    instruction: str,
) -> dict[str, Any]:
    payload = {
        key: value
        for key, value in settings.items()
        if key not in {"updated_at", "revision"}
    }
    payload["draft_custom_instruction"] = instruction
    return _request_json(
        base_url,
        "/api/runtime-settings",
        method="PATCH",
        payload=payload,
    )


if __name__ == "__main__":
    unittest.main()
