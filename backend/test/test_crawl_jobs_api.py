from __future__ import annotations

import asyncio
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from test.migrated_database import create_migrated_sqlite_database


class CrawlJobsApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "crawl_jobs_api_test.db"
        os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{self.db_path.as_posix()}"
        os.environ["ENABLE_BACKGROUND_WORKERS"] = "0"
        create_migrated_sqlite_database(self.db_path)
        self.getaddrinfo_patcher = patch(
            "app.services.crawler_tools.socket.getaddrinfo",
            return_value=[
                (0, 0, 0, "", ("93.184.216.34", 443)),
            ],
        )
        self.getaddrinfo_patcher.start()

        from app.core.config import get_settings
        from app.core.database import dispose_engine, get_engine, get_session_factory
        from main import create_app

        get_settings.cache_clear()
        if get_engine.cache_info().currsize:
            asyncio.run(dispose_engine())
        get_session_factory.cache_clear()
        get_settings.cache_clear()

        self.client = TestClient(create_app())

    def tearDown(self) -> None:
        self.client.close()
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

    def test_create_crawl_job_allows_domain_without_dns_resolution(self) -> None:
        with patch(
            "app.services.crawler_tools.socket.getaddrinfo",
            side_effect=AssertionError("Creating a crawl job should not resolve domain names"),
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
        self.assertEqual(response.json()["start_url"], "https://cai.jxufe.edu.cn/lists/26.html")

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
        self.assertEqual([item["name"] for item in candidates], ["高分导师", "低分导师", "无邮箱导师"])
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
        self.assertEqual(no_email_patch_response.status_code, 200, msg=no_email_patch_response.text)
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

        cancel_completed_response = self.client.post(f"/api/crawl-jobs/{job['id']}/cancel")
        self.assertEqual(cancel_completed_response.status_code, 200)
        self.assertEqual(cancel_completed_response.json()["status"], "completed")

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

    def test_pause_rejects_terminal_or_review_jobs(self) -> None:
        for job_status in ("needs_review", "partially_completed", "completed", "failed", "canceled"):
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
                self.assertEqual(create_response.status_code, 201, msg=create_response.text)
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

    def test_crawl_job_events_include_status_trace_page_and_candidate_messages(self) -> None:
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
                    "event_type": "tool_call",
                    "message": "Agent 调用 crawl_page",
                    "created_at": "2026-04-26T10:01:00+00:00",
                },
            ],
        )

        response = self.client.get(f"/api/crawl-jobs/{job_id}/events")

        self.assertEqual(response.status_code, 200, msg=response.text)
        messages = [event["message"] for event in response.json()]
        self.assertIn("任务进入待审核", messages)
        self.assertIn("Agent 调用 crawl_page", messages)
        self.assertIn("已抓取页面：Faculty", messages)
        self.assertIn("发现候选导师：高分导师", messages)

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

        class Summary:
            selected_count = 1
            enriched_count = 1
            unchanged_count = 0
            failed_count = 0

        async def fake_enrich_selected(*args, **kwargs):
            self.assertEqual(kwargs["job_id"], job_id)
            self.assertEqual(kwargs["candidate_ids"], [selected_id])
            return Summary()

        with patch("app.api.crawl_jobs.enrich_selected_crawl_candidates", new=fake_enrich_selected):
            response = self.client.post(
                f"/api/crawl-jobs/{job_id}/enrich",
                json={"candidate_ids": [selected_id]},
            )

        self.assertEqual(response.status_code, 200, msg=response.text)
        self.assertEqual(response.json()["selected_count"], 1)
        self.assertEqual(response.json()["enriched_count"], 1)
        self.assertIn("补全完成", response.json()["message"])
        self.assertEqual(self.client.get(f"/api/crawl-jobs/{job_id}").json()["status"], "partially_completed")

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

        class Summary:
            selected_count = 1
            enriched_count = 1
            unchanged_count = 0
            failed_count = 0

        async def fake_enrich_selected(*args, **kwargs):
            self.assertEqual(kwargs["job_id"], job_id)
            self.assertEqual(kwargs["candidate_ids"], [selected_id])
            return Summary()

        with patch("app.api.crawl_jobs.enrich_selected_crawl_candidates", new=fake_enrich_selected):
            response = self.client.post(
                f"/api/crawl-jobs/{job_id}/enrich",
                json={"candidate_ids": [selected_id]},
            )

        self.assertEqual(response.status_code, 200, msg=response.text)
        self.assertEqual(response.json()["selected_count"], 1)
        self.assertEqual(response.json()["enriched_count"], 1)
        self.assertIn("补全完成", response.json()["message"])

    def test_approve_partially_completed_job_can_finish_remaining_candidates(self) -> None:
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
        initial_candidates = self.client.get(f"/api/crawl-jobs/{job_id}/candidates").json()

        first_response = self.client.post(
            f"/api/crawl-jobs/{job_id}/approve",
            json={"candidate_ids": [initial_candidates[0]["id"]]},
        )
        self.assertEqual(first_response.status_code, 200, msg=first_response.text)
        self.assertEqual(self.client.get(f"/api/crawl-jobs/{job_id}").json()["status"], "partially_completed")

        refreshed_candidates = self.client.get(f"/api/crawl-jobs/{job_id}/candidates").json()
        no_email_candidate = next(candidate for candidate in refreshed_candidates if candidate["email"] is None)
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

        remaining_candidates = self.client.get(f"/api/crawl-jobs/{job_id}/candidates").json()
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
        self.assertEqual(self.client.get(f"/api/crawl-jobs/{job_id}").json()["status"], "completed")

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
        other_candidates = self.client.get(f"/api/crawl-jobs/{first_job_id}/candidates").json()

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
            from app.models import CrawlCandidate, CrawlPage, CrawlPageChunk, CrawlPageChunkStatus, CrawlPageStatus

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
                    select(func.count()).select_from(CrawlPageChunk).where(CrawlPageChunk.job_id == job_id),
                )
                return int(count or 0)

        return asyncio.run(_count())

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
