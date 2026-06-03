from __future__ import annotations

import os
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, patch

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from test.schema_database import create_schema_sqlite_database

from app.models import CrawlCandidate, CrawlCandidateEnrichmentTask, CrawlJob, CrawlJobStatus, CrawlPageChunk, CrawlPageChunkStatus, CrawlPageTask, CrawlWorkerTokenUsage, LLMProfile
from app.services.crawler_v2_chunk_worker import complete_current_chunk, invoke_v2_chunk_agent, run_crawler_v2_chunk_worker_once
from app.services.crawler_tools import ProfessorCandidatePayload


class CrawlerV2ChunkWorkerTests(unittest.IsolatedAsyncioTestCase):
    def test_chunk_prompt_includes_v1_quality_constraints(self) -> None:
        from app.services.crawler_v2_chunk_worker import build_v2_chunk_prompt

        prompt = build_v2_chunk_prompt(
            university="示例大学",
            school="计算机学院",
            source_url="https://example.edu/faculty",
            chunk_content="[张三](https://example.edu/zhang.html) 教授",
        )

        self.assertIn("最多 10 个候选", prompt)
        self.assertIn("缺少 email 且缺少 profile_url", prompt)
        self.assertIn("Markdown", prompt)
        self.assertIn("导师个人主页", prompt)
        self.assertIn("不能放入 discovered_urls", prompt)
        self.assertIn("只输出一个 JSON 对象", prompt)
        self.assertIn("输出示例", prompt)
        self.assertIn('"chunk_status": "completed"', prompt)
        self.assertIn('"chunk_status": "no_candidates"', prompt)
        self.assertIn('"chunk_status": "too_many_candidates"', prompt)
        self.assertIn('"candidates": []', prompt)
        self.assertIn('"discovered_urls": []', prompt)
    def test_chunk_prompt_treats_markdown_profile_links_as_candidates(self) -> None:
        from app.services.crawler_v2_chunk_worker import build_v2_chunk_prompt

        chunk_content = "\n".join(
            f"[教师{i}](https://faculty.example.edu/t{i}/main.psp)"
            for i in range(12)
        )

        prompt = build_v2_chunk_prompt(
            university="示例大学",
            school="计算机学院",
            source_url="https://example.edu/faculty",
            chunk_content=chunk_content,
        )

        self.assertIn("姓名 + profile_url", prompt)
        self.assertIn("不是 no_candidates", prompt)
        self.assertIn("必须返回 too_many_candidates", prompt)
        self.assertIn("no_candidates 只允许", prompt)
        self.assertLess(prompt.index("输出示例（当前 chunk 明确超过 10 个候选）"), prompt.index("输出示例（无候选）"))

    async def asyncSetUp(self) -> None:
        fd, self.db_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        create_schema_sqlite_database(Path(self.db_path))
        self.engine = create_async_engine(f"sqlite+aiosqlite:///{Path(self.db_path).as_posix()}")
        self.session_factory = async_sessionmaker(self.engine, expire_on_commit=False)
        self._thinking_adaptation_patch = patch(
            "app.services.crawler_v2_chunk_worker.ensure_thinking_adaptation",
            new=AsyncMock(return_value=None),
        )
        self._thinking_adaptation_patch.start()

    async def asyncTearDown(self) -> None:
        self._thinking_adaptation_patch.stop()
        await self.engine.dispose()
        try:
            os.unlink(self.db_path)
        except FileNotFoundError:
            pass

    async def test_complete_chunk_marks_terminal_when_split_cannot_continue(self) -> None:
        _, chunk_id = await self._seed_processing_chunk()
        async with self.session_factory() as session:
            chunk = await session.get(CrawlPageChunk, chunk_id)
            assert chunk is not None
            chunk.content = "张三"
            chunk.split_depth = 4
            await session.commit()
        candidates = [
            ProfessorCandidatePayload(name=f"教师{i}", profile_url=f"https://example.edu/t{i}.html", confidence=0.9)
            for i in range(11)
        ]

        result = await complete_current_chunk(
            self.session_factory,
            chunk_id=chunk_id,
            worker_id="w1",
            candidates=candidates,
            discovered_urls=[],
            chunk_status="completed",
        )

        self.assertEqual(result["status"], CrawlPageChunkStatus.FAILED_TERMINAL.value)
        async with self.session_factory() as session:
            chunk = await session.get(CrawlPageChunk, chunk_id)
        assert chunk is not None
        self.assertEqual(chunk.status, CrawlPageChunkStatus.FAILED_TERMINAL.value)
    async def test_complete_chunk_too_many_candidates_triggers_backend_split(self) -> None:
        _, chunk_id = await self._seed_processing_chunk()
        async with self.session_factory() as session:
            chunk = await session.get(CrawlPageChunk, chunk_id)
            assert chunk is not None
            chunk.content = "\n".join(f"教师{i} https://example.edu/t{i}.html 研究方向 软件工程 人工智能 数据挖掘" for i in range(80))
            await session.commit()

        result = await complete_current_chunk(
            self.session_factory,
            chunk_id=chunk_id,
            worker_id="w1",
            candidates=[],
            discovered_urls=[],
            chunk_status="too_many_candidates",
        )

        self.assertEqual(result["status"], CrawlPageChunkStatus.SPLIT_REQUIRED.value)
        self.assertGreaterEqual(result["child_count"], 1)
        async with self.session_factory() as session:
            parent = await session.get(CrawlPageChunk, chunk_id)
            children = list(
                await session.scalars(
                    select(CrawlPageChunk).where(CrawlPageChunk.parent_chunk_id == "c1").order_by(CrawlPageChunk.id)
                )
            )
        assert parent is not None
        self.assertEqual(parent.status, CrawlPageChunkStatus.SUPERSEDED.value)
        self.assertEqual(parent.split_reason, "too_many_candidates")
        self.assertGreaterEqual(len(children), 1)

    async def test_complete_chunk_ignores_legacy_split_required_status(self) -> None:
        _, chunk_id = await self._seed_processing_chunk()
        result = await complete_current_chunk(
            self.session_factory,
            chunk_id=chunk_id,
            worker_id="w1",
            candidates=[],
            discovered_urls=[],
            chunk_status="split_required",
        )

        self.assertEqual(result["status"], "saved")
        async with self.session_factory() as session:
            chunk = await session.get(CrawlPageChunk, chunk_id)
            children = list(await session.scalars(select(CrawlPageChunk).where(CrawlPageChunk.parent_chunk_id == "c1")))
        assert chunk is not None
        self.assertEqual(chunk.status, CrawlPageChunkStatus.COMPLETED.value)
        self.assertEqual(children, [])

    async def test_complete_chunk_exactly_ten_candidates_does_not_split(self) -> None:
        _, chunk_id = await self._seed_processing_chunk()
        candidates = [
            ProfessorCandidatePayload(name=f"教师{i}", profile_url=f"https://example.edu/t{i}.html", confidence=0.9)
            for i in range(10)
        ]

        result = await complete_current_chunk(
            self.session_factory,
            chunk_id=chunk_id,
            worker_id="w1",
            candidates=candidates,
            discovered_urls=[],
            chunk_status="completed",
        )

        self.assertEqual(result["status"], "saved")
        self.assertEqual(result["saved_count"], 10)
        async with self.session_factory() as session:
            chunk = await session.get(CrawlPageChunk, chunk_id)
            children = list(await session.scalars(select(CrawlPageChunk).where(CrawlPageChunk.parent_chunk_id == "c1")))
        assert chunk is not None
        self.assertEqual(chunk.status, CrawlPageChunkStatus.COMPLETED.value)
        self.assertEqual(children, [])
    async def test_chunk_worker_marks_retryable_when_payload_shape_is_invalid(self) -> None:
        job_id, chunk_id = await self._seed_processing_chunk(with_profile=True)

        with patch("app.services.crawler_v2_chunk_worker.invoke_v2_chunk_agent", new=AsyncMock(return_value=({"candidates": []}, None))):
            processed = await run_crawler_v2_chunk_worker_once(self.session_factory, chunk_id=chunk_id, worker_id="w1")

        self.assertEqual(processed, 1)
        async with self.session_factory() as session:
            chunk = await session.get(CrawlPageChunk, chunk_id)
            candidates = list(await session.scalars(select(CrawlCandidate).where(CrawlCandidate.job_id == job_id)))
        assert chunk is not None
        self.assertEqual(chunk.status, CrawlPageChunkStatus.FAILED_RETRYABLE.value)
        self.assertEqual(candidates, [])

    async def test_chunk_worker_marks_retryable_when_llm_output_is_invalid_json(self) -> None:
        job_id, chunk_id = await self._seed_processing_chunk(with_profile=True)

        with patch("app.services.crawler_v2_chunk_worker.invoke_v2_chunk_agent", new=AsyncMock(side_effect=ValueError("invalid json"))):
            processed = await run_crawler_v2_chunk_worker_once(self.session_factory, chunk_id=chunk_id, worker_id="w1")

        self.assertEqual(processed, 1)
        async with self.session_factory() as session:
            chunk = await session.get(CrawlPageChunk, chunk_id)
            candidates = list(await session.scalars(select(CrawlCandidate).where(CrawlCandidate.job_id == job_id)))
        assert chunk is not None
        self.assertEqual(chunk.status, CrawlPageChunkStatus.FAILED_RETRYABLE.value)
        self.assertIn("invalid json", chunk.last_error)
        self.assertEqual(candidates, [])

    async def test_chunk_worker_writes_v2_debug_jsonl(self) -> None:
        job_id, chunk_id = await self._seed_processing_chunk(with_profile=True)
        payload = {
            "candidates": [
                {"name": "张三", "email": "zhang@example.edu", "confidence": 0.9},
            ],
            "discovered_urls": [],
            "chunk_status": "completed",
        }
        usage = {"input_tokens": 20, "output_tokens": 30, "cached_tokens": 10, "total_tokens": 50}

        raw_model_text = "模型原始输出：{\"chunk_status\":\"completed\"}"
        with patch("app.services.crawler_v2_chunk_worker.invoke_v2_chunk_agent", new=AsyncMock(return_value=(payload, usage, raw_model_text))), patch("app.services.crawler_v2_chunk_worker.append_crawler_v2_debug_event") as debug_mock:
            processed = await run_crawler_v2_chunk_worker_once(self.session_factory, chunk_id=chunk_id, worker_id="w1")

        self.assertEqual(processed, 1)
        events = [call.kwargs["event_name"] for call in debug_mock.call_args_list]
        self.assertIn("llm_response", events)
        self.assertIn("chunk_completed", events)
        llm_call = next(call for call in debug_mock.call_args_list if call.kwargs["event_name"] == "llm_response")
        self.assertEqual(llm_call.args[0], job_id)
        self.assertEqual(llm_call.kwargs["worker_kind"], "chunk")
        self.assertEqual(llm_call.kwargs["work_item_id"], chunk_id)
        self.assertEqual(llm_call.kwargs["payload"]["raw_payload"], payload)
        self.assertEqual(llm_call.kwargs["payload"]["raw_model_text"], raw_model_text)
        self.assertEqual(llm_call.kwargs["payload"]["token_usage"], usage)

    async def test_complete_chunk_splits_when_candidate_count_exceeds_limit(self) -> None:
        job_id, chunk_id = await self._seed_processing_chunk()
        async with self.session_factory() as session:
            chunk = await session.get(CrawlPageChunk, chunk_id)
            assert chunk is not None
            chunk.content = "\n".join(f"教师{i} [详情](https://example.edu/t{i}.html) 研究方向 软件工程 人工智能 数据挖掘 机器学习 教学科研项目 招生信息 联系方式 学术成果" for i in range(80))
            await session.commit()
        candidates = [
            ProfessorCandidatePayload(name=f"教师{i}", profile_url=f"https://example.edu/t{i}.html", confidence=0.9)
            for i in range(11)
        ]

        result = await complete_current_chunk(
            self.session_factory,
            chunk_id=chunk_id,
            worker_id="w1",
            candidates=candidates,
            discovered_urls=[],
            chunk_status="completed",
        )

        self.assertEqual(result["status"], "split_required")
        self.assertEqual(result["saved_count"], 0)
        async with self.session_factory() as session:
            saved = list(await session.scalars(select(CrawlCandidate).where(CrawlCandidate.job_id == job_id)))
            chunks = list(await session.scalars(select(CrawlPageChunk).where(CrawlPageChunk.job_id == job_id).order_by(CrawlPageChunk.id)))
        self.assertEqual(saved, [])
        self.assertEqual(chunks[0].status, CrawlPageChunkStatus.SUPERSEDED.value)
        self.assertGreaterEqual(len(chunks), 2)
    async def test_complete_chunk_does_not_enqueue_candidate_profile_url(self) -> None:
        job_id, chunk_id = await self._seed_processing_chunk()

        result = await complete_current_chunk(
            self.session_factory,
            chunk_id=chunk_id,
            worker_id="w1",
            candidates=[ProfessorCandidatePayload(name="张三", profile_url="https://example.edu/zhang.html", confidence=0.9)],
            discovered_urls=["https://example.edu/zhang.html"],
            chunk_status="completed",
        )

        self.assertEqual(result["saved_count"], 1)
        self.assertEqual(result["url_count"], 0)
        async with self.session_factory() as session:
            tasks = list(await session.scalars(select(CrawlPageTask).where(CrawlPageTask.job_id == job_id)))
        self.assertEqual(tasks, [])
    async def test_complete_chunk_fills_profile_url_from_markdown_link(self) -> None:
        job_id, chunk_id = await self._seed_processing_chunk()
        async with self.session_factory() as session:
            chunk = await session.get(CrawlPageChunk, chunk_id)
            assert chunk is not None
            chunk.content = "[张三](https://example.edu/zhang.html) 教授，研究方向：软件工程"
            await session.commit()

        result = await complete_current_chunk(
            self.session_factory,
            chunk_id=chunk_id,
            worker_id="w1",
            candidates=[ProfessorCandidatePayload(name="张三", confidence=0.9)],
            discovered_urls=[],
            chunk_status="completed",
        )

        self.assertEqual(result["saved_count"], 1)
        self.assertEqual(result["rejected_count"], 0)
        async with self.session_factory() as session:
            row = await session.scalar(select(CrawlCandidate).where(CrawlCandidate.job_id == job_id))
        assert row is not None
        self.assertEqual(row.profile_url, "https://example.edu/zhang.html")
    async def test_complete_chunk_rejects_candidate_without_email_and_profile_url(self) -> None:
        job_id, chunk_id = await self._seed_processing_chunk()

        result = await complete_current_chunk(
            self.session_factory,
            chunk_id=chunk_id,
            worker_id="w1",
            candidates=[
                ProfessorCandidatePayload(name="张三", confidence=0.8),
                ProfessorCandidatePayload(name="李四", profile_url="https://example.edu/li.html", confidence=0.9),
            ],
            discovered_urls=[],
            chunk_status="completed",
        )

        self.assertEqual(result["saved_count"], 1)
        self.assertEqual(result["rejected_count"], 1)
        async with self.session_factory() as session:
            rows = list(await session.scalars(select(CrawlCandidate).where(CrawlCandidate.job_id == job_id)))
        self.assertEqual([row.name for row in rows], ["李四"])
    async def test_complete_chunk_saves_candidates_and_urls_without_auto_enrichment_tasks(self) -> None:
        job_id, chunk_id = await self._seed_processing_chunk()
        candidate = ProfessorCandidatePayload(name="张三", profile_url="https://example.edu/zhang.html", source_url="https://example.edu/faculty", confidence=0.9)

        result = await complete_current_chunk(
            self.session_factory,
            chunk_id=chunk_id,
            worker_id="w1",
            candidates=[candidate],
            discovered_urls=["https://example.edu/faculty/list2.html", "https://other.edu/nope"],
            chunk_status="completed",
        )

        self.assertEqual(result["saved_count"], 1)
        async with self.session_factory() as session:
            chunk = await session.get(CrawlPageChunk, chunk_id)
            candidates = list(await session.scalars(select(CrawlCandidate).where(CrawlCandidate.job_id == job_id)))
            page_tasks = list(await session.scalars(select(CrawlPageTask).where(CrawlPageTask.job_id == job_id).order_by(CrawlPageTask.id)))
            enrichment_tasks = list(await session.scalars(select(CrawlCandidateEnrichmentTask).where(CrawlCandidateEnrichmentTask.job_id == job_id)))
        assert chunk is not None
        self.assertEqual(chunk.status, CrawlPageChunkStatus.COMPLETED.value)
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].name, "张三")
        self.assertEqual([task.normalized_url for task in page_tasks], ["https://example.edu/faculty/list2.html"])
        self.assertEqual(enrichment_tasks, [])


    async def test_chunk_worker_does_not_save_after_job_is_paused(self) -> None:
        job_id, chunk_id = await self._seed_processing_chunk(with_profile=True)

        async def pause_job_during_llm(*_args, **_kwargs):
            async with self.session_factory() as session:
                job = await session.get(CrawlJob, job_id)
                chunk = await session.get(CrawlPageChunk, chunk_id)
                assert job is not None and chunk is not None
                job.status = CrawlJobStatus.PAUSED.value
                chunk.status = CrawlPageChunkStatus.PENDING.value
                chunk.worker_id = None
                await session.commit()
            return ({
                "candidates": [{"name": "张三", "profile_url": "https://example.edu/zhang.html"}],
                "discovered_urls": ["https://example.edu/faculty/list2.html"],
                "chunk_status": "completed",
            }, {"input_tokens": 10, "output_tokens": 5, "cached_tokens": 0})

        with patch("app.services.crawler_v2_chunk_worker.invoke_v2_chunk_agent", new=AsyncMock(side_effect=pause_job_during_llm)):
            processed = await run_crawler_v2_chunk_worker_once(self.session_factory, chunk_id=chunk_id, worker_id="w1")

        self.assertEqual(processed, 0)
        async with self.session_factory() as session:
            candidates = list(await session.scalars(select(CrawlCandidate).where(CrawlCandidate.job_id == job_id)))
            token_usage = list(await session.scalars(select(CrawlWorkerTokenUsage).where(CrawlWorkerTokenUsage.job_id == job_id)))
            chunk = await session.get(CrawlPageChunk, chunk_id)
        assert chunk is not None
        self.assertEqual(chunk.status, CrawlPageChunkStatus.PENDING.value)
        self.assertEqual(candidates, [])
        self.assertEqual(token_usage, [])

    async def test_chunk_worker_without_llm_profile_marks_retryable(self) -> None:
        _, chunk_id = await self._seed_processing_chunk()

        processed = await run_crawler_v2_chunk_worker_once(
            self.session_factory,
            chunk_id=chunk_id,
            worker_id="w1",
        )

        self.assertEqual(processed, 1)
        async with self.session_factory() as session:
            chunk = await session.get(CrawlPageChunk, chunk_id)
        assert chunk is not None
        self.assertEqual(chunk.status, CrawlPageChunkStatus.FAILED_RETRYABLE.value)
        self.assertIsNone(chunk.worker_id)
        self.assertIn("LLM Profile", chunk.last_error or "")


    async def test_chunk_worker_uses_single_tool_payload_instead_of_legacy_agent(self) -> None:
        _, chunk_id = await self._seed_processing_chunk(with_profile=True)
        payload = {
            "candidates": [
                {
                    "name": "张三",
                    "profile_url": "https://example.edu/zhang.html",
                    "source_url": "https://example.edu/faculty",
                    "confidence": 0.9,
                }
            ],
            "discovered_urls": ["https://example.edu/faculty/list2.html"],
            "chunk_status": "completed",
        }

        with patch("app.services.crawler_v2_chunk_worker.invoke_v2_chunk_agent", new=AsyncMock(return_value=payload)) as invoke_mock:
            processed = await run_crawler_v2_chunk_worker_once(
                self.session_factory,
                chunk_id=chunk_id,
                worker_id="w1",
            )

        self.assertEqual(processed, 1)
        import app.services.crawler_v2_chunk_worker as module
        self.assertFalse(hasattr(module, "run_faculty_crawler_agent"))
        invoke_mock.assert_awaited_once()
        async with self.session_factory() as session:
            chunk = await session.get(CrawlPageChunk, chunk_id)
            candidates = list(await session.scalars(select(CrawlCandidate)))
            tasks = list(await session.scalars(select(CrawlPageTask).order_by(CrawlPageTask.id)))
        assert chunk is not None
        self.assertEqual(chunk.status, CrawlPageChunkStatus.COMPLETED.value)
        self.assertEqual([candidate.name for candidate in candidates], ["张三"])
        self.assertEqual([task.normalized_url for task in tasks], ["https://example.edu/faculty/list2.html"])


    async def test_complete_chunk_enqueues_worker_discovered_safe_same_domain_urls(self) -> None:
        job_id, chunk_id = await self._seed_processing_chunk()

        result = await complete_current_chunk(
            self.session_factory,
            chunk_id=chunk_id,
            worker_id="w1",
            candidates=[ProfessorCandidatePayload(name="张三", profile_url="https://example.edu/people/li.html", confidence=0.9)],
            discovered_urls=[
                "https://example.edu/people/li.html",
                "https://example.edu/about.html",
                "https://example.edu/news/2024.html",
                "https://example.edu/faculty/list2.html",
                "https://example.edu/teachers?page=2",
                "https://example.edu/faculty/index1.htm",
            ],
            chunk_status="completed",
        )

        self.assertEqual(result["saved_count"], 1)
        self.assertEqual(result["url_count"], 5)
        async with self.session_factory() as session:
            tasks = list(await session.scalars(select(CrawlPageTask).where(CrawlPageTask.job_id == job_id).order_by(CrawlPageTask.id)))
        self.assertEqual(
            [task.normalized_url for task in tasks],
            [
                "https://example.edu/about.html",
                "https://example.edu/news/2024.html",
                "https://example.edu/faculty/list2.html",
                "https://example.edu/teachers?page=2",
                "https://example.edu/faculty/index1.htm",
            ],
        )

    async def test_complete_chunk_idempotently_ignores_url_already_found_by_page_worker(self) -> None:
        job_id, chunk_id = await self._seed_processing_chunk()
        async with self.session_factory() as session:
            session.add(
                CrawlPageTask(
                    job_id=job_id,
                    normalized_url="https://example.edu/faculty/list2.html",
                    original_url="https://example.edu/faculty/list2.html",
                )
            )
            await session.commit()

        result = await complete_current_chunk(
            self.session_factory,
            chunk_id=chunk_id,
            worker_id="w1",
            candidates=[ProfessorCandidatePayload(name="张三", email="zhang@example.edu", confidence=0.9)],
            discovered_urls=["https://example.edu/faculty/list2.html", "https://example.edu/faculty/list2.html#section"],
            chunk_status="completed",
        )

        self.assertEqual(result["saved_count"], 1)
        self.assertEqual(result["url_count"], 0)
        async with self.session_factory() as session:
            tasks = list(await session.scalars(select(CrawlPageTask).where(CrawlPageTask.job_id == job_id)))
            candidates = list(await session.scalars(select(CrawlCandidate).where(CrawlCandidate.job_id == job_id)))
        self.assertEqual(len(tasks), 1)
        self.assertEqual(len(candidates), 1)

    async def test_complete_chunk_keeps_candidate_save_when_url_insert_hits_unique_conflict(self) -> None:
        job_id, chunk_id = await self._seed_processing_chunk()
        async def flush_with_url_conflict(self_session, *args, **kwargs):
            if any(isinstance(item, CrawlPageTask) for item in self_session.new):
                raise IntegrityError("insert", {}, Exception("unique conflict"))
            return await original_flush(self_session, *args, **kwargs)


        async with self.session_factory() as probe_session:
            original_flush = type(probe_session).flush

        with patch("sqlalchemy.ext.asyncio.AsyncSession.flush", flush_with_url_conflict):
            result = await complete_current_chunk(
                self.session_factory,
                chunk_id=chunk_id,
                worker_id="w1",
                candidates=[ProfessorCandidatePayload(name="张三", email="zhang@example.edu", confidence=0.9)],
                discovered_urls=["https://example.edu/race.html"],
                chunk_status="completed",
            )

        self.assertEqual(result["saved_count"], 1)
        self.assertEqual(result["url_count"], 0)
        async with self.session_factory() as session:
            chunk = await session.get(CrawlPageChunk, chunk_id)
            candidates = list(await session.scalars(select(CrawlCandidate).where(CrawlCandidate.job_id == job_id)))
            tasks = list(await session.scalars(select(CrawlPageTask).where(CrawlPageTask.job_id == job_id)))
        assert chunk is not None
        self.assertEqual(chunk.status, CrawlPageChunkStatus.COMPLETED.value)
        self.assertEqual(len(candidates), 1)
        self.assertEqual(len(tasks), 0)

    async def test_complete_chunk_rejects_expired_lease_without_writing(self) -> None:
        job_id, chunk_id = await self._seed_processing_chunk()
        expired = datetime.now(UTC) - timedelta(seconds=1)
        async with self.session_factory() as session:
            chunk = await session.get(CrawlPageChunk, chunk_id)
            assert chunk is not None
            chunk.lease_expires_at = expired
            await session.commit()
        candidate = ProfessorCandidatePayload(name="张三", profile_url="https://example.edu/zhang.html", source_url="https://example.edu/faculty", confidence=0.9)

        result = await complete_current_chunk(
            self.session_factory,
            chunk_id=chunk_id,
            worker_id="w1",
            candidates=[candidate],
            discovered_urls=["https://example.edu/faculty/list2.html"],
            chunk_status="completed",
        )

        self.assertEqual(result["status"], "lease_expired")
        async with self.session_factory() as session:
            chunk = await session.get(CrawlPageChunk, chunk_id)
            candidates = list(await session.scalars(select(CrawlCandidate).where(CrawlCandidate.job_id == job_id)))
            page_tasks = list(await session.scalars(select(CrawlPageTask).where(CrawlPageTask.job_id == job_id)))
        assert chunk is not None
        self.assertEqual(chunk.status, CrawlPageChunkStatus.PROCESSING.value)
        self.assertEqual(len(candidates), 0)
        self.assertEqual(len(page_tasks), 0)
    async def test_invoke_chunk_agent_passes_thinking_extra_body_to_model(self) -> None:
        class FakeResponse:
            content = '{"candidates": [], "discovered_urls": [], "chunk_status": "no_candidates"}'
            usage_metadata = {"input_tokens": 1, "output_tokens": 1, "cached_tokens": 0, "total_tokens": 2}

        fake_model = AsyncMock()
        fake_model.ainvoke = AsyncMock(return_value=FakeResponse())
        extra_body = {"enable_thinking": False}
        llm_profile = object()

        with patch("app.services.crawler_v2_chunk_worker.build_faculty_crawler_model", return_value=fake_model) as build_mock:
            payload, usage, raw_model_text = await invoke_v2_chunk_agent(
                llm_profile,
                university="示例大学",
                school="计算机学院",
                source_url="https://example.edu/faculty",
                chunk_content="张三",
                thinking_extra_body=extra_body,
            )

        build_mock.assert_called_once_with(llm_profile, extra_body=extra_body)
        self.assertEqual(payload["chunk_status"], "no_candidates")
        self.assertEqual(usage["input_tokens"], 1)
        self.assertIn("no_candidates", raw_model_text)

    async def test_chunk_worker_uses_thinking_adaptation_extra_body(self) -> None:
        _, chunk_id = await self._seed_processing_chunk(with_profile=True)
        payload = {"candidates": [], "discovered_urls": [], "chunk_status": "no_candidates"}
        usage = {"input_tokens": 10, "output_tokens": 2, "cached_tokens": 0}
        extra_body = {"enable_thinking": False}

        with patch("app.services.crawler_v2_chunk_worker.ensure_thinking_adaptation", new=AsyncMock(return_value=extra_body), create=True) as adapt_mock, patch("app.services.crawler_v2_chunk_worker.invoke_v2_chunk_agent", new=AsyncMock(return_value=(payload, usage, '{"chunk_status":"no_candidates","candidates":[],"discovered_urls":[]}'))) as invoke_mock:
            processed = await run_crawler_v2_chunk_worker_once(
                self.session_factory,
                chunk_id=chunk_id,
                worker_id="w1",
            )

        self.assertEqual(processed, 1)
        adapt_mock.assert_awaited_once()
        invoke_mock.assert_awaited_once()
        self.assertEqual(invoke_mock.await_args.kwargs["thinking_extra_body"], extra_body)

    async def test_chunk_worker_records_llm_token_usage(self) -> None:
        _, chunk_id = await self._seed_processing_chunk(with_profile=True)

        class FakeResponse:
            content = '{"candidates": [], "discovered_urls": [], "chunk_status": "no_candidates"}'
            usage_metadata = {"input_tokens": 100, "output_tokens": 20, "cached_tokens": 80, "total_tokens": 120}

        fake_model = AsyncMock()
        fake_model.ainvoke = AsyncMock(return_value=FakeResponse())

        with patch("app.services.crawler_v2_chunk_worker.build_faculty_crawler_model", return_value=fake_model):
            processed = await run_crawler_v2_chunk_worker_once(
                self.session_factory,
                chunk_id=chunk_id,
                worker_id="w1",
            )

        self.assertEqual(processed, 1)
        async with self.session_factory() as session:
            usage = await session.scalar(select(CrawlWorkerTokenUsage))
        assert usage is not None
        self.assertEqual(usage.worker_kind, "chunk")
        self.assertEqual(usage.work_item_id, str(chunk_id))
        self.assertEqual(usage.model_name, "deepseek")
        self.assertEqual(usage.input_tokens, 100)
        self.assertEqual(usage.output_tokens, 20)
        self.assertEqual(usage.cached_tokens, 80)
    async def _seed_processing_chunk(self, *, with_profile: bool = False) -> tuple[int, int]:
        async with self.session_factory() as session:
            llm_profile_id = None
            if with_profile:
                profile = LLMProfile(name="默认", provider="openai", api_base_url="https://api.example.com/v1", api_key="sk-test", model_name="deepseek", is_default=True)
                session.add(profile)
                await session.flush()
                llm_profile_id = profile.id
            job = CrawlJob(university="示例大学", school="计算机学院", start_url="https://example.edu/faculty", status=CrawlJobStatus.RUNNING.value, runtime_version="v2", llm_profile_id=llm_profile_id)
            session.add(job)
            await session.flush()
            chunk = CrawlPageChunk(job_id=job.id, page_id=None, source_url="https://example.edu/faculty", page_fingerprint="p", chunk_id="c1", chunk_index=0, chunk_hash="h", content="张三", status=CrawlPageChunkStatus.PROCESSING.value, worker_id="w1")
            session.add(chunk)
            await session.commit()
            return job.id, chunk.id


if __name__ == "__main__":
    unittest.main()
