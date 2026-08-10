# 桌面 API + Worker 通用 Beta 验证 Goal 验收记录

- 状态：执行中（B0～B1 已完成，B2 进行中）
- 计划：[`desktop-api-worker-beta-goal-plan.md`](../architecture/desktop-api-worker-beta-goal-plan.md)
- 前置证据：[`desktop_api_worker_goal_acceptance.md`](./desktop_api_worker_goal_acceptance.md)
- 建立日期：2026-08-10

## 起点

- 原 Goal 已取消；G0～G5 已完成证据不重跑、不改写，G6 开发验证证据继续作为前置输入。
- B0 开始时为 detached HEAD `6e06be9bfeae11b78eae78096782d84b3176c931`，工作区包含
  前置双进程改动。
- B0 开始时本地 `master` 多 5 个提交；该风险已通过前置快照和语义化合并解除。
- 当前 Desktop 版本和最新稳定版均为 `2.5.4`，默认模式为 `combined`。
- 尚未授权 push、远端 workflow、tag、GitHub Prerelease 或 master 合并。

## 阶段证据

| 阶段 | 状态 | 证据 |
| --- | --- | --- |
| B0：分支与 master 集成 | 已完成 | `e062f36`、`c51df44`、`4fe1bdf`；聚焦 291/291；修复后全仓 0 failures |
| B1：模式设置与安全重启 | 已完成 | Desktop 208/208；Frontend 956/956；后端聚焦 27/27；20 次切换；macOS 隔离 UI、初始启动失败及 group-restart 原生回退实测 |
| B2：本地诊断与 analyzer | 执行中 | — |
| B3：通用 prerelease 发布体系 | 待执行 | — |
| B4：完整与重复回归 | 待执行 | — |
| B5：Mac/Windows 内部 Beta | 待执行 | — |
| B6：远端与公开批准门 | 待批准 | — |
| B7：证据收口 | 待执行 | — |

## AC 证据矩阵

没有证据的条目保持未通过，不以实现说明、源码 smoke 或另一平台结果替代。

| AC 组 | 当前状态 | 关闭要求 |
| --- | --- | --- |
| AC-BRANCH | 部分通过 | AC-BRANCH-01/02 已通过；AC-BRANCH-03 随 B3 的通用 source branch/SHA 合同关闭 |
| AC-MODE | 已通过 | 原子设置、UI 当前/下次状态、20 次同库切换、发送窗口硬阻断、初始与运行中 split 故障原生回退 |
| AC-OBS | 未通过 | 本地有界记录、故障时导出、bundle schema 和 analyzer |
| AC-PRIV | 未通过 | allowlist、统一脱敏、canary 解包扫描和零上传路径 |
| AC-REL | 未通过 | Skill、脚本、workflow、候选 manifest 与恢复合同 |
| AC-ISO | 未通过 | Prerelease 非 Latest，稳定 feed/客户端完全隔离 |
| AC-BETA-QA | 未通过 | 双平台 exact-package lifecycle、2h normal、1h chaos |

## 首次失败与修复记录

所有首次失败、错误命令、环境问题、产品缺陷和修复后原场景重放都追加在此，不覆盖旧记录。

### B0：保护工作与合入 master

1. 将 detached/dirty 前置实现完整保护到本地分支 `beta/desktop-api-worker`，
   快照提交为 `e062f36c19d34db7b937b8367b80671b22dc9401`。
2. 合入 `origin/master@4b54b5897d796bdb496432d1e3d41b7a3c32f2d3`，合并提交为
   `c51df440b8c7cd827a9c346e552e6468cc0f48af`。本次分支名不会写入通用 Release Skill。
3. 三个文本冲突均做了语义合并：
   - `email_log.py`：同时保留 delivery-attempt 唯一索引和大规模查询索引；
   - IMAP sync：同时保留双进程 fault injection 与 master 的 chunked query；
   - workspace runtime：同时保留 fault injection 与 master 的分块查询工具。
4. 两侧 Alembic 分支使用 merge revision `20260810_merge_delivery_scale` 收敛，
   `alembic heads` 只有一个 head。Ruff、compileall 和高风险聚焦回归 291/291 通过。

### B0：首次合并后全仓回归

命令：

```bash
rtk proxy uv run --project backend --no-sync python scripts/quality/run_all_tests.py
```

- Backend：1893 tests，2 failures + 1 error，1 skip，约 10m20s。
- CLI：218/218；Frontend、Desktop、Website 均通过；全仓约 11m34s。
- 两个 failure 是 packaged upgrade 测试硬编码了合并前 Alembic head
  `20260809_delivery_at_most_once`，已改为唯一新 head `20260810_merge_delivery_scale`。
- 剩余 error 为
  `test_api_worker_lock_contention_degrades_and_recovers_without_corruption`。单独运行和部分
  前序组合会通过，但特定全套件顺序中将恢复窗口从 10 秒临时放宽到 30 秒
  仍失败，因此按产品缺陷处理，未当作普通负载波动。

### B0：SQLite maintenance 恢复缺陷

- 根因：master 新增的 `sqlite-maintenance` 正常周期为 21,600 秒。通用 Worker
  loop 失败后仍等待完整正常周期；若首轮在 SQLite 写锁下失败，Worker 会保持
  degraded 最长 6 小时。该子系统也未预先放入初始 health 列表，因此行为取决于
  启动时的协程调度顺序。
- 修复：`4fe1bdf20734bf53742eb0862dacecbed905a180` 为长周期循环增加独立的有界
  failure retry，SQLite maintenance 失败后 5 秒内重试，成功后恢复 6 小时正常周期；
  `sqlite-maintenance` 从 Worker 启动第一刻起就在 health 列表中。
- 测试：新增确定性 5s failure retry/health 合同测试；真实 API + Worker + SQLite
  锁冲突测试恢复为原本严格的 10 秒门限。

聚焦命令：

```bash
rtk uv run --no-sync python -m unittest \
  test.test_runtime_manager test.test_database_engine test.test_split_sqlite_faults
```

结果：27/27 通过，57.225s。

### B0：修复后完整全仓

命令：

```bash
rtk proxy uv run --project backend --no-sync python scripts/quality/run_all_tests.py --slowest 10
```

| 套件 | 结果 | 时长 |
| --- | --- | --- |
| Backend | 1894/1894 | 10m40s |
| CLI | 218/218 | 16s |
| Frontend | PASS | 19s |
| Desktop | PASS | 38s |
| Website | PASS | <1s |
| 合计 | 0 failures | 11m54s |

B0 完成。这是 master 集成与缺陷修复的基线证据；B4 仍会在 B1～B3 完成后
执行计划要求的连续两次全仓和 20 次 split 集成，不以本轮提前替代。

### B1：模式持久化、安全重启与回退实现

- Electron 在启动后端前读取 `<userData>/desktop/settings.json`。设置带 schema version，
  使用私有目录/文件权限、同目录临时文件、文件 `fsync` 和原子替换；文件缺失、超过
  64 KiB、未知 schema 或损坏时均安全忽略并保留明确警告。
- 模式优先级固定为命令行、开发/QA 环境变量、用户设置、发布通道默认值。稳定版默认
  `combined`，`alpha`/`beta`/`rc` 默认 `split`，显式用户选择跨版本保留。运行中的
  后端进程组重启不会中途改变拓扑，只有完整 Electron relaunch 才采用下次模式。
- “其他设置”只在桌面环境显示模式入口，准确展示当前运行和下次启动模式，并提供
  “保存，稍后重启”和“保存并安全重启”。命令行/环境覆盖会如实显示，且不会执行一个
  明知无法改变 effective mode 的重启。
- `GET /api/desktop/restart-safety` 只统计活跃工作。任何 `sending` 邮件行均硬阻断重启，
  第二次确认也不能绕过；其他可恢复租约要求一次确认；无活跃工作直接允许。Electron
  在真正 relaunch 前重新查询，随后走既有 Worker/API/Playwright 后代停止和 descriptor
  清理路径。
- 邮件发送的不确定结果继续遵守既定产品语义：不要求用户确认投递结果，不依赖 Sent/IMAP
  证据，不自动重发；发送与最终本地提交窗口只允许等待结束，不提供强制重启按钮。
- split 初始启动失败和运行中进程组重启失败均使用 Electron 原生对话框提供 combined
  回退；托盘也提供同一安全回退。回退会持久化 combined，并附加仅本次 relaunch 使用的
  命令行保险参数；不会静默掩盖 split 故障。

### B1：自动化证据

最后一轮实现回归：

```bash
cd desktop
rtk npm run typecheck
rtk npm run test
rtk env AUTO_EMAIL_SENDER_MODE_SWITCH_QA=1 npm run test -- backendModeSwitch.integration.test.ts

cd frontend
rtk npm run lint
rtk npm run test
rtk npm run build

cd backend
rtk uv run --no-sync ruff check \
  app/modules/system/restart_safety app/api/routers.py test/test_restart_safety.py
rtk uv run --no-sync python -m unittest \
  test.test_restart_safety \
  test.test_runtime_manager \
  test.test_split_sqlite_faults.SplitSQLiteFaultTests.test_api_worker_lock_contention_degrades_and_recovers_without_corruption \
  test.test_email_delivery_process_safety
```

| 检查 | 结果 |
| --- | --- |
| Desktop typecheck | 通过 |
| Desktop full | 29 files / 208 tests 通过；2 个环境门禁测试跳过 |
| Frontend lint / build | 通过 |
| Frontend full | 123 files / 956 tests 通过 |
| Backend Ruff | 通过 |
| Backend restart/runtime/SQLite/delivery 聚焦 | 27/27 通过，100.238s |
| combined↔split 同库切换 | 20 次切换、21 次启动通过，56.83s |

模式切换集成每次启动都会写入并读回同一数据库，停止后确认所有累计 API/Worker PID
均已退出、`runtime/worker.json` 已删除；最终 `integrity_check=ok`、外键错误 0、journal
mode 为 WAL。

### B1：macOS 隔离真机证据

- 环境：macOS 26.5.2（25F84），Apple Silicon arm64；源码开发构建；数据位于
  `mktemp` 创建的独立 `/tmp/auto-email-sender-b1-qa.*` userData，不含真实身份、邮箱、
  导师、模型或密钥。
- UI 实测稳定版本默认显示“当前运行：单进程兼容模式 / 下次启动：单进程兼容模式”。
  选择 split 并“保存，稍后重启”后，当前仍为 combined、下次准确变为 split；设置文件
  持久化 `backend_mode=split`。
- “保存并安全重启”真实完成 combined → split。重启后 UI 的当前/下次均为 split，
  Worker 使用独立 PID 且报告 healthy。随后真实完成 split → combined，Worker 状态文件
  被删除，旧 Worker PID 不再存在。
- 运行中故障：强杀 split API，并在其退出后占用原 API 端口，使整个进程组重启失败。
  Electron 原生窗口显示“系统服务重启失败，正在继续重试”，选择“使用兼容模式重启”后
  combined 在另一可用端口恢复；旧 Worker 已退出。
- 初始故障：将隔离 runtime 目录临时设为只读，并用命令行要求 split，使 split API 在
  写入启动身份前失败。无需依赖页面，Electron 原生窗口显示“系统准备失败”；选择兼容
  模式后 combined 正常启动。测试后恢复目录权限。
- 原生回退后的 UI 准确显示 combined，且明确标注本次命令行安全参数覆盖。退出应用后，
  隔离 `runtime/` 与 `agent/` 均为空，无匹配该 QA userData 的 Electron/Python 后代；
  数据库再次为 `integrity_check=ok`、外键错误 0、WAL。

以上是真实开发构建的 B1 行为证据，不替代 B5 的签名候选 DMG/NSIS、覆盖升级和长稳门禁。

### B1：首次失败与修复

1. 一次将 Desktop、Frontend、Backend 重测试并行运行时，既有
   `WorkspacePage draft saving` 测试触及 5 秒超时。原测试立即单独通过（192ms），随后
   Frontend 串行全套 956/956 通过，判定为并行资源竞争；未放宽超时或掩盖产品失败。
2. 代码审阅发现整个 split 进程组重新创建直接抛错时，catch 只发布 error 状态而没有调用
   原生回退。已补齐该路径和源码合同测试；随后真实“强杀 API + 占用原端口”重放通过。
3. 首次真机启动发现本机另一工作树已占用 Vite 5173，本工作树自动使用 5174，Electron
   因默认 URL 加载了旧前端。确认后端和数据库始终位于隔离 userData 后，停止该次实例并
   显式绑定 5174 重跑；这不是产品失败，也未触及日常数据。

B1 完成。B2 从本地有界记录器、诊断包 schema、后端失效 partial 导出和统一脱敏开始；
不得把现有普通诊断日志误当成 AC-OBS/AC-PRIV 已通过。
