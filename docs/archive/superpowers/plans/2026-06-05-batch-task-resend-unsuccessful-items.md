# 批量任务未成功项重新发起实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 在批量任务详情页选择未成功触达的老师，带入原任务身份、模板和材料，跳转创建页重新创建一批新任务。

**架构：** 后端新增只读 `GET /api/batch-tasks/{task_id}/resend-context`，集中返回候选项、不可用原因和创建页预填快照。前端在任务详情页负责选择和确认，使用 `sessionStorage` 把老师列表与预填上下文交给创建页；创建页只预填身份相关配置，不继承旧 LLM 和旧排程。

**技术栈：** FastAPI、SQLAlchemy async、Pydantic、React、TypeScript、Vite、Vitest、Testing Library、PowerShell 7、uv。

---

## 文件结构

- 创建：`backend/app/services/batch_task_resend_context.py`：候选判断、原因文案、材料过滤。
- 修改：`backend/app/schemas/batch_task.py`：新增重发上下文响应 schema。
- 修改：`backend/app/api/batch_tasks.py`：新增 `resend-context` 路由。
- 修改：`backend/test/test_api_endpoints.py`：新增后端接口集成测试。
- 创建：`frontend/src/features/batch-tasks/client/batchTaskResendPrefill.ts`：重发预填上下文的 `sessionStorage` 读写和清理。
- 创建：`frontend/src/features/batch-tasks/client/batchTaskResendPrefill.test.ts`：覆盖预填上下文存储。
- 创建：`frontend/src/features/batch-tasks/components/BatchTaskResendDialog.tsx`：任务详情页选择面板。
- 修改：`frontend/src/types/index.ts`：新增重发上下文 DTO。
- 修改：`frontend/src/lib/api/batchTasksApi.ts`：新增 `getBatchTaskResendContext`。
- 修改：`frontend/src/pages/TasksPage.tsx`：入口、选择、确认、写入上下文、跳转。
- 修改：`frontend/src/pages/TasksPage.test.tsx`：覆盖任务详情页交互。
- 修改：`frontend/src/pages/CreateTaskPage.tsx`：读取上下文并预填创建表单。
- 修改：`frontend/src/pages/CreateTaskPage.test.tsx`：覆盖创建页预填与清理。

---

### 任务 1：后端候选规则服务

**文件：**
- 创建：`backend/app/services/batch_task_resend_context.py`
- 修改：`backend/test/test_api_endpoints.py`

- [ ] **步骤 1：编写失败的接口测试**

在 `backend/test/test_api_endpoints.py` 的批量任务测试附近新增：

```python
def test_batch_task_resend_context_selects_unsuccessful_items(self) -> None:
    identity_id = self._create_identity(with_imap=False)
    llm_id = self._create_llm()
    professor_ids = [
        self._create_professor(name="过期导师", email="expired-resend@example.edu"),
        self._create_professor(name="中止导师", email="stopped-resend@example.edu"),
        self._create_professor(name="失败导师", email="failed-resend@example.edu"),
        self._create_professor(name="已发导师", email="sent-resend@example.edu"),
        self._create_professor(name="移除导师", email="removed-resend@example.edu"),
    ]
    batch_task_id = self._insert_batch_task_with_material(
        identity_id=identity_id,
        llm_id=llm_id,
        status="expired",
        primary_material_id=None,
        name="原批量任务",
        schedule_type="scheduled",
        email_subject="原主题 {{name}}",
        email_body="原正文 {{sender_name}}",
    )
    rows = [
        (professor_ids[0], "canceled", "schedule_expired"),
        (professor_ids[1], "canceled", "batch_stopped"),
        (professor_ids[2], "send_failed", None),
        (professor_ids[3], "sent", None),
        (professor_ids[4], "canceled", "user_removed"),
    ]
    for professor_id, status, reason in rows:
        self._insert_email_task_with_material(
            identity_id=identity_id,
            llm_id=llm_id,
            professor_id=professor_id,
            status=status,
            cancellation_reason=reason,
            primary_material_id=None,
            batch_task_id=batch_task_id,
            source="batch",
            outreach_generation_mode="llm",
            outreach_template_subject="原主题 {{name}}",
            outreach_template_body_text="原正文 {{sender_name}}",
            outreach_template_body_html="<p>原正文 {{sender_name}}</p>",
        )

    response = self.client.get(f"/api/batch-tasks/{batch_task_id}/resend-context")

    self.assertEqual(response.status_code, 200, msg=response.text)
    payload = response.json()
    self.assertEqual(payload["defaults"]["identity_id"], identity_id)
    self.assertNotIn("llm_profile_id", payload["defaults"])
    self.assertNotIn("scheduled_dates", payload["defaults"])
    selectable_items = [item for item in payload["items"] if item["selectable"]]
    self.assertEqual([item["professor_id"] for item in selectable_items], professor_ids[:3])
    self.assertEqual(payload["summary"]["candidate_count"], 3)
    self.assertTrue(all(item["default_selected"] for item in selectable_items))
```

如果现有 helper 参数不完全一致，按 helper 真实签名调整数据创建方式，但保留断言语义。

- [ ] **步骤 2：运行测试验证失败**

运行：`cd backend; uv run python -m unittest test.test_api_endpoints.ApiEndpointTests.test_batch_task_resend_context_selects_unsuccessful_items`

预期：FAIL，原因是接口或服务尚不存在。

- [ ] **步骤 3：实现候选规则服务**

创建 `backend/app/services/batch_task_resend_context.py`：

```python
from __future__ import annotations

from dataclasses import dataclass

from app.models import EmailTask, EmailTaskCancellationReason, EmailTaskStatus, IdentityMaterial
from app.services.materials import material_can_be_primary

SUCCESS_STATUSES = {EmailTaskStatus.SENT.value, EmailTaskStatus.REPLY_DETECTED.value}
EXCLUDED_RUNNING_STATUSES = {EmailTaskStatus.SENDING.value}

REASON_LABELS: dict[tuple[str, str | None], str] = {
    (EmailTaskStatus.CANCELED.value, EmailTaskCancellationReason.SCHEDULE_EXPIRED.value): "发送窗口已过期",
    (EmailTaskStatus.CANCELED.value, EmailTaskCancellationReason.BATCH_STOPPED.value): "任务中止后未发送",
    (EmailTaskStatus.SEND_FAILED.value, None): "发送失败",
    (EmailTaskStatus.DRAFT_FAILED.value, None): "草稿生成失败",
    (EmailTaskStatus.REVIEW_REQUIRED.value, None): "待审核未发送",
    (EmailTaskStatus.APPROVED.value, None): "已批准未发送",
    (EmailTaskStatus.SCHEDULED.value, None): "已排程未发送",
    (EmailTaskStatus.DISCOVERED.value, None): "尚未完成发信准备",
    (EmailTaskStatus.MATCHED.value, None): "尚未完成发信准备",
    (EmailTaskStatus.GENERATING_DRAFT.value, None): "尚未完成发信准备",
}

@dataclass(frozen=True)
class ResendItemDecision:
    selectable: bool
    default_selected: bool
    reason_label: str
    unavailable_reason: str | None


def decide_resend_item(email_task: EmailTask) -> ResendItemDecision:
    professor = email_task.professor
    if professor is None:
        return ResendItemDecision(False, False, "导师不存在", "导师已不存在，未带入新任务")
    if professor.archived_at is not None:
        return ResendItemDecision(False, False, "导师已归档", "导师已归档，未带入新任务")
    if email_task.status in SUCCESS_STATUSES:
        return ResendItemDecision(False, False, "已成功触达", "已成功触达，未带入新任务")
    if email_task.status == EmailTaskStatus.CANCELED.value and email_task.cancellation_reason == EmailTaskCancellationReason.USER_REMOVED.value:
        return ResendItemDecision(False, False, "用户已移除", "已从原任务单独移除，未带入新任务")
    if email_task.status in EXCLUDED_RUNNING_STATUSES:
        return ResendItemDecision(False, False, "发送中", "正在发送的邮件未带入新任务")
    reason_label = REASON_LABELS.get((email_task.status, email_task.cancellation_reason), REASON_LABELS.get((email_task.status, None), "未成功触达"))
    return ResendItemDecision(True, True, reason_label, None)


def filter_available_material_defaults(*, materials: list[IdentityMaterial], primary_material_id: int | None, selected_material_ids: list[int] | None) -> tuple[int | None, list[int], list[str]]:
    material_by_id = {material.id: material for material in materials}
    warnings: list[str] = []
    next_primary_id = None
    if primary_material_id is not None:
        material = material_by_id.get(primary_material_id)
        if material is not None and material_can_be_primary(material):
            next_primary_id = primary_material_id
        else:
            warnings.append("部分原材料已不存在或不再支持分析，未带入新任务")
    next_selected_ids = [material_id for material_id in (selected_material_ids or []) if material_id in material_by_id]
    if selected_material_ids and len(next_selected_ids) != len(selected_material_ids):
        warnings.append("部分原随信附件已不存在，未带入新任务")
    return next_primary_id, next_selected_ids, list(dict.fromkeys(warnings))
```

- [ ] **步骤 4：运行测试确认失败推进到 schema/路由**

运行同步骤 2 命令。

预期：仍 FAIL，但不应出现新服务文件语法错误。

- [ ] **步骤 5：Commit**

运行：`git add backend/app/services/batch_task_resend_context.py backend/test/test_api_endpoints.py; git commit -m "test(批量任务): 覆盖未成功项重发候选规则"`

---### 任务 2：后端 schema 和 `resend-context` 接口

**文件：**
- 修改：`backend/app/schemas/batch_task.py`
- 修改：`backend/app/api/batch_tasks.py`
- 修改：`backend/test/test_api_endpoints.py`

- [ ] **步骤 1：补充失败测试：材料过滤和原身份缺失**

新增两个测试：

```python
def test_batch_task_resend_context_filters_deleted_material_defaults(self) -> None:
    identity_id = self._create_identity(with_imap=False)
    llm_id = self._create_llm()
    primary_id = self._upload_material(identity_id, filename="resume.txt", content=b"resume", material_type="resume")
    attachment_id = self._upload_material(identity_id, filename="paper.pdf", content=b"paper", material_type="publication")
    professor_id = self._create_professor(name="材料导师", email="material-resend@example.edu")
    batch_task_id = self._insert_batch_task_with_material(
        identity_id=identity_id,
        llm_id=llm_id,
        status="expired",
        primary_material_id=primary_id,
        selected_material_ids=[attachment_id, 999999],
        name="材料任务",
    )
    self._insert_email_task_with_material(
        identity_id=identity_id,
        llm_id=llm_id,
        professor_id=professor_id,
        status="canceled",
        cancellation_reason="schedule_expired",
        primary_material_id=primary_id,
        batch_task_id=batch_task_id,
        source="batch",
    )
    connection = sqlite3.connect(self.db_path)
    try:
        connection.execute("DELETE FROM identity_materials WHERE id = ?", (primary_id,))
        connection.commit()
    finally:
        connection.close()

    response = self.client.get(f"/api/batch-tasks/{batch_task_id}/resend-context")

    self.assertEqual(response.status_code, 200, msg=response.text)
    payload = response.json()
    self.assertIsNone(payload["defaults"]["primary_material_id"])
    self.assertEqual(payload["defaults"]["selected_material_ids"], [attachment_id])
    self.assertTrue(any("材料" in warning for warning in payload["warnings"]))


def test_batch_task_resend_context_rejects_missing_identity(self) -> None:
    identity_id = self._create_identity(with_imap=False)
    llm_id = self._create_llm()
    batch_task_id = self._insert_batch_task_with_material(
        identity_id=identity_id,
        llm_id=llm_id,
        status="expired",
        primary_material_id=None,
        name="身份被删任务",
    )
    connection = sqlite3.connect(self.db_path)
    try:
        connection.execute("DELETE FROM identity_profiles WHERE id = ?", (identity_id,))
        connection.commit()
    finally:
        connection.close()

    response = self.client.get(f"/api/batch-tasks/{batch_task_id}/resend-context")

    self.assertEqual(response.status_code, 400)
    self.assertEqual(response.json()["detail"], "原任务身份已不存在，无法直接重新发起。")
```

如果 SQLite 外键阻止删除身份，则改为直接把 `batch_tasks.identity_id` 更新成不存在的 ID，并断言同一错误。

- [ ] **步骤 2：运行测试验证失败**

运行：`cd backend; uv run python -m unittest test.test_api_endpoints.ApiEndpointTests.test_batch_task_resend_context_filters_deleted_material_defaults test.test_api_endpoints.ApiEndpointTests.test_batch_task_resend_context_rejects_missing_identity`

预期：FAIL，接口尚未实现。

- [ ] **步骤 3：新增 schema**

在 `backend/app/schemas/batch_task.py` 添加：

```python
class BatchTaskResendContextTaskRead(ApiSchema):
    id: int
    name: str
    identity_id: int
    schedule_type: str

class BatchTaskResendDefaultsRead(ApiSchema):
    identity_id: int
    outreach_generation_mode: str | None
    outreach_template_subject: str | None
    outreach_template_body_text: str | None
    outreach_template_body_html: str | None
    primary_material_id: int | None
    selected_material_ids: list[int]

class BatchTaskResendItemRead(ApiSchema):
    email_task_id: int
    professor_id: int | None
    professor_name: str
    professor_email: str | None
    status: str
    cancellation_reason: str | None
    reason_label: str
    default_selected: bool
    selectable: bool
    unavailable_reason: str | None
    updated_at: datetime

class BatchTaskResendSummaryRead(ApiSchema):
    candidate_count: int
    default_selected_count: int
    unavailable_count: int

class BatchTaskResendContextRead(ApiSchema):
    task: BatchTaskResendContextTaskRead
    defaults: BatchTaskResendDefaultsRead
    items: list[BatchTaskResendItemRead]
    summary: BatchTaskResendSummaryRead
    warnings: list[str]
```

- [ ] **步骤 4：新增 API 路由**

在 `backend/app/api/batch_tasks.py` 导入新 schema 和服务函数。新增路由放在 `/{task_id}/items` 路由之前：

```python
@router.get("/{task_id}/resend-context", response_model=BatchTaskResendContextRead)
async def get_batch_task_resend_context(
    task_id: int,
    session: AsyncSession = Depends(get_async_session),
) -> BatchTaskResendContextRead:
    task = await session.scalar(
        select(BatchTask)
        .options(selectinload(BatchTask.email_tasks).selectinload(EmailTask.professor))
        .where(BatchTask.id == task_id)
        .execution_options(populate_existing=True),
    )
    if task is None:
        raise HTTPException(status_code=404, detail="未找到批量任务")
    identity = await session.scalar(
        select(IdentityProfile)
        .options(selectinload(IdentityProfile.materials))
        .where(IdentityProfile.id == task.identity_id),
    )
    if identity is None:
        raise HTTPException(status_code=400, detail="原任务身份已不存在，无法直接重新发起。")

    primary_material_id, selected_material_ids, warnings = filter_available_material_defaults(
        materials=list(identity.materials),
        primary_material_id=task.primary_material_id,
        selected_material_ids=task.selected_material_ids,
    )
    sorted_email_tasks = sorted(task.email_tasks, key=lambda item: (item.created_at, item.id))
    snapshot_task = sorted_email_tasks[0] if sorted_email_tasks else None
    items = []
    for email_task in sorted_email_tasks:
        decision = decide_resend_item(email_task)
        professor = email_task.professor
        items.append(BatchTaskResendItemRead(
            email_task_id=email_task.id,
            professor_id=professor.id if professor else None,
            professor_name=professor.name if professor else "已删除导师",
            professor_email=professor.email if professor else None,
            status=email_task.status,
            cancellation_reason=email_task.cancellation_reason,
            reason_label=decision.reason_label,
            default_selected=decision.default_selected,
            selectable=decision.selectable,
            unavailable_reason=decision.unavailable_reason,
            updated_at=email_task.updated_at,
        ))
    return BatchTaskResendContextRead(
        task=BatchTaskResendContextTaskRead(id=task.id, name=task.name, identity_id=task.identity_id, schedule_type=task.schedule_type),
        defaults=BatchTaskResendDefaultsRead(
            identity_id=task.identity_id,
            outreach_generation_mode=snapshot_task.outreach_generation_mode if snapshot_task else identity.outreach_generation_mode,
            outreach_template_subject=snapshot_task.outreach_template_subject if snapshot_task else task.email_subject,
            outreach_template_body_text=snapshot_task.outreach_template_body_text if snapshot_task else task.email_body,
            outreach_template_body_html=snapshot_task.outreach_template_body_html if snapshot_task else None,
            primary_material_id=primary_material_id,
            selected_material_ids=selected_material_ids,
        ),
        items=items,
        summary=BatchTaskResendSummaryRead(
            candidate_count=sum(1 for item in items if item.selectable),
            default_selected_count=sum(1 for item in items if item.default_selected),
            unavailable_count=sum(1 for item in items if not item.selectable),
        ),
        warnings=warnings,
    )
```

- [ ] **步骤 5：运行后端聚焦测试**

运行：`cd backend; uv run python -m unittest test.test_api_endpoints.ApiEndpointTests.test_batch_task_resend_context_selects_unsuccessful_items test.test_api_endpoints.ApiEndpointTests.test_batch_task_resend_context_filters_deleted_material_defaults test.test_api_endpoints.ApiEndpointTests.test_batch_task_resend_context_rejects_missing_identity`

预期：PASS。

- [ ] **步骤 6：Commit**

运行：`git add backend/app/schemas/batch_task.py backend/app/api/batch_tasks.py backend/app/services/batch_task_resend_context.py backend/test/test_api_endpoints.py; git commit -m "feat(批量任务): 提供未成功项重发上下文接口"`

---

### 任务 3：前端 DTO、API 和预填存储 helper

**文件：**
- 修改：`frontend/src/types/index.ts`
- 修改：`frontend/src/lib/api/batchTasksApi.ts`
- 创建：`frontend/src/features/batch-tasks/client/batchTaskResendPrefill.ts`
- 创建：`frontend/src/features/batch-tasks/client/batchTaskResendPrefill.test.ts`
- 修改：`frontend/test/BatchTasksApi.test.ts`

- [ ] **步骤 1：编写失败的存储 helper 测试**

创建 `frontend/src/features/batch-tasks/client/batchTaskResendPrefill.test.ts`：

```ts
import { beforeEach, describe, expect, it } from "vitest";
import {
  BATCH_RESEND_PREFILL_CONTEXT_KEY,
  clearBatchResendPrefillContext,
  readBatchResendPrefillContext,
  writeBatchResendPrefillContext,
} from "./batchTaskResendPrefill";

const context = {
  sourceTaskId: 12,
  sourceTaskName: "原任务",
  identityId: 3,
  professorIds: [88, 89],
  defaults: {
    identity_id: 3,
    outreach_generation_mode: "template" as const,
    outreach_template_subject: "主题 {{name}}",
    outreach_template_body_text: "正文 {{sender_name}}",
    outreach_template_body_html: "<p>正文 {{sender_name}}</p>",
    primary_material_id: 10,
    selected_material_ids: [11, 12],
  },
  warnings: ["部分原材料已不存在，未带入新任务"],
};

describe("batchTaskResendPrefill", () => {
  beforeEach(() => window.sessionStorage.clear());

  it("writes and reads resend prefill context", () => {
    writeBatchResendPrefillContext(context);
    expect(readBatchResendPrefillContext()).toEqual(context);
  });

  it("returns null and clears invalid JSON", () => {
    window.sessionStorage.setItem(BATCH_RESEND_PREFILL_CONTEXT_KEY, "{");
    expect(readBatchResendPrefillContext()).toBeNull();
    expect(window.sessionStorage.getItem(BATCH_RESEND_PREFILL_CONTEXT_KEY)).toBeNull();
  });

  it("clears resend prefill context", () => {
    writeBatchResendPrefillContext(context);
    clearBatchResendPrefillContext();
    expect(readBatchResendPrefillContext()).toBeNull();
  });
});
```

- [ ] **步骤 2：运行测试验证失败**

运行：`cd frontend; npm run test -- src/features/batch-tasks/client/batchTaskResendPrefill.test.ts`

预期：FAIL，模块不存在。

- [ ] **步骤 3：新增前端 DTO**

在 `frontend/src/types/index.ts` 的批量任务类型附近添加：

```ts
export interface BatchTaskResendContextTaskDTO {
  id: number;
  name: string;
  identity_id: number;
  schedule_type: "immediate" | "scheduled";
}

export interface BatchTaskResendDefaultsDTO {
  identity_id: number;
  outreach_generation_mode: OutreachGenerationMode | null;
  outreach_template_subject: string | null;
  outreach_template_body_text: string | null;
  outreach_template_body_html: string | null;
  primary_material_id: number | null;
  selected_material_ids: number[];
}

export interface BatchTaskResendItemDTO {
  email_task_id: number;
  professor_id: number | null;
  professor_name: string;
  professor_email: string | null;
  status: WorkspaceTaskStatus;
  cancellation_reason: string | null;
  reason_label: string;
  default_selected: boolean;
  selectable: boolean;
  unavailable_reason: string | null;
  updated_at: string;
}

export interface BatchTaskResendContextDTO {
  task: BatchTaskResendContextTaskDTO;
  defaults: BatchTaskResendDefaultsDTO;
  items: BatchTaskResendItemDTO[];
  summary: { candidate_count: number; default_selected_count: number; unavailable_count: number };
  warnings: string[];
}

export interface BatchTaskResendPrefillContextDTO {
  sourceTaskId: number;
  sourceTaskName: string;
  identityId: number;
  professorIds: number[];
  defaults: BatchTaskResendDefaultsDTO;
  warnings: string[];
}
```

- [ ] **步骤 4：实现存储 helper**

创建 `frontend/src/features/batch-tasks/client/batchTaskResendPrefill.ts`：

```ts
import type { BatchTaskResendPrefillContextDTO } from "@/types";

export const BATCH_RESEND_PREFILL_CONTEXT_KEY = "batch_resend_prefill_context";
export const SELECTED_PROFESSOR_IDS_KEY = "selected_professor_ids";

const isNumberArray = (value: unknown): value is number[] =>
  Array.isArray(value) && value.every((item) => Number.isFinite(item));

export const clearBatchResendPrefillContext = () => {
  window.sessionStorage.removeItem(BATCH_RESEND_PREFILL_CONTEXT_KEY);
};

export const readBatchResendPrefillContext = (): BatchTaskResendPrefillContextDTO | null => {
  try {
    const raw = window.sessionStorage.getItem(BATCH_RESEND_PREFILL_CONTEXT_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as Partial<BatchTaskResendPrefillContextDTO>;
    if (!Number.isFinite(parsed.sourceTaskId) || typeof parsed.sourceTaskName !== "string" || !Number.isFinite(parsed.identityId) || !isNumberArray(parsed.professorIds) || !parsed.defaults || parsed.defaults.identity_id !== parsed.identityId) {
      clearBatchResendPrefillContext();
      return null;
    }
    return parsed as BatchTaskResendPrefillContextDTO;
  } catch {
    clearBatchResendPrefillContext();
    return null;
  }
};

export const writeBatchResendPrefillContext = (context: BatchTaskResendPrefillContextDTO) => {
  window.sessionStorage.setItem(BATCH_RESEND_PREFILL_CONTEXT_KEY, JSON.stringify(context));
};

export const writeSelectedProfessorIdsForBatchTask = (professorIds: number[]) => {
  window.sessionStorage.setItem(SELECTED_PROFESSOR_IDS_KEY, JSON.stringify(professorIds));
};
```

- [ ] **步骤 5：新增 API 封装和 API 测试**

在 `frontend/src/lib/api/batchTasksApi.ts` 添加：

```ts
export const getBatchTaskResendContext = (taskId: number) =>
  apiFetch<BatchTaskResendContextDTO>(`/api/batch-tasks/${taskId}/resend-context`);
```

在 `frontend/test/BatchTasksApi.test.ts` 依照现有 fetch mock 风格添加测试，核心断言 URL 包含 `/api/batch-tasks/12/resend-context`。

- [ ] **步骤 6：运行前端聚焦测试**

运行：`cd frontend; npm run test -- src/features/batch-tasks/client/batchTaskResendPrefill.test.ts test/BatchTasksApi.test.ts`

预期：PASS。

- [ ] **步骤 7：Commit**

运行：`git add frontend/src/types/index.ts frontend/src/lib/api/batchTasksApi.ts frontend/src/features/batch-tasks/client/batchTaskResendPrefill.ts frontend/src/features/batch-tasks/client/batchTaskResendPrefill.test.ts frontend/test/BatchTasksApi.test.ts; git commit -m "feat(frontend): 添加批量任务重发预填上下文"`

---### 任务 4：任务详情页选择面板和跳转

**文件：**
- 创建：`frontend/src/features/batch-tasks/components/BatchTaskResendDialog.tsx`
- 修改：`frontend/src/pages/TasksPage.tsx`
- 修改：`frontend/src/pages/TasksPage.test.tsx`

- [ ] **步骤 1：编写失败测试：入口、默认全选、确认跳转**

在 `frontend/src/pages/TasksPage.test.tsx`：

- 给 `apiMocks` 增加 `getBatchTaskResendContext`。
- `@/lib/api/batchTasksApi` mock 导出 `getBatchTaskResendContext`。
- 给 `selectionMock` 增加 `setSelectedIdentityId: vi.fn()` 和 `setSelectedLlmProfileId: vi.fn()`。
- mock `react-router-dom` 的 `useNavigate` 为 `navigateMock`。

新增测试：

```ts
it("selects resend candidates and opens create task with original identity without changing llm", async () => {
  apiMocks.listBatchTasks.mockResolvedValue([
    buildBatchTask({ id: 12, name: "过期任务", status: "expired", failed_count: 1 }),
  ]);
  apiMocks.listBatchTaskItems.mockResolvedValue([]);
  apiMocks.getBatchTaskResendContext.mockResolvedValue({
    task: { id: 12, name: "过期任务", identity_id: 7, schedule_type: "scheduled" },
    defaults: {
      identity_id: 7,
      outreach_generation_mode: "template",
      outreach_template_subject: "原主题 {{name}}",
      outreach_template_body_text: "原正文",
      outreach_template_body_html: "<p>原正文</p>",
      primary_material_id: 10,
      selected_material_ids: [11],
    },
    items: [
      {
        email_task_id: 101,
        professor_id: 88,
        professor_name: "张三",
        professor_email: "zhang@example.edu",
        status: "canceled",
        cancellation_reason: "schedule_expired",
        reason_label: "发送窗口已过期",
        default_selected: true,
        selectable: true,
        unavailable_reason: null,
        updated_at: "2026-06-05T10:00:00",
      },
      {
        email_task_id: 102,
        professor_id: 89,
        professor_name: "李四",
        professor_email: "li@example.edu",
        status: "send_failed",
        cancellation_reason: null,
        reason_label: "发送失败",
        default_selected: true,
        selectable: true,
        unavailable_reason: null,
        updated_at: "2026-06-05T10:01:00",
      },
    ],
    summary: { candidate_count: 2, default_selected_count: 2, unavailable_count: 0 },
    warnings: [],
  });

  render(<MemoryRouter><TasksPage /></MemoryRouter>);

  fireEvent.click(await screen.findByRole("button", { name: "查看详情" }));
  fireEvent.click(await screen.findByRole("button", { name: "重新发起未成功项" }));
  expect(await screen.findByText("可重新发起 2 位，已选 2 位")).toBeInTheDocument();
  fireEvent.click(screen.getByLabelText("选择老师 张三"));
  expect(screen.getByText("可重新发起 2 位，已选 1 位")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "全选可发起" }));
  fireEvent.click(screen.getByRole("button", { name: "去创建新任务" }));

  await waitFor(() => expect(confirmMock).toHaveBeenCalledWith(expect.objectContaining({
    description: expect.stringContaining("模型使用当前已选择的模型"),
  })));
  expect(selectionMock.setSelectedIdentityId).toHaveBeenCalledWith(7);
  expect(selectionMock.setSelectedLlmProfileId).not.toHaveBeenCalled();
  expect(JSON.parse(window.sessionStorage.getItem("selected_professor_ids") ?? "[]")).toEqual([88, 89]);
  expect(JSON.parse(window.sessionStorage.getItem("batch_resend_prefill_context") ?? "{}")).toMatchObject({
    sourceTaskId: 12,
    identityId: 7,
    professorIds: [88, 89],
  });
  expect(navigateMock).toHaveBeenCalledWith("/create-task");
});
```

- [ ] **步骤 2：运行测试验证失败**

运行：`cd frontend; npm run test -- src/pages/TasksPage.test.tsx`

预期：FAIL，入口或组件尚不存在。

- [ ] **步骤 3：创建选择面板组件**

创建 `frontend/src/features/batch-tasks/components/BatchTaskResendDialog.tsx`。组件 props：

```ts
type BatchTaskResendDialogProps = {
  context: BatchTaskResendContextDTO | null;
  loading: boolean;
  selectedProfessorIds: number[];
  onSelectAll: () => void;
  onClear: () => void;
  onToggleProfessor: (professorId: number) => void;
  onClose: () => void;
  onSubmit: () => void;
};
```

组件必须渲染：

```tsx
const selectableItems = context?.items.filter((item) => item.selectable && item.professor_id !== null) ?? [];
const selectedCount = selectedProfessorIds.length;

<p>可重新发起 {selectableItems.length} 位，已选 {selectedCount} 位</p>
<button type="button" onClick={onSelectAll}>全选可发起</button>
<button type="button" onClick={onClear}>清空选择</button>
<button type="button" disabled={selectedCount === 0} onClick={onSubmit}>去创建新任务</button>

{selectableItems.map((item) => (
  <label key={item.email_task_id}>
    <input
      type="checkbox"
      checked={item.professor_id !== null && selectedProfessorIds.includes(item.professor_id)}
      onChange={() => item.professor_id !== null && onToggleProfessor(item.professor_id)}
      aria-label={`选择老师 ${item.professor_name}`}
    />
    <span>{item.professor_name}</span>
    <span>{item.professor_email ?? "暂无邮箱"}</span>
    <span>{item.reason_label}</span>
  </label>
))}
```

样式沿用 `TasksPage.tsx` 已有弹窗风格：固定遮罩、白色面板、`ui-btn-primary` / `ui-btn-secondary`，关闭按钮使用 `X` 图标。

- [ ] **步骤 4：接入 `TasksPage.tsx`**

在 `frontend/src/pages/TasksPage.tsx`：

- import `useNavigate`。
- import `getBatchTaskResendContext`。
- import `BatchTaskResendDialog`。
- import `writeBatchResendPrefillContext` 和 `writeSelectedProfessorIdsForBatchTask`。
- 从 `useSelectionContext()` 解构 `setSelectedIdentityId`，不要调用 `setSelectedLlmProfileId`。

新增 state：

```ts
const [resendContext, setResendContext] = useState<BatchTaskResendContextDTO | null>(null);
const [resendLoading, setResendLoading] = useState(false);
const [resendDialogOpen, setResendDialogOpen] = useState(false);
const [selectedResendProfessorIds, setSelectedResendProfessorIds] = useState<number[]>([]);
```

新增入口展示条件：

```ts
const canOpenBatchResend = (task: BatchTaskCardDTO, view: TaskListView) =>
  view === "current" && ["expired", "stopped", "completed"].includes(task.status);
```

点击入口加载上下文：

```ts
const handleOpenBatchResend = async (task: BatchTaskCardDTO) => {
  setResendDialogOpen(true);
  setResendLoading(true);
  setSelectedResendProfessorIds([]);
  try {
    const context = await getBatchTaskResendContext(task.id);
    setResendContext(context);
    setSelectedResendProfessorIds(
      context.items
        .filter((item) => item.selectable && item.default_selected && item.professor_id !== null)
        .map((item) => item.professor_id as number),
    );
  } catch (error) {
    notifyError("加载可重新发起项失败", error instanceof Error ? error.message : "请稍后重试");
    setResendDialogOpen(false);
  } finally {
    setResendLoading(false);
  }
};
```

确认跳转：

```ts
const handleSubmitBatchResend = async () => {
  if (!resendContext || selectedResendProfessorIds.length === 0) return;
  const confirmed = await confirm({
    title: "确认重新发起这批老师？",
    description: "将自动切换到原任务身份，并带入原任务使用的发信模式、模板和材料。模型使用当前已选择的模型，发送日期和时间窗口需要重新设置。进入创建页后仍可修改这些内容。",
    confirmLabel: "去创建新任务",
    cancelLabel: "继续选择",
    tone: "danger",
  });
  if (!confirmed) return;
  setSelectedIdentityId(resendContext.task.identity_id);
  writeSelectedProfessorIdsForBatchTask(selectedResendProfessorIds);
  writeBatchResendPrefillContext({
    sourceTaskId: resendContext.task.id,
    sourceTaskName: resendContext.task.name,
    identityId: resendContext.task.identity_id,
    professorIds: selectedResendProfessorIds,
    defaults: resendContext.defaults,
    warnings: resendContext.warnings,
  });
  navigate("/create-task");
};
```

- [ ] **步骤 5：运行任务页测试**

运行：`cd frontend; npm run test -- src/pages/TasksPage.test.tsx`

预期：PASS。

- [ ] **步骤 6：Commit**

运行：`git add frontend/src/features/batch-tasks/components/BatchTaskResendDialog.tsx frontend/src/pages/TasksPage.tsx frontend/src/pages/TasksPage.test.tsx; git commit -m "feat(任务中心): 支持选择未成功项重新发起"`

---

### 任务 5：创建任务页读取重发预填上下文

**文件：**
- 修改：`frontend/src/pages/CreateTaskPage.tsx`
- 修改：`frontend/src/pages/CreateTaskPage.test.tsx`

- [ ] **步骤 1：编写失败测试：预填模板材料且不继承排程**

在 `frontend/src/pages/CreateTaskPage.test.tsx` 添加：

```ts
it("prefills resend context without carrying old schedule or llm profile", async () => {
  window.sessionStorage.setItem("selected_professor_ids", JSON.stringify([selectedProfessor.id]));
  window.sessionStorage.setItem("batch_resend_prefill_context", JSON.stringify({
    sourceTaskId: 12,
    sourceTaskName: "过期任务",
    identityId: selectedIdentity.id,
    professorIds: [selectedProfessor.id],
    defaults: {
      identity_id: selectedIdentity.id,
      outreach_generation_mode: "template",
      outreach_template_subject: "重发主题 {{name}}",
      outreach_template_body_text: "重发正文",
      outreach_template_body_html: "<p>重发正文</p>",
      primary_material_id: null,
      selected_material_ids: [7],
    },
    warnings: [],
  }));

  render(<MemoryRouter><CreateTaskPage /></MemoryRouter>);

  expect(await screen.findByText("张明")).toBeInTheDocument();
  expect(screen.getByText(/已从「过期任务」带入 1 位老师/)).toBeInTheDocument();
  expect(screen.getByDisplayValue("重新发起 - 过期任务")).toBeInTheDocument();
  expect(screen.getByLabelText("发送方式")).toHaveValue("immediate");
  fireEvent.click(screen.getByRole("button", { name: /创建任务/ }));

  await waitFor(() => expect(createBatchTaskMock).toHaveBeenCalledTimes(1));
  expect(createBatchTaskMock).toHaveBeenCalledWith(expect.objectContaining({
    llm_profile_id: selectedLlmProfile.id,
    outreach_generation_mode: "template",
    outreach_template_subject: "重发主题 {{name}}",
    outreach_template_body_text: "重发正文",
    outreach_template_body_html: "<p>重发正文</p>",
    selected_material_ids: [7],
    schedule_type: "immediate",
    scheduled_dates: null,
    window_start_time: null,
    window_end_time: null,
    emails_per_window: null,
  }));
});
```

- [ ] **步骤 2：编写失败测试：创建成功清理上下文**

```ts
it("clears resend prefill context after creating task", async () => {
  window.sessionStorage.setItem("batch_resend_prefill_context", JSON.stringify({
    sourceTaskId: 12,
    sourceTaskName: "过期任务",
    identityId: selectedIdentity.id,
    professorIds: [selectedProfessor.id],
    defaults: {
      identity_id: selectedIdentity.id,
      outreach_generation_mode: "llm",
      outreach_template_subject: "AI 主题",
      outreach_template_body_text: "AI 正文",
      outreach_template_body_html: "<p>AI 正文</p>",
      primary_material_id: null,
      selected_material_ids: [],
    },
    warnings: [],
  }));

  render(<MemoryRouter><CreateTaskPage /></MemoryRouter>);

  expect(await screen.findByText(selectedProfessor.name)).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: /创建任务/ }));

  await waitFor(() => expect(createBatchTaskMock).toHaveBeenCalledTimes(1));
  expect(window.sessionStorage.getItem("batch_resend_prefill_context")).toBeNull();
});
```

- [ ] **步骤 3：运行测试验证失败**

运行：`cd frontend; npm run test -- src/pages/CreateTaskPage.test.tsx`

预期：FAIL，创建页尚未读取重发上下文。

- [ ] **步骤 4：实现创建页预填**

在 `frontend/src/pages/CreateTaskPage.tsx` import：

```ts
import { clearBatchResendPrefillContext, readBatchResendPrefillContext } from '@/features/batch-tasks/client/batchTaskResendPrefill';
```

新增 state：

```ts
const [resendPrefillContext] = useState(() => readBatchResendPrefillContext());
const isResendPrefillActive = resendPrefillContext !== null && resendPrefillContext.identityId === selectedIdentityId;
```

在 `selectedIdentity` 初始化 effect 中，先保留现有身份默认值逻辑，再在末尾覆盖重发上下文：

```ts
if (isResendPrefillActive && resendPrefillContext) {
  setTaskName(`重新发起 - ${resendPrefillContext.sourceTaskName}`);
  const mode = resendPrefillContext.defaults.outreach_generation_mode ?? selectedIdentity.outreach_generation_mode ?? 'llm';
  setTaskMode(mode);
  const subjectValue = resendPrefillContext.defaults.outreach_template_subject ?? '';
  const bodyTextValue = resendPrefillContext.defaults.outreach_template_body_text ?? '';
  const bodyHtmlValue = resendPrefillContext.defaults.outreach_template_body_html ?? (bodyTextValue ? textToEmailHtml(bodyTextValue) : '');
  setSubject(subjectValue);
  setBody(bodyTextValue);
  setBodyHtml(bodyHtmlValue);
  setTemplateSubject(subjectValue);
  setTemplateBodyText(bodyTextValue);
  setTemplateBodyHtml(bodyHtmlValue);
  const materialIds = new Set(selectedIdentity.materials.map((material) => material.id));
  setPrimaryMaterialId(
    resendPrefillContext.defaults.primary_material_id !== null && materialIds.has(resendPrefillContext.defaults.primary_material_id)
      ? resendPrefillContext.defaults.primary_material_id
      : null,
  );
  setSelectedMaterialIds(resendPrefillContext.defaults.selected_material_ids.filter((id) => materialIds.has(id)));
}
```

在页面标题卡片内增加提示：

```tsx
{isResendPrefillActive && resendPrefillContext ? (
  <div className="mt-4 rounded-2xl border border-amber-200 bg-amber-50 px-4 py-3 text-sm leading-6 text-amber-900">
    已从「{resendPrefillContext.sourceTaskName}」带入 {resendPrefillContext.professorIds.length} 位老师、原身份、模板和材料。模型使用当前选择，发送时间需要重新设置；提交前可自行修改。
    {resendPrefillContext.warnings.map((warning) => (
      <span key={warning} className="mt-1 block text-xs text-amber-800">{warning}</span>
    ))}
  </div>
) : null}
```

创建成功时，在移除 `selected_professor_ids` 后调用 `clearBatchResendPrefillContext()`。再增加卸载清理：

```ts
useEffect(() => {
  return () => {
    clearBatchResendPrefillContext();
  };
}, []);
```

如果身份不匹配，创建页不应用上下文，并在加载后清理上下文；不要切换 LLM。

- [ ] **步骤 5：运行创建页测试**

运行：`cd frontend; npm run test -- src/pages/CreateTaskPage.test.tsx`

预期：PASS。

- [ ] **步骤 6：Commit**

运行：`git add frontend/src/pages/CreateTaskPage.tsx frontend/src/pages/CreateTaskPage.test.tsx; git commit -m "feat(创建任务): 支持重发上下文预填"`

---

### 任务 6：回归验证和收尾

**文件：**
- 修改：仅修正前面任务涉及文件。
- 可选修改：`docs/superpowers/specs/2026-06-05-batch-task-resend-unsuccessful-items-design.md`，仅当实现字段名和规格需要同步。

- [ ] **步骤 1：运行后端聚焦测试**

运行：`cd backend; uv run python -m unittest test.test_api_endpoints.ApiEndpointTests.test_batch_task_resend_context_selects_unsuccessful_items test.test_api_endpoints.ApiEndpointTests.test_batch_task_resend_context_filters_deleted_material_defaults test.test_api_endpoints.ApiEndpointTests.test_batch_task_resend_context_rejects_missing_identity`

预期：PASS。

- [ ] **步骤 2：运行完整后端测试**

运行：`cd backend; uv run python -m unittest discover test`

预期：PASS。若失败来自无关既有测试，记录测试名、错误摘要和与本变更无关的证据。

- [ ] **步骤 3：运行前端聚焦测试**

运行：`cd frontend; npm run test -- src/features/batch-tasks/client/batchTaskResendPrefill.test.ts src/pages/TasksPage.test.tsx src/pages/CreateTaskPage.test.tsx test/BatchTasksApi.test.ts`

预期：PASS。

- [ ] **步骤 4：运行前端 lint 和完整测试**

运行：`cd frontend; npm run lint; npm run test`

预期：PASS。若完整测试耗时或环境失败，保留聚焦测试和 lint 结果。

- [ ] **步骤 5：人工走查关键路径**

启动后端：`cd backend; uv run python dev_entry.py`

启动前端：`cd frontend; npm run dev`

打开 `http://127.0.0.1:5173/tasks`，检查：

- 过期/中止/完成批量任务详情页显示「重新发起未成功项」。
- 面板默认选中可发起老师，清空选择后提交按钮禁用。
- 确认文案说明自动切换身份、带入模板材料、模型使用当前选择、发送时间需重新设置。
- 创建页显示重发提示，任务名为 `重新发起 - 原任务名`。
- 创建页发送方式仍为默认立即发送，定时日期和窗口未继承。

- [ ] **步骤 6：检查 diff**

运行：`git diff --check; git status --short`

再查看关键 diff：

```powershell
git diff -- backend/app/services/batch_task_resend_context.py backend/app/schemas/batch_task.py backend/app/api/batch_tasks.py backend/test/test_api_endpoints.py frontend/src/types/index.ts frontend/src/lib/api/batchTasksApi.ts frontend/src/features/batch-tasks/client/batchTaskResendPrefill.ts frontend/src/features/batch-tasks/components/BatchTaskResendDialog.tsx frontend/src/pages/TasksPage.tsx frontend/src/pages/CreateTaskPage.tsx
```

预期：无 whitespace 错误；diff 只包含本功能相关变更。

- [ ] **步骤 7：最终 Commit**

如果前面任务已逐步 commit，本步骤只提交验证修正或文档同步；没有剩余变更则跳过。

运行：`git add backend frontend docs/superpowers/plans/2026-06-05-batch-task-resend-unsuccessful-items.md; git commit -m "feat(批量任务): 支持未成功项重新发起"`

---

## 自检

- 规格覆盖度：计划覆盖后端统一候选规则、`GET /api/batch-tasks/{task_id}/resend-context`、任务详情页入口、选择面板、全选/清空/逐项勾选、跳转确认、自动切换身份、不切换 LLM、`sessionStorage` 新键、创建页预填、不继承旧排程、创建成功和离页清理、测试与验证。
- 非目标保持：没有原地重试，没有新建批量任务草稿模型，没有自动创建新任务，没有复制旧邮件草稿、审核正文或发送日志。
- 类型一致性：后端响应字段使用 snake_case；前端 API DTO 保持 snake_case；session 预填上下文外层使用 camelCase，`defaults` 保持后端快照。
- 主要风险：`CreateTaskPage` 现有身份默认 effect 会覆盖表单，计划要求同一 effect 末尾二次应用重发上下文；`BatchTask.identity` 关系可能不存在，计划采用单独查询身份。