# Windows 11 Parallels 发布验收

这台 Mac 上有一台专用于 Auto Email Sender 发布前验收的 Parallels Desktop 虚拟机。它用于候选发布版本、Windows 安装包或 Windows 专属进程问题，不用于每次小改动后的日常测试。

## 固定环境

- 虚拟机名称：`Windows 11`
- 虚拟机 UUID：`{c56e66ee-22c9-4cdb-9878-2d98a532db9a}`
- 系统：Windows 11 ARM64
- Windows 用户：`junie`
- 本机仓库：`/Users/junie/Programs/AutoEmailSender`
- VM 内 NTFS 仓库：`C:\Users\junie\Projects\AutoEmailSender-Windows-QA`
- Parallels 命令：`/usr/local/bin/prlctl`
- 共享传输目录：Mac 的 `/Users/junie/Parallels Shared` 对应 Windows 的 `Z:\`（`\\Mac\Parallels Shared`）

不要直接在 `Z:` 共享盘上安装依赖或构建。Node、Python、SQLite 和打包工具应在 VM 的 NTFS 工作区中运行；共享盘只传输 Git bundle 和临时启动脚本。

宿主 runner 在传输前会创建临时探针，确认上述 Mac 目录和 Windows 路径确实指向同一共享目录。若以后修改共享名称或盘符，可分别通过 `AUTO_EMAIL_SENDER_WINDOWS_QA_HOST_TRANSFER_DIR` 和 `AUTO_EMAIL_SENDER_WINDOWS_QA_GUEST_TRANSFER_DIR` 覆盖默认值，不需要修改脚本。Parallels 只需共享这个专用目录，不需要开启桌面、文稿、下载目录或整个 Mac 用户目录共享。

VM 已长期配置 Git for Windows 2.55、Node.js 24、npm 11、`C:\Users\junie\DevTools\uv\uv.exe`、uv 管理的 Python 3.12，以及 Microsoft Visual C++ x64 Runtime。发布 QA 固定使用 `C:\Users\junie\DevTools\node-v24.19.0-win-x64`，使 Node、Python、Electron 和发布目标都按 x64 Windows 包验证；系统另有 ARM64 Node，但 runner 不使用它。Windows 打包会下载、校验微软签名并把官方 x64 Redistributable 放入 NSIS，由安装程序负责安装；VM 中预装运行库只用于开发工具链，不能替代安装包检查。

## 何时运行

以下情况必须运行真实 VM 验收：

- 准备创建发布 tag 或公开发布 Windows 安装包；
- 修改 Electron、PyInstaller、NSIS、CLI 安装、后端启动/退出或运行描述文件；
- 修改 Windows 路径、权限、SQLite 文件生命周期或原生依赖；
- CI 通过但需要确认真实 Windows 行为。

普通前后端逻辑小改动先运行本机聚焦测试和 CI，不要为每次小测试启动 VM。

不确定修改会使哪些证据失效时，先让脚本按 Git 变更生成最小重跑集合：

```bash
node scripts/release/release-impact.mjs --base <上次已认证SHA> --head HEAD
```

测试夹具、公告和发布编排分别只触发对应测试；只有 Windows 安装器、打包、运行时或原生依赖输入变化时才立即要求正式模式。对最终冻结候选加 `--candidate`，它会明确要求一次正式 Windows 验收和 macOS 候选认证。

开发过程中确实需要真实 Windows 验证、但还没有进入发布候选阶段时，可运行快速模式：

```bash
rtk bash scripts/quality/run-windows-vm-release-qa.sh --quick
```

快速模式仍会传输已提交的 `HEAD`，并按输入指纹运行或复用发布入口契约、前端、CLI、后端和桌面测试及冻结构建，但会跳过 VC++ 安装器准备、NSIS 构建和打包后的启动/运行身份生命周期。因此它适合日常 Windows 回归，不构成发布前验收结果。最终候选提交后、提升公开前，必须对同一 SHA 运行一次不带 `--quick` 的正式模式。

## 一键验收

先提交需要测试的代码。脚本只打包 `HEAD`，不会复制工作区中的未提交修改：

```bash
rtk bash scripts/quality/run-windows-vm-release-qa.sh \
  --candidate-installer /绝对路径/AutoEmailSender-Setup-<当前版本>.exe \
  --candidate-installer-sha256 <候选清单中的64位SHA-256> \
  --candidate-manifest /绝对路径/release-candidate.json \
  --candidate-run-id <候选workflow run ID> \
  --previous-installer /绝对路径/AutoEmailSender-Setup-<上一稳定版>.exe \
  --previous-installer-sha256 <上一稳定版公开NSIS的64位SHA-256>
```

正式模式强制要求候选 workflow 的确切当前 NSIS 和上一稳定版真实 NSIS。当前包摘要必须来自
同一 workflow 的 `release-candidate.json` 和 run ID；同 SHA 在 VM 中重新构建的包不能替代候选字节。
宿主 runner 从当前 SHA 最新可达的 `v*` tag 推导期望旧版本，先在宿主核对两包摘要，再把包
安全复制到专用共享目录；guest 复核后复制到本地 NTFS 暂存区并再次复核，Python driver 还会
在场景前后重算。Windows 启动旧应用后核对其实际版本，不能用任意旧包或与当前相同版本代替。
共享副本在 guest 命令结束后删除，原文件不变；guest-local 包随 QA 证据保留。

当前公开上一稳定版 v2.5.4 的 Windows NSIS SHA-256 为
`245aadcdf63ccae80913ede6a4cda9571884f83da9f23b957c724a6fb3b15d21`。后续稳定版发布后必须
从该 Release 的可信候选或资产清单取得新摘要，不能继续照抄此值。

API + Worker 改造的最终长稳认证在同一次安装会话中追加 24 小时 normal soak 和 8 小时
seeded chaos：

```bash
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

`--normal-soak-seconds` 不得低于 86400，`--seeded-chaos-seconds` 不得低于 28800；短时调试
只能用源码/开发 smoke，不能降低正式 runner 的门槛。`--quick` 与长稳参数互斥。

宿主脚本会确认 VM，并让 Windows 更新本地 NTFS checkout：首次运行传输完整 Git bundle，已有基线时只传增量对象，目标提交已经存在时不再传 bundle。随后运行需要更新的依赖与发布构建阶段并删除临时传输文件。Windows checkout 有 tracked 修改时会安全停止，不会执行 `reset --hard`。

VM 会按 Git tree 内容、Node/npm/uv/Python 工具链和必需输出记录已成功阶段。输入完全一致时，后续运行可复用发布 PowerShell 入口契约、前端构建、CLI/后端测试与冻结包、桌面依赖和测试；后端全套测试只在 `backend/**` 或工具链变化时重跑，发布脚本、打包脚本和仓库 Skill 变化只重跑对应的聚焦契约测试。任何相关文件或工具版本变化都会自动重跑对应阶段。正式模式的 NSIS 安装器和打包后运行时生命周期不使用阶段缓存，但只对冻结后的最终候选运行一次；纯公告修改不应重新运行正式 VM。快速模式明确跳过二者。需要排查缓存或周期性做全新基线时运行：

```bash
rtk bash scripts/quality/run-windows-vm-release-qa.sh --quick --force-full
```

`--force-full` 只忽略可缓存阶段的验证记录；它不会改变正式/快速模式边界。正式模式需要把它
追加到上面的完整候选/旧版参数命令，不能省略包来源参数。`npm ci` 仍使用 npm 下载缓存，
Playwright Chromium 按锁定版本保存在专用 QA checkout 中并由安装命令重新核对，不再为每个
提交强制下载同一个浏览器。

Windows 侧会先结束可执行路径位于专用 QA checkout 内的残留应用进程，避免上一次中止的验收锁住冻结包；不会按进程名清理 checkout 外的程序。随后执行：

1. 先运行 PowerShell prepare/release 入口契约；只要发布脚本、workflow 或 Release Skill 输入不变，后续可复用该结果；
2. 正式模式下载并校验微软签名的 VC++ x64 Runtime，使环境或 PowerShell 模块问题在昂贵测试前失败；
3. 前端 `npm ci`、Rolldown 当前架构原生绑定检查和 Vite 生产构建；
4. CLI 全部测试、干净 PyInstaller 构建和冻结版本校验；
5. 后端全部测试、Playwright 运行时核对、干净 PyInstaller 构建及自检；
6. Electron `npm ci`、类型检查、全部桌面测试和本地 NSIS 构建合同；本地重建包不作为后续
   lifecycle 的候选资产；
7. 把已核对摘要的候选/旧版 NSIS 从共享目录复制到 guest-local NTFS；在 `%TEMP%` 下含中文、
   空格和 Ω 的专用路径静默安装上一稳定版，通过其 Agent API 写入设置、导师和材料，再把
   确切候选 NSIS 覆盖安装到同一路径；
8. 对已安装应用运行 packaged lifecycle：验证 split API/Worker 身份、原生 Windows
   sleep/wake 事件、Worker/API replacement、多实例、真实 Playwright 后代清理、combined
   回退、快速退出、卸载后用户数据保留；
9. 若指定长稳参数，使用同一安装产物真实驱动 Dispatcher、双 IMAP、Batch Draft、Matching
   和 Crawler。seeded chaos 包含 API/Worker kill、网络 flap、SQLite lock、suspend/resume、
   时间跳变和原生系统休眠；报告记录安装包/artifact SHA-256、轨迹、数据库和资源审计；
10. 最后静默卸载当前应用，确认可执行文件已移除、每个隔离 userData 数据库仍存在，并在
    `finally` 中结束可执行路径位于专用安装根内的所有 QA 进程。

失败会阻止发布。先判断是产品缺陷、锁文件/打包缺陷还是 VM 环境损坏，不要手工向 `node_modules` 塞包后把结果记为通过。VM runner 为 Electron 与 electron-builder 使用可访问的镜像，但 npm 依赖仍严格来自锁文件；修改镜像不能掩盖校验和或架构错误。

## 数据与清理

此 VM 是专用 QA 机器。packaged lifecycle 不使用默认
`%APPDATA%\auto-email-sender-desktop`，而是通过启动前 fail-closed gate 固定到带专用 marker
的临时 userData；其中只配置 loopback fake SMTP/IMAP/LLM/HTTP，不访问真实邮箱或外部服务。
不要在 VM 中配置真实邮箱、导师资料或 API 密钥，也不要把默认数据目录当作测试夹具。

脚本强制结束测试应用进程树，以复现意外退出和旧描述文件场景。下一次启动必须清理旧身份并发布新的 v3 描述文件。原生 sleep/wake 还要求 Windows 记录 Kernel-Power 42 和
Power-Troubleshooter 1，唤醒后 runtime id、API PID、Worker PID 不变且 heartbeat 推进。
测试后确认没有 `Auto Email Sender.exe`、`backend.exe`、Playwright Chromium 或测试 CLI
残留进程；报告和临时安装目录保留用于调查，不得把其中的失败报告覆盖为成功。

真实 Ctrl+C 控制台路径在修改启动/退出代码时额外执行：从 Windows Terminal 启动开发版，按 Ctrl+C 后确认 Electron、uv/Python 后端均退出；随后 CLI 必须报告 `stopped`，重新启动必须生成新的 `runtime_id`。强制退出测试不能替代这项人工控制台检查。

## 环境恢复

如果工具丢失，恢复顺序如下：

1. 用 `winget` 安装 Git for Windows，并把官方 Node.js 24 x64 ZIP 固定到 `C:\Users\junie\DevTools\node-v24.19.0-win-x64`；
2. 把官方 x64 `uv.exe` 固定到 `C:\Users\junie\DevTools\uv` 并加入用户 PATH；
3. 执行 `uv python install 3.12`；
4. 安装 Microsoft Visual C++ 2015–2022 x64 Redistributable；
5. 删除损坏的 VM checkout 后，重新运行宿主脚本创建它。删除前先确认目录确实是专用 QA checkout 且没有需要保留的改动。

`greenlet` 报缺少 `MSVCP140.dll` 时说明 x64 VC++ Runtime 不完整。前端找不到 Rolldown 绑定时先检查 `node -p "process.arch"`，再确认全新 `npm ci` 是否安装了对应的 `@rolldown/binding-win32-<arch>-msvc`；不要把 ARM64 和 x64 绑定混用。
