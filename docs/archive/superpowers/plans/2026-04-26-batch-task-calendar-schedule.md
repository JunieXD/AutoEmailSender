# 批量任务日历式定时发送实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 为批量任务新增“日期范围初选 + 日历点击微调 + 最终日期数组 + 动态派发”的定时发送能力。

**架构：** 前端只把用户最终在日历上选中的日期数组提交给后端，日期范围和快捷规则只作为前端初选工具。后端在 `batch_tasks` 保存排序去重后的 `scheduled_dates`，dispatcher 每轮动态判断当前日期、当前时间窗口和当天发送数量，不提前为批量子任务固化 `scheduled_at`。

**技术栈：** React + TypeScript + Vite、FastAPI、SQLAlchemy、Alembic、unittest、uv。

---

## 文件结构

- 修改：`frontend/src/features/create-task/types.ts`
  - 为旧创建任务表单类型补充 `scheduledDates?: string[]`，避免 legacy 创建页和新 DTO 语义脱节。
- 创建：`frontend/src/features/create-task/client/scheduleDates.ts`
  - 纯函数：日期格式校验、排序去重、按规则生成日期、切换日期、摘要生成。
- 创建：`frontend/src/features/create-task/client/scheduleDates.test.ts`
  - 覆盖工作日生成、范围外添加、切换、排序去重。
- 创建：`frontend/src/components/molecules/TaskDateSelector.tsx`
  - 日历式日期选择组件，负责初选范围、快捷规则、月份切换、日期点击切换和范围外日期添加。
- 修改：`frontend/src/components/molecules/TaskScheduleSettings.tsx`
  - 在定时发送配置中挂载 `TaskDateSelector`，摘要改为按已选日期显示。
- 修改：`frontend/src/features/create-task/client/useCreateTaskForm.ts`
  - 管理 `scheduledDates` 状态和变更方法。
- 修改：`frontend/src/features/create-task/server/validateTaskForm.ts`
  - 定时发送时校验至少一个发送日期。
- 修改：`frontend/src/types/index.ts`
  - 为 `CreateBatchTaskRequestDTO`、`BatchTaskCardDTO` 增加 `scheduled_dates`。
- 修改：`frontend/src/data/mockData.ts`
  - 更新 mock 任务 schedule 文案，体现“日期跨度 + 总天数 + 每日最多”。
- 创建：`backend/app/services/batch_schedule.py`
  - 后端纯函数：归一化日期、校验时间、判断当前时刻是否落在批量任务发送窗口。
- 创建：`backend/test/test_batch_schedule.py`
  - 覆盖日期归一化和窗口判断。
- 创建：`backend/alembic/versions/6d7e8f9a0b12_add_batch_task_scheduled_dates.py`
  - 给 `batch_tasks` 增加 `scheduled_dates` JSON 字段。
- 修改：`backend/app/models/batch_task.py`
  - 增加 `scheduled_dates` ORM 字段。
- 修改：`backend/app/schemas/batch_task.py`
  - 请求和返回 schema 增加 `scheduled_dates`。
- 修改：`backend/app/api/batch_tasks.py`
  - 创建批量任务时校验并保存 `scheduled_dates`，序列化返回。
- 修改：`backend/app/services/task_runtime.py`
  - dispatcher 动态过滤批量任务发送日期、时间窗口和每日发送上限。
- 修改：`backend/test/test_api_endpoints.py`
  - 补充创建定时批量任务的 API 校验测试。
- 创建：`backend/test/test_batch_task_dispatch_schedule.py`
  - 覆盖 dispatcher 对选中日期、窗口和每日上限的行为。

## 任务 1：后端日期规则纯函数

**文件：**
- 创建：`backend/app/services/batch_schedule.py`
- 测试：`backend/test/test_batch_schedule.py`

- [ ] **步骤 1：编写失败的日期归一化测试**

在 `backend/test/test_batch_schedule.py` 新建：

```python
import unittest
from datetime import UTC, datetime

from app.services.batch_schedule import (
    is_datetime_in_batch_window,
    normalize_scheduled_dates,
)


class BatchScheduleTest(unittest.TestCase):
    def test_normalize_scheduled_dates_sorts_and_deduplicates_dates(self) -> None:
        result = normalize_scheduled_dates(
            ["2026-05-04", "2026-04-28", "2026-05-04"],
        )

        self.assertEqual(result, ["2026-04-28", "2026-05-04"])
```

- [ ] **步骤 2：运行测试验证失败**

运行：`cd backend; uv run python -m unittest test.test_batch_schedule.BatchScheduleTest.test_normalize_scheduled_dates_sorts_and_deduplicates_dates`

预期：FAIL，报错包含 `ModuleNotFoundError: No module named 'app.services.batch_schedule'`。

- [ ] **步骤 3：实现日期归一化函数**

创建 `backend/app/services/batch_schedule.py`：

```python
from __future__ import annotations

from datetime import date, datetime


def normalize_scheduled_dates(values: list[str] | None) -> list[str]:
    if not values:
        return []

    normalized: set[str] = set()
    for value in values:
        try:
            parsed = date.fromisoformat(value)
        except ValueError as exc:
            raise ValueError("发送日期必须使用 YYYY-MM-DD 格式") from exc
        normalized.add(parsed.isoformat())

    return sorted(normalized)


def is_datetime_in_batch_window(
    now: datetime,
    *,
    scheduled_dates: list[str] | None,
    window_start_time: str | None,
    window_end_time: str | None,
) -> bool:
    return False
```

- [ ] **步骤 4：运行测试验证通过**

运行：`cd backend; uv run python -m unittest test.test_batch_schedule.BatchScheduleTest.test_normalize_scheduled_dates_sorts_and_deduplicates_dates`

预期：PASS。

- [ ] **步骤 5：补充非法日期测试**

追加到 `BatchScheduleTest`：

```python
    def test_normalize_scheduled_dates_rejects_invalid_date(self) -> None:
        with self.assertRaisesRegex(ValueError, "YYYY-MM-DD"):
            normalize_scheduled_dates(["2026-02-30"])
```

- [ ] **步骤 6：运行日期归一化测试**

运行：`cd backend; uv run python -m unittest test.test_batch_schedule.BatchScheduleTest`

预期：PASS。

- [ ] **步骤 7：编写时间窗口测试**

追加：

```python
    def test_is_datetime_in_batch_window_requires_selected_date_and_time_window(self) -> None:
        now = datetime(2026, 5, 4, 10, 30, tzinfo=UTC)

        self.assertTrue(
            is_datetime_in_batch_window(
                now,
                scheduled_dates=["2026-05-04"],
                window_start_time="09:00",
                window_end_time="18:00",
            ),
        )
        self.assertFalse(
            is_datetime_in_batch_window(
                now,
                scheduled_dates=["2026-05-05"],
                window_start_time="09:00",
                window_end_time="18:00",
            ),
        )
        self.assertFalse(
            is_datetime_in_batch_window(
                now,
                scheduled_dates=["2026-05-04"],
                window_start_time="11:00",
                window_end_time="18:00",
            ),
        )
```

- [ ] **步骤 8：运行测试验证失败**

运行：`cd backend; uv run python -m unittest test.test_batch_schedule.BatchScheduleTest.test_is_datetime_in_batch_window_requires_selected_date_and_time_window`

预期：FAIL，返回 `False is not true`。

- [ ] **步骤 9：实现窗口判断**

替换 `is_datetime_in_batch_window`：

```python
def is_datetime_in_batch_window(
    now: datetime,
    *,
    scheduled_dates: list[str] | None,
    window_start_time: str | None,
    window_end_time: str | None,
) -> bool:
    dates = set(normalize_scheduled_dates(scheduled_dates))
    if now.date().isoformat() not in dates:
        return False
    if not window_start_time or not window_end_time:
        return False

    current = now.strftime("%H:%M")
    return window_start_time <= current < window_end_time
```

- [ ] **步骤 10：运行后端纯函数测试**

运行：`cd backend; uv run python -m unittest test.test_batch_schedule`

预期：PASS。

- [ ] **步骤 11：Commit**

```bash
git add backend/app/services/batch_schedule.py backend/test/test_batch_schedule.py
git commit -m "test(backend): add batch schedule date rules"
```

## 任务 2：后端模型、迁移和 API 校验

**文件：**
- 创建：`backend/alembic/versions/6d7e8f9a0b12_add_batch_task_scheduled_dates.py`
- 修改：`backend/app/models/batch_task.py`
- 修改：`backend/app/schemas/batch_task.py`
- 修改：`backend/app/api/batch_tasks.py`
- 测试：`backend/test/test_api_endpoints.py`

- [ ] **步骤 1：编写 API 失败测试：定时任务必须选择日期**

在 `backend/test/test_api_endpoints.py` 的批量任务 API 测试类中新增测试。复用同文件里已有创建身份、LLM、导师的 helper；如果当前测试类没有 helper，按相邻创建批量任务测试的 setup 写法构造 payload，只改 schedule 字段：

```python
    def test_create_scheduled_batch_task_requires_scheduled_dates(self) -> None:
        identity_id = self._create_identity(with_imap=False)
        llm_profile_id = self._create_llm()
        self.client.post("/api/professors/import-sample")
        professor_id = self.client.get("/api/professors").json()[0]["id"]

        response = self.client.post(
            "/api/batch-tasks",
            json={
                "identity_id": identity_id,
                "llm_profile_id": llm_profile_id,
                "name": "定时发送测试",
                "professor_ids": [professor_id],
                "schedule_type": "scheduled",
                "scheduled_dates": [],
                "window_start_time": "09:00",
                "window_end_time": "18:00",
                "emails_per_window": 20,
                "primary_material_id": None,
                "email_subject": "Hello {{导师姓名}}",
                "email_body": "Body",
                "selected_material_ids": None,
                "outreach_generation_mode": "template",
                "outreach_template_subject": "Hello {{导师姓名}}",
                "outreach_template_body_text": "Body",
                "outreach_template_body_html": None,
            },
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("发送日期", response.json()["detail"])
```

- [ ] **步骤 2：运行测试验证失败**

运行：`cd backend; uv run python -m unittest test.test_api_endpoints.ApiEndpointTests.test_create_scheduled_batch_task_requires_scheduled_dates`

预期：FAIL，实际可能为 `201 != 400` 或 schema 忽略 `scheduled_dates`。

- [ ] **步骤 3：创建 Alembic 迁移**

创建 `backend/alembic/versions/6d7e8f9a0b12_add_batch_task_scheduled_dates.py`，内容：

```python
"""add batch task scheduled dates

Revision ID: 6d7e8f9a0b12
Revises: 8a6d2f4c9b31
Create Date: 2026-04-26
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "6d7e8f9a0b12"
down_revision = "8a6d2f4c9b31"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("batch_tasks", sa.Column("scheduled_dates", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("batch_tasks", "scheduled_dates")
```

同时把 `backend/test/test_api_endpoints.py` 顶部的 `HEAD_REVISION` 改为：

```python
HEAD_REVISION = "6d7e8f9a0b12"
```

- [ ] **步骤 4：更新 ORM 模型**

在 `backend/app/models/batch_task.py` 的 `BatchTask` 中 `emails_per_window` 后加入：

```python
    scheduled_dates: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
```

- [ ] **步骤 5：更新 schema**

在 `backend/app/schemas/batch_task.py`：

```python
class CreateBatchTaskRequest(BaseModel):
    ...
    scheduled_dates: list[str] | None = None
```

在 `BatchTaskCardRead`：

```python
    scheduled_dates: list[str] | None
```

- [ ] **步骤 6：更新创建接口校验和保存**

在 `backend/app/api/batch_tasks.py` 引入：

```python
from app.services.batch_schedule import normalize_scheduled_dates
```

在创建 `BatchTask` 前加入：

```python
    scheduled_dates = normalize_scheduled_dates(payload.scheduled_dates)
    if payload.schedule_type == "scheduled":
        if not scheduled_dates:
            raise HTTPException(status_code=400, detail="请至少选择一个发送日期")
        if not payload.window_start_time or not payload.window_end_time:
            raise HTTPException(status_code=400, detail="请填写发送时间窗口")
        if payload.window_end_time <= payload.window_start_time:
            raise HTTPException(status_code=400, detail="结束时间必须晚于开始时间")
        if not payload.emails_per_window or payload.emails_per_window <= 0:
            raise HTTPException(status_code=400, detail="请输入每天发送数量")
```

创建 `BatchTask` 时加入：

```python
        scheduled_dates=scheduled_dates or None,
```

`_serialize_batch_task` 返回中加入：

```python
        scheduled_dates=task.scheduled_dates,
```

- [ ] **步骤 7：运行 API 失败测试验证通过**

运行：`cd backend; uv run python -m unittest test.test_api_endpoints.ApiEndpointTests.test_create_scheduled_batch_task_requires_scheduled_dates`

预期：PASS。

- [ ] **步骤 8：编写 API 成功测试：日期排序去重后返回**

同一测试类新增：

```python
    def test_create_scheduled_batch_task_returns_normalized_scheduled_dates(self) -> None:
        identity_id = self._create_identity(with_imap=False)
        llm_profile_id = self._create_llm()
        self.client.post("/api/professors/import-sample")
        professor_id = self.client.get("/api/professors").json()[0]["id"]

        response = self.client.post(
            "/api/batch-tasks",
            json={
                "identity_id": identity_id,
                "llm_profile_id": llm_profile_id,
                "name": "日历定时发送",
                "professor_ids": [professor_id],
                "schedule_type": "scheduled",
                "scheduled_dates": ["2026-05-04", "2026-04-28", "2026-05-04"],
                "window_start_time": "09:00",
                "window_end_time": "18:00",
                "emails_per_window": 20,
                "primary_material_id": None,
                "email_subject": "Hello {{导师姓名}}",
                "email_body": "Body",
                "selected_material_ids": None,
                "outreach_generation_mode": "template",
                "outreach_template_subject": "Hello {{导师姓名}}",
                "outreach_template_body_text": "Body",
                "outreach_template_body_html": None,
            },
        )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()["scheduled_dates"], ["2026-04-28", "2026-05-04"])
```

- [ ] **步骤 9：运行 API 成功测试**

运行：`cd backend; uv run python -m unittest test.test_api_endpoints.ApiEndpointTests.test_create_scheduled_batch_task_returns_normalized_scheduled_dates`

预期：PASS。

- [ ] **步骤 10：运行批量任务相关 API 测试**

运行：`cd backend; uv run python -m unittest test.test_api_endpoints`

预期：PASS。

- [ ] **步骤 11：Commit**

```bash
git add backend/alembic/versions/6d7e8f9a0b12_add_batch_task_scheduled_dates.py backend/app/models/batch_task.py backend/app/schemas/batch_task.py backend/app/api/batch_tasks.py backend/test/test_api_endpoints.py
git commit -m "feat(backend): store batch task scheduled dates"
```

## 任务 3：后端动态派发窗口和每日上限

**文件：**
- 修改：`backend/app/services/task_runtime.py`
- 测试：`backend/test/test_batch_task_dispatch_schedule.py`

- [ ] **步骤 1：编写 dispatcher 非选中日期不派发测试**

创建 `backend/test/test_batch_task_dispatch_schedule.py`。复用项目测试里的数据库 session factory 写法，构造一个 `running` 批量任务、一个 `approved` 子任务，批量任务 `scheduled_dates=["2026-05-04"]`，然后把当前时间注入为 `2026-05-05 10:00 UTC`。如果 `dispatch_due_tasks_once` 当前不支持注入 `now`，测试先按目标签名写：

```python
import unittest
from datetime import UTC, datetime

from app.services.task_runtime import dispatch_due_tasks_once


class BatchTaskDispatchScheduleTest(unittest.IsolatedAsyncioTestCase):
    async def test_dispatch_due_tasks_skips_batch_task_on_unselected_date(self) -> None:
        session_factory, task_id = await self._create_approved_batch_email_task(
            scheduled_dates=["2026-05-04"],
            window_start_time="09:00",
            window_end_time="18:00",
            emails_per_window=20,
        )

        processed = await dispatch_due_tasks_once(
            session_factory,
            now=datetime(2026, 5, 5, 10, 0, tzinfo=UTC),
        )

        self.assertEqual(processed, 0)
        task = await self._load_email_task(session_factory, task_id)
        self.assertEqual(task.status, "approved")
```

在同文件中补齐 `_create_approved_batch_email_task` 和 `_load_email_task`，按 `backend/test/test_api_endpoints.py` 已有测试数据库模式创建 `IdentityProfile`、`LLMProfile`、`Professor`、`BatchTask`、`EmailTask`。`mail_runtime.send_email` 不应被调用，因为本测试期望 `processed=0`。

- [ ] **步骤 2：运行测试验证失败**

运行：`cd backend; uv run python -m unittest test.test_batch_task_dispatch_schedule.BatchTaskDispatchScheduleTest.test_dispatch_due_tasks_skips_batch_task_on_unselected_date`

预期：FAIL，报错包含 `unexpected keyword argument 'now'`。

- [ ] **步骤 3：给 dispatcher 增加 now 注入参数**

修改 `backend/app/services/task_runtime.py`：

```python
async def dispatch_due_tasks_once(
    session_factory: async_sessionmaker[AsyncSession],
    limit: int = 10,
    *,
    now: datetime | None = None,
) -> int:
    now = now or datetime.now(UTC)
```

- [ ] **步骤 4：实现查询后过滤批量任务窗口**

在 `task_runtime.py` 引入：

```python
from app.services.batch_schedule import is_datetime_in_batch_window
```

将 `task_ids` 查询从只选 `EmailTask.id` 改为加载 `EmailTask` 及 `BatchTask`，或查询后逐个加载。筛选逻辑必须保留已有条件，并额外跳过：

```python
def _batch_task_allows_dispatch(batch_task: BatchTask | None, now: datetime) -> bool:
    if batch_task is None:
        return True
    if batch_task.status != BatchTaskStatus.RUNNING.value:
        return False
    if batch_task.schedule_type != "scheduled":
        return True
    return is_datetime_in_batch_window(
        now,
        scheduled_dates=batch_task.scheduled_dates,
        window_start_time=batch_task.window_start_time,
        window_end_time=batch_task.window_end_time,
    )
```

使用该 helper 过滤候选任务。

- [ ] **步骤 5：运行非选中日期测试**

运行：`cd backend; uv run python -m unittest test.test_batch_task_dispatch_schedule.BatchTaskDispatchScheduleTest.test_dispatch_due_tasks_skips_batch_task_on_unselected_date`

预期：PASS。

- [ ] **步骤 6：编写窗口外不派发测试**

追加：

```python
    async def test_dispatch_due_tasks_skips_batch_task_outside_time_window(self) -> None:
        session_factory, task_id = await self._create_approved_batch_email_task(
            scheduled_dates=["2026-05-04"],
            window_start_time="09:00",
            window_end_time="18:00",
            emails_per_window=20,
        )

        processed = await dispatch_due_tasks_once(
            session_factory,
            now=datetime(2026, 5, 4, 8, 59, tzinfo=UTC),
        )

        self.assertEqual(processed, 0)
        task = await self._load_email_task(session_factory, task_id)
        self.assertEqual(task.status, "approved")
```

- [ ] **步骤 7：运行窗口外测试**

运行：`cd backend; uv run python -m unittest test.test_batch_task_dispatch_schedule.BatchTaskDispatchScheduleTest.test_dispatch_due_tasks_skips_batch_task_outside_time_window`

预期：PASS。

- [ ] **步骤 8：编写每日上限测试**

追加：

```python
    async def test_dispatch_due_tasks_skips_when_daily_limit_reached(self) -> None:
        session_factory, task_id = await self._create_approved_batch_email_task(
            scheduled_dates=["2026-05-04"],
            window_start_time="09:00",
            window_end_time="18:00",
            emails_per_window=1,
            sent_count_today=1,
        )

        processed = await dispatch_due_tasks_once(
            session_factory,
            now=datetime(2026, 5, 4, 10, 0, tzinfo=UTC),
        )

        self.assertEqual(processed, 0)
        task = await self._load_email_task(session_factory, task_id)
        self.assertEqual(task.status, "approved")
```

`_create_approved_batch_email_task(..., sent_count_today=1)` 应额外创建一个同批量任务下 `sent` 状态子任务，`sent_at=datetime(2026, 5, 4, 9, 30, tzinfo=UTC)`。

- [ ] **步骤 9：运行每日上限测试验证失败**

运行：`cd backend; uv run python -m unittest test.test_batch_task_dispatch_schedule.BatchTaskDispatchScheduleTest.test_dispatch_due_tasks_skips_when_daily_limit_reached`

预期：FAIL，processed 不是 0。

- [ ] **步骤 10：实现每日上限统计**

在 `task_runtime.py` 增加 helper：

```python
async def _batch_task_sent_count_on_date(
    session: AsyncSession,
    batch_task_id: int,
    now: datetime,
) -> int:
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    end = start + timedelta(days=1)
    return int(
        await session.scalar(
            select(func.count(EmailTask.id)).where(
                EmailTask.batch_task_id == batch_task_id,
                EmailTask.status.in_(
                    [
                        EmailTaskStatus.SENT.value,
                        EmailTaskStatus.REPLY_DETECTED.value,
                    ],
                ),
                EmailTask.sent_at >= start,
                EmailTask.sent_at < end,
            ),
        )
        or 0
    )
```

同时引入：

```python
from datetime import timedelta
from sqlalchemy import func
```

在候选过滤中，如果 `batch_task.schedule_type == "scheduled"` 且 `emails_per_window` 不为空，已发送数量大于等于上限则跳过。

- [ ] **步骤 11：运行动态派发测试**

运行：`cd backend; uv run python -m unittest test.test_batch_task_dispatch_schedule`

预期：PASS。

- [ ] **步骤 12：运行后端相关测试**

运行：`cd backend; uv run python -m unittest test.test_batch_schedule test.test_batch_task_dispatch_schedule test.test_api_endpoints`

预期：PASS。

- [ ] **步骤 13：Commit**

```bash
git add backend/app/services/task_runtime.py backend/test/test_batch_task_dispatch_schedule.py
git commit -m "feat(backend): dispatch batch tasks by selected dates"
```

## 任务 4：前端日期规则纯函数

**文件：**
- 创建：`frontend/src/features/create-task/client/scheduleDates.ts`
- 创建：`frontend/src/features/create-task/client/scheduleDates.test.ts`

- [ ] **步骤 1：编写失败测试**

创建 `frontend/src/features/create-task/client/scheduleDates.test.ts`：

```ts
import { describe, expect, it } from 'vitest';
import {
  applyDateRule,
  normalizeScheduledDates,
  toggleScheduledDate,
} from './scheduleDates';

describe('scheduleDates', () => {
  it('normalizes dates by sorting and deduplicating', () => {
    expect(normalizeScheduledDates(['2026-05-04', '2026-04-28', '2026-05-04'])).toEqual([
      '2026-04-28',
      '2026-05-04',
    ]);
  });

  it('generates weekdays from a date range', () => {
    expect(applyDateRule('weekdays', '2026-05-01', '2026-05-05')).toEqual([
      '2026-05-01',
      '2026-05-04',
      '2026-05-05',
    ]);
  });

  it('toggles selected dates', () => {
    expect(toggleScheduledDate(['2026-05-04'], '2026-05-04')).toEqual([]);
    expect(toggleScheduledDate([], '2026-05-04')).toEqual(['2026-05-04']);
  });
});
```

- [ ] **步骤 2：运行测试验证失败**

运行：`cd frontend; npm run test -- scheduleDates`

如果项目没有 test 脚本，先运行：`cd frontend; npm pkg get scripts`

预期：没有 test 脚本或 FAIL，报错缺少 `./scheduleDates`。如果没有 test 脚本，在任务 4 步骤 3 同时添加 `"test": "vitest run"`。

- [ ] **步骤 3：实现纯函数**

创建 `frontend/src/features/create-task/client/scheduleDates.ts`：

```ts
export type DateRule = 'all' | 'weekdays' | 'mon-wed-fri' | 'weekends';

const isoDatePattern = /^\d{4}-\d{2}-\d{2}$/;

export const isValidIsoDate = (value: string) => {
  if (!isoDatePattern.test(value)) {
    return false;
  }
  const date = new Date(`${value}T00:00:00`);
  return !Number.isNaN(date.getTime()) && date.toISOString().slice(0, 10) === value;
};

export const normalizeScheduledDates = (dates: string[]) =>
  Array.from(new Set(dates.filter(isValidIsoDate))).sort();

const toDate = (value: string) => new Date(`${value}T00:00:00`);
const toIsoDate = (date: Date) => date.toISOString().slice(0, 10);

const matchesRule = (date: Date, rule: DateRule) => {
  const day = date.getDay();
  if (rule === 'all') {
    return true;
  }
  if (rule === 'weekdays') {
    return day >= 1 && day <= 5;
  }
  if (rule === 'mon-wed-fri') {
    return day === 1 || day === 3 || day === 5;
  }
  return day === 0 || day === 6;
};

export const applyDateRule = (rule: DateRule, startDate: string, endDate: string) => {
  if (!isValidIsoDate(startDate) || !isValidIsoDate(endDate) || startDate > endDate) {
    return [];
  }

  const dates: string[] = [];
  const cursor = toDate(startDate);
  const end = toDate(endDate);
  while (cursor <= end) {
    if (matchesRule(cursor, rule)) {
      dates.push(toIsoDate(cursor));
    }
    cursor.setDate(cursor.getDate() + 1);
  }
  return dates;
};

export const toggleScheduledDate = (dates: string[], date: string) => {
  if (!isValidIsoDate(date)) {
    return normalizeScheduledDates(dates);
  }
  if (dates.includes(date)) {
    return dates.filter((item) => item !== date);
  }
  return normalizeScheduledDates([...dates, date]);
};
```

如果没有 test 脚本，修改 `frontend/package.json`：

```json
"test": "vitest run"
```

若未安装 vitest，运行 `cd frontend; npm install -D vitest` 并提交 `package.json`、lockfile。

- [ ] **步骤 4：运行纯函数测试**

运行：`cd frontend; npm run test -- scheduleDates`

预期：PASS。

- [ ] **步骤 5：Commit**

```bash
git add frontend/src/features/create-task/client/scheduleDates.ts frontend/src/features/create-task/client/scheduleDates.test.ts frontend/package.json frontend/package-lock.json
git commit -m "test(frontend): add schedule date utilities"
```

如果没有改 `package.json` 和 lockfile，`git add` 只添加两个新文件。

## 任务 5：前端日历选择组件

**文件：**
- 创建：`frontend/src/components/molecules/TaskDateSelector.tsx`
- 修改：`frontend/src/components/molecules/TaskScheduleSettings.tsx`
- 修改：`frontend/src/features/create-task/types.ts`
- 修改：`frontend/src/features/create-task/client/useCreateTaskForm.ts`
- 修改：`frontend/src/features/create-task/server/validateTaskForm.ts`

- [ ] **步骤 1：扩展前端表单类型**

在 `frontend/src/features/create-task/types.ts` 的 `TaskScheduleConfig` 中增加：

```ts
  /** 定时发送时最终选中的发送日期，YYYY-MM-DD */
  scheduledDates?: string[];
```

- [ ] **步骤 2：更新表单 hook 状态**

在 `frontend/src/features/create-task/client/useCreateTaskForm.ts`：

```ts
  const setScheduledDates = useCallback((dates: string[]) => {
    setSchedule((prev) => ({ ...prev, scheduledDates: dates }));
  }, []);
```

`setScheduleType('scheduled')` 的默认值加入：

```ts
            scheduledDates: prev.scheduledDates ?? [],
```

return 对象中加入：

```ts
    setScheduledDates,
```

- [ ] **步骤 3：创建 `TaskDateSelector` 组件**

创建 `frontend/src/components/molecules/TaskDateSelector.tsx`：

```tsx
import { useMemo, useState } from 'react';
import clsx from 'clsx';
import {
  applyDateRule,
  normalizeScheduledDates,
  toggleScheduledDate,
  type DateRule,
} from '@/features/create-task/client/scheduleDates';

interface TaskDateSelectorProps {
  selectedDates: string[];
  onChange: (dates: string[]) => void;
}

const ruleLabels: Array<{ label: string; value: DateRule }> = [
  { label: '每天', value: 'all' },
  { label: '工作日', value: 'weekdays' },
  { label: '周一三五', value: 'mon-wed-fri' },
  { label: '周末', value: 'weekends' },
];

const toIsoDate = (date: Date) => date.toISOString().slice(0, 10);

const buildMonthDays = (monthDate: Date) => {
  const year = monthDate.getFullYear();
  const month = monthDate.getMonth();
  const firstDay = new Date(year, month, 1);
  const startOffset = (firstDay.getDay() + 6) % 7;
  const start = new Date(year, month, 1 - startOffset);
  return Array.from({ length: 42 }, (_, index) => {
    const date = new Date(start);
    date.setDate(start.getDate() + index);
    return date;
  });
};

export const TaskDateSelector: React.FC<TaskDateSelectorProps> = ({ selectedDates, onChange }) => {
  const today = toIsoDate(new Date());
  const [rangeStart, setRangeStart] = useState(today);
  const [rangeEnd, setRangeEnd] = useState(today);
  const [visibleMonth, setVisibleMonth] = useState(() => new Date(`${today}T00:00:00`));
  const normalizedDates = useMemo(() => normalizeScheduledDates(selectedDates), [selectedDates]);
  const selectedSet = useMemo(() => new Set(normalizedDates), [normalizedDates]);
  const monthDays = useMemo(() => buildMonthDays(visibleMonth), [visibleMonth]);

  const applyRule = (rule: DateRule) => {
    const dates = applyDateRule(rule, rangeStart, rangeEnd);
    onChange(dates);
    if (dates[0]) {
      setVisibleMonth(new Date(`${dates[0]}T00:00:00`));
    }
  };

  const shiftMonth = (offset: number) => {
    setVisibleMonth((prev) => new Date(prev.getFullYear(), prev.getMonth() + offset, 1));
  };

  const addDate = (date: string) => {
    onChange(toggleScheduledDate(normalizedDates, date));
    setVisibleMonth(new Date(`${date}T00:00:00`));
  };

  return (
    <div className="flex flex-col gap-4 rounded-lg border border-stone-200 bg-white p-4">
      <div className="grid gap-3 md:grid-cols-[240px_1fr]">
        <div className="flex flex-col gap-3">
          <span className="text-xs font-medium text-stone-500">快速选择日期</span>
          <div className="grid grid-cols-2 gap-2">
            <input type="date" value={rangeStart} onChange={(event) => setRangeStart(event.target.value)} className="h-9 rounded-lg border border-stone-200 px-2 text-sm" />
            <input type="date" value={rangeEnd} onChange={(event) => setRangeEnd(event.target.value)} className="h-9 rounded-lg border border-stone-200 px-2 text-sm" />
          </div>
          <div className="grid grid-cols-2 gap-2">
            {ruleLabels.map((rule) => (
              <button key={rule.value} type="button" onClick={() => applyRule(rule.value)} className="rounded-lg border border-stone-200 px-3 py-2 text-sm text-stone-600 hover:border-primary hover:text-primary">
                {rule.label}
              </button>
            ))}
            <button type="button" onClick={() => onChange([])} className="rounded-lg border border-stone-200 px-3 py-2 text-sm text-stone-600 hover:border-primary hover:text-primary">
              清空重选
            </button>
          </div>
          <input type="date" onChange={(event) => addDate(event.target.value)} className="h-9 rounded-lg border border-stone-200 px-2 text-sm" />
        </div>

        <div className="flex flex-col gap-3">
          <div className="flex items-center justify-between">
            <button type="button" onClick={() => shiftMonth(-1)} className="rounded-lg border border-stone-200 px-3 py-1.5 text-sm">上月</button>
            <span className="text-sm font-semibold text-stone-700">{visibleMonth.getFullYear()} 年 {visibleMonth.getMonth() + 1} 月 · 已选 {normalizedDates.length} 天</span>
            <button type="button" onClick={() => shiftMonth(1)} className="rounded-lg border border-stone-200 px-3 py-1.5 text-sm">下月</button>
          </div>
          <div className="grid grid-cols-7 gap-1 text-center text-xs">
            {['一', '二', '三', '四', '五', '六', '日'].map((day) => <span key={day} className="py-1 font-medium text-stone-500">{day}</span>)}
            {monthDays.map((day) => {
              const iso = toIsoDate(day);
              const inMonth = day.getMonth() === visibleMonth.getMonth();
              const selected = selectedSet.has(iso);
              return (
                <button
                  key={iso}
                  type="button"
                  onClick={() => onChange(toggleScheduledDate(normalizedDates, iso))}
                  className={clsx(
                    'h-9 rounded-lg border text-xs transition-all',
                    selected && 'border-primary bg-primary text-white',
                    !selected && inMonth && 'border-stone-200 bg-stone-50 text-stone-700 hover:border-primary',
                    !selected && !inMonth && 'border-dashed border-stone-200 bg-white text-stone-300',
                  )}
                >
                  {day.getDate()}
                </button>
              );
            })}
          </div>
        </div>
      </div>
    </div>
  );
};
```

- [ ] **步骤 4：挂载组件到定时设置**

修改 `frontend/src/components/molecules/TaskScheduleSettings.tsx`：

```tsx
import { TaskDateSelector } from './TaskDateSelector';
```

props 增加：

```ts
  onScheduledDatesChange: (dates: string[]) => void;
```

组件参数中接收 `onScheduledDatesChange`，在定时配置顶部加入：

```tsx
          <TaskDateSelector
            selectedDates={schedule.scheduledDates ?? []}
            onChange={onScheduledDatesChange}
          />
```

摘要替换为：

```tsx
          {schedule.startTime && schedule.endTime && schedule.emailsToSend && (
            <p className="text-xs text-stone-500">
              已选 {schedule.scheduledDates?.length ?? 0} 天，将在 {schedule.startTime} 至 {schedule.endTime} 之间动态发送，每天最多 {schedule.emailsToSend} 封
            </p>
          )}
```

- [ ] **步骤 5：更新调用方**

在 `frontend/src/components/organisms/CreateTaskClient.tsx` 解构加入：

```ts
    setScheduledDates,
```

传给 `TaskScheduleSettings`：

```tsx
            onScheduledDatesChange={setScheduledDates}
```

- [ ] **步骤 6：更新前端校验**

在 `frontend/src/features/create-task/server/validateTaskForm.ts` 的 scheduled 分支中加入：

```ts
    if (!data.schedule.scheduledDates?.length) {
      errors.scheduledDates = '请至少选择一个发送日期';
    }
```

- [ ] **步骤 7：运行前端校验**

运行：`cd frontend; npm run lint`

预期：PASS。

- [ ] **步骤 8：运行前端构建**

运行：`cd frontend; npm run build`

预期：PASS。

- [ ] **步骤 9：Commit**

```bash
git add frontend/src/components/molecules/TaskDateSelector.tsx frontend/src/components/molecules/TaskScheduleSettings.tsx frontend/src/components/organisms/CreateTaskClient.tsx frontend/src/features/create-task/types.ts frontend/src/features/create-task/client/useCreateTaskForm.ts frontend/src/features/create-task/server/validateTaskForm.ts
git commit -m "feat(frontend): add calendar date selector for scheduled tasks"
```

## 任务 6：前后端 DTO 和任务卡片展示

**文件：**
- 修改：`frontend/src/types/index.ts`
- 修改：`frontend/src/lib/api/createTask.ts`
- 修改：`frontend/src/lib/api/batchTasksApi.ts`
- 修改：`frontend/src/data/mockData.ts`
- 可修改：`frontend/src/components/molecules/BatchTaskCard.tsx`

- [ ] **步骤 1：更新 DTO 类型**

在 `frontend/src/types/index.ts`：

`CreateBatchTaskRequestDTO` 增加：

```ts
  scheduled_dates: string[] | null;
```

`BatchTaskCardDTO` 增加：

```ts
  scheduled_dates: string[] | null;
```

- [ ] **步骤 2：确认创建 payload 映射**

如果当前真实创建批量任务入口直接构造 `CreateBatchTaskRequestDTO`，确保 `scheduled_dates` 来源为 `schedule.scheduledDates ?? null`。如果 `frontend/src/lib/api/createTask.ts` 仍是 legacy mock，只把 console payload 类型保持兼容，不引入真实 API 行为。

目标 payload 形态：

```ts
{
  schedule_type: 'scheduled',
  scheduled_dates: ['2026-04-28', '2026-05-04'],
  window_start_time: '09:00',
  window_end_time: '18:00',
  emails_per_window: 20,
}
```

- [ ] **步骤 3：更新 mock 任务文案**

修改 `frontend/src/data/mockData.ts` 中 `MOCK_TASKS` 的 `schedule`：

```ts
schedule: '4/28-5/12 共 8 天，09:00-18:00，每天最多 20 封',
```

其他 mock 按同样格式更新为 2-3 个不同日期跨度。

- [ ] **步骤 4：运行前端 lint**

运行：`cd frontend; npm run lint`

预期：PASS。

- [ ] **步骤 5：运行前端 build**

运行：`cd frontend; npm run build`

预期：PASS。

- [ ] **步骤 6：Commit**

```bash
git add frontend/src/types/index.ts frontend/src/lib/api/createTask.ts frontend/src/lib/api/batchTasksApi.ts frontend/src/data/mockData.ts frontend/src/components/molecules/BatchTaskCard.tsx
git commit -m "feat(frontend): include scheduled dates in batch task data"
```

只提交实际修改过的文件。

## 任务 7：最终验证和清理

**文件：**
- 检查：全仓库

- [ ] **步骤 1：运行后端核心测试**

运行：`cd backend; uv run python -m unittest test.test_batch_schedule test.test_batch_task_dispatch_schedule test.test_api_endpoints`

预期：PASS。

- [ ] **步骤 2：运行前端测试**

运行：`cd frontend; npm run test -- scheduleDates`

预期：PASS。

- [ ] **步骤 3：运行前端 lint**

运行：`cd frontend; npm run lint`

预期：PASS。

- [ ] **步骤 4：运行前端 build**

运行：`cd frontend; npm run build`

预期：PASS。

- [ ] **步骤 5：检查迁移**

运行：`cd backend; uv run alembic upgrade head`

预期：成功升级到包含 `scheduled_dates` 的 head。

- [ ] **步骤 6：检查 git 状态**

运行：`git status --short`

预期：没有未提交的实现文件。若只有本地运行产物或 `.superpowers` 忽略文件，无需处理。

- [ ] **步骤 7：最终提交**

如果步骤 1-6 中产生必要修复：

```bash
git add backend/app/services/task_runtime.py backend/app/services/batch_schedule.py frontend/src/components/molecules/TaskDateSelector.tsx frontend/src/components/molecules/TaskScheduleSettings.tsx frontend/src/features/create-task/client/scheduleDates.ts
git commit -m "fix: stabilize batch task calendar scheduling"
```

如果没有变更，不创建空提交。

## 规格覆盖自检

- 日期范围初选：任务 4、任务 5 覆盖。
- 日历点击微调：任务 4、任务 5 覆盖。
- 范围外日期：任务 4、任务 5 覆盖。
- 最终日期数组：任务 2、任务 4、任务 6 覆盖。
- 后端校验：任务 1、任务 2 覆盖。
- 动态派发：任务 3 覆盖。
- 任务卡片摘要：任务 6 覆盖。
- 验证命令：任务 7 覆盖。
