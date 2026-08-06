# Windows 桌面打包与自动更新设计

## 背景

Auto Email Sender 当前是本地运行的 Web 应用，前端使用 React、Vite 和 TypeScript，后端使用 FastAPI、SQLite 和 uv。开发者可以通过命令行分别启动前端和后端，但目标用户是 Windows 同学，其中很多人不懂编程，也不熟悉命令行。

因此，桌面发布版本需要做到：

- 用户只需要下载安装包并双击打开。
- 用户机器不需要安装 Python、Node.js、uv、npm 或 Git。
- 应用可以从 GitHub Releases 检查更新。
- 发布者可以通过 GitHub 推送代码并自动生成 Release。
- 第一版先不购买 Windows 代码签名证书，但为后续签名预留配置位置。

## 已确认的产品决策

- 第一版只支持 Windows 用户。
- 采用 Electron 作为桌面壳。
- 使用 GitHub 公开仓库 `JunieXD/AutoEmailSender` 作为发布源。
- 使用 `master` 作为正式发布分支。
- 使用 GitHub Releases 作为安装包和自动更新元数据的分发渠道。
- 使用 `electron-builder` 生成 Windows 安装包。
- 使用 `electron-updater` 实现启动自动检查和手动检查更新。
- 后端在构建时冻结为 Windows 可执行文件，用户不需要安装 Python 环境。
- 第一版不做代码签名，但保留签名配置入口。
- 新增一键发布脚本，避免发布者手动记忆版本号修改、tag 创建和 tag 推送细节。

## 目标

- 用户可以从 GitHub Release 页面下载 `AutoEmailSender Setup x.y.z.exe`。
- 用户安装后可以从桌面快捷方式或开始菜单打开应用。
- Electron 主进程可以自动启动内置 FastAPI 后端，并在退出时关闭后端进程。
- 前端在桌面环境中访问本地内置后端，不暴露命令行窗口。
- 应用启动后可以自动检查 GitHub Releases 是否有新版本。
- 应用提供「检查更新」入口，允许用户手动检查更新。
- 发布者可以通过一条脚本命令发布新版本。
- GitHub Actions 可以在 tag 推送后自动构建 Windows 安装包并创建 Release。

## 非目标

- 不在第一版支持 macOS 或 Linux。
- 不在第一版购买或配置 Windows 代码签名证书。
- 不在第一版提供 MSI、企业部署、组策略分发或 Microsoft Store 分发。
- 不在第一版提供 beta、nightly 或多更新频道。
- 不在第一版提供 GitHub Actions 网页手动发布入口。
- 不要求用户理解端口、后端服务、Python 环境或命令行。

## 方案比较

### 方案 A：Electron + PyInstaller + GitHub Releases

优点：

- 和现有 React/Vite 前端适配自然。
- `electron-builder` 和 `electron-updater` 对 GitHub Releases 支持成熟。
- 可以把前端静态资源和后端可执行文件打进同一个安装包。
- Windows 安装包、桌面快捷方式、开始菜单和自动更新能力较完整。
- 适合「完全不懂命令行」的用户群体。

缺点：

- 安装包体积会比纯 Web 应用大。
- Electron 运行时资源占用高于 Tauri。
- Python 后端需要额外冻结为可执行文件。

### 方案 B：Tauri + Python 后端

优点：

- 桌面壳更轻，安装包通常更小。
- Rust 原生能力较强。

缺点：

- 当前项目已有 FastAPI 后端，仍然需要处理 Python 后端打包和生命周期。
- 自动更新、后端子进程管理和构建链路需要更多定制。
- 对当前仓库来说，第一版落地成本高于 Electron。

### 方案 C：便携版启动器

优点：

- 可以绕开一部分安装器复杂度。
- 构建链路可能更短。

缺点：

- 用户体验弱于正式安装包。
- 自动更新体验较差。
- 容易暴露后端、端口或命令行细节。
- 不符合「完全不懂命令行的同学方便打开」这一目标。

推荐采用方案 A。

## 总体架构

新增 `desktop/` 目录承载 Electron 桌面壳：

```text
repo/
  frontend/
    src/
    dist/
    package.json
  backend/
    main.py
    pyproject.toml
    uv.lock
  desktop/
    package.json
    src/
      main.ts
      preload.ts
    electron-builder 配置
  scripts/
    release.ps1
  .github/
    workflows/
      release.yml
```

打包后的用户侧运行模型：

```text
Windows 用户
  -> 打开 Auto Email Sender
    -> Electron 主进程启动
      -> 启动内置 backend.exe
      -> 等待 /health 就绪
      -> 加载内置前端页面
      -> 前端访问本地后端 API
```

用户退出应用时：

```text
关闭窗口
  -> Electron 主进程拦截退出
  -> 关闭 backend.exe 子进程
  -> Electron 退出
```

## 后端打包

后端使用 PyInstaller 构建 Windows 可执行文件：

```text
backend.exe
```

构建时由 GitHub Actions 在 Windows runner 中完成：

1. 安装 uv。
2. 执行 `uv sync`。
3. 安装或调用 PyInstaller。
4. 把 FastAPI 入口和运行依赖冻结为 `backend.exe`。
5. 将 `backend.exe` 作为 Electron extra resource 打入安装包。

后端需要新增健康检查接口：

```text
GET /health
```

响应：

```json
{
  "status": "ok"
}
```

Electron 主进程通过该接口判断后端是否启动完成。如果后端启动失败，桌面端必须展示清晰错误，而不是白屏。

## 前端打包

前端继续使用现有 Vite 构建：

```text
frontend/dist
```

Electron 生产环境加载内置静态资源。前端 API base URL 由桌面壳注入或由运行时配置提供，指向 Electron 启动的本地后端地址。

开发环境可以继续保留当前方式：

```text
frontend npm run dev
backend uv run uvicorn main:app --reload
```

同时新增桌面开发模式，方便验证 Electron 主进程、更新菜单和后端生命周期。

## Electron 主进程职责

Electron 主进程负责：

- 创建主窗口。
- 启动内置 `backend.exe`。
- 选择或确认本地后端端口。
- 等待 `/health` 返回成功。
- 向渲染进程暴露应用版本和更新状态。
- 处理自动更新检查、下载和重启安装。
- 在应用退出时关闭后端子进程。
- 当后端启动失败时展示错误页或错误对话框。

主进程不承载业务逻辑。导师抓取、邮件发送、匹配分析和数据库访问仍然属于 FastAPI 后端。

## 自动更新

自动更新使用 `electron-updater` 和 GitHub Releases。

更新触发：

- 应用启动后自动检查。
- 用户在设置页或关于页点击「检查更新」。

更新状态：

```text
idle
checking
available
not_available
downloading
downloaded
error
```

推荐 IPC 能力：

```text
app:get-version
update:check
update:download
update:quit-and-install
update:on-status
```

交互规则：

- 启动自动检查时，不打断用户当前操作。
- 发现新版本时提示用户是否下载。
- 下载过程中展示进度。
- 下载完成后提示用户「重启并安装」或「下次启动安装」。
- 手动检查如果没有新版本，明确提示「当前已是最新版本」。
- 更新失败时展示可理解的错误摘要，并允许稍后重试。

公开 GitHub 仓库不需要在客户端内置 GitHub token。

## 发布流程

正式发布分支为 `master`。

第一版新增发布脚本：

```powershell
pwsh -NoProfile -Command ".\scripts\release.ps1 0.1.1"
```

脚本职责：

1. 确认当前分支是 `master`。
2. 确认工作区没有未提交改动。
3. 校验版本号格式。
4. 更新版本号文件，例如：
   - `desktop/package.json`
   - 必要时同步 `frontend/package.json`
5. 创建发布提交：
   ```text
   chore(release): v0.1.1
   ```
6. 创建 Git tag：
   ```text
   v0.1.1
   ```
7. 推送 `master`。
8. 推送 tag。
9. 由 GitHub Actions 自动完成 Release 构建和发布。

GitHub Actions 触发条件：

```yaml
on:
  push:
    tags:
      - "v*"
```

Actions 工作流职责：

1. Checkout 代码。
2. 安装 Node.js。
3. 安装前端依赖。
4. 构建前端。
5. 安装 uv 和 Python 依赖。
6. 构建 `backend.exe`。
7. 安装 Electron 桌面依赖。
8. 使用 `electron-builder` 构建 Windows NSIS 安装包。
9. 创建 GitHub Release。
10. 上传安装包和自动更新元数据。

## 用户安装体验

用户路径：

1. 打开 GitHub Release 页面。
2. 下载 `AutoEmailSender Setup x.y.z.exe`。
3. 双击安装。
4. 从桌面快捷方式或开始菜单打开应用。
5. 首次启动时配置邮箱、模型和个人材料。
6. 后续有新版本时，应用内提示更新。

第一版不隐藏未签名带来的 Windows 提示。文档需要告知用户：安装时可能出现「未知发布者」或 SmartScreen 提示，这是因为第一版暂未购买代码签名证书。

## 错误处理

### 后端启动失败

可能原因：

- `backend.exe` 被安全软件拦截。
- 本地端口被占用。
- 数据目录不可写。
- 后端运行依赖未被正确打包。

处理方式：

- 展示错误对话框或错误页。
- 提供「重试」按钮。
- 提示用户查看日志目录。
- 日志中记录后端启动命令、退出码和 stderr 摘要。

### 自动更新失败

可能原因：

- 无法访问 GitHub Releases。
- 下载中断。
- Release 元数据缺失。
- 安装包被系统或安全软件拦截。

处理方式：

- 展示「更新失败，请稍后重试」。
- 手动检查入口保持可用。
- 不影响当前版本继续使用。

### 退出后后端残留

Electron 退出流程必须主动关闭子进程。如果正常关闭失败，应做二次清理，避免用户下次打开时端口冲突。

## 配置与数据目录

应用安装目录不应写入运行数据。SQLite 数据库、日志和用户上传材料应继续放在用户可写目录下。

推荐由后端统一使用应用数据目录，例如：

```text
%APPDATA%/AutoEmailSender/
```

需要区分：

- 安装目录：存放应用程序和内置资源。
- 数据目录：存放数据库、日志、用户文件和配置。

自动更新只替换应用程序，不应覆盖用户数据。

## 安全与签名

第一版不配置 Windows 代码签名证书。

已知影响：

- 安装包可能显示「未知发布者」。
- SmartScreen 可能提示用户谨慎运行。
- 新发布版本可能更容易被安全软件拦截。

设计预留：

- `electron-builder` 配置中保留签名相关位置。
- GitHub Actions 预留证书密钥注入点。
- 后续购买代码签名证书后，可在不重做整体发布架构的情况下接入签名。

## 验证策略

### 本地开发验证

- 前端 lint 通过。
- 前端 build 通过。
- 后端 `/health` 接口可访问。
- Electron dev 模式能启动窗口。
- Electron 能启动和关闭后端进程。

### 本地打包验证

- 能生成 Windows 安装包。
- 安装包可以正常安装。
- 桌面快捷方式可以打开应用。
- 应用启动后后端健康检查成功。
- 退出应用后没有残留 `backend.exe`。

### GitHub Release 验证

- 运行 `scripts/release.ps1 x.y.z` 后创建发布提交和 tag。
- tag 推送后 GitHub Actions 自动执行。
- Release 中包含安装包和更新元数据。
- 下载 Release 安装包后可以安装并启动。

### 自动更新验证

- 安装旧版本。
- 发布新版本。
- 打开旧版本后可以发现新版本。
- 下载完成后可以重启并安装。
- 手动检查在最新版时显示「当前已是最新版本」。

## 成功标准

当以下条件同时满足时，视为第一版目标达成：

- Windows 用户无需命令行即可安装和打开 Auto Email Sender。
- 用户机器无需安装 Python、Node.js、uv、npm 或 Git。
- 桌面 App 可以自动启动和关闭内置 FastAPI 后端。
- 应用内可以自动检查更新，也可以手动检查更新。
- GitHub tag 推送后可以自动创建 Release。
- 发布者可以用一条 `scripts/release.ps1` 命令完成版本号更新、提交、tag 和推送。
- 自动更新不会覆盖用户数据。
- 未签名安装包的限制在用户文档中清楚说明，并为后续签名预留配置位置。

## 后续增强

- 增加 Windows 代码签名。
- 增加 GitHub Actions 网页手动发布入口。
- 增加 beta/pre-release 更新频道。
- 增加安装包下载页或项目官网。
- 增加崩溃日志导出入口，方便排查用户机器上的启动问题。
