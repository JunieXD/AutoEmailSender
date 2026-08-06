# 抓取任务后续运行使用当前选中模型设计

## 背景

当前抓取任务在创建时会把前端选中的 `llm_profile_id` 写入 `crawl_jobs.llm_profile_id`。后续补全候选信息时，后端优先读取任务自身绑定的 `llm_profile_id`，因此即使用户在顶部导航切换了当前选中模型，已有任务的补全仍会使用创建任务时绑定的模型。

用户期望调整为：当用户明确触发某个抓取任务进入下一次运行时，该任务应使用前端当前选中的模型；已经在运行中的任务不受切换影响。

## 目标

- 用户切换顶部导航中的当前选中模型后，不自动修改已有抓取任务。
- 当用户触发非运行中抓取任务进入下一次运行时，任务绑定模型刷新为当前选中模型。
- 已经处于 `running` 的任务保持本轮运行启动时的模型，不进行热切换。
- 已经处于 `queued` 但尚未开始运行的任务仍使用入队时写入的模型，不因用户后续切换顶部选中模型而改变。
- 刷新任务绑定模型时保留审计记录，便于排查后续运行实际使用的模型。

## 非目标

- 不引入后端全局「当前选中模型」状态。
- 不把顶部导航的当前选中模型等同于 `llm_profiles.is_default`。
- 不批量更新所有非运行任务的 `crawl_jobs.llm_profile_id`。
- 不支持运行中任务热切换模型。
- 不重跑已经完成的模型调用，也不重新计算历史 token 统计。

## 术语

- **当前选中模型：** 前端 `SelectionContext` 中的 `selectedLlmProfileId`，持久化在浏览器或 Electron WebView 的 `localStorage` 中。
- **默认模型：** 后端 `llm_profiles.is_default = true` 的模型，仅作为无本地选择时的兜底和列表排序依据。
- **任务绑定模型：** `crawl_jobs.llm_profile_id`，表示抓取任务下一次运行实际应使用的 LLM Profile。
- **进入下一次运行：** 用户触发会让抓取任务从非运行态进入执行流程的动作，例如继续、重试、补全缺失信息。

## 现有行为

1. 前端顶部导航切换模型时，只更新 `selectedLlmProfileId` 并写入 `localStorage`。
2. 创建抓取任务时，前端把当时的 `selectedLlmProfileId` 作为 `llm_profile_id` 传给后端。
3. 后端创建任务后将该值保存到 `crawl_jobs.llm_profile_id`。
4. 后续补全接口优先读取 `job.llm_profile_id`，只有任务没有绑定模型时才回退到后端默认模型。
5. 因此，已有任务不会自动跟随顶部导航后来选中的模型。

## 目标行为

### 创建抓取任务

- 创建时继续使用当前前端选中的模型。
- 后端保存该模型到 `crawl_jobs.llm_profile_id`。
- 任务进入 `queued` 后，即使用户切换顶部选中模型，该 queued 任务仍使用入队时的模型。

### 补全缺失信息

- 用户点击「补全缺失信息」时，前端必须把当前 `selectedLlmProfileId` 传给后端。
- 后端在任务进入 `running` 前校验并刷新 `crawl_jobs.llm_profile_id`。
- 本次补全使用刷新后的模型。
- 如果任务已经是 `running`，后端继续返回现有冲突错误，不刷新模型。

### 继续抓取任务

- 用户点击「继续」时，前端必须把当前 `selectedLlmProfileId` 传给后端。
- 后端在任务重新入队或恢复运行前刷新 `crawl_jobs.llm_profile_id`。
- 本次继续运行使用刷新后的模型。

### 重试抓取任务

- 用户点击「重试」时，前端必须把当前 `selectedLlmProfileId` 传给后端。
- 后端在创建重试运行记录或重新入队前刷新 `crawl_jobs.llm_profile_id`。
- 本次重试使用刷新后的模型。

### 转入待审核

- 「转入待审核」本身不启动模型调用，不需要刷新任务绑定模型。
- 如果用户随后点击「补全缺失信息」，再按补全流程刷新。

## API 设计

### 通用字段

为会触发下一次运行的请求增加可选字段：

```json
{
  "llm_profile_id": 2
}
```

字段规则：

- `llm_profile_id` 必须指向存在的 `LLMProfile`。
- 前端正常情况下必须传入当前 `selectedLlmProfileId`。
- 后端为兼容旧客户端，允许该字段为空；为空时保留现有行为，即使用任务当前绑定模型，任务无绑定时回退默认模型。

### 受影响接口

- `POST /api/crawl-jobs/{job_id}/enrich`
  - 请求体从 `{ candidate_ids }` 扩展为 `{ candidate_ids, llm_profile_id? }`。
- `POST /api/crawl-jobs/{job_id}/resume`
  - 请求体从空请求扩展为可选 `{ llm_profile_id? }`。
- `POST /api/crawl-jobs/{job_id}/retry`
  - 请求体从 `{ clear_existing_data }` 扩展为 `{ clear_existing_data, llm_profile_id? }`。

## 后端设计

### 模型刷新规则

新增内部辅助逻辑，用于在任务进入下一次运行前解析并刷新模型：

1. 如果请求传入 `llm_profile_id`：
   - 校验模型存在。
   - 如果任务状态允许进入下一次运行，更新 `crawl_jobs.llm_profile_id`。
   - 如果新旧模型不同，记录操作日志。
2. 如果请求未传入 `llm_profile_id`：
   - 使用任务当前 `llm_profile_id`。
   - 如果任务没有绑定模型，回退到 `is_default = true` 的模型。
3. 如果最终无法解析出模型，返回 `409`：`请先配置可用的 LLM Profile`。

### 状态约束

- `running`：拒绝进入下一次运行相关动作，不刷新模型。
- `queued`：不因用户切换顶部模型而自动刷新；如果接口本身允许对 queued 任务执行动作，应按该动作原有状态校验处理。
- `needs_review`、`partially_completed`：允许补全前刷新。
- `paused`：允许继续前刷新。
- `failed`、`canceled`：允许重试或转待审核后的补全流程刷新。

### 审计日志

当 `crawl_jobs.llm_profile_id` 因用户触发下一次运行而变化时，写入 `operation_logs`：

- `category`: `crawler`
- `event_name`: `crawl_job.llm_profile_refreshed`
- `entity_type`: `crawl_job`
- `entity_id`: 抓取任务 ID
- `metadata`:

```json
{
  "old_llm_profile_id": 1,
  "old_model_name": "deepseek-v4-flash",
  "new_llm_profile_id": 2,
  "new_model_name": "mimo-v2.5",
  "trigger": "enrich"
}
```

`trigger` 可取值：`enrich`、`resume`、`retry`。

## 前端设计

### 数据流

- 继续使用 `SelectionContext.selectedLlmProfileId` 作为当前选中模型来源。
- 不新增后端「当前选中模型」接口。
- 不改变 `is_default` 的含义。

### API 客户端

- `enrichCrawlCandidates(jobId, candidateIds)` 增加 `llmProfileId` 参数。
- `resumeCrawlJob(jobId)` 增加 `llmProfileId` 参数。
- `retryCrawlJob(jobId, payload)` 的 payload 增加 `llmProfileId`。

### 页面交互

- 用户点击「补全缺失信息」时，传入当前 `selectedLlmProfileId`。
- 用户点击「继续」时，传入当前 `selectedLlmProfileId`。
- 用户点击「重试」时，传入当前 `selectedLlmProfileId`。
- 如果当前没有选中模型，按钮应禁用或给出错误提示：`请先选择模型配置`。

### 提示文案

在抓取任务详情中可展示当前任务绑定模型，辅助用户理解后续运行会使用哪个模型。建议文案：

> 下次运行将使用当前顶部选中的模型；已在运行中的任务不会中途切换。

该展示不是本次功能的硬性要求，可以作为后续体验优化。

## 兼容性

- 旧客户端不传 `llm_profile_id` 时，后端保持现有行为。
- 历史抓取任务保留原有 `crawl_jobs.llm_profile_id`。
- 已经在 `queued` 状态的任务不会因为本次改动自动刷新模型。
- `llm_profiles.is_default` 继续作为兜底默认值，不承担当前选中模型职责。

## 错误处理

- 传入不存在的 `llm_profile_id`：返回 `404` 或 `400`，提示 `模型配置不存在`。
- 当前未选择模型：前端阻止操作并提示 `请先选择模型配置`。
- 任务正在运行：保持现有 `409` 行为，提示任务正在运行或正在补全。
- 没有可用模型配置：返回 `409`，提示 `请先配置可用的 LLM Profile`。

## 测试计划

### 后端测试

- 补全接口传入新 `llm_profile_id` 时，任务绑定模型更新并使用新模型。
- 补全接口未传 `llm_profile_id` 时，保持旧行为。
- 运行中任务调用补全接口时不刷新模型并返回冲突错误。
- 继续 paused 任务时，传入模型会刷新任务绑定模型。
- 重试 failed 或 canceled 任务时，传入模型会刷新任务绑定模型。
- 传入不存在的模型 ID 时返回明确错误。
- 模型刷新时写入 `crawl_job.llm_profile_refreshed` 操作日志。

### 前端测试

- 点击「补全缺失信息」时，请求体包含当前 `selectedLlmProfileId`。
- 点击「继续」时，请求体包含当前 `selectedLlmProfileId`。
- 点击「重试」时，请求体包含当前 `selectedLlmProfileId`。
- 当前没有选中模型时，相关按钮禁用或显示错误提示。

## 验收标准

- 用户切换顶部选中模型后，已有非运行任务不会立即变更数据库中的 `crawl_jobs.llm_profile_id`。
- 用户随后对该任务点击「补全缺失信息」后，后端先把 `crawl_jobs.llm_profile_id` 更新为当前选中模型，再执行补全。
- 运行中的任务不会因为用户切换顶部选中模型而改变本轮模型。
- 已经 queued 但未开始的任务仍使用入队时模型。
- 操作日志能看出某次后续运行前任务绑定模型是否发生过刷新。