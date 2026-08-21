"""Optimistic-concurrency helpers for Agent API resources.

Revision tokens intentionally describe the user-visible resource rather than a
database row version.  This keeps them stable across the CLI and API while
remaining additive to existing response DTOs.
"""

from __future__ import annotations

import json
from hashlib import sha256
from typing import Any

from pydantic import BaseModel

from app.core.agent_api_errors import AgentApiError


def revision_for(value: BaseModel | dict[str, Any]) -> str:
    payload = (
        value.model_dump(mode="json") if isinstance(value, BaseModel) else dict(value)
    )
    payload.pop("revision", None)
    payload.pop("created_at", None)
    payload.pop("updated_at", None)
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return sha256(encoded).hexdigest()[:20]


def ensure_revision(
    expected: str | None,
    actual: str | None,
    *,
    resource: str,
    resource_id: int | str,
    latest: dict[str, Any] | None = None,
) -> None:
    if not expected:
        return
    if expected == actual:
        return
    raise AgentApiError(
        status_code=409,
        code="REVISION_CONFLICT",
        message="对象已被 GUI、其他 CLI 调用方或后台任务修改；为避免静默覆盖，本次写入已取消。",
        retryable=True,
        details={
            "resource": resource,
            "resource_id": str(resource_id),
            "expected_revision": expected,
            "actual_revision": actual,
            "latest": latest or {},
            "suggested_action": "重新读取对象，核对变更后使用最新 revision 重试。",
        },
    )
