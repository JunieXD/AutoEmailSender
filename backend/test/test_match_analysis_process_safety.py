from __future__ import annotations

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


_REPETITIONS_ENV = "AUTO_EMAIL_SENDER_MATCH_CHAOS_REPETITIONS"


class MatchAnalysisProcessSafetyTests(unittest.TestCase):
    def test_worker_kill_matrix_fences_results_and_converges_once(self) -> None:
        cases = (
            ("matching.before_claim", "queued", 0, 1, 1),
            ("matching.claim_committed", "running", 0, 1, 2),
            ("matching.before_external_call", "running", 0, 1, 2),
            ("matching.external_call_returned", "running", 1, 2, 2),
            ("matching.before_final_commit", "running", 1, 2, 2),
            ("matching.after_final_commit", "succeeded", 1, 1, 1),
        )
        repetitions = self._chaos_repetitions()
        for repetition in range(1, repetitions + 1):
            for (
                fault_point,
                expected_fault_status,
                expected_fault_requests,
                expected_total_requests,
                expected_attempt_count,
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
                        expected_attempt_count=expected_attempt_count,
                    )

    def test_api_cancel_fences_result_already_returned_to_worker(self) -> None:
        repetitions = self._chaos_repetitions()
        for repetition in range(1, repetitions + 1):
            with self.subTest(repetition=repetition):
                self._exercise_api_cancel_case()

    def _exercise_kill_case(
        self,
        *,
        fault_point: str,
        expected_fault_status: str,
        expected_fault_requests: int,
        expected_total_requests: int,
        expected_attempt_count: int,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            data_dir = root / "data"
            fault_controller = FaultController(root / "faults")
            with FakeLLMServer() as llm_server:
                api = DesktopBackendProcess(
                    data_dir=data_dir,
                    role="api",
                    runtime_id=f"match-chaos-{uuid.uuid4()}",
                )
                fault_worker: DesktopBackendProcess | None = None
                recovery_worker: DesktopBackendProcess | None = None
                try:
                    api.start()
                    api.wait_ready()
                    job_id, item_id = self._seed_match_job(
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
                                process_id="match-worker",
                            ),
                        },
                    ).start()
                    fault_worker.wait_worker_ready()
                    fault_controller.wait_for_reached(
                        fault_point,
                        timeout_seconds=20,
                    )

                    fault_state = self._read_match_state(
                        data_dir / "auto_email_sender.db",
                        job_id,
                        item_id,
                    )
                    self.assertEqual(
                        fault_state["item_status"],
                        expected_fault_status,
                    )
                    self.assertEqual(llm_server.request_count, expected_fault_requests)
                    if expected_fault_status == "running":
                        self.assertIsNotNone(fault_state["claim_id"])
                        self.assertEqual(fault_state["canonical_count"], 0)
                        self.assertEqual(fault_state["succeeded_run_count"], 0)
                    elif expected_fault_status == "succeeded":
                        self.assertIsNone(fault_state["claim_id"])
                        self.assertEqual(fault_state["canonical_count"], 1)
                        self.assertEqual(fault_state["succeeded_run_count"], 1)

                    fault_worker.process.kill()
                    fault_worker.process.wait(timeout=10)
                    self.assertEqual(
                        fetch_json(f"{api.base_url}/startup-status")["state"],
                        "ready",
                    )
                    if expected_fault_status == "running":
                        self._move_match_claim_far_into_future(
                            data_dir / "auto_email_sender.db",
                            item_id,
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
                    final_state = self._wait_for_job_status(
                        data_dir / "auto_email_sender.db",
                        job_id,
                        item_id,
                        "completed",
                    )

                    self.assertEqual(final_state["item_status"], "succeeded")
                    self.assertIsNone(final_state["claim_id"])
                    self.assertIsNone(final_state["claimed_at"])
                    self.assertIsNone(final_state["lease_expires_at"])
                    self.assertEqual(
                        final_state["attempt_count"],
                        expected_attempt_count,
                    )
                    self.assertEqual(final_state["succeeded_count"], 1)
                    self.assertEqual(final_state["failed_count"], 0)
                    self.assertEqual(final_state["canonical_count"], 1)
                    self.assertEqual(final_state["canonical_score"], 85)
                    self.assertEqual(final_state["succeeded_run_count"], 1)
                    self.assertEqual(final_state["running_run_count"], 0)
                    self.assertEqual(final_state["completion_log_count"], 1)
                    self.assertEqual(llm_server.request_count, expected_total_requests)

                    api_job = fetch_json(
                        f"{api.base_url}/api/match-analysis-jobs/{job_id}"
                    )
                    self.assertEqual(api_job["status"], "completed")
                    self.assertEqual(api_job["succeeded_count"], 1)
                    time.sleep(0.1)
                    self.assertEqual(llm_server.request_count, expected_total_requests)
                finally:
                    if recovery_worker is not None:
                        recovery_worker.stop()
                    if fault_worker is not None:
                        fault_worker.stop()
                    api.stop()

    def _exercise_api_cancel_case(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            data_dir = root / "data"
            fault_controller = FaultController(root / "faults")
            with FakeLLMServer() as llm_server:
                api = DesktopBackendProcess(
                    data_dir=data_dir,
                    role="api",
                    runtime_id=f"match-cancel-{uuid.uuid4()}",
                )
                worker: DesktopBackendProcess | None = None
                try:
                    api.start()
                    api.wait_ready()
                    job_id, item_id = self._seed_match_job(
                        data_dir / "auto_email_sender.db",
                        llm_base_url=llm_server.base_url,
                    )
                    worker = DesktopBackendProcess(
                        data_dir=data_dir,
                        role="worker",
                        runtime_id=api.runtime_id,
                        api_pid=api.process.pid,
                        worker_generation=f"cancel-{uuid.uuid4()}",
                        extra_env={
                            **self._worker_environment(),
                            **fault_controller.environment(
                                "matching.external_call_returned",
                                process_id="match-cancel-worker",
                            ),
                        },
                    ).start()
                    worker.wait_worker_ready()
                    reached_path = fault_controller.wait_for_reached(
                        "matching.external_call_returned",
                        timeout_seconds=20,
                    )
                    self.assertEqual(llm_server.request_count, 1)

                    response = post_json(
                        f"{api.base_url}/api/match-analysis-jobs/{job_id}/cancel"
                    )
                    self.assertTrue(response["ok"])
                    fault_controller.release(reached_path)
                    wait_until(
                        reached_path.with_suffix(".completed").exists,
                        timeout_seconds=10,
                        description="released match result fault point",
                    )
                    canceled = self._wait_for_job_status(
                        data_dir / "auto_email_sender.db",
                        job_id,
                        item_id,
                        "canceled",
                    )
                    self.assertEqual(canceled["item_status"], "canceled")
                    self.assertIsNone(canceled["claim_id"])
                    self.assertEqual(canceled["canonical_count"], 0)
                    self.assertEqual(canceled["succeeded_run_count"], 0)
                    self.assertEqual(canceled["running_run_count"], 0)
                    self.assertEqual(llm_server.request_count, 1)
                    api_job = fetch_json(
                        f"{api.base_url}/api/match-analysis-jobs/{job_id}"
                    )
                    self.assertEqual(api_job["status"], "canceled")
                finally:
                    if worker is not None:
                        worker.stop()
                    api.stop()

    @staticmethod
    def _worker_environment() -> dict[str, str]:
        return {
            "ENABLE_BACKGROUND_WORKERS": "1",
            "DISPATCHER_INTERVAL_SECONDS": "3600",
            "IMAP_POLL_INTERVAL_SECONDS": "3600",
            "MATCH_ANALYSIS_JOB_INTERVAL_SECONDS": "1",
            "MATCH_ANALYSIS_JOB_ITEM_CONCURRENCY": "1",
            "LLM_REQUEST_TIMEOUT_SECONDS": "10",
        }

    @staticmethod
    def _seed_match_job(
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
            connection.execute(
                """
                INSERT OR IGNORE INTO app_settings (
                    id, match_analysis_job_worker_count,
                    match_analysis_job_item_concurrency,
                    match_analysis_job_interval_seconds
                ) VALUES (1, 1, 1, 1)
                """
            )
            identity_id = int(
                connection.execute(
                    """
                    INSERT INTO identity_profiles (
                        name, profile_name, sender_name, email_address,
                        smtp_host, smtp_port, smtp_username, smtp_password
                    )
                    VALUES (?, ?, ?, ?, 'smtp.example.com', 465, ?, 'secret')
                    """,
                    (
                        "Match identity",
                        "Match identity",
                        "Match sender",
                        f"match-{suffix}@example.com",
                        f"match-{suffix}@example.com",
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
                            'AI agents and deterministic systems.', 'resume')
                    """,
                    (identity_id, "1" * 64),
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
                    ) VALUES (?, 'openai', ?, 'test-key', ?, 1)
                    """,
                    (f"Match model {suffix}", llm_base_url, model_name),
                ).lastrowid
            )
            connection.execute(
                """
                    INSERT INTO llm_endpoint_adaptation_cache (
                        api_base_url, model_name, learned_endpoint_kind, probed_at
                    ) VALUES (?, ?, 'chat_completions', ?)
                    """,
                    (llm_base_url, model_name, now.isoformat(sep=" ")),
                )
            connection.execute(
                """
                    INSERT INTO thinking_adaptation_cache (
                        api_base_url, model_name, endpoint_kind,
                        learned_extra_body, probed_at
                    ) VALUES (?, ?, 'chat_completions', 'null', ?)
                    """,
                    (llm_base_url, model_name, now.isoformat(sep=" ")),
                )
            connection.execute(
                """
                    INSERT INTO llm_structured_output_adaptation_cache (
                        api_base_url, model_name, endpoint_kind, probe_version,
                        learned_mode, probed_at, expires_at
                    ) VALUES (?, ?, 'chat_completions', 3,
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
                        name, email, title, university, school,
                        research_direction, recent_papers, crawl_status
                    ) VALUES ('Match professor', ?, 'Professor',
                              'Example University', 'Computing',
                              'AI agents', '[]', 'discovered')
                    """,
                    (f"match-professor-{suffix}@example.edu",),
                ).lastrowid
            )
            job_id = int(
                connection.execute(
                    """
                    INSERT INTO match_analysis_jobs (
                        name, identity_id, match_source_identity_id,
                        llm_profile_id, status, target_count
                    ) VALUES ('Process match job', ?, ?, ?, 'queued', 1)
                    """,
                    (identity_id, identity_id, llm_profile_id),
                ).lastrowid
            )
            item_id = int(
                connection.execute(
                    """
                    INSERT INTO match_analysis_job_items (
                        job_id, professor_id, status
                    ) VALUES (?, ?, 'queued')
                    """,
                    (job_id, professor_id),
                ).lastrowid
            )
            connection.commit()
            return job_id, item_id
        finally:
            connection.close()

    @staticmethod
    def _move_match_claim_far_into_future(database_path: Path, item_id: int) -> None:
        connection = sqlite3.connect(database_path, timeout=10)
        try:
            future_lease = (
                datetime.now(UTC).replace(tzinfo=None) + timedelta(days=365)
            ).isoformat(sep=" ")
            result = connection.execute(
                """
                UPDATE match_analysis_job_items
                SET lease_expires_at = ?
                WHERE id = ? AND status = 'running' AND claim_id IS NOT NULL
                """,
                (future_lease, item_id),
            )
            if result.rowcount != 1:
                raise AssertionError(
                    f"Could not future-date match claim for item {item_id}"
                )
            connection.commit()
        finally:
            connection.close()

    @classmethod
    def _wait_for_job_status(
        cls,
        database_path: Path,
        job_id: int,
        item_id: int,
        expected_status: str,
    ) -> dict[str, Any]:
        return wait_until(
            lambda: (
                state
                if (
                    state := cls._read_match_state(database_path, job_id, item_id)
                )["job_status"]
                == expected_status
                else None
            ),
            timeout_seconds=20,
            description=f"match job status {expected_status}",
        )

    @staticmethod
    def _read_match_state(
        database_path: Path,
        job_id: int,
        item_id: int,
    ) -> dict[str, Any]:
        connection = sqlite3.connect(database_path, timeout=2)
        connection.row_factory = sqlite3.Row
        try:
            row = connection.execute(
                """
                SELECT match_analysis_jobs.status AS job_status,
                       match_analysis_jobs.succeeded_count,
                       match_analysis_jobs.failed_count,
                       match_analysis_jobs.total_tokens,
                       match_analysis_job_items.status AS item_status,
                       match_analysis_job_items.claim_id,
                       match_analysis_job_items.claimed_at,
                       match_analysis_job_items.lease_expires_at,
                       match_analysis_job_items.attempt_count,
                       match_analysis_job_items.match_analysis_run_id
                FROM match_analysis_job_items
                JOIN match_analysis_jobs
                  ON match_analysis_jobs.id = match_analysis_job_items.job_id
                WHERE match_analysis_jobs.id = ?
                      AND match_analysis_job_items.id = ?
                """,
                (job_id, item_id),
            ).fetchone()
            if row is None:
                raise AssertionError(f"Missing match job/item {job_id}/{item_id}")
            run_counts = connection.execute(
                """
                SELECT COUNT(*) AS total,
                       SUM(CASE WHEN status = 'running' THEN 1 ELSE 0 END) AS running,
                       SUM(CASE WHEN status = 'succeeded' THEN 1 ELSE 0 END) AS succeeded
                FROM match_analysis_runs
                WHERE identity_id = (
                    SELECT match_source_identity_id
                    FROM match_analysis_jobs WHERE id = ?
                )
                """,
                (job_id,),
            ).fetchone()
            canonical = connection.execute(
                """
                SELECT COUNT(*) AS count, MAX(match_score) AS score
                FROM identity_professor_match_results
                WHERE identity_id = (
                    SELECT match_source_identity_id
                    FROM match_analysis_jobs WHERE id = ?
                )
                """,
                (job_id,),
            ).fetchone()
            completion_log_count = int(
                connection.execute(
                    """
                    SELECT COUNT(*) FROM operation_logs
                    WHERE event_name = 'match_analysis_job.completed'
                          AND entity_type = 'match_analysis_job'
                          AND entity_id = ?
                    """,
                    (str(job_id),),
                ).fetchone()[0]
            )
            return {
                **dict(row),
                "run_count": int(run_counts["total"] or 0),
                "running_run_count": int(run_counts["running"] or 0),
                "succeeded_run_count": int(run_counts["succeeded"] or 0),
                "canonical_count": int(canonical["count"] or 0),
                "canonical_score": canonical["score"],
                "completion_log_count": completion_log_count,
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
