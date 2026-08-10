# 桌面 API + Worker 通用 Beta 验证 Goal 执行计划

- 状态：执行中（现有 Goal 为 active；B0～B4 已完成，当前阶段为 B5）
- 当前 Goal ID：`019fe582-2dea-7e42-bd2e-684bae191421`
- 建立日期：2026-08-10
- 前置实现计划：[`desktop-api-worker-process-plan.md`](./desktop-api-worker-process-plan.md)
- 前置验收证据：[`desktop_api_worker_goal_acceptance.md`](../development/desktop_api_worker_goal_acceptance.md)
- 本 Goal 验收报告：[`desktop_api_worker_beta_goal_acceptance.md`](../development/desktop_api_worker_beta_goal_acceptance.md)

## 1. Goal 定义

### 1.1 可直接用于 Goal 模式的目标

```text
严格按照 docs/architecture/desktop-api-worker-beta-goal-plan.md，在保留既有 API + Worker
实现和 combined 回退语义的基础上，建立可供本次及未来测试版本复用的桌面 prerelease
体系：先保护当前 detached/dirty 工作并将最新 master 合入专用开发分支；实现由 Electron
持久化、在“其他设置”中可选择且重启后生效的 combined/split 模式以及页面外安全回退；
实现默认仅保存在用户本地、空间有界、严格脱敏、后端失效时仍可导出的 Beta 诊断包和本地
分析器；把通用测试版本的 Prepare、Certify、Publish Prerelease、Verify Isolation、Observe
和 Supersede 流程写入 Release Skill、脚本、工作流、测试与运维文档；使用同一精确候选资产
在本机 macOS 专用测试环境和 Windows Parallels VM 完成真实覆盖升级、模式切换、生命周期、
故障注入与内部长稳。只有全部必选验收标准通过、稳定版更新入口未发生变化、诊断包未泄露
敏感信息、没有未解决的阻断级或高风险缺陷，并在用户明确批准后将精确候选发布为非 Latest
的 GitHub Prerelease 且完成隔离验证时，Goal 才算完成。未经单独批准，不得 push、创建 tag、
dispatch 远端发布工作流、公开 Release、合并 master 或发布稳定版本。
```

### 1.2 B0 起点与当前基线

- 原双进程 Goal 已取消，但 G0～G5 的实现与证据保留；G6 packaged QA 自动化已经完成开发验证。
- B0 开始时桌面仍默认 `combined`，通过 `AUTO_EMAIL_SENDER_BACKEND_MODE=split` 可进行开发/QA 切换。
- B0 开始时工作区位于 detached HEAD
  `6e06be9bfeae11b78eae78096782d84b3176c931`，包含大量未提交的本 Goal 前置改动，
  并比当时的 `master` 落后 5 个提交。
- B0 已将前置改动保护到本地分支 `beta/desktop-api-worker`，并将
  `origin/master@4b54b5897d796bdb496432d1e3d41b7a3c32f2d3` 语义化合入。该分支名只是
  本次实现记录，不是通用 prerelease 发布规则。
- B0 开始时当前版本和最新稳定版均为 `2.5.4`；B5 已选择首个本地候选版本
  `2.6.0-beta.1`，但这不代表已经授权远端认证或公开发布。
- 尚无外部候选 workflow 的 DMG、NSIS、`release-candidate.json` 或 run ID。

### 1.3 Goal 当前检查点（2026-08-11）

- Goal 系统当前将 `019fe582-2dea-7e42-bd2e-684bae191421` 报告为 `active`。另一个 Codex
  进程意外关闭没有取消 Goal；继续沿用同一 Goal ID，不创建相互冲突的新 Goal。
- 当前本地分支为 `beta/desktop-api-worker`。2026-08-11 重新 fetch 后，最新
  `origin/master` 仍为 `2fcc431d25ba36b1de6380bb316589a750cebc2f`；它已通过 merge commit
  `e313811528adc407211cfd8aa6f68e6a3c84749d` 合入。当前分支相对 master 为 ahead 37、behind 0，
  merge-base 精确为该 master。该分支名只描述本次开发工作，不会成为通用测试版发布条件。
- `e313811` 的五个文本冲突同时保留 Beta 的 powerMonitor、模式切换、页面外回退和本地诊断，
  以及 master 的 Agent UI handoff 生命周期、IPC、preload 缓冲和文档入口；Alembic 通过
  `20260810_merge_agent_ui_delivery` 合并两侧 revision，`alembic heads` 只有一个 head。
- B2 已完成 Electron/Python 有界记录器、设置页/托盘/启动失败导出、API 宕机 partial ZIP、
  单包/多包安全 analyzer、恶意 ZIP 防护和最终 ZIP 跨语言 canary 零泄漏门禁。最终证据为
  Desktop 239/239、Frontend 完整 962/962 且最终聚焦 18/18、Backend 115/115、analyzer
  10/10、跨语言 ZIP 7/7；AC-OBS/AC-PRIV 已关闭，B5 仍会在 exact package 上重复真实故障场景。
- B3 已把稳定版与通用 prerelease 双状态机落实到 Release Skill、POSIX/PowerShell 入口、
  workflow、候选 manifest、恢复/隔离验证和合同测试。入口显式绑定任意安全来源分支、exact SHA、
  channel 和版本，不依赖本次分支名；AC-BRANCH-03 与 AC-REL 已关闭，AC-ISO 保留到真实公开门。
- Windows VM 在精确 `fd7ecb5` 上完成 quick QA：Backend 1933/1933（7 skip）、冻结后端三角色与
  文档自检通过、Desktop 35 files / 237 tests（11 skip）通过。quick 模式未执行 NSIS 和安装后
  lifecycle，因此只关闭 B3，不替代 B5/B6 候选证据。
- B4 已在 `e313811` 上完成连续两次完整全仓，Backend 每轮 1957/1957、CLI 每轮 234/234，
  Frontend/Desktop/Website 全部通过；split 模式切换集成连续 20/20 轮通过，总计 420 次后端
  启动和 400 次模式切换。此后候选代码又修复 Windows CLI 性能、Windows SQLite 测试句柄和
  Frontend mutation/read 竞态；最终产品代码 `2123af58e20e0499abc19d7016e36d1647039927`
  从零重新计数的两轮全仓也连续通过，Backend 每轮 1957/1957、CLI 每轮 235/235，其余套件
  全部通过，总时长分别为 10m40s 和 11m09s。
- B5 已在本地准备 `2.6.0-beta.1` 元数据和公告；Desktop/Frontend 使用 SemVer
  `2.6.0-beta.1`，Python CLI 使用等价 PEP 440 版本 `2.6.0b1`。本机发布合同、前端、CLI、
  Desktop、macOS 冻结后端和 Windows quick QA 均已通过。
- Windows 在 `f94c6669a7ddf6aa7cfefe1fbb9fd8b769041317` 上完成 Backend 1957/1957
  （7 skip）、冻结后端 API/Worker/combined/document 自检和 Desktop 248/248（11 skip）。
  期间真实发现并修复 Agent CLI 冻结包意图查询 p95 超标（`b27f6b2`）和迁移测试未显式关闭
  SQLite 导致的 `WinError 32`（`f94c666`）；没有放宽 1000ms 性能门槛或忽略 Windows 文件锁。
  Frontend 修复提交 `2123af5` 的 Windows quick QA 也通过，runner 对变更输入执行干净 Frontend
  安装/生产构建，并安全复用其余输入完全相同的成功阶段。
- B5 现在停在远端候选批准门前：本地 quick/source/frozen smoke 不替代同一 workflow 原始
  DMG/EXE 的覆盖升级、lifecycle、2h normal 和 1h seeded chaos。需要 push、远端 Certify
  workflow 或 exact candidate 资产时必须取得独立人工批准。
- 当前没有获得 push、远端 workflow、tag、GitHub Prerelease、合回 `master` 或稳定版发布授权。

### 1.4 授权边界

本 Goal 允许在本地完成代码、文档、测试、分支创建和提交，也允许为避免长期漂移而把最新
`origin/master` 语义化合入当前测试开发分支。这里的“同步 master”不等于把测试分支合回
`master`。以下动作均设置独立人工批准门，不因“启动 Goal”自动获得授权：

1. 首次 push 任意 Beta 来源分支；
2. dispatch 会使用 GitHub Secrets 或生成候选资产的远端 workflow；
3. 创建或推送 prerelease tag；
4. 创建、公开、撤回或删除 GitHub Prerelease；
5. 将测试分支合并到 `master`、修改稳定更新 feed 或发布稳定版本。

在批准门之前，可以完成本地候选构建和开发 smoke，但不得把它表述成远端候选来源证据。

### 1.5 明确不在本 Goal 内的事项

- 不建设远程遥测、自动上传或后台上报；诊断数据只在本地轮转保存，由用户主动导出并发送。
- 不收集邮件地址、导师姓名、邮件主题/正文、附件内容、LLM prompt/response、凭据或完整本地路径。
- 不把 Beta 结果当作正式稳定版 24h normal soak、8h seeded chaos 或发布认证的替代品。
- 不让稳定版客户端加入 Beta 更新通道，不把 GitHub Prerelease 标记为 Latest。
- 不删除或覆盖已经公开的 Beta 资产；修复使用更高 prerelease 版本。
- 不自动降级数据库。`combined` 是同版本拓扑回退，不等于回装旧稳定版。

## 2. 通用分支、版本与候选合同

### 2.1 来源分支

Release Skill 和工作流不得写死某个业务分支。每次测试版本必须显式提供：

- `source_branch`：远端已存在的来源分支；
- `release_sha`：来源分支当前精确 40 位提交；
- `version`：合法 prerelease 版本；
- `channel`：本计划首期支持 `alpha`、`beta`、`rc`，并要求与版本后缀一致。

候选入口验证 `origin/<source_branch>` 精确指向 `release_sha`、SHA 可达、工作区干净、版本元数据
一致。分支可按项目需要使用 `beta/<topic>`、`release/<series>` 或其他经批准的名称；本次实际
分支名只记录在验收报告中，不成为 Skill 的通用规则。

稳定发布仍只允许 `master`。所有“上一稳定版”逻辑只接受精确匹配
`^v[0-9]+\.[0-9]+\.[0-9]+$` 的 tag，不能把 `v2.6.0-beta.1` 一类 tag 当成稳定升级基线。

### 2.2 候选和公开资产

- Beta 同样遵循 Prepare → Certify → Promote exact candidate，不允许公开阶段重建安装包。
- 候选 manifest 记录 channel、来源分支、SHA、run ID、版本、默认后端模式、诊断 schema、
  平台资产名/大小/SHA-256 和工具链。
- GitHub Release 必须为 public prerelease、`make_latest=false`，标题和应用 UI 明确显示测试版本。
- 首期 Beta 只做手动覆盖更新，不发布稳定通道使用的 `latest.yml`、`appcast.xml` 或 delta。
- Windows/macOS Beta 保持既有 appId、bundle id、安装路径和 userData，以真实验证稳定版覆盖升级；
  不能通过改成另一应用身份规避升级风险。
- 稳定版 `/releases/latest`、公开 appcast、Windows update metadata 和其 SHA-256 在 Beta 发布前后
  必须完全不变。

## 3. combined / split 用户设置

模式必须在后端启动前解析，因此由 Electron 保存到 `userData` 下带 schema version 的独立
桌面设置文件，使用临时文件 + 原子替换，并对损坏文件安全降级。解析优先级固定为：

1. 显式命令行安全模式参数；
2. 开发/QA 环境变量；
3. Electron 桌面设置；
4. 当前发布 channel 的默认值。

Beta 候选默认 `split`；稳定版在未来正式切换前继续默认 `combined`。用户显式选择必须跨升级
保留，且诊断中同时记录 requested/effective mode。

“其他设置”提供：

- 当前运行模式与下次启动模式；
- “API + Worker 测试模式”和“单进程兼容模式”的人类可读说明；
- 保存但稍后重启，以及保存并安全重启；
- 运行中任务提示，发送事务处于不可安全中断窗口时不得诱导立即重启；
- 仅桌面环境显示，Web 模式不伪造能力。

Electron 重启必须先 drain/停止 Worker、API 和 Playwright 后代、清理 runtime descriptor/锁，再
调用 relaunch。若 split 在前端可用前连续启动失败，原生错误窗口或命令行参数必须允许用户
选择 `combined`；不得静默回退并掩盖缺陷。

## 4. 本地 Beta 可观测性

### 4.1 记录原则

收集“回答验收问题所需的全部指标”，而不是收集全部原始用户数据。持续记录器由 Electron
持有基础时间线，即使 API 或 Worker 崩溃也能继续写入；各 Python 角色补充自身的健康、任务和
SQLite 聚合指标。记录失败、只读目录或磁盘满不能使产品流程失败。

默认使用 append-only JSONL、大小/时间双重轮转和总目录上限。必须记录单调时间与墙钟时间，
区分系统 sleep、墙钟跳变和真实 hang。用户可以查看占用、清空本地 Beta 数据并选择导出最近
1 小时、24 小时、7 天或全部保留期。

### 4.2 导出包

一键导出 `auto-email-sender-beta-diagnostics-<timestamp>.zip`，至少包含：

- `manifest.json`：schema、随机本地安装 ID、报告 ID、app/channel/version/SHA、OS/架构、
  requested/effective mode、候选 run/asset 身份；
- `timeline.jsonl`：Electron/API/Worker 启停、ready、exit、restart/backoff、sleep/wake、模式变更、
  安装升级和非正常会话标记；
- `resource-samples.jsonl`：CPU、RSS、句柄/FD、子进程/Playwright 数、数据库/WAL/SHM、日志和
  runtime 文件大小趋势；
- `workload-summary.json`：六类后台工作数量、耗时、队列年龄、状态和恢复统计，不含业务内容；
- `database-health.json`：Alembic revision、integrity/foreign key 结果、lock/busy 聚合和备份元数据；
- 脱敏的 Electron、API、Worker、startup 和 operation log；
- `summary.json`、`README.txt` 与 `checksums.sha256`。

正常设置页、启动失败原生窗口和桌面托盘至少各有一个可达导出入口；API 不可用时生成 partial
bundle 并明确缺失项，不能让最需要诊断的故障反而无法导出。提供“标记刚才的问题”入口，记录
时间和可选的短说明。

### 4.3 隐私与分析

- 结构化指标使用 allowlist；自由文本再经过统一脱敏，不依赖事后正则作为唯一防线。
- 用户名、机器名、非 loopback IP、邮箱、URL query、完整 home/userData 路径和业务实体内容
  均删除、归一化或报告内单向伪名化。
- 自动生成 canary，向所有可能来源注入 token、密码、邮箱、中文姓名、路径和正文；解压最终
  bundle 全量扫描，任一命中即失败。
- crash dump、数据库副本和 crawler 原始调试正文默认不进入 Beta bundle。
- 提供安全解压、校验 schema/checksum、批量聚合多个 bundle 的本地 analyzer；输出 combined /
  split、OS、版本、资源趋势、重启、锁、积压和不变量告警，不执行 bundle 内任何内容。
- 因为没有远程分母，测试者除故障报告外还要定期提交健康报告；随机本地安装 ID 只随用户主动
  导出出现，用于去重，不来源于硬件标识。

## 5. Release Skill 和工作流

在现有稳定发布状态机之外增加通用 prerelease 状态机：

1. **Prepare Prerelease**：验证 channel/version/source branch，生成带风险、备份、回退和诊断说明的公告。
2. **Certify Prerelease**：从精确 SHA 构建一次双平台候选和 manifest，不创建 tag/Release。
3. **Publish Prerelease**：仅在人工批准后发布同一候选，设置 prerelease 且非 Latest。
4. **Verify Isolation**：核对 tag/SHA/资产、稳定 Latest/feed 摘要和上一稳定客户端检查更新结果。
5. **Observe**：保存内部和用户主动提交的健康/故障 bundle 汇总，不远程拉取用户数据。
6. **Supersede/Withdraw**：不覆盖资产；更高 prerelease 修复。严重版本标记为停止使用并记录通知方案。

Skill、POSIX/PowerShell 入口、workflow、候选 manifest 和测试必须共同执行同一合同，不能只在
文档中声明。稳定发布入口仍必须拒绝非 `master`，prerelease 入口不得拥有修改稳定 feed 的路径。

## 6. 内部 Mac / Windows Dogfood

所有破坏性测试使用隔离数据：macOS 使用专用测试账户或在最早启动阶段固定 QA userData；Windows
使用既有 Parallels 专用 VM。不得配置真实导师、日常邮箱或生产密钥。外部依赖默认使用 loopback
fake SMTP/IMAP/LLM/HTTP；真实邮箱只允许另行批准的受控测试账户和收件人。

对同一候选 DMG/NSIS 至少执行：

- v2.5.4 → Beta 真实覆盖安装、迁移备份、材料哈希和 combined/split 同库读写；
- clean install、覆盖安装、卸载保留数据、重装和明确的备份恢复演练；
- UI 模式切换、稍后重启、立即重启、页面外 combined safe mode；
- Worker/API/Electron kill、旧后代清理、circuit breaker、第二实例和快速退出；
- 原生 sleep/wake、VM pause/resume、时间前后跳、网络 flap、SQLite lock、低磁盘/只读；
- Dispatcher、双 IMAP、Batch Draft、Matching、Crawler 六类真实工作；
- 每次注入的已知故障都必须在导出 bundle 中可重建，且 canary 扫描仍为零泄漏；
- 稳定 v2.5.4 应用内检查更新看不到 Beta，Beta 只能手动覆盖安装。

公开首个 Beta 前，两平台各自使用精确候选完成 packaged lifecycle、至少连续 2 小时 normal
soak 和 1 小时 seeded chaos；这些时长是 Beta 内部门禁，不替代未来稳定版 24h/8h 正式认证。

## 7. 分阶段执行

| 阶段 | 范围 | 完成条件 |
| --- | --- | --- |
| B0 | 文档、当前工作保护、分支与 master 集成 | **已完成**：前置快照 `e062f36`；master 合并 `c51df44`；合并修复 `4fe1bdf` |
| B1 | Electron 模式设置、UI、安全重启与页面外回退 | **已完成**：AC-MODE 全部通过；combined/split 同库回归和 macOS 隔离真机故障回退通过 |
| B2 | 本地记录器、诊断 ZIP、脱敏与 analyzer | **已完成**：AC-OBS/PRIV 全部通过；后端宕机仍能导出 partial bundle |
| B3 | 通用 prerelease Skill、脚本、workflow 与合同测试 | **已完成**：AC-BRANCH-03/AC-REL 全部通过；未触及稳定 feed |
| B4 | 合并后的全仓与重复专项回归 | **已完成**：`origin/master@2fcc431` 已通过 `e313811` 合入；最终产品代码 `2123af5` 全仓连续 2 次、split 集成连续 20 次通过 |
| B5 | 本地候选、Mac/Windows exact-package Dogfood | **执行中**：`2.6.0-beta.1` 本地准备与双平台开发/quick smoke 已通过，Windows 最终 quick 绑定 `2123af5`；仍待远端同 run 的 DMG/EXE 完成两平台 lifecycle、2h normal、1h chaos 和诊断重建 |
| B6 | 远端候选与公开 Prerelease 人工批准门 | 获得明确批准后才 push/dispatch/publish；AC-ISO 全部通过 |
| B7 | 证据收口与观察交接 | 报告包含所有命令、SHA、资产摘要、seed、资源和已知限制 |

### 7.1 本次恢复后的固定执行顺序

1. **B2 已完成**：先完成设置页诊断 UI，再完成 analyzer、恶意包防护、canary 解包扫描和相关回归；
   未通过隐私零命中前，不进入发布流程改造。
2. **B3 已完成**：把 Release Skill、POSIX/PowerShell 入口、workflow、候选 manifest、恢复规则和
   合同测试一起改为“稳定版 + 通用 prerelease”双状态机。通用入口只认显式
   `source_branch + release_sha + version + channel`，不得绑定当前分支名。
3. **B4 已完成**：最新 `origin/master@2fcc431` 已合入；最终产品代码的连续两次全仓和不受后续
   Frontend-only 修复影响的连续 20 次 split 集成均已通过，首次失败与修复原样记入验收报告。
4. **正在执行 B5**：`2.6.0-beta.1` 本地准备与安全 smoke 已完成；先对证据文档提交后的最终
   SHA 运行 release impact、prerelease preflight 和 certify dry-run。获得 push 与远端候选
   workflow 的独立授权后，两平台只使用同一 run、同一 SHA 对应的原始候选资产完成覆盖升级、
   lifecycle、2h normal 和 1h seeded chaos。
5. **停在 B6 人工门**：在没有单独批准时不 push、不 dispatch、不创建 tag/Release。获得批准后
   才发布非 Latest 的 GitHub Prerelease，并验证稳定 Latest/feed 和稳定客户端完全隔离。
6. **完成 B7**：收口可复现证据、已知限制和后续观察方式；仍不自动合回 `master` 或发布稳定版。

## 8. 必选验收标准

### AC-BRANCH

- **AC-BRANCH-01**：当前工作从 detached HEAD 进入具名分支，无文件丢失，提交前后内容清单一致。
- **AC-BRANCH-02**：最新 `master` 已合入；所有冲突都有测试或人工语义核对记录。
- **AC-BRANCH-03**：通用 prerelease 入口验证显式 source branch + exact SHA，不硬编码本次分支名。

### AC-MODE

- **AC-MODE-01**：设置在后端启动前由 Electron 原子读取，损坏设置不阻止 combined safe mode。
- **AC-MODE-02**：UI 准确区分当前/下次模式，保存后不谎报已切换，重启后 effective mode 一致。
- **AC-MODE-03**：安全重启无残留 API/Worker/Playwright、无旧锁；连续 20 次双向切换数据库不损坏。
- **AC-MODE-04**：split 启动失败且前端不可达时，原生入口仍可切回 combined，并记录原因。
- **AC-MODE-05**：Beta 默认 split；稳定版默认值和用户既有选择不被 Beta 发布流程篡改。

### AC-OBS / AC-PRIV

- **AC-OBS-01**：Electron/API/Worker 时间线、资源、工作负载和数据库健康均有 schema-versioned 本地证据。
- **AC-OBS-02**：异常退出、强杀和 API 不可用后仍能导出可校验 partial bundle。
- **AC-OBS-03**：记录目录有时间、单文件和总量上限；记录失败不影响业务或进程健康。
- **AC-OBS-04**：analyzer 安全处理单份/多份、损坏、未知 schema 和恶意路径 ZIP，并输出可操作告警。
- **AC-PRIV-01**：最终 ZIP 的 allowlist、脱敏和 canary 全量扫描通过，禁止项零命中。
- **AC-PRIV-02**：没有网络上传路径；只有用户主动保存文件会把随机安装 ID 带出本机。

### AC-REL / AC-ISO

- **AC-REL-01**：Release Skill 同时描述稳定与通用 prerelease，分支、channel、版本和批准门合同一致。
- **AC-REL-02**：候选 manifest 绑定 source branch/SHA/run/channel/default mode/diagnostic schema/资产摘要。
- **AC-REL-03**：稳定 tag 发现排除 prerelease；稳定发布脚本继续只允许 `master`。
- **AC-REL-04**：公开资产不可替换；修复、撤回和更高 Beta 的恢复流程有自动合同测试。
- **AC-ISO-01**：Prerelease 为 public、prerelease=true、Latest=false，直接链接可下载。
- **AC-ISO-02**：公开前后稳定 Latest、appcast、Windows metadata 和摘要不变。
- **AC-ISO-03**：真实 v2.5.4 Windows/macOS 检查更新均看不到 Beta。

### AC-BETA-QA

- **AC-BETA-QA-01**：同一候选资产在 macOS 和 Windows 完成 v2.5.4 覆盖升级与完整 lifecycle。
- **AC-BETA-QA-02**：两平台各自单次连续 ≥2h normal soak、≥1h seeded chaos，双时钟达标。
- **AC-BETA-QA-03**：故障轨迹、进程恢复、资源趋势、数据库与任务不变量能由用户同格式 bundle 重建。
- **AC-BETA-QA-04**：零数据损坏、零重复 SMTP DATA、零孤儿进程、零未解释退出。

## 9. Definition of Done

只有同时满足以下条件才能完成本 Goal：

1. B0～B7 完成，全部必选 AC 有可复现证据且通过。
2. 合入最新 `master` 之后的候选代码完整全仓测试连续通过 2 次，split 集成套件连续通过 20 次。
3. combined/split 设置、安全重启、页面外回退、诊断导出和 analyzer 均有跨平台测试。
4. Mac 与 Windows VM 使用同一版本、同一 SHA 对应的精确候选资产完成内部 Beta 门禁。
5. 没有未解决的阻断级、高风险、数据正确性、隐私或稳定更新隔离缺陷。
6. 用户明确批准远端候选与公开操作后，GitHub Prerelease 已发布、非 Latest、稳定客户端不可见；
   若批准尚未提供，Goal 应停在批准门并标记 blocked，不能伪造完成。
7. 验收报告记录首次失败、修复、命令、测试数、OS/架构、SHA、run ID、资产摘要、seed、时长、
   资源趋势、诊断 schema、隐私扫描和剩余风险。

本 Goal 完成只表示通用 Beta 体系和首个实际 Beta 已通过规定门禁，不授权将双进程改动合并到
`master` 或发布正式稳定版。稳定切换仍需未来使用原计划的 24h/8h 正式认证与独立批准。

## 10. 停止条件

出现下列任一情况立即停止当前阶段并保留证据：

- master 集成产生无法通过现有语义和测试判定的冲突；
- 模式切换可能在 SMTP 不确定窗口诱导重发；
- 诊断 bundle 泄露禁止项、无界增长或影响业务正确性；
- Beta workflow 能修改稳定 Latest/feed，或无法证明稳定客户端隔离；
- 候选 SHA、run ID、资产摘要或平台证据不一致；
- 需要 push、远端 workflow、tag、Release、撤回或 master 合并但尚未获得相应批准。
