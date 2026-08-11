from __future__ import annotations

import json
import http.client
import os
import signal
import sqlite3
import tempfile
import time
import unittest
import urllib.parse
import uuid
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from test.process_harness import (
    DesktopBackendProcess,
    FakeSMTPServer,
    FaultController,
    TestClockController,
    fetch_json,
    wait_until,
)


_DISPATCHER_REPETITIONS_ENV = "AUTO_EMAIL_SENDER_DISPATCHER_CHAOS_REPETITIONS"


class EmailDeliveryProcessSafetyTests(unittest.TestCase):
    @unittest.skipUnless(hasattr(signal, "SIGSTOP"), "requires POSIX process suspension")
    def test_sleep_wake_and_clock_jumps_send_scheduled_mail_once(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            data_dir = root / "data"
            test_clock = TestClockController(root / "test-clock")
            clock_environment = test_clock.environment()
            with FakeSMTPServer(root / "smtp") as smtp_server:
                api = DesktopBackendProcess(
                    data_dir=data_dir,
                    role="api",
                    runtime_id=f"clock-jump-{uuid.uuid4()}",
                    extra_env=clock_environment,
                )
                worker: DesktopBackendProcess | None = None
                worker_suspended = False
                try:
                    api.start()
                    api.wait_ready()
                    task_id = self._seed_delivery_task(
                        data_dir / "auto_email_sender.db",
                        smtp_port=smtp_server.port,
                        status="scheduled",
                        scheduled_at=datetime.now(UTC) + timedelta(hours=1),
                    )
                    worker = DesktopBackendProcess(
                        data_dir=data_dir,
                        role="worker",
                        runtime_id=api.runtime_id,
                        api_pid=api.process.pid,
                        worker_generation=f"clock-worker-{uuid.uuid4()}",
                        extra_env={
                            **clock_environment,
                            "ENABLE_BACKGROUND_WORKERS": "1",
                            "DISPATCHER_INTERVAL_SECONDS": "1",
                            "IMAP_POLL_INTERVAL_SECONDS": "3600",
                            "MATCH_ANALYSIS_JOB_INTERVAL_SECONDS": "3600",
                        },
                    ).start()
                    initial_status = worker.wait_worker_ready()
                    initial_heartbeat = initial_status["heartbeat_at"]
                    time.sleep(1.2)
                    self.assertEqual(smtp_server.accepted_count, 0)

                    os.kill(worker.process.pid, signal.SIGSTOP)
                    worker_suspended = True
                    time.sleep(2.5)
                    self.assertEqual(
                        fetch_json(f"{api.base_url}/startup-status")["state"],
                        "ready",
                    )
                    os.kill(worker.process.pid, signal.SIGCONT)
                    worker_suspended = False

                    status_path = data_dir / "runtime" / "worker.json"
                    resumed_status = wait_until(
                        lambda: (
                            status
                            if (
                                status := json.loads(
                                    status_path.read_text(encoding="utf-8")
                                )
                            ).get("heartbeat_at")
                            != initial_heartbeat
                            else None
                        ),
                        timeout_seconds=10,
                        description="Worker heartbeat after simulated wake",
                    )
                    self.assertEqual(resumed_status["pid"], worker.process.pid)
                    self.assertEqual(smtp_server.accepted_count, 0)

                    test_clock.set_offset_seconds(2 * 60 * 60)
                    final_state = self._wait_for_delivery_outcome(
                        data_dir / "auto_email_sender.db",
                        task_id,
                        "smtp_accepted",
                    )
                    self.assertEqual(final_state["status"], "sent")
                    self.assertEqual(final_state["attempt_count"], 1)
                    self.assertEqual(final_state["delivery_log_count"], 1)
                    self.assertEqual(smtp_server.accepted_count, 1)

                    test_clock.set_offset_seconds(-2 * 60 * 60)
                    time.sleep(2.2)
                    stable_state = self._read_delivery_state(
                        data_dir / "auto_email_sender.db",
                        task_id,
                    )
                    self.assertEqual(stable_state["status"], "sent")
                    self.assertEqual(stable_state["attempt_count"], 1)
                    self.assertEqual(stable_state["delivery_log_count"], 1)
                    self.assertEqual(smtp_server.accepted_count, 1)
                    self.assertIsNone(worker.process.poll())
                    self.assertEqual(
                        fetch_json(f"{api.base_url}/startup-status")["state"],
                        "ready",
                    )
                finally:
                    if worker_suspended and worker is not None:
                        os.kill(worker.process.pid, signal.SIGCONT)
                    if worker is not None:
                        worker.stop()
                    api.stop()

    def test_api_crash_before_claim_leaves_task_safely_retryable_once(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            data_dir = root / "data"
            fault_controller = FaultController(root / "faults")
            with FakeSMTPServer(root / "smtp") as smtp_server:
                fault_api = DesktopBackendProcess(
                    data_dir=data_dir,
                    role="api",
                    extra_env=fault_controller.environment(
                        "delivery.before_claim",
                        process_id="api-before-claim",
                    ),
                )
                retry_api: DesktopBackendProcess | None = None
                executor = ThreadPoolExecutor(max_workers=1)
                request_future: Future[dict[str, Any]] | None = None
                try:
                    fault_api.start()
                    fault_api.wait_ready()
                    task_id = self._seed_delivery_task(
                        data_dir / "auto_email_sender.db",
                        smtp_port=smtp_server.port,
                        status="review_required",
                    )
                    request_future = executor.submit(
                        self._approve_and_send,
                        fault_api.base_url,
                        task_id,
                    )
                    fault_controller.wait_for_reached("delivery.before_claim")
                    fault_api.process.kill()
                    fault_api.process.wait(timeout=10)
                    self._ignore_request_failure(request_future)

                    before_retry = self._read_delivery_state(
                        data_dir / "auto_email_sender.db",
                        task_id,
                    )
                    self.assertEqual(before_retry["status"], "approved")
                    self.assertIsNone(before_retry["delivery_attempt_id"])
                    self.assertEqual(before_retry["attempt_count"], 0)
                    self.assertEqual(smtp_server.accepted_count, 0)

                    retry_api = DesktopBackendProcess(
                        data_dir=data_dir,
                        role="api",
                        runtime_id=f"retry-{uuid.uuid4()}",
                    ).start()
                    retry_api.wait_ready()
                    response = self._approve_and_send(
                        retry_api.base_url,
                        task_id,
                    )
                    self.assertIn("current_task", response)
                    final_state = self._wait_for_delivery_outcome(
                        data_dir / "auto_email_sender.db",
                        task_id,
                        "smtp_accepted",
                    )
                    self.assertEqual(final_state["status"], "sent")
                    self.assertEqual(final_state["attempt_count"], 1)
                    self.assertEqual(final_state["delivery_log_count"], 1)
                    self.assertEqual(smtp_server.accepted_count, 1)
                finally:
                    executor.shutdown(wait=False, cancel_futures=True)
                    if retry_api is not None:
                        retry_api.stop()
                    fault_api.stop()

    def test_api_immediate_send_kill_matrix_never_accepts_data_twice(self) -> None:
        cases = (
            ("delivery.claim_committed", 0, "assumed_sent_after_interruption"),
            ("delivery.before_smtp", 0, "assumed_sent_after_interruption"),
            ("delivery.smtp_accepted", 1, "assumed_sent_after_interruption"),
            ("delivery.before_final_commit", 1, "assumed_sent_after_interruption"),
            ("delivery.after_final_commit", 1, "smtp_accepted"),
        )
        for fault_point, expected_accepts, expected_outcome in cases:
            with self.subTest(fault_point=fault_point):
                self._exercise_api_kill_case(
                    fault_point=fault_point,
                    expected_accepts=expected_accepts,
                    expected_outcome=expected_outcome,
                )

    def test_worker_scheduled_send_kill_matrix_never_accepts_data_twice(self) -> None:
        cases = (
            ("delivery.before_claim", 1, "smtp_accepted"),
            ("delivery.claim_committed", 0, "assumed_sent_after_interruption"),
            ("delivery.before_smtp", 0, "assumed_sent_after_interruption"),
            ("delivery.smtp_accepted", 1, "assumed_sent_after_interruption"),
            ("delivery.before_final_commit", 1, "assumed_sent_after_interruption"),
            ("delivery.after_final_commit", 1, "smtp_accepted"),
        )
        for repetition in range(1, self._dispatcher_repetitions() + 1):
            for fault_point, expected_accepts, expected_outcome in cases:
                with self.subTest(
                    repetition=repetition,
                    fault_point=fault_point,
                ):
                    self._exercise_worker_kill_case(
                        fault_point=fault_point,
                        expected_accepts=expected_accepts,
                        expected_outcome=expected_outcome,
                    )

    def test_lost_smtp_data_response_is_assumed_sent_without_retry(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            data_dir = root / "data"
            with FakeSMTPServer(root / "smtp", drop_data_response=True) as smtp_server:
                api = DesktopBackendProcess(
                    data_dir=data_dir,
                    role="api",
                    runtime_id=f"lost-response-{uuid.uuid4()}",
                )
                worker: DesktopBackendProcess | None = None
                try:
                    api.start()
                    api.wait_ready()
                    task_id = self._seed_delivery_task(
                        data_dir / "auto_email_sender.db",
                        smtp_port=smtp_server.port,
                        status="scheduled",
                        scheduled_at=datetime.now(UTC) - timedelta(seconds=1),
                    )
                    worker = DesktopBackendProcess(
                        data_dir=data_dir,
                        role="worker",
                        runtime_id=api.runtime_id,
                        api_pid=api.process.pid,
                        worker_generation=f"lost-response-worker-{uuid.uuid4()}",
                        extra_env={
                            "ENABLE_BACKGROUND_WORKERS": "1",
                            "DISPATCHER_INTERVAL_SECONDS": "1",
                        },
                    ).start()
                    worker.wait_worker_ready()
                    final_state = self._wait_for_delivery_outcome(
                        data_dir / "auto_email_sender.db",
                        task_id,
                        "assumed_sent_after_interruption",
                    )
                    self.assertEqual(final_state["status"], "sent")
                    self.assertIsNone(final_state["last_rfc_message_id"])
                    self.assertEqual(final_state["attempt_count"], 1)
                    self.assertEqual(final_state["delivery_log_count"], 1)
                    self.assertEqual(smtp_server.accepted_count, 1)
                    time.sleep(1.2)
                    self.assertEqual(smtp_server.accepted_count, 1)
                finally:
                    if worker is not None:
                        worker.stop()
                    api.stop()

    def _exercise_api_kill_case(
        self,
        *,
        fault_point: str,
        expected_accepts: int,
        expected_outcome: str,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            data_dir = root / "data"
            fault_controller = FaultController(root / "faults")
            with FakeSMTPServer(root / "smtp") as smtp_server:
                fault_api = DesktopBackendProcess(
                    data_dir=data_dir,
                    role="api",
                    extra_env=fault_controller.environment(
                        fault_point,
                        process_id="api-delivery",
                    ),
                )
                recovery_api: DesktopBackendProcess | None = None
                recovery_worker: DesktopBackendProcess | None = None
                executor = ThreadPoolExecutor(max_workers=1)
                request_future: Future[dict[str, Any]] | None = None
                try:
                    fault_api.start()
                    fault_api.wait_ready()
                    task_id = self._seed_delivery_task(
                        data_dir / "auto_email_sender.db",
                        smtp_port=smtp_server.port,
                        status="review_required",
                    )
                    request_future = executor.submit(
                        self._approve_and_send,
                        fault_api.base_url,
                        task_id,
                    )
                    fault_controller.wait_for_reached(fault_point)
                    self.assertEqual(smtp_server.accepted_count, expected_accepts)
                    fault_api.process.kill()
                    fault_api.process.wait(timeout=10)
                    self._ignore_request_failure(request_future)

                    recovery_api = DesktopBackendProcess(
                        data_dir=data_dir,
                        role="api",
                        runtime_id=f"api-recovery-{uuid.uuid4()}",
                    ).start()
                    recovery_api.wait_ready()
                    recovery_worker = DesktopBackendProcess(
                        data_dir=data_dir,
                        role="worker",
                        runtime_id=recovery_api.runtime_id,
                        api_pid=recovery_api.process.pid,
                        worker_generation=f"worker-recovery-{uuid.uuid4()}",
                        extra_env={
                            "ENABLE_BACKGROUND_WORKERS": "1",
                            "DISPATCHER_INTERVAL_SECONDS": "1",
                        },
                    ).start()
                    recovery_worker.wait_worker_ready()

                    final_state = self._wait_for_delivery_outcome(
                        data_dir / "auto_email_sender.db",
                        task_id,
                        expected_outcome,
                    )
                    self.assertEqual(final_state["status"], "sent")
                    self.assertEqual(final_state["attempt_count"], 1)
                    self.assertEqual(final_state["delivery_log_count"], 1)
                    self.assertEqual(smtp_server.accepted_count, expected_accepts)
                    time.sleep(0.1)
                    self.assertEqual(smtp_server.accepted_count, expected_accepts)
                finally:
                    executor.shutdown(wait=False, cancel_futures=True)
                    if recovery_worker is not None:
                        recovery_worker.stop()
                    if recovery_api is not None:
                        recovery_api.stop()
                    fault_api.stop()

    def _exercise_worker_kill_case(
        self,
        *,
        fault_point: str,
        expected_accepts: int,
        expected_outcome: str,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            data_dir = root / "data"
            fault_controller = FaultController(root / "faults")
            with FakeSMTPServer(root / "smtp") as smtp_server:
                api = DesktopBackendProcess(
                    data_dir=data_dir,
                    role="api",
                    runtime_id=f"worker-matrix-{uuid.uuid4()}",
                )
                fault_worker: DesktopBackendProcess | None = None
                recovery_worker: DesktopBackendProcess | None = None
                try:
                    api.start()
                    api.wait_ready()
                    task_id = self._seed_delivery_task(
                        data_dir / "auto_email_sender.db",
                        smtp_port=smtp_server.port,
                        status="scheduled",
                        scheduled_at=datetime.now(UTC) - timedelta(seconds=1),
                    )
                    fault_worker = DesktopBackendProcess(
                        data_dir=data_dir,
                        role="worker",
                        runtime_id=api.runtime_id,
                        api_pid=api.process.pid,
                        worker_generation=f"fault-worker-{uuid.uuid4()}",
                        extra_env={
                            "ENABLE_BACKGROUND_WORKERS": "1",
                            "DISPATCHER_INTERVAL_SECONDS": "1",
                            **fault_controller.environment(
                                fault_point,
                                process_id="worker-delivery",
                            ),
                        },
                    ).start()
                    fault_worker.wait_worker_ready()
                    fault_controller.wait_for_reached(fault_point)
                    accepts_at_fault = (
                        0 if fault_point == "delivery.before_claim" else expected_accepts
                    )
                    self.assertEqual(smtp_server.accepted_count, accepts_at_fault)
                    fault_state = self._read_delivery_state(
                        data_dir / "auto_email_sender.db",
                        task_id,
                    )
                    self.assertEqual(fault_state["integrity_check"], "ok")
                    self.assertEqual(fault_state["foreign_key_errors"], 0)
                    if fault_point == "delivery.before_claim":
                        self.assertEqual(fault_state["status"], "scheduled")
                        self.assertEqual(fault_state["attempt_count"], 0)
                    elif fault_point == "delivery.after_final_commit":
                        self.assertEqual(fault_state["status"], "sent")
                        self.assertEqual(fault_state["attempt_count"], 1)
                        self.assertEqual(fault_state["delivery_log_count"], 1)
                    else:
                        self.assertEqual(fault_state["status"], "sending")
                        self.assertEqual(fault_state["attempt_count"], 1)
                        self.assertEqual(fault_state["attempt_outcome"], "claimed")
                    fault_worker.process.kill()
                    fault_worker.process.wait(timeout=10)
                    self.assertEqual(
                        fetch_json(f"{api.base_url}/startup-status")["state"],
                        "ready",
                    )

                    recovery_worker = DesktopBackendProcess(
                        data_dir=data_dir,
                        role="worker",
                        runtime_id=api.runtime_id,
                        api_pid=api.process.pid,
                        worker_generation=f"recovery-worker-{uuid.uuid4()}",
                        extra_env={
                            "ENABLE_BACKGROUND_WORKERS": "1",
                            "DISPATCHER_INTERVAL_SECONDS": "1",
                        },
                    ).start()
                    recovery_worker.wait_worker_ready()
                    final_state = self._wait_for_delivery_outcome(
                        data_dir / "auto_email_sender.db",
                        task_id,
                        expected_outcome,
                    )
                    self.assertEqual(final_state["status"], "sent")
                    self.assertEqual(final_state["attempt_count"], 1)
                    self.assertEqual(final_state["delivery_log_count"], 1)
                    self.assertEqual(final_state["operation_log_count"], 1)
                    self.assertEqual(final_state["attempt_outcome"], expected_outcome)
                    self.assertEqual(final_state["integrity_check"], "ok")
                    self.assertEqual(final_state["foreign_key_errors"], 0)
                    self.assertEqual(smtp_server.accepted_count, expected_accepts)
                    time.sleep(0.1)
                    self.assertEqual(smtp_server.accepted_count, expected_accepts)
                finally:
                    if recovery_worker is not None:
                        recovery_worker.stop()
                    if fault_worker is not None:
                        fault_worker.stop()
                    api.stop()

    @staticmethod
    def _seed_delivery_task(
        database_path: Path,
        *,
        smtp_port: int,
        status: str,
        scheduled_at: datetime | None = None,
    ) -> int:
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
                    VALUES (?, ?, ?, ?, '127.0.0.1', ?, ?, 'secret', 1, 1)
                    """,
                    (
                        "Process identity",
                        "Process identity",
                        "Process sender",
                        f"sender-{suffix}@example.com",
                        smtp_port,
                        f"sender-{suffix}@example.com",
                    ),
                ).lastrowid
            )
            llm_profile_id = int(
                connection.execute(
                    """
                    INSERT INTO llm_profiles (name, provider, api_key, model_name)
                    VALUES (?, 'openai', 'test-key', 'test-model')
                    """,
                    (f"Process model {suffix}",),
                ).lastrowid
            )
            professor_id = int(
                connection.execute(
                    """
                    INSERT INTO professors (name, email, research_direction, crawl_status)
                    VALUES ('Process professor', ?, 'Testing', 'discovered')
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
                    VALUES ('manual', ?, ?, ?, ?, 'Process subject',
                            'Process body', '[]', ?, ?)
                    """,
                    (
                        identity_id,
                        llm_profile_id,
                        professor_id,
                        status,
                        datetime.now(UTC).replace(tzinfo=None).isoformat(sep=" "),
                        (
                            scheduled_at.astimezone(UTC)
                            .replace(tzinfo=None)
                            .isoformat(sep=" ")
                            if scheduled_at
                            else None
                        ),
                    ),
                ).lastrowid
            )
            connection.commit()
            return task_id
        finally:
            connection.close()

    @staticmethod
    def _approve_and_send(base_url: str, task_id: int) -> dict[str, Any]:
        payload = json.dumps(
            {
                "subject": "Immediate process subject",
                "body_text": "Immediate process body",
                "body_html": None,
                "selected_material_ids": [],
            }
        ).encode("utf-8")
        parsed = urllib.parse.urlsplit(base_url)
        connection = http.client.HTTPConnection(
            parsed.hostname,
            parsed.port,
            timeout=30,
        )
        try:
            connection.request(
                "POST",
                f"/api/email-tasks/{task_id}/approve-and-send",
                body=payload,
                headers={"Content-Type": "application/json"},
            )
            response = connection.getresponse()
            response_payload = response.read()
            if response.status >= 400:
                raise RuntimeError(
                    f"HTTP {response.status}: {response_payload.decode('utf-8', errors='replace')}"
                )
            result = json.loads(response_payload.decode("utf-8"))
        finally:
            connection.close()
        if not isinstance(result, dict):
            raise TypeError("Expected an object response")
        return result

    @staticmethod
    def _ignore_request_failure(request_future: Future[dict[str, Any]]) -> None:
        try:
            request_future.result(timeout=5)
        except Exception:
            return

    @classmethod
    def _wait_for_delivery_outcome(
        cls,
        database_path: Path,
        task_id: int,
        outcome: str,
    ) -> dict[str, Any]:
        return wait_until(
            lambda: (
                state
                if (state := cls._read_delivery_state(database_path, task_id))[
                    "delivery_outcome"
                ]
                == outcome
                else None
            ),
            timeout_seconds=15,
            description=f"delivery outcome {outcome}",
        )

    @staticmethod
    def _read_delivery_state(database_path: Path, task_id: int) -> dict[str, Any]:
        connection = sqlite3.connect(database_path, timeout=2)
        connection.row_factory = sqlite3.Row
        try:
            task = connection.execute(
                """
                SELECT status, delivery_attempt_id, delivery_outcome,
                       delivery_outcome_at, last_rfc_message_id
                FROM email_tasks
                WHERE id = ?
                """,
                (task_id,),
            ).fetchone()
            if task is None:
                raise AssertionError(f"Missing email task {task_id}")
            attempt_count = int(
                connection.execute(
                    "SELECT COUNT(*) FROM email_delivery_attempts WHERE email_task_id = ?",
                    (task_id,),
                ).fetchone()[0]
            )
            delivery_log_count = int(
                connection.execute(
                    """
                    SELECT COUNT(*) FROM email_logs
                    WHERE email_task_id = ? AND delivery_attempt_id IS NOT NULL
                    """,
                    (task_id,),
                ).fetchone()[0]
            )
            attempt = connection.execute(
                """
                SELECT outcome, finalized_at
                FROM email_delivery_attempts
                WHERE email_task_id = ?
                ORDER BY started_at, id
                LIMIT 1
                """,
                (task_id,),
            ).fetchone()
            operation_log_count = int(
                connection.execute(
                    """
                    SELECT COUNT(*) FROM operation_logs
                    WHERE entity_type = 'email_task'
                      AND entity_id = ?
                      AND event_name IN (
                          'email_task.sent',
                          'email_task.assumed_sent_after_interruption'
                      )
                    """,
                    (str(task_id),),
                ).fetchone()[0]
            )
            integrity_check = str(
                connection.execute("PRAGMA integrity_check").fetchone()[0]
            )
            foreign_key_errors = len(
                connection.execute("PRAGMA foreign_key_check").fetchall()
            )
            return {
                **dict(task),
                "attempt_count": attempt_count,
                "delivery_log_count": delivery_log_count,
                "attempt_outcome": attempt["outcome"] if attempt is not None else None,
                "attempt_finalized_at": (
                    attempt["finalized_at"] if attempt is not None else None
                ),
                "operation_log_count": operation_log_count,
                "integrity_check": integrity_check,
                "foreign_key_errors": foreign_key_errors,
            }
        finally:
            connection.close()

    @staticmethod
    def _dispatcher_repetitions() -> int:
        raw_value = os.getenv(_DISPATCHER_REPETITIONS_ENV, "1")
        try:
            repetitions = int(raw_value)
        except ValueError as exc:
            raise AssertionError(
                f"{_DISPATCHER_REPETITIONS_ENV} must be an integer, "
                f"got {raw_value!r}"
            ) from exc
        if not 1 <= repetitions <= 100:
            raise AssertionError(
                f"{_DISPATCHER_REPETITIONS_ENV} must be between 1 and 100, "
                f"got {repetitions}"
            )
        return repetitions


if __name__ == "__main__":
    unittest.main()
