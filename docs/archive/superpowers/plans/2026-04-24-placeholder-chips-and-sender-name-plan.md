# 占位符标签与发件人姓名拆分实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 将邮件模板占位符改为编辑器内联标签，同时拆分身份的配置名称与发件人姓名，并保证测试写信发送前会实际渲染占位符。

**架构：** 后端新增 `profile_name` 与 `sender_name` 字段，并保留旧 `name` 作为兼容字段。编辑器新增 Tiptap inline atom 节点，编辑态显示中文标签，对外仍序列化为 `{{name}}` 等模板变量。测试写信发送前使用测试上下文渲染 subject、body text、body html，再清洗和发送。

**技术栈：** FastAPI、SQLAlchemy、Alembic、Pydantic、unittest、React、Tiptap、Vitest、Testing Library、Tailwind CSS。

---

## 实施注意事项

- 当前工作区已有未提交改动，实施时只改本计划列出的文件，不要回滚或格式化无关文件。
- 所有文件保持 UTF-8 编码。
- Python 依赖与测试使用 `uv`。
- 前端路径基于 `frontend/`，后端路径基于 `backend/`。
- 计划中的提交步骤是逻辑提交建议；如果工作区仍有用户未提交改动，提交前先确认 stage 内容只包含本任务文件。

## 文件结构

- 创建：`backend/alembic/versions/2f6a9d8c1e20_add_identity_profile_and_sender_names.py`
  - 给 `identity_profiles` 增加 `profile_name` 和 `sender_name`，并迁移旧 `name`。
- 修改：`backend/app/models/identity_profile.py`
  - 增加 SQLAlchemy 字段。
- 修改：`backend/app/schemas/identity.py`
  - 请求兼容旧 `name`，响应新增 `profile_name`、`sender_name`，并继续输出 `name`。
- 修改：`backend/app/api/identity_serializers.py`
  - 统一输出兼容字段。
- 修改：`backend/app/api/identities.py`
  - 归一化创建/更新 payload，保证 3 个名称字段同步。
- 修改：`backend/app/schemas/test_compose.py`、`backend/app/schemas/workspace.py`
  - 在测试写信和工作区身份 DTO 中暴露新字段。
- 修改：`backend/app/services/outreach_templates.py`
  - `{{sender_name}}` 使用发件人姓名；导出测试渲染上下文辅助函数。
- 修改：`backend/app/services/mail_runtime.py`
  - 邮件 `From` 使用发件人姓名。
- 修改：`backend/app/services/test_compose_runtime.py`
  - 生成草稿和直接发送都使用「测试收件人」上下文。
- 修改：`backend/test/test_api_endpoints.py`、`backend/test/test_outreach_templates.py`
  - 增加身份字段、测试发送渲染、正式模板回归测试。
- 创建：`frontend/src/lib/templatePlaceholders.ts`
  - 前端占位符定义、HTML 预处理、HTML 序列化。
- 创建：`frontend/src/components/molecules/tiptap/TemplatePlaceholder.ts`
  - Tiptap 占位符 inline node。
- 修改：`frontend/src/components/molecules/EmailTemplateEditor.tsx`
  - 注册占位符节点，新增占位符菜单，加载/输出时转换。
- 修改：`frontend/src/index.css`
  - 占位符标签样式。
- 修改：`frontend/src/types/index.ts`
  - 身份 DTO 和 payload 增加 `profile_name`、`sender_name`。
- 修改：`frontend/src/pages/ProfilePage.tsx`
  - 表单拆成「配置名称」和「发件人姓名」。
- 修改：`frontend/src/components/organisms/TopNavBar.tsx`
  - 顶部身份选择器使用配置名称。
- 修改：`frontend/src/pages/TestComposePage.tsx`
  - 显示测试占位符提示。
- 修改：`frontend/test/EmailTemplateEditor.test.tsx`、`frontend/test/ProfilePageOnboarding.test.tsx`、`frontend/test/TestComposePage.test.tsx`、`frontend/test/SelectionContextNotifications.test.tsx`
  - 覆盖编辑器、身份表单、测试写信和全局选择器。

### 任务 1：身份字段拆分与 API 兼容

**文件：**
- 创建：`backend/alembic/versions/2f6a9d8c1e20_add_identity_profile_and_sender_names.py`
- 修改：`backend/app/models/identity_profile.py`
- 修改：`backend/app/schemas/identity.py`
- 修改：`backend/app/api/identity_serializers.py`
- 修改：`backend/app/api/identities.py`
- 测试：`backend/test/test_api_endpoints.py`

- [ ] **步骤 1：编写失败的 API 测试**

在 `backend/test/test_api_endpoints.py` 的 `test_identity_and_llm_connectivity_endpoints` 附近新增测试：

```python
def test_identity_accepts_profile_name_and_sender_name_with_name_compatibility(self) -> None:
    payload = self._build_identity_payload(
        with_imap=False,
        outreach_template_subject="申请与{{name}}老师交流",
        outreach_template_body_text="老师您好，我是{{sender_name}}。",
    )
    payload["name"] = "兼容配置名称"
    payload["profile_name"] = "博士申请配置"
    payload["sender_name"] = "王同学"
    payload["email_address"] = "sender-profile-name@example.com"
    payload["smtp_username"] = "sender-profile-name@example.com"

    response = self.client.post("/api/identities", json=payload)

    self.assertEqual(response.status_code, 201, msg=response.text)
    body = response.json()
    self.assertEqual(body["name"], "博士申请配置")
    self.assertEqual(body["profile_name"], "博士申请配置")
    self.assertEqual(body["sender_name"], "王同学")

    list_payload = self.client.get("/api/identities").json()
    created = next(item for item in list_payload if item["id"] == body["id"])
    self.assertEqual(created["name"], "博士申请配置")
    self.assertEqual(created["profile_name"], "博士申请配置")
    self.assertEqual(created["sender_name"], "王同学")

def test_identity_legacy_name_populates_profile_and_sender_name(self) -> None:
    payload = self._build_identity_payload(
        with_imap=False,
        outreach_template_subject="申请与{{name}}老师交流",
        outreach_template_body_text="老师您好，我是{{sender_name}}。",
    )
    payload["email_address"] = "legacy-name@example.com"
    payload["smtp_username"] = "legacy-name@example.com"
    payload.pop("profile_name", None)
    payload.pop("sender_name", None)
    payload["name"] = "旧身份名称"

    response = self.client.post("/api/identities", json=payload)

    self.assertEqual(response.status_code, 201, msg=response.text)
    body = response.json()
    self.assertEqual(body["name"], "旧身份名称")
    self.assertEqual(body["profile_name"], "旧身份名称")
    self.assertEqual(body["sender_name"], "旧身份名称")
```

- [ ] **步骤 2：运行测试验证失败**

运行：

```powershell
cd backend
uv run python -m unittest test.test_api_endpoints.ApiEndpointTests.test_identity_accepts_profile_name_and_sender_name_with_name_compatibility test.test_api_endpoints.ApiEndpointTests.test_identity_legacy_name_populates_profile_and_sender_name
```

预期：失败，响应 JSON 中没有 `profile_name` 或 `sender_name`。

- [ ] **步骤 3：新增 Alembic 迁移**

创建 `backend/alembic/versions/2f6a9d8c1e20_add_identity_profile_and_sender_names.py`：

```python
"""add identity profile and sender names

Revision ID: 2f6a9d8c1e20
Revises: f14c0e8d3b7a
Create Date: 2026-04-24 00:00:00.000000
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "2f6a9d8c1e20"
down_revision = "f14c0e8d3b7a"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("identity_profiles") as batch_op:
        batch_op.add_column(sa.Column("profile_name", sa.String(length=100), nullable=True))
        batch_op.add_column(sa.Column("sender_name", sa.String(length=100), nullable=True))

    op.execute(
        """
        UPDATE identity_profiles
        SET profile_name = COALESCE(NULLIF(profile_name, ''), name),
            sender_name = COALESCE(NULLIF(sender_name, ''), name)
        """
    )

    with op.batch_alter_table("identity_profiles") as batch_op:
        batch_op.alter_column("profile_name", existing_type=sa.String(length=100), nullable=False)
        batch_op.alter_column("sender_name", existing_type=sa.String(length=100), nullable=False)


def downgrade() -> None:
    with op.batch_alter_table("identity_profiles") as batch_op:
        batch_op.drop_column("sender_name")
        batch_op.drop_column("profile_name")
```

- [ ] **步骤 4：更新后端模型和 schema**

在 `backend/app/models/identity_profile.py` 的 `name` 后新增：

```python
profile_name: Mapped[str] = mapped_column(String(100), nullable=False)
sender_name: Mapped[str] = mapped_column(String(100), nullable=False)
```

在 `backend/app/schemas/identity.py` 中调整身份 schema：

```python
class IdentityProfileBase(BaseModel):
    name: str | None = None
    profile_name: str | None = None
    sender_name: str | None = None
    email_address: str
    smtp_host: str
    smtp_port: int = 465
    smtp_username: str
    smtp_password: str
    imap_host: str | None = None
    imap_port: int | None = None
    imap_username: str | None = None
    imap_password: str | None = None
    default_language: str = "zh-CN"
    outreach_generation_mode: str = OutreachGenerationMode.LLM.value
    outreach_template_subject: str | None = None
    outreach_template_body_text: str | None = None
    outreach_template_body_html: str | None = None
    match_threshold: int | None = None
    daily_send_limit: int | None = None
    send_interval_min: int | None = None
    send_interval_max: int | None = None
    same_domain_cooldown_minutes: int | None = None
    is_default: bool = False

class IdentityProfileRead(IdentityProfileBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    profile_name: str
    sender_name: str
    current_primary_material_id: int | None
    current_primary_material: IdentityMaterialRead | None
    materials: list[IdentityMaterialRead]
    created_at: datetime
    updated_at: datetime
```

- [ ] **步骤 5：更新序列化和 payload 归一化**

在 `backend/app/api/identity_serializers.py` 中使用兼容值：

```python
profile_name = identity.profile_name or identity.name
sender_name = identity.sender_name or profile_name
return IdentityProfileRead(
    id=identity.id,
    name=profile_name,
    profile_name=profile_name,
    sender_name=sender_name,
    email_address=identity.email_address,
    ...
)
```

在 `backend/app/api/identities.py` 的 `_normalize_identity_payload()` 中加入：

```python
profile_name = _clean_required_text(data.get("profile_name") or data.get("name"))
sender_name = _clean_required_text(data.get("sender_name") or profile_name)
data["profile_name"] = profile_name
data["sender_name"] = sender_name
data["name"] = profile_name
```

并在文件底部新增：

```python
def _clean_required_text(value: object) -> str:
    cleaned = str(value or "").strip()
    if not cleaned:
        raise HTTPException(status_code=400, detail="请填写配置名称和发件人姓名")
    return cleaned
```

- [ ] **步骤 6：运行测试验证通过**

运行：

```powershell
cd backend
uv run python -m unittest test.test_api_endpoints.ApiEndpointTests.test_identity_accepts_profile_name_and_sender_name_with_name_compatibility test.test_api_endpoints.ApiEndpointTests.test_identity_legacy_name_populates_profile_and_sender_name
```

预期：2 个测试通过。

- [ ] **步骤 7：Commit**

```powershell
git add backend/alembic/versions/2f6a9d8c1e20_add_identity_profile_and_sender_names.py backend/app/models/identity_profile.py backend/app/schemas/identity.py backend/app/api/identity_serializers.py backend/app/api/identities.py backend/test/test_api_endpoints.py
git commit -m "feat(身份): 拆分配置名称和发件人姓名"
```

### 任务 2：后端占位符渲染与测试写信发送

**文件：**
- 修改：`backend/app/services/outreach_templates.py`
- 修改：`backend/app/services/mail_runtime.py`
- 修改：`backend/app/services/test_compose_runtime.py`
- 修改：`backend/app/schemas/test_compose.py`
- 修改：`backend/app/schemas/workspace.py`
- 测试：`backend/test/test_outreach_templates.py`
- 测试：`backend/test/test_api_endpoints.py`

- [ ] **步骤 1：编写失败的后端测试**

在 `backend/test/test_outreach_templates.py` 增加：

```python
def test_template_context_uses_sender_name_field(self) -> None:
    from app.models import IdentityProfile, Professor
    from app.services.outreach_templates import render_outreach_template

    identity = IdentityProfile(
        name="内部配置",
        profile_name="博士申请配置",
        sender_name="王同学",
        email_address="sender@example.com",
        smtp_host="smtp.example.com",
        smtp_username="sender@example.com",
        smtp_password="secret",
    )
    professor = Professor(name="李老师", email="li@example.edu")

    rendered = render_outreach_template(
        identity,
        professor,
        subject_template="申请与{{name}}老师交流",
        body_text_template="{{name}}老师您好，我是{{sender_name}}。",
        body_html_template="<p>{{name}}老师您好，我是{{sender_name}}。</p>",
    )

    self.assertEqual(rendered.subject, "申请与李老师老师交流")
    self.assertIn("我是王同学", rendered.body_text)
    self.assertIn("我是王同学", rendered.body_html)
```

在 `backend/test/test_api_endpoints.py` 增加：

```python
def test_test_compose_send_renders_placeholders_before_sending(self) -> None:
    identity_id = self._create_identity(with_imap=False)
    llm_id = self._create_llm()

    update_payload = self._build_identity_payload(
        with_imap=False,
        outreach_template_subject="测试给{{name}}",
        outreach_template_body_text="{{name}}您好，我是{{sender_name}}。",
        outreach_template_body_html="<p>{{name}}您好，我是{{sender_name}}。</p>",
    )
    update_payload["profile_name"] = "测试配置"
    update_payload["sender_name"] = "王同学"
    self.client.put(f"/api/identities/{identity_id}", json=update_payload)

    with patch(
        "app.services.test_compose_runtime.mail_runtime.send_email_to_recipient",
        AsyncMock(return_value=self._build_send_result(message_id="<test-render@example.com>", provider_payload={})),
    ) as mocked_send:
        response = self.client.post(
            f"/api/test-compose/{identity_id}/{llm_id}/send",
            json={
                "subject": "发送给{{name}}",
                "body_text": "{{name}}您好，我是{{sender_name}}，研究方向：{{research_direction}}。",
                "body_html": "<p>{{name}}您好，我是{{sender_name}}，研究方向：{{research_direction}}。</p>",
                "selected_material_ids": [],
            },
        )

    self.assertEqual(response.status_code, 200, msg=response.text)
    kwargs = mocked_send.await_args.kwargs
    self.assertEqual(kwargs["recipient_name"], "测试收件人")
    self.assertEqual(kwargs["subject"], "发送给测试收件人")
    self.assertIn("测试收件人您好", kwargs["body_text"])
    self.assertIn("我是王同学", kwargs["body_text"])
    self.assertIn("测试研究方向", kwargs["body_text"])
    self.assertNotIn("{{name}}", kwargs["body_html"])

    history = response.json()["history"][0]
    self.assertEqual(history["subject"], "发送给测试收件人")
    self.assertIn("测试收件人您好", history["content"])
    self.assertNotIn("{{sender_name}}", history["content_html"])
```

- [ ] **步骤 2：运行测试验证失败**

运行：

```powershell
cd backend
uv run python -m unittest test.test_outreach_templates.OutreachTemplateTests.test_template_context_uses_sender_name_field test.test_api_endpoints.ApiEndpointTests.test_test_compose_send_renders_placeholders_before_sending
```

预期：失败，`sender_name` 未参与模板上下文，测试发送仍保留原始占位符。

- [ ] **步骤 3：实现后端渲染辅助函数**

在 `backend/app/services/outreach_templates.py` 中新增：

```python
TEST_RECIPIENT_NAME = "测试收件人"

def get_identity_sender_name(identity: IdentityProfile) -> str:
    return (
        getattr(identity, "sender_name", None)
        or getattr(identity, "profile_name", None)
        or identity.name
        or ""
    )

def build_test_compose_template_context(identity: IdentityProfile) -> dict[str, str]:
    return {
        "name": TEST_RECIPIENT_NAME,
        "email": identity.email_address or "",
        "title": TEST_RECIPIENT_NAME,
        "university": "测试学校",
        "school": "测试学院",
        "department": "测试院系",
        "research_direction": "测试研究方向",
        "sender_name": get_identity_sender_name(identity),
        "sender_email": identity.email_address or "",
    }

def render_template_with_context(value: str | None, context: dict[str, str]) -> str:
    return render_template_string(value or "", context)
```

并修改 `build_template_context()`：

```python
"sender_name": get_identity_sender_name(identity),
```

- [ ] **步骤 4：更新邮件 From 和测试写信发送**

在 `backend/app/services/mail_runtime.py` 的 `build_email_message()` 中修改：

```python
from app.services.outreach_templates import get_identity_sender_name

message["From"] = formataddr((get_identity_sender_name(identity), identity.email_address))
```

在 `backend/app/services/test_compose_runtime.py` 中导入并使用：

```python
from app.services.outreach_templates import (
    OUTREACH_GENERATION_MODE_TEMPLATE,
    TEST_RECIPIENT_NAME,
    build_test_compose_template_context,
    get_outreach_template_defaults_validation_error,
    render_outreach_template,
    render_template_with_context,
    resolve_outreach_template_config,
)
```

修改 `_build_self_recipient_professor()`：

```python
return Professor(
    name=TEST_RECIPIENT_NAME,
    email=identity.email_address,
    title=TEST_RECIPIENT_NAME,
    university="测试学校",
    school="测试学院",
    department="测试院系",
    research_direction="测试研究方向",
    recent_papers=[],
)
```

在 `send_test_compose_message()` 中，清洗前先渲染：

```python
context = build_test_compose_template_context(identity)
subject = render_template_with_context(payload.subject, context).strip()
rendered_body_text = render_template_with_context(payload.body_text, context)
rendered_body_html = render_template_with_context(payload.body_html, context)

if rendered_body_html.strip():
    rendered = normalize_email_html(rendered_body_html)
else:
    rendered = text_to_email_html(rendered_body_text)
```

发送时使用：

```python
recipient_name=TEST_RECIPIENT_NAME,
```

- [ ] **步骤 5：更新测试写信和工作区 identity DTO**

在 `backend/app/schemas/test_compose.py` 和 `backend/app/schemas/workspace.py` 的 identity read model 中新增：

```python
profile_name: str
sender_name: str
```

在 `backend/app/services/test_compose_runtime.py` 的 `_serialize_test_compose_thread()` 中输出：

```python
profile_name=identity.profile_name or identity.name,
sender_name=get_identity_sender_name(identity),
```

在 `backend/app/api/workspace_support.py` 的 workspace identity 序列化位置同步输出相同字段。

- [ ] **步骤 6：运行测试验证通过**

运行：

```powershell
cd backend
uv run python -m unittest test.test_outreach_templates.OutreachTemplateTests.test_template_context_uses_sender_name_field test.test_api_endpoints.ApiEndpointTests.test_test_compose_send_renders_placeholders_before_sending test.test_api_endpoints.ApiEndpointTests.test_test_compose_page_can_generate_and_send_to_self
```

预期：3 个测试通过。

- [ ] **步骤 7：Commit**

```powershell
git add backend/app/services/outreach_templates.py backend/app/services/mail_runtime.py backend/app/services/test_compose_runtime.py backend/app/schemas/test_compose.py backend/app/schemas/workspace.py backend/app/api/workspace_support.py backend/test/test_outreach_templates.py backend/test/test_api_endpoints.py
git commit -m "fix(测试写信): 发送前渲染模板占位符"
```

### 任务 3：编辑器占位符标签节点

**文件：**
- 创建：`frontend/src/lib/templatePlaceholders.ts`
- 创建：`frontend/src/components/molecules/tiptap/TemplatePlaceholder.ts`
- 修改：`frontend/src/components/molecules/EmailTemplateEditor.tsx`
- 修改：`frontend/src/index.css`
- 测试：`frontend/test/EmailTemplateEditor.test.tsx`

- [ ] **步骤 1：编写失败的编辑器测试**

在 `frontend/test/EmailTemplateEditor.test.tsx` 增加：

```tsx
it("renders known template tokens as inline placeholder chips", () => {
  render(
    <EmailTemplateEditor
      label="邮件正文"
      html="<p>{{name}}老师您好，我是{{sender_name}}。</p>"
      onChange={vi.fn()}
    />,
  );

  expect(screen.getByText("导师姓名")).toBeInTheDocument();
  expect(screen.getByText("发件人姓名")).toBeInTheDocument();
  expect(screen.queryByText("{{name}}")).not.toBeInTheDocument();
});

it("inserts placeholder chips and emits template tokens", () => {
  const handleChange = vi.fn();
  render(
    <EmailTemplateEditor
      label="邮件正文"
      html="<p>老师您好</p>"
      onChange={handleChange}
    />,
  );

  fireEvent.click(screen.getByRole("button", { name: "占位符菜单" }));
  fireEvent.click(screen.getByRole("button", { name: "导师姓名" }));

  expect(screen.getByText("导师姓名")).toBeInTheDocument();
  expect(handleChange).toHaveBeenLastCalledWith(
    expect.objectContaining({
      html: expect.stringContaining("{{name}}"),
      text: expect.stringContaining("{{name}}"),
    }),
  );
});
```

- [ ] **步骤 2：运行测试验证失败**

运行：

```powershell
cd frontend
npm test -- EmailTemplateEditor.test.tsx
```

预期：新增测试失败，找不到「占位符菜单」或「导师姓名」标签。

- [ ] **步骤 3：创建前端占位符定义与 HTML 转换**

创建 `frontend/src/lib/templatePlaceholders.ts`：

```ts
export type TemplatePlaceholderKey =
  | "name"
  | "email"
  | "title"
  | "university"
  | "school"
  | "department"
  | "research_direction"
  | "sender_name"
  | "sender_email";

export type TemplatePlaceholderOption = {
  key: TemplatePlaceholderKey;
  label: string;
  token: string;
};

export const TEMPLATE_PLACEHOLDER_OPTIONS: TemplatePlaceholderOption[] = [
  { key: "name", label: "导师姓名", token: "{{name}}" },
  { key: "email", label: "导师邮箱", token: "{{email}}" },
  { key: "title", label: "导师职称", token: "{{title}}" },
  { key: "university", label: "导师学校", token: "{{university}}" },
  { key: "school", label: "导师学院", token: "{{school}}" },
  { key: "department", label: "导师院系", token: "{{department}}" },
  { key: "research_direction", label: "研究方向", token: "{{research_direction}}" },
  { key: "sender_name", label: "发件人姓名", token: "{{sender_name}}" },
  { key: "sender_email", label: "发件邮箱", token: "{{sender_email}}" },
];

export const getTemplatePlaceholder = (key: string | null | undefined) =>
  TEMPLATE_PLACEHOLDER_OPTIONS.find((option) => option.key === key);

const tokenPattern = /\{\{\s*(name|email|title|university|school|department|research_direction|sender_name|sender_email)\s*\}\}/g;

export const prepareTemplatePlaceholderHtml = (html: string) =>
  html.replace(tokenPattern, (_match, key: TemplatePlaceholderKey) => {
    const option = getTemplatePlaceholder(key);
    return `<span data-template-placeholder="${key}">${option?.token ?? `{{${key}}}`}</span>`;
  });

export const serializeTemplatePlaceholderHtml = (html: string) =>
  html.replace(
    /<span[^>]*data-template-placeholder=["']([^"']+)["'][^>]*>.*?<\/span>/g,
    (_match, key: string) => getTemplatePlaceholder(key)?.token ?? "",
  );
```

- [ ] **步骤 4：创建 Tiptap 占位符节点**

创建 `frontend/src/components/molecules/tiptap/TemplatePlaceholder.ts`：

```ts
import { Node, mergeAttributes } from "@tiptap/core";
import {
  getTemplatePlaceholder,
  type TemplatePlaceholderKey,
} from "@/lib/templatePlaceholders";

declare module "@tiptap/core" {
  interface Commands<ReturnType> {
    templatePlaceholder: {
      insertTemplatePlaceholder: (key: TemplatePlaceholderKey) => ReturnType;
    };
  }
}

export const TemplatePlaceholder = Node.create({
  name: "templatePlaceholder",
  inline: true,
  group: "inline",
  atom: true,
  selectable: true,

  addAttributes() {
    return {
      key: {
        default: null,
        parseHTML: (element) => element.getAttribute("data-template-placeholder"),
        renderHTML: (attributes) => ({
          "data-template-placeholder": attributes.key,
        }),
      },
    };
  },

  parseHTML() {
    return [{ tag: "span[data-template-placeholder]" }];
  },

  renderHTML({ node, HTMLAttributes }) {
    const option = getTemplatePlaceholder(node.attrs.key);
    return [
      "span",
      mergeAttributes(HTMLAttributes, {
        class: "email-placeholder-chip",
        "data-label": option?.label ?? node.attrs.key,
      }),
      option?.token ?? "",
    ];
  },

  renderText({ node }) {
    return getTemplatePlaceholder(node.attrs.key)?.token ?? "";
  },

  addCommands() {
    return {
      insertTemplatePlaceholder:
        (key) =>
        ({ commands }) =>
          commands.insertContent({
            type: this.name,
            attrs: { key },
          }),
    };
  },
});
```

- [ ] **步骤 5：接入编辑器菜单与序列化**

在 `frontend/src/components/molecules/EmailTemplateEditor.tsx`：

1. 将 `MenuKey` 扩为：

```ts
type MenuKey = "placeholder" | "font" | "fontSize" | "lineHeight" | "indent";
```

2. 导入：

```ts
import {
  TEMPLATE_PLACEHOLDER_OPTIONS,
  prepareTemplatePlaceholderHtml,
  serializeTemplatePlaceholderHtml,
} from "@/lib/templatePlaceholders";
import { TemplatePlaceholder } from "@/components/molecules/tiptap/TemplatePlaceholder";
```

3. 在 extensions 中加入 `TemplatePlaceholder`。

4. 初始化和同步内容时使用：

```ts
content: prepareTemplatePlaceholderHtml(html),
```

```ts
editor.commands.setContent(prepareTemplatePlaceholderHtml(html), false);
```

5. `onUpdate` 中序列化：

```ts
const nextHtml = serializeTemplatePlaceholderHtml(currentEditor.getHTML());
onChange({
  html: nextHtml,
  text: deriveTextFromEmailHtml(nextHtml),
});
```

6. 工具栏第一行最前面新增：

```tsx
<ToolbarMenu
  active={openMenu === "placeholder"}
  ariaLabel="占位符菜单"
  buttonLabel="占位符"
  options={TEMPLATE_PLACEHOLDER_OPTIONS.map((option) => ({
    label: option.label,
    value: option.key,
  }))}
  selectedValue={null}
  onSelect={(value) => {
    editor.chain().focus().insertTemplatePlaceholder(value as TemplatePlaceholderKey).run();
  }}
  onToggle={() => setOpenMenu((current) => (current === "placeholder" ? null : "placeholder"))}
  onClose={() => setOpenMenu(null)}
/>
```

- [ ] **步骤 6：添加占位符样式**

在 `frontend/src/index.css` 添加：

```css
.email-placeholder-chip {
  display: inline-flex;
  align-items: center;
  min-height: 1.45em;
  border: 1px solid rgb(254 202 202);
  border-radius: 999px;
  background: rgb(254 242 242);
  color: rgb(153 27 27);
  padding: 0 0.5rem;
  font-size: 0.92em;
  font-weight: 600;
  line-height: 1.45;
  vertical-align: baseline;
}

.email-placeholder-chip::before {
  content: attr(data-label);
}

.email-placeholder-chip {
  font-size: 0;
}

.email-placeholder-chip::before {
  font-size: 0.8125rem;
}

.ProseMirror-selectednode.email-placeholder-chip {
  box-shadow: 0 0 0 2px rgb(153 27 27 / 0.2);
}
```

- [ ] **步骤 7：运行测试验证通过**

运行：

```powershell
cd frontend
npm test -- EmailTemplateEditor.test.tsx
```

预期：`EmailTemplateEditor.test.tsx` 全部通过。

- [ ] **步骤 8：Commit**

```powershell
git add frontend/src/lib/templatePlaceholders.ts frontend/src/components/molecules/tiptap/TemplatePlaceholder.ts frontend/src/components/molecules/EmailTemplateEditor.tsx frontend/src/index.css frontend/test/EmailTemplateEditor.test.tsx
git commit -m "feat(编辑器): 添加模板占位符标签"
```

### 任务 4：前端身份字段接入

**文件：**
- 修改：`frontend/src/types/index.ts`
- 修改：`frontend/src/pages/ProfilePage.tsx`
- 修改：`frontend/src/components/organisms/TopNavBar.tsx`
- 测试：`frontend/test/ProfilePageOnboarding.test.tsx`
- 测试：`frontend/test/SelectionContextNotifications.test.tsx`

- [ ] **步骤 1：编写失败的前端测试**

在 `frontend/test/ProfilePageOnboarding.test.tsx` 的 `selectedIdentity` fixture 中添加：

```ts
profile_name: "博士申请配置",
sender_name: "王同学",
```

新增测试：

```tsx
it("shows separate profile name and sender name fields", async () => {
  renderPage();

  expect(await screen.findByLabelText("配置名称")).toHaveValue("博士申请配置");
  expect(screen.getByLabelText("发件人姓名")).toHaveValue("王同学");
});
```

在 `frontend/test/SelectionContextNotifications.test.tsx` 或已有顶部导航测试中，确保 identity fixture 含 `profile_name`，并断言顶部选项显示配置名称：

```tsx
expect(screen.getByText("博士申请配置（默认）")).toBeInTheDocument();
expect(screen.queryByText("王同学（默认）")).not.toBeInTheDocument();
```

- [ ] **步骤 2：运行测试验证失败**

运行：

```powershell
cd frontend
npm test -- ProfilePageOnboarding.test.tsx SelectionContextNotifications.test.tsx
```

预期：找不到「发件人姓名」字段或顶部仍显示旧 `name`。

- [ ] **步骤 3：更新前端类型**

在 `frontend/src/types/index.ts` 中更新：

```ts
export interface IdentityDTO {
  id: number;
  name: string;
  profile_name: string;
  sender_name: string;
  email_address: string;
  ...
}

export interface IdentityPayload {
  name: string;
  profile_name: string;
  sender_name: string;
  email_address: string;
  ...
}

export interface WorkspaceIdentityDTO {
  id: number;
  name: string;
  profile_name: string;
  sender_name: string;
  email_address: string;
}
```

- [ ] **步骤 4：更新 ProfilePage 表单状态和 payload**

在 `frontend/src/pages/ProfilePage.tsx`：

1. `IdentityFormState` 增加：

```ts
profile_name: string;
sender_name: string;
```

2. `createEmptyIdentityForm()` 设置：

```ts
name: "",
profile_name: "",
sender_name: "",
```

3. `toIdentityForm()` 使用兼容值：

```ts
const profileName = identity.profile_name ?? identity.name;
return {
  name: profileName,
  profile_name: profileName,
  sender_name: identity.sender_name ?? profileName,
  ...
};
```

4. `toIdentityPayload()` 输出：

```ts
const profileName = form.profile_name.trim();
return {
  name: profileName,
  profile_name: profileName,
  sender_name: form.sender_name.trim(),
  ...
};
```

5. 原「配置名称」输入绑定到 `profile_name`，旁边新增「发件人姓名」：

```tsx
<label className="block">
  {renderFieldLabel("配置名称", true)}
  <input
    ref={identityNameInputRef}
    value={identityForm.profile_name}
    onChange={(event) =>
      setIdentityForm((previous) => ({
        ...previous,
        name: event.target.value,
        profile_name: event.target.value,
      }))
    }
    className={inputClassName}
    placeholder="示例：博士申请邮箱"
  />
</label>
<label className="block">
  {renderFieldLabel("发件人姓名", true)}
  <input
    value={identityForm.sender_name}
    onChange={(event) =>
      setIdentityForm((previous) => ({
        ...previous,
        sender_name: event.target.value,
      }))
    }
    className={inputClassName}
    placeholder="示例：王同学"
  />
</label>
```

6. 保存前校验增加：

```ts
if (!identityForm.profile_name.trim() || !identityForm.sender_name.trim()) {
  notifyFormErrors("请检查表单", ["请填写配置名称和发件人姓名"]);
  return;
}
```

- [ ] **步骤 5：更新顶部身份选择器**

在 `frontend/src/components/organisms/TopNavBar.tsx`：

```ts
const identityOptions = identities.map((identity) => {
  const profileName = identity.profile_name ?? identity.name;
  return {
    value: identity.id,
    label: `${profileName}${identity.is_default ? "（默认）" : ""}`,
  };
});
```

- [ ] **步骤 6：运行测试验证通过**

运行：

```powershell
cd frontend
npm test -- ProfilePageOnboarding.test.tsx SelectionContextNotifications.test.tsx
```

预期：相关测试通过。

- [ ] **步骤 7：Commit**

```powershell
git add frontend/src/types/index.ts frontend/src/pages/ProfilePage.tsx frontend/src/components/organisms/TopNavBar.tsx frontend/test/ProfilePageOnboarding.test.tsx frontend/test/SelectionContextNotifications.test.tsx
git commit -m "feat(身份): 前端接入配置名称和发件人姓名"
```

### 任务 5：测试写信提示与最终验证

**文件：**
- 修改：`frontend/src/pages/TestComposePage.tsx`
- 测试：`frontend/test/TestComposePage.test.tsx`

- [ ] **步骤 1：编写失败的测试写信提示测试**

在 `frontend/test/TestComposePage.test.tsx` 的 thread fixture identity 中加入：

```ts
profile_name: "测试配置",
sender_name: "王同学",
```

在 `"loads the draft and send history for the current identity and llm"` 中追加：

```tsx
expect(screen.getByText("{{name}} 会在测试邮件中替换为「测试收件人」")).toBeInTheDocument();
expect(screen.getByText("发件人姓名：王同学")).toBeInTheDocument();
```

- [ ] **步骤 2：运行测试验证失败**

运行：

```powershell
cd frontend
npm test -- TestComposePage.test.tsx
```

预期：找不到测试占位符提示。

- [ ] **步骤 3：更新测试写信页提示**

在 `frontend/src/pages/TestComposePage.tsx` 的右侧发送上下文卡片中增加轻量提示：

```tsx
<div className="mt-4 rounded-2xl border border-primary/15 bg-primary/5 px-4 py-3 text-xs leading-6 text-stone-600">
  <div>{"{{name}} 会在测试邮件中替换为「测试收件人」"}</div>
  <div>发件人姓名：{thread.identity.sender_name ?? thread.identity.name}</div>
</div>
```

- [ ] **步骤 4：运行前端测试验证通过**

运行：

```powershell
cd frontend
npm test -- TestComposePage.test.tsx EmailTemplateEditor.test.tsx ProfilePageOnboarding.test.tsx SelectionContextNotifications.test.tsx
```

预期：相关前端测试通过。

- [ ] **步骤 5：运行后端相关测试验证通过**

运行：

```powershell
cd backend
uv run python -m unittest test.test_outreach_templates test.test_api_endpoints
```

预期：后端模板和 API 测试通过。

- [ ] **步骤 6：运行全量前端检查**

运行：

```powershell
cd frontend
npm test
npm run lint
npm run build
```

预期：Vitest 全部通过，ESLint 无错误，生产构建成功。若 Vite 只提示 chunk 体积警告，不视为失败。

- [ ] **步骤 7：运行后端迁移冒烟**

运行：

```powershell
cd backend
uv run python -m alembic upgrade head
```

预期：迁移成功，退出码为 0。

- [ ] **步骤 8：Commit**

```powershell
git add frontend/src/pages/TestComposePage.tsx frontend/test/TestComposePage.test.tsx
git commit -m "feat(测试写信): 提示测试占位符替换规则"
```

## 最终验收清单

- [ ] 个人页身份表单展示「配置名称」和「发件人姓名」。
- [ ] 顶部身份选择器展示配置名称。
- [ ] 邮件 `From` 和 `{{sender_name}}` 使用发件人姓名。
- [ ] 编辑器显示「导师姓名」等内联标签，不显示 `{{name}}` 原始文本。
- [ ] 编辑器保存输出仍包含 `{{name}}` 等模板变量。
- [ ] 测试写信直接发送会把 `{{name}}` 替换为「测试收件人」。
- [ ] 发送历史不包含原始 `{{name}}` 或 `{{sender_name}}`。
- [ ] `cd backend && uv run python -m unittest test.test_outreach_templates test.test_api_endpoints` 通过。
- [ ] `cd frontend && npm test && npm run lint && npm run build` 通过。
