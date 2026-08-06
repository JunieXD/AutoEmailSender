# 移除 dry_run 并引入测试写信页 实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 删除 `dry_run / live` 全局发送模式，新增独立测试写信页，并用发送前风险确认替代原有模式开关。

**架构：** 后端把正式导师发送统一收敛为真实 SMTP 路径，同时新增独立的测试写信数据模型与 API，专门承载“发给自己”的测试链路。前端移除全局发送模式状态，在个人页新增测试写信入口，在新页面复用现有写信体验，并在工作区与批量创建页加入风险确认。

**技术栈：** FastAPI、SQLAlchemy、Alembic、React、Vite、Vitest、unittest

---

## 文件结构

### 后端

- 修改：`backend/app/models/app_setting.py`
  - 删除 `MailDeliveryMode` 和 `mail_delivery_mode` 字段，只保留时间戳设置表。
- 修改：`backend/app/models/email_task.py`
  - 删除 `delivery_mode` 字段，正式任务不再记录模式快照。
- 修改：`backend/app/models/email_log.py`
  - 删除 `delivery_mode` 字段，正式日志不再携带 `dry_run/live`。
- 修改：`backend/app/models/__init__.py`
  - 更新模型导出，移除 `MailDeliveryMode`，加入测试写信模型。
- 创建：`backend/app/models/test_compose_session.py`
  - 保存测试写信页的当前会话草稿、当前身份、当前 LLM。
- 创建：`backend/app/models/test_compose_message.py`
  - 保存测试发送历史和失败记录，与导师通信彻底隔离。
- 修改：`backend/app/schemas/workspace.py`
  - 删除 `mail_delivery_mode`、`delivery_mode` 响应字段。
- 修改：`backend/app/schemas/batch_task.py`
  - 删除 `dry_run_count`、`live_count`。
- 创建：`backend/app/schemas/test_compose.py`
  - 定义测试写信页读取、保存草稿、生成草稿、发送测试邮件的请求与响应。
- 修改：`backend/app/api/batch_tasks.py`
  - 删除模式统计，批量任务只返回真实进度。
- 修改：`backend/app/api/__init__.py`
  - 移除 `system_settings_router`，导出 `test_compose_router`。
- 修改：`backend/app/api/system_settings.py`
  - 删除整套路由文件与引用；如果项目约定需要保留文件，则文件内容改为空路由并从 `main.py` 解除注册。
- 创建：`backend/app/api/test_compose.py`
  - 提供测试写信页的读取、生成草稿、保存草稿、发送测试邮件接口。
- 修改：`backend/app/api/workspace_support.py`
  - 精简工作区响应，不再返回模式字段。
- 修改：`backend/app/services/system_settings.py`
  - 移除发送模式相关逻辑，保留或简化设置读取逻辑。
- 修改：`backend/app/services/task_runtime.py`
  - 正式任务批准发送后统一走真实 SMTP，不再读取系统模式。
- 创建：`backend/app/services/test_compose_runtime.py`
  - 负责测试写信会话加载、草稿生成、真实发给自己、历史保存。
- 修改：`backend/app/services/mail_runtime.py`
  - 抽出可覆盖收件人的邮件构建/发送入口，供测试写信复用。
- 修改：`backend/main.py`
  - 移除 `system_settings_router`，注册 `test_compose_router`。
- 创建：`backend/alembic/versions/9c3d5b4a7f21_remove_mail_delivery_mode_add_test_compose.py`
  - 删除旧字段，创建测试写信表。
- 修改：`backend/test/test_database_schema.py`
  - 更新 schema 断言，改为测试新表和删字段结果。
- 修改：`backend/test/test_api_endpoints.py`
  - 删除系统设置/模式相关断言，补测试写信接口和正式发送统一真实发送。

### 前端

- 修改：`frontend/src/App.tsx`
  - 注册测试写信页路由。
- 修改：`frontend/src/types/index.ts`
  - 删除 `MailDeliveryMode`、`SystemSettingsDTO.mail_delivery_mode`、批量模式统计字段，新增测试写信页 DTO。
- 修改：`frontend/src/context/SelectionContext.tsx`
  - 删除 `systemSettings` 与 `setMailDeliveryMode`，只保留身份和 LLM 全局上下文。
- 修改：`frontend/src/lib/api/systemSettings.ts`
  - 删除文件及其调用方；若保留文件，则改为不再导出任何请求。
- 创建：`frontend/src/lib/api/testComposeApi.ts`
  - 封装测试写信页所需的获取、保存、生成、发送接口。
- 修改：`frontend/src/components/organisms/TopNavBar.tsx`
  - 删除顶部发送模式显示与切换。
- 修改：`frontend/src/pages/ProfilePage.tsx`
  - 删除模式展示，在页面底部新增测试写信入口卡片。
- 创建：`frontend/src/pages/TestComposePage.tsx`
  - 实现独立测试写信页，支持草稿生成、编辑、附件、历史、立即发送测试邮件。
- 修改：`frontend/src/pages/WorkspacePage.tsx`
  - 删除模式依赖，加入单封立即发送/定时发送的风险确认。
- 修改：`frontend/src/components/organisms/WorkspaceComposerDock.tsx`
  - 删除模式相关文案，强化正式发送表达。
- 修改：`frontend/src/pages/CreateTaskPage.tsx`
  - 在提交批量任务前增加立即/定时发送风险确认。
- 修改：`frontend/src/pages/TasksPage.tsx`
  - 删除模式快照摘要区域。
- 修改：`frontend/src/pages/HomePage.tsx`
  - 删除首页顶部“模式”标签，首页只展示身份与模型上下文。
- 创建：`frontend/test/TestComposePage.test.tsx`
  - 覆盖测试写信页的主要交互。
- 修改：`frontend/test/HomePageOnboarding.test.tsx`
  - 更新首页 mock，移除 `systemSettings` 依赖与模式文案断言。
- 修改：`frontend/test/ProfilePageOnboarding.test.tsx`
  - 断言个人页出现测试写信入口，不再出现模式展示。
- 修改：`frontend/test/SelectionContextNotifications.test.tsx`
  - 删除 `getSystemSettings`/`setMailDeliveryMode` 相关测试。
- 修改：`frontend/test/WorkspacePageNextStep.test.tsx`
  - 更新工作区线程 mock，删除模式字段，并覆盖发送确认。
- 修改：`frontend/test/WorkspaceComposerDockCopy.test.tsx`
  - 更新发送区文案断言。

### 文档

- 修改：`docs/operations_runbook.md`
  - 用“测试写信页”替代“本地演练”操作说明。
- 修改：`docs/project_description.md`
  - 删除全局模式描述，补测试写信页定位。
- 修改：`docs/database_table_design.md`
  - 删除 `mail_delivery_mode` / `delivery_mode`，新增测试写信表说明。
- 修改：`docs/real_delivery_and_llm_design.md`
  - 移除 `dry_run/live` 设计，改为正式发送与测试写信并行结构。
- 修改：`docs/real_delivery_and_llm_implementation.md`
  - 更新接口与字段实现说明。

## 任务 1：删除发送模式数据库字段与系统设置接口

**文件：**
- 创建：`backend/alembic/versions/9c3d5b4a7f21_remove_mail_delivery_mode_add_test_compose.py`
- 修改：`backend/app/models/app_setting.py`
- 修改：`backend/app/models/email_task.py`
- 修改：`backend/app/models/email_log.py`
- 修改：`backend/app/models/__init__.py`
- 修改：`backend/app/api/__init__.py`
- 修改：`backend/app/schemas/workspace.py`
- 修改：`backend/app/schemas/batch_task.py`
- 修改：`backend/app/services/system_settings.py`
- 修改：`backend/app/api/system_settings.py`
- 修改：`backend/main.py`
- 测试：`backend/test/test_database_schema.py`
- 测试：`backend/test/test_api_endpoints.py`

- [ ] **步骤 1：先写失败的 schema 与接口测试**

```python
def test_runtime_tables_and_columns_are_created(self) -> None:
    settings_columns = self._get_columns("app_settings")
    task_columns = self._get_columns("email_tasks")
    log_columns = self._get_columns("email_logs")

    self.assertNotIn("mail_delivery_mode", settings_columns)
    self.assertNotIn("delivery_mode", task_columns)
    self.assertNotIn("delivery_mode", log_columns)
    self.assertIn("test_compose_sessions", table_names)
    self.assertIn("test_compose_messages", table_names)

def test_system_settings_endpoint_is_removed(self) -> None:
    response = self.client.get("/api/system-settings")
    self.assertEqual(response.status_code, 404)
```

- [ ] **步骤 2：运行测试确认失败**

运行：`cd backend && uv run python -m unittest test.test_database_schema.DatabaseSchemaTests.test_runtime_tables_and_columns_are_created test.test_api_endpoints.ApiEndpointTests.test_system_settings_endpoint_is_removed -v`

预期：FAIL，报错仍然存在 `mail_delivery_mode` / `delivery_mode` 字段，且 `/api/system-settings` 仍返回 200。

- [ ] **步骤 3：编写最小迁移和模型/路由清理代码**

```python
# backend/alembic/versions/9c3d5b4a7f21_remove_mail_delivery_mode_add_test_compose.py
def upgrade() -> None:
    with op.batch_alter_table("app_settings") as batch_op:
        batch_op.drop_column("mail_delivery_mode")
    with op.batch_alter_table("email_tasks") as batch_op:
        batch_op.drop_column("delivery_mode")
    with op.batch_alter_table("email_logs") as batch_op:
        batch_op.drop_column("delivery_mode")
    op.create_table(
        "test_compose_sessions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("identity_id", sa.Integer(), sa.ForeignKey("identity_profiles.id"), nullable=False),
        sa.Column("llm_profile_id", sa.Integer(), sa.ForeignKey("llm_profiles.id"), nullable=False),
        sa.Column("subject", sa.Text(), nullable=True),
        sa.Column("body_text", sa.Text(), nullable=False, server_default=""),
        sa.Column("body_html", sa.Text(), nullable=True),
        sa.Column("selected_material_ids", sa.JSON(), nullable=True),
    )
```

```python
# backend/main.py
from app.api import test_compose_router

app.include_router(test_compose_router)
# 删除 app.include_router(system_settings_router)
```

- [ ] **步骤 4：运行测试验证通过**

运行：`cd backend && uv run python -m unittest test.test_database_schema.DatabaseSchemaTests.test_runtime_tables_and_columns_are_created test.test_api_endpoints.ApiEndpointTests.test_system_settings_endpoint_is_removed -v`

预期：PASS，schema 断言通过，`/api/system-settings` 返回 404。

- [ ] **步骤 5：Commit**

```bash
git add backend/alembic/versions/9c3d5b4a7f21_remove_mail_delivery_mode_add_test_compose.py backend/app/models/app_setting.py backend/app/models/email_task.py backend/app/models/email_log.py backend/app/models/__init__.py backend/app/api/__init__.py backend/app/schemas/workspace.py backend/app/schemas/batch_task.py backend/app/services/system_settings.py backend/app/api/system_settings.py backend/main.py backend/test/test_database_schema.py backend/test/test_api_endpoints.py
git commit -m "refactor(backend): remove delivery mode settings"
```

## 任务 2：让正式导师发送始终走真实 SMTP，并清理批量模式统计

**文件：**
- 修改：`backend/app/services/task_runtime.py`
- 修改：`backend/app/api/workspace_support.py`
- 修改：`backend/app/api/batch_tasks.py`
- 修改：`backend/app/schemas/workspace.py`
- 修改：`backend/app/schemas/batch_task.py`
- 测试：`backend/test/test_api_endpoints.py`

- [ ] **步骤 1：先写失败的正式发送和批量列表测试**

```python
def test_manual_send_always_uses_real_smtp(self) -> None:
    with patch(
        "app.services.task_runtime.mail_runtime.send_email",
        AsyncMock(return_value=self._build_send_result("<msg-1@example.com>", {"smtp_host": "smtp.example.com"})),
    ) as mocked_send:
        response = self.client.post(
            f"/api/email-tasks/{task_id}/approve-and-send",
            json={"subject": "科研交流申请", "body_text": "老师您好", "body_html": None, "selected_material_ids": []},
        )

    self.assertEqual(response.status_code, 200)
    self.assertEqual(response.json()["current_task"]["status"], "sent")
    self.assertNotIn("delivery_mode", response.json()["current_task"])
    mocked_send.assert_awaited_once()

def test_batch_task_card_hides_delivery_mode_snapshot(self) -> None:
    payload = self.client.get("/api/batch-tasks").json()[0]
    self.assertNotIn("dry_run_count", payload)
    self.assertNotIn("live_count", payload)
```

- [ ] **步骤 2：运行测试确认失败**

运行：`cd backend && uv run python -m unittest test.test_api_endpoints.ApiEndpointTests.test_manual_send_always_uses_real_smtp test.test_api_endpoints.ApiEndpointTests.test_batch_task_card_hides_delivery_mode_snapshot -v`

预期：FAIL，响应仍包含 `delivery_mode`，批量卡片仍返回 `dry_run_count/live_count`。

- [ ] **步骤 3：编写最少实现代码**

```python
# backend/app/services/task_runtime.py
result = await mail_runtime.send_email(
    identity=task.identity,
    professor=task.professor,
    subject=subject,
    body_text=body_text,
    body_html=body_html,
    attachments=attachments,
)
task.last_rfc_message_id = result.message_id
```

```python
# backend/app/api/batch_tasks.py
return BatchTaskCardRead(
    id=task.id,
    name=task.name,
    sent_count=status_counter.get(EmailTaskStatus.SENT.value, 0),
    replied_count=status_counter.get(EmailTaskStatus.REPLY_DETECTED.value, 0),
)
```

- [ ] **步骤 4：运行测试验证通过**

运行：`cd backend && uv run python -m unittest test.test_api_endpoints.ApiEndpointTests.test_manual_send_always_uses_real_smtp test.test_api_endpoints.ApiEndpointTests.test_batch_task_card_hides_delivery_mode_snapshot -v`

预期：PASS，正式发送统一走 SMTP，批量任务卡片不再返回模式统计。

- [ ] **步骤 5：Commit**

```bash
git add backend/app/services/task_runtime.py backend/app/api/workspace_support.py backend/app/api/batch_tasks.py backend/app/schemas/workspace.py backend/app/schemas/batch_task.py backend/test/test_api_endpoints.py
git commit -m "refactor(backend): make task sending always live"
```

## 任务 3：实现测试写信页后端数据模型与 API

**文件：**
- 创建：`backend/app/models/test_compose_session.py`
- 创建：`backend/app/models/test_compose_message.py`
- 创建：`backend/app/schemas/test_compose.py`
- 创建：`backend/app/services/test_compose_runtime.py`
- 创建：`backend/app/api/test_compose.py`
- 修改：`backend/app/models/__init__.py`
- 修改：`backend/app/services/mail_runtime.py`
- 修改：`backend/main.py`
- 测试：`backend/test/test_api_endpoints.py`

- [ ] **步骤 1：先写失败的测试写信 API 测试**

```python
def test_test_compose_page_can_generate_and_send_to_self(self) -> None:
    identity_id = self._create_identity(with_imap=False)
    llm_id = self._create_llm()

    with (
        patch("app.services.test_compose_runtime.llm_runtime.generate_draft_content", AsyncMock(return_value=self._build_draft_generation_result(subject="测试主题", body_text="测试正文", body_html="<p>测试正文</p>"))),
        patch("app.services.test_compose_runtime.mail_runtime.send_email_to_recipient", AsyncMock(return_value=self._build_send_result("<self-test@example.com>", {"to": "sender@example.com"}))),
    ):
        draft_response = self.client.post(f"/api/test-compose/{identity_id}/{llm_id}/generate-draft")
        send_response = self.client.post(
            f"/api/test-compose/{identity_id}/{llm_id}/send",
            json={"subject": "测试主题", "body_text": "测试正文", "body_html": "<p>测试正文</p>", "selected_material_ids": []},
        )

    self.assertEqual(draft_response.status_code, 200)
    self.assertEqual(send_response.status_code, 200)
    self.assertEqual(send_response.json()["history"][0]["recipient_email"], "sender@example.com")
    self.assertEqual(send_response.json()["history"][0]["status"], "sent")
```

- [ ] **步骤 2：运行测试确认失败**

运行：`cd backend && uv run python -m unittest test.test_api_endpoints.ApiEndpointTests.test_test_compose_page_can_generate_and_send_to_self -v`

预期：FAIL，报错 `/api/test-compose/{identity_id}/{llm_profile_id}/generate-draft` 或 `/api/test-compose/{identity_id}/{llm_profile_id}/send` 路由不存在，或 `send_email_to_recipient` 未定义。

- [ ] **步骤 3：编写最少实现代码**

```python
# backend/app/services/mail_runtime.py
async def send_email_to_recipient(
    *,
    identity: IdentityProfile,
    recipient_name: str,
    recipient_email: str,
    subject: str,
    body_text: str,
    body_html: str | None,
    attachments: list[MailAttachment],
) -> SendMailResult:
    message = build_email_message_for_recipient(
        identity=identity,
        recipient_name=recipient_name,
        recipient_email=recipient_email,
        subject=subject,
        body_text=body_text,
        body_html=body_html,
        attachments=attachments,
    )
    await asyncio.to_thread(_send_email_sync, identity, message)
    return SendMailResult(message_id=message["Message-ID"], provider_payload={"to": recipient_email})
```

```python
# backend/app/api/test_compose.py
@router.post("/{identity_id}/{llm_profile_id}/send", response_model=TestComposeThreadRead)
async def send_test_compose(
    identity_id: int,
    llm_profile_id: int,
    payload: TestComposeMessageSendRequest,
    session: AsyncSession = Depends(get_async_session),
) -> TestComposeThreadRead:
    return await send_test_compose_message(
        session,
        identity_id=identity_id,
        llm_profile_id=llm_profile_id,
        payload=payload,
    )
```

- [ ] **步骤 4：运行测试验证通过**

运行：`cd backend && uv run python -m unittest test.test_api_endpoints.ApiEndpointTests.test_test_compose_page_can_generate_and_send_to_self -v`

预期：PASS，测试写信可生成草稿、真实发给自己，并写入独立历史。

- [ ] **步骤 5：Commit**

```bash
git add backend/app/models/test_compose_session.py backend/app/models/test_compose_message.py backend/app/schemas/test_compose.py backend/app/services/test_compose_runtime.py backend/app/api/test_compose.py backend/app/services/mail_runtime.py backend/app/models/__init__.py backend/main.py backend/test/test_api_endpoints.py
git commit -m "feat(backend): add test compose api"
```

## 任务 4：清理前端全局发送模式状态并更新 API 类型

**文件：**
- 修改：`frontend/src/types/index.ts`
- 修改：`frontend/src/context/SelectionContext.tsx`
- 修改：`frontend/src/components/organisms/TopNavBar.tsx`
- 修改：`frontend/src/pages/HomePage.tsx`
- 修改：`frontend/src/pages/TasksPage.tsx`
- 修改：`frontend/src/lib/api/systemSettings.ts`
- 测试：`frontend/test/HomePageOnboarding.test.tsx`
- 测试：`frontend/test/SelectionContextNotifications.test.tsx`
- 测试：`frontend/test/WorkspaceComposerDockCopy.test.tsx`

- [ ] **步骤 1：先写失败的前端上下文与顶部栏测试**

```tsx
it("does not request system settings during bootstrap", async () => {
  render(<SelectionProvider><Harness /></SelectionProvider>);
  await waitFor(() => expect(listIdentities).toHaveBeenCalled());
  expect(getSystemSettings).not.toHaveBeenCalled();
});

it("does not render delivery mode badge in top nav", () => {
  render(<TopNavBar />);
  expect(screen.queryByText("当前发送状态")).not.toBeInTheDocument();
  expect(screen.queryByText("本地演练")).not.toBeInTheDocument();
});

it("does not render a mode chip on the home dashboard", async () => {
  render(<HomePage />);
  expect(await screen.findByRole("heading", { name: "导师看板" })).toBeInTheDocument();
  expect(screen.queryByText(/模式：/)).not.toBeInTheDocument();
});
```

- [ ] **步骤 2：运行测试确认失败**

运行：`cd frontend && npm run test -- HomePageOnboarding.test.tsx SelectionContextNotifications.test.tsx WorkspaceComposerDockCopy.test.tsx`

预期：FAIL，`SelectionContext` 仍会调用 `getSystemSettings`，顶部栏或发送区仍出现模式文案。

- [ ] **步骤 3：编写最少实现代码**

```tsx
// frontend/src/context/SelectionContext.tsx
const [identityData, llmData] = await Promise.all([
  listIdentities(),
  listLLMProfiles(),
]);
setIdentities(identityData);
setLlmProfiles(llmData);
```

```tsx
// frontend/src/components/organisms/TopNavBar.tsx
<div className="flex flex-wrap items-center justify-end gap-3">
  <TopBarSelectMenu
    placeholder="身份"
    value={selectedIdentityId}
    options={identityOptions}
    onChange={(value) => setSelectedIdentityId(Number(value))}
  />
  <TopBarSelectMenu
    placeholder="模型"
    value={selectedLlmProfileId}
    options={llmOptions}
    onChange={(value) => setSelectedLlmProfileId(Number(value))}
  />
</div>
```

```tsx
// frontend/src/pages/HomePage.tsx
<div className="mt-3 flex flex-wrap gap-2 text-xs text-stone-600">
  <span className="rounded-full border border-stone-200 bg-white px-3 py-1.5">
    身份：{selectedIdentity.name}
  </span>
  <span className="rounded-full border border-stone-200 bg-white px-3 py-1.5">
    模型：{selectedLlmProfile.name}
  </span>
</div>
```

- [ ] **步骤 4：运行测试验证通过**

运行：`cd frontend && npm run test -- HomePageOnboarding.test.tsx SelectionContextNotifications.test.tsx WorkspaceComposerDockCopy.test.tsx`

预期：PASS，全局模式状态被移除，顶部栏和任务页不再显示模式文案。

- [ ] **步骤 5：Commit**

```bash
git add frontend/src/types/index.ts frontend/src/context/SelectionContext.tsx frontend/src/components/organisms/TopNavBar.tsx frontend/src/pages/HomePage.tsx frontend/src/pages/TasksPage.tsx frontend/src/lib/api/systemSettings.ts frontend/test/HomePageOnboarding.test.tsx frontend/test/SelectionContextNotifications.test.tsx frontend/test/WorkspaceComposerDockCopy.test.tsx
git commit -m "refactor(frontend): remove delivery mode state"
```

## 任务 5：实现个人页入口与独立测试写信页

**文件：**
- 修改：`frontend/src/App.tsx`
- 修改：`frontend/src/pages/ProfilePage.tsx`
- 创建：`frontend/src/lib/api/testComposeApi.ts`
- 创建：`frontend/src/pages/TestComposePage.tsx`
- 测试：`frontend/test/ProfilePageOnboarding.test.tsx`
- 测试：`frontend/test/TestComposePage.test.tsx`

- [ ] **步骤 1：先写失败的入口与页面交互测试**

```tsx
it("shows a test compose entry at the bottom of profile page", async () => {
  render(<ProfilePage />);
  expect(await screen.findByRole("link", { name: "进入测试写信页" })).toHaveAttribute("href", "/test-compose");
});

it("loads draft helpers and send history on the test compose page", async () => {
  getTestComposeThread.mockResolvedValue({
    identity: { id: 1, name: "博士申请邮箱", email_address: "sender@example.com" },
    llm_profile: { id: 1, name: "GPT-5.4", provider: "openai", model_name: "gpt-5.4" },
    draft: { subject: "测试主题", body_text: "测试正文", body_html: "<p>测试正文</p>", selected_material_ids: [] },
    history: [{ id: 1, status: "sent", recipient_email: "sender@example.com", subject: "测试主题" }],
    material_options: [],
  });
  render(<TestComposePage />);
  expect(await screen.findByDisplayValue("测试主题")).toBeInTheDocument();
  expect(screen.getByText("sender@example.com")).toBeInTheDocument();
});
```

- [ ] **步骤 2：运行测试确认失败**

运行：`cd frontend && npm run test -- ProfilePageOnboarding.test.tsx TestComposePage.test.tsx`

预期：FAIL，`/test-compose` 路由、入口卡片和 API 封装尚不存在。

- [ ] **步骤 3：编写最少实现代码**

```tsx
// frontend/src/App.tsx
<Route path="/test-compose" element={<TestComposePage />} />
```

```tsx
// frontend/src/pages/ProfilePage.tsx
<section className="rounded-3xl border border-stone-200 bg-white p-6 shadow-sm">
  <h2 className="text-xl font-semibold text-stone-900">测试写信</h2>
  <p className="mt-2 text-sm leading-6 text-stone-600">
    配置完成后，先给自己发一封测试邮件，确认模板、附件和 SMTP 都正常。
  </p>
  <Link to="/test-compose" className="ui-btn-primary mt-4">进入测试写信页</Link>
</section>
```

- [ ] **步骤 4：运行测试验证通过**

运行：`cd frontend && npm run test -- ProfilePageOnboarding.test.tsx TestComposePage.test.tsx`

预期：PASS，个人页出现测试写信入口，新页面能读取当前草稿和历史。

- [ ] **步骤 5：Commit**

```bash
git add frontend/src/App.tsx frontend/src/pages/ProfilePage.tsx frontend/src/lib/api/testComposeApi.ts frontend/src/pages/TestComposePage.tsx frontend/test/ProfilePageOnboarding.test.tsx frontend/test/TestComposePage.test.tsx
git commit -m "feat(frontend): add test compose page"
```

## 任务 6：为正式发送与批量创建加入风险确认

**文件：**
- 修改：`frontend/src/pages/WorkspacePage.tsx`
- 修改：`frontend/src/components/organisms/WorkspaceComposerDock.tsx`
- 修改：`frontend/src/pages/CreateTaskPage.tsx`
- 修改：`frontend/src/lib/useConfirmDialog.tsx`
- 测试：`frontend/test/WorkspacePageNextStep.test.tsx`
- 测试：`frontend/test/CreateTaskPageCopy.test.tsx`

- [ ] **步骤 1：先写失败的发送确认测试**

```tsx
it("asks for confirmation before sending a real email now", async () => {
  render(<WorkspacePage />);
  await user.click(await screen.findByRole("button", { name: "立即发送" }));
  expect(await screen.findByText("确认立即发送这封真实邮件？")).toBeInTheDocument();
});

it("asks for stronger confirmation before creating a scheduled batch task", async () => {
  render(<CreateTaskPage />);
  await user.selectOptions(screen.getByLabelText("发送方式"), "scheduled");
  await user.click(screen.getByRole("button", { name: "创建任务" }));
  expect(await screen.findByText("这会创建一个自动定时真实发送的批量任务。")).toBeInTheDocument();
});
```

- [ ] **步骤 2：运行测试确认失败**

运行：`cd frontend && npm run test -- WorkspacePageNextStep.test.tsx CreateTaskPageCopy.test.tsx`

预期：FAIL，点击发送或创建任务后不会出现确认弹窗。

- [ ] **步骤 3：编写最少实现代码**

```tsx
// frontend/src/pages/WorkspacePage.tsx
const { confirm, dialog: confirmDialog } = useConfirmDialog();

const confirmed = await confirm({
  title: "确认立即发送这封真实邮件？",
  description: `将发送给 ${thread.professor.email ?? "当前导师邮箱"}，并附带 ${selectedMaterialIds.length} 份附件。`,
  confirmLabel: "确认发送",
  cancelLabel: "再检查一下",
  tone: "danger",
});
if (!confirmed) return;
```

```tsx
// frontend/src/pages/CreateTaskPage.tsx
const confirmed = await confirm({
  title: scheduleType === "scheduled" ? "确认创建定时批量发送任务？" : "确认创建真实发送任务？",
  description: scheduleType === "scheduled"
    ? "这会创建一个自动定时真实发送的批量任务。"
    : "后续进入工作区审批后，发送将是真实发给导师。",
  confirmLabel: "继续创建",
});
if (!confirmed) return;
```

- [ ] **步骤 4：运行测试验证通过**

运行：`cd frontend && npm run test -- WorkspacePageNextStep.test.tsx CreateTaskPageCopy.test.tsx`

预期：PASS，正式发送和批量创建前都会弹出对应风险确认。

- [ ] **步骤 5：Commit**

```bash
git add frontend/src/pages/WorkspacePage.tsx frontend/src/components/organisms/WorkspaceComposerDock.tsx frontend/src/pages/CreateTaskPage.tsx frontend/src/lib/useConfirmDialog.tsx frontend/test/WorkspacePageNextStep.test.tsx frontend/test/CreateTaskPageCopy.test.tsx
git commit -m "feat(frontend): add send risk confirmations"
```

## 任务 7：更新文档并跑全量验证

**文件：**
- 修改：`docs/operations_runbook.md`
- 修改：`docs/project_description.md`
- 修改：`docs/database_table_design.md`
- 修改：`docs/real_delivery_and_llm_design.md`
- 修改：`docs/real_delivery_and_llm_implementation.md`

- [ ] **步骤 1：先写失败的文档一致性检查**

```bash
rg -n "dry_run|mail_delivery_mode|delivery_mode|本地演练|发送模式快照" docs backend/app frontend/src
```

预期：当前仍能在文档和代码中搜到多处旧概念。

- [ ] **步骤 2：运行检查确认存在旧术语**

运行：`rg -n "dry_run|mail_delivery_mode|delivery_mode|本地演练|发送模式快照" docs backend/app frontend/src`

预期：输出包含 `docs/operations_runbook.md`、`docs/project_description.md`、`frontend/src/pages/TasksPage.tsx` 等旧引用。

- [ ] **步骤 3：更新文档并收口验证命令**

```md
1. 在个人页完成发件身份配置。
2. 点击“进入测试写信页”，先给自己发一封测试邮件。
3. 确认主题、正文、HTML 与附件效果都正常。
4. 再进入工作区处理真实导师邮件。
```

```bash
cd backend && uv run python -m unittest
cd frontend && npm run test
cd frontend && npm run lint
cd frontend && npm run build
```

- [ ] **步骤 4：运行全量验证**

运行：

```bash
cd backend && uv run python -m unittest
cd frontend && npm run test
cd frontend && npm run lint
cd frontend && npm run build
```

预期：全部 PASS，`rg -n "dry_run|mail_delivery_mode|delivery_mode|本地演练|发送模式快照" docs backend/app frontend/src` 只剩迁移脚本或历史设计文档中明确保留的迁移说明。

- [ ] **步骤 5：Commit**

```bash
git add docs/operations_runbook.md docs/project_description.md docs/database_table_design.md docs/real_delivery_and_llm_design.md docs/real_delivery_and_llm_implementation.md
git commit -m "docs: replace dry run with test compose flow"
```
