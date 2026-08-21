from __future__ import annotations

import asyncio
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from sqlalchemy import event


class DatabaseEngineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        os.environ["ENABLE_BACKGROUND_WORKERS"] = "0"

    def tearDown(self) -> None:
        from app.core.config import get_settings
        from app.core.database import dispose_engine, get_engine, get_session_factory

        if get_engine.cache_info().currsize:
            asyncio.run(dispose_engine())
        get_session_factory.cache_clear()
        get_settings.cache_clear()
        os.environ.pop("DATABASE_URL", None)
        os.environ.pop("ENABLE_BACKGROUND_WORKERS", None)
        os.environ.pop("SQLITE_BUSY_TIMEOUT_MS", None)
        os.environ.pop("SQLITE_ENABLE_WAL", None)
        os.environ.pop("SQLITE_CACHE_SIZE_MIB", None)
        os.environ.pop("SQLITE_MMAP_SIZE_MIB", None)
        os.environ.pop("SQLITE_INCREMENTAL_VACUUM_PAGES", None)
        self.temp_dir.cleanup()

    def test_sqlite_engine_applies_busy_timeout_and_wal(self) -> None:
        from app.core.config import get_settings
        from app.core.database import dispose_engine, get_engine

        db_path = Path(self.temp_dir.name) / "engine-wal.db"
        os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{db_path.as_posix()}"
        os.environ["SQLITE_BUSY_TIMEOUT_MS"] = "1234"
        os.environ["SQLITE_ENABLE_WAL"] = "1"
        get_settings.cache_clear()

        async def scenario() -> tuple[int, str, int, int, int, int]:
            engine = get_engine()
            try:
                async with engine.connect() as connection:
                    busy_timeout = (
                        await connection.exec_driver_sql("PRAGMA busy_timeout")
                    ).scalar()
                    journal_mode = (
                        await connection.exec_driver_sql("PRAGMA journal_mode")
                    ).scalar()
                    foreign_keys = (
                        await connection.exec_driver_sql("PRAGMA foreign_keys")
                    ).scalar()
                    synchronous = (
                        await connection.exec_driver_sql("PRAGMA synchronous")
                    ).scalar()
                    cache_size = (
                        await connection.exec_driver_sql("PRAGMA cache_size")
                    ).scalar()
                    temp_store = (
                        await connection.exec_driver_sql("PRAGMA temp_store")
                    ).scalar()
                return (
                    int(busy_timeout),
                    str(journal_mode),
                    int(foreign_keys),
                    int(synchronous),
                    int(cache_size),
                    int(temp_store),
                )
            finally:
                await dispose_engine()

        (
            busy_timeout,
            journal_mode,
            foreign_keys,
            synchronous,
            cache_size,
            temp_store,
        ) = asyncio.run(scenario())

        self.assertEqual(busy_timeout, 1234)
        self.assertEqual(journal_mode.lower(), "wal")
        self.assertEqual(foreign_keys, 1)
        self.assertEqual(synchronous, 1)  # NORMAL
        self.assertEqual(cache_size, -(64 * 1024))
        self.assertEqual(temp_store, 2)  # MEMORY

    def test_sqlite_engine_can_disable_wal(self) -> None:
        from app.core.config import get_settings
        from app.core.database import dispose_engine, get_engine

        db_path = Path(self.temp_dir.name) / "engine-no-wal.db"
        os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{db_path.as_posix()}"
        os.environ["SQLITE_ENABLE_WAL"] = "0"
        get_settings.cache_clear()

        async def scenario() -> str:
            engine = get_engine()
            try:
                async with engine.connect() as connection:
                    journal_mode = (
                        await connection.exec_driver_sql("PRAGMA journal_mode")
                    ).scalar()
                return str(journal_mode)
            finally:
                await dispose_engine()

        journal_mode = asyncio.run(scenario())

        self.assertNotEqual(journal_mode.lower(), "wal")

    def test_sqlite_engine_disable_wal_reverts_existing_wal_database(self) -> None:
        from app.core.config import get_settings
        from app.core.database import dispose_engine, get_engine

        db_path = Path(self.temp_dir.name) / "engine-revert-wal.db"
        os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{db_path.as_posix()}"

        async def current_journal_mode(*, wal_enabled: bool) -> str:
            os.environ["SQLITE_ENABLE_WAL"] = "1" if wal_enabled else "0"
            get_settings.cache_clear()
            engine = get_engine()
            try:
                async with engine.connect() as connection:
                    journal_mode = (
                        await connection.exec_driver_sql("PRAGMA journal_mode")
                    ).scalar()
                return str(journal_mode)
            finally:
                await dispose_engine()

        enabled_mode = asyncio.run(current_journal_mode(wal_enabled=True))
        disabled_mode = asyncio.run(current_journal_mode(wal_enabled=False))

        self.assertEqual(enabled_mode.lower(), "wal")
        self.assertNotEqual(disabled_mode.lower(), "wal")

    def test_sqlite_online_maintenance_uses_session_bind_and_runs_all_pragmas(
        self,
    ) -> None:
        from app.core.config import get_settings
        from app.core.database import dispose_engine, get_engine, get_session_factory
        from app.core.sqlite_maintenance import run_sqlite_maintenance_once

        db_path = Path(self.temp_dir.name) / "engine-maintenance.db"
        os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{db_path.as_posix()}"
        get_settings.cache_clear()
        statements: list[str] = []

        async def scenario() -> int:
            engine = get_engine()

            @event.listens_for(engine.sync_engine, "before_cursor_execute")
            def capture_statement(
                _connection,
                _cursor,
                statement,
                _parameters,
                _context,
                _executemany,
            ) -> None:  # type: ignore[no-untyped-def]
                statements.append(" ".join(str(statement).split()))

            try:
                with patch(
                    "app.core.sqlite_maintenance.get_settings",
                    return_value=SimpleNamespace(sqlite_incremental_vacuum_pages=37),
                ):
                    return await run_sqlite_maintenance_once(get_session_factory())
            finally:
                await dispose_engine()

        processed = asyncio.run(scenario())

        self.assertEqual(processed, 0)
        self.assertIn("PRAGMA optimize", statements)
        self.assertIn("PRAGMA wal_checkpoint(PASSIVE)", statements)
        self.assertIn("PRAGMA incremental_vacuum(37)", statements)


if __name__ == "__main__":
    unittest.main()
