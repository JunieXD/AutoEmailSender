# 智能抓取 Runtime V2 数据库调度器实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 将智能抓取主流程从 V1 长对话 Agent 切换为 V2 数据库调度器 + 短生命周期 Worker，并保留 V1 代码但不作为新任务默认路径。

**架构：** V2 以数据库为唯一事实来源：`crawl_page_tasks` 负责任务队列，`crawl_page_fetch_states` 记录页面抓取事实，`crawl_page_chunks` 承载 chunk 工作项，`crawl_candidate_enrichment_tasks` 承载候选补全工作项。调度器单实例决策，按并发配额启动 Page Worker、Chunk Worker、Enrichment Worker；所有 Worker 只处理一个工作项并通过 lease、状态条件更新和幂等 upsert 防止重复处理。

**技术栈：** FastAPI 后端、SQLAlchemy Async ORM、Alembic、SQLite、unittest、DeepAgents/LLM 工具封装、现有 crawler chunk/page ledger 服务。

---

## 规格来源

- 规格文档：`C:\StudyPrograms\AutoEmailSender\docs\superpowers\specs\2026-05-29-crawler-runtime-v2-database-scheduler-design.md`
- 核心原则：所有节省 token 或提速的优化都不能伤害正常抓取流程、覆盖率、重试、去重和任务结束判断。

## 文件结构

### 新增文件

- `C:\StudyPrograms\AutoEmailSender\backend\alembic\versions\b2e7c9f1a4d6_add_crawler_runtime_v2_tables.py`：新增 V2 字段、队列表、token 记录表和 lease 字段。
- `C:\StudyPrograms\AutoEmailSender\backend\app\services\crawler_v2_url_utils.py`：URL 规范化、同域判断、任务去重 key。
- `C:\StudyPrograms\AutoEmailSender\backend\app\services\crawler_v2_models.py`：V2 内部 dataclass/枚举，避免把调度 DTO 塞进 ORM 文件。
- `C:\StudyPrograms\AutoEmailSender\backend\app\services\crawler_v2_scheduler.py`：单实例调度决策、并发配额、任务结束判断。
- `C:\StudyPrograms\AutoEmailSender\backend\app\services\crawler_v2_page_worker.py`：每次处理一个 page task，负责 direct fetch、browser fallback、写 page/chunk；不发现或入队新 URL。
- `C:\StudyPrograms\AutoEmailSender\backend\app\services\crawler_v2_chunk_worker.py`：每次处理一个 chunk，暴露 `complete_current_chunk` 语义并原子保存 candidates 和 discovered_urls。
- `C:\StudyPrograms\AutoEmailSender\backend\app\services\crawler_v2_enrichment_worker.py`：每次补全一个候选，写回候选字段和补全任务状态。
- `C:\StudyPrograms\AutoEmailSender\backend\app\services\crawler_v2_token_usage.py`：按 worker_kind/work_item_id 记录 token。
- `C:\StudyPrograms\AutoEmailSender\backend\test\test_crawler_v2_url_utils.py`：URL 规范化和同域规则测试。
- `C:\StudyPrograms\AutoEmailSender\backend\test\test_crawler_v2_scheduler.py`：调度优先级、并发配额、结束判断测试。
- `C:\StudyPrograms\AutoEmailSender\backend\test\test_crawler_v2_page_worker.py`：Page Worker direct/browser fallback、chunk 生成、禁止链接入队测试。
- `C:\StudyPrograms\AutoEmailSender\backend\test\test_crawler_v2_chunk_worker.py`：Chunk Worker 领取、`complete_current_chunk`、候选/URL 原子保存测试。
- `C:\StudyPrograms\AutoEmailSender\backend\test\test_crawler_v2_enrichment_worker.py`：候选补全任务 lease、跳过、失败重试测试。
- `C:\StudyPrograms\AutoEmailSender\backend\test\test_crawler_v2_runtime_routing.py`：新任务默认 V2、V1 不再自动处理新任务测试。

### 修改文件

- `C:\StudyPrograms\AutoEmailSender\backend\app\models\crawl_job.py`：新增 `runtime_version` 等 job 字段，新增 `CrawlPageTask`、`CrawlCandidateEnrichmentTask`、`CrawlWorkerTokenUsage` ORM 模型，扩展 `CrawlPageFetchState` 抓取路径字段。
- `C:\StudyPrograms\AutoEmailSender\backend\app\models\crawl_chunk.py`：为 `CrawlPageChunk` 增加 `worker_id`、`claimed_at`、`lease_expires_at`，支持并发安全领取。
- `C:\StudyPrograms\AutoEmailSender\backend\app\models\__init__.py`：导出新增 ORM 模型。
- `C:\StudyPrograms\AutoEmailSender\backend\app\api\crawl_jobs.py`：创建任务时写入 `runtime_version='v2'`，并初始化入口 page task。
- `C:\StudyPrograms\AutoEmailSender\backend\app\schemas\crawl_job.py`：如 API 响应需要展示 runtime，补充 `runtime_version` 字段。
- `C:\StudyPrograms\AutoEmailSender\backend\app\services\crawl_job_runtime.py`：保留 V1 逻辑，但自动 worker 入口跳过 V2 新任务或转调 V2 runtime。
- `C:\StudyPrograms\AutoEmailSender\backend\app\services\runtime_manager.py`：crawler worker 调用 V2 调度入口，停止把新任务送进 V1 长 Agent。
- `C:\StudyPrograms\AutoEmailSender\backend\app\services\runtime_settings.py` 和相关 schema/model：补充或复用并发配置，确保 Page Worker 小并发、Chunk Worker 默认 1、Enrichment Worker 可并发。
- `C:\StudyPrograms\AutoEmailSender\backend\app\services\crawler_page_fetch_ledger.py`：复用并扩展页面账本记录 fetch mode、direct/browser 状态和 fallback reason。
- `C:\StudyPrograms\AutoEmailSender\backend\app\services\crawler_chunk_runtime.py`：保留 V1 工具；抽出候选合并/保存共用函数供 V2 Chunk Worker 使用。

---

### 任务 1：新增 V2 数据库模型和迁移

**文件：**
- 创建：`C:\StudyPrograms\AutoEmailSender\backend\alembic\versions\b2e7c9f1a4d6_add_crawler_runtime_v2_tables.py`
- 修改：`C:\StudyPrograms\AutoEmailSender\backend\app\models\crawl_job.py`
- 修改：`C:\StudyPrograms\AutoEmailSender\backend\app\models\crawl_chunk.py`
- 修改：`C:\StudyPrograms\AutoEmailSender\backend\app\models\__init__.py`
- 测试：`C:\StudyPrograms\AutoEmailSender\backend\test\test_crawl_job_models.py`

- [ ] **步骤 1：编写失败的模型测试**

在 `C:\StudyPrograms\AutoEmailSender\backend\test\test_crawl_job_models.py` 增加测试，确认 V2 表和关键默认值可写入。

```python
async def run() -> tuple[str, str, int, int, str]:
    session_factory = await _create_test_session_factory()
    async with session_factory() as session:
        job = CrawlJob(
            university="示例大学",
            school="计算机学院",
            start_url="https://cs.example.edu/faculty",
            runtime_version="v2",
        )
        session.add(job)
        await session.flush()
        page_task = CrawlPageTask(
            job_id=job.id,
            url="https://cs.example.edu/faculty",
            normalized_url="https://cs.example.edu/faculty",
            status="pending",
            source_kind="entry",
        )
        candidate = CrawlCandidate(job_id=job.id, name="张三", university="示例大学", school="计算机学院")
        session.add(candidate)
        await session.flush()
        enrich_task = CrawlCandidateEnrichmentTask(
            job_id=job.id,
            candidate_id=candidate.id,
            status="pending",
        )
        token_usage = CrawlWorkerTokenUsage(
            job_id=job.id,
            worker_kind="page",
            work_item_id="1",
            model="deepseek-chat",
            input_tokens=10,
            output_tokens=2,
            cached_tokens=8,
            total_tokens=12,
            duration_ms=100,
            status="succeeded",
        )
        session.add_all([page_task, enrich_task, token_usage])
        await session.commit()
        return job.runtime_version, page_task.status, enrich_task.attempt_count, token_usage.cached_tokens, page_task.normalized_url

self.assertEqual(asyncio.run(run()), ("v2", "pending", 0, 8, "https://cs.example.edu/faculty"))
```

- [ ] **步骤 2：运行测试验证失败**

运行：`cd C:\StudyPrograms\AutoEmailSender\backend; uv run python -m unittest test.test_crawl_job_models -v`

预期：FAIL，报错包含 `NameError: name 'CrawlPageTask' is not defined` 或 ORM 字段不存在。

- [ ] **步骤 3：实现 ORM 模型**

在 `CrawlJob` 增加字段：

```python
runtime_version: Mapped[str] = mapped_column(String(16), nullable=False, server_default=text("'v2'"), index=True)
current_worker_kind: Mapped[str | None] = mapped_column(String(64), nullable=True)
last_scheduler_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
```

新增 `CrawlPageTask`：

```python
class CrawlPageTask(Base):
    __tablename__ = "crawl_page_tasks"
    __table_args__ = (UniqueConstraint("job_id", "normalized_url", name="uq_crawl_page_tasks_job_url"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("crawl_jobs.id", ondelete="CASCADE"), index=True)
    url: Mapped[str] = mapped_column(String(1000), nullable=False)
    normalized_url: Mapped[str] = mapped_column(String(1000), nullable=False)
    parent_url: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    source_kind: Mapped[str] = mapped_column(String(64), nullable=False, server_default=text("'discovered'"))
    priority: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    status: Mapped[str] = mapped_column(String(64), nullable=False, index=True, server_default=text("'pending'"))
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    worker_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    fetched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP"))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP"), onupdate=lambda: datetime.now(UTC))
```

新增 `CrawlCandidateEnrichmentTask`：

```python
class CrawlCandidateEnrichmentTask(Base):
    __tablename__ = "crawl_candidate_enrichment_tasks"
    __table_args__ = (UniqueConstraint("job_id", "candidate_id", name="uq_crawl_candidate_enrichment_tasks_job_candidate"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("crawl_jobs.id", ondelete="CASCADE"), index=True)
    candidate_id: Mapped[int] = mapped_column(ForeignKey("crawl_candidates.id", ondelete="CASCADE"), index=True)
    status: Mapped[str] = mapped_column(String(64), nullable=False, index=True, server_default=text("'pending'"))
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    worker_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    enriched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP"))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP"), onupdate=lambda: datetime.now(UTC))
```

新增 `CrawlWorkerTokenUsage`：

```python
class CrawlWorkerTokenUsage(Base):
    __tablename__ = "crawl_worker_token_usages"

    id: Mapped[int] = mapped_column(primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("crawl_jobs.id", ondelete="CASCADE"), index=True)
    worker_kind: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    work_item_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    model: Mapped[str | None] = mapped_column(String(128), nullable=True)
    input_tokens: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    output_tokens: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    cached_tokens: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    total_tokens: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(String(64), nullable=False)
    error_kind: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP"))
```

为 `CrawlPageFetchState` 增加：

```python
fetch_mode: Mapped[str | None] = mapped_column(String(64), nullable=True)
direct_status: Mapped[str | None] = mapped_column(String(64), nullable=True)
fallback_reason: Mapped[str | None] = mapped_column(String(255), nullable=True)
browser_status: Mapped[str | None] = mapped_column(String(64), nullable=True)
```

为 `CrawlPageChunkStatus` 增加 `FAILED_RETRYABLE = "failed_retryable"` 和 `FAILED_TERMINAL = "failed_terminal"`，让调度器能统一判断 chunk 重试和终止失败。

为 `CrawlPageChunk` 增加：

```python
worker_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
```

- [ ] **步骤 4：创建 Alembic 迁移**

运行：`cd C:\StudyPrograms\AutoEmailSender\backend; uv run alembic revision -m "add crawler runtime v2 tables"`

编辑新迁移文件，使用 `op.add_column`、`op.create_table`、`op.create_index`、`op.create_unique_constraint` 创建上述字段和表。`downgrade()` 反向删除新表和新增列。

- [ ] **步骤 5：运行模型测试验证通过**

运行：`cd C:\StudyPrograms\AutoEmailSender\backend; uv run python -m unittest test.test_crawl_job_models -v`

预期：PASS。

- [ ] **步骤 6：验证迁移链**

运行：`cd C:\StudyPrograms\AutoEmailSender\backend; uv run alembic upgrade head`

预期：命令退出码为 0，无 migration head 冲突。

- [ ] **步骤 7：Commit**

```powershell
git add backend/app/models/crawl_job.py backend/app/models/crawl_chunk.py backend/app/models/__init__.py backend/alembic/versions/*_add_crawler_runtime_v2_tables.py backend/test/test_crawl_job_models.py
git commit -m "feat(crawler): 添加 V2 调度数据模型"
```

### 任务 2：实现 URL 规范化和队列入队服务

**文件：**
- 创建：`C:\StudyPrograms\AutoEmailSender\backend\app\services\crawler_v2_url_utils.py`
- 创建：`C:\StudyPrograms\AutoEmailSender\backend\test\test_crawler_v2_url_utils.py`

- [ ] **步骤 1：编写失败的 URL 测试**

```python
class CrawlerV2UrlUtilsTest(unittest.TestCase):
    def test_normalize_url_removes_fragment_and_orders_query(self) -> None:
        self.assertEqual(
            normalize_crawl_url("https://cs.example.edu/list?a=2&b=1#teacher"),
            "https://cs.example.edu/list?a=2&b=1",
        )

    def test_same_domain_allows_sub_path_only(self) -> None:
        self.assertTrue(is_allowed_crawl_url("https://cs.example.edu/a", root_url="https://cs.example.edu/start"))
        self.assertFalse(is_allowed_crawl_url("https://other.example.edu/a", root_url="https://cs.example.edu/start"))
```

- [ ] **步骤 2：运行测试验证失败**

运行：`cd C:\StudyPrograms\AutoEmailSender\backend; uv run python -m unittest test.test_crawler_v2_url_utils -v`

预期：FAIL，模块不存在。

- [ ] **步骤 3：实现 URL 工具**

```python
from __future__ import annotations

from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


def normalize_crawl_url(url: str) -> str:
    parts = urlsplit(url.strip())
    scheme = parts.scheme.lower() or "https"
    netloc = parts.netloc.lower()
    path = parts.path or "/"
    query = urlencode(sorted(parse_qsl(parts.query, keep_blank_values=True)))
    return urlunsplit((scheme, netloc, path, query, ""))


def is_allowed_crawl_url(url: str, *, root_url: str) -> bool:
    current = urlsplit(normalize_crawl_url(url))
    root = urlsplit(normalize_crawl_url(root_url))
    return current.scheme in {"http", "https"} and current.netloc == root.netloc
```

- [ ] **步骤 4：运行测试验证通过**

运行：`cd C:\StudyPrograms\AutoEmailSender\backend; uv run python -m unittest test.test_crawler_v2_url_utils -v`

预期：PASS。

- [ ] **步骤 5：Commit**

```powershell
git add backend/app/services/crawler_v2_url_utils.py backend/test/test_crawler_v2_url_utils.py
git commit -m "feat(crawler): 添加 V2 URL 规范化工具"
```

### 任务 3：实现 V2 调度器核心领取和结束判断

**文件：**
- 创建：`C:\StudyPrograms\AutoEmailSender\backend\app\services\crawler_v2_models.py`
- 创建：`C:\StudyPrograms\AutoEmailSender\backend\app\services\crawler_v2_scheduler.py`
- 创建：`C:\StudyPrograms\AutoEmailSender\backend\test\test_crawler_v2_scheduler.py`

- [ ] **步骤 1：编写失败的调度测试**

覆盖三个行为：page 优先、Chunk Worker 默认并发 1、没有 pending/processing/retryable 时结束。

```python
async def run() -> tuple[str, int, bool]:
    session_factory = await _create_test_session_factory()
    async with session_factory() as session:
        job = CrawlJob(university="示例大学", school="计算机学院", start_url="https://cs.example.edu", runtime_version="v2", status="running")
        session.add(job)
        await session.flush()
        session.add(CrawlPageTask(job_id=job.id, url="https://cs.example.edu", normalized_url="https://cs.example.edu/", status="pending", source_kind="entry"))
        await session.commit()
        job_id = job.id

    result = await run_crawler_v2_scheduler_once(session_factory, worker_id="scheduler-test")
    async with session_factory() as session:
        task_count = await session.scalar(select(func.count(CrawlPageTask.id)))
        job = await session.get(CrawlJob, job_id)
        return result.selected_kind, task_count or 0, job.status == "running"

self.assertEqual(asyncio.run(run()), ("page", 1, True))
```

- [ ] **步骤 2：运行测试验证失败**

运行：`cd C:\StudyPrograms\AutoEmailSender\backend; uv run python -m unittest test.test_crawler_v2_scheduler -v`

预期：FAIL，`run_crawler_v2_scheduler_once` 不存在。

- [ ] **步骤 3：实现调度 DTO**

在 `crawler_v2_models.py` 中定义：

```python
from dataclasses import dataclass

@dataclass(frozen=True)
class WorkerQuota:
    page: int = 2
    chunk: int = 1
    enrichment: int = 4

@dataclass(frozen=True)
class SchedulerResult:
    selected_kind: str
    selected_count: int = 0
    reason: str = ""
```

- [ ] **步骤 4：实现最小调度器**

`run_crawler_v2_scheduler_once` 在本任务只实现“单实例决策 + 并发安全领取 + 结束判断”，不直接执行真实 Worker。它查询一个 `running` 或 `queued` 且 `runtime_version='v2'` 的任务；若是 `queued`，先置为 `running`。按顺序领取 page、chunk、enrichment；领取通过 `UPDATE crawl_page_tasks SET status = 'processing', worker_id = :worker_id WHERE id = :id AND status IN ('pending', 'failed_retryable') AND (lease_expires_at IS NULL OR lease_expires_at < :now)` 条件更新，写入 `worker_id`、`claimed_at`、`lease_expires_at`。真实 Worker 执行在任务 8 接入，避免本任务依赖尚未实现的 Worker。

- [ ] **步骤 5：实现结束判断**

当 page task、chunk、enrichment task 均不存在 pending、processing 未过期、failed_retryable 时，将任务置为 `needs_review`。如果存在 terminal failed 工作项且也没有可恢复工作项，置为 `partially_completed`。

- [ ] **步骤 6：运行调度测试验证通过**

运行：`cd C:\StudyPrograms\AutoEmailSender\backend; uv run python -m unittest test.test_crawler_v2_scheduler -v`

预期：PASS。

- [ ] **步骤 7：Commit**

```powershell
git add backend/app/services/crawler_v2_models.py backend/app/services/crawler_v2_scheduler.py backend/test/test_crawler_v2_scheduler.py
git commit -m "feat(crawler): 实现 V2 调度器骨架"
```

### 任务 4：实现 Page Worker direct fetch 优先和 browser fallback

**文件：**
- 创建：`C:\StudyPrograms\AutoEmailSender\backend\app\services\crawler_v2_page_worker.py`
- 创建：`C:\StudyPrograms\AutoEmailSender\backend\test\test_crawler_v2_page_worker.py`
- 修改：`C:\StudyPrograms\AutoEmailSender\backend\app\services\crawler_page_fetch_ledger.py`

- [ ] **步骤 1：编写失败的 Page Worker 测试**

测试 direct fetch 成功时不调用 browser fallback。

```python
class FakeFetcher:
    def __init__(self) -> None:
        self.browser_called = False

    async def direct_fetch(self, url: str):
        return PageSnapshot(url=url, title="教师队伍", text="张三 教授 zhang@example.edu", html="<a href='/p2'>下一页</a>", links=["https://cs.example.edu/p2"], fetch_method="direct", status="succeeded")

    async def browser_fetch(self, url: str):
        self.browser_called = True
        raise AssertionError("browser fallback should not be called")
```

断言：page task 变为 `fetched`，`CrawlPage.fetch_method == 'direct'`，生成 chunks，但不入队 `p2`。

- [ ] **步骤 2：编写 browser fallback 测试**

direct 返回空正文或 403 时，browser 被调用且 `CrawlPageFetchState.fetch_mode == 'browser'`，`fallback_reason` 非空。

- [ ] **步骤 3：运行测试验证失败**

运行：`cd C:\StudyPrograms\AutoEmailSender\backend; uv run python -m unittest test.test_crawler_v2_page_worker -v`

预期：FAIL，Page Worker 不存在。

- [ ] **步骤 4：实现 Page Worker**

实现 `run_page_worker_once(session_factory, page_task_id, worker_id, fetcher)`：

```python
async def run_page_worker_once(session_factory, *, page_task_id: int, worker_id: str, fetcher: PageFetcher) -> PageWorkerResult:
    # 1. 校验 page task 属于当前 worker 且 lease 未过期
    # 2. 查 page ledger，terminal/proceeded 跳过
    # 3. direct_fetch
    # 4. direct 不可用时 browser_fetch 一次
    # 5. 写 CrawlPage、mark_page_fetch_result、create_chunks_for_page
    # 6. 不从页面 links 自动 upsert CrawlPageTask
    # 7. page task 标记 fetched/skipped/failed_retryable/failed_terminal
```

`direct` 不可用判断至少包括：状态非 succeeded、HTTP 403/429/5xx、正文为空、正文明显过短且 HTML 以脚本壳为主、反爬/验证码提示。

- [ ] **步骤 5：运行 Page Worker 测试验证通过**

运行：`cd C:\StudyPrograms\AutoEmailSender\backend; uv run python -m unittest test.test_crawler_v2_page_worker -v`

预期：PASS。

- [ ] **步骤 6：Commit**

```powershell
git add backend/app/services/crawler_v2_page_worker.py backend/app/services/crawler_page_fetch_ledger.py backend/test/test_crawler_v2_page_worker.py
git commit -m "feat(crawler): 实现 V2 页面 Worker"
```

### 任务 5：实现 Chunk Worker 和 `complete_current_chunk`

**文件：**
- 创建：`C:\StudyPrograms\AutoEmailSender\backend\app\services\crawler_v2_chunk_worker.py`
- 创建：`C:\StudyPrograms\AutoEmailSender\backend\test\test_crawler_v2_chunk_worker.py`
- 修改：`C:\StudyPrograms\AutoEmailSender\backend\app\services\crawler_chunk_runtime.py`

- [ ] **步骤 1：编写失败的 Chunk Worker 并发领取测试**

创建两个 Worker 同时领取同一个 pending chunk，断言只有一个成功。

```python
results = await asyncio.gather(
    claim_chunk_for_worker(session_factory, job_id=job_id, worker_id="chunk-a"),
    claim_chunk_for_worker(session_factory, job_id=job_id, worker_id="chunk-b"),
)
self.assertEqual(sum(1 for result in results if result is not None), 1)
```

- [ ] **步骤 2：编写 `complete_current_chunk` 原子保存测试**

提交一个候选和两个 discovered URL，其中一个 URL 非法。断言候选保存成功，合法 URL 入队，非法 URL 出现在 `rejected_discovered_urls`，chunk 状态 completed。

- [ ] **步骤 3：运行测试验证失败**

运行：`cd C:\StudyPrograms\AutoEmailSender\backend; uv run python -m unittest test.test_crawler_v2_chunk_worker -v`

预期：FAIL，模块或函数不存在。

- [ ] **步骤 4：抽出候选合并函数**

从 `crawler_chunk_runtime.py` 中抽出后端确定性候选保存逻辑，例如：

```python
async def save_or_merge_crawl_candidates(session: AsyncSession, *, job_id: int, source_chunk_id: str, candidates: Sequence[dict[str, object]]) -> CandidateSaveResult:
    result = CandidateSaveResult(created=0, merged=0, skipped=0)
    for payload in candidates:
        candidate = normalize_candidate_payload(payload, job_id=job_id, source_chunk_id=source_chunk_id)
        existing = await find_existing_candidate(session, job_id=job_id, candidate=candidate)
        if existing is None:
            session.add(candidate)
            result.created += 1
        else:
            merge_candidate_fields(existing, candidate)
            result.merged += 1
    return result
```

V1 原函数继续调用该共用函数，保证旧测试不破。

- [ ] **步骤 5：实现 Chunk Worker 领取**

`claim_chunk_for_worker` 使用状态条件更新，默认一次只领取一个 `pending` 或 lease 过期的 `processing` chunk。写入 `worker_id`、`claimed_at`、`lease_expires_at`、`attempt_count = attempt_count + 1`。

- [ ] **步骤 6：实现 `complete_current_chunk`**

实现流程：校验 chunk 归属和 lease；校验 candidates；保存/合并候选；为新建或更新后仍需补全的候选 upsert `crawl_candidate_enrichment_tasks`；校验 discovered_urls；合法 URL upsert 到 `crawl_page_tasks`；在同一事务中标记 chunk 状态。非法 URL 不回滚合法候选、补全任务和合法 URL。

- [ ] **步骤 7：运行 Chunk Worker 测试和 V1 chunk 测试**

运行：`cd C:\StudyPrograms\AutoEmailSender\backend; uv run python -m unittest test.test_crawler_v2_chunk_worker test.test_crawler_chunk_runtime -v`

预期：PASS。

- [ ] **步骤 8：Commit**

```powershell
git add backend/app/services/crawler_v2_chunk_worker.py backend/app/services/crawler_chunk_runtime.py backend/test/test_crawler_v2_chunk_worker.py backend/test/test_crawler_chunk_runtime.py
git commit -m "feat(crawler): 实现 V2 Chunk Worker"
```

### 任务 6：实现 Enrichment Worker 和补全队列

**文件：**
- 创建：`C:\StudyPrograms\AutoEmailSender\backend\app\services\crawler_v2_enrichment_worker.py`
- 创建：`C:\StudyPrograms\AutoEmailSender\backend\test\test_crawler_v2_enrichment_worker.py`

- [ ] **步骤 1：编写失败的补全队列测试**

测试无 `profile_url` 的候选标记 `skipped_no_profile`，字段完整的候选标记 `not_needed`，需要补全的候选被 lease 领取。

- [ ] **步骤 2：编写候选补全写回测试**

Fake enricher 返回 email、title、department、research_direction，断言候选字段更新，任务状态 `completed`，`enriched_at` 非空。

- [ ] **步骤 3：运行测试验证失败**

运行：`cd C:\StudyPrograms\AutoEmailSender\backend; uv run python -m unittest test.test_crawler_v2_enrichment_worker -v`

预期：FAIL，模块不存在。

- [ ] **步骤 4：实现补全任务同步**

实现：

```python
async def sync_candidate_enrichment_tasks(session: AsyncSession, *, job_id: int) -> EnrichmentSyncResult:
    result = EnrichmentSyncResult(created=0, skipped=0)
    candidates = list(await session.scalars(select(CrawlCandidate).where(CrawlCandidate.job_id == job_id)))
    for candidate in candidates:
        status = resolve_enrichment_task_status(candidate)
        task = await get_or_create_enrichment_task(session, job_id=job_id, candidate_id=candidate.id)
        if status in {"skipped_no_profile", "not_needed"}:
            task.status = status
            result.skipped += 1
        elif task.status not in {"completed", "processing"}:
            task.status = "pending"
            result.created += 1
    return result
```

规则：没有 `profile_url` 的候选写 `skipped_no_profile`；字段已完整写 `not_needed`；其余 pending。该同步函数用于修复历史或异常状态；正常新候选应在 `complete_current_chunk` 事务内同步创建补全任务，不能等到额外人工触发。

- [ ] **步骤 5：实现 Enrichment Worker**

`run_enrichment_worker_once` 领取一个任务，调用注入的 `enricher.enrich(candidate)`，写回候选字段；普通失败进入 `failed_retryable` 并增加 `attempt_count`，超过预算进入 `failed_terminal`。

- [ ] **步骤 6：运行 Enrichment Worker 测试验证通过**

运行：`cd C:\StudyPrograms\AutoEmailSender\backend; uv run python -m unittest test.test_crawler_v2_enrichment_worker -v`

预期：PASS。

- [ ] **步骤 7：Commit**

```powershell
git add backend/app/services/crawler_v2_enrichment_worker.py backend/test/test_crawler_v2_enrichment_worker.py
git commit -m "feat(crawler): 实现 V2 候选补全 Worker"
```

### 任务 7：实现 token 分 worker 记录

**文件：**
- 创建：`C:\StudyPrograms\AutoEmailSender\backend\app\services\crawler_v2_token_usage.py`
- 修改：`C:\StudyPrograms\AutoEmailSender\backend\app\services\crawler_v2_page_worker.py`
- 修改：`C:\StudyPrograms\AutoEmailSender\backend\app\services\crawler_v2_chunk_worker.py`
- 修改：`C:\StudyPrograms\AutoEmailSender\backend\app\services\crawler_v2_enrichment_worker.py`
- 测试：`C:\StudyPrograms\AutoEmailSender\backend\test\test_crawler_v2_scheduler.py`

- [ ] **步骤 1：编写失败的 token 聚合测试**

插入 page/chunk/enrichment 三条 token 记录，调用聚合函数，断言能按 `worker_kind` 返回 totals。

- [ ] **步骤 2：运行测试验证失败**

运行：`cd C:\StudyPrograms\AutoEmailSender\backend; uv run python -m unittest test.test_crawler_v2_scheduler -v`

预期：FAIL，`record_worker_token_usage` 或聚合函数不存在。

- [ ] **步骤 3：实现 token 记录服务**

```python
async def record_worker_token_usage(session: AsyncSession, *, job_id: int, worker_kind: str, work_item_id: str, usage: TokenUsagePayload) -> None:
    session.add(CrawlWorkerTokenUsage(
        job_id=job_id,
        worker_kind=worker_kind,
        work_item_id=work_item_id,
        model=usage.model,
        input_tokens=usage.input_tokens,
        output_tokens=usage.output_tokens,
        cached_tokens=usage.cached_tokens,
        total_tokens=usage.total_tokens,
        duration_ms=usage.duration_ms,
        status=usage.status,
        error_kind=usage.error_kind,
    ))
```

`TokenUsagePayload` 至少包含 model、input_tokens、output_tokens、cached_tokens、total_tokens、duration_ms、status、error_kind。

- [ ] **步骤 4：接入 Worker**

在 Worker 完成 LLM 或浏览器/抓取相关统计后调用记录函数。没有 LLM 调用的 direct page fetch 可记录 `input_tokens=0`，用于计算耗时和状态。

- [ ] **步骤 5：运行 token 测试验证通过**

运行：`cd C:\StudyPrograms\AutoEmailSender\backend; uv run python -m unittest test.test_crawler_v2_scheduler -v`

预期：PASS。

- [ ] **步骤 6：Commit**

```powershell
git add backend/app/services/crawler_v2_token_usage.py backend/app/services/crawler_v2_page_worker.py backend/app/services/crawler_v2_chunk_worker.py backend/app/services/crawler_v2_enrichment_worker.py backend/test/test_crawler_v2_scheduler.py
git commit -m "feat(crawler): 记录 V2 Worker token 用量"
```

### 任务 8：接入新建任务入口和 runtime manager

**文件：**
- 修改：`C:\StudyPrograms\AutoEmailSender\backend\app\api\crawl_jobs.py`
- 修改：`C:\StudyPrograms\AutoEmailSender\backend\app\schemas\crawl_job.py`
- 修改：`C:\StudyPrograms\AutoEmailSender\backend\app\services\crawl_job_runtime.py`
- 修改：`C:\StudyPrograms\AutoEmailSender\backend\app\services\runtime_manager.py`
- 创建：`C:\StudyPrograms\AutoEmailSender\backend\test\test_crawler_v2_runtime_routing.py`

- [ ] **步骤 1：编写失败的路由测试**

测试创建新 Crawl Job 后：`runtime_version == 'v2'`，入口 `start_url/start_urls` 都写入 `crawl_page_tasks`，runtime manager 调用 V2 scheduler 而不是 V1 agent。

- [ ] **步骤 2：运行测试验证失败**

运行：`cd C:\StudyPrograms\AutoEmailSender\backend; uv run python -m unittest test.test_crawler_v2_runtime_routing -v`

预期：FAIL，新入口未写 page task 或仍调用 V1。

- [ ] **步骤 3：创建任务时初始化 V2 队列**

在 crawl job 创建逻辑中：写 `runtime_version='v2'`，对 `start_url` 和 `start_urls` 逐个 normalize，upsert 到 `crawl_page_tasks`，`source_kind='entry'`。

- [ ] **步骤 4：实现 V2 调度执行入口**

新增或完善 `run_crawler_v2_once(session_factory, worker_id)`：先调用调度器领取工作项，再按领取结果执行对应 Worker。page task 调用 `run_page_worker_once`，chunk 调用 `run_chunk_worker_once`，enrichment 调用 `run_enrichment_worker_once`。如果某类 Worker 并发配额大于 1，使用 `asyncio.gather` 执行不同工作项；同一工作项仍只能被一个 Worker 领取。

- [ ] **步骤 5：runtime manager 切 V2 默认路径**

把 crawler worker 的自动入口改为调用 `run_crawler_v2_once`，不能只调用“领取但不执行”的调度函数。`crawl_job_runtime.py` 保留 V1 函数，但仅处理显式 `runtime_version='v1'` 的调试任务；遇到 V2 任务不启动长 Agent。

- [ ] **步骤 6：运行路由测试和旧 runtime 测试**

运行：`cd C:\StudyPrograms\AutoEmailSender\backend; uv run python -m unittest test.test_crawler_v2_runtime_routing test.test_crawl_job_runtime test.test_runtime_manager -v`

预期：PASS。

- [ ] **步骤 7：Commit**

```powershell
git add backend/app/api/crawl_jobs.py backend/app/schemas/crawl_job.py backend/app/services/crawl_job_runtime.py backend/app/services/runtime_manager.py backend/app/services/crawler_v2_scheduler.py backend/test/test_crawler_v2_runtime_routing.py
git commit -m "feat(crawler): 默认启用 V2 调度入口"
```

### 任务 9：补齐暂停、取消、失败预算和最终状态

**文件：**
- 修改：`C:\StudyPrograms\AutoEmailSender\backend\app\services\crawler_v2_scheduler.py`
- 修改：`C:\StudyPrograms\AutoEmailSender\backend\app\services\crawler_v2_page_worker.py`
- 修改：`C:\StudyPrograms\AutoEmailSender\backend\app\services\crawler_v2_chunk_worker.py`
- 修改：`C:\StudyPrograms\AutoEmailSender\backend\app\services\crawler_v2_enrichment_worker.py`
- 测试：`C:\StudyPrograms\AutoEmailSender\backend\test\test_crawler_v2_scheduler.py`

- [ ] **步骤 1：编写暂停/取消测试**

任务状态为 `paused` 或 `canceled` 时，scheduler 不领取新工作项；Worker 在 LLM/外部抓取/写库前检查状态并退出。

- [ ] **步骤 2：编写失败预算测试**

page 最大 3 次，chunk 最大 2 次，enrichment 最大 3 次。超过预算进入 terminal 状态，未超过进入 retryable。

- [ ] **步骤 3：运行测试验证失败**

运行：`cd C:\StudyPrograms\AutoEmailSender\backend; uv run python -m unittest test.test_crawler_v2_scheduler -v`

预期：FAIL，暂停/预算逻辑未实现完整。

- [ ] **步骤 4：实现状态安全点**

在 Worker 开始前、外部抓取前、LLM 调用前、写库前、完成后调用统一函数：

```python
async def ensure_job_active(session: AsyncSession, job_id: int) -> bool:
    job = await session.get(CrawlJob, job_id)
    return job is not None and job.status not in {"paused", "canceled"}
```

- [ ] **步骤 5：实现失败预算和最终状态**

可恢复失败更新为 `failed_retryable`，超过预算为 `failed_terminal`。调度器仅在没有 pending、processing 未过期和 retryable 工作项时结束任务；有 terminal failed 则 `partially_completed`，否则 `needs_review`。

- [ ] **步骤 6：运行测试验证通过**

运行：`cd C:\StudyPrograms\AutoEmailSender\backend; uv run python -m unittest test.test_crawler_v2_scheduler -v`

预期：PASS。

- [ ] **步骤 7：Commit**

```powershell
git add backend/app/services/crawler_v2_scheduler.py backend/app/services/crawler_v2_page_worker.py backend/app/services/crawler_v2_chunk_worker.py backend/app/services/crawler_v2_enrichment_worker.py backend/test/test_crawler_v2_scheduler.py
git commit -m "feat(crawler): 完善 V2 状态恢复与失败预算"
```

### 任务 10：端到端回归和规格覆盖验证

**文件：**
- 修改：`C:\StudyPrograms\AutoEmailSender\backend\app\services\crawler_v2_scheduler.py`
- 修改：`C:\StudyPrograms\AutoEmailSender\backend\app\services\crawler_v2_page_worker.py`
- 修改：`C:\StudyPrograms\AutoEmailSender\backend\app\services\crawler_v2_chunk_worker.py`
- 修改：`C:\StudyPrograms\AutoEmailSender\backend\app\services\crawler_v2_enrichment_worker.py`
- 修改：`C:\StudyPrograms\AutoEmailSender\backend\app\services\crawler_v2_token_usage.py`
- 修改：`C:\StudyPrograms\AutoEmailSender\backend\app\api\crawl_jobs.py`
- 测试：相关后端 crawler 测试

- [ ] **步骤 1：运行 V2 专项测试**

运行：`cd C:\StudyPrograms\AutoEmailSender\backend; uv run python -m unittest test.test_crawler_v2_url_utils test.test_crawler_v2_scheduler test.test_crawler_v2_page_worker test.test_crawler_v2_chunk_worker test.test_crawler_v2_enrichment_worker test.test_crawler_v2_runtime_routing -v`

预期：全部 PASS。

- [ ] **步骤 2：运行现有 crawler 回归测试**

运行：`cd C:\StudyPrograms\AutoEmailSender\backend; uv run python -m unittest test.test_crawl_job_runtime test.test_crawler_tools test.test_crawler_page_fetch_ledger test.test_crawler_chunk_runtime test.test_crawler_chunking test.test_crawl_jobs_api -v`

预期：全部 PASS。若失败，先确认是否 V2 改动导致；不要修 unrelated failure。

- [ ] **步骤 3：运行迁移验证**

运行：`cd C:\StudyPrograms\AutoEmailSender\backend; uv run alembic upgrade head`

预期：退出码 0。

- [ ] **步骤 4：运行后端完整测试**

运行：`cd C:\StudyPrograms\AutoEmailSender\backend; uv run python -m unittest discover test -v`

预期：全部 PASS，或仅存在实现前已知无关失败并在最终说明中列明。

- [ ] **步骤 5：检查规格覆盖**

逐项核对规格成功标准：候选数量不低于 V1、关键字段完整度不低于 V1、页面不重复抓取、chunk 不漏处理、新 URL 不丢失、prompt 不随历史线性增长、token 可按 worker 聚合、状态可正确结束。

- [ ] **步骤 6：最终 Commit**

```powershell
git status --short
git add backend/app backend/alembic backend/test
git commit -m "test(crawler): 覆盖 V2 调度器端到端流程"
```