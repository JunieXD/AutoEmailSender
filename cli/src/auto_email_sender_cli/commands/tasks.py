from __future__ import annotations

from typing import Annotated

import typer

from auto_email_sender_cli.commands.common import format_detail, run_write_command
from auto_email_sender_cli.commands.ui_handoffs import run_ui_handoff_command

tasks_app = typer.Typer(
    help="处理单封邮件任务；真实发送仍必须使用 drafts 和 plans。",
    no_args_is_help=True,
)


@tasks_app.command("present")
def present_task(
    ctx: typer.Context,
    task_id: Annotated[int, typer.Argument(min=1)],
) -> None:
    run_ui_handoff_command(
        ctx,
        command="tasks.present",
        path=f"/api/agent/v1/tasks/{task_id}/present",
        use_idempotency_key=True,
    )


@tasks_app.command("cancel-schedule")
def cancel_task_schedule(
    ctx: typer.Context,
    task_id: Annotated[int, typer.Argument(min=1)],
) -> None:
    run_write_command(
        ctx,
        command="tasks.cancel-schedule",
        path=f"/api/agent/v1/tasks/{task_id}/cancel-schedule",
        human_formatter=format_detail,
    )


@tasks_app.command("continue-manually")
def continue_task_manually(
    ctx: typer.Context,
    task_id: Annotated[int, typer.Argument(min=1)],
) -> None:
    run_write_command(
        ctx,
        command="tasks.continue-manually",
        path=f"/api/agent/v1/tasks/{task_id}/continue-manually",
        human_formatter=format_detail,
    )


@tasks_app.command("start-follow-up")
def start_task_follow_up(
    ctx: typer.Context,
    task_id: Annotated[int, typer.Argument(min=1)],
) -> None:
    run_write_command(
        ctx,
        command="tasks.start-follow-up",
        path=f"/api/agent/v1/tasks/{task_id}/start-follow-up",
        human_formatter=format_detail,
    )


@tasks_app.command("set-primary-material")
def set_task_primary_material(
    ctx: typer.Context,
    task_id: Annotated[int, typer.Argument(min=1)],
    material_id: Annotated[
        int,
        typer.Option("--material-id", min=1, help="该身份下作为 AI 参考的材料 ID。"),
    ],
) -> None:
    run_write_command(
        ctx,
        command="tasks.set-primary-material",
        path=f"/api/agent/v1/tasks/{task_id}/primary-material",
        json_body={"primary_material_id": material_id},
        human_formatter=format_detail,
    )


@tasks_app.command("set-outreach-config")
def set_task_outreach_config(
    ctx: typer.Context,
    task_id: Annotated[int, typer.Argument(min=1)],
    generation_mode: Annotated[
        str,
        typer.Option("--generation-mode", help="llm 或 template。"),
    ],
    template_id: Annotated[int | None, typer.Option("--template-id", min=1)] = None,
    clear_template: Annotated[
        bool,
        typer.Option("--clear-template", help="解除本次任务与模板的关联。"),
    ] = False,
    subject: Annotated[str | None, typer.Option("--subject")] = None,
    clear_subject: Annotated[bool, typer.Option("--clear-subject")] = False,
    body_text: Annotated[str | None, typer.Option("--body-text")] = None,
    clear_body_text: Annotated[bool, typer.Option("--clear-body-text")] = False,
    body_html: Annotated[str | None, typer.Option("--body-html")] = None,
    clear_body_html: Annotated[bool, typer.Option("--clear-body-html")] = False,
) -> None:
    if template_id is not None and clear_template:
        raise typer.BadParameter(
            "--clear-template 不能和 --template-id 同时使用。",
            param_hint="--clear-template",
        )
    fields = {
        "outreach_template_subject": (subject, clear_subject, "--clear-subject"),
        "outreach_template_body_text": (
            body_text,
            clear_body_text,
            "--clear-body-text",
        ),
        "outreach_template_body_html": (
            body_html,
            clear_body_html,
            "--clear-body-html",
        ),
    }
    payload: dict[str, object] = {"outreach_generation_mode": generation_mode}
    if template_id is not None:
        payload["outreach_template_id"] = template_id
    elif clear_template:
        payload["outreach_template_id"] = None
    for key, (value, should_clear, clear_option) in fields.items():
        if value is not None and should_clear:
            raise typer.BadParameter(
                f"{clear_option} 不能和对应正文选项同时使用。",
                param_hint=clear_option,
            )
        if value is not None:
            payload[key] = value
        elif should_clear:
            payload[key] = None
    run_write_command(
        ctx,
        command="tasks.set-outreach-config",
        path=f"/api/agent/v1/tasks/{task_id}/outreach-config",
        json_body=payload,
        human_formatter=format_detail,
    )


@tasks_app.command("calculate-match")
def calculate_task_match(
    ctx: typer.Context,
    task_id: Annotated[int, typer.Argument(min=1)],
    llm_profile_id: Annotated[
        int | None, typer.Option("--llm-profile-id", min=1)
    ] = None,
) -> None:
    run_write_command(
        ctx,
        command="tasks.calculate-match",
        path=f"/api/agent/v1/tasks/{task_id}/calculate-match",
        json_body={"llm_profile_id": llm_profile_id},
        human_formatter=format_detail,
        timeout=360.0,
    )
