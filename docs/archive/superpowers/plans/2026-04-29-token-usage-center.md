# Token 消耗记录中心实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 在个人中心最底部新增默认收起的 Token 消耗记录中心，展示全局最近功能级 token 消耗。

**架构：** 后端新增 token usage 聚合 API，从现有 `crawl_job_runs`、`match_analysis_runs`、`email_logs.provider_payload.usage` 读取功能级记录并统一 DTO。前端新增独立 API client、格式化工具和卡片组件，`ProfilePage` 只负责挂载。

**技术栈：** FastAPI、SQLAlchemy async、Pydantic、unittest、React 19、TypeScript、Vitest、Tailwind CSS。

---

## 文件结构

- 创建：`backend/app/schemas/token_usage.py`
  - 定义 `TokenUsageRecordRead` 和 `TokenUsageRecordListRead`。
- 创建：`backend/app/services/token_usage_records.py`
  - 负责查询三类已有记录、映射状态、合并排序、计算 summary。
- 创建：`backend/app/api/token_usage.py`
  - 暴露 `GET /api/token-usage/records`。
- 修改：`backend/app/api/__init__.py`
  - 导出 `token_usage_router`。
- 修改：`backend/main.py`
  - 注册 `token_usage_router`。
- 创建：`backend/test/test_token_usage_records.py`
  - 覆盖聚合 service 和 API。
- 修改：`frontend/src/types/index.ts`
  - 新增 token usage DTO 类型。
- 创建：`frontend/src/lib/api/tokenUsage.ts`
  - 调用 `/api/token-usage/records`。
- 创建：`frontend/src/features/token-usage/client/tokenUsage.ts`
  - 格式化 token、时间、状态和功能标签。
- 创建：`frontend/src/features/token-usage/client/tokenUsage.test.ts`
  - 覆盖格式化和 nullable 字段。
- 创建：`frontend/src/components/molecules/TokenUsageCenterCard.tsx`
  - 独立默认收起卡片，展开时加载最近 20 条记录。
- 修改：`frontend/src/pages/ProfilePage.tsx`
  - 在 `DiagnosticLogPanel` 后挂载 `TokenUsageCenterCard`。

---

### 任务 1：后端 service 红灯测试

**文件：**
- 创建：`backend/test/test_token_usage_records.py`
- 读取参考：`backend/test/test_match_analysis_runtime.py`
- 读取参考：`backend/test/test_crawl_job_metrics.py`

- [ ] **步骤 1：编写失败的 service 测试**

创建 `backend/test/test_token_usage_records.py`，先写聚合 service 的期望行为。测试应直接使用临时 SQLite 和 `Base.metadata.create_all`，避免依赖真实数据。

```python
from __future__ import annotations

import asyncio
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models import (
    Base,
    CrawlJob,
    CrawlJobRun,
    CrawlJobStatus,
    EmailDirection,
    EmailLog,
    EmailTask,
    IdentityProfile,
    LLMProfile,
    MatchAnalysisRun,
    Professor,
)
from app.services.token_usage_records import list_token_usage_records


class TokenUsageRecordsServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "token_usage_records_test.db"
        self.engine = create_async_engine(
            f"sqlite+aiosqlite:///{self.db_path.as_posix()}",
            future=True,
        )
        self.session_factory = async_sessionmaker(
            bind=self.engine,
            autoflush=False,
            expire_on_commit=False,
        )
        self._run_async(self._create_schema())

    def tearDown(self) -> None:
        self._run_async(self.engine.dispose())
        self.temp_dir.cleanup()

    def _run_async(self, awaitable):
        return asyncio.run(awaitable)

    async def _create_schema(self) -> None:
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)

    async def _seed_records(self) -> None:
        now = datetime(2026, 4, 29, 10, 0, 0, tzinfo=UTC)
        async with self.session_factory() as session:
            identity = IdentityProfile(
                name="博士申请邮箱",
                profile_name="博士申请邮箱",
                sender_name="王同学",
                email_address="sender@example.com",
                smtp_host="smtp.example.com",
                smtp_port=465,
                smtp_username="sender@example.com",
                smtp_password="secret",
                default_language="zh-CN",
                outreach_generation_mode="llm",
            )
            llm_profile = LLMProfile(
                name="OpenAI",
                provider="openai",
                api_key="test-key",
                model_name="gpt-test",
            )
            professor = Professor(
                name="李老师",
                email="li@example.edu",
                university="示例大学",
                school="计算机学院",
                research_direction="信息抽取",
            )
            session.add_all([identity, llm_profile, professor])
            await session.flush()

            task = EmailTask(
                identity_id=identity.id,
                llm_profile_id=llm_profile.id,
                professor_id=professor.id,
                selected_material_ids=[],
            )
            session.add(task)
            await session.flush()

            crawl_job = CrawlJob(
                university="示例大学",
                school="计算机学院",
                start_url="https://example.edu/faculty",
                status=CrawlJobStatus.RUNNING.value,
                progress_current=1,
                progress_total=3,
                llm_profile_id=llm_profile.id,
                created_at=now - timedelta(minutes=4),
                updated_at=now - timedelta(minutes=4),
            )
            session.add(crawl_job)
            await session.flush()
            session.add(
                CrawlJobRun(
                    job_id=crawl_job.id,
                    attempt_number=1,
                    status=CrawlJobStatus.RUNNING.value,
                    input_tokens=100,
                    output_tokens=20,
                    total_tokens=120,
                    created_at=now - timedelta(minutes=4),
                    updated_at=now - timedelta(minutes=1),
                )
            )

            session.add(
                MatchAnalysisRun(
                    email_task_id=task.id,
                    professor_id=professor.id,
                    identity_id=identity.id,
                    llm_profile_id=llm_profile.id,
                    success=True,
                    match_score=91,
                    prompt_tokens=200,
                    completion_tokens=30,
                    cached_tokens=80,
                    total_tokens=230,
                    created_at=now - timedelta(minutes=2),
                )
            )

            session.add(
                EmailLog(
                    email_task_id=task.id,
                    identity_id=identity.id,
                    llm_profile_id=llm_profile.id,
                    professor_id=professor.id,
                    direction=EmailDirection.DRAFT.value,
                    subject="申请交流",
                    content="李老师您好",
                    provider_payload={
                        "source": "llm",
                        "usage": {
                            "prompt_tokens": 300,
                            "completion_tokens": 40,
                            "total_tokens": 340,
                        },
                    },
                    created_at=now - timedelta(minutes=3),
                )
            )

            session.add(
                EmailLog(
                    email_task_id=task.id,
                    identity_id=identity.id,
                    llm_profile_id=llm_profile.id,
                    professor_id=professor.id,
                    direction=EmailDirection.DRAFT.value,
                    subject="模板草稿",
                    content="模板正文",
                    provider_payload={"source": "template", "usage": None},
                    created_at=now,
                )
            )

            await session.commit()

    def test_lists_recent_function_level_token_records(self) -> None:
        self._run_async(self._seed_records())

        async def run_query():
            async with self.session_factory() as session:
                return await list_token_usage_records(session, limit=20)

        result = self._run_async(run_query())

        self.assertEqual([item.feature_type for item in result.records], [
            "match_analysis",
            "draft_generation",
            "crawl",
        ])
        self.assertEqual(result.records[0].input_tokens, 200)
        self.assertEqual(result.records[0].output_tokens, 30)
        self.assertEqual(result.records[0].cached_tokens, 80)
        self.assertEqual(result.records[0].status, "success")
        self.assertEqual(result.records[1].total_tokens, 340)
        self.assertEqual(result.records[2].status, "running")
        self.assertEqual(result.summary.record_count, 3)
        self.assertEqual(result.summary.input_tokens, 600)
        self.assertEqual(result.summary.output_tokens, 90)
        self.assertEqual(result.summary.cached_tokens, 80)
        self.assertEqual(result.summary.total_tokens, 690)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **步骤 2：运行测试验证失败**

运行：

```bash
cd backend && uv run python -m unittest test.test_token_usage_records.TokenUsageRecordsServiceTests.test_lists_recent_function_level_token_records
```

预期：FAIL 或 ERROR，报错包含 `No module named 'app.services.token_usage_records'`。

- [ ] **步骤 3：提交红灯测试**

```bash
git add backend/test/test_token_usage_records.py
git commit -m "test(token): 覆盖消耗记录聚合服务"
```

---

### 任务 2：后端 schema 和聚合 service

**文件：**
- 创建：`backend/app/schemas/token_usage.py`
- 创建：`backend/app/services/token_usage_records.py`
- 测试：`backend/test/test_token_usage_records.py`

- [ ] **步骤 1：新增响应 schema**

创建 `backend/app/schemas/token_usage.py`。

```python
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


TokenUsageFeatureType = Literal["crawl", "match_analysis", "draft_generation"]
TokenUsageStatus = Literal["success", "failed", "running", "unknown"]


class TokenUsageRecordRead(BaseModel):
    id: str
    feature_type: TokenUsageFeatureType
    feature_label: str
    title: str
    input_tokens: int | None = None
    output_tokens: int | None = None
    cached_tokens: int | None = None
    total_tokens: int | None = None
    model_name: str | None = None
    identity_name: str | None = None
    created_at: datetime
    status: TokenUsageStatus


class TokenUsageSummaryRead(BaseModel):
    input_tokens: int = 0
    output_tokens: int = 0
    cached_tokens: int = 0
    total_tokens: int = 0
    record_count: int = 0


class TokenUsageRecordListRead(BaseModel):
    records: list[TokenUsageRecordRead] = Field(default_factory=list)
    summary: TokenUsageSummaryRead
```

- [ ] **步骤 2：实现聚合 service**

创建 `backend/app/services/token_usage_records.py`。

```python
from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models import (
    CrawlJob,
    CrawlJobRun,
    CrawlJobStatus,
    EmailDirection,
    EmailLog,
    MatchAnalysisRun,
)
from app.schemas.token_usage import (
    TokenUsageRecordListRead,
    TokenUsageRecordRead,
    TokenUsageSummaryRead,
)


async def list_token_usage_records(
    session: AsyncSession,
    *,
    limit: int = 20,
) -> TokenUsageRecordListRead:
    resolved_limit = min(max(limit, 1), 100)
    candidates: list[TokenUsageRecordRead] = []
    candidates.extend(await _list_crawl_records(session, limit=resolved_limit))
    candidates.extend(await _list_match_records(session, limit=resolved_limit))
    candidates.extend(await _list_draft_records(session, limit=resolved_limit))

    records = sorted(candidates, key=lambda item: item.created_at, reverse=True)[
        :resolved_limit
    ]
    return TokenUsageRecordListRead(
        records=records,
        summary=_build_summary(records),
    )


async def _list_crawl_records(
    session: AsyncSession,
    *,
    limit: int,
) -> list[TokenUsageRecordRead]:
    runs = list(
        await session.scalars(
            select(CrawlJobRun)
            .options(
                selectinload(CrawlJobRun.job).selectinload(CrawlJob.llm_profile),
            )
            .order_by(CrawlJobRun.updated_at.desc())
            .limit(limit)
        )
    )
    return [_crawl_run_to_record(run) for run in runs]


async def _list_match_records(
    session: AsyncSession,
    *,
    limit: int,
) -> list[TokenUsageRecordRead]:
    runs = list(
        await session.scalars(
            select(MatchAnalysisRun)
            .options(
                selectinload(MatchAnalysisRun.professor),
                selectinload(MatchAnalysisRun.identity),
                selectinload(MatchAnalysisRun.llm_profile),
            )
            .order_by(MatchAnalysisRun.created_at.desc())
            .limit(limit)
        )
    )
    return [_match_run_to_record(run) for run in runs]


async def _list_draft_records(
    session: AsyncSession,
    *,
    limit: int,
) -> list[TokenUsageRecordRead]:
    logs = list(
        await session.scalars(
            select(EmailLog)
            .options(
                selectinload(EmailLog.professor),
                selectinload(EmailLog.identity),
                selectinload(EmailLog.llm_profile),
            )
            .where(EmailLog.direction == EmailDirection.DRAFT.value)
            .order_by(EmailLog.created_at.desc())
            .limit(limit * 3)
        )
    )
    records = [
        record
        for log in logs
        for record in [_draft_log_to_record(log)]
        if record is not None
    ]
    return records[:limit]


def _crawl_run_to_record(run: CrawlJobRun) -> TokenUsageRecordRead:
    job = run.job
    title_context = None
    if job is not None:
        title_context = job.school or job.university or job.start_url
    return TokenUsageRecordRead(
        id=f"crawl:{run.id}",
        feature_type="crawl",
        feature_label="智能爬取",
        title=f"智能爬取 - {title_context or '未命名任务'}",
        input_tokens=run.input_tokens,
        output_tokens=run.output_tokens,
        cached_tokens=None,
        total_tokens=run.total_tokens,
        model_name=job.llm_profile.model_name if job and job.llm_profile else None,
        identity_name=None,
        created_at=run.updated_at or run.created_at,
        status=_map_crawl_status(run.status),
    )


def _match_run_to_record(run: MatchAnalysisRun) -> TokenUsageRecordRead:
    professor_name = run.professor.name if run.professor else "未关联导师"
    return TokenUsageRecordRead(
        id=f"match_analysis:{run.id}",
        feature_type="match_analysis",
        feature_label="匹配分析",
        title=f"{professor_name} - 匹配分析",
        input_tokens=run.prompt_tokens,
        output_tokens=run.completion_tokens,
        cached_tokens=run.cached_tokens,
        total_tokens=run.total_tokens,
        model_name=run.llm_profile.model_name if run.llm_profile else None,
        identity_name=_identity_name(run.identity),
        created_at=run.created_at,
        status="success" if run.success else "failed",
    )


def _draft_log_to_record(log: EmailLog) -> TokenUsageRecordRead | None:
    usage = _extract_usage(log.provider_payload)
    if usage is None:
        return None
    professor_name = log.professor.name if log.professor else "未关联导师"
    return TokenUsageRecordRead(
        id=f"draft_generation:{log.id}",
        feature_type="draft_generation",
        feature_label="AI 草稿",
        title=f"{professor_name} - AI 草稿",
        input_tokens=usage.get("prompt_tokens"),
        output_tokens=usage.get("completion_tokens"),
        cached_tokens=None,
        total_tokens=usage.get("total_tokens"),
        model_name=log.llm_profile.model_name if log.llm_profile else None,
        identity_name=_identity_name(log.identity),
        created_at=log.created_at,
        status="success",
    )


def _extract_usage(payload: dict[str, object] | None) -> dict[str, int | None] | None:
    if not payload:
        return None
    raw_usage = payload.get("usage")
    if not isinstance(raw_usage, dict):
        return None
    return {
        "prompt_tokens": _int_or_none(raw_usage.get("prompt_tokens")),
        "completion_tokens": _int_or_none(raw_usage.get("completion_tokens")),
        "total_tokens": _int_or_none(raw_usage.get("total_tokens")),
    }


def _int_or_none(value: Any) -> int | None:
    return value if isinstance(value, int) else None


def _identity_name(identity: object) -> str | None:
    profile_name = getattr(identity, "profile_name", None)
    name = getattr(identity, "name", None)
    if isinstance(profile_name, str) and profile_name.strip():
        return profile_name
    return name if isinstance(name, str) and name.strip() else None


def _map_crawl_status(status: str) -> str:
    if status in {
        CrawlJobStatus.QUEUED.value,
        CrawlJobStatus.RUNNING.value,
        CrawlJobStatus.PAUSED.value,
    }:
        return "running"
    if status in {
        CrawlJobStatus.NEEDS_REVIEW.value,
        CrawlJobStatus.COMPLETED.value,
    }:
        return "success"
    if status in {
        CrawlJobStatus.FAILED.value,
        CrawlJobStatus.CANCELED.value,
    }:
        return "failed"
    return "unknown"


def _build_summary(records: list[TokenUsageRecordRead]) -> TokenUsageSummaryRead:
    return TokenUsageSummaryRead(
        input_tokens=sum(item.input_tokens or 0 for item in records),
        output_tokens=sum(item.output_tokens or 0 for item in records),
        cached_tokens=sum(item.cached_tokens or 0 for item in records),
        total_tokens=sum(item.total_tokens or 0 for item in records),
        record_count=len(records),
    )
```

- [ ] **步骤 3：运行 service 测试验证通过**

运行：

```bash
cd backend && uv run python -m unittest test.test_token_usage_records.TokenUsageRecordsServiceTests.test_lists_recent_function_level_token_records
```

预期：PASS。

- [ ] **步骤 4：Commit**

```bash
git add backend/app/schemas/token_usage.py backend/app/services/token_usage_records.py backend/test/test_token_usage_records.py
git commit -m "feat(token): 聚合功能级消耗记录"
```

---

### 任务 3：后端 API 路由

**文件：**
- 修改：`backend/test/test_token_usage_records.py`
- 创建：`backend/app/api/token_usage.py`
- 修改：`backend/app/api/__init__.py`
- 修改：`backend/main.py`

- [ ] **步骤 1：编写失败的 API 测试**

在 `TokenUsageRecordsServiceTests` 中追加 API 测试。为了少建一个测试类，复用已有临时数据库和 `create_app()`。

```python
    def test_api_returns_token_usage_records(self) -> None:
        self._run_async(self._seed_records())

        from fastapi.testclient import TestClient
        from main import create_app

        client = TestClient(create_app())
        try:
            response = client.get("/api/token-usage/records?limit=2")
        finally:
            client.close()

        self.assertEqual(response.status_code, 200, msg=response.text)
        payload = response.json()
        self.assertEqual(len(payload["records"]), 2)
        self.assertEqual(payload["records"][0]["feature_type"], "match_analysis")
        self.assertEqual(payload["summary"]["record_count"], 2)
```

- [ ] **步骤 2：运行 API 测试验证失败**

运行：

```bash
cd backend && uv run python -m unittest test.test_token_usage_records.TokenUsageRecordsServiceTests.test_api_returns_token_usage_records
```

预期：FAIL，状态码为 404。

- [ ] **步骤 3：新增 FastAPI 路由**

创建 `backend/app/api/token_usage.py`。

```python
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_async_session
from app.schemas.token_usage import TokenUsageRecordListRead
from app.services.token_usage_records import list_token_usage_records


router = APIRouter(prefix="/api/token-usage", tags=["token-usage"])


@router.get("/records", response_model=TokenUsageRecordListRead)
async def list_records(
    limit: int = Query(default=20, ge=1, le=100),
    session: AsyncSession = Depends(get_async_session),
) -> TokenUsageRecordListRead:
    return await list_token_usage_records(session, limit=limit)
```

- [ ] **步骤 4：导出并注册路由**

修改 `backend/app/api/__init__.py`：

```python
from app.api.token_usage import router as token_usage_router
```

并在 `__all__` 中加入：

```python
"token_usage_router",
```

修改 `backend/main.py` 的导入列表，加入：

```python
token_usage_router,
```

在 `create_app()` 中注册：

```python
app.include_router(token_usage_router)
```

- [ ] **步骤 5：运行 API 测试验证通过**

运行：

```bash
cd backend && uv run python -m unittest test.test_token_usage_records.TokenUsageRecordsServiceTests.test_api_returns_token_usage_records
```

预期：PASS。

- [ ] **步骤 6：运行后端新增测试文件**

运行：

```bash
cd backend && uv run python -m unittest test.test_token_usage_records
```

预期：PASS。

- [ ] **步骤 7：Commit**

```bash
git add backend/app/api/token_usage.py backend/app/api/__init__.py backend/main.py backend/test/test_token_usage_records.py
git commit -m "feat(token): 添加消耗记录查询接口"
```

---

### 任务 4：前端类型、API client 和格式化工具

**文件：**
- 修改：`frontend/src/types/index.ts`
- 创建：`frontend/src/lib/api/tokenUsage.ts`
- 创建：`frontend/src/features/token-usage/client/tokenUsage.ts`
- 创建：`frontend/src/features/token-usage/client/tokenUsage.test.ts`

- [ ] **步骤 1：编写失败的前端工具测试**

创建 `frontend/src/features/token-usage/client/tokenUsage.test.ts`。

```typescript
import { describe, expect, it } from 'vitest';
import {
  formatTokenRecordStatus,
  formatTokenValue,
  getTokenRecordFeatureTone,
} from './tokenUsage';

describe('token usage center helpers', () => {
  it('formats missing token fields as not returned', () => {
    expect(formatTokenValue(null)).toBe('未返回');
    expect(formatTokenValue(1200)).toBe('1,200');
  });

  it('formats record status labels', () => {
    expect(formatTokenRecordStatus('success')).toBe('成功');
    expect(formatTokenRecordStatus('failed')).toBe('失败');
    expect(formatTokenRecordStatus('running')).toBe('进行中');
    expect(formatTokenRecordStatus('unknown')).toBe('未知');
  });

  it('returns stable visual tones for feature types', () => {
    expect(getTokenRecordFeatureTone('crawl')).toBe('amber');
    expect(getTokenRecordFeatureTone('match_analysis')).toBe('emerald');
    expect(getTokenRecordFeatureTone('draft_generation')).toBe('sky');
  });
});
```

- [ ] **步骤 2：运行测试验证失败**

运行：

```bash
cd frontend && npm run test -- src/features/token-usage/client/tokenUsage.test.ts
```

预期：FAIL，报错找不到 `./tokenUsage`。

- [ ] **步骤 3：新增 DTO 类型**

在 `frontend/src/types/index.ts` 末尾附近新增：

```typescript
export type TokenUsageRecordFeatureTypeDTO = 'crawl' | 'match_analysis' | 'draft_generation';
export type TokenUsageRecordStatusDTO = 'success' | 'failed' | 'running' | 'unknown';

export interface TokenUsageRecordDTO {
  id: string;
  feature_type: TokenUsageRecordFeatureTypeDTO;
  feature_label: string;
  title: string;
  input_tokens: number | null;
  output_tokens: number | null;
  cached_tokens: number | null;
  total_tokens: number | null;
  model_name: string | null;
  identity_name: string | null;
  created_at: string;
  status: TokenUsageRecordStatusDTO;
}

export interface TokenUsageSummaryDTO {
  input_tokens: number;
  output_tokens: number;
  cached_tokens: number;
  total_tokens: number;
  record_count: number;
}

export interface TokenUsageRecordListDTO {
  records: TokenUsageRecordDTO[];
  summary: TokenUsageSummaryDTO;
}
```

- [ ] **步骤 4：新增 API client**

创建 `frontend/src/lib/api/tokenUsage.ts`。

```typescript
import { apiFetch } from '@/lib/api/client';
import type { TokenUsageRecordListDTO } from '@/types';

export const listTokenUsageRecords = (limit = 20) =>
  apiFetch<TokenUsageRecordListDTO>('/api/token-usage/records', undefined, {
    limit,
  });
```

- [ ] **步骤 5：新增格式化工具**

创建 `frontend/src/features/token-usage/client/tokenUsage.ts`。

```typescript
import type {
  TokenUsageRecordFeatureTypeDTO,
  TokenUsageRecordStatusDTO,
} from '@/types';

export type TokenRecordFeatureTone = 'amber' | 'emerald' | 'sky' | 'stone';

export const formatTokenValue = (value: number | null): string =>
  value === null ? '未返回' : value.toLocaleString('zh-CN');

export const formatTokenRecordStatus = (
  status: TokenUsageRecordStatusDTO,
): string => {
  const labels: Record<TokenUsageRecordStatusDTO, string> = {
    success: '成功',
    failed: '失败',
    running: '进行中',
    unknown: '未知',
  };
  return labels[status];
};

export const getTokenRecordFeatureTone = (
  featureType: TokenUsageRecordFeatureTypeDTO,
): TokenRecordFeatureTone => {
  const tones: Record<TokenUsageRecordFeatureTypeDTO, TokenRecordFeatureTone> = {
    crawl: 'amber',
    match_analysis: 'emerald',
    draft_generation: 'sky',
  };
  return tones[featureType] ?? 'stone';
};
```

- [ ] **步骤 6：运行前端工具测试验证通过**

运行：

```bash
cd frontend && npm run test -- src/features/token-usage/client/tokenUsage.test.ts
```

预期：PASS。

- [ ] **步骤 7：Commit**

```bash
git add frontend/src/types/index.ts frontend/src/lib/api/tokenUsage.ts frontend/src/features/token-usage/client/tokenUsage.ts frontend/src/features/token-usage/client/tokenUsage.test.ts
git commit -m "feat(token): 添加前端消耗记录工具"
```

---

### 任务 5：个人中心卡片组件

**文件：**
- 创建：`frontend/src/components/molecules/TokenUsageCenterCard.tsx`
- 修改：`frontend/src/pages/ProfilePage.tsx`

- [ ] **步骤 1：创建卡片组件**

创建 `frontend/src/components/molecules/TokenUsageCenterCard.tsx`。

```tsx
import { useEffect, useState } from 'react';
import clsx from 'clsx';
import { ChevronDown, Loader2, RefreshCw } from 'lucide-react';
import { listTokenUsageRecords } from '@/lib/api/tokenUsage';
import type { TokenUsageRecordDTO, TokenUsageRecordListDTO } from '@/types';
import {
  formatTokenRecordStatus,
  formatTokenValue,
  getTokenRecordFeatureTone,
} from '@/features/token-usage/client/tokenUsage';

const emptyResult: TokenUsageRecordListDTO = {
  records: [],
  summary: {
    input_tokens: 0,
    output_tokens: 0,
    cached_tokens: 0,
    total_tokens: 0,
    record_count: 0,
  },
};

export function TokenUsageCenterCard() {
  const [open, setOpen] = useState(false);
  const [result, setResult] = useState<TokenUsageRecordListDTO>(emptyResult);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const loadRecords = async () => {
    setLoading(true);
    setError(null);
    try {
      setResult(await listTokenUsageRecords(20));
    } catch (loadError) {
      setError(loadError instanceof Error ? loadError.message : '加载 token 消耗记录失败');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    if (!open || loading || result.records.length > 0 || error) {
      return;
    }
    void loadRecords();
  }, [open]);

  return (
    <section className="overflow-hidden rounded-2xl border border-stone-200 bg-white shadow-sm">
      <button
        type="button"
        aria-expanded={open}
        aria-controls="token-usage-center-content"
        onClick={() => setOpen((previous) => !previous)}
        className="collapsible-card-toggle flex w-full items-center justify-between gap-4 px-6 py-5 text-left transition hover:bg-stone-50 active:bg-stone-50"
      >
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <h2 className="text-xl font-semibold text-stone-900">
              Token 消耗记录中心
            </h2>
            <span className="rounded-full border border-stone-200 bg-stone-50 px-3 py-1.5 text-xs text-stone-600">
              最近 {result.summary.record_count} 条
            </span>
          </div>
          <p className="mt-2 text-sm leading-6 text-stone-600">
            汇总智能爬取、匹配分析和 AI 草稿的功能级消耗。
          </p>
        </div>
        <ChevronDown
          className={clsx(
            'h-5 w-5 shrink-0 text-stone-500 transition-transform',
            open ? 'rotate-180' : 'rotate-0',
          )}
        />
      </button>

      {open ? (
        <div id="token-usage-center-content" className="px-6 pb-6">
          {loading ? (
            <div className="flex items-center justify-center gap-2 rounded-2xl border border-stone-200 bg-stone-50 px-4 py-8 text-sm text-stone-500">
              <Loader2 className="h-4 w-4 animate-spin" />
              正在加载 token 消耗记录...
            </div>
          ) : error ? (
            <div className="rounded-2xl border border-red-200 bg-red-50 px-4 py-4 text-sm text-red-700">
              <div>{error}</div>
              <button
                type="button"
                onClick={() => void loadRecords()}
                className="mt-3 inline-flex items-center gap-2 rounded-xl border border-red-200 bg-white px-3 py-2 text-xs font-medium text-red-700 transition hover:bg-red-50"
              >
                <RefreshCw className="h-3.5 w-3.5" />
                重试
              </button>
            </div>
          ) : result.records.length === 0 ? (
            <div className="rounded-2xl border border-stone-200 bg-stone-50 px-4 py-8 text-center text-sm text-stone-500">
              暂无 token 消耗记录
            </div>
          ) : (
            <div className="space-y-4">
              <TokenUsageSummaryGrid result={result} />
              <div className="overflow-hidden rounded-2xl border border-stone-200">
                {result.records.map((record) => (
                  <TokenUsageRecordRow key={record.id} record={record} />
                ))}
              </div>
            </div>
          )}
        </div>
      ) : null}
    </section>
  );
}

function TokenUsageSummaryGrid({ result }: { result: TokenUsageRecordListDTO }) {
  const items = [
    ['输入', result.summary.input_tokens],
    ['输出', result.summary.output_tokens],
    ['缓存命中', result.summary.cached_tokens],
    ['总计', result.summary.total_tokens],
  ] as const;

  return (
    <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
      {items.map(([label, value]) => (
        <div key={label} className="rounded-2xl border border-stone-200 bg-[#fcfbf8] px-4 py-3">
          <div className="text-xs text-stone-500">{label}</div>
          <div className="mt-1 text-lg font-semibold text-stone-900">
            {value.toLocaleString('zh-CN')}
          </div>
        </div>
      ))}
    </div>
  );
}

function TokenUsageRecordRow({ record }: { record: TokenUsageRecordDTO }) {
  const tone = getTokenRecordFeatureTone(record.feature_type);
  return (
    <article className="border-b border-stone-200 bg-white px-4 py-4 last:border-b-0">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <span
              className={clsx(
                'rounded-full px-2.5 py-1 text-xs font-medium',
                tone === 'amber' && 'bg-amber-100 text-amber-700',
                tone === 'emerald' && 'bg-emerald-100 text-emerald-700',
                tone === 'sky' && 'bg-sky-100 text-sky-700',
                tone === 'stone' && 'bg-stone-100 text-stone-700',
              )}
            >
              {record.feature_label}
            </span>
            <span className="text-xs text-stone-500">
              {formatTokenRecordStatus(record.status)}
            </span>
          </div>
          <h3 className="mt-2 text-sm font-semibold text-stone-900">{record.title}</h3>
          <p className="mt-1 text-xs text-stone-500">
            身份：{record.identity_name ?? '未关联'} · 模型：{record.model_name ?? '未关联'}
          </p>
        </div>
        <time className="text-xs text-stone-400" dateTime={record.created_at}>
          {new Date(record.created_at).toLocaleString('zh-CN')}
        </time>
      </div>
      <div className="mt-3 grid gap-2 text-xs text-stone-600 sm:grid-cols-4">
        <span>输入 {formatTokenValue(record.input_tokens)}</span>
        <span>输出 {formatTokenValue(record.output_tokens)}</span>
        <span>缓存 {formatTokenValue(record.cached_tokens)}</span>
        <span>总计 {formatTokenValue(record.total_tokens)}</span>
      </div>
    </article>
  );
}
```

- [ ] **步骤 2：挂载到个人中心底部**

修改 `frontend/src/pages/ProfilePage.tsx`，增加导入：

```tsx
import { TokenUsageCenterCard } from '@/components/molecules/TokenUsageCenterCard';
```

在 `DiagnosticLogPanel` 后追加卡片：

```tsx
          <DiagnosticLogPanel />

          <TokenUsageCenterCard />
```

- [ ] **步骤 3：运行 TypeScript 构建检查**

运行：

```bash
cd frontend && npm run build
```

预期：PASS。

- [ ] **步骤 4：Commit**

```bash
git add frontend/src/components/molecules/TokenUsageCenterCard.tsx frontend/src/pages/ProfilePage.tsx
git commit -m "feat(token): 在个人中心展示消耗记录"
```

---

### 任务 6：全量验证和收尾

**文件：**
- 检查：全部本次变更文件

- [ ] **步骤 1：运行后端相关测试**

运行：

```bash
cd backend && uv run python -m unittest test.test_token_usage_records
```

预期：PASS。

- [ ] **步骤 2：运行后端完整测试**

运行：

```bash
cd backend && uv run python -m unittest discover test
```

预期：PASS。

- [ ] **步骤 3：运行前端测试**

运行：

```bash
cd frontend && npm run test
```

预期：PASS。

- [ ] **步骤 4：运行前端 lint**

运行：

```bash
cd frontend && npm run lint
```

预期：PASS。

- [ ] **步骤 5：运行前端构建**

运行：

```bash
cd frontend && npm run build
```

预期：PASS。

- [ ] **步骤 6：检查最终 diff**

运行：

```bash
git status --short
git diff --stat
```

预期：

- 只包含本功能相关文件。
- 没有 `.env`、`.venv`、`node_modules`、运行数据或无关格式化文件。

- [ ] **步骤 7：最终 commit**

如果任务 1 到任务 5 已分别提交，且任务 6 没有产生文件变更，则跳过此步骤。若验证中产生必要修正，提交：

```bash
git add backend/app/schemas/token_usage.py backend/app/services/token_usage_records.py backend/app/api/token_usage.py backend/app/api/__init__.py backend/main.py backend/test/test_token_usage_records.py frontend/src/types/index.ts frontend/src/lib/api/tokenUsage.ts frontend/src/features/token-usage/client/tokenUsage.ts frontend/src/features/token-usage/client/tokenUsage.test.ts frontend/src/components/molecules/TokenUsageCenterCard.tsx frontend/src/pages/ProfilePage.tsx
git commit -m "fix(token): 修正消耗记录中心验证问题"
```

---

## 自检清单

- 后端 API 只返回功能级记录，不返回底层 LLM 调用明细。
- `provider_payload.usage = null` 的模板草稿不会进入记录中心。
- `cached_tokens` 缺失时前端显示 `未返回`。
- summary 只汇总当前响应内记录。
- 个人中心卡片默认收起，展开后才请求接口。
- `ProfilePage` 只新增导入和挂载，不承载数据请求细节。
- 所有新增测试都经历红灯和绿灯。
