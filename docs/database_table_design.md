# 数据库表设计（当前实现）

本文档描述的是当前已经落地在 SQLite + SQLAlchemy + Alembic 中的 schema。

## 1. `app_settings`
系统级配置表。

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `id` | INTEGER PK | 固定使用 `1` |
| `created_at` | DATETIME | 创建时间 |
| `updated_at` | DATETIME | 最近更新时间 |

## 2. `identity_profiles`
发送身份表。

关键字段：
- SMTP / IMAP 配置
- `current_primary_material_id`：身份当前默认材料
- 发送节流与频率相关配置
- `is_default`

说明：
- 身份默认材料只决定“默认用于匹配和草稿生成的材料”
- 具体任务使用哪份材料，以 `email_tasks.primary_material_id` 为准

## 3. `identity_materials`
身份下的统一材料库。

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `id` | INTEGER PK | 主键 |
| `identity_id` | INTEGER FK | 所属身份 |
| `display_name` | TEXT | 用户可见名称 |
| `original_filename` | TEXT | 上传时原文件名 |
| `file_path` | TEXT | 本地存储路径 |
| `mime_type` | TEXT NULL | MIME |
| `size_bytes` | INTEGER | 文件大小 |
| `sha256` | TEXT | 摘要 |
| `extracted_text` | TEXT NULL | 按需提取并缓存的 Markdown 文本 |
| `material_type` | TEXT | `resume` / `transcript` / `publication` / `portfolio` / `other` |
| `created_at` | DATETIME | 上传时间 |

说明：
- 上传阶段只保存文件和元数据，不同步解析文本
- 只有在工作区手动执行匹配 / 生成草稿时，系统才会通过 MarkItDown 按需补齐 `extracted_text`
- 同一份材料既可以是默认材料，也可以同时被选为随信材料

## 4. `llm_profiles`
LLM 配置表。

关键字段：
- `name`
- `provider`
- `api_base_url`
- `api_key`
- `model_name`
- `temperature`
- `max_tokens`
- `is_default`

## 5. `professors`
导师主表。

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `id` | INTEGER PK | 主键 |
| `name` | TEXT | 姓名 |
| `email` | TEXT UNIQUE NULL | 导师邮箱，系统去重键 |
| `title` | TEXT NULL | 职称 |
| `university` / `school` / `department` | TEXT NULL | 学校、学院、院系 |
| `research_direction` | TEXT NULL | 研究方向 |
| `recent_papers` | JSON NULL | 近期论文标题数组 |
| `profile_url` / `source_url` | TEXT NULL | 主页与来源链接 |
| `crawl_status` | TEXT | 当前抓取状态 |
| `skip_reason` | TEXT NULL | 跳过原因 |
| `archived_at` | DATETIME NULL | 回收站时间；为空表示正常状态 |
| `created_at` / `updated_at` | DATETIME | 时间戳 |

说明：
- 归档隐藏使用 `archived_at`，不做硬删
- 首页、创建任务、工作区只使用 `archived_at IS NULL` 的导师
- 导入 `csv/xlsx` 时按邮箱覆盖已有导师；如果旧记录已归档，会自动清空 `archived_at` 恢复为正常状态

## 6. `batch_tasks`
批量任务聚合表。

关键字段：
- `identity_id`
- `llm_profile_id`
- `name`
- `schedule_type`
- `window_start_time` / `window_end_time`
- `emails_per_window`
- `status`
- `primary_material_id`
- `email_subject` / `email_body`
- `selected_material_ids`
- `target_count`

说明：
- `batch_tasks` 只负责聚合与调度，不代表单封邮件
- 创建子任务时会把 `primary_material_id` 和 `selected_material_ids` 快照到 `email_tasks`

## 7. `match_analysis_jobs`
后台批量匹配分析任务聚合表。

关键字段：
- `identity_id`
- `llm_profile_id`
- `name`
- `status`
- `target_count`
- `succeeded_count` / `failed_count` / `skipped_count`
- `total_prompt_tokens` / `total_completion_tokens` / `total_tokens`
- `cancel_requested_at`
- `started_at` / `finished_at`
- `last_error`

说明：
- `match_analysis_jobs` 只表示一次后台批量匹配分析运行，不代表邮件发送批次
- 任务中心使用它展示匹配分析任务进度、状态和 token 汇总
- 实际单次模型调用审计仍以 `match_analysis_runs` 为准

## 8. `match_analysis_job_items`
后台批量匹配分析明细表。

关键字段：
- `job_id`
- `professor_id`
- `email_task_id`
- `status`
- `match_analysis_run_id`
- `error_message`
- `skip_reason`
- `prompt_tokens` / `completion_tokens` / `total_tokens`
- `started_at` / `finished_at`

说明：
- 每条记录对应一个导师的一次批量分析项
- `email_task_id` 复用或创建对应导师任务，用于把匹配结果写回现有任务流
- `match_analysis_run_id` 关联实际模型调用审计，便于任务中心与 token 记录中心互相追踪

## 9. `email_tasks`
单导师执行单元。

关键字段：
- `source`
- `batch_task_id`
- `parent_task_id`
- `identity_id`
- `llm_profile_id`
- `professor_id`
- `primary_material_id`
- `status`
- `cancellation_reason`
- `match_score` / `match_reason`
- `generated_subject` / `generated_content_text` / `generated_content_html`
- `selected_material_ids`
- `approved_subject` / `approved_body_text` / `approved_body_html`
- `scheduled_at`
- `last_send_attempt_at`
- `sent_at`
- `last_rfc_message_id`
- `retry_count`
- `is_read` / `is_replied`
- `last_error`

说明：
- `source` 用于区分任务来源；当前实现包含 `manual` 和 `batch`
- `parent_task_id` 用于串联“继续联系”或 follow-up 创建出来的手动子任务；同一个父任务最多只允许一个手动子任务
- 同一 `identity_id + professor_id` 允许存在多条 `email_tasks`
- `primary_material_id` 是任务级快照；之后即使身份默认材料变了，旧任务也不会被动跟随
- 如果 `primary_material_id` 为空，任务仍可手动写信并发送，只是不能执行匹配和草稿生成
- `cancellation_reason` 目前用于记录明确取消原因；批量停止时会把未完成子任务置为 `canceled`，并写入 `batch_stopped`
- 执行状态主链为 `discovered -> matched -> review_required -> approved -> scheduled -> sent -> reply_detected`
- `send_failed` 是发送阶段的失败分支，不会继续流转到 `reply_detected`
- `canceled` 是显式取消态，不再把 `skipped` 当作当前执行状态
- `match_score` 只用于筛选、排序和解释，不参与是否继续执行的自动裁决

## 10. `email_logs`
工作区双向消息流水。

关键字段：
- `email_task_id`
- `identity_id`
- `llm_profile_id`
- `professor_id`
- `direction`
- `subject`
- `content`
- `content_html`
- `rfc_message_id`
- `provider_payload`
- `failure_summary`
- `reply_headers`
- `created_at`

说明：
- `draft` 日志记录模型生成草稿
- `sent` 日志记录真实发信动作
- `received` 日志仅来自 IMAP 回复检测
- 草稿日志的 `provider_payload.usage` 会记录 `prompt_tokens / completion_tokens / total_tokens`

## 11. `test_compose_sessions`
测试写信页的当前草稿会话。

关键字段：
- `identity_id`
- `llm_profile_id`
- `subject`
- `body_text`
- `body_html`
- `selected_material_ids`
- `created_at` / `updated_at`

说明：
- 每套“身份 + 模型”组合会维护一份测试写信草稿
- 这套数据不进入导师任务流

## 12. `test_compose_messages`
测试写信页的发送历史。

关键字段：
- `session_id`
- `identity_id`
- `llm_profile_id`
- `recipient_email`
- `subject`
- `content` / `content_html`
- `status`
- `rfc_message_id`
- `provider_payload`
- `failure_summary`
- `created_at`

说明：
- 测试邮件固定发给当前身份自己的邮箱
- 历史与 `email_logs` 分离保存，不污染导师通信记录

## 13. 导师导入与归档规则
- 模板字段固定为：
  - `name`
  - `email`
  - `title`
  - `university`
  - `school`
  - `department`
  - `research_direction`
  - `recent_papers`
  - `profile_url`
  - `source_url`
- `recent_papers` 在模板中使用 `|` 分隔多篇论文标题
- 导入时：
  - `name` 和 `email` 必填
  - 邮箱格式错误或必填缺失记为失败，但不影响整批导入
  - 数据库已有同邮箱导师时执行覆盖更新，不跳过
  - 如果旧导师处于归档状态，会在导入后自动恢复

## 14. `thinking_adaptation_cache`

思考模式 LLM 协议自适应缓存表。按 `(api_base_url, model_name)` 维度记录每个具体模型一次性"学到"的请求方式，避免每次抓取都被「思考模式协议错」（`reasoning_content must be passed back`）打断。

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `id` | INTEGER PK | 自增主键 |
| `api_base_url` | VARCHAR(500) NOT NULL | 模型 API 入口（已 strip 末尾 `/`） |
| `model_name` | VARCHAR(255) NOT NULL | 模型 ID（如 `deepseek-chat`、`qwen3-32b`） |
| `learned_extra_body` | JSON NULL | 探活成功时使用的 `extra_body` 字典；NULL 表示该模型无需 `extra_body`（已确认） |
| `probed_at` | DATETIME NOT NULL | 上次探活完成的时间 |
| `created_at` | DATETIME NOT NULL | 行创建时间 |
| `updated_at` | DATETIME NOT NULL | 最后一次更新时间，行更新时由 ORM 自动写 |

唯一约束：`(api_base_url, model_name)`，索引：`model_name`。

### 维护策略

- 缓存由后端自动维护，不暴露给前端。
- 学习成果按"模型本身"持久化，与具体的 `LLMProfile` 行解耦：
  - 用户在同一 `LLMProfile` 里改 `model_name` 到新模型 → 老模型缓存保留，新模型独立探活。
  - 用户改回老 `model_name` → 直接命中老缓存，零成本。
  - 多个 `LLMProfile` 用同一个模型 → 共享同一行缓存。
- 抓取启动前会调用 `app.services.thinking_adaptation.ensure_thinking_adaptation`：缓存命中直接返回，未命中则触发一次"3 轮多轮探活 + 候选 `extra_body` 切换"，找到的值随当前 session 的 commit 一起落库。
- 测活路径 `POST /api/llm-profiles/{id}/test` 在单轮探活成功后也会触发 `ensure_thinking_adaptation`，让用户在保存模型后第一次点测试时就能完成学习；预览测活 `POST /api/llm-profiles/preview/test` 不传 session，不会写缓存。
- 候选 `extra_body` 列表见 `app/services/thinking_adaptation.py` 的 `THINKING_DISABLE_CANDIDATES`（当前覆盖 `thinking={"type":"disabled"}` / `enable_thinking=False` / `reasoning={"effort":"off"}` / `thinking_budget=0`）。
- 如果候选列表全部用尽仍失败，会抛 `ThinkingAdaptationFailed`，抓取任务被标 FAILED，并在 `error_message` 中提示用户在 GitHub 上报。
- 抓取过程中如果 LangChain 内部仍然吐出协议错（极端边角场景），`_mark_job_failed` 与 `_complete_running_job` 两条路径都会调用 `adapt_failure_message_for_thinking_error`，给用户拼一段引导提示。


## `crawl_page_chunks`

列表页 chunk 处理状态表，用于让抓取 Agent 每次只处理一个页面片段，避免重复处理完整页面。

| 字段 | 类型 | 说明 |
| ---- | ---- | ---- |
| `id` | INTEGER | 主键 |
| `job_id` | INTEGER | 抓取任务 ID |
| `page_id` | INTEGER NULL | 来源页面 ID |
| `source_url` | TEXT | 来源页面 URL |
| `page_fingerprint` | TEXT | 页面内容指纹 |
| `chunk_id` | TEXT | 单任务内稳定 chunk 标识 |
| `parent_chunk_id` | TEXT NULL | 父 chunk ID |
| `chunk_index` | INTEGER | 同级顺序 |
| `chunk_hash` | TEXT | chunk 内容指纹 |
| `status` | TEXT | `pending`、`processing`、`completed`、`no_candidates`、`split_required`、`superseded`、`failed` |
| `content` | TEXT | 链接增强文本 |
| `token_estimate` | INTEGER | token 估算 |
| `text_start_offset` | INTEGER NULL | 页面文本起始位置 |
| `text_end_offset` | INTEGER NULL | 页面文本结束位置 |
| `overlap_prefix` | BOOLEAN | 是否包含前向 overlap |
| `overlap_suffix` | BOOLEAN | 是否包含后向 overlap |
| `split_depth` | INTEGER | 拆分深度 |
| `split_reason` | TEXT NULL | 拆分原因 |
| `attempt_count` | INTEGER | 处理尝试次数 |
| `last_error` | TEXT NULL | 最近错误 |
| `created_at` | DATETIME | 创建时间 |
| `updated_at` | DATETIME | 更新时间 |
