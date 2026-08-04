from __future__ import annotations

"""Thinking-mode adaptation: detect protocol errors and find the right extra_body per endpoint.

The cache is keyed by (api_base_url, model_name, endpoint_kind), so the same
model can retain independent thinking adaptations for different endpoint
protocols. Rows are stored in ``thinking_adaptation_cache``; see
``docs/database_table_design.md`` for field semantics.
"""


from app.core.time import utc_now

from typing import Final, Literal


EndpointKind = Literal["chat_completions", "responses"]


THINKING_PROTOCOL_ERROR_KEYWORDS: Final[tuple[str, ...]] = (
    "reasoning_content",
    "reasoning blocks",
    "reasoning block",
    "thinking mode",
    "thinking block",
    "thinking blocks",
    "must be passed back",
    "must be preserved",
)

# Candidates are tried in priority order. The first item covers DeepSeek /
# MiMo / Doubao / Moonshot; the rest cover Qwen3 / GLM / OpenRouter / Gemini.
THINKING_DISABLE_CANDIDATES: Final[tuple[dict[str, object], ...]] = (
    {"thinking": {"type": "disabled"}},
    {"enable_thinking": False},
    {"reasoning": {"effort": "off"}},
    {"reasoning_effort": "low"},
    {"thinking_budget": 0},
)

_THINKING_KEYS: Final[tuple[str, ...]] = (
    "thinking",
    "enable_thinking",
    "reasoning",
    "reasoning_effort",
    "thinking_budget",
)


def is_thinking_mode_protocol_error(status_code: int, response_text: str) -> bool:
    """Return True when an HTTP failure looks like a thinking-mode protocol error.

    These errors only appear on multi-turn calls where the upstream model
    requires the previous assistant ``reasoning_content`` to be replayed.
    """

    if status_code != 400 or not response_text:
        return False
    haystack = response_text.lower()
    return any(keyword in haystack for keyword in THINKING_PROTOCOL_ERROR_KEYWORDS)


def strip_thinking_keys(payload: dict[str, object]) -> dict[str, object]:
    """Remove every known thinking-mode override key from ``payload`` (out-of-place)."""

    cleaned = dict(payload)
    for key in _THINKING_KEYS:
        cleaned.pop(key, None)
    return cleaned


def merge_extra_body(
    payload: dict[str, object],
    extra_body: dict[str, object] | None,
) -> dict[str, object]:
    """Strip any existing thinking keys from ``payload`` and overlay ``extra_body``.

    Always overwrites so a single attempt's intent is unambiguous: if
    ``extra_body`` is ``None`` we strip and write nothing back.
    """

    merged = strip_thinking_keys(payload)
    if extra_body:
        merged.update(extra_body)
    return merged


from datetime import UTC, datetime

from sqlalchemy import JSON, delete, select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import ThinkingAdaptationCache
from app.models import LLMProfile
from app.services.llm_runtime import LLMRuntimeError


async def get_cached_extra_body(
    session: AsyncSession,
    *,
    api_base_url: str,
    model_name: str,
    endpoint_kind: EndpointKind = "chat_completions",
) -> tuple[bool, dict[str, object] | None]:
    """Look up the cached extra_body for a (base_url, model_name, endpoint) target.

    Returns ``(hit, value)`` where ``hit`` is True if a row exists (even if the
    stored value is ``None``, which positively means "we tried and the model
    needs no extra_body").
    """

    row = await session.scalar(
        select(ThinkingAdaptationCache).where(
            ThinkingAdaptationCache.api_base_url == api_base_url,
            ThinkingAdaptationCache.model_name == model_name,
            ThinkingAdaptationCache.endpoint_kind == endpoint_kind,
        )
    )
    if row is None:
        return False, None
    value = row.learned_extra_body
    return True, dict(value) if isinstance(value, dict) else None


async def record_thinking_adaptation(
    session: AsyncSession,
    *,
    api_base_url: str,
    model_name: str,
    endpoint_kind: str = "chat_completions",
    learned_extra_body: dict[str, object] | None,
) -> None:
    """Insert or update the cache row for a ``(base_url, model_name, endpoint)`` target.

    The caller is responsible for committing the surrounding session.
    """

    now = utc_now()
    learned_value = dict(learned_extra_body) if learned_extra_body else None
    statement = sqlite_insert(ThinkingAdaptationCache).values(
        api_base_url=api_base_url,
        model_name=model_name,
        endpoint_kind=endpoint_kind,
        learned_extra_body=learned_value,
        probed_at=now,
        updated_at=now,
    )
    await session.execute(
        statement.on_conflict_do_update(
            index_elements=[
                ThinkingAdaptationCache.api_base_url,
                ThinkingAdaptationCache.model_name,
                ThinkingAdaptationCache.endpoint_kind,
            ],
            set_={
                "learned_extra_body": statement.excluded.learned_extra_body,
                "probed_at": statement.excluded.probed_at,
                "updated_at": now,
            },
        )
    )


async def invalidate_thinking_adaptation(
    session: AsyncSession,
    *,
    api_base_url: str,
    model_name: str,
    endpoint_kind: EndpointKind,
    expected_extra_body: dict[str, object] | None,
) -> bool:
    """Delete one cache row only when its learned value still matches.

    A single conditional DELETE avoids deleting a newer adaptation written by
    a concurrent request after the failing request read its stale value.
    """

    # The model stores ``None`` as JSON null rather than SQL NULL.
    expected_value: object = (
        JSON.NULL if expected_extra_body is None else expected_extra_body
    )
    result = await session.execute(
        delete(ThinkingAdaptationCache).where(
            ThinkingAdaptationCache.api_base_url == api_base_url,
            ThinkingAdaptationCache.model_name == model_name,
            ThinkingAdaptationCache.endpoint_kind == endpoint_kind,
            ThinkingAdaptationCache.learned_extra_body == expected_value,
        )
    )
    return result.rowcount > 0


class ThinkingAdaptationFailed(RuntimeError):
    """Raised when no candidate extra_body can satisfy the model's thinking-mode protocol."""

    def __init__(
        self,
        message: str,
        *,
        attempted_extra_bodies: list[dict[str, object] | None],
        last_error: LLMRuntimeError | None = None,
    ) -> None:
        super().__init__(message)
        self.attempted_extra_bodies = attempted_extra_bodies
        self.last_error = last_error


def _usage_completion_tokens(result: object) -> int | None:
    usage = getattr(result, "usage", None)
    return getattr(usage, "completion_tokens", None)


def _usage_reasoning_tokens(result: object) -> int | None:
    usage = getattr(result, "usage", None)
    return getattr(usage, "reasoning_tokens", None)


def _looks_like_thinking_enabled(result: object) -> bool:
    reasoning_tokens = _usage_reasoning_tokens(result)
    if reasoning_tokens is not None:
        return reasoning_tokens > 0
    completion_tokens = _usage_completion_tokens(result)
    return completion_tokens is not None and completion_tokens > 32


def _is_better_thinking_disable_result(
    *,
    candidate_result: object,
    baseline_result: object,
) -> bool:
    candidate_reasoning = _usage_reasoning_tokens(candidate_result)
    baseline_reasoning = _usage_reasoning_tokens(baseline_result)
    candidate_completion = _usage_completion_tokens(candidate_result)
    baseline_completion = _usage_completion_tokens(baseline_result)
    if candidate_reasoning is not None or baseline_reasoning is not None:
        if candidate_reasoning is None:
            return False
        if baseline_reasoning is None:
            return candidate_reasoning == 0 and (
                candidate_completion is not None
                and (baseline_completion is None or candidate_completion < baseline_completion)
            )
        if candidate_reasoning != baseline_reasoning:
            return candidate_reasoning < baseline_reasoning
        if candidate_completion is None or baseline_completion is None:
            return False
        return candidate_completion < baseline_completion

    if candidate_completion is None or baseline_completion is None:
        return False
    return candidate_completion < baseline_completion


def _build_probe_payload(profile: LLMProfile) -> dict[str, object]:
    """Build a 3-turn payload that triggers thinking-mode protocol errors on
    affected models, but is harmless on regular models (they just answer "7")."""

    from app.services.llm_runtime import probe_max_tokens_for_profile

    return {
        "model": profile.model_name,
        "messages": [
            {"role": "user", "content": "记住数字 7。"},
            {"role": "assistant", "content": "好的，我已记住数字 7。"},
            {"role": "user", "content": "我让你记的数字是几？只回复数字。"},
        ],
        "temperature": 0,
        "max_tokens": probe_max_tokens_for_profile(profile, fallback=16),
    }


def resolve_base_url_for_cache(api_base_url: str | None) -> str:
    """Normalize the api_base_url for use as a cache key."""

    from app.services.llm_runtime import resolve_base_url

    return resolve_base_url(api_base_url)


async def probe_and_learn_extra_body(
    session: AsyncSession,
    profile: LLMProfile,
    *,
    endpoint_kind: EndpointKind = "chat_completions",
) -> dict[str, object] | None:
    """Send a multi-turn probe and learn which extra_body the model needs.

    On success: writes a row into ``thinking_adaptation_cache`` and returns the
    learned value (``None`` if the model needs no extra_body).

    On thinking-mode protocol failure across all candidates: raises
    ``ThinkingAdaptationFailed`` and writes nothing.

    On other 4xx/5xx: re-raises ``LLMRuntimeError`` (caller decides what to do).
    """

    from app.services.llm_runtime import _request_completion_endpoint

    payload = _build_probe_payload(profile)
    attempts: list[dict[str, object] | None] = [None, *THINKING_DISABLE_CANDIDATES]
    last_error: LLMRuntimeError | None = None

    for index, candidate in enumerate(attempts):
        try:
            completion = await _request_completion_endpoint(
                profile,
                payload,
                endpoint_kind=endpoint_kind,
                extra_body=candidate,
            )
        except LLMRuntimeError as exc:
            last_error = exc
            # 两种"思考模式信号"会触发候选切换：
            #   1. HTTP 400 + 协议错关键词（典型：reasoning_content must be passed back）
            #   2. HTTP 200 但 content 为空——思考模型把回答塞进 reasoning_content，
            #      content 留空，request_chat_completion 因此抛 "模型返回了空内容"
            is_protocol_400 = (
                exc.status_code == 400
                and is_thinking_mode_protocol_error(exc.status_code or 0, str(exc))
            )
            is_empty_content_200 = (
                exc.status_code == 200
                and "空内容" in str(exc)
            )
            if not (is_protocol_400 or is_empty_content_200):
                raise
            if index == len(attempts) - 1:
                raise ThinkingAdaptationFailed(
                    "已尝试全部候选 extra_body，仍无法绕开思考模式协议错。",
                    attempted_extra_bodies=attempts,
                    last_error=exc,
                ) from exc
            continue

        if candidate is None and _looks_like_thinking_enabled(completion):
            baseline_completion = completion
            best_candidate: dict[str, object] | None = None
            best_completion = completion
            for disable_candidate in THINKING_DISABLE_CANDIDATES:
                try:
                    disable_completion = await _request_completion_endpoint(
                        profile,
                        payload,
                        endpoint_kind=endpoint_kind,
                        extra_body=disable_candidate,
                    )
                except LLMRuntimeError:
                    continue
                if _is_better_thinking_disable_result(
                    candidate_result=disable_completion,
                    baseline_result=baseline_completion,
                ) and _is_better_thinking_disable_result(
                    candidate_result=disable_completion,
                    baseline_result=best_completion,
                ):
                    best_candidate = disable_candidate
                    best_completion = disable_completion
            await record_thinking_adaptation(
                session,
                api_base_url=resolve_base_url_for_cache(profile.api_base_url),
                model_name=profile.model_name,
                endpoint_kind=endpoint_kind,
                learned_extra_body=best_candidate,
            )
            return dict(best_candidate) if best_candidate else None

        await record_thinking_adaptation(
            session,
            api_base_url=resolve_base_url_for_cache(profile.api_base_url),
            model_name=profile.model_name,
            endpoint_kind=endpoint_kind,
            learned_extra_body=candidate,
        )
        return dict(candidate) if candidate else None

    # 不可达：循环要么 return 要么 raise
    raise AssertionError("probe_and_learn_extra_body terminated unexpectedly")


async def ensure_thinking_adaptation(
    session: AsyncSession,
    profile: LLMProfile,
    *,
    endpoint_kind: EndpointKind = "chat_completions",
) -> dict[str, object] | None:
    """Return the extra_body to use for ``profile``, probing on cache miss.

    - Cache hit (any value, including ``None``) → return cached value
    - Cache miss → run :func:`probe_and_learn_extra_body`, write the row, return the result
    - Probe-level errors propagate to the caller (``ThinkingAdaptationFailed`` or ``LLMRuntimeError``)
    """

    api_base_url = resolve_base_url_for_cache(profile.api_base_url)
    hit, value = await get_cached_extra_body(
        session,
        api_base_url=api_base_url,
        model_name=profile.model_name,
        endpoint_kind=endpoint_kind,
    )
    if hit:
        return value
    try:
        return await probe_and_learn_extra_body(
            session,
            profile,
            endpoint_kind=endpoint_kind,
        )
    except IntegrityError:
        await session.rollback()
        hit, value = await get_cached_extra_body(
            session,
            api_base_url=api_base_url,
            model_name=profile.model_name,
            endpoint_kind=endpoint_kind,
        )
        if hit:
            return value
        raise


async def resolve_thinking_extra_body(profile: LLMProfile) -> dict[str, object] | None:
    """Best-effort process-wide resolver for callers that lack a database session.

    Session-owning workflows should prefer :func:`ensure_thinking_adaptation`,
    which can actively probe and persist cache misses. Direct LLM calls use this
    function to pick up already learned cache rows without forcing every call site
    to pass ``extra_body`` manually.
    """

    try:
        from app.core.database import get_session_factory

        async with get_session_factory()() as session:
            return await ensure_thinking_adaptation(session, profile)
    except Exception:
        return None



def adapt_failure_message_for_thinking_error(message: str | None) -> str | None:
    """If ``message`` looks like a thinking-mode protocol error from the upstream model,
    append a user-facing hint pointing at the remediation path."""

    if not message:
        return message
    # 抓取通过 LangChain 触发的协议错没有显式 status_code；统一以 400 视角做关键词匹配
    if not is_thinking_mode_protocol_error(400, message):
        return message
    return (
        f"{message}\n\n"
        "提示：模型在多轮调用中要求回传 thinking 字段。"
        "请在 LLM Profile 设置中点击「测试连接」重新触发自适应探活，再重新启动抓取。"
        "如果反复失败，请在 GitHub Issue 报告该模型与对应错误信息。"
    )
