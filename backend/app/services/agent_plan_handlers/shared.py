from __future__ import annotations

from app.core.agent_api_errors import AgentApiError
from app.services.agent_mutations import fingerprint


def _request_state_summary_fingerprint(snapshot: dict[str, object]) -> str:
    return fingerprint(
        {key: snapshot.get(key) for key in ("request", "state", "summary")}
    )


def _request_state_fingerprint(snapshot: dict[str, object]) -> str:
    return fingerprint(
        {
            "request": snapshot.get("request"),
            "state": snapshot.get("state"),
        },
    )


def _invalid_change_plan_snapshot_error() -> AgentApiError:
    return AgentApiError(
        status_code=500,
        code="INVALID_CHANGE_PLAN_SNAPSHOT",
        message="变更计划快照无效，请重新生成计划。",
    )
