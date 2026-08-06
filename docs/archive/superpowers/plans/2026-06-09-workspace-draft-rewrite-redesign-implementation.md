# 工作区草稿 AI 改写重设计实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 将工作区写信区改为“当前草稿 + AI 改写当前草稿”的交互与后端业务模型，修复空编辑器下点击 AI 生成卡住、模板草稿不可直接发送、改写过程状态不可靠的问题。

**架构：** 后端新增统一 `draft` 视图作为工作区编辑器唯一数据源，并新增 `/api/email-tasks/{task_id}/rewrite-draft` 以点击瞬间的编辑器内容作为 LLM 改写输入。任务进入改写中之前先持久化源草稿和开始时间，成功写入 `generated_*`，失败、超时或启动恢复时把源草稿恢复为可编辑保存草稿。前端消费 `draft` 视图，按钮语义改为“AI 改写”，生成中锁定编辑器和保存/发送/定时/模式切换/附件选择，但允许离开页面。

**技术栈：** FastAPI、SQLAlchemy async、Alembic、unittest、Vite、React、TypeScript、Vitest、React Testing Library。

---

## 文件结构

- 修改：`backend/app/models/email_task.py`
  - 新增工作区改写源草稿字段和 `draft_generation_started_at` ORM 映射。
- 创建：`backend/alembic/versions/20260609_add_workspace_draft_rewrite_fields.py`
  - 给 `email_tasks` 增加改写源草稿字段。
- 修改：`backend/app/schemas/workspace.py`
  - 新增 `WorkspaceDraftRead`，并在 `WorkspaceTaskSummaryRead` 上返回 `draft`。
- 修改：`backend/app/schemas/email_task.py`
  - 新增 `EmailTaskRewriteDraftRequest`，复用保存草稿 payload 的主体结构并携带运行模型。
- 修改：`backend/app/api/workspace_support.py`
  - 增加当前草稿选择器，统一生成 `current_task.draft`。
- 修改：`backend/app/api/email_tasks.py`
  - 新增 `/rewrite-draft` 入口，并继续保留旧 `/generate-draft`。
- 修改：`backend/app/services/task_runtime.py`
  - 新增 `rewrite_task_draft()`，抽取源草稿保存、失败恢复和成功提交逻辑；生成中保存、发送、定时返回新文案。
- 修改：`backend/app/services/batch_draft_generation_runtime.py`
  - 把工作区 5 分钟超时恢复纳入集中常量和启动恢复路径，同时不改变批量 worker 的 30 分钟默认行为。
- 修改：`backend/main.py`
  - 启动清理调用工作区 5 分钟恢复，周期 worker 复用同一恢复函数。
- 修改：`backend/test/schema_database.py`
  - 测试数据库建表 SQL 增加新字段。
- 修改：`backend/test/test_database_schema.py`
  - 覆盖新字段存在。
- 修改：`backend/test/test_workspace_support.py`
  - 覆盖 `draft` 视图优先级和生成中源草稿展示。
- 修改：`backend/test/test_api_endpoints.py`
  - 覆盖 `/rewrite-draft` 成功、空正文拒绝、源草稿输入、生成中动作拒绝。
- 修改：`backend/test/test_batch_draft_generation_runtime.py`
  - 覆盖 5 分钟工作区恢复、未超时不恢复、批量旧恢复不回归。
- 修改：`backend/test/test_startup_runtime.py`
  - 覆盖启动恢复使用工作区 5 分钟规则和中断文案。
- 修改：`frontend/src/types/index.ts`
  - 新增 `WorkspaceDraftDTO` 和 `EmailTaskRewriteDraftPayloadDTO`，给 `WorkspaceTaskSummaryDTO` 增加 `draft`。
- 修改：`frontend/src/lib/api/emailTasksApi.ts`
  - 新增 `rewriteDraft()` 调用 `/rewrite-draft`。
- 修改：`frontend/src/pages/WorkspacePage.tsx`
  - 用 `current_task.draft` 初始化编辑器；改写请求发送当前编辑器内容；生成中不触发脏草稿离开保护。
- 修改：`frontend/src/components/organisms/WorkspaceComposerDock.tsx`
  - 文案改为“AI 改写”，生成中状态和禁用逻辑显式化。
- 修改：`frontend/src/pages/WorkspacePage.test.tsx`
  - 覆盖当前草稿初始化、改写 payload、空草稿禁用、生成中锁定、成功/失败后恢复。
- 修改：`frontend/src/components/organisms/WorkspaceSidebar.test.tsx`
  - 补齐 `draft` 字段测试夹具，保持现有侧边栏测试稳定。
- 修改：`frontend/src/features/workspace/client/openWorkspaceThread.test.tsx`
  - 补齐 `draft` 字段测试夹具，保持 bootstrap 逻辑稳定。

---

## 任务 1：数据库字段与 schema 基线

**文件：**
- 修改：`backend/app/models/email_task.py`
- 创建：`backend/alembic/versions/20260609_add_workspace_draft_rewrite_fields.py`
- 修改：`backend/test/schema_database.py`
- 修改：`backend/test/test_database_schema.py`

- [ ] **步骤 1：编写失败的数据库字段测试**

在 `backend/test/test_database_schema.py` 的 email task 字段测试附近加入断言：

```python
def test_email_tasks_contains_workspace_rewrite_fields(self) -> None:
    connection = sqlite3.connect(self.db_path)
    try:
        columns = {
            row[1]
            for row in connection.execute("PRAGMA table_info(email_tasks)").fetchall()
        }
    finally:
        connection.close()

    self.assertIn("draft_generation_started_at", columns)
    self.assertIn("draft_rewrite_source_subject", columns)
    self.assertIn("draft_rewrite_source_body_text", columns)
    self.assertIn("draft_rewrite_source_body_html", columns)
    self.assertIn("draft_rewrite_source_selected_material_ids", columns)
```

- [ ] **步骤 2：运行测试验证失败**

运行：`cd backend && uv run python -m unittest test.test_database_schema.DatabaseSchemaTests.test_email_tasks_contains_workspace_rewrite_fields`

预期：FAIL，断言至少提示 `draft_generation_started_at` 不在 `columns` 中。

- [ ] **步骤 3：实现 ORM、迁移和测试建表 SQL**

在 `backend/app/models/email_task.py` 的 `draft_generation_previous_status` 后增加：

```python
draft_generation_started_at: Mapped[datetime | None] = mapped_column(
    UTCDateTime(),
    nullable=True,
)
draft_rewrite_source_subject: Mapped[str | None] = mapped_column(Text, nullable=True)
draft_rewrite_source_body_text: Mapped[str | None] = mapped_column(Text, nullable=True)
draft_rewrite_source_body_html: Mapped[str | None] = mapped_column(Text, nullable=True)
draft_rewrite_source_selected_material_ids: Mapped[list[int] | None] = mapped_column(JSON, nullable=True)
```

创建迁移 `backend/alembic/versions/20260609_add_workspace_draft_rewrite_fields.py`：

```python
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260609_workspace_rewrite_fields"
down_revision = "9a7c5e3d2b1f"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("email_tasks", sa.Column("draft_generation_started_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("email_tasks", sa.Column("draft_rewrite_source_subject", sa.Text(), nullable=True))
    op.add_column("email_tasks", sa.Column("draft_rewrite_source_body_text", sa.Text(), nullable=True))
    op.add_column("email_tasks", sa.Column("draft_rewrite_source_body_html", sa.Text(), nullable=True))
    op.add_column("email_tasks", sa.Column("draft_rewrite_source_selected_material_ids", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("email_tasks", "draft_rewrite_source_selected_material_ids")
    op.drop_column("email_tasks", "draft_rewrite_source_body_html")
    op.drop_column("email_tasks", "draft_rewrite_source_body_text")
    op.drop_column("email_tasks", "draft_rewrite_source_subject")
    op.drop_column("email_tasks", "draft_generation_started_at")
```

如果当前 Alembic head 不是 `9a7c5e3d2b1f`，先运行 `cd backend && uv run python - <<'PY'\nfrom app.core.migrations import get_head_revision\nprint(get_head_revision())\nPY`，把 `down_revision` 改为实际 head。

在 `backend/test/schema_database.py` 的 `email_tasks` 建表 SQL 中添加同名字段，类型使用现有 SQLite 风格：

```sql
draft_generation_started_at DATETIME,
draft_rewrite_source_subject TEXT,
draft_rewrite_source_body_text TEXT,
draft_rewrite_source_body_html TEXT,
draft_rewrite_source_selected_material_ids JSON,
```

- [ ] **步骤 4：运行测试验证通过**

运行：`cd backend && uv run python -m unittest test.test_database_schema.DatabaseSchemaTests.test_email_tasks_contains_workspace_rewrite_fields`

预期：PASS。

- [ ] **步骤 5：Commit**

```bash
git add backend/app/models/email_task.py backend/alembic/versions/20260609_add_workspace_draft_rewrite_fields.py backend/test/schema_database.py backend/test/test_database_schema.py
git commit -m "feat(backend): add workspace rewrite draft fields"
```

---

## 任务 2：后端工作区 `draft` 视图

**文件：**
- 修改：`backend/app/schemas/workspace.py`
- 修改：`backend/app/api/workspace_support.py`
- 修改：`backend/test/test_workspace_support.py`

- [ ] **步骤 1：编写失败的草稿视图测试**

在 `backend/test/test_workspace_support.py` 增加 5 个行为测试：

```python
def test_workspace_draft_uses_rendered_template_without_history(self) -> None:
    thread = self._run_async(self._build_thread_with_task(
        outreach_template_subject="申请加入{{name}}老师课题组",
        outreach_template_body_text="老师您好，我是{{sender_name}}。",
    ))
    self.assertEqual(thread.current_task.draft.source, "template")
    self.assertIn("老师您好", thread.current_task.draft.body_text)
    self.assertTrue(thread.current_task.draft.sendable)
    self.assertTrue(thread.current_task.draft.editable)

def test_workspace_draft_uses_saved_draft_before_generated_result(self) -> None:
    thread = self._run_async(self._build_thread_with_task(
        generated_subject="AI 主题",
        generated_content_text="AI 正文",
        generated_content_html="<p>AI 正文</p>",
        approved_subject="保存主题",
        approved_body_text="保存正文",
        approved_body_html="<p>保存正文</p>",
    ))
    self.assertEqual(thread.current_task.draft.source, "saved")
    self.assertEqual(thread.current_task.draft.subject, "保存主题")
    self.assertEqual(thread.current_task.draft.body_text, "保存正文")

def test_workspace_draft_uses_ai_rewrite_when_no_saved_draft(self) -> None:
    thread = self._run_async(self._build_thread_with_task(
        generated_subject="AI 主题",
        generated_content_text="AI 正文",
        generated_content_html="<p>AI 正文</p>",
    ))
    self.assertEqual(thread.current_task.draft.source, "ai_rewrite")
    self.assertEqual(thread.current_task.draft.body_text, "AI 正文")

def test_workspace_draft_uses_rewrite_source_while_generating(self) -> None:
    thread = self._run_async(self._build_thread_with_task(
        status=EmailTaskStatus.GENERATING_DRAFT.value,
        draft_rewrite_source_subject="源主题",
        draft_rewrite_source_body_text="源正文",
        draft_rewrite_source_body_html="<p>源正文</p>",
    ))
    self.assertEqual(thread.current_task.draft.source, "rewrite_source")
    self.assertEqual(thread.current_task.draft.body_text, "源正文")
    self.assertFalse(thread.current_task.draft.editable)
    self.assertFalse(thread.current_task.draft.sendable)

def test_workspace_draft_is_empty_without_template_or_history(self) -> None:
    thread = self._run_async(self._build_thread_with_task(
        outreach_template_subject=None,
        outreach_template_body_text=None,
        outreach_template_body_html=None,
    ))
    self.assertEqual(thread.current_task.draft.source, "manual_empty")
    self.assertEqual(thread.current_task.draft.body_text, "")
    self.assertFalse(thread.current_task.draft.sendable)
```

如果当前测试文件没有 `_build_thread_with_task` helper，就新增一个 helper，使用现有 fixture 创建 identity、professor、llm_profile、task 后调用 `build_workspace_thread_for_task()`。

- [ ] **步骤 2：运行测试验证失败**

运行：`cd backend && uv run python -m unittest test.test_workspace_support`

预期：FAIL，错误为 `WorkspaceTaskSummaryRead` 没有 `draft` 字段或访问 `thread.current_task.draft` 失败。

- [ ] **步骤 3：新增 schema 和草稿选择器**

在 `backend/app/schemas/workspace.py` 增加：

```python
class WorkspaceDraftRead(ApiSchema):
    subject: str | None
    body_text: str
    body_html: str | None
    source: str
    sendable: bool
    editable: bool
```

并在 `WorkspaceTaskSummaryRead` 中增加：

```python
draft: WorkspaceDraftRead
```

在 `backend/app/api/workspace_support.py` 增加 helper：

```python
def _has_meaningful_body(body_text: str | None, body_html: str | None) -> bool:
    return bool((body_text or "").strip() or (body_html or "").strip())


def _task_blocks_draft_actions(task: EmailTask | None) -> bool:
    if task is None:
        return True
    return bool(
        task.status in {
            EmailTaskStatus.GENERATING_DRAFT.value,
            EmailTaskStatus.SENDING.value,
            EmailTaskStatus.SENT.value,
            EmailTaskStatus.REPLY_DETECTED.value,
            EmailTaskStatus.CANCELED.value,
        }
        or task.sent_at
        or task.is_replied
        or _can_continue_manually(task)
        or _can_write_follow_up(task)
    )


def _build_workspace_draft(
    *,
    task: EmailTask | None,
    rendered_template: RenderedOutreachTemplate | None,
) -> WorkspaceDraftRead:
    if task is None:
        return WorkspaceDraftRead(
            subject=rendered_template.subject if rendered_template else None,
            body_text=rendered_template.body_text if rendered_template else "",
            body_html=rendered_template.body_html if rendered_template else None,
            source="template" if rendered_template and _has_meaningful_body(rendered_template.body_text, rendered_template.body_html) else "manual_empty",
            sendable=bool(rendered_template and _has_meaningful_body(rendered_template.body_text, rendered_template.body_html)),
            editable=True,
        )
    if task.status == EmailTaskStatus.GENERATING_DRAFT.value:
        return WorkspaceDraftRead(
            subject=task.draft_rewrite_source_subject or task.approved_subject or task.generated_subject or rendered_template.subject if rendered_template else None,
            body_text=task.draft_rewrite_source_body_text or task.approved_body_text or task.generated_content_text or "",
            body_html=task.draft_rewrite_source_body_html or task.approved_body_html or task.generated_content_html,
            source="rewrite_source",
            sendable=False,
            editable=False,
        )
    if _has_meaningful_body(task.approved_body_text, task.approved_body_html):
        source = "saved"
        subject = task.approved_subject
        body_text = task.approved_body_text or ""
        body_html = task.approved_body_html
    elif _has_meaningful_body(task.generated_content_text, task.generated_content_html):
        source = "ai_rewrite"
        subject = task.generated_subject
        body_text = task.generated_content_text or ""
        body_html = task.generated_content_html
    elif rendered_template and _has_meaningful_body(rendered_template.body_text, rendered_template.body_html):
        source = "template"
        subject = rendered_template.subject
        body_text = rendered_template.body_text
        body_html = rendered_template.body_html
    else:
        source = "manual_empty"
        subject = None
        body_text = ""
        body_html = None
    return WorkspaceDraftRead(
        subject=subject,
        body_text=body_text,
        body_html=body_html,
        source=source,
        sendable=_has_meaningful_body(body_text, body_html) and not _task_blocks_draft_actions(task),
        editable=not _task_blocks_draft_actions(task),
    )
```

实现时把三元表达式拆成局部变量，避免 `or ... if ... else ...` 优先级误读。随后在 `WorkspaceTaskSummaryRead(...)` 构造参数中传入：

```python
draft=_build_workspace_draft(task=current_task, rendered_template=rendered_template),
```

- [ ] **步骤 4：运行测试验证通过**

运行：`cd backend && uv run python -m unittest test.test_workspace_support`

预期：PASS。

- [ ] **步骤 5：Commit**

```bash
git add backend/app/schemas/workspace.py backend/app/api/workspace_support.py backend/test/test_workspace_support.py
git commit -m "feat(backend): expose workspace current draft view"
```

---

## 任务 3：工作区 AI 改写接口和运行时

**文件：**
- 修改：`backend/app/schemas/email_task.py`
- 修改：`backend/app/api/email_tasks.py`
- 修改：`backend/app/services/task_runtime.py`
- 修改：`backend/test/test_api_endpoints.py`

- [ ] **步骤 1：编写失败的接口测试**

在 `backend/test/test_api_endpoints.py` 增加测试：

```python
def test_rewrite_draft_uses_request_body_as_llm_input(self) -> None:
    task_id = self._create_workspace_task_with_template(
        template_body="身份默认模板正文",
        primary_material_text="CV text",
        professor_research_direction="AI systems",
    )

    async def fake_generate(**kwargs):
        self.assertEqual(kwargs["custom_subject"], "用户改过主题")
        self.assertEqual(kwargs["custom_body"], "用户改过正文")
        self.assertEqual(kwargs["custom_body_html"], "<p>用户改过正文</p>")
        return self._build_draft_generation_result(
            subject="AI 改写主题",
            body_text="AI 改写正文",
            body_html="<p>AI 改写正文</p>",
        )

    with patch(
        "app.services.task_runtime.llm_runtime.generate_draft_content",
        new=AsyncMock(side_effect=fake_generate),
    ):
        response = self.client.post(
            f"/api/email-tasks/{task_id}/rewrite-draft",
            json={
                "subject": "用户改过主题",
                "body_text": "用户改过正文",
                "body_html": "<p>用户改过正文</p>",
                "selected_material_ids": [],
                "llm_profile_id": None,
            },
        )

    self.assertEqual(response.status_code, 200)
    current_task = response.json()["current_task"]
    self.assertEqual(current_task["draft"]["source"], "ai_rewrite")
    self.assertEqual(current_task["draft"]["body_text"], "AI 改写正文")
```

再增加空正文测试：

```python
def test_rewrite_draft_rejects_empty_body_without_calling_llm(self) -> None:
    task_id = self._create_workspace_task_with_template(
        template_body="身份默认模板正文",
        primary_material_text="CV text",
        professor_research_direction="AI systems",
    )
    with patch(
        "app.services.task_runtime.llm_runtime.generate_draft_content",
        new=AsyncMock(side_effect=AssertionError("空正文不能调用 LLM")),
    ):
        response = self.client.post(
            f"/api/email-tasks/{task_id}/rewrite-draft",
            json={
                "subject": "主题",
                "body_text": "",
                "body_html": "",
                "selected_material_ids": [],
                "llm_profile_id": None,
            },
        )

    self.assertEqual(response.status_code, 400)
    self.assertEqual(response.json()["detail"], "先写入正文或配置默认模板后再使用 AI 改写")
```

再增加源草稿落库测试：

```python
def test_rewrite_draft_persists_source_before_llm_returns(self) -> None:
    task_id = self._create_workspace_task_with_template(
        template_body="身份默认模板正文",
        primary_material_text="CV text",
        professor_research_direction="AI systems",
    )

    async def fake_generate(**kwargs):
        async with self.session_factory() as session:
            task = await session.get(EmailTask, task_id)
            assert task is not None
            self.assertEqual(task.status, EmailTaskStatus.GENERATING_DRAFT.value)
            self.assertIsNotNone(task.draft_generation_started_at)
            self.assertEqual(task.draft_rewrite_source_body_text, "点击瞬间正文")
        return self._build_draft_generation_result(
            subject="AI 主题",
            body_text="AI 正文",
            body_html="<p>AI 正文</p>",
        )
```

- [ ] **步骤 2：运行测试验证失败**

运行：`cd backend && uv run python -m unittest test.test_api_endpoints.ApiEndpointTests.test_rewrite_draft_uses_request_body_as_llm_input test.test_api_endpoints.ApiEndpointTests.test_rewrite_draft_rejects_empty_body_without_calling_llm test.test_api_endpoints.ApiEndpointTests.test_rewrite_draft_persists_source_before_llm_returns`

预期：FAIL，`/rewrite-draft` 返回 404 或 schema 不存在。

- [ ] **步骤 3：实现请求 schema 和 API 入口**

在 `backend/app/schemas/email_task.py` 增加：

```python
class EmailTaskRewriteDraftRequest(ApiSchema):
    subject: str | None = None
    body_text: str = ""
    body_html: str | None = None
    selected_material_ids: list[int] | None = None
    llm_profile_id: int | None = None
```

在 `backend/app/api/email_tasks.py` import `EmailTaskRewriteDraftRequest` 和 `rewrite_task_draft`，并新增：

```python
@router.post("/{task_id}/rewrite-draft", response_model=WorkspaceThreadRead)
async def rewrite_draft(
    task_id: int,
    payload: EmailTaskRewriteDraftRequest,
    session: AsyncSession = Depends(get_async_session),
) -> WorkspaceThreadRead:
    return await _run_workspace_action(
        session,
        lambda: rewrite_task_draft(get_session_factory(), task_id, payload),
    )
```

- [ ] **步骤 4：实现 runtime 改写流程**

在 `backend/app/services/task_runtime.py` 增加常量：

```python
WORKSPACE_DRAFT_REWRITE_TIMEOUT = timedelta(minutes=5)
WORKSPACE_DRAFT_REWRITE_TIMEOUT_SECONDS = int(WORKSPACE_DRAFT_REWRITE_TIMEOUT.total_seconds())
WORKSPACE_DRAFT_REWRITE_TIMEOUT_MESSAGE = "AI 改写超时，请稍后重试"
WORKSPACE_DRAFT_REWRITE_INTERRUPTED_MESSAGE = "AI 改写已中断，请重试"
```

实现 `rewrite_task_draft()`：

```python
async def rewrite_task_draft(
    session_factory: async_sessionmaker[AsyncSession],
    task_id: int,
    payload: EmailTaskRewriteDraftRequest,
) -> tuple[int, int, int]:
    source_body_text = payload.body_text.strip()
    source_body_html = (payload.body_html or "").strip()
    if not source_body_text and not source_body_html:
        raise ValueError("先写入正文或配置默认模板后再使用 AI 改写")

    async with session_factory() as session:
        task = await _load_email_task(session, task_id)
        if not task:
            raise ValueError(f"EmailTask {task_id} 不存在")
        _ensure_task_allows_legacy_manual_actions(task)
        if task.status == EmailTaskStatus.GENERATING_DRAFT.value:
            raise ValueError("AI 正在改写当前草稿，请稍后刷新")
        if task.primary_material is None:
            raise ValueError("请选择用于匹配的材料后再使用 AI 改写")
        if not _has_professor_research_direction(task.professor):
            raise ValueError("请先补充导师研究方向，再使用 AI 改写")
        await _validate_selected_material_ids(session, task.identity_id, payload.selected_material_ids)
        ensure_material_extracted_text(task.primary_material)
        runtime_llm_profile = await _resolve_runtime_llm_profile(session, task, payload.llm_profile_id)
        runtime_settings = await get_runtime_settings(session)
        now = utc_now()
        previous_status = task.status if task.status != EmailTaskStatus.GENERATING_DRAFT.value else EmailTaskStatus.REVIEW_REQUIRED.value
        task.llm_profile_id = runtime_llm_profile.id
        task.draft_generation_previous_status = previous_status
        task.draft_generation_started_at = now
        task.draft_rewrite_source_subject = (payload.subject or "").strip() or None
        task.draft_rewrite_source_body_text = source_body_text
        task.draft_rewrite_source_body_html = source_body_html or None
        task.draft_rewrite_source_selected_material_ids = payload.selected_material_ids
        task.selected_material_ids = payload.selected_material_ids
        task.status = EmailTaskStatus.GENERATING_DRAFT.value
        task.last_error = None
        task.updated_at = now
        await session.commit()

    try:
        generation = await asyncio.wait_for(
            llm_runtime.generate_draft_content(
                identity=task.identity,
                primary_material=task.primary_material,
                llm_profile=runtime_llm_profile,
                professor=task.professor,
                available_materials=list(task.identity.materials),
                custom_subject=payload.subject,
                custom_body=source_body_text,
                custom_body_html=source_body_html or None,
                current_match=_build_match_result_from_task(task),
                max_tokens=runtime_settings.draft_max_tokens,
                rewrite_preferences=llm_runtime.DraftRewritePreferences(
                    draft_rewrite_intensity=runtime_settings.draft_rewrite_intensity,
                    draft_rewrite_tone=runtime_settings.draft_rewrite_tone,
                    draft_rewrite_formality=runtime_settings.draft_rewrite_formality,
                    draft_rewrite_length=runtime_settings.draft_rewrite_length,
                    draft_rewrite_specificity=runtime_settings.draft_rewrite_specificity,
                    draft_template_preservation=runtime_settings.draft_template_preservation,
                    draft_custom_instruction=runtime_settings.draft_custom_instruction,
                ),
            ),
            timeout=WORKSPACE_DRAFT_REWRITE_TIMEOUT_SECONDS,
        )
    except asyncio.TimeoutError:
        await _restore_workspace_rewrite_source(session_factory, task_id, WORKSPACE_DRAFT_REWRITE_TIMEOUT_MESSAGE)
        raise ValueError(WORKSPACE_DRAFT_REWRITE_TIMEOUT_MESSAGE)
    except llm_runtime.LLMRuntimeError as exc:
        await _restore_workspace_rewrite_source(session_factory, task_id, str(exc))
        raise
    except Exception as exc:
        await _restore_workspace_rewrite_source(session_factory, task_id, str(exc))
        raise

    return await _complete_workspace_rewrite(session_factory, task_id, generation)
```

实现时不要在 session 关闭后继续访问 ORM relationship。把 LLM 所需的 identity、professor、primary_material、available_materials、runtime_settings、runtime_llm_profile 和 `current_match` 在 session 内拷贝到局部变量，再 commit 后发起 LLM 请求。

新增 `_restore_workspace_rewrite_source()` 和 `_complete_workspace_rewrite()`：

```python
async def _restore_workspace_rewrite_source(
    session_factory: async_sessionmaker[AsyncSession],
    task_id: int,
    error_message: str,
) -> tuple[int, int, int]:
    async with session_factory() as session:
        task = await _load_email_task(session, task_id)
        if not task:
            raise ValueError(f"EmailTask {task_id} 不存在")
        task.approved_subject = task.draft_rewrite_source_subject
        task.approved_body_text = task.draft_rewrite_source_body_text or ""
        task.approved_body_html = task.draft_rewrite_source_body_html or text_to_email_html(task.approved_body_text or "").html
        task.selected_material_ids = task.draft_rewrite_source_selected_material_ids
        task.status = task.draft_generation_previous_status or EmailTaskStatus.REVIEW_REQUIRED.value
        task.draft_generation_previous_status = None
        task.draft_generation_started_at = None
        task.last_error = error_message
        task.updated_at = utc_now()
        await session.commit()
        return task.professor_id, task.identity_id, task.llm_profile_id
```

成功提交时写入 `generated_*`、清空 `draft_generation_previous_status` 和 `draft_generation_started_at`、保留或清空 `draft_rewrite_source_*` 均可；为诊断保留源字段，但前端不展示。

- [ ] **步骤 5：运行测试验证通过**

运行：`cd backend && uv run python -m unittest test.test_api_endpoints.ApiEndpointTests.test_rewrite_draft_uses_request_body_as_llm_input test.test_api_endpoints.ApiEndpointTests.test_rewrite_draft_rejects_empty_body_without_calling_llm test.test_api_endpoints.ApiEndpointTests.test_rewrite_draft_persists_source_before_llm_returns`

预期：PASS。

- [ ] **步骤 6：Commit**

```bash
git add backend/app/schemas/email_task.py backend/app/api/email_tasks.py backend/app/services/task_runtime.py backend/test/test_api_endpoints.py
git commit -m "feat(backend): add workspace AI draft rewrite endpoint"
```

---

## 任务 4：超时、启动恢复和生成中动作拒绝

**文件：**
- 修改：`backend/app/services/batch_draft_generation_runtime.py`
- 修改：`backend/app/services/task_runtime.py`
- 修改：`backend/main.py`
- 修改：`backend/test/test_batch_draft_generation_runtime.py`
- 修改：`backend/test/test_startup_runtime.py`
- 修改：`backend/test/test_api_endpoints.py`

- [ ] **步骤 1：编写失败的恢复和动作拒绝测试**

在 `backend/test/test_batch_draft_generation_runtime.py` 增加：

```python
def test_recover_stale_workspace_rewrite_uses_started_at_and_restores_source(self) -> None:
    task_id = self._run_async(self._create_manual_workspace_rewrite_task(
        started_at=datetime.now(UTC) - timedelta(minutes=6),
        previous_status=EmailTaskStatus.MATCHED.value,
        source_body="改写前正文",
    ))

    restored_count = self._run_async(recover_stale_workspace_draft_rewrites(self.session_factory))
    task = self._run_async(self._get_task(task_id))

    self.assertEqual(restored_count, 1)
    self.assertEqual(task.status, EmailTaskStatus.MATCHED.value)
    self.assertEqual(task.approved_body_text, "改写前正文")
    self.assertEqual(task.last_error, "AI 改写已中断，请重试")
    self.assertIsNone(task.draft_generation_started_at)

def test_recover_stale_workspace_rewrite_skips_recent_started_at(self) -> None:
    task_id = self._run_async(self._create_manual_workspace_rewrite_task(
        started_at=datetime.now(UTC) - timedelta(minutes=4),
        previous_status=EmailTaskStatus.MATCHED.value,
        source_body="改写前正文",
    ))

    restored_count = self._run_async(recover_stale_workspace_draft_rewrites(self.session_factory))
    task = self._run_async(self._get_task(task_id))

    self.assertEqual(restored_count, 0)
    self.assertEqual(task.status, EmailTaskStatus.GENERATING_DRAFT.value)
```

在 `backend/test/test_api_endpoints.py` 增加：

```python
def test_save_send_and_schedule_reject_generating_rewrite(self) -> None:
    task_id = self._create_generating_workspace_rewrite_task()
    payload = {
        "subject": "主题",
        "body_text": "正文",
        "body_html": "<p>正文</p>",
        "selected_material_ids": [],
    }
    save_response = self.client.post(f"/api/email-tasks/{task_id}/save-draft", json=payload)
    send_response = self.client.post(f"/api/email-tasks/{task_id}/approve-and-send", json=payload)
    schedule_response = self.client.post(
        f"/api/email-tasks/{task_id}/approve-and-schedule",
        json={**payload, "scheduled_at": "2030-01-01T10:00:00+00:00"},
    )
    self.assertEqual(save_response.status_code, 400)
    self.assertEqual(send_response.status_code, 400)
    self.assertEqual(schedule_response.status_code, 400)
    self.assertEqual(save_response.json()["detail"], "AI 正在改写当前草稿，请等待完成后再保存。")
    self.assertEqual(send_response.json()["detail"], "AI 正在改写当前草稿，请等待完成后再发送。")
```

- [ ] **步骤 2：运行测试验证失败**

运行：`cd backend && uv run python -m unittest test.test_batch_draft_generation_runtime.BatchDraftGenerationRuntimeTests.test_recover_stale_workspace_rewrite_uses_started_at_and_restores_source test.test_batch_draft_generation_runtime.BatchDraftGenerationRuntimeTests.test_recover_stale_workspace_rewrite_skips_recent_started_at test.test_api_endpoints.ApiEndpointTests.test_save_send_and_schedule_reject_generating_rewrite`

预期：FAIL，恢复函数不存在或错误文案仍是旧“草稿正在生成”。

- [ ] **步骤 3：实现 5 分钟恢复函数**

在 `backend/app/services/batch_draft_generation_runtime.py` import 任务运行时常量和恢复 helper，新增：

```python
async def recover_stale_workspace_draft_rewrites(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    now: datetime | None = None,
) -> int:
    resolved_now = now or utc_now()
    cutoff = resolved_now - WORKSPACE_DRAFT_REWRITE_TIMEOUT
    async with session_factory() as session:
        tasks = list(
            await session.scalars(
                select(EmailTask).where(
                    EmailTask.status == EmailTaskStatus.GENERATING_DRAFT.value,
                    EmailTask.draft_generation_started_at.is_not(None),
                    EmailTask.draft_generation_started_at < cutoff,
                )
            )
        )
        for task in tasks:
            task.approved_subject = task.draft_rewrite_source_subject
            task.approved_body_text = task.draft_rewrite_source_body_text or ""
            task.approved_body_html = task.draft_rewrite_source_body_html or text_to_email_html(task.approved_body_text or "").html
            task.selected_material_ids = task.draft_rewrite_source_selected_material_ids
            task.status = task.draft_generation_previous_status or EmailTaskStatus.REVIEW_REQUIRED.value
            task.draft_generation_previous_status = None
            task.draft_generation_started_at = None
            task.last_error = WORKSPACE_DRAFT_REWRITE_INTERRUPTED_MESSAGE
            task.updated_at = resolved_now
        await session.commit()
        return len(tasks)
```

如 `text_to_email_html` 位于 `task_runtime.py`，优先抽出小 helper 或从 `task_runtime` import，避免复制 HTML 规范化逻辑。

- [ ] **步骤 4：启动清理调用工作区恢复**

在 `backend/main.py` import `recover_stale_workspace_draft_rewrites`，并在 `cleanup_runtime_state()` 中先调用：

```python
await recover_stale_workspace_draft_rewrites(get_session_factory())
```

保留现有 `recover_stale_generating_drafts(... stale_after=timedelta(seconds=0))` 测试时，需要调整测试 mock 断言为两个恢复函数都被调用。旧函数用于批量和历史无 `started_at` 的生成状态，新增函数用于工作区 5 分钟规则。

- [ ] **步骤 5：生成中保存、发送、定时文案**

在 `backend/app/services/task_runtime.py` 调整：

```python
def _ensure_task_allows_approval(task: EmailTask) -> None:
    if task.status == EmailTaskStatus.GENERATING_DRAFT.value:
        raise ValueError("AI 正在改写当前草稿，请等待完成后再发送。")
```

在 `_ensure_task_allows_draft_save()` 中先判断生成中：

```python
if task.status == EmailTaskStatus.GENERATING_DRAFT.value:
    raise ValueError("AI 正在改写当前草稿，请等待完成后再保存。")
```

- [ ] **步骤 6：运行测试验证通过**

运行：`cd backend && uv run python -m unittest test.test_batch_draft_generation_runtime.BatchDraftGenerationRuntimeTests.test_recover_stale_workspace_rewrite_uses_started_at_and_restores_source test.test_batch_draft_generation_runtime.BatchDraftGenerationRuntimeTests.test_recover_stale_workspace_rewrite_skips_recent_started_at test.test_api_endpoints.ApiEndpointTests.test_save_send_and_schedule_reject_generating_rewrite test.test_startup_runtime`

预期：PASS。

- [ ] **步骤 7：Commit**

```bash
git add backend/app/services/batch_draft_generation_runtime.py backend/app/services/task_runtime.py backend/main.py backend/test/test_batch_draft_generation_runtime.py backend/test/test_startup_runtime.py backend/test/test_api_endpoints.py
git commit -m "feat(backend): recover stale workspace draft rewrites"
```

---

## 任务 5：前端 DTO、API 和工作区编辑器数据源

**文件：**
- 修改：`frontend/src/types/index.ts`
- 修改：`frontend/src/lib/api/emailTasksApi.ts`
- 修改：`frontend/src/pages/WorkspacePage.tsx`
- 修改：`frontend/src/pages/WorkspacePage.test.tsx`
- 修改：`frontend/src/components/organisms/WorkspaceSidebar.test.tsx`
- 修改：`frontend/src/features/workspace/client/openWorkspaceThread.test.tsx`

- [ ] **步骤 1：编写失败的前端数据源测试**

在 `frontend/src/pages/WorkspacePage.test.tsx` 修改 api mock：

```ts
vi.mock('@/lib/api/emailTasksApi', () => ({
  rewriteDraft: vi.fn(),
  calculateMatch: vi.fn(),
  saveDraft: vi.fn(),
  approveAndSend: vi.fn(),
  approveAndSchedule: vi.fn(),
  cancelScheduledTask: vi.fn(),
  continueManually: vi.fn(),
  startFollowUp: vi.fn(),
  updateTaskOutreachConfig: vi.fn(),
}));
```

新增测试：

```ts
it('uses backend current draft as composer initial value', async () => {
  apiMocks.getWorkspaceThread.mockResolvedValueOnce(buildWorkspaceThread({
    current_task: {
      draft: {
        subject: '后端草稿主题',
        body_text: '后端草稿正文',
        body_html: '<p>后端草稿正文</p>',
        source: 'template',
        sendable: true,
        editable: true,
      },
      approved_body_text: null,
      generated_content_text: null,
      rendered_template_body_text: '旧推导正文',
    },
  }));

  renderWorkspacePage();
  await userEvent.click(await screen.findByRole('button', { name: /编辑草稿|写信/ }));

  expect(screen.getByDisplayValue('后端草稿主题')).toBeInTheDocument();
  expect(screen.getByText('后端草稿正文')).toBeInTheDocument();
});
```

新增改写 payload 测试：

```ts
it('sends current composer content when rewriting draft', async () => {
  apiMocks.getWorkspaceThread.mockResolvedValueOnce(buildWorkspaceThread({
    current_task: {
      id: 101,
      primary_material_id: 7,
      selected_material_ids: [7],
      draft: {
        subject: '模板主题',
        body_text: '模板正文',
        body_html: '<p>模板正文</p>',
        source: 'template',
        sendable: true,
        editable: true,
      },
    },
    professor: { research_direction: 'AI systems' },
  }));
  apiMocks.rewriteDraft.mockResolvedValueOnce(buildWorkspaceThread({
    current_task: {
      draft: {
        subject: '改写后主题',
        body_text: '改写后正文',
        body_html: '<p>改写后正文</p>',
        source: 'ai_rewrite',
        sendable: true,
        editable: true,
      },
    },
  }));

  renderWorkspacePage();
  await userEvent.click(await screen.findByRole('button', { name: /编辑草稿|写信/ }));
  await userEvent.clear(screen.getByLabelText('邮件主题'));
  await userEvent.type(screen.getByLabelText('邮件主题'), '用户改过主题');
  await replaceEditorText('用户改过正文');
  await userEvent.click(screen.getByRole('button', { name: 'AI 改写' }));

  expect(apiMocks.rewriteDraft).toHaveBeenCalledWith(101, {
    subject: '用户改过主题',
    body_text: '用户改过正文',
    body_html: expect.stringContaining('用户改过正文'),
    selected_material_ids: [7],
    llm_profile_id: 2,
  });
});
```

- [ ] **步骤 2：运行测试验证失败**

运行：`cd frontend && npm run test -- WorkspacePage.test.tsx`

预期：FAIL，`rewriteDraft` 不存在或编辑器仍使用旧推导字段。

- [ ] **步骤 3：新增类型和 API**

在 `frontend/src/types/index.ts` 增加：

```ts
export type WorkspaceDraftSourceDTO =
  | 'saved'
  | 'ai_rewrite'
  | 'template'
  | 'manual_empty'
  | 'rewrite_source';

export interface WorkspaceDraftDTO {
  subject: string | null;
  body_text: string;
  body_html: string | null;
  source: WorkspaceDraftSourceDTO;
  sendable: boolean;
  editable: boolean;
}
```

在 `WorkspaceTaskSummaryDTO` 增加：

```ts
draft: WorkspaceDraftDTO;
```

新增：

```ts
export interface EmailTaskRewriteDraftPayloadDTO extends EmailTaskApprovalPayloadDTO {
  llm_profile_id: number | null;
}
```

在 `frontend/src/lib/api/emailTasksApi.ts` 增加：

```ts
export const rewriteDraft = (taskId: number, payload: EmailTaskRewriteDraftPayloadDTO) =>
  apiFetch<WorkspaceThreadDTO>(`/api/email-tasks/${taskId}/rewrite-draft`, {
    method: 'POST',
    body: JSON.stringify(payload),
  });
```

- [ ] **步骤 4：工作区用 `draft` 初始化编辑器**

在 `frontend/src/pages/WorkspacePage.tsx` 的 import 中把 `generateDraft` 替换为 `rewriteDraft`。

把 `syncComposer()` 中复杂本地推导收敛为：

```ts
const draft = currentTask?.draft;
const hiddenDraftContent = shouldHideDirectDraftContent(currentTask);
const nextSubject = hiddenDraftContent ? '' : draft?.subject ?? '';
const nextContentText = hiddenDraftContent ? '' : draft?.body_text ?? '';
const nextContentHtml = hiddenDraftContent ? null : draft?.body_html ?? null;

setSubject(nextSubject);
setContent(nextContentText);
setContentHtml(nextContentHtml);
setComposerHasSendableDraft(Boolean(!hiddenDraftContent && draft?.sendable));
setSelectedMaterialIds(hiddenDraftContent ? [] : currentTask?.selected_material_ids ?? []);
```

保留 `deriveBodyTextFromDraft()` 给 payload 构造使用，删除 `getLatestDraftMessage()` 和 `draftMatchesTemplate()` 的工作区初始化调用。

- [ ] **步骤 5：改写请求发送点击瞬间内容**

把 `handleGenerateDraft` 改名为 `handleRewriteDraft`，内容改为：

```ts
const handleRewriteDraft = useCallback(() => {
  if (!currentTaskId) {
    return;
  }
  const bodyText = deriveBodyTextFromDraft({ content, contentHtml });
  if (!bodyText.trim() && !contentHtml?.trim()) {
    notifyError('无法 AI 改写', '先写入正文或配置默认模板后再使用 AI 改写');
    return;
  }
  const startedAt = Date.now();
  void runAction(
    () =>
      rewriteDraft(currentTaskId, {
        subject: subject.trim() || null,
        body_text: bodyText,
        body_html: contentHtml,
        selected_material_ids: selectedMaterialIds,
        llm_profile_id: selectedLlmProfileId ?? null,
      }),
    'AI 改写失败',
    'AI 改写失败',
    (data) => {
      setComposerExpanded(true);
      notifySuccess(
        'AI 改写已完成',
        buildDraftGenerationSuccessDescription(data.current_task, Date.now() - startedAt),
      );
    },
  );
}, [content, contentHtml, currentTaskId, notifyError, notifySuccess, runAction, selectedLlmProfileId, selectedMaterialIds, subject]);
```

将传给 `WorkspaceComposerDock` 的 handler 改为 `onGenerateDraft={handleRewriteDraft}`，或同步把 prop 命名改成 `onRewriteDraft`。

- [ ] **步骤 6：运行测试验证通过**

运行：`cd frontend && npm run test -- WorkspacePage.test.tsx`

预期：PASS。

- [ ] **步骤 7：Commit**

```bash
git add frontend/src/types/index.ts frontend/src/lib/api/emailTasksApi.ts frontend/src/pages/WorkspacePage.tsx frontend/src/pages/WorkspacePage.test.tsx frontend/src/components/organisms/WorkspaceSidebar.test.tsx frontend/src/features/workspace/client/openWorkspaceThread.test.tsx
git commit -m "feat(frontend): use workspace draft view for composer"
```

---

## 任务 6：前端交互锁定、文案和脏草稿保护

**文件：**
- 修改：`frontend/src/pages/WorkspacePage.tsx`
- 修改：`frontend/src/components/organisms/WorkspaceComposerDock.tsx`
- 修改：`frontend/src/pages/WorkspacePage.test.tsx`

- [ ] **步骤 1：编写失败的交互测试**

在 `frontend/src/pages/WorkspacePage.test.tsx` 增加：

```ts
it('disables rewrite save send schedule and editor while rewriting', async () => {
  apiMocks.getWorkspaceThread.mockResolvedValueOnce(buildWorkspaceThread({
    current_task: {
      status: 'generating_draft',
      draft: {
        subject: '源主题',
        body_text: '源正文',
        body_html: '<p>源正文</p>',
        source: 'rewrite_source',
        sendable: false,
        editable: false,
      },
    },
  }));

  renderWorkspacePage();
  await userEvent.click(await screen.findByRole('button', { name: /编辑草稿|写信/ }));

  expect(screen.getByText('AI 改写中')).toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'AI 改写' })).toBeDisabled();
  expect(screen.getByRole('button', { name: '保存草稿' })).toBeDisabled();
  expect(screen.getByRole('button', { name: '立即发送' })).toBeDisabled();
});
```

新增空草稿测试：

```ts
it('keeps AI rewrite disabled for empty draft', async () => {
  apiMocks.getWorkspaceThread.mockResolvedValueOnce(buildWorkspaceThread({
    current_task: {
      draft: {
        subject: null,
        body_text: '',
        body_html: null,
        source: 'manual_empty',
        sendable: false,
        editable: true,
      },
    },
  }));

  renderWorkspacePage();
  await userEvent.click(await screen.findByRole('button', { name: /写信/ }));

  expect(screen.getByRole('button', { name: 'AI 改写' })).toBeDisabled();
  expect(screen.getByText('先写入正文或配置默认模板后再使用 AI 改写。')).toBeInTheDocument();
});
```

- [ ] **步骤 2：运行测试验证失败**

运行：`cd frontend && npm run test -- WorkspacePage.test.tsx`

预期：FAIL，文案仍是“生成草稿”或生成中保存按钮没有禁用。

- [ ] **步骤 3：实现按钮条件**

在 `frontend/src/pages/WorkspacePage.tsx` 调整：

```ts
const hasDraftBody = hasMeaningfulBody({ content, contentHtml });
const isRewriting = currentTask?.status === 'generating_draft';
const canRewrite =
  Boolean(currentTaskId) &&
  hasDraftBody &&
  !isRewriting &&
  !shouldHideDirectDraftContent(currentTask) &&
  Boolean(currentTask?.primary_material_id) &&
  hasProfessorResearchDirection(thread?.professor);
const canSubmitDraft =
  Boolean(currentTaskId) &&
  hasDraftBody &&
  !isRewriting &&
  !shouldBlockDirectDraftActions(currentTask);
const hasDraft = Boolean(currentTask?.draft?.sendable || hasDraftBody);
```

导航保护改为：

```ts
const shouldBlockNavigation = hasDirtyDraft && !isRewriting;
```

因为改写开始前源草稿已经由后端落库，生成中离开页面不弹保存确认。

- [ ] **步骤 4：更新 `WorkspaceComposerDock` 文案和锁定状态**

给 props 增加 `isRewriting` 和 `hasDraftBody`，把 `editorDisabled` 改为：

```ts
const editorDisabled = acting || draftSaving || isRewriting || currentTask.draft?.editable === false;
```

写信区状态 pill：

```tsx
{isRewriting ? 'AI 改写中' : hasDraftBody ? '草稿可编辑' : '空草稿'}
```

生成区改为：

```tsx
<ComposerSection
  icon={<Bot className="h-4 w-4" />}
  title="AI 改写"
  description={
    isRewriting
      ? '正在改写当前草稿，完成前不能保存或发送。'
      : hasDraftBody
        ? '基于当前编辑器内容生成个性化版本。'
        : '先写入正文或配置默认模板后再使用 AI 改写。'
  }
>
```

按钮文案全部从“生成草稿”改为“AI 改写”。折叠态标题：

```tsx
const collapsedTitle = isRewriting ? 'AI 正在改写' : hasDraftBody ? '继续写信' : '写第一封信';
const collapsedDescription = isRewriting
  ? '当前草稿已锁定，完成后会自动显示新版本。'
  : hasDraftBody
    ? '可直接编辑、保存或发送，也可以让 AI 改写。'
    : '先写入正文或配置默认模板后再使用 AI 改写。';
```

保存、定时、立即发送按钮都用 `disabled={editorDisabled || !canSubmitDraft}` 或 `disabled={editorDisabled || !draftReady}`，保证生成中锁住。

- [ ] **步骤 5：运行测试验证通过**

运行：`cd frontend && npm run test -- WorkspacePage.test.tsx`

预期：PASS。

- [ ] **步骤 6：Commit**

```bash
git add frontend/src/pages/WorkspacePage.tsx frontend/src/components/organisms/WorkspaceComposerDock.tsx frontend/src/pages/WorkspacePage.test.tsx
git commit -m "feat(frontend): clarify workspace AI rewrite interactions"
```

---

## 任务 7：回归验证与收尾

**文件：**
- 修改：按前面任务实际改动文件。

- [ ] **步骤 1：运行后端重点测试**

运行：

```bash
cd backend && uv run python -m unittest \
  test.test_database_schema \
  test.test_workspace_support \
  test.test_api_endpoints \
  test.test_batch_draft_generation_runtime \
  test.test_startup_runtime
```

预期：全部 PASS。

- [ ] **步骤 2：运行前端重点测试和 lint**

运行：

```bash
cd frontend && npm run test -- WorkspacePage.test.tsx WorkspaceSidebar.test.tsx openWorkspaceThread.test.tsx
cd frontend && npm run lint
```

预期：全部 PASS，lint 无错误。

- [ ] **步骤 3：运行构建验证**

运行：

```bash
cd frontend && npm run build
```

预期：TypeScript 编译和 Vite build 成功。

- [ ] **步骤 4：检查旧文案和旧接口误用**

运行：

```bash
rg -n "生成草稿|generateDraft\\(" frontend/src/pages/WorkspacePage.tsx frontend/src/components/organisms/WorkspaceComposerDock.tsx frontend/src/lib/api/emailTasksApi.ts
```

预期：工作区页面和写信区组件中不再出现面向用户的“生成草稿”；`generateDraft(` 只允许保留在旧兼容 API 或测试写作页，不允许被 `WorkspacePage.tsx` 调用。

- [ ] **步骤 5：检查 git diff**

运行：

```bash
git status --short
git diff --stat
```

预期：只包含本计划涉及的后端、前端和测试文件。

- [ ] **步骤 6：最终 commit**

如果任务 1 到任务 6 已经逐项提交，此步骤只在存在验证修复时提交：

```bash
git add <验证修复涉及的文件>
git commit -m "test: cover workspace draft rewrite flow"
```

如果没有未提交文件，记录“无额外提交”。

---

## 计划自检

- 规格覆盖：当前草稿视图、模板默认显示、空草稿禁用 AI、点击瞬间内容改写、生成中锁定、离开页面例外、5 分钟请求超时、启动恢复和生成中后端动作拒绝均已对应到任务。
- 占位符扫描：计划不包含待补充的实现项；每个任务都有明确文件、测试、实现方向、验证命令和 commit。
- 类型一致性：后端使用 `WorkspaceDraftRead` 和 `EmailTaskRewriteDraftRequest`；前端使用 `WorkspaceDraftDTO` 和 `EmailTaskRewriteDraftPayloadDTO`；接口名统一为 `rewrite-draft` / `rewriteDraft`。
- 风险控制：旧 `/generate-draft` 保留给批量和兼容路径，工作区页面迁移到 `/rewrite-draft`；批量 30 分钟恢复不被工作区 5 分钟规则直接替换。
