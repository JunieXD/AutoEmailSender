# 近两自然年邮箱历史同步重设计

## 背景

现有邮箱历史同步已经具备 IMAP 增量同步、Sent 文件夹发现、老师通信记录落库和多层去重能力。但当前历史补齐路径里有一段邮箱级倒序 UID 区间扫描：从文件夹最高 UID 起，每批按固定 UID 数字窗口向 0 扫描，直到 `next_before_uid <= 0` 才认为历史扫描完成。

这个策略在 163 等邮箱上会失效。UID 是文件夹内的稳定编号，不代表连续邮件序号，也不代表邮件数量。163 邮箱 UID 可达到十亿级且可能稀疏，按 `1743736000:1743736199` 这类数字区间扫描会消耗大量空区间，导致历史同步长期停留在 `pending`，老师级 targeted 同步也被前置门槛阻塞。

本设计保留“邮箱维度优先”的产品动机：用户可能有几千个老师，但真实发信数量只有几十封。历史同步不应对每个老师逐个全量搜索，而应先从已发送文件夹里找出最近实际发生过通信的邮件，再映射回系统已有老师。

## 目标

- 默认同步当前自然年和上一自然年的邮箱历史。例如 2026 年运行时，窗口从 2025-01-01 开始。
- 移除 UID 倒序区间历史扫描，不再把 UID 数字空间当作邮件集合遍历。
- 历史 Sent 同步按真实存在的邮件 UID 执行，适配老师库大、真实发信少的场景。
- 历史 INBOX 同步只围绕已联系老师执行，避免扫描整个收件箱。
- 增量同步继续使用 `last_seen_uid` 独立运行，覆盖软件关闭期间新增收件和发件。
- 所有历史导入继续复用现有 `upsert_email_log` 去重与合并逻辑。
- 一封物理邮件命中多个系统老师时，为每个老师各建立一条通信记录。
- 用户侧仍看到统一通信记录，不展示系统来源和 IMAP 技术细节。

## 非目标

- 不默认同步全部历史。
- 不提供第一版“同步全部历史”入口。
- 不自动发现或导入系统外的新老师联系人。
- 不构建通用邮箱客户端。
- 不默认下载附件二进制内容。
- 不在数据库迁移过程中执行 IMAP 网络请求。
- 不尝试同步已归档到非 INBOX/Sent 文件夹的全部邮件。

## 核心口径

### 自然年窗口

历史同步窗口按自然年计算：

- 当前年份为 `Y`。
- 历史同步起始日期为 `Y - 1` 年 1 月 1 日。
- IMAP 搜索日期使用该日期，例如 `01-Jan-2025`。

2026 年运行时，默认同步从 2025-01-01 到当前时间的邮件。2027 年运行时，默认同步从 2026-01-01 开始。

### 同步分层

系统保留两类同步：

1. 增量同步：文件夹级 cursor，用于处理新增邮件。
2. 近期历史同步：自然年窗口内的 Sent 发现和 INBOX 回复补齐。

增量同步不依赖历史同步完成。历史同步也不再要求邮箱级扫描 `completed` 后才能创建或运行老师级同步。

### 邮箱维度的正确用法

邮箱维度同步的入口是“真实邮件集合”，不是 UID 数字区间：

- 使用 `UID SEARCH SINCE <start_date>` 获取真实存在的 UID。
- 分批 `UID FETCH` 这些 UID 的 header。
- 从 header 中解析发件人、收件人、抄送、密送、Message-ID、Date、References 等信息。
- 只有命中系统已有老师或已有通信链路的邮件才拉正文和落库。

禁止继续使用类似 `FETCH 1743648160:1743648359` 的倒序区间扫描作为历史同步策略。

## 历史同步流程

### 阶段一：Sent 近期发现

Sent 阶段负责发现当前身份最近两自然年实际发给哪些系统老师。

流程：

1. 获取或发现当前身份的 Sent 文件夹。
2. 对 Sent 文件夹执行 `UID SEARCH SINCE <history_start_date>`。
3. 得到真实存在的 UID 列表。
4. 按批次获取轻量 header，不拉附件。
5. 从 `TO`、`CC`、`BCC` 提取并规范化收件人邮箱。
6. 将收件人邮箱与未归档的 `professors.email` 匹配。
7. 对每个匹配老师拉取必要正文并写入一条 `direction = sent` 的通信记录。
8. 如果一封邮件命中多个老师，为每个老师各写一条通信记录。

Sent 阶段输出“已联系老师集合”，供 INBOX 阶段使用。

### 阶段二：INBOX 近期回复补齐

INBOX 阶段负责补齐老师发来的回复或来信，但不扫描整个收件箱。

候选老师来源：

- Sent 近期发现阶段命中的老师。
- 现有 `email_tasks` 中已经联系过或准备联系的老师。
- 现有 `email_logs` 中已经存在通信记录的老师。

流程：

1. 构建候选老师邮箱集合。
2. 对每个候选老师执行 `UID SEARCH FROM "<professor_email>" SINCE <history_start_date>`。
3. 分批获取命中 UID 的 header。
4. 跳过已经落库的邮件。
5. 对缺失邮件拉取文本正文并写入 `direction = received` 的通信记录。
6. 如果邮件能命中既有任务，将相关任务推进到回复状态；没有任务也允许作为老师通信记录存在。

INBOX 阶段不再等待 Sent 或 mailbox 历史扫描“全量完成到 UID 0”。Sent 阶段产生的候选老师可以渐进进入 INBOX 阶段。

### 增量同步

现有文件夹级增量同步继续保留：

- `INBOX` 通过 `last_seen_uid + 1:*` 搜索新增收件。
- Sent 文件夹通过 `last_seen_uid + 1:*` 搜索新增发件。
- `uidvalidity` 不变时推进 `last_seen_uid`。
- `uidvalidity` 变化时重置 cursor，并依赖近期历史同步在自然年窗口内补齐。

增量同步的职责是“从现在开始不漏新增邮件”，不负责补齐历史窗口内所有旧邮件。

## 状态模型

### 文件夹级状态

`imap_mailbox_sync_states` 继续作为文件夹级 cursor 和诊断状态使用：

- `identity_id`
- `folder_role`
- `folder`
- `uidvalidity`
- `last_seen_uid`
- `last_sync_at`
- `last_error`
- Sent 文件夹发现相关字段
- 节流相关字段

旧的历史扫描字段不再作为流程门槛。迁移后可保留字段以兼容旧库，但新流程不应依赖 `history_scan_status = completed` 才启动老师相关历史同步。

### 近期历史状态

需要为近期历史同步记录阶段进度。实现可以复用现有表并调整语义，也可以新增轻量状态表；无论选择哪种，状态语义应覆盖：

- `sent_recent_discovery_pending`
- `sent_recent_discovery_running`
- `inbox_recent_replies_pending`
- `inbox_recent_replies_running`
- `completed`
- `failed`

状态记录至少需要包含：

- `identity_id`
- 历史窗口起始日期
- 当前阶段
- 最近处理的 folder role
- 最近处理的 UID 或 professor email
- 已扫描 header 数
- 已匹配老师数
- 已写入通信记录数
- `last_error`
- `started_at`
- `completed_at`
- `updated_at`

状态应支持应用关闭后恢复，并支持重复运行不产生重复通信记录。

## 去重与合并

新历史同步必须通过 `upsert_email_log` 写入通信记录，不直接 `session.add(EmailLog)`。

现有去重层继续作为权威逻辑：

1. `identity_id + professor_id + direction + normalized_message_id`
2. `identity_id + professor_id + folder_role + folder + uidvalidity + imap_uid`
3. `identity_id + professor_id + direction + message_fingerprint`

多收件人场景下，`professor_id` 是去重键的一部分，因此同一封物理邮件可以为多个命中的老师分别建立通信记录。重复运行历史同步时，同一老师下不会重复增加记录。

合并规则继续遵循现有逻辑：

- 已有记录非空字段不被覆盖。
- 新同步结果可以补齐旧记录缺失的 `folder_role`、`folder`、`uidvalidity`、`imap_uid`、`from_email`、`to_emails`、`cc_emails`、`bcc_emails`、`reply_headers` 等字段。
- `synced_at` 在每次成功合并时更新。

## 错误处理与节流

- 每个身份继续使用身份级锁，避免后台同步、手动刷新和历史任务并发访问同一邮箱。
- 保留现有 IMAP 命令速率限制和服务商节流检测。
- Sent 文件夹发现失败时记录错误，不阻塞 INBOX 增量同步。
- 某个老师的 INBOX targeted 搜索失败时，只标记该老师或该阶段失败，不影响其他老师。
- 服务商临时限流时暂停该身份的历史同步，保留可恢复状态。
- 历史同步失败不应阻塞日常增量同步。

## UI 与用户可见行为

第一版不新增复杂配置：

- 默认自动同步当前自然年和上一自然年。
- 不提供“全部历史同步”按钮。
- 用户侧通信时间线继续展示统一通信记录。
- 不展示 `ingest_source`、`uidvalidity`、`imap_uid` 等技术字段。

如果需要展示进度，文案应使用产品语义，例如“正在同步最近两年邮件记录”，不要展示 UID 区间或 IMAP 内部细节。

## 测试策略

### 单元测试

- 自然年窗口计算：2026 年返回 2025-01-01，2027 年返回 2026-01-01。
- Sent 搜索使用 `UID SEARCH SINCE <start_date>`，不再调用 UID 区间 FETCH。
- Sent header 中多个收件人命中多个老师时，为每个老师生成一条记录。
- Sent 重复运行不增加重复记录。
- INBOX 候选老师集合由 Sent 命中结果和已有任务/日志合并。
- INBOX targeted 搜索只针对候选老师，不对所有老师执行。
- INBOX 重复运行不增加重复记录。
- 无 Message-ID 邮件使用现有 fingerprint 兜底。
- `uidvalidity + imap_uid` 能补齐系统发送后由 Sent 同步回来的记录。

### 集成测试

- 老师库有数千老师、Sent 只有几十封真实邮件时，同步命令数随 Sent 邮件数增长，而不是随老师数增长。
- 旧数据库存在 `history_scan_status = pending` 且 `history_next_before_uid` 巨大时，新流程仍能执行近期历史同步。
- IMAP 登录成功但 Sent 文件夹发现失败时，INBOX 增量同步仍可运行。
- 历史同步失败后，增量同步仍可处理新增邮件。

### 回归验证

- 现有 `email_log_ingestion` 去重测试继续通过。
- 现有 IMAP message fetcher 测试继续通过。
- 现有任务状态推进和回复检测测试继续通过。

## 迁移与兼容

- 不删除旧历史扫描字段，避免破坏现有安装。
- 新运行时不再依赖旧 `history_scan_status` 作为老师同步前置条件。
- 对已经卡在 UID 倒序扫描中的用户，新流程启动后应可以直接进入近期历史同步。
- 旧的 `history_scanned_count`、`history_matched_count` 可作为诊断遗留字段，不参与新同步判断。

## 未来扩展

第一版已明确不支持全部历史同步。未来如果用户需要更长历史，可增加手动入口，并复用同一套“真实 UID 搜索 + header 匹配 + upsert 去重”机制，而不是恢复 UID 数字区间扫描。
