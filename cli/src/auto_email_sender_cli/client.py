from __future__ import annotations

from typing import Any

import httpx

from auto_email_sender_cli.errors import CliError, RuntimeUnavailableError
from auto_email_sender_cli.runtime import (
    RuntimeDescriptor,
    ensure_runtime_descriptor,
    ensure_runtime_protocol_compatible,
)


class AgentApiClient:
    def __init__(
        self,
        descriptor: RuntimeDescriptor | None = None,
        *,
        timeout: float = 30.0,
    ) -> None:
        self._refresh_runtime_on_failure = descriptor is None
        self.descriptor = ensure_runtime_protocol_compatible(
            descriptor or ensure_runtime_descriptor(),
        )
        self.timeout = timeout

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
    ) -> Any:
        response = self._perform_request(
            method,
            path,
            params=params,
            json_body=json_body,
            data=data,
            files=files,
            idempotency_key=idempotency_key,
            accept="application/json",
        )
        if response.is_success:
            if response.status_code == 204:
                return None
            return response.json()

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
        accept: str,
    ) -> httpx.Response:
        headers = {
            "Authorization": f"Bearer {self.descriptor.access_token}",
            "Accept": accept,
        }
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key
        response: httpx.Response | None = None
        last_network_error: httpx.HTTPError | None = None
        for attempt in range(2):
            headers["Authorization"] = f"Bearer {self.descriptor.access_token}"
            try:
                response = httpx.request(
                    method,
                    f"{self.descriptor.base_url.rstrip('/')}/{path.lstrip('/')}",
                    params=params,
                    json=json_body,
                    data=data,
                    files=files,
                    headers=headers,
                    timeout=self.timeout,
                )
            except httpx.HTTPError as exc:
                last_network_error = exc
                if attempt == 0 and self._refresh_runtime_on_failure:
                    self.descriptor = ensure_runtime_descriptor()
                    continue
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
                refreshed = ensure_runtime_descriptor()
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


def _runtime_identity(descriptor: RuntimeDescriptor) -> tuple[str, str, int]:
    return (
        descriptor.base_url,
        descriptor.access_token,
        descriptor.desktop_pid,
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
    exit_code = 8 if response.status_code in {401, 403} else 5
    if response.status_code == 404:
        exit_code = 4
    elif response.status_code >= 500:
        exit_code = 9
    raise CliError(
        code=code,
        message=message,
        exit_code=exit_code,
        retryable=retryable,
        details=details if isinstance(details, dict) else {},
        suggested_command=suggested_command,
    )
