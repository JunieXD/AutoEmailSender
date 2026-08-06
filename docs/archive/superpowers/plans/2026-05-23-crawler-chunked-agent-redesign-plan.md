# 抓取 Agent Chunk 化重构实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 将智能抓取从「模型反复处理完整页面并自行记忆已保存候选」重构为「后端维护列表页 chunk 状态、模型单 chunk 抽取、保存层幂等合并」的可控流程。

**架构：** 后端新增 chunk 切片与状态表，列表页抓取后生成链接增强文本 chunk；Agent 通过 `claim_next_page_chunk` 领取未处理 chunk，通过 `submit_chunk_candidates` 提交最多 10 个候选。保存层负责候选准入、`email/profile_url` 幂等、字段级合并和重复循环反馈。

**技术栈：** Python 3、FastAPI 后端、SQLAlchemy ORM、Alembic、unittest、LangChain / DeepAgents 工具调用、uv。

---

## 参考规格

- `docs/superpowers/specs/2026-05-23-crawler-chunked-agent-redesign-design.md`

## 文件结构

### 新增文件

- `backend/app/models/crawl_chunk.py`：定义 `CrawlPageChunk` ORM 模型和 chunk 状态枚举。
- `backend/app/services/crawler_chunking.py`：通用 HTML 清洗、链接增强文本生成、chunk 切片、chunk hash 和 token 估算。
- `backend/app/services/crawler_chunk_runtime.py`：chunk 持久化、领取、提交、状态推进、自动拆分、重复循环统计。
- `backend/alembic/versions/a1b2c3d4e5f6_add_crawl_page_chunks.py`：新增 `crawl_page_chunks` 表和候选来源字段迁移。
- `backend/test/test_crawler_chunking.py`：切片器单元测试。
- `backend/test/test_crawler_chunk_runtime.py`：chunk 状态机、提交、拆分和恢复测试。

### 修改文件

- `backend/app/models/__init__.py`：导出 `CrawlPageChunk` 和状态枚举。
- `backend/app/models/crawl_job.py`：为 `CrawlCandidate` 增加来源、身份键和合并元数据字段；必要时增加 relationship。
- `backend/app/services/crawler_tools.py`：增强保存准入、去重、合并、返回结果结构；扩展 `CrawlToolContext` 的重复循环状态。
- `backend/app/agents/faculty_crawler_agent.py`：新增受控工具 `claim_next_page_chunk`、`submit_chunk_candidates`；调整 prompt 和 tool allowlist。
- `backend/app/services/crawl_job_runtime.py`：在页面抓取成功后生成列表页 chunk，并在任务运行循环中处理 chunk 队列完成条件。
- `backend/test/test_crawler_tools.py`：补充保存层准入、去重、合并测试。
- `backend/test/test_faculty_crawler_agent.py`：补充工具 allowlist、chunk 提交校验和重复内容保护测试。
- `backend/test/test_crawl_job_models.py`：补充 chunk 状态枚举稳定性测试。
- `docs/database_table_design.md`：记录 `crawl_page_chunks` 表。

### 命令约定

所有命令从仓库根目录运行，涉及中文输出时使用 UTF-8：

```powershell
[Console]::InputEncoding=[System.Text.Encoding]::UTF8
[Console]::OutputEncoding=[System.Text.Encoding]::UTF8
$OutputEncoding=[System.Text.Encoding]::UTF8
```

后端测试命令：

```powershell
cd backend
uv run python -m unittest test.test_crawler_tools
```

---

## 任务 1：保存层准入与 `profile_url` 幂等

**文件：**
- 修改：`backend/app/services/crawler_tools.py`
- 测试：`backend/test/test_crawler_tools.py`

- [ ] **步骤 1：编写失败测试：无邮箱无详情页拒绝保存**

在 `backend/test/test_crawler_tools.py` 的数据库保存测试类中新增测试。如果没有 `_create_sqlite_session_factory`，在文件底部新增 helper。

```python
async def _create_sqlite_session_factory() -> async_sessionmaker[AsyncSession]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    return async_sessionmaker(engine, expire_on_commit=False)
```

```python
    def test_save_candidate_batch_rejects_candidate_without_email_or_profile_url(self) -> None:
        async def run() -> None:
            session_factory = await _create_sqlite_session_factory()
            async with session_factory() as session:
                job = CrawlJob(
                    university="示例大学",
                    school="计算机学院",
                    start_url="https://cs.example.edu/faculty",
                    status=CrawlJobStatus.RUNNING.value,
                )
                session.add(job)
                await session.commit()
                await session.refresh(job)

            ctx = CrawlToolContext(
                job_id=job.id,
                start_url="https://cs.example.edu/faculty",
                university="示例大学",
                school="计算机学院",
                session_factory=session_factory,
            )

            result = await save_candidate_batch(
                ctx,
                [ProfessorCandidatePayload(name="张三", email=None, profile_url=None, source_url="https://cs.example.edu/faculty")],
            )

            self.assertEqual(result["saved_count"], 0)
            self.assertEqual(result["rejected_count"], 1)
            self.assertIn("缺少邮箱和详情页链接", result["rejected_items"][0]["reason"])
            self.assertEqual(await count_saved_candidates(ctx), 0)

        asyncio.run(run())
```

- [ ] **步骤 2：运行测试验证失败**

```powershell
cd backend
uv run python -m unittest test.test_crawler_tools.CrawlerToolTests.test_save_candidate_batch_rejects_candidate_without_email_or_profile_url
```

预期：FAIL，错误包含 `KeyError: 'rejected_count'` 或 `saved_count` 为 1。

- [ ] **步骤 3：实现保存准入结果结构**

在 `backend/app/services/crawler_tools.py` 中扩展 `CandidateBatchSaveResult`，加入 `merged_count`、`skipped_duplicate_count`、`rejected_count`、`rejected_items`、`next_instruction`。

新增 helper：

```python
def _candidate_missing_contact_path(payload: dict[str, Any]) -> bool:
    email = str(payload.get("email") or "").strip()
    profile_url = str(payload.get("profile_url") or "").strip()
    return not email and not profile_url
```

在 `save_candidate_batch` 调用 `_save_normalized_candidate_payloads` 前过滤：

```python
    accepted_payloads: list[dict[str, Any]] = []
    rejected_items: list[CandidateBatchFailure] = []
    for index, payload in enumerate(payloads):
        if _candidate_missing_contact_path(payload):
            rejected_items.append({"index": index, "name": payload.get("name"), "reason": "缺少邮箱和详情页链接，无法用于联系或后续补全"})
            continue
        accepted_payloads.append(payload)
```

- [ ] **步骤 4：运行测试验证通过**

```powershell
cd backend
uv run python -m unittest test.test_crawler_tools.CrawlerToolTests.test_save_candidate_batch_rejects_candidate_without_email_or_profile_url
```

预期：PASS。

- [ ] **步骤 5：编写失败测试：同一 `profile_url` 不重复新增**

```python
    def test_save_candidate_batch_skips_duplicate_profile_url_without_email(self) -> None:
        async def run() -> None:
            session_factory = await _create_sqlite_session_factory()
            async with session_factory() as session:
                job = CrawlJob(university="示例大学", school="计算机学院", start_url="https://cs.example.edu/faculty", status=CrawlJobStatus.RUNNING.value)
                session.add(job)
                await session.commit()
                await session.refresh(job)

            ctx = CrawlToolContext(job_id=job.id, start_url="https://cs.example.edu/faculty", university="示例大学", school="计算机学院", session_factory=session_factory)
            first = ProfessorCandidatePayload(name="张三", profile_url="https://cs.example.edu/teachers/zhang#bio", source_url="https://cs.example.edu/faculty")
            second = ProfessorCandidatePayload(name="张三", profile_url="https://cs.example.edu/teachers/zhang", source_url="https://cs.example.edu/faculty?page=1")

            first_result = await save_candidate_batch(ctx, [first])
            second_result = await save_candidate_batch(ctx, [second])

            self.assertEqual(first_result["saved_count"], 1)
            self.assertEqual(second_result["saved_count"], 0)
            self.assertEqual(second_result["skipped_duplicate_count"], 1)
            self.assertEqual(await count_saved_candidates(ctx), 1)

        asyncio.run(run())
```

- [ ] **步骤 6：运行测试验证失败**

```powershell
cd backend
uv run python -m unittest test.test_crawler_tools.CrawlerToolTests.test_save_candidate_batch_skips_duplicate_profile_url_without_email
```

预期：FAIL，`count_saved_candidates(ctx)` 为 2 或缺少 `skipped_duplicate_count`。

- [ ] **步骤 7：实现 `profile_url` 归一化去重**

在 `crawler_tools.py` 新增：

```python
def normalize_candidate_profile_url(value: object, *, base_url: str | None = None) -> str | None:
    if value is None:
        return None
    raw = str(value).strip()
    if not raw:
        return None
    absolute = urljoin(base_url or "", raw) if base_url else raw
    parsed = urlparse(absolute)
    normalized = parsed._replace(fragment="").geturl().rstrip("/")
    return normalized or None
```

新增加载函数：

```python
async def _load_existing_candidate_profile_urls(session: AsyncSession, job_id: int) -> set[str]:
    result = await session.scalars(select(CrawlCandidate.profile_url).where(CrawlCandidate.job_id == job_id, CrawlCandidate.profile_url.is_not(None)))
    return {normalized for profile_url in result if (normalized := normalize_candidate_profile_url(profile_url))}
```

将 `_save_normalized_candidate_payloads` 改为返回 `CandidatePersistenceResult(saved, merged_count, skipped_duplicate_count)`，保存前同时检查 `seen_emails` 和 `seen_profile_urls`。

- [ ] **步骤 8：运行相关保存层测试**

```powershell
cd backend
uv run python -m unittest test.test_crawler_tools.CrawlerToolTests.test_save_candidate_batch_rejects_candidate_without_email_or_profile_url test.test_crawler_tools.CrawlerToolTests.test_save_candidate_batch_skips_duplicate_profile_url_without_email
```

预期：PASS。

- [ ] **步骤 9：Commit**

```powershell
git add backend/app/services/crawler_tools.py backend/test/test_crawler_tools.py
git commit -m "fix(crawler): make candidate saves idempotent"
```

---

## 任务 2：候选字段级合并

**文件：**
- 修改：`backend/app/services/crawler_tools.py`
- 修改：`backend/app/models/crawl_job.py`
- 测试：`backend/test/test_crawler_tools.py`

- [ ] **步骤 1：编写失败测试：重复候选补充空字段时合并**

在 `backend/test/test_crawler_tools.py` 增加 `from sqlalchemy import select`，然后新增测试：

```python
    def test_save_candidate_batch_merges_more_complete_duplicate_profile(self) -> None:
        async def run() -> None:
            session_factory = await _create_sqlite_session_factory()
            async with session_factory() as session:
                job = CrawlJob(university="示例大学", school="计算机学院", start_url="https://cs.example.edu/faculty", status=CrawlJobStatus.RUNNING.value)
                session.add(job)
                await session.commit()
                await session.refresh(job)

            ctx = CrawlToolContext(job_id=job.id, start_url="https://cs.example.edu/faculty", university="示例大学", school="计算机学院", session_factory=session_factory)
            await save_candidate_batch(ctx, [ProfessorCandidatePayload(name="张三", profile_url="https://cs.example.edu/teachers/zhang", source_url="https://cs.example.edu/faculty")])
            result = await save_candidate_batch(
                ctx,
                [ProfessorCandidatePayload(name="张三", profile_url="https://cs.example.edu/teachers/zhang", source_url="https://cs.example.edu/faculty#chunk2", research_direction="数据库与大数据管理", evidence={"summary": "后续 chunk 提供研究方向"})],
            )

            self.assertEqual(result["saved_count"], 0)
            self.assertEqual(result["merged_count"], 1)
            async with session_factory() as session:
                row = (await session.scalars(select(CrawlCandidate))).one()
                self.assertEqual(row.research_direction, "数据库与大数据管理")
                self.assertIn("后续 chunk", str(row.evidence))

        asyncio.run(run())
```

- [ ] **步骤 2：运行测试验证失败**

```powershell
cd backend
uv run python -m unittest test.test_crawler_tools.CrawlerToolTests.test_save_candidate_batch_merges_more_complete_duplicate_profile
```

预期：FAIL，`merged_count` 为 0 或字段未更新。

- [ ] **步骤 3：实现合并 helper**

在 `crawler_tools.py` 新增：

```python
_MERGEABLE_TEXT_FIELDS = ("email", "title", "university", "school", "department", "research_direction", "profile_url", "source_url")


def _merge_json_dict(current: object, incoming: object) -> dict[str, object]:
    merged: dict[str, object] = {}
    if isinstance(current, dict):
        merged.update(current)
    if isinstance(incoming, dict):
        merged.update(incoming)
    return merged


def _merge_candidate_payload(existing: CrawlCandidate, payload: dict[str, Any]) -> bool:
    changed = False
    for field_name in _MERGEABLE_TEXT_FIELDS:
        new_value = payload.get(field_name)
        if new_value in (None, ""):
            continue
        old_value = getattr(existing, field_name)
        if old_value in (None, ""):
            setattr(existing, field_name, new_value)
            changed = True
    if payload.get("recent_papers") and not existing.recent_papers:
        existing.recent_papers = payload["recent_papers"]
        changed = True
    if payload.get("field_confidence"):
        existing.field_confidence = _merge_json_dict(existing.field_confidence, payload["field_confidence"])
        changed = True
    if payload.get("evidence"):
        existing.evidence = _merge_json_dict(existing.evidence, payload["evidence"])
        changed = True
    return changed
```

- [ ] **步骤 4：查询重复行并合并**

新增：

```python
async def _find_existing_candidate_for_payload(session: AsyncSession, *, job_id: int, email: str | None, profile_url: str | None) -> CrawlCandidate | None:
    if email:
        row = await session.scalar(select(CrawlCandidate).where(CrawlCandidate.job_id == job_id, func.lower(CrawlCandidate.email) == email.lower()))
        if row is not None:
            return row
    if profile_url:
        row = await session.scalar(select(CrawlCandidate).where(CrawlCandidate.job_id == job_id, CrawlCandidate.profile_url == profile_url))
        if row is not None:
            return row
    return None
```

在保存循环中，发现重复时调用 `_merge_candidate_payload`；有变化累加 `merged_count`，无变化累加 `skipped_duplicate_count`。

- [ ] **步骤 5：运行合并测试**

```powershell
cd backend
uv run python -m unittest test.test_crawler_tools.CrawlerToolTests.test_save_candidate_batch_merges_more_complete_duplicate_profile
```

预期：PASS。

- [ ] **步骤 6：补充低质量字段不覆盖测试**

```python
    def test_save_candidate_batch_does_not_replace_existing_email_with_empty_value(self) -> None:
        async def run() -> None:
            session_factory = await _create_sqlite_session_factory()
            async with session_factory() as session:
                job = CrawlJob(university="示例大学", school="计算机学院", start_url="https://cs.example.edu/faculty", status=CrawlJobStatus.RUNNING.value)
                session.add(job)
                await session.commit()
                await session.refresh(job)

            ctx = CrawlToolContext(job_id=job.id, start_url="https://cs.example.edu/faculty", university="示例大学", school="计算机学院", session_factory=session_factory)
            await save_candidate_batch(ctx, [ProfessorCandidatePayload(name="李四", email="li@example.edu")])
            await save_candidate_batch(ctx, [ProfessorCandidatePayload(name="李四", email=None, profile_url="https://cs.example.edu/li")])

            async with session_factory() as session:
                row = (await session.scalars(select(CrawlCandidate).where(CrawlCandidate.name == "李四"))).one()
                self.assertEqual(row.email, "li@example.edu")

        asyncio.run(run())
```

- [ ] **步骤 7：运行保存层全量测试**

```powershell
cd backend
uv run python -m unittest test.test_crawler_tools
```

预期：PASS。

- [ ] **步骤 8：Commit**

```powershell
git add backend/app/services/crawler_tools.py backend/app/models/crawl_job.py backend/test/test_crawler_tools.py
git commit -m "feat(crawler): merge duplicate candidate evidence"
```

---

## 任务 3：新增 chunk 模型与迁移

**文件：**
- 创建：`backend/app/models/crawl_chunk.py`
- 修改：`backend/app/models/__init__.py`
- 修改：`backend/app/models/crawl_job.py`
- 创建：`backend/alembic/versions/a1b2c3d4e5f6_add_crawl_page_chunks.py`
- 测试：`backend/test/test_crawl_job_models.py`
- 测试：`backend/test/test_database_schema.py`

- [ ] **步骤 1：编写失败测试：chunk 状态枚举稳定**

在 `backend/test/test_crawl_job_models.py` 中新增 import 和测试：

```python
from app.models.crawl_chunk import CrawlPageChunkStatus
```

```python
    def test_chunk_status_constants_are_stable(self) -> None:
        self.assertEqual(CrawlPageChunkStatus.PENDING.value, "pending")
        self.assertEqual(CrawlPageChunkStatus.PROCESSING.value, "processing")
        self.assertEqual(CrawlPageChunkStatus.COMPLETED.value, "completed")
        self.assertEqual(CrawlPageChunkStatus.NO_CANDIDATES.value, "no_candidates")
        self.assertEqual(CrawlPageChunkStatus.SPLIT_REQUIRED.value, "split_required")
        self.assertEqual(CrawlPageChunkStatus.SUPERSEDED.value, "superseded")
        self.assertEqual(CrawlPageChunkStatus.FAILED.value, "failed")
```

- [ ] **步骤 2：运行测试验证失败**

```powershell
cd backend
uv run python -m unittest test.test_crawl_job_models.CrawlJobModelTests.test_chunk_status_constants_are_stable
```

预期：FAIL，`ModuleNotFoundError: app.models.crawl_chunk`。

- [ ] **步骤 3：创建 ORM 模型**

创建 `backend/app/models/crawl_chunk.py`，定义：

```python
class CrawlPageChunkStatus(str, Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    NO_CANDIDATES = "no_candidates"
    SPLIT_REQUIRED = "split_required"
    SUPERSEDED = "superseded"
    FAILED = "failed"
```

同文件创建 `CrawlPageChunk(Base)`，字段必须包含：`job_id`、`page_id`、`source_url`、`page_fingerprint`、`chunk_id`、`parent_chunk_id`、`chunk_index`、`chunk_hash`、`status`、`content`、`token_estimate`、`text_start_offset`、`text_end_offset`、`overlap_prefix`、`overlap_suffix`、`split_depth`、`split_reason`、`attempt_count`、`last_error`、`created_at`、`updated_at`。`__table_args__` 增加 `UniqueConstraint("job_id", "chunk_id", name="uq_crawl_page_chunks_job_chunk_id")`。

- [ ] **步骤 4：导出模型并扩展候选模型**

修改 `backend/app/models/__init__.py`：

```python
from app.models.crawl_chunk import CrawlPageChunk, CrawlPageChunkStatus
```

修改 `backend/app/models/crawl_job.py`：导入 `Boolean`，在 `CrawlCandidate` 增加：

```python
    source_chunk_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    source_kind: Mapped[str | None] = mapped_column(String(64), nullable=True)
    boundary_risk: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("0"))
    identity_key: Mapped[str | None] = mapped_column(String(1000), nullable=True, index=True)
    merge_history: Mapped[list[dict[str, object]] | None] = mapped_column(JSON, nullable=True)
    field_sources: Mapped[dict[str, object] | None] = mapped_column(JSON, nullable=True)
    conflicts: Mapped[dict[str, object] | None] = mapped_column(JSON, nullable=True)
```

- [ ] **步骤 5：创建 Alembic 迁移**

创建 `backend/alembic/versions/a1b2c3d4e5f6_add_crawl_page_chunks.py`：创建 `crawl_page_chunks` 表；为 `crawl_candidates` 增加 `source_chunk_id`、`source_kind`、`boundary_risk`、`identity_key`、`merge_history`、`field_sources`、`conflicts`；为 `(job_id, chunk_id)` 添加唯一约束；为 `status`、`job_id`、`source_chunk_id`、`identity_key` 添加索引。`down_revision` 填写 `f8a9b0c1d2e3`。

- [ ] **步骤 6：运行模型与数据库测试**

```powershell
cd backend
uv run python -m unittest test.test_crawl_job_models test.test_database_schema
```

预期：PASS。

- [ ] **步骤 6：Commit**

```powershell
git add backend/app/models/crawl_chunk.py backend/app/models/__init__.py backend/app/models/crawl_job.py backend/alembic/versions/*_add_crawl_page_chunks.py backend/test/test_crawl_job_models.py backend/test/test_database_schema.py
git commit -m "feat(crawler): add page chunk persistence model"
```

---

## 任务 4：通用 HTML chunk 切片器

**文件：**
- 创建：`backend/app/services/crawler_chunking.py`
- 测试：`backend/test/test_crawler_chunking.py`

- [ ] **步骤 1：编写切片器测试文件**

创建 `backend/test/test_crawler_chunking.py`：

```python
from __future__ import annotations

import unittest

from app.services.crawler_chunking import ChunkingConfig, build_page_chunks, estimate_tokens, fingerprint_page


class CrawlerChunkingTests(unittest.TestCase):
    def test_build_page_chunks_preserves_links_as_markdown(self) -> None:
        html = """
        <html><body><nav>首页</nav><main>
        <div class="teacher"><a href="/zhang.htm">张三</a><p>研究方向：数据库</p></div>
        <div class="teacher"><a href="https://cs.example.edu/li.htm">李四</a><p>邮箱：li@example.edu</p></div>
        </main><script>alert(1)</script></body></html>
        """
        chunks = build_page_chunks(source_url="https://cs.example.edu/faculty/index.htm", html=html, text="张三\n李四", config=ChunkingConfig())
        self.assertEqual(len(chunks), 1)
        self.assertIn("[张三](https://cs.example.edu/zhang.htm)", chunks[0].content)
        self.assertIn("[李四](https://cs.example.edu/li.htm)", chunks[0].content)
        self.assertNotIn("alert", chunks[0].content)

    def test_build_page_chunks_splits_long_text_with_overlap(self) -> None:
        blocks = "\n".join(f"教师{i} 研究方向 数据库 [详情](https://cs.example.edu/t{i}.htm)" for i in range(80))
        chunks = build_page_chunks(
            source_url="https://cs.example.edu/faculty/index.htm",
            html=f"<main>{''.join(f'<p>{line}</p>' for line in blocks.splitlines())}</main>",
            text=blocks,
            config=ChunkingConfig(target_tokens=120, soft_max_tokens=160, hard_max_tokens=220, overlap_tokens=30),
        )
        self.assertGreater(len(chunks), 1)
        self.assertFalse(chunks[0].overlap_prefix)
        self.assertTrue(chunks[0].overlap_suffix)
        self.assertTrue(chunks[1].overlap_prefix)
        self.assertLessEqual(max(chunk.token_estimate for chunk in chunks), 220)

    def test_fingerprint_page_is_stable(self) -> None:
        self.assertEqual(fingerprint_page("  张三\n李四  "), fingerprint_page("张三 李四"))

    def test_estimate_tokens_counts_chinese_and_ascii(self) -> None:
        self.assertGreaterEqual(estimate_tokens("张三教授 email@example.edu"), 6)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **步骤 2：运行测试验证失败**

```powershell
cd backend
uv run python -m unittest test.test_crawler_chunking
```

预期：FAIL，`ModuleNotFoundError: app.services.crawler_chunking`。

- [ ] **步骤 3：实现切片器数据结构**

创建 `backend/app/services/crawler_chunking.py`：

```python
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from html.parser import HTMLParser
from urllib.parse import urljoin


@dataclass(frozen=True)
class ChunkingConfig:
    target_tokens: int = 2000
    soft_max_tokens: int = 2800
    hard_max_tokens: int = 3200
    overlap_tokens: int = 180
    min_split_tokens: int = 500
    max_split_depth: int = 4


@dataclass(frozen=True)
class PageChunkDraft:
    chunk_id: str
    source_url: str
    page_fingerprint: str
    chunk_index: int
    chunk_hash: str
    content: str
    token_estimate: int
    text_start_offset: int | None
    text_end_offset: int | None
    overlap_prefix: bool
    overlap_suffix: bool
    split_depth: int = 0
    parent_chunk_id: str | None = None
```

- [ ] **步骤 4：实现链接增强解析和 token 估算**

实现 `_LinkTextHTMLParser`，跳过 `script/style/noscript/svg`，在 `a` 结束时输出 `[锚文本](绝对 URL)`。实现：

```python
def estimate_tokens(value: str) -> int:
    chinese_chars = len(re.findall(r"[\u4e00-\u9fff]", value))
    ascii_words = len(re.findall(r"[A-Za-z0-9_@./:-]+", value))
    other_chars = max(len(value) - chinese_chars, 0)
    return max(1, chinese_chars + ascii_words + other_chars // 4)


def fingerprint_page(value: str) -> str:
    normalized = " ".join(value.split())
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()
```

- [ ] **步骤 5：实现 `build_page_chunks`**

按行聚合链接增强文本，超过 `target_tokens` 切分，超过 `hard_max_tokens` 强制二分，使用 `_overlap_tail(lines, overlap_tokens)` 生成 overlap。`chunk_id` 使用 `page_fingerprint[:16]` 加序号；`chunk_hash` 使用 chunk 内容 sha256。

- [ ] **步骤 6：运行切片器测试**

```powershell
cd backend
uv run python -m unittest test.test_crawler_chunking
```

预期：PASS。

- [ ] **步骤 6：Commit**

```powershell
git add backend/app/services/crawler_chunking.py backend/test/test_crawler_chunking.py
git commit -m "feat(crawler): add generic page chunking"
```

---

## 任务 5：chunk 运行时领取与提交

**文件：**
- 创建：`backend/app/services/crawler_chunk_runtime.py`
- 测试：`backend/test/test_crawler_chunk_runtime.py`

- [ ] **步骤 1：编写运行时测试文件**

创建 `backend/test/test_crawler_chunk_runtime.py`，包含 SQLite helper，并新增领取测试：

```python
from __future__ import annotations

import asyncio
import unittest

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models import CrawlJob, CrawlPage, CrawlPageChunk, CrawlPageChunkStatus, CrawlJobStatus
from app.models.base import Base
from app.services.crawler_chunking import ChunkingConfig, build_page_chunks
from app.services.crawler_chunk_runtime import claim_next_page_chunk, create_chunks_for_page, submit_chunk_candidates
from app.services.crawler_tools import CrawlToolContext


async def _session_factory() -> async_sessionmaker[AsyncSession]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    return async_sessionmaker(engine, expire_on_commit=False)


class CrawlerChunkRuntimeTests(unittest.TestCase):
    def test_claim_next_page_chunk_marks_chunk_processing(self) -> None:
        async def run() -> None:
            session_factory = await _session_factory()
            async with session_factory() as session:
                job = CrawlJob(university="示例大学", school="计算机学院", start_url="https://cs.example.edu", status=CrawlJobStatus.RUNNING.value)
                page = CrawlPage(job=job, url="https://cs.example.edu/faculty", fetch_method="http", status="succeeded")
                session.add_all([job, page])
                await session.commit()
                await session.refresh(job)
                await session.refresh(page)
            drafts = build_page_chunks(source_url="https://cs.example.edu/faculty", html="<p>张三</p>", text="张三", config=ChunkingConfig())
            await create_chunks_for_page(session_factory, job_id=job.id, page_id=page.id, drafts=drafts)
            claimed = await claim_next_page_chunk(session_factory, job_id=job.id)
            self.assertEqual(claimed.status, "ok")
            self.assertIn("张三", claimed.content)
            async with session_factory() as session:
                row = (await session.scalars(select(CrawlPageChunk))).one()
                self.assertEqual(row.status, CrawlPageChunkStatus.PROCESSING.value)
        asyncio.run(run())
```

- [ ] **步骤 2：运行测试验证失败**

```powershell
cd backend
uv run python -m unittest test.test_crawler_chunk_runtime.CrawlerChunkRuntimeTests.test_claim_next_page_chunk_marks_chunk_processing
```

预期：FAIL，`ModuleNotFoundError: app.services.crawler_chunk_runtime`。

- [ ] **步骤 3：实现运行时基础函数**

创建 `backend/app/services/crawler_chunk_runtime.py`，定义：

```python
@dataclass(frozen=True)
class ClaimedChunk:
    status: Literal["ok", "empty", "already_processed"]
    chunk_id: str | None = None
    source_url: str | None = None
    chunk_index: int | None = None
    content: str | None = None
    max_candidates: int = 10
    message: str | None = None
```

实现 `create_chunks_for_page(session_factory, job_id, page_id, drafts)`：逐个插入 `CrawlPageChunk`，跳过同 `job_id + chunk_id` 已存在记录。

实现 `claim_next_page_chunk(session_factory, job_id)`：选择最早 `pending` chunk，状态改为 `processing`，`attempt_count += 1`，返回 `ClaimedChunk(status="ok", ...)`；没有待处理 chunk 返回 `ClaimedChunk(status="empty", message="当前没有待处理页面片段。请探索新页面或结束任务。")`。

- [ ] **步骤 4：运行领取测试**

```powershell
cd backend
uv run python -m unittest test.test_crawler_chunk_runtime.CrawlerChunkRuntimeTests.test_claim_next_page_chunk_marks_chunk_processing
```

预期：PASS。

- [ ] **步骤 5：编写无候选提交测试**

在同文件新增：

```python
    def test_submit_chunk_candidates_marks_no_candidates(self) -> None:
        async def run() -> None:
            session_factory = await _session_factory()
            async with session_factory() as session:
                job = CrawlJob(university="示例大学", school="计算机学院", start_url="https://cs.example.edu", status=CrawlJobStatus.RUNNING.value)
                page = CrawlPage(job=job, url="https://cs.example.edu/faculty", fetch_method="http", status="succeeded")
                session.add_all([job, page])
                await session.commit()
                await session.refresh(job)
                await session.refresh(page)
            drafts = build_page_chunks(source_url="https://cs.example.edu/faculty", html="<p>导航</p>", text="导航", config=ChunkingConfig())
            await create_chunks_for_page(session_factory, job_id=job.id, page_id=page.id, drafts=drafts)
            claimed = await claim_next_page_chunk(session_factory, job_id=job.id)
            ctx = CrawlToolContext(job_id=job.id, start_url="https://cs.example.edu", university="示例大学", school="计算机学院", session_factory=session_factory)
            result = await submit_chunk_candidates(ctx, chunk_id=claimed.chunk_id or "", chunk_status="no_candidates", has_more_candidates_in_chunk=False, candidates=[])
            self.assertEqual(result["chunk_status"], CrawlPageChunkStatus.NO_CANDIDATES.value)
        asyncio.run(run())
```

- [ ] **步骤 6：实现 `submit_chunk_candidates` 基础逻辑**

实现函数签名：

```python
async def submit_chunk_candidates(ctx: CrawlToolContext, *, chunk_id: str, chunk_status: str, has_more_candidates_in_chunk: bool, candidates: list[dict[str, object]]) -> dict[str, Any]:
```

行为：校验 chunk 属于当前 job；已完成或 superseded 返回 `already_processed`；候选数超过 10 调 `_mark_chunk_split_required`；`chunk_status == "no_candidates"` 标记 `no_candidates`；否则将 candidates 通过 `ProfessorCandidatePayload.model_validate` 后调用 `save_candidate_batch`；候选数为 10、`has_more_candidates_in_chunk` 为 true 或 `chunk_status == "too_many_candidates"` 时标记 `split_required`；否则标记 `completed`。

- [ ] **步骤 7：运行 runtime 测试**

```powershell
cd backend
uv run python -m unittest test.test_crawler_chunk_runtime
```

预期：PASS。

- [ ] **步骤 8：Commit**

```powershell
git add backend/app/services/crawler_chunk_runtime.py backend/test/test_crawler_chunk_runtime.py
git commit -m "feat(crawler): add page chunk runtime"
```

---

## 任务 6：chunk 自动拆分与父子状态

**文件：**
- 修改：`backend/app/services/crawler_chunk_runtime.py`
- 修改：`backend/app/services/crawler_chunking.py`
- 测试：`backend/test/test_crawler_chunk_runtime.py`

- [ ] **步骤 1：编写失败测试：提交 10 个候选触发拆分并生成子 chunk**

在 `backend/test/test_crawler_chunk_runtime.py` 新增：

```python
    def test_submit_ten_candidates_splits_parent_chunk_into_children(self) -> None:
        async def run() -> None:
            session_factory = await _session_factory()
            async with session_factory() as session:
                job = CrawlJob(university="示例大学", school="计算机学院", start_url="https://cs.example.edu", status=CrawlJobStatus.RUNNING.value)
                page = CrawlPage(job=job, url="https://cs.example.edu/faculty", fetch_method="http", status="succeeded")
                session.add_all([job, page])
                await session.commit()
                await session.refresh(job)
                await session.refresh(page)
            content = "\n".join(f"教师{i} [详情](https://cs.example.edu/t{i}.htm) 研究方向 数据库" for i in range(40))
            drafts = build_page_chunks(source_url="https://cs.example.edu/faculty", html="", text=content, config=ChunkingConfig(target_tokens=1000, soft_max_tokens=1200, hard_max_tokens=1400, overlap_tokens=30))[:1]
            await create_chunks_for_page(session_factory, job_id=job.id, page_id=page.id, drafts=drafts)
            claimed = await claim_next_page_chunk(session_factory, job_id=job.id)
            ctx = CrawlToolContext(job_id=job.id, start_url="https://cs.example.edu", university="示例大学", school="计算机学院", session_factory=session_factory)
            candidates = [{"name": f"教师{i}", "profile_url": f"https://cs.example.edu/t{i}.htm", "source_url": "https://cs.example.edu/faculty"} for i in range(10)]
            result = await submit_chunk_candidates(ctx, chunk_id=claimed.chunk_id or "", chunk_status="completed", has_more_candidates_in_chunk=True, candidates=candidates)
            self.assertEqual(result["chunk_status"], CrawlPageChunkStatus.SPLIT_REQUIRED.value)
            async with session_factory() as session:
                rows = list(await session.scalars(select(CrawlPageChunk).order_by(CrawlPageChunk.id)))
                self.assertEqual(rows[0].status, CrawlPageChunkStatus.SUPERSEDED.value)
                self.assertGreaterEqual(len(rows), 3)
                self.assertTrue(all(row.parent_chunk_id == rows[0].chunk_id for row in rows[1:]))
                self.assertTrue(all(row.status == CrawlPageChunkStatus.PENDING.value for row in rows[1:]))
        asyncio.run(run())
```

- [ ] **步骤 2：运行测试验证失败**

```powershell
cd backend
uv run python -m unittest test.test_crawler_chunk_runtime.CrawlerChunkRuntimeTests.test_submit_ten_candidates_splits_parent_chunk_into_children
```

预期：FAIL，父 chunk 为 `split_required`，没有子 chunk。

- [ ] **步骤 3：实现 `split_chunk_content`**

在 `crawler_chunking.py` 新增：

```python
def split_chunk_content(*, source_url: str, content: str, parent_chunk_id: str, page_fingerprint: str, split_depth: int, config: ChunkingConfig | None = None) -> list[PageChunkDraft]:
    selected_config = config or ChunkingConfig()
    if estimate_tokens(content) <= selected_config.min_split_tokens:
        return []
    lines = content.splitlines()
    midpoint = max(1, len(lines) // 2)
    left_lines = lines[:midpoint]
    right_lines = [*_overlap_tail(left_lines, selected_config.overlap_tokens), *lines[midpoint:]]
    drafts: list[PageChunkDraft] = []
    for index, child_lines in enumerate((left_lines, right_lines)):
        normalized = _normalize_lines("\n".join(child_lines))
        if not normalized:
            continue
        drafts.append(PageChunkDraft(chunk_id=f"{parent_chunk_id}.{index + 1}", source_url=source_url, page_fingerprint=page_fingerprint, chunk_index=index, chunk_hash=chunk_hash(normalized), content=normalized, token_estimate=estimate_tokens(normalized), text_start_offset=None, text_end_offset=None, overlap_prefix=index > 0, overlap_suffix=index == 0, split_depth=split_depth, parent_chunk_id=parent_chunk_id))
    return drafts
```

- [ ] **步骤 4：实现 `_split_chunk_in_session`**

在 `crawler_chunk_runtime.py` 新增 helper：接收当前 session、`job_id`、父 chunk 和 reason；超过 `ChunkingConfig().max_split_depth` 时标记 `failed`；调用 `split_chunk_content` 生成子 chunk；父 chunk 标记 `superseded`；子 chunk 插入为 `pending`；返回子 chunk 数量。

- [ ] **步骤 5：让 `_mark_chunk_split_required` 调用拆分**

将 `_mark_chunk_split_required` 改为在同一 session 内调用 `_split_chunk_in_session`，返回：

```python
{"chunk_status": CrawlPageChunkStatus.SPLIT_REQUIRED.value, "split_reason": reason, "created_child_chunks": child_count}
```

- [ ] **步骤 6：运行拆分测试和 runtime 全量测试**

```powershell
cd backend
uv run python -m unittest test.test_crawler_chunk_runtime.CrawlerChunkRuntimeTests.test_submit_ten_candidates_splits_parent_chunk_into_children
uv run python -m unittest test.test_crawler_chunk_runtime
```

预期：PASS。

- [ ] **步骤 6：Commit**

```powershell
git add backend/app/services/crawler_chunking.py backend/app/services/crawler_chunk_runtime.py backend/test/test_crawler_chunk_runtime.py
git commit -m "feat(crawler): split dense page chunks"
```

---

## 任务 7：Agent 工具接入 chunk 工作流

**文件：**
- 修改：`backend/app/agents/faculty_crawler_agent.py`
- 测试：`backend/test/test_faculty_crawler_agent.py`

- [ ] **步骤 1：编写失败测试：受控工具包含 chunk 工具**

```python
    def test_controlled_tool_names_include_chunk_tools(self) -> None:
        self.assertIn("claim_next_page_chunk", CONTROLLED_CRAWLER_TOOL_NAMES)
        self.assertIn("submit_chunk_candidates", CONTROLLED_CRAWLER_TOOL_NAMES)
```

- [ ] **步骤 2：运行测试验证失败**

```powershell
cd backend
uv run python -m unittest test.test_faculty_crawler_agent.FacultyCrawlerAgentTests.test_controlled_tool_names_include_chunk_tools
```

预期：FAIL。

- [ ] **步骤 3：更新工具 allowlist 和系统提示**

把 `claim_next_page_chunk`、`submit_chunk_candidates` 加入 `CONTROLLED_CRAWLER_TOOL_NAMES`。在系统提示中明确：列表页候选抽取优先领取 chunk；每次只处理当前 chunk；最多提交 10 个候选；不要根据记忆提交其他 chunk 候选；探索新 URL 时才使用 `crawl_page`。

- [ ] **步骤 4：新增工具函数**

在 `create_faculty_crawler_agent` 中新增：

```python
    @tool
    async def claim_next_page_chunk() -> dict[str, Any]:
        """领取下一个待处理的列表页片段；如果没有待处理片段，会返回 empty。"""
        claimed = await claim_chunk_runtime(ctx.session_factory, job_id=ctx.job_id)
        return {"status": claimed.status, "chunk_id": claimed.chunk_id, "source_url": claimed.source_url, "chunk_index": claimed.chunk_index, "content": claimed.content, "max_candidates": claimed.max_candidates, "message": claimed.message}

    @tool
    async def submit_chunk_candidates(chunk_id: str, chunk_status: str, has_more_candidates_in_chunk: bool, candidates: list[dict[str, object]]) -> dict[str, Any]:
        """提交当前 chunk 中识别出的候选；单次最多 10 个。"""
        return await submit_chunk_candidates_runtime(ctx, chunk_id=chunk_id, chunk_status=chunk_status, has_more_candidates_in_chunk=has_more_candidates_in_chunk, candidates=candidates)
```

把两个工具加入 `create_deep_agent(... tools=[...])`。

- [ ] **步骤 5：运行 Agent 测试**

```powershell
cd backend
uv run python -m unittest test.test_faculty_crawler_agent
```

预期：PASS。

- [ ] **步骤 6：Commit**

```powershell
git add backend/app/agents/faculty_crawler_agent.py backend/test/test_faculty_crawler_agent.py
git commit -m "feat(crawler): expose chunk tools to agent"
```

---

## 任务 8：页面抓取后生成列表页 chunk

**文件：**
- 修改：`backend/app/services/crawl_job_runtime.py`
- 修改：`backend/app/agents/faculty_crawler_agent.py`
- 修改：`backend/app/services/crawler_chunk_runtime.py`
- 测试：`backend/test/test_crawl_job_runtime.py`
- 测试：`backend/test/test_faculty_crawler_agent.py`

- [ ] **步骤 1：编写测试：成功页面快照创建 chunk**

在 `backend/test/test_crawl_job_runtime.py` 中新增测试 `test_successful_directory_page_snapshot_creates_chunks`，构造 `PageSnapshot(status="succeeded", html="<main><a href='/zhang.htm'>张三</a></main>")`，调用 `create_chunks_for_successful_page_snapshot(session_factory, job_id=job.id, page_id=page.id, snapshot=snapshot)`，断言返回值大于 0。

- [ ] **步骤 2：运行测试验证失败**

```powershell
cd backend
uv run python -m unittest test.test_crawl_job_runtime.CrawlJobRuntimeTests.test_successful_directory_page_snapshot_creates_chunks
```

预期：FAIL，函数不存在。

- [ ] **步骤 3：实现 `create_chunks_for_successful_page_snapshot`**

在 `crawl_job_runtime.py` 新增：

```python
async def create_chunks_for_successful_page_snapshot(session_factory: async_sessionmaker[AsyncSession], *, job_id: int, page_id: int | None, snapshot: PageSnapshot) -> int:
    if snapshot.status != "succeeded":
        return 0
    if not snapshot.text.strip() and not snapshot.html.strip():
        return 0
    drafts = build_page_chunks(source_url=snapshot.url, html=snapshot.html, text=snapshot.text, config=ChunkingConfig())
    return await create_chunks_for_page(session_factory, job_id=job_id, page_id=page_id, drafts=drafts)
```

- [ ] **步骤 4：避免已生成 chunk 的页面重复返回正文**

在 `crawler_chunk_runtime.py` 新增：

```python
async def has_chunks_for_source_url(session_factory: async_sessionmaker[AsyncSession], *, job_id: int, source_url: str) -> bool:
    async with session_factory() as session:
        chunk_id = await session.scalar(select(CrawlPageChunk.id).where(CrawlPageChunk.job_id == job_id, CrawlPageChunk.source_url == source_url).limit(1))
        return chunk_id is not None
```

在 `faculty_crawler_agent.py` 的 `crawl_page` 工具中，抓取前检查绝对 URL 已有 chunk 时返回短消息：`{"status":"chunked","message":"该页面已生成待处理片段，请调用 claim_next_page_chunk。"}`。

- [ ] **步骤 5：抓取成功后生成 chunk**

在 `crawl_page` 工具中，`snapshot = await crawl_page_with_crawl4ai(ctx, url)` 后调用 `create_chunks_for_successful_page_snapshot(ctx.session_factory, job_id=ctx.job_id, page_id=None, snapshot=snapshot)`。

- [ ] **步骤 6：运行相关测试**

```powershell
cd backend
uv run python -m unittest test.test_crawl_job_runtime test.test_faculty_crawler_agent test.test_crawler_chunk_runtime
```

预期：PASS。

- [ ] **步骤 6：Commit**

```powershell
git add backend/app/services/crawl_job_runtime.py backend/app/agents/faculty_crawler_agent.py backend/app/services/crawler_chunk_runtime.py backend/test/test_crawl_job_runtime.py backend/test/test_faculty_crawler_agent.py
git commit -m "feat(crawler): create chunks for crawled pages"
```

---

## 任务 9：重复循环反馈与停止条件

**文件：**
- 修改：`backend/app/services/crawler_tools.py`
- 修改：`backend/app/services/crawl_job_runtime.py`
- 测试：`backend/test/test_crawler_tools.py`
- 测试：`backend/test/test_crawl_job_runtime.py`

- [ ] **步骤 1：编写测试：连续重复提交返回 duplicate_loop**

在 `backend/test/test_crawler_tools.py` 新增：

```python
    def test_repeated_duplicate_submissions_return_duplicate_loop(self) -> None:
        async def run() -> None:
            session_factory = await _create_sqlite_session_factory()
            async with session_factory() as session:
                job = CrawlJob(university="示例大学", school="计算机学院", start_url="https://cs.example.edu", status=CrawlJobStatus.RUNNING.value)
                session.add(job)
                await session.commit()
                await session.refresh(job)
            ctx = CrawlToolContext(job_id=job.id, start_url="https://cs.example.edu", university="示例大学", school="计算机学院", session_factory=session_factory)
            candidate = ProfessorCandidatePayload(name="张三", profile_url="https://cs.example.edu/zhang", source_url="https://cs.example.edu/faculty")
            await save_candidate_batch(ctx, [candidate])
            await save_candidate_batch(ctx, [candidate])
            await save_candidate_batch(ctx, [candidate])
            third = await save_candidate_batch(ctx, [candidate])
            self.assertEqual(third["batch_status"], "duplicate_loop")
        asyncio.run(run())
```

- [ ] **步骤 2：运行测试验证失败**

```powershell
cd backend
uv run python -m unittest test.test_crawler_tools.CrawlerToolTests.test_repeated_duplicate_submissions_return_duplicate_loop
```

预期：FAIL，`batch_status` 不是 `duplicate_loop`。

- [ ] **步骤 3：扩展 `CrawlToolContext` 重复循环状态**

在 `crawler_tools.py` 新增：

```python
@dataclass
class DuplicateSaveLoopState:
    consecutive_duplicate_batches: int = 0
```

在 `CrawlToolContext` 增加字段：

```python
    duplicate_save_loop: DuplicateSaveLoopState = field(default_factory=DuplicateSaveLoopState)
```

在 `save_candidate_batch` 生成结果后，如果 `saved_count == 0`、`merged_count == 0`、`skipped_duplicate_count > 0`，连续计数加 1；否则清零。连续达到 3 时，设置：

```python
result["batch_status"] = "duplicate_loop"
result["next_instruction"] = "连续多个批次均为重复候选，请停止保存当前内容，获取下一个 chunk 或结束任务。"
```

- [ ] **步骤 4：运行 duplicate loop 测试**

```powershell
cd backend
uv run python -m unittest test.test_crawler_tools.CrawlerToolTests.test_repeated_duplicate_submissions_return_duplicate_loop
```

预期：PASS。

- [ ] **步骤 5：实现 pending work helper 测试**

在 `backend/test/test_crawl_job_runtime.py` 新增：空任务 `crawl_job_has_pending_work(session_factory, job_id)` 返回 False；存在 `pending` chunk 返回 True。

- [ ] **步骤 6：实现 `crawl_job_has_pending_work`**

在 `crawl_job_runtime.py` 新增：查询 `CrawlPageChunk.status` 是否在 `pending`、`processing`、`split_required` 中；有则返回 True，否则 False。后续 URL 队列表接入时再扩展。

- [ ] **步骤 7：运行相关测试**

```powershell
cd backend
uv run python -m unittest test.test_crawler_tools test.test_crawl_job_runtime
```

预期：PASS。

- [ ] **步骤 8：Commit**

```powershell
git add backend/app/services/crawler_tools.py backend/app/services/crawl_job_runtime.py backend/test/test_crawler_tools.py backend/test/test_crawl_job_runtime.py
git commit -m "feat(crawler): stop duplicate save loops"
```

---

## 任务 10：详情页补全优先级与候选来源字段

**文件：**
- 修改：`backend/app/services/crawler_tools.py`
- 修改：`backend/app/services/crawl_job_runtime.py`
- 测试：`backend/test/test_crawler_tools.py`
- 测试：`backend/test/test_crawl_job_runtime.py`

- [ ] **步骤 1：编写测试：详情页邮箱覆盖列表页边界邮箱**

在 `backend/test/test_crawl_job_runtime.py` 中新增：构造已有 `CrawlCandidate(email="zhang@example.com", profile_url="https://cs.example.edu/zhang", boundary_risk=True, source_kind="list_chunk", field_confidence={"email": 0.4})`，再用 `source_kind="profile_page"`、`email="zhang@example.com.cn"`、`field_confidence={"email": 0.95}` 合并，断言邮箱更新为 `.com.cn`。

- [ ] **步骤 2：运行测试验证失败**

运行 `cd backend && uv run python -m unittest test.test_crawl_job_runtime`。预期：FAIL，合并函数不存在或不覆盖。

- [ ] **步骤 3：实现来源优先级 helper**

在 `backend/app/services/crawl_job_runtime.py` 或可复用的 `backend/app/services/crawler_tools.py` 中新增：

```python
_SOURCE_PRIORITY = {"profile_page": 3, "list_chunk": 2, None: 1}


def should_replace_field(*, old_value: object, new_value: object, old_source_kind: str | None, new_source_kind: str | None, old_confidence: float | None, new_confidence: float | None, old_boundary_risk: bool, new_boundary_risk: bool) -> bool:
    if new_value in (None, ""):
        return False
    if old_value in (None, ""):
        return True
    if _SOURCE_PRIORITY.get(new_source_kind, 1) > _SOURCE_PRIORITY.get(old_source_kind, 1):
        return True
    if old_boundary_risk and not new_boundary_risk:
        return True
    return (new_confidence or 0) > (old_confidence or 0) + 0.2
```

- [ ] **步骤 4：将 helper 接入候选合并**

在 `_merge_candidate_payload` 中至少对 `email` 使用 `should_replace_field`。详情页来源优先级高于列表页；边界风险字段可被非边界字段覆盖；低置信字段不能覆盖高置信字段。

- [ ] **步骤 5：运行详情补全相关测试**

```powershell
cd backend
uv run python -m unittest test.test_crawler_tools
```

同时运行 `uv run python -m unittest test.test_crawl_job_runtime`。预期：PASS。

- [ ] **步骤 6：Commit**

```powershell
git add backend/app/services/crawler_tools.py backend/app/services/crawl_job_runtime.py backend/test/test_crawler_tools.py backend/test/test_crawl_job_runtime.py
git commit -m "feat(crawler): prefer profile evidence during merge"
```

---

## 任务 11：调试日志与可观测性

**文件：**
- 修改：`backend/app/services/crawl_job_events.py`
- 修改：`backend/app/agents/faculty_crawler_agent.py`
- 测试：`backend/test/test_crawl_job_events.py`
- 测试：`backend/test/test_faculty_crawler_agent.py`
- 测试：`backend/test/test_diagnostics_api.py`

- [ ] **步骤 1：编写事件命名测试**

在 `backend/test/test_crawl_job_events.py` 中新增断言：

```python
self.assertEqual(describe_crawl_event("claim_next_page_chunk"), "Agent 领取待处理页面片段")
self.assertEqual(describe_crawl_event("submit_chunk_candidates"), "Agent 提交页面片段候选")
self.assertEqual(describe_crawl_event("chunk_split_required"), "页面片段候选过密，已触发拆分")
```

如果现有 API 名不是 `describe_crawl_event`，使用该文件已有的映射访问方式。

- [ ] **步骤 2：运行测试验证失败**

```powershell
cd backend
uv run python -m unittest test.test_crawl_job_events
```

预期：FAIL，事件名缺失。

- [ ] **步骤 3：补充事件映射**

在 `backend/app/services/crawl_job_events.py` 增加：

```python
"claim_next_page_chunk": "Agent 领取待处理页面片段",
"submit_chunk_candidates": "Agent 提交页面片段候选",
"chunk_split_required": "页面片段候选过密，已触发拆分",
"duplicate_loop": "候选重复提交循环，已要求停止当前保存",
```

- [ ] **步骤 4：限制 trace 中 chunk 正文长度**

在 `faculty_crawler_agent.py` 的 `build_trace_event` 解析后调用 `_redact_large_chunk_content`。规则：递归遍历 dict/list；当 key 为 `content` 且字符串超过 1000 字符时，保留前 1000 字符并附加「chunk 内容已截断」。

- [ ] **步骤 5：补充 trace 截断测试**

在 `test_faculty_crawler_agent.py` 中新增：

```python
    def test_build_trace_event_truncates_large_chunk_content(self) -> None:
        event = {"data": {"tools": {"messages": [{"content": "x" * 2000}]}}}
        trace = build_trace_event(event)
        self.assertNotIn("x" * 1500, str(trace))
```

- [ ] **步骤 6：运行诊断相关测试**

```powershell
cd backend
uv run python -m unittest test.test_crawl_job_events test.test_faculty_crawler_agent test.test_diagnostics_api
```

预期：PASS。

- [ ] **步骤 6：Commit**

```powershell
git add backend/app/services/crawl_job_events.py backend/app/agents/faculty_crawler_agent.py backend/test/test_crawl_job_events.py backend/test/test_faculty_crawler_agent.py
git commit -m "feat(crawler): log chunk workflow events"
```

---

## 任务 12：端到端回归与文档同步

**文件：**
- 修改：`docs/database_table_design.md`
- 修改：`docs/superpowers/specs/2026-05-23-crawler-chunked-agent-redesign-design.md`

- [ ] **步骤 1：更新数据库设计文档**

在 `docs/database_table_design.md` 中新增 `crawl_page_chunks` 表说明，至少包含 `job_id`、`page_id`、`source_url`、`chunk_id`、`status`、`content`、`token_estimate`、`parent_chunk_id`、`split_depth` 字段。

- [ ] **步骤 2：更新规格实现备注**

在规格文档增加：

```markdown
## 实现备注

第一版实现参数：`target_tokens=2000`、`soft_max_tokens=2800`、`hard_max_tokens=3200`、`overlap_tokens=180`、`min_split_tokens=500`、`max_split_depth=4`。
```

- [ ] **步骤 3：运行 focused 测试**

```powershell
cd backend
uv run python -m unittest test.test_crawler_tools test.test_crawler_chunking test.test_crawler_chunk_runtime test.test_faculty_crawler_agent test.test_crawl_job_runtime test.test_crawl_job_models test.test_database_schema
```

预期：全部 PASS。

- [ ] **步骤 4：运行后端全量测试**

```powershell
cd backend
uv run python -m unittest discover test
```

预期：全部 PASS。如果出现无关失败，不要顺手修，记录失败测试和错误摘要。

- [ ] **步骤 5：检查 git diff**

```powershell
git diff --stat
git diff -- backend/app/services/crawler_tools.py backend/app/services/crawler_chunking.py backend/app/services/crawler_chunk_runtime.py backend/app/agents/faculty_crawler_agent.py
```

预期：diff 只包含本计划相关文件。

- [ ] **步骤 6：Commit**

```powershell
git add docs/database_table_design.md docs/superpowers/specs/2026-05-23-crawler-chunked-agent-redesign-design.md
git commit -m "docs(crawler): document chunked crawler workflow"
```

---

## 自检

### 规格覆盖度

- 保存准入：任务 1 覆盖。
- `email/profile_url` 幂等：任务 1 覆盖。
- 字段级合并、边界截断和详情页优先级：任务 2 和任务 10 覆盖。
- chunk 模型、状态和数据持久化：任务 3 覆盖。
- 通用 HTML 清洗、链接增强文本、参数 `target_tokens=2000` / `soft_max_tokens=2800` / `hard_max_tokens=3200` / `overlap_tokens=180`：任务 4 覆盖。
- chunk 领取、提交、无候选 chunk：任务 5 覆盖。
- chunk 超过 10 个候选自动拆分、父子状态、递归限制基础：任务 6 覆盖。
- Agent 工具接入和提示调整：任务 7 覆盖。
- 页面抓取后生成 chunk、已 chunk 页面不再返回正文：任务 8 覆盖。
- 重复循环反馈和停止条件：任务 9 覆盖。
- 调试日志和可观测性：任务 11 覆盖。
- 文档与端到端验证：任务 12 覆盖。

### 类型一致性

- chunk 状态统一使用 `CrawlPageChunkStatus`。
- chunk 主键对模型可见使用 `chunk_id` 字符串，不暴露数据库自增 `id`。
- 保存结果统一包含 `saved_count`、`merged_count`、`skipped_duplicate_count`、`rejected_count`。
- 模型工具提交字段统一为 `chunk_status`、`has_more_candidates_in_chunk`、`candidates`。

### 验证命令

最终至少运行：

```powershell
cd backend
uv run python -m unittest test.test_crawler_tools test.test_crawler_chunking test.test_crawler_chunk_runtime test.test_faculty_crawler_agent test.test_crawl_job_runtime test.test_crawl_job_models test.test_database_schema
```

建议最终运行：

```powershell
cd backend
uv run python -m unittest discover test
```


