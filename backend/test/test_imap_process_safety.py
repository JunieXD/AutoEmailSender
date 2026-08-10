from __future__ import annotations

import os
import json
import sqlite3
import tempfile
import time
import unittest
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from test.process_harness import (
    DesktopBackendProcess,
    FakeIMAPServer,
    FakeImapMessage,
    FaultController,
    fetch_json,
    patch_json,
    wait_until,
)


_REPETITIONS_ENV = "AUTO_EMAIL_SENDER_IMAP_CHAOS_REPETITIONS"
_MESSAGE_UID = 11
_UIDVALIDITY = 7001


class ImapProcessSafetyTests(unittest.TestCase):
    def test_incremental_worker_kill_matrix_converges_without_duplicates(self) -> None:
        fault_points = (
            "imap_incremental.before_claim",
            "imap_incremental.claim_committed",
            "imap_incremental.before_external_call",
            "imap_incremental.external_call_returned",
            "imap_incremental.before_final_commit",
            "imap_incremental.after_final_commit",
        )
        for repetition in range(1, self._chaos_repetitions() + 1):
            for fault_point in fault_points:
                with self.subTest(repetition=repetition, fault_point=fault_point):
                    self._exercise_kill_case(
                        workload="incremental",
                        fault_point=fault_point,
                    )

    def test_history_worker_kill_matrix_converges_without_duplicates(self) -> None:
        fault_points = (
            "imap_history.before_claim",
            "imap_history.claim_committed",
            "imap_history.before_external_call",
            "imap_history.external_call_returned",
            "imap_history.before_final_commit",
            "imap_history.after_final_commit",
        )
        for repetition in range(1, self._chaos_repetitions() + 1):
            for fault_point in fault_points:
                with self.subTest(repetition=repetition, fault_point=fault_point):
                    self._exercise_kill_case(
                        workload="history",
                        fault_point=fault_point,
                    )

    def test_incremental_rejects_a_replaced_lease_owner_result(self) -> None:
        for repetition in range(1, self._chaos_repetitions() + 1):
            with self.subTest(repetition=repetition):
                self._exercise_replaced_owner_case("incremental")

    def test_history_rejects_a_replaced_lease_owner_result(self) -> None:
        for repetition in range(1, self._chaos_repetitions() + 1):
            with self.subTest(repetition=repetition):
                self._exercise_replaced_owner_case("history")

    def test_incremental_network_outage_degrades_without_restart_and_recovers(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            data_dir = root / "data"
            message_id = f"<imap-network-{uuid.uuid4().hex}@example.edu>"
            message = FakeImapMessage(
                _MESSAGE_UID,
                self._raw_message(message_id),
            )
            initial_server = FakeIMAPServer(
                [message],
                uidvalidity=_UIDVALIDITY,
            ).start()
            restored_server: FakeIMAPServer | None = None
            api = DesktopBackendProcess(
                data_dir=data_dir,
                role="api",
                runtime_id=f"imap-network-{uuid.uuid4()}",
            )
            worker: DesktopBackendProcess | None = None
            try:
                api.start()
                api.wait_ready()
                identity_id, professor_id = self._seed_imap_workload(
                    data_dir / "auto_email_sender.db",
                    imap_port=initial_server.port,
                    workload="incremental",
                )
                imap_port = initial_server.port
                initial_server.stop()

                worker = DesktopBackendProcess(
                    data_dir=data_dir,
                    role="worker",
                    runtime_id=api.runtime_id,
                    api_pid=api.process.pid,
                    worker_generation=f"imap-network-{uuid.uuid4()}",
                    extra_env=self._worker_environment("incremental"),
                ).start()
                ready_status = worker.wait_worker_ready()
                worker_pid = worker.process.pid

                outage_state = wait_until(
                    lambda: self._incremental_network_failure_state(
                        data_dir / "auto_email_sender.db",
                        identity_id=identity_id,
                        professor_id=professor_id,
                    ),
                    timeout_seconds=20,
                    description="IMAP network failure persistence",
                )
                self.assertEqual(outage_state["incremental_cursor"], 10)
                self.assertEqual(outage_state["email_log_count"], 0)
                self.assertIsNone(outage_state["identity_claim_id"])
                degraded = wait_until(
                    lambda: self._imap_worker_health(data_dir, degraded=True),
                    timeout_seconds=15,
                    description="IMAP degraded worker status",
                )
                self.assertEqual(degraded["pid"], worker_pid)

                settings_payload = fetch_json(
                    f"{api.base_url}/api/runtime-settings"
                )
                settings_payload.pop("revision", None)
                settings_payload.pop("updated_at", None)
                settings_payload["draft_custom_instruction"] = (
                    "api-write-during-imap-network-outage"
                )
                updated = patch_json(
                    f"{api.base_url}/api/runtime-settings",
                    settings_payload,
                )
                self.assertEqual(
                    updated["draft_custom_instruction"],
                    "api-write-during-imap-network-outage",
                )

                restored_server = FakeIMAPServer(
                    [message],
                    uidvalidity=_UIDVALIDITY,
                    port=imap_port,
                ).start()
                final_state = self._wait_for_final_state(
                    data_dir / "auto_email_sender.db",
                    identity_id=identity_id,
                    professor_id=professor_id,
                    workload="incremental",
                )
                self._assert_final_state("incremental", final_state)
                recovered = wait_until(
                    lambda: self._imap_worker_health(data_dir, degraded=False),
                    timeout_seconds=15,
                    description="IMAP recovered worker status",
                )
                self.assertEqual(recovered["pid"], worker_pid)
                self.assertEqual(recovered["generation"], ready_status["generation"])
                self.assertIsNone(worker.process.poll())
                self.assertEqual(
                    fetch_json(f"{api.base_url}/startup-status")["state"],
                    "ready",
                )
            finally:
                if worker is not None:
                    worker.stop()
                api.stop()
                if restored_server is not None:
                    restored_server.stop()
                initial_server.stop()

    def _exercise_kill_case(self, *, workload: str, fault_point: str) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            data_dir = root / "data"
            fault_controller = FaultController(root / "faults")
            message_id = f"<imap-{uuid.uuid4().hex}@example.edu>"
            with FakeIMAPServer(
                [FakeImapMessage(_MESSAGE_UID, self._raw_message(message_id))],
                uidvalidity=_UIDVALIDITY,
            ) as imap_server:
                api = DesktopBackendProcess(
                    data_dir=data_dir,
                    role="api",
                    runtime_id=f"imap-{workload}-{uuid.uuid4()}",
                )
                fault_worker: DesktopBackendProcess | None = None
                recovery_worker: DesktopBackendProcess | None = None
                try:
                    api.start()
                    api.wait_ready()
                    identity_id, professor_id = self._seed_imap_workload(
                        data_dir / "auto_email_sender.db",
                        imap_port=imap_server.port,
                        workload=workload,
                    )
                    fault_worker = DesktopBackendProcess(
                        data_dir=data_dir,
                        role="worker",
                        runtime_id=api.runtime_id,
                        api_pid=api.process.pid,
                        worker_generation=f"fault-{uuid.uuid4()}",
                        extra_env={
                            **self._worker_environment(workload),
                            **fault_controller.environment(
                                fault_point,
                                process_id=f"imap-{workload}-worker",
                                timeout_seconds=60,
                            ),
                        },
                    ).start()
                    fault_worker.wait_worker_ready()
                    fault_controller.wait_for_reached(
                        fault_point,
                        timeout_seconds=30,
                    )

                    fault_state = self._read_state(
                        data_dir / "auto_email_sender.db",
                        identity_id=identity_id,
                        professor_id=professor_id,
                    )
                    self._assert_fault_boundary_state(
                        workload=workload,
                        fault_point=fault_point,
                        state=fault_state,
                        fetch_count=imap_server.fetch_count,
                    )

                    fault_worker.process.kill()
                    fault_worker.process.wait(timeout=10)
                    self.assertEqual(
                        fetch_json(f"{api.base_url}/startup-status")["state"],
                        "ready",
                    )
                    self._move_imap_claims_far_into_future(
                        data_dir / "auto_email_sender.db",
                        identity_id=identity_id,
                    )

                    recovery_worker = DesktopBackendProcess(
                        data_dir=data_dir,
                        role="worker",
                        runtime_id=api.runtime_id,
                        api_pid=api.process.pid,
                        worker_generation=f"recovery-{uuid.uuid4()}",
                        extra_env=self._worker_environment(workload),
                    ).start()
                    recovery_worker.wait_worker_ready()
                    final_state = self._wait_for_final_state(
                        data_dir / "auto_email_sender.db",
                        identity_id=identity_id,
                        professor_id=professor_id,
                        workload=workload,
                    )
                    self._assert_final_state(workload, final_state)
                    self.assertEqual(
                        fetch_json(f"{api.base_url}/startup-status")["state"],
                        "ready",
                    )
                    time.sleep(0.15)
                    stable_state = self._read_state(
                        data_dir / "auto_email_sender.db",
                        identity_id=identity_id,
                        professor_id=professor_id,
                    )
                    self._assert_final_state(workload, stable_state)
                finally:
                    if recovery_worker is not None:
                        recovery_worker.stop()
                    if fault_worker is not None:
                        fault_worker.stop()
                    api.stop()

    def _exercise_replaced_owner_case(self, workload: str) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            data_dir = root / "data"
            fault_controller = FaultController(root / "faults")
            fault_point = f"imap_{workload}.external_call_returned"
            message_id = f"<imap-stale-{uuid.uuid4().hex}@example.edu>"
            with FakeIMAPServer(
                [FakeImapMessage(_MESSAGE_UID, self._raw_message(message_id))],
                uidvalidity=_UIDVALIDITY,
            ) as imap_server:
                api = DesktopBackendProcess(
                    data_dir=data_dir,
                    role="api",
                    runtime_id=f"imap-stale-{workload}-{uuid.uuid4()}",
                )
                stale_worker: DesktopBackendProcess | None = None
                recovery_worker: DesktopBackendProcess | None = None
                try:
                    api.start()
                    api.wait_ready()
                    identity_id, professor_id = self._seed_imap_workload(
                        data_dir / "auto_email_sender.db",
                        imap_port=imap_server.port,
                        workload=workload,
                    )
                    stale_worker = DesktopBackendProcess(
                        data_dir=data_dir,
                        role="worker",
                        runtime_id=api.runtime_id,
                        api_pid=api.process.pid,
                        worker_generation=f"stale-{uuid.uuid4()}",
                        extra_env={
                            **self._worker_environment(workload),
                            **fault_controller.environment(
                                fault_point,
                                process_id=f"imap-stale-{workload}",
                                timeout_seconds=60,
                            ),
                        },
                    ).start()
                    stale_worker.wait_worker_ready()
                    reached_path = fault_controller.wait_for_reached(
                        fault_point,
                        timeout_seconds=30,
                    )
                    before_state = self._read_state(
                        data_dir / "auto_email_sender.db",
                        identity_id=identity_id,
                        professor_id=professor_id,
                    )
                    self.assertEqual(before_state["email_log_count"], 0)
                    replacement_claim = self._replace_identity_claim(
                        data_dir / "auto_email_sender.db",
                        identity_id=identity_id,
                        workload=workload,
                    )

                    fault_controller.release(reached_path)
                    wait_until(
                        reached_path.with_suffix(".completed").exists,
                        timeout_seconds=10,
                        description="released stale IMAP owner fault point",
                    )
                    rejected_state = wait_until(
                        lambda: self._read_rejected_owner_state(
                            data_dir / "auto_email_sender.db",
                            identity_id=identity_id,
                            professor_id=professor_id,
                            replacement_claim=replacement_claim,
                        ),
                        timeout_seconds=15,
                        description="stale IMAP owner result rejection",
                    )
                    self.assertEqual(rejected_state["email_log_count"], 0)
                    self.assertEqual(
                        rejected_state["incremental_cursor"],
                        before_state["incremental_cursor"],
                    )
                    self.assertEqual(
                        rejected_state["history_cursor"],
                        before_state["history_cursor"],
                    )
                    self.assertEqual(
                        fetch_json(f"{api.base_url}/startup-status")["state"],
                        "ready",
                    )

                    stale_worker.stop()
                    self._move_imap_claims_far_into_future(
                        data_dir / "auto_email_sender.db",
                        identity_id=identity_id,
                    )
                    recovery_worker = DesktopBackendProcess(
                        data_dir=data_dir,
                        role="worker",
                        runtime_id=api.runtime_id,
                        api_pid=api.process.pid,
                        worker_generation=f"replacement-{uuid.uuid4()}",
                        extra_env=self._worker_environment(workload),
                    ).start()
                    recovery_worker.wait_worker_ready()
                    final_state = self._wait_for_final_state(
                        data_dir / "auto_email_sender.db",
                        identity_id=identity_id,
                        professor_id=professor_id,
                        workload=workload,
                    )
                    self._assert_final_state(workload, final_state)
                finally:
                    if recovery_worker is not None:
                        recovery_worker.stop()
                    if stale_worker is not None:
                        stale_worker.stop()
                    api.stop()

    def _assert_fault_boundary_state(
        self,
        *,
        workload: str,
        fault_point: str,
        state: dict[str, Any],
        fetch_count: int,
    ) -> None:
        stage = fault_point.rsplit(".", 1)[-1]
        if stage in {"claim_committed", "before_external_call", "external_call_returned", "before_final_commit", "after_final_commit"}:
            self.assertEqual(state["identity_claim_kind"], workload)
            self.assertIsNotNone(state["identity_claim_id"])
        if stage in {"before_claim", "claim_committed", "before_external_call"}:
            self.assertEqual(fetch_count, 0)
        else:
            self.assertGreaterEqual(fetch_count, 1)

        if workload == "incremental":
            expected_committed = stage == "after_final_commit"
            expected_ingested = stage in {"before_final_commit", "after_final_commit"}
            self.assertEqual(state["incremental_cursor"], 11 if expected_committed else 10)
            self.assertEqual(state["email_log_count"], 1 if expected_ingested else 0)
            return

        if stage in {"before_claim", "claim_committed"}:
            expected_status = "pending"
        elif stage == "after_final_commit":
            expected_status = "completed"
        else:
            expected_status = "running"
        self.assertEqual(state["history_status"], expected_status)
        self.assertEqual(
            state["history_cursor"],
            11 if stage == "after_final_commit" else 5,
        )
        self.assertEqual(
            state["email_log_count"],
            1 if stage in {"before_final_commit", "after_final_commit"} else 0,
        )

    def _assert_final_state(self, workload: str, state: dict[str, Any]) -> None:
        self.assertEqual(state["integrity_check"], "ok")
        self.assertEqual(state["email_log_count"], 1)
        self.assertEqual(state["distinct_imap_location_count"], 1)
        self.assertEqual(state["logged_uid"], _MESSAGE_UID)
        self.assertEqual(state["logged_uidvalidity"], _UIDVALIDITY)
        if workload == "incremental":
            self.assertEqual(state["incremental_cursor"], _MESSAGE_UID)
        else:
            self.assertEqual(state["history_status"], "completed")
            self.assertEqual(state["history_cursor"], _MESSAGE_UID)
            self.assertIsNone(state["history_claim_id"])
            self.assertIsNone(state["history_lease_expires_at"])

    @staticmethod
    def _raw_message(
        message_id: str,
        *,
        professor_email: str = "professor@example.edu",
    ) -> bytes:
        return (
            f"From: Professor <{professor_email}>\r\n".encode("ascii")
            + b"To: Student <student@example.com>\r\n"
            b"Subject: Re: Hello\r\n"
            + f"Message-ID: {message_id}\r\n".encode("ascii")
            + b"In-Reply-To: <sent@example.com>\r\n"
            b"References: <sent@example.com>\r\n"
            b"Date: Sun, 09 Aug 2026 12:00:00 +0000\r\n"
            b"Content-Type: text/plain; charset=utf-8\r\n"
            b"\r\n"
            b"Deterministic IMAP reply.\r\n"
        )

    @staticmethod
    def _worker_environment(workload: str) -> dict[str, str]:
        environment = {
            "ENABLE_BACKGROUND_WORKERS": "1",
            "DRAFT_WORKER_INTERVAL_SECONDS": "3600",
            "DISPATCHER_INTERVAL_SECONDS": "3600",
            "IMAP_POLL_INTERVAL_SECONDS": "1",
            "IMAP_IDENTITY_LEASE_SECONDS": "60",
            "IMAP_IDENTITY_SYNC_TIMEOUT_SECONDS": "45",
            "IMAP_HISTORY_BATCH_SIZE": "10",
            "IMAP_HISTORY_COMMAND_BUDGET_PER_MINUTE": "10000",
            "IMAP_HISTORY_COMMAND_RATE_PER_MINUTE": "10000",
            "IMAP_HISTORY_COMMAND_BURST": "10000",
            "IMAP_HISTORY_QUEUE_SETTLE_SECONDS": "0",
            "IMAP_FETCH_BATCH_SIZE": "20",
            "IMAP_SENT_FOLDER_FAILURE_TTL_SECONDS": "3600",
            "MATCH_ANALYSIS_JOB_INTERVAL_SECONDS": "3600",
        }
        if workload == "incremental":
            environment["IMAP_HISTORY_BATCH_SIZE"] = "0"
        return environment

    @staticmethod
    def _seed_imap_workload(
        database_path: Path,
        *,
        imap_port: int,
        workload: str,
        professor_email: str = "professor@example.edu",
    ) -> tuple[int, int]:
        connection = sqlite3.connect(database_path, timeout=10)
        try:
            connection.execute("PRAGMA foreign_keys = ON")
            suffix = uuid.uuid4().hex
            now = datetime.now(UTC).replace(tzinfo=None)
            identity_id = int(
                connection.execute(
                    """
                    INSERT INTO identity_profiles (
                        name, profile_name, sender_name, email_address,
                        smtp_host, smtp_port, smtp_username, smtp_password,
                        imap_host, imap_port, imap_username, imap_password
                    )
                    VALUES ('IMAP process identity', 'IMAP process identity',
                            'Student', ?, 'smtp.example.com', 465, ?, 'secret',
                            '127.0.0.1', ?, ?, 'secret')
                    """,
                    (
                        f"student-{suffix}@example.com",
                        f"student-{suffix}@example.com",
                        imap_port,
                        f"student-{suffix}@example.com",
                    ),
                ).lastrowid
            )
            professor_id = int(
                connection.execute(
                    """
                    INSERT INTO professors (
                        name, email, title, university, school, department,
                        research_direction, recent_papers, crawl_status,
                        communication_sync_version
                    )
                    VALUES ('IMAP Professor', ?, 'Professor',
                            'Example University', 'School of AI',
                            'Computer Science', 'Distributed systems', '[]',
                            'discovered', 1)
                    """,
                    (professor_email,),
                ).lastrowid
            )
            connection.execute(
                """
                INSERT INTO imap_mailbox_sync_states (
                    identity_id, folder_role, folder, uidvalidity,
                    last_seen_uid, history_scan_status,
                    history_high_water_uid, history_next_before_uid
                )
                VALUES (?, 'inbox', 'INBOX', ?, ?, 'completed', ?, 0)
                """,
                (
                    identity_id,
                    _UIDVALIDITY,
                    10 if workload == "incremental" else 11,
                    10 if workload == "incremental" else 11,
                ),
            )
            connection.execute(
                """
                INSERT INTO imap_mailbox_sync_states (
                    identity_id, folder_role, folder,
                    sent_folder_discovery_failed_at,
                    sent_folder_discovery_error,
                    history_scan_status
                )
                VALUES (?, 'sent', 'Sent', ?, 'disabled in process test', 'completed')
                """,
                (identity_id, now.isoformat(sep=" ")),
            )
            if workload == "history":
                connection.execute(
                    """
                    INSERT INTO imap_professor_sync_states (
                        identity_id, professor_id, professor_email,
                        folder_role, folder, historical_scan_status,
                        last_scanned_uid, history_strategy_version,
                        history_start_date, trigger_reason, batch_id,
                        available_at, priority, professor_sync_version
                    )
                    VALUES (?, ?, ?, 'inbox', 'INBOX',
                            'pending', 5, 'recent-v2', '2025-01-01',
                            'process_test', ?, ?, 100, 1)
                    """,
                    (
                        identity_id,
                        professor_id,
                        professor_email,
                        f"queue:{suffix}",
                        (now - timedelta(seconds=1)).isoformat(sep=" "),
                    ),
                )
            connection.commit()
            return identity_id, professor_id
        finally:
            connection.close()

    @staticmethod
    def _move_imap_claims_far_into_future(
        database_path: Path,
        *,
        identity_id: int,
    ) -> None:
        connection = sqlite3.connect(database_path, timeout=10)
        try:
            future_lease = (datetime.now(UTC) + timedelta(days=365)).replace(
                tzinfo=None
            ).isoformat(sep=" ")
            connection.execute(
                """
                UPDATE imap_identity_sync_leases
                SET lease_expires_at = ?
                WHERE identity_id = ? AND claim_id IS NOT NULL
                """,
                (future_lease, identity_id),
            )
            connection.execute(
                """
                UPDATE imap_professor_sync_states
                SET history_lease_expires_at = ?
                WHERE identity_id = ? AND history_claim_id IS NOT NULL
                """,
                (future_lease, identity_id),
            )
            connection.commit()
        finally:
            connection.close()

    @staticmethod
    def _replace_identity_claim(
        database_path: Path,
        *,
        identity_id: int,
        workload: str,
    ) -> str:
        replacement_claim = str(uuid.uuid4())
        connection = sqlite3.connect(database_path, timeout=10)
        try:
            now = datetime.now(UTC).replace(tzinfo=None)
            expired_at = (now - timedelta(seconds=1)).isoformat(sep=" ")
            lease_expires_at = (now + timedelta(minutes=1)).isoformat(sep=" ")
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                UPDATE imap_identity_sync_leases
                SET lease_expires_at = ?
                WHERE identity_id = ?
                """,
                (expired_at, identity_id),
            )
            transition = connection.execute(
                """
                UPDATE imap_identity_sync_leases
                SET claim_id = ?, claim_kind = ?, claimed_at = ?,
                    lease_expires_at = ?, updated_at = ?
                WHERE identity_id = ? AND lease_expires_at <= ?
                """,
                (
                    replacement_claim,
                    workload,
                    now.isoformat(sep=" "),
                    lease_expires_at,
                    now.isoformat(sep=" "),
                    identity_id,
                    now.isoformat(sep=" "),
                ),
            )
            if transition.rowcount != 1:
                raise AssertionError("failed to install replacement IMAP identity claim")
            connection.commit()
            return replacement_claim
        finally:
            connection.close()

    @classmethod
    def _read_rejected_owner_state(
        cls,
        database_path: Path,
        *,
        identity_id: int,
        professor_id: int,
        replacement_claim: str,
    ) -> dict[str, Any] | None:
        state = cls._read_state(
            database_path,
            identity_id=identity_id,
            professor_id=professor_id,
        )
        if state["identity_claim_id"] != replacement_claim:
            return None
        return state if state["email_log_count"] == 0 else None

    @classmethod
    def _wait_for_final_state(
        cls,
        database_path: Path,
        *,
        identity_id: int,
        professor_id: int,
        workload: str,
    ) -> dict[str, Any]:
        def probe() -> dict[str, Any] | None:
            state = cls._read_state(
                database_path,
                identity_id=identity_id,
                professor_id=professor_id,
            )
            if state["email_log_count"] != 1:
                return None
            if workload == "incremental":
                return state if state["incremental_cursor"] == _MESSAGE_UID else None
            return (
                state
                if state["history_status"] == "completed"
                and state["history_cursor"] == _MESSAGE_UID
                else None
            )

        return wait_until(
            probe,
            timeout_seconds=30,
            poll_seconds=0.05,
            description=f"final IMAP {workload} state",
        )

    @staticmethod
    def _read_state(
        database_path: Path,
        *,
        identity_id: int,
        professor_id: int,
    ) -> dict[str, Any]:
        connection = sqlite3.connect(database_path, timeout=10)
        connection.row_factory = sqlite3.Row
        try:
            mailbox = connection.execute(
                """
                SELECT last_seen_uid, last_error
                FROM imap_mailbox_sync_states
                WHERE identity_id = ? AND folder_role = 'inbox' AND folder = 'INBOX'
                """,
                (identity_id,),
            ).fetchone()
            history = connection.execute(
                """
                SELECT historical_scan_status, last_scanned_uid,
                       history_claim_id, history_lease_expires_at
                FROM imap_professor_sync_states
                WHERE identity_id = ? AND professor_id = ?
                      AND folder_role = 'inbox' AND folder = 'INBOX'
                """,
                (identity_id, professor_id),
            ).fetchone()
            identity_claim = connection.execute(
                """
                SELECT claim_id, claim_kind
                FROM imap_identity_sync_leases WHERE identity_id = ?
                """,
                (identity_id,),
            ).fetchone()
            log_summary = connection.execute(
                """
                SELECT COUNT(*) AS total,
                       COUNT(DISTINCT printf('%s:%s:%s:%s',
                           folder_role, folder, uidvalidity, imap_uid)) AS locations,
                       MAX(imap_uid) AS logged_uid,
                       MAX(uidvalidity) AS logged_uidvalidity
                FROM email_logs
                WHERE identity_id = ? AND professor_id = ?
                      AND direction = 'received'
                """,
                (identity_id, professor_id),
            ).fetchone()
            integrity_check = connection.execute("PRAGMA integrity_check").fetchone()[0]
            return {
                "incremental_cursor": mailbox["last_seen_uid"] if mailbox else None,
                "incremental_last_error": mailbox["last_error"] if mailbox else None,
                "history_status": history["historical_scan_status"] if history else None,
                "history_cursor": history["last_scanned_uid"] if history else None,
                "history_claim_id": history["history_claim_id"] if history else None,
                "history_lease_expires_at": (
                    history["history_lease_expires_at"] if history else None
                ),
                "identity_claim_id": (
                    identity_claim["claim_id"] if identity_claim else None
                ),
                "identity_claim_kind": (
                    identity_claim["claim_kind"] if identity_claim else None
                ),
                "email_log_count": int(log_summary["total"] or 0),
                "distinct_imap_location_count": int(log_summary["locations"] or 0),
                "logged_uid": log_summary["logged_uid"],
                "logged_uidvalidity": log_summary["logged_uidvalidity"],
                "integrity_check": integrity_check,
            }
        finally:
            connection.close()

    @classmethod
    def _incremental_network_failure_state(
        cls,
        database_path: Path,
        *,
        identity_id: int,
        professor_id: int,
    ) -> dict[str, Any] | None:
        state = cls._read_state(
            database_path,
            identity_id=identity_id,
            professor_id=professor_id,
        )
        if (
            state["incremental_last_error"]
            and state["incremental_cursor"] == 10
            and state["email_log_count"] == 0
            and state["identity_claim_id"] is None
        ):
            return state
        return None

    @staticmethod
    def _imap_worker_health(
        data_dir: Path,
        *,
        degraded: bool,
    ) -> dict[str, Any] | None:
        try:
            status = json.loads(
                (data_dir / "runtime" / "worker.json").read_text(
                    encoding="utf-8"
                )
            )
        except (FileNotFoundError, OSError, json.JSONDecodeError):
            return None
        subsystems = status.get("subsystems")
        if not isinstance(subsystems, dict):
            return None
        incremental = subsystems.get("imap-incremental-poller")
        if not isinstance(incremental, dict):
            return None
        failures = int(incremental.get("consecutive_failures") or 0)
        expected_health = "degraded" if degraded else "healthy"
        if status.get("health") != expected_health:
            return None
        if degraded and failures <= 0:
            return None
        if not degraded and failures != 0:
            return None
        return status

    @staticmethod
    def _chaos_repetitions() -> int:
        raw_value = os.getenv(_REPETITIONS_ENV, "1")
        try:
            repetitions = int(raw_value)
        except ValueError as exc:
            raise AssertionError(
                f"{_REPETITIONS_ENV} must be an integer, got {raw_value!r}"
            ) from exc
        if not 1 <= repetitions <= 100:
            raise AssertionError(
                f"{_REPETITIONS_ENV} must be between 1 and 100, got {repetitions}"
            )
        return repetitions


if __name__ == "__main__":
    unittest.main()
