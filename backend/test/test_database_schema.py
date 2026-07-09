from __future__ import annotations

import ast
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from app.services.outreach_templates import import_outreach_template_file
from app.core.migrations import get_alembic_config, get_head_revision
from test.migrated_database import create_migrated_sqlite_database


BACKEND_DIR = Path(__file__).resolve().parents[1]
HEAD_REVISION = get_head_revision(get_alembic_config())
LEGACY_RUNTIME_REVISION = "7a1d5e42c9bd"


class MigrationScriptTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_alembic_revision_ids_are_unique(self) -> None:
        revision_ids: dict[str, list[Path]] = {}
        for migration_path in (BACKEND_DIR / "alembic" / "versions").glob("*.py"):
            tree = ast.parse(migration_path.read_text(encoding="utf-8-sig"))
            revision = None
            for node in tree.body:
                if isinstance(node, ast.Assign):
                    target_names = [target.id for target in node.targets if isinstance(target, ast.Name)]
                elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                    target_names = [node.target.id]
                else:
                    continue
                if "revision" in target_names and isinstance(node.value, ast.Constant):
                    revision = node.value.value
                    break
            self.assertIsInstance(revision, str, migration_path.name)
            revision_ids.setdefault(revision, []).append(migration_path)

        duplicates = {
            revision: [path.name for path in paths]
            for revision, paths in revision_ids.items()
            if len(paths) > 1
        }

        self.assertEqual({}, duplicates)

    def test_unified_email_history_upgrade_skips_normalized_message_duplicates(self) -> None:
        legacy_db_path = Path(self.temp_dir.name) / "unified_email_duplicate_messages.db"
        env = os.environ.copy()
        env["DATABASE_URL"] = f"sqlite+aiosqlite:///{legacy_db_path.as_posix()}"

        self._run_alembic(env, "upgrade", "20260614taskmat")
        connection = sqlite3.connect(legacy_db_path)
        connection.execute("PRAGMA foreign_keys = ON")
        try:
            identity_id = DatabaseSchemaTests._insert_identity_into(
                connection,
                email_address="normalized-duplicate@example.com",
            )
            llm_profile_id = DatabaseSchemaTests._insert_llm_profile_into(
                connection,
                name="Normalized Duplicate Model",
            )
            professor_id = DatabaseSchemaTests._insert_professor_into(
                connection,
                "normalized-duplicate@example.edu",
            )
            first_task_id = DatabaseSchemaTests._insert_workspace_root_task_into(
                connection,
                identity_id,
                llm_profile_id,
                professor_id,
            )
            second_task_id = DatabaseSchemaTests._insert_manual_child_task_into(
                connection,
                parent_task_id=first_task_id,
                identity_id=identity_id,
                llm_profile_id=llm_profile_id,
                professor_id=professor_id,
            )
            first_log_id = DatabaseSchemaTests._insert_email_log_into(
                connection,
                first_task_id,
                identity_id,
                llm_profile_id,
                professor_id,
                rfc_message_id="<Msg@example.edu>",
            )
            second_log_id = DatabaseSchemaTests._insert_email_log_into(
                connection,
                second_task_id,
                identity_id,
                llm_profile_id,
                professor_id,
                rfc_message_id=" <msg@example.edu> ",
            )
            connection.commit()
        finally:
            connection.close()

        self._run_alembic(env, "upgrade", "head")

        upgraded = sqlite3.connect(legacy_db_path)
        try:
            rows = upgraded.execute(
                """
                SELECT id, rfc_message_id, normalized_message_id
                FROM email_logs
                WHERE id IN (?, ?)
                ORDER BY id
                """,
                (first_log_id, second_log_id),
            ).fetchall()
            version = upgraded.execute("SELECT version_num FROM alembic_version").fetchone()[0]
        finally:
            upgraded.close()

        self.assertEqual(version, HEAD_REVISION)
        self.assertEqual(
            rows,
            [
                (first_log_id, "<Msg@example.edu>", "<msg@example.edu>"),
                (second_log_id, " <msg@example.edu> ", None),
            ],
        )

    def test_unified_email_history_downgrade_reports_null_llm_profile_logs(self) -> None:
        legacy_db_path = Path(self.temp_dir.name) / "unified_email_null_llm_downgrade.db"
        env = os.environ.copy()
        env["DATABASE_URL"] = f"sqlite+aiosqlite:///{legacy_db_path.as_posix()}"

        self._run_alembic(env, "upgrade", "head")
        connection = sqlite3.connect(legacy_db_path)
        connection.execute("PRAGMA foreign_keys = ON")
        try:
            identity_id = DatabaseSchemaTests._insert_identity_into(
                connection,
                email_address="null-llm-downgrade@example.com",
            )
            professor_id = DatabaseSchemaTests._insert_professor_into(
                connection,
                "null-llm-downgrade@example.edu",
            )
            connection.execute(
                """
                INSERT INTO email_logs (
                    identity_id,
                    llm_profile_id,
                    professor_id,
                    direction,
                    content,
                    ingest_source
                )
                VALUES (?, NULL, ?, 'received', 'hello', 'imap')
                """,
                (identity_id, professor_id),
            )
            connection.commit()
        finally:
            connection.close()

        result = self._run_alembic_result(env, "downgrade", "20260614taskmat")

        self.assertNotEqual(result.returncode, 0)
        combined_output = result.stdout + result.stderr
        self.assertIn("llm_profile_id", combined_output)
        self.assertIn("NULL", combined_output)
        self.assertIn("cannot downgrade", combined_output)

    def _run_alembic(self, env: dict[str, str], *args: str) -> None:
        result = self._run_alembic_result(env, *args)
        if result.returncode != 0:
            self.fail(
                "Alembic command failed.\n"
                f"command: {' '.join(args)}\n"
                f"stdout:\n{result.stdout}\n"
                f"stderr:\n{result.stderr}",
            )

    @staticmethod
    def _run_alembic_result(env: dict[str, str], *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, "-m", "alembic", *args],
            cwd=BACKEND_DIR,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )


class DatabaseSchemaTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self._schema_db_path = Path(self.temp_dir.name) / "schema_test.db"
        self._connection: sqlite3.Connection | None = None

    def tearDown(self) -> None:
        if self._connection is not None:
            self._connection.close()
        self.temp_dir.cleanup()

    @property
    def connection(self) -> sqlite3.Connection:
        if self._connection is None:
            create_migrated_sqlite_database(self._schema_db_path)
            self._connection = sqlite3.connect(self._schema_db_path)
            self._connection.execute("PRAGMA foreign_keys = ON")
        return self._connection

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
                "crawler_agent_max_chunks_per_run",
                "draft_max_tokens",
                "batch_draft_generation_concurrency",
                "draft_rewrite_intensity",
                "draft_rewrite_tone",
                "draft_rewrite_formality",
                "draft_rewrite_length",
                "draft_rewrite_specificity",
                "draft_template_preservation",
                "draft_custom_instruction",
                "intended_research_direction",
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
                "primary_material_id",
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
                "ix_match_analysis_runs_primary_material_id",
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

    def test_dashboard_professor_query_indexes_are_created(self) -> None:
        self.assertEqual(
            self._get_index_columns("professors", "ix_professors_archived_created_id"),
            ["archived_at", "created_at", "id"],
        )
        self.assertEqual(
            self._get_index_columns("email_tasks", "ix_email_tasks_identity_professor_created_id"),
            ["identity_id", "professor_id", "created_at", "id"],
        )
        self.assertEqual(
            self._get_index_columns(
                "email_logs",
                "ix_email_logs_status_identity_professor_direction_created",
            ),
            ["identity_id", "professor_id", "direction", "created_at", "id"],
        )

    def test_email_tasks_contains_workspace_rewrite_fields(self) -> None:
        task_columns = self._get_columns("email_tasks")

        self.assertIn("draft_generation_started_at", task_columns)
        self.assertIn("draft_rewrite_source_subject", task_columns)
        self.assertIn("draft_rewrite_source_body_text", task_columns)
        self.assertIn("draft_rewrite_source_body_html", task_columns)
        self.assertIn("draft_rewrite_source_selected_material_ids", task_columns)

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
        self.assertIn("crawl_page_fetch_states", table_names)
        self.assertIn("crawl_candidates", table_names)

        self.assertIn("current_run_id", self._get_columns("crawl_jobs"))
        self.assertTrue(
            {
                "id",
                "job_id",
                "normalized_url",
                "original_url",
                "status",
                "last_fetch_method",
                "terminal_reason",
                "transient_failure_count",
                "last_error_message",
                "last_page_id",
                "first_seen_at",
                "last_attempted_at",
                "updated_at",
            }.issubset(self._get_columns("crawl_page_fetch_states")),
        )
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

    def test_app_metadata_table_is_created_by_alembic_head(self) -> None:
        self.assertIn("app_metadata", self._get_table_names())
        self.assertEqual(
            self._get_columns("app_metadata"),
            {"key", "value"},
        )

    def test_migration_backfills_match_run_primary_material_id(self) -> None:
        legacy_dir = tempfile.TemporaryDirectory()
        try:
            legacy_db_path = Path(legacy_dir.name) / "legacy_match_run_material.db"
            legacy_env = os.environ.copy()
            legacy_env["DATABASE_URL"] = f"sqlite+aiosqlite:///{legacy_db_path.as_posix()}"
            self._run_alembic(legacy_env, "upgrade", "20260609rewrite")

            connection = sqlite3.connect(legacy_db_path)
            connection.execute("PRAGMA foreign_keys = ON")
            identity_id = self._insert_identity_into(connection, email_address="run-material@example.com")
            llm_profile_id = self._insert_llm_profile_into(connection, name="运行记录模型")
            professor_id = self._insert_professor_into(connection, "run-material@example.edu")
            material_id = self._insert_identity_material_into(
                connection,
                identity_id,
                original_filename="resume.txt",
                extracted_text="resume",
            )
            task_id = self._insert_email_task_with_material_into(
                connection,
                identity_id,
                llm_profile_id,
                professor_id,
                primary_material_id=material_id,
            )
            connection.execute(
                """
                INSERT INTO match_analysis_runs (
                    email_task_id, professor_id, identity_id, llm_profile_id,
                    status, success, match_score
                )
                VALUES (?, ?, ?, ?, 'succeeded', 1, 88)
                """,
                (task_id, professor_id, identity_id, llm_profile_id),
            )
            connection.commit()
            connection.close()

            self._run_alembic(legacy_env, "upgrade", "head")

            upgraded = sqlite3.connect(legacy_db_path)
            run_material_id = upgraded.execute(
                "SELECT primary_material_id FROM match_analysis_runs",
            ).fetchone()[0]
            upgraded.close()

            self.assertEqual(run_material_id, material_id)
        finally:
            legacy_dir.cleanup()

    def test_migration_leaves_match_run_primary_material_null_when_task_material_is_missing(self) -> None:
        legacy_dir = tempfile.TemporaryDirectory()
        try:
            legacy_db_path = Path(legacy_dir.name) / "legacy_missing_run_material.db"
            legacy_env = os.environ.copy()
            legacy_env["DATABASE_URL"] = f"sqlite+aiosqlite:///{legacy_db_path.as_posix()}"
            self._run_alembic(legacy_env, "upgrade", "20260609rewrite")

            connection = sqlite3.connect(legacy_db_path)
            connection.execute("PRAGMA foreign_keys = OFF")
            identity_id = self._insert_identity_into(connection, email_address="missing-run-material@example.com")
            llm_profile_id = self._insert_llm_profile_into(connection, name="缺失材料模型")
            professor_id = self._insert_professor_into(connection, "missing-run-material@example.edu")
            task_id = self._insert_email_task_with_material_into(
                connection,
                identity_id,
                llm_profile_id,
                professor_id,
                primary_material_id=999999,
            )
            connection.execute(
                """
                INSERT INTO match_analysis_runs (
                    email_task_id, professor_id, identity_id, llm_profile_id,
                    status, success, match_score
                )
                VALUES (?, ?, ?, ?, 'succeeded', 1, 88)
                """,
                (task_id, professor_id, identity_id, llm_profile_id),
            )
            connection.commit()
            connection.close()

            self._run_alembic(legacy_env, "upgrade", "head")

            upgraded = sqlite3.connect(legacy_db_path)
            run_material_id = upgraded.execute(
                "SELECT primary_material_id FROM match_analysis_runs",
            ).fetchone()[0]
            upgraded.close()

            self.assertIsNone(run_material_id)
        finally:
            legacy_dir.cleanup()

    def test_migration_leaves_match_run_primary_material_null_for_cross_identity_task_material(self) -> None:
        legacy_dir = tempfile.TemporaryDirectory()
        try:
            legacy_db_path = Path(legacy_dir.name) / "legacy_cross_identity_run_material.db"
            legacy_env = os.environ.copy()
            legacy_env["DATABASE_URL"] = f"sqlite+aiosqlite:///{legacy_db_path.as_posix()}"
            self._run_alembic(legacy_env, "upgrade", "20260609rewrite")

            connection = sqlite3.connect(legacy_db_path)
            connection.execute("PRAGMA foreign_keys = ON")
            identity_id = self._insert_identity_into(connection, email_address="run-owner@example.com")
            other_identity_id = self._insert_identity_into(connection, email_address="material-owner@example.com")
            llm_profile_id = self._insert_llm_profile_into(connection, name="跨身份材料模型")
            professor_id = self._insert_professor_into(connection, "cross-identity-run@example.edu")
            other_material_id = self._insert_identity_material_into(
                connection,
                other_identity_id,
                original_filename="other-resume.txt",
                extracted_text="other",
            )
            connection.execute("PRAGMA foreign_keys = OFF")
            task_id = self._insert_email_task_with_material_into(
                connection,
                identity_id,
                llm_profile_id,
                professor_id,
                primary_material_id=other_material_id,
            )
            connection.execute(
                """
                INSERT INTO match_analysis_runs (
                    email_task_id, professor_id, identity_id, llm_profile_id,
                    status, success, match_score
                )
                VALUES (?, ?, ?, ?, 'succeeded', 1, 88)
                """,
                (task_id, professor_id, identity_id, llm_profile_id),
            )
            connection.commit()
            connection.close()

            self._run_alembic(legacy_env, "upgrade", "head")

            upgraded = sqlite3.connect(legacy_db_path)
            run_material_id = upgraded.execute(
                "SELECT primary_material_id FROM match_analysis_runs",
            ).fetchone()[0]
            upgraded.close()

            self.assertIsNone(run_material_id)
        finally:
            legacy_dir.cleanup()

    def test_migration_leaves_match_run_primary_material_null_for_non_primary_material_file(self) -> None:
        legacy_dir = tempfile.TemporaryDirectory()
        try:
            legacy_db_path = Path(legacy_dir.name) / "legacy_non_primary_run_material.db"
            legacy_env = os.environ.copy()
            legacy_env["DATABASE_URL"] = f"sqlite+aiosqlite:///{legacy_db_path.as_posix()}"
            self._run_alembic(legacy_env, "upgrade", "20260609rewrite")

            connection = sqlite3.connect(legacy_db_path)
            connection.execute("PRAGMA foreign_keys = ON")
            identity_id = self._insert_identity_into(connection, email_address="run-image-material@example.com")
            llm_profile_id = self._insert_llm_profile_into(connection, name="不可匹配材料模型")
            professor_id = self._insert_professor_into(connection, "run-image-material@example.edu")
            material_id = self._insert_identity_material_into(
                connection,
                identity_id,
                display_name="作品集图片",
                original_filename="portfolio.png",
                extracted_text="image metadata",
                material_type="portfolio",
            )
            task_id = self._insert_email_task_with_material_into(
                connection,
                identity_id,
                llm_profile_id,
                professor_id,
                primary_material_id=material_id,
            )
            connection.execute(
                """
                INSERT INTO match_analysis_runs (
                    email_task_id, professor_id, identity_id, llm_profile_id,
                    status, success, match_score
                )
                VALUES (?, ?, ?, ?, 'succeeded', 1, 88)
                """,
                (task_id, professor_id, identity_id, llm_profile_id),
            )
            connection.commit()
            connection.close()

            self._run_alembic(legacy_env, "upgrade", "head")

            upgraded = sqlite3.connect(legacy_db_path)
            run_material_id = upgraded.execute(
                "SELECT primary_material_id FROM match_analysis_runs",
            ).fetchone()[0]
            upgraded.close()

            self.assertIsNone(run_material_id)
        finally:
            legacy_dir.cleanup()

    def test_migration_recovers_identity_current_primary_material_from_recent_task(self) -> None:
        legacy_dir = tempfile.TemporaryDirectory()
        try:
            legacy_db_path = Path(legacy_dir.name) / "legacy_identity_primary_material.db"
            legacy_env = os.environ.copy()
            legacy_env["DATABASE_URL"] = f"sqlite+aiosqlite:///{legacy_db_path.as_posix()}"
            self._run_alembic(legacy_env, "upgrade", "20260609rewrite")

            connection = sqlite3.connect(legacy_db_path)
            connection.execute("PRAGMA foreign_keys = ON")
            identity_id = self._insert_identity_into(connection, email_address="recover-primary@example.com")
            llm_profile_id = self._insert_llm_profile_into(connection, name="默认材料恢复模型")
            old_professor_id = self._insert_professor_into(connection, "recover-old-primary@example.edu")
            recent_professor_id = self._insert_professor_into(connection, "recover-recent-primary@example.edu")
            old_material_id = self._insert_identity_material_into(
                connection,
                identity_id,
                display_name="旧材料",
                original_filename="old-resume.txt",
                extracted_text="old",
            )
            recent_material_id = self._insert_identity_material_into(
                connection,
                identity_id,
                display_name="最近材料",
                original_filename="recent-resume.txt",
                extracted_text="recent",
            )
            self._insert_email_task_with_material_into(
                connection,
                identity_id,
                llm_profile_id,
                old_professor_id,
                primary_material_id=old_material_id,
                updated_at="2026-06-01 08:00:00",
            )
            self._insert_email_task_with_material_into(
                connection,
                identity_id,
                llm_profile_id,
                recent_professor_id,
                primary_material_id=recent_material_id,
                updated_at="2026-06-02 08:00:00",
            )
            connection.commit()
            connection.close()

            self._run_alembic(legacy_env, "upgrade", "head")

            upgraded = sqlite3.connect(legacy_db_path)
            current_primary_material_id = upgraded.execute(
                "SELECT current_primary_material_id FROM identity_profiles WHERE id = ?",
                (identity_id,),
            ).fetchone()[0]
            upgraded.close()

            self.assertEqual(current_primary_material_id, recent_material_id)
        finally:
            legacy_dir.cleanup()

    def test_migration_recovers_identity_current_primary_material_from_transcript_pdf_task(self) -> None:
        legacy_dir = tempfile.TemporaryDirectory()
        try:
            legacy_db_path = Path(legacy_dir.name) / "legacy_transcript_primary_material.db"
            legacy_env = os.environ.copy()
            legacy_env["DATABASE_URL"] = f"sqlite+aiosqlite:///{legacy_db_path.as_posix()}"
            self._run_alembic(legacy_env, "upgrade", "20260609rewrite")

            connection = sqlite3.connect(legacy_db_path)
            connection.execute("PRAGMA foreign_keys = ON")
            identity_id = self._insert_identity_into(connection, email_address="recover-transcript@example.com")
            llm_profile_id = self._insert_llm_profile_into(connection, name="成绩单默认材料模型")
            professor_id = self._insert_professor_into(connection, "recover-transcript@example.edu")
            material_id = self._insert_identity_material_into(
                connection,
                identity_id,
                display_name="成绩单",
                original_filename="transcript.pdf",
                extracted_text="transcript",
                material_type="transcript",
            )
            self._insert_email_task_with_material_into(
                connection,
                identity_id,
                llm_profile_id,
                professor_id,
                primary_material_id=material_id,
            )
            connection.commit()
            connection.close()

            self._run_alembic(legacy_env, "upgrade", "head")

            upgraded = sqlite3.connect(legacy_db_path)
            current_primary_material_id = upgraded.execute(
                "SELECT current_primary_material_id FROM identity_profiles WHERE id = ?",
                (identity_id,),
            ).fetchone()[0]
            upgraded.close()

            self.assertEqual(current_primary_material_id, material_id)
        finally:
            legacy_dir.cleanup()

    def test_migration_recovers_identity_current_primary_material_from_only_material(self) -> None:
        legacy_dir = tempfile.TemporaryDirectory()
        try:
            legacy_db_path = Path(legacy_dir.name) / "legacy_only_identity_material.db"
            legacy_env = os.environ.copy()
            legacy_env["DATABASE_URL"] = f"sqlite+aiosqlite:///{legacy_db_path.as_posix()}"
            self._run_alembic(legacy_env, "upgrade", "20260609rewrite")

            connection = sqlite3.connect(legacy_db_path)
            connection.execute("PRAGMA foreign_keys = ON")
            identity_id = self._insert_identity_into(connection, email_address="only-primary@example.com")
            material_id = self._insert_identity_material_into(
                connection,
                identity_id,
                display_name="唯一材料",
                original_filename="only-resume.txt",
                extracted_text="only",
            )
            connection.commit()
            connection.close()

            self._run_alembic(legacy_env, "upgrade", "head")

            upgraded = sqlite3.connect(legacy_db_path)
            current_primary_material_id = upgraded.execute(
                "SELECT current_primary_material_id FROM identity_profiles WHERE id = ?",
                (identity_id,),
            ).fetchone()[0]
            upgraded.close()

            self.assertEqual(current_primary_material_id, material_id)
        finally:
            legacy_dir.cleanup()

    def test_migration_backfills_empty_task_primary_material_from_identity_current_primary_material(self) -> None:
        legacy_dir = tempfile.TemporaryDirectory()
        try:
            legacy_db_path = Path(legacy_dir.name) / "legacy_task_primary_material_backfill.db"
            legacy_env = os.environ.copy()
            legacy_env["DATABASE_URL"] = f"sqlite+aiosqlite:///{legacy_db_path.as_posix()}"
            self._run_alembic(legacy_env, "upgrade", "20260609rewrite")

            connection = sqlite3.connect(legacy_db_path)
            connection.execute("PRAGMA foreign_keys = ON")
            identity_id = self._insert_identity_into(connection, email_address="task-backfill@example.com")
            llm_profile_id = self._insert_llm_profile_into(connection, name="任务材料回填模型")
            professor_id = self._insert_professor_into(connection, "task-backfill@example.edu")
            material_id = self._insert_identity_material_into(
                connection,
                identity_id,
                display_name="默认简历",
                original_filename="default-resume.txt",
                extracted_text="resume",
            )
            connection.execute(
                "UPDATE identity_profiles SET current_primary_material_id = ? WHERE id = ?",
                (material_id, identity_id),
            )
            task_id = self._insert_email_task_with_material_into(
                connection,
                identity_id,
                llm_profile_id,
                professor_id,
                primary_material_id=None,
            )
            connection.commit()
            connection.close()

            self._run_alembic(legacy_env, "upgrade", "head")

            upgraded = sqlite3.connect(legacy_db_path)
            task_primary_material_id = upgraded.execute(
                "SELECT primary_material_id FROM email_tasks WHERE id = ?",
                (task_id,),
            ).fetchone()[0]
            upgraded.close()

            self.assertEqual(task_primary_material_id, material_id)
        finally:
            legacy_dir.cleanup()

    def test_migration_leaves_ambiguous_identity_current_primary_material_empty(self) -> None:
        legacy_dir = tempfile.TemporaryDirectory()
        try:
            legacy_db_path = Path(legacy_dir.name) / "legacy_ambiguous_identity_material.db"
            legacy_env = os.environ.copy()
            legacy_env["DATABASE_URL"] = f"sqlite+aiosqlite:///{legacy_db_path.as_posix()}"
            self._run_alembic(legacy_env, "upgrade", "20260609rewrite")

            connection = sqlite3.connect(legacy_db_path)
            connection.execute("PRAGMA foreign_keys = ON")
            identity_id = self._insert_identity_into(connection, email_address="ambiguous-primary@example.com")
            self._insert_identity_material_into(
                connection,
                identity_id,
                display_name="材料 A",
                original_filename="a-resume.txt",
                extracted_text="a",
            )
            self._insert_identity_material_into(
                connection,
                identity_id,
                display_name="材料 B",
                original_filename="b-resume.txt",
                extracted_text="b",
            )
            connection.commit()
            connection.close()

            self._run_alembic(legacy_env, "upgrade", "head")

            upgraded = sqlite3.connect(legacy_db_path)
            current_primary_material_id = upgraded.execute(
                "SELECT current_primary_material_id FROM identity_profiles WHERE id = ?",
                (identity_id,),
            ).fetchone()[0]
            upgraded.close()

            self.assertIsNone(current_primary_material_id)
        finally:
            legacy_dir.cleanup()


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

    def test_identity_next_send_after_upgrade_skips_existing_column(self) -> None:
        legacy_db_path = Path(self.temp_dir.name) / "identity_next_send_after_drift.db"
        env = os.environ.copy()
        env["DATABASE_URL"] = f"sqlite+aiosqlite:///{legacy_db_path.as_posix()}"

        self._run_alembic(env, "upgrade", "20260630_imap_efficiency_guards")
        connection = sqlite3.connect(legacy_db_path)
        try:
            connection.execute("ALTER TABLE identity_profiles ADD COLUMN next_send_after DATETIME")
            connection.commit()
        finally:
            connection.close()

        self._run_alembic(env, "upgrade", "head")

        connection = sqlite3.connect(legacy_db_path)
        try:
            columns = {
                row[1]
                for row in connection.execute("PRAGMA table_info(identity_profiles)").fetchall()
            }
            version = connection.execute(
                "SELECT version_num FROM alembic_version",
            ).fetchone()[0]
        finally:
            connection.close()

        self.assertIn("next_send_after", columns)
        self.assertEqual(version, HEAD_REVISION)

    def test_recent_partial_migrations_can_resume_to_head(self) -> None:
        legacy_db_path = Path(self.temp_dir.name) / "recent_partial_migration.db"
        env = os.environ.copy()
        env["DATABASE_URL"] = f"sqlite+aiosqlite:///{legacy_db_path.as_posix()}"

        self._run_alembic(env, "upgrade", "20260702_identity_next_send_after")
        connection = sqlite3.connect(legacy_db_path)
        try:
            connection.executescript(
                """
                ALTER TABLE imap_mailbox_sync_states
                    ADD COLUMN history_scan_status VARCHAR(32) DEFAULT 'pending' NOT NULL;
                ALTER TABLE imap_mailbox_sync_states
                    ADD COLUMN history_high_water_uid INTEGER;
                ALTER TABLE imap_mailbox_sync_states
                    ADD COLUMN history_next_before_uid INTEGER;
                ALTER TABLE imap_mailbox_sync_states
                    ADD COLUMN history_scan_started_at DATETIME;
                ALTER TABLE imap_mailbox_sync_states
                    ADD COLUMN history_scan_completed_at DATETIME;
                ALTER TABLE imap_mailbox_sync_states
                    ADD COLUMN history_last_error TEXT;
                ALTER TABLE imap_mailbox_sync_states
                    ADD COLUMN history_scanned_count INTEGER DEFAULT 0 NOT NULL;
                ALTER TABLE imap_mailbox_sync_states
                    ADD COLUMN history_matched_count INTEGER DEFAULT 0 NOT NULL;
                ALTER TABLE imap_mailbox_sync_states
                    ADD COLUMN history_strategy_version VARCHAR(32) DEFAULT 'folder-v1' NOT NULL;
                CREATE INDEX ix_imap_mailbox_sync_identity_history_status_updated
                    ON imap_mailbox_sync_states (
                        identity_id,
                        history_scan_status,
                        updated_at,
                        id
                    );
                ALTER TABLE app_settings
                    ADD COLUMN intended_research_direction TEXT DEFAULT '' NOT NULL;
                ALTER TABLE imap_professor_sync_states
                    ADD COLUMN history_strategy_version VARCHAR(32) DEFAULT 'legacy' NOT NULL;
                CREATE INDEX ix_professors_archived_created_id
                    ON professors (archived_at, created_at, id);
                CREATE INDEX ix_email_tasks_identity_professor_created_id
                    ON email_tasks (identity_id, professor_id, created_at, id);
                CREATE INDEX ix_email_logs_status_identity_professor_direction_created
                    ON email_logs (identity_id, professor_id, direction, created_at, id);
                """
            )
            connection.commit()
        finally:
            connection.close()

        self._run_alembic(env, "upgrade", "head")

        connection = sqlite3.connect(legacy_db_path)
        try:
            version = connection.execute(
                "SELECT version_num FROM alembic_version",
            ).fetchone()[0]
            mailbox_columns = {
                row[1]
                for row in connection.execute("PRAGMA table_info(imap_mailbox_sync_states)").fetchall()
            }
            professor_state_columns = {
                row[1]
                for row in connection.execute("PRAGMA table_info(imap_professor_sync_states)").fetchall()
            }
            app_setting_columns = {
                row[1]
                for row in connection.execute("PRAGMA table_info(app_settings)").fetchall()
            }
        finally:
            connection.close()

        self.assertEqual(version, HEAD_REVISION)
        self.assertIn("history_scan_status", mailbox_columns)
        self.assertIn("history_strategy_version", professor_state_columns)
        self.assertIn("intended_research_direction", app_setting_columns)

    def test_existing_crawl_jobs_are_backfilled_as_v1_when_runtime_v2_is_added(self) -> None:
        legacy_db_path = Path(self.temp_dir.name) / "runtime_v2_legacy_jobs.db"
        env = os.environ.copy()
        env["DATABASE_URL"] = f"sqlite+aiosqlite:///{legacy_db_path.as_posix()}"

        self._run_alembic(env, "upgrade", "a9c3e7d1f4b2")
        connection = sqlite3.connect(legacy_db_path)
        try:
            connection.execute(
                """
                INSERT INTO crawl_jobs (
                    university,
                    school,
                    start_url,
                    status,
                    progress_current,
                    progress_total
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                ("历史大学", "计算机学院", "https://example.edu/faculty", "needs_review", 1, 1),
            )
            connection.commit()
        finally:
            connection.close()

        self._run_alembic(env, "upgrade", "head")

        connection = sqlite3.connect(legacy_db_path)
        try:
            runtime_version = connection.execute(
                "SELECT runtime_version FROM crawl_jobs WHERE university = ?",
                ("历史大学",),
            ).fetchone()[0]
        finally:
            connection.close()

        self.assertEqual(runtime_version, "v1")

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

    def test_concurrency_guard_migration_merges_duplicate_roots_with_existing_child(self) -> None:
        legacy_db_path = Path(self.temp_dir.name) / "concurrency_guard_existing_child.db"
        env = os.environ.copy()
        env["DATABASE_URL"] = f"sqlite+aiosqlite:///{legacy_db_path.as_posix()}"

        self._run_alembic(env, "upgrade", "c6d7e8f9a012")
        connection = sqlite3.connect(legacy_db_path)
        connection.execute("PRAGMA foreign_keys = ON")
        try:
            identity_id = self._insert_identity_into(connection, email_address="guard-scope@example.com")
            llm_profile_id = self._insert_llm_profile_into(connection, name="并发保护模型")
            professor_id = self._insert_professor_into(connection, "guard-scope@example.edu")
            duplicate_root_id = self._insert_workspace_root_task_into(
                connection,
                identity_id,
                llm_profile_id,
                professor_id,
            )
            keep_root_id = self._insert_workspace_root_task_into(
                connection,
                identity_id,
                llm_profile_id,
                professor_id,
            )
            existing_child_id = self._insert_manual_child_task_into(
                connection,
                parent_task_id=duplicate_root_id,
                identity_id=identity_id,
                llm_profile_id=llm_profile_id,
                professor_id=professor_id,
            )
            connection.commit()
        finally:
            connection.close()

        self._run_alembic(env, "upgrade", "d0f1a2b3c4d5")

        connection = sqlite3.connect(legacy_db_path)
        try:
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
            duplicate_parent_id = connection.execute(
                "SELECT parent_task_id FROM email_tasks WHERE id = ?",
                (duplicate_root_id,),
            ).fetchone()[0]
            existing_child_parent_id = connection.execute(
                "SELECT parent_task_id FROM email_tasks WHERE id = ?",
                (existing_child_id,),
            ).fetchone()[0]
        finally:
            connection.close()

        self.assertEqual(root_count, 1)
        self.assertEqual(duplicate_parent_id, keep_root_id)
        self.assertEqual(existing_child_parent_id, duplicate_root_id)

    def test_contact_state_migration_tolerates_existing_backup_table(self) -> None:
        legacy_db_path = Path(self.temp_dir.name) / "contact_state_existing_backup.db"
        env = os.environ.copy()
        env["DATABASE_URL"] = f"sqlite+aiosqlite:///{legacy_db_path.as_posix()}"

        self._run_alembic(env, "upgrade", "2f6a9d8c1e20")
        connection = sqlite3.connect(legacy_db_path)
        try:
            identity_id = self._insert_identity_into(connection, email_address="contact-state@example.com")
            llm_profile_id = self._insert_llm_profile_into(connection, name="状态迁移模型")
            professor_id = self._insert_professor_into(connection, "contact-state@example.edu")
            skipped_task_id = connection.execute(
                """
                INSERT INTO email_tasks (
                    identity_id,
                    llm_profile_id,
                    professor_id,
                    status
                )
                VALUES (?, ?, ?, 'skipped')
                """,
                (identity_id, llm_profile_id, professor_id),
            ).lastrowid
            connection.execute(
                """
                CREATE TABLE email_task_state_redesign_backup (
                    email_task_id INTEGER PRIMARY KEY NOT NULL,
                    previous_status VARCHAR(32) NOT NULL
                )
                """
            )
            connection.execute(
                "INSERT INTO email_task_state_redesign_backup (email_task_id, previous_status) VALUES (?, ?)",
                (skipped_task_id, "skipped"),
            )
            connection.commit()
        finally:
            connection.close()

        self._run_alembic(env, "upgrade", "4c1a2b3d4e5f")

        connection = sqlite3.connect(legacy_db_path)
        try:
            status, source = connection.execute(
                "SELECT status, source FROM email_tasks WHERE id = ?",
                (skipped_task_id,),
            ).fetchone()
            backup_count = connection.execute(
                "SELECT COUNT(*) FROM email_task_state_redesign_backup WHERE email_task_id = ?",
                (skipped_task_id,),
            ).fetchone()[0]
        finally:
            connection.close()

        self.assertEqual(status, "matched")
        self.assertEqual(source, "manual")
        self.assertEqual(backup_count, 1)

    def test_crawl_job_runs_migration_tolerates_existing_backfill_run(self) -> None:
        legacy_db_path = Path(self.temp_dir.name) / "crawl_runs_existing_backfill.db"
        env = os.environ.copy()
        env["DATABASE_URL"] = f"sqlite+aiosqlite:///{legacy_db_path.as_posix()}"

        self._run_alembic(env, "upgrade", "6d7e8f9a0b12")
        connection = sqlite3.connect(legacy_db_path)
        try:
            job_id = connection.execute(
                """
                INSERT INTO crawl_jobs (
                    university,
                    school,
                    start_url,
                    status,
                    progress_current,
                    progress_total,
                    agent_trace
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                ("半迁移大学", "计算机学院", "https://example.edu", "needs_review", 1, 1, json.dumps([])),
            ).lastrowid
            connection.execute(
                """
                CREATE TABLE crawl_job_runs (
                    id INTEGER NOT NULL,
                    job_id INTEGER NOT NULL,
                    attempt_number INTEGER NOT NULL,
                    status VARCHAR(64) NOT NULL,
                    started_at DATETIME,
                    active_started_at DATETIME,
                    paused_at DATETIME,
                    finished_at DATETIME,
                    active_seconds INTEGER DEFAULT 0 NOT NULL,
                    input_tokens INTEGER DEFAULT 0 NOT NULL,
                    output_tokens INTEGER DEFAULT 0 NOT NULL,
                    total_tokens INTEGER DEFAULT 0 NOT NULL,
                    error_message TEXT,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL,
                    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL,
                    CONSTRAINT pk_crawl_job_runs PRIMARY KEY (id),
                    CONSTRAINT uq_crawl_job_runs_job_attempt UNIQUE (job_id, attempt_number),
                    CONSTRAINT fk_crawl_job_runs_job_id_crawl_jobs FOREIGN KEY(job_id) REFERENCES crawl_jobs (id) ON DELETE CASCADE
                )
                """
            )
            run_id = connection.execute(
                """
                INSERT INTO crawl_job_runs (
                    job_id,
                    attempt_number,
                    status,
                    finished_at
                ) VALUES (?, 1, 'needs_review', CURRENT_TIMESTAMP)
                """,
                (job_id,),
            ).lastrowid
            connection.execute("CREATE INDEX ix_crawl_job_runs_job_id ON crawl_job_runs (job_id)")
            connection.execute("CREATE INDEX ix_crawl_job_runs_status ON crawl_job_runs (status)")
            connection.execute("ALTER TABLE crawl_jobs ADD COLUMN current_run_id INTEGER")
            connection.execute("UPDATE crawl_jobs SET current_run_id = ? WHERE id = ?", (run_id, job_id))
            connection.commit()
        finally:
            connection.close()

        self._run_alembic(env, "upgrade", "f2a7c9d8e1b3")

        connection = sqlite3.connect(legacy_db_path)
        try:
            run_count = connection.execute(
                "SELECT COUNT(*) FROM crawl_job_runs WHERE job_id = ? AND attempt_number = 1",
                (job_id,),
            ).fetchone()[0]
            current_run_id = connection.execute(
                "SELECT current_run_id FROM crawl_jobs WHERE id = ?",
                (job_id,),
            ).fetchone()[0]
        finally:
            connection.close()

        self.assertEqual(run_count, 1)
        self.assertEqual(current_run_id, run_id)

    def test_identity_scope_migration_merges_duplicate_roots_with_existing_child(self) -> None:
        legacy_db_path = Path(self.temp_dir.name) / "identity_scope_existing_child.db"
        env = os.environ.copy()
        env["DATABASE_URL"] = f"sqlite+aiosqlite:///{legacy_db_path.as_posix()}"

        self._run_alembic(env, "upgrade", "b2e7c9f1a4d6")
        connection = sqlite3.connect(legacy_db_path)
        connection.execute("PRAGMA foreign_keys = ON")
        try:
            identity_id = self._insert_identity_into(connection, email_address="identity-scope@example.com")
            first_llm_profile_id = self._insert_llm_profile_into(connection, name="身份范围模型一")
            second_llm_profile_id = self._insert_llm_profile_into(connection, name="身份范围模型二")
            professor_id = self._insert_professor_into(connection, "identity-scope@example.edu")
            duplicate_root_id = self._insert_workspace_root_task_into(
                connection,
                identity_id,
                first_llm_profile_id,
                professor_id,
            )
            keep_root_id = self._insert_workspace_root_task_into(
                connection,
                identity_id,
                second_llm_profile_id,
                professor_id,
            )
            existing_child_id = self._insert_manual_child_task_into(
                connection,
                parent_task_id=keep_root_id,
                identity_id=identity_id,
                llm_profile_id=second_llm_profile_id,
                professor_id=professor_id,
            )
            connection.commit()
        finally:
            connection.close()

        self._run_alembic(env, "upgrade", "d6e4b8c2a1f0")

        connection = sqlite3.connect(legacy_db_path)
        try:
            root_count = connection.execute(
                """
                SELECT COUNT(*)
                FROM email_tasks
                WHERE source = 'manual'
                  AND batch_task_id IS NULL
                  AND parent_task_id IS NULL
                  AND professor_id = ?
                  AND identity_id = ?
                """,
                (professor_id, identity_id),
            ).fetchone()[0]
            duplicate_parent_id = connection.execute(
                "SELECT parent_task_id FROM email_tasks WHERE id = ?",
                (duplicate_root_id,),
            ).fetchone()[0]
            existing_child_parent_id = connection.execute(
                "SELECT parent_task_id FROM email_tasks WHERE id = ?",
                (existing_child_id,),
            ).fetchone()[0]
        finally:
            connection.close()

        self.assertEqual(root_count, 1)
        self.assertEqual(existing_child_parent_id, keep_root_id)
        self.assertEqual(duplicate_parent_id, existing_child_id)

    def test_identity_scope_migration_tolerates_existing_app_metadata_table(self) -> None:
        legacy_db_path = Path(self.temp_dir.name) / "identity_scope_existing_metadata.db"
        env = os.environ.copy()
        env["DATABASE_URL"] = f"sqlite+aiosqlite:///{legacy_db_path.as_posix()}"

        self._run_alembic(env, "upgrade", "b2e7c9f1a4d6")
        connection = sqlite3.connect(legacy_db_path)
        connection.execute("PRAGMA foreign_keys = ON")
        try:
            identity_id = self._insert_identity_into(connection, email_address="retry-scope@example.com")
            first_llm_profile_id = self._insert_llm_profile_into(connection, name="重试范围模型一")
            second_llm_profile_id = self._insert_llm_profile_into(connection, name="重试范围模型二")
            professor_id = self._insert_professor_into(connection, "retry-scope@example.edu")
            duplicate_root_id = self._insert_workspace_root_task_into(
                connection,
                identity_id,
                first_llm_profile_id,
                professor_id,
            )
            keep_root_id = self._insert_workspace_root_task_into(
                connection,
                identity_id,
                second_llm_profile_id,
                professor_id,
            )
            existing_child_id = self._insert_manual_child_task_into(
                connection,
                parent_task_id=keep_root_id,
                identity_id=identity_id,
                llm_profile_id=second_llm_profile_id,
                professor_id=professor_id,
            )
            connection.execute("CREATE TABLE app_metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
            connection.commit()
        finally:
            connection.close()

        self._run_alembic(env, "upgrade", "d6e4b8c2a1f0")

        connection = sqlite3.connect(legacy_db_path)
        try:
            version = connection.execute("SELECT version_num FROM alembic_version").fetchone()[0]
            metadata_table_count = connection.execute(
                "SELECT COUNT(*) FROM sqlite_master WHERE type = 'table' AND name = 'app_metadata'",
            ).fetchone()[0]
            duplicate_parent_id = connection.execute(
                "SELECT parent_task_id FROM email_tasks WHERE id = ?",
                (duplicate_root_id,),
            ).fetchone()[0]
            existing_child_parent_id = connection.execute(
                "SELECT parent_task_id FROM email_tasks WHERE id = ?",
                (existing_child_id,),
            ).fetchone()[0]
        finally:
            connection.close()

        self.assertEqual(version, "d6e4b8c2a1f0")
        self.assertEqual(metadata_table_count, 1)
        self.assertEqual(existing_child_parent_id, keep_root_id)
        self.assertEqual(duplicate_parent_id, existing_child_id)

    def test_identity_scope_migration_tolerates_missing_legacy_workspace_index(self) -> None:
        legacy_db_path = Path(self.temp_dir.name) / "identity_scope_missing_legacy_index.db"
        env = os.environ.copy()
        env["DATABASE_URL"] = f"sqlite+aiosqlite:///{legacy_db_path.as_posix()}"

        self._run_alembic(env, "upgrade", "b2e7c9f1a4d6")
        connection = sqlite3.connect(legacy_db_path)
        try:
            connection.execute("CREATE TABLE app_metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
            connection.execute("DROP INDEX uq_email_tasks_workspace_task")
            connection.commit()
        finally:
            connection.close()

        self._run_alembic(env, "upgrade", "d6e4b8c2a1f0")

        connection = sqlite3.connect(legacy_db_path)
        try:
            indexes = {
                row[1]
                for row in connection.execute("PRAGMA index_list('email_tasks')").fetchall()
            }
            version = connection.execute("SELECT version_num FROM alembic_version").fetchone()[0]
        finally:
            connection.close()

        self.assertEqual(version, "d6e4b8c2a1f0")
        self.assertIn("uq_email_tasks_workspace_task", indexes)

    def test_professor_tags_migration_tolerates_existing_tables_and_default_tag(self) -> None:
        legacy_db_path = Path(self.temp_dir.name) / "professor_tags_existing_tables.db"
        env = os.environ.copy()
        env["DATABASE_URL"] = f"sqlite+aiosqlite:///{legacy_db_path.as_posix()}"

        self._run_alembic(env, "upgrade", "d6e4b8c2a1f0")
        connection = sqlite3.connect(legacy_db_path)
        try:
            connection.execute(
                """
                CREATE TABLE professor_tags (
                    id INTEGER NOT NULL,
                    name VARCHAR(64) NOT NULL,
                    text_color VARCHAR(16) NOT NULL,
                    background_color VARCHAR(16) NOT NULL,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL,
                    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL,
                    CONSTRAINT pk_professor_tags PRIMARY KEY (id),
                    CONSTRAINT uq_professor_tags_name UNIQUE (name)
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE professor_tag_links (
                    professor_id INTEGER NOT NULL,
                    tag_id INTEGER NOT NULL,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL,
                    CONSTRAINT pk_professor_tag_links PRIMARY KEY (professor_id, tag_id),
                    CONSTRAINT uq_professor_tag_links_professor_tag UNIQUE (professor_id, tag_id),
                    CONSTRAINT fk_professor_tag_links_professor_id_professors FOREIGN KEY(professor_id) REFERENCES professors (id) ON DELETE CASCADE,
                    CONSTRAINT fk_professor_tag_links_tag_id_professor_tags FOREIGN KEY(tag_id) REFERENCES professor_tags (id) ON DELETE CASCADE
                )
                """
            )
            connection.execute("CREATE INDEX ix_professor_tag_links_tag_id ON professor_tag_links (tag_id)")
            connection.execute(
                """
                INSERT INTO professor_tags (name, text_color, background_color)
                VALUES ('已退休', '#92400e', '#fef3c7')
                """
            )
            connection.commit()
        finally:
            connection.close()

        self._run_alembic(env, "upgrade", "20260606tags")

        connection = sqlite3.connect(legacy_db_path)
        try:
            tag_names = {
                row[0]
                for row in connection.execute("SELECT name FROM professor_tags").fetchall()
            }
            link_table_count = connection.execute(
                "SELECT COUNT(*) FROM sqlite_master WHERE type = 'table' AND name = 'professor_tag_links'",
            ).fetchone()[0]
        finally:
            connection.close()

        self.assertEqual(
            tag_names,
            {"已退休", "高意愿", "低意愿", "羊导", "高强度"},
        )
        self.assertEqual(link_table_count, 1)

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

    def _get_table_names(self) -> set[str]:
        rows = self.connection.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        return {row[0] for row in rows}


    def _get_columns(self, table_name: str) -> set[str]:
        rows = self.connection.execute(f"PRAGMA table_info('{table_name}')").fetchall()
        return {row[1] for row in rows}

    def _get_index_columns(self, table_name: str, index_name: str) -> list[str]:
        index_names = {
            row[1]
            for row in self.connection.execute(f"PRAGMA index_list('{table_name}')").fetchall()
        }
        self.assertIn(index_name, index_names)
        rows = self.connection.execute(f"PRAGMA index_info('{index_name}')").fetchall()
        return [row[2] for row in rows]

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
    def _insert_identity_material_into(
        connection: sqlite3.Connection,
        identity_id: int,
        *,
        display_name: str = "简历",
        original_filename: str = "resume.txt",
        extracted_text: str = "resume",
        material_type: str = "resume",
    ) -> int:
        cursor = connection.execute(
            """
            INSERT INTO identity_materials (
                identity_id,
                display_name,
                original_filename,
                file_path,
                material_type,
                sha256,
                extracted_text
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                identity_id,
                display_name,
                original_filename,
                f"data/materials/{original_filename}",
                material_type,
                "a" * 64,
                extracted_text,
            ),
        )
        return int(cursor.lastrowid)

    @staticmethod
    def _insert_email_task_with_material_into(
        connection: sqlite3.Connection,
        identity_id: int,
        llm_profile_id: int,
        professor_id: int,
        *,
        primary_material_id: int | None,
        updated_at: str = "2026-06-01 08:00:00",
    ) -> int:
        cursor = connection.execute(
            """
            INSERT INTO email_tasks (
                source,
                identity_id,
                llm_profile_id,
                professor_id,
                primary_material_id,
                status,
                selected_material_ids,
                created_at,
                updated_at
            )
            VALUES ('manual', ?, ?, ?, ?, 'matched', ?, ?, ?)
            """,
            (
                identity_id,
                llm_profile_id,
                professor_id,
                primary_material_id,
                json.dumps([]),
                updated_at,
                updated_at,
            ),
        )
        return int(cursor.lastrowid)

    @staticmethod
    def _insert_manual_child_task_into(
        connection: sqlite3.Connection,
        *,
        parent_task_id: int,
        identity_id: int,
        llm_profile_id: int,
        professor_id: int,
    ) -> int:
        cursor = connection.execute(
            """
            INSERT INTO email_tasks (
                source,
                parent_task_id,
                identity_id,
                llm_profile_id,
                professor_id,
                status,
                selected_material_ids
            )
            VALUES ('manual', ?, ?, ?, ?, 'sent', ?)
            """,
            (parent_task_id, identity_id, llm_profile_id, professor_id, json.dumps([])),
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
