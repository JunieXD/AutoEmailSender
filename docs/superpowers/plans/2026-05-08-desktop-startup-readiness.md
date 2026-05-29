# 桌面端启动就绪状态体验实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 将桌面端启动体验从“30 秒 readiness 超时报错”改为“后端进程先启动、业务初始化阶段可见、ready 前禁用写操作、完成后自动恢复”。

**架构：** 后端新增结构化启动状态 `/startup-status`，保留 `/health` 和 `/ready` 的既有职责。Electron 主进程先等待 `/health` 创建窗口，再持续轮询 `/startup-status` 并通过 IPC 发布阶段状态。前端新增桌面启动状态上下文和全局提示，业务未 ready 时禁用保存身份等写操作。

**技术栈：** FastAPI、Alembic、Electron、TypeScript、React、Vitest、unittest。

---

## 文件结构

- 修改：`backend/main.py`
  - 维护 `app.state.startup_status`。
  - 在 `initialize_runtime()` 各阶段更新状态。
  - 新增 `GET /startup-status`。
  - 保持 `/health` 和 `/ready` 兼容。
- 修改：`backend/test/test_desktop_runtime.py`
  - 覆盖 `/startup-status` 在 starting、ready、error 阶段的行为。
- 修改：`desktop/src/types.ts`
  - 扩展 `BackendStatus`，新增启动阶段类型。
  - 扩展 `BackendController`，保留 `ready` Promise 兼容现有调用。
- 修改：`desktop/src/backend.ts`
  - 新增 `/health` 等待逻辑。
  - 新增 `/startup-status` 轮询逻辑。
  - 30 秒不再失败，只进入长启动提示。
  - 硬超时后才进入 error。
- 修改：`desktop/src/main.ts`
  - 使用新的 `status` 事件源发布结构化 backend status。
  - ready 后继续触发更新检查。
- 修改：`desktop/test/backend.test.ts`
  - 覆盖 `/health` 成功后可返回 controller。
  - 覆盖 startup status 从 starting 到 ready。
  - 覆盖 startup status error 和硬超时。
- 修改：`frontend/src/types/desktop.d.ts`
  - 与桌面端 `BackendStatus` 对齐。
- 创建：`frontend/src/context/DesktopBackendContext.tsx`
  - 统一保存桌面后端状态。
  - 提供 `backendReady`、`startupMessage`、`disableReason`。
- 创建：`frontend/src/components/organisms/DesktopStartupStatusBanner.tsx`
  - 显示“正在准备本地数据”等全局提示。
- 修改：`frontend/src/App.tsx`
  - 接入 `DesktopBackendProvider` 和 `DesktopStartupStatusBanner`。
  - ready 时仍调用 `updateDesktopBackendBaseUrl()`。
- 修改：`frontend/src/lib/api/client.ts`
  - 等待桌面后端 ready 的错误文案改为用户可理解文本。
  - 兼容新的 backend status 类型。
- 修改：`frontend/src/lib/api/client.test.ts`
  - 覆盖 starting 状态不会触发 fetch。
  - 覆盖 error 状态转译为“系统准备失败”。
- 修改：`frontend/src/pages/ProfilePage.tsx`
  - 保存身份按钮在 desktop backend 未 ready 时禁用。
  - 非 ready 时保存身份提示“系统正在准备本地数据”，不再提示“身份保存失败”。
- 新增或修改：`frontend/src/pages/ProfilePageOnboarding.test.tsx`
  - 覆盖保存身份按钮禁用和文案。

## 任务 1：后端启动状态接口

**文件：**
- 修改：`backend/main.py`
- 测试：`backend/test/test_desktop_runtime.py`

- [ ] **步骤 1：编写 starting 状态测试**

在 `backend/test/test_desktop_runtime.py` 的 `DesktopRuntimeTests` 中新增测试：

```python
def test_startup_status_reports_database_migration_phase(self) -> None:
    os.environ["ENABLE_BACKGROUND_WORKERS"] = "1"

    from app.core.config import get_settings
    import main as main_module

    get_settings.cache_clear()
    schema_started = False

    async def slow_schema() -> None:
        nonlocal schema_started
        schema_started = True
        await asyncio.Event().wait()

    with (
        patch.object(main_module, "ensure_database_schema", slow_schema),
        patch.object(main_module.RuntimeManager, "start", new_callable=AsyncMock),
    ):
        with TestClient(main_module.create_app()) as client:
            response = client.get("/startup-status")

    self.assertTrue(schema_started)
    self.assertEqual(response.status_code, 200, msg=response.text)
    data = response.json()
    self.assertEqual(data["state"], "starting")
    self.assertEqual(data["phase"], "migrating_database")
    self.assertEqual(data["message"], "正在检查和升级本地数据")
    self.assertIsNone(data["error"])
    self.assertIsInstance(data["elapsed_seconds"], int)
```

- [ ] **步骤 2：运行测试验证失败**

运行：

```powershell
rtk uv run python -m unittest backend.test.test_desktop_runtime.DesktopRuntimeTests.test_startup_status_reports_database_migration_phase
```

预期：失败，`/startup-status` 返回 404。

- [ ] **步骤 3：实现启动状态模型和接口**

在 `backend/main.py` 中新增导入：

```python
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
```

在 `logger = logging.getLogger(__name__)` 下方新增：

```python
@dataclass(slots=True)
class StartupStatus:
    state: str
    phase: str
    message: str
    started_at: datetime
    updated_at: datetime
    error: str | None = None

    def to_response(self) -> dict[str, object]:
        payload = asdict(self)
        payload["started_at"] = self.started_at.isoformat()
        payload["updated_at"] = self.updated_at.isoformat()
        payload["elapsed_seconds"] = max(
            0,
            int((datetime.now(UTC) - self.started_at).total_seconds()),
        )
        return payload


STARTUP_PHASE_MESSAGES = {
    "starting": "正在启动系统服务",
    "migrating_database": "正在检查和升级本地数据",
    "cleaning_logs": "正在整理本地运行日志",
    "starting_workers": "正在启动后台任务",
    "ready": "系统已准备就绪",
    "error": "系统准备失败",
}


def initialize_startup_status(app: FastAPI) -> None:
    now = datetime.now(UTC)
    app.state.startup_status = StartupStatus(
        state="starting",
        phase="starting",
        message=STARTUP_PHASE_MESSAGES["starting"],
        started_at=now,
        updated_at=now,
    )


def set_startup_status(
    app: FastAPI,
    *,
    state: str,
    phase: str,
    error: str | None = None,
) -> None:
    current = getattr(app.state, "startup_status", None)
    now = datetime.now(UTC)
    started_at = current.started_at if current is not None else now
    app.state.startup_status = StartupStatus(
        state=state,
        phase=phase,
        message=STARTUP_PHASE_MESSAGES[phase],
        started_at=started_at,
        updated_at=now,
        error=error,
    )
```

在 `lifespan()` 开头设置状态：

```python
initialize_startup_status(app)
app.state.runtime_ready = False
```

在 `initialize_runtime()` 中按阶段更新：

```python
set_startup_status(app, state="starting", phase="migrating_database")
await ensure_database_schema()
set_startup_status(app, state="starting", phase="cleaning_logs")
async with get_session_factory()() as session:
    await cleanup_old_operation_logs(session)
    await session.commit()
set_startup_status(app, state="starting", phase="starting_workers")
if get_settings().enable_background_workers:
    runtime_manager = RuntimeManager(get_session_factory())
    await runtime_manager.start()
    app.state.runtime_manager = runtime_manager
app.state.runtime_ready = True
set_startup_status(app, state="ready", phase="ready")
```

在异常分支中更新 error：

```python
except Exception as exc:
    app.state.runtime_error = str(exc)
    set_startup_status(app, state="error", phase="error", error=str(exc))
    raise
```

在 `create_app()` 中新增接口：

```python
@app.get("/startup-status")
async def startup_status() -> dict[str, object]:
    status = getattr(app.state, "startup_status", None)
    if status is None:
        initialize_startup_status(app)
        status = app.state.startup_status
    return status.to_response()
```

- [ ] **步骤 4：运行 starting 状态测试验证通过**

运行：

```powershell
rtk uv run python -m unittest backend.test.test_desktop_runtime.DesktopRuntimeTests.test_startup_status_reports_database_migration_phase
```

预期：通过。

- [ ] **步骤 5：补充 ready 和 error 测试**

在同一测试类新增：

```python
def test_startup_status_reports_ready_after_runtime_initialization(self) -> None:
    os.environ["ENABLE_BACKGROUND_WORKERS"] = "0"

    from app.core.config import get_settings
    from main import create_app

    get_settings.cache_clear()
    with TestClient(create_app()) as client:
        response = client.get("/startup-status")

    self.assertEqual(response.status_code, 200, msg=response.text)
    data = response.json()
    self.assertEqual(data["state"], "ready")
    self.assertEqual(data["phase"], "ready")
    self.assertEqual(data["message"], "系统已准备就绪")
    self.assertIsNone(data["error"])


def test_startup_status_reports_error_without_http_500(self) -> None:
    os.environ["ENABLE_BACKGROUND_WORKERS"] = "1"

    from app.core.config import get_settings
    import main as main_module

    get_settings.cache_clear()

    async def failing_schema() -> None:
        raise RuntimeError("database is locked")

    with patch.object(main_module, "ensure_database_schema", failing_schema):
        with TestClient(main_module.create_app()) as client:
            response = client.get("/startup-status")
            ready_response = client.get("/ready")

    self.assertEqual(response.status_code, 200, msg=response.text)
    data = response.json()
    self.assertEqual(data["state"], "error")
    self.assertEqual(data["phase"], "error")
    self.assertEqual(data["message"], "系统准备失败")
    self.assertEqual(data["error"], "database is locked")
    self.assertEqual(ready_response.status_code, 500)
```

- [ ] **步骤 6：运行后端桌面运行时测试**

运行：

```powershell
rtk uv run python -m unittest backend.test.test_desktop_runtime
```

预期：全部通过。

- [ ] **步骤 7：Commit**

```powershell
rtk git add backend/main.py backend/test/test_desktop_runtime.py
rtk git commit -m "feat(backend): expose startup readiness status"
```

## 任务 2：桌面端启动轮询状态机

**文件：**
- 修改：`desktop/src/types.ts`
- 修改：`desktop/src/backend.ts`
- 修改：`desktop/src/main.ts`
- 测试：`desktop/test/backend.test.ts`

- [ ] **步骤 1：扩展桌面端类型**

在 `desktop/src/types.ts` 中新增：

```typescript
export type BackendStartupPhase =
  | "starting"
  | "migrating_database"
  | "cleaning_logs"
  | "starting_workers"
  | "ready"
  | "error";

export type BackendStartupStatus = {
  state: "starting" | "ready" | "error";
  phase: BackendStartupPhase;
  message: string;
  elapsed_seconds: number;
  error: string | null;
};
```

将 `BackendStatus` 改为：

```typescript
export type BackendStatus =
  | {
      state: "starting";
      phase: Exclude<BackendStartupPhase, "ready" | "error">;
      message: string;
      elapsedSeconds: number;
      slowStartup: boolean;
      verySlowStartup: boolean;
    }
  | { state: "restarting"; code: number | null; signal: NodeJS.Signals | null }
  | {
      state: "ready";
      baseUrl: string;
      phase: "ready";
      message: string;
      elapsedSeconds: number;
    }
  | {
      state: "error";
      message: string;
      phase: "error";
      elapsedSeconds: number;
      detail?: string;
    };
```

将 `BackendController` 改为：

```typescript
export type BackendController = {
  baseUrl: string;
  ready: Promise<void>;
  onStatus: (handler: (status: BackendStatus) => void) => () => void;
  stop: () => Promise<void>;
};
```

- [ ] **步骤 2：编写桌面端 helper 测试**

在 `desktop/test/backend.test.ts` 中补充导入：

```typescript
import { createServer, type Server } from "node:http";
import { AddressInfo } from "node:net";
```

新增本地测试服务器辅助函数：

```typescript
async function withStartupServer(
  statuses: Array<{ state: "starting" | "ready" | "error"; phase: string; message: string; elapsed_seconds: number; error: string | null }>,
  test: (baseUrl: string) => Promise<void>,
): Promise<void> {
  let statusIndex = 0;
  const server = createServer((request, response) => {
    if (request.url === "/health") {
      response.writeHead(200, { "Content-Type": "application/json" });
      response.end(JSON.stringify({ status: "ok" }));
      return;
    }
    if (request.url === "/startup-status") {
      const status = statuses[Math.min(statusIndex, statuses.length - 1)];
      statusIndex += 1;
      response.writeHead(200, { "Content-Type": "application/json" });
      response.end(JSON.stringify(status));
      return;
    }
    response.writeHead(404);
    response.end();
  });

  await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", resolve));
  const address = server.address() as AddressInfo;
  try {
    await test(`http://127.0.0.1:${address.port}`);
  } finally {
    await new Promise<void>((resolve, reject) =>
      server.close((error) => (error ? reject(error) : resolve())),
    );
  }
}
```

新增测试：

```typescript
it("polls startup status until the backend is ready", async () => {
  const observed: string[] = [];

  await withStartupServer(
    [
      {
        state: "starting",
        phase: "migrating_database",
        message: "正在检查和升级本地数据",
        elapsed_seconds: 3,
        error: null,
      },
      {
        state: "ready",
        phase: "ready",
        message: "系统已准备就绪",
        elapsed_seconds: 4,
        error: null,
      },
    ],
    async (baseUrl) => {
      const ready = waitForStartupStatus(baseUrl, {
        onStatus: (status) => observed.push(status.state),
        pollIntervalMs: 1,
        hardTimeoutMs: 1_000,
      });

      await expect(ready).resolves.toBeUndefined();
    },
  );

  expect(observed).toEqual(["starting", "ready"]);
});
```

再新增 error 测试：

```typescript
it("fails startup polling when startup status reports error", async () => {
  await withStartupServer(
    [
      {
        state: "error",
        phase: "error",
        message: "系统准备失败",
        elapsed_seconds: 5,
        error: "database is locked",
      },
    ],
    async (baseUrl) => {
      await expect(
        waitForStartupStatus(baseUrl, {
          onStatus: () => undefined,
          pollIntervalMs: 1,
          hardTimeoutMs: 1_000,
        }),
      ).rejects.toThrow("系统准备失败");
    },
  );
});
```

- [ ] **步骤 3：运行桌面端测试验证失败**

运行：

```powershell
cd desktop; rtk npm test -- backend.test.ts
```

预期：失败，`waitForStartupStatus` 未导出。

- [ ] **步骤 4：实现 `/startup-status` 轮询 helper**

在 `desktop/src/backend.ts` 中导入类型：

```typescript
import type {
  BackendController,
  BackendEnvInput,
  BackendExit,
  BackendExitHandler,
  BackendPathInput,
  BackendStartupStatus,
  BackendStatus,
} from "./types.js";
```

导出 helper：

```typescript
export async function waitForStartupStatus(
  baseUrl: string,
  options: {
    onStatus: (status: BackendStatus) => void;
    pollIntervalMs?: number;
    hardTimeoutMs?: number;
  },
): Promise<void> {
  const pollIntervalMs = options.pollIntervalMs ?? 800;
  const hardTimeoutMs = options.hardTimeoutMs ?? 10 * 60_000;
  const deadline = Date.now() + hardTimeoutMs;
  let lastStatus: BackendStatus | null = null;

  while (Date.now() < deadline) {
    const status = await fetchStartupStatus(baseUrl);
    if (status.state === "ready") {
      const readyStatus: BackendStatus = {
        state: "ready",
        baseUrl,
        phase: "ready",
        message: status.message,
        elapsedSeconds: status.elapsed_seconds,
      };
      options.onStatus(readyStatus);
      return;
    }

    if (status.state === "error") {
      const message = "系统准备失败";
      const errorStatus: BackendStatus = {
        state: "error",
        phase: "error",
        message,
        elapsedSeconds: status.elapsed_seconds,
        detail: status.error ?? status.message,
      };
      options.onStatus(errorStatus);
      throw new Error(`${message}: ${errorStatus.detail ?? ""}`.trim());
    }

    lastStatus = {
      state: "starting",
      phase: status.phase === "ready" || status.phase === "error" ? "starting" : status.phase,
      message: status.message,
      elapsedSeconds: status.elapsed_seconds,
      slowStartup: status.elapsed_seconds >= 30,
      verySlowStartup: status.elapsed_seconds >= 120,
    };
    options.onStatus(lastStatus);
    await new Promise((resolve) => setTimeout(resolve, pollIntervalMs));
  }

  const elapsedSeconds = lastStatus?.state === "starting" ? lastStatus.elapsedSeconds : Math.round(hardTimeoutMs / 1000);
  const message = "系统准备时间过长";
  options.onStatus({
    state: "error",
    phase: "error",
    message,
    elapsedSeconds,
    detail: "启动状态轮询超过 10 分钟仍未完成",
  });
  throw new Error(message);
}

async function fetchStartupStatus(baseUrl: string): Promise<BackendStartupStatus> {
  return new Promise((resolve, reject) => {
    const request = http.get(`${baseUrl}/startup-status`, (response) => {
      let body = "";
      response.setEncoding("utf8");
      response.on("data", (chunk) => {
        body += chunk;
      });
      response.on("end", () => {
        if (response.statusCode !== 200) {
          reject(new Error(`Startup status request failed: ${response.statusCode}`));
          return;
        }
        try {
          resolve(JSON.parse(body) as BackendStartupStatus);
        } catch (error) {
          reject(error);
        }
      });
    });
    request.on("error", reject);
    request.setTimeout(1_000, () => {
      request.destroy(new Error("Startup status request timed out"));
    });
  });
}
```

- [ ] **步骤 5：运行 helper 测试验证通过**

运行：

```powershell
cd desktop; rtk npm test -- backend.test.ts
```

预期：新增 helper 测试通过；如果旧测试因类型变更失败，按新的 `BackendStatus` 补齐测试数据字段。

- [ ] **步骤 6：接入 `startBackend()` 和 `main.ts`**

在 `desktop/src/backend.ts` 中，`startBackend()` 创建状态订阅集合：

```typescript
const statusHandlers = new Set<(status: BackendStatus) => void>();
const emitStatus = (status: BackendStatus) => {
  statusHandlers.forEach((handler) => handler(status));
};
```

返回 controller 时改为：

```typescript
return {
  baseUrl,
  ready: waitForReady(baseUrl, child, emitStatus),
  onStatus: (handler) => {
    statusHandlers.add(handler);
    return () => statusHandlers.delete(handler);
  },
  stop: () => stopBackend(child, lifecycle),
};
```

将 `waitForReady()` 改为：

```typescript
async function waitForReady(
  baseUrl: string,
  child: ChildProcessWithoutNullStreams,
  onStatus: (status: BackendStatus) => void,
): Promise<void> {
  let stderr = "";
  child.stderr.on("data", (chunk: Buffer) => {
    stderr += chunk.toString("utf8");
  });

  await waitForHealth(baseUrl, child, () => stderr);
  try {
    await waitForStartupStatus(baseUrl, { onStatus });
  } catch (error) {
    if (child.exitCode !== null) {
      throw new Error(`后端进程已退出：${stderr.slice(-800)}`);
    }
    throw error;
  }
}
```

新增 `waitForHealth()`：

```typescript
async function waitForHealth(
  baseUrl: string,
  child: ChildProcessWithoutNullStreams,
  getStderr: () => string,
): Promise<void> {
  const deadline = Date.now() + 30_000;
  while (Date.now() < deadline) {
    if (child.exitCode !== null) {
      throw new Error(`Backend exited before health check succeeded: ${getStderr().slice(-800)}`);
    }
    if (await isEndpointOk(`${baseUrl}/health`)) {
      return;
    }
    await new Promise((resolve) => setTimeout(resolve, 400));
  }
  throw new Error(`Backend health check timed out: ${getStderr().slice(-800)}`);
}
```

将 `isReady()` 替换为通用：

```typescript
async function isEndpointOk(url: string): Promise<boolean> {
  return new Promise((resolve) => {
    const request = http.get(url, (response) => {
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
```

在 `desktop/src/main.ts` 的 `publishBackendReady()` 中订阅状态：

```typescript
function publishBackendReady(controller: BackendController): void {
  publishBackendStatus({
    state: "starting",
    phase: "starting",
    message: "正在启动系统服务",
    elapsedSeconds: 0,
    slowStartup: false,
    verySlowStartup: false,
  });
  const unsubscribe = controller.onStatus((status) => publishBackendStatus(status));
  controller.ready
    .then(() => {
      unsubscribe();
      checkForUpdatesOnStartup();
    })
    .catch((error: unknown) => {
      unsubscribe();
      const message = error instanceof Error ? error.message : String(error);
      publishBackendStatus({
        state: "error",
        message,
        phase: "error",
        elapsedSeconds: 0,
      });
    });
}
```

- [ ] **步骤 7：运行桌面端 typecheck 和测试**

运行：

```powershell
cd desktop; rtk npm run typecheck
cd desktop; rtk npm test -- backend.test.ts
```

预期：全部通过。

- [ ] **步骤 8：Commit**

```powershell
rtk git add desktop/src/types.ts desktop/src/backend.ts desktop/src/main.ts desktop/test/backend.test.ts
rtk git commit -m "feat(desktop): report backend startup phases"
```

## 任务 3：前端桌面后端状态上下文和全局提示

**文件：**
- 修改：`frontend/src/types/desktop.d.ts`
- 创建：`frontend/src/context/DesktopBackendContext.tsx`
- 创建：`frontend/src/components/organisms/DesktopStartupStatusBanner.tsx`
- 修改：`frontend/src/App.tsx`

- [ ] **步骤 1：更新前端桌面类型**

在 `frontend/src/types/desktop.d.ts` 中新增：

```typescript
export type DesktopBackendStartupPhase =
  | "starting"
  | "migrating_database"
  | "cleaning_logs"
  | "starting_workers"
  | "ready"
  | "error";
```

将 `DesktopBackendStatus` 改为：

```typescript
export type DesktopBackendStatus =
  | {
      state: "starting";
      phase: Exclude<DesktopBackendStartupPhase, "ready" | "error">;
      message: string;
      elapsedSeconds: number;
      slowStartup: boolean;
      verySlowStartup: boolean;
    }
  | { state: "restarting"; code: number | null; signal: string | null }
  | {
      state: "ready";
      baseUrl: string;
      phase: "ready";
      message: string;
      elapsedSeconds: number;
    }
  | {
      state: "error";
      message: string;
      phase: "error";
      elapsedSeconds: number;
      detail?: string;
    };
```

- [ ] **步骤 2：创建后端状态上下文**

创建 `frontend/src/context/DesktopBackendContext.tsx`：

```tsx
import {
  createContext,
  useContext,
  useEffect,
  useMemo,
  useState,
  type PropsWithChildren,
} from "react";
import type { DesktopBackendStatus } from "@/types/desktop";
import { updateDesktopBackendBaseUrl } from "@/lib/api/client";
import { recordDiagnosticEvent } from "@/lib/diagnostics";

type DesktopBackendContextValue = {
  status: DesktopBackendStatus | null;
  isDesktop: boolean;
  isReady: boolean;
  disableReason: string | null;
};

const DesktopBackendContext = createContext<DesktopBackendContextValue | null>(null);

export const DesktopBackendProvider = ({ children }: PropsWithChildren) => {
  const isDesktop = Boolean(window.autoEmailSender);
  const [status, setStatus] = useState<DesktopBackendStatus | null>(() =>
    isDesktop
      ? {
          state: "starting",
          phase: "starting",
          message: "正在启动系统服务",
          elapsedSeconds: 0,
          slowStartup: false,
          verySlowStartup: false,
        }
      : null,
  );

  useEffect(() => {
    const unsubscribe = window.autoEmailSender?.onBackendStatus?.((nextStatus) => {
      setStatus(nextStatus);
      if (nextStatus.state === "ready") {
        updateDesktopBackendBaseUrl(nextStatus.baseUrl);
      }

      try {
        recordDiagnosticEvent({
          level: nextStatus.state === "error" ? "error" : "info",
          category: "system",
          eventName: `desktop.backend_${nextStatus.state}`,
          data: nextStatus,
        });
      } catch {
        // Diagnostics should never affect app startup.
      }
    });

    return () => {
      unsubscribe?.();
    };
  }, []);

  const value = useMemo<DesktopBackendContextValue>(() => {
    const isReady = !isDesktop || status?.state === "ready";
    return {
      status,
      isDesktop,
      isReady,
      disableReason: isReady ? null : "系统准备中",
    };
  }, [isDesktop, status]);

  return (
    <DesktopBackendContext.Provider value={value}>
      {children}
    </DesktopBackendContext.Provider>
  );
};

export const useDesktopBackend = (): DesktopBackendContextValue => {
  const context = useContext(DesktopBackendContext);
  if (context === null) {
    throw new Error("DesktopBackendContext 未初始化");
  }
  return context;
};
```

- [ ] **步骤 3：创建全局提示组件**

创建 `frontend/src/components/organisms/DesktopStartupStatusBanner.tsx`：

```tsx
import { AlertCircle, Database, Loader2 } from "lucide-react";
import { useDesktopBackend } from "@/context/DesktopBackendContext";

export const DesktopStartupStatusBanner = () => {
  const { isDesktop, status } = useDesktopBackend();

  if (!isDesktop || !status || status.state === "ready") {
    return null;
  }

  if (status.state === "error") {
    return (
      <div className="border-b border-red-200 bg-red-50 px-6 py-3 text-sm text-red-900">
        <div className="mx-auto flex max-w-7xl items-start gap-3">
          <AlertCircle className="mt-0.5 h-4 w-4 flex-none" />
          <div>
            <div className="font-medium">系统准备失败</div>
            <div className="mt-1 text-red-800">
              应用启动时未能完成本地数据检查。请重启应用后再试；如果问题仍然存在，请导出诊断日志反馈。
            </div>
          </div>
        </div>
      </div>
    );
  }

  const secondary = status.verySlowStartup
    ? "如果长时间停留在此状态，可以重启应用；若仍无法恢复，请导出诊断日志反馈。"
    : status.slowStartup
      ? "首次启动或版本升级时可能会稍慢，这不是配置错误。请保持应用打开，完成后会自动恢复。"
      : "新版首次启动可能需要检查或升级本地数据库，通常需要 1-3 分钟。请保持应用打开。";

  return (
    <div className="border-b border-amber-200 bg-amber-50 px-6 py-3 text-sm text-amber-950">
      <div className="mx-auto flex max-w-7xl items-start gap-3">
        {status.phase === "migrating_database" ? (
          <Database className="mt-0.5 h-4 w-4 flex-none" />
        ) : (
          <Loader2 className="mt-0.5 h-4 w-4 flex-none animate-spin" />
        )}
        <div>
          <div className="font-medium">{status.message}</div>
          <div className="mt-1 text-amber-900">{secondary}</div>
        </div>
      </div>
    </div>
  );
};
```

- [ ] **步骤 4：接入 App**

在 `frontend/src/App.tsx` 中删除本地 `useEffect` 后端状态订阅，新增导入：

```tsx
import { DesktopBackendProvider } from '@/context/DesktopBackendContext';
import { DesktopStartupStatusBanner } from '@/components/organisms/DesktopStartupStatusBanner';
```

将返回结构改为：

```tsx
return (
  <Router>
    <RouteScrollRestoration />
    <NotificationProvider>
      <DesktopBackendProvider>
        <SelectionProvider>
          <div className="flex min-h-screen flex-col bg-background">
            <DesktopStartupStatusBanner />
            <TopNavBar />
            <div className="min-h-0 flex-1">
              <Routes>
                ...
              </Routes>
            </div>
          </div>
        </SelectionProvider>
      </DesktopBackendProvider>
    </NotificationProvider>
  </Router>
);
```

- [ ] **步骤 5：运行前端类型检查**

运行：

```powershell
cd frontend; rtk npm run build
```

预期：TypeScript 编译通过。

- [ ] **步骤 6：Commit**

```powershell
rtk git add frontend/src/types/desktop.d.ts frontend/src/context/DesktopBackendContext.tsx frontend/src/components/organisms/DesktopStartupStatusBanner.tsx frontend/src/App.tsx
rtk git commit -m "feat(frontend): show desktop startup readiness state"
```

## 任务 4：前端 API 等待和错误文案

**文件：**
- 修改：`frontend/src/lib/api/client.ts`
- 修改：`frontend/src/lib/api/client.test.ts`

- [ ] **步骤 1：补充 API client 测试**

在 `frontend/src/lib/api/client.test.ts` 中新增：

```typescript
it("keeps waiting while desktop backend status is starting", async () => {
  let backendStatusCallback: ((status: Parameters<NonNullable<Window["autoEmailSender"]>["onBackendStatus"]>[0] extends (arg: infer T) => void ? T : never) => void) | undefined;
  const fetchMock = vi.fn(async () => new Response(JSON.stringify({ status: "ok" })));
  vi.stubGlobal("fetch", fetchMock);
  window.autoEmailSender = {
    getVersion: async () => "0.1.0",
    checkForUpdate: async () => ({ state: "not_available", version: "0.1.0" }),
    downloadUpdate: async () => ({ state: "not_available", version: "0.1.0" }),
    switchToFullDownload: async () => ({ state: "not_available", version: "0.1.0" }),
    quitAndInstall: async () => undefined,
    onBackendStatus: (callback) => {
      backendStatusCallback = callback;
      return () => undefined;
    },
    onUpdateStatus: () => () => undefined,
  };

  const request = apiFetch<{ status: string }>("/health");
  await Promise.resolve();

  backendStatusCallback?.({
    state: "starting",
    phase: "migrating_database",
    message: "正在检查和升级本地数据",
    elapsedSeconds: 10,
    slowStartup: false,
    verySlowStartup: false,
  });
  await Promise.resolve();

  expect(fetchMock).not.toHaveBeenCalled();

  backendStatusCallback?.({
    state: "ready",
    baseUrl: "http://127.0.0.1:48124",
    phase: "ready",
    message: "系统已准备就绪",
    elapsedSeconds: 12,
  });

  await expect(request).resolves.toEqual({ status: "ok" });
});

it("uses a user-facing message when desktop backend startup fails", async () => {
  let backendStatusCallback:
    | ((status: { state: "error"; message: string; phase: "error"; elapsedSeconds: number; detail?: string }) => void)
    | undefined;
  window.autoEmailSender = {
    getVersion: async () => "0.1.0",
    checkForUpdate: async () => ({ state: "not_available", version: "0.1.0" }),
    downloadUpdate: async () => ({ state: "not_available", version: "0.1.0" }),
    switchToFullDownload: async () => ({ state: "not_available", version: "0.1.0" }),
    quitAndInstall: async () => undefined,
    onBackendStatus: (callback) => {
      backendStatusCallback = callback as typeof backendStatusCallback;
      return () => undefined;
    },
    onUpdateStatus: () => () => undefined,
  };

  const request = apiFetch<{ status: string }>("/health");
  await Promise.resolve();

  backendStatusCallback?.({
    state: "error",
    phase: "error",
    message: "系统准备失败",
    elapsedSeconds: 10,
    detail: "database is locked",
  });

  await expect(request).rejects.toThrow("系统准备失败");
});
```

- [ ] **步骤 2：运行测试验证失败**

运行：

```powershell
cd frontend; rtk npm test -- client.test.ts
```

预期：类型或断言失败，因为当前 API client 会透传原始 `status.message`。

- [ ] **步骤 3：实现用户文案**

在 `frontend/src/lib/api/client.ts` 中新增：

```typescript
function getDesktopBackendStartupErrorMessage(statusMessage: string): string {
  if (statusMessage.includes("系统准备失败")) {
    return "系统准备失败。请重启应用后再试；如果问题仍然存在，请导出诊断日志反馈。";
  }

  return "系统正在准备本地数据。请保持应用打开，完成后再继续操作。";
}
```

在 `waitForDesktopBackendBaseUrl()` 的 error 分支改为：

```typescript
if (status.state === "error") {
  window.clearTimeout(timeout);
  unsubscribe();
  reject(new Error(getDesktopBackendStartupErrorMessage(status.message)));
}
```

将 35 秒 timeout 文案改为：

```typescript
reject(new Error("系统正在准备本地数据。请保持应用打开，完成后再继续操作。"));
```

- [ ] **步骤 4：运行 API client 测试**

运行：

```powershell
cd frontend; rtk npm test -- client.test.ts
```

预期：通过。

- [ ] **步骤 5：Commit**

```powershell
rtk git add frontend/src/lib/api/client.ts frontend/src/lib/api/client.test.ts
rtk git commit -m "fix(frontend): use user-facing startup messages"
```

## 任务 5：保存身份 ready 前禁用

**文件：**
- 修改：`frontend/src/pages/ProfilePage.tsx`
- 测试：`frontend/test/ProfilePageOnboarding.test.tsx`

- [ ] **步骤 1：编写保存身份禁用测试**

在 `frontend/test/ProfilePageOnboarding.test.tsx` 中新增 mock：

```typescript
vi.mock("@/context/DesktopBackendContext", () => ({
  useDesktopBackend: () => ({
    isDesktop: true,
    isReady: false,
    disableReason: "系统准备中",
    status: {
      state: "starting",
      phase: "migrating_database",
      message: "正在检查和升级本地数据",
      elapsedSeconds: 12,
      slowStartup: false,
      verySlowStartup: false,
    },
  }),
}));
```

新增测试：

```typescript
it("disables identity saving while desktop backend is not ready", () => {
  renderProfilePageWithBootstrap({
    identities: [selectedIdentity],
    selectedIdentityId: selectedIdentity.id,
    selectedIdentity,
  });

  const saveButton = screen.getByRole("button", { name: "系统准备中" });
  expect(saveButton).toBeDisabled();
  expect(screen.getByText("本地数据准备完成后即可继续操作，已填写内容不会丢失。")).toBeInTheDocument();
});
```

如果该测试文件已有集中 mock，不要重复 `vi.mock` 同一模块；改为使用可变 mock 函数：

```typescript
const desktopBackendState = vi.fn(() => ({
  isDesktop: false,
  isReady: true,
  disableReason: null,
  status: null,
}));

vi.mock("@/context/DesktopBackendContext", () => ({
  useDesktopBackend: () => desktopBackendState(),
}));
```

- [ ] **步骤 2：运行测试验证失败**

运行：

```powershell
cd frontend; rtk npm test -- ProfilePageOnboarding.test.tsx
```

预期：失败，按钮仍显示“保存身份”且未禁用。

- [ ] **步骤 3：实现 ProfilePage 禁用逻辑**

在 `frontend/src/pages/ProfilePage.tsx` 中新增导入：

```tsx
import { useDesktopBackend } from "@/context/DesktopBackendContext";
```

在组件内读取：

```tsx
const { isReady: desktopBackendReady, disableReason: desktopDisableReason } = useDesktopBackend();
```

在 `saveIdentity()` 开头表单校验之前新增：

```tsx
if (!desktopBackendReady) {
  notifyError(
    "系统正在准备本地数据",
    "这不是身份配置错误。请等待系统准备完成后再保存，已填写内容不会丢失。",
  );
  return null;
}
```

将保存身份按钮从：

```tsx
<button
  type="button"
  disabled={submittingIdentity}
>
  {submittingIdentity && <Loader2 className="h-4 w-4 animate-spin" />}
  保存身份
</button>
```

改为保留原有 className 和其他属性，只调整 disabled 与内容：

```tsx
<button
  type="button"
  disabled={submittingIdentity || !desktopBackendReady}
>
  {submittingIdentity && <Loader2 className="h-4 w-4 animate-spin" />}
  {!desktopBackendReady ? (desktopDisableReason ?? "系统准备中") : "保存身份"}
</button>
```

在按钮附近增加提示：

```tsx
{!desktopBackendReady && (
  <p className="text-xs text-amber-700">
    本地数据准备完成后即可继续操作，已填写内容不会丢失。
  </p>
)}
```

- [ ] **步骤 4：运行保存身份相关测试**

运行：

```powershell
cd frontend; rtk npm test -- ProfilePageOnboarding.test.tsx
```

预期：通过。

- [ ] **步骤 5：Commit**

```powershell
rtk git add frontend/src/pages/ProfilePage.tsx frontend/test/ProfilePageOnboarding.test.tsx
rtk git commit -m "fix(frontend): disable identity save until desktop backend is ready"
```

## 任务 6：端到端验证与回归检查

**文件：**
- 验证：后端、桌面端、前端测试命令

- [ ] **步骤 1：运行后端运行时测试**

运行：

```powershell
rtk uv run python -m unittest backend.test.test_desktop_runtime
```

预期：全部通过。

- [ ] **步骤 2：运行桌面端测试和类型检查**

运行：

```powershell
cd desktop; rtk npm run typecheck
cd desktop; rtk npm test -- backend.test.ts
```

预期：全部通过。

- [ ] **步骤 3：运行前端目标测试**

运行：

```powershell
cd frontend; rtk npm test -- client.test.ts ProfilePageOnboarding.test.tsx
```

预期：全部通过。

- [ ] **步骤 4：运行前端构建**

运行：

```powershell
cd frontend; rtk npm run build
```

预期：构建通过。

- [ ] **步骤 5：手动桌面启动验证**

开发环境运行：

```powershell
cd frontend; rtk npm run build
cd desktop; rtk npm run build
cd desktop; rtk npm run dev
```

验证：

- 桌面窗口能打开。
- 后端未 ready 时显示“正在准备本地数据”。
- 保存身份按钮显示“系统准备中”。
- 后端 ready 后提示消失，保存按钮恢复“保存身份”。

- [ ] **步骤 6：最终 diff 检查**

运行：

```powershell
rtk git diff --stat
rtk git status --short
```

预期：只包含本计划相关文件。若存在用户已有改动，不要回滚；在交付说明中单独说明。

- [ ] **步骤 7：最终 Commit**

如果前面任务已经逐步 commit，确认工作区干净即可。若还有未提交的修正：

```powershell
rtk git add <remaining-files>
rtk git commit -m "test: cover desktop startup readiness flow"
```

## 自检清单

- 规格中的 `/health`、`/startup-status`、`/ready` 三种职责均有任务覆盖。
- 后端状态阶段 `migrating_database`、`cleaning_logs`、`starting_workers`、`ready`、`error` 均有设计和测试入口。
- 桌面端 30 秒不再作为失败阈值，硬超时设为 10 分钟。
- 前端在非 ready 状态下禁用保存身份，并保留用户输入。
- 用户不会再看到 `Backend readiness check timed out` 作为主错误文案。
- 计划没有引入自动重试保存，符合规格中第一版范围。
