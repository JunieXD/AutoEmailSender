# IMAP 同步 Worker 拆分实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 将用户体验关键的增量邮件同步从历史补扫中拆出，补齐 provider throttle 闭环，避免历史慢跑拖慢新邮件同步。

**架构：** 后台运行两个 IMAP worker：增量 worker 只处理 INBOX/Sent 新邮件和状态 ensure，历史 worker 独立慢跑 professor-folder 状态。账号锁拆为增量锁与历史锁，history SEARCH/FETCH 继续使用每账号平滑限速。

**技术栈：** FastAPI 后端，asyncio worker loop，SQLAlchemy async session，unittest。

---

### 任务 1：增量 worker 与历史 worker 拆分

**文件：**
- 修改：`backend/app/services/task_runtime.py`
- 修改：`backend/app/services/runtime_manager.py`
- 测试：`backend/test/test_imap_sync_runtime.py`
- 测试：`backend/test/test_runtime_manager.py`

- [ ] **步骤 1：编写失败测试**

测试 `poll_for_replies_once` 只调用增量入口，不调用历史入口；新增 `poll_imap_history_once` 只调用历史入口。

- [ ] **步骤 2：运行测试确认失败**

运行：`cd backend && rtk uv run python -m unittest test.test_imap_sync_runtime test.test_runtime_manager`
预期：新测试失败，因为历史仍在原 worker 中执行，runtime manager 未创建 history worker。

- [ ] **步骤 3：实现最小代码**

新增 `sync_identity_incremental_poll_once`、`sync_identity_history_poll_once` 或等价私有函数；`poll_for_replies_once` 遍历账号只跑增量；新增 `poll_imap_history_once` 遍历账号只跑历史。`RuntimeManager.start` 新增 `imap-history-poller`。

- [ ] **步骤 4：运行测试确认通过**

运行：`cd backend && rtk uv run python -m unittest test.test_imap_sync_runtime test.test_runtime_manager`

### 任务 2：锁与限流闭环

**文件：**
- 修改：`backend/app/services/task_runtime.py`
- 测试：`backend/test/test_imap_sync_runtime.py`

- [ ] **步骤 1：编写失败测试**

覆盖 inbox 增量触发 account throttle 后同轮不再跑 sent，也不跑 history；覆盖 discovery 抛 provider throttle 后写入 throttle 并跳过后续 IMAP；覆盖 direct repair 指定 professor_email 时走账号锁。

- [ ] **步骤 2：运行测试确认失败**

运行：`cd backend && rtk uv run python -m unittest test.test_imap_sync_runtime`

- [ ] **步骤 3：实现最小代码**

拆出 `_IMAP_INCREMENTAL_LOCKS`、`_IMAP_HISTORY_LOCKS` 或复用 helper；增量入口在 inbox 后重新检查 `is_imap_incremental_paused`；discovery 捕获 provider throttle 时调用 `mark_imap_throttled`；repair 指定邮箱路径包账号锁。

- [ ] **步骤 4：运行测试确认通过**

运行：`cd backend && rtk uv run python -m unittest test.test_imap_sync_runtime`

### 任务 3：历史正文 fetch 去除无用重复 part

**文件：**
- 修改：`backend/app/services/mail_runtime.py`
- 测试：`backend/test/test_mail_runtime.py`

- [ ] **步骤 1：编写失败测试**

新增历史正文成功解析 text part 的测试；新增重复 text/plain 或 text/html part 时不 fetch 第二个已不需要 part 的测试。

- [ ] **步骤 2：运行测试确认失败**

运行：`cd backend && rtk uv run python -m unittest test.test_mail_runtime`

- [ ] **步骤 3：实现最小代码**

`_fetch_history_text_body_parts_by_uid` 在已拥有对应 content type 后跳过重复 part。

- [ ] **步骤 4：运行测试确认通过**

运行：`cd backend && rtk uv run python -m unittest test.test_mail_runtime`

### 任务 4：最终验证

**文件：**
- 全部相关后端文件

- [ ] **步骤 1：运行相关测试**

运行：`cd backend && rtk uv run python -m unittest test.test_imap_rate_limiter test.test_imap_sync_models test.test_mail_runtime test.test_imap_message_fetcher test.test_imap_sync_runtime test.test_runtime_manager`

- [ ] **步骤 2：运行编译检查**

运行：`cd backend && rtk uv run python -m compileall app`

- [ ] **步骤 3：运行 diff check**

运行：`rtk git diff --check`
