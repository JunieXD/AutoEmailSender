from __future__ import annotations

import os
import sqlite3
import tempfile
import unittest
from pathlib import Path


class SQLiteDiagnosticsTests(unittest.TestCase):
    def test_detects_lock_error_in_context_when_cause_is_non_lock(self) -> None:
        from app.core.sqlite_diagnostics import is_sqlite_database_lock_error

        try:
            raise sqlite3.OperationalError("database is locked")
        except sqlite3.OperationalError:
            try:
                raise RuntimeError("outer wrapper") from ValueError("non-lock cause")
            except RuntimeError as exc:
                self.assertTrue(is_sqlite_database_lock_error(exc))

    def test_bad_exception_string_does_not_break_lock_detection(self) -> None:
        from app.core.sqlite_diagnostics import is_sqlite_database_lock_error

        class BadStringError(Exception):
            def __str__(self) -> str:
                raise RuntimeError("broken __str__")

        lock_error = sqlite3.OperationalError("database is locked")
        bad_error = BadStringError()
        bad_error.__context__ = lock_error

        self.assertTrue(is_sqlite_database_lock_error(bad_error))

    def test_safe_exception_message_handles_nested_bad_string(self) -> None:
        from app.core.error_formatting import safe_exception_message

        class NestedBadStringError(Exception):
            def __str__(self) -> str:
                raise RuntimeError("nested broken __str__")

        class BadStringError(Exception):
            def __str__(self) -> str:
                raise NestedBadStringError()

        message = safe_exception_message(BadStringError())

        self.assertIn("BadStringError", message)
        self.assertIn("NestedBadStringError", message)

    def test_detects_real_sqlite_database_lock_error(self) -> None:
        from app.core.sqlite_diagnostics import is_sqlite_database_lock_error

        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "locked.db"
            holder = sqlite3.connect(db_path, timeout=0.1, isolation_level=None)
            contender = sqlite3.connect(db_path, timeout=0.1)
            try:
                holder.execute("CREATE TABLE items (id INTEGER PRIMARY KEY)")
                holder.execute("BEGIN EXCLUSIVE")
                holder.execute("INSERT INTO items DEFAULT VALUES")

                with self.assertRaises(sqlite3.OperationalError) as ctx:
                    contender.execute("INSERT INTO items DEFAULT VALUES")

                self.assertTrue(is_sqlite_database_lock_error(ctx.exception))
            finally:
                holder.rollback()
                holder.close()
                contender.close()

    def test_user_message_hides_raw_sql_and_parameters(self) -> None:
        from app.core.sqlite_diagnostics import sqlite_lock_user_message

        lock_error = sqlite3.OperationalError(
            "database is locked [SQL: INSERT INTO secret_table VALUES ('secret')]"
        )

        message = sqlite_lock_user_message(lock_error)

        self.assertIsNotNone(message)
        self.assertIn("本地数据库正忙", message)
        self.assertNotIn("secret_table", message)
        self.assertNotIn("secret", message)

    def test_backend_error_log_marks_sqlite_lock_diagnostics(self) -> None:
        from app.core.backend_error_logging import write_backend_error_log
        from app.core.config import get_settings

        with tempfile.TemporaryDirectory() as temp_dir:
            get_settings.cache_clear()
            os.environ["AUTO_EMAIL_SENDER_DATA_DIR"] = temp_dir
            try:
                try:
                    raise sqlite3.OperationalError("database is locked")
                except sqlite3.OperationalError as exc:
                    write_backend_error_log(
                        request_id="lock-request",
                        method="POST",
                        path="/api/test",
                        exc=exc,
                    )
            finally:
                os.environ.pop("AUTO_EMAIL_SENDER_DATA_DIR", None)
                get_settings.cache_clear()

            log_text = (Path(temp_dir) / "logs" / "backend-errors.log").read_text(
                encoding="utf-8",
            )

        self.assertIn("diagnostic=sqlite_database_locked", log_text)
        self.assertIn("database_lock=1", log_text)
        self.assertIn("request_id=lock-request POST /api/test", log_text)


if __name__ == "__main__":
    unittest.main()
