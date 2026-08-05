"""Conservative, user-facing effects for agent confirmation plans.

The ``plans.execute`` route is a shared gateway for several independent plan
families.  Keeping the effects next to the plan protocol means a caller can
inspect the exact plan action before confirming it instead of inheriting the
effects of whichever plan type happened to be implemented first.
"""

from __future__ import annotations

from typing import Final

from app.schemas.agent import AgentPlanEffectsRead


_EFFECTS: Final[dict[str, dict[str, object]]] = {
    # Local state changes.
    "template.archive": {
        "mutates": True,
        "external_services": [],
        "cost_may_apply": False,
        "reversible": True,
        "impact_scope": "指定邮件模板及其默认关联",
        "confirmation_rule": "explicit_plan_confirmation",
        "unknown_external_result_protection": False,
    },
    "material.delete": {
        "mutates": True,
        "external_services": [],
        "cost_may_apply": False,
        "reversible": False,
        "impact_scope": "指定材料记录及其本地文件",
        "confirmation_rule": "explicit_plan_confirmation",
        "unknown_external_result_protection": False,
    },
    "professor.tags.bulk": {
        "mutates": True,
        "external_services": [],
        "cost_may_apply": False,
        "reversible": True,
        "impact_scope": "计划中指定的导师标签关系",
        "confirmation_rule": "explicit_plan_confirmation",
        "unknown_external_result_protection": False,
    },
    "professor.archive.bulk": {
        "mutates": True,
        "external_services": [],
        "cost_may_apply": False,
        "reversible": True,
        "impact_scope": "计划中指定的导师档案归档状态",
        "confirmation_rule": "explicit_plan_confirmation",
        "unknown_external_result_protection": False,
    },
    "professor.tag.delete": {
        "mutates": True,
        "external_services": [],
        "cost_may_apply": False,
        "reversible": False,
        "impact_scope": "指定导师标签及其关联关系",
        "confirmation_rule": "explicit_plan_confirmation",
        "unknown_external_result_protection": False,
    },
    "professor.import": {
        "mutates": True,
        "external_services": [],
        "cost_may_apply": False,
        "reversible": True,
        "impact_scope": "导入文件中选定的导师档案和标签",
        "confirmation_rule": "explicit_plan_confirmation",
        "unknown_external_result_protection": False,
    },
    "community_mentor.import": {
        "mutates": True,
        "external_services": [],
        "cost_may_apply": False,
        "reversible": True,
        "impact_scope": "计划中选定的社区导师字段和本地关联",
        "confirmation_rule": "explicit_plan_confirmation",
        "unknown_external_result_protection": False,
    },
    "crawler.candidates.approve": {
        "mutates": True,
        "external_services": [],
        "cost_may_apply": False,
        "reversible": True,
        "impact_scope": "指定抓取任务候选及对应导师档案",
        "confirmation_rule": "explicit_plan_confirmation",
        "unknown_external_result_protection": False,
    },
    "campaign.create": {
        "mutates": True,
        "external_services": [],
        "cost_may_apply": False,
        "reversible": True,
        "impact_scope": "新建的暂停状态批量草稿活动及其草稿项",
        "confirmation_rule": "explicit_plan_confirmation",
        "unknown_external_result_protection": False,
    },
    # Plans that hand work to an external service after confirmation.
    "test_email.send": {
        "mutates": True,
        "external_services": ["smtp"],
        "cost_may_apply": False,
        "reversible": False,
        "impact_scope": "向当前发件身份邮箱发送一封真实测试邮件",
        "confirmation_rule": "explicit_plan_confirmation",
        "unknown_external_result_protection": True,
    },
    "crawler.job.retry": {
        "mutates": True,
        "external_services": ["public_web", "llm"],
        "cost_may_apply": True,
        "reversible": False,
        "impact_scope": "重新排队指定抓取任务及其公开网页、模型调用范围",
        "confirmation_rule": "explicit_plan_confirmation",
        "unknown_external_result_protection": True,
    },
    "campaign.send": {
        "mutates": True,
        "external_services": ["smtp"],
        "cost_may_apply": False,
        "reversible": False,
        "impact_scope": "计划中指定的批量邮件发送或排程项",
        "confirmation_rule": "explicit_plan_confirmation",
        "unknown_external_result_protection": True,
    },
    "campaign.resume": {
        "mutates": True,
        "external_services": ["smtp"],
        "cost_may_apply": False,
        "reversible": False,
        "impact_scope": "恢复指定活动并允许其待发送项继续进入投递流程",
        "confirmation_rule": "explicit_plan_confirmation",
        "unknown_external_result_protection": True,
    },
    "campaign.item_send_restore": {
        "mutates": True,
        "external_services": ["smtp"],
        "cost_may_apply": False,
        "reversible": False,
        "impact_scope": "恢复指定活动项的发送资格",
        "confirmation_rule": "explicit_plan_confirmation",
        "unknown_external_result_protection": True,
    },
    "email.send": {
        "mutates": True,
        "external_services": ["smtp"],
        "cost_may_apply": False,
        "reversible": False,
        "impact_scope": "指定单封邮件的真实发送",
        "confirmation_rule": "explicit_plan_confirmation",
        "unknown_external_result_protection": True,
    },
    "email.schedule": {
        "mutates": True,
        "external_services": ["smtp"],
        "cost_may_apply": False,
        "reversible": False,
        "impact_scope": "指定单封邮件的排程及后续真实投递",
        "confirmation_rule": "explicit_plan_confirmation",
        "unknown_external_result_protection": True,
    },
}


def resolve_agent_plan_effects(action: str) -> AgentPlanEffectsRead:
    """Return the effect contract for a concrete plan action.

    Unknown actions deliberately fail closed: the gateway may still be able
    to read an old plan, but it must never advertise an unknown action as
    harmless or local-only.
    """

    normalized = action.strip().lower()
    values = _EFFECTS.get(normalized)
    if values is None:
        values = {
            "mutates": True,
            "external_services": ["unknown"],
            "cost_may_apply": True,
            "reversible": False,
            "impact_scope": "该计划 action 的完整影响范围（未知 action，必须人工核对）",
            "confirmation_rule": "explicit_plan_confirmation_and_manual_review",
            "unknown_external_result_protection": True,
        }
    return AgentPlanEffectsRead(
        resolution="delegated",
        action=normalized,
        **values,
    )


def known_agent_plan_actions() -> frozenset[str]:
    """Expose the finite action set for cross-layer contract tests."""

    return frozenset(_EFFECTS)
