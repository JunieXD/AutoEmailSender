# 桌面端 API + Worker 双进程 Goal 验收记录（前置证据）

- 状态：原 Goal 已取消；已有证据冻结并由通用 Beta Goal 继续使用
- Goal 启动日期：2026-08-09
- 计划：[`desktop-api-worker-process-plan.md`](../architecture/desktop-api-worker-process-plan.md)
- 后续计划：[`desktop-api-worker-beta-goal-plan.md`](../architecture/desktop-api-worker-beta-goal-plan.md)
- 后续验收：[`desktop_api_worker_beta_goal_acceptance.md`](./desktop_api_worker_beta_goal_acceptance.md)
- 基线 commit：`6e06be9bfeae11b78eae78096782d84b3176c931`
- 基线平台：macOS Apple Silicon
- 工具链：Node.js 24.18.0、Python 3.12.13、uv 0.11.26

## G0：基线与测试基础设施

### 环境准备记录

首次统一测试运行时，此独立 worktree 尚无 Python virtualenv 和 Node `node_modules`；
`--no-sync` 创建空 Python 环境后，各工作区因缺少依赖而失败。该结果被保留为环境准备
证据，不归类为产品基线失败。

随后严格使用锁文件完成：

```text
backend/: uv sync --dev
cli/: uv sync --dev
frontend/: npm ci
desktop/: npm ci
website/: npm ci
```

未修改依赖声明或锁文件。

### 干净功能基线

```text
rtk proxy uv run --project backend --no-sync python scripts/quality/run_all_tests.py
```

结果：

| 工作区 | 结果 | 用时 |
| --- | --- | ---: |
| Backend | 1761 tests passed | 2m37s |
| CLI | 196 tests passed | 19s |
| Frontend | passed | 17s |
| Desktop | passed | 2s |
| Website | passed | 1s |
| 合计 | 0 failures | 3m18s |

### G0 新增基础设施

- `backend/app/core/fault_injection.py`：默认完全不生效、只有显式测试环境和白名单 fault
  point 同时存在才暂停的同步/异步故障门。
- `backend/test/process_harness.py`：真实进程启动、随机端口、HTTP readiness、文件日志、
  有界停止和 fault marker 控制。
- `backend/test/test_process_harness.py`：验证 fault gate 的默认关闭、真实进程停点/释放、
  `desktop_entry.py` 迁移/readiness，以及同数据目录真实实例锁。

定向验证：

```text
cd backend
uv run python -m unittest \
  test.test_process_harness \
  test.test_backend_instance \
  test.test_desktop_runtime
```

结果：27 tests passed。

变更后完整回归使用同一统一命令，结果：

| 工作区 | 结果 | 用时 |
| --- | --- | ---: |
| Backend | 1765 tests passed | 1m39s |
| CLI | 196 tests passed | 19s |
| Frontend | passed | 14s |
| Desktop | passed | <1s |
| Website | passed | <1s |
| 合计 | 0 failures | 2m14s |

附加门禁：新增 Python 文件 Ruff 通过；文档拓扑与本地链接 4/4 通过。

G0 状态：已完成（2026-08-09）。

## G1：角色、锁与迁移所有权

状态：已完成（2026-08-09）。

实现证据：

- `desktop_entry.py --role api|worker|combined` 使用同一入口和冻结依赖集合。
- API/group、Worker 和 migration 三类 OS file lock 互相独立且各自唯一。
- Worker 只执行 schema head/compatibility 检查；迁移和 cold-start recovery 只在 API 或
  combined 执行。
- API/Worker runtime 状态以 runtime id、PID 和 generation 防止读取旧进程状态。

真实进程专项结果：9 tests passed，覆盖：

- API + Worker 同数据目录同时 ready，PID 不同；
- Worker 端口不可连接；
- 第二个 API、第二个 Worker 快速失败且不影响首个角色；
- Worker-first 不创建数据库；
- API 卡在 migration lock fault gate 时 Worker 拒绝启动，释放后仅 API 完成迁移；
- runtime id 错配拒绝；
- API leader 退出后 Worker 自行退出。

附加验证：

| 验证 | 结果 |
| --- | --- |
| Backend 完整 unittest | 1776 passed，1 skipped |
| `desktop_entry.py --self-check --role api` | passed |
| `desktop_entry.py --self-check --role worker` | passed |
| `desktop_entry.py --self-check --role combined` | passed |
| 相关 Python Ruff | passed |

## G2：后台边界、取消、缓存与恢复

状态：已完成（2026-08-09）。

实现证据：

- `RuntimeManager` 只在 Worker 或显式 combined 组合根启动；API split role 不持有后台
  manager。
- 批量草稿使用 1 秒只读数据库 watcher 感知暂停、停止、claim 替换和租约失效；90 秒
  lease 仍按低频写入续期。专项测试未调用本地 coordinator，仍能取消在途 LLM coroutine，
  且迟到结果不能提交。
- Crawler profile text cache key 从进程本地 factory/job/candidate/url 扩展为
  factory/job/**persistent run**/candidate/url。API 取消测试刻意保留本进程旧 cache，仍以
  持久化 job/task 状态完成取消；新 run 不命中旧 cache。
- Crawler owner id 形如
  `crawler-worker-N:<runtime_id>:<worker_generation>`，替换 Worker 的 owner 不会复用。
- 真实 OS 进程测试启动 API + Worker，在数据库中保持一个 API 手动匹配 run 为 running，
  停止并以新 generation 启动 Worker；过期 Worker item 被恢复为 canceled，手动 run 仍为
  running，API PID 全程存活。

定向验证：

```text
cd backend
uv run --no-sync python -m unittest \
  test.test_batch_draft_generation_runtime \
  test.test_crawler_v2_profile_text_cache \
  test.test_crawler_v2_enrichment_worker \
  test.test_professor_information_enrichment_api \
  test.test_crawler_v2_runtime_routing \
  test.test_runtime_manager \
  test.test_backend_roles \
  test.test_startup_runtime \
  test.test_match_analysis_jobs
```

结果：117 tests passed。

完整验证：

| 验证 | 结果 | 用时 |
| --- | --- | ---: |
| Backend 完整 unittest | 1780 passed，1 skipped | 1m49s |
| Backend（统一全仓 runner） | 1780 passed | 1m53s |
| CLI | 196 passed | 18s |
| Frontend | passed | 14s |
| Desktop | passed | <1s |
| Website | passed | <1s |
| 全仓合计 | 0 failures | 2m28s |

相关 Python Ruff 与架构边界测试均通过。G2 没有新增 schema 或迁移；桌面默认仍为
`combined`。

## G3：邮件 at-most-once 与最小提交窗口

状态：已完成（2026-08-09）。

实现证据：

- 新增 `email_delivery_attempts`，持久化 attempt id、owner role、runtime id、generation、PID、
  prepared Message-ID、内容快照与终态；`EmailTask` 保存当前 attempt/outcome，`EmailLog` 对
  非空 attempt id 建立唯一索引。
- Alembic head `20260809_delivery_at_most_once` 从
  `20260808_crawl_llm_snapshot` 升级；旧 `sending` 记录保守迁移为
  `sent + assumed_sent_after_interruption`。迁移专项验证 schema、索引、外键、数据保留及
  `PRAGMA foreign_key_check`。
- 发送准备全部位于 claim 前；claim 与身份发送窗口在一个短事务中提交。claim 之后只调用
  prepared SMTP transport 和最终 CAS 事务，不再读取附件、渲染模板或调用 Sent/IMAP。
- SMTP 明确拒绝记录为 `pre_submission_failed`；连接在提交开始后丢失、响应丢失、取消或未知
  异常均保守记录为 `assumed_sent_after_interruption`。该状态用户可见为 sent，不触发用户确认
  或任何自动重发。
- 最终事务同时更新 attempt/task，并写入唯一 EmailLog 和操作日志；若事务失败，task 保持
  `sending`。best-effort abandoned marker 与进程内标记允许恢复；即使模拟磁盘满导致 marker
  写入也失败，task 仍不可 dispatch，随后恢复为 assume-sent。
- API/Worker owner liveness 使用 runtime status 的 role/runtime/generation/PID；combined 使用
  当前进程 incarnation。存活 owner 不因墙钟超时被恢复，失效 owner 恢复后旧 SMTP 返回被
  attempt CAS 拒绝。

真实进程故障矩阵：

| 入口 | fault point | kill 时 DATA 接受数 | 恢复终态 | 恢复后 DATA 总数 |
| --- | --- | ---: | --- | ---: |
| API 立即发送 | `delivery.before_claim` | 0 | 新 API 安全发送成功 | 1 |
| API 立即发送 | `delivery.claim_committed` | 0 | assumed sent | 0 |
| API 立即发送 | `delivery.before_smtp` | 0 | assumed sent | 0 |
| API 立即发送 | `delivery.smtp_accepted` | 1 | assumed sent | 1 |
| API 立即发送 | `delivery.before_final_commit` | 1 | assumed sent | 1 |
| API 立即发送 | `delivery.after_final_commit` | 1 | smtp accepted | 1 |
| Worker 排程发送 | `delivery.before_claim` | 0 | 新 Worker 安全发送成功 | 1 |
| Worker 排程发送 | `delivery.claim_committed` | 0 | assumed sent | 0 |
| Worker 排程发送 | `delivery.before_smtp` | 0 | assumed sent | 0 |
| Worker 排程发送 | `delivery.smtp_accepted` | 1 | assumed sent | 1 |
| Worker 排程发送 | `delivery.before_final_commit` | 1 | assumed sent | 1 |
| Worker 排程发送 | `delivery.after_final_commit` | 1 | smtp accepted | 1 |

补充 fault 证据：本地 STARTTLS fake SMTP 在接受 DATA 后主动断开、不返回最终响应；系统记录
assume-sent，超过一个 dispatcher 周期后服务端接受数仍为 1。单元故障注入另覆盖
`database is locked`、`database or disk is full`、abandoned marker 同时写失败、存活 owner
长 SMTP、失效 owner 恢复和迟到成功返回。

性能与结构证据：

- 500 次本地 fake SMTP 发送全部成功，DATA 接受数 500；“SMTP success 到
  `dispatch_email_task` 返回”的保守上界 p99 为 **2.171 ms**，低于 250 ms。
- AST 门禁确认 prepare → claim → prepared SMTP 顺序；最终事务只 await SQLAlchemy 操作、
  操作日志写入和默认关闭的 fault gate；SMTP DATA 成功后不执行 `QUIT` 网络往返。

验证记录：

```text
cd backend
uv run --no-sync python -m unittest \
  test.test_batch_task_dispatch_schedule \
  test.test_mail_runtime \
  test.test_email_delivery_process_safety \
  test.test_email_delivery_structure \
  test.test_database_schema \
  test.test_api_endpoints \
  test.test_operation_log_integration
```

结果：402 tests passed，87.1s；发现并修复测试 HTTP 客户端在强杀 API 后未显式关闭 socket 的
ResourceWarning，强制显示 ResourceWarning 的 API kill 矩阵复跑无警告。

```text
cd backend
uv run --no-sync python -m unittest discover test
```

结果：1800 tests passed，1 skipped，180.5s。唯一首次失败来自 Agent API 测试仍 mock 旧
`send_email()` 接缝；改为 `send_prepared_email()` 后相关 82 tests passed，再次 Backend 全量
1800 tests passed、1 skipped，180.5s。

```text
rtk proxy uv run --project backend --no-sync python scripts/quality/run_all_tests.py
```

| 验证 | 结果 | 用时 |
| --- | --- | ---: |
| Backend | 1800 passed | 2m47s |
| CLI | 196 passed | 18s |
| Frontend | passed | 14s |
| Desktop | passed | <1s |
| Website | passed | <1s |
| 全仓合计 | 0 failures | 3m22s |

此后新增并单独通过 1 个磁盘满且 marker 写失败专项测试；它只扩展测试、不改变运行代码，后续
全量门禁将自然纳入，Backend 收集数将变为 1801。

AC-MAIL 映射：

| AC | 状态 | 主要证据 |
| --- | --- | --- |
| AC-MAIL-01 | 通过 | API 与 Worker `before_claim` 强杀，未 claim 可安全发送 1 次 |
| AC-MAIL-02 | 通过 | 两入口 claim commit/SMTP 前强杀，0 次 DATA、assume-sent、不重发 |
| AC-MAIL-03 | 通过 | 两入口 SMTP accepted/commit 前强杀，DATA 总数恒为 1 |
| AC-MAIL-04 | 通过 | after-commit 强杀后 task/attempt/EmailLog 一致且各 1 条 |
| AC-MAIL-05 | 通过 | 明确拒绝分类与真实丢失 SMTP 响应均通过 |
| AC-MAIL-06 | 通过 | locked、disk full、marker 写失败和 SIGKILL 均保持不可 dispatch |
| AC-MAIL-07 | 通过 | Sent sync 被禁用；无 IMAP 回查和用户确认流程 |
| AC-MAIL-08 | 通过 | AST 门禁；500 次 p99 2.171 ms |
| AC-MAIL-09 | 通过 | 存活 owner 不按超时恢复；失效恢复后迟到返回被 CAS fencing |

## G4：Electron runtime-group supervisor

状态：已完成（2026-08-09）。桌面默认模式仍为 `combined`；只有显式设置
`AUTO_EMAIL_SENDER_BACKEND_MODE=split` 才启用 split，默认切换留到 G7。

### 实现证据

- split controller 由 Electron 同级监督一个 API 和一个 Worker；启动顺序为 API ready 后才
  spawn Worker。Worker 不继承 UI/Agent token，也不监听 TCP 端口。
- Worker 意外退出、heartbeat hang 或 Worker 子系统降级均保持 API base URL、PID 和 runtime
  id；仅 Worker replacement generation 变化。API 意外退出时先停止旧 Worker，再生成新
  runtime id 重建 API + Worker。
- Worker 每 2 秒原子写协议 v2 状态，包含 runtime/generation/PID、health、draining，以及
  dispatcher、两个 IMAP loop、batch drafts、matching loops、crawler loops 的最近开始、成功、
  失败、连续失败数和脱敏错误。
- Electron 用单调时钟判断 heartbeat 是否推进；15 秒阈值、1 秒检查周期。虚拟时钟测试覆盖
  1/2/4/8/16/30 秒退避、±20% jitter、5 分钟内 5 次失败后的 60 秒 circuit breaker、过期
  circuit 和稳定运行 5 分钟后的重置。
- API/Worker 的 stdout/stderr 从 spawn 时立即排空。两个真实 Node 子进程分别写入 100 MiB
  stdout 和 100 MiB stderr，四个完成标记均被读到，每个 stream 缓存最终恰为 1 MiB。
- IPC/Agent descriptor 保持 `backend` 表示 API，向后兼容地增加可选 `worker`；前端在
  `degraded/background_unavailable` 时保留 API 连接并提示后台不可用但查询/编辑仍可使用。
- 正常关闭按 Worker → API 顺序；Worker 最多 drain 5 秒，Electron 最多等待 Worker 8 秒、
  API 5 秒并清理完整 POSIX 进程组。状态监听器异常被隔离，不会破坏监督循环。
- 脱敏实现位于 `app.core.diagnostic_redaction`，Backend core 不反向依赖 services；错误日志、
  runtime status 和 Electron 展示均不保留 token、密码或正文类字段。

### 真实生命周期与专项验证

平台：macOS 26.5.2（25F84），Apple Silicon arm64；Node.js 24.18.0、Python 3.12.13、
uv 0.11.26。当前 commit 为基线 SHA `6e06be9bfeae11b78eae78096782d84b3176c931`，worktree
包含本 Goal 的未提交改动，因此不是 clean worktree。未使用真实外部网络服务或真实邮箱。

```text
cd desktop
rtk npm run test -- runtimeGroup.integration.test.ts
```

结果：1 test passed，37.79s（测试框架总用时 37.92s）。真实矩阵依次证明：

1. SIGKILL Worker 后先观察 degraded；同一 API 上 GET 与 PATCH 均成功；退避后只出现一个新
   Worker，API PID/runtime id 不变。
2. 注入一个带密码和正文的 degraded subsystem 状态，前端状态只含脱敏摘要；恢复 healthy
   后仍为原 Worker，没有进程级 restart storm。
3. SIGSTOP Worker 后，`background_hung` 不早于 14 秒且在 20 秒测试门槛内出现；只替换
   Worker，API PID 不变。
4. SIGKILL API 后先确认旧 Worker 消失，再出现新 runtime id、新 API 和新 Worker。
5. 正常 stop 后两个最终 PID 均消失，包含进程组等待在内的退出用时小于 10 秒。

```text
cd desktop
rtk npm run test -- runtimeGroupParentDeath.integration.test.ts
```

结果：1 test passed，4.02s（框架总用时 4.11s）。外层测试 SIGKILL 模拟 Electron 的独立
父进程组，持续检查 API/Worker PID 以及两个角色 process group 的非 zombie 成员；全部在
15 秒门槛内消失，测试后 `pgrep` 未发现该 runtime 的残留。

```text
cd desktop
rtk npm run typecheck
rtk npm run test -- \
  backend.test.ts restartPolicy.test.ts processOutput.test.ts \
  workerStatus.test.ts ipcContracts.test.ts
```

结果：typecheck passed；5 files、35 tests passed，195ms。覆盖 Worker token 剥离、descriptor/
IPC 合同、虚拟时钟退避、协议 v2 校验、四流 100 MiB 压力和 1 MiB 硬上限。

```text
cd backend
rtk uv run --no-sync python -m unittest \
  test.test_runtime_manager \
  test.test_backend_roles \
  test.test_operation_logs \
  test.test_sqlite_diagnostics
```

结果：40 tests passed，52.518s。覆盖 5 秒 drain 宽限、超时取消且无任务泄漏、各 loop 健康
恢复、status heartbeat、角色校验和错误日志脱敏。相关 Ruff 全部通过。

### 首次失败与修复记录

第一次 Desktop 全量（2026-08-09 19:02 CST）不是成功：26 个文件中
`runtimeGroupParentDeath.integration.test.ts` 在 ready 前退出，最终 1 failed、181 passed、
1 skipped。随后同时运行两个真实 runtime-group 文件稳定复现为 `Runtime identity request
failed: 401`：两套独立 supervisor 并行执行 `findAvailablePort(48120)`，在监听前的 TOCTOU
窗口选中同一端口，其中一套连到了另一套 token 不同的 API。修复包括：

- parent-death 错误从启动时截取空 stderr 改为失败时读取有界脱敏尾部；
- `StartBackendOptions.portRangeStart` 允许测试组合根使用互不重叠、仍会探测可用性的端口范围；
- 两个真实文件与 backend helper 同跑：3 files、25 tests passed，37.35s。

第一次 Backend 全量也不是成功：架构门禁报告
`app/core/backend_error_logging.py -> app.services.operation_logs` 为新增反向层级依赖。修复为把
纯文本脱敏移动到 `app/core/diagnostic_redaction.py`，`operation_logs` 继续向后兼容地导出同一
函数。架构、operation log、SQLite diagnostics 和 Agent error 专项共 18 tests passed；Ruff
passed。未把该违规加入 reviewed legacy baseline。

### G4 最终回归

```text
cd desktop
rtk npm run typecheck && rtk npm run test && rtk npm run build
```

结果：typecheck/build passed；26 files passed、1 platform file skipped；182 tests passed、
1 Windows-only test skipped，39.03s。唯一 skip 是当前 macOS 无法执行的 Windows 条件测试，
不是 G4 必选场景的静默跳过。

```text
cd frontend
rtk npm run lint && rtk npm run test && rtk npm run build
```

结果：lint/build passed；123 files、952 tests passed，22.76s。

```text
cd backend
rtk uv run --no-sync python -m unittest discover test
```

结果：1806 tests passed、1 existing platform/optional test skipped，224.234s。

```text
rtk proxy uv run --project backend --no-sync python scripts/quality/run_all_tests.py
```

| 工作区 | 结果 | 用时 |
| --- | --- | ---: |
| Backend | 1806 passed | 4m05s |
| CLI | 196 passed | 21s |
| Frontend | passed | 22s |
| Desktop | passed | 40s |
| Website | passed | <1s |
| 合计 | 0 failures | 5m29s |

### AC-LIFE 映射

| AC | 状态 | 主要证据 |
| --- | --- | --- |
| AC-LIFE-01 | 通过 | Worker kill；API GET/PATCH；单 replacement；PID/runtime id 不变 |
| AC-LIFE-02 | 通过 | API kill；旧 Worker 先退出；新 runtime id/API/Worker |
| AC-LIFE-03 | 通过 | Electron fixture SIGKILL；15 秒内 PID 与非 zombie process group 成员清零 |
| AC-LIFE-04 | 通过 | SIGSTOP；单调时钟门禁 14–20 秒；只替换 Worker |
| AC-LIFE-05 | 通过 | 单 subsystem degraded、错误脱敏、API/其他循环可用且不重启 |
| AC-LIFE-06 | 通过 | 虚拟时钟验证退避、jitter、cap、circuit、prune 与稳定重置 |
| AC-LIFE-07 | 通过 | 两进程各 100 MiB × stdout/stderr；四流完成；每流恰为 1 MiB |

## G5：并发、故障与兼容

状态：离散故障、工作负载与兼容专项已完成（2026-08-10）；G5 正式状态仍为执行中。
AC-WORK 与 AC-COMPAT 已全部通过，AC-DATA-02～05 已通过；AC-DATA-01 要求连续至少
8 小时并发运行，将由 G6 最终冻结包 seeded-chaos 同时提供更强证据。在取得该时长证据前，
不把 G5 标为完成。

### API 启动门禁与 Worker generation recovery

- API 为了让 Electron 能查询 `/health`、`/ready` 和 `/startup-status`，仍会在迁移与 cold
  recovery 完成前开始监听；新增统一 readiness middleware，此时所有业务路由返回带
  `Retry-After: 1` 的 503。初始化失败后业务路由返回相同的脱敏启动错误，不能绕过失败状态
  继续读写数据库。
- 真实进程在 `migration.lock_acquired` 暂停期间证明 `/health` 可用、`/api/ping` 为 503，且
  Worker 拒绝加入；释放屏障、迁移与 recovery 完成后 `/api/ping` 才变为 200。这样 API
  manual IMAP/Match 等同步请求不可能早于 cold recovery 进入。
- Worker 在验证 API leader/schema 后、启动任何 `RuntimeManager` 循环前，按新 generation
  恢复 Batch Draft、Match Job、Crawler v2 和 IMAP 后台 claim。正式故障矩阵把旧 claim 的
  lease 统一推到未来 365 天；replacement 仍立即恢复，证明正确性不依赖墙钟超时。
- Match Job item 在外部调用前原子绑定具体 `MatchAnalysisRun`，因此只终止该 item 的旧 run，
  不按身份/导师误伤 API 手动匹配。Worker-only IMAP recovery 保留 API `claim_kind=full` 及其
  嵌套 history claim，并以 SQLite `BEGIN IMMEDIATE` 和同时开始的手动同步串行化；API cold
  start 才恢复全部 IMAP claim。
- generation recovery 刻意不导入 delivery attempt，也不修改任何 `sending` 邮件状态。存活
  delivery owner 在墙钟前跳七天时仍保持 sending；失效 owner 继续只走 assume-sent 路径，
  不会变回 dispatchable。

专项验证：启动/角色/运行时/SQLite 60 tests passed，87.665s；Crawler 120 tests passed，
154.306s；IMAP/并发 122 tests passed，82.008s。相关真实进程测试另覆盖 API 手动匹配在
Worker 换代及墙钟回拨一年后仍为 running，以及 Worker recovery 保留 API full IMAP claim。

### Batch Draft 真实进程故障矩阵

- 新增本地 OpenAI-compatible fake LLM，使用真实 HTTP 连接并逐请求计数；测试预置已确认的
  endpoint、thinking 与 structured-output 适配缓存，保证一次业务外部调用恰好对应一个
  HTTP 请求，不把协议探测混入业务次数。
- 真实 API 在随机端口和临时数据目录启动；真实 Worker 分别阻塞在 `before_claim`、
  `claim_committed`、`before_external_call`、`external_call_returned`、`before_final_commit`、
  `after_final_commit`，随后由测试执行 OS 级强杀并以新 generation 启动 replacement Worker。
- 对 claim 后且尚未提交的强杀，测试把旧租约推到未来 365 天；replacement 先执行
  generation recovery，再重新认领。每轮均检查 API 在 Worker 消失期间仍为 ready、claim 字段最终
  全部清空、EmailLog 中只有一条 draft、operation log 中只有一条
  `email_task.draft_generated`，且 API thread 读到 `review_required`。
- 外部调用返回后或 commit 前强杀会产生第二次 LLM 调用，但第一次未提交结果不会写入；
  commit 后强杀不再调用 LLM。各断点的请求次数分别稳定为 1、1、1、2、2、1。
- 另有真实 API pause/stop 交错：Worker 已取得 LLM 结果但停在写库前，API 提交暂停或停止后
  再放行旧 Worker。旧结果被 claim/status fencing；pause 后 replacement Worker + API resume
  最终只提交一份草稿，stop 后保持 canceled 且零草稿日志。该路径不依赖 API 进程内
  coordinator。

验证命令：

```text
cd backend
rtk env AUTO_EMAIL_SENDER_BATCH_DRAFT_CHAOS_REPETITIONS=20 \
  uv run --no-sync python -m unittest -v \
  test.test_batch_draft_process_safety
```

最新 generation-recovery 正式复跑结果：2 tests passed，627.919s；测试内部共执行 160 个真实进程场景，其中六个 kill 断点
各 20 次（120 场景），pause/stop 各 20 次（40 场景）。没有失败、重试掩盖或外部网络访问。

当前 AC 映射：

| AC | 状态 | 主要证据 |
| --- | --- | --- |
| AC-WORK-01 / Batch drafts | 通过 | 六断点各 20 次；claim、HTTP 次数、唯一日志与终态均通过 |
| AC-WORK-03 / Batch drafts | 通过 | API pause/stop 各 20 次；1 秒持久化 watcher 之外另有提交前 fence |

### Match Jobs 真实进程故障矩阵

实现矩阵前的代码审计发现并修复了一个真实跨事务窗口：原实现先提交
`MatchAnalysisRun`/canonical result，随后才用 claim CAS 提交 job item 成功；旧 owner 即使
最终 CAS 失败，也可能已经覆盖 canonical result。现在有效 claim、未取消 job、未过期 lease、
item succeeded、run result 和 canonical result 在同一 SQLite 写事务内完成；失效 claim 的
结果回滚并把对应旧 run 标为 `stale_claim`。Worker 启动还会收敛“item 已终态但 job summary
尚未更新”的 after-commit 崩溃窗口。

真实矩阵覆盖 `before_claim`、`claim_committed`、`before_external_call`、
`external_call_returned`、`before_final_commit`、`after_final_commit`。每轮均强杀 Worker、把
旧 lease 推到未来 365 天、启动新 generation，并验证 canonical result 恰好 1 条、成功 run 恰好
1 条、无永久 running run、完成日志恰好 1 条、claim 清空、API 持续 ready 且最终读取
completed。另在 LLM 结果已返回时通过真实 API 提交 cancel，再放行 Worker，验证零 canonical
result、零成功 run、item/job 均 canceled。

```text
cd backend
rtk env AUTO_EMAIL_SENDER_MATCH_CHAOS_REPETITIONS=20 \
  uv run --no-sync python -m unittest -v \
  test.test_match_analysis_process_safety
```

最新 generation-recovery 正式复跑结果：2 tests passed，516.940s；内部共 140 个真实进程场景，其中六个 kill 断点各 20 次
（120 场景），API cancel 交错 20 次。此前相关 52 个 Match、角色与恢复测试也全部通过，
32.194s。

| AC | 状态 | 主要证据 |
| --- | --- | --- |
| AC-WORK-01 / Match jobs | 通过 | 六断点各 20 次；原子 claim/result/item、唯一 canonical 与终态 |
| AC-WORK-03 / Match jobs | 通过 | API cancel 20 次；返回后的旧结果被持久化 claim/job fence 拒绝 |

### IMAP Incremental / History 真实进程故障矩阵

- `process_harness.py` 新增本地 plaintext IMAP4rev1 服务，生产 `imaplib` 客户端真实执行
  `CAPABILITY`、`LOGIN`、`ID`、`LIST`、`SELECT`、`UID SEARCH`、`UID FETCH`、
  `UIDVALIDITY`、`UIDNEXT` 与 `LOGOUT`；所有邮件、端口和数据库均为每场景隔离的本地资源。
- Incremental 与 History 分别设置 `before_claim`、`claim_committed`、
  `before_external_call`、`external_call_returned`、`before_final_commit`、
  `after_final_commit` 六个仅测试环境可启用的断点。每次同步 attempt 的相同 stage 最多阻塞一次，
  不改变生产默认路径。
- 每个断点均启动真实 API/Worker，停在目标边界后执行 OS 级强杀，确认 API 仍为 ready，
  把旧 identity/item lease 推到未来 365 天，再以新 generation 启动 replacement Worker；
  recovery 不等待墙钟过期。
- Incremental 从 UID cursor 10 收敛到 11；History 从 professor cursor 5 收敛到 11。
  `before_final_commit` 特别覆盖“EmailLog 已提交、cursor 尚未提交”窗口；replacement 再次读取
  同一 UID 时通过 IMAP location 唯一键去重，最终始终只有一条 received EmailLog。
- 另对两类同步各执行 20 次 lease-owner 替换：旧 Worker 已拿到真实 IMAP 结果后暂停，测试在
  SQLite 事务中将过期 lease 替换为新 claim，再放行旧 Worker。旧 owner 的提交因同事务 lease
  CAS 失败而整体回滚，不能写日志或推进 cursor；随后真实 replacement Worker 正常收敛。
- 每个场景都检查 UID、UIDVALIDITY、唯一 IMAP location、子 claim 清理和
  `PRAGMA integrity_check = ok`，完成后再等待并复查一次，排除下一轮 poll 产生重复写入。

验证命令：

```text
cd backend
rtk env AUTO_EMAIL_SENDER_IMAP_CHAOS_REPETITIONS=20 \
  uv run --no-sync python -m unittest -v \
  test.test_imap_process_safety
```

最新正式复跑结果：5 tests passed，1331.105s；内部共 280 个高重复真实进程场景：Incremental 六个 kill 断点
各 20 次（120 场景）、History 六个 kill 断点各 20 次（120 场景），以及两类 lease-owner
替换各 20 次（40 场景）；另包含 1 个断网/恢复真实进程场景。无失败、重试掩盖或外部网络访问。

相关 IMAP、并发、mail runtime 与 harness 回归：210 tests passed，72.645s。

| AC | 状态 | 主要证据 |
| --- | --- | --- |
| AC-WORK-01 / IMAP incremental | 通过 | 六断点各 20 次；有效 owner 单调写 cursor，旧 owner 20 次均被 fence |
| AC-WORK-01 / IMAP history | 通过 | 六断点各 20 次；历史 cursor 不倒退、相同邮件唯一、旧 owner 20 次均被 fence |
| AC-DATA-04 / IMAP 子矩阵 | 通过（子矩阵） | 280 场景逐轮完整性、唯一位置和状态机不变量检查通过 |

### Crawler v2 Page / Chunk / Enrichment 真实进程故障矩阵

实现矩阵前的代码审计发现并修复了两个跨进程风险：Page 与 Enrichment 的网页抓取上下文
原先没有携带工作项 claim fence，旧 generation 可能在最终工作项校验前写入 `crawl_pages`
或 fetch ledger；Enrichment 的持久化 profile 正文复用也没有按持久化 `current_run_id`
隔离，retry 后可能读取上一轮相同 URL 的旧正文。现在所有页面快照、fetch ledger、候选、
token usage 与最终状态写入均受同一 work-item owner/lease fence 约束；新建 `CrawlPage`
显式记录高精度 UTC 时间，同一 run 的 replacement 可复用已成功持久化的正文，跨 run
则必须重新抓取。

真实矩阵分别覆盖 Page、Chunk、Enrichment 的 `before_claim`、`claim_committed`、
`before_external_call`、`external_call_returned`、`before_final_commit`、
`after_final_commit`。每轮使用随机端口、临时数据库、真实 API/Worker 进程、本地
OpenAI-compatible fake LLM 和本地 fake HTTP；HTTP 只通过同时满足 fault test gate、显式
allowlist 和 `*.test.invalid` 保留域名的测试专用 pinned-loopback 解析进入，生产 SSRF 策略
没有放宽。Worker 被 OS 强杀后，测试把旧 lease 推到未来 365 天并启动新 generation；逐轮验证
API 持续 ready、唯一 canonical candidate、无永久 processing、claim 全清、job/run 收敛到
`needs_review`、`PRAGMA integrity_check = ok` 且 `foreign_key_check` 为空。

额外交错覆盖：

- API 在三类 Worker 已取得外部结果后各执行 20 次 cancel，旧结果均被 job/claim fence
  拒绝，任务保持 canceled，API 不依赖进程内 coordinator。
- 三类工作项各执行 20 次 owner 替换：旧 Worker 暂停后直接以新的持久化 owner/lease 覆盖，
  放行旧 Worker 后其迟到结果不能写业务表；再由真实 replacement 收敛。
- Enrichment 另执行 20 次输入变更：旧 Worker 已取得旧 URL、旧正文和旧 LLM 结果后，
  profile URL 与 owner 同时持久化替换。旧结果被拒绝；replacement 只抓取新 URL，最终字段
  来自新正文。该路径同时证明 URL 变化不会命中旧 cache。
- 同 Worker 命中、同 job retry/run 隔离和正文变化另有单元测试；Enrichment 在
  `external_call_returned`/`before_final_commit` 强杀后的 replacement 冷启动逐轮从同一 run
  的 `CrawlPage` 复用正文，HTTP 请求总数保持 1，只重做未提交的 LLM 调用。

正式验收按四个完全隔离的组并行执行（每组内部顺序重复 20 次，不共享端口、数据库或
fake 服务）：

```text
cd backend
rtk env AUTO_EMAIL_SENDER_CRAWLER_CHAOS_REPETITIONS=20 \
  uv run --no-sync python -m unittest -v \
  test.test_crawler_process_safety.CrawlerProcessSafetyTests.test_page_worker_kill_matrix_converges_with_one_candidate

rtk env AUTO_EMAIL_SENDER_CRAWLER_CHAOS_REPETITIONS=20 \
  uv run --no-sync python -m unittest -v \
  test.test_crawler_process_safety.CrawlerProcessSafetyTests.test_chunk_worker_kill_matrix_converges_with_one_candidate

rtk env AUTO_EMAIL_SENDER_CRAWLER_CHAOS_REPETITIONS=20 \
  uv run --no-sync python -m unittest -v \
  test.test_crawler_process_safety.CrawlerProcessSafetyTests.test_enrichment_worker_kill_matrix_reuses_persisted_profile

rtk env AUTO_EMAIL_SENDER_CRAWLER_CHAOS_REPETITIONS=20 \
  uv run --no-sync python -m unittest -v \
  test.test_crawler_process_safety.CrawlerProcessSafetyTests.test_api_cancel_fences_all_returned_crawler_results \
  test.test_crawler_process_safety.CrawlerProcessSafetyTests.test_replaced_owner_fences_all_late_crawler_results \
  test.test_crawler_process_safety.CrawlerProcessSafetyTests.test_enrichment_input_change_rejects_stale_result_and_refetches
```

最新 generation-recovery 正式复跑结果：Page 120 场景，737.485s；Chunk 120 场景，
482.232s；Enrichment 120 场景，689.246s；cancel/owner/input-change 140 场景，
654.819s。合计 500 个真实进程场景，
无失败、无重试掩盖、无外部网络访问。包含新进程套件的一轮完整 Crawler 回归为
363 tests passed，150.211s。

| AC | 状态 | 主要证据 |
| --- | --- | --- |
| AC-WORK-01 / Crawler v2 | 通过 | Page/Chunk/Enrichment 六断点各 20 次；旧 owner 写入被 fence，唯一候选且终态收敛 |
| AC-WORK-03 / Crawler v2 | 通过 | 三类外部结果返回后 API cancel 各 20 次；持久化 job/claim fence 拒绝旧结果 |
| AC-WORK-04 / Crawler v2 | 通过 | 同 Worker 命中、replacement 冷启动、同 job retry/run 隔离、URL/正文变化与迟到旧结果均覆盖 |
| AC-DATA-04 / Crawler 子矩阵 | 通过（子矩阵） | 500 场景逐轮 integrity、外键、唯一性、claim 和状态机审计通过 |

### Dispatcher 真实进程故障矩阵

G3 已证明 API 立即发送和 Worker 排程发送的 at-most-once 结构与单轮故障语义；G5 在同一
Worker 排程入口上将六个边界各重复 20 次，并补充逐轮 API 可用性和数据库审计。边界映射为：
`delivery.before_claim`（claim 前）、`delivery.claim_committed`（claim 后）、
`delivery.before_smtp`（外部调用前）、`delivery.smtp_accepted`（外部调用返回且服务端已接受
DATA）、`delivery.before_final_commit`（commit 前）和 `delivery.after_final_commit`
（commit 后）。

每轮均以临时数据库、随机 API 端口和本地 STARTTLS fake SMTP 启动真实 API/Worker；停在
目标边界后 OS 强杀 Worker，再启动新 generation。`before_claim` 可由 replacement 安全发送
一次；从 claim commit 开始，无论 SMTP 是否实际收到 DATA，恢复都不会再次发送：SMTP 前
强杀收敛为 `assumed_sent_after_interruption` 且 DATA=0，SMTP 接受后强杀 DATA 恒为 1，
after-commit 保持 `smtp_accepted`。这严格保持“不要求用户确认、不依赖 Sent/IMAP/SMTP
回查、结果不确定即 assume-sent、永不自动重发”的产品取舍。

逐轮检查 API 在 Worker 消失期间仍为 ready、delivery attempt 恰好 1 条、EmailLog 恰好
1 条、最终操作日志恰好 1 条、attempt/task outcome 一致、SMTP DATA 接受数不超过 1、
`PRAGMA integrity_check = ok` 且 `foreign_key_check` 为空。

```text
cd backend
rtk env AUTO_EMAIL_SENDER_DISPATCHER_CHAOS_REPETITIONS=20 \
  uv run --no-sync python -m unittest -v \
  test.test_email_delivery_process_safety.EmailDeliveryProcessSafetyTests.test_worker_scheduled_send_kill_matrix_never_accepts_data_twice
```

最新正式复跑结果：1 test passed，462.122s；内部为六断点各 20 次、共 120 个真实进程场景，0 失败，
0 次重复 SMTP DATA 接受。

| AC | 状态 | 主要证据 |
| --- | --- | --- |
| AC-WORK-01 / Dispatcher | 通过 | 六断点各 20 次；claim 前可安全发送一次，claim 后一律不重发，DATA 总数始终 ≤ 1 |
| AC-WORK-03 / Dispatcher | 通过 | claim CAS 覆盖排程变更/取消，持久化状态决定是否取得工作；既有并发/取消专项回归通过 |
| AC-DATA-04 / Dispatcher 子矩阵 | 通过（子矩阵） | 120 场景逐轮 integrity、外键、attempt/log 唯一性与状态机审计通过 |

### Worker 连续强杀/替换 200 次与资源审计

新增跨平台 `psutil` 开发依赖和真实进程压力测试。测试保持同一个 API/runtime id 和同一个
Crawler Page 工作项，连续创建 200 个唯一 Worker generation；每一代都在 claim 已提交、
HTTP 尚未调用时暂停并由 OS 强杀，确认 PID 已消失后把旧 lease 推到未来 365 天，再启动下一代。
每轮通过真实 API PATCH runtime settings 产生同步写、检查 API ready，并审计恰好一个
processing owner、owner 与 PID 唯一、attempt 单调递增、无候选提前落库、integrity 与外键。
第 201 个无 fault Worker 最终只执行一次 HTTP 和一次 LLM，提交一个 canonical candidate，
工作项/run/job 正常收敛，attempt_count 精确为 201。

资源采样覆盖常驻 API 及每个暂停中的 Worker：RSS、进程句柄/FD、打开文件、SQLite
DB/WAL/SHM FD、INET 连接、runtime status 文件数与大小、生产日志目录总量。门禁同时限制
峰值、终值和线性回归斜率，不以“最终值偶然较低”替代趋势检查。

```text
cd backend
rtk env AUTO_EMAIL_SENDER_WORKER_RESTART_REPETITIONS=200 \
  uv run --no-sync python -m unittest -v \
  test.test_crawler_process_safety.CrawlerProcessSafetyTests.test_worker_restart_stress_preserves_one_owner_and_bounded_resources
```

最新 generation-recovery 正式复跑结果：1 test passed，131.219s；此前同参数两轮也通过，
126.393s 与 125.592s。

| 指标 | 基线/范围 | 峰值或终值 | 每次 restart 斜率 |
| --- | ---: | ---: | ---: |
| API RSS | 193,445,888 B | peak 194,052,096 B；final 117,669,888 B | -530,751.699 B |
| API handles | 11 | peak/final 11 | 0 |
| API SQLite FD | — | peak 3 | — |
| API INET connections | — | peak 1（监听 socket） | — |
| Worker RSS（200 个独立进程） | 127,434,752–130,646,016 B | max 130,646,016 B | 不适用；每代新进程 |
| Worker handles | 19–20 | 20 | 不适用；每代新进程 |
| Worker SQLite FD | 12–13 | 13 | 不适用；每代新进程 |
| runtime 普通文件 | baseline 1 | final 2（新增当前 worker status） | 无 generation 累积 |
| 生产日志 | 0 B 起 | final 91,588 B | 低于 5 MiB 门禁 |

200 个 Worker PID 和 200 个持久化 claim owner 全部唯一；旧 PID 均在下一代启动前消失，
API handles 完全不变，RSS 回归斜率为负，没有双 owner、永久 processing、负计数、异常终态、
status 临时文件或资源单调增长。

| AC | 状态 | 主要证据 |
| --- | --- | --- |
| AC-WORK-02 | 通过 | 连续强杀/替换 200 次后第 201 代收敛；200 PID/owner 唯一，无永久 running 或状态损坏 |
| AC-DATA-04 / restart 子矩阵 | 通过（子矩阵） | 每轮 integrity、外键、claim/attempt/候选不变量通过 |
| AC-SOAK-04 / 短时压力子项 | 部分通过 | 200 次 restart 的 RSS/句柄/连接/status/log 无单调增长；24h/8h 最终冻结包长稳仍待 G6 |

### SQLite 文件系统故障

真实进程套件覆盖六类场景：非 WAL 数据库、API/Worker 写锁竞争、损坏数据库、只读目录、
磁盘写满，以及分别无法创建 `-wal` / `-shm` 侧车文件。

- 磁盘满不是 mock SQLite 调用：POSIX 测试子进程以 `RLIMIT_FSIZE=4096` 限制新文件扩展，
  API 在 cold recovery 删除过期 operation log 时收到真实 SQLite 写失败。原数据库 SHA-256
  保持不变，startup error 的 `updated_at` 一秒后仍不变，证明没有破坏性紧密重试。
- `-wal` 与 `-shm` 路径分别预置为目录，SQLite 的真实侧车创建失败；两种情况下 API 都进入
  明确 error，Worker 不能 ready，数据库摘要保持不变。
- 损坏数据库不被覆盖且不生成 schema backup；只读数据库不产生 WAL/SHM；写锁竞争发生在
  Worker 已完成 generation recovery 之后，相关子系统进入 degraded，释放锁后同一 PID 自动
  恢复 healthy，API 读写和 integrity/foreign-key 检查恢复正常。
- 每一种可解除的故障都在移除限制后用同一数据库重新启动 API + Worker，并通过
  `PRAGMA integrity_check` 与 `foreign_key_check`，不是仅验证“能够报错”。

```text
cd backend
rtk uv run --no-sync python -m unittest -v test.test_split_sqlite_faults
```

结果：6 tests passed，31.599s。

| AC | 状态 | 主要证据 |
| --- | --- | --- |
| AC-DATA-02 | 通过 | 只读、真实磁盘满、WAL/SHM 创建失败均明确报错；危险的部分 Worker 不启动；原库不被循环改写 |
| AC-DATA-03 | 通过 | 损坏库 SHA-256 全程不变，无自动覆盖、无误导备份、Worker 不启动 |
| AC-DATA-04 / SQLite 子矩阵 | 通过（子矩阵） | 解除故障后同库 integrity 与外键检查通过 |

### HTTP/LLM 与 IMAP 网络断开/恢复

- Crawler 真实 Worker 启动前关闭已分配端口上的 fake HTTP 与 fake OpenAI-compatible LLM。
  第一次 Page 尝试在 HTTP 连接失败后持久化 `failed_retryable` 并上报 crawler subsystem
  degraded；HTTP 恢复后第二次尝试到达页面但在 LLM 连接失败，再次安全退避；LLM 恢复后
  第三次尝试提交唯一候选，清除终态 `last_error`，Worker 回到 healthy。
- IMAP incremental 使用同一端口执行关闭/恢复。断网期间 cursor 保持 10、received log 为
  0、identity claim 被释放，IMAP subsystem 为 degraded；恢复后生产 `imaplib` 客户端把 cursor
  单调推进到 11，唯一落一封 received log，claim 清空并回到 healthy。
- 两个场景中 Worker PID/generation 均未变化，API 始终 ready，并在断网期间真实完成一次
  runtime-settings GET/PATCH；没有进程级 restart storm，也没有游标、任务状态或唯一性损坏。
- Crawler/IMAP 只在持久化安全的 retry/error/cursor 状态后向 RuntimeManager 上报本轮失败；
  直接同步调用的既有返回合同保持不变。SMTP 继续沿用 G3 证据：连接建立失败属于明确
  pre-submission failure；DATA 开始后的响应丢失一律 assume-sent，绝不因网络恢复自动重发。

专项结果：Crawler network recovery 1 test passed，24.231s；IMAP network recovery 1 test
passed，10.896s。随后 Crawler 120、IMAP/并发 122、运行时/角色/SQLite 60 项领域回归全部
通过，所有服务均为临时目录和 loopback fake server，没有访问真实外部网络或邮箱。

| AC | 状态 | 主要证据 |
| --- | --- | --- |
| AC-LIFE-05 / network | 通过 | HTTP、LLM、IMAP 断开时 subsystem degraded，API 可读写且 Worker 不重启；恢复后自动 healthy |
| AC-WORK-04 / network cache | 通过 | HTTP 成功/LLM 失败后的 retry 不提交半成品，恢复后唯一候选且旧错误清除 |
| AC-DATA-04 / network 子矩阵 | 通过（子矩阵） | IMAP cursor、claim、唯一日志及 Crawler 状态机/integrity 通过 |

### 时间跳变、上一稳定版升级与 combined 回退

时间与兼容专项使用真实 API/Worker/combined 进程、真实 SQLite 文件和本地 fake SMTP，精确
命令为：

```text
cd backend
rtk uv run --no-sync python -m unittest -v \
  test.test_previous_stable_split_upgrade.PreviousStableSplitUpgradeTests.test_idle_queued_running_leased_and_sending_snapshot_upgrades_safely \
  test.test_split_combined_compatibility.SplitCombinedCompatibilityTests.test_combined_fallback_reads_split_database_and_processes_safe_task \
  test.test_email_delivery_process_safety.EmailDeliveryProcessSafetyTests.test_sleep_wake_and_clock_jumps_send_scheduled_mail_once \
  test.test_backend_roles.BackendRoleRealProcessTests.test_worker_restart_after_clock_rollback_preserves_api_manual_run \
  test.test_imap_sync_runtime.ImapSyncRuntimeTestCase.test_worker_generation_recovery_preserves_live_api_full_sync \
  test.test_batch_task_dispatch_schedule.BatchTaskDispatchScheduleTests.test_live_delivery_owner_is_not_recovered_by_wall_clock_age \
  test.test_batch_task_dispatch_schedule.BatchTaskDispatchScheduleTests.test_worker_generation_recovery_never_touches_live_sending_attempt
```

最新复跑结果：7 tests passed，21.149s；此前同组正式运行 7 tests passed，20.184s。覆盖证据：

- 上一稳定 revision `20260808_crawl_llm_snapshot` 的 idle、queued、running/leased 和 legacy
  sending 状态升级到当前 head；迁移前 schema backup 恰好一份、可读，中文/空格材料文件
  SHA-256 不变，升级后 integrity、外键和 claim 一致性通过。legacy sending 保守收敛为
  assume-sent，不进入可重发状态。
- split API/Worker 写入运行时设置后完整退出；在静止的同版本数据库中植入无歧义逾期任务，
  combined 能读取设置并按既有安全语义处理；再切回 split 后数据、状态与外键保持一致。
- 真实 Worker `SIGSTOP`/`SIGCONT` 模拟 sleep/wake：唤醒后 heartbeat 由同一 PID 推进，未来排程
  未提前发送；测试时钟前跳两小时后 SMTP DATA 恰好接受一次，随后回跳两小时仍不重复发送。
- 墙钟回拨一年并替换 Worker 后，只恢复 Worker 所有的 Match item，API 手动 Match run 保持
  running；Worker generation recovery 同样保留 API full IMAP claim 及其 history 子 claim。
- 墙钟前跳和 Worker generation recovery 均不能回收仍有存活 owner 的 sending attempt；
  SMTP 返回后任务只提交一次 sent 终态。

| AC | 状态 | 主要证据 |
| --- | --- | --- |
| AC-DATA-05 | 通过 | sleep/wake、前跳/回跳、lease owner、排程邮件与 sending fencing 均通过 |
| AC-COMPAT-01 | 通过 | 上一稳定版五类状态、单一可读备份、材料哈希、head/integrity/外键 |
| AC-COMPAT-02 | 通过 | split → combined → split 使用同一数据库，非歧义任务继续处理且无重复 delivery attempt |

### G5 全量回归：首次失败、根因与收口

第一次统一全仓运行必须保留为失败证据，不能被后续成功覆盖：

```text
rtk proxy uv run --project backend --no-sync python scripts/quality/run_all_tests.py
```

Backend 收集 1846 tests，用时 521.618s，结果为 2 failures、2 errors、1 个既有 skip；CLI
196 passed，Frontend、Desktop、Website 均通过，全仓总用时 9m59s。四项失败均为旧测试未适配
新的 cold-start readiness/recovery 边界：

1. Agent 开放兼容模式测试在 recovery 完成前请求 `/api/ping`，得到预期 503。
2. stale database 自动迁移测试在后台迁移完成前请求 `/api/ping`，得到预期 503；迁移本身成功。
3. 两个 `StartupRuntimeTest` 的 `SimpleNamespace` session mock 未隔离新增的
   `recover_interrupted_worker_claims`。

修复只调整测试：前两项先轮询 `/startup-status=ready`，后两项 mock 并验证新 recovery 参数；
readiness middleware 和生产 recovery 均未削弱。四项加整个 `test_desktop_runtime` 定向复跑
22 tests passed，3.362s。

随后第一次 Backend 全量复跑仍不是成功：1846 tests，554.642s，2 errors、1 skip。

- `test_batch_draft_total_timeout_marks_only_claimed_item_failed` 用 10ms 外层超时驱动真实
  `generate_task_draft`，全仓负载下可能在到达 fake LLM 前取消 SQLite 操作，测试自身制造了
  未归还连接和 `database is locked`。该用例改为直接挂起 scheduler 的 generation task，
  只验证总超时/claim 终态；相邻的真实 LLM 取消与迟到结果用例继续覆盖数据库和 fencing。
- combined 回退用例最初只有 10 秒观测窗。增强失败诊断后，后续 Backend 全量在 541.346s
  结束为 1 failure、1 skip，并明确记录任务实际已变成 `send_failed`：测试在 split Worker 仍
  存活时直接植入逾期任务，慢速主机超过其 30 秒 dispatcher 周期后，旧 Worker 会抢先尝试
  SMTP。修复为先完整停止 split API/Worker，再向静止数据库植入专供 combined 处理的夹具；
  观测窗扩至 30 秒并保留任务状态及子进程日志诊断。

调整 mock 边界后的首次定向运行还暴露一次测试 double 位置参数签名错误；修正为接受
`*args, **kwargs` 后，批量草稿整套与 combined 兼容共 29 tests passed，6.857s。正式压力复跑
将批量超时重复 100 次、combined 回退重复 20 次，共 120 tests passed，115.541s，无重试
掩盖。最终 Backend 全量结果：1846 tests passed、1 existing skip，511.383s。

最后再次运行统一全仓入口，正式结果：

| 工作区 | 结果 | 用时 |
| --- | --- | ---: |
| Backend | 1846 passed | 8m37s |
| CLI | 196 passed | 19s |
| Frontend | passed | 16s |
| Desktop | passed | 38s |
| Website | passed | <1s |
| 合计 | 0 failures | 9m51s |

### G5 AC 汇总

| AC | 状态 | 主要证据 |
| --- | --- | --- |
| AC-WORK-01 | 通过 | Batch 160、Match 140、IMAP 280、Crawler 500、Dispatcher 120 个真实进程场景；每断点至少 20 次 |
| AC-WORK-02 | 通过 | Worker 连续强杀 200 次及第 201 代收敛；PID/owner 唯一、资源有界 |
| AC-WORK-03 | 通过 | Batch pause/stop、Match cancel、Crawler cancel/owner/input-change 与持久化 fence |
| AC-WORK-04 | 通过 | Crawler cache 冷/热、retry/run/input 隔离及网络恢复；旧结果不能提交 |
| AC-DATA-01 | 待 G6 时长认证 | 离散并发和故障均通过，但尚未取得单次连续至少 8 小时证据 |
| AC-DATA-02 | 通过 | 只读、真实 disk full、WAL/SHM 创建失败与锁竞争均明确失败/降级并可恢复 |
| AC-DATA-03 | 通过 | 损坏库保留原 SHA-256，不覆盖、不启动 Worker |
| AC-DATA-04 | 通过 | 全部 1200 个高重复 workload 场景及 restart/SQLite/network 子矩阵逐轮审计 |
| AC-DATA-05 | 通过 | sleep/wake、墙钟前后跳、lease 与排程发送专项 7/7 通过 |
| AC-COMPAT-01 | 通过 | 上一稳定版状态快照、迁移前备份、材料哈希与 current head |
| AC-COMPAT-02 | 通过 | combined 回退读写 split 同版本数据库，静止夹具保证唯一 owner |
| AC-COMPAT-03 | 通过 | 旧 CLI descriptor 合同保持；新 CLI 可读取可选 Worker 信息，CLI 196/196 |
| AC-COMPAT-04 | 通过 | Worker 不继承 UI/Agent token；status、日志与 IPC 错误均脱敏 |

G5 的实现、离散 chaos、兼容和全仓回归已收口；唯一未关闭项是 AC-DATA-01 的连续 8 小时
时长门禁。它不得由累计短测时长替代，将与 G6 两平台 8 小时 seeded chaos 一并认证。

## G6：冻结包、真实安装升级与长稳认证

状态：执行中（2026-08-10）。默认桌面模式仍为 `combined`；本节全部正式门禁通过前不进入
G7，也不创建 tag、push 或发布版本。

### 已实现的认证入口与证据合同

- `scripts/quality/packaged-runtime-qa.py` 直接启动真实 packaged Electron，使用带中文、空格和
  Ω 的隔离 userData；报告不记录 Agent token。正式报告要求 clean committed SHA、仓库版本、
  候选 manifest/run ID、当前安装包 SHA-256 和安装后 artifact tree SHA-256，结束时重新计算
  manifest、当前/旧包与 artifact tree 并要求完全不变。manifest 必须把平台资产名、大小、
  SHA-256、版本和 workflow run 绑定到同一最终 SHA。
- lifecycle 覆盖 split 身份、Worker 无监听端口、认证 API 读写、第二实例、原生系统
  sleep/wake、Worker-only restart、API whole-group restart、真实 Playwright 后代清理、强杀后
  重启、combined 同库回退和快速退出。
- normal-soak 与 seeded-chaos 每一轮真实创建并等待 Dispatcher、IMAP incremental、IMAP
  history、Batch Draft、Matching、Crawler 六类工作，不允许仅靠 API 空转关闭 AC-SOAK-01。
  所有外部依赖都是 loopback fake SMTP/IMAP/LLM/HTTP；network flap 明确排除 SMTP，避免
  测试设计诱导不确定发送重试。
- SMTP 语义保持不变：结果不确定即 assume-sent；不要求用户确认，不依赖 Sent/IMAP/SMTP
  回查，永不自动重发。每轮审计 SMTP DATA ≤ 1、attempt/log 唯一性和终态一致；极端故障
  接受少发以规避重复发送。
- seeded chaos 按 seed 随机执行 Worker kill、API kill、HTTP/IMAP network flap、SQLite
  write lock、Worker suspend/resume、墙钟前跳/回跳，并执行一次原生系统 sleep/wake。kill 前
  捕获旧角色完整进程树，replacement 后要求旧 PID（含 Playwright 后代）全部退出。
- 原生 macOS 路径使用 `pmset relative wake` + `pmset sleepnow`，验证 sleep/wake counter；
  原生 Windows 路径使用 resume-capable waitable timer + `SetSuspendState`，验证 Kernel-Power
  42 与 Power-Troubleshooter 1。两平台唤醒后 runtime id、API PID、Worker PID 必须不变，
  heartbeat 推进且 API 可继续写。
- 资源采样与自动门禁覆盖 RSS、句柄/FD、INET/SQLite 文件、child count、Playwright 后代、
  runtime 文件数、status/log 大小及趋势。24h/8h 最小时长同时要求请求值、单调时钟实测值和
  墙钟实测值达到标准。
- `seed-previous-packaged-upgrade.py` 通过上一稳定版真实 Agent API 写设置和导师，再写入真实
  identity material 记录及中文/空格/Ω 文件。runner 从当前 SHA 最新可达的 `v*` tag 推导
  期望旧版本，并把旧 DMG/NSIS 摘要与 manifest 互相核对。Windows 在 host、guest 和 Python
  driver 三层核对旧 NSIS；macOS 在挂载前、driver 和报告三层核对旧 DMG。development smoke
  可自动固定现场旧包摘要，但该值不构成正式公开资产来源证据。
- 当前包启动后，升级库 revision 必须精确等于仓库唯一 Alembic head；schema 变化时必须
  出现旧 manifest 备份清单之外的新备份，且该备份可读、integrity=ok、revision 等于旧库。
- Windows runner 用同一含非 ASCII 路径先静默安装旧 NSIS、seed，再用当前 NSIS 覆盖安装；
  已修正受支持 Windows PowerShell 5.1 VM 没有 `ProcessStartInfo.ArgumentList` 的问题，改用
  跨 PowerShell 5.1 主机兼容的 `Arguments/EnvironmentVariables`，并在任何失败的 `finally`
  中清理 QA 进程。
  host 正式入口同时拒绝 tracked、staged 和 untracked 改动，避免脏工作树生成伪 clean-SHA 证据。
  正式入口还要求候选 workflow 的当前 NSIS 及候选清单摘要；host、guest 共享副本、guest-local
  NTFS 副本和 Python driver 场景前后均核对，VM 同 SHA 重建只保留为打包合同，不能替代确切
  候选资产。
- macOS runner 从旧/新 DMG 分别只读挂载并 `ditto` 到同一安装路径；所有正式场景都必须来自
  DMG。lifecycle 正式升级要求专用测试账户；原生 sleep 前要求现有 sudo ticket，缺失即失败，
  不保存密码或扩大权限。
- 为避免在整套构建后才发现确定性安装缺陷，runner 增加两个明确非认证层级：
  `harness-rehearsal` 可使用失效包做故意中断/立即重跑，并验证 Windows stale 注册表、进程与
  timeout 或 macOS DMG mount trap；`candidate-admission` 绑定新候选 manifest/run/SHA/摘要，
  在源码全套和长稳前直接跑真实覆盖升级、lifecycle、原生 sleep/wake、Beta 诊断 ZIP、卸载与
  重装。两者报告均固定 `certification_eligible=false`，不关闭本节任何正式 AC。

### 当前开发验证

以下均为当前未提交工作树上的开发验证，不可记作最终发布证据：

```text
rtk bash scripts/build/build-backend.sh --clean
```

macOS 26.5.2 arm64、Python 3.12.13、PyInstaller 6.20.0；真实冻结后端依次输出：
`role=api ok`、`role=worker ok`、`role=combined ok`、`packaged document self-check ok`。构建和
全部 self-check 约 38 秒完成。

```text
cd backend
rtk uv run --project . --no-sync python -m unittest \
  test.test_packaged_runtime_qa \
  test.test_previous_stable_split_upgrade \
  test.test_backend_build_script \
  test.test_desktop_runtime \
  test.test_backend_roles
```

结果：56 tests passed，50.390s。加入候选 manifest 绑定合同后，packaged QA 聚焦套件最新为
15 tests passed、23.205s；其中两轮真实六类负载包含一次 HTTP + 双 IMAP 断网、持久化失败观测和同 Worker
恢复。

```text
cd desktop
rtk npm run typecheck
rtk npm run test
```

结果：typecheck 通过；Desktop 最新为 196 passed、1 skipped，37.44s。第一次 Desktop 全量曾为
191 passed、2 failed、1 skipped：两项失败都来自 import-boundary 测试仍假定旧 `main.ts`
只有静态 bootstrap import。修正测试使其识别动态 `import()`，并明确要求 QA gate 先于
bootstrap 后，聚焦 4/4 及全量均通过；首次失败记录保留。

完成摘要链路和 runner 收紧后，再次执行统一全仓入口：

```text
rtk proxy uv run --project backend --no-sync python scripts/quality/run_all_tests.py
```

开发工作树 SHA 为 `6e06be9bfeae11b78eae78096782d84b3176c931`（dirty，仅用于开发验证）；
macOS arm64、Node 24.18.0、Python 3.12.13、uv 0.11.26。结果：Backend 1861/1861 passed
（8m54s）、CLI 196/196 passed（18s）、Frontend passed（14s）、Desktop passed（38s）、Website
passed（<1s），合计 10m06s、0 failures。

完成候选资产 manifest/run ID 绑定、macOS 旧包摘要链和 Windows PowerShell 5.1 实机兼容
验证后，再次从同一 dirty 开发工作树执行上述统一入口。结果：Backend 1862/1862 passed
（8m50s）、CLI 196/196 passed（18s）、Frontend passed（14s）、Desktop passed（38s）、Website
passed（<1s），合计 10m02s、0 failures。两轮均为完整独立运行；第二轮不是对失败项的选择性
重试。它证明新增候选资产来源合同没有破坏现有工作区，但仍不替代 clean committed SHA 上的
正式候选包证据。

发布链附加门禁同轮通过：Desktop build、Frontend lint/build、release notes 2/2、frontend
desktop packaging 2/2、desktop packaging/QA isolation 35/35，以及 POSIX
`prepare-release.test.sh`、`release-script.test.sh`。候选 manifest 单资产绑定新增 Windows/
macOS、错 SHA 与篡改拒绝合同，4/4 通过。Windows PowerShell 完整流程仍只能由 VM 实跑关闭。

另有一次聚焦 Python 命令在仓库根使用了错误模块工作目录，直接报 `No module named test`，
没有执行产品测试；改为在 `backend` 目录使用 `--project .` 后，同一套件通过。该命令错误不
计作产品失败，但保留在执行记录中。

一次过宽的 Ruff 调用把未修改的既有测试基线也纳入并失败；随后对本 Goal 的全部 84 个
changed Python 文件精确运行，首先发现 CLI runtime/test 的 7 项类型异常、import 顺序和
nested context lint。修正为对非法类型抛 `TypeError` 并由用户可见错误边界统一捕获，同时只做
等价测试整理；CLI runtime 20/20 通过，84 个 changed Python 文件 Ruff 全部通过。Python
compileall、`git diff --check`、两个 Bash runner 的 `bash -n`、macOS runner/两个 Python
driver 的 `--help` 均通过。

随后在真实 Parallels Windows 11、Windows PowerShell 5.1.26100.8875 上，把当前
`run-windows-release-qa.ps1` 通过共享目录传入并核对 SHA-256
`798142a72281f199a53ac901cc1db46df9faf2b48ea083099b773e7f55a4a285`；PowerShell parser
0 errors，AST 同时确认旧包、候选包、候选 manifest/run ID 六个来源参数及 driver 传参存在。
`.NET Framework 4.0.30319.42000` 实测 `ArgumentList=False`、`Environment=True`，
兼容实现使用的 `Arguments/EnvironmentVariables` 成功保留中文、空格、Ω 参数/环境并启动
子进程。首个环境探针错误假定 `Environment` 也必须不存在而自行失败；放宽该探针假设后通过，
产品脚本没有为探针改行为。临时共享副本已删除，VM 已恢复为原先 stopped。此项只关闭语法和
API 兼容未知量；NSIS 覆盖安装、waitable timer、事件日志和完整 lifecycle 仍必须由正式 VM
认证关闭。

### 最终冻结 SHA 的正式命令

Windows 在宿主 Mac 上用上一稳定版真实 NSIS，一次连续运行 lifecycle、24h normal soak 和
8h seeded chaos：

```text
rtk bash scripts/quality/run-windows-vm-release-qa.sh \
  --candidate-installer /绝对路径/AutoEmailSender-Setup-<当前版本>.exe \
  --candidate-installer-sha256 <候选清单中的64位SHA-256> \
  --candidate-manifest /绝对路径/release-candidate.json \
  --candidate-run-id <候选workflow run ID> \
  --previous-installer /绝对路径/AutoEmailSender-Setup-<上一稳定版>.exe \
  --previous-installer-sha256 <上一稳定版公开NSIS的64位SHA-256> \
  --normal-soak \
  --seeded-chaos \
  --seed 20260810
```

macOS 三次命令必须使用同一个当前 DMG；报告中的 `package_sha256` 必须一致。运行含原生
sleep 的 lifecycle/seeded-chaos 前，操作员先在专用测试账户执行一次 `sudo -v`：

```text
rtk bash scripts/quality/run-macos-packaged-qa.sh \
  --scenario lifecycle --certification --expected-revision <最终40位SHA> \
  --dmg /绝对路径/AutoEmailSender-<当前版本>-arm64.dmg \
  --expected-dmg-sha256 <候选清单中的64位SHA-256> \
  --candidate-manifest /绝对路径/release-candidate.json \
  --candidate-run-id <候选workflow run ID> \
  --previous-dmg /绝对路径/AutoEmailSender-<上一稳定版>-arm64.dmg \
  --expected-previous-dmg-sha256 <上一稳定版公开DMG的64位SHA-256> \
  --dedicated-test-account

rtk bash scripts/quality/run-macos-packaged-qa.sh \
  --scenario normal-soak --certification --expected-revision <最终40位SHA> \
  --dmg /绝对路径/AutoEmailSender-<当前版本>-arm64.dmg \
  --expected-dmg-sha256 <同一个候选SHA-256> \
  --candidate-manifest /绝对路径/release-candidate.json \
  --candidate-run-id <同一个候选workflow run ID>

rtk bash scripts/quality/run-macos-packaged-qa.sh \
  --scenario seeded-chaos --certification --expected-revision <最终40位SHA> \
  --dmg /绝对路径/AutoEmailSender-<当前版本>-arm64.dmg \
  --expected-dmg-sha256 <同一个候选SHA-256> \
  --candidate-manifest /绝对路径/release-candidate.json \
  --candidate-run-id <同一个候选workflow run ID> \
  --seed 20260810
```

截至本计划编写时，上一稳定版 v2.5.4 的公开资产摘要为：Windows NSIS
`245aadcdf63ccae80913ede6a4cda9571884f83da9f23b957c724a6fb3b15d21`，macOS arm64 DMG
`c67fe772766751798163b16a985a9e3e97893c4ad906cde161c4e85bc6c9447b`。正式 runner 会在宿主、
guest/driver 和报告层核对这些输入；未来上一稳定版变化时必须换成新 Release 的可信摘要。

正式报告必须记录两平台 OS/架构、工具链、开始/结束时间、实际双时钟时长、seed/完整轨迹、
旧/新包摘要、artifact tree 摘要、数据库审计、资源曲线和所有首次失败。任一失败修复后必须
用原 seed 重放，并重新完成受影响的整段长稳。

### 当前未关闭项

- 当前 Desktop 版本仍为 `2.5.4`，最新可达稳定 tag 也是 `v2.5.4`。升级 driver 会正确拒绝
  “旧版 2.5.4 → 当前 2.5.4”的同版本伪升级；正式认证前必须由所有者确定下一版本并形成最终
  version commit，本 Goal 不擅自决定版本号。
- 本机未配置 `SPARKLE_PUBLIC_ED_KEY`（只检查变量是否存在，未读取或输出值），因此不能从
  当前源码构建可信 macOS bundle/DMG。
- 尚未提供候选 workflow 的当前 Windows NSIS、`release-candidate.json`、run ID 与摘要；同
  SHA 的本地重建不再被正式 runner 接受为 lifecycle 资产。
- 尚未提供上一稳定版 DMG 和 Windows NSIS；无法做真实原地升级。
- Windows PowerShell 5.1 语法和 `ProcessStartInfo` 兼容探针已通过；waitable timer、事件日志、
  NSIS 覆盖安装和完整进程生命周期仍必须在正式 VM 流程中验证。
- 工作树尚未提交且包含本 Goal 的大量改动；正式 runner 会拒绝这种状态。24h/8h、两平台
  lifecycle 与 clean-SHA 证据均仍为未通过。
- 在上述正式项全部通过前，AC-DATA-01、AC-PKG-02～05、AC-SOAK-01～05 均保持未关闭，
  不能把当前短时开发 smoke 累计替代。

## AC 证据矩阵

| AC | 当前状态 | 关闭所需证据 |
| --- | --- | --- |
| AC-DATA-01 | 未通过 | 两平台各自单次连续 8h seeded chaos 的六类真实负载与数据库审计 |
| AC-COMPAT-01 / packaged | 自动化完成，正式未通过 | 两平台上一稳定版真实 DMG/NSIS 原地升级、current head、新备份 revision、材料哈希 |
| AC-PKG-01 | macOS arm64 开发验证通过，正式未通过 | 最终 clean SHA 的 Windows x64 与 macOS arm64 冻结产物三 role self-check |
| AC-PKG-02 | 未通过 | Windows NSIS lifecycle、原生 sleep/wake、升级与卸载报告 |
| AC-PKG-03 | 未通过 | macOS DMG 对等 lifecycle、原生 sleep/wake、升级与卸载模拟报告 |
| AC-PKG-04 | 自动化完成，正式未通过 | 两平台 Electron/API/Worker/Playwright 完整进程树及锁释放证据 |
| AC-PKG-05 | 自动化完成，正式未通过 | 两平台中文、空格、Ω 安装与 userData 路径实跑证据 |
| AC-SOAK-01 | 未通过 | Windows 与 macOS 各一次实际双时钟 ≥24h normal soak，六类负载全覆盖 |
| AC-SOAK-02 | 未通过 | Windows 与 macOS 各一次实际双时钟 ≥8h seeded chaos，seed/轨迹完整 |
| AC-SOAK-03 | 未通过 | 正式长稳 SMTP DATA≤1、状态/数据库审计、零孤儿和零未解释退出 |
| AC-SOAK-04 | 短压通过，正式未通过 | 两平台长稳资源采样、趋势和自动阈值全部通过 |
| AC-SOAK-05 | 未触发关闭 | 正式失败若出现，必须保留首次证据、原 seed 重放并重跑受影响整段 |

没有正式证据的条目保持未通过，不以实现说明、本地源码测试或累计短时 smoke 代替。
