from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path

from app.core.schema_backup import create_schema_backup, prune_schema_backups

class SchemaBackupTests(unittest.TestCase):
    def test_creates_database_copy_and_metadata_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            db_path = root / "auto_email_sender.db"
            backup_dir = root / "backups" / "schema"
            connection = sqlite3.connect(db_path)
            try:
                connection.execute("CREATE TABLE sample (id INTEGER PRIMARY KEY)")
                connection.execute("INSERT INTO sample (id) VALUES (1)")
                connection.commit()
            finally:
                connection.close()

            result = create_schema_backup(
                database_path=db_path,
                backup_dir=backup_dir,
                app_version="2.3.0",
                source_schema_revision="04d66ff4c25b",
                target_schema_revision="d6e4b8c2a1f0",
            )

            self.assertTrue(result.database_backup_path.exists())
            self.assertTrue(result.metadata_path.exists())
            copied = sqlite3.connect(result.database_backup_path)
            try:
                self.assertEqual(copied.execute("SELECT id FROM sample").fetchone()[0], 1)
            finally:
                copied.close()
            metadata = json.loads(result.metadata_path.read_text(encoding="utf-8"))
            self.assertEqual(metadata["app_version"], "2.3.0")
            self.assertEqual(metadata["database_path"], str(db_path))
            self.assertEqual(metadata["reason"], "before_schema_migration")
            self.assertEqual(metadata["source_schema_revision"], "04d66ff4c25b")
            self.assertEqual(metadata["target_schema_revision"], "d6e4b8c2a1f0")
            self.assertIn("created_at", metadata)


    def test_creates_consistent_backup_while_source_connection_is_open(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            db_path = root / "auto_email_sender.db"
            backup_dir = root / "backups" / "schema"
            connection = sqlite3.connect(db_path)
            try:
                connection.execute("CREATE TABLE sample (id INTEGER PRIMARY KEY, name TEXT NOT NULL)")
                connection.execute("INSERT INTO sample (id, name) VALUES (1, 'committed')")
                connection.commit()
                connection.execute("INSERT INTO sample (id, name) VALUES (2, 'uncommitted')")

                result = create_schema_backup(
                    database_path=db_path,
                    backup_dir=backup_dir,
                    app_version="2.3.0",
                    source_schema_revision="04d66ff4c25b",
                    target_schema_revision="d6e4b8c2a1f0",
                )
            finally:
                connection.close()

            copied = sqlite3.connect(result.database_backup_path)
            try:
                rows = copied.execute("SELECT id, name FROM sample ORDER BY id").fetchall()
            finally:
                copied.close()

            self.assertEqual(rows, [(1, "committed")])

    def test_prunes_schema_backups_to_recent_five_pairs(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            backup_dir = Path(temp_dir) / "backups" / "schema"
            backup_dir.mkdir(parents=True)
            base_time = datetime(2026, 6, 5, tzinfo=UTC)
            for index in range(7):
                db_file = backup_dir / f"auto_email_sender.before-2.3.0.20260605-12000{index}.db"
                json_file = db_file.with_suffix(".json")
                db_file.write_text(f"db-{index}", encoding="utf-8")
                json_file.write_text(
                    json.dumps({"created_at": (base_time + timedelta(minutes=index)).isoformat()}),
                    encoding="utf-8",
                )

            prune_schema_backups(backup_dir, keep=5)

            remaining = sorted(path.name for path in backup_dir.glob("*.db"))
            self.assertEqual(len(remaining), 5)
            self.assertNotIn("auto_email_sender.before-2.3.0.20260605-120000.db", remaining)
            self.assertNotIn("auto_email_sender.before-2.3.0.20260605-120001.db", remaining)
            self.assertEqual(len(list(backup_dir.glob("*.json"))), 5)

if __name__ == "__main__":
    unittest.main()