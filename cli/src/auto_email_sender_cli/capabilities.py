from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable, Mapping
from dataclasses import asdict, dataclass
from typing import Final, Literal

from auto_email_sender_cli.operation_specs import effect_has_external_action, get_operation_spec
from auto_email_sender_cli.version import get_build_identity


RiskLevel = Literal["L0", "L1", "L2", "L3"]
Availability = Literal["available", "planned", "ui_only", "unsupported_on_platform"]

CONTRACT_VERSION: Final = "3"
CAPABILITY_CATALOG_VERSION: Final = "3"


# Discovery must be cheap enough to use at the start of every Agent turn.
# These are deliberately resource-level descriptions: operation-specific
# contracts remain in ``describe`` so this table cannot become another manual.
_DISCOVERY_RESOURCE_SUMMARIES: Final[dict[str, str]] = {
    "system": "CLI 版本、运行状态、诊断和协议发现",
    "professors": "导师档案、标签、导入导出和社区资料",
    "professors.tags": "导师标签及批量标签变更",
    "professors.community": "社区导师目录、字段比对和导入计划",
    "communications": "邮件线程、邮件记录与邮箱同步",
    "templates": "邮件模板的读取、导入和维护",
    "materials": "参考材料和真实邮件附件",
    "identities": "发件身份及 SMTP/IMAP 连接检查",
    "llm-profiles": "已保存模型配置和连接检查",
    "matching": "导师匹配分析任务",
    "enrichment": "导师公开资料补全任务",
    "drafts": "单封邮件草稿的生成、编辑和发送准备",
    "campaigns": "批量草稿活动、审核和发送准备",
    "crawler": "公开网页抓取、候选审核和导入准备",
    "communication-groups": "发件身份间的通信历史共享范围",
    "test-email": "发送到本人地址的测试邮件",
    "dashboard": "工作概览",
    "usage": "模型 Token 使用记录与汇总",
    "diagnostics": "已脱敏的运行诊断信息",
    "settings": "不含凭据的运行设置",
    "workspaces": "单位导师的邮件工作区",
    "tasks": "单封任务的状态和写信设置",
    "plans": "高风险操作的确认计划",
}

_SYSTEM_DISCOVERY_COMMANDS: Final[frozenset[str]] = frozenset(
    {"version", "status", "doctor", "guide", "capabilities", "describe", "invoke", "wait"},
)


# Collection behavior is deliberately explicit instead of inferred from a
# command's suffix.  A few endpoints contain a bounded nested list (for
# example community comparison records) but do not expose the common cursor
# contract; advertising them as paged would make an Agent send unsupported
# flags.  Keeping the sets here also makes protocol changes reviewable.
_PAGED_COLLECTION_COMMANDS: Final[frozenset[str]] = frozenset(
    {
        "professors.list",
        "professors.tags.list",
        "communications.threads.list",
        "communications.messages.list",
        "templates.list",
        "materials.list",
        "identities.list",
        "llm-profiles.list",
        "matching.jobs.list",
        "matching.jobs.items",
        "enrichment.jobs.list",
        "enrichment.jobs.items",
        "crawler.jobs.list",
        "crawler.jobs.pages",
        "crawler.jobs.events",
        "crawler.jobs.candidates",
        "communication-groups.list",
        "campaigns.list",
        "campaigns.items",
        "usage.records",
        "diagnostics.logs",
    },
)


# These endpoints return a bounded ``records`` array inside a comparison
# envelope rather than the common cursor page.  They still support the CLI's
# safe local projection flag; keeping the set separate prevents us from
# advertising pagination or structured filtering they do not implement.
_FIELD_SELECTION_COMMANDS: Final[frozenset[str]] = frozenset(
    {
        "professors.community.records",
        "professors.community.preview",
    },
)


_FILE_EXPORT_COMMANDS: Final[frozenset[str]] = frozenset(
    {
        "professors.export",
        "professors.community.export-package",
        "communications.messages.export",
        "diagnostics.export",
        "diagnostics.crawler-debug",
    }
    | _PAGED_COLLECTION_COMMANDS
)


# ``wait`` is useful only when the command returns or observes a resource with
# a background lifecycle.  Keeping this allow-list explicit avoids suggesting
# a poll after ordinary list, pause, archive, or plan-preview operations.
_WAIT_CAPABILITY_COMMANDS: Final[frozenset[str]] = frozenset(
    {
        "matching.jobs.create",
        "matching.jobs.get",
        "matching.jobs.items",
        "matching.jobs.retry-failed",
        "enrichment.jobs.create",
        "enrichment.jobs.get",
        "enrichment.jobs.items",
        "enrichment.jobs.retry-failed",
        "crawler.jobs.create",
        "crawler.jobs.get",
        "crawler.jobs.pages",
        "crawler.jobs.events",
        "crawler.jobs.candidates",
        "crawler.jobs.resume",
        "crawler.jobs.enrich",
        "campaigns.get",
        "campaigns.items",
        "campaigns.start-drafts",
        "campaigns.retry-item-draft",
    }
)


# Optimistic concurrency is deliberately advertised only for routes that
# actually consume the ``If-Revision`` header in the Agent API.  Accepting the
# global flag on every POST would make an Agent believe a write was protected
# even when the endpoint simply ignored the header.
_IF_REVISION_COMMANDS: Final[frozenset[str]] = frozenset(
    {
        "professors.update",
        "professors.archive",
        "professors.restore",
        "professors.tags.set",
        "templates.update",
        "templates.set-default",
        "templates.restore",
        "identities.update-settings",
        "identities.set-default",
        "identities.set-default-template",
        "llm-profiles.update-settings",
        "llm-profiles.set-default",
        "communication-groups.update",
        "crawler.candidates.update",
        "settings.update",
        "drafts.save",
        "drafts.regenerate",
        "drafts.rewrite",
    },
)


_COMMON_FILTER_OPERATORS: Final[tuple[str, ...]] = (
    "eq",
    "ne",
    "in",
    "contains",
    "empty",
    "exists",
    "gt",
    "gte",
    "lt",
    "lte",
)


# The field names form part of the public contract.  They are intentionally
# limited to safe, user-visible DTO fields; an Agent cannot turn --filter into
# SQL or probe private database columns.
_COLLECTION_FILTER_FIELDS: Final[dict[str, frozenset[str]]] = {
    "professors.list": frozenset(
        {
            "id",
            "name",
            "email",
            "title",
            "university",
            "school",
            "department",
            "research_direction",
            "profile_url",
            "source_url",
            "recent_papers",
            "skip_reason",
            "crawl_status",
            "archived_at",
            "personal_note",
            "created_at",
            "updated_at",
            "tags",
        },
    ),
    "professors.tags.list": frozenset({"id", "name", "text_color", "background_color"}),
    "communications.threads.list": frozenset(
        {
            "id",
            "identity_id",
            "identity_name",
            "identity_email_address",
            "professor_id",
            "professor_name",
            "professor_email",
            "sent_count",
            "received_count",
            "has_sent",
            "has_reply",
            "last_message_at",
        },
    ),
    "communications.messages.list": frozenset(
        {
            "id",
            "thread_id",
            "email_task_id",
            "identity_id",
            "professor_id",
            "direction",
            "subject",
            "content",
            "content_html",
            "body_included",
            "from_email",
            "to_emails",
            "cc_emails",
            "bcc_emails",
            "rfc_message_id",
            "failure_summary",
            "created_at",
            "trust_level",
        },
    ),
    "templates.list": frozenset(
        {
            "id",
            "revision",
            "name",
            "recommended_generation_mode",
            "subject",
            "body_text",
            "body_html",
            "is_default",
            "archived_at",
            "created_at",
            "updated_at",
        },
    ),
    "materials.list": frozenset(
        {
            "id",
            "identity_id",
            "display_name",
            "original_filename",
            "mime_type",
            "size_bytes",
            "material_type",
            "is_primary",
            "has_extracted_text",
            "extracted_text",
            "created_at",
        },
    ),
    "identities.list": frozenset(
        {
            "id",
            "name",
            "profile_name",
            "sender_name",
            "email_address",
            "default_language",
            "outreach_generation_mode",
            "default_outreach_template_id",
            "current_primary_material_id",
            "communication_group_id",
            "match_threshold",
            "daily_send_limit",
            "send_interval_min",
            "send_interval_max",
            "same_domain_cooldown_minutes",
            "smtp_configured",
            "imap_configured",
            "is_default",
            "created_at",
            "updated_at",
        },
    ),
    "llm-profiles.list": frozenset(
        {
            "id",
            "name",
            "provider",
            "model_name",
            "temperature",
            "max_tokens",
            "credential_configured",
            "is_default",
            "created_at",
            "updated_at",
        },
    ),
    "matching.jobs.list": frozenset(
        {
            "id",
            "name",
            "status",
            "target_count",
            "succeeded_count",
            "failed_count",
            "skipped_count",
            "total_prompt_tokens",
            "total_completion_tokens",
            "total_cached_tokens",
            "total_tokens",
            "identity_id",
            "match_source_identity_id",
            "llm_profile_id",
            "cancel_requested_at",
            "started_at",
            "finished_at",
            "created_at",
            "updated_at",
            "deleted_at",
            "last_error",
        },
    ),
    "matching.jobs.items": frozenset(
        {
            "id",
            "job_id",
            "professor_id",
            "professor_name",
            "professor_email",
            "professor_title",
            "professor_university",
            "professor_school",
            "email_task_id",
            "status",
            "match_score",
            "match_analysis_run_id",
            "error_message",
            "skip_reason",
            "prompt_tokens",
            "completion_tokens",
            "cached_tokens",
            "total_tokens",
            "started_at",
            "finished_at",
            "updated_at",
        },
    ),
    "enrichment.jobs.list": frozenset({"id", "name", "trigger_mode", "status", "target_count", "completed_count", "queued_count", "running_count", "succeeded_count", "failed_count", "skipped_count", "canceled_count", "input_tokens", "output_tokens", "cached_tokens", "total_tokens", "llm_profile_id", "started_at", "finished_at", "duration_seconds", "created_at", "updated_at", "last_error", "deleted_at"}),
    "enrichment.jobs.items": frozenset({"id", "job_id", "professor_id", "professor_name", "professor_email", "professor_title", "professor_university", "professor_school", "professor_department", "profile_url", "status", "enriched_fields", "error_message", "skip_reason", "input_tokens", "output_tokens", "cached_tokens", "total_tokens", "attempt_count", "started_at", "finished_at", "created_at", "updated_at"}),
    "crawler.jobs.list": frozenset({"id", "university", "school", "start_url", "start_urls", "entry_type", "llm_profile_id", "status", "progress_current", "progress_total", "error_message", "page_count", "candidate_count", "latest_event_message", "input_tokens", "output_tokens", "cached_tokens", "total_tokens", "duration_seconds", "created_at", "updated_at", "deleted_at"}),
    "crawler.jobs.pages": frozenset({"id", "job_id", "url", "parent_url", "fetch_method", "page_type", "status", "title", "text_excerpt", "error_message", "created_at", "trust_level"}),
    "crawler.jobs.events": frozenset({"id", "job_id", "event_type", "message", "created_at", "raw", "trust_level"}),
    "crawler.jobs.candidates": frozenset({"id", "revision", "job_id", "professor_id", "name", "email", "title", "university", "school", "department", "research_direction", "recent_papers", "profile_url", "source_url", "confidence", "field_confidence", "evidence", "review_status", "created_at", "updated_at", "trust_level"}),
    "communication-groups.list": frozenset({"id", "revision", "members", "match_source_identity_id", "created_at", "updated_at"}),
    "campaigns.list": frozenset({"id", "name", "status", "generation_mode", "schedule_type", "target_count", "pending_generation_count", "generating_draft_count", "draft_failed_count", "review_required_count", "approved_count", "scheduled_count", "sending_count", "sent_count", "failed_count", "canceled_count", "canceled_send_count", "can_start_draft_generation", "created_at", "updated_at"}),
    "campaigns.items": frozenset({"id", "campaign_id", "professor_id", "professor_name", "professor_email", "status", "generation_mode", "subject", "has_final_content", "attachment_material_ids", "scheduled_at", "send_canceled_at", "sent_at", "last_error", "can_remove", "can_cancel_send", "can_restore_send", "can_retry_draft", "updated_at"}),
    "usage.records": frozenset({"id", "feature_type", "feature_label", "title", "input_tokens", "output_tokens", "cached_tokens", "total_tokens", "model_name", "identity_name", "created_at", "status"}),
    "diagnostics.logs": frozenset({"id", "request_id", "category", "event_name", "level", "message", "entity_type", "entity_id", "metadata", "created_at"}),
}


@dataclass(frozen=True, slots=True)
class Capability:
    command: str
    summary: str
    risk_level: RiskLevel
    availability: Availability
    mutates: bool = False
    external_action: bool = False
    requires_plan: bool = False
    long_running: bool = False
    # Kept only while command handlers and older callers still pass it.  The
    # runtime protocol no longer advertises static prose-guide topics.
    guide_topic: str = "overview"
    unavailable_reason: str | None = None
    manual_action: str | None = None
    ui_location: str | None = None

    def to_dict(self) -> dict[str, object]:
        """Return the single registry record consumed by discovery clients.

        The original protocol fields are kept intact.  The additional contract
        metadata is derived here so a new command cannot accidentally appear in
        ``describe`` and ``capabilities`` with different resource semantics.
        Detailed input/output schemas are supplied by ``describe``; these fields
        let an Agent decide which commands are worth describing first.
        """

        spec = _require_operation_spec(self.command)
        result = asdict(self)
        result.pop("guide_topic", None)
        manual_action = result.pop("manual_action", None)
        ui_location = result.pop("ui_location", None)
        result.update(
            {
                "contract_version": CONTRACT_VERSION,
                "resource": discovery_resource(self.command),
                "operation": capability_operation(self.command),
                "is_leaf": True,
                "supports_pagination": supports_pagination(self.command),
                "supports_field_selection": supports_field_selection(self.command),
                "supports_structured_filter": supports_structured_filter(self.command),
                "supports_file_export": supports_file_export(self.command),
                "filter_fields": sorted(collection_filter_fields(self.command)),
                "filter_operators": list(_COMMON_FILTER_OPERATORS)
                if supports_structured_filter(self.command)
                else [],
                "supports_wait": supports_wait(self.command),
                "supports_if_revision": supports_if_revision(self.command),
                "supports_idempotent_retry": spec.idempotency.supports_idempotent_retry,
                # Derive this from the semantic manifest.  Delegated gateways
                # are external-capable even when their concrete service list
                # is resolved only after reading a target/plan.
                "external_action": effect_has_external_action(spec.effects),
                "risk_mode": spec.effects.risk_mode,
                "plan_role": spec.effects.plan_role,
                "delegated_effects": spec.effects.delegated_effects,
                "requires_target_contract": spec.effects.requires_target_contract,
                "confirmation_required_before_invocation": spec.effects.requires_confirmation_plan,
                "produces_confirmation_plan": spec.effects.produces_confirmation_plan,
                "stateful": spec.stateful,
                "introduced_in": spec.introduced_in,
                "deprecated": spec.deprecated,
                "replaced_by": list(spec.replaced_by),
            },
        )
        if self.availability != "available":
            result["manual_action"] = {
                "type": "open_desktop_ui",
                "location": ui_location,
                "instruction": manual_action,
            }
        return result


CAPABILITIES: Final[tuple[Capability, ...]] = (
    Capability("version", "查看 CLI 与协议版本", "L0", "available"),
    Capability("status", "查看桌面应用和本地服务状态", "L0", "available"),
    Capability("doctor", "检查 CLI、Skill、运行文件和本地服务", "L0", "available"),
    Capability("guide", "读取已废弃的兼容使用约定；命令契约请使用 describe", "L0", "available"),
    Capability("capabilities", "读取当前命令能力和风险信息", "L0", "available"),
    Capability("describe", "读取某个命令的机器可读操作说明", "L0", "available"),
    Capability(
        "invoke",
        "通过 delegated gateway 调用已发布命令；必须先读取目标命令合同",
        "L0",
        "available",
        external_action=True,
    ),
    Capability("wait", "等待已运行的后台任务进入终态，不会启动桌面应用", "L0", "available", long_running=True),
    Capability("professors.list", "分页查询或读取全部导师档案", "L0", "available"),
    Capability("professors.get", "按 ID 读取导师完整档案", "L0", "available"),
    Capability("professors.tags.list", "读取导师标签", "L0", "available"),
    Capability("professors.tags.usage", "读取一个标签及其关联导师", "L0", "available"),
    Capability("professors.create", "新增一位导师", "L1", "available", mutates=True),
    Capability("professors.update", "局部修改一位导师", "L1", "available", mutates=True),
    Capability("professors.archive", "将一位导师移入回收站", "L1", "available", mutates=True),
    Capability("professors.restore", "恢复一位已归档导师", "L1", "available", mutates=True),
    Capability("professors.tags.create", "新增导师标签", "L1", "available", mutates=True),
    Capability("professors.tags.set", "设置一位导师的全部标签", "L1", "available", mutates=True),
    Capability(
        "professors.tags.prepare-bulk",
        "生成批量追加、移除或替换导师标签的影响预览和确认计划",
        "L2",
        "available",
        mutates=True,
        requires_plan=True,
        guide_topic="safety",
    ),
    Capability(
        "professors.prepare-bulk-archive",
        "生成批量移入导师回收站的影响预览和确认计划",
        "L2",
        "available",
        mutates=True,
        requires_plan=True,
        guide_topic="safety",
    ),
    Capability(
        "professors.tags.prepare-delete",
        "生成删除导师标签及解除关联的影响预览和确认计划",
        "L2",
        "available",
        mutates=True,
        requires_plan=True,
        guide_topic="safety",
    ),
    Capability(
        "professors.import",
        "从 CSV 或 XLSX 生成导师导入预览和确认计划",
        "L2",
        "available",
        mutates=True,
        requires_plan=True,
        guide_topic="safety",
    ),
    Capability(
        "professors.export",
        "导出导师表格",
        "L0",
        "available",
    ),
    Capability(
        "professors.community.catalog",
        "读取或刷新社区导师目录",
        "L1",
        "available",
        external_action=True,
        guide_topic="community",
    ),
    Capability(
        "professors.community.records",
        "读取选定学院的社区导师及本地比对结果",
        "L0",
        "available",
        guide_topic="community",
    ),
    Capability(
        "professors.community.preview",
        "预览指定社区导师与本地档案的字段差异",
        "L0",
        "available",
        guide_topic="community",
    ),
    Capability(
        "professors.community.import",
        "生成社区导师导入的字段选择、冲突处理和确认计划",
        "L2",
        "available",
        mutates=True,
        requires_plan=True,
        guide_topic="community",
    ),
    Capability(
        "professors.community.export-package",
        "导出可提交给社区的导师共享包",
        "L0",
        "available",
        guide_topic="community",
    ),
    Capability(
        "communications.threads.list",
        "按已发送、已回复、身份和导师筛选通信线程",
        "L0",
        "available",
        guide_topic="communications",
    ),
    Capability(
        "communications.threads.get",
        "读取一个通信线程及其邮件，可按需包含正文",
        "L0",
        "available",
        guide_topic="communications",
    ),
    Capability(
        "communications.messages.list",
        "分页或完整读取发件、收件和草稿记录",
        "L0",
        "available",
        guide_topic="communications",
    ),
    Capability(
        "communications.messages.get",
        "按 ID 读取一封邮件的完整正文",
        "L0",
        "available",
        guide_topic="communications",
    ),
    Capability(
        "communications.messages.export",
        "把大量邮件和完整正文导出为 JSONL",
        "L0",
        "available",
        guide_topic="communications",
    ),
    Capability(
        "communications.sync",
        "手动同步邮箱通信记录",
        "L1",
        "available",
        mutates=True,
        external_action=True,
        long_running=True,
        guide_topic="communications",
    ),
    Capability("templates.list", "查询邮件模板", "L0", "available"),
    Capability("templates.get", "按 ID 读取完整模板内容", "L0", "available"),
    Capability(
        "templates.import-file",
        "解析本地 DOCX、HTML、Markdown 或文本邮件模板，不会保存或发送",
        "L0",
        "available",
    ),
    Capability("templates.create", "新增邮件模板", "L1", "available", mutates=True),
    Capability("templates.update", "局部修改邮件模板", "L1", "available", mutates=True),
    Capability("templates.duplicate", "复制邮件模板", "L1", "available", mutates=True),
    Capability("templates.set-default", "设为全局默认邮件模板", "L1", "available", mutates=True),
    Capability("templates.restore", "恢复已归档邮件模板", "L1", "available", mutates=True),
    Capability(
        "templates.prepare-archive",
        "生成邮件模板归档的影响预览和确认计划",
        "L2",
        "available",
        mutates=True,
        requires_plan=True,
        guide_topic="sending",
    ),
    Capability(
        "materials.list",
        "查询可作为 AI 参考或附件的材料元数据",
        "L0",
        "available",
        guide_topic="materials",
    ),
    Capability(
        "materials.get",
        "按 ID 读取材料元数据和可选的已提取文本",
        "L0",
        "available",
        guide_topic="materials",
    ),
    Capability(
        "materials.upload",
        "从本地文件上传 AI 参考材料或邮件附件",
        "L1",
        "available",
        mutates=True,
        guide_topic="materials",
    ),
    Capability(
        "materials.set-primary",
        "设置一份可提取文本的默认 AI 参考材料",
        "L1",
        "available",
        mutates=True,
        guide_topic="materials",
    ),
    Capability(
        "materials.download",
        "下载已保存的材料文件",
        "L0",
        "available",
        guide_topic="materials",
    ),
    Capability(
        "materials.prepare-delete",
        "生成材料删除的影响预览和确认计划",
        "L2",
        "available",
        mutates=True,
        requires_plan=True,
        guide_topic="materials",
    ),
    Capability("identities.list", "查询发件身份的脱敏视图", "L0", "available"),
    Capability("identities.get", "按 ID 读取发件身份的脱敏视图", "L0", "available"),
    Capability(
        "identities.update-settings",
        "修改发件身份的显示名、写信策略和发送频率，不会读取或修改连接与密码",
        "L1",
        "available",
        mutates=True,
        guide_topic="identities",
    ),
    Capability(
        "identities.set-default",
        "设为默认发件身份",
        "L1",
        "available",
        mutates=True,
        guide_topic="identities",
    ),
    Capability(
        "identities.set-default-template",
        "设置或清除某个发件身份的默认邮件模板",
        "L1",
        "available",
        mutates=True,
        guide_topic="identities",
    ),
    Capability(
        "identities.test-smtp",
        "使用已保存的 SMTP 凭据测试发件连接，不返回密码",
        "L1",
        "available",
        mutates=True,
        external_action=True,
        guide_topic="identities",
    ),
    Capability(
        "identities.test-imap",
        "使用已保存的 IMAP 凭据测试收件连接，不返回密码",
        "L1",
        "available",
        mutates=True,
        external_action=True,
        guide_topic="identities",
    ),
    Capability(
        "identities.credentials",
        "创建或删除发件身份，以及编辑邮件服务器、账号和密码",
        "L1",
        "ui_only",
        mutates=True,
        unavailable_reason="为避免密码出现在 Agent 对话、命令行历史或进程参数中，邮件服务器、账号和凭据只能在桌面端安全录入。",
        manual_action="在桌面端创建或编辑发件身份，并在连接设置中录入 SMTP/IMAP 凭据。",
        ui_location="个人中心 > 发件身份",
    ),
    Capability("llm-profiles.list", "查询模型配置的脱敏视图", "L0", "available"),
    Capability("llm-profiles.get", "按 ID 读取模型配置的脱敏视图", "L0", "available"),
    Capability(
        "llm-profiles.update-settings",
        "修改模型配置名称、模型名、温度和输出 Token 上限，不会读取或修改服务地址和 API Key",
        "L1",
        "available",
        mutates=True,
        guide_topic="llm-profiles",
    ),
    Capability(
        "llm-profiles.set-default",
        "切换默认模型配置，不会显示 API Key",
        "L1",
        "available",
        mutates=True,
        guide_topic="llm-profiles",
    ),
    Capability(
        "llm-profiles.models",
        "使用已保存的凭据读取模型列表，不会显示 API Key",
        "L1",
        "available",
        external_action=True,
        guide_topic="llm-profiles",
    ),
    Capability(
        "llm-profiles.test",
        "使用已保存的凭据测试模型连接；可能消耗少量 Token，不会显示 API Key",
        "L2",
        "available",
        mutates=True,
        external_action=True,
        guide_topic="llm-profiles",
    ),
    Capability(
        "llm-profiles.write",
        "创建或删除模型配置，以及编辑服务提供方、服务地址、提示词模板和 API Key",
        "L1",
        "ui_only",
        mutates=True,
        unavailable_reason="为避免 API Key 出现在 Agent 对话、命令行历史或进程参数中，模型服务地址、提示词模板和凭据只能在桌面端安全录入。",
        manual_action="在桌面端创建或编辑模型配置，并在安全表单中录入服务地址与 API Key。",
        ui_location="个人中心 > 模型配置",
    ),
    Capability(
        "matching.jobs.list",
        "分页查询当前或回收站中的匹配分析任务",
        "L0",
        "available",
        guide_topic="matching",
    ),
    Capability(
        "matching.jobs.get",
        "读取一个匹配分析任务的状态、进度和 Token 用量",
        "L0",
        "available",
        guide_topic="matching",
    ),
    Capability(
        "matching.jobs.items",
        "读取匹配分析任务中每位导师的状态、分数和失败原因",
        "L0",
        "available",
        guide_topic="matching",
    ),
    Capability(
        "matching.jobs.create",
        "创建异步匹配分析任务；会调用指定 LLM，但不会发送邮件",
        "L2",
        "available",
        mutates=True,
        external_action=True,
        long_running=True,
        guide_topic="matching",
    ),
    Capability(
        "matching.jobs.cancel",
        "请求取消排队中或运行中的匹配分析任务",
        "L1",
        "available",
        mutates=True,
        guide_topic="matching",
    ),
    Capability(
        "matching.jobs.retry-failed",
        "为失败或已取消的导师项创建新的异步匹配分析任务",
        "L2",
        "available",
        mutates=True,
        external_action=True,
        long_running=True,
        guide_topic="matching",
    ),
    Capability(
        "matching.jobs.delete",
        "将已结束的匹配分析任务移入回收站",
        "L1",
        "available",
        mutates=True,
        guide_topic="matching",
    ),
    Capability(
        "matching.jobs.restore",
        "恢复回收站中的匹配分析任务",
        "L1",
        "available",
        mutates=True,
        guide_topic="matching",
    ),
    Capability("drafts.get", "按任务 ID 读取草稿、参考材料和附件", "L0", "available", guide_topic="drafts"),
    Capability(
        "drafts.generate",
        "按导师、身份、模板、参考材料和附件生成 draft_only 草稿",
        "L1",
        "available",
        mutates=True,
        external_action=True,
        long_running=True,
        guide_topic="drafts",
    ),
    Capability(
        "drafts.save",
        "保存手工主题、正文和附件；不会发送",
        "L1",
        "available",
        mutates=True,
        guide_topic="drafts",
    ),
    Capability(
        "drafts.regenerate",
        "按当前模板配置重新渲染或调用 AI 改写草稿；不会发送",
        "L1",
        "available",
        mutates=True,
        external_action=True,
        long_running=True,
        guide_topic="drafts",
    ),
    Capability(
        "drafts.rewrite",
        "将指定草稿文本交给已配置模型改写；不会发送邮件",
        "L2",
        "available",
        mutates=True,
        external_action=True,
        long_running=True,
        guide_topic="drafts",
    ),
    Capability(
        "drafts.prepare-send",
        "为一封最终草稿创建 30 分钟有效的一次性发送或排程计划",
        "L3",
        "available",
        mutates=True,
        external_action=True,
        requires_plan=True,
        guide_topic="sending",
    ),
    Capability("campaigns.list", "分页查询当前或回收站中的批量活动", "L0", "available", guide_topic="campaigns"),
    Capability("campaigns.get", "读取一个批量活动的状态、草稿和发送进度", "L0", "available", guide_topic="campaigns"),
    Capability(
        "campaigns.resend-context",
        "读取旧活动可重新发起的导师、原模板、材料和警告",
        "L0",
        "available",
        guide_topic="campaigns",
    ),
    Capability("campaigns.items", "分页读取活动中的导师、草稿状态和主题", "L0", "available", guide_topic="campaigns"),
    Capability(
        "campaigns.create",
        "生成暂停批量草稿活动的影响预览和确认计划",
        "L2",
        "available",
        mutates=True,
        requires_plan=True,
        guide_topic="campaigns",
    ),
    Capability(
        "campaigns.start-drafts",
        "启动活动中待处理的 AI 草稿生成；会调用模型，但不会发送邮件",
        "L2",
        "available",
        mutates=True,
        external_action=True,
        long_running=True,
        guide_topic="campaigns",
    ),
    Capability(
        "campaigns.pause",
        "暂停活动，停止后续草稿生成和批量邮件调度",
        "L1",
        "available",
        mutates=True,
        guide_topic="campaigns",
    ),
    Capability(
        "campaigns.stop",
        "终止活动并取消尚未开始发送的活动项",
        "L1",
        "available",
        mutates=True,
        guide_topic="campaigns",
    ),
    Capability(
        "campaigns.archive",
        "将已停止或已结束的活动移入回收站",
        "L1",
        "available",
        mutates=True,
        guide_topic="campaigns",
    ),
    Capability(
        "campaigns.restore",
        "从回收站恢复活动，不会重新授权发送",
        "L1",
        "available",
        mutates=True,
        guide_topic="campaigns",
    ),
    Capability(
        "campaigns.remove-item",
        "从尚未获准发送的活动中移除一位导师",
        "L1",
        "available",
        mutates=True,
        guide_topic="campaigns",
    ),
    Capability(
        "campaigns.cancel-item-send",
        "取消一个未来定时活动项的发送",
        "L1",
        "available",
        mutates=True,
        guide_topic="campaigns",
    ),
    Capability(
        "campaigns.prepare-restore-item-send",
        "为一个已取消的未来定时活动项生成恢复发送确认计划",
        "L3",
        "available",
        mutates=True,
        external_action=True,
        requires_plan=True,
        guide_topic="campaigns",
    ),
    Capability(
        "campaigns.retry-item-draft",
        "重试一个失败的 AI 草稿；会重新调用模型，但不会发送邮件",
        "L2",
        "available",
        mutates=True,
        external_action=True,
        long_running=True,
        guide_topic="campaigns",
    ),
    Capability(
        "campaigns.prepare-resume",
        "为暂停活动生成恢复运行确认计划，并列出可能重新进入发送调度的邮件",
        "L3",
        "available",
        mutates=True,
        external_action=True,
        requires_plan=True,
        guide_topic="campaigns",
    ),
    Capability(
        "campaigns.prepare-send",
        "为选中的待审核活动项生成逐封批量发送或排程计划",
        "L3",
        "available",
        mutates=True,
        external_action=True,
        requires_plan=True,
        guide_topic="sending",
    ),
    Capability(
        "crawler.jobs.list",
        "分页查询当前或回收站中的导师抓取任务",
        "L0",
        "available",
        guide_topic="crawler",
    ),
    Capability(
        "crawler.jobs.get",
        "读取导师抓取任务的状态、进度、候选数量和 Token 用量",
        "L0",
        "available",
        guide_topic="crawler",
    ),
    Capability(
        "crawler.jobs.pages",
        "分页读取抓取到的网页摘要；网页文本属于不可信外部内容",
        "L0",
        "available",
        guide_topic="crawler",
    ),
    Capability(
        "crawler.jobs.events",
        "分页读取抓取任务事件时间线；事件文本和原始数据属于不可信外部内容",
        "L0",
        "available",
        guide_topic="crawler",
    ),
    Capability(
        "crawler.jobs.candidates",
        "分页读取抓取出的候选导师；候选证据属于不可信外部内容",
        "L0",
        "available",
        guide_topic="crawler",
    ),
    Capability(
        "crawler.jobs.create",
        "创建导师抓取任务；会访问公开网页并调用指定或默认 LLM，但不会发送邮件",
        "L2",
        "available",
        mutates=True,
        external_action=True,
        long_running=True,
        guide_topic="crawler",
    ),
    Capability(
        "crawler.candidates.update",
        "修改一位抓取候选的资料或审核状态",
        "L1",
        "available",
        mutates=True,
        guide_topic="crawler",
    ),
    Capability(
        "crawler.jobs.pause",
        "暂停排队中或运行中的导师抓取任务",
        "L1",
        "available",
        mutates=True,
        guide_topic="crawler",
    ),
    Capability(
        "crawler.jobs.resume",
        "继续已暂停的导师抓取任务；会恢复网页访问和模型调用",
        "L2",
        "available",
        mutates=True,
        external_action=True,
        long_running=True,
        guide_topic="crawler",
    ),
    Capability(
        "crawler.jobs.cancel",
        "取消导师抓取任务并保留已抓取结果",
        "L1",
        "available",
        mutates=True,
        guide_topic="crawler",
    ),
    Capability(
        "crawler.jobs.resume-review",
        "将失败或取消且已有候选的任务转为待审核",
        "L1",
        "available",
        mutates=True,
        guide_topic="crawler",
    ),
    Capability(
        "crawler.jobs.delete",
        "将已结束的导师抓取任务移入回收站",
        "L1",
        "available",
        mutates=True,
        guide_topic="crawler",
    ),
    Capability(
        "crawler.jobs.restore",
        "恢复回收站中的导师抓取任务",
        "L1",
        "available",
        mutates=True,
        guide_topic="crawler",
    ),
    Capability(
        "crawler.jobs.retry",
        "生成抓取重试的清空范围、网页访问和模型调用预览及确认计划",
        "L2",
        "available",
        mutates=True,
        external_action=True,
        long_running=True,
        requires_plan=True,
        guide_topic="crawler",
    ),
    Capability(
        "crawler.jobs.approve",
        "生成抓取候选导入导师库的逐项影响预览和确认计划",
        "L2",
        "available",
        mutates=True,
        requires_plan=True,
        guide_topic="crawler",
    ),
    Capability(
        "crawler.jobs.enrich",
        "将指定候选加入资料补全队列；会访问公开主页并调用模型，但不会发送邮件",
        "L2",
        "available",
        mutates=True,
        external_action=True,
        long_running=True,
        guide_topic="crawler",
    ),
    Capability(
        "communication-groups.list",
        "分页查询通信共享组及成员",
        "L0",
        "available",
        guide_topic="communication-groups",
    ),
    Capability(
        "communication-groups.get",
        "读取一个通信共享组及其成员",
        "L0",
        "available",
        guide_topic="communication-groups",
    ),
    Capability(
        "communication-groups.create",
        "创建通信共享组；合并已有组必须显式确认",
        "L1",
        "available",
        mutates=True,
        guide_topic="communication-groups",
    ),
    Capability(
        "communication-groups.update",
        "更新通信共享组成员；合并已有组必须显式确认",
        "L1",
        "available",
        mutates=True,
        guide_topic="communication-groups",
    ),
    Capability(
        "communication-groups.delete",
        "删除通信共享组并解除其成员关联",
        "L1",
        "available",
        mutates=True,
        guide_topic="communication-groups",
    ),
    Capability(
        "enrichment.jobs.list",
        "分页查询当前或回收站中的批量导师信息补全任务",
        "L0",
        "available",
        guide_topic="enrichment",
    ),
    Capability(
        "enrichment.jobs.get",
        "读取导师信息补全任务的状态、进度和 Token 用量",
        "L0",
        "available",
        guide_topic="enrichment",
    ),
    Capability(
        "enrichment.jobs.items",
        "读取补全任务中每位导师的状态、已补全字段和失败原因",
        "L0",
        "available",
        guide_topic="enrichment",
    ),
    Capability(
        "enrichment.jobs.create",
        "创建批量导师信息补全任务；会访问导师主页并调用指定 LLM，但不会发送邮件",
        "L2",
        "available",
        mutates=True,
        external_action=True,
        long_running=True,
        guide_topic="enrichment",
    ),
    Capability(
        "enrichment.jobs.cancel",
        "请求取消排队中或运行中的导师信息补全任务",
        "L1",
        "available",
        mutates=True,
        guide_topic="enrichment",
    ),
    Capability(
        "enrichment.jobs.retry-failed",
        "为失败或已取消的导师项创建新的批量信息补全任务",
        "L2",
        "available",
        mutates=True,
        external_action=True,
        long_running=True,
        guide_topic="enrichment",
    ),
    Capability(
        "enrichment.jobs.delete",
        "将已结束的导师信息补全任务移入回收站",
        "L1",
        "available",
        mutates=True,
        guide_topic="enrichment",
    ),
    Capability(
        "enrichment.jobs.restore",
        "恢复回收站中的导师信息补全任务",
        "L1",
        "available",
        mutates=True,
        guide_topic="enrichment",
    ),
    Capability(
        "test-email.status",
        "查看指定发件身份是否已成功发送过测试邮件",
        "L0",
        "available",
        guide_topic="test-email",
    ),
    Capability(
        "test-email.get",
        "读取发给自己的测试邮件草稿、附件选项和历史",
        "L0",
        "available",
        guide_topic="test-email",
    ),
    Capability(
        "test-email.generate",
        "生成测试邮件草稿；AI 模式会调用已保存的模型配置",
        "L2",
        "available",
        mutates=True,
        external_action=True,
        guide_topic="test-email",
    ),
    Capability(
        "test-email.save",
        "保存测试邮件草稿和附件选择，不会发送邮件",
        "L1",
        "available",
        mutates=True,
        guide_topic="test-email",
    ),
    Capability(
        "test-email.prepare-send",
        "生成发送到当前身份自己邮箱的测试邮件确认计划",
        "L3",
        "available",
        mutates=True,
        external_action=True,
        requires_plan=True,
        guide_topic="test-email",
    ),
    Capability(
        "dashboard.overview",
        "读取指定身份的导师匹配、发送、回信和待处理概览",
        "L0",
        "available",
        guide_topic="insights",
    ),
    Capability(
        "usage.records",
        "分页读取已记录的 LLM Token 用量",
        "L0",
        "available",
        guide_topic="insights",
    ),
    Capability(
        "usage.chart",
        "读取指定时间范围的 Token 用量趋势",
        "L0",
        "available",
        guide_topic="insights",
    ),
    Capability(
        "usage.visualization",
        "读取 Token 汇总、分布、模型排行和近期记录",
        "L0",
        "available",
        guide_topic="insights",
    ),
    Capability(
        "diagnostics.logs",
        "按筛选读取已脱敏的操作诊断日志",
        "L0",
        "available",
        guide_topic="diagnostics",
    ),
    Capability(
        "diagnostics.export",
        "导出已脱敏的操作日志与启动诊断信息",
        "L0",
        "available",
        guide_topic="diagnostics",
    ),
    Capability(
        "diagnostics.crawler-debug",
        "导出已脱敏的抓取任务调试 JSONL",
        "L0",
        "available",
        guide_topic="diagnostics",
    ),
    Capability(
        "settings.get",
        "读取不含凭据的运行设置",
        "L0",
        "available",
        guide_topic="settings",
    ),
    Capability(
        "settings.update",
        "修改抓取、匹配和草稿生成的运行设置",
        "L1",
        "available",
        mutates=True,
        guide_topic="settings",
    ),
    Capability(
        "workspaces.get",
        "读取一位导师在指定身份下的邮件工作区、草稿和通信记录",
        "L0",
        "available",
        guide_topic="workspaces",
    ),
    Capability(
        "workspaces.ensure-task",
        "确保一位导师有可继续处理的手动邮件任务；不会发送邮件",
        "L1",
        "available",
        mutates=True,
        guide_topic="workspaces",
    ),
    Capability(
        "workspaces.refresh-replies",
        "按通信共享范围同步一位导师的回信；会连接已配置的 IMAP 邮箱",
        "L1",
        "available",
        mutates=True,
        external_action=True,
        long_running=True,
        guide_topic="workspaces",
    ),
    Capability(
        "tasks.cancel-schedule",
        "取消单封任务的定时状态并回到待审核草稿",
        "L1",
        "available",
        mutates=True,
        guide_topic="tasks",
    ),
    Capability(
        "tasks.continue-manually",
        "为已因批量活动停止而取消的任务创建可继续处理的手动任务",
        "L1",
        "available",
        mutates=True,
        guide_topic="tasks",
    ),
    Capability(
        "tasks.start-follow-up",
        "为已发送或已回信任务创建一封跟进草稿任务",
        "L1",
        "available",
        mutates=True,
        guide_topic="tasks",
    ),
    Capability(
        "tasks.set-primary-material",
        "切换单封任务的 AI 参考材料并重新生成草稿；AI 模式会调用模型",
        "L2",
        "available",
        mutates=True,
        external_action=True,
        long_running=True,
        guide_topic="tasks",
    ),
    Capability(
        "tasks.set-outreach-config",
        "修改单封任务的模板、生成模式或本次模板文本，不会发送邮件",
        "L1",
        "available",
        mutates=True,
        guide_topic="tasks",
    ),
    Capability(
        "tasks.calculate-match",
        "调用模型重新计算单封任务与导师的匹配度，不会发送邮件",
        "L2",
        "available",
        mutates=True,
        external_action=True,
        long_running=True,
        guide_topic="tasks",
    ),
    Capability(
        "plans.show",
        "读取发送计划、最终正文和确认状态",
        "L0",
        "available",
        guide_topic="sending",
    ),
    Capability(
        "plans.execute",
        "在读取具体计划 delegated effects 并获得确认后执行计划",
        "L3",
        "available",
        mutates=True,
        external_action=True,
        requires_plan=True,
        guide_topic="sending",
    ),
    Capability(
        "plans.cancel",
        "取消尚未执行的发送计划",
        "L1",
        "available",
        mutates=True,
        guide_topic="sending",
    ),
)


def list_capabilities(
    command: str | None = None,
    *,
    resource: str | None = None,
    contract_revisions: Mapping[str, str] | None = None,
) -> list[dict[str, object]]:
    """Return the complete records for an explicit full-capability request.

    The public ``capabilities`` command defaults to catalog/card views.  This
    function intentionally keeps the complete form for ``--view full``,
    compatibility callers, and contract tests.
    """

    records: list[dict[str, object]] = []
    for item in _select_capabilities(command, resource=resource):
        record = item.to_dict()
        revision = _contract_revision_for(item, contract_revisions)
        if revision is not None:
            record["contract_revision"] = revision
        records.append(record)
    return records


def list_capability_cards(
    command: str | None = None,
    *,
    resource: str | None = None,
    contract_revisions: Mapping[str, str] | None = None,
) -> list[dict[str, object]]:
    """Return compact command cards suitable for routine Agent discovery."""

    return [
        _capability_card(item, contract_revision=_contract_revision_for(item, contract_revisions))
        for item in _select_capabilities(command, resource=resource)
    ]


def list_resource_catalog(
    command: str | None = None,
    *,
    resource: str | None = None,
) -> list[dict[str, object]]:
    """Return a bounded resource index without enumerating every operation."""

    selected = _select_capabilities(command, resource=resource)
    selected_resources = {
        discovery_resource(item.command)
        for item in selected
    }
    grouped: dict[str, list[Capability]] = {}
    for item in CAPABILITIES:
        resource_name = discovery_resource(item.command)
        if resource_name not in selected_resources:
            continue
        grouped.setdefault(resource_name, []).append(item)

    return [
        _resource_card(discovery_resource, capabilities)
        for discovery_resource, capabilities in grouped.items()
    ]


def capability_catalog_revision(
    contract_revisions: Mapping[str, str] | None = None,
    *,
    commands: Iterable[str] | None = None,
    view: str | None = None,
) -> str:
    """Return a stable short revision for a catalog or selected discovery scope.

    Callers that have the real Click-derived command revisions must pass them
    here.  The manifest revision fallback keeps lower-level library callers
    deterministic, while the public CLI always supplies complete contracts.
    ``view`` is included only for a cacheable presentation scope, so a cached
    resource catalog cannot be mistaken for cached command cards or full data.
    """

    selected_commands = set(commands) if commands is not None else None
    snapshot: list[dict[str, object]] = []
    for item in CAPABILITIES:
        if selected_commands is not None and item.command not in selected_commands:
            continue
        spec = _require_operation_spec(item.command)
        snapshot.append(
            {
                "command": item.command,
                "summary": item.summary,
                "risk_level": item.risk_level,
                "availability": item.availability,
                "mutates": item.mutates,
                "external_action": effect_has_external_action(spec.effects),
                "requires_plan": item.requires_plan,
                "long_running": item.long_running,
                "semantic_revision": spec.manifest_revision(),
                "contract_revision": _contract_revision_for(item, contract_revisions),
            },
        )
    encoded = json.dumps(
        {
            "catalog_version": CAPABILITY_CATALOG_VERSION,
            "contract_version": CONTRACT_VERSION,
            "build": get_build_identity(),
            "view": view,
            "items": snapshot,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:16]


def _select_capabilities(
    command: str | None = None,
    *,
    resource: str | None = None,
) -> tuple[Capability, ...]:
    items = CAPABILITIES
    if command:
        normalized = normalize_capability_command(command)
        items = tuple(
            item
            for item in CAPABILITIES
            if item.command == normalized or item.command.startswith(f"{normalized}.")
        )
    if resource:
        normalized_resource = normalize_capability_command(resource)
        items = tuple(
            item
            for item in items
            if (
                discovery_resource(item.command) == normalized_resource
                or discovery_resource(item.command).startswith(f"{normalized_resource}.")
            )
        )
    return items


def _capability_card(
    item: Capability,
    *,
    contract_revision: str | None,
) -> dict[str, object]:
    """Keep the routing facts while omitting detailed schema duplicates."""

    spec = _require_operation_spec(item.command)
    card: dict[str, object] = {
        "command": item.command,
        "summary": item.summary,
        "resource": discovery_resource(item.command),
        "operation": capability_operation(item.command),
        "availability": item.availability,
        "risk_level": item.risk_level,
        "risk_mode": spec.effects.risk_mode,
        "plan_role": spec.effects.plan_role,
        "delegated_effects": spec.effects.delegated_effects,
        "requires_target_contract": spec.effects.requires_target_contract,
        "effects": {
            "mutates": spec.effects.mutates,
            "external_action": effect_has_external_action(spec.effects),
            "delegated_effects": spec.effects.delegated_effects,
            "requires_target_contract": spec.effects.requires_target_contract,
            "confirmation_required_before_invocation": spec.effects.requires_confirmation_plan,
            "produces_confirmation_plan": spec.effects.produces_confirmation_plan,
            "current": {
                "mutates": spec.effects.mutates,
                "external_services": list(spec.effects.external_services),
                "cost_may_apply": spec.effects.cost_may_apply,
            },
            "downstream": {
                "mutates": spec.effects.downstream_mutates,
                "external_services": list(spec.effects.downstream_external_services),
                "cost_may_apply": spec.effects.downstream_cost_may_apply,
            },
            "long_running": item.long_running,
        },
        "contract_version": CONTRACT_VERSION,
        "introduced_in": spec.introduced_in,
        "deprecated": spec.deprecated,
        "replaced_by": list(spec.replaced_by),
    }
    if contract_revision is not None:
        card["contract_revision"] = contract_revision
    if item.availability != "available":
        card["unavailable_reason"] = item.unavailable_reason
        card["manual_action"] = {
            "type": "open_desktop_ui",
            "location": item.ui_location,
            "instruction": item.manual_action,
        }
    return card


def _resource_card(
    resource: str,
    capabilities: list[Capability],
) -> dict[str, object]:
    available_count = sum(item.availability == "available" for item in capabilities)
    risk_levels = sorted(
        {item.risk_level for item in capabilities},
        key=lambda level: int(level[1:]),
    )
    return {
        "resource": resource,
        "summary": _DISCOVERY_RESOURCE_SUMMARIES.get(resource, resource),
        "command_count": len(capabilities),
        "available_count": available_count,
        "unavailable_count": len(capabilities) - available_count,
        "risk_levels": risk_levels,
        "has_mutations": any(
            _require_operation_spec(item.command).effects.mutates
            or _require_operation_spec(item.command).effects.delegated_effects
            for item in capabilities
        ),
        "has_external_actions": any(
            effect_has_external_action(_require_operation_spec(item.command).effects)
            for item in capabilities
        ),
        "has_delegated_gateways": any(
            _require_operation_spec(item.command).effects.delegated_effects
            for item in capabilities
        ),
    }


def discovery_resource(command: str) -> str:
    normalized = normalize_capability_command(command)
    if normalized in _SYSTEM_DISCOVERY_COMMANDS:
        return "system"
    return capability_resource(normalized)


def normalize_capability_command(command: str) -> str:
    """Accept both CLI-style spaces and capability-style dotted identifiers."""

    return re.sub(r"\.+", ".", re.sub(r"\s+", ".", command.strip().lower())).strip(".")


def capability_resource(command: str) -> str:
    """Return the stable resource family for a capability.

    Resource names intentionally remain product-level concepts (``professors``,
    ``communications`` and so on), not database table names.  Nested command
    groups such as ``professors.tags`` are kept together so an Agent can filter
    discovery without guessing an internal route.
    """

    normalized = normalize_capability_command(command)
    if normalized.startswith("communications."):
        return "communications"
    if normalized.startswith("communication-groups."):
        return "communication-groups"
    if normalized.startswith("professors.community."):
        return "professors.community"
    if normalized.startswith("professors.tags."):
        return "professors.tags"
    if normalized.startswith("campaigns."):
        return "campaigns"
    if normalized.startswith("crawler."):
        return "crawler"
    if normalized.startswith("matching."):
        return "matching"
    if normalized.startswith("enrichment."):
        return "enrichment"
    if normalized.startswith("llm-profiles."):
        return "llm-profiles"
    if normalized.startswith("test-email."):
        return "test-email"
    if normalized.startswith("workspaces."):
        return "workspaces"
    return normalized.split(".", 1)[0]


def capability_operation(command: str) -> str:
    normalized = normalize_capability_command(command)
    return normalized.rsplit(".", 1)[-1]


def supports_pagination(command: str) -> bool:
    return normalize_capability_command(command) in _PAGED_COLLECTION_COMMANDS


def supports_field_selection(command: str) -> bool:
    # Collection reads use the same CLI-side projection contract.  The actual
    # backend remains responsible for filtering and pagination.
    normalized = normalize_capability_command(command)
    return normalized in _PAGED_COLLECTION_COMMANDS or normalized in _FIELD_SELECTION_COMMANDS


def supports_structured_filter(command: str) -> bool:
    return normalize_capability_command(command) in _PAGED_COLLECTION_COMMANDS


def supports_file_export(command: str) -> bool:
    normalized = normalize_capability_command(command)
    return normalized in _FILE_EXPORT_COMMANDS or normalized.endswith((".export", ".export-package"))


def supports_wait(command: str) -> bool:
    """Whether the registered generic ``wait`` command can poll this result.

    Some operations are potentially slow because they call an LLM, but they
    complete inside the command and have no standalone job route (for example
    ``drafts.generate`` and ``tasks.calculate-match``).  Only resources with a
    stable ``get`` endpoint and a background lifecycle belong here.
    """

    return normalize_capability_command(command) in _WAIT_CAPABILITY_COMMANDS | {"wait"}


def supports_if_revision(command: str) -> bool:
    return normalize_capability_command(command) in _IF_REVISION_COMMANDS


def collection_filter_fields(command: str) -> frozenset[str]:
    return _COLLECTION_FILTER_FIELDS.get(normalize_capability_command(command), frozenset())


def collection_output_fields(command: str) -> frozenset[str]:
    """Return the stable, user-visible fields for a command's result.

    Collection commands publish their fields directly in
    ``_COLLECTION_FILTER_FIELDS``.  Detail and mutation commands use the same
    resource DTO, so resolving their longest matching collection prefix keeps
    ``describe`` useful without duplicating a second field inventory.
    """

    normalized = normalize_capability_command(command)
    direct = collection_filter_fields(normalized)
    if direct:
        return direct
    matches = [
        (candidate, fields, candidate.rsplit(".", 1)[0])
        for candidate, fields in _COLLECTION_FILTER_FIELDS.items()
        if normalized.startswith(candidate.rsplit(".", 1)[0])
    ]
    if not matches:
        return frozenset()
    _, fields, _ = max(
        matches,
        key=lambda item: (
            len(item[2]),
            1 if item[0].endswith(".list") else 0,
        ),
    )
    return fields


def collection_filter_operators(command: str) -> tuple[str, ...]:
    return _COMMON_FILTER_OPERATORS if supports_structured_filter(command) else ()


def capability_stateful(command: str) -> bool:
    spec = get_operation_spec(normalize_capability_command(command))
    return spec.stateful if spec is not None else False


def supports_dynamic_action_links(command: str) -> bool:
    """Whether a result can expose runtime-resolved executable actions."""

    normalized = normalize_capability_command(command)
    spec = get_operation_spec(normalized)
    return bool(
        normalized in {"plans.show", "wait"}
        or (
            normalized != "invoke"
            and
            spec is not None
            and (
                spec.stateful
                or spec.effects.requires_confirmation_plan
                or spec.effects.produces_confirmation_plan
            )
        )
    )


def _require_operation_spec(command: str):
    spec = get_operation_spec(command)
    if spec is None:
        raise RuntimeError(f"Missing operation manifest for capability: {command}")
    return spec


def _contract_revision_for(
    item: Capability,
    contract_revisions: Mapping[str, str] | None,
) -> str | None:
    if contract_revisions is None:
        return None
    revision = contract_revisions.get(item.command)
    if revision is None:
        if item.availability != "available":
            return None
        raise RuntimeError(f"Missing command contract revision: {item.command}")
    return revision


def get_capability(command: str) -> Capability | None:
    normalized = normalize_capability_command(command)
    return next((item for item in CAPABILITIES if item.command == normalized), None)


def suggest_capabilities(command: str, limit: int = 5) -> list[str]:
    normalized = normalize_capability_command(command)
    if not normalized:
        return [item.command for item in CAPABILITIES[:limit]]
    tokens = set(normalized.replace("-", ".").split("."))
    matches = [
        item.command
        for item in CAPABILITIES
        if normalized in item.command
        or any(token and token in item.command.replace("-", ".") for token in tokens)
    ]
    return matches[:limit]
