# 任务中心回收站实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 为任务中心的批量邮件、教师抓取、匹配分析 3 类任务增加「删除到回收站」和「恢复」能力。

**架构：** 后端为 3 张任务主表增加 `deleted_at` 软删除字段，列表接口通过 `view=current|trash` 分流，删除和恢复接口保持幂等。前端在每个任务分类内增加「当前任务 / 回收站」切换，当前列表展示删除入口，回收站展示恢复入口。

**技术栈：** FastAPI、SQLAlchemy、Alembic、unittest、Vite、React、TypeScript、Vitest、Testing Library。

---

## 文件结构

- 修改：`backend/app/models/batch_task.py`
  为 `BatchTask` 增加 `deleted_at` 字段。
- 修改：`backend/app/models/crawl_job.py`
  为 `CrawlJob` 增加 `deleted_at` 字段。
- 修改：`backend/app/models/match_analysis_job.py`
  为 `MatchAnalysisJob` 增加 `deleted_at` 字段。
- 创建：`backend/alembic/versions/e8f7a6b5c4d3_add_task_trash_deleted_at.py`
  为 3 张任务主表新增 `deleted_at` 列和索引。
- 修改：`backend/app/schemas/batch_task.py`
  在批量任务读模型中返回 `deleted_at`。
- 修改：`backend/app/schemas/crawl_job.py`
  在抓取任务读模型中返回 `deleted_at`。
- 修改：`backend/app/schemas/match_analysis_job.py`
  在匹配分析任务读模型中返回 `deleted_at`。
- 修改：`backend/app/api/batch_tasks.py`
  增加 `view` 列表过滤、删除接口、恢复接口和操作日志。
- 修改：`backend/app/api/crawl_jobs.py`
  增加 `view` 列表过滤、删除接口、恢复接口和操作日志。
- 修改：`backend/app/api/match_analysis_jobs.py`
  增加 `view` 列表过滤、删除接口、恢复接口和操作日志。
- 修改：`backend/app/services/task_runtime.py`
  批量邮件调度查询排除已删除批量任务。
- 修改：`backend/app/services/crawl_job_runtime.py`
  抓取任务调度查询排除已删除任务。
- 修改：`backend/app/services/match_analysis_job_runtime.py`
  匹配分析任务调度查询排除已删除任务。
- 修改：`backend/test/test_database_schema.py`
  验证 3 张表都有 `deleted_at` 字段。
- 修改：`backend/test/test_api_endpoints.py`
  覆盖批量任务和匹配分析任务的回收站 API。
- 修改：`backend/test/test_crawl_jobs_api.py`
  覆盖抓取任务的回收站 API。
- 修改：`backend/test/test_crawl_job_runtime.py`
  覆盖抓取调度排除已删除任务。
- 修改：`backend/test/test_match_analysis_jobs.py`
  覆盖匹配分析调度排除已删除任务。
- 修改：`backend/test/test_batch_task_dispatch_schedule.py`
  覆盖批量邮件调度排除已删除任务。
- 修改：`frontend/src/types/index.ts`
  为 3 类任务 DTO 增加 `deleted_at`，增加 `TaskListView` 类型。
- 修改：`frontend/src/lib/api/batchTasksApi.ts`
  增加 `view` 参数、删除和恢复 API。
- 修改：`frontend/src/lib/api/crawlJobsApi.ts`
  增加 `view` 参数、删除和恢复 API。
- 修改：`frontend/src/lib/api/matchAnalysisJobsApi.ts`
  增加 `view` 参数、删除和恢复 API。
- 修改：`frontend/src/pages/TasksPage.tsx`
  增加当前/回收站切换、删除/恢复操作、计数规则和列表刷新。
- 修改：`frontend/src/pages/TasksPage.test.tsx`
  增加任务卡删除/恢复入口的组件级测试。

## 任务 1：数据模型和迁移

**文件：**
- 修改：`backend/app/models/batch_task.py`
- 修改：`backend/app/models/crawl_job.py`
- 修改：`backend/app/models/match_analysis_job.py`
- 创建：`backend/alembic/versions/e8f7a6b5c4d3_add_task_trash_deleted_at.py`
- 修改：`backend/test/test_database_schema.py`

- [ ] **步骤 1：编写失败的数据库结构测试**

在 `backend/test/test_database_schema.py` 的表结构测试附近添加断言：

```python
def test_task_tables_have_deleted_at_for_trash(self) -> None:
    self.assertIn("deleted_at", self._get_columns("batch_tasks"))
    self.assertIn("deleted_at", self._get_columns("crawl_jobs"))
    self.assertIn("deleted_at", self._get_columns("match_analysis_jobs"))
```

- [ ] **步骤 2：运行测试验证失败**

运行：

```powershell
rtk uv run python -m unittest test.test_database_schema.DatabaseSchemaTests.test_task_tables_have_deleted_at_for_trash
```

工作目录：`backend`

预期：FAIL，断言提示 `deleted_at` 不在至少一张表的列集合中。

- [ ] **步骤 3：增加 ORM 字段**

在 3 个模型中增加字段，写法保持现有时间字段风格：

```python
deleted_at: Mapped[datetime | None] = mapped_column(
    DateTime(timezone=True),
    index=True,
    nullable=True,
)
```

添加位置：

- `BatchTask`：放在 `updated_at` 后。
- `CrawlJob`：放在 `updated_at` 后。
- `MatchAnalysisJob`：放在 `updated_at` 后。

- [ ] **步骤 4：创建 Alembic 迁移**

创建 `backend/alembic/versions/e8f7a6b5c4d3_add_task_trash_deleted_at.py`：

```python
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "e8f7a6b5c4d3"
down_revision = "e8f2a4b6c9d0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for table_name in ("batch_tasks", "crawl_jobs", "match_analysis_jobs"):
        with op.batch_alter_table(table_name, schema=None) as batch_op:
            batch_op.add_column(sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True))
            batch_op.create_index(batch_op.f(f"ix_{table_name}_deleted_at"), ["deleted_at"], unique=False)


def downgrade() -> None:
    for table_name in ("match_analysis_jobs", "crawl_jobs", "batch_tasks"):
        with op.batch_alter_table(table_name, schema=None) as batch_op:
            batch_op.drop_index(batch_op.f(f"ix_{table_name}_deleted_at"))
            batch_op.drop_column("deleted_at")
```

如果当前 head 已变化，先运行 `rtk alembic heads`（工作目录 `backend`）确认最新 `down_revision`，再替换上方 `down_revision`。

- [ ] **步骤 5：运行数据库结构测试验证通过**

运行：

```powershell
rtk uv run python -m unittest test.test_database_schema.DatabaseSchemaTests.test_task_tables_have_deleted_at_for_trash
```

工作目录：`backend`

预期：PASS。

- [ ] **步骤 6：Commit**

```powershell
rtk git add backend/app/models/batch_task.py backend/app/models/crawl_job.py backend/app/models/match_analysis_job.py backend/alembic/versions/e8f7a6b5c4d3_add_task_trash_deleted_at.py backend/test/test_database_schema.py
rtk git commit -m "feat(任务中心): 增加任务回收站字段"
```

## 任务 2：批量邮件任务回收站 API

**文件：**
- 修改：`backend/app/schemas/batch_task.py`
- 修改：`backend/app/api/batch_tasks.py`
- 修改：`backend/test/test_api_endpoints.py`

- [ ] **步骤 1：编写失败的 API 测试**

在 `backend/test/test_api_endpoints.py` 的批量任务测试附近添加：

```python
def test_batch_task_delete_restore_and_trash_view(self) -> None:
    identity_id = self._create_identity(with_imap=False)
    llm_profile_id = self._create_llm()
    self.client.post("/api/professors/import-sample")
    professor_id = self.client.get("/api/professors").json()[0]["id"]
    created = self.client.post(
        "/api/batch-tasks",
        json={
            "identity_id": identity_id,
            "llm_profile_id": llm_profile_id,
            "name": "可删除批量任务",
            "professor_ids": [professor_id],
            "schedule_type": "immediate",
            "primary_material_id": None,
            "email_subject": "Hello {{导师姓名}}",
            "email_body": "Body",
            "selected_material_ids": None,
            "outreach_generation_mode": "template",
            "outreach_template_subject": "Hello {{导师姓名}}",
            "outreach_template_body_text": "Body",
            "outreach_template_body_html": None,
        },
    )
    self.assertEqual(created.status_code, 201, msg=created.text)
    task_id = created.json()["id"]

    blocked = self.client.post(f"/api/batch-tasks/{task_id}/delete")
    self.assertEqual(blocked.status_code, 400)
    self.assertIn("请先中止/取消任务后再删除", blocked.json()["detail"])

    stopped = self.client.post(f"/api/batch-tasks/{task_id}/stop")
    self.assertEqual(stopped.status_code, 200, msg=stopped.text)
    deleted = self.client.post(f"/api/batch-tasks/{task_id}/delete")
    self.assertEqual(deleted.status_code, 200, msg=deleted.text)
    self.assertIsNotNone(deleted.json()["task"]["deleted_at"])

    current = self.client.get(
        "/api/batch-tasks",
        params={"identity_id": identity_id, "llm_profile_id": llm_profile_id},
    )
    self.assertEqual(current.status_code, 200)
    self.assertEqual(current.json(), [])

    trash = self.client.get(
        "/api/batch-tasks",
        params={"identity_id": identity_id, "llm_profile_id": llm_profile_id, "view": "trash"},
    )
    self.assertEqual(trash.status_code, 200)
    self.assertEqual([item["id"] for item in trash.json()], [task_id])

    restored = self.client.post(f"/api/batch-tasks/{task_id}/restore")
    self.assertEqual(restored.status_code, 200, msg=restored.text)
    self.assertIsNone(restored.json()["task"]["deleted_at"])
```

- [ ] **步骤 2：运行测试验证失败**

运行：

```powershell
rtk uv run python -m unittest test.test_api_endpoints.ApiEndpointTests.test_batch_task_delete_restore_and_trash_view
```

工作目录：`backend`

预期：FAIL，原因是响应中缺少 `deleted_at` 或删除接口返回 `404`。

- [ ] **步骤 3：扩展批量任务 schema**

在 `backend/app/schemas/batch_task.py` 的 `BatchTaskCardRead` 增加：

```python
deleted_at: datetime | None
```

- [ ] **步骤 4：实现列表过滤和序列化**

在 `backend/app/api/batch_tasks.py` 中给 `list_batch_tasks` 增加参数：

```python
view: str = "current",
```

在查询条件中增加：

```python
if view == "trash":
    statement = statement.where(BatchTask.deleted_at.is_not(None))
elif view == "current":
    statement = statement.where(BatchTask.deleted_at.is_(None))
else:
    raise HTTPException(status_code=400, detail="未知任务视图")
```

在 `_serialize_batch_task` 返回值中增加：

```python
deleted_at=task.deleted_at,
```

- [ ] **步骤 5：实现删除和恢复接口**

在 `backend/app/api/batch_tasks.py` 增加：

```python
BATCH_TASK_DELETABLE_STATUSES = {
    BatchTaskStatus.STOPPED.value,
    BatchTaskStatus.COMPLETED.value,
}


@router.post("/{task_id}/delete", response_model=BatchTaskActionResponse)
async def delete_batch_task(
    task_id: int,
    session: AsyncSession = Depends(get_async_session),
) -> BatchTaskActionResponse:
    task = await _get_batch_task(session, task_id)
    if task.status not in BATCH_TASK_DELETABLE_STATUSES:
        raise HTTPException(status_code=400, detail="请先中止/取消任务后再删除")
    previous_deleted_at = task.deleted_at
    if task.deleted_at is None:
        task.deleted_at = datetime.now(UTC)
        task.updated_at = datetime.now(UTC)
    await _record_batch_task_action(
        session,
        task,
        "batch_task.deleted",
        extra_metadata={"previous_deleted_at": previous_deleted_at.isoformat() if previous_deleted_at else None},
    )
    await session.commit()
    await session.refresh(task, attribute_names=["email_tasks"])
    return BatchTaskActionResponse(ok=True, task=_serialize_batch_task(task))


@router.post("/{task_id}/restore", response_model=BatchTaskActionResponse)
async def restore_batch_task(
    task_id: int,
    session: AsyncSession = Depends(get_async_session),
) -> BatchTaskActionResponse:
    task = await _get_batch_task(session, task_id)
    previous_deleted_at = task.deleted_at
    if task.deleted_at is not None:
        task.deleted_at = None
        task.updated_at = datetime.now(UTC)
    await _record_batch_task_action(
        session,
        task,
        "batch_task.restored",
        extra_metadata={"previous_deleted_at": previous_deleted_at.isoformat() if previous_deleted_at else None},
    )
    await session.commit()
    await session.refresh(task, attribute_names=["email_tasks"])
    return BatchTaskActionResponse(ok=True, task=_serialize_batch_task(task))
```

如果 `_record_batch_task_action` 当前不接受 `extra_metadata`，把签名扩展为：

```python
async def _record_batch_task_action(
    session: AsyncSession,
    task: BatchTask,
    event_name: str,
    extra_metadata: dict[str, object] | None = None,
) -> None:
    metadata = {
        "status": task.status,
        "identity_id": task.identity_id,
        "llm_profile_id": task.llm_profile_id,
    }
    if extra_metadata:
        metadata.update(extra_metadata)
    await record_operation_log(
        session,
        category="email",
        event_name=event_name,
        entity_type="batch_task",
        entity_id=str(task.id),
        metadata=metadata,
    )
```

- [ ] **步骤 6：运行批量任务 API 测试验证通过**

运行：

```powershell
rtk uv run python -m unittest test.test_api_endpoints.ApiEndpointTests.test_batch_task_delete_restore_and_trash_view
```

工作目录：`backend`

预期：PASS。

- [ ] **步骤 7：Commit**

```powershell
rtk git add backend/app/schemas/batch_task.py backend/app/api/batch_tasks.py backend/test/test_api_endpoints.py
rtk git commit -m "feat(任务中心): 支持批量任务回收站接口"
```

## 任务 3：教师抓取任务回收站 API

**文件：**
- 修改：`backend/app/schemas/crawl_job.py`
- 修改：`backend/app/api/crawl_jobs.py`
- 修改：`backend/test/test_crawl_jobs_api.py`

- [ ] **步骤 1：编写失败的抓取任务 API 测试**

在 `backend/test/test_crawl_jobs_api.py` 添加：

```python
def test_crawl_job_delete_restore_and_trash_view(self) -> None:
    created = self.client.post(
        "/api/crawl-jobs",
        json={
            "university": "示例大学",
            "school": "计算机学院",
            "start_url": "https://example.edu/faculty",
            "llm_profile_id": None,
        },
    )
    self.assertEqual(created.status_code, 201, msg=created.text)
    job_id = created.json()["id"]

    blocked = self.client.post(f"/api/crawl-jobs/{job_id}/delete")
    self.assertEqual(blocked.status_code, 400)
    self.assertIn("请先中止/取消任务后再删除", blocked.json()["detail"])

    canceled = self.client.post(f"/api/crawl-jobs/{job_id}/cancel")
    self.assertEqual(canceled.status_code, 200, msg=canceled.text)
    deleted = self.client.post(f"/api/crawl-jobs/{job_id}/delete")
    self.assertEqual(deleted.status_code, 200, msg=deleted.text)
    self.assertIsNotNone(deleted.json()["deleted_at"])

    current = self.client.get("/api/crawl-jobs")
    self.assertEqual(current.status_code, 200)
    self.assertEqual(current.json(), [])

    trash = self.client.get("/api/crawl-jobs", params={"view": "trash"})
    self.assertEqual(trash.status_code, 200)
    self.assertEqual([item["id"] for item in trash.json()], [job_id])

    restored = self.client.post(f"/api/crawl-jobs/{job_id}/restore")
    self.assertEqual(restored.status_code, 200, msg=restored.text)
    self.assertIsNone(restored.json()["deleted_at"])
```

- [ ] **步骤 2：运行测试验证失败**

运行：

```powershell
rtk uv run python -m unittest test.test_crawl_jobs_api.CrawlJobsApiTests.test_crawl_job_delete_restore_and_trash_view
```

工作目录：`backend`

预期：FAIL，删除接口不存在或响应缺少 `deleted_at`。

- [ ] **步骤 3：扩展抓取任务 schema**

在 `backend/app/schemas/crawl_job.py` 的 `CrawlJobRead` 或其基类中增加：

```python
deleted_at: datetime | None
```

确保 `CrawlJobSummaryRead` 继承后也包含该字段。

- [ ] **步骤 4：实现列表过滤、序列化、删除、恢复**

在 `backend/app/api/crawl_jobs.py` 列表接口增加 `view: str = "current"`，并在查询中加入：

```python
if view == "trash":
    statement = statement.where(CrawlJob.deleted_at.is_not(None))
elif view == "current":
    statement = statement.where(CrawlJob.deleted_at.is_(None))
else:
    raise HTTPException(status_code=400, detail="未知任务视图")
```

在序列化函数中返回：

```python
deleted_at=job.deleted_at,
```

新增可删除状态和接口：

```python
CRAWL_JOB_DELETABLE_STATUSES = {
    CrawlJobStatus.COMPLETED.value,
    CrawlJobStatus.FAILED.value,
    CrawlJobStatus.CANCELED.value,
}


@router.post("/{job_id}/delete", response_model=CrawlJobRead)
async def delete_crawl_job(
    job_id: int,
    session: AsyncSession = Depends(get_async_session),
) -> CrawlJobRead:
    job = await _get_crawl_job_or_404(session, job_id)
    if job.status not in CRAWL_JOB_DELETABLE_STATUSES:
        raise HTTPException(status_code=400, detail="请先中止/取消任务后再删除")
    previous_deleted_at = job.deleted_at
    if job.deleted_at is None:
        job.deleted_at = datetime.now(UTC)
        job.updated_at = datetime.now(UTC)
    await record_operation_log(
        session,
        category="crawl",
        event_name="crawl_job.deleted",
        entity_type="crawl_job",
        entity_id=str(job.id),
        metadata={
            "status": job.status,
            "llm_profile_id": job.llm_profile_id,
            "previous_deleted_at": previous_deleted_at.isoformat() if previous_deleted_at else None,
        },
    )
    await session.commit()
    await session.refresh(job)
    return _serialize_crawl_job(job)


@router.post("/{job_id}/restore", response_model=CrawlJobRead)
async def restore_crawl_job(
    job_id: int,
    session: AsyncSession = Depends(get_async_session),
) -> CrawlJobRead:
    job = await _get_crawl_job_or_404(session, job_id)
    previous_deleted_at = job.deleted_at
    if job.deleted_at is not None:
        job.deleted_at = None
        job.updated_at = datetime.now(UTC)
    await record_operation_log(
        session,
        category="crawl",
        event_name="crawl_job.restored",
        entity_type="crawl_job",
        entity_id=str(job.id),
        metadata={
            "status": job.status,
            "llm_profile_id": job.llm_profile_id,
            "previous_deleted_at": previous_deleted_at.isoformat() if previous_deleted_at else None,
        },
    )
    await session.commit()
    await session.refresh(job)
    return _serialize_crawl_job(job)
```

如果文件里的获取函数名称不同，复用现有详情接口使用的获取函数，不新增重复查询逻辑。

- [ ] **步骤 5：运行抓取 API 测试验证通过**

运行：

```powershell
rtk uv run python -m unittest test.test_crawl_jobs_api.CrawlJobsApiTests.test_crawl_job_delete_restore_and_trash_view
```

工作目录：`backend`

预期：PASS。

- [ ] **步骤 6：Commit**

```powershell
rtk git add backend/app/schemas/crawl_job.py backend/app/api/crawl_jobs.py backend/test/test_crawl_jobs_api.py
rtk git commit -m "feat(任务中心): 支持抓取任务回收站接口"
```

## 任务 4：匹配分析任务回收站 API

**文件：**
- 修改：`backend/app/schemas/match_analysis_job.py`
- 修改：`backend/app/api/match_analysis_jobs.py`
- 修改：`backend/test/test_api_endpoints.py`

- [ ] **步骤 1：编写失败的匹配分析 API 测试**

在 `backend/test/test_api_endpoints.py` 添加：

```python
def test_match_analysis_job_delete_restore_and_trash_view(self) -> None:
    identity_id = self._create_identity(with_imap=False)
    llm_id = self._create_llm()
    self._upload_material(
        identity_id,
        filename="resume.txt",
        content=b"AI systems",
        material_type="resume",
    )
    professor_response = self.client.post(
        "/api/professors",
        json={
            "name": "回收站导师",
            "email": "trash-match@example.edu",
            "title": "Professor",
            "university": "Example University",
            "school": "School of Computing",
            "department": "Computer Science",
            "research_direction": "AI agents",
            "recent_papers": ["Agent paper"],
            "profile_url": None,
            "source_url": None,
        },
    )
    self.assertEqual(professor_response.status_code, 201, msg=professor_response.text)
    created = self.client.post(
        "/api/match-analysis-jobs",
        json={
            "identity_id": identity_id,
            "llm_profile_id": llm_id,
            "professor_ids": [professor_response.json()["id"]],
        },
    )
    self.assertEqual(created.status_code, 201, msg=created.text)
    job_id = created.json()["id"]

    blocked = self.client.post(f"/api/match-analysis-jobs/{job_id}/delete")
    self.assertEqual(blocked.status_code, 400)
    self.assertIn("请先中止/取消任务后再删除", blocked.json()["detail"])

    canceled = self.client.post(f"/api/match-analysis-jobs/{job_id}/cancel")
    self.assertEqual(canceled.status_code, 200, msg=canceled.text)
    deleted = self.client.post(f"/api/match-analysis-jobs/{job_id}/delete")
    self.assertEqual(deleted.status_code, 200, msg=deleted.text)
    self.assertIsNotNone(deleted.json()["job"]["deleted_at"])

    current = self.client.get(
        "/api/match-analysis-jobs",
        params={"identity_id": identity_id, "llm_profile_id": llm_id},
    )
    self.assertEqual(current.status_code, 200)
    self.assertEqual(current.json(), [])

    trash = self.client.get(
        "/api/match-analysis-jobs",
        params={"identity_id": identity_id, "llm_profile_id": llm_id, "view": "trash"},
    )
    self.assertEqual(trash.status_code, 200)
    self.assertEqual([item["id"] for item in trash.json()], [job_id])

    restored = self.client.post(f"/api/match-analysis-jobs/{job_id}/restore")
    self.assertEqual(restored.status_code, 200, msg=restored.text)
    self.assertIsNone(restored.json()["job"]["deleted_at"])
```

- [ ] **步骤 2：运行测试验证失败**

运行：

```powershell
rtk uv run python -m unittest test.test_api_endpoints.ApiEndpointTests.test_match_analysis_job_delete_restore_and_trash_view
```

工作目录：`backend`

预期：FAIL，删除接口不存在或响应缺少 `deleted_at`。

- [ ] **步骤 3：扩展匹配分析 schema**

在 `backend/app/schemas/match_analysis_job.py` 的 `MatchAnalysisJobRead` 增加：

```python
deleted_at: datetime | None
```

- [ ] **步骤 4：实现列表过滤、删除和恢复接口**

在 `backend/app/api/match_analysis_jobs.py` 列表接口增加 `view: str = "current"`，查询加入：

```python
if view == "trash":
    statement = statement.where(MatchAnalysisJob.deleted_at.is_not(None))
elif view == "current":
    statement = statement.where(MatchAnalysisJob.deleted_at.is_(None))
else:
    raise HTTPException(status_code=400, detail="未知任务视图")
```

序列化返回增加：

```python
deleted_at=job.deleted_at,
```

新增接口：

```python
MATCH_ANALYSIS_JOB_DELETABLE_STATUSES = {
    MatchAnalysisJobStatus.COMPLETED.value,
    MatchAnalysisJobStatus.PARTIAL_FAILED.value,
    MatchAnalysisJobStatus.FAILED.value,
    MatchAnalysisJobStatus.CANCELED.value,
}


@router.post("/{job_id}/delete", response_model=MatchAnalysisJobActionResponse)
async def delete_match_analysis_job(
    job_id: int,
    session: AsyncSession = Depends(get_async_session),
) -> MatchAnalysisJobActionResponse:
    job = await _get_match_analysis_job_or_404(session, job_id)
    if job.status not in MATCH_ANALYSIS_JOB_DELETABLE_STATUSES:
        raise HTTPException(status_code=400, detail="请先中止/取消任务后再删除")
    previous_deleted_at = job.deleted_at
    if job.deleted_at is None:
        job.deleted_at = datetime.now(UTC)
        job.updated_at = datetime.now(UTC)
    await record_operation_log(
        session,
        category="match_analysis",
        event_name="match_analysis_job.deleted",
        entity_type="match_analysis_job",
        entity_id=str(job.id),
        metadata={
            "status": job.status,
            "identity_id": job.identity_id,
            "llm_profile_id": job.llm_profile_id,
            "previous_deleted_at": previous_deleted_at.isoformat() if previous_deleted_at else None,
        },
    )
    await session.commit()
    await session.refresh(job)
    return MatchAnalysisJobActionResponse(ok=True, job=_serialize_match_analysis_job(job))


@router.post("/{job_id}/restore", response_model=MatchAnalysisJobActionResponse)
async def restore_match_analysis_job(
    job_id: int,
    session: AsyncSession = Depends(get_async_session),
) -> MatchAnalysisJobActionResponse:
    job = await _get_match_analysis_job_or_404(session, job_id)
    previous_deleted_at = job.deleted_at
    if job.deleted_at is not None:
        job.deleted_at = None
        job.updated_at = datetime.now(UTC)
    await record_operation_log(
        session,
        category="match_analysis",
        event_name="match_analysis_job.restored",
        entity_type="match_analysis_job",
        entity_id=str(job.id),
        metadata={
            "status": job.status,
            "identity_id": job.identity_id,
            "llm_profile_id": job.llm_profile_id,
            "previous_deleted_at": previous_deleted_at.isoformat() if previous_deleted_at else None,
        },
    )
    await session.commit()
    await session.refresh(job)
    return MatchAnalysisJobActionResponse(ok=True, job=_serialize_match_analysis_job(job))
```

如果现有文件没有 `_get_match_analysis_job_or_404` 或 `_serialize_match_analysis_job`，按文件中的既有 helper 名称改写，避免重复新建一套序列化。

- [ ] **步骤 5：运行匹配分析 API 测试验证通过**

运行：

```powershell
rtk uv run python -m unittest test.test_api_endpoints.ApiEndpointTests.test_match_analysis_job_delete_restore_and_trash_view
```

工作目录：`backend`

预期：PASS。

- [ ] **步骤 6：Commit**

```powershell
rtk git add backend/app/schemas/match_analysis_job.py backend/app/api/match_analysis_jobs.py backend/test/test_api_endpoints.py
rtk git commit -m "feat(任务中心): 支持匹配任务回收站接口"
```

## 任务 5：运行时调度排除已删除任务

**文件：**
- 修改：`backend/app/services/task_runtime.py`
- 修改：`backend/app/services/crawl_job_runtime.py`
- 修改：`backend/app/services/match_analysis_job_runtime.py`
- 修改：`backend/test/test_batch_task_dispatch_schedule.py`
- 修改：`backend/test/test_crawl_job_runtime.py`
- 修改：`backend/test/test_match_analysis_jobs.py`

- [ ] **步骤 1：编写失败的调度测试**

分别添加 3 个测试。

批量邮件测试核心断言：

```python
def test_dispatch_ignores_deleted_batch_task(self) -> None:
    # 复用本文件现有 seed helper 创建 running 批量任务和待处理 EmailTask。
    # 将 batch_task.deleted_at 写为当前时间。
    # 调用现有调度入口。
    # 断言没有发送或生成任何任务，待处理任务状态保持不变。
```

抓取任务测试核心断言：

```python
def test_run_queued_crawl_jobs_ignores_deleted_job(self) -> None:
    job = CrawlJob(
        university="示例大学",
        school="计算机学院",
        start_url="https://example.edu/faculty",
        status=CrawlJobStatus.QUEUED.value,
        deleted_at=datetime.now(UTC),
    )
    session.add(job)
    await session.commit()

    processed = await run_queued_crawl_jobs_once(self.session_factory)

    self.assertEqual(processed, 0)
```

匹配分析测试核心断言：

```python
def test_run_queued_match_analysis_jobs_ignores_deleted_job(self) -> None:
    identity_id, llm_profile_id, professor_ids = self._run_async(self._seed_create_job_data())
    job = self._run_async(
        create_match_analysis_job(
            self.session_factory,
            identity_id=identity_id,
            llm_profile_id=llm_profile_id,
            professor_ids=[professor_ids[0]],
            name="已删除匹配任务",
        ),
    )
    self._run_async(self._mark_match_job_deleted(job.id))

    processed = self._run_async(run_queued_match_analysis_jobs_once(self.session_factory))

    self.assertEqual(processed, 0)
```

如果现有测试文件没有对应 helper，新增最小 helper：

```python
async def _mark_match_job_deleted(self, job_id: int) -> None:
    async with self.session_factory() as session:
        job = await session.get(MatchAnalysisJob, job_id)
        assert job is not None
        job.deleted_at = datetime.now(UTC)
        await session.commit()
```

- [ ] **步骤 2：运行测试验证失败**

运行：

```powershell
rtk uv run python -m unittest test.test_crawl_job_runtime.CrawlJobRuntimeTests.test_run_queued_crawl_jobs_ignores_deleted_job
rtk uv run python -m unittest test.test_match_analysis_jobs.MatchAnalysisJobRuntimeTests.test_run_queued_match_analysis_jobs_ignores_deleted_job
```

工作目录：`backend`

预期：至少一个测试 FAIL，表现为已删除任务仍被处理。

- [ ] **步骤 3：实现运行时查询过滤**

在 `backend/app/services/task_runtime.py` 的批量任务查询中加入：

```python
BatchTask.deleted_at.is_(None),
```

在 `backend/app/services/crawl_job_runtime.py` 所有选择 queued 任务的位置加入：

```python
CrawlJob.deleted_at.is_(None),
```

在 `backend/app/services/match_analysis_job_runtime.py` 所有选择 queued/running 任务继续推进的位置加入：

```python
MatchAnalysisJob.deleted_at.is_(None),
```

重点检查这些形态：

```python
.where(MatchAnalysisJob.status == MatchAnalysisJobStatus.QUEUED.value)
.where(MatchAnalysisJob.status == MatchAnalysisJobStatus.RUNNING.value)
```

- [ ] **步骤 4：运行调度测试验证通过**

运行：

```powershell
rtk uv run python -m unittest test.test_batch_task_dispatch_schedule test.test_crawl_job_runtime.CrawlJobRuntimeTests.test_run_queued_crawl_jobs_ignores_deleted_job test.test_match_analysis_jobs.MatchAnalysisJobRuntimeTests.test_run_queued_match_analysis_jobs_ignores_deleted_job
```

工作目录：`backend`

预期：PASS。

- [ ] **步骤 5：Commit**

```powershell
rtk git add backend/app/services/task_runtime.py backend/app/services/crawl_job_runtime.py backend/app/services/match_analysis_job_runtime.py backend/test/test_batch_task_dispatch_schedule.py backend/test/test_crawl_job_runtime.py backend/test/test_match_analysis_jobs.py
rtk git commit -m "fix(任务中心): 调度器跳过回收站任务"
```

## 任务 6：前端类型和 API 客户端

**文件：**
- 修改：`frontend/src/types/index.ts`
- 修改：`frontend/src/lib/api/batchTasksApi.ts`
- 修改：`frontend/src/lib/api/crawlJobsApi.ts`
- 修改：`frontend/src/lib/api/matchAnalysisJobsApi.ts`

- [ ] **步骤 1：编写类型使用检查**

先在 API 文件中按目标用法写调用代码，让 TypeScript 暴露缺失类型。目标签名：

```ts
export type TaskListView = 'current' | 'trash';
```

```ts
export const deleteBatchTask = (taskId: number) =>
  apiFetch<{ ok: boolean; task: BatchTaskCardDTO }>(`/api/batch-tasks/${taskId}/delete`, {
    method: 'POST',
  });
```

- [ ] **步骤 2：运行 TypeScript 验证失败**

运行：

```powershell
rtk npm run build
```

工作目录：`frontend`

预期：FAIL，缺少 `TaskListView` 或 DTO 的 `deleted_at` 字段。

- [ ] **步骤 3：补齐类型**

在 `frontend/src/types/index.ts` 增加：

```ts
export type TaskListView = 'current' | 'trash';
```

为 `BatchTaskCardDTO`、`CrawlJobSummaryDTO`、`MatchAnalysisJobDTO` 增加：

```ts
deleted_at: string | null;
```

- [ ] **步骤 4：补齐 API 客户端**

批量任务：

```ts
export const listBatchTasks = (params?: {
  identityId?: number | null;
  llmProfileId?: number | null;
  view?: TaskListView;
}) =>
  apiFetch<BatchTaskCardDTO[]>(
    '/api/batch-tasks',
    undefined,
    {
      identity_id: params?.identityId ?? undefined,
      llm_profile_id: params?.llmProfileId ?? undefined,
      view: params?.view ?? undefined,
    },
  );
```

抓取任务：

```ts
export const listCrawlJobs = (params: { limit?: number; view?: TaskListView } = {}) =>
  apiFetch<CrawlJobSummaryDTO[]>('/api/crawl-jobs', undefined, {
    limit: params.limit,
    view: params.view,
  });
```

匹配分析：

```ts
export const listMatchAnalysisJobs = (params?: {
  identityId?: number | null;
  llmProfileId?: number | null;
  view?: TaskListView;
}) =>
  apiFetch<MatchAnalysisJobDTO[]>(
    '/api/match-analysis-jobs',
    undefined,
    {
      identity_id: params?.identityId ?? undefined,
      llm_profile_id: params?.llmProfileId ?? undefined,
      view: params?.view ?? undefined,
    },
  );
```

并分别增加 `delete*`、`restore*` 函数。

- [ ] **步骤 5：运行构建验证通过**

运行：

```powershell
rtk npm run build
```

工作目录：`frontend`

预期：PASS。

- [ ] **步骤 6：Commit**

```powershell
rtk git add frontend/src/types/index.ts frontend/src/lib/api/batchTasksApi.ts frontend/src/lib/api/crawlJobsApi.ts frontend/src/lib/api/matchAnalysisJobsApi.ts
rtk git commit -m "feat(任务中心): 增加回收站前端接口"
```

## 任务 7：任务中心 UI

**文件：**
- 修改：`frontend/src/pages/TasksPage.tsx`
- 修改：`frontend/src/pages/TasksPage.test.tsx`

- [ ] **步骤 1：编写失败的组件测试**

扩展 `frontend/src/pages/TasksPage.test.tsx`，让 `CrawlJobCard` 支持回收站动作测试。先把组件 props 目标写出来：

```tsx
render(
  <CrawlJobCard
    job={{ ...buildCrawlJob(), deleted_at: null }}
    listView="current"
    pausingCrawlJobId={null}
    resumingCrawlJobId={null}
    retryingCrawlJobId={null}
    resumingCrawlJobReviewId={null}
    onOpenDetails={vi.fn()}
    onPause={vi.fn()}
    onResume={vi.fn()}
    onCancel={vi.fn()}
    onRetry={vi.fn()}
    onResumeReview={vi.fn()}
    onDelete={vi.fn()}
    onRestore={vi.fn()}
    formatUpdatedAt={() => "05/01 14:49:02"}
  />,
);

expect(screen.getByRole("button", { name: "删除" })).toBeInTheDocument();
expect(screen.queryByRole("button", { name: "恢复" })).not.toBeInTheDocument();
```

再加回收站视图：

```tsx
render(
  <CrawlJobCard
    job={{ ...buildCrawlJob(), deleted_at: "2026-05-07T10:00:00" }}
    listView="trash"
    pausingCrawlJobId={null}
    resumingCrawlJobId={null}
    retryingCrawlJobId={null}
    resumingCrawlJobReviewId={null}
    onOpenDetails={vi.fn()}
    onPause={vi.fn()}
    onResume={vi.fn()}
    onCancel={vi.fn()}
    onRetry={vi.fn()}
    onResumeReview={vi.fn()}
    onDelete={vi.fn()}
    onRestore={vi.fn()}
    formatUpdatedAt={() => "05/01 14:49:02"}
  />,
);

expect(screen.getByRole("button", { name: "恢复" })).toBeInTheDocument();
expect(screen.queryByRole("button", { name: "删除" })).not.toBeInTheDocument();
expect(screen.queryByRole("button", { name: "重试" })).not.toBeInTheDocument();
```

- [ ] **步骤 2：运行前端测试验证失败**

运行：

```powershell
rtk npm run test -- TasksPage.test.tsx
```

工作目录：`frontend`

预期：FAIL，`CrawlJobCard` 缺少新 props 或按钮不存在。

- [ ] **步骤 3：实现列表视图状态和加载参数**

在 `TasksPage.tsx` 增加：

```ts
type TaskListViews = Record<TasksTab, TaskListView>;

const [taskListViews, setTaskListViews] = useState<TaskListViews>({
  batch: "current",
  crawl: "current",
  match: "current",
});

const activeTaskListView = taskListViews[activeTab];
```

加载列表时传入 `view`：

```ts
const data = await listBatchTasks({
  identityId: selectedIdentityId,
  llmProfileId: selectedLlmProfileId,
  view: taskListViews.batch,
});
```

抓取和匹配同理。

把对应 `useCallback` 依赖补上 `taskListViews.batch`、`taskListViews.crawl`、`taskListViews.match`。

- [ ] **步骤 4：实现当前/回收站切换**

在分类页签下方、列表上方增加切换按钮：

```tsx
<div className="mt-4 inline-flex gap-1 rounded-2xl border border-stone-200 bg-white p-1 shadow-sm">
  {(["current", "trash"] as TaskListView[]).map((view) => (
    <button
      key={view}
      type="button"
      onClick={() =>
        setTaskListViews((current) => ({ ...current, [activeTab]: view }))
      }
      className={
        activeTaskListView === view
          ? "inline-flex min-h-9 items-center rounded-xl bg-stone-900 px-4 text-sm font-medium text-white"
          : "inline-flex min-h-9 items-center rounded-xl px-4 text-sm font-medium text-stone-600 hover:bg-stone-50"
      }
    >
      {view === "current" ? "当前任务" : "回收站"}
    </button>
  ))}
</div>
```

- [ ] **步骤 5：实现删除和恢复处理函数**

为 3 类任务分别新增处理函数。批量任务示例：

```ts
const handleDeleteBatchTask = useCallback(
  async (task: BatchTaskCardDTO) => {
    const accepted = await confirm({
      title: "删除任务",
      description: "删除后会移入回收站，不会清除任务记录，可在回收站恢复。",
      confirmText: "删除",
      tone: "danger",
    });
    if (!accepted) {
      return;
    }
    try {
      await deleteBatchTask(task.id);
      notifySuccess("已移入回收站");
      if (selectedBatchTask?.id === task.id) {
        closeBatchTaskDetails();
      }
      await loadTasks();
    } catch (error) {
      notifyError("删除任务失败", error instanceof Error ? error.message : "删除任务失败");
    }
  },
  [closeBatchTaskDetails, confirm, loadTasks, notifyError, notifySuccess, selectedBatchTask?.id],
);
```

恢复示例：

```ts
const handleRestoreBatchTask = useCallback(
  async (taskId: number) => {
    try {
      await restoreBatchTask(taskId);
      notifySuccess("已恢复任务");
      await loadTasks();
    } catch (error) {
      notifyError("恢复任务失败", error instanceof Error ? error.message : "恢复任务失败");
    }
  },
  [loadTasks, notifyError, notifySuccess],
);
```

抓取和匹配复用同样结构，分别调用对应 API 和加载函数。

- [ ] **步骤 6：调整卡片操作区**

当前列表：

- 已结束任务显示「删除」。
- 未结束任务不显示「删除」。
- 保留现有暂停、继续、中止、取消、重试、详情。

回收站列表：

- 只显示「恢复」和「查看详情」。
- 不显示暂停、继续、中止、取消、重试。

可删除判断：

```ts
const canDeleteBatchTask = (task: BatchTaskCardDTO) =>
  task.status === "stopped" || task.status === "completed";

const canDeleteCrawlJob = (job: CrawlJobSummaryDTO) =>
  job.status === "completed" || job.status === "failed" || job.status === "canceled";

const canDeleteMatchJob = (job: MatchAnalysisJobDTO) =>
  job.status === "completed" ||
  job.status === "partial_failed" ||
  job.status === "failed" ||
  job.status === "canceled";
```

- [ ] **步骤 7：调整空状态和统计文案**

当前列表空状态保持现有文案。回收站空状态使用：

```tsx
回收站暂无任务。
```

顶部统计继续使用当前列表数据，不额外查询回收站数量。切换到回收站时统计区仍显示当前任务统计，避免误把回收站任务算入运行中或待处理。

- [ ] **步骤 8：运行前端测试和 lint**

运行：

```powershell
rtk npm run test -- TasksPage.test.tsx
rtk npm run lint
```

工作目录：`frontend`

预期：PASS。

- [ ] **步骤 9：Commit**

```powershell
rtk git add frontend/src/pages/TasksPage.tsx frontend/src/pages/TasksPage.test.tsx
rtk git commit -m "feat(任务中心): 增加当前任务和回收站切换"
```

## 任务 8：端到端验证和收尾

**文件：**
- 修改：按验证发现的问题最小范围调整。

- [ ] **步骤 1：运行后端回归测试**

运行：

```powershell
rtk uv run python -m unittest test.test_api_endpoints test.test_crawl_jobs_api test.test_database_schema
```

工作目录：`backend`

预期：PASS。

- [ ] **步骤 2：运行前端构建和 lint**

运行：

```powershell
rtk npm run lint
rtk npm run build
```

工作目录：`frontend`

预期：PASS。

- [ ] **步骤 3：启动本地服务做手动验证**

后端：

```powershell
rtk uv run python dev_entry.py
```

工作目录：`backend`

前端：

```powershell
rtk npm run dev
```

工作目录：`frontend`

手动验证：

- 批量邮件任务中止后删除，进入回收站，查看详情，恢复。
- 教师抓取任务取消后删除，进入回收站，查看详情，恢复。
- 匹配分析任务取消后删除，进入回收站，查看详情，恢复。
- 当前任务统计不包含回收站任务。

- [ ] **步骤 4：检查工作区只包含本功能变更**

运行：

```powershell
rtk git status --short
rtk git diff --stat
```

预期：只有任务中心回收站相关文件有改动；如果存在用户已有改动，不要暂存或覆盖。

- [ ] **步骤 5：最终 commit**

如果步骤 1-4 产生修复改动：

```powershell
rtk git add <本功能相关文件>
rtk git commit -m "fix(任务中心): 完善回收站验证问题"
```

如果没有新改动，跳过提交。

## 自检

- 规格覆盖度：计划覆盖数据字段、列表过滤、删除/恢复接口、幂等行为、运行时排除、前端当前/回收站切换、删除/恢复文案、统计规则和验证路径。
- 红旗扫描：计划中没有禁用的空泛写法。
- 类型一致性：统一使用 `deleted_at`、`TaskListView = "current" | "trash"`、`view=current|trash`、`delete*` 和 `restore*` API 命名。
- 范围控制：不做物理删除，不做跨类型统一回收站，不引入回收站数量聚合接口。
