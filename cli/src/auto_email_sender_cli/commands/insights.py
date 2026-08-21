from __future__ import annotations

from typing import Annotated

import typer

from auto_email_sender_cli.commands.common import format_detail, run_read_command


dashboard_app = typer.Typer(help="读取导师与邮件工作概览。", no_args_is_help=True)
usage_app = typer.Typer(help="读取 LLM Token 用量记录和汇总。", no_args_is_help=True)


@dashboard_app.command("overview")
def read_dashboard_overview(
    ctx: typer.Context,
    identity_id: Annotated[int, typer.Option("--identity-id", min=1)],
    llm_profile_id: Annotated[
        int | None, typer.Option("--llm-profile-id", min=1)
    ] = None,
    university: Annotated[str | None, typer.Option("--university")] = None,
    school: Annotated[str | None, typer.Option("--school")] = None,
    email_university: Annotated[str | None, typer.Option("--email-university")] = None,
    email_school: Annotated[str | None, typer.Option("--email-school")] = None,
    start_date: Annotated[
        str | None, typer.Option("--start-date", help="YYYY-MM-DD。")
    ] = None,
    end_date: Annotated[
        str | None, typer.Option("--end-date", help="YYYY-MM-DD。")
    ] = None,
) -> None:
    run_read_command(
        ctx,
        command="dashboard.overview",
        path="/api/agent/v1/dashboard/overview",
        params={
            "identity_id": identity_id,
            "llm_profile_id": llm_profile_id,
            "university": university,
            "school": school,
            "email_university": email_university,
            "email_school": email_school,
            "start_date": start_date,
            "end_date": end_date,
        },
        guide_topic="insights",
        human_formatter=format_detail,
    )


@usage_app.command("records")
def list_token_usage_records(
    ctx: typer.Context,
    page: Annotated[int, typer.Option("--page", min=1)] = 1,
    page_size: Annotated[int, typer.Option("--page-size", min=1, max=100)] = 25,
    feature_type: Annotated[
        str,
        typer.Option(
            "--feature-type",
            help="all、crawl、information_enrichment、match_analysis 或 draft_generation。",
        ),
    ] = "all",
    model_name: Annotated[str | None, typer.Option("--model-name")] = None,
    start_at: Annotated[
        str | None, typer.Option("--start-at", help="带时区的 ISO 8601 时间。")
    ] = None,
    end_at: Annotated[
        str | None, typer.Option("--end-at", help="带时区的 ISO 8601 时间。")
    ] = None,
    fields: Annotated[
        str | None, typer.Option("--fields", help="只返回需要的字段，逗号分隔。")
    ] = None,
    all_items: Annotated[
        bool, typer.Option("--all", help="读取全部分页记录。")
    ] = False,
) -> None:
    run_read_command(
        ctx,
        command="usage.records",
        path="/api/agent/v1/usage/records",
        params={
            "page": page,
            "page_size": page_size,
            "feature_type": feature_type,
            "model_name": model_name,
            "start_at": start_at,
            "end_at": end_at,
        },
        guide_topic="insights",
        fetch_all=all_items,
        fields=fields,
        human_formatter=format_detail,
    )


@usage_app.command("chart")
def read_token_usage_chart(
    ctx: typer.Context,
    preset: Annotated[
        str,
        typer.Option(
            "--preset",
            help="last_6_hours、last_24_hours、last_7_days、last_30_days 或 custom。",
        ),
    ] = "last_24_hours",
    feature_type: Annotated[
        str,
        typer.Option(
            "--feature-type",
            help="all、crawl、information_enrichment、match_analysis 或 draft_generation。",
        ),
    ] = "all",
    model_name: Annotated[str | None, typer.Option("--model-name")] = None,
    start_at: Annotated[
        str | None,
        typer.Option("--start-at", help="custom 时使用带时区的 ISO 8601 时间。"),
    ] = None,
    end_at: Annotated[
        str | None,
        typer.Option("--end-at", help="custom 时使用带时区的 ISO 8601 时间。"),
    ] = None,
) -> None:
    run_read_command(
        ctx,
        command="usage.chart",
        path="/api/agent/v1/usage/chart",
        params={
            "preset": preset,
            "feature_type": feature_type,
            "model_name": model_name,
            "start_at": start_at,
            "end_at": end_at,
        },
        guide_topic="insights",
        human_formatter=format_detail,
    )


@usage_app.command("visualization")
def read_token_usage_visualization(
    ctx: typer.Context,
    preset: Annotated[
        str,
        typer.Option(
            "--preset",
            help="last_6_hours、last_24_hours、last_7_days、last_30_days 或 custom。",
        ),
    ] = "last_24_hours",
    start_at: Annotated[
        str | None,
        typer.Option("--start-at", help="custom 时使用带时区的 ISO 8601 时间。"),
    ] = None,
    end_at: Annotated[
        str | None,
        typer.Option("--end-at", help="custom 时使用带时区的 ISO 8601 时间。"),
    ] = None,
) -> None:
    run_read_command(
        ctx,
        command="usage.visualization",
        path="/api/agent/v1/usage/visualization",
        params={
            "preset": preset,
            "start_at": start_at,
            "end_at": end_at,
        },
        guide_topic="insights",
        human_formatter=format_detail,
    )
