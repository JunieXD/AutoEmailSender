from __future__ import annotations

import json
import sqlite3
import tempfile
import urllib.request
import uuid
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from test.process_harness import DesktopBackendProcess, wait_until


class SplitCombinedCompatibilityTests(unittest.TestCase):
    def test_combined_fallback_reads_split_database_and_processes_safe_task(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            data_dir = Path(temp_dir) / "data"
            split_api = DesktopBackendProcess(data_dir=data_dir, role="api")
            split_worker: DesktopBackendProcess | None = None
            combined: DesktopBackendProcess | None = None
            resumed_api: DesktopBackendProcess | None = None
            resumed_worker: DesktopBackendProcess | None = None
            try:
                split_api.start()
                split_api.wait_ready()
                split_worker = DesktopBackendProcess(
                    data_dir=data_dir,
                    role="worker",
                    runtime_id=split_api.runtime_id,
                    api_pid=split_api.process.pid,
                    extra_env={
                        "ENABLE_BACKGROUND_WORKERS": "1",
                        "DISPATCHER_INTERVAL_SECONDS": "30",
                    },
                ).start()
                split_worker.wait_worker_ready()

                settings = _request_json(
                    split_api.base_url,
                    "/api/runtime-settings",
                )
                updated = _patch_runtime_instruction(
                    split_api.base_url,
                    settings,
                    "split-to-combined-preserved",
                )
                self.assertEqual(
                    updated["draft_custom_instruction"],
                    "split-to-combined-preserved",
                )

                split_worker.stop()
                split_worker = None
                split_api.stop()
                # Seed only after the split runtime is fully quiescent. Otherwise
                # its dispatcher can wake under a slow test host and consume the
                # fixture before the combined fallback owns the database.
                task_id = _seed_overdue_manual_task(
                    data_dir / "auto_email_sender.db"
                )

                combined = DesktopBackendProcess(
                    data_dir=data_dir,
                    role="combined",
                    runtime_id=f"combined-{uuid.uuid4()}",
                    extra_env={
                        "ENABLE_BACKGROUND_WORKERS": "1",
                        "DISPATCHER_INTERVAL_SECONDS": "1",
                    },
                ).start()
                combined.wait_ready()
                combined_settings = _request_json(
                    combined.base_url,
                    "/api/runtime-settings",
                )
                self.assertEqual(
                    combined_settings["draft_custom_instruction"],
                    "split-to-combined-preserved",
                )
                try:
                    wait_until(
                        lambda: _task_status(
                            data_dir / "auto_email_sender.db",
                            task_id,
                            "schedule_missed",
                        ),
                        timeout_seconds=30,
                        description="combined fallback processing overdue safe task",
                    )
                except TimeoutError as exc:
                    assert combined.managed is not None
                    self.fail(
                        f"{exc}; current_status="
                        f"{_read_task_status(data_dir / 'auto_email_sender.db', task_id)!r}; "
                        f"stdout={combined.managed.read_stdout()[-4000:]!r}; "
                        f"stderr={combined.managed.read_stderr()[-8000:]!r}"
                    )
                self.assertEqual(_delivery_attempt_count(data_dir, task_id), 0)
                combined.stop()
                combined = None

                resumed_api = DesktopBackendProcess(
                    data_dir=data_dir,
                    role="api",
                    runtime_id=f"resumed-split-{uuid.uuid4()}",
                ).start()
                resumed_api.wait_ready()
                resumed_worker = DesktopBackendProcess(
                    data_dir=data_dir,
                    role="worker",
                    runtime_id=resumed_api.runtime_id,
                    api_pid=resumed_api.process.pid,
                    extra_env={"ENABLE_BACKGROUND_WORKERS": "1"},
                ).start()
                resumed_worker.wait_worker_ready()
                resumed_settings = _request_json(
                    resumed_api.base_url,
                    "/api/runtime-settings",
                )
                self.assertEqual(
                    resumed_settings["draft_custom_instruction"],
                    "split-to-combined-preserved",
                )
                self.assertEqual(
                    _read_task_status(data_dir / "auto_email_sender.db", task_id),
                    "schedule_missed",
                )
                self.assertEqual(_delivery_attempt_count(data_dir, task_id), 0)

                connection = sqlite3.connect(data_dir / "auto_email_sender.db")
                try:
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
                if resumed_worker is not None:
                    resumed_worker.stop()
                if resumed_api is not None:
                    resumed_api.stop()
                if combined is not None:
                    combined.stop()
                if split_worker is not None:
                    split_worker.stop()
                split_api.stop()


def _request_json(base_url: str, path: str, *, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        f"{base_url}{path}",
        data=data,
        method="GET" if data is None else "PATCH",
        headers={"Content-Type": "application/json"} if data is not None else {},
    )
    with urllib.request.urlopen(request, timeout=5) as response:
        result = json.loads(response.read().decode("utf-8"))
    if not isinstance(result, dict):
        raise TypeError(f"Expected object response from {path}")
    return result


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
        payload=payload,
    )


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
                    "Fallback identity",
                    "Fallback identity",
                    "Fallback sender",
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
                (f"Fallback model {suffix}",),
            ).lastrowid
        )
        professor_id = int(
            connection.execute(
                """
                INSERT INTO professors (name, email, research_direction, crawl_status)
                VALUES ('Fallback professor', ?, 'Testing', 'discovered')
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
                VALUES ('manual', ?, ?, ?, 'scheduled', 'Fallback subject',
                        'Fallback body', '[]', ?, ?)
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


def _task_status(database_path: Path, task_id: int, expected: str) -> bool:
    return _read_task_status(database_path, task_id) == expected


def _read_task_status(database_path: Path, task_id: int) -> str:
    connection = sqlite3.connect(database_path, timeout=2)
    try:
        row = connection.execute(
            "SELECT status FROM email_tasks WHERE id = ?",
            (task_id,),
        ).fetchone()
    finally:
        connection.close()
    if row is None:
        raise AssertionError(f"Missing email task {task_id}")
    return str(row[0])


def _delivery_attempt_count(data_dir: Path, task_id: int) -> int:
    connection = sqlite3.connect(data_dir / "auto_email_sender.db", timeout=2)
    try:
        return int(
            connection.execute(
                "SELECT COUNT(*) FROM email_delivery_attempts WHERE email_task_id = ?",
                (task_id,),
            ).fetchone()[0]
        )
    finally:
        connection.close()


if __name__ == "__main__":
    unittest.main()
