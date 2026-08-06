from __future__ import annotations

"""Capability probing and caching for structured LLM output protocols."""

import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import timedelta
from typing import Final, Literal, cast

from sqlalchemy import delete, select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import set_committed_value

from app.core.time import utc_now
from app.models import LLMProfile, LLMStructuredOutputAdaptationCache
from ..runtime import LLMRuntimeError


EndpointKind = Literal["chat_completions", "responses"]
StructuredOutputMode = Literal[
    "json_schema_strict",
    "json_object",
    "prompt_only",
]

STRUCTURED_OUTPUT_PROBE_VERSION: Final[int] = 3
PROMPT_ONLY_CACHE_TTL: Final[timedelta] = timedelta(days=7)

_CONFLICT_PROMPT: Final[str] = (
    "Capability probe. Reply with exactly PLAIN (five ASCII letters). "
    "Do not output braces, quotes, a colon, or an object. "
    "The word JSON appears only to satisfy any API keyword precondition."
)
_POSITIVE_JSON_PROMPT: Final[str] = (
    'Return exactly this JSON object: {"probe":"JSON_OK"}. '
    "Do not return a Markdown fence or any extra text."
)
_STRICT_PROBE_SCHEMA: Final[dict[str, object]] = {
    "$defs": {
        "ProbeItem": {
            "type": "object",
            "properties": {
                "value": {
                    "type": "string",
                },
                "count": {
                    "type": "integer",
                },
                "score": {
                    "type": "number",
                },
                "enabled": {
                    "type": "boolean",
                },
                "kind": {
                    "type": "string",
                    "enum": ["probe"],
                },
                "tags": {
                    "type": "array",
                    "items": {"type": "string"},
                },
            },
            "required": ["value", "count", "score", "enabled", "kind", "tags"],
            "additionalProperties": False,
        },
    },
    "type": "object",
    "properties": {
        "probe": {"$ref": "#/$defs/ProbeItem"},
        "items": {
            "type": "array",
            "items": {"$ref": "#/$defs/ProbeItem"},
        },
    },
    "required": ["probe", "items"],
    "additionalProperties": False,
}

_PROTOCOL_REJECTION_KEYWORDS: Final[tuple[str, ...]] = (
    "response_format",
    "response format",
    "json_schema",
    "json schema",
    "structured output",
    "text.format",
    "text format",
)
_UNSUPPORTED_KEYWORDS: Final[tuple[str, ...]] = (
    "unavailable",
    "unsupported",
    "not supported",
    "unknown parameter",
    "unknown field",
    "invalid type",
    "must be one of",
)


def resolve_base_url_for_cache(api_base_url: str | None) -> str:
    from ..runtime import resolve_base_url

    return resolve_base_url(api_base_url)


def _cache_key(
    api_base_url: str,
    model_name: str,
    endpoint_kind: EndpointKind,
    probe_version: int,
) -> tuple[str, str, EndpointKind, int]:
    return (
        resolve_base_url_for_cache(api_base_url),
        model_name.strip(),
        endpoint_kind,
        probe_version,
    )


async def get_cached_structured_output_mode(
    session: AsyncSession,
    *,
    api_base_url: str,
    model_name: str,
    endpoint_kind: EndpointKind,
    probe_version: int = STRUCTURED_OUTPUT_PROBE_VERSION,
) -> StructuredOutputMode | None:
    normalized_url, normalized_model, endpoint, version = _cache_key(
        api_base_url,
        model_name,
        endpoint_kind,
        probe_version,
    )
    row = await session.scalar(
        select(LLMStructuredOutputAdaptationCache).where(
            LLMStructuredOutputAdaptationCache.api_base_url == normalized_url,
            LLMStructuredOutputAdaptationCache.model_name == normalized_model,
            LLMStructuredOutputAdaptationCache.endpoint_kind == endpoint,
            LLMStructuredOutputAdaptationCache.probe_version == version,
        )
    )
    if row is None:
        return None
    if row.expires_at is not None and row.expires_at <= utc_now():
        return None
    if row.learned_mode not in (
        "json_schema_strict",
        "json_object",
        "prompt_only",
    ):
        return None
    return cast(StructuredOutputMode, row.learned_mode)


def _loaded_rows_for_target(
    session: AsyncSession,
    *,
    api_base_url: str,
    model_name: str,
    endpoint_kind: EndpointKind,
    probe_version: int,
) -> list[LLMStructuredOutputAdaptationCache]:
    return [
        row
        for row in session.identity_map.values()
        if isinstance(row, LLMStructuredOutputAdaptationCache)
        and row.api_base_url == api_base_url
        and row.model_name == model_name
        and row.endpoint_kind == endpoint_kind
        and row.probe_version == probe_version
    ]


async def record_structured_output_adaptation(
    session: AsyncSession,
    *,
    api_base_url: str,
    model_name: str,
    endpoint_kind: EndpointKind,
    learned_mode: StructuredOutputMode,
    probe_version: int = STRUCTURED_OUTPUT_PROBE_VERSION,
) -> None:
    normalized_url, normalized_model, endpoint, version = _cache_key(
        api_base_url,
        model_name,
        endpoint_kind,
        probe_version,
    )
    now = utc_now()
    expires_at = now + PROMPT_ONLY_CACHE_TTL if learned_mode == "prompt_only" else None
    statement = sqlite_insert(LLMStructuredOutputAdaptationCache).values(
        api_base_url=normalized_url,
        model_name=normalized_model,
        endpoint_kind=endpoint,
        probe_version=version,
        learned_mode=learned_mode,
        probed_at=now,
        expires_at=expires_at,
        updated_at=now,
    )
    await session.execute(
        statement.on_conflict_do_update(
            index_elements=[
                LLMStructuredOutputAdaptationCache.api_base_url,
                LLMStructuredOutputAdaptationCache.model_name,
                LLMStructuredOutputAdaptationCache.endpoint_kind,
                LLMStructuredOutputAdaptationCache.probe_version,
            ],
            set_={
                "learned_mode": statement.excluded.learned_mode,
                "probed_at": statement.excluded.probed_at,
                "expires_at": statement.excluded.expires_at,
                "updated_at": statement.excluded.updated_at,
            },
        )
    )
    for row in _loaded_rows_for_target(
        session,
        api_base_url=normalized_url,
        model_name=normalized_model,
        endpoint_kind=endpoint,
        probe_version=version,
    ):
        set_committed_value(row, "learned_mode", learned_mode)
        set_committed_value(row, "probed_at", now)
        set_committed_value(row, "expires_at", expires_at)
        set_committed_value(row, "updated_at", now)


async def invalidate_structured_output_adaptation(
    session: AsyncSession,
    *,
    api_base_url: str,
    model_name: str,
    endpoint_kind: EndpointKind,
    expected_mode: StructuredOutputMode,
    probe_version: int = STRUCTURED_OUTPUT_PROBE_VERSION,
) -> bool:
    normalized_url, normalized_model, endpoint, version = _cache_key(
        api_base_url,
        model_name,
        endpoint_kind,
        probe_version,
    )
    result = await session.execute(
        delete(LLMStructuredOutputAdaptationCache).where(
            LLMStructuredOutputAdaptationCache.api_base_url == normalized_url,
            LLMStructuredOutputAdaptationCache.model_name == normalized_model,
            LLMStructuredOutputAdaptationCache.endpoint_kind == endpoint,
            LLMStructuredOutputAdaptationCache.probe_version == version,
            LLMStructuredOutputAdaptationCache.learned_mode == expected_mode,
        )
    )
    return result.rowcount > 0


def is_structured_output_protocol_rejection(error: LLMRuntimeError) -> bool:
    if error.status_code not in (400, 422):
        return False
    text = str(error).lower()
    return (
        any(keyword in text for keyword in _PROTOCOL_REJECTION_KEYWORDS)
        and any(keyword in text for keyword in _UNSUPPORTED_KEYWORDS)
    )


def _parse_exact_json_object(content: str) -> dict[str, object] | None:
    try:
        value = json.loads(content)
    except (TypeError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _matches_strict_probe_schema(content: str) -> bool:
    value = _parse_exact_json_object(content)
    if value is None or set(value) != {"probe", "items"}:
        return False
    probe = value.get("probe")
    items = value.get("items")
    if not _matches_strict_probe_item(probe) or not isinstance(items, list):
        return False
    return all(_matches_strict_probe_item(item) for item in items)


def _matches_strict_probe_item(item: object) -> bool:
    required = {"value", "count", "score", "enabled", "kind", "tags"}
    return (
        isinstance(item, dict)
        and set(item) == required
        and isinstance(item.get("value"), str)
        and isinstance(item.get("count"), int)
        and not isinstance(item.get("count"), bool)
        and isinstance(item.get("score"), (int, float))
        and not isinstance(item.get("score"), bool)
        and isinstance(item.get("enabled"), bool)
        and item.get("kind") == "probe"
        and isinstance(item.get("tags"), list)
        and all(isinstance(tag, str) for tag in item["tags"])
    )


def _probe_payload(profile: LLMProfile, prompt: str, *, max_tokens: int) -> dict[str, object]:
    return {
        "model": profile.model_name,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0,
        "max_tokens": max_tokens,
    }


async def _request_probe(
    profile: LLMProfile,
    *,
    endpoint_kind: EndpointKind,
    thinking_extra_body: dict[str, object] | None,
    prompt: str,
    max_tokens: int,
    mode: StructuredOutputMode | None,
) -> object:
    from ..runtime import (
        _request_completion_endpoint,
        with_structured_output,
    )

    payload = _probe_payload(profile, prompt, max_tokens=max_tokens)
    if mode is not None:
        payload = with_structured_output(
            payload,
            mode=mode,
            schema=(
                dict(_STRICT_PROBE_SCHEMA)
                if mode == "json_schema_strict"
                else None
            ),
            schema_name="structured_capability_probe",
        )
    return await _request_completion_endpoint(
        profile,
        payload,
        endpoint_kind=endpoint_kind,
        extra_body=thinking_extra_body,
        allow_empty_content=True,
    )


async def probe_structured_output_mode(
    session: AsyncSession,
    profile: LLMProfile,
    *,
    endpoint_kind: EndpointKind,
    thinking_extra_body: dict[str, object] | None,
    probe_version: int = STRUCTURED_OUTPUT_PROBE_VERSION,
) -> StructuredOutputMode:
    api_base_url = resolve_base_url_for_cache(profile.api_base_url)

    try:
        strict_result = await _request_probe(
            profile,
            endpoint_kind=endpoint_kind,
            thinking_extra_body=thinking_extra_body,
            prompt=_CONFLICT_PROMPT,
            max_tokens=64,
            mode="json_schema_strict",
        )
    except LLMRuntimeError as error:
        if not is_structured_output_protocol_rejection(error):
            raise
    else:
        strict_content = getattr(strict_result, "content", "")
        if _matches_strict_probe_schema(strict_content):
            await record_structured_output_adaptation(
                session,
                api_base_url=api_base_url,
                model_name=profile.model_name,
                endpoint_kind=endpoint_kind,
                learned_mode="json_schema_strict",
                probe_version=probe_version,
            )
            return "json_schema_strict"

    baseline_result = await _request_probe(
        profile,
        endpoint_kind=endpoint_kind,
        thinking_extra_body=thinking_extra_body,
        prompt=_CONFLICT_PROMPT,
        max_tokens=12,
        mode=None,
    )
    baseline_content = str(getattr(baseline_result, "content", "")).strip()
    if baseline_content != "PLAIN":
        mode: StructuredOutputMode = "prompt_only"
    else:
        try:
            conflict_result = await _request_probe(
                profile,
                endpoint_kind=endpoint_kind,
                thinking_extra_body=thinking_extra_body,
                prompt=_CONFLICT_PROMPT,
                max_tokens=12,
                mode="json_object",
            )
            conflict_content = str(getattr(conflict_result, "content", ""))
            conflict_object = _parse_exact_json_object(conflict_content)
            shows_protocol_effect = (
                conflict_object is not None or not conflict_content.strip()
            ) and conflict_content.strip() != "PLAIN"
            if not shows_protocol_effect:
                mode = "prompt_only"
            else:
                positive_result = await _request_probe(
                    profile,
                    endpoint_kind=endpoint_kind,
                    thinking_extra_body=thinking_extra_body,
                    prompt=_POSITIVE_JSON_PROMPT,
                    max_tokens=24,
                    mode="json_object",
                )
                positive_content = str(getattr(positive_result, "content", ""))
                mode = (
                    "json_object"
                    if _parse_exact_json_object(positive_content) == {"probe": "JSON_OK"}
                    else "prompt_only"
                )
        except LLMRuntimeError as error:
            if not is_structured_output_protocol_rejection(error):
                raise
            mode = "prompt_only"

    await record_structured_output_adaptation(
        session,
        api_base_url=api_base_url,
        model_name=profile.model_name,
        endpoint_kind=endpoint_kind,
        learned_mode=mode,
        probe_version=probe_version,
    )
    return mode


@dataclass
class _StructuredOutputAdaptationLockState:
    lock: asyncio.Lock
    users: int = 0
    learned_mode: StructuredOutputMode | None = None
    probe_error: Exception | None = None


_structured_output_adaptation_locks: dict[
    tuple[str, str, EndpointKind, int],
    _StructuredOutputAdaptationLockState,
] = {}


@asynccontextmanager
async def structured_output_adaptation_lock(
    api_base_url: str,
    model_name: str,
    endpoint_kind: EndpointKind,
    probe_version: int = STRUCTURED_OUTPUT_PROBE_VERSION,
) -> AsyncIterator[_StructuredOutputAdaptationLockState]:
    key = _cache_key(api_base_url, model_name, endpoint_kind, probe_version)
    state = _structured_output_adaptation_locks.get(key)
    if state is None:
        state = _StructuredOutputAdaptationLockState(lock=asyncio.Lock())
        _structured_output_adaptation_locks[key] = state
    state.users += 1
    try:
        async with state.lock:
            yield state
    finally:
        state.users -= 1
        if state.users == 0 and _structured_output_adaptation_locks.get(key) is state:
            del _structured_output_adaptation_locks[key]


async def ensure_structured_output_adaptation(
    session: AsyncSession,
    profile: LLMProfile,
    *,
    endpoint_kind: EndpointKind,
    thinking_extra_body: dict[str, object] | None,
    probe_version: int = STRUCTURED_OUTPUT_PROBE_VERSION,
) -> StructuredOutputMode:
    api_base_url = resolve_base_url_for_cache(profile.api_base_url)
    cached = await get_cached_structured_output_mode(
        session,
        api_base_url=api_base_url,
        model_name=profile.model_name,
        endpoint_kind=endpoint_kind,
        probe_version=probe_version,
    )
    if cached is not None:
        return cached

    async with structured_output_adaptation_lock(
        api_base_url,
        profile.model_name,
        endpoint_kind,
        probe_version,
    ) as coordination:
        cached = await get_cached_structured_output_mode(
            session,
            api_base_url=api_base_url,
            model_name=profile.model_name,
            endpoint_kind=endpoint_kind,
            probe_version=probe_version,
        )
        if cached is not None:
            return cached
        if coordination.learned_mode is not None:
            return coordination.learned_mode
        if coordination.probe_error is not None:
            raise coordination.probe_error
        try:
            learned = await probe_structured_output_mode(
                session,
                profile,
                endpoint_kind=endpoint_kind,
                thinking_extra_body=thinking_extra_body,
                probe_version=probe_version,
            )
        except Exception as error:
            coordination.probe_error = error
            raise
        coordination.learned_mode = learned
        return learned


__all__ = [
    "EndpointKind",
    "PROMPT_ONLY_CACHE_TTL",
    "STRUCTURED_OUTPUT_PROBE_VERSION",
    "StructuredOutputMode",
    "ensure_structured_output_adaptation",
    "get_cached_structured_output_mode",
    "invalidate_structured_output_adaptation",
    "is_structured_output_protocol_rejection",
    "probe_structured_output_mode",
    "record_structured_output_adaptation",
    "resolve_base_url_for_cache",
    "structured_output_adaptation_lock",
]
