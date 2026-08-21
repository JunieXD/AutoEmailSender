from __future__ import annotations

import asyncio
from contextlib import closing
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from app.models import CrawlJob, CrawlJobKind, CrawlJobStatus, CrawlJobTriggerMode
from test.migrated_database import create_migrated_sqlite_database


class ProfessorInformationEnrichmentApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        from main import create_app

        cls.client = TestClient(create_app())

    @classmethod
    def tearDownClass(cls) -> None:
        cls.client.close()

    def setUp(self) -> None:
        from app.modules.crawler.runtime.profile_text_cache import profile_text_cache

        profile_text_cache.clear()
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = (
            Path(self.temp_dir.name) / "professor_information_enrichment_api.db"
        )
        os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{self.db_path.as_posix()}"
        os.environ["ENABLE_BACKGROUND_WORKERS"] = "0"
        create_migrated_sqlite_database(self.db_path)

        from app.core.config import get_settings
        from app.core.database import dispose_engine, get_engine, get_session_factory

        get_settings.cache_clear()
        if get_engine.cache_info().currsize:
            asyncio.run(dispose_engine())
        get_session_factory.cache_clear()
        get_settings.cache_clear()
        self.llm_profile_id = self._create_llm_profile()

    def tearDown(self) -> None:
        self.client.cookies.clear()
        from app.modules.crawler.runtime.profile_text_cache import profile_text_cache

        profile_text_cache.clear()
        from app.core.config import get_settings
        from app.core.database import dispose_engine, get_engine, get_session_factory

        if get_engine.cache_info().currsize:
            asyncio.run(dispose_engine())
        get_session_factory.cache_clear()
        get_settings.cache_clear()
        os.environ.pop("DATABASE_URL", None)
        os.environ.pop("ENABLE_BACKGROUND_WORKERS", None)
        self.temp_dir.cleanup()

    def test_single_job_is_hidden_from_task_lists_and_reports_active_state(
        self,
    ) -> None:
        professor_id = self._create_professor(
            name="单次补全导师",
            email="single@example.edu",
            profile_url="https://example.edu/single",
        )

        created = self.client.post(
            f"/api/professors/{professor_id}/information-enrichment",
            json={"llm_profile_id": self.llm_profile_id},
        )
        self.assertEqual(created.status_code, 201, msg=created.text)
        self.assertEqual(created.json()["trigger_mode"], "single")
        self.assertEqual(created.json()["status"], "queued")

        active = self.client.get(
            f"/api/professors/{professor_id}/information-enrichment/active",
        )
        self.assertEqual(active.status_code, 200, msg=active.text)
        self.assertTrue(active.json()["active"])
        self.assertEqual(active.json()["job"]["id"], created.json()["id"])

        information_jobs = self.client.get("/api/professor-information-enrichment-jobs")
        self.assertEqual(information_jobs.status_code, 200)
        self.assertEqual(information_jobs.json(), [])
        crawl_jobs = self.client.get("/api/crawl-jobs")
        self.assertEqual(crawl_jobs.status_code, 200)
        self.assertEqual(crawl_jobs.json(), [])

        with closing(sqlite3.connect(self.db_path)) as connection, connection:
            candidate_id = int(
                connection.execute(
                    "SELECT id FROM crawl_candidates WHERE job_id = ?",
                    (created.json()["id"],),
                ).fetchone()[0]
            )
        candidate_update = self.client.patch(
            f"/api/crawl-jobs/candidates/{candidate_id}",
            json={
                "name": "不应被普通抓取接口修改",
                "email": "mutated@example.edu",
                "recent_papers": [],
                "review_status": "pending",
            },
        )
        self.assertEqual(candidate_update.status_code, 404, msg=candidate_update.text)

    def test_task_center_information_enrichment_page_reports_complete_counts(
        self,
    ) -> None:
        async def seed_jobs() -> None:
            from app.core.database import get_session_factory

            async with get_session_factory()() as session:
                session.add_all(
                    [
                        CrawlJob(
                            university="示例大学",
                            school="计算机学院",
                            start_url=f"https://example.edu/enrichment/{index}",
                            job_kind=CrawlJobKind.PROFESSOR_ENRICHMENT.value,
                            trigger_mode=CrawlJobTriggerMode.BATCH.value,
                            task_center_visible=True,
                            display_name=f"信息补全任务 {index}",
                            llm_profile_id=self.llm_profile_id,
                            status=CrawlJobStatus.QUEUED.value,
                            progress_current=0,
                            progress_total=0,
                            agent_trace=[],
                        )
                        for index in range(51)
                    ]
                )
                await session.commit()

        asyncio.run(seed_jobs())

        response = self.client.get("/api/professor-information-enrichment-jobs")

        self.assertEqual(response.status_code, 200, msg=response.text)
        self.assertEqual(len(response.json()), 50)
        self.assertEqual(response.json()[0]["name"], "信息补全任务 50")

        page = self.client.get(
            "/api/professor-information-enrichment-jobs/page?offset=48&limit=8"
        )

        self.assertEqual(page.status_code, 200, msg=page.text)
        self.assertEqual(len(page.json()["items"]), 3)
        self.assertEqual(page.json()["total_count"], 51)
        self.assertEqual(page.json()["current_total_count"], 51)
        self.assertEqual(page.json()["items"][0]["name"], "信息补全任务 2")

        unpaged = self.client.get(
            "/api/professor-information-enrichment-jobs/page?limit=1&unpaged=true"
        )
        self.assertEqual(unpaged.status_code, 200, msg=unpaged.text)
        self.assertEqual(len(unpaged.json()["items"]), 51)

    def test_task_center_information_enrichment_page_filters_and_counts_views(
        self,
    ) -> None:
        async def seed_jobs() -> list[int]:
            from app.core.database import get_session_factory

            async with get_session_factory()() as session:
                jobs = [
                    CrawlJob(
                        university="示例大学",
                        school="计算机学院",
                        start_url=f"https://example.edu/enrichment-filter/{index}",
                        job_kind=CrawlJobKind.PROFESSOR_ENRICHMENT.value,
                        trigger_mode=CrawlJobTriggerMode.BATCH.value,
                        task_center_visible=True,
                        display_name=name,
                        llm_profile_id=self.llm_profile_id,
                        status=status,
                        progress_current=progress_current,
                        progress_total=4,
                        agent_trace=[],
                    )
                    for index, (name, status, progress_current) in enumerate(
                        [
                            ("重点导师补全", CrawlJobStatus.RUNNING.value, 1),
                            ("普通导师补全", CrawlJobStatus.FAILED.value, 3),
                            ("回收任务", CrawlJobStatus.COMPLETED.value, 4),
                        ]
                    )
                ]
                session.add_all(jobs)
                await session.flush()
                job_ids = [job.id for job in jobs]
                jobs[2].deleted_at = jobs[2].updated_at
                await session.commit()
                return job_ids

        job_ids = asyncio.run(seed_jobs())

        current = self.client.get(
            "/api/professor-information-enrichment-jobs/page"
            "?sort_key=progress&sort_direction=asc"
        )
        self.assertEqual(current.status_code, 200, msg=current.text)
        self.assertEqual(current.json()["total_count"], 2)
        self.assertEqual(current.json()["current_total_count"], 2)
        self.assertEqual(
            [item["id"] for item in current.json()["items"]],
            [job_ids[0], job_ids[1]],
        )

        failed = self.client.get(
            "/api/professor-information-enrichment-jobs/page?status=failed"
        )
        self.assertEqual(failed.status_code, 200, msg=failed.text)
        self.assertEqual(failed.json()["total_count"], 1)
        self.assertEqual(failed.json()["items"][0]["id"], job_ids[1])
        self.assertEqual(failed.json()["current_total_count"], 2)

        search = self.client.get(
            "/api/professor-information-enrichment-jobs/page?keyword=重点"
        )
        self.assertEqual(search.status_code, 200, msg=search.text)
        self.assertEqual(search.json()["items"][0]["id"], job_ids[0])

        trash = self.client.get(
            "/api/professor-information-enrichment-jobs/page?view=trash"
        )
        self.assertEqual(trash.status_code, 200, msg=trash.text)
        self.assertEqual(trash.json()["total_count"], 1)
        self.assertEqual(trash.json()["current_total_count"], 2)
        self.assertEqual(trash.json()["items"][0]["id"], job_ids[2])

    def test_batch_job_retains_conflicts_as_skipped_and_supports_trash_actions(
        self,
    ) -> None:
        professor_id = self._create_professor(
            name="批量补全导师",
            email="batch@example.edu",
            profile_url="https://example.edu/batch",
        )
        first = self.client.post(
            f"/api/professors/{professor_id}/information-enrichment",
            json={"llm_profile_id": self.llm_profile_id},
        )
        self.assertEqual(first.status_code, 201, msg=first.text)

        batch = self.client.post(
            "/api/professor-information-enrichment-jobs",
            json={
                "professor_ids": [professor_id],
                "llm_profile_id": self.llm_profile_id,
            },
        )
        self.assertEqual(batch.status_code, 201, msg=batch.text)
        payload = batch.json()
        self.assertEqual(payload["status"], "completed")
        self.assertEqual(payload["skipped_count"], 1)
        job_id = payload["id"]

        items = self.client.get(
            f"/api/professor-information-enrichment-jobs/{job_id}/items",
        )
        self.assertEqual(items.status_code, 200, msg=items.text)
        self.assertEqual(items.json()[0]["status"], "skipped")
        self.assertEqual(items.json()[0]["skip_reason"], "已有信息补全正在进行")
        self.assertEqual(items.json()[0]["skip_reason_code"], "ENRICHMENT_IN_PROGRESS")
        self.assertTrue(items.json()[0]["skip_recoverable"])
        self.assertEqual(items.json()[0]["suggested_action"], "enrichment.jobs.list")
        self.assertEqual(
            payload["skip_reasons"],
            [
                {
                    "code": "ENRICHMENT_IN_PROGRESS",
                    "count": 1,
                    "message": "已有信息补全正在进行",
                    "recoverable": True,
                    "suggested_action": "enrichment.jobs.list",
                }
            ],
        )

        deleted = self.client.delete(
            f"/api/professor-information-enrichment-jobs/{job_id}",
        )
        self.assertEqual(deleted.status_code, 200, msg=deleted.text)
        self.assertIsNotNone(deleted.json()["job"]["deleted_at"])
        self.assertEqual(
            self.client.get("/api/professor-information-enrichment-jobs").json(),
            [],
        )
        trash = self.client.get(
            "/api/professor-information-enrichment-jobs",
            params={"view": "trash"},
        )
        self.assertEqual([item["id"] for item in trash.json()], [job_id])

        restored = self.client.post(
            f"/api/professor-information-enrichment-jobs/{job_id}/restore",
        )
        self.assertEqual(restored.status_code, 200, msg=restored.text)
        self.assertIsNone(restored.json()["job"]["deleted_at"])

    def test_cancel_clears_cached_profile_text_for_canceled_items(self) -> None:
        from app.modules.crawler.runtime.profile_text_cache import profile_text_cache

        professor_id = self._create_professor(
            name="取消补全导师",
            email="cancel@example.edu",
            profile_url="https://example.edu/cancel",
        )
        created = self.client.post(
            "/api/professor-information-enrichment-jobs",
            json={
                "professor_ids": [professor_id],
                "llm_profile_id": self.llm_profile_id,
            },
        )
        self.assertEqual(created.status_code, 201, msg=created.text)
        job_id = int(created.json()["id"])
        with closing(sqlite3.connect(self.db_path)) as connection, connection:
            candidate_id = int(
                connection.execute(
                    "SELECT candidate_id FROM crawl_candidate_enrichment_tasks WHERE job_id = ?",
                    (job_id,),
                ).fetchone()[0]
            )
        cache_key = (999, job_id, candidate_id, "https://example.edu/cancel")
        profile_text_cache.put(cache_key, "cached profile")

        canceled = self.client.post(
            f"/api/professor-information-enrichment-jobs/{job_id}/cancel",
        )

        self.assertEqual(canceled.status_code, 200, msg=canceled.text)
        self.assertNotIn(cache_key, profile_text_cache)
        items = self.client.get(
            f"/api/professor-information-enrichment-jobs/{job_id}/items",
        )
        self.assertEqual(items.status_code, 200, msg=items.text)
        self.assertEqual(items.json()[0]["status"], "canceled")

    def _create_llm_profile(self) -> int:
        response = self.client.post(
            "/api/llm-profiles",
            json={
                "name": "测试模型",
                "provider": "openai",
                "api_base_url": "https://api.example.com/v1",
                "api_key": "sk-test",
                "model_name": "gpt-test",
                "is_default": True,
            },
        )
        self.assertEqual(response.status_code, 201, msg=response.text)
        return int(response.json()["id"])

    def _create_professor(self, *, name: str, email: str, profile_url: str) -> int:
        response = self.client.post(
            "/api/professors",
            json={
                "name": name,
                "email": email,
                "title": None,
                "university": "示例大学",
                "school": "计算机学院",
                "department": None,
                "research_direction": None,
                "recent_papers": [],
                "profile_url": profile_url,
                "source_url": None,
                "personal_note": None,
                "tag_ids": [],
            },
        )
        self.assertEqual(response.status_code, 201, msg=response.text)
        return int(response.json()["id"])


if __name__ == "__main__":
    unittest.main()
