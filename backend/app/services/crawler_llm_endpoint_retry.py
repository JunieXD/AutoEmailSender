from __future__ import annotations

"""Endpoint adaptation retry for crawler paths that call ``ChatOpenAI`` directly."""

from collections.abc import Callable
from typing import Any

from openai import APIResponseValidationError, APIStatusError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models import LLMProfile
from app.services.llm_endpoint_adaptation import invalidate_endpoint_adaptation
from app.services.llm_runtime import (
    LLMRuntimeAdaptation,
    ensure_llm_runtime_adaptation,
    resolve_base_url,
)


def is_crawler_endpoint_protocol_error(exc: BaseException) -> bool:
    """Return whether a direct ChatOpenAI failure signals a wrong endpoint."""

    if isinstance(exc, APIResponseValidationError):
        return True
    return isinstance(exc, APIStatusError) and exc.status_code in {404, 405, 501}


async def invoke_crawler_llm_with_endpoint_retry(
    session_factory: async_sessionmaker[AsyncSession],
    llm_profile: LLMProfile,
    adaptation: LLMRuntimeAdaptation,
    *,
    prompt: str,
    build_model: Callable[..., Any],
) -> tuple[Any, LLMRuntimeAdaptation]:
    """Invoke a direct crawler model, relearning its endpoint at most once."""

    model = build_model(llm_profile, adaptation=adaptation)
    try:
        return await model.ainvoke(prompt), adaptation
    except Exception as exc:
        if not is_crawler_endpoint_protocol_error(exc):
            raise

    async with session_factory() as session:
        await invalidate_endpoint_adaptation(
            session,
            api_base_url=resolve_base_url(llm_profile.api_base_url),
            model_name=llm_profile.model_name,
            failed_endpoint_kind=adaptation.endpoint_kind,
        )
        await session.commit()

    async with session_factory() as session:
        retry_adaptation = await ensure_llm_runtime_adaptation(
            session,
            llm_profile,
            failed_endpoint_kind=adaptation.endpoint_kind,
        )
        await session.commit()

    retry_model = build_model(llm_profile, adaptation=retry_adaptation)
    try:
        return await retry_model.ainvoke(prompt), retry_adaptation
    except Exception as exc:
        if not is_crawler_endpoint_protocol_error(exc):
            raise
        async with session_factory() as session:
            await invalidate_endpoint_adaptation(
                session,
                api_base_url=resolve_base_url(llm_profile.api_base_url),
                model_name=llm_profile.model_name,
                failed_endpoint_kind=retry_adaptation.endpoint_kind,
            )
            await session.commit()
        raise
