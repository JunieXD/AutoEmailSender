# 真实发信与真实 LLM 设计说明

## 1. 目标
把当前系统从“前后端闭环但服务端仍有占位逻辑”提升到“真实 LLM + 真实 SMTP + IMAP 回信检测”的可运行状态，同时保留单机、单进程、低依赖的产品边界。

## 2. 核心设计决策
### 2.1 发送安全
- 导师工作区里的“批准并发送 / 批准并排程”始终是真实发送。
- 风险通过动作前确认表达，不再要求用户先理解全局模式。
- 个人页底部提供独立的测试写信页，测试邮件固定发给当前身份自己的邮箱。
- 测试写信与正式导师任务流分离保存。

### 2.2 真实 LLM
- 统一走 OpenAI 兼容 `chat/completions`。
- 单次草稿生成同时返回匹配结果和草稿 JSON：
  - `match_score`
  - `match_reason`
  - `fit_points`
  - `risk_points`
  - `keywords`
  - `subject`
  - `body_text`
  - `body_html`
  - `suggested_attachment_ids`
- 后端必须做 JSON 解析和结构校验，拒绝把不合法输出直接落库。

### 2.3 真实发信
- 导师发送统一走真实 SMTP，构造 `text/plain + text/html + attachments` 邮件。
- 测试写信页也走真实 SMTP，但收件人固定为当前身份自己的邮箱。
- 发信成功后写入 `last_rfc_message_id`，供 IMAP 回信关联。

### 2.4 回信检测
- 只处理已经真实发出的导师任务。
- 关联顺序：
  1. `In-Reply-To`
  2. `References`
  3. `from_email + normalized subject`
  4. 同身份下最近一次已发送任务兜底
- 检测到回复后统一标记 `reply_detected`，当前版本不细分“自动回复 / 正常回复 / 退信”。

## 3. 后台运行时
系统不使用 APScheduler / Celery / Redis。当前只有发送与回信检测放在 FastAPI 生命周期里的本地循环中，匹配与草稿生成改为工作区手动触发：

1. 发送 dispatcher
- 读取 `EmailTask.status in (approved, scheduled)`
- 立即发送或按 `scheduled_at` 发送
- 统一按真实 SMTP 发送

2. IMAP poller
- 按身份轮询最近若干小时收件箱
- 关联回信并写入 `email_logs(received)`

## 4. 前端交互原则
- 顶部导航只展示身份与模型上下文，不再展示发送模式。
- 个人页底部提供“进入测试写信页”入口。
- 工作区是唯一的任务审批中心：
  - 查看匹配结果和原因
  - 选择 AI 写信参考材料
  - 手动生成或重新生成匹配与草稿
  - 编辑主题 / 正文 / 附件
  - 批准并发送
  - 批准并排程
  - 取消排程
- 测试写信页负责：
  - 生成测试草稿
  - 编辑主题 / 正文 / 附件
  - 真实发送测试邮件给自己
  - 查看测试发送历史
- 任务页只做批次控制和状态观察，不承担逐封审批。

## 5. 失败处理
- LLM 失败：保留任务，更新 `last_error`，用户可在工作区再次手动触发。
- SMTP 失败：任务进入 `send_failed`，写失败日志。
- IMAP 失败：只记录错误，不让主服务崩溃。
- 任一 worker 异常都不能导致 FastAPI 进程退出。
