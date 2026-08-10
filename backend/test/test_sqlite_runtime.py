from __future__ import annotations

import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.core.config import get_settings
from app.core.sqlite_runtime import (
    configure_sqlite_runtime,
    require_sqlite_runtime_ready,
)


class SQLiteRuntimeTests(unittest.TestCase):
    def tearDown(self) -> None:
        get_settings.cache_clear()

    def test_api_configures_wal_before_worker_verifies_runtime(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            database_path = Path(temp_dir) / "runtime.db"
            self._create_database(database_path)

            with self._settings(database_path):
                configure_sqlite_runtime()
                require_sqlite_runtime_ready()

            connection = sqlite3.connect(database_path)
            try:
                journal_mode = str(
                    connection.execute("PRAGMA journal_mode").fetchone()[0]
                )
                integrity = str(
                    connection.execute("PRAGMA integrity_check").fetchone()[0]
                )
            finally:
                connection.close()

        self.assertEqual(journal_mode.lower(), "wal")
        self.assertEqual(integrity, "ok")

    def test_worker_rejects_database_that_is_not_in_wal_mode(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            database_path = Path(temp_dir) / "runtime.db"
            self._create_database(database_path)

            with self._settings(database_path):
                with self.assertRaisesRegex(RuntimeError, "journal_mode=delete"):
                    require_sqlite_runtime_ready()

    def test_runtime_can_explicitly_disable_wal_for_non_split_diagnostics(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            database_path = Path(temp_dir) / "runtime.db"
            self._create_database(database_path)

            with self._settings(database_path, wal_enabled=False):
                configure_sqlite_runtime()
                require_sqlite_runtime_ready()

    def test_runtime_supports_non_ascii_and_space_in_database_path(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            database_path = Path(temp_dir) / "中文 data" / "runtime.db"
            database_path.parent.mkdir(parents=True)
            self._create_database(database_path)

            with self._settings(database_path):
                configure_sqlite_runtime()
                require_sqlite_runtime_ready()

    @staticmethod
    def _create_database(database_path: Path) -> None:
        connection = sqlite3.connect(database_path)
        try:
            connection.execute("CREATE TABLE marker (value TEXT NOT NULL)")
            connection.execute("INSERT INTO marker (value) VALUES ('preserved')")
            connection.commit()
        finally:
            connection.close()

    @staticmethod
    def _settings(database_path: Path, *, wal_enabled: bool = True):
        get_settings.cache_clear()
        return patch.dict(
            os.environ,
            {
                "DATABASE_URL": (
                    f"sqlite+aiosqlite:///{database_path.as_posix()}"
                ),
                "AUTO_EMAIL_SENDER_DATA_DIR": str(database_path.parent),
                "SQLITE_ENABLE_WAL": "1" if wal_enabled else "0",
            },
        )


if __name__ == "__main__":
    unittest.main()
