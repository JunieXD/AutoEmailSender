from __future__ import annotations

from collections.abc import Mapping
from time import perf_counter
from typing import Final, Literal

from app.models import LLMProfile

from .contracts import ChatCompletionUsage as ChatCompletionUsage

DEFAULT_BASE_URL = "https://api.openai.com/v1"


DEFAULT_LLM_MAX_TOKENS = 6000


STEPFUN_PROBE_MAX_TOKENS = 128


STRUCTURED_OUTPUT_CONTROL_KEY = "__structured_output_control__"


_STEPFUN_OPENAI_BASE_URLS = frozenset(
    {
        "https://api.stepfun.com/v1",
        "https://api.stepfun.com/step_plan/v1",
    }
)


def resolve_base_url(api_base_url: str | None) -> str:
    return (api_base_url or DEFAULT_BASE_URL).strip().rstrip("/")


def with_structured_output(
    payload: dict[str, object],
    *,
    mode: Literal["json_schema_strict", "json_object", "prompt_only"],
    schema: dict[str, object] | None = None,
    schema_name: str = "structured_response",
) -> dict[str, object]:
    """Attach endpoint-neutral structured-output metadata to a request payload."""

    if mode == "json_schema_strict" and schema is None:
        raise ValueError("严格 JSON Schema 模式缺少 schema")
    result = dict(payload)
    result[STRUCTURED_OUTPUT_CONTROL_KEY] = {
        "mode": mode,
        "schema": dict(schema) if schema is not None else None,
        "schema_name": schema_name,
    }
    return result


def _prepare_strict_json_schema(schema: dict[str, object]) -> dict[str, object]:
    """Remove annotation-only Pydantic keywords from the wire schema."""

    def normalize(value: object) -> object:
        if isinstance(value, dict):
            normalized_items: dict[str, object] = {}
            for key, item in value.items():
                if key in {"title", "description", "default", "examples"}:
                    continue
                if key in {"properties", "$defs"} and isinstance(item, dict):
                    # Field/definition names are user-controlled keys.  A field
                    # named ``title`` is not the JSON Schema annotation keyword.
                    normalized_items[key] = {
                        child_key: normalize(child_value)
                        for child_key, child_value in item.items()
                    }
                else:
                    normalized_items[key] = normalize(item)
            return normalized_items
        if isinstance(value, list):
            return [normalize(item) for item in value]
        return value

    normalized = normalize(schema)
    if not isinstance(normalized, dict):
        raise ValueError("严格 JSON Schema 必须是对象")
    _validate_strict_json_schema_contract(normalized)
    return normalized


def _validate_strict_json_schema_contract(
    schema: dict[str, object],
    *,
    path: str = "$",
) -> None:
    if schema.get("type") == "object":
        properties = schema.get("properties")
        if not isinstance(properties, dict):
            raise ValueError(f"严格 JSON Schema 的对象缺少 properties: {path}")
        if schema.get("additionalProperties") is not False:
            raise ValueError(f"严格 JSON Schema 的对象必须禁止额外字段: {path}")
        required = schema.get("required")
        if not isinstance(required, list) or set(required) != set(properties):
            raise ValueError(
                f"严格 JSON Schema 的对象必须将全部属性标记为 required: {path}"
            )

    for key, value in schema.items():
        if isinstance(value, dict):
            _validate_strict_json_schema_contract(
                value,
                path=f"{path}.{key}",
            )
        elif isinstance(value, list):
            for index, item in enumerate(value):
                if isinstance(item, dict):
                    _validate_strict_json_schema_contract(
                        item,
                        path=f"{path}.{key}[{index}]",
                    )


def _extract_structured_output_control(
    payload: dict[str, object],
) -> tuple[dict[str, object], dict[str, object] | None]:
    request_payload = dict(payload)
    raw_control = request_payload.pop(STRUCTURED_OUTPUT_CONTROL_KEY, None)
    control = dict(raw_control) if isinstance(raw_control, dict) else None
    return request_payload, control


def _structured_output_format(control: dict[str, object]) -> dict[str, object] | None:
    mode = control.get("mode")
    if mode == "json_object":
        return {"type": "json_object"}
    if mode != "json_schema_strict":
        return None
    schema = control.get("schema")
    if not isinstance(schema, dict):
        raise ValueError("严格 JSON Schema 模式缺少有效 schema")
    return {
        "type": "json_schema",
        "name": str(control.get("schema_name") or "structured_response"),
        "strict": True,
        "schema": schema,
    }


def build_chat_completions_payload(payload: dict[str, object]) -> dict[str, object]:
    request_payload, control = _extract_structured_output_control(payload)
    if control is None:
        return request_payload
    output_format = _structured_output_format(control)
    if output_format is None:
        return request_payload
    if output_format.get("type") == "json_schema":
        request_payload["response_format"] = {
            "type": "json_schema",
            "json_schema": {
                key: value for key, value in output_format.items() if key != "type"
            },
        }
    else:
        request_payload["response_format"] = output_format
    return request_payload


def is_deepseek_profile(profile: LLMProfile) -> bool:
    provider = (profile.provider or "").strip().lower()
    if provider == "deepseek":
        return True

    model_name = (profile.model_name or "").strip().lower()
    if model_name.startswith("deepseek"):
        return True

    base_url = resolve_base_url(profile.api_base_url).lower()
    return "deepseek" in base_url


def is_stepfun_profile(profile: LLMProfile) -> bool:
    """Return whether a profile targets one of StepFun's official OpenAI APIs."""

    return resolve_base_url(profile.api_base_url).lower() in _STEPFUN_OPENAI_BASE_URLS


def probe_max_tokens_for_profile(profile: LLMProfile, *, fallback: int) -> int:
    """Keep generic probe budgets unchanged while giving StepFun room to reason."""

    if not is_stepfun_profile(profile):
        return fallback
    configured_limit = profile.max_tokens or DEFAULT_LLM_MAX_TOKENS
    return min(configured_limit, STEPFUN_PROBE_MAX_TOKENS)


def _empty_content_error_message(
    profile: LLMProfile,
    data: dict[str, object],
    endpoint_kind: str,
) -> str:
    """Describe StepFun reasoning-only replies without treating them as success."""

    if endpoint_kind != "chat_completions" or not is_stepfun_profile(profile):
        return "模型返回了空内容"

    choices = data.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        return "模型返回了空内容"
    choice = choices[0]
    message = choice.get("message")
    if not isinstance(message, dict):
        return "模型返回了空内容"
    reasoning = message.get("reasoning") or message.get("reasoning_content")
    if not isinstance(reasoning, str) or not reasoning.strip():
        return "模型返回了空内容"
    if choice.get("finish_reason") == "length":
        return "StepFun 模型仅返回了推理内容，输出 Token 已耗尽，尚未返回最终文本"
    return "StepFun 模型仅返回了推理内容，尚未返回最终文本"


def build_endpoint_url(base_url: str, suffix: str) -> str:
    return f"{base_url.rstrip('/')}/{suffix.lstrip('/')}"


def compute_duration_ms(start: float) -> int:
    return max(int((perf_counter() - start) * 1000), 1)


def build_responses_payload(payload: dict[str, object]) -> dict[str, object]:
    payload, control = _extract_structured_output_control(payload)
    request_payload: dict[str, object] = {
        "model": payload["model"],
        "input": _build_responses_input(payload.get("messages", [])),
    }
    for key in ("thinking", "enable_thinking", "reasoning", "thinking_budget"):
        if key in payload:
            request_payload[key] = payload[key]
    if payload.get("reasoning_effort") is not None:
        request_payload["reasoning_effort"] = payload["reasoning_effort"]
    if payload.get("temperature") is not None:
        request_payload["temperature"] = payload["temperature"]
    if payload.get("max_tokens") is not None:
        request_payload["max_output_tokens"] = payload["max_tokens"]
    if payload.get("prompt_cache_key") is not None:
        request_payload["prompt_cache_key"] = payload["prompt_cache_key"]
    if payload.get("prompt_cache_retention") is not None:
        request_payload["prompt_cache_retention"] = payload["prompt_cache_retention"]
    if control is not None:
        output_format = _structured_output_format(control)
        if output_format is not None:
            request_payload["text"] = {"format": output_format}
    return request_payload


def _build_responses_input(messages: object) -> list[dict[str, object]]:
    if not isinstance(messages, list):
        return []

    input_items: list[dict[str, object]] = []
    for message in messages:
        if not isinstance(message, dict):
            continue
        role = message.get("role")
        content = message.get("content")
        if not isinstance(role, str):
            continue
        input_items.append(
            {
                "type": "message",
                "role": role,
                "content": _build_responses_content_items(content),
            },
        )
    return input_items


def _build_responses_content_items(content: object) -> list[dict[str, str]]:
    if isinstance(content, str):
        return [{"type": "input_text", "text": content}]
    if not isinstance(content, list):
        return []

    content_items: list[dict[str, str]] = []
    for item in content:
        if isinstance(item, str):
            content_items.append({"type": "input_text", "text": item})
            continue
        if not isinstance(item, dict):
            continue
        text = item.get("text")
        if isinstance(text, str):
            content_items.append({"type": "input_text", "text": text})
    return content_items


def extract_chat_completion_content(data: dict[str, object]) -> str:
    content = data["choices"][0]["message"]["content"]
    if not isinstance(content, str):
        raise ValueError("choices[0].message.content 不是字符串")
    return content


def extract_responses_content(data: dict[str, object]) -> str:
    direct_output_text = data.get("output_text")
    if isinstance(direct_output_text, str) and direct_output_text.strip():
        return direct_output_text

    output_items = data.get("output")
    if not isinstance(output_items, list):
        raise ValueError("responses.output 不存在")

    chunks: list[str] = []
    for output_item in output_items:
        if not isinstance(output_item, dict):
            continue
        content_items = output_item.get("content")
        if not isinstance(content_items, list):
            continue
        for content_item in content_items:
            if not isinstance(content_item, dict):
                continue
            text_value = content_item.get("text")
            if isinstance(text_value, str) and text_value.strip():
                chunks.append(text_value)

    if not chunks:
        raise ValueError("responses.output 缺少文本内容")
    return "\n".join(chunks).strip()


def extract_model_ids(data: dict[str, object]) -> list[str]:
    raw_items = data.get("data", data.get("models"))
    if not isinstance(raw_items, list):
        raise ValueError("缺少 data/models 列表")

    model_ids: list[str] = []
    for item in raw_items:
        if isinstance(item, str) and item.strip():
            model_ids.append(item.strip())
            continue
        if not isinstance(item, dict):
            continue
        model_id = item.get("id")
        if isinstance(model_id, str) and model_id.strip():
            model_ids.append(model_id.strip())

    if not model_ids:
        raise ValueError("未解析到模型 ID")
    return model_ids


def format_http_error(status_code: int, response_text: str, request_url: str) -> str:
    return f"模型接口返回错误 {status_code}: {response_text[:300]} (请求 URL: {request_url})"


def parse_completion_usage(raw_usage: object) -> ChatCompletionUsage | None:
    if not isinstance(raw_usage, dict):
        return None
    cached_tokens = _coerce_token_count(raw_usage.get("prompt_cache_hit_tokens"))
    if cached_tokens is None:
        for details_key in ("prompt_tokens_details", "input_tokens_details"):
            details = raw_usage.get(details_key)
            if isinstance(details, dict):
                cached_tokens = _coerce_token_count(details.get("cached_tokens"))
                if cached_tokens is not None:
                    break
    reasoning_tokens = None
    for details_key in ("completion_tokens_details", "output_tokens_details"):
        details = raw_usage.get(details_key)
        if isinstance(details, dict):
            reasoning_tokens = _coerce_token_count(details.get("reasoning_tokens"))
            if reasoning_tokens is not None:
                break
    return ChatCompletionUsage(
        prompt_tokens=_coerce_token_count(
            raw_usage.get("prompt_tokens", raw_usage.get("input_tokens")),
        ),
        completion_tokens=_coerce_token_count(
            raw_usage.get("completion_tokens", raw_usage.get("output_tokens")),
        ),
        total_tokens=_coerce_token_count(raw_usage.get("total_tokens")),
        cached_tokens=cached_tokens,
        reasoning_tokens=reasoning_tokens,
    )


def _coerce_token_count(value: object) -> int | None:
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    return None


EndpointKind = Literal["chat_completions", "responses"]


ResponseEnvelopeClassification = Literal["valid", "other_endpoint", "invalid"]


def _is_chat_completions_envelope(data: object) -> bool:
    if not isinstance(data, Mapping):
        return False
    choices = data.get("choices")
    return (
        isinstance(choices, list)
        and bool(choices)
        and all(
            isinstance(choice, Mapping) and isinstance(choice.get("message"), Mapping)
            for choice in choices
        )
    )


def _is_responses_envelope(data: object) -> bool:
    if not isinstance(data, Mapping):
        return False
    return isinstance(data.get("output"), list) or isinstance(
        data.get("output_text"), str
    )


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


_THINKING_KEYS: Final[tuple[str, ...]] = (
    "thinking",
    "enable_thinking",
    "reasoning",
    "reasoning_effort",
    "thinking_budget",
)


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
