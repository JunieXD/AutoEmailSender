from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_async_session
from app.modules.llm.public import LLMRuntimeError
from .schemas import (
    TestComposeDraftUpdateRequest,
    TestComposeGenerateRequest,
    TestComposeMessageSendRequest,
    TestComposeStatusRead,
    TestComposeThreadRead,
)
from .runtime import (
    build_test_compose_thread,
    generate_test_compose_draft,
    get_test_compose_status,
    save_test_compose_draft,
    send_test_compose_message,
)


router = APIRouter(prefix="/api/test-compose", tags=["test-compose"])


@router.get("/{identity_id}/status", response_model=TestComposeStatusRead)
async def get_test_compose_identity_status(
    identity_id: int,
    session: AsyncSession = Depends(get_async_session),
) -> TestComposeStatusRead:
    return await _run_test_compose_action(
        session,
        lambda: get_test_compose_status(
            session,
            identity_id=identity_id,
        ),
    )


@router.get("/{identity_id}/{llm_profile_id}", response_model=TestComposeThreadRead)
async def get_test_compose_thread(
    identity_id: int,
    llm_profile_id: int,
    session: AsyncSession = Depends(get_async_session),
) -> TestComposeThreadRead:
    return await _run_test_compose_action(
        session,
        lambda: build_test_compose_thread(
            session,
            identity_id=identity_id,
            llm_profile_id=llm_profile_id,
        ),
    )


@router.post("/{identity_id}/{llm_profile_id}/generate-draft", response_model=TestComposeThreadRead)
async def generate_test_compose(
    identity_id: int,
    llm_profile_id: int,
    payload: TestComposeGenerateRequest | None = None,
    session: AsyncSession = Depends(get_async_session),
) -> TestComposeThreadRead:
    return await _run_test_compose_action(
        session,
        lambda: generate_test_compose_draft(
            session,
            identity_id=identity_id,
            llm_profile_id=llm_profile_id,
            outreach_template_id=(payload.outreach_template_id if payload else None),
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
                    {"subject", "body_text", "body_html"}
                    & payload.model_fields_set
                )
            ),
        ),
    )


@router.post("/{identity_id}/{llm_profile_id}/draft", response_model=TestComposeThreadRead)
async def update_test_compose_draft(
    identity_id: int,
    llm_profile_id: int,
    payload: TestComposeDraftUpdateRequest,
    session: AsyncSession = Depends(get_async_session),
) -> TestComposeThreadRead:
    return await _run_test_compose_action(
        session,
        lambda: save_test_compose_draft(
            session,
            identity_id=identity_id,
            llm_profile_id=llm_profile_id,
            payload=payload,
        ),
    )


@router.post("/{identity_id}/{llm_profile_id}/send", response_model=TestComposeThreadRead)
async def send_test_compose(
    identity_id: int,
    llm_profile_id: int,
    payload: TestComposeMessageSendRequest,
    session: AsyncSession = Depends(get_async_session),
) -> TestComposeThreadRead:
    return await _run_test_compose_action(
        session,
        lambda: send_test_compose_message(
            session,
            identity_id=identity_id,
            llm_profile_id=llm_profile_id,
            payload=payload,
        ),
    )


async def _run_test_compose_action(
    session: AsyncSession,
    action,
) -> TestComposeThreadRead:
    try:
        return await action()
    except LLMRuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except ValueError as exc:
        detail = str(exc)
        status_code = 404 if "未找到" in detail else 400
        raise HTTPException(status_code=status_code, detail=detail) from exc
