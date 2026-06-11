# Auto Email Sender v2

## 1. 项目定位
这是一个单用户、本地运行的导师联系辅助系统。核心目标不是“自动群发”，而是把“整理导师数据 -> 评估匹配 -> 生成草稿 -> 人工审核 -> 真实发送 -> 跟踪回复”这条链路在本地跑通。

当前版本遵循这些边界：
- 单用户、本地 SQLite、无账号系统
- 多身份、多套 LLM 配置，运行时显式选择
- 真实发信使用 SMTP，回复检测使用 IMAP
- 后端保持单进程，不引入 Redis、RabbitMQ、Celery

## 2. 当前核心能力
### 2.1 身份与材料
- 每个身份维护一套统一材料库，不再拆成“简历”和“附件”两套结构
- 每个身份最多只有 1 份“默认材料”，用于匹配分析
- 任务可以快照自己的 AI 写信参考材料和随信材料

### 2.2 模型与发送
- LLM 运行时使用 OpenAI 兼容接口
- SMTP 用于真实发信，IMAP 用于自动检测回复
- 个人页提供独立的测试写信入口，用于先给自己发测试邮件

### 2.3 导师管理
- 导师管理独立成 `/professors` 页面，不再堆在首页
- 支持下载导入模板、导入 `csv/xlsx`、手动新增、编辑、单个删除、批量删除
- “删除”实际是归档隐藏，不做硬删；历史任务和通信保留
- 导入时按邮箱覆盖原记录；如果原记录已归档，导入后会自动恢复

### 2.4 任务流
- 首页继续承担“筛选导师并创建批量任务”的主流程
- 导师管理页负责数据录入、导入、编辑和归档
- 工作区负责手动触发匹配与草稿生成、审核、发送和排程
- 批量匹配分析作为后台任务在任务中心观察，单个导师匹配仍可在首页或工作区即时触发
- 草稿生成和改写使用 AI 写信参考材料，不读取匹配分析结果

## 3. 发送安全策略
- 工作区里的导师发送始终是真实发送
- 风险通过动作前确认表达，而不是通过全局模式表达
- 个人页底部提供“测试写信页”，测试邮件固定发给当前身份自己的邮箱
- 测试写信历史与导师通信历史隔离保存
- 回复检测只处理真实导师邮件

## 4. 导师管理工作流
1. 用户进入“导师管理”页
2. 下载 `xlsx/csv` 模板并填写导师信息，或手动新增导师
3. 导入时系统按邮箱做覆盖更新，不会创建重复导师
4. 如果导师被移入回收站，导入同邮箱数据会自动恢复该导师
5. 首页只显示未归档导师，创建任务时也只允许选择未归档导师

## 5. 任务状态机
建议状态保持为：

`discovered -> matched -> review_required -> approved -> scheduled -> sent -> reply_detected`

当前关键含义：
- `discovered`：任务刚创建，尚未手动执行匹配和草稿生成
- `matched`：已经完成匹配计算，并保存匹配分、解释、关键词等结果；不会因为分数高低被自动跳过
- `review_required`：已经生成匹配结果与草稿，等待人工处理
- `approved`：已批准，等待立即派发
- `scheduled`：已批准并设置了未来发送时间
- `sent`：邮件已经真实发出
- `send_failed`：真实发送尝试失败，需要人工处理后再决定是否继续
- `reply_detected`：已经检测到导师回复
- `canceled`：明确取消态，主要用于批量停止后终止原批量子任务；后续如需继续联系，会新建手动子任务承接，而不是复用原任务

补充约束：
- `reply_detected` 只会出现在 `sent` 之后，不作为 `send_failed` 的后续状态
- `send_failed` 是发送阶段的失败分支，不会继续流转到 `reply_detected`
- 匹配分只用于筛选、排序和解释，不参与执行裁决
- 系统不会再根据匹配分自动把任务转成“跳过”状态
- 手动继续联系和 follow-up 都通过新建手动任务衔接，以保留父任务历史

## 6. 技术栈
- 前端：React + Vite + TailwindCSS
- 后端：FastAPI
- 数据库：SQLite + SQLAlchemy + Alembic
- 模型调用：OpenAI 兼容接口
- 文本提取：MarkItDown 按需提取匹配默认材料或 AI 写信参考材料文本
- 发信与收信：SMTP + IMAP

## 7. 相关文档
- `docs/material_management_design.md`
- `docs/material_management_implementation.md`
- `docs/professor_management_design.md`
- `docs/professor_management_implementation.md`
- `docs/real_delivery_and_llm_design.md`
- `docs/real_delivery_and_llm_implementation.md`
- `docs/operations_runbook.md`
- `docs/database_table_design.md`
