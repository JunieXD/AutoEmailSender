# 桌面端后端 API + Worker 双进程改造 Goal 执行计划（前置计划）

- 状态：原 Goal 已取消；G0～G5 与 G6 开发验证证据保留
- 建立日期：2026-08-09
- 适用范围：`backend/`、`desktop/`、相关合同、测试、打包与发布前认证流程
- 默认发布策略：全部准入门槛通过前，桌面端继续使用现有单进程 `combined` 模式

> 2026-08-10 起，当前工作由
> [`desktop-api-worker-beta-goal-plan.md`](./desktop-api-worker-beta-goal-plan.md) 接续。
> 新 Goal 在本计划已完成的双进程实现上增加通用 Prerelease 发布、用户可选拓扑、本地诊断包、
> Mac/Windows Dogfood 和稳定更新隔离；本文件继续保存原始架构决策与历史验收标准。

## 1. Goal 定义

### 1.1 可直接用于 Goal 模式的目标

```text
严格按照 docs/architecture/desktop-api-worker-process-plan.md，将 Auto Email Sender
桌面端后端改造成由 Electron 监督的 API + Worker 两个同级 Python 进程；保留可回退的
combined 模式，修复所有跨进程正确性问题，完成真实多进程、故障注入、SQLite 并发、
长稳和 Windows/macOS 打包验收，并把逐项证据写入验收报告。只有本计划全部必选验收
标准通过、无未解决的阻断级或高风险缺陷、split 成为桌面默认模式且 combined 回退仍可用
时，Goal 才算完成。不要创建 tag、push 或发布版本。
```

Goal 执行时不应把“代码已经拆开”“单元测试通过”或“开发环境可以启动”视为完成。
完成必须同时满足第 9 节验收标准、第 10 节 Definition of Done 和第 11 节证据要求。

### 1.2 Goal 交付物

1. 可打包的 `api`、`worker`、`combined` 三种互斥角色。
2. Electron 双子进程监督器、状态合同、日志与有界重启策略。
3. 跨进程取消、缓存、恢复、租约和邮件发送安全语义。
4. 真实 OS 进程测试工具、确定性故障注入工具和工作负载测试矩阵。
5. Windows x64 与 macOS Apple Silicon 的打包生命周期和长稳证据。
6. `docs/development/desktop_api_worker_goal_acceptance.md` 验收报告。
7. 更新后的架构、运行、排障与发布前 QA 文档。

### 1.3 明确不在本 Goal 内的事项

- 不引入 Redis、RabbitMQ、Celery 或外部服务依赖；SQLite WAL 仍是桌面端唯一协调存储。
- 不把 API 改造成完全无业务执行能力的“薄 API”。
- 不改变现有 HTTP 路径、正常请求/响应字段、Agent API、CLI 或 Desktop IPC 的业务语义，
  除非是向后兼容地增加进程健康字段。
- 不在本 Goal 中把下列同步动作改为 `202 + job_id + polling`：
  手动草稿生成/重写、手动匹配、立即发送、SMTP/IMAP/LLM 测试、手动 IMAP 同步、
  模型或社区网络读取、文档解析与导入。
- 不创建发布 tag，不上传构建产物，不向真实导师发送测试邮件，不发布新版本。

这些同步动作继续在 API 进程中执行。将它们命令化并迁往 Worker 是另一个需要同时修改
HTTP 和前端合同的独立项目，不能混入本 Goal。

## 2. 当前基线与改造理由

当前桌面端由 Electron 只启动一个 Python 子进程，并设置
`ENABLE_BACKGROUND_WORKERS=true`。FastAPI 生命周期在同一进程中依次执行数据库迁移、
启动恢复和 `RuntimeManager.start()`，然后同时承担 HTTP 请求与所有后台循环。

`RuntimeManager` 当前包含：

| 后台子系统 | 当前并发形态 | 目标归属 |
| --- | --- | --- |
| 定时邮件 dispatcher | 1 个循环 | Worker |
| IMAP 增量同步 | 1 个循环 | Worker |
| IMAP 历史同步 | 1 个循环 | Worker |
| 批量草稿 scheduler | 1 个 scheduler | Worker |
| 匹配分析任务 | 配置数量的循环 | Worker |
| Crawler v2 工作项 | 8 个循环 | Worker |

本次改造已有较好的数据正确性基础：Crawler、批量草稿、匹配分析与 IMAP 已普遍具备
持久化 claim、lease、heartbeat 或 stale-write fencing。不过，现状仍有若干明确的
单进程假设，不能只把 `RuntimeManager` 搬到另一个入口便结束：

- `BackendInstanceLock` 只允许每个数据目录存在一个后端进程，API 与 Worker 会互相排斥。
- FastAPI 启动会执行迁移和全局恢复，两个角色同时进入该路径会发生竞态。
- 批量任务暂停/停止在提交数据库后，仍直接调用当前进程内的
  `runtime_manager.cancel_batch_draft_generation()`。
- Crawler 的 `profile_text_cache` 明确是进程内缓存，API 中的失效调用无法影响 Worker。
- `recover_interrupted_match_analysis_runs()` 会处理所有 `running` 记录；Worker 单独重启时
  不能误伤仍由 API 同步请求拥有的手动匹配。
- 当前邮件发送在 SMTP 成功和数据库 `sent` 提交之间存在不可原子化的窗口，且过期
  `sending` 会被重新排队，可能造成重复发送。
- 后台循环捕获异常后继续存活，因此“Worker PID 还在”不等于各子系统健康。
- 当前 Electron 对 stderr 的字符串累积没有硬上限，stdout 也必须持续排空；拆成两个
  长驻子进程后需要正式的有界日志策略。
- SQLite 已开启 WAL 和 busy timeout，但现有测试主要是同进程 coroutine/session 竞争，
  不能代表两个真实 OS 进程的锁竞争行为。

相关当前实现入口：

- [`backend/main.py`](../../backend/main.py)
- [`backend/desktop_entry.py`](../../backend/desktop_entry.py)
- [`backend/app/services/runtime_manager.py`](../../backend/app/services/runtime_manager.py)
- [`backend/app/core/instance_lock.py`](../../backend/app/core/instance_lock.py)
- [`backend/app/core/migrations.py`](../../backend/app/core/migrations.py)
- [`backend/app/modules/workspace/tasks/delivery.py`](../../backend/app/modules/workspace/tasks/delivery.py)
- [`desktop/src/main/backend/service.ts`](../../desktop/src/main/backend/service.ts)
- [`desktop/src/main/bootstrap/application.ts`](../../desktop/src/main/bootstrap/application.ts)

## 3. 不可违反的架构决策

### AD-1：Electron 监督两个同级进程

目标拓扑如下：

```text
Electron main process（唯一监督者）
├── API process
│   ├── FastAPI / Uvicorn
│   ├── HTTP、UI token、Agent token
│   ├── 数据库迁移与一次性全局恢复
│   └── 保留现有同步请求动作
└── Worker process
    ├── RuntimeManager
    ├── dispatcher / IMAP / batch drafts / matching / crawler
    ├── worker heartbeat 与各子系统状态
    └── 不监听 TCP 端口，不持有 UI/Agent access token

API process ─────┐
                 ├── 同一数据目录 / SQLite WAL / 材料文件
Worker process ──┘
```

Worker 不作为 API 的子进程，API 也不负责 fork Worker。Electron 持有两个 controller，
因此能分别判断、停止和重启它们。

### AD-2：复用同一个冻结后端可执行文件

打包后继续只分发一份 Python/PyInstaller 后端可执行文件，通过显式参数启动：

```text
--role api
--role worker
--role combined
```

- `api`：只启动 FastAPI，不创建 `RuntimeManager`。
- `worker`：不导入或启动 Uvicorn，只运行 Worker 组合根。
- `combined`：保留当前单进程行为，供开发、诊断和一个发布周期内的紧急回退使用。

角色必须显式且互斥。打包桌面端不得再用含义模糊的
`ENABLE_BACKGROUND_WORKERS=true` 决定进程拓扑。

### AD-3：API 是 runtime group leader 和唯一 migrator

API 必须先启动并独占完成：

1. 数据库兼容性检查与备份；
2. Alembic upgrade；
3. 日志清理；
4. 仅适合整组冷启动的一次性全局恢复；
5. API readiness 发布。

Electron 只有在 API 报告数据库和恢复均完成后才允许启动 Worker。Worker 启动时只检查
当前 schema 已是 head；不创建备份、不执行迁移、不执行全局恢复。迁移入口仍必须有
跨进程锁作为最后一道防线，不能只信任正常启动顺序。

### AD-4：SQLite 是进程间业务协调的事实来源

所有影响任务正确性的 API → Worker 指令必须先持久化，Worker 根据数据库状态采取动作。
本地内存事件只能作为 Worker 内部的加速信号，不能成为正确性条件。

进程心跳和诊断状态是非业务数据，可放在数据目录下原子替换的 runtime status 文件中，
避免用高频 SQLite 写入制造额外锁竞争。状态文件不得包含 token、密码、邮件正文或模型密钥。

### AD-5：先完成后台边界，不同时重写同步 API

本 Goal 迁移 `RuntimeManager` 拥有的持久化后台工作。同步 HTTP 动作保留在 API，且全局
恢复必须能区分“整组冷启动”和“仅 Worker 重启”，避免 Worker 恢复路径处理 API 正在执行
的工作。

### AD-6：邮件采用 at-most-once 偏向

SMTP 和 SQLite 无法组成原子事务，也不能假设邮件服务提供可靠幂等键。因此本项目明确
选择“宁可在极端崩溃中少发一封，也不能重复发送”的 at-most-once 偏向，不宣称
exactly-once。

具体规则见第 7 节。这是硬性产品决策，执行 Goal 时不得改回“过期后自动重发”。

## 4. Runtime group 合同

### 4.1 身份与锁

每次整组启动生成唯一 `runtime_id`；每次 Worker 重启再生成唯一 `worker_generation`。
所有 Crawler worker id 及可持久化的进程所有权标识必须包含这两个值，不能继续只使用
跨重启重复的 `crawler-worker-1` 一类名称。

锁的目标语义：

- API 持有现有数据目录级 group leader 锁，阻止第二个桌面实例或第二个 API。
- Worker 持有独立的 Worker role 锁，阻止同一数据目录出现两个有效 Worker。
- `combined` 按固定顺序同时取得 group leader 锁和 Worker role 锁。
- Worker 必须验证匹配的 `runtime_id`、Electron parent PID 和 API leader 身份；任一不再
  匹配时自行退出。
- 所有锁冲突都必须快速失败并给出可诊断信息，不能等待到 SQLite 锁超时。

### 4.2 启动顺序

```text
Electron 生成 runtime_id、端口与 API tokens
  → 启动 API
  → API 取得 group lock
  → API 执行迁移和 cold-start recovery
  → API /ready
  → Electron 启动 Worker（不传 UI/Agent token）
  → Worker 取得 worker lock、校验 API/schema/runtime_id
  → Worker 启动 RuntimeManager 与状态 heartbeat
  → Worker ready
  → Electron 发布整体 ready；Agent runtime descriptor 增加 Worker 信息
```

API ready 与 Worker ready 必须分开表达。API 已可用但 Worker 尚未启动或暂时故障时，
前端连接仍然有效，桌面状态为 `degraded/background_unavailable`，不能把整个应用伪装成
HTTP 后端不可用。

### 4.3 状态和心跳

Worker 必须原子更新一个只含诊断信息的状态文件，至少包含：

- 协议版本、`runtime_id`、`worker_generation`、PID、启动时间；
- `starting | ready | stopping | error`；
- 最近进程 heartbeat 时间；
- dispatcher、IMAP incremental、IMAP history、batch drafts、每个 matching loop、
  每个 crawler loop 的最近开始、最近成功、最近失败、连续失败数和脱敏错误摘要；
- 当前是否正在 drain。

生产默认 heartbeat 间隔不得大于 5 秒。连续 15 秒无 heartbeat 视为 Worker hang；测试可
注入更短时间。单个子系统异常必须可见，不能因为总进程 heartbeat 正常就报告全部健康。
外部网络暂时不可用可以进入 degraded，不应无条件触发紧密重启循环；事件循环失去心跳、
组合根致命错误或状态文件协议损坏才触发进程级恢复。

API 的 runtime 信息和 Electron IPC 状态应向后兼容地增加 Worker 状态。现有 descriptor
中的 `backend` 继续代表 API，新增可选 `worker` 字段，避免旧 CLI 因字段重命名失效。

### 4.4 监督与重启

- Worker 意外退出：API 和前端连接保持可用；Electron 将状态切为 degraded，并按退避
  策略只重启 Worker。
- API 意外退出：先确认旧 Worker 及其 Playwright 后代全部终止，再清理 descriptor，
  生成新的 runtime group 并按完整顺序重启 API + Worker。
- Electron 退出或崩溃：API 与 Worker 都通过 parent liveness 自行退出；Electron 正常退出
  时仍显式按 Worker → API 顺序停止。
- 新 Worker 启动前必须确认旧 Worker 已死亡且 role lock 已释放；不得以新 generation
  覆盖一个仍能写数据库的旧 Worker。

退避策略必须有常量、抖动、上限和 circuit breaker，并由虚拟时钟测试。建议基线为
1、2、4、8、16 秒后封顶 30 秒并加入抖动；5 分钟内连续 5 次崩溃时进入 60 秒冷却，
冷却期间 API 继续服务并明确报告后台不可用。稳定运行一段时间后重置崩溃计数。

### 4.5 正常关闭

Worker 收到关闭请求后按以下顺序 drain：

1. 停止取得新 claim；
2. 通知各循环停止；
3. 给已有数据库提交和可安全结束的外部调用一个有界宽限期；
4. 释放 Worker role lock 并写入 stopping/stopped 诊断；
5. 超时后由 Electron 终止完整进程树。

正常空闲退出时两个 Python PID 及 Playwright 后代应在 10 秒内消失；任何情况下 15 秒后
不得遗留本 runtime group 的进程。API 只有在 Worker 已停止后才关闭数据库服务。

### 4.6 日志

两个子进程的 stdout 和 stderr 都必须从 spawn 时立即排空。每个进程、每个 stream 的
内存尾部缓存必须有明确硬上限且不超过 1 MiB；需要落盘时使用有总容量上限的滚动日志。
错误界面只展示脱敏尾部，禁止包含 token、SMTP/IMAP 密码、模型密钥和邮件正文。

## 5. 启动恢复所有权

恢复分成两类，不能混用：

### 5.1 整组冷启动恢复（仅 API）

只有新 runtime group 的 API、且只在 Worker 已确认不存在时执行：

- `recover_interrupted_crawl_jobs()`；
- `recover_interrupted_match_analysis_runs()`；
- `recover_interrupted_workspace_draft_rewrites()`；
- 冷启动所需的 stale batch draft 恢复；
- 旧日志清理和其他一次性维护。

这些步骤完成前 API 不进入 ready，Worker 也不得启动。

### 5.2 Worker 局部重启恢复

Worker 局部重启只能依赖各后台工作负载自己的 claim、lease、heartbeat、取消标志和
claim-id fencing。它不能调用会扫描所有 `running` 记录的全局恢复函数。

必须有一个真实双进程测试：API 正在执行同步手动匹配时杀死并重启 Worker，手动匹配的
`MatchAnalysisRun` 不得被标记 interrupted；与此同时，Worker 拥有且租约过期的匹配工作项
必须正确恢复。

## 6. 跨进程正确性改造

### 6.1 批量草稿取消

API 暂停或停止批量任务时，先在同一事务中提交持久化状态。Worker 必须在 claim 前、外部
LLM 调用的安全检查点和结果提交前重新验证数据库状态。

验收语义：

- 暂停/停止提交后不得取得新草稿 claim；
- 已在途的 LLM 请求允许结束，但过期 claim 或已取消任务的结果不得写回；
- API 不再通过 `request.app.state.runtime_manager` 影响正确性；
- Worker 内部 coordinator 可保留，但只能作为降低取消延迟的优化。

### 6.2 进程内 profile text cache

缓存只允许由 Worker 使用。API 中的取消、重试、删除不能依赖直接 `discard_job()` 使
Worker 正确。

缓存 key 必须包含持久化的 job run/revision 或等价输入指纹，使一次重试、重新抓取或输入
变更自然得到新 key；任何数据库状态已使工作项失效时，即使旧缓存仍存在也不能提交结果。
Worker 重启后缓存为空应只影响性能，不改变结果。

### 6.3 claim 与 stale-write fencing

每个后台工作负载都必须满足：

1. 使用条件更新取得唯一 claim；
2. claim 带不可跨进程重用的 id/generation；
3. 长任务更新 lease/heartbeat；
4. 最终写入再次校验 claim id、有效 lease 与业务取消状态；
5. 旧 Worker 在网络调用返回后不能覆盖新 Worker 或用户动作的结果；
6. lease 恢复是幂等的，多次执行结果一致。

现有保护可以复用，但必须通过真实进程 kill 测试证明，而不是只通过 mock/session 并发测试。

### 6.4 数据库锁竞争

- API 和 Worker 都继续使用 WAL 与 busy timeout；启动时验证实际 journal mode。
- 只对已证明幂等或有 CAS 保护的事务做有界 `database is locked` 重试，不能盲目重放
  SMTP 或其他外部副作用。
- 长时间网络调用期间不得无必要地持有 SQLite 写锁。
- API 高频读写、Worker 所有循环与状态查询并发时，不得出现未处理的 lock error、连接泄漏
  或无界等待。

## 7. 邮件发送硬性安全策略

### 7.1 产品取舍

不要求用户确认发送结果，不依赖 Sent 文件夹、IMAP 搜索或 SMTP 服务端回查。部分学校
邮箱不会把 SMTP 发送的邮件写入 Sent，因此这些证据不能成为恢复条件。

该规则同时适用于 Worker dispatcher 拥有的排程发送和 API 同步请求拥有的立即发送，不能
只保护其中一条入口。发送流程采用以下边界：

```text
可安全重试区域
  渲染主题/正文、读取附件、校验身份与窗口、构造 MIME
  → CAS 并提交 delivery claim / sending

禁止重发区域
  立即调用 SMTP
  → SMTP 返回成功
  → 只执行一次最小 SQLite 最终提交
  → sent
```

所有可以前置的模板渲染、附件读取、MIME 构造和业务校验必须在进入 `sending` 前完成。
进入 `sending` 后不得再做无关网络、文件解析或其他长耗时准备。

### 7.2 中断恢复语义

一旦任务已经提交 `sending` claim，任何无法证明 SMTP 尚未开始的中断都按“已发送”处理：

- claim 持久化唯一 attempt id、owner role、`runtime_id` 和 process/worker generation；
- stale `sending` 绝不恢复为 `approved` 或 `scheduled`；
- 恢复时将用户可见状态推进到 `sent`，并持久化
  `assumed_sent_after_interruption` 或等价内部审计标志；
- 不伪造 RFC Message-ID；保留实际已知的 attempt id、时间和错误上下文；
- 不要求用户确认，不查询 Sent/IMAP，不自动重发，也不为同一任务提供会绕过该保护的
  恢复性重发路径。

恢复不能只根据墙钟经过 30 分钟便处理仍然存活的 owner。它必须确认 claim 所属进程或
generation 已失效，或者该 attempt 已被明确放弃。SMTP 返回后的最终提交必须再次以
attempt id 和 `sending` 状态做 CAS；若恢复已经推进为 assume-sent，旧 owner 随后返回也
不得覆盖 outcome 或重复写 EmailLog。

这意味着 Worker 可能恰好在提交 `sending` 后、真正调用 SMTP 前崩溃，此时邮件可能少发，
但系统仍按已发送记录。该代价是明确接受的；它比给导师重复发信更安全。

SMTP 抛错也要区分阶段：只有能够证明失败发生在发送尝试开始前的错误才可进入普通
`send_failed`；已经开始传输 DATA、服务端响应丢失或阶段不可判定时，一律按上述
assume-sent 规则处理。

### 7.3 缩短成功提交窗口

SMTP 返回成功之后的代码路径必须满足：

- 不再等待任何外部服务或文件 I/O；
- 只构造/写入发送结果、邮件日志和操作日志；
- 使用单个短事务立即 commit；
- 结构化测试证明该区间没有新增无关 `await`；
- 在发布 QA 参考机器、无故障注入的本地 fake SMTP 基准中，500 次发送的
  “SMTP success → SQLite commit 完成”延迟 p99 不高于 250 ms。

该性能指标用于压缩概率窗口，不代表能跨 SMTP 与 SQLite 提供原子性。

## 8. 分阶段实施计划

每个阶段只在自己的验收证据写入报告后进入下一阶段。若发现必须扩大 HTTP/前端业务合同，
立即按第 12 节停止，不得把完全薄 API 混入本 Goal。

| 阶段 | 范围 | 初始状态 | 阶段完成条件 |
| --- | --- | --- | --- |
| G0 | 冻结基线、建立真实多进程与故障注入测试工具 | 已完成（2026-08-09） | 基线全绿；测试能确定性停在 claim/外部调用/commit 边界 |
| G1 | 角色入口、role locks、迁移所有权、combined 模式 | 已完成（2026-08-09） | 两角色真实启动；只有 API 迁移；重复角色被拒绝 |
| G2 | RuntimeManager 迁移、恢复边界、取消与 cache 修复 | 已完成（2026-08-09） | 全部后台循环只在 Worker；Worker 重启不影响 API 同步任务 |
| G3 | 邮件 at-most-once 与最小提交窗口 | 已完成（2026-08-09） | 第 7 节和 AC-MAIL 全部通过 |
| G4 | Electron runtime-group supervisor、状态、日志与降级 UI | 已完成（2026-08-09） | Worker/API/Electron 故障矩阵通过且无孤儿进程 |
| G5 | SQLite 竞争、工作负载级 chaos、升级与资源测试 | 已完成（2026-08-09；AC-DATA-01 时长由 G6 关闭） | AC-WORK、AC-COMPAT 与 AC-DATA-02～05 通过 |
| G6 | Windows/macOS 冻结包、安装包生命周期与长稳认证 | 执行中（2026-08-10） | 两平台 AC-PKG、AC-SOAK 全部通过 |
| G7 | 默认切换为 split、回退演练、最终验收报告 | 待执行 | Definition of Done 全部满足；未执行实际发布 |

### G0：基线和测试基础设施

- 在最终实现前记录完整仓库测试、当前 combined 启动、打包 self-check 和典型任务状态基线。
- 建立使用临时数据目录、随机端口和本地 fake SMTP/IMAP/LLM/HTTP 服务的进程 harness。
- 故障注入点必须能通过测试专用环境开关或本地控制通道，确定性阻塞在：
  claim 前、claim commit 后、外部调用前、外部调用返回后、最终 commit 前后。
- 故障开关在生产默认关闭，且打包测试证明普通运行无法意外启用。

### G1：进程角色和启动所有权

- 增加三种 role 组合根与明确 CLI 参数。
- 拆分 group leader lock、Worker role lock 和 migration lock。
- API 保留迁移/全局恢复；Worker 只做 schema head 防御性检查。
- combined 模式使用与 split 相同的 Worker 组合根，避免维护两份后台实现。
- 新增 migration exactly-once 与不同启动交错顺序的真实进程测试。

### G2：后台迁移与业务边界

- 将 `RuntimeManager` 从 API lifespan 移入 Worker 组合根。
- 修复 API 对进程内取消器和 profile cache 的依赖。
- 拆分 cold-start recovery 与 Worker lease recovery。
- 将 Worker incarnation 纳入持久化 worker id/诊断。
- 对六类后台子系统逐一完成 claim、取消、恢复和 stale-write 测试。

### G3：邮件安全

- 前移所有发送准备工作。
- 增加唯一 delivery attempt/claim 和内部 delivery outcome 审计能力；如需 schema 变化，提供
  单独 Alembic revision、旧数据库迁移测试和备份恢复测试。
- 删除 stale `sending` 自动回到 dispatchable 状态的行为。
- 建立可记录 SMTP DATA 接受次数的 fake server，并对排程发送杀死 Worker、对立即发送
  杀死 API，覆盖两条入口的进程 kill 矩阵。
- 加入成功返回到 commit 的结构门禁和延迟基准。

### G4：Electron 监督器

- 将单 `BackendController` 扩展为 runtime group controller。
- 实现 API-first 启动、Worker-only restart、whole-group restart、退避与 circuit breaker。
- 排空并限制四个 child streams；状态和错误经现有 IPC 向后兼容地发布。
- Agent runtime descriptor 原子增加 Worker 信息，清理逻辑继续使用 runtime id 防误删。
- 实现 Worker unavailable 时 API 可用、前端明确降级的体验。

### G5：并发、故障与兼容

- 用真实 API/Worker 进程运行每种工作负载的边界 kill 测试。
- 执行 SQLite 高竞争、锁超时、只读、磁盘满、损坏数据库和 WAL 生命周期测试。
- 执行系统 sleep/wake、时间前跳/后跳、网络断开/恢复和重复崩溃测试。
- 使用上一稳定版数据库快照覆盖空闲、排队、running、leased、sending 等状态升级。
- 监控 PID、文件句柄、连接、内存、状态文件和日志总量，证明没有单调增长。

### G6：打包和长稳

- 冻结后端 self-check 覆盖三个 role 以及 Worker 所需全部动态导入。
- Windows x64 与 macOS Apple Silicon 安装包均执行首次启动、升级、退出、强杀、重启、
  多实例、Playwright 后代清理和卸载/保留数据测试。
- 为 macOS 补齐与 Windows release QA 对等的 packaged lifecycle 自动化，不以源码开发模式
  代替真实 app bundle 验收。
- 按 AC-SOAK 运行固定种子和随机种子的长稳测试。
- 正式报告必须同时绑定 clean commit SHA、当前版本、当前安装包 SHA-256、安装后 artifact
  tree SHA-256 和从最新可达 `v*` tag 推导的上一稳定版版本；场景结束后重新计算当前安装包
  与 artifact tree，任何变化均失败。
- 上一稳定版升级必须由真实 DMG/NSIS 启动旧应用写入设置、导师和中文/空格/Ω 材料；当前
  数据库 revision 必须精确等于仓库唯一 Alembic head。schema 变化时必须出现一份升级前
  清单之外的新备份，且备份 revision 必须等于旧库 revision。
- 正常长稳和 seeded chaos 每轮都实际驱动 Dispatcher、IMAP incremental、IMAP history、
  Batch Draft、Matching、Crawler 六类 Worker 工作；不能用 API 空转代替。
- 最小时长同时以单调时钟和墙钟实测；资源门禁覆盖 RSS、句柄/FD、连接、SQLite 文件、
  child/Playwright 后代、runtime 文件数及 status/log 大小和趋势。

### G7：切换和收尾

- 只有 G0–G6 全部通过后才把桌面默认角色从 combined 改为 split。
- 在最终 SHA 上重新运行全部自动测试、两平台打包测试和回退演练。
- combined 至少保留一个正式发布周期；回退只改变拓扑，不允许降级数据库或丢弃任务。
- 完成验收报告、运维排障文档和剩余风险声明。

## 9. 必选验收标准

下列每一项都需要自动化测试或可复现的发布 QA 证据。没有证据等同未通过。

### 9.1 架构与启动（AC-ARCH）

- **AC-ARCH-01**：split 模式中恰有一个 API PID 和一个 Worker PID；Worker 不监听端口。
- **AC-ARCH-02**：API 进程内不存在 `RuntimeManager` 后台 task，Worker 进程不启动 FastAPI。
- **AC-ARCH-03**：同一数据目录的第二个 API、第二个 Worker 和第二个 combined 均快速失败，
  且不影响第一个 runtime group。
- **AC-ARCH-04**：API 未 ready、schema 非 head 或 runtime id 不匹配时 Worker 拒绝启动。
- **AC-ARCH-05**：至少 20 种并发/交错启动顺序中，迁移函数只执行一次，备份只生成一次，
  数据库最终为 head 且 `PRAGMA integrity_check` 通过。
- **AC-ARCH-06**：combined 和 split 复用同一 Worker 组合根；不存在复制的后台循环列表。
- **AC-ARCH-07**：正常 HTTP、Agent API、CLI 和 IPC 合同没有非预期差异。

### 9.2 生命周期与健康（AC-LIFE）

- **AC-LIFE-01**：杀死 Worker 后 API 连续健康，普通读取和一个同步写请求均成功，前端收到
  degraded 状态，随后只产生一个新 Worker。
- **AC-LIFE-02**：杀死 API 后，旧 Worker 和所有后代先退出，再创建新 runtime id 和新组；
  不存在旧 generation 写入新组数据的情况。
- **AC-LIFE-03**：强杀 Electron 后 15 秒内 API、Worker、Playwright 后代全部退出。
- **AC-LIFE-04**：Worker 进程存活但 heartbeat 停止时，15 秒阈值后被判定 hung 并重启。
- **AC-LIFE-05**：单个循环连续抛错时，其状态为 degraded 并保留脱敏错误；其他循环和 API
  继续工作，且不会形成无退避 restart storm。
- **AC-LIFE-06**：在虚拟时间下完整验证退避、抖动、上限、circuit breaker 和稳定后重置。
- **AC-LIFE-07**：每个子进程输出 100 MiB stdout 和 100 MiB stderr 都不会阻塞；Electron
  内存不随输出量线性增长，每个 stream 尾部缓存不超过规定上限。

### 9.3 工作负载正确性（AC-WORK）

以下每类都必须测试“claim 前、claim 后、外部调用中、外部调用返回后、commit 前、commit 后”
杀死 Worker，以及取消/暂停与 lease 过期交错：

| 工作负载 | 必须证明的结果 |
| --- | --- |
| Dispatcher | 除第 7 节的 assume-sent 取舍外，同一任务不会被两个 Worker 并发发送 |
| IMAP incremental | 同一身份只有有效 lease owner 写游标；旧 owner 结果被 fencing |
| IMAP history | 历史游标不倒退、不重复落相同邮件、不被旧 generation 覆盖 |
| Batch drafts | 暂停/停止后无新 claim；在途旧结果不能覆盖用户动作 |
| Match jobs | 过期 item 正确恢复；Worker 重启不终止 API 手动匹配 |
| Crawler v2 | page/chunk/enrichment 的旧 worker 写入全部被拒绝，任务可继续收敛 |

- **AC-WORK-01**：上述矩阵全部通过，且每个断点至少重复 20 次。
- **AC-WORK-02**：连续杀死并重启 Worker 200 次后，无双 owner、永久 running、负计数或
  不可解释的终态。
- **AC-WORK-03**：API 提交取消/暂停后，在配置的取消轮询上限内 Worker 停止取得新工作；
  不依赖 API 进程内对象。
- **AC-WORK-04**：Worker cache 冷启动、命中、API 侧取消、同 job retry 和输入变更得到一致
  业务结果；旧 cache 不能使失效工作提交。

### 9.4 邮件（AC-MAIL）

- **AC-MAIL-01**：进入 `sending` 前崩溃且尚未 claim，任务可由新 Worker 正常发送一次。
- **AC-MAIL-02**：`sending` claim 提交后、SMTP 调用前杀死拥有该发送的 API 或 Worker，
  恢复为内部标记的 assume-sent，永不自动重发；允许测试确认该邮件实际未发送。
- **AC-MAIL-03**：fake SMTP 已接受 DATA、但拥有该发送的 API 或 Worker 在数据库 commit
  前被杀死时，服务端接受计数始终为 1，恢复为 assume-sent，重启后不再次发送。
- **AC-MAIL-04**：SMTP 成功并 commit 后任意重启，任务、EmailLog 和操作日志保持一致，
  不重复写入。
- **AC-MAIL-05**：SMTP 响应丢失和发送阶段不可判定时保守 assume-sent；只有确定的
  preflight 失败可进入普通失败路径。
- **AC-MAIL-06**：最终 commit 遇到 database locked、磁盘满或进程强杀时，恢复路径仍不
  使任务重新 dispatchable。
- **AC-MAIL-07**：不调用 Sent/IMAP 证据接口，不出现用户确认流程。
- **AC-MAIL-08**：成功到 commit 的路径满足第 7.3 节结构门禁和 p99 延迟指标。
- **AC-MAIL-09**：存活 owner 的长 SMTP 调用不会仅因墙钟超时被恢复；失效 owner 被
  assume-sent 后，即使旧调用迟到返回也无法覆盖 outcome 或重复写日志。

### 9.5 SQLite 与故障（AC-DATA）

- **AC-DATA-01**：API 持续读写与所有 Worker 循环并发至少 8 小时，无未处理
  `database is locked`、事务泄漏或数据库损坏。
- **AC-DATA-02**：数据库目录只读、磁盘满、WAL/SHM 创建失败时，应用进入明确错误或降级
  状态，不启动危险的部分 Worker，不循环破坏原文件。
- **AC-DATA-03**：损坏数据库不会被自动覆盖；启动失败保留原文件和诊断，Worker 不启动。
- **AC-DATA-04**：每轮 chaos 前后 `PRAGMA integrity_check` 通过，业务关键表的外键、唯一性
  和状态机不变量通过自动审计。
- **AC-DATA-05**：系统时间向前/向后跳、sleep/wake 后，lease 不产生永久占用或两个有效
  owner；排程邮件遵守现有过期和发送窗口语义。

### 9.6 兼容、安全与打包（AC-COMPAT / AC-PKG）

- **AC-COMPAT-01**：上一稳定版数据库的空闲、queued、running、leased、sending 快照均能
  升级；迁移前备份可用于恢复，用户材料不变。
- **AC-COMPAT-02**：combined 回退可读取 split 使用后的同版本数据库，并继续处理非歧义任务。
- **AC-COMPAT-03**：runtime descriptor 对旧 CLI 保持兼容，新 CLI 能显示 API/Worker 状态。
- **AC-COMPAT-04**：Worker 环境、status 文件和日志不包含 UI/Agent token 或其他秘密。
- **AC-PKG-01**：冻结 self-check 分别执行 `api`、`worker`、`combined`，所有动态依赖存在。
- **AC-PKG-02**：Windows x64 安装包完成首次启动、升级、快速退出、强杀、重复启动、系统
  sleep/wake 和卸载数据保留测试。
- **AC-PKG-03**：macOS Apple Silicon app/DMG 完成与 AC-PKG-02 对等的真实 bundle 测试。
- **AC-PKG-04**：两平台均验证 Electron、API、Worker、Playwright 进程树清理和锁释放。
- **AC-PKG-05**：路径含中文、空格和非 ASCII 用户名时两个角色均能启动和读写数据。

### 9.7 长稳与资源（AC-SOAK）

- **AC-SOAK-01**：Windows 与 macOS 的最终冻结包各运行一次不少于 24 小时的正常长稳；
  持续执行 API 请求和六类后台任务。
- **AC-SOAK-02**：两平台各运行一次不少于 8 小时的 seeded chaos；随机执行 Worker kill、
  API kill、网络 flap、SQLite lock、sleep/wake 和时间跳变，seed 与轨迹完整留档。
- **AC-SOAK-03**：长稳期间无重复邮件、无任务状态损坏、无孤儿进程、无未解释异常退出。
- **AC-SOAK-04**：RSS、文件句柄、SQLite 连接、Playwright 子进程、status/log 文件大小没有
  统计显著的单调增长；资源阈值和采样图写入验收报告。
- **AC-SOAK-05**：任何失败都必须定位和修复；只重跑到一次通过不能关闭缺陷。修复后使用
  原失败 seed 重放，并重新完成受影响的整段长稳。

## 10. Definition of Done

只有同时满足以下条件才能把 Goal 标为完成：

1. G0–G7 全部完成，所有 AC 条目都有证据且通过。
2. 最终 SHA 上完整仓库测试连续通过 3 次；split 多进程集成套件连续通过 20 次。
3. Windows x64 与 macOS Apple Silicon 最终冻结包均通过真实生命周期和长稳测试；不得用
   mock、源码模式或另一平台替代。
4. 没有未解决的阻断级、高风险或数据正确性缺陷；没有被静默 skip 的必选测试。
5. 邮件故障矩阵证明每个任务跨任意 Worker 重启最多只有一次 SMTP DATA 接受。
6. API 可用性、Worker 降级、恢复、日志、诊断和回退路径均有面向用户/运维的说明。
7. split 已成为桌面端默认，combined 回退演练通过，且没有执行 tag、push 或实际发布。
8. `docs/development/desktop_api_worker_goal_acceptance.md` 记录最终 commit、OS/架构、命令、
   测试数、持续时间、seed、产物哈希、资源曲线、已知限制和逐项 AC 映射。

必须在验收报告中明确说明：即使全部通过，也不能数学上保证“任何情况下绝不故障”；本计划
提供的是可重复证据、故障隔离和保守恢复语义，而不是无法兑现的绝对保证。

## 11. 验证命令与证据要求

### 11.1 日常与完整门禁

完整仓库测试使用项目统一入口：

```text
rtk proxy uv run --project backend --no-sync python scripts/quality/run_all_tests.py
```

桌面端改动至少执行：

```text
cd desktop
rtk npm run typecheck
rtk npm run test
rtk npm run build
```

涉及打包和发布链时，执行仓库指南列出的 Frontend release notes、Desktop packaging、
POSIX/PowerShell release script 合同测试。Windows 真机/VM 继续使用：

- [`docs/operations/windows-parallels-release-qa.md`](../operations/windows-parallels-release-qa.md)
- [`scripts/quality/run-windows-vm-release-qa.sh`](../../scripts/quality/run-windows-vm-release-qa.sh)
- [`scripts/quality/run-windows-release-qa.ps1`](../../scripts/quality/run-windows-release-qa.ps1)

macOS packaged lifecycle、normal soak 与 seeded chaos 使用
[`scripts/quality/run-macos-packaged-qa.sh`](../../scripts/quality/run-macos-packaged-qa.sh)；精确正式
命令、上一稳定版参数和时长见
[`desktop_api_worker_goal_acceptance.md`](../development/desktop_api_worker_goal_acceptance.md#最终冻结-sha-的正式命令)
及 [`sparkle-release-operations.md`](../operations/sparkle-release-operations.md#api--worker-冻结包认证)。

### 11.2 证据格式

每次阶段验收至少记录：

- AC 编号；
- 精确命令和测试名称；
- commit SHA、是否 clean worktree；
- OS、CPU 架构、Node/Python/uv 版本；
- 开始时间、持续时间、结果；
- fake service 配置、fault point、seed；
- 失败时的日志和最小复现；
- 打包产物哈希；
- 若为人工步骤，操作人和可验证截图/日志。

测试重跑只能用于定位。第一次失败的证据不得删除或被最后一次成功覆盖。

## 12. 停止条件

出现以下任一情况，停止当前阶段并先修正设计或缩小批次：

- 发现 API 与 Worker 会同时迁移或同时执行全局恢复。
- 无法证明旧 Worker 已退出便启动新 Worker。
- 任何 fault test 观察到同一邮件被 SMTP 接受两次。
- 为让测试通过而需要恢复 stale `sending` 自动重发。
- API 动作仍依赖直接操作 Worker 进程内对象或 cache。
- Worker 单独重启会终止/覆盖 API 同步请求的结果。
- SQLite 故障可能覆盖原数据库、跳过备份或产生无法解释的状态损坏。
- stdout/stderr、状态文件、日志、内存、连接或子进程出现无界增长。
- 必须同时把同步 HTTP 动作改成异步 job 才能继续。
- 任一支持平台的真实打包验收不可用或被跳过。
- 完整测试失败且不能证明与本阶段无关。

不得通过引入外部队列、扩大到完全薄 API、降低测试门槛或把失败标成 flaky 来绕过停止条件。

## 13. 预计文件范围

最终范围以 G0 调用图和测试基线为准，预计至少涉及：

- Backend 组合根：`backend/desktop_entry.py`、`backend/main.py`、新增 Worker entry/runtime status。
- Backend 平台：instance/migration lock、数据库启动检查、runtime descriptor/诊断。
- 后台领域：RuntimeManager、batch drafts、matching、crawler、IMAP、delivery。
- 数据库：若邮件 outcome/attempt 需要新字段，新增单一 Alembic revision 及迁移测试。
- Desktop：backend service/controller、application bootstrap、types、IPC contract、Agent runtime。
- 测试：Backend 真实进程 harness、Desktop supervisor 测试、两平台 packaged lifecycle、chaos/soak。
- 文档：本计划、验收报告、模块地图、依赖规则、运维和发布前 QA。

每一阶段只提交一个可独立验证的逻辑变化，不顺带整理无关代码或改变产品行为。

## 14. 执行记录

计划建立时只完成了现状审计和文档编写，没有修改运行代码，也没有把 split 设为默认。
开始 Goal 后，在此处记录各阶段的开始/完成日期、实际范围、偏差和验收报告链接。

### G0：基线与真实进程测试基础（已完成）

- 完成日期：2026-08-09
- 建立前基线：Backend 1761、CLI 196，Frontend/Desktop/Website 全部通过。
- 新增默认关闭的同步/异步 fault gate、真实进程 harness、随机端口、readiness、文件日志和
  有界停止能力。
- 新增 4 个专项测试，覆盖默认关闭、真实进程停点/释放、真实后端迁移/readiness 和同数据
  目录实例锁。
- 变更后完整仓库回归：Backend 1765、CLI 196，Frontend/Desktop/Website 全部通过，
  0 failures。
- 详细证据：[`desktop_api_worker_goal_acceptance.md`](../development/desktop_api_worker_goal_acceptance.md)

### G1：角色、锁与迁移所有权（已完成）

- 开始日期：2026-08-09

完成日期：2026-08-09

- 新增显式 `api`、`worker`、`combined` 三角色；默认仍为 combined。
- API 与 Worker 使用独立 role lock，combined 按固定顺序同时持有；迁移另有跨进程锁。
- API 写入带 runtime id/PID/generation 的 ready 状态；Worker 只校验 schema head 和匹配的
  API leader，不执行迁移或全局恢复。
- 真实进程测试覆盖双角色并存、Worker 不监听端口、重复 API/Worker、迁移屏障、runtime id
  错配和 API leader 消失后的 Worker 自终止。
- Backend 完整回归：1776 tests passed，1 skipped；三角色 runtime self-check 均通过。

### G2：后台边界、取消、缓存与恢复（已完成）

- 开始日期：2026-08-09
- 完成日期：2026-08-09
- 批量草稿保留低频 lease 写入，并新增 1 秒只读持久化 claim watcher；API 的进程内
  coordinator 调用只作为 combined 模式延迟优化，移除它也不影响取消正确性。
- Crawler profile text cache key 纳入 `CrawlJob.current_run_id`；API 的取消、重试、删除
  不再直接清理进程内 cache，同 job 新 run 自然隔离旧值。
- Crawler 持久化 owner id 纳入 `runtime_id` 与 `worker_generation`，Worker 重启不再复用
  `crawler-worker-1` 一类 owner。
- 真实双进程测试证明：Worker 重启只恢复过期的后台匹配 item，不会将 API 同步手动匹配
  所有的 `MatchAnalysisRun` 标记为 interrupted。
- G2 定向 117 tests passed；Backend 完整 1780 passed、1 skipped；全仓 Backend、CLI、
  Frontend、Desktop、Website 0 failures。

### G3：邮件 at-most-once 与最小提交窗口（已完成）

- 开始日期：2026-08-09
- 完成日期：2026-08-09
- 新增持久化 delivery attempt、task outcome 与每 attempt 唯一 EmailLog；旧数据库中的
  `sending` 在迁移时保守升级为 `sent + assumed_sent_after_interruption`，不会重新排队。
- 主题/正文渲染、附件读取、凭据校验和 MIME 构造全部前移到 claim 之前；claim 原子持久化
  role、runtime id、generation、PID 和 prepared Message-ID，claim 后立即进入 SMTP。
- SMTP 明确拒绝进入 `pre_submission_failed`；DATA 开始后的连接丢失及无法分类异常进入
  `assumed_sent_after_interruption`。不请求用户确认、不查询 Sent/IMAP/SMTP 证据，也不自动重发。
- 最终事务用 `status=sending + attempt_id` 双重 CAS，同时提交 task、attempt、EmailLog 和操作
  日志；恢复后的旧 owner 迟到返回不能覆盖 outcome 或重复写日志。
- 真实 API/Worker 进程在 claim 前后、SMTP 前后和 commit 前后共覆盖 12 个 kill 场景；本地
  STARTTLS fake SMTP 对每个任务的 DATA 接受次数始终不超过 1。响应丢失场景实际接受 1 次，
  恢复后没有再次发送。
- 500 次本地 fake SMTP 基准的“SMTP success 到 dispatch 返回”保守上界 p99 为 2.171 ms，
  低于 250 ms；结构门禁证明最终事务路径只等待数据库操作和默认关闭的 fault gate。
- G3 完成时 Backend 1800 tests passed、1 skipped；全仓 Backend、CLI 196、Frontend、
  Desktop、Website 全绿，0 failures，3m22s。随后新增的磁盘满 marker 写失败专项测试单独通过；
  最终阶段回归将继续包含该测试。
- 详细证据：[`desktop_api_worker_goal_acceptance.md`](../development/desktop_api_worker_goal_acceptance.md)

### G4：Electron runtime-group supervisor（已完成）

- 开始日期：2026-08-09
- 完成日期：2026-08-09
- Electron split supervisor 按 API ready → Worker ready 启动；Worker 故障只替换 Worker，API
  故障先清理旧 Worker/进程组再生成新 runtime id 重建整组；默认仍为 `combined`。
- Worker heartbeat、每个 RuntimeManager 子系统健康、draining、generation 和 PID 使用协议 v2
  原子状态文件发布；15 秒未推进按 hang 处理，墙钟回拨不影响单调计时判断。
- 重启策略完整实现 1/2/4/8/16/30 秒退避、±20% jitter、5 分钟失败窗口、60 秒 circuit
  breaker 和稳定 5 分钟重置；API 在 Worker 降级或冷却期间继续服务。
- 四个 stdout/stderr stream 从 spawn 时立即排空，每流尾部硬限 1 MiB，展示前统一脱敏；
  Agent descriptor、Desktop IPC、preload 和前端向后兼容地增加 Worker/degraded 状态。
- 正常关闭按 Worker → API 顺序执行，Worker 有 5 秒有界 drain；POSIX 等待并在超时后清理
  完整角色进程组。真实测试证明正常空闲整组 10 秒内退出，强杀 Electron 后 15 秒内无角色
  进程组存活成员。
- 首次 Desktop 全量暴露两套真实 supervisor 并行选择同一端口的测试竞态，复现表现为一套
  supervisor 连接到另一套 API 并收到 401；为测试组合根增加独立可扫描端口范围后，同一
  全量命令通过。首次 Backend 全量暴露 core → services 脱敏依赖边界违规；脱敏实现下沉到
  纯 core 模块后架构门禁通过。两次失败和修复均保留在验收报告。
- G4 最终回归：Backend 1806 passed、1 skipped；CLI 196 passed；Frontend、Desktop、Website
  全绿；统一全仓门禁 0 failures，5m29s。
- 详细证据：[`desktop_api_worker_goal_acceptance.md`](../development/desktop_api_worker_goal_acceptance.md)

### G5：并发、故障与兼容（已完成）

- 完成日期：2026-08-09
- 六类 Worker 工作负载完成真实进程边界 kill、generation recovery、取消/fence、网络恢复和
  SQLite 不变量矩阵；连续替换 Worker 200 次后由第 201 代正常收敛。
- 只读、disk full、WAL/SHM、损坏库、锁竞争、时间跳变、上一 stable revision 升级和
  split → combined → split 回退均通过。
- AC-WORK、AC-COMPAT、AC-DATA-02～05 已关闭；AC-DATA-01 不能由累计短测替代，保留到
  G6 两平台单次连续 8 小时 seeded chaos。
- 详细命令、次数、首次失败与资源数据见
  [`desktop_api_worker_goal_acceptance.md`](../development/desktop_api_worker_goal_acceptance.md)。

### G6：冻结包、安装升级与长稳（执行中）

- 开始日期：2026-08-10
- 已实现跨平台 packaged QA driver、macOS DMG runner、Windows NSIS/Parallels runner、真实
  上一稳定版 seeder、原生系统 sleep/wake、六类持续负载、seeded chaos、数据库/资源审计和
  机器可读报告。
- 当前包和上一稳定版包均有独立 SHA-256 来源门禁；Windows host/guest/driver 与 macOS
  runner/driver/report 会交叉核对，且正式入口拒绝不完整的摘要或脏工作树。Windows lifecycle
  必须安装候选 workflow 的确切 NSIS；VM 内同 SHA 重建只验证打包合同，不能替代候选字节。
- 最新开发全仓回归为 Backend 1861、CLI 196、Frontend/Desktop/Website 全部通过，
  0 failures、10m06s；changed Python Ruff、Frontend lint/build、Desktop typecheck/build、
  packaging/release 合同均通过。
- 真实 Windows PowerShell 5.1 对同摘要 runner 完成 0-error AST 解析，并验证兼容
  `Arguments/EnvironmentVariables` API 可携带非 ASCII 参数；完整 NSIS/系统休眠仍留给正式流程。
- 当前桌面默认仍为 `combined`；本阶段正式证据全部通过前不得进入 G7。
- 本机脏工作树只用于开发验证。正式证据仍必须来自 clean committed SHA、真实上一稳定版
  安装包和两平台最终冻结包；当前版本与上一稳定版同为 2.5.4，且缺失 24h/8h、Windows VM
  与 macOS DMG 结果，相关门禁保持未通过。
- 进展与阻断项持续记录在
  [`desktop_api_worker_goal_acceptance.md`](../development/desktop_api_worker_goal_acceptance.md)。
