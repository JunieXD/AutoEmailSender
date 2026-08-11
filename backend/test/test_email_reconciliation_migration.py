from __future__ import annotations

import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path

from test.migrated_database import create_migrated_sqlite_database
from test.test_database_schema import run_alembic_in_process


class EmailReconciliationMigrationTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_upgrade_quarantines_candidates_and_downgrade_preserves_every_row(self) -> None:
        database_path = Path(self.temp_dir.name) / "legacy-email-history.db"
        create_migrated_sqlite_database(
            database_path,
            revision="20260810_agent_ui_handoffs",
        )
        with sqlite3.connect(database_path) as connection:
            connection.execute(
                """
                INSERT INTO identity_profiles(
                    id, name, profile_name, sender_name, email_address,
                    smtp_host, smtp_username, smtp_password
                ) VALUES (1, 'Student', 'Student', 'Student', 'student@example.edu',
                          'smtp.example.edu', 'student@example.edu', 'secret')
                """,
            )
            connection.execute(
                """
                INSERT INTO llm_profiles(id, name, api_key, model_name)
                VALUES (1, 'Test', 'secret', 'test-model')
                """,
            )
            connection.execute(
                "INSERT INTO professors(id, name, email) VALUES (1, 'Teacher', 'teacher@example.edu')",
            )
            connection.executemany(
                """
                INSERT INTO email_tasks(
                    id, source, identity_id, llm_profile_id, professor_id, status, retry_count
                ) VALUES (?, 'batch', 1, 1, 1, 'sent', ?)
                """,
                [(1, 1), (2, 1), (3, 1), (4, 1), (5, 2)],
            )
            rows = [
                (
                    1,
                    1,
                    "system",
                    "Unique",
                    "Exact body",
                    "<178642000000.1.1@example.edu>",
                    None,
                    None,
                    None,
                    "2026-08-11 10:00:00",
                    None,
                ),
                (
                    2,
                    None,
                    "imap",
                    "Unique",
                    "Exact body",
                    "<tencent_unique@example.edu>",
                    "sent",
                    "Sent",
                    10,
                    "2026-08-11 10:00:02",
                    "2026-08-11 10:10:00",
                ),
                (
                    3,
                    None,
                    "imap",
                    "External",
                    "External body",
                    "<external@example.edu>",
                    "sent",
                    "Sent",
                    11,
                    "2026-08-11 10:30:00",
                    "2026-08-11 11:00:00",
                ),
                (
                    4,
                    2,
                    "system",
                    "Repeated",
                    "Same body",
                    "<first-repeat@example.edu>",
                    None,
                    None,
                    None,
                    "2026-08-11 11:00:00",
                    None,
                ),
                (
                    5,
                    3,
                    "system",
                    "Repeated",
                    "Same body",
                    "<second-repeat@example.edu>",
                    None,
                    None,
                    None,
                    "2026-08-11 11:00:20",
                    None,
                ),
                (
                    6,
                    None,
                    "imap",
                    "Repeated",
                    "Same body",
                    "<ambiguous-repeat@example.edu>",
                    "sent",
                    "Sent",
                    12,
                    "2026-08-11 11:00:10",
                    "2026-08-11 11:10:00",
                ),
                (
                    7,
                    4,
                    "system",
                    "No server-side sent copy",
                    "System log remains authoritative",
                    "<system-only@example.edu>",
                    None,
                    None,
                    None,
                    "2026-08-11 12:00:00",
                    None,
                ),
                (
                    8,
                    5,
                    "system",
                    "Retry",
                    "Same retry body",
                    None,
                    None,
                    None,
                    None,
                    "2026-08-11 13:00:00",
                    None,
                ),
                (
                    9,
                    5,
                    "system",
                    "Retry",
                    "Same retry body",
                    "<retry-success@example.edu>",
                    None,
                    None,
                    None,
                    "2026-08-11 13:01:00",
                    None,
                ),
            ]
            connection.executemany(
                """
                INSERT INTO email_logs(
                    id, email_task_id, identity_id, llm_profile_id, professor_id,
                    direction, subject, content, rfc_message_id, ingest_source,
                    folder_role, folder, uidvalidity, imap_uid, normalized_message_id,
                    from_email, to_emails, synced_at, created_at
                ) VALUES (
                    ?, ?, 1, CASE WHEN ? = 'system' THEN 1 ELSE NULL END, 1,
                    'sent', ?, ?, ?, ?, ?, ?, 1, ?, lower(?),
                    CASE WHEN ? = 'imap' THEN 'student@example.edu' ELSE NULL END,
                    CASE WHEN ? = 'imap' THEN '["teacher@example.edu"]' ELSE NULL END,
                    ?, ?
                )
                """,
                [
                    (
                        row_id,
                        task_id,
                        ingest_source,
                        subject,
                        content,
                        message_id,
                        ingest_source,
                        folder_role,
                        folder,
                        imap_uid,
                        message_id,
                        ingest_source,
                        ingest_source,
                        synced_at,
                        created_at,
                    )
                    for (
                        row_id,
                        task_id,
                        ingest_source,
                        subject,
                        content,
                        message_id,
                        folder_role,
                        folder,
                        imap_uid,
                        created_at,
                        synced_at,
                    ) in rows
                ],
            )
            connection.execute(
                "UPDATE email_logs SET failure_summary = 'SMTP rejected' WHERE id = 8",
            )
            connection.commit()

        env = os.environ.copy()
        env["DATABASE_URL"] = f"sqlite+aiosqlite:///{database_path.as_posix()}"
        run_alembic_in_process(env, "upgrade", "head")

        with sqlite3.connect(database_path) as connection:
            states = connection.execute(
                "SELECT id, record_state, merged_into_id FROM email_logs ORDER BY id",
            ).fetchall()
            observations = connection.execute(
                """
                SELECT legacy_email_log_id, email_log_id, candidate_email_log_id,
                       resolution, match_method, message_id
                FROM email_observations
                ORDER BY legacy_email_log_id
                """,
            ).fetchall()
            attempt_count = connection.execute(
                "SELECT COUNT(*) FROM email_delivery_attempts",
            ).fetchone()[0]
            retry_attempts = connection.execute(
                """
                SELECT attempt_number, status
                FROM email_delivery_attempts
                WHERE email_task_id = 5
                ORDER BY attempt_number
                """,
            ).fetchall()
            summary_row = connection.execute(
                "SELECT value FROM app_metadata WHERE key = 'email_reconciliation_v1_summary'",
            ).fetchone()

        self.assertEqual(
            states,
            [
                (1, "canonical", None),
                (2, "pending", None),
                (3, "canonical", None),
                (4, "canonical", None),
                (5, "canonical", None),
                (6, "pending", None),
                (7, "canonical", None),
                (8, "canonical", None),
                (9, "canonical", None),
            ],
        )
        self.assertEqual(
            observations,
            [
                (
                    2,
                    None,
                    1,
                    "pending",
                    "legacy_automatic_fold_v1",
                    "<tencent_unique@example.edu>",
                ),
                (
                    3,
                    3,
                    None,
                    "external",
                    "legacy_external_v1",
                    "<external@example.edu>",
                ),
                (
                    6,
                    None,
                    4,
                    "pending",
                    "legacy_automatic_fold_v1",
                    "<ambiguous-repeat@example.edu>",
                ),
            ],
        )
        self.assertEqual(attempt_count, 6)
        self.assertEqual(retry_attempts, [(1, "failed"), (2, "accepted")])
        self.assertEqual(
            json.loads(summary_row[0]),
            {
                "attempt_count": 6,
                "candidate_count": 2,
                "external_count": 1,
                "matched_count": 0,
                "observation_count": 3,
                "pending_count": 2,
            },
        )

        run_alembic_in_process(env, "downgrade", "20260810_agent_ui_handoffs")
        with sqlite3.connect(database_path) as connection:
            email_log_count = connection.execute(
                "SELECT COUNT(*) FROM email_logs",
            ).fetchone()[0]
            email_log_columns = {
                row[1]
                for row in connection.execute("PRAGMA table_info(email_logs)").fetchall()
            }
            reconciliation_tables = {
                row[0]
                for row in connection.execute(
                    """
                    SELECT name FROM sqlite_master
                    WHERE type = 'table'
                      AND name IN ('email_delivery_attempts', 'email_observations')
                    """,
                ).fetchall()
            }
            summary_after_downgrade = connection.execute(
                "SELECT value FROM app_metadata WHERE key = 'email_reconciliation_v1_summary'",
            ).fetchone()

        self.assertEqual(email_log_count, 9)
        self.assertFalse(
            {
                "delivery_attempt_id",
                "merged_into_id",
                "record_state",
                "reconciliation_version",
            }
            & email_log_columns,
        )
        self.assertEqual(reconciliation_tables, set())
        self.assertIsNone(summary_after_downgrade)

    def test_upgrade_uses_body_time_and_one_to_one_assignment(self) -> None:
        database_path = Path(self.temp_dir.name) / "legacy-one-to-one.db"
        create_migrated_sqlite_database(
            database_path,
            revision="20260810_agent_ui_handoffs",
        )
        with sqlite3.connect(database_path) as connection:
            connection.execute(
                """
                INSERT INTO identity_profiles(
                    id, name, profile_name, sender_name, email_address,
                    smtp_host, smtp_username, smtp_password
                ) VALUES (1, 'Student', 'Student', 'Student', 'student@example.edu',
                          'smtp.example.edu', 'student@example.edu', 'secret')
                """,
            )
            connection.execute(
                """
                INSERT INTO llm_profiles(id, name, api_key, model_name)
                VALUES (1, 'Test', 'secret', 'test-model')
                """,
            )
            connection.execute(
                "INSERT INTO professors(id, name, email) VALUES (1, 'Teacher', 'teacher@example.edu')",
            )
            connection.executemany(
                """
                INSERT INTO email_tasks(
                    id, source, identity_id, llm_profile_id, professor_id, status, retry_count
                ) VALUES (?, 'batch', 1, 1, 1, 'sent', 1)
                """,
                [(1,), (2,), (3,)],
            )
            connection.executemany(
                """
                INSERT INTO email_logs(
                    id, email_task_id, identity_id, llm_profile_id, professor_id,
                    direction, subject, content, rfc_message_id, ingest_source,
                    folder_role, folder, uidvalidity, imap_uid, normalized_message_id,
                    from_email, to_emails, synced_at, created_at
                ) VALUES (
                    ?, ?, 1, ?, 1, 'sent', ?, ?, ?, ?, ?, ?, 1, ?, lower(?),
                    ?, ?, ?, ?
                )
                """,
                [
                    (
                        1,
                        1,
                        1,
                        "Delayed",
                        "Original body long enough to identify",
                        "<app-delayed@example.edu>",
                        "system",
                        None,
                        None,
                        None,
                        "<app-delayed@example.edu>",
                        None,
                        None,
                        None,
                        "2026-08-11 10:00:00",
                    ),
                    (
                        2,
                        None,
                        None,
                        "Delayed",
                        "Provider footer\nOriginal body long enough to identify",
                        "<provider-delayed@example.edu>",
                        "imap",
                        "sent",
                        "Sent",
                        20,
                        "<provider-delayed@example.edu>",
                        "student@example.edu",
                        '["teacher@example.edu"]',
                        "2026-08-11 10:30:00",
                        "2026-08-11 10:08:00",
                    ),
                    (
                        3,
                        2,
                        1,
                        "Same subject",
                        "Software application body",
                        "<app-different@example.edu>",
                        "system",
                        None,
                        None,
                        None,
                        "<app-different@example.edu>",
                        None,
                        None,
                        None,
                        "2026-08-11 11:00:00",
                    ),
                    (
                        4,
                        None,
                        None,
                        "Same subject",
                        "A manually written and unrelated body",
                        "<manual-different@example.edu>",
                        "imap",
                        "sent",
                        "Sent",
                        21,
                        "<manual-different@example.edu>",
                        "student@example.edu",
                        '["teacher@example.edu"]',
                        "2026-08-11 11:10:00",
                        "2026-08-11 11:00:05",
                    ),
                    (
                        5,
                        3,
                        1,
                        "Repeated",
                        "Exactly repeated body",
                        "<app-repeat@example.edu>",
                        "system",
                        None,
                        None,
                        None,
                        "<app-repeat@example.edu>",
                        None,
                        None,
                        None,
                        "2026-08-11 12:00:00",
                    ),
                    (
                        6,
                        None,
                        None,
                        "Repeated",
                        "Exactly repeated body",
                        "<provider-repeat@example.edu>",
                        "imap",
                        "sent",
                        "Sent",
                        22,
                        "<provider-repeat@example.edu>",
                        "student@example.edu",
                        '["teacher@example.edu"]',
                        "2026-08-11 12:10:00",
                        "2026-08-11 12:00:01",
                    ),
                    (
                        7,
                        None,
                        None,
                        "Repeated",
                        "Exactly repeated body",
                        "<manual-repeat@example.edu>",
                        "imap",
                        "sent",
                        "Sent",
                        23,
                        "<manual-repeat@example.edu>",
                        "student@example.edu",
                        '["teacher@example.edu"]',
                        "2026-08-11 12:10:00",
                        "2026-08-11 12:00:02",
                    ),
                ],
            )
            connection.commit()

        env = os.environ.copy()
        env["DATABASE_URL"] = f"sqlite+aiosqlite:///{database_path.as_posix()}"
        run_alembic_in_process(env, "upgrade", "head")

        with sqlite3.connect(database_path) as connection:
            states = connection.execute(
                "SELECT id, record_state FROM email_logs ORDER BY id",
            ).fetchall()
            observations = connection.execute(
                """
                SELECT legacy_email_log_id, candidate_email_log_id, resolution, match_method
                FROM email_observations
                ORDER BY legacy_email_log_id
                """,
            ).fetchall()

        self.assertEqual(
            states,
            [
                (1, "canonical"),
                (2, "pending"),
                (3, "canonical"),
                (4, "canonical"),
                (5, "canonical"),
                (6, "pending"),
                (7, "canonical"),
            ],
        )
        self.assertEqual(
            observations,
            [
                (2, 1, "pending", "legacy_automatic_fold_v1"),
                (4, None, "external", "legacy_external_v1"),
                (6, 5, "pending", "legacy_automatic_fold_v1"),
                (7, None, "external", "legacy_external_v1"),
            ],
        )
