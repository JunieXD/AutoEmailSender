from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, MagicMock, Mock, patch

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models import AppSetting, Base
from datetime import UTC, datetime, timedelta, timezone

from app.services.task_runtime import _has_future_scheduled_at
from app.services.runtime_manager import RuntimeManager, _run_match_analysis_worker_once



class TaskRuntimeTimeHandlingTests(unittest.TestCase):
    def test_has_future_scheduled_at_treats_naive_sqlite_timestamp_as_utc(self) -> None:
        shanghai = timezone(timedelta(hours=8))
        scheduled_at = datetime(2026, 5, 31, 1, 0, 0)
        now_utc = datetime(2026, 5, 31, 0, 30, 0, tzinfo=UTC)

        self.assertTrue(
            _has_future_scheduled_at(
                scheduled_at,
                now_utc,
                scheduled_dates=["2026-05-31"],
                local_timezone=shanghai,
            )
        )
class RuntimeManagerTests(unittest.IsolatedAsyncioTestCase):
    async def test_start_creates_multiple_crawler_workers_from_settings(self) -> None:
        session = object()
        session_context = MagicMock()
        session_context.__aenter__ = AsyncMock(return_value=session)
        session_context.__aexit__ = AsyncMock(return_value=None)
        session_factory = Mock(return_value=session_context)
        manager = RuntimeManager(session_factory)

        async def idle_loop() -> None:
            await asyncio.Event().wait()

        def build_idle_loop(*args: object, **kwargs: object):
            _ = args, kwargs
            return idle_loop()

        async def fake_load_worker_runtime_settings(session_arg: object) -> SimpleNamespace:
            self.assertIs(session_arg, session)
            return SimpleNamespace(
                crawler_worker_count=2,
                match_analysis_job_worker_count=1,
                match_analysis_job_interval_seconds=10,
            )

        with patch("app.services.runtime_manager.get_settings") as mocked_get_settings:
            mocked_get_settings.return_value = type(
                "SettingsStub",
                (),
                {
                    "dispatcher_interval_seconds": 30,
                    "imap_poll_interval_seconds": 60,
                    "crawler_worker_count": 2,
                    "match_analysis_job_worker_count": 1,
                    "match_analysis_job_interval_seconds": 10,
                },
            )()
            with patch(
                "app.services.runtime_manager._load_worker_runtime_settings",
                new=fake_load_worker_runtime_settings,
            ), patch.object(
                manager,
                "_loop",
                new=Mock(side_effect=build_idle_loop),
            ) as mocked_loop:
                await manager.start()

        worker_names = [call.args[0] for call in mocked_loop.call_args_list]
        self.assertEqual(worker_names.count("crawler-worker-1"), 1)
        self.assertEqual(worker_names.count("crawler-worker-2"), 1)
        self.assertIn("dispatcher", worker_names)
        self.assertIn("imap-poller", worker_names)

        await manager.stop()

    async def test_start_uses_runtime_settings_for_worker_counts_and_match_interval(self) -> None:
        session = object()
        session_context = MagicMock()
        session_context.__aenter__ = AsyncMock(return_value=session)
        session_context.__aexit__ = AsyncMock(return_value=None)
        session_factory = Mock(return_value=session_context)
        manager = RuntimeManager(session_factory)

        async def idle_loop() -> None:
            await asyncio.Event().wait()

        def build_idle_loop(*args: object, **kwargs: object):
            _ = args, kwargs
            return idle_loop()

        async def fake_load_worker_runtime_settings(session_arg: object) -> SimpleNamespace:
            self.assertIs(session_arg, session)
            return SimpleNamespace(
                crawler_worker_count=3,
                match_analysis_job_worker_count=2,
                match_analysis_job_interval_seconds=5,
            )

        with patch("app.services.runtime_manager.get_settings") as mocked_get_settings:
            mocked_get_settings.return_value = type(
                "SettingsStub",
                (),
                {
                    "dispatcher_interval_seconds": 30,
                    "imap_poll_interval_seconds": 60,
                    "crawler_worker_count": 1,
                    "match_analysis_job_worker_count": 1,
                    "match_analysis_job_interval_seconds": 10,
                },
            )()
            with patch(
                "app.services.runtime_manager._load_worker_runtime_settings",
                new=fake_load_worker_runtime_settings,
            ), patch.object(
                manager,
                "_loop",
                new=Mock(side_effect=build_idle_loop),
            ) as mocked_loop:
                await manager.start()

        worker_calls = {call.args[0]: call.args for call in mocked_loop.call_args_list}
        self.assertIn("crawler-worker-1", worker_calls)
        self.assertIn("crawler-worker-2", worker_calls)
        self.assertIn("crawler-worker-3", worker_calls)
        self.assertNotIn("crawler-worker-4", worker_calls)
        self.assertEqual(worker_calls["match-analysis-worker-1"][1], 5)
        self.assertEqual(worker_calls["match-analysis-worker-2"][1], 5)
        self.assertNotIn("match-analysis-worker-3", worker_calls)

        await manager.stop()

    async def test_start_falls_back_to_environment_worker_settings_when_runtime_settings_fail(self) -> None:
        session = object()
        session_context = MagicMock()
        session_context.__aenter__ = AsyncMock(return_value=session)
        session_context.__aexit__ = AsyncMock(return_value=None)
        session_factory = Mock(return_value=session_context)
        manager = RuntimeManager(session_factory)

        async def idle_loop() -> None:
            await asyncio.Event().wait()

        def build_idle_loop(*args: object, **kwargs: object):
            _ = args, kwargs
            return idle_loop()

        async def fail_load_worker_runtime_settings(session_arg: object) -> SimpleNamespace:
            self.assertIs(session_arg, session)
            raise RuntimeError("database unavailable")

        with patch("app.services.runtime_manager.get_settings") as mocked_get_settings:
            mocked_get_settings.return_value = type(
                "SettingsStub",
                (),
                {
                    "dispatcher_interval_seconds": 30,
                    "imap_poll_interval_seconds": 60,
                    "crawler_worker_count": 2,
                    "match_analysis_job_worker_count": 1,
                    "match_analysis_job_interval_seconds": 11,
                },
            )()
            with patch(
                "app.services.runtime_manager._load_worker_runtime_settings",
                new=fail_load_worker_runtime_settings,
            ), patch.object(
                manager,
                "_loop",
                new=Mock(side_effect=build_idle_loop),
            ) as mocked_loop, patch(
                "app.services.runtime_manager.logger.exception",
            ) as mocked_log_exception:
                await manager.start()

        worker_calls = {call.args[0]: call.args for call in mocked_loop.call_args_list}
        self.assertIn("crawler-worker-1", worker_calls)
        self.assertIn("crawler-worker-2", worker_calls)
        self.assertNotIn("crawler-worker-3", worker_calls)
        self.assertEqual(worker_calls["match-analysis-worker-1"][1], 11)
        mocked_log_exception.assert_called_once_with(
            "读取运行时 worker 设置失败，已回退到环境配置",
        )

        await manager.stop()

    async def test_worker_startup_settings_use_environment_when_app_settings_row_is_missing(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "runtime-manager.db"
            engine = create_async_engine(f"sqlite+aiosqlite:///{db_path.as_posix()}")
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)

            session_factory = async_sessionmaker(engine, expire_on_commit=False)
            manager = RuntimeManager(session_factory)
            settings_stub = type(
                "SettingsStub",
                (),
                {
                    "crawler_worker_count": 9,
                    "match_analysis_job_worker_count": 8,
                    "match_analysis_job_interval_seconds": 7,
                },
            )()

            try:
                resolved = await manager._resolve_worker_startup_settings(settings_stub)
                async with session_factory() as session:
                    app_settings_count = len(
                        list((await session.execute(select(AppSetting))).scalars()),
                    )
            finally:
                await engine.dispose()

        self.assertEqual(resolved.crawler_worker_count, 9)
        self.assertEqual(resolved.match_analysis_job_worker_count, 8)
        self.assertEqual(resolved.match_analysis_job_interval_seconds, 7)
        self.assertEqual(app_settings_count, 0)

    async def test_loop_immediately_continues_after_processing_work(self) -> None:
        session_factory = Mock()
        manager = RuntimeManager(session_factory)
        processed_results = [1, 0]
        worker_calls = 0
        sleep_calls: list[float] = []

        async def worker(session_factory_arg: object) -> int:
            nonlocal worker_calls
            self.assertIs(session_factory_arg, session_factory)
            worker_calls += 1
            if worker_calls == 2:
                manager._stopped.set()
            return processed_results.pop(0)

        async def fake_wait_for(awaitable: object, timeout: float) -> object:
            sleep_calls.append(timeout)
            manager._stopped.set()
            return await awaitable

        with patch("app.services.runtime_manager.asyncio.wait_for", new=fake_wait_for):
            await manager._loop("crawler-worker-1", 10, worker)

        self.assertEqual(worker_calls, 2)
        self.assertEqual(sleep_calls, [10])
    async def test_loop_waits_random_jitter_after_processing_crawler_work(self) -> None:
        session_factory = Mock()
        manager = RuntimeManager(session_factory)
        worker_calls = 0
        wait_calls: list[float] = []

        async def worker(session_factory_arg: object) -> int:
            nonlocal worker_calls
            self.assertIs(session_factory_arg, session_factory)
            worker_calls += 1
            return 1

        async def fake_wait_for(awaitable: object, timeout: float) -> object:
            wait_calls.append(timeout)
            manager._stopped.set()
            return await awaitable

        with patch("app.services.runtime_manager.random.uniform", return_value=7.5) as mocked_uniform, patch("app.services.runtime_manager.asyncio.wait_for", new=fake_wait_for):
            await manager._loop("crawler-worker-1", 10, worker, processed_jitter_seconds=(2, 10))

        self.assertEqual(worker_calls, 1)
        mocked_uniform.assert_called_once_with(2, 10)
        self.assertEqual(wait_calls, [7.5])

    async def test_start_configures_crawler_workers_with_claim_jitter(self) -> None:
        session = object()
        session_context = MagicMock()
        session_context.__aenter__ = AsyncMock(return_value=session)
        session_context.__aexit__ = AsyncMock(return_value=None)
        session_factory = Mock(return_value=session_context)
        manager = RuntimeManager(session_factory)

        async def idle_loop() -> None:
            await asyncio.Event().wait()

        def build_idle_loop(*args: object, **kwargs: object):
            _ = args, kwargs
            return idle_loop()

        async def fake_load_worker_runtime_settings(session_arg: object) -> SimpleNamespace:
            self.assertIs(session_arg, session)
            return SimpleNamespace(
                crawler_worker_count=1,
                match_analysis_job_worker_count=1,
                match_analysis_job_interval_seconds=10,
            )

        with patch("app.services.runtime_manager.get_settings") as mocked_get_settings:
            mocked_get_settings.return_value = type(
                "SettingsStub",
                (),
                {
                    "dispatcher_interval_seconds": 30,
                    "imap_poll_interval_seconds": 60,
                    "crawler_worker_count": 1,
                    "match_analysis_job_worker_count": 1,
                    "match_analysis_job_interval_seconds": 10,
                },
            )()
            with patch(
                "app.services.runtime_manager._load_worker_runtime_settings",
                new=fake_load_worker_runtime_settings,
            ), patch.object(
                manager,
                "_loop",
                new=Mock(side_effect=build_idle_loop),
            ) as mocked_loop:
                await manager.start()

        worker_calls = {call.args[0]: call for call in mocked_loop.call_args_list}
        self.assertEqual(worker_calls["crawler-worker-1"].kwargs["processed_jitter_seconds"], (2, 10))
        self.assertNotIn("processed_jitter_seconds", worker_calls["dispatcher"].kwargs)

        await manager.stop()
    async def test_worker_startup_settings_default_crawler_worker_count_is_eight(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "runtime-manager-defaults.db"
            engine = create_async_engine(f"sqlite+aiosqlite:///{db_path.as_posix()}")
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)

            session_factory = async_sessionmaker(engine, expire_on_commit=False)
            manager = RuntimeManager(session_factory)
            settings_stub = type(
                "SettingsStub",
                (),
                {
                    "match_analysis_job_worker_count": 1,
                    "match_analysis_job_interval_seconds": 10,
                },
            )()

            try:
                resolved = await manager._resolve_worker_startup_settings(settings_stub)
            finally:
                await engine.dispose()

        self.assertEqual(resolved.crawler_worker_count, 8)
    async def test_match_analysis_worker_uses_runtime_item_concurrency(self) -> None:
        session = object()
        session_context = MagicMock()
        session_context.__aenter__ = AsyncMock(return_value=session)
        session_context.__aexit__ = AsyncMock(return_value=None)
        session_factory = Mock(return_value=session_context)

        async def fake_get_runtime_settings(session: object) -> SimpleNamespace:
            _ = session
            return SimpleNamespace(match_analysis_job_item_concurrency=7)

        with patch(
            "app.services.runtime_manager.get_runtime_settings",
            new=fake_get_runtime_settings,
        ), patch(
            "app.services.runtime_manager.run_queued_match_analysis_jobs_once",
            new=AsyncMock(return_value=1),
        ) as mocked_run:
            processed = await _run_match_analysis_worker_once(session_factory)

        self.assertEqual(processed, 1)
        mocked_run.assert_awaited_once_with(session_factory, item_concurrency=7)


if __name__ == "__main__":
    unittest.main()
