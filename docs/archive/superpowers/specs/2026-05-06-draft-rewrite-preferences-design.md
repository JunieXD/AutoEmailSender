# 草稿改写偏好设置设计

## 背景

当前系统已经支持基于套磁信模板和导师信息生成 AI 草稿。现有草稿生成规则强调“以模板为基础润色”，但普通用户无法调整改写风格，只能接受系统默认输出。

用户希望在“其他设置”中调整 LLM 改写草稿的部分提示词效果，例如风格、语气、长度和信息保留优先级。该能力面向普通用户，因此不应暴露完整系统提示词，也不应允许用户修改核心安全约束和结构化输出约束。

本设计聚焦 AI 草稿生成偏好，不改变匹配分析、导师抓取、邮件发送和富文本 schema。

## 目标

- 在“其他设置”中新增“草稿改写偏好”配置。
- 普通用户通过结构化选项调整 AI 草稿输出风格。
- 后端固定系统提示词骨架，只把用户偏好转换为受控改写约束。
- 支持设置页预览，让用户理解当前偏好会如何影响草稿。
- 配置缺失、非法或旧数据不完整时，自动回退默认值。

## 非目标

- 不提供完整 system prompt 编辑器。
- 不允许用户覆盖 JSON 输出、富文本 schema、模板保留、研究方向个性化等硬约束。
- 不扩展当前 rich_body 富文本结构。
- 不改变模板模式，因为模板模式不调用 LLM。
- 不为历史草稿重新生成或迁移改写效果。

## 推荐方案

采用“系统预置提示词 + 用户可调偏好”方案。

前端只展示普通用户能理解的结构化选项。后端将这些选项归一化后，生成一段“草稿改写偏好”文本，插入到 `build_draft_prompt()` 的任务要求中。系统级提示词 `SYSTEM_DRAFT_PROMPT` 仍保留固定约束。

该方案比完整 prompt 编辑器更稳定，也比完全不开放设置更可控。用户能调节输出观感，但不能破坏核心生成协议。

## 设置项

### 改写强度

字段名：`draft_rewrite_intensity`

选项：

| 值 | 展示文案 | 说明 |
| --- | --- | --- |
| `light` | 轻微 | 只做必要个性化，最大限度保留原文 |
| `moderate` | 中等 | 在保留结构的基础上优化表达，默认值 |
| `strong` | 明显 | 更主动地优化措辞和连接句，但不从零重写 |

### 语气

字段名：`draft_rewrite_tone`

选项：

| 值 | 展示文案 | 说明 |
| --- | --- | --- |
| `polite` | 礼貌 | 更重视谦逊、尊重和边界感，默认值 |
| `professional` | 专业 | 更突出研究表达和学术沟通 |
| `friendly` | 亲和 | 表达更自然，减少生硬套话 |

### 正式程度

字段名：`draft_rewrite_formality`

选项：

| 值 | 展示文案 | 说明 |
| --- | --- | --- |
| `natural` | 更自然 | 句式更口语化，但保持礼貌 |
| `balanced` | 默认 | 兼顾自然和正式，默认值 |
| `formal` | 更正式 | 更接近正式学术邮件 |

### 长度

字段名：`draft_rewrite_length`

选项：

| 值 | 展示文案 | 说明 |
| --- | --- | --- |
| `shorter` | 更短 | 压缩冗余表达，避免过长段落 |
| `default` | 默认 | 保持接近模板长度，默认值 |
| `more_detailed` | 更详细 | 允许补充更具体的匹配理由，但不堆砌 |

### 具体性

字段名：`draft_rewrite_specificity`

选项：

| 值 | 展示文案 | 说明 |
| --- | --- | --- |
| `concise` | 概括 | 匹配理由更简洁 |
| `balanced` | 平衡 | 兼顾简洁和具体，默认值 |
| `detailed` | 细节更足 | 更强调导师方向、论文和材料经历的具体连接 |

### 模板保留度

字段名：`draft_template_preservation`

选项：

| 值 | 展示文案 | 说明 |
| --- | --- | --- |
| `structure_first` | 优先保留结构 | 尽量保持段落顺序和原有话术，默认值 |
| `balanced` | 平衡 | 保留结构，同时允许优化表达 |
| `content_first` | 更重内容表达 | 允许较多改写个性化内容，但仍不能从零重写 |

## 默认值

默认配置如下：

```json
{
  "draft_rewrite_intensity": "moderate",
  "draft_rewrite_tone": "polite",
  "draft_rewrite_formality": "balanced",
  "draft_rewrite_length": "default",
  "draft_rewrite_specificity": "balanced",
  "draft_template_preservation": "structure_first"
}
```

这些默认值应尽量贴近现有生成行为，避免老用户升级后明显感知到草稿风格变化。

## 后端设计

### 数据模型

在 `AppSetting` 中增加 6 个字符串字段，保存草稿改写偏好。字段使用枚举字符串，便于前后端一致映射。

在 `RuntimeSettingsRead` 和 `RuntimeSettingsUpdate` 中加入对应字段。更新接口继续使用现有 `/api/runtime-settings`。

后端 schema 使用 `Literal` 或枚举校验取值。旧数据缺失时，由 `get_or_create_app_settings()` 或序列化函数回填默认值。

### Prompt 组装

新增一个小函数，例如：

```python
def build_draft_rewrite_preferences(settings: AppSetting) -> str:
    ...
```

该函数负责把结构化偏好转换为自然语言约束，例如：

```text
草稿改写偏好：
- 改写强度：中等，在保留模板结构的基础上优化表达。
- 语气：礼貌，表达尊重和边界感。
- 正式程度：默认，兼顾自然和正式。
- 长度：默认，保持接近模板长度。
- 具体性：平衡，匹配理由要具体但不堆砌。
- 模板保留度：优先保留结构，尽量保持段落顺序和主要话术。

这些偏好只影响表达方式，不得覆盖系统要求、JSON 输出结构、富文本 schema 和模板保留硬约束。
```

`generate_draft_content()` 或其上层服务需要拿到运行时设置，并把该偏好文本传入 `build_draft_prompt()`。如果调用路径暂时不方便传入完整 `AppSetting`，可以传入一个轻量 DTO 或普通字典。

### 冲突处理

后端按以下优先级处理偏好冲突：

1. 硬约束优先：结构化 JSON、富文本 schema、必须基于模板、必须结合导师研究方向。
2. 模板保留度优先于改写强度：如果用户选择“明显改写”且“优先保留结构”，则允许优化措辞，但不能大幅调整段落结构。
3. 长度优先于具体性：如果用户选择“更短”和“细节更足”，则保留最关键的 1-2 个具体连接点，不展开长段落。

非法值一律回退默认值，不把错误透传给 LLM。

## 前端设计

### 设置页

在现有 `OtherSettingsCard` 中新增“草稿改写偏好”区块。并发设置保持原样，草稿偏好使用下拉或分段控件。

交互要求：

- 每个字段显示清晰名称和一句简短说明。
- 提供“恢复默认”按钮，仅重置草稿改写偏好，不影响并发设置。
- 保存仍使用现有“保存设置”入口。
- 摘要文案从当前“匹配 / 抓取”扩展为包含草稿偏好，例如“匹配 3 / 抓取 3 / 草稿 默认”。

### 即时示例预览

设置区块右侧或下方展示“示例效果”。该预览不调用 LLM，由前端根据当前偏好选择固定样例文案。

示例输入可以固定为：

```text
我对您的人工智能研究方向很感兴趣，希望有机会加入课题组。
```

前端根据主要偏好组合展示一段短输出。该预览只用于帮助用户理解设置方向，不作为最终草稿质量承诺。

### 真实预览

新增“生成真实预览”按钮。点击后调用后端预览接口，使用当前偏好、当前模板、当前导师和默认材料生成预览草稿。

建议接口：

```http
POST /api/tasks/{task_id}/draft-preview
```

行为：

- 调用 LLM 生成草稿预览。
- 不保存到 `email_tasks`。
- 不新增正式草稿日志。
- 返回 subject、body_html、body_text、rich_body 和 token usage。
- 前端明确提示“真实预览会消耗一次模型调用”。

如果后端暂时不想增加新接口，可以先只实现即时示例预览，真实预览作为后续迭代。但最终体验建议保留真实预览。

## API 设计

### 运行时设置读取

`GET /api/runtime-settings` 返回新增字段：

```json
{
  "draft_rewrite_intensity": "moderate",
  "draft_rewrite_tone": "polite",
  "draft_rewrite_formality": "balanced",
  "draft_rewrite_length": "default",
  "draft_rewrite_specificity": "balanced",
  "draft_template_preservation": "structure_first"
}
```

### 运行时设置更新

`PATCH /api/runtime-settings` 接收新增字段。为保持当前前端实现简单，短期可以要求提交完整设置对象；如果后续设置项继续增加，再改成 partial update。

### 草稿真实预览

`POST /api/tasks/{task_id}/draft-preview`

请求体：

```json
{
  "preferences": {
    "draft_rewrite_intensity": "moderate",
    "draft_rewrite_tone": "polite",
    "draft_rewrite_formality": "balanced",
    "draft_rewrite_length": "default",
    "draft_rewrite_specificity": "balanced",
    "draft_template_preservation": "structure_first"
  }
}
```

如果请求体不传 `preferences`，则使用已保存的运行时设置。

响应体沿用草稿生成结果结构，并额外返回 usage：

```json
{
  "subject": "申请与李老师交流科研方向",
  "body_text": "...",
  "body_html": "...",
  "rich_body": {},
  "suggested_material_ids": [],
  "usage": {
    "prompt_tokens": 1200,
    "completion_tokens": 350,
    "total_tokens": 1550
  }
}
```

## 数据迁移

新增 Alembic migration，为 `app_settings` 表增加 6 个字段。字段可以设置数据库默认值，也可以在应用层回填。推荐两者都做：

- 数据库层保证新增列有默认值。
- 应用层序列化时对 `None` 做默认值兜底。

## 测试计划

### 后端测试

- `RuntimeSettingsRead` 返回 6 个草稿改写偏好字段。
- `PATCH /api/runtime-settings` 能保存合法偏好。
- 非法枚举值返回 422 或被明确拒绝。
- 旧设置缺失字段时，序列化结果使用默认值。
- `build_draft_rewrite_preferences()` 能生成稳定、可预测的偏好文本。
- 草稿生成 prompt 包含偏好文本，但仍保留模板改写硬约束。
- 真实预览接口不会写入正式草稿，也不会改变任务状态。

### 前端验证

- `OtherSettingsCard` 能展示和保存 6 个草稿偏好字段。
- “恢复默认”只重置草稿偏好，不影响并发设置。
- 即时示例预览会随字段变化更新。
- 真实预览按钮在没有当前任务或缺少必要条件时禁用，并显示原因。
- 保存失败时沿用现有错误提示。

## 规格自检

- 无 TODO、占位符或未定字段。
- 范围聚焦于 AI 草稿改写偏好，不扩展无关功能。
- 普通用户只接触结构化选项，不接触完整 system prompt。
- 后端保留硬约束，用户偏好只影响表达方式。
- 预览分为不消耗模型的即时示例和会消耗模型的真实预览，边界明确。
