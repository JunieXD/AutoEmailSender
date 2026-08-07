from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from test.migrated_database import create_migrated_sqlite_database


class MigratedDatabaseTests(unittest.TestCase):
    def test_reuses_migrated_template_for_multiple_databases(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            backend_dir = root / "backend"
            versions_dir = backend_dir / "alembic" / "versions"
            versions_dir.mkdir(parents=True)
            (versions_dir / "001_create_tables.py").write_text("# migration\n", encoding="utf-8")
            (backend_dir / "alembic.ini").write_text("[alembic]\n", encoding="utf-8")

            created_templates: list[Path] = []

            def fake_run(
                args: list[str],
                *,
                cwd: Path,
                env: dict[str, str],
                capture_output: bool,
                text: bool,
                check: bool,
            ) -> subprocess.CompletedProcess[str]:
                self.assertEqual(args[-3:-1], ["alembic", "upgrade"])
                self.assertEqual(cwd, backend_dir)
                template_url = env["DATABASE_URL"]
                self.assertTrue(template_url.startswith("sqlite+aiosqlite:///"))
                template_path = Path(template_url.removeprefix("sqlite+aiosqlite:///"))
                template_path.write_text(f"migrated:{args[-1]}", encoding="utf-8")
                created_templates.append(template_path)
                return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

            first_db = root / "first.db"
            second_db = root / "second.db"

            with patch("test.migrated_database.subprocess.run", side_effect=fake_run) as run:
                create_migrated_sqlite_database(
                    first_db,
                    backend_dir=backend_dir,
                    template_root=root / "templates",
                )
                first_db.write_text("test-local-change", encoding="utf-8")

                create_migrated_sqlite_database(
                    second_db,
                    backend_dir=backend_dir,
                    template_root=root / "templates",
                )

                self.assertEqual(run.call_count, 1)
                self.assertEqual(len(created_templates), 1)
                historical_db = root / "historical.db"
                create_migrated_sqlite_database(
                    historical_db,
                    revision="previous-revision",
                    backend_dir=backend_dir,
                    template_root=root / "templates",
                )

                self.assertEqual(run.call_count, 2)
                self.assertEqual(len(created_templates), 2)
            self.assertEqual(second_db.read_text(encoding="utf-8"), "migrated:head")
            self.assertEqual(first_db.read_text(encoding="utf-8"), "test-local-change")
            self.assertEqual(
                historical_db.read_text(encoding="utf-8"),
                "migrated:previous-revision",
            )



class SchemaDatabaseTests(unittest.TestCase):
    def test_reuses_schema_template_for_multiple_databases(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            first_db = root / "first.db"
            second_db = root / "second.db"
            template_root = root / "templates"

            from test import schema_database

            created_templates: list[Path] = []

            def fake_create_template(template_path: Path) -> None:
                template_path.parent.mkdir(parents=True, exist_ok=True)
                template_path.write_text("schema", encoding="utf-8")
                created_templates.append(template_path)

            with patch("test.schema_database._create_template_database", side_effect=fake_create_template):
                schema_database.create_schema_sqlite_database(first_db, template_root=template_root)
                first_db.write_text("test-local-change", encoding="utf-8")

                schema_database.create_schema_sqlite_database(second_db, template_root=template_root)

            self.assertEqual(len(created_templates), 1)
            self.assertEqual(second_db.read_text(encoding="utf-8"), "schema")
            self.assertEqual(first_db.read_text(encoding="utf-8"), "test-local-change")

if __name__ == "__main__":
    unittest.main()
