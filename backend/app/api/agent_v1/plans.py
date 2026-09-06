from __future__ import annotations

from collections.abc import Callable

from fastapi import APIRouter, Depends, Header

from app.core.database import get_session_factory
from app.modules.community.public import CommunityMentorDataService
from app.schemas.agent import (
    AgentActionPlanRead,
    AgentChangePlanRead,
    AgentPlanExecuteRequest,
)
from app.services.agent_action_plans import (
    cancel_email_action_plan,
    execute_email_action_plan,
    get_email_action_plan,
)
from app.services.agent_change_plans import (
    cancel_change_plan,
    execute_change_plan,
    get_change_plan,
)
from app.services.agent_mutations import execute_agent_factory_mutation

from .support import (
    get_agent_community_mentor_data_service,
)

router = APIRouter()


def get_agent_community_mentor_data_service_factory() -> Callable[
    [], CommunityMentorDataService
]:
    return get_agent_community_mentor_data_service


@router.get(
    "/plans/{plan_id}", response_model=AgentActionPlanRead | AgentChangePlanRead
)
async def read_agent_action_plan(
    plan_id: str,
) -> AgentActionPlanRead | AgentChangePlanRead:
    if plan_id.startswith("change_"):
        return await get_change_plan(get_session_factory(), plan_id)
    return await get_email_action_plan(get_session_factory(), plan_id)


@router.post(
    "/plans/{plan_id}/execute",
    response_model=AgentActionPlanRead | AgentChangePlanRead,
)
async def execute_agent_action_plan(
    plan_id: str,
    payload: AgentPlanExecuteRequest,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    community_service_factory: Callable[[], CommunityMentorDataService] = Depends(
        get_agent_community_mentor_data_service_factory,
    ),
) -> AgentActionPlanRead | AgentChangePlanRead:
    if plan_id.startswith("change_"):
        return await execute_agent_factory_mutation(
            get_session_factory(),
            command="plans.execute",
            request_data={"plan_id": plan_id, **payload.model_dump(mode="json")},
            idempotency_key=idempotency_key,
            response_type=AgentChangePlanRead,
            mutation=lambda: execute_change_plan(
                get_session_factory(),
                plan_id,
                payload,
                community_service_factory=community_service_factory,
            ),
            external_execution=True,
        )
    return await execute_agent_factory_mutation(
        get_session_factory(),
        command="plans.execute",
        request_data={"plan_id": plan_id, **payload.model_dump(mode="json")},
        idempotency_key=idempotency_key,
        response_type=AgentActionPlanRead,
        mutation=lambda: execute_email_action_plan(
            get_session_factory(), plan_id, payload
        ),
        external_execution=True,
    )


@router.post(
    "/plans/{plan_id}/cancel",
    response_model=AgentActionPlanRead | AgentChangePlanRead,
)
async def cancel_agent_action_plan(
    plan_id: str,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> AgentActionPlanRead | AgentChangePlanRead:
    if plan_id.startswith("change_"):
        return await execute_agent_factory_mutation(
            get_session_factory(),
            command="plans.cancel",
            request_data={"plan_id": plan_id},
            idempotency_key=idempotency_key,
            response_type=AgentChangePlanRead,
            mutation=lambda: cancel_change_plan(get_session_factory(), plan_id),
        )
    return await execute_agent_factory_mutation(
        get_session_factory(),
        command="plans.cancel",
        request_data={"plan_id": plan_id},
        idempotency_key=idempotency_key,
        response_type=AgentActionPlanRead,
        mutation=lambda: cancel_email_action_plan(get_session_factory(), plan_id),
    )
