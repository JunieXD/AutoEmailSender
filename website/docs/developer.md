# 开发者文档

本文面向需要本地运行、调试或打包 Auto Email Sender 的开发者。普通用户请直接下载 Windows 安装版。

## 环境要求

- Node.js
- Python 3.12
- uv
- Git

本地跑起来之前，先准备一个邮箱的 SMTP/IMAP 授权码，以及一套可用的 OpenAI 兼容 LLM API。[DeepSeek API](https://platform.deepseek.com/) 适合作为联调起点。

## 首次初始化

Web 版本和桌面端都需要后端的浏览器自动化能力。先在 GitHub 上 fork 本仓库，然后克隆你自己的 fork：

```powershell
git clone https://github.com/<你的 GitHub 用户名>/AutoEmailSender.git
cd AutoEmailSender
```

将 `<你的 GitHub 用户名>` 替换为你的 GitHub 用户名。使用 SSH 的话，改为你的 SSH 仓库地址。

安装后端依赖和浏览器运行时：

```powershell
.\scripts\install-backend-playwright.ps1
```

脚本会执行 `uv sync --dev`，并将 Playwright 的 Chromium headless shell 下载到 `backend/ms-playwright/`。该目录已在 `.gitignore` 中忽略，无需提交。

## 本地运行 Web 版本

启动后端：

```powershell
cd backend
uv run alembic upgrade head
uv run python dev_entry.py
```

启动前端：

```powershell
cd frontend
npm install
npm run dev
```

打开 `http://127.0.0.1:5173`。

后端默认将数据写入仓库根目录的 `data/`。可通过环境变量 `AUTO_EMAIL_SENDER_DATA_DIR` 覆盖。

## 桌面端调试

桌面端基于 Electron。开发模式下无需手动启动后端，`npm run dev` 会通过 `uv run python desktop_entry.py` 自动拉起。

调试桌面壳时，先启动前端开发服务器：

```powershell
cd frontend
npm install
npm run dev
```

再在另一个终端启动桌面端：

```powershell
cd desktop
npm install
npm run dev
```

桌面端开发模式加载 `http://127.0.0.1:5173`，并自动选择本地端口启动后端。看到 `ERR_CONNECTION_REFUSED` 时，通常是前端开发服务器未启动或端口不是 `5173`。后端启动失败时，确认已完成首次初始化且 `backend/ms-playwright/` 目录存在。

桌面版启动时将用户数据目录传给后端，因此安装版和源码版的数据默认位置不同。安装版数据落在当前用户的 AppData 目录下。

## 常用配置

- **SMTP/IMAP：** 发信和收信所用，需要在邮箱服务商后台开启客户端授权或生成授权码。
- **LLM API：** 匹配分析和自动写信所用，兼容 OpenAI 接口即可接入。
- **推荐起点：** DeepSeek API，Base URL 填 `https://api.deepseek.com`，模型名可用 `deepseek-v4-flash`。

## 本地打包安装包

本地测试 Windows 安装包：

```powershell
cd frontend
npm run build

cd ../desktop
npm run dist
```

打包前确认已在仓库根目录执行过 `.\scripts\install-backend-playwright.ps1`。桌面端打包会将 `backend/ms-playwright/` 复制到安装包资源目录，跳过这一步会导致安装版中的浏览器自动化无法启动。

安装包生成到 `desktop/release/`。本地打包不会自动发布到 GitHub。

### macOS 与 Sparkle

macOS 只发布 Apple Silicon `arm64` DMG，并保持 ad-hoc 签名，不需要 Apple Developer Program。构建前需要准备 Sparkle 和用于更新归档验签的公开密钥：

```bash
./scripts/setup-sparkle.sh
export SPARKLE_PUBLIC_ED_KEY="<Sparkle EdDSA 公钥>"

cd desktop
npm run dist:mac
```

`dist:mac` 会自动编译原生桥接并把 `Sparkle.framework` 放入应用包。DMG 同时用于首次安装和 Sparkle 无法使用差分包时的全量回退，不再另外发布裸 `.app`。

正式发布由 tag 触发 GitHub Actions：Windows 和 macOS job 只构建产物，最后由单独的 publish job 一次性创建 Release。macOS job 会复用上一版 `appcast.xml`，下载最近 3 个旧 DMG，生成并签名差分包与新的 appcast。完整的首次密钥配置、迁移和故障处理见 [Sparkle 发布运维说明](https://github.com/JunieXD/AutoEmailSender/blob/master/docs/sparkle-release-operations.md)。
