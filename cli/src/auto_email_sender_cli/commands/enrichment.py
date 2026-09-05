from __future__ import annotations

from typing import Annotated

import typer

from auto_email_sender_cli.commands.common import (
    format_detail,
    format_page,
    run_read_command,
    run_write_command,
)


enrichment_app = typer.Typer(
    help="创建和观察导师信息补全任务；任务会访问导师主页并调用已配置的 LLM，但不会发送邮件。",
    no_args_is_help=True,
)
jobs_app = typer.Typer(help="管理批量导师信息补全任务。", no_args_is_help=True)
enrichment_app.add_typer(jobs_app, name="jobs")


@jobs_app.command("list")
def list_professor_information_enrichment_jobs(
    ctx: typer.Context,
    view: Annotated[
        str,
        typer.Option("--view", help="current 或 trash。"),
    ] = "current",
    cursor: Annotated[int, typer.Option("--cursor", min=0)] = 0,
    limit: Annotated[int, typer.Option("--limit", min=1, max=500)] = 25,
    fields: Annotated[
        str | None, typer.Option("--fields", help="只返回需要的字段，逗号分隔。")
    ] = None,
    all_items: Annotated[
        bool, typer.Option("--all", help="自动读取全部分页结果。")
    ] = False,
) -> None:
    run_read_command(
        ctx,
        command="enrichment.jobs.list",
        path="/api/agent/v1/enrichment/jobs",
        params={"view": view, "cursor": cursor, "limit": limit},
        fetch_all=all_items,
        fields=fields,
        human_formatter=lambda data: format_page(
            data,
            columns=(
                ("id", "ID"),
                ("name", "任务"),
                ("status", "状态"),
                ("target_count", "目标数"),
                ("succeeded_count", "成功"),
                ("failed_count", "失败"),
            ),
        ),
    )


@jobs_app.command("create")
def create_professor_information_enrichment_job(
    ctx: typer.Context,
    llm_profile_id: Annotated[int, typer.Option("--llm-profile-id", min=1)],
    professor_ids: Annotated[
        list[int],
        typer.Option("--professor-id", min=1, help="可重复指定需要补全的导师 ID。"),
    ],
    name: Annotated[str | None, typer.Option("--name", help="可选任务名称。")] = None,
) -> None:
    run_write_command(
        ctx,
        command="enrichment.jobs.create",
        path="/api/agent/v1/enrichment/jobs",
        json_body={
            "professor_ids": professor_ids,
            "llm_profile_id": llm_profile_id,
            "name": name,
        },
        human_formatter=format_detail,
    )


@jobs_app.command("get")
def get_professor_information_enrichment_job(
    ctx: typer.Context,
    job_id: Annotated[int, typer.Argument(min=1)],
) -> None:
    run_read_command(
        ctx,
        command="enrichment.jobs.get",
        path=f"/api/agent/v1/enrichment/jobs/{job_id}",
        human_formatter=format_detail,
    )


@jobs_app.command("items")
def list_professor_information_enrichment_job_items(
    ctx: typer.Context,
    job_id: Annotated[int, typer.Argument(min=1)],
    cursor: Annotated[int, typer.Option("--cursor", min=0)] = 0,
    limit: Annotated[int, typer.Option("--limit", min=1, max=500)] = 25,
    fields: Annotated[
        str | None, typer.Option("--fields", help="只返回需要的字段，逗号分隔。")
    ] = None,
    all_items: Annotated[
        bool, typer.Option("--all", help="自动读取全部分页结果。")
    ] = False,
) -> None:
    run_read_command(
        ctx,
        command="enrichment.jobs.items",
        path=f"/api/agent/v1/enrichment/jobs/{job_id}/items",
        params={"cursor": cursor, "limit": limit},
        fetch_all=all_items,
        fields=fields,
        human_formatter=lambda data: format_page(
            data,
            columns=(
                ("id", "项 ID"),
                ("professor_name", "导师"),
                ("status", "状态"),
                ("enriched_fields", "已补全"),
                ("total_tokens", "Token"),
                ("error_message", "错误"),
            ),
        ),
    )


@jobs_app.command("cancel")
def cancel_professor_information_enrichment_job(
    ctx: typer.Context,
    job_id: Annotated[int, typer.Argument(min=1)],
) -> None:
    run_write_command(
        ctx,
        command="enrichment.jobs.cancel",
        path=f"/api/agent/v1/enrichment/jobs/{job_id}/cancel",
        human_formatter=format_detail,
    )


@jobs_app.command("retry-failed")
def retry_failed_professor_information_enrichment_job(
    ctx: typer.Context,
    job_id: Annotated[int, typer.Argument(min=1)],
) -> None:
    run_write_command(
        ctx,
        command="enrichment.jobs.retry-failed",
        path=f"/api/agent/v1/enrichment/jobs/{job_id}/retry-failed",
        human_formatter=format_detail,
    )


@jobs_app.command("delete")
def delete_professor_information_enrichment_job(
    ctx: typer.Context,
    job_id: Annotated[int, typer.Argument(min=1)],
) -> None:
    run_write_command(
        ctx,
        command="enrichment.jobs.delete",
        path=f"/api/agent/v1/enrichment/jobs/{job_id}/delete",
        human_formatter=format_detail,
    )


@jobs_app.command("restore")
def restore_professor_information_enrichment_job(
    ctx: typer.Context,
    job_id: Annotated[int, typer.Argument(min=1)],
) -> None:
    run_write_command(
        ctx,
        command="enrichment.jobs.restore",
        path=f"/api/agent/v1/enrichment/jobs/{job_id}/restore",
        human_formatter=format_detail,
    )
