# 桌面 API + Worker 通用 Beta 验证 Goal 验收记录

- 状态：执行中（用户已恢复同一 Goal；B0～B4 已完成，当前阶段为 B5）
- 当前 Goal ID：`019fe582-2dea-7e42-bd2e-684bae191421`
- 计划：[`desktop-api-worker-beta-goal-plan.md`](../architecture/desktop-api-worker-beta-goal-plan.md)
- 前置证据：[`desktop_api_worker_goal_acceptance.md`](./desktop_api_worker_goal_acceptance.md)
- 建立日期：2026-08-10

## 起点

- 原 Goal 已取消；G0～G5 已完成证据不重跑、不改写，G6 开发验证证据继续作为前置输入。
- B0 开始时为 detached HEAD `6e06be9bfeae11b78eae78096782d84b3176c931`，工作区包含
  前置双进程改动。
- B0 开始时本地 `master` 多 5 个提交；该风险已通过前置快照和语义化合并解除。
- B0 开始时 Desktop 版本和最新稳定版均为 `2.5.4`，默认模式为 `combined`；B5 已准备
  `2.6.0-beta.1`。首个远端 candidate run `31417575421` 已完成认证，但因 Windows QA
  传输层缺陷被拒绝；替代 run `31453411547` 也完成认证，但 Windows 正式 QA 又发现上一稳定版
  seed 合同缺陷；第二个替代 run `31464156897` 通过远端认证，但 Windows 正式 QA 在候选安装前
  发现上一失败 QA 遗留的卸载注册表会让 v2.5.4 安装器无限等待。三者均未形成 tag 或公开
  Release，且都不得用于后续发布。
- push 和 Certify dispatch 的三轮一次性授权已分别用于 `bd19519d`、`908dfa9` 与 `45c5d5f`。
  当前 QA 恢复提交 `b6381e1` 之后的新最终 SHA 仍需重新批准 push/dispatch；tag、GitHub
  Prerelease、master 合并和稳定版发布始终未获授权。

## Goal 恢复检查点

- Goal 系统在 2026-08-11 仍返回同一 Goal ID，并保留上次批准门产生的 `blocked` 标记；用户已
  明确恢复继续执行。另一个 Codex 进程意外关闭没有取消 Goal，因此继续沿用同一 Goal ID，不并行创建第二个 Goal。当前分支为
  `beta/desktop-api-worker`，当前产品代码收口提交为 `2123af5`，当前 QA failure-recovery 代码为
  `b6381e1`。
- B0/B1 已通过证据保持有效；只有受后续代码影响的检查才按 impact 重新执行，最终仍由 B4/B5
  的完整回归和 exact-package 证据统一收口。
- 当前允许继续本地 failure-recovery、测试和提交。`bd19519d`、`908dfa9`、`45c5d5f` 的 push
  与 Certify dispatch 均已按各自一次性授权完成；这些授权不覆盖 `b6381e1` 之后的新最终 SHA。
  仍未授权反向合回 `master`、新的 push/workflow、tag、GitHub Release 或稳定版发布。

## 阶段证据

| 阶段 | 状态 | 证据 |
| --- | --- | --- |
| B0：分支与 master 集成 | 已完成 | `e062f36`、`c51df44`、`4fe1bdf`；聚焦 291/291；修复后全仓 0 failures |
| B1：模式设置与安全重启 | 已完成 | Desktop 208/208；Frontend 956/956；后端聚焦 27/27；20 次切换；macOS 隔离 UI、初始启动失败及 group-restart 原生回退实测 |
| B2：本地诊断与 analyzer | 已完成 | Desktop 239/239；Frontend 完整 962/962、最终聚焦 18/18；Backend 115/115；analyzer 恶意包 10/10；最终 ZIP 跨语言 canary 7/7；audit 0 |
| B3：通用 prerelease 发布体系 | 已完成 | `17d5b41` 起实现；`fd7ecb5` 收口；通用双状态机、双平台入口、exact candidate、隔离/恢复合同和 Windows quick QA 通过 |
| B4：完整与重复回归 | 已完成 | 最新 `origin/master@2fcc431` 合入为 `e313811`；最终产品代码 `2123af5` 全仓连续 2 次 0 failures；split 集成连续 20/20 轮通过 |
| B5：Mac/Windows 内部 Beta | 执行中 | run `31464156897` 认证成功但 Windows 正式 QA 在候选安装前发现 stale QA 卸载注册表与安装器无界等待；`b6381e1` 已通过 Windows 5.1 聚焦探针与 exact-SHA quick QA，仍待新 candidate 的双平台正式证据 |
| B6：远端与公开批准门 | 待批准 | — |
| B7：证据收口 | 待执行 | — |

## AC 证据矩阵

没有证据的条目保持未通过，不以实现说明、源码 smoke 或另一平台结果替代。

| AC 组 | 当前状态 | 关闭要求 |
| --- | --- | --- |
| AC-BRANCH | 已通过 | 具名分支保护、最新 `origin/master@2fcc431` 通过 `e313811` 语义合入、通用 source branch + exact SHA 合同均有证据 |
| AC-MODE | 已通过 | 原子设置、UI 当前/下次状态、20 次同库切换、发送窗口硬阻断、初始与运行中 split 故障原生回退 |
| AC-OBS | 已通过 | Electron/API/Worker/combined 有界记录、六类工作摘要、API 宕机 partial ZIP、三类页面外导出入口和单包/多包 analyzer 均有自动化证据；B5 将用 exact package 重复故障注入 |
| AC-PRIV | 已通过 | allowlist、固定自由文本标签、最终 ZIP canary 零命中、恶意 ZIP 拒绝和无远程上传源码合同均通过 |
| AC-REL | 已通过 | Skill、POSIX/PowerShell、workflow、候选 manifest、immutable promote、supersede/withdraw 与稳定入口隔离合同均通过 |
| AC-ISO | 未通过 | 自动合同已实现；仍须用真实公开 Prerelease 证明非 Latest、稳定 feed 摘要不变及两平台 v2.5.4 客户端不可见 |
| AC-BETA-QA | 未通过 | 双平台 exact-package lifecycle、每平台 5 分钟 normal + 5 分钟 chaos 密集门禁 |

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

### B2：本地诊断、隐私门禁与 analyzer

#### 实现边界

- Electron 持有独立基础时间线和资源采样；API、Worker 与 combined 角色补充结构化时间线、
  资源及健康指标。记录器使用 14 天保留期、2 MiB 分片、64 KiB 单记录和 64 MiB 总上限，
  每 10 秒采样一次。活动分片同样计入总上限，记录、轮转、清理失败均不得抛入产品流程。
- “其他设置”只在诊断实际启用时显示本地占用、保留期、上限、1h/24h/7d/all 导出、问题标记
  和清空入口，并明确说明不会自动上传。稳定版及禁用状态不显示该区域。
- 托盘、split 启动失败和通用启动失败原生窗口均能在页面不可达时导出；API 宕机时仍生成
  `partial` ZIP，并明确列出后端缺失项。Electron exporter 在读前关闭并原子收口自身活动分片，
  随后可继续记录。
- ZIP 包含 manifest、时间线、资源样本、六类工作聚合、数据库健康、分类后的启动/后端错误摘要、
  `summary.json`、README 与 checksums。它不包含数据库、SQL、operation log 原文、业务正文、
  crash dump 或 crawler 原始调试内容。
- 本地 analyzer 支持单包和多包，在内存中校验并聚合 combined/split、平台、版本、安装、资源趋势、
  重启、SQLite 锁、积压及不变量；不解压到磁盘，也不执行包内内容。绝对路径、反斜杠、traversal、
  symlink/特殊文件、重复或未知 entry、未知 schema、checksum/CRC 错误、加密、未知压缩方法、
  声明大小不一致和 zip bomb 均为整批硬失败。

#### 隐私设计与零上传证明

- 结构化时间线和后端摘要使用逐字段 allowlist。用户的问题说明不保存脱敏后的原句，而只转换为
  内置故障关键词，例如 `background_stall`、`email_delivery`；无法识别的文本固定写为
  `[FREE_TEXT_OMITTED]`。形似内部标签的输入也只接受内置 tag，不能伪造任意文本旁路。
- 安装 ID 为本机随机 UUID，不来自硬件标识；只有用户主动导出 ZIP 时才离开诊断目录。
- Desktop 源码合同测试确认诊断模块没有远程上传 client，只请求既有认证的 loopback 相对路由
  `/api/diagnostics/beta-summary`；应用内文案和操作文档均明确“不自动上传”。
- 最终隐私门禁不是扫描中间对象：测试由实际 Electron `yazl` writer 写出 ZIP，再由 Python
  analyzer 校验 checksums/schema 并扫描 token、密码、邮箱、中文姓名、home 路径、远程 URL、
  非 loopback IP、机器名和正文 canary，7/7 通过且零命中。

#### 首次失败与修复

1. 首次最终 ZIP canary 扫描命中用户自由说明中的中文姓名“张三”。继续扩展姓名正则仍无法覆盖
   无标签姓名，因此改为根本不保存自由说明原文，只提取固定故障标签；同时增加伪造
   `[FREE_TEXT_OMITTED tags=...]` 的旁路测试。原 canary 场景重放后 7/7、零命中。
2. 清空诊断的并发测试发现，在途资源采样可能持有已删除活动分片的句柄并继续写入“幽灵文件”。
   资源采样现由单一队列串行化，clear、stop 和 export checkpoint 均先等待在途采样，再关闭 writer
   和删除/读取分片；原并发场景重放通过。
3. 安全审阅继续补上：安装/会话元数据有界且 `O_NOFOLLOW` 读取；Python SQLite 指标扫描同样
   `O_NOFOLLOW` + `fstat` + 大小限制；Windows 覆盖导出使用完整临时 ZIP 的可恢复替换，不写半包、
   不跟随目标 symlink；服务层串行化 export/clear/mark，避免并发保存对话框和存储竞争。
4. 增加 ZIP 依赖后 production audit 暴露既有 updater/builder 依赖链中的 `js-yaml 4.3.0` high
   漏洞。未使用会扩大修改面的 `npm audit fix`，只把 lockfile 收敛到修复版 `4.3.1`；最终
   `npm audit --omit=dev` 为 0 vulnerabilities。
5. 最终后端影响面回归第一次误写了不存在的模块 `test.test_migrations`，因此出现一项
   `ModuleNotFoundError`，不属于产品测试失败。改为真实模块 `test.test_runtime_settings_module` 后，
   第二组 52/52 通过；与第一组 63/63 合计 115/115。

#### 最终自动化证据

```bash
cd desktop
rtk npm run typecheck
rtk npm run test
rtk env AUTO_EMAIL_SENDER_BETA_DIAGNOSTICS_CROSS_QA=1 \
  npm run test -- betaDiagnosticsExporter.test.ts
rtk npm audit --omit=dev

cd frontend
rtk npm run lint
rtk npm run test -- OtherSettingsCard.test.tsx
rtk npm run build

cd backend
rtk uv run --no-sync ruff check <B2 变更的 Python 文件>
rtk uv run --no-sync python -m unittest \
  test.test_beta_diagnostics test.test_beta_diagnostics_analyzer \
  test.test_diagnostics_api test.test_sqlite_diagnostics \
  test.test_startup_runtime test.test_database_engine test.test_runtime_manager
rtk uv run --no-sync python -m unittest \
  test.test_desktop_runtime test.test_packaged_runtime_qa test.test_sqlite_runtime \
  test.test_migrations_runtime test.test_runtime_settings_api \
  test.test_migrated_database test.test_runtime_settings_module
rtk uv run --no-sync python -m compileall -q \
  app main.py ../scripts/quality/analyze_beta_diagnostics.py
rtk uv lock --check
```

| 检查 | 结果 |
| --- | --- |
| Desktop typecheck | 通过 |
| Desktop full | 35 files 通过、2 skipped；239 tests 通过、3 skipped |
| Electron ZIP → Python analyzer 跨语言门禁 | 7/7，所有 canary 零命中 |
| Frontend 完整回归（实现收口期间） | 123 files / 962 tests 通过 |
| Frontend 最终 lint / 设置页聚焦 / build | 通过；18/18；通过 |
| Backend Ruff / compileall / lock | 通过 |
| Backend B2 影响面 | 63/63 + 52/52 = 115/115 |
| analyzer 恶意包与批量聚合 | 10/10 |
| Desktop production dependency audit | 0 vulnerabilities |

B2 完成。这里关闭 AC-OBS 和 AC-PRIV 的实现与自动化门禁；B5 仍须在两平台 exact package 上
重复页面外导出、强杀、API 不可用、磁盘/权限故障和最终 ZIP canary，不能用本节代替真实候选证据。

### B3：通用 prerelease 双状态机与 Windows 收口

#### 通用发布合同

- `17d5b41` 起将 Release Skill、稳定入口和新增 prerelease 入口统一成两个相互隔离的状态机。
  prerelease 不绑定 `beta/desktop-api-worker` 或任何固定业务分支，而是要求调用者显式给出
  `source_branch`、40 位 `release_sha`、`version` 与匹配的 `alpha`/`beta`/`rc` channel。
- Certify 只从远端来源分支的 exact SHA 构建一次 Windows/macOS 候选和
  `prerelease-candidate.json`，不会创建 tag 或 Release；Publish 只能按候选 run ID 提升同一批
  字节，不得重建或替换公开资产。修复必须使用更高的同 core prerelease 版本。
- manifest 绑定来源分支、SHA、run、channel、默认 `split`、诊断 schema、工具链和两平台资产
  SHA-256。打包资源另嵌入 exact prerelease build identity，packaged QA 再反向核对 manifest、
  run ID、版本和资产摘要。
- 公开 workflow 只允许 `prerelease=true`、`make_latest=false`，拒绝 `latest.yml`、`appcast.xml`
  等稳定更新资产；公开前后捕获并比较稳定 Latest 与更新 metadata 摘要。稳定 tag 发现明确排除
  所有 prerelease tag，稳定发布入口仍只允许 `master` 和 `x.y.z`。
- Release Skill 已加入通用 Prepare、Certify、Publish Prerelease、Verify Isolation、Observe、
  Supersede/Withdraw 流程和独立人工批准门；文档明确本地诊断不会自动上传。分支命名只作为每次
  调用的显式输入，不成为通用规则。
- POSIX、PowerShell、`.github/workflows/release.yml`、可复用 `prerelease.yml`、候选/隔离工具、
  Windows VM runner 和运维文档共同执行同一合同，不存在只写在说明中的软约束。

#### Windows 首次失败与修复

Windows 专项始终使用专用 NTFS checkout、隔离数据和 loopback fake 服务，未修改 VM 代理设置。
所有失败均保留首次现场，先精确重放再决定是否修改；没有通过放宽性能预算或跳过测试收口。

1. 早期 quick QA 暴露 Windows 专属生命周期和测试夹具差异。`ed4a192`～`2a9820a` 依次修复
   QA cache 输入、CLI 原生 install binding、退出 PID 保留、角色优雅停止、Worker break shutdown、
   CRLF/进程日志、runtime status 瞬时锁、Batch recovery read、Proactor socket pair、loopback
   proxy bypass、合法 PID 复用与 fault release 上界。其中生产相关的退出/锁行为有独立回归，
   平台夹具修复没有改变产品语义。
2. `697e0b4` 为 Windows `.release` 删除和 `.reached→.completed` 原子替换的瞬时
   `PermissionError` 加入仅 Windows 生效的有界重试，总等待 0.63 秒；持续失败仍抛出，五处
   临时放宽的 completion timeout 恢复为 5 秒。
3. 第一轮完整 Windows backend 为 1931 tests，1 failure + 2 errors + 7 skipped，
   1693.464 秒。三项分别是迁移测试未关闭 SQLite connection、packaged workload 过早认定
   IMAP claim 已释放，以及 10 万教授仪表盘 3.557 秒超过 2.5 秒门槛。`89b8d52` 收口句柄和
   IMAP 终态竞态，`112a2ea` 删除一次重复学校聚合；三项原测试精确重放 3/3，61.728 秒。
4. Crawler/Batch/Matching 三轮真实进程矩阵随后发现 Matching 在“失败、通过、失败”中可能让
   detached 计算对应的 `MatchAnalysisRun` 短暂保留 `running`。`27a2dac` 在取消宽限耗尽后，
   将底层 run 与 item 在同一事务收口；确定性单测和 Windows API cancel 连续 20 次通过。
5. 第二轮完整 Windows backend 为 1933 tests，2 failures + 1 error + 7 skipped，
   1865.932 秒。失败为仪表盘 3.2169 秒、全选 ID 1.6099 秒，以及 SQLite 锁竞争压力下一个
   loopback 请求超过 5 秒。`984ba1c` 复用导师页已有学校汇总并在 SQLite 下把有序 ID 聚合为
   JSON 一次解析；`fd7ecb5` 改用现有 trimmed hierarchy 表达式索引，学校汇总在 Windows
   profile 中由 0.7917 秒降至 0.0261 秒，完整仪表盘为 0.9941 秒。
6. 精确 `fd7ecb5` 上连续 5 轮冷启动规模测试全部通过，每轮重新迁移、写入 10 万教授；独立
   API + Worker SQLite 锁竞争连续 10 轮全部通过。由于锁场景没有复现，且最终完整组合负载也
   通过，没有增加生产锁、没有扩大 5 秒请求超时，也没有降低 1.5/2.5 秒性能预算。

#### B3 最终证据

本机复核：

```bash
rtk node --test \
  scripts/release/check-release-version.test.mjs \
  scripts/release/release-preflight.test.mjs \
  scripts/release/prerelease-contract.test.mjs \
  scripts/release/prerelease-notes.test.mjs \
  scripts/release/prerelease-preflight.test.mjs \
  scripts/release/prerelease-build-identity.test.mjs \
  scripts/release/prerelease-candidate.test.mjs \
  scripts/release/prerelease-isolation.test.mjs \
  scripts/release/prerelease-workflow.test.mjs \
  scripts/quality/script-topology.test.mjs \
  scripts/quality/document-topology.test.mjs
rtk bash scripts/release/prepare-prerelease.test.sh
rtk bash scripts/release/prerelease-script.test.sh
```

- Node 合同 34/34 通过；两套 POSIX 入口通过。
- 本机未安装 `pwsh`，因此本机 PowerShell 命令无法启动；这不计为产品失败。当前 Windows
  checkout 上直接重跑 `prerelease-script.test.ps1` 通过，quick QA 的完整
  `release-orchestration-contracts` 输入指纹也命中此前同输入成功证据。

Windows 最终命令：

```bash
rtk bash scripts/quality/run-windows-vm-release-qa.sh --quick
```

精确提交：`fd7ecb5c4dc6895c2db9c9f1f64a748f409cc7f9`。

| 检查 | 结果 |
| --- | --- |
| Backend full | 1933/1933，7 skipped，1886.357s |
| Backend frozen build | API / Worker / combined / document self-check 全部通过，157.7s |
| Desktop typecheck | 通过 |
| Desktop full | 35 files / 237 tests 通过，3 files / 11 tests skipped，46.03s |
| Desktop clean install + tests stage | 通过，125.1s |
| Windows quick QA | 退出成功；精确 SHA 一致 |

quick QA 明确跳过 VC++ installer preparation、NSIS 和安装后 packaged lifecycle，因此不是 B5
或公开发布的正式候选证据。B3 只关闭 AC-BRANCH-03 与 AC-REL；AC-ISO 仍等待真实 Prerelease
和稳定客户端隔离验证，B5 仍等待同一 exact package 的双平台覆盖升级与长稳。

B3 完成。当前没有 push、tag、workflow dispatch、GitHub Release、稳定 feed 修改或合回
`master`；B4 从刷新最新 `origin/master`、连续两次全仓和连续 20 次 split 集成开始。

### B4：最新 master 集成、双全仓与 split 重复回归

#### master 刷新与语义合并

- 原 SSH 22 端口 fetch 首次等待后超时，错误为连接 `20.205.243.166:22` 超时；工作树和远端
  配置均未改变。随后使用 GitHub 官方 `ssh.github.com:443` 临时 URL 成功 fetch，没有修改
  `origin` 的 fetch/push 地址。
- 刷新后最新 master 为 `a4062f80ad75ed5661ab1362aaa8cf9681ebe1e6`，包含社区导师完整导出、
  Agent CLI 搜索/批量计划安全、批量任务模板应用和作用域修复共 4 个提交。
- merge 只有 `backend/test/test_agent_api.py` 一个文本冲突。语义合并同时保留 master 新增的
  `confirmed_fingerprint` 不匹配 409 断言，以及本分支 at-most-once 路径的
  `send_prepared_email` mock；没有退回旧 `send_email` 发送函数。merge commit 为 `ab30799`。
- merge 前后聚焦验证：Backend Agent/API/社区影响面 359/359；CLI 225/225；Frontend
  Tasks/Professors 影响面 132/132；相关 Python Ruff 全部通过。

#### 连续两次完整全仓

同一代码提交 `ab30799` 串行执行两次，未并行争抢资源：

```bash
rtk proxy uv run --project backend --no-sync python scripts/quality/run_all_tests.py
```

| 套件 | 第 1 轮 | 第 2 轮 |
| --- | --- | --- |
| Backend | 1937/1937，11m18s | 1937/1937，10m28s |
| CLI | 225/225，16s | 225/225，16s |
| Frontend | PASS，21s | PASS，24s |
| Desktop | PASS，39s | PASS，38s |
| Website | PASS，<1s | PASS，<1s |
| 合计 | 12m37s，0 failures | 11m48s，0 failures |

两轮之间工作树保持干净；第 2 轮不是失败后的重试，而是计划要求的独立连续成功证据。

#### split 集成连续 20 轮

以下命令使用 20 个相互独立的 Vitest 进程逐轮执行，任一非零退出立即停止；未使用 Vitest
`--retry`，也没有放宽测试自身的 180 秒上界：

```bash
rtk env AUTO_EMAIL_SENDER_MODE_SWITCH_QA=1 \
  npm run test -- backendModeSwitch.integration.test.ts
```

- 20/20 轮连续通过，总时长 805.148 秒；单轮 39.794～40.873 秒。
- 每轮对一个全新隔离 userData 和同一数据库完成 21 次启动、20 次 combined↔split 双向切换，
  因此累计 420 次后端启动、400 次模式切换。
- 每次停止均等待所有累计 API/Worker PID 退出并确认 `runtime/worker.json` 删除；每轮最终数据库
  均为 `integrity_check=ok`、`foreign_key_check=0`、`journal_mode=wal`。
- 临时重复驱动位于 `/tmp`，完成后已删除；未把一次性测试文件带入仓库或候选内容。

B4 完成，AC-BRANCH 全部关闭。当前仍没有 push、tag、workflow dispatch、GitHub Release、
稳定 feed 修改或合回 `master`。B5 从本地开发候选和安全 smoke 开始；需要远端 exact candidate
时必须停在独立人工批准门。

### B5：首个本地 Prerelease 准备与双平台开发验证

#### 版本选择、Prepare 与发布合同缺陷

- 首个本地候选选择 `2.6.0-beta.1`：core `2.6.0` 高于最新稳定版 `2.5.4`，channel 为
  `beta`，序号为正整数 `1`。来源分支仍为本次显式输入 `beta/desktop-api-worker`，没有把该
  分支名写入通用 Skill 或脚本。
- Desktop、Frontend、公告和公开资产名使用 SemVer `2.6.0-beta.1`；Python CLI 的包版本使用
  等价 PEP 440 `2.6.0b1`。第一次真实 Prepare 暴露 prerelease preflight 错把 CLI 也要求为
  SemVer 的合同缺陷。`4288f4c` 统一限定 `alpha`/`beta`/`rc` 加正整数序号，并让校验器接受
  对应 PEP 440 Python 版本；POSIX 与 PowerShell 夹具同步修正，发布合同 34/34 通过。
- `050c1c8` 完成本地 Prepare：CLI/Desktop/Frontend 版本元数据、
  `docs/releases/v2.6.0-beta.1.md` 和 `desktop/release-notes.md` 已同步，两个公告文件逐字一致。
  本地 certify dry-run 曾在该 SHA 通过，并明确没有 push、tag 或 dispatch；最终文档提交形成
  新 SHA 后必须重新运行，旧 dry-run 不作为最终冻结证据。
- macOS 本机已通过前端公告/打包合同与 production build、Desktop 打包合同/类型检查/完整测试、
  CLI 测试/lock/冻结构建与性能门槛，以及冻结 Backend 的 API/Worker/combined/document 自检。
  完整 DMG development smoke 在启动前因本机环境未提供 `SPARKLE_PUBLIC_ED_KEY` 而安全停止；
  没有读取、重建、打印或用占位 key 绕过。该环境门也不被记为产品通过或产品失败。

#### Windows 两次首次失败、重放与测试看门狗修复

Windows 始终使用专用 NTFS checkout、隔离数据和 loopback fake 服务；未修改 VM 代理。以下
两次完整 Backend 首次现场都保留，没有用聚焦重放替代最终全套结果：

1. 精确 `050c1c8` 的第一次完整 Backend 共 1937 项，结果为 1936 passed、7 skipped、1 error。
   `test_run_queued_job_finishes_warmup_item_before_starting_remaining_items` 在 VM 已经历长时间压力后，
   原 1 秒看门狗内没有调度到 mocked warm-up 事件。原测试精确重放 1/1、连续单项 20/20、
   Matching 模块 25/25、模块连续五轮共 125/125 均通过。
2. 该测试验证 warm-up 与剩余 item 的顺序，不声明 1 秒产品 SLA。`202a094` 只把测试内部过于
   激进的 1 秒/2 秒事件看门狗统一改为 5 秒；排序断言、生产代码、产品 5 秒请求超时和
   1.5/2.5 秒性能预算均未改变。
3. 精确 `202a0942c7bf16db29762376f2e127136c6a2669` 的第二次完整 Backend 仍为
   1936 passed、7 skipped、1 error，但失败项变为
   `test_worker_kill_matrix_fences_results_and_converges_once`：`matching.after_final_commit` 场景的
   `/startup-status` 单次请求超过既有 5 秒。完整强杀矩阵精确重放 1/1 通过，耗时 75.297 秒；
   没有扩大 5 秒超时。
4. 随后的五轮矩阵第一次运行被另一个 Codex 进程通过 `shutdown.exe` 正常关闭 Windows VM；
   Windows Event 1074 与用户说明均确认这是外部关机，所以该退出码 255 和被打断的半轮不计入
   产品结果。VM 重启后从头执行五轮，共覆盖 30 个故障点，5/5 全部通过，耗时 368.433 秒。

#### Windows 最终完整 quick QA

命令：

```bash
rtk bash scripts/quality/run-windows-vm-release-qa.sh --quick
```

产品/打包输入精确 SHA：`202a0942c7bf16db29762376f2e127136c6a2669`。

| 检查 | 结果 |
| --- | --- |
| Backend full | 1937/1937，7 skipped，1502.069s |
| Backend frozen build | API / Worker / combined / document self-check 全部通过，136.3s |
| Desktop typecheck | 通过 |
| Desktop full | 35 files / 237 tests 通过，3 files / 11 tests skipped，27.94s |
| Desktop clean install + tests stage | 通过，77.6s |
| Windows quick QA | 退出成功；精确 SHA 一致 |

此次完整全套没有复现前两次一次性超时。quick runner 会安全复用输入和输出完全一致且已有成功
记录的发布编排、Frontend 和 CLI 阶段；Backend 因前两次未成功留档而从头运行，随后进行了
干净 PyInstaller 构建和真实三角色自检，Desktop 也执行了干净 `npm ci`、类型检查与完整测试。

本节仍不是 AC-BETA-QA 的 exact-package 发布证据：quick 明确跳过 VC++ installer preparation、
NSIS 和安装后 lifecycle；macOS 本地冻结构建也不是远端候选 DMG。证据文档提交后先运行
`release-impact.mjs`、prerelease preflight 和 certify dry-run；只有用户分别批准 push 与远端
Certify workflow 后，才能取得同一 run 的原始 DMG/EXE/manifest，并继续两平台覆盖升级、
lifecycle、短时密集 normal/chaos 与诊断重建。当前仍没有 push、tag、workflow dispatch、
GitHub Release、稳定 feed 修改或合回 `master`。

### B5 续：最新 master 再同步与最终候选代码收口

#### `origin/master@2fcc431` 语义合并

- 2026-08-11 最终复核前再次通过 GitHub SSH 443 临时 URL fetch，没有修改 `origin` 配置。
  最新 master 仍为 `2fcc431d25ba36b1de6380bb316589a750cebc2f`；当前分支相对它 ahead 37、
  behind 0，merge-base 精确为该提交。
- 该 master 已在 `e313811528adc407211cfd8aa6f68e6a3c84749d` 合入，merge parents 为
  `8c6682be260e0a0dc86be925bb8a48e98dca7956` 和 `2fcc431d25ba36b1de6380bb316589a750cebc2f`。
- 五个文本冲突均按功能并集语义解决：
  - `desktop/src/main/bootstrap/application.ts` 同时保留 Beta 的 `powerMonitor`、诊断记录、隔离
    QA home、split 重启/combined 原生回退，以及 master 的 Agent UI handoff 创建、ready poll
    和退出停止；
  - `desktop/src/main/ipc/register.ts` 同时保留模式/诊断 IPC 与 Agent UI handoff 校验；
  - `desktop/src/preload/bridge.ts` 同时保留后端连接保持语义、模式/诊断 bridge 与 handoff 缓冲；
  - `desktop/test/ipcRegistration.test.ts` 同时保留 Beta 输入边界和 handoff acknowledgement 边界；
  - `docs/architecture/README.md` 同时保留 API + Worker/Beta 计划和 Agent UI handoff 文档入口。
- 两侧 Alembic heads 由 `20260810_merge_agent_ui_delivery` 合并，不额外执行 DDL；其 parents 为
  `20260810_agent_ui_handoffs` 和 `20260810_merge_delivery_scale`。最终
  `rtk uv run --no-sync alembic heads` 只报告这一个 head。

`e313811` 上连续两次完整全仓均为 0 failures：Backend 每轮 1957/1957、CLI 每轮 234/234，
Frontend、Desktop 和 Website 全部通过。split 集成随后以 20 个独立 Vitest 进程连续 20/20
通过，累计 420 次后端启动和 400 次 combined↔split 切换；每轮继续核对所有 PID 退出、Worker
descriptor 删除和 SQLite integrity/foreign key/WAL 不变量。

#### Windows Agent CLI 冻结包性能首次失败

最新 master 带来的 Agent CLI 查询路径在源码测试中正确，但 Windows 冻结包 benchmark 首次
报告 intent routing p95 为 1082ms，超过既有 1000ms 门槛；同一聚焦场景重放为 1158ms，因此
按真实性能缺陷处理，而不是记为调度噪声或提高预算。

根因是每个 capability 都重复解析相同 query 的 resource matches 和 tokens。`b27f6b2` 改为每次
查询只派生一次并传给逐 capability 评分，同时增加“每项查询特征只计算一次”的回归。修复后：

- Windows 冻结包 intent p95 为 672ms，准确率 100%；
- macOS 冻结包 intent p95 为 242ms，准确率 100%；
- CLI 完整测试 235/235 通过；1000ms 门槛未改变。

#### Windows SQLite 文件句柄首次失败

随后 Windows quick QA 暴露 Agent UI handoff migration 测试使用普通
`with sqlite3.connect(...)` 后仍可能由 Python 保留连接，测试清理临时数据库时出现
`WinError 32`。这不是产品迁移失败，但会使 Windows 全套不可靠。`f94c666` 将四处连接改为
`contextlib.closing(sqlite3.connect(...))`，确保离开作用域即显式关闭；Windows 原测试重放 1/1、
macOS schema 模块 63/63 通过。

精确 `f94c6669a7ddf6aa7cfefe1fbb9fd8b769041317` 的最终完整 Windows quick QA：

| 检查 | 结果 |
| --- | --- |
| Backend full | 1957/1957，7 skipped，1608.622s |
| Backend frozen build | API / Worker / combined / document self-check 全部通过 |
| Desktop typecheck | 通过 |
| Desktop full | 37 files / 248 tests 通过，3 files / 11 tests skipped |
| Windows quick QA | 退出成功；精确 SHA 一致 |

quick 仍明确跳过 NSIS、覆盖安装和 packaged lifecycle，不作为正式候选证据。

#### Frontend 取消状态竞态首次失败与修复

为满足“最终候选代码连续两次全仓”，在 `f94c666` 从零开始的新第 1 轮中，Backend 1957/1957、
CLI 235/235、Desktop 和 Website 通过；Frontend 998/999，唯一失败为
`supports canceling and retrying failed information enrichment items`，页面找不到“重试失败项”。
精确单测首次重放通过，但 20 个独立 Vitest 进程中再次出现 1 次相同失败，因此没有把它当作
单次测试噪声，也没有扩大 Testing Library 等待时间。

根因是取消成功返回 `canceled` 后，取消前已经在途的任务列表读取仍可用旧 `running` 覆盖
mutation 结果。`2123af5` 在提交取消结果前推进列表 request generation，使所有更早读取失效，
并结束可能由失效读取留下的 loading 状态。测试同时做了两项收口：正常取消/重试 mock 在服务端
提交后返回新状态；另一个可控 deferred-request 测试强制让取消前的旧 `running` 响应最后返回，
验证 UI 仍保持 `canceled` 且提供“重试失败项”。

修复后的证据：

- 两个取消/竞态场景在 20 个独立 Vitest 进程中连续 20/20 通过；
- TasksPage 模块 62/62；Frontend lint、production build 和完整 125 files / 1000 tests 通过；
- Desktop typecheck 通过，完整 38 files / 256 tests 通过，2 files / 3 tests 按平台门禁跳过；
- `release-impact.mjs --base f94c6669a7ddf6aa7cfefe1fbb9fd8b769041317 --head
  2123af58e20e0499abc19d7016e36d1647039927` 将变更分类为 Frontend，要求
  Frontend suite/build、Desktop suite 和 Windows quick；三项均已执行。Windows quick 在
  `2123af5` 上做了干净 Frontend install/build，并只复用输入完全相同且此前成功的 CLI、Backend、
  frozen build 和 Desktop 阶段，最终退出成功。

#### 最终候选代码连续两次全仓

重新 fetch 后 master 未前进，工作区干净。在同一产品代码提交
`2123af58e20e0499abc19d7016e36d1647039927` 上串行执行：

```bash
rtk proxy uv run --project backend --no-sync python scripts/quality/run_all_tests.py
```

| 套件 | 第 1 轮 | 第 2 轮 |
| --- | --- | --- |
| Backend | 1957/1957，9m27s | 1957/1957，9m51s |
| CLI | 235/235，18s | 235/235，19s |
| Frontend | PASS，15s | PASS，18s |
| Desktop | PASS，38s | PASS，39s |
| Website | PASS，<1s | PASS，<1s |
| 合计 | 10m40s，0 failures | 11m09s，0 failures |

两轮之间 SHA 未变化、工作区保持干净，未使用 retry。连续 20 次 split 证据位于 `e313811`；
其后的三个提交分别只修改 CLI 查询计算、测试 SQLite 句柄和 Frontend 页面状态，均由各自 impact
测试覆盖，没有修改 Electron backend topology、模式设置、runtime manager 或 split 集成输入。

当前仍处于 B5 本地收口：本证据文档提交后必须以新的最终 `release_sha` 重跑 release impact、
prerelease preflight、certify dry-run 和本地冻结 build identity。没有 push、远端 workflow、tag、
GitHub Release、稳定 feed 修改或合回 `master`；取得 push 与 dispatch 的分别授权前必须停止。

### B5 续：首个远端候选、Windows 首次失败与传输修复

#### 首个 Prerelease Certify

- 用户明确批准 push 来源分支和 dispatch Prerelease Certify 后，远端
  `beta/desktop-api-worker` 精确指向冻结 SHA
  `bd19519d24cf372cd7a6d3e766a2c173c74ff2c6`。
- `Release Desktop` run `31417575421` 的 Ubuntu prerelease preflight、Windows build、macOS
  build 和 candidate certify 全部成功；publish job 按设计跳过。run 未创建 tag、draft 或
  GitHub Release，也没有修改稳定 update feed。
- 下载到仓库外的原始资产与 manifest 重新计算摘要后为：
  - Windows EXE：`7985bae7929fefef7adb8371b3faeb192cc5ed2140215ab209d36aef7f59aa28`；
  - macOS DMG：`7be7c61effd8430efe88b8c60fc27b030f2c68718acdd16d250c46c574348f5e`；
  - `prerelease-candidate.json`：
    `bcf4c31b02dea0a266031c3b72a73f49905a45ce0eead658e180f575399fdd33`。
- manifest 同时绑定 run ID、冻结 SHA、`beta/desktop-api-worker`、默认 `split`、诊断 schema 1、
  公告摘要和认证前稳定 Latest/feed 基线。上一稳定版 v2.5.4 EXE/DMG 也从公开 Release 下载到
  仓库外并分别复核为
  `245aadcdf63ccae80913ede6a4cda9571884f83da9f23b957c724a6fb3b15d21` 与
  `c67fe772766751798163b16a985a9e3e97893c4ad906cde161c4e85bc6c9447b`。

#### Windows exact-package 首次失败

使用 run `31417575421` 的原始 EXE、manifest、上一稳定版 EXE、seed `20260810` 和
Prerelease 2h/1h 时长门禁启动正式 runner。宿主首先成功输出：

```text
[ok] windows AutoEmailSender-Setup-2.6.0-beta.1.exe 已绑定候选 run 31417575421。
```

VM 随后在任何安装或长稳场景开始前失败：

```text
windows prerelease asset name 不匹配：AutoEmailSender-Candidate-64462.exe，
预期 AutoEmailSender-Setup-2.6.0-beta.1.exe。
candidate manifest and Windows installer binding failed with exit code 1
```

首次现场原样保存在仓库外
`/Users/junie/Programs/AutoEmailSender-release-candidates/31417575421/windows-exact-package-first-failure.txt`。
该运行没有启动安装器、lifecycle、normal soak 或 seeded chaos，不能计作正式 Windows 证据。

根因不是候选字节、摘要或 run ID：host 在传输前已经用原始文件名通过严格 manifest 校验，但
`run-windows-vm-release-qa.sh` 为避免共享目录重名，把同一 EXE staging 为
`AutoEmailSender-Candidate-<pid>.exe`；guest 随后再次调用同一个严格校验器，必然因 manifest
资产名不一致而拒绝。既有静态 packaging 测试只确认参数存在，没有覆盖跨 VM 后仍保留资产名。
因此 run `31417575421` 已拒绝用于发布，其资产只保留为首次失败证据，不与后续 SHA/run 混用。

#### 修复与原场景重放

修复提交 `bd5e52e1bff20e73a2d322ef1fbfccb0899a3b3d` 使用 `mktemp -d` 在专用 Parallels 共享根下
创建每次运行唯一的临时目录，并在目录内保留候选、上一稳定版和 manifest 的原始 basename；
退出时只删除明确列出的临时文件并移除该空目录。这样同时满足并发隔离和 manifest 文件名合同。
Desktop packaging 回归新增四项正向路径断言，并明确禁止恢复
`AutoEmailSender-Candidate-<pid>.exe` 改名。

本机修复验证：

| 检查 | 结果 |
| --- | --- |
| Bash syntax / `git diff --check` | 通过 |
| Desktop packaging 聚焦 | 24/24 |
| Desktop typecheck / full | 通过；256/256，3 个平台门禁 skip |
| 发布公告 | 2/2 |
| POSIX prepare/release/prerelease 入口 | 全部通过 |
| release candidate / prerelease / impact / workflow 合同 | 20/20 |

真实共享目录探针随后把 run `31417575421` 的同一 EXE 和 manifest 放入唯一目录但保留原始文件名，
由 VM 内 `bd19519d` checkout 的冻结校验器复核。guest 成功输出同一候选绑定信息；临时文件和目录
随后删除，VM 恢复 suspended。该探针只证明传输根因已修复，不计作 formal QA。

`release-impact.mjs --base bd19519d --head bd5e52e` 将变更分类为 `desktop` 和
`release-orchestration`，要求 release contracts、Windows PowerShell contracts、Desktop suite
和 Windows quick QA；可跳过 Windows formal QA 与 macOS Sparkle candidate。前三项本机已通过，
真实 Windows quick QA 在精确 `bd5e52e` 上完成：

- 唯一共享目录、增量 bundle 和 checkout 更新成功；
- 输入未变的 Frontend、CLI、Backend 与冻结 build 阶段安全复用已有成功指纹；
- Desktop 在 VM 内重新 `npm ci`、typecheck 和完整测试，248/248 通过，11 个环境/平台门禁 skip；
- runner 以 0 退出并报告精确 SHA 一致。

quick 明确跳过 VC++、NSIS 和 installed packaged lifecycle，仍不替代 AC-BETA-QA。虽然产品安装包
输入没有变化，但修复提交改变了 `release_sha`，旧 manifest 仍不可绑定新 SHA；必须形成新的最终
文档提交并在重新批准后 push、dispatch 新 Certify run，之后从同一个替代 run 重新取得三份原始
资产。当前没有新的 push/dispatch 授权，也没有 tag、Release、稳定 feed、master 合并或稳定版
发布授权。

### B5 续：替代候选、上一稳定版 seed 失败与恢复

#### 替代 Prerelease Certify

- 用户分别批准 push `908dfa953ceb3e4a4c364261683f89f9f8fad68f` 和 dispatch replacement
  Prerelease Certify 后，`Release Desktop` run `31453411547` 的 Ubuntu preflight、Windows
  build、macOS build 与 candidate certify 全部成功；publish job 按 `publish=false` 跳过。
- run 未创建 tag、draft 或 GitHub Release。manifest 绑定同一 SHA、来源分支
  `beta/desktop-api-worker`、`2.6.0-beta.1`、默认 split、诊断 schema 1 和未变化的 v2.5.4
  stable Latest/feed 基线。
- 下载到仓库外并复核的原始资产为：Windows EXE
  `254342465e700265c9e4fdf211f9e98cefbc24c499b38160350256f7c293690e`，macOS DMG
  `5d484267dbf0c0a390aff5b546851312cd608b804cf6956be1c9e936b6e8dd8c`，manifest
  `15369a31d070ea0a6673dd501eaf4bea8884b1a767be57ca3de08582591bb36b`。

#### Windows 正式 QA 首次失败

run `31453411547` 的 exact EXE、manifest、公开 v2.5.4 EXE、seed `20260810` 与 2h/1h
Prerelease 门禁完成 host/guest 两层资产绑定、精确 checkout、VC++、冻结 CLI build identity、
CLI intent p95 `727.88ms`/accuracy `1.0` 和本地 NSIS packaging contract 后，在上一稳定版数据
seed 阶段失败：

```text
PATCH /api/agent/v1/settings returned HTTP 422
code: INVALID_AGENT_REQUEST
validation_error_count: 14
```

失败发生在候选覆盖安装、packaged lifecycle、normal soak 和 seeded chaos 之前；现场保存在
`/Users/junie/Programs/AutoEmailSender-release-candidates/31453411547/windows-exact-package-first-failure.txt`。
该 run 不构成 Windows 正式证据，也不得与后续 SHA/run 混用。

#### 分类、三层修复与真实重放

- `v2.5.4` 的 `/api/agent/v1/settings` 虽使用 PATCH，`RuntimeSettingsUpdate` 除测试 marker 外仍有
  14 个必填字段；seed 只发送 marker，恰好产生 14 个 missing-field 错误。这是 QA 客户端合同
  缺陷，不是候选数据库迁移或产品升级失败。`61bdbeb` 从前置 GET 快照回送全部可写字段，只排除
  `revision`/`updated_at`，并增加固定 v2.5.4 合同测试。
- 首次真实聚焦重放已越过 422、写入设置/导师/材料，但安装树证据哈希在深层 Playwright 文件上
  返回 `WinError 3`。`402d9db` 把 package/EXE/tree identity 快照移到旧应用启动之前；进一步
  取证确认目标路径超过传统 Windows 260 字符限制，而非文件消失。`27bd475` 对 drive、UNC 和已
  扩展路径统一使用 Windows extended-length path，并增加三类回归合同。
- 一次独立的全新安装诊断因旧 NSIS 内置 WMI 占用检查运行 19 分钟未退出而终止；它未进入 seed，
  不计作产品或候选结果。终止挂起的 PowerShell 对象经 VM 正常 restart 清除，复核相关进程为 0，
  VM 恢复原 `suspended` 状态，失败目录保留在专用 VM。

最终聚焦与回归结果：

| 检查 | 结果 |
| --- | --- |
| packaged runtime/seed 聚焦合同 | 21/21，23.430s |
| Ruff / `git diff --check` | 通过 |
| 最终完整 Backend | 1960/1960，1 skip，631.702s |
| 真实 v2.5.4 seed 重放 | 通过；设置 marker、导师、材料、integrity/foreign key、零残留进程 |

真实重放精确绑定 `27bd47547e5b284f96dcee36daa6b6af348ba627`、seed 脚本摘要
`b8fd2837400d79fbe5ef1c2ebd5594ef8d1a439bcef349a8bb891b5f70ffe4d0`、公开 v2.5.4 EXE 摘要
`245aadcdf63ccae80913ede6a4cda9571884f83da9f23b957c724a6fb3b15d21`，生成 manifest 摘要
`e375e72dbe9da687a3d846e6f14aa7ab3d918d37af7a5c749567fee861e636b0`。manifest 与日志保存在
`/Users/junie/Programs/AutoEmailSender-release-candidates/31453411547/windows-seed-replay-27bd475/`，
不提交 Git。

该聚焦重放只关闭本次 failure-recovery，不替代新 SHA/run 的候选覆盖安装、lifecycle 和当前规定的
短时密集 normal/chaos。`31453411547` 已因 QA 代码变化失效；形成新最终文档 SHA 后必须重新取得 push 与
Certify dispatch 授权。仍未授权 tag、公开 GitHub Prerelease、稳定 feed、master 合并或稳定版
发布。

### B5 续：第二个替代候选、stale Windows 安装状态与恢复

#### 第二个替代 Prerelease Certify

- 用户分别批准 push `45c5d5f8eb6b707f4ed905b3d697be5b6e1b0608` 和再次 dispatch
  replacement Prerelease Certify（允许 no-op push）。`Release Desktop` run `31464156897` 的
  Ubuntu preflight、Windows build、macOS build 与 candidate certify 全部成功，publish job
  按 `publish=false` 跳过。
- run 未创建 tag、draft 或 GitHub Release。manifest 绑定来源分支 `beta/desktop-api-worker`、
  `2.6.0-beta.1`、精确 SHA、默认 split、诊断 schema 1 与 v2.5.4 stable isolation baseline。
- 原始资产复核结果：Windows EXE
  `57e1b73a91fe74f6b5f5c54b24695c9a0d711e0e77066476bfaa18de841e4064`，macOS DMG
  `746ac4b2362b63531cfcd468ec034532d355a472592bb983d5f59259da7d9532`，manifest
  `180261eec56d22cf1aedb4a5c5dede87a8d2763f25a233e0f3e75f53326adf0d`。

#### Windows 正式 QA 首次失败

run `31464156897` 的 exact EXE/manifest、公开 v2.5.4 EXE、当时配置的 2h normal/1h chaos 和 seed
`20260810` 完成候选绑定、精确 checkout、VC++、release contracts、CLI 冻结 identity 与 intent
accuracy `1.0`/p95 `572.49ms`、Backend 1960/1960（7 skip）、冻结后端三角色/文档 self-check 和
本地 NSIS packaging contract。进入 installed lifecycle 后，上一稳定版安装器在候选覆盖之前
持续等待约 32 分钟；只读 UI Automation 取得错误：

```text
Failed to uninstall old application files. Please try running the installer again.: 2
```

根因证据显示 HKCU uninstall key 仍指向上一失败 SHA `908dfa9` 的专用临时安装根。旧 uninstaller
及目录存在、目录下无运行进程，但新一轮 v2.5.4 安装会先尝试卸载该残留并弹窗；runner 的
`WaitForExit()` 又没有超时，因此无法自行失败退出。候选尚未安装，lifecycle、normal soak 和
seeded chaos 均未开始；该 run 不构成 Windows 正式证据。

首个现场、候选绑定、注册表路径与截图保存在：

```text
/Users/junie/Programs/AutoEmailSender-release-candidates/31464156897/windows-qa-first-failure/
```

原 runner 在证据保存后通过终止错误安装器以 `-1` 退出；guest 中 Auto Email Sender、backend、
Python QA 进程均为 0，VM 手动恢复 suspended。候选三项资产复哈希未变化。

#### runner 修复与真实 Windows 5.1 验证

`b6381e1591d99314e0d9d10b4742c1c6e5f5cc60` 完成以下最小恢复：

- 只枚举 DisplayName 为 Auto Email Sender 且 uninstaller 规范路径位于
  `%TEMP%\auto-email-sender-packaged-qa\` 下的 HKCU 项；正式运行前清理旧项，`finally` 只清理
  当前 install root 的项，不触及正常用户安装，也不删除失败文件/数据证据；
- 缺少属性、非引号命令、路径规范化失败或 QA 根外的注册项全部跳过；
- 安装/卸载进程改为 600 秒有界等待，超时记录最后窗口标题并用 `taskkill /T /F`，且确认进程树
  已退出；
- host runner 只在自己启动 VM 时于任何退出路径恢复 suspended，并保留原主流程失败码。

本机和 Windows 聚焦结果：

| 检查 | 结果 |
| --- | --- |
| Desktop typecheck / 完整测试 | 通过；256 passed，3 skipped |
| Desktop packaging / Frontend desktop packaging | 24/24；2/2 |
| POSIX prepare/release/prerelease、release notes、impact | 全部通过；impact 10/10 |
| Windows PowerShell 5.1 helper 探针 | parser 0 errors；真实 stale 项只读识别；synthetic 专用项精确删除；缺属性项保留；1s timeout 子树终止 |
| Windows quick QA（精确 `b6381e1`） | PowerShell contracts 通过；Desktop 248/248，11 skip；runner 0 退出；VM 自动恢复 suspended |

本机没有 `shellcheck` 可执行文件；同一 host runner 的 `bash -n` 通过，且真实 quick QA 已完整执行
其启动、传输、退出 trap 与 suspended 恢复路径。

`release-impact.mjs --base 45c5d5f --head b6381e1` 将范围分类为 `desktop` 与
`release-orchestration`，要求 release contracts、Windows PowerShell contracts、Desktop suite
和 Windows quick QA；这些均已完成，Windows formal 与 macOS candidate 可在新冻结 SHA 形成前
跳过。但 manifest 绑定的是 `45c5d5f`，因此 `31464156897` 已失效，不得在清理 VM 后直接重用。
形成最终文档提交后，必须再次取得新 SHA 的 push 与 Certify dispatch 授权；仍未授权 tag、公开
GitHub Prerelease、稳定 feed、master 合并或稳定版发布。

### B5 续：正式 QA 前移快速失败门禁

复盘三次 replacement 后确认，候选传输改名可在 1 分钟内发现，v2.5.4 settings 422 和 Windows
长路径可在旧版 seed 后数分钟发现，stale uninstall 与无界 wait 也应在安装入口数秒内发现；旧
runner 却把这些边界放在 release contracts、VC++、Frontend、CLI、Backend、冻结 build、Desktop
和本地 NSIS 之后。为此，本地实现三层门禁：

- `harness-rehearsal` 不绑定 manifest/run，可使用 run `31464156897` 的失效包；第一轮故意中断，
  第二轮必须证明 Windows stale 注册表/进程和 1 秒 timeout 子树恢复，或 macOS mount trap 恢复；
- `candidate-admission` 绑定 exact manifest/run/SHA/version/新旧摘要，跳过昂贵源码与本地重建，
  直接执行覆盖升级、split/combined lifecycle、迁移完整性、原生 sleep/wake、真实 Beta 诊断 ZIP、
  卸载和重复安装；
- 正式 `--prerelease-certification` 的 lifecycle 保持不变；2026-08-12 起按用户明确的首个 Beta
  风险选择，改为每平台连续 5 分钟 normal 和 5 分钟 chaos 密集门禁。

packaged driver 对前两层固定输出 `certification_eligible=false` 和独立 `evidence_purpose`；rehearsal
主动拒绝失效 manifest，admission 才允许 exact candidate 绑定。提交前本地结果为 Backend packaged
QA 24/24、Desktop 聚焦 36/36、typecheck 通过、Desktop 全量 256 passed/3 skipped、四个 POSIX
release/prerelease 合同通过、Ruff/Bash syntax/`git diff --check` 通过。真实双平台两轮 rehearsal
仍待执行，因此本节尚不关闭 AC-BETA-QA-00，也不构成申请 replacement Certify 的依据。

首次真实 Windows rehearsal 在 1.2 秒内、任何构建或安装前发现新 runner 试图在删除 stale 注册项
后递归删除旧 install root；PowerShell 5.1 对旧 Playwright 深路径报 `DirectoryNotFoundException`。
这正是本门禁要提前暴露的 harness 缺陷：恢复只需终止专用根进程并删除专用 HKCU 项，不应删除
失败安装树或调查证据。实现已移除 install-root 递归删除并增加禁止恢复该行为的静态合同；该次
不是计划中的故意中断轮，修复后从第一轮重新计数。

修复后的下一次聚焦 rehearsal 已在 1.3 秒内通过受控 timeout 子树探针，但 v2.5.4 安装器在
120 秒门限内仍未退出；runner 终止安装器及一个子进程并恢复 VM suspended，没有进入 seed。
原超时证据只有 PID，无法区分 VC++、应用误启动或 NSIS 子进程。后续超时因此增加不含命令行的
PID/父 PID/可执行名/路径摘要，再做一次最多 120 秒的聚焦重放后分类，不猜测根因。

增加进程树证据后的重放确认子树为 `previous-stable.exe -> vc_redist.x64.exe ->
vc_redist.x64.exe`，不是应用进程或 NSIS 卸载窗口。随后通过专用 Parallels 共享目录执行一次性只读
采集脚本，取得 `%TEMP%` 的 Burn 日志：一次历史成功执行在 11:16:17 记录
`Launching elevated engine process`，到 11:22:51 才记录 engine 已启动，11:22:52 以 `0x0`
完成；已安装 14.51 runtime，高于旧包内置的 14.44，日志明确将旧包操作判定为兼容登记而非降级。
因此 120 秒是确定性的误判，不是更快失败门禁。

Windows preflight 现将安装操作固定为 600 秒硬上限，覆盖真实观测的 394 秒提权延迟；卸载仍保持
120 秒。安装超时时除有界终止完整进程树外，还会从本次启动后生成的 `dd_vcredist_*.log` 输出
最近 Burn 事件、日志大小和最后写入时间，不读取 CIM 命令行。该变化仍须从 rehearsal 第一轮重新
执行；run `31464156897` 继续只可作为失效包输入，不得成为 admission 或正式证据。

`d6c6513` 的 Windows rehearsal 第一轮完成 v2.5.4 安装与真实 seed 后按计划硬中断；第二轮立即
发现并清理 1 个专用 HKCU 注册项和 3 个安装根进程，旧证据树保持不变，1 秒 timeout 探针再次
通过。随后 v2.5.4 重装/seed 成功，但失效候选覆盖时弹出
`Failed to uninstall old application files...: 2`；测试者点击确定后进程才退出，因此第二轮明确
失败且不计证据。

失败现场没有安装根进程或卸载注册项，5228 个约 740 MB 文件几乎完整保留；安装根长 130 字符，
Playwright 最深路径达到 280 字符，唯一 3 个独占读取失败均为 261/275/280 字符路径的
`DirectoryNotFoundException`，不是共享锁。隔离现场中 `cmd rd` + `\\?\` 在 0.210 秒删除该树；
进一步用 PowerShell 5.1 的 `[System.IO.Directory]::Delete()` 对 299 字符独立探针验证为 0.019 秒，
因此产品修复采用后者，避免安装目录进入 shell 重新解析。

新安装器会在默认应用进程检查完成、真正覆盖旧版之前运行内嵌 extended-path 清理；新卸载器也
清理同一安装根内固定的 `resources\ms-playwright`，拒绝祖先 reparse point，不触及 userData。
Windows 合同测试新增 260+ 字符、中文、空格、`Ω`、`&` 和相邻文件保留场景。runner 同时新增
静默窗口轮询，未来此类 NSIS 弹窗会在数秒内自动失败并终止进程树，不再依赖人工点击或等待
600 秒。该产品/包装变化必须生成新的本地包重跑两轮 rehearsal，并最终生成新 exact candidate。

`ad91c78` 的真实 Windows PowerShell 5.1 超长路径聚焦测试通过，随后生成的本地 beta/split NSIS
绑定 `ad91c78d78b29b298128421692133b8d5d85cc53`，大小 `240438135`，SHA-256 为
`3a57213e908317a0f42ae2b0a8ea320adf66f5cfb792cfa7b5debfd67565c580`。重新开始第一轮时，旧版
v2.5.4 内置 VC++ bootstrapper 在 600 秒仍停留于 `Launching elevated engine process`；runner
自动记录 `previous-stable.exe -> vc_redist.x64.exe -> vc_redist.x64.exe`、终止子树并恢复 VM
suspended。测试者恢复 VM 后看到残留 VC Redist 对话框并点击确定；复核 VC 进程为 0、无待重启
键，Burn 日志没有越过该事件，因此该轮未进入 seed，也不计为 rehearsal 证据。

只读取证确认 VM 的 x64 Runtime 为 `14.51.36247.0`，内置版本为 `14.44.35211.0`，Burn plan 对
x64 minimum/additional 均为 `execute: None`，却仍为依赖登记进入 Apply/UAC。后续安装器因此新增
两种 registry view 的兼容版本检测：相同或更高版本直接跳过，过旧/缺失/检测失败才执行内置
redistributable；PowerShell 5.1 已对 equal/newer/older/invalid/missing 与真实 registry 全部分支
聚焦验证。rehearsal 同时只在非认证模式复用经过版本、旧包/EXE 摘要、数据库、固定路径、
Playwright 与 reparse point 检查的上一稳定版 seed；恢复轮续用同一中断安装根，不再重复执行旧
installer。candidate admission 和正式 QA 不采用检查点，仍从公开 v2.5.4 安装包开始。

### B5 验收标准变更：首个 Beta 改用 10 分钟密集门禁

2026-08-12 用户明确把目标调整为“尽快公开首个 Beta，在稳定隔离和可恢复前提下接受低频问题由
后续更高 Beta 修复”。因此测试版正式运行门禁从每平台 2h normal + 1h chaos 改为：

- 每个平台使用同一 exact candidate 连续运行 300 秒 normal 和 300 秒 seeded chaos；
- 资源采样间隔不超过 10 秒，故障动作间隔不超过 5 秒；
- 全部声明的 chaos 动作、六类工作、数据库完整性、资源阈值、诊断导出和进程清理必须通过；
- lifecycle、上一稳定版覆盖升级、双平台 admission、资产/run/SHA 绑定和稳定更新隔离不得省略；
- 稳定版认证继续保持 24h normal + 8h chaos，本次变更不降低稳定版发布标准。

该门禁不能证明小时级资源泄漏、积压或低频竞态不存在。这一剩余风险必须写入 Beta 证据和公告；
出现阻断、数据正确性或重复发送风险时停止使用该 Beta，保留原资产并发布更高 prerelease。

### B5 续：Windows GUI 优雅退出门禁修复

当前 HEAD 的 Windows 第二轮 rehearsal 已依次通过 stale 注册/进程恢复、1 秒 timeout、上一稳定版
seed 恢复、split ready、Worker 无监听、覆盖升级与迁移备份、API 读写、single-instance、Worker
重启、进程组重启、数据库审计、真实 browser 后代清理和强杀后恢复。最后失败点为
`taskkill /PID /T` 返回“process can only be terminated forcefully”；该命令不向 Windows GUI 应用
发送正常退出事件，不能作为 Electron `before-quit -> stopBackendAndExit` 的证据。

修复增加固定 `WM_APP` 范围消息，并满足三重隔离：仅 `win32`、仅 packaged QA userData 全授权
成功、仅目标 Electron PID 的顶层窗口。消息处理调用现有 `quitFromTray()`，继续走真实
`app.quit()`、`before-quit`、API/Worker/Playwright 清理和 recorder flush。runner 使用 Win32
`EnumWindows`、`GetWindowThreadProcessId` 和异步 `PostMessageW`，快速启动场景在 20 秒内每 50ms
有界重试；窗口在枚举后消失时继续下一轮，不回退到 `/F`。

提交前验证结果：Desktop typecheck 通过，Desktop 全量 258 passed/3 skipped，packaged runtime QA
35 passed/3 platform skipped，Ruff、Python compile 和 `git diff --check` 通过。该变化触及 Windows
packaged Electron 与 QA harness；先用当前 HEAD 重建 rehearsal EXE 并只重跑检查点支持的 Windows
两轮。macOS 输入是否失效由 `release-impact.mjs` 决定，不因文档或 Win32 条件代码盲目重跑。

`508119f` 的真实第二轮随后证明正常 split 和 combined 的 QA 消息均触发完整退出，分别约 0.64 秒
和 2.21 秒；100ms rapid-exit 则超时。trace 显示 runner 在 BrowserWindow 创建前已向同 PID 的
早期顶层窗口成功投递，因此停止重试，但该窗口没有 Electron hook。修复将“已投递”只作为诊断
状态，不作为完成条件；每 50ms 继续枚举/投递，直至 PID 真实消失或 20 秒硬上限。此变化只有
Python harness 和测试/文档，不改变 SHA-256 为
`7f519fe9c76f401d2f9092b720a59a024ef4ec26efca76a0b698c4298bb251ae` 的 rehearsal EXE，故聚焦
合同通过后从失败的第二轮 lifecycle 重放，不重复 NSIS 构建、完整 Backend/Desktop、VC++ 或已
证明 stale 恢复的第一轮。

修复后的真实 rapid-exit 在约 1.10 秒完成，`report.json` 为 `status=passed`，19 项 check 全部
通过、5 次数据库审计全部通过、资源违规为空。Windows host runner 随后在证据分层检查处失败：
PowerShell 5.1 的 `Get-Content` 默认 ANSI 解码无 BOM UTF-8，导致报告内中文隔离路径变成无效文本，
`ConvertFrom-Json` 拒绝。runner 新增严格 UTF-8 JSON helper，并统一用于 runtime、checkpoint、
cache、package metadata 和两处 report 读取。该缺陷发生在已通过 lifecycle 之后，但卸载、重复
安装和最终 runner success 尚未形成证据，因此必须重放第二轮至完整成功。

严格 UTF-8 后处理已在下一次真实重放通过；runner 随后在静默卸载父进程返回并固定等待 2 秒后
仍看到主 EXE。失败后立即只读检查时安装根已不存在、文件数为 0、根内进程为 0，证明 NSIS
临时卸载子进程只是晚于父进程异步收尾，不是产品卸载或长路径删除失败。runner 现对主 EXE 消失
每 200ms 轮询，30 秒硬上限，首次与重复卸载使用相同判断；超时仍会保留完整失败证据。

`e23b6e75333a32205b8a804fe845dea2ccd59d1c` 的最终 Windows 双轮 rehearsal 完整通过。第二轮报告：

- `status=passed`、`certification_eligible=false`、
  `evidence_purpose=non-certifying-harness-rehearsal`；
- current package SHA-256
  `7f519fe9c76f401d2f9092b720a59a024ef4ec26efca76a0b698c4298bb251ae`，previous stable SHA-256
  `245aadcdf63ccae80913ede6a4cda9571884f83da9f23b957c724a6fb3b15d21`；
- 19/19 checks、5/5 database audits、0 resource violations，lifecycle observed 59.438s；
- stale 3 个进程 + 1 个注册项恢复、timeout 1.2s、覆盖升级、split/combined、Worker/整组重启、
  browser 后代清理、强杀恢复、rapid-exit、首次卸载、重复安装和重复卸载全部通过。

宿主保存报告为 `/Users/junie/Parallels Shared/win-passed-rehearsal-e23b6e7/report.json`。macOS 修复后
报告 `/private/var/folders/z9/x7nb1cpd7_55hm9d3vpddkmc0000gn/T/auto-email-sender-macos-qa.J0hbA8/
evidence/auto-email-sender-packaged-qa/20260811T201422Z-343f2f17ae/report.json` 同样为 19/19、零违规，
DMG SHA-256 `93c6aa4e6f1fb15022ec05f505a57355b81de68d9d381774ea50739c86b6dac5`。后续 impact 明确无需重跑
macOS，因此 AC-BETA-QA-00 关闭；admission 与正式 exact-package 证据仍等待新 Certify run。

### B5 续：Windows 休眠适配与最终候选前聚焦回归

run `31551840527` 的首次 Windows admission 在 S3 返回 `ERROR_NOT_SUPPORTED(50)` 后停止。QA runner
现仅在该错误下使用 Windows 原生休眠：guest 先写原子握手，host 必须观察 VM 进入 `stopped` 后
重新启动；最终仍要求 Kernel-Power 42、Power-Troubleshooter 1、原 runtime/API/Worker PID、
heartbeat 推进和 API 读写全部成立。Parallels `exec` 因休眠断开时，由 guest wrapper 的 UTF-8
原子状态文件传递真实退出码。真实探针已证明同一 PowerShell PID 可跨休眠保留；packaged runtime
合同 38 passed/3 skipped、Desktop packaging 24/24、Desktop 全量 258 passed/3 skipped、typecheck、
Backend 2031 passed/5 skipped和 prerelease contracts 22/22 均通过。

随后精确 `fd5df8c` 的 Windows quick QA 在 Backend 2031 项的最后阶段发现一处 Windows 专属测试
清理竞态：`test_timeout_releases_slot_when_llm_ignores_cancellation` 已按产品设计释放调度槽并隔离迟到
LLM 结果，但测试固定等待 20ms 后删除临时 SQLite；Windows 的异步会话收尾尚未完成，因而报
`WinError 32`。`f73a8a6` 只把测试同步改为等待迟到任务真实完成并让事件循环排空，不修改 master
业务逻辑或生产超时语义。本机该测试连续 5 次通过，Windows 聚焦测试 0.505s 通过；
`release-impact.mjs --base fd5df8c --head f73a8a6` 只要求 Backend suite，并明确跳过 Windows formal
和 macOS candidate。本机 Backend 2031/2031 在 10m47s 通过。该聚焦证据取代再次运行整个 Windows
quick 套件；形成最终证据提交后冻结新 SHA，再申请同 SHA replacement Certify。

run `31564882972` 已为 `5b9418f` 生成并认证 exact EXE/DMG/manifest；Windows admission 通过候选
绑定、上一稳定版安装/seed、候选覆盖、split ready 后进入原生休眠。VM 确实进入 `stopped`，恢复
后同一 Electron/API/Worker/Python driver 进程仍存活，但 host 先观察到 `prlctl exec=255`、尚未
看到共享握手，因而提前失败并由 cleanup 删除握手目录。这是 host 监督顺序竞态，不是产品失败，
该 run 仍按 exact evidence 规则失效。

`d77835d` 让 `exec` 断开与 VM `stopped` 两种先后顺序都获得最多 15 秒的共享握手收敛窗口；只有
握手存在且 VM 真实 stopped 才启动，启动后必须在 60 秒内离开 stopped，原来的次数上限和无握手
失败条件不变。真实轻量探针直接覆盖失败顺序：`exec=255`、观察 `stopped+handshake`、自动启动，
guest 同一 PowerShell PID `10684` 写出 resumed。本机 release contracts、Desktop typecheck/全量、
packaging 24/24、Bash syntax 与 Windows PowerShell release contracts 通过；产品安装包逻辑未变。

run `31567826340` 的下一次 admission 再次证明旧版 seed、候选覆盖和 split ready，随后仍在休眠处
复现：Python `write_text` 返回后共享目录尚未对 host 可见，guest 已执行 `shutdown /h`；因此仅在
host 端等待传播不能构成可靠协议。修复改为带唯一 `request_id` 的两阶段握手：guest 写 requested
后最长等待 30 秒，host 解析并原子回写同 ID 的 acknowledged，guest 只接受匹配 ACK 后才允许休眠；
每轮先清理旧 requested/ACK/resumed，host 对不匹配 ACK 必须重写，防止 lifecycle 与 chaos 两次
休眠交叉复用。

真实 Windows 探针刻意使用与 packaged driver 相同的 Python 快速 `write_text`：host ACK
`7f9b146a-bddd-451c-81b2-36add3b0fcf5` 后 guest 才休眠，`prlctl exec=255`，VM stopped/自动启动，
同一 Python PID `5460` 恢复且 resumed request ID 完全一致。完整 packaged runtime contracts
39 passed/3 skipped、Desktop 258 passed/3 skipped、typecheck、全部 POSIX release contracts、
Bash syntax 和 diff check 通过。该真实探针在申请新 candidate 前直接覆盖原失败路径。

run `31570525027` 随后为 `1be5a41a0a45f678202f43fe01dff9a569349ef4` 生成 exact candidate；远端
contract、preflight、Windows/macOS build 和 certify 全部成功。下载后重新计算得到 EXE
`a560b7b752d373afa309eee17edd8a03ca83ae999e5a2502701d5d07beb9fdcf`、DMG
`5eb74a1eea441a861d09197876a97610d0692e049bb803011e50d8a65a5e60ec`，大小、版本、分支、run ID、
release SHA、默认 split 和稳定隔离基线均与 manifest 一致。

Windows admission 已通过 stale 专用注册恢复、1.3 秒受控 timeout、v2.5.4 安装与 seed、候选覆盖、
split 桌面启动及 API/Worker ready。原生休眠时 guest 已看到匹配 ACK 并使 VM stopped，产品和 driver
进程在恢复后仍存活；host 却在 stopped 时看不到共享 request 文件，15 秒后误判为无握手停止。
这是 host 监督器丢失自己已 ACK 状态的确定性缺陷，不是产品故障；run `31570525027` 因而失效。

修复让 host 在内存中保留已验证且 ACK 的唯一 request ID，共享文件在 stopped 瞬间不可见时仍只
允许该 ID 驱动恢复；看到同 ID resumed 后才清除 pending 状态。没有 pending ACK 的普通停止仍失败，
恢复次数上限不变。独立自动实机探针完整复现 `prlctl exec=255 -> VM stopped -> start`，休眠前后
Python PID 均为 `5712`，request ID 均为 `d936d204-7430-4e1c-a50f-b8886b8d75b3`。Desktop packaging
聚焦 24/24 与 Bash syntax 已通过；其余 impact 合同通过并形成新冻结 SHA 后重新 Certify。

`9a6d1f0` 的 Windows quick 内部测试随后全部成功：Backend 2032 tests、7 skip、0 failure，冻结
API/Worker/combined/document 四项自检通过，Desktop 250 tests、11 skip、0 failure，runner 明确打印
`Windows quick QA passed`。外层 host 在成功后把整份约 28 分钟 UTF-16 日志一次性写入终端，终端
背压使 `iconv` 和 fallback `cat` 返回 `Resource temporarily unavailable`，cleanup 因而在读取原子
status 前以 1 退出。这不推翻内部测试证据，但暴露了输出层可覆盖真实状态的缺陷。修复先读取原子
status，再对成功日志只回显末 200 行、失败日志末 2000 行；回显失败不再改变 QA 真实退出码。
重放使用 stage cache，禁止重复已通过的 Backend/冻结构建/Desktop。

修复输出层后，Windows quick 对 `01f9180dbd0fb40c0d8155fa29112bd5281b46ea` 以外层退出码 0 完成；
Backend 2032 和冻结构建从成功 cache 复用，Desktop 250/250（11 skip）重跑通过。replacement run
`31576240231` 的 contract、preflight、双平台 build 和 certify 全部成功，下载复核 EXE
`79ffdfc1987194053d4c5868c9cce67527125d9679c8c37ea36feedccb8484d5`、DMG
`c862f47e1ca69e53127ac5453fd7f5315d0b958e36700f4500ab30bbaba4da2a` 与 manifest 完全一致。

该 run 的 Windows admission 通过 stale/timeout、v2.5.4 seed、候选覆盖和 split ready 后仍在休眠
监督处失败。guest 已拿到 ACK 并能休眠，但 `shutdown /h` 在 VM 真正 stopped 前返回，guest 过早写
resumed；host 当时仅校验同 ID，因而清除 pending，稍后看到 stopped 时误判无握手。最终协议增加
同 ID restarted：guest 必须在 `shutdown /h` 返回后等待 host 观察 stopped、执行 start 并原子写
restarted，之后才写 resumed；host 只有记录 restarted 后才接受 resumed。聚焦 Python 顺序合同、
Desktop packaging 24/24、typecheck 均通过。真实探针严格得到
`ACK -> exec=255 -> stopped/start -> restarted -> resumed`，休眠前后 Python PID `10824`、request ID
`54b1a9a6-a3d5-4974-844b-6170bfe3cbc7` 均一致。按 Skill 先用失效候选重做非认证 rehearsal，再决定
是否申请新 run。

复核 runner 后确认，历史 Windows `harness-rehearsal` 只执行了 packaged lifecycle，但
`run-windows-release-qa.ps1` 当时仅为 formal/candidate-admission 添加 `--system-sleep-wake` 和
休眠握手目录。因此旧 19 项全绿报告不能证明之后才加入的四阶段协议在完整 runner 中工作，
AC-BETA-QA-00 的 Windows 部分重新打开；这不是产品回归，也不推翻旧报告对安装、升级、进程和
数据库生命周期的证据。

当前 QA-only 修复把 `-HibernateHandshakePath` 扩展到所有 Windows packaged lifecycle，并让
rehearsal lifecycle 同样执行 native sleep/wake。rehearsal 仍不得携带 candidate manifest/run ID，
报告仍须为 `certification_eligible=false` 和
`evidence_purpose=non-certifying-harness-rehearsal`。本机验证通过：Desktop packaging 24/24、
packaged-runtime 完整合同、Desktop 全套、TypeScript typecheck、Bash syntax 和 `git diff --check`。
下一步只用失效 run `31576240231` 的原始 EXE 重做 Windows“故意中断 -> 立即恢复”两轮，要求最终
报告明确包含原生 sleep/wake；该 Windows-only harness 变化不使既有 macOS rehearsal 失效。
