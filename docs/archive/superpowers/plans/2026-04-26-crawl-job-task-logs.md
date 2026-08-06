# 抓取任务实时日志与任务页接入实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 让用户能在 Tasks 页面看到教师抓取任务，并通过轮询查看任务状态、执行日志、已抓页面和候选导师数量。

**架构：** 第一版不引入 SSE/WebSocket，使用 2 秒轮询降低实现风险。后端把已有 `crawl_jobs.agent_trace`、`crawl_pages`、`crawl_candidates` 规范化成任务摘要和时间线事件；前端在 Tasks 页面增加「批量邮件 / 教师抓取」Tab 和抓取任务详情抽屉。

**技术栈：** FastAPI、SQLAlchemy async、Pydantic、React、Vitest、unittest。

---

## 文件结构

- 修改 `backend/app/schemas/crawl_job.py`：新增抓取任务摘要、事件 DTO。
- 创建 `backend/app/services/crawl_job_events.py`：把 job/page/candidate/agent trace 转成用户可读时间线。
- 修改 `backend/app/services/crawl_job_runtime.py`：给新追加的 agent trace 包装时间戳与摘要文本。
- 修改 `backend/app/api/crawl_jobs.py`：列表和详情返回摘要；新增事件接口。
- 修改 `backend/test/test_crawl_jobs_api.py`：覆盖摘要、事件接口、trace 包装。
- 修改 `frontend/src/types/index.ts`：补充抓取任务摘要、页面、事件类型。
- 修改 `frontend/src/lib/api/crawlJobsApi.ts`：新增 pages/events API client。
- 修改 `frontend/test/CrawlJobsApi.test.ts`：覆盖新增 client。
- 修改 `frontend/src/pages/TasksPage.tsx`：增加抓取任务 Tab、轮询、详情抽屉。
- 创建或修改 `frontend/test/TasksPageCrawler.test.tsx`：覆盖抓取任务出现在 Tasks 页面、轮询刷新、取消动作、详情日志。

## 任务 1：后端事件摘要服务

**文件：**
- 创建：`backend/app/services/crawl_job_events.py`
- 测试：`backend/test/test_crawl_jobs_api.py`

- [ ] **步骤 1：编写失败的事件摘要测试**

在 `backend/test/test_crawl_jobs_api.py` 中新增测试，先直接通过 API 验证事件输出。使用已有 `_seed_page_and_candidates()` 和 `_set_job_status()` 风格，另加 trace 写入辅助。

```python
def test_crawl_job_events_include_status_trace_pages_and_candidates(self) -> None:
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
    self._set_job_status(job_id, "needs_review")
    self._set_job_trace(
        job_id,
        [
            {
                "created_at": "2026-04-26T10:00:00Z",
                "event_type": "tool_call",
                "message": "调用 crawl_page 抓取入口页面",
                "payload": {"name": "crawl_page"},
            }
        ],
    )

    response = self.client.get(f"/api/crawl-jobs/{job_id}/events")

    self.assertEqual(response.status_code, 200, msg=response.text)
    events = response.json()
    messages = [event["message"] for event in events]
    self.assertIn("任务进入待审核", messages)
    self.assertIn("调用 crawl_page 抓取入口页面", messages)
    self.assertTrue(any("已抓取页面" in message for message in messages))
    self.assertTrue(any("发现候选导师" in message for message in messages))
```

同时添加辅助函数：

```python
def _set_job_trace(self, job_id: int, trace: list[dict[str, object]]) -> None:
    async def _set_trace() -> None:
        from app.core.database import get_session_factory
        from app.models import CrawlJob

        async with get_session_factory()() as session:
            job = await session.get(CrawlJob, job_id)
            self.assertIsNotNone(job)
            job.agent_trace = trace
            await session.commit()

    asyncio.run(_set_trace())
```

- [ ] **步骤 2：运行测试验证失败**

运行：`cd backend && uv run python -m unittest test.test_crawl_jobs_api.CrawlJobsApiTests.test_crawl_job_events_include_status_trace_pages_and_candidates`

预期：FAIL，404 或 `GET /api/crawl-jobs/{job_id}/events` 未定义。

- [ ] **步骤 3：创建事件摘要服务**

创建 `backend/app/services/crawl_job_events.py`：

```python
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from app.models import CrawlCandidate, CrawlJob, CrawlPage


STATUS_MESSAGES = {
    "queued": "任务已排队",
    "running": "任务正在运行",
    "needs_review": "任务进入待审核",
    "completed": "任务已完成",
    "failed": "任务失败",
    "canceled": "任务已取消",
}


def build_crawl_job_events(
    job: CrawlJob,
    *,
    pages: list[CrawlPage],
    candidates: list[CrawlCandidate],
) -> list[dict[str, object]]:
    events: list[dict[str, object]] = [
        {
            "id": "status",
            "job_id": job.id,
            "event_type": "status",
            "message": STATUS_MESSAGES.get(job.status, f"任务状态：{job.status}"),
            "created_at": _iso_or_none(job.updated_at),
            "raw": {"status": job.status, "error_message": job.error_message},
        }
    ]
    events.extend(_trace_events(job))
    events.extend(_page_events(job.id, pages))
    events.extend(_candidate_events(job.id, candidates))
    return sorted(events, key=lambda item: str(item.get("created_at") or ""))


def normalize_agent_trace_event(event: dict[str, object]) -> dict[str, object]:
    return {
        "created_at": event.get("created_at") or datetime.now(UTC).isoformat(),
        "event_type": str(event.get("event_type") or event.get("type") or "agent_event"),
        "message": summarize_agent_trace_event(event),
        "payload": event.get("payload", event),
    }


def summarize_agent_trace_event(event: dict[str, object]) -> str:
    message = event.get("message")
    if isinstance(message, str) and message.strip():
        return message.strip()

    name = _find_nested_string(event, "name")
    if name:
        return f"Agent 调用 {name}"

    event_type = event.get("event_type") or event.get("type")
    if event_type:
        return f"Agent 事件：{event_type}"

    return "Agent 更新了执行状态"


def _trace_events(job: CrawlJob) -> list[dict[str, object]]:
    trace = job.agent_trace if isinstance(job.agent_trace, list) else []
    result: list[dict[str, object]] = []
    for index, item in enumerate(trace):
        if not isinstance(item, dict):
            continue
        normalized = normalize_agent_trace_event(item)
        result.append(
            {
                "id": f"trace-{index}",
                "job_id": job.id,
                "event_type": normalized["event_type"],
                "message": normalized["message"],
                "created_at": normalized["created_at"],
                "raw": normalized["payload"],
            }
        )
    return result


def _page_events(job_id: int, pages: list[CrawlPage]) -> list[dict[str, object]]:
    return [
        {
            "id": f"page-{page.id}",
            "job_id": job_id,
            "event_type": "page",
            "message": f"已抓取页面：{page.title or page.url}",
            "created_at": _iso_or_none(page.created_at),
            "raw": {"url": page.url, "status": page.status, "fetch_method": page.fetch_method},
        }
        for page in pages
    ]


def _candidate_events(job_id: int, candidates: list[CrawlCandidate]) -> list[dict[str, object]]:
    return [
        {
            "id": f"candidate-{candidate.id}",
            "job_id": job_id,
            "event_type": "candidate",
            "message": f"发现候选导师：{candidate.name}",
            "created_at": _iso_or_none(candidate.created_at),
            "raw": {"candidate_id": candidate.id, "confidence": candidate.confidence},
        }
        for candidate in candidates
    ]


def _iso_or_none(value: object) -> str | None:
    return value.isoformat() if isinstance(value, datetime) else None


def _find_nested_string(value: object, key: str) -> str | None:
    if isinstance(value, dict):
        candidate = value.get(key)
        if isinstance(candidate, str) and candidate.strip():
            return candidate.strip()
        for nested in value.values():
            found = _find_nested_string(nested, key)
            if found:
                return found
    if isinstance(value, list):
        for nested in value:
            found = _find_nested_string(nested, key)
            if found:
                return found
    return None
```

- [ ] **步骤 4：运行测试确认仍失败于 API 未接入**

运行：`cd backend && uv run python -m unittest test.test_crawl_jobs_api.CrawlJobsApiTests.test_crawl_job_events_include_status_trace_pages_and_candidates`

预期：仍 FAIL，接口未接入或 schema 未定义。

- [ ] **步骤 5：Commit**

```bash
git add backend/app/services/crawl_job_events.py backend/test/test_crawl_jobs_api.py
git commit -m "test(backend): cover crawl job event timeline"
```

## 任务 2：后端 API 暴露任务摘要与事件

**文件：**
- 修改：`backend/app/schemas/crawl_job.py`
- 修改：`backend/app/api/crawl_jobs.py`
- 修改：`backend/app/services/crawl_job_runtime.py`
- 测试：`backend/test/test_crawl_jobs_api.py`

- [ ] **步骤 1：编写失败的任务摘要测试**

在 `backend/test/test_crawl_jobs_api.py` 新增：

```python
def test_crawl_job_list_and_detail_include_counts_and_latest_event(self) -> None:
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
    self._set_job_trace(
        job_id,
        [{"created_at": "2026-04-26T10:01:00Z", "message": "保存候选导师"}],
    )

    list_payload = self.client.get("/api/crawl-jobs").json()[0]
    detail_payload = self.client.get(f"/api/crawl-jobs/{job_id}").json()

    for payload in (list_payload, detail_payload):
        self.assertEqual(payload["page_count"], 1)
        self.assertEqual(payload["candidate_count"], 3)
        self.assertEqual(payload["latest_event_message"], "保存候选导师")
```

- [ ] **步骤 2：运行测试验证失败**

运行：`cd backend && uv run python -m unittest test.test_crawl_jobs_api.CrawlJobsApiTests.test_crawl_job_list_and_detail_include_counts_and_latest_event`

预期：FAIL，响应缺少 `page_count`。

- [ ] **步骤 3：扩展 schema**

在 `backend/app/schemas/crawl_job.py` 中添加：

```python
class CrawlJobSummaryRead(CrawlJobRead):
    page_count: int = 0
    candidate_count: int = 0
    latest_event_message: str | None = None


class CrawlJobEventRead(BaseModel):
    id: str
    job_id: int
    event_type: str
    message: str
    created_at: str | None
    raw: dict[str, object] | None = None
```

- [ ] **步骤 4：修改 runtime 的 trace 包装**

在 `backend/app/services/crawl_job_runtime.py` 中导入：

```python
from app.services.crawl_job_events import normalize_agent_trace_event
```

修改 `_append_agent_trace()` 中追加事件的逻辑：

```python
trace = list(_normalize_trace(job.agent_trace))
trace.append(normalize_agent_trace_event(event))
job.agent_trace = trace[-MAX_AGENT_TRACE_EVENTS:]
```

- [ ] **步骤 5：接入 API 摘要和 events**

在 `backend/app/api/crawl_jobs.py` 中导入：

```python
from sqlalchemy import func
from app.services.crawl_job_events import build_crawl_job_events, summarize_agent_trace_event
```

把 `list_crawl_jobs()` 的 response model 改为 `list[CrawlJobSummaryRead]`，并返回 dict：

```python
@router.get("", response_model=list[CrawlJobSummaryRead])
async def list_crawl_jobs(session: AsyncSession = Depends(get_async_session)) -> list[dict[str, object]]:
    jobs = list(
        (
            await session.execute(
                select(CrawlJob)
                .order_by(CrawlJob.created_at.desc(), CrawlJob.id.desc())
                .limit(50),
            )
        ).scalars(),
    )
    return [await _build_crawl_job_summary(session, job) for job in jobs]
```

把 `get_crawl_job()` response model 改为 `CrawlJobSummaryRead`：

```python
@router.get("/{job_id}", response_model=CrawlJobSummaryRead)
async def get_crawl_job(job_id: int, session: AsyncSession = Depends(get_async_session)) -> dict[str, object]:
    job = await _get_crawl_job_or_404(session, job_id)
    return await _build_crawl_job_summary(session, job)
```

新增事件接口：

```python
@router.get("/{job_id}/events", response_model=list[CrawlJobEventRead])
async def list_crawl_events(
    job_id: int,
    session: AsyncSession = Depends(get_async_session),
) -> list[dict[str, object]]:
    job = await _get_crawl_job_or_404(session, job_id)
    pages = list(
        (
            await session.execute(
                select(CrawlPage)
                .where(CrawlPage.job_id == job_id)
                .order_by(CrawlPage.created_at.asc(), CrawlPage.id.asc()),
            )
        ).scalars(),
    )
    candidates = list(
        (
            await session.execute(
                select(CrawlCandidate)
                .where(CrawlCandidate.job_id == job_id)
                .order_by(CrawlCandidate.created_at.asc(), CrawlCandidate.id.asc()),
            )
        ).scalars(),
    )
    return build_crawl_job_events(job, pages=pages, candidates=candidates)
```

新增摘要 helper：

```python
async def _build_crawl_job_summary(session: AsyncSession, job: CrawlJob) -> dict[str, object]:
    page_count = await session.scalar(
        select(func.count(CrawlPage.id)).where(CrawlPage.job_id == job.id)
    )
    candidate_count = await session.scalar(
        select(func.count(CrawlCandidate.id)).where(CrawlCandidate.job_id == job.id)
    )
    return {
        "id": job.id,
        "university": job.university,
        "school": job.school,
        "start_url": job.start_url,
        "llm_profile_id": job.llm_profile_id,
        "status": job.status,
        "progress_current": job.progress_current,
        "progress_total": job.progress_total,
        "error_message": job.error_message,
        "created_at": job.created_at,
        "updated_at": job.updated_at,
        "page_count": int(page_count or 0),
        "candidate_count": int(candidate_count or 0),
        "latest_event_message": _latest_trace_message(job),
    }


def _latest_trace_message(job: CrawlJob) -> str | None:
    trace = job.agent_trace if isinstance(job.agent_trace, list) else []
    for item in reversed(trace):
        if isinstance(item, dict):
            return summarize_agent_trace_event(item)
    return None
```

- [ ] **步骤 6：运行后端 API 测试验证通过**

运行：`cd backend && uv run python -m unittest test.test_crawl_jobs_api`

预期：OK。

- [ ] **步骤 7：Commit**

```bash
git add backend/app/schemas/crawl_job.py backend/app/api/crawl_jobs.py backend/app/services/crawl_job_runtime.py backend/app/services/crawl_job_events.py backend/test/test_crawl_jobs_api.py
git commit -m "feat(backend): expose crawl job timeline"
```

## 任务 3：前端 API 类型与 client

**文件：**
- 修改：`frontend/src/types/index.ts`
- 修改：`frontend/src/lib/api/crawlJobsApi.ts`
- 测试：`frontend/test/CrawlJobsApi.test.ts`

- [ ] **步骤 1：编写失败的 API client 测试**

在 `frontend/test/CrawlJobsApi.test.ts` 中扩展 import：

```ts
import {
  createCrawlJob,
  getCrawlJobEvents,
  listCrawlJobs,
  listCrawlPages,
} from '@/lib/api/crawlJobsApi';
```

新增测试：

```ts
it('lists crawl jobs, pages, and timeline events', async () => {
  mockedApiFetch.mockResolvedValueOnce([]);
  await expect(listCrawlJobs()).resolves.toEqual([]);
  expect(mockedApiFetch).toHaveBeenCalledWith('/api/crawl-jobs');

  mockedApiFetch.mockResolvedValueOnce([]);
  await expect(listCrawlPages(7)).resolves.toEqual([]);
  expect(mockedApiFetch).toHaveBeenCalledWith('/api/crawl-jobs/7/pages');

  mockedApiFetch.mockResolvedValueOnce([]);
  await expect(getCrawlJobEvents(7)).resolves.toEqual([]);
  expect(mockedApiFetch).toHaveBeenCalledWith('/api/crawl-jobs/7/events');
});
```

- [ ] **步骤 2：运行测试验证失败**

运行：`cd frontend && npm test -- --run frontend/test/CrawlJobsApi.test.ts`

预期：FAIL，`getCrawlJobEvents` 或 `listCrawlPages` 未导出。

- [ ] **步骤 3：补充类型**

在 `frontend/src/types/index.ts` 中扩展 `CrawlJobDTO`：

```ts
export interface CrawlJobDTO {
  id: number;
  university: string;
  school: string;
  start_url: string;
  llm_profile_id: number | null;
  status: CrawlJobStatusDTO;
  progress_current: number;
  progress_total: number;
  error_message: string | null;
  page_count: number;
  candidate_count: number;
  latest_event_message: string | null;
  created_at: string;
  updated_at: string;
}
```

新增：

```ts
export interface CrawlPageDTO {
  id: number;
  job_id: number;
  url: string;
  parent_url: string | null;
  fetch_method: string;
  page_type: string;
  status: string;
  title: string | null;
  text_excerpt: string | null;
  error_message: string | null;
  created_at: string;
}

export interface CrawlJobEventDTO {
  id: string;
  job_id: number;
  event_type: string;
  message: string;
  created_at: string | null;
  raw: Record<string, unknown> | null;
}
```

- [ ] **步骤 4：补充 API client**

在 `frontend/src/lib/api/crawlJobsApi.ts` 导入类型：

```ts
import type {
  CrawlJobEventDTO,
  CrawlPageDTO,
} from '@/types';
```

新增函数：

```ts
export const listCrawlPages = (jobId: number) =>
  apiFetch<CrawlPageDTO[]>(`/api/crawl-jobs/${jobId}/pages`);

export const getCrawlJobEvents = (jobId: number) =>
  apiFetch<CrawlJobEventDTO[]>(`/api/crawl-jobs/${jobId}/events`);
```

- [ ] **步骤 5：运行测试验证通过**

运行：`cd frontend && npm test -- --run frontend/test/CrawlJobsApi.test.ts`

预期：PASS。

- [ ] **步骤 6：Commit**

```bash
git add frontend/src/types/index.ts frontend/src/lib/api/crawlJobsApi.ts frontend/test/CrawlJobsApi.test.ts
git commit -m "feat(frontend): add crawl job timeline api"
```

## 任务 4：Tasks 页面加入抓取任务 Tab 和轮询

**文件：**
- 修改：`frontend/src/pages/TasksPage.tsx`
- 测试：`frontend/test/TasksPageCrawler.test.tsx`

- [ ] **步骤 1：编写失败的 Tasks 页面测试**

创建 `frontend/test/TasksPageCrawler.test.tsx`。mock selection、notification、confirm 和 API：

```tsx
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { TasksPage } from '@/pages/TasksPage';

const mockedListBatchTasks = vi.hoisted(() => vi.fn());
const mockedListCrawlJobs = vi.hoisted(() => vi.fn());
const mockedCancelCrawlJob = vi.hoisted(() => vi.fn());

vi.mock('@/context/SelectionContext', () => ({
  useSelectionContext: () => ({ selectedIdentityId: 1, selectedLlmProfileId: 2 }),
}));

vi.mock('@/context/NotificationContext', () => ({
  useNotification: () => ({ notifyError: vi.fn(), notifySuccess: vi.fn() }),
}));

vi.mock('@/lib/useConfirmDialog', () => ({
  useConfirmDialog: () => ({ confirm: vi.fn().mockResolvedValue(true), dialog: null }),
}));

vi.mock('@/lib/api/batchTasksApi', () => ({
  listBatchTasks: mockedListBatchTasks,
  pauseBatchTask: vi.fn(),
  resumeBatchTask: vi.fn(),
  stopBatchTask: vi.fn(),
}));

vi.mock('@/lib/api/crawlJobsApi', () => ({
  listCrawlJobs: mockedListCrawlJobs,
  cancelCrawlJob: mockedCancelCrawlJob,
  listCrawlPages: vi.fn().mockResolvedValue([]),
  listCrawlCandidates: vi.fn().mockResolvedValue([]),
  getCrawlJobEvents: vi.fn().mockResolvedValue([]),
}));

describe('TasksPage crawl jobs', () => {
  beforeEach(() => {
    mockedListBatchTasks.mockResolvedValue([]);
    mockedListCrawlJobs.mockResolvedValue([
      {
        id: 7,
        university: '示例大学',
        school: '计算机学院',
        start_url: 'https://example.edu/faculty',
        llm_profile_id: null,
        status: 'running',
        progress_current: 0,
        progress_total: 0,
        page_count: 3,
        candidate_count: 12,
        latest_event_message: '正在抓取入口页面',
        error_message: null,
        created_at: '2026-04-26T10:00:00Z',
        updated_at: '2026-04-26T10:01:00Z',
      },
    ]);
    mockedCancelCrawlJob.mockResolvedValue({});
  });

  it('shows crawl jobs in the crawl tab and can cancel a running job', async () => {
    render(
      <MemoryRouter>
        <TasksPage />
      </MemoryRouter>,
    );

    await userEvent.click(await screen.findByRole('button', { name: '教师抓取' }));

    expect(await screen.findByText('示例大学 / 计算机学院')).toBeInTheDocument();
    expect(screen.getByText('已抓页面 3')).toBeInTheDocument();
    expect(screen.getByText('候选导师 12')).toBeInTheDocument();
    expect(screen.getByText('正在抓取入口页面')).toBeInTheDocument();

    await userEvent.click(screen.getByRole('button', { name: '取消抓取' }));

    await waitFor(() => expect(mockedCancelCrawlJob).toHaveBeenCalledWith(7));
  });
});
```

- [ ] **步骤 2：运行测试验证失败**

运行：`cd frontend && npm test -- --run frontend/test/TasksPageCrawler.test.tsx`

预期：FAIL，找不到「教师抓取」Tab。

- [ ] **步骤 3：修改 TasksPage 状态和加载函数**

在 `frontend/src/pages/TasksPage.tsx` 导入：

```tsx
import {
  cancelCrawlJob,
  listCrawlJobs,
} from "@/lib/api/crawlJobsApi";
import type { CrawlJobDTO } from "@/types";
```

添加状态：

```tsx
type TaskTab = "batch" | "crawler";

const [activeTab, setActiveTab] = useState<TaskTab>("batch");
const [crawlJobs, setCrawlJobs] = useState<CrawlJobDTO[]>([]);
const [crawlLoading, setCrawlLoading] = useState(false);
```

新增加载函数：

```tsx
const loadCrawlJobs = useCallback(async () => {
  setCrawlLoading(true);
  try {
    const data = await listCrawlJobs();
    setCrawlJobs(data);
  } catch (loadError) {
    const message = loadError instanceof Error ? loadError.message : "加载抓取任务失败";
    notifyError("加载抓取任务失败", message);
  } finally {
    setCrawlLoading(false);
  }
}, [notifyError]);
```

新增 2 秒轮询：

```tsx
useEffect(() => {
  if (activeTab !== "crawler") {
    return;
  }
  void loadCrawlJobs();
  const timer = window.setInterval(() => {
    void loadCrawlJobs();
  }, 2000);
  return () => window.clearInterval(timer);
}, [activeTab, loadCrawlJobs]);
```

- [ ] **步骤 4：添加 Tab 和抓取任务卡片**

在标题区域下方添加 Tab：

```tsx
<div className="mt-5 inline-flex rounded-2xl border border-stone-200 bg-white p-1">
  <button
    type="button"
    onClick={() => setActiveTab("batch")}
    className={activeTab === "batch" ? "ui-btn-primary" : "ui-btn-secondary"}
  >
    批量邮件
  </button>
  <button
    type="button"
    onClick={() => setActiveTab("crawler")}
    className={activeTab === "crawler" ? "ui-btn-primary" : "ui-btn-secondary"}
  >
    教师抓取
  </button>
</div>
```

在 batch 列表外层加条件渲染；新增 crawler 列表：

```tsx
{activeTab === "crawler" ? (
  crawlLoading ? (
    <div className="mt-6 flex items-center justify-center gap-2 rounded-3xl border border-stone-200 bg-white px-6 py-14 text-sm text-stone-500 shadow-sm">
      <Loader2 className="h-4 w-4 animate-spin" />
      正在加载抓取任务...
    </div>
  ) : crawlJobs.length === 0 ? (
    <div className="mt-6 rounded-3xl border border-dashed border-stone-300 bg-white px-6 py-14 text-center text-sm text-stone-500 shadow-sm">
      暂无抓取任务。可从导师管理页创建。
    </div>
  ) : (
    <div className="mt-6 grid gap-6 md:grid-cols-2">
      {crawlJobs.map((job) => (
        <article key={job.id} className="rounded-3xl border border-stone-200 bg-white p-6 shadow-sm">
          <div className="flex items-start justify-between gap-4">
            <div>
              <h2 className="text-xl font-semibold text-stone-900">
                {job.university} / {job.school}
              </h2>
              <p className="mt-2 break-all text-sm text-stone-500">{job.start_url}</p>
            </div>
            <span className="rounded-full bg-stone-100 px-3 py-1 text-xs font-medium text-stone-700">
              {CRAWL_JOB_STATUS_LABELS[job.status]}
            </span>
          </div>
          <div className="mt-5 grid gap-3 sm:grid-cols-2">
            <div className="rounded-2xl bg-stone-50 px-4 py-3 text-sm text-stone-700">
              已抓页面 {job.page_count}
            </div>
            <div className="rounded-2xl bg-stone-50 px-4 py-3 text-sm text-stone-700">
              候选导师 {job.candidate_count}
            </div>
          </div>
          {job.latest_event_message ? (
            <p className="mt-4 text-sm text-stone-500">{job.latest_event_message}</p>
          ) : null}
          <div className="mt-6 flex flex-wrap gap-3">
            <button type="button" className="ui-btn-secondary">
              查看日志
            </button>
            {job.status === "queued" || job.status === "running" ? (
              <button
                type="button"
                onClick={() => void handleCancelCrawlJob(job.id)}
                className="ui-btn-danger"
              >
                <Square className="h-4 w-4" />
                取消抓取
              </button>
            ) : null}
          </div>
        </article>
      ))}
    </div>
  )
) : null}
```

添加 label 常量和取消函数：

```tsx
const CRAWL_JOB_STATUS_LABELS = {
  queued: "排队中",
  running: "运行中",
  needs_review: "待审核",
  completed: "已完成",
  failed: "失败",
  canceled: "已取消",
} satisfies Record<CrawlJobDTO["status"], string>;

const handleCancelCrawlJob = async (jobId: number) => {
  const confirmed = await confirm({
    title: "确认取消这个抓取任务？",
    description: "取消后后台 worker 不会继续写入新的抓取结果。",
    confirmLabel: "确认取消",
    cancelLabel: "先保留",
    tone: "danger",
  });
  if (!confirmed) {
    return;
  }
  await cancelCrawlJob(jobId);
  await loadCrawlJobs();
};
```

- [ ] **步骤 5：运行测试验证通过**

运行：`cd frontend && npm test -- --run frontend/test/TasksPageCrawler.test.tsx`

预期：PASS。

- [ ] **步骤 6：Commit**

```bash
git add frontend/src/pages/TasksPage.tsx frontend/test/TasksPageCrawler.test.tsx
git commit -m "feat(frontend): show crawl jobs on tasks page"
```

## 任务 5：抓取任务详情抽屉展示实时日志

**文件：**
- 修改：`frontend/src/pages/TasksPage.tsx`
- 测试：`frontend/test/TasksPageCrawler.test.tsx`

- [ ] **步骤 1：编写失败的详情抽屉测试**

扩展 API mock：

```tsx
const mockedListCrawlPages = vi.hoisted(() => vi.fn());
const mockedListCrawlCandidates = vi.hoisted(() => vi.fn());
const mockedGetCrawlJobEvents = vi.hoisted(() => vi.fn());
```

在 `vi.mock('@/lib/api/crawlJobsApi')` 中使用这些 mock。

新增测试：

```tsx
it('opens a crawl job detail drawer with timeline, pages, and candidates', async () => {
  mockedListCrawlPages.mockResolvedValue([
    {
      id: 1,
      job_id: 7,
      url: 'https://example.edu/faculty',
      parent_url: null,
      fetch_method: 'http',
      page_type: 'unknown',
      status: 'succeeded',
      title: 'Faculty',
      text_excerpt: 'Faculty page',
      error_message: null,
      created_at: '2026-04-26T10:00:30Z',
    },
  ]);
  mockedListCrawlCandidates.mockResolvedValue([
    {
      id: 2,
      job_id: 7,
      professor_id: null,
      name: '张教授',
      email: 'zhang@example.edu',
      title: 'Professor',
      university: '示例大学',
      school: '计算机学院',
      department: null,
      research_direction: 'AI',
      recent_papers: [],
      profile_url: null,
      source_url: 'https://example.edu/faculty',
      confidence: 0.9,
      field_confidence: null,
      evidence: null,
      review_status: 'pending',
      created_at: '2026-04-26T10:01:00Z',
      updated_at: '2026-04-26T10:01:00Z',
    },
  ]);
  mockedGetCrawlJobEvents.mockResolvedValue([
    {
      id: 'trace-0',
      job_id: 7,
      event_type: 'tool_call',
      message: '调用 crawl_page 抓取入口页面',
      created_at: '2026-04-26T10:00:00Z',
      raw: null,
    },
  ]);

  render(
    <MemoryRouter>
      <TasksPage />
    </MemoryRouter>,
  );

  await userEvent.click(await screen.findByRole('button', { name: '教师抓取' }));
  await userEvent.click(await screen.findByRole('button', { name: '查看日志' }));

  expect(await screen.findByRole('dialog', { name: '抓取任务日志' })).toBeInTheDocument();
  expect(screen.getByText('调用 crawl_page 抓取入口页面')).toBeInTheDocument();
  expect(screen.getByText('Faculty')).toBeInTheDocument();
  expect(screen.getByText('张教授')).toBeInTheDocument();
});
```

- [ ] **步骤 2：运行测试验证失败**

运行：`cd frontend && npm test -- --run frontend/test/TasksPageCrawler.test.tsx`

预期：FAIL，详情抽屉不存在。

- [ ] **步骤 3：添加详情状态和加载函数**

在 `TasksPage` 中添加：

```tsx
const [selectedCrawlJob, setSelectedCrawlJob] = useState<CrawlJobDTO | null>(null);
const [crawlPages, setCrawlPages] = useState<CrawlPageDTO[]>([]);
const [crawlCandidates, setCrawlCandidates] = useState<CrawlCandidateDTO[]>([]);
const [crawlEvents, setCrawlEvents] = useState<CrawlJobEventDTO[]>([]);
const [crawlDetailLoading, setCrawlDetailLoading] = useState(false);

const loadCrawlJobDetail = useCallback(async (jobId: number) => {
  setCrawlDetailLoading(true);
  try {
    const [pages, candidates, events] = await Promise.all([
      listCrawlPages(jobId),
      listCrawlCandidates(jobId),
      getCrawlJobEvents(jobId),
    ]);
    setCrawlPages(pages);
    setCrawlCandidates(candidates);
    setCrawlEvents(events);
  } catch (loadError) {
    const message = loadError instanceof Error ? loadError.message : "加载抓取日志失败";
    notifyError("加载抓取日志失败", message);
  } finally {
    setCrawlDetailLoading(false);
  }
}, [notifyError]);
```

添加详情轮询：

```tsx
useEffect(() => {
  if (!selectedCrawlJob) {
    return;
  }
  void loadCrawlJobDetail(selectedCrawlJob.id);
  const timer = window.setInterval(() => {
    void loadCrawlJobDetail(selectedCrawlJob.id);
  }, 2000);
  return () => window.clearInterval(timer);
}, [loadCrawlJobDetail, selectedCrawlJob]);
```

- [ ] **步骤 4：实现详情抽屉**

在卡片「查看日志」按钮上：

```tsx
onClick={() => setSelectedCrawlJob(job)}
```

在页面底部加抽屉：

```tsx
{selectedCrawlJob ? (
  <div
    role="dialog"
    aria-label="抓取任务日志"
    aria-modal="true"
    className="fixed inset-0 z-[80] bg-stone-950/35 p-4 backdrop-blur-md"
    onClick={() => setSelectedCrawlJob(null)}
  >
    <div
      className="ml-auto flex h-full w-full max-w-3xl flex-col overflow-hidden rounded-3xl border border-stone-200 bg-white shadow-2xl"
      onClick={(event) => event.stopPropagation()}
    >
      <div className="border-b border-stone-100 px-6 py-5">
        <div className="flex items-start justify-between gap-4">
          <div>
            <h2 className="text-xl font-semibold text-stone-900">
              {selectedCrawlJob.university} / {selectedCrawlJob.school}
            </h2>
            <p className="mt-2 break-all text-sm text-stone-500">
              {selectedCrawlJob.start_url}
            </p>
          </div>
          <button type="button" onClick={() => setSelectedCrawlJob(null)} className="ui-btn-secondary">
            关闭
          </button>
        </div>
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto px-6 py-5">
        {crawlDetailLoading ? (
          <div className="flex items-center gap-2 text-sm text-stone-500">
            <Loader2 className="h-4 w-4 animate-spin" />
            正在刷新日志...
          </div>
        ) : null}
        <section>
          <h3 className="text-sm font-semibold text-stone-900">执行日志</h3>
          <div className="mt-3 space-y-3">
            {crawlEvents.map((event) => (
              <div key={event.id} className="rounded-2xl border border-stone-100 bg-stone-50 px-4 py-3">
                <div className="text-sm text-stone-800">{event.message}</div>
                <div className="mt-1 text-xs text-stone-500">{event.created_at ?? "--"}</div>
              </div>
            ))}
          </div>
        </section>
        <section className="mt-6">
          <h3 className="text-sm font-semibold text-stone-900">已抓页面</h3>
          <div className="mt-3 space-y-2">
            {crawlPages.map((page) => (
              <div key={page.id} className="rounded-2xl border border-stone-100 px-4 py-3 text-sm">
                <div className="font-medium text-stone-800">{page.title || page.url}</div>
                <div className="mt-1 break-all text-xs text-stone-500">{page.url}</div>
              </div>
            ))}
          </div>
        </section>
        <section className="mt-6">
          <h3 className="text-sm font-semibold text-stone-900">候选导师</h3>
          <div className="mt-3 space-y-2">
            {crawlCandidates.map((candidate) => (
              <div key={candidate.id} className="rounded-2xl border border-stone-100 px-4 py-3 text-sm">
                <div className="font-medium text-stone-800">{candidate.name}</div>
                <div className="mt-1 text-xs text-stone-500">
                  {candidate.email ?? "暂无邮箱"} · 置信度 {Math.round(candidate.confidence * 100)}%
                </div>
              </div>
            ))}
          </div>
        </section>
      </div>
    </div>
  </div>
) : null}
```

- [ ] **步骤 5：运行测试验证通过**

运行：`cd frontend && npm test -- --run frontend/test/TasksPageCrawler.test.tsx`

预期：PASS。

- [ ] **步骤 6：Commit**

```bash
git add frontend/src/pages/TasksPage.tsx frontend/test/TasksPageCrawler.test.tsx
git commit -m "feat(frontend): add crawl job log drawer"
```

## 任务 6：全量验证与收尾

**文件：**
- 修改：无功能代码，按验证结果只修复必要问题。

- [ ] **步骤 1：运行后端聚焦测试**

运行：`cd backend && uv run python -m unittest test.test_crawl_jobs_api test.test_crawl_job_models test.test_crawler_tools`

预期：OK。

- [ ] **步骤 2：运行前端聚焦测试**

运行：`cd frontend && npm test -- --run frontend/test/CrawlJobsApi.test.ts frontend/test/TasksPageCrawler.test.tsx`

预期：PASS。

- [ ] **步骤 3：运行全量后端测试**

运行：`cd backend && uv run python -m unittest discover test`

预期：OK。

- [ ] **步骤 4：运行前端全量测试、lint、build**

运行：

```bash
cd frontend
npm test -- --run
npm run lint
npm run build
```

预期：全部 exit 0；如果 Vite 仍提示 chunk size warning，只记录为非阻塞警告。

- [ ] **步骤 5：检查工作树**

运行：`git status --short`

预期：输出为空。

- [ ] **步骤 6：Commit 验证修复**

如果步骤 1-4 发现并修复了遗漏问题，提交：

```bash
git add <fixed-files>
git commit -m "fix: stabilize crawl job task logs"
```

如果没有修复文件，不创建空提交。

## 自检

- 规格覆盖：Tasks 页面可看到抓取任务；详情可看实时日志；后端暴露摘要和事件；取消动作保留；全部有测试。
- 占位符扫描：计划中没有 TODO、待定、后续实现，也没有“类似任务”。
- 类型一致性：后端 `CrawlJobSummaryRead` 对应前端 `CrawlJobDTO`；后端 `CrawlJobEventRead` 对应前端 `CrawlJobEventDTO`；API client 函数名和 TasksPage 调用一致。
- 范围控制：第一版使用轮询，不实现 SSE/WebSocket，不实现完整候选审核页面。

