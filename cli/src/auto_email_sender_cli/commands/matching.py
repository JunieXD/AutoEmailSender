from __future__ import annotations

from typing import Annotated

import typer

from auto_email_sender_cli.commands.common import (
    format_detail,
    format_page,
    run_read_command,
    run_write_command,
)


matching_app = typer.Typer(
    help="创建和观察异步匹配分析任务；任务会调用已配置的 LLM，但不会发送邮件。",
    no_args_is_help=True,
)
jobs_app = typer.Typer(help="管理匹配分析任务。", no_args_is_help=True)
matching_app.add_typer(jobs_app, name="jobs")


@jobs_app.command("list")
def list_match_analysis_jobs(
    ctx: typer.Context,
    identity_id: Annotated[int | None, typer.Option("--identity-id", min=1)] = None,
    llm_profile_id: Annotated[int | None, typer.Option("--llm-profile-id", min=1)] = None,
    view: Annotated[
        str,
        typer.Option("--view", help="current 或 trash。"),
    ] = "current",
    cursor: Annotated[int, typer.Option("--cursor", min=0)] = 0,
    limit: Annotated[int, typer.Option("--limit", min=1, max=500)] = 25,
    fields: Annotated[str | None, typer.Option("--fields", help="只返回需要的字段，逗号分隔。") ] = None,
    all_items: Annotated[bool, typer.Option("--all", help="自动读取全部分页结果。")] = False,
) -> None:
    run_read_command(
        ctx,
        command="matching.jobs.list",
        path="/api/agent/v1/matching/jobs",
        params={
            "identity_id": identity_id,
            "llm_profile_id": llm_profile_id,
            "view": view,
            "cursor": cursor,
            "limit": limit,
        },
        fetch_all=all_items,
        fields=fields,
        guide_topic="matching",
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
def create_match_analysis_job(
    ctx: typer.Context,
    identity_id: Annotated[int, typer.Option("--identity-id", min=1)],
    llm_profile_id: Annotated[int, typer.Option("--llm-profile-id", min=1)],
    professor_ids: Annotated[
        list[int],
        typer.Option("--professor-id", min=1, help="可重复指定需要分析的导师 ID。"),
    ],
    name: Annotated[str | None, typer.Option("--name", help="可选任务名称。")]=None,
) -> None:
    run_write_command(
        ctx,
        command="matching.jobs.create",
        path="/api/agent/v1/matching/jobs",
        json_body={
            "identity_id": identity_id,
            "llm_profile_id": llm_profile_id,
            "professor_ids": professor_ids,
            "name": name,
        },
        guide_topic="matching",
        human_formatter=format_detail,
    )


@jobs_app.command("get")
def get_match_analysis_job(
    ctx: typer.Context,
    job_id: Annotated[int, typer.Argument(min=1)],
) -> None:
    run_read_command(
        ctx,
        command="matching.jobs.get",
        path=f"/api/agent/v1/matching/jobs/{job_id}",
        guide_topic="matching",
        human_formatter=format_detail,
    )


@jobs_app.command("items")
def list_match_analysis_job_items(
    ctx: typer.Context,
    job_id: Annotated[int, typer.Argument(min=1)],
    cursor: Annotated[int, typer.Option("--cursor", min=0)] = 0,
    limit: Annotated[int, typer.Option("--limit", min=1, max=500)] = 25,
    fields: Annotated[str | None, typer.Option("--fields", help="只返回需要的字段，逗号分隔。") ] = None,
    all_items: Annotated[bool, typer.Option("--all", help="自动读取全部分页结果。")] = False,
) -> None:
    run_read_command(
        ctx,
        command="matching.jobs.items",
        path=f"/api/agent/v1/matching/jobs/{job_id}/items",
        params={"cursor": cursor, "limit": limit},
        fetch_all=all_items,
        fields=fields,
        guide_topic="matching",
        human_formatter=lambda data: format_page(
            data,
            columns=(
                ("id", "项 ID"),
                ("professor_name", "导师"),
                ("status", "状态"),
                ("match_score", "匹配分"),
                ("total_tokens", "Token"),
                ("error_message", "错误"),
            ),
        ),
    )


@jobs_app.command("cancel")
def cancel_match_analysis_job(
    ctx: typer.Context,
    job_id: Annotated[int, typer.Argument(min=1)],
) -> None:
    run_write_command(
        ctx,
        command="matching.jobs.cancel",
        path=f"/api/agent/v1/matching/jobs/{job_id}/cancel",
        guide_topic="matching",
        human_formatter=format_detail,
    )


@jobs_app.command("retry-failed")
def retry_failed_match_analysis_job(
    ctx: typer.Context,
    job_id: Annotated[int, typer.Argument(min=1)],
) -> None:
    run_write_command(
        ctx,
        command="matching.jobs.retry-failed",
        path=f"/api/agent/v1/matching/jobs/{job_id}/retry-failed",
        guide_topic="matching",
        human_formatter=format_detail,
    )


@jobs_app.command("delete")
def delete_match_analysis_job(
    ctx: typer.Context,
    job_id: Annotated[int, typer.Argument(min=1)],
) -> None:
    run_write_command(
        ctx,
        command="matching.jobs.delete",
        path=f"/api/agent/v1/matching/jobs/{job_id}/delete",
        guide_topic="matching",
        human_formatter=format_detail,
    )


@jobs_app.command("restore")
def restore_match_analysis_job(
    ctx: typer.Context,
    job_id: Annotated[int, typer.Argument(min=1)],
) -> None:
    run_write_command(
        ctx,
        command="matching.jobs.restore",
        path=f"/api/agent/v1/matching/jobs/{job_id}/restore",
        guide_topic="matching",
        human_formatter=format_detail,
    )
