"""Explicit dispatch for plan actions with uniform transactional results."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.agent_api_errors import AgentApiError
from app.models import AgentChangePlan
from app.modules.campaigns.public import (
    execute_campaign_create_snapshot,
    execute_campaign_restore_send_snapshot,
    execute_campaign_resume_snapshot,
    execute_campaign_send_snapshot,
)
from app.modules.community.public import CommunityDataError, CommunityMentorDataService

from .community import _community_import_error, _execute_community_mentor_import
from .crawler import _execute_crawl_candidate_approval, _execute_crawl_job_retry
from .materials import _execute_material_delete
from .professors import (
    _execute_professor_bulk_archive,
    _execute_professor_bulk_tags,
    _execute_professor_import,
    _execute_professor_tag_delete,
)
from .templates import _execute_template_archive
from .test_email import _execute_test_email_send


@dataclass(frozen=True)
class PlanActionResult:
    data: dict[str, object]
    file_path_to_delete: str | None = None


PlanHandler = Callable[[AsyncSession, AgentChangePlan], Awaitable[dict[str, object]]]
SnapshotHandler = Callable[
    [AsyncSession, dict[str, object]], Awaitable[dict[str, object]]
]

_PLAN_HANDLERS: dict[str, PlanHandler] = {
    "template.archive": _execute_template_archive,
    "professor.tags.bulk": _execute_professor_bulk_tags,
    "professor.archive.bulk": _execute_professor_bulk_archive,
    "professor.tag.delete": _execute_professor_tag_delete,
    "professor.import": _execute_professor_import,
    "test_email.send": _execute_test_email_send,
    "crawler.candidates.approve": _execute_crawl_candidate_approval,
    "crawler.job.retry": _execute_crawl_job_retry,
}
_SNAPSHOT_HANDLERS: dict[str, SnapshotHandler] = {
    "campaign.create": execute_campaign_create_snapshot,
    "campaign.send": execute_campaign_send_snapshot,
    "campaign.resume": execute_campaign_resume_snapshot,
    "campaign.item_send_restore": execute_campaign_restore_send_snapshot,
}


async def execute_plan_action(
    session: AsyncSession,
    plan: AgentChangePlan,
    *,
    community_service: CommunityMentorDataService | None = None,
    community_service_factory: Callable[[], CommunityMentorDataService] | None = None,
) -> PlanActionResult:
    if handler := _PLAN_HANDLERS.get(plan.action):
        return PlanActionResult(await handler(session, plan))
    if snapshot_handler := _SNAPSHOT_HANDLERS.get(plan.action):
        return PlanActionResult(await snapshot_handler(session, plan.snapshot))
    if plan.action == "material.delete":
        data, path = await _execute_material_delete(session, plan)
        return PlanActionResult(data, path)
    if plan.action == "community_mentor.import":
        try:
            service = community_service or (
                community_service_factory()
                if community_service_factory is not None
                else CommunityMentorDataService()
            )
        except CommunityDataError as exc:
            raise _community_import_error(exc) from exc
        return PlanActionResult(
            await _execute_community_mentor_import(session, plan, service)
        )
    raise AgentApiError(
        status_code=500,
        code="UNSUPPORTED_CHANGE_PLAN_ACTION",
        message="该变更计划的动作类型不受支持。",
    )
