from __future__ import annotations

import asyncio
import os
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient


class RuntimeSettingsApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.temp_dir = tempfile.TemporaryDirectory()
        cls.db_path = Path(cls.temp_dir.name) / "runtime_settings.db"
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

    def test_get_runtime_settings_returns_defaults(self) -> None:
        response = self.client.get("/api/runtime-settings")

        self.assertEqual(response.status_code, 200, msg=response.text)
        payload = response.json()
        self.assertEqual(payload["match_analysis_job_item_concurrency"], 3)
        self.assertEqual(payload["crawler_host_concurrency"], 1)
        self.assertEqual(payload["draft_max_tokens"], 6000)
        self.assertEqual(payload["batch_draft_generation_concurrency"], 3)
        self.assertEqual(payload["draft_rewrite_intensity"], "moderate")
        self.assertEqual(payload["draft_rewrite_tone"], "polite")
        self.assertEqual(payload["draft_rewrite_formality"], "balanced")
        self.assertEqual(payload["draft_rewrite_length"], "default")
        self.assertEqual(payload["draft_rewrite_specificity"], "balanced")
        self.assertEqual(payload["draft_template_preservation"], "structure_first")
        self.assertEqual(payload["draft_custom_instruction"], "")

    def test_patch_runtime_settings_updates_values_and_records_log(self) -> None:
        response = self.client.patch(
            "/api/runtime-settings",
            json={
                "match_analysis_job_worker_count": 2,
                "match_analysis_job_item_concurrency": 4,
                "match_analysis_job_interval_seconds": 5,
                "crawler_worker_count": 3,
                "crawler_profile_enrichment_concurrency": 4,
                "crawler_host_concurrency": 2,
                "draft_max_tokens": 4800,
                "batch_draft_generation_concurrency": 5,
                "draft_rewrite_intensity": "strong",
                "draft_rewrite_tone": "professional",
                "draft_rewrite_formality": "formal",
                "draft_rewrite_length": "more_detailed",
                "draft_rewrite_specificity": "detailed",
                "draft_template_preservation": "content_first",
                "draft_custom_instruction": "请少用套话，结尾保持简短。",
            },
        )

        self.assertEqual(response.status_code, 200, msg=response.text)
        self.assertEqual(response.json()["match_analysis_job_item_concurrency"], 4)
        self.assertEqual(response.json()["draft_max_tokens"], 4800)
        self.assertEqual(response.json()["batch_draft_generation_concurrency"], 5)
        self.assertEqual(response.json()["draft_rewrite_intensity"], "strong")
        self.assertEqual(response.json()["draft_rewrite_tone"], "professional")
        self.assertEqual(response.json()["draft_rewrite_formality"], "formal")
        self.assertEqual(response.json()["draft_rewrite_length"], "more_detailed")
        self.assertEqual(response.json()["draft_rewrite_specificity"], "detailed")
        self.assertEqual(response.json()["draft_template_preservation"], "content_first")
        self.assertEqual(response.json()["draft_custom_instruction"], "请少用套话，结尾保持简短。")
        logs = self.client.get(
            "/api/diagnostics/operation-logs",
            params={"event_name": "runtime_settings.updated"},
        )
        self.assertEqual(logs.status_code, 200, msg=logs.text)
        self.assertEqual(logs.json()["total"], 1)

    def test_patch_runtime_settings_rejects_out_of_range_values(self) -> None:
        response = self.client.patch(
            "/api/runtime-settings",
            json={
                "match_analysis_job_worker_count": 0,
                "match_analysis_job_item_concurrency": 4,
                "match_analysis_job_interval_seconds": 5,
                "crawler_worker_count": 3,
                "crawler_profile_enrichment_concurrency": 4,
                "crawler_host_concurrency": 2,
                "draft_max_tokens": 4800,
                "batch_draft_generation_concurrency": 3,
                "draft_rewrite_intensity": "moderate",
                "draft_rewrite_tone": "polite",
                "draft_rewrite_formality": "balanced",
                "draft_rewrite_length": "default",
                "draft_rewrite_specificity": "balanced",
                "draft_template_preservation": "structure_first",
                "draft_custom_instruction": "",
            },
        )

        self.assertEqual(response.status_code, 422)

    def test_patch_runtime_settings_rejects_draft_max_tokens_out_of_range(self) -> None:
        response = self.client.patch(
            "/api/runtime-settings",
            json={
                "match_analysis_job_worker_count": 1,
                "match_analysis_job_item_concurrency": 4,
                "match_analysis_job_interval_seconds": 5,
                "crawler_worker_count": 3,
                "crawler_profile_enrichment_concurrency": 4,
                "crawler_host_concurrency": 2,
                "draft_max_tokens": 0,
                "batch_draft_generation_concurrency": 3,
                "draft_rewrite_intensity": "moderate",
                "draft_rewrite_tone": "polite",
                "draft_rewrite_formality": "balanced",
                "draft_rewrite_length": "default",
                "draft_rewrite_specificity": "balanced",
                "draft_template_preservation": "structure_first",
                "draft_custom_instruction": "",
            },
        )

        self.assertEqual(response.status_code, 422)

    def test_patch_runtime_settings_rejects_batch_draft_concurrency_out_of_range(self) -> None:
        response = self.client.patch(
            "/api/runtime-settings",
            json={
                "match_analysis_job_worker_count": 1,
                "match_analysis_job_item_concurrency": 4,
                "match_analysis_job_interval_seconds": 5,
                "crawler_worker_count": 3,
                "crawler_profile_enrichment_concurrency": 4,
                "crawler_host_concurrency": 2,
                "draft_max_tokens": 4800,
                "batch_draft_generation_concurrency": 0,
                "draft_rewrite_intensity": "moderate",
                "draft_rewrite_tone": "polite",
                "draft_rewrite_formality": "balanced",
                "draft_rewrite_length": "default",
                "draft_rewrite_specificity": "balanced",
                "draft_template_preservation": "structure_first",
                "draft_custom_instruction": "",
            },
        )

        self.assertEqual(response.status_code, 422)

    def test_patch_runtime_settings_rejects_invalid_draft_rewrite_preference(self) -> None:
        response = self.client.patch(
            "/api/runtime-settings",
            json={
                "match_analysis_job_worker_count": 1,
                "match_analysis_job_item_concurrency": 4,
                "match_analysis_job_interval_seconds": 5,
                "crawler_worker_count": 3,
                "crawler_profile_enrichment_concurrency": 4,
                "crawler_host_concurrency": 2,
                "draft_max_tokens": 4800,
                "batch_draft_generation_concurrency": 3,
                "draft_rewrite_intensity": "rewrite_everything",
                "draft_rewrite_tone": "polite",
                "draft_rewrite_formality": "balanced",
                "draft_rewrite_length": "default",
                "draft_rewrite_specificity": "balanced",
                "draft_template_preservation": "structure_first",
                "draft_custom_instruction": "",
            },
        )

        self.assertEqual(response.status_code, 422)

    @classmethod
    async def _create_schema(cls) -> None:
        from app.core.database import get_engine
        from app.models import Base

        async with get_engine().begin() as connection:
            await connection.run_sync(Base.metadata.create_all)


if __name__ == "__main__":
    unittest.main()
