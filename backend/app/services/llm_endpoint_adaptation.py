from __future__ import annotations

"""Cache and coordination primitives for adapting LLM endpoint protocols."""

import asyncio
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Literal, cast

from sqlalchemy import delete, select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import set_committed_value

from app.core.time import utc_now
from app.models import LLMEndpointAdaptationCache
from app.services.llm_runtime import resolve_base_url


EndpointKind = Literal["chat_completions", "responses"]
ResponseEnvelopeClassification = Literal["valid", "other_endpoint", "invalid"]


def _is_chat_completions_envelope(data: object) -> bool:
    if not isinstance(data, Mapping):
        return False
    choices = data.get("choices")
    return isinstance(choices, list) and bool(choices) and all(
        isinstance(choice, Mapping) and isinstance(choice.get("message"), Mapping)
        for choice in choices
    )


def _is_responses_envelope(data: object) -> bool:
    if not isinstance(data, Mapping):
        return False
    return isinstance(data.get("output"), list) or isinstance(data.get("output_text"), str)


def classify_response_envelope(
    endpoint_kind: EndpointKind,
    data: object,
) -> ResponseEnvelopeClassification:
    """Classify ``data`` against the protocol expected by ``endpoint_kind``."""

    expected_is_valid = (
        _is_chat_completions_envelope(data)
        if endpoint_kind == "chat_completions"
        else _is_responses_envelope(data)
    )
    if expected_is_valid:
        return "valid"

    other_is_valid = (
        _is_responses_envelope(data)
        if endpoint_kind == "chat_completions"
        else _is_chat_completions_envelope(data)
    )
    if other_is_valid:
        return "other_endpoint"
    return "invalid"


def endpoint_candidates(
    failed_endpoint_kind: EndpointKind | None = None,
) -> tuple[EndpointKind, EndpointKind]:
    """Return endpoint kinds in the order that should be attempted next."""

    if failed_endpoint_kind == "chat_completions":
        return "responses", "chat_completions"
    return "chat_completions", "responses"


def _cache_key(api_base_url: str, model_name: str) -> tuple[str, str]:
    return resolve_base_url(api_base_url), model_name


async def get_cached_endpoint_kind(
    session: AsyncSession,
    *,
    api_base_url: str,
    model_name: str,
) -> EndpointKind | None:
    """Return the endpoint protocol learned for a normalized target, if any."""

    normalized_base_url, normalized_model_name = _cache_key(api_base_url, model_name)
    learned_endpoint_kind = await session.scalar(
        select(LLMEndpointAdaptationCache.learned_endpoint_kind).where(
            LLMEndpointAdaptationCache.api_base_url == normalized_base_url,
            LLMEndpointAdaptationCache.model_name == normalized_model_name,
        )
    )
    if learned_endpoint_kind in ("chat_completions", "responses"):
        return cast(EndpointKind, learned_endpoint_kind)
    return None


def _loaded_rows_for_target(
    session: AsyncSession,
    *,
    api_base_url: str,
    model_name: str,
) -> list[LLMEndpointAdaptationCache]:
    return [
        row
        for row in session.identity_map.values()
        if isinstance(row, LLMEndpointAdaptationCache)
        and row.api_base_url == api_base_url
        and row.model_name == model_name
    ]


async def record_endpoint_adaptation(
    session: AsyncSession,
    *,
    api_base_url: str,
    model_name: str,
    endpoint_kind: EndpointKind,
) -> None:
    """Insert or update a learned endpoint protocol without committing ``session``."""

    normalized_base_url, normalized_model_name = _cache_key(api_base_url, model_name)
    now = utc_now()
    statement = sqlite_insert(LLMEndpointAdaptationCache).values(
        api_base_url=normalized_base_url,
        model_name=normalized_model_name,
        learned_endpoint_kind=endpoint_kind,
        probed_at=now,
        updated_at=now,
    )
    await session.execute(
        statement.on_conflict_do_update(
            index_elements=[
                LLMEndpointAdaptationCache.api_base_url,
                LLMEndpointAdaptationCache.model_name,
            ],
            set_={
                "learned_endpoint_kind": statement.excluded.learned_endpoint_kind,
                "probed_at": statement.excluded.probed_at,
                "updated_at": statement.excluded.updated_at,
            },
        )
    )

    # Core upserts bypass ORM state synchronization. Keep an already-loaded row
    # coherent for callers that continue using this session before committing.
    for row in _loaded_rows_for_target(
        session,
        api_base_url=normalized_base_url,
        model_name=normalized_model_name,
    ):
        set_committed_value(row, "learned_endpoint_kind", endpoint_kind)
        set_committed_value(row, "probed_at", now)
        set_committed_value(row, "updated_at", now)


async def invalidate_endpoint_adaptation(
    session: AsyncSession,
    *,
    api_base_url: str,
    model_name: str,
    failed_endpoint_kind: EndpointKind,
) -> bool:
    """Delete a cache entry only when it still refers to the failed endpoint."""

    normalized_base_url, normalized_model_name = _cache_key(api_base_url, model_name)
    result = await session.execute(
        delete(LLMEndpointAdaptationCache)
        .where(
            LLMEndpointAdaptationCache.api_base_url == normalized_base_url,
            LLMEndpointAdaptationCache.model_name == normalized_model_name,
            LLMEndpointAdaptationCache.learned_endpoint_kind == failed_endpoint_kind,
        )
        .execution_options(synchronize_session="fetch")
    )
    return result.rowcount > 0


@dataclass
class _EndpointAdaptationLockState:
    lock: asyncio.Lock
    users: int = 0
    learned_endpoint_kind: EndpointKind | None = None
    probe_error: Exception | None = None


_endpoint_adaptation_locks: dict[tuple[str, str], _EndpointAdaptationLockState] = {}


@asynccontextmanager
async def endpoint_adaptation_lock(
    api_base_url: str,
    model_name: str,
) -> AsyncIterator[_EndpointAdaptationLockState]:
    """Serialize and share one adaptation probe per normalized target."""

    key = _cache_key(api_base_url, model_name)
    state = _endpoint_adaptation_locks.get(key)
    if state is None:
        state = _EndpointAdaptationLockState(lock=asyncio.Lock())
        _endpoint_adaptation_locks[key] = state
    state.users += 1
    try:
        async with state.lock:
            yield state
    finally:
        state.users -= 1
        if state.users == 0 and _endpoint_adaptation_locks.get(key) is state:
            del _endpoint_adaptation_locks[key]
