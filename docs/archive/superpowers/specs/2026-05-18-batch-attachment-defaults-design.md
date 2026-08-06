# 批处理随信附件默认值设计

## 背景

用户反馈：批处理审核草稿时，「随信附件」自动勾选结果每封都不一样，不会保留创建批处理时的选择。

当前原因是 AI 生成草稿会返回 `suggested_material_ids`，后端在生成完成后用该字段覆盖单封任务的 `selected_material_ids`。这会让批处理创建时的附件选择失去稳定性。

## 目标

- 新建批处理时，「随信附件」默认空。
- 用户在创建批处理时勾选的附件，作为该批次固定默认附件。
- 批次下每封邮件生成草稿后，审核弹窗默认沿用批次创建时的附件。
- 用户在审核某一封邮件时手动修改附件，只影响该封邮件。
- 单封邮件的附件修改不反向更新批次默认附件，也不影响同批其他邮件。
- 如果创建批处理时未选择附件，则该批次每封邮件默认附件为空。
- AI 不再建议或自动选择附件。

## 非目标

- 不增加身份级「默认随信附件」。
- 不记住上一次新建批处理时的附件选择。
- 不改变材料库、主分析材料和附件发送解析逻辑。
- 不调整批处理发送节奏、草稿审核流程和邮件正文生成策略。

## 行为规则

### 创建批处理

- 前端保持现状：进入创建页时 `selectedMaterialIds` 初始化为空。
- 用户提交批处理时：
  - 有勾选附件：提交 `selected_material_ids: number[]`。
  - 未勾选附件：提交 `selected_material_ids: null`。
- 后端创建 `BatchTask` 时保存该值到 `batch_tasks.selected_material_ids`。
- 后端创建每个 `EmailTask` 时把同一份值写入 `email_tasks.selected_material_ids`。

### 生成草稿

- LLM 返回结果不再包含 `suggested_material_ids`。
- Prompt 不再要求模型选择附件。
- 后端生成草稿后不再根据 AI 输出更新 `task.selected_material_ids`。
- 批处理子任务保留创建时写入的 `selected_material_ids`。
- 非批处理任务如果没有人工选择附件，则继续保持 `null` 或空列表语义，不由 AI 自动填充。

### 审核和发送

- 审核弹窗继续从当前 `EmailTask.selected_material_ids` 初始化勾选状态。
- 用户审核通过、立即发送或定时发送时，提交当前弹窗里的附件选择。
- 后端只更新当前 `EmailTask.selected_material_ids`。
- `BatchTask.selected_material_ids` 不变。

## 数据模型影响

- 不新增数据库字段。
- 保留现有字段：
  - `batch_tasks.selected_material_ids`
  - `email_tasks.selected_material_ids`
- 移除或停用 LLM 结果模型中的 `suggested_material_ids` 字段。
- 移除日志和 provider payload 中与 AI 附件建议相关的字段。

## 后端改动范围

### `backend/app/services/llm_runtime.py`

- 从草稿生成结果 schema 中移除 `suggested_material_ids`。
- 清理系统提示词和用户提示词中关于附件选择的要求。
- 清理示例 JSON 中的 `suggested_material_ids`。
- 清理结果归一化和校验逻辑中针对 `suggested_material_ids` 的代码。

### `backend/app/services/task_runtime.py`

- 草稿生成完成后不再读取 `generation.result.suggested_material_ids`。
- 不再用 AI 返回值覆盖 `task.selected_material_ids`。
- 模板模式下可继续沿用已有任务字段，不需要额外设置。
- `email_task.draft_generated` 操作日志可继续记录最终 `task.selected_material_ids`，用于排查当前任务真实附件选择。
- `provider_payload` 不再记录 `suggested_material_ids`。

### `backend/app/services/test_compose_runtime.py`

- 测试写信生成草稿后不再从 LLM 结果同步附件建议。
- 测试写信附件只由用户在测试写信界面手动选择和提交。

### `backend/app/schemas/email_task.py`

- 移除与 `suggested_material_ids` 相关的响应字段（如果仅用于 LLM 结果传递）。
- 保留审批 payload 的 `selected_material_ids`。

## 前端改动范围

前端主流程不需要新增 UI。需要检查并清理：

- 是否存在展示 AI 建议附件的文案或字段。
- 是否有类型定义引用 `suggested_material_ids`。
- 批处理审核弹窗继续使用 `current_task.selected_material_ids`。
- 创建批处理页继续默认空，不引入历史记忆。

## 兼容性

- 历史批处理和历史单封任务中已保存的 `selected_material_ids` 继续有效。
- 历史 `EmailLog.provider_payload` 中可能仍包含 `suggested_material_ids`，不做迁移。
- 新生成的 provider payload 不再写入该字段。
- 数据库 schema 无需迁移。

## 测试计划

### 后端单元测试

- 批处理创建时选择附件 `[A, B]`，生成草稿后任务仍保留 `[A, B]`。
- 批处理创建时未选择附件，生成草稿后任务仍为空。
- 审核某一封时提交 `[C]`，仅该封任务变为 `[C]`，批次默认仍为 `[A, B]`。
- LLM 结果模型不再接受或不再依赖 `suggested_material_ids`。

### 前端测试

- 创建批处理页面初始附件为空。
- 提交批处理时只提交用户勾选的附件。
- 批处理审核弹窗使用当前任务的 `selected_material_ids` 初始化。

### 回归验证

- 运行后端相关 unittest。
- 运行前端相关 Vitest。
- 视改动范围运行 `frontend` lint。

## 验收标准

- 同一批处理内，创建时选择的附件在每封草稿审核时保持一致。
- AI 生成不同邮件内容不会改变附件勾选。
- 单封审核手动调整附件只影响当前邮件。
- 新建批处理页面每次默认没有勾选附件。
- 代码中不再存在面向 LLM 的 `suggested_material_ids` 提示、schema 和覆盖逻辑。
