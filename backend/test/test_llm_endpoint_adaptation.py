from __future__ import annotations

import asyncio
import unittest

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool


class ResponseEnvelopeClassificationTests(unittest.TestCase):
    def test_chat_completions_envelope_is_valid_for_chat(self) -> None:
        from app.modules.llm.adaptation.endpoint import classify_response_envelope

        result = classify_response_envelope(
            "chat_completions",
            {"choices": [{"message": {"role": "assistant", "content": "ok"}}]},
        )

        self.assertEqual(result, "valid")

    def test_responses_envelope_is_other_endpoint_for_chat(self) -> None:
        from app.modules.llm.adaptation.endpoint import classify_response_envelope

        result = classify_response_envelope("chat_completions", {"output": []})

        self.assertEqual(result, "other_endpoint")

    def test_target_protocol_wins_when_envelopes_overlap(self) -> None:
        from app.modules.llm.adaptation.endpoint import classify_response_envelope

        result = classify_response_envelope(
            "responses",
            {
                "choices": [{"message": {"role": "assistant"}}],
                "output_text": "ok",
            },
        )

        self.assertEqual(result, "valid")

    def test_invalid_envelope_is_invalid(self) -> None:
        from app.modules.llm.adaptation.endpoint import classify_response_envelope

        self.assertEqual(
            classify_response_envelope("chat_completions", {"choices": []}),
            "invalid",
        )

    def test_chat_completions_rejects_malformed_choice_entries(self) -> None:
        from app.modules.llm.adaptation.endpoint import classify_response_envelope

        malformed_envelopes = (
            {"choices": ["not-a-choice"]},
            {"choices": [{}]},
            {"choices": [{"message": "not-a-message"}]},
        )

        for envelope in malformed_envelopes:
            with self.subTest(envelope=envelope):
                self.assertEqual(
                    classify_response_envelope("chat_completions", envelope),
                    "invalid",
                )
        self.assertEqual(
            classify_response_envelope("responses", {"output_text": None}),
            "invalid",
        )


class EndpointCandidateTests(unittest.TestCase):
    def test_candidates_prefer_chat_by_default_and_fallback_from_failure(self) -> None:
        from app.modules.llm.adaptation.endpoint import endpoint_candidates

        self.assertEqual(endpoint_candidates(), ("chat_completions", "responses"))
        self.assertEqual(
            endpoint_candidates("chat_completions"),
            ("responses", "chat_completions"),
        )
        self.assertEqual(
            endpoint_candidates("responses"),
            ("chat_completions", "responses"),
        )


class EndpointAdaptationCacheTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        from app.models import Base

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

    async def asyncTearDown(self) -> None:
        await self.engine.dispose()

    async def test_cache_miss_and_normalized_url_hit(self) -> None:
        from app.modules.llm.adaptation.endpoint import (
            get_cached_endpoint_kind,
            record_endpoint_adaptation,
        )

        async with self.session_factory() as session:
            self.assertIsNone(
                await get_cached_endpoint_kind(
                    session,
                    api_base_url="https://api.example.test/v1",
                    model_name="example-model",
                )
            )
            await record_endpoint_adaptation(
                session,
                api_base_url=" https://api.example.test/v1/ ",
                model_name="example-model",
                endpoint_kind="responses",
            )
            await session.commit()

        async with self.session_factory() as session:
            cached = await get_cached_endpoint_kind(
                session,
                api_base_url="https://api.example.test/v1",
                model_name="example-model",
            )
        self.assertEqual(cached, "responses")

    async def test_cache_is_scoped_by_base_url_and_model_name(self) -> None:
        from app.modules.llm.adaptation.endpoint import (
            get_cached_endpoint_kind,
            record_endpoint_adaptation,
        )

        first_base_url = "https://first.example.test/v1"
        second_base_url = "https://second.example.test/v1"
        first_model_name = "first-model"
        second_model_name = "second-model"

        async with self.session_factory() as session:
            await record_endpoint_adaptation(
                session,
                api_base_url=first_base_url,
                model_name=first_model_name,
                endpoint_kind="chat_completions",
            )
            await record_endpoint_adaptation(
                session,
                api_base_url=first_base_url,
                model_name=second_model_name,
                endpoint_kind="responses",
            )
            await record_endpoint_adaptation(
                session,
                api_base_url=second_base_url,
                model_name=first_model_name,
                endpoint_kind="responses",
            )
            await session.commit()

        async with self.session_factory() as session:
            self.assertEqual(
                await get_cached_endpoint_kind(
                    session,
                    api_base_url=first_base_url,
                    model_name=first_model_name,
                ),
                "chat_completions",
            )
            self.assertEqual(
                await get_cached_endpoint_kind(
                    session,
                    api_base_url=first_base_url,
                    model_name=second_model_name,
                ),
                "responses",
            )
            self.assertEqual(
                await get_cached_endpoint_kind(
                    session,
                    api_base_url=second_base_url,
                    model_name=first_model_name,
                ),
                "responses",
            )

    async def test_upsert_updates_one_row_and_loaded_instance(self) -> None:
        from app.models import LLMEndpointAdaptationCache
        from app.modules.llm.adaptation.endpoint import (
            get_cached_endpoint_kind,
            record_endpoint_adaptation,
        )

        async with self.session_factory() as session:
            await record_endpoint_adaptation(
                session,
                api_base_url="https://api.example.test/v1",
                model_name="example-model",
                endpoint_kind="chat_completions",
            )
            await session.commit()

            loaded = await session.scalar(select(LLMEndpointAdaptationCache))
            self.assertIsNotNone(loaded)
            await record_endpoint_adaptation(
                session,
                api_base_url="https://api.example.test/v1",
                model_name="example-model",
                endpoint_kind="responses",
            )

            self.assertEqual(loaded.learned_endpoint_kind, "responses")
            self.assertEqual(
                await get_cached_endpoint_kind(
                    session,
                    api_base_url="https://api.example.test/v1",
                    model_name="example-model",
                ),
                "responses",
            )
            await session.commit()

        async with self.session_factory() as session:
            row_count = await session.scalar(
                select(func.count()).select_from(LLMEndpointAdaptationCache)
            )
        self.assertEqual(row_count, 1)

    async def test_invalidate_deletes_only_the_endpoint_that_failed(self) -> None:
        from app.modules.llm.adaptation.endpoint import (
            get_cached_endpoint_kind,
            invalidate_endpoint_adaptation,
            record_endpoint_adaptation,
        )

        async with self.session_factory() as session:
            await record_endpoint_adaptation(
                session,
                api_base_url="https://api.example.test/v1",
                model_name="example-model",
                endpoint_kind="responses",
            )
            self.assertFalse(
                await invalidate_endpoint_adaptation(
                    session,
                    api_base_url="https://api.example.test/v1",
                    model_name="example-model",
                    failed_endpoint_kind="chat_completions",
                )
            )
            self.assertTrue(
                await invalidate_endpoint_adaptation(
                    session,
                    api_base_url="https://api.example.test/v1",
                    model_name="example-model",
                    failed_endpoint_kind="responses",
                )
            )
            self.assertIsNone(
                await get_cached_endpoint_kind(
                    session,
                    api_base_url="https://api.example.test/v1",
                    model_name="example-model",
                )
            )

    async def test_concurrent_writes_keep_one_cache_row(self) -> None:
        from app.models import LLMEndpointAdaptationCache
        from app.modules.llm.adaptation.endpoint import record_endpoint_adaptation

        async def record(endpoint_kind: str) -> None:
            async with self.session_factory() as session:
                await record_endpoint_adaptation(
                    session,
                    api_base_url="https://api.concurrent.test/v1",
                    model_name="concurrent-model",
                    endpoint_kind=endpoint_kind,  # type: ignore[arg-type]
                )
                await session.commit()

        await asyncio.gather(record("chat_completions"), record("responses"))

        async with self.session_factory() as session:
            row_count = await session.scalar(
                select(func.count()).select_from(LLMEndpointAdaptationCache)
            )
        self.assertEqual(row_count, 1)


class EndpointAdaptationLockTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        from app.modules.llm.adaptation import endpoint as llm_endpoint_adaptation

        llm_endpoint_adaptation._endpoint_adaptation_locks.clear()

    async def asyncTearDown(self) -> None:
        from app.modules.llm.adaptation import endpoint as llm_endpoint_adaptation

        llm_endpoint_adaptation._endpoint_adaptation_locks.clear()

    async def test_same_key_is_serialized(self) -> None:
        from app.modules.llm.adaptation.endpoint import endpoint_adaptation_lock

        entered_first = asyncio.Event()
        release_first = asyncio.Event()
        order: list[str] = []

        async def first() -> None:
            async with endpoint_adaptation_lock("https://api.example.test/v1", "model"):
                order.append("first")
                entered_first.set()
                await release_first.wait()

        async def second() -> None:
            await entered_first.wait()
            async with endpoint_adaptation_lock("https://api.example.test/v1", "model"):
                order.append("second")

        first_task = asyncio.create_task(first())
        await entered_first.wait()
        second_task = asyncio.create_task(second())
        await asyncio.sleep(0)
        self.assertEqual(order, ["first"])
        release_first.set()
        await asyncio.gather(first_task, second_task)
        self.assertEqual(order, ["first", "second"])

    async def test_different_keys_can_proceed_in_parallel(self) -> None:
        from app.modules.llm.adaptation.endpoint import endpoint_adaptation_lock

        first_entered = asyncio.Event()
        second_entered = asyncio.Event()
        release = asyncio.Event()

        async def hold_first() -> None:
            async with endpoint_adaptation_lock("https://api.example.test/v1", "model-a"):
                first_entered.set()
                await release.wait()

        async def enter_second() -> None:
            await first_entered.wait()
            async with endpoint_adaptation_lock("https://api.example.test/v1", "model-b"):
                second_entered.set()

        first_task = asyncio.create_task(hold_first())
        second_task = asyncio.create_task(enter_second())
        await asyncio.wait_for(second_entered.wait(), timeout=1)
        release.set()
        await asyncio.gather(first_task, second_task)

    async def test_registry_entry_is_removed_after_last_waiter_leaves(self) -> None:
        from app.modules.llm.adaptation.endpoint import (
            _endpoint_adaptation_locks,
            endpoint_adaptation_lock,
        )

        key = ("https://api.example.test/v1", "model")
        async with endpoint_adaptation_lock(*key):
            self.assertIn(key, _endpoint_adaptation_locks)
        self.assertNotIn(key, _endpoint_adaptation_locks)

    async def test_registry_entry_survives_holder_exit_until_waiter_leaves(self) -> None:
        from app.modules.llm.adaptation.endpoint import (
            _endpoint_adaptation_locks,
            endpoint_adaptation_lock,
        )

        key = ("https://api.example.test/v1", "model")
        holder_entered = asyncio.Event()
        release_holder = asyncio.Event()
        waiter_entered = asyncio.Event()
        release_waiter = asyncio.Event()

        async def holder() -> None:
            async with endpoint_adaptation_lock(*key):
                holder_entered.set()
                await release_holder.wait()

        async def waiter() -> None:
            await holder_entered.wait()
            async with endpoint_adaptation_lock(*key):
                waiter_entered.set()
                await release_waiter.wait()

        holder_task = asyncio.create_task(holder())
        await holder_entered.wait()
        waiter_task = asyncio.create_task(waiter())

        for _ in range(10):
            state = _endpoint_adaptation_locks.get(key)
            if state is not None and state.users == 2:
                break
            await asyncio.sleep(0)
        else:
            self.fail("等待者未在持锁者释放前注册")

        release_holder.set()
        await holder_task
        await asyncio.wait_for(waiter_entered.wait(), timeout=1)
        self.assertIn(key, _endpoint_adaptation_locks)
        self.assertIs(_endpoint_adaptation_locks[key], state)

        release_waiter.set()
        await waiter_task
        self.assertNotIn(key, _endpoint_adaptation_locks)

    async def test_cancelled_waiter_decrements_users_and_cleans_up_registry(self) -> None:
        from app.modules.llm.adaptation.endpoint import (
            _endpoint_adaptation_locks,
            endpoint_adaptation_lock,
        )

        key = ("https://api.example.test/v1", "model")
        holder_entered = asyncio.Event()
        release_holder = asyncio.Event()
        waiter_started = asyncio.Event()

        async def holder() -> None:
            async with endpoint_adaptation_lock(*key):
                holder_entered.set()
                await release_holder.wait()

        async def waiter() -> None:
            await holder_entered.wait()
            waiter_started.set()
            async with endpoint_adaptation_lock(*key):
                self.fail("已取消的等待者不应获得锁")

        holder_task = asyncio.create_task(holder())
        await holder_entered.wait()
        waiter_task = asyncio.create_task(waiter())
        await waiter_started.wait()

        state = _endpoint_adaptation_locks[key]
        self.assertTrue(state.lock.locked())
        self.assertEqual(state.users, 2)

        waiter_task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await waiter_task

        self.assertIs(_endpoint_adaptation_locks[key], state)
        self.assertEqual(state.users, 1)
        release_holder.set()
        await holder_task
        self.assertNotIn(key, _endpoint_adaptation_locks)

    async def test_cancelled_holder_releases_lock_and_allows_reacquisition(self) -> None:
        from app.modules.llm.adaptation.endpoint import (
            _endpoint_adaptation_locks,
            endpoint_adaptation_lock,
        )

        key = ("https://api.example.test/v1", "model")
        holder_entered = asyncio.Event()
        keep_holder_waiting = asyncio.Event()

        async def holder() -> None:
            async with endpoint_adaptation_lock(*key):
                holder_entered.set()
                await keep_holder_waiting.wait()

        holder_task = asyncio.create_task(holder())
        await holder_entered.wait()
        self.assertTrue(_endpoint_adaptation_locks[key].lock.locked())

        holder_task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await holder_task

        self.assertNotIn(key, _endpoint_adaptation_locks)
        async with endpoint_adaptation_lock(*key):
            self.assertTrue(_endpoint_adaptation_locks[key].lock.locked())
        self.assertNotIn(key, _endpoint_adaptation_locks)
