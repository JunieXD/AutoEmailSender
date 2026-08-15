from __future__ import annotations

from typing import Any

import httpx

from auto_email_sender_cli.errors import (
    CliError,
    ExternalExecutionUnknownError,
    RuntimeUnavailableError,
)
from auto_email_sender_cli.runtime import (
    RuntimeDescriptor,
    create_runtime_http_client,
    ensure_runtime_descriptor,
    ensure_runtime_protocol_compatible,
)


class AgentApiClient:
    def __init__(
        self,
        descriptor: RuntimeDescriptor | None = None,
        *,
        timeout: float = 30.0,
        http_client: httpx.Client | None = None,
    ) -> None:
        self._refresh_runtime_on_failure = descriptor is None
        self.timeout = timeout
        self._http_client = (
            http_client
            if http_client is not None
            else create_runtime_http_client(
                base_url=descriptor.base_url if descriptor is not None else None,
                timeout=timeout,
            )
        )
        self._owns_http_client = http_client is None
        try:
            self.descriptor = ensure_runtime_protocol_compatible(
                descriptor or ensure_runtime_descriptor(http_client=self._http_client),
            )
        except Exception:
            if self._owns_http_client:
                self._http_client.close()
            raise
        self.last_request_id: str | None = None
        self.last_response_status: int | None = None
        self.last_response_headers: dict[str, str] = {}

    def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, object] | None = None,
        json_body: object | None = None,
        data: dict[str, object] | None = None,
        files: Any | None = None,
        idempotency_key: str | None = None,
        if_revision: str | None = None,
        request_timeout: float | None = None,
    ) -> Any:
        response = self._perform_request(
            method,
            path,
            params=params,
            json_body=json_body,
            data=data,
            files=files,
            idempotency_key=idempotency_key,
            if_revision=if_revision,
            accept="application/json",
            request_timeout=request_timeout,
        )
        if response.is_success:
            self._record_response_metadata(response)
            if response.status_code == 204:
                return None
            try:
                return response.json()
            except ValueError as exc:
                raise CliError(
                    code="INVALID_API_RESPONSE",
                    message="本地服务返回了无法解析的 JSON 响应。",
                    exit_code=8,
                    retryable=False,
                    details={"status_code": response.status_code},
                ) from exc

        _raise_api_error(response)

    def download_bytes(
        self,
        path: str,
        *,
        params: dict[str, object] | None = None,
    ) -> bytes:
        response = self._perform_request(
            "GET",
            path,
            params=params,
            accept="application/octet-stream",
        )
        if response.is_success:
            return response.content

        _raise_api_error(response)

    def _perform_request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, object] | None = None,
        json_body: object | None = None,
        data: dict[str, object] | None = None,
        files: Any | None = None,
        idempotency_key: str | None = None,
        if_revision: str | None = None,
        accept: str,
        request_timeout: float | None = None,
    ) -> httpx.Response:
        headers = {
            "Authorization": f"Bearer {self.descriptor.access_token}",
            "Accept": accept,
        }
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key
        if if_revision:
            headers["If-Revision"] = if_revision
        response: httpx.Response | None = None
        last_network_error: httpx.HTTPError | None = None
        safe_method = method.upper() in {"GET", "HEAD", "OPTIONS"}
        for attempt in range(2):
            headers["Authorization"] = f"Bearer {self.descriptor.access_token}"
            try:
                response = self._http_client.request(
                    method,
                    f"{self.descriptor.base_url.rstrip('/')}/{path.lstrip('/')}",
                    params=params,
                    json=json_body,
                    data=data,
                    files=files,
                    headers=headers,
                    timeout=request_timeout if request_timeout is not None else self.timeout,
                )
            except httpx.HTTPError as exc:
                last_network_error = exc
                if (
                    attempt == 0
                    and self._refresh_runtime_on_failure
                    and safe_method
                    and isinstance(exc, (httpx.ConnectError, httpx.ConnectTimeout))
                ):
                    self.descriptor = ensure_runtime_protocol_compatible(
                        ensure_runtime_descriptor(http_client=self._http_client),
                    )
                    continue
                if not safe_method and not isinstance(
                    exc,
                    (httpx.ConnectError, httpx.ConnectTimeout),
                ):
                    raise ExternalExecutionUnknownError(
                        command=path,
                        request_id=idempotency_key,
                    ) from exc
                raise RuntimeUnavailableError(
                    f"无法连接 Auto Email Sender 本地服务：{exc}。"
                    "请确认软件已手动打开并完成加载后重试。"
                ) from exc

            if (
                response.status_code in {401, 403}
                and attempt == 0
                and self._refresh_runtime_on_failure
            ):
                previous_runtime = _runtime_identity(self.descriptor)
                refreshed = ensure_runtime_protocol_compatible(
                    ensure_runtime_descriptor(http_client=self._http_client),
                )
                if _runtime_identity(refreshed) != previous_runtime:
                    self.descriptor = refreshed
                    continue
            break

        if response is None:
            raise RuntimeUnavailableError(
                f"无法连接 Auto Email Sender 本地服务：{last_network_error}。"
                "请确认软件已手动打开并完成加载后重试。"
            )
        return response

    def close(self) -> None:
        if self._owns_http_client:
            self._http_client.close()

    def __enter__(self) -> AgentApiClient:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def _record_response_metadata(self, response: httpx.Response) -> None:
        self.last_response_status = response.status_code
        self.last_response_headers = {
            key.lower(): value
            for key, value in response.headers.items()
            if key.lower()
            in {
                "x-request-id",
                "x-agent-mutation-receipt",
                "x-agent-mutation-status",
                "x-agent-mutation-command",
                "x-audit-reference",
            }
        }
        self.last_request_id = self.last_response_headers.get("x-request-id")


def _runtime_identity(descriptor: RuntimeDescriptor) -> tuple[str, str, str]:
    return (
        descriptor.base_url,
        descriptor.access_token,
        descriptor.runtime_id,
    )

def _raise_api_error(response: httpx.Response) -> None:
    try:
        payload = response.json()
    except ValueError:
        payload = {}
    error = payload.get("error") if isinstance(payload, dict) else None
    detail = payload.get("detail") if isinstance(payload, dict) else None
    if isinstance(error, dict):
        code = str(error.get("code") or f"HTTP_{response.status_code}")
        message = str(error.get("message") or detail or "本地服务请求失败")
        retryable = bool(error.get("retryable", response.status_code >= 500))
        details = error.get("details")
        suggested = error.get("suggested_action")
        suggested_command = (
            str(suggested.get("command"))
            if isinstance(suggested, dict) and suggested.get("command")
            else None
        )
    else:
        code = f"HTTP_{response.status_code}"
        message = str(detail or "本地服务请求失败")
        retryable = response.status_code >= 500
        details = {}
        suggested_command = None
    exit_code = _exit_code_for_api_error(
        status_code=response.status_code,
        code=code,
    )
    raise CliError(
        code=code,
        message=message,
        exit_code=exit_code,
        retryable=retryable,
        details=details if isinstance(details, dict) else {},
        suggested_command=suggested_command,
    )


def _exit_code_for_api_error(*, status_code: int, code: str) -> int:
    """Map the stable Agent error contract to CLI exit codes.

    HTTP status codes describe transport semantics, while an Agent needs the
    product-level action to take next.  In particular, a 409 can mean either a
    normal state conflict (exit 5) or a request for explicit confirmation
    (exit 6), and a 422 is an argument error (exit 2), not a generic conflict.
    """

    normalized = code.upper()
    if normalized in {
        "APP_UNAVAILABLE",
        "RUNTIME_UNAVAILABLE",
        "RUNTIME_PROTOCOL_MISMATCH",
    }:
        return 7
    if status_code in {401, 403} or normalized in {
        "AUTH_REQUIRED",
        "INVALID_ACCESS_TOKEN",
        "TOKEN_SCOPE_FORBIDDEN",
        "INVALID_AGENT_TOKEN",
    }:
        return 8
    if normalized == "PLAN_CONFIRMATION_REQUIRED" or normalized.endswith(
        "_CONFIRMATION_REQUIRED",
    ):
        return 6
    if normalized in {
        "INVALID_ARGUMENT",
        "INVALID_AGENT_REQUEST",
        "INVALID_FILTER",
        "INVALID_FIELD_SELECTION",
        "INVALID_GUIDE_TOPIC",
        "INVALID_IDEMPOTENCY_KEY",
        "COMMAND_NOT_FOUND",
        "CAPABILITY_NOT_FOUND",
    } or status_code == 422:
        return 2
    if normalized in {
        "PARTIAL_SUCCESS",
        "PARTIALLY_SUCCEEDED",
        "PARTIALLY_COMPLETED",
    }:
        return 10
    if status_code == 404:
        return 4
    if normalized in {
        "EXTERNAL_EXECUTION_UNKNOWN",
        "SMTP_ERROR",
        "IMAP_ERROR",
        "LLM_ERROR",
        "CRAWLER_ERROR",
        "EXTERNAL_SERVICE_ERROR",
    } or status_code >= 500:
        return 9
    return 5
