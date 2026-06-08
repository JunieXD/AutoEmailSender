from __future__ import annotations

import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.core.schema_metadata import (
    DatabaseRequiresNewerAppError,
    get_current_app_version,
    read_app_metadata,
)

class MigrationRuntimeTests(unittest.TestCase):
    def tearDown(self) -> None:
        from app.core.config import get_settings

        get_settings.cache_clear()
        os.environ.pop("DATABASE_URL", None)
        os.environ.pop("AUTO_EMAIL_SENDER_DATA_DIR", None)

    def test_run_migrations_to_head_writes_app_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "runtime.db"
            with patch.dict(os.environ, {"DATABASE_URL": f"sqlite+aiosqlite:///{db_path.as_posix()}"}):
                from app.core.config import get_settings
                from app.core.migrations import run_migrations_to_head

                get_settings.cache_clear()
                run_migrations_to_head()

            connection = sqlite3.connect(db_path)
            try:
                metadata = read_app_metadata(connection)
            finally:
                connection.close()

        current_app_version = get_current_app_version()
        self.assertEqual(metadata["minimum_supported_app_version"], current_app_version)
        self.assertEqual(metadata["schema_updated_by_app_version"], current_app_version)
        self.assertIn("schema_revision", metadata)
        self.assertIn("schema_updated_at", metadata)

    def test_existing_database_is_backed_up_before_migration(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            db_path = root / "auto_email_sender.db"
            data_dir = root / "data"
            connection = sqlite3.connect(db_path)
            try:
                connection.execute("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)")
                connection.execute("INSERT INTO alembic_version (version_num) VALUES ('04d66ff4c25b')")
                connection.commit()
            finally:
                connection.close()

            with patch.dict(os.environ, {
                "DATABASE_URL": f"sqlite+aiosqlite:///{db_path.as_posix()}",
                "AUTO_EMAIL_SENDER_DATA_DIR": str(data_dir),
            }):
                from app.core.config import get_settings
                import app.core.migrations as migrations

                get_settings.cache_clear()
                with patch.object(migrations.command, "upgrade"):
                    migrations.run_migrations_to_head()

            backups = list((data_dir / "backups" / "schema").glob("*.db"))
            metadata_files = list((data_dir / "backups" / "schema").glob("*.json"))
            self.assertEqual(len(backups), 1)
            self.assertEqual(len(metadata_files), 1)

    def test_current_schema_does_not_raise_minimum_supported_version(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            db_path = root / "auto_email_sender.db"
            connection = sqlite3.connect(db_path)
            try:
                connection.execute("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)")
                connection.execute("INSERT INTO alembic_version (version_num) VALUES ('d6e4b8c2a1f0')")
                connection.execute("CREATE TABLE app_metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
                connection.executemany(
                    "INSERT INTO app_metadata (key, value) VALUES (?, ?)",
                    [
                        ("minimum_supported_app_version", "2.3.0"),
                        ("schema_updated_by_app_version", "2.3.0"),
                        ("schema_revision", "d6e4b8c2a1f0"),
                    ],
                )
                connection.commit()
            finally:
                connection.close()

            with patch.dict(os.environ, {
                "DATABASE_URL": f"sqlite+aiosqlite:///{db_path.as_posix()}",
                "AUTO_EMAIL_SENDER_APP_VERSION": "2.4.0",
            }):
                from app.core.config import get_settings
                import app.core.migrations as migrations

                get_settings.cache_clear()
                with patch.object(migrations.command, "upgrade"):
                    migrations.run_migrations_to_head()

            connection = sqlite3.connect(db_path)
            try:
                metadata = read_app_metadata(connection)
            finally:
                connection.close()

        self.assertEqual(metadata["minimum_supported_app_version"], "2.4.0")
        self.assertEqual(metadata["schema_updated_by_app_version"], "2.4.0")

    def test_backup_failure_prevents_alembic_upgrade(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            db_path = root / "auto_email_sender.db"
            connection = sqlite3.connect(db_path)
            try:
                connection.execute("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)")
                connection.commit()
            finally:
                connection.close()

            with patch.dict(os.environ, {"DATABASE_URL": f"sqlite+aiosqlite:///{db_path.as_posix()}"}):
                from app.core.config import get_settings
                import app.core.migrations as migrations

                get_settings.cache_clear()
                with (
                    patch.object(migrations, "create_schema_backup", side_effect=OSError("copy failed")),
                    patch.object(migrations.command, "upgrade") as upgrade,
                ):
                    with self.assertRaises(OSError):
                        migrations.run_migrations_to_head()

            upgrade.assert_not_called()

    def test_future_database_version_prevents_alembic_upgrade(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            db_path = root / "auto_email_sender.db"
            connection = sqlite3.connect(db_path)
            try:
                connection.execute("CREATE TABLE app_metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
                connection.execute(
                    "INSERT INTO app_metadata (key, value) VALUES (?, ?)",
                    ("minimum_supported_app_version", "9.9.9"),
                )
                connection.commit()
            finally:
                connection.close()

            with patch.dict(os.environ, {"DATABASE_URL": f"sqlite+aiosqlite:///{db_path.as_posix()}"}):
                from app.core.config import get_settings
                import app.core.migrations as migrations

                get_settings.cache_clear()
                with patch.object(migrations.command, "upgrade") as upgrade:
                    with self.assertRaises(DatabaseRequiresNewerAppError):
                        migrations.run_migrations_to_head()

            upgrade.assert_not_called()

if __name__ == "__main__":
    unittest.main()
