# 统一材料管理实现说明

## 1. 数据库迁移顺序
本次使用两段 Alembic 迁移。

### 1.1 第一段：新增统一材料结构并回填
迁移文件：`b1f4f0d34c6a_add_identity_materials.py`

执行内容：
- 新增 `identity_materials`
- `identity_profiles` 新增 `current_primary_material_id`
- `batch_tasks` 新增 `primary_material_id`、`selected_material_ids`
- `email_tasks` 新增 `primary_material_id`、`selected_material_ids`
- 把旧主简历回填成 `identity_materials(material_type='resume')`
- 把旧附件回填成 `identity_materials(material_type='other')`
- 把旧附件选择 JSON 映射成新的材料选择 JSON

### 1.2 第二段：删除旧结构
迁移文件：`c8d7e1a42b90_drop_legacy_material_fields.py`

执行内容：
- 删除 `attachment_assets`
- 删除 `identity_profiles.resume_file_path`
- 删除 `identity_profiles.resume_text`
- 删除 `batch_tasks.selected_attachment_ids`
- 删除 `email_tasks.selected_attachments`

## 2. 文件存储策略
- 新上传文件统一通过 `save_upload()` 保存。
- 磁盘文件名使用 UUID，仅保留扩展名。
- 目录结构：
  - `data/uploads/identities/{identity_id}/materials/{uuid}{ext}`
- 返回给数据库的是：
  - `file_path`
  - `original_filename`
  - `size_bytes`
  - `sha256`
- 上传阶段不主动做文本提取；`extracted_text` 由后续匹配分析或草稿生成时通过内置结构化文档提取器按需补齐。

## 3. 后端 DTO
### 3.1 `IdentityMaterialDTO`
- `id`
- `display_name`
- `original_filename`
- `mime_type`
- `size_bytes`
- `material_type`
- `is_primary`
- `created_at`

### 3.2 `IdentityDTO`
新增：
- `current_primary_material_id`
- `current_primary_material`
- `materials`

### 3.3 任务相关 DTO
- `CreateBatchTaskRequest.primary_material_id`
- `CreateBatchTaskRequest.selected_material_ids`
- `WorkspaceThreadDTO.material_options`
- `WorkspaceTaskSummaryDTO.primary_material_id`
- `WorkspaceTaskSummaryDTO.primary_material`
- `WorkspaceTaskSummaryDTO.selected_material_ids`

## 4. API 变更
### 4.1 新增接口
- `POST /api/identities/{id}/materials`
- `POST /api/materials/{id}/set-primary`
- `DELETE /api/materials/{id}`
- `GET /api/materials/{id}/open`
- `GET /api/materials/{id}/download`
- `POST /api/email-tasks/{id}/primary-material`

### 4.2 现有接口变更
- `POST /api/batch-tasks`
  - 新增 `primary_material_id`
  - `selected_attachment_ids` 改为 `selected_material_ids`
- `POST /api/email-tasks/{id}/approve-and-send`
  - `selected_attachment_ids` 改为 `selected_material_ids`
- `POST /api/email-tasks/{id}/approve-and-schedule`
  - `selected_attachment_ids` 改为 `selected_material_ids`
- `GET /api/workspaces/{professor_id}`
  - `attachment_options` 改为 `material_options`

## 5. 运行时行为
### 5.1 匹配分析与草稿生成
- 匹配分析读取：
  - `identity.current_primary_material`
- 草稿生成读取：
  - `task.primary_material`
  - `task.identity.materials`
- 系统会先通过内置 PDF/DOCX 结构化提取器对本次使用的材料做一次按需 Markdown 提取；如果文件不可提取或解析失败，则继续按“无可提取文本”处理。
- `llm_runtime.generate_match_evaluation()` 使用个人页当前默认材料生成匹配结果。
- `llm_runtime.generate_draft_content()` 使用 AI 写信参考材料生成草稿，不读取匹配结果。
- 草稿生成不再由后台 worker 自动推进，而是通过 `POST /api/email-tasks/{id}/regenerate-draft` 手动触发。
- 如果任务没有 AI 写信参考材料，草稿接口返回 400，提示用户选择 AI 写信参考材料。

### 5.2 旧任务写信材料
- `task_runtime.generate_task_draft()` 读取：
  - `task.primary_material`
  - `task.identity.materials`

### 5.3 发送
- `dispatch_email_task()` 只解析 `selected_material_ids`。
- 每个材料在发送前会转成 `MailAttachment(file_path, download_name)`。

### 5.4 切换 AI 写信参考材料
- `POST /api/email-tasks/{id}/primary-material` 会：
  - 校验材料属于当前身份
  - 校验材料可作为 AI 写信参考材料
  - 更新 `email_tasks.primary_material_id`
  - 清空已批准稿和排程

### 5.5 删除身份当前默认材料
- `DELETE /api/materials/{id}` 如果删除的是身份当前默认材料：
  - 且没有未终态任务引用该材料
  - 后端会把 `identity_profiles.current_primary_material_id` 置空
  - 身份进入“未设默认材料”状态

## 6. 前端实现
### 6.1 个人页
- 移除“主简历 + 附件”分裂区域。
- 新增统一材料列表和上传入口。
- 通过 `materials.ts` 调用文件类接口。

### 6.2 创建任务页
- 使用 `selectedIdentity.materials` 渲染材料选择。
- AI 写信参考材料单选，随信材料多选。
- 没有 AI 写信参考材料时仍允许创建任务，只是不执行 AI 草稿生成。
- 提交 payload 为 `primary_material_id` + `selected_material_ids`。

### 6.3 工作区
- 使用 `thread.material_options` 渲染材料列表。
- 支持切换 AI 写信参考材料。
- 审批发送 / 审批排程提交 `selected_material_ids`。

## 7. 验证重点
- 旧库升级后，历史主简历和附件都能映射到 `identity_materials`
- 创建批任务时，AI 写信参考材料和随信材料能正确快照到 `email_tasks`
- 工作区切换 AI 写信参考材料后可重新生成草稿
- 没有默认材料时仍可创建任务并手动发送
- 个人页和工作区都不再展示本地文件路径
