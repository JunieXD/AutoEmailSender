# 批量定时邮件随机均匀排程实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 批量定时任务创建时为每封邮件写入随机抖动的近似均匀 `scheduled_at`，避免发送窗口开始后集中发送。

**架构：** 在 `backend/app/services/batch_schedule.py` 中新增纯函数排程能力，负责按日期顺序分配任务、按单日可用窗口铺满并添加随机抖动。`backend/app/api/batch_tasks.py` 在创建子 `EmailTask` 后调用排程函数写入 `scheduled_at`，调度器继续复用现有 `scheduled_at <= now` 筛选逻辑。

**技术栈：** Python 3、FastAPI、SQLAlchemy AsyncSession、unittest、uv。

---

## 文件结构

- 修改：`backend/app/services/batch_schedule.py`  
  职责：保存批量任务时间窗口判断和新增的纯函数排程算法。
- 修改：`backend/app/api/batch_tasks.py`  
  职责：在批量任务创建流程中调用排程算法，并在容量不足时返回 400。
- 修改：`backend/test/test_batch_schedule.py`  
  职责：覆盖排程算法的单元测试，包括多天分配、剩余窗口、随机抖动边界。
- 修改：`backend/test/test_api_endpoints.py`  
  职责：覆盖创建批量定时任务后子任务持久化 `scheduled_at`，以及容量不足时的 API 错误。
- 修改：`backend/test/test_batch_task_dispatch_schedule.py`  
  职责：覆盖调度器不会在窗口开始时发送未来 `scheduled_at` 的任务。

## 任务 1：新增排程纯函数

**文件：**
- 修改：`backend/test/test_batch_schedule.py`
- 修改：`backend/app/services/batch_schedule.py`

- [ ] **步骤 1：编写失败的单日铺满测试**

在 `backend/test/test_batch_schedule.py` 的 import 中加入 `build_jittered_batch_schedule`，并在 `BatchScheduleTest` 中新增测试：

```python
    def test_build_jittered_batch_schedule_spreads_actual_count_across_window(self) -> None:
        result = build_jittered_batch_schedule(
            task_count=6,
            scheduled_dates=["2026-05-04"],
            window_start_time="09:00",
            window_end_time="18:00",
            emails_per_window=20,
            now=datetime(2026, 5, 3, 12, 0, tzinfo=UTC),
            jitter_ratio=0,
        )

        self.assertEqual(len(result), 6)
        self.assertEqual(result[0], datetime(2026, 5, 4, 9, 45, tzinfo=UTC))
        self.assertEqual(result[-1], datetime(2026, 5, 4, 17, 15, tzinfo=UTC))
        self.assertEqual(result, sorted(result))
```

- [ ] **步骤 2：运行测试验证失败**

运行：

```bash
cd backend && uv run python -m unittest test.test_batch_schedule.BatchScheduleTest.test_build_jittered_batch_schedule_spreads_actual_count_across_window
```

预期：失败，错误包含 `cannot import name 'build_jittered_batch_schedule'` 或 `NameError`。

- [ ] **步骤 3：实现最小排程函数骨架**

在 `backend/app/services/batch_schedule.py` 中添加导入：

```python
import random
from datetime import UTC, date, datetime, time, timedelta
```

用该导入替换原有 `from datetime import date, datetime, time`。

在文件末尾新增：

```python
def build_jittered_batch_schedule(
    *,
    task_count: int,
    scheduled_dates: list[str],
    window_start_time: str,
    window_end_time: str,
    emails_per_window: int,
    now: datetime,
    jitter_ratio: float = 0.3,
    max_jitter: timedelta = timedelta(minutes=10),
    random_source: random.Random | None = None,
) -> list[datetime]:
    if task_count <= 0:
        return []
    if emails_per_window <= 0:
        raise ValueError("每天发送数量必须大于 0")

    dates = normalize_scheduled_dates(scheduled_dates)
    start_clock = time.fromisoformat(window_start_time)
    end_clock = time.fromisoformat(window_end_time)
    if end_clock <= start_clock:
        raise ValueError("结束时间必须晚于开始时间")

    local_now = now.replace(tzinfo=None)
    timezone = now.tzinfo or UTC
    remaining = task_count
    scheduled: list[datetime] = []
    rng = random_source or random.Random()

    for value in dates:
        if remaining <= 0:
            break
        current_date = date.fromisoformat(value)
        window_start = datetime.combine(current_date, start_clock)
        window_end = datetime.combine(current_date, end_clock)
        if current_date == local_now.date():
            window_start = max(window_start, local_now)
        if window_start >= window_end:
            continue

        count_for_day = min(remaining, emails_per_window)
        day_schedule = _build_day_schedule(
            window_start.replace(tzinfo=timezone),
            window_end.replace(tzinfo=timezone),
            count_for_day,
            jitter_ratio=jitter_ratio,
            max_jitter=max_jitter,
            random_source=rng,
        )
        scheduled.extend(day_schedule)
        remaining -= count_for_day

    if remaining > 0:
        raise ValueError("选中的发送日期和每天发送数量不足以覆盖全部任务")
    return scheduled


def _build_day_schedule(
    window_start: datetime,
    window_end: datetime,
    count: int,
    *,
    jitter_ratio: float,
    max_jitter: timedelta,
    random_source: random.Random,
) -> list[datetime]:
    if count <= 0:
        return []
    total_seconds = (window_end - window_start).total_seconds()
    if total_seconds <= 0:
        return []

    slot_seconds = total_seconds / count
    jitter_seconds = min(slot_seconds * jitter_ratio, max_jitter.total_seconds())
    values: list[datetime] = []
    for index in range(count):
        base_offset = slot_seconds * (index + 0.5)
        jitter = random_source.uniform(-jitter_seconds, jitter_seconds) if jitter_seconds > 0 else 0
        scheduled_at = window_start + timedelta(seconds=base_offset + jitter)
        scheduled_at = max(window_start, min(scheduled_at, window_end))
        values.append(scheduled_at)
    return sorted(values)
```

- [ ] **步骤 4：运行单日测试验证通过**

运行：

```bash
cd backend && uv run python -m unittest test.test_batch_schedule.BatchScheduleTest.test_build_jittered_batch_schedule_spreads_actual_count_across_window
```

预期：通过。

- [ ] **步骤 5：提交任务 1**

```bash
git add backend/app/services/batch_schedule.py backend/test/test_batch_schedule.py
git commit -m "feat(backend): add jittered batch schedule builder"
```

## 任务 2：补齐排程算法边界测试

**文件：**
- 修改：`backend/test/test_batch_schedule.py`
- 修改：`backend/app/services/batch_schedule.py`

- [ ] **步骤 1：编写多天顺序填满测试**

在 `BatchScheduleTest` 中新增：

```python
    def test_build_jittered_batch_schedule_fills_dates_in_order(self) -> None:
        result = build_jittered_batch_schedule(
            task_count=30,
            scheduled_dates=["2026-05-04", "2026-05-05", "2026-05-06"],
            window_start_time="09:00",
            window_end_time="18:00",
            emails_per_window=20,
            now=datetime(2026, 5, 3, 12, 0, tzinfo=UTC),
            jitter_ratio=0,
        )

        dates = [item.date().isoformat() for item in result]
        self.assertEqual(dates.count("2026-05-04"), 20)
        self.assertEqual(dates.count("2026-05-05"), 10)
        self.assertEqual(dates.count("2026-05-06"), 0)
```

- [ ] **步骤 2：编写当天剩余窗口测试**

在 `BatchScheduleTest` 中新增：

```python
    def test_build_jittered_batch_schedule_uses_remaining_window_for_today(self) -> None:
        result = build_jittered_batch_schedule(
            task_count=4,
            scheduled_dates=["2026-05-04"],
            window_start_time="09:00",
            window_end_time="18:00",
            emails_per_window=20,
            now=datetime(2026, 5, 4, 14, 0, tzinfo=UTC),
            jitter_ratio=0,
        )

        self.assertEqual(result[0], datetime(2026, 5, 4, 14, 30, tzinfo=UTC))
        self.assertEqual(result[-1], datetime(2026, 5, 4, 17, 30, tzinfo=UTC))
        self.assertTrue(all(item >= datetime(2026, 5, 4, 14, 0, tzinfo=UTC) for item in result))
```

- [ ] **步骤 3：编写跳过已结束当天测试**

在 `BatchScheduleTest` 中新增：

```python
    def test_build_jittered_batch_schedule_skips_expired_today_window(self) -> None:
        result = build_jittered_batch_schedule(
            task_count=2,
            scheduled_dates=["2026-05-04", "2026-05-05"],
            window_start_time="09:00",
            window_end_time="18:00",
            emails_per_window=20,
            now=datetime(2026, 5, 4, 18, 30, tzinfo=UTC),
            jitter_ratio=0,
        )

        self.assertEqual({item.date().isoformat() for item in result}, {"2026-05-05"})
```

- [ ] **步骤 4：编写抖动边界和容量不足测试**

在 `BatchScheduleTest` 中新增：

```python
    def test_build_jittered_batch_schedule_keeps_jitter_inside_window(self) -> None:
        result = build_jittered_batch_schedule(
            task_count=12,
            scheduled_dates=["2026-05-04"],
            window_start_time="09:00",
            window_end_time="10:00",
            emails_per_window=12,
            now=datetime(2026, 5, 3, 12, 0, tzinfo=UTC),
            random_source=random.Random(42),
        )

        window_start = datetime(2026, 5, 4, 9, 0, tzinfo=UTC)
        window_end = datetime(2026, 5, 4, 10, 0, tzinfo=UTC)
        self.assertEqual(len(result), 12)
        self.assertEqual(result, sorted(result))
        self.assertTrue(all(window_start <= item <= window_end for item in result))

    def test_build_jittered_batch_schedule_rejects_insufficient_capacity(self) -> None:
        with self.assertRaisesRegex(ValueError, "不足以覆盖全部任务"):
            build_jittered_batch_schedule(
                task_count=5,
                scheduled_dates=["2026-05-04"],
                window_start_time="09:00",
                window_end_time="18:00",
                emails_per_window=4,
                now=datetime(2026, 5, 3, 12, 0, tzinfo=UTC),
                jitter_ratio=0,
            )
```

同时在 `backend/test/test_batch_schedule.py` 顶部添加：

```python
import random
```

- [ ] **步骤 5：运行排程单元测试验证通过**

运行：

```bash
cd backend && uv run python -m unittest test.test_batch_schedule
```

预期：全部通过。

- [ ] **步骤 6：提交任务 2**

```bash
git add backend/app/services/batch_schedule.py backend/test/test_batch_schedule.py
git commit -m "test(backend): cover jittered batch schedule edges"
```

## 任务 3：创建批量任务时写入 scheduled_at

**文件：**
- 修改：`backend/test/test_api_endpoints.py`
- 修改：`backend/app/api/batch_tasks.py`

- [ ] **步骤 1：编写创建后持久化 scheduled_at 的失败测试**

在 `backend/test/test_api_endpoints.py` 中找到批量任务 API 测试类，在相邻的定时批量任务测试附近新增：

```python
    def test_create_scheduled_batch_task_assigns_jittered_scheduled_at(self) -> None:
        identity_id = self._create_identity(with_imap=False)
        llm_profile_id = self._create_llm()
        self.client.post("/api/professors/import-sample")
        professor_ids = [item["id"] for item in self.client.get("/api/professors").json()[:3]]
        tomorrow = (datetime.now().date() + timedelta(days=1)).isoformat()

        response = self.client.post(
            "/api/batch-tasks",
            json={
                "identity_id": identity_id,
                "llm_profile_id": llm_profile_id,
                "name": "随机均匀定时发送",
                "professor_ids": professor_ids,
                "schedule_type": "scheduled",
                "scheduled_dates": [tomorrow],
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

        self.assertEqual(response.status_code, 201, msg=response.text)
        task_id = response.json()["id"]
        items_response = self.client.get(f"/api/batch-tasks/{task_id}/items")
        self.assertEqual(items_response.status_code, 200)
        scheduled_values = [item["scheduled_at"] for item in items_response.json()]
        self.assertEqual(len(scheduled_values), len(professor_ids))
        self.assertTrue(all(value is not None for value in scheduled_values))
        self.assertEqual(scheduled_values, sorted(scheduled_values))
```

- [ ] **步骤 2：运行测试验证失败**

运行：

```bash
cd backend && uv run python -m unittest test.test_api_endpoints.ApiEndpointTests.test_create_scheduled_batch_task_assigns_jittered_scheduled_at
```

预期：失败，`scheduled_at` 为 `None`。

- [ ] **步骤 3：在 API 中调用排程函数**

修改 `backend/app/api/batch_tasks.py` 的导入：

```python
from app.services.batch_schedule import (
    build_jittered_batch_schedule,
    has_future_batch_window,
    normalize_scheduled_dates,
)
```

在 `create_batch_task` 中，`professors` 校验完成后、创建 `BatchTask` 前添加：

```python
    scheduled_at_values: list[datetime | None] = [None] * len(professors)
    if payload.schedule_type == "scheduled":
        try:
            scheduled_at_values = list(
                build_jittered_batch_schedule(
                    task_count=len(professors),
                    scheduled_dates=scheduled_dates,
                    window_start_time=payload.window_start_time or "",
                    window_end_time=payload.window_end_time or "",
                    emails_per_window=payload.emails_per_window or 0,
                    now=datetime.now().astimezone(),
                ),
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
```

修改创建子任务循环：

```python
    for index, professor in enumerate(professors):
```

并在 `EmailTask(...)` 参数中加入：

```python
            scheduled_at=scheduled_at_values[index],
```

- [ ] **步骤 4：运行创建 scheduled_at 测试验证通过**

运行：

```bash
cd backend && uv run python -m unittest test.test_api_endpoints.ApiEndpointTests.test_create_scheduled_batch_task_assigns_jittered_scheduled_at
```

预期：通过。

- [ ] **步骤 5：提交任务 3**

```bash
git add backend/app/api/batch_tasks.py backend/test/test_api_endpoints.py
git commit -m "feat(backend): assign scheduled times to batch emails"
```

## 任务 4：容量不足 API 错误

**文件：**
- 修改：`backend/test/test_api_endpoints.py`
- 修改：`backend/app/api/batch_tasks.py`

- [ ] **步骤 1：编写容量不足失败测试**

在 `backend/test/test_api_endpoints.py` 同一测试类中新增：

```python
    def test_create_scheduled_batch_task_rejects_insufficient_schedule_capacity(self) -> None:
        identity_id = self._create_identity(with_imap=False)
        llm_profile_id = self._create_llm()
        self.client.post("/api/professors/import-sample")
        professor_ids = [item["id"] for item in self.client.get("/api/professors").json()[:2]]
        tomorrow = (datetime.now().date() + timedelta(days=1)).isoformat()

        response = self.client.post(
            "/api/batch-tasks",
            json={
                "identity_id": identity_id,
                "llm_profile_id": llm_profile_id,
                "name": "容量不足定时发送",
                "professor_ids": professor_ids,
                "schedule_type": "scheduled",
                "scheduled_dates": [tomorrow],
                "window_start_time": "09:00",
                "window_end_time": "18:00",
                "emails_per_window": 1,
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
        self.assertIn("不足以覆盖全部任务", response.json()["detail"])
```

- [ ] **步骤 2：运行容量不足测试验证通过**

运行：

```bash
cd backend && uv run python -m unittest test.test_api_endpoints.ApiEndpointTests.test_create_scheduled_batch_task_rejects_insufficient_schedule_capacity
```

预期：通过。任务 3 已实现 `ValueError` 到 400 的转换时，该测试应直接通过；如果失败，检查 payload 是否确实包含 2 个导师且每天上限为 1。

- [ ] **步骤 3：运行相关批量任务 API 测试**

运行：

```bash
cd backend && uv run python -m unittest test.test_api_endpoints.ApiEndpointTests.test_create_scheduled_batch_task_returns_normalized_scheduled_dates test.test_api_endpoints.ApiEndpointTests.test_create_scheduled_batch_task_rejects_expired_windows test.test_api_endpoints.ApiEndpointTests.test_create_scheduled_batch_task_assigns_jittered_scheduled_at test.test_api_endpoints.ApiEndpointTests.test_create_scheduled_batch_task_rejects_insufficient_schedule_capacity
```

预期：全部通过。

- [ ] **步骤 4：提交任务 4**

```bash
git add backend/app/api/batch_tasks.py backend/test/test_api_endpoints.py
git commit -m "test(backend): reject undersized batch schedules"
```

## 任务 5：验证调度器不会窗口开始全发

**文件：**
- 修改：`backend/test/test_batch_task_dispatch_schedule.py`

- [ ] **步骤 1：编写未来 scheduled_at 跳过测试**

在 `BatchTaskDispatchScheduleTests` 中新增：

```python
    def test_dispatch_due_tasks_skips_batch_task_before_scheduled_at_inside_window(self) -> None:
        task_id = self._run_async(
            self._create_batch_task_with_approved_task(
                scheduled_dates=["2026-05-04"],
                emails_per_window=20,
            ),
        )
        self._run_async(
            self._set_task_scheduled_at(
                task_id,
                datetime(2026, 5, 4, 10, 30, tzinfo=UTC),
            ),
        )

        with patch(
            "app.services.task_runtime.mail_runtime.send_email",
            AsyncMock(return_value=self._build_send_result()),
        ) as mocked_send:
            processed = self._run_async(
                dispatch_due_tasks_once(
                    self.session_factory,
                    now=datetime(2026, 5, 4, 10, 0, tzinfo=UTC),
                    local_timezone=UTC,
                ),
            )

        self.assertEqual(processed, 0)
        self.assertEqual(self._run_async(self._get_task_status(task_id)), EmailTaskStatus.APPROVED.value)
        mocked_send.assert_not_called()
```

在同文件 helper 区域新增：

```python
    async def _set_task_scheduled_at(self, task_id: int, scheduled_at: datetime) -> None:
        async with self.session_factory() as session:
            task = await session.get(EmailTask, task_id)
            assert task is not None
            task.scheduled_at = scheduled_at
            task.updated_at = datetime.now(UTC)
            await session.commit()
```

- [ ] **步骤 2：运行调度器测试验证通过**

运行：

```bash
cd backend && uv run python -m unittest test.test_batch_task_dispatch_schedule.BatchTaskDispatchScheduleTests.test_dispatch_due_tasks_skips_batch_task_before_scheduled_at_inside_window
```

预期：通过。该测试证明现有调度器会遵守未来 `scheduled_at`。

- [ ] **步骤 3：提交任务 5**

```bash
git add backend/test/test_batch_task_dispatch_schedule.py
git commit -m "test(backend): keep future scheduled batch emails pending"
```

## 任务 6：最终验证与文档状态检查

**文件：**
- 检查：`docs/superpowers/specs/2026-05-18-jittered-batch-send-schedule-design.md`
- 检查：`docs/superpowers/plans/2026-05-18-jittered-batch-send-schedule.md`

- [ ] **步骤 1：运行后端相关测试集合**

运行：

```bash
cd backend && uv run python -m unittest test.test_batch_schedule test.test_batch_task_dispatch_schedule
```

预期：全部通过。

- [ ] **步骤 2：运行 API 相关测试集合**

运行：

```bash
cd backend && uv run python -m unittest test.test_api_endpoints
```

预期：全部通过。如发现无关既有失败，不修改无关代码，记录失败用例和错误摘要。

- [ ] **步骤 3：检查工作区状态**

运行：

```bash
git status --short --branch
```

预期：当前分支为 `feat/jittered-batch-send-schedule`，只有本功能相关文件变更或没有未提交变更。

- [ ] **步骤 4：提交计划文档**

```bash
git add docs/superpowers/specs/2026-05-18-jittered-batch-send-schedule-design.md docs/superpowers/plans/2026-05-18-jittered-batch-send-schedule.md
git commit -m "docs: add jittered batch schedule plan"
```


