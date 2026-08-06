from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import httpx
from openai import APIResponseValidationError, APIStatusError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models import LLMProfile
from app.modules.llm.adaptation.endpoint import get_cached_endpoint_kind, record_endpoint_adaptation
from app.modules.llm.runtime import LLMRuntimeAdaptation
from test.schema_database import create_schema_sqlite_database


class CrawlerLLMEndpointRetryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        fd, self.db_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        create_schema_sqlite_database(Path(self.db_path))
        self.engine = create_async_engine(f"sqlite+aiosqlite:///{Path(self.db_path).as_posix()}")
        self.session_factory = async_sessionmaker(self.engine, expire_on_commit=False)
        self.profile = LLMProfile(
            name="relay",
            provider="openai",
            api_base_url="https://relay.example/v1",
            api_key="sk-test",
            model_name="relay-model",
        )
        self.chat_adaptation = LLMRuntimeAdaptation("chat_completions", None)
        self.responses_adaptation = LLMRuntimeAdaptation("responses", {"enable_thinking": False})

    async def asyncTearDown(self) -> None:
        await self.engine.dispose()
        try:
            os.unlink(self.db_path)
        except FileNotFoundError:
            pass

    async def test_protocol_status_error_relearns_persists_rebuilds_and_retries_once(self) -> None:
        from app.modules.crawler.llm.endpoint_retry import invoke_crawler_llm_with_endpoint_retry

        request = httpx.Request("POST", "https://relay.example/v1/chat/completions")
        models = [
            SimpleNamespace(ainvoke=AsyncMock(side_effect=APIStatusError("not found", response=httpx.Response(404, request=request), body=None))),
            SimpleNamespace(ainvoke=AsyncMock(return_value="ok")),
        ]
        build_model = Mock(side_effect=models)

        async def relearn(session, profile, *, failed_endpoint_kind):
            self.assertEqual(failed_endpoint_kind, "chat_completions")
            await record_endpoint_adaptation(
                session,
                api_base_url=profile.api_base_url,
                model_name=profile.model_name,
                endpoint_kind="responses",
            )
            return self.responses_adaptation

        with (
            patch(
                "app.modules.crawler.llm.endpoint_retry.invalidate_endpoint_adaptation",
                new=AsyncMock(return_value=True),
            ) as invalidate,
            patch(
                "app.modules.crawler.llm.endpoint_retry.ensure_llm_runtime_adaptation",
                new=AsyncMock(side_effect=relearn),
            ) as ensure,
        ):
            response, adaptation = await invoke_crawler_llm_with_endpoint_retry(
                self.session_factory,
                self.profile,
                self.chat_adaptation,
                prompt="hello",
                build_model=build_model,
            )

        self.assertEqual(response, "ok")
        self.assertIs(adaptation, self.responses_adaptation)
        self.assertEqual(build_model.call_count, 2)
        self.assertEqual(models[0].ainvoke.await_count, 1)
        self.assertEqual(models[1].ainvoke.await_count, 1)
        invalidate.assert_awaited_once()
        ensure.assert_awaited_once()
        async with self.session_factory() as session:
            self.assertEqual(
                await get_cached_endpoint_kind(
                    session,
                    api_base_url=self.profile.api_base_url,
                    model_name=self.profile.model_name,
                ),
                "responses",
            )

    async def test_response_validation_error_relearns_and_retries_once(self) -> None:
        from app.modules.crawler.llm.endpoint_retry import invoke_crawler_llm_with_endpoint_retry

        request = httpx.Request("POST", "https://relay.example/v1/chat/completions")
        models = [
            SimpleNamespace(
                ainvoke=AsyncMock(
                    side_effect=APIResponseValidationError(
                        httpx.Response(200, request=request),
                        body={"output": []},
                    ),
                ),
            ),
            SimpleNamespace(ainvoke=AsyncMock(return_value="ok")),
        ]
        build_model = Mock(side_effect=models)

        with (
            patch("app.modules.crawler.llm.endpoint_retry.invalidate_endpoint_adaptation", new=AsyncMock(return_value=True)) as invalidate,
            patch("app.modules.crawler.llm.endpoint_retry.ensure_llm_runtime_adaptation", new=AsyncMock(return_value=self.responses_adaptation)) as ensure,
        ):
            response, adaptation = await invoke_crawler_llm_with_endpoint_retry(
                self.session_factory,
                self.profile,
                self.chat_adaptation,
                prompt="hello",
                build_model=build_model,
            )

        self.assertEqual(response, "ok")
        self.assertIs(adaptation, self.responses_adaptation)
        self.assertEqual(build_model.call_count, 2)
        invalidate.assert_awaited_once()
        ensure.assert_awaited_once()

    async def test_relearn_failure_keeps_failed_endpoint_cache_invalidated(self) -> None:
        from app.modules.crawler.llm.endpoint_retry import invoke_crawler_llm_with_endpoint_retry

        await self._record_endpoint_kind("chat_completions")
        request = httpx.Request("POST", "https://relay.example/v1/chat/completions")
        model = SimpleNamespace(
            ainvoke=AsyncMock(
                side_effect=APIStatusError(
                    "not found",
                    response=httpx.Response(404, request=request),
                    body=None,
                ),
            ),
        )

        with (
            patch(
                "app.modules.crawler.llm.endpoint_retry.ensure_llm_runtime_adaptation",
                new=AsyncMock(side_effect=RuntimeError("reprobe failed")),
            ),
            self.assertRaisesRegex(RuntimeError, "reprobe failed"),
        ):
            await invoke_crawler_llm_with_endpoint_retry(
                self.session_factory,
                self.profile,
                self.chat_adaptation,
                prompt="hello",
                build_model=Mock(return_value=model),
            )

        async with self.session_factory() as session:
            self.assertIsNone(
                await get_cached_endpoint_kind(
                    session,
                    api_base_url=self.profile.api_base_url,
                    model_name=self.profile.model_name,
                ),
            )

    async def test_second_protocol_error_is_not_retried_a_third_time(self) -> None:
        from app.modules.crawler.llm.endpoint_retry import invoke_crawler_llm_with_endpoint_retry

        request = httpx.Request("POST", "https://relay.example/v1/chat/completions")
        error = APIStatusError("not found", response=httpx.Response(404, request=request), body=None)
        models = [
            SimpleNamespace(ainvoke=AsyncMock(side_effect=error)),
            SimpleNamespace(ainvoke=AsyncMock(side_effect=error)),
        ]
        build_model = Mock(side_effect=models)

        with (
            patch("app.modules.crawler.llm.endpoint_retry.invalidate_endpoint_adaptation", new=AsyncMock(return_value=True)) as invalidate,
            patch("app.modules.crawler.llm.endpoint_retry.ensure_llm_runtime_adaptation", new=AsyncMock(return_value=self.responses_adaptation)) as ensure,
            self.assertRaises(APIStatusError),
        ):
            await invoke_crawler_llm_with_endpoint_retry(
                self.session_factory,
                self.profile,
                self.chat_adaptation,
                prompt="hello",
                build_model=build_model,
            )

        self.assertEqual(build_model.call_count, 2)
        self.assertEqual(invalidate.await_count, 2)
        self.assertEqual(
            [call.kwargs["failed_endpoint_kind"] for call in invalidate.await_args_list],
            ["chat_completions", "responses"],
        )
        ensure.assert_awaited_once()

    async def test_second_protocol_error_invalidates_retry_endpoint_before_raising(self) -> None:
        from app.modules.crawler.llm.endpoint_retry import invoke_crawler_llm_with_endpoint_retry

        await self._record_endpoint_kind("chat_completions")
        request = httpx.Request("POST", "https://relay.example/v1/chat/completions")
        error = APIStatusError("not found", response=httpx.Response(404, request=request), body=None)
        models = [
            SimpleNamespace(ainvoke=AsyncMock(side_effect=error)),
            SimpleNamespace(ainvoke=AsyncMock(side_effect=error)),
        ]

        async def relearn(session, profile, *, failed_endpoint_kind):
            self.assertEqual(failed_endpoint_kind, "chat_completions")
            await record_endpoint_adaptation(
                session,
                api_base_url=profile.api_base_url,
                model_name=profile.model_name,
                endpoint_kind="responses",
            )
            return self.responses_adaptation

        with (
            patch(
                "app.modules.crawler.llm.endpoint_retry.ensure_llm_runtime_adaptation",
                new=AsyncMock(side_effect=relearn),
            ),
            self.assertRaises(APIStatusError),
        ):
            await invoke_crawler_llm_with_endpoint_retry(
                self.session_factory,
                self.profile,
                self.chat_adaptation,
                prompt="hello",
                build_model=Mock(side_effect=models),
            )

        async with self.session_factory() as session:
            self.assertIsNone(
                await get_cached_endpoint_kind(
                    session,
                    api_base_url=self.profile.api_base_url,
                    model_name=self.profile.model_name,
                ),
            )

    async def test_auth_rate_limit_and_server_errors_do_not_relearn(self) -> None:
        from app.modules.crawler.llm.endpoint_retry import invoke_crawler_llm_with_endpoint_retry

        request = httpx.Request("POST", "https://relay.example/v1/chat/completions")
        for status_code in (401, 429, 500):
            with self.subTest(status_code=status_code):
                model = SimpleNamespace(
                    ainvoke=AsyncMock(
                        side_effect=APIStatusError(
                            "upstream failure",
                            response=httpx.Response(status_code, request=request),
                            body=None,
                        ),
                    ),
                )
                build_model = Mock(return_value=model)
                with (
                    patch("app.modules.crawler.llm.endpoint_retry.invalidate_endpoint_adaptation", new=AsyncMock()) as invalidate,
                    patch("app.modules.crawler.llm.endpoint_retry.ensure_llm_runtime_adaptation", new=AsyncMock()) as ensure,
                    self.assertRaises(APIStatusError),
                ):
                    await invoke_crawler_llm_with_endpoint_retry(
                        self.session_factory,
                        self.profile,
                        self.chat_adaptation,
                        prompt="hello",
                        build_model=build_model,
                    )
                self.assertEqual(build_model.call_count, 1)
                invalidate.assert_not_awaited()
                ensure.assert_not_awaited()

    async def _record_endpoint_kind(self, endpoint_kind: str) -> None:
        async with self.session_factory() as session:
            await record_endpoint_adaptation(
                session,
                api_base_url=self.profile.api_base_url,
                model_name=self.profile.model_name,
                endpoint_kind=endpoint_kind,  # type: ignore[arg-type]
            )
            await session.commit()


if __name__ == "__main__":
    unittest.main()
