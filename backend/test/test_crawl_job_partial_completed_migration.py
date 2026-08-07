from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from test.migrated_database import create_migrated_sqlite_database


BACKEND_DIR = Path(__file__).resolve().parents[1]
PRE_FIX_REVISION = "b9d1e3f4a6c7"


class CrawlJobPartialCompletedMigrationTests(unittest.TestCase):
    def test_completed_jobs_with_pending_candidates_are_reopened(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "migration.db"
            env = os.environ.copy()
            env["DATABASE_URL"] = f"sqlite+aiosqlite:///{db_path.as_posix()}"
            env["ENABLE_BACKGROUND_WORKERS"] = "0"

            create_migrated_sqlite_database(db_path, revision=PRE_FIX_REVISION)
            self._seed_legacy_crawl_jobs(db_path)

            self._run_alembic("upgrade", "head", env=env)

            connection = sqlite3.connect(db_path)
            try:
                job_rows = dict(
                    connection.execute(
                        "SELECT id, status FROM crawl_jobs ORDER BY id",
                    ).fetchall(),
                )
                run_rows = dict(
                    connection.execute(
                        "SELECT job_id, status FROM crawl_job_runs ORDER BY job_id",
                    ).fetchall(),
                )
            finally:
                connection.close()

        self.assertEqual(job_rows[1], "partially_completed")
        self.assertEqual(run_rows[1], "partially_completed")
        self.assertEqual(job_rows[2], "completed")
        self.assertEqual(run_rows[2], "completed")

    def _run_alembic(self, *args: str, env: dict[str, str]) -> None:
        result = subprocess.run(
            [sys.executable, "-m", "alembic", *args],
            cwd=BACKEND_DIR,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, msg=result.stderr)

    def _seed_legacy_crawl_jobs(self, db_path: Path) -> None:
        connection = sqlite3.connect(db_path)
        try:
            connection.execute(
                """
                INSERT INTO crawl_jobs (
                    id, university, school, start_url, status,
                    progress_current, progress_total
                )
                VALUES
                    (1, '示例大学', '计算机学院', 'https://example.edu/partial', 'completed', 1, 1),
                    (2, '示例大学', '信息学院', 'https://example.edu/done', 'completed', 1, 1)
                """,
            )
            connection.execute(
                """
                INSERT INTO crawl_job_runs (
                    id, job_id, attempt_number, status
                )
                VALUES
                    (101, 1, 1, 'completed'),
                    (102, 2, 1, 'completed')
                """,
            )
            connection.execute(
                "UPDATE crawl_jobs SET current_run_id = 101 WHERE id = 1",
            )
            connection.execute(
                "UPDATE crawl_jobs SET current_run_id = 102 WHERE id = 2",
            )
            connection.execute(
                """
                INSERT INTO crawl_candidates (
                    job_id, name, email, review_status, confidence
                )
                VALUES
                    (1, '待处理导师', NULL, 'pending', 0.8),
                    (1, '已导入导师', 'accepted@example.edu', 'accepted', 0.9),
                    (2, '完成导师', 'done@example.edu', 'accepted', 0.9)
                """,
            )
            connection.commit()
        finally:
            connection.close()


if __name__ == "__main__":
    unittest.main()
