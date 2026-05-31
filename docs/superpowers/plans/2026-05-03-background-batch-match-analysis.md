# 后台批量匹配分析任务实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 将首页“批量分析匹配度”升级为可离开页面后继续运行、可在任务中心查看进度的后台任务。

**架构：** 新增 `match_analysis_jobs` 与 `match_analysis_job_items` 两张表表达批量任务和明细项，后台 worker 领取 job 后按固定并发执行 item。单项分析复用现有 `calculate_task_match` service 与 `match_analysis_runs` 审计/防重入，不复用发送批次 `batch_tasks`。

**技术栈：** FastAPI、SQLAlchemy async、Alembic、SQLite、unittest、React、Vite、TypeScript、TailwindCSS。

---

## 文件结构

后端新增：

- `backend/app/models/match_analysis_job.py`：定义批量匹配分析 job、item 和状态枚举。
- `backend/app/schemas/match_analysis_job.py`：定义创建、列表、明细、操作响应 DTO。
- `backend/app/api/match_analysis_jobs.py`：提供 job 创建、列表、明细、取消、重试失败 API。
- `backend/app/services/match_analysis_job_runtime.py`：创建 job、领取 job、执行 item、统计 job、取消和重试逻辑。
- `backend/alembic/versions/a9c8e7d6f5b4_add_match_analysis_jobs.py`：新增两张表和索引。
- `backend/test/test_match_analysis_jobs.py`：覆盖后台 job 创建、执行、失败隔离、取消、重试。

后端修改：

- `backend/app/models/__init__.py`：导出新增模型和枚举。
- `backend/app/schemas/__init__.py`：导出新增 schema。
- `backend/app/api/__init__.py`：导出新增 router。
- `backend/main.py`：注册新增 router。
- `backend/app/core/config.py`：新增匹配分析 worker 配置。
- `backend/app/services/runtime_manager.py`：启动匹配分析后台 worker。
- `backend/app/services/task_runtime.py`：保持 `calculate_task_match` 作为后台和即时入口共享的 service，不改变现有单个分析 API 语义。
- `backend/test/test_database_schema.py`：更新 head revision、表和列断言。
- `backend/test/test_api_endpoints.py`：补 API 集成冒烟测试。
- `backend/.env.example`：补充匹配分析 worker 配置说明。

前端新增：

- `frontend/src/lib/api/matchAnalysisJobsApi.ts`：封装新增 API。

前端修改：

- `frontend/src/types/index.ts`：新增匹配分析 job DTO 类型和状态 label。
- `frontend/src/pages/HomePage.tsx`：把批量按钮从页面内并发改为创建后台 job。
- `frontend/src/pages/TasksPage.tsx`：新增匹配分析 tab、列表、详情、取消、重试失败。

验证命令：

- 后端单测：`rtk powershell -NoProfile -Command "cd backend; uv run python -m unittest test.test_match_analysis_jobs test.test_database_schema test.test_api_endpoints"`
- 前端检查：`rtk powershell -NoProfile -Command "cd frontend; npm run lint"`
- 前端构建：`rtk powershell -NoProfile -Command "cd frontend; npm run build"`

## 任务 1：数据库迁移与模型

**文件：**
- 创建：`backend/app/models/match_analysis_job.py`
- 创建：`backend/alembic/versions/a9c8e7d6f5b4_add_match_analysis_jobs.py`
- 修改：`backend/app/models/__init__.py`
- 修改：`backend/test/test_database_schema.py`

- [ ] **步骤 1：编写失败的 schema 测试**

在 `backend/test/test_database_schema.py` 中把 `HEAD_REVISION` 改为新 revision：

```python
HEAD_REVISION = "a9c8e7d6f5b4"
```

在 `test_runtime_tables_and_columns_are_created` 的 table 集合中加入：

```python
"match_analysis_jobs",
"match_analysis_job_items",
```

新增列断言：

```python
match_job_columns = self._get_columns("match_analysis_jobs")
match_job_item_columns = self._get_columns("match_analysis_job_items")

self.assertTrue(
    {
        "id",
        "name",
        "identity_id",
        "llm_profile_id",
        "status",
        "target_count",
        "succeeded_count",
        "failed_count",
        "skipped_count",
        "total_prompt_tokens",
        "total_completion_tokens",
        "total_tokens",
        "cancel_requested_at",
        "started_at",
        "finished_at",
        "created_at",
        "updated_at",
        "last_error",
    }.issubset(match_job_columns),
)
self.assertTrue(
    {
        "id",
        "job_id",
        "professor_id",
        "email_task_id",
        "status",
        "match_analysis_run_id",
        "error_message",
        "skip_reason",
        "prompt_tokens",
        "completion_tokens",
        "total_tokens",
        "started_at",
        "finished_at",
        "created_at",
        "updated_at",
    }.issubset(match_job_item_columns),
)
```

在索引测试中加入：

```python
job_indexes = self.connection.execute("PRAGMA index_list('match_analysis_jobs')").fetchall()
job_item_indexes = self.connection.execute("PRAGMA index_list('match_analysis_job_items')").fetchall()
self.assertTrue(any(row[1] == "ix_match_analysis_jobs_status" for row in job_indexes))
self.assertTrue(any(row[1] == "ix_match_analysis_job_items_job_id" for row in job_item_indexes))
self.assertTrue(any(row[1] == "ix_match_analysis_job_items_status" for row in job_item_indexes))
```

- [ ] **步骤 2：运行测试确认失败**

运行：

```bash
rtk powershell -NoProfile -Command "cd backend; uv run python -m unittest test.test_database_schema"
```

预期：失败，提示 Alembic head revision 不存在或缺少 `match_analysis_jobs` 表。

- [ ] **步骤 3：创建模型**

创建 `backend/app/models/match_analysis_job.py`：

```python
from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base

if TYPE_CHECKING:
    from app.models.email_task import EmailTask
    from app.models.identity_profile import IdentityProfile
    from app.models.llm_profile import LLMProfile
    from app.models.match_analysis_run import MatchAnalysisRun
    from app.models.professor import Professor


class MatchAnalysisJobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    PARTIAL_FAILED = "partial_failed"
    FAILED = "failed"
    CANCELED = "canceled"


class MatchAnalysisJobItemStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    SKIPPED = "skipped"
    CANCELED = "canceled"


class MatchAnalysisJob(Base):
    __tablename__ = "match_analysis_jobs"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    identity_id: Mapped[int] = mapped_column(ForeignKey("identity_profiles.id"), index=True, nullable=False)
    llm_profile_id: Mapped[int] = mapped_column(ForeignKey("llm_profiles.id"), index=True, nullable=False)
    status: Mapped[str] = mapped_column(String(32), index=True, nullable=False, server_default=text("'queued'"))
    target_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    succeeded_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    failed_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    skipped_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    total_prompt_tokens: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    total_completion_tokens: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    total_tokens: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    cancel_requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP"))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP"), onupdate=lambda: datetime.now(UTC))
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)

    identity: Mapped["IdentityProfile"] = relationship()
    llm_profile: Mapped["LLMProfile"] = relationship()
    items: Mapped[list["MatchAnalysisJobItem"]] = relationship(back_populates="job", cascade="all, delete-orphan")


class MatchAnalysisJobItem(Base):
    __tablename__ = "match_analysis_job_items"

    id: Mapped[int] = mapped_column(primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("match_analysis_jobs.id"), index=True, nullable=False)
    professor_id: Mapped[int] = mapped_column(ForeignKey("professors.id"), index=True, nullable=False)
    email_task_id: Mapped[int | None] = mapped_column(ForeignKey("email_tasks.id"), index=True, nullable=True)
    status: Mapped[str] = mapped_column(String(32), index=True, nullable=False, server_default=text("'queued'"))
    match_analysis_run_id: Mapped[int | None] = mapped_column(ForeignKey("match_analysis_runs.id"), index=True, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    skip_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    prompt_tokens: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    completion_tokens: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    total_tokens: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP"))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP"), onupdate=lambda: datetime.now(UTC))

    job: Mapped["MatchAnalysisJob"] = relationship(back_populates="items")
    professor: Mapped["Professor"] = relationship()
    email_task: Mapped["EmailTask | None"] = relationship()
    match_analysis_run: Mapped["MatchAnalysisRun | None"] = relationship()
```

更新 `backend/app/models/__init__.py`：

```python
from app.models.match_analysis_job import (
    MatchAnalysisJob,
    MatchAnalysisJobItem,
    MatchAnalysisJobItemStatus,
    MatchAnalysisJobStatus,
)
```

并把 4 个名字加入 `__all__`。

- [ ] **步骤 4：创建 Alembic 迁移**

创建 `backend/alembic/versions/a9c8e7d6f5b4_add_match_analysis_jobs.py`，`down_revision = "e5f1c2d3a4b6"`，建表字段与模型保持一致。索引至少包含：

```python
op.create_index("ix_match_analysis_jobs_status", "match_analysis_jobs", ["status"])
op.create_index("ix_match_analysis_jobs_identity_id", "match_analysis_jobs", ["identity_id"])
op.create_index("ix_match_analysis_jobs_llm_profile_id", "match_analysis_jobs", ["llm_profile_id"])
op.create_index("ix_match_analysis_job_items_job_id", "match_analysis_job_items", ["job_id"])
op.create_index("ix_match_analysis_job_items_status", "match_analysis_job_items", ["status"])
op.create_index("ix_match_analysis_job_items_email_task_id", "match_analysis_job_items", ["email_task_id"])
op.create_index("ix_match_analysis_job_items_professor_id", "match_analysis_job_items", ["professor_id"])
op.create_index("ix_match_analysis_job_items_match_analysis_run_id", "match_analysis_job_items", ["match_analysis_run_id"])
```

- [ ] **步骤 5：运行 schema 测试验证通过**

运行：

```bash
rtk powershell -NoProfile -Command "cd backend; uv run python -m unittest test.test_database_schema"
```

预期：PASS。

- [ ] **步骤 6：Commit**

```bash
rtk powershell -NoProfile -Command "git add backend/app/models/match_analysis_job.py backend/app/models/__init__.py backend/alembic/versions/a9c8e7d6f5b4_add_match_analysis_jobs.py backend/test/test_database_schema.py; git commit -m 'feat(backend): add match analysis job schema'"
```

## 任务 2：后端 schema 与 API 创建/查询

**文件：**
- 创建：`backend/app/schemas/match_analysis_job.py`
- 创建：`backend/app/api/match_analysis_jobs.py`
- 创建：`backend/app/services/match_analysis_job_runtime.py`
- 修改：`backend/app/schemas/__init__.py`
- 修改：`backend/app/api/__init__.py`
- 修改：`backend/main.py`
- 测试：`backend/test/test_match_analysis_jobs.py`
- 测试：`backend/test/test_api_endpoints.py`

- [ ] **步骤 1：编写失败的 API 测试**

创建 `backend/test/test_match_analysis_jobs.py`，使用临时 SQLite、`Base.metadata.create_all`、`async_sessionmaker`，测试 service 创建 job：

```python
from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models import Base, IdentityMaterial, IdentityProfile, LLMProfile, MatchAnalysisJob, MatchAnalysisJobItem, Professor
from app.services.match_analysis_job_runtime import create_match_analysis_job


class MatchAnalysisJobRuntimeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "match_jobs.db"
        self.engine = create_async_engine(f"sqlite+aiosqlite:///{self.db_path.as_posix()}", future=True)
        self.session_factory = async_sessionmaker(bind=self.engine, autoflush=False, expire_on_commit=False)
        self._run_async(Base.metadata.create_all(self.engine))

    def tearDown(self) -> None:
        self._run_async(self.engine.dispose())
        self.temp_dir.cleanup()

    def test_create_job_deduplicates_professors_and_skips_missing_evidence(self) -> None:
        identity_id, llm_profile_id, professor_ids = self._run_async(self._seed_create_job_data())
        job = self._run_async(
            create_match_analysis_job(
                self.session_factory,
                identity_id=identity_id,
                llm_profile_id=llm_profile_id,
                professor_ids=[professor_ids[0], professor_ids[0], professor_ids[1]],
                name="首轮匹配",
            ),
        )

        self.assertEqual(job.name, "首轮匹配")
        self.assertEqual(job.status, "queued")
        self.assertEqual(job.target_count, 1)
        self.assertEqual(job.skipped_count, 1)
```

在同一测试类里补 `_seed_create_job_data`：创建 identity、默认 material、llm profile、一个有 `research_direction` 的 professor、一个无研究证据的 professor。断言数据库里有 2 条 item，其中一条 `queued`、一条 `skipped`。

在 `backend/test/test_api_endpoints.py` 增加一个集成冒烟测试：

```python
def test_create_and_list_match_analysis_jobs(self) -> None:
    identity_id = self._create_identity(with_imap=False)
    llm_id = self._create_llm()
    self._upload_material(identity_id, filename="resume.txt", content=b"AI systems", material_type="resume")
    professor_response = self.client.post(
        "/api/professors",
        json={
            "name": "王老师",
            "email": "wang-match@example.edu",
            "title": "Professor",
            "university": "Example University",
            "school": "School of Computing",
            "department": "Computer Science",
            "research_direction": "AI agents",
            "recent_papers": [],
            "profile_url": None,
            "source_url": None,
        },
    )
    self.assertEqual(professor_response.status_code, 201, msg=professor_response.text)
    professor_id = professor_response.json()["id"]

    created = self.client.post(
        "/api/match-analysis-jobs",
        json={
            "identity_id": identity_id,
            "llm_profile_id": llm_id,
            "professor_ids": [professor_id],
        },
    )
    self.assertEqual(created.status_code, 201, msg=created.text)
    self.assertEqual(created.json()["target_count"], 1)

    listed = self.client.get(
        "/api/match-analysis-jobs",
        params={"identity_id": identity_id, "llm_profile_id": llm_id},
    )
    self.assertEqual(listed.status_code, 200)
    self.assertEqual(len(listed.json()), 1)
```

- [ ] **步骤 2：运行测试确认失败**

运行：

```bash
rtk powershell -NoProfile -Command "cd backend; uv run python -m unittest test.test_match_analysis_jobs test.test_api_endpoints"
```

预期：失败，提示 `app.services.match_analysis_job_runtime` 或 `/api/match-analysis-jobs` 不存在。

- [ ] **步骤 3：定义 schema**

创建 `backend/app/schemas/match_analysis_job.py`：

```python
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class CreateMatchAnalysisJobRequest(BaseModel):
    identity_id: int
    llm_profile_id: int
    professor_ids: list[int] = Field(min_length=1)
    name: str | None = None


class MatchAnalysisJobRead(BaseModel):
    id: int
    name: str
    status: str
    target_count: int
    succeeded_count: int
    failed_count: int
    skipped_count: int
    total_prompt_tokens: int
    total_completion_tokens: int
    total_tokens: int
    identity_id: int
    llm_profile_id: int
    cancel_requested_at: datetime | None
    started_at: datetime | None
    finished_at: datetime | None
    created_at: datetime
    updated_at: datetime
    last_error: str | None


class MatchAnalysisJobItemRead(BaseModel):
    id: int
    job_id: int
    professor_id: int
    professor_name: str
    professor_email: str | None
    professor_title: str | None
    professor_school: str | None
    email_task_id: int | None
    status: str
    match_score: int | None
    match_analysis_run_id: int | None
    error_message: str | None
    skip_reason: str | None
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    started_at: datetime | None
    finished_at: datetime | None
    updated_at: datetime


class MatchAnalysisJobActionResponse(BaseModel):
    ok: bool
    job: MatchAnalysisJobRead
```

更新 `backend/app/schemas/__init__.py` 导出这些类型。

- [ ] **步骤 4：实现创建和查询 service**

创建 `backend/app/services/match_analysis_job_runtime.py`，先实现创建、读取、序列化辅助，不执行 worker：

```python
from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import selectinload

from app.models import (
    EmailTask,
    EmailTaskSource,
    EmailTaskStatus,
    IdentityProfile,
    LLMProfile,
    MatchAnalysisJob,
    MatchAnalysisJobItem,
    MatchAnalysisJobItemStatus,
    MatchAnalysisJobStatus,
    Professor,
)


async def create_match_analysis_job(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    identity_id: int,
    llm_profile_id: int,
    professor_ids: list[int],
    name: str | None = None,
) -> MatchAnalysisJob:
    unique_professor_ids = list(dict.fromkeys(professor_ids))
    if not unique_professor_ids:
        raise ValueError("请选择要分析匹配度的导师")

    async with session_factory() as session:
        identity = await session.get(IdentityProfile, identity_id)
        if identity is None:
            raise ValueError("身份不存在")
        if identity.current_primary_material_id is None:
            raise ValueError("请先设置默认材料")
        llm_profile = await session.get(LLMProfile, llm_profile_id)
        if llm_profile is None:
            raise ValueError("LLM 配置不存在")

        professors = (
            await session.scalars(
                select(Professor)
                .where(Professor.id.in_(unique_professor_ids), Professor.archived_at.is_(None))
                .order_by(Professor.id.asc())
            )
        ).all()
        if not professors:
            raise ValueError("没有可分析的导师")

        now = datetime.now(UTC)
        job = MatchAnalysisJob(
            name=name or f"批量匹配分析 {now:%Y-%m-%d %H:%M}",
            identity_id=identity_id,
            llm_profile_id=llm_profile_id,
            status=MatchAnalysisJobStatus.QUEUED.value,
            target_count=0,
            skipped_count=0,
            created_at=now,
            updated_at=now,
        )
        session.add(job)
        await session.flush()

        queued_count = 0
        skipped_count = 0
        for professor in professors:
            if _has_professor_match_evidence(professor):
                email_task = await _ensure_match_email_task(session, professor, identity, llm_profile)
                item = MatchAnalysisJobItem(
                    job_id=job.id,
                    professor_id=professor.id,
                    email_task_id=email_task.id,
                    status=MatchAnalysisJobItemStatus.QUEUED.value,
                )
                queued_count += 1
            else:
                item = MatchAnalysisJobItem(
                    job_id=job.id,
                    professor_id=professor.id,
                    email_task_id=None,
                    status=MatchAnalysisJobItemStatus.SKIPPED.value,
                    skip_reason="缺少研究方向或近期论文",
                    finished_at=now,
                )
                skipped_count += 1
            session.add(item)

        if queued_count == 0:
            raise ValueError("已选导师都缺少研究方向或近期论文，暂不能分析匹配度")
        job.target_count = queued_count
        job.skipped_count = skipped_count
        await session.commit()
        await session.refresh(job)
        return job
```

同文件补 `_has_professor_match_evidence` 和 `_ensure_match_email_task`。`_ensure_match_email_task` 查找同 identity/profile/professor 下最新未取消 `EmailTask`，不存在则创建：

```python
EmailTask(
    professor_id=professor.id,
    identity_id=identity.id,
    llm_profile_id=llm_profile.id,
    source=EmailTaskSource.MANUAL.value,
    status=EmailTaskStatus.DISCOVERED.value,
    primary_material_id=identity.current_primary_material_id,
    selected_material_ids=[],
)
```

- [ ] **步骤 5：实现 API router**

创建 `backend/app/api/match_analysis_jobs.py`：

```python
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.database import get_async_session, get_session_factory
from app.models import MatchAnalysisJob, MatchAnalysisJobItem
from app.schemas.match_analysis_job import CreateMatchAnalysisJobRequest, MatchAnalysisJobItemRead, MatchAnalysisJobRead
from app.services.match_analysis_job_runtime import create_match_analysis_job, serialize_match_analysis_job, serialize_match_analysis_job_item

router = APIRouter(prefix="/api/match-analysis-jobs", tags=["match-analysis-jobs"])


@router.get("", response_model=list[MatchAnalysisJobRead])
async def list_match_analysis_jobs(
    identity_id: int | None = Query(default=None),
    llm_profile_id: int | None = Query(default=None),
    session: AsyncSession = Depends(get_async_session),
) -> list[MatchAnalysisJobRead]:
    statement = select(MatchAnalysisJob).order_by(MatchAnalysisJob.created_at.desc(), MatchAnalysisJob.id.desc())
    if identity_id is not None:
        statement = statement.where(MatchAnalysisJob.identity_id == identity_id)
    if llm_profile_id is not None:
        statement = statement.where(MatchAnalysisJob.llm_profile_id == llm_profile_id)
    jobs = (await session.scalars(statement)).all()
    return [serialize_match_analysis_job(job) for job in jobs]


@router.post("", response_model=MatchAnalysisJobRead, status_code=status.HTTP_201_CREATED)
async def create_job(payload: CreateMatchAnalysisJobRequest) -> MatchAnalysisJobRead:
    try:
        job = await create_match_analysis_job(
            get_session_factory(),
            identity_id=payload.identity_id,
            llm_profile_id=payload.llm_profile_id,
            professor_ids=payload.professor_ids,
            name=payload.name,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return serialize_match_analysis_job(job)
```

同 router 增加 `GET /{job_id}` 和 `GET /{job_id}/items`。items 查询使用 `selectinload(MatchAnalysisJobItem.professor)` 与 `selectinload(MatchAnalysisJobItem.email_task)`，不存在返回 404。

更新 `backend/app/api/__init__.py`、`backend/main.py` 注册 router。

- [ ] **步骤 6：运行 API 测试验证通过**

运行：

```bash
rtk powershell -NoProfile -Command "cd backend; uv run python -m unittest test.test_match_analysis_jobs test.test_api_endpoints"
```

预期：新增创建、列表测试 PASS。

- [ ] **步骤 7：Commit**

```bash
rtk powershell -NoProfile -Command "git add backend/app/schemas/match_analysis_job.py backend/app/schemas/__init__.py backend/app/services/match_analysis_job_runtime.py backend/app/api/match_analysis_jobs.py backend/app/api/__init__.py backend/main.py backend/test/test_match_analysis_jobs.py backend/test/test_api_endpoints.py; git commit -m 'feat(backend): add match analysis job API'"
```

## 任务 3：后台 worker 执行、取消、重试

**文件：**
- 修改：`backend/app/services/match_analysis_job_runtime.py`
- 修改：`backend/app/services/runtime_manager.py`
- 修改：`backend/app/core/config.py`
- 修改：`backend/.env.example`
- 修改：`backend/app/api/match_analysis_jobs.py`
- 测试：`backend/test/test_match_analysis_jobs.py`

- [ ] **步骤 1：编写失败的 worker 测试**

在 `backend/test/test_match_analysis_jobs.py` 添加：

```python
def test_run_queued_job_marks_success_and_updates_counts(self) -> None:
    identity_id, llm_profile_id, professor_ids = self._run_async(self._seed_create_job_data())
    job = self._run_async(
        create_match_analysis_job(
            self.session_factory,
            identity_id=identity_id,
            llm_profile_id=llm_profile_id,
            professor_ids=[professor_ids[0]],
            name=None,
        ),
    )

    with patch(
        "app.services.task_runtime.llm_runtime.generate_match_evaluation",
        AsyncMock(return_value=self._build_match_evaluation_result(match_score=88)),
    ):
        processed = self._run_async(run_queued_match_analysis_jobs_once(self.session_factory, item_concurrency=1))

    self.assertEqual(processed, 1)
    stored = self._run_async(self._get_job(job.id))
    self.assertEqual(stored.status, "completed")
    self.assertEqual(stored.succeeded_count, 1)
    self.assertEqual(stored.failed_count, 0)
    self.assertEqual(stored.total_tokens, 100)
```

同文件再加两个测试：

```python
def test_run_queued_job_keeps_going_after_item_failure(self) -> None:
    identity_id, llm_profile_id, professor_ids = self._run_async(
        self._seed_create_job_data(extra_analyzable_professor=True),
    )
    job = self._run_async(
        create_match_analysis_job(
            self.session_factory,
            identity_id=identity_id,
            llm_profile_id=llm_profile_id,
            professor_ids=[professor_ids[0], professor_ids[2]],
            name=None,
        ),
    )

    async def fake_generate(*args, **kwargs):
        if fake_generate.calls == 0:
            fake_generate.calls += 1
            raise RuntimeError("模型临时失败")
        return self._build_match_evaluation_result(match_score=91)

    fake_generate.calls = 0
    with patch(
        "app.services.task_runtime.llm_runtime.generate_match_evaluation",
        AsyncMock(side_effect=fake_generate),
    ):
        processed = self._run_async(
            run_queued_match_analysis_jobs_once(self.session_factory, item_concurrency=1),
        )

    self.assertEqual(processed, 1)
    stored = self._run_async(self._get_job(job.id))
    self.assertEqual(stored.status, "partial_failed")
    self.assertEqual(stored.failed_count, 1)
    self.assertEqual(stored.succeeded_count, 1)

def test_cancel_job_marks_queued_items_canceled(self) -> None:
    identity_id, llm_profile_id, professor_ids = self._run_async(self._seed_create_job_data())
    job = self._run_async(
        create_match_analysis_job(
            self.session_factory,
            identity_id=identity_id,
            llm_profile_id=llm_profile_id,
            professor_ids=[professor_ids[0]],
            name=None,
        ),
    )

    self._run_async(request_match_analysis_job_cancel(self.session_factory, job.id))
    processed = self._run_async(
        run_queued_match_analysis_jobs_once(self.session_factory, item_concurrency=1),
    )

    self.assertEqual(processed, 0)
    stored = self._run_async(self._get_job(job.id))
    self.assertEqual(stored.status, "canceled")
    items = self._run_async(self._get_job_items(job.id))
    self.assertEqual(items[0].status, "canceled")
```

这些测试需要在测试类里提供 `_build_match_evaluation_result`：

```python
from types import SimpleNamespace


def _build_match_evaluation_result(self, *, match_score: int):
    return SimpleNamespace(
        result=SimpleNamespace(
            match_score=match_score,
            match_reason="研究方向匹配",
            fit_points=["方向一致"],
            risk_points=[],
            keywords=["AI agents"],
        ),
        usage=SimpleNamespace(
            prompt_tokens=60,
            completion_tokens=40,
            total_tokens=100,
            cached_tokens=0,
        ),
        duration_ms=1200,
        endpoint_kind="chat_completions",
        status_code=200,
        prompt_hash="prompt-hash",
        stable_prefix_hash="prefix-hash",
    )
```

- [ ] **步骤 2：运行测试确认失败**

运行：

```bash
rtk powershell -NoProfile -Command "cd backend; uv run python -m unittest test.test_match_analysis_jobs"
```

预期：失败，提示 `run_queued_match_analysis_jobs_once`、`request_match_analysis_job_cancel` 未定义。

- [ ] **步骤 3：实现 worker claim 与 item 执行**

在 `backend/app/services/match_analysis_job_runtime.py` 增加：

```python
async def run_queued_match_analysis_jobs_once(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    item_concurrency: int | None = None,
) -> int:
    job_id = await _claim_next_match_analysis_job(session_factory)
    if job_id is None:
        return 0
    await _run_match_analysis_job(session_factory, job_id, item_concurrency=item_concurrency or 3)
    return 1
```

`_claim_next_match_analysis_job` 使用 SQLAlchemy `update(MatchAnalysisJob)` 原子领取：

```python
claim_result = await session.execute(
    update(MatchAnalysisJob)
    .where(
        MatchAnalysisJob.id == job_id,
        MatchAnalysisJob.status == MatchAnalysisJobStatus.QUEUED.value,
    )
    .values(
        status=MatchAnalysisJobStatus.RUNNING.value,
        started_at=now,
        updated_at=now,
        last_error=None,
    ),
)
if claim_result.rowcount != 1:
    await session.rollback()
    return None
await session.commit()
return job_id
```

`_run_match_analysis_job` 查询 queued items，使用 `asyncio.Semaphore(item_concurrency)` 控制并发。每个 item 调用 `_run_match_analysis_job_item`。

`_run_match_analysis_job_item` 流程：

1. 打开 session，检查 job 是否 cancel requested。
2. 将 item 更新为 `running`，写 `started_at`。
3. 调用 `calculate_task_match(session_factory, item.email_task_id, force=True, ignore_batch_status=True)`。
4. 成功后把 item 更新为 `succeeded`，写 `match_analysis_run_id` 和 usage。
5. 捕获 `MatchAnalysisAlreadyRunningError` 时标记 `skipped`，`skip_reason="该导师已有匹配分析进行中"`。
6. 捕获 `ValueError` 时标记 `skipped`，`skip_reason=str(exc)`。
7. 捕获其他异常时标记 `failed`，`error_message=str(exc)`。

完成所有 item 后调用 `_refresh_match_analysis_job_summary` 汇总：

```python
if canceled_count > 0 and succeeded_count == 0 and failed_count == 0:
    status = MatchAnalysisJobStatus.CANCELED.value
elif failed_count == 0 and queued_count == 0 and running_count == 0 and succeeded_count > 0:
    status = MatchAnalysisJobStatus.COMPLETED.value
elif succeeded_count > 0:
    status = MatchAnalysisJobStatus.PARTIAL_FAILED.value
else:
    status = MatchAnalysisJobStatus.FAILED.value
```

`total_*_tokens` 从 items 求和。

- [ ] **步骤 4：实现取消和重试失败 service/API**

在 service 增加：

```python
async def request_match_analysis_job_cancel(session_factory: async_sessionmaker[AsyncSession], job_id: int) -> MatchAnalysisJob:
    async with session_factory() as session:
        job = await session.get(MatchAnalysisJob, job_id)
        if job is None:
            raise ValueError("匹配分析任务不存在")
        now = datetime.now(UTC)
        if job.status == MatchAnalysisJobStatus.QUEUED.value:
            job.status = MatchAnalysisJobStatus.CANCELED.value
            job.cancel_requested_at = now
            job.finished_at = now
            await session.execute(
                update(MatchAnalysisJobItem)
                .where(
                    MatchAnalysisJobItem.job_id == job.id,
                    MatchAnalysisJobItem.status == MatchAnalysisJobItemStatus.QUEUED.value,
                )
                .values(status=MatchAnalysisJobItemStatus.CANCELED.value, finished_at=now, updated_at=now),
            )
        elif job.status == MatchAnalysisJobStatus.RUNNING.value:
            job.cancel_requested_at = now
        else:
            raise ValueError("只有排队中或运行中的匹配分析任务可以取消")
        job.updated_at = now
        await session.commit()
        await session.refresh(job)
        return job

async def retry_failed_match_analysis_job(session_factory: async_sessionmaker[AsyncSession], job_id: int) -> MatchAnalysisJob:
    async with session_factory() as session:
        job = await session.get(MatchAnalysisJob, job_id)
        if job is None:
            raise ValueError("匹配分析任务不存在")
        professor_ids = list(
            await session.scalars(
                select(MatchAnalysisJobItem.professor_id)
                .where(
                    MatchAnalysisJobItem.job_id == job_id,
                    MatchAnalysisJobItem.status.in_([
                        MatchAnalysisJobItemStatus.FAILED.value,
                        MatchAnalysisJobItemStatus.CANCELED.value,
                    ]),
                )
                .order_by(MatchAnalysisJobItem.id.asc())
            )
        )
    if not professor_ids:
        raise ValueError("没有可重试的失败项")
    return await create_match_analysis_job(
        session_factory,
        identity_id=job.identity_id,
        llm_profile_id=job.llm_profile_id,
        professor_ids=professor_ids,
        name=f"{job.name} - 重试",
    )
```

在 `backend/app/api/match_analysis_jobs.py` 增加：

```python
@router.post("/{job_id}/cancel", response_model=MatchAnalysisJobActionResponse)
async def cancel_job(job_id: int) -> MatchAnalysisJobActionResponse:
    try:
        job = await request_match_analysis_job_cancel(get_session_factory(), job_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return MatchAnalysisJobActionResponse(ok=True, job=serialize_match_analysis_job(job))

@router.post("/{job_id}/retry-failed", response_model=MatchAnalysisJobRead, status_code=status.HTTP_201_CREATED)
async def retry_failed(job_id: int) -> MatchAnalysisJobRead:
    try:
        job = await retry_failed_match_analysis_job(get_session_factory(), job_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return serialize_match_analysis_job(job)
```

- [ ] **步骤 5：接入 RuntimeManager 与配置**

修改 `backend/app/core/config.py`：

```python
match_analysis_job_worker_count: int
match_analysis_job_interval_seconds: int
match_analysis_job_item_concurrency: int
```

默认值：

```python
match_analysis_job_worker_count=_get_int_env("MATCH_ANALYSIS_JOB_WORKER_COUNT", 1),
match_analysis_job_interval_seconds=_get_int_env("MATCH_ANALYSIS_JOB_INTERVAL_SECONDS", 10),
match_analysis_job_item_concurrency=_get_int_env("MATCH_ANALYSIS_JOB_ITEM_CONCURRENCY", 3),
```

修改 `backend/app/services/runtime_manager.py`，导入 `run_queued_match_analysis_jobs_once`，启动 worker：

```python
match_analysis_tasks = [
    asyncio.create_task(
        self._loop(
            f"match-analysis-worker-{index}",
            settings.match_analysis_job_interval_seconds,
            lambda session_factory: run_queued_match_analysis_jobs_once(
                session_factory,
                item_concurrency=settings.match_analysis_job_item_concurrency,
            ),
        ),
    )
    for index in range(1, settings.match_analysis_job_worker_count + 1)
]
```

把 `*match_analysis_tasks` 加入 `self._tasks`。

更新 `backend/.env.example`：

```text
MATCH_ANALYSIS_JOB_WORKER_COUNT=1
MATCH_ANALYSIS_JOB_INTERVAL_SECONDS=10
MATCH_ANALYSIS_JOB_ITEM_CONCURRENCY=3
```

- [ ] **步骤 6：运行 worker 测试验证通过**

运行：

```bash
rtk powershell -NoProfile -Command "cd backend; uv run python -m unittest test.test_match_analysis_jobs"
```

预期：PASS。

- [ ] **步骤 7：Commit**

```bash
rtk powershell -NoProfile -Command "git add backend/app/services/match_analysis_job_runtime.py backend/app/services/runtime_manager.py backend/app/core/config.py backend/.env.example backend/app/api/match_analysis_jobs.py backend/test/test_match_analysis_jobs.py; git commit -m 'feat(backend): run match analysis jobs in background'"
```

## 任务 4：前端 API 类型与首页创建后台任务

**文件：**
- 创建：`frontend/src/lib/api/matchAnalysisJobsApi.ts`
- 修改：`frontend/src/types/index.ts`
- 修改：`frontend/src/pages/HomePage.tsx`

- [ ] **步骤 1：新增前端类型**

在 `frontend/src/types/index.ts` 增加：

```ts
export type MatchAnalysisJobStatus =
  | 'queued'
  | 'running'
  | 'completed'
  | 'partial_failed'
  | 'failed'
  | 'canceled';

export type MatchAnalysisJobItemStatus =
  | 'queued'
  | 'running'
  | 'succeeded'
  | 'failed'
  | 'skipped'
  | 'canceled';

export interface CreateMatchAnalysisJobRequestDTO {
  identity_id: number;
  llm_profile_id: number;
  professor_ids: number[];
  name?: string | null;
}

export interface MatchAnalysisJobDTO {
  id: number;
  name: string;
  status: MatchAnalysisJobStatus;
  target_count: number;
  succeeded_count: number;
  failed_count: number;
  skipped_count: number;
  total_prompt_tokens: number;
  total_completion_tokens: number;
  total_tokens: number;
  identity_id: number;
  llm_profile_id: number;
  cancel_requested_at: string | null;
  started_at: string | null;
  finished_at: string | null;
  created_at: string;
  updated_at: string;
  last_error: string | null;
}

export interface MatchAnalysisJobItemDTO {
  id: number;
  job_id: number;
  professor_id: number;
  professor_name: string;
  professor_email: string | null;
  professor_title: string | null;
  professor_school: string | null;
  email_task_id: number | null;
  status: MatchAnalysisJobItemStatus;
  match_score: number | null;
  match_analysis_run_id: number | null;
  error_message: string | null;
  skip_reason: string | null;
  prompt_tokens: number;
  completion_tokens: number;
  total_tokens: number;
  started_at: string | null;
  finished_at: string | null;
  updated_at: string;
}

export const MATCH_ANALYSIS_JOB_STATUS_LABELS: Record<MatchAnalysisJobStatus, string> = {
  queued: '排队中',
  running: '运行中',
  completed: '已完成',
  partial_failed: '部分失败',
  failed: '失败',
  canceled: '已取消',
};
```

- [ ] **步骤 2：新增 API client**

创建 `frontend/src/lib/api/matchAnalysisJobsApi.ts`：

```ts
import { apiFetch } from '@/lib/api/client';
import type {
  CreateMatchAnalysisJobRequestDTO,
  MatchAnalysisJobDTO,
  MatchAnalysisJobItemDTO,
} from '@/types';

export const listMatchAnalysisJobs = (params?: {
  identityId?: number | null;
  llmProfileId?: number | null;
}) =>
  apiFetch<MatchAnalysisJobDTO[]>(
    '/api/match-analysis-jobs',
    undefined,
    {
      identity_id: params?.identityId ?? undefined,
      llm_profile_id: params?.llmProfileId ?? undefined,
    },
  );

export const createMatchAnalysisJob = (payload: CreateMatchAnalysisJobRequestDTO) =>
  apiFetch<MatchAnalysisJobDTO>('/api/match-analysis-jobs', {
    method: 'POST',
    body: JSON.stringify(payload),
  });

export const listMatchAnalysisJobItems = (jobId: number) =>
  apiFetch<MatchAnalysisJobItemDTO[]>(`/api/match-analysis-jobs/${jobId}/items`);

export const cancelMatchAnalysisJob = (jobId: number) =>
  apiFetch<{ ok: boolean; job: MatchAnalysisJobDTO }>(`/api/match-analysis-jobs/${jobId}/cancel`, {
    method: 'POST',
  });

export const retryFailedMatchAnalysisJob = (jobId: number) =>
  apiFetch<MatchAnalysisJobDTO>(`/api/match-analysis-jobs/${jobId}/retry-failed`, {
    method: 'POST',
  });
```

- [ ] **步骤 3：修改首页批量按钮逻辑**

在 `frontend/src/pages/HomePage.tsx` 中导入：

```ts
import { createMatchAnalysisJob } from "@/lib/api/matchAnalysisJobsApi";
```

把 `handleGenerateSelected` 中 `runWarmupThenConcurrent` 整段替换为创建 job：

```ts
setBulkScoring(true);
try {
  const selectedProfessorIds = Array.from(selectedIds);
  const selectedProfessors = professors.filter((item) => selectedIds.has(item.id));
  const analyzableProfessors = selectedProfessors.filter(hasMatchEvidence);
  if (analyzableProfessors.length === 0) {
    notifyWarning(
      "缺少研究信息",
      "已选导师都缺少研究方向或近期论文，暂不能分析匹配度。",
    );
    return;
  }
  const job = await createMatchAnalysisJob({
    identity_id: selectedIdentityId,
    llm_profile_id: selectedLlmProfileId,
    professor_ids: selectedProfessorIds,
    name: null,
  });
  notifySuccess(
    "已创建批量匹配分析任务",
    `任务中心会继续后台分析 ${job.target_count} 位导师。`,
  );
  setSelectedIds(new Set());
} catch (actionError) {
  const message = actionError instanceof Error ? actionError.message : "创建批量匹配分析任务失败";
  notifyError("创建任务失败", message);
} finally {
  setBulkScoring(false);
}
```

保留单个导师 `handleGenerateOne` 和 `runCalculateMatchForProfessor`。

- [ ] **步骤 4：运行前端 lint**

运行：

```bash
rtk powershell -NoProfile -Command "cd frontend; npm run lint"
```

预期：PASS。若失败，修复本任务引入的未使用 import，例如 `runWarmupThenConcurrent`、`TokenUsage`、`sumTokenUsage`。

- [ ] **步骤 5：Commit**

```bash
rtk powershell -NoProfile -Command "git add frontend/src/types/index.ts frontend/src/lib/api/matchAnalysisJobsApi.ts frontend/src/pages/HomePage.tsx; git commit -m 'feat(frontend): create background match analysis jobs'"
```

## 任务 5：任务中心展示匹配分析任务

**文件：**
- 修改：`frontend/src/pages/TasksPage.tsx`

- [ ] **步骤 1：扩展任务中心 tab 与状态文案**

在 `frontend/src/pages/TasksPage.tsx` 中把：

```ts
type TasksTab = "batch" | "crawl";
```

改为：

```ts
type TasksTab = "batch" | "crawl" | "match";
```

导入新增 API 和类型：

```ts
import {
  cancelMatchAnalysisJob,
  listMatchAnalysisJobItems,
  listMatchAnalysisJobs,
  retryFailedMatchAnalysisJob,
} from "@/lib/api/matchAnalysisJobsApi";
import {
  MATCH_ANALYSIS_JOB_STATUS_LABELS,
  type MatchAnalysisJobDTO,
  type MatchAnalysisJobItemDTO,
  type MatchAnalysisJobStatus,
  type MatchAnalysisJobItemStatus,
} from "@/types";
```

新增状态 tone：

```ts
const MATCH_ANALYSIS_JOB_STATUS_TONES: Record<MatchAnalysisJobStatus, string> = {
  queued: "border-sky-200 bg-sky-50 text-sky-700",
  running: "border-primary/20 bg-primary/10 text-primary",
  completed: "border-emerald-200 bg-emerald-50 text-emerald-700",
  partial_failed: "border-amber-200 bg-amber-50 text-amber-700",
  failed: "border-red-200 bg-red-50 text-red-700",
  canceled: "border-stone-200 bg-stone-100 text-stone-600",
};
```

- [ ] **步骤 2：增加状态、加载和刷新逻辑**

在 `TasksPage` state 区增加：

```ts
const [matchAnalysisJobs, setMatchAnalysisJobs] = useState<MatchAnalysisJobDTO[]>([]);
const [matchJobsLoading, setMatchJobsLoading] = useState(false);
const [selectedMatchJob, setSelectedMatchJob] = useState<MatchAnalysisJobDTO | null>(null);
const [selectedMatchJobItems, setSelectedMatchJobItems] = useState<MatchAnalysisJobItemDTO[]>([]);
const [matchJobDetailsLoading, setMatchJobDetailsLoading] = useState(false);
const [matchPage, setMatchPage] = useState(1);
const [cancelingMatchJobId, setCancelingMatchJobId] = useState<number | null>(null);
const [retryingMatchJobId, setRetryingMatchJobId] = useState<number | null>(null);
```

新增 `loadMatchAnalysisJobs`：

```ts
const loadMatchAnalysisJobs = useCallback(async () => {
  if (!selectedIdentityId || !selectedLlmProfileId) {
    setMatchAnalysisJobs([]);
    return;
  }
  setMatchJobsLoading(true);
  try {
    const data = await listMatchAnalysisJobs({
      identityId: selectedIdentityId,
      llmProfileId: selectedLlmProfileId,
    });
    setMatchAnalysisJobs(data);
  } catch (error) {
    const message = error instanceof Error ? error.message : "加载匹配分析任务失败";
    notifyError("加载失败", message);
  } finally {
    setMatchJobsLoading(false);
  }
}, [notifyError, selectedIdentityId, selectedLlmProfileId]);
```

在 `activeTab === "match"` 时加载，并每 5 秒刷新 running/queued：

```ts
useEffect(() => {
  if (activeTab !== "match") {
    return;
  }
  void loadMatchAnalysisJobs();
}, [activeTab, loadMatchAnalysisJobs]);
```

- [ ] **步骤 3：增加列表卡片和详情抽屉**

在 tab 按钮区域增加“匹配分析”。在内容区域新增 `activeTab === "match"` 分支，展示：

```tsx
{visibleMatchJobs.map((job) => (
  <article key={job.id} className="rounded-lg border border-stone-200 bg-white p-4 shadow-sm">
    <div className="flex items-start justify-between gap-3">
      <div>
        <h3 className="text-base font-semibold text-stone-900">{job.name}</h3>
        <p className="mt-1 text-sm text-stone-500">
          成功 {job.succeeded_count} / 失败 {job.failed_count} / 跳过 {job.skipped_count} / 共 {job.target_count}
        </p>
      </div>
      <span className={`rounded-full border px-2.5 py-1 text-xs font-medium ${MATCH_ANALYSIS_JOB_STATUS_TONES[job.status]}`}>
        {MATCH_ANALYSIS_JOB_STATUS_LABELS[job.status]}
      </span>
    </div>
    <div className="mt-3 text-sm text-stone-600">
      Token：{job.total_tokens.toLocaleString()}，更新于 {formatUpdatedAt(job.updated_at)}
    </div>
    <div className="mt-4 flex flex-wrap gap-2">
      <button type="button" className="ui-btn-secondary" onClick={() => void openMatchJobDetails(job)}>
        查看详情
      </button>
      {(job.status === "queued" || job.status === "running") ? (
        <button type="button" className="ui-btn-secondary" onClick={() => void handleCancelMatchJob(job.id)}>
          取消
        </button>
      ) : null}
      {(job.status === "partial_failed" || job.status === "failed" || job.status === "canceled") ? (
        <button type="button" className="ui-btn-secondary" onClick={() => void handleRetryMatchJob(job.id)}>
          重试失败项
        </button>
      ) : null}
    </div>
  </article>
))}
```

详情抽屉使用固定右侧 overlay：标题显示 job 名称，顶部四个统计块显示成功、失败、跳过、总 token；下方表格列为“导师”“状态”“匹配分”“说明”“Token”“更新时间”。说明列优先显示 `error_message`，其次显示 `skip_reason`，都为空时显示“已完成”。

- [ ] **步骤 4：实现取消与重试动作**

新增：

```ts
const handleCancelMatchJob = async (jobId: number) => {
  const confirmed = await confirm({
    title: "取消匹配分析任务？",
    description: "已开始的单项分析会在安全点结束，未开始的导师会被取消。",
    confirmLabel: "取消任务",
  });
  if (!confirmed) {
    return;
  }
  setCancelingMatchJobId(jobId);
  try {
    const result = await cancelMatchAnalysisJob(jobId);
    setMatchAnalysisJobs((prev) => prev.map((job) => (job.id === jobId ? result.job : job)));
    notifySuccess("已请求取消", "匹配分析任务会在安全点停止。");
  } catch (error) {
    notifyError("取消失败", error instanceof Error ? error.message : "取消匹配分析任务失败");
  } finally {
    setCancelingMatchJobId(null);
  }
};
```

`handleRetryMatchJob` 调用 `retryFailedMatchAnalysisJob`，成功后把新 job 插到列表顶部并通知“重试任务已创建”。

- [ ] **步骤 5：运行前端 lint 和 build**

运行：

```bash
rtk powershell -NoProfile -Command "cd frontend; npm run lint"
rtk powershell -NoProfile -Command "cd frontend; npm run build"
```

预期：两个命令均 PASS。

- [ ] **步骤 6：Commit**

```bash
rtk powershell -NoProfile -Command "git add frontend/src/pages/TasksPage.tsx; git commit -m 'feat(frontend): show match analysis jobs in task center'"
```

## 任务 6：端到端回归与文档同步

**文件：**
- 修改：`docs/superpowers/specs/2026-05-02-match-and-crawler-concurrency-design.md`
- 修改：`docs/project_description.md`
- 修改：`docs/database_table_design.md`
- 测试：所有相关测试

- [ ] **步骤 1：更新旧并发设计边界**

在 `docs/superpowers/specs/2026-05-02-match-and-crawler-concurrency-design.md` 中把非目标：

```markdown
- 不把匹配分析改造成独立的后台队列系统。
```

改为：

```markdown
- 本设计当时不把匹配分析改造成独立后台队列；后续已通过 `2026-05-03-background-batch-match-analysis-design.md` 将“批量匹配分析”升级为受控本地后台任务。
```

- [ ] **步骤 2：更新项目说明**

在 `docs/project_description.md` 的任务流部分补一句：

```markdown
- 批量匹配分析作为后台任务在任务中心观察，单个导师匹配仍可在首页或工作区即时触发。
```

- [ ] **步骤 3：更新数据库表说明**

在 `docs/database_table_design.md` 的 `match_analysis_runs` 附近新增两节：

```markdown
## `match_analysis_jobs`
后台批量匹配分析任务聚合表。它只表示一次批量计算，不代表邮件发送批次。

## `match_analysis_job_items`
后台批量匹配分析明细表。每条记录对应一个导师的一次批量分析项，并通过 `match_analysis_run_id` 关联实际模型调用审计。
```

- [ ] **步骤 4：运行完整验证**

运行：

```bash
rtk powershell -NoProfile -Command "cd backend; uv run python -m unittest test.test_match_analysis_jobs test.test_database_schema test.test_api_endpoints"
rtk powershell -NoProfile -Command "cd frontend; npm run lint"
rtk powershell -NoProfile -Command "cd frontend; npm run build"
```

预期：全部 PASS。

- [ ] **步骤 5：人工验证**

启动后端和前端：

```bash
rtk powershell -NoProfile -Command "cd backend; uv run uvicorn main:app --reload"
rtk powershell -NoProfile -Command "cd frontend; npm run dev"
```

手动验证：

1. 首页选择多个导师，点击“批量分析匹配度”。
2. 页面出现“已创建批量匹配分析任务”通知。
3. 切到任务中心“匹配分析”，能看到 running/queued 任务。
4. 刷新首页或离开页面后，任务中心进度继续变化。
5. job 完成后首页刷新能看到匹配分。
6. 对 running job 点击取消，未开始 item 进入 canceled。
7. 对 partial_failed job 点击“重试失败项”，生成一个新的 job。

- [ ] **步骤 6：Commit**

```bash
rtk powershell -NoProfile -Command "git add docs/superpowers/specs/2026-05-02-match-and-crawler-concurrency-design.md docs/project_description.md docs/database_table_design.md; git commit -m 'docs: update background match analysis task docs'"
```

## 实施注意事项

- 不要复用 `batch_tasks` 表或发送状态机。
- 不要删除现有单个导师即时匹配入口。
- 后台 worker 调用 service，不要从后端内部反向请求 HTTP API。
- `match_analysis_runs` 仍然是 token 用量中心的来源；job 表只做任务中心摘要。
- 当前工作区可能已有用户未提交的前端改动，实现时只修改本计划列出的文件，并在提交前检查 `git status --short`。
- 所有 shell 命令必须使用 `rtk` 前缀。
