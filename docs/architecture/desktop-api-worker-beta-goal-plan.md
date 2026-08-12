# 桌面 API + Worker 通用 Beta 验证 Goal 执行计划

- 状态：Goal 已启动（B0～B4R 已完成；Windows harness rehearsal 因新增原生休眠协议重新打开）
- 当前 Goal ID：`019fe582-2dea-7e42-bd2e-684bae191421`（2026-08-12 重新创建并激活）
- 建立日期：2026-08-10
- 前置实现计划：[`desktop-api-worker-process-plan.md`](./desktop-api-worker-process-plan.md)
- 前置验收证据：[`desktop_api_worker_goal_acceptance.md`](../development/desktop_api_worker_goal_acceptance.md)
- 本 Goal 验收报告：[`desktop_api_worker_beta_goal_acceptance.md`](../development/desktop_api_worker_beta_goal_acceptance.md)

## 1. Goal 定义

### 1.1 可直接用于 Goal 模式的目标

```text
严格按照 docs/architecture/desktop-api-worker-beta-goal-plan.md 完成首个桌面 API + Worker Beta。
先把冻结的最新 origin/master 合入 beta/desktop-api-worker；所有产品功能、数据模型、迁移、API、
前端交互和业务测试以 master 为准，Beta 分支只在其上保留双进程拓扑、进程安全、combined 回退、
本地诊断和 prerelease 发布适配，不保留与 master 冲突的旧业务实现。完成冲突聚焦回归和一轮全仓，
再完成双平台 harness rehearsal；冻结同一 SHA 后按已授予的一次性全流程授权 push 来源分支并
dispatch Prerelease Certify。只使用该 run 的原始 EXE、DMG 和 manifest，依次完成双平台 exact
admission、覆盖升级、lifecycle、每平台 300 秒 normal + 300 秒 seeded chaos 密集门禁和诊断重建。
全部必选 AC 通过且没有阻断/高风险/数据正确性/隐私/稳定更新隔离缺陷后，按同一授权创建不可变
tag 并公开为非 Latest GitHub Prerelease，最后证明稳定 Latest/feed 不变且 v2.5.4 两平台客户端
检查更新均看不到 Beta，收口证据后完成 Goal。不得把 Beta 合回 master、修改稳定 feed 或发布
稳定版本。
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
- B0 开始时当前版本和最新稳定版均为 `2.5.4`；B5 已选择首个候选版本
  `2.6.0-beta.1`。首个远端 candidate run `31417575421` 已完成构建认证，但随后在 Windows
  exact-package 入口发现 QA 传输层改名缺陷，因此该 run 已拒绝用于发布并只保留为失败证据。
- run `31417575421` 的原始 DMG、NSIS 和 `prerelease-candidate.json` 已下载到仓库外并复核摘要；
  它们不得与修复后的 SHA 或任何替代 run 混用。

### 1.3 Goal 当前检查点（2026-08-12）

- 旧 Goal 曾被意外取消；本计划校验通过后已于 2026-08-12 重新创建活动 Goal。Goal 系统沿用
  task ID `019fe582-2dea-7e42-bd2e-684bae191421`，不沿用旧 Goal 的取消状态。
- 本计划更新前，本地分支为 `beta/desktop-api-worker`，HEAD 为
  `bfb679869a23313b71d25a985178239e9f1dd641`，工作区干净。远端来源分支仍停在
  `45c5d5f8eb6b707f4ed905b3d697be5b6e1b0608`，当前本地提交尚未 push。
- 最新 `origin/master` 为 `3c1e064dceac0917a966cb510385856fc9fe7ea1`；当前分支相对它 behind
  16、ahead 62。只读 merge-tree 预测 8 个冲突：模型导出、delivery attempt、通信 transport、
  delivery task、三个后端测试和 `TasksPage.tsx`。B4 的旧 master 证据继续作为前置基线，但
  AC-BRANCH-02 在 B4R 重新通过前视为打开。
- 历史 B4 基线：2026-08-11 重新 fetch 后，当时的
  `origin/master` 仍为 `2fcc431d25ba36b1de6380bb316589a750cebc2f`；它已通过 merge commit
  `e313811528adc407211cfd8aa6f68e6a3c84749d` 合入。当前分支相对 master 为 behind 0，merge-base
  精确为该 master；ahead 会随本地 failure-recovery 与证据提交增长。该分支名只描述本次开发工作，
  不会成为通用测试版发布条件。
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
- 用户曾分别批准将冻结 SHA `bd19519d24cf372cd7a6d3e766a2c173c74ff2c6` push 到
  `beta/desktop-api-worker` 并 dispatch Prerelease Certify；run `31417575421` 全部认证 job
  通过且 publish job 按设计跳过，没有创建 tag 或 Release。该两项授权已执行完毕，不自动延伸到
  后续替代 SHA。
- 首次 Windows exact-package 运行在安装前失败：host 已成功绑定原始
  `AutoEmailSender-Setup-2.6.0-beta.1.exe`，但 transfer staging 将同一字节改名为
  `AutoEmailSender-Candidate-<pid>.exe`，guest 的第二次 manifest 校验因此正确拒绝。失败证据已
  保留，run `31417575421` 不再是可发布候选。
- `bd5e52e1bff20e73a2d322ef1fbfccb0899a3b3d` 用每次运行唯一的临时共享目录保留 manifest 原始
  文件名，并增加禁止传输改名的回归合同。macOS/Windows 本机合同和真实 Windows quick QA
  均通过；quick 仍不替代同一替代 workflow 原始 DMG/EXE 的覆盖升级、lifecycle 和每平台
  5 分钟 normal + 5 分钟 seeded chaos 密集门禁。
- 用户又分别批准 push `908dfa9` 与 replacement Certify；run `31453411547` 双平台构建和
  candidate certify 成功，未创建 tag/Release。Windows 正式 QA 在候选安装前发现 v2.5.4
  settings seed 少传 14 个必填字段；后续真实重放又发现安装树哈希未使用 Windows 扩展长度路径。
- `61bdbeb`、`402d9db`、`27bd475` 依次修复完整 settings round-trip、启动前 artifact snapshot
  与 Windows extended-length drive/UNC 长路径哈希。聚焦合同 21/21、最终 Backend 1960/1960
  和真实 v2.5.4
  seed 重放通过，VM 零残留并恢复 suspended；这些只关闭 failure-recovery，不替代 formal QA。
- 用户随后批准 push `45c5d5f` 与再次 dispatch replacement Certify；run `31464156897` 双平台
  build/certify 成功且未创建 tag/Release。Windows 正式 QA 的 Backend 1960/1960、冻结三角色和
  本地 NSIS 通过，但 v2.5.4 安装器在候选安装前被上一失败 QA 的卸载注册表阻塞，并因 runner
  无 timeout 持续等待；首个现场已保存，该 run 不构成正式证据。
- `b6381e1` 只清理专用 QA 临时根的 stale uninstall 项，为安装/卸载增加有界进程树终止，并让
  host runner 恢复自己启动的 VM。Windows 5.1 synthetic/真实 stale 探针、Desktop/发布合同和
  精确 SHA quick QA 均通过，VM 自动恢复 suspended。
- 连续失败复盘确认，确定性缺陷集中在候选传输、旧版 seed、Windows 长路径、VC++ 下载和
  安装器恢复边界，而旧 runner 在首次真实安装前先执行整套源码/冻结构建。B5 因此先增加通用
  `harness-rehearsal → candidate-admission → formal certification` 三层门禁；完成双平台演练前不再
  申请替代 candidate。run `31464156897` 只允许作为 rehearsal 的失效包，不能重新成为候选证据。
- `bfb6798` 已把测试版正式运行门禁调整为每平台 5 分钟 normal + 5 分钟 seeded chaos，并强制
  10 秒资源采样、5 秒故障动作和全部 chaos 动作覆盖；稳定版 24h/8h 标准不变。
- 用户已在 2026-08-12 一次性批准完成本 Goal 所需的来源分支 push、Prerelease Certify dispatch、
  prerelease tag 创建/推送和通过全部门禁后的公开 GitHub Prerelease，不再设置重复人工暂停点。
- B4R 已冻结并合入 `origin/master@3c1e064dceac0917a966cb510385856fc9fe7ea1`。合并提交为
  `306d841`，聚焦适配提交为 `0ac0be6`；业务模型、API、迁移和页面行为以 master 为准，Beta
  只保留双进程适配。Alembic 单一 head、70 项迁移/数据库测试、聚焦邮件/IMAP/进程安全测试、
  Frontend lint 和 Desktop typecheck 均通过；合并后全仓仅暴露 5 个确定性测试适配问题，均已
  聚焦修复并通过，CLI 237/237、Frontend、Desktop、Website 全部通过。
- B5 rehearsal 已证明两平台第一轮受控中断和清理路径有效。macOS 第二轮已经通过覆盖升级、
  split 身份、Worker 无监听端口、进程替换、数据库审计等前置门，随后因 browser probe 未创建
  LLM profile 而失败；`cf5d164` 已只在 QA harness 中补建 loopback FakeLLM profile，并通过
  packaged runtime 合同 30/30、Ruff 和 compile。Windows 旧本地包在恢复后未能形成完整 split
  runtime，禁止继续盲目重试；必须使用当前 HEAD 构建的 rehearsal-only 包完成两轮演练。
- 当前 HEAD 的 Windows 第二轮 rehearsal 已通过 stale/timeout 恢复、覆盖升级、split/combined
  lifecycle、迁移备份、浏览器后代、数据库审计和强杀恢复，最后只在“优雅退出”失败。现场证明
  `taskkill /PID /T` 对 GUI 应用只能强杀，不能触发 Electron `before-quit`，因此这是 QA harness
  缺陷而非产品退出缺陷。修复只在 Windows 且 packaged QA nonce/sentinel/userData 全授权后注册
  `WM_APP` 消息；runner 枚举目标 PID 顶层窗口并为快速启动有界重试，普通用户启动不暴露入口。
  本机聚焦和完整检查已通过：Desktop 258 passed/3 skipped，packaged runtime QA 35 passed/3
  platform skipped，TypeScript typecheck、Ruff、Python compile 与 diff check 均通过。下一步只构建
  当前 HEAD rehearsal EXE 并重跑 Windows 两轮检查点演练；macOS 是否重跑由 release impact 判定。
- `508119f` 的新包已证明正常 split/combined 优雅退出分别约 0.64s/2.21s；100ms rapid-exit 随后
  暴露 runner 把“消息投递成功”误当成“应用退出成功”。BrowserWindow 创建前同 PID 已存在其他
  顶层窗口，首条消息可能被无 hook 的窗口接收。修复只改 runner：投递后继续以 50ms 间隔重试，
  直到目标 PID 真正退出或 20 秒硬上限；安装包字节未变，只重放受影响的第二轮 lifecycle，不重建
  NSIS、不重跑 Backend/Desktop/VC++/第一轮中断。
- 新 harness 的真实 rapid-exit 已在约 1.10s 通过，最终数据库审计和全部 19 项 lifecycle check
  也通过；随后 PowerShell 5.1 用默认 ANSI 编码读取无 BOM UTF-8 `report.json`，中文路径被破坏并
  使 `ConvertFrom-Json` 失败。runner 现用严格 UTF-8 helper 读取所有 JSON 文件；这只影响证据
  后处理，须重放第二轮直到卸载、重复安装和报告分层检查也成功。
- UTF-8 后处理重放成功进入卸载，但固定 2 秒后主 EXE 尚在；只读现场不到 1 秒后安装根已完全
  消失且无进程，证明 NSIS 临时卸载子进程晚于父进程返回，而非卸载失败。runner 改为每 200ms
  轮询主 EXE 消失并设 30 秒硬上限，首次和重复卸载共用该门禁。
- Windows 最终双轮 rehearsal 已在 `e23b6e7` 完整通过。第一轮复用并重新校验 v2.5.4 seed 后按
  计划中断；第二轮恢复 3 个进程和 1 个专用注册项，1.2s timeout、覆盖升级、19 项 lifecycle、
  5 次数据库审计、split/combined、浏览器后代、强杀恢复、rapid-exit、严格 UTF-8 报告、首次
  卸载、重复安装和重复卸载全部通过，资源违规为空。报告绑定 rehearsal EXE SHA-256
  `7f519fe9c76f401d2f9092b720a59a024ef4ec26efca76a0b698c4298bb251ae` 和公开 v2.5.4 EXE 摘要。
- macOS 修复后 rehearsal 报告同样为 19 项全部通过、零资源违规，DMG SHA-256 为
  `93c6aa4e6f1fb15022ec05f505a57355b81de68d9d381774ea50739c86b6dac5`。报告和 DMG 均晚于 browser
  probe 修复；`release-impact.mjs --base cf5d164 --head e23b6e7` 明确允许跳过 macOS candidate，
  后续变化仅为 Win32 Electron、Windows Python 分支和 PowerShell runner。因此 AC-BETA-QA-00
  关闭，下一步冻结 clean SHA 并 Certify，不再重跑 rehearsal。
- 冻结 SHA `3902d11434751bf309153568dae23a8214883731` 的 prerelease run `31551840527` 已通过
  contract、preflight、Windows/macOS build 和 certify，publish 按设计跳过；原始 EXE 摘要为
  `d8e42aa4d9b29a3cc1cf3cad4b564e42946cae2f678e5d5544776cc8bae9502c`，DMG 摘要为
  `969cd91e3766418ab2c23b2d6bbd553e11874a0043289f060070978814c79049`。首次 Windows admission
  证明候选身份、v2.5.4 覆盖升级、迁移备份、split 身份、Worker 无监听端口、本地诊断导出和
  同库读写均通过；随后因 Parallels ARM64 固件只支持 S0 Modern Standby、标准 S3
  `SetSuspendState` 返回 `ERROR_NOT_SUPPORTED(50)` 而停止。该 run 保留为失败现场，不再作为
  最终候选。
- Windows QA 的 S0-only VM 电源适配只在原生 S3 明确返回 error 50 时使用系统真实休眠；host
  必须观察到 guest 写入休眠握手、VM 进入 stopped 后才重新启动。实机探针已证明休眠前后的
  PowerShell PID 同为 `1664`，并产生 Kernel-Power 42、Power-Troubleshooter 1。最终报告仍要求
  runtime id、API/Worker PID 不变、Worker heartbeat 推进和 API 读写通过；普通失败、VM pause 或
  没有握手的停止不能进入此后备路径。Parallels `exec` 会在休眠时断开，因此 guest wrapper 以
  原子 UTF-8 状态文件向 host 回传最终退出码，不能把连接断开当作成功。
- 精确 `fd5df8c` 的 Windows quick QA 在 Backend 末段发现不服从取消的 LLM 测试以固定 20ms 等待
  Windows SQLite 收尾，触发 `WinError 32`。`f73a8a6` 只等待迟到任务真实完成并排空事件循环，
  不修改业务逻辑；本机连续 5 次与 Windows 聚焦单测通过。impact 只要求 Backend suite并明确
  跳过双平台 formal/candidate，本机 Backend 2031/2031 已通过。下一步形成证据提交并以新的
  clean SHA 执行 Certify；不再重跑无关安装、Desktop 或 macOS 阶段。
- run `31564882972` 的 Windows admission 证明 exact binding、旧版 seed、覆盖升级和 split ready 后，
  在原生休眠处暴露 host 先收到 `prlctl exec=255`、共享握手稍后可见的监督竞态；恢复后同一组产品
  和 driver 进程仍存活，故不是产品失败。`d77835d` 为断连/VM stopped 两种顺序增加 15 秒握手
  收敛窗口，并保留“握手 + 真实 stopped + 次数上限”硬条件。真实探针已以同一 PID `10684`
  完成 hibernate/resume；新冻结 SHA 必须重新 Certify，旧 run 不得作为最终证据。
- run `31567826340` 证明单纯 host 宽限仍不足：Python 写共享 requested 后可在 host 可见前完成
  `shutdown /h`。最终协议使用唯一 `request_id` 的 requested → host acknowledged → hibernate；
  guest 只接受匹配 ACK，每轮清旧文件，host 重写不匹配 ACK。真实 Python 快速写探针已以同一
  PID `5460` 和同一 request ID 完成 stopped/resume；完整 packaged/desktop/release 聚焦合同通过。
  必须以包含该协议的新 SHA Certify，旧 run 继续只作失败证据。
- 冻结 SHA `1be5a41a0a45f678202f43fe01dff9a569349ef4` 的 run `31570525027` 已通过全部远端
  contract、preflight、Windows/macOS build 和 certify；EXE SHA-256 为
  `a560b7b752d373afa309eee17edd8a03ca83ae999e5a2502701d5d07beb9fdcf`，DMG SHA-256 为
  `5eb74a1eea441a861d09197876a97610d0692e049bb803011e50d8a65a5e60ec`。Windows admission 已通过
  stale/timeout 恢复、公开 v2.5.4 安装与 seed、候选覆盖、split/API/Worker ready，进入原生休眠后
  guest 确实先收到匹配 ACK 并使 VM stopped；但 stopped 瞬间 request 文件在 host 共享视图短暂
  不可见，host 未保留已 ACK 的 request ID，因而把合法休眠误判为无握手停止。该 run 继续只作
  失败证据，不得进入后续 admission/formal/publish。
- host 监督器现将已验证并 ACK 的唯一 request ID 保存在内存中；共享文件短暂不可见时只允许该
  pending ID 驱动一次真实 stopped/resume，恢复后必须看到同 ID 的 resumed 才清除。没有已 ACK
  pending ID 的普通 VM 停止仍按原 15 秒上限失败，次数上限保持不变。真实自动探针得到
  `prlctl exec=255`，同一 Python PID `5712` 和 request ID
  `d936d204-7430-4e1c-a50f-b8886b8d75b3` 跨 stopped/start 完全一致；新冻结 SHA 只需按 impact
  运行受影响合同并重新 Certify，不重复无关全仓或旧失败候选。
- 同一冻结 SHA 的 Windows quick QA 内部已完成 Backend 2032/2032（7 skip）、冻结 API/Worker/
  combined/document 自检和 Desktop 250/250（11 skip），并打印最终 passed；宿主随后一次性回显整份
  UTF-16 日志，终端背压令 `iconv`/`cat` 失败并覆盖了真实成功状态。host 现先解析 wrapper 原子
  status，再按成功 200 行、失败 2000 行有界回显，日志显示失败不再覆盖 QA 退出码；重放必须复用
  已成功阶段，不重复 28 分钟 Backend。
- run `31576240231` 已为 `01f9180dbd0fb40c0d8155fa29112bd5281b46ea` 成功生成 exact candidate；
  EXE SHA-256 `79ffdfc1987194053d4c5868c9cce67527125d9679c8c37ea36feedccb8484d5`，DMG SHA-256
  `c862f47e1ca69e53127ac5453fd7f5315d0b958e36700f4500ab30bbaba4da2a`。Windows admission 再次
  通过旧版 seed、覆盖和 split ready 后进入休眠，但 host 在 stopped 前已看到 guest 写出的 resumed
  并清除了 pending，随后把真实 stopped 误判为无握手。这证明 Windows 的 `shutdown /h` 可在 VM
  真正 stopped/start 前返回；同 ID resumed 本身不足以证明 host 已完成恢复，该 run 失效。
- 协议新增同 ID `restarted` 阶段：guest 在 `shutdown /h` 返回后必须等待 host 亲眼观察 stopped、
  执行 `prlctl start` 并写出 restarted，之后才允许写 resumed；host 也只有在自身记录 restarted 后
  才接受 resumed。秒级顺序合同和真实探针均通过，后者为
  `ACK -> exec=255 -> stopped/start -> restarted -> resumed`，Python PID 始终为 `10824`，request ID
  始终为 `54b1a9a6-a3d5-4974-844b-6170bfe3cbc7`。再次申请 candidate 前必须先用失效包完成
  non-certifying rehearsal，禁止直接消耗新构建。
- 复核旧 rehearsal 报告发现 Windows `harness-rehearsal` 模式没有传
  `--system-sleep-wake`/`--windows-hibernate-handshake-dir`；旧报告的 19 项 lifecycle 因而不能
  证明后来新增的 `requested -> acknowledged -> stopped/start -> restarted -> resumed` 协议。
  QA harness 现要求所有 packaged lifecycle（包括 rehearsal）都建立宿主握手目录，且 rehearsal
  lifecycle 必须执行原生 sleep/wake；它仍拒绝 candidate manifest/run ID。聚焦 packaging 24/24、
  packaged-runtime 完整合同、Desktop 全套、typecheck、Bash syntax 和 diff check 均通过。
  AC-BETA-QA-00 的 Windows 部分重新打开，须用失效 run `31576240231` 的 EXE 完成故意中断与恢复
  两轮并在最终报告看到 sleep/wake 检查；macOS 证据不受 Windows-only harness 变化影响。
- 首次接入 native sleep 的 rehearsal 第二轮在约 20 秒内暴露另一条此前未覆盖的真实路径：本次
  Windows `SetSuspendState` 直接成功，VM 进入 stopped 且恢复后系统日志有 Kernel-Power 42 和
  Power-Troubleshooter 1，同一 Electron/API/Worker/Python 进程组仍存活；但原 handshake 只在
  S3 返回 error 50 后的 hibernate fallback 创建，host 因而在 15 秒内正确拒绝“无握手停止”。
  driver 现于任何 Windows 原生睡眠调用前统一执行 requested/ACK，S3 成功和 error 50 fallback
  共用同一 request ID，恢复后都必须等待 host stopped/start/restarted 才写 resumed；其他 S3
  错误仍硬失败。新增 S3 顺序合同及完整 packaged-runtime 40 passed/3 skipped、packaging 24/24、
  Ruff、compile、Bash syntax 和 diff check 均通过；须从新提交重新计数 Windows 两轮 rehearsal。
- `d1b8722` 的重跑已证明统一 S3 协议本身成功：request ID
  `fa29a09a-9fc6-44e0-adb8-087bb10d781e` 严格完成 ACK、VM stopped/start、restarted、resumed；
  driver 报告 20 项全部通过，原生 S3 20.019 秒、Kernel-Power 42/Power-Troubleshooter 1、原
  runtime/API/Worker PID、heartbeat 和数据库审计均通过，资产摘要前后不变。外层仍退出 1 的根因
  是 wrapper 将实时 UTF-16 日志 `Tee` 到 Parallels 共享目录，睡眠期间映射断开令 Tee 失败；driver
  继续完成，首次卸载和数据保留也已发生。wrapper 现先写 guest 本地 `%TEMP%`，runner 结束后再
  一次性复制共享输出；host 同时记住 completed request ID，禁止旧 requested 文件反复重置 pending。
  该传输层修复仍须用新提交重新执行双轮，不拼接已有报告。

### 1.4 本次一次性授权边界

本 Goal 已获一次性授权执行以下远端动作，不需要再次暂停询问：

1. push 本 Goal 最终冻结的 `beta/desktop-api-worker` 来源分支；
2. dispatch 与该最终 SHA 精确绑定的 Prerelease Certify workflow；
3. 在全部双平台 exact-package AC 通过后创建并推送不可变 prerelease tag；
4. 将同一 candidate run 的原始资产公开为 GitHub Prerelease，且 `Latest=false`。

授权不包括：把 Beta 分支合回 `master`、修改稳定更新 feed、发布稳定版、删除/覆盖公开资产，或
绕过失败门禁。若出现这些新范围、需要破坏性恢复，或出现无法由代码/测试判定的产品决策，才停止
请求新授权。

### 1.5 master 业务优先的合并规则

- 冻结本次 `origin/master@3c1e064` 后只合入一次；Beta 发布前不再追逐普通 master 提交，只有
  安全、数据损坏或发布阻断修复才重新评估。
- 冲突文件中的业务模型、字段、迁移、API 合同、状态机、UI 行为和测试预期以 master 为准；先
  还原 master 的完整业务结果，再移植 split 进程边界、租约/fencing、诊断和打包所需的最小适配。
- 不用 Beta 旧实现覆盖 master 的邮件投递对账、全局材料库、任务页面拆分或其他新业务；不删除
  master migration/DDL。出现双 Alembic head 时只增加无 DDL 的 merge revision。
- 合并后先运行 8 个冲突文件对应测试，再运行迁移、邮件投递、IMAP、Worker 进程安全和 TasksPage
  聚焦回归；聚焦通过后只运行一轮完整全仓。失败先定位和重放聚焦场景，不从头重复所有成功阶段。

### 1.6 明确不在本 Goal 内的事项

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

公开首个 Beta 前，两平台各自使用精确候选完成 packaged lifecycle、连续 5 分钟 normal soak 和
连续 5 分钟 seeded chaos。资源每 10 秒采样、故障动作至多间隔 5 秒，全部声明的 chaos 动作、
六类工作和不变量必须通过。该短时密集门禁不替代未来稳定版 24h/8h 正式认证。

## 7. 分阶段执行

| 阶段 | 范围 | 完成条件 |
| --- | --- | --- |
| B0 | 文档、当前工作保护、分支与 master 集成 | **已完成**：前置快照 `e062f36`；master 合并 `c51df44`；合并修复 `4fe1bdf` |
| B1 | Electron 模式设置、UI、安全重启与页面外回退 | **已完成**：AC-MODE 全部通过；combined/split 同库回归和 macOS 隔离真机故障回退通过 |
| B2 | 本地记录器、诊断 ZIP、脱敏与 analyzer | **已完成**：AC-OBS/PRIV 全部通过；后端宕机仍能导出 partial bundle |
| B3 | 通用 prerelease Skill、脚本、workflow 与合同测试 | **已完成**：AC-BRANCH-03/AC-REL 全部通过；未触及稳定 feed |
| B4 | 合并后的全仓与重复专项回归 | **已完成**：`origin/master@2fcc431` 已通过 `e313811` 合入；最终产品代码 `2123af5` 全仓连续 2 次、split 集成连续 20 次通过 |
| B4R | 冻结并同步最新 master | **已完成**：`origin/master@3c1e064` 通过 `306d841` 语义合入；业务逻辑以 master 为准，聚焦回归和一轮全仓已通过 |
| B5 | 本地候选、Mac/Windows exact-package Dogfood | **执行中**：macOS rehearsal 已完成；Windows rehearsal 因新增四阶段原生休眠协议重新执行，随后才冻结新 run 并串行完成双平台 exact admission、正式 lifecycle、每平台 5 分钟 normal + 5 分钟 chaos 和诊断重建 |
| B6 | 远端候选与公开 Prerelease | **已预授权**：B4R/B5 门禁通过后直接 push、dispatch、publish；AC-ISO 全部通过 |
| B7 | 证据收口与观察交接 | 报告包含所有命令、SHA、资产摘要、seed、资源和已知限制 |

### 7.1 本次恢复后的固定执行顺序

1. **B4R**：冻结 `origin/master@3c1e064` 并语义合入；业务逻辑以 master 为准，只移植双进程适配。
   完成冲突聚焦、迁移/邮件/split 高风险回归和一轮完整全仓，提交并记录 impact。
2. **B5 rehearsal**：在 Windows 和 macOS 各执行“故意中断 → 立即重跑”，证明 stale
   注册表/进程或 DMG 挂载与超时自动恢复；失败先按产品、包装或 harness 分类，只修复和重放
   `release-impact.mjs` 判定受影响的阶段，不重复已通过且输入未变的源码、VC++ 或平台门禁。
3. **冻结与 Certify**：完成公告同步、preflight、certify dry-run 和 clean SHA；按现有授权 push
   `beta/desktop-api-worker` 并 dispatch Prerelease Certify，确认 workflow head SHA 精确一致。
4. **Exact candidate QA**：下载并复核同一 run 的 EXE、DMG、manifest；依次运行 Windows
   admission、macOS admission、Windows lifecycle + 300s/300s、macOS lifecycle + 300s/300s，
   分析最终诊断包并要求全部不变量通过。
5. **Publish 与隔离**：按现有授权 publish 同一 candidate run；验证 tag/SHA/资产、非 Latest、
   稳定 feed 摘要不变，并在 v2.5.4 Windows/macOS 客户端真实检查更新确认看不到 Beta。
6. **B7**：更新验收报告，记录冲突决策、命令、测试数、run ID、摘要、seed、失败与剩余风险；
   清理隔离测试进程并完成 Goal，不合回 `master`、不发布稳定版。

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

- **AC-BETA-QA-00**：两平台 harness rehearsal 均完成故意中断后的立即重跑；Windows 证明专用
  stale 注册表与进程被精确清理、1 秒超时子树有界退出，macOS 证明中断时挂载的 DMG 已自动卸载。
- **AC-BETA-QA-00A**：新 run 的 Windows/macOS admission 均绑定同一 manifest/run/SHA/摘要，
  在昂贵正式门禁前通过覆盖升级、split/combined、迁移完整性、诊断导出、sleep/wake、卸载与重装；
  两份报告明确不可用于认证。
- **AC-BETA-QA-01**：同一候选资产在 macOS 和 Windows 完成 v2.5.4 覆盖升级与完整 lifecycle。
- **AC-BETA-QA-02**：两平台各自单次连续 ≥300s normal soak、≥300s seeded chaos，双时钟达标；
  采样间隔 ≤10s、故障动作间隔 ≤5s，全部声明的 chaos 动作均至少完成一次。
- **AC-BETA-QA-03**：故障轨迹、进程恢复、资源趋势、数据库与任务不变量能由用户同格式 bundle 重建。
- **AC-BETA-QA-04**：零数据损坏、零重复 SMTP DATA、零孤儿进程、零未解释退出。

## 9. Definition of Done

只有同时满足以下条件才能完成本 Goal：

1. B0～B7 完成，全部必选 AC 有可复现证据且通过。
2. 合入冻结的最新 `master` 后运行一次完整全仓测试，并对冲突文件、迁移、邮件投递和 split
   进程安全执行聚焦重复回归；此前连续两次全仓和 20 次 split 证据继续作为前置基线。
3. combined/split 设置、安全重启、页面外回退、诊断导出和 analyzer 均有跨平台测试。
4. Mac 与 Windows VM 使用同一版本、同一 SHA 对应的精确候选资产完成内部 Beta 门禁。
5. 没有未解决的阻断级、高风险、数据正确性、隐私或稳定更新隔离缺陷。
6. 已使用用户 2026-08-12 的一次性授权完成精确候选 push/certify/publish；GitHub Prerelease 已
   发布、非 Latest，稳定客户端不可见。
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
- 需要把 Beta 合回 master、发布稳定版、删除/覆盖公开资产或执行其他未获授权的新范围。
