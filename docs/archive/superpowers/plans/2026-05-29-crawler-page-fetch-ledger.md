# 智能抓取页面抓取账本实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 为智能抓取新增数据库级页面抓取账本，让 Agent 重启后仍能避免重复抓取明确无价值页面，同时保留临时失败的正常重试机会。

**架构：** 新增 `crawl_page_fetch_states` 表作为 `job_id + normalized_url` 的任务级状态账本，`crawl_pages` 继续作为详细日志。抓取工具在真实访问网络/browser 前先调用账本服务获取决策，真实抓取后再写回状态。内存缓存只作为同一 Agent 内的性能优化，不承担正确性。

**技术栈：** FastAPI 后端、SQLAlchemy ORM、Alembic 迁移、SQLite、Python unittest、现有 crawler tools / faculty crawler agent。

---

## 文件结构

- 创建：`backend/app/services/crawler_page_fetch_ledger.py`
  - 职责：URL 归一化、失败分类、抓取前决策、抓取后账本更新。
- 修改：`backend/app/models/crawl_job.py`
  - 职责：新增 `CrawlPageFetchState` ORM 模型和状态枚举。
- 修改：`backend/app/models/__init__.py`
  - 职责：导出新模型，确保迁移和测试导入一致。
- 创建：`backend/alembic/versions/a9c3e7d1f4b2_add_crawl_page_fetch_states.py`
  - 职责：新增账本表、唯一约束和索引。
- 修改：`backend/app/services/crawler_tools.py`
  - 职责：在 `crawl_page_with_crawl4ai`、`browser_investigate`、成功 chunk 创建路径附近接入账本决策/更新。
- 修改：`backend/app/agents/faculty_crawler_agent.py`
  - 职责：当工具返回账本跳过/复用结果时，给 Agent 明确下一步提示；如果服务层已封装好，尽量少改。
- 修改：`backend/app/services/crawler_chunk_runtime.py`
  - 职责：chunk 完成后将对应页面状态推进到 `processed`。
- 修改：`backend/test/test_crawler_page_fetch_ledger.py`
  - 职责：新增账本服务单元测试。
- 修改：`backend/test/test_crawler_tools.py`
  - 职责：验证真实抓取前置拦截和失败写回。
- 修改：`backend/test/test_faculty_crawler_agent.py`
  - 职责：验证 Agent 重启后 terminal failed URL 不再真实抓取。
- 修改：`backend/test/test_database_schema.py`
  - 职责：断言迁移后表结构包含账本表。

---

### 任务 1：新增账本模型和迁移

**文件：**
- 修改：`backend/app/models/crawl_job.py`
- 修改：`backend/app/models/__init__.py`
- 创建：`backend/alembic/versions/a9c3e7d1f4b2_add_crawl_page_fetch_states.py`
- 测试：`backend/test/test_database_schema.py`

- [ ] **步骤 1：编写失败的 schema 测试**

在 `backend/test/test_database_schema.py` 的 schema 断言测试中加入 `crawl_page_fetch_states` 表检查。若文件里已有统一的表/列断言，沿用现有方法添加以下断言：

```python
fetch_state_columns = self._table_columns(connection, "crawl_page_fetch_states")
self.assertTrue(
    {
        "id",
        "job_id",
        "normalized_url",
        "original_url",
        "status",
        "last_fetch_method",
        "terminal_reason",
        "transient_failure_count",
        "last_error_message",
        "last_page_id",
        "first_seen_at",
        "last_attempted_at",
        "updated_at",
    }.issubset(fetch_state_columns),
)
```

如果测试文件没有 `_table_columns` helper，则按现有 `PRAGMA table_info(...)` 模式获取列名，不新建通用框架。

- [ ] **步骤 2：运行测试验证失败**

运行：

```powershell
cd backend
uv run python -m unittest -v test.test_database_schema
```

预期：FAIL，错误说明 `crawl_page_fetch_states` 表不存在或缺少列。

- [ ] **步骤 3：新增 ORM 模型**

在 `backend/app/models/crawl_job.py` 中添加状态枚举：

```python
class CrawlPageFetchStatus(str, Enum):
    SUCCEEDED = "succeeded"
    CHUNKED = "chunked"
    PROCESSED = "processed"
    TRANSIENT_FAILED = "transient_failed"
    TERMINAL_FAILED = "terminal_failed"
```

在 `CrawlJob` 上添加关系：

```python
    page_fetch_states: Mapped[list["CrawlPageFetchState"]] = relationship(
        back_populates="job",
        cascade="all, delete-orphan",
    )
```

在 `CrawlPage` 类之后添加模型：

```python
class CrawlPageFetchState(Base):
    __tablename__ = "crawl_page_fetch_states"

    id: Mapped[int] = mapped_column(primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("crawl_jobs.id", ondelete="CASCADE"), index=True)
    normalized_url: Mapped[str] = mapped_column(String(1000), nullable=False)
    original_url: Mapped[str] = mapped_column(String(1000), nullable=False)
    status: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    last_fetch_method: Mapped[str | None] = mapped_column(String(64), nullable=True)
    terminal_reason: Mapped[str | None] = mapped_column(String(128), nullable=True)
    transient_failure_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    last_error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_page_id: Mapped[int | None] = mapped_column(ForeignKey("crawl_pages.id", ondelete="SET NULL"), nullable=True)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP"))
    last_attempted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
        onupdate=lambda: datetime.now(UTC),
    )

    job: Mapped["CrawlJob"] = relationship(back_populates="page_fetch_states")
    last_page: Mapped["CrawlPage | None"] = relationship()
```

同时在文件顶部 SQLAlchemy import 中加入 `UniqueConstraint`，并给模型添加表参数：

```python
    __table_args__ = (
        UniqueConstraint("job_id", "normalized_url", name="uq_crawl_page_fetch_states_job_url"),
    )
```

- [ ] **步骤 4：导出新模型**

在 `backend/app/models/__init__.py` 追加导入和 `__all__` 条目：

```python
from app.models.crawl_job import CrawlPageFetchState, CrawlPageFetchStatus
```

确保 `CrawlPageFetchState` 和 `CrawlPageFetchStatus` 出现在 `__all__` 中；如果该文件没有 `__all__`，只添加 import。

- [ ] **步骤 5：新增 Alembic 迁移**

创建新迁移文件 `backend/alembic/versions/a9c3e7d1f4b2_add_crawl_page_fetch_states.py`，revision 使用 `a9c3e7d1f4b2`，`down_revision` 使用当前 head `c4b8e2a9d6f3`。迁移内容：

```python
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "a9c3e7d1f4b2"
down_revision = "c4b8e2a9d6f3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "crawl_page_fetch_states",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("job_id", sa.Integer(), nullable=False),
        sa.Column("normalized_url", sa.String(length=1000), nullable=False),
        sa.Column("original_url", sa.String(length=1000), nullable=False),
        sa.Column("status", sa.String(length=64), nullable=False),
        sa.Column("last_fetch_method", sa.String(length=64), nullable=True),
        sa.Column("terminal_reason", sa.String(length=128), nullable=True),
        sa.Column("transient_failure_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("last_error_message", sa.Text(), nullable=True),
        sa.Column("last_page_id", sa.Integer(), nullable=True),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("last_attempted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.ForeignKeyConstraint(["job_id"], ["crawl_jobs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["last_page_id"], ["crawl_pages.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("job_id", "normalized_url", name="uq_crawl_page_fetch_states_job_url"),
    )
    op.create_index("ix_crawl_page_fetch_states_job_id", "crawl_page_fetch_states", ["job_id"])
    op.create_index("ix_crawl_page_fetch_states_status", "crawl_page_fetch_states", ["status"])


def downgrade() -> None:
    op.drop_index("ix_crawl_page_fetch_states_status", table_name="crawl_page_fetch_states")
    op.drop_index("ix_crawl_page_fetch_states_job_id", table_name="crawl_page_fetch_states")
    op.drop_table("crawl_page_fetch_states")
```

- [ ] **步骤 6：运行 schema 测试验证通过**

运行：

```powershell
cd backend
uv run python -m unittest -v test.test_database_schema
```

预期：PASS。

- [ ] **步骤 7：Commit**

```powershell
git add backend/app/models/crawl_job.py backend/app/models/__init__.py backend/alembic/versions/a9c3e7d1f4b2_add_crawl_page_fetch_states.py backend/test/test_database_schema.py
git commit -m "feat(crawler): add page fetch ledger schema"
```

---

### 任务 2：实现账本服务的纯逻辑

**文件：**
- 创建：`backend/app/services/crawler_page_fetch_ledger.py`
- 创建：`backend/test/test_crawler_page_fetch_ledger.py`

- [ ] **步骤 1：编写 URL 归一化和失败分类测试**

创建 `backend/test/test_crawler_page_fetch_ledger.py`，加入：

```python
from __future__ import annotations

import unittest

from app.services.crawler_page_fetch_ledger import (
    classify_page_fetch_failure,
    normalize_fetch_url,
)
from app.services.crawler_tools import PageSnapshot


class CrawlerPageFetchLedgerPureTests(unittest.TestCase):
    def test_normalize_fetch_url_lowercases_scheme_host_and_removes_fragment(self) -> None:
        self.assertEqual(
            normalize_fetch_url("HTTPS://CS.EXAMPLE.EDU/faculty?page=1#section"),
            "https://cs.example.edu/faculty?page=1",
        )

    def test_classifies_antibot_empty_response_as_terminal(self) -> None:
        snapshot = PageSnapshot(
            url="https://cs.example.edu/faculty",
            title=None,
            text="",
            html="",
            links=[],
            fetch_method="browser",
            status="failed",
            error_message="Blocked by anti-bot protection",
            suspicious_empty=True,
        )

        result = classify_page_fetch_failure(snapshot)

        self.assertEqual(result.status, "terminal_failed")
        self.assertEqual(result.reason, "anti_bot_or_empty_response")

    def test_classifies_wait_condition_failure_as_transient(self) -> None:
        snapshot = PageSnapshot(
            url="https://cs.example.edu/faculty",
            title=None,
            text="",
            html="",
            links=[],
            fetch_method="browser",
            status="failed",
            error_message="wait condition failed",
            suspicious_empty=False,
        )

        result = classify_page_fetch_failure(snapshot)

        self.assertEqual(result.status, "transient_failed")
        self.assertIsNone(result.reason)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **步骤 2：运行测试验证失败**

运行：

```powershell
cd backend
uv run python -m unittest -v test.test_crawler_page_fetch_ledger
```

预期：FAIL，模块或函数不存在。

- [ ] **步骤 3：实现纯逻辑**

创建 `backend/app/services/crawler_page_fetch_ledger.py`：

```python
from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlsplit, urlunsplit

from app.models.crawl_job import CrawlPageFetchStatus
from app.services.crawler_tools import PageSnapshot

TRANSIENT_FETCH_RETRY_LIMIT = 2

_TERMINAL_FAILURE_MARKERS = (
    "anti-bot",
    "blocked",
    "captcha",
    "cloudflare",
    "access denied",
    "security check",
)


@dataclass(frozen=True, slots=True)
class FetchFailureClassification:
    status: str
    reason: str | None = None


def normalize_fetch_url(url: str) -> str:
    parsed = urlsplit(url.strip())
    scheme = parsed.scheme.lower()
    netloc = parsed.netloc.lower()
    return urlunsplit((scheme, netloc, parsed.path or "/", parsed.query, ""))


def classify_page_fetch_failure(snapshot: PageSnapshot) -> FetchFailureClassification:
    if snapshot.status != "failed":
        raise ValueError("Only failed snapshots can be classified")
    error_message = (snapshot.error_message or "").lower()
    if snapshot.suspicious_empty or any(marker in error_message for marker in _TERMINAL_FAILURE_MARKERS):
        return FetchFailureClassification(
            status=CrawlPageFetchStatus.TERMINAL_FAILED.value,
            reason="anti_bot_or_empty_response",
        )
    return FetchFailureClassification(status=CrawlPageFetchStatus.TRANSIENT_FAILED.value)
```

- [ ] **步骤 4：运行纯逻辑测试验证通过**

运行：

```powershell
cd backend
uv run python -m unittest -v test.test_crawler_page_fetch_ledger
```

预期：PASS。

- [ ] **步骤 5：Commit**

```powershell
git add backend/app/services/crawler_page_fetch_ledger.py backend/test/test_crawler_page_fetch_ledger.py
git commit -m "feat(crawler): add page fetch ledger logic"
```

---

### 任务 3：实现账本数据库决策和更新

**文件：**
- 修改：`backend/app/services/crawler_page_fetch_ledger.py`
- 修改：`backend/test/test_crawler_page_fetch_ledger.py`

- [ ] **步骤 1：编写数据库决策测试**

在 `backend/test/test_crawler_page_fetch_ledger.py` 追加异步测试。沿用项目现有测试创建临时 SQLite session factory 的 helper；如果没有可复用 helper，在本测试文件内创建最小 helper：

```python
import asyncio
import tempfile
from pathlib import Path

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models.base import Base
from app.models.crawl_job import CrawlJob, CrawlPageFetchState
from app.services.crawler_page_fetch_ledger import (
    get_page_fetch_decision,
    mark_page_fetch_result,
)
```

加入测试：

```python
class CrawlerPageFetchLedgerDatabaseTests(unittest.TestCase):
    def test_terminal_failed_decision_skips_fetch_after_restart(self) -> None:
        async def run() -> str:
            session_factory = await _create_test_session_factory()
            async with session_factory() as session:
                job = CrawlJob(university="示例大学", school="计算机学院", start_url="https://cs.example.edu/faculty")
                session.add(job)
                await session.flush()
                session.add(
                    CrawlPageFetchState(
                        job_id=job.id,
                        normalized_url="https://cs.example.edu/faculty",
                        original_url="https://cs.example.edu/faculty",
                        status="terminal_failed",
                        last_fetch_method="browser",
                        terminal_reason="anti_bot_or_empty_response",
                        last_error_message="Blocked by anti-bot protection",
                    )
                )
                await session.commit()
                job_id = job.id

            decision = await get_page_fetch_decision(
                session_factory,
                job_id=job_id,
                url="https://cs.example.edu/faculty#ignored",
            )
            return decision.action

        self.assertEqual(asyncio.run(run()), "skip_terminal_failed")
```

再追加临时失败可重试测试：

```python
    def test_transient_failed_allows_retry_before_limit(self) -> None:
        async def run() -> str:
            session_factory = await _create_test_session_factory()
            async with session_factory() as session:
                job = CrawlJob(university="示例大学", school="计算机学院", start_url="https://cs.example.edu/faculty")
                session.add(job)
                await session.flush()
                session.add(
                    CrawlPageFetchState(
                        job_id=job.id,
                        normalized_url="https://cs.example.edu/faculty",
                        original_url="https://cs.example.edu/faculty",
                        status="transient_failed",
                        transient_failure_count=1,
                    )
                )
                await session.commit()
                job_id = job.id

            decision = await get_page_fetch_decision(session_factory, job_id=job_id, url="https://cs.example.edu/faculty")
            return decision.action

        self.assertEqual(asyncio.run(run()), "allow_retry")
```

- [ ] **步骤 2：运行测试验证失败**

运行：

```powershell
cd backend
uv run python -m unittest -v test.test_crawler_page_fetch_ledger
```

预期：FAIL，`get_page_fetch_decision` 或 `mark_page_fetch_result` 不存在。

- [ ] **步骤 3：实现数据库 API**

在 `crawler_page_fetch_ledger.py` 添加：

```python
from datetime import UTC, datetime
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.crawl_job import CrawlPageFetchState


@dataclass(frozen=True, slots=True)
class PageFetchDecision:
    action: str
    normalized_url: str
    state_id: int | None = None
    status: str | None = None
    message: str | None = None


async def get_page_fetch_decision(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    job_id: int,
    url: str,
) -> PageFetchDecision:
    normalized_url = normalize_fetch_url(url)
    async with session_factory() as session:
        state = await session.scalar(
            select(CrawlPageFetchState).where(
                CrawlPageFetchState.job_id == job_id,
                CrawlPageFetchState.normalized_url == normalized_url,
            )
        )
        if state is None:
            return PageFetchDecision(action="allow_first_fetch", normalized_url=normalized_url)
        if state.status == CrawlPageFetchStatus.TERMINAL_FAILED.value:
            return PageFetchDecision(
                action="skip_terminal_failed",
                normalized_url=normalized_url,
                state_id=state.id,
                status=state.status,
                message=state.last_error_message,
            )
        if state.status == CrawlPageFetchStatus.TRANSIENT_FAILED.value:
            if state.transient_failure_count >= TRANSIENT_FETCH_RETRY_LIMIT:
                state.status = CrawlPageFetchStatus.TERMINAL_FAILED.value
                state.terminal_reason = "transient_retry_exhausted"
                state.updated_at = datetime.now(UTC)
                await session.commit()
                return PageFetchDecision(
                    action="skip_terminal_failed",
                    normalized_url=normalized_url,
                    state_id=state.id,
                    status=state.status,
                    message=state.last_error_message,
                )
            return PageFetchDecision(action="allow_retry", normalized_url=normalized_url, state_id=state.id, status=state.status)
        if state.status == CrawlPageFetchStatus.CHUNKED.value:
            return PageFetchDecision(action="claim_chunk", normalized_url=normalized_url, state_id=state.id, status=state.status)
        if state.status == CrawlPageFetchStatus.PROCESSED.value:
            return PageFetchDecision(action="skip_processed", normalized_url=normalized_url, state_id=state.id, status=state.status)
        if state.status == CrawlPageFetchStatus.SUCCEEDED.value:
            return PageFetchDecision(action="reuse_success", normalized_url=normalized_url, state_id=state.id, status=state.status)
        return PageFetchDecision(action="allow_retry", normalized_url=normalized_url, state_id=state.id, status=state.status)
```

添加结果写回函数：

```python
async def mark_page_fetch_result(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    job_id: int,
    original_url: str,
    snapshot: PageSnapshot,
    generated_chunks: bool = False,
) -> None:
    normalized_url = normalize_fetch_url(snapshot.url or original_url)
    now = datetime.now(UTC)
    async with session_factory() as session:
        state = await session.scalar(
            select(CrawlPageFetchState).where(
                CrawlPageFetchState.job_id == job_id,
                CrawlPageFetchState.normalized_url == normalized_url,
            )
        )
        if state is None:
            state = CrawlPageFetchState(
                job_id=job_id,
                normalized_url=normalized_url,
                original_url=original_url,
                status=CrawlPageFetchStatus.SUCCEEDED.value,
            )
            session.add(state)
        state.original_url = original_url
        state.last_fetch_method = snapshot.fetch_method
        state.last_page_id = snapshot.page_id
        state.last_attempted_at = now
        state.updated_at = now
        state.last_error_message = snapshot.error_message
        if snapshot.status == "succeeded":
            state.status = CrawlPageFetchStatus.CHUNKED.value if generated_chunks else CrawlPageFetchStatus.SUCCEEDED.value
            state.terminal_reason = None
        else:
            classification = classify_page_fetch_failure(snapshot)
            state.status = classification.status
            state.terminal_reason = classification.reason
            if classification.status == CrawlPageFetchStatus.TRANSIENT_FAILED.value:
                state.transient_failure_count += 1
        await session.commit()
```

- [ ] **步骤 4：运行账本测试验证通过**

运行：

```powershell
cd backend
uv run python -m unittest -v test.test_crawler_page_fetch_ledger
```

预期：PASS。

- [ ] **步骤 5：Commit**

```powershell
git add backend/app/services/crawler_page_fetch_ledger.py backend/test/test_crawler_page_fetch_ledger.py
git commit -m "feat(crawler): persist page fetch decisions"
```

---

### 任务 4：接入抓取工具前置决策

**文件：**
- 修改：`backend/app/services/crawler_tools.py`
- 修改：`backend/test/test_crawler_tools.py`

- [ ] **步骤 1：编写 terminal failed 不触发真实抓取测试**

在 `backend/test/test_crawler_tools.py` 新增测试，使用 `CrawlToolContext` 和临时数据库 session factory：

```python
async def seed_terminal_failed_state(session_factory: async_sessionmaker[AsyncSession], job_id: int) -> None:
    async with session_factory() as session:
        session.add(
            CrawlPageFetchState(
                job_id=job_id,
                normalized_url="https://cs.example.edu/faculty",
                original_url="https://cs.example.edu/faculty",
                status="terminal_failed",
                last_fetch_method="browser",
                terminal_reason="anti_bot_or_empty_response",
                last_error_message="Blocked by anti-bot protection",
            )
        )
        await session.commit()
```

测试主体：

```python
def test_crawl_page_skips_terminal_failed_url_without_network(self) -> None:
    async def run() -> tuple[str, int]:
        session_factory, job_id = await create_crawl_job_for_test()
        await seed_terminal_failed_state(session_factory, job_id)
        ctx = CrawlToolContext(
            job_id=job_id,
            start_url="https://cs.example.edu/faculty",
            university="示例大学",
            school="计算机学院",
            session_factory=session_factory,
        )
        with patch("app.services.crawler_tools.crawl_page_with_http", AsyncMock()) as http_mock:
            snapshot = await crawl_page_with_crawl4ai(ctx, "https://cs.example.edu/faculty")
        return snapshot.status, http_mock.await_count

    status, await_count = asyncio.run(run())
    self.assertEqual(status, "failed")
    self.assertEqual(await_count, 0)
```

如果 `test_crawler_tools.py` 现有 helper 名称不同，复用现有 helper；不要引入 pytest。

- [ ] **步骤 2：运行测试验证失败**

运行：

```powershell
cd backend
uv run python -m unittest -v test.test_crawler_tools
```

预期：FAIL，当前工具不会查账本，会真实尝试抓取或 mock 调用次数不为 0。

- [ ] **步骤 3：在工具层接入决策**

在 `backend/app/services/crawler_tools.py` import：

```python
from app.services.crawler_page_fetch_ledger import (
    get_page_fetch_decision,
    mark_page_fetch_result,
)
```

在 `crawl_page_with_crawl4ai` 中，URL 安全校验和内存缓存检查之后、真实 HTTP/browser 抓取之前加入：

```python
decision = await get_page_fetch_decision(ctx.session_factory, job_id=ctx.job_id, url=absolute_url)
if decision.action == "skip_terminal_failed":
    return _failed_snapshot(
        url=absolute_url,
        fetch_method="ledger",
        error_message=f"该页面此前已明确抓取失败，已跳过：{decision.message or 'terminal_failed'}",
    )
if decision.action == "skip_processed":
    return _failed_snapshot(
        url=absolute_url,
        fetch_method="ledger",
        error_message="该页面已处理完成，已跳过重复抓取",
    )
if decision.action == "claim_chunk":
    return PageSnapshot(
        url=absolute_url,
        title=None,
        text="",
        html="",
        links=[],
        fetch_method="ledger",
        status="failed",
        error_message="该页面已有待处理片段，请领取 chunk，不要重复抓取",
    )
```

在 `browser_investigate` 中同样加入账本决策，但保留 `crawl_job_has_pending_work` 和安全校验在前。

- [ ] **步骤 4：抓取后写回账本**

在 `crawl_page_with_crawl4ai` 的返回前，对真实抓取结果调用：

```python
await mark_page_fetch_result(
    ctx.session_factory,
    job_id=ctx.job_id,
    original_url=absolute_url,
    snapshot=processed_snapshot,
    generated_chunks=False,
)
```

在 HTTP 失败后 browser fallback 的 `processed_browser_snapshot` 返回前也调用同一函数。

在 `browser_investigate` 的 `record_page_snapshot` 后、返回前调用 `mark_page_fetch_result(..., generated_chunks=False)`。

注意：不要对 ledger 自己返回的跳过 snapshot 再写入新抓取日志。

- [ ] **步骤 5：运行工具测试验证通过**

运行：

```powershell
cd backend
uv run python -m unittest -v test.test_crawler_tools
```

预期：PASS。

- [ ] **步骤 6：Commit**

```powershell
git add backend/app/services/crawler_tools.py backend/test/test_crawler_tools.py
git commit -m "feat(crawler): skip terminal failed page fetches"
```

---

### 任务 5：chunk 状态推进到账本

**文件：**
- 修改：`backend/app/agents/faculty_crawler_agent.py`
- 修改：`backend/app/services/crawler_chunk_runtime.py`
- 修改：`backend/app/services/crawler_page_fetch_ledger.py`
- 修改：`backend/test/test_faculty_crawler_agent.py`
- 修改：`backend/test/test_crawler_chunk_runtime.py`

- [ ] **步骤 1：编写生成 chunk 后状态为 chunked 的测试**

在 `backend/test/test_faculty_crawler_agent.py` 现有 `test_browser_investigate_chunks_successful_page_snapshot` 附近增加断言：mock `mark_page_fetch_result`，验证 `generated_chunks=True`：

```python
patch("app.agents.faculty_crawler_agent.mark_page_fetch_result", AsyncMock()) as mark_fetch_mock,
```

在调用后加入：

```python
mark_fetch_mock.assert_awaited_once()
self.assertTrue(mark_fetch_mock.await_args.kwargs["generated_chunks"])
```

- [ ] **步骤 2：实现 Agent chunk 生成后的账本更新**

在 `backend/app/agents/faculty_crawler_agent.py` import：

```python
from app.services.crawler_page_fetch_ledger import mark_page_fetch_result
```

在 `crawl_page` 和 `investigate_with_browser` 中，`created_chunks > 0` 且返回 chunked 响应前调用：

```python
await mark_page_fetch_result(
    ctx.session_factory,
    job_id=ctx.job_id,
    original_url=absolute_url,
    snapshot=snapshot,
    generated_chunks=True,
)
```

如果任务 4 已在工具层写入 `succeeded`，这里会把状态推进为 `chunked`。

- [ ] **步骤 3：编写 chunk 完成后状态为 processed 的测试**

在 `backend/test/test_crawler_chunk_runtime.py` 的 chunk 完成测试中，创建 `CrawlPageFetchState(status="chunked")`，提交 `submit_page_chunk_candidates_runtime(... chunk_status="completed")` 后查询状态：

```python
state = await session.scalar(
    select(CrawlPageFetchState).where(CrawlPageFetchState.job_id == job.id)
)
self.assertEqual(state.status, "processed")
```

- [ ] **步骤 4：实现 processed 推进函数**

在 `crawler_page_fetch_ledger.py` 添加：

```python
async def mark_page_chunks_processed(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    job_id: int,
    source_url: str,
) -> None:
    normalized_url = normalize_fetch_url(source_url)
    async with session_factory() as session:
        state = await session.scalar(
            select(CrawlPageFetchState).where(
                CrawlPageFetchState.job_id == job_id,
                CrawlPageFetchState.normalized_url == normalized_url,
            )
        )
        if state is not None:
            state.status = CrawlPageFetchStatus.PROCESSED.value
            state.updated_at = datetime.now(UTC)
            await session.commit()
```

- [ ] **步骤 5：在 chunk runtime 调用 processed 推进**

在 `backend/app/services/crawler_chunk_runtime.py` import `mark_page_chunks_processed`。当 `chunk_status` 是 `completed` 或 `no_candidates`，且该 source_url 没有未完成 chunk 时调用：

```python
await mark_page_chunks_processed(
    ctx.session_factory,
    job_id=ctx.job_id,
    source_url=chunk.source_url,
)
```

必须在确认没有 pending/splitting chunk 后调用，避免一个 chunk 完成就误判整页完成。

- [ ] **步骤 6：运行相关测试验证通过**

运行：

```powershell
cd backend
uv run python -m unittest -v test.test_faculty_crawler_agent test.test_crawler_chunk_runtime
```

预期：PASS。

- [ ] **步骤 7：Commit**

```powershell
git add backend/app/agents/faculty_crawler_agent.py backend/app/services/crawler_chunk_runtime.py backend/app/services/crawler_page_fetch_ledger.py backend/test/test_faculty_crawler_agent.py backend/test/test_crawler_chunk_runtime.py
git commit -m "feat(crawler): sync page ledger with chunk lifecycle"
```

---

### 任务 6：覆盖 Agent 重启场景

**文件：**
- 修改：`backend/test/test_crawl_job_runtime.py`
- 修改：`backend/test/test_faculty_crawler_agent.py`

- [ ] **步骤 1：编写 Agent 重启后不重复抓 terminal failed 的集成测试**

在 `backend/test/test_crawl_job_runtime.py` 新增或扩展运行时测试：

```python
def test_restarted_agent_skips_terminal_failed_start_url(self) -> None:
    async def run() -> int:
        session_factory, job_id = await create_running_crawl_job_for_test(
            start_url="https://cs.example.edu/faculty",
        )
        async with session_factory() as session:
            session.add(
                CrawlPageFetchState(
                    job_id=job_id,
                    normalized_url="https://cs.example.edu/faculty",
                    original_url="https://cs.example.edu/faculty",
                    status="terminal_failed",
                    last_fetch_method="browser",
                    terminal_reason="anti_bot_or_empty_response",
                    last_error_message="Blocked by anti-bot protection",
                )
            )
            await session.commit()

        with patch("app.services.crawler_tools._crawl_page_with_crawl4ai_browser", AsyncMock()) as browser_mock:
            ctx = CrawlToolContext(
                job_id=job_id,
                start_url="https://cs.example.edu/faculty",
                university="示例大学",
                school="计算机学院",
                session_factory=session_factory,
            )
            await crawl_page_with_crawl4ai(ctx, "https://cs.example.edu/faculty")
            await crawl_page_with_crawl4ai(ctx, "https://cs.example.edu/faculty")
            return browser_mock.await_count

    self.assertEqual(asyncio.run(run()), 0)
```

如果现有 runtime 测试已有更合适 helper，复用 helper；测试重点是“新 ctx 也查数据库”。

- [ ] **步骤 2：运行测试验证通过**

运行：

```powershell
cd backend
uv run python -m unittest -v test.test_crawl_job_runtime test.test_faculty_crawler_agent
```

预期：PASS。

- [ ] **步骤 3：补充 trace/debug 事件测试**

如果现有 `test_crawl_job_events.py` 能读取页面事件，新增断言：ledger 跳过返回的页面事件或工具结果包含“此前已明确抓取失败，已跳过”。如果事件层不展示工具结果，本步骤改为在 `test_crawler_tools.py` 断言返回 `error_message` 包含该文案。

```python
self.assertIn("此前已明确抓取失败", snapshot.error_message)
```

- [ ] **步骤 4：Commit**

```powershell
git add backend/test/test_crawl_job_runtime.py backend/test/test_faculty_crawler_agent.py backend/test/test_crawler_tools.py
git commit -m "test(crawler): cover ledger across agent restarts"
```

---

### 任务 7：最终验证和清理

**文件：**
- 检查：`backend/app/services/crawler_tools.py`
- 检查：`backend/app/services/crawler_page_fetch_ledger.py`
- 检查：`backend/app/agents/faculty_crawler_agent.py`
- 检查：`backend/app/services/crawler_chunk_runtime.py`

- [ ] **步骤 1：运行后端聚焦测试**

运行：

```powershell
cd backend
uv run python -m unittest -v test.test_crawler_page_fetch_ledger test.test_crawler_tools test.test_faculty_crawler_agent test.test_crawler_chunk_runtime test.test_crawl_job_runtime test.test_database_schema
```

预期：PASS。

- [ ] **步骤 2：运行后端全量 unittest**

运行：

```powershell
cd backend
uv run python -m unittest discover test
```

预期：PASS。若失败，先判断是否与本次改动有关；无关失败不要顺手修，记录在最终说明。

- [ ] **步骤 3：检查迁移链**

运行：

```powershell
cd backend
uv run alembic heads
uv run alembic upgrade head
```

预期：只有一个 head，upgrade 成功。

- [ ] **步骤 4：检查 diff 是否聚焦**

运行：

```powershell
git diff --stat
git diff -- backend/app/services/crawler_tools.py backend/app/services/crawler_page_fetch_ledger.py backend/app/agents/faculty_crawler_agent.py backend/app/services/crawler_chunk_runtime.py
```

预期：只包含页面账本相关变更，没有无关重构。

- [ ] **步骤 5：最终 Commit**

如果前面任务已逐步 commit，本步骤只提交遗漏文件：

```powershell
git status --short
git add backend/app/services/crawler_tools.py backend/app/services/crawler_page_fetch_ledger.py backend/app/agents/faculty_crawler_agent.py backend/app/services/crawler_chunk_runtime.py backend/test/test_crawler_page_fetch_ledger.py backend/test/test_crawler_tools.py backend/test/test_faculty_crawler_agent.py backend/test/test_crawler_chunk_runtime.py backend/test/test_crawl_job_runtime.py backend/test/test_database_schema.py
git commit -m "test(crawler): verify page fetch ledger"
```

如果没有遗漏文件，不创建空 commit。