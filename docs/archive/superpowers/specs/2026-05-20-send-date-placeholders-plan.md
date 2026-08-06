# 发送日期占位符实施计划

关联设计文档：

- `docs/superpowers/specs/2026-05-20-send-date-placeholders-design.md`

采用方案：

- 新增 `{{year}}`、`{{month}}`、`{{day}}` 三个模板占位符。
- 前端只负责插入和展示 token。
- 后端在最终发送前按本地时区 计算实际发送日并渲染 token。

## 1. 改动范围

### 预计涉及文件

前端：

- `frontend/src/lib/templatePlaceholders.ts`
- `frontend/src/pages/CreateTaskPage.tsx`
- `frontend/src/pages/ProfilePage.tsx`
- `frontend/test/templatePlaceholders.test.ts`
- `frontend/test/SubjectTemplateInput.test.tsx`
- `frontend/test/EmailTemplateEditor.test.tsx`

后端：

- `backend/app/services/outreach_templates.py`
- `backend/app/services/template_draft_rewrite.py`
- `backend/app/services/task_runtime.py`
- `backend/app/services/test_compose_runtime.py`
- `backend/test/test_outreach_templates.py`
- `backend/test/test_api_endpoints.py`
- `backend/test/test_template_draft_rewrite.py`

### 不预计修改

- 不修改数据库模型和迁移。
- 不修改邮件发送 SMTP 组装逻辑。
- 不修改调度窗口和定时任务计算逻辑。
- 不新增用户配置项。
- 不新增前端日期格式设置 UI。

## 2. 实施步骤

### 2.1 扩展前端占位符定义

文件：`frontend/src/lib/templatePlaceholders.ts`

操作：

1. 在 `TemplatePlaceholderKey` 中新增：

```ts
| "year"
| "month"
| "day"
```

2. 在 `TEMPLATE_PLACEHOLDER_OPTIONS` 中新增：

```ts
{ key: "year", label: "发送年份", token: "{{year}}" },
{ key: "month", label: "发送月份", token: "{{month}}" },
{ key: "day", label: "发送日期", token: "{{day}}" },
```

3. 更新 `createTemplateTokenPattern`，让前端能把已有模板中的 `{{year}}`、`{{month}}`、`{{day}}` 渲染为 chip。

目标结果：

- 主题输入框和正文编辑器的占位符菜单自动出现新字段。
- 保存值继续是原始 token。

### 2.2 更新前端提示文案

文件：

- `frontend/src/pages/CreateTaskPage.tsx`
- `frontend/src/pages/ProfilePage.tsx`

操作：

1. 找到现有“支持占位符”的说明区域。
2. 加入 `{{year}}`、`{{month}}`、`{{day}}` 示例。
3. 保持提示简洁，不新增日期格式选择控件。

推荐文案：

```text
支持 {{name}}、{{university}}、{{sender_name}}、{{year}}、{{month}}、{{day}} 等占位符。
```

### 2.3 新增本地时区日期上下文工具

文件：`backend/app/services/outreach_templates.py`

操作：

1. 引入 `datetime` 和 `tzinfo`。
2. 使用运行环境本地时区作为默认时区：

```python
local_timezone = datetime.now().astimezone().tzinfo
```

3. 新增函数：

```python
def build_send_date_context(
    now: datetime | None = None,
    *,
    local_timezone: tzinfo | None = None,
) -> dict[str, str]:
    local_timezone = local_timezone or datetime.now().astimezone().tzinfo
    current = now or datetime.now(local_timezone)
    if current.tzinfo is None:
        current = current.replace(tzinfo=local_timezone)
    local_now = current.astimezone(local_timezone)
    return {
        "year": str(local_now.year),
        "month": str(local_now.month),
        "day": str(local_now.day),
    }
```

注意：

- `month` 和 `day` 直接 `str(int)`，不补零。
- 测试可传入固定 UTC 时间验证跨日转换。

### 2.4 区分基础模板上下文和发送上下文

文件：`backend/app/services/outreach_templates.py`

操作：

1. 在 `PLACEHOLDER_HELP_TEXT` 中加入：

```python
"year": "本地发送年份",
"month": "本地发送月份",
"day": "本地发送日期",
```

2. 让 `build_template_context(identity, professor)` 继续负责导师和身份字段，并把 `year/month/day` 设为原始 token，避免早期渲染阶段把日期占位符清空。
3. 新增发送上下文组合函数，例如：

```python
def build_send_template_context(
    identity: IdentityProfile,
    professor: Professor,
    *,
    now: datetime | None = None,
) -> dict[str, str]:
    return {
        **build_template_context(identity, professor),
        **build_send_date_context(now),
    }
```

4. 为测试发信新增类似组合函数：

```python
def build_test_compose_send_template_context(
    identity: IdentityProfile,
    *,
    now: datetime | None = None,
) -> dict[str, str]:
    return {
        **build_test_compose_template_context(identity),
        **build_send_date_context(now),
    }
```

目标：

- 普通模板生成和 AI 改写仍可使用保留原始日期 token 的基础上下文，避免早期固化日期。
- 最终发送阶段显式使用发送上下文。

### 2.5 调整最终发送渲染路径

文件：`backend/app/services/task_runtime.py`

操作：

1. 找到最终发送前渲染 `subject_template`、`body_text_template`、`body_html_template` 的逻辑。
2. 将 context 从 `build_template_context(task.identity, task.professor)` 改为发送上下文组合函数。
3. 确认调用发生在 `mail_runtime.send_email(...)` 前。

目标：

- 最终发送当天才替换 `{{year}}`、`{{month}}`、`{{day}}`。
- 定时任务跨天发送时使用真实发送时刻的本地时区日期。

### 2.6 调整测试发信渲染路径

文件：`backend/app/services/test_compose_runtime.py`

操作：

1. 找到测试发送时的 context 构建。
2. 将发送动作使用的 context 改为测试发信发送上下文。
3. 保持测试收件人、测试学校等现有字段不变。

目标：

- 测试发送按触发发送时的本地时区渲染日期。

### 2.7 保护早期生成阶段不固化日期

文件：

- `backend/app/services/outreach_templates.py`
- `backend/app/services/template_draft_rewrite.py`
- `backend/app/services/task_runtime.py`

操作：

1. 检查创建任务时直接套用模板生成 `generated_*` 和 `approved_*` 的逻辑。
2. 避免这里使用发送日期上下文。
3. 如果当前流程必须产出预览文本，则允许日期 token 暂时保留为 `{{year}}`、`{{month}}`、`{{day}}`。
4. 确认最终发送前会再次渲染 token。

目标：

- 预览可以显示 token 或临时预览值，但最终发送不能依赖早期日期。

## 3. 测试计划

### 3.1 前端测试

建议更新或新增：

- `frontend/test/templatePlaceholders.test.ts`
- `frontend/test/SubjectTemplateInput.test.tsx`
- `frontend/test/EmailTemplateEditor.test.tsx`

覆盖点：

1. `{{year}}`、`{{month}}`、`{{day}}` 能被解析为 placeholder segment。
2. `prepareTemplateEditorHtml` 能将日期 token 转为 `span[data-template-placeholder]`。
3. `serializeTemplatePlaceholderHtml` 能将日期 chip 转回 token。
4. 主题占位符菜单展示三个新选项。
5. 正文占位符菜单展示三个新选项。

### 3.2 后端单元测试

建议更新或新增：

- `backend/test/test_outreach_templates.py`
- `backend/test/test_template_draft_rewrite.py`

覆盖点：

1. 固定 UTC 时间 `2026-05-19 16:30:00Z`，发送日期上下文为 `2026`、`5`、`20`。
2. `{{year}}年{{month}}月{{day}}日` 渲染为不补零本地时区日期。
3. 基础模板上下文不应在早期阶段强制固化发送日期。
4. AI 改写前的模板渲染对日期 token 不应破坏最终发送语义。

### 3.3 后端集成测试

建议更新或新增：

- `backend/test/test_api_endpoints.py`

覆盖点：

1. 直接套用模板任务最终发送时，日期占位符被替换为本地发送日。
2. 手动发送或测试发送路径能渲染日期占位符。
3. 使用跨 UTC 日期边界的固定时间，防止误用 UTC 日期。

## 4. 验证命令

前端聚焦测试：

```bash
cd frontend && npm run test -- templatePlaceholders.test.ts SubjectTemplateInput.test.tsx EmailTemplateEditor.test.tsx
```

如果 Vitest 文件筛选不可用，则运行：

```bash
cd frontend && npm run test
```

后端聚焦测试：

```bash
cd backend && uv run python -m unittest test.test_outreach_templates test.test_template_draft_rewrite
```

后端接口测试：

```bash
cd backend && uv run python -m unittest test.test_api_endpoints
```

如果改动触及 lint 或类型：

```bash
cd frontend && npm run lint
cd frontend && npm run build
```

## 5. 手动验收

### 模板编辑验收

1. 启动前端。
2. 进入个人中心的默认套磁信模板编辑。
3. 打开主题占位符菜单，确认出现发送年份、发送月份、发送日期。
4. 打开正文占位符菜单，确认出现发送年份、发送月份、发送日期。
5. 插入 `{{year}}年{{month}}月{{day}}日` 组合并保存。
6. 重新打开页面，确认 token 显示为 chip 和普通文本混排。

### 发送验收

1. 创建或编辑任务，使邮件正文包含：

```text
{{year}}年{{month}}月{{day}}日
```

2. 触发测试发送或手动发送。
3. 检查已发送邮件内容。
4. 确认日期为本地发送日，月份和日期不补零。

### 跨日验收

如果可通过测试注入固定时间，应覆盖：

- UTC：`2026-05-20 06:30:00Z`
- 本地时区：`UTC-07:00`
- 期望渲染：`2026年5月19日`

## 6. 风险与处理

### 风险 1：日期在创建任务时被提前固化

表现：

- 定时到未来日期发送时，邮件仍显示创建任务当天。

处理：

- 最终发送路径必须使用发送上下文重新渲染。
- 早期生成路径不要使用发送日期上下文。
- 增加跨天定时发送测试。

### 风险 2：误用 UTC 或系统本地时区

表现：

- 本地时区凌晨发送时，日期显示为 UTC 前一天。

处理：

- 所有日期占位符统一通过 本地时区 派生。
- 增加 UTC 到本地时区跨日测试。

### 风险 3：AI 改写吃掉日期 token

表现：

- 模型把 `{{year}}` 改成固定数字，或删除日期占位符。

处理：

- 发送日期 token 应作为用户模板内容保留到最终发送阶段。
- 如果 AI 改写链路需要展示日期语义，优先在 prompt 中说明 token 含义，而不是提前替换为具体日期。

### 风险 4：未知占位符行为被改变

表现：

- 旧模板中的未知 token 行为和过去不同。

处理：

- 只扩展已知占位符集合和发送上下文。
- 不重写通用 `render_template_string` 的未知 key 规则，除非测试要求。

## 7. 回滚方式

如果实现后发现日期占位符影响发送链路，可按以下方式回滚：

1. 从前端 `TEMPLATE_PLACEHOLDER_OPTIONS` 和 token 正则中移除 `year/month/day`。
2. 从后端 `PLACEHOLDER_HELP_TEXT` 和发送上下文中移除日期字段。
3. 恢复最终发送路径使用原有 `build_template_context`。
4. 删除新增测试。

回滚不涉及数据库迁移，也不影响已有任务和模板数据。

## 8. 不做项确认

本次计划明确不做：

- 不新增 `{{date}}`。
- 不补零。
- 不新增日期格式 UI。
- 不新增用户时区配置。
- 不改调度算法。
- 不改数据库 schema。
- 不重构模板编辑器。

