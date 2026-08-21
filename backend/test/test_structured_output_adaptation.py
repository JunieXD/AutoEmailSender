from __future__ import annotations

import asyncio
import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from pydantic import BaseModel, ConfigDict
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.models import Base, LLMProfile
from app.modules.llm.runtime import (
    ChatCompletionResult,
    LLMRuntimeAdaptation,
    LLMRuntimeError,
    build_chat_completions_payload,
    build_responses_payload,
    request_structured_completion,
    with_structured_output,
)


class _StructuredResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    value: str


class StructuredOutputPayloadTests(unittest.TestCase):
    def test_chat_completions_strict_schema_payload(self) -> None:
        payload = with_structured_output(
            {"model": "test-model", "messages": []},
            mode="json_schema_strict",
            schema={"type": "object", "additionalProperties": False},
            schema_name="probe",
        )

        request = build_chat_completions_payload(payload)

        self.assertEqual(request["response_format"]["type"], "json_schema")
        self.assertEqual(request["response_format"]["json_schema"]["name"], "probe")
        self.assertTrue(request["response_format"]["json_schema"]["strict"])
        self.assertNotIn("__structured_output_control__", request)

    def test_responses_json_object_payload(self) -> None:
        payload = with_structured_output(
            {
                "model": "test-model",
                "messages": [{"role": "user", "content": "JSON please"}],
            },
            mode="json_object",
        )

        request = build_responses_payload(payload)

        self.assertEqual(request["text"], {"format": {"type": "json_object"}})
        self.assertNotIn("response_format", request)


class StructuredOutputAdaptationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.engine = create_async_engine(
            "sqlite+aiosqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        self.session_factory = async_sessionmaker(
            self.engine,
            autoflush=False,
            expire_on_commit=False,
        )
        self.profile = LLMProfile(
            name="test",
            provider="openai",
            api_base_url="https://api.example.test/v1/",
            api_key="secret",
            model_name="test-model",
        )

    async def asyncTearDown(self) -> None:
        await self.engine.dispose()

    async def test_strict_schema_has_highest_priority(self) -> None:
        from app.modules.llm.adaptation.structured_output import (
            get_cached_structured_output_mode,
            probe_structured_output_mode,
        )

        with patch(
            "app.modules.llm.adaptation.structured_output._request_probe",
            new=AsyncMock(
                return_value=SimpleNamespace(
                    content=(
                        '{"probe":{"value":"SCHEMA_OK","count":1,"score":1.0,'
                        '"enabled":true,"kind":"probe","tags":[]},"items":[]}'
                    ),
                ),
            ),
        ) as request_probe:
            async with self.session_factory() as session:
                mode = await probe_structured_output_mode(
                    session,
                    self.profile,
                    endpoint_kind="chat_completions",
                    thinking_extra_body=None,
                )
                cached = await get_cached_structured_output_mode(
                    session,
                    api_base_url="https://api.example.test/v1",
                    model_name="test-model",
                    endpoint_kind="chat_completions",
                )

        self.assertEqual(mode, "json_schema_strict")
        self.assertEqual(cached, "json_schema_strict")
        self.assertEqual(request_probe.await_count, 1)

    async def test_strict_probe_exercises_production_schema_features(self) -> None:
        from app.modules.llm.adaptation.structured_output import _request_probe

        with patch(
            "app.modules.llm.runtime._request_completion_endpoint",
            new=AsyncMock(return_value=ChatCompletionResult(content="{}")),
        ) as request_mock:
            await _request_probe(
                self.profile,
                endpoint_kind="chat_completions",
                thinking_extra_body=None,
                prompt="probe",
                max_tokens=24,
                mode="json_schema_strict",
            )

        internal_payload = request_mock.await_args.args[1]
        wire_payload = build_chat_completions_payload(internal_payload)
        schema = wire_payload["response_format"]["json_schema"]["schema"]
        self.assertIn("$defs", schema)
        self.assertEqual(
            schema["properties"]["probe"],
            {"$ref": "#/$defs/ProbeItem"},
        )
        self.assertEqual(
            schema["properties"]["items"]["items"],
            {"$ref": "#/$defs/ProbeItem"},
        )
        self.assertEqual(
            schema["$defs"]["ProbeItem"]["properties"]["count"]["type"], "integer"
        )
        self.assertEqual(
            schema["$defs"]["ProbeItem"]["properties"]["enabled"]["type"], "boolean"
        )
        self.assertEqual(
            schema["$defs"]["ProbeItem"]["properties"]["kind"]["enum"], ["probe"]
        )
        self.assertFalse(schema["additionalProperties"])
        self.assertFalse(schema["$defs"]["ProbeItem"]["additionalProperties"])

    async def test_deepseek_like_blank_conflict_and_positive_json_learns_json_object(
        self,
    ) -> None:
        from app.modules.llm.adaptation.structured_output import (
            probe_structured_output_mode,
        )

        strict_error = LLMRuntimeError(
            "This response_format type is unavailable now",
            status_code=400,
        )
        responses = [
            strict_error,
            SimpleNamespace(content="PLAIN"),
            SimpleNamespace(content="  "),
            SimpleNamespace(content='{"probe":"JSON_OK"}'),
        ]
        with patch(
            "app.modules.llm.adaptation.structured_output._request_probe",
            new=AsyncMock(side_effect=responses),
        ) as request_probe:
            async with self.session_factory() as session:
                mode = await probe_structured_output_mode(
                    session,
                    self.profile,
                    endpoint_kind="chat_completions",
                    thinking_extra_body={"thinking": {"type": "disabled"}},
                )

        self.assertEqual(mode, "json_object")
        self.assertEqual(request_probe.await_count, 4)

    async def test_silently_ignored_json_object_is_not_misclassified(self) -> None:
        from app.modules.llm.adaptation.structured_output import (
            probe_structured_output_mode,
        )

        responses = [
            SimpleNamespace(content="PLAIN"),
            SimpleNamespace(content="PLAIN"),
            SimpleNamespace(content="PLAIN"),
        ]
        with patch(
            "app.modules.llm.adaptation.structured_output._request_probe",
            new=AsyncMock(side_effect=responses),
        ):
            async with self.session_factory() as session:
                mode = await probe_structured_output_mode(
                    session,
                    self.profile,
                    endpoint_kind="chat_completions",
                    thinking_extra_body=None,
                )

        self.assertEqual(mode, "prompt_only")

    async def test_rate_limit_is_not_cached_as_unsupported(self) -> None:
        from app.models import LLMStructuredOutputAdaptationCache
        from app.modules.llm.adaptation.structured_output import (
            probe_structured_output_mode,
        )
        from sqlalchemy import select

        error = LLMRuntimeError("rate limited", status_code=429)
        with patch(
            "app.modules.llm.adaptation.structured_output._request_probe",
            new=AsyncMock(side_effect=error),
        ):
            async with self.session_factory() as session:
                with self.assertRaises(LLMRuntimeError):
                    await probe_structured_output_mode(
                        session,
                        self.profile,
                        endpoint_kind="chat_completions",
                        thinking_extra_body=None,
                    )
                row = await session.scalar(select(LLMStructuredOutputAdaptationCache))

        self.assertIsNone(row)

    async def test_conditional_invalidation_keeps_newer_mode(self) -> None:
        from app.modules.llm.adaptation.structured_output import (
            get_cached_structured_output_mode,
            invalidate_structured_output_adaptation,
            record_structured_output_adaptation,
        )

        async with self.session_factory() as session:
            await record_structured_output_adaptation(
                session,
                api_base_url="https://api.example.test/v1",
                model_name="test-model",
                endpoint_kind="chat_completions",
                learned_mode="json_object",
            )
            deleted = await invalidate_structured_output_adaptation(
                session,
                api_base_url="https://api.example.test/v1",
                model_name="test-model",
                endpoint_kind="chat_completions",
                expected_mode="json_schema_strict",
            )
            cached = await get_cached_structured_output_mode(
                session,
                api_base_url="https://api.example.test/v1",
                model_name="test-model",
                endpoint_kind="chat_completions",
            )

        self.assertFalse(deleted)
        self.assertEqual(cached, "json_object")

    async def test_prompt_only_negative_cache_expires(self) -> None:
        from datetime import timedelta

        from sqlalchemy import select

        from app.core.time import utc_now
        from app.models import LLMStructuredOutputAdaptationCache
        from app.modules.llm.adaptation.structured_output import (
            get_cached_structured_output_mode,
            record_structured_output_adaptation,
        )

        async with self.session_factory() as session:
            await record_structured_output_adaptation(
                session,
                api_base_url=self.profile.api_base_url or "",
                model_name=self.profile.model_name,
                endpoint_kind="chat_completions",
                learned_mode="prompt_only",
            )
            row = await session.scalar(select(LLMStructuredOutputAdaptationCache))
            assert row is not None
            row.expires_at = utc_now() - timedelta(seconds=1)
            await session.flush()
            cached = await get_cached_structured_output_mode(
                session,
                api_base_url=self.profile.api_base_url or "",
                model_name=self.profile.model_name,
                endpoint_kind="chat_completions",
            )

        self.assertIsNone(cached)

    async def test_concurrent_cache_misses_share_one_probe(self) -> None:
        from app.modules.llm.adaptation import (
            structured_output as structured_output_adaptation,
        )

        probe_started = asyncio.Event()
        release_probe = asyncio.Event()
        probe_count = 0

        async def probe(*args, **kwargs):
            nonlocal probe_count
            probe_count += 1
            probe_started.set()
            await release_probe.wait()
            return "json_object"

        async def ensure_from_own_session() -> str:
            async with self.session_factory() as session:
                return await structured_output_adaptation.ensure_structured_output_adaptation(
                    session,
                    self.profile,
                    endpoint_kind="chat_completions",
                    thinking_extra_body=None,
                )

        structured_output_adaptation._structured_output_adaptation_locks.clear()
        try:
            with patch(
                "app.modules.llm.adaptation.structured_output.probe_structured_output_mode",
                side_effect=probe,
            ):
                first = asyncio.create_task(ensure_from_own_session())
                await probe_started.wait()
                second = asyncio.create_task(ensure_from_own_session())
                for _ in range(100):
                    state = structured_output_adaptation._structured_output_adaptation_locks.get(
                        (
                            "https://api.example.test/v1",
                            self.profile.model_name,
                            "chat_completions",
                            structured_output_adaptation.STRUCTURED_OUTPUT_PROBE_VERSION,
                        ),
                    )
                    if state is not None and state.users == 2:
                        break
                    await asyncio.sleep(0.01)
                else:
                    self.fail("第二个会话未进入结构化输出能力协调锁")
                release_probe.set()
                first_mode, second_mode = await asyncio.gather(first, second)
        finally:
            structured_output_adaptation._structured_output_adaptation_locks.clear()

        self.assertEqual(probe_count, 1)
        self.assertEqual(first_mode, "json_object")
        self.assertEqual(second_mode, "json_object")

    async def test_request_uses_cached_strict_schema_and_parses_exact_json(
        self,
    ) -> None:
        from app.modules.llm.adaptation.structured_output import (
            record_structured_output_adaptation,
        )

        adaptation = LLMRuntimeAdaptation(
            endpoint_kind="chat_completions",
            thinking_extra_body=None,
        )
        async with self.session_factory() as session:
            await record_structured_output_adaptation(
                session,
                api_base_url=self.profile.api_base_url or "",
                model_name=self.profile.model_name,
                endpoint_kind="chat_completions",
                learned_mode="json_schema_strict",
            )
            with patch(
                "app.modules.llm.runtime.request_chat_completion",
                new=AsyncMock(
                    return_value=ChatCompletionResult(content='{"value":"ok"}')
                ),
            ) as request_mock:
                _completion, result, mode = await request_structured_completion(
                    self.profile,
                    {"model": self.profile.model_name, "messages": []},
                    _StructuredResult,
                    session=session,
                    adaptation=adaptation,
                )

        sent_payload = request_mock.await_args.args[1]
        wire_payload = build_chat_completions_payload(sent_payload)
        self.assertEqual(mode, "json_schema_strict")
        self.assertEqual(result.value, "ok")
        self.assertEqual(wire_payload["response_format"]["type"], "json_schema")
        self.assertTrue(wire_payload["response_format"]["json_schema"]["strict"])
        self.assertNotIn(
            '"title"',
            json.dumps(wire_payload["response_format"]["json_schema"]["schema"]),
        )

    async def test_strict_mode_output_violation_invalidates_cached_capability(
        self,
    ) -> None:
        from app.modules.llm.adaptation.structured_output import (
            get_cached_structured_output_mode,
            record_structured_output_adaptation,
        )

        adaptation = LLMRuntimeAdaptation("chat_completions", None)
        async with self.session_factory() as session:
            await record_structured_output_adaptation(
                session,
                api_base_url=self.profile.api_base_url or "",
                model_name=self.profile.model_name,
                endpoint_kind="chat_completions",
                learned_mode="json_schema_strict",
            )
            with patch(
                "app.modules.llm.runtime.request_chat_completion",
                new=AsyncMock(
                    return_value=ChatCompletionResult(
                        content='prefix {"value":"ok"}',
                    ),
                ),
            ):
                with self.assertRaisesRegex(LLMRuntimeError, "JSON 结构无效"):
                    await request_structured_completion(
                        self.profile,
                        {"model": self.profile.model_name, "messages": []},
                        _StructuredResult,
                        session=session,
                        adaptation=adaptation,
                    )
            cached = await get_cached_structured_output_mode(
                session,
                api_base_url=self.profile.api_base_url or "",
                model_name=self.profile.model_name,
                endpoint_kind="chat_completions",
            )

        self.assertIsNone(cached)

    async def test_json_object_semantic_failure_does_not_invalidate_protocol(
        self,
    ) -> None:
        from app.modules.llm.adaptation.structured_output import (
            get_cached_structured_output_mode,
            record_structured_output_adaptation,
        )

        adaptation = LLMRuntimeAdaptation("chat_completions", None)
        async with self.session_factory() as session:
            await record_structured_output_adaptation(
                session,
                api_base_url=self.profile.api_base_url or "",
                model_name=self.profile.model_name,
                endpoint_kind="chat_completions",
                learned_mode="json_object",
            )
            with patch(
                "app.modules.llm.runtime.request_chat_completion",
                new=AsyncMock(
                    return_value=ChatCompletionResult(content='{"other":"x"}')
                ),
            ):
                with self.assertRaisesRegex(LLMRuntimeError, "JSON 结构无效"):
                    await request_structured_completion(
                        self.profile,
                        {"model": self.profile.model_name, "messages": []},
                        _StructuredResult,
                        session=session,
                        adaptation=adaptation,
                    )
            cached = await get_cached_structured_output_mode(
                session,
                api_base_url=self.profile.api_base_url or "",
                model_name=self.profile.model_name,
                endpoint_kind="chat_completions",
            )

        self.assertEqual(cached, "json_object")

    async def test_explicit_protocol_rejection_invalidates_cached_mode(self) -> None:
        from app.modules.llm.adaptation.structured_output import (
            get_cached_structured_output_mode,
            record_structured_output_adaptation,
        )

        adaptation = LLMRuntimeAdaptation("chat_completions", None)
        async with self.session_factory() as session:
            await record_structured_output_adaptation(
                session,
                api_base_url=self.profile.api_base_url or "",
                model_name=self.profile.model_name,
                endpoint_kind="chat_completions",
                learned_mode="json_schema_strict",
            )
            error = LLMRuntimeError(
                "response_format json_schema is not supported",
                status_code=400,
            )
            with patch(
                "app.modules.llm.runtime.request_chat_completion",
                new=AsyncMock(side_effect=error),
            ):
                with self.assertRaises(LLMRuntimeError):
                    await request_structured_completion(
                        self.profile,
                        {"model": self.profile.model_name, "messages": []},
                        _StructuredResult,
                        session=session,
                        adaptation=adaptation,
                    )
            cached = await get_cached_structured_output_mode(
                session,
                api_base_url=self.profile.api_base_url or "",
                model_name=self.profile.model_name,
                endpoint_kind="chat_completions",
            )

        self.assertIsNone(cached)

    async def test_unrelated_bad_request_does_not_invalidate_cached_mode(self) -> None:
        from app.modules.llm.adaptation.structured_output import (
            get_cached_structured_output_mode,
            record_structured_output_adaptation,
        )

        adaptation = LLMRuntimeAdaptation("chat_completions", None)
        async with self.session_factory() as session:
            await record_structured_output_adaptation(
                session,
                api_base_url=self.profile.api_base_url or "",
                model_name=self.profile.model_name,
                endpoint_kind="chat_completions",
                learned_mode="json_schema_strict",
            )
            error = LLMRuntimeError("max_tokens is invalid", status_code=400)
            with patch(
                "app.modules.llm.runtime.request_chat_completion",
                new=AsyncMock(side_effect=error),
            ):
                with self.assertRaises(LLMRuntimeError):
                    await request_structured_completion(
                        self.profile,
                        {"model": self.profile.model_name, "messages": []},
                        _StructuredResult,
                        session=session,
                        adaptation=adaptation,
                    )
            cached = await get_cached_structured_output_mode(
                session,
                api_base_url=self.profile.api_base_url or "",
                model_name=self.profile.model_name,
                endpoint_kind="chat_completions",
            )

        self.assertEqual(cached, "json_schema_strict")


if __name__ == "__main__":
    unittest.main()
