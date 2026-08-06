# Crawler v2 详情页模式整页抽取设计

## 背景

智能抓取目前已经统一进入 v2 调度器。用户选择「详情页模式」并输入多个 URL 时，系统会把这些 URL 创建为 `CrawlPageTask`，再经过 page worker 和 chunk worker。这个流程能运行，但语义上仍偏向列表页发现：页面会被切分成多个 chunk，chunk worker 负责从片段中发现候选。

详情页模式的用户意图不同。用户输入的每个 URL 已经是某位老师的个人详情页，系统不需要在该页面中继续发现列表页或分页，也不需要把页面切成列表 chunk 后再猜候选。更合理的做法是抓取整页内容，直接调用模型抽取一个候选导师。

本规格定义 v2 详情页模式的目标行为、状态流转和边界控制，重点保证暂停可恢复、终止可停止、转入待审核后不会后台继续跑。

## 目标

- `entry_type="profile"` 时，每个用户输入的详情页 URL 作为一个独立详情页抽取单元处理。
- 详情页模式不创建 `CrawlPageChunk`，不走列表页 chunk discovery。
- 详情页模式使用整页文本调用 LLM，从单个详情页抽取 0 或 1 位候选导师。
- 用户输入的详情页 URL 必须保留为候选 `profile_url`，不能被列表页 URL 防污染规则清空。
- 暂停、恢复、取消、转入待审核都必须遵守 v2 worker 的租约和状态机，不留下继续运行的后台工作。

## 非目标

- 不在后端解析不同学校的 HTML 结构来判断字段归属。
- 不把详情页模式改成直接复用现有 enrichment worker。
- 不在详情页模式里自动发现分页、目录页或相关教师链接。
- 不改变列表页模式现有 page/chunk/discovered URL 流程。

## 设计原则

1. **LLM 负责页面理解。** 后端只提供整页文本、任务上下文和明确的输出 schema，不根据页面结构推断姓名、邮箱或主页。
2. **任务语义决定 URL 角色。** `entry_type="profile"` 的 `start_urls` 是详情页；`entry_type="list"` 的 `start_urls` 是列表页入口。
3. **状态先于提交。** 任何耗时 LLM 调用前后都要检查任务和 work item 状态，防止暂停、取消或转入待审核后继续写入结果。
4. **单页单候选。** 一个详情页最多保存 1 个候选。模型认为页面不是个人详情页时，不保存候选。

## 目标流程

### 创建任务

`POST /api/crawl-jobs` 保持现有行为：

- `runtime_version` 固定为 `v2`。
- `start_urls` 中的每个 URL 创建一条 `CrawlPageTask`。
- `entry_type` 保存为 `profile` 或 `list`。

### 调度流程

v2 scheduler 继续领取 `CrawlPageTask`。page worker 在成功抓取页面后按任务类型分支：

```text
entry_type=list
-> 保存 CrawlPage
-> 创建 CrawlPageChunk
-> 后续 chunk worker 抽取候选和 discovered_urls

entry_type=profile
-> 保存 CrawlPage
-> 不创建 CrawlPageChunk
-> 直接整页调用 profile extraction
-> 保存 0 或 1 个 CrawlCandidate
-> 标记 CrawlPageTask succeeded 或 failed_terminal
```

不新增第四种 worker。详情页整页抽取放在 `crawler_v2_page_worker` 或其拆出的 helper 中执行，保持调度器模型不变。

## 与 v1 详情页模式的机制对齐

v1 的 `_run_profile_crawl_job` 已经提供了几个需要保留的机制，v2 详情页模式实现时不能遗漏：

- 抓取前后都调用任务状态检查，防止暂停或取消后继续提交。
- 抓取详情页时使用 profile 意图（`intent="profile"`），不要退化成 generic 页面抓取。
- LLM 结构化输出支持重试。第一次输出为空或 JSON 结构不合法时，使用结构化重试提示在同一个 page worker 内重新调用，最多沿用 `DIRECT_LLM_STRUCTURED_MAX_ATTEMPTS`。这不是 chunk worker 的 `too_many_candidates` 拆分，也不是立刻把 page task 丢回队列。
- 每次直接 LLM 响应都要记录 token 用量。v2 实现应写入 `crawl_worker_token_usages`，并确保当前 run 的 token 统计能汇总到任务摘要。
- 抽取成功和失败都要写 debug/trace 事件，便于安装版日志排查。
- 模型返回候选但缺少 `name` 时视为抽取失败。
- 模型上下文必须附带用户输入的学校、学院。模型漏填 `university`、`school`、`profile_url`、`source_url` 时，后端用任务上下文和当前详情页 URL 兜底。
- 候选保存继续复用共享保存逻辑和保存失败预算，不新增绕过查重/字段规范化的保存路径。

## LLM 思考模式适配

详情页整页抽取必须复用现有 `ensure_thinking_adaptation` 流程：

- 在 page worker 领取 `entry_type="profile"` 的任务后，先解析 `LLMProfile`。
- 调用 `ensure_thinking_adaptation(session, llm_profile)` 获取 `thinking_extra_body`。
- 构建模型时通过 `build_faculty_crawler_model(llm_profile, extra_body=thinking_extra_body)` 传入。
- 不在详情页抽取路径里硬编码某个模型厂商的禁用参数。
- 复用现有缓存表 `thinking_adaptation_cache`，避免每个详情页重复探活。

这与现有 v2 chunk worker 和 enrichment worker 保持一致，确保思考模型在多轮或结构化输出场景下不会因为 `reasoning_content` / thinking block 协议错误导致抓取失败。

## Profile extraction 输入与输出

### 输入

- 学校：用户创建任务时输入的 `job.university`
- 学院：用户创建任务时输入的 `job.school`
- 当前页面 URL：优先使用最终抓取 URL，保留原始 task URL 作为回退
- 页面标题
- 整页文本
- 可选 HTML 摘要或链接列表，仅作为辅助证据

整页文本可以复用现有 page snapshot 的文本截断策略，避免超过模型上下文。详情页模式不使用 chunk 分割。

### 输出 schema

```json
{
  "status": "candidate | no_candidate",
  "candidate": {
    "name": "",
    "email": "",
    "title": "",
    "university": "",
    "school": "",
    "department": "",
    "research_direction": "",
    "recent_papers": [],
    "profile_url": "",
    "source_url": "",
    "confidence": 0.0,
    "field_confidence": {},
    "evidence": {}
  }
}
```

规则：

- `status="candidate"` 时必须有 `name`。
- `profile_url` 强制使用当前详情页 URL；如果模型漏填，后端用当前详情页 URL 补上。
- `source_url` 强制使用当前详情页 URL。
- 不输出 `discovered_urls`。
- 如果页面更像列表页、搜索结果页、新闻页或登录页，返回 `no_candidate`。

## 保存规则

详情页模式保存候选时使用现有候选保存路径，但传入 profile 模式上下文：

- `source_kind="profile_page"`
- `source_url=当前详情页 URL`
- `profile_url=当前详情页 URL`
- `boundary_risk=false`，除非模型明确表示页面归属不确定

列表页 URL 防污染规则需要按任务语义区分：

- `entry_type="list"`：`start_urls`、已有 `CrawlPageTask`、本次 `discovered_urls` 都可以作为非个人主页 URL，用于清空候选 `profile_url`。
- `entry_type="profile"`：`start_urls` 是用户输入的详情页，不能作为非个人主页 URL。详情页模式默认不处理 `discovered_urls`。

## 暂停与恢复

### 暂停时

暂停接口应继续把 `processing` 状态的 v2 work item 释放回可恢复状态：

- `CrawlPageTask.processing -> pending`
- `CrawlPageChunk.processing -> pending`
- `CrawlCandidateEnrichmentTask.processing -> pending`

详情页整页抽取运行在 page worker 中，因此暂停时重点是 `CrawlPageTask`。

如果暂停发生在 LLM 调用期间，无法强行中断外部请求时，worker 在 LLM 返回后必须再次检查：

- job 仍是 `queued` 或 `running`
- 当前 `CrawlPageTask` 仍归属该 worker
- 当前 `CrawlPageTask.status == processing`

任一条件不满足，不写入候选，不标记成功，直接返回。

### 恢复时

恢复后 scheduler 重新领取 `pending` 或租约过期的 `failed_retryable` work item。

详情页模式必须满足：

- 已成功的 page task 不重复抽取。
- 已保存候选的页面不因恢复重复保存同一候选。
- 之前未提交结果的 page task 可重新抓取和抽取。

## 取消与终止

取消任务时：

- job 状态变为 `canceled`。
- 所有 processing work item 释放或终止，后续 scheduler 不再领取，因为 scheduler 只处理 `queued/running` job。
- 正在进行的 LLM 调用返回后必须通过提交前状态检查阻止写入。

失败终止时：

- 单个详情页抓取失败可标记该 `CrawlPageTask` 为 `failed_retryable` 或 `failed_terminal`，遵循现有重试策略。
- 全部详情页都处理完且没有候选时，job 最终为 `failed`，错误信息为「抓取未发现候选导师」。
- 至少有一个候选时，job 最终为 `needs_review`。

## 转入待审核

`resume-review` 或同等「转入待审核」动作必须冻结发现和抽取工作：

- `CrawlPageTask.pending/processing/failed_retryable -> failed_terminal`
- `CrawlPageChunk.pending/processing/split_required/failed_retryable -> failed_terminal`
- 不自动创建新的详情页抽取工作。

对于详情页模式，这意味着剩余未处理详情页不再继续抓取。正在执行的 page worker 在提交前检查到 job 已不在 active 状态后，不写入候选。

用户进入待审核后点击「补全」，只允许处理已存在候选的 enrichment task，不允许恢复之前被冻结的详情页抽取任务。

## 与 enrichment worker 的关系

详情页整页抽取和 enrichment worker 都会把整页文本交给模型，但职责不同：

- **profile extraction**：从用户输入的详情页创建候选，必须识别 `name`。
- **enrichment**：对已存在候选补充缺失字段，不创建候选。

因此不直接复用 `CrawlCandidateEnrichmentTask` 作为初始详情页抽取任务。后续用户在待审核阶段选择候选补全时，仍使用现有 v2 enrichment worker。

## 直接结构化 LLM 调用

详情页整页抽取应提取一个专用 helper，例如 `invoke_v2_profile_extraction_agent`。该 helper 的重试语义分为两层：

- **结构化输出重试：** 模型返回空内容、非法 JSON 或 schema 校验失败时，在同一个 page worker 内追加结构化重试提示并再次调用 LLM。
- **work item 级重试：** 网络异常、抓取失败、LLM API 异常、租约过期等运行失败，才交给 v2 page task 的 `failed_retryable` / backoff / `attempt_count` 机制。

该 helper 负责：

- 构建 profile extraction prompt。
- 通过 `build_faculty_crawler_model(..., extra_body=thinking_extra_body)` 创建模型。
- 调用模型并提取响应文本。
- 使用 `parse_structured_result` 解析 schema。
- 在空响应或解析失败时追加结构化重试提示，再调用一次或多次。
- 为每次 LLM attempt 收集 `raw_model_text`、解析结果和 token usage。
- 返回最终结构化 payload、累计 token usage、每次 attempt 的调试信息。

不要把这段逻辑塞进 page worker 主函数。page worker 只负责状态检查、抓取、调用 helper、保存结果和标记 task 状态。

## Token 统计

v1 通过 `_accumulate_direct_llm_response_tokens` 把详情页抽取的直接 LLM 调用计入当前 crawl run。v2 详情页抽取应使用与 chunk worker 一致的 v2 token 记录机制：

- `worker_kind = CrawlWorkerKind.PAGE` 或新增清晰的 profile extraction 事件名，但不新增数据库枚举。
- `work_item_id = CrawlPageTask.id`。
- `model_name = llm_profile.model_name`。
- 记录 `input_tokens`、`output_tokens`、`cached_tokens` 和原始 usage。
- 如果结构化输出重试调用了多次 LLM，token usage 在 helper 内累计后记录为同一个 page task 的用量。

任务摘要依赖 run/token 汇总时，必须能看到详情页抽取消耗，不能只记录 debug JSONL。

## 抓取意图

`entry_type="profile"` 的页面抓取必须使用 profile 意图：

- 直接 HTTP 抓取仍可复用现有 direct fetch。
- browser fallback 应使用 `intent="profile"`，让 Crawl4AI/browser 配置按详情页场景等待和提取。
- 页面抓取日志应保留 `fetch_mode`、`direct_status`、`fallback_reason`、`browser_status`，与现有 page worker 一致。

## 错误处理

- 抓取失败：沿用 page worker 的 retry/backoff 逻辑。
- LLM 输出无效 JSON：当前 page task 标记为 `failed_retryable`。
- LLM 返回 `no_candidate`：当前 page task 标记为 `failed_terminal`，错误信息说明「详情页未识别到导师候选」。
- LLM 返回候选但缺少姓名：视为 `no_candidate`。
- 候选保存失败：遵循现有保存失败预算和错误记录策略。

## 调试日志

v2 debug JSONL 增加详情页抽取事件。每次 LLM attempt 都要记录模型返回，便于排查结构化重试问题。

- `profile_extract_requested`
- `profile_extract_llm_response`（每次 attempt 一条）
- `profile_extract_completed`
- `profile_extract_no_candidate`
- `profile_extract_skipped_inactive`

事件中记录：

- `job_id`
- `task_id`
- `source_url`
- `status`
- `candidate_name`
- `profile_url`
- `attempt_number`
- `raw_model_text`（LLM 返回内容，按现有 debug 截断策略保存）
- `raw_payload`（解析后的结构化结果，解析失败时记录错误）
- token usage（如可用）
- page text 摘要、hash 和长度；不完整保存整页输入原文

## 测试要求

### 单元测试

- `entry_type="profile"` 的 page worker 抓取成功后不创建 chunk。
- `entry_type="profile"` 的 page worker 对整页调用 profile extraction，并保存 1 个候选。
- 模型漏填 `profile_url` 时，后端补为当前详情页 URL。
- 模型返回 `no_candidate` 时不保存候选，page task 终止。
- `entry_type="list"` 的现有 chunk 流程保持不变。
- 列表页 URL 防污染规则不清空 profile 模式的 `start_url`。
- 列表页 URL 防污染规则仍清空 list 模式的 `start_url` 和 discovered URL。

### 状态流转测试

- LLM 调用期间 job 被暂停，LLM 返回后不写入候选，任务可恢复。
- LLM 调用期间 job 被取消，LLM 返回后不写入候选，任务保持取消。
- 转入待审核后，未处理的 profile page task 被 terminal，后台 worker 不再写入候选。
- 多个 profile URL 中一个失败不阻塞其他 URL 处理。
- 全部失败且无候选时 job 进入 failed；存在候选时进入 needs_review。

### 回归测试

- 多详情页 URL 每个最多保存 1 个候选。
- 详情页模式不会产生 `discovered_urls` 或新 page task。
- 审核阶段补全仍创建 `CrawlCandidateEnrichmentTask` 并由 enrichment worker 执行。

## 迁移影响

无需数据库结构变更。现有表足够表达该流程：

- `crawl_jobs.entry_type`
- `crawl_page_tasks`
- `crawl_pages`
- `crawl_candidates`
- `crawl_worker_token_usages`

实现时可以新增 Python helper 和测试，不需要 Alembic migration。

## 验收标准

- 用户选择详情页模式并输入多个 URL 后，每个 URL 独立抽取候选。
- 详情页模式不创建 chunk，不继续发现新 URL。
- 候选 `profile_url` 保留用户输入的详情页 URL。
- 暂停后可恢复未完成详情页抽取。
- 取消后不再写入新候选。
- 转入待审核后后台不继续抓取或抽取详情页。
- 现有列表页模式和补全流程测试通过。