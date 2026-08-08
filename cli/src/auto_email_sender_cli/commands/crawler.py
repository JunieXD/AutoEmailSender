from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated

import typer

from auto_email_sender_cli.commands.common import (
    format_detail,
    format_page,
    run_read_command,
    run_write_command,
)


crawler_app = typer.Typer(
    help="创建、查看和管理导师抓取任务；开始或继续任务会访问网页并调用已配置的 LLM，但不会发送邮件。",
    no_args_is_help=True,
)
jobs_app = typer.Typer(help="管理导师抓取任务。", no_args_is_help=True)
candidates_app = typer.Typer(help="审核和修正抓取到的候选导师。", no_args_is_help=True)
crawler_app.add_typer(jobs_app, name="jobs")
crawler_app.add_typer(candidates_app, name="candidates")


@jobs_app.command("list")
def list_faculty_crawl_jobs(
    ctx: typer.Context,
    view: Annotated[str, typer.Option("--view", help="current 或 trash。")] = "current",
    cursor: Annotated[int, typer.Option("--cursor", min=0)] = 0,
    limit: Annotated[int, typer.Option("--limit", min=1, max=500)] = 25,
    fields: Annotated[str | None, typer.Option("--fields", help="只返回需要的字段，逗号分隔。") ] = None,
    all_items: Annotated[bool, typer.Option("--all", help="自动读取全部分页结果。")] = False,
) -> None:
    run_read_command(
        ctx,
        command="crawler.jobs.list",
        path="/api/agent/v1/crawler/jobs",
        params={"view": view, "cursor": cursor, "limit": limit},
        fetch_all=all_items,
        fields=fields,
        guide_topic="crawler",
        human_formatter=lambda data: format_page(
            data,
            columns=(
                ("id", "ID"),
                ("university", "学校"),
                ("school", "学院"),
                ("status", "状态"),
                ("candidate_count", "候选"),
            ),
        ),
    )


@jobs_app.command("create")
def create_faculty_crawl_job(
    ctx: typer.Context,
    university: Annotated[str, typer.Option("--university", help="学校名称。")],
    school: Annotated[str, typer.Option("--school", help="学院、系所或实验室名称。")],
    start_url: Annotated[str, typer.Option("--start-url", help="首个公开教师目录或个人主页 URL。")],
    additional_start_urls: Annotated[
        list[str],
        typer.Option("--additional-start-url", help="可重复指定的其他公开入口 URL。"),
    ] = [],
    entry_type: Annotated[str, typer.Option("--entry-type", help="list 或 profile。")] = "list",
    llm_profile_id: Annotated[int | None, typer.Option("--llm-profile-id", min=1)] = None,
) -> None:
    run_write_command(
        ctx,
        command="crawler.jobs.create",
        path="/api/agent/v1/crawler/jobs",
        json_body={
            "university": university,
            "school": school,
            "start_url": start_url,
            "start_urls": [start_url, *additional_start_urls],
            "entry_type": entry_type,
            "llm_profile_id": llm_profile_id,
        },
        guide_topic="crawler",
        human_formatter=format_detail,
    )


@jobs_app.command("create-many")
def create_many_faculty_crawl_jobs(
    ctx: typer.Context,
    items_file: Annotated[
        Path,
        typer.Option(
            "--items-file",
            exists=True,
            dir_okay=False,
            readable=True,
            help="包含抓取任务对象数组或 {items: [...]} 的 UTF-8 JSON 文件。",
        ),
    ],
) -> None:
    run_write_command(
        ctx,
        command="crawler.jobs.create-many",
        path="/api/agent/v1/crawler/jobs/create-many",
        json_body={"items": _read_batch_items(items_file)},
        guide_topic="crawler",
        human_formatter=format_detail,
    )


@jobs_app.command("get")
def get_faculty_crawl_job(
    ctx: typer.Context,
    job_id: Annotated[int, typer.Argument(min=1)],
) -> None:
    run_read_command(
        ctx,
        command="crawler.jobs.get",
        path=f"/api/agent/v1/crawler/jobs/{job_id}",
        guide_topic="crawler",
        human_formatter=format_detail,
    )


@jobs_app.command("pages")
def list_faculty_crawl_pages(
    ctx: typer.Context,
    job_id: Annotated[int, typer.Argument(min=1)],
    cursor: Annotated[int, typer.Option("--cursor", min=0)] = 0,
    limit: Annotated[int, typer.Option("--limit", min=1, max=500)] = 25,
    fields: Annotated[str | None, typer.Option("--fields", help="只返回需要的字段，逗号分隔。") ] = None,
    all_items: Annotated[bool, typer.Option("--all", help="自动读取全部分页结果。")] = False,
) -> None:
    run_read_command(
        ctx,
        command="crawler.jobs.pages",
        path=f"/api/agent/v1/crawler/jobs/{job_id}/pages",
        params={"cursor": cursor, "limit": limit},
        fetch_all=all_items,
        fields=fields,
        guide_topic="crawler",
        human_formatter=lambda data: format_page(
            data,
            columns=(
                ("id", "ID"),
                ("url", "URL"),
                ("page_type", "类型"),
                ("status", "状态"),
                ("title", "标题"),
            ),
        ),
    )


@jobs_app.command("events")
def list_faculty_crawl_job_events(
    ctx: typer.Context,
    job_id: Annotated[int, typer.Argument(min=1)],
    cursor: Annotated[int, typer.Option("--cursor", min=0)] = 0,
    limit: Annotated[int, typer.Option("--limit", min=1, max=500)] = 25,
    fields: Annotated[str | None, typer.Option("--fields", help="只返回需要的字段，逗号分隔。") ] = None,
    all_items: Annotated[bool, typer.Option("--all", help="自动读取全部分页结果。")] = False,
) -> None:
    run_read_command(
        ctx,
        command="crawler.jobs.events",
        path=f"/api/agent/v1/crawler/jobs/{job_id}/events",
        params={"cursor": cursor, "limit": limit},
        fetch_all=all_items,
        fields=fields,
        guide_topic="crawler",
        human_formatter=lambda data: format_page(
            data,
            columns=(
                ("created_at", "时间"),
                ("event_type", "类型"),
                ("message", "事件"),
            ),
        ),
    )


@jobs_app.command("candidates")
def list_faculty_crawl_candidates(
    ctx: typer.Context,
    job_id: Annotated[int, typer.Argument(min=1)],
    cursor: Annotated[int, typer.Option("--cursor", min=0)] = 0,
    limit: Annotated[int, typer.Option("--limit", min=1, max=500)] = 25,
    fields: Annotated[str | None, typer.Option("--fields", help="只返回需要的字段，逗号分隔。") ] = None,
    all_items: Annotated[bool, typer.Option("--all", help="自动读取全部分页结果。")] = False,
) -> None:
    run_read_command(
        ctx,
        command="crawler.jobs.candidates",
        path=f"/api/agent/v1/crawler/jobs/{job_id}/candidates",
        params={"cursor": cursor, "limit": limit},
        fetch_all=all_items,
        fields=fields,
        guide_topic="crawler",
        human_formatter=lambda data: format_page(
            data,
            columns=(
                ("id", "ID"),
                ("name", "姓名"),
                ("email", "邮箱"),
                ("title", "职称"),
                ("review_status", "审核状态"),
                ("confidence", "置信度"),
            ),
        ),
    )


@candidates_app.command("update")
def update_faculty_crawl_candidate(
    ctx: typer.Context,
    candidate_id: Annotated[int, typer.Argument(min=1)],
    name: Annotated[str | None, typer.Option("--name")] = None,
    email: Annotated[str | None, typer.Option("--email")] = None,
    clear_email: Annotated[bool, typer.Option("--clear-email")] = False,
    title: Annotated[str | None, typer.Option("--title")] = None,
    clear_title: Annotated[bool, typer.Option("--clear-title")] = False,
    university: Annotated[str | None, typer.Option("--university")] = None,
    clear_university: Annotated[bool, typer.Option("--clear-university")] = False,
    school: Annotated[str | None, typer.Option("--school")] = None,
    clear_school: Annotated[bool, typer.Option("--clear-school")] = False,
    department: Annotated[str | None, typer.Option("--department")] = None,
    clear_department: Annotated[bool, typer.Option("--clear-department")] = False,
    research_direction: Annotated[str | None, typer.Option("--research-direction")] = None,
    clear_research_direction: Annotated[bool, typer.Option("--clear-research-direction")] = False,
    recent_papers: Annotated[list[str] | None, typer.Option("--recent-paper")] = None,
    clear_recent_papers: Annotated[bool, typer.Option("--clear-recent-papers")] = False,
    profile_url: Annotated[str | None, typer.Option("--profile-url")] = None,
    clear_profile_url: Annotated[bool, typer.Option("--clear-profile-url")] = False,
    source_url: Annotated[str | None, typer.Option("--source-url")] = None,
    clear_source_url: Annotated[bool, typer.Option("--clear-source-url")] = False,
    review_status: Annotated[
        str | None,
        typer.Option("--review-status", help="pending、accepted、rejected 或 merged。"),
    ] = None,
) -> None:
    values = {
        "name": name,
        "email": email,
        "title": title,
        "university": university,
        "school": school,
        "department": department,
        "research_direction": research_direction,
        "recent_papers": recent_papers,
        "profile_url": profile_url,
        "source_url": source_url,
        "review_status": review_status,
    }
    clear_options = {
        "email": clear_email,
        "title": clear_title,
        "university": clear_university,
        "school": clear_school,
        "department": clear_department,
        "research_direction": clear_research_direction,
        "recent_papers": clear_recent_papers,
        "profile_url": clear_profile_url,
        "source_url": clear_source_url,
    }
    payload = {key: value for key, value in values.items() if value is not None}
    for field, should_clear in clear_options.items():
        if not should_clear:
            continue
        if values[field] is not None:
            raise typer.BadParameter(
                f"--clear-{field.replace('_', '-')} 不能和对应的修改选项同时使用。",
                param_hint=f"--clear-{field.replace('_', '-')}",
            )
        payload[field] = None
    if not payload:
        raise typer.BadParameter("请至少提供一个需要修改的字段。")
    run_write_command(
        ctx,
        command="crawler.candidates.update",
        path=f"/api/agent/v1/crawler/candidates/{candidate_id}",
        method="PATCH",
        json_body=payload,
        guide_topic="crawler",
        human_formatter=format_detail,
    )


@jobs_app.command("approve")
def prepare_faculty_crawl_candidate_approval(
    ctx: typer.Context,
    job_id: Annotated[int, typer.Argument(min=1)],
    candidate_ids: Annotated[
        list[int],
        typer.Option(
            "--candidate-id",
            min=1,
            help="可重复指定要导入导师库的候选 ID；该命令只生成预览。",
        ),
    ],
) -> None:
    run_write_command(
        ctx,
        command="crawler.jobs.approve",
        path=f"/api/agent/v1/crawler/jobs/{job_id}/prepare-approve",
        json_body={"candidate_ids": candidate_ids},
        guide_topic="crawler",
        human_formatter=format_detail,
    )


@jobs_app.command("retry")
def prepare_faculty_crawl_job_retry(
    ctx: typer.Context,
    job_id: Annotated[int, typer.Argument(min=1)],
    keep_existing_data: Annotated[
        bool,
        typer.Option(
            "--keep-existing-data",
            help="保留已抓取的页面和候选；默认会在重试前清空它们。",
        ),
    ] = False,
    llm_profile_id: Annotated[int | None, typer.Option("--llm-profile-id", min=1)] = None,
) -> None:
    run_write_command(
        ctx,
        command="crawler.jobs.retry",
        path=f"/api/agent/v1/crawler/jobs/{job_id}/prepare-retry",
        json_body={
            "clear_existing_data": not keep_existing_data,
            "llm_profile_id": llm_profile_id,
        },
        guide_topic="crawler",
        human_formatter=format_detail,
    )


@jobs_app.command("enrich")
def enrich_faculty_crawl_candidates(
    ctx: typer.Context,
    job_id: Annotated[int, typer.Argument(min=1)],
    selection_mode: Annotated[
        str,
        typer.Option(
            "--selection",
            help="候选选择方式：ids、all 或 filter。",
        ),
    ],
    candidate_ids: Annotated[
        list[int],
        typer.Option("--candidate-id", min=1, help="可重复指定需要补全资料的候选 ID。"),
    ] = [],
    review_statuses: Annotated[
        list[str],
        typer.Option(
            "--review-status",
            help="filter 模式下可重复指定 pending、accepted、rejected 或 merged。",
        ),
    ] = [],
    exclude_candidate_ids: Annotated[
        list[int],
        typer.Option("--exclude-candidate-id", min=1, help="从选择结果中排除候选 ID。"),
    ] = [],
    llm_profile_id: Annotated[int | None, typer.Option("--llm-profile-id", min=1)] = None,
) -> None:
    normalized_mode = selection_mode.strip().lower()
    if normalized_mode not in {"ids", "all", "filter"}:
        raise typer.BadParameter(
            "--selection 必须是 ids、all 或 filter。",
            param_hint="--selection",
        )
    if normalized_mode == "ids" and not candidate_ids:
        raise typer.BadParameter(
            "--selection ids 必须至少提供一个 --candidate-id。",
            param_hint="--candidate-id",
        )
    if normalized_mode != "ids" and candidate_ids:
        raise typer.BadParameter(
            "只有 --selection ids 可以提供 --candidate-id。",
            param_hint="--candidate-id",
        )
    if normalized_mode == "filter" and not review_statuses:
        raise typer.BadParameter(
            "--selection filter 必须至少提供一个 --review-status。",
            param_hint="--review-status",
        )
    if normalized_mode != "filter" and review_statuses:
        raise typer.BadParameter(
            "只有 --selection filter 可以提供 --review-status。",
            param_hint="--review-status",
        )
    run_write_command(
        ctx,
        command="crawler.jobs.enrich",
        path=f"/api/agent/v1/crawler/jobs/{job_id}/enrich",
        json_body={
            "selection": {
                "mode": normalized_mode,
                "ids": candidate_ids,
                "filter": {"review_status": review_statuses} if review_statuses else {},
                "exclude_ids": exclude_candidate_ids,
            },
            "llm_profile_id": llm_profile_id,
        },
        guide_topic="crawler",
        human_formatter=format_detail,
    )


@jobs_app.command("enrich-many")
def enrich_many_faculty_crawl_jobs(
    ctx: typer.Context,
    job_ids: Annotated[
        list[int],
        typer.Option("--job-id", min=1, help="可重复指定要补全候选的抓取任务 ID。"),
    ],
    selection_mode: Annotated[
        str,
        typer.Option("--selection", help="批量候选选择方式：all 或 filter。"),
    ],
    review_statuses: Annotated[
        list[str],
        typer.Option(
            "--review-status",
            help="filter 模式下可重复指定 pending、accepted、rejected 或 merged。",
        ),
    ] = [],
    llm_profile_id: Annotated[int | None, typer.Option("--llm-profile-id", min=1)] = None,
) -> None:
    normalized_mode = selection_mode.strip().lower()
    if normalized_mode not in {"all", "filter"}:
        raise typer.BadParameter(
            "批量补全的 --selection 必须是 all 或 filter；ids 模式请逐任务调用 enrich。",
            param_hint="--selection",
        )
    if len(set(job_ids)) != len(job_ids):
        raise typer.BadParameter("--job-id 不能重复。", param_hint="--job-id")
    if len(job_ids) > 100:
        raise typer.BadParameter("一次最多提交 100 个抓取任务。", param_hint="--job-id")
    if normalized_mode == "filter" and not review_statuses:
        raise typer.BadParameter(
            "--selection filter 必须至少提供一个 --review-status。",
            param_hint="--review-status",
        )
    if normalized_mode == "all" and review_statuses:
        raise typer.BadParameter(
            "只有 --selection filter 可以提供 --review-status。",
            param_hint="--review-status",
        )
    selection = {
        "mode": normalized_mode,
        "ids": [],
        "filter": {"review_status": review_statuses} if review_statuses else {},
        "exclude_ids": [],
    }
    run_write_command(
        ctx,
        command="crawler.jobs.enrich-many",
        path="/api/agent/v1/crawler/jobs/enrich-many",
        json_body={
            "items": [
                {
                    "job_id": job_id,
                    "selection": selection,
                    "llm_profile_id": llm_profile_id,
                }
                for job_id in job_ids
            ],
        },
        guide_topic="crawler",
        human_formatter=format_detail,
    )


def _read_batch_items(items_file: Path) -> list[dict[str, object]]:
    try:
        payload = json.loads(items_file.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise typer.BadParameter(
            "--items-file 必须是可读取的 UTF-8 JSON 文件。",
            param_hint="--items-file",
        ) from exc
    if isinstance(payload, dict):
        payload = payload.get("items")
    if not isinstance(payload, list) or not payload or not all(isinstance(item, dict) for item in payload):
        raise typer.BadParameter(
            "--items-file 必须包含非空任务对象数组或 {items: [...]}。",
            param_hint="--items-file",
        )
    if len(payload) > 100:
        raise typer.BadParameter("一次最多提交 100 个抓取任务。", param_hint="--items-file")
    return payload


@jobs_app.command("pause")
def pause_faculty_crawl_job(
    ctx: typer.Context,
    job_id: Annotated[int, typer.Argument(min=1)],
) -> None:
    run_write_command(
        ctx,
        command="crawler.jobs.pause",
        path=f"/api/agent/v1/crawler/jobs/{job_id}/pause",
        guide_topic="crawler",
        human_formatter=format_detail,
    )


@jobs_app.command("resume")
def resume_faculty_crawl_job(
    ctx: typer.Context,
    job_id: Annotated[int, typer.Argument(min=1)],
    llm_profile_id: Annotated[int | None, typer.Option("--llm-profile-id", min=1)] = None,
) -> None:
    run_write_command(
        ctx,
        command="crawler.jobs.resume",
        path=f"/api/agent/v1/crawler/jobs/{job_id}/resume",
        json_body={"llm_profile_id": llm_profile_id} if llm_profile_id is not None else None,
        guide_topic="crawler",
        human_formatter=format_detail,
    )


@jobs_app.command("cancel")
def cancel_faculty_crawl_job(
    ctx: typer.Context,
    job_id: Annotated[int, typer.Argument(min=1)],
) -> None:
    run_write_command(
        ctx,
        command="crawler.jobs.cancel",
        path=f"/api/agent/v1/crawler/jobs/{job_id}/cancel",
        guide_topic="crawler",
        human_formatter=format_detail,
    )


@jobs_app.command("resume-review")
def resume_faculty_crawl_job_review(
    ctx: typer.Context,
    job_id: Annotated[int, typer.Argument(min=1)],
) -> None:
    run_write_command(
        ctx,
        command="crawler.jobs.resume-review",
        path=f"/api/agent/v1/crawler/jobs/{job_id}/resume-review",
        guide_topic="crawler",
        human_formatter=format_detail,
    )


@jobs_app.command("delete")
def delete_faculty_crawl_job(
    ctx: typer.Context,
    job_id: Annotated[int, typer.Argument(min=1)],
) -> None:
    run_write_command(
        ctx,
        command="crawler.jobs.delete",
        path=f"/api/agent/v1/crawler/jobs/{job_id}/delete",
        guide_topic="crawler",
        human_formatter=format_detail,
    )


@jobs_app.command("restore")
def restore_faculty_crawl_job(
    ctx: typer.Context,
    job_id: Annotated[int, typer.Argument(min=1)],
) -> None:
    run_write_command(
        ctx,
        command="crawler.jobs.restore",
        path=f"/api/agent/v1/crawler/jobs/{job_id}/restore",
        guide_topic="crawler",
        human_formatter=format_detail,
    )
