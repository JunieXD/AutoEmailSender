# 首页导师状态模型实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 将首页导师状态改为「未开始 / 准备中 / 待发送 / 已联系 / 已回复 / 失败」，移除「需处理」，避免任务取消污染导师关系状态。

**架构：** 后端集中维护首页状态派生函数，`list_professors` 只负责查询任务和发送记录并调用派生函数。前端只消费后端状态值，更新类型、文案和筛选测试。

**技术栈：** FastAPI、SQLAlchemy、unittest、React、TypeScript、Vitest。

---

## 文件结构

- 修改：`backend/app/api/professors.py`
  - 职责：收敛 `_map_dashboard_status` 派生规则，支持 `sent_count` 输入，移除 `needs_attention` 返回值。
- 修改：`backend/app/schemas/professor.py`
  - 职责：把 `ProfessorDashboardStatus` 从 `needs_attention` 改为 `failed`。
- 修改：`backend/test/test_api_endpoints.py`
  - 职责：覆盖后端首页状态矩阵，特别是 `canceled`、`draft_failed`、已联系后续失败。
- 修改：`frontend/src/types/index.ts`
  - 职责：把 `ProfessorDashboardStatus` 类型从 `needs_attention` 改为 `failed`。
- 修改：`frontend/src/features/professor-status/dashboardStatus.ts`
  - 职责：文案表移除「需处理」，新增「失败」。
- 修改：`frontend/test/professorDashboardStatus.test.ts`
  - 职责：覆盖新文案，不再期待 `needs_attention`。
- 修改：`frontend/test/HomePageOnboarding.test.tsx`
  - 职责：状态筛选测试从「需处理」改为「失败」。

## 任务 1：后端状态派生规则

**文件：**
- 修改：`backend/test/test_api_endpoints.py`
- 修改：`backend/app/api/professors.py`
- 修改：`backend/app/schemas/professor.py`

- [ ] **步骤 1：编写失败测试**

在 `ApiEndpointTests` 中更新 `test_professor_dashboard_returns_contact_state_labels`：

```python
professor_cases = [
    ("未联系导师", "dashboard-not-contacted@example.edu", None, "not_contacted"),
    ("准备中导师", "dashboard-preparing@example.edu", "matched", "preparing"),
    ("生成中导师", "dashboard-generating@example.edu", "generating_draft", "preparing"),
    ("待审核导师", "dashboard-review@example.edu", "review_required", "preparing"),
    ("approved 导师", "dashboard-approved@example.edu", "approved", "ready_to_send"),
    ("待发送导师", "dashboard-ready@example.edu", "scheduled", "ready_to_send"),
    ("草稿失败导师", "dashboard-draft-failed@example.edu", "draft_failed", "failed"),
    ("send_failed 导师", "dashboard-send-failed@example.edu", "send_failed", "failed"),
    ("已取消导师", "dashboard-canceled@example.edu", "canceled", "not_contacted"),
    ("已联系导师", "dashboard-contacted@example.edu", "sent", "contacted"),
    ("已回复导师", "dashboard-replied@example.edu", "reply_detected", "replied"),
]
```

把 `assertNotIn` 集合更新为：

```python
{"matched", "scheduled", "sent", "skipped", "send_failed", "needs_attention"}
```

新增两个测试：

```python
def test_professor_dashboard_keeps_contacted_when_later_task_is_canceled(self) -> None:
    ...
    self.assertEqual(professor["status"], "contacted")

def test_professor_dashboard_keeps_contacted_when_later_task_fails(self) -> None:
    ...
    self.assertEqual(professor["status"], "contacted")
```

两个测试均创建导师、创建首次任务并写为 `sent`，插入一条 `email_logs.direction = 'sent'`，再插入更晚的 `canceled` 或 `send_failed` 任务。

- [ ] **步骤 2：运行后端测试验证失败**

运行：

```powershell
cd backend
uv run python -m unittest test.test_api_endpoints.ApiEndpointTests.test_professor_dashboard_returns_contact_state_labels test.test_api_endpoints.ApiEndpointTests.test_professor_dashboard_keeps_contacted_when_later_task_is_canceled test.test_api_endpoints.ApiEndpointTests.test_professor_dashboard_keeps_contacted_when_later_task_fails
```

预期：失败，原因包括 `failed` 不在 schema 枚举中、`canceled` 仍映射为 `needs_attention`、已联系后续取消仍不是 `contacted`。

- [ ] **步骤 3：实现后端最小改动**

在 `backend/app/schemas/professor.py` 中：

```python
ProfessorDashboardStatus = Literal[
    "not_contacted",
    "preparing",
    "ready_to_send",
    "contacted",
    "replied",
    "failed",
]
```

在 `backend/app/api/professors.py` 中，把调用改为：

```python
status=_map_dashboard_status(
    tasks_by_professor.get(professor.id, []),
    sent_count_by_professor.get(professor.id, 0),
),
```

把函数签名和规则改为：

```python
def _map_dashboard_status(tasks: list[EmailTask], sent_count: int = 0) -> str:
    if any(
        task.is_replied or task.status == EmailTaskStatus.REPLY_DETECTED.value
        for task in tasks
    ):
        return "replied"

    if sent_count > 0 or any(task.status == EmailTaskStatus.SENT.value or task.sent_at for task in tasks):
        return "contacted"

    if not tasks:
        return "not_contacted"

    latest_task = tasks[0]
    if latest_task.status in {
        EmailTaskStatus.DRAFT_FAILED.value,
        EmailTaskStatus.SEND_FAILED.value,
    }:
        return "failed"

    if latest_task.status in {
        EmailTaskStatus.APPROVED.value,
        EmailTaskStatus.SCHEDULED.value,
    }:
        return "ready_to_send"

    if latest_task.status in {
        EmailTaskStatus.DISCOVERED.value,
        EmailTaskStatus.MATCHED.value,
        EmailTaskStatus.GENERATING_DRAFT.value,
        EmailTaskStatus.REVIEW_REQUIRED.value,
    }:
        return "preparing"

    return "not_contacted"
```

- [ ] **步骤 4：运行后端测试验证通过**

运行同一步骤 2 命令。

预期：3 个测试通过。

## 任务 2：前端状态类型和文案

**文件：**
- 修改：`frontend/src/types/index.ts`
- 修改：`frontend/src/features/professor-status/dashboardStatus.ts`
- 修改：`frontend/test/professorDashboardStatus.test.ts`
- 修改：`frontend/test/HomePageOnboarding.test.tsx`

- [ ] **步骤 1：编写失败测试**

更新 `frontend/test/professorDashboardStatus.test.ts`：

```typescript
expect(PROFESSOR_DASHBOARD_STATUS_LABELS.failed).toBe("失败");
expect(PROFESSOR_DASHBOARD_STATUS_LABELS).not.toHaveProperty("needs_attention");
```

把状态选项参数用例改为：

```typescript
["failed", "失败"],
```

更新 `frontend/test/HomePageOnboarding.test.tsx`：

```typescript
createProfessor(106, "失败导师", "failed"),
expect(screen.getByRole("option", { name: "失败" })).toBeInTheDocument();
expect(screen.queryByText("失败导师")).not.toBeInTheDocument();
```

- [ ] **步骤 2：运行前端测试验证失败**

运行：

```powershell
cd frontend
npm run test -- professorDashboardStatus HomePageOnboarding
```

预期：失败，原因是 `failed` 文案不存在，`needs_attention` 仍存在。

- [ ] **步骤 3：实现前端最小改动**

在 `frontend/src/types/index.ts` 中：

```typescript
export type ProfessorDashboardStatus =
  | 'not_contacted'
  | 'preparing'
  | 'ready_to_send'
  | 'contacted'
  | 'replied'
  | 'failed';
```

在 `frontend/src/features/professor-status/dashboardStatus.ts` 中：

```typescript
export const PROFESSOR_DASHBOARD_STATUS_LABELS: Record<ProfessorDashboardStatus, string> = {
  not_contacted: "未开始",
  preparing: "准备中",
  ready_to_send: "待发送",
  contacted: "已联系",
  replied: "已回复",
  failed: "失败",
};
```

- [ ] **步骤 4：运行前端测试验证通过**

运行同一步骤 2 命令。

预期：相关测试通过。

## 任务 3：回归验证

**文件：**
- 无新增文件。

- [ ] **步骤 1：运行后端目标测试**

```powershell
cd backend
uv run python -m unittest test.test_api_endpoints.ApiEndpointTests.test_professor_dashboard_returns_contact_state_labels test.test_api_endpoints.ApiEndpointTests.test_professor_dashboard_prioritizes_existing_contact_over_follow_up_draft test.test_api_endpoints.ApiEndpointTests.test_professor_dashboard_keeps_contacted_when_later_task_is_canceled test.test_api_endpoints.ApiEndpointTests.test_professor_dashboard_keeps_contacted_when_later_task_fails
```

预期：全部通过。

- [ ] **步骤 2：运行前端目标测试**

```powershell
cd frontend
npm run test -- professorDashboardStatus HomePageOnboarding
```

预期：全部通过。

- [ ] **步骤 3：运行前端 lint**

```powershell
cd frontend
npm run lint
```

预期：通过，或只出现与本次改动无关的既有问题。若出现本次改动引入的问题，修复后重跑。

- [ ] **步骤 4：检查未提交改动范围**

```powershell
git status --short
git diff -- backend/app/api/professors.py backend/app/schemas/professor.py backend/test/test_api_endpoints.py frontend/src/types/index.ts frontend/src/features/professor-status/dashboardStatus.ts frontend/test/professorDashboardStatus.test.ts frontend/test/HomePageOnboarding.test.tsx
```

预期：只包含本计划列出的文件改动；用户已有无关改动保留不动。
