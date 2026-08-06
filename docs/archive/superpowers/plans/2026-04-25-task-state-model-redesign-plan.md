# 联系任务状态模型重构实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 让匹配度彻底退出执行链路，移除“低分自动跳过”，将批量停止改为取消未完成子任务，并允许用户从工作区新建手动任务继续联系或发起跟进邮件。

**架构：** 后端先重构 `email_tasks` 状态与迁移逻辑，引入 `canceled`、`source`、`parent_task_id`、`cancellation_reason`，再新增“作为单独联系继续 / 写跟进邮件”的手动任务创建能力。前端同步切换为“首页展示导师关系状态、工作区展示执行动作”，并从个人页移除 `match_threshold` 配置入口。

**技术栈：** FastAPI、SQLAlchemy、Alembic、Pydantic、unittest、React、TypeScript、Vitest、Testing Library、Tailwind CSS。

---

## 实施注意事项

- 当前工作区存在用户未提交改动，实施时只修改本计划列出的文件，绝不回滚无关改动。
- 所有文件保持 UTF-8 编码。
- 后端命令在 `backend/` 目录下用 `uv` 执行。
- 前端命令在 `frontend/` 目录下执行。
- 按 TDD 走：先写失败测试，确认失败，再写最少实现，最后跑回归。
- 本计划中的 commit 步骤是逻辑提交建议；如果工作区仍有用户未提交改动，提交前先确认暂存区只包含本任务文件。

## 文件结构

- 创建：`backend/alembic/versions/4c1a2b3d4e5f_redesign_contact_task_states.py`
  - 为 `email_tasks` 增加 `source`、`parent_task_id`、`cancellation_reason`，把历史 `skipped` 迁移为 `matched / canceled`。
- 修改：`backend/app/models/email_task.py`
  - 调整 `EmailTaskStatus`，新增字段与父子任务关系。
- 修改：`backend/app/models/__init__.py`
  - 导出更新后的 `EmailTaskStatus`。
- 修改：`backend/app/services/task_runtime.py`
  - 移除匹配低自动跳过；新增“继续联系 / 跟进邮件”手动任务创建逻辑；发送逻辑识别 `canceled`。
- 修改：`backend/app/services/materials.py`
  - 终态引用集合从 `skipped` 改为 `canceled`。
- 修改：`backend/app/api/batch_tasks.py`
  - 停止批量任务时把未完成子任务改为 `canceled` 并记录原因。
- 修改：`backend/app/api/workspace_support.py`
  - 工作区返回新增的任务来源、取消原因、父任务关系和动作能力。
- 修改：`backend/app/api/email_tasks.py`
  - 新增 `continue-manually`、`start-follow-up` 接口。
- 修改：`backend/app/api/professors.py`
  - 首页改为返回导师关系状态，不再直接映射底层任务状态。
- 修改：`backend/app/schemas/workspace.py`
  - 暴露 `source`、`parent_task_id`、`cancellation_reason`、动作能力字段。
- 修改：`backend/app/schemas/professor.py`
  - 首页导师卡片状态切换为关系状态枚举。
- 修改：`backend/test/test_api_endpoints.py`
  - 覆盖匹配低不跳过、批量停止改取消、继续联系新建手动任务、跟进邮件新建手动任务、首页状态映射。
- 修改：`backend/test/test_database_schema.py`
  - 覆盖新列存在、历史迁移结果正确。
- 修改：`frontend/src/types/index.ts`
  - 更新首页状态、工作区状态、任务摘要字段与动作能力。
- 修改：`frontend/src/lib/api/emailTasksApi.ts`
  - 新增继续联系和跟进邮件 API。
- 修改：`frontend/src/features/professor-status/dashboardStatus.ts`
  - 首页状态组选项基于导师关系状态。
- 修改：`frontend/src/pages/HomePage.tsx`
  - 首页状态筛选与展示切换为导师关系状态。
- 修改：`frontend/src/features/workspace/client/getWorkspaceNextStep.ts`
  - 工作区下一步改成面向 `canceled / send_failed / sent / reply_detected` 的动作语言。
- 修改：`frontend/src/pages/WorkspacePage.tsx`
  - 接入新动作，显示“作为单独联系继续 / 写跟进邮件”。
- 修改：`frontend/src/components/organisms/WorkspaceComposerDock.tsx`
  - 展示新按钮和原因文案。
- 修改：`frontend/src/pages/ProfilePage.tsx`
  - 移除 `match_threshold` 表单控件与保存逻辑。
- 修改：`frontend/test/professorDashboardStatus.test.ts`
  - 覆盖首页状态收敛后的关系状态映射。
- 修改：`frontend/test/HomePageOnboarding.test.tsx`
  - 验证首页状态选项。
- 修改：`frontend/test/getWorkspaceNextStep.test.ts`
  - 验证工作区下一步提示切换。
- 修改：`frontend/test/WorkspacePageNextStep.test.tsx`
  - 验证继续联系、跟进邮件、发送失败等动作。
- 修改：`frontend/test/ProfilePageOnboarding.test.tsx`
  - 验证个人页不再显示匹配阈值输入。
- 修改：`docs/project_description.md`
  - 更新任务状态机描述。
- 修改：`docs/database_table_design.md`
  - 更新 `email_tasks` 字段和状态说明。

## 任务 1：重构后端任务状态与数据库迁移

**文件：**
- 创建：`backend/alembic/versions/4c1a2b3d4e5f_redesign_contact_task_states.py`
- 修改：`backend/app/models/email_task.py`
- 修改：`backend/app/models/__init__.py`
- 修改：`backend/app/services/task_runtime.py`
- 修改：`backend/app/services/materials.py`
- 修改：`backend/app/api/batch_tasks.py`
- 测试：`backend/test/test_api_endpoints.py`
- 测试：`backend/test/test_database_schema.py`

- [ ] **步骤 1：编写失败的 API 与迁移测试**

在 `backend/test/test_api_endpoints.py` 新增两个测试：

```python
def test_calculate_match_keeps_low_score_task_in_matched_state(self) -> None:
    identity_id = self._create_identity(with_imap=False)
    llm_id = self._create_llm()
    professor_id = self._create_professor(name="低分导师")
    workspace = self.client.post(
        f"/api/workspaces/{professor_id}/ensure-task",
        params={"identity_id": identity_id, "llm_profile_id": llm_id},
    )
    task_id = workspace.json()["current_task"]["id"]

    with patch(
        "app.services.task_runtime.llm_runtime.generate_match_evaluation",
        AsyncMock(return_value=self._build_match_evaluation_result(match_score=22)),
    ):
        response = self.client.post(f"/api/email-tasks/{task_id}/calculate-match")

    self.assertEqual(response.status_code, 200, msg=response.text)
    self.assertEqual(response.json()["current_task"]["status"], "matched")
    self.assertEqual(response.json()["current_task"]["match_score"], 22)

def test_stop_batch_task_marks_pending_items_as_canceled(self) -> None:
    identity_id = self._create_identity(with_imap=False)
    llm_id = self._create_llm()
    professor_ids = [self._create_professor(name="批量导师甲"), self._create_professor(name="批量导师乙")]
    create_response = self.client.post(
        "/api/batch-tasks",
        json={
            "identity_id": identity_id,
            "llm_profile_id": llm_id,
            "name": "停止后取消",
            "professor_ids": professor_ids,
            "schedule_type": "immediate",
            "window_start_time": None,
            "window_end_time": None,
            "emails_per_window": None,
            "primary_material_id": None,
            "email_subject": "测试主题",
            "email_body": "测试正文",
            "selected_material_ids": None,
            "outreach_generation_mode": "template",
            "outreach_template_subject": "测试主题",
            "outreach_template_body_text": "测试正文",
            "outreach_template_body_html": None,
        },
    )
    batch_id = create_response.json()["id"]

    stop_response = self.client.post(f"/api/batch-tasks/{batch_id}/stop")

    self.assertEqual(stop_response.status_code, 200, msg=stop_response.text)
    workspace = self.client.get(
        f"/api/workspaces/{professor_ids[0]}",
        params={"identity_id": identity_id, "llm_profile_id": llm_id},
    )
    self.assertEqual(workspace.json()["current_task"]["status"], "canceled")
    self.assertEqual(workspace.json()["current_task"]["cancellation_reason"], "batch_stopped")
```

在 `backend/test/test_database_schema.py` 新增迁移验证：

```python
def test_email_tasks_has_manual_source_and_cancellation_fields(self) -> None:
    columns = {
        row[1]
        for row in self.connection.execute("PRAGMA table_info(email_tasks)").fetchall()
    }
    self.assertIn("source", columns)
    self.assertIn("parent_task_id", columns)
    self.assertIn("cancellation_reason", columns)
```

- [ ] **步骤 2：运行测试验证失败**

运行：

```powershell
cd backend
uv run python -m unittest ^
  test.test_api_endpoints.ApiEndpointTests.test_calculate_match_keeps_low_score_task_in_matched_state ^
  test.test_api_endpoints.ApiEndpointTests.test_stop_batch_task_marks_pending_items_as_canceled ^
  test.test_database_schema.DatabaseSchemaTests.test_email_tasks_has_manual_source_and_cancellation_fields
```

预期：FAIL。
当前失败点应包括：
- 低分任务仍返回 `skipped`
- 批量停止后仍返回 `skipped`
- 数据库缺少 `source`、`parent_task_id`、`cancellation_reason`

- [ ] **步骤 3：新增 Alembic 迁移**

创建 `backend/alembic/versions/4c1a2b3d4e5f_redesign_contact_task_states.py`：

```python
"""redesign contact task states

Revision ID: 4c1a2b3d4e5f
Revises: 2f6a9d8c1e20
Create Date: 2026-04-25 00:00:00.000000
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "4c1a2b3d4e5f"
down_revision: Union[str, Sequence[str], None] = "2f6a9d8c1e20"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("email_tasks") as batch_op:
        batch_op.add_column(sa.Column("source", sa.String(length=20), nullable=True))
        batch_op.add_column(sa.Column("parent_task_id", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("cancellation_reason", sa.String(length=64), nullable=True))
        batch_op.create_foreign_key(
            "fk_email_tasks_parent_task_id",
            "email_tasks",
            ["parent_task_id"],
            ["id"],
        )

    op.execute("UPDATE email_tasks SET source = CASE WHEN batch_task_id IS NULL THEN 'manual' ELSE 'batch' END")
    op.execute(
        \"\"\"
        UPDATE email_tasks
        SET status = CASE
            WHEN status = 'skipped' AND batch_task_id IS NOT NULL THEN 'canceled'
            WHEN status = 'skipped' THEN 'matched'
            ELSE status
        END,
            cancellation_reason = CASE
            WHEN status = 'skipped' AND batch_task_id IS NOT NULL THEN 'batch_stopped'
            ELSE cancellation_reason
        END
        \"\"\"
    )

    with op.batch_alter_table("email_tasks") as batch_op:
        batch_op.alter_column("source", existing_type=sa.String(length=20), nullable=False)


def downgrade() -> None:
    with op.batch_alter_table("email_tasks") as batch_op:
        batch_op.drop_constraint("fk_email_tasks_parent_task_id", type_="foreignkey")
        batch_op.drop_column("cancellation_reason")
        batch_op.drop_column("parent_task_id")
        batch_op.drop_column("source")
```

- [ ] **步骤 4：更新模型与运行时状态**

在 `backend/app/models/email_task.py` 中把状态和字段改成：

```python
class EmailTaskStatus(StrEnum):
    DISCOVERED = "discovered"
    MATCHED = "matched"
    REVIEW_REQUIRED = "review_required"
    APPROVED = "approved"
    SCHEDULED = "scheduled"
    SENT = "sent"
    SEND_FAILED = "send_failed"
    REPLY_DETECTED = "reply_detected"
    CANCELED = "canceled"


source: Mapped[str] = mapped_column(
    String(20),
    nullable=False,
    server_default=text("'manual'"),
)
parent_task_id: Mapped[int | None] = mapped_column(
    ForeignKey("email_tasks.id"),
    index=True,
    nullable=True,
)
cancellation_reason: Mapped[str | None] = mapped_column(String(64), nullable=True)

parent_task: Mapped["EmailTask | None"] = relationship(
    remote_side="EmailTask.id",
    back_populates="child_tasks",
)
child_tasks: Mapped[list["EmailTask"]] = relationship(
    back_populates="parent_task",
)
```

在 `backend/app/services/task_runtime.py` 的匹配逻辑里改为：

```python
task.match_score = result.match_score
task.match_reason = result.match_reason
task.fit_points = result.fit_points
task.risk_points = result.risk_points
task.match_keywords = result.keywords
task.status = EmailTaskStatus.MATCHED.value
task.updated_at = datetime.now(UTC)
task.last_error = None
```

在 `backend/app/api/batch_tasks.py` 的 `stop_batch_task()` 中改为：

```python
for email_task in task.email_tasks:
    if email_task.status not in {
        EmailTaskStatus.SENT.value,
        EmailTaskStatus.REPLY_DETECTED.value,
    }:
        email_task.status = EmailTaskStatus.CANCELED.value
        email_task.cancellation_reason = "batch_stopped"
        email_task.updated_at = datetime.now(UTC)
```

在 `backend/app/api/batch_tasks.py` 的任务创建与统计里同步更新：

```python
session.add(
    EmailTask(
        batch_task_id=batch_task.id,
        identity_id=payload.identity_id,
        llm_profile_id=payload.llm_profile_id,
        professor_id=professor.id,
        primary_material_id=primary_material_id,
        outreach_generation_mode=outreach_config.generation_mode,
        outreach_template_subject=_normalize_nullable_text(outreach_config.subject_template),
        outreach_template_body_text=_normalize_nullable_text(outreach_config.body_text_template),
        outreach_template_body_html=_normalize_nullable_text(outreach_config.body_html_template),
        status=EmailTaskStatus.DISCOVERED.value,
        source="batch",
        selected_material_ids=selected_material_ids,
    ),
)

pending_generation_count = sum(
    status_counter.get(item, 0)
    for item in [
        EmailTaskStatus.DISCOVERED.value,
        EmailTaskStatus.MATCHED.value,
    ]
)
```

在 `backend/app/api/workspace_support.py` 的 `ensure_workspace_task()` 中补上：

```python
task = EmailTask(
    batch_task_id=None,
    identity_id=identity.id,
    llm_profile_id=llm_profile_id,
    professor_id=professor.id,
    primary_material_id=identity.current_primary_material_id,
    outreach_generation_mode=snapshot.generation_mode,
    outreach_template_subject=_normalize_nullable_text(snapshot.subject_template),
    outreach_template_body_text=_normalize_nullable_text(snapshot.body_text_template),
    outreach_template_body_html=_normalize_nullable_text(snapshot.body_html_template),
    status=EmailTaskStatus.DISCOVERED.value,
    source="manual",
    selected_material_ids=None,
    created_at=datetime.now(UTC),
    updated_at=datetime.now(UTC),
)
```

在 `backend/app/services/materials.py` 中把终态集合更新为：

```python
TERMINAL_MATERIAL_REFERENCING_STATUSES = {
    EmailTaskStatus.SENT.value,
    EmailTaskStatus.REPLY_DETECTED.value,
    EmailTaskStatus.CANCELED.value,
}
```

- [ ] **步骤 5：运行测试验证通过**

运行：

```powershell
cd backend
uv run python -m unittest ^
  test.test_api_endpoints.ApiEndpointTests.test_calculate_match_keeps_low_score_task_in_matched_state ^
  test.test_api_endpoints.ApiEndpointTests.test_stop_batch_task_marks_pending_items_as_canceled ^
  test.test_database_schema.DatabaseSchemaTests.test_email_tasks_has_manual_source_and_cancellation_fields
```

预期：PASS。

- [ ] **步骤 6：Commit**

```bash
git add backend/alembic/versions/4c1a2b3d4e5f_redesign_contact_task_states.py \
  backend/app/models/email_task.py \
  backend/app/models/__init__.py \
  backend/app/services/task_runtime.py \
  backend/app/services/materials.py \
  backend/app/api/batch_tasks.py \
  backend/test/test_api_endpoints.py \
  backend/test/test_database_schema.py
git commit -m "feat(状态模型): 引入取消态并移除低分自动跳过"
```

## 任务 2：新增手动继续联系与跟进邮件任务

**文件：**
- 修改：`backend/app/services/task_runtime.py`
- 修改：`backend/app/api/email_tasks.py`
- 修改：`backend/app/api/workspace_support.py`
- 修改：`backend/app/schemas/workspace.py`
- 测试：`backend/test/test_api_endpoints.py`

- [ ] **步骤 1：编写失败的继续联系与跟进测试**

在 `backend/test/test_api_endpoints.py` 新增：

```python
def test_continue_manually_creates_manual_child_task_from_batch_stopped_task(self) -> None:
    identity_id = self._create_identity(with_imap=False)
    llm_id = self._create_llm()
    professor_id = self._create_professor(name="继续联系导师")

    batch_response = self.client.post(
        "/api/batch-tasks",
        json={
            "identity_id": identity_id,
            "llm_profile_id": llm_id,
            "name": "停止后继续",
            "professor_ids": [professor_id],
            "schedule_type": "immediate",
            "window_start_time": None,
            "window_end_time": None,
            "emails_per_window": None,
            "primary_material_id": None,
            "email_subject": "测试主题",
            "email_body": "测试正文",
            "selected_material_ids": None,
            "outreach_generation_mode": "template",
            "outreach_template_subject": "测试主题",
            "outreach_template_body_text": "测试正文",
            "outreach_template_body_html": None,
        },
    )
    batch_id = batch_response.json()["id"]
    self.client.post(f"/api/batch-tasks/{batch_id}/stop")

    workspace = self.client.get(
        f"/api/workspaces/{professor_id}",
        params={"identity_id": identity_id, "llm_profile_id": llm_id},
    )
    original_task = workspace.json()["current_task"]

    continue_response = self.client.post(
        f"/api/email-tasks/{original_task['id']}/continue-manually",
    )

    self.assertEqual(continue_response.status_code, 200, msg=continue_response.text)
    current_task = continue_response.json()["current_task"]
    self.assertNotEqual(current_task["id"], original_task["id"])
    self.assertEqual(current_task["source"], "manual")
    self.assertIsNone(current_task["batch_task_id"])
    self.assertEqual(current_task["parent_task_id"], original_task["id"])

def test_start_follow_up_creates_manual_child_task_from_sent_task(self) -> None:
    identity_id = self._create_identity(with_imap=False)
    llm_id = self._create_llm()
    professor_id = self._create_professor(name="跟进导师")
    workspace = self.client.post(
        f"/api/workspaces/{professor_id}/ensure-task",
        params={"identity_id": identity_id, "llm_profile_id": llm_id},
    )
    task_id = workspace.json()["current_task"]["id"]

    with patch(
        "app.services.task_runtime.mail_runtime.send_email",
        AsyncMock(return_value=self._build_send_result(message_id="<follow-up@example.com>")),
    ):
        self.client.post(
            f"/api/email-tasks/{task_id}/approve-and-send",
            json={
                "subject": "首封邮件",
                "body_text": "老师您好",
                "body_html": None,
                "selected_material_ids": [],
            },
        )

    follow_up_response = self.client.post(f"/api/email-tasks/{task_id}/start-follow-up")

    self.assertEqual(follow_up_response.status_code, 200, msg=follow_up_response.text)
    current_task = follow_up_response.json()["current_task"]
    self.assertEqual(current_task["source"], "manual")
    self.assertEqual(current_task["parent_task_id"], task_id)
    self.assertEqual(current_task["status"], "matched")
```

- [ ] **步骤 2：运行测试验证失败**

运行：

```powershell
cd backend
uv run python -m unittest ^
  test.test_api_endpoints.ApiEndpointTests.test_continue_manually_creates_manual_child_task_from_batch_stopped_task ^
  test.test_api_endpoints.ApiEndpointTests.test_start_follow_up_creates_manual_child_task_from_sent_task
```

预期：FAIL，接口返回 404 或响应中缺少 `source`、`parent_task_id`、`cancellation_reason`。

- [ ] **步骤 3：实现手动子任务创建服务**

在 `backend/app/services/task_runtime.py` 新增通用辅助函数和两个服务入口：

```python
def _derive_manual_child_status(task: EmailTask, *, reuse_existing_draft: bool) -> str:
    if reuse_existing_draft and (
        task.approved_body_text
        or task.generated_content_text
        or task.generated_content_html
    ):
        return EmailTaskStatus.REVIEW_REQUIRED.value
    if task.match_score is not None:
        return EmailTaskStatus.MATCHED.value
    return EmailTaskStatus.DISCOVERED.value


async def continue_task_manually(
    session_factory: async_sessionmaker[AsyncSession],
    task_id: int,
) -> tuple[int, int, int]:
    return await _create_manual_child_task(
        session_factory,
        task_id=task_id,
        reuse_existing_draft=True,
        allowed_statuses={EmailTaskStatus.CANCELED.value},
    )


async def start_follow_up_task(
    session_factory: async_sessionmaker[AsyncSession],
    task_id: int,
) -> tuple[int, int, int]:
    return await _create_manual_child_task(
        session_factory,
        task_id=task_id,
        reuse_existing_draft=False,
        allowed_statuses={EmailTaskStatus.SENT.value, EmailTaskStatus.REPLY_DETECTED.value},
    )
```

在 `_create_manual_child_task()` 中写最小复制逻辑：

```python
child_task = EmailTask(
    batch_task_id=None,
    identity_id=task.identity_id,
    llm_profile_id=task.llm_profile_id,
    professor_id=task.professor_id,
    primary_material_id=task.primary_material_id,
    outreach_generation_mode=task.outreach_generation_mode,
    outreach_template_subject=task.outreach_template_subject,
    outreach_template_body_text=task.outreach_template_body_text,
    outreach_template_body_html=task.outreach_template_body_html,
    selected_material_ids=task.selected_material_ids,
    match_score=task.match_score,
    match_reason=task.match_reason,
    fit_points=task.fit_points,
    risk_points=task.risk_points,
    match_keywords=task.match_keywords,
    generated_subject=task.generated_subject if reuse_existing_draft else None,
    generated_content_text=task.generated_content_text if reuse_existing_draft else None,
    generated_content_html=task.generated_content_html if reuse_existing_draft else None,
    approved_subject=task.approved_subject if reuse_existing_draft else None,
    approved_body_text=task.approved_body_text if reuse_existing_draft else None,
    approved_body_html=task.approved_body_html if reuse_existing_draft else None,
    status=_derive_manual_child_status(task, reuse_existing_draft=reuse_existing_draft),
    source="manual",
    parent_task_id=task.id,
)
```

- [ ] **步骤 4：暴露 API 与工作区字段**

在 `backend/app/api/email_tasks.py` 新增：

```python
@router.post("/{task_id}/continue-manually", response_model=WorkspaceThreadRead)
async def continue_manually(task_id: int, session: AsyncSession = Depends(get_async_session)) -> WorkspaceThreadRead:
    return await _run_workspace_action(
        session,
        lambda: continue_task_manually(get_session_factory(), task_id),
    )


@router.post("/{task_id}/start-follow-up", response_model=WorkspaceThreadRead)
async def start_follow_up(task_id: int, session: AsyncSession = Depends(get_async_session)) -> WorkspaceThreadRead:
    return await _run_workspace_action(
        session,
        lambda: start_follow_up_task(get_session_factory(), task_id),
    )
```

在 `backend/app/schemas/workspace.py` 给 `WorkspaceTaskSummaryRead` 增加：

```python
source: str | None = None
parent_task_id: int | None = None
cancellation_reason: str | None = None
can_continue_manually: bool = False
can_write_follow_up: bool = False
```

在 `backend/app/api/workspace_support.py` 写入这些字段：

```python
source=current_task.source if current_task else None,
parent_task_id=current_task.parent_task_id if current_task else None,
cancellation_reason=current_task.cancellation_reason if current_task else None,
can_continue_manually=bool(
    current_task
    and current_task.status == EmailTaskStatus.CANCELED.value
    and current_task.cancellation_reason == "batch_stopped"
),
can_write_follow_up=bool(
    current_task and current_task.status in {
        EmailTaskStatus.SENT.value,
        EmailTaskStatus.REPLY_DETECTED.value,
    }
),
```

- [ ] **步骤 5：运行测试验证通过**

运行：

```powershell
cd backend
uv run python -m unittest ^
  test.test_api_endpoints.ApiEndpointTests.test_continue_manually_creates_manual_child_task_from_batch_stopped_task ^
  test.test_api_endpoints.ApiEndpointTests.test_start_follow_up_creates_manual_child_task_from_sent_task
```

预期：PASS。

- [ ] **步骤 6：Commit**

```bash
git add backend/app/services/task_runtime.py \
  backend/app/api/email_tasks.py \
  backend/app/api/workspace_support.py \
  backend/app/schemas/workspace.py \
  backend/test/test_api_endpoints.py
git commit -m "feat(工作区): 支持继续联系和跟进邮件任务"
```

## 任务 3：将首页切换为导师关系状态

**文件：**
- 修改：`backend/app/api/professors.py`
- 修改：`backend/app/schemas/professor.py`
- 修改：`frontend/src/types/index.ts`
- 修改：`frontend/src/features/professor-status/dashboardStatus.ts`
- 修改：`frontend/src/pages/HomePage.tsx`
- 测试：`backend/test/test_api_endpoints.py`
- 测试：`frontend/test/professorDashboardStatus.test.ts`
- 测试：`frontend/test/HomePageOnboarding.test.tsx`

- [ ] **步骤 1：编写失败的首页状态测试**

在 `backend/test/test_api_endpoints.py` 新增：

```python
def test_professor_dashboard_returns_contact_state_labels(self) -> None:
    identity_id = self._create_identity(with_imap=False)
    llm_id = self._create_llm()
    professor_id = self._create_professor(name="首页状态导师")

    workspace = self.client.post(
        f"/api/workspaces/{professor_id}/ensure-task",
        params={"identity_id": identity_id, "llm_profile_id": llm_id},
    )
    task_id = workspace.json()["current_task"]["id"]

    with patch(
        "app.services.task_runtime.llm_runtime.generate_match_evaluation",
        AsyncMock(return_value=self._build_match_evaluation_result(match_score=33)),
    ):
        self.client.post(f"/api/email-tasks/{task_id}/calculate-match")

    response = self.client.get(
        "/api/professors",
        params={"identity_id": identity_id, "llm_profile_id": llm_id},
    )

    self.assertEqual(response.status_code, 200, msg=response.text)
    item = next(entry for entry in response.json() if entry["id"] == professor_id)
    self.assertEqual(item["status"], "preparing")
```

前端测试里把 `frontend/test/professorDashboardStatus.test.ts` 的映射表改成：

```ts
it.each([
  ["not_contacted", "not_started"],
  ["preparing", "preparing"],
  ["ready_to_send", "ready_to_send"],
  ["contacted", "contacted"],
  ["replied", "replied"],
  ["needs_attention", "needs_attention"],
])("maps %s to the homepage group %s", (status, expectedGroup) => {
  expect(getProfessorDashboardStatusGroup(status as ProfessorDashboardItemDTO["status"])).toBe(expectedGroup);
});
```

- [ ] **步骤 2：运行测试验证失败**

运行：

```powershell
cd backend
uv run python -m unittest test.test_api_endpoints.ApiEndpointTests.test_professor_dashboard_returns_contact_state_labels

cd ..\frontend
npm run test -- professorDashboardStatus.test.ts HomePageOnboarding.test.tsx
```

预期：FAIL。
当前失败点应包括：
- 后端仍返回 `matched / scheduled / sent / skipped`
- 首页状态选项不包含 `待发送 / 已联系 / 需处理`

- [ ] **步骤 3：更新后端教授看板状态映射**

在 `backend/app/api/professors.py` 中改造 `_map_dashboard_status()`：

```python
def _map_dashboard_status(task: EmailTask | None) -> str:
    if task is None:
        return "not_contacted"
    if task.is_replied or task.status == EmailTaskStatus.REPLY_DETECTED.value:
        return "replied"
    if task.status in {
        EmailTaskStatus.APPROVED.value,
        EmailTaskStatus.SCHEDULED.value,
    }:
        return "ready_to_send"
    if task.status == EmailTaskStatus.SENT.value:
        return "contacted"
    if task.status in {
        EmailTaskStatus.SEND_FAILED.value,
        EmailTaskStatus.CANCELED.value,
    }:
        return "needs_attention"
    return "preparing"
```

同步更新 `backend/app/schemas/professor.py` 和 `frontend/src/types/index.ts` 的 `ProfessorDashboardItemDTO["status"]` 为：

```ts
status:
  | "not_contacted"
  | "preparing"
  | "ready_to_send"
  | "contacted"
  | "replied"
  | "needs_attention";
```

- [ ] **步骤 4：更新首页状态组选项**

在 `frontend/src/features/professor-status/dashboardStatus.ts` 中改成：

```ts
export type ProfessorDashboardStatusGroup =
  | "not_started"
  | "preparing"
  | "ready_to_send"
  | "contacted"
  | "replied"
  | "needs_attention";

export const PROFESSOR_DASHBOARD_STATUS_GROUP_LABELS = {
  not_started: "未开始",
  preparing: "准备中",
  ready_to_send: "待发送",
  contacted: "已联系",
  replied: "已回复",
  needs_attention: "需处理",
} as const;

export const getProfessorDashboardStatusGroup = (
  status: ProfessorDashboardItemDTO["status"],
): ProfessorDashboardStatusGroup => {
  if (status === "not_contacted") return "not_started";
  return status;
};
```

`frontend/src/pages/HomePage.tsx` 保持消费这个 helper，不再关心底层任务状态。

- [ ] **步骤 5：运行测试验证通过**

运行：

```powershell
cd backend
uv run python -m unittest test.test_api_endpoints.ApiEndpointTests.test_professor_dashboard_returns_contact_state_labels

cd ..\frontend
npm run test -- professorDashboardStatus.test.ts HomePageOnboarding.test.tsx
```

预期：PASS。

- [ ] **步骤 6：Commit**

```bash
git add backend/app/api/professors.py \
  backend/app/schemas/professor.py \
  frontend/src/types/index.ts \
  frontend/src/features/professor-status/dashboardStatus.ts \
  frontend/src/pages/HomePage.tsx \
  frontend/test/professorDashboardStatus.test.ts \
  frontend/test/HomePageOnboarding.test.tsx \
  backend/test/test_api_endpoints.py
git commit -m "feat(首页): 切换导师关系状态展示"
```

## 任务 4：重构工作区动作与个人页阈值配置

**文件：**
- 修改：`frontend/src/types/index.ts`
- 修改：`frontend/src/lib/api/emailTasksApi.ts`
- 修改：`frontend/src/features/workspace/client/getWorkspaceNextStep.ts`
- 修改：`frontend/src/pages/WorkspacePage.tsx`
- 修改：`frontend/src/components/organisms/WorkspaceComposerDock.tsx`
- 修改：`frontend/src/pages/ProfilePage.tsx`
- 测试：`frontend/test/getWorkspaceNextStep.test.ts`
- 测试：`frontend/test/WorkspacePageNextStep.test.tsx`
- 测试：`frontend/test/ProfilePageOnboarding.test.tsx`

- [ ] **步骤 1：编写失败的前端交互测试**

在 `frontend/test/getWorkspaceNextStep.test.ts` 中新增：

```ts
it("prompts to continue manually for batch-stopped canceled tasks", () => {
  expect(
    getWorkspaceNextStep({
      status: "canceled",
      cancellationReason: "batch_stopped",
      hasDraft: false,
      hasPrimaryMaterial: true,
      canWriteFollowUp: false,
    }),
  ).toEqual({ title: "作为单独联系继续" });
});

it("prompts to write a follow-up for sent tasks", () => {
  expect(
    getWorkspaceNextStep({
      status: "sent",
      cancellationReason: null,
      hasDraft: false,
      hasPrimaryMaterial: true,
      canWriteFollowUp: true,
    }),
  ).toEqual({ title: "写跟进邮件" });
});
```

在 `frontend/test/WorkspacePageNextStep.test.tsx` 中新增：

```ts
it("shows continue manually for batch-stopped canceled tasks", async () => {
  mockedGetWorkspaceThread.mockResolvedValue(
    buildThread({
      status: "canceled",
      cancellationReason: "batch_stopped",
      canContinueManually: true,
    }),
  );

  renderPage();

  expect(await screen.findByText("作为单独联系继续")).toBeInTheDocument();
});

it("shows write follow-up for sent tasks", async () => {
  mockedGetWorkspaceThread.mockResolvedValue(
    buildThread({
      status: "sent",
      canWriteFollowUp: true,
    }),
  );

  renderPage();

  expect(await screen.findByText("写跟进邮件")).toBeInTheDocument();
});
```

在 `frontend/test/ProfilePageOnboarding.test.tsx` 中新增：

```ts
it("does not render match threshold controls in the identity form", async () => {
  renderPage();

  expect(await screen.findByRole("heading", { name: "个人设置" })).toBeInTheDocument();
  expect(screen.queryByLabelText("匹配阈值")).not.toBeInTheDocument();
  expect(screen.queryByText("低于该分数时自动跳过")).not.toBeInTheDocument();
});
```

- [ ] **步骤 2：运行测试验证失败**

运行：

```powershell
cd frontend
npm run test -- getWorkspaceNextStep.test.ts WorkspacePageNextStep.test.tsx ProfilePageOnboarding.test.tsx
```

预期：FAIL。
当前失败点应包括：
- `WorkspaceTaskStatus` 不包含 `canceled`
- 工作区没有“继续联系 / 跟进邮件”动作
- 个人页仍显示匹配阈值输入

- [ ] **步骤 3：更新前端类型与 API**

在 `frontend/src/types/index.ts` 中更新：

```ts
export type WorkspaceTaskStatus =
  | "discovered"
  | "matched"
  | "review_required"
  | "approved"
  | "scheduled"
  | "sent"
  | "send_failed"
  | "reply_detected"
  | "canceled";

export type WorkspaceTaskCancellationReason =
  | "batch_stopped"
  | "user_canceled"
  | "superseded"
  | null;
```

给 `WorkspaceTaskSummaryDTO` 增加：

```ts
source: "batch" | "manual" | null;
parent_task_id: number | null;
cancellation_reason: WorkspaceTaskCancellationReason;
can_continue_manually: boolean;
can_write_follow_up: boolean;
```

在 `frontend/src/lib/api/emailTasksApi.ts` 新增：

```ts
export const continueManually = (taskId: number) =>
  apiFetch<WorkspaceThreadDTO>(`/api/email-tasks/${taskId}/continue-manually`, {
    method: "POST",
  });

export const startFollowUp = (taskId: number) =>
  apiFetch<WorkspaceThreadDTO>(`/api/email-tasks/${taskId}/start-follow-up`, {
    method: "POST",
  });
```

- [ ] **步骤 4：重构工作区下一步与按钮**

在 `frontend/src/features/workspace/client/getWorkspaceNextStep.ts` 中调整输入和规则：

```ts
export interface WorkspaceNextStepInput {
  status: WorkspaceTaskStatus;
  cancellationReason: WorkspaceTaskCancellationReason;
  hasDraft: boolean;
  hasPrimaryMaterial: boolean;
  canWriteFollowUp: boolean;
}

if (input.status === "canceled" && input.cancellationReason === "batch_stopped") {
  return { title: "作为单独联系继续" };
}

if (input.canWriteFollowUp) {
  return { title: "写跟进邮件" };
}
```

在 `frontend/src/pages/WorkspacePage.tsx` 中接入新 API：

```ts
const handleContinueManually = useCallback(() => {
  if (!currentTaskId) return;
  void runAction(() => continueManually(currentTaskId), "继续联系失败", "继续联系失败");
}, [currentTaskId, runAction]);

const handleStartFollowUp = useCallback(() => {
  if (!currentTaskId) return;
  void runAction(() => startFollowUp(currentTaskId), "创建跟进邮件失败", "创建跟进邮件失败");
}, [currentTaskId, runAction]);
```

在 `frontend/src/components/organisms/WorkspaceComposerDock.tsx` 中新增按钮分支：

```tsx
{currentTask.can_continue_manually ? (
  <button type="button" onClick={onContinueManually} className="ui-btn-primary">
    作为单独联系继续
  </button>
) : null}

{currentTask.can_write_follow_up ? (
  <button type="button" onClick={onStartFollowUp} className="ui-btn-secondary">
    写跟进邮件
  </button>
) : null}
```

并在 `ProfilePage.tsx` 里移除表单状态、payload 和 UI 中的 `match_threshold` 控件，保存时固定传 `null`：

```ts
match_threshold: null,
```

- [ ] **步骤 5：运行测试验证通过**

运行：

```powershell
cd frontend
npm run test -- getWorkspaceNextStep.test.ts WorkspacePageNextStep.test.tsx ProfilePageOnboarding.test.tsx
```

预期：PASS。

- [ ] **步骤 6：Commit**

```bash
git add frontend/src/types/index.ts \
  frontend/src/lib/api/emailTasksApi.ts \
  frontend/src/features/workspace/client/getWorkspaceNextStep.ts \
  frontend/src/pages/WorkspacePage.tsx \
  frontend/src/components/organisms/WorkspaceComposerDock.tsx \
  frontend/src/pages/ProfilePage.tsx \
  frontend/test/getWorkspaceNextStep.test.ts \
  frontend/test/WorkspacePageNextStep.test.tsx \
  frontend/test/ProfilePageOnboarding.test.tsx
git commit -m "feat(工作区): 支持继续联系与跟进邮件动作"
```

## 任务 5：更新文档并完成回归验证

**文件：**
- 修改：`docs/project_description.md`
- 修改：`docs/database_table_design.md`
- 测试：`backend/test/test_api_endpoints.py`
- 测试：`backend/test/test_database_schema.py`
- 测试：`frontend/test/professorDashboardStatus.test.ts`
- 测试：`frontend/test/HomePageOnboarding.test.tsx`
- 测试：`frontend/test/getWorkspaceNextStep.test.ts`
- 测试：`frontend/test/WorkspacePageNextStep.test.tsx`
- 测试：`frontend/test/ProfilePageOnboarding.test.tsx`

- [ ] **步骤 1：更新项目文档**

在 `docs/project_description.md` 的状态机章节改成：

```md
`discovered -> matched -> review_required -> approved -> scheduled -> sent / send_failed -> reply_detected`

补充说明：
- `matched`：已完成匹配分析，但不会因为低分自动跳过
- `canceled`：任务被明确取消，例如批量任务停止
- 匹配度只用于筛选、排序和解释，不参与执行裁决
```

在 `docs/database_table_design.md` 中为 `email_tasks` 补充：

```md
- `source`
- `parent_task_id`
- `cancellation_reason`

状态：
- `discovered`
- `matched`
- `review_required`
- `approved`
- `scheduled`
- `sent`
- `send_failed`
- `reply_detected`
- `canceled`
```

- [ ] **步骤 2：运行后端回归**

运行：

```powershell
cd backend
uv run python -m unittest test.test_api_endpoints test.test_database_schema
```

预期：PASS。

- [ ] **步骤 3：运行前端回归**

运行：

```powershell
cd ..\frontend
npm run test -- professorDashboardStatus.test.ts HomePageOnboarding.test.tsx getWorkspaceNextStep.test.ts WorkspacePageNextStep.test.tsx ProfilePageOnboarding.test.tsx
npm run lint
npm run build
```

预期：
- 所有 Vitest 用例 PASS
- `eslint .` exit 0
- `tsc -b && vite build` exit 0

- [ ] **步骤 4：Commit**

```bash
git add docs/project_description.md docs/database_table_design.md
git commit -m "docs(状态模型): 更新联系任务状态说明"
```

## 自检结果

- 规格覆盖度：已覆盖匹配度退场、`canceled` 状态、批量停止改取消、继续联系新建手动任务、首页状态重映射、工作区继续联系与跟进动作、个人页阈值入口移除、历史数据迁移、文档更新。
- 占位符扫描：计划中未使用“TODO”“后续补充”等占位描述；每个任务都给出了具体文件、测试、命令和关键代码片段。
- 类型一致性：计划统一使用 `canceled`、`source`、`parent_task_id`、`cancellation_reason`、`can_continue_manually`、`can_write_follow_up`；首页关系状态统一为 `not_contacted / preparing / ready_to_send / contacted / replied / needs_attention`。
