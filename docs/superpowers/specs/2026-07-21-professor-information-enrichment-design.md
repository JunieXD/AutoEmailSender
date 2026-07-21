# 导师主页智能信息补全设计

## 背景

现有智能抓取流程已经可以访问导师详情页，并通过浏览器回退、结构化 LLM 输出和重试机制补全候选导师的邮箱、职称、系所、研究方向与近期论文。但导师已经进入导师库后，如果资料仍有缺失，用户只能手工编辑，或重新创建抓取任务后再人工合并。

本设计在导师管理页增加“智能补全”能力：使用导师已保存的主页链接访问详情页，只填充当前为空的允许字段，不覆盖用户已有内容。单导师补全是一项轻量的一次性操作，不进入任务中心；批量补全是一项可观察的后台任务，进入任务中心新增的“信息补全”页签。两种入口都复用现有 V2 详情页补全执行链路、重试策略、Token 审计和日志能力。

## 已确认的产品决策

- 允许补全的字段只有：邮箱、职称、系所、研究方向、近期论文。
- 姓名、学校、学院、主页链接、来源链接、备注和标签不参与补全，也不会被修改。
- 只使用数据库中已经保存的主页链接；编辑弹窗内尚未保存的链接不参与本次操作。
- 单导师补全不显示在任务中心，但必须写入 Token 消耗中心和日志。
- 批量补全显示在任务中心；“信息补全”位于“匹配分析”右侧，是任务类型切换栏的第四个页签。
- 批量任务中缺少主页链接、没有待补字段、已经归档或已有活动补全任务的导师保留在任务明细中并标记“跳过”，其他导师继续执行。
- 回收站中的导师不允许发起单次或批量补全。
- 信息补全任务列表不依赖身份选择，和教师抓取任务一样始终可以查看。
- 继续复用 `crawler_profile_enrichment_concurrency`，默认值统一为 `3`，不新增设置项；智能抓取详情补全和导师管理页补全共享同一个全局上限。
- 用户可见的错误通知和任务明细保留原始异常文本，只对 API Key、Authorization、Cookie 等敏感内容脱敏。

## 目标

- 用户可以在导师编辑弹窗中为当前导师发起一次智能补全。
- 用户可以在导师管理页勾选多位导师后创建批量信息补全任务。
- 后台任务不依赖页面保持打开，并且应用重启后可以继续处理待执行或可重试工作项。
- 单导师按钮在对应任务运行期间禁用，开始和结束时均有明确通知。
- 任一导师失败或被跳过不影响批量中的其他导师。
- worker 提交结果时再次判断字段是否为空，避免覆盖任务运行期间由用户手工写入的内容。
- 单次和批量运行都能在 Token 消耗中心按“信息补全”独立统计。
- 用户操作、任务生命周期、单项执行、重试和异常都进入现有日志体系。

## 非目标

- 不新增导师主页自动发现能力；没有已保存主页链接时不猜测或搜索链接。
- 不允许模型修改已有非空字段，也不提供“强制覆盖”选项。
- 不补全学校、学院、姓名、主页链接、来源链接、备注或标签。
- 不在编辑弹窗中自动保存用户尚未提交的更改。
- 不引入 Redis、Celery、RabbitMQ 或外部分布式队列。
- 不新增一套独立抓取器、浏览器策略、LLM 提示词或限流算法。
- 不把单导师补全展示为任务中心卡片。
- 不为每个补全入口增加独立并发设置。

## 方案选择

### 方案 A：前端直接请求并等待补全完成

优点：

- 接口和状态最简单。

缺点：

- 页面关闭或刷新后丢失状态。
- 请求持续时间长，容易受到前端超时影响。
- 无法与现有 V2 worker 共用全局并发池。
- 批量任务难以提供持久化明细、取消和失败重试。

### 方案 B：新增完全独立的信息补全表和 worker

优点：

- 数据语义独立。

缺点：

- 会重复实现详情页抓取、浏览器回退、结构化输出、重试、Token 和调度逻辑。
- 两套 worker 难以严格共享同一个并发上限。
- 后续修复抓取兼容性时容易出现两条链路行为不一致。

### 方案 C：扩展 V2 抓取任务并复用候选项与补全工作项

优点：

- `CrawlCandidate` 已能表达本次允许补全的全部字段，并且已经支持关联 `professor_id`。
- `CrawlCandidateEnrichmentTask` 已具备领取、租约、失败重试和终态。
- 可以直接复用详情页 HTTP/浏览器抓取、结构化 LLM 重试、Token 记录和调试事件。
- 所有详情页补全工作项由同一 scheduler 领取，天然共享全局并发上限。

缺点：

- 需要为抓取任务增加明确用途和展示范围，避免普通教师抓取与信息补全混在一起。
- 需要在补全 worker 中增加受控的导师写回分支。

采用方案 C。

## 总体架构

信息补全分为五层：

1. 导师管理页入口：单导师或已勾选导师发起请求，并传入顶部栏当前选择的 `llm_profile_id`。
2. 任务创建服务：创建专用于信息补全的 `CrawlJob`、`CrawlCandidate` 和 `CrawlCandidateEnrichmentTask`。
3. V2 scheduler：和现有智能抓取详情补全统一领取工作项，并应用共享并发上限。
4. 详情补全 worker：复用已有抓取、浏览器回退、LLM 结构化解析和重试；成功后按空字段规则写回 `Professor`。
5. 展示与审计：单次由导师页轮询并通知；批量由任务中心展示；两类任务均进入 Token 中心、操作日志和调试日志。

普通教师抓取与导师信息补全仍共用底层表，但通过任务用途隔离 API、任务中心列表和业务行为。

## 数据模型

### `crawl_jobs`

新增字段：

```text
job_kind              // faculty_crawl | professor_enrichment
trigger_mode          // crawl | single | batch
task_center_visible   // boolean
display_name          // nullable
```

规则：

- 既有数据迁移为 `job_kind = faculty_crawl`、`trigger_mode = crawl`、`task_center_visible = true`。
- 单导师补全使用 `job_kind = professor_enrichment`、`trigger_mode = single`、`task_center_visible = false`。
- 批量补全使用 `job_kind = professor_enrichment`、`trigger_mode = batch`、`task_center_visible = true`。
- `display_name` 默认生成“信息补全 YYYY-MM-DD HH:mm”；单导师可使用“姓名 · 信息补全”。
- 信息补全任务继续使用 `runtime_version = v2` 和 `entry_type = profile`。
- 为兼容现有非空字段，信息补全任务的 `university`、`school` 使用创建时的概括值，`start_url` 使用第一个有效主页链接；这些字段不作为信息补全详情页的展示或写回依据。

### `crawl_candidates`

每位导师创建一个候选快照：

```text
job_id
professor_id
name
email
title
university
school
department
research_direction
recent_papers
profile_url
source_url
```

规则：

- `professor_id` 必须指向创建任务时的导师。
- 候选项保存任务创建时的导师数据，供提示词、详情展示和运行诊断使用。
- `profile_url` 只取已持久化的导师主页链接。
- 普通智能抓取候选的人工采纳流程保持不变；信息补全候选不进入抓取审核和采纳流程。

### `crawl_candidate_enrichment_tasks`

新增字段：

```text
professor_id          // nullable，普通抓取任务为空
skip_reason           // nullable
enriched_fields       // 实际写入导师记录的字段名列表
started_at            // nullable
finished_at           // nullable
```

状态扩展为：

```text
pending
processing
succeeded
skipped
failed_retryable
failed_terminal
canceled
```

`professor_id` 冗余保存到工作项，用于高效查询导师活动任务并建立部分唯一索引。`enriched_fields` 记录本项最终实际写入的允许字段，任务详情据此展示补全结果。对 `pending`、`processing`、`failed_retryable` 状态建立“同一 `professor_id` 只能有一个活动工作项”的约束；普通抓取的空 `professor_id` 不受影响。

## 字段补全与并发写入规则

允许写回字段和模型字段映射如下：

```text
Professor.email              <- CrawlCandidate.email
Professor.title              <- CrawlCandidate.title
Professor.department         <- CrawlCandidate.department
Professor.research_direction <- CrawlCandidate.research_direction
Professor.recent_papers       <- CrawlCandidate.recent_papers
```

空值定义：

- 字符串字段为 `NULL`、空字符串或去除首尾空白后为空。
- `recent_papers` 为 `NULL`、空数组，或规范化后没有有效条目。

写回分两次判断：

1. 创建任务时判断导师是否至少存在一个待补字段；全部非空时，批量项直接标记为 `skipped`。
2. worker 完成模型调用后，在同一数据库事务中重新读取 `Professor`，逐字段检查当前值。只有当前仍为空且模型结果非空时才赋值。

因此，用户在补全运行期间手工保存的新值优先，worker 不会覆盖它。候选快照中的非空旧值也不会因为模型返回不同内容而更新导师。

近期论文写回前继续使用现有规范化逻辑和数量上限。邮箱和职称继续使用现有导师字段规范化逻辑。模型返回的学校、学院、姓名、链接或其他扩展字段即使存在也必须丢弃。

## 创建前置校验与跳过规则

### 单导师

发起前必须满足：

- 导师存在且未归档。
- 导师已经保存合法的 HTTP/HTTPS 主页链接。
- 至少一个允许字段为空。
- 没有该导师的活动信息补全工作项。
- 请求指定的 LLM 配置存在；前端默认使用顶部栏当前选择的模型。

校验不通过时不创建隐藏任务，接口返回明确错误。活动任务冲突返回 `409`，并返回现有运行标识供前端继续轮询；其他业务校验返回 `422`。

### 批量

请求中的导师 ID 去重后逐项处理：

- 导师不存在：整个请求返回错误，避免用户选择与创建结果不一致。
- 导师已归档：创建 `skipped` 项，原因为“导师已在回收站”。
- 缺少已保存主页链接：创建 `skipped` 项，原因为“缺少导师主页链接”。
- 没有待补字段：创建 `skipped` 项，原因为“资料已完整，无需补全”。
- 已有活动信息补全工作项：创建 `skipped` 项，原因为“已有信息补全正在进行”。
- 其余导师创建 `pending` 工作项。

即使全部导师都被跳过，仍保留这次批量任务和明细，并立即收口为完成状态，方便用户理解没有实际执行的原因。

## 状态与统计

### 任务状态

沿用 `CrawlJobStatus`，信息补全使用以下流转：

```text
queued -> running -> completed
queued -> running -> partially_completed
queued -> running -> failed
queued -> canceled
running -> canceled
```

不使用 `paused` 和 `needs_review`：

- 全部实际执行项成功，或任务只有跳过项：`completed`。
- 至少一个成功，同时存在失败、取消或跳过：`partially_completed`。
- 没有成功项且至少一个失败：`failed`。
- 用户取消后，未开始项标记为 `canceled`，运行中工作项在下一个安全检查点停止提交；任务收口为 `canceled`。

### 明细展示状态映射

```text
pending / failed_retryable -> queued
processing                 -> running
succeeded                  -> succeeded
skipped                    -> skipped
failed_terminal            -> failed
canceled                   -> canceled
```

任务汇总按明细实时计算：目标数、已完成数、成功数、失败数、跳过数、取消数以及 Token 总量。单导师隐藏任务使用同样状态机，便于刷新后恢复按钮状态和查询结果。

## 后端 API

新增专用 API，不把信息补全混入普通抓取审核接口。

### 单导师接口

```http
POST /api/professors/{professor_id}/information-enrichment
GET  /api/professors/{professor_id}/information-enrichment/active
GET  /api/professor-information-enrichment-jobs/{job_id}
```

创建请求：

```json
{
  "llm_profile_id": 1
}
```

创建响应返回 `job_id`、导师 ID、任务状态和待补字段。前端通过详情接口轮询，直到任务进入终态。活动查询用于编辑弹窗重新打开或导师页刷新后恢复禁用状态。

### 批量任务接口

```http
GET    /api/professor-information-enrichment-jobs
POST   /api/professor-information-enrichment-jobs
GET    /api/professor-information-enrichment-jobs/{job_id}
GET    /api/professor-information-enrichment-jobs/{job_id}/items
POST   /api/professor-information-enrichment-jobs/{job_id}/cancel
POST   /api/professor-information-enrichment-jobs/{job_id}/retry-failed
DELETE /api/professor-information-enrichment-jobs/{job_id}
POST   /api/professor-information-enrichment-jobs/{job_id}/restore
```

批量创建请求：

```json
{
  "name": "可选任务名",
  "professor_ids": [1, 2, 3],
  "llm_profile_id": 1
}
```

列表接口只返回 `job_kind = professor_enrichment` 且 `task_center_visible = true` 的任务，并支持现有任务中心的当前任务/回收站视图。读取信息补全列表和详情不要求 `identity_id`。

取消只影响尚未提交的工作项。删除沿用任务中心软删除语义，只允许终态任务进入回收站；恢复只恢复任务记录，不自动重跑。

`retry-failed` 为原任务中 `failed_terminal` 和 `canceled` 的导师创建一个新的批量任务，保留原任务作为历史记录；已经成功和纯业务跳过项不重试。若导师当前已有活动任务，新任务中的对应明细标记为跳过。

## Worker 与共享并发

信息补全不启动新的 runtime worker。现有 V2 scheduler 在每轮调度时统一领取普通智能抓取和信息补全的详情工作项。

共享限制：

```text
crawler_profile_enrichment_concurrency = 3
```

这个数值表示当前应用内所有 `CrawlCandidateEnrichmentTask` 同时处于执行状态的最大数量，而不是“每个任务各 3 个”。例如普通智能抓取已占用 2 个名额时，导师管理页的信息补全最多再领取 1 个。

个人中心“其他设置”不新增字段，继续编辑现有 `crawler_profile_enrichment_concurrency`：

- 默认值从界面中的 5 统一为 3，与数据库和环境配置一致。
- 标签调整为“同时补全导师详情页数”。
- 提示说明该值同时作用于智能抓取和导师管理页的信息补全，并在下一轮调度生效。

每个工作项继续沿用：

- HTTP 抓取和浏览器回退。
- 同域名抓取并发限制。
- 结构化 LLM 输出重试。
- 最多 4 次工作项尝试。
- `5、10、20、40` 秒退避，上限 60 秒。
- 租约超时回收和应用重启恢复。

## 导师管理页交互

### 单导师编辑弹窗

在弹窗右上角、关闭按钮左侧增加带图标的“智能补全”按钮。

行为：

- 按钮使用导师数据库中已保存的主页链接，不读取当前表单尚未保存的值。
- 没有选中的 LLM 配置时提示用户先选择模型，不发请求。
- 请求创建成功后立即发出信息通知：`正在为「导师姓名」智能补全资料。`
- 该导师存在活动任务时按钮显示加载状态并禁用，直到任务成功、失败或取消。
- 关闭弹窗不会取消任务；导师页在后台继续轮询活动单次任务。
- 成功后刷新导师列表和当前编辑表单中未被用户改动的已保存数据。
- 成功通知列出实际新增字段；没有写入任何字段时说明“补全过程完成，但没有发现可新增的信息”。
- 失败通知包含导师姓名和脱敏后的原始错误文本。

单次任务结束通知示例：

```text
补全完成：张三
已补全：邮箱、研究方向。
```

```text
补全失败：张三
HTTP 403: Access denied
```

### 批量操作区

导师管理页选中导师后显示的底部批量操作卡增加“批量智能补全”按钮，位置在“批量改标签”右侧、删除或恢复操作之前。

行为：

- 回收站视图不显示或禁用该按钮，并明确说明回收站导师不可补全。
- 没有选中的 LLM 配置时提示用户先选择模型。
- 点击后创建一个后台批量任务，不由前端循环调用单导师接口。
- 创建成功后通知任务已创建，并保留当前选择；用户可前往任务中心查看。
- 创建响应可同时返回实际排队数和跳过数，通知中展示摘要。

## 任务中心交互

任务类型切换顺序固定为：

```text
批量发送 | 教师抓取 | 匹配分析 | 信息补全
```

“信息补全”页签不受顶部身份选择影响。进入页签时独立请求信息补全任务列表。

列表卡片展示：

- 任务名称、状态和创建时间。
- 总目标数与进度条。
- 成功、失败、跳过和取消数量。
- Token 总量和耗时。
- 取消、失败重试、删除、恢复和查看详情操作，按任务状态显示。

详情抽屉复用“匹配分析”详情的信息层级和交互：

- 顶部显示任务汇总、进度和 Token。
- 明细按导师逐行展示姓名、学校/学院、主页链接、状态、实际补全字段、开始和结束时间。
- 失败项显示脱敏后的原始错误文本。
- 跳过项显示明确 `skip_reason`。
- 支持状态筛选、分页和刷新。
- 不提供候选采纳、修改抓取结果或进入教师抓取审核的入口。

## Token 消耗中心

扩展 Token 功能类型：

```text
information_enrichment // 信息补全
```

聚合规则：

- `job_kind = professor_enrichment` 的 `CrawlWorkerTokenUsage` 映射为“信息补全”，不再计入“智能抓取”。
- 单次隐藏任务和批量可见任务都进入记录列表、趋势图、功能分布和总计。
- 一次实际模型调用只产生一条明细来源；任务卡汇总不再作为第二条 Token 记录，避免重复计数。
- 单导师记录标题使用“导师姓名 · 信息补全”。
- 批量记录标题使用任务名称，并保留可关联的 job ID。
- 抓取失败但模型已经返回 usage 时仍记录实际 Token；完全未调用模型的跳过项为 0，不生成虚假消耗。

## 错误信息与敏感内容脱敏

错误链路保留最接近源头的异常文本，包括 HTTP 状态、浏览器错误、解析错误、模型服务错误和重试耗尽原因。向用户展示前只遮盖敏感值，不把异常统一替换成泛化文案。

最少脱敏范围：

- `Authorization` 请求头。
- `Bearer` token。
- `api_key`、`apikey`、`api-key` 等键值。
- `Cookie` 和 `Set-Cookie`。
- URL 查询参数中的常见 token、key、secret。

脱敏后的文本同时用于通知、任务明细和普通操作日志。只允许本地调试日志在现有安全策略允许的范围内保存结构化上下文；任何密钥都不得写入调试日志。

## 日志与可观测性

### 操作日志

新增或完善以下用户操作事件：

```text
professor_information_enrichment.single_created
professor_information_enrichment.batch_created
professor_information_enrichment.cancel_requested
professor_information_enrichment.retry_created
professor_information_enrichment.deleted
professor_information_enrichment.restored
```

记录 actor、job ID、导师 ID 或目标数量、模型配置 ID、排队/跳过数量和结果状态，不记录密钥或整段网页内容。

### Worker 和调试日志

沿用 crawler V2 debug event，并增加信息补全上下文：

- `job_kind`、`trigger_mode`、job ID、task ID、candidate ID、professor ID。
- 领取、抓取方式、浏览器回退、模型调用、结构化重试、字段写回、空字段冲突跳过和终态。
- 每次尝试的脱敏错误、退避时间和最终错误。
- 批量任务收口时的成功、失败、跳过、取消和 Token 汇总。

## 数据迁移与兼容

- Alembic 为 `crawl_jobs` 和 `crawl_candidate_enrichment_tasks` 增加字段、默认值和索引。
- 既有 `crawl_jobs` 回填为普通教师抓取，不改变其任务中心展示。
- 既有补全工作项的 `professor_id` 允许为空，不影响当前抓取任务。
- 部分唯一索引只约束具有 `professor_id` 的活动信息补全工作项。
- 现有抓取任务列表 API 默认只返回 `faculty_crawl`，防止信息补全任务进入教师抓取页签。
- 现有教师抓取详情、候选审核、采纳、暂停和恢复行为保持不变。
- downgrade 移除本次字段和索引，不删除原有抓取任务数据。

## 测试范围

### 后端模型与迁移

- 新建数据库具有新增字段、默认值和部分唯一索引。
- 既有抓取任务迁移后仍被识别为 `faculty_crawl`。
- 同一导师不能创建两个活动工作项，终态后可以再次创建。
- downgrade/upgrade 基本往返可执行。

### 创建与校验

- 单导师使用已保存主页链接和指定 LLM 配置创建隐藏任务。
- 尚未保存的弹窗链接不会进入请求或任务。
- 归档、缺少主页、资料完整和无模型配置的单次请求返回明确错误。
- 批量请求去重导师 ID，并为归档、缺少主页、资料完整和活动冲突项创建跳过明细。
- 一位导师失败或跳过不影响其他导师排队。

### Worker 与数据一致性

- 只写回邮箱、职称、系所、研究方向和近期论文。
- 所有已有非空字段保持不变。
- 模型运行期间由用户补写的字段在提交阶段保持不变。
- 邮箱、职称、研究方向和论文继续通过现有规范化。
- 详情抓取、浏览器回退、结构化解析失败沿用现有重试和退避。
- 达到最大次数后进入 `failed_terminal`，原始错误经脱敏后可读取。
- 取消后未开始项变为 `canceled`，已取消任务不能再提交导师数据。
- 普通抓取和信息补全同时存在时，执行中的详情工作项总数不超过运行设置。

### API 与任务中心

- 单次活动查询能在页面刷新后恢复运行状态。
- 单次隐藏任务不会出现在教师抓取或信息补全任务列表。
- 批量任务只出现在“信息补全”列表。
- 信息补全列表不传身份 ID 也能读取。
- 取消、失败重试、软删除和恢复符合状态限制。
- 明细状态、跳过原因、实际补全字段和脱敏错误正确。

### Token 与日志

- 单次和批量模型调用都映射为 `information_enrichment`。
- 普通智能抓取仍映射为 `crawl`。
- 汇总、明细、趋势和功能分布不重复计数。
- 未调用模型的跳过项不产生 Token。
- 创建、取消、重试、删除、恢复和 worker 终态均有日志。
- API Key、Authorization 和 Cookie 不出现在通知、任务明细或日志中。

### 前端

- 编辑弹窗右上角显示“智能补全”，且使用已保存导师数据发起请求。
- 发起成功立即显示进行中通知，按钮加载并禁用至终态。
- 成功、无新增字段和失败分别显示正确通知；失败包含脱敏后的原始错误。
- 批量操作卡在正常导师视图显示“批量智能补全”，在回收站不可发起。
- 任务中心第四个页签为“信息补全”，位于“匹配分析”右侧。
- 详情抽屉显示汇总、明细、跳过原因和错误。
- 设置项只有一项，并发默认回退值为 3，文案说明共享范围。

## 验收标准

- 用户可从导师编辑弹窗发起单导师智能补全，开始和结束均收到通知，运行期间对应按钮不可重复点击。
- 用户可从导师管理页对已选导师创建一个批量补全任务，并在任务中心“信息补全”页签持续查看。
- 缺少主页、资料完整、已归档或已有活动任务的批量项显示为跳过，不导致整批失败。
- 补全只填充邮箱、职称、系所、研究方向和近期论文的空值，任何已有内容都不会被覆盖。
- 单次任务不出现在任务中心；单次和批量任务都出现在 Token 消耗中心并归类为“信息补全”。
- 新旧详情页补全共享 `crawler_profile_enrichment_concurrency = 3` 的全局上限。
- 失败通知和任务明细提供经敏感信息脱敏后的原始异常文本。
- 教师抓取、候选审核、导师编辑、任务中心其他三类任务和既有 Token 统计没有行为回归。
