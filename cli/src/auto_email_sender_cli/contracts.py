"""Machine-readable contracts shared by ``capabilities`` and ``describe``.

The CLI deliberately keeps this module dependency-light.  It is a protocol
description layer, not a second implementation of business rules: the Typer
command tree supplies the executable input parameters and the capability
registry supplies risk and effect metadata.
"""

from __future__ import annotations

from typing import Any

from auto_email_sender_cli.capabilities import (
    CONTRACT_VERSION,
    Capability,
    capability_operation,
    capability_resource,
    capability_stateful,
    collection_output_fields,
    collection_filter_fields,
    collection_filter_operators,
    supports_field_selection,
    supports_file_export,
    supports_if_revision,
    supports_wait,
    supports_pagination,
    supports_structured_filter,
)


CONTRACT_REQUIRED_KEYS = (
    "command",
    "contract_version",
    "input",
    "output",
    "effects",
    "preconditions",
    "state_transitions",
    "errors",
    "next_actions",
)


def build_command_contract(
    *,
    command: str,
    parameters: list[dict[str, object]],
    input_file_examples: list[dict[str, object]],
    capability: Capability | None,
    next_steps: list[str],
) -> dict[str, object]:
    """Build the stable v1 contract returned by ``describe``.

    ``parameters`` is generated directly from the Click command object.  That
    means a required flag, enum, range or path cannot drift from the actual
    parser while the surrounding contract remains stable for Agents.
    """

    normalized = command
    supports_list = supports_pagination(normalized)
    contract: dict[str, object] = {
        "command": normalized,
        "contract_version": CONTRACT_VERSION,
        "resource": capability_resource(normalized),
        "operation": capability_operation(normalized),
        "input": _input_contract(normalized, parameters, input_file_examples),
        "output": _output_contract(normalized, supports_list),
        "effects": _effects_contract(capability),
        "preconditions": _preconditions_contract(normalized),
        "state_transitions": _state_transitions_contract(normalized, capability),
        "errors": _errors_contract(normalized, capability),
        "next_actions": _next_actions_contract(normalized, capability),
        # Keep the earlier human-oriented fields for protocol v2 clients.
        "next_steps": next_steps,
    }
    return contract


def _input_contract(
    command: str,
    parameters: list[dict[str, object]],
    input_file_examples: list[dict[str, object]],
) -> dict[str, object]:
    properties: dict[str, object] = {}
    required: list[str] = []
    for parameter in parameters:
        name = str(parameter.get("name", ""))
        if not name:
            continue
        type_info = parameter.get("type")
        schema_type = _schema_type_for_parameter(type_info)
        properties[name] = {
            **(type_info if isinstance(type_info, dict) else {"kind": "string"}),
            "type": "array" if bool(parameter.get("multiple")) else schema_type,
            "description": parameter.get("help"),
            "flags": parameter.get("flags", []),
            "kind": parameter.get("kind", "option"),
            "multiple": bool(parameter.get("multiple", False)),
            "nargs": parameter.get("nargs", 1),
            "default": parameter.get("default"),
            "clear_semantics": _clear_semantics(name),
        }
        if bool(parameter.get("multiple")):
            properties[name]["items"] = {"type": schema_type}
        if bool(parameter.get("required")):
            required.append(name)
    return {
        "schema": {
            "type": "object",
            "properties": properties,
            "required": required,
            "additionalProperties": False,
        },
        "parameters": parameters,
        "global_options": {
            "request_id": {
                "flags": ["--request-id"],
                "type": "string",
                "required": False,
                "position": "before_command",
                "description": "可复用的本地操作标识；重试同一请求不会重复本地副作用。",
            },
            "format": {
                "flags": ["--format", "--json"],
                "values": ["table", "json", "jsonl"],
                "required": False,
                "position": "before_command",
            },
            "if_revision": {
                "flags": ["--if-revision"],
                "type": "string",
                "required": False,
                "position": "before_command",
                "supported": supports_if_revision(command),
                "description": "仅支持版本保护的写入命令；只在对象版本未变化时执行写入。",
            },
            "output_file": {
                "flags": ["--output-file"],
                "type": "path",
                "required": False,
                "position": "before_command",
                "description": "集合结果写入 JSONL；stdout 返回路径、数量和游标摘要。",
            },
            "force_output": {
                "flags": ["--force-output"],
                "type": "boolean",
                "required": False,
                "position": "before_command",
            },
            "filter": {
                "flags": ["--filter"],
                "type": "object-json",
                "required": False,
                "position": "before_command",
                "description": "对集合应用白名单结构化筛选；不接受 SQL 或任意表达式。",
            },
        },
        "clear_semantics": (
            "omitted=preserve; supplied value=set; explicit --clear-* or clear=true=clear"
        ),
        "file_and_stdin_examples": input_file_examples,
    }


def _schema_type_for_parameter(type_info: object) -> object:
    if not isinstance(type_info, dict):
        return "string"
    kind = type_info.get("kind")
    if kind == "enum":
        values = type_info.get("values")
        return "string" if not isinstance(values, list) else "string"
    if kind == "integer":
        return "integer"
    if kind == "number":
        return "number"
    if kind == "boolean":
        return "boolean"
    if kind == "path":
        return "string"
    return "string"


def _clear_semantics(name: str) -> str:
    if name.startswith("clear_"):
        return "true explicitly clears the corresponding value"
    if name in {"body_text", "body_html", "subject", "name", "email"}:
        return "omitted=preserve; supplied value=set; use the command's clear option when available"
    return "omitted=preserve; supplied value=set"


def _output_contract(command: str, supports_list: bool) -> dict[str, object]:
    output_fields = _known_output_fields(command) or _GENERIC_OUTPUT_FIELDS
    if command not in _REVISION_EXCLUDED_COMMANDS and (supports_list or _has_revision_output(output_fields)):
        # ``run_read_command`` always exposes a revision on collection items,
        # and detail reads expose the same token on identified objects.
        output_fields = output_fields | {"revision"}
    if capability_stateful(command) and (supports_list or "status" in output_fields):
        # ``augment_state_metadata`` adds these fields to every identified
        # stateful object, including collection items and nested task items.
        output_fields = output_fields | {"available_actions", "blocked_actions", "blocked_reason"}
    item_schema = _object_schema(output_fields, command=command)
    data_schema: dict[str, object]
    if supports_list:
        data_schema = {
            "type": "object",
            "properties": {
                "items": {"type": "array", "items": item_schema},
                "next_cursor": {"type": ["string", "null"]},
                "has_more": {"type": "boolean"},
                "total": {"type": ["integer", "null"]},
                "offset": {"type": ["integer", "null"]},
                "limit": {"type": ["integer", "null"]},
                "pagination_mode": {"type": ["string", "null"]},
                "fetched_all": {"type": "boolean"},
                "selected_fields": {"type": "array"},
                "filter": {"type": ["object", "null"]},
                "filtered_count": {"type": ["integer", "null"]},
                "records": {"type": "array"},
                "pagination": {"type": ["object", "null"]},
                "summary": {"type": ["object", "null"]},
                "model_options": {"type": "array"},
            },
            "required": ["items", "next_cursor", "has_more"],
        }
    else:
        # Detail, mutation and task commands return the corresponding resource
        # DTO (sometimes augmented with a plan/job/receipt).  Publishing the
        # known DTO fields here gives an Agent a useful projection without
        # pretending that every backend response has exactly one concrete
        # shape.  ``additionalProperties`` remains true for additive protocol
        # fields, while the stable fields are still machine-readable.
        data_schema = item_schema
    return {
        "schema": {
            "type": "object",
            "properties": {
                "ok": {"const": True},
                "data": data_schema,
                "_meta": {"type": "object"},
            },
            "required": ["ok", "data", "_meta"],
        },
        "pagination": supports_list,
        "field_selection": supports_field_selection(command),
        "structured_filter": supports_structured_filter(command),
        "filter_contract": {
            "supported": supports_structured_filter(command),
            "fields": {
                field: {**_field_schema(field), "operators": list(collection_filter_operators(command))}
                for field in sorted(collection_filter_fields(command))
            },
            "operators": list(collection_filter_operators(command)),
        },
        "file_export": supports_file_export(command),
        "terminal_states": [
            "succeeded",
            "completed",
            "partially_succeeded",
            "partially_completed",
            "partial_failed",
            "failed",
            "canceled",
            "stopped",
            "expired",
        ]
        if capability_stateful(command)
        else [],
        "state_metadata": {
            "status": "string",
            "available_actions": "array[string]",
            "blocked_actions": "object[action -> reason]",
            "blocked_reason": "string|null",
        }
        if capability_stateful(command)
        else None,
        "mutation_receipt": {
            "description": "写操作返回的统一变更回执；计划型操作返回 plan_id 和影响摘要。",
            "schema": {
                "type": "object",
                "properties": {
                    "request_id": {"type": "string"},
                    "status": {"type": "string", "enum": ["pending", "applied", "replayed"]},
                    "changed_resources": {"type": "array"},
                    "warnings": {"type": "array"},
                    "audit_reference": {"type": ["string", "null"]},
                },
                "required": ["request_id", "status", "changed_resources", "warnings", "audit_reference"],
            },
        },
        "known_fields": sorted(output_fields),
        "envelope_fields": [
            "items",
            "next_cursor",
            "has_more",
            "total",
            "offset",
            "limit",
            "pagination_mode",
            "fetched_all",
            "selected_fields",
            "filter",
            "filtered_count",
            "records",
            "pagination",
            "summary",
            "model_options",
        ]
        if supports_list
        else [],
    }


_GENERIC_OUTPUT_FIELDS = frozenset(
    {
        "id",
        "task_id",
        "job_id",
        "plan_id",
        "status",
        "revision",
        "items",
        "next_cursor",
        "has_more",
        "mutation_receipt",
        "warnings",
        "message",
        "error",
    },
)


_REVISION_EXCLUDED_COMMANDS = frozenset(
    {
        # These commands download or poll a result and do not pass through the
        # common revision augmentation layer.
        "wait",
        "materials.download",
        "diagnostics.crawler-debug",
    },
)


_SPECIAL_OUTPUT_FIELDS: dict[str, frozenset[str]] = {
    "drafts": frozenset(
        {
            "task_id", "revision", "source", "batch_task_id", "parent_task_id",
            "identity_id", "professor_id", "professor_name", "professor_email",
            "llm_profile_id", "status", "generation_mode", "template_id",
            "reference_material_id", "attachment_material_ids", "generated_subject",
            "generated_body_text", "generated_body_html", "approved_subject",
            "approved_body_text", "approved_body_html", "approved_at", "scheduled_at",
            "sent_at", "last_error", "created_at", "updated_at",
        },
    ),
    "workspaces": frozenset(
        {
            "professor", "identity", "llm_profile", "material_options", "current_task",
            "messages", "communication_scope", "sync_warnings",
        },
    ),
    "tasks": frozenset(
        {
            "task_id", "run_id", "thread", "usage", "status", "can_continue_manually",
            "can_write_follow_up", "outreach_generation_mode", "generated_subject",
            "generated_content_text", "generated_content_html", "approved_subject",
            "approved_body_text", "approved_body_html", "primary_material_id",
            "selected_material_ids", "match_score", "match_reason", "fit_points",
            "risk_points", "match_keywords", "last_error",
        },
    ),
    "plans": frozenset(
        {
            "plan_id", "action", "status", "task_id", "content_fingerprint", "expires_at",
            "confirmed_at", "executed_at", "canceled_at", "summary", "warnings", "result",
            "idempotent_replay", "confirmation_message",
        },
    ),
    "campaigns": frozenset(
        {
            "id", "name", "status", "identity", "llm_profile", "generation_mode", "template",
            "reference_material", "attachment_material_ids", "schedule_type", "window_start_time",
            "window_end_time", "emails_per_window", "scheduled_dates", "target_count",
            "pending_generation_count", "generating_draft_count", "draft_failed_count",
            "review_required_count", "approved_count", "scheduled_count", "sending_count",
            "sent_count", "failed_count", "canceled_count", "canceled_send_count",
            "can_start_draft_generation", "created_at", "updated_at",
        },
    ),
    "communication-groups": frozenset({"id", "revision", "members", "created_at", "updated_at"}),
    "test-email": frozenset(
        {
            "completed", "identity", "llm_profile", "material_options", "draft", "history",
            "id", "recipient_email", "subject", "content", "content_html", "status",
            "rfc_message_id", "failure_summary", "created_at", "outreach_template_id",
            "selected_material_ids",
        },
    ),
    "communications.sync": frozenset({"identity_id", "detected_count", "warnings", "status"}),
    "dashboard.overview": frozenset({"mentor", "email", "professor_count", "email_count", "sent_count", "reply_count", "needs_attention_count", "by_status", "by_identity"}),
    "settings": frozenset({"revision", "updated_at", "settings"}),
}


_COMMAND_OUTPUT_FIELDS: dict[str, frozenset[str]] = {
    "version": frozenset({"cli_version", "protocol_version"}),
    "status": frozenset({"state", "desktop_process_running", "backend_ready", "app_version", "protocol_version", "protocol_compatible", "runtime_file", "message"}),
    "guide": frozenset({"version", "topic", "title", "deprecated", "rules", "replacement"}),
    "capabilities": frozenset({"catalog_version", "catalog_revision", "view", "items", "summary", "next"}),
    "describe": frozenset({"command", "kind", "summary", "usage", "example", "parameters", "children", "input_file_examples", "risk", "preconditions", "next_steps", "suggestions", "contract_version", "resource", "operation", "input", "output", "effects", "state_transitions", "errors", "next_actions", "details_available", "details"}),
    "doctor": frozenset({"healthy", "checks", "recommended_action", "repair_command"}),
    "wait": frozenset({"resource", "id", "status", "terminal", "timed_out", "poll_count", "elapsed_seconds", "result", "available_actions"}),
    "professors.tags.usage": frozenset({"tag", "professors"}),
    "communications.threads.get": frozenset({"id", "identity_id", "identity_name", "identity_email_address", "professor_id", "professor_name", "professor_email", "sent_count", "received_count", "has_sent", "has_reply", "last_message_at", "messages", "messages_next_cursor", "messages_has_more"}),
    "communications.sync": frozenset({"identity_id", "detected_count", "completed_at", "message"}),
    "identities.test-smtp": frozenset({"ok", "message", "host", "possible_cause"}),
    "identities.test-imap": frozenset({"ok", "message", "host", "possible_cause"}),
    "llm-profiles.models": frozenset({"profile_id", "ok", "message", "resolved_base_url", "request_url", "attempted_urls", "endpoint_kind", "status_code", "duration_ms", "consumes_tokens", "models", "selected_model_available", "trust_level"}),
    "llm-profiles.test": frozenset({"profile_id", "ok", "message", "resolved_base_url", "request_url", "attempted_urls", "endpoint_kind", "status_code", "duration_ms", "consumes_tokens", "prompt_tokens", "completion_tokens", "total_tokens", "trust_level"}),
    "communication-groups.delete": frozenset({"ok", "group_id"}),
    "usage.chart": frozenset({"preset", "granularity", "range_start", "range_end", "buckets"}),
    "usage.visualization": frozenset({"preset", "summary", "chart", "feature_distribution", "model_ranking", "recent_records"}),
    "test-email.status": frozenset({"completed"}),
    "test-email.get": frozenset({"identity", "llm_profile", "material_options", "draft", "history"}),
    "test-email.generate": frozenset({"identity", "llm_profile", "material_options", "draft", "history"}),
    "test-email.save": frozenset({"identity", "llm_profile", "material_options", "draft", "history"}),
    "crawler.jobs.enrich": frozenset({"selected_count", "enriched_count", "unchanged_count", "failed_count", "skipped_count", "message"}),
    "matching.jobs.cancel": frozenset({"ok", "job"}),
    "matching.jobs.delete": frozenset({"ok", "job"}),
    "matching.jobs.restore": frozenset({"ok", "job"}),
    "enrichment.jobs.cancel": frozenset({"ok", "job"}),
    "enrichment.jobs.delete": frozenset({"ok", "job"}),
    "enrichment.jobs.restore": frozenset({"ok", "job"}),
    "templates.import-file": frozenset({"subject", "body_text", "body_html", "format_name", "trust_level"}),
    "crawler.candidates.update": frozenset({"id", "revision", "job_id", "professor_id", "name", "email", "title", "university", "school", "department", "research_direction", "recent_papers", "profile_url", "source_url", "confidence", "field_confidence", "evidence", "review_status", "created_at", "updated_at", "trust_level"}),
    "professors.export": frozenset({"output", "format", "size_bytes"}),
    "professors.community.export-package": frozenset({"output", "professor_ids", "size_bytes"}),
    "professors.community.catalog": frozenset({"schema_version", "dataset_version", "generated_at", "record_count", "universities", "source", "stale", "warning", "verified_at", "lifecycle_warnings"}),
    "professors.community.records": frozenset({"dataset_version", "source", "stale", "warning", "records", "lifecycle_warnings", "record", "comparison_token", "category", "local_professor_id", "local_professor_name", "local_archived", "linked", "identity_conflict", "match_reason", "import_blocked", "import_blocked_reason", "fields"}),
    "professors.community.preview": frozenset({"dataset_version", "source", "stale", "warning", "records", "lifecycle_warnings", "record", "comparison_token", "category", "local_professor_id", "local_professor_name", "local_archived", "linked", "identity_conflict", "match_reason", "import_blocked", "import_blocked_reason", "fields"}),
    "communications.messages.export": frozenset({"output", "record_count", "format", "body_included"}),
    "materials.download": frozenset({"material_id", "output", "size_bytes"}),
    "diagnostics.export": frozenset({"output", "total"}),
    "diagnostics.crawler-debug": frozenset({"job_id", "output", "size_bytes"}),
    "campaigns.get": frozenset({"id", "name", "status", "identity", "llm_profile", "generation_mode", "template", "reference_material", "attachment_material_ids", "schedule_type", "window_start_time", "window_end_time", "emails_per_window", "scheduled_dates", "target_count", "pending_generation_count", "generating_draft_count", "draft_failed_count", "review_required_count", "approved_count", "scheduled_count", "sending_count", "sent_count", "failed_count", "canceled_count", "canceled_send_count", "can_start_draft_generation", "created_at", "updated_at"}),
    "campaigns.resend-context": frozenset({"task", "defaults", "items", "summary", "warnings"}),
}

_PLAN_OUTPUT_COMMANDS = frozenset(
    {
        "plans.show",
        "plans.execute",
        "plans.cancel",
        "drafts.prepare-send",
        "campaigns.create",
        "campaigns.prepare-send",
        "campaigns.prepare-resume",
        "campaigns.prepare-restore-item-send",
        "crawler.jobs.approve",
        "crawler.jobs.retry",
        "professors.import",
        "professors.community.import",
        "professors.tags.prepare-bulk",
        "professors.tags.prepare-delete",
        "professors.prepare-bulk-archive",
        "templates.prepare-archive",
        "materials.prepare-delete",
        "test-email.prepare-send",
    }
)


def _known_output_fields(command: str) -> frozenset[str]:
    normalized = command.strip().lower()
    exact = _COMMAND_OUTPUT_FIELDS.get(normalized)
    if exact is not None:
        return exact
    if normalized in _PLAN_OUTPUT_COMMANDS:
        return _SPECIAL_OUTPUT_FIELDS["plans"]
    fields = collection_output_fields(normalized)
    for prefix, special_fields in _SPECIAL_OUTPUT_FIELDS.items():
        if normalized == prefix or normalized.startswith(f"{prefix}."):
            fields = fields | special_fields
    return fields


def _has_revision_output(fields: frozenset[str]) -> bool:
    return bool(
        fields
        & {
            "id",
            "task_id",
            "job_id",
            "plan_id",
            "campaign_id",
            "professor_id",
            "template_id",
            "material_id",
            "thread_id",
        }
    )


def _object_schema(
    fields: frozenset[str],
    *,
    command: str | None = None,
) -> dict[str, object]:
    resolved = fields or _GENERIC_OUTPUT_FIELDS
    return {
        "type": "object",
        "properties": {field: _field_schema(field, command=command) for field in sorted(resolved)},
        "additionalProperties": True,
    }


def _field_schema(field: str, *, command: str | None = None) -> dict[str, object]:
    normalized = field.lower()
    if command == "dashboard.overview" and normalized in {"mentor", "email"}:
        return {"type": ["object", "null"]}
    if normalized in {"id", "task_id", "job_id", "plan_id", "professor_id", "identity_id", "llm_profile_id", "template_id", "material_id", "thread_id", "email_task_id", "campaign_id"} or normalized.endswith("_id"):
        return {"type": ["integer", "string", "null"]}
    if normalized.endswith(("_count", "_tokens", "size_bytes", "_ms")) or normalized in {"count", "record_count", "target_count", "total", "status_code", "duration_ms", "offset", "limit", "page", "page_size", "total_pages", "total_records", "poll_count"}:
        return {"type": ["integer", "null"]}
    if normalized.endswith("_seconds") or normalized in {"match_score", "score", "temperature", "confidence"}:
        return {"type": ["number", "null"]}
    if normalized.startswith(("is_", "has_", "can_")) or normalized.endswith(("_configured", "_running", "_ready", "_compatible")) or normalized in {"archived", "body_included", "completed", "terminal", "timed_out", "ok", "healthy", "running", "ready", "consumes_tokens", "selected_model_available", "linked", "identity_conflict", "import_blocked", "stale", "default_selected", "selectable", "sendable", "editable", "deprecated"}:
        return {"type": "boolean"}
    if normalized in {"professor", "identity", "llm_profile", "current_task", "draft", "thread", "usage", "summary", "result", "settings", "by_status", "by_identity", "tag", "job", "template", "reference_material", "defaults", "task", "chart", "metadata", "raw", "field_confidence", "evidence", "filters", "next", "replacement", "details_available", "details"}:
        return {"type": ["object", "null"]}
    if normalized in {
        "tags",
        "members",
        "professors",
        "professor_ids",
        "tag_ids",
        "record_ids",
        "to_emails",
        "cc_emails",
        "bcc_emails",
        "attachment_material_ids",
        "enriched_fields",
        "items",
        "warnings",
        "checks",
        "material_options",
        "messages",
        "communication_scope",
        "sync_warnings",
        "history",
        "fit_points",
        "risk_points",
        "match_keywords",
        "scheduled_dates",
        "start_urls",
        "recent_papers",
        "buckets",
        "attempted_urls",
        "models",
        "feature_distribution",
        "model_ranking",
        "recent_records",
        "records",
        "unit_paths",
    }:
        return {"type": "array"}
    if normalized in {"mutation_receipt", "error", "details"}:
        return {"type": ["object", "null"]}
    return {"type": ["string", "null"]}


def _effects_contract(capability: Capability | None) -> dict[str, object]:
    if capability is None:
        return {
            "mutates": False,
            "external_services": [],
            "cost_may_apply": False,
            "reversible": True,
            "requires_explicit_user_intent": False,
            "requires_confirmation_plan": False,
            "impact_scope": "当前命令的读取范围",
            "confirmation_rule": "none",
            "unknown_external_result_protection": False,
        }
    services: list[str] = []
    command = capability.command
    if capability.external_action:
        if command.startswith("communications") or command.startswith("identities.test"):
            services.append("imap_or_smtp")
        elif command.startswith(("crawler", "enrichment")):
            services.append("public_web")
        elif command.startswith(("drafts", "matching", "campaigns", "tasks", "test-email")) and not command.startswith(
            ("drafts.prepare-send", "campaigns.prepare-", "test-email.prepare-send"),
        ):
            services.append("llm")
        elif command.startswith("llm-profiles."):
            services.append("llm")
        if command.startswith(
            (
                "plans",
                "drafts.prepare-send",
                "campaigns.prepare-send",
                "campaigns.prepare-restore-item-send",
                "campaigns.prepare-resume",
                "test-email.prepare-send",
            ),
        ):
            services.append("smtp")
    return {
        "mutates": capability.mutates,
        "external_services": sorted(set(services)),
        "cost_may_apply": "llm" in services and command != "llm-profiles.models",
        "reversible": capability.risk_level in {"L0", "L1", "L2"},
        "requires_explicit_user_intent": capability.mutates or capability.external_action,
        "requires_confirmation_plan": capability.requires_plan or capability.risk_level == "L3",
        "impact_scope": (
            "由命令参数指定的资源或计划范围"
            if capability.mutates or capability.external_action
            else "当前命令的读取范围"
        ),
        "confirmation_rule": (
            "explicit_plan_confirmation"
            if capability.requires_plan or capability.risk_level == "L3"
            else ("explicit_user_intent" if capability.mutates or capability.external_action else "none")
        ),
        "unknown_external_result_protection": capability.external_action,
    }


def _preconditions_contract(command: str) -> dict[str, object]:
    offline = command in {"version", "guide", "capabilities", "describe", "doctor"}
    return {
        "desktop_app_must_be_open": not offline,
        "manual_app_open_required": not offline,
        "runtime": "offline" if offline else "desktop_app_ready",
        "requirements": []
        if offline
        else ["用户必须先手动打开 Auto Email Sender 并等待本地服务 ready"],
        "blocked_reason_when_unavailable": "APP_UNAVAILABLE：请手动打开软件并等待加载完成",
    }


def _state_transitions_contract(
    command: str,
    capability: Capability | None,
) -> list[dict[str, object]]:
    if capability is None or not capability_stateful(command):
        return []
    if command.startswith(("drafts.", "tasks.", "workspaces.")):
        return [
            {
                "from": "discovered|matched|review_required|draft_failed",
                "to": "review_required|generating_draft|approved",
                "action": "save|regenerate|rewrite",
            },
            {
                "from": "approved|scheduled",
                "to": "awaiting_confirmation",
                "action": "prepare-send",
            },
            {
                "from": "generating_draft",
                "to": "review_required|draft_failed",
                "action": "wait",
            },
        ]
    if command.endswith(".list"):
        return [{"from": "any", "to": "read_only", "action": "list"}]
    if command.endswith(".get") or command.endswith(".items"):
        return [{"from": "queued|running|paused|succeeded|partially_succeeded|failed|canceled", "to": "observed", "action": "read"}]
    return [
        {"from": "queued", "to": "running|canceled|failed", "action": "execute"},
        {"from": "running", "to": "paused|succeeded|partially_succeeded|failed|canceled", "action": "observe"},
    ]


def _errors_contract(command: str, capability: Capability | None) -> list[dict[str, object]]:
    errors: list[dict[str, object]] = [
        {"code": "INVALID_ARGUMENT", "retryable": False, "when": "输入不符合合同或业务约束"},
        {"code": "RESOURCE_NOT_FOUND", "retryable": False, "when": "按 ID 查询的对象不存在"},
        {"code": "CONFLICT", "retryable": True, "when": "当前业务状态或对象版本不允许该操作"},
        {"code": "APP_UNAVAILABLE", "retryable": True, "when": "桌面应用未手动打开或本地服务未 ready"},
        {"code": "RUNTIME_PROTOCOL_MISMATCH", "retryable": False, "when": "CLI 与桌面端协议不兼容"},
        {"code": "IF_REVISION_REQUIRES_WRITE", "retryable": False, "when": "--if-revision 只允许出现在支持版本保护的写入命令上"},
    ]
    if capability and capability.mutates:
        errors.extend(
            [
                {"code": "IDEMPOTENCY_KEY_REUSED", "retryable": False, "when": "同一 request_id 被用于不同请求"},
                {"code": "CONFLICT", "retryable": True, "when": "对象版本已变化或业务状态不允许"},
            ],
        )
    if capability and capability.requires_plan:
        errors.append({"code": "PLAN_CONFIRMATION_REQUIRED", "retryable": False, "when": "尚未得到用户对该计划的明确确认"})
        errors.append({"code": "PLAN_STALE", "retryable": True, "when": "计划范围或对象版本已变化"})
    if capability and capability.external_action:
        errors.append({"code": "EXTERNAL_EXECUTION_UNKNOWN", "retryable": False, "when": "外部服务执行结果在连接中断时无法确定；禁止自动重试"})
    return errors


_EXPLICIT_NEXT_ACTIONS: dict[str, tuple[tuple[str, str], ...]] = {
    "professors.tags.create": (("professors.tags.list", "重新读取标签列表和新标签 ID"),),
    "professors.tags.set": (("professors.get", "重新读取导师及其完整标签"),),
    "professors.community.catalog": (("professors.community.records", "读取选定学院的社区导师记录"),),
    "professors.community.records": (("professors.community.preview", "读取与本地档案的字段比对"),),
    "professors.community.preview": (("professors.community.import", "根据最新 comparison_token 生成导入计划"),),
    "communications.sync": (
        ("communications.threads.list", "重新读取同步后的通信线程"),
        ("communications.messages.list", "读取同步后的邮件记录"),
    ),
    "communication-groups.delete": (("communication-groups.list", "确认通信共享组已解除"),),
    "matching.jobs.create": (
        ("matching.jobs.get", "使用返回的 job_id 读取任务状态"),
        ("matching.jobs.items", "使用返回的 job_id 读取逐位结果"),
    ),
    "matching.jobs.get": (("matching.jobs.items", "读取任务中每位导师的结果"),),
    "matching.jobs.items": (("matching.jobs.get", "使用返回的 job_id 读取任务状态"),),
    "matching.jobs.retry-failed": (("matching.jobs.get", "读取新建重试任务状态"),),
    "matching.jobs.cancel": (("matching.jobs.get", "确认任务已取消"),),
    "matching.jobs.delete": (("matching.jobs.get", "确认任务已移入回收站"),),
    "matching.jobs.restore": (("matching.jobs.get", "确认任务已恢复"),),
    "enrichment.jobs.create": (
        ("enrichment.jobs.get", "使用返回的 job_id 读取任务状态"),
        ("enrichment.jobs.items", "使用返回的 job_id 读取逐位补全结果"),
    ),
    "enrichment.jobs.get": (("enrichment.jobs.items", "读取任务中每位导师的结果"),),
    "enrichment.jobs.items": (("enrichment.jobs.get", "使用返回的 job_id 读取任务状态"),),
    "enrichment.jobs.retry-failed": (("enrichment.jobs.get", "读取新建重试任务状态"),),
    "enrichment.jobs.cancel": (("enrichment.jobs.get", "确认任务已取消"),),
    "enrichment.jobs.delete": (("enrichment.jobs.get", "确认任务已移入回收站"),),
    "enrichment.jobs.restore": (("enrichment.jobs.get", "确认任务已恢复"),),
    "crawler.jobs.create": (
        ("crawler.jobs.get", "使用返回的 job_id 读取任务状态"),
        ("crawler.jobs.events", "读取抓取事件时间线"),
        ("crawler.jobs.pages", "读取抓取网页摘要"),
        ("crawler.jobs.candidates", "读取抓取候选导师"),
    ),
    "crawler.jobs.get": (
        ("crawler.jobs.events", "读取抓取事件时间线"),
        ("crawler.jobs.pages", "读取抓取网页摘要"),
        ("crawler.jobs.candidates", "读取抓取候选导师"),
    ),
    "crawler.jobs.events": (("crawler.jobs.get", "读取任务状态和进度"),),
    "crawler.jobs.pages": (("crawler.jobs.get", "读取任务状态和进度"),),
    "crawler.jobs.candidates": (("crawler.jobs.get", "读取任务状态和进度"),),
    "crawler.jobs.enrich": (("crawler.jobs.get", "读取候选补全后的任务状态"),),
    "crawler.jobs.pause": (("crawler.jobs.get", "确认任务已暂停"),),
    "crawler.jobs.resume": (("crawler.jobs.get", "读取继续运行的任务状态"),),
    "crawler.jobs.cancel": (("crawler.jobs.get", "确认任务已取消"),),
    "crawler.jobs.resume-review": (("crawler.jobs.get", "确认任务已转入人工审核"),),
    "crawler.jobs.delete": (("crawler.jobs.get", "确认任务已移入回收站"),),
    "crawler.jobs.restore": (("crawler.jobs.get", "确认任务已恢复"),),
    "crawler.candidates.update": (("crawler.jobs.candidates", "重新读取候选及其审核状态"),),
    "campaigns.get": (("campaigns.items", "读取活动中的逐封草稿和发送状态"),),
    "campaigns.items": (("campaigns.get", "读取活动汇总状态"),),
    "campaigns.start-drafts": (("campaigns.get", "读取活动草稿生成进度"),),
    "campaigns.retry-item-draft": (("campaigns.get", "读取活动草稿生成进度"),),
    "campaigns.pause": (("campaigns.get", "确认活动已暂停"),),
    "campaigns.stop": (("campaigns.get", "确认活动已停止"),),
    "campaigns.archive": (("campaigns.get", "确认活动已归档"),),
    "campaigns.restore": (("campaigns.get", "确认活动已恢复"),),
    "campaigns.remove-item": (("campaigns.get", "确认活动项已移除"),),
    "campaigns.cancel-item-send": (("campaigns.get", "确认活动项发送已取消"),),
    "tasks.cancel-schedule": (("drafts.get", "重新读取回到审核状态的草稿"),),
    "tasks.continue-manually": (("drafts.get", "读取新建的手动草稿"),),
    "tasks.start-follow-up": (("drafts.get", "读取新建的跟进草稿"),),
    "tasks.set-primary-material": (
        ("workspaces.get", "重新读取工作区和材料配置"),
        ("drafts.get", "读取重新生成的草稿"),
    ),
    "tasks.set-outreach-config": (
        ("workspaces.get", "重新读取工作区和写信配置"),
        ("drafts.get", "读取配置变更后的草稿"),
    ),
    "tasks.calculate-match": (
        ("workspaces.get", "读取包含最新匹配分析的工作区"),
        ("drafts.get", "读取任务的当前草稿"),
    ),
    "plans.show": (("plans.execute", "仅在用户明确确认当前计划后执行"),),
    "plans.execute": (("plans.show", "读取执行后的计划状态和结果"),),
    "plans.cancel": (("plans.show", "确认计划已取消"),),
}


def _next_actions_contract(command: str, capability: Capability | None) -> list[dict[str, object]]:
    """Return only executable, registered follow-up commands.

    The old implementation matched substrings in command names.  This table is
    deliberately conservative: an Agent gets a valid read/plan/recovery route,
    never a guessed command that happens to share a word.
    """

    from auto_email_sender_cli.capabilities import get_capability

    candidates: list[tuple[str, str]] = []
    if capability is None:
        return []
    if command == "wait":
        return []
    if command == "plans.execute":
        candidates.append(("plans.show", "读取执行后的计划状态和结果"))
    elif capability.requires_plan:
        candidates.extend(
            [
                ("plans.show", "读取生成的影响预览和确认状态"),
                ("plans.execute", "仅在用户明确确认该计划后执行"),
            ],
        )
    elif command in _EXPLICIT_NEXT_ACTIONS:
        candidates.extend(_EXPLICIT_NEXT_ACTIONS[command])
    elif capability.mutates:
        candidates.append((f"{capability_resource(command)}.get", "重新读取受影响对象"))
    elif capability.long_running:
        candidates.append((f"{capability_resource(command)}.get", "读取任务状态；queued/running 不是完成"))
    elif command.endswith(".list"):
        candidates.append((command[:-5] + ".get", "使用稳定 ID 读取单个对象"))
    elif command.endswith(".get") and capability_stateful(command):
        candidates.append((command[:-4] + ".items", "读取逐项结果"))
    # Generic waiting is exposed as a real command in the CLI.  Only suggest it
    # for stateful long-running contracts.
    if supports_wait(command):
        candidates.append(("wait", "在超时内等待已运行的任务状态变化；不会启动桌面应用"))
    actions: list[dict[str, object]] = []
    for next_command, reason in candidates:
        registered = get_capability(next_command)
        if registered is None and next_command != "wait":
            continue
        actions.append(
            {
                "command": next_command,
                "reason": reason,
                "blocked_reason": None,
            },
        )
    return actions


def validate_command_contract(contract: dict[str, object]) -> list[str]:
    """Return deterministic contract violations for CI and local doctor tests."""

    errors: list[str] = []
    for key in CONTRACT_REQUIRED_KEYS:
        if key not in contract:
            errors.append(f"missing:{key}")
    if contract.get("contract_version") != CONTRACT_VERSION:
        errors.append("invalid:contract_version")
    for key in ("input", "output", "effects", "preconditions"):
        if not isinstance(contract.get(key), dict):
            errors.append(f"invalid:{key}")
    for key in ("state_transitions", "errors", "next_actions"):
        if not isinstance(contract.get(key), list):
            errors.append(f"invalid:{key}")
    input_contract = contract.get("input")
    if isinstance(input_contract, dict):
        schema = input_contract.get("schema")
        if not isinstance(schema, dict) or schema.get("type") != "object":
            errors.append("invalid:input.schema")
        properties = schema.get("properties") if isinstance(schema, dict) else None
        if isinstance(properties, dict):
            for name, property_schema in properties.items():
                if not isinstance(property_schema, dict) or "type" not in property_schema:
                    errors.append(f"invalid:input.schema.properties.{name}")
    output_contract = contract.get("output")
    if isinstance(output_contract, dict):
        schema = output_contract.get("schema")
        if not isinstance(schema, dict) or schema.get("type") != "object":
            errors.append("invalid:output.schema")
        properties = schema.get("properties") if isinstance(schema, dict) else None
        if not isinstance(properties, dict) or "data" not in properties:
            errors.append("invalid:output.schema.properties.data")
        elif not isinstance(properties["data"], dict) or "type" not in properties["data"]:
            errors.append("invalid:output.schema.properties.data.type")
    return errors
