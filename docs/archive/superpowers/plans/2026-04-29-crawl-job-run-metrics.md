# 抓取任务运行指标实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 为智能抓取任务引入持久化 run 指标，确保重试、暂停继续、trace 截断后耗时和 token 统计仍准确。

**架构：** `crawl_jobs` 表示业务任务，新增 `crawl_job_runs` 表表示一次执行尝试。暂停和继续沿用同一个 run；失败或取消后的重试创建新 run；摘要接口优先从当前 run 读取 token 与活跃耗时，旧任务无 run 时才走兼容 fallback。

**技术栈：** FastAPI、SQLAlchemy async、Alembic、Pydantic、SQLite、Python `unittest`。

---

## 规格依据

实现必须覆盖以下规格：

`@docs/superpowers/specs/2026-04-28-crawl-job-run-metrics-design.md`

## 文件结构

- 创建：`backend/alembic/versions/f2a7c9d8e1b3_add_crawl_job_runs.py`
  职责：新增 `crawl_job_runs` 表、`crawl_jobs.current_run_id`，并为旧任务创建兼容 run。
- 修改：`backend/app/models/crawl_job.py`
  职责：新增 `CrawlJobRun` ORM 模型和 `CrawlJob.current_run` 关系。
- 修改：`backend/app/services/crawl_job_metrics.py`
  职责：把 run 持久指标作为主来源，保留旧 trace 解析 fallback。
- 创建：`backend/app/services/crawl_job_runs.py`
  职责：封装 run 生命周期、活跃时长结算、token 累加和当前 run 查询。
- 修改：`backend/app/api/crawl_jobs.py`
  职责：创建、暂停、继续、取消、重试时维护 run，并在摘要查询中批量读取 current run。
- 修改：`backend/app/services/crawl_job_runtime.py`
  职责：worker claim、完成、失败、取消、trace 到达时更新 run。
- 修改：`backend/app/models/__init__.py`
  职责：导出 `CrawlJobRun`。
- 修改：`backend/test/test_crawl_job_metrics.py`
  职责：覆盖 run 指标、运行中耗时、旧任务 fallback。
- 修改：`backend/test/test_crawl_jobs_api.py`
  职责：覆盖创建 run、pause/resume 不新建 run、retry 新建 run。
- 修改：`backend/test/test_crawl_job_runtime.py`
  职责：覆盖 worker claim、token 累加、终态结算。

## 实现原则

- `created_at` 和 `updated_at` 不再用于新任务的正式耗时计算。
- `agent_trace` 只用于展示日志，不再作为新任务 token 统计来源。
- 暂停继续不创建新 run，且暂停期间耗时不增长。
- 失败或取消后的重试一定创建新 run，token 和活跃耗时从 0 开始。
- 对无 run 的旧任务保持接口可用，不让历史数据阻塞页面加载。

### 任务 1：为 run 指标编写失败测试

**文件：**
- 修改：`backend/test/test_crawl_job_metrics.py`

- [ ] **步骤 1：添加 run 测试所需导入**

在文件顶部导入 `timedelta` 和 `CrawlJobRun`：

```python
from datetime import UTC, datetime, timedelta

from app.models import CrawlJob, CrawlJobRun, CrawlJobStatus
```

- [ ] **步骤 2：编写 current run 指标优先测试**

在 `CrawlJobMetricsTests` 中添加：

```python
def test_build_metrics_prefers_current_run_values(self) -> None:
    now = datetime(2026, 4, 29, 10, 0, 0, tzinfo=UTC)
    run = CrawlJobRun(
        id=10,
        job_id=1,
        attempt_number=2,
        status=CrawlJobStatus.PAUSED.value,
        active_seconds=125,
        input_tokens=1000,
        output_tokens=80,
        total_tokens=1080,
    )
    job = CrawlJob(
        id=1,
        university="示例大学",
        school="计算机学院",
        start_url="https://example.edu/faculty",
        status=CrawlJobStatus.PAUSED.value,
        progress_current=0,
        progress_total=0,
        agent_trace=[
            {
                "raw": {
                    "type": "updates",
                    "data": {
                        "model": {
                            "messages": [
                                "usage_metadata={'input_tokens': 1, 'output_tokens': 2, 'total_tokens': 3}",
                            ]
                        }
                    },
                }
            }
        ],
        created_at=now - timedelta(hours=4),
        updated_at=now,
    )
    job.current_run = run

    metrics = build_crawl_job_metrics(job, now=now)

    self.assertEqual(metrics.input_tokens, 1000)
    self.assertEqual(metrics.output_tokens, 80)
    self.assertEqual(metrics.total_tokens, 1080)
    self.assertEqual(metrics.duration_seconds, 125)
```

- [ ] **步骤 3：编写运行中 active_started_at 追加耗时测试**

添加：

```python
def test_build_metrics_adds_open_active_segment_for_running_run(self) -> None:
    now = datetime(2026, 4, 29, 10, 5, 0, tzinfo=UTC)
    run = CrawlJobRun(
        id=11,
        job_id=2,
        attempt_number=1,
        status=CrawlJobStatus.RUNNING.value,
        started_at=datetime(2026, 4, 29, 10, 0, 0, tzinfo=UTC),
        active_started_at=datetime(2026, 4, 29, 10, 3, 0, tzinfo=UTC),
        active_seconds=60,
        input_tokens=34,
        output_tokens=12,
        total_tokens=46,
    )
    job = CrawlJob(
        id=2,
        university="示例大学",
        school="计算机学院",
        start_url="https://example.edu/faculty",
        status=CrawlJobStatus.RUNNING.value,
        progress_current=0,
        progress_total=0,
        agent_trace=[],
        created_at=datetime(2026, 4, 29, 9, 0, 0, tzinfo=UTC),
        updated_at=now,
    )
    job.current_run = run

    metrics = build_crawl_job_metrics(job, now=now)

    self.assertEqual(metrics.duration_seconds, 180)
    self.assertEqual(metrics.total_tokens, 46)
```

- [ ] **步骤 4：确认旧 fallback 测试保留**

保留现有 `test_build_metrics_aggregates_token_usage_and_duration`、`test_build_metrics_falls_back_to_response_metadata_token_usage`、`test_build_metrics_handles_missing_trace`，不要删掉。它们验证无 current run 时仍可兼容旧数据。

- [ ] **步骤 5：运行指标测试验证失败**

运行：

```bash
cd backend
uv run python -m unittest test.test_crawl_job_metrics.CrawlJobMetricsTests.test_build_metrics_prefers_current_run_values test.test_crawl_job_metrics.CrawlJobMetricsTests.test_build_metrics_adds_open_active_segment_for_running_run
```

预期：失败，报错包含 `ImportError: cannot import name 'CrawlJobRun'` 或 `TypeError: build_crawl_job_metrics() got an unexpected keyword argument 'now'`。

### 任务 2：新增数据库模型与迁移

**文件：**
- 创建：`backend/alembic/versions/f2a7c9d8e1b3_add_crawl_job_runs.py`
- 修改：`backend/app/models/crawl_job.py`
- 修改：`backend/app/models/__init__.py`

- [ ] **步骤 1：新增 ORM 模型**

在 `backend/app/models/crawl_job.py` 的 `CrawlJob` 之后、`CrawlPage` 之前新增：

```python
class CrawlJobRun(Base):
    __tablename__ = "crawl_job_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("crawl_jobs.id", ondelete="CASCADE"), index=True)
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    active_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    paused_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    active_seconds: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    input_tokens: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    output_tokens: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    total_tokens: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
        onupdate=lambda: datetime.now(UTC),
    )

    job: Mapped["CrawlJob"] = relationship(
        back_populates="runs",
        foreign_keys=[job_id],
    )
```

- [ ] **步骤 2：给 CrawlJob 增加 current_run_id 和关系**

在 `CrawlJob` 的 `agent_trace` 后添加：

```python
    current_run_id: Mapped[int | None] = mapped_column(
        ForeignKey("crawl_job_runs.id", ondelete="SET NULL"),
        nullable=True,
    )
```

在 `CrawlJob` 关系区添加：

```python
    current_run: Mapped["CrawlJobRun | None"] = relationship(
        foreign_keys=[current_run_id],
        post_update=True,
    )
    runs: Mapped[list["CrawlJobRun"]] = relationship(
        back_populates="job",
        cascade="all, delete-orphan",
        foreign_keys="CrawlJobRun.job_id",
    )
```

- [ ] **步骤 3：导出模型**

在 `backend/app/models/__init__.py` 的 crawl job 导入中加入 `CrawlJobRun`，并把 `CrawlJobRun` 加到 `__all__`。

导入块应包含：

```python
from app.models.crawl_job import (
    CrawlCandidate,
    CrawlCandidateReviewStatus,
    CrawlJob,
    CrawlJobRun,
    CrawlJobStatus,
    CrawlPage,
    CrawlPageStatus,
)
```

- [ ] **步骤 4：创建 Alembic 迁移**

创建 `backend/alembic/versions/f2a7c9d8e1b3_add_crawl_job_runs.py`：

```python
"""add crawl job runs

Revision ID: f2a7c9d8e1b3
Revises: 6d7e8f9a0b12
Create Date: 2026-04-29 00:00:00.000000
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "f2a7c9d8e1b3"
down_revision = "6d7e8f9a0b12"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "crawl_job_runs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("job_id", sa.Integer(), nullable=False),
        sa.Column("attempt_number", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=64), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("active_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("paused_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("active_seconds", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("input_tokens", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("output_tokens", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("total_tokens", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.ForeignKeyConstraint(["job_id"], ["crawl_jobs.id"], name=op.f("fk_crawl_job_runs_job_id_crawl_jobs"), ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_crawl_job_runs")),
        sa.UniqueConstraint("job_id", "attempt_number", name=op.f("uq_crawl_job_runs_job_attempt")),
    )
    op.create_index(op.f("ix_crawl_job_runs_job_id"), "crawl_job_runs", ["job_id"], unique=False)
    op.create_index(op.f("ix_crawl_job_runs_status"), "crawl_job_runs", ["status"], unique=False)

    with op.batch_alter_table("crawl_jobs") as batch_op:
        batch_op.add_column(sa.Column("current_run_id", sa.Integer(), nullable=True))
        batch_op.create_foreign_key(
            batch_op.f("fk_crawl_jobs_current_run_id_crawl_job_runs"),
            "crawl_job_runs",
            ["current_run_id"],
            ["id"],
            ondelete="SET NULL",
        )

    connection = op.get_bind()
    jobs = connection.execute(
        sa.text(
            """
            SELECT id, status, created_at, updated_at, agent_trace
            FROM crawl_jobs
            ORDER BY id
            """
        )
    ).mappings()
    for job in jobs:
        active_seconds = _legacy_active_seconds(job["created_at"], job["updated_at"])
        tokens = _legacy_token_totals(job["agent_trace"])
        result = connection.execute(
            sa.text(
                """
                INSERT INTO crawl_job_runs (
                    job_id, attempt_number, status, finished_at, active_seconds,
                    input_tokens, output_tokens, total_tokens, created_at, updated_at
                )
                VALUES (
                    :job_id, 1, :status, :finished_at, :active_seconds,
                    :input_tokens, :output_tokens, :total_tokens, :created_at, :updated_at
                )
                """
            ),
            {
                "job_id": job["id"],
                "status": job["status"],
                "finished_at": job["updated_at"] if job["status"] in {"needs_review", "completed", "failed", "canceled"} else None,
                "active_seconds": active_seconds,
                "input_tokens": tokens[0],
                "output_tokens": tokens[1],
                "total_tokens": tokens[2],
                "created_at": job["created_at"],
                "updated_at": job["updated_at"],
            },
        )
        run_id = result.lastrowid
        connection.execute(
            sa.text("UPDATE crawl_jobs SET current_run_id = :run_id WHERE id = :job_id"),
            {"run_id": run_id, "job_id": job["id"]},
        )


def downgrade() -> None:
    with op.batch_alter_table("crawl_jobs") as batch_op:
        batch_op.drop_constraint(batch_op.f("fk_crawl_jobs_current_run_id_crawl_job_runs"), type_="foreignkey")
        batch_op.drop_column("current_run_id")

    op.drop_index(op.f("ix_crawl_job_runs_status"), table_name="crawl_job_runs")
    op.drop_index(op.f("ix_crawl_job_runs_job_id"), table_name="crawl_job_runs")
    op.drop_table("crawl_job_runs")


def _legacy_active_seconds(created_at: object, updated_at: object) -> int:
    if created_at is None or updated_at is None:
        return 0
    try:
        return max(0, int((updated_at - created_at).total_seconds()))
    except AttributeError:
        return 0


def _legacy_token_totals(agent_trace: object) -> tuple[int, int, int]:
    import json
    import re

    if not agent_trace:
        return (0, 0, 0)
    if isinstance(agent_trace, str):
        try:
            trace = json.loads(agent_trace)
        except json.JSONDecodeError:
            return (0, 0, 0)
    else:
        trace = agent_trace
    if not isinstance(trace, list):
        return (0, 0, 0)

    patterns = (
        re.compile(r"usage_metadata=\\{'input_tokens':\\s*(?P<input>\\d+),\\s*'output_tokens':\\s*(?P<output>\\d+),\\s*'total_tokens':\\s*(?P<total>\\d+)"),
        re.compile(r"'token_usage':\\s*\\{'completion_tokens':\\s*(?P<output>\\d+),\\s*'prompt_tokens':\\s*(?P<input>\\d+),\\s*'total_tokens':\\s*(?P<total>\\d+)"),
    )
    totals = [0, 0, 0]
    for event in trace:
        haystack = str(event)
        for pattern in patterns:
            match = pattern.search(haystack)
            if match:
                totals[0] += int(match.group("input"))
                totals[1] += int(match.group("output"))
                totals[2] += int(match.group("total"))
                break
    return (totals[0], totals[1], totals[2])
```

如果本地 `alembic heads` 显示最新 head 不是 `6d7e8f9a0b12`，先运行：

```bash
cd backend
uv run alembic heads
```

然后把 `down_revision` 改为实际单一 head。

- [ ] **步骤 5：运行指标测试验证模型已存在但逻辑未过**

运行：

```bash
cd backend
uv run python -m unittest test.test_crawl_job_metrics.CrawlJobMetricsTests.test_build_metrics_prefers_current_run_values test.test_crawl_job_metrics.CrawlJobMetricsTests.test_build_metrics_adds_open_active_segment_for_running_run
```

预期：失败，报错集中在 `build_crawl_job_metrics` 仍未读取 current run。

### 任务 3：实现指标读取与 run 生命周期服务

**文件：**
- 创建：`backend/app/services/crawl_job_runs.py`
- 修改：`backend/app/services/crawl_job_metrics.py`
- 修改：`backend/test/test_crawl_job_metrics.py`

- [ ] **步骤 1：创建 run 服务模块**

创建 `backend/app/services/crawl_job_runs.py`：

```python
from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import CrawlJob, CrawlJobRun, CrawlJobStatus


USAGE_METADATA_PATTERN = re.compile(
    r"usage_metadata=\{'input_tokens':\s*(?P<input>\d+),\s*'output_tokens':\s*(?P<output>\d+),\s*'total_tokens':\s*(?P<total>\d+)",
)
TOKEN_USAGE_PATTERN = re.compile(
    r"'token_usage':\s*\{'completion_tokens':\s*(?P<output>\d+),\s*'prompt_tokens':\s*(?P<input>\d+),\s*'total_tokens':\s*(?P<total>\d+)",
)


async def create_initial_crawl_job_run(
    session: AsyncSession,
    job: CrawlJob,
    *,
    now: datetime | None = None,
) -> CrawlJobRun:
    resolved_now = now or datetime.now(UTC)
    run = CrawlJobRun(
        job_id=job.id,
        attempt_number=1,
        status=job.status,
        created_at=resolved_now,
        updated_at=resolved_now,
    )
    session.add(run)
    await session.flush()
    job.current_run_id = run.id
    return run


async def create_retry_crawl_job_run(
    session: AsyncSession,
    job: CrawlJob,
    *,
    now: datetime | None = None,
) -> CrawlJobRun:
    resolved_now = now or datetime.now(UTC)
    max_attempt = await session.scalar(
        select(func.max(CrawlJobRun.attempt_number)).where(CrawlJobRun.job_id == job.id)
    )
    run = CrawlJobRun(
        job_id=job.id,
        attempt_number=int(max_attempt or 0) + 1,
        status=CrawlJobStatus.QUEUED.value,
        created_at=resolved_now,
        updated_at=resolved_now,
    )
    session.add(run)
    await session.flush()
    job.current_run_id = run.id
    return run


async def get_or_create_current_crawl_job_run(
    session: AsyncSession,
    job: CrawlJob,
    *,
    now: datetime | None = None,
) -> CrawlJobRun:
    if job.current_run_id is not None:
        run = await session.get(CrawlJobRun, job.current_run_id)
        if run is not None:
            return run
    return await create_initial_crawl_job_run(session, job, now=now)


async def mark_crawl_job_run_running(
    session: AsyncSession,
    job: CrawlJob,
    *,
    now: datetime | None = None,
) -> CrawlJobRun:
    resolved_now = now or datetime.now(UTC)
    run = await get_or_create_current_crawl_job_run(session, job, now=resolved_now)
    run.status = CrawlJobStatus.RUNNING.value
    if run.started_at is None:
        run.started_at = resolved_now
    run.active_started_at = resolved_now
    run.updated_at = resolved_now
    return run


async def mark_crawl_job_run_paused(
    session: AsyncSession,
    job: CrawlJob,
    *,
    now: datetime | None = None,
) -> CrawlJobRun:
    resolved_now = now or datetime.now(UTC)
    run = await get_or_create_current_crawl_job_run(session, job, now=resolved_now)
    _settle_active_segment(run, now=resolved_now)
    run.status = CrawlJobStatus.PAUSED.value
    run.paused_at = resolved_now
    run.updated_at = resolved_now
    return run


async def mark_crawl_job_run_queued(
    session: AsyncSession,
    job: CrawlJob,
    *,
    now: datetime | None = None,
) -> CrawlJobRun:
    resolved_now = now or datetime.now(UTC)
    run = await get_or_create_current_crawl_job_run(session, job, now=resolved_now)
    run.status = CrawlJobStatus.QUEUED.value
    run.updated_at = resolved_now
    return run


async def mark_crawl_job_run_finished(
    session: AsyncSession,
    job: CrawlJob,
    *,
    status: str,
    error_message: str | None = None,
    now: datetime | None = None,
) -> CrawlJobRun:
    resolved_now = now or datetime.now(UTC)
    run = await get_or_create_current_crawl_job_run(session, job, now=resolved_now)
    _settle_active_segment(run, now=resolved_now)
    run.status = status
    run.finished_at = resolved_now
    run.error_message = error_message
    run.updated_at = resolved_now
    return run


async def accumulate_crawl_job_run_tokens(
    session: AsyncSession,
    job_id: int,
    event: dict[str, object],
) -> bool:
    usage = extract_token_usage(event)
    if usage is None:
        return False

    job = await session.get(CrawlJob, job_id)
    if job is None:
        return False
    run = await get_or_create_current_crawl_job_run(session, job)
    run.input_tokens += usage["input_tokens"]
    run.output_tokens += usage["output_tokens"]
    run.total_tokens += usage["total_tokens"]
    run.updated_at = datetime.now(UTC)
    return True


def extract_token_usage(event: dict[str, object]) -> dict[str, int] | None:
    haystack = _stringify_trace_payload(event)
    for pattern in (USAGE_METADATA_PATTERN, TOKEN_USAGE_PATTERN):
        match = pattern.search(haystack)
        if match:
            return {
                "input_tokens": int(match.group("input")),
                "output_tokens": int(match.group("output")),
                "total_tokens": int(match.group("total")),
            }
    return None


def _settle_active_segment(run: CrawlJobRun, *, now: datetime) -> None:
    active_started_at = _ensure_datetime(run.active_started_at)
    if active_started_at is None:
        return
    run.active_seconds += max(0, int((now - active_started_at).total_seconds()))
    run.active_started_at = None


def _stringify_trace_payload(event: dict[str, object]) -> str:
    parts: list[str] = []
    for key in ("message", "summary"):
        value = event.get(key)
        if isinstance(value, str):
            parts.append(value)
    raw = event.get("raw")
    if raw is not None:
        parts.append(str(raw))
    else:
        parts.append(str(event))
    return "\n".join(parts)


def _ensure_datetime(value: object) -> datetime | None:
    if not isinstance(value, datetime):
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value
```

- [ ] **步骤 2：修改指标服务读取 current run**

把 `build_crawl_job_metrics` 签名改为：

```python
def build_crawl_job_metrics(
    job: Any,
    *,
    now: datetime | None = None,
) -> CrawlJobMetrics:
```

在函数开头加入：

```python
    current_run = getattr(job, "current_run", None)
    if current_run is not None:
        duration_seconds = int(getattr(current_run, "active_seconds", 0) or 0)
        active_started_at = _ensure_datetime(getattr(current_run, "active_started_at", None))
        if getattr(current_run, "status", None) == "running" and active_started_at is not None:
            resolved_now = now or datetime.now(UTC)
            duration_seconds += max(0, int((resolved_now - active_started_at).total_seconds()))
        return CrawlJobMetrics(
            input_tokens=int(getattr(current_run, "input_tokens", 0) or 0),
            output_tokens=int(getattr(current_run, "output_tokens", 0) or 0),
            total_tokens=int(getattr(current_run, "total_tokens", 0) or 0),
            duration_seconds=duration_seconds,
        )
```

保留后续旧 `agent_trace` fallback 逻辑不变。

- [ ] **步骤 3：避免重复正则定义**

在 `crawl_job_metrics.py` 删除本地 `USAGE_METADATA_PATTERN`、`TOKEN_USAGE_PATTERN` 和 `_extract_token_usage`，改为导入：

```python
from app.services.crawl_job_runs import extract_token_usage
```

循环中使用：

```python
usage = extract_token_usage(event)
```

- [ ] **步骤 4：运行指标测试验证通过**

运行：

```bash
cd backend
uv run python -m unittest test.test_crawl_job_metrics
```

预期：输出 `OK`。

- [ ] **步骤 5：Commit**

```bash
git add backend/app/models/crawl_job.py backend/app/models/__init__.py backend/alembic/versions/f2a7c9d8e1b3_add_crawl_job_runs.py backend/app/services/crawl_job_runs.py backend/app/services/crawl_job_metrics.py backend/test/test_crawl_job_metrics.py
git commit -m "feat(crawler): persist crawl job run metrics"
```

### 任务 4：API 状态流接入 run

**文件：**
- 修改：`backend/test/test_crawl_jobs_api.py`
- 修改：`backend/app/api/crawl_jobs.py`

- [ ] **步骤 1：添加 API run 断言辅助方法**

在 `CrawlJobsApiTests` 中添加：

```python
def _list_job_runs(self, job_id: int) -> list[dict[str, object]]:
    async def _list() -> list[dict[str, object]]:
        from app.core.database import get_session_factory
        from app.models import CrawlJobRun
        from sqlalchemy import select

        async with get_session_factory()() as session:
            runs = list(
                (
                    await session.execute(
                        select(CrawlJobRun)
                        .where(CrawlJobRun.job_id == job_id)
                        .order_by(CrawlJobRun.attempt_number.asc())
                    )
                ).scalars()
            )
            return [
                {
                    "id": run.id,
                    "attempt_number": run.attempt_number,
                    "status": run.status,
                    "active_seconds": run.active_seconds,
                    "input_tokens": run.input_tokens,
                    "total_tokens": run.total_tokens,
                }
                for run in runs
            ]

    return asyncio.run(_list())
```

- [ ] **步骤 2：编写创建任务创建 run 测试**

添加测试：

```python
def test_create_crawl_job_creates_initial_run(self) -> None:
    response = self.client.post(
        "/api/crawl-jobs",
        json={
            "university": "示例大学",
            "school": "计算机学院",
            "start_url": "https://example.edu/faculty",
            "llm_profile_id": None,
        },
    )

    self.assertEqual(response.status_code, 201, msg=response.text)
    runs = self._list_job_runs(response.json()["id"])
    self.assertEqual(len(runs), 1)
    self.assertEqual(runs[0]["attempt_number"], 1)
    self.assertEqual(runs[0]["status"], "queued")
```

- [ ] **步骤 3：扩展 pause/resume 测试断言不新建 run**

在现有 `test_pause_resume_crawl_job_flow_preserves_saved_data` 的 pause 后加入：

```python
self.assertEqual(len(self._list_job_runs(job_id)), 1)
self.assertEqual(self._list_job_runs(job_id)[0]["status"], "paused")
```

在 resume 后加入：

```python
self.assertEqual(len(self._list_job_runs(job_id)), 1)
self.assertEqual(self._list_job_runs(job_id)[0]["status"], "queued")
```

- [ ] **步骤 4：编写 retry 新建 run 测试**

添加测试：

```python
def test_retry_crawl_job_creates_new_run(self) -> None:
    create_response = self.client.post(
        "/api/crawl-jobs",
        json={
            "university": "示例大学",
            "school": "计算机学院",
            "start_url": "https://example.edu/faculty",
            "llm_profile_id": None,
        },
    )
    self.assertEqual(create_response.status_code, 201, msg=create_response.text)
    job_id = create_response.json()["id"]
    self._set_job_status(job_id, "failed")

    response = self.client.post(
        f"/api/crawl-jobs/{job_id}/retry",
        json={"clear_existing_data": True},
    )

    self.assertEqual(response.status_code, 200, msg=response.text)
    runs = self._list_job_runs(job_id)
    self.assertEqual([run["attempt_number"] for run in runs], [1, 2])
    self.assertEqual(runs[-1]["status"], "queued")
    detail_response = self.client.get(f"/api/crawl-jobs/{job_id}")
    self.assertEqual(detail_response.json()["duration_seconds"], 0)
    self.assertEqual(detail_response.json()["total_tokens"], 0)
```

- [ ] **步骤 5：运行新增 API 测试验证失败**

运行：

```bash
cd backend
uv run python -m unittest test.test_crawl_jobs_api.CrawlJobsApiTests.test_create_crawl_job_creates_initial_run test.test_crawl_jobs_api.CrawlJobsApiTests.test_pause_resume_crawl_job_flow_preserves_saved_data test.test_crawl_jobs_api.CrawlJobsApiTests.test_retry_crawl_job_creates_new_run
```

预期：至少部分失败，原因是 API 尚未维护 run。

- [ ] **步骤 6：创建任务时创建 initial run**

在 `crawl_jobs.py` 导入：

```python
from sqlalchemy.orm import selectinload

from app.services.crawl_job_runs import (
    create_initial_crawl_job_run,
    create_retry_crawl_job_run,
    mark_crawl_job_run_paused,
    mark_crawl_job_run_queued,
    mark_crawl_job_run_finished,
)
```

在 `create_crawl_job` 的 `await session.flush()` 后添加：

```python
    await create_initial_crawl_job_run(session, job)
```

- [ ] **步骤 7：pause/resume/cancel/retry 接入 run**

在 `pause_crawl_job` 设置 job 状态后添加：

```python
    await mark_crawl_job_run_paused(session, job)
```

在 `resume_crawl_job` 设置 job 状态后添加：

```python
    await mark_crawl_job_run_queued(session, job)
```

在 `cancel_crawl_job` 设置 job 状态后添加：

```python
    await mark_crawl_job_run_finished(
        session,
        job,
        status=CrawlJobStatus.CANCELED.value,
    )
```

在 `retry_crawl_job` 设置 job 状态后、record log 前添加：

```python
    await create_retry_crawl_job_run(session, job, now=now)
```

其中 `retry_crawl_job` 中先定义一次：

```python
    now = datetime.now(UTC)
```

并复用它设置 `job.updated_at = now`。

- [ ] **步骤 8：摘要查询预加载 current_run**

把 list 查询改为：

```python
                select(CrawlJob)
                .options(selectinload(CrawlJob.current_run))
                .order_by(CrawlJob.created_at.desc(), CrawlJob.id.desc())
                .limit(50),
```

把 `_get_crawl_job_or_404` 改为：

```python
async def _get_crawl_job_or_404(session: AsyncSession, job_id: int) -> CrawlJob:
    job = await session.scalar(
        select(CrawlJob)
        .options(selectinload(CrawlJob.current_run))
        .where(CrawlJob.id == job_id)
        .limit(1)
    )
    if job is None:
        raise HTTPException(status_code=404, detail="未找到抓取任务")
    return job
```

- [ ] **步骤 9：运行 API 测试验证通过**

运行：

```bash
cd backend
uv run python -m unittest test.test_crawl_jobs_api
```

预期：输出 `OK`。

- [ ] **步骤 10：Commit**

```bash
git add backend/app/api/crawl_jobs.py backend/test/test_crawl_jobs_api.py
git commit -m "feat(crawler): manage crawl job runs in API"
```

### 任务 5：运行时接入 run 与 token 累加

**文件：**
- 修改：`backend/test/test_crawl_job_runtime.py`
- 修改：`backend/app/services/crawl_job_runtime.py`

- [ ] **步骤 1：添加 runtime run 查询辅助方法**

在 `CrawlJobRuntimeTests` 中添加：

```python
async def _get_current_run(self, job_id: int):
    from app.models import CrawlJobRun

    async with self.session_factory() as session:
        job = await session.get(CrawlJob, job_id)
        if job is None or job.current_run_id is None:
            raise AssertionError(f"crawl job {job_id} has no current run")
        run = await session.get(CrawlJobRun, job.current_run_id)
        if run is None:
            raise AssertionError(f"crawl job run {job.current_run_id} not found")
        return run
```

- [ ] **步骤 2：创建默认任务时创建 run**

在 `_create_default_profile_and_job` 的 `await session.commit()` 前添加：

```python
            await session.flush()
            from app.services.crawl_job_runs import create_initial_crawl_job_run

            await create_initial_crawl_job_run(session, job)
```

- [ ] **步骤 3：编写 token 累加测试**

添加测试：

```python
async def test_run_queued_crawl_job_accumulates_tokens_on_current_run(self) -> None:
    job_id = await self._create_default_profile_and_job()

    async def fake_run(
        ctx: CrawlToolContext,
        llm_profile: LLMProfile,
        trace_callback=None,
    ) -> dict[str, object]:
        _ = ctx, llm_profile
        if trace_callback is not None:
            await trace_callback(
                {
                    "type": "updates",
                    "data": {
                        "model": {
                            "messages": [
                                "usage_metadata={'input_tokens': 100, 'output_tokens': 20, 'total_tokens': 120}",
                            ]
                        }
                    },
                }
            )
        return {}

    with patch(
        "app.services.crawl_job_runtime.run_faculty_crawler_agent",
        new=fake_run,
    ):
        await run_queued_crawl_jobs_once(self.session_factory)

    run = await self._get_current_run(job_id)
    self.assertEqual(run.input_tokens, 100)
    self.assertEqual(run.output_tokens, 20)
    self.assertEqual(run.total_tokens, 120)
```

- [ ] **步骤 4：编写 claim 和完成结算测试**

添加测试：

```python
async def test_run_queued_crawl_job_finishes_current_run(self) -> None:
    job_id = await self._create_default_profile_and_job()

    async def fake_run(
        ctx: CrawlToolContext,
        llm_profile: LLMProfile,
        trace_callback=None,
    ) -> dict[str, object]:
        _ = llm_profile, trace_callback
        await save_candidates(
            ctx,
            [ProfessorCandidatePayload(name="张三", email="zhang@example.edu")],
        )
        return {}

    with patch(
        "app.services.crawl_job_runtime.run_faculty_crawler_agent",
        new=fake_run,
    ):
        await run_queued_crawl_jobs_once(self.session_factory)

    run = await self._get_current_run(job_id)
    self.assertEqual(run.status, CrawlJobStatus.NEEDS_REVIEW.value)
    self.assertIsNotNone(run.started_at)
    self.assertIsNone(run.active_started_at)
    self.assertIsNotNone(run.finished_at)
    self.assertGreaterEqual(run.active_seconds, 0)
```

- [ ] **步骤 5：扩展暂停测试断言 run 状态**

在 `test_running_job_paused_by_tool_stays_paused` 末尾加入：

```python
run = await self._get_current_run(job_id)
self.assertEqual(run.status, CrawlJobStatus.PAUSED.value)
self.assertIsNone(run.active_started_at)
```

- [ ] **步骤 6：运行 runtime 新测试验证失败**

运行：

```bash
cd backend
uv run python -m unittest test.test_crawl_job_runtime.CrawlJobRuntimeTests.test_run_queued_crawl_job_accumulates_tokens_on_current_run test.test_crawl_job_runtime.CrawlJobRuntimeTests.test_run_queued_crawl_job_finishes_current_run test.test_crawl_job_runtime.CrawlJobRuntimeTests.test_running_job_paused_by_tool_stays_paused
```

预期：失败，原因是 runtime 尚未更新 current run。

- [ ] **步骤 7：worker claim 时标记 run running**

在 `crawl_job_runtime.py` 导入：

```python
from app.services.crawl_job_runs import (
    accumulate_crawl_job_run_tokens,
    mark_crawl_job_run_finished,
    mark_crawl_job_run_running,
)
```

在 claim 成功并取到 job 后、`await session.commit()` 前添加：

```python
        await mark_crawl_job_run_running(session, job, now=now)
```

- [ ] **步骤 8：trace callback 累加 token**

在 `_append_agent_trace` 中 `job.updated_at = datetime.now(UTC)` 前添加：

```python
        await accumulate_crawl_job_run_tokens(session, job_id, normalized_event)
```

注意：传入 `normalized_event`，因为其中的 `raw` 保留原始 trace。

- [ ] **步骤 9：完成和失败时结算 run**

在 `_complete_running_job` 中，设置 job status 后添加：

```python
        await mark_crawl_job_run_finished(
            session,
            job,
            status=job.status,
            error_message=job.error_message,
        )
```

在 `_mark_job_failed` 中设置 job status 后添加：

```python
        await mark_crawl_job_run_finished(
            session,
            job,
            status=CrawlJobStatus.FAILED.value,
            error_message=error_message,
        )
```

在 `except CrawlJobPaused` 分支里不需要再次结算；pause API 或工具 checkpoint 已将 job 置为 paused。若测试显示 runtime 直接捕获 paused 但 run 仍 running，则在该分支中打开 session 读取 job 并调用 `mark_crawl_job_run_finished` 不合适，应改用 `mark_crawl_job_run_paused`。

- [ ] **步骤 10：取消分支结算 run**

在 `except CrawlJobCanceled` 分支追加一个小 helper，确保取消后 run 为 canceled：

```python
        await _mark_job_canceled(session_factory, job_id)
```

新增 helper：

```python
async def _mark_job_canceled(
    session_factory: async_sessionmaker[AsyncSession],
    job_id: int,
) -> None:
    async with session_factory() as session:
        job = await session.get(CrawlJob, job_id)
        if job is None:
            return
        job.status = CrawlJobStatus.CANCELED.value
        job.updated_at = datetime.now(UTC)
        await mark_crawl_job_run_finished(
            session,
            job,
            status=CrawlJobStatus.CANCELED.value,
        )
        await session.commit()
```

- [ ] **步骤 11：运行 runtime 测试验证通过**

运行：

```bash
cd backend
uv run python -m unittest test.test_crawl_job_runtime
```

预期：输出 `OK`。

- [ ] **步骤 12：Commit**

```bash
git add backend/app/services/crawl_job_runtime.py backend/test/test_crawl_job_runtime.py
git commit -m "feat(crawler): track run metrics during crawl runtime"
```

### 任务 6：数据库迁移和接口集成验证

**文件：**
- 检查：`backend/alembic/versions/f2a7c9d8e1b3_add_crawl_job_runs.py`
- 检查：`backend/app/api/crawl_jobs.py`
- 检查：`backend/app/services/crawl_job_runtime.py`
- 检查：`backend/app/services/crawl_job_metrics.py`

- [ ] **步骤 1：运行 Alembic 升级验证**

运行：

```bash
cd backend
uv run alembic upgrade head
```

预期：退出码 0。本地 `data/auto_email_sender.db` 增加 `crawl_job_runs` 表，旧 `crawl_jobs` 有对应 run。

- [ ] **步骤 2：运行抓取相关后端测试**

运行：

```bash
cd backend
uv run python -m unittest test.test_crawl_job_metrics test.test_crawl_jobs_api test.test_crawl_job_runtime test.test_crawler_tools
```

预期：输出 `OK`。

- [ ] **步骤 3：抽查本地数据库迁移结果**

运行：

```bash
cd backend
@'
import sqlite3
from pathlib import Path

db = Path("../data/auto_email_sender.db")
conn = sqlite3.connect(db)
conn.row_factory = sqlite3.Row
print(conn.execute("select count(*) as count from crawl_job_runs").fetchone()["count"])
for row in conn.execute("select id, current_run_id from crawl_jobs order by id limit 5"):
    print(dict(row))
conn.close()
'@ | uv run python -
```

预期：第一行大于等于现有 `crawl_jobs` 数量；抽样 job 的 `current_run_id` 不是空。

- [ ] **步骤 4：运行全量后端测试**

运行：

```bash
cd backend
uv run python -m unittest discover test
```

预期：输出 `OK`。如果实验性 crawler 测试因外部网络失败，记录失败测试名和原因，不要修改无关代码。

- [ ] **步骤 5：检查差异范围**

运行：

```bash
git status --short
git diff --stat
```

预期：只包含本计划列出的后端模型、迁移、服务、API 和测试文件。

- [ ] **步骤 6：最终 Commit**

如果前面任务已分步提交，此步骤无需再提交。若实现者选择单提交，使用：

```bash
git add backend/alembic/versions/f2a7c9d8e1b3_add_crawl_job_runs.py backend/app/models/crawl_job.py backend/app/models/__init__.py backend/app/services/crawl_job_runs.py backend/app/services/crawl_job_metrics.py backend/app/api/crawl_jobs.py backend/app/services/crawl_job_runtime.py backend/test/test_crawl_job_metrics.py backend/test/test_crawl_jobs_api.py backend/test/test_crawl_job_runtime.py
git commit -m "feat(crawler): persist crawl job run metrics"
```

## 自检

- 规格覆盖度：计划覆盖 run 表、current run、暂停继续同 run、失败取消后重试新 run、token 持久累加、运行中耗时、旧任务 fallback、API 响应兼容和测试验收标准。
- 红旗词扫描：计划中没有会让实现者自行猜测的未完成步骤。
- 类型一致性：统一使用 `CrawlJobRun`、`current_run_id`、`active_started_at`、`active_seconds`、`create_initial_crawl_job_run`、`create_retry_crawl_job_run`、`mark_crawl_job_run_*`。
- 范围控制：不改前端 DTO，不改抓取策略，不引入费用估算；只修复运行指标的数据来源和状态流。
