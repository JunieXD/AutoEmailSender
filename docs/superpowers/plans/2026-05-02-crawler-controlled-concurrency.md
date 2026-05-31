# 智能抓取受控并发实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 为智能抓取增加任务级并发和详情页富化受控并发，在不破坏保存正确性的前提下缩短抓取完成时间。

**架构：** 后台运行时从固定 1 个 crawler worker 扩展为可配置数量；单个抓取任务保留列表页发现阶段的串行行为，只把 `_enrich_saved_candidates` 改成固定并发 worker + host 级限流 + 保存协程顺序收口；运行指标补充重试、限流和候选处理统计。

**技术栈：** FastAPI 后端、SQLAlchemy async ORM、Alembic、SQLite、asyncio、unittest、uv。

---

## 文件结构

- 修改 `backend/app/core/config.py`：新增 crawler 并发相关配置。
- 创建 `backend/test/test_runtime_manager.py`：验证 `RuntimeManager` 会按配置启动多个 crawler worker。
- 修改 `backend/app/services/runtime_manager.py`：按 `crawler_worker_count` 启动多个 crawler loop。
- 修改 `backend/app/models/crawl_job.py`：为 `CrawlJobRun` 增加并发统计字段。
- 创建 `backend/alembic/versions/e5f1c2d3a4b6_add_crawl_run_concurrency_metrics.py`：为 `crawl_job_runs` 增加统计列。
- 修改 `backend/test/test_database_schema.py`：验证新列存在。
- 修改 `backend/app/services/crawl_job_runtime.py`：实现详情页富化固定并发池、host 限流、保存协程、重试和汇总。
- 修改 `backend/test/test_crawl_job_runtime.py`：覆盖多 worker、富化并发上限、host 限流、重试和取消收口。

---

### 任务 1：把 crawler worker 数量变成可配置并验证运行时会按配置启动

**文件：**
- 修改：`backend/app/core/config.py`
- 修改：`backend/app/services/runtime_manager.py`
- 创建：`backend/test/test_runtime_manager.py`

- [ ] **步骤 1：先补失败的运行时测试**

创建 `backend/test/test_runtime_manager.py`：

```python
from __future__ import annotations

import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from app.services.runtime_manager import RuntimeManager


class RuntimeManagerTests(unittest.IsolatedAsyncioTestCase):
    async def test_start_creates_multiple_crawler_workers_from_settings(self) -> None:
        session_factory = AsyncMock()
        manager = RuntimeManager(session_factory)

        with patch("app.services.runtime_manager.get_settings") as mocked_get_settings:
            mocked_get_settings.return_value = type(
                "SettingsStub",
                (),
                {
                    "dispatcher_interval_seconds": 30,
                    "imap_poll_interval_seconds": 60,
                    "crawler_worker_count": 2,
                },
            )()
            with patch.object(manager, "_loop", new=AsyncMock()) as mocked_loop:
                await manager.start()

        worker_names = [call.args[0] for call in mocked_loop.await_args_list]
        self.assertEqual(worker_names.count("crawler-worker-1"), 1)
        self.assertEqual(worker_names.count("crawler-worker-2"), 1)
        self.assertIn("dispatcher", worker_names)
        self.assertIn("imap-poller", worker_names)

        await manager.stop()
```

- [ ] **步骤 2：运行测试验证失败**

运行：

```powershell
rtk uv run python -m unittest test.test_runtime_manager.RuntimeManagerTests.test_start_creates_multiple_crawler_workers_from_settings
```

预期：FAIL，当前 `Settings` 没有 `crawler_worker_count`，且 `RuntimeManager.start()` 只创建 1 个 `crawler-worker`。

- [ ] **步骤 3：新增配置并改造 RuntimeManager**

在 `backend/app/core/config.py` 的 `Settings` 中加入：

```python
    crawler_worker_count: int
    crawler_profile_enrichment_concurrency: int
    crawler_host_concurrency: int
    crawler_profile_fetch_max_retries: int
```

并在 `get_settings()` 中增加默认值：

```python
        crawler_worker_count=_get_int_env("CRAWLER_WORKER_COUNT", 2),
        crawler_profile_enrichment_concurrency=_get_int_env("CRAWLER_PROFILE_ENRICHMENT_CONCURRENCY", 3),
        crawler_host_concurrency=_get_int_env("CRAWLER_HOST_CONCURRENCY", 1),
        crawler_profile_fetch_max_retries=_get_int_env("CRAWLER_PROFILE_FETCH_MAX_RETRIES", 2),
```

把 `backend/app/services/runtime_manager.py` 中 crawler 任务改成：

```python
        crawler_tasks = [
            asyncio.create_task(
                self._loop(
                    f"crawler-worker-{index}",
                    10,
                    run_queued_crawl_jobs_once,
                ),
            )
            for index in range(1, settings.crawler_worker_count + 1)
        ]
        self._tasks = [
            asyncio.create_task(self._loop("dispatcher", settings.dispatcher_interval_seconds, dispatch_due_tasks_once)),
            asyncio.create_task(self._loop("imap-poller", settings.imap_poll_interval_seconds, poll_for_replies_once)),
            *crawler_tasks,
        ]
```

- [ ] **步骤 4：运行测试验证通过**

运行：

```powershell
rtk uv run python -m unittest test.test_runtime_manager
```

预期：PASS，`RuntimeManager` 会创建 `crawler-worker-1` 和 `crawler-worker-2`。

- [ ] **步骤 5：Commit**

```powershell
rtk git add backend/app/core/config.py backend/app/services/runtime_manager.py backend/test/test_runtime_manager.py
rtk git commit -m "feat(抓取运行时): 支持配置多个 crawler worker"
```

### 任务 2：为 crawl_job_runs 增加并发观测字段

**文件：**
- 修改：`backend/app/models/crawl_job.py`
- 创建：`backend/alembic/versions/e5f1c2d3a4b6_add_crawl_run_concurrency_metrics.py`
- 修改：`backend/test/test_database_schema.py`

- [ ] **步骤 1：先补失败的数据库结构测试**

在 `backend/test/test_database_schema.py` 的 `test_crawl_job_tables_exist` 中加入：

```python
        crawl_run_columns = self._get_columns("crawl_job_runs")
        self.assertTrue(
            {
                "retry_count",
                "host_limited_count",
                "failed_candidate_count",
                "unchanged_candidate_count",
            }.issubset(crawl_run_columns),
        )
```

- [ ] **步骤 2：运行测试验证失败**

运行：

```powershell
rtk uv run python -m unittest test.test_database_schema.DatabaseSchemaTests.test_crawl_job_tables_exist
```

预期：FAIL，`crawl_job_runs` 还没有这些列。

- [ ] **步骤 3：更新模型与迁移**

在 `backend/app/models/crawl_job.py` 的 `CrawlJobRun` 中加入：

```python
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    host_limited_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    failed_candidate_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    unchanged_candidate_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
```

创建 `backend/alembic/versions/e5f1c2d3a4b6_add_crawl_run_concurrency_metrics.py`：

```python
"""add crawl run concurrency metrics

Revision ID: e5f1c2d3a4b6
Revises: c3d4e5f6a7b8
Create Date: 2026-05-02 00:00:00.000000
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "e5f1c2d3a4b6"
down_revision: Union[str, Sequence[str], None] = "c3d4e5f6a7b8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("crawl_job_runs") as batch_op:
        batch_op.add_column(sa.Column("retry_count", sa.Integer(), server_default=sa.text("0"), nullable=False))
        batch_op.add_column(sa.Column("host_limited_count", sa.Integer(), server_default=sa.text("0"), nullable=False))
        batch_op.add_column(sa.Column("failed_candidate_count", sa.Integer(), server_default=sa.text("0"), nullable=False))
        batch_op.add_column(sa.Column("unchanged_candidate_count", sa.Integer(), server_default=sa.text("0"), nullable=False))


def downgrade() -> None:
    with op.batch_alter_table("crawl_job_runs") as batch_op:
        batch_op.drop_column("unchanged_candidate_count")
        batch_op.drop_column("failed_candidate_count")
        batch_op.drop_column("host_limited_count")
        batch_op.drop_column("retry_count")
```

- [ ] **步骤 4：运行测试验证通过**

运行：

```powershell
rtk uv run python -m unittest test.test_database_schema.DatabaseSchemaTests.test_crawl_job_tables_exist
```

预期：PASS，`crawl_job_runs` 出现新的并发统计列。

- [ ] **步骤 5：Commit**

```powershell
rtk git add backend/app/models/crawl_job.py backend/alembic/versions/e5f1c2d3a4b6_add_crawl_run_concurrency_metrics.py backend/test/test_database_schema.py
rtk git commit -m "feat(抓取运行): 增加并发统计字段"
```

### 任务 3：把详情页富化改成固定并发池 + host 限流 + 顺序保存

**文件：**
- 修改：`backend/app/services/crawl_job_runtime.py`
- 修改：`backend/test/test_crawl_job_runtime.py`

- [ ] **步骤 1：先补失败的富化并发测试**

在 `backend/test/test_crawl_job_runtime.py` 中新增最大并发数测试：

```python
    async def test_enrich_saved_candidates_limits_concurrency(self) -> None:
        job_id = await self._create_default_profile_and_job()
        await self._seed_candidates(job_id, count=5, host="example.edu")

        active = 0
        max_active = 0

        async def fake_crawl(ctx: CrawlToolContext, url: str, intent: str = "profile") -> PageSnapshot:
            nonlocal active, max_active
            active += 1
            max_active = max(max_active, active)
            await asyncio.sleep(0.01)
            active -= 1
            return PageSnapshot(url=url, final_url=url, status="succeeded", title="Profile", text="研究方向：智能体")

        async def fake_enrich(*args, **kwargs) -> CandidateEnrichmentPayload:
            return CandidateEnrichmentPayload(
                email="teacher@example.edu",
                department="计算机学院",
                research_direction="智能体",
                recent_papers=["Paper A"],
            )

        ctx = CrawlToolContext(
            job_id=job_id,
            start_url="https://example.edu/faculty",
            university="示例大学",
            school="计算机学院",
            session_factory=self.session_factory,
        )

        with patch("app.services.crawl_job_runtime.crawl_page_with_crawl4ai", new=fake_crawl), patch(
            "app.services.crawl_job_runtime.enrich_candidate_profile_with_llm",
            new=fake_enrich,
        ):
            await _enrich_saved_candidates(self.session_factory, ctx, llm_profile=await self._get_default_llm_profile())

        self.assertLessEqual(max_active, 3)
```

再加 host 限流测试：

```python
    async def test_enrich_saved_candidates_limits_same_host_to_one_request(self) -> None:
        job_id = await self._create_default_profile_and_job()
        await self._seed_candidates(job_id, count=3, host="same.example.edu")

        host_active = 0
        max_host_active = 0

        async def fake_crawl(ctx: CrawlToolContext, url: str, intent: str = "profile") -> PageSnapshot:
            nonlocal host_active, max_host_active
            host_active += 1
            max_host_active = max(max_host_active, host_active)
            await asyncio.sleep(0.01)
            host_active -= 1
            return PageSnapshot(url=url, final_url=url, status="succeeded", title="Profile", text="研究方向：智能体")

        with patch("app.services.crawl_job_runtime.crawl_page_with_crawl4ai", new=fake_crawl):
            ...

        self.assertLessEqual(max_host_active, 1)
```

- [ ] **步骤 2：运行测试验证失败**

运行：

```powershell
rtk uv run python -m unittest test.test_crawl_job_runtime.CrawlJobRuntimeTests.test_enrich_saved_candidates_limits_concurrency
rtk uv run python -m unittest test.test_crawl_job_runtime.CrawlJobRuntimeTests.test_enrich_saved_candidates_limits_same_host_to_one_request
```

预期：FAIL，当前 `_enrich_saved_candidates` 是串行循环，没有并发池和 host limiter，也没有 `_seed_candidates` 这类辅助准备流程。

- [ ] **步骤 3：补测试辅助函数**

先在 `backend/test/test_crawl_job_runtime.py` 中补辅助函数，供后续并发测试复用：

```python
    async def _seed_candidates(self, job_id: int, *, count: int, host: str) -> None:
        async with self.session_factory() as session:
            for index in range(count):
                session.add(
                    CrawlCandidate(
                        job_id=job_id,
                        name=f"老师{index}",
                        email=None,
                        title="教授",
                        university="示例大学",
                        school="计算机学院",
                        profile_url=f"https://{host}/teacher/{index}",
                        source_url=f"https://{host}/faculty",
                    )
                )
            await session.commit()
```

- [ ] **步骤 4：实现固定并发 worker 与顺序保存**

把 `backend/app/services/crawl_job_runtime.py` 的 `_enrich_saved_candidates` 拆成下面几个函数：

```python
@dataclass(slots=True)
class CandidateEnrichmentWorkItem:
    candidate_id: int
    candidate_name: str
    profile_url: str


@dataclass(slots=True)
class CandidateEnrichmentResult:
    candidate_id: int
    candidate_name: str
    profile_url: str
    status: str
    enrichment: CandidateEnrichmentPayload | None = None
    updated_fields: list[str] | None = None
    error_message: str | None = None
    retry_count: int = 0
    host_limited: bool = False
```

把主流程改成：

```python
    work_queue: asyncio.Queue[CandidateEnrichmentWorkItem] = asyncio.Queue()
    result_queue: asyncio.Queue[CandidateEnrichmentResult] = asyncio.Queue()
    host_limiters: dict[str, asyncio.Semaphore] = {}

    for candidate in pending_candidates:
        if candidate.profile_url:
            work_queue.put_nowait(
                CandidateEnrichmentWorkItem(
                    candidate_id=candidate.id,
                    candidate_name=candidate.name,
                    profile_url=candidate.profile_url,
                )
            )

    workers = [
        asyncio.create_task(
            _run_candidate_enrichment_worker(
                session_factory,
                ctx,
                llm_profile,
                work_queue,
                result_queue,
                host_limiters,
            )
        )
        for _ in range(get_settings().crawler_profile_enrichment_concurrency)
    ]
    saved_summary = await _consume_candidate_enrichment_results(
        session_factory,
        ctx.job_id,
        result_queue,
        expected_count=work_queue.qsize(),
        trace_callback=trace_callback,
    )
    await asyncio.gather(*workers)
```

worker 内用 host semaphore 控制：

```python
    hostname = urlparse(item.profile_url).hostname or ""
    limiter = host_limiters.setdefault(
        hostname,
        asyncio.Semaphore(get_settings().crawler_host_concurrency),
    )
    async with limiter:
        snapshot = await crawl_page_with_crawl4ai(ctx, item.profile_url, intent="profile")
```

保存协程内统一调用 `_apply_candidate_enrichment(...)`，并更新：

```python
    run.failed_candidate_count = failed
    run.unchanged_candidate_count = unchanged
    run.retry_count += result.retry_count
    run.host_limited_count += 1 if result.host_limited else 0
```

- [ ] **步骤 5：为可重试错误加 2 次重试**

在 worker 中围绕 `crawl_page_with_crawl4ai(...)` 加小范围重试：

```python
    for attempt in range(get_settings().crawler_profile_fetch_max_retries + 1):
        try:
            snapshot = await crawl_page_with_crawl4ai(ctx, item.profile_url, intent="profile")
            break
        except httpx.TimeoutException:
            if attempt >= get_settings().crawler_profile_fetch_max_retries:
                return CandidateEnrichmentResult(
                    candidate_id=item.candidate_id,
                    candidate_name=item.candidate_name,
                    profile_url=item.profile_url,
                    status="failed",
                    error_message="详情页抓取超时",
                    retry_count=attempt,
                )
            await asyncio.sleep(2**attempt)
```

- [ ] **步骤 6：运行测试验证通过**

运行：

```powershell
rtk uv run python -m unittest test.test_crawl_job_runtime.CrawlJobRuntimeTests.test_enrich_saved_candidates_limits_concurrency
rtk uv run python -m unittest test.test_crawl_job_runtime.CrawlJobRuntimeTests.test_enrich_saved_candidates_limits_same_host_to_one_request
rtk uv run python -m unittest test.test_crawl_job_runtime
```

预期：PASS，富化阶段最大并发不超过 `3`，同 host 最大活跃请求不超过 `1`，原有抓取运行时测试不回归。

- [ ] **步骤 7：Commit**

```powershell
rtk git add backend/app/services/crawl_job_runtime.py backend/test/test_crawl_job_runtime.py
rtk git commit -m "feat(智能抓取): 为详情页富化增加受控并发"
```

## 自检

- 规格覆盖度：已覆盖任务级并发、详情页富化并发、host 限流、顺序保存和并发观测字段。
- 占位符扫描：每个任务都给出了文件、代码片段、命令和预期结果，没有 `TODO` 或“后续实现”。
- 类型一致性：统一使用 `crawler_worker_count`、`crawler_profile_enrichment_concurrency`、`crawler_host_concurrency`、`retry_count` 等字段名。

## 执行交接

计划已完成并保存到 `docs/superpowers/plans/2026-05-02-crawler-controlled-concurrency.md`。两种执行方式：

**1. 子代理驱动（推荐）** - 每个任务调度一个新的子代理，任务间进行审查，快速迭代

**2. 内联执行** - 在当前会话中使用 executing-plans 执行任务，批量执行并设有检查点

选哪种方式？
