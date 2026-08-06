# 工作区独立发信实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 `superpowers:subagent-driven-development`（推荐）或 `superpowers:executing-plans` 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 工作区发信不再被旧批次 task 的终态锁死；当历史 task 已过期或取消时，进入工作区应自动切到一条可编辑、可发送的独立手动 task。

**架构：** 保留 `email_task` 作为审计和历史链路，不再把“能否在工作区继续发信”绑定到旧历史 task。后端在构建工作区线程时识别不可直接编辑的历史 task，并按现有手动继续规则创建一条新的 `manual` task 作为当前工作区承载体。前端仍然消费 `WorkspaceThreadDTO.current_task`，但它看到的是新创建的可发送 task，而不是旧的过期 task。

**技术栈：** FastAPI、SQLAlchemy、Pydantic、Vitest、React、TypeScript、SQLite。

---

### 任务 1：后端工作区语义切换

**文件：**
- 修改：`backend/app/api/workspace_support.py`
- 修改：`backend/app/api/workspaces.py`
- 修改：`backend/app/services/task_runtime.py`
- 修改：`backend/app/schemas/workspace.py`
- 修改：`backend/app/api/email_tasks.py`
- 测试：`backend/test/test_api_endpoints.py`
- 测试：`backend/test/test_workspace_support.py`

- [ ] **步骤 1：先写失败测试**

```python
async def test_workspace_thread_creates_manual_task_for_expired_history(self):
    # 先放一条 schedule_expired 的历史 task
    # 再请求 workspace thread
    # 断言 current_task 变成新 task
    # 断言新 task.source == "manual"
    # 断言新 task.parent_task_id 指向旧 task
    # 断言新 task.status 可继续编辑/发送
    ...
```

- [ ] **步骤 2：跑测试确认失败**

运行：
`cd backend && uv run python -m unittest backend.test.test_api_endpoints backend.test.test_workspace_support -v`

预期：
测试失败，提示 workspace 仍返回旧 `canceled / schedule_expired` task。

- [ ] **步骤 3：实现最小改动**

```python
# 1. 在 workspace_support 里抽出“当前可用 task”解析逻辑
# 2. 若最新历史 task 不能直接承载工作区发信，则复用现有手动继续语义创建新 task
# 3. 让 build_workspace_thread 返回新 task 而不是旧历史 task
# 4. 保持 email_logs 继续按 professor/identity/llm 读取，不改历史时间线
```

- [ ] **步骤 4：跑测试确认通过**

运行：
`cd backend && uv run python -m unittest backend.test.test_api_endpoints backend.test.test_workspace_support -v`

预期：
通过，且新 workspace task 能直接进入发送流程。

- [ ] **步骤 5：提交这一段改动**

```bash
git add backend/app/api/workspace_support.py backend/app/api/workspaces.py backend/app/services/task_runtime.py backend/app/schemas/workspace.py backend/app/api/email_tasks.py backend/test/test_api_endpoints.py backend/test/test_workspace_support.py
git commit -m "feat(workspace): decouple send session from expired task"
```

### 任务 2：前端改成消费“可发送工作区 task”

**文件：**
- 修改：`frontend/src/pages/WorkspacePage.tsx`
- 修改：`frontend/src/components/organisms/WorkspaceComposerDock.tsx`
- 修改：`frontend/src/lib/api/workspacesApi.ts`
- 修改：`frontend/src/types/index.ts`
- 测试：`frontend/src/components/organisms/WorkspaceSidebar.test.tsx`
- 测试：`frontend/src/pages/WorkspacePageNextStep.test.tsx`

- [ ] **步骤 1：先写失败测试**

```tsx
it("keeps composer enabled when workspace opens on expired history", async () => {
  // mock workspace thread returns a fresh manual current_task
  // assert send buttons stay enabled
  // assert next step no longer describes canceled history as hard stop
  ...
});
```

- [ ] **步骤 2：跑测试确认失败**

运行：
`cd frontend && npm run test -- WorkspacePageNextStep.test.tsx WorkspaceSidebar.test.tsx`

预期：
用旧逻辑时，测试仍会把 expired history 当成不可发。

- [ ] **步骤 3：实现最小改动**

```tsx
// 1. 去掉以旧历史 task.status 直接封死发送区的分支
// 2. 让页面只看后端返回的 current_task 是否可编辑
// 3. 保持 sidebar 仍然展示历史 task 语义，但不再把它当作发送权限本体
```

- [ ] **步骤 4：跑测试确认通过**

运行：
`cd frontend && npm run test -- WorkspacePageNextStep.test.tsx WorkspaceSidebar.test.tsx`

预期：
通过，发送区只受当前工作区 task 影响。

- [ ] **步骤 5：提交这一段改动**

```bash
git add frontend/src/pages/WorkspacePage.tsx frontend/src/components/organisms/WorkspaceComposerDock.tsx frontend/src/lib/api/workspacesApi.ts frontend/src/types/index.ts frontend/src/components/organisms/WorkspaceSidebar.test.tsx frontend/src/pages/WorkspacePageNextStep.test.tsx
git commit -m "feat(frontend): open workspace on active manual task"
```

### 任务 3：回归验证与收尾

**文件：**
- 修改：`frontend/test/HomePageMatchAnalysis.test.tsx` 或相关工作区入口测试
- 修改：`backend/test/test_operation_log_integration.py`（如工作区日志链路受影响）
- 可能修改：`docs/real_delivery_and_llm_implementation.md`

- [ ] **步骤 1：跑最小回归**

运行：
`cd backend && uv run python -m unittest discover test`

运行：
`cd frontend && npm run test`

- [ ] **步骤 2：检查工作区入口**

确认首页进入工作区后，旧 expired task 会被换成新的 manual task，而不是继续沿用旧状态。

- [ ] **步骤 3：补文档**

把“工作区 task = 可编辑会话，历史 task = 审计记录”写进产品/实现说明，避免后续再把权限绑回旧 task。

- [ ] **步骤 4：最终提交**

```bash
git add docs/real_delivery_and_llm_implementation.md
git commit -m "docs: clarify workspace task and audit split"
```

