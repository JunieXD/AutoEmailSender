from __future__ import annotations

import asyncio
import tempfile
import unittest
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, Mock, patch

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models import AppSetting, Base
from app.modules.workspace.tasks.delivery import _has_future_scheduled_at
from app.services.runtime_manager import (
    CRAWLER_WORK_ITEM_WORKER_COUNT,
    RuntimeManager,
    SQLITE_MAINTENANCE_FAILURE_RETRY_SECONDS,
    _run_match_analysis_worker_once,
)


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
    async def test_crawler_worker_owner_changes_with_worker_generation(self) -> None:
        session_factory = Mock()
        first = RuntimeManager(
            session_factory,
            runtime_id="runtime-1",
            worker_generation="generation-1",
        )
        replacement = RuntimeManager(
            session_factory,
            runtime_id="runtime-1",
            worker_generation="generation-2",
        )

        self.assertEqual(
            first._crawler_worker_id(1),
            "crawler-worker-1:runtime-1:generation-1",
        )
        self.assertNotEqual(
            first._crawler_worker_id(1),
            replacement._crawler_worker_id(1),
        )

    async def test_start_creates_fixed_crawler_work_item_pool(self) -> None:
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
        for index in range(1, CRAWLER_WORK_ITEM_WORKER_COUNT + 1):
            self.assertEqual(worker_names.count(f"crawler-worker-{index}"), 1)
        self.assertIn("dispatcher", worker_names)
        self.assertIn("sqlite-maintenance", worker_names)
        self.assertIn("imap-incremental-poller", worker_names)
        self.assertIn("imap-history-poller", worker_names)

        sqlite_maintenance_call = next(
            call
            for call in mocked_loop.call_args_list
            if call.args[0] == "sqlite-maintenance"
        )
        self.assertEqual(
            sqlite_maintenance_call.kwargs["failure_retry_seconds"],
            SQLITE_MAINTENANCE_FAILURE_RETRY_SECONDS,
        )

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
        for index in range(1, CRAWLER_WORK_ITEM_WORKER_COUNT + 1):
            self.assertIn(f"crawler-worker-{index}", worker_calls)
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
        for index in range(1, CRAWLER_WORK_ITEM_WORKER_COUNT + 1):
            self.assertIn(f"crawler-worker-{index}", worker_calls)
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

    async def test_loop_health_exposes_sanitized_failure_and_recovers(self) -> None:
        session_factory = Mock()
        manager = RuntimeManager(session_factory)
        allow_success = asyncio.Event()
        worker_calls = 0

        async def worker(session_factory_arg: object) -> int:
            nonlocal worker_calls
            self.assertIs(session_factory_arg, session_factory)
            worker_calls += 1
            if worker_calls == 1:
                raise RuntimeError(
                    "poll failed password=super-secret token=private-token"
                )
            await allow_success.wait()
            manager._stopped.set()
            return 0

        loop_task = asyncio.create_task(
            manager._loop("imap-incremental-poller", 0.01, worker)
        )
        try:
            async with asyncio.timeout(1):
                while not manager.is_degraded():
                    await asyncio.sleep(0)

            failed = manager.get_health_snapshot()["imap-incremental-poller"]
            self.assertEqual(failed["consecutive_failures"], 1)
            self.assertIsNotNone(failed["last_started_at"])
            self.assertIsNotNone(failed["last_failed_at"])
            self.assertIsNone(failed["last_succeeded_at"])
            self.assertIn("password=[REDACTED]", str(failed["error"]))
            self.assertIn("token=[REDACTED]", str(failed["error"]))
            self.assertNotIn("super-secret", str(failed["error"]))
            self.assertNotIn("private-token", str(failed["error"]))

            allow_success.set()
            await loop_task
        finally:
            allow_success.set()
            if not loop_task.done():
                manager._stopped.set()
                await loop_task

        recovered = manager.get_health_snapshot()["imap-incremental-poller"]
        self.assertEqual(recovered["consecutive_failures"], 0)
        self.assertIsNotNone(recovered["last_succeeded_at"])
        self.assertIsNone(recovered["error"])
        self.assertFalse(manager.is_degraded())

    async def test_loop_uses_bounded_failure_retry_before_normal_interval(self) -> None:
        session_factory = Mock()
        manager = RuntimeManager(session_factory)
        worker_calls = 0
        wait_calls: list[float] = []

        async def worker(session_factory_arg: object) -> int:
            nonlocal worker_calls
            self.assertIs(session_factory_arg, session_factory)
            worker_calls += 1
            if worker_calls == 1:
                raise RuntimeError("database is locked")
            manager._stopped.set()
            return 0

        async def fake_wait_for(awaitable: object, timeout: float) -> object:
            wait_calls.append(timeout)
            if len(wait_calls) == 1:
                awaitable.close()
                raise TimeoutError
            return await awaitable

        with patch(
            "app.services.runtime_manager.asyncio.wait_for",
            new=fake_wait_for,
        ), patch(
            "app.services.runtime_manager.write_backend_worker_error_log",
        ):
            await manager._loop(
                "sqlite-maintenance",
                21_600,
                worker,
                failure_retry_seconds=5,
            )

        self.assertEqual(worker_calls, 2)
        self.assertEqual(wait_calls, [5, 21_600])
        self.assertFalse(manager.is_degraded())

    async def test_stop_allows_in_flight_loop_to_finish_within_grace(self) -> None:
        manager = RuntimeManager(Mock())
        started = asyncio.Event()
        release = asyncio.Event()
        canceled = False

        async def in_flight() -> None:
            nonlocal canceled
            started.set()
            try:
                await release.wait()
            except asyncio.CancelledError:
                canceled = True
                raise

        task = asyncio.create_task(in_flight())
        manager._tasks = [task]
        await started.wait()
        stop_task = asyncio.create_task(manager.stop(grace_seconds=1))
        await asyncio.sleep(0)

        self.assertTrue(manager._stopped.is_set())
        self.assertFalse(task.cancelled())
        self.assertFalse(stop_task.done())

        release.set()
        await stop_task
        self.assertFalse(canceled)
        self.assertTrue(task.done())
        self.assertEqual(manager._tasks, [])

    async def test_stop_cancels_work_that_exceeds_grace(self) -> None:
        manager = RuntimeManager(Mock())
        canceled = asyncio.Event()

        async def stubborn() -> None:
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                canceled.set()
                raise

        task = asyncio.create_task(stubborn())
        manager._tasks = [task]
        await asyncio.sleep(0)

        await manager.stop(grace_seconds=0.01)

        self.assertTrue(canceled.is_set())
        self.assertTrue(task.cancelled())
        self.assertEqual(manager._tasks, [])

    async def test_loop_writes_backend_error_log_when_worker_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            from app.core.config import get_settings

            get_settings.cache_clear()
            session_factory = Mock()
            manager = RuntimeManager(session_factory)

            async def failing_worker(session_factory_arg: object) -> int:
                self.assertIs(session_factory_arg, session_factory)
                manager._stopped.set()
                raise RuntimeError("crawler worker boom")

            async def fake_wait_for(awaitable: object, timeout: float) -> object:
                _ = timeout
                return await awaitable

            with patch.dict("os.environ", {"AUTO_EMAIL_SENDER_DATA_DIR": temp_dir}):
                get_settings.cache_clear()
                with patch("app.services.runtime_manager.asyncio.wait_for", new=fake_wait_for):
                    await manager._loop("crawler-worker-1", 10, failing_worker)

                log_path = Path(temp_dir) / "logs" / "backend-errors.log"
                self.assertTrue(log_path.is_file())
                log_text = log_path.read_text(encoding="utf-8")
                self.assertIn("worker_name=crawler-worker-1", log_text)
                self.assertIn("RuntimeError: crawler worker boom", log_text)

            get_settings.cache_clear()
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
        self.assertEqual(worker_calls["crawler-worker-1"].args[1], 5)
        self.assertEqual(worker_calls["crawler-worker-1"].kwargs["processed_jitter_seconds"], (2, 5))
        self.assertEqual(worker_calls["dispatcher"].kwargs["processed_jitter_seconds"], (5, 5))

        await manager.stop()

    async def test_start_configures_separate_imap_pollers_to_wait_after_processing(self) -> None:
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
                    "imap_poll_interval_seconds": 300,
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
        self.assertTrue(worker_calls["imap-incremental-poller"].kwargs["wait_after_processed"])
        self.assertTrue(worker_calls["imap-history-poller"].kwargs["wait_after_processed"])
        self.assertNotEqual(
            worker_calls["imap-incremental-poller"].args[2],
            worker_calls["imap-history-poller"].args[2],
        )

        await manager.stop()
    async def test_worker_startup_settings_only_contains_restart_bound_values(self) -> None:
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

        self.assertFalse(hasattr(resolved, "crawler_worker_count"))
        self.assertEqual(resolved.match_analysis_job_worker_count, 1)
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
