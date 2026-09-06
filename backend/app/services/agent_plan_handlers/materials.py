from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.agent_api_errors import AgentApiError
from app.models import AgentChangePlan
from app.modules.identities.public import (
    MaterialMutationError,
    delete_identity_material_record,
)


async def _execute_material_delete(
    session: AsyncSession,
    plan: AgentChangePlan,
) -> tuple[dict[str, object], str | None]:
    snapshot = plan.snapshot
    material_id = snapshot.get("material_id")
    expected_fingerprint = snapshot.get("deletion_fingerprint")
    if not isinstance(material_id, int) or not isinstance(expected_fingerprint, str):
        raise AgentApiError(
            status_code=500,
            code="INVALID_CHANGE_PLAN_SNAPSHOT",
            message="变更计划快照无效，请重新生成计划。",
        )
    try:
        result = await delete_identity_material_record(
            session,
            material_id,
            event_name="agent_cli.material.deleted",
            actor="agent_cli",
            expected_fingerprint=expected_fingerprint,
        )
    except MaterialMutationError as exc:
        raise AgentApiError(
            status_code=409,
            code="PLAN_STALE",
            message="材料或其引用关系已发生变化，请重新生成删除预览。",
            details={"changed_fields": ["material", "material_references"]},
            suggested_command=f"auto-email-sender materials prepare-delete {material_id}",
        ) from exc
    return result.to_agent_result(), result.file_path


def _material_error(error: MaterialMutationError) -> AgentApiError:
    return AgentApiError(
        status_code=error.status_code,
        code=error.code,
        message=error.message,
        details=error.details or {},
    )
