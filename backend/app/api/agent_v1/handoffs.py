from __future__ import annotations

from fastapi import APIRouter, status
from fastapi.responses import Response

from app.core.database import get_session_factory
from app.schemas.agent import (
    AgentUiHandoffAcknowledgeRequest,
    AgentUiHandoffClaimRead,
    AgentUiHandoffClaimRequest,
    AgentUiHandoffRead,
)
from app.services.agent_ui_handoffs import (
    acknowledge_ui_handoff,
    cancel_ui_handoff,
    claim_next_ui_handoff,
    get_ui_handoff,
    retry_ui_handoff,
)

router = APIRouter()


@router.get("/ui-handoffs/{handoff_id}", response_model=AgentUiHandoffRead)
async def read_agent_ui_handoff(handoff_id: str) -> AgentUiHandoffRead:
    return await get_ui_handoff(get_session_factory(), handoff_id)


@router.post("/ui-handoffs/{handoff_id}/cancel", response_model=AgentUiHandoffRead)
async def cancel_agent_ui_handoff(handoff_id: str) -> AgentUiHandoffRead:
    return await cancel_ui_handoff(get_session_factory(), handoff_id)


@router.post("/ui-handoffs/{handoff_id}/retry", response_model=AgentUiHandoffRead)
async def retry_agent_ui_handoff(handoff_id: str) -> AgentUiHandoffRead:
    return await retry_ui_handoff(get_session_factory(), handoff_id)


@router.post(
    "/ui-handoffs/claim-next",
    response_model=AgentUiHandoffClaimRead,
    responses={status.HTTP_204_NO_CONTENT: {"description": "没有待交付界面状态"}},
)
async def claim_agent_ui_handoff(
    payload: AgentUiHandoffClaimRequest,
) -> AgentUiHandoffClaimRead | Response:
    handoff = await claim_next_ui_handoff(
        get_session_factory(),
        payload.consumer_id,
    )
    if handoff is None:
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    return handoff


@router.post(
    "/ui-handoffs/{handoff_id}/acknowledge",
    response_model=AgentUiHandoffRead,
)
async def acknowledge_agent_ui_handoff(
    handoff_id: str,
    payload: AgentUiHandoffAcknowledgeRequest,
) -> AgentUiHandoffRead:
    return await acknowledge_ui_handoff(
        get_session_factory(),
        handoff_id,
        payload,
    )
