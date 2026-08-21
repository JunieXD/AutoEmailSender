from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
import os
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient


class CrawlJobApprovalTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.temp_dir = tempfile.TemporaryDirectory()
        cls.db_path = Path(cls.temp_dir.name) / "crawl_job_approval.db"
        os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{cls.db_path.as_posix()}"
        os.environ["ENABLE_BACKGROUND_WORKERS"] = "0"

        from app.core.config import get_settings
        from app.core.database import dispose_engine, get_engine, get_session_factory
        from main import create_app

        get_settings.cache_clear()
        if get_engine.cache_info().currsize:
            asyncio.run(dispose_engine())
        get_session_factory.cache_clear()
        get_settings.cache_clear()

        asyncio.run(cls._create_schema())
        cls.client = TestClient(create_app())

    @classmethod
    def tearDownClass(cls) -> None:
        cls.client.close()

        from app.core.config import get_settings
        from app.core.database import dispose_engine, get_engine, get_session_factory

        if get_engine.cache_info().currsize:
            asyncio.run(dispose_engine())
        get_session_factory.cache_clear()
        get_settings.cache_clear()
        os.environ.pop("DATABASE_URL", None)
        os.environ.pop("ENABLE_BACKGROUND_WORKERS", None)
        cls.temp_dir.cleanup()

    def setUp(self) -> None:
        asyncio.run(self._clear_database())

    def test_approve_partially_imports_valid_candidates_and_skips_missing_email(
        self,
    ) -> None:
        job_id, valid_candidate_id, missing_email_candidate_id = asyncio.run(
            self._create_crawl_candidates(
                [
                    {"name": "有效邮箱导师", "email": "Valid.Teacher@Example.EDU"},
                    {"name": "无邮箱导师", "email": None},
                ]
            )
        )

        response = self.client.post(
            f"/api/crawl-jobs/{job_id}/approve",
            json={"candidate_ids": [valid_candidate_id, missing_email_candidate_id]},
        )

        self.assertEqual(response.status_code, 200, msg=response.text)
        self.assertEqual(
            response.json(),
            {
                "inserted_count": 1,
                "updated_count": 0,
                "skipped_count": 1,
                "message": "审核完成：新增 1 位导师，更新 0 位导师，跳过 1 位候选。",
            },
        )
        snapshot = asyncio.run(
            self._load_job_candidate_and_professor_snapshot(
                job_id,
                [valid_candidate_id, missing_email_candidate_id],
            )
        )
        self.assertEqual(snapshot["job_status"], "partially_completed")
        self.assertEqual(snapshot["professor_count"], 1)
        self.assertEqual(snapshot["professor_emails"], ["valid.teacher@example.edu"])
        self.assertEqual(
            snapshot["candidates"][valid_candidate_id]["review_status"], "accepted"
        )
        self.assertIsNotNone(snapshot["candidates"][valid_candidate_id]["professor_id"])
        self.assertEqual(
            snapshot["candidates"][missing_email_candidate_id]["review_status"],
            "pending",
        )
        self.assertIsNone(
            snapshot["candidates"][missing_email_candidate_id]["professor_id"]
        )

    def test_approval_treats_same_email_aliases_as_one_new_professor(self) -> None:
        job_id, first_candidate_id, second_candidate_id = asyncio.run(
            self._create_crawl_candidates(
                [
                    {"name": "张三", "email": "same@example.edu"},
                    {"name": "Zhang San", "email": "SAME@example.edu"},
                ]
            )
        )
        asyncio.run(
            self._consolidate_candidates(
                first_candidate_id,
                second_candidate_id,
            )
        )

        candidates_response = self.client.get(f"/api/crawl-jobs/{job_id}/candidates")
        self.assertEqual(
            candidates_response.status_code, 200, msg=candidates_response.text
        )
        self.assertEqual(len(candidates_response.json()), 1)

        response = self.client.post(
            f"/api/crawl-jobs/{job_id}/approve",
            json={"candidate_ids": [first_candidate_id, second_candidate_id]},
        )

        self.assertEqual(response.status_code, 200, msg=response.text)
        self.assertEqual(
            response.json(),
            {
                "inserted_count": 1,
                "updated_count": 0,
                "skipped_count": 0,
                "message": "审核完成：新增 1 位导师，更新 0 位导师，跳过 0 位候选。",
            },
        )

    def test_manual_email_clear_removes_identity_from_hidden_aliases(self) -> None:
        _, first_candidate_id, second_candidate_id = asyncio.run(
            self._create_crawl_candidates(
                [
                    {"name": "张三", "email": "wrong@example.edu"},
                    {"name": "Zhang San", "email": "WRONG@example.edu"},
                ]
            )
        )
        asyncio.run(
            self._consolidate_candidates(
                first_candidate_id,
                second_candidate_id,
            )
        )

        response = self.client.patch(
            f"/api/crawl-jobs/candidates/{second_candidate_id}",
            json={
                "name": "张三",
                "email": None,
                "title": "Professor",
                "university": "示例大学",
                "school": "计算机学院",
                "department": "计算机科学系",
                "research_direction": "智能系统",
                "recent_papers": [],
                "profile_url": None,
                "source_url": None,
                "review_status": "pending",
            },
        )

        self.assertEqual(response.status_code, 200, msg=response.text)
        self.assertEqual(response.json()["id"], first_candidate_id)
        self.assertIsNone(response.json()["email"])
        self.assertFalse(
            asyncio.run(
                self._identity_key_exists(
                    job_id=response.json()["job_id"],
                    key_type="email",
                    normalized_value="wrong@example.edu",
                )
            )
        )

    def test_concurrent_jobs_approve_same_email_without_duplicate_professors(
        self,
    ) -> None:
        jobs_and_candidates = [
            asyncio.run(
                self._create_crawl_candidates(
                    [{"name": f"同邮箱候选 {index}", "email": "race@example.edu"}]
                )
            )
            for index in range(8)
        ]

        def approve(item: tuple[int, ...]):
            job_id, candidate_id = item
            return self.client.post(
                f"/api/crawl-jobs/{job_id}/approve",
                json={"candidate_ids": [candidate_id]},
            )

        with ThreadPoolExecutor(max_workers=8) as executor:
            responses = list(executor.map(approve, jobs_and_candidates))

        self.assertTrue(
            all(response.status_code == 200 for response in responses),
            msg=[(response.status_code, response.text) for response in responses],
        )
        self.assertEqual(
            sum(response.json()["inserted_count"] for response in responses),
            1,
        )
        self.assertEqual(
            sum(response.json()["updated_count"] for response in responses),
            7,
        )

    async def _create_crawl_candidates(
        self, candidates: list[dict[str, str | None]]
    ) -> tuple[int, ...]:
        from app.core.database import get_session_factory
        from app.models import CrawlCandidate, CrawlJob, CrawlJobStatus

        async with get_session_factory()() as session:
            job = CrawlJob(
                university="示例大学",
                school="计算机学院",
                start_url="https://example.edu/faculty",
                start_urls=["https://example.edu/faculty"],
                status=CrawlJobStatus.NEEDS_REVIEW.value,
                progress_current=1,
                progress_total=1,
            )
            session.add(job)
            await session.flush()
            candidate_ids: list[int] = []
            for candidate_data in candidates:
                candidate = CrawlCandidate(
                    job_id=job.id,
                    name=candidate_data["name"] or "候选导师",
                    email=candidate_data["email"],
                    title="Professor",
                    university="示例大学",
                    school="计算机学院",
                    department="计算机科学系",
                    research_direction="智能系统",
                    recent_papers=[],
                    confidence=0.9,
                )
                session.add(candidate)
                await session.flush()
                candidate_ids.append(candidate.id)
            await session.commit()
            return (job.id, *candidate_ids)

    async def _load_job_candidate_and_professor_snapshot(
        self,
        job_id: int,
        candidate_ids: list[int],
    ) -> dict[str, object]:
        from sqlalchemy import func, select

        from app.core.database import get_session_factory
        from app.models import CrawlCandidate, CrawlJob, Professor

        async with get_session_factory()() as session:
            job = await session.get(CrawlJob, job_id)
            candidates = list(
                (
                    await session.execute(
                        select(CrawlCandidate).where(
                            CrawlCandidate.id.in_(candidate_ids)
                        )
                    )
                ).scalars()
            )
            professor_count = await session.scalar(
                select(func.count()).select_from(Professor)
            )
            professor_emails = list(
                (
                    await session.execute(
                        select(Professor.email).order_by(Professor.email.asc())
                    )
                ).scalars()
            )
            return {
                "job_status": job.status if job is not None else None,
                "professor_count": int(professor_count or 0),
                "professor_emails": professor_emails,
                "candidates": {
                    candidate.id: {
                        "review_status": candidate.review_status,
                        "professor_id": candidate.professor_id,
                    }
                    for candidate in candidates
                },
            }

    async def _consolidate_candidates(self, *candidate_ids: int) -> None:
        from app.core.database import get_session_factory
        from app.models import CrawlCandidate
        from app.modules.crawler.candidate_identity import (
            consolidate_candidate_identity,
        )

        async with get_session_factory()() as session:
            for candidate_id in candidate_ids:
                candidate = await session.get(CrawlCandidate, candidate_id)
                assert candidate is not None
                await consolidate_candidate_identity(session, candidate)
            await session.commit()

    async def _identity_key_exists(
        self,
        *,
        job_id: int,
        key_type: str,
        normalized_value: str,
    ) -> bool:
        from sqlalchemy import select

        from app.core.database import get_session_factory
        from app.models import CrawlCandidateIdentityKey

        async with get_session_factory()() as session:
            key = await session.scalar(
                select(CrawlCandidateIdentityKey.id).where(
                    CrawlCandidateIdentityKey.job_id == job_id,
                    CrawlCandidateIdentityKey.key_type == key_type,
                    CrawlCandidateIdentityKey.normalized_value == normalized_value,
                )
            )
            return key is not None

    async def _clear_database(self) -> None:
        from sqlalchemy import delete

        from app.core.database import get_session_factory
        from app.models import CrawlCandidate, CrawlJob, OperationLog, Professor

        async with get_session_factory()() as session:
            for model in [OperationLog, CrawlCandidate, CrawlJob, Professor]:
                await session.execute(delete(model))
            await session.commit()

    @classmethod
    async def _create_schema(cls) -> None:
        from app.core.database import get_engine
        from app.models import Base

        async with get_engine().begin() as connection:
            await connection.run_sync(Base.metadata.create_all)


if __name__ == "__main__":
    unittest.main()
