from __future__ import annotations

import asyncio
from contextlib import closing
import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from alembic import command
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.core.database import dispose_engine, get_engine, get_session_factory
from app.core.migrations import get_alembic_config, get_head_revision
from test.migrated_database import create_migrated_sqlite_database


PREVIOUS_REVISION = "20260803_crawl_run_app_version"


class RecentPapersMigrationTests(unittest.TestCase):
    def test_upgrade_caps_valid_arrays_and_starts_with_legacy_values(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            database_path = Path(temp_dir) / "recent-papers.db"
            env = os.environ.copy()
            env["DATABASE_URL"] = (
                f"sqlite+aiosqlite:///{database_path.as_posix()}"
            )
            env["ENABLE_BACKGROUND_WORKERS"] = "0"

            with patch.dict(os.environ, env, clear=True):
                get_settings.cache_clear()
                config = get_alembic_config()
                config.get_main_option("script_location")
                config.config_file_name = None
                create_migrated_sqlite_database(
                    database_path,
                    revision=PREVIOUS_REVISION,
                )

                papers = [f"Paper {index}" for index in range(1, 13)]
                with closing(sqlite3.connect(database_path)) as connection, connection:
                    connection.executemany(
                        "INSERT INTO professors (name, email, recent_papers) VALUES (?, ?, ?)",
                        [
                            ("有效数组", "valid@example.edu", json.dumps(papers)),
                            ("空值", "null@example.edu", None),
                            ("空数组", "empty@example.edu", "[]"),
                            ("异常历史值", "malformed@example.edu", "not-json"),
                        ],
                    )
                    job_id = connection.execute(
                        """
                        INSERT INTO crawl_jobs (university, school, start_url)
                        VALUES ('示例大学', '计算机学院', 'https://example.edu/faculty')
                        """
                    ).lastrowid
                    connection.executemany(
                        """
                        INSERT INTO crawl_candidates (job_id, name, recent_papers)
                        VALUES (?, ?, ?)
                        """,
                        [
                            (job_id, "有效候选", json.dumps(papers)),
                            (job_id, "空候选", None),
                            (job_id, "异常候选", "{broken"),
                        ],
                    )

                command.upgrade(config, "head")
                command.downgrade(config, PREVIOUS_REVISION)
                command.upgrade(config, "head")

                with closing(sqlite3.connect(database_path)) as connection, connection:
                    professor_rows = dict(
                        connection.execute(
                            "SELECT email, recent_papers FROM professors"
                        ).fetchall()
                    )
                    candidate_rows = dict(
                        connection.execute(
                            "SELECT name, recent_papers FROM crawl_candidates"
                        ).fetchall()
                    )
                    version = connection.execute(
                        "SELECT version_num FROM alembic_version"
                    ).fetchone()[0]

                self.assertEqual(
                    json.loads(professor_rows["valid@example.edu"]),
                    papers[:8],
                )
                self.assertIsNone(professor_rows["null@example.edu"])
                self.assertEqual(professor_rows["empty@example.edu"], "[]")
                self.assertEqual(
                    professor_rows["malformed@example.edu"],
                    "not-json",
                )
                self.assertEqual(json.loads(candidate_rows["有效候选"]), papers[:8])
                self.assertIsNone(candidate_rows["空候选"])
                self.assertEqual(candidate_rows["异常候选"], "{broken")
                self.assertEqual(version, get_head_revision(config))

                from main import create_app

                with TestClient(create_app()) as client:
                    self.assertEqual(client.get("/health").status_code, 200)

                if get_engine.cache_info().currsize:
                    asyncio.run(dispose_engine())
                get_session_factory.cache_clear()
                get_settings.cache_clear()
