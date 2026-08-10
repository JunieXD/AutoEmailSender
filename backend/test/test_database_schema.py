from __future__ import annotations

import ast
import json
import logging
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from alembic import command
from alembic.script import ScriptDirectory
from app.core.config import get_settings
from app.modules.campaigns.templates.rendering import import_outreach_template_file
from app.core.migrations import get_alembic_config, get_head_revision
from test.migrated_database import create_migrated_sqlite_database


BACKEND_DIR = Path(__file__).resolve().parents[1]
HEAD_REVISION = get_head_revision(get_alembic_config())
LEGACY_RUNTIME_REVISION = "7a1d5e42c9bd"
PERFORMANCE_INDEXES = {
    "email_tasks": {
        "ix_email_tasks_dispatch_ready",
        "ix_email_tasks_unstarted_generation_recovery",
        "ix_email_tasks_started_generation_recovery",
        "ix_email_tasks_batch_sent_at",
    },
    "match_analysis_jobs": {
        "ix_match_analysis_jobs_status_deleted_created_id",
    },
    "crawl_jobs": {
        "ix_crawl_jobs_kind_deleted_created_id",
    },
}


def run_alembic_in_process(env: dict[str, str], *args: str) -> None:
    if len(args) != 2 or args[0] not in {"upgrade", "downgrade"}:
        raise ValueError(f"Unsupported Alembic command: {' '.join(args)}")

    operation = command.upgrade if args[0] == "upgrade" else command.downgrade
    database_url = env.get("DATABASE_URL", "")
    sqlite_prefix = "sqlite+aiosqlite:///"
    if args[0] == "upgrade" and database_url.startswith(sqlite_prefix):
        database_path = Path(database_url.removeprefix(sqlite_prefix))
        if database_path.as_posix() != ":memory:" and not database_path.exists():
            create_migrated_sqlite_database(database_path, revision=args[1])
            return

    with patch.dict(os.environ, env, clear=True):
        get_settings.cache_clear()
        previous_logging_threshold = logging.root.manager.disable
        logging.disable(logging.CRITICAL)
        try:
            config = get_alembic_config()
            # Parse the ini before hiding its path so Alembic does not reconfigure
            # process-wide logging on every in-process migration command.
            config.get_main_option("script_location")
            config.config_file_name = None
            operation(config, args[1])
        finally:
            logging.disable(previous_logging_threshold)
            get_settings.cache_clear()


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

    def test_alembic_graph_has_exactly_one_head(self) -> None:
        config = get_alembic_config()
        heads = ScriptDirectory.from_config(config).get_heads()

        self.assertEqual(
            1,
            len(heads),
            f"Alembic migration graph must have exactly one head, found: {heads}",
        )
        self.assertEqual(heads[0], get_head_revision(config))

    def test_professor_scale_search_migration_round_trip_preserves_data(self) -> None:
        database_path = Path(self.temp_dir.name) / "professor_scale_search.db"
        env = os.environ.copy()
        env["DATABASE_URL"] = f"sqlite+aiosqlite:///{database_path.as_posix()}"
        previous_revision = "20260808_crawl_llm_snapshot"
        migration_revision = "20260809_professor_scale_search"

        self._run_alembic(env, "upgrade", previous_revision)
        with sqlite3.connect(database_path) as connection:
            connection.execute(
                """
                INSERT INTO professors(name, email, research_direction)
                VALUES ('迁移导师', 'migration-scale@example.edu', '数据库优化')
                """,
            )
            connection.commit()

        self._run_alembic(env, "upgrade", migration_revision)
        with sqlite3.connect(database_path) as connection:
            indexes = {
                row[1]
                for row in connection.execute("PRAGMA index_list('professors')")
            }
            fts_names = connection.execute(
                """
                SELECT name FROM professors_fts
                WHERE professors_fts MATCH '数据库优化'
                """,
            ).fetchall()
            version = connection.execute(
                "SELECT version_num FROM alembic_version",
            ).fetchone()[0]
        self.assertEqual(version, migration_revision)
        self.assertIn("ix_professors_archived_updated_id", indexes)
        self.assertEqual(fts_names, [("迁移导师",)])

        self._run_alembic(env, "downgrade", previous_revision)
        with sqlite3.connect(database_path) as connection:
            remaining_indexes = {
                row[1]
                for row in connection.execute("PRAGMA index_list('professors')")
            }
            fts_table = connection.execute(
                """
                SELECT name FROM sqlite_master
                WHERE type = 'table' AND name = 'professors_fts'
                """,
            ).fetchone()
            professor = connection.execute(
                "SELECT name FROM professors WHERE email = 'migration-scale@example.edu'",
            ).fetchone()
        self.assertNotIn("ix_professors_archived_updated_id", remaining_indexes)
        self.assertIsNone(fts_table)
        self.assertEqual(professor, ("迁移导师",))

        self._run_alembic(env, "upgrade", migration_revision)
        with sqlite3.connect(database_path) as connection:
            rebuilt_fts = connection.execute(
                """
                SELECT name FROM professors_fts
                WHERE professors_fts MATCH '数据库优化'
                """,
            ).fetchall()
        self.assertEqual(rebuilt_fts, [("迁移导师",)])

    def test_agent_ui_handoff_migration_upgrades_and_downgrades_cleanly(self) -> None:
        database_path = Path(self.temp_dir.name) / "agent_ui_handoffs_migration.db"
        env = os.environ.copy()
        env["DATABASE_URL"] = f"sqlite+aiosqlite:///{database_path.as_posix()}"
        previous_revision = "20260809_professor_scale_search"
        migration_revision = "20260810_agent_ui_handoffs"

        self._run_alembic(env, "upgrade", previous_revision)
        with sqlite3.connect(database_path) as connection:
            connection.execute(
                """
                INSERT INTO professors(name, email, research_direction)
                VALUES ('交接迁移导师', 'handoff-migration@example.edu', 'Agent UI')
                """,
            )
            connection.commit()

        self._run_alembic(env, "upgrade", migration_revision)
        with sqlite3.connect(database_path) as connection:
            tables = {
                row[0]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'",
                )
            }
            handoff_indexes = {
                row[1]
                for row in connection.execute(
                    "PRAGMA index_list('agent_ui_handoffs')",
                )
            }
            item_indexes = {
                row[1]
                for row in connection.execute(
                    "PRAGMA index_list('agent_ui_handoff_items')",
                )
            }
            version = connection.execute(
                "SELECT version_num FROM alembic_version",
            ).fetchone()[0]
        self.assertEqual(version, migration_revision)
        self.assertIn("agent_ui_handoffs", tables)
        self.assertIn("agent_ui_handoff_items", tables)
        self.assertIn("ix_agent_ui_handoffs_status_expires_at", handoff_indexes)
        self.assertIn("ix_agent_ui_handoffs_consumer_claim", handoff_indexes)
        self.assertIn("ix_agent_ui_handoff_items_resource", item_indexes)

        self._run_alembic(env, "downgrade", previous_revision)
        with sqlite3.connect(database_path) as connection:
            remaining_tables = {
                row[0]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'",
                )
            }
            professor = connection.execute(
                "SELECT name FROM professors WHERE email = 'handoff-migration@example.edu'",
            ).fetchone()
            version = connection.execute(
                "SELECT version_num FROM alembic_version",
            ).fetchone()[0]
        self.assertEqual(version, previous_revision)
        self.assertNotIn("agent_ui_handoff_items", remaining_tables)
        self.assertNotIn("agent_ui_handoffs", remaining_tables)
        self.assertEqual(professor, ("交接迁移导师",))

        self._run_alembic(env, "upgrade", migration_revision)
        with sqlite3.connect(database_path) as connection:
            rebuilt_tables = {
                row[0]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'",
                )
            }
        self.assertIn("agent_ui_handoffs", rebuilt_tables)
        self.assertIn("agent_ui_handoff_items", rebuilt_tables)

    def test_match_analysis_task_decoupling_migration_preserves_legacy_runs(self) -> None:
        database_path = Path(self.temp_dir.name) / "match_task_decoupling.db"
        env = os.environ.copy()
        env["DATABASE_URL"] = f"sqlite+aiosqlite:///{database_path.as_posix()}"
        previous_revision = "20260807_email_delivery_management"
        migration_revision = "20260807_match_task_decoupling"

        self._run_alembic(env, "upgrade", previous_revision)
        connection = sqlite3.connect(database_path)
        connection.execute("PRAGMA foreign_keys = ON")
        identity_id = DatabaseSchemaTests._insert_identity_into(
            connection,
            email_address="match-decoupling@example.com",
        )
        llm_profile_id = DatabaseSchemaTests._insert_llm_profile_into(
            connection,
            name="匹配解耦迁移模型",
        )
        professor_id = DatabaseSchemaTests._insert_professor_into(
            connection,
            "match-decoupling-professor@example.edu",
        )
        task_id = DatabaseSchemaTests._insert_workspace_root_task_into(
            connection,
            identity_id=identity_id,
            llm_profile_id=llm_profile_id,
            professor_id=professor_id,
        )
        legacy_run_id = int(
            connection.execute(
                """
                INSERT INTO match_analysis_runs (
                    email_task_id, professor_id, identity_id, llm_profile_id,
                    status, success, match_score
                )
                VALUES (?, ?, ?, ?, 'succeeded', 1, 81)
                """,
                (task_id, professor_id, identity_id, llm_profile_id),
            ).lastrowid
        )
        connection.commit()
        connection.close()

        self._run_alembic(env, "upgrade", migration_revision)
        upgraded = sqlite3.connect(database_path)
        upgraded.execute("PRAGMA foreign_keys = ON")
        column_rows = {
            row[1]: row
            for row in upgraded.execute(
                "PRAGMA table_info('match_analysis_runs')"
            ).fetchall()
        }
        legacy_email_task_id = upgraded.execute(
            "SELECT email_task_id FROM match_analysis_runs WHERE id = ?",
            (legacy_run_id,),
        ).fetchone()[0]
        detached_run_id = int(
            upgraded.execute(
                """
                INSERT INTO match_analysis_runs (
                    email_task_id, professor_id, identity_id, llm_profile_id,
                    status, success, match_score
                )
                VALUES (NULL, ?, ?, ?, 'succeeded', 1, 92)
                """,
                (professor_id, identity_id, llm_profile_id),
            ).lastrowid
        )
        upgraded.commit()

        self.assertEqual(column_rows["email_task_id"][3], 0)
        self.assertEqual(legacy_email_task_id, task_id)
        self.assertIsNone(
            upgraded.execute(
                "SELECT email_task_id FROM match_analysis_runs WHERE id = ?",
                (detached_run_id,),
            ).fetchone()[0]
        )

        upgraded.execute(
            "DELETE FROM match_analysis_runs WHERE id = ?",
            (detached_run_id,),
        )
        upgraded.commit()
        upgraded.close()

        self._run_alembic(env, "downgrade", previous_revision)
        downgraded = sqlite3.connect(database_path)
        downgraded_columns = {
            row[1]: row
            for row in downgraded.execute(
                "PRAGMA table_info('match_analysis_runs')"
            ).fetchall()
        }
        preserved_email_task_id = downgraded.execute(
            "SELECT email_task_id FROM match_analysis_runs WHERE id = ?",
            (legacy_run_id,),
        ).fetchone()[0]
        downgraded.close()

        self.assertEqual(downgraded_columns["email_task_id"][3], 1)
        self.assertEqual(preserved_email_task_id, task_id)

    def test_background_scheduler_lease_migration_round_trip(self) -> None:
        database_path = Path(self.temp_dir.name) / "scheduler_leases.db"
        env = os.environ.copy()
        env["DATABASE_URL"] = f"sqlite+aiosqlite:///{database_path.as_posix()}"
        previous_revision = "20260807_batch_draft_fair"
        lease_revision = "20260807_scheduler_leases"

        self._run_alembic(env, "upgrade", previous_revision)
        self._run_alembic(env, "upgrade", lease_revision)
        upgraded = sqlite3.connect(database_path)
        try:
            tables = {
                row[0]
                for row in upgraded.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                )
            }
            job_columns = {
                row[1]
                for row in upgraded.execute(
                    "PRAGMA table_info(match_analysis_jobs)"
                )
            }
            item_columns = {
                row[1]
                for row in upgraded.execute(
                    "PRAGMA table_info(match_analysis_job_items)"
                )
            }
            mailbox_columns = {
                row[1]
                for row in upgraded.execute(
                    "PRAGMA table_info(imap_mailbox_sync_states)"
                )
            }
            professor_columns = {
                row[1]
                for row in upgraded.execute(
                    "PRAGMA table_info(imap_professor_sync_states)"
                )
            }
            item_indexes = {
                row[1]
                for row in upgraded.execute(
                    "PRAGMA index_list(match_analysis_job_items)"
                )
            }
        finally:
            upgraded.close()

        self.assertIn("imap_identity_sync_leases", tables)
        self.assertIn("item_last_dispatched_at", job_columns)
        self.assertTrue(
            {"claim_id", "claimed_at", "lease_expires_at", "attempt_count"}
            <= item_columns
        )
        self.assertTrue(
            {"history_claim_id", "history_lease_expires_at"} <= mailbox_columns
        )
        self.assertTrue(
            {"history_claim_id", "history_lease_expires_at"} <= professor_columns
        )
        self.assertIn("ix_match_analysis_job_items_lease_recovery", item_indexes)

        self._run_alembic(env, "downgrade", previous_revision)
        downgraded = sqlite3.connect(database_path)
        try:
            tables = {
                row[0]
                for row in downgraded.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                )
            }
            item_columns = {
                row[1]
                for row in downgraded.execute(
                    "PRAGMA table_info(match_analysis_job_items)"
                )
            }
        finally:
            downgraded.close()

        self.assertNotIn("imap_identity_sync_leases", tables)
        self.assertNotIn("claim_id", item_columns)

        self._run_alembic(env, "upgrade", "head")
        restored = sqlite3.connect(database_path)
        try:
            version = restored.execute(
                "SELECT version_num FROM alembic_version"
            ).fetchone()[0]
        finally:
            restored.close()
        self.assertEqual(version, HEAD_REVISION)

    def test_crawl_job_concurrency_default_migrates_to_serial(self) -> None:
        database_path = Path(self.temp_dir.name) / "crawl_job_concurrency.db"
        env = os.environ.copy()
        env["DATABASE_URL"] = f"sqlite+aiosqlite:///{database_path.as_posix()}"
        previous_revision = "20260805_merge_match_fallback"

        self._run_alembic(env, "upgrade", previous_revision)
        connection = sqlite3.connect(database_path)
        try:
            self.assertEqual(
                connection.execute(
                    "SELECT crawler_worker_count FROM app_settings WHERE id = 1"
                ).fetchone()[0],
                2,
            )
            connection.execute(
                "INSERT INTO app_settings (id, crawler_worker_count) VALUES (2, 3)"
            )
            connection.commit()
        finally:
            connection.close()

        self._run_alembic(env, "upgrade", "head")
        upgraded = sqlite3.connect(database_path)
        try:
            values = upgraded.execute(
                "SELECT id, crawler_worker_count FROM app_settings ORDER BY id"
            ).fetchall()
            column = next(
                row
                for row in upgraded.execute("PRAGMA table_info(app_settings)").fetchall()
                if row[1] == "crawler_worker_count"
            )
        finally:
            upgraded.close()

        self.assertEqual(values, [(1, 1), (2, 3)])
        self.assertEqual(column[4], "1")

        self._run_alembic(env, "downgrade", previous_revision)
        downgraded = sqlite3.connect(database_path)
        try:
            values = downgraded.execute(
                "SELECT id, crawler_worker_count FROM app_settings ORDER BY id"
            ).fetchall()
        finally:
            downgraded.close()
        self.assertEqual(values, [(1, 2), (2, 3)])

    def test_crawler_identity_migration_preserves_historical_candidates_and_tasks(self) -> None:
        database_path = Path(self.temp_dir.name) / "crawler_recovery.db"
        env = os.environ.copy()
        env["DATABASE_URL"] = f"sqlite+aiosqlite:///{database_path.as_posix()}"
        previous_revision = "20260806_crawl_job_serial"

        self._run_alembic(env, "upgrade", previous_revision)
        connection = sqlite3.connect(database_path)
        try:
            job_id = connection.execute(
                """
                INSERT INTO crawl_jobs (
                    university, school, start_url, status,
                    progress_current, progress_total, runtime_version
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "示例大学",
                    "计算机学院",
                    "https://example.edu/faculty",
                    "running",
                    0,
                    0,
                    "v2",
                ),
            ).lastrowid
            first_candidate_id = connection.execute(
                """
                INSERT INTO crawl_candidates (
                    job_id, name, email, title, identity_key
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (job_id, "张三", "ZHANG@example.edu", "教授", "legacy-a"),
            ).lastrowid
            second_candidate_id = connection.execute(
                """
                INSERT INTO crawl_candidates (
                    job_id, name, email, department, identity_key
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (job_id, "张三", "zhang@example.edu", "计算机系", "legacy-b"),
            ).lastrowid
            first_invalid_profile_id = connection.execute(
                """
                INSERT INTO crawl_candidates (
                    job_id, name, profile_url, identity_key
                ) VALUES (?, ?, ?, ?)
                """,
                (job_id, "无效主页一", "not-a-url", "legacy-invalid-a"),
            ).lastrowid
            second_invalid_profile_id = connection.execute(
                """
                INSERT INTO crawl_candidates (
                    job_id, name, profile_url, identity_key
                ) VALUES (?, ?, ?, ?)
                """,
                (job_id, "无效主页二", "not-a-url", "legacy-invalid-b"),
            ).lastrowid
            connection.execute(
                """
                INSERT INTO crawl_candidate_enrichment_tasks (
                    job_id, candidate_id, status, attempt_count
                ) VALUES (?, ?, ?, ?), (?, ?, ?, ?)
                """,
                (
                    job_id,
                    first_candidate_id,
                    "succeeded",
                    1,
                    job_id,
                    second_candidate_id,
                    "skipped",
                    1,
                ),
            )
            connection.commit()
        finally:
            connection.close()

        self._run_alembic(env, "upgrade", "head")

        upgraded = sqlite3.connect(database_path)
        try:
            candidates = upgraded.execute(
                """
                SELECT id, email, title, department, merged_into_candidate_id
                FROM crawl_candidates
                WHERE job_id = ?
                ORDER BY id
                """,
                (job_id,),
            ).fetchall()
            candidate_indexes = {
                row[1]
                for row in upgraded.execute(
                    "PRAGMA index_list('crawl_candidates')"
                ).fetchall()
            }
            page_task_columns = {
                row[1]
                for row in upgraded.execute(
                    "PRAGMA table_info('crawl_page_tasks')"
                ).fetchall()
            }
            token_usage_columns = {
                row[1]
                for row in upgraded.execute(
                    "PRAGMA table_info('crawl_worker_token_usages')"
                ).fetchall()
            }
            enrichment_task_count = upgraded.execute(
                "SELECT count(*) FROM crawl_candidate_enrichment_tasks WHERE job_id = ?",
                (job_id,),
            ).fetchone()[0]
            identity_keys = upgraded.execute(
                """
                SELECT candidate_id, key_type, normalized_value
                FROM crawl_candidate_identity_keys
                WHERE job_id = ?
                ORDER BY key_type, normalized_value
                """,
                (job_id,),
            ).fetchall()
        finally:
            upgraded.close()

        self.assertEqual(
            candidates,
            [
                (
                    first_candidate_id,
                    "ZHANG@example.edu",
                    "教授",
                    None,
                    second_candidate_id,
                ),
                (
                    second_candidate_id,
                    "zhang@example.edu",
                    "教授",
                    "计算机系",
                    None,
                ),
                (first_invalid_profile_id, None, None, None, None),
                (second_invalid_profile_id, None, None, None, None),
            ],
        )
        self.assertTrue(
            {
                "uq_crawl_candidates_job_identity_key",
                "uq_crawl_candidates_job_email_ci",
                "uq_crawl_candidates_job_profile_url",
            }.isdisjoint(candidate_indexes)
        )
        self.assertEqual(enrichment_task_count, 2)
        self.assertEqual(
            identity_keys,
            [(second_candidate_id, "email", "zhang@example.edu")],
        )
        self.assertIn("failure_count", page_task_columns)
        self.assertTrue({"run_id", "claim_id"}.issubset(token_usage_columns))

        self._run_alembic(env, "downgrade", previous_revision)
        downgraded = sqlite3.connect(database_path)
        try:
            candidate_count = downgraded.execute(
                "SELECT count(*) FROM crawl_candidates WHERE job_id = ?",
                (job_id,),
            ).fetchone()[0]
            enrichment_task_count = downgraded.execute(
                "SELECT count(*) FROM crawl_candidate_enrichment_tasks WHERE job_id = ?",
                (job_id,),
            ).fetchone()[0]
            candidate_columns = {
                row[1]
                for row in downgraded.execute(
                    "PRAGMA table_info('crawl_candidates')"
                ).fetchall()
            }
            identity_table = downgraded.execute(
                """
                SELECT name FROM sqlite_master
                WHERE type = 'table' AND name = 'crawl_candidate_identity_keys'
                """
            ).fetchone()
        finally:
            downgraded.close()

        self.assertEqual(candidate_count, 4)
        self.assertEqual(enrichment_task_count, 2)
        self.assertNotIn("merged_into_candidate_id", candidate_columns)
        self.assertIsNone(identity_table)

    def test_professor_history_queue_migration_upgrades_and_downgrades(self) -> None:
        database_path = Path(self.temp_dir.name) / "professor_history_queue.db"
        env = os.environ.copy()
        env["DATABASE_URL"] = f"sqlite+aiosqlite:///{database_path.as_posix()}"

        previous_revision = "20260716_llm_endpoint_adaptation"
        self._run_alembic(env, "upgrade", previous_revision)
        connection = sqlite3.connect(database_path)
        try:
            professor_id = DatabaseSchemaTests._insert_professor_into(
                connection,
                "existing-before-queue@example.edu",
            )
            connection.commit()
        finally:
            connection.close()

        self._run_alembic(env, "upgrade", "head")
        upgraded = sqlite3.connect(database_path)
        try:
            professor_columns = {
                row[1] for row in upgraded.execute("PRAGMA table_info(professors)").fetchall()
            }
            mailbox_columns = {
                row[1]
                for row in upgraded.execute(
                    "PRAGMA table_info(imap_mailbox_sync_states)",
                ).fetchall()
            }
            professor_state_columns = {
                row[1]
                for row in upgraded.execute(
                    "PRAGMA table_info(imap_professor_sync_states)",
                ).fetchall()
            }
            professor_state_indexes = {
                row[1]
                for row in upgraded.execute(
                    "PRAGMA index_list(imap_professor_sync_states)",
                ).fetchall()
            }
            sync_version = upgraded.execute(
                "SELECT communication_sync_version FROM professors WHERE id = ?",
                (professor_id,),
            ).fetchone()[0]
        finally:
            upgraded.close()

        self.assertIn("communication_sync_version", professor_columns)
        self.assertIn("history_batch_id", mailbox_columns)
        self.assertTrue(
            {
                "history_start_date",
                "trigger_reason",
                "batch_id",
                "available_at",
                "priority",
                "professor_sync_version",
            }.issubset(professor_state_columns),
        )
        self.assertIn("ix_imap_professor_sync_recent_due", professor_state_indexes)
        self.assertEqual(sync_version, 1)

        self._run_alembic(env, "downgrade", previous_revision)
        downgraded = sqlite3.connect(database_path)
        try:
            downgraded_professor_columns = {
                row[1] for row in downgraded.execute("PRAGMA table_info(professors)").fetchall()
            }
            downgraded_mailbox_columns = {
                row[1]
                for row in downgraded.execute(
                    "PRAGMA table_info(imap_mailbox_sync_states)",
                ).fetchall()
            }
            downgraded_professor_state_columns = {
                row[1]
                for row in downgraded.execute(
                    "PRAGMA table_info(imap_professor_sync_states)",
                ).fetchall()
            }
            downgraded_indexes = {
                row[1]
                for row in downgraded.execute(
                    "PRAGMA index_list(imap_professor_sync_states)",
                ).fetchall()
            }
        finally:
            downgraded.close()

        self.assertNotIn("communication_sync_version", downgraded_professor_columns)
        self.assertNotIn("history_batch_id", downgraded_mailbox_columns)
        self.assertNotIn("history_start_date", downgraded_professor_state_columns)
        self.assertNotIn("ix_imap_professor_sync_recent_due", downgraded_indexes)

        self._run_alembic(env, "upgrade", "head")
        upgraded_again = sqlite3.connect(database_path)
        try:
            version = upgraded_again.execute(
                "SELECT version_num FROM alembic_version",
            ).fetchone()[0]
        finally:
            upgraded_again.close()
        self.assertEqual(version, HEAD_REVISION)

    def test_professor_information_enrichment_migration_upgrades_and_downgrades(self) -> None:
        database_path = Path(self.temp_dir.name) / "professor_information_enrichment.db"
        env = os.environ.copy()
        env["DATABASE_URL"] = f"sqlite+aiosqlite:///{database_path.as_posix()}"
        previous_revision = "20260719_professor_history_queue"

        self._run_alembic(env, "upgrade", previous_revision)
        self._run_alembic(env, "upgrade", "head")
        upgraded = sqlite3.connect(database_path)
        try:
            crawl_job_columns = {
                row[1] for row in upgraded.execute("PRAGMA table_info(crawl_jobs)").fetchall()
            }
            enrichment_task_columns = {
                row[1]
                for row in upgraded.execute(
                    "PRAGMA table_info(crawl_candidate_enrichment_tasks)",
                ).fetchall()
            }
            enrichment_task_indexes = {
                row[1]
                for row in upgraded.execute(
                    "PRAGMA index_list(crawl_candidate_enrichment_tasks)",
                ).fetchall()
            }
        finally:
            upgraded.close()

        self.assertTrue(
            {"job_kind", "trigger_mode", "task_center_visible", "display_name"}.issubset(
                crawl_job_columns,
            ),
        )
        self.assertTrue(
            {
                "professor_id",
                "skip_reason",
                "enriched_fields",
                "started_at",
                "finished_at",
            }.issubset(enrichment_task_columns),
        )
        self.assertIn(
            "uq_crawl_candidate_enrichment_tasks_active_professor",
            enrichment_task_indexes,
        )

        self._run_alembic(env, "downgrade", previous_revision)
        downgraded = sqlite3.connect(database_path)
        try:
            downgraded_job_columns = {
                row[1] for row in downgraded.execute("PRAGMA table_info(crawl_jobs)").fetchall()
            }
            downgraded_task_columns = {
                row[1]
                for row in downgraded.execute(
                    "PRAGMA table_info(crawl_candidate_enrichment_tasks)",
                ).fetchall()
            }
        finally:
            downgraded.close()
        self.assertNotIn("job_kind", downgraded_job_columns)
        self.assertNotIn("professor_id", downgraded_task_columns)

        self._run_alembic(env, "upgrade", "head")

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

    def test_identity_communication_group_migration_preserves_existing_data(self) -> None:
        database_path = Path(self.temp_dir.name) / "identity_communication_groups.db"
        env = os.environ.copy()
        env["DATABASE_URL"] = f"sqlite+aiosqlite:///{database_path.as_posix()}"
        previous_revision = "20260721_match_analysis_cache"

        self._run_alembic(env, "upgrade", previous_revision)
        connection = sqlite3.connect(database_path)
        try:
            identity_id = DatabaseSchemaTests._insert_identity_into(
                connection,
                email_address="before-sharing@example.com",
            )
            llm_profile_id = DatabaseSchemaTests._insert_llm_profile_into(
                connection,
                name="Before Sharing Model",
            )
            professor_id = DatabaseSchemaTests._insert_professor_into(
                connection,
                "before-sharing@example.edu",
            )
            task_id = DatabaseSchemaTests._insert_workspace_root_task_into(
                connection,
                identity_id,
                llm_profile_id,
                professor_id,
            )
            log_id = int(
                connection.execute(
                    """
                    INSERT INTO email_logs (
                        email_task_id,
                        identity_id,
                        llm_profile_id,
                        professor_id,
                        direction,
                        subject,
                        content,
                        normalized_message_id
                    )
                    VALUES (?, ?, ?, ?, 'sent', '迁移前邮件', '正文', 'before-sharing@example.com')
                    """,
                    (task_id, identity_id, llm_profile_id, professor_id),
                ).lastrowid,
            )
            connection.commit()
        finally:
            connection.close()

        self._run_alembic(env, "upgrade", "head")
        upgraded = sqlite3.connect(database_path)
        try:
            tables = {
                row[0]
                for row in upgraded.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'",
                ).fetchall()
            }
            identity_columns = {
                row[1]
                for row in upgraded.execute(
                    "PRAGMA table_info(identity_profiles)",
                ).fetchall()
            }
            identity_indexes = {
                row[1]
                for row in upgraded.execute(
                    "PRAGMA index_list(identity_profiles)",
                ).fetchall()
            }
            identity_row = upgraded.execute(
                "SELECT communication_group_id FROM identity_profiles WHERE id = ?",
                (identity_id,),
            ).fetchone()
            task_row = upgraded.execute(
                "SELECT identity_id FROM email_tasks WHERE id = ?",
                (task_id,),
            ).fetchone()
            log_row = upgraded.execute(
                "SELECT identity_id FROM email_logs WHERE id = ?",
                (log_id,),
            ).fetchone()
        finally:
            upgraded.close()

        self.assertIn("identity_communication_groups", tables)
        self.assertIn("communication_group_id", identity_columns)
        self.assertIn("ix_identity_profiles_communication_group_id", identity_indexes)
        self.assertEqual(identity_row, (None,))
        self.assertEqual(task_row, (identity_id,))
        self.assertEqual(log_row, (identity_id,))

        self._run_alembic(env, "downgrade", previous_revision)
        downgraded = sqlite3.connect(database_path)
        try:
            downgraded_tables = {
                row[0]
                for row in downgraded.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'",
                ).fetchall()
            }
            downgraded_identity_columns = {
                row[1]
                for row in downgraded.execute(
                    "PRAGMA table_info(identity_profiles)",
                ).fetchall()
            }
            remaining_task_count = downgraded.execute(
                "SELECT COUNT(*) FROM email_tasks WHERE id = ?",
                (task_id,),
            ).fetchone()[0]
            remaining_log_count = downgraded.execute(
                "SELECT COUNT(*) FROM email_logs WHERE id = ?",
                (log_id,),
            ).fetchone()[0]
        finally:
            downgraded.close()

        self.assertNotIn("identity_communication_groups", downgraded_tables)
        self.assertNotIn("communication_group_id", downgraded_identity_columns)
        self.assertEqual(remaining_task_count, 1)
        self.assertEqual(remaining_log_count, 1)

    def test_outreach_template_library_migration_preserves_legacy_templates(self) -> None:
        database_path = Path(self.temp_dir.name) / "outreach_template_library.db"
        env = os.environ.copy()
        env["DATABASE_URL"] = f"sqlite+aiosqlite:///{database_path.as_posix()}"
        previous_revision = "20260721_identity_comm_groups"

        self._run_alembic(env, "upgrade", previous_revision)
        connection = sqlite3.connect(database_path)
        try:
            default_identity_id = DatabaseSchemaTests._insert_identity_into(
                connection,
                email_address="template-migration@example.com",
            )
            empty_identity_id = DatabaseSchemaTests._insert_identity_into(
                connection,
                email_address="empty-template@example.com",
            )
            html_only_identity_id = DatabaseSchemaTests._insert_identity_into(
                connection,
                email_address="html-only-template@example.com",
            )
            connection.execute(
                """
                UPDATE identity_profiles
                SET is_default = 1,
                    outreach_generation_mode = 'template',
                    outreach_template_subject = ?,
                    outreach_template_body_text = ?,
                    outreach_template_body_html = ?
                WHERE id = ?
                """,
                (
                    " 申请与 {{name}} 老师交流 ",
                    "{{name}} 老师您好，我是 {{sender_name}}。",
                    "<p><strong>{{name}}</strong> 老师您好。</p>",
                    default_identity_id,
                ),
            )
            connection.execute(
                """
                UPDATE identity_profiles
                SET outreach_template_body_html = '<p>仅 HTML 的旧草稿</p>'
                WHERE id = ?
                """,
                (html_only_identity_id,),
            )
            llm_profile_id = DatabaseSchemaTests._insert_llm_profile_into(
                connection,
                name="迁移测试模型",
            )
            professor_id = DatabaseSchemaTests._insert_professor_into(
                connection,
                "template-migration@example.edu",
            )
            task_id = DatabaseSchemaTests._insert_workspace_root_task_into(
                connection,
                default_identity_id,
                llm_profile_id,
                professor_id,
            )
            fallback_professor_id = DatabaseSchemaTests._insert_professor_into(
                connection,
                "template-fallback-migration@example.edu",
            )
            fallback_task_id = DatabaseSchemaTests._insert_workspace_root_task_into(
                connection,
                empty_identity_id,
                llm_profile_id,
                fallback_professor_id,
            )
            connection.execute(
                """
                UPDATE email_tasks
                SET outreach_generation_mode = 'template',
                    outreach_template_subject = '任务自己的主题',
                    outreach_template_body_text = '任务自己的正文',
                    outreach_template_body_html = '<p>任务自己的正文</p>'
                WHERE id = ?
                """,
                (task_id,),
            )
            connection.execute(
                """
                UPDATE email_tasks
                SET outreach_generation_mode = 'template'
                WHERE id = ?
                """,
                (fallback_task_id,),
            )
            connection.commit()
        finally:
            connection.close()

        self._run_alembic(env, "upgrade", "head")
        upgraded = sqlite3.connect(database_path)
        try:
            template_rows = upgraded.execute(
                """
                SELECT
                    migrated_from_identity_id,
                    recommended_generation_mode,
                    subject,
                    body_text,
                    body_html,
                    is_default
                FROM outreach_templates
                ORDER BY migrated_from_identity_id
                """
            ).fetchall()
            identity_links = dict(
                upgraded.execute(
                    """
                    SELECT id, default_outreach_template_id
                    FROM identity_profiles
                    ORDER BY id
                    """
                ).fetchall()
            )
            task_row = upgraded.execute(
                """
                SELECT
                    outreach_template_id,
                    outreach_template_snapshot_version,
                    outreach_generation_mode,
                    outreach_template_subject,
                    outreach_template_body_text,
                    outreach_template_body_html
                FROM email_tasks
                WHERE id = ?
                """,
                (task_id,),
            ).fetchone()
            fallback_snapshot_version = upgraded.execute(
                """
                SELECT outreach_template_snapshot_version
                FROM email_tasks
                WHERE id = ?
                """,
                (fallback_task_id,),
            ).fetchone()[0]
        finally:
            upgraded.close()

        self.assertEqual(len(template_rows), 3)
        self.assertEqual(
            template_rows[0],
            (
                default_identity_id,
                "template",
                " 申请与 {{name}} 老师交流 ",
                "{{name}} 老师您好，我是 {{sender_name}}。",
                "<p><strong>{{name}}</strong> 老师您好。</p>",
                1,
            ),
        )
        self.assertEqual(
            template_rows[1],
            (
                empty_identity_id,
                "llm",
                None,
                None,
                None,
                0,
            ),
        )
        self.assertEqual(
            template_rows[2],
            (
                html_only_identity_id,
                "llm",
                None,
                None,
                "<p>仅 HTML 的旧草稿</p>",
                0,
            ),
        )
        self.assertIsNotNone(identity_links[default_identity_id])
        self.assertIsNotNone(identity_links[empty_identity_id])
        self.assertIsNotNone(identity_links[html_only_identity_id])
        self.assertEqual(
            task_row,
            (
                None,
                1,
                "template",
                "任务自己的主题",
                "任务自己的正文",
                "<p>任务自己的正文</p>",
            ),
        )
        self.assertIsNone(fallback_snapshot_version)

        self._run_alembic(env, "downgrade", previous_revision)
        downgraded = sqlite3.connect(database_path)
        try:
            tables = {
                row[0]
                for row in downgraded.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'",
                ).fetchall()
            }
            restored = downgraded.execute(
                """
                SELECT
                    outreach_generation_mode,
                    outreach_template_subject,
                    outreach_template_body_text,
                    outreach_template_body_html
                FROM identity_profiles
                WHERE id = ?
                """,
                (default_identity_id,),
            ).fetchone()
        finally:
            downgraded.close()

        self.assertNotIn("outreach_templates", tables)
        self.assertEqual(
            restored,
            (
                "template",
                " 申请与 {{name}} 老师交流 ",
                "{{name}} 老师您好，我是 {{sender_name}}。",
                "<p><strong>{{name}}</strong> 老师您好。</p>",
            ),
        )

    def test_outreach_template_library_downgrade_refuses_data_loss(self) -> None:
        database_path = Path(self.temp_dir.name) / "outreach_template_downgrade.db"
        env = os.environ.copy()
        env["DATABASE_URL"] = f"sqlite+aiosqlite:///{database_path.as_posix()}"
        previous_revision = "20260721_identity_comm_groups"

        self._run_alembic(env, "upgrade", "head")
        connection = sqlite3.connect(database_path)
        try:
            connection.execute(
                """
                INSERT INTO outreach_templates (
                    name,
                    recommended_generation_mode,
                    subject,
                    body_text
                )
                VALUES ('独立模板', 'llm', '主题', '正文')
                """
            )
            connection.commit()
        finally:
            connection.close()

        result = self._run_alembic_result(env, "downgrade", previous_revision)

        self.assertNotEqual(result.returncode, 0)
        self.assertIn(
            "cannot downgrade outreach template library without losing independent templates",
            result.stdout + result.stderr,
        )

    def test_database_performance_index_migration_upgrades_and_downgrades(self) -> None:
        database_path = Path(self.temp_dir.name) / "database_performance_indexes.db"
        env = os.environ.copy()
        env["DATABASE_URL"] = f"sqlite+aiosqlite:///{database_path.as_posix()}"
        previous_revision = "20260730_crawler_expansion"

        self._run_alembic(env, "upgrade", previous_revision)
        before_upgrade = sqlite3.connect(database_path)
        try:
            for table_name, expected_indexes in PERFORMANCE_INDEXES.items():
                existing_indexes = {
                    row[1]
                    for row in before_upgrade.execute(
                        f"PRAGMA index_list('{table_name}')",
                    ).fetchall()
                }
                self.assertTrue(expected_indexes.isdisjoint(existing_indexes))
        finally:
            before_upgrade.close()

        self._run_alembic(env, "upgrade", "head")
        upgraded = sqlite3.connect(database_path)
        try:
            for table_name, expected_indexes in PERFORMANCE_INDEXES.items():
                existing_indexes = {
                    row[1]
                    for row in upgraded.execute(
                        f"PRAGMA index_list('{table_name}')",
                    ).fetchall()
                }
                self.assertTrue(expected_indexes.issubset(existing_indexes))
        finally:
            upgraded.close()

        self._run_alembic(env, "downgrade", previous_revision)
        downgraded = sqlite3.connect(database_path)
        try:
            for table_name, expected_indexes in PERFORMANCE_INDEXES.items():
                existing_indexes = {
                    row[1]
                    for row in downgraded.execute(
                        f"PRAGMA index_list('{table_name}')",
                    ).fetchall()
                }
                self.assertTrue(expected_indexes.isdisjoint(existing_indexes))
        finally:
            downgraded.close()

        self._run_alembic(env, "upgrade", "head")

    def test_batch_template_snapshot_migration_upgrades_pre_library_batch_data(self) -> None:
        database_path = Path(self.temp_dir.name) / "batch_template_snapshot_pre_library.db"
        env = os.environ.copy()
        env["DATABASE_URL"] = f"sqlite+aiosqlite:///{database_path.as_posix()}"
        pre_library_revision = "2f6a9d8c1e20"

        self._run_alembic(env, "upgrade", pre_library_revision)
        connection = sqlite3.connect(database_path)
        try:
            identity_id = DatabaseSchemaTests._insert_identity_into(
                connection,
                email_address="batch-template-pre-library@example.com",
            )
            llm_profile_id = DatabaseSchemaTests._insert_llm_profile_into(
                connection,
                name="模板库前批次模型",
            )
            professor_id = DatabaseSchemaTests._insert_professor_into(
                connection,
                "batch-template-pre-library@example.edu",
            )
            batch_task_id = int(
                connection.execute(
                    """
                    INSERT INTO batch_tasks (
                        identity_id,
                        llm_profile_id,
                        name,
                        email_subject,
                        email_body,
                        target_count
                    )
                    VALUES (?, ?, '模板库前历史批次', '旧批次主题', '旧批次正文', 1)
                    """,
                    (identity_id, llm_profile_id),
                ).lastrowid,
            )
            connection.execute(
                """
                INSERT INTO email_tasks (
                    batch_task_id,
                    identity_id,
                    llm_profile_id,
                    professor_id,
                    status,
                    outreach_generation_mode,
                    outreach_template_subject,
                    outreach_template_body_text,
                    outreach_template_body_html
                )
                VALUES (?, ?, ?, ?, 'review_required', 'llm', ?, ?, ?)
                """,
                (
                    batch_task_id,
                    identity_id,
                    llm_profile_id,
                    professor_id,
                    "模板库前最终主题 {{name}}",
                    "模板库前最终正文 {{sender_name}}",
                    "<p>模板库前最终正文 {{sender_name}}</p>",
                ),
            )
            connection.commit()
        finally:
            connection.close()

        self._run_alembic(env, "upgrade", "head")
        upgraded = sqlite3.connect(database_path)
        try:
            snapshot = upgraded.execute(
                """
                SELECT
                    outreach_template_id,
                    outreach_template_name_snapshot,
                    outreach_template_snapshot_version,
                    outreach_generation_mode,
                    outreach_template_subject,
                    outreach_template_body_text,
                    outreach_template_body_html,
                    email_subject,
                    email_body
                FROM batch_tasks
                WHERE id = ?
                """,
                (batch_task_id,),
            ).fetchone()
        finally:
            upgraded.close()

        self.assertEqual(
            snapshot,
            (
                None,
                None,
                1,
                "llm",
                "模板库前最终主题 {{name}}",
                "模板库前最终正文 {{sender_name}}",
                "<p>模板库前最终正文 {{sender_name}}</p>",
                "旧批次主题",
                "旧批次正文",
            ),
        )

    def test_batch_template_snapshot_migration_backfills_existing_batches(self) -> None:
        database_path = Path(self.temp_dir.name) / "batch_template_snapshot.db"
        env = os.environ.copy()
        env["DATABASE_URL"] = f"sqlite+aiosqlite:///{database_path.as_posix()}"
        previous_revision = "20260730_db_performance"

        self._run_alembic(env, "upgrade", previous_revision)
        connection = sqlite3.connect(database_path)
        try:
            identity_id = DatabaseSchemaTests._insert_identity_into(
                connection,
                email_address="batch-template-migration@example.com",
            )
            llm_profile_id = DatabaseSchemaTests._insert_llm_profile_into(
                connection,
                name="批次模板迁移模型",
            )
            professor_id = DatabaseSchemaTests._insert_professor_into(
                connection,
                "batch-template-migration@example.edu",
            )
            template_id = connection.execute(
                """
                INSERT INTO outreach_templates (
                    name,
                    recommended_generation_mode,
                    subject,
                    body_text,
                    body_html
                )
                VALUES (?, 'llm', ?, ?, ?)
                """,
                (
                    "迁移前批次模板",
                    "迁移主题 {{name}}",
                    "迁移正文 {{sender_name}}",
                    "<p>迁移正文 {{sender_name}}</p>",
                ),
            ).lastrowid
            batch_task_id = connection.execute(
                """
                INSERT INTO batch_tasks (
                    identity_id,
                    llm_profile_id,
                    name,
                    email_subject,
                    email_body,
                    target_count
                )
                VALUES (?, ?, ?, ?, ?, 1)
                """,
                (
                    identity_id,
                    llm_profile_id,
                    "迁移前批量任务",
                    "批次旧主题",
                    "批次旧正文",
                ),
            ).lastrowid
            connection.execute(
                """
                INSERT INTO email_tasks (
                    source,
                    batch_task_id,
                    identity_id,
                    llm_profile_id,
                    professor_id,
                    status,
                    outreach_template_id,
                    outreach_template_snapshot_version,
                    outreach_generation_mode,
                    outreach_template_subject,
                    outreach_template_body_text,
                    outreach_template_body_html
                )
                VALUES ('batch', ?, ?, ?, ?, 'review_required', ?, 1, 'llm', ?, ?, ?)
                """,
                (
                    batch_task_id,
                    identity_id,
                    llm_profile_id,
                    professor_id,
                    template_id,
                    "最终主题 {{name}}",
                    "最终正文 {{sender_name}}",
                    "<p>最终正文 {{sender_name}}</p>",
                ),
            )
            connection.commit()
        finally:
            connection.close()

        self._run_alembic(env, "upgrade", "head")
        upgraded = sqlite3.connect(database_path)
        try:
            snapshot = upgraded.execute(
                """
                SELECT
                    outreach_template_id,
                    outreach_template_name_snapshot,
                    outreach_template_snapshot_version,
                    outreach_generation_mode,
                    outreach_template_subject,
                    outreach_template_body_text,
                    outreach_template_body_html
                FROM batch_tasks
                WHERE id = ?
                """,
                (batch_task_id,),
            ).fetchone()
        finally:
            upgraded.close()

        self.assertEqual(
            snapshot,
            (
                template_id,
                "迁移前批次模板",
                1,
                "llm",
                "最终主题 {{name}}",
                "最终正文 {{sender_name}}",
                "<p>最终正文 {{sender_name}}</p>",
            ),
        )

        self._run_alembic(env, "downgrade", previous_revision)
        downgraded = sqlite3.connect(database_path)
        try:
            downgraded_columns = {
                row[1]
                for row in downgraded.execute("PRAGMA table_info(batch_tasks)").fetchall()
            }
        finally:
            downgraded.close()
        self.assertNotIn("outreach_template_id", downgraded_columns)
        self.assertNotIn("outreach_template_name_snapshot", downgraded_columns)

        self._run_alembic(env, "upgrade", "head")

    def test_batch_template_snapshot_migration_preserves_legacy_rows_and_skips_unversioned_sources(
        self,
    ) -> None:
        database_path = Path(self.temp_dir.name) / "batch_template_snapshot_safety.db"
        env = os.environ.copy()
        env["DATABASE_URL"] = f"sqlite+aiosqlite:///{database_path.as_posix()}"
        previous_revision = "20260730_db_performance"

        self._run_alembic(env, "upgrade", previous_revision)
        connection = sqlite3.connect(database_path)
        try:
            identity_id = DatabaseSchemaTests._insert_identity_into(
                connection,
                email_address="batch-template-safety@example.com",
            )
            connection.execute(
                "UPDATE identity_profiles SET outreach_generation_mode = 'template' WHERE id = ?",
                (identity_id,),
            )
            llm_profile_id = DatabaseSchemaTests._insert_llm_profile_into(
                connection,
                name="批次迁移安全模型",
            )
            professor_id = DatabaseSchemaTests._insert_professor_into(
                connection,
                "batch-template-safety@example.edu",
            )
            template_id = int(
                connection.execute(
                    """
                    INSERT INTO outreach_templates (
                        name,
                        recommended_generation_mode,
                        subject,
                        body_text,
                        body_html
                    )
                    VALUES ('历史模板', 'template', '库主题', '库正文', '<p>库正文</p>')
                    """,
                ).lastrowid,
            )
            stale_template_id = int(
                connection.execute(
                    """
                    INSERT INTO outreach_templates (
                        name,
                        recommended_generation_mode,
                        subject,
                        body_text,
                        body_html
                    )
                    VALUES ('稍后删除的模板', 'template', '旧主题', '旧正文', '<p>旧正文</p>')
                    """,
                ).lastrowid,
            )

            def insert_batch(name: str, subject: str, body: str, target_count: int) -> int:
                return int(
                    connection.execute(
                        """
                        INSERT INTO batch_tasks (
                            identity_id,
                            llm_profile_id,
                            name,
                            schedule_type,
                            window_start_time,
                            window_end_time,
                            emails_per_window,
                            scheduled_dates,
                            status,
                            email_subject,
                            email_body,
                            selected_material_ids,
                            target_count,
                            created_at,
                            updated_at,
                            deleted_at
                        )
                        VALUES (
                            ?, ?, ?, 'scheduled', '08:05', '18:55', 7, ?, 'stopped',
                            ?, ?, ?, ?, '2026-07-31 01:02:03', '2026-08-01 04:05:06',
                            '2026-08-01 07:08:09'
                        )
                        """,
                        (
                            identity_id,
                            llm_profile_id,
                            name,
                            json.dumps(["2026-08-02", "2026-08-03"]),
                            subject,
                            body,
                            json.dumps([9, 3, 9]),
                            target_count,
                        ),
                    ).lastrowid,
                )

            def insert_email_task(
                batch_task_id: int,
                *,
                template_source_id: int | None,
                snapshot_version: int | None,
                generation_mode: str | None,
                subject: str | None,
                body_text: str | None,
                body_html: str | None,
                created_at: str,
            ) -> None:
                connection.execute(
                    """
                    INSERT INTO email_tasks (
                        source,
                        batch_task_id,
                        identity_id,
                        llm_profile_id,
                        professor_id,
                        status,
                        outreach_template_id,
                        outreach_template_snapshot_version,
                        outreach_generation_mode,
                        outreach_template_subject,
                        outreach_template_body_text,
                        outreach_template_body_html,
                        created_at,
                        updated_at
                    )
                    VALUES (
                        'batch', ?, ?, ?, ?, 'review_required', ?, ?, ?, ?, ?, ?, ?, ?
                    )
                    """,
                    (
                        batch_task_id,
                        identity_id,
                        llm_profile_id,
                        professor_id,
                        template_source_id,
                        snapshot_version,
                        generation_mode,
                        subject,
                        body_text,
                        body_html,
                        created_at,
                        created_at,
                    ),
                )

            versioned_batch_id = insert_batch(
                "有版本快照的历史批次",
                "批次回退主题",
                "批次回退正文",
                2,
            )
            insert_email_task(
                versioned_batch_id,
                template_source_id=template_id,
                snapshot_version=1,
                generation_mode=None,
                subject="第一封最终主题 {{name}}",
                body_text="第一封最终正文 {{sender_name}}",
                body_html="<p>第一封最终正文 {{sender_name}}</p>",
                created_at="2026-07-31 02:00:00",
            )
            insert_email_task(
                versioned_batch_id,
                template_source_id=None,
                snapshot_version=1,
                generation_mode="llm",
                subject="不能覆盖第一封的主题",
                body_text="不能覆盖第一封的正文",
                body_html=None,
                created_at="2026-07-31 03:00:00",
            )

            unversioned_batch_id = insert_batch(
                "没有版本标记的残缺批次",
                "保留旧批次主题",
                "保留旧批次正文",
                1,
            )
            insert_email_task(
                unversioned_batch_id,
                template_source_id=template_id,
                snapshot_version=None,
                generation_mode="llm",
                subject="未标记版本的主题",
                body_text="未标记版本的正文",
                body_html="<p>未标记版本的正文</p>",
                created_at="2026-07-31 04:00:00",
            )

            no_child_batch_id = insert_batch(
                "没有子任务的历史批次",
                "孤立批次主题",
                "孤立批次正文",
                0,
            )

            deleted_template_batch_id = insert_batch(
                "来源模板已删除的历史批次",
                "删除模板批次主题",
                "删除模板批次正文",
                1,
            )
            insert_email_task(
                deleted_template_batch_id,
                template_source_id=stale_template_id,
                snapshot_version=1,
                generation_mode="template",
                subject="删除前最终主题",
                body_text="删除前最终正文",
                body_html="<p>删除前最终正文</p>",
                created_at="2026-07-31 05:00:00",
            )
            # Historical SQLite installations did not always enforce foreign keys.
            # A stale provenance ID must not be copied into the new batch FK.
            connection.execute(
                "DELETE FROM outreach_templates WHERE id = ?",
                (stale_template_id,),
            )
            connection.commit()

            legacy_columns = [
                row[1]
                for row in connection.execute("PRAGMA table_info(batch_tasks)").fetchall()
            ]
            legacy_projection = ", ".join(f'"{column}"' for column in legacy_columns)
            legacy_rows_before = connection.execute(
                f"SELECT {legacy_projection} FROM batch_tasks ORDER BY id",
            ).fetchall()
        finally:
            connection.close()

        self._run_alembic(env, "upgrade", "head")
        upgraded = sqlite3.connect(database_path)
        try:
            legacy_rows_after = upgraded.execute(
                f"SELECT {legacy_projection} FROM batch_tasks ORDER BY id",
            ).fetchall()
            snapshots = {
                row[0]: row[1:]
                for row in upgraded.execute(
                    """
                    SELECT
                        id,
                        outreach_template_id,
                        outreach_template_name_snapshot,
                        outreach_template_snapshot_version,
                        outreach_generation_mode,
                        outreach_template_subject,
                        outreach_template_body_text,
                        outreach_template_body_html
                    FROM batch_tasks
                    ORDER BY id
                    """,
                ).fetchall()
            }
            batch_foreign_keys = upgraded.execute(
                "PRAGMA foreign_key_list(batch_tasks)",
            ).fetchall()
            batch_foreign_key_violations = upgraded.execute(
                "PRAGMA foreign_key_check(batch_tasks)",
            ).fetchall()
            batch_indexes = {
                row[1]
                for row in upgraded.execute("PRAGMA index_list(batch_tasks)").fetchall()
            }
            upgraded.execute("PRAGMA foreign_keys = ON")
            upgraded.execute(
                "DELETE FROM outreach_templates WHERE id = ?",
                (template_id,),
            )
            upgraded.commit()
            snapshot_after_template_delete = upgraded.execute(
                """
                SELECT
                    outreach_template_id,
                    outreach_template_name_snapshot,
                    outreach_template_snapshot_version,
                    outreach_template_subject,
                    outreach_template_body_text,
                    outreach_template_body_html
                FROM batch_tasks
                WHERE id = ?
                """,
                (versioned_batch_id,),
            ).fetchone()
        finally:
            upgraded.close()

        self.assertEqual(legacy_rows_after, legacy_rows_before)
        self.assertEqual(
            snapshots[versioned_batch_id],
            (
                template_id,
                "历史模板",
                1,
                "template",
                "第一封最终主题 {{name}}",
                "第一封最终正文 {{sender_name}}",
                "<p>第一封最终正文 {{sender_name}}</p>",
            ),
        )
        self.assertEqual(snapshots[unversioned_batch_id], (None,) * 7)
        self.assertEqual(snapshots[no_child_batch_id], (None,) * 7)
        self.assertEqual(
            snapshots[deleted_template_batch_id],
            (
                None,
                None,
                1,
                "template",
                "删除前最终主题",
                "删除前最终正文",
                "<p>删除前最终正文</p>",
            ),
        )
        self.assertTrue(
            any(
                row[2] == "outreach_templates"
                and row[3] == "outreach_template_id"
                and row[6].upper() == "SET NULL"
                for row in batch_foreign_keys
            ),
        )
        self.assertEqual(batch_foreign_key_violations, [])
        self.assertIn("ix_batch_tasks_outreach_template_id", batch_indexes)
        self.assertEqual(
            snapshot_after_template_delete,
            (
                None,
                "历史模板",
                1,
                "第一封最终主题 {{name}}",
                "第一封最终正文 {{sender_name}}",
                "<p>第一封最终正文 {{sender_name}}</p>",
            ),
        )

    def test_batch_template_snapshot_migration_resumes_from_partial_schema(self) -> None:
        database_path = Path(self.temp_dir.name) / "batch_template_snapshot_partial.db"
        env = os.environ.copy()
        env["DATABASE_URL"] = f"sqlite+aiosqlite:///{database_path.as_posix()}"
        previous_revision = "20260730_db_performance"

        self._run_alembic(env, "upgrade", previous_revision)
        connection = sqlite3.connect(database_path)
        try:
            identity_id = DatabaseSchemaTests._insert_identity_into(
                connection,
                email_address="batch-template-partial@example.com",
            )
            llm_profile_id = DatabaseSchemaTests._insert_llm_profile_into(
                connection,
                name="批次迁移恢复模型",
            )
            professor_id = DatabaseSchemaTests._insert_professor_into(
                connection,
                "batch-template-partial@example.edu",
            )
            template_id = int(
                connection.execute(
                    """
                    INSERT INTO outreach_templates (
                        name,
                        recommended_generation_mode,
                        subject,
                        body_text
                    )
                    VALUES ('恢复模板', 'llm', '恢复库主题', '恢复库正文')
                    """,
                ).lastrowid,
            )
            batch_task_id = int(
                connection.execute(
                    """
                    INSERT INTO batch_tasks (
                        identity_id,
                        llm_profile_id,
                        name,
                        email_subject,
                        email_body,
                        target_count
                    )
                    VALUES (?, ?, '迁移中断批次', '回退主题', '回退正文', 1)
                    """,
                    (identity_id, llm_profile_id),
                ).lastrowid,
            )
            connection.execute(
                """
                INSERT INTO email_tasks (
                    source,
                    batch_task_id,
                    identity_id,
                    llm_profile_id,
                    professor_id,
                    status,
                    outreach_template_id,
                    outreach_template_snapshot_version,
                    outreach_generation_mode,
                    outreach_template_subject,
                    outreach_template_body_text
                )
                VALUES ('batch', ?, ?, ?, ?, 'review_required', ?, 1, 'llm', ?, ?)
                """,
                (
                    batch_task_id,
                    identity_id,
                    llm_profile_id,
                    professor_id,
                    template_id,
                    "恢复最终主题",
                    "恢复最终正文",
                ),
            )
            connection.executescript(
                """
                ALTER TABLE batch_tasks ADD COLUMN outreach_template_id INTEGER;
                ALTER TABLE batch_tasks ADD COLUMN outreach_template_name_snapshot VARCHAR(120);
                ALTER TABLE batch_tasks ADD COLUMN outreach_template_snapshot_version INTEGER;
                CREATE INDEX ix_batch_tasks_outreach_template_id
                    ON batch_tasks (outreach_template_id);
                """,
            )
            connection.execute(
                """
                UPDATE batch_tasks
                SET outreach_template_name_snapshot = '迁移中断残值',
                    outreach_template_snapshot_version = 99
                WHERE id = ?
                """,
                (batch_task_id,),
            )
            connection.commit()
        finally:
            connection.close()

        self._run_alembic(env, "upgrade", "head")
        upgraded = sqlite3.connect(database_path)
        try:
            snapshot = upgraded.execute(
                """
                SELECT
                    outreach_template_id,
                    outreach_template_name_snapshot,
                    outreach_template_snapshot_version,
                    outreach_generation_mode,
                    outreach_template_subject,
                    outreach_template_body_text,
                    outreach_template_body_html
                FROM batch_tasks
                WHERE id = ?
                """,
                (batch_task_id,),
            ).fetchone()
            columns = {
                row[1] for row in upgraded.execute("PRAGMA table_info(batch_tasks)").fetchall()
            }
            index_names = [
                row[1] for row in upgraded.execute("PRAGMA index_list(batch_tasks)").fetchall()
            ]
        finally:
            upgraded.close()

        self.assertEqual(
            snapshot,
            (
                template_id,
                "恢复模板",
                1,
                "llm",
                "恢复最终主题",
                "恢复最终正文",
                None,
            ),
        )
        self.assertTrue(
            {
                "outreach_template_id",
                "outreach_template_name_snapshot",
                "outreach_template_snapshot_version",
                "outreach_generation_mode",
                "outreach_template_subject",
                "outreach_template_body_text",
                "outreach_template_body_html",
            }.issubset(columns),
        )
        self.assertEqual(index_names.count("ix_batch_tasks_outreach_template_id"), 1)

    def test_identity_professor_match_result_migration_backfills_latest_task_once(self) -> None:
        legacy_dir = tempfile.TemporaryDirectory()
        try:
            legacy_db_path = Path(legacy_dir.name) / "legacy_identity_match_results.db"
            legacy_env = os.environ.copy()
            legacy_env["DATABASE_URL"] = (
                f"sqlite+aiosqlite:///{legacy_db_path.as_posix()}"
            )
            self._run_alembic(
                legacy_env,
                "upgrade",
                "20260804_merge_agent_change_recent_papers",
            )

            connection = sqlite3.connect(legacy_db_path)
            connection.execute("PRAGMA foreign_keys = ON")
            identity_id = DatabaseSchemaTests._insert_identity_into(
                connection,
                email_address="legacy-match-result@example.com",
            )
            llm_profile_id = DatabaseSchemaTests._insert_llm_profile_into(
                connection,
                name="匹配结果迁移模型",
            )
            professor_id = DatabaseSchemaTests._insert_professor_into(
                connection,
                "legacy-match-result-professor@example.edu",
            )
            material_id = DatabaseSchemaTests._insert_identity_material_into(
                connection,
                identity_id,
                display_name="迁移默认简历",
                original_filename="migration-resume.txt",
            )
            connection.execute(
                "UPDATE identity_profiles SET current_primary_material_id = ? WHERE id = ?",
                (material_id, identity_id),
            )
            older_task_id = DatabaseSchemaTests._insert_email_task_with_material_into(
                connection,
                identity_id,
                llm_profile_id,
                professor_id,
                primary_material_id=material_id,
                updated_at="2026-08-01 08:00:00",
            )
            latest_task_id = DatabaseSchemaTests._insert_manual_child_task_into(
                connection,
                parent_task_id=older_task_id,
                identity_id=identity_id,
                llm_profile_id=llm_profile_id,
                professor_id=professor_id,
            )
            connection.execute(
                """
                UPDATE email_tasks
                SET match_score = ?, match_reason = ?, fit_points = ?,
                    risk_points = ?, match_keywords = ?
                WHERE id = ?
                """,
                (
                    71,
                    "旧任务结果",
                    json.dumps(["旧契合点"]),
                    json.dumps([]),
                    json.dumps(["old"]),
                    older_task_id,
                ),
            )
            connection.execute(
                """
                UPDATE email_tasks
                SET primary_material_id = ?, match_score = ?, match_reason = ?,
                    fit_points = ?, risk_points = ?, match_keywords = ?,
                    created_at = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    material_id,
                    92,
                    "最新任务结果",
                    json.dumps(["最新契合点"]),
                    json.dumps(["最新风险点"]),
                    json.dumps(["latest"]),
                    "2026-08-02 08:00:00",
                    "2026-08-02 08:00:00",
                    latest_task_id,
                ),
            )
            run_cursor = connection.execute(
                """
                INSERT INTO match_analysis_runs (
                    email_task_id, professor_id, identity_id, llm_profile_id,
                    primary_material_id, status, success, match_score, finished_at
                )
                VALUES (?, ?, ?, ?, ?, 'succeeded', 1, 92, ?)
                """,
                (
                    latest_task_id,
                    professor_id,
                    identity_id,
                    llm_profile_id,
                    material_id,
                    "2026-08-02 08:01:00",
                ),
            )
            latest_run_id = int(run_cursor.lastrowid)
            connection.commit()
            connection.close()

            self._run_alembic(legacy_env, "upgrade", "head")

            upgraded = sqlite3.connect(legacy_db_path)
            rows = upgraded.execute(
                """
                SELECT identity_id, professor_id, source_email_task_id,
                       latest_analysis_run_id, primary_material_id,
                       match_score, match_reason, fit_points, risk_points,
                       match_keywords
                FROM identity_professor_match_results
                """,
            ).fetchall()
            run_details = upgraded.execute(
                """
                SELECT match_reason, fit_points, risk_points, match_keywords
                FROM match_analysis_runs
                WHERE id = ?
                """,
                (latest_run_id,),
            ).fetchone()
            task_match_source_identity_id = upgraded.execute(
                "SELECT match_source_identity_id FROM email_tasks WHERE id = ?",
                (latest_task_id,),
            ).fetchone()[0]
            upgraded.close()

            self.assertEqual(len(rows), 1)
            row = rows[0]
            self.assertEqual(row[0], identity_id)
            self.assertEqual(row[1], professor_id)
            self.assertEqual(row[2], latest_task_id)
            self.assertEqual(row[3], latest_run_id)
            self.assertEqual(row[4], material_id)
            self.assertEqual(row[5], 92)
            self.assertEqual(row[6], "最新任务结果")
            self.assertEqual(DatabaseSchemaTests._load_json(row[7]), ["最新契合点"])
            self.assertEqual(DatabaseSchemaTests._load_json(row[8]), ["最新风险点"])
            self.assertEqual(DatabaseSchemaTests._load_json(row[9]), ["latest"])
            self.assertEqual(run_details[0], "最新任务结果")
            self.assertEqual(
                DatabaseSchemaTests._load_json(run_details[1]),
                ["最新契合点"],
            )
            self.assertEqual(task_match_source_identity_id, identity_id)
        finally:
            legacy_dir.cleanup()

    def test_identity_match_migration_normalizes_malformed_legacy_results(self) -> None:
        legacy_dir = tempfile.TemporaryDirectory()
        try:
            legacy_db_path = Path(legacy_dir.name) / "malformed_identity_matches.db"
            legacy_env = os.environ.copy()
            legacy_env["DATABASE_URL"] = (
                f"sqlite+aiosqlite:///{legacy_db_path.as_posix()}"
            )
            previous_revision = "20260804_merge_agent_change_recent_papers"
            self._run_alembic(legacy_env, "upgrade", previous_revision)

            connection = sqlite3.connect(legacy_db_path)
            identity_id = DatabaseSchemaTests._insert_identity_into(
                connection,
                email_address="malformed-match@example.com",
            )
            llm_profile_id = DatabaseSchemaTests._insert_llm_profile_into(
                connection,
                name="异常旧匹配迁移模型",
            )
            malformed_professor_id = DatabaseSchemaTests._insert_professor_into(
                connection,
                "malformed-match-professor@example.edu",
            )
            fallback_professor_id = DatabaseSchemaTests._insert_professor_into(
                connection,
                "fallback-match-professor@example.edu",
            )
            malformed_task_id = (
                DatabaseSchemaTests._insert_email_task_with_material_into(
                    connection,
                    identity_id,
                    llm_profile_id,
                    malformed_professor_id,
                    primary_material_id=None,
                )
            )
            connection.execute(
                """
                UPDATE email_tasks
                SET match_score = 145,
                    match_reason = '异常旧结果',
                    fit_points = 'not-json',
                    risk_points = '{"unexpected": true}',
                    match_keywords = '["有效关键词", 2]'
                WHERE id = ?
                """,
                (malformed_task_id,),
            )

            fallback_task_id = (
                DatabaseSchemaTests._insert_email_task_with_material_into(
                    connection,
                    identity_id,
                    llm_profile_id,
                    fallback_professor_id,
                    primary_material_id=None,
                    updated_at="2026-08-01 08:00:00",
                )
            )
            connection.execute(
                """
                UPDATE email_tasks
                SET match_score = 82,
                    match_reason = '可用旧结果',
                    fit_points = '["可靠契合点"]'
                WHERE id = ?
                """,
                (fallback_task_id,),
            )
            invalid_latest_task_id = (
                DatabaseSchemaTests._insert_manual_child_task_into(
                    connection,
                    parent_task_id=fallback_task_id,
                    identity_id=identity_id,
                    llm_profile_id=llm_profile_id,
                    professor_id=fallback_professor_id,
                )
            )
            connection.execute(
                """
                UPDATE email_tasks
                SET match_score = 'not-a-number',
                    match_reason = '不可用的最新结果',
                    updated_at = '2026-08-02 08:00:00'
                WHERE id = ?
                """,
                (invalid_latest_task_id,),
            )
            connection.commit()
            connection.close()

            self._run_alembic(legacy_env, "upgrade", "head")

            upgraded = sqlite3.connect(legacy_db_path)
            rows = upgraded.execute(
                """
                SELECT professor_id, source_email_task_id, match_score,
                       match_reason, fit_points, risk_points, match_keywords
                FROM identity_professor_match_results
                ORDER BY professor_id ASC
                """,
            ).fetchall()
            upgraded.close()

            self.assertEqual(len(rows), 2)
            malformed_row, fallback_row = rows
            self.assertEqual(malformed_row[0], malformed_professor_id)
            self.assertEqual(malformed_row[2], 100)
            self.assertEqual(DatabaseSchemaTests._load_json(malformed_row[4]), [])
            self.assertEqual(DatabaseSchemaTests._load_json(malformed_row[5]), [])
            self.assertEqual(
                DatabaseSchemaTests._load_json(malformed_row[6]),
                ["有效关键词"],
            )
            self.assertEqual(fallback_row[0], fallback_professor_id)
            self.assertEqual(fallback_row[1], fallback_task_id)
            self.assertEqual(fallback_row[2], 82)
            self.assertEqual(fallback_row[3], "可用旧结果")
        finally:
            legacy_dir.cleanup()

    def test_identity_match_migration_recovers_partial_and_repeated_upgrade(self) -> None:
        legacy_dir = tempfile.TemporaryDirectory()
        try:
            legacy_db_path = Path(legacy_dir.name) / "partial_identity_matches.db"
            legacy_env = os.environ.copy()
            legacy_env["DATABASE_URL"] = (
                f"sqlite+aiosqlite:///{legacy_db_path.as_posix()}"
            )
            previous_revision = "20260804_merge_agent_change_recent_papers"
            self._run_alembic(legacy_env, "upgrade", previous_revision)

            partial = sqlite3.connect(legacy_db_path)
            partial.execute(
                "ALTER TABLE email_tasks ADD COLUMN match_source_identity_id INTEGER"
            )
            partial.execute(
                """
                ALTER TABLE identity_communication_groups
                ADD COLUMN match_source_identity_id INTEGER
                """
            )
            partial.execute(
                """
                ALTER TABLE match_analysis_jobs
                ADD COLUMN match_source_identity_id INTEGER
                """
            )
            partial.execute(
                "ALTER TABLE match_analysis_runs ADD COLUMN match_reason TEXT"
            )
            partial.commit()
            partial.close()

            self._run_alembic(legacy_env, "upgrade", "head")

            unstamped = sqlite3.connect(legacy_db_path)
            unstamped.execute(
                "DROP INDEX ix_identity_professor_match_results_identity_updated"
            )
            unstamped.execute(
                "UPDATE alembic_version SET version_num = ?",
                (previous_revision,),
            )
            unstamped.commit()
            unstamped.close()

            self._run_alembic(legacy_env, "upgrade", "head")

            upgraded = sqlite3.connect(legacy_db_path)
            version = upgraded.execute(
                "SELECT version_num FROM alembic_version",
            ).fetchone()[0]
            result_indexes = {
                row[1]
                for row in upgraded.execute(
                    "PRAGMA index_list('identity_professor_match_results')",
                ).fetchall()
            }
            group_foreign_keys = upgraded.execute(
                "PRAGMA foreign_key_list('identity_communication_groups')",
            ).fetchall()
            job_foreign_keys = upgraded.execute(
                "PRAGMA foreign_key_list('match_analysis_jobs')",
            ).fetchall()
            run_columns = {
                row[1]
                for row in upgraded.execute(
                    "PRAGMA table_info('match_analysis_runs')",
                ).fetchall()
            }
            upgraded.close()

            self.assertEqual(version, HEAD_REVISION)
            self.assertIn(
                "ix_identity_professor_match_results_identity_updated",
                result_indexes,
            )
            self.assertTrue(
                any(
                    row[2] == "identity_profiles"
                    and row[3] == "match_source_identity_id"
                    for row in group_foreign_keys
                ),
            )
            self.assertTrue(
                any(
                    row[2] == "identity_profiles"
                    and row[3] == "match_source_identity_id"
                    for row in job_foreign_keys
                ),
            )
            self.assertTrue(
                {"match_reason", "fit_points", "risk_points", "match_keywords"}
                .issubset(run_columns),
            )
        finally:
            legacy_dir.cleanup()

    def test_identity_match_migration_repairs_orphaned_source_identity_ids(self) -> None:
        legacy_db_path = Path(self.temp_dir.name) / "orphaned-match-source-ids.db"
        legacy_env = os.environ.copy()
        legacy_env["DATABASE_URL"] = (
            f"sqlite+aiosqlite:///{legacy_db_path.as_posix()}"
        )
        previous_revision = "20260804_merge_agent_change_recent_papers"
        self._run_alembic(legacy_env, "upgrade", previous_revision)

        connection = sqlite3.connect(legacy_db_path)
        identity_id = DatabaseSchemaTests._insert_identity_into(
            connection,
            email_address="orphaned-match-source@example.com",
        )
        llm_profile_id = DatabaseSchemaTests._insert_llm_profile_into(
            connection,
            name="孤立匹配来源迁移模型",
        )
        group_id = int(
            connection.execute(
                "INSERT INTO identity_communication_groups DEFAULT VALUES",
            ).lastrowid,
        )
        job_id = int(
            connection.execute(
                """
                INSERT INTO match_analysis_jobs (name, identity_id, llm_profile_id)
                VALUES ('孤立来源任务', ?, ?)
                """,
                (identity_id, llm_profile_id),
            ).lastrowid,
        )
        connection.execute(
            """
            ALTER TABLE identity_communication_groups
            ADD COLUMN match_source_identity_id INTEGER
            """,
        )
        connection.execute(
            """
            ALTER TABLE match_analysis_jobs
            ADD COLUMN match_source_identity_id INTEGER
            """,
        )
        missing_identity_id = identity_id + 100_000
        connection.execute(
            """
            UPDATE identity_communication_groups
            SET match_source_identity_id = ?
            WHERE id = ?
            """,
            (missing_identity_id, group_id),
        )
        connection.execute(
            """
            UPDATE match_analysis_jobs
            SET match_source_identity_id = ?
            WHERE id = ?
            """,
            (missing_identity_id, job_id),
        )
        connection.commit()
        connection.close()

        self._run_alembic(legacy_env, "upgrade", "head")

        upgraded = sqlite3.connect(legacy_db_path)
        group_source_id = upgraded.execute(
            """
            SELECT match_source_identity_id
            FROM identity_communication_groups
            WHERE id = ?
            """,
            (group_id,),
        ).fetchone()[0]
        job_source_id = upgraded.execute(
            """
            SELECT match_source_identity_id
            FROM match_analysis_jobs
            WHERE id = ?
            """,
            (job_id,),
        ).fetchone()[0]
        group_foreign_keys = upgraded.execute(
            "PRAGMA foreign_key_list('identity_communication_groups')",
        ).fetchall()
        job_foreign_keys = upgraded.execute(
            "PRAGMA foreign_key_list('match_analysis_jobs')",
        ).fetchall()
        version = upgraded.execute(
            "SELECT version_num FROM alembic_version",
        ).fetchone()[0]
        upgraded.close()

        self.assertIsNone(group_source_id)
        self.assertEqual(job_source_id, identity_id)
        self.assertTrue(
            any(
                row[2] == "identity_profiles"
                and row[3] == "match_source_identity_id"
                for row in group_foreign_keys
            ),
        )
        self.assertTrue(
            any(
                row[2] == "identity_profiles"
                and row[3] == "match_source_identity_id"
                for row in job_foreign_keys
            ),
        )
        self.assertEqual(version, HEAD_REVISION)

    def test_identity_match_migration_rebuilds_incomplete_result_table(self) -> None:
        legacy_db_path = Path(self.temp_dir.name) / "incomplete-match-results.db"
        legacy_env = os.environ.copy()
        legacy_env["DATABASE_URL"] = (
            f"sqlite+aiosqlite:///{legacy_db_path.as_posix()}"
        )
        previous_revision = "20260804_merge_agent_change_recent_papers"
        self._run_alembic(legacy_env, "upgrade", previous_revision)

        connection = sqlite3.connect(legacy_db_path)
        identity_id = DatabaseSchemaTests._insert_identity_into(
            connection,
            email_address="incomplete-match-result@example.com",
        )
        llm_profile_id = DatabaseSchemaTests._insert_llm_profile_into(
            connection,
            name="半成品匹配结果迁移模型",
        )
        professor_id = DatabaseSchemaTests._insert_professor_into(
            connection,
            "incomplete-match-result-professor@example.edu",
        )
        task_id = DatabaseSchemaTests._insert_email_task_with_material_into(
            connection,
            identity_id,
            llm_profile_id,
            professor_id,
            primary_material_id=None,
        )
        connection.execute(
            """
            UPDATE email_tasks
            SET match_score = 77, match_reason = '可恢复的旧匹配结果'
            WHERE id = ?
            """,
            (task_id,),
        )
        connection.execute(
            """
            CREATE TABLE identity_professor_match_results (
                id INTEGER PRIMARY KEY,
                identity_id INTEGER NOT NULL,
                professor_id INTEGER NOT NULL,
                match_score INTEGER NOT NULL
            )
            """,
        )
        connection.execute(
            """
            INSERT INTO identity_professor_match_results (
                identity_id, professor_id, match_score
            ) VALUES (?, ?, 13)
            """,
            (identity_id, professor_id),
        )
        connection.commit()
        connection.close()

        self._run_alembic(legacy_env, "upgrade", "head")

        upgraded = sqlite3.connect(legacy_db_path)
        columns = {
            row[1]
            for row in upgraded.execute(
                "PRAGMA table_info('identity_professor_match_results')",
            ).fetchall()
        }
        row = upgraded.execute(
            """
            SELECT source_email_task_id, match_score, match_reason
            FROM identity_professor_match_results
            WHERE identity_id = ? AND professor_id = ?
            """,
            (identity_id, professor_id),
        ).fetchone()
        version = upgraded.execute(
            "SELECT version_num FROM alembic_version",
        ).fetchone()[0]
        upgraded.close()

        self.assertTrue(
            {
                "llm_profile_id",
                "primary_material_id",
                "source_email_task_id",
                "latest_analysis_run_id",
                "match_reason",
                "fit_points",
                "risk_points",
                "match_keywords",
                "analyzed_at",
                "created_at",
                "updated_at",
            }.issubset(columns),
        )
        self.assertEqual(row, (task_id, 77, "可恢复的旧匹配结果"))
        self.assertEqual(version, HEAD_REVISION)

    def test_match_and_batch_fallback_merge_upgrades_from_each_parent(self) -> None:
        parent_revisions = (
            "20260805_batch_draft_fallback",
            "20260805_identity_match_results",
        )
        for parent_revision in parent_revisions:
            with self.subTest(parent_revision=parent_revision):
                with tempfile.TemporaryDirectory() as legacy_dir:
                    legacy_db_path = (
                        Path(legacy_dir) / f"merge-from-{parent_revision}.db"
                    )
                    legacy_env = os.environ.copy()
                    legacy_env["DATABASE_URL"] = (
                        f"sqlite+aiosqlite:///{legacy_db_path.as_posix()}"
                    )

                    self._run_alembic(legacy_env, "upgrade", parent_revision)
                    self._run_alembic(legacy_env, "upgrade", "head")

                    upgraded = sqlite3.connect(legacy_db_path)
                    version = upgraded.execute(
                        "SELECT version_num FROM alembic_version",
                    ).fetchone()[0]
                    email_task_columns = {
                        row[1]
                        for row in upgraded.execute(
                            "PRAGMA table_info('email_tasks')",
                        ).fetchall()
                    }
                    table_names = {
                        row[0]
                        for row in upgraded.execute(
                            "SELECT name FROM sqlite_master WHERE type = 'table'",
                        ).fetchall()
                    }
                    upgraded.close()

                    self.assertEqual(version, HEAD_REVISION)
                    self.assertTrue(
                        {
                            "draft_generation_source",
                            "draft_fallback_reason",
                            "match_source_identity_id",
                        }.issubset(email_task_columns),
                    )
                    self.assertIn(
                        "identity_professor_match_results",
                        table_names,
                    )

    def _run_alembic(self, env: dict[str, str], *args: str) -> None:
        try:
            run_alembic_in_process(env, *args)
        except Exception as exc:
            self.fail(
                "Alembic command failed.\n"
                f"command: {' '.join(args)}\n"
                f"error: {type(exc).__name__}: {exc}",
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
                "identity_professor_match_results",
                "match_analysis_jobs",
                "match_analysis_job_items",
                "thinking_adaptation_cache",
                "llm_endpoint_adaptation_cache",
                "llm_structured_output_adaptation_cache",
            }.issubset(table_names),
        )
        self.assertNotIn("attachment_assets", table_names)

        thinking_cache_columns = self._get_columns("thinking_adaptation_cache")
        endpoint_cache_columns = self._get_columns("llm_endpoint_adaptation_cache")
        structured_cache_columns = self._get_columns(
            "llm_structured_output_adaptation_cache",
        )
        self.assertTrue(
            {
                "id",
                "api_base_url",
                "model_name",
                "learned_extra_body",
                "endpoint_kind",
                "probed_at",
                "created_at",
                "updated_at",
            }.issubset(thinking_cache_columns),
        )
        self.assertTrue(
            {
                "id",
                "api_base_url",
                "model_name",
                "learned_endpoint_kind",
                "probed_at",
                "created_at",
                "updated_at",
            }.issubset(endpoint_cache_columns),
        )
        self.assertTrue(
            {
                "id",
                "api_base_url",
                "model_name",
                "endpoint_kind",
                "probe_version",
                "learned_mode",
                "probed_at",
                "expires_at",
                "created_at",
                "updated_at",
            }.issubset(structured_cache_columns),
        )
        self.assertEqual(
            self._get_unique_index_columns("thinking_adaptation_cache"),
            ["api_base_url", "model_name", "endpoint_kind"],
        )
        self.assertTrue(
            self._is_column_not_null("thinking_adaptation_cache", "endpoint_kind"),
        )
        self.assertEqual(
            self._get_unique_index_columns("llm_endpoint_adaptation_cache"),
            ["api_base_url", "model_name"],
        )
        self.assertEqual(
            self._get_unique_index_columns("llm_structured_output_adaptation_cache"),
            ["api_base_url", "model_name", "endpoint_kind", "probe_version"],
        )
        self.assertEqual(
            self._get_index_columns(
                "thinking_adaptation_cache",
                "ix_thinking_adaptation_cache_model_name",
            ),
            ["model_name"],
        )
        self.assertEqual(
            self._get_index_columns(
                "llm_endpoint_adaptation_cache",
                "ix_llm_endpoint_adaptation_cache_model_name",
            ),
            ["model_name"],
        )
        self.assertEqual(
            self._get_index_columns(
                "llm_structured_output_adaptation_cache",
                "ix_llm_structured_output_adaptation_cache_model_name",
            ),
            ["model_name"],
        )

        identity_columns = self._get_columns("identity_profiles")
        batch_columns = self._get_columns("batch_tasks")
        task_columns = self._get_columns("email_tasks")
        material_columns = self._get_columns("identity_materials")
        professor_columns = self._get_columns("professors")
        log_columns = self._get_columns("email_logs")
        settings_columns = self._get_columns("app_settings")
        operation_log_columns = self._get_columns("operation_logs")
        match_run_columns = self._get_columns("match_analysis_runs")
        match_result_columns = self._get_columns("identity_professor_match_results")
        match_job_columns = self._get_columns("match_analysis_jobs")
        match_job_item_columns = self._get_columns("match_analysis_job_items")
        communication_group_columns = self._get_columns(
            "identity_communication_groups",
        )

        self.assertIn("current_primary_material_id", identity_columns)
        self.assertNotIn("resume_file_path", identity_columns)
        self.assertNotIn("resume_text", identity_columns)
        self.assertIn("primary_material_id", batch_columns)
        self.assertIn("selected_material_ids", batch_columns)
        self.assertIn("scheduled_dates", batch_columns)
        self.assertTrue(
            {
                "outreach_template_id",
                "outreach_template_name_snapshot",
                "outreach_template_snapshot_version",
                "outreach_generation_mode",
                "outreach_template_subject",
                "outreach_template_body_text",
                "outreach_template_body_html",
            }.issubset(batch_columns),
        )
        self.assertEqual(
            self._get_index_columns(
                "batch_tasks",
                "ix_batch_tasks_outreach_template_id",
            ),
            ["outreach_template_id"],
        )
        self.assertNotIn("selected_attachment_ids", batch_columns)
        self.assertIn("primary_material_id", task_columns)
        self.assertIn("match_source_identity_id", task_columns)
        self.assertIn("selected_material_ids", task_columns)
        self.assertIn("draft_generation_previous_status", task_columns)
        self.assertIn("draft_generation_source", task_columns)
        self.assertIn("draft_fallback_reason", task_columns)
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
                "identity_id",
                "professor_id",
                "llm_profile_id",
                "primary_material_id",
                "source_email_task_id",
                "latest_analysis_run_id",
                "match_score",
                "match_reason",
                "fit_points",
                "risk_points",
                "match_keywords",
                "analyzed_at",
            }.issubset(match_result_columns),
        )
        self.assertTrue(
            {"match_reason", "fit_points", "risk_points", "match_keywords"}.issubset(
                match_run_columns,
            ),
        )
        self.assertIn("match_source_identity_id", match_job_columns)
        self.assertIn("match_source_identity_id", communication_group_columns)
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
                "total_cached_tokens",
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
                "cached_tokens",
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
                "uq_match_analysis_runs_running_per_identity_professor",
            }.issubset(match_run_indexes),
        )
        match_result_indexes = {
            row[1]
            for row in self.connection.execute(
                "PRAGMA index_list('identity_professor_match_results')"
            ).fetchall()
        }
        self.assertTrue(
            {
                "ix_identity_professor_match_results_identity_id",
                "ix_identity_professor_match_results_professor_id",
                "ix_identity_professor_match_results_identity_updated",
            }.issubset(match_result_indexes),
        )
        self.assertEqual(
            self._get_unique_index_columns("identity_professor_match_results"),
            ["identity_id", "professor_id"],
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
                "ix_match_analysis_jobs_match_source_identity_id",
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

    def test_database_performance_indexes_support_hot_queries(self) -> None:
        self.assertEqual(
            self._get_index_columns("email_tasks", "ix_email_tasks_dispatch_ready"),
            ["approved_at", "created_at", "id"],
        )
        self.assertEqual(
            self._get_index_columns(
                "email_tasks",
                "ix_email_tasks_unstarted_generation_recovery",
            ),
            ["updated_at"],
        )
        self.assertEqual(
            self._get_index_columns(
                "email_tasks",
                "ix_email_tasks_started_generation_recovery",
            ),
            ["draft_generation_started_at"],
        )
        self.assertEqual(
            self._get_index_columns("email_tasks", "ix_email_tasks_batch_sent_at"),
            ["batch_task_id", "sent_at"],
        )
        self.assertEqual(
            self._get_index_columns(
                "match_analysis_jobs",
                "ix_match_analysis_jobs_status_deleted_created_id",
            ),
            ["status", "deleted_at", "created_at", "id"],
        )
        self.assertEqual(
            self._get_index_columns(
                "crawl_jobs",
                "ix_crawl_jobs_kind_deleted_created_id",
            ),
            ["job_kind", "deleted_at", "created_at", "id"],
        )

        plans = {
            "ix_email_tasks_dispatch_ready": (
                """
                SELECT email_tasks.id
                FROM email_tasks
                LEFT JOIN batch_tasks ON email_tasks.batch_task_id = batch_tasks.id
                WHERE (
                    email_tasks.status = ?
                    OR email_tasks.status = ?
                )
                  AND (
                    email_tasks.scheduled_at IS NULL
                    OR email_tasks.scheduled_at <= ?
                  )
                  AND (
                    batch_tasks.id IS NULL
                    OR (
                      batch_tasks.status = 'running'
                      AND batch_tasks.deleted_at IS NULL
                    )
                  )
                ORDER BY email_tasks.approved_at, email_tasks.created_at, email_tasks.id
                LIMIT ?
                """,
                ("approved", "scheduled", "2026-07-30 00:00:00", 10),
            ),
            "ix_email_tasks_unstarted_generation_recovery": (
                """
                SELECT id
                FROM email_tasks
                WHERE status = ?
                  AND draft_generation_started_at IS NULL
                  AND updated_at < ?
                """,
                ("generating_draft", "2026-07-30 00:00:00"),
            ),
            "ix_email_tasks_started_generation_recovery": (
                """
                SELECT id
                FROM email_tasks
                WHERE status = ?
                  AND draft_generation_started_at IS NOT NULL
                  AND draft_generation_started_at <= ?
                """,
                ("generating_draft", "2026-07-30 00:00:00"),
            ),
            "ix_email_tasks_batch_sent_at": (
                """
                SELECT count(id)
                FROM email_tasks
                WHERE batch_task_id = ?
                  AND (status = ? OR status = ?)
                  AND sent_at >= ?
                  AND sent_at < ?
                """,
                (
                    1,
                    "sent",
                    "reply_detected",
                    "2026-07-30 00:00:00",
                    "2026-07-31 00:00:00",
                ),
            ),
            "ix_match_analysis_jobs_status_deleted_created_id": (
                """
                SELECT id
                FROM match_analysis_jobs
                WHERE status = ? AND deleted_at IS NULL
                ORDER BY created_at, id
                LIMIT ?
                """,
                ("queued", 1),
            ),
            "ix_crawl_jobs_kind_deleted_created_id": (
                """
                SELECT id
                FROM crawl_jobs
                WHERE job_kind = ? AND deleted_at IS NULL
                ORDER BY created_at DESC, id DESC
                LIMIT ?
                """,
                ("faculty_crawl", 50),
            ),
        }
        for expected_index, (query, parameters) in plans.items():
            details = [
                row[3]
                for row in self.connection.execute(
                    f"EXPLAIN QUERY PLAN {query}",
                    parameters,
                ).fetchall()
            ]
            self.assertTrue(
                any(expected_index in detail for detail in details),
                f"{expected_index} not used by query plan: {details}",
            )

    def test_llm_endpoint_adaptation_upgrade_discards_old_cache_and_is_recoverable(self) -> None:
        legacy_db_path = Path(self.temp_dir.name) / "llm_endpoint_adaptation_upgrade.db"
        env = os.environ.copy()
        env["DATABASE_URL"] = f"sqlite+aiosqlite:///{legacy_db_path.as_posix()}"

        self._run_alembic(env, "upgrade", "20260709_professor_dashboard_indexes")
        connection = sqlite3.connect(legacy_db_path)
        try:
            connection.execute(
                """
                INSERT INTO thinking_adaptation_cache (
                    api_base_url, model_name, learned_extra_body
                )
                VALUES ('https://api.example.test/v1', 'test-model', '{"thinking": false}')
                """,
            )
            connection.commit()
        finally:
            connection.close()

        self._run_alembic(env, "upgrade", "head")

        connection = sqlite3.connect(legacy_db_path)
        try:
            self.assertEqual(
                connection.execute("SELECT COUNT(*) FROM thinking_adaptation_cache").fetchone()[0],
                0,
            )
            connection.execute(
                """
                INSERT INTO thinking_adaptation_cache (
                    api_base_url, model_name, learned_extra_body, endpoint_kind
                )
                VALUES (?, ?, NULL, ?), (?, ?, NULL, ?)
                """,
                (
                    "https://api.example.test/v1",
                    "test-model",
                    "chat_completions",
                    "https://api.example.test/v1",
                    "test-model",
                    "responses",
                ),
            )
            connection.execute(
                "UPDATE alembic_version SET version_num = '20260709_professor_dashboard_indexes'",
            )
            connection.commit()
        finally:
            connection.close()

        self._run_alembic(env, "upgrade", "head")

        connection = sqlite3.connect(legacy_db_path)
        try:
            self.assertEqual(
                connection.execute("SELECT COUNT(*) FROM thinking_adaptation_cache").fetchone()[0],
                0,
            )
            self.assertEqual(
                connection.execute("SELECT version_num FROM alembic_version").fetchone()[0],
                HEAD_REVISION,
            )
        finally:
            connection.close()

    def test_llm_endpoint_adaptation_downgrade_restores_legacy_thinking_cache(self) -> None:
        database_path = Path(self.temp_dir.name) / "llm_endpoint_adaptation_downgrade.db"
        env = os.environ.copy()
        env["DATABASE_URL"] = f"sqlite+aiosqlite:///{database_path.as_posix()}"

        self._run_alembic(env, "upgrade", "head")
        self._run_alembic(env, "downgrade", "20260709_professor_dashboard_indexes")

        connection = sqlite3.connect(database_path)
        try:
            table_names = {
                row[0]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'",
                ).fetchall()
            }
            self.assertNotIn("llm_endpoint_adaptation_cache", table_names)
            self.assertNotIn(
                "endpoint_kind",
                {row[1] for row in connection.execute("PRAGMA table_info('thinking_adaptation_cache')")},
            )
            unique_indexes = [
                row[1]
                for row in connection.execute(
                    "PRAGMA index_list('thinking_adaptation_cache')",
                ).fetchall()
                if row[2]
            ]
            self.assertEqual(len(unique_indexes), 1)
            self.assertEqual(
                [
                    row[2]
                    for row in connection.execute(
                        f"PRAGMA index_info('{unique_indexes[0]}')",
                    ).fetchall()
                ],
                ["api_base_url", "model_name"],
            )
        finally:
            connection.close()

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

        crawl_job_columns = self._get_columns("crawl_jobs")
        self.assertIn("current_run_id", crawl_job_columns)
        self.assertTrue(
            {"job_kind", "trigger_mode", "task_center_visible", "display_name"}.issubset(
                crawl_job_columns,
            ),
        )
        self.assertTrue(
            {
                "professor_id",
                "skip_reason",
                "enriched_fields",
                "started_at",
                "finished_at",
            }.issubset(self._get_columns("crawl_candidate_enrichment_tasks")),
        )
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
                "parent_url",
                "discovery_reason",
                "expansion_mode",
                "allow_expansion",
                "depth",
            }.issubset(self._get_columns("crawl_page_tasks")),
        )
        self.assertTrue(
            {
                "job_id",
                "attempt_number",
                "active_seconds",
                "app_version",
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

    def test_obsolete_runtime_version_is_removed_without_losing_crawl_jobs(self) -> None:
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
            columns = {
                row[1]
                for row in connection.execute("PRAGMA table_info(crawl_jobs)").fetchall()
            }
            settings_columns = {
                row[1]
                for row in connection.execute("PRAGMA table_info(app_settings)").fetchall()
            }
            job_count = connection.execute(
                "SELECT COUNT(*) FROM crawl_jobs WHERE university = ?",
                ("历史大学",),
            ).fetchone()[0]
        finally:
            connection.close()

        self.assertNotIn("runtime_version", columns)
        self.assertNotIn("crawler_agent_max_chunks_per_run", settings_columns)
        self.assertEqual(job_count, 1)

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
        try:
            run_alembic_in_process(env, *args)
        except Exception as exc:
            self.fail(
                "Alembic command failed.\n"
                f"command: {' '.join(args)}\n"
                f"error: {type(exc).__name__}: {exc}",
            )

    def _get_table_names(self) -> set[str]:
        rows = self.connection.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        return {row[0] for row in rows}


    def _get_columns(self, table_name: str) -> set[str]:
        rows = self.connection.execute(f"PRAGMA table_info('{table_name}')").fetchall()
        return {row[1] for row in rows}

    def _is_column_not_null(self, table_name: str, column_name: str) -> bool:
        rows = self.connection.execute(f"PRAGMA table_info('{table_name}')").fetchall()
        for row in rows:
            if row[1] == column_name:
                return bool(row[3])
        self.fail(f"{table_name}.{column_name} does not exist")

    def _get_index_columns(self, table_name: str, index_name: str) -> list[str]:
        index_names = {
            row[1]
            for row in self.connection.execute(f"PRAGMA index_list('{table_name}')").fetchall()
        }
        self.assertIn(index_name, index_names)
        rows = self.connection.execute(f"PRAGMA index_info('{index_name}')").fetchall()
        return [row[2] for row in rows]

    def _get_unique_index_columns(self, table_name: str) -> list[str]:
        unique_indexes = [
            row[1]
            for row in self.connection.execute(f"PRAGMA index_list('{table_name}')").fetchall()
            if row[2]
        ]
        self.assertEqual(len(unique_indexes), 1)
        rows = self.connection.execute(f"PRAGMA index_info('{unique_indexes[0]}')").fetchall()
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
