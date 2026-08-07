from __future__ import annotations

import asyncio
import os
import tempfile
import unittest
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models import (
    CrawlCandidate,
    CrawlCandidateIdentityKey,
    CrawlJob,
)
from app.modules.crawler.candidate_identity import (
    apply_candidate_enrichment_values,
    canonical_candidate_clause,
    canonicalize_candidate_ids,
    consolidate_candidate_identity,
    mark_candidate_fields_manual,
    rebuild_candidate_identity_keys,
    resolve_canonical_candidate,
)
from test.schema_database import create_schema_sqlite_database


class CrawlCandidateIdentityTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        fd, self.db_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        create_schema_sqlite_database(Path(self.db_path))
        self.engine = create_async_engine(
            f"sqlite+aiosqlite:///{Path(self.db_path).as_posix()}",
        )
        self.session_factory = async_sessionmaker(self.engine, expire_on_commit=False)
        async with self.session_factory() as session:
            job = CrawlJob(
                university="示例大学",
                school="计算机学院",
                start_url="https://example.edu/faculty",
            )
            session.add(job)
            await session.commit()
            self.job_id = job.id

    async def asyncTearDown(self) -> None:
        await self.engine.dispose()
        try:
            os.unlink(self.db_path)
        except FileNotFoundError:
            pass

    async def _create_candidate(
        self,
        *,
        name: str,
        email: str | None = None,
        profile_url: str | None = None,
    ) -> int:
        async with self.session_factory() as session:
            candidate = CrawlCandidate(
                job_id=self.job_id,
                name=name,
                email=email,
                profile_url=profile_url,
            )
            session.add(candidate)
            await session.flush()
            await consolidate_candidate_identity(session, candidate)
            await session.commit()
            return candidate.id

    async def test_enrichment_same_email_merges_candidates_and_preserves_aliases(self) -> None:
        first_id = await self._create_candidate(
            name="张三",
            profile_url="https://example.edu/people/zhang",
        )
        second_id = await self._create_candidate(
            name="Zhang San",
            profile_url="https://example.edu/lab/zhang-san",
        )

        async with self.session_factory() as session:
            first = await session.get(CrawlCandidate, first_id)
            second = await session.get(CrawlCandidate, second_id)
            assert first is not None and second is not None
            apply_candidate_enrichment_values(first, {"email": "ZHANG@example.edu"})
            await consolidate_candidate_identity(session, first)
            apply_candidate_enrichment_values(second, {"email": "zhang@example.edu"})
            canonical = await consolidate_candidate_identity(session, second)
            await session.commit()

        self.assertEqual(canonical.id, first_id)
        async with self.session_factory() as session:
            rows = list(
                await session.scalars(
                    select(CrawlCandidate)
                    .where(CrawlCandidate.job_id == self.job_id)
                    .order_by(CrawlCandidate.id),
                )
            )
            canonical_rows = list(
                await session.scalars(
                    select(CrawlCandidate).where(
                        CrawlCandidate.job_id == self.job_id,
                        canonical_candidate_clause(),
                    )
                )
            )
            keys = list(
                await session.scalars(
                    select(CrawlCandidateIdentityKey).where(
                        CrawlCandidateIdentityKey.job_id == self.job_id,
                    )
                )
            )
            canonicalized, missing = await canonicalize_candidate_ids(
                session,
                job_id=self.job_id,
                candidate_ids=[first_id, second_id],
            )

        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[1].merged_into_candidate_id, first_id)
        self.assertEqual([row.id for row in canonical_rows], [first_id])
        self.assertEqual({key.candidate_id for key in keys}, {first_id})
        self.assertEqual(
            {(key.key_type, key.normalized_value) for key in keys},
            {
                ("email", "zhang@example.edu"),
                ("profile_url", "https://example.edu/people/zhang"),
                ("profile_url", "https://example.edu/lab/zhang-san"),
            },
        )
        self.assertEqual([row.id for row in canonicalized], [first_id])
        self.assertEqual(missing, [])

    async def test_alias_enrichment_updates_canonical_and_manual_values_win(self) -> None:
        first_id = await self._create_candidate(
            name="张三",
            email="zhang@example.edu",
            profile_url="https://example.edu/a",
        )
        second_id = await self._create_candidate(
            name="张三老师",
            email="zhang@example.edu",
            profile_url="https://example.edu/b",
        )

        async with self.session_factory() as session:
            canonical = await session.get(CrawlCandidate, first_id)
            alias = await session.get(CrawlCandidate, second_id)
            assert canonical is not None and alias is not None
            canonical.title = "手动职称"
            mark_candidate_fields_manual(canonical, ["title"])
            apply_candidate_enrichment_values(
                alias,
                {
                    "title": "自动职称",
                    "department": "计算机系",
                },
            )
            await consolidate_candidate_identity(session, alias)
            first_history_size = len(canonical.merge_history or [])
            await consolidate_candidate_identity(session, alias)
            second_history_size = len(canonical.merge_history or [])
            await session.commit()

        self.assertEqual(canonical.title, "手动职称")
        self.assertEqual(canonical.department, "计算机系")
        self.assertEqual(first_history_size, second_history_size)

    async def test_same_name_with_different_emails_does_not_merge(self) -> None:
        await self._create_candidate(name="张三", email="one@example.edu")
        await self._create_candidate(name="张三", email="two@example.edu")

        async with self.session_factory() as session:
            count = await session.scalar(
                select(func.count())
                .select_from(CrawlCandidate)
                .where(
                    CrawlCandidate.job_id == self.job_id,
                    canonical_candidate_clause(),
                )
            )

        self.assertEqual(count, 2)

    async def test_identity_normalization_merges_unicode_email_and_tracking_urls(self) -> None:
        email_root_id = await self._create_candidate(
            name="邮箱写法一",
            email="USER＠Example．EDU",
        )
        await self._create_candidate(
            name="邮箱写法二",
            email="user@example.edu",
        )
        profile_root_id = await self._create_candidate(
            name="主页写法一",
            profile_url="https://Example.edu/person?id=1&utm_source=list",
        )
        await self._create_candidate(
            name="主页写法二",
            profile_url="https://example.edu/person?utm_campaign=detail&id=1",
        )

        async with self.session_factory() as session:
            roots = list(
                await session.scalars(
                    select(CrawlCandidate)
                    .where(
                        CrawlCandidate.job_id == self.job_id,
                        canonical_candidate_clause(),
                    )
                    .order_by(CrawlCandidate.id)
                )
            )

        self.assertEqual([row.id for row in roots], [email_root_id, profile_root_id])

    async def test_invalid_matching_email_text_is_not_an_identity(self) -> None:
        await self._create_candidate(name="无效邮箱一", email="not-an-email")
        await self._create_candidate(name="无效邮箱二", email="not-an-email")

        async with self.session_factory() as session:
            count = await session.scalar(
                select(func.count())
                .select_from(CrawlCandidate)
                .where(
                    CrawlCandidate.job_id == self.job_id,
                    canonical_candidate_clause(),
                )
            )

        self.assertEqual(count, 2)

    async def test_invalid_matching_profile_text_is_not_an_identity(self) -> None:
        await self._create_candidate(
            name="无效主页一",
            profile_url="not-a-url",
        )
        await self._create_candidate(
            name="无效主页二",
            profile_url="not-a-url",
        )

        async with self.session_factory() as session:
            count = await session.scalar(
                select(func.count())
                .select_from(CrawlCandidate)
                .where(
                    CrawlCandidate.job_id == self.job_id,
                    canonical_candidate_clause(),
                )
            )

        self.assertEqual(count, 2)

    async def test_concurrent_same_email_registration_keeps_one_canonical_candidate(self) -> None:
        candidate_ids = [
            await self._create_candidate(name=f"并发候选 {index}")
            for index in range(8)
        ]
        ready = asyncio.Event()
        loaded = 0
        loaded_lock = asyncio.Lock()

        async def enrich(candidate_id: int) -> None:
            nonlocal loaded
            async with self.session_factory() as session:
                candidate = await session.get(CrawlCandidate, candidate_id)
                assert candidate is not None
                apply_candidate_enrichment_values(
                    candidate,
                    {"email": "same@example.edu"},
                )
                async with loaded_lock:
                    loaded += 1
                    if loaded == len(candidate_ids):
                        ready.set()
                await ready.wait()
                await consolidate_candidate_identity(session, candidate)
                await session.commit()

        await asyncio.gather(*(enrich(candidate_id) for candidate_id in candidate_ids))

        async with self.session_factory() as session:
            canonical_count = await session.scalar(
                select(func.count())
                .select_from(CrawlCandidate)
                .where(
                    CrawlCandidate.job_id == self.job_id,
                    canonical_candidate_clause(),
                )
            )
            email_key_count = await session.scalar(
                select(func.count())
                .select_from(CrawlCandidateIdentityKey)
                .where(
                    CrawlCandidateIdentityKey.job_id == self.job_id,
                    CrawlCandidateIdentityKey.key_type == "email",
                    CrawlCandidateIdentityKey.normalized_value == "same@example.edu",
                )
            )

        self.assertEqual(canonical_count, 1)
        self.assertEqual(email_key_count, 1)

    async def test_manual_email_clear_removes_stale_identity_key(self) -> None:
        first_id = await self._create_candidate(
            name="错误邮箱候选",
            email="wrong@example.edu",
        )
        await self._create_candidate(
            name="错误邮箱别名",
            email="WRONG@example.edu",
        )
        async with self.session_factory() as session:
            candidate = await session.get(CrawlCandidate, first_id)
            assert candidate is not None
            candidate.email = None
            mark_candidate_fields_manual(candidate, ["email"])
            await rebuild_candidate_identity_keys(
                session,
                candidate,
                exclude_identities={("email", "wrong@example.edu")},
            )
            await session.commit()

        second_id = await self._create_candidate(
            name="真实邮箱候选",
            email="wrong@example.edu",
        )

        async with self.session_factory() as session:
            rows = list(
                await session.scalars(
                    select(CrawlCandidate)
                    .where(
                        CrawlCandidate.job_id == self.job_id,
                        canonical_candidate_clause(),
                    )
                    .order_by(CrawlCandidate.id)
                )
            )
            email_key = await session.scalar(
                select(CrawlCandidateIdentityKey).where(
                    CrawlCandidateIdentityKey.job_id == self.job_id,
                    CrawlCandidateIdentityKey.key_type == "email",
                    CrawlCandidateIdentityKey.normalized_value == "wrong@example.edu",
                )
            )

        self.assertEqual([row.id for row in rows], [first_id, second_id])
        self.assertIsNone(rows[0].email)
        assert email_key is not None
        self.assertEqual(email_key.candidate_id, second_id)

    async def test_manual_fields_survive_merge_into_smaller_canonical_id(self) -> None:
        first_id = await self._create_candidate(
            name="自动候选",
            email="target@example.edu",
        )
        second_id = await self._create_candidate(
            name="手动候选",
            email="other@example.edu",
        )
        async with self.session_factory() as session:
            first = await session.get(CrawlCandidate, first_id)
            second = await session.get(CrawlCandidate, second_id)
            assert first is not None and second is not None
            first.title = "自动职称"
            first.recent_papers = ["自动论文"]
            first.source_kind = "profile_page"
            second.email = "target@example.edu"
            second.title = "手动职称"
            second.recent_papers = ["手动论文"]
            mark_candidate_fields_manual(
                second,
                ["email", "title", "recent_papers"],
            )
            canonical = await rebuild_candidate_identity_keys(session, second)
            await session.commit()

        self.assertEqual(canonical.id, first_id)
        self.assertEqual(canonical.title, "手动职称")
        self.assertEqual(canonical.recent_papers, ["手动论文"])
        self.assertEqual(
            canonical.field_sources["title"]["source_kind"],
            "manual",
        )

    async def test_candidate_can_bridge_two_existing_identity_components(self) -> None:
        first_id = await self._create_candidate(
            name="第一条",
            email="bridge@example.edu",
            profile_url="https://example.edu/first",
        )
        await self._create_candidate(
            name="第二条",
            email="second@example.edu",
            profile_url="https://example.edu/second",
        )
        await self._create_candidate(
            name="桥接条目",
            email="bridge@example.edu",
            profile_url="https://example.edu/second",
        )

        async with self.session_factory() as session:
            roots = list(
                await session.scalars(
                    select(CrawlCandidate).where(
                        CrawlCandidate.job_id == self.job_id,
                        canonical_candidate_clause(),
                    )
                )
            )
            keys = list(
                await session.scalars(
                    select(CrawlCandidateIdentityKey).where(
                        CrawlCandidateIdentityKey.job_id == self.job_id,
                    )
                )
            )

        self.assertEqual([row.id for row in roots], [first_id])
        self.assertEqual(len(keys), 4)
        self.assertEqual({key.candidate_id for key in keys}, {first_id})

    async def test_same_identity_in_different_jobs_never_merges(self) -> None:
        first_id = await self._create_candidate(
            name="任务一候选",
            email="shared@example.edu",
        )
        async with self.session_factory() as session:
            other_job = CrawlJob(
                university="另一所大学",
                school="计算机学院",
                start_url="https://other.example.edu/faculty",
            )
            session.add(other_job)
            await session.flush()
            other = CrawlCandidate(
                job_id=other_job.id,
                name="任务二候选",
                email="shared@example.edu",
            )
            session.add(other)
            await session.flush()
            other_root = await consolidate_candidate_identity(session, other)
            await session.commit()

        self.assertNotEqual(other_root.id, first_id)
        self.assertEqual(other_root.job_id, other_job.id)

    async def test_corrupt_alias_cycle_and_cross_job_target_fail_closed(self) -> None:
        first_id = await self._create_candidate(name="候选一")
        second_id = await self._create_candidate(name="候选二")
        async with self.session_factory() as session:
            first = await session.get(CrawlCandidate, first_id)
            second = await session.get(CrawlCandidate, second_id)
            assert first is not None and second is not None
            first.merged_into_candidate_id = second.id
            second.merged_into_candidate_id = first.id
            await session.flush()
            with self.assertRaisesRegex(RuntimeError, "循环"):
                await resolve_canonical_candidate(session, first)
            await session.rollback()

        async with self.session_factory() as session:
            other_job = CrawlJob(
                university="另一所大学",
                school="计算机学院",
                start_url="https://other.example.edu/faculty",
            )
            session.add(other_job)
            await session.flush()
            cross_job = CrawlCandidate(job_id=other_job.id, name="跨任务候选")
            session.add(cross_job)
            await session.flush()
            first = await session.get(CrawlCandidate, first_id)
            assert first is not None
            first.merged_into_candidate_id = cross_job.id
            await session.flush()
            with self.assertRaisesRegex(RuntimeError, "不属于同一任务"):
                await resolve_canonical_candidate(session, first)
