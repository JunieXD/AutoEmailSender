# 抓取任务运行指标设计

## 背景

最新一次智能抓取暴露出两个指标问题：

- 重试后的“已耗时长”仍从任务首次创建时间开始计算，导致重启抓取后显示的耗时远大于本轮实际运行时间。
- Token 统计从 `crawl_jobs.agent_trace` 的滚动日志中反推；当后续详情补全事件挤掉早期 LLM usage 事件后，前端显示的 token 会变成 0 或明显偏低。

两个问题的共同根因是：系统把业务任务、执行尝试、展示日志和运行指标混在同一条 `crawl_jobs` 记录上，通过 `created_at`、`updated_at` 和滚动 `agent_trace` 临时推导指标。这些字段本身不是可靠的指标来源。

## 目标

- 暂停后继续算同一次运行，不创建新的执行尝试。
- 只有失败或取消后的“重试”才创建新的执行尝试。
- “已耗时长”只统计当前执行尝试的活跃运行时间，不包含任务创建后等待、暂停、审核、取消后的日志追加等时间。
- Token 统计作为持久指标累加保存，不依赖 `agent_trace` 是否保留完整。
- 保持现有抓取任务列表和详情接口的响应字段稳定，降低前端改动。

## 非目标

- 不重新设计智能抓取 Agent 的抓取策略。
- 不把完整 debug JSONL 搬进数据库。
- 不在本次设计中加入按模型计费、费用估算或多模型分摊。
- 不追求完全修复旧历史任务的 token 数据；旧数据只做尽力兼容。

## 推荐方案

采用“Job 负责业务对象，Run 负责一次执行尝试，Metrics 作为一等数据持久化”的结构。

`crawl_jobs` 继续表示用户创建的抓取任务，保留学校、学院、入口 URL、当前状态、候选数据归属和前端展示入口。

新增 `crawl_job_runs` 表，表示一次实际执行尝试。创建任务时生成第 1 次 run；失败或取消后的重试生成新的 run；暂停和继续沿用当前 run。

## 数据模型

新增表 `crawl_job_runs`：

```text
id
job_id
attempt_number
status
started_at
active_started_at
paused_at
finished_at
active_seconds
input_tokens
output_tokens
total_tokens
error_message
created_at
updated_at
```

字段语义：

- `attempt_number` 从 1 开始，同一个 job 内递增。
- `started_at` 表示该 run 第一次进入 running 的时间，用于审计。
- `active_started_at` 表示当前连续活跃运行段的开始时间；仅在 running 时有值。
- `paused_at` 表示当前 run 最近一次暂停时间，用于审计和前端未来扩展，不参与主计算。
- `finished_at` 表示 run 到达完成、失败或取消的时间。
- `active_seconds` 是已经结算的活跃运行秒数。
- `input_tokens`、`output_tokens`、`total_tokens` 是当前 run 的累计 LLM token。

`crawl_jobs` 可增加 `current_run_id` 指向当前 run。若为了减少迁移复杂度，也可以在查询时按 `job_id + attempt_number desc` 查当前 run；但推荐保留 `current_run_id`，让摘要查询和状态转移更明确。

## 状态流

创建任务：

- 创建 `crawl_jobs`。
- 创建 `crawl_job_runs(attempt_number=1, status=queued)`。
- `crawl_jobs.current_run_id` 指向该 run。

worker 领取 queued job：

- job 状态改为 `running`。
- 当前 run 状态改为 `running`。
- 如果 run 尚未开始过，则写 `started_at=now`。
- 写 `active_started_at=now`。

暂停：

- 仅允许 queued 或 running job 暂停。
- job 状态改为 `paused`。
- 当前 run 状态改为 `paused`。
- 如果 run 处于 running 且 `active_started_at` 有值，则把 `now - active_started_at` 累加进 `active_seconds`。
- 清空 `active_started_at`。
- 写 `paused_at=now`。

继续：

- 仅允许 paused job 继续。
- job 状态改为 `queued`。
- 当前 run 状态改为 `queued`。
- 不清空 token。
- 不清空 `active_seconds`。
- 不创建新 run。

失败或取消后重试：

- 仅允许 failed 或 canceled job 重试。
- 创建新的 `crawl_job_runs`，`attempt_number = 上一个 attempt_number + 1`，状态 `queued`。
- job 状态改为 `queued`，`current_run_id` 指向新 run。
- 如果用户选择清空现有数据，清空页面、候选和展示 trace。
- 新 run 的 token 和耗时从 0 开始。

完成、失败、取消：

- job 进入对应终态。
- 当前 run 进入对应终态。
- 结算当前活跃段到 `active_seconds`。
- 清空 `active_started_at`。
- 写 `finished_at=now`。
- 失败时同步写 run 的 `error_message`。

## 指标计算

摘要接口继续返回：

```text
input_tokens
output_tokens
total_tokens
duration_seconds
```

新任务优先从当前 run 读取：

- `input_tokens = current_run.input_tokens`
- `output_tokens = current_run.output_tokens`
- `total_tokens = current_run.total_tokens`
- `duration_seconds = current_run.active_seconds`

如果 run 当前处于 running 且 `active_started_at` 有值：

```text
duration_seconds = active_seconds + max(0, now - active_started_at)
```

如果任务没有 current run，则走旧兼容逻辑：从 `agent_trace` 尽力解析 token，并使用 `updated_at - created_at` 计算历史耗时。

## Token 累加

LLM trace 到达时，先保留现有 `agent_trace` 展示逻辑，再从原始事件解析 token usage。解析成功后立即累加到当前 run。

`agent_trace` 仍只保存最后 100 条，用于前端执行日志和最新事件摘要。它不再是正式 token 指标来源。

debug JSONL 继续保留完整原始事件，用于排障和事后审计。它不参与接口实时指标计算。

## 查询和 API 影响

前端 DTO 暂不需要新增字段。`CrawlJobSummaryRead` 保持现有 `input_tokens`、`output_tokens`、`total_tokens`、`duration_seconds` 字段。

后端 `_build_crawl_job_summaries` 查询 job 列表时，应批量加载 current run，避免逐行查询。无 run 的旧任务使用 fallback。

未来如果要展示“第几次重试”或“历史尝试记录”，可以再新增 run 列表接口，不影响本次修复。

## 旧数据迁移

迁移时为已有 `crawl_jobs` 创建 attempt 1：

- `attempt_number=1`
- `status` 使用 job 当前状态
- `started_at` 可为空
- `active_started_at` 为空
- `finished_at` 对终态 job 可使用 `updated_at`
- `active_seconds` 使用旧算法 `max(0, updated_at - created_at)`
- token 使用现有 `agent_trace` 尽力回填

这是兼容策略，不保证修复已经被滚动 trace 覆盖掉的旧 token。新 run 的指标必须准确。

## 错误处理

- 如果 job 缺少 current run，摘要接口不能失败，应使用 fallback。
- 如果 token 事件无法解析，不影响抓取流程，只是不累加该事件。
- 如果状态转移时 current run 缺失，应按保守方式创建一个 run 并记录可排查日志，而不是让用户操作失败。
- 取消或暂停请求应尽快被运行时 checkpoint 感知；即使后续还有 trace 追加，也不应继续增加 active duration。

## 测试计划

后端单元测试覆盖：

- 重试后新建 run，duration 和 token 从 0 开始。
- 暂停后继续沿用同一个 run，`active_seconds` 累加，token 保留。
- `agent_trace` 被截断后，run 上的 token 仍正确返回。
- 完成、失败、取消会结算当前活跃段并写 `finished_at`。
- 旧任务没有 run 时，摘要接口仍使用 fallback 返回兼容指标。

API 测试覆盖：

- 创建任务会创建 attempt 1。
- 重试 canceled 或 failed job 会创建 attempt N+1。
- pause/resume 不创建新 run。
- 列表和详情接口字段保持兼容。

## 验收标准

- 最近一次重试任务的“已耗时长”从最近 run 开始计算，而不是从 job 创建时间计算。
- 详情补全日志超过 100 条后，Token 统计不归零、不丢失早期 LLM usage。
- 暂停期间耗时不增长；继续后从同一个 run 继续累计。
- 失败或取消后重试，前端显示的是新 run 的 token 和耗时。
- 现有前端页面不需要结构性改造即可显示正确指标。
