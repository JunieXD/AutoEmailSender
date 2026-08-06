# 智能抓取暂停与继续设计

## 背景

当前智能抓取任务已经支持排队、运行、取消、失败、重试和审核，但「取消」会表达终止语义，不适合用户只是想暂时停一下的场景。

用户需要的是两个清晰分开的动作：

- 暂停：临时停止推进，保留已抓页面、候选导师、事件日志和指标，后续可以继续。
- 取消：明确终止任务，表示本次抓取不再继续。

相关位置：

`@backend/app/models/crawl_job.py`

`@backend/app/api/crawl_jobs.py`

`@backend/app/services/crawl_job_runtime.py`

`@backend/app/services/crawler_tools.py`

`@frontend/src/lib/api/crawlJobsApi.ts`

`@frontend/src/pages/TasksPage.tsx`

`@frontend/src/types/index.ts`

## 目标

- 新增 `paused` 抓取任务状态，和现有 `canceled` 明确区分。
- 支持对 `queued` 和 `running` 任务执行暂停。
- 支持对 `paused` 任务执行继续，继续时保留已抓取数据。
- 取消仍然保留，语义为终止本次抓取，不作为暂停的替代。
- 前端同时提供「暂停」「继续」「取消」能力，并用文案区分风险。

## 非目标

- 不实现 DeepAgents 内部执行状态的精确冻结和恢复。
- 不清空或重建已有抓取结果来模拟继续。
- 不引入外部任务队列或分布式锁。
- 不重做智能抓取的整体调度架构。
- 不改变候选导师审核、入库和编辑流程。

## 方案选择

本次采用「协作式暂停」：

1. 用户点击暂停后，后端将任务状态写为 `paused`。
2. 后台 worker 和抓取工具在安全检查点读取状态。
3. 如果发现任务已暂停，当前步骤尽快收尾，不再推进后续抓取和补全。
4. 用户点击继续后，后端将任务状态改回 `queued`。
5. 现有 crawler worker 再次领取任务，并在已保存页面和候选的基础上继续。

该方案的重点是「可控地停止推进」，不是强制杀掉正在执行的协程。这样能避免写入半截数据，也能复用当前已经存在的页面、候选和事件表。

## 状态语义

### `paused`

`paused` 表示任务临时停住，后续可以继续。

允许进入 `paused` 的来源状态：

- `queued`
- `running`

允许从 `paused` 转出的目标状态：

- `queued`：用户点击继续后，交回 worker 调度。
- `canceled`：用户决定不再继续时，可以从暂停状态取消。

不允许直接进入 `paused` 的状态：

- `needs_review`
- `completed`
- `failed`
- `canceled`

这些状态要么已经进入人工审核，要么已经是终态，不再需要暂停语义。

### `canceled`

`canceled` 表示本次抓取被用户明确终止。

取消后：

- 不再自动继续。
- 不再被 worker 领取。
- 可以通过现有重试能力重新开始。
- 重试是否清空已有数据仍由 `clear_existing_data` 决定。

### 状态流转

```text
queued  -> running -> needs_review -> completed
queued  -> paused  -> queued
running -> paused  -> queued
queued  -> canceled
running -> canceled
paused  -> canceled
running -> failed
failed  -> queued  (retry)
canceled -> queued (retry)
```

## 后端 API

新增两个接口：

```text
POST /api/crawl-jobs/{job_id}/pause
POST /api/crawl-jobs/{job_id}/resume
```

### 暂停接口

允许状态：

- `queued`
- `running`

行为：

- 将任务状态更新为 `paused`。
- 保留 `progress_current`、`progress_total`、`agent_trace`、`crawl_pages`、`crawl_candidates`。
- 记录操作日志 `crawl_job.paused`。
- 如果任务已经是 `paused`，直接返回当前任务，保证接口幂等。

冲突处理：

- 如果任务是 `completed`、`failed`、`canceled` 或 `needs_review`，返回 409。

### 继续接口

允许状态：

- `paused`

行为：

- 将任务状态更新为 `queued`。
- 清空 `error_message`。
- 不清空页面、候选和 trace。
- 记录操作日志 `crawl_job.resumed`。
- 由现有 crawler worker 在下一轮调度中领取。

冲突处理：

- 如果任务不是 `paused`，返回 409。

## 运行时行为

### Worker 领取任务

`run_queued_crawl_jobs_once` 继续只领取 `queued` 任务。

继续任务时，`paused -> queued -> running` 复用现有领取路径，避免新增一条特殊执行通道。

### 安全检查点

抓取运行时需要在以下位置检查暂停和取消：

- Agent 工具执行前。
- HTTP 抓取前后。
- 浏览器抓取前后。
- 页面快照入库前后。
- 候选导师保存前后。
- 候选详情补全循环的每个候选开始前。
- 候选详情补全写入前。

检查结果建议分为 3 类：

- `running`：继续执行。
- `paused`：停止后续推进，保持任务为 `paused`。
- `canceled`：停止后续推进，保持任务为 `canceled`。

### 暂停时的退出方式

暂停不应被记录为失败。推荐新增内部异常或返回信号，例如 `CrawlJobPaused`，只在抓取运行时内部使用。

当运行时捕获暂停信号时：

- 不调用 `_mark_job_failed`。
- 不调用 `_complete_running_job`。
- 不覆盖 `paused` 状态。
- 追加一条 trace，提示「任务已暂停，已保留当前抓取结果」。

### 取消时的退出方式

取消继续保持终止语义。

当运行时发现 `canceled`：

- 不继续抓取。
- 不写入新候选。
- 不把任务改成 `failed`。
- 追加一条 trace，提示「任务已取消」。

## 继续语义

继续采用「保留成果再跑」：

- 已保存的页面继续保留。
- 已保存的候选继续保留。
- 已保存的 trace 继续保留。
- 已有候选邮箱去重逻辑继续生效。

第一版不承诺从 Agent 上一次内部思考位置精确恢复。继续后 Agent 会重新从入口页面开始，但工具层需要尽量避免产生重复数据。

推荐增加页面 URL 去重能力：

- `record_page_snapshot` 写入前检查同一 job 下是否已有相同 URL 和相同 fetch_method。
- 如果已有成功快照，避免重复插入。
- 如果已有失败快照，可以允许后续成功快照覆盖或追加，具体实现以最小改动为准。

候选去重继续优先使用邮箱。没有邮箱的候选如果后续需要更严格去重，可再按姓名、主页 URL、学校和学院组合补充。

## 前端交互

### 状态展示

`CrawlJobStatusDTO` 新增 `paused`。

状态文案：

- `queued`：排队中
- `running`：运行中
- `paused`：已暂停
- `needs_review`：待审核
- `completed`：已完成
- `failed`：失败
- `canceled`：已取消

### 操作按钮

任务列表中建议展示：

- `queued`：显示「暂停」和「取消」。
- `running`：显示「暂停」和「取消」。
- `paused`：显示「继续」和「取消」。
- `failed`：显示「重试」。
- `canceled`：显示「重试」。
- `needs_review`：显示「查看 / 审核」。
- `completed`：不显示运行控制按钮。

### 文案区分

暂停确认文案强调可恢复：

```text
暂停后会保留已抓到的页面和候选导师，之后可以继续。
```

取消确认文案强调终止：

```text
取消后本次抓取不会继续。如需重新抓取，请使用重试。
```

## 数据与兼容性

### 数据库

当前 `status` 是字符串字段，不需要立即新增数据库枚举迁移。

需要同步更新：

- Python `CrawlJobStatus`
- Pydantic `CrawlJobStatusDTO`
- TypeScript `CrawlJobStatusDTO`
- 状态消息映射
- 前端状态标签和样式

### 历史数据

历史数据不需要迁移。

已有 `canceled` 仍表示已取消，不自动转换为 `paused`。

## 错误处理

- 暂停接口对已终态任务返回 409，避免用户误以为可以暂停已结束任务。
- 继续接口只接受 `paused`，避免误把失败或取消任务当成暂停恢复。
- worker 停止或服务重启导致的 `asyncio.CancelledError` 仍按现有逻辑处理，不和用户暂停混淆。
- 如果暂停发生在一次 HTTP 或浏览器请求中间，允许当前请求自然完成；暂停保证的是不再进入下一步。

## 测试计划

### 后端单元测试

- `queued` 任务调用暂停后变为 `paused`。
- `running` 任务调用暂停后变为 `paused`。
- `paused` 任务调用继续后变为 `queued`。
- `paused` 任务调用取消后变为 `canceled`。
- `completed`、`failed`、`canceled`、`needs_review` 调用暂停返回 409。
- 非 `paused` 任务调用继续返回 409。
- worker 不会领取 `paused` 任务。
- 运行时发现 `paused` 后不把任务改为 `failed`。
- 暂停后页面和候选数据仍保留。
- 继续后不会清空 `agent_trace`、页面和候选。

### 前端验证

- 抓取任务状态为 `paused` 时显示「已暂停」。
- `queued` 和 `running` 状态同时显示「暂停」和「取消」。
- `paused` 状态显示「继续」和「取消」。
- 暂停和取消的确认文案不同。
- 点击继续后任务回到排队或运行链路。

## 风险与取舍

- 协作式暂停不是瞬时硬中断。如果正在执行一个网络请求或 LLM 调用，按钮点击后可能需要等当前步骤返回才停住。
- 继续不是 DeepAgents 内部状态级恢复。它会保留结果重新推进，因此需要依赖工具层去重降低重复页面和候选。
- 如果暂停发生在候选详情补全阶段，已补全的候选保留，未补全的候选继续时再处理。
- 如果用户真正不想继续，应使用取消，而不是暂停后长期放置。

## 实施分期

### 第一期：核心状态与 API

- 增加 `paused` 状态。
- 新增暂停和继续接口。
- 更新事件文案和操作日志。
- 更新 worker 状态筛选，确保不领取 `paused`。

### 第二期：运行时检查点

- 抽出统一的任务控制状态检查函数。
- 在抓取工具和候选补全循环中接入暂停检查。
- 确保暂停不会落入失败状态。

### 第三期：前端交互

- 更新 TypeScript 状态类型。
- 增加暂停和继续 API client。
- 在任务页同时展示暂停、继续和取消。
- 区分暂停与取消确认文案。

### 第四期：去重增强

- 为页面快照写入补充 URL 去重。
- 视实际重复情况，再考虑无邮箱候选的轻量去重。

## 自检

- 暂停和取消的语义已经分离：暂停可继续，取消是终止。
- 继续路径保留已有数据，不清空页面、候选和 trace。
- 方案不依赖 DeepAgents 内部状态持久化，避免过度设计。
- 改动范围集中在抓取任务状态、运行时控制点和任务页操作，不影响导师审核入库流程。
- 未留下占位描述或未完成章节。
