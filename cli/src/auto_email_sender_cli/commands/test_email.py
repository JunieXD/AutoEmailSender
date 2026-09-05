from __future__ import annotations

from typing import Annotated

import typer

from auto_email_sender_cli.commands.attachment_options import build_attachment_payload
from auto_email_sender_cli.commands.common import (
    format_detail,
    run_read_command,
    run_write_command,
)


test_email_app = typer.Typer(
    help="读取、生成和保存发给自己的测试邮件；发送必须经过确认计划。",
    no_args_is_help=True,
)


@test_email_app.command("status")
def get_test_email_status(
    ctx: typer.Context,
    identity_id: Annotated[int, typer.Option("--identity-id", min=1)],
) -> None:
    run_read_command(
        ctx,
        command="test-email.status",
        path=f"/api/agent/v1/test-email/{identity_id}/status",
        human_formatter=format_detail,
    )


@test_email_app.command("get")
def get_test_email_thread(
    ctx: typer.Context,
    identity_id: Annotated[int, typer.Option("--identity-id", min=1)],
    llm_profile_id: Annotated[int, typer.Option("--llm-profile-id", min=1)],
) -> None:
    run_read_command(
        ctx,
        command="test-email.get",
        path=f"/api/agent/v1/test-email/{identity_id}/{llm_profile_id}",
        human_formatter=format_detail,
    )


@test_email_app.command("generate")
def generate_test_email_draft(
    ctx: typer.Context,
    identity_id: Annotated[int, typer.Option("--identity-id", min=1)],
    llm_profile_id: Annotated[int, typer.Option("--llm-profile-id", min=1)],
    template_id: Annotated[int | None, typer.Option("--template-id", min=1)] = None,
    clear_template: Annotated[bool, typer.Option("--clear-template")] = False,
    subject: Annotated[str | None, typer.Option("--subject")] = None,
    body_text: Annotated[str | None, typer.Option("--body-text")] = None,
    body_html: Annotated[str | None, typer.Option("--body-html")] = None,
) -> None:
    payload = _template_selection_payload(template_id, clear_template)
    if subject is not None:
        payload["subject"] = subject
    if body_text is not None:
        payload["body_text"] = body_text
    if body_html is not None:
        payload["body_html"] = body_html
    run_write_command(
        ctx,
        command="test-email.generate",
        path=f"/api/agent/v1/test-email/{identity_id}/{llm_profile_id}/generate-draft",
        json_body=payload,
        human_formatter=format_detail,
        timeout=360.0,
    )


@test_email_app.command("save")
def save_test_email_draft(
    ctx: typer.Context,
    identity_id: Annotated[int, typer.Option("--identity-id", min=1)],
    llm_profile_id: Annotated[int, typer.Option("--llm-profile-id", min=1)],
    body_text: Annotated[str, typer.Option("--body-text")],
    template_id: Annotated[int | None, typer.Option("--template-id", min=1)] = None,
    clear_template: Annotated[bool, typer.Option("--clear-template")] = False,
    subject: Annotated[str | None, typer.Option("--subject")] = None,
    body_html: Annotated[str | None, typer.Option("--body-html")] = None,
    material_ids: Annotated[
        list[int] | None,
        typer.Option("--material-id", min=1, help="重复指定真实测试邮件附件。"),
    ] = None,
    clear_attachments: Annotated[
        bool,
        typer.Option("--clear-attachments", help="明确移除全部测试邮件附件。"),
    ] = False,
) -> None:
    payload = {
        **_template_selection_payload(template_id, clear_template),
        "subject": subject,
        "body_text": body_text,
        "body_html": body_html,
        **build_attachment_payload(
            material_ids,
            field_name="selected_material_ids",
            material_option="--material-id",
            clear_attachments=clear_attachments,
        ),
    }
    run_write_command(
        ctx,
        command="test-email.save",
        path=f"/api/agent/v1/test-email/{identity_id}/{llm_profile_id}/draft",
        method="PUT",
        json_body=payload,
        human_formatter=format_detail,
    )


@test_email_app.command("prepare-send")
def prepare_test_email_send(
    ctx: typer.Context,
    identity_id: Annotated[int, typer.Option("--identity-id", min=1)],
    llm_profile_id: Annotated[int, typer.Option("--llm-profile-id", min=1)],
    body_text: Annotated[str, typer.Option("--body-text")],
    template_id: Annotated[int | None, typer.Option("--template-id", min=1)] = None,
    clear_template: Annotated[bool, typer.Option("--clear-template")] = False,
    subject: Annotated[str | None, typer.Option("--subject")] = None,
    body_html: Annotated[str | None, typer.Option("--body-html")] = None,
    material_ids: Annotated[
        list[int] | None,
        typer.Option("--material-id", min=1, help="重复指定真实测试邮件附件。"),
    ] = None,
    clear_attachments: Annotated[
        bool,
        typer.Option("--clear-attachments", help="明确移除全部测试邮件附件。"),
    ] = False,
) -> None:
    payload = {
        **_template_selection_payload(template_id, clear_template),
        "subject": subject,
        "body_text": body_text,
        "body_html": body_html,
        **build_attachment_payload(
            material_ids,
            field_name="selected_material_ids",
            material_option="--material-id",
            clear_attachments=clear_attachments,
        ),
    }
    run_write_command(
        ctx,
        command="test-email.prepare-send",
        path=f"/api/agent/v1/test-email/{identity_id}/{llm_profile_id}/prepare-send",
        json_body=payload,
        human_formatter=format_detail,
        timeout=90.0,
    )


def _template_selection_payload(
    template_id: int | None,
    clear_template: bool,
) -> dict[str, object]:
    if template_id is not None and clear_template:
        raise typer.BadParameter(
            "--clear-template 不能和 --template-id 同时使用。",
            param_hint="--clear-template",
        )
    if clear_template:
        return {"outreach_template_id": None}
    if template_id is not None:
        return {"outreach_template_id": template_id}
    return {}
