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


class DeliveryView(StrEnum):
    UPCOMING = "upcoming"
    ATTENTION = "attention"
    HISTORY = "history"


class DeliverySource(StrEnum):
    ALL = "all"
    MANUAL = "manual"
    BATCH = "batch"


class DeliverySort(StrEnum):
    SCHEDULED_ASC = "scheduled_asc"
    SCHEDULED_DESC = "scheduled_desc"
    UPDATED_DESC = "updated_desc"
    UPDATED_ASC = "updated_asc"
    EVENT_DESC = "event_desc"
    EVENT_ASC = "event_asc"


class DeliverySearchField(StrEnum):
    RECIPIENT_NAME = "recipient_name"
    RECIPIENT_EMAIL = "recipient_email"
    SUBJECT = "subject"
    BATCH_NAME = "batch_name"


deliveries_app = typer.Typer(
    help="查询统一发送计划，并安全修改尚未发送的单封邮件时间。",
    no_args_is_help=True,
)


@deliveries_app.command("list")
def list_deliveries(
    ctx: typer.Context,
    view: Annotated[
        DeliveryView,
        typer.Option("--view", help="upcoming、attention 或 history。"),
    ] = DeliveryView.UPCOMING,
    page: Annotated[int, typer.Option("--page", min=1)] = 1,
    page_size: Annotated[int, typer.Option("--page-size", min=1, max=100)] = 25,
    identity_id: Annotated[int | None, typer.Option("--identity-id", min=1)] = None,
    source: Annotated[
        DeliverySource,
        typer.Option("--source", help="all、manual 或 batch。"),
    ] = DeliverySource.ALL,
    status: Annotated[str | None, typer.Option("--status", help="发送计划状态筛选。")]=None,
    sort: Annotated[DeliverySort | None, typer.Option("--sort")] = None,
    search_fields: Annotated[
        list[DeliverySearchField] | None,
        typer.Option("--search-field", help="可重复：recipient_name、recipient_email、subject、batch_name。"),
    ] = None,
    query: Annotated[str | None, typer.Option("--query", "-q")] = None,
    task_id: Annotated[int | None, typer.Option("--task-id", min=1)] = None,
    all_items: Annotated[bool, typer.Option("--all", help="自动读取全部分页结果。")]=False,
    fields: Annotated[str | None, typer.Option("--fields", help="只返回需要的字段，逗号分隔。")]=None,
) -> None:
    run_read_command(
        ctx,
        command="deliveries.list",
        path="/api/agent/v1/deliveries",
        params={
            "view": view.value,
            "page": page,
            "page_size": page_size,
            "identity_id": identity_id,
            "source": source.value,
            "status": status,
            "sort": sort.value if sort is not None else None,
            "search_fields": (
                ",".join(item.value for item in search_fields)
                if search_fields
                else None
            ),
            "query": query,
            "task_id": task_id,
        },
        fetch_all=all_items,
        fields=fields,
        guide_topic="tasks",
        human_formatter=lambda data: format_page(
            data,
            columns=(
                ("id", "任务 ID"),
                ("professor_name", "收件人"),
                ("identity_name", "发件身份"),
                ("status_label", "状态"),
                ("scheduled_at", "计划时间"),
            ),
        ),
    )


@deliveries_app.command("reschedule")
def reschedule_delivery(
    ctx: typer.Context,
    task_id: Annotated[int, typer.Argument(min=1)],
    scheduled_at: Annotated[
        str,
        typer.Option("--scheduled-at", help="带时区的 ISO 8601 时间，且至少晚于当前时间 1 分钟。"),
    ],
    expected_updated_at: Annotated[
        str,
        typer.Option(
            "--expected-updated-at",
            help="deliveries list 返回的 expected_updated_at；状态已变化时拒绝覆盖。",
        ),
    ],
) -> None:
    run_write_command(
        ctx,
        command="deliveries.reschedule",
        path=f"/api/agent/v1/deliveries/{task_id}/schedule",
        method="PATCH",
        json_body={
            "scheduled_at": scheduled_at,
            "expected_updated_at": expected_updated_at,
        },
        guide_topic="tasks",
        human_formatter=format_detail,
    )
