# 桌面端后端启动速度优化实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 缩短桌面端后端从进程启动到 `/health` 可响应的时间，并让启动超时诊断能定位到具体阶段。

**架构：** 将 API 路由聚合从 `app.api.__init__` 迁移到无副作用的专用聚合模块，避免轻量子模块导入触发全量路由加载。将启动阶段日志下沉到入口与应用创建路径，桌面端延长健康检查保底等待，并在慢启动阶段持续上报状态。

**技术栈：** FastAPI、uvicorn、Python unittest、Electron、TypeScript、Vitest、PyInstaller。

---

## 文件结构

- 创建：`backend/app/api/routers.py`，集中导出后端 API 路由列表，供 `main.py` 注册路由。
- 修改：`backend/app/api/__init__.py`，移除全量路由导入副作用，保留空包或轻量包说明。
- 修改：`backend/main.py`，改为从 `app.api.routers` 获取路由，注册路由时写启动阶段耗时日志。
- 修改：`backend/desktop_entry.py`，在 uvicorn 启动前写入口阶段日志，并确保不依赖完整 FastAPI 应用导入。
- 创建：`backend/app/core/startup_logging.py`，提供轻量启动日志函数，优先使用 `AUTO_EMAIL_SENDER_DATA_DIR`，失败时退化到 stderr。
- 创建：`backend/test/test_api_import_boundaries.py`，验证轻量 API 子模块导入不会触发全量路由导入。
- 修改：`backend/test/test_startup_runtime.py`，补充启动阶段日志写入的单元测试。
- 修改：`desktop/src/backend.ts`，将健康检查等待提升到 120 秒，并在 30 秒后继续上报慢启动状态。
- 修改：`desktop/test/backend.test.ts`，覆盖健康检查慢启动状态与 120 秒超时逻辑。
- 修改：`desktop/src/types.ts`，仅在需要新增诊断字段时调整类型；若 `BackendStatus` 现有字段足够，不修改。

## 任务 1：拆除 API 包全量导入副作用

**文件：**
- 创建：`backend/app/api/routers.py`
- 修改：`backend/app/api/__init__.py`
- 修改：`backend/main.py`
- 测试：`backend/test/test_api_import_boundaries.py`

- [ ] **步骤 1：编写失败的导入边界测试**

创建 `backend/test/test_api_import_boundaries.py`：

```python
from __future__ import annotations

import importlib
import sys
import unittest


class ApiImportBoundaryTest(unittest.TestCase):
    def test_identity_serializers_import_does_not_load_route_modules(self) -> None:
        for module_name in list(sys.modules):
            if module_name == "app.api" or module_name.startswith("app.api."):
                sys.modules.pop(module_name)

        importlib.import_module("app.api.identity_serializers")

        self.assertNotIn("app.api.batch_tasks", sys.modules)
        self.assertNotIn("app.api.crawl_jobs", sys.modules)
        self.assertNotIn("app.api.test_compose", sys.modules)
        self.assertNotIn("app.api.workspaces", sys.modules)

    def test_router_aggregation_loads_expected_routers(self) -> None:
        routers = importlib.import_module("app.api.routers")

        self.assertGreaterEqual(len(routers.API_ROUTERS), 10)
        self.assertTrue(all(hasattr(router, "routes") for router in routers.API_ROUTERS))


if __name__ == "__main__":
    unittest.main()
```

- [ ] **步骤 2：运行测试验证失败**

运行：`cd backend && uv run python -m unittest test.test_api_import_boundaries`

预期：第一个测试失败，因为当前 `app.api.__init__` 会导入所有路由；第二个测试失败，因为 `app.api.routers` 尚不存在。

- [ ] **步骤 3：创建路由聚合模块**

创建 `backend/app/api/routers.py`：

```python
from __future__ import annotations

from fastapi import APIRouter

from app.api.batch_tasks import router as batch_tasks_router
from app.api.crawl_jobs import router as crawl_jobs_router
from app.api.diagnostics import router as diagnostics_router
from app.api.email_tasks import router as email_tasks_router
from app.api.identities import router as identities_router
from app.api.llm_profiles import router as llm_profiles_router
from app.api.materials import router as materials_router
from app.api.match_analysis_jobs import router as match_analysis_jobs_router
from app.api.professors import router as professors_router
from app.api.runtime_settings import router as runtime_settings_router
from app.api.test_compose import router as test_compose_router
from app.api.token_usage import router as token_usage_router
from app.api.workspaces import router as workspaces_router

API_ROUTERS: tuple[APIRouter, ...] = (
    identities_router,
    materials_router,
    match_analysis_jobs_router,
    llm_profiles_router,
    professors_router,
    test_compose_router,
    crawl_jobs_router,
    diagnostics_router,
    batch_tasks_router,
    email_tasks_router,
    workspaces_router,
    token_usage_router,
    runtime_settings_router,
)
```

- [ ] **步骤 4：清空 `app.api.__init__` 的副作用导入**

将 `backend/app/api/__init__.py` 改为：

```python
"""API package.

Route aggregation lives in app.api.routers so importing lightweight API helper
modules does not eagerly import every route module.
"""
```

- [ ] **步骤 5：让 `main.py` 使用路由列表注册**

将 `backend/main.py` 顶层路由导入替换为：

```python
from app.api.routers import API_ROUTERS
```

将 `create_app()` 中连续的 `app.include_router(...)` 替换为：

```python
    for router in API_ROUTERS:
        app.include_router(router)
```

- [ ] **步骤 6：运行导入边界测试验证通过**

运行：`cd backend && uv run python -m unittest test.test_api_import_boundaries`

预期：PASS，`app.api.identity_serializers` 不再加载所有路由。

- [ ] **步骤 7：运行相关后端测试**

运行：`cd backend && uv run python -m unittest test.test_desktop_runtime test.test_workspace_support test.test_operation_log_integration`

预期：PASS，说明路由注册和现有 patch 路径未被破坏。

- [ ] **步骤 8：Commit**

```bash
git add backend/app/api/__init__.py backend/app/api/routers.py backend/main.py backend/test/test_api_import_boundaries.py
git commit -m "perf(backend): decouple api package imports"
```

## 任务 2：增加轻量启动阶段日志

**文件：**
- 创建：`backend/app/core/startup_logging.py`
- 修改：`backend/desktop_entry.py`
- 修改：`backend/main.py`
- 修改：`backend/test/test_startup_runtime.py`

- [ ] **步骤 1：编写启动日志单元测试**

在 `backend/test/test_startup_runtime.py` 中新增测试：

```python
    def test_startup_phase_log_writes_without_full_settings(self) -> None:
        from app.core.startup_logging import write_startup_phase_log

        with tempfile.TemporaryDirectory() as temp_dir:
            with patch.dict("os.environ", {"AUTO_EMAIL_SENDER_DATA_DIR": temp_dir}):
                write_startup_phase_log("desktop_entry.start", detail="port=48120")

            log_text = (Path(temp_dir) / "logs" / "startup.log").read_text(encoding="utf-8")

        self.assertIn("desktop_entry.start", log_text)
        self.assertIn("port=48120", log_text)
```

- [ ] **步骤 2：运行测试验证失败**

运行：`cd backend && uv run python -m unittest test.test_startup_runtime.StartupRuntimeTest.test_startup_phase_log_writes_without_full_settings`

预期：FAIL，报错 `ModuleNotFoundError: No module named 'app.core.startup_logging'`。

- [ ] **步骤 3：实现轻量启动日志模块**

创建 `backend/app/core/startup_logging.py`：

```python
from __future__ import annotations

import os
import sys
from datetime import UTC, datetime
from pathlib import Path


def write_startup_phase_log(phase: str, *, detail: str | None = None) -> None:
    data_dir = os.environ.get("AUTO_EMAIL_SENDER_DATA_DIR")
    if not data_dir:
        print(f"[startup] {phase}{_format_detail(detail)}", file=sys.stderr)
        return

    try:
        log_path = Path(data_dir) / "logs" / "startup.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        line = f"[{datetime.now(UTC).isoformat()}] phase={phase}{_format_detail(detail)}\n"
        with log_path.open("a", encoding="utf-8", newline="\n") as file:
            file.write(line)
    except Exception as exc:
        print(f"[startup] failed to write phase log: {phase}: {exc}", file=sys.stderr)


def _format_detail(detail: str | None) -> str:
    return "" if detail is None else f" detail={detail}"
```

- [ ] **步骤 4：在入口和应用创建阶段写日志**

修改 `backend/desktop_entry.py`：

```python
from app.core.startup_logging import write_startup_phase_log
```

在 `main()` 中 `uvicorn.run(...)` 前加入：

```python
    write_startup_phase_log("desktop_entry.start", detail=f"host={options['host']} port={options['port']}")
```

修改 `backend/main.py`：

```python
from app.core.startup_logging import write_startup_phase_log
```

在 `create_app()` 开头和返回前加入：

```python
    write_startup_phase_log("main.create_app.start")
```

```python
    write_startup_phase_log("main.create_app.ready", detail=f"routers={len(API_ROUTERS)}")
```

- [ ] **步骤 5：复用启动日志模块写异常日志**

保留 `main.py` 中现有 `write_startup_diagnostic_log(...)` 格式，不要在本任务迁移异常日志实现；只新增阶段日志，避免影响已有诊断测试。

- [ ] **步骤 6：运行启动日志测试验证通过**

运行：`cd backend && uv run python -m unittest test.test_startup_runtime`

预期：PASS，既有数据库锁日志测试仍通过。

- [ ] **步骤 7：Commit**

```bash
git add backend/app/core/startup_logging.py backend/desktop_entry.py backend/main.py backend/test/test_startup_runtime.py
git commit -m "feat(backend): log early startup phases"
```

## 任务 3：优化桌面端健康检查等待与慢启动状态

**文件：**
- 修改：`desktop/src/backend.ts`
- 修改：`desktop/test/backend.test.ts`
- 可选修改：`desktop/src/types.ts`

- [ ] **步骤 1：导出健康检查等待函数以便测试**

将 `desktop/src/backend.ts` 中的：

```ts
async function waitForHealth(
```

改为：

```ts
export async function waitForHealth(
```

并把签名扩展为可注入测试参数：

```ts
export async function waitForHealth(
  baseUrl: string,
  child: ChildProcessWithoutNullStreams,
  getStderr: () => string,
  options: {
    onStatus?: (status: BackendStatus) => void;
    pollIntervalMs?: number;
    timeoutMs?: number;
    slowStartupMs?: number;
  } = {},
): Promise<void> {
```

- [ ] **步骤 2：编写慢健康检查测试**

在 `desktop/test/backend.test.ts` 导入列表中加入 `waitForHealth`，并新增测试：

```ts
  it("keeps waiting after slow health startup threshold", async () => {
    const observed: string[] = [];
    const child = createRunningChildProcess();
    const server = createServer((request, response) => {
      if (request.url === "/health") {
        response.writeHead(200, { "Content-Type": "application/json" });
        response.end(JSON.stringify({ status: "ok" }));
        return;
      }
      response.writeHead(404);
      response.end();
    });

    await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", resolve));
    const address = server.address() as AddressInfo;

    try {
      await expect(
        waitForHealth(`http://127.0.0.1:${address.port}`, child, () => "", {
          pollIntervalMs: 1,
          timeoutMs: 1_000,
          slowStartupMs: 0,
          onStatus: (status) => observed.push(status.message),
        }),
      ).resolves.toBeUndefined();
    } finally {
      await new Promise<void>((resolve, reject) => {
        server.close((error) => (error ? reject(error) : resolve()));
      });
    }

    expect(observed).toContain("首次启动可能较慢，正在继续等待本地服务");
  });
```

如果测试文件中没有可复用的运行中子进程 helper，添加：

```ts
function createRunningChildProcess(): ChildProcessWithoutNullStreams {
  const child = new EventEmitter() as ChildProcessWithoutNullStreams;
  child.exitCode = null;
  child.stderr = new EventEmitter() as ChildProcessWithoutNullStreams["stderr"];
  return child;
}
```

- [ ] **步骤 3：运行测试验证失败**

运行：`cd desktop && npm run test -- backend.test.ts`

预期：FAIL，原因是 `waitForHealth` 未导出或不会发慢启动状态。

- [ ] **步骤 4：实现 120 秒保底和慢启动状态**

在 `waitForReady(...)` 调用处改为：

```ts
  await waitForHealth(baseUrl, child, () => stderr, { onStatus });
```

在 `waitForHealth(...)` 内使用默认值：

```ts
  const pollIntervalMs = options.pollIntervalMs ?? 400;
  const timeoutMs = options.timeoutMs ?? 120_000;
  const slowStartupMs = options.slowStartupMs ?? 30_000;
  const startedAt = Date.now();
  const deadline = startedAt + timeoutMs;
  let slowStatusEmitted = false;
```

循环中在超过慢启动阈值后上报：

```ts
    const elapsedMs = Date.now() - startedAt;
    if (!slowStatusEmitted && elapsedMs >= slowStartupMs) {
      slowStatusEmitted = true;
      const elapsedSeconds = Math.round(elapsedMs / 1000);
      options.onStatus?.({
        state: "starting",
        phase: "starting",
        message: "首次启动可能较慢，正在继续等待本地服务",
        elapsedSeconds,
        slowStartup: true,
        verySlowStartup: elapsedSeconds >= 120,
      });
    }
```

将循环 sleep 改为：

```ts
    await new Promise((resolve) => setTimeout(resolve, pollIntervalMs));
```

- [ ] **步骤 5：增强超时错误信息**

将超时错误改为包含等待秒数和进程状态：

```ts
  const waitedSeconds = Math.round((Date.now() - startedAt) / 1000);
  throw new Error(
    `Backend health check timed out after ${waitedSeconds}s; processExited=${child.exitCode !== null}: ${getStderr().slice(-800)}`,
  );
```

- [ ] **步骤 6：运行桌面端测试验证通过**

运行：`cd desktop && npm run test -- backend.test.ts`

预期：PASS。

- [ ] **步骤 7：运行桌面端类型检查**

运行：`cd desktop && npm run typecheck`

预期：PASS。

- [ ] **步骤 8：Commit**

```bash
git add desktop/src/backend.ts desktop/test/backend.test.ts desktop/src/types.ts
git commit -m "fix(desktop): tolerate slow backend health startup"
```

## 任务 4：第一批重依赖懒加载

**文件：**
- 修改：`backend/app/services/crawl_job_runtime.py`
- 修改：`backend/app/agents/faculty_crawler_agent.py`
- 修改：`backend/app/services/llm_runtime.py`
- 修改：`backend/app/services/materials.py`
- 测试：复用现有后端测试；必要时新增 `backend/test/test_import_performance_boundaries.py`

- [ ] **步骤 1：记录优化前基线**

运行：`cd backend && uv run python -X importtime -c "import main" 2> ..\importtime-before.log`

运行：`cd backend && uv run python -c "import time; t=time.perf_counter(); import main; print(round(time.perf_counter()-t, 3))"`

预期：记录当前 `import main` 秒数到任务执行备注，不提交 `importtime-before.log`。

- [ ] **步骤 2：识别顶层重依赖**

运行：`cd backend && rg -n "^from (langchain|openai|markitdown|playwright|patchright|pdfplumber|pypdf)|^import (langchain|openai|markitdown|playwright|patchright|pdfplumber|pypdf)" app`

预期：得到需要懒加载的导入清单。

- [ ] **步骤 3：为 LLM 运行时添加局部导入函数**

在 `backend/app/services/llm_runtime.py` 中，将仅在请求模型、探测配置或生成内容时使用的第三方 SDK 移到局部函数。示例模式：

```python
def _get_openai_client_class():
    from openai import AsyncOpenAI

    return AsyncOpenAI
```

将原本直接使用 `AsyncOpenAI(...)` 的位置改为：

```python
AsyncOpenAI = _get_openai_client_class()
client = AsyncOpenAI(api_key=profile.api_key, base_url=profile.base_url)
```

- [ ] **步骤 4：为爬虫运行时添加局部导入函数**

在 `backend/app/services/crawl_job_runtime.py` 中，将只在真正执行爬虫任务时需要的 agent 构建和浏览器工具导入迁移到函数内部。示例模式：

```python
def _get_crawler_agent_functions():
    from app.agents.faculty_crawler_agent import build_faculty_crawler_model, run_faculty_crawler_agent

    return build_faculty_crawler_model, run_faculty_crawler_agent
```

在执行任务前使用：

```python
build_faculty_crawler_model, run_faculty_crawler_agent = _get_crawler_agent_functions()
```

- [ ] **步骤 5：为材料解析添加局部导入函数**

在实际解析文件内容的函数内部导入 `markitdown`、`pdfplumber`、`pypdf`、`mammoth` 等依赖。保持 `material_can_be_primary(...)` 等轻量判断函数不依赖重解析库。

- [ ] **步骤 6：运行针对性后端测试**

运行：`cd backend && uv run python -m unittest test.test_materials_api test.test_crawl_jobs_api test.test_llm_profiles_api test.test_desktop_runtime`

预期：PASS。若某个测试模块不存在，改为运行对应现有测试文件名，并在执行记录中说明。

- [ ] **步骤 7：记录优化后基线并清理临时文件**

运行：`cd backend && uv run python -X importtime -c "import main" 2> ..\importtime-after.log`

运行：`cd backend && uv run python -c "import time; t=time.perf_counter(); import main; print(round(time.perf_counter()-t, 3))"`

确认 `importtime-before.log` 和 `importtime-after.log` 不进入 git：

```bash
git status --short
```

删除临时日志：

```powershell
Remove-Item ..\importtime-before.log, ..\importtime-after.log -ErrorAction SilentlyContinue
```

- [ ] **步骤 8：Commit**

```bash
git add backend/app/services/crawl_job_runtime.py backend/app/agents/faculty_crawler_agent.py backend/app/services/llm_runtime.py backend/app/services/materials.py
git commit -m "perf(backend): lazy load heavy startup dependencies"
```

## 任务 5：端到端验证与文档回填

**文件：**
- 修改：`docs/superpowers/specs/2026-05-19-desktop-backend-startup-optimization-design.md`
- 修改：`docs/superpowers/plans/2026-05-19-desktop-backend-startup-optimization.md`

- [ ] **步骤 1：运行后端完整测试**

运行：`cd backend && uv run python -m unittest discover test`

预期：PASS。

- [ ] **步骤 2：运行桌面端验证**

运行：`cd desktop && npm run typecheck`

预期：PASS。

运行：`cd desktop && npm run test`

预期：PASS。

- [ ] **步骤 3：运行前端验证**

如果桌面端状态字段或文案影响前端展示，运行：`cd frontend && npm run test -- DesktopBackendContext`

预期：PASS。若没有匹配测试，运行 `cd frontend && npm run test`，并记录结果。

- [ ] **步骤 4：打包后端冒烟测试**

运行：`pwsh scripts/build-backend.ps1`

预期：`backend/dist/backend/backend.exe` 生成成功。

随后运行一次手动健康检查：

```powershell
$env:AUTO_EMAIL_SENDER_DATA_DIR = Join-Path $env:TEMP "aes-startup-smoke"
$env:ENABLE_BACKGROUND_WORKERS = "true"
$process = Start-Process -FilePath "backend\dist\backend\backend.exe" -ArgumentList @("--host", "127.0.0.1", "--port", "48288") -PassThru -WindowStyle Hidden
try {
  Start-Sleep -Seconds 3
  Invoke-WebRequest -Uri "http://127.0.0.1:48288/health" -UseBasicParsing
} finally {
  Stop-Process -Id $process.Id -Force -ErrorAction SilentlyContinue
}
```

预期：返回 HTTP 200 和 `{"status":"ok"}`。

- [ ] **步骤 5：回填实测结果**

在规格或计划文档的验证记录中补充：

```markdown
## 实施验证记录

- `import main` 优化前：X.XXX 秒；优化后：Y.YYY 秒。
- 打包后端 `/health` 可响应时间：Z.ZZZ 秒。
- 后端测试：`uv run python -m unittest discover test`，PASS。
- 桌面端测试：`npm run test`，PASS。
```

- [ ] **步骤 6：最终状态检查**

运行：`git status --short`

预期：只包含本次代码和文档改动，不包含 `importtime-*.log`、临时数据目录、打包输出或测试缓存。

- [ ] **步骤 7：Commit**

```bash
git add docs/superpowers/specs/2026-05-19-desktop-backend-startup-optimization-design.md docs/superpowers/plans/2026-05-19-desktop-backend-startup-optimization.md
git commit -m "docs: add backend startup optimization plan"
```

