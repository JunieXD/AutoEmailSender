# 其他设置运行时并发配置实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 在个人中心的开发诊断日志前新增“其他设置”卡片，让用户配置批量匹配和智能抓取相关并发限制，并让后台 worker 尽量运行时读取最新配置。

**架构：** 复用现有单行 `app_settings` 表，新增并发配置字段、Pydantic schema、`/api/runtime-settings` 读写接口和服务层校验。前端新增 `OtherSettingsCard`，通过 API 读取/保存设置，并插入 `TokenUsageCenterCard` 与 `DiagnosticLogPanel` 之间。后台 `RuntimeManager` 保持 worker 数在启动时确定，但每轮读取数据库中的单 job 并发与轮询间隔；抓取 worker 数需要重启后端生效，UI 中明确展示这一点。

**技术栈：** FastAPI、SQLAlchemy/Alembic、unittest、Vite React、TypeScript、Tailwind、lucide-react。

---

## 文件结构

- 修改：`backend/app/models/app_setting.py`
  - 为 `app_settings` 单行记录新增运行时配置字段。
- 新建：`backend/alembic/versions/<revision>_add_runtime_concurrency_settings.py`
  - 增加新列并提供 downgrade。
- 新建：`backend/app/schemas/runtime_settings.py`
  - 定义 `RuntimeSettingsRead` 和 `RuntimeSettingsUpdate`。
- 新建：`backend/app/services/runtime_settings.py`
  - 读取默认值、创建设置、校验范围、应用 patch。
- 新建：`backend/app/api/runtime_settings.py`
  - 暴露 `GET /api/runtime-settings` 和 `PATCH /api/runtime-settings`。
- 修改：`backend/app/api/__init__.py`
  - 导出 runtime settings router。
- 修改：`backend/main.py`
  - 注册 runtime settings router。
- 修改：`backend/app/services/runtime_manager.py`
  - 每轮匹配任务读取数据库中的 `match_analysis_job_item_concurrency` 与 interval；启动时 worker 数仍来自配置表当前值或 env 默认。
- 修改：`backend/app/services/crawl_job_runtime.py`
  - 单次抓取任务运行时读取 `crawler_profile_enrichment_concurrency` 和 `crawler_host_concurrency` 覆盖 env 默认。
- 新建：`frontend/src/lib/api/runtimeSettings.ts`
  - 前端 API client。
- 新建：`frontend/src/components/molecules/OtherSettingsCard.tsx`
  - 折叠卡片和表单。
- 修改：`frontend/src/pages/ProfilePage.tsx`
  - 导入并插入 `OtherSettingsCard`，位置在 `TokenUsageCenterCard` 后、`DiagnosticLogPanel` 前。
- 测试：`backend/test/test_runtime_settings_api.py`
  - 覆盖读取默认值、保存校验、日志写入。
- 测试：`backend/test/test_runtime_manager.py`
  - 覆盖 RuntimeManager 每轮读取 item 并发。
- 测试：`backend/test/test_crawl_job_runtime.py`
  - 覆盖 crawl runtime 使用数据库配置覆盖默认并发。
- 测试：`frontend/test/OtherSettingsCard.test.tsx`
  - 覆盖加载、保存、校验失败提示。

---

### 任务 1：后端持久化字段和 schema

**文件：**
- 修改：`backend/app/models/app_setting.py`
- 创建：`backend/alembic/versions/<revision>_add_runtime_concurrency_settings.py`
- 创建：`backend/app/schemas/runtime_settings.py`
- 测试：`backend/test/test_database_schema.py`

- [ ] **步骤 1：编写失败的数据库 schema 测试**

在 `backend/test/test_database_schema.py` 的 `test_tables_have_expected_columns` 中追加断言，检查 `app_settings` 包含下列列：

```python
app_setting_columns = self._get_columns("app_settings")
for column_name in [
    "match_analysis_job_worker_count",
    "match_analysis_job_item_concurrency",
    "match_analysis_job_interval_seconds",
    "crawler_worker_count",
    "crawler_profile_enrichment_concurrency",
    "crawler_host_concurrency",
]:
    self.assertIn(column_name, app_setting_columns)
```

- [ ] **步骤 2：运行测试验证失败**

运行：

```bash
rtk powershell -NoProfile -Command "uv run python -m unittest test.test_database_schema.DatabaseSchemaTests.test_tables_have_expected_columns"
```

预期：FAIL，缺少新增列。

- [ ] **步骤 3：修改模型**

在 `backend/app/models/app_setting.py` 中导入 `Integer`，并在 `AppSetting` 上新增字段：

```python
from sqlalchemy import DateTime, Integer, text

match_analysis_job_worker_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))
match_analysis_job_item_concurrency: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("3"))
match_analysis_job_interval_seconds: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("10"))
crawler_worker_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("2"))
crawler_profile_enrichment_concurrency: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("3"))
crawler_host_concurrency: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))
```

- [ ] **步骤 4：新增 Alembic 迁移**

创建 `backend/alembic/versions/<revision>_add_runtime_concurrency_settings.py`，revision 用新的 12 位十六进制字符串，`down_revision` 指向当前 head。升级代码：

```python
def upgrade() -> None:
    with op.batch_alter_table("app_settings", schema=None) as batch_op:
        batch_op.add_column(sa.Column("match_analysis_job_worker_count", sa.Integer(), server_default="1", nullable=False))
        batch_op.add_column(sa.Column("match_analysis_job_item_concurrency", sa.Integer(), server_default="3", nullable=False))
        batch_op.add_column(sa.Column("match_analysis_job_interval_seconds", sa.Integer(), server_default="10", nullable=False))
        batch_op.add_column(sa.Column("crawler_worker_count", sa.Integer(), server_default="2", nullable=False))
        batch_op.add_column(sa.Column("crawler_profile_enrichment_concurrency", sa.Integer(), server_default="3", nullable=False))
        batch_op.add_column(sa.Column("crawler_host_concurrency", sa.Integer(), server_default="1", nullable=False))
```

降级按反序 drop。

- [ ] **步骤 5：新增 schema**

创建 `backend/app/schemas/runtime_settings.py`：

```python
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class RuntimeSettingsRead(BaseModel):
    match_analysis_job_worker_count: int
    match_analysis_job_item_concurrency: int
    match_analysis_job_interval_seconds: int
    crawler_worker_count: int
    crawler_profile_enrichment_concurrency: int
    crawler_host_concurrency: int
    updated_at: datetime


class RuntimeSettingsUpdate(BaseModel):
    match_analysis_job_worker_count: int = Field(ge=1, le=8)
    match_analysis_job_item_concurrency: int = Field(ge=1, le=20)
    match_analysis_job_interval_seconds: int = Field(ge=1, le=300)
    crawler_worker_count: int = Field(ge=1, le=8)
    crawler_profile_enrichment_concurrency: int = Field(ge=1, le=20)
    crawler_host_concurrency: int = Field(ge=1, le=8)
```

- [ ] **步骤 6：运行 schema 测试通过**

运行：

```bash
rtk powershell -NoProfile -Command "uv run python -m unittest test.test_database_schema.DatabaseSchemaTests.test_tables_have_expected_columns"
```

预期：OK。

- [ ] **步骤 7：Commit**

```bash
git add backend/app/models/app_setting.py backend/app/schemas/runtime_settings.py backend/alembic/versions/<revision>_add_runtime_concurrency_settings.py backend/test/test_database_schema.py
git commit -m "feat(settings): add runtime concurrency schema"
```

---

### 任务 2：后端 runtime settings API

**文件：**
- 创建：`backend/app/services/runtime_settings.py`
- 创建：`backend/app/api/runtime_settings.py`
- 修改：`backend/app/api/__init__.py`
- 修改：`backend/main.py`
- 测试：`backend/test/test_runtime_settings_api.py`

- [ ] **步骤 1：编写失败的 API 测试**

创建 `backend/test/test_runtime_settings_api.py`，测试默认读取、更新和非法值：

```python
from __future__ import annotations

import asyncio
import os
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient


class RuntimeSettingsApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.temp_dir = tempfile.TemporaryDirectory()
        cls.db_path = Path(cls.temp_dir.name) / "runtime_settings.db"
        os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{cls.db_path.as_posix()}"
        os.environ["ENABLE_BACKGROUND_WORKERS"] = "0"

        from app.core.config import get_settings
        from app.core.database import dispose_engine, get_engine, get_session_factory
        from main import create_app

        get_settings.cache_clear()
        if get_engine.cache_info().currsize:
            asyncio.run(dispose_engine())
        get_session_factory.cache_clear()
        get_settings.cache_clear()

        asyncio.run(cls._create_schema())
        cls.client = TestClient(create_app())

    @classmethod
    def tearDownClass(cls) -> None:
        cls.client.close()
        from app.core.config import get_settings
        from app.core.database import dispose_engine, get_engine, get_session_factory

        if get_engine.cache_info().currsize:
            asyncio.run(dispose_engine())
        get_session_factory.cache_clear()
        get_settings.cache_clear()
        os.environ.pop("DATABASE_URL", None)
        os.environ.pop("ENABLE_BACKGROUND_WORKERS", None)
        cls.temp_dir.cleanup()

    def test_get_runtime_settings_returns_defaults(self) -> None:
        response = self.client.get("/api/runtime-settings")

        self.assertEqual(response.status_code, 200, msg=response.text)
        payload = response.json()
        self.assertEqual(payload["match_analysis_job_item_concurrency"], 3)
        self.assertEqual(payload["crawler_host_concurrency"], 1)

    def test_patch_runtime_settings_updates_values_and_records_log(self) -> None:
        response = self.client.patch(
            "/api/runtime-settings",
            json={
                "match_analysis_job_worker_count": 2,
                "match_analysis_job_item_concurrency": 4,
                "match_analysis_job_interval_seconds": 5,
                "crawler_worker_count": 3,
                "crawler_profile_enrichment_concurrency": 4,
                "crawler_host_concurrency": 2,
            },
        )

        self.assertEqual(response.status_code, 200, msg=response.text)
        self.assertEqual(response.json()["match_analysis_job_item_concurrency"], 4)
        logs = self.client.get(
            "/api/diagnostics/operation-logs",
            params={"event_name": "runtime_settings.updated"},
        )
        self.assertEqual(logs.status_code, 200, msg=logs.text)
        self.assertEqual(len(logs.json()["items"]), 1)

    def test_patch_runtime_settings_rejects_out_of_range_values(self) -> None:
        response = self.client.patch(
            "/api/runtime-settings",
            json={
                "match_analysis_job_worker_count": 0,
                "match_analysis_job_item_concurrency": 4,
                "match_analysis_job_interval_seconds": 5,
                "crawler_worker_count": 3,
                "crawler_profile_enrichment_concurrency": 4,
                "crawler_host_concurrency": 2,
            },
        )

        self.assertEqual(response.status_code, 422)

    @classmethod
    async def _create_schema(cls) -> None:
        from app.core.database import get_engine
        from app.models import Base

        async with get_engine().begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
```

- [ ] **步骤 2：运行测试验证失败**

运行：

```bash
rtk powershell -NoProfile -Command "uv run python -m unittest test/test_runtime_settings_api.py"
```

预期：FAIL，`/api/runtime-settings` 返回 404。

- [ ] **步骤 3：实现服务层**

创建 `backend/app/services/runtime_settings.py`：

```python
from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models import AppSetting
from app.schemas.runtime_settings import RuntimeSettingsRead, RuntimeSettingsUpdate
from app.services.operation_logs import record_operation_log
from app.services.system_settings import get_or_create_app_settings


def _apply_env_defaults(settings: AppSetting) -> None:
    env_settings = get_settings()
    if settings.match_analysis_job_worker_count is None:
        settings.match_analysis_job_worker_count = env_settings.match_analysis_job_worker_count


def serialize_runtime_settings(settings: AppSetting) -> RuntimeSettingsRead:
    return RuntimeSettingsRead(
        match_analysis_job_worker_count=settings.match_analysis_job_worker_count,
        match_analysis_job_item_concurrency=settings.match_analysis_job_item_concurrency,
        match_analysis_job_interval_seconds=settings.match_analysis_job_interval_seconds,
        crawler_worker_count=settings.crawler_worker_count,
        crawler_profile_enrichment_concurrency=settings.crawler_profile_enrichment_concurrency,
        crawler_host_concurrency=settings.crawler_host_concurrency,
        updated_at=settings.updated_at,
    )


async def get_runtime_settings(session: AsyncSession) -> AppSetting:
    settings = await get_or_create_app_settings(session)
    return settings


async def update_runtime_settings(
    session: AsyncSession,
    payload: RuntimeSettingsUpdate,
) -> AppSetting:
    settings = await get_or_create_app_settings(session)
    previous = serialize_runtime_settings(settings).model_dump(mode="json")
    for key, value in payload.model_dump().items():
        setattr(settings, key, value)
    settings.updated_at = datetime.now(UTC)
    await record_operation_log(
        session,
        category="backend",
        event_name="runtime_settings.updated",
        entity_type="runtime_settings",
        entity_id="1",
        metadata={
            "previous": previous,
            "next": payload.model_dump(),
        },
    )
    return settings
```

如果模型字段为非 nullable，可删除 `_apply_env_defaults`。

- [ ] **步骤 4：实现 API**

创建 `backend/app/api/runtime_settings.py`：

```python
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_async_session
from app.schemas.runtime_settings import RuntimeSettingsRead, RuntimeSettingsUpdate
from app.services.runtime_settings import (
    get_runtime_settings,
    serialize_runtime_settings,
    update_runtime_settings,
)


router = APIRouter(prefix="/api/runtime-settings", tags=["runtime-settings"])


@router.get("", response_model=RuntimeSettingsRead)
async def read_runtime_settings(
    session: AsyncSession = Depends(get_async_session),
) -> RuntimeSettingsRead:
    settings = await get_runtime_settings(session)
    await session.commit()
    return serialize_runtime_settings(settings)


@router.patch("", response_model=RuntimeSettingsRead)
async def patch_runtime_settings(
    payload: RuntimeSettingsUpdate,
    session: AsyncSession = Depends(get_async_session),
) -> RuntimeSettingsRead:
    settings = await update_runtime_settings(session, payload)
    await session.commit()
    return serialize_runtime_settings(settings)
```

- [ ] **步骤 5：注册 router**

在 `backend/app/api/__init__.py` 导入：

```python
from app.api.runtime_settings import router as runtime_settings_router
```

加入 `__all__`。

在 `backend/main.py` 的 `from app.api import (...)` 添加 `runtime_settings_router`，并在 `create_app()` 中加入：

```python
app.include_router(runtime_settings_router)
```

- [ ] **步骤 6：运行 API 测试通过**

运行：

```bash
rtk powershell -NoProfile -Command "uv run python -m unittest test/test_runtime_settings_api.py"
```

预期：OK。

- [ ] **步骤 7：Commit**

```bash
git add backend/app/services/runtime_settings.py backend/app/api/runtime_settings.py backend/app/api/__init__.py backend/main.py backend/test/test_runtime_settings_api.py
git commit -m "feat(settings): expose runtime settings api"
```

---

### 任务 3：后台 worker 读取运行时设置

**文件：**
- 修改：`backend/app/services/runtime_manager.py`
- 修改：`backend/app/services/crawl_job_runtime.py`
- 测试：`backend/test/test_runtime_manager.py`
- 测试：`backend/test/test_crawl_job_runtime.py`

- [ ] **步骤 1：编写 RuntimeManager 失败测试**

在 `backend/test/test_runtime_manager.py` 添加测试，mock `get_runtime_settings` 返回 `match_analysis_job_item_concurrency=7`，断言传给 `run_queued_match_analysis_jobs_once` 的 `item_concurrency` 是 7。测试骨架：

```python
async def fake_get_runtime_settings(session):
    return SimpleNamespace(
        match_analysis_job_interval_seconds=10,
        match_analysis_job_item_concurrency=7,
    )
```

使用 `AsyncMock` patch `app.services.runtime_manager.run_queued_match_analysis_jobs_once`，直接调用新增 helper `_run_match_analysis_worker_once`。

- [ ] **步骤 2：运行 RuntimeManager 测试验证失败**

运行：

```bash
rtk powershell -NoProfile -Command "uv run python -m unittest test/test_runtime_manager.py"
```

预期：FAIL，helper 不存在或仍使用 env 值。

- [ ] **步骤 3：实现 RuntimeManager helper**

在 `backend/app/services/runtime_manager.py` 导入：

```python
from app.services.runtime_settings import get_runtime_settings
```

新增方法：

```python
async def _run_match_analysis_worker_once(
    session_factory: async_sessionmaker[AsyncSession],
) -> int:
    async with session_factory() as session:
        runtime_settings = await get_runtime_settings(session)
    return await run_queued_match_analysis_jobs_once(
        session_factory,
        item_concurrency=runtime_settings.match_analysis_job_item_concurrency,
    )
```

`start()` 中 match worker 的 worker 参数改成 `_run_match_analysis_worker_once`。interval 仍用启动时 `settings.match_analysis_job_interval_seconds` 或计划内可进一步用动态 sleep；本任务只要求并发运行时生效。

- [ ] **步骤 4：编写 crawl 运行时失败测试**

在 `backend/test/test_crawl_job_runtime.py` 中 patch runtime settings，断言 `_enrich_saved_candidates` 或调用点使用数据库设置中的 `crawler_profile_enrichment_concurrency` 与 `crawler_host_concurrency`。如果现有测试太重，新增一个针对配置解析 helper 的测试：

```python
async def test_resolve_crawl_runtime_concurrency_prefers_database_settings(self):
    settings = SimpleNamespace(
        crawler_profile_enrichment_concurrency=6,
        crawler_host_concurrency=2,
    )
    resolved = resolve_crawl_runtime_concurrency(settings)
    self.assertEqual(resolved.profile_enrichment_concurrency, 6)
    self.assertEqual(resolved.host_concurrency, 2)
```

- [ ] **步骤 5：实现 crawl 配置解析**

在 `backend/app/services/crawl_job_runtime.py` 新增小 dataclass 和 helper：

```python
@dataclass(slots=True)
class CrawlRuntimeConcurrency:
    profile_enrichment_concurrency: int
    host_concurrency: int


def resolve_crawl_runtime_concurrency(settings) -> CrawlRuntimeConcurrency:
    return CrawlRuntimeConcurrency(
        profile_enrichment_concurrency=max(1, settings.crawler_profile_enrichment_concurrency),
        host_concurrency=max(1, settings.crawler_host_concurrency),
    )
```

在运行单个 job 时读取 `get_runtime_settings(session)`，把值传到现有 enrichment 调用。

- [ ] **步骤 6：运行 worker 测试通过**

运行：

```bash
rtk powershell -NoProfile -Command "uv run python -m unittest test/test_runtime_manager.py test/test_crawl_job_runtime.py"
```

预期：OK。

- [ ] **步骤 7：Commit**

```bash
git add backend/app/services/runtime_manager.py backend/app/services/crawl_job_runtime.py backend/test/test_runtime_manager.py backend/test/test_crawl_job_runtime.py
git commit -m "feat(settings): apply runtime concurrency settings"
```

---

### 任务 4：前端 API 与其他设置卡片

**文件：**
- 创建：`frontend/src/lib/api/runtimeSettings.ts`
- 创建：`frontend/src/components/molecules/OtherSettingsCard.tsx`
- 修改：`frontend/src/pages/ProfilePage.tsx`
- 测试：`frontend/test/OtherSettingsCard.test.tsx`

- [ ] **步骤 1：编写失败的前端测试**

创建 `frontend/test/OtherSettingsCard.test.tsx`：

```tsx
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { vi } from "vitest";
import { OtherSettingsCard } from "@/components/molecules/OtherSettingsCard";

vi.mock("@/lib/api/runtimeSettings", () => ({
  getRuntimeSettings: vi.fn(async () => ({
    match_analysis_job_worker_count: 1,
    match_analysis_job_item_concurrency: 3,
    match_analysis_job_interval_seconds: 10,
    crawler_worker_count: 2,
    crawler_profile_enrichment_concurrency: 3,
    crawler_host_concurrency: 1,
    updated_at: "2026-05-04T00:00:00Z",
  })),
  updateRuntimeSettings: vi.fn(async (payload) => ({
    ...payload,
    updated_at: "2026-05-04T00:00:01Z",
  })),
}));

describe("OtherSettingsCard", () => {
  it("loads and saves runtime concurrency settings", async () => {
    render(<OtherSettingsCard />);
    fireEvent.click(screen.getByRole("button", { name: /其他设置/ }));
    expect(await screen.findByLabelText("批量匹配分析并发数")).toHaveValue(3);
    fireEvent.change(screen.getByLabelText("批量匹配分析并发数"), { target: { value: "4" } });
    fireEvent.click(screen.getByRole("button", { name: "保存设置" }));
    await waitFor(() => expect(screen.getByText("设置已保存")).toBeInTheDocument());
  });
});
```

如果项目 setup 没有 jest-dom matcher，把最后一行改成：

```tsx
await screen.findByText("设置已保存");
```

- [ ] **步骤 2：运行测试验证失败**

运行：

```bash
rtk powershell -NoProfile -Command "npm run test -- OtherSettingsCard"
```

预期：FAIL，组件/API 文件不存在。

- [ ] **步骤 3：实现 API client**

创建 `frontend/src/lib/api/runtimeSettings.ts`：

```ts
import { apiFetch } from "@/lib/api/client";

export interface RuntimeSettingsDTO {
  match_analysis_job_worker_count: number;
  match_analysis_job_item_concurrency: number;
  match_analysis_job_interval_seconds: number;
  crawler_worker_count: number;
  crawler_profile_enrichment_concurrency: number;
  crawler_host_concurrency: number;
  updated_at: string;
}

export type RuntimeSettingsUpdateDTO = Omit<RuntimeSettingsDTO, "updated_at">;

export const getRuntimeSettings = () =>
  apiFetch<RuntimeSettingsDTO>("/api/runtime-settings");

export const updateRuntimeSettings = (payload: RuntimeSettingsUpdateDTO) =>
  apiFetch<RuntimeSettingsDTO>("/api/runtime-settings", {
    method: "PATCH",
    body: JSON.stringify(payload),
  });
```

- [ ] **步骤 4：实现 OtherSettingsCard**

创建 `frontend/src/components/molecules/OtherSettingsCard.tsx`，遵循 `DiagnosticLogPanel` 的折叠卡片模式。字段使用 `input type="number"`，按钮使用 lucide `Settings`, `Save`, `Loader2`。表单 state 全部保存为字符串，提交时转 number。

关键字段配置：

```ts
const fields = [
  {
    key: "match_analysis_job_item_concurrency",
    label: "批量匹配分析并发数",
    hint: "单个批量匹配任务内同时分析的导师数，保存后下一轮后台任务生效。",
    min: 1,
    max: 20,
  },
  {
    key: "match_analysis_job_worker_count",
    label: "批量匹配 Worker 数",
    hint: "同时处理的批量匹配任务数量，后端重启后生效。",
    min: 1,
    max: 8,
  },
  {
    key: "crawler_worker_count",
    label: "智能抓取任务并发数",
    hint: "同时运行的抓取任务数量，后端重启后生效。",
    min: 1,
    max: 8,
  },
  {
    key: "crawler_profile_enrichment_concurrency",
    label: "详情页补全并发数",
    hint: "单个抓取任务内同时补全的详情页数量，保存后下一轮抓取生效。",
    min: 1,
    max: 20,
  },
  {
    key: "crawler_host_concurrency",
    label: "同站点抓取并发数",
    hint: "同一域名同时抓取的详情页数量，建议保持 1。",
    min: 1,
    max: 8,
  },
];
```

卡片中不要写教程式大段说明，只在每个字段下方用短 hint。

- [ ] **步骤 5：插入 ProfilePage**

在 `frontend/src/pages/ProfilePage.tsx` 顶部导入：

```ts
import { OtherSettingsCard } from "@/components/molecules/OtherSettingsCard";
```

在 JSX 中改成：

```tsx
<TokenUsageCenterCard />

<OtherSettingsCard />

<DiagnosticLogPanel />
```

- [ ] **步骤 6：运行前端测试通过**

运行：

```bash
rtk powershell -NoProfile -Command "npm run test -- OtherSettingsCard"
```

预期：OK。

- [ ] **步骤 7：Commit**

```bash
git add frontend/src/lib/api/runtimeSettings.ts frontend/src/components/molecules/OtherSettingsCard.tsx frontend/src/pages/ProfilePage.tsx frontend/test/OtherSettingsCard.test.tsx
git commit -m "feat(frontend): add other settings card"
```

---

### 任务 5：端到端验证与收尾

**文件：**
- 修改：无，除非测试暴露缺口。

- [ ] **步骤 1：运行后端聚合测试**

运行：

```bash
rtk powershell -NoProfile -Command "uv run python -m unittest test/test_runtime_settings_api.py test/test_runtime_manager.py test/test_crawl_job_runtime.py test/test_operation_log_integration.py test/test_diagnostics_api.py"
```

预期：OK。

- [ ] **步骤 2：运行前端 lint**

运行：

```bash
rtk powershell -NoProfile -Command "npm run lint"
```

工作目录：`frontend`

预期：OK。

- [ ] **步骤 3：运行前端测试**

运行：

```bash
rtk powershell -NoProfile -Command "npm run test -- OtherSettingsCard"
```

工作目录：`frontend`

预期：OK。

- [ ] **步骤 4：手动验证 UI**

启动前端和后端：

```bash
rtk powershell -NoProfile -Command "uv run uvicorn main:app --reload"
```

工作目录：`backend`

```bash
rtk powershell -NoProfile -Command "npm run dev"
```

工作目录：`frontend`

打开个人中心，确认顺序为 `Token 消耗记录中心`、`其他设置`、`开发诊断日志`。展开 `其他设置`，修改批量匹配分析并发数为 4，保存后刷新页面，仍显示 4。

- [ ] **步骤 5：Commit 验证修正**

如果步骤 1-4 有测试修正：

```bash
git add <changed-files>
git commit -m "test(settings): verify runtime settings flow"
```

如果没有修正，不创建空 commit。

---

## 自检

- 规格覆盖度：计划覆盖个人中心新增卡片、后端持久化、读写 API、运行时设置应用、诊断日志、测试验证。
- 占位符扫描：计划中唯一 `<revision>` 和 `<changed-files>` 是执行时必须替换的具体文件名/变更集合，不是业务占位；执行者必须在对应步骤中替换为实际值。
- 类型一致性：后端字段统一使用 snake_case；前端 DTO 与 API 字段保持 snake_case；组件表单 key 与 DTO key 一致。
- 范围控制：不恢复已移除的 `/api/system-settings`；不把所有 env 配置都暴露到 UI；worker 数标注为重启后生效，避免承诺当前 RuntimeManager 不支持的动态增减 worker。
