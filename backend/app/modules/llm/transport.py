from __future__ import annotations

import json
import re
import ssl
from datetime import UTC, datetime
from time import perf_counter
from typing import Literal
from urllib.parse import urlsplit, urlunsplit

import httpx

from app.core.config import get_settings
from app.models import LLMProfile

from .contracts import (
    ChatCompletionResult as ChatCompletionResult,
    LLMEmptyContentError as LLMEmptyContentError,
    LLMEndpointProtocolError as LLMEndpointProtocolError,
    LLMRuntimeError as LLMRuntimeError,
)
from .wire import (
    _empty_content_error_message as _empty_content_error_message,
    build_chat_completions_payload as build_chat_completions_payload,
    build_endpoint_url as build_endpoint_url,
    build_responses_payload as build_responses_payload,
    compute_duration_ms as compute_duration_ms,
    extract_chat_completion_content as extract_chat_completion_content,
    extract_responses_content as extract_responses_content,
    format_http_error as format_http_error,
    is_deepseek_profile as is_deepseek_profile,
    parse_completion_usage as parse_completion_usage,
    resolve_base_url as resolve_base_url,
)


def format_llm_client_initialization_error(exc: ImportError | ValueError) -> str:
    return f"模型请求初始化失败: {exc}"


_LLM_CONNECTION_ERROR_MARKERS = (
    "all connection attempts failed",
    "bad record mac",
    "connecterror",
    "connect error",
    "connection refused",
    "connection reset",
    "failed to establish a new connection",
    "name or service not known",
    "temporary failure in name resolution",
    "nodename nor servname",
    "getaddrinfo failed",
    "network is unreachable",
    "no route to host",
    "ssl/tls alert",
    "sslv3_alert_bad_record_mac",
)


_LLM_TLS_ERROR_MARKERS = (
    "bad record mac",
    "ssl/tls alert",
    "sslv3_alert_bad_record_mac",
)


_LLM_TLS_CONNECTION_ERROR_MESSAGE = (
    "模型服务 TLS 连接失败，请检查系统代理、网络或稍后重试。"
)


_LLM_RUNTIME_LOG_NAME = "llm-runtime.log"


_LOG_URL_PATTERN = re.compile(r"https?://[^\s'\"<>]+")


def format_llm_runtime_error_for_user(message_or_exc: object) -> str:
    message = str(message_or_exc).strip()
    if not message:
        return "模型请求失败"
    if message.rstrip(":").strip() in {"模型请求失败", "获取模型列表失败"}:
        return message.rstrip(":").strip()
    if "模型服务连接失败" in message:
        return message

    haystack_parts = [message]
    haystack_parts.append(type(message_or_exc).__name__)
    cause = getattr(message_or_exc, "__cause__", None)
    if cause is not None:
        haystack_parts.append(type(cause).__name__)
        haystack_parts.append(str(cause))
    haystack = " ".join(haystack_parts).lower()
    if any(marker in haystack for marker in _LLM_TLS_ERROR_MARKERS):
        return _LLM_TLS_CONNECTION_ERROR_MESSAGE
    if any(marker in haystack for marker in _LLM_CONNECTION_ERROR_MARKERS):
        return "模型服务连接失败，请检查系统代理或网络后重试。"

    return message


def _append_llm_runtime_log(entry: str) -> None:
    try:
        log_dir = get_settings().data_dir / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        with (log_dir / _LLM_RUNTIME_LOG_NAME).open(
            "a", encoding="utf-8", newline="\n"
        ) as file:
            file.write(entry)
    except Exception:
        return


def _exception_chain_details(exc: BaseException) -> list[dict[str, str]]:
    details: list[dict[str, str]] = []
    current: BaseException | None = exc
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        details.append(
            {
                "type": type(current).__name__,
                "message": _sanitize_log_text(str(current)),
                "repr": _sanitize_log_text(repr(current)),
            },
        )
        current = current.__cause__ or current.__context__
    return details


def _sanitize_log_text(value: str) -> str:
    def replace_url(match: re.Match[str]) -> str:
        url = match.group(0)
        trailing = ""
        while url and url[-1] in ".,);]":
            trailing = f"{url[-1]}{trailing}"
            url = url[:-1]
        return f"{sanitize_llm_url(url) or url}{trailing}"

    return _LOG_URL_PATTERN.sub(replace_url, value)


def _is_tls_bad_record_mac_error(exc: BaseException) -> bool:
    haystack = " ".join(
        part
        for detail in _exception_chain_details(exc)
        for part in (detail["type"], detail["message"], detail["repr"])
    ).lower()
    return any(marker in haystack for marker in _LLM_TLS_ERROR_MARKERS)


def sanitize_llm_url(url: str | None) -> str | None:
    if url is None:
        return None
    parsed = urlsplit(url)
    hostname = parsed.hostname
    if hostname is None:
        netloc = parsed.netloc.rsplit("@", 1)[-1]
    else:
        netloc = f"[{hostname}]" if ":" in hostname else hostname
        try:
            port = parsed.port
        except ValueError:
            port = None
        if port is not None:
            netloc = f"{netloc}:{port}"
    return urlunsplit((parsed.scheme, netloc, parsed.path, "", ""))


def _log_llm_http_exception(
    *,
    profile: LLMProfile,
    request_url: str,
    endpoint_kind: str,
    tls_mode: str,
    exc: BaseException,
    will_retry: bool,
    retry_reason: str | None = None,
) -> None:
    entry = {
        "timestamp": datetime.now(UTC).isoformat(),
        "event": "llm_http_request_failed",
        "provider": profile.provider,
        "model_name": profile.model_name,
        "api_base_url": sanitize_llm_url(resolve_base_url(profile.api_base_url)),
        "request_url": sanitize_llm_url(request_url),
        "endpoint_kind": endpoint_kind,
        "tls_mode": tls_mode,
        "will_retry": will_retry,
        "retry_reason": retry_reason,
        "error_chain": _exception_chain_details(exc),
    }
    _append_llm_runtime_log(
        json.dumps(entry, ensure_ascii=False, sort_keys=True) + "\n"
    )


def _build_tls12_context() -> ssl.SSLContext:
    context = ssl.create_default_context()
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.maximum_version = ssl.TLSVersion.TLSv1_2
    return context


def _should_retry_with_tls12(
    profile: LLMProfile, exc: BaseException, tls_mode: str
) -> bool:
    return (
        tls_mode != "tls12"
        and is_deepseek_profile(profile)
        and _is_tls_bad_record_mac_error(exc)
    )


async def _send_llm_http_request(
    *,
    method: str,
    profile: LLMProfile,
    url: str,
    endpoint_kind: str,
    headers: dict[str, str],
    timeout: httpx.Timeout,
    json_body: dict[str, object] | None = None,
) -> httpx.Response:
    tls_context: ssl.SSLContext | None = None
    while True:
        tls_mode = "tls12" if tls_context is not None else "default"
        client_kwargs: dict[str, object] = {"timeout": timeout}
        if tls_context is not None:
            client_kwargs["verify"] = tls_context
        try:
            async with httpx.AsyncClient(**client_kwargs) as client:
                if method == "GET":
                    return await client.get(url, headers=headers)
                if method == "POST":
                    return await client.post(url, headers=headers, json=json_body)
                raise ValueError(f"Unsupported LLM HTTP method: {method}")
        except httpx.TimeoutException:
            raise
        except (httpx.HTTPError, ssl.SSLError) as exc:
            will_retry = _should_retry_with_tls12(profile, exc, tls_mode)
            retry_reason = "tls12_retry" if will_retry else None
            _log_llm_http_exception(
                profile=profile,
                request_url=url,
                endpoint_kind=endpoint_kind,
                tls_mode=tls_mode,
                exc=exc,
                will_retry=will_retry,
                retry_reason=retry_reason,
            )
            if will_retry:
                tls_context = _build_tls12_context()
                continue
            raise


async def _request_completion_endpoint(
    profile: LLMProfile,
    payload: dict[str, object],
    *,
    endpoint_kind: Literal["chat_completions", "responses"],
    extra_body: dict[str, object] | None = None,
    allow_empty_content: bool = False,
) -> ChatCompletionResult:
    from .wire import merge_extra_body

    chat_payload = merge_extra_body(payload, extra_body)
    base_url = resolve_base_url(profile.api_base_url)
    if endpoint_kind == "chat_completions":
        url = build_endpoint_url(base_url, "chat/completions")
        request_body = build_chat_completions_payload(chat_payload)
        content_extractor = extract_chat_completion_content
    else:
        url = build_endpoint_url(base_url, "responses")
        request_body = build_responses_payload(chat_payload)
        content_extractor = extract_responses_content

    timeout_seconds = get_settings().llm_request_timeout_seconds
    timeout = httpx.Timeout(timeout_seconds)
    headers = {
        "Authorization": f"Bearer {profile.api_key}",
        "Content-Type": "application/json",
    }
    start = perf_counter()
    try:
        response = await _send_llm_http_request(
            method="POST",
            profile=profile,
            url=url,
            endpoint_kind=endpoint_kind,
            headers=headers,
            timeout=timeout,
            json_body=request_body,
        )
    except (ImportError, ValueError) as exc:
        raise LLMRuntimeError(
            format_llm_client_initialization_error(exc),
            request_url=url,
            endpoint_kind=endpoint_kind,
            duration_ms=compute_duration_ms(start),
        ) from exc
    except httpx.TimeoutException as exc:
        raise LLMRuntimeError(
            f"模型请求超时（{timeout_seconds} 秒）",
            request_url=url,
            endpoint_kind=endpoint_kind,
            duration_ms=compute_duration_ms(start),
        ) from exc
    except (httpx.HTTPError, ssl.SSLError) as exc:
        raise LLMRuntimeError(
            format_llm_runtime_error_for_user(f"模型请求失败: {exc}"),
            request_url=url,
            endpoint_kind=endpoint_kind,
            duration_ms=compute_duration_ms(start),
        ) from exc

    duration_ms = compute_duration_ms(start)
    if response.status_code in (404, 405, 501):
        raise LLMEndpointProtocolError(
            format_http_error(response.status_code, response.text, url),
            failed_endpoint_kind=endpoint_kind,
            response_envelope=None,
            request_url=url,
            status_code=response.status_code,
            duration_ms=duration_ms,
        )
    if not 200 <= response.status_code < 300:
        raise LLMRuntimeError(
            format_http_error(response.status_code, response.text, url),
            request_url=url,
            endpoint_kind=endpoint_kind,
            status_code=response.status_code,
            duration_ms=duration_ms,
        )

    try:
        data = response.json()
    except (TypeError, ValueError) as exc:
        raise LLMEndpointProtocolError(
            "模型响应缺少有效的 JSON 外壳",
            failed_endpoint_kind=endpoint_kind,
            response_envelope="invalid",
            request_url=url,
            status_code=response.status_code,
            duration_ms=duration_ms,
        ) from exc

    from .wire import classify_response_envelope

    response_envelope = classify_response_envelope(endpoint_kind, data)
    if response_envelope != "valid":
        raise LLMEndpointProtocolError(
            "模型响应与请求端点协议不匹配"
            if response_envelope == "other_endpoint"
            else "模型响应缺少有效的端点外壳",
            failed_endpoint_kind=endpoint_kind,
            response_envelope=response_envelope,
            request_url=url,
            status_code=response.status_code,
            duration_ms=duration_ms,
        )

    if not isinstance(data, dict):
        raise LLMEndpointProtocolError(
            "模型响应缺少有效的端点外壳",
            failed_endpoint_kind=endpoint_kind,
            response_envelope="invalid",
            request_url=url,
            status_code=response.status_code,
            duration_ms=duration_ms,
        )

    try:
        content = content_extractor(data)
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        raise LLMRuntimeError(
            "模型响应缺少可解析的文本内容",
            request_url=url,
            endpoint_kind=endpoint_kind,
            status_code=response.status_code,
            duration_ms=duration_ms,
        ) from exc

    if not isinstance(content, str) or not content.strip():
        if allow_empty_content:
            # 测活路径用：思考模型可能把回答放在 reasoning_content 字段，
            # content 为空字符串。这种情况视为"模型可达"，不抛错。
            content = "" if not isinstance(content, str) else content
        else:
            raise LLMEmptyContentError(
                _empty_content_error_message(profile, data, endpoint_kind),
                request_url=url,
                endpoint_kind=endpoint_kind,
                status_code=response.status_code,
                duration_ms=duration_ms,
            )

    return ChatCompletionResult(
        content=content,
        usage=parse_completion_usage(data.get("usage")),
        request_url=url,
        attempted_urls=[url],
        endpoint_kind=endpoint_kind,
        status_code=response.status_code,
        duration_ms=duration_ms,
    )
