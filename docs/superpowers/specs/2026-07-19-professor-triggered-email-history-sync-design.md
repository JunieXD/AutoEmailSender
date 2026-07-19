# 导师触发式近期通信同步与旧版本修复设计

## 背景

现有近期邮箱历史同步会先扫描当前身份的 Sent 文件夹，再把邮件收件人与系统内已有导师匹配。邮箱级状态通过 `history_high_water_uid` 记录已扫描进度。

当用户先添加身份、后台在导师库为空时完成 Sent 扫描，随后再通过文件导入、智能抓取或手动新增导师时，旧邮件 UID 已经低于高水位，不会再次参与匹配。这使通信记录是否完整依赖“先添加导师还是先添加身份”的操作顺序。

本设计把近期历史同步改为三层职责明确的机制：

1. 文件夹级 UID 增量同步继续负责新增邮件。
2. 有效导师邮箱进入系统时，创建持久化的近期通信补扫任务。
3. 新版本首次运行时执行一次 `recent-v2` 自动修复，补齐旧策略可能遗漏的记录。

## 已确认产品口径

- 历史窗口为当前自然年和上一自然年；2026 年运行时从 2025-01-01 开始。
- 手动新增、文件导入、智能抓取采纳、补充邮箱、修改邮箱和恢复导师都纳入统一触发语义。
- 只有导师邮箱首次有效、发生变化或从回收站恢复时重新补扫；姓名、学校、标签、备注等资料更新不触发。
- 对所有配置完整 IMAP 的身份分别补扫，不只处理默认身份。
- 导师旧邮箱对应的历史通信记录保留，但旧邮箱不自动成为长期别名，也不参与未来增量匹配。
- 第一版不增加“重新同步最近两年”手动按钮。
- 只覆盖 INBOX 和自动发现的 Sent 文件夹，不扫描任意归档文件夹，不下载附件二进制内容。

## 非目标

- 不构建通用邮箱客户端。
- 不同步系统外联系人。
- 不默认同步全部邮箱历史。
- 不在 Alembic 数据库迁移中执行 IMAP 网络请求。
- 不通过前端请求同步等待邮箱扫描完成。
- 不为旧邮箱自动建立导师邮箱别名模型。

## 总体架构

### 保留的机制

- `ImapMailboxSyncState.last_seen_uid` 文件夹级增量游标。
- INBOX 与 Sent 文件夹发现和 `UIDVALIDITY` 处理。
- 身份级锁、增量锁、历史锁、服务商节流检测和 IMAP 命令限速。
- 先取轻量 header、确认需要后再取正文的分阶段拉取。
- `upsert_email_log` 的 Message-ID、IMAP UID 和 fingerprint 多层去重。
- 通信记录对任务状态和导师状态的现有推进逻辑。

### 改造的机制

- `ImapProfessorSyncState` 从旧 targeted 扫描状态升级为近期通信持久任务。
- `sync_identity_history_once` 从“每轮先扫描 Sent”改为“对账、聚合、选策略、消费任务”。
- Sent 邮箱级高水位只表示当前批次进度，不再跨导师批次永久生效。
- INBOX 不再以 Sent 命中作为扫描门槛；每个新进入系统的有效导师邮箱都会生成 INBOX 定向任务。
- Sent 命中、已有任务或已有日志只影响优先级，不影响是否最终扫描。

### 停用的机制

- UID 数字区间倒序扫描不再进入活动运行路径。
- `legacy` 和 `recent-v1` 导师状态不再被新历史 worker 领取。
- 旧 mailbox history `completed` 不再是导师任务创建或执行的前置条件。
- 旧字段暂时保留以兼容已有数据库，后续版本确认稳定后再删除。

## 状态模型

### 导师同步版本

`professors` 增加内部字段：

- `communication_sync_version`：默认 `1`。

以下变化自动递增版本：

- `email` 发生变化。
- `archived_at` 发生变化。

这样所有 ORM 写入路径都共享同一触发语义，无需分别依赖导入、抓取和手动新增接口。归档后的任务不会被领取；恢复后版本再次变化，会重新创建或重置当前邮箱任务。

### 导师近期任务

复用 `imap_professor_sync_states`，新增：

- `history_start_date`：本次需要覆盖的日期起点。
- `trigger_reason`：`professor_activated`、`email_changed`、`restored`、`identity_configured`、`upgrade_repair`、`sent_folder_discovered` 或 `reconcile`。
- `batch_id`：任务聚合批次或正在执行的 bulk 批次。
- `available_at`：固定聚合窗口截止时间。
- `priority`：新导师任务高于旧版本修复任务。
- `professor_sync_version`：创建任务时观察到的导师通信同步版本。

唯一约束继续使用 `identity_id + professor_id + professor_email + folder_role + folder`。

运行时只领取满足以下条件的任务：

- `history_strategy_version = recent-v2`。
- 导师未归档。
- 状态中的邮箱仍等于导师当前邮箱。
- `professor_sync_version` 等于导师当前通信同步版本。
- `available_at <= now`。
- 状态为 `pending`、`failed` 或超过 stale 时限的 `running`。

### 邮箱批次状态

`imap_mailbox_sync_states` 增加：

- `history_batch_id`：当前邮箱级 Sent 批次。

当新 bulk 批次与当前 `history_batch_id` 不同时，重置 `history_high_water_uid`、计数和错误。相同批次可在软件退出后从高水位继续。

已经开始但尚未完成的 bulk 批次优先于本轮成本判断无条件续跑。即使剩余导师数已经降到快速阈值以下，或近期 Sent 数量后来发生变化，也不会把带有 bulk `batch_id` 的任务留成永久不可领取状态。

新任务在扫描过程中进入系统时使用新批次，不会被当前批次误标为完成。

Sent 文件夹名称变化或暂时不可用时，旧文件夹任务改记为 `recent-v2-obsolete`，不再参与领取。以后同一文件夹再次被发现时，对账会将它重新重置为 `recent-v2`，因此既不会挤占当前任务，也不会永久丢失重试机会。

## 任务创建与对账

每次历史 worker 处理某个身份时，在发起网络请求前执行本地对账：

1. 读取该身份、全部未归档且邮箱有效的导师及已发现的 Sent 文件夹。
2. 为导师当前邮箱创建 INBOX 状态。
3. Sent 文件夹可用时创建 Sent 状态。
4. 缺少状态时创建 `recent-v2 pending`。
5. 状态策略仍为旧版本时，以 `upgrade_repair` 重置为 `recent-v2 pending`。
6. 导师 `communication_sync_version` 更高时，按当前邮箱和版本重置任务。
7. 已归档、旧邮箱或旧版本状态保留用于兼容和诊断，但不会被领取。

对账是正确性的兜底，因此未来新增导师写入入口即使没有显式调用同步 helper，也不会永久漏扫。

## 固定聚合窗口

- 聚合窗口默认 10 秒。
- `available_at` 在任务创建或重置时固定为请求时间加 10 秒。
- 窗口不会因为后续任务到来而滚动延长。
- 同一数据库事务创建的大批导师会在同一次对账中获得相同 `batch_id`。
- worker 开始执行时冻结任务 ID 快照；之后到来的任务进入下一批。

后台轮询可能晚于 10 秒运行，但不得早于 `available_at` 领取任务。

## 少量与大量的策略选择

设：

- `R = min(IMAP_HISTORY_COMMAND_BUDGET_PER_MINUTE, IMAP_HISTORY_COMMAND_RATE_PER_MINUTE)`。
- 预留 25% 命令给 header/body FETCH。
- 组合 Sent 搜索可用时，每位导师基础搜索成本 `C = 2`：Sent 1 条、INBOX 1 条。
- 组合 Sent 搜索不可用、需要 TO/CC/BCC fallback 时，`C = 4`。
- 快速阈值 `T = floor(R * 0.75 / C)`。

默认配置下 `R = 40`，组合搜索对应 `T = 15`。

### 小批量

当待处理不同导师数 `M <= T` 时，直接执行定向搜索：

- Sent：`UID SEARCH SINCE <date> OR(TO/CC/BCC <email>)`。
- INBOX：`UID SEARCH FROM <email> SINCE <date>`。

### 大批量

当 `M > T` 时，先用一条 `UID SEARCH SINCE <date>` 获取 Sent 近期真实 UID 数量 `N`，再比较：

- 定向 Sent 成本约为 `M * sent_search_commands`。
- 邮箱级 Sent 成本约为 `1 + ceil(N / IMAP_FETCH_BATCH_SIZE)`。

仅当以下条件同时满足时选择邮箱级 Sent：

- 邮箱级预计命令更少。
- `N <= IMAP_HISTORY_BULK_HEADER_LIMIT`，默认 5000。

否则继续使用 Sent 定向搜索。INBOX 无论批量大小都逐导师定向搜索，避免扫描整个收件箱。

## 定向扫描流程

1. 在邮箱服务器执行包含自然年窗口的搜索，只返回 UID。
2. 按批次获取命中 UID 的 header。
3. 规范化并精确比较 FROM、TO、CC、BCC，防止 IMAP 子串匹配误关联。
4. 根据 Message-ID 和 IMAP 定位字段判断记录是否已经存在。
5. 只为缺失邮件获取正文文本和 HTML；不下载附件内容。
6. 使用 `upsert_email_log` 写入或合并。
7. 完成当前导师和文件夹状态；命令预算不足时保存 `last_scanned_uid` 并恢复为 pending。

## 邮箱级 Sent 扫描流程

1. 冻结本批待处理 Sent 状态 ID，并分配 bulk `batch_id`。
2. 如果 mailbox `history_batch_id` 不同，重置批次高水位。
3. 复用成本探测时 `UID SEARCH SINCE` 返回的 UID 快照；只有 `UIDVALIDITY` 已变化或没有快照时才重新搜索，然后分批取 header。
4. header 与全部当前有效导师邮箱匹配；已经下载 header 时允许顺带修复非本批导师的缺失 Sent 日志。
5. 只获取缺失正文，并通过统一 upsert 写入。
6. 批次完全覆盖后，只将冻结快照内的 Sent 任务标为 completed。
7. 新增任务不会因为顺带命中而直接标为完成，后续运行依靠去重快速完成。

## `recent-v2` 旧版本自动修复

新版本运行时不在迁移中访问邮箱。历史 worker 首次对账时：

1. 把所有当前有效导师的旧策略状态创建或重置为 `recent-v2`。
2. 对每个配置完整 IMAP 的身份独立处理。
3. Sent 根据成本选择一次邮箱级发现或逐导师定向搜索，完全忽略旧策略的长期高水位。
4. INBOX 为所有有效导师创建定向任务。
5. 优先级依次为：日常 UID 增量、新导师/恢复/邮箱修改、已有联系线索的修复、其他修复。
6. 软件退出、服务商限流或临时失败时保留状态，后续继续。
7. 已有通信记录依靠 upsert 去重，不产生重复时间线项目。

若升级时导师库为空，不执行无意义的全 Sent 历史下载；以后导师进入系统时通过正常任务补扫。

## 调度与错误处理

- 日常增量同步优先于历史任务。
- 历史 worker 继续使用身份级历史锁，同一身份不并发访问 IMAP。
- 新导师任务优先级高于旧版本修复积压。
- Sent 文件夹发现失败不阻塞 INBOX 定向任务。
- 身份缺少 IMAP 配置时保留本地状态，不执行网络请求。
- 服务商限流沿用现有暂停时间；任务不丢失。
- stale running 状态沿用现有恢复机制。
- 旧策略状态永远不与 `recent-v2` worker 竞争领取。

## UI 行为

- 不新增手动重扫按钮。
- 导师新增、导入或抓取采纳请求不等待 IMAP。
- 工作区和统计继续读取统一通信记录，无需展示 ingest source。
- 第一版不新增复杂队列 UI；错误继续通过现有身份/IMAP 诊断信息处理。

## 迁移策略

- Alembic 只新增字段、索引和默认值。
- `professors.communication_sync_version` 对已有导师初始化为 1。
- 现有 IMAP 历史字段和旧状态行不删除。
- 运行时通过 `history_strategy_version = recent-v2` 触发自动修复。
- downgrade 只移除本次新增字段和索引，不尝试恢复网络同步状态。

## 测试与验收

### 状态与触发

- 新导师为所有 IMAP 身份创建 INBOX/Sent 任务。
- 导入、智能抓取和手动新增通过同一模型版本语义被对账发现。
- 邮箱变化和恢复递增通信同步版本并重新排队。
- 普通资料更新不递增版本、不重扫。
- 旧邮箱状态不再被领取。
- 10 秒前任务不可领取，之后可领取；窗口不滚动。

### 策略

- 默认配置计算快速阈值为 15。
- 小批量不执行邮箱级 Sent 搜索。
- 大批量且近期 Sent 较少时选择邮箱级扫描。
- Sent 超过 5000 个近期 UID 时回退定向搜索。
- Sent 定向搜索必须包含 `SINCE`，组合失败时 fallback 也包含 `SINCE`。
- INBOX 对所有新导师执行 `FROM + SINCE`，不再依赖 Sent 候选。

### 顺序与修复回归

- 身份先添加、空导师库已运行同步、后来新增导师，仍能补齐旧 Sent 和 INBOX 邮件。
- `recent-v1` 已完成且高水位越过旧邮件时，升级后 `recent-v2` 仍重新覆盖窗口。
- 一次导入大量导师时按成本选择策略，不逐个下载无关邮件。
- 重复运行、崩溃恢复和批次重试不产生重复通信记录。
- 扫描过程中新增导师不会被当前 bulk 批次误标完成。
- 增量同步在历史修复失败时仍能正常运行。

## 已知边界

- 邮件已从服务器删除时无法从 IMAP 恢复。
- 非 INBOX/Sent 文件夹中的归档邮件不保证补齐。
- BCC 是否保留取决于邮箱服务商 Sent 文件夹行为。
- 旧邮箱不自动作为别名，因此邮箱修改后的未来来信只按新邮箱匹配。
