from __future__ import annotations

import asyncio
import os
import tempfile
import unittest
from pathlib import Path


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
        self.temp_dir.cleanup()

    def test_sqlite_engine_applies_busy_timeout_and_wal(self) -> None:
        from app.core.config import get_settings
        from app.core.database import dispose_engine, get_engine

        db_path = Path(self.temp_dir.name) / "engine-wal.db"
        os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{db_path.as_posix()}"
        os.environ["SQLITE_BUSY_TIMEOUT_MS"] = "1234"
        os.environ["SQLITE_ENABLE_WAL"] = "1"
        get_settings.cache_clear()

        async def scenario() -> tuple[int, str]:
            engine = get_engine()
            try:
                async with engine.connect() as connection:
                    busy_timeout = (
                        await connection.exec_driver_sql("PRAGMA busy_timeout")
                    ).scalar()
                    journal_mode = (
                        await connection.exec_driver_sql("PRAGMA journal_mode")
                    ).scalar()
                return int(busy_timeout), str(journal_mode)
            finally:
                await dispose_engine()

        busy_timeout, journal_mode = asyncio.run(scenario())

        self.assertEqual(busy_timeout, 1234)
        self.assertEqual(journal_mode.lower(), "wal")

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


if __name__ == "__main__":
    unittest.main()
