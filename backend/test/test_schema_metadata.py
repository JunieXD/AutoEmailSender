from __future__ import annotations

import os
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from app.core.schema_metadata import (
    CURRENT_SCHEMA_VERSION,
    DatabaseRequiresNewerAppError,
    check_database_compatibility,
    compare_versions,
    get_current_app_version,
    get_sqlite_database_path,
    read_app_metadata,
    update_app_metadata,
)

class SchemaMetadataTests(unittest.TestCase):
    def test_current_app_version_comes_from_environment(self) -> None:
        previous = os.environ.get("AUTO_EMAIL_SENDER_APP_VERSION")
        os.environ["AUTO_EMAIL_SENDER_APP_VERSION"] = "9.8.7"
        try:
            self.assertEqual(get_current_app_version(), "9.8.7")
        finally:
            if previous is None:
                os.environ.pop("AUTO_EMAIL_SENDER_APP_VERSION", None)
            else:
                os.environ["AUTO_EMAIL_SENDER_APP_VERSION"] = previous

    def test_current_app_version_falls_back_to_desktop_package_json(self) -> None:
        previous = os.environ.pop("AUTO_EMAIL_SENDER_APP_VERSION", None)
        try:
            package_json = Path(__file__).resolve().parents[2] / "desktop" / "package.json"
            expected_version = json.loads(package_json.read_text(encoding="utf-8"))["version"]
            self.assertEqual(get_current_app_version(), expected_version)
        finally:
            if previous is not None:
                os.environ["AUTO_EMAIL_SENDER_APP_VERSION"] = previous

    def test_resolves_sqlite_database_path_from_async_url(self) -> None:
        path = get_sqlite_database_path("sqlite+aiosqlite:///C:/data/auto_email_sender.db")

        self.assertEqual(path, Path("C:/data/auto_email_sender.db"))

    def test_returns_none_for_non_sqlite_database_url(self) -> None:
        self.assertIsNone(get_sqlite_database_path("postgresql://example/db"))

    def test_reads_empty_metadata_when_table_is_missing(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "metadata.db"
            connection = sqlite3.connect(db_path)
            try:
                self.assertEqual(read_app_metadata(connection), {})
            finally:
                connection.close()

    def test_writes_and_reads_app_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "metadata.db"
            connection = sqlite3.connect(db_path)
            try:
                update_app_metadata(
                    connection,
                    app_version="2.3.0",
                    schema_revision="d6e4b8c2a1f0",
                )
                metadata = read_app_metadata(connection)
            finally:
                connection.close()

        self.assertEqual(metadata["schema_version"], str(CURRENT_SCHEMA_VERSION))
        self.assertEqual(metadata["schema_revision"], "d6e4b8c2a1f0")
        self.assertEqual(metadata["schema_updated_by_app_version"], "2.3.0")
        self.assertEqual(metadata["minimum_supported_app_version"], get_current_app_version())
        self.assertIn("schema_updated_at", metadata)

    def test_allows_missing_metadata_for_legacy_database(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "legacy.db"
            connection = sqlite3.connect(db_path)
            try:
                connection.execute("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)")
                check_database_compatibility(connection, current_app_version="2.3.0")
            finally:
                connection.close()

    def test_rejects_database_that_requires_newer_app(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "future.db"
            backup_dir = Path(temp_dir) / "backups" / "schema"
            connection = sqlite3.connect(db_path)
            try:
                connection.execute("CREATE TABLE app_metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
                connection.execute(
                    "INSERT INTO app_metadata (key, value) VALUES (?, ?)",
                    ("minimum_supported_app_version", "2.4.0"),
                )
                connection.commit()

                with self.assertRaises(DatabaseRequiresNewerAppError) as context:
                    check_database_compatibility(
                        connection,
                        current_app_version="2.3.0",
                        backup_directory=backup_dir,
                    )
            finally:
                connection.close()

        self.assertEqual(context.exception.current_app_version, "2.3.0")
        self.assertEqual(context.exception.minimum_supported_app_version, "2.4.0")
        self.assertEqual(context.exception.backup_directory, backup_dir)
        self.assertEqual(context.exception.code, "DATABASE_REQUIRES_NEWER_APP")
        self.assertEqual(context.exception.to_payload()["minimum_supported_app_version"], "2.4.0")

    def test_compares_semver_like_versions(self) -> None:
        self.assertLess(compare_versions("2.3.0", "2.4.0"), 0)
        self.assertEqual(compare_versions("v2.3.0", "2.3.0"), 0)
        self.assertGreater(compare_versions("2.10.0", "2.9.9"), 0)
        self.assertEqual(compare_versions("2.3.0-beta", "2.3.0"), 0)

if __name__ == "__main__":
    unittest.main()
