# 定时批量任务过期处理实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 让定时批量任务在所有发送窗口错过后自动进入 `expired` 状态，并取消剩余未完成邮件，避免任务静默停留在 `running`。

**架构：** 后端把发送窗口判断集中在 `app.services.batch_schedule`，把批量任务过期和子任务取消集中在 `app.services.task_runtime`，由后台调度、审核批准和恢复任务复用。前端只消费后端状态，补齐 `expired` 与 `schedule_expired` 的类型、展示和详情文案。

**技术栈：** FastAPI、SQLAlchemy、unittest、React、TypeScript、Vitest。

---

## 文件结构

- 修改：`backend/app/services/batch_schedule.py`
  - 负责日期归一化、窗口内判断、是否仍有未来窗口、是否所有窗口已过期。
- 修改：`backend/app/models/batch_task.py`
  - 新增 `BatchTaskStatus.EXPIRED`。
- 修改：`backend/app/models/email_task.py`
  - 新增 `EmailTaskCancellationReason.SCHEDULE_EXPIRED`。
- 修改：`backend/app/services/task_runtime.py`
  - 新增未完成/最终状态集合。
  - 新增批量任务过期服务函数。
  - 调整 `dispatch_due_tasks_once`、审核批准函数与恢复路径可复用的过期检查。
- 修改：`backend/app/api/batch_tasks.py`
  - 创建时校验已过期窗口。
  - 恢复任务时处理已过期任务。
  - 允许删除 `expired` 批量任务。
  - 序列化时保留 `expired` 状态。
- 修改：`backend/test/test_batch_schedule.py`
  - 覆盖未来窗口与已过期窗口判断。
- 修改：`backend/test/test_batch_task_dispatch_schedule.py`
  - 覆盖调度过期、保留最终状态、不触发真实发送、未来窗口不误过期。
- 修改：`backend/test/test_api_endpoints.py`
  - 覆盖创建时已过期校验、审核过期任务失败、恢复暂停过期任务。
- 修改：`frontend/src/types/index.ts`
  - 补齐 `expired` 与 `schedule_expired` 类型和标签。
- 修改：`frontend/src/features/batch-tasks/client/batchTaskDisplay.ts`
  - 对过期取消项展示「发送窗口已过期」。
- 修改：`frontend/src/pages/TasksPage.tsx`
  - 展示 `expired` 状态，允许删除过期任务，隐藏无效的中止按钮，展示过期辅助文案。
- 修改：`frontend/src/pages/TasksPage.test.tsx`
  - 覆盖任务中心过期状态和取消原因展示。
- 修改：`frontend/src/pages/CreateTaskPage.tsx`
  - 前端创建时增加明显已过期窗口的即时提示。
- 测试命令：
  - `cd backend && uv run python -m unittest backend.test.test_batch_schedule backend.test.test_batch_task_dispatch_schedule`
  - `cd backend && uv run python -m unittest backend.test.test_api_endpoints`
  - `cd frontend && npm run test -- TasksPage`
  - `cd frontend && npm run lint`

## 任务 1：补齐批量发送窗口纯函数

**文件：**
- 修改：`backend/app/services/batch_schedule.py`
- 测试：`backend/test/test_batch_schedule.py`

- [ ] **步骤 1：编写失败的窗口判断测试**

在 `backend/test/test_batch_schedule.py` 中扩展导入：

```python
from app.services.batch_schedule import (
    has_future_batch_window,
    is_batch_window_expired,
    is_datetime_in_batch_window,
    normalize_scheduled_dates,
)
```

追加测试：

```python
    def test_has_future_batch_window_includes_active_and_future_windows(self) -> None:
        self.assertTrue(
            has_future_batch_window(
                datetime(2026, 5, 4, 10, 30, tzinfo=UTC),
                scheduled_dates=["2026-05-04"],
                window_end_time="18:00",
            ),
        )
        self.assertTrue(
            has_future_batch_window(
                datetime(2026, 5, 4, 20, 0, tzinfo=UTC),
                scheduled_dates=["2026-05-05"],
                window_end_time="09:00",
            ),
        )
        self.assertFalse(
            has_future_batch_window(
                datetime(2026, 5, 4, 18, 0, tzinfo=UTC),
                scheduled_dates=["2026-05-04"],
                window_end_time="18:00",
            ),
        )

    def test_is_batch_window_expired_only_after_last_window_end(self) -> None:
        self.assertFalse(
            is_batch_window_expired(
                datetime(2026, 5, 4, 17, 59, tzinfo=UTC),
                scheduled_dates=["2026-05-04"],
                window_end_time="18:00",
            ),
        )
        self.assertFalse(
            is_batch_window_expired(
                datetime(2026, 5, 4, 20, 0, tzinfo=UTC),
                scheduled_dates=["2026-05-04", "2026-05-05"],
                window_end_time="09:00",
            ),
        )
        self.assertTrue(
            is_batch_window_expired(
                datetime(2026, 5, 5, 9, 0, tzinfo=UTC),
                scheduled_dates=["2026-05-04", "2026-05-05"],
                window_end_time="09:00",
            ),
        )
```

- [ ] **步骤 2：运行测试验证失败**

运行：

```powershell
cd backend; uv run python -m unittest backend.test.test_batch_schedule
```

预期：失败，报错包含 `cannot import name 'has_future_batch_window'` 或 `cannot import name 'is_batch_window_expired'`。

- [ ] **步骤 3：实现最少窗口判断函数**

在 `backend/app/services/batch_schedule.py` 中新增：

```python
def _combine_date_and_time(value: str, time_value: str) -> datetime:
    parsed_date = date.fromisoformat(value)
    parsed_time = datetime.strptime(time_value, "%H:%M").time()
    return datetime.combine(parsed_date, parsed_time)


def has_future_batch_window(
    now: datetime,
    *,
    scheduled_dates: list[str] | None,
    window_end_time: str | None,
) -> bool:
    if not window_end_time:
        return False
    dates = normalize_scheduled_dates(scheduled_dates)
    if not dates:
        return False
    now_naive = now.replace(tzinfo=None)
    return any(now_naive < _combine_date_and_time(value, window_end_time) for value in dates)


def is_batch_window_expired(
    now: datetime,
    *,
    scheduled_dates: list[str] | None,
    window_end_time: str | None,
) -> bool:
    dates = normalize_scheduled_dates(scheduled_dates)
    if not dates or not window_end_time:
        return False
    return not has_future_batch_window(
        now,
        scheduled_dates=dates,
        window_end_time=window_end_time,
    )
```

- [ ] **步骤 4：运行测试验证通过**

运行：

```powershell
cd backend; uv run python -m unittest backend.test.test_batch_schedule
```

预期：`OK`。

- [ ] **步骤 5：Commit**

```powershell
git add backend/app/services/batch_schedule.py backend/test/test_batch_schedule.py
git commit -m "feat(backend): add batch schedule expiration helpers"
```

## 任务 2：新增后端状态枚举和过期服务

**文件：**
- 修改：`backend/app/models/batch_task.py`
- 修改：`backend/app/models/email_task.py`
- 修改：`backend/app/services/task_runtime.py`
- 测试：`backend/test/test_batch_task_dispatch_schedule.py`

- [ ] **步骤 1：编写调度过期失败测试**

在 `backend/test/test_batch_task_dispatch_schedule.py` 中新增测试：

```python
    def test_dispatch_due_tasks_expires_batch_after_last_window(self) -> None:
        first_task_id, second_task_id = self._run_async(
            self._create_batch_task_with_multiple_approved_tasks(
                scheduled_dates=["2026-05-04"],
                emails_per_window=20,
                task_count=2,
            ),
        )

        with patch(
            "app.services.task_runtime.mail_runtime.send_email",
            AsyncMock(return_value=self._build_send_result()),
        ) as mocked_send:
            processed = self._run_async(
                dispatch_due_tasks_once(
                    self.session_factory,
                    now=datetime(2026, 5, 4, 18, 0, tzinfo=UTC),
                    local_timezone=UTC,
                ),
            )

        self.assertEqual(processed, 0)
        mocked_send.assert_not_called()
        self.assertEqual(
            self._run_async(self._get_batch_task_status_by_email_task_id(first_task_id)),
            BatchTaskStatus.EXPIRED.value,
        )
        self.assertEqual(self._run_async(self._get_task_status(first_task_id)), EmailTaskStatus.CANCELED.value)
        self.assertEqual(self._run_async(self._get_task_status(second_task_id)), EmailTaskStatus.CANCELED.value)
        self.assertEqual(
            self._run_async(self._get_task_cancellation_reason(first_task_id)),
            EmailTaskCancellationReason.SCHEDULE_EXPIRED.value,
        )
```

补充 helper：

```python
    async def _get_batch_task_status_by_email_task_id(self, task_id: int) -> str:
        async with self.session_factory() as session:
            task = await session.get(EmailTask, task_id)
            batch_task = await session.get(BatchTask, task.batch_task_id)
            return batch_task.status

    async def _get_task_cancellation_reason(self, task_id: int) -> str | None:
        async with self.session_factory() as session:
            task = await session.get(EmailTask, task_id)
            return task.cancellation_reason
```

如果文件顶部没有导入 `EmailTaskCancellationReason`，从 `app.models` 添加导入。

- [ ] **步骤 2：运行测试验证失败**

运行：

```powershell
cd backend; uv run python -m unittest backend.test.test_batch_task_dispatch_schedule.BatchTaskDispatchScheduleTests.test_dispatch_due_tasks_expires_batch_after_last_window
```

预期：失败，报错包含 `EXPIRED` 或 `SCHEDULE_EXPIRED` 不存在。

- [ ] **步骤 3：新增枚举值和过期服务**

在 `backend/app/models/batch_task.py` 的 `BatchTaskStatus` 中新增：

```python
    EXPIRED = "expired"
```

在 `backend/app/models/email_task.py` 的 `EmailTaskCancellationReason` 中新增：

```python
    SCHEDULE_EXPIRED = "schedule_expired"
```

在 `backend/app/services/task_runtime.py` 顶部常量区新增：

```python
FINAL_EMAIL_TASK_STATUSES = {
    EmailTaskStatus.SENT.value,
    EmailTaskStatus.REPLY_DETECTED.value,
    EmailTaskStatus.SEND_FAILED.value,
    EmailTaskStatus.CANCELED.value,
}

INCOMPLETE_EMAIL_TASK_STATUSES = {
    EmailTaskStatus.DISCOVERED.value,
    EmailTaskStatus.MATCHED.value,
    EmailTaskStatus.GENERATING_DRAFT.value,
    EmailTaskStatus.DRAFT_FAILED.value,
    EmailTaskStatus.REVIEW_REQUIRED.value,
    EmailTaskStatus.APPROVED.value,
    EmailTaskStatus.SCHEDULED.value,
}
```

在 `task_runtime.py` 中导入：

```python
from app.services.batch_schedule import (
    has_future_batch_window,
    is_batch_window_expired,
    is_datetime_in_batch_window,
)
```

新增服务函数：

```python
async def expire_batch_task_if_needed(
    session: AsyncSession,
    batch_task: BatchTask,
    local_now: datetime,
) -> bool:
    if batch_task.schedule_type != "scheduled":
        return False
    if batch_task.status != BatchTaskStatus.RUNNING.value:
        return False
    if not is_batch_window_expired(
        local_now,
        scheduled_dates=batch_task.scheduled_dates,
        window_end_time=batch_task.window_end_time,
    ):
        return False

    canceled_count = 0
    now_utc = datetime.now(UTC)
    for email_task in batch_task.email_tasks:
        if email_task.status in INCOMPLETE_EMAIL_TASK_STATUSES:
            email_task.status = EmailTaskStatus.CANCELED.value
            email_task.cancellation_reason = EmailTaskCancellationReason.SCHEDULE_EXPIRED.value
            email_task.draft_generation_previous_status = None
            email_task.updated_at = now_utc
            canceled_count += 1

    if canceled_count == 0:
        return False

    batch_task.status = BatchTaskStatus.EXPIRED.value
    batch_task.updated_at = now_utc
    await record_operation_log(
        session,
        category="email",
        event_name="batch_task.expired",
        entity_type="batch_task",
        entity_id=str(batch_task.id),
        metadata={
            "canceled_count": canceled_count,
            "scheduled_dates": batch_task.scheduled_dates,
            "window_end_time": batch_task.window_end_time,
        },
    )
    return True
```

- [ ] **步骤 4：把调度扫描接入过期服务**

在 `dispatch_due_tasks_once` 查询中保留 `selectinload(EmailTask.batch_task)`，并追加加载批量任务子任务：

```python
.options(selectinload(EmailTask.batch_task).selectinload(BatchTask.email_tasks))
```

在循环中、`_batch_task_allows_dispatch` 之前添加：

```python
                if (
                    batch_task is not None
                    and batch_task.schedule_type == "scheduled"
                    and await expire_batch_task_if_needed(session, batch_task, local_now)
                ):
                    await session.commit()
                    continue
```

- [ ] **步骤 5：运行单测验证通过**

运行：

```powershell
cd backend; uv run python -m unittest backend.test.test_batch_task_dispatch_schedule.BatchTaskDispatchScheduleTests.test_dispatch_due_tasks_expires_batch_after_last_window
```

预期：`OK`。

- [ ] **步骤 6：Commit**

```powershell
git add backend/app/models/batch_task.py backend/app/models/email_task.py backend/app/services/task_runtime.py backend/test/test_batch_task_dispatch_schedule.py
git commit -m "feat(backend): expire missed scheduled batch tasks"
```

## 任务 3：补齐调度过期边界测试

**文件：**
- 修改：`backend/test/test_batch_task_dispatch_schedule.py`
- 修改：`backend/app/services/task_runtime.py`

- [ ] **步骤 1：编写未来窗口不误过期测试**

新增测试：

```python
    def test_dispatch_due_tasks_keeps_batch_running_when_future_window_exists(self) -> None:
        task_id = self._run_async(
            self._create_batch_task_with_approved_task(
                scheduled_dates=["2026-05-04", "2026-05-05"],
                emails_per_window=20,
            ),
        )

        with patch(
            "app.services.task_runtime.mail_runtime.send_email",
            AsyncMock(return_value=self._build_send_result()),
        ) as mocked_send:
            processed = self._run_async(
                dispatch_due_tasks_once(
                    self.session_factory,
                    now=datetime(2026, 5, 4, 18, 0, tzinfo=UTC),
                    local_timezone=UTC,
                ),
            )

        self.assertEqual(processed, 0)
        mocked_send.assert_not_called()
        self.assertEqual(
            self._run_async(self._get_batch_task_status_by_email_task_id(task_id)),
            BatchTaskStatus.RUNNING.value,
        )
        self.assertEqual(self._run_async(self._get_task_status(task_id)), EmailTaskStatus.APPROVED.value)
```

- [ ] **步骤 2：编写最终状态不被覆盖测试**

新增测试：

```python
    def test_expiring_batch_preserves_final_item_statuses(self) -> None:
        sent_task_id, failed_task_id, pending_task_id = self._run_async(
            self._create_batch_task_with_final_and_pending_tasks(
                scheduled_dates=["2026-05-04"],
                emails_per_window=20,
            ),
        )

        with patch(
            "app.services.task_runtime.mail_runtime.send_email",
            AsyncMock(return_value=self._build_send_result()),
        ):
            self._run_async(
                dispatch_due_tasks_once(
                    self.session_factory,
                    now=datetime(2026, 5, 4, 18, 0, tzinfo=UTC),
                    local_timezone=UTC,
                ),
            )

        self.assertEqual(self._run_async(self._get_task_status(sent_task_id)), EmailTaskStatus.SENT.value)
        self.assertEqual(self._run_async(self._get_task_status(failed_task_id)), EmailTaskStatus.SEND_FAILED.value)
        self.assertEqual(self._run_async(self._get_task_status(pending_task_id)), EmailTaskStatus.CANCELED.value)
```

新增 helper `_create_batch_task_with_final_and_pending_tasks`，复用现有 identity、LLM、professor 创建模式，创建 3 个同批次任务：

```python
    async def _create_batch_task_with_final_and_pending_tasks(
        self,
        *,
        scheduled_dates: list[str],
        emails_per_window: int,
    ) -> tuple[int, int, int]:
        sent_task_id, failed_task_id, pending_task_id = await self._create_batch_task_with_multiple_approved_tasks(
            scheduled_dates=scheduled_dates,
            emails_per_window=emails_per_window,
            task_count=3,
        )
        async with self.session_factory() as session:
            sent_task = await session.get(EmailTask, sent_task_id)
            failed_task = await session.get(EmailTask, failed_task_id)
            sent_task.status = EmailTaskStatus.SENT.value
            sent_task.sent_at = datetime(2026, 5, 4, 9, 30, tzinfo=UTC)
            failed_task.status = EmailTaskStatus.SEND_FAILED.value
            failed_task.last_error = "smtp timeout"
            await session.commit()
        return sent_task_id, failed_task_id, pending_task_id
```

- [ ] **步骤 3：运行新增测试**

运行：

```powershell
cd backend; uv run python -m unittest backend.test.test_batch_task_dispatch_schedule.BatchTaskDispatchScheduleTests.test_dispatch_due_tasks_keeps_batch_running_when_future_window_exists backend.test.test_batch_task_dispatch_schedule.BatchTaskDispatchScheduleTests.test_expiring_batch_preserves_final_item_statuses
```

预期：如果任务 2 实现正确，两个测试均 `OK`。如果失败，修正过期服务中「只处理未完成状态」和「未来窗口判断」。

- [ ] **步骤 4：运行完整调度测试**

运行：

```powershell
cd backend; uv run python -m unittest backend.test.test_batch_task_dispatch_schedule
```

预期：`OK`。

- [ ] **步骤 5：Commit**

```powershell
git add backend/app/services/task_runtime.py backend/test/test_batch_task_dispatch_schedule.py
git commit -m "test(backend): cover scheduled batch expiration boundaries"
```

## 任务 4：创建、审核和恢复路径接入过期判断

**文件：**
- 修改：`backend/app/api/batch_tasks.py`
- 修改：`backend/app/services/task_runtime.py`
- 测试：`backend/test/test_api_endpoints.py`

- [ ] **步骤 1：编写创建过期窗口失败测试**

在 `backend/test/test_api_endpoints.py` 中新增：

```python
    def test_create_scheduled_batch_task_rejects_expired_windows(self) -> None:
        identity_id = self._create_identity(with_imap=False)
        llm_profile_id = self._create_llm()
        self.client.post("/api/professors/import-sample")
        professor_id = self.client.get("/api/professors").json()[0]["id"]
        yesterday = (datetime.now().date() - timedelta(days=1)).isoformat()

        response = self.client.post(
            "/api/batch-tasks",
            json={
                "identity_id": identity_id,
                "llm_profile_id": llm_profile_id,
                "name": "过期定时发送",
                "professor_ids": [professor_id],
                "schedule_type": "scheduled",
                "scheduled_dates": [yesterday],
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
        self.assertIn("发送窗口已全部过期", response.json()["detail"])
```

- [ ] **步骤 2：编写过期后审核失败测试**

新增测试：

```python
    def test_approve_batch_draft_rejects_expired_scheduled_batch(self) -> None:
        task_id = self._create_batch_review_task_with_expired_window()

        response = self.client.post(
            f"/api/email-tasks/{task_id}/approve",
            json={
                "subject": "申请交流",
                "body_text": "老师您好",
                "body_html": "<p>老师您好</p>",
                "selected_material_ids": [],
            },
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("发送窗口已全部过期", response.json()["detail"])
```

新增 helper 时，创建一个 `scheduled` 批量任务，`scheduled_dates` 使用昨天，子任务状态设为 `review_required`。helper 可以直接通过测试数据库插入模型，避免创建接口的过期校验影响测试。

- [ ] **步骤 3：编写暂停恢复过期测试**

新增测试：

```python
    def test_resume_scheduled_batch_task_expires_when_window_has_passed(self) -> None:
        batch_task_id = self._create_paused_batch_task_with_expired_window()

        response = self.client.post(f"/api/batch-tasks/{batch_task_id}/resume")

        self.assertEqual(response.status_code, 200, msg=response.text)
        payload = response.json()["task"]
        self.assertEqual(payload["status"], "expired")
        items = self.client.get(f"/api/batch-tasks/{batch_task_id}/items").json()
        self.assertEqual(items[0]["status"], "canceled")
        self.assertEqual(items[0]["cancellation_reason"], "schedule_expired")
```

- [ ] **步骤 4：运行测试验证失败**

运行：

```powershell
cd backend; uv run python -m unittest backend.test.test_api_endpoints.ApiEndpointsTest.test_create_scheduled_batch_task_rejects_expired_windows backend.test.test_api_endpoints.ApiEndpointsTest.test_approve_batch_draft_rejects_expired_scheduled_batch backend.test.test_api_endpoints.ApiEndpointsTest.test_resume_scheduled_batch_task_expires_when_window_has_passed
```

预期：失败，分别体现创建未拦截、审核未拦截、恢复未过期。

- [ ] **步骤 5：实现创建时过期校验**

在 `backend/app/api/batch_tasks.py` 导入：

```python
from app.services.batch_schedule import has_future_batch_window, normalize_scheduled_dates
```

在 `create_batch_task` 的 `payload.schedule_type == "scheduled"` 分支内、校验窗口和数量后添加：

```python
        if not has_future_batch_window(
            datetime.now().astimezone(),
            scheduled_dates=scheduled_dates,
            window_end_time=payload.window_end_time,
        ):
            raise HTTPException(status_code=400, detail="当前定时发送窗口已全部过期，请重新选择发送日期或结束时间。")
```

- [ ] **步骤 6：实现审核批准前的过期保护**

在 `backend/app/services/task_runtime.py` 新增：

```python
def ensure_batch_task_has_future_window(task: EmailTask, local_now: datetime) -> None:
    batch_task = task.batch_task
    if batch_task is None or batch_task.schedule_type != "scheduled":
        return
    if batch_task.status == BatchTaskStatus.EXPIRED.value or not has_future_batch_window(
        local_now,
        scheduled_dates=batch_task.scheduled_dates,
        window_end_time=batch_task.window_end_time,
    ):
        raise ValueError("当前批量任务的发送窗口已全部过期，请重新安排发送时间后再审核发送。")
```

在 `approve_draft_task`、`approve_and_schedule_task`、`approve_and_send_task` 中，`_ensure_task_allows_legacy_manual_actions(task)` 之后添加：

```python
        ensure_batch_task_has_future_window(task, datetime.now().astimezone())
```

- [ ] **步骤 7：实现恢复时过期处理**

在 `backend/app/api/batch_tasks.py` 导入 `expire_batch_task_if_needed`：

```python
from app.services.task_runtime import dispatch_email_task, expire_batch_task_if_needed
```

在 `resume_batch_task` 中替换直接设为 `running` 的逻辑：

```python
    if task.schedule_type == "scheduled":
        task.status = BatchTaskStatus.RUNNING.value
        expired = await expire_batch_task_if_needed(session, task, datetime.now().astimezone())
        if not expired:
            task.status = BatchTaskStatus.RUNNING.value
            task.updated_at = datetime.now(UTC)
            await _record_batch_task_action(session, task, "batch_task.resumed")
    else:
        task.status = BatchTaskStatus.RUNNING.value
        task.updated_at = datetime.now(UTC)
        await _record_batch_task_action(session, task, "batch_task.resumed")
```

如果 `expired` 为 `True`，不要再记录 `batch_task.resumed`，过期服务会记录 `batch_task.expired`。

- [ ] **步骤 8：允许删除过期任务并保留序列化状态**

在 `backend/app/api/batch_tasks.py` 的 `BATCH_TASK_DELETABLE_STATUSES` 中加入：

```python
    BatchTaskStatus.EXPIRED.value,
```

在 `_serialize_batch_task` 的自动 `completed` 判断中排除 `expired`：

```python
        and task.status not in {
            BatchTaskStatus.STOPPED.value,
            BatchTaskStatus.EXPIRED.value,
        }
```

- [ ] **步骤 9：运行接口测试验证通过**

运行：

```powershell
cd backend; uv run python -m unittest backend.test.test_api_endpoints.ApiEndpointsTest.test_create_scheduled_batch_task_rejects_expired_windows backend.test.test_api_endpoints.ApiEndpointsTest.test_approve_batch_draft_rejects_expired_scheduled_batch backend.test.test_api_endpoints.ApiEndpointsTest.test_resume_scheduled_batch_task_expires_when_window_has_passed
```

预期：`OK`。

- [ ] **步骤 10：Commit**

```powershell
git add backend/app/api/batch_tasks.py backend/app/services/task_runtime.py backend/test/test_api_endpoints.py
git commit -m "feat(backend): guard expired scheduled batch actions"
```

## 任务 5：前端补齐过期状态展示

**文件：**
- 修改：`frontend/src/types/index.ts`
- 修改：`frontend/src/features/batch-tasks/client/batchTaskDisplay.ts`
- 修改：`frontend/src/pages/TasksPage.tsx`
- 测试：`frontend/src/pages/TasksPage.test.tsx`

- [ ] **步骤 1：编写任务中心过期展示测试**

在 `frontend/src/pages/TasksPage.test.tsx` 中新增测试：

```tsx
it("shows expired batch tasks and schedule-expired canceled items", async () => {
  server.use(
    http.get("/api/batch-tasks", () =>
      HttpResponse.json([
        buildBatchTask({
          status: "expired",
          approved_count: 0,
          scheduled_count: 0,
        }),
      ]),
    ),
    http.get("/api/batch-tasks/:taskId/items", () =>
      HttpResponse.json([
        buildBatchItem({
          status: "canceled",
          cancellation_reason: "schedule_expired",
        }),
      ]),
    ),
  );

  render(<TasksPage />);

  expect(await screen.findByText("已过期")).toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: "查看详情" }));
  expect(await screen.findByText("发送窗口已过期")).toBeInTheDocument();
});
```

如果当前测试文件没有 `server`、`http`、`HttpResponse` 或 `userEvent`，按该文件已有 mock 风格改写，目标保持断言不变。

- [ ] **步骤 2：运行测试验证失败**

运行：

```powershell
cd frontend; npm run test -- TasksPage
```

预期：TypeScript 或测试失败，原因是 `expired` / `schedule_expired` 类型或文案不存在。

- [ ] **步骤 3：补齐前端类型和标签**

在 `frontend/src/types/index.ts` 中修改：

```ts
export type BatchTaskRuntimeStatus =
  | 'running'
  | 'paused'
  | 'stopped'
  | 'completed'
  | 'expired';
```

```ts
export type WorkspaceTaskCancellationReason =
  | 'batch_stopped'
  | 'schedule_expired';
```

在 `BATCH_TASK_STATUS_LABELS` 中加入：

```ts
  expired: '已过期',
```

- [ ] **步骤 4：展示取消原因和过期任务卡片文案**

在 `frontend/src/features/batch-tasks/client/batchTaskDisplay.ts` 中新增：

```ts
export const getBatchTaskItemCancellationText = (item: BatchTaskItemDTO) => {
  if (item.cancellation_reason === "schedule_expired") {
    return "发送窗口已过期";
  }
  if (item.cancellation_reason === "batch_stopped") {
    return "批量任务已中止";
  }
  return null;
};
```

在 `frontend/src/pages/TasksPage.tsx` 中导入该函数，并调整：

```ts
const canDeleteBatchTask = (task: BatchTaskCardDTO) =>
  task.status === "stopped" ||
  task.status === "completed" ||
  task.status === "expired";
```

中止按钮条件改为：

```tsx
task.status !== "stopped" &&
task.status !== "completed" &&
task.status !== "expired"
```

在批量任务卡片的日程文案后添加：

```tsx
{task.status === "expired" ? (
  <p className="mt-2 text-sm text-red-700">
    发送窗口已过期，剩余邮件已取消。可重新创建任务。
  </p>
) : null}
```

在「还未发送给」列表每个 item 的状态区域显示取消原因：

```tsx
const cancellationText = getBatchTaskItemCancellationText(item);
```

并在 action 区域追加：

```tsx
{cancellationText ? (
  <span className="font-medium text-red-700">{cancellationText}</span>
) : null}
```

- [ ] **步骤 5：让过期取消项出现在详情列表**

调整 `pendingBatchTaskItems` 过滤条件，把 `schedule_expired` 取消项纳入「还未发送给」：

```ts
(item.status === "canceled" &&
  (item.cancellation_reason === "batch_stopped" ||
    item.cancellation_reason === "schedule_expired")) ||
```

- [ ] **步骤 6：运行前端测试验证通过**

运行：

```powershell
cd frontend; npm run test -- TasksPage
```

预期：`TasksPage` 相关测试通过。

- [ ] **步骤 7：Commit**

```powershell
git add frontend/src/types/index.ts frontend/src/features/batch-tasks/client/batchTaskDisplay.ts frontend/src/pages/TasksPage.tsx frontend/src/pages/TasksPage.test.tsx
git commit -m "feat(frontend): show expired scheduled batch tasks"
```

## 任务 6：前端创建时基础防呆

**文件：**
- 修改：`frontend/src/pages/CreateTaskPage.tsx`
- 测试：`frontend/src/features/create-task/client/scheduleDates.ts`
- 测试：`frontend/src/features/create-task/client/scheduleDates.test.ts`

- [ ] **步骤 1：编写日期窗口过期工具测试**

在 `frontend/src/features/create-task/client/scheduleDates.test.ts` 中新增：

```ts
import { hasFutureScheduleWindow } from "./scheduleDates";

it("detects whether selected schedule windows still have a future end time", () => {
  expect(
    hasFutureScheduleWindow(["2026-05-08"], "18:00", new Date("2026-05-08T09:00:00")),
  ).toBe(true);
  expect(
    hasFutureScheduleWindow(["2026-05-08"], "18:00", new Date("2026-05-08T18:00:00")),
  ).toBe(false);
  expect(
    hasFutureScheduleWindow(
      ["2026-05-08", "2026-05-09"],
      "09:00",
      new Date("2026-05-08T20:00:00"),
    ),
  ).toBe(true);
});
```

- [ ] **步骤 2：运行测试验证失败**

运行：

```powershell
cd frontend; npm run test -- scheduleDates
```

预期：失败，报错包含 `hasFutureScheduleWindow` 未导出。

- [ ] **步骤 3：实现前端工具函数**

在 `frontend/src/features/create-task/client/scheduleDates.ts` 中新增：

```ts
export const hasFutureScheduleWindow = (
  dates: string[],
  endTime: string,
  now = new Date(),
) =>
  normalizeScheduledDates(dates).some((date) => {
    const endAt = new Date(`${date}T${endTime}:00`);
    return now.getTime() < endAt.getTime();
  });
```

- [ ] **步骤 4：接入创建页校验**

在 `frontend/src/pages/CreateTaskPage.tsx` 的 `handleSubmit` 中，定时发送字段校验后添加：

```ts
    if (
      scheduleType === 'scheduled' &&
      endTime &&
      normalizedScheduledDates.length > 0 &&
      !hasFutureScheduleWindow(normalizedScheduledDates, endTime)
    ) {
      validationErrors.push('当前定时发送窗口已全部过期，请重新选择发送日期或结束时间');
    }
```

并从 `scheduleDates` 导入 `hasFutureScheduleWindow`。

- [ ] **步骤 5：运行测试验证通过**

运行：

```powershell
cd frontend; npm run test -- scheduleDates
```

预期：`scheduleDates` 测试通过。

- [ ] **步骤 6：Commit**

```powershell
git add frontend/src/pages/CreateTaskPage.tsx frontend/src/features/create-task/client/scheduleDates.ts frontend/src/features/create-task/client/scheduleDates.test.ts
git commit -m "feat(frontend): validate expired schedule windows"
```

## 任务 7：全量验证与收尾

**文件：**
- 修改：按前面任务实际变更文件。

- [ ] **步骤 1：运行后端窗口与调度测试**

运行：

```powershell
cd backend; uv run python -m unittest backend.test.test_batch_schedule backend.test.test_batch_task_dispatch_schedule
```

预期：`OK`。

- [ ] **步骤 2：运行后端接口测试**

运行：

```powershell
cd backend; uv run python -m unittest backend.test.test_api_endpoints
```

预期：`OK`。如果耗时较长，至少必须运行任务 4 中新增的 3 个接口测试和所有已有批量任务相关接口测试。

- [ ] **步骤 3：运行前端测试和 lint**

运行：

```powershell
cd frontend; npm run test -- TasksPage scheduleDates
cd frontend; npm run lint
```

预期：测试通过，lint 无错误。

- [ ] **步骤 4：检查 Git diff**

运行：

```powershell
git diff --stat
git diff --check
git status --short
```

预期：没有空白错误；变更文件只包含本功能相关文件和计划/规格文件。

- [ ] **步骤 5：最终 Commit**

如果前面任务还剩未提交修正，提交：

```powershell
git add backend/app frontend/src backend/test frontend/src/pages/TasksPage.test.tsx frontend/src/features/create-task/client
git commit -m "feat: handle expired scheduled batch tasks"
```

如果没有剩余 diff，不需要额外提交。

## 自检

- 规格覆盖度：计划覆盖了状态模型、后台调度、审核批准、创建防呆、暂停恢复、前端展示、操作日志和测试范围。
- 占位符扫描：计划中没有未完成标记或未定义占位步骤。
- 类型一致性：后端新增 `expired` 和 `schedule_expired`；前端对应新增 `BatchTaskRuntimeStatus` 与 `WorkspaceTaskCancellationReason` 类型；显示文案使用「已过期」和「发送窗口已过期」。
- 范围控制：第一版不实现重新安排发送时间、自动复制任务或提醒系统。
