from __future__ import annotations

import asyncio
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models import (
    AppSetting,
    Base,
    EmailDirection,
    EmailTaskCancellationReason,
    EmailLog,
    EmailTask,
    EmailTaskSource,
    EmailTaskStatus,
    IdentityMaterial,
    IdentityMaterialType,
    IdentityProfile,
    ImapIdentitySyncLease,
    ImapMailboxHistoricalScanStatus,
    ImapMailboxSyncState,
    ImapProfessorHistoricalScanStatus,
    ImapProfessorSyncState,
    LLMProfile,
    Professor,
)
from app.modules.llm import runtime as llm_runtime
from app.modules.communications.imap.sync import (
    _mark_recent_sent_history_failed,
    _run_imap_identities_bounded,
    _run_identity_sync_with_lease,
    poll_identity_replies,
    repair_identity_replies,
    sync_identity_history_poll_once,
    sync_identity_imap_once,
    sync_identity_incremental_poll_once,
    sync_workspace_professor_replies,
)
from app.modules.communications.imap.state import (
    claim_imap_identity_sync,
    claim_next_mailbox_history_scans,
    claim_next_professor_scans,
    mark_mailbox_history_scan_progress,
    mark_professor_scan_completed,
    release_imap_identity_sync_claim,
)
from app.modules.workspace.tasks.runtime import (
    _create_manual_child_task,
    continue_task_manually,
    generate_task_draft,
)
from app.modules.workspace.thread import ensure_workspace_task


class ConcurrencyGuardTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "concurrency_guards.db"
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

    def test_generate_task_draft_claims_task_before_generation(self) -> None:
        task_id = self._run_async(self._create_manual_draft_task())

        async def delayed_generate(**kwargs):
            await asyncio.sleep(0.05)
            return self._build_draft_generation_result()

        async def run_twice() -> list[object]:
            return await asyncio.gather(
                generate_task_draft(self.session_factory, task_id, force=True),
                generate_task_draft(self.session_factory, task_id, force=True),
                return_exceptions=True,
            )

        with (
            patch(
                "app.modules.workspace.tasks.runtime.llm_runtime.generate_draft_content",
                new=AsyncMock(side_effect=delayed_generate),
            ) as mocked_generate,
            patch(
                "app.modules.workspace.tasks.runtime.llm_runtime.ensure_llm_runtime_adaptation",
                new=AsyncMock(
                    return_value=llm_runtime.LLMRuntimeAdaptation(
                        "chat_completions", None
                    ),
                ),
            ),
        ):
            results = self._run_async(run_twice())

        self.assertEqual(mocked_generate.await_count, 1)
        self.assertEqual(
            self._run_async(self._get_task_status(task_id)),
            EmailTaskStatus.REVIEW_REQUIRED.value,
        )
        self.assertEqual(
            self._run_async(
                self._count_email_logs(task_id, EmailDirection.DRAFT.value)
            ),
            1,
        )
        self.assertEqual(
            sum(1 for result in results if isinstance(result, tuple)),
            1,
        )
        self.assertEqual(
            sum(1 for result in results if isinstance(result, Exception)),
            1,
        )

    def test_imap_identity_lease_is_atomic_across_schedulers(self) -> None:
        async def scenario() -> tuple[int, bool]:
            identity_id = await self._create_imap_identity()
            claims = await asyncio.gather(
                claim_imap_identity_sync(
                    self.session_factory,
                    identity_id,
                    claim_kind="history",
                    lease_seconds=60,
                ),
                claim_imap_identity_sync(
                    self.session_factory,
                    identity_id,
                    claim_kind="history",
                    lease_seconds=60,
                ),
            )
            winners = [claim for claim in claims if claim is not None]
            released = await release_imap_identity_sync_claim(
                self.session_factory,
                winners[0],
            )
            return len(winners), released

        self.assertEqual(self._run_async(scenario()), (1, True))

    def test_imap_identity_timeout_returns_but_keeps_lease_until_expiry(self) -> None:
        async def scenario() -> tuple[int, bool, bool]:
            identity_id = await self._create_imap_identity()
            release = asyncio.Event()
            ignored_cancellation = asyncio.Event()
            finished = asyncio.Event()

            async def stubborn_operation() -> int:
                while not release.is_set():
                    try:
                        await release.wait()
                    except asyncio.CancelledError:
                        ignored_cancellation.set()
                finished.set()
                return 7

            settings = SimpleNamespace(
                imap_identity_lease_seconds=60,
                imap_identity_sync_timeout_seconds=0.01,
            )
            with (
                patch(
                    "app.modules.communications.imap.sync.get_settings",
                    return_value=settings,
                ),
                patch(
                    "app.modules.communications.imap.sync._IMAP_TASK_CANCEL_GRACE_SECONDS",
                    0.01,
                ),
            ):
                result = await asyncio.wait_for(
                    _run_identity_sync_with_lease(
                        self.session_factory,
                        identity_id,
                        claim_kind="history",
                        operation=stubborn_operation,
                    ),
                    timeout=0.5,
                )

            async with self.session_factory() as session:
                lease = await session.get(ImapIdentitySyncLease, identity_id)
                lease_is_held = bool(
                    lease is not None
                    and lease.claim_id
                    and lease.lease_expires_at
                    and lease.lease_expires_at > datetime.now(UTC)
                )
            cancellation_was_ignored = ignored_cancellation.is_set()
            release.set()
            await asyncio.wait_for(finished.wait(), timeout=0.5)
            return result, lease_is_held, cancellation_was_ignored

        self.assertEqual(self._run_async(scenario()), (0, True, True))

    def test_imap_identity_rejects_result_when_work_and_lost_heartbeat_finish_together(
        self,
    ) -> None:
        async def scenario() -> tuple[int, bool]:
            identity_id = await self._create_imap_identity()

            async def operation() -> int:
                return 7

            async def lost_heartbeat(*args, **kwargs) -> bool:
                return False

            async def wait_for_both(tasks, **kwargs):
                task_set = set(tasks)
                await asyncio.gather(*task_set, return_exceptions=True)
                return task_set, set()

            settings = SimpleNamespace(
                imap_identity_lease_seconds=60,
                imap_identity_sync_timeout_seconds=1,
            )
            with (
                patch(
                    "app.modules.communications.imap.sync.get_settings",
                    return_value=settings,
                ),
                patch(
                    "app.modules.communications.imap.sync._run_imap_identity_heartbeat",
                    new=lost_heartbeat,
                ),
                patch(
                    "app.modules.communications.imap.sync.asyncio.wait",
                    new=wait_for_both,
                ),
            ):
                result = await _run_identity_sync_with_lease(
                    self.session_factory,
                    identity_id,
                    claim_kind="history",
                    operation=operation,
                )

            async with self.session_factory() as session:
                lease = await session.get(ImapIdentitySyncLease, identity_id)
                lease_remains_held = bool(lease is not None and lease.claim_id)
            return result, lease_remains_held

        self.assertEqual(self._run_async(scenario()), (0, True))

    def test_imap_identity_stale_worker_cannot_commit_after_replacement_claim(
        self,
    ) -> None:
        async def scenario() -> tuple[int, int, str | None]:
            identity_id = await self._create_imap_identity()
            heartbeat_release = asyncio.Event()

            async def operation() -> int:
                async with self.session_factory() as session:
                    await session.execute(
                        update(ImapIdentitySyncLease)
                        .where(ImapIdentitySyncLease.identity_id == identity_id)
                        .values(
                            claim_id="replacement-claim",
                            claim_kind="history",
                            lease_expires_at=datetime.now(UTC) + timedelta(minutes=1),
                        )
                    )
                    await session.commit()
                await _mark_recent_sent_history_failed(
                    self.session_factory,
                    identity_id=identity_id,
                    sent_folder="Sent",
                    error=RuntimeError("stale write"),
                )
                return 7

            async def blocked_heartbeat(*args, **kwargs) -> bool:
                await heartbeat_release.wait()
                return True

            settings = SimpleNamespace(
                imap_identity_lease_seconds=60,
                imap_identity_sync_timeout_seconds=1,
            )
            with (
                patch(
                    "app.modules.communications.imap.sync.get_settings",
                    return_value=settings,
                ),
                patch(
                    "app.modules.communications.imap.sync._run_imap_identity_heartbeat",
                    new=blocked_heartbeat,
                ),
            ):
                result = await _run_identity_sync_with_lease(
                    self.session_factory,
                    identity_id,
                    claim_kind="history",
                    operation=operation,
                )

            async with self.session_factory() as session:
                mailbox_state_count = int(
                    await session.scalar(
                        select(func.count(ImapMailboxSyncState.id)).where(
                            ImapMailboxSyncState.identity_id == identity_id,
                        )
                    )
                    or 0
                )
                lease = await session.get(ImapIdentitySyncLease, identity_id)
                replacement_claim_id = lease.claim_id if lease is not None else None
            return result, mailbox_state_count, replacement_claim_id

        self.assertEqual(
            self._run_async(scenario()),
            (0, 0, "replacement-claim"),
        )

    def test_imap_identity_heartbeat_loss_does_not_wait_for_stubborn_operation(
        self,
    ) -> None:
        async def scenario() -> tuple[int, bool, bool]:
            identity_id = await self._create_imap_identity()
            started = asyncio.Event()
            release = asyncio.Event()
            ignored_cancellation = asyncio.Event()
            finished = asyncio.Event()

            async def stubborn_operation() -> int:
                started.set()
                while not release.is_set():
                    try:
                        await release.wait()
                    except asyncio.CancelledError:
                        ignored_cancellation.set()
                finished.set()
                return 7

            async def lost_heartbeat(*args, **kwargs) -> bool:
                await started.wait()
                return False

            settings = SimpleNamespace(
                imap_identity_lease_seconds=60,
                imap_identity_sync_timeout_seconds=1,
            )
            with (
                patch(
                    "app.modules.communications.imap.sync.get_settings",
                    return_value=settings,
                ),
                patch(
                    "app.modules.communications.imap.sync._run_imap_identity_heartbeat",
                    new=lost_heartbeat,
                ),
                patch(
                    "app.modules.communications.imap.sync._IMAP_TASK_CANCEL_GRACE_SECONDS",
                    0.01,
                ),
            ):
                result = await asyncio.wait_for(
                    _run_identity_sync_with_lease(
                        self.session_factory,
                        identity_id,
                        claim_kind="history",
                        operation=stubborn_operation,
                    ),
                    timeout=0.5,
                )

            returned_before_operation = not finished.is_set()
            release.set()
            await asyncio.wait_for(finished.wait(), timeout=0.5)
            return result, ignored_cancellation.is_set(), returned_before_operation

        self.assertEqual(self._run_async(scenario()), (0, True, True))

    def test_imap_identity_operation_error_releases_current_claim(self) -> None:
        async def scenario() -> str | None:
            identity_id = await self._create_imap_identity()

            async def operation() -> int:
                raise RuntimeError("sync failed")

            settings = SimpleNamespace(
                imap_identity_lease_seconds=60,
                imap_identity_sync_timeout_seconds=1,
            )
            with patch(
                "app.modules.communications.imap.sync.get_settings",
                return_value=settings,
            ):
                with self.assertRaisesRegex(RuntimeError, "sync failed"):
                    await _run_identity_sync_with_lease(
                        self.session_factory,
                        identity_id,
                        claim_kind="history",
                        operation=operation,
                    )

            async with self.session_factory() as session:
                lease = await session.get(ImapIdentitySyncLease, identity_id)
                return lease.claim_id if lease is not None else None

        self.assertIsNone(self._run_async(scenario()))

    def test_imap_mailbox_work_item_claim_is_atomic(self) -> None:
        async def scenario() -> tuple[int, int]:
            identity_id = await self._create_imap_identity()
            async with self.session_factory() as session:
                state = ImapMailboxSyncState(
                    identity_id=identity_id,
                    folder_role="inbox",
                    folder="INBOX",
                    history_scan_status=ImapMailboxHistoricalScanStatus.PENDING.value,
                )
                session.add(state)
                await session.commit()
                state_id = state.id
            claims = await asyncio.gather(
                claim_next_mailbox_history_scans(
                    self.session_factory,
                    identity_id,
                    folders=[("inbox", "INBOX")],
                    limit=1,
                ),
                claim_next_mailbox_history_scans(
                    self.session_factory,
                    identity_id,
                    folders=[("inbox", "INBOX")],
                    limit=1,
                ),
            )
            claimed_ids = [item.id for group in claims for item in group]
            return len(claimed_ids), state_id

        claimed_count, _ = self._run_async(scenario())
        self.assertEqual(claimed_count, 1)

    def test_old_mailbox_claim_cannot_write_after_replacement_claim(self) -> None:
        async def scenario() -> tuple[str, bool, int, int]:
            identity_id = await self._create_imap_identity()
            async with self.session_factory() as session:
                state = ImapMailboxSyncState(
                    identity_id=identity_id,
                    folder_role="inbox",
                    folder="INBOX",
                    history_scan_status=ImapMailboxHistoricalScanStatus.PENDING.value,
                )
                session.add(state)
                await session.commit()
                state_id = state.id
            [old_claim] = await claim_next_mailbox_history_scans(
                self.session_factory,
                identity_id,
                folders=[("inbox", "INBOX")],
                limit=1,
            )
            async with self.session_factory() as session:
                state = await session.get(ImapMailboxSyncState, state_id)
                assert state is not None
                state.history_lease_expires_at = datetime.now(UTC) - timedelta(
                    seconds=1
                )
                await session.commit()
            [new_claim] = await claim_next_mailbox_history_scans(
                self.session_factory,
                identity_id,
                folders=[("inbox", "INBOX")],
                limit=1,
            )
            await mark_mailbox_history_scan_progress(
                self.session_factory,
                state_id,
                next_before_uid=50,
                scanned_count_delta=3,
                matched_count_delta=2,
                uidvalidity=10,
                high_water_uid=100,
                last_seen_uid_floor=25,
                completed=True,
                claim_id=old_claim.history_claim_id,
            )
            async with self.session_factory() as session:
                stored = await session.get(ImapMailboxSyncState, state_id)
                assert stored is not None
                return (
                    stored.history_scan_status,
                    stored.history_claim_id == new_claim.history_claim_id,
                    stored.history_scanned_count,
                    stored.history_matched_count,
                )

        self.assertEqual(
            self._run_async(scenario()),
            (ImapMailboxHistoricalScanStatus.RUNNING.value, True, 0, 0),
        )

    def test_old_professor_claim_cannot_write_after_replacement_claim(self) -> None:
        async def scenario() -> tuple[str, bool, int | None]:
            identity_id = await self._create_imap_identity()
            async with self.session_factory() as session:
                professor = Professor(
                    name="IMAP claim 测试导师",
                    email="imap-claim@example.edu",
                    research_direction="AI",
                    recent_papers=[],
                )
                session.add(professor)
                await session.flush()
                state = ImapProfessorSyncState(
                    identity_id=identity_id,
                    professor_id=professor.id,
                    professor_email=professor.email,
                    historical_scan_status=ImapProfessorHistoricalScanStatus.PENDING.value,
                )
                session.add(state)
                await session.commit()
                state_id = state.id
            [old_claim] = await claim_next_professor_scans(
                self.session_factory,
                identity_id,
                limit=1,
            )
            async with self.session_factory() as session:
                state = await session.get(ImapProfessorSyncState, state_id)
                assert state is not None
                state.history_lease_expires_at = datetime.now(UTC) - timedelta(
                    seconds=1
                )
                await session.commit()
            [new_claim] = await claim_next_professor_scans(
                self.session_factory,
                identity_id,
                limit=1,
            )
            await mark_professor_scan_completed(
                self.session_factory,
                state_id,
                123,
                claim_id=old_claim.history_claim_id,
            )
            async with self.session_factory() as session:
                stored = await session.get(ImapProfessorSyncState, state_id)
                assert stored is not None
                return (
                    stored.historical_scan_status,
                    stored.history_claim_id == new_claim.history_claim_id,
                    stored.last_scanned_uid,
                )

        self.assertEqual(
            self._run_async(scenario()),
            (ImapProfessorHistoricalScanStatus.RUNNING.value, True, None),
        )

    def test_imap_identity_polling_uses_bounded_concurrency(self) -> None:
        async def scenario() -> tuple[int, int]:
            active = 0
            max_active = 0

            async def worker(_session_factory, identity_id: int) -> int:
                nonlocal active, max_active
                active += 1
                max_active = max(max_active, active)
                try:
                    await asyncio.sleep(0.03)
                    return identity_id
                finally:
                    active -= 1

            with patch(
                "app.modules.communications.imap.sync.get_settings",
                return_value=SimpleNamespace(imap_identity_concurrency=2),
            ):
                total = await _run_imap_identities_bounded(
                    self.session_factory,
                    [1, 2, 3, 4],
                    worker,
                    poll_name="test",
                )
            return total, max_active

        self.assertEqual(self._run_async(scenario()), (10, 2))

    def test_ensure_workspace_task_is_idempotent_under_concurrent_calls(self) -> None:
        identity_id, llm_profile_id, professor_id = self._run_async(
            self._create_workspace_context()
        )

        async def create_task() -> int:
            async with self.session_factory() as session:
                task = await ensure_workspace_task(
                    session,
                    professor_id=professor_id,
                    identity_id=identity_id,
                    llm_profile_id=llm_profile_id,
                )
                return task.id

        async def run_twice() -> list[int]:
            return await asyncio.gather(create_task(), create_task())

        results = self._run_async(run_twice())

        self.assertEqual(results[0], results[1])
        self.assertEqual(
            self._run_async(
                self._count_workspace_tasks(identity_id, llm_profile_id, professor_id)
            ),
            1,
        )

    def test_continue_task_manually_recovers_when_child_is_created_concurrently(
        self,
    ) -> None:
        task_id, identity_id, llm_profile_id, professor_id = self._run_async(
            self._create_continue_context(),
        )
        inserted = False

        async def create_competing_child(*args, **kwargs) -> None:
            nonlocal inserted
            if inserted:
                return
            inserted = True
            async with self.session_factory() as competing_session:
                parent_task = await competing_session.get(EmailTask, task_id)
                assert parent_task is not None
                competing_child = _create_manual_child_task(
                    parent_task, reuse_existing_draft=True
                )
                competing_session.add(competing_child)
                await competing_session.commit()

        with patch(
            "app.modules.workspace.tasks.runtime._ensure_no_manual_child_exists",
            new=AsyncMock(side_effect=create_competing_child),
        ):
            result = self._run_async(
                continue_task_manually(self.session_factory, task_id)
            )

        self.assertEqual(result, (professor_id, identity_id, llm_profile_id))
        self.assertEqual(self._run_async(self._count_manual_children(task_id)), 1)

    def test_poll_identity_replies_uses_guarded_sync_entrypoint(self) -> None:
        identity_id, _, professor_id = self._run_async(self._create_reply_context())

        async def poll_twice() -> list[int]:
            return await asyncio.gather(
                poll_identity_replies(self.session_factory, identity_id),
                poll_identity_replies(self.session_factory, identity_id),
            )

        async def delayed_sync(*args, **kwargs):
            await asyncio.sleep(0.05)
            return 1

        with patch(
            "app.modules.communications.imap.sync._sync_identity_imap_once_unlocked",
            new=AsyncMock(side_effect=delayed_sync),
        ) as mocked_sync:
            results = self._run_async(poll_twice())

        self.assertEqual(mocked_sync.await_count, 1)
        self.assertEqual(sum(results), 1)

    def test_imap_identity_sync_is_single_flight(self) -> None:
        identity_id, _, _ = self._run_async(self._create_reply_context())

        async def delayed_sync(*args, **kwargs):
            await asyncio.sleep(0.05)
            return 1

        async def sync_twice() -> list[int]:
            return await asyncio.gather(
                sync_identity_imap_once(self.session_factory, identity_id),
                sync_identity_imap_once(self.session_factory, identity_id),
            )

        with patch(
            "app.modules.communications.imap.sync._sync_identity_imap_once_unlocked",
            new=AsyncMock(side_effect=delayed_sync),
        ) as mocked_sync:
            results = self._run_async(sync_twice())

        self.assertEqual(mocked_sync.await_count, 1)
        self.assertEqual(sum(results), 1)

    def test_full_imap_sync_skips_while_incremental_poll_is_running(self) -> None:
        identity_id, _, _ = self._run_async(self._create_reply_context())

        started = asyncio.Event()
        release = asyncio.Event()

        async def delayed_incremental(*args, **kwargs):
            started.set()
            await release.wait()
            return 1

        async def scenario() -> tuple[int, int, int]:
            incremental_task = asyncio.create_task(
                sync_identity_incremental_poll_once(self.session_factory, identity_id),
            )
            await started.wait()
            full_result = await sync_identity_imap_once(
                self.session_factory, identity_id
            )
            release.set()
            incremental_result = await incremental_task
            return incremental_result, full_result, full_sync_mock.await_count

        with (
            patch(
                "app.modules.communications.imap.sync._sync_identity_incremental_once_unlocked",
                new=AsyncMock(side_effect=delayed_incremental),
            ),
            patch(
                "app.modules.communications.imap.sync._sync_identity_imap_once_unlocked",
                new=AsyncMock(return_value=10),
            ) as full_sync_mock,
        ):
            result = self._run_async(scenario())

        self.assertEqual(result, (1, 0, 0))

    def test_full_imap_sync_skips_while_history_poll_is_running(self) -> None:
        identity_id, _, _ = self._run_async(self._create_reply_context())

        started = asyncio.Event()
        release = asyncio.Event()

        async def delayed_history(*args, **kwargs):
            started.set()
            await release.wait()
            return 1

        async def scenario() -> tuple[int, int, int]:
            history_task = asyncio.create_task(
                sync_identity_history_poll_once(self.session_factory, identity_id),
            )
            await started.wait()
            full_result = await sync_identity_imap_once(
                self.session_factory, identity_id
            )
            release.set()
            history_result = await history_task
            return history_result, full_result, full_sync_mock.await_count

        with (
            patch(
                "app.modules.communications.imap.sync.sync_identity_history_once",
                new=AsyncMock(side_effect=delayed_history),
            ),
            patch(
                "app.modules.communications.imap.sync._sync_identity_imap_once_unlocked",
                new=AsyncMock(return_value=10),
            ) as full_sync_mock,
        ):
            result = self._run_async(scenario())

        self.assertEqual(result, (1, 0, 0))

    def test_full_imap_sync_blocks_incremental_and_history_pollers(self) -> None:
        identity_id, _, _ = self._run_async(self._create_reply_context())

        started = asyncio.Event()
        release = asyncio.Event()

        async def delayed_full_sync(*args, **kwargs):
            started.set()
            await release.wait()
            return 1

        async def scenario() -> tuple[int, int, int, int, int]:
            full_task = asyncio.create_task(
                sync_identity_imap_once(self.session_factory, identity_id)
            )
            await started.wait()
            incremental_result = await sync_identity_incremental_poll_once(
                self.session_factory,
                identity_id,
            )
            history_result = await sync_identity_history_poll_once(
                self.session_factory,
                identity_id,
            )
            release.set()
            full_result = await full_task
            return (
                full_result,
                incremental_result,
                history_result,
                incremental_mock.await_count,
                history_mock.await_count,
            )

        with (
            patch(
                "app.modules.communications.imap.sync._sync_identity_imap_once_unlocked",
                new=AsyncMock(side_effect=delayed_full_sync),
            ),
            patch(
                "app.modules.communications.imap.sync._sync_identity_incremental_once_unlocked",
                new=AsyncMock(return_value=10),
            ) as incremental_mock,
            patch(
                "app.modules.communications.imap.sync.sync_identity_history_once",
                new=AsyncMock(return_value=20),
            ) as history_mock,
        ):
            result = self._run_async(scenario())

        self.assertEqual(result, (1, 0, 0, 0, 0))

    def test_history_poller_skips_while_incremental_poller_is_running(self) -> None:
        identity_id, _, _ = self._run_async(self._create_reply_context())

        incremental_started = asyncio.Event()
        release_incremental = asyncio.Event()

        async def delayed_incremental(*args, **kwargs):
            incremental_started.set()
            await release_incremental.wait()
            return 1

        async def scenario() -> tuple[int, int, int]:
            incremental_task = asyncio.create_task(
                sync_identity_incremental_poll_once(self.session_factory, identity_id),
            )
            await incremental_started.wait()
            history_result = await sync_identity_history_poll_once(
                self.session_factory, identity_id
            )
            release_incremental.set()
            incremental_result = await incremental_task
            return incremental_result, history_result, history_mock.await_count

        with (
            patch(
                "app.modules.communications.imap.sync._sync_identity_incremental_once_unlocked",
                new=AsyncMock(side_effect=delayed_incremental),
            ),
            patch(
                "app.modules.communications.imap.sync.sync_identity_history_once",
                new=AsyncMock(return_value=2),
            ) as history_mock,
        ):
            result = self._run_async(scenario())

        self.assertEqual(result, (1, 0, 0))

    def test_workspace_professor_sync_skips_while_full_imap_sync_is_running(
        self,
    ) -> None:
        identity_id, _, professor_id = self._run_async(self._create_reply_context())

        started = asyncio.Event()
        release = asyncio.Event()

        async def delayed_full_sync(*args, **kwargs):
            started.set()
            await release.wait()
            return 1

        async def scenario() -> tuple[int, int, int]:
            full_task = asyncio.create_task(
                sync_identity_imap_once(self.session_factory, identity_id)
            )
            await started.wait()
            workspace_result = await sync_workspace_professor_replies(
                self.session_factory,
                identity_id,
                professor_id,
            )
            release.set()
            full_result = await full_task
            return full_result, workspace_result, fetch_mock.await_count

        with (
            patch(
                "app.modules.communications.imap.sync._sync_identity_imap_once_unlocked",
                new=AsyncMock(side_effect=delayed_full_sync),
            ),
            patch(
                "app.modules.communications.imap.sync.mail_runtime.fetch_professor_history_inbox_messages",
                new=AsyncMock(return_value=[]),
            ) as fetch_mock,
        ):
            result = self._run_async(scenario())

        self.assertEqual(result, (1, 0, 0))

    def test_professor_repair_skips_while_full_imap_sync_is_running(self) -> None:
        identity_id, _, _ = self._run_async(self._create_reply_context())

        started = asyncio.Event()
        release = asyncio.Event()

        async def delayed_full_sync(*args, **kwargs):
            started.set()
            await release.wait()
            return 1

        async def scenario() -> tuple[int, int, int]:
            full_task = asyncio.create_task(
                sync_identity_imap_once(self.session_factory, identity_id)
            )
            await started.wait()
            repair_result = await repair_identity_replies(
                self.session_factory,
                identity_id,
                professor_email="professor@example.edu",
            )
            release.set()
            full_result = await full_task
            return full_result, repair_result, fetch_mock.await_count

        with (
            patch(
                "app.modules.communications.imap.sync._sync_identity_imap_once_unlocked",
                new=AsyncMock(side_effect=delayed_full_sync),
            ),
            patch(
                "app.modules.communications.imap.sync.mail_runtime.fetch_professor_history_inbox_messages",
                new=AsyncMock(return_value=[]),
            ) as fetch_mock,
        ):
            result = self._run_async(scenario())

        self.assertEqual(result, (1, 0, 0))

    async def _create_schema(self) -> None:
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)

    async def _create_imap_identity(self) -> int:
        async with self.session_factory() as session:
            identity = IdentityProfile(
                name="IMAP 测试身份",
                profile_name="IMAP 测试身份",
                sender_name="王同学",
                email_address="imap-scheduler@example.com",
                smtp_host="smtp.example.com",
                smtp_port=465,
                smtp_username="imap-scheduler@example.com",
                smtp_password="secret",
                imap_host="imap.example.com",
                imap_port=993,
                imap_username="imap-scheduler@example.com",
                imap_password="secret",
                default_language="zh-CN",
                outreach_generation_mode="template",
                outreach_template_subject="测试主题",
                outreach_template_body_text="测试正文",
                is_default=True,
            )
            session.add(identity)
            await session.commit()
            return identity.id

    async def _create_manual_draft_task(self) -> int:
        async with self.session_factory() as session:
            session.add(AppSetting(id=1))
            identity = IdentityProfile(
                name="测试身份",
                profile_name="测试身份",
                sender_name="王同学",
                email_address="sender@example.com",
                smtp_host="smtp.example.com",
                smtp_port=465,
                smtp_username="sender@example.com",
                smtp_password="secret",
                default_language="zh-CN",
                outreach_generation_mode="llm",
                outreach_template_subject="申请与{{name}}老师交流",
                outreach_template_body_text="老师您好，我是{{sender_name}}。",
                is_default=True,
            )
            material = IdentityMaterial(
                identity=identity,
                display_name="简历",
                original_filename="resume.txt",
                file_path="resume.txt",
                mime_type="text/plain",
                size_bytes=32,
                sha256="0" * 64,
                extracted_text="My research focuses on agents.",
                material_type=IdentityMaterialType.RESUME.value,
            )
            identity.current_primary_material = material
            llm_profile = LLMProfile(
                name=f"默认模型-{datetime.now(UTC).timestamp()}",
                provider="openai",
                api_base_url="https://api.example.com/v1",
                api_key="sk-test-key",
                model_name="gpt-test",
                is_default=True,
            )
            professor = Professor(
                name="张教授",
                email="professor@example.edu",
                title="Professor",
                university="Example University",
                school="School of AI",
                department="Computer Science",
                research_direction="Large language models",
                recent_papers=[],
            )
            task = EmailTask(
                source=EmailTaskSource.MANUAL.value,
                batch_task_id=None,
                identity=identity,
                llm_profile=llm_profile,
                professor=professor,
                primary_material=material,
                status=EmailTaskStatus.DISCOVERED.value,
                outreach_generation_mode="llm",
                outreach_template_subject="申请与{{name}}老师交流",
                outreach_template_body_text="老师您好，我是{{sender_name}}。",
                outreach_template_body_html="<p>老师您好，我是{{sender_name}}。</p>",
                selected_material_ids=[],
            )
            session.add(task)
            await session.commit()
            return task.id

    async def _create_workspace_context(self) -> tuple[int, int, int]:
        async with self.session_factory() as session:
            identity = IdentityProfile(
                name="测试身份",
                profile_name="测试身份",
                sender_name="王同学",
                email_address="sender-workspace@example.com",
                smtp_host="smtp.example.com",
                smtp_port=465,
                smtp_username="sender@example.com",
                smtp_password="secret",
                default_language="zh-CN",
                outreach_generation_mode="template",
                outreach_template_subject="申请与{{name}}老师交流",
                outreach_template_body_text="老师您好，我是{{sender_name}}。",
                is_default=True,
            )
            llm_profile = LLMProfile(
                name=f"默认模型-workspace-{datetime.now(UTC).timestamp()}",
                provider="openai",
                api_base_url="https://api.example.com/v1",
                api_key="sk-test-key",
                model_name="gpt-test",
                is_default=True,
            )
            professor = Professor(
                name="张教授",
                email="workspace-professor@example.edu",
                title="Professor",
                university="Example University",
                school="School of AI",
                department="Computer Science",
                research_direction="Large language models",
                recent_papers=[],
            )
            session.add_all([identity, llm_profile, professor])
            await session.commit()
            return identity.id, llm_profile.id, professor.id

    async def _create_reply_context(self) -> tuple[int, int, int]:
        async with self.session_factory() as session:
            identity = IdentityProfile(
                name="测试身份",
                profile_name="测试身份",
                sender_name="王同学",
                email_address="sender-reply@example.com",
                smtp_host="smtp.example.com",
                smtp_port=465,
                smtp_username="sender@example.com",
                smtp_password="secret",
                imap_host="imap.example.com",
                imap_port=993,
                imap_username="sender@example.com",
                imap_password="secret",
                default_language="zh-CN",
                outreach_generation_mode="template",
                outreach_template_subject="申请与{{name}}老师交流",
                outreach_template_body_text="老师您好，我是{{sender_name}}。",
                is_default=True,
            )
            llm_profile = LLMProfile(
                name=f"默认模型-reply-{datetime.now(UTC).timestamp()}",
                provider="openai",
                api_base_url="https://api.example.com/v1",
                api_key="sk-test-key",
                model_name="gpt-test",
                is_default=True,
            )
            professor = Professor(
                name="张教授",
                email="professor@example.edu",
                title="Professor",
                university="Example University",
                school="School of AI",
                department="Computer Science",
                research_direction="Large language models",
                recent_papers=[],
            )
            task = EmailTask(
                source=EmailTaskSource.MANUAL.value,
                batch_task_id=None,
                identity=identity,
                llm_profile=llm_profile,
                professor=professor,
                status=EmailTaskStatus.SENT.value,
                outreach_generation_mode="template",
                approved_subject="申请与{{name}}老师交流",
                approved_body_text="老师您好，我是{{sender_name}}。",
                approved_body_html="<p>老师您好，我是{{sender_name}}。</p>",
                sent_at=datetime.now(UTC),
                last_rfc_message_id="<sent@example.edu>",
                selected_material_ids=[],
            )
            session.add_all(
                [
                    identity,
                    llm_profile,
                    professor,
                    task,
                    EmailLog(
                        email_task=task,
                        identity=identity,
                        llm_profile=llm_profile,
                        professor=professor,
                        direction=EmailDirection.SENT.value,
                        subject="申请与张教授老师交流",
                        content="老师您好，我是王同学。",
                        content_html="<p>老师您好，我是王同学。</p>",
                        rfc_message_id="<sent@example.edu>",
                    ),
                ],
            )
            await session.commit()
            return identity.id, llm_profile.id, professor.id

    async def _create_continue_context(self) -> tuple[int, int, int, int]:
        async with self.session_factory() as session:
            identity = IdentityProfile(
                name="测试身份",
                profile_name="测试身份",
                sender_name="王同学",
                email_address="sender-continue@example.com",
                smtp_host="smtp.example.com",
                smtp_port=465,
                smtp_username="sender@example.com",
                smtp_password="secret",
                default_language="zh-CN",
                outreach_generation_mode="template",
                outreach_template_subject="申请与{{name}}老师交流",
                outreach_template_body_text="老师您好，我是{{sender_name}}。",
                is_default=True,
            )
            llm_profile = LLMProfile(
                name=f"默认模型-continue-{datetime.now(UTC).timestamp()}",
                provider="openai",
                api_base_url="https://api.example.com/v1",
                api_key="sk-test-key",
                model_name="gpt-test",
                is_default=True,
            )
            professor = Professor(
                name="张教授",
                email="continue-professor@example.edu",
                title="Professor",
                university="Example University",
                school="School of AI",
                department="Computer Science",
                research_direction="Large language models",
                recent_papers=[],
            )
            task = EmailTask(
                source=EmailTaskSource.MANUAL.value,
                batch_task_id=None,
                identity=identity,
                llm_profile=llm_profile,
                professor=professor,
                status=EmailTaskStatus.CANCELED.value,
                cancellation_reason=EmailTaskCancellationReason.BATCH_STOPPED.value,
                outreach_generation_mode="template",
                outreach_template_subject="申请与{{name}}老师交流",
                outreach_template_body_text="老师您好，我是{{sender_name}}。",
                outreach_template_body_html="<p>老师您好，我是{{sender_name}}。</p>",
                selected_material_ids=[],
            )
            session.add_all([identity, llm_profile, professor, task])
            await session.commit()
            return task.id, identity.id, llm_profile.id, professor.id

    async def _count_workspace_tasks(
        self, identity_id: int, llm_profile_id: int, professor_id: int
    ) -> int:
        async with self.session_factory() as session:
            return int(
                await session.scalar(
                    select(func.count())
                    .select_from(EmailTask)
                    .where(
                        EmailTask.identity_id == identity_id,
                        EmailTask.llm_profile_id == llm_profile_id,
                        EmailTask.professor_id == professor_id,
                        EmailTask.source == EmailTaskSource.MANUAL.value,
                        EmailTask.batch_task_id.is_(None),
                        EmailTask.parent_task_id.is_(None),
                    ),
                )
                or 0
            )

    async def _count_manual_children(self, parent_task_id: int) -> int:
        async with self.session_factory() as session:
            return int(
                await session.scalar(
                    select(func.count())
                    .select_from(EmailTask)
                    .where(
                        EmailTask.parent_task_id == parent_task_id,
                    ),
                )
                or 0
            )

    async def _count_reply_logs(self, message_id: str) -> int:
        async with self.session_factory() as session:
            return int(
                await session.scalar(
                    select(func.count())
                    .select_from(EmailLog)
                    .where(EmailLog.rfc_message_id == message_id),
                )
                or 0
            )

    async def _count_email_logs(self, task_id: int, direction: str) -> int:
        async with self.session_factory() as session:
            return int(
                await session.scalar(
                    select(func.count())
                    .select_from(EmailLog)
                    .where(
                        EmailLog.email_task_id == task_id,
                        EmailLog.direction == direction,
                    ),
                )
                or 0
            )

    async def _get_task_status(self, task_id: int) -> str:
        async with self.session_factory() as session:
            task = await session.get(EmailTask, task_id)
            assert task is not None
            return task.status

    async def _get_task_status_by_professor(
        self, professor_id: int, identity_id: int
    ) -> str:
        async with self.session_factory() as session:
            task = await session.scalar(
                select(EmailTask).where(
                    EmailTask.professor_id == professor_id,
                    EmailTask.identity_id == identity_id,
                ),
            )
            assert task is not None
            return task.status

    @staticmethod
    def _build_draft_generation_result() -> llm_runtime.GeneratedDraftContent:
        return llm_runtime.GeneratedDraftContent(
            result=llm_runtime.DraftGenerationResult(
                subject="生成主题",
                body_text="生成正文",
                body_html="<p>生成正文</p>",
            ),
            usage=llm_runtime.ChatCompletionUsage(
                prompt_tokens=10,
                completion_tokens=5,
                total_tokens=15,
            ),
        )

    @staticmethod
    def _build_received_email(
        *,
        from_email: str,
        subject: str,
        content: str,
        message_id: str,
        in_reply_to: str,
    ):
        from app.modules.communications.transport import ReceivedEmail

        return ReceivedEmail(
            from_email=from_email,
            subject=subject,
            content=content,
            content_html=None,
            message_id=message_id,
            in_reply_to=in_reply_to,
            references=in_reply_to,
            sent_at=datetime.now(UTC),
            headers={
                "from": from_email,
                "subject": subject,
                "message_id": message_id,
                "in_reply_to": in_reply_to,
                "references": in_reply_to,
                "to": "sender@example.com",
            },
        )

    @staticmethod
    def _run_async(coro):
        return asyncio.run(coro)


if __name__ == "__main__":
    unittest.main()
