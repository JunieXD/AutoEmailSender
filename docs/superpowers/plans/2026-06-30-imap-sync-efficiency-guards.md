# IMAP 同步省流量与限流保护实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 让邮箱通信同步在不改变用户口径的前提下显著减少 IMAP 流量，并对 163/126/yeah 等黑盒限流邮箱提供保守保护。

**架构：** 增量同步保持优先执行，历史补扫改为批处理但受账号级 IMAP 命令预算与 provider throttle 状态约束。Sent folder 发现、老师状态 ensure、历史正文抓取都改为可缓存/可短路/可回退的形式，保证省流量但不漏邮件。

**技术栈：** FastAPI 后端、SQLAlchemy async ORM、Alembic、Python unittest、IMAP4。

---

### 任务 1：配置与状态模型

**文件：**
- 修改：`backend/app/core/config.py`
- 修改：`backend/app/models/imap_sync.py`
- 创建：`backend/alembic/versions/20260630_imap_efficiency_guards.py`
- 测试：`backend/test/test_imap_sync_models.py`

- [ ] **步骤 1：写失败测试**

覆盖默认 5 分钟轮询、新增 IMAP 预算配置、mailbox state 中 Sent folder 缓存和 throttle/ensure 字段、professor state 查询索引存在。

- [ ] **步骤 2：运行红灯测试**

运行：`cd backend && uv run python -m unittest test.test_imap_sync_models`

- [ ] **步骤 3：实现模型与迁移**

在 `Settings` 增加：
- `imap_poll_interval_seconds` 默认从 60 改为 300。
- `imap_history_batch_size` 默认 50。
- `imap_history_command_budget_per_minute` 默认 20。
- `imap_fetch_batch_size` 默认 20。
- `imap_sent_folder_failure_ttl_seconds` 默认 3600。
- `imap_throttle_backoff_seconds` 默认 86400。
- `imap_ensure_state_ttl_seconds` 默认 300。

在 `ImapMailboxSyncState` 增加：
- `discovered_sent_folder`
- `sent_folder_discovered_at`
- `sent_folder_discovery_failed_at`
- `sent_folder_discovery_error`
- `throttle_paused_until`
- `throttle_reason`
- `last_professor_state_ensure_at`
- `professor_state_fingerprint`

为 `ImapProfessorSyncState` 增加状态查询索引：`identity_id, historical_scan_status, updated_at, id`。

- [ ] **步骤 4：运行绿灯测试**

运行：`cd backend && uv run python -m unittest test.test_imap_sync_models`

### 任务 2：Sent folder 缓存、ensure 节流与 throttle 状态

**文件：**
- 修改：`backend/app/services/imap_sync_state.py`
- 修改：`backend/app/services/task_runtime.py`
- 测试：`backend/test/test_imap_sync_runtime.py`

- [ ] **步骤 1：写失败测试**

覆盖：
- 成功发现 Sent folder 后写入缓存，下轮不再调用 discovery。
- discovery 失败后在 TTL 内不重复发现。
- 老师列表未变化且 ensure TTL 未过时不重复执行昂贵状态创建。
- 老师新增或邮箱变更时 fingerprint 变化，会重新 ensure。
- 历史补扫遇到 provider throttle 后写入暂停状态。
- throttle 期间跳过历史补扫；账号级 throttle 可让增量降频。

- [ ] **步骤 2：运行红灯测试**

运行：`cd backend && uv run python -m unittest test.test_imap_sync_runtime`

- [ ] **步骤 3：实现运行时状态辅助函数**

增加辅助函数：
- `get_cached_or_discover_sent_folder`
- `should_ensure_professor_scan_states`
- `mark_imap_throttled`
- `is_imap_history_paused`
- `is_imap_incremental_paused`

确保 throttle 只作用于 IMAP 同步流程，不触碰 SMTP 发信。

- [ ] **步骤 4：运行绿灯测试**

运行：`cd backend && uv run python -m unittest test.test_imap_sync_runtime`

### 任务 3：历史补扫批处理、预算与进度日志

**文件：**
- 修改：`backend/app/services/imap_sync_state.py`
- 修改：`backend/app/services/task_runtime.py`
- 测试：`backend/test/test_imap_sync_runtime.py`

- [ ] **步骤 1：写失败测试**

覆盖：
- 每轮最多领取 50 个 professor-folder state。
- 命令预算不足时停止领取并保留剩余状态。
- 每个 state 失败只标记该 state，不阻断整轮。
- 同步日志包含 pending/completed/failed/paused 统计。

- [ ] **步骤 2：运行红灯测试**

运行：`cd backend && uv run python -m unittest test.test_imap_sync_runtime`

- [ ] **步骤 3：实现批处理与预算**

增加 `claim_next_professor_scans(limit=...)`，保留单个 claim 包装以兼容现有调用。历史补扫循环按命令预算扣减，默认每个 state 至少预估一次 SEARCH，实际 fetch 函数返回 command count 时再扣减。

- [ ] **步骤 4：运行绿灯测试**

运行：`cd backend && uv run python -m unittest test.test_imap_sync_runtime`

### 任务 4：IMAP fetcher 省流量能力

**文件：**
- 修改：`backend/app/services/imap_message_fetcher.py`
- 修改：`backend/app/services/mail_runtime.py`
- 测试：`backend/test/test_mail_runtime.py`
- 测试：`backend/test/test_imap_sync_runtime.py`

- [ ] **步骤 1：写失败测试**

覆盖：
- Sent 历史搜索优先用组合 TO/CC/BCC，失败回退三次搜索。
- header 可以批量 FETCH，并限制 batch size。
- header-first 能识别已存在 Message-ID/UID 的邮件并跳过正文。
- 新邮件必须拉正文后才入库；正文失败时 state 不标记 completed。

- [ ] **步骤 2：运行红灯测试**

运行：`cd backend && uv run python -m unittest test.test_mail_runtime test.test_imap_sync_runtime`

- [ ] **步骤 3：实现 header-first 与批量 FETCH**

增加轻量 header fetched 类型或复用 `ImapFetchedMessage` 的 header-only 标记。历史补扫先拉 header，调用数据库去重判断；只对未入库邮件拉正文。批量大小使用配置，默认 20。正文 fetch 小批量或逐封，遇到 provider 不兼容自动回退。

- [ ] **步骤 4：运行绿灯测试**

运行：`cd backend && uv run python -m unittest test.test_mail_runtime test.test_imap_sync_runtime`

### 任务 5：集成验证

**文件：**
- 修改：相关测试与实现文件

- [ ] **步骤 1：运行聚焦测试**

运行：
`cd backend && uv run python -m unittest test.test_unified_email_log_models test.test_imap_sync_models test.test_email_log_ingestion test.test_mail_runtime test.test_imap_sync_runtime test.test_concurrency_guards test.test_contact_status test.test_workspace_support test.test_dashboard_stats`

- [ ] **步骤 2：运行迁移**

运行：`cd backend && uv run alembic upgrade head`

- [ ] **步骤 3：静态检查**

运行：`git diff --check`

- [ ] **步骤 4：总结风险**

说明未运行全量 unittest discover 的原因（若存在外部 OpenAI key / Windows event loop 既有问题），并列出本次新保护如何避免影响 SMTP 发信。
