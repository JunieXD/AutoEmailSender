from __future__ import annotations

import json
import os
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
    FakeLLMServer,
    FaultController,
    fetch_json,
    post_json,
    wait_until,
)


_REPETITIONS_ENV = "AUTO_EMAIL_SENDER_BATCH_DRAFT_CHAOS_REPETITIONS"


class BatchDraftProcessSafetyTests(unittest.TestCase):
    def test_worker_kill_matrix_recovers_with_one_committed_draft(self) -> None:
        cases = (
            ("batch_draft.before_claim", "discovered", 0, 1),
            ("batch_draft.claim_committed", "generating_draft", 0, 1),
            ("batch_draft.before_external_call", "generating_draft", 0, 1),
            ("batch_draft.external_call_returned", "generating_draft", 1, 2),
            ("batch_draft.before_final_commit", "generating_draft", 1, 2),
            ("batch_draft.after_final_commit", "review_required", 1, 1),
        )
        repetitions = self._chaos_repetitions()
        for repetition in range(1, repetitions + 1):
            for (
                fault_point,
                expected_fault_status,
                expected_fault_requests,
                expected_total_requests,
            ) in cases:
                with self.subTest(
                    repetition=repetition,
                    fault_point=fault_point,
                ):
                    self._exercise_kill_case(
                        fault_point=fault_point,
                        expected_fault_status=expected_fault_status,
                        expected_fault_requests=expected_fault_requests,
                        expected_total_requests=expected_total_requests,
                    )

    def test_api_pause_and_stop_fence_returned_worker_results(self) -> None:
        repetitions = self._chaos_repetitions()
        for repetition in range(1, repetitions + 1):
            for action, expected_status, expected_batch_status in (
                ("pause", "discovered", "paused"),
                ("stop", "canceled", "stopped"),
            ):
                with self.subTest(repetition=repetition, action=action):
                    self._exercise_api_control_case(
                        action=action,
                        expected_status=expected_status,
                        expected_batch_status=expected_batch_status,
                    )

    def _exercise_kill_case(
        self,
        *,
        fault_point: str,
        expected_fault_status: str,
        expected_fault_requests: int,
        expected_total_requests: int,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            data_dir = root / "data"
            fault_controller = FaultController(root / "faults")
            with FakeLLMServer() as llm_server:
                api = DesktopBackendProcess(
                    data_dir=data_dir,
                    role="api",
                    runtime_id=f"batch-draft-{uuid.uuid4()}",
                )
                fault_worker: DesktopBackendProcess | None = None
                recovery_worker: DesktopBackendProcess | None = None
                try:
                    api.start()
                    api.wait_ready()
                    batch_task_id, email_task_id = self._seed_batch_draft_task(
                        data_dir / "auto_email_sender.db",
                        llm_base_url=llm_server.base_url,
                    )
                    fault_worker = DesktopBackendProcess(
                        data_dir=data_dir,
                        role="worker",
                        runtime_id=api.runtime_id,
                        api_pid=api.process.pid,
                        worker_generation=f"fault-{uuid.uuid4()}",
                        extra_env={
                            **self._worker_environment(),
                            **fault_controller.environment(
                                fault_point,
                                process_id="batch-draft-worker",
                            ),
                        },
                    ).start()
                    fault_worker.wait_worker_ready()
                    fault_controller.wait_for_reached(
                        fault_point,
                        timeout_seconds=20,
                    )

                    fault_state = self._read_batch_draft_state(
                        data_dir / "auto_email_sender.db",
                        email_task_id,
                    )
                    self.assertEqual(fault_state["status"], expected_fault_status)
                    self.assertEqual(llm_server.request_count, expected_fault_requests)
                    if expected_fault_status == "generating_draft":
                        self.assertIsNotNone(fault_state["draft_claim_id"])
                        self.assertEqual(fault_state["draft_log_count"], 0)
                        self.assertEqual(fault_state["operation_log_count"], 0)
                    elif expected_fault_status == "review_required":
                        self.assertIsNone(fault_state["draft_claim_id"])
                        self.assertEqual(fault_state["draft_log_count"], 1)
                        self.assertEqual(fault_state["operation_log_count"], 1)

                    fault_worker.process.kill()
                    fault_worker.process.wait(timeout=10)
                    self.assertEqual(
                        fetch_json(f"{api.base_url}/startup-status")["state"],
                        "ready",
                    )

                    if expected_fault_status == "generating_draft":
                        self._move_batch_draft_claim_far_into_future(
                            data_dir / "auto_email_sender.db",
                            email_task_id,
                        )

                    recovery_worker = DesktopBackendProcess(
                        data_dir=data_dir,
                        role="worker",
                        runtime_id=api.runtime_id,
                        api_pid=api.process.pid,
                        worker_generation=f"recovery-{uuid.uuid4()}",
                        extra_env=self._worker_environment(),
                    ).start()
                    recovery_worker.wait_worker_ready()
                    final_state = self._wait_for_batch_draft_status(
                        data_dir / "auto_email_sender.db",
                        email_task_id,
                        "review_required",
                    )

                    self.assertIsNone(final_state["draft_claim_id"])
                    self.assertIsNone(final_state["draft_claimed_at"])
                    self.assertIsNone(final_state["draft_lease_expires_at"])
                    self.assertIsNone(final_state["draft_generation_previous_status"])
                    self.assertEqual(final_state["draft_log_count"], 1)
                    self.assertEqual(final_state["operation_log_count"], 1)
                    self.assertEqual(final_state["last_error"], None)
                    self.assertEqual(llm_server.request_count, expected_total_requests)

                    thread_payload = fetch_json(
                        f"{api.base_url}/api/batch-tasks/"
                        f"{batch_task_id}/items/{email_task_id}/thread"
                    )
                    self.assertEqual(
                        thread_payload["current_task"]["status"],
                        "review_required",
                    )
                    time.sleep(0.1)
                    self.assertEqual(llm_server.request_count, expected_total_requests)
                finally:
                    if recovery_worker is not None:
                        recovery_worker.stop()
                    if fault_worker is not None:
                        fault_worker.stop()
                    api.stop()

    def _exercise_api_control_case(
        self,
        *,
        action: str,
        expected_status: str,
        expected_batch_status: str,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            data_dir = root / "data"
            fault_controller = FaultController(root / "faults")
            with FakeLLMServer() as llm_server:
                api = DesktopBackendProcess(
                    data_dir=data_dir,
                    role="api",
                    runtime_id=f"batch-control-{uuid.uuid4()}",
                )
                fault_worker: DesktopBackendProcess | None = None
                recovery_worker: DesktopBackendProcess | None = None
                try:
                    api.start()
                    api.wait_ready()
                    batch_task_id, email_task_id = self._seed_batch_draft_task(
                        data_dir / "auto_email_sender.db",
                        llm_base_url=llm_server.base_url,
                    )
                    fault_worker = DesktopBackendProcess(
                        data_dir=data_dir,
                        role="worker",
                        runtime_id=api.runtime_id,
                        api_pid=api.process.pid,
                        worker_generation=f"control-{uuid.uuid4()}",
                        extra_env={
                            **self._worker_environment(),
                            **fault_controller.environment(
                                "batch_draft.external_call_returned",
                                process_id="batch-control-worker",
                            ),
                        },
                    ).start()
                    fault_worker.wait_worker_ready()
                    reached_path = fault_controller.wait_for_reached(
                        "batch_draft.external_call_returned",
                        timeout_seconds=20,
                    )
                    self.assertEqual(llm_server.request_count, 1)

                    response = post_json(
                        f"{api.base_url}/api/batch-tasks/{batch_task_id}/{action}"
                    )
                    self.assertTrue(response["ok"])
                    fault_controller.release(reached_path)
                    completed_path = reached_path.with_suffix(".completed")
                    wait_until(
                        completed_path.exists,
                        timeout_seconds=5,
                        description="released batch draft fault point",
                    )
                    controlled_state = self._wait_for_batch_draft_status(
                        data_dir / "auto_email_sender.db",
                        email_task_id,
                        expected_status,
                    )
                    self.assertEqual(
                        controlled_state["batch_status"],
                        expected_batch_status,
                    )
                    self.assertIsNone(controlled_state["draft_claim_id"])
                    self.assertEqual(controlled_state["draft_log_count"], 0)
                    self.assertEqual(controlled_state["operation_log_count"], 0)
                    self.assertEqual(llm_server.request_count, 1)

                    if action == "pause":
                        fault_worker.stop()
                        recovery_worker = DesktopBackendProcess(
                            data_dir=data_dir,
                            role="worker",
                            runtime_id=api.runtime_id,
                            api_pid=api.process.pid,
                            worker_generation=f"resume-{uuid.uuid4()}",
                            extra_env=self._worker_environment(),
                        ).start()
                        recovery_worker.wait_worker_ready()
                        resume_response = post_json(
                            f"{api.base_url}/api/batch-tasks/{batch_task_id}/resume"
                        )
                        self.assertTrue(resume_response["ok"])
                        final_state = self._wait_for_batch_draft_status(
                            data_dir / "auto_email_sender.db",
                            email_task_id,
                            "review_required",
                        )
                        self.assertEqual(final_state["batch_status"], "running")
                        self.assertEqual(final_state["draft_log_count"], 1)
                        self.assertEqual(final_state["operation_log_count"], 1)
                        self.assertEqual(llm_server.request_count, 2)
                    else:
                        time.sleep(1.2)
                        final_state = self._read_batch_draft_state(
                            data_dir / "auto_email_sender.db",
                            email_task_id,
                        )
                        self.assertEqual(final_state["status"], "canceled")
                        self.assertEqual(final_state["batch_status"], "stopped")
                        self.assertEqual(final_state["draft_log_count"], 0)
                        self.assertEqual(final_state["operation_log_count"], 0)
                        self.assertEqual(llm_server.request_count, 1)
                finally:
                    if recovery_worker is not None:
                        recovery_worker.stop()
                    if fault_worker is not None:
                        fault_worker.stop()
                    api.stop()

    @staticmethod
    def _worker_environment() -> dict[str, str]:
        return {
            "ENABLE_BACKGROUND_WORKERS": "1",
            "DISPATCHER_INTERVAL_SECONDS": "3600",
            "IMAP_POLL_INTERVAL_SECONDS": "3600",
            "MATCH_ANALYSIS_JOB_INTERVAL_SECONDS": "3600",
            "LLM_REQUEST_TIMEOUT_SECONDS": "10",
        }

    @staticmethod
    def _seed_batch_draft_task(
        database_path: Path,
        *,
        llm_base_url: str,
        model_name: str = "test-model",
    ) -> tuple[int, int]:
        connection = sqlite3.connect(database_path, timeout=10)
        try:
            connection.execute("PRAGMA foreign_keys = ON")
            suffix = uuid.uuid4().hex
            now = datetime.now(UTC).replace(tzinfo=None)
            future = now + timedelta(days=1)
            connection.execute("INSERT OR IGNORE INTO app_settings (id) VALUES (1)")
            identity_id = int(
                connection.execute(
                    """
                    INSERT INTO identity_profiles (
                        name, profile_name, sender_name, email_address,
                        smtp_host, smtp_port, smtp_username, smtp_password,
                        outreach_generation_mode, outreach_template_subject,
                        outreach_template_body_text
                    )
                    VALUES (?, ?, ?, ?, 'smtp.example.com', 465, ?, 'secret',
                            'llm', '申请与{{name}}老师交流',
                            '老师您好，我是{{sender_name}}。')
                    """,
                    (
                        "Process identity",
                        "Process identity",
                        "Process sender",
                        f"sender-{suffix}@example.com",
                        f"sender-{suffix}@example.com",
                    ),
                ).lastrowid
            )
            material_id = int(
                connection.execute(
                    """
                    INSERT INTO identity_materials (
                        identity_id, display_name, original_filename,
                        file_path, mime_type, size_bytes, sha256,
                        extracted_text, material_type
                    )
                    VALUES (?, 'Resume', 'resume.txt', 'resume.txt',
                            'text/plain', 32, ?,
                            'My research focuses on deterministic systems.', 'resume')
                    """,
                    (identity_id, "0" * 64),
                ).lastrowid
            )
            connection.execute(
                "UPDATE identity_profiles SET current_primary_material_id = ? WHERE id = ?",
                (material_id, identity_id),
            )
            llm_profile_id = int(
                connection.execute(
                    """
                    INSERT INTO llm_profiles (
                        name, provider, api_base_url, api_key, model_name, is_default
                    )
                    VALUES (?, 'openai', ?, 'test-key', ?, 1)
                    """,
                    (f"Process model {suffix}", llm_base_url, model_name),
                ).lastrowid
            )
            connection.execute(
                """
                    INSERT INTO llm_endpoint_adaptation_cache (
                        api_base_url, model_name, learned_endpoint_kind, probed_at
                    )
                    VALUES (?, ?, 'chat_completions', ?)
                    """,
                    (llm_base_url, model_name, now.isoformat(sep=" ")),
                )
            connection.execute(
                """
                    INSERT INTO thinking_adaptation_cache (
                        api_base_url, model_name, endpoint_kind,
                        learned_extra_body, probed_at
                    )
                    VALUES (?, ?, 'chat_completions', 'null', ?)
                    """,
                    (llm_base_url, model_name, now.isoformat(sep=" ")),
                )
            connection.execute(
                """
                    INSERT INTO llm_structured_output_adaptation_cache (
                        api_base_url, model_name, endpoint_kind, probe_version,
                        learned_mode, probed_at, expires_at
                    )
                    VALUES (?, ?, 'chat_completions', 3,
                            'prompt_only', ?, ?)
                    """,
                    (
                        llm_base_url,
                        model_name,
                        now.isoformat(sep=" "),
                        future.isoformat(sep=" "),
                    ),
            )
            professor_id = int(
                connection.execute(
                    """
                    INSERT INTO professors (
                        name, email, title, university, school, department,
                        research_direction, recent_papers, crawl_status
                    )
                    VALUES ('Process professor', ?, 'Professor',
                            'Example University', 'School of AI',
                            'Computer Science', 'Deterministic systems', '[]',
                            'discovered')
                    """,
                    (f"professor-{suffix}@example.edu",),
                ).lastrowid
            )
            batch_task_id = int(
                connection.execute(
                    """
                    INSERT INTO batch_tasks (
                        identity_id, llm_profile_id, name, schedule_type,
                        status, primary_material_id,
                        outreach_template_snapshot_version,
                        outreach_generation_mode, outreach_template_subject,
                        outreach_template_body_text, email_subject, email_body,
                        selected_material_ids, target_count
                    )
                    VALUES (?, ?, 'Process batch draft', 'immediate', 'running', ?,
                            1, 'llm', '申请与{{name}}老师交流',
                            '老师您好，我是{{sender_name}}。',
                            '申请与{{name}}老师交流',
                            '老师您好，我是{{sender_name}}。', '[]', 1)
                    """,
                    (identity_id, llm_profile_id, material_id),
                ).lastrowid
            )
            email_task_id = int(
                connection.execute(
                    """
                    INSERT INTO email_tasks (
                        source, batch_task_id, identity_id, llm_profile_id,
                        professor_id, primary_material_id, status,
                        outreach_generation_mode,
                        outreach_template_snapshot_version,
                        outreach_template_subject, outreach_template_body_text,
                        selected_material_ids
                    )
                    VALUES ('batch', ?, ?, ?, ?, ?, 'discovered', 'llm', 1,
                            '申请与{{name}}老师交流',
                            '老师您好，我是{{sender_name}}。', '[]')
                    """,
                    (
                        batch_task_id,
                        identity_id,
                        llm_profile_id,
                        professor_id,
                        material_id,
                    ),
                ).lastrowid
            )
            connection.commit()
            return batch_task_id, email_task_id
        finally:
            connection.close()

    @staticmethod
    def _move_batch_draft_claim_far_into_future(
        database_path: Path,
        email_task_id: int,
    ) -> None:
        connection = sqlite3.connect(database_path, timeout=10)
        try:
            future_lease = (
                datetime.now(UTC).replace(tzinfo=None) + timedelta(days=365)
            ).isoformat(sep=" ")
            result = connection.execute(
                """
                UPDATE email_tasks
                SET draft_lease_expires_at = ?
                WHERE id = ? AND status = 'generating_draft'
                      AND draft_claim_id IS NOT NULL
                """,
                (future_lease, email_task_id),
            )
            if result.rowcount != 1:
                raise AssertionError(
                    f"Could not future-date batch draft claim for task {email_task_id}"
                )
            connection.commit()
        finally:
            connection.close()

    @classmethod
    def _wait_for_batch_draft_status(
        cls,
        database_path: Path,
        email_task_id: int,
        expected_status: str,
    ) -> dict[str, Any]:
        return wait_until(
            lambda: (
                state
                if (
                    state := cls._read_batch_draft_state(
                        database_path,
                        email_task_id,
                    )
                )["status"]
                == expected_status
                else None
            ),
            timeout_seconds=20,
            description=f"batch draft status {expected_status}",
        )

    @staticmethod
    def _read_batch_draft_state(
        database_path: Path,
        email_task_id: int,
    ) -> dict[str, Any]:
        connection = sqlite3.connect(database_path, timeout=2)
        connection.row_factory = sqlite3.Row
        try:
            task = connection.execute(
                """
                SELECT email_tasks.status,
                       batch_tasks.status AS batch_status,
                       email_tasks.draft_generation_previous_status,
                       email_tasks.draft_claim_id,
                       email_tasks.draft_claimed_at,
                       email_tasks.draft_lease_expires_at,
                       email_tasks.generated_subject,
                       email_tasks.generated_content_text,
                       email_tasks.generated_content_html,
                       email_tasks.last_error
                FROM email_tasks
                JOIN batch_tasks ON batch_tasks.id = email_tasks.batch_task_id
                WHERE email_tasks.id = ?
                """,
                (email_task_id,),
            ).fetchone()
            if task is None:
                raise AssertionError(f"Missing email task {email_task_id}")
            draft_log_count = int(
                connection.execute(
                    """
                    SELECT COUNT(*) FROM email_logs
                    WHERE email_task_id = ? AND direction = 'draft'
                    """,
                    (email_task_id,),
                ).fetchone()[0]
            )
            operation_log_count = int(
                connection.execute(
                    """
                    SELECT COUNT(*) FROM operation_logs
                    WHERE event_name = 'email_task.draft_generated'
                          AND entity_type = 'email_task' AND entity_id = ?
                    """,
                    (str(email_task_id),),
                ).fetchone()[0]
            )
            return {
                **dict(task),
                "draft_log_count": draft_log_count,
                "operation_log_count": operation_log_count,
            }
        finally:
            connection.close()

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
