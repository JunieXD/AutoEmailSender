from __future__ import annotations

import hashlib
import json
from typing import Any

from auto_email_sender_cli.action_links import resolve_action_links
from auto_email_sender_cli.capabilities import supports_dynamic_action_links
from auto_email_sender_cli.output import CliContext

_TERMINAL_STATES = {
    "succeeded",
    "partially_succeeded",
    "partial_failed",
    "partially_completed",
    "completed",
    "failed",
    "canceled",
    "cancelled",
    "stopped",
    "archived",
    "expired",
    "sent",
    "send_failed",
    "draft_failed",
    "reply_detected",
}


def augment_state_metadata(data: Any, *, command: str) -> Any:
    """Expose a uniform state/action view while preserving existing DTO fields."""

    if not isinstance(data, dict):
        return data
    # A ``status`` field also appears in ordinary analytics and communication
    # records (for example usage ``success``). Only lifecycle resources should
    # receive executable action metadata; otherwise a read-only record looks
    # like a task an Agent can cancel or retry.
    if not supports_dynamic_action_links(command):
        return data
    return _augment_state_value(data, command=command)


def compact_collection_action_metadata(data: Any, *, command: str) -> Any:
    """Group repeated lifecycle actions on top-level resource lists."""

    if not command.endswith(".list") or not isinstance(data, dict):
        return data
    items = data.get("items")
    if not isinstance(items, list):
        return data

    grouped: dict[str, dict[str, object]] = {}
    compact_items: list[object] = []
    for item in items:
        if not isinstance(item, dict):
            compact_items.append(item)
            continue
        resource_id = item.get("id")
        status = item.get("status")
        actions = item.get("available_actions")
        if (
            isinstance(resource_id, bool)
            or not isinstance(resource_id, str | int)
            or not isinstance(status, str)
            or not isinstance(actions, list)
        ):
            compact_items.append(item)
            continue
        compact_actions = [
            {key: value for key, value in action.items() if key != "arguments"}
            for action in actions
            if isinstance(action, dict)
        ]
        blocked_actions = (
            item.get("blocked_actions")
            if isinstance(item.get("blocked_actions"), dict)
            else {}
        )
        signature = json.dumps(
            {
                "status": status,
                "available_actions": compact_actions,
                "blocked_actions": blocked_actions,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        group = grouped.setdefault(
            signature,
            {
                "status": status,
                "ids": [],
                "available_actions": compact_actions,
                **({"blocked_actions": blocked_actions} if blocked_actions else {}),
            },
        )
        group_ids = group["ids"]
        assert isinstance(group_ids, list)
        group_ids.append(resource_id)
        compact_items.append(
            {
                key: value
                for key, value in item.items()
                if key not in {"available_actions", "blocked_actions", "blocked_reason"}
            },
        )

    if not grouped:
        return data
    action_groups = sorted(
        grouped.values(),
        key=lambda group: (str(group["status"]), str(group["ids"][0])),
    )
    return {**data, "items": compact_items, "action_groups": action_groups}


def _augment_state_value(value: Any, *, command: str) -> Any:
    if isinstance(value, list):
        return [_augment_state_value(item, command=command) for item in value]
    if not isinstance(value, dict):
        return value
    result = {
        key: _augment_state_value(item, command=command) for key, item in value.items()
    }
    if isinstance(result.get("status"), str):
        result = _augment_state_item(result, command=command)
    elif _supports_present_in_app(command, result):
        action_links, blocked_actions = resolve_action_links(
            command,
            result,
            actions=["present-in-app"],
        )
        result["available_actions"] = action_links
        result["blocked_actions"] = blocked_actions
        result["blocked_reason"] = (
            None if action_links else "当前结果无法定位到桌面页面"
        )
    return result


def add_revisions(data: Any, *, include_collection: bool = True) -> Any:
    """Add deterministic optimistic-concurrency tokens to returned objects."""

    if not isinstance(data, dict):
        return data
    result = dict(data)
    if isinstance(result.get("items"), list):
        if include_collection:
            result["items"] = [
                _with_revision(item) if isinstance(item, dict) else item
                for item in result["items"]
            ]
    elif "revision" not in result and any(
        key in result for key in ("id", "task_id", "job_id", "plan_id", "handoff_id")
    ):
        result = _with_revision(result)
    return result


def _collection_revisions_requested(context: CliContext, fields: str | None) -> bool:
    if context.include_revisions:
        return True
    return "revision" in {
        field.strip() for field in (fields or "").split(",") if field.strip()
    }


def _with_revision(value: dict[str, object]) -> dict[str, object]:
    if isinstance(value.get("revision"), str) and value["revision"]:
        return value
    payload = {
        key: item
        for key, item in value.items()
        if key not in {"revision", "updated_at", "created_at"}
    }
    encoded = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, default=str, separators=(",", ":")
    )
    return {
        **value,
        "revision": hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:20],
    }


def _augment_state_item(item: dict[str, object], *, command: str) -> dict[str, object]:
    result = dict(item)
    status_value = str(result.get("status", "")).lower()
    if not status_value:
        return result
    existing_actions = result.get("available_actions")
    existing_blocked = result.get("blocked_actions")
    if isinstance(existing_actions, list):
        actions, blocked_actions = _normalize_existing_action_metadata(
            existing_actions,
            existing_blocked,
        )
    else:
        actions, blocked_actions = _state_actions_for_item(
            command,
            status_value,
            result,
        )
    if _supports_present_in_app(command, result) and "present-in-app" not in actions:
        actions.append("present-in-app")
    action_links, resolved_blocked_actions = resolve_action_links(
        command,
        result,
        actions=actions,
        blocked_actions=blocked_actions,
    )
    result["available_actions"] = action_links
    result["blocked_actions"] = resolved_blocked_actions
    if "blocked_reason" not in result:
        result["blocked_reason"] = (
            None if result["available_actions"] else "当前状态没有可执行动作"
        )
    return result


def _supports_present_in_app(command: str, item: dict[str, object]) -> bool:
    normalized = command.strip().lower()
    if normalized.startswith("ui-handoffs."):
        return False
    if normalized == "professors.get":
        return _positive_state_identifier(
            item.get("professor_id") or item.get("id"),
        )
    if normalized.startswith("communications.threads."):
        return isinstance(item.get("id") or item.get("thread_id"), str)
    if normalized.startswith("crawler.jobs."):
        return _positive_state_identifier(item.get("job_id") or item.get("id"))
    if normalized.startswith(("drafts.", "tasks.", "workspaces.")):
        return _positive_state_identifier(item.get("task_id") or item.get("id"))
    if normalized == "campaigns.item-thread":
        return _positive_state_identifier(item.get("task_id"))
    return False


def _positive_state_identifier(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _normalize_existing_action_metadata(
    actions: list[object],
    blocked_actions: object,
) -> tuple[list[str], dict[str, str]]:
    """Accept legacy backend action tokens without trusting their arguments.

    Some older desktop versions may still return ``[{action, allowed}]``. The
    CLI uses only their declarative action names, then rebuilds target commands
    and identifiers from its own manifest and the current structured DTO.
    """

    allowed: list[str] = []
    blocked: dict[str, str] = {}
    for raw_action in actions:
        if isinstance(raw_action, str):
            allowed.append(raw_action)
            continue
        if not isinstance(raw_action, dict):
            continue
        action = raw_action.get("action")
        if not isinstance(action, str):
            continue
        if raw_action.get("allowed", True):
            allowed.append(action)
        else:
            blocked[action] = str(raw_action.get("reason") or "当前状态不允许该动作。")
    if isinstance(blocked_actions, dict):
        for action, reason in blocked_actions.items():
            if isinstance(action, str):
                blocked[action] = str(reason or "当前状态不允许该动作。")
    return allowed, blocked


def _state_actions_for_item(
    command: str,
    status: str,
    item: dict[str, object],
) -> tuple[list[str], dict[str, str]]:
    """Map known product states to safe actions without claiming completion.

    The backend remains authoritative for business rules.  This projection is
    intentionally conservative: an action is advertised only when its state
    makes it a plausible next operation; every other known action is explained
    in ``blocked_actions`` so an Agent does not have to guess.
    """

    normalized = command.lower()
    if "plan_id" in item:
        return _plan_state_actions(status)
    if normalized.startswith("deliveries."):
        if item.get("can_reschedule") is True:
            return ["reschedule"], {}
        return [], {
            "reschedule": "当前发送项状态或来源不允许改期",
        }
    if normalized.startswith(("drafts.", "tasks.", "workspaces.")) or any(
        key in item for key in ("task_id", "approved_body_text", "generated_body_text")
    ):
        return _draft_state_actions(status, item)

    if status in {"partial_failed", "partially_completed", "failed"}:
        actions = ["read", "retry"]
        if normalized.startswith("crawler.jobs.") and status == "partially_completed":
            actions.append("enrich")
        return actions, {
            "wait": "对象已结束；请读取逐项结果后仅重试失败项",
            "cancel": "对象已结束，不能取消",
            "resume": "部分成功对象不能直接恢复",
        }

    if status in _TERMINAL_STATES:
        actions = ["read"]
        if normalized.endswith((".list", ".items")):
            actions.append("archive")
        return actions, {
            "wait": "对象已进入终态，不能继续等待",
            "cancel": "对象已进入终态，不能取消",
            "pause": "对象已进入终态，不能暂停",
            "resume": "对象已进入终态，不能恢复",
            "retry": "当前终态没有可重试项，需先读取逐项失败原因",
        }
    if status in {"queued", "running", "processing"}:
        actions = ["read", "wait", "cancel"]
        if normalized.startswith("crawler.jobs."):
            actions.append("pause")
        return actions, {
            "resume": "对象尚未暂停，不能恢复",
            "archive": "运行中的对象不能归档",
        }
    if status == "paused":
        return ["read", "resume", "cancel"], {
            "wait": "对象已暂停，请先恢复后再等待",
            "retry": "对象已暂停，不能直接重试",
        }
    if status in {"needs_review", "review_required"}:
        actions = ["read"]
        if normalized.startswith("crawler.jobs."):
            actions.extend(["resume-review", "approve", "enrich"])
        if normalized.startswith("campaigns."):
            actions.extend(["approve", "prepare-send"])
        return actions, {
            "wait": "对象正在等待人工审核，不是后台执行中",
            "cancel": "请使用该资源声明的取消动作",
            "resume": "请先完成审核或使用 resume-review",
        }
    return ["read"], {
        "wait": f"状态 {status} 未声明为可等待状态",
        "cancel": f"状态 {status} 未声明为可取消状态",
        "pause": f"状态 {status} 未声明为可暂停状态",
        "resume": f"状态 {status} 未声明为可恢复状态",
        "retry": f"状态 {status} 未声明为可重试状态",
    }


def _draft_state_actions(
    status: str, item: dict[str, object]
) -> tuple[list[str], dict[str, str]]:
    if status == "generating_draft":
        return ["read", "wait"], {
            "save": "草稿正在生成，不能同时保存",
            "regenerate": "草稿正在生成，不能重复生成",
            "rewrite": "草稿正在生成，不能同时改写",
            "approve": "草稿尚未生成完成，不能批准",
            "prepare-send": "草稿尚未完成，不能准备发送计划",
        }
    if status in {"discovered", "matched", "draft_failed", "review_required"}:
        actions = ["read", "save", "regenerate", "rewrite", "approve"]
        if item.get("approved_body_text") or item.get("approved_body_html"):
            actions.append("prepare-send")
        return actions, {
            "wait": "当前草稿不是后台运行任务",
            "cancel": "草稿取消需要使用对应任务或活动动作",
        }
    if status in {"approved", "scheduled"}:
        return ["read", "prepare-send"], {
            "save": "已批准或排程的草稿需先取消排程再编辑",
            "regenerate": "已批准或排程的草稿需先回到审核状态",
            "rewrite": "已批准或排程的草稿需先回到审核状态",
            "approve": "草稿已经批准，无需重复批准",
        }
    if status in {"sending", "sent", "reply_detected", "send_failed", "canceled"}:
        return ["read"], {
            "save": "当前任务已进入发送或结束状态，不能作为草稿修改",
            "regenerate": "当前任务已进入发送或结束状态，不能重新生成",
            "rewrite": "当前任务已进入发送或结束状态，不能改写",
            "approve": "当前任务已进入发送或结束状态，不能批准草稿",
            "prepare-send": "当前任务不处于可准备发送计划的状态",
        }
    return ["read"], {
        "save": f"状态 {status} 不允许保存草稿",
        "regenerate": f"状态 {status} 不允许重新生成",
        "rewrite": f"状态 {status} 不允许 AI 改写",
        "approve": f"状态 {status} 不允许批准草稿",
        "prepare-send": f"状态 {status} 不允许准备发送计划",
    }


def _plan_state_actions(status: str) -> tuple[list[str], dict[str, str]]:
    if status in {"pending", "ready", "awaiting_confirmation", "confirmed"}:
        return ["read", "execute", "cancel"], {
            "retry": "计划尚未进入可重试终态",
        }
    if status in {
        "executed",
        "completed",
        "canceled",
        "cancelled",
        "expired",
        "failed",
    }:
        return ["read"], {
            "execute": "计划已进入终态，不能再次执行",
            "cancel": "计划已进入终态，不能取消",
        }
    return ["read"], {
        "execute": f"计划状态 {status} 未声明为可执行状态",
        "cancel": f"计划状态 {status} 未声明为可取消状态",
    }
