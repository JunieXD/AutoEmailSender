"""Resolve lifecycle actions into directly invokable, bounded contracts.

The backend remains authoritative for state transitions.  This module only
turns an already-allowed action plus stable identifiers from the current DTO
into a concrete CLI command.  It never copies free-form external content into
an action input, so a message, webpage, log entry, or model output cannot turn
into an executable argument.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from auto_email_sender_cli.capabilities import get_capability
from auto_email_sender_cli.operation_specs import get_operation_spec


ActionLink = dict[str, object]


def resolve_action_links(
    command: str,
    item: Mapping[str, object],
    *,
    actions: Iterable[str],
    blocked_actions: Mapping[str, object] | None = None,
) -> tuple[list[ActionLink], dict[str, str]]:
    """Return executable links for allowed lifecycle actions.

    A link is emitted only when the current result carries every stable ID the
    target parser needs.  Otherwise the action is moved into ``blocked_actions``
    with a concrete reason instead of leaving an Agent to infer a command or
    fabricate an identifier.
    """

    blocked = _normalize_blocked_actions(blocked_actions)
    links: list[ActionLink] = []
    seen: set[str] = set()
    for raw_action in actions:
        action = raw_action.strip().lower()
        if not action or action in seen:
            continue
        seen.add(action)
        target = _resolve_target(command, item, action)
        if target is None:
            blocked.setdefault(action, "当前结果缺少生成该动作所需的稳定资源 ID。")
            continue
        target_command, arguments, required_input, execution_mode = target
        capability = get_capability(target_command)
        spec = get_operation_spec(target_command)
        if capability is None or capability.availability != "available" or spec is None:
            blocked.setdefault(action, "当前 CLI 未发布该动作对应的可执行命令。")
            continue
        link: ActionLink = {
            "action": action,
            "command": target_command,
            "arguments": arguments,
            "risk_level": capability.risk_level,
        }
        if required_input:
            link["required_input"] = required_input
        if execution_mode != "invoke":
            link["execution_mode"] = execution_mode
        if spec.effects.requires_confirmation_plan:
            link["confirmation_required_before_invocation"] = True
        if spec.effects.produces_confirmation_plan:
            link["produces_confirmation_plan"] = True
        if spec.effects.plan_role != "none":
            link["plan_role"] = spec.effects.plan_role
        links.append(link)
    return links, blocked


def _normalize_blocked_actions(
    value: Mapping[str, object] | None,
) -> dict[str, str]:
    if value is None:
        return {}
    result: dict[str, str] = {}
    for raw_action, raw_reason in value.items():
        if not isinstance(raw_action, str) or not raw_action.strip():
            continue
        action = raw_action.strip().lower()
        result[action] = str(raw_reason or "当前状态不允许该动作。")
    return result


def _resolve_target(
    source_command: str,
    item: Mapping[str, object],
    action: str,
) -> tuple[str, dict[str, object], list[str], str] | None:
    """Map a resource-specific action name to a registered leaf command."""

    source = source_command.strip().lower()
    plan_id = _string_identifier(item.get("plan_id"))
    if plan_id is not None:
        return _plan_target(action, plan_id)

    task_id = _positive_integer(item.get("task_id"))
    if task_id is not None or source.startswith(("drafts.", "tasks.", "workspaces.")):
        task_id = task_id or _positive_integer(item.get("id"))
        return _draft_target(action, task_id)

    if source.startswith("matching.jobs."):
        return _job_target(
            "matching.jobs",
            action,
            _job_identifier(item, nested=source.endswith(".items")),
        )
    if source.startswith("enrichment.jobs."):
        return _job_target(
            "enrichment.jobs",
            action,
            _job_identifier(item, nested=source.endswith(".items")),
        )
    if source.startswith("crawler.jobs."):
        return _crawler_target(
            action,
            _job_identifier(
                item,
                nested=source.endswith((".pages", ".events", ".candidates")),
            ),
        )
    if source.startswith("campaigns."):
        return _campaign_target(source, action, item)
    if source.startswith("deliveries."):
        return _delivery_target(action, item)
    return None


def _plan_target(
    action: str,
    plan_id: str,
) -> tuple[str, dict[str, object], list[str], str] | None:
    if action == "read":
        return "plans.show", {"plan_id": plan_id}, [], "invoke"
    if action == "execute":
        # Do not pre-fill --confirm.  It is supplied only after the Agent has
        # obtained an explicit confirmation for this exact plan.
        return "plans.execute", {"plan_id": plan_id}, ["confirm"], "invoke"
    if action == "cancel":
        return "plans.cancel", {"plan_id": plan_id}, [], "invoke"
    return None


def _draft_target(
    action: str,
    task_id: int | None,
) -> tuple[str, dict[str, object], list[str], str] | None:
    if task_id is None:
        return None
    arguments = {"task_id": task_id}
    if action == "read":
        return "drafts.get", arguments, [], "invoke"
    if action == "wait":
        # Draft generation does not have a generic wait route.  A bounded
        # re-read is still executable and accurately models a single poll.
        return "drafts.get", arguments, [], "poll"
    if action == "save":
        return "drafts.save", arguments, ["body_text"], "invoke"
    if action == "regenerate":
        return "drafts.regenerate", arguments, [], "invoke"
    if action == "rewrite":
        return "drafts.rewrite", arguments, ["body_text"], "invoke"
    if action == "approve":
        return "drafts.approve", arguments, ["body_text"], "invoke"
    if action == "prepare-send":
        return "drafts.prepare-send", arguments, [], "invoke"
    if action == "cancel":
        return "tasks.cancel-schedule", arguments, [], "invoke"
    if action == "continue-manually":
        return "tasks.continue-manually", arguments, [], "invoke"
    if action == "start-follow-up":
        return "tasks.start-follow-up", arguments, [], "invoke"
    return None


def _job_target(
    resource: str,
    action: str,
    job_id: int | None,
) -> tuple[str, dict[str, object], list[str], str] | None:
    if job_id is None:
        return None
    arguments = {"job_id": job_id}
    if action == "read":
        return f"{resource}.get", arguments, [], "invoke"
    if action == "wait":
        return "wait", {"resource": resource, "resource_id": [job_id]}, [], "invoke"
    if action == "cancel":
        return f"{resource}.cancel", arguments, [], "invoke"
    if action == "retry":
        return f"{resource}.retry-failed", arguments, [], "invoke"
    if action == "archive":
        return f"{resource}.delete", arguments, [], "invoke"
    if action == "restore":
        return f"{resource}.restore", arguments, [], "invoke"
    return None


def _crawler_target(
    action: str,
    job_id: int | None,
) -> tuple[str, dict[str, object], list[str], str] | None:
    if job_id is None:
        return None
    arguments = {"job_id": job_id}
    if action == "read":
        return "crawler.jobs.get", arguments, [], "invoke"
    if action == "wait":
        return "wait", {"resource": "crawler.jobs", "resource_id": [job_id]}, [], "invoke"
    if action in {"cancel", "pause", "resume", "resume-review", "archive", "restore"}:
        command = {
            "archive": "crawler.jobs.delete",
            "resume-review": "crawler.jobs.resume-review",
        }.get(action, f"crawler.jobs.{action}")
        return command, arguments, [], "invoke"
    if action == "retry":
        return "crawler.jobs.retry", arguments, [], "invoke"
    if action == "approve":
        return "crawler.jobs.approve", arguments, ["selection_mode"], "invoke"
    if action == "enrich":
        return "crawler.jobs.enrich", arguments, ["selection_mode"], "invoke"
    return None


def _campaign_target(
    source: str,
    action: str,
    item: Mapping[str, object],
) -> tuple[str, dict[str, object], list[str], str] | None:
    campaign_id = _positive_integer(item.get("campaign_id"))
    item_id = _positive_integer(item.get("id")) if source.endswith(".items") else None
    if campaign_id is None and item_id is None:
        campaign_id = _positive_integer(item.get("id"))
    if campaign_id is None:
        return None

    campaign_arguments = {"campaign_id": campaign_id}
    if action == "read":
        if item_id is not None:
            return (
                "campaigns.item-thread",
                {**campaign_arguments, "item_id": item_id},
                [],
                "invoke",
            )
        return "campaigns.get", campaign_arguments, [], "invoke"
    if action == "wait":
        return "wait", {"resource": "campaigns", "resource_id": [campaign_id]}, [], "invoke"
    if action == "start-drafts":
        if item_id is not None:
            return None
        return "campaigns.start-drafts", campaign_arguments, [], "invoke"
    if action == "pause":
        if item_id is not None:
            return None
        return "campaigns.pause", campaign_arguments, [], "invoke"
    if action == "cancel":
        if item_id is not None:
            return (
                "campaigns.cancel-item-send",
                {**campaign_arguments, "item_id": item_id},
                [],
                "invoke",
            )
        return "campaigns.stop", campaign_arguments, [], "invoke"
    if action == "archive":
        if item_id is not None:
            return None
        return "campaigns.archive", campaign_arguments, [], "invoke"
    if action == "restore":
        if item_id is not None:
            return None
        return "campaigns.restore", campaign_arguments, [], "invoke"
    if action == "resume":
        if item_id is not None:
            return (
                "campaigns.prepare-restore-item-send",
                {**campaign_arguments, "item_id": item_id},
                [],
                "invoke",
            )
        return "campaigns.prepare-resume", campaign_arguments, [], "invoke"
    if action == "retry" and item_id is not None:
        return (
            "campaigns.retry-item-draft",
            {**campaign_arguments, "item_id": item_id},
            [],
            "invoke",
        )
    if action == "approve" and item_id is not None:
        return (
            "campaigns.approve-item-draft",
            {**campaign_arguments, "item_id": item_id},
            ["body_text"],
            "invoke",
        )
    if action == "prepare-send":
        arguments: dict[str, object] = dict(campaign_arguments)
        if item_id is not None:
            arguments["item_ids"] = [item_id]
        return "campaigns.prepare-send", arguments, [], "invoke"
    return None


def _delivery_target(
    action: str,
    item: Mapping[str, object],
) -> tuple[str, dict[str, object], list[str], str] | None:
    task_id = _positive_integer(item.get("task_id")) or _positive_integer(item.get("id"))
    if task_id is None:
        return None
    if action == "reschedule":
        expected_updated_at = _string_identifier(item.get("expected_updated_at"))
        if expected_updated_at is None:
            return None
        return (
            "deliveries.reschedule",
            {
                "task_id": task_id,
                "expected_updated_at": expected_updated_at,
            },
            ["scheduled_at"],
            "invoke",
        )
    return None


def _job_identifier(item: Mapping[str, object], *, nested: bool = False) -> int | None:
    job_id = _positive_integer(item.get("job_id"))
    if job_id is not None or nested:
        return job_id
    return _positive_integer(item.get("id"))


def _positive_integer(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int) and value > 0:
        return value
    return None


def _string_identifier(value: object) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    if isinstance(value, int) and not isinstance(value, bool):
        return str(value)
    return None
