from __future__ import annotations

from enum import StrEnum
from typing import Annotated

import typer

from auto_email_sender_cli.commands.common import (
    format_detail,
    format_page,
    run_read_command,
    run_write_command,
)


class CampaignGenerationMode(StrEnum):
    TEMPLATE = "template"
    AI_REWRITE = "ai_rewrite"


class CampaignScheduleType(StrEnum):
    IMMEDIATE = "immediate"
    SCHEDULED = "scheduled"


campaigns_app = typer.Typer(
    help="创建暂停的批量草稿活动，审核草稿后再准备一次性批量发送计划。",
    no_args_is_help=True,
)


@campaigns_app.command("list")
def list_campaigns(
    ctx: typer.Context,
    view: Annotated[str, typer.Option("--view", help="current 或 trash。")] = "current",
    identity_id: Annotated[int | None, typer.Option("--identity-id", min=1)] = None,
    cursor: Annotated[int, typer.Option("--cursor", min=0)] = 0,
    limit: Annotated[int, typer.Option("--limit", min=1, max=500)] = 25,
    fields: Annotated[str | None, typer.Option("--fields", help="只返回需要的字段，逗号分隔。") ] = None,
    all_items: Annotated[bool, typer.Option("--all", help="自动读取全部分页结果。")] = False,
) -> None:
    run_read_command(
        ctx,
        command="campaigns.list",
        path="/api/agent/v1/campaigns",
        params={
            "view": view,
            "identity_id": identity_id,
            "cursor": cursor,
            "limit": limit,
        },
        fetch_all=all_items,
        fields=fields,
        guide_topic="campaigns",
        human_formatter=lambda data: format_page(
            data,
            columns=(
                ("id", "ID"),
                ("name", "活动"),
                ("status", "状态"),
                ("target_count", "收件人"),
                ("review_required_count", "待审核"),
                ("sent_count", "已发送"),
                ("canceled_send_count", "已取消定时发送"),
            ),
        ),
    )


@campaigns_app.command("get")
def get_campaign(
    ctx: typer.Context,
    campaign_id: Annotated[int, typer.Argument(min=1)],
) -> None:
    run_read_command(
        ctx,
        command="campaigns.get",
        path=f"/api/agent/v1/campaigns/{campaign_id}",
        guide_topic="campaigns",
        human_formatter=format_detail,
    )


@campaigns_app.command("resend-context")
def get_campaign_resend_context(
    ctx: typer.Context,
    campaign_id: Annotated[int, typer.Argument(min=1)],
) -> None:
    run_read_command(
        ctx,
        command="campaigns.resend-context",
        path=f"/api/agent/v1/campaigns/{campaign_id}/resend-context",
        guide_topic="campaigns",
        human_formatter=format_detail,
    )


@campaigns_app.command("items")
def list_campaign_items(
    ctx: typer.Context,
    campaign_id: Annotated[int, typer.Argument(min=1)],
    cursor: Annotated[int, typer.Option("--cursor", min=0)] = 0,
    limit: Annotated[int, typer.Option("--limit", min=1, max=500)] = 25,
    fields: Annotated[str | None, typer.Option("--fields", help="只返回需要的字段，逗号分隔。") ] = None,
    all_items: Annotated[bool, typer.Option("--all", help="自动读取全部分页结果。")] = False,
) -> None:
    run_read_command(
        ctx,
        command="campaigns.items",
        path=f"/api/agent/v1/campaigns/{campaign_id}/items",
        params={"cursor": cursor, "limit": limit},
        fetch_all=all_items,
        fields=fields,
        guide_topic="campaigns",
        human_formatter=lambda data: format_page(
            data,
            columns=(
                ("id", "活动项 ID"),
                ("professor_name", "导师"),
                ("professor_email", "邮箱"),
                ("status", "状态"),
                ("subject", "主题"),
            ),
        ),
    )


@campaigns_app.command("create")
def prepare_campaign_create(
    ctx: typer.Context,
    name: Annotated[str, typer.Option("--name", help="活动名称。")],
    identity_id: Annotated[int, typer.Option("--identity-id", min=1)],
    llm_profile_id: Annotated[int, typer.Option("--llm-profile-id", min=1)],
    professor_ids: Annotated[
        list[int],
        typer.Option("--professor-id", min=1, help="可重复指定活动中的导师 ID。"),
    ] = [],
    generation_mode: Annotated[
        CampaignGenerationMode,
        typer.Option("--generation-mode", help="template 或 ai_rewrite。"),
    ] = CampaignGenerationMode.TEMPLATE,
    template_id: Annotated[int | None, typer.Option("--template-id", min=1)] = None,
    reference_material_id: Annotated[
        int | None,
        typer.Option(
            "--reference-material-id",
            min=1,
            help="仅供 AI 改写参考，不会作为附件发送。",
        ),
    ] = None,
    attachment_material_ids: Annotated[
        list[int] | None,
        typer.Option(
            "--attachment-material-id",
            min=1,
            help="可重复；这些文件会在真正发送时随信发送。",
        ),
    ] = None,
    subject: Annotated[str | None, typer.Option("--subject")] = None,
    body_text: Annotated[str | None, typer.Option("--body-text")] = None,
    body_html: Annotated[str | None, typer.Option("--body-html")] = None,
    schedule_type: Annotated[
        CampaignScheduleType,
        typer.Option("--schedule-type", help="immediate 或 scheduled。"),
    ] = CampaignScheduleType.IMMEDIATE,
    window_start_time: Annotated[str | None, typer.Option("--window-start-time")] = None,
    window_end_time: Annotated[str | None, typer.Option("--window-end-time")] = None,
    emails_per_window: Annotated[
        int | None,
        typer.Option("--emails-per-window", min=1),
    ] = None,
    scheduled_dates: Annotated[
        list[str] | None,
        typer.Option("--scheduled-date", help="可重复，格式 YYYY-MM-DD。"),
    ] = None,
) -> None:
    if generation_mode == CampaignGenerationMode.AI_REWRITE and reference_material_id is None:
        raise typer.BadParameter(
            "--generation-mode ai_rewrite 必须指定 --reference-material-id。",
            param_hint="--reference-material-id",
        )
    if schedule_type == CampaignScheduleType.SCHEDULED:
        if not scheduled_dates or not window_start_time or not window_end_time or emails_per_window is None:
            raise typer.BadParameter(
                "定时活动必须指定 --scheduled-date、--window-start-time、--window-end-time 和 --emails-per-window。",
                param_hint="--schedule-type",
            )
    run_write_command(
        ctx,
        command="campaigns.create",
        path="/api/agent/v1/campaigns/prepare-create",
        json_body={
            "name": name,
            "identity_id": identity_id,
            "llm_profile_id": llm_profile_id,
            "professor_ids": professor_ids,
            "generation_mode": generation_mode.value,
            "template_id": template_id,
            "reference_material_id": reference_material_id,
            "attachment_material_ids": attachment_material_ids or [],
            "subject": subject,
            "body_text": body_text,
            "body_html": body_html,
            "schedule_type": schedule_type.value,
            "window_start_time": window_start_time,
            "window_end_time": window_end_time,
            "emails_per_window": emails_per_window,
            "scheduled_dates": scheduled_dates or [],
        },
        guide_topic="campaigns",
        human_formatter=format_detail,
    )


@campaigns_app.command("start-drafts")
def start_campaign_drafts(
    ctx: typer.Context,
    campaign_id: Annotated[int, typer.Argument(min=1)],
) -> None:
    run_write_command(
        ctx,
        command="campaigns.start-drafts",
        path=f"/api/agent/v1/campaigns/{campaign_id}/start-drafts",
        guide_topic="campaigns",
        human_formatter=format_detail,
    )


@campaigns_app.command("pause")
def pause_campaign(
    ctx: typer.Context,
    campaign_id: Annotated[int, typer.Argument(min=1)],
) -> None:
    run_write_command(
        ctx,
        command="campaigns.pause",
        path=f"/api/agent/v1/campaigns/{campaign_id}/pause",
        guide_topic="campaigns",
        human_formatter=format_detail,
    )


@campaigns_app.command("stop")
def stop_campaign(
    ctx: typer.Context,
    campaign_id: Annotated[int, typer.Argument(min=1)],
) -> None:
    run_write_command(
        ctx,
        command="campaigns.stop",
        path=f"/api/agent/v1/campaigns/{campaign_id}/stop",
        guide_topic="campaigns",
        human_formatter=format_detail,
    )


@campaigns_app.command("archive")
def archive_campaign(
    ctx: typer.Context,
    campaign_id: Annotated[int, typer.Argument(min=1)],
) -> None:
    run_write_command(
        ctx,
        command="campaigns.archive",
        path=f"/api/agent/v1/campaigns/{campaign_id}/archive",
        guide_topic="campaigns",
        human_formatter=format_detail,
    )


@campaigns_app.command("restore")
def restore_campaign(
    ctx: typer.Context,
    campaign_id: Annotated[int, typer.Argument(min=1)],
) -> None:
    run_write_command(
        ctx,
        command="campaigns.restore",
        path=f"/api/agent/v1/campaigns/{campaign_id}/restore",
        guide_topic="campaigns",
        human_formatter=format_detail,
    )


@campaigns_app.command("remove-item")
def remove_campaign_item(
    ctx: typer.Context,
    campaign_id: Annotated[int, typer.Argument(min=1)],
    item_id: Annotated[int, typer.Argument(min=1)],
) -> None:
    run_write_command(
        ctx,
        command="campaigns.remove-item",
        path=f"/api/agent/v1/campaigns/{campaign_id}/items/{item_id}/remove",
        guide_topic="campaigns",
        human_formatter=format_detail,
    )


@campaigns_app.command("cancel-item-send")
def cancel_campaign_item_send(
    ctx: typer.Context,
    campaign_id: Annotated[int, typer.Argument(min=1)],
    item_id: Annotated[int, typer.Argument(min=1)],
) -> None:
    run_write_command(
        ctx,
        command="campaigns.cancel-item-send",
        path=f"/api/agent/v1/campaigns/{campaign_id}/items/{item_id}/cancel-send",
        guide_topic="campaigns",
        human_formatter=format_detail,
    )


@campaigns_app.command("prepare-restore-item-send")
def prepare_campaign_item_send_restore(
    ctx: typer.Context,
    campaign_id: Annotated[int, typer.Argument(min=1)],
    item_id: Annotated[int, typer.Argument(min=1)],
) -> None:
    run_write_command(
        ctx,
        command="campaigns.prepare-restore-item-send",
        path=(
            f"/api/agent/v1/campaigns/{campaign_id}/items/{item_id}/prepare-restore-send"
        ),
        guide_topic="campaigns",
        human_formatter=format_detail,
    )


@campaigns_app.command("retry-item-draft")
def retry_campaign_item_draft(
    ctx: typer.Context,
    campaign_id: Annotated[int, typer.Argument(min=1)],
    item_id: Annotated[int, typer.Argument(min=1)],
) -> None:
    run_write_command(
        ctx,
        command="campaigns.retry-item-draft",
        path=f"/api/agent/v1/campaigns/{campaign_id}/items/{item_id}/retry-draft",
        guide_topic="campaigns",
        human_formatter=format_detail,
    )


@campaigns_app.command("prepare-resume")
def prepare_campaign_resume(
    ctx: typer.Context,
    campaign_id: Annotated[int, typer.Argument(min=1)],
) -> None:
    run_write_command(
        ctx,
        command="campaigns.prepare-resume",
        path=f"/api/agent/v1/campaigns/{campaign_id}/prepare-resume",
        guide_topic="campaigns",
        human_formatter=format_detail,
    )


@campaigns_app.command("prepare-send")
def prepare_campaign_send(
    ctx: typer.Context,
    campaign_id: Annotated[int, typer.Argument(min=1)],
    item_ids: Annotated[
        list[int],
        typer.Option(
            "--item-id",
            min=1,
            help="可重复指定要发送的待审核活动项 ID；该命令只生成发送计划。",
        ),
    ] = [],
) -> None:
    run_write_command(
        ctx,
        command="campaigns.prepare-send",
        path=f"/api/agent/v1/campaigns/{campaign_id}/prepare-send",
        json_body={"item_ids": item_ids},
        guide_topic="sending",
        human_formatter=format_detail,
    )
