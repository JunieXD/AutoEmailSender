from __future__ import annotations

import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.core.migrations import (
    PUBLIC_BETA_REVISION,
    get_head_revision,
    run_migrations_to_head,
)
from app.core.schema_metadata import read_app_metadata
from test.migrated_database import create_migrated_sqlite_database


FORMAL_BETA_BASELINE = "20260811_delivery_reconcile"
BETA_ATTEMPT_ID = "11111111-1111-1111-1111-111111111111"


class PublicBetaUpgradeTests(unittest.TestCase):
    def tearDown(self) -> None:
        from app.core.config import get_settings

        get_settings.cache_clear()
        os.environ.pop("DATABASE_URL", None)
        os.environ.pop("AUTO_EMAIL_SENDER_DATA_DIR", None)
        os.environ.pop("AUTO_EMAIL_SENDER_APP_VERSION", None)

    def test_public_beta_database_upgrades_to_formal_head_with_backup(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            database_path = root / "auto_email_sender.db"
            data_dir = root / "data"
            create_migrated_sqlite_database(
                database_path,
                revision=FORMAL_BETA_BASELINE,
            )
            seeded_ids = _convert_to_public_beta_fixture(database_path)

            with patch.dict(
                os.environ,
                {
                    "DATABASE_URL": (f"sqlite+aiosqlite:///{database_path.as_posix()}"),
                    "AUTO_EMAIL_SENDER_DATA_DIR": str(data_dir),
                    "AUTO_EMAIL_SENDER_APP_VERSION": "2.6.0",
                },
            ):
                from app.core.config import get_settings

                get_settings.cache_clear()
                run_migrations_to_head()

            backup_dir = data_dir / "backups" / "schema"
            backup_paths = list(backup_dir.glob("*.db"))
            metadata_paths = list(backup_dir.glob("*.json"))
            self.assertEqual(len(backup_paths), 1)
            self.assertEqual(len(metadata_paths), 1)
            _assert_beta_backup(backup_paths[0], seeded_ids)

            backup_metadata = json.loads(
                metadata_paths[0].read_text(encoding="utf-8"),
            )
            self.assertEqual(
                backup_metadata["source_schema_revision"],
                PUBLIC_BETA_REVISION,
            )
            self.assertEqual(
                backup_metadata["target_schema_revision"],
                get_head_revision(),
            )

            connection = sqlite3.connect(database_path)
            try:
                revision = connection.execute(
                    "SELECT version_num FROM alembic_version",
                ).fetchone()[0]
                attempt = connection.execute(
                    """
                    SELECT id, email_task_id, identity_id, professor_id,
                           attempt_number, recipient_email, status,
                           app_message_id
                    FROM email_delivery_attempts
                    WHERE id = ?
                    """,
                    (BETA_ATTEMPT_ID,),
                ).fetchone()
                attempt_columns = {
                    str(row[1])
                    for row in connection.execute(
                        "PRAGMA table_info(email_delivery_attempts)",
                    )
                }
                task_columns = {
                    str(row[1])
                    for row in connection.execute("PRAGMA table_info(email_tasks)")
                }
                crawl_columns = {
                    str(row[1])
                    for row in connection.execute("PRAGMA table_info(crawl_jobs)")
                }
                log_attempt_id = connection.execute(
                    "SELECT delivery_attempt_id FROM email_logs WHERE id = ?",
                    (seeded_ids["email_log_id"],),
                ).fetchone()[0]
                observation_attempt_id = connection.execute(
                    "SELECT delivery_attempt_id FROM email_observations WHERE id = ?",
                    (seeded_ids["observation_id"],),
                ).fetchone()[0]
                metadata = read_app_metadata(connection)
                integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
                foreign_key_violations = connection.execute(
                    "PRAGMA foreign_key_check",
                ).fetchall()
            finally:
                connection.close()

            self.assertEqual(revision, get_head_revision())
            self.assertEqual(
                attempt,
                (
                    BETA_ATTEMPT_ID,
                    seeded_ids["email_task_id"],
                    seeded_ids["identity_id"],
                    seeded_ids["professor_id"],
                    1,
                    "beta-professor@example.edu",
                    "accepted",
                    "<beta-attempt@example.test>",
                ),
            )
            self.assertEqual(log_attempt_id, BETA_ATTEMPT_ID)
            self.assertEqual(observation_attempt_id, BETA_ATTEMPT_ID)
            self.assertNotIn("owner_role", attempt_columns)
            self.assertNotIn("outcome", attempt_columns)
            self.assertNotIn("delivery_attempt_id", task_columns)
            self.assertIn("active_candidate_enrichment_operation_id", crawl_columns)
            self.assertIn("active_candidate_enrichment_skipped_count", crawl_columns)
            self.assertEqual(metadata["schema_revision"], get_head_revision())
            self.assertEqual(metadata["schema_updated_by_app_version"], "2.6.0")
            self.assertEqual(integrity, "ok")
            self.assertEqual(foreign_key_violations, [])

    def test_public_beta_marker_without_beta_schema_is_rejected_unchanged(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            database_path = root / "auto_email_sender.db"
            data_dir = root / "data"
            create_migrated_sqlite_database(
                database_path,
                revision=FORMAL_BETA_BASELINE,
            )
            connection = sqlite3.connect(database_path)
            try:
                connection.execute(
                    "UPDATE alembic_version SET version_num = ?",
                    (PUBLIC_BETA_REVISION,),
                )
                connection.commit()
            finally:
                connection.close()

            with patch.dict(
                os.environ,
                {
                    "DATABASE_URL": (f"sqlite+aiosqlite:///{database_path.as_posix()}"),
                    "AUTO_EMAIL_SENDER_DATA_DIR": str(data_dir),
                    "AUTO_EMAIL_SENDER_APP_VERSION": "2.6.0",
                },
            ):
                from app.core.config import get_settings

                get_settings.cache_clear()
                with self.assertRaisesRegex(
                    RuntimeError,
                    "does not match the published beta schema",
                ):
                    run_migrations_to_head()

            connection = sqlite3.connect(database_path)
            try:
                revision = connection.execute(
                    "SELECT version_num FROM alembic_version",
                ).fetchone()[0]
            finally:
                connection.close()
            self.assertEqual(revision, PUBLIC_BETA_REVISION)
            self.assertFalse((data_dir / "backups" / "schema").exists())

    def test_public_beta_backup_failure_does_not_normalize_revision(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            database_path = root / "auto_email_sender.db"
            data_dir = root / "data"
            create_migrated_sqlite_database(
                database_path,
                revision=FORMAL_BETA_BASELINE,
            )
            _convert_to_public_beta_fixture(database_path)

            with patch.dict(
                os.environ,
                {
                    "DATABASE_URL": (f"sqlite+aiosqlite:///{database_path.as_posix()}"),
                    "AUTO_EMAIL_SENDER_DATA_DIR": str(data_dir),
                    "AUTO_EMAIL_SENDER_APP_VERSION": "2.6.0",
                },
            ):
                from app.core.config import get_settings
                import app.core.migrations as migrations

                get_settings.cache_clear()
                with (
                    patch.object(
                        migrations,
                        "create_schema_backup",
                        side_effect=OSError("backup failed"),
                    ),
                    patch.object(migrations.command, "upgrade") as upgrade,
                ):
                    with self.assertRaisesRegex(OSError, "backup failed"):
                        run_migrations_to_head()

            connection = sqlite3.connect(database_path)
            try:
                revision = connection.execute(
                    "SELECT version_num FROM alembic_version",
                ).fetchone()[0]
            finally:
                connection.close()
            self.assertEqual(revision, PUBLIC_BETA_REVISION)
            upgrade.assert_not_called()


def _convert_to_public_beta_fixture(database_path: Path) -> dict[str, int]:
    connection = sqlite3.connect(database_path)
    try:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.executescript(
            """
            ALTER TABLE email_delivery_attempts
                ADD COLUMN owner_role VARCHAR(16) DEFAULT 'legacy' NOT NULL;
            ALTER TABLE email_delivery_attempts
                ADD COLUMN runtime_id VARCHAR(128) DEFAULT 'legacy' NOT NULL;
            ALTER TABLE email_delivery_attempts
                ADD COLUMN owner_generation VARCHAR(128) DEFAULT 'pre-split' NOT NULL;
            ALTER TABLE email_delivery_attempts
                ADD COLUMN owner_pid INTEGER DEFAULT 0 NOT NULL;
            ALTER TABLE email_delivery_attempts
                ADD COLUMN outcome VARCHAR(48) DEFAULT 'claimed' NOT NULL;
            ALTER TABLE email_delivery_attempts ADD COLUMN finalized_at DATETIME;
            ALTER TABLE email_delivery_attempts ADD COLUMN smtp_accepted_at DATETIME;
            ALTER TABLE email_delivery_attempts
                ADD COLUMN prepared_rfc_message_id VARCHAR(255);
            ALTER TABLE email_delivery_attempts ADD COLUMN subject TEXT DEFAULT '' NOT NULL;
            ALTER TABLE email_delivery_attempts ADD COLUMN content TEXT DEFAULT '' NOT NULL;
            ALTER TABLE email_delivery_attempts ADD COLUMN content_html TEXT;
            ALTER TABLE email_delivery_attempts
                ADD COLUMN attachment_count INTEGER DEFAULT 0 NOT NULL;
            ALTER TABLE email_delivery_attempts ADD COLUMN provider_payload JSON;
            ALTER TABLE email_delivery_attempts ADD COLUMN error_summary TEXT;
            CREATE INDEX ix_email_delivery_attempts_outcome_finalized
                ON email_delivery_attempts (outcome, finalized_at);

            ALTER TABLE email_tasks ADD COLUMN delivery_attempt_id VARCHAR(36);
            ALTER TABLE email_tasks ADD COLUMN delivery_outcome VARCHAR(48);
            ALTER TABLE email_tasks ADD COLUMN delivery_outcome_at DATETIME;
            CREATE INDEX ix_email_tasks_delivery_sending_attempt
                ON email_tasks (delivery_attempt_id) WHERE status = 'sending';
            """,
        )
        identity_id = int(
            connection.execute(
                """
                INSERT INTO identity_profiles (
                    name, profile_name, sender_name, email_address,
                    smtp_host, smtp_username, smtp_password
                ) VALUES (
                    'Beta identity', 'Beta identity', 'Beta sender',
                    'beta-sender@example.com', 'smtp.example.com',
                    'beta-sender@example.com', 'secret'
                )
                """,
            ).lastrowid,
        )
        llm_profile_id = int(
            connection.execute(
                """
                INSERT INTO llm_profiles (name, provider, api_key, model_name)
                VALUES ('Beta model', 'openai', 'test-key', 'test-model')
                """,
            ).lastrowid,
        )
        professor_id = int(
            connection.execute(
                """
                INSERT INTO professors (name, email)
                VALUES ('Beta professor', 'beta-professor@example.edu')
                """,
            ).lastrowid,
        )
        email_task_id = int(
            connection.execute(
                """
                INSERT INTO email_tasks (
                    identity_id, llm_profile_id, professor_id, status,
                    delivery_attempt_id, delivery_outcome, delivery_outcome_at
                ) VALUES (?, ?, ?, 'sent', ?, 'smtp_accepted', CURRENT_TIMESTAMP)
                """,
                (identity_id, llm_profile_id, professor_id, BETA_ATTEMPT_ID),
            ).lastrowid,
        )
        connection.execute(
            """
            INSERT INTO email_delivery_attempts (
                id, email_task_id, identity_id, professor_id, attempt_number,
                recipient_email, subject_fingerprint, content_fingerprint,
                app_message_id, normalized_app_message_id, status,
                started_at, completed_at,
                owner_role, runtime_id, owner_generation, owner_pid,
                outcome, finalized_at, smtp_accepted_at,
                prepared_rfc_message_id, subject, content, attachment_count
            ) VALUES (
                ?, ?, ?, ?, 1, 'beta-professor@example.edu',
                'sha256:subject', 'sha256:content',
                '<beta-attempt@example.test>', '<beta-attempt@example.test>',
                'accepted', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP,
                'worker', 'beta-runtime', 'beta-generation', 42,
                'smtp_accepted', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP,
                '<beta-attempt@example.test>', 'Beta subject', 'Beta content', 0
            )
            """,
            (BETA_ATTEMPT_ID, email_task_id, identity_id, professor_id),
        )
        email_log_id = int(
            connection.execute(
                """
                INSERT INTO email_logs (
                    email_task_id, identity_id, llm_profile_id, professor_id,
                    direction, subject, content, rfc_message_id,
                    delivery_attempt_id, record_state, reconciliation_version
                ) VALUES (
                    ?, ?, ?, ?, 'sent', 'Beta subject', 'Beta content',
                    '<beta-attempt@example.test>', ?, 'canonical', 1
                )
                """,
                (
                    email_task_id,
                    identity_id,
                    llm_profile_id,
                    professor_id,
                    BETA_ATTEMPT_ID,
                ),
            ).lastrowid,
        )
        observation_id = int(
            connection.execute(
                """
                INSERT INTO email_observations (
                    email_log_id, delivery_attempt_id, identity_id,
                    professor_id, direction, source, resolution,
                    message_sent_at, observed_at
                ) VALUES (
                    ?, ?, ?, ?, 'sent', 'smtp', 'matched',
                    CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                )
                """,
                (email_log_id, BETA_ATTEMPT_ID, identity_id, professor_id),
            ).lastrowid,
        )
        connection.execute(
            "UPDATE alembic_version SET version_num = ?",
            (PUBLIC_BETA_REVISION,),
        )
        connection.executemany(
            """
            INSERT INTO app_metadata (key, value)
            VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """,
            (
                ("minimum_supported_app_version", "2.6.0-beta.1"),
                ("schema_updated_by_app_version", "2.6.0-beta.1"),
                ("schema_revision", PUBLIC_BETA_REVISION),
            ),
        )
        connection.commit()
        return {
            "identity_id": identity_id,
            "professor_id": professor_id,
            "email_task_id": email_task_id,
            "email_log_id": email_log_id,
            "observation_id": observation_id,
        }
    finally:
        connection.close()


def _assert_beta_backup(backup_path: Path, seeded_ids: dict[str, int]) -> None:
    connection = sqlite3.connect(backup_path)
    try:
        revision = connection.execute(
            "SELECT version_num FROM alembic_version",
        ).fetchone()[0]
        beta_attempt = connection.execute(
            """
            SELECT runtime_id, owner_generation, outcome, subject, content
            FROM email_delivery_attempts
            WHERE id = ?
            """,
            (BETA_ATTEMPT_ID,),
        ).fetchone()
        beta_task = connection.execute(
            "SELECT delivery_attempt_id FROM email_tasks WHERE id = ?",
            (seeded_ids["email_task_id"],),
        ).fetchone()[0]
        integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
    finally:
        connection.close()

    if revision != PUBLIC_BETA_REVISION:
        raise AssertionError(f"Unexpected beta backup revision: {revision}")
    if beta_attempt != (
        "beta-runtime",
        "beta-generation",
        "smtp_accepted",
        "Beta subject",
        "Beta content",
    ):
        raise AssertionError(f"Beta delivery attempt changed in backup: {beta_attempt}")
    if beta_task != BETA_ATTEMPT_ID:
        raise AssertionError(f"Beta task attempt changed in backup: {beta_task}")
    if integrity != "ok":
        raise AssertionError(f"Beta backup integrity failed: {integrity}")


if __name__ == "__main__":
    unittest.main()
