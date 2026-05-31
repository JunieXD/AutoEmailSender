# 智能抓取暂停与继续实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 为智能抓取任务增加可恢复的暂停/继续能力，同时保留现有取消作为终止动作。

**架构：** 后端新增 `paused` 状态和 `pause/resume` API，运行时通过协作式检查点停止推进，继续时把任务重新放回 `queued`。前端在任务页同时展示「暂停」「继续」「取消」，并通过不同确认文案区分临时暂停和终止取消。

**技术栈：** FastAPI、SQLAlchemy async、Pydantic、Python `unittest`、Vite、React、TypeScript。

---

## 规格依据

实现必须覆盖以下规格：

`@docs/superpowers/specs/2026-04-28-crawl-job-pause-resume-design.md`

## 文件结构

- 修改：`backend/app/models/crawl_job.py`
  职责：新增 `CrawlJobStatus.PAUSED`。
- 修改：`backend/app/schemas/crawl_job.py`
  职责：让 API DTO 接受并返回 `paused`。
- 修改：`backend/app/services/crawl_job_events.py`
  职责：为 `paused` 提供任务事件文案。
- 修改：`backend/app/api/crawl_jobs.py`
  职责：新增暂停和继续接口，调整取消接口允许取消暂停任务。
- 修改：`backend/app/services/crawler_tools.py`
  职责：统一检查抓取任务控制状态，在页面记录和候选保存处支持暂停。
- 修改：`backend/app/services/crawl_job_runtime.py`
  职责：运行时捕获暂停和取消信号，避免误标失败，候选补全阶段响应暂停。
- 修改：`backend/test/test_crawl_jobs_api.py`
  职责：覆盖 pause/resume API、状态流转、数据保留。
- 修改：`backend/test/test_crawl_job_runtime.py`
  职责：覆盖 worker 不领取 paused、运行中暂停不变 failed、继续保留结果。
- 修改：`backend/test/test_crawler_tools.py`
  职责：覆盖工具层遇到 paused 时不写入新页面或候选。
- 修改：`frontend/src/types/index.ts`
  职责：前端 DTO 增加 `paused`。
- 修改：`frontend/src/lib/api/crawlJobsApi.ts`
  职责：新增 `pauseCrawlJob`、`resumeCrawlJob`。
- 修改：`frontend/src/pages/TasksPage.tsx`
  职责：任务页展示暂停、继续、取消，使用不同确认文案。

## 实现原则

- 暂停是临时状态，取消是终止状态，两个动作都必须保留。
- 暂停不能清空 `crawl_pages`、`crawl_candidates`、`agent_trace`。
- 继续只允许从 `paused` 到 `queued`，不复用重试接口。
- 运行时发现暂停时不能写成 `failed`。
- 第一版不做 DeepAgents 内部状态精确恢复。

### 任务 1：后端状态和 API 测试

**文件：**
- 修改：`backend/test/test_crawl_jobs_api.py`

- [ ] **步骤 1：编写 pause/resume 成功路径测试**

在 `CrawlJobsApiTests` 中添加测试：

```python
def test_pause_resume_crawl_job_flow_preserves_saved_data(self) -> None:
    create_response = self.client.post(
        "/api/crawl-jobs",
        json={
            "university": "示例大学",
            "school": "计算机学院",
            "start_url": "https://example.edu/faculty",
            "llm_profile_id": None,
        },
    )
    self.assertEqual(create_response.status_code, 201, msg=create_response.text)
    job_id = create_response.json()["id"]
    self._seed_page_and_candidates(job_id)

    pause_response = self.client.post(f"/api/crawl-jobs/{job_id}/pause")

    self.assertEqual(pause_response.status_code, 200, msg=pause_response.text)
    self.assertEqual(pause_response.json()["status"], "paused")

    detail_response = self.client.get(f"/api/crawl-jobs/{job_id}")
    self.assertEqual(detail_response.status_code, 200)
    self.assertEqual(detail_response.json()["page_count"], 1)
    self.assertEqual(detail_response.json()["candidate_count"], 3)

    resume_response = self.client.post(f"/api/crawl-jobs/{job_id}/resume")

    self.assertEqual(resume_response.status_code, 200, msg=resume_response.text)
    self.assertEqual(resume_response.json()["status"], "queued")

    resumed_detail_response = self.client.get(f"/api/crawl-jobs/{job_id}")
    self.assertEqual(resumed_detail_response.json()["page_count"], 1)
    self.assertEqual(resumed_detail_response.json()["candidate_count"], 3)
```

- [ ] **步骤 2：编写非法状态测试**

在同一个测试类中添加：

```python
def test_pause_rejects_terminal_or_review_jobs(self) -> None:
    for status in ("needs_review", "completed", "failed", "canceled"):
        with self.subTest(status=status):
            create_response = self.client.post(
                "/api/crawl-jobs",
                json={
                    "university": "示例大学",
                    "school": "计算机学院",
                    "start_url": "https://example.edu/faculty",
                    "llm_profile_id": None,
                },
            )
            job_id = create_response.json()["id"]
            self._set_job_status(job_id, status)

            response = self.client.post(f"/api/crawl-jobs/{job_id}/pause")

            self.assertEqual(response.status_code, 409, msg=response.text)

def test_resume_rejects_non_paused_job(self) -> None:
    create_response = self.client.post(
        "/api/crawl-jobs",
        json={
            "university": "示例大学",
            "school": "计算机学院",
            "start_url": "https://example.edu/faculty",
            "llm_profile_id": None,
        },
    )
    job_id = create_response.json()["id"]

    response = self.client.post(f"/api/crawl-jobs/{job_id}/resume")

    self.assertEqual(response.status_code, 409)
```

- [ ] **步骤 3：编写暂停后仍可取消测试**

添加测试：

```python
def test_paused_crawl_job_can_be_canceled(self) -> None:
    create_response = self.client.post(
        "/api/crawl-jobs",
        json={
            "university": "示例大学",
            "school": "计算机学院",
            "start_url": "https://example.edu/faculty",
            "llm_profile_id": None,
        },
    )
    job_id = create_response.json()["id"]
    self.client.post(f"/api/crawl-jobs/{job_id}/pause")

    response = self.client.post(f"/api/crawl-jobs/{job_id}/cancel")

    self.assertEqual(response.status_code, 200, msg=response.text)
    self.assertEqual(response.json()["status"], "canceled")
```

- [ ] **步骤 4：运行 API 测试验证失败**

运行：

```bash
cd backend
uv run python -m unittest test.test_crawl_jobs_api.CrawlJobsApiTests.test_pause_resume_crawl_job_flow_preserves_saved_data test.test_crawl_jobs_api.CrawlJobsApiTests.test_pause_rejects_terminal_or_review_jobs test.test_crawl_jobs_api.CrawlJobsApiTests.test_resume_rejects_non_paused_job test.test_crawl_jobs_api.CrawlJobsApiTests.test_paused_crawl_job_can_be_canceled
```

预期：失败，原因包含 `404 Not Found` 或状态不支持 `paused`。

### 任务 2：实现后端状态和 API

**文件：**
- 修改：`backend/app/models/crawl_job.py`
- 修改：`backend/app/schemas/crawl_job.py`
- 修改：`backend/app/services/crawl_job_events.py`
- 修改：`backend/app/api/crawl_jobs.py`

- [ ] **步骤 1：新增后端状态枚举和 DTO**

在 `CrawlJobStatus` 中增加：

```python
PAUSED = "paused"
```

将 `CrawlJobStatusDTO` 改为包含 `paused`：

```python
CrawlJobStatusDTO = Literal[
    "queued",
    "running",
    "paused",
    "needs_review",
    "completed",
    "failed",
    "canceled",
]
```

在 `STATUS_MESSAGES` 中增加：

```python
"paused": "任务已暂停",
```

- [ ] **步骤 2：新增暂停接口**

在 `backend/app/api/crawl_jobs.py` 中新增：

```python
@router.post("/{job_id}/pause", response_model=CrawlJobRead)
async def pause_crawl_job(
    job_id: int,
    session: AsyncSession = Depends(get_async_session),
) -> CrawlJob:
    job = await _get_crawl_job_or_404(session, job_id)
    if job.status == CrawlJobStatus.PAUSED.value:
        return job
    if job.status not in {CrawlJobStatus.QUEUED.value, CrawlJobStatus.RUNNING.value}:
        raise HTTPException(status_code=409, detail="仅允许暂停排队中或运行中的抓取任务")

    job.status = CrawlJobStatus.PAUSED.value
    job.updated_at = datetime.now(UTC)
    await record_operation_log(
        session,
        category="crawler",
        event_name="crawl_job.paused",
        entity_type="crawl_job",
        entity_id=str(job.id),
        metadata={"status": job.status},
    )
    await session.commit()
    await session.refresh(job)
    return job
```

- [ ] **步骤 3：新增继续接口**

在同一文件中新增：

```python
@router.post("/{job_id}/resume", response_model=CrawlJobRead)
async def resume_crawl_job(
    job_id: int,
    session: AsyncSession = Depends(get_async_session),
) -> CrawlJob:
    job = await _get_crawl_job_or_404(session, job_id)
    if job.status != CrawlJobStatus.PAUSED.value:
        raise HTTPException(status_code=409, detail="仅允许继续已暂停的抓取任务")

    job.status = CrawlJobStatus.QUEUED.value
    job.error_message = None
    job.updated_at = datetime.now(UTC)
    await record_operation_log(
        session,
        category="crawler",
        event_name="crawl_job.resumed",
        entity_type="crawl_job",
        entity_id=str(job.id),
        metadata={"status": job.status},
    )
    await session.commit()
    await session.refresh(job)
    return job
```

- [ ] **步骤 4：调整取消接口终态判断**

把取消接口中的终态判断改为：

```python
if job.status in {
    CrawlJobStatus.COMPLETED.value,
    CrawlJobStatus.FAILED.value,
    CrawlJobStatus.CANCELED.value,
}:
    return job
```

这样 `paused` 仍可被取消，`canceled` 保持幂等。

- [ ] **步骤 5：运行 API 测试验证通过**

运行：

```bash
cd backend
uv run python -m unittest test.test_crawl_jobs_api.CrawlJobsApiTests.test_pause_resume_crawl_job_flow_preserves_saved_data test.test_crawl_jobs_api.CrawlJobsApiTests.test_pause_rejects_terminal_or_review_jobs test.test_crawl_jobs_api.CrawlJobsApiTests.test_resume_rejects_non_paused_job test.test_crawl_jobs_api.CrawlJobsApiTests.test_paused_crawl_job_can_be_canceled
```

预期：所有测试通过。

- [ ] **步骤 6：Commit**

```bash
git add backend/app/models/crawl_job.py backend/app/schemas/crawl_job.py backend/app/services/crawl_job_events.py backend/app/api/crawl_jobs.py backend/test/test_crawl_jobs_api.py
git commit -m "feat(crawler): add pause and resume API"
```

### 任务 3：运行时暂停控制测试

**文件：**
- 修改：`backend/test/test_crawl_job_runtime.py`
- 修改：`backend/test/test_crawler_tools.py`

- [ ] **步骤 1：编写 worker 不领取 paused 测试**

在 `CrawlJobRuntimeTests` 添加：

```python
async def test_worker_does_not_claim_paused_crawl_job(self) -> None:
    job_id = await self._create_default_profile_and_job()
    async with self.session_factory() as session:
        job = await session.get(CrawlJob, job_id)
        assert job is not None
        job.status = CrawlJobStatus.PAUSED.value
        await session.commit()

    processed = await run_queued_crawl_jobs_once(self.session_factory)

    self.assertEqual(processed, 0)
    job = await self._get_job(job_id)
    self.assertEqual(job.status, CrawlJobStatus.PAUSED.value)
```

- [ ] **步骤 2：编写运行中暂停不变 failed 测试**

在 `CrawlJobRuntimeTests` 添加：

```python
async def test_running_job_paused_by_tool_stays_paused(self) -> None:
    job_id = await self._create_default_profile_and_job()

    async def fake_run(
        ctx: CrawlToolContext,
        llm_profile: LLMProfile,
        trace_callback=None,
    ) -> dict[str, object]:
        _ = llm_profile, trace_callback
        async with ctx.session_factory() as session:
            job = await session.get(CrawlJob, ctx.job_id)
            assert job is not None
            job.status = CrawlJobStatus.PAUSED.value
            await session.commit()
        from app.services.crawler_tools import CrawlJobPaused

        raise CrawlJobPaused()

    with patch("app.services.crawl_job_runtime.run_faculty_crawler_agent", new=fake_run):
        processed = await run_queued_crawl_jobs_once(self.session_factory)

    self.assertEqual(processed, 1)
    job = await self._get_job(job_id)
    self.assertEqual(job.status, CrawlJobStatus.PAUSED.value)
    self.assertIsNone(job.error_message)
```

- [ ] **步骤 3：编写补全阶段暂停测试**

在 `CrawlJobRuntimeTests` 添加：

```python
async def test_enrichment_stops_when_job_is_paused(self) -> None:
    job_id = await self._create_default_profile_and_job(
        start_url="https://example.edu/faculty",
    )
    enrichment_calls: list[str] = []

    async def fake_run(
        ctx: CrawlToolContext,
        llm_profile: LLMProfile,
        trace_callback=None,
    ) -> dict[str, object]:
        _ = llm_profile, trace_callback
        await save_candidates(
            ctx,
            [
                ProfessorCandidatePayload(
                    name="张三",
                    email="zhang@example.edu",
                    profile_url="https://example.edu/zhang",
                )
            ],
        )
        async with ctx.session_factory() as session:
            job = await session.get(CrawlJob, ctx.job_id)
            assert job is not None
            job.status = CrawlJobStatus.PAUSED.value
            await session.commit()
        return {}

    async def fake_crawl_page_with_crawl4ai(
        ctx: CrawlToolContext,
        url: str,
    ) -> PageSnapshot:
        _ = ctx
        enrichment_calls.append(url)
        return PageSnapshot(
            url=url,
            title="张三",
            text="院系：计算机学院",
            html="<html></html>",
            links=[],
            fetch_method="http",
            status="succeeded",
        )

    with patch(
        "app.services.crawl_job_runtime.run_faculty_crawler_agent",
        new=fake_run,
    ), patch(
        "app.services.crawl_job_runtime.crawl_page_with_crawl4ai",
        new=fake_crawl_page_with_crawl4ai,
    ):
        processed = await run_queued_crawl_jobs_once(self.session_factory)

    self.assertEqual(processed, 1)
    self.assertEqual(enrichment_calls, [])
    job = await self._get_job(job_id)
    self.assertEqual(job.status, CrawlJobStatus.PAUSED.value)
```

- [ ] **步骤 4：编写工具层 paused 跳过写入测试**

在 `CrawlerHttpToolTests` 添加：

```python
async def test_save_candidates_skips_paused_job(self) -> None:
    session_factory = _FakeSessionFactory(job_status="paused")
    ctx = CrawlToolContext(
        job_id=1,
        start_url="https://cs.example.edu/faculty",
        university="示例大学",
        school="计算机学院",
        session_factory=session_factory,  # type: ignore[arg-type]
    )

    saved = await save_candidates(
        ctx,
        [ProfessorCandidatePayload(name="张三", email="zhang@example.edu")],
    )

    self.assertEqual(saved, [])
    self.assertEqual(session_factory.added, [])

async def test_record_page_snapshot_skips_paused_job(self) -> None:
    session_factory = _FakeSessionFactory(job_status="paused")
    ctx = CrawlToolContext(
        job_id=1,
        start_url="https://cs.example.edu/faculty",
        university="示例大学",
        school="计算机学院",
        session_factory=session_factory,  # type: ignore[arg-type]
    )

    row = await record_page_snapshot(
        ctx,
        PageSnapshot(
            url="https://cs.example.edu/faculty",
            title="Faculty",
            text="Faculty page",
            fetch_method="http",
            status="succeeded",
        ),
    )

    self.assertIsNone(row)
    self.assertEqual(session_factory.added, [])
```

- [ ] **步骤 5：运行运行时和工具测试验证失败**

运行：

```bash
cd backend
uv run python -m unittest test.test_crawl_job_runtime.CrawlJobRuntimeTests.test_worker_does_not_claim_paused_crawl_job test.test_crawl_job_runtime.CrawlJobRuntimeTests.test_running_job_paused_by_tool_stays_paused test.test_crawl_job_runtime.CrawlJobRuntimeTests.test_enrichment_stops_when_job_is_paused test.test_crawler_tools.CrawlerHttpToolTests.test_save_candidates_skips_paused_job test.test_crawler_tools.CrawlerHttpToolTests.test_record_page_snapshot_skips_paused_job
```

预期：至少部分测试失败，原因是 `CrawlJobPaused` 未定义或工具层未识别 `paused`。

### 任务 4：实现运行时检查点

**文件：**
- 修改：`backend/app/services/crawler_tools.py`
- 修改：`backend/app/services/crawl_job_runtime.py`

- [ ] **步骤 1：在工具层定义暂停和取消信号**

在 `crawler_tools.py` 中 `CrawlToolContext` 附近添加：

```python
class CrawlJobPaused(RuntimeError):
    """Raised internally when a crawl job is paused at a safe checkpoint."""


class CrawlJobCanceled(RuntimeError):
    """Raised internally when a crawl job is canceled at a safe checkpoint."""
```

- [ ] **步骤 2：新增任务控制状态检查函数**

在 `crawler_tools.py` 中替换现有 `_is_crawl_job_canceled` 周边函数：

```python
async def ensure_crawl_job_can_continue(
    session: AsyncSession,
    job_id: int,
) -> None:
    status = await _get_job_status(session, job_id)
    if status == CrawlJobStatus.PAUSED.value:
        raise CrawlJobPaused()
    if status == CrawlJobStatus.CANCELED.value:
        raise CrawlJobCanceled()


async def _is_crawl_job_stopped(session: AsyncSession, job_id: int) -> bool:
    status = await _get_job_status(session, job_id)
    return status in {CrawlJobStatus.PAUSED.value, CrawlJobStatus.CANCELED.value}
```

- [ ] **步骤 3：让保存候选响应 paused**

在 `save_candidates` 中保留当前「遇到 stopped 返回空列表」行为，使用 `_is_crawl_job_stopped` 替代 `_is_crawl_job_canceled`：

```python
if await _is_crawl_job_stopped(session, ctx.job_id):
    return []
```

第二次检查同样替换：

```python
if await _is_crawl_job_stopped(session, ctx.job_id):
    await session.rollback()
    return []
```

- [ ] **步骤 4：让页面记录响应 paused**

在 `record_page_snapshot` 中同样替换：

```python
if await _is_crawl_job_stopped(session, ctx.job_id):
    return None
```

和：

```python
if await _is_crawl_job_stopped(session, ctx.job_id):
    await session.rollback()
    return None
```

- [ ] **步骤 5：运行时捕获暂停和取消信号**

在 `crawl_job_runtime.py` 导入：

```python
from app.services.crawler_tools import (
    CrawlJobCanceled,
    CrawlJobPaused,
    CrawlToolContext,
    CandidateEnrichmentPayload,
    build_candidate_enrichment_prompt,
    crawl_page_with_crawl4ai,
    ensure_crawl_job_can_continue,
    extract_candidate_profile_enrichment,
)
```

在 `run_queued_crawl_jobs_once` 中增加捕获，位置在 `asyncio.CancelledError` 之前：

```python
    try:
        await run_faculty_crawler_agent(ctx, llm_profile, trace_callback=trace_callback)
    except CrawlJobPaused:
        await _emit_trace_event(
            trace_callback,
            {
                "event_type": "job_control",
                "message": "任务已暂停，已保留当前抓取结果",
                "created_at": datetime.now(UTC).isoformat(),
            },
        )
    except CrawlJobCanceled:
        await _emit_trace_event(
            trace_callback,
            {
                "event_type": "job_control",
                "message": "任务已取消",
                "created_at": datetime.now(UTC).isoformat(),
            },
        )
    except asyncio.CancelledError:
        await _mark_job_failed(session_factory, job_id, WORKER_CANCELLED_ERROR)
        raise
```

注意：捕获 `CrawlJobPaused` 和 `CrawlJobCanceled` 后不要执行补全和 `_complete_running_job`。实现时可用 `else` 保留原来的补全和完成逻辑。

- [ ] **步骤 6：候选补全循环检查状态**

在 `_enrich_saved_candidates` 中，每个候选开始前加入：

```python
    for candidate in pending_candidates:
        async with session_factory() as session:
            await ensure_crawl_job_can_continue(session, ctx.job_id)
```

在 `_apply_candidate_enrichment` 写入前加入：

```python
        await ensure_crawl_job_can_continue(session, candidate.job_id)
```

- [ ] **步骤 7：运行运行时和工具测试验证通过**

运行：

```bash
cd backend
uv run python -m unittest test.test_crawl_job_runtime.CrawlJobRuntimeTests.test_worker_does_not_claim_paused_crawl_job test.test_crawl_job_runtime.CrawlJobRuntimeTests.test_running_job_paused_by_tool_stays_paused test.test_crawl_job_runtime.CrawlJobRuntimeTests.test_enrichment_stops_when_job_is_paused test.test_crawler_tools.CrawlerHttpToolTests.test_save_candidates_skips_paused_job test.test_crawler_tools.CrawlerHttpToolTests.test_record_page_snapshot_skips_paused_job
```

预期：所有测试通过。

- [ ] **步骤 8：Commit**

```bash
git add backend/app/services/crawler_tools.py backend/app/services/crawl_job_runtime.py backend/test/test_crawl_job_runtime.py backend/test/test_crawler_tools.py
git commit -m "feat(crawler): stop running jobs at pause checkpoints"
```

### 任务 5：前端暂停与继续交互

**文件：**
- 修改：`frontend/src/types/index.ts`
- 修改：`frontend/src/lib/api/crawlJobsApi.ts`
- 修改：`frontend/src/pages/TasksPage.tsx`

- [ ] **步骤 1：更新前端状态类型**

在 `CrawlJobStatusDTO` 中加入 `paused`：

```typescript
export type CrawlJobStatusDTO =
  | 'queued'
  | 'running'
  | 'paused'
  | 'needs_review'
  | 'completed'
  | 'failed'
  | 'canceled';
```

- [ ] **步骤 2：新增 API client**

在 `frontend/src/lib/api/crawlJobsApi.ts` 中加入：

```typescript
export const pauseCrawlJob = (jobId: number) =>
  apiFetch<CrawlJobDTO>(`/api/crawl-jobs/${jobId}/pause`, {
    method: 'POST',
  });

export const resumeCrawlJob = (jobId: number) =>
  apiFetch<CrawlJobDTO>(`/api/crawl-jobs/${jobId}/resume`, {
    method: 'POST',
  });
```

- [ ] **步骤 3：更新任务页状态文案和样式**

在 `TasksPage.tsx` 的 `CRAWL_JOB_STATUS_LABELS` 增加：

```typescript
paused: "已暂停",
```

在 `CRAWL_JOB_STATUS_TONES` 增加：

```typescript
paused: "border-orange-200 bg-orange-50 text-orange-700",
```

如果还有 `Record<CrawlJobStatusDTO, ...>` 的状态图标、点色或统计映射，也同步增加 `paused`。

- [ ] **步骤 4：调整运行中统计**

把运行统计从只统计 `queued/running` 改为只统计真正会推进的任务：

```typescript
const crawlRunningCount = useMemo(
  () =>
    crawlJobs.filter(
      (job) => job.status === "queued" || job.status === "running",
    ).length,
  [crawlJobs],
);
```

如果当前代码已经是这个逻辑，不需要改动。不要把 `paused` 计入运行中。

- [ ] **步骤 5：新增暂停处理函数**

参考现有 `handleCancelCrawlJob` 添加：

```typescript
const handlePauseCrawlJob = async (jobId: number) => {
  const confirmed = await confirm({
    title: "暂停智能抓取",
    description: "暂停后会保留已抓到的页面和候选导师，之后可以继续。",
    confirmText: "暂停",
    cancelText: "先不暂停",
  });
  if (!confirmed) {
    return;
  }

  try {
    await pauseCrawlJob(jobId);
    showNotification({
      type: "success",
      message: "智能抓取已暂停，当前结果已保留。",
    });
    await loadCrawlJobs();
  } catch (error) {
    showNotification({
      type: "error",
      message: getErrorMessage(error, "暂停智能抓取失败"),
    });
  }
};
```

实际函数名 `confirm`、`showNotification`、`getErrorMessage` 要匹配 `TasksPage.tsx` 中已有写法。

- [ ] **步骤 6：新增继续处理函数**

添加：

```typescript
const handleResumeCrawlJob = async (jobId: number) => {
  try {
    await resumeCrawlJob(jobId);
    showNotification({
      type: "success",
      message: "智能抓取已继续，任务将重新进入队列。",
    });
    await loadCrawlJobs();
  } catch (error) {
    showNotification({
      type: "error",
      message: getErrorMessage(error, "继续智能抓取失败"),
    });
  }
};
```

- [ ] **步骤 7：调整按钮展示**

在任务卡片操作区使用以下规则：

```tsx
{job.status === "queued" || job.status === "running" ? (
  <>
    <button onClick={() => void handlePauseCrawlJob(job.id)}>暂停</button>
    <button onClick={() => void handleCancelCrawlJob(job.id)}>取消</button>
  </>
) : job.status === "paused" ? (
  <>
    <button onClick={() => void handleResumeCrawlJob(job.id)}>继续</button>
    <button onClick={() => void handleCancelCrawlJob(job.id)}>取消</button>
  </>
) : null}
```

不要直接粘贴这个 JSX 覆盖样式。实现时保留现有按钮 className、布局和禁用状态，只替换判断逻辑和文案。

- [ ] **步骤 8：区分取消确认文案**

确认 `handleCancelCrawlJob` 使用终止语义：

```typescript
description: "取消后本次抓取不会继续。如需重新抓取，请使用重试。",
confirmText: "取消抓取",
```

- [ ] **步骤 9：运行前端验证**

运行：

```bash
cd frontend
npm run lint
npm run build
```

预期：两个命令退出码都是 0。

- [ ] **步骤 10：Commit**

```bash
git add frontend/src/types/index.ts frontend/src/lib/api/crawlJobsApi.ts frontend/src/pages/TasksPage.tsx
git commit -m "feat(frontend): add crawl job pause controls"
```

### 任务 6：集成回归验证

**文件：**
- 检查：`backend/app/api/crawl_jobs.py`
- 检查：`backend/app/services/crawl_job_runtime.py`
- 检查：`backend/app/services/crawler_tools.py`
- 检查：`frontend/src/pages/TasksPage.tsx`

- [ ] **步骤 1：运行抓取相关后端测试**

运行：

```bash
cd backend
uv run python -m unittest test.test_crawl_jobs_api test.test_crawl_job_runtime test.test_crawler_tools
```

预期：全部通过，输出包含 `OK`。

- [ ] **步骤 2：运行前端验证**

运行：

```bash
cd frontend
npm run lint
npm run build
```

预期：两个命令退出码都是 0。

- [ ] **步骤 3：检查暂存和差异**

运行：

```bash
git status --short
git diff --stat
```

预期：只包含本功能相关文件。注意当前仓库可能已经存在其他未提交改动，提交时必须只暂存本计划涉及的文件。

- [ ] **步骤 4：最终 Commit**

如果任务 2、任务 4、任务 5 已分别提交，此步骤不需要再提交代码。若实现者选择单提交，使用：

```bash
git add backend/app/models/crawl_job.py backend/app/schemas/crawl_job.py backend/app/services/crawl_job_events.py backend/app/api/crawl_jobs.py backend/app/services/crawler_tools.py backend/app/services/crawl_job_runtime.py backend/test/test_crawl_jobs_api.py backend/test/test_crawl_job_runtime.py backend/test/test_crawler_tools.py frontend/src/types/index.ts frontend/src/lib/api/crawlJobsApi.ts frontend/src/pages/TasksPage.tsx
git commit -m "feat(crawler): support pausing crawl jobs"
```

## 自检

- 规格覆盖度：计划覆盖 `paused` 状态、暂停 API、继续 API、取消保留、运行时检查点、前端按钮和验证标准。
- 占位符扫描：未留下占位描述或未完成章节。
- 类型一致性：统一使用 `paused`、`pauseCrawlJob`、`resumeCrawlJob`、`CrawlJobPaused`、`CrawlJobCanceled`。
- 范围控制：未要求实现 DeepAgents 内部状态持久化，页面 URL 去重只作为后续增强，不阻塞第一版暂停/继续。
