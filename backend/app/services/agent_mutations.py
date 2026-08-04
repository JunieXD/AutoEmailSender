from __future__ import annotations

import hashlib
import json
import secrets
from collections.abc import Awaitable, Callable
from typing import TypeVar

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.agent_api_errors import AgentApiError
from app.models import AgentMutationReceipt


MutationResponse = TypeVar("MutationResponse", bound=BaseModel)


async def execute_agent_mutation(
    session: AsyncSession,
    *,
    command: str,
    request_data: object,
    idempotency_key: str | None,
    response_type: type[MutationResponse],
    mutation: Callable[[], Awaitable[MutationResponse]],
) -> MutationResponse:
    """Execute one Agent write atomically and replay its prior result when retried."""

    normalized_key = normalize_idempotency_key(idempotency_key)
    request_fingerprint = fingerprint({"command": command, "request": request_data})
    if normalized_key is not None:
        existing = await session.scalar(
            select(AgentMutationReceipt).where(
                AgentMutationReceipt.idempotency_key == normalized_key,
            ),
        )
        if existing is not None:
            ensure_same_idempotent_request(
                existing,
                command=command,
                request_fingerprint=request_fingerprint,
            )
            return response_type.model_validate(existing.response)

    response = await mutation()
    if normalized_key is not None:
        session.add(
            AgentMutationReceipt(
                id=new_mutation_receipt_id(),
                command=command,
                idempotency_key=normalized_key,
                request_fingerprint=request_fingerprint,
                response=response.model_dump(mode="json"),
            ),
        )
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        if normalized_key is not None:
            existing = await session.scalar(
                select(AgentMutationReceipt).where(
                    AgentMutationReceipt.idempotency_key == normalized_key,
                ),
            )
            if existing is not None:
                ensure_same_idempotent_request(
                    existing,
                    command=command,
                    request_fingerprint=request_fingerprint,
                )
                return response_type.model_validate(existing.response)
        raise AgentApiError(
            status_code=409,
            code="AGENT_MUTATION_CONFLICT",
            message="操作发生冲突，请重新读取数据后再试。",
            retryable=True,
        ) from exc
    return response


def normalize_idempotency_key(value: str | None) -> str | None:
    normalized = (value or "").strip()
    if not normalized:
        return None
    if len(normalized) > 160:
        raise AgentApiError(
            status_code=400,
            code="INVALID_IDEMPOTENCY_KEY",
            message="Idempotency-Key 不能超过 160 个字符。",
        )
    return normalized


def fingerprint(value: object) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def ensure_same_idempotent_request(
    receipt: AgentMutationReceipt,
    *,
    command: str,
    request_fingerprint: str,
) -> None:
    if (
        receipt.command != command
        or receipt.request_fingerprint != request_fingerprint
    ):
        raise AgentApiError(
            status_code=409,
            code="IDEMPOTENCY_KEY_REUSED",
            message="同一个 Idempotency-Key 已用于不同的操作请求。",
        )


def new_mutation_receipt_id() -> str:
    return f"mutation_{secrets.token_urlsafe(18)}"
