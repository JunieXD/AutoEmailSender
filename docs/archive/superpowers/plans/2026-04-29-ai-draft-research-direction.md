# AI 生成草稿研究方向约束实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 让工作区 AI 生成草稿必须依赖导师研究方向，生成时轻微改写并贴合研究方向，同时在前端显示 token 消耗。

**架构：** 后端在 LLM 草稿生成入口做硬约束，前端同步禁用按钮并展示原因。Prompt 调整仍使用现有受控富文本 JSON，不扩展表格 schema；token 展示复用 Workspace DTO 已有字段。

**技术栈：** FastAPI、SQLAlchemy async、unittest、React、TypeScript、Vite、TailwindCSS、Tiptap。

---

## 文件结构

- 修改：`backend/app/services/task_runtime.py`
  - 在 LLM 草稿生成分支增加导师研究方向前置校验。
  - 保持模板模式不受影响。
- 修改：`backend/app/services/llm_runtime.py`
  - 强化 AI 草稿 prompt，要求基于研究方向轻微改写并尽量保留现有富文本格式。
- 修改：`backend/test/test_api_endpoints.py`
  - 覆盖研究方向为空时 AI 草稿接口失败、不调用 LLM、不新增 draft 日志。
  - 覆盖模板模式不受研究方向缺失影响。
- 修改：`backend/test/test_llm_runtime.py`
  - 覆盖 prompt 包含研究方向个性化和格式保留要求。
- 修改：`frontend/src/pages/WorkspacePage.tsx`
  - LLM 模式下 `canGenerateDraft` 增加导师 `research_direction` 判断。
- 修改：`frontend/src/components/organisms/WorkspaceComposerDock.tsx`
  - 增加 token 展示文案。
  - 增加研究方向缺失提示。

## 任务 1：后端研究方向硬约束测试

**文件：**
- 修改：`backend/test/test_api_endpoints.py`

- [ ] **步骤 1：编写失败测试：LLM 草稿缺少研究方向时返回 400**

在 `ApiEndpointTests` 中靠近工作区草稿相关测试处新增测试。复用该文件已有 helper 创建 identity、llm profile、professor、material 和 workspace task。测试代码应表达以下行为：

```python
def test_generate_draft_requires_professor_research_direction(self) -> None:
    identity_id = self._create_identity(with_imap=False)
    llm_profile_id = self._create_llm()
    material_id = self._upload_material(
        identity_id,
        filename="resume.txt",
        content=b"My background covers agent systems.",
        material_type="resume",
    )
    professor_response = self.client.post(
        "/api/professors",
        json={
            "name": "李老师",
            "email": "li@example.edu",
            "title": "Professor",
            "university": "Example University",
            "school": "School of Computing",
            "department": "Computer Science",
            "research_direction": None,
            "recent_papers": ["Agent paper"],
            "profile_url": None,
            "source_url": None,
        },
    )
    self.assertEqual(professor_response.status_code, 201, msg=professor_response.text)
    professor_id = professor_response.json()["id"]
    workspace = self.client.post(
        f"/api/workspaces/{professor_id}/ensure-task",
        params={"identity_id": identity_id, "llm_profile_id": llm_profile_id},
    )
    self.assertEqual(workspace.status_code, 200)
    task_id = workspace.json()["current_task"]["id"]

    with patch(
        "app.services.task_runtime.llm_runtime.generate_draft_content",
        AsyncMock(),
    ) as mocked_generate:
        response = self.client.post(f"/api/email-tasks/{task_id}/generate-draft")

    self.assertEqual(response.status_code, 400)
    self.assertIn("请先补充导师研究方向", response.json()["detail"])
    mocked_generate.assert_not_awaited()

    refreshed = self.client.get(
        f"/api/workspaces/{professor_id}",
        params={"identity_id": identity_id, "llm_profile_id": llm_profile_id},
    )
    self.assertEqual(refreshed.status_code, 200)
    self.assertFalse(
        any(message["direction"] == "draft" for message in refreshed.json()["messages"])
    )
```

- [ ] **步骤 2：运行测试验证失败**

运行：

```bash
cd backend
uv run python -m unittest test.test_api_endpoints.ApiEndpointTests.test_generate_draft_requires_professor_research_direction
```

预期：失败。可能失败点是响应仍为 200，或者 mock 被调用。

## 任务 2：实现后端研究方向硬约束

**文件：**
- 修改：`backend/app/services/task_runtime.py`

- [ ] **步骤 1：添加专用判断函数**

在 `_has_professor_match_evidence()` 附近新增：

```python
def _has_professor_research_direction(professor: Professor) -> bool:
    return bool((professor.research_direction or "").strip())
```

- [ ] **步骤 2：在 LLM 草稿分支调用判断**

在 `generate_task_draft()` 的 `else:` 分支中，位于 `if task.primary_material is None:` 检查之后、`ensure_material_extracted_text(task.primary_material)` 之前加入：

```python
if not _has_professor_research_direction(task.professor):
    raise ValueError("请先补充导师研究方向，再使用 AI 生成草稿")
```

不要放到模板模式分支外面，避免模板模式被误拦截。

- [ ] **步骤 3：运行后端单测验证通过**

运行：

```bash
cd backend
uv run python -m unittest test.test_api_endpoints.ApiEndpointTests.test_generate_draft_requires_professor_research_direction
```

预期：PASS。

- [ ] **步骤 4：Commit**

运行：

```bash
git add backend/app/services/task_runtime.py backend/test/test_api_endpoints.py
git commit -m "fix(backend): 要求 AI 草稿具备导师研究方向"
```

## 任务 3：模板模式不受研究方向缺失影响

**文件：**
- 修改：`backend/test/test_api_endpoints.py`

- [ ] **步骤 1：编写模板模式回归测试**

新增测试，确认导师研究方向为空时模板草稿仍可生成：

```python
def test_template_draft_does_not_require_professor_research_direction(self) -> None:
    identity_response = self.client.post(
        "/api/identities",
        json=self._build_identity_payload(
            with_imap=False,
            outreach_generation_mode="template",
            outreach_template_subject="申请与{{name}}老师交流",
            outreach_template_body_text="{{name}}老师您好，我是{{sender_name}}。",
        ),
    )
    self.assertEqual(identity_response.status_code, 201, msg=identity_response.text)
    identity_id = identity_response.json()["id"]
    llm_profile_id = self._create_llm()
    professor_response = self.client.post(
        "/api/professors",
        json={
            "name": "李老师",
            "email": "li-template@example.edu",
            "title": "Professor",
            "university": "Example University",
            "school": "School of Computing",
            "department": "Computer Science",
            "research_direction": None,
            "recent_papers": [],
            "profile_url": None,
            "source_url": None,
        },
    )
    self.assertEqual(professor_response.status_code, 201, msg=professor_response.text)
    professor_id = professor_response.json()["id"]
    workspace = self.client.post(
        f"/api/workspaces/{professor_id}/ensure-task",
        params={"identity_id": identity_id, "llm_profile_id": llm_profile_id},
    )
    self.assertEqual(workspace.status_code, 200)
    task_id = workspace.json()["current_task"]["id"]

    with patch(
        "app.services.task_runtime.llm_runtime.generate_draft_content",
        AsyncMock(),
    ) as mocked_generate:
        response = self.client.post(f"/api/email-tasks/{task_id}/generate-draft")

    self.assertEqual(response.status_code, 200)
    self.assertEqual(response.json()["current_task"]["status"], "review_required")
    self.assertEqual(response.json()["messages"][-1]["direction"], "draft")
    mocked_generate.assert_not_awaited()
```

- [ ] **步骤 2：运行回归测试**

运行：

```bash
cd backend
uv run python -m unittest test.test_api_endpoints.ApiEndpointTests.test_template_draft_does_not_require_professor_research_direction
```

预期：PASS。

- [ ] **步骤 3：运行两个草稿前置条件测试**

运行：

```bash
cd backend
uv run python -m unittest test.test_api_endpoints.ApiEndpointTests.test_generate_draft_requires_professor_research_direction test.test_api_endpoints.ApiEndpointTests.test_template_draft_does_not_require_professor_research_direction
```

预期：两个测试均 PASS。

- [ ] **步骤 4：Commit**

运行：

```bash
git add backend/test/test_api_endpoints.py
git commit -m "test(backend): 覆盖模板草稿研究方向豁免"
```

## 任务 4：强化草稿 prompt 的研究方向和格式要求

**文件：**
- 修改：`backend/test/test_llm_runtime.py`
- 修改：`backend/app/services/llm_runtime.py`

- [ ] **步骤 1：扩展 prompt 测试**

在 `test_build_draft_prompt_requires_template_first_and_limits_changes` 中追加断言：

```python
self.assertIn("导师研究方向", prompt)
self.assertIn("Information Extraction", prompt)
self.assertIn("围绕导师研究方向", prompt)
self.assertIn("轻微", prompt)
self.assertIn("保留可表达的富文本标记", prompt)
self.assertIn("加粗", prompt)
self.assertIn("链接", prompt)
```

同时新增系统 prompt 测试：

```python
def test_system_draft_prompt_requires_research_direction_and_format_preservation(self) -> None:
    from app.services.llm_runtime import SYSTEM_DRAFT_PROMPT

    self.assertIn("导师研究方向", SYSTEM_DRAFT_PROMPT)
    self.assertIn("轻微", SYSTEM_DRAFT_PROMPT)
    self.assertIn("不要从零重写", SYSTEM_DRAFT_PROMPT)
    self.assertIn("保留", SYSTEM_DRAFT_PROMPT)
    self.assertIn("加粗", SYSTEM_DRAFT_PROMPT)
```

- [ ] **步骤 2：运行 prompt 测试验证失败**

运行：

```bash
cd backend
uv run python -m unittest test.test_llm_runtime.LLMRuntimeTests.test_build_draft_prompt_requires_template_first_and_limits_changes test.test_llm_runtime.LLMRuntimeTests.test_system_draft_prompt_requires_research_direction_and_format_preservation
```

预期：至少新增断言失败。

- [ ] **步骤 3：更新 `SYSTEM_DRAFT_PROMPT`**

在 `SYSTEM_DRAFT_PROMPT` 的额外要求中加入这些要求，保留现有 JSON schema 约束：

```text
- 必须围绕导师研究方向进行个性化改写，不能只写泛泛的“我关注您的研究”。
- 只做轻微修改，不要从零重写整封邮件。
- 尽量保留模板中可表达的富文本标记，例如加粗、斜体、链接和列表。
- 如果模板包含表格，尽量保留其中的信息顺序和语义，但仍按允许的 rich_body 结构输出。
```

- [ ] **步骤 4：更新 `build_draft_prompt()` 任务要求**

在 `build_draft_prompt()` 的 `任务要求` 中加入：

```text
8. 必须围绕导师研究方向进行个性化改写，研究方向来自“导师信息 - 研究方向”。
9. 尽量保留模板中可表达的富文本标记，例如加粗、斜体、链接和列表。
10. 如果模板包含表格，保留表格中的信息顺序和语义，但不要输出 schema 不支持的表格节点。
```

- [ ] **步骤 5：运行 prompt 测试验证通过**

运行：

```bash
cd backend
uv run python -m unittest test.test_llm_runtime.LLMRuntimeTests.test_build_draft_prompt_requires_template_first_and_limits_changes test.test_llm_runtime.LLMRuntimeTests.test_system_draft_prompt_requires_research_direction_and_format_preservation
```

预期：PASS。

- [ ] **步骤 6：Commit**

运行：

```bash
git add backend/app/services/llm_runtime.py backend/test/test_llm_runtime.py
git commit -m "feat(backend): 强化 AI 草稿研究方向改写提示"
```

## 任务 5：前端禁用规则与提示

**文件：**
- 修改：`frontend/src/pages/WorkspacePage.tsx`
- 修改：`frontend/src/components/organisms/WorkspaceComposerDock.tsx`

- [ ] **步骤 1：在页面层增加研究方向判断**

在 `WorkspacePage.tsx` 中已有 `hasProfessorMatchEvidence`。新增更严格函数：

```ts
const hasProfessorResearchDirection = (professor: WorkspaceProfessorDTO | null | undefined) =>
  Boolean(professor?.research_direction?.trim());
```

修改 `canGenerateDraft`：

```ts
const canGenerateDraft =
  Boolean(currentTaskId) &&
  !blocksDirectDraftActions &&
  (currentTaskMode === 'template'
    ? hasTemplateConfigured
    : hasTemplateConfigured &&
      Boolean(currentTask?.primary_material_id) &&
      hasProfessorResearchDirection(thread?.professor));
```

保留 `canCalculateMatch` 现有逻辑，不把匹配分析同步改成只看研究方向。

- [ ] **步骤 2：在组件层增加限制提示**

在 `WorkspaceComposerDock.tsx` 中新增：

```ts
const hasProfessorResearchDirection = Boolean(thread.professor.research_direction?.trim());
```

把 `limitationHint` 的 LLM 分支调整为：

```ts
const limitationHint =
  currentTaskMode === 'template'
    ? hasTemplateConfigured
      ? null
      : '请先在身份页补充模板。'
    : !hasTemplateConfigured
      ? '请先在身份页补充套磁信模板。'
      : !currentTask.primary_material_id
        ? '请选择用于匹配的材料。'
        : !hasProfessorResearchDirection
          ? '请先补充导师研究方向，再使用 AI 生成草稿。'
          : null;
```

- [ ] **步骤 3：运行前端 lint**

运行：

```bash
cd frontend
npm run lint
```

预期：PASS。

- [ ] **步骤 4：Commit**

运行：

```bash
git add frontend/src/pages/WorkspacePage.tsx frontend/src/components/organisms/WorkspaceComposerDock.tsx
git commit -m "feat(frontend): 按研究方向控制 AI 草稿入口"
```

## 任务 6：前端 token 消耗显示

**文件：**
- 修改：`frontend/src/components/organisms/WorkspaceComposerDock.tsx`

- [ ] **步骤 1：添加 token 格式化 helper**

在 `formatScheduleSummary` 后新增：

```ts
const formatTokenCount = (value: number | null | undefined) =>
  typeof value === 'number' && Number.isFinite(value) ? value.toLocaleString('zh-CN') : '未知';

const buildDraftTokenSummary = (
  task: WorkspaceTaskSummaryDTO,
  mode: OutreachGenerationMode,
) => {
  if (mode === 'template') {
    return '模板模式不消耗 token';
  }

  if (
    task.last_draft_prompt_tokens != null ||
    task.last_draft_completion_tokens != null ||
    task.last_draft_total_tokens != null
  ) {
    return `上次消耗：输入 ${formatTokenCount(task.last_draft_prompt_tokens)} / 输出 ${formatTokenCount(task.last_draft_completion_tokens)} / 总计 ${formatTokenCount(task.last_draft_total_tokens)}`;
  }

  if (
    task.estimated_prompt_tokens != null ||
    task.estimated_completion_tokens_upper_bound != null ||
    task.estimated_total_tokens_upper_bound != null
  ) {
    return `预计上限：输入 ${formatTokenCount(task.estimated_prompt_tokens)} / 输出最多 ${formatTokenCount(task.estimated_completion_tokens_upper_bound)} / 总计最多 ${formatTokenCount(task.estimated_total_tokens_upper_bound)}`;
  }

  return '暂无 token 记录';
};
```

- [ ] **步骤 2：在组件中计算并展示**

在组件 body 中增加：

```ts
const draftTokenSummary = buildDraftTokenSummary(currentTask, currentTaskMode);
```

在「生成草稿」的 `ComposerSection` 内，按钮组下方增加：

```tsx
<div className="rounded-2xl border border-stone-100 bg-stone-50/70 px-3 py-2 text-xs leading-5 text-stone-500">
  {draftTokenSummary}
</div>
```

位置应在两个按钮下面，属于生成草稿区域，不放到发送动作区域。

- [ ] **步骤 3：运行前端 lint**

运行：

```bash
cd frontend
npm run lint
```

预期：PASS。

- [ ] **步骤 4：Commit**

运行：

```bash
git add frontend/src/components/organisms/WorkspaceComposerDock.tsx
git commit -m "feat(frontend): 显示 AI 草稿 token 用量"
```

## 任务 7：综合验证

**文件：**
- 不新增文件。

- [ ] **步骤 1：运行后端相关测试**

运行：

```bash
cd backend
uv run python -m unittest test.test_llm_runtime test.test_api_endpoints.ApiEndpointTests.test_generate_draft_requires_professor_research_direction test.test_api_endpoints.ApiEndpointTests.test_template_draft_does_not_require_professor_research_direction
```

预期：全部 PASS。

- [ ] **步骤 2：运行前端 lint**

运行：

```bash
cd frontend
npm run lint
```

预期：PASS。

- [ ] **步骤 3：检查工作区状态**

运行：

```bash
git status --short
```

预期：没有未提交变更。

## 自检

- 规格覆盖度：已覆盖后端硬约束、模板豁免、prompt 个性化、格式保留边界、token 记录和前端显示。
- 占位符扫描：没有未完成标记、模糊任务或未定义步骤。
- 类型一致性：前端使用现有 `WorkspaceTaskSummaryDTO` token 字段；后端使用现有 `Professor.research_direction`、`EmailLog.provider_payload.usage`。
- 范围控制：不扩展表格 schema，不新增数据库迁移，不改发送链路。
