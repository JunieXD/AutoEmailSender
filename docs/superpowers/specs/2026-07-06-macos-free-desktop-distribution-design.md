# macOS 免费桌面分发设计

## 背景

Auto Email Sender 已有 Windows 桌面版：Electron 壳启动本地 FastAPI 后端，前端通过 preload API 访问本地服务，并通过 GitHub Releases 做应用内更新。现有桌面打包配置、发布脚本和 CI 都偏 Windows，macOS 尚未形成可面向普通用户发布的安装包。

本设计选择免费 macOS 分发路线：不购买 Apple Developer Program，不配置 Developer ID 证书，不做 notarization。应用可以通过 GitHub Releases 发布 `.dmg`，普通用户首次打开时按 macOS 的未验证开发者放行流程操作。Windows 现有安装和自动更新行为保持不变。

## 目标

- 生成可供普通 macOS 用户下载的 `dmg` 安装包。
- 保持 macOS 桌面版的核心功能与 Windows 一致：本地后端自动启动、托盘、关闭隐藏、开机自启、材料打开、外链打开和桌面启动状态提示。
- macOS 端支持应用内检查 GitHub Releases 新版本，并引导用户打开下载页手动更新。
- 官网文档和每次 release note 都包含简短的 macOS 首次打开说明。
- 为未来切换 Developer ID 签名和 notarization 留出清晰边界，但当前实现不依赖付费账号。

## 非目标

- 不实现 macOS 端静默下载、自动替换或一键重启安装更新。
- 不绕过 Gatekeeper，不要求用户执行 `xattr`、`spctl` 或终端命令。
- 不上架 Mac App Store。
- 不在本轮引入 Tauri、Wails 或原生 macOS 客户端重写。
- 不改变 Windows 现有 NSIS 安装包和应用内增量/全量更新流程。

## 关键决策

### 分发形态

macOS 发布产物使用 `dmg`。用户从 GitHub Releases 下载后，把 `Auto Email Sender.app` 拖到“应用程序”目录。

当前不把 `zip` 作为用户可见主产物。`zip` 仅在未来需要 Developer ID 签名后的自动更新时再加入发布链路。这样可以避免 unsigned `zip` 暗示 macOS 端支持自动替换安装。

### 首次打开体验

未签名未公证的 macOS 应用首次打开会被 Gatekeeper 阻止。官网文档和 release note 只写关键步骤：

1. 下载 macOS 版 `.dmg`，打开后拖到“应用程序”。
2. 首次打开若提示无法验证开发者，进入“系统设置 > 隐私与安全性”，点击“仍要打开”。
3. 再次确认“打开”后即可正常使用，之后可直接双击启动。

说明文字必须强调下载来源应为本项目 GitHub Releases 页面。

### 更新策略

Windows 继续使用现有 `electron-updater` 下载和安装更新。

macOS 使用“应用内检查 + 浏览器下载”的手动更新模式：

- 应用内仍显示版本和检查更新入口。
- 主进程请求 GitHub Releases 最新版本信息，比较当前版本与最新版本。
- 如发现新版本，前端展示“前往下载”动作。
- 点击后通过现有外链服务打开 GitHub Release 页面。
- 不调用 `downloadUpdate`、`quitAndInstall` 或 differential update 流程。

类型上新增或扩展桌面更新状态，使 UI 能区分“可自动下载的更新”和“需手动下载的 macOS 更新”。推荐新增状态：

```ts
{ state: "manual_download_available"; version: string; nextVersion: string; releaseUrl: string; releaseNotes?: string }
```

如果 macOS 检查更新失败，应展示普通错误状态，不影响应用其他功能。

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

- `pack:mac`: 构建未签名 `.app` 目录，用于本地调试。
- `dist:mac`: 构建 `.dmg`，不发布。
- `publish:mac`: 构建 `.dmg` 并发布到 GitHub Releases。

Windows 脚本保留现有行为。

### 图标资源

新增 `desktop/build/icon.icns`。可以从现有 `desktop/build/icon.png` 生成，但最终文件必须适配 macOS Dock、访达和 DMG 展示。

### GitHub Actions

现有 release workflow 保留 Windows job，并新增 macOS job：

- runner 使用 `macos-latest`。
- 安装 Node.js、Python 3.12 和 uv。
- 构建前端。
- 构建 macOS 后端。
- 安装 desktop 依赖。
- 执行 desktop 测试。
- 构建并发布 macOS `.dmg`。

macOS job 不要求 Apple 证书 secrets。若 electron-builder 在 CI 中尝试签名，需要显式关闭 macOS 代码签名。

Release notes 更新步骤仍使用同一份 `desktop/release-notes.md`，但发布说明需要同时覆盖 Windows 与 macOS。

## 用户体验

### 官网文档

修改 `website/docs/getting-started.md` 或 `website/docs/install.md`：

- 下载区域拆分为 Windows 和 macOS。
- Windows 保留当前安装说明和 SmartScreen 提示。
- macOS 新增简短首次打开说明。
- 更新章节说明：Windows 支持应用内下载安装；macOS 支持应用内检查更新并打开 GitHub Releases 下载页。

首页和 README 的下载文案从“下载 Windows 安装包”调整为“下载桌面版”，进入 Releases 后按系统选择安装包。

### Release Note

`docs/releases/vX.Y.Z.md` 和同步后的 `desktop/release-notes.md` 的“安装说明”改为：

- Windows：下载 `AutoEmailSender Setup x.y.z.exe`。
- macOS：下载 `AutoEmailSender-x.y.z-arm64.dmg`，拖到“应用程序”。首次打开若提示无法验证开发者，到“系统设置 > 隐私与安全性”点击“仍要打开”，再确认打开。

“自动更新”改为：

- Windows：应用内可下载并安装更新。
- macOS：应用内可检查更新，发现新版本后打开 GitHub Releases 手动下载新版 `.dmg`。

## 错误处理

- macOS 后端打包缺失或不可执行时，启动页沿用现有“系统准备失败”提示，错误详情包含后端路径。
- macOS 检查更新失败时，只影响更新按钮状态，不阻止主功能使用。
- GitHub Releases 没有 macOS `.dmg` 资产时，仍打开 release 页面，由用户查看当前可用产物。
- 开机自启设置失败时，沿用现有托盘和设置页错误提示。

## 测试策略

### 单元测试

- `desktop/test/backend.test.ts` 增加 macOS packaged 后端路径断言。
- `desktop/test/startup.test.ts` 增加 macOS login item 读取、启用和禁用测试。
- `desktop/test/updates.test.ts` 增加 macOS 手动下载状态和 GitHub Release URL 解析测试。
- `desktop/test/packaging.test.ts` 增加 macOS `dmg` target、`icon.icns` 和 Windows 配置不回退测试。
- `backend/test/test_backend_build_script.py` 增加 `scripts/build-backend.sh` 的 PyInstaller 参数、Playwright 路径和 self-check 断言。

### 集成验证

在 macOS 本机或 CI 上验证：

- `scripts/build-backend.sh --clean`
- `cd frontend && npm run build`
- `cd desktop && npm test`
- `cd desktop && npm run dist:mac`
- 打开 `.dmg`，拖入“应用程序”，按未验证开发者流程放行后启动。
- 验证本地后端 ready、抓取依赖可用、开机自启设置可读写、应用内检查更新可打开 GitHub Releases。

### 发布验证

发布 tag 后确认：

- GitHub Release 同时包含 Windows `.exe` 和 macOS `.dmg`。
- 官网下载说明指向 GitHub Releases。
- macOS release note 的首次打开说明存在且简短。
- Windows 应用内更新不受 macOS 改造影响。

## 风险与缓解

- **Gatekeeper 提示影响转化。** 通过官网和 release note 提前说明，只保留系统设置放行路径，不提供终端绕过命令。
- **macOS 自动更新不可用。** 明确采用手动更新模式，UI 文案不得出现“下载并安装”这类自动更新承诺。
- **PyInstaller macOS 依赖缺失。** 使用 packaged runtime self-check，并让 macOS build script 与 Windows 脚本保持同一组 collect 规则。
- **Intel Mac 用户无法运行 arm64 包。** 第一版优先发布 Apple Silicon `arm64` 包；如反馈需要 Intel 支持，再增加 `x64` 构建 job 和对应文件命名。
- **Windows 发布被配置改动影响。** 保留 Windows 脚本和 NSIS target，新增测试锁定现有 Windows 配置。

## 验收标准

- macOS release 产物可以从 GitHub Releases 下载并按文档完成首次打开。
- macOS 桌面版启动后能自动拉起本地后端，核心业务流程可用。
- macOS 开机自启可在应用内启用和禁用，重启登录后按 `--startup` 隐藏窗口启动。
- macOS 更新入口能检查 GitHub Releases 并打开下载页，不尝试自动安装 unsigned 更新。
- 官网文档和 release note 包含简洁的 macOS 首次打开说明。
- Windows 现有安装包和应用内更新行为保持不变。
