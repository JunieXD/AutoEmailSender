from __future__ import annotations

from enum import StrEnum
from typing import Annotated

import typer

from auto_email_sender_cli.commands.common import (
    format_detail,
    run_read_command,
    run_write_command,
)
from auto_email_sender_cli.commands.ui_handoffs import run_ui_handoff_command


class DraftGenerationMode(StrEnum):
    TEMPLATE = "template"
    AI_REWRITE = "ai_rewrite"
    MANUAL = "manual"


class PlanDelivery(StrEnum):
    IMMEDIATE = "immediate"
    SCHEDULED = "scheduled"


drafts_app = typer.Typer(
    help="生成、保存和读取 draft_only 草稿，并准备发送计划。",
    no_args_is_help=True,
)


@drafts_app.command("present")
def present_draft(
    ctx: typer.Context,
    task_id: Annotated[int, typer.Argument(min=1)],
) -> None:
    run_ui_handoff_command(
        ctx,
        command="drafts.present",
        path=f"/api/agent/v1/drafts/{task_id}/present",
        use_idempotency_key=True,
    )


@drafts_app.command("get")
def get_draft(
    ctx: typer.Context,
    task_id: Annotated[int, typer.Argument(min=1)],
) -> None:
    run_read_command(
        ctx,
        command="drafts.get",
        path=f"/api/agent/v1/drafts/{task_id}",
        guide_topic="drafts",
        human_formatter=format_detail,
    )


@drafts_app.command("generate")
def generate_draft(
    ctx: typer.Context,
    professor_id: Annotated[int, typer.Option("--professor-id", min=1)],
    identity_id: Annotated[int, typer.Option("--identity-id", min=1)],
    llm_profile_id: Annotated[int, typer.Option("--llm-profile-id", min=1)],
    generation_mode: Annotated[
        DraftGenerationMode,
        typer.Option("--generation-mode", help="template、ai_rewrite 或 manual。"),
    ],
    template_id: Annotated[int | None, typer.Option("--template-id", min=1)] = None,
    reference_material_id: Annotated[
        int | None,
        typer.Option(
            "--reference-material-id",
            min=1,
            help="仅供 AI 参考，不会作为附件发送。",
        ),
    ] = None,
    attachment_material_ids: Annotated[
        list[int] | None,
        typer.Option(
            "--attachment-material-id",
            min=1,
            help="可重复；这些文件会随真实邮件发送，不会自动供 AI 参考。",
        ),
    ] = None,
    subject: Annotated[str | None, typer.Option("--subject")] = None,
    body_text: Annotated[str | None, typer.Option("--body-text")] = None,
    body_html: Annotated[str | None, typer.Option("--body-html")] = None,
) -> None:
    run_write_command(
        ctx,
        command="drafts.generate",
        path="/api/agent/v1/drafts",
        json_body={
            "professor_id": professor_id,
            "identity_id": identity_id,
            "llm_profile_id": llm_profile_id,
            "generation_mode": generation_mode.value,
            "template_id": template_id,
            "reference_material_id": reference_material_id,
            "attachment_material_ids": attachment_material_ids or [],
            "subject": subject,
            "body_text": body_text,
            "body_html": body_html,
        },
        guide_topic="drafts",
        human_formatter=format_detail,
    )


@drafts_app.command("save")
def save_draft(
    ctx: typer.Context,
    task_id: Annotated[int, typer.Argument(min=1)],
    subject: Annotated[str | None, typer.Option("--subject")] = None,
    body_text: Annotated[str, typer.Option("--body-text")] = "",
    body_html: Annotated[str | None, typer.Option("--body-html")] = None,
    attachment_material_ids: Annotated[
        list[int] | None,
        typer.Option("--attachment-material-id", min=1),
    ] = None,
) -> None:
    run_write_command(
        ctx,
        command="drafts.save",
        path=f"/api/agent/v1/drafts/{task_id}",
        method="PUT",
        json_body={
            "subject": subject,
            "body_text": body_text,
            "body_html": body_html,
            "attachment_material_ids": attachment_material_ids or [],
        },
        guide_topic="drafts",
        human_formatter=format_detail,
    )


@drafts_app.command("regenerate")
def regenerate_draft(
    ctx: typer.Context,
    task_id: Annotated[int, typer.Argument(min=1)],
    llm_profile_id: Annotated[int | None, typer.Option("--llm-profile-id", min=1)] = None,
) -> None:
    run_write_command(
        ctx,
        command="drafts.regenerate",
        path=f"/api/agent/v1/drafts/{task_id}/regenerate",
        json_body={"llm_profile_id": llm_profile_id},
        guide_topic="drafts",
        human_formatter=format_detail,
    )


@drafts_app.command("approve")
def approve_draft(
    ctx: typer.Context,
    task_id: Annotated[int, typer.Argument(min=1)],
    body_text: Annotated[
        str,
        typer.Option("--body-text", help="最终纯文本正文；即使为空也必须明确提供。"),
    ],
    subject: Annotated[str | None, typer.Option("--subject")] = None,
    body_html: Annotated[str | None, typer.Option("--body-html")] = None,
    attachment_material_ids: Annotated[
        list[int] | None,
        typer.Option("--attachment-material-id", min=1),
    ] = None,
) -> None:
    run_write_command(
        ctx,
        command="drafts.approve",
        path=f"/api/agent/v1/tasks/{task_id}/approve-draft",
        json_body={
            "subject": subject,
            "body_text": body_text,
            "body_html": body_html,
            "attachment_material_ids": attachment_material_ids or [],
        },
        guide_topic="drafts",
        human_formatter=format_detail,
    )


@drafts_app.command("rewrite")
def rewrite_draft(
    ctx: typer.Context,
    task_id: Annotated[int, typer.Argument(min=1)],
    body_text: Annotated[str, typer.Option("--body-text")] = "",
    subject: Annotated[str | None, typer.Option("--subject")] = None,
    body_html: Annotated[str | None, typer.Option("--body-html")] = None,
    llm_profile_id: Annotated[int | None, typer.Option("--llm-profile-id", min=1)] = None,
    attachment_material_ids: Annotated[
        list[int] | None,
        typer.Option("--attachment-material-id", min=1),
    ] = None,
) -> None:
    run_write_command(
        ctx,
        command="drafts.rewrite",
        path=f"/api/agent/v1/drafts/{task_id}/rewrite",
        json_body={
            "subject": subject,
            "body_text": body_text,
            "body_html": body_html,
            "llm_profile_id": llm_profile_id,
            "attachment_material_ids": attachment_material_ids or [],
        },
        guide_topic="drafts",
        human_formatter=format_detail,
    )


@drafts_app.command("prepare-send")
def prepare_send(
    ctx: typer.Context,
    task_id: Annotated[int, typer.Argument(min=1)],
    delivery: Annotated[
        PlanDelivery,
        typer.Option("--delivery", help="immediate 或 scheduled。"),
    ] = PlanDelivery.IMMEDIATE,
    scheduled_at: Annotated[
        str | None,
        typer.Option("--scheduled-at", help="带时区的 ISO 8601 时间。"),
    ] = None,
) -> None:
    run_write_command(
        ctx,
        command="drafts.prepare-send",
        path=f"/api/agent/v1/drafts/{task_id}/prepare-send",
        json_body={
            "delivery": delivery.value,
            "scheduled_at": scheduled_at,
        },
        guide_topic="sending",
        human_formatter=format_detail,
    )
