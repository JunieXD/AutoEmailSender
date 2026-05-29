# 智能抓取任务部分导入状态实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 为智能抓取任务新增“部分已导入”状态，让用户导入部分候选导师后仍能继续补全和审核剩余候选。

**架构：** 后端新增 `partially_completed` 状态，并让审核导入接口根据剩余 `pending` 候选决定任务状态；补全接口允许从该状态发起并在结束后恢复。前端扩展状态类型、标签和可审核判断，同时把候选选择范围收紧为仅 `pending`，避免重复导入已处理候选。

**技术栈：** FastAPI、SQLAlchemy async、Pydantic、unittest、Vite、React、TypeScript、Vitest。

---

## 规格来源

- `docs/superpowers/specs/2026-05-08-crawl-job-partial-review-design.md`

## 文件结构

- 修改：`backend/app/models/crawl_job.py`
  - 职责：新增后端抓取任务状态枚举值。
- 修改：`backend/app/schemas/crawl_job.py`
  - 职责：新增 API DTO 可返回和接收的抓取任务状态字面量。
- 修改：`backend/app/services/crawl_job_events.py`
  - 职责：新增状态事件文案。
- 修改：`backend/app/api/crawl_jobs.py`
  - 职责：更新审核导入和候选补全接口的状态判断、状态恢复和状态推进规则。
- 修改：`backend/test/test_crawl_job_models.py`
  - 职责：锁定新增状态枚举。
- 修改：`backend/test/test_crawl_job_events.py`
  - 职责：锁定新增状态事件文案。
- 修改：`backend/test/test_crawl_jobs_api.py`
  - 职责：覆盖部分导入、继续导入、补全恢复状态、完成态拒绝等后端行为。
- 修改：`frontend/src/types/index.ts`
  - 职责：新增前端抓取任务状态类型。
- 修改：`frontend/src/features/crawl-review/client/reviewCandidates.ts`
  - 职责：把可审核候选定义为 `pending`，避免重复选择已导入候选。
- 修改：`frontend/src/features/crawl-review/client/reviewCandidates.test.ts`
  - 职责：锁定候选选择规则。
- 修改：`frontend/src/pages/TasksPage.tsx`
  - 职责：新增状态标签/颜色、可审核状态判断、删除按钮规则和部分导入确认文案。
- 修改：`frontend/test/TasksPageCrawler.test.tsx`
  - 职责：覆盖“部分已导入”状态下继续补全、继续导入、不显示删除按钮。

## 任务 1：新增后端状态枚举、DTO 与事件文案

**文件：**
- 修改：`backend/test/test_crawl_job_models.py`
- 修改：`backend/test/test_crawl_job_events.py`
- 修改：`backend/app/models/crawl_job.py`
- 修改：`backend/app/schemas/crawl_job.py`
- 修改：`backend/app/services/crawl_job_events.py`

- [ ] **步骤 1：编写失败的模型测试**

在 `backend/test/test_crawl_job_models.py` 的 `test_status_constants_are_stable` 中加入：

```python
self.assertEqual(CrawlJobStatus.PARTIALLY_COMPLETED.value, "partially_completed")
```

放在 `NEEDS_REVIEW` 和 `COMPLETED` 之间，让测试表达状态顺序。

- [ ] **步骤 2：编写失败的事件文案测试**

在 `backend/test/test_crawl_job_events.py` 增加测试：

```python
def test_partially_completed_status_message(self) -> None:
    class Job:
        id = 7
        status = "partially_completed"
        updated_at = datetime(2026, 5, 8, 10, 0, tzinfo=UTC)
        created_at = updated_at
        error_message = None
        agent_trace = None

    events = build_crawl_job_events(Job(), pages=[], candidates=[])

    self.assertEqual(events[0]["message"], "任务部分候选已导入")
    self.assertEqual(events[0]["raw"]["status"], "partially_completed")
```

如果文件尚未导入 `UTC`、`datetime` 或 `build_crawl_job_events`，使用文件内已有导入模式补齐。

- [ ] **步骤 3：运行测试验证失败**

在 `backend` 目录运行：

```powershell
rtk uv run python -m unittest test.test_crawl_job_models test.test_crawl_job_events
```

预期：失败，报错包含 `PARTIALLY_COMPLETED` 不存在或状态文案不是“任务部分候选已导入”。

- [ ] **步骤 4：新增最小实现**

在 `backend/app/models/crawl_job.py` 的 `CrawlJobStatus` 中加入：

```python
PARTIALLY_COMPLETED = "partially_completed"
```

在 `backend/app/schemas/crawl_job.py` 的 `CrawlJobStatusDTO` 中加入：

```python
"partially_completed",
```

在 `backend/app/services/crawl_job_events.py` 的 `STATUS_MESSAGES` 中加入：

```python
"partially_completed": "任务部分候选已导入",
```

- [ ] **步骤 5：运行测试验证通过**

在 `backend` 目录运行：

```powershell
rtk uv run python -m unittest test.test_crawl_job_models test.test_crawl_job_events
```

预期：`OK`。

- [ ] **步骤 6：Commit**

```powershell
rtk git add backend/app/models/crawl_job.py backend/app/schemas/crawl_job.py backend/app/services/crawl_job_events.py backend/test/test_crawl_job_models.py backend/test/test_crawl_job_events.py
rtk git commit -m "feat(抓取任务): 添加部分导入状态"
```

## 任务 2：后端审核导入支持部分完成状态

**文件：**
- 修改：`backend/test/test_crawl_jobs_api.py`
- 修改：`backend/app/api/crawl_jobs.py`

- [ ] **步骤 1：编写失败的部分导入测试**

在 `backend/test/test_crawl_jobs_api.py` 中加入：

```python
def test_approve_partially_imported_job_keeps_remaining_candidates_reviewable(self) -> None:
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
    candidates = self.client.get(f"/api/crawl-jobs/{job_id}/candidates").json()

    first_response = self.client.post(
        f"/api/crawl-jobs/{job_id}/approve",
        json={"candidate_ids": [candidates[0]["id"]]},
    )

    self.assertEqual(first_response.status_code, 200, msg=first_response.text)
    self.assertEqual(first_response.json()["inserted_count"], 1)
    partially_completed = self.client.get(f"/api/crawl-jobs/{job_id}").json()
    self.assertEqual(partially_completed["status"], "partially_completed")

    remaining = self.client.get(f"/api/crawl-jobs/{job_id}/candidates").json()
    remaining_pending_ids = [
        candidate["id"]
        for candidate in remaining
        if candidate["review_status"] == "pending" and candidate["email"]
    ]

    second_response = self.client.post(
        f"/api/crawl-jobs/{job_id}/approve",
        json={"candidate_ids": remaining_pending_ids},
    )

    self.assertEqual(second_response.status_code, 200, msg=second_response.text)
    final_job = self.client.get(f"/api/crawl-jobs/{job_id}").json()
    self.assertEqual(final_job["status"], "partially_completed")
```

这个测试先验证：导入一部分后变为 `partially_completed`，并且该状态下还可以继续导入。最后仍有一个无邮箱 `pending` 候选，因此状态保持 `partially_completed`。

- [ ] **步骤 2：编写失败的最后一批完成测试**

在同一文件加入：

```python
def test_approve_last_pending_candidate_completes_partially_imported_job(self) -> None:
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
    self._set_job_status(job_id, "partially_completed")
    candidates = self.client.get(f"/api/crawl-jobs/{job_id}/candidates").json()

    for candidate in candidates:
        if not candidate["email"]:
            patch_response = self.client.patch(
                f"/api/crawl-jobs/candidates/{candidate['id']}",
                json={**candidate, "email": "filled@example.edu"},
            )
            self.assertEqual(patch_response.status_code, 200, msg=patch_response.text)

    refreshed = self.client.get(f"/api/crawl-jobs/{job_id}/candidates").json()
    response = self.client.post(
        f"/api/crawl-jobs/{job_id}/approve",
        json={"candidate_ids": [candidate["id"] for candidate in refreshed]},
    )

    self.assertEqual(response.status_code, 200, msg=response.text)
    detail_response = self.client.get(f"/api/crawl-jobs/{job_id}")
    self.assertEqual(detail_response.json()["status"], "completed")
```

这个测试从 `partially_completed` 继续导入所有剩余 `pending` 候选，预期任务进入 `completed`。

- [ ] **步骤 3：编写失败的完成态拒绝测试**

在同一文件加入：

```python
def test_approve_rejects_completed_job_even_with_pending_candidates(self) -> None:
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
    self._set_job_status(job_id, "completed")
    candidates = self.client.get(f"/api/crawl-jobs/{job_id}/candidates").json()

    response = self.client.post(
        f"/api/crawl-jobs/{job_id}/approve",
        json={"candidate_ids": [candidates[0]["id"]]},
    )

    self.assertEqual(response.status_code, 409)
    self.assertEqual(response.json()["detail"], "抓取任务尚未进入审核状态")
```

- [ ] **步骤 4：运行测试验证失败**

在 `backend` 目录运行：

```powershell
rtk uv run python -m unittest test.test_crawl_jobs_api.CrawlJobsApiTests.test_approve_partially_imported_job_keeps_remaining_candidates_reviewable test.test_crawl_jobs_api.CrawlJobsApiTests.test_approve_last_pending_candidate_completes_partially_imported_job test.test_crawl_jobs_api.CrawlJobsApiTests.test_approve_rejects_completed_job_even_with_pending_candidates
```

预期：前两个测试因为 `partially_completed` 不能审核或状态仍为 `completed` 而失败；第三个测试应通过或保持 409。

- [ ] **步骤 5：实现审核状态推进**

在 `backend/app/api/crawl_jobs.py` 中，给审核接口增加可审核状态集合：

```python
reviewable_statuses = {
    CrawlJobStatus.NEEDS_REVIEW.value,
    CrawlJobStatus.PARTIALLY_COMPLETED.value,
    CrawlJobStatus.CANCELED.value,
}
if job.status not in reviewable_statuses:
    raise HTTPException(status_code=409, detail="抓取任务尚未进入审核状态")
```

在成功处理候选后、提交前统计剩余 `pending`：

```python
remaining_pending_count = await session.scalar(
    select(func.count())
    .select_from(CrawlCandidate)
    .where(
        CrawlCandidate.job_id == job_id,
        CrawlCandidate.review_status == CrawlCandidateReviewStatus.PENDING.value,
    ),
)
```

替换现有：

```python
if job.status == CrawlJobStatus.NEEDS_REVIEW.value:
    job.status = CrawlJobStatus.COMPLETED.value
```

为：

```python
if job.status in {
    CrawlJobStatus.NEEDS_REVIEW.value,
    CrawlJobStatus.PARTIALLY_COMPLETED.value,
}:
    job.status = (
        CrawlJobStatus.PARTIALLY_COMPLETED.value
        if int(remaining_pending_count or 0) > 0
        else CrawlJobStatus.COMPLETED.value
    )
```

保持 `canceled` 不改状态。

- [ ] **步骤 6：运行测试验证通过**

在 `backend` 目录运行：

```powershell
rtk uv run python -m unittest test.test_crawl_jobs_api.CrawlJobsApiTests.test_approve_partially_imported_job_keeps_remaining_candidates_reviewable test.test_crawl_jobs_api.CrawlJobsApiTests.test_approve_last_pending_candidate_completes_partially_imported_job test.test_crawl_jobs_api.CrawlJobsApiTests.test_approve_rejects_completed_job_even_with_pending_candidates
```

预期：`OK`。

- [ ] **步骤 7：Commit**

```powershell
rtk git add backend/app/api/crawl_jobs.py backend/test/test_crawl_jobs_api.py
rtk git commit -m "feat(抓取任务): 支持部分导入后继续审核"
```

## 任务 3：后端补全接口支持部分完成状态

**文件：**
- 修改：`backend/test/test_crawl_jobs_api.py`
- 修改：`backend/app/api/crawl_jobs.py`

- [ ] **步骤 1：编写失败的补全恢复状态测试**

在 `backend/test/test_crawl_jobs_api.py` 中加入：

```python
def test_enrich_selected_candidates_allows_partially_completed_job(self) -> None:
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
    self._seed_default_llm_profile()
    self._set_job_status(job_id, "partially_completed")
    candidates = self.client.get(f"/api/crawl-jobs/{job_id}/candidates").json()
    selected_id = candidates[0]["id"]

    class Summary:
        selected_count = 1
        enriched_count = 0
        unchanged_count = 1
        failed_count = 0

    async def fake_enrich_selected(*args, **kwargs):
        self.assertEqual(kwargs["job_id"], job_id)
        self.assertEqual(kwargs["candidate_ids"], [selected_id])
        return Summary()

    with patch("app.api.crawl_jobs.enrich_selected_crawl_candidates", new=fake_enrich_selected):
        response = self.client.post(
            f"/api/crawl-jobs/{job_id}/enrich",
            json={"candidate_ids": [selected_id]},
        )

    self.assertEqual(response.status_code, 200, msg=response.text)
    detail_response = self.client.get(f"/api/crawl-jobs/{job_id}")
    self.assertEqual(detail_response.json()["status"], "partially_completed")
```

- [ ] **步骤 2：编写失败的完成态补全拒绝测试**

在同一文件加入：

```python
def test_enrich_selected_candidates_rejects_completed_job(self) -> None:
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
    self._set_job_status(job_id, "completed")
    candidates = self.client.get(f"/api/crawl-jobs/{job_id}/candidates").json()

    response = self.client.post(
        f"/api/crawl-jobs/{job_id}/enrich",
        json={"candidate_ids": [candidates[0]["id"]]},
    )

    self.assertEqual(response.status_code, 409)
    self.assertEqual(response.json()["detail"], "抓取任务尚未进入审核状态")
```

- [ ] **步骤 3：运行测试验证失败**

在 `backend` 目录运行：

```powershell
rtk uv run python -m unittest test.test_crawl_jobs_api.CrawlJobsApiTests.test_enrich_selected_candidates_allows_partially_completed_job test.test_crawl_jobs_api.CrawlJobsApiTests.test_enrich_selected_candidates_rejects_completed_job
```

预期：第一个测试因 `partially_completed` 被拒绝而失败；第二个测试保持 409。

- [ ] **步骤 4：实现补全状态允许与恢复**

在 `backend/app/api/crawl_jobs.py` 的补全接口中，保存发起补全前的状态：

```python
review_status_before_enrich = job.status
if job.status == CrawlJobStatus.RUNNING.value:
    raise HTTPException(status_code=409, detail="候选信息正在补全中，请稍后再试")
if job.status not in {
    CrawlJobStatus.NEEDS_REVIEW.value,
    CrawlJobStatus.PARTIALLY_COMPLETED.value,
}:
    raise HTTPException(status_code=409, detail="抓取任务尚未进入审核状态")
```

在 `finally` 中把恢复状态从固定 `needs_review` 改为：

```python
final_job.status = review_status_before_enrich
final_job.updated_at = datetime.now(UTC)
await mark_crawl_job_run_finished(
    final_session,
    final_job,
    status=review_status_before_enrich,
    now=datetime.now(UTC),
)
```

- [ ] **步骤 5：运行测试验证通过**

在 `backend` 目录运行：

```powershell
rtk uv run python -m unittest test.test_crawl_jobs_api.CrawlJobsApiTests.test_enrich_selected_candidates_allows_partially_completed_job test.test_crawl_jobs_api.CrawlJobsApiTests.test_enrich_selected_candidates_rejects_completed_job
```

预期：`OK`。

- [ ] **步骤 6：Commit**

```powershell
rtk git add backend/app/api/crawl_jobs.py backend/test/test_crawl_jobs_api.py
rtk git commit -m "feat(抓取任务): 允许部分导入任务继续补全"
```

## 任务 4：前端类型与候选选择规则

**文件：**
- 修改：`frontend/src/types/index.ts`
- 修改：`frontend/src/features/crawl-review/client/reviewCandidates.ts`
- 修改：`frontend/src/features/crawl-review/client/reviewCandidates.test.ts`

- [ ] **步骤 1：编写失败的候选选择规则测试**

把 `frontend/src/features/crawl-review/client/reviewCandidates.test.ts` 中第一个测试改为：

```typescript
it('returns only pending candidate ids as reviewable', () => {
  const candidates = [
    buildCandidate({ id: 1, review_status: 'pending' }),
    buildCandidate({ id: 2, review_status: 'rejected' }),
    buildCandidate({ id: 3, review_status: 'accepted' }),
    buildCandidate({ id: 4, review_status: 'merged' }),
  ];

  expect(getReviewableCandidateIds(candidates)).toEqual([1]);
});
```

把第三个测试名和断言改为：

```typescript
it('prunes selected ids that no longer exist or are no longer pending', () => {
  const candidates = [
    buildCandidate({ id: 1, review_status: 'pending' }),
    buildCandidate({ id: 2, review_status: 'rejected' }),
    buildCandidate({ id: 3, review_status: 'accepted' }),
    buildCandidate({ id: 4, review_status: 'pending' }),
  ];

  expect(pruneSelectedCandidateIds([4, 3, 2, 999, 1], candidates)).toEqual([4, 1]);
});
```

- [ ] **步骤 2：运行测试验证失败**

在 `frontend` 目录运行：

```powershell
rtk npm test -- src/features/crawl-review/client/reviewCandidates.test.ts
```

预期：失败，`accepted` 仍被当作可审核。

- [ ] **步骤 3：实现最小前端类型和选择规则**

在 `frontend/src/types/index.ts` 的 `CrawlJobStatusDTO` 中加入：

```typescript
  | 'partially_completed'
```

在 `frontend/src/features/crawl-review/client/reviewCandidates.ts` 中改为：

```typescript
export const getReviewableCandidateIds = (
  candidates: CrawlCandidateDTO[],
): number[] =>
  candidates
    .filter((candidate) => candidate.review_status === 'pending')
    .map((candidate) => candidate.id);

export const getReviewableCandidateIdsWithoutEmail = (
  candidates: CrawlCandidateDTO[],
): number[] =>
  candidates
    .filter(
      (candidate) =>
        candidate.review_status === 'pending' && !candidate.email?.trim(),
    )
    .map((candidate) => candidate.id);
```

`pruneSelectedCandidateIds` 继续复用 `getReviewableCandidateIds`，无需额外分支。

- [ ] **步骤 4：运行测试验证通过**

在 `frontend` 目录运行：

```powershell
rtk npm test -- src/features/crawl-review/client/reviewCandidates.test.ts
```

预期：`1 passed` 或该文件内全部测试通过。

- [ ] **步骤 5：Commit**

```powershell
rtk git add frontend/src/types/index.ts frontend/src/features/crawl-review/client/reviewCandidates.ts frontend/src/features/crawl-review/client/reviewCandidates.test.ts
rtk git commit -m "feat(抓取任务): 收紧候选审核选择规则"
```

## 任务 5：前端任务页支持部分已导入状态

**文件：**
- 修改：`frontend/test/TasksPageCrawler.test.tsx`
- 修改：`frontend/src/pages/TasksPage.tsx`

- [ ] **步骤 1：编写失败的状态交互测试**

在 `frontend/test/TasksPageCrawler.test.tsx` 中加入：

```typescript
it("allows continuing review from a partially imported crawl job", async () => {
  const partiallyCompletedJob = {
    ...runningJob,
    status: "partially_completed",
  } as const;
  vi.mocked(listCrawlJobs).mockResolvedValue([partiallyCompletedJob]);
  vi.mocked(getCrawlJob).mockResolvedValue(partiallyCompletedJob);
  vi.mocked(listCrawlCandidates).mockResolvedValue([
    {
      id: 21,
      job_id: 7,
      professor_id: null,
      name: "张教授",
      email: "zhang@example.edu",
      title: null,
      university: "示例大学",
      school: "计算机学院",
      department: null,
      research_direction: null,
      recent_papers: [],
      profile_url: null,
      source_url: "https://example.edu/faculty",
      confidence: 0.86,
      field_confidence: null,
      evidence: null,
      review_status: "pending",
      created_at: "2026-04-26T10:02:00Z",
      updated_at: "2026-04-26T10:02:00Z",
    },
  ]);

  renderPage();

  fireEvent.click(screen.getByRole("button", { name: "教师抓取" }));
  expect(await screen.findByText("部分已导入")).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "删除" })).not.toBeInTheDocument();
  fireEvent.click(await screen.findByRole("button", { name: "查看详情" }));

  const dialog = await screen.findByRole("dialog", { name: "抓取任务详情" });
  fireEvent.click(
    within(dialog).getByRole("checkbox", { name: "选择候选导师 张教授" }),
  );
  fireEvent.click(
    within(dialog).getByRole("button", { name: "审核通过并导入" }),
  );

  await waitFor(() => {
    expect(approveCrawlCandidates).toHaveBeenCalledWith(7, [21]);
  });
});
```

- [ ] **步骤 2：编写失败的部分导入补全测试**

在同一文件加入：

```typescript
it("allows enrichment from a partially imported crawl job", async () => {
  const partiallyCompletedJob = {
    ...runningJob,
    status: "partially_completed",
  } as const;
  vi.mocked(listCrawlJobs).mockResolvedValue([partiallyCompletedJob]);
  vi.mocked(getCrawlJob).mockResolvedValue(partiallyCompletedJob);
  vi.mocked(listCrawlCandidates).mockResolvedValue([
    {
      id: 21,
      job_id: 7,
      professor_id: null,
      name: "张教授",
      email: null,
      title: null,
      university: "示例大学",
      school: "计算机学院",
      department: null,
      research_direction: null,
      recent_papers: [],
      profile_url: null,
      source_url: "https://example.edu/faculty",
      confidence: 0.86,
      field_confidence: null,
      evidence: null,
      review_status: "pending",
      created_at: "2026-04-26T10:02:00Z",
      updated_at: "2026-04-26T10:02:00Z",
    },
  ]);

  renderPage();

  fireEvent.click(screen.getByRole("button", { name: "教师抓取" }));
  fireEvent.click(await screen.findByRole("button", { name: "查看详情" }));

  const dialog = await screen.findByRole("dialog", { name: "抓取任务详情" });
  fireEvent.click(within(dialog).getByRole("button", { name: "全选无邮箱" }));
  fireEvent.click(within(dialog).getByRole("button", { name: "补全缺失信息" }));

  await waitFor(() => {
    expect(enrichCrawlCandidates).toHaveBeenCalledWith(7, [21]);
  });
});
```

- [ ] **步骤 3：运行测试验证失败**

在 `frontend` 目录运行：

```powershell
rtk npm test -- test/TasksPageCrawler.test.tsx
```

预期：失败，`partially_completed` 没有标签或详情中没有审核工具条。

- [ ] **步骤 4：实现状态标签、颜色和可审核判断**

在 `frontend/src/pages/TasksPage.tsx` 的 `CRAWL_JOB_STATUS_LABELS` 中加入：

```typescript
  partially_completed: "部分已导入",
```

在 `CRAWL_JOB_STATUS_TONES` 中加入：

```typescript
  partially_completed: "border-blue-200 bg-blue-50 text-blue-700",
```

把 `selectedCrawlJobCanReview` 改为：

```typescript
const selectedCrawlJobCanReview =
  selectedCrawlJob?.status === "needs_review" ||
  selectedCrawlJob?.status === "partially_completed" ||
  selectedCrawlJob?.status === "canceled" ||
  selectedCrawlJob?.status === "failed";
```

保持 `canDeleteCrawlJob` 不包含 `partially_completed`。

- [ ] **步骤 5：实现确认弹窗文案**

在 `handleApproveSelectedCrawlCandidates` 中把确认描述拆成变量：

```typescript
const approveDescription =
  selectedCrawlJob?.status === "canceled"
    ? "通过后，这些候选导师会写入导师库，当前抓取任务会保留已取消状态。"
    : selectedCrawlJob?.status === "partially_completed"
      ? "通过后会导入所选候选，任务中剩余待审核候选仍可继续处理。"
      : "通过后，这些候选导师会写入导师库；如仍有待审核候选，任务会标记为部分已导入。";
```

然后在 `confirm` 入参里使用：

```typescript
description: approveDescription,
```

- [ ] **步骤 6：运行测试验证通过**

在 `frontend` 目录运行：

```powershell
rtk npm test -- test/TasksPageCrawler.test.tsx
```

预期：该文件测试通过。

- [ ] **步骤 7：Commit**

```powershell
rtk git add frontend/src/pages/TasksPage.tsx frontend/test/TasksPageCrawler.test.tsx
rtk git commit -m "feat(抓取任务): 前端支持部分已导入状态"
```

## 任务 6：端到端回归验证

**文件：**
- 只在前面任务失败时修正对应实现文件和测试文件。

- [ ] **步骤 1：运行后端抓取任务相关测试**

在 `backend` 目录运行：

```powershell
rtk uv run python -m unittest test.test_crawl_job_models test.test_crawl_job_events test.test_crawl_jobs_api
```

预期：`OK`。

- [ ] **步骤 2：运行前端相关测试**

在 `frontend` 目录运行：

```powershell
rtk npm test -- src/features/crawl-review/client/reviewCandidates.test.ts test/TasksPageCrawler.test.tsx
```

预期：所有相关测试通过。

- [ ] **步骤 3：运行前端 lint**

在 `frontend` 目录运行：

```powershell
rtk npm run lint
```

预期：无 ESLint 错误。

- [ ] **步骤 4：检查暂存和未提交变更**

在仓库根目录运行：

```powershell
rtk git status --short
```

预期：只出现本功能相关文件，若仍存在开始前就有的 `frontend/test/ProfilePageOnboarding.test.tsx`，不要修改、暂存或回滚它。

- [ ] **步骤 5：最终 Commit**

如果步骤 1-3 触发了额外修正，提交修正：

```powershell
rtk git add backend/app/models/crawl_job.py backend/app/schemas/crawl_job.py backend/app/services/crawl_job_events.py backend/app/api/crawl_jobs.py backend/test/test_crawl_job_models.py backend/test/test_crawl_job_events.py backend/test/test_crawl_jobs_api.py frontend/src/types/index.ts frontend/src/features/crawl-review/client/reviewCandidates.ts frontend/src/features/crawl-review/client/reviewCandidates.test.ts frontend/src/pages/TasksPage.tsx frontend/test/TasksPageCrawler.test.tsx
rtk git commit -m "test(抓取任务): 验证部分导入流程"
```

如果没有额外修正，不创建空提交。

## 实施注意事项

- 不要把 `partially_completed` 作为删除终态；任务卡片不显示删除按钮。
- 不要允许 `completed` 状态继续审核或补全。
- 不要复用已 `accepted`、`merged` 或 `rejected` 的候选参与全选、补全和导入。
- 不要修改智能抓取重新抓取、暂停、取消、重试逻辑。
- 不要暂存或回滚开始前已有的 `frontend/test/ProfilePageOnboarding.test.tsx` 改动。
