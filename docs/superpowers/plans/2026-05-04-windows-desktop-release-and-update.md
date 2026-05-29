# Windows 桌面打包与自动更新实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 为 Auto Email Sender 增加 Windows 桌面安装包、内置 FastAPI 后端启动、GitHub Releases 自动更新和一键发布脚本。

**架构：** 新增 `desktop/` Electron 壳，生产环境加载 `frontend/dist`，并启动 PyInstaller 产物 `backend.exe`。后端通过可配置数据目录写入 `%APPDATA%` 等用户可写位置，前端通过桌面 preload 注入的后端地址访问 API，GitHub tag 推送触发 Windows Release 构建。

**技术栈：** Electron、electron-builder、electron-updater、React/Vite、FastAPI、PyInstaller、PowerShell、GitHub Actions、unittest、Vitest。

---

## 文件结构

- 修改：`backend/main.py`
  - 职责：新增 `/health` 健康检查接口，供 Electron 等待后端就绪。
- 修改：`backend/app/core/config.py`
  - 职责：支持 `AUTO_EMAIL_SENDER_DATA_DIR`，让桌面版把数据库、上传文件和日志写入用户数据目录。
- 创建：`backend/desktop_entry.py`
  - 职责：提供 PyInstaller 入口，支持 `--host` 和 `--port` 参数，生产启动时关闭 reload。
- 修改：`backend/pyproject.toml`
  - 职责：加入 PyInstaller 开发依赖。
- 修改：`backend/uv.lock`
  - 职责：锁定新增 Python 构建依赖。
- 创建：`backend/test/test_desktop_runtime.py`
  - 职责：测试 `/health`、桌面数据目录解析和桌面入口参数。
- 创建：`scripts/build-backend.ps1`
  - 职责：在 Windows 上构建 `backend/dist/backend/backend.exe`，并携带 Alembic 资源。
- 创建：`desktop/package.json`
  - 职责：定义 Electron 桌面包、脚本、依赖、electron-builder 配置入口。
- 创建：`desktop/package-lock.json`
  - 职责：锁定 Electron 桌面依赖。
- 创建：`desktop/tsconfig.json`
  - 职责：配置 Electron 主进程和 preload 的 TypeScript 编译。
- 创建：`desktop/electron-builder.yml`
  - 职责：定义 Windows NSIS 安装包、extraResources、GitHub 发布源和未签名配置预留位。
- 创建：`desktop/src/backend.ts`
  - 职责：解析后端可执行文件路径、选择端口、构建环境变量、启动和停止后端子进程。
- 创建：`desktop/src/updates.ts`
  - 职责：封装 `electron-updater`，把更新事件转换为稳定 IPC 状态。
- 创建：`desktop/src/main.ts`
  - 职责：创建窗口、启动后端、加载前端、注册 IPC、管理退出流程。
- 创建：`desktop/src/preload.ts`
  - 职责：通过 `contextBridge` 暴露桌面 API 给前端。
- 创建：`desktop/src/types.ts`
  - 职责：共享后端启动和更新 IPC 类型。
- 创建：`desktop/test/backend.test.ts`
  - 职责：测试后端路径、环境变量和端口解析等纯函数。
- 创建：`desktop/test/updates.test.ts`
  - 职责：测试更新状态格式化函数。
- 修改：`frontend/src/lib/api/client.ts`
  - 职责：支持 Electron 桌面环境下使用 preload 注入的后端 base URL。
- 创建：`frontend/src/types/desktop.d.ts`
  - 职责：声明 `window.autoEmailSender` 类型。
- 创建：`frontend/src/lib/desktopApi.ts`
  - 职责：为前端封装桌面版本、更新检查、下载和重启安装 API。
- 创建：`frontend/src/lib/desktopApi.test.ts`
  - 职责：测试浏览器环境和桌面环境下的 API 行为。
- 创建：`frontend/src/components/molecules/DesktopUpdateCard.tsx`
  - 职责：在个人中心展示桌面版本和手动检查更新入口；非桌面环境不渲染。
- 创建：`frontend/src/components/molecules/DesktopUpdateCard.test.tsx`
  - 职责：测试更新卡片渲染、按钮和状态展示。
- 修改：`frontend/src/pages/ProfilePage.tsx`
  - 职责：把 `DesktopUpdateCard` 接入个人中心。
- 修改：`frontend/package.json`
  - 职责：版本号由发布脚本同步维护。
- 修改：`frontend/package-lock.json`
  - 职责：同步前端 package 版本。
- 修改：`.gitignore`
  - 职责：忽略 Electron 和 PyInstaller 构建产物。
- 创建：`.github/workflows/release.yml`
  - 职责：tag 推送后构建 Windows 安装包并发布 GitHub Release。
- 创建：`scripts/release.ps1`
  - 职责：一键检查分支与工作区、更新版本号、创建提交、创建 tag 并推送。
- 修改：`README.md`
  - 职责：增加 Windows 安装版下载说明、未签名提示和发布命令。

---

### 任务 1：后端健康检查和桌面数据目录

**文件：**
- 修改：`backend/main.py`
- 修改：`backend/app/core/config.py`
- 创建：`backend/test/test_desktop_runtime.py`

- [ ] **步骤 1：编写失败的后端桌面运行测试**

创建 `backend/test/test_desktop_runtime.py`：

```python
from __future__ import annotations

import asyncio
import os
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient


class DesktopRuntimeTests(unittest.TestCase):
    def tearDown(self) -> None:
        from app.core.config import get_settings
        from app.core.database import dispose_engine, get_engine, get_session_factory

        if get_engine.cache_info().currsize:
            asyncio.run(dispose_engine())
        get_session_factory.cache_clear()
        get_settings.cache_clear()
        os.environ.pop("AUTO_EMAIL_SENDER_DATA_DIR", None)
        os.environ.pop("DATABASE_URL", None)
        os.environ.pop("ENABLE_BACKGROUND_WORKERS", None)

    def test_health_endpoint_returns_ok(self) -> None:
        os.environ["ENABLE_BACKGROUND_WORKERS"] = "0"

        from app.core.config import get_settings
        from main import create_app

        get_settings.cache_clear()
        with TestClient(create_app()) as client:
            response = client.get("/health")

        self.assertEqual(response.status_code, 200, msg=response.text)
        self.assertEqual(response.json(), {"status": "ok"})

    def test_desktop_data_dir_controls_default_storage_paths(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            data_dir = Path(temp_dir) / "AutoEmailSender"
            os.environ["AUTO_EMAIL_SENDER_DATA_DIR"] = str(data_dir)
            os.environ.pop("DATABASE_URL", None)

            from app.core.config import get_settings

            get_settings.cache_clear()
            settings = get_settings()

            self.assertEqual(settings.data_dir, data_dir)
            self.assertEqual(settings.uploads_dir, data_dir / "uploads")
            self.assertEqual(
                settings.database_url,
                f"sqlite+aiosqlite:///{(data_dir / 'auto_email_sender.db').as_posix()}",
            )
            self.assertTrue(settings.uploads_dir.exists())
            self.assertTrue((data_dir / "logs" / "crawler").exists())


if __name__ == "__main__":
    unittest.main()
```

- [ ] **步骤 2：运行测试验证失败**

运行：

```powershell
rtk pwsh -NoProfile -Command "cd backend; uv run python -m unittest test.test_desktop_runtime"
```

预期：FAIL，至少包含 `404` 或 `{'detail': 'Not Found'}`，因为 `/health` 尚未实现；数据目录测试也会因为 `AUTO_EMAIL_SENDER_DATA_DIR` 尚未生效而失败。

- [ ] **步骤 3：实现 `AUTO_EMAIL_SENDER_DATA_DIR`**

修改 `backend/app/core/config.py`，把现有顶层目录常量替换为默认目录和解析函数：

```python
PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_DATA_DIR = PROJECT_ROOT / "data"


def _resolve_data_dir() -> Path:
    raw_value = os.getenv("AUTO_EMAIL_SENDER_DATA_DIR")
    if raw_value is None or not raw_value.strip():
        return DEFAULT_DATA_DIR
    return Path(raw_value).expanduser().resolve()


def _build_default_database_url(data_dir: Path) -> str:
    return f"sqlite+aiosqlite:///{(data_dir / 'auto_email_sender.db').as_posix()}"
```

在 `get_settings()` 中改为：

```python
@lru_cache(maxsize=1)
def get_settings() -> Settings:
    data_dir = _resolve_data_dir()
    uploads_dir = data_dir / "uploads"
    data_dir.mkdir(parents=True, exist_ok=True)
    uploads_dir.mkdir(parents=True, exist_ok=True)
    crawler_debug_dir = Path(
        os.getenv("CRAWLER_DEBUG_DIR", (data_dir / "logs" / "crawler").as_posix())
    )
    crawler_debug_dir.mkdir(parents=True, exist_ok=True)
    database_url = _normalize_database_url(
        os.getenv("DATABASE_URL", _build_default_database_url(data_dir)),
    )
    return Settings(
        project_root=PROJECT_ROOT,
        data_dir=data_dir,
        uploads_dir=uploads_dir,
        crawler_debug_dir=crawler_debug_dir,
        database_url=database_url,
        ...
    )
```

保留 `Settings` 结构中的字段名，不改调用方。

- [ ] **步骤 4：实现 `/health`**

在 `backend/main.py` 的 `create_app()` 内、`/api/ping` 附近加入：

```python
    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}
```

- [ ] **步骤 5：运行目标测试验证通过**

运行：

```powershell
rtk pwsh -NoProfile -Command "cd backend; uv run python -m unittest test.test_desktop_runtime"
```

预期：PASS，输出包含 `OK`。

- [ ] **步骤 6：运行后端 API 基础回归**

运行：

```powershell
rtk pwsh -NoProfile -Command "cd backend; uv run python -m unittest test.test_runtime_settings_api test.test_api_endpoints.ApiEndpointTests.test_system_settings_endpoint_is_removed"
```

预期：PASS，输出包含 `OK`。

- [ ] **步骤 7：Commit 后端健康检查**

运行：

```powershell
rtk pwsh -NoProfile -Command "git add backend/main.py backend/app/core/config.py backend/test/test_desktop_runtime.py; git commit -m 'feat(桌面端): 添加后端健康检查和数据目录配置'"
```

---

### 任务 2：后端桌面入口和 PyInstaller 构建脚本

**文件：**
- 创建：`backend/desktop_entry.py`
- 修改：`backend/test/test_desktop_runtime.py`
- 修改：`backend/pyproject.toml`
- 修改：`backend/uv.lock`
- 创建：`scripts/build-backend.ps1`
- 修改：`.gitignore`

- [ ] **步骤 1：编写失败的桌面入口参数测试**

在 `backend/test/test_desktop_runtime.py` 的 `DesktopRuntimeTests` 中追加：

```python
    def test_desktop_entry_builds_uvicorn_options_from_args(self) -> None:
        from desktop_entry import build_uvicorn_options

        options = build_uvicorn_options(["--host", "127.0.0.1", "--port", "48123"])

        self.assertEqual(options["app"], "main:app")
        self.assertEqual(options["host"], "127.0.0.1")
        self.assertEqual(options["port"], 48123)
        self.assertIs(options["reload"], False)
```

- [ ] **步骤 2：运行测试验证失败**

运行：

```powershell
rtk pwsh -NoProfile -Command "cd backend; uv run python -m unittest test.test_desktop_runtime.DesktopRuntimeTests.test_desktop_entry_builds_uvicorn_options_from_args"
```

预期：FAIL，提示 `No module named 'desktop_entry'`。

- [ ] **步骤 3：创建 `backend/desktop_entry.py`**

创建文件：

```python
from __future__ import annotations

import argparse
from collections.abc import Sequence
from typing import Any

import uvicorn


def build_uvicorn_options(argv: Sequence[str] | None = None) -> dict[str, Any]:
    parser = argparse.ArgumentParser(description="Run Auto Email Sender desktop backend.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args(argv)
    return {
        "app": "main:app",
        "host": args.host,
        "port": args.port,
        "reload": False,
    }


def main() -> None:
    options = build_uvicorn_options()
    app_path = options.pop("app")
    uvicorn.run(app_path, **options)


if __name__ == "__main__":
    main()
```

- [ ] **步骤 4：加入 PyInstaller 依赖**

运行：

```powershell
rtk pwsh -NoProfile -Command "cd backend; uv add --dev pyinstaller"
```

预期：`backend/pyproject.toml` 出现 dev dependency，`backend/uv.lock` 更新。

- [ ] **步骤 5：创建后端构建脚本**

创建 `scripts/build-backend.ps1`：

```powershell
param(
  [switch]$Clean
)

$ErrorActionPreference = "Stop"
$RepoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
$BackendDir = Join-Path $RepoRoot "backend"

Push-Location $BackendDir
try {
  if ($Clean) {
    Remove-Item -Recurse -Force -ErrorAction SilentlyContinue "build", "dist"
  }

  uv sync --dev
  uv run pyinstaller `
    --noconfirm `
    --clean `
    --onedir `
    --name backend `
    --add-data "alembic.ini;." `
    --add-data "alembic;alembic" `
    desktop_entry.py
} finally {
  Pop-Location
}
```

- [ ] **步骤 6：更新 `.gitignore`**

在 `.gitignore` 追加：

```gitignore
backend/build/
backend/dist/
desktop/dist/
desktop/release/
```

- [ ] **步骤 7：运行目标测试验证通过**

运行：

```powershell
rtk pwsh -NoProfile -Command "cd backend; uv run python -m unittest test.test_desktop_runtime"
```

预期：PASS，输出包含 `OK`。

- [ ] **步骤 8：构建后端可执行文件**

运行：

```powershell
rtk pwsh -NoProfile -Command ".\scripts\build-backend.ps1 -Clean"
```

预期：命令 exit 0，存在 `backend/dist/backend/backend.exe`。

- [ ] **步骤 9：启动 PyInstaller 产物并验证健康检查**

运行：

```powershell
rtk pwsh -NoProfile -Command "$env:AUTO_EMAIL_SENDER_DATA_DIR=(Join-Path $pwd 'data\desktop-smoke'); $p=Start-Process -FilePath '.\backend\dist\backend\backend.exe' -ArgumentList '--host','127.0.0.1','--port','48123' -PassThru -WindowStyle Hidden; try { Start-Sleep -Seconds 8; Invoke-RestMethod 'http://127.0.0.1:48123/health' | ConvertTo-Json -Compress } finally { Stop-Process -Id $p.Id -Force -ErrorAction SilentlyContinue }"
```

预期：输出 `{"status":"ok"}`。

- [ ] **步骤 10：Commit 后端打包入口**

运行：

```powershell
rtk pwsh -NoProfile -Command "git add .gitignore backend/desktop_entry.py backend/pyproject.toml backend/uv.lock backend/test/test_desktop_runtime.py scripts/build-backend.ps1; git commit -m 'build(后端): 添加桌面版 PyInstaller 构建入口'"
```

---

### 任务 3：Electron 桌面壳基础能力

**文件：**
- 创建：`desktop/package.json`
- 创建：`desktop/package-lock.json`
- 创建：`desktop/tsconfig.json`
- 创建：`desktop/src/types.ts`
- 创建：`desktop/src/backend.ts`
- 创建：`desktop/src/main.ts`
- 创建：`desktop/src/preload.ts`
- 创建：`desktop/test/backend.test.ts`

- [ ] **步骤 1：初始化桌面包依赖**

运行：

```powershell
rtk pwsh -NoProfile -Command "New-Item -ItemType Directory -Force desktop/src, desktop/test | Out-Null; cd desktop; npm init -y; npm install electron-updater; npm install -D electron electron-builder typescript vitest @types/node"
```

预期：生成 `desktop/package.json` 和 `desktop/package-lock.json`。

- [ ] **步骤 2：替换 `desktop/package.json` 脚本和基础配置**

将 `desktop/package.json` 调整为：

```json
{
  "name": "auto-email-sender-desktop",
  "version": "0.1.0",
  "private": true,
  "type": "module",
  "main": "dist/main.js",
  "scripts": {
    "typecheck": "tsc -p tsconfig.json --noEmit",
    "build": "tsc -p tsconfig.json",
    "test": "vitest run",
    "dev": "npm run build && electron . --dev",
    "pack": "npm run build && electron-builder --win --dir",
    "dist": "npm run build && electron-builder --win nsis --publish never",
    "publish": "npm run build && electron-builder --win nsis --publish always"
  },
  "dependencies": {
    "electron-updater": "^6.0.0"
  },
  "devDependencies": {
    "@types/node": "^24.0.0",
    "electron": "^39.0.0",
    "electron-builder": "^26.0.0",
    "typescript": "^5.9.0",
    "vitest": "^4.0.0"
  }
}
```

运行 `npm install` 后保留实际解析出的版本，不要手工回退 lockfile。

- [ ] **步骤 3：创建 TypeScript 配置**

创建 `desktop/tsconfig.json`：

```json
{
  "compilerOptions": {
    "target": "ES2022",
    "module": "NodeNext",
    "moduleResolution": "NodeNext",
    "strict": true,
    "esModuleInterop": true,
    "skipLibCheck": true,
    "rootDir": ".",
    "outDir": "dist",
    "types": ["node", "vitest"]
  },
  "include": ["src/**/*.ts", "test/**/*.ts"]
}
```

- [ ] **步骤 4：编写失败的后端管理测试**

创建 `desktop/test/backend.test.ts`：

```typescript
import { describe, expect, it } from "vitest";
import {
  buildBackendEnv,
  getBackendExecutablePath,
  getFrontendIndexPath,
  normalizePort,
} from "../src/backend.js";

describe("desktop backend helpers", () => {
  it("resolves packaged backend executable path", () => {
    expect(
      getBackendExecutablePath({
        isPackaged: true,
        resourcesPath: "C:\\App\\resources",
        repoRoot: "C:\\Repo",
      }),
    ).toBe("C:\\App\\resources\\backend\\backend.exe");
  });

  it("resolves dev backend entry path", () => {
    expect(
      getBackendExecutablePath({
        isPackaged: false,
        resourcesPath: "C:\\App\\resources",
        repoRoot: "C:\\Repo",
      }),
    ).toBe("C:\\Repo\\backend\\desktop_entry.py");
  });

  it("resolves packaged frontend index path", () => {
    expect(
      getFrontendIndexPath({
        isPackaged: true,
        resourcesPath: "C:\\App\\resources",
        repoRoot: "C:\\Repo",
      }),
    ).toBe("C:\\App\\resources\\frontend\\index.html");
  });

  it("builds backend environment with desktop data dir", () => {
    const env = buildBackendEnv({
      baseEnv: { PATH: "C:\\Windows" },
      userDataPath: "C:\\Users\\Alice\\AppData\\Roaming\\Auto Email Sender",
    });

    expect(env.PATH).toBe("C:\\Windows");
    expect(env.AUTO_EMAIL_SENDER_DATA_DIR).toBe(
      "C:\\Users\\Alice\\AppData\\Roaming\\Auto Email Sender",
    );
    expect(env.ENABLE_BACKGROUND_WORKERS).toBe("true");
  });

  it("normalizes valid ports", () => {
    expect(normalizePort("48123")).toBe(48123);
  });
});
```

- [ ] **步骤 5：运行桌面测试验证失败**

运行：

```powershell
rtk pwsh -NoProfile -Command "cd desktop; npm test -- backend.test.ts"
```

预期：FAIL，提示无法找到 `../src/backend.js`。

- [ ] **步骤 6：创建共享类型**

创建 `desktop/src/types.ts`：

```typescript
export type BackendPathInput = {
  isPackaged: boolean;
  resourcesPath: string;
  repoRoot: string;
};

export type BackendEnvInput = {
  baseEnv: NodeJS.ProcessEnv;
  userDataPath: string;
};

export type BackendController = {
  baseUrl: string;
  stop: () => Promise<void>;
};

export type UpdateStatus =
  | { state: "idle"; version: string }
  | { state: "checking"; version: string }
  | { state: "available"; version: string; nextVersion: string }
  | { state: "not_available"; version: string }
  | { state: "downloading"; version: string; percent: number }
  | { state: "downloaded"; version: string; nextVersion: string }
  | { state: "error"; version: string; message: string };
```

- [ ] **步骤 7：实现后端管理模块**

创建 `desktop/src/backend.ts`：

```typescript
import { spawn, type ChildProcessWithoutNullStreams } from "node:child_process";
import { existsSync } from "node:fs";
import http from "node:http";
import path from "node:path";
import type { BackendController, BackendEnvInput, BackendPathInput } from "./types.js";

export function normalizePort(value: string): number {
  const port = Number(value);
  if (!Number.isInteger(port) || port < 1 || port > 65535) {
    throw new Error(`Invalid port: ${value}`);
  }
  return port;
}

export function getBackendExecutablePath(input: BackendPathInput): string {
  if (input.isPackaged) {
    return path.join(input.resourcesPath, "backend", "backend.exe");
  }
  return path.join(input.repoRoot, "backend", "desktop_entry.py");
}

export function getFrontendIndexPath(input: BackendPathInput): string {
  if (input.isPackaged) {
    return path.join(input.resourcesPath, "frontend", "index.html");
  }
  return path.join(input.repoRoot, "frontend", "dist", "index.html");
}

export function buildBackendEnv(input: BackendEnvInput): NodeJS.ProcessEnv {
  return {
    ...input.baseEnv,
    AUTO_EMAIL_SENDER_DATA_DIR: input.userDataPath,
    ENABLE_BACKGROUND_WORKERS: "true",
  };
}

export async function findAvailablePort(startPort = 48120): Promise<number> {
  for (let port = startPort; port < startPort + 100; port += 1) {
    if (await canListen(port)) {
      return port;
    }
  }
  throw new Error("No available backend port found.");
}

export async function startBackend(options: {
  isPackaged: boolean;
  resourcesPath: string;
  repoRoot: string;
  userDataPath: string;
}): Promise<BackendController> {
  const port = await findAvailablePort();
  const baseUrl = `http://127.0.0.1:${port}`;
  const backendPath = getBackendExecutablePath(options);

  if (!existsSync(backendPath)) {
    throw new Error(`Backend executable not found: ${backendPath}`);
  }

  const child = spawnBackend({
    backendPath,
    isPackaged: options.isPackaged,
    port,
    env: buildBackendEnv({
      baseEnv: process.env,
      userDataPath: options.userDataPath,
    }),
    repoRoot: options.repoRoot,
  });

  await waitForHealth(baseUrl, child);

  return {
    baseUrl,
    stop: () => stopBackend(child),
  };
}

function spawnBackend(input: {
  backendPath: string;
  isPackaged: boolean;
  port: number;
  env: NodeJS.ProcessEnv;
  repoRoot: string;
}): ChildProcessWithoutNullStreams {
  if (input.isPackaged) {
    return spawn(input.backendPath, ["--host", "127.0.0.1", "--port", String(input.port)], {
      env: input.env,
      windowsHide: true,
    });
  }

  return spawn("uv", ["run", "python", "desktop_entry.py", "--host", "127.0.0.1", "--port", String(input.port)], {
    cwd: path.join(input.repoRoot, "backend"),
    env: input.env,
    windowsHide: true,
  });
}

async function waitForHealth(baseUrl: string, child: ChildProcessWithoutNullStreams): Promise<void> {
  const deadline = Date.now() + 30_000;
  let stderr = "";
  child.stderr.on("data", (chunk: Buffer) => {
    stderr += chunk.toString("utf8");
  });

  while (Date.now() < deadline) {
    if (child.exitCode !== null) {
      throw new Error(`Backend exited before health check succeeded: ${stderr.slice(-800)}`);
    }
    if (await isHealthy(baseUrl)) {
      return;
    }
    await new Promise((resolve) => setTimeout(resolve, 400));
  }

  throw new Error(`Backend health check timed out: ${stderr.slice(-800)}`);
}

async function isHealthy(baseUrl: string): Promise<boolean> {
  return new Promise((resolve) => {
    const request = http.get(`${baseUrl}/health`, (response) => {
      response.resume();
      resolve(response.statusCode === 200);
    });
    request.on("error", () => resolve(false));
    request.setTimeout(800, () => {
      request.destroy();
      resolve(false);
    });
  });
}

async function canListen(port: number): Promise<boolean> {
  return new Promise((resolve) => {
    const server = http.createServer();
    server.once("error", () => resolve(false));
    server.once("listening", () => {
      server.close(() => resolve(true));
    });
    server.listen(port, "127.0.0.1");
  });
}

async function stopBackend(child: ChildProcessWithoutNullStreams): Promise<void> {
  if (child.exitCode !== null) {
    return;
  }

  child.kill();
  await new Promise<void>((resolve) => {
    const timeout = setTimeout(() => {
      if (child.exitCode === null) {
        child.kill("SIGKILL");
      }
      resolve();
    }, 3_000);
    child.once("exit", () => {
      clearTimeout(timeout);
      resolve();
    });
  });
}
```

- [ ] **步骤 8：创建 preload**

创建 `desktop/src/preload.ts`：

```typescript
import { contextBridge, ipcRenderer, type IpcRendererEvent } from "electron";
import type { UpdateStatus } from "./types.js";

const backendBaseUrl = process.argv
  .find((value) => value.startsWith("--backend-base-url="))
  ?.replace("--backend-base-url=", "");

contextBridge.exposeInMainWorld("autoEmailSender", {
  backendBaseUrl,
  getVersion: () => ipcRenderer.invoke("app:get-version") as Promise<string>,
  checkForUpdate: () => ipcRenderer.invoke("update:check") as Promise<UpdateStatus>,
  downloadUpdate: () => ipcRenderer.invoke("update:download") as Promise<UpdateStatus>,
  quitAndInstall: () => ipcRenderer.invoke("update:quit-and-install") as Promise<void>,
  onUpdateStatus: (callback: (status: UpdateStatus) => void) => {
    const listener = (_event: IpcRendererEvent, status: UpdateStatus) => callback(status);
    ipcRenderer.on("update:status", listener);
    return () => {
      ipcRenderer.removeListener("update:status", listener);
    };
  },
});
```

- [ ] **步骤 9：创建最小主进程**

创建 `desktop/src/main.ts`：

```typescript
import { app, BrowserWindow, dialog, ipcMain } from "electron";
import path from "node:path";
import { pathToFileURL } from "node:url";
import { getFrontendIndexPath, startBackend } from "./backend.js";
import type { BackendController } from "./types.js";

let mainWindow: BrowserWindow | null = null;
let backend: BackendController | null = null;

const repoRoot = path.resolve(app.getAppPath(), "..");

async function createWindow(): Promise<void> {
  backend = await startBackend({
    isPackaged: app.isPackaged,
    resourcesPath: process.resourcesPath,
    repoRoot,
    userDataPath: app.getPath("userData"),
  });

  mainWindow = new BrowserWindow({
    width: 1280,
    height: 860,
    minWidth: 1024,
    minHeight: 700,
    webPreferences: {
      preload: path.join(app.getAppPath(), "dist", "preload.js"),
      contextIsolation: true,
      nodeIntegration: false,
      additionalArguments: [`--backend-base-url=${backend.baseUrl}`],
    },
  });

  if (!app.isPackaged && process.argv.includes("--dev")) {
    await mainWindow.loadURL("http://127.0.0.1:5173");
    mainWindow.webContents.openDevTools({ mode: "detach" });
    return;
  }

  const indexPath = getFrontendIndexPath({
    isPackaged: app.isPackaged,
    resourcesPath: process.resourcesPath,
    repoRoot,
  });
  await mainWindow.loadURL(pathToFileURL(indexPath).toString());
}

ipcMain.handle("app:get-version", () => app.getVersion());

app.whenReady().then(() => {
  createWindow().catch((error: unknown) => {
    const message = error instanceof Error ? error.message : String(error);
    dialog.showErrorBox("启动失败", message);
    app.quit();
  });
});

app.on("window-all-closed", () => {
  app.quit();
});

app.on("before-quit", (event) => {
  if (backend === null) {
    return;
  }
  event.preventDefault();
  const currentBackend = backend;
  backend = null;
  currentBackend.stop().finally(() => app.exit(0));
});
```

- [ ] **步骤 10：运行桌面测试和类型检查**

运行：

```powershell
rtk pwsh -NoProfile -Command "cd desktop; npm test -- backend.test.ts; npm run typecheck"
```

预期：两个命令都 exit 0。

- [ ] **步骤 11：Commit Electron 基础壳**

运行：

```powershell
rtk pwsh -NoProfile -Command "git add desktop/package.json desktop/package-lock.json desktop/tsconfig.json desktop/src desktop/test; git commit -m 'feat(桌面端): 添加 Electron 启动壳'"
```

---

### 任务 4：桌面环境下的前端 API 地址适配

**文件：**
- 修改：`frontend/src/lib/api/client.ts`
- 创建：`frontend/src/types/desktop.d.ts`
- 创建：`frontend/src/lib/api/client.test.ts`

- [ ] **步骤 1：编写失败的 API base URL 测试**

创建 `frontend/src/lib/api/client.test.ts`：

```typescript
import { beforeEach, describe, expect, it } from "vitest";
import { buildApiPath, buildApiUrl } from "@/lib/api/client";

describe("api client desktop base url", () => {
  beforeEach(() => {
    Reflect.deleteProperty(window, "autoEmailSender");
  });

  it("uses relative paths in browser mode", () => {
    expect(buildApiPath("/api/ping")).toBe("/api/ping");
    expect(buildApiUrl("/api/ping")).toBe("http://localhost:3000/api/ping");
  });

  it("uses desktop backend base url when preload provides it", () => {
    window.autoEmailSender = {
      backendBaseUrl: "http://127.0.0.1:48123",
      getVersion: async () => "0.1.0",
      checkForUpdate: async () => ({ state: "not_available", version: "0.1.0" }),
      downloadUpdate: async () => ({ state: "not_available", version: "0.1.0" }),
      quitAndInstall: async () => undefined,
      onUpdateStatus: () => () => undefined,
    };

    expect(buildApiPath("/api/ping")).toBe("http://127.0.0.1:48123/api/ping");
    expect(buildApiUrl("/api/ping")).toBe("http://127.0.0.1:48123/api/ping");
  });
});
```

- [ ] **步骤 2：运行测试验证失败**

运行：

```powershell
rtk pwsh -NoProfile -Command "cd frontend; npm run test -- src/lib/api/client.test.ts"
```

预期：FAIL，提示 `autoEmailSender` 类型不存在或 `buildApiPath` 仍返回相对路径。

- [ ] **步骤 3：声明桌面 preload 类型**

创建 `frontend/src/types/desktop.d.ts`：

```typescript
export {};

export type DesktopUpdateStatus =
  | { state: "idle"; version: string }
  | { state: "checking"; version: string }
  | { state: "available"; version: string; nextVersion: string }
  | { state: "not_available"; version: string }
  | { state: "downloading"; version: string; percent: number }
  | { state: "downloaded"; version: string; nextVersion: string }
  | { state: "error"; version: string; message: string };

declare global {
  interface Window {
    autoEmailSender?: {
      backendBaseUrl?: string;
      getVersion: () => Promise<string>;
      checkForUpdate: () => Promise<DesktopUpdateStatus>;
      downloadUpdate: () => Promise<DesktopUpdateStatus>;
      quitAndInstall: () => Promise<void>;
      onUpdateStatus: (callback: (status: DesktopUpdateStatus) => void) => () => void;
    };
  }
}
```

- [ ] **步骤 4：更新 API URL 构造**

在 `frontend/src/lib/api/client.ts` 中加入：

```typescript
function getDesktopBackendBaseUrl(): string | null {
  const baseUrl = window.autoEmailSender?.backendBaseUrl?.trim();
  return baseUrl ? baseUrl.replace(/\/+$/, "") : null;
}
```

将 `buildApiPath` 和 `buildApiUrl` 改为：

```typescript
export const buildApiPath = (
  path: string,
  params?: Record<string, string | number | null | undefined>,
) => {
  const baseUrl = getDesktopBackendBaseUrl();
  const url = new URL(path, baseUrl ?? window.location.origin);
  Object.entries(params ?? {}).forEach(([key, value]) => {
    if (value === null || value === undefined || value === "") {
      return;
    }
    url.searchParams.set(key, String(value));
  });
  return baseUrl ? url.toString() : `${url.pathname}${url.search}`;
};

export const buildApiUrl = (
  path: string,
  params?: Record<string, string | number | null | undefined>,
) => {
  const apiPath = buildApiPath(path, params);
  return apiPath.startsWith("http") ? apiPath : new URL(apiPath, window.location.origin).toString();
};
```

- [ ] **步骤 5：运行 API 客户端测试**

运行：

```powershell
rtk pwsh -NoProfile -Command "cd frontend; npm run test -- src/lib/api/client.test.ts"
```

预期：PASS。

- [ ] **步骤 6：运行前端相关回归测试**

运行：

```powershell
rtk pwsh -NoProfile -Command "cd frontend; npm run test -- src/lib/api/client.test.ts src/lib/dateTime.test.ts"
```

预期：PASS。

- [ ] **步骤 7：Commit API 地址适配**

运行：

```powershell
rtk pwsh -NoProfile -Command "git add frontend/src/lib/api/client.ts frontend/src/lib/api/client.test.ts frontend/src/types/desktop.d.ts; git commit -m 'feat(桌面端): 支持前端访问内置后端'"
```

---

### 任务 5：自动更新 IPC 和前端检查更新入口

**文件：**
- 创建：`desktop/src/updates.ts`
- 修改：`desktop/src/main.ts`
- 创建：`desktop/test/updates.test.ts`
- 创建：`frontend/src/lib/desktopApi.ts`
- 创建：`frontend/src/lib/desktopApi.test.ts`
- 创建：`frontend/src/components/molecules/DesktopUpdateCard.tsx`
- 创建：`frontend/src/components/molecules/DesktopUpdateCard.test.tsx`
- 修改：`frontend/src/pages/ProfilePage.tsx`

- [ ] **步骤 1：编写失败的更新状态测试**

创建 `desktop/test/updates.test.ts`：

```typescript
import { describe, expect, it } from "vitest";
import { formatDownloadProgress } from "../src/updates.js";

describe("update helpers", () => {
  it("rounds download progress to one decimal place", () => {
    expect(formatDownloadProgress(47.236)).toBe(47.2);
  });
});
```

- [ ] **步骤 2：运行桌面更新测试验证失败**

运行：

```powershell
rtk pwsh -NoProfile -Command "cd desktop; npm test -- updates.test.ts"
```

预期：FAIL，提示无法找到 `../src/updates.js`。

- [ ] **步骤 3：实现更新 IPC 模块**

创建 `desktop/src/updates.ts`：

```typescript
import { app, BrowserWindow, ipcMain } from "electron";
import { autoUpdater } from "electron-updater";
import type { UpdateStatus } from "./types.js";

let currentStatus: UpdateStatus = { state: "idle", version: "0.0.0" };

export function formatDownloadProgress(percent: number): number {
  return Math.round(percent * 10) / 10;
}

export function registerUpdateIpc(getWindow: () => BrowserWindow | null): void {
  autoUpdater.autoDownload = false;
  currentStatus = { state: "idle", version: app.getVersion() };

  autoUpdater.on("checking-for-update", () => publish(getWindow, { state: "checking", version: app.getVersion() }));
  autoUpdater.on("update-available", (info) =>
    publish(getWindow, { state: "available", version: app.getVersion(), nextVersion: info.version }),
  );
  autoUpdater.on("update-not-available", () =>
    publish(getWindow, { state: "not_available", version: app.getVersion() }),
  );
  autoUpdater.on("download-progress", (progress) =>
    publish(getWindow, {
      state: "downloading",
      version: app.getVersion(),
      percent: formatDownloadProgress(progress.percent),
    }),
  );
  autoUpdater.on("update-downloaded", (info) =>
    publish(getWindow, { state: "downloaded", version: app.getVersion(), nextVersion: info.version }),
  );
  autoUpdater.on("error", (error) =>
    publish(getWindow, { state: "error", version: app.getVersion(), message: error.message }),
  );

  ipcMain.handle("update:check", async () => {
    if (!app.isPackaged) {
      currentStatus = { state: "not_available", version: app.getVersion() };
      return currentStatus;
    }
    await autoUpdater.checkForUpdates();
    return currentStatus;
  });

  ipcMain.handle("update:download", async () => {
    if (!app.isPackaged) {
      return currentStatus;
    }
    await autoUpdater.downloadUpdate();
    return currentStatus;
  });

  ipcMain.handle("update:quit-and-install", () => {
    autoUpdater.quitAndInstall(false, true);
  });
}

export function checkForUpdatesOnStartup(): void {
  if (!app.isPackaged) {
    return;
  }
  setTimeout(() => {
    autoUpdater.checkForUpdates().catch(() => undefined);
  }, 3_000);
}

function publish(getWindow: () => BrowserWindow | null, status: UpdateStatus): void {
  currentStatus = status;
  getWindow()?.webContents.send("update:status", status);
}
```

- [ ] **步骤 4：接入主进程更新 IPC**

修改 `desktop/src/main.ts`：

```typescript
import { checkForUpdatesOnStartup, registerUpdateIpc } from "./updates.js";
```

在 `ipcMain.handle("app:get-version", ...)` 后加入：

```typescript
registerUpdateIpc(() => mainWindow);
```

在窗口加载成功后加入：

```typescript
checkForUpdatesOnStartup();
```

- [ ] **步骤 5：编写失败的前端桌面 API 测试**

创建 `frontend/src/lib/desktopApi.test.ts`：

```typescript
import { beforeEach, describe, expect, it } from "vitest";
import { getDesktopAppVersion, isDesktopApp } from "@/lib/desktopApi";

describe("desktopApi", () => {
  beforeEach(() => {
    Reflect.deleteProperty(window, "autoEmailSender");
  });

  it("detects browser mode", () => {
    expect(isDesktopApp()).toBe(false);
  });

  it("reads desktop app version", async () => {
    window.autoEmailSender = {
      backendBaseUrl: "http://127.0.0.1:48123",
      getVersion: async () => "0.1.0",
      checkForUpdate: async () => ({ state: "not_available", version: "0.1.0" }),
      downloadUpdate: async () => ({ state: "not_available", version: "0.1.0" }),
      quitAndInstall: async () => undefined,
      onUpdateStatus: () => () => undefined,
    };

    expect(isDesktopApp()).toBe(true);
    await expect(getDesktopAppVersion()).resolves.toBe("0.1.0");
  });
});
```

- [ ] **步骤 6：实现前端桌面 API 封装**

创建 `frontend/src/lib/desktopApi.ts`：

```typescript
import type { DesktopUpdateStatus } from "@/types/desktop";

export const isDesktopApp = () => Boolean(window.autoEmailSender);

export async function getDesktopAppVersion(): Promise<string> {
  const api = getDesktopApi();
  return api.getVersion();
}

export async function checkForDesktopUpdate() {
  const api = getDesktopApi();
  return api.checkForUpdate();
}

export async function downloadDesktopUpdate() {
  const api = getDesktopApi();
  return api.downloadUpdate();
}

export async function quitAndInstallDesktopUpdate(): Promise<void> {
  const api = getDesktopApi();
  await api.quitAndInstall();
}

export function onDesktopUpdateStatus(callback: (status: DesktopUpdateStatus) => void) {
  const api = window.autoEmailSender;
  if (!api) {
    return () => undefined;
  }
  return api.onUpdateStatus(callback);
}

function getDesktopApi(): NonNullable<typeof window.autoEmailSender> {
  if (!window.autoEmailSender) {
    throw new Error("当前不是桌面应用环境");
  }
  return window.autoEmailSender;
}
```

如果 TypeScript 对条件类型可读性不佳，改用从 `frontend/src/types/desktop.d.ts` 导出的 `DesktopUpdateStatus` 类型。

- [ ] **步骤 7：创建更新卡片组件**

创建 `frontend/src/components/molecules/DesktopUpdateCard.tsx`：

```tsx
import { useEffect, useState } from "react";
import { Download, Loader2, RefreshCw, RotateCcw } from "lucide-react";
import {
  checkForDesktopUpdate,
  downloadDesktopUpdate,
  getDesktopAppVersion,
  isDesktopApp,
  onDesktopUpdateStatus,
  quitAndInstallDesktopUpdate,
} from "@/lib/desktopApi";
import type { DesktopUpdateStatus } from "@/types/desktop";

export function DesktopUpdateCard() {
  const [version, setVersion] = useState<string>("加载中");
  const [status, setStatus] = useState<DesktopUpdateStatus | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!isDesktopApp()) {
      return;
    }
    void getDesktopAppVersion().then(setVersion).catch(() => setVersion("未知"));
    return onDesktopUpdateStatus(setStatus);
  }, []);

  if (!isDesktopApp()) {
    return null;
  }

  const statusText = formatStatus(status);

  return (
    <section className="min-w-0 rounded-2xl border border-stone-200 bg-white px-6 py-5 shadow-sm">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <div className="flex flex-wrap items-center gap-2">
            <h2 className="text-xl font-semibold text-stone-900">桌面应用更新</h2>
            <span className="rounded-full border border-stone-200 bg-stone-50 px-3 py-1.5 text-xs text-stone-600">
              v{version}
            </span>
          </div>
          <p className="mt-2 text-sm leading-6 text-stone-600">{statusText}</p>
        </div>
        <div className="flex flex-wrap gap-3">
          <button
            type="button"
            disabled={busy}
            onClick={() => runAction(setBusy, () => checkForDesktopUpdate().then(setStatus))}
            className="ui-btn-secondary disabled:cursor-not-allowed disabled:opacity-60"
          >
            {busy ? <Loader2 className="h-4 w-4 animate-spin" /> : <RefreshCw className="h-4 w-4" />}
            检查更新
          </button>
          {status?.state === "available" ? (
            <button
              type="button"
              disabled={busy}
              onClick={() => runAction(setBusy, () => downloadDesktopUpdate().then(setStatus))}
              className="ui-btn-primary disabled:cursor-not-allowed disabled:opacity-60"
            >
              <Download className="h-4 w-4" />
              下载更新
            </button>
          ) : null}
          {status?.state === "downloaded" ? (
            <button
              type="button"
              onClick={() => void quitAndInstallDesktopUpdate()}
              className="ui-btn-primary"
            >
              <RotateCcw className="h-4 w-4" />
              重启并安装
            </button>
          ) : null}
        </div>
      </div>
    </section>
  );
}

function formatStatus(status: DesktopUpdateStatus | null): string {
  if (!status) {
    return "可手动检查是否有新版本。";
  }
  if (status.state === "checking") {
    return "正在检查更新...";
  }
  if (status.state === "available") {
    return `发现新版本 v${status.nextVersion}。`;
  }
  if (status.state === "not_available") {
    return "当前已是最新版本。";
  }
  if (status.state === "downloading") {
    return `正在下载更新：${status.percent}%`;
  }
  if (status.state === "downloaded") {
    return `新版本 v${status.nextVersion} 已下载，重启后安装。`;
  }
  if (status.state === "error") {
    return `更新失败：${status.message}`;
  }
  return "可手动检查是否有新版本。";
}

async function runAction(setBusy: (busy: boolean) => void, action: () => Promise<void>) {
  setBusy(true);
  try {
    await action();
  } finally {
    setBusy(false);
  }
}
```

- [ ] **步骤 8：编写更新卡片测试**

创建 `frontend/src/components/molecules/DesktopUpdateCard.test.tsx`：

```tsx
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { DesktopUpdateCard } from "@/components/molecules/DesktopUpdateCard";

describe("DesktopUpdateCard", () => {
  beforeEach(() => {
    Reflect.deleteProperty(window, "autoEmailSender");
  });

  it("does not render in browser mode", () => {
    const { container } = render(<DesktopUpdateCard />);
    expect(container).toBeEmptyDOMElement();
  });

  it("checks update in desktop mode", async () => {
    const checkForUpdate = vi.fn(async () => ({ state: "not_available" as const, version: "0.1.0" }));
    window.autoEmailSender = {
      backendBaseUrl: "http://127.0.0.1:48123",
      getVersion: async () => "0.1.0",
      checkForUpdate,
      downloadUpdate: async () => ({ state: "not_available", version: "0.1.0" }),
      quitAndInstall: async () => undefined,
      onUpdateStatus: () => () => undefined,
    };

    render(<DesktopUpdateCard />);

    expect(await screen.findByText("桌面应用更新")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /检查更新/ }));

    await waitFor(() => {
      expect(checkForUpdate).toHaveBeenCalledTimes(1);
    });
    expect(await screen.findByText("当前已是最新版本。")).toBeInTheDocument();
  });
});
```

- [ ] **步骤 9：接入个人中心**

在 `frontend/src/pages/ProfilePage.tsx` 顶部加入：

```typescript
import { DesktopUpdateCard } from "@/components/molecules/DesktopUpdateCard";
```

在底部现有 `<OtherSettingsCard />` 前加入：

```tsx
          <DesktopUpdateCard />
```

- [ ] **步骤 10：运行桌面和前端测试**

运行：

```powershell
rtk pwsh -NoProfile -Command "cd desktop; npm test -- updates.test.ts; npm run typecheck"
rtk pwsh -NoProfile -Command "cd frontend; npm run test -- src/lib/desktopApi.test.ts src/components/molecules/DesktopUpdateCard.test.tsx"
```

预期：全部 PASS。

- [ ] **步骤 11：Commit 自动更新入口**

运行：

```powershell
rtk pwsh -NoProfile -Command "git add desktop/src desktop/test frontend/src/lib/desktopApi.ts frontend/src/lib/desktopApi.test.ts frontend/src/components/molecules/DesktopUpdateCard.tsx frontend/src/components/molecules/DesktopUpdateCard.test.tsx frontend/src/pages/ProfilePage.tsx; git commit -m 'feat(桌面端): 添加应用内更新检查入口'"
```

---

### 任务 6：electron-builder 配置和本地安装包构建

**文件：**
- 创建：`desktop/electron-builder.yml`
- 修改：`desktop/package.json`
- 修改：`desktop/src/main.ts`

- [ ] **步骤 1：创建 electron-builder 配置**

创建 `desktop/electron-builder.yml`：

```yaml
appId: com.juniexd.autoemailsender
productName: Auto Email Sender
artifactName: "AutoEmailSender Setup ${version}.${ext}"
asar: true
directories:
  output: release
files:
  - dist/**
  - package.json
extraResources:
  - from: ../frontend/dist
    to: frontend
  - from: ../backend/dist/backend
    to: backend
win:
  target:
    - target: nsis
      arch:
        - x64
nsis:
  oneClick: true
  perMachine: false
  createDesktopShortcut: true
  createStartMenuShortcut: true
  shortcutName: Auto Email Sender
publish:
  provider: github
  owner: JunieXD
  repo: AutoEmailSender
```

- [ ] **步骤 2：让 desktop package 指向配置文件**

在 `desktop/package.json` 增加：

```json
{
  "build": {
    "extends": "./electron-builder.yml"
  }
}
```

如果 `electron-builder` 对 `extends` 行为不符合预期，改为在脚本中使用 `electron-builder --config electron-builder.yml`：

```json
"pack": "npm run build && electron-builder --config electron-builder.yml --win --dir",
"dist": "npm run build && electron-builder --config electron-builder.yml --win nsis --publish never",
"publish": "npm run build && electron-builder --config electron-builder.yml --win nsis --publish always"
```

- [ ] **步骤 3：构建前端、后端和桌面安装包**

运行：

```powershell
rtk pwsh -NoProfile -Command "cd frontend; npm ci; npm run build"
rtk pwsh -NoProfile -Command ".\scripts\build-backend.ps1 -Clean"
rtk pwsh -NoProfile -Command "cd desktop; npm ci; npm run dist"
```

预期：

- `frontend/dist/index.html` 存在。
- `backend/dist/backend/backend.exe` 存在。
- `desktop/release/AutoEmailSender Setup 0.1.0.exe` 存在。

- [ ] **步骤 4：运行 Electron 目录打包冒烟测试**

运行：

```powershell
rtk pwsh -NoProfile -Command "cd desktop; npm run pack"
```

预期：`desktop/release/win-unpacked/Auto Email Sender.exe` 存在。

- [ ] **步骤 5：Commit 安装包配置**

运行：

```powershell
rtk pwsh -NoProfile -Command "git add desktop/electron-builder.yml desktop/package.json; git commit -m 'build(桌面端): 配置 Windows 安装包构建'"
```

---

### 任务 7：GitHub Actions 自动发布 Release

**文件：**
- 创建：`.github/workflows/release.yml`

- [ ] **步骤 1：创建 Release workflow**

创建 `.github/workflows/release.yml`：

```yaml
name: Release Windows Desktop

on:
  push:
    tags:
      - "v*"

permissions:
  contents: write

jobs:
  build-windows:
    runs-on: windows-latest

    steps:
      - name: Checkout
        uses: actions/checkout@v4

      - name: Setup Node.js
        uses: actions/setup-node@v4
        with:
          node-version: 24

      - name: Setup Python
        uses: actions/setup-python@v5
        with:
          python-version: "3.12"

      - name: Setup uv
        uses: astral-sh/setup-uv@v5

      - name: Install frontend dependencies
        working-directory: frontend
        run: npm ci

      - name: Test frontend
        working-directory: frontend
        run: npm run test

      - name: Lint frontend
        working-directory: frontend
        run: npm run lint

      - name: Build frontend
        working-directory: frontend
        run: npm run build

      - name: Install backend dependencies
        working-directory: backend
        run: uv sync --dev

      - name: Test backend desktop runtime
        working-directory: backend
        run: uv run python -m unittest test.test_desktop_runtime

      - name: Build backend executable
        run: ./scripts/build-backend.ps1 -Clean
        shell: pwsh

      - name: Install desktop dependencies
        working-directory: desktop
        run: npm ci

      - name: Test desktop
        working-directory: desktop
        run: npm test

      - name: Build and publish desktop release
        working-directory: desktop
        env:
          GH_TOKEN: ${{ secrets.GITHUB_TOKEN }}
        run: npm run publish
```

- [ ] **步骤 2：本地 YAML 基础检查**

运行：

```powershell
rtk pwsh -NoProfile -Command "Get-Content '.github/workflows/release.yml' | Select-String 'tags:'; Get-Content '.github/workflows/release.yml' | Select-String 'contents: write'; Get-Content '.github/workflows/release.yml' | Select-String 'npm run publish'"
```

预期：三条 `Select-String` 都有匹配输出。

- [ ] **步骤 3：Commit Release workflow**

运行：

```powershell
rtk pwsh -NoProfile -Command "git add .github/workflows/release.yml; git commit -m 'ci(发布): 添加 Windows 桌面版自动发布流程'"
```

---

### 任务 8：一键发布脚本

**文件：**
- 创建：`scripts/release.ps1`
- 修改：`frontend/package.json`
- 修改：`frontend/package-lock.json`
- 修改：`desktop/package.json`
- 修改：`desktop/package-lock.json`

- [ ] **步骤 1：创建发布脚本**

创建 `scripts/release.ps1`：

```powershell
param(
  [Parameter(Mandatory = $true, Position = 0)]
  [ValidatePattern('^\d+\.\d+\.\d+(-[0-9A-Za-z.-]+)?$')]
  [string]$Version,

  [switch]$DryRun
)

$ErrorActionPreference = "Stop"
$RepoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")

function Run-Git {
  param([Parameter(ValueFromRemainingArguments = $true)][string[]]$Args)
  if ($DryRun) {
    Write-Host "[dry-run] git $($Args -join ' ')"
    return
  }
  git @Args
}

function Assert-CleanRepository {
  $branch = git branch --show-current
  if ($branch -ne "master") {
    throw "发布必须在 master 分支执行，当前分支是 $branch。"
  }

  $status = git status --porcelain
  if ($status) {
    throw "工作区存在未提交改动，请先提交或清理后再发布。"
  }
}

function Set-NpmVersion {
  param([string]$Directory)
  Push-Location (Join-Path $RepoRoot $Directory)
  try {
    if ($DryRun) {
      Write-Host "[dry-run] npm version $Version --no-git-tag-version in $Directory"
      return
    }
    npm version $Version --no-git-tag-version
  } finally {
    Pop-Location
  }
}

Assert-CleanRepository
Set-NpmVersion "desktop"
Set-NpmVersion "frontend"

Run-Git add desktop/package.json desktop/package-lock.json frontend/package.json frontend/package-lock.json
Run-Git commit -m "chore(release): v$Version"
Run-Git tag "v$Version"
Run-Git push origin master
Run-Git push origin "v$Version"

Write-Host "已发布 v$Version。GitHub Actions 将自动创建 Release。"
```

- [ ] **步骤 2：运行 dry-run 验证**

运行：

```powershell
rtk pwsh -NoProfile -Command ".\scripts\release.ps1 0.1.1 -DryRun"
```

预期：输出包含：

```text
[dry-run] npm version 0.1.1 --no-git-tag-version in desktop
[dry-run] npm version 0.1.1 --no-git-tag-version in frontend
[dry-run] git tag v0.1.1
```

- [ ] **步骤 3：Commit 发布脚本**

运行：

```powershell
rtk pwsh -NoProfile -Command "git add scripts/release.ps1; git commit -m 'build(发布): 添加一键发布脚本'"
```

---

### 任务 9：README 用户说明和发布说明

**文件：**
- 修改：`README.md`

- [ ] **步骤 1：更新 README 的 Windows 安装说明**

在 `README.md` 的「快速开始」前新增：

```markdown
## Windows 安装版

普通用户推荐下载 Windows 安装版：

1. 打开 [Releases](https://github.com/JunieXD/AutoEmailSender/releases)。
2. 下载最新的 `AutoEmailSender Setup x.y.z.exe`。
3. 双击安装并从桌面快捷方式打开。

安装版会自动启动本地后端，不需要安装 Python、Node.js、uv、npm 或 Git。

第一版安装包暂未购买 Windows 代码签名证书。安装时如果看到「未知发布者」或 SmartScreen 提示，请确认下载来源是本项目 GitHub Releases 页面。
```

在「快速开始」后新增开发者发布说明：

````markdown
## 发布 Windows 新版本

正式发布分支是 `master`。发布前确保工作区干净，并确认前端、后端和桌面构建验证通过。

运行：

```powershell
rtk pwsh -NoProfile -Command ".\scripts\release.ps1 0.1.1"
```

脚本会更新版本号、创建发布提交、创建 `v0.1.1` tag，并推送到 GitHub。tag 推送后，GitHub Actions 会自动构建 Windows 安装包并创建 Release。
````

- [ ] **步骤 2：检查 README 文案**

运行：

```powershell
rtk pwsh -NoProfile -Command "Select-String -Path README.md -Pattern 'Windows 安装版','release.ps1','未知发布者'"
```

预期：三类关键词都有匹配输出。

- [ ] **步骤 3：Commit 文档**

运行：

```powershell
rtk pwsh -NoProfile -Command "git add README.md; git commit -m 'docs(发布): 补充 Windows 安装版说明'"
```

---

### 任务 10：完整本地验证

**文件：**
- 验证：前端、后端、桌面和构建产物。

- [ ] **步骤 1：运行后端验证**

运行：

```powershell
rtk pwsh -NoProfile -Command "cd backend; uv run python -m unittest test.test_desktop_runtime test.test_runtime_settings_api"
```

预期：PASS，输出包含 `OK`。

- [ ] **步骤 2：运行前端验证**

运行：

```powershell
rtk pwsh -NoProfile -Command "cd frontend; npm run lint; npm run test; npm run build"
```

预期：三个命令都 exit 0。

- [ ] **步骤 3：运行桌面验证**

运行：

```powershell
rtk pwsh -NoProfile -Command "cd desktop; npm test; npm run typecheck"
```

预期：两个命令都 exit 0。

- [ ] **步骤 4：运行完整打包验证**

运行：

```powershell
rtk pwsh -NoProfile -Command ".\scripts\build-backend.ps1 -Clean"
rtk pwsh -NoProfile -Command "cd frontend; npm run build"
rtk pwsh -NoProfile -Command "cd desktop; npm run dist"
```

预期：

- `backend/dist/backend/backend.exe` 存在。
- `frontend/dist/index.html` 存在。
- `desktop/release/AutoEmailSender Setup 0.1.0.exe` 存在。

- [ ] **步骤 5：运行后端可执行文件健康检查**

运行：

```powershell
rtk pwsh -NoProfile -Command "$env:AUTO_EMAIL_SENDER_DATA_DIR=(Join-Path $pwd 'data\desktop-smoke'); $p=Start-Process -FilePath '.\backend\dist\backend\backend.exe' -ArgumentList '--host','127.0.0.1','--port','48123' -PassThru -WindowStyle Hidden; try { Start-Sleep -Seconds 8; Invoke-RestMethod 'http://127.0.0.1:48123/health' | ConvertTo-Json -Compress } finally { Stop-Process -Id $p.Id -Force -ErrorAction SilentlyContinue }"
```

预期：输出 `{"status":"ok"}`。

- [ ] **步骤 6：检查工作区**

运行：

```powershell
rtk pwsh -NoProfile -Command "git status --short"
```

预期：没有未提交变更；如果构建产生未忽略文件，只更新 `.gitignore` 并 commit。

- [ ] **步骤 7：整理提交记录**

运行：

```powershell
rtk pwsh -NoProfile -Command "git log --oneline -10"
```

预期：能看到桌面端、后端打包、自动更新、CI、发布脚本和文档相关提交。

---

## 参考资料

- Electron 更新发布教程：https://www.electronjs.org/docs/latest/tutorial/tutorial-publishing-updating
- electron-builder 自动更新文档：https://www.electron.build/auto-update.html
- electron-builder GitHub 发布配置：https://www.electron.build/publish.html
- PyInstaller 官方文档：https://pyinstaller.org/en/stable/
- GitHub Actions workflow 语法：https://docs.github.com/actions/using-workflows/workflow-syntax-for-github-actions
