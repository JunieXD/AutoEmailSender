from __future__ import annotations

import hashlib
import sqlite3
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path

from test.migrated_database import create_migrated_sqlite_database
from test.process_harness import DesktopBackendProcess


PREVIOUS_STABLE_REVISION = "20260808_crawl_llm_snapshot"
CURRENT_REVISION = "20260812_merge_beta_master"


class PreviousStableSplitUpgradeTests(unittest.TestCase):
    def test_idle_queued_running_leased_and_sending_snapshot_upgrades_safely(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            data_dir = Path(temp_dir) / "data"
            database_path = data_dir / "auto_email_sender.db"
            create_migrated_sqlite_database(
                database_path,
                revision=PREVIOUS_STABLE_REVISION,
            )
            snapshot_ids = _seed_previous_stable_states(database_path)
            material_path = data_dir / "materials" / "用户 材料.txt"
            material_path.parent.mkdir(parents=True)
            material_path.write_text("must remain byte-identical\n", encoding="utf-8")
            material_digest = _sha256(material_path)

            api = DesktopBackendProcess(data_dir=data_dir, role="api")
            try:
                api.start()
                api.wait_ready()

                backups = sorted((data_dir / "backups" / "schema").glob("*.db"))
                self.assertEqual(len(backups), 1)
                _assert_previous_snapshot_in_backup(
                    backups[0],
                    snapshot_ids,
                )
                self.assertTrue(backups[0].with_suffix(".json").is_file())
                self.assertEqual(_sha256(material_path), material_digest)

                connection = sqlite3.connect(database_path)
                connection.row_factory = sqlite3.Row
                try:
                    self.assertEqual(
                        connection.execute(
                            "SELECT version_num FROM alembic_version"
                        ).fetchone()[0],
                        CURRENT_REVISION,
                    )
                    states = {
                        row["id"]: row
                        for row in connection.execute(
                            """
                            SELECT id, status, draft_claim_id,
                                   draft_lease_expires_at,
                                   delivery_attempt_id, delivery_outcome
                            FROM email_tasks
                            WHERE id IN (?, ?, ?, ?)
                            """,
                            (
                                snapshot_ids["idle_task"],
                                snapshot_ids["queued_task"],
                                snapshot_ids["draft_task"],
                                snapshot_ids["sending_task"],
                            ),
                        )
                    }
                    self.assertEqual(
                        states[snapshot_ids["idle_task"]]["status"],
                        "review_required",
                    )
                    self.assertEqual(
                        states[snapshot_ids["queued_task"]]["status"],
                        "approved",
                    )
                    sending = states[snapshot_ids["sending_task"]]
                    self.assertEqual(sending["status"], "sent")
                    self.assertEqual(
                        sending["delivery_attempt_id"],
                        f"legacy-{snapshot_ids['sending_task']}",
                    )
                    self.assertEqual(
                        sending["delivery_outcome"],
                        "assumed_sent_after_interruption",
                    )

                    draft = states[snapshot_ids["draft_task"]]
                    if draft["status"] == "generating_draft":
                        self.assertIsNotNone(draft["draft_claim_id"])
                        self.assertIsNotNone(draft["draft_lease_expires_at"])
                    else:
                        self.assertIsNone(draft["draft_claim_id"])
                        self.assertIsNone(draft["draft_lease_expires_at"])

                    _assert_claim_coherence(connection)
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
                api.stop()

            self.assertEqual(_sha256(material_path), material_digest)


def _seed_previous_stable_states(database_path: Path) -> dict[str, int]:
    connection = sqlite3.connect(database_path)
    try:
        connection.execute("PRAGMA foreign_keys = ON")
        now_utc = datetime.now(UTC)
        now = _sqlite_timestamp(now_utc)
        future = _sqlite_timestamp(now_utc + timedelta(days=1))
        identity_id = int(
            connection.execute(
                """
                INSERT INTO identity_profiles (
                    name, profile_name, sender_name, email_address,
                    smtp_host, smtp_username, smtp_password,
                    imap_host, imap_port, imap_username, imap_password
                )
                VALUES (
                    'Upgrade identity', 'Upgrade identity', 'Upgrade sender',
                    'upgrade-sender@example.com', 'smtp.example.com',
                    'upgrade-sender@example.com', 'secret',
                    '127.0.0.1', 1, 'upgrade-sender@example.com', 'secret'
                )
                """
            ).lastrowid
        )
        llm_profile_id = int(
            connection.execute(
                """
                INSERT INTO llm_profiles (name, provider, api_key, model_name)
                VALUES ('Upgrade model', 'openai', 'test-key', 'test-model')
                """
            ).lastrowid
        )
        professor_ids = [
            int(
                connection.execute(
                    "INSERT INTO professors (name, email) VALUES (?, ?)",
                    (f"Upgrade professor {index}", f"upgrade-{index}@example.edu"),
                ).lastrowid
            )
            for index in range(1, 6)
        ]
        batch_task_id = int(
            connection.execute(
                """
                INSERT INTO batch_tasks (
                    identity_id, llm_profile_id, name, status, target_count
                )
                VALUES (?, ?, 'Upgrade running batch', 'running', 1)
                """,
                (identity_id, llm_profile_id),
            ).lastrowid
        )

        def add_email_task(
            professor_id: int,
            status: str,
            *,
            source: str = "manual",
            batch_id: int | None = None,
            leased: bool = False,
        ) -> int:
            return int(
                connection.execute(
                    """
                    INSERT INTO email_tasks (
                        source, batch_task_id, identity_id, llm_profile_id,
                        professor_id, status, approved_subject,
                        approved_body_text, selected_material_ids,
                        draft_generation_previous_status,
                        draft_generation_started_at, draft_claim_id,
                        draft_claimed_at, draft_lease_expires_at,
                        last_send_attempt_at, created_at, updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, 'Upgrade subject',
                            'Upgrade body', '[]', ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        source,
                        batch_id,
                        identity_id,
                        llm_profile_id,
                        professor_id,
                        status,
                        "matched" if leased else None,
                        now if leased else None,
                        "legacy-draft-claim" if leased else None,
                        now if leased else None,
                        future if leased else None,
                        now if status == "sending" else None,
                        now,
                        now,
                    ),
                ).lastrowid
            )

        idle_task = add_email_task(professor_ids[0], "review_required")
        queued_task = add_email_task(professor_ids[1], "approved")
        draft_task = add_email_task(
            professor_ids[2],
            "generating_draft",
            source="batch",
            batch_id=batch_task_id,
            leased=True,
        )
        sending_task = add_email_task(professor_ids[3], "sending")

        match_job_id = int(
            connection.execute(
                """
                INSERT INTO match_analysis_jobs (
                    name, identity_id, llm_profile_id, status, target_count,
                    started_at
                )
                VALUES ('Upgrade match job', ?, ?, 'running', 1, ?)
                """,
                (identity_id, llm_profile_id, now),
            ).lastrowid
        )
        match_item_id = int(
            connection.execute(
                """
                INSERT INTO match_analysis_job_items (
                    job_id, professor_id, status, claim_id,
                    claimed_at, lease_expires_at, attempt_count, started_at
                )
                VALUES (?, ?, 'running', 'legacy-match-claim', ?, ?, 1, ?)
                """,
                (match_job_id, professor_ids[4], now, future, now),
            ).lastrowid
        )
        connection.execute(
            """
            INSERT INTO imap_identity_sync_leases (
                identity_id, claim_id, claim_kind, claimed_at, lease_expires_at
            )
            VALUES (?, 'legacy-imap-claim', 'incremental', ?, ?)
            """,
            (identity_id, now, future),
        )

        crawl_job_id = int(
            connection.execute(
                """
                INSERT INTO crawl_jobs (
                    university, school, start_url, status, progress_total
                )
                VALUES (
                    'Upgrade University', 'Upgrade School',
                    'https://example.invalid/faculty', 'running', 3
                )
                """
            ).lastrowid
        )
        crawl_run_id = int(
            connection.execute(
                """
                INSERT INTO crawl_job_runs (
                    job_id, attempt_number, status, started_at, active_started_at
                )
                VALUES (?, 1, 'running', ?, ?)
                """,
                (crawl_job_id, now, now),
            ).lastrowid
        )
        connection.execute(
            "UPDATE crawl_jobs SET current_run_id = ? WHERE id = ?",
            (crawl_run_id, crawl_job_id),
        )
        crawl_page_task_id = int(
            connection.execute(
                """
                INSERT INTO crawl_page_tasks (
                    job_id, normalized_url, original_url, status, worker_id,
                    claimed_at, lease_expires_at, attempt_count
                )
                VALUES (
                    ?, 'https://example.invalid/faculty',
                    'https://example.invalid/faculty', 'processing',
                    'legacy-page-worker', ?, ?, 1
                )
                """,
                (crawl_job_id, now, future),
            ).lastrowid
        )
        crawl_chunk_id = int(
            connection.execute(
                """
                INSERT INTO crawl_page_chunks (
                    job_id, source_url, page_fingerprint, chunk_id,
                    chunk_index, chunk_hash, content, status, worker_id,
                    claimed_at, lease_expires_at, attempt_count
                )
                VALUES (
                    ?, 'https://example.invalid/faculty', 'fingerprint',
                    'legacy-chunk', 0, 'chunk-hash', 'chunk content',
                    'processing', 'legacy-chunk-worker', ?, ?, 1
                )
                """,
                (crawl_job_id, now, future),
            ).lastrowid
        )
        candidate_id = int(
            connection.execute(
                """
                INSERT INTO crawl_candidates (job_id, name, email)
                VALUES (?, 'Upgrade candidate', 'candidate@example.edu')
                """,
                (crawl_job_id,),
            ).lastrowid
        )
        enrichment_task_id = int(
            connection.execute(
                """
                INSERT INTO crawl_candidate_enrichment_tasks (
                    job_id, candidate_id, status, worker_id,
                    claimed_at, lease_expires_at, attempt_count, started_at
                )
                VALUES (
                    ?, ?, 'processing', 'legacy-enrichment-worker',
                    ?, ?, 1, ?
                )
                """,
                (crawl_job_id, candidate_id, now, future, now),
            ).lastrowid
        )
        connection.commit()
        return {
            "idle_task": idle_task,
            "queued_task": queued_task,
            "draft_task": draft_task,
            "sending_task": sending_task,
            "batch_task": batch_task_id,
            "match_job": match_job_id,
            "match_item": match_item_id,
            "crawl_job": crawl_job_id,
            "crawl_page_task": crawl_page_task_id,
            "crawl_chunk": crawl_chunk_id,
            "enrichment_task": enrichment_task_id,
        }
    finally:
        connection.close()


def _assert_previous_snapshot_in_backup(
    backup_path: Path,
    snapshot_ids: dict[str, int],
) -> None:
    connection = sqlite3.connect(backup_path)
    try:
        self_revision = connection.execute(
            "SELECT version_num FROM alembic_version"
        ).fetchone()[0]
        sending_status = connection.execute(
            "SELECT status FROM email_tasks WHERE id = ?",
            (snapshot_ids["sending_task"],),
        ).fetchone()[0]
        match_claim = connection.execute(
            "SELECT claim_id FROM match_analysis_job_items WHERE id = ?",
            (snapshot_ids["match_item"],),
        ).fetchone()[0]
        crawl_worker = connection.execute(
            "SELECT worker_id FROM crawl_page_tasks WHERE id = ?",
            (snapshot_ids["crawl_page_task"],),
        ).fetchone()[0]
        integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
    finally:
        connection.close()
    if self_revision != PREVIOUS_STABLE_REVISION:
        raise AssertionError(f"Unexpected backup revision: {self_revision}")
    if sending_status != "sending":
        raise AssertionError(f"Backup changed sending status: {sending_status}")
    if match_claim != "legacy-match-claim":
        raise AssertionError(f"Backup changed match claim: {match_claim}")
    if crawl_worker != "legacy-page-worker":
        raise AssertionError(f"Backup changed crawl worker: {crawl_worker}")
    if integrity != "ok":
        raise AssertionError(f"Backup integrity check failed: {integrity}")


def _assert_claim_coherence(connection: sqlite3.Connection) -> None:
    incoherent_drafts = connection.execute(
        """
        SELECT COUNT(*)
        FROM email_tasks
        WHERE (status = 'generating_draft') != (draft_claim_id IS NOT NULL)
        """
    ).fetchone()[0]
    incoherent_match = connection.execute(
        """
        SELECT COUNT(*)
        FROM match_analysis_job_items
        WHERE (status = 'running') != (claim_id IS NOT NULL)
        """
    ).fetchone()[0]
    incoherent_page = connection.execute(
        """
        SELECT COUNT(*)
        FROM crawl_page_tasks
        WHERE (status = 'processing') != (worker_id IS NOT NULL)
        """
    ).fetchone()[0]
    incoherent_chunk = connection.execute(
        """
        SELECT COUNT(*)
        FROM crawl_page_chunks
        WHERE (status = 'processing') != (worker_id IS NOT NULL)
        """
    ).fetchone()[0]
    incoherent_enrichment = connection.execute(
        """
        SELECT COUNT(*)
        FROM crawl_candidate_enrichment_tasks
        WHERE (status = 'processing') != (worker_id IS NOT NULL)
        """
    ).fetchone()[0]
    if any(
        (
            incoherent_drafts,
            incoherent_match,
            incoherent_page,
            incoherent_chunk,
            incoherent_enrichment,
        )
    ):
        raise AssertionError(
            "Claim coherence audit failed: "
            f"draft={incoherent_drafts}, match={incoherent_match}, "
            f"page={incoherent_page}, chunk={incoherent_chunk}, "
            f"enrichment={incoherent_enrichment}"
        )


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _sqlite_timestamp(value: datetime) -> str:
    return value.astimezone(UTC).replace(tzinfo=None).isoformat(sep=" ")


if __name__ == "__main__":
    unittest.main()
