from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import TypeVar

from fastapi import APIRouter, Depends, Header, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.agent_api_errors import AgentApiError
from app.core.database import get_async_session, get_session_factory
from app.modules.communications.public import (
    TestComposeDraftUpdateRequest,
    TestComposeGenerateRequest,
    TestComposeMessageSendRequest,
    TestComposeStatusRead,
    TestComposeThreadRead,
    build_test_compose_thread,
    generate_test_compose_draft,
    get_test_compose_status,
    save_test_compose_draft,
)
from app.modules.llm.public import LLMRuntimeError
from app.schemas.agent import AgentChangePlanRead
from app.services.agent_change_plans import create_test_email_send_change_plan
from app.services.agent_mutations import (
    execute_agent_factory_mutation,
    execute_agent_mutation,
)

TestEmailActionResult = TypeVar("TestEmailActionResult")


router = APIRouter()


@router.get("/test-email/{identity_id}/status", response_model=TestComposeStatusRead)
async def get_agent_test_email_status(
    identity_id: int,
    session: AsyncSession = Depends(get_async_session),
) -> TestComposeStatusRead:
    return await _run_agent_test_email_action(
        lambda: get_test_compose_status(session, identity_id=identity_id),
    )


@router.get(
    "/test-email/{identity_id}/{llm_profile_id}", response_model=TestComposeThreadRead
)
async def get_agent_test_email_thread(
    identity_id: int,
    llm_profile_id: int,
    session: AsyncSession = Depends(get_async_session),
) -> TestComposeThreadRead:
    return await _run_agent_test_email_action(
        lambda: build_test_compose_thread(
            session,
            identity_id=identity_id,
            llm_profile_id=llm_profile_id,
        ),
    )


@router.post(
    "/test-email/{identity_id}/{llm_profile_id}/generate-draft",
    response_model=TestComposeThreadRead,
)
async def generate_agent_test_email_draft(
    identity_id: int,
    llm_profile_id: int,
    payload: TestComposeGenerateRequest | None = None,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    session: AsyncSession = Depends(get_async_session),
) -> TestComposeThreadRead:
    request_data = {
        "identity_id": identity_id,
        "llm_profile_id": llm_profile_id,
        "payload": payload.model_dump(mode="json", exclude_unset=True)
        if payload
        else {},
    }

    async def mutation() -> TestComposeThreadRead:
        async with get_session_factory()() as mutation_session:
            result = await generate_test_compose_draft(
                mutation_session,
                identity_id=identity_id,
                llm_profile_id=llm_profile_id,
                outreach_template_id=(
                    payload.outreach_template_id if payload else None
                ),
                template_selection_explicit=(
                    payload is not None
                    and "outreach_template_id" in payload.model_fields_set
                ),
                subject_template=(payload.subject if payload else None),
                body_text_template=(payload.body_text if payload else None),
                body_html_template=(payload.body_html if payload else None),
                template_content_explicit=(
                    payload is not None
                    and bool(
                        {"subject", "body_text", "body_html"} & payload.model_fields_set
                    )
                ),
                commit=False,
                event_name="agent_cli.test_email.draft_generated",
                actor="agent_cli",
            )
            await mutation_session.commit()
            return result

    return await _run_agent_test_email_action(
        lambda: execute_agent_factory_mutation(
            get_session_factory(),
            command="test-email.generate",
            request_data=request_data,
            idempotency_key=idempotency_key,
            response_type=TestComposeThreadRead,
            mutation=mutation,
            external_execution=True,
        ),
    )


@router.put(
    "/test-email/{identity_id}/{llm_profile_id}/draft",
    response_model=TestComposeThreadRead,
)
async def save_agent_test_email_draft(
    identity_id: int,
    llm_profile_id: int,
    payload: TestComposeDraftUpdateRequest,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    session: AsyncSession = Depends(get_async_session),
) -> TestComposeThreadRead:
    return await _run_agent_test_email_action(
        lambda: execute_agent_mutation(
            session,
            command="test-email.save",
            request_data={
                "identity_id": identity_id,
                "llm_profile_id": llm_profile_id,
                "payload": payload.model_dump(mode="json", exclude_unset=True),
            },
            idempotency_key=idempotency_key,
            response_type=TestComposeThreadRead,
            mutation=lambda: save_test_compose_draft(
                session,
                identity_id=identity_id,
                llm_profile_id=llm_profile_id,
                payload=payload,
                commit=False,
                event_name="agent_cli.test_email.draft_saved",
                actor="agent_cli",
            ),
        ),
    )


@router.post(
    "/test-email/{identity_id}/{llm_profile_id}/prepare-send",
    response_model=AgentChangePlanRead,
    status_code=status.HTTP_201_CREATED,
)
async def prepare_agent_test_email_send(
    identity_id: int,
    llm_profile_id: int,
    payload: TestComposeMessageSendRequest,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> AgentChangePlanRead:
    return await create_test_email_send_change_plan(
        get_session_factory(),
        identity_id,
        llm_profile_id,
        payload,
        idempotency_key=idempotency_key,
    )


async def _run_agent_test_email_action(
    action: Callable[[], Awaitable[TestEmailActionResult]],
) -> TestEmailActionResult:
    try:
        return await action()
    except LLMRuntimeError as exc:
        raise AgentApiError(
            status_code=502,
            code="TEST_EMAIL_LLM_FAILED",
            message=str(exc),
            external_execution_unknown=True,
        ) from exc
    except ValueError as exc:
        message = str(exc)
        raise AgentApiError(
            status_code=404 if "未找到" in message else 400,
            code="TEST_EMAIL_OPERATION_REJECTED",
            message=message,
        ) from exc
