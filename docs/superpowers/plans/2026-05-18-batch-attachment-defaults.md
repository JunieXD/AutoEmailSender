# 批处理随信附件默认值实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 移除 AI 附件建议能力，让批处理创建时选择的随信附件成为整批固定默认值，并允许单封审核时独立覆盖。

**架构：** 附件选择只由用户输入驱动，批处理创建时将选择写入 `BatchTask` 和每个 `EmailTask`。草稿生成仅更新主题和正文，不读取或写入 LLM 附件建议字段；审核流程继续只更新当前 `EmailTask`。

**技术栈：** FastAPI、SQLAlchemy AsyncSession、Pydantic、Python unittest、Vite、React、Vitest。

---

## 文件结构

- 修改：`backend/app/services/llm_runtime.py`
  - 移除 `DraftGenerationResult.suggested_material_ids` 字段。
  - 清理草稿生成 prompt、示例 JSON、结果归一化和字段校验。
- 修改：`backend/app/services/task_runtime.py`
  - 生成草稿后不再读取 LLM 附件建议，不再覆盖 `EmailTask.selected_material_ids`。
  - `provider_payload` 不再写入 `suggested_material_ids`。
- 修改：`backend/app/services/test_compose_runtime.py`
  - 测试写信生成草稿不再同步 LLM 附件建议。
- 修改：`backend/app/schemas/email_task.py`
  - 移除对 `suggested_material_ids` 的公开 schema 定义或导出。
- 修改：`backend/test/test_llm_runtime.py`
  - 调整 LLM 结果解析测试，确认 schema 不再依赖附件建议字段。
- 修改：`backend/test/test_llm_rich_draft.py`
  - 移除示例中的 `suggested_material_ids`，保持富文本草稿解析覆盖。
- 修改：`backend/test/test_batch_draft_generation_runtime.py`
  - 增加批处理附件固定默认值测试，调整 `DraftGenerationResult` 构造。
- 修改：`frontend/src/types/index.ts`
  - 如存在前端 `suggested_material_ids` 类型引用则删除。
- 修改：`frontend/src/pages/CreateTaskPage.test.tsx`
  - 补充新建批处理默认附件为空、提交选择附件的测试。
- 修改：`frontend/src/pages/TasksPage.test.tsx`
  - 保留或补充审核弹窗从当前任务附件初始化的测试。

---

### 任务 1：移除 LLM 结果 schema 中的附件建议

**文件：**
- 修改：`backend/app/services/llm_runtime.py`
- 修改：`backend/test/test_llm_runtime.py`
- 修改：`backend/test/test_llm_rich_draft.py`

- [ ] **步骤 1：编写失败的 LLM schema 测试**

在 `backend/test/test_llm_runtime.py` 中找到 `DraftGenerationResult` 相关测试，增加或调整断言，确认结果对象不再有 `suggested_material_ids` 属性：

```python
from app.services.llm_runtime import DraftGenerationResult


def test_draft_generation_result_has_no_suggested_material_ids_field(self) -> None:
    result = DraftGenerationResult.model_validate(
        {
            "subject": "申请交流",
            "body_text": "老师您好。",
            "body_html": "<p>老师您好。</p>",
        }
    )

    self.assertFalse(hasattr(result, "suggested_material_ids"))
    self.assertNotIn("suggested_material_ids", result.model_dump())
```

如果该测试文件使用普通函数而不是 `unittest.TestCase` 方法，则写成：

```python
def test_draft_generation_result_has_no_suggested_material_ids_field() -> None:
    result = DraftGenerationResult.model_validate(
        {
            "subject": "申请交流",
            "body_text": "老师您好。",
            "body_html": "<p>老师您好。</p>",
        }
    )

    assert not hasattr(result, "suggested_material_ids")
    assert "suggested_material_ids" not in result.model_dump()
```

- [ ] **步骤 2：运行测试验证失败**

运行：

```powershell
cd backend
uv run python -m unittest test.test_llm_runtime test.test_llm_rich_draft
```

预期：至少 1 个测试失败，失败原因是 `DraftGenerationResult` 仍包含 `suggested_material_ids`，或现有测试仍引用该字段。

- [ ] **步骤 3：删除 schema 字段和归一化逻辑**

在 `backend/app/services/llm_runtime.py` 中：

1. 删除 `DraftGenerationResult` 和相关草稿结果模型里的字段：

```python
suggested_material_ids: list[int] = Field(default_factory=list)
```

2. 删除结果后处理中的归一化代码：

```python
result.suggested_material_ids = _normalize_integer_list(result.suggested_material_ids)
```

3. 删除所有生成结果构造中的 `suggested_material_ids=[...]` 参数。

4. 如果存在将 rewrite 结果附件建议复制到最终结果的代码，删除类似逻辑：

```python
suggested_material_ids=[
    material_id
    for material_id in rewrite_result.suggested_material_ids
    if material_id in available_material_ids
]
```

- [ ] **步骤 4：清理 prompt 和示例 JSON**

在 `backend/app/services/llm_runtime.py` 中搜索 `suggested_material_ids`，删除所有面向模型的要求和示例字段，包括类似内容：

```text
- suggested_material_ids: 整数数组，只能从输入给出的可选材料 ID 中选择
```

```json
{
  "suggested_material_ids": [12]
}
```

保留 `available_materials` 作为上下文材料信息，前提是它仍用于正文生成；不要要求模型输出附件 ID。

- [ ] **步骤 5：更新富文本草稿测试样例**

在 `backend/test/test_llm_rich_draft.py` 中，把测试 JSON 从：

```json
{
  "subject": "申请交流",
  "body": {
    "type": "doc",
    "content": []
  },
  "suggested_material_ids": [1]
}
```

调整为：

```json
{
  "subject": "申请交流",
  "body": {
    "type": "doc",
    "content": []
  }
}
```

并删除对 `result.suggested_material_ids` 的断言。

- [ ] **步骤 6：运行测试验证通过**

运行：

```powershell
cd backend
uv run python -m unittest test.test_llm_runtime test.test_llm_rich_draft
```

预期：`OK`。

- [ ] **步骤 7：Commit**

```powershell
git add backend/app/services/llm_runtime.py backend/test/test_llm_runtime.py backend/test/test_llm_rich_draft.py
git commit -m "feat(backend): remove llm attachment suggestions"
```

---

### 任务 2：草稿生成不再覆盖任务附件

**文件：**
- 修改：`backend/app/services/task_runtime.py`
- 修改：`backend/app/services/test_compose_runtime.py`
- 修改：`backend/app/schemas/email_task.py`
- 修改：`backend/test/test_batch_draft_generation_runtime.py`

- [ ] **步骤 1：编写批处理附件保持不变的失败测试**

在 `backend/test/test_batch_draft_generation_runtime.py` 中，复用现有测试夹具和 `_create_batch_with_tasks`。新增测试：

```python
    def test_batch_draft_generation_keeps_batch_selected_materials(self) -> None:
        async def scenario() -> list[int] | None:
            task_ids = await self._create_batch_with_tasks(
                [EmailTaskStatus.DISCOVERED.value],
                selected_material_ids=[101, 102],
            )
            async with self.session_factory() as session:
                task = await session.get(EmailTask, task_ids[0])
                self.assertIsNotNone(task)
                task.selected_material_ids = [101, 102]
                await session.commit()

            with patch(
                "app.services.llm_runtime.generate_draft_content",
                new=AsyncMock(
                    return_value=llm_runtime.DraftGenerationResponse(
                        result=llm_runtime.DraftGenerationResult(
                            subject="生成主题",
                            body_text="生成正文",
                            body_html="<p>生成正文</p>",
                        ),
                        usage=llm_runtime.ChatCompletionUsage(
                            prompt_tokens=10,
                            completion_tokens=20,
                            total_tokens=30,
                            cached_tokens=0,
                        ),
                    )
                ),
            ):
                await task_runtime.generate_task_draft(
                    self.session_factory,
                    task_ids[0],
                    force=True,
                    automatic_batch=True,
                )

            async with self.session_factory() as session:
                task = await session.get(EmailTask, task_ids[0])
                self.assertIsNotNone(task)
                return task.selected_material_ids

        self.assertEqual(asyncio.run(scenario()), [101, 102])
```

如果 `_create_batch_with_tasks` 当前不支持 `selected_material_ids` 参数，先在测试 helper 中加入参数：

```python
    async def _create_batch_with_tasks(
        self,
        statuses: list[str],
        *,
        selected_material_ids: list[int] | None = None,
    ) -> list[int]:
```

并在创建 `BatchTask` 和 `EmailTask` 时传入：

```python
selected_material_ids=selected_material_ids,
```

- [ ] **步骤 2：编写未选择附件保持为空的失败测试**

在同一测试文件新增：

```python
    def test_batch_draft_generation_keeps_empty_selected_materials(self) -> None:
        async def scenario() -> list[int] | None:
            task_ids = await self._create_batch_with_tasks(
                [EmailTaskStatus.DISCOVERED.value],
                selected_material_ids=None,
            )

            with patch(
                "app.services.llm_runtime.generate_draft_content",
                new=AsyncMock(
                    return_value=llm_runtime.DraftGenerationResponse(
                        result=llm_runtime.DraftGenerationResult(
                            subject="生成主题",
                            body_text="生成正文",
                            body_html="<p>生成正文</p>",
                        ),
                        usage=llm_runtime.ChatCompletionUsage(
                            prompt_tokens=10,
                            completion_tokens=20,
                            total_tokens=30,
                            cached_tokens=0,
                        ),
                    )
                ),
            ):
                await task_runtime.generate_task_draft(
                    self.session_factory,
                    task_ids[0],
                    force=True,
                    automatic_batch=True,
                )

            async with self.session_factory() as session:
                task = await session.get(EmailTask, task_ids[0])
                self.assertIsNotNone(task)
                return task.selected_material_ids

        self.assertIsNone(asyncio.run(scenario()))
```

- [ ] **步骤 3：运行测试验证失败**

运行：

```powershell
cd backend
uv run python -m unittest test.test_batch_draft_generation_runtime
```

预期：测试因生产代码仍引用 `generation.result.suggested_material_ids` 或测试构造签名不匹配而失败。

- [ ] **步骤 4：修改 `task_runtime.py` 生成草稿逻辑**

在 `backend/app/services/task_runtime.py` 中：

1. 模板模式下删除 `suggested_material_ids` 临时变量赋值：

```python
suggested_material_ids = (
    batch_task.selected_material_ids if batch_task else None
)
```

2. LLM 模式下删除读取 LLM 建议的逻辑：

```python
suggested_material_ids = (
    generation.result.suggested_material_ids
    or (batch_task.selected_material_ids if batch_task else None)
)
```

3. 删除 `provider_payload` 中的字段：

```python
"suggested_material_ids": generation.result.suggested_material_ids,
```

4. 删除生成完成后的覆盖逻辑：

```python
if suggested_material_ids is not None:
    task.selected_material_ids = suggested_material_ids
```

保留日志里的最终任务状态：

```python
"selected_material_ids": task.selected_material_ids,
```

- [ ] **步骤 5：修改 `test_compose_runtime.py`**

在 `backend/app/services/test_compose_runtime.py` 中删除生成草稿后同步附件建议的逻辑：

```python
if generation.result.suggested_material_ids is not None:
    compose_session.selected_material_ids = generation.result.suggested_material_ids
```

测试写信附件只通过发送或保存接口提交的 `payload.selected_material_ids` 更新。

- [ ] **步骤 6：修改 `email_task.py` schema**

在 `backend/app/schemas/email_task.py` 中删除：

```python
suggested_material_ids: list[int] = Field(default_factory=list)
```

如果删除后 `Field` 不再使用，同步清理导入：

```python
from pydantic import BaseModel, Field
```

改为：

```python
from pydantic import BaseModel
```

保留审批请求：

```python
selected_material_ids: list[int] | None = None
```

- [ ] **步骤 7：运行后端相关测试**

运行：

```powershell
cd backend
uv run python -m unittest test.test_batch_draft_generation_runtime test.test_llm_runtime test.test_llm_rich_draft
```

预期：`OK`。

- [ ] **步骤 8：Commit**

```powershell
git add backend/app/services/task_runtime.py backend/app/services/test_compose_runtime.py backend/app/schemas/email_task.py backend/test/test_batch_draft_generation_runtime.py
git commit -m "fix(backend): keep batch attachment defaults during draft generation"
```

---

### 任务 3：清理前端类型并补充附件默认行为测试

**文件：**
- 修改：`frontend/src/types/index.ts`
- 修改：`frontend/src/pages/CreateTaskPage.test.tsx`
- 修改：`frontend/src/pages/TasksPage.test.tsx`

- [ ] **步骤 1：搜索前端附件建议字段**

运行：

```powershell
rg -n "suggested_material_ids|suggestedMaterialIds" frontend/src frontend/test
```

预期：如果有结果，全部来自待清理类型或测试；如果无结果，此步骤无需改代码。

- [ ] **步骤 2：清理前端类型引用**

如果 `frontend/src/types/index.ts` 中存在字段：

```ts
suggested_material_ids: number[];
```

删除该字段。不要删除审批和任务上的字段：

```ts
selected_material_ids: number[] | null;
```

- [ ] **步骤 3：补充创建批处理默认附件为空测试**

在 `frontend/src/pages/CreateTaskPage.test.tsx` 中新增测试，确认未勾选附件时提交 `selected_material_ids: null`：

```tsx
  it("submits null selected materials by default for new batch tasks", async () => {
    render(
      <MemoryRouter>
        <CreateTaskPage />
      </MemoryRouter>,
    );

    await screen.findByText(selectedProfessor.name);
    fireEvent.click(screen.getByRole("button", { name: "创建任务" }));

    await waitFor(() => expect(createBatchTaskMock).toHaveBeenCalledTimes(1));
    expect(createBatchTaskMock).toHaveBeenCalledWith(
      expect.objectContaining({
        selected_material_ids: null,
      }),
    );
  });
```

如果页面提交前有确认弹窗，沿用现有测试里的 `confirmMock.mockResolvedValue(true)` 设置，不新增额外 mock。

- [ ] **步骤 4：补充创建批处理提交勾选附件测试**

在 `frontend/src/pages/CreateTaskPage.test.tsx` 中使用测试身份材料数据，点击附件复选框后提交：

```tsx
  it("submits user selected materials for batch tasks", async () => {
    render(
      <MemoryRouter>
        <CreateTaskPage />
      </MemoryRouter>,
    );

    await screen.findByText("Portfolio.pdf");
    fireEvent.click(screen.getByLabelText("Portfolio.pdf"));
    fireEvent.click(screen.getByRole("button", { name: "创建任务" }));

    await waitFor(() => expect(createBatchTaskMock).toHaveBeenCalledTimes(1));
    expect(createBatchTaskMock).toHaveBeenCalledWith(
      expect.objectContaining({
        selected_material_ids: [7],
      }),
    );
  });
```

如果当前材料名称或 ID 不同，使用测试文件里已有 `selectedIdentity.materials` 中的实际名称和 ID；不要为测试单独引入无关 fixture。

- [ ] **步骤 5：补充或确认审核弹窗初始化测试**

在 `frontend/src/pages/TasksPage.test.tsx` 中查找已有 `selected_material_ids: [7]` fixture。如果已经有测试断言审核弹窗中 ID 7 对应附件被勾选，则无需新增。否则新增测试：

```tsx
  it("initializes batch draft review attachments from current task", async () => {
    render(
      <MemoryRouter>
        <TasksPage />
      </MemoryRouter>,
    );

    await screen.findByText("批量审核草稿");
    expect(screen.getByLabelText("Portfolio.pdf")).toBeChecked();
  });
```

如果现有测试通过按钮打开审核弹窗，按现有交互路径触发，不绕过组件内部状态。

- [ ] **步骤 6：运行前端测试**

运行：

```powershell
cd frontend
npm run test -- CreateTaskPage.test.tsx TasksPage.test.tsx
```

预期：相关测试通过。

- [ ] **步骤 7：Commit**

```powershell
git add frontend/src/types/index.ts frontend/src/pages/CreateTaskPage.test.tsx frontend/src/pages/TasksPage.test.tsx
git commit -m "test(frontend): cover batch attachment defaults"
```

---

### 任务 4：全局清理和回归验证

**文件：**
- 修改：`docs/superpowers/specs/2026-05-18-batch-attachment-defaults-design.md`（仅当实现中发现规格需要同步修正时）
- 检查：全仓 `suggested_material_ids` 引用

- [ ] **步骤 1：全局搜索旧字段**

运行：

```powershell
rg -n "suggested_material_ids|suggestedMaterialIds" backend frontend docs
```

预期：

- `backend/app` 和 `frontend/src` 中没有运行时代码引用。
- `docs/superpowers/specs/2026-05-18-batch-attachment-defaults-design.md` 可以保留历史背景说明。
- 旧设计文档或历史 release 文档如仍提到该字段，不在本任务范围内修改，除非它们描述当前实现。

- [ ] **步骤 2：修复残留运行时代码引用**

如果步骤 1 在运行时代码里发现残留，删除对应字段。例如：

```python
"suggested_material_ids": result.suggested_material_ids,
```

或：

```ts
suggested_material_ids: number[];
```

删除后重新运行步骤 1。

- [ ] **步骤 3：运行后端回归测试**

运行：

```powershell
cd backend
uv run python -m unittest discover test
```

预期：`OK`。如果出现与本改动无关的既有失败，记录失败测试名、错误信息和判定依据，不扩大修复范围。

- [ ] **步骤 4：运行前端验证**

运行：

```powershell
cd frontend
npm run lint
npm run test
```

预期：lint 和测试通过。若出现与本改动无关的既有失败，记录失败项，不扩大修复范围。

- [ ] **步骤 5：检查工作区状态**

运行：

```powershell
git status --short --branch
```

预期：只包含本计划相关变更，且不包含其他未跟踪历史文档，除非用户明确要求一起处理。

- [ ] **步骤 6：最终 Commit**

如果步骤 1 或验证过程中还有清理变更，提交：

```powershell
git add backend frontend docs/superpowers/specs/2026-05-18-batch-attachment-defaults-design.md
git commit -m "chore: verify batch attachment default behavior"
```

如果没有新增变更，不创建空 commit。
