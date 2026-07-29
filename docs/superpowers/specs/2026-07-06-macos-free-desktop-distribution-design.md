# macOS 免费桌面分发设计

## 背景

Auto Email Sender 已有 Windows 桌面版：Electron 壳启动本地 FastAPI 后端，前端通过 preload API 访问本地服务，并通过 GitHub Releases 做应用内更新。现有桌面打包配置、发布脚本和 CI 都偏 Windows，macOS 尚未形成可面向普通用户发布的安装包。

本设计选择免费 macOS 分发路线：不购买 Apple Developer Program，不配置 Developer ID 证书，不做 notarization。应用可以通过 GitHub Releases 发布 `.dmg`，普通用户首次打开时按 macOS 的未验证开发者放行流程操作。Windows 现有安装和自动更新行为保持不变。

## 目标

- 生成可供普通 macOS 用户下载的 `dmg` 安装包。
- 保持 macOS 桌面版的核心功能与 Windows 一致：本地后端自动启动、托盘、关闭隐藏、开机自启、材料打开、外链打开和桌面启动状态提示。
- macOS 端通过 Sparkle 原生窗口自动检查更新，并在用户确认后下载、替换和重启安装。
- 官网文档和每次 release note 都包含简短的 macOS 首次打开说明。
- 为未来切换 Developer ID 签名和 notarization 留出清晰边界，但当前实现不依赖付费账号。

## 非目标

- 不实现 macOS 端静默下载或无确认安装；下载和安装必须由用户在 Sparkle 原生窗口中确认。
- 不绕过 Gatekeeper，不要求用户执行 `xattr`、`spctl` 或终端命令。
- 不上架 Mac App Store。
- 不在本轮引入 Tauri、Wails 或原生 macOS 客户端重写。
- 不改变 Windows 现有 NSIS 安装包和应用内增量/全量更新流程。

## 关键决策

### 分发形态

macOS 发布产物使用 `dmg`。用户从 GitHub Releases 下载后，把 `Auto Email Sender.app` 拖到“应用程序”目录。

不发布裸 `.app` 或额外 ZIP。`.app` 是目录包，不适合作为 GitHub Release 资产直接传输；Sparkle 2 原生支持以 DMG 作为更新归档。DMG 同时承担首次安装和差分不可用时的全量回退，因此只维护一种完整 macOS 产物。

### 首次打开体验

未经过 Developer ID 签名和公证的 macOS 应用首次打开会被 Gatekeeper 阻止。官网文档和 release note 只写关键步骤：

1. 下载 macOS 版 `.dmg`，打开后拖到“应用程序”。
2. 首次打开若提示无法验证开发者，进入“系统设置 > 隐私与安全性”，点击“仍要打开”。
3. 再次确认“打开”后即可正常使用，之后可直接双击启动。

说明文字必须强调下载来源应为本项目 GitHub Releases 页面。

### 更新策略

Windows 继续使用现有 `electron-updater` 下载和安装更新。

macOS 使用 Sparkle 2.9.4：

- Electron 主进程通过一个很薄的 Objective-C++ N-API 模块创建 `SPUStandardUpdaterController`。
- 应用启动后启用 Sparkle 的 24 小时自动检查周期；用户也可点击现有“检查更新”按钮唤起原生窗口。
- Sparkle 原生 UI 负责版本提示、发布说明、下载进度、安装确认和重启，不把状态机复制到 React。
- 允许差分下载和 DMG 全量回退，但关闭自动下载和静默安装。
- appcast、DMG 和 delta 使用项目独立的 Ed25519 密钥签名；私钥只存放在 GitHub Actions Secret 中并经标准输入传给签名工具。
- 继续使用 ad-hoc Apple 签名，不依赖 Developer ID 或 notarization。

首个集成 Sparkle 的版本是过渡版本：旧客户端仍需手动下载 DMG 覆盖一次，之后才具备应用内更新能力。Sparkle 加载或检查失败只影响更新入口，不阻止应用其他功能。

### 开机自启

现有 `desktop/src/startup.ts` 只支持 Windows 注册表。macOS 端使用 Electron `app.getLoginItemSettings` 和 `app.setLoginItemSettings`：

- 仅安装后的桌面版支持开机自启，开发模式仍返回不支持。
- 启用时设置 `openAtLogin: true`，并传递 `--startup`。
- 应用收到 `--startup` 时保持现有启动时不显示窗口的行为。
- 禁用时设置 `openAtLogin: false`。

托盘菜单和个人设置页继续复用现有 `StartupAtLoginStatus` 类型，不额外暴露平台差异。

## 架构

### 后端运行时

新增 macOS 后端构建脚本 `scripts/build-backend.sh`：

- 使用 `uv sync --dev` 准备 Python 依赖。
- 设置 `PLAYWRIGHT_BROWSERS_PATH=backend/ms-playwright`。
- 安装 Playwright Chromium shell。
- 使用 PyInstaller `--onedir` 构建 `backend/dist/backend/backend`。
- 携带 Alembic 配置和迁移目录。
- 执行 `backend/dist/backend/backend --self-check` 验证打包运行时。

脚本应尽量与 `scripts/build-backend.ps1` 的隐藏导入、collect-all 和 self-check 保持一致，避免 Windows 与 macOS 后端运行时能力分叉。

### Electron 主进程

`desktop/src/backend.ts` 需要把 packaged 后端路径改成平台感知：

- `win32`: `resources/backend/backend.exe`
- `darwin`: `resources/backend/backend`
- 其他平台：`resources/backend/backend`

启动方式继续直接 spawn PyInstaller 产物，端口仍使用 `127.0.0.1` 动态可用端口。`PLAYWRIGHT_BROWSERS_PATH` 在 packaged 模式下继续指向 `resources/ms-playwright`。

### 打包配置

`desktop/electron-builder.yml` 增加 macOS 配置：

- `mac.target` 使用 `dmg`。
- `mac.icon` 使用 `build/icon.icns`。
- `mac.category` 使用教育或生产力类目。
- 明确当前 macOS 包不做签名和公证。
- `artifactName` 区分 Windows 与 macOS，避免 `Setup` 命名污染 macOS 产物。

`desktop/package.json` 增加 macOS 脚本：

- `setup:sparkle`: 下载 Sparkle 2.9.4 并校验固定 SHA-256。
- `build:sparkle`: 用系统 `clang++` 构建 arm64 N-API 桥接。
- `pack:mac`: 准备 Sparkle 后构建 `.app` 目录，用于本地调试。
- `dist:mac`: 准备 Sparkle 后构建 `.dmg`，不直接发布。

Windows 脚本保留现有行为。

`electron-builder.yml` 把原生模块放入 `Contents/Resources/native`，把 `Sparkle.framework` 放入 `Contents/Frameworks`，并通过 `extendInfo` 注入 feed URL、公钥、自动检查周期和必须签名 feed 等配置。Framework、工具和原生构建产物均由脚本生成并加入 `.gitignore`。

### 图标资源

新增 `desktop/build/icon.icns`。可以从现有 `desktop/build/icon.png` 生成，但最终文件必须适配 macOS Dock、访达和 DMG 展示。

### GitHub Actions

release workflow 分为 Windows 构建、macOS 构建和统一发布三个 job：

- runner 使用 `macos-latest`。
- 安装 Node.js、Python 3.12 和 uv。
- 构建前端。
- 构建 macOS 后端。
- 安装 desktop 依赖。
- 执行 desktop 测试。
- 使用 ad-hoc 签名构建 macOS `.dmg`，不要求 Apple 证书 Secret。
- 使用 `SPARKLE_PUBLIC_ED_KEY` 注入公钥，使用 `SPARKLE_ED_PRIVATE_KEY` 签名 appcast、DMG 元数据和差分包。
- 读取上一版 appcast 并下载最近 3 个旧 DMG，生成最多 3 个 delta；首个 Sparkle 版本没有旧 appcast 时仅生成全量条目。
- 两个平台只上传 Actions artifacts；统一 publish job 等待两边成功后暂存 draft Release，先上传安装包和 delta，最后上传 appcast，全部成功后再发布为稳定 Release，避免并发发布竞争和 feed 提前可见。

## 用户体验

### 官网文档

修改 `website/docs/getting-started.md` 或 `website/docs/install.md`：

- 下载区域拆分为 Windows 和 macOS。
- Windows 保留当前安装说明和 SmartScreen 提示。
- macOS 新增简短首次打开说明。
- 更新章节说明：Windows 使用现有内置更新；macOS 使用 Sparkle 原生窗口并要求用户确认安装。

首页和 README 的下载文案从“下载 Windows 安装包”调整为“下载桌面版”，进入 Releases 后按系统选择安装包。

### Release Note

`docs/releases/vX.Y.Z.md` 和同步后的 `desktop/release-notes.md` 的“安装说明”改为：

- Windows：下载 `AutoEmailSender Setup x.y.z.exe`。
- macOS：下载 `AutoEmailSender-x.y.z-arm64.dmg`，拖到“应用程序”。首次打开若提示无法验证开发者，到“系统设置 > 隐私与安全性”点击“仍要打开”，再确认打开。

“自动更新”改为：

- Windows：应用内可下载并安装更新。
- macOS：自动检查或手动唤起 Sparkle，确认后下载并重启安装；旧客户端升级过渡版本时仍需手动覆盖一次。

## 错误处理

- macOS 后端打包缺失或不可执行时，启动页沿用现有“系统准备失败”提示，错误详情包含后端路径。
- Sparkle 加载或检查更新失败时，只影响更新功能，不阻止主功能使用。
- appcast 缺失、签名错误或没有 macOS DMG 时，Sparkle 拒绝更新并显示原生错误，不退回不受验证的下载路径。
- 开机自启设置失败时，沿用现有托盘和设置页错误提示。

## 测试策略

### 单元测试

- `desktop/test/backend.test.ts` 增加 macOS packaged 后端路径断言。
- `desktop/test/startup.test.ts` 增加 macOS login item 读取、启用和禁用测试。
- `desktop/test/updates.test.ts` 验证 macOS 调用 Sparkle、Windows 仍使用 `electron-updater`。
- `desktop/test/macSparkle.test.ts` 验证开发和打包环境下原生模块路径及桥接接口。
- `desktop/test/packaging.test.ts` 验证 DMG、Sparkle Framework、Info.plist 配置和 Windows 配置不回退。
- `scripts/prepare-sparkle-release.test.mjs` 验证 appcast 旧 DMG 解析、仓库 URL 限制和最近 3 版选择。
- `backend/test/test_backend_build_script.py` 增加 `scripts/build-backend.sh` 的 PyInstaller 参数、Playwright 路径和 self-check 断言。

### 集成验证

在 macOS 本机或 CI 上验证：

- `scripts/build-backend.sh --clean`
- `cd frontend && npm run build`
- `cd desktop && npm test`
- `node --test scripts/prepare-sparkle-release.test.mjs`
- `cd desktop && npm run dist:mac`
- 打开 `.dmg`，拖入“应用程序”，按未验证开发者流程放行后启动。
- 验证本地后端 ready、抓取依赖可用、开机自启设置可读写、应用内检查更新可打开 Sparkle 原生窗口。

### 发布验证

发布 tag 后确认：

- GitHub Release 同时包含 Windows `.exe`、macOS `.dmg`、`appcast.xml` 和可用的 `.delta`。
- 官网下载说明指向 GitHub Releases。
- macOS release note 的首次打开说明存在且简短。
- Windows 应用内更新不受 macOS 改造影响。

## 风险与缓解

- **Gatekeeper 提示影响转化。** 通过官网和 release note 提前说明，只保留系统设置放行路径，不提供终端绕过命令。
- **没有 Developer ID。** Sparkle 的 Ed25519 验签可以保护更新链路，但不能消除 Gatekeeper 的首次打开提示；文档保留系统设置放行流程。
- **Sparkle 私钥丢失或泄露。** 私钥不得进入仓库，需离线备份；不能直接轮换密钥，否则旧客户端会失去更新能力。
- **首版没有差分包。** 首个 Sparkle 版本没有历史 appcast，这是预期行为；旧客户端手动覆盖一次后，后续发布才生成差分。
- **PyInstaller macOS 依赖缺失。** 使用 packaged runtime self-check，并让 macOS build script 与 Windows 脚本保持同一组 collect 规则。
- **Intel Mac 用户无法运行 arm64 包。** 第一版优先发布 Apple Silicon `arm64` 包；如反馈需要 Intel 支持，再增加 `x64` 构建 job 和对应文件命名。
- **Windows 发布被配置改动影响。** 保留 Windows 脚本和 NSIS target，新增测试锁定现有 Windows 配置。

## 验收标准

- macOS release 产物可以从 GitHub Releases 下载并按文档完成首次打开。
- macOS 桌面版启动后能自动拉起本地后端，核心业务流程可用。
- macOS 开机自启可在应用内启用和禁用，重启登录后按 `--startup` 隐藏窗口启动。
- macOS 更新入口能唤起 Sparkle 原生窗口，自动检查、验签，并在用户确认后下载和重启安装。
- 发布 feed 强制签名，保留当前 DMG 全量回退和最近 3 个旧版本差分。
- 官网文档和 release note 包含简洁的 macOS 首次打开说明。
- Windows 现有安装包和应用内更新行为保持不变。
