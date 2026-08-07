from __future__ import annotations

import unittest
from datetime import UTC, datetime

import asyncio
import os
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


if TYPE_CHECKING:
    from app.models import LLMProfile


def _make_test_session_factory() -> tuple[async_sessionmaker, Path]:
    """Return (session_factory, db_path) bound to a fresh migrated sqlite file."""
    from test.migrated_database import create_migrated_sqlite_database

    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    db_path = Path(tmp.name)
    create_migrated_sqlite_database(db_path)
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path.as_posix()}")
    session_factory = async_sessionmaker(
        engine,
        autoflush=False,
        expire_on_commit=False,
    )
    return session_factory, db_path


class ThinkingAdaptationCacheModelTests(unittest.TestCase):
    def test_model_round_trip_in_memory(self) -> None:
        from app.models import ThinkingAdaptationCache

        row = ThinkingAdaptationCache(
            api_base_url="https://api.deepseek.com/v1",
            model_name="deepseek-chat",
            endpoint_kind="chat_completions",
            learned_extra_body={"thinking": {"type": "disabled"}},
            probed_at=datetime(2026, 5, 14, tzinfo=UTC),
        )
        self.assertEqual(row.api_base_url, "https://api.deepseek.com/v1")
        self.assertEqual(row.model_name, "deepseek-chat")
        self.assertEqual(row.endpoint_kind, "chat_completions")
        self.assertEqual(
            row.learned_extra_body,
            {"thinking": {"type": "disabled"}},
        )

    def test_learned_extra_body_can_be_none(self) -> None:
        from app.models import ThinkingAdaptationCache

        row = ThinkingAdaptationCache(
            api_base_url="https://api.openai.com/v1",
            model_name="gpt-4o-mini",
            endpoint_kind="chat_completions",
            learned_extra_body=None,
            probed_at=datetime(2026, 5, 14, tzinfo=UTC),
        )
        self.assertIsNone(row.learned_extra_body)


class IsThinkingModeProtocolErrorTests(unittest.TestCase):
    def test_returns_true_for_deepseek_reasoning_content_error(self) -> None:
        from app.modules.llm.adaptation.thinking import is_thinking_mode_protocol_error

        body = (
            '{"error":{"code":"400","message":"Param Incorrect",'
            '"param":"The reasoning_content in the thinking mode '
            'must be passed back to the API."}}'
        )
        self.assertTrue(is_thinking_mode_protocol_error(400, body))

    def test_returns_true_for_thinking_block_error(self) -> None:
        from app.modules.llm.adaptation.thinking import is_thinking_mode_protocol_error

        body = '{"error":{"message":"thinking block must be preserved"}}'
        self.assertTrue(is_thinking_mode_protocol_error(400, body))

    def test_returns_false_for_non_400_status(self) -> None:
        from app.modules.llm.adaptation.thinking import is_thinking_mode_protocol_error

        body = (
            '{"error":{"message":"The reasoning_content in the thinking '
            'mode must be passed back to the API."}}'
        )
        self.assertFalse(is_thinking_mode_protocol_error(500, body))
        self.assertFalse(is_thinking_mode_protocol_error(401, body))

    def test_returns_false_for_unrelated_400(self) -> None:
        from app.modules.llm.adaptation.thinking import is_thinking_mode_protocol_error

        body = '{"error":{"message":"Not supported model"}}'
        self.assertFalse(is_thinking_mode_protocol_error(400, body))

    def test_returns_false_for_empty_body(self) -> None:
        from app.modules.llm.adaptation.thinking import is_thinking_mode_protocol_error

        self.assertFalse(is_thinking_mode_protocol_error(400, ""))


class ThinkingDisableCandidatesTests(unittest.TestCase):
    def test_candidates_in_priority_order(self) -> None:
        from app.modules.llm.adaptation.thinking import THINKING_DISABLE_CANDIDATES

        self.assertEqual(
            list(THINKING_DISABLE_CANDIDATES),
            [
                {"thinking": {"type": "disabled"}},
                {"enable_thinking": False},
                {"reasoning": {"effort": "off"}},
                {"reasoning_effort": "low"},
                {"thinking_budget": 0},
            ],
        )

    def test_thinking_keys_include_reasoning_effort(self) -> None:
        from app.modules.llm.adaptation.thinking import merge_extra_body

        merged = merge_extra_body(
            {
                "model": "deepseek-v4",
                "messages": [{"role": "user", "content": "ping"}],
                "reasoning_effort": "high",
            },
            {"enable_thinking": False},
        )

        self.assertEqual(merged["enable_thinking"], False)
        self.assertNotIn("reasoning_effort", merged)

    def test_merge_extra_body_overrides_existing_thinking_keys(self) -> None:
        from app.modules.llm.adaptation.thinking import merge_extra_body

        merged = merge_extra_body(
            {
                "model": "deepseek-chat",
                "messages": [{"role": "user", "content": "ping"}],
                "thinking": {"type": "enabled"},
                "enable_thinking": True,
            },
            {"thinking": {"type": "disabled"}},
        )
        self.assertEqual(merged["thinking"], {"type": "disabled"})
        self.assertNotIn("enable_thinking", merged)
        self.assertEqual(merged["messages"], [{"role": "user", "content": "ping"}])

    def test_merge_extra_body_handles_none(self) -> None:
        from app.modules.llm.adaptation.thinking import merge_extra_body

        merged = merge_extra_body(
            {"model": "gpt-4o-mini", "thinking": {"type": "enabled"}},
            None,
        )
        self.assertNotIn("thinking", merged)
        self.assertEqual(merged["model"], "gpt-4o-mini")


class CacheReadWriteTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.session_factory, self.db_path = _make_test_session_factory()

    async def asyncTearDown(self) -> None:
        engine = self.session_factory.kw.get("bind")
        if engine is not None:
            await engine.dispose()
        try:
            os.unlink(self.db_path)
        except FileNotFoundError:
            pass

    async def test_miss_returns_false_with_none(self) -> None:
        from app.modules.llm.adaptation.thinking import get_cached_extra_body

        async with self.session_factory() as session:
            hit, value = await get_cached_extra_body(
                session,
                api_base_url="https://api.deepseek.com/v1",
                model_name="deepseek-chat",
            )
        self.assertFalse(hit)
        self.assertIsNone(value)

    async def test_record_then_get_returns_hit_with_value(self) -> None:
        from app.models import ThinkingAdaptationCache
        from app.modules.llm.adaptation.thinking import (
            get_cached_extra_body,
            record_thinking_adaptation,
        )
        from sqlalchemy import select

        async with self.session_factory() as session:
            await record_thinking_adaptation(
                session,
                api_base_url="https://api.deepseek.com/v1",
                model_name="deepseek-chat",
                learned_extra_body={"thinking": {"type": "disabled"}},
            )
            await session.commit()

        async with self.session_factory() as session:
            hit, value = await get_cached_extra_body(
                session,
                api_base_url="https://api.deepseek.com/v1",
                model_name="deepseek-chat",
            )
            row = await session.scalar(select(ThinkingAdaptationCache))
        self.assertTrue(hit)
        self.assertEqual(value, {"thinking": {"type": "disabled"}})
        self.assertIsNotNone(row)
        self.assertEqual(row.endpoint_kind, "chat_completions")

    async def test_explicit_endpoint_kind_isolated_from_default_cache(self) -> None:
        from app.modules.llm.adaptation.thinking import (
            get_cached_extra_body,
            record_thinking_adaptation,
        )

        async with self.session_factory() as session:
            await record_thinking_adaptation(
                session,
                api_base_url="https://api.example.test/v1",
                model_name="test-model",
                learned_extra_body={"enable_thinking": False},
            )
            await record_thinking_adaptation(
                session,
                api_base_url="https://api.example.test/v1",
                model_name="test-model",
                endpoint_kind="responses",
                learned_extra_body={"thinking": {"type": "disabled"}},
            )
            await session.commit()

        async with self.session_factory() as session:
            default_hit, default_value = await get_cached_extra_body(
                session,
                api_base_url="https://api.example.test/v1",
                model_name="test-model",
            )
            responses_hit, responses_value = await get_cached_extra_body(
                session,
                api_base_url="https://api.example.test/v1",
                model_name="test-model",
                endpoint_kind="responses",
            )

        self.assertTrue(default_hit)
        self.assertEqual(default_value, {"enable_thinking": False})
        self.assertTrue(responses_hit)
        self.assertEqual(responses_value, {"thinking": {"type": "disabled"}})

    async def test_record_with_none_persists_known_no_extra_body(self) -> None:
        from app.modules.llm.adaptation.thinking import (
            get_cached_extra_body,
            record_thinking_adaptation,
        )

        async with self.session_factory() as session:
            await record_thinking_adaptation(
                session,
                api_base_url="https://api.openai.com/v1",
                model_name="gpt-4o-mini",
                learned_extra_body=None,
            )
            await session.commit()

        async with self.session_factory() as session:
            hit, value = await get_cached_extra_body(
                session,
                api_base_url="https://api.openai.com/v1",
                model_name="gpt-4o-mini",
            )
        # 命中但值为 None：表示已探活，确认无需 extra_body
        self.assertTrue(hit)
        self.assertIsNone(value)

    async def test_record_twice_updates_existing_row(self) -> None:
        from app.modules.llm.adaptation.thinking import (
            get_cached_extra_body,
            record_thinking_adaptation,
        )

        async with self.session_factory() as session:
            await record_thinking_adaptation(
                session,
                api_base_url="https://api.acme.ai/v1",
                model_name="acme-v1",
                learned_extra_body={"thinking": {"type": "disabled"}},
            )
            await session.commit()

        async with self.session_factory() as session:
            await record_thinking_adaptation(
                session,
                api_base_url="https://api.acme.ai/v1",
                model_name="acme-v1",
                learned_extra_body={"enable_thinking": False},
            )
            await session.commit()

        async with self.session_factory() as session:
            hit, value = await get_cached_extra_body(
                session,
                api_base_url="https://api.acme.ai/v1",
                model_name="acme-v1",
            )
        self.assertTrue(hit)
        self.assertEqual(value, {"enable_thinking": False})

    async def test_record_twice_before_commit_updates_single_pending_row(self) -> None:
        from app.models import ThinkingAdaptationCache
        from app.modules.llm.adaptation.thinking import (
            get_cached_extra_body,
            record_thinking_adaptation,
        )
        from sqlalchemy import func, select

        async with self.session_factory() as session:
            await record_thinking_adaptation(
                session,
                api_base_url="https://api.acme.ai/v1",
                model_name="acme-v1",
                learned_extra_body=None,
            )
            await record_thinking_adaptation(
                session,
                api_base_url="https://api.acme.ai/v1",
                model_name="acme-v1",
                learned_extra_body={"thinking": {"type": "disabled"}},
            )
            await session.commit()

        async with self.session_factory() as session:
            hit, value = await get_cached_extra_body(
                session,
                api_base_url="https://api.acme.ai/v1",
                model_name="acme-v1",
            )
            row_count = await session.scalar(
                select(func.count()).select_from(ThinkingAdaptationCache)
            )

        self.assertTrue(hit)
        self.assertEqual(value, {"thinking": {"type": "disabled"}})
        self.assertEqual(row_count, 1)

    async def test_concurrent_record_uses_single_cache_row(self) -> None:
        from app.models import ThinkingAdaptationCache
        from app.modules.llm.adaptation.thinking import (
            get_cached_extra_body,
            record_thinking_adaptation,
        )
        from sqlalchemy import func, select

        async def record(value: dict[str, object] | None) -> None:
            async with self.session_factory() as session:
                await record_thinking_adaptation(
                    session,
                    api_base_url="https://api.concurrent.ai/v1",
                    model_name="concurrent-v1",
                    learned_extra_body=value,
                )
                await session.commit()

        await asyncio.gather(
            record({"thinking": {"type": "disabled"}}),
            record({"enable_thinking": False}),
        )

        async with self.session_factory() as session:
            hit, value = await get_cached_extra_body(
                session,
                api_base_url="https://api.concurrent.ai/v1",
                model_name="concurrent-v1",
            )
            row_count = await session.scalar(
                select(func.count()).select_from(ThinkingAdaptationCache)
            )

        self.assertTrue(hit)
        self.assertIn(
            value,
            [
                {"thinking": {"type": "disabled"}},
                {"enable_thinking": False},
            ],
        )
        self.assertEqual(row_count, 1)

    async def test_record_updates_loaded_row_in_same_session(self) -> None:
        from app.modules.llm.adaptation.thinking import (
            get_cached_extra_body,
            record_thinking_adaptation,
        )

        async with self.session_factory() as session:
            await record_thinking_adaptation(
                session,
                api_base_url="https://api.identity-map.ai/v1",
                model_name="identity-v1",
                learned_extra_body={"thinking": {"type": "disabled"}},
            )
            await session.commit()

        async with self.session_factory() as session:
            hit, value = await get_cached_extra_body(
                session,
                api_base_url="https://api.identity-map.ai/v1",
                model_name="identity-v1",
            )
            self.assertTrue(hit)
            self.assertEqual(value, {"thinking": {"type": "disabled"}})

            await record_thinking_adaptation(
                session,
                api_base_url="https://api.identity-map.ai/v1",
                model_name="identity-v1",
                learned_extra_body={"enable_thinking": False},
            )

            hit, value = await get_cached_extra_body(
                session,
                api_base_url="https://api.identity-map.ai/v1",
                model_name="identity-v1",
            )

        self.assertTrue(hit)
        self.assertEqual(value, {"enable_thinking": False})


class ProbeAndLearnTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.session_factory, self.db_path = _make_test_session_factory()

    async def asyncTearDown(self) -> None:
        engine = self.session_factory.kw.get("bind")
        if engine is not None:
            await engine.dispose()
        try:
            os.unlink(self.db_path)
        except FileNotFoundError:
            pass

    def _profile(self) -> LLMProfile:
        from app.models import LLMProfile

        return LLMProfile(
            name="acme",
            provider="openai",
            api_base_url="https://api.acme.ai/v1",
            api_key="sk-test",
            model_name="acme-think-v1",
        )

    async def test_non_thinking_model_records_none_and_returns_none(self) -> None:
        from unittest.mock import patch

        from test.test_llm_runtime import _FakeAsyncClient, _FakeResponse

        from app.modules.llm.adaptation.thinking import (
            get_cached_extra_body,
            probe_and_learn_extra_body,
        )

        calls: list[tuple[str, dict[str, object] | None]] = []
        responses = [
            _FakeResponse(
                status_code=200,
                payload={"choices": [{"message": {"content": "7"}}]},
            ),
        ]

        with patch(
            "app.modules.llm.runtime.httpx.AsyncClient",
            side_effect=lambda *a, **kw: _FakeAsyncClient(responses, calls),
        ):
            async with self.session_factory() as session:
                result = await probe_and_learn_extra_body(session, self._profile())
                await session.commit()

        self.assertIsNone(result)
        self.assertEqual(len(calls), 1)
        first_payload = calls[0][1]
        assert first_payload is not None
        # 多轮 messages：3 条
        self.assertEqual(len(first_payload["messages"]), 3)

        async with self.session_factory() as session:
            hit, value = await get_cached_extra_body(
                session,
                api_base_url="https://api.acme.ai/v1",
                model_name="acme-think-v1",
            )
        self.assertTrue(hit)
        self.assertIsNone(value)

    def test_stepfun_probe_payload_uses_larger_budget_only_for_official_base_urls(self) -> None:
        from app.models import LLMProfile
        from app.modules.llm.adaptation.thinking import _build_probe_payload

        for base_url in (
            "https://api.stepfun.com/v1",
            "https://api.stepfun.com/step_plan/v1/",
        ):
            with self.subTest(base_url=base_url):
                profile = LLMProfile(
                    name="stepfun",
                    provider="openai",
                    api_base_url=base_url,
                    api_key="sk-test",
                    model_name="step-3.7-flash",
                    max_tokens=6000,
                )
                self.assertEqual(_build_probe_payload(profile)["max_tokens"], 128)

        self.assertEqual(_build_probe_payload(self._profile())["max_tokens"], 16)

    async def test_silent_thinking_model_selects_lower_token_candidate(self) -> None:
        from unittest.mock import patch

        from test.test_llm_runtime import _FakeAsyncClient, _FakeResponse

        from app.modules.llm.adaptation.thinking import (
            get_cached_extra_body,
            probe_and_learn_extra_body,
        )

        calls: list[tuple[str, dict[str, object] | None]] = []
        responses = [
            _FakeResponse(
                status_code=200,
                payload={
                    "choices": [{"message": {"content": "7"}}],
                    "usage": {
                        "prompt_tokens": 250,
                        "completion_tokens": 152,
                        "total_tokens": 402,
                        "completion_tokens_details": {"reasoning_tokens": 144},
                    },
                },
            ),
            _FakeResponse(
                status_code=200,
                payload={
                    "choices": [{"message": {"content": "7"}}],
                    "usage": {
                        "prompt_tokens": 250,
                        "completion_tokens": 80,
                        "total_tokens": 330,
                        "completion_tokens_details": {"reasoning_tokens": 20},
                    },
                },
            ),
            _FakeResponse(
                status_code=200,
                payload={
                    "choices": [{"message": {"content": "7"}}],
                    "usage": {
                        "prompt_tokens": 250,
                        "completion_tokens": 2,
                        "total_tokens": 252,
                        "completion_tokens_details": {"reasoning_tokens": 0},
                    },
                },
            ),
            _FakeResponse(
                status_code=200,
                payload={
                    "choices": [{"message": {"content": "7"}}],
                    "usage": {
                        "prompt_tokens": 250,
                        "completion_tokens": 10,
                        "total_tokens": 260,
                        "completion_tokens_details": {"reasoning_tokens": 1},
                    },
                },
            ),
            _FakeResponse(
                status_code=200,
                payload={
                    "choices": [{"message": {"content": "7"}}],
                    "usage": {
                        "prompt_tokens": 250,
                        "completion_tokens": 8,
                        "total_tokens": 258,
                        "completion_tokens_details": {"reasoning_tokens": 1},
                    },
                },
            ),
            _FakeResponse(
                status_code=200,
                payload={
                    "choices": [{"message": {"content": "7"}}],
                    "usage": {
                        "prompt_tokens": 250,
                        "completion_tokens": 12,
                        "total_tokens": 262,
                        "completion_tokens_details": {"reasoning_tokens": 1},
                    },
                },
            ),
        ]

        with patch(
            "app.modules.llm.runtime.httpx.AsyncClient",
            side_effect=lambda *a, **kw: _FakeAsyncClient(responses, calls),
        ):
            async with self.session_factory() as session:
                result = await probe_and_learn_extra_body(session, self._profile())
                await session.commit()

        self.assertEqual(result, {"enable_thinking": False})
        self.assertEqual(len(calls), 6)
        selected_payload = calls[2][1]
        assert selected_payload is not None
        self.assertEqual(selected_payload["enable_thinking"], False)

        async with self.session_factory() as session:
            hit, value = await get_cached_extra_body(
                session,
                api_base_url="https://api.acme.ai/v1",
                model_name="acme-think-v1",
            )
        self.assertTrue(hit)
        self.assertEqual(value, {"enable_thinking": False})

    async def test_thinking_model_retries_first_candidate_and_caches(self) -> None:
        from unittest.mock import patch

        from test.test_llm_runtime import _FakeAsyncClient, _FakeResponse

        from app.modules.llm.adaptation.thinking import (
            get_cached_extra_body,
            probe_and_learn_extra_body,
        )

        calls: list[tuple[str, dict[str, object] | None]] = []
        protocol_body = (
            '{"error":{"code":"400","message":"Param Incorrect",'
            '"param":"The reasoning_content in the thinking mode '
            'must be passed back to the API."}}'
        )
        responses = [
            _FakeResponse(status_code=400, text=protocol_body),
            _FakeResponse(
                status_code=200,
                payload={"choices": [{"message": {"content": "7"}}]},
            ),
        ]

        with patch(
            "app.modules.llm.runtime.httpx.AsyncClient",
            side_effect=lambda *a, **kw: _FakeAsyncClient(responses, calls),
        ):
            async with self.session_factory() as session:
                result = await probe_and_learn_extra_body(session, self._profile())
                await session.commit()

        self.assertEqual(result, {"thinking": {"type": "disabled"}})
        self.assertEqual(len(calls), 2)
        first_payload, second_payload = calls[0][1], calls[1][1]
        assert first_payload is not None and second_payload is not None
        self.assertNotIn("thinking", first_payload)
        self.assertEqual(second_payload["thinking"], {"type": "disabled"})

        async with self.session_factory() as session:
            hit, value = await get_cached_extra_body(
                session,
                api_base_url="https://api.acme.ai/v1",
                model_name="acme-think-v1",
            )
        self.assertTrue(hit)
        self.assertEqual(value, {"thinking": {"type": "disabled"}})

    async def test_responses_probe_stays_on_responses_endpoint_and_cache(self) -> None:
        from unittest.mock import patch

        from test.test_llm_runtime import _FakeAsyncClient, _FakeResponse

        from app.modules.llm.adaptation.thinking import (
            get_cached_extra_body,
            probe_and_learn_extra_body,
        )

        calls: list[tuple[str, dict[str, object] | None]] = []
        with patch(
            "app.modules.llm.runtime.httpx.AsyncClient",
            side_effect=lambda *a, **kw: _FakeAsyncClient(
                [_FakeResponse(status_code=200, payload={"output_text": "7"})],
                calls,
            ),
        ):
            async with self.session_factory() as session:
                result = await probe_and_learn_extra_body(
                    session,
                    self._profile(),
                    endpoint_kind="responses",
                )
                await session.commit()

        self.assertIsNone(result)
        self.assertEqual([url for url, _ in calls], ["https://api.acme.ai/v1/responses"])
        async with self.session_factory() as session:
            hit, cached = await get_cached_extra_body(
                session,
                api_base_url="https://api.acme.ai/v1",
                model_name="acme-think-v1",
                endpoint_kind="responses",
            )
        self.assertTrue(hit)
        self.assertIsNone(cached)

    async def test_thinking_model_walks_candidates_until_success(self) -> None:
        from unittest.mock import patch

        from test.test_llm_runtime import _FakeAsyncClient, _FakeResponse

        from app.modules.llm.adaptation.thinking import (
            probe_and_learn_extra_body,
        )

        calls: list[tuple[str, dict[str, object] | None]] = []
        protocol_body = (
            '{"error":{"message":"reasoning_content must be passed back"}}'
        )
        responses = [
            _FakeResponse(status_code=400, text=protocol_body),  # 不带 extra_body
            _FakeResponse(status_code=400, text=protocol_body),  # 候选 1
            _FakeResponse(  # 候选 2 成功
                status_code=200,
                payload={"choices": [{"message": {"content": "7"}}]},
            ),
        ]

        with patch(
            "app.modules.llm.runtime.httpx.AsyncClient",
            side_effect=lambda *a, **kw: _FakeAsyncClient(responses, calls),
        ):
            async with self.session_factory() as session:
                result = await probe_and_learn_extra_body(session, self._profile())
                await session.commit()

        self.assertEqual(result, {"enable_thinking": False})
        third_payload = calls[2][1]
        assert third_payload is not None
        self.assertEqual(third_payload["enable_thinking"], False)

    async def test_all_candidates_exhausted_raises(self) -> None:
        from unittest.mock import patch

        from test.test_llm_runtime import _FakeAsyncClient, _FakeResponse

        from app.modules.llm.adaptation.thinking import (
            ThinkingAdaptationFailed,
            THINKING_DISABLE_CANDIDATES,
            probe_and_learn_extra_body,
        )

        calls: list[tuple[str, dict[str, object] | None]] = []
        protocol_body = (
            '{"error":{"message":"reasoning_content must be passed back"}}'
        )
        responses = [
            _FakeResponse(status_code=400, text=protocol_body)
            for _ in range(len(THINKING_DISABLE_CANDIDATES) + 1)
        ]

        with patch(
            "app.modules.llm.runtime.httpx.AsyncClient",
            side_effect=lambda *a, **kw: _FakeAsyncClient(responses, calls),
        ):
            async with self.session_factory() as session:
                with self.assertRaises(ThinkingAdaptationFailed) as ctx:
                    await probe_and_learn_extra_body(session, self._profile())

        self.assertEqual(
            ctx.exception.attempted_extra_bodies,
            [None, *THINKING_DISABLE_CANDIDATES],
        )

    async def test_non_protocol_400_propagates_without_retry(self) -> None:
        from unittest.mock import patch

        from test.test_llm_runtime import _FakeAsyncClient, _FakeResponse
        from app.modules.llm.runtime import LLMRuntimeError

        from app.modules.llm.adaptation.thinking import probe_and_learn_extra_body

        calls: list[tuple[str, dict[str, object] | None]] = []
        responses = [
            _FakeResponse(
                status_code=400,
                text='{"error":{"message":"Not supported model"}}',
            ),
        ]

        with patch(
            "app.modules.llm.runtime.httpx.AsyncClient",
            side_effect=lambda *a, **kw: _FakeAsyncClient(responses, calls),
        ):
            async with self.session_factory() as session:
                with self.assertRaises(LLMRuntimeError):
                    await probe_and_learn_extra_body(session, self._profile())

        self.assertEqual(len(calls), 1)

    async def test_thinking_model_with_empty_content_triggers_candidate_switch(self) -> None:
        # 思考模型在第 1 次调用时返回 HTTP 200，但 content 为空（思考内容塞进了 reasoning_content）
        # → request_chat_completion 抛 LLMRuntimeError("模型返回了空内容", status_code=200)
        # → probe_and_learn_extra_body 应识别为思考模式信号，切候选 1 重试
        from unittest.mock import patch

        from test.test_llm_runtime import _FakeAsyncClient, _FakeResponse

        from app.modules.llm.adaptation.thinking import (
            get_cached_extra_body,
            probe_and_learn_extra_body,
        )

        calls: list[tuple[str, dict[str, object] | None]] = []
        responses = [
            # 第 1 次：HTTP 200，但 content 为空字符串
            _FakeResponse(
                status_code=200,
                payload={"choices": [{"message": {"content": ""}}]},
            ),
            # 第 2 次：带 thinking={"type":"disabled"}，正常返回
            _FakeResponse(
                status_code=200,
                payload={"choices": [{"message": {"content": "7"}}]},
            ),
        ]

        with patch(
            "app.modules.llm.runtime.httpx.AsyncClient",
            side_effect=lambda *a, **kw: _FakeAsyncClient(responses, calls),
        ):
            async with self.session_factory() as session:
                result = await probe_and_learn_extra_body(session, self._profile())
                await session.commit()

        self.assertEqual(result, {"thinking": {"type": "disabled"}})
        self.assertEqual(len(calls), 2)

        async with self.session_factory() as session:
            hit, value = await get_cached_extra_body(
                session,
                api_base_url="https://api.acme.ai/v1",
                model_name="acme-think-v1",
            )
        self.assertTrue(hit)
        self.assertEqual(value, {"thinking": {"type": "disabled"}})


class EnsureThinkingAdaptationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.session_factory, self.db_path = _make_test_session_factory()

    async def asyncTearDown(self) -> None:
        engine = self.session_factory.kw.get("bind")
        if engine is not None:
            await engine.dispose()
        try:
            os.unlink(self.db_path)
        except FileNotFoundError:
            pass

    def _profile(self) -> LLMProfile:
        from app.models import LLMProfile

        return LLMProfile(
            name="acme",
            provider="openai",
            api_base_url="https://api.acme.ai/v1",
            api_key="sk-test",
            model_name="acme-think-v1",
        )

    async def test_cache_hit_short_circuits_without_http_call(self) -> None:
        from unittest.mock import patch

        from test.test_llm_runtime import _FakeAsyncClient

        from app.modules.llm.adaptation.thinking import (
            ensure_thinking_adaptation,
            record_thinking_adaptation,
        )

        async with self.session_factory() as session:
            await record_thinking_adaptation(
                session,
                api_base_url="https://api.acme.ai/v1",
                model_name="acme-think-v1",
                learned_extra_body={"thinking": {"type": "disabled"}},
            )
            await session.commit()

        calls: list[tuple[str, dict[str, object] | None]] = []
        with patch(
            "app.modules.llm.runtime.httpx.AsyncClient",
            side_effect=lambda *a, **kw: _FakeAsyncClient([], calls),
        ):
            async with self.session_factory() as session:
                result = await ensure_thinking_adaptation(session, self._profile())

        self.assertEqual(result, {"thinking": {"type": "disabled"}})
        self.assertEqual(calls, [])

    async def test_cache_miss_runs_probe_and_returns_learned_value(self) -> None:
        from unittest.mock import patch

        from test.test_llm_runtime import _FakeAsyncClient, _FakeResponse

        from app.modules.llm.adaptation.thinking import (
            ensure_thinking_adaptation,
            get_cached_extra_body,
        )

        responses = [
            _FakeResponse(
                status_code=400,
                text='{"error":{"message":"reasoning_content must be passed back"}}',
            ),
            _FakeResponse(
                status_code=200,
                payload={"choices": [{"message": {"content": "7"}}]},
            ),
        ]
        calls: list[tuple[str, dict[str, object] | None]] = []
        with patch(
            "app.modules.llm.runtime.httpx.AsyncClient",
            side_effect=lambda *a, **kw: _FakeAsyncClient(responses, calls),
        ):
            async with self.session_factory() as session:
                result = await ensure_thinking_adaptation(session, self._profile())
                await session.commit()

        self.assertEqual(result, {"thinking": {"type": "disabled"}})

        async with self.session_factory() as session:
            hit, value = await get_cached_extra_body(
                session,
                api_base_url="https://api.acme.ai/v1",
                model_name="acme-think-v1",
            )
        self.assertTrue(hit)
        self.assertEqual(value, {"thinking": {"type": "disabled"}})

    async def test_cache_write_conflict_returns_existing_value(self) -> None:
        from unittest.mock import AsyncMock, patch

        from sqlalchemy.exc import IntegrityError

        from app.modules.llm.adaptation.thinking import ensure_thinking_adaptation

        from app.models import LLMProfile

        profile = LLMProfile(
            name="acme",
            provider="openai",
            api_base_url="https://api.acme.ai/v1",
            api_key="sk-test",
            model_name="acme-think-v1",
        )
        cached_value = {"thinking": {"type": "disabled"}}
        calls = {"count": 0}

        async def fake_get_cached_extra_body(*args, **kwargs):
            calls["count"] += 1
            if calls["count"] == 1:
                return False, None
            return True, cached_value

        with (
            patch(
                "app.modules.llm.adaptation.thinking.get_cached_extra_body",
                side_effect=fake_get_cached_extra_body,
            ),
            patch(
                "app.modules.llm.adaptation.thinking.probe_and_learn_extra_body",
                AsyncMock(
                    side_effect=IntegrityError(
                        "insert",
                        {},
                        Exception("UNIQUE constraint failed"),
                    ),
                ),
            ),
        ):
            async with self.session_factory() as session:
                result = await ensure_thinking_adaptation(session, profile)

        self.assertEqual(result, cached_value)

    async def test_invalidate_removes_only_current_endpoint_triple(self) -> None:
        from app.modules.llm.adaptation.thinking import (
            get_cached_extra_body,
            invalidate_thinking_adaptation,
            record_thinking_adaptation,
        )

        async with self.session_factory() as session:
            for endpoint_kind, value in (
                ("chat_completions", {"enable_thinking": False}),
                ("responses", {"thinking": {"type": "disabled"}}),
            ):
                await record_thinking_adaptation(
                    session,
                    api_base_url="https://api.acme.ai/v1",
                    model_name="acme-think-v1",
                    endpoint_kind=endpoint_kind,
                    learned_extra_body=value,
                )
            self.assertFalse(
                await invalidate_thinking_adaptation(
                    session,
                    api_base_url="https://api.acme.ai/v1",
                    model_name="acme-think-v1",
                    endpoint_kind="responses",
                    expected_extra_body={"enable_thinking": False},
                )
            )
            self.assertTrue(
                await invalidate_thinking_adaptation(
                    session,
                    api_base_url="https://api.acme.ai/v1",
                    model_name="acme-think-v1",
                    endpoint_kind="responses",
                    expected_extra_body={"thinking": {"type": "disabled"}},
                )
            )
            self.assertTrue(
                (await get_cached_extra_body(
                    session,
                    api_base_url="https://api.acme.ai/v1",
                    model_name="acme-think-v1",
                    endpoint_kind="chat_completions",
                ))[0]
            )
            self.assertFalse(
                (await get_cached_extra_body(
                    session,
                    api_base_url="https://api.acme.ai/v1",
                    model_name="acme-think-v1",
                    endpoint_kind="responses",
                ))[0]
            )

    async def test_invalidate_matches_cached_json_null(self) -> None:
        from app.modules.llm.adaptation.thinking import (
            get_cached_extra_body,
            invalidate_thinking_adaptation,
            record_thinking_adaptation,
        )

        async with self.session_factory() as session:
            await record_thinking_adaptation(
                session,
                api_base_url="https://api.null.ai/v1",
                model_name="null-think-v1",
                learned_extra_body=None,
            )
            self.assertTrue(
                await invalidate_thinking_adaptation(
                    session,
                    api_base_url="https://api.null.ai/v1",
                    model_name="null-think-v1",
                    endpoint_kind="chat_completions",
                    expected_extra_body=None,
                )
            )
            self.assertFalse(
                (await get_cached_extra_body(
                    session,
                    api_base_url="https://api.null.ai/v1",
                    model_name="null-think-v1",
                ))[0]
            )

    async def test_invalidate_keeps_value_refreshed_between_check_and_delete(self) -> None:
        from unittest.mock import patch

        from app.modules.llm.adaptation.thinking import (
            get_cached_extra_body,
            invalidate_thinking_adaptation,
            record_thinking_adaptation,
        )

        api_base_url = "https://api.concurrent.ai/v1"
        model_name = "concurrent-think-v1"
        stale_value = {"enable_thinking": False}
        refreshed_value = {"thinking": {"type": "disabled"}}

        async with self.session_factory() as session:
            await record_thinking_adaptation(
                session,
                api_base_url=api_base_url,
                model_name=model_name,
                learned_extra_body=stale_value,
            )
            await session.commit()

        async with self.session_factory() as stale_session:
            original_execute = stale_session.execute
            refreshed = False

            async def refresh_before_delete(statement, *args, **kwargs):
                nonlocal refreshed
                if not refreshed and getattr(statement, "is_delete", False):
                    refreshed = True
                    async with self.session_factory() as refresh_session:
                        await record_thinking_adaptation(
                            refresh_session,
                            api_base_url=api_base_url,
                            model_name=model_name,
                            learned_extra_body=refreshed_value,
                        )
                        await refresh_session.commit()
                return await original_execute(statement, *args, **kwargs)

            with patch.object(stale_session, "execute", side_effect=refresh_before_delete):
                self.assertFalse(
                    await invalidate_thinking_adaptation(
                        stale_session,
                        api_base_url=api_base_url,
                        model_name=model_name,
                        endpoint_kind="chat_completions",
                        expected_extra_body=stale_value,
                    )
                )

        async with self.session_factory() as session:
            self.assertEqual(
                await get_cached_extra_body(
                    session,
                    api_base_url=api_base_url,
                    model_name=model_name,
                ),
                (True, refreshed_value),
            )
            self.assertTrue(
                await invalidate_thinking_adaptation(
                    session,
                    api_base_url=api_base_url,
                    model_name=model_name,
                    endpoint_kind="chat_completions",
                    expected_extra_body=refreshed_value,
                )
            )
if __name__ == "__main__":
    unittest.main()
