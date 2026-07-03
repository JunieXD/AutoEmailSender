# 运行与排障手册

## 1. 本地启动
### 1.1 后端
```bash
cd backend
uv sync
uv run alembic upgrade head
uv run python dev_entry.py
```

### 1.2 前端
```bash
cd frontend
npm install
npm run dev
```

## 2. 关键环境变量
参考 `backend/.env.example`：

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `DATABASE_URL` | SQLite 本地文件 | 数据库位置 |
| `DRAFT_WORKER_INTERVAL_SECONDS` | `10` | 兼容保留，当前未启用 |
| `DISPATCHER_INTERVAL_SECONDS` | `30` | 发送 dispatcher 周期 |
| `IMAP_POLL_INTERVAL_SECONDS` | `60` | IMAP 同步周期 |
| `IMAP_HISTORY_BATCH_SIZE` | `200` | 历史文件夹扫描每轮向前看的 UID 窗口大小 |
| `IMAP_HISTORY_COMMAND_BUDGET_PER_MINUTE` | `120` | 单轮历史补扫最多消耗多少条 SEARCH/FETCH 预算 |
| `IMAP_HISTORY_COMMAND_RATE_PER_MINUTE` | `40` | 历史补扫 SEARCH/FETCH 平均每分钟放行多少条 |
| `IMAP_HISTORY_COMMAND_BURST` | `3` | 历史补扫允许的短时突发命令数 |
| `IMAP_FETCH_BATCH_SIZE` | `20` | 历史补扫批量 FETCH 的 UID 数 |
| `LLM_REQUEST_TIMEOUT_SECONDS` | `90` | LLM 请求超时 |
| `SMTP_SEND_TIMEOUT_SECONDS` | `30` | SMTP 超时 |
| `IMAP_LOOKBACK_HOURS` | `72` | IMAP 回溯窗口 |
| `OPERATION_LOG_RETENTION_DAYS` | `30` | 后端诊断日志保留天数，设置为 `0` 可关闭自动清理 |
| `ENABLE_BACKGROUND_WORKERS` | `true` | 测试时可关闭 |

## 3. IMAP 同步口径
后台会启动两个 IMAP worker：`imap-incremental-poller` 每轮优先同步 `INBOX` 和已缓存的 Sent 新 UID，`imap-history-poller` 按文件夹 UID 游标向历史方向分页抓取 header，并在本地匹配系统已有导师邮箱；只有命中导师邮箱的邮件才会继续抓正文并进入邮件记录。用户侧不区分邮件来源，系统发送和邮箱里已有的通信都会进入同一套邮件记录、状态和统计口径。

`IMAP_HISTORY_COMMAND_BUDGET_PER_MINUTE` 是单轮历史补扫最多可消耗的 SEARCH/FETCH 预算，`IMAP_HISTORY_COMMAND_RATE_PER_MINUTE` 和 `IMAP_HISTORY_COMMAND_BURST` 是实际发命令前的平滑限速。遇到 `Fetch volume limit exceed`、`Too many requests` 或类似服务商限流错误时，当前账号会暂停历史补扫；账号级限流会连增量同步一起退避，避免继续消耗 IMAP 配额。

主历史补扫按 `INBOX` 和已发现的 Sent 文件夹维护文件夹级状态；已完成的文件夹状态不会重复参与候选查询。旧的逐导师历史状态只作为 targeted catch-up 补漏机制保留：主文件夹历史完成后，既有导师作为 baseline completed，后续新增少量导师才会进入 pending targeted 队列。

## 4. 首次配置建议
1. 在个人页完成发件身份配置，把 SMTP 和 IMAP 一起确认好。
2. 在个人页上传一份默认材料，并准备一版默认模板。
3. 配置一套 LLM 模型，并完成真实连通性测试。
4. 点击个人页底部“进入测试写信页”，先给自己发一封测试邮件。
5. 确认主题、正文、附件和 SMTP 效果都正常。
6. 导入第一批导师，创建第一批任务。
7. 在工作区跑通匹配、草稿、人工审核和真实发送。

## 5. 真实发送前的检查清单
- 身份页 SMTP 测试通过。
- 测试写信页能成功把邮件发到当前身份自己的邮箱。
- 如果需要回信检测，IMAP 测试通过。
- LLM 测试通过，且 `response_preview` 正常。
- 工作区草稿内容已经人工审核。

## 6. 验证真实发送
1. 在工作区点击“批准并发送”。
2. 在发送前确认弹窗里核对收件人和附件。
3. 在任务详情里确认：
   - `status = sent`
   - `last_rfc_message_id` 已写入
4. 收件箱里确认邮件已真实发出。

## 7. 验证回信检测
1. 使用刚刚发出的真实邮件进行真实回复。
2. 等待 IMAP poller 下一轮执行，或缩短 `IMAP_POLL_INTERVAL_SECONDS`。
3. 在工作区确认：
   - 最后一条消息方向为 `received`
   - 任务状态变为 `reply_detected`
   - `is_replied = true`

## 8. 常见问题
### 8.1 LLM 测试失败
- 检查 `api_base_url` 是否是 OpenAI 兼容地址。
- 检查 `api_key` 是否有效。
- 检查模型名是否存在。
- 如果是响应慢的模型或中转服务，适当调大 `LLM_REQUEST_TIMEOUT_SECONDS`。
- 如果服务商不支持标准 `/v1/chat/completions`，当前实现不会兼容。

### 8.2 SMTP 测试失败
- 465 端口默认走 SSL。
- 非 465 端口会尝试 `STARTTLS`。
- 某些邮箱需要“授权码”而不是登录密码。

### 8.3 IMAP 没有检测到回复
- 确认该任务已经真实发出，并且 `last_rfc_message_id` 已写入。
- 确认身份已完整配置 IMAP。
- 确认回复邮件头里能带上 `In-Reply-To` 或 `References`。
- 如果邮件服务商延迟同步，适当增大 `IMAP_LOOKBACK_HOURS`。

### 8.4 任务一直停留在 `discovered`
- 这是正常的，表示你还没有在工作区手动执行“生成匹配与草稿”。
- 如果已经手动执行但仍未推进，检查当前任务是否已选择默认材料。
- 再检查后端日志里是否有 LLM 调用错误。

### 8.5 已排程任务没有发出
- 检查批量任务是否被 `paused` 或 `stopped`。
- 检查 `scheduled_at` 是否已经到点。
- 检查该任务的主题、正文和附件是否已经在排程前确认。
