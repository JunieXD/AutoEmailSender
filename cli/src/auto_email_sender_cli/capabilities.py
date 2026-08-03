from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Final, Literal


RiskLevel = Literal["L0", "L1", "L2", "L3"]
Availability = Literal["available", "planned", "ui_only"]


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
    guide_topic: str = "overview"
    unavailable_reason: str | None = None

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


CAPABILITIES: Final[tuple[Capability, ...]] = (
    Capability("version", "查看 CLI 与协议版本", "L0", "available"),
    Capability("status", "查看桌面应用和本地服务状态", "L0", "available"),
    Capability("doctor", "检查 CLI、Skill、运行文件和本地服务", "L0", "available"),
    Capability("guide", "读取 Agent 使用说明", "L0", "available"),
    Capability("capabilities", "读取当前命令能力和风险信息", "L0", "available"),
    Capability("professors.list", "分页查询或读取全部导师档案", "L0", "available"),
    Capability("professors.get", "按 ID 读取导师完整档案", "L0", "available"),
    Capability("professors.tags.list", "读取导师标签", "L0", "available"),
    Capability(
        "professors.write",
        "新增、修改、归档和标记导师",
        "L1",
        "planned",
        mutates=True,
        unavailable_reason="当前版本先提供完整读取；导师写入仍在接入安全审计。",
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
    Capability("templates.list", "查询邮件模板", "L0", "available"),
    Capability("templates.get", "按 ID 读取完整模板内容", "L0", "available"),
    Capability(
        "templates.write",
        "新增、修改、复制、归档和恢复模板",
        "L1",
        "planned",
        mutates=True,
        unavailable_reason="模板写入仍在接入幂等与审计协议。",
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
        "materials.write",
        "上传、设为默认或删除材料",
        "L1",
        "planned",
        mutates=True,
        guide_topic="materials",
        unavailable_reason="材料写入和删除预览仍在接入。",
    ),
    Capability("identities.list", "查询发件身份的脱敏视图", "L0", "available"),
    Capability("identities.get", "按 ID 读取发件身份的脱敏视图", "L0", "available"),
    Capability(
        "identities.write",
        "管理发件身份和凭据",
        "L1",
        "planned",
        mutates=True,
        unavailable_reason="敏感凭据只能通过专用安全入口设置。",
    ),
    Capability("llm-profiles.list", "查询模型配置的脱敏视图", "L0", "available"),
    Capability("llm-profiles.get", "按 ID 读取模型配置的脱敏视图", "L0", "available"),
    Capability(
        "llm-profiles.write",
        "管理模型配置和凭据",
        "L1",
        "planned",
        mutates=True,
        unavailable_reason="敏感凭据只能通过专用安全入口设置。",
    ),
    Capability(
        "matching",
        "创建和观察匹配分析任务",
        "L1",
        "planned",
        mutates=True,
        external_action=True,
        long_running=True,
        unavailable_reason="将在长任务协议完成后启用。",
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
        "drafts.prepare-send",
        "为一封最终草稿创建 30 分钟有效的一次性发送或排程计划",
        "L3",
        "available",
        mutates=True,
        requires_plan=True,
        guide_topic="sending",
    ),
    Capability(
        "campaigns",
        "创建和控制批量任务",
        "L2",
        "planned",
        mutates=True,
        long_running=True,
        unavailable_reason="将在批量任务安全交付策略完成后启用。",
    ),
    Capability(
        "crawler",
        "创建、观察和审核导师抓取任务",
        "L1",
        "planned",
        mutates=True,
        external_action=True,
        long_running=True,
        unavailable_reason="将在长任务协议完成后启用。",
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
        "在用户明确确认后一次性执行发送或排程计划",
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


def list_capabilities(command: str | None = None) -> list[dict[str, object]]:
    items = CAPABILITIES
    if command:
        normalized = command.strip().lower()
        items = tuple(
            item
            for item in CAPABILITIES
            if item.command == normalized or item.command.startswith(f"{normalized}.")
        )
    return [item.to_dict() for item in items]
