# 诊断日志系统实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 建立一套可导出的诊断日志系统，让用户遇到问题时可以导出前端操作、失败请求、后端请求 ID 和关键业务操作记录，交给开发者定位问题。

**架构：** 前端维护最近操作和 API 结果的本地环形日志，并提供导出诊断包入口；后端为每个请求生成 `request_id` 并返回 `X-Request-ID`；关键业务操作写入数据库审计表。第一版不记录每一次键盘输入和鼠标移动，只记录有排查价值的操作事件。

**技术栈：** React/Vite、FastAPI、SQLAlchemy、Alembic、localStorage、unittest、Vitest。

---

## 文件结构

- 创建：`frontend/src/lib/diagnostics.ts`
  - 前端诊断日志的核心模块：环形存储、脱敏、导出 JSON、记录 API 请求结果。
- 创建：`frontend/test/diagnostics.test.ts`
  - 测试前端诊断日志写入、容量裁剪、敏感字段脱敏和导出结构。
- 修改：`frontend/src/lib/api/client.ts`
  - 为每个 API 请求生成前端请求 ID，记录请求开始/成功/失败，捕获后端 `X-Request-ID`。
- 修改：`frontend/test/apiClient.test.ts`
  - 覆盖 API 诊断日志记录和后端 request id 提取。
- 创建：`frontend/src/components/organisms/DiagnosticLogPanel.tsx`
  - 一个小型诊断面板，用于复制/下载诊断包和清空本地诊断日志。
- 修改：`frontend/src/pages/ProfilePage.tsx`
  - 在个人中心底部增加“诊断日志”区域，承载 `DiagnosticLogPanel`。
- 创建：`backend/app/core/request_context.py`
  - 使用 `contextvars` 保存当前请求 ID，供业务审计写入时读取。
- 创建：`backend/app/core/request_logging.py`
  - FastAPI middleware：生成 request_id、记录请求状态、响应头写入 `X-Request-ID`。
- 修改：`backend/main.py`
  - 注册请求日志 middleware 和 diagnostics router。
- 创建：`backend/app/models/operation_audit_log.py`
  - 数据库审计表模型，记录关键用户操作。
- 修改：`backend/app/models/__init__.py`
  - 导出 `OperationAuditLog`。
- 创建：`backend/alembic/versions/a4f7e8d9c012_add_operation_audit_logs.py`
  - 新增 `operation_audit_logs` 表和索引。
- 创建：`backend/app/schemas/diagnostics.py`
  - 后端诊断接口返回模型。
- 创建：`backend/app/services/audit_log.py`
  - 统一写审计事件，默认做敏感字段脱敏。
- 创建：`backend/app/api/diagnostics.py`
  - 提供最近审计日志查询接口：`GET /api/diagnostics/audit-logs`。
- 修改：`backend/app/api/__init__.py`
  - 导出 diagnostics router。
- 修改：`backend/app/api/crawl_jobs.py`
  - 为创建/取消/审核抓取任务写审计日志。
- 修改：`backend/app/api/batch_tasks.py`
  - 为创建/暂停/继续/中止批量任务写审计日志。
- 修改：`backend/app/api/professors.py`
  - 为导入、归档、恢复、保存导师写审计日志。
- 创建：`backend/test/test_request_logging.py`
  - 测试 request_id 响应头、上下文设置和异常请求仍返回 request id。
- 创建：`backend/test/test_operation_audit_logs.py`
  - 测试审计日志写入、脱敏、查询和关键接口落库。

---

### 任务 1：前端本地诊断日志核心

**文件：**
- 创建：`frontend/src/lib/diagnostics.ts`
- 创建：`frontend/test/diagnostics.test.ts`

- [ ] **步骤 1：编写失败的测试**

创建 `frontend/test/diagnostics.test.ts`：

```ts
import { beforeEach, describe, expect, it, vi } from "vitest";
import {
  clearDiagnosticEvents,
  exportDiagnosticSnapshot,
  recordDiagnosticEvent,
} from "@/lib/diagnostics";

describe("diagnostics", () => {
  beforeEach(() => {
    window.localStorage.clear();
    vi.setSystemTime(new Date("2026-04-26T10:00:00.000Z"));
  });

  it("records recent diagnostic events in localStorage", () => {
    recordDiagnosticEvent({
      type: "user_action",
      message: "创建抓取任务",
      metadata: { page: "/professors" },
    });

    const snapshot = exportDiagnosticSnapshot();

    expect(snapshot.events).toHaveLength(1);
    expect(snapshot.events[0]).toMatchObject({
      type: "user_action",
      message: "创建抓取任务",
      metadata: { page: "/professors" },
      created_at: "2026-04-26T10:00:00.000Z",
    });
    expect(snapshot.client.user_agent).toBeTruthy();
  });

  it("redacts sensitive metadata before storing events", () => {
    recordDiagnosticEvent({
      type: "api_error",
      message: "保存身份失败",
      metadata: {
        smtp_password: "secret",
        apiKey: "sk-test",
        nested: { token: "abc", safe: "ok" },
      },
    });

    const snapshot = exportDiagnosticSnapshot();

    expect(snapshot.events[0].metadata).toEqual({
      smtp_password: "[REDACTED]",
      apiKey: "[REDACTED]",
      nested: { token: "[REDACTED]", safe: "ok" },
    });
  });

  it("keeps only the latest 200 events", () => {
    for (let index = 0; index < 205; index += 1) {
      recordDiagnosticEvent({
        type: "user_action",
        message: `event-${index}`,
      });
    }

    const snapshot = exportDiagnosticSnapshot();

    expect(snapshot.events).toHaveLength(200);
    expect(snapshot.events[0].message).toBe("event-5");
    expect(snapshot.events[199].message).toBe("event-204");
  });

  it("clears diagnostic events", () => {
    recordDiagnosticEvent({ type: "user_action", message: "clicked" });
    clearDiagnosticEvents();

    expect(exportDiagnosticSnapshot().events).toEqual([]);
  });
});
```

- [ ] **步骤 2：运行测试验证失败**

运行：

```bash
cd frontend
npm test -- --run test/diagnostics.test.ts
```

预期：FAIL，报错包含 `Cannot find module '@/lib/diagnostics'`。

- [ ] **步骤 3：实现诊断日志核心**

创建 `frontend/src/lib/diagnostics.ts`：

```ts
export type DiagnosticEventType =
  | "user_action"
  | "api_request"
  | "api_success"
  | "api_error"
  | "system";

export type DiagnosticEvent = {
  id: string;
  type: DiagnosticEventType;
  message: string;
  created_at: string;
  request_id?: string | null;
  backend_request_id?: string | null;
  metadata?: unknown;
};

export type DiagnosticSnapshot = {
  exported_at: string;
  client: {
    user_agent: string;
    location: string;
    language: string;
  };
  events: DiagnosticEvent[];
};

const DIAGNOSTIC_STORAGE_KEY = "auto-email-sender:diagnostics:v1";
const MAX_DIAGNOSTIC_EVENTS = 200;
const SENSITIVE_KEY_PATTERN = /(password|secret|token|api[_-]?key|authorization|cookie|smtp_password|imap_password)/i;

const createEventId = () =>
  `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;

const safeParseEvents = (raw: string | null): DiagnosticEvent[] => {
  if (!raw) {
    return [];
  }
  try {
    const parsed = JSON.parse(raw);
    return Array.isArray(parsed) ? parsed.filter((item) => item && typeof item === "object") : [];
  } catch {
    return [];
  }
};

export const redactDiagnosticValue = (value: unknown): unknown => {
  if (Array.isArray(value)) {
    return value.map((item) => redactDiagnosticValue(item));
  }
  if (value && typeof value === "object") {
    return Object.fromEntries(
      Object.entries(value).map(([key, item]) => [
        key,
        SENSITIVE_KEY_PATTERN.test(key) ? "[REDACTED]" : redactDiagnosticValue(item),
      ]),
    );
  }
  return value;
};

export const readDiagnosticEvents = (): DiagnosticEvent[] =>
  safeParseEvents(window.localStorage.getItem(DIAGNOSTIC_STORAGE_KEY));

export const recordDiagnosticEvent = (
  event: Omit<DiagnosticEvent, "id" | "created_at"> & { created_at?: string },
) => {
  const nextEvent: DiagnosticEvent = {
    id: createEventId(),
    created_at: event.created_at ?? new Date().toISOString(),
    type: event.type,
    message: event.message,
    request_id: event.request_id,
    backend_request_id: event.backend_request_id,
    metadata: redactDiagnosticValue(event.metadata),
  };
  const nextEvents = [...readDiagnosticEvents(), nextEvent].slice(-MAX_DIAGNOSTIC_EVENTS);
  window.localStorage.setItem(DIAGNOSTIC_STORAGE_KEY, JSON.stringify(nextEvents));
  return nextEvent;
};

export const clearDiagnosticEvents = () => {
  window.localStorage.removeItem(DIAGNOSTIC_STORAGE_KEY);
};

export const exportDiagnosticSnapshot = (): DiagnosticSnapshot => ({
  exported_at: new Date().toISOString(),
  client: {
    user_agent: window.navigator.userAgent,
    location: window.location.href,
    language: window.navigator.language,
  },
  events: readDiagnosticEvents(),
});

export const downloadDiagnosticSnapshot = () => {
  const snapshot = exportDiagnosticSnapshot();
  const blob = new Blob([JSON.stringify(snapshot, null, 2)], {
    type: "application/json;charset=utf-8",
  });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = `diagnostic-log-${new Date().toISOString().replace(/[:.]/g, "-")}.json`;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
};
```

- [ ] **步骤 4：运行测试验证通过**

运行：

```bash
cd frontend
npm test -- --run test/diagnostics.test.ts
```

预期：PASS，4 个测试通过。

- [ ] **步骤 5：Commit**

```bash
git add frontend/src/lib/diagnostics.ts frontend/test/diagnostics.test.ts
git commit -m "feat(frontend): add local diagnostic log store"
```

---

### 任务 2：前端 API 请求自动写入诊断日志

**文件：**
- 修改：`frontend/src/lib/api/client.ts`
- 修改：`frontend/test/apiClient.test.ts`

- [ ] **步骤 1：编写失败的测试**

在 `frontend/test/apiClient.test.ts` 中追加：

```ts
import {
  clearDiagnosticEvents,
  exportDiagnosticSnapshot,
} from "@/lib/diagnostics";
```

在 `afterEach` 中增加：

```ts
clearDiagnosticEvents();
```

追加两个测试：

```ts
it("records successful API calls with frontend and backend request ids", async () => {
  vi.spyOn(globalThis, "fetch").mockResolvedValue(
    new Response(JSON.stringify({ ok: true }), {
      status: 200,
      headers: { "X-Request-ID": "backend-123" },
    }),
  );

  await apiFetch("/api/ping");

  const events = exportDiagnosticSnapshot().events;
  expect(events.map((event) => event.type)).toEqual(["api_request", "api_success"]);
  expect(events[1]).toMatchObject({
    backend_request_id: "backend-123",
    metadata: {
      method: "GET",
      path: "/api/ping",
      status: 200,
    },
  });
});

it("records failed API calls with readable error message", async () => {
  vi.spyOn(globalThis, "fetch").mockResolvedValue(
    new Response(JSON.stringify({ detail: "未找到抓取任务" }), {
      status: 404,
      headers: { "X-Request-ID": "backend-404" },
    }),
  );

  await expect(apiFetch("/api/crawl-jobs/404")).rejects.toThrow("未找到抓取任务");

  const events = exportDiagnosticSnapshot().events;
  expect(events.at(-1)).toMatchObject({
    type: "api_error",
    message: "未找到抓取任务",
    backend_request_id: "backend-404",
    metadata: {
      method: "GET",
      path: "/api/crawl-jobs/404",
      status: 404,
    },
  });
});
```

- [ ] **步骤 2：运行测试验证失败**

运行：

```bash
cd frontend
npm test -- --run test/apiClient.test.ts
```

预期：FAIL，新增测试中 `events` 为空。

- [ ] **步骤 3：实现 API 诊断记录**

在 `frontend/src/lib/api/client.ts` 顶部增加：

```ts
import { recordDiagnosticEvent } from "@/lib/diagnostics";
```

在 `apiFetch` 内，计算 `apiPath` 并记录事件。用下面结构替换当前 `fetch(buildApiPath(...))` 到错误处理之间的代码：

```ts
  const apiPath = buildApiPath(path, params);
  const method = options?.method ?? "GET";
  const requestId = `client-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`;
  const startedAt = performance.now();

  recordDiagnosticEvent({
    type: "api_request",
    message: `${method} ${apiPath}`,
    request_id: requestId,
    metadata: { method, path: apiPath },
  });

  const response = await fetch(apiPath, {
    ...options,
    headers: {
      ...(options?.body instanceof FormData ? {} : { "Content-Type": "application/json" }),
      "X-Client-Request-ID": requestId,
      ...(options?.headers ?? {}),
    },
  });
```

在解析 `data` 之后、`if (!response.ok)` 之前增加：

```ts
  const backendRequestId = response.headers.get("X-Request-ID");
  const durationMs = Math.round(performance.now() - startedAt);
```

将 `if (!response.ok)` 替换为：

```ts
  if (!response.ok) {
    const message = extractApiErrorMessage(data);
    recordDiagnosticEvent({
      type: "api_error",
      message,
      request_id: requestId,
      backend_request_id: backendRequestId,
      metadata: { method, path: apiPath, status: response.status, duration_ms: durationMs },
    });
    throw new ApiError(response.status, message);
  }

  recordDiagnosticEvent({
    type: "api_success",
    message: `${method} ${apiPath} ${response.status}`,
    request_id: requestId,
    backend_request_id: backendRequestId,
    metadata: { method, path: apiPath, status: response.status, duration_ms: durationMs },
  });
```

- [ ] **步骤 4：运行测试验证通过**

运行：

```bash
cd frontend
npm test -- --run test/apiClient.test.ts test/diagnostics.test.ts
```

预期：PASS。

- [ ] **步骤 5：Commit**

```bash
git add frontend/src/lib/api/client.ts frontend/test/apiClient.test.ts
git commit -m "feat(frontend): record API diagnostics"
```

---

### 任务 3：后端请求 ID 和请求日志中间件

**文件：**
- 创建：`backend/app/core/request_context.py`
- 创建：`backend/app/core/request_logging.py`
- 修改：`backend/main.py`
- 创建：`backend/test/test_request_logging.py`

- [ ] **步骤 1：编写失败的测试**

创建 `backend/test/test_request_logging.py`：

```python
from __future__ import annotations

import unittest

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.request_context import get_request_id
from app.core.request_logging import RequestLoggingMiddleware


class RequestLoggingTests(unittest.TestCase):
    def test_adds_request_id_to_response_and_context(self) -> None:
        app = FastAPI()
        app.add_middleware(RequestLoggingMiddleware)

        @app.get("/context")
        async def read_context() -> dict[str, str | None]:
            return {"request_id": get_request_id()}

        response = TestClient(app).get("/context")

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.headers["X-Request-ID"])
        self.assertEqual(response.json()["request_id"], response.headers["X-Request-ID"])

    def test_preserves_client_request_id_when_present(self) -> None:
        app = FastAPI()
        app.add_middleware(RequestLoggingMiddleware)

        @app.get("/ping")
        async def ping() -> dict[str, str]:
            return {"ok": "true"}

        response = TestClient(app).get("/ping", headers={"X-Client-Request-ID": "client-abc"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["X-Request-ID"], "client-abc")
```

- [ ] **步骤 2：运行测试验证失败**

运行：

```bash
cd backend
uv run python -m unittest test.test_request_logging
```

预期：FAIL，报错包含 `No module named 'app.core.request_context'`。

- [ ] **步骤 3：实现请求上下文**

创建 `backend/app/core/request_context.py`：

```python
from __future__ import annotations

from contextvars import ContextVar


_request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)


def get_request_id() -> str | None:
    return _request_id_var.get()


def set_request_id(request_id: str):
    return _request_id_var.set(request_id)


def reset_request_id(token: object) -> None:
    _request_id_var.reset(token)
```

- [ ] **步骤 4：实现请求日志 middleware**

创建 `backend/app/core/request_logging.py`：

```python
from __future__ import annotations

import logging
import time
import uuid

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.request_context import reset_request_id, set_request_id


logger = logging.getLogger("app.request")


class RequestLoggingMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = {
            key.decode("latin-1").lower(): value.decode("latin-1")
            for key, value in scope.get("headers", [])
        }
        request_id = headers.get("x-client-request-id") or uuid.uuid4().hex
        token = set_request_id(request_id)
        started_at = time.perf_counter()
        status_code = 500

        async def send_with_request_id(message):
            if message["type"] == "http.response.start":
                nonlocal status_code
                status_code = int(message.get("status", 500))
                headers = list(message.get("headers", []))
                headers.append((b"x-request-id", request_id.encode("latin-1")))
                message["headers"] = headers
            await send(message)

        try:
            await self.app(scope, receive, send_with_request_id)
        except Exception:
            logger.exception(
                "request failed",
                extra={
                    "request_id": request_id,
                    "method": scope.get("method"),
                    "path": scope.get("path"),
                },
            )
            raise
        finally:
            duration_ms = round((time.perf_counter() - started_at) * 1000, 2)
            logger.info(
                "request completed",
                extra={
                    "request_id": request_id,
                    "method": scope.get("method"),
                    "path": scope.get("path"),
                    "status_code": status_code,
                    "duration_ms": duration_ms,
                },
            )
            reset_request_id(token)
```

- [ ] **步骤 5：注册 middleware**

在 `backend/main.py` imports 中增加：

```python
from app.core.request_logging import RequestLoggingMiddleware
```

在 `create_app()` 中，创建 app 后、CORS 前增加：

```python
    app.add_middleware(RequestLoggingMiddleware)
```

- [ ] **步骤 6：运行测试验证通过**

运行：

```bash
cd backend
uv run python -m unittest test.test_request_logging
```

预期：PASS，2 个测试通过。

- [ ] **步骤 7：Commit**

```bash
git add backend/app/core/request_context.py backend/app/core/request_logging.py backend/main.py backend/test/test_request_logging.py
git commit -m "feat(backend): add request id logging middleware"
```

---

### 任务 4：后端操作审计日志表和服务

**文件：**
- 创建：`backend/app/models/operation_audit_log.py`
- 修改：`backend/app/models/__init__.py`
- 创建：`backend/alembic/versions/a4f7e8d9c012_add_operation_audit_logs.py`
- 创建：`backend/app/services/audit_log.py`
- 创建：`backend/test/test_operation_audit_logs.py`

- [ ] **步骤 1：编写失败的服务测试**

创建 `backend/test/test_operation_audit_logs.py`：

```python
from __future__ import annotations

import os
import tempfile
import unittest

from sqlalchemy import select

from app.core.database import dispose_engine, get_session_factory
from app.core.migrations import ensure_database_schema
from app.core.request_context import set_request_id, reset_request_id
from app.models.operation_audit_log import OperationAuditLog
from app.services.audit_log import create_audit_log


class OperationAuditLogTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.temp_dir.name, "test.db")
        os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{self.db_path}"

    def tearDown(self) -> None:
        async def cleanup() -> None:
            await dispose_engine()

        import asyncio

        asyncio.run(cleanup())
        os.environ.pop("DATABASE_URL", None)
        get_session_factory.cache_clear()
        self.temp_dir.cleanup()

    def _run_async(self, coro):
        import asyncio

        return asyncio.run(coro)

    def test_create_audit_log_redacts_sensitive_metadata_and_uses_request_id(self) -> None:
        async def scenario() -> OperationAuditLog:
            await ensure_database_schema()
            token = set_request_id("req-123")
            try:
                session_factory = get_session_factory()
                async with session_factory() as session:
                    await create_audit_log(
                        session,
                        action="crawl_job.create",
                        entity_type="crawl_job",
                        entity_id=7,
                        status="success",
                        metadata={"start_url": "https://example.edu", "api_key": "secret"},
                    )
                    await session.commit()

                async with session_factory() as session:
                    return (await session.execute(select(OperationAuditLog))).scalar_one()
            finally:
                reset_request_id(token)

        log = self._run_async(scenario())

        self.assertEqual(log.request_id, "req-123")
        self.assertEqual(log.action, "crawl_job.create")
        self.assertEqual(log.metadata_json["api_key"], "[REDACTED]")
        self.assertEqual(log.metadata_json["start_url"], "https://example.edu")
```

- [ ] **步骤 2：运行测试验证失败**

运行：

```bash
cd backend
uv run python -m unittest test.test_operation_audit_logs.OperationAuditLogTests.test_create_audit_log_redacts_sensitive_metadata_and_uses_request_id
```

预期：FAIL，报错包含 `No module named 'app.models.operation_audit_log'`。

- [ ] **步骤 3：创建模型**

创建 `backend/app/models/operation_audit_log.py`：

```python
from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Integer, JSON, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class OperationAuditLog(Base):
    __tablename__ = "operation_audit_logs"

    id: Mapped[int] = mapped_column(primary_key=True)
    request_id: Mapped[str | None] = mapped_column(String(64), index=True, nullable=True)
    actor_type: Mapped[str] = mapped_column(String(32), nullable=False, server_default=text("'local_user'"))
    action: Mapped[str] = mapped_column(String(100), index=True, nullable=False)
    entity_type: Mapped[str | None] = mapped_column(String(100), index=True, nullable=True)
    entity_id: Mapped[int | None] = mapped_column(Integer, index=True, nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, server_default=text("'success'"))
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    metadata_json: Mapped[dict[str, object] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    )
```

修改 `backend/app/models/__init__.py`：

```python
from app.models.operation_audit_log import OperationAuditLog
```

并在 `__all__` 中加入：

```python
"OperationAuditLog",
```

- [ ] **步骤 4：创建 Alembic 迁移**

创建 `backend/alembic/versions/a4f7e8d9c012_add_operation_audit_logs.py`：

```python
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "a4f7e8d9c012"
down_revision = "7b9c2d4e6f10"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "operation_audit_logs",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column("request_id", sa.String(length=64), nullable=True),
        sa.Column("actor_type", sa.String(length=32), server_default="local_user", nullable=False),
        sa.Column("action", sa.String(length=100), nullable=False),
        sa.Column("entity_type", sa.String(length=100), nullable=True),
        sa.Column("entity_id", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(length=32), server_default="success", nullable=False),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("metadata_json", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
    )
    op.create_index(op.f("ix_operation_audit_logs_request_id"), "operation_audit_logs", ["request_id"], unique=False)
    op.create_index(op.f("ix_operation_audit_logs_action"), "operation_audit_logs", ["action"], unique=False)
    op.create_index(op.f("ix_operation_audit_logs_entity_type"), "operation_audit_logs", ["entity_type"], unique=False)
    op.create_index(op.f("ix_operation_audit_logs_entity_id"), "operation_audit_logs", ["entity_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_operation_audit_logs_entity_id"), table_name="operation_audit_logs")
    op.drop_index(op.f("ix_operation_audit_logs_entity_type"), table_name="operation_audit_logs")
    op.drop_index(op.f("ix_operation_audit_logs_action"), table_name="operation_audit_logs")
    op.drop_index(op.f("ix_operation_audit_logs_request_id"), table_name="operation_audit_logs")
    op.drop_table("operation_audit_logs")
```

如果当前最新迁移不是 `7b9c2d4e6f10`，先运行 `cd backend && uv run alembic heads`，把 `down_revision` 改成实际唯一 head。

- [ ] **步骤 5：实现审计服务**

创建 `backend/app/services/audit_log.py`：

```python
from __future__ import annotations

import re
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.request_context import get_request_id
from app.models.operation_audit_log import OperationAuditLog


SENSITIVE_KEY_PATTERN = re.compile(
    r"(password|secret|token|api[_-]?key|authorization|cookie|smtp_password|imap_password)",
    re.IGNORECASE,
)


def redact_metadata(value: Any) -> Any:
    if isinstance(value, list):
        return [redact_metadata(item) for item in value]
    if isinstance(value, dict):
        return {
            str(key): "[REDACTED]" if SENSITIVE_KEY_PATTERN.search(str(key)) else redact_metadata(item)
            for key, item in value.items()
        }
    return value


async def create_audit_log(
    session: AsyncSession,
    *,
    action: str,
    entity_type: str | None = None,
    entity_id: int | None = None,
    status: str = "success",
    summary: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> OperationAuditLog:
    log = OperationAuditLog(
        request_id=get_request_id(),
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        status=status,
        summary=summary,
        metadata_json=redact_metadata(metadata or {}),
    )
    session.add(log)
    await session.flush()
    return log
```

- [ ] **步骤 6：运行服务测试验证通过**

运行：

```bash
cd backend
uv run python -m unittest test.test_operation_audit_logs.OperationAuditLogTests.test_create_audit_log_redacts_sensitive_metadata_and_uses_request_id
```

预期：PASS。

- [ ] **步骤 7：Commit**

```bash
git add backend/app/models/operation_audit_log.py backend/app/models/__init__.py backend/alembic/versions/a4f7e8d9c012_add_operation_audit_logs.py backend/app/services/audit_log.py backend/test/test_operation_audit_logs.py
git commit -m "feat(backend): add operation audit log model"
```

---

### 任务 5：后端诊断日志查询接口

**文件：**
- 创建：`backend/app/schemas/diagnostics.py`
- 创建：`backend/app/api/diagnostics.py`
- 修改：`backend/app/api/__init__.py`
- 修改：`backend/main.py`
- 修改：`backend/test/test_operation_audit_logs.py`

- [ ] **步骤 1：编写失败的 API 测试**

在 `backend/test/test_operation_audit_logs.py` 追加：

```python
from fastapi.testclient import TestClient

from main import create_app
```

追加测试：

```python
    def test_diagnostics_api_lists_recent_audit_logs(self) -> None:
        async def seed() -> None:
            await ensure_database_schema()
            session_factory = get_session_factory()
            async with session_factory() as session:
                await create_audit_log(
                    session,
                    action="batch_task.create",
                    entity_type="batch_task",
                    entity_id=10,
                    summary="创建批量任务",
                    metadata={"target_count": 3},
                )
                await session.commit()

        self._run_async(seed())

        response = TestClient(create_app()).get("/api/diagnostics/audit-logs")

        self.assertEqual(response.status_code, 200, msg=response.text)
        payload = response.json()
        self.assertEqual(payload[0]["action"], "batch_task.create")
        self.assertEqual(payload[0]["entity_type"], "batch_task")
        self.assertEqual(payload[0]["entity_id"], 10)
```

- [ ] **步骤 2：运行测试验证失败**

运行：

```bash
cd backend
uv run python -m unittest test.test_operation_audit_logs.OperationAuditLogTests.test_diagnostics_api_lists_recent_audit_logs
```

预期：FAIL，状态码 404。

- [ ] **步骤 3：创建 schema**

创建 `backend/app/schemas/diagnostics.py`：

```python
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class OperationAuditLogRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    request_id: str | None
    actor_type: str
    action: str
    entity_type: str | None
    entity_id: int | None
    status: str
    summary: str | None
    metadata_json: dict[str, object] | None
    created_at: datetime
```

- [ ] **步骤 4：创建 diagnostics API**

创建 `backend/app/api/diagnostics.py`：

```python
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_async_session
from app.models.operation_audit_log import OperationAuditLog
from app.schemas.diagnostics import OperationAuditLogRead


router = APIRouter(prefix="/api/diagnostics", tags=["diagnostics"])


@router.get("/audit-logs", response_model=list[OperationAuditLogRead])
async def list_audit_logs(
    limit: int = 100,
    session: AsyncSession = Depends(get_async_session),
) -> list[OperationAuditLog]:
    bounded_limit = min(max(limit, 1), 500)
    return list(
        (
            await session.execute(
                select(OperationAuditLog)
                .order_by(OperationAuditLog.created_at.desc(), OperationAuditLog.id.desc())
                .limit(bounded_limit),
            )
        ).scalars(),
    )
```

修改 `backend/app/api/__init__.py`：

```python
from app.api.diagnostics import router as diagnostics_router
```

在 `__all__` 中加入：

```python
"diagnostics_router",
```

修改 `backend/main.py` 的 API import：

```python
    diagnostics_router,
```

在 `create_app()` 中注册 router：

```python
    app.include_router(diagnostics_router)
```

- [ ] **步骤 5：运行测试验证通过**

运行：

```bash
cd backend
uv run python -m unittest test.test_operation_audit_logs.OperationAuditLogTests.test_diagnostics_api_lists_recent_audit_logs
```

预期：PASS。

- [ ] **步骤 6：Commit**

```bash
git add backend/app/schemas/diagnostics.py backend/app/api/diagnostics.py backend/app/api/__init__.py backend/main.py backend/test/test_operation_audit_logs.py
git commit -m "feat(backend): expose diagnostic audit logs"
```

---

### 任务 6：关键后端业务操作写审计日志

**文件：**
- 修改：`backend/app/api/crawl_jobs.py`
- 修改：`backend/app/api/batch_tasks.py`
- 修改：`backend/app/api/professors.py`
- 修改：`backend/test/test_operation_audit_logs.py`

- [ ] **步骤 1：编写失败的关键操作测试**

在 `backend/test/test_operation_audit_logs.py` 追加：

```python
    def test_create_crawl_job_writes_audit_log(self) -> None:
        response = TestClient(create_app()).post(
            "/api/crawl-jobs",
            json={
                "university": "示例大学",
                "school": "计算机学院",
                "start_url": "https://example.edu/faculty",
                "llm_profile_id": None,
            },
        )
        self.assertEqual(response.status_code, 201, msg=response.text)

        async def fetch_log() -> OperationAuditLog:
            session_factory = get_session_factory()
            async with session_factory() as session:
                return (
                    await session.execute(
                        select(OperationAuditLog).where(OperationAuditLog.action == "crawl_job.create")
                    )
                ).scalar_one()

        log = self._run_async(fetch_log())
        self.assertEqual(log.entity_type, "crawl_job")
        self.assertEqual(log.entity_id, response.json()["id"])
        self.assertEqual(log.metadata_json["start_url"], "https://example.edu/faculty")
```

- [ ] **步骤 2：运行测试验证失败**

运行：

```bash
cd backend
uv run python -m unittest test.test_operation_audit_logs.OperationAuditLogTests.test_create_crawl_job_writes_audit_log
```

预期：FAIL，报错 `No row was found when one was required`。

- [ ] **步骤 3：为抓取任务接口写审计**

在 `backend/app/api/crawl_jobs.py` imports 增加：

```python
from app.services.audit_log import create_audit_log
```

在 `create_crawl_job()` 中 `await session.refresh(job)` 前增加：

```python
    await create_audit_log(
        session,
        action="crawl_job.create",
        entity_type="crawl_job",
        entity_id=job.id,
        summary=f"创建抓取任务：{payload.university} / {payload.school}",
        metadata={
            "university": payload.university,
            "school": payload.school,
            "start_url": payload.start_url,
            "llm_profile_id": payload.llm_profile_id,
        },
    )
```

在 `cancel_crawl_job()` 中 `await session.commit()` 前增加：

```python
    await create_audit_log(
        session,
        action="crawl_job.cancel",
        entity_type="crawl_job",
        entity_id=job.id,
        summary="取消抓取任务",
        metadata={"status": job.status},
    )
```

在 `approve_crawl_candidates()` 中 `await session.commit()` 前增加：

```python
    await create_audit_log(
        session,
        action="crawl_job.approve",
        entity_type="crawl_job",
        entity_id=job.id,
        summary="审核抓取候选导师",
        metadata={
            "candidate_ids": payload.candidate_ids,
            "inserted_count": inserted_count,
            "updated_count": updated_count,
            "skipped_count": skipped_count,
        },
    )
```

- [ ] **步骤 4：为批量任务接口写审计**

在 `backend/app/api/batch_tasks.py` imports 增加：

```python
from app.services.audit_log import create_audit_log
```

在 `create_batch_task()` 中 `await session.commit()` 前增加：

```python
    await create_audit_log(
        session,
        action="batch_task.create",
        entity_type="batch_task",
        entity_id=batch_task.id,
        summary=f"创建批量任务：{batch_task.name}",
        metadata={
            "name": batch_task.name,
            "target_count": batch_task.target_count,
            "schedule_type": batch_task.schedule_type,
            "identity_id": batch_task.identity_id,
            "llm_profile_id": batch_task.llm_profile_id,
        },
    )
```

在 `pause_batch_task()`、`resume_batch_task()`、`stop_batch_task()` 中提交前分别加入：

```python
    await create_audit_log(
        session,
        action="batch_task.pause",
        entity_type="batch_task",
        entity_id=task.id,
        summary=f"暂停批量任务：{task.name}",
        metadata={"status": task.status},
    )
```

```python
    await create_audit_log(
        session,
        action="batch_task.resume",
        entity_type="batch_task",
        entity_id=task.id,
        summary=f"继续批量任务：{task.name}",
        metadata={"status": task.status},
    )
```

```python
    await create_audit_log(
        session,
        action="batch_task.stop",
        entity_type="batch_task",
        entity_id=task.id,
        summary=f"中止批量任务：{task.name}",
        metadata={"status": task.status},
    )
```

- [ ] **步骤 5：为导师管理接口写审计**

在 `backend/app/api/professors.py` imports 增加：

```python
from app.services.audit_log import create_audit_log
```

在新增/更新/归档/恢复/批量归档/文件导入成功提交前写对应 action：

```python
await create_audit_log(
    session,
    action="professor.upsert",
    entity_type="professor",
    entity_id=professor.id,
    summary=f"保存导师：{professor.name}",
    metadata={"name": professor.name, "email": professor.email},
)
```

```python
await create_audit_log(
    session,
    action="professor.archive",
    entity_type="professor",
    entity_id=professor.id,
    summary=f"归档导师：{professor.name}",
    metadata={"name": professor.name, "email": professor.email},
)
```

```python
await create_audit_log(
    session,
    action="professor.restore",
    entity_type="professor",
    entity_id=professor.id,
    summary=f"恢复导师：{professor.name}",
    metadata={"name": professor.name, "email": professor.email},
)
```

```python
await create_audit_log(
    session,
    action="professor.bulk_archive",
    entity_type="professor",
    summary="批量归档导师",
    metadata={"ids": payload.ids, "affected_count": affected_count},
)
```

```python
await create_audit_log(
    session,
    action="professor.import_file",
    entity_type="professor",
    summary="导入导师文件",
    metadata={
        "inserted_count": inserted_count,
        "updated_count": updated_count,
        "failed_count": failed_count,
    },
)
```

使用当前文件里已有变量名；如果变量名不同，只替换变量名，不改变 action 字符串。

- [ ] **步骤 6：运行审计测试**

运行：

```bash
cd backend
uv run python -m unittest test.test_operation_audit_logs
```

预期：PASS。

- [ ] **步骤 7：Commit**

```bash
git add backend/app/api/crawl_jobs.py backend/app/api/batch_tasks.py backend/app/api/professors.py backend/test/test_operation_audit_logs.py
git commit -m "feat(backend): audit key user operations"
```

---

### 任务 7：前端诊断日志导出入口

**文件：**
- 创建：`frontend/src/components/organisms/DiagnosticLogPanel.tsx`
- 修改：`frontend/src/pages/ProfilePage.tsx`
- 创建：`frontend/test/DiagnosticLogPanel.test.tsx`

- [ ] **步骤 1：编写失败的组件测试**

创建 `frontend/test/DiagnosticLogPanel.test.tsx`：

```tsx
import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { DiagnosticLogPanel } from "@/components/organisms/DiagnosticLogPanel";
import {
  clearDiagnosticEvents,
  recordDiagnosticEvent,
} from "@/lib/diagnostics";

describe("DiagnosticLogPanel", () => {
  beforeEach(() => {
    window.localStorage.clear();
    vi.stubGlobal("URL", {
      createObjectURL: vi.fn(() => "blob:diagnostics"),
      revokeObjectURL: vi.fn(),
    });
  });

  it("shows diagnostic event count and clears logs", () => {
    recordDiagnosticEvent({ type: "user_action", message: "创建抓取任务" });

    render(<DiagnosticLogPanel />);

    expect(screen.getByText("最近 1 条诊断记录")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "清空诊断日志" }));

    expect(screen.getByText("最近 0 条诊断记录")).toBeInTheDocument();
  });

  it("downloads diagnostic snapshot", () => {
    const click = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => undefined);
    const appendChild = vi.spyOn(document.body, "appendChild");

    render(<DiagnosticLogPanel />);

    fireEvent.click(screen.getByRole("button", { name: "导出诊断日志" }));

    expect(appendChild).toHaveBeenCalled();
    expect(click).toHaveBeenCalled();
  });
});
```

- [ ] **步骤 2：运行测试验证失败**

运行：

```bash
cd frontend
npm test -- --run test/DiagnosticLogPanel.test.tsx
```

预期：FAIL，报错包含 `Cannot find module '@/components/organisms/DiagnosticLogPanel'`。

- [ ] **步骤 3：实现诊断面板**

创建 `frontend/src/components/organisms/DiagnosticLogPanel.tsx`：

```tsx
import { useSyncExternalStore } from "react";
import { Download, Trash2 } from "lucide-react";
import {
  clearDiagnosticEvents,
  downloadDiagnosticSnapshot,
  readDiagnosticEvents,
} from "@/lib/diagnostics";

const subscribeDiagnostics = (onStoreChange: () => void) => {
  window.addEventListener("storage", onStoreChange);
  window.addEventListener("diagnostics:changed", onStoreChange);
  return () => {
    window.removeEventListener("storage", onStoreChange);
    window.removeEventListener("diagnostics:changed", onStoreChange);
  };
};

const getSnapshot = () => readDiagnosticEvents().length;

export const DiagnosticLogPanel = () => {
  const eventCount = useSyncExternalStore(subscribeDiagnostics, getSnapshot, () => 0);

  const handleClear = () => {
    clearDiagnosticEvents();
    window.dispatchEvent(new Event("diagnostics:changed"));
  };

  return (
    <section className="rounded-3xl border border-stone-200 bg-white p-6 shadow-sm">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <h2 className="text-xl font-semibold text-stone-900">诊断日志</h2>
          <p className="mt-2 text-sm text-stone-500">
            最近 {eventCount} 条诊断记录。遇到问题时可导出给开发者排查。
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          <button type="button" className="ui-btn-secondary" onClick={downloadDiagnosticSnapshot}>
            <Download className="h-4 w-4" />
            导出诊断日志
          </button>
          <button type="button" className="ui-btn-secondary" onClick={handleClear}>
            <Trash2 className="h-4 w-4" />
            清空诊断日志
          </button>
        </div>
      </div>
    </section>
  );
};
```

同时在 `recordDiagnosticEvent` 和 `clearDiagnosticEvents` 内部追加事件派发：

```ts
window.dispatchEvent(new Event("diagnostics:changed"));
```

- [ ] **步骤 4：接入个人中心**

在 `frontend/src/pages/ProfilePage.tsx` imports 增加：

```tsx
import { DiagnosticLogPanel } from "@/components/organisms/DiagnosticLogPanel";
```

在页面底部的主要内容区域中，放在身份配置和模型配置区域之后：

```tsx
<DiagnosticLogPanel />
```

- [ ] **步骤 5：运行测试验证通过**

运行：

```bash
cd frontend
npm test -- --run test/DiagnosticLogPanel.test.tsx test/diagnostics.test.ts
```

预期：PASS。

- [ ] **步骤 6：Commit**

```bash
git add frontend/src/components/organisms/DiagnosticLogPanel.tsx frontend/src/pages/ProfilePage.tsx frontend/src/lib/diagnostics.ts frontend/test/DiagnosticLogPanel.test.tsx
git commit -m "feat(frontend): add diagnostic log export panel"
```

---

### 任务 8：记录关键前端用户操作

**文件：**
- 修改：`frontend/src/pages/ProfessorsPage.tsx`
- 修改：`frontend/src/pages/CreateTaskPage.tsx`
- 修改：`frontend/src/pages/TasksPage.tsx`
- 修改：`frontend/src/pages/ProfilePage.tsx`
- 修改：`frontend/test/ProfessorsPageCrawler.test.tsx`
- 修改：`frontend/test/TasksPageLayout.test.tsx`

- [ ] **步骤 1：编写失败的前端操作测试**

在 `frontend/test/ProfessorsPageCrawler.test.tsx` 中 mock diagnostics：

```ts
const recordDiagnosticEvent = vi.hoisted(() => vi.fn());

vi.mock("@/lib/diagnostics", () => ({
  recordDiagnosticEvent,
}));
```

在 `beforeEach` 中增加：

```ts
recordDiagnosticEvent.mockReset();
```

在创建抓取任务测试的提交断言后增加：

```ts
expect(recordDiagnosticEvent).toHaveBeenCalledWith(
  expect.objectContaining({
    type: "user_action",
    message: "创建抓取任务",
    metadata: expect.objectContaining({
      university: "测试大学",
      school: "计算机学院",
      start_url: "https://example.edu/faculty",
    }),
  }),
);
```

- [ ] **步骤 2：运行测试验证失败**

运行：

```bash
cd frontend
npm test -- --run test/ProfessorsPageCrawler.test.tsx
```

预期：FAIL，`recordDiagnosticEvent` 未被调用。

- [ ] **步骤 3：在创建抓取任务时记录操作**

在 `frontend/src/pages/ProfessorsPage.tsx` imports 增加：

```ts
import { recordDiagnosticEvent } from "@/lib/diagnostics";
```

在 `handleCreateCrawlJob` 调用 `createCrawlJob(payload)` 前增加：

```ts
recordDiagnosticEvent({
  type: "user_action",
  message: "创建抓取任务",
  metadata: payload,
});
```

在 catch 中增加失败操作记录：

```ts
recordDiagnosticEvent({
  type: "user_action",
  message: "创建抓取任务失败",
  metadata: {
    ...payload,
    error: getActionErrorMessage(crawlerError, "创建抓取任务失败"),
  },
});
```

- [ ] **步骤 4：记录其他关键页面操作**

在下列文件中 import `recordDiagnosticEvent`，并在用户主动触发的操作入口记录事件：

`frontend/src/pages/CreateTaskPage.tsx`

```ts
recordDiagnosticEvent({
  type: "user_action",
  message: "创建批量任务",
  metadata: {
    name: taskName,
    professor_count: selectedProfessorIds.length,
    schedule_type: scheduleType,
  },
});
```

`frontend/src/pages/TasksPage.tsx`

```ts
recordDiagnosticEvent({
  type: "user_action",
  message: `批量任务操作：${action}`,
  metadata: { task_id: taskId },
});
```

```ts
recordDiagnosticEvent({
  type: "user_action",
  message: "取消抓取任务",
  metadata: { job_id: jobId },
});
```

`frontend/src/pages/ProfilePage.tsx`

在保存身份、删除身份、保存模型、删除模型、测试模型连接、测试 SMTP/IMAP 连接时记录事件，使用这些 message 字符串：

```ts
"保存身份配置"
"删除身份配置"
"保存模型配置"
"删除模型配置"
"测试模型连接"
"测试邮箱连接"
```

metadata 只包含 `id`、`name`、`provider`、`email_address`、`host` 这类非密钥字段。

- [ ] **步骤 5：运行前端测试**

运行：

```bash
cd frontend
npm test -- --run test/ProfessorsPageCrawler.test.tsx test/TasksPageLayout.test.tsx
```

预期：PASS。

- [ ] **步骤 6：Commit**

```bash
git add frontend/src/pages/ProfessorsPage.tsx frontend/src/pages/CreateTaskPage.tsx frontend/src/pages/TasksPage.tsx frontend/src/pages/ProfilePage.tsx frontend/test/ProfessorsPageCrawler.test.tsx frontend/test/TasksPageLayout.test.tsx
git commit -m "feat(frontend): record key user actions"
```

---

### 任务 9：最终验证和文档说明

**文件：**
- 创建：`docs/diagnostic_logging.md`

- [ ] **步骤 1：创建使用说明文档**

创建 `docs/diagnostic_logging.md`：

```markdown
# 诊断日志说明

系统提供两类诊断日志：

1. 前端本地诊断日志
   - 记录最近 200 条用户关键操作、API 请求成功和失败。
   - 存储在浏览器 localStorage。
   - 不记录密码、Token、API Key、SMTP/IMAP 密码。
   - 用户可在个人中心导出 JSON 诊断包。

2. 后端操作审计日志
   - 记录关键业务操作：创建抓取任务、审核抓取候选、创建/暂停/继续/中止批量任务、导师管理操作。
   - 每条日志包含 request_id，可与前端导出的 `backend_request_id` 对应。
   - 查询接口：`GET /api/diagnostics/audit-logs?limit=100`。

排查问题建议流程：

1. 让用户在个人中心点击“导出诊断日志”。
2. 在 JSON 中找到最后一条 `api_error` 或失败的 `user_action`。
3. 复制其中的 `backend_request_id`。
4. 通过后端日志或 `/api/diagnostics/audit-logs` 查询同一个 request_id。
5. 对照业务实体 ID，例如 `crawl_job.id`、`batch_task.id`、`professor.id`。
```

- [ ] **步骤 2：运行完整验证命令**

运行：

```bash
cd frontend
npm test -- --run test/diagnostics.test.ts test/apiClient.test.ts test/DiagnosticLogPanel.test.tsx test/ProfessorsPageCrawler.test.tsx test/TasksPageLayout.test.tsx
npm run lint
npm run build
```

预期：前端测试、lint、build 全部通过；build 允许出现现有 Vite 大 chunk 警告。

运行：

```bash
cd backend
uv run python -m unittest test.test_request_logging test.test_operation_audit_logs test.test_crawl_jobs_api
```

预期：后端测试全部通过。

- [ ] **步骤 3：检查日志脱敏**

运行：

```bash
rg -n "password|api_key|secret|token" frontend/src/lib/diagnostics.ts backend/app/services/audit_log.py frontend/test/diagnostics.test.ts backend/test/test_operation_audit_logs.py
```

预期：只出现在脱敏规则或测试断言中，不出现真实密钥写入逻辑。

- [ ] **步骤 4：Commit**

```bash
git add docs/diagnostic_logging.md
git commit -m "docs: add diagnostic logging guide"
```

---

## 自检

**规格覆盖度：**
- 前端最近操作日志：任务 1、2、7、8 覆盖。
- 后端 request_id：任务 3 覆盖。
- 后端关键业务审计：任务 4、5、6 覆盖。
- 一键导出诊断包：任务 7 覆盖。
- 敏感信息脱敏：任务 1、4、9 覆盖。
- 测试和文档：每个任务都有定向测试，任务 9 覆盖最终验证和使用说明。

**占位符扫描：**
- 本计划没有未落地的占位说明或空步骤。
- 每个代码任务包含具体路径、测试、实现片段、验证命令和 commit 命令。

**类型一致性：**
- 前端事件类型统一使用 `DiagnosticEventType`。
- 前端 request id 字段统一为 `request_id`，后端响应头对应字段为 `backend_request_id`。
- 后端审计模型字段统一为 `metadata_json`，避免与 SQLAlchemy `metadata` 命名冲突。
