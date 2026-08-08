from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models import Base, CrawlJob, CrawlJobStatus, LLMProfile
from app.modules.crawler.jobs.llm_context import (
    public_llm_context,
    resolve_crawl_job_runtime_profile,
    snapshot_crawl_job_llm_profile,
)
from app.modules.crawler.jobs.runs import create_initial_crawl_job_run


class CrawlJobLLMContextTests(unittest.IsolatedAsyncioTestCase):
    async def test_runtime_uses_frozen_settings_and_current_credentials(self) -> None:
        descriptor, database_path = tempfile.mkstemp(suffix=".db")
        os.close(descriptor)
        engine = create_async_engine(f"sqlite+aiosqlite:///{Path(database_path).as_posix()}")
        try:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            session_factory = async_sessionmaker(engine, expire_on_commit=False)
            async with session_factory() as session:
                profile = LLMProfile(
                    name="抓取模型",
                    provider="openai",
                    api_base_url="https://old.example/v1",
                    api_key="old-key",
                    model_name="step-3.5-flash",
                    temperature=0.2,
                    max_tokens=2048,
                    is_default=True,
                )
                session.add(profile)
                await session.flush()
                job = CrawlJob(
                    university="示例大学",
                    school="计算机学院",
                    start_url="https://example.edu/faculty",
                    llm_profile_id=profile.id,
                    status=CrawlJobStatus.QUEUED.value,
                )
                session.add(job)
                await session.flush()
                run = await create_initial_crawl_job_run(session, job)
                snapshot = await snapshot_crawl_job_llm_profile(
                    session,
                    job,
                    profile,
                    source="explicit",
                )
                await session.commit()

                profile.model_name = "changed-model"
                profile.api_base_url = "https://new.example/v1"
                profile.api_key = "rotated-key"
                profile.temperature = 0.9
                await session.commit()

                runtime = await resolve_crawl_job_runtime_profile(session, job)
                assert runtime is not None
                self.assertEqual(runtime.model_name, "step-3.5-flash")
                self.assertEqual(runtime.api_base_url, "https://old.example/v1")
                self.assertEqual(runtime.temperature, 0.2)
                self.assertEqual(runtime.api_key, "rotated-key")
                self.assertEqual(run.llm_runtime_snapshot, snapshot)
                self.assertEqual(
                    public_llm_context(snapshot, effective_models=["step-3.5-flash"]),
                    {
                        "profile_source": "explicit",
                        "profile_id": profile.id,
                        "profile_revision": snapshot["profile_revision"],
                        "profile_name": "抓取模型",
                        "provider": "openai",
                        "model_name": "step-3.5-flash",
                        "effective_models": ["step-3.5-flash"],
                    },
                )
        finally:
            await engine.dispose()
            try:
                os.unlink(database_path)
            except FileNotFoundError:
                pass
