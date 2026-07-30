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
from app.services.crawler_v2_chunk_worker import (
    _derive_chunk_status,
    _validate_chunk_agent_payload,
    complete_current_chunk,
    invoke_v2_chunk_agent,
    run_crawler_v2_chunk_worker_once,
)
from app.services.crawler_tools import ProfessorCandidatePayload
from app.services.crawler_structured_output import V2ChunkWirePayload
from app.services.llm_runtime import (
    ChatCompletionResult,
    ChatCompletionUsage,
    LLMRuntimeAdaptation,
    LLMRuntimeError,
)


class CrawlerV2ChunkWorkerTests(unittest.IsolatedAsyncioTestCase):
    def test_chunk_prompt_includes_v1_quality_constraints(self) -> None:
        from app.services.crawler_v2_chunk_worker import build_v2_chunk_prompt

        prompt = build_v2_chunk_prompt(
            university="示例大学",
            school="计算机学院",
            source_url="https://example.edu/faculty",
            chunk_content="[张三](https://example.edu/zhang.html) 教授",
        )

        self.assertIn("1 到 10 时 candidates 数组长度必须与 candidate_count 相等", prompt)
        self.assertIn("缺少 email 且缺少 profile_url", prompt)
        self.assertIn("Markdown", prompt)
        self.assertIn("导师个人主页", prompt)
        self.assertIn("不发现、不选择", prompt)
        self.assertIn("只输出一个 JSON 对象", prompt)
        self.assertIn("输出示例", prompt)
        self.assertNotIn('"chunk_status"', prompt)
        self.assertIn('"candidate_count": 0', prompt)
        self.assertIn('"candidate_count": 1', prompt)
        self.assertIn('"candidate_count": 11', prompt)
        self.assertIn("candidate_count", prompt)
        self.assertIn("candidate_count 必须是非负整数", prompt)
        self.assertIn("汇总数字一律不参与计数", prompt)
        self.assertIn("指出第 11 个不同人员", prompt)
        self.assertIn("禁止浮点数、字符串和布尔值", prompt)
        self.assertIn('"candidates": []', prompt)
        self.assertNotIn('"discovered_urls"', prompt)
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
        self.assertIn("candidate_count 必须为 11 或更大", prompt)
        self.assertIn("no_candidates 只允许", prompt)
        self.assertLess(prompt.index("输出示例（当前 chunk 明确超过 10 个候选）"), prompt.index("输出示例（无候选）"))

    def test_payload_validation_requires_candidate_count_contract(self) -> None:
        payload = {
            "candidate_count": 1,
            "candidates": [{"name": "张三"}],
        }

        self.assertEqual(_validate_chunk_agent_payload(payload), payload)

    def test_payload_validation_rejects_invalid_candidate_counts_and_shapes(self) -> None:
        invalid_payloads = [
            {},
            {"candidate_count": -1, "candidates": []},
            {"candidate_count": True, "candidates": []},
            {"candidate_count": 1.0, "candidates": []},
            {"candidate_count": "1", "candidates": []},
            {"candidate_count": 0, "candidates": {}},
            {"candidate_count": 0, "candidates": [{}]},
            {"candidate_count": 1, "candidates": []},
        ]

        for payload in invalid_payloads:
            with self.subTest(payload=payload):
                with self.assertRaises(ValueError):
                    _validate_chunk_agent_payload(payload)

        with self.assertRaisesRegex(ValueError, "candidate_count 与 candidates 数量不一致"):
            _validate_chunk_agent_payload({"candidate_count": 1, "candidates": []})

    def test_payload_validation_allows_non_empty_candidates_when_count_exceeds_limit(self) -> None:
        payload = {"candidate_count": 11, "candidates": [{"invalid": True}]}

        self.assertEqual(_validate_chunk_agent_payload(payload), payload)

    def test_derive_chunk_status_uses_candidate_count(self) -> None:
        self.assertEqual(_derive_chunk_status(0), CrawlPageChunkStatus.NO_CANDIDATES.value)
        self.assertEqual(_derive_chunk_status(10), CrawlPageChunkStatus.COMPLETED.value)
        self.assertEqual(_derive_chunk_status(11), CrawlPageChunkStatus.SPLIT_REQUIRED.value)

    def test_worker_module_does_not_expose_unused_typed_agent_payload(self) -> None:
        import app.services.crawler_v2_chunk_worker as module

        self.assertFalse(hasattr(module, "V2ChunkAgentPayload"))

    async def test_complete_chunk_marks_no_candidates_from_zero_candidate_count(self) -> None:
        _, chunk_id = await self._seed_processing_chunk()

        result = await complete_current_chunk(
            self.session_factory,
            chunk_id=chunk_id,
            worker_id="w1",
            candidates=[],
            discovered_urls=[],
            candidate_count=0,
        )

        self.assertEqual(result["status"], "saved")
        self.assertEqual(result["derived_chunk_status"], CrawlPageChunkStatus.NO_CANDIDATES.value)
        async with self.session_factory() as session:
            chunk = await session.get(CrawlPageChunk, chunk_id)
        assert chunk is not None
        self.assertEqual(chunk.status, CrawlPageChunkStatus.NO_CANDIDATES.value)

    async def test_worker_splits_count_over_limit_before_candidate_schema_validation(self) -> None:
        job_id, chunk_id = await self._seed_processing_chunk(with_profile=True)
        async with self.session_factory() as session:
            chunk = await session.get(CrawlPageChunk, chunk_id)
            assert chunk is not None
            chunk.content = "\n".join(f"教师{i} 研究方向 软件工程 人工智能 数据挖掘" for i in range(80))
            await session.commit()
        payload = {
            "candidate_count": 11,
            "candidates": [{"not": "a valid candidate"}],
            "discovered_urls": ["https://example.edu/faculty/list2.html"],
        }

        with patch("app.services.crawler_v2_chunk_worker.invoke_v2_chunk_agent", new=AsyncMock(return_value=payload)), patch("app.services.crawler_v2_chunk_worker.append_crawler_v2_debug_event") as debug_mock:
            processed = await run_crawler_v2_chunk_worker_once(self.session_factory, chunk_id=chunk_id, worker_id="w1")

        self.assertEqual(processed, 1)
        completed_call = next(call for call in debug_mock.call_args_list if call.kwargs["event_name"] == "chunk_completed")
        save_result = completed_call.kwargs["payload"]["save_result"]
        self.assertEqual(save_result["contract_warning"], "candidate_count_candidates_conflict")
        self.assertEqual(save_result["candidate_count"], 11)
        self.assertEqual(save_result["candidate_payload_count"], 1)
        async with self.session_factory() as session:
            candidates = list(await session.scalars(select(CrawlCandidate).where(CrawlCandidate.job_id == job_id)))
            tasks = list(await session.scalars(select(CrawlPageTask).where(CrawlPageTask.job_id == job_id)))
        self.assertEqual(candidates, [])
        self.assertEqual(tasks, [])

    async def test_worker_splits_structured_result_over_limit(self) -> None:
        job_id, chunk_id = await self._seed_processing_chunk(with_profile=True)
        async with self.session_factory() as session:
            chunk = await session.get(CrawlPageChunk, chunk_id)
            assert chunk is not None
            chunk.content = "\n".join(f"教师{i} 研究方向 软件工程 人工智能 数据挖掘" for i in range(80))
            await session.commit()

        wire_payload = V2ChunkWirePayload(
            candidate_count=11,
            candidates=[],
        )
        with patch(
            "app.services.crawler_v2_chunk_worker.request_crawler_structured_completion",
            new=AsyncMock(
                return_value=(
                    ChatCompletionResult(content='{"candidate_count":11}'),
                    wire_payload,
                    "json_schema_strict",
                )
            ),
        ), patch("app.services.crawler_v2_chunk_worker.append_crawler_v2_debug_event") as debug_mock:
            processed = await run_crawler_v2_chunk_worker_once(self.session_factory, chunk_id=chunk_id, worker_id="w1")

        self.assertEqual(processed, 1)
        async with self.session_factory() as session:
            parent = await session.get(CrawlPageChunk, chunk_id)
            candidates = list(await session.scalars(select(CrawlCandidate).where(CrawlCandidate.job_id == job_id)))
            tasks = list(await session.scalars(select(CrawlPageTask).where(CrawlPageTask.job_id == job_id)))
        assert parent is not None
        self.assertEqual(parent.status, CrawlPageChunkStatus.SUPERSEDED.value)
        self.assertEqual(parent.split_reason, "candidate_count_exceeded")
        self.assertEqual(candidates, [])
        self.assertEqual(tasks, [])
        completed_call = next(call for call in debug_mock.call_args_list if call.kwargs["event_name"] == "chunk_completed")
        save_result = completed_call.kwargs["payload"]["save_result"]
        self.assertEqual(save_result["candidate_count"], 11)
        self.assertEqual(save_result["candidate_payload_count"], 0)

    async def test_worker_retries_when_structured_result_rejects_non_integer_count(self) -> None:
        _, first_chunk_id = await self._seed_processing_chunk(with_profile=True)
        for index, raw_count in enumerate(("true", "1.0", '"1"')):
            with self.subTest(raw_count=raw_count):
                if index == 0:
                    chunk_id = first_chunk_id
                else:
                    _, chunk_id = await self._seed_processing_chunk()

                with patch(
                    "app.services.crawler_v2_chunk_worker.request_crawler_structured_completion",
                    new=AsyncMock(
                        side_effect=LLMRuntimeError(
                            f"模型返回的 JSON 结构无效: candidate_count={raw_count}"
                        )
                    ),
                ):
                    processed = await run_crawler_v2_chunk_worker_once(self.session_factory, chunk_id=chunk_id, worker_id="w1")

                self.assertEqual(processed, 1)
                async with self.session_factory() as session:
                    chunk = await session.get(CrawlPageChunk, chunk_id)
                assert chunk is not None
                self.assertEqual(chunk.status, CrawlPageChunkStatus.FAILED_RETRYABLE.value)

    def test_wire_ignores_legacy_chunk_status_only_outside_strict_mode(self) -> None:
        raw_payload = {
            "candidate_count": 0,
            "candidates": [],
            "discovered_urls": [],
            "chunk_status": "too_many_candidates",
        }

        fallback = V2ChunkWirePayload.model_validate(
            raw_payload,
            context={"structured_output_mode": "json_object"},
        )

        self.assertEqual(fallback.candidate_count, 0)
        with self.assertRaises(ValueError):
            V2ChunkWirePayload.model_validate(
                raw_payload,
                context={"structured_output_mode": "json_schema_strict"},
            )

    async def test_worker_marks_retryable_when_candidate_count_mismatches_payload(self) -> None:
        job_id, chunk_id = await self._seed_processing_chunk(with_profile=True)
        payload = {
            "candidate_count": 1,
            "candidates": [],
            "discovered_urls": [],
        }

        with patch("app.services.crawler_v2_chunk_worker.invoke_v2_chunk_agent", new=AsyncMock(return_value=payload)):
            processed = await run_crawler_v2_chunk_worker_once(self.session_factory, chunk_id=chunk_id, worker_id="w1")

        self.assertEqual(processed, 1)
        async with self.session_factory() as session:
            chunk = await session.get(CrawlPageChunk, chunk_id)
            candidates = list(await session.scalars(select(CrawlCandidate).where(CrawlCandidate.job_id == job_id)))
        assert chunk is not None
        self.assertEqual(chunk.status, CrawlPageChunkStatus.FAILED_RETRYABLE.value)
        self.assertIn("candidate_count 与 candidates 数量不一致", chunk.last_error or "")
        self.assertEqual(candidates, [])

    async def asyncSetUp(self) -> None:
        fd, self.db_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        create_schema_sqlite_database(Path(self.db_path))
        self.engine = create_async_engine(f"sqlite+aiosqlite:///{Path(self.db_path).as_posix()}")
        self.session_factory = async_sessionmaker(self.engine, expire_on_commit=False)
        self._runtime_adaptation_patch = patch(
            "app.services.crawler_v2_chunk_worker.ensure_llm_runtime_adaptation",
            new=AsyncMock(return_value=LLMRuntimeAdaptation("chat_completions", None)),
        )
        self._runtime_adaptation_patch.start()

    async def asyncTearDown(self) -> None:
        self._runtime_adaptation_patch.stop()
        await self.engine.dispose()
        try:
            os.unlink(self.db_path)
        except FileNotFoundError:
            pass

    async def test_complete_chunk_marks_terminal_at_minimum_split_tokens(self) -> None:
        _, chunk_id = await self._seed_processing_chunk()
        async with self.session_factory() as session:
            chunk = await session.get(CrawlPageChunk, chunk_id)
            assert chunk is not None
            chunk.content = "\n".join(["甲" * 25] * 4)
            await session.commit()

        result = await complete_current_chunk(
            self.session_factory,
            chunk_id=chunk_id,
            worker_id="w1",
            candidates=[],
            discovered_urls=[],
            candidate_count=11,
        )

        self.assertEqual(result["status"], CrawlPageChunkStatus.FAILED_TERMINAL.value)
        async with self.session_factory() as session:
            chunk = await session.get(CrawlPageChunk, chunk_id)
        assert chunk is not None
        self.assertEqual(chunk.status, CrawlPageChunkStatus.FAILED_TERMINAL.value)
        self.assertEqual(
            chunk.last_error,
            "chunk_split_min_tokens_reached token_estimate=100 min_split_tokens=100 reason=candidate_count_exceeded",
        )

    async def test_complete_chunk_marks_terminal_at_maximum_split_depth(self) -> None:
        _, chunk_id = await self._seed_processing_chunk()
        async with self.session_factory() as session:
            chunk = await session.get(CrawlPageChunk, chunk_id)
            assert chunk is not None
            chunk.content = "\n".join(["甲" * 25] * 5)
            chunk.split_depth = 7
            await session.commit()

        result = await complete_current_chunk(
            self.session_factory,
            chunk_id=chunk_id,
            worker_id="w1",
            candidates=[],
            discovered_urls=[],
            candidate_count=11,
        )

        self.assertEqual(result["status"], CrawlPageChunkStatus.FAILED_TERMINAL.value)
        async with self.session_factory() as session:
            chunk = await session.get(CrawlPageChunk, chunk_id)
        assert chunk is not None
        self.assertEqual(chunk.status, CrawlPageChunkStatus.FAILED_TERMINAL.value)
        self.assertEqual(
            chunk.last_error,
            "chunk_split_max_depth_exceeded split_depth=7 max_split_depth=7 reason=candidate_count_exceeded",
        )

    async def test_complete_chunk_marks_terminal_when_normalization_removes_children(self) -> None:
        _, chunk_id = await self._seed_processing_chunk()
        async with self.session_factory() as session:
            chunk = await session.get(CrawlPageChunk, chunk_id)
            assert chunk is not None
            chunk.content = " " * 500
            await session.commit()

        result = await complete_current_chunk(
            self.session_factory,
            chunk_id=chunk_id,
            worker_id="w1",
            candidates=[],
            discovered_urls=[],
            candidate_count=11,
        )

        self.assertEqual(result["status"], CrawlPageChunkStatus.FAILED_TERMINAL.value)
        async with self.session_factory() as session:
            chunk = await session.get(CrawlPageChunk, chunk_id)
        assert chunk is not None
        self.assertEqual(chunk.status, CrawlPageChunkStatus.FAILED_TERMINAL.value)
        self.assertEqual(
            chunk.last_error,
            "chunk_split_no_valid_children token_estimate=125 split_depth=0 reason=candidate_count_exceeded",
        )
    async def test_complete_chunk_candidate_count_exceeds_limit_triggers_backend_split(self) -> None:
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
            candidate_count=11,
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
        self.assertEqual(parent.split_reason, "candidate_count_exceeded")
        self.assertGreaterEqual(len(children), 1)

    async def test_chunk_worker_ignores_legacy_chunk_status_when_candidate_count_is_within_limit(self) -> None:
        job_id, chunk_id = await self._seed_processing_chunk(with_profile=True)
        payload = {
            "candidate_count": 1,
            "candidates": [{"name": "张三", "email": "zhang@example.edu", "confidence": 0.9}],
            "discovered_urls": [],
            "chunk_status": "too_many_candidates",
        }

        with patch("app.services.crawler_v2_chunk_worker.invoke_v2_chunk_agent", new=AsyncMock(return_value=payload)):
            processed = await run_crawler_v2_chunk_worker_once(self.session_factory, chunk_id=chunk_id, worker_id="w1")

        self.assertEqual(processed, 1)
        async with self.session_factory() as session:
            chunk = await session.get(CrawlPageChunk, chunk_id)
            candidates = list(await session.scalars(select(CrawlCandidate).where(CrawlCandidate.job_id == job_id)))
        assert chunk is not None
        self.assertEqual(chunk.status, CrawlPageChunkStatus.COMPLETED.value)
        self.assertEqual([candidate.name for candidate in candidates], ["张三"])

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
            candidate_count=10,
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

    async def test_chunk_worker_failure_sets_retry_backoff(self) -> None:
        _, chunk_id = await self._seed_processing_chunk(with_profile=True)

        with patch("app.services.crawler_v2_chunk_worker.invoke_v2_chunk_agent", new=AsyncMock(side_effect=ValueError("429 Too Many Requests"))):
            processed = await run_crawler_v2_chunk_worker_once(self.session_factory, chunk_id=chunk_id, worker_id="w1")

        self.assertEqual(processed, 1)
        async with self.session_factory() as session:
            chunk = await session.get(CrawlPageChunk, chunk_id)
        assert chunk is not None
        self.assertEqual(chunk.status, CrawlPageChunkStatus.FAILED_RETRYABLE.value)
        self.assertIn("429", chunk.last_error or "")
        self.assertIsNone(chunk.worker_id)
        self.assertIsNone(chunk.claimed_at)
        self.assertIsNotNone(chunk.lease_expires_at)
    async def test_chunk_worker_writes_v2_debug_jsonl(self) -> None:
        job_id, chunk_id = await self._seed_processing_chunk(with_profile=True)
        payload = {
            "candidate_count": 1,
            "candidates": [
                {"name": "张三", "email": "zhang@example.edu", "confidence": 0.9},
            ],
            "discovered_urls": [],
        }
        usage = {"input_tokens": 20, "output_tokens": 30, "cached_tokens": 10, "total_tokens": 50}

        raw_model_text = "模型原始输出：{\"candidate_count\":1}"
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
            candidate_count=11,
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
            discovered_urls=[],
            candidate_count=1,
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
            candidate_count=1,
        )

        self.assertEqual(result["saved_count"], 1)
        self.assertEqual(result["rejected_count"], 0)
        async with self.session_factory() as session:
            row = await session.scalar(select(CrawlCandidate).where(CrawlCandidate.job_id == job_id))
        assert row is not None
        self.assertEqual(row.profile_url, "https://example.edu/zhang.html")
    async def test_complete_chunk_clears_profile_url_when_it_matches_known_listing_url(self) -> None:
        job_id, chunk_id = await self._seed_processing_chunk()
        listing_url = "https://example.edu/faculty"

        result = await complete_current_chunk(
            self.session_factory,
            chunk_id=chunk_id,
            worker_id="w1",
            candidates=[ProfessorCandidatePayload(name="张三", email="zhang@example.edu", profile_url=listing_url, confidence=0.9)],
            discovered_urls=[],
            candidate_count=1,
        )

        self.assertEqual(result["saved_count"], 1)
        async with self.session_factory() as session:
            row = await session.scalar(select(CrawlCandidate).where(CrawlCandidate.job_id == job_id))
        assert row is not None
        self.assertEqual(row.name, "张三")
        self.assertEqual(row.email, "zhang@example.edu")
        self.assertIsNone(row.profile_url)
        self.assertEqual(row.identity_key, "zhang@example.edu")
        self.assertNotIn("profile_url", row.field_sources or {})

    async def test_complete_chunk_ignores_legacy_discovered_url_argument(self) -> None:
        job_id, chunk_id = await self._seed_processing_chunk()
        discovered_listing_url = "https://example.edu/page2.html"

        result = await complete_current_chunk(
            self.session_factory,
            chunk_id=chunk_id,
            worker_id="w1",
            candidates=[ProfessorCandidatePayload(name="张三", email="zhang@example.edu", profile_url=discovered_listing_url, confidence=0.9)],
            discovered_urls=[discovered_listing_url],
            candidate_count=1,
        )

        self.assertEqual(result["saved_count"], 1)
        self.assertEqual(result["url_count"], 0)
        async with self.session_factory() as session:
            row = await session.scalar(select(CrawlCandidate).where(CrawlCandidate.job_id == job_id))
            tasks = list(await session.scalars(select(CrawlPageTask).where(CrawlPageTask.job_id == job_id)))
        assert row is not None
        self.assertEqual(row.profile_url, discovered_listing_url)
        self.assertEqual(row.identity_key, "zhang@example.edu")
        self.assertEqual(tasks, [])

    async def test_complete_chunk_does_not_merge_distinct_candidates_sharing_listing_profile_url(self) -> None:
        job_id, chunk_id = await self._seed_processing_chunk()
        listing_url = "https://example.edu/faculty"

        result = await complete_current_chunk(
            self.session_factory,
            chunk_id=chunk_id,
            worker_id="w1",
            candidates=[
                ProfessorCandidatePayload(
                    name="张三",
                    email="zhang@example.edu",
                    title="教授",
                    profile_url=listing_url,
                    confidence=0.9,
                ),
                ProfessorCandidatePayload(
                    name="李四",
                    email="li@example.edu",
                    title="副教授",
                    profile_url=listing_url,
                    confidence=0.9,
                ),
            ],
            discovered_urls=[],
            candidate_count=2,
        )

        self.assertEqual(result["saved_count"], 2)
        self.assertEqual(result["rejected_count"], 0)
        self.assertEqual(result["merged_count"], 0)
        async with self.session_factory() as session:
            rows = list(await session.scalars(select(CrawlCandidate).where(CrawlCandidate.job_id == job_id).order_by(CrawlCandidate.id)))
        self.assertEqual([row.name for row in rows], ["张三", "李四"])
        self.assertEqual([row.profile_url for row in rows], [None, None])
        self.assertEqual(
            [row.identity_key for row in rows],
            ["zhang@example.edu", "li@example.edu"],
        )
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
            candidate_count=2,
        )

        self.assertEqual(result["saved_count"], 1)
        self.assertEqual(result["rejected_count"], 1)
        async with self.session_factory() as session:
            rows = list(await session.scalars(select(CrawlCandidate).where(CrawlCandidate.job_id == job_id)))
        self.assertEqual([row.name for row in rows], ["李四"])
    async def test_complete_chunk_saves_candidates_without_discovering_urls(self) -> None:
        job_id, chunk_id = await self._seed_processing_chunk()
        candidate = ProfessorCandidatePayload(name="张三", profile_url="https://example.edu/zhang.html", source_url="https://example.edu/faculty", confidence=0.9)

        result = await complete_current_chunk(
            self.session_factory,
            chunk_id=chunk_id,
            worker_id="w1",
            candidates=[candidate],
            discovered_urls=["https://example.edu/faculty/list2.html", "https://other.edu/nope"],
            candidate_count=1,
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
        self.assertEqual(page_tasks, [])
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
                "candidate_count": 1,
                "candidates": [{"name": "张三", "profile_url": "https://example.edu/zhang.html"}],
                "discovered_urls": ["https://example.edu/faculty/list2.html"],
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
            "candidate_count": 1,
            "candidates": [
                {
                    "name": "张三",
                    "profile_url": "https://example.edu/zhang.html",
                    "source_url": "https://example.edu/faculty",
                    "confidence": 0.9,
                }
            ],
            "discovered_urls": ["https://example.edu/faculty/list2.html"],
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
        self.assertEqual(tasks, [])


    async def test_complete_chunk_never_enqueues_worker_discovered_urls(self) -> None:
        job_id, chunk_id = await self._seed_processing_chunk()

        result = await complete_current_chunk(
            self.session_factory,
            chunk_id=chunk_id,
            worker_id="w1",
            candidates=[
                ProfessorCandidatePayload(
                    name="张三",
                    email="zhang@example.edu",
                    profile_url="https://example.edu/people/li.html",
                    confidence=0.9,
                ),
            ],
            discovered_urls=[
                "https://example.edu/people/li.html",
                "https://example.edu/about.html",
                "https://example.edu/news/2024.html",
                "https://example.edu/faculty/list2.html",
                "https://example.edu/teachers?page=2",
                "https://example.edu/faculty/index1.htm",
            ],
            candidate_count=1,
        )

        self.assertEqual(result["saved_count"], 1)
        self.assertEqual(result["rejected_count"], 0)
        self.assertEqual(result["url_count"], 0)
        async with self.session_factory() as session:
            tasks = list(await session.scalars(select(CrawlPageTask).where(CrawlPageTask.job_id == job_id).order_by(CrawlPageTask.id)))
            candidate = await session.scalar(select(CrawlCandidate).where(CrawlCandidate.job_id == job_id))
        self.assertEqual(tasks, [])
        assert candidate is not None
        self.assertEqual(candidate.profile_url, "https://example.edu/people/li.html")

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
            candidate_count=1,
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
                candidate_count=1,
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
            candidate_count=1,
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
    async def test_invoke_chunk_agent_passes_runtime_adaptation_to_structured_request(self) -> None:
        completion = ChatCompletionResult(
            content='{"candidate_count": 0, "candidates": [], "discovered_urls": []}',
            usage=ChatCompletionUsage(
                prompt_tokens=1,
                completion_tokens=1,
                total_tokens=2,
                cached_tokens=0,
            ),
        )
        wire_payload = V2ChunkWirePayload(
            candidate_count=0,
            candidates=[],
        )
        adaptation = LLMRuntimeAdaptation("responses", {"enable_thinking": False})
        llm_profile = LLMProfile(
            name="test",
            provider="openai",
            api_key="test",
            model_name="test-model",
        )

        with patch(
            "app.services.crawler_v2_chunk_worker.request_crawler_structured_completion",
            new=AsyncMock(
                return_value=(completion, wire_payload, "json_schema_strict")
            ),
        ) as invoke_mock:
            payload, usage, raw_model_text = await invoke_v2_chunk_agent(
                llm_profile,
                session_factory=self.session_factory,
                university="示例大学",
                school="计算机学院",
                source_url="https://example.edu/faculty",
                chunk_content="张三",
                adaptation=adaptation,
            )

        invoke_mock.assert_awaited_once()
        self.assertIs(invoke_mock.await_args.args[0], self.session_factory)
        self.assertIs(invoke_mock.await_args.args[1], llm_profile)
        self.assertIs(invoke_mock.await_args.args[2], adaptation)
        self.assertIs(invoke_mock.await_args.kwargs["result_model"], V2ChunkWirePayload)
        self.assertEqual(payload["candidate_count"], 0)
        self.assertEqual(usage["input_tokens"], 1)
        self.assertIn("candidate_count", raw_model_text)

    async def test_chunk_worker_uses_thinking_adaptation_extra_body(self) -> None:
        _, chunk_id = await self._seed_processing_chunk(with_profile=True)
        payload = {"candidate_count": 0, "candidates": []}
        usage = {"input_tokens": 10, "output_tokens": 2, "cached_tokens": 0}
        adaptation = LLMRuntimeAdaptation("responses", {"enable_thinking": False})

        with patch("app.services.crawler_v2_chunk_worker.ensure_llm_runtime_adaptation", new=AsyncMock(return_value=adaptation)) as adapt_mock, patch("app.services.crawler_v2_chunk_worker.invoke_v2_chunk_agent", new=AsyncMock(return_value=(payload, usage, '{"candidate_count":0,"candidates":[]}'))) as invoke_mock:
            processed = await run_crawler_v2_chunk_worker_once(
                self.session_factory,
                chunk_id=chunk_id,
                worker_id="w1",
            )

        self.assertEqual(processed, 1)
        adapt_mock.assert_awaited_once()
        invoke_mock.assert_awaited_once()
        self.assertIs(invoke_mock.await_args.kwargs["adaptation"], adaptation)

    async def test_chunk_worker_records_thinking_adaptation_failure_on_chunk(self) -> None:
        _, chunk_id = await self._seed_processing_chunk(with_profile=True)

        with patch(
            "app.services.crawler_v2_chunk_worker.ensure_llm_runtime_adaptation",
            new=AsyncMock(side_effect=RuntimeError("模型服务连接失败，请检查系统代理或网络后重试")),
        ):
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
        self.assertIn("模型服务连接失败", chunk.last_error or "")
        self.assertIsNone(chunk.worker_id)
        self.assertIsNone(chunk.claimed_at)

    async def test_chunk_worker_records_llm_token_usage(self) -> None:
        _, chunk_id = await self._seed_processing_chunk(with_profile=True)

        completion = ChatCompletionResult(
            content='{"candidate_count":0,"candidates":[]}',
            usage=ChatCompletionUsage(
                prompt_tokens=100,
                completion_tokens=20,
                total_tokens=120,
                cached_tokens=80,
            ),
        )
        wire_payload = V2ChunkWirePayload(
            candidate_count=0,
            candidates=[],
        )

        with patch(
            "app.services.crawler_v2_chunk_worker.request_crawler_structured_completion",
            new=AsyncMock(return_value=(completion, wire_payload, "json_object")),
        ):
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
