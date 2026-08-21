from __future__ import annotations

import hashlib
import json
import secrets
from contextvars import ContextVar
from collections.abc import Awaitable, Callable
from typing import TypeVar

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy import delete
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.agent_api_errors import AgentApiError
from app.models import AgentMutationReceipt


MutationResponse = TypeVar("MutationResponse", bound=BaseModel)

_mutation_receipt_context: ContextVar[dict[str, str] | None] = ContextVar(
    "agent_mutation_receipt",
    default=None,
)


def _set_mutation_receipt(value: dict[str, str]) -> None:
    # Middleware installs a mutable box before dispatch.  Mutating that box is
    # visible across Starlette/AnyIO child task boundaries where replacing a
    # ContextVar value would not be propagated back to the response sender.
    current = _mutation_receipt_context.get()
    if current is not None:
        current.clear()
        current.update(value)
    else:
        _mutation_receipt_context.set(value)


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
            if _is_pending_response(existing.response):
                raise AgentApiError(
                    status_code=409,
                    code="MUTATION_RESULT_UNKNOWN",
                    message="同一操作仍处于结果未知状态；请先读取对应业务对象确认实际结果，不能重复执行。",
                    retryable=False,
                    details={"request_id": normalized_key, "receipt_id": existing.id},
                )
            _set_mutation_receipt(
                {
                    "id": existing.id,
                    "status": "replayed",
                    "command": existing.command,
                },
            )
            return response_type.model_validate(existing.response)

    response = await mutation()
    if normalized_key is not None:
        receipt = AgentMutationReceipt(
            id=new_mutation_receipt_id(),
            command=command,
            idempotency_key=normalized_key,
            request_fingerprint=request_fingerprint,
            response=response.model_dump(mode="json"),
        )
        session.add(receipt)
        _set_mutation_receipt(
            {
                "id": receipt.id,
                "status": "applied",
                "command": command,
            },
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
                if _is_pending_response(existing.response):
                    raise AgentApiError(
                        status_code=409,
                        code="MUTATION_RESULT_UNKNOWN",
                        message="同一操作仍处于结果未知状态；请先读取对应业务对象确认实际结果，不能重复执行。",
                        retryable=False,
                        details={
                            "request_id": normalized_key,
                            "receipt_id": existing.id,
                        },
                    )
                _set_mutation_receipt(
                    {
                        "id": existing.id,
                        "status": "replayed",
                        "command": existing.command,
                    },
                )
                return response_type.model_validate(existing.response)
        raise AgentApiError(
            status_code=409,
            code="AGENT_MUTATION_CONFLICT",
            message="操作发生冲突，请重新读取数据后再试。",
            retryable=True,
        ) from exc
    return response


async def execute_agent_factory_mutation(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    command: str,
    request_data: object,
    idempotency_key: str | None,
    response_type: type[MutationResponse],
    mutation: Callable[[], Awaitable[MutationResponse]],
    external_execution: bool = False,
) -> MutationResponse:
    """Idempotently wrap a service that owns its own database session.

    A durable placeholder is reserved before the service runs.  If the process
    or an external model call disappears after the service may have committed,
    a retry observes the placeholder and receives ``MUTATION_RESULT_UNKNOWN``
    instead of running the business mutation a second time.  The service result
    then replaces the placeholder with the normal replayable response.
    """

    normalized_key = normalize_idempotency_key(idempotency_key)
    if normalized_key is None:
        return await mutation()
    request_fingerprint = fingerprint({"command": command, "request": request_data})
    async with session_factory() as session:
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
            if _is_pending_response(existing.response):
                raise AgentApiError(
                    status_code=409,
                    code="MUTATION_RESULT_UNKNOWN",
                    message="同一操作仍处于结果未知状态；请先读取对应业务对象确认实际结果，不能重复执行。",
                    retryable=False,
                    details={"request_id": normalized_key, "receipt_id": existing.id},
                )
            _set_mutation_receipt(
                {"id": existing.id, "status": "replayed", "command": existing.command}
            )
            return _mark_replayed_response(
                response_type.model_validate(existing.response)
            )
        receipt = AgentMutationReceipt(
            id=new_mutation_receipt_id(),
            command=command,
            idempotency_key=normalized_key,
            request_fingerprint=request_fingerprint,
            response={"_pending": True},
        )
        session.add(receipt)
        try:
            await session.commit()
        except IntegrityError:
            await session.rollback()
            existing = await session.scalar(
                select(AgentMutationReceipt).where(
                    AgentMutationReceipt.idempotency_key == normalized_key,
                ),
            )
            if existing is None:
                raise AgentApiError(
                    status_code=409,
                    code="AGENT_MUTATION_CONFLICT",
                    message="操作发生冲突，请重新读取数据后再试。",
                    retryable=True,
                )
            ensure_same_idempotent_request(
                existing,
                command=command,
                request_fingerprint=request_fingerprint,
            )
            if _is_pending_response(existing.response):
                raise AgentApiError(
                    status_code=409,
                    code="MUTATION_RESULT_UNKNOWN",
                    message="同一操作仍处于结果未知状态；请先读取对应业务对象确认实际结果，不能重复执行。",
                    retryable=False,
                    details={"request_id": normalized_key, "receipt_id": existing.id},
                )
            _set_mutation_receipt(
                {"id": existing.id, "status": "replayed", "command": existing.command}
            )
            return _mark_replayed_response(
                response_type.model_validate(existing.response)
            )
        _set_mutation_receipt(
            {"id": receipt.id, "status": "pending", "command": command}
        )

    try:
        response = await mutation()
    except AgentApiError as exc:
        if exc.external_execution_unknown:
            raise _external_execution_unknown_error(normalized_key) from exc
        await _remove_pending_receipt(session_factory, normalized_key)
        raise
    except ValueError:
        # Validation/state failures happen before the external work is
        # considered executed.  Remove the reservation so the caller can fix
        # the request and safely reuse the same request id.
        await _remove_pending_receipt(session_factory, normalized_key)
        raise
    except Exception as exc:
        if external_execution:
            # The durable pending receipt deliberately remains.  The external
            # provider may have accepted the request before the process saw the
            # exception, so a retry with the same key must not execute again.
            raise _external_execution_unknown_error(normalized_key) from exc
        # This mutation has no external side effect.  A programming error,
        # database failure, or cancellation before the service returned must
        # not poison the caller's idempotency key forever: the next attempt
        # should be allowed to retry after the failure has been addressed.
        await _remove_pending_receipt(session_factory, normalized_key)
        raise
    async with session_factory() as session:
        stored = await session.scalar(
            select(AgentMutationReceipt).where(
                AgentMutationReceipt.idempotency_key == normalized_key,
            ),
        )
        if stored is None:
            raise AgentApiError(
                status_code=409,
                code="AGENT_MUTATION_CONFLICT",
                message="操作回执丢失，不能安全判断是否可重试。",
                retryable=False,
            )
        stored.response = response.model_dump(mode="json")
        await session.commit()
        _set_mutation_receipt(
            {"id": stored.id, "status": "applied", "command": command}
        )
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
    if receipt.command != command or receipt.request_fingerprint != request_fingerprint:
        raise AgentApiError(
            status_code=409,
            code="IDEMPOTENCY_KEY_REUSED",
            message="同一个 Idempotency-Key 已用于不同的操作请求。",
        )


def new_mutation_receipt_id() -> str:
    return f"mutation_{secrets.token_urlsafe(18)}"


def get_mutation_receipt_context() -> dict[str, str] | None:
    return _mutation_receipt_context.get()


def clear_mutation_receipt_context() -> None:
    _mutation_receipt_context.set(None)


def install_mutation_receipt_context_box() -> None:
    """Install a mutable request-local box for the receipt middleware."""

    _mutation_receipt_context.set({})


def _is_pending_response(value: object) -> bool:
    return isinstance(value, dict) and value.get("_pending") is True


def _mark_replayed_response(response: MutationResponse) -> MutationResponse:
    """Preserve plan replay semantics while keeping ordinary DTOs unchanged."""

    if "idempotent_replay" not in type(response).model_fields:
        return response
    return response.model_copy(update={"idempotent_replay": True})


def _external_execution_unknown_error(request_id: str | None) -> AgentApiError:
    return AgentApiError(
        status_code=502,
        code="EXTERNAL_EXECUTION_UNKNOWN",
        message="外部服务执行结果未知；请先读取同步结果，不能自动再次执行。",
        retryable=False,
        details={"request_id": request_id} if request_id else {},
    )


async def _remove_pending_receipt(
    session_factory: async_sessionmaker[AsyncSession],
    idempotency_key: str | None,
) -> None:
    if idempotency_key is None:
        return
    async with session_factory() as session:
        receipt = await session.scalar(
            select(AgentMutationReceipt).where(
                AgentMutationReceipt.idempotency_key == idempotency_key,
            ),
        )
        if receipt is None or not _is_pending_response(receipt.response):
            return
        await session.execute(
            delete(AgentMutationReceipt).where(AgentMutationReceipt.id == receipt.id)
        )
        await session.commit()
