from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models import Base
from app.modules.system.runtime_settings.service import get_or_create_app_settings


class SystemSettingsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "system_settings.db"
        self.engine = create_async_engine(
            f"sqlite+aiosqlite:///{self.db_path.as_posix()}",
            future=True,
        )
        self.session_factory = async_sessionmaker(
            bind=self.engine,
            autoflush=False,
            expire_on_commit=False,
        )
        self._run_async(self._create_schema())

    def tearDown(self) -> None:
        self._run_async(self.engine.dispose())
        self.temp_dir.cleanup()

    def test_get_or_create_app_settings_is_safe_under_concurrent_calls(self) -> None:
        async def run_twice() -> list[int]:
            return [
                settings.id
                for settings in await asyncio.gather(
                    self._load_settings(),
                    self._load_settings(),
                )
            ]

        ids = self._run_async(run_twice())

        self.assertEqual(ids, [1, 1])

    async def _create_schema(self) -> None:
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)

    async def _load_settings(self):
        async with self.session_factory() as session:
            return await get_or_create_app_settings(session)

    @staticmethod
    def _run_async(coro):
        return asyncio.run(coro)


if __name__ == "__main__":
    unittest.main()
