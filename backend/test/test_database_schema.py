from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from app.services.outreach_templates import import_outreach_template_file
from test.migrated_database import create_migrated_sqlite_database


BACKEND_DIR = Path(__file__).resolve().parents[1]
HEAD_REVISION = "a1b2c3d4e5f6"
LEGACY_RUNTIME_REVISION = "7a1d5e42c9bd"


class DatabaseSchemaTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "schema_test.db"
        create_migrated_sqlite_database(self.db_path)

        self.connection = sqlite3.connect(self.db_path)
        self.connection.execute("PRAGMA foreign_keys = ON")

    def tearDown(self) -> None:
        self.connection.close()
        self.temp_dir.cleanup()

    def test_runtime_tables_and_columns_are_created(self) -> None:
        rows = self.connection.execute(
            """
            SELECT name
            FROM sqlite_master
            WHERE type = 'table'
            """,
        ).fetchall()
        table_names = {row[0] for row in rows}

        self.assertTrue(
            {
                "alembic_version",
                "identity_profiles",
                "identity_materials",
                "llm_profiles",
                "professors",
                "email_tasks",
                "batch_tasks",
                "email_logs",
                "app_settings",
                "test_compose_sessions",
                "test_compose_messages",
                "operation_logs",
                "match_analysis_runs",
                "match_analysis_jobs",
                "match_analysis_job_items",
            }.issubset(table_names),
        )
        self.assertNotIn("attachment_assets", table_names)

        identity_columns = self._get_columns("identity_profiles")
        batch_columns = self._get_columns("batch_tasks")
        task_columns = self._get_columns("email_tasks")
        material_columns = self._get_columns("identity_materials")
        professor_columns = self._get_columns("professors")
        log_columns = self._get_columns("email_logs")
        settings_columns = self._get_columns("app_settings")
        operation_log_columns = self._get_columns("operation_logs")
        match_run_columns = self._get_columns("match_analysis_runs")
        match_job_columns = self._get_columns("match_analysis_jobs")
        match_job_item_columns = self._get_columns("match_analysis_job_items")

        self.assertIn("current_primary_material_id", identity_columns)
        self.assertNotIn("resume_file_path", identity_columns)
        self.assertNotIn("resume_text", identity_columns)
        self.assertIn("primary_material_id", batch_columns)
        self.assertIn("selected_material_ids", batch_columns)
        self.assertIn("scheduled_dates", batch_columns)
        self.assertNotIn("selected_attachment_ids", batch_columns)
        self.assertIn("primary_material_id", task_columns)
        self.assertIn("selected_material_ids", task_columns)
        self.assertIn("draft_generation_previous_status", task_columns)
        self.assertNotIn("selected_attachments", task_columns)
        self.assertIn("display_name", material_columns)
        self.assertIn("original_filename", material_columns)
        self.assertIn("sha256", material_columns)
        self.assertIn("material_type", material_columns)
        self.assertIn("archived_at", professor_columns)
        self.assertIn("provider_payload", log_columns)
        self.assertIn("reply_headers", log_columns)
        self.assertNotIn("mail_delivery_mode", settings_columns)
        self.assertTrue(
            {
                "match_analysis_job_worker_count",
                "match_analysis_job_item_concurrency",
                "match_analysis_job_interval_seconds",
                "crawler_worker_count",
                "crawler_profile_enrichment_concurrency",
                "crawler_host_concurrency",
                "draft_max_tokens",
                "batch_draft_generation_concurrency",
                "draft_rewrite_intensity",
                "draft_rewrite_tone",
                "draft_rewrite_formality",
                "draft_rewrite_length",
                "draft_rewrite_specificity",
                "draft_template_preservation",
                "draft_custom_instruction",
            }.issubset(settings_columns),
        )
        self.assertNotIn("signature", identity_columns)
        self.assertTrue(
            {
                "id",
                "request_id",
                "category",
                "event_name",
                "level",
                "message",
                "entity_type",
                "entity_id",
                "metadata",
                "created_at",
            }.issubset(operation_log_columns),
        )
        self.assertTrue(
            {
                "id",
                "email_task_id",
                "professor_id",
                "identity_id",
                "llm_profile_id",
                "success",
                "match_score",
                "prompt_tokens",
                "completion_tokens",
                "total_tokens",
                "cached_tokens",
                "duration_ms",
                "endpoint_kind",
                "status_code",
                "prompt_hash",
                "stable_prefix_hash",
                "status",
                "started_at",
                "finished_at",
                "error_kind",
                "error_message",
                "created_at",
            }.issubset(match_run_columns),
        )
        self.assertTrue(
            {
                "id",
                "name",
                "identity_id",
                "llm_profile_id",
                "status",
                "target_count",
                "succeeded_count",
                "failed_count",
                "skipped_count",
                "total_prompt_tokens",
                "total_completion_tokens",
                "total_tokens",
                "cancel_requested_at",
                "started_at",
                "finished_at",
                "created_at",
                "updated_at",
                "last_error",
            }.issubset(match_job_columns),
        )
        self.assertTrue(
            {
                "id",
                "job_id",
                "professor_id",
                "email_task_id",
                "status",
                "match_analysis_run_id",
                "error_message",
                "skip_reason",
                "prompt_tokens",
                "completion_tokens",
                "total_tokens",
                "started_at",
                "finished_at",
                "created_at",
                "updated_at",
            }.issubset(match_job_item_columns),
        )

        operation_log_indexes = {
            row[1]
            for row in self.connection.execute(
                "PRAGMA index_list('operation_logs')"
            ).fetchall()
        }
        self.assertTrue(
            {
                "ix_operation_logs_request_id",
                "ix_operation_logs_category",
                "ix_operation_logs_event_name",
                "ix_operation_logs_entity_type",
                "ix_operation_logs_entity_id",
                "ix_operation_logs_created_at",
            }.issubset(operation_log_indexes),
        )
        match_run_indexes = {
            row[1]
            for row in self.connection.execute(
                "PRAGMA index_list('match_analysis_runs')"
            ).fetchall()
        }
        self.assertTrue(
            {
                "ix_match_analysis_runs_email_task_id",
                "ix_match_analysis_runs_professor_id",
                "ix_match_analysis_runs_created_at",
                "uq_match_analysis_runs_running_per_task",
            }.issubset(match_run_indexes),
        )
        match_job_indexes = {
            row[1]
            for row in self.connection.execute(
                "PRAGMA index_list('match_analysis_jobs')"
            ).fetchall()
        }
        self.assertTrue(
            {
                "ix_match_analysis_jobs_status",
                "ix_match_analysis_jobs_identity_id",
                "ix_match_analysis_jobs_llm_profile_id",
            }.issubset(match_job_indexes),
        )
        match_job_item_indexes = {
            row[1]
            for row in self.connection.execute(
                "PRAGMA index_list('match_analysis_job_items')"
            ).fetchall()
        }
        self.assertTrue(
            {
                "ix_match_analysis_job_items_job_id",
                "ix_match_analysis_job_items_status",
                "ix_match_analysis_job_items_email_task_id",
                "ix_match_analysis_job_items_professor_id",
                "ix_match_analysis_job_items_match_analysis_run_id",
            }.issubset(match_job_item_indexes),
        )

    def test_task_tables_have_deleted_at_for_trash(self) -> None:
        self.assertIn("deleted_at", self._get_columns("batch_tasks"))
        self.assertIn("deleted_at", self._get_columns("crawl_jobs"))
        self.assertIn("deleted_at", self._get_columns("match_analysis_jobs"))

    def test_crawl_job_tables_exist(self) -> None:
        rows = self.connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'",
        ).fetchall()
        table_names = {row[0] for row in rows}

        self.assertIn("crawl_jobs", table_names)
        self.assertIn("crawl_job_runs", table_names)
        self.assertIn("crawl_pages", table_names)
        self.assertIn("crawl_candidates", table_names)

        self.assertIn("current_run_id", self._get_columns("crawl_jobs"))
        self.assertTrue(
            {
                "job_id",
                "attempt_number",
                "active_seconds",
                "input_tokens",
                "output_tokens",
                "cached_tokens",
                "retry_count",
                "host_limited_count",
                "failed_candidate_count",
                "unchanged_candidate_count",
                "total_tokens",
            }.issubset(self._get_columns("crawl_job_runs")),
        )

    def test_html_template_import_derives_text_from_sanitized_html(self) -> None:
        imported = import_outreach_template_file(
            "template.html",
            b'<p>Hello <strong>{{name}}</strong></p><script>alert(1)</script>',
        )

        self.assertEqual(imported.body_html, "<p>Hello <strong>{{name}}</strong></p>")
        self.assertEqual(imported.body_text, "Hello {{name}}")

    def test_old_revision_can_upgrade_to_head(self) -> None:
        legacy_db_path = Path(self.temp_dir.name) / "old_revision_upgrade.db"
        env = os.environ.copy()
        env["DATABASE_URL"] = f"sqlite+aiosqlite:///{legacy_db_path.as_posix()}"

        self._run_alembic(env, "upgrade", "04d66ff4c25b")
        self._run_alembic(env, "upgrade", "head")

        connection = sqlite3.connect(legacy_db_path)
        version = connection.execute(
            "SELECT version_num FROM alembic_version",
        ).fetchone()[0]
        connection.close()

        self.assertEqual(version, HEAD_REVISION)

    def test_concurrency_guard_migration_cleans_existing_duplicates(self) -> None:
        legacy_db_path = Path(self.temp_dir.name) / "concurrency_guard_duplicates.db"
        env = os.environ.copy()
        env["DATABASE_URL"] = f"sqlite+aiosqlite:///{legacy_db_path.as_posix()}"

        self._run_alembic(env, "upgrade", "c6d7e8f9a012")
        connection = sqlite3.connect(legacy_db_path)
        connection.execute("PRAGMA foreign_keys = ON")
        try:
            identity_id = self._insert_identity_into(connection, email_address="duplicate-identity@example.com")
            llm_profile_id = self._insert_llm_profile_into(connection, name="重复清理模型")
            professor_id = self._insert_professor_into(connection, "duplicate-professor@example.edu")
            first_task_id = self._insert_workspace_root_task_into(connection, identity_id, llm_profile_id, professor_id)
            second_task_id = self._insert_workspace_root_task_into(connection, identity_id, llm_profile_id, professor_id)
            self._insert_email_log_into(
                connection,
                first_task_id,
                identity_id,
                llm_profile_id,
                professor_id,
                rfc_message_id="<duplicate@example.edu>",
            )
            self._insert_email_log_into(
                connection,
                second_task_id,
                identity_id,
                llm_profile_id,
                professor_id,
                rfc_message_id="<duplicate@example.edu>",
            )
            connection.commit()
        finally:
            connection.close()

        self._run_alembic(env, "upgrade", "head")

        connection = sqlite3.connect(legacy_db_path)
        try:
            version = connection.execute("SELECT version_num FROM alembic_version").fetchone()[0]
            root_count = connection.execute(
                """
                SELECT COUNT(*)
                FROM email_tasks
                WHERE source = 'manual'
                  AND batch_task_id IS NULL
                  AND parent_task_id IS NULL
                  AND professor_id = ?
                  AND identity_id = ?
                  AND llm_profile_id = ?
                """,
                (professor_id, identity_id, llm_profile_id),
            ).fetchone()[0]
            log_count = connection.execute(
                "SELECT COUNT(*) FROM email_logs WHERE rfc_message_id = ?",
                ("<duplicate@example.edu>",),
            ).fetchone()[0]
        finally:
            connection.close()

        self.assertEqual(version, HEAD_REVISION)
        self.assertEqual(root_count, 1)
        self.assertEqual(log_count, 1)

    def test_runtime_code_has_no_mail_delivery_mode_residue(self) -> None:
        banned_terms = [
            "dry_run",
            "mail_delivery_mode",
            "MailDeliveryMode",
            "default_mail_delivery_mode",
            "SystemSettingsRead",
            "SystemSettingsUpdate",
        ]
        runtime_files = sorted((BACKEND_DIR / "app").rglob("*.py"))
        violations: list[str] = []
        for path in runtime_files:
            content = path.read_text(encoding="utf-8")
            for term in banned_terms:
                if term in content:
                    violations.append(f"{path.relative_to(BACKEND_DIR)}: {term}")

        self.assertEqual(violations, [])

    def test_defaults_and_foreign_keys_work(self) -> None:
        identity_id = self._insert_identity()
        llm_profile_id = self._insert_llm_profile()
        professor_id = self._insert_professor("defaults@example.edu")

        self.connection.execute(
            """
            INSERT INTO batch_tasks (
                identity_id,
                llm_profile_id,
                name,
                target_count,
                primary_material_id,
                selected_material_ids
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (identity_id, llm_profile_id, "测试任务", 1, None, json.dumps([])),
        )
        batch_task_id = self.connection.execute(
            "SELECT id FROM batch_tasks",
        ).fetchone()[0]

        self.connection.execute(
            """
            INSERT INTO email_tasks (
                batch_task_id,
                identity_id,
                llm_profile_id,
                professor_id,
                primary_material_id,
                selected_material_ids
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (batch_task_id, identity_id, llm_profile_id, professor_id, None, json.dumps([])),
        )
        email_task_id = self.connection.execute("SELECT id FROM email_tasks").fetchone()[0]

        self.connection.execute(
            """
            INSERT INTO email_logs (email_task_id, identity_id, llm_profile_id, professor_id, direction, content)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (email_task_id, identity_id, llm_profile_id, professor_id, "sent", "hello"),
        )

        status, retry_count, is_read, is_replied = self.connection.execute(
            """
            SELECT status, retry_count, is_read, is_replied
            FROM email_tasks
            WHERE id = ?
            """,
            (email_task_id,),
        ).fetchone()

        self.assertEqual(status, "discovered")
        self.assertEqual(retry_count, 0)
        self.assertEqual(is_read, 0)
        self.assertEqual(is_replied, 0)

    def test_email_tasks_has_manual_source_and_cancellation_fields(self) -> None:
        task_columns = self._get_columns("email_tasks")

        self.assertIn("source", task_columns)
        self.assertIn("parent_task_id", task_columns)
        self.assertIn("cancellation_reason", task_columns)

        identity_id = self._insert_identity()
        llm_profile_id = self._insert_llm_profile()
        professor_id = self._insert_professor("manual-source@example.edu")

        self.connection.execute(
            """
            INSERT INTO email_tasks (
                identity_id,
                llm_profile_id,
                professor_id,
                primary_material_id,
                selected_material_ids
            )
            VALUES (?, ?, ?, ?, ?)
            """,
            (identity_id, llm_profile_id, professor_id, None, json.dumps([])),
        )

        source, parent_task_id, cancellation_reason = self.connection.execute(
            """
            SELECT source, parent_task_id, cancellation_reason
            FROM email_tasks
            ORDER BY id DESC
            LIMIT 1
            """,
        ).fetchone()

        self.assertEqual(source, "manual")
        self.assertIsNone(parent_task_id)
        self.assertIsNone(cancellation_reason)

    def test_email_tasks_parent_task_id_is_unique_for_non_null_values(self) -> None:
        indexes = self.connection.execute("PRAGMA index_list('email_tasks')").fetchall()
        unique_indexes = [row for row in indexes if row[2] == 1]
        indexed_columns = set()
        for index in unique_indexes:
            for column in self.connection.execute(f"PRAGMA index_info('{index[1]}')").fetchall():
                indexed_columns.add(column[2])

        self.assertIn("parent_task_id", indexed_columns)

        identity_id = self._insert_identity()
        llm_profile_id = self._insert_llm_profile()
        professor_id = self._insert_professor("unique-parent@example.edu")

        self.connection.execute(
            """
            INSERT INTO email_tasks (
                identity_id,
                llm_profile_id,
                professor_id,
                primary_material_id,
                selected_material_ids
            )
            VALUES (?, ?, ?, ?, ?)
            """,
            (identity_id, llm_profile_id, professor_id, None, json.dumps([])),
        )
        parent_task_id = self.connection.execute(
            "SELECT id FROM email_tasks ORDER BY id DESC LIMIT 1",
        ).fetchone()[0]

        self.connection.execute(
            """
            INSERT INTO email_tasks (
                source,
                parent_task_id,
                identity_id,
                llm_profile_id,
                professor_id,
                primary_material_id,
                selected_material_ids
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            ("manual", parent_task_id, identity_id, llm_profile_id, professor_id, None, json.dumps([])),
        )

        with self.assertRaises(sqlite3.IntegrityError):
            self.connection.execute(
                """
                INSERT INTO email_tasks (
                    source,
                    parent_task_id,
                    identity_id,
                    llm_profile_id,
                    professor_id,
                    primary_material_id,
                    selected_material_ids
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                ("manual", parent_task_id, identity_id, llm_profile_id, professor_id, None, json.dumps([])),
            )

    def test_contact_task_state_migration_backfill_and_downgrade_restore_legacy_statuses(self) -> None:
        legacy_dir = tempfile.TemporaryDirectory()
        try:
            legacy_db_path = Path(legacy_dir.name) / "legacy_task_states.db"
            legacy_env = os.environ.copy()
            legacy_env["DATABASE_URL"] = f"sqlite+aiosqlite:///{legacy_db_path.as_posix()}"

            self._run_alembic(legacy_env, "upgrade", "2f6a9d8c1e20")

            legacy = sqlite3.connect(legacy_db_path)
            legacy.execute("PRAGMA foreign_keys = ON")

            identity_id = legacy.execute(
                """
                INSERT INTO identity_profiles (
                    name,
                    profile_name,
                    sender_name,
                    email_address,
                    smtp_host,
                    smtp_username,
                    smtp_password
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "旧身份",
                    "旧身份",
                    "旧发件人",
                    "legacy-task-state@example.com",
                    "smtp.example.com",
                    "legacy-task-state@example.com",
                    "secret",
                ),
            ).lastrowid
            llm_profile_id = legacy.execute(
                """
                INSERT INTO llm_profiles (name, provider, api_key, model_name)
                VALUES (?, ?, ?, ?)
                """,
                ("默认模型", "openai", "sk-test-key", "gpt-4o-mini"),
            ).lastrowid
            professor_id = legacy.execute(
                """
                INSERT INTO professors (name, email, research_direction, crawl_status)
                VALUES (?, ?, ?, ?)
                """,
                ("导师甲", "legacy-task-prof@example.edu", "agents", "discovered"),
            ).lastrowid
            running_batch_task_id = legacy.execute(
                """
                INSERT INTO batch_tasks (
                    identity_id,
                    llm_profile_id,
                    name,
                    target_count,
                    primary_material_id,
                    selected_material_ids
                )
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (identity_id, llm_profile_id, "运行中批量任务", 1, None, json.dumps([])),
            ).lastrowid
            stopped_batch_task_id = legacy.execute(
                """
                INSERT INTO batch_tasks (
                    identity_id,
                    llm_profile_id,
                    name,
                    target_count,
                    primary_material_id,
                    selected_material_ids
                )
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (identity_id, llm_profile_id, "已停止批量任务", 1, None, json.dumps([])),
            ).lastrowid
            legacy.execute(
                "UPDATE batch_tasks SET status = ? WHERE id = ?",
                ("stopped", stopped_batch_task_id),
            )

            legacy.execute(
                """
                INSERT INTO email_tasks (
                    batch_task_id,
                    identity_id,
                    llm_profile_id,
                    professor_id,
                    primary_material_id,
                    selected_material_ids,
                    status
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (None, identity_id, llm_profile_id, professor_id, None, json.dumps([]), "skipped"),
            )
            manual_task_id = legacy.execute("SELECT last_insert_rowid()").fetchone()[0]
            legacy.execute(
                """
                INSERT INTO email_tasks (
                    batch_task_id,
                    identity_id,
                    llm_profile_id,
                    professor_id,
                    primary_material_id,
                    selected_material_ids,
                    status
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    running_batch_task_id,
                    identity_id,
                    llm_profile_id,
                    professor_id,
                    None,
                    json.dumps([]),
                    "skipped",
                ),
            )
            running_batch_task_item_id = legacy.execute("SELECT last_insert_rowid()").fetchone()[0]
            legacy.execute(
                """
                INSERT INTO email_tasks (
                    batch_task_id,
                    identity_id,
                    llm_profile_id,
                    professor_id,
                    primary_material_id,
                    selected_material_ids,
                    status
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    stopped_batch_task_id,
                    identity_id,
                    llm_profile_id,
                    professor_id,
                    None,
                    json.dumps([]),
                    "skipped",
                ),
            )
            stopped_batch_task_item_id = legacy.execute("SELECT last_insert_rowid()").fetchone()[0]
            legacy.commit()
            legacy.close()

            self._run_alembic(legacy_env, "upgrade", "head")

            upgraded = sqlite3.connect(legacy_db_path)
            upgraded.execute("PRAGMA foreign_keys = ON")
            upgraded_rows = upgraded.execute(
                """
                SELECT id, status, source, cancellation_reason
                FROM email_tasks
                ORDER BY id
                """
            ).fetchall()

            self.assertEqual(
                upgraded_rows,
                [
                    (manual_task_id, "matched", "manual", None),
                    (running_batch_task_item_id, "matched", "batch", None),
                    (stopped_batch_task_item_id, "canceled", "batch", "batch_stopped"),
                ],
            )

            upgraded.execute(
                """
                INSERT INTO email_tasks (
                    source,
                    batch_task_id,
                    identity_id,
                    llm_profile_id,
                    professor_id,
                    primary_material_id,
                    selected_material_ids,
                    status,
                    cancellation_reason
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "batch",
                    stopped_batch_task_id,
                    identity_id,
                    llm_profile_id,
                    professor_id,
                    None,
                    json.dumps([]),
                    "canceled",
                    "batch_stopped",
                ),
            )
            post_upgrade_task_id = upgraded.execute("SELECT last_insert_rowid()").fetchone()[0]
            upgraded.commit()
            upgraded.close()

            self._run_alembic(legacy_env, "downgrade", "2f6a9d8c1e20")

            downgraded = sqlite3.connect(legacy_db_path)
            downgraded.execute("PRAGMA foreign_keys = ON")
            task_columns = {row[1] for row in downgraded.execute("PRAGMA table_info('email_tasks')").fetchall()}
            downgraded_rows = downgraded.execute(
                """
                SELECT id, status
                FROM email_tasks
                ORDER BY id
                """
            ).fetchall()
            version = downgraded.execute(
                "SELECT version_num FROM alembic_version"
            ).fetchone()[0]
            downgraded.close()

            self.assertNotIn("source", task_columns)
            self.assertNotIn("parent_task_id", task_columns)
            self.assertNotIn("cancellation_reason", task_columns)
            self.assertEqual(version, "2f6a9d8c1e20")
            self.assertEqual(
                downgraded_rows,
                [
                    (manual_task_id, "skipped"),
                    (running_batch_task_item_id, "skipped"),
                    (stopped_batch_task_item_id, "skipped"),
                    (post_upgrade_task_id, "skipped"),
                ],
            )
        finally:
            legacy_dir.cleanup()

    def test_legacy_resume_and_attachment_data_are_backfilled(self) -> None:
        legacy_dir = tempfile.TemporaryDirectory()
        try:
            legacy_db_path = Path(legacy_dir.name) / "legacy_schema.db"
            legacy_env = os.environ.copy()
            legacy_env["DATABASE_URL"] = f"sqlite+aiosqlite:///{legacy_db_path.as_posix()}"

            self._run_alembic(legacy_env, "upgrade", LEGACY_RUNTIME_REVISION)

            resume_path = Path(legacy_dir.name) / "legacy_resume.txt"
            attachment_path = Path(legacy_dir.name) / "legacy_attachment.txt"
            resume_path.write_text("Legacy resume text", encoding="utf-8")
            attachment_path.write_text("Legacy attachment text", encoding="utf-8")

            connection = sqlite3.connect(legacy_db_path)
            connection.execute("PRAGMA foreign_keys = ON")

            identity_id = connection.execute(
                """
                INSERT INTO identity_profiles (
                    name,
                    email_address,
                    smtp_host,
                    smtp_username,
                    smtp_password,
                    resume_file_path,
                    resume_text
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "旧身份",
                    "legacy@example.com",
                    "smtp.example.com",
                    "legacy@example.com",
                    "secret",
                    resume_path.as_posix(),
                    "Legacy resume text",
                ),
            ).lastrowid
            llm_profile_id = connection.execute(
                """
                INSERT INTO llm_profiles (name, provider, api_key, model_name)
                VALUES (?, ?, ?, ?)
                """,
                ("默认模型", "openai", "sk-test-key", "gpt-4o-mini"),
            ).lastrowid
            professor_id = connection.execute(
                """
                INSERT INTO professors (name, email, research_direction, crawl_status)
                VALUES (?, ?, ?, ?)
                """,
                ("李老师", "legacy-prof@example.edu", "大模型", "discovered"),
            ).lastrowid
            attachment_id = connection.execute(
                """
                INSERT INTO attachment_assets (identity_id, file_name, file_path, mime_type)
                VALUES (?, ?, ?, ?)
                """,
                (
                    identity_id,
                    "legacy_attachment.txt",
                    attachment_path.as_posix(),
                    "text/plain",
                ),
            ).lastrowid
            batch_task_id = connection.execute(
                """
                INSERT INTO batch_tasks (
                    identity_id,
                    llm_profile_id,
                    name,
                    target_count,
                    selected_attachment_ids
                )
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    identity_id,
                    llm_profile_id,
                    "旧批次任务",
                    1,
                    json.dumps([attachment_id]),
                ),
            ).lastrowid
            connection.execute(
                """
                INSERT INTO email_tasks (
                    batch_task_id,
                    identity_id,
                    llm_profile_id,
                    professor_id,
                    selected_attachments
                )
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    batch_task_id,
                    identity_id,
                    llm_profile_id,
                    professor_id,
                    json.dumps([attachment_id]),
                ),
            )
            connection.commit()
            connection.close()

            self._run_alembic(legacy_env, "upgrade", "head")

            upgraded = sqlite3.connect(legacy_db_path)
            upgraded.execute("PRAGMA foreign_keys = ON")

            material_rows = upgraded.execute(
                """
                SELECT display_name, original_filename, material_type
                FROM identity_materials
                WHERE identity_id = ?
                ORDER BY id
                """,
                (identity_id,),
            ).fetchall()
            current_primary_material_id = upgraded.execute(
                "SELECT current_primary_material_id FROM identity_profiles WHERE id = ?",
                (identity_id,),
            ).fetchone()[0]
            batch_row = upgraded.execute(
                """
                SELECT primary_material_id, selected_material_ids
                FROM batch_tasks
                WHERE id = ?
                """,
                (batch_task_id,),
            ).fetchone()
            email_row = upgraded.execute(
                """
                SELECT primary_material_id, selected_material_ids
                FROM email_tasks
                WHERE batch_task_id = ?
                """,
                (batch_task_id,),
            ).fetchone()

            self.assertEqual(len(material_rows), 2)
            self.assertEqual({row[2] for row in material_rows}, {"resume", "other"})
            self.assertIsNotNone(current_primary_material_id)
            self.assertEqual(batch_row[0], current_primary_material_id)
            self.assertEqual(email_row[0], current_primary_material_id)
            self.assertEqual(len(self._load_json(batch_row[1])), 1)
            self.assertEqual(len(self._load_json(email_row[1])), 1)

            upgraded.close()
        finally:
            legacy_dir.cleanup()

    def _run_alembic(self, env: dict[str, str], *args: str) -> None:
        result = subprocess.run(
            [sys.executable, "-m", "alembic", *args],
            cwd=BACKEND_DIR,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            self.fail(
                "Alembic command failed.\n"
                f"command: {' '.join(args)}\n"
                f"stdout:\n{result.stdout}\n"
                f"stderr:\n{result.stderr}",
            )

    def _get_columns(self, table_name: str) -> set[str]:
        rows = self.connection.execute(f"PRAGMA table_info('{table_name}')").fetchall()
        return {row[1] for row in rows}

    @staticmethod
    def _load_json(raw_value: str | None) -> list[int]:
        if raw_value is None:
            return []
        if isinstance(raw_value, str):
            return json.loads(raw_value)
        return list(raw_value)

    def _insert_identity(self) -> int:
        return self._insert_identity_into(self.connection, email_address="identity-default@example.com")

    @staticmethod
    def _insert_identity_into(connection: sqlite3.Connection, *, email_address: str) -> int:
        cursor = connection.execute(
            """
            INSERT INTO identity_profiles (
                name,
                profile_name,
                sender_name,
                email_address,
                smtp_host,
                smtp_username,
                smtp_password
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "默认身份",
                "默认身份",
                "默认发件人",
                email_address,
                "smtp.example.com",
                email_address,
                "secret",
            ),
        )
        return int(cursor.lastrowid)

    def _insert_llm_profile(self) -> int:
        return self._insert_llm_profile_into(self.connection, name="默认模型")

    @staticmethod
    def _insert_llm_profile_into(connection: sqlite3.Connection, *, name: str) -> int:
        cursor = connection.execute(
            """
            INSERT INTO llm_profiles (
                name,
                provider,
                api_key,
                model_name
            )
            VALUES (?, ?, ?, ?)
            """,
            (
                name,
                "openai",
                "sk-test-key",
                "gpt-4o-mini",
            ),
        )
        return int(cursor.lastrowid)

    def _insert_professor(self, email: str) -> int:
        return self._insert_professor_into(self.connection, email)

    @staticmethod
    def _insert_professor_into(connection: sqlite3.Connection, email: str) -> int:
        cursor = connection.execute(
            """
            INSERT INTO professors (name, email, research_direction, crawl_status)
            VALUES (?, ?, ?, ?)
            """,
            ("王老师", email, "知识图谱与大模型", "discovered"),
        )
        return int(cursor.lastrowid)

    @staticmethod
    def _insert_workspace_root_task_into(
        connection: sqlite3.Connection,
        identity_id: int,
        llm_profile_id: int,
        professor_id: int,
    ) -> int:
        cursor = connection.execute(
            """
            INSERT INTO email_tasks (
                source,
                identity_id,
                llm_profile_id,
                professor_id,
                status,
                selected_material_ids
            )
            VALUES ('manual', ?, ?, ?, 'discovered', ?)
            """,
            (identity_id, llm_profile_id, professor_id, json.dumps([])),
        )
        return int(cursor.lastrowid)

    @staticmethod
    def _insert_email_log_into(
        connection: sqlite3.Connection,
        email_task_id: int,
        identity_id: int,
        llm_profile_id: int,
        professor_id: int,
        *,
        rfc_message_id: str,
    ) -> int:
        cursor = connection.execute(
            """
            INSERT INTO email_logs (
                email_task_id,
                identity_id,
                llm_profile_id,
                professor_id,
                direction,
                content,
                rfc_message_id
            )
            VALUES (?, ?, ?, ?, 'sent', 'hello', ?)
            """,
            (email_task_id, identity_id, llm_profile_id, professor_id, rfc_message_id),
        )
        return int(cursor.lastrowid)


if __name__ == "__main__":
    unittest.main()

