# 后台批量匹配分析任务设计

## 背景

当前首页已经支持选择导师后批量执行“分析匹配度”。这套能力使用前端 warm-up + 固定并发池触发多个 `calculate-match` 请求，后端也已经通过 `match_analysis_runs` 增加了同一 `email_task` 的运行中防重入保护。

这个设计适合页面内快速操作，但不适合较大的批量分析：

- 用户关闭页面、刷新页面或离开首页后，前端批量流程会中断或失去进度上下文。
- 批量结果目前主要靠一次通知汇总，缺少可回看的任务记录。
- 任务中心已经承载发送批次和智能抓取，用户天然会期待长耗时工作也能在那里观察。
- 最近的匹配分析并发设计曾把“独立后台队列”列为非目标；现在产品决策已经变化，需要把这个边界更新为“受控的本地后台任务”。

本设计将批量匹配分析升级为可在后台继续运行的任务，并在任务中心展示。

## 已确认的产品决策

- 批量匹配分析在用户离开首页、刷新页面或关闭当前页面后仍应继续运行。
- 批量匹配分析应进入任务中心，作为一次可观察的任务。
- 匹配分析任务不复用发送批量任务的语义，不直接混入 `batch_tasks`。
- 任务中心的定位从“发送任务大厅”扩展为“后台任务中心”，承载发送、抓取和匹配分析三类长耗时工作。

## 目标

- 用户可以从首页对已选导师创建一个后台批量匹配分析任务。
- 后台 worker 在本地 FastAPI 进程内继续执行匹配分析，不依赖前端页面保持打开。
- 任务中心可以展示匹配分析任务的状态、进度、成功数、失败数、跳过数和 token 汇总。
- 单个导师分析失败不影响同一批次内其他导师继续执行。
- 同一 `email_task` 的匹配分析仍依赖 `match_analysis_runs` 防重入，避免重复并发运行。
- 首页刷新后可以看到已经写回的匹配分和解释。

## 非目标

- 不引入 Redis、RabbitMQ、Celery 或分布式队列。
- 不把单个导师的“分析匹配度”按钮改成后台任务；单个分析继续保持即时请求。
- 不让匹配分自动决定是否进入发送流程。
- 不在本次设计中重做任务中心的整体 UI 视觉风格。
- 不为匹配分析任务增加复杂排程、定时启动或周期性重跑。
- 不在第一版支持暂停后精确恢复到某个模型调用内部；取消只在任务项之间的安全检查点生效。

## 方案比较

### 方案 A：继续首页前端批量并发

优点：

- 改动最小，沿用现有实现。
- 前端已有批量进度和汇总通知。

缺点：

- 无法满足离开页面后继续运行。
- 批量过程和结果不可回看。
- 较大批量时用户需要守在页面上等待。

### 方案 B：复用 `batch_tasks`

优点：

- 可以直接借用现有任务中心里的批量任务卡片、暂停、停止和详情抽屉。
- 数据模型已有批次与子任务关系。

缺点：

- `batch_tasks` 当前语义是批量邮件发送任务，包含发送窗口、主题、正文、附件和邮件任务状态统计。
- 匹配分析只是计算任务，强行复用会让发送状态机和分析状态混杂。
- 后续调试时难以判断一个 batch task 是“发送批次”还是“分析批次”。

### 方案 C：新增 `match_analysis_jobs`

优点：

- 语义清晰：一条 job 表示一次批量匹配分析运行。
- 可以独立定义状态、进度、失败摘要和 token 汇总。
- 能复用现有 `match_analysis_runs` 作为每个 `email_task` 的运行审计，不重复造单次分析记录。
- 与任务中心的信息架构匹配，后续也方便把抓取、发送、匹配统一成不同任务类型。

缺点：

- 需要新增表、API、worker 调度和任务中心展示入口。
- 首页批量分析流程需要从“直接执行”改成“创建任务并跳转或提示去任务中心查看”。

推荐采用方案 C。

## 总体架构

后台批量匹配分析分为四层：

1. 首页创建任务：选择导师后创建 `match_analysis_job` 和对应 items。
2. 本地后台 worker：轮询 queued/running job，并按受控并发执行 item。
3. 单项分析运行：复用现有 `calculate_task_match` 核心逻辑和 `match_analysis_runs` 防重入。
4. 任务中心展示：按任务维度展示进度、状态、失败摘要和 token 汇总。

任务中心不需要把三类任务合并进同一张数据库表。前端可以在同一页面里分别请求：

- 发送批量任务：`batch_tasks`
- 智能抓取任务：`crawl_jobs`
- 批量匹配分析任务：`match_analysis_jobs`

## 数据模型

### `match_analysis_jobs`

新增批量匹配分析任务表：

```text
id
name
identity_id
llm_profile_id
status              // queued | running | completed | partial_failed | failed | canceled
target_count
succeeded_count
failed_count
skipped_count
total_prompt_tokens
total_completion_tokens
total_tokens
cancel_requested_at
started_at
finished_at
created_at
updated_at
last_error
```

字段说明：

- `name` 默认可生成为“批量匹配分析 YYYY-MM-DD HH:mm”。
- `identity_id` 和 `llm_profile_id` 固定本次任务使用的身份和模型配置。
- token 汇总来自成功完成的 item 对应 usage，失败项如果已有 token 审计则也可以纳入总数。
- `cancel_requested_at` 用于表达用户已请求停止，worker 在 item 间检查。

### `match_analysis_job_items`

新增任务明细表：

```text
id
job_id
professor_id
email_task_id
status              // queued | running | succeeded | failed | skipped | canceled
match_analysis_run_id
error_message
skip_reason
prompt_tokens
completion_tokens
total_tokens
started_at
finished_at
created_at
updated_at
```

字段说明：

- 创建 job 时尽量为每个导师准备好或复用对应的 `email_task`，并把 `email_task_id` 固化到 item。
- 如果某个导师缺少研究方向或近期论文，item 可以直接标记为 `skipped`。
- 如果缺少默认材料，整个 job 创建失败，不创建半成品任务。
- `match_analysis_run_id` 指向实际单次模型调用审计记录，便于 token 中心和任务详情互相追踪。

## 状态流转

### Job 状态

```text
queued -> running -> completed
queued -> running -> partial_failed
queued -> running -> failed
queued -> running -> canceled
queued -> canceled
```

规则：

- 所有可分析 item 成功，且没有失败项时，job 为 `completed`。
- 至少一个 item 成功，同时存在失败或跳过时，job 为 `partial_failed`。
- 没有任何 item 成功，且存在失败时，job 为 `failed`。
- 用户取消后，未开始 item 标记为 `canceled`；已运行中的 item 等待当前安全点结束。
- 如果创建后因导师数据变化只存在跳过项且没有成功或失败，job 为 `failed`，并在 `last_error` 中说明没有可分析导师。

### Item 状态

```text
queued -> running -> succeeded
queued -> running -> failed
queued -> skipped
queued -> canceled
```

跳过原因包括：

- 缺少研究方向或近期论文
- 对应导师已归档
- 准备 `email_task` 时发现身份或模型配置不完整
- 同一 `email_task` 已有运行中的匹配分析

## 后端 API

新增 API：

```text
GET    /api/match-analysis-jobs
POST   /api/match-analysis-jobs
GET    /api/match-analysis-jobs/{job_id}
GET    /api/match-analysis-jobs/{job_id}/items
POST   /api/match-analysis-jobs/{job_id}/cancel
POST   /api/match-analysis-jobs/{job_id}/retry-failed
```

`POST /api/match-analysis-jobs` 请求包含：

```json
{
  "name": "可选任务名",
  "identity_id": 1,
  "llm_profile_id": 1,
  "professor_ids": [1, 2, 3]
}
```

创建前置校验：

- 必须有默认材料。
- 必须至少选择 1 位导师。
- 身份和模型配置必须存在。
- professor IDs 去重，并只允许未归档导师进入任务。
- 至少要有 1 位导师具备研究方向或近期论文；如果全部缺少研究证据，直接创建失败并提示原因。

`retry-failed` 只为失败和取消的 item 创建新 job，不在原 job 上原地重跑。这样可以保留历史运行记录，也避免修改已经完成的统计口径。

## 后台执行模型

沿用当前本地后台 worker 思路，在 FastAPI 生命周期内增加匹配分析 job 轮询。

推荐默认参数：

```text
match_analysis_job_worker_count = 1
match_analysis_job_item_concurrency = 3
```

含义：

- 同一时间默认只领取 1 个批量匹配分析 job。
- 单个 job 内最多并发分析 3 个 item。
- 以后可以把 worker count 调到 2，但第一版保持保守，避免模型调用和 SQLite 写入压力过大。

执行流程：

1. worker 领取一个 `queued` job，并更新为 `running`。
2. 查询该 job 下 `queued` items。
3. 对 item 使用固定并发池执行。
4. 每个 item 开始前检查 job 是否已请求取消。
5. item 执行时调用已有匹配分析核心逻辑。
6. 成功时写回 `email_tasks.match_score`、匹配解释和 item token。
7. 失败时只标记当前 item，不中断其他 item。
8. 所有 item 收口后重新统计 job 状态和 token 汇总。

## 与现有匹配分析的关系

现有单个接口：

```text
POST /api/email-tasks/{task_id}/calculate-match
```

继续保留，用于工作区和首页单个导师的即时分析。

后台 job 不应从 HTTP 层反向调用这个接口，而是复用其下方的 service 逻辑。建议将可复用核心整理为：

```text
calculate_task_match(session, task_id, actor)
```

后台 worker 和 API endpoint 共享该 service。这样可以保证：

- 单项分析 prompt、token 审计和写回逻辑一致。
- `match_analysis_runs` 的运行中防重入继续生效。
- 后续优化评分量表或缓存策略时只改一处。

## 前端交互

### 首页

首页“批量分析匹配度”按钮改为创建后台任务：

1. 用户选择导师。
2. 前端执行本地基础校验：是否选择导师、是否有默认材料。
3. 调用 `POST /api/match-analysis-jobs`。
4. 创建成功后显示全局通知：“已创建批量匹配分析任务，可在任务中心查看进度。”
5. 可提供“查看任务”操作，跳转到任务中心的匹配分析 tab。

首页不再承担批量分析的并发池和长时间 loading。单个导师行内“分析匹配度”保持不变。

### 任务中心

任务中心新增“匹配分析”tab，或者在现有任务类型筛选中增加“匹配分析”。

列表卡片展示：

- 任务名
- 状态
- 进度：成功 / 失败 / 跳过 / 总数
- token 总消耗
- 创建时间、完成时间
- 操作：查看详情、取消、重试失败

详情抽屉展示 item 列表：

- 导师姓名
- 学校 / 学院
- 状态
- 匹配分
- 失败原因或跳过原因
- token 消耗

## 错误处理

- 缺少默认材料：创建 job 前直接返回 400，不创建任务。
- 全部导师都不可分析：创建失败并提示原因，避免任务中心出现无意义空任务。
- 单个 item 缺少研究证据：标记 skipped。
- 单个 item 模型调用失败：标记 failed，并记录错误摘要。
- 防重入冲突：标记 skipped，原因是该导师已有匹配分析进行中。
- worker 异常退出：下次启动时将长时间停留在 running 且未完成的 item 恢复为 queued 或 failed，具体按是否已有 run 结束记录判断。

## 可观测与通知

- 任务中心是匹配分析后台任务的主要观察入口。
- 全局通知只负责创建成功、创建失败、取消成功、重试任务已创建等即时反馈。
- token 用量中心继续读取 `match_analysis_runs`，不直接读取 job 表。
- job 表的 token 汇总用于任务中心快速展示，不替代功能级 token 审计。

## 迁移影响

需要更新或新增的主要范围：

- 后端模型：`match_analysis_jobs`、`match_analysis_job_items`
- 后端 API：新增 match analysis jobs router
- 后端 worker：新增本地 job 轮询与 item 并发执行
- 前端 API：新增 match analysis jobs client
- 首页：批量按钮改为创建后台任务
- 任务中心：新增匹配分析任务列表和详情
- 文档：更新先前并发设计中“不把匹配分析改造成独立后台队列系统”的非目标

不应改动：

- 单个导师即时匹配分析入口
- 邮件发送状态机
- `batch_tasks` 的发送批次语义
- 匹配分不自动裁决发送流程的产品原则

## 测试策略

### 后端测试

- 创建 job 时 professor IDs 去重，并过滤归档导师。
- 缺少默认材料时创建失败。
- job worker 能把 queued job 推进到 completed。
- 单个 item 失败不影响其他 item。
- 防重入冲突会被记录为 skipped 或 failed，不导致整个 job 崩溃。
- 取消请求会让未开始 item 进入 canceled。
- retry failed 会创建新 job，而不是修改原 job。

### 前端验证

- 首页批量分析创建任务后不再长时间锁住页面。
- 创建成功通知出现，并能进入任务中心查看。
- 任务中心能展示 running、completed、partial_failed、failed、canceled 状态。
- 详情抽屉能展示每个导师的成功、失败或跳过原因。
- 首页刷新后能看到后台写回的匹配分。

### 回归验证

- 单个导师“分析匹配度”仍可即时执行。
- 工作区里的匹配分析不受后台 job 改造影响。
- 批量发送任务仍按原状态机和调度规则运行。
- token 用量中心仍能看到每次匹配分析记录。

## 成功标准

当以下条件同时满足时，视为设计目标达成：

- 用户可以从首页创建后台批量匹配分析任务。
- 用户离开首页或刷新页面后，后台任务仍继续执行。
- 任务中心可以查看匹配分析任务进度和明细。
- 单个导师失败不会中断整批任务。
- 每次实际模型调用仍记录在 `match_analysis_runs`。
- 匹配结果正确写回现有导师任务数据，并可在首页和工作区查看。
- 发送批量任务和匹配分析任务在数据模型与界面语义上保持清晰区分。
