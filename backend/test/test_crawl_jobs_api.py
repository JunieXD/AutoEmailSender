from __future__ import annotations

import asyncio
from contextlib import closing
import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from uuid import UUID

from fastapi.testclient import TestClient
from test.migrated_database import create_migrated_sqlite_database


class CrawlJobsApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        from main import create_app

        cls.client = TestClient(create_app())

    @classmethod
    def tearDownClass(cls) -> None:
        cls.client.close()

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "crawl_jobs_api_test.db"
        os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{self.db_path.as_posix()}"
        os.environ["ENABLE_BACKGROUND_WORKERS"] = "0"
        create_migrated_sqlite_database(self.db_path)
        self.getaddrinfo_patcher = patch(
            "app.modules.crawler.pages.url_safety.socket.getaddrinfo",
            return_value=[
                (0, 0, 0, "", ("93.184.216.34", 443)),
            ],
        )
        self.getaddrinfo_patcher.start()

        from app.core.config import get_settings
        from app.core.database import dispose_engine, get_engine, get_session_factory

        get_settings.cache_clear()
        if get_engine.cache_info().currsize:
            asyncio.run(dispose_engine())
        get_session_factory.cache_clear()
        get_settings.cache_clear()

    def tearDown(self) -> None:
        self.client.cookies.clear()
        from app.core.config import get_settings
        from app.core.database import dispose_engine, get_engine, get_session_factory

        if get_engine.cache_info().currsize:
            asyncio.run(dispose_engine())
        get_session_factory.cache_clear()
        get_settings.cache_clear()
        os.environ.pop("DATABASE_URL", None)
        os.environ.pop("ENABLE_BACKGROUND_WORKERS", None)
        self.getaddrinfo_patcher.stop()
        self.temp_dir.cleanup()

    def test_create_crawl_job_rejects_non_http_url(self) -> None:
        response = self.client.post(
            "/api/crawl-jobs",
            json={
                "university": "示例大学",
                "school": "计算机学院",
                "start_url": "ftp://example.edu/faculty",
                "llm_profile_id": None,
            },
        )

        self.assertEqual(response.status_code, 422)

    def test_create_crawl_job_rejects_localhost_url(self) -> None:
        response = self.client.post(
            "/api/crawl-jobs",
            json={
                "university": "示例大学",
                "school": "计算机学院",
                "start_url": "http://127.0.0.1/faculty",
                "llm_profile_id": None,
            },
        )

        self.assertEqual(response.status_code, 422)

    def test_create_crawl_job_defaults_to_list_entry_type(self) -> None:
        response = self.client.post(
            "/api/crawl-jobs",
            json={
                "university": "示例大学",
                "school": "计算机学院",
                "start_url": "https://example.edu/faculty",
                "llm_profile_id": None,
            },
        )

        self.assertEqual(response.status_code, 201, msg=response.text)
        self.assertEqual(response.json()["entry_type"], "list")
        import sqlite3

        with closing(sqlite3.connect(self.db_path)) as connection, connection:
            task = connection.execute(
                "SELECT parent_url, discovery_reason, expansion_mode, depth FROM crawl_page_tasks WHERE job_id = ?",
                (response.json()["id"],),
            ).fetchone()
        self.assertEqual(task, (None, "start", "entry", 0))

    def test_create_crawl_job_accepts_multiple_start_urls(self) -> None:
        response = self.client.post(
            "/api/crawl-jobs",
            json={
                "university": "示例大学",
                "school": "计算机学院",
                "start_url": "https://example.edu/faculty",
                "start_urls": [
                    " https://example.edu/faculty ",
                    "https://example.edu/faculty?page=2",
                    "https://example.edu/faculty",
                ],
                "llm_profile_id": None,
            },
        )

        self.assertEqual(response.status_code, 201, msg=response.text)
        self.assertEqual(response.json()["start_url"], "https://example.edu/faculty")
        self.assertEqual(
            response.json()["start_urls"],
            [
                "https://example.edu/faculty",
                "https://example.edu/faculty?page=2",
            ],
        )

    def test_create_crawl_job_deduplicates_start_urls_by_normalized_url(self) -> None:
        try:
            response = self.client.post(
                "/api/crawl-jobs",
                json={
                    "university": "示例大学",
                    "school": "计算机学院",
                    "start_url": "https://example.edu/faculty?utm_source=first",
                    "start_urls": [
                        "https://example.edu/faculty?utm_source=first",
                        "https://example.edu/faculty?utm_source=second",
                    ],
                    "llm_profile_id": None,
                },
            )
        except (
            Exception
        ) as exc:  # pragma: no cover - keeps the red test as an assertion failure.
            self.fail(f"create crawl job raised {exc!r}")

        self.assertEqual(response.status_code, 201, msg=response.text)
        job_id = response.json()["id"]
        self.assertEqual(
            self._list_page_task_statuses(job_id),
            [("https://example.edu/faculty", "pending")],
        )

    def test_list_crawl_jobs_allows_limit_for_diagnostics_selector(self) -> None:
        for index in range(3):
            response = self.client.post(
                "/api/crawl-jobs",
                json={
                    "university": "示例大学",
                    "school": f"学院 {index}",
                    "start_url": f"https://example.edu/faculty/{index}",
                    "llm_profile_id": None,
                },
            )
            self.assertEqual(response.status_code, 201, msg=response.text)

        response = self.client.get("/api/crawl-jobs?limit=2")

        self.assertEqual(response.status_code, 200, msg=response.text)
        payload = response.json()
        self.assertEqual(len(payload), 2)
        self.assertEqual(payload[0]["school"], "学院 2")
        self.assertEqual(payload[1]["school"], "学院 1")

    def test_task_center_crawl_job_page_reports_complete_counts(self) -> None:
        for index in range(51):
            response = self.client.post(
                "/api/crawl-jobs",
                json={
                    "university": "示例大学",
                    "school": f"任务中心学院 {index}",
                    "start_url": f"https://example.edu/task-center/{index}",
                    "llm_profile_id": None,
                },
            )
            self.assertEqual(response.status_code, 201, msg=response.text)

        response = self.client.get("/api/crawl-jobs")

        self.assertEqual(response.status_code, 200, msg=response.text)
        self.assertEqual(len(response.json()), 50)
        self.assertEqual(response.json()[0]["school"], "任务中心学院 50")

        page = self.client.get("/api/crawl-jobs/page?offset=48&limit=8")

        self.assertEqual(page.status_code, 200, msg=page.text)
        self.assertEqual(len(page.json()["items"]), 3)
        self.assertEqual(page.json()["total_count"], 51)
        self.assertEqual(page.json()["current_total_count"], 51)
        self.assertEqual(page.json()["items"][0]["school"], "任务中心学院 2")

        unpaged = self.client.get("/api/crawl-jobs/page?limit=1&unpaged=true")
        self.assertEqual(unpaged.status_code, 200, msg=unpaged.text)
        self.assertEqual(len(unpaged.json()["items"]), 51)

    def test_task_center_crawl_job_page_filters_sorts_and_counts_views(self) -> None:
        jobs = []
        for university, school, url in [
            ("甲大学", "计算机学院", "https://example.edu/alpha"),
            ("乙大学", "自动化学院", "https://example.edu/beta"),
            ("丙大学", "材料学院", "https://example.edu/gamma"),
        ]:
            response = self.client.post(
                "/api/crawl-jobs",
                json={
                    "university": university,
                    "school": school,
                    "start_url": url,
                    "llm_profile_id": None,
                },
            )
            self.assertEqual(response.status_code, 201, msg=response.text)
            jobs.append(response.json())

        with closing(sqlite3.connect(self.db_path)) as connection, connection:
            connection.execute(
                """
                UPDATE crawl_jobs
                SET status = ?, progress_current = ?, progress_total = ?,
                    updated_at = ?, agent_trace = ?
                WHERE id = ?
                """,
                (
                    "running",
                    1,
                    4,
                    "2026-08-20 10:00:00.000000",
                    json.dumps([{"summary": "正在解析重点教师页面"}]),
                    jobs[0]["id"],
                ),
            )
            connection.execute(
                """
                UPDATE crawl_jobs
                SET status = ?, progress_current = ?, progress_total = ?,
                    updated_at = ?, agent_trace = ?
                WHERE id = ?
                """,
                (
                    "failed",
                    3,
                    4,
                    "2026-08-20 11:00:00.000000",
                    json.dumps([{"summary": "连接失败"}]),
                    jobs[1]["id"],
                ),
            )
            connection.execute(
                """
                UPDATE crawl_jobs
                SET status = ?, deleted_at = ?
                WHERE id = ?
                """,
                ("completed", "2026-08-20 12:00:00.000000", jobs[2]["id"]),
            )

        current = self.client.get(
            "/api/crawl-jobs/page?limit=8&sort_key=progress&sort_direction=asc"
        )
        self.assertEqual(current.status_code, 200, msg=current.text)
        self.assertEqual(current.json()["total_count"], 2)
        self.assertEqual(current.json()["current_total_count"], 2)
        self.assertEqual(
            [item["id"] for item in current.json()["items"]],
            [jobs[0]["id"], jobs[1]["id"]],
        )

        failed = self.client.get("/api/crawl-jobs/page?status=failed")
        self.assertEqual(failed.status_code, 200, msg=failed.text)
        self.assertEqual(failed.json()["total_count"], 1)
        self.assertEqual(failed.json()["items"][0]["id"], jobs[1]["id"])
        self.assertEqual(failed.json()["current_total_count"], 2)

        school_search = self.client.get(
            "/api/crawl-jobs/page?keyword=计算机&search_scopes=school"
        )
        self.assertEqual(school_search.status_code, 200, msg=school_search.text)
        self.assertEqual(school_search.json()["items"][0]["id"], jobs[0]["id"])

        event_search = self.client.get(
            "/api/crawl-jobs/page?keyword=重点教师&search_scopes=event"
        )
        self.assertEqual(event_search.status_code, 200, msg=event_search.text)
        self.assertEqual(event_search.json()["items"][0]["id"], jobs[0]["id"])

        trash = self.client.get("/api/crawl-jobs/page?view=trash")
        self.assertEqual(trash.status_code, 200, msg=trash.text)
        self.assertEqual(trash.json()["total_count"], 1)
        self.assertEqual(trash.json()["current_total_count"], 2)
        self.assertEqual(trash.json()["items"][0]["id"], jobs[2]["id"])

        invalid_scope = self.client.get(
            "/api/crawl-jobs/page?keyword=x&search_scopes=unknown"
        )
        self.assertEqual(invalid_scope.status_code, 400, msg=invalid_scope.text)

    def test_crawl_job_delete_restore_and_trash_view(self) -> None:
        blocked = self.client.post(
            "/api/crawl-jobs",
            json={
                "university": "示例大学",
                "school": "计算机学院",
                "start_url": "https://example.edu/faculty",
                "llm_profile_id": None,
            },
        )
        self.assertEqual(blocked.status_code, 201, msg=blocked.text)
        blocked_job_id = blocked.json()["id"]

        blocked = self.client.post(f"/api/crawl-jobs/{blocked_job_id}/delete")
        self.assertEqual(blocked.status_code, 400)
        self.assertIn("请先中止/取消任务后再删除", blocked.json()["detail"])

        for status in [
            "needs_review",
            "partially_completed",
            "completed",
            "failed",
            "canceled",
        ]:
            created = self.client.post(
                "/api/crawl-jobs",
                json={
                    "university": f"示例大学-{status}",
                    "school": "计算机学院",
                    "start_url": "https://example.edu/faculty",
                    "llm_profile_id": None,
                },
            )
            self.assertEqual(created.status_code, 201, msg=created.text)
            job_id = created.json()["id"]
            if status == "canceled":
                canceled = self.client.post(f"/api/crawl-jobs/{job_id}/cancel")
                self.assertEqual(canceled.status_code, 200, msg=canceled.text)
            else:
                self._set_job_status(job_id, status)

            deleted = self.client.post(f"/api/crawl-jobs/{job_id}/delete")
            self.assertEqual(deleted.status_code, 200, msg=deleted.text)
            self.assertIsNotNone(deleted.json()["deleted_at"])

            repeated_delete = self.client.post(f"/api/crawl-jobs/{job_id}/delete")
            self.assertEqual(repeated_delete.status_code, 200, msg=repeated_delete.text)

        canceled = self.client.post(f"/api/crawl-jobs/{blocked_job_id}/cancel")
        self.assertEqual(canceled.status_code, 200, msg=canceled.text)
        deleted = self.client.post(f"/api/crawl-jobs/{blocked_job_id}/delete")
        self.assertEqual(deleted.status_code, 200, msg=deleted.text)
        self.assertIsNotNone(deleted.json()["deleted_at"])

        current = self.client.get("/api/crawl-jobs")
        self.assertEqual(current.status_code, 200)
        self.assertEqual(current.json(), [])

        trash = self.client.get("/api/crawl-jobs", params={"view": "trash"})
        self.assertEqual(trash.status_code, 200)
        self.assertEqual(len(trash.json()), 6)

        restored = self.client.post(f"/api/crawl-jobs/{blocked_job_id}/restore")
        self.assertEqual(restored.status_code, 200, msg=restored.text)
        self.assertIsNone(restored.json()["deleted_at"])

        repeated_restore = self.client.post(f"/api/crawl-jobs/{blocked_job_id}/restore")
        self.assertEqual(repeated_restore.status_code, 200, msg=repeated_restore.text)

    def test_create_crawl_job_rejects_unsafe_start_urls_item(self) -> None:
        response = self.client.post(
            "/api/crawl-jobs",
            json={
                "university": "示例大学",
                "school": "计算机学院",
                "start_url": "https://example.edu/faculty",
                "start_urls": [
                    "https://example.edu/faculty",
                    "http://127.0.0.1/faculty",
                ],
                "llm_profile_id": None,
            },
        )

        self.assertEqual(response.status_code, 422)

    def test_create_crawl_job_accepts_profile_entry_type(self) -> None:
        response = self.client.post(
            "/api/crawl-jobs",
            json={
                "university": "示例大学",
                "school": "计算机学院",
                "start_url": "https://example.edu/faculty/zhang",
                "entry_type": "profile",
                "llm_profile_id": None,
            },
        )

        self.assertEqual(response.status_code, 201, msg=response.text)
        self.assertEqual(response.json()["entry_type"], "profile")
        import sqlite3

        with closing(sqlite3.connect(self.db_path)) as connection, connection:
            task = connection.execute(
                "SELECT discovery_reason, expansion_mode, depth FROM crawl_page_tasks WHERE job_id = ?",
                (response.json()["id"],),
            ).fetchone()
        self.assertEqual(task, ("start", "none", 0))

    def test_create_crawl_job_allows_domain_without_dns_resolution(self) -> None:
        with patch(
            "app.modules.crawler.pages.url_safety.socket.getaddrinfo",
            side_effect=AssertionError(
                "Creating a crawl job should not resolve domain names"
            ),
        ):
            response = self.client.post(
                "/api/crawl-jobs",
                json={
                    "university": "江西财经大学",
                    "school": "会计学院",
                    "start_url": "https://cai.jxufe.edu.cn/lists/26.html",
                    "llm_profile_id": None,
                },
            )

        self.assertEqual(response.status_code, 201, msg=response.text)
        self.assertEqual(
            response.json()["start_url"], "https://cai.jxufe.edu.cn/lists/26.html"
        )

    def test_create_crawl_job_creates_initial_run(self) -> None:
        response = self.client.post(
            "/api/crawl-jobs",
            json={
                "university": "示例大学",
                "school": "计算机学院",
                "start_url": "https://example.edu/faculty",
                "llm_profile_id": None,
            },
        )

        self.assertEqual(response.status_code, 201, msg=response.text)
        job_id = response.json()["id"]
        runs = self._list_job_runs(job_id)

        self.assertEqual(len(runs), 1)
        self.assertEqual(runs[0]["attempt_number"], 1)
        self.assertEqual(runs[0]["status"], "queued")
        self.assertEqual(self._get_job_current_run_id(job_id), runs[0]["id"])

    def test_crawl_job_review_flow(self) -> None:
        create_response = self.client.post(
            "/api/crawl-jobs",
            json={
                "university": "示例大学",
                "school": "计算机学院",
                "start_url": "https://example.edu/faculty",
                "llm_profile_id": None,
            },
        )
        self.assertEqual(create_response.status_code, 201, msg=create_response.text)
        job = create_response.json()
        self.assertEqual(job["status"], "queued")
        self.assertEqual(job["entry_type"], "list")

        self._seed_page_and_candidates(job["id"])
        self._set_job_status(job["id"], "needs_review")
        self._set_job_trace(job["id"], [{"summary": "Agent 已完成入口页面分析"}])

        list_response = self.client.get("/api/crawl-jobs")
        self.assertEqual(list_response.status_code, 200)
        list_job = list_response.json()[0]
        self.assertEqual(list_job["id"], job["id"])
        self.assertEqual(list_job["page_count"], 1)
        self.assertEqual(list_job["candidate_count"], 3)
        self.assertEqual(list_job["latest_event_message"], "Agent 已完成入口页面分析")
        self.assertEqual(list_job["entry_type"], "list")

        detail_response = self.client.get(f"/api/crawl-jobs/{job['id']}")
        self.assertEqual(detail_response.status_code, 200)
        detail_job = detail_response.json()
        self.assertEqual(detail_job["start_url"], "https://example.edu/faculty")
        self.assertEqual(detail_job["page_count"], 1)
        self.assertEqual(detail_job["candidate_count"], 3)
        self.assertEqual(detail_job["latest_event_message"], "Agent 已完成入口页面分析")
        self.assertEqual(detail_job["entry_type"], "list")

        pages_response = self.client.get(f"/api/crawl-jobs/{job['id']}/pages")
        self.assertEqual(pages_response.status_code, 200)
        self.assertEqual(pages_response.json()[0]["url"], "https://example.edu/faculty")

        candidates_response = self.client.get(f"/api/crawl-jobs/{job['id']}/candidates")
        self.assertEqual(candidates_response.status_code, 200)
        candidates = candidates_response.json()
        self.assertEqual(
            [item["name"] for item in candidates],
            ["高分导师", "低分导师", "无邮箱导师"],
        )
        self.assertEqual(candidates[1]["recent_papers"], [])

        patch_response = self.client.patch(
            f"/api/crawl-jobs/candidates/{candidates[1]['id']}",
            json={
                "name": "低分导师更新",
                "email": "low@example.edu",
                "title": "Associate Professor",
                "university": "示例大学",
                "school": "计算机学院",
                "department": "CS",
                "research_direction": "信息抽取",
                "recent_papers": ["Paper X"],
                "profile_url": "https://example.edu/low",
                "source_url": "https://example.edu/faculty",
                "review_status": "pending",
            },
        )
        self.assertEqual(patch_response.status_code, 200, msg=patch_response.text)
        self.assertEqual(patch_response.json()["name"], "低分导师更新")

        no_email_patch_response = self.client.patch(
            f"/api/crawl-jobs/candidates/{candidates[2]['id']}",
            json={
                "name": "无邮箱导师更新",
                "email": "no-email@example.edu",
                "title": "Professor",
                "university": "示例大学",
                "school": "计算机学院",
                "department": "CS",
                "research_direction": "系统",
                "recent_papers": [],
                "profile_url": None,
                "source_url": "https://example.edu/faculty",
                "review_status": "pending",
            },
        )
        self.assertEqual(
            no_email_patch_response.status_code, 200, msg=no_email_patch_response.text
        )
        self.assertEqual(no_email_patch_response.json()["name"], "无邮箱导师更新")

        approve_response = self.client.post(
            f"/api/crawl-jobs/{job['id']}/approve",
            json={"candidate_ids": [item["id"] for item in candidates]},
        )
        self.assertEqual(approve_response.status_code, 200, msg=approve_response.text)
        self.assertEqual(approve_response.json()["inserted_count"], 3)
        self.assertEqual(approve_response.json()["skipped_count"], 0)
        self.assertIn("审核完成", approve_response.json()["message"])

        completed_response = self.client.get(f"/api/crawl-jobs/{job['id']}")
        self.assertEqual(completed_response.json()["status"], "completed")

        cancel_completed_response = self.client.post(
            f"/api/crawl-jobs/{job['id']}/cancel"
        )
        self.assertEqual(cancel_completed_response.status_code, 200)
        self.assertEqual(cancel_completed_response.json()["status"], "completed")

    def test_crawl_job_pages_and_count_deduplicate_fetch_attempts_by_normalized_url(
        self,
    ) -> None:
        create_response = self.client.post(
            "/api/crawl-jobs",
            json={
                "university": "示例大学",
                "school": "计算机学院",
                "start_url": "https://example.edu/faculty",
                "llm_profile_id": None,
            },
        )
        self.assertEqual(create_response.status_code, 201, msg=create_response.text)
        job_id = create_response.json()["id"]

        async def seed_attempts() -> None:
            from app.core.database import get_session_factory
            from app.models import CrawlPage

            async with get_session_factory()() as session:
                session.add_all(
                    [
                        CrawlPage(
                            job_id=job_id,
                            url="https://EXAMPLE.edu/faculty?utm_source=direct",
                            parent_url=None,
                            fetch_method="http",
                            page_type="entry",
                            status="failed",
                            title=None,
                            text_excerpt=None,
                            error_message="HTTP 412 blocked",
                        ),
                        CrawlPage(
                            job_id=job_id,
                            url="https://example.edu/faculty",
                            parent_url=None,
                            fetch_method="browser",
                            page_type="entry",
                            status="failed",
                            title="Faculty",
                            text_excerpt="Partial browser content",
                            error_message="Browser wait timed out",
                        ),
                        CrawlPage(
                            job_id=job_id,
                            url="https://example.edu/faculty",
                            parent_url=None,
                            fetch_method="browser",
                            page_type="entry",
                            status="succeeded",
                            title="Faculty directory",
                            text_excerpt="Complete browser content",
                            error_message=None,
                        ),
                        CrawlPage(
                            job_id=job_id,
                            url="https://example.edu/faculty?page=2",
                            parent_url="https://example.edu/faculty",
                            fetch_method="http",
                            page_type="pagination",
                            status="succeeded",
                            title="Faculty directory page 2",
                            text_excerpt="Second page",
                            error_message=None,
                        ),
                    ],
                )
                await session.commit()

        asyncio.run(seed_attempts())

        pages_response = self.client.get(f"/api/crawl-jobs/{job_id}/pages")
        self.assertEqual(pages_response.status_code, 200, msg=pages_response.text)
        pages = pages_response.json()
        self.assertEqual(len(pages), 2)
        self.assertEqual(
            [page["title"] for page in pages],
            ["Faculty directory", "Faculty directory page 2"],
        )

        detail_response = self.client.get(f"/api/crawl-jobs/{job_id}")
        self.assertEqual(detail_response.status_code, 200, msg=detail_response.text)
        self.assertEqual(detail_response.json()["page_count"], 2)

        list_response = self.client.get("/api/crawl-jobs")
        self.assertEqual(list_response.status_code, 200, msg=list_response.text)
        self.assertEqual(list_response.json()[0]["page_count"], 2)

        import sqlite3

        with closing(sqlite3.connect(self.db_path)) as connection, connection:
            raw_attempt_count = connection.execute(
                "SELECT COUNT(*) FROM crawl_pages WHERE job_id = ?",
                (job_id,),
            ).fetchone()[0]
        self.assertEqual(raw_attempt_count, 4)

    def test_pause_resume_crawl_job_flow_preserves_saved_data(self) -> None:
        create_response = self.client.post(
            "/api/crawl-jobs",
            json={
                "university": "示例大学",
                "school": "计算机学院",
                "start_url": "https://example.edu/faculty",
                "llm_profile_id": None,
            },
        )
        self.assertEqual(create_response.status_code, 201, msg=create_response.text)
        job_id = create_response.json()["id"]
        self._seed_page_and_candidates(job_id)
        initial_runs = self._list_job_runs(job_id)
        self.assertEqual(len(initial_runs), 1)

        pause_response = self.client.post(f"/api/crawl-jobs/{job_id}/pause")

        self.assertEqual(pause_response.status_code, 200, msg=pause_response.text)
        self.assertEqual(pause_response.json()["status"], "paused")
        paused_runs = self._list_job_runs(job_id)
        self.assertEqual(len(paused_runs), 1)
        self.assertEqual(paused_runs[0]["id"], initial_runs[0]["id"])
        self.assertEqual(paused_runs[0]["status"], "paused")

        detail_response = self.client.get(f"/api/crawl-jobs/{job_id}")
        self.assertEqual(detail_response.status_code, 200)
        self.assertEqual(detail_response.json()["page_count"], 1)
        self.assertEqual(detail_response.json()["candidate_count"], 3)

        resume_response = self.client.post(f"/api/crawl-jobs/{job_id}/resume")

        self.assertEqual(resume_response.status_code, 200, msg=resume_response.text)
        self.assertEqual(resume_response.json()["status"], "queued")
        resumed_runs = self._list_job_runs(job_id)
        self.assertEqual(len(resumed_runs), 1)
        self.assertEqual(resumed_runs[0]["id"], initial_runs[0]["id"])
        self.assertEqual(resumed_runs[0]["status"], "queued")

        resumed_detail_response = self.client.get(f"/api/crawl-jobs/{job_id}")
        self.assertEqual(resumed_detail_response.json()["page_count"], 1)
        self.assertEqual(resumed_detail_response.json()["candidate_count"], 3)

    def test_pause_releases_processing_runtime_work_for_safe_resume(self) -> None:
        create_response = self.client.post(
            "/api/crawl-jobs",
            json={
                "university": "示例大学",
                "school": "计算机学院",
                "start_url": "https://example.edu/faculty",
                "llm_profile_id": None,
            },
        )
        self.assertEqual(create_response.status_code, 201, msg=create_response.text)
        job_id = create_response.json()["id"]
        self._seed_processing_runtime_work(job_id)

        response = self.client.post(f"/api/crawl-jobs/{job_id}/pause")

        self.assertEqual(response.status_code, 200, msg=response.text)
        self.assertEqual(response.json()["status"], "paused")
        self.assertEqual(
            self._list_runtime_work_statuses(job_id),
            {
                "page_tasks": ["pending"],
                "chunks": ["pending"],
                "enrichment_tasks": ["pending"],
            },
        )

        resume_response = self.client.post(f"/api/crawl-jobs/{job_id}/resume")

        self.assertEqual(resume_response.status_code, 200, msg=resume_response.text)
        self.assertEqual(resume_response.json()["status"], "queued")
        self.assertEqual(
            self._list_runtime_work_statuses(job_id),
            {
                "page_tasks": ["pending"],
                "chunks": ["pending"],
                "enrichment_tasks": ["pending"],
            },
        )

    def test_cancel_releases_processing_runtime_work_so_workers_cannot_commit_late(
        self,
    ) -> None:
        create_response = self.client.post(
            "/api/crawl-jobs",
            json={
                "university": "示例大学",
                "school": "计算机学院",
                "start_url": "https://example.edu/faculty",
                "llm_profile_id": None,
            },
        )
        self.assertEqual(create_response.status_code, 201, msg=create_response.text)
        job_id = create_response.json()["id"]
        self._seed_processing_runtime_work(job_id)

        response = self.client.post(f"/api/crawl-jobs/{job_id}/cancel")

        self.assertEqual(response.status_code, 200, msg=response.text)
        self.assertEqual(response.json()["status"], "canceled")
        self.assertEqual(
            self._list_runtime_work_statuses(job_id),
            {
                "page_tasks": ["pending"],
                "chunks": ["pending"],
                "enrichment_tasks": ["pending"],
            },
        )

    def test_resume_review_freezes_unfinished_discovery_work_before_enrichment(
        self,
    ) -> None:
        profile_id = self._create_llm_profile("测试模型", "test-model")
        create_response = self.client.post(
            "/api/crawl-jobs",
            json={
                "university": "示例大学",
                "school": "计算机学院",
                "start_url": "https://example.edu/faculty",
                "llm_profile_id": profile_id,
            },
        )
        self.assertEqual(create_response.status_code, 201, msg=create_response.text)
        job_id = create_response.json()["id"]
        self._seed_pending_discovery_work_with_candidate(job_id)
        self._set_job_status(job_id, "canceled")
        candidate_id = self._latest_candidate_id(job_id)

        review_response = self.client.post(f"/api/crawl-jobs/{job_id}/resume-review")
        enrich_response = self.client.post(
            f"/api/crawl-jobs/{job_id}/enrich",
            json={"candidate_ids": [candidate_id], "llm_profile_id": profile_id},
        )

        self.assertEqual(review_response.status_code, 200, msg=review_response.text)
        self.assertEqual(enrich_response.status_code, 200, msg=enrich_response.text)
        self.assertEqual(
            self._list_runtime_work_statuses(job_id),
            {
                "page_tasks": ["failed_terminal"],
                "chunks": ["failed_terminal"],
                "enrichment_tasks": ["pending"],
            },
        )

    def test_pause_rejects_terminal_or_review_jobs(self) -> None:
        for job_status in (
            "needs_review",
            "partially_completed",
            "completed",
            "failed",
            "canceled",
        ):
            with self.subTest(status=job_status):
                create_response = self.client.post(
                    "/api/crawl-jobs",
                    json={
                        "university": "示例大学",
                        "school": "计算机学院",
                        "start_url": "https://example.edu/faculty",
                        "llm_profile_id": None,
                    },
                )
                self.assertEqual(
                    create_response.status_code, 201, msg=create_response.text
                )
                job_id = create_response.json()["id"]
                self._set_job_status(job_id, job_status)

                response = self.client.post(f"/api/crawl-jobs/{job_id}/pause")

                self.assertEqual(response.status_code, 409, msg=response.text)

    def test_resume_rejects_non_paused_job(self) -> None:
        create_response = self.client.post(
            "/api/crawl-jobs",
            json={
                "university": "示例大学",
                "school": "计算机学院",
                "start_url": "https://example.edu/faculty",
                "llm_profile_id": None,
            },
        )
        self.assertEqual(create_response.status_code, 201, msg=create_response.text)
        job_id = create_response.json()["id"]

        response = self.client.post(f"/api/crawl-jobs/{job_id}/resume")

        self.assertEqual(response.status_code, 409)

    def test_resume_accepts_llm_profile_id_payload(self) -> None:
        old_profile_id = self._create_llm_profile("旧模型", "old-model")
        new_profile_id = self._create_llm_profile("新模型", "new-model")
        create_response = self.client.post(
            "/api/crawl-jobs",
            json={
                "university": "示例大学",
                "school": "计算机学院",
                "start_url": "https://example.edu/faculty",
                "llm_profile_id": old_profile_id,
            },
        )
        self.assertEqual(create_response.status_code, 201, msg=create_response.text)
        job_id = create_response.json()["id"]
        self._set_job_status(job_id, "paused")

        response = self.client.post(
            f"/api/crawl-jobs/{job_id}/resume",
            json={"llm_profile_id": new_profile_id},
        )

        self.assertEqual(response.status_code, 200, msg=response.text)

    def test_resume_refreshes_job_llm_profile_before_queueing(self) -> None:
        old_profile_id = self._create_llm_profile("旧模型", "old-model")
        new_profile_id = self._create_llm_profile("新模型", "new-model")
        create_response = self.client.post(
            "/api/crawl-jobs",
            json={
                "university": "示例大学",
                "school": "计算机学院",
                "start_url": "https://example.edu/faculty",
                "llm_profile_id": old_profile_id,
            },
        )
        self.assertEqual(create_response.status_code, 201, msg=create_response.text)
        job_id = create_response.json()["id"]
        self._set_job_status(job_id, "paused")

        response = self.client.post(
            f"/api/crawl-jobs/{job_id}/resume",
            json={"llm_profile_id": new_profile_id},
        )

        self.assertEqual(response.status_code, 200, msg=response.text)
        self.assertEqual(response.json()["llm_profile_id"], new_profile_id)
        self.assertEqual(self._get_job_llm_profile_id(job_id), new_profile_id)

    def test_resume_model_refresh_records_operation_log(self) -> None:
        old_profile_id = self._create_llm_profile("旧模型", "old-model")
        new_profile_id = self._create_llm_profile("新模型", "new-model")
        create_response = self.client.post(
            "/api/crawl-jobs",
            json={
                "university": "示例大学",
                "school": "计算机学院",
                "start_url": "https://example.edu/faculty",
                "llm_profile_id": old_profile_id,
            },
        )
        self.assertEqual(create_response.status_code, 201, msg=create_response.text)
        job_id = create_response.json()["id"]
        self._set_job_status(job_id, "paused")

        response = self.client.post(
            f"/api/crawl-jobs/{job_id}/resume",
            json={"llm_profile_id": new_profile_id},
        )

        self.assertEqual(response.status_code, 200, msg=response.text)
        logs = self._list_operation_logs("crawl_job.llm_profile_refreshed", str(job_id))
        self.assertEqual(len(logs), 1)
        self.assertEqual(logs[0]["metadata"]["old_llm_profile_id"], old_profile_id)
        self.assertEqual(logs[0]["metadata"]["old_model_name"], "old-model")
        self.assertEqual(logs[0]["metadata"]["new_llm_profile_id"], new_profile_id)
        self.assertEqual(logs[0]["metadata"]["new_model_name"], "new-model")
        self.assertEqual(logs[0]["metadata"]["trigger"], "resume")

    def test_paused_crawl_job_can_be_canceled(self) -> None:
        create_response = self.client.post(
            "/api/crawl-jobs",
            json={
                "university": "示例大学",
                "school": "计算机学院",
                "start_url": "https://example.edu/faculty",
                "llm_profile_id": None,
            },
        )
        self.assertEqual(create_response.status_code, 201, msg=create_response.text)
        job_id = create_response.json()["id"]
        self.client.post(f"/api/crawl-jobs/{job_id}/pause")

        response = self.client.post(f"/api/crawl-jobs/{job_id}/cancel")

        self.assertEqual(response.status_code, 200, msg=response.text)
        self.assertEqual(response.json()["status"], "canceled")
        runs = self._list_job_runs(job_id)
        self.assertEqual(len(runs), 1)
        self.assertEqual(runs[0]["status"], "canceled")
        self.assertIsNotNone(runs[0]["finished_at"])

    def test_retry_refreshes_job_llm_profile_before_queueing(self) -> None:
        old_profile_id = self._create_llm_profile("旧模型", "old-model")
        new_profile_id = self._create_llm_profile("新模型", "new-model")
        create_response = self.client.post(
            "/api/crawl-jobs",
            json={
                "university": "示例大学",
                "school": "计算机学院",
                "start_url": "https://example.edu/faculty",
                "llm_profile_id": old_profile_id,
            },
        )
        self.assertEqual(create_response.status_code, 201, msg=create_response.text)
        job_id = create_response.json()["id"]
        self._set_job_status(job_id, "failed")

        response = self.client.post(
            f"/api/crawl-jobs/{job_id}/retry",
            json={"clear_existing_data": False, "llm_profile_id": new_profile_id},
        )

        self.assertEqual(response.status_code, 200, msg=response.text)
        self.assertEqual(response.json()["llm_profile_id"], new_profile_id)
        self.assertEqual(self._get_job_llm_profile_id(job_id), new_profile_id)
        logs = self._list_operation_logs("crawl_job.llm_profile_refreshed", str(job_id))
        self.assertEqual(logs[-1]["metadata"]["trigger"], "retry")

    def test_enrich_selected_candidates_returns_skip_summary_when_all_lack_profile_url(
        self,
    ) -> None:
        profile_id = self._create_llm_profile("默认模型", "deepseek")
        create_response = self.client.post(
            "/api/crawl-jobs",
            json={
                "university": "示例大学",
                "school": "计算机学院",
                "start_url": "https://example.edu/faculty",
                "llm_profile_id": profile_id,
            },
        )
        self.assertEqual(create_response.status_code, 201, msg=create_response.text)
        job_id = create_response.json()["id"]
        self._set_job_status(job_id, "needs_review")
        self._seed_candidate(job_id, name="王老师", profile_url="")
        candidate_id = self._latest_candidate_id(job_id)

        response = self.client.post(
            f"/api/crawl-jobs/{job_id}/enrich",
            json={"candidate_ids": [candidate_id], "llm_profile_id": profile_id},
        )

        self.assertEqual(response.status_code, 200, msg=response.text)
        body = response.json()
        self.assertEqual(body["selected_count"], 0)
        self.assertEqual(body["skipped_count"], 1)
        self.assertEqual(body["failed_count"], 0)
        self.assertIsNone(body["operation_id"])
        self.assertIn("跳过 1 位缺少详情页 URL", body["message"])

    def test_enrich_selected_candidates_enqueues_database_tasks(self) -> None:
        profile_id = self._create_llm_profile("测试模型", "test-model")
        create_response = self.client.post(
            "/api/crawl-jobs",
            json={
                "university": "示例大学",
                "school": "计算机学院",
                "start_url": "https://example.edu/faculty",
                "llm_profile_id": profile_id,
            },
        )
        self.assertEqual(create_response.status_code, 201, msg=create_response.text)
        job_id = create_response.json()["id"]
        self._set_job_status(job_id, "needs_review")
        self._seed_candidate(
            job_id, name="王老师", profile_url="https://example.edu/wang"
        )
        candidate_id = self._latest_candidate_id(job_id)
        self._seed_candidate(job_id, name="李老师", profile_url="")
        missing_profile_candidate_id = self._latest_candidate_id(job_id)

        response = self.client.post(
            f"/api/crawl-jobs/{job_id}/enrich",
            json={
                "candidate_ids": [candidate_id, missing_profile_candidate_id],
                "llm_profile_id": profile_id,
            },
        )

        self.assertEqual(response.status_code, 200, msg=response.text)
        body = response.json()
        self.assertEqual(body["selected_count"], 1)
        self.assertEqual(body["enriched_count"], 0)
        self.assertEqual(body["failed_count"], 0)
        self.assertEqual(body["skipped_count"], 1)
        operation_id = body["operation_id"]
        self.assertEqual(str(UUID(operation_id)), operation_id)
        self.assertIn("已加入补全队列", body["message"])
        self.assertEqual(
            self.client.get(f"/api/crawl-jobs/{job_id}").json()["status"], "running"
        )
        statuses = self._list_runtime_work_statuses(job_id)
        self.assertEqual(statuses["enrichment_tasks"], ["pending"])
        import sqlite3

        with closing(sqlite3.connect(self.db_path)) as connection:
            persisted_operation_id, persisted_skipped_count = connection.execute(
                """
                SELECT active_candidate_enrichment_operation_id,
                       active_candidate_enrichment_skipped_count
                FROM crawl_jobs WHERE id = ?
                """,
                (job_id,),
            ).fetchone()
        self.assertEqual(persisted_operation_id, operation_id)
        self.assertEqual(persisted_skipped_count, 1)

    def test_cancel_candidate_enrichment_records_its_operation_id(self) -> None:
        profile_id = self._create_llm_profile("测试模型", "test-model")
        create_response = self.client.post(
            "/api/crawl-jobs",
            json={
                "university": "示例大学",
                "school": "计算机学院",
                "start_url": "https://example.edu/faculty",
                "llm_profile_id": profile_id,
            },
        )
        self.assertEqual(create_response.status_code, 201, msg=create_response.text)
        job_id = create_response.json()["id"]
        self._set_job_status(job_id, "needs_review")
        self._seed_candidate(
            job_id, name="王老师", profile_url="https://example.edu/wang"
        )
        candidate_id = self._latest_candidate_id(job_id)

        enrich_response = self.client.post(
            f"/api/crawl-jobs/{job_id}/enrich",
            json={"candidate_ids": [candidate_id], "llm_profile_id": profile_id},
        )
        self.assertEqual(enrich_response.status_code, 200, msg=enrich_response.text)
        operation_id = enrich_response.json()["operation_id"]

        cancel_response = self.client.post(f"/api/crawl-jobs/{job_id}/cancel")
        self.assertEqual(cancel_response.status_code, 200, msg=cancel_response.text)
        events = self.client.get(f"/api/crawl-jobs/{job_id}/events").json()
        canceled_event = next(
            event
            for event in events
            if event["event_type"] == "enrichment"
            and event["message"] == "候选导师详情补全已取消"
        )
        self.assertEqual(canceled_event["raw"]["raw"]["operation_id"], operation_id)
        self.assertEqual(canceled_event["raw"]["raw"]["status"], "canceled")

        import sqlite3

        with closing(sqlite3.connect(self.db_path)) as connection:
            active_operation_id = connection.execute(
                "SELECT active_candidate_enrichment_operation_id FROM crawl_jobs WHERE id = ?",
                (job_id,),
            ).fetchone()[0]
        self.assertIsNone(active_operation_id)

    def test_runtime_enrich_requeues_succeeded_task_when_candidate_has_missing_fields(
        self,
    ) -> None:
        profile_id = self._create_llm_profile("测试模型", "test-model")
        create_response = self.client.post(
            "/api/crawl-jobs",
            json={
                "university": "示例大学",
                "school": "计算机学院",
                "start_url": "https://example.edu/faculty",
                "llm_profile_id": profile_id,
            },
        )
        self.assertEqual(create_response.status_code, 201, msg=create_response.text)
        job_id = create_response.json()["id"]
        self._set_job_status(job_id, "needs_review")
        self._seed_candidate_with_fields(
            job_id,
            name="刘德喜",
            email="dexi@example.edu",
            title=None,
            department="计算机学院",
            research_direction="自然语言处理",
            recent_papers=["Paper A"],
            profile_url="https://example.edu/liudexi",
        )
        candidate_id = self._latest_candidate_id(job_id)
        self._seed_enrichment_task(
            candidate_id, status="succeeded", last_error="Connection error."
        )

        response = self.client.post(
            f"/api/crawl-jobs/{job_id}/enrich",
            json={"candidate_ids": [candidate_id], "llm_profile_id": profile_id},
        )

        self.assertEqual(response.status_code, 200, msg=response.text)
        body = response.json()
        self.assertEqual(body["selected_count"], 1)
        self.assertEqual(body["unchanged_count"], 0)
        self.assertIn("入队 1 位", body["message"])
        self.assertEqual(
            self._list_runtime_work_statuses(job_id)["enrichment_tasks"], ["pending"]
        )

    def test_runtime_enrich_resets_previous_task_attempt_state(self) -> None:
        from app.modules.crawler.runtime.profile_text_cache import profile_text_cache

        profile_id = self._create_llm_profile("测试模型", "test-model")
        create_response = self.client.post(
            "/api/crawl-jobs",
            json={
                "university": "示例大学",
                "school": "计算机学院",
                "start_url": "https://example.edu/faculty",
                "llm_profile_id": profile_id,
            },
        )
        self.assertEqual(create_response.status_code, 201, msg=create_response.text)
        job_id = create_response.json()["id"]
        self._set_job_status(job_id, "needs_review")
        self._seed_candidate(
            job_id, name="重新补全导师", profile_url="https://example.edu/retry"
        )
        candidate_id = self._latest_candidate_id(job_id)
        self._seed_enrichment_task(
            candidate_id, status="failed_terminal", last_error="旧失败"
        )
        cache_key = (999, job_id, candidate_id, "https://example.edu/retry")
        profile_text_cache.put(cache_key, "旧轮次正文 old@example.edu")

        import sqlite3

        with closing(sqlite3.connect(self.db_path)) as connection, connection:
            connection.execute(
                """
                UPDATE crawl_candidate_enrichment_tasks
                SET worker_id = 'old-worker', claimed_at = CURRENT_TIMESTAMP,
                    lease_expires_at = CURRENT_TIMESTAMP, attempt_count = 4,
                    failure_count = 3, skip_reason = '旧跳过原因',
                    enriched_fields = '["email"]', started_at = CURRENT_TIMESTAMP,
                    finished_at = CURRENT_TIMESTAMP
                WHERE job_id = ? AND candidate_id = ?
                """,
                (job_id, candidate_id),
            )

        response = self.client.post(
            f"/api/crawl-jobs/{job_id}/enrich",
            json={"candidate_ids": [candidate_id], "llm_profile_id": profile_id},
        )

        self.assertEqual(response.status_code, 200, msg=response.text)
        with closing(sqlite3.connect(self.db_path)) as connection:
            row = connection.execute(
                """
                SELECT status, worker_id, claimed_at, lease_expires_at,
                       attempt_count, failure_count, last_error, skip_reason,
                       enriched_fields, started_at, finished_at
                FROM crawl_candidate_enrichment_tasks
                WHERE job_id = ? AND candidate_id = ?
                """,
                (job_id, candidate_id),
            ).fetchone()
        self.assertEqual(
            row,
            ("pending", None, None, None, 0, 0, None, None, "null", None, None),
        )
        self.assertNotIn(cache_key, profile_text_cache)

    def test_runtime_enrich_skips_succeeded_task_only_when_candidate_is_complete(
        self,
    ) -> None:
        profile_id = self._create_llm_profile("测试模型", "test-model")
        create_response = self.client.post(
            "/api/crawl-jobs",
            json={
                "university": "示例大学",
                "school": "计算机学院",
                "start_url": "https://example.edu/faculty",
                "llm_profile_id": profile_id,
            },
        )
        self.assertEqual(create_response.status_code, 201, msg=create_response.text)
        job_id = create_response.json()["id"]
        self._set_job_status(job_id, "needs_review")
        self._seed_candidate_with_fields(
            job_id,
            name="完整导师",
            email="done@example.edu",
            title="教授",
            department="计算机学院",
            research_direction="机器学习",
            recent_papers=["Paper A"],
            profile_url="https://example.edu/done",
        )
        candidate_id = self._latest_candidate_id(job_id)
        self._seed_enrichment_task(candidate_id, status="succeeded", last_error=None)

        response = self.client.post(
            f"/api/crawl-jobs/{job_id}/enrich",
            json={"candidate_ids": [candidate_id], "llm_profile_id": profile_id},
        )

        self.assertEqual(response.status_code, 200, msg=response.text)
        body = response.json()
        self.assertEqual(body["selected_count"], 1)
        self.assertEqual(body["unchanged_count"], 1)
        self.assertIn("已补全跳过 1 位", body["message"])
        self.assertEqual(
            self._list_runtime_work_statuses(job_id)["enrichment_tasks"], ["succeeded"]
        )

    def test_enrich_refreshes_job_llm_profile_before_running(self) -> None:
        old_profile_id = self._create_llm_profile("旧模型", "old-model")
        new_profile_id = self._create_llm_profile("新模型", "new-model")
        create_response = self.client.post(
            "/api/crawl-jobs",
            json={
                "university": "示例大学",
                "school": "计算机学院",
                "start_url": "https://example.edu/faculty",
                "llm_profile_id": old_profile_id,
            },
        )
        self.assertEqual(create_response.status_code, 201, msg=create_response.text)
        job_id = create_response.json()["id"]
        self._set_job_status(job_id, "needs_review")
        self._seed_candidate(
            job_id, name="王老师", profile_url="https://example.edu/wang"
        )
        candidate_id = self._latest_candidate_id(job_id)

        response = self.client.post(
            f"/api/crawl-jobs/{job_id}/enrich",
            json={"candidate_ids": [candidate_id], "llm_profile_id": new_profile_id},
        )

        self.assertEqual(response.status_code, 200, msg=response.text)
        self.assertEqual(self._get_job_llm_profile_id(job_id), new_profile_id)
        logs = self._list_operation_logs("crawl_job.llm_profile_refreshed", str(job_id))
        self.assertEqual(logs[-1]["metadata"]["trigger"], "enrich")

    def test_enrich_rejects_missing_requested_llm_profile(self) -> None:
        profile_id = self._create_llm_profile("旧模型", "old-model")
        create_response = self.client.post(
            "/api/crawl-jobs",
            json={
                "university": "示例大学",
                "school": "计算机学院",
                "start_url": "https://example.edu/faculty",
                "llm_profile_id": profile_id,
            },
        )
        self.assertEqual(create_response.status_code, 201, msg=create_response.text)
        job_id = create_response.json()["id"]
        self._set_job_status(job_id, "needs_review")

        response = self.client.post(
            f"/api/crawl-jobs/{job_id}/enrich",
            json={"candidate_ids": [999], "llm_profile_id": 999999},
        )

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["detail"], "模型配置不存在")
        self.assertEqual(self._get_job_llm_profile_id(job_id), profile_id)

    def test_retry_crawl_job_creates_new_run(self) -> None:
        create_response = self.client.post(
            "/api/crawl-jobs",
            json={
                "university": "示例大学",
                "school": "计算机学院",
                "start_url": "https://example.edu/faculty",
                "llm_profile_id": None,
            },
        )
        self.assertEqual(create_response.status_code, 201, msg=create_response.text)
        job_id = create_response.json()["id"]
        initial_run_id = self._list_job_runs(job_id)[0]["id"]
        self._set_job_status(job_id, "failed")

        self._mark_page_tasks_succeeded(job_id)

        response = self.client.post(
            f"/api/crawl-jobs/{job_id}/retry",
            json={"clear_existing_data": False},
        )

        self.assertEqual(response.status_code, 200, msg=response.text)
        self.assertEqual(response.json()["status"], "queued")
        runs = self._list_job_runs(job_id)
        self.assertEqual([run["attempt_number"] for run in runs], [1, 2])
        self.assertEqual(runs[0]["id"], initial_run_id)
        self.assertEqual(runs[1]["status"], "queued")
        self.assertEqual(self._get_job_current_run_id(job_id), runs[1]["id"])
        self.assertEqual(
            self._list_page_task_statuses(job_id),
            [("https://example.edu/faculty", "pending")],
        )

    def test_retry_runtime_crawl_job_reseeds_pending_page_tasks(self) -> None:
        create_response = self.client.post(
            "/api/crawl-jobs",
            json={
                "university": "示例大学",
                "school": "计算机学院",
                "start_url": "https://example.edu/faculty",
                "start_urls": [
                    "https://example.edu/faculty",
                    "https://example.edu/faculty?page=2",
                ],
                "llm_profile_id": None,
            },
        )
        self.assertEqual(create_response.status_code, 201, msg=create_response.text)
        job_id = create_response.json()["id"]
        self._mark_page_tasks_succeeded(job_id)
        self._set_job_status(job_id, "failed")

        response = self.client.post(
            f"/api/crawl-jobs/{job_id}/retry",
            json={"clear_existing_data": True},
        )

        self.assertEqual(response.status_code, 200, msg=response.text)
        self.assertEqual(response.json()["status"], "queued")
        self.assertEqual(
            self._list_page_task_statuses(job_id),
            [
                ("https://example.edu/faculty", "pending"),
                ("https://example.edu/faculty?page=2", "pending"),
            ],
        )

    def test_retry_runtime_crawl_job_deduplicates_historical_start_urls_by_normalized_url(
        self,
    ) -> None:
        create_response = self.client.post(
            "/api/crawl-jobs",
            json={
                "university": "示例大学",
                "school": "计算机学院",
                "start_url": "https://example.edu/faculty",
                "llm_profile_id": None,
            },
        )
        self.assertEqual(create_response.status_code, 201, msg=create_response.text)
        job_id = create_response.json()["id"]
        self._set_job_start_urls(
            job_id,
            [
                "https://example.edu/faculty?utm_source=old",
                "https://example.edu/faculty?utm_source=older",
            ],
        )
        self._mark_page_tasks_succeeded(job_id)
        self._set_job_status(job_id, "failed")

        try:
            response = self.client.post(
                f"/api/crawl-jobs/{job_id}/retry",
                json={"clear_existing_data": True},
            )
        except (
            Exception
        ) as exc:  # pragma: no cover - keeps the red test as an assertion failure.
            self.fail(f"retry crawl job raised {exc!r}")

        self.assertEqual(response.status_code, 200, msg=response.text)
        self.assertEqual(
            self._list_page_task_statuses(job_id),
            [("https://example.edu/faculty", "pending")],
        )

    def test_retry_crawl_job_clear_existing_data_removes_page_chunks(self) -> None:
        create_response = self.client.post(
            "/api/crawl-jobs",
            json={
                "university": "示例大学",
                "school": "计算机学院",
                "start_url": "https://example.edu/faculty",
                "llm_profile_id": None,
            },
        )
        self.assertEqual(create_response.status_code, 201, msg=create_response.text)
        job_id = create_response.json()["id"]
        self._seed_page_candidate_and_chunk(job_id)
        self._set_job_status(job_id, "failed")

        response = self.client.post(
            f"/api/crawl-jobs/{job_id}/retry",
            json={"clear_existing_data": True},
        )

        self.assertEqual(response.status_code, 200, msg=response.text)
        self.assertEqual(response.json()["status"], "queued")
        self.assertEqual(self._count_page_chunks(job_id), 0)

    def test_retry_crawl_job_clears_previous_page_fetch_ledger(self) -> None:
        create_response = self.client.post(
            "/api/crawl-jobs",
            json={
                "university": "示例大学",
                "school": "计算机学院",
                "start_url": "https://example.edu/faculty",
                "llm_profile_id": None,
            },
        )
        self.assertEqual(create_response.status_code, 201, msg=create_response.text)
        job_id = create_response.json()["id"]
        self._set_job_status(job_id, "failed")

        with closing(sqlite3.connect(self.db_path)) as connection, connection:
            connection.execute(
                """
                INSERT INTO crawl_page_fetch_states
                    (job_id, normalized_url, original_url, status,
                     transient_failure_count, terminal_reason, last_error_message)
                VALUES (?, ?, ?, 'terminal_failed', 2, ?, ?)
                """,
                (
                    job_id,
                    "https://example.edu/faculty",
                    "https://example.edu/faculty",
                    "transient_retry_exhausted",
                    "旧轮次失败",
                ),
            )

        response = self.client.post(
            f"/api/crawl-jobs/{job_id}/retry",
            json={"clear_existing_data": False},
        )

        self.assertEqual(response.status_code, 200, msg=response.text)
        with closing(sqlite3.connect(self.db_path)) as connection:
            state_count = connection.execute(
                "SELECT COUNT(*) FROM crawl_page_fetch_states WHERE job_id = ?",
                (job_id,),
            ).fetchone()[0]
        self.assertEqual(state_count, 0)

    def test_crawl_job_events_include_status_trace_page_and_candidate_messages(
        self,
    ) -> None:
        create_response = self.client.post(
            "/api/crawl-jobs",
            json={
                "university": "示例大学",
                "school": "计算机学院",
                "start_url": "https://example.edu/faculty",
                "llm_profile_id": None,
            },
        )
        self.assertEqual(create_response.status_code, 201, msg=create_response.text)
        job_id = create_response.json()["id"]
        self._seed_page_and_candidates(job_id)
        self._set_job_status(job_id, "needs_review")
        self._set_job_trace(
            job_id,
            [
                {
                    "event_type": "enrichment",
                    "message": "候选导师详情补全成功：高分导师",
                    "created_at": "2026-04-26T10:01:00+00:00",
                },
            ],
        )

        response = self.client.get(f"/api/crawl-jobs/{job_id}/events")

        self.assertEqual(response.status_code, 200, msg=response.text)
        messages = [event["message"] for event in response.json()]
        self.assertIn("任务进入待审核", messages)
        self.assertIn("候选导师详情补全成功：高分导师", messages)
        self.assertIn("已抓取页面：Faculty", messages)
        self.assertIn("发现候选导师：高分导师、低分导师、无邮箱导师", messages)

    def test_crawl_job_details_returns_summary_and_reuses_detail_records(self) -> None:
        create_response = self.client.post(
            "/api/crawl-jobs",
            json={
                "university": "示例大学",
                "school": "计算机学院",
                "start_url": "https://example.edu/faculty",
                "llm_profile_id": None,
            },
        )
        self.assertEqual(create_response.status_code, 201, msg=create_response.text)
        job_id = create_response.json()["id"]
        self._seed_page_and_candidates(job_id)
        self._set_job_status(job_id, "needs_review")

        response = self.client.get(f"/api/crawl-jobs/{job_id}/details")

        self.assertEqual(response.status_code, 200, msg=response.text)
        payload = response.json()
        self.assertEqual(payload["job"]["id"], job_id)
        self.assertEqual(payload["job"]["page_count"], 1)
        self.assertEqual(payload["job"]["candidate_count"], 3)
        self.assertEqual(len(payload["pages"]), 1)
        self.assertEqual(len(payload["candidates"]), 3)
        self.assertIn(
            "任务进入待审核", [event["message"] for event in payload["events"]]
        )

    def test_approve_requires_candidate_ids(self) -> None:
        create_response = self.client.post(
            "/api/crawl-jobs",
            json={
                "university": "示例大学",
                "school": "计算机学院",
                "start_url": "https://example.edu/faculty",
                "llm_profile_id": None,
            },
        )
        self.assertEqual(create_response.status_code, 201, msg=create_response.text)
        self._set_job_status(create_response.json()["id"], "needs_review")

        response = self.client.post(
            f"/api/crawl-jobs/{create_response.json()['id']}/approve",
            json={"candidate_ids": []},
        )

        self.assertEqual(response.status_code, 400)

    def test_approve_caps_legacy_candidate_recent_papers_to_first_8(self) -> None:
        import json
        import sqlite3

        create_response = self.client.post(
            "/api/crawl-jobs",
            json={
                "university": "示例大学",
                "school": "计算机学院",
                "start_url": "https://example.edu/faculty",
                "llm_profile_id": None,
            },
        )
        self.assertEqual(create_response.status_code, 201, msg=create_response.text)
        job_id = create_response.json()["id"]
        papers = [f"Paper {index}" for index in range(1, 13)]
        self._seed_candidate_with_fields(
            job_id,
            name="超限论文导师",
            email="papers@example.edu",
            title="教授",
            department="计算机系",
            research_direction="人工智能",
            recent_papers=papers,
            profile_url="https://example.edu/papers",
        )
        self._set_job_status(job_id, "needs_review")
        candidate_id = self._latest_candidate_id(job_id)

        response = self.client.post(
            f"/api/crawl-jobs/{job_id}/approve",
            json={"candidate_ids": [candidate_id]},
        )

        self.assertEqual(response.status_code, 200, msg=response.text)
        with closing(sqlite3.connect(self.db_path)) as connection, connection:
            stored = connection.execute(
                "SELECT recent_papers FROM professors WHERE email = ?",
                ("papers@example.edu",),
            ).fetchone()
        self.assertIsNotNone(stored)
        self.assertEqual(json.loads(stored[0]), papers[:8])

    def test_approve_allows_canceled_job_and_preserves_canceled_status(self) -> None:
        create_response = self.client.post(
            "/api/crawl-jobs",
            json={
                "university": "示例大学",
                "school": "计算机学院",
                "start_url": "https://example.edu/faculty",
                "llm_profile_id": None,
            },
        )
        self.assertEqual(create_response.status_code, 201, msg=create_response.text)
        job_id = create_response.json()["id"]
        self._seed_page_and_candidates(job_id)
        self._set_job_status(job_id, "canceled")
        candidates = self.client.get(f"/api/crawl-jobs/{job_id}/candidates").json()

        response = self.client.post(
            f"/api/crawl-jobs/{job_id}/approve",
            json={"candidate_ids": [candidates[0]["id"]]},
        )

        self.assertEqual(response.status_code, 200, msg=response.text)
        self.assertEqual(response.json()["inserted_count"], 1)
        detail_response = self.client.get(f"/api/crawl-jobs/{job_id}")
        self.assertEqual(detail_response.json()["status"], "canceled")

    def test_approve_rejects_paused_job_with_saved_candidates(self) -> None:
        create_response = self.client.post(
            "/api/crawl-jobs",
            json={
                "university": "示例大学",
                "school": "计算机学院",
                "start_url": "https://example.edu/faculty",
                "llm_profile_id": None,
            },
        )
        self.assertEqual(create_response.status_code, 201, msg=create_response.text)
        job_id = create_response.json()["id"]
        self._seed_page_and_candidates(job_id)
        self._set_job_status(job_id, "paused")
        candidates = self.client.get(f"/api/crawl-jobs/{job_id}/candidates").json()

        response = self.client.post(
            f"/api/crawl-jobs/{job_id}/approve",
            json={"candidate_ids": [candidates[0]["id"]]},
        )

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["detail"], "抓取任务尚未进入审核状态")

    def test_approve_rejects_job_before_review_state(self) -> None:
        create_response = self.client.post(
            "/api/crawl-jobs",
            json={
                "university": "示例大学",
                "school": "计算机学院",
                "start_url": "https://example.edu/faculty",
                "llm_profile_id": None,
            },
        )
        self.assertEqual(create_response.status_code, 201, msg=create_response.text)
        job_id = create_response.json()["id"]
        self._seed_page_and_candidates(job_id)
        candidates = self.client.get(f"/api/crawl-jobs/{job_id}/candidates").json()

        response = self.client.post(
            f"/api/crawl-jobs/{job_id}/approve",
            json={"candidate_ids": [candidates[0]["id"]]},
        )

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["detail"], "抓取任务尚未进入审核状态")

    def test_approve_rejects_completed_job_even_with_saved_candidates(self) -> None:
        create_response = self.client.post(
            "/api/crawl-jobs",
            json={
                "university": "示例大学",
                "school": "计算机学院",
                "start_url": "https://example.edu/faculty",
                "llm_profile_id": None,
            },
        )
        self.assertEqual(create_response.status_code, 201, msg=create_response.text)
        job_id = create_response.json()["id"]
        self._seed_page_and_candidates(job_id)
        self._set_job_status(job_id, "completed")
        candidates = self.client.get(f"/api/crawl-jobs/{job_id}/candidates").json()

        response = self.client.post(
            f"/api/crawl-jobs/{job_id}/approve",
            json={"candidate_ids": [candidates[0]["id"]]},
        )

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["detail"], "抓取任务尚未进入审核状态")

    def test_enrich_selected_candidates_requires_review_state(self) -> None:
        create_response = self.client.post(
            "/api/crawl-jobs",
            json={
                "university": "示例大学",
                "school": "计算机学院",
                "start_url": "https://example.edu/faculty",
                "llm_profile_id": None,
            },
        )
        self.assertEqual(create_response.status_code, 201, msg=create_response.text)

        response = self.client.post(
            f"/api/crawl-jobs/{create_response.json()['id']}/enrich",
            json={"candidate_ids": [1]},
        )

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["detail"], "抓取任务尚未进入审核状态")

    def test_enrich_selected_candidates_rejects_running_job(self) -> None:
        create_response = self.client.post(
            "/api/crawl-jobs",
            json={
                "university": "示例大学",
                "school": "计算机学院",
                "start_url": "https://example.edu/faculty",
                "llm_profile_id": None,
            },
        )
        self.assertEqual(create_response.status_code, 201, msg=create_response.text)
        self._set_job_status(create_response.json()["id"], "running")

        response = self.client.post(
            f"/api/crawl-jobs/{create_response.json()['id']}/enrich",
            json={"candidate_ids": [1]},
        )

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["detail"], "候选信息正在补全中，请稍后再试")

    def test_enrich_selected_candidates_rejects_completed_job(self) -> None:
        create_response = self.client.post(
            "/api/crawl-jobs",
            json={
                "university": "示例大学",
                "school": "计算机学院",
                "start_url": "https://example.edu/faculty",
                "llm_profile_id": None,
            },
        )
        self.assertEqual(create_response.status_code, 201, msg=create_response.text)
        job_id = create_response.json()["id"]
        self._seed_page_and_candidates(job_id)
        self._set_job_status(job_id, "completed")

        response = self.client.post(
            f"/api/crawl-jobs/{job_id}/enrich",
            json={"candidate_ids": [1]},
        )

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["detail"], "抓取任务尚未进入审核状态")

    def test_enrich_selected_candidates_allows_partially_completed_job(self) -> None:
        create_response = self.client.post(
            "/api/crawl-jobs",
            json={
                "university": "示例大学",
                "school": "计算机学院",
                "start_url": "https://example.edu/faculty",
                "llm_profile_id": None,
            },
        )
        self.assertEqual(create_response.status_code, 201, msg=create_response.text)
        job_id = create_response.json()["id"]
        self._seed_page_and_candidates(job_id)
        self._seed_default_llm_profile()
        self._set_job_status(job_id, "partially_completed")
        candidates = self.client.get(f"/api/crawl-jobs/{job_id}/candidates").json()
        selected_id = candidates[0]["id"]

        response = self.client.post(
            f"/api/crawl-jobs/{job_id}/enrich",
            json={"candidate_ids": [selected_id]},
        )

        self.assertEqual(response.status_code, 200, msg=response.text)
        self.assertEqual(response.json()["selected_count"], 1)
        self.assertEqual(response.json()["enriched_count"], 0)
        self.assertIn("已加入补全队列", response.json()["message"])
        self.assertEqual(
            self.client.get(f"/api/crawl-jobs/{job_id}").json()["status"], "running"
        )

    def test_resume_review_allows_canceled_job_with_candidates(self) -> None:
        create_response = self.client.post(
            "/api/crawl-jobs",
            json={
                "university": "示例大学",
                "school": "计算机学院",
                "start_url": "https://example.edu/faculty",
                "llm_profile_id": None,
            },
        )
        self.assertEqual(create_response.status_code, 201, msg=create_response.text)
        job_id = create_response.json()["id"]
        self._seed_page_and_candidates(job_id)
        self._set_job_status(job_id, "canceled")

        response = self.client.post(f"/api/crawl-jobs/{job_id}/resume-review")

        self.assertEqual(response.status_code, 200, msg=response.text)
        self.assertEqual(response.json()["status"], "needs_review")

    def test_resume_review_rejects_failed_job_without_candidates(self) -> None:
        create_response = self.client.post(
            "/api/crawl-jobs",
            json={
                "university": "示例大学",
                "school": "计算机学院",
                "start_url": "https://example.edu/faculty",
                "llm_profile_id": None,
            },
        )
        self.assertEqual(create_response.status_code, 201, msg=create_response.text)
        job_id = create_response.json()["id"]
        self._set_job_status(job_id, "failed")

        response = self.client.post(f"/api/crawl-jobs/{job_id}/resume-review")

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["detail"], "当前任务没有可审核的候选导师")

    def test_enrich_selected_candidates_requires_candidate_ids(self) -> None:
        create_response = self.client.post(
            "/api/crawl-jobs",
            json={
                "university": "示例大学",
                "school": "计算机学院",
                "start_url": "https://example.edu/faculty",
                "llm_profile_id": None,
            },
        )
        self.assertEqual(create_response.status_code, 201, msg=create_response.text)
        self._set_job_status(create_response.json()["id"], "needs_review")

        response = self.client.post(
            f"/api/crawl-jobs/{create_response.json()['id']}/enrich",
            json={"candidate_ids": []},
        )

        self.assertEqual(response.status_code, 400)

    def test_enrich_selected_candidates_returns_summary(self) -> None:
        create_response = self.client.post(
            "/api/crawl-jobs",
            json={
                "university": "示例大学",
                "school": "计算机学院",
                "start_url": "https://example.edu/faculty",
                "llm_profile_id": None,
            },
        )
        self.assertEqual(create_response.status_code, 201, msg=create_response.text)
        job_id = create_response.json()["id"]
        self._seed_page_and_candidates(job_id)
        self._seed_default_llm_profile()
        self._set_job_status(job_id, "needs_review")
        candidates = self.client.get(f"/api/crawl-jobs/{job_id}/candidates").json()
        selected_id = candidates[0]["id"]

        response = self.client.post(
            f"/api/crawl-jobs/{job_id}/enrich",
            json={"candidate_ids": [selected_id]},
        )

        self.assertEqual(response.status_code, 200, msg=response.text)
        self.assertEqual(response.json()["selected_count"], 1)
        self.assertEqual(response.json()["enriched_count"], 0)
        self.assertIn("已加入补全队列", response.json()["message"])

    def test_approve_partially_completed_job_can_finish_remaining_candidates(
        self,
    ) -> None:
        create_response = self.client.post(
            "/api/crawl-jobs",
            json={
                "university": "示例大学",
                "school": "计算机学院",
                "start_url": "https://example.edu/faculty",
                "llm_profile_id": None,
            },
        )
        self.assertEqual(create_response.status_code, 201, msg=create_response.text)
        job_id = create_response.json()["id"]
        self._seed_page_and_candidates(job_id)
        self._set_job_status(job_id, "needs_review")
        initial_candidates = self.client.get(
            f"/api/crawl-jobs/{job_id}/candidates"
        ).json()

        first_response = self.client.post(
            f"/api/crawl-jobs/{job_id}/approve",
            json={"candidate_ids": [initial_candidates[0]["id"]]},
        )
        self.assertEqual(first_response.status_code, 200, msg=first_response.text)
        self.assertEqual(
            self.client.get(f"/api/crawl-jobs/{job_id}").json()["status"],
            "partially_completed",
        )

        refreshed_candidates = self.client.get(
            f"/api/crawl-jobs/{job_id}/candidates"
        ).json()
        no_email_candidate = next(
            candidate
            for candidate in refreshed_candidates
            if candidate["email"] is None
        )
        patch_response = self.client.patch(
            f"/api/crawl-jobs/candidates/{no_email_candidate['id']}",
            json={
                "name": no_email_candidate["name"],
                "email": "filled@example.edu",
                "title": no_email_candidate["title"],
                "university": no_email_candidate["university"],
                "school": no_email_candidate["school"],
                "department": no_email_candidate["department"],
                "research_direction": no_email_candidate["research_direction"],
                "recent_papers": no_email_candidate["recent_papers"],
                "profile_url": no_email_candidate["profile_url"],
                "source_url": no_email_candidate["source_url"],
                "review_status": "pending",
            },
        )
        self.assertEqual(patch_response.status_code, 200, msg=patch_response.text)

        remaining_candidates = self.client.get(
            f"/api/crawl-jobs/{job_id}/candidates"
        ).json()
        remaining_ids = [
            candidate["id"]
            for candidate in remaining_candidates
            if candidate["review_status"] == "pending"
        ]

        second_response = self.client.post(
            f"/api/crawl-jobs/{job_id}/approve",
            json={"candidate_ids": remaining_ids},
        )

        self.assertEqual(second_response.status_code, 200, msg=second_response.text)
        self.assertEqual(
            self.client.get(f"/api/crawl-jobs/{job_id}").json()["status"], "completed"
        )

    def test_approve_rejects_candidates_from_other_job(self) -> None:
        first_response = self.client.post(
            "/api/crawl-jobs",
            json={
                "university": "示例大学",
                "school": "计算机学院",
                "start_url": "https://example.edu/faculty",
                "llm_profile_id": None,
            },
        )
        second_response = self.client.post(
            "/api/crawl-jobs",
            json={
                "university": "另一大学",
                "school": "信息学院",
                "start_url": "https://other.example.edu/faculty",
                "llm_profile_id": None,
            },
        )
        self.assertEqual(first_response.status_code, 201, msg=first_response.text)
        self.assertEqual(second_response.status_code, 201, msg=second_response.text)
        first_job_id = first_response.json()["id"]
        second_job_id = second_response.json()["id"]
        self._seed_page_and_candidates(first_job_id)
        self._set_job_status(second_job_id, "needs_review")
        other_candidates = self.client.get(
            f"/api/crawl-jobs/{first_job_id}/candidates"
        ).json()

        response = self.client.post(
            f"/api/crawl-jobs/{second_job_id}/approve",
            json={"candidate_ids": [other_candidates[0]["id"]]},
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["detail"], "未找到可审核的候选导师")

    def test_missing_crawl_job_returns_chinese_message(self) -> None:
        response = self.client.get("/api/crawl-jobs/999999")

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["detail"], "未找到抓取任务")

    def _seed_pending_discovery_work_with_candidate(self, job_id: int) -> None:
        async def _seed() -> None:
            from sqlalchemy import select

            from app.core.database import get_session_factory
            from app.models import (
                CrawlCandidate,
                CrawlPage,
                CrawlPageChunk,
                CrawlPageChunkStatus,
                CrawlPageStatus,
                CrawlPageTask,
                CrawlPageTaskStatus,
            )

            async with get_session_factory()() as session:
                page = CrawlPage(
                    job_id=job_id,
                    url="https://example.edu/faculty",
                    parent_url=None,
                    fetch_method="browser",
                    page_type="faculty_list",
                    status=CrawlPageStatus.SUCCEEDED.value,
                    title="Faculty",
                    text_excerpt="Faculty page",
                    error_message=None,
                )
                session.add(page)
                await session.flush()
                page_task = await session.scalar(
                    select(CrawlPageTask).where(CrawlPageTask.job_id == job_id)
                )
                self.assertIsNotNone(page_task)
                page_task.status = CrawlPageTaskStatus.PENDING.value
                session.add(
                    CrawlCandidate(
                        job_id=job_id,
                        name="待补全导师",
                        profile_url="https://example.edu/profile",
                        confidence=0.8,
                    ),
                )
                session.add(
                    CrawlPageChunk(
                        job_id=job_id,
                        page_id=page.id,
                        source_url="https://example.edu/faculty",
                        page_fingerprint="page-pending",
                        chunk_id="chunk-pending",
                        chunk_index=0,
                        chunk_hash="hash-pending",
                        status=CrawlPageChunkStatus.PENDING.value,
                        content="待处理 chunk",
                        token_estimate=10,
                    ),
                )
                await session.commit()

        asyncio.run(_seed())

    def _seed_processing_runtime_work(self, job_id: int) -> None:
        async def _seed() -> None:
            from sqlalchemy import select

            from app.core.database import get_session_factory
            from app.models import (
                CrawlCandidate,
                CrawlCandidateEnrichmentTask,
                CrawlCandidateEnrichmentTaskStatus,
                CrawlPage,
                CrawlPageChunk,
                CrawlPageChunkStatus,
                CrawlPageStatus,
                CrawlPageTask,
                CrawlPageTaskStatus,
            )

            async with get_session_factory()() as session:
                page = CrawlPage(
                    job_id=job_id,
                    url="https://example.edu/faculty",
                    parent_url=None,
                    fetch_method="http",
                    page_type="faculty_list",
                    status=CrawlPageStatus.SUCCEEDED.value,
                    title="Faculty",
                    text_excerpt="Faculty page",
                    error_message=None,
                )
                session.add(page)
                await session.flush()
                candidate = CrawlCandidate(
                    job_id=job_id,
                    name="处理中导师",
                    profile_url="https://example.edu/profile",
                    confidence=0.8,
                )
                session.add(candidate)
                await session.flush()
                page_task = await session.scalar(
                    select(CrawlPageTask).where(CrawlPageTask.job_id == job_id)
                )
                self.assertIsNotNone(page_task)
                page_task.status = CrawlPageTaskStatus.PROCESSING.value
                page_task.worker_id = "w-page"
                session.add_all(
                    [
                        CrawlPageChunk(
                            job_id=job_id,
                            page_id=page.id,
                            source_url="https://example.edu/faculty",
                            page_fingerprint="page-processing",
                            chunk_id="chunk-processing",
                            chunk_index=0,
                            chunk_hash="hash-processing",
                            status=CrawlPageChunkStatus.PROCESSING.value,
                            worker_id="w-chunk",
                            content="处理中 chunk",
                            token_estimate=10,
                        ),
                        CrawlCandidateEnrichmentTask(
                            job_id=job_id,
                            candidate_id=candidate.id,
                            status=CrawlCandidateEnrichmentTaskStatus.PROCESSING.value,
                            worker_id="w-enrich",
                        ),
                    ]
                )
                await session.commit()

        asyncio.run(_seed())

    def _list_runtime_work_statuses(self, job_id: int) -> dict[str, list[str]]:
        async def _list() -> dict[str, list[str]]:
            from sqlalchemy import select

            from app.core.database import get_session_factory
            from app.models import (
                CrawlCandidateEnrichmentTask,
                CrawlPageChunk,
                CrawlPageTask,
            )

            async with get_session_factory()() as session:
                page_tasks = list(
                    await session.scalars(
                        select(CrawlPageTask.status)
                        .where(CrawlPageTask.job_id == job_id)
                        .order_by(CrawlPageTask.id.asc())
                    )
                )
                chunks = list(
                    await session.scalars(
                        select(CrawlPageChunk.status)
                        .where(CrawlPageChunk.job_id == job_id)
                        .order_by(CrawlPageChunk.id.asc())
                    )
                )
                enrichment_tasks = list(
                    await session.scalars(
                        select(CrawlCandidateEnrichmentTask.status)
                        .where(CrawlCandidateEnrichmentTask.job_id == job_id)
                        .order_by(CrawlCandidateEnrichmentTask.id.asc())
                    )
                )
                return {
                    "page_tasks": page_tasks,
                    "chunks": chunks,
                    "enrichment_tasks": enrichment_tasks,
                }

        return asyncio.run(_list())

    def _seed_page_and_candidates(self, job_id: int) -> None:
        async def _seed() -> None:
            from app.core.database import get_session_factory
            from app.models import CrawlCandidate, CrawlPage, CrawlPageStatus

            async with get_session_factory()() as session:
                session.add(
                    CrawlPage(
                        job_id=job_id,
                        url="https://example.edu/faculty",
                        parent_url=None,
                        fetch_method="http",
                        page_type="faculty_list",
                        status=CrawlPageStatus.SUCCEEDED.value,
                        title="Faculty",
                        text_excerpt="Faculty page",
                        error_message=None,
                    ),
                )
                session.add_all(
                    [
                        CrawlCandidate(
                            job_id=job_id,
                            name="低分导师",
                            email="low@example.edu",
                            title="Assistant Professor",
                            university="示例大学",
                            school="计算机学院",
                            department="CS",
                            research_direction="数据库",
                            recent_papers=None,
                            profile_url="https://example.edu/low",
                            source_url="https://example.edu/faculty",
                            confidence=0.5,
                        ),
                        CrawlCandidate(
                            job_id=job_id,
                            name="高分导师",
                            email="high@example.edu",
                            title="Professor",
                            university="示例大学",
                            school="计算机学院",
                            department="CS",
                            research_direction="机器学习",
                            recent_papers=["Paper A"],
                            profile_url="https://example.edu/high",
                            source_url="https://example.edu/faculty",
                            confidence=0.9,
                        ),
                        CrawlCandidate(
                            job_id=job_id,
                            name="无邮箱导师",
                            email=None,
                            title="Professor",
                            university="示例大学",
                            school="计算机学院",
                            department="CS",
                            research_direction="系统",
                            recent_papers=[],
                            profile_url=None,
                            source_url="https://example.edu/faculty",
                            confidence=0.2,
                        ),
                    ],
                )
                await session.commit()

        asyncio.run(_seed())

    def _seed_page_candidate_and_chunk(self, job_id: int) -> None:
        async def _seed() -> None:
            from app.core.database import get_session_factory
            from app.models import (
                CrawlCandidate,
                CrawlPage,
                CrawlPageChunk,
                CrawlPageChunkStatus,
                CrawlPageStatus,
            )

            async with get_session_factory()() as session:
                page = CrawlPage(
                    job_id=job_id,
                    url="https://example.edu/faculty",
                    parent_url=None,
                    fetch_method="http",
                    page_type="faculty_list",
                    status=CrawlPageStatus.SUCCEEDED.value,
                    title="Faculty",
                    text_excerpt="Faculty page",
                    error_message=None,
                )
                session.add(page)
                await session.flush()
                session.add(
                    CrawlCandidate(
                        job_id=job_id,
                        name="旧导师",
                        email="old@example.edu",
                        title="Professor",
                        university="示例大学",
                        school="计算机学院",
                        department="CS",
                        research_direction="旧方向",
                        recent_papers=[],
                        profile_url="https://example.edu/old",
                        source_url="https://example.edu/faculty",
                        confidence=0.8,
                    ),
                )
                session.add(
                    CrawlPageChunk(
                        job_id=job_id,
                        page_id=page.id,
                        source_url="https://example.edu/faculty",
                        page_fingerprint="fp-old",
                        chunk_id="old-chunk",
                        chunk_index=0,
                        chunk_hash="hash-old",
                        status=CrawlPageChunkStatus.COMPLETED.value,
                        content="旧 chunk",
                        token_estimate=10,
                    ),
                )
                await session.commit()

        asyncio.run(_seed())

    def _count_page_chunks(self, job_id: int) -> int:
        async def _count() -> int:
            from sqlalchemy import func, select

            from app.core.database import get_session_factory
            from app.models import CrawlPageChunk

            async with get_session_factory()() as session:
                count = await session.scalar(
                    select(func.count())
                    .select_from(CrawlPageChunk)
                    .where(CrawlPageChunk.job_id == job_id),
                )
                return int(count or 0)

        return asyncio.run(_count())

    def _mark_page_tasks_succeeded(self, job_id: int) -> None:
        async def _mark() -> None:
            from sqlalchemy import select

            from app.core.database import get_session_factory
            from app.models import CrawlPageTask, CrawlPageTaskStatus

            async with get_session_factory()() as session:
                tasks = list(
                    await session.scalars(
                        select(CrawlPageTask).where(CrawlPageTask.job_id == job_id)
                    )
                )
                for task in tasks:
                    task.status = CrawlPageTaskStatus.SUCCEEDED.value
                await session.commit()

        asyncio.run(_mark())

    def _list_page_task_statuses(self, job_id: int) -> list[tuple[str, str]]:
        async def _list() -> list[tuple[str, str]]:
            from sqlalchemy import select

            from app.core.database import get_session_factory
            from app.models import CrawlPageTask

            async with get_session_factory()() as session:
                tasks = list(
                    await session.scalars(
                        select(CrawlPageTask)
                        .where(CrawlPageTask.job_id == job_id)
                        .order_by(CrawlPageTask.id.asc()),
                    )
                )
                return [(task.normalized_url, task.status) for task in tasks]

        return asyncio.run(_list())

    def _seed_candidate(self, job_id: int, *, name: str, profile_url: str) -> None:
        import sqlite3

        connection = sqlite3.connect(self.db_path)
        try:
            connection.execute(
                """
                INSERT INTO crawl_candidates (
                    job_id, name, profile_url, confidence, review_status, created_at, updated_at
                ) VALUES (?, ?, ?, 0.9, 'pending', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                """,
                (job_id, name, profile_url),
            )
            connection.commit()
        finally:
            connection.close()

    def _seed_candidate_with_fields(
        self,
        job_id: int,
        *,
        name: str,
        email: str | None,
        title: str | None,
        department: str | None,
        research_direction: str | None,
        recent_papers: list[str],
        profile_url: str,
    ) -> None:
        import json
        import sqlite3

        connection = sqlite3.connect(self.db_path)
        try:
            connection.execute(
                """
                INSERT INTO crawl_candidates (
                    job_id, name, email, title, university, school, department,
                    research_direction, recent_papers, profile_url, confidence,
                    review_status, created_at, updated_at
                ) VALUES (?, ?, ?, ?, '示例大学', '计算机学院', ?, ?, ?, ?, 0.9, 'pending', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                """,
                (
                    job_id,
                    name,
                    email,
                    title,
                    department,
                    research_direction,
                    json.dumps(recent_papers),
                    profile_url,
                ),
            )
            connection.commit()
        finally:
            connection.close()

    def _seed_enrichment_task(
        self,
        candidate_id: int,
        *,
        status: str,
        last_error: str | None,
    ) -> None:
        import sqlite3

        connection = sqlite3.connect(self.db_path)
        try:
            row = connection.execute(
                "SELECT job_id FROM crawl_candidates WHERE id = ?",
                (candidate_id,),
            ).fetchone()
            self.assertIsNotNone(row)
            connection.execute(
                """
                INSERT INTO crawl_candidate_enrichment_tasks (
                    job_id, candidate_id, status, last_error, created_at, updated_at
                ) VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                """,
                (int(row[0]), candidate_id, status, last_error),
            )
            connection.commit()
        finally:
            connection.close()

    def _latest_candidate_id(self, job_id: int) -> int:
        import sqlite3

        connection = sqlite3.connect(self.db_path)
        try:
            row = connection.execute(
                "SELECT id FROM crawl_candidates WHERE job_id = ? ORDER BY id DESC LIMIT 1",
                (job_id,),
            ).fetchone()
            self.assertIsNotNone(row)
            return int(row[0])
        finally:
            connection.close()

    def _get_job_llm_profile_id(self, job_id: int) -> int | None:
        import sqlite3

        connection = sqlite3.connect(self.db_path)
        try:
            row = connection.execute(
                "SELECT llm_profile_id FROM crawl_jobs WHERE id = ?",
                (job_id,),
            ).fetchone()
            return None if row is None else row[0]
        finally:
            connection.close()

    def _list_operation_logs(
        self, event_name: str, entity_id: str
    ) -> list[dict[str, object]]:
        import json
        import sqlite3

        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        try:
            rows = connection.execute(
                """
                SELECT event_name, entity_id, metadata
                FROM operation_logs
                WHERE event_name = ? AND entity_id = ?
                ORDER BY id ASC
                """,
                (event_name, entity_id),
            ).fetchall()
            return [
                {
                    "event_name": row["event_name"],
                    "entity_id": row["entity_id"],
                    "metadata": json.loads(row["metadata"]),
                }
                for row in rows
            ]
        finally:
            connection.close()

    def _create_llm_profile(self, name: str, model_name: str) -> int:
        response = self.client.post(
            "/api/llm-profiles",
            json={
                "name": name,
                "provider": "openai",
                "api_base_url": "https://api.example.com/v1",
                "api_key": "test-key",
                "model_name": model_name,
                "matcher_prompt_template": None,
                "writer_prompt_template": None,
                "temperature": 0.2,
                "max_tokens": None,
                "is_default": False,
            },
        )
        self.assertEqual(response.status_code, 201, msg=response.text)
        return int(response.json()["id"])

    def _set_job_status(self, job_id: int, status: str) -> None:
        async def _set_status() -> None:
            from app.core.database import get_session_factory
            from app.models import CrawlJob

            async with get_session_factory()() as session:
                job = await session.get(CrawlJob, job_id)
                self.assertIsNotNone(job)
                job.status = status
                await session.commit()

        asyncio.run(_set_status())

    def _set_job_start_urls(self, job_id: int, start_urls: list[str]) -> None:
        async def _set_start_urls() -> None:
            from app.core.database import get_session_factory
            from app.models import CrawlJob

            async with get_session_factory()() as session:
                job = await session.get(CrawlJob, job_id)
                self.assertIsNotNone(job)
                job.start_url = start_urls[0]
                job.start_urls = start_urls
                await session.commit()

        asyncio.run(_set_start_urls())

    def _seed_default_llm_profile(self) -> None:
        async def _seed_profile() -> None:
            from app.core.database import get_session_factory
            from app.models import LLMProfile

            async with get_session_factory()() as session:
                session.add(
                    LLMProfile(
                        name="default",
                        provider="openai",
                        api_key="test-key",
                        model_name="test-model",
                        is_default=True,
                    ),
                )
                await session.commit()

        asyncio.run(_seed_profile())

    def _set_job_trace(self, job_id: int, trace: list[dict[str, object]]) -> None:
        async def _set_trace() -> None:
            from app.core.database import get_session_factory
            from app.models import CrawlJob

            async with get_session_factory()() as session:
                job = await session.get(CrawlJob, job_id)
                self.assertIsNotNone(job)
                job.agent_trace = trace
                await session.commit()

        asyncio.run(_set_trace())

    def _list_job_runs(self, job_id: int) -> list[dict[str, object]]:
        async def _list_runs() -> list[dict[str, object]]:
            from app.core.database import get_session_factory
            from app.models import CrawlJobRun
            from sqlalchemy import select

            async with get_session_factory()() as session:
                runs = list(
                    (
                        await session.execute(
                            select(CrawlJobRun)
                            .where(CrawlJobRun.job_id == job_id)
                            .order_by(CrawlJobRun.attempt_number.asc()),
                        )
                    ).scalars(),
                )
                return [
                    {
                        "id": run.id,
                        "attempt_number": run.attempt_number,
                        "status": run.status,
                        "finished_at": run.finished_at,
                    }
                    for run in runs
                ]

        return asyncio.run(_list_runs())

    def _get_job_current_run_id(self, job_id: int) -> int | None:
        async def _get_current_run_id() -> int | None:
            from app.core.database import get_session_factory
            from app.models import CrawlJob

            async with get_session_factory()() as session:
                job = await session.get(CrawlJob, job_id)
                self.assertIsNotNone(job)
                return job.current_run_id

        return asyncio.run(_get_current_run_id())


if __name__ == "__main__":
    unittest.main()
