from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.api.communication_groups import (
    create_communication_group,
    delete_communication_group,
    list_communication_groups,
    update_communication_group,
)
from app.models import IdentityCommunicationGroup, IdentityProfile, OperationLog
from app.schemas.communication_group import IdentityCommunicationGroupWrite
from app.services.identity_communication_groups import (
    cleanup_communication_group_after_identity_delete,
    resolve_identity_communication_scope,
)
from test.schema_database import create_schema_sqlite_database


class IdentityCommunicationGroupTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "communication_groups.db"
        create_schema_sqlite_database(self.db_path)
        self.engine = create_async_engine(
            f"sqlite+aiosqlite:///{self.db_path.as_posix()}",
            future=True,
        )
        self.session_factory = async_sessionmaker(
            bind=self.engine,
            autoflush=False,
            expire_on_commit=False,
        )

    def tearDown(self) -> None:
        self._run_async(self.engine.dispose())
        self.temp_dir.cleanup()

    def _run_async(self, awaitable):
        return asyncio.run(awaitable)

    def test_group_lifecycle_replaces_members_without_deleting_identities(self) -> None:
        async def scenario():
            async with self.session_factory() as session:
                identities = [
                    self._identity("身份 A", "group-a@example.com"),
                    self._identity("身份 B", "group-b@example.com"),
                    self._identity("身份 C", "group-c@example.com"),
                ]
                session.add_all(identities)
                await session.commit()

                created = await create_communication_group(
                    IdentityCommunicationGroupWrite(
                        identity_ids=[identities[0].id, identities[1].id],
                    ),
                    session=session,
                )
                listed = await list_communication_groups(session=session)
                updated = await update_communication_group(
                    created.id,
                    IdentityCommunicationGroupWrite(
                        identity_ids=[identities[1].id, identities[2].id],
                    ),
                    session=session,
                )
                await delete_communication_group(created.id, session=session)

                saved_identities = list(
                    await session.scalars(
                        select(IdentityProfile).order_by(IdentityProfile.id.asc()),
                    ),
                )
                groups = list(await session.scalars(select(IdentityCommunicationGroup)))
                logs = list(
                    await session.scalars(
                        select(OperationLog)
                        .where(
                            OperationLog.event_name.in_(
                                [
                                    "communication_group.created",
                                    "communication_group.updated",
                                    "communication_group.deleted",
                                ],
                            ),
                        )
                        .order_by(OperationLog.id.asc()),
                    ),
                )
                return created, listed, updated, saved_identities, groups, logs

        created, listed, updated, identities, groups, logs = self._run_async(scenario())

        self.assertEqual([member.id for member in created.members], [1, 2])
        self.assertEqual([group.id for group in listed], [created.id])
        self.assertEqual([member.id for member in updated.members], [2, 3])
        self.assertEqual(len(identities), 3)
        self.assertTrue(all(identity.communication_group_id is None for identity in identities))
        self.assertEqual(groups, [])
        self.assertEqual(
            [log.event_name for log in logs],
            [
                "communication_group.created",
                "communication_group.updated",
                "communication_group.deleted",
            ],
        )
        self.assertEqual(logs[1].event_metadata["before_member_ids"], [1, 2])
        self.assertEqual(logs[1].event_metadata["after_member_ids"], [2, 3])

    def test_group_validation_and_confirmed_merge_are_atomic(self) -> None:
        async def scenario():
            async with self.session_factory() as session:
                identities = [
                    self._identity(f"身份 {index}", f"merge-{index}@example.com")
                    for index in range(1, 5)
                ]
                session.add_all(identities)
                await session.commit()

                first = await create_communication_group(
                    IdentityCommunicationGroupWrite(
                        identity_ids=[identities[0].id, identities[1].id],
                    ),
                    session=session,
                )
                second = await create_communication_group(
                    IdentityCommunicationGroupWrite(
                        identity_ids=[identities[2].id, identities[3].id],
                    ),
                    session=session,
                )

                with self.assertRaises(HTTPException) as conflict_context:
                    await update_communication_group(
                        first.id,
                        IdentityCommunicationGroupWrite(
                            identity_ids=[identities[0].id, identities[2].id],
                        ),
                        session=session,
                    )

                unchanged = await list_communication_groups(session=session)
                merged = await update_communication_group(
                    first.id,
                    IdentityCommunicationGroupWrite(
                        identity_ids=[identities[0].id, identities[2].id],
                        confirm_merge_existing_groups=True,
                    ),
                    session=session,
                )
                saved_identities = list(
                    await session.scalars(
                        select(IdentityProfile).order_by(IdentityProfile.id.asc()),
                    ),
                )
                merge_log = await session.scalar(
                    select(OperationLog)
                    .where(OperationLog.event_name == "communication_group.merged")
                    .order_by(OperationLog.id.desc()),
                )
                return (
                    conflict_context.exception,
                    first.id,
                    second.id,
                    unchanged,
                    merged,
                    saved_identities,
                    merge_log,
                )

        conflict, first_id, second_id, unchanged, merged, identities, merge_log = (
            self._run_async(scenario())
        )

        self.assertEqual(conflict.status_code, 409)
        self.assertEqual(conflict.detail["group_ids"], [second_id])
        self.assertEqual(len(unchanged), 2)
        self.assertEqual(merged.id, first_id)
        self.assertEqual([member.id for member in merged.members], [1, 3, 4])
        self.assertIsNone(identities[1].communication_group_id)
        self.assertEqual(identities[0].communication_group_id, first_id)
        self.assertEqual(identities[2].communication_group_id, first_id)
        self.assertEqual(identities[3].communication_group_id, first_id)
        self.assertIsNotNone(merge_log)
        self.assertEqual(merge_log.event_metadata["merged_group_ids"], [second_id])

    def test_rejects_duplicate_only_and_missing_identity_selections(self) -> None:
        async def scenario():
            async with self.session_factory() as session:
                identity = self._identity("唯一身份", "single@example.com")
                session.add(identity)
                await session.commit()

                errors: list[HTTPException] = []
                for identity_ids in ([identity.id, identity.id], [identity.id, 999]):
                    try:
                        await create_communication_group(
                            IdentityCommunicationGroupWrite(identity_ids=identity_ids),
                            session=session,
                        )
                    except HTTPException as exc:
                        errors.append(exc)
                return errors

        errors = self._run_async(scenario())

        self.assertEqual([error.status_code for error in errors], [422, 422])
        self.assertEqual(errors[1].detail["identity_ids"], [999])

    def test_match_source_can_be_switched_cleared_and_is_cleared_when_removed(self) -> None:
        async def scenario():
            async with self.session_factory() as session:
                identities = [
                    self._identity("身份 A", "match-source-a@example.com"),
                    self._identity("身份 B", "match-source-b@example.com"),
                    self._identity("身份 C", "match-source-c@example.com"),
                ]
                session.add_all(identities)
                await session.commit()

                created = await create_communication_group(
                    IdentityCommunicationGroupWrite(
                        identity_ids=[identities[0].id, identities[1].id],
                        match_source_identity_id=identities[0].id,
                    ),
                    session=session,
                )
                created_source_id = created.match_source_identity_id
                preserved = await update_communication_group(
                    created.id,
                    IdentityCommunicationGroupWrite(
                        identity_ids=[identity.id for identity in identities],
                    ),
                    session=session,
                )
                preserved_source_id = preserved.match_source_identity_id
                cleared_explicitly = await update_communication_group(
                    created.id,
                    IdentityCommunicationGroupWrite(
                        identity_ids=[identity.id for identity in identities],
                        match_source_identity_id=None,
                    ),
                    session=session,
                )
                explicit_source_id = cleared_explicitly.match_source_identity_id
                switched = await update_communication_group(
                    created.id,
                    IdentityCommunicationGroupWrite(
                        identity_ids=[identity.id for identity in identities],
                        match_source_identity_id=identities[2].id,
                    ),
                    session=session,
                )
                switched_source_id = switched.match_source_identity_id
                cleared_by_member_removal = await update_communication_group(
                    created.id,
                    IdentityCommunicationGroupWrite(
                        identity_ids=[identities[0].id, identities[1].id],
                    ),
                    session=session,
                )
                removed_source_id = cleared_by_member_removal.match_source_identity_id
                try:
                    await update_communication_group(
                        created.id,
                        IdentityCommunicationGroupWrite(
                            identity_ids=[identities[0].id, identities[1].id],
                            match_source_identity_id=identities[2].id,
                        ),
                        session=session,
                    )
                except HTTPException as exc:
                    invalid_source_error = exc
                else:
                    self.fail("非组成员不应被允许作为匹配依据")

                update_logs = list(
                    await session.scalars(
                        select(OperationLog)
                        .where(OperationLog.event_name == "communication_group.updated")
                        .order_by(OperationLog.id.asc()),
                    ),
                )
                return (
                    created_source_id,
                    preserved_source_id,
                    explicit_source_id,
                    switched_source_id,
                    removed_source_id,
                    invalid_source_error,
                    update_logs,
                )

        (
            created_source_id,
            preserved_source_id,
            explicit_source_id,
            switched_source_id,
            removed_source_id,
            invalid_source_error,
            update_logs,
        ) = self._run_async(scenario())

        self.assertEqual(created_source_id, 1)
        self.assertEqual(preserved_source_id, 1)
        self.assertIsNone(explicit_source_id)
        self.assertEqual(switched_source_id, 3)
        self.assertIsNone(removed_source_id)
        self.assertEqual(invalid_source_error.status_code, 422)
        self.assertEqual(
            invalid_source_error.detail["message"],
            "匹配依据身份必须属于当前共享组",
        )
        self.assertEqual(invalid_source_error.detail["match_source_identity_id"], 3)
        self.assertEqual(
            update_logs[-1].event_metadata["before_match_source_identity_id"],
            3,
        )
        self.assertIsNone(
            update_logs[-1].event_metadata["after_match_source_identity_id"],
        )

    def test_scope_order_and_identity_deletion_cleanup(self) -> None:
        async def scenario():
            async with self.session_factory() as session:
                group = IdentityCommunicationGroup()
                identities = [
                    self._identity("身份 A", "scope-a@example.com"),
                    self._identity("身份 B", "scope-b@example.com"),
                    self._identity("身份 C", "scope-c@example.com"),
                    self._identity("身份 D", "scope-d@example.com"),
                ]
                session.add_all([group, *identities])
                await session.flush()
                for identity in identities[:3]:
                    identity.communication_group_id = group.id
                await session.commit()

                scope_b = await resolve_identity_communication_scope(
                    session,
                    active_identity_id=identities[1].id,
                )
                scope_d = await resolve_identity_communication_scope(
                    session,
                    active_identity_id=identities[3].id,
                )

                await session.delete(identities[2])
                await session.flush()
                first_cleanup = await cleanup_communication_group_after_identity_delete(
                    session,
                    group_id=group.id,
                    removed_identity_id=identities[2].id,
                )
                await session.commit()

                await session.delete(identities[1])
                await session.flush()
                second_cleanup = await cleanup_communication_group_after_identity_delete(
                    session,
                    group_id=group.id,
                    removed_identity_id=identities[1].id,
                )
                await session.commit()

                remaining_a = await session.get(IdentityProfile, identities[0].id)
                saved_group = await session.get(IdentityCommunicationGroup, group.id)
                return scope_b.identity_ids, scope_d.identity_ids, first_cleanup, second_cleanup, remaining_a, saved_group

        scope_b, scope_d, first_cleanup, second_cleanup, remaining_a, saved_group = (
            self._run_async(scenario())
        )

        self.assertEqual(scope_b, (2, 1, 3))
        self.assertEqual(scope_d, (4,))
        self.assertFalse(first_cleanup.dissolved)
        self.assertEqual(first_cleanup.member_ids, (1, 2))
        self.assertTrue(second_cleanup.dissolved)
        self.assertEqual(second_cleanup.member_ids, (1,))
        self.assertIsNone(remaining_a.communication_group_id)
        self.assertIsNone(saved_group)

    @staticmethod
    def _identity(profile_name: str, email_address: str) -> IdentityProfile:
        return IdentityProfile(
            name=profile_name,
            profile_name=profile_name,
            sender_name=profile_name,
            email_address=email_address,
            smtp_host="smtp.example.com",
            smtp_port=465,
            smtp_username=email_address,
            smtp_password="secret",
            default_language="zh-CN",
            outreach_generation_mode="template",
        )


if __name__ == "__main__":
    unittest.main()
