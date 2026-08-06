# 智能抓取页面抓取账本设计

## 背景

当前智能抓取为了降低 DeepSeek 调用成本，引入了 chunk 处理和 Agent 短链重启。这个方向是正确的：及时切断无价值历史，避免单个 Agent 上下文无限膨胀。

但测试暴露了一个新问题：同一个入口页在一次抓取任务中被反复抓取。数据库记录显示，某任务中的同一 URL 先 HTTP 403，再 browser 403，随后 browser 又抓了一次。根因不是 chunk 机制本身，而是页面抓取状态没有成为任务级持久事实：

- 成功页和 chunk 状态可以在部分路径上避免重复；
- 明确失败页只作为 `crawl_pages` 日志写入；
- Agent 重启后内存缓存丢失；
- 新一轮 Agent 不会基于数据库中的失败记录决定“这个 URL 是否还值得抓”。

因此，根治方向不是继续扩大 Agent 内存缓存，而是把“页面是否应该再次抓取”提升为数据库中的任务级页面抓取账本。

## 目标

1. 在同一个抓取任务内，避免重复抓取已经明确无价值的页面。
2. Agent 重启、worker 重启、应用重启后，页面抓取决策仍可恢复。
3. 不因为省 token 牺牲正常功能流程：临时失败必须保留合理重试机会。
4. 让抓取前置判断、抓取结果记录、重试策略有清晰边界，便于测试和后续维护。

## 非目标

- 不做跨任务全局 URL 黑名单。同一个 URL 在不同任务中仍应独立判断。
- 不永久屏蔽所有失败页面。只有明确无价值失败才跳过。
- 不用 Agent 的自然语言记忆承担页面去重职责。
- 不改变候选导师保存、chunk 拆分、详情补全的业务语义。

## 核心原则

所有节省 token 的优化必须服从一个原则：不能对正常功能流程造成风险或负面影响。

因此页面账本只阻止“确定不值得再次抓”的请求；对仍可能恢复的临时失败，必须允许有限重试。

## 页面账本模型

页面账本以 `job_id + normalized_url` 为唯一任务级页面身份。建议新增独立表 `crawl_page_fetch_states`，不要直接把 `crawl_pages` 日志表改造成状态机。

建议字段：

- `id`
- `job_id`
- `normalized_url`
- `original_url`
- `status`
- `last_fetch_method`
- `terminal_reason`
- `transient_failure_count`
- `last_error_message`
- `last_page_id`
- `first_seen_at`
- `last_attempted_at`
- `updated_at`

`normalized_url` 用于去重，至少应统一：

- scheme 和 host 小写；
- 去掉 fragment；
- 保留 path、query；
- 处理尾部斜杠时保持保守，避免把不同页面误合并。

## 状态定义

页面账本状态建议使用以下几类：

- `new`：没有账本记录，允许正常抓取。
- `succeeded`：页面已成功抓取，后续应复用页面/进入 chunk，不重复 fetch。
- `chunked`：页面已生成待处理 chunk，后续应领取 chunk，不重复 fetch。
- `processed`：该页面的 chunk 已处理完成，后续不再返回正文。
- `transient_failed`：临时失败，允许按重试预算再次抓取。
- `terminal_failed`：明确无价值失败，后续直接跳过，不再抓取。

## 失败分类

### 终止失败

以下情况应标记为 `terminal_failed`：

- HTTP 403 / 412 / 429 且响应近乎为空，明确提示需要浏览器或反爬；
- browser 抓取后仍然是 anti-bot、captcha、cloudflare、access denied、security check；
- 浏览器返回近乎空响应，并被系统识别为反爬或无正文拦截；
- URL 被安全策略拒绝，例如非公开地址、不同域、危险协议。

这些失败通常不是“再抓一次就好”的问题。重复抓只会浪费 token、增加日志噪音，还可能触发站点更强拦截。

### 临时失败

以下情况应标记为 `transient_failed`：

- 网络超时；
- DNS 或连接中断；
- 服务端 5xx；
- Crawl4AI 启动或执行的偶发异常；
- wait condition failed；
- browser returned no result，但没有明确反爬证据。

这些失败不能直接永久跳过。后续同一任务内允许有限重试。

## 重试策略

`transient_failed` 页面应有任务级重试预算。建议默认最多重试 2 次。

抓取前判断：

- `transient_failed` 且 `transient_failure_count < 2`：允许再次抓取；
- `transient_failed` 且 `transient_failure_count >= 2`：标记为 `terminal_failed`，原因记录为 `transient_retry_exhausted`，后续跳过；
- `terminal_failed`：直接返回跳过结果；
- `succeeded/chunked/processed`：按已有成功或 chunk 流程处理。

这里的 2 次不是为了省 token 随意截断，而是为了避免临时故障无限循环。未来可以做成运行时设置，但初版应保持简单。

## 抓取前决策流程

每次调用 `crawl_page` 或 `investigate_with_browser` 前，先执行页面账本判断：

1. 归一化 URL。
2. 查询 `crawl_page_fetch_states` 中当前 `job_id + normalized_url` 的记录。
3. 根据状态返回决策：
   - `skip_terminal_failed`：返回已失败且不重试的结构化结果；
   - `reuse_success`：返回已有页面摘要或引导领取 chunk；
   - `claim_chunk`：提示领取待处理 chunk；
   - `allow_retry`：允许继续抓取；
   - `allow_first_fetch`：允许首次抓取。
4. 只有 `allow_retry` 和 `allow_first_fetch` 会真正访问网络或浏览器。

## 抓取后更新流程

每次真实抓取结束后，必须更新页面账本：

- 成功：写入 `succeeded`，记录 `last_page_id`。
- 成功且生成 chunk：写入 `chunked`。
- chunk 全部处理完成：写入 `processed`。
- 终止失败：写入 `terminal_failed`，记录原因和错误信息。
- 临时失败：写入 `transient_failed`，累加 `transient_failure_count`。

`crawl_pages` 继续作为详细抓取日志保留；页面账本只保存最新决策状态。

## Agent 重启行为

Agent 重启后会重新创建 `CrawlToolContext`，所以不能依赖内存缓存判断页面是否抓过。

新一轮 Agent 开始时：

- 如果数据库存在 pending chunk，应直接领取 chunk；
- 如果 Agent 试图重新抓入口页，工具层必须先查页面账本；
- 如果入口页已是 `terminal_failed`，工具层直接返回跳过结果，Agent 不会触发真实抓取；
- 如果入口页已是 `chunked/processed`，工具层引导 Agent 处理 chunk 或结束。

这样即使短链 Agent 反复重启，也不会重复抓明确无价值页面。

## 与现有短期内存缓存的关系

内存缓存仍然可以保留，用于同一 Agent 运行内快速复用 `PageSnapshot`，减少数据库查询和对象重建。

但内存缓存只能是性能优化，不再是正确性依赖。正确性必须由数据库页面账本保证。

## 用户可见行为

当页面被账本跳过时，调试日志应明确说明：

- URL；
- 跳过原因；
- 上次抓取方式；
- 上次错误摘要；
- 是否因为临时失败重试次数耗尽。

用户看到的结果应是“页面被反爬/无法访问，已跳过”，而不是静默消失。

## 测试要求

至少覆盖以下场景：

1. 同一 Agent 内重复请求同一反爬失败 URL，只真实抓取一次。
2. Agent 重启后重复请求同一 `terminal_failed` URL，不再真实抓取。
3. 临时失败第一次后，下一轮仍允许重试。
4. 临时失败达到重试上限后，转为 `terminal_failed`。
5. 成功页生成 chunk 后，重复抓取同 URL 会引导领取 chunk，而不是重新 fetch。
6. 不同 `job_id` 的相同 URL 不互相影响。
7. URL fragment 不影响去重；query 保守保留，避免误合并分页或筛选页。

## 迁移与兼容

新增表后不需要回填历史任务。页面账本从新任务开始生效。

对于已有 `crawl_pages` 日志，仍保持只读展示和调试用途，不改变历史记录语义。

## 风险与缓解

最大风险是误把可恢复页面标记为 `terminal_failed`。缓解方式：

- 终止失败分类必须保守；
- 普通网络、5xx、wait condition failed 不直接终止；
- 终止原因必须写入数据库和调试日志；
- 测试覆盖“临时失败仍可重试”。

第二个风险是 URL 归一化误合并。缓解方式：

- 初版保留 query；
- 不激进合并尾斜杠；
- 不做跨域、跨任务共享。

## 成功标准

- 同一任务中，明确反爬失败 URL 在 Agent 重启后不会再次真实抓取。
- 临时失败 URL 仍能按预算重试，不影响正常抓取成功率。
- 调试日志能解释为什么某页面被跳过。
- 相关后端测试通过。