from __future__ import annotations

import secrets
from typing import Annotated, Any

import typer

from auto_email_sender_cli.client import AgentApiClient
from auto_email_sender_cli.commands.common import cli_context, format_detail, run_read_command
from auto_email_sender_cli.errors import CliError
from auto_email_sender_cli.output import emit_error, emit_success


settings_app = typer.Typer(help="读取和修改不含凭据的运行设置。", no_args_is_help=True)


@settings_app.command("get")
def get_runtime_settings(ctx: typer.Context) -> None:
    run_read_command(
        ctx,
        command="settings.get",
        path="/api/agent/v1/settings",
        guide_topic="settings",
        human_formatter=format_detail,
    )


@settings_app.command("update")
def update_runtime_settings(
    ctx: typer.Context,
    match_analysis_job_worker_count: Annotated[
        int | None,
        typer.Option("--match-analysis-job-worker-count", min=1, max=8),
    ] = None,
    match_analysis_job_item_concurrency: Annotated[
        int | None,
        typer.Option("--match-analysis-job-item-concurrency", min=1, max=20),
    ] = None,
    match_analysis_job_interval_seconds: Annotated[
        int | None,
        typer.Option("--match-analysis-job-interval-seconds", min=1, max=300),
    ] = None,
    crawler_worker_count: Annotated[
        int | None,
        typer.Option("--crawler-worker-count", min=1, max=8),
    ] = None,
    crawler_profile_enrichment_concurrency: Annotated[
        int | None,
        typer.Option("--crawler-profile-enrichment-concurrency", min=1, max=20),
    ] = None,
    crawler_host_concurrency: Annotated[
        int | None,
        typer.Option("--crawler-host-concurrency", min=1, max=10),
    ] = None,
    crawler_agent_max_chunks_per_run: Annotated[
        int | None,
        typer.Option("--crawler-agent-max-chunks-per-run", min=1, max=20),
    ] = None,
    draft_max_tokens: Annotated[
        int | None,
        typer.Option("--draft-max-tokens", min=256, max=32000),
    ] = None,
    batch_draft_generation_concurrency: Annotated[
        int | None,
        typer.Option("--batch-draft-generation-concurrency", min=1, max=20),
    ] = None,
    draft_rewrite_intensity: Annotated[
        str | None,
        typer.Option("--draft-rewrite-intensity", help="light、moderate 或 strong。"),
    ] = None,
    draft_rewrite_tone: Annotated[
        str | None,
        typer.Option("--draft-rewrite-tone", help="polite、professional 或 friendly。"),
    ] = None,
    draft_rewrite_formality: Annotated[
        str | None,
        typer.Option("--draft-rewrite-formality", help="natural、balanced 或 formal。"),
    ] = None,
    draft_rewrite_length: Annotated[
        str | None,
        typer.Option("--draft-rewrite-length", help="shorter、default 或 more_detailed。"),
    ] = None,
    draft_rewrite_specificity: Annotated[
        str | None,
        typer.Option("--draft-rewrite-specificity", help="concise、balanced 或 detailed。"),
    ] = None,
    draft_template_preservation: Annotated[
        str | None,
        typer.Option("--draft-template-preservation", help="structure_first、balanced 或 content_first。"),
    ] = None,
    draft_custom_instruction: Annotated[
        str | None,
        typer.Option("--draft-custom-instruction", help="AI 改写补充要求，最多 2000 字符。"),
    ] = None,
    intended_research_direction: Annotated[
        str | None,
        typer.Option("--intended-research-direction", help="目标研究方向，最多 2000 字符。"),
    ] = None,
) -> None:
    requested_updates = {
        key: value
        for key, value in {
            "match_analysis_job_worker_count": match_analysis_job_worker_count,
            "match_analysis_job_item_concurrency": match_analysis_job_item_concurrency,
            "match_analysis_job_interval_seconds": match_analysis_job_interval_seconds,
            "crawler_worker_count": crawler_worker_count,
            "crawler_profile_enrichment_concurrency": crawler_profile_enrichment_concurrency,
            "crawler_host_concurrency": crawler_host_concurrency,
            "crawler_agent_max_chunks_per_run": crawler_agent_max_chunks_per_run,
            "draft_max_tokens": draft_max_tokens,
            "batch_draft_generation_concurrency": batch_draft_generation_concurrency,
            "draft_rewrite_intensity": draft_rewrite_intensity,
            "draft_rewrite_tone": draft_rewrite_tone,
            "draft_rewrite_formality": draft_rewrite_formality,
            "draft_rewrite_length": draft_rewrite_length,
            "draft_rewrite_specificity": draft_rewrite_specificity,
            "draft_template_preservation": draft_template_preservation,
            "draft_custom_instruction": draft_custom_instruction,
            "intended_research_direction": intended_research_direction,
        }.items()
        if value is not None
    }
    context = cli_context(ctx)
    if not requested_updates:
        error = CliError(
            code="SETTINGS_UPDATE_EMPTY",
            message="请至少指定一个需要修改的设置项。",
            exit_code=2,
        )
        emit_error(context, command="settings.update", error=error, guide_topic="settings")
        raise typer.Exit(error.exit_code)

    try:
        client = AgentApiClient()
        current = client.request("GET", "/api/agent/v1/settings")
        if not isinstance(current, dict):
            raise CliError(
                code="INVALID_API_RESPONSE",
                message="本地服务返回了无法识别的运行设置。",
                exit_code=8,
            )
        payload = _runtime_settings_payload(current)
        payload.update(requested_updates)
        data = client.request(
            "PATCH",
            "/api/agent/v1/settings",
            json_body=payload,
            idempotency_key=f"cli_{secrets.token_urlsafe(24)}",
        )
        emit_success(
            context,
            command="settings.update",
            data=data,
            human_text=format_detail(data),
            guide_topic="settings",
            app_version=client.descriptor.app_version,
        )
    except CliError as error:
        emit_error(context, command="settings.update", error=error, guide_topic="settings")
        raise typer.Exit(error.exit_code) from error


def _runtime_settings_payload(current: dict[str, Any]) -> dict[str, Any]:
    keys = (
        "match_analysis_job_worker_count",
        "match_analysis_job_item_concurrency",
        "match_analysis_job_interval_seconds",
        "crawler_worker_count",
        "crawler_profile_enrichment_concurrency",
        "crawler_host_concurrency",
        "crawler_agent_max_chunks_per_run",
        "draft_max_tokens",
        "batch_draft_generation_concurrency",
        "draft_rewrite_intensity",
        "draft_rewrite_tone",
        "draft_rewrite_formality",
        "draft_rewrite_length",
        "draft_rewrite_specificity",
        "draft_template_preservation",
        "draft_custom_instruction",
        "intended_research_direction",
    )
    missing = [key for key in keys if key not in current]
    if missing:
        raise CliError(
            code="INVALID_API_RESPONSE",
            message="本地服务返回的运行设置缺少必要字段。",
            exit_code=8,
            details={"missing_fields": missing},
        )
    return {key: current[key] for key in keys}
