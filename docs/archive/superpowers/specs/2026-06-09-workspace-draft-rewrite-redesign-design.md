# 工作区草稿与 AI 改写重设计规格

## 背景

当前工作区把“模板生成草稿”和“AI 生成草稿”混在同一套交互里。用户进入工作区时，AI 模式下编辑器可能是空的；点击“生成草稿”后，后端实际基于身份或任务里的模板快照生成，而不是基于用户当前编辑器内容。用户如果先切到模板模式生成一次，编辑器有内容后再使用 AI，体验才看起来正常。

这导致几个问题：

- 用户上传了默认套磁信模板，但进入工作区仍看到空编辑器，会误以为模板没有生效。
- “生成草稿”听起来像创建正文，但实际需求是对当前正文做 AI 改写。
- 用户在编辑器中修改模板后，当前 AI 生成接口不会使用这些即时修改。
- AI 生成过程中，保存、发送、定时发送等动作需要稳定锁定，避免写入并发冲突。
- 模型长时间不返回或应用进程异常退出时，任务不能永久停留在生成中。

本规格将工作区改为“当前草稿 + AI 改写当前草稿”的模型。编辑器始终代表当前可编辑草稿；AI 只是对点击瞬间的编辑器内容发起异步改写。

## 目标

1. 用户进入工作区后，如果有默认套磁信模板，编辑器直接显示按当前导师渲染后的模板。
2. 如果没有模板，也没有历史草稿，编辑器保持空白。
3. 编辑器中的内容就是当前草稿；用户可以直接保存、发送或定时发送。
4. AI 动作改名为“AI 改写”，并基于点击瞬间的编辑器内容改写。
5. AI 改写前必须先把当前编辑器内容落库，保证用户离开页面或刷新后内容不丢。
6. AI 改写中锁定编辑器和草稿动作，但允许用户离开页面。
7. 重新进入工作区时，后端返回真实状态：改写中、改写成功、改写失败或已恢复。
8. 模型超时、进程中断、应用重启后，任务能够恢复到可编辑状态，不永久卡在改写中。
9. 尽量减少前端推导复杂度，由后端提供当前草稿视图和任务状态。

## 非目标

- 不重写富文本编辑器。
- 不改变批量任务自动草稿生成的整体调度模型。
- 不取消模板模式；模板模式仍表示“不调用 LLM，直接套用模板”。
- 不要求远端 LLM 请求在本地超时或取消后停止计费；系统只保证本地状态正确恢复。
- 不在本规格内实现多版本草稿历史或 AI 改写前后 diff 对比。

## 核心产品模型

工作区编辑器始终显示“当前草稿”：

1. 若任务已有用户保存草稿，显示保存草稿。
2. 若任务已有 AI 改写结果，显示 AI 改写结果。
3. 若任务没有草稿但有默认套磁信模板，显示按当前导师渲染后的模板。
4. 若任务没有草稿也没有模板，显示空草稿。
5. 若任务正在 AI 改写，显示改写源草稿并锁定。

用户不需要点击按钮来“生成模板草稿”。模板是当前草稿的初始值。AI 只负责把当前草稿改写成更个性化的版本。

## 状态定义

### 当前草稿来源

后端在工作区响应中增加或明确返回一个当前草稿视图，例如：

```json
{
  "draft": {
    "subject": "当前主题",
    "body_text": "当前正文纯文本",
    "body_html": "<p>当前正文</p>",
    "source": "template",
    "sendable": true,
    "editable": true
  }
}
```

`source` 取值：

| 值 | 含义 |
| --- | --- |
| `saved` | 用户保存过的草稿 |
| `ai_rewrite` | AI 改写成功后的草稿 |
| `template` | 默认模板渲染出的初始草稿 |
| `manual_empty` | 没有可用模板和历史草稿 |
| `rewrite_source` | AI 改写中展示的源草稿 |

`sendable` 表示当前内容是否可以作为邮件正文发送。只要正文非空，且任务不处于生成中、发送中、已取消、已发送等阻塞状态，就可以发送。它不再等同于“AI 生成过”。

`editable` 表示编辑器是否可编辑。AI 改写中为 `false`。

### 任务状态

继续使用现有 `EmailTaskStatus.GENERATING_DRAFT`，但在工作区前端语义中展示为“AI 改写中”。状态流转：

- 可编辑状态 -> `generating_draft`：用户点击 AI 改写。
- `generating_draft` -> `review_required`：AI 改写成功。
- `generating_draft` -> 改写前状态：AI 改写失败、超时或中断恢复。

改写前状态通过 `draft_generation_previous_status` 保存。若该字段为空，恢复为 `review_required`，因为只要源草稿已经落库，用户就可以继续编辑或发送。

## 后端接口设计

### 工作区读取

工作区读取接口继续返回 `WorkspaceThreadRead`，但应由后端统一计算当前草稿视图，避免前端自行拼装 `approved_*`、`generated_*`、draft log 和 `rendered_template_*` 的优先级。

当前草稿选择优先级：

1. AI 改写中：返回改写源草稿，`source = rewrite_source`，`editable = false`。
2. 用户保存草稿：返回 `approved_subject/approved_body_*` 或新的保存草稿字段，`source = saved`。
3. AI 改写结果：返回 `generated_subject/generated_content_*`，`source = ai_rewrite`。
4. 默认模板可渲染：返回渲染后的模板，`source = template`。
5. 以上都没有：返回空草稿，`source = manual_empty`。

注意：如果历史字段仍使用 `approved_*` 保存“已保存但未发送草稿”，需要在命名上通过序列化层隐藏历史歧义，前端只看 `draft`。

### AI 改写接口

新增工作区专用接口：

`POST /api/email-tasks/{task_id}/rewrite-draft`

请求体：

```json
{
  "subject": "当前主题",
  "body_text": "当前正文纯文本",
  "body_html": "<p>当前正文</p>",
  "selected_material_ids": [1, 2],
  "llm_profile_id": 2
}
```

处理流程：

1. 加载任务并校验任务允许工作区草稿操作。
2. 校验 `body_text` 或 `body_html` 派生出的正文非空。
3. 校验 AI 改写所需条件：默认材料、导师研究方向、运行模型配置、材料可提取文本。
4. 在同一事务中保存改写源草稿快照，包括主题、正文 HTML、正文纯文本、附件选择、发起时间。
5. 将任务状态原子更新为 `generating_draft`，写入 `draft_generation_previous_status`、`draft_generation_started_at`，清空旧 `last_error`。
6. 启动异步改写任务，立即返回最新工作区线程，或等待本次改写完成后返回最终线程。

推荐第一版沿用现有手动生成接口的同步等待模式，但必须先落库源草稿并置为 `generating_draft`。这样即使请求过程中用户离开页面，重新进入也能看到正确的生成中状态。

后续如果需要更顺滑的体验，可以把工作区 AI 改写改成真正后台任务：接口领取后立即返回 `generating_draft`，前端轮询或靠已有工作区刷新拿到结果。

### 保存草稿接口

保存草稿接口继续接收当前编辑器内容。生成中时拒绝保存，返回 400 或 409：

```text
AI 正在改写当前草稿，请等待完成后再保存。
```

保存成功后，当前草稿来源为 `saved`，状态进入可编辑可发送状态。

### 发送接口

发送和定时发送继续接收当前编辑器内容。生成中时拒绝发送和定时，返回：

```text
AI 正在改写当前草稿，请等待完成后再发送。
```

发送不要求 AI 改写完成。只要当前草稿正文非空且其他发送条件满足，就可以发送。

### 旧接口兼容

现有 `/generate-draft` 可保留给批量任务、老测试或兼容入口。工作区前端应迁移到 `/rewrite-draft`。

旧接口后端仍可基于任务模板生成，不在本规格中强制删除。但新的工作区交互不再调用它。

## 数据模型

需要在 `email_tasks` 上增加或复用字段保存改写源草稿。推荐新增明确字段，避免覆盖当前可发送草稿：

- `draft_generation_started_at: datetime | null`
- `draft_rewrite_source_subject: text | null`
- `draft_rewrite_source_body_text: text | null`
- `draft_rewrite_source_body_html: text | null`
- `draft_rewrite_source_selected_material_ids: json | null`

已有字段继续使用：

- `draft_generation_previous_status`
- `generated_subject`
- `generated_content_text`
- `generated_content_html`
- `approved_subject`
- `approved_body_text`
- `approved_body_html`
- `selected_material_ids`
- `last_error`

改写开始时：

- 保存源草稿到 `draft_rewrite_source_*`。
- 同步 `selected_material_ids`，确保重新进入工作区显示一致。
- 状态改为 `generating_draft`。

改写成功时：

- 写入 `generated_*`。
- 可清空 `draft_rewrite_source_*`，也可保留用于日志诊断；前端不再展示。
- 状态改为 `review_required`。
- 清空 `draft_generation_previous_status`、`draft_generation_started_at`、`last_error`。
- 写入 draft email log 和 token 用量记录。

改写失败、超时或中断恢复时：

- 将源草稿恢复为当前可编辑草稿。实现上可以写入 `approved_*` 作为保存草稿，或由工作区序列化层在 `last_error` 存在时优先返回 `draft_rewrite_source_*`。
- 状态恢复为 `draft_generation_previous_status`；若为空，恢复为 `review_required`。
- 清空 `draft_generation_previous_status`、`draft_generation_started_at`。
- 保留 `last_error`。
- 不写入成功 draft log，不记录 LLM token，除非远端返回了可统计的失败用量且现有 token 体系明确支持失败记录。

## AI 改写输入

AI 改写必须基于接口请求里的当前草稿，而不是身份默认模板或任务旧模板快照。

输入到 LLM 的模板字段：

- `custom_subject = payload.subject`
- `custom_body = payload.body_text`
- `custom_body_html = payload.body_html`

若 `body_html` 存在，优先走富文本模板改写路径。若只有 `body_text`，转换成邮件 HTML 后改写。该逻辑应复用现有 `llm_runtime.generate_draft_content()` 的模板改写能力。

## 前端交互设计

### 文案

统一把工作区内的“生成草稿”改为“AI 改写”。

状态标签：

| 状态 | 标签 |
| --- | --- |
| 当前草稿正文非空，且未生成中 | `草稿可编辑` |
| 当前任务生成中 | `AI 改写中` |
| 当前草稿为空 | `空草稿` |
| 已发送、已回信等不可编辑状态 | 沿用现有状态标签 |

折叠态标题：

| 状态 | 标题 | 描述 |
| --- | --- | --- |
| 有正文 | `继续写信` | `可直接编辑、保存或发送，也可以让 AI 改写。` |
| 空正文 | `写第一封信` | `先写入正文或配置默认模板后再使用 AI 改写。` |
| 生成中 | `AI 正在改写` | `当前草稿已锁定，完成后会自动显示新版本。` |

生成区标题保留为“AI 改写”或将 `ComposerSection` 标题从“生成草稿”改为“AI 改写”。描述：

- 有正文：`基于当前编辑器内容生成个性化版本。`
- 空正文：`先写入正文或配置默认模板后再使用 AI 改写。`
- 生成中：`正在改写当前草稿，完成前不能保存或发送。`

### 按钮可用规则

变量定义：

- `hasDraftBody`：当前草稿正文非空。
- `isRewriting`：任务状态为 `generating_draft`。
- `canRewrite`：`hasDraftBody && !isRewriting && hasPrimaryMaterial && hasProfessorResearchDirection`。
- `canSaveDraft`：`hasDraftBody && !isRewriting && canSubmitDraft`。
- `canSend`：`hasDraftBody && !isRewriting && canSubmitDraft && professorEmailAvailable`。

按钮规则：

| 按钮 | 可用条件 |
| --- | --- |
| AI 改写 | `canRewrite` |
| 保存草稿 | `canSaveDraft` |
| 定时发送 | `canSend` |
| 立即发送 | `canSend` |
| 分析匹配度 | 保持现有条件，但生成中禁用 |
| 切换模式 | 生成中禁用 |
| 附件选择 | 生成中禁用 |

生成中时，编辑器和所有草稿动作都禁用。用户可以离开工作区。

### 编辑器初始化

前端不再自行决定是否用 `rendered_template_*` 填充编辑器。它应直接使用后端返回的 `draft`：

- `setSubject(draft.subject)`
- `setContent(draft.body_text)`
- `setContentHtml(draft.body_html)`
- `setComposerHasSendableDraft(draft.sendable)`

如果要保留兼容旧后端的过渡期，可以把现有 `syncComposer` 逻辑封装为 fallback；实现完成后应移除重复推导。

### 脏草稿保护

用户编辑当前草稿后仍触发现有离开保护。例外：AI 改写中不应弹“保存草稿修改？”对话框，因为源草稿已经在改写开始前落库。

生成中离开页面：

- 不阻止导航。
- 重新进入后由后端状态决定显示改写中或结果。

## 超时与中断恢复

### 请求级超时

LLM 请求继续使用运行配置中的模型请求超时，但工作区 AI 改写的有效超时上限为 5 分钟。即使运行配置或底层客户端允许更长时间，工作区改写也应在 5 分钟内收敛为成功或失败。超时后后端捕获异常并执行失败恢复：

- 状态恢复到改写前状态。
- 当前草稿恢复为改写源草稿。
- `last_error = "AI 改写超时，请稍后重试"`。
- 编辑器重新可用。

### 持久化生成开始时间

任务进入 `generating_draft` 时必须写入 `draft_generation_started_at`。不能只依赖 `updated_at`，因为其他刷新或日志写入可能改变 `updated_at`。

### 启动恢复

应用启动时执行恢复扫描：

1. 查找 `status = generating_draft` 且 `draft_generation_started_at` 早于恢复阈值的任务。
2. 对这些任务执行中断恢复。
3. 写入 `last_error = "AI 改写已中断，请重试"`。
4. 保留源草稿作为当前草稿。

恢复阈值固定为 5 分钟。只要任务处于 `generating_draft` 且 `draft_generation_started_at` 早于当前时间 5 分钟，就视为超时或中断，需要恢复。

```text
workspace_draft_rewrite_timeout = 5 minutes
```

该阈值应在后端集中定义常量，接口请求级超时、启动恢复和周期性恢复共用同一规则，避免前后不一致。

### 定时恢复

运行时后台可以周期性执行同一恢复逻辑，避免桌面应用长时间运行但某次任务因未捕获异常卡住。

恢复逻辑必须幂等：同一任务多次扫描只恢复一次；已经成功或失败的任务不会被误恢复。

## 错误处理

用户可见错误文案：

| 场景 | 文案 |
| --- | --- |
| 空正文点击 AI 改写 | `先写入正文或配置默认模板后再使用 AI 改写` |
| 缺默认材料 | `请选择用于匹配的材料后再使用 AI 改写` |
| 缺导师研究方向 | `请先补充导师研究方向，再使用 AI 改写` |
| 生成中重复点击 | `AI 正在改写当前草稿，请稍后刷新` |
| 模型超时 | `AI 改写超时，请稍后重试` |
| 进程中断恢复 | `AI 改写已中断，请重试` |

失败后工作区应显示源草稿，并保留错误提示。用户可以继续编辑、保存、发送或再次 AI 改写。

## 与模板模式的关系

模板模式含义保持不变：直接套用模板，不调用 LLM。

但工作区编辑器体验统一：

- 直接套用模板模式：进入工作区后显示渲染模板，用户可编辑、保存、发送。
- AI 辅助写信模式：进入工作区后同样显示渲染模板，用户可编辑、保存、发送，也可点击 AI 改写。

两者区别只在于默认建议动作：

- 模板模式推荐“检查后发送”。
- AI 模式推荐“AI 改写”。

如果用户在 AI 模式下不点击 AI 改写，也允许直接发送当前模板草稿。

## 与批量任务的关系

本规格主要面向单个工作区手动写信。批量任务已有后台草稿生成流程，不应被工作区 `/rewrite-draft` 强行替换。

批量任务项进入工作区后，如果允许人工处理，应遵循当前草稿视图规则；但批量后台 worker 仍可继续使用现有自动生成入口。后续如需统一批量 AI 改写，也应另写规格处理并发、暂停、停止和审核流。

## 测试计划

### 后端接口测试

1. 有默认模板且无历史草稿时，工作区返回渲染后的当前草稿。
2. 无默认模板且无历史草稿时，工作区返回空草稿。
3. 有保存草稿时，当前草稿优先使用保存草稿。
4. 有 AI 改写结果时，当前草稿优先使用 AI 改写结果。
5. 调用 `/rewrite-draft` 时，后端传给 LLM 的 `custom_body/custom_body_html` 来自请求体，而不是身份默认模板。
6. `/rewrite-draft` 在正文为空时返回 400，且不调用 LLM。
7. `/rewrite-draft` 开始后任务进入 `generating_draft`，并保存源草稿。
8. AI 改写成功后写入 `generated_*`，状态为 `review_required`。
9. AI 改写失败后恢复源草稿，状态恢复，`last_error` 可见。
10. AI 改写超时后恢复源草稿，状态恢复，`last_error` 为超时文案。
11. 启动恢复能恢复超时的 `generating_draft` 任务，并保留源草稿。
12. 未超过恢复阈值的 `generating_draft` 任务不会被恢复。
13. 生成中保存、发送、定时发送接口拒绝操作。

### 前端测试

1. 进入工作区时，编辑器显示后端返回的当前草稿。
2. 有模板草稿时，折叠态显示“继续写信”，AI 按钮可用。
3. 空草稿时，AI 改写按钮禁用，保存和发送按钮禁用。
4. 用户修改编辑器后点击 AI 改写，请求体包含修改后的主题、正文和附件选择。
5. AI 改写中编辑器禁用，保存、发送、定时发送、再次 AI 改写禁用。
6. AI 改写中允许路由离开，不弹保存确认。
7. 重新进入仍为生成中时，显示锁定源草稿。
8. AI 改写成功后显示新正文，按钮恢复。
9. AI 改写失败后显示源草稿和错误提示，按钮恢复。
10. 旧“生成草稿”文案在工作区替换为“AI 改写”。

## 迁移策略

1. 数据库新增字段后，旧任务无需立即迁移。
2. 工作区序列化层兼容旧数据：
   - `approved_*` 非空视为保存草稿。
   - `generated_*` 非空视为 AI 改写结果。
   - 两者都为空时尝试渲染模板。
3. 前端先兼容 `draft` 字段；如果后端暂未返回 `draft`，可临时使用旧逻辑 fallback。
4. 工作区迁移完成后，逐步删除前端复杂的本地草稿来源推导。

## 风险与取舍

- 允许用户不经 AI 直接发送模板草稿，会改变旧的“AI 模式必须先生成”的隐含流程。但这符合“编辑器内容即当前草稿”的新模型，也减少用户困惑。
- 新增源草稿字段会增加状态恢复复杂度，但这是保证离开页面和进程中断不丢内容的必要成本。
- 同步等待式 `/rewrite-draft` 仍可能让前端按钮锁定时间较长；但因源草稿已经落库，刷新或离开不会破坏状态。后续可升级为真正后台任务。
- 前端短期需要同时兼容旧字段和新 `draft` 字段；实现时应尽快收敛到后端提供的当前草稿视图。

## 推荐实施顺序

1. 新增后端当前草稿视图序列化和对应测试。
2. 新增改写源草稿字段和迁移。
3. 新增 `/rewrite-draft` 接口，先落库源草稿，再进入 `generating_draft`。
4. 实现成功、失败、超时恢复逻辑。
5. 实现启动和周期性中断恢复。
6. 前端接入 `draft` 视图，移除主要本地推导。
7. 前端将“生成草稿”改为“AI 改写”，调整按钮可用规则。
8. 补齐前后端回归测试。

## 规格自检

- 无待定项或 TODO。
- 状态机覆盖空草稿、模板初始草稿、保存草稿、AI 改写中、AI 成功、AI 失败、超时和进程中断。
- 接口契约明确要求 AI 改写使用点击瞬间的编辑器内容。
- 前端按钮可用规则与后端状态约束一致。
- 范围聚焦工作区草稿和 AI 改写，不包含批量任务整体重构。
