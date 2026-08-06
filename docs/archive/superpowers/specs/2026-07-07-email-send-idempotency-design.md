# 发信幂等与中断恢复设计

## 背景

2.3.8 版本后有用户反馈真实发信出现重复发送。同一类反馈包含两种表现：

- 工作区或批量任务中点击一次“立即发送”，老师收到两封邮件。
- 创建模板批量任务后由后台自动发送，部分老师收到两封邮件，其他老师正常。

排查结果显示，底层 SMTP `send_message` 本身只在 `mail_runtime.send_email` 中调用一次；更高概率的问题不在 SMTP 封装重复调用，而在真实发信链路缺少跨请求重试、进程中断、网络中断后的幂等保护。

邮件投递没有真正的 exactly-once 语义。应用调用 SMTP 后，服务商可能已经接受邮件，但本地进程在落库前崩溃或断网。此时系统不能可靠证明“没有发送”，因此设计目标应是 at-most-once：只要进入过真实 SMTP 副作用风险区，就不能自动再次投递同一封邮件。

## 已确认的风险点

### 状态回退入口

以下入口会把任务重新推回可发送或待编辑状态，当前缺少对 `sending/sent/reply_detected` 等真实发信边界状态的严格门禁：

- `approve_and_send_task`：审核并立即发送，会先将任务状态写为 `approved`，再调用 `dispatch_email_task`。
- `approve_draft_task`：审核草稿，会将任务写为 `approved` 或 `scheduled`。
- `approve_and_schedule_task`：审核并定时，会将任务写为 `scheduled`。
- `cancel_scheduled_task`：取消定时，会将任务写为 `review_required`。
- `recover_stale_sending_tasks`：恢复超时 `sending`，会通过 `_restore_or_cancel_interrupted_send` 把任务恢复为 `approved` 或 `scheduled`。

这些入口不是都错误。业务上确实需要允许：

- `scheduled -> review_required`：用户取消定时。
- `review_required/send_failed -> approved/scheduled`：用户重新审核、重新排程。
- 已领取但尚未进入 SMTP 前，批量任务暂停或停止时恢复/取消任务。

需要禁止的是跨越真实发信边界的回退：

- `sent/reply_detected -> approved/scheduled/review_required`
- 已经进入 SMTP 风险区的 `sending -> approved/scheduled`

### 发送中断风险

当前 `dispatch_email_task` 的主要流程是：

1. 条件更新任务为 `sending`。
2. commit 领取结果。
3. 调用 `mail_runtime.send_email`。
4. SMTP 成功返回后，再做已发送箱检查/追加。
5. 最后将任务标记为 `sent` 并写入 `EmailLog`。

2.3.8 增加了 SMTP 成功后的已发送箱检查/追加逻辑，这会拉长任务停留在 `sending` 的时间。如果应用进程在 SMTP 已成功但任务尚未标记 `sent` 之间终止，恢复逻辑会把任务重新变成 `approved/scheduled`，后台 dispatcher 之后可能再次发送。

这能解释“模板批量自动发送中只有部分老师重复”：只有当时卡在这个窗口内的任务会被恢复重发，其他任务不会受影响。

### 前端请求重试风险

前端 `apiFetch` 在桌面端网络错误时会重试一次请求，目前没有区分 GET 和非幂等 POST。对于 `approve-and-send`、`createBatchTask` 等真实副作用请求，如果第一次请求已经在后端产生副作用但响应丢失，第二次 POST 会再次执行。

### 重复创建批次的边界

如果创建模板批量任务的 POST 被完整重试，可能创建第二个批次。若两个批次都完整跑完，通常会表现为所选老师整体重复；若第二个批次被暂停、中止、应用关闭、发送失败或受发送窗口限制，则可能只表现为部分老师重复。

这个问题与单个任务 `sending` 恢复重发不同，但同样需要通过幂等 key 防止。

## 目标

- 保证同一个用户意图不会因为网络重试、前端重放、后端重启而重复真实投递。
- 保证已经进入 SMTP 风险区的任务不会被自动放回发送队列。
- 由系统尽量判断中断发送是否已经成功，而不是要求用户手工检查。
- 对无法高置信判断的发送，系统进入可恢复、可解释的未知状态，而不是永久卡在“进行中”。
- 保留合法的重新发送能力，但必须作为新的显式业务意图处理。

## 非目标

- 不承诺邮件服务商层面的 exactly-once。SMTP、IMAP 和服务商行为不提供这种保证。
- 不把“查不到已发送箱记录”直接等价为“未发送”。IMAP 可能未配置、延迟、被限流，或服务商不保存 SMTP 发出的邮件。
- 不在第一阶段重做所有 IMAP 历史同步架构；只定义发信幂等和恢复所需的最小依赖。

## 设计原则

### 真实副作用边界优先

系统必须明确区分“还没进入 SMTP”和“可能已经进入 SMTP”。

- 还没进入 SMTP：可以由系统安全接管、重试或恢复为可发送。
- 可能已经进入 SMTP：不能自动重发，只能进入核验流程。

### At-most-once 优先

重复发信比漏发后提示更危险。系统在不确定时应选择不自动重发，并继续核验或提示状态不确定。

### 系统自动核验优先

系统应主动用本地记录、SMTP 返回、IMAP 已发送箱、Message-ID、内容指纹等证据判断是否成功。用户不应成为主要判断手段。

### 幂等 key 表示同一次用户意图

同一个 idempotency key 的重复请求必须被视为同一次操作。它不能表达“再执行一次”。如果用户确实要重新发送，必须创建新的显式操作和新的 key。

### 慢同步后置

SMTP 成功返回后，应尽快落库为 `sent`。已发送箱追加、IMAP 历史同步等慢操作不应阻塞任务进入最终发送成功状态。

## 状态模型

现有 `EmailTask.status = sending` 过于粗糙。设计引入持久化的 `EmailSendAttempt`，用它表达一次真实发送尝试的阶段。

### EmailTask 状态

保留现有主要状态，并新增两个状态：

- `send_confirming`：系统正在确认一次中断发送是否已经成功。
- `send_unconfirmed`：系统经过多轮核验后仍无法高置信确认成功或失败，不会自动重发。

`send_confirming` 和 `send_unconfirmed` 都不能被 dispatcher 领取。

### EmailSendAttempt 阶段

新增表 `email_send_attempts`，记录每次真实发送尝试。核心字段：

- `id`
- `email_task_id`
- `attempt_key`：服务端生成的稳定发送尝试 ID。
- `idempotency_key`：来自请求或后台调度的用户意图 key。
- `rfc_message_id`：发送前预生成的 Message-ID。
- `identity_id`
- `professor_id`
- `recipient_email`
- `subject`
- `body_fingerprint`
- `attachment_fingerprint`
- `phase`
- `lease_owner`
- `lease_expires_at`
- `heartbeat_at`
- `smtp_started_at`
- `smtp_accepted_at`
- `verification_started_at`
- `verification_finished_at`
- `verification_confidence`
- `verification_evidence`
- `error_summary`
- `created_at`
- `updated_at`

`phase` 取值：

- `prepared`：已创建发送尝试，已生成 Message-ID，但还没有进入 SMTP。
- `smtp_inflight`：即将调用或正在调用 SMTP，已经进入真实副作用风险区。
- `smtp_accepted`：SMTP 正常返回成功。
- `persisted_sent`：本地任务和发送日志已写入成功。
- `verifying_sent_folder`：正在补做已发送箱同步或核验。
- `completed`：发送成功并完成必要收尾。
- `failed_safe`：确定没有进入 SMTP，可安全重新发送。
- `confirming`：进程恢复后正在确认中断发送结果。
- `unconfirmed`：多轮核验后仍无法确认。

## 核心流程

### 发送前准备

`dispatch_email_task` 不直接进入 SMTP。它先创建或复用一个 `EmailSendAttempt`：

1. 条件领取 `EmailTask`，只允许从 `approved/scheduled/send_failed` 进入发送。
2. 生成 `attempt_key` 和 `rfc_message_id`。
3. 渲染并固化收件人、主题、正文指纹、附件指纹。
4. 将 attempt 写为 `prepared`，任务写为 `sending`。
5. commit。

此时如果进程崩溃，恢复 worker 看到 attempt 还在 `prepared`，可以安全接管并继续发送。

`rfc_message_id` 必须由 `EmailSendAttempt` 预生成并传入邮件构建过程。`mail_runtime` 不能在 SMTP 调用内部隐式生成新的 Message-ID，否则恢复核验无法用同一个稳定标识查证。

### 进入 SMTP 风险区

调用 SMTP 前必须先持久化阶段：

1. 将 attempt 更新为 `smtp_inflight`。
2. 设置 `smtp_started_at` 和租约。
3. commit。
4. 调用 SMTP。

从这一步开始，系统不能再自动把任务恢复为 `approved/scheduled`。

### SMTP 成功返回

SMTP 正常返回后：

1. 将 attempt 更新为 `smtp_accepted`。
2. 立即将 `EmailTask` 写为 `sent`。
3. 写入 `EmailLog`，包含 `rfc_message_id`、attempt 信息、provider payload。
4. commit。
5. 后置执行已发送箱追加/核验。

后置同步失败不改变 `EmailTask.status = sent`，只记录 attempt 的同步结果。

### SMTP 明确失败

如果 SMTP 在建立连接、登录、发送前或发送中返回明确异常，需要区分是否已经进入风险区：

- 还在 `prepared`：标记 `failed_safe`，任务可以进入 `send_failed`，允许用户或系统按既有策略重试。
- 已经 `smtp_inflight`：如果异常无法证明服务商没有接受邮件，进入 `confirming`，不能自动重发。
- 已知 SMTP 明确拒绝且没有接受邮件：标记 `failed_safe`，任务进入 `send_failed`。

具体异常分类应保守处理。无法证明未接受时，按 `confirming` 处理。

## 中断恢复

恢复 worker 定期扫描租约过期或长时间未更新的 attempt。

### 租约仍有效

如果 `lease_expires_at` 未过期，认为操作仍在进行。相同 idempotency key 的请求返回当前任务和 attempt 状态，不重复执行。

### 租约过期且未进入 SMTP

如果 attempt 处于 `prepared`，说明没有进入真实 SMTP 风险区。系统可以接管租约并继续发送，或将任务恢复为可发送状态。

### 租约过期且已进入 SMTP

如果 attempt 处于 `smtp_inflight`、`smtp_accepted` 之前的未知点，系统不能重发。恢复流程：

1. 将任务标记为 `send_confirming`。
2. 将 attempt 标记为 `confirming`。
3. 使用预生成的 `rfc_message_id`、收件人、主题、内容指纹做系统核验。
4. 若高置信成功，写入 `sent` 和 `EmailLog`。
5. 若无法确认，保持 `send_confirming` 并按退避策略继续核验。
6. 多轮核验仍无法确认后，进入 `send_unconfirmed`。

`send_unconfirmed` 不是卡死状态。它表示系统已完成自动恢复判断，但证据不足。该状态不会被后台 dispatcher 自动发送。

## 系统核验策略

系统按证据强度判断中断发送是否成功。

### 高置信成功

满足任一条件可判定成功：

- SMTP 调用已正常返回，并且 attempt 到达 `smtp_accepted`。
- 本地已有同 `attempt_key` 或同 `rfc_message_id` 的 sent `EmailLog`。
- 已发送箱中按 `Message-ID` 命中同一邮件。
- 已发送箱中命中同收件人、同主题、相近发送时间窗口、同内容指纹的邮件。

### 中等置信成功

满足以下组合时可判定为“很可能已发送”，但应在 `verification_evidence` 中记录证据：

- 已发送箱中找到同收件人和同主题的邮件。
- 邮件时间落在 attempt `smtp_started_at` 后的合理窗口内。
- 正文文本或 HTML 指纹高度一致。

### 无法确认

以下情况不能直接判定未发送：

- IMAP 未配置。
- 已发送箱发现失败。
- 服务商限流。
- 已发送箱 Message-ID 查找失败。
- 用户服务商不自动保存 SMTP 发出的已发送邮件。

无法确认时，系统应继续核验或进入 `send_unconfirmed`，但不能自动重发。

### 核验节奏

建议使用退避节奏：

- 第 1 次：恢复后立即查。
- 第 2 次：30 秒后。
- 第 3 次：2 分钟后。
- 第 4 次：10 分钟后。
- 第 5 次：30 分钟后。

超过最大核验次数后进入 `send_unconfirmed`。后续 IMAP 历史同步如果发现证据，仍可把任务修正为 `sent`。

## Idempotency-Key 语义

新增表 `idempotency_records`，记录用户意图级别的幂等状态。

核心字段：

- `key`
- `scope`：例如 `email_task:{task_id}:approve_and_send`、`batch_task:create`。
- `request_fingerprint`
- `status`
- `resource_type`
- `resource_id`
- `response_payload`
- `attempt_id`
- `error_summary`
- `lease_expires_at`
- `created_at`
- `updated_at`

`status` 取值：

- `in_progress`
- `completed`
- `failed_before_side_effect`
- `side_effect_started`
- `recovering`
- `completed_unknown`

### 同 key 再来时的响应

- `completed`：返回第一次操作的结果，不重复执行。
- `in_progress` 且租约有效：返回当前资源状态，表示操作仍在进行。
- `in_progress` 且租约过期，但未进入副作用区：接管并继续。
- `side_effect_started`：不重复执行，进入或返回恢复核验状态。
- `recovering`：返回当前恢复状态。
- `completed_unknown`：返回当前任务状态和不确定原因。
- `failed_before_side_effect`：同 key、同请求指纹可以重新执行；如果已有资源绑定则返回当前失败结果，不重复创建资源。

### 请求指纹不一致

同一个 key 如果带来不同请求内容，返回冲突错误。不能用同一个 key 表达不同操作。

### 后台自动发送的 key

后台 dispatcher 没有前端传入的 key，必须由服务端生成稳定 key。模板批量自动发送的默认 key 使用：

`auto-send:{email_task_id}:{send_generation}`

其中 `send_generation` 初始为 `1`。只有显式重新发送才会生成新的 generation。普通恢复、网络重试、dispatcher 重入都必须复用同一个 generation，不能创建新的发送意图。

### 真正需要重复执行

如果业务上确实要再次发送，必须是新的显式业务动作：

- 使用新的 idempotency key。
- 创建新的 `EmailSendAttempt`。
- 记录 `resend_of_attempt_id` 或等价来源。
- UI 必须明确展示这是“重新发送”，不是自动恢复。

同一个 key 永远只代表同一次用户意图。

## 前端与 API 调整

### 前端

- 对真实副作用 POST 生成并传递 `Idempotency-Key`。
- `apiFetch` 不应对没有幂等保护的 POST 自动重试。
- 对已经有幂等 key 的 POST，可以重试，但后端必须按 key 返回同一次操作状态。

需要覆盖的入口：

- 创建批量任务。
- 工作区 `approve-and-send`。
- 批量任务 item `approve-and-send`。
- 其他会触发真实发信或创建批量任务的入口。

### 后端

- 每个真实副作用入口先登记 idempotency record。
- 注册成功后再进入业务逻辑。
- 业务逻辑中将 record 与新建资源或 send attempt 绑定。
- 响应返回当前资源状态，而不是盲目重新执行。

## 状态门禁

### 允许进入 approve-and-send 的状态

`approve_and_send_task` 只允许以下状态：

- `review_required`
- `approved`
- `scheduled`
- `send_failed`

如果任务已是：

- `sending`
- `send_confirming`
- `send_unconfirmed`
- `sent`
- `reply_detected`
- `canceled`

则不能无条件写回 `approved`。

处理方式：

- `sending/send_confirming`：返回当前发送状态。
- `send_unconfirmed`：返回当前未知状态，需要显式恢复或重新发送动作。
- `sent/reply_detected`：返回当前已发送状态，不重复发送。
- `canceled`：沿用现有错误语义。

### 允许进入 approve/schedule/cancel schedule 的状态

审核、排程、取消定时只允许在尚未进入真实发信边界的状态执行：

- `discovered`
- `matched`
- `draft_failed`
- `review_required`
- `approved`
- `scheduled`
- `send_failed`

不允许对 `sending/send_confirming/send_unconfirmed/sent/reply_detected` 执行会回退状态的编辑动作。

### Dispatcher 领取状态

dispatcher 只能领取：

- `approved`
- `scheduled`

不能领取：

- `sending`
- `send_confirming`
- `send_unconfirmed`
- `sent`
- `reply_detected`

## 已发送箱同步调整

当前 SMTP 后的已发送箱检查/追加在 `mail_runtime.send_email` 内同步执行，会延迟任务落库。新设计将其拆出主发送事务：

1. `send_email` 只负责构建并发送 SMTP，返回 Message-ID 和 SMTP provider payload。
2. `dispatch_email_task` 在 SMTP 返回后立即写入 `sent`。
3. 已发送箱同步以后台任务或 attempt 后置步骤执行。
4. 同步结果写入 attempt/provider payload，不改变 `EmailTask.sent` 的事实。

如果后置同步发现服务商已自动保存，则记录 `existing_copy_found`。如果没有保存并成功 append，则记录 `appended`。如果失败，则记录失败原因，但不重发邮件。

## 批量任务场景

### 模板批量自动发送

模板批量任务创建后，email task 会直接进入 `approved` 或 `scheduled`，由 dispatcher 自动发送。幂等保护点：

- 创建批量任务 POST 使用 idempotency key，避免重复创建批次。
- 每个 email task 的自动发送都有独立 `EmailSendAttempt`。
- 单个 attempt 中断后进入系统核验，不自动恢复为 `approved/scheduled`。

这样即使某一封在 SMTP 风险区中断，也只会进入 `send_confirming/send_unconfirmed`，不会自动再发。

### 逐封审核后立即发送

批量 item 的 `approve-and-send` 使用与工作区相同的幂等语义。重复请求不会把已发送 item 改回 `approved`，也不会再次调用 SMTP。

### 重复创建批次诊断

如果未来仍发现批量任务部分重复，应通过以下证据区分原因：

- 不同 `batch_task_id` 给同一老师发送：偏向重复创建批次或显式重发批次。
- 同一 `email_task_id` 多次 attempt：偏向中断恢复或重复请求。
- 同一 attempt 多次 SMTP 调用：违反本规格，应视为严重 bug。

## 迁移与兼容

### 数据库迁移

新增：

- `email_send_attempts`
- `idempotency_records`

`email_tasks.status` 增加：

- `send_confirming`
- `send_unconfirmed`

旧数据迁移：

- 已有 `sent_at` 且状态仍为非终态的任务，沿用现有逻辑修正为 `sent`。
- 当前处于 `sending` 的旧任务迁移为 `send_confirming`，并创建一条兼容 attempt，使用现有 `last_rfc_message_id` 或生成缺失标记。恢复 worker 后续进行系统核验。
- 没有足够证据的旧 `sending` 不自动恢复为 `approved/scheduled`。

### API 兼容

前端未传 idempotency key 的旧请求：

- 对非发信类请求保持现状。
- 对真实发信类请求，后端可以生成临时 key，但无法跨前端重试稳定复用。桌面前端应同步升级。

## 测试要求

### 后端单元测试

- 同一个 `approve-and-send` idempotency key 重放，只调用一次 SMTP。
- 同一任务已 `sent` 后再次 approve-and-send，不会重发。
- `approve_draft_task` 不能把 `sent` 改回 `approved`。
- `approve_and_schedule_task` 不能把 `sent` 改回 `scheduled`。
- `cancel_scheduled_task` 不能把 `sent/sending/send_confirming` 改回 `review_required`。
- `prepared` attempt 租约过期后可被接管。
- `smtp_inflight` attempt 租约过期后进入 `confirming`，不会重新 SMTP。
- SMTP 成功返回后，即使已发送箱同步失败，任务仍为 `sent`。
- `recover_stale_sending_tasks` 不再把已进入 SMTP 风险区的任务恢复为 `approved/scheduled`。

### 集成测试

- 模板批量自动发送中，模拟 SMTP 后进程中断，恢复后不重复发送。
- 工作区单击发送后模拟前端网络错误重试，后端只发送一次。
- 创建批量任务 POST 重试，后端只创建一个批次。
- 同 key 不同请求体返回冲突。
- 显式重新发送使用新 key，产生新的 attempt，并保留来源记录。

### 诊断测试

- operation log 记录 request id、idempotency key、attempt key。
- duplicate-send 排查能通过日志区分重复批次、重复请求和中断恢复。

## 观测与诊断

新增操作日志事件：

- `email_send_attempt.prepared`
- `email_send_attempt.smtp_inflight`
- `email_send_attempt.smtp_accepted`
- `email_send_attempt.persisted_sent`
- `email_send_attempt.confirming`
- `email_send_attempt.confirmed_sent`
- `email_send_attempt.unconfirmed`
- `email_send_attempt.failed_safe`
- `idempotency.replayed`
- `idempotency.conflict`

每条日志至少包含：

- `email_task_id`
- `batch_task_id`
- `identity_id`
- `professor_id`
- `recipient_email`
- `attempt_key`
- `idempotency_key`
- `rfc_message_id`
- `phase`
- `request_id`

## 用户体验

### 正在发送

当任务处于 `sending` 或 `send_confirming`：

- UI 显示“正在确认发送结果”。
- 禁用再次发送、审核、排程、取消定时等会回退状态的操作。
- 可以允许刷新状态。

### 已发送

当系统高置信确认成功：

- UI 显示已发送。
- 展示发送时间和 Message-ID。
- 如果已发送箱同步失败，只在详情中提示同步状态，不影响发送成功。

### 未确认

当进入 `send_unconfirmed`：

- UI 显示“发送结果未确认”。
- 展示系统已做过的核验证据。
- 后台仍可在 IMAP 历史同步中继续修正。
- 不自动重发。

如果产品需要允许用户重新发送，应提供单独的显式动作，使用新的 key 和新的 attempt，并清楚标注这是一次新的发送。

## 实施顺序建议

1. 收紧状态门禁，禁止 `sent/reply_detected/sending` 被审核、排程、取消定时等入口回退。
2. 拆分 SMTP 成功落库与已发送箱同步，做到 SMTP 成功后立即标 `sent`。
3. 引入 `EmailSendAttempt`，记录阶段、租约、Message-ID 和核验证据。
4. 改造 `recover_stale_sending_tasks` 为阶段化恢复。
5. 引入 idempotency records，覆盖创建批量任务和真实发信入口。
6. 调整前端 POST 重试策略和 Idempotency-Key 传递。
7. 补齐诊断日志和重复发送排查视图。

## 成功标准

- 同一用户意图重复提交不会造成重复 SMTP。
- 已经 `sent` 的任务无法被旧入口重新推回可发送状态。
- 进程在 SMTP 风险区中断后，系统不会自动重发。
- SMTP 成功后慢速 IMAP 同步不会让任务长时间停留在 `sending`。
- 模板批量自动发送中的单封中断只会进入确认流程，不会被 dispatcher 自动再次领取。
- 诊断日志足以区分重复创建批次、重复请求、发送中断恢复三类问题。
