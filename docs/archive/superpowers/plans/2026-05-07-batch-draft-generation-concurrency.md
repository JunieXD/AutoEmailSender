# 批量草稿生成并发设置实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 在“其他设置”中新增批量邮件 LLM 草稿生成并发数，并让后端按该设置自动生成批量 LLM 草稿，同时支持暂停、停止、恢复、失败处理和前端进度展示。

**架构：** 运行时设置继续由 `app_settings` 和 `/api/runtime-settings` 管理。后端新增 LLM 批量草稿 worker 与运行登记表，worker 领取 `discovered/matched` 的 LLM 批量子任务到 `generating_draft`，成功后进入 `review_required`，失败后进入 `draft_failed`，暂停或停止时主动取消本地任务并通过数据库状态兜底。模板模式在创建批量任务时直接渲染到 `approved`，不进入 LLM worker。

**技术栈：** FastAPI、SQLAlchemy、Alembic、Pydantic、asyncio、unittest、Vite、React、TypeScript、Vitest、Testing Library。

---

## 当前上下文

- 规格文档：`docs/superpowers/specs/2026-05-07-batch-draft-generation-concurrency-design.md`
- 当前 Alembic head：`e8f2a4b6c9d0`
- 已有运行时设置链路：
  - `backend/app/models/app_setting.py`
  - `backend/app/schemas/runtime_settings.py`
  - `backend/app/services/runtime_settings.py`
  - `frontend/src/lib/api/runtimeSettings.ts`
  - `frontend/src/components/molecules/OtherSettingsCard.tsx`
- 已有批量任务链路：
  - `backend/app/api/batch_tasks.py`
  - `backend/app/schemas/batch_task.py`
  - `backend/app/models/batch_task.py`
  - `backend/app/models/email_task.py`
  - `frontend/src/pages/CreateTaskPage.tsx`
  - `frontend/src/pages/TasksPage.tsx`
- 已有草稿生成入口：`backend/app/services/task_runtime.py::generate_task_draft()`
- 已有后台运行时：`backend/app/services/runtime_manager.py`

## 文件结构

### 后端设置与数据模型

- 修改：`backend/app/models/app_setting.py`
  - 新增 `batch_draft_generation_concurrency`。
- 修改：`backend/app/schemas/runtime_settings.py`
  - `RuntimeSettingsRead` / `RuntimeSettingsUpdate` 新增并发字段。
- 修改：`backend/app/services/runtime_settings.py`
  - 序列化新增字段。
- 修改：`backend/app/models/email_task.py`
  - 新增 `EmailTaskStatus.GENERATING_DRAFT`、`EmailTaskStatus.DRAFT_FAILED`。
  - 新增 `draft_generation_previous_status` 字段。
- 新增：`backend/alembic/versions/f6b2c9d8a1e4_add_batch_draft_generation_runtime.py`
  - 在 `app_settings` 新增并发字段。
  - 在 `email_tasks` 新增 `draft_generation_previous_status`。

### 后端批量草稿运行时

- 新增：`backend/app/services/batch_draft_generation_runtime.py`
  - 负责恢复卡住任务、领取候选任务、并发执行、登记运行任务、取消批量任务下的本地任务。
- 修改：`backend/app/services/task_runtime.py`
  - 支持自动批量模式、写入 `draft_failed`、写入 token 到操作日志、写入前检查批量任务状态。
- 修改：`backend/app/services/runtime_manager.py`
  - 启动 `batch-draft-worker` 循环。
  - 持有批量草稿运行登记表，暴露 `cancel_batch_draft_generation()`。
- 修改：`backend/app/api/batch_tasks.py`
  - 模板模式创建时直接渲染为 `approved`。
  - 暂停/停止后通知 `runtime_manager` 主动取消本地任务。
  - 序列化 `generating_draft_count` 和 `draft_failed_count`。
- 修改：`backend/app/schemas/batch_task.py`
  - 批量任务卡片 DTO 新增统计字段。

### 前端

- 修改：`frontend/src/lib/api/runtimeSettings.ts`
  - 新增 `batch_draft_generation_concurrency`。
- 修改：`frontend/src/components/molecules/OtherSettingsCard.tsx`
  - 新增“批量邮件 LLM 草稿并发数”。
  - 摘要显示草稿并发值。
- 修改：`frontend/src/types/index.ts`
  - `WorkspaceTaskStatus` 新增 `generating_draft`、`draft_failed`。
  - `BatchTaskCardDTO` 新增 `generating_draft_count`、`draft_failed_count`。
  - `BatchTaskItemDTO.status` 的使用方支持新状态。
- 修改：`frontend/src/pages/CreateTaskPage.tsx`
  - 模板模式确认文案说明创建后按发送策略发送。
  - LLM 模式确认文案说明需要生成草稿并人工审核。
- 修改：`frontend/src/pages/TasksPage.tsx`
  - 批量任务卡片和详情展示正在生成草稿、草稿生成失败。
  - 注意数量包含 `draft_failed_count`。

### 测试

- 修改：`backend/test/test_runtime_settings_api.py`
- 修改：`backend/test/test_database_schema.py`
- 修改：`backend/test/test_api_endpoints.py`
- 新增：`backend/test/test_batch_draft_generation_runtime.py`
- 修改：`frontend/test/OtherSettingsCard.test.tsx`
- 修改：`frontend/test/TasksPageLayout.test.tsx`
- 修改：`frontend/test/CreateTaskPageCopy.test.tsx`

---

## 任务 1：运行时设置与数据库字段

**文件：**

- 修改：`backend/test/test_runtime_settings_api.py`
- 修改：`backend/test/test_database_schema.py`
- 修改：`backend/app/models/app_setting.py`
- 修改：`backend/app/schemas/runtime_settings.py`
- 修改：`backend/app/services/runtime_settings.py`
- 修改：`backend/app/models/email_task.py`
- 新增：`backend/alembic/versions/f6b2c9d8a1e4_add_batch_draft_generation_runtime.py`
- 修改：`frontend/src/lib/api/runtimeSettings.ts`
- 修改：`frontend/test/OtherSettingsCard.test.tsx`

- [ ] **步骤 1：编写后端设置失败测试**

在 `backend/test/test_runtime_settings_api.py` 的 `test_get_runtime_settings_returns_defaults` 中增加：

```python
self.assertEqual(payload["batch_draft_generation_concurrency"], 3)
```

在 `test_patch_runtime_settings_updates_values_and_records_log` 请求体中增加：

```python
"batch_draft_generation_concurrency": 5,
```

并增加响应断言：

```python
self.assertEqual(response.json()["batch_draft_generation_concurrency"], 5)
```

新增越界测试：

```python
def test_patch_runtime_settings_rejects_batch_draft_concurrency_out_of_range(self) -> None:
    response = self.client.patch(
        "/api/runtime-settings",
        json={
            "match_analysis_job_worker_count": 1,
            "match_analysis_job_item_concurrency": 4,
            "match_analysis_job_interval_seconds": 5,
            "crawler_worker_count": 3,
            "crawler_profile_enrichment_concurrency": 4,
            "crawler_host_concurrency": 2,
            "draft_max_tokens": 4800,
            "batch_draft_generation_concurrency": 0,
            "draft_rewrite_intensity": "moderate",
            "draft_rewrite_tone": "polite",
            "draft_rewrite_formality": "balanced",
            "draft_rewrite_length": "default",
            "draft_rewrite_specificity": "balanced",
            "draft_template_preservation": "structure_first",
        },
    )

    self.assertEqual(response.status_code, 422)
```

- [ ] **步骤 2：运行后端设置测试确认失败**

运行：

```powershell
rtk uv run python -m unittest test/test_runtime_settings_api.py
```

工作目录：`backend`

预期：失败，响应缺少 `batch_draft_generation_concurrency`。

- [ ] **步骤 3：编写数据库 schema 失败测试**

在 `backend/test/test_database_schema.py::test_runtime_tables_and_columns_are_created` 中加入：

```python
self.assertIn("draft_generation_previous_status", task_columns)
```

并把 `batch_draft_generation_concurrency` 加入 `settings_columns` 的 `issubset` 集合：

```python
"batch_draft_generation_concurrency",
```

- [ ] **步骤 4：运行数据库 schema 测试确认失败**

运行：

```powershell
rtk uv run python -m unittest test/test_database_schema.py
```

工作目录：`backend`

预期：失败，列不存在。

- [ ] **步骤 5：实现模型、schema 和序列化**

在 `backend/app/models/app_setting.py` 中新增：

```python
batch_draft_generation_concurrency: Mapped[int] = mapped_column(
    Integer,
    nullable=False,
    server_default=text("3"),
)
```

在 `backend/app/schemas/runtime_settings.py` 中加入：

```python
batch_draft_generation_concurrency: int
```

到 `RuntimeSettingsRead`，并加入：

```python
batch_draft_generation_concurrency: int = Field(ge=1, le=20)
```

到 `RuntimeSettingsUpdate`。

在 `backend/app/services/runtime_settings.py::serialize_runtime_settings()` 中加入：

```python
batch_draft_generation_concurrency=settings.batch_draft_generation_concurrency,
```

在 `backend/app/models/email_task.py` 中扩展状态：

```python
class EmailTaskStatus(StrEnum):
    DISCOVERED = "discovered"
    MATCHED = "matched"
    GENERATING_DRAFT = "generating_draft"
    DRAFT_FAILED = "draft_failed"
    REVIEW_REQUIRED = "review_required"
    APPROVED = "approved"
    SCHEDULED = "scheduled"
    SENT = "sent"
    SEND_FAILED = "send_failed"
    REPLY_DETECTED = "reply_detected"
    CANCELED = "canceled"
```

并在 `EmailTask` 上新增：

```python
draft_generation_previous_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
```

- [ ] **步骤 6：新增 Alembic migration**

创建 `backend/alembic/versions/f6b2c9d8a1e4_add_batch_draft_generation_runtime.py`：

```python
"""add batch draft generation runtime

Revision ID: f6b2c9d8a1e4
Revises: e8f2a4b6c9d0
Create Date: 2026-05-07 00:00:00.000000
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "f6b2c9d8a1e4"
down_revision: Union[str, Sequence[str], None] = "e8f2a4b6c9d0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("app_settings", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "batch_draft_generation_concurrency",
                sa.Integer(),
                server_default=sa.text("3"),
                nullable=False,
            ),
        )
    with op.batch_alter_table("email_tasks", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("draft_generation_previous_status", sa.String(length=32), nullable=True),
        )


def downgrade() -> None:
    with op.batch_alter_table("email_tasks", schema=None) as batch_op:
        batch_op.drop_column("draft_generation_previous_status")
    with op.batch_alter_table("app_settings", schema=None) as batch_op:
        batch_op.drop_column("batch_draft_generation_concurrency")
```

- [ ] **步骤 7：扩展前端 DTO 和 mock**

在 `frontend/src/lib/api/runtimeSettings.ts` 的 `RuntimeSettingsDTO` 中加入：

```typescript
batch_draft_generation_concurrency: number;
```

在 `frontend/test/OtherSettingsCard.test.tsx` 的 `getRuntimeSettings` mock 和保存返回值中确保包含：

```typescript
batch_draft_generation_concurrency: 3,
```

- [ ] **步骤 8：运行设置和 schema 测试验证通过**

运行：

```powershell
rtk uv run python -m unittest test/test_runtime_settings_api.py test/test_database_schema.py
```

工作目录：`backend`

预期：`OK`。

- [ ] **步骤 9：Commit**

```powershell
rtk git add backend/app/models/app_setting.py backend/app/schemas/runtime_settings.py backend/app/services/runtime_settings.py backend/app/models/email_task.py backend/alembic/versions/f6b2c9d8a1e4_add_batch_draft_generation_runtime.py backend/test/test_runtime_settings_api.py backend/test/test_database_schema.py frontend/src/lib/api/runtimeSettings.ts frontend/test/OtherSettingsCard.test.tsx
rtk git commit -m "feat(设置): 添加批量草稿生成运行时字段"
```

---

## 任务 2：批量任务 DTO、模板模式直通和任务统计

**文件：**

- 修改：`backend/test/test_api_endpoints.py`
- 修改：`backend/app/schemas/batch_task.py`
- 修改：`backend/app/api/batch_tasks.py`
- 修改：`frontend/src/types/index.ts`
- 修改：`frontend/test/TasksPageLayout.test.tsx`

- [ ] **步骤 1：编写批量任务统计失败测试**

在 `backend/test/test_api_endpoints.py` 的批量任务创建或列表测试中，新增一个场景：手动把同一批量任务下的子任务状态改成 `generating_draft` 和 `draft_failed` 后，读取 `/api/batch-tasks`。

核心断言：

```python
self.assertEqual(task_payload["generating_draft_count"], 1)
self.assertEqual(task_payload["draft_failed_count"], 1)
self.assertEqual(task_payload["pending_generation_count"], 1)
```

其中 `pending_generation_count` 只统计仍为 `discovered` 或 `matched` 的任务。

- [ ] **步骤 2：编写模板模式直通失败测试**

在 `backend/test/test_api_endpoints.py` 新增：

```python
def test_template_batch_task_creates_approved_items_without_review(self) -> None:
    response = self.client.post(
        "/api/batch-tasks",
        json={
            "identity_id": identity_id,
            "llm_profile_id": llm_profile_id,
            "name": "模板批量任务",
            "professor_ids": [professor_id],
            "schedule_type": "immediate",
            "outreach_generation_mode": "template",
            "outreach_template_subject": "发送给{{name}}",
            "outreach_template_body_text": "{{name}}老师您好，我是{{sender_name}}。",
            "outreach_template_body_html": "<p>{{name}}老师您好，我是{{sender_name}}。</p>",
        },
    )

    self.assertEqual(response.status_code, 201, msg=response.text)
    task_id = response.json()["id"]
    items = self.client.get(f"/api/batch-tasks/{task_id}/items").json()
    self.assertEqual(items[0]["status"], "approved")
```

使用同文件现有 helper 创建 `identity_id`、`llm_profile_id` 和 `professor_id`。

- [ ] **步骤 3：运行 API 测试确认失败**

运行：

```powershell
rtk uv run python -m unittest test/test_api_endpoints.py
```

工作目录：`backend`

预期：失败，缺少统计字段，模板模式子任务仍是 `discovered`。

- [ ] **步骤 4：实现后端 DTO 与序列化**

在 `backend/app/schemas/batch_task.py::BatchTaskCardRead` 中新增：

```python
generating_draft_count: int
draft_failed_count: int
```

在 `backend/app/api/batch_tasks.py::_serialize_batch_task()` 中：

```python
pending_generation_count = sum(
    status_counter.get(item, 0)
    for item in [
        EmailTaskStatus.DISCOVERED.value,
        EmailTaskStatus.MATCHED.value,
    ]
)
```

保持不包含 `generating_draft` 和 `draft_failed`，并在返回值加入：

```python
generating_draft_count=status_counter.get(EmailTaskStatus.GENERATING_DRAFT.value, 0),
draft_failed_count=status_counter.get(EmailTaskStatus.DRAFT_FAILED.value, 0),
```

- [ ] **步骤 5：实现模板模式创建为 approved**

在 `backend/app/api/batch_tasks.py` 中导入：

```python
from app.services.outreach_templates import (
    OUTREACH_GENERATION_MODE_TEMPLATE,
    build_template_context,
    get_outreach_template_defaults_validation_error,
    render_template_with_context,
    resolve_outreach_template_config,
)
```

合并到现有 `from app.services.outreach_templates import (...)` 导入块。

创建每个 `EmailTask` 前，对模板模式计算：

```python
if outreach_config.generation_mode == OUTREACH_GENERATION_MODE_TEMPLATE:
    context = build_template_context(identity, professor)
    generated_subject = render_template_with_context(
        _normalize_nullable_text(outreach_config.subject_template) or "",
        context,
    ).strip()
    generated_body_text = render_template_with_context(
        _normalize_nullable_text(outreach_config.body_text_template) or "",
        context,
    ).strip()
    generated_body_html = (
        render_template_with_context(outreach_config.body_html_template, context)
        if outreach_config.body_html_template
        else None
    )
    task_status = EmailTaskStatus.APPROVED.value
    approved_at = datetime.now(UTC)
else:
    generated_subject = None
    generated_body_text = None
    generated_body_html = None
    task_status = EmailTaskStatus.DISCOVERED.value
    approved_at = None
```

构造 `EmailTask` 时设置：

```python
status=task_status,
generated_subject=generated_subject,
generated_content_text=generated_body_text,
generated_content_html=generated_body_html,
approved_subject=generated_subject,
approved_body_text=generated_body_text,
approved_body_html=generated_body_html,
approved_at=approved_at,
```

- [ ] **步骤 6：扩展前端类型与测试 fixture**

在 `frontend/src/types/index.ts::BatchTaskCardDTO` 中新增：

```typescript
generating_draft_count: number;
draft_failed_count: number;
```

在 `frontend/test/TasksPageLayout.test.tsx` 的 `BatchTaskCardDTO` fixture 中加入：

```typescript
generating_draft_count: 0,
draft_failed_count: 0,
```

- [ ] **步骤 7：运行 API 测试验证通过**

运行：

```powershell
rtk uv run python -m unittest test/test_api_endpoints.py
```

工作目录：`backend`

预期：`OK`。

- [ ] **步骤 8：Commit**

```powershell
rtk git add backend/app/schemas/batch_task.py backend/app/api/batch_tasks.py backend/test/test_api_endpoints.py frontend/src/types/index.ts frontend/test/TasksPageLayout.test.tsx
rtk git commit -m "feat(批量任务): 区分草稿生成状态与模板直通"
```

---

## 任务 3：批量 LLM 草稿 worker、恢复和主动取消

**文件：**

- 新增：`backend/test/test_batch_draft_generation_runtime.py`
- 新增：`backend/app/services/batch_draft_generation_runtime.py`
- 修改：`backend/app/services/task_runtime.py`
- 修改：`backend/app/services/runtime_manager.py`
- 修改：`backend/app/api/batch_tasks.py`

- [ ] **步骤 1：编写并发限制失败测试**

创建 `backend/test/test_batch_draft_generation_runtime.py`，使用临时 SQLite 和 `asyncio.run()` 风格参考 `backend/test/test_batch_task_dispatch_schedule.py`。

新增测试函数：

```python
def test_run_queued_batch_drafts_limits_llm_concurrency(self) -> None:
    max_seen = self._run_async(self._run_two_llm_tasks_and_measure_concurrency(concurrency=1))
    self.assertEqual(max_seen, 1)
```

测试中 patch：

```python
with patch(
    "app.services.task_runtime.llm_runtime.generate_draft_content",
    new=AsyncMock(side_effect=fake_generate),
):
    await run_queued_batch_drafts_once(
        self.session_factory,
        concurrency=1,
        coordinator=BatchDraftGenerationCoordinator(),
    )
```

`fake_generate` 使用 `asyncio.Event` 和计数器记录同时运行数量。

- [ ] **步骤 2：编写恢复和失败语义失败测试**

在同文件新增：

```python
def test_recover_stale_generating_draft_restores_previous_status(self) -> None:
    restored = self._run_async(self._recover_stale_task(previous_status="matched"))
    self.assertEqual(restored.status, EmailTaskStatus.MATCHED.value)
    self.assertIsNone(restored.draft_generation_previous_status)
```

新增：

```python
def test_llm_failure_marks_draft_failed_without_retry(self) -> None:
    task = self._run_async(self._run_task_with_llm_runtime_error())
    self.assertEqual(task.status, EmailTaskStatus.DRAFT_FAILED.value)
    self.assertIn("LLM", task.last_error)
```

新增：

```python
def test_draft_failed_is_not_claimed_again(self) -> None:
    processed = self._run_async(self._run_worker_for_existing_status(EmailTaskStatus.DRAFT_FAILED.value))
    self.assertEqual(processed, 0)
```

- [ ] **步骤 3：编写暂停/停止晚到结果失败测试**

新增：

```python
def test_paused_batch_discards_late_llm_result(self) -> None:
    task = self._run_async(self._pause_batch_while_llm_is_running())
    self.assertIn(task.status, {EmailTaskStatus.DISCOVERED.value, EmailTaskStatus.MATCHED.value})
    self.assertIsNone(task.generated_subject)
```

新增：

```python
def test_stopped_batch_discards_late_llm_result_and_cancels_task(self) -> None:
    task = self._run_async(self._stop_batch_while_llm_is_running())
    self.assertEqual(task.status, EmailTaskStatus.CANCELED.value)
    self.assertEqual(task.cancellation_reason, EmailTaskCancellationReason.BATCH_STOPPED.value)
    self.assertIsNone(task.generated_subject)
```

- [ ] **步骤 4：运行 worker 测试确认失败**

运行：

```powershell
rtk uv run python -m unittest test/test_batch_draft_generation_runtime.py
```

工作目录：`backend`

预期：失败，模块不存在。

- [ ] **步骤 5：实现运行登记表和恢复函数**

创建 `backend/app/services/batch_draft_generation_runtime.py`：

```python
from __future__ import annotations

import asyncio
from collections import defaultdict
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import selectinload

from app.models import BatchTask, BatchTaskStatus, EmailTask, EmailTaskCancellationReason, EmailTaskSource, EmailTaskStatus
from app.services.task_runtime import generate_task_draft


STALE_GENERATING_DRAFT_AFTER = timedelta(minutes=30)


class BatchDraftGenerationCoordinator:
    def __init__(self) -> None:
        self._tasks_by_batch_id: dict[int, set[asyncio.Task[object]]] = defaultdict(set)

    @asynccontextmanager
    async def track(self, batch_task_id: int, task: asyncio.Task[object]):
        self._tasks_by_batch_id[batch_task_id].add(task)
        try:
            yield
        finally:
            tasks = self._tasks_by_batch_id.get(batch_task_id)
            if tasks is not None:
                tasks.discard(task)
                if not tasks:
                    self._tasks_by_batch_id.pop(batch_task_id, None)

    def cancel_batch(self, batch_task_id: int) -> None:
        for task in list(self._tasks_by_batch_id.get(batch_task_id, set())):
            task.cancel()
```

添加恢复函数：

```python
async def recover_stale_generating_drafts(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    now: datetime | None = None,
) -> int:
    resolved_now = now or datetime.now(UTC)
    cutoff = resolved_now - STALE_GENERATING_DRAFT_AFTER
    async with session_factory() as session:
        tasks = list(
            await session.scalars(
                select(EmailTask)
                .options(selectinload(EmailTask.batch_task))
                .where(
                    EmailTask.status == EmailTaskStatus.GENERATING_DRAFT.value,
                    EmailTask.updated_at < cutoff,
                )
            )
        )
        for task in tasks:
            if task.batch_task and task.batch_task.status == BatchTaskStatus.STOPPED.value:
                task.status = EmailTaskStatus.CANCELED.value
                task.cancellation_reason = EmailTaskCancellationReason.BATCH_STOPPED.value
            else:
                task.status = task.draft_generation_previous_status or EmailTaskStatus.DISCOVERED.value
            task.draft_generation_previous_status = None
            task.updated_at = resolved_now
        await session.commit()
        return len(tasks)
```

- [ ] **步骤 6：实现领取和并发执行**

在同文件新增：

```python
async def run_queued_batch_drafts_once(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    concurrency: int,
    coordinator: BatchDraftGenerationCoordinator,
) -> int:
    await recover_stale_generating_drafts(session_factory)
    claimed = await _claim_queued_llm_drafts(session_factory, limit=max(concurrency, 1) * 2)
    semaphore = asyncio.Semaphore(max(concurrency, 1))

    async def run_claimed(task_id: int, batch_task_id: int) -> None:
        async with semaphore:
            task = asyncio.create_task(
                generate_task_draft(
                    session_factory,
                    task_id,
                    force=True,
                    automatic_batch=True,
                    require_running_batch=True,
                )
            )
            async with coordinator.track(batch_task_id, task):
                await task

    await asyncio.gather(*(run_claimed(task_id, batch_id) for task_id, batch_id in claimed), return_exceptions=False)
    return len(claimed)
```

实现 `_claim_queued_llm_drafts()`：

```python
async def _claim_queued_llm_drafts(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    limit: int,
) -> list[tuple[int, int]]:
    async with session_factory() as session:
        candidates = list(
            await session.scalars(
                select(EmailTask)
                .options(selectinload(EmailTask.batch_task))
                .join(BatchTask, EmailTask.batch_task_id == BatchTask.id)
                .where(
                    EmailTask.source == EmailTaskSource.BATCH.value,
                    EmailTask.status.in_([EmailTaskStatus.DISCOVERED.value, EmailTaskStatus.MATCHED.value]),
                    EmailTask.outreach_generation_mode == "llm",
                    BatchTask.status == BatchTaskStatus.RUNNING.value,
                )
                .order_by(BatchTask.created_at.asc(), EmailTask.created_at.asc(), EmailTask.id.asc())
                .limit(limit)
            )
        )
        claimed: list[tuple[int, int]] = []
        now = datetime.now(UTC)
        for task in candidates:
            task.draft_generation_previous_status = task.status
            task.status = EmailTaskStatus.GENERATING_DRAFT.value
            task.updated_at = now
            if task.batch_task_id is not None:
                claimed.append((task.id, task.batch_task_id))
        await session.commit()
        return claimed
```

- [ ] **步骤 7：扩展 generate_task_draft 自动批量模式**

在 `backend/app/services/task_runtime.py::generate_task_draft()` 签名中新增：

```python
automatic_batch: bool = False,
require_running_batch: bool = False,
```

在加载任务后，如果 `task.status == EmailTaskStatus.GENERATING_DRAFT.value` 且不是自动批量模式，不允许手动重复生成：

```python
if task.status == EmailTaskStatus.GENERATING_DRAFT.value and not automatic_batch:
    raise ValueError("草稿正在后台生成，请稍后刷新")
```

在缺默认材料处，自动批量模式直接抛错：

```python
if task.primary_material is None:
    raise ValueError("请先选择用于匹配的默认材料")
```

在捕获异常时：

```python
except (llm_runtime.LLMRuntimeError, ValueError) as exc:
    task.last_error = str(exc)
    if automatic_batch:
        task.status = EmailTaskStatus.DRAFT_FAILED.value
        task.draft_generation_previous_status = None
    task.updated_at = datetime.now(UTC)
    await session.commit()
    if isinstance(exc, ValueError) and not automatic_batch:
        raise
    return task.professor_id, task.identity_id, task.llm_profile_id
```

在 LLM 返回后、写入草稿前刷新批量任务状态：

```python
if require_running_batch and task.batch_task_id is not None:
    await session.refresh(task, attribute_names=["batch_task"])
    if task.batch_task is None or task.batch_task.status != BatchTaskStatus.RUNNING.value:
        task.status = (
            task.draft_generation_previous_status
            if task.batch_task and task.batch_task.status == BatchTaskStatus.PAUSED.value
            else EmailTaskStatus.CANCELED.value
        )
        if task.status == EmailTaskStatus.CANCELED.value:
            task.cancellation_reason = EmailTaskCancellationReason.BATCH_STOPPED.value
        task.draft_generation_previous_status = None
        task.updated_at = datetime.now(UTC)
        await session.commit()
        return task.professor_id, task.identity_id, task.llm_profile_id
```

成功写入时清空：

```python
task.draft_generation_previous_status = None
```

操作日志 metadata 增加：

```python
"prompt_tokens": usage.prompt_tokens if usage is not None else None,
"completion_tokens": usage.completion_tokens if usage is not None else None,
"cached_tokens": usage.cached_tokens if usage is not None else None,
"total_tokens": usage.total_tokens if usage is not None else None,
```

- [ ] **步骤 8：接入 RuntimeManager**

在 `backend/app/services/runtime_manager.py` 中导入：

```python
from app.services.batch_draft_generation_runtime import (
    BatchDraftGenerationCoordinator,
    run_queued_batch_drafts_once,
)
```

在 `RuntimeManager.__init__()` 中：

```python
self._batch_draft_coordinator = BatchDraftGenerationCoordinator()
```

在 `start()` 的 `_tasks` 中加入：

```python
asyncio.create_task(
    self._loop(
        "batch-draft-worker",
        settings.dispatcher_interval_seconds,
        _run_batch_draft_worker_once,
    ),
),
```

新增方法：

```python
def cancel_batch_draft_generation(self, batch_task_id: int) -> None:
    self._batch_draft_coordinator.cancel_batch(batch_task_id)
```

新增 worker 函数：

```python
async def _run_batch_draft_worker_once(
    session_factory: async_sessionmaker[AsyncSession],
    coordinator: BatchDraftGenerationCoordinator,
) -> int:
    async with session_factory() as session:
        runtime_settings = await get_runtime_settings(session)
    return await run_queued_batch_drafts_once(
        session_factory,
        concurrency=runtime_settings.batch_draft_generation_concurrency,
        coordinator=coordinator,
    )
```

当前 `_loop` 只接受 `Callable[[async_sessionmaker[AsyncSession]], Awaitable[int]]`。不要修改 `_loop` 签名；在 `start()` 内创建闭包：

```python
async def run_batch_draft_worker(session_factory: async_sessionmaker[AsyncSession]) -> int:
    async with session_factory() as session:
        runtime_settings = await get_runtime_settings(session)
    return await run_queued_batch_drafts_once(
        session_factory,
        concurrency=runtime_settings.batch_draft_generation_concurrency,
        coordinator=self._batch_draft_coordinator,
    )
```

然后把该闭包传给 `_loop()`：

```python
asyncio.create_task(
    self._loop(
        "batch-draft-worker",
        settings.dispatcher_interval_seconds,
        run_batch_draft_worker,
    ),
),
```

- [ ] **步骤 9：pause/stop 通知运行时取消**

在 `backend/app/api/batch_tasks.py` 中让 `pause_batch_task()` 和 `stop_batch_task()` 接收 `request: Request`。

提交数据库状态后调用：

```python
runtime_manager = getattr(request.app.state, "runtime_manager", None)
if runtime_manager is not None:
    runtime_manager.cancel_batch_draft_generation(task_id)
```

在 `stop_batch_task()` 的状态更新集合中确保 `generating_draft` 和 `draft_failed` 会按现有逻辑取消，`sent/reply_detected/send_failed` 保留。

- [ ] **步骤 10：运行 worker 测试验证通过**

运行：

```powershell
rtk uv run python -m unittest test/test_batch_draft_generation_runtime.py
```

工作目录：`backend`

预期：`OK`。

- [ ] **步骤 11：运行相关 API 测试**

运行：

```powershell
rtk uv run python -m unittest test/test_api_endpoints.py
```

工作目录：`backend`

预期：`OK`。

- [ ] **步骤 12：Commit**

```powershell
rtk git add backend/app/services/batch_draft_generation_runtime.py backend/app/services/task_runtime.py backend/app/services/runtime_manager.py backend/app/api/batch_tasks.py backend/test/test_batch_draft_generation_runtime.py backend/test/test_api_endpoints.py
rtk git commit -m "feat(批量草稿): 添加后台生成与取消恢复"
```

---

## 任务 4：前端其他设置和批量任务状态展示

**文件：**

- 修改：`frontend/test/OtherSettingsCard.test.tsx`
- 修改：`frontend/test/TasksPageLayout.test.tsx`
- 修改：`frontend/test/CreateTaskPageCopy.test.tsx`
- 修改：`frontend/src/components/molecules/OtherSettingsCard.tsx`
- 修改：`frontend/src/types/index.ts`
- 修改：`frontend/src/pages/TasksPage.tsx`
- 修改：`frontend/src/pages/CreateTaskPage.tsx`

- [ ] **步骤 1：编写其他设置失败测试**

在 `frontend/test/OtherSettingsCard.test.tsx` 的第一个测试中增加：

```typescript
expect(screen.getByLabelText("批量邮件 LLM 草稿并发数")).toHaveValue(3);
```

修改后保存：

```typescript
fireEvent.change(screen.getByLabelText("批量邮件 LLM 草稿并发数"), {
  target: { value: "6" },
});
```

保存断言加入：

```typescript
batch_draft_generation_concurrency: 6,
```

- [ ] **步骤 2：编写任务页失败测试**

在 `frontend/test/TasksPageLayout.test.tsx` 的 running fixture 加入：

```typescript
generating_draft_count: 1,
draft_failed_count: 1,
```

新增断言：

```typescript
expect(await screen.findByText("生成中 1")).toBeInTheDocument();
expect(screen.getByText("草稿失败 1")).toBeInTheDocument();
```

- [ ] **步骤 3：编写创建页确认文案失败测试**

在 `frontend/test/CreateTaskPageCopy.test.tsx` 新增或扩展测试：选择模板模式后点击创建，确认弹窗包含：

```typescript
expect(screen.getByText(/直接套用模板/)).toBeInTheDocument();
expect(screen.getByText(/创建后会按发送策略发送/)).toBeInTheDocument();
```

LLM 模式确认弹窗包含：

```typescript
expect(screen.getByText(/生成草稿并人工审核/)).toBeInTheDocument();
```

- [ ] **步骤 4：运行前端测试确认失败**

运行：

```powershell
rtk npm --prefix frontend test -- OtherSettingsCard TasksPageLayout CreateTaskPageCopy
```

预期：失败，UI 未展示新字段或新文案。

- [ ] **步骤 5：实现其他设置 UI**

在 `frontend/src/components/molecules/OtherSettingsCard.tsx` 的 `numberFields` 中加入：

```typescript
{
  key: "batch_draft_generation_concurrency",
  label: "批量邮件 LLM 草稿并发数",
  hint: "后台批量生成 AI 草稿时同时执行的 LLM 请求数量，保存后下一轮任务生效。",
  min: 1,
  max: 20,
},
```

摘要中增加：

```typescript
const draftConcurrency = form.batch_draft_generation_concurrency || "3";
return `草稿 ${draftMaxTokens} / 草稿并发 ${draftConcurrency} / 偏好 ${draftMode} / 匹配 ${matchConcurrency} / 抓取 ${crawlConcurrency}`;
```

- [ ] **步骤 6：扩展前端状态类型**

在 `frontend/src/types/index.ts` 中把工作区任务状态 union 加入：

```typescript
| 'generating_draft'
| 'draft_failed'
```

在 `BatchTaskCardDTO` 中加入：

```typescript
generating_draft_count: number;
draft_failed_count: number;
```

在 `PROFESSOR_STATUS_LABELS` 中加入：

```typescript
generating_draft: '正在生成草稿',
draft_failed: '草稿生成失败',
```

- [ ] **步骤 7：实现任务页展示**

在 `frontend/src/pages/TasksPage.tsx` 的 `batchAttentionCount` 中加入：

```typescript
task.draft_failed_count
```

在批量任务卡片统计区域加入：

```tsx
{task.generating_draft_count > 0 ? (
  <span className="rounded-full bg-sky-50 px-2.5 py-1 text-xs text-sky-700">
    生成中 {task.generating_draft_count}
  </span>
) : null}
{task.draft_failed_count > 0 ? (
  <span className="rounded-full bg-red-50 px-2.5 py-1 text-xs text-red-700">
    草稿失败 {task.draft_failed_count}
  </span>
) : null}
```

在详情分组中把 `pendingBatchTaskItems` 拆成：

```typescript
const generatingDraftBatchTaskItems = useMemo(
  () => selectedBatchTaskItems.filter((item) => item.status === "generating_draft"),
  [selectedBatchTaskItems],
);
const draftFailedBatchTaskItems = useMemo(
  () => selectedBatchTaskItems.filter((item) => item.status === "draft_failed"),
  [selectedBatchTaskItems],
);
```

并在详情中新增对应区块，复用现有 item row 渲染结构。

- [ ] **步骤 8：实现创建页确认文案**

在 `frontend/src/pages/CreateTaskPage.tsx` 的 confirm description 改为根据 `taskMode` 分支：

```typescript
const confirmDescription =
  taskMode === "template"
    ? scheduleType === "scheduled"
      ? "将直接套用模板生成可发送内容，创建后会按定时发送策略发送。"
      : "将直接套用模板生成可发送内容，创建后会按立即发送策略发送。"
    : scheduleType === "scheduled"
      ? "将创建批量任务，后台生成 AI 草稿后需要人工审核，再按定时发送策略发送。"
      : "将创建批量任务，后台生成 AI 草稿后需要人工审核，再手动确认发送。";
```

传给 `confirm({ description: confirmDescription })`。

- [ ] **步骤 9：运行前端测试验证通过**

运行：

```powershell
rtk npm --prefix frontend test -- OtherSettingsCard TasksPageLayout CreateTaskPageCopy
```

预期：`PASS`。

- [ ] **步骤 10：Commit**

```powershell
rtk git add frontend/src/components/molecules/OtherSettingsCard.tsx frontend/src/types/index.ts frontend/src/pages/TasksPage.tsx frontend/src/pages/CreateTaskPage.tsx frontend/test/OtherSettingsCard.test.tsx frontend/test/TasksPageLayout.test.tsx frontend/test/CreateTaskPageCopy.test.tsx
rtk git commit -m "feat(前端): 展示批量草稿生成设置与状态"
```

---

## 任务 5：Token 记录和端到端回归

**文件：**

- 修改：`backend/test/test_token_usage_records.py`
- 修改：`backend/test/test_api_endpoints.py`
- 修改：`backend/app/services/task_runtime.py`

- [ ] **步骤 1：编写操作日志 token 失败测试**

在 `backend/test/test_api_endpoints.py` 找到现有草稿生成 token 断言附近，增加对操作日志 metadata 的校验：

```python
logs = self.client.get(
    "/api/diagnostics/operation-logs",
    params={"event_name": "email_task.draft_generated"},
)
self.assertEqual(logs.status_code, 200, msg=logs.text)
metadata = logs.json()["items"][0]["metadata"]
self.assertEqual(metadata["prompt_tokens"], 612)
self.assertEqual(metadata["completion_tokens"], 248)
self.assertEqual(metadata["total_tokens"], 860)
```

使用测试中实际 mock usage 数字。

- [ ] **步骤 2：编写批量草稿进入 Token 用量中心测试**

在 `backend/test/test_token_usage_records.py` 新增或扩展草稿记录测试：创建 `EmailLog(direction="draft")` 且 `provider_payload.usage` 带 token，关联 batch task 的 email task 后，调用 `list_token_usage_records()`。

核心断言：

```python
self.assertIn("draft_generation", [record.feature_type for record in result.records])
self.assertEqual(result.summary.total_tokens, 860)
```

- [ ] **步骤 3：运行 token 测试确认失败**

运行：

```powershell
rtk uv run python -m unittest test/test_api_endpoints.py test/test_token_usage_records.py
```

工作目录：`backend`

预期：操作日志 token 断言失败。

- [ ] **步骤 4：补齐操作日志 token metadata**

在 `backend/app/services/task_runtime.py` 的 `email_task.draft_generated` metadata 中加入：

```python
"prompt_tokens": usage.prompt_tokens if usage is not None else None,
"completion_tokens": usage.completion_tokens if usage is not None else None,
"cached_tokens": usage.cached_tokens if usage is not None else None,
"total_tokens": usage.total_tokens if usage is not None else None,
```

保留该片段，确保操作日志 metadata 与测试断言一致。

- [ ] **步骤 5：运行 token 测试验证通过**

运行：

```powershell
rtk uv run python -m unittest test/test_api_endpoints.py test/test_token_usage_records.py
```

工作目录：`backend`

预期：`OK`。

- [ ] **步骤 6：Commit**

```powershell
rtk git add backend/app/services/task_runtime.py backend/test/test_api_endpoints.py backend/test/test_token_usage_records.py
rtk git commit -m "test(草稿): 覆盖批量草稿 token 记录"
```

---

## 任务 6：最终验证

**文件：**

- 不新增业务文件。
- 验证当前分支所有相关改动。

- [ ] **步骤 1：运行后端定向测试**

运行：

```powershell
rtk uv run python -m unittest test/test_runtime_settings_api.py test/test_database_schema.py test/test_api_endpoints.py test/test_batch_draft_generation_runtime.py test/test_token_usage_records.py
```

工作目录：`backend`

预期：`OK`。

- [ ] **步骤 2：运行前端定向测试**

运行：

```powershell
rtk npm --prefix frontend test -- OtherSettingsCard TasksPageLayout CreateTaskPageCopy
```

工作目录：仓库根目录。

预期：`PASS`。

- [ ] **步骤 3：运行前端 lint**

运行：

```powershell
rtk npm --prefix frontend run lint
```

工作目录：仓库根目录。

预期：退出码 `0`。

- [ ] **步骤 4：检查 Alembic head**

运行：

```powershell
rtk uv run alembic heads
```

工作目录：`backend`

预期：只显示一个 head：`f6b2c9d8a1e4 (head)`。

- [ ] **步骤 5：检查 diff**

运行：

```powershell
rtk git status --short
rtk git diff --stat
```

工作目录：仓库根目录。

预期：没有未提交改动；如果有，只包含本功能相关文件。

- [ ] **步骤 6：最终修正提交**

如果最终验证产生小修，按相关任务归属提交：

```powershell
rtk git add <changed-files>
rtk git commit -m "fix(批量草稿): 完善验证发现的问题"
```

---

## 自检

- 规格中的设置项已覆盖：任务 1 和任务 4。
- `generating_draft`、`draft_failed`、`draft_generation_previous_status` 已覆盖：任务 1、任务 2、任务 3、任务 4。
- 模板模式不调用 LLM 且不需要审核已覆盖：任务 2 和任务 4。
- LLM 模式后台生成、并发限制、恢复、取消和失败处理已覆盖：任务 3。
- 暂停/停止不落库晚到结果已覆盖：任务 3。
- 批量任务统计和前端可见性已覆盖：任务 2 和任务 4。
- Token 用量中心与操作日志 token 已覆盖：任务 5。
- 验证命令已覆盖：任务 6。
