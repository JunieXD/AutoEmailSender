# 抓取任务使用当前选中模型刷新实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 在用户触发抓取任务继续、重试或补全时，将任务绑定模型刷新为前端当前选中的模型，并保持运行中任务与 queued 任务的既有模型稳定性。

**架构：** 前端继续以 `SelectionContext.selectedLlmProfileId` 作为当前选中模型来源，只在用户触发下一次运行动作时随请求传给后端。后端新增共享解析/刷新逻辑，在状态校验通过后、任务进入运行或入队前更新 `crawl_jobs.llm_profile_id`，并记录审计日志。旧客户端不传模型 ID 时保持现有回退行为。

**技术栈：** FastAPI、SQLAlchemy AsyncSession、Pydantic、unittest、React、TypeScript、Vitest、Testing Library。

---

## 文件结构

- 修改：`backend/app/schemas/crawl_job.py`：为继续、重试、补全请求增加可选 `llm_profile_id`。
- 修改：`backend/app/api/crawl_jobs.py`：新增任务运行前模型解析/刷新辅助函数，并接入 `enrich`、`resume`、`retry`。
- 修改：`backend/test/test_crawl_jobs_api.py`：覆盖模型刷新、旧请求兼容、缺失模型错误和日志。
- 修改：`frontend/src/types/index.ts`：为重试 payload 增加可选 `llmProfileId`。
- 修改：`frontend/src/lib/api/crawlJobsApi.ts`：把 `llmProfileId` 转换为后端字段 `llm_profile_id`。
- 修改：`frontend/src/pages/TasksPage.tsx`：在继续、重试、补全动作中传入当前 `selectedLlmProfileId`。
- 修改：`frontend/src/pages/TasksPage.test.tsx`：验证页面动作调用 API 时携带当前选中模型。

---

## 任务 1：后端 Schema 支持运行前模型参数

**文件：**
- 修改：`backend/app/schemas/crawl_job.py`
- 修改：`backend/app/api/crawl_jobs.py`
- 测试：`backend/test/test_crawl_jobs_api.py`

- [ ] **步骤 1：编写失败的 resume payload 测试**

在 `backend/test/test_crawl_jobs_api.py` 的 `CrawlJobsApiTests` 中添加辅助方法：

```python
    def _create_llm_profile(self, name: str, model_name: str) -> int:
        response = self.client.post(
            "/api/llm-profiles",
            json={
                "name": name,
                "provider": "openai",
                "api_base_url": "https://api.example.com/v1",
                "api_key": "test-key",
                "model_name": model_name,
                "matcher_prompt_template": None,
                "writer_prompt_template": None,
                "temperature": 0.2,
                "max_tokens": None,
                "is_default": False,
            },
        )
        self.assertEqual(response.status_code, 201, msg=response.text)
        return int(response.json()["id"])
```

添加测试：

```python
    def test_resume_accepts_llm_profile_id_payload(self) -> None:
        old_profile_id = self._create_llm_profile("旧模型", "old-model")
        new_profile_id = self._create_llm_profile("新模型", "new-model")
        create_response = self.client.post(
            "/api/crawl-jobs",
            json={
                "university": "示例大学",
                "school": "计算机学院",
                "start_url": "https://example.edu/faculty",
                "llm_profile_id": old_profile_id,
            },
        )
        self.assertEqual(create_response.status_code, 201, msg=create_response.text)
        job_id = create_response.json()["id"]
        self._set_job_status(job_id, "paused")

        response = self.client.post(
            f"/api/crawl-jobs/{job_id}/resume",
            json={"llm_profile_id": new_profile_id},
        )

        self.assertEqual(response.status_code, 200, msg=response.text)
```

- [ ] **步骤 2：运行测试验证失败**

运行：`cd backend; uv run python -m unittest test.test_crawl_jobs_api.CrawlJobsApiTests.test_resume_accepts_llm_profile_id_payload`

预期：FAIL，`resume` 接口尚未声明请求体或请求体未被接受。

- [ ] **步骤 3：实现 Schema**

修改 `backend/app/schemas/crawl_job.py`：

```python
class CrawlJobRetryPayload(BaseModel):
    clear_existing_data: bool = True
    llm_profile_id: int | None = None

class CrawlJobResumePayload(BaseModel):
    llm_profile_id: int | None = None

class CrawlJobEnrichPayload(BaseModel):
    candidate_ids: list[int]
    llm_profile_id: int | None = None
```

- [ ] **步骤 4：让 resume 接口接收 payload**

修改 `backend/app/api/crawl_jobs.py`：导入 `CrawlJobResumePayload`，并将 `resume_crawl_job` 签名改为：

```python
async def resume_crawl_job(
    job_id: int,
    payload: CrawlJobResumePayload | None = None,
    session: AsyncSession = Depends(get_async_session),
) -> CrawlJob:
```

本步骤先不使用 `payload`，只保证请求体兼容。

- [ ] **步骤 5：运行测试验证通过**

运行：`cd backend; uv run python -m unittest test.test_crawl_jobs_api.CrawlJobsApiTests.test_resume_accepts_llm_profile_id_payload`

预期：PASS。

---

## 任务 2：后端实现任务模型刷新与审计日志

**文件：**
- 修改：`backend/app/api/crawl_jobs.py`
- 测试：`backend/test/test_crawl_jobs_api.py`

- [ ] **步骤 1：编写失败的刷新和日志测试**

在 `backend/test/test_crawl_jobs_api.py` 中添加辅助方法：

```python
    def _get_job_llm_profile_id(self, job_id: int) -> int | None:
        import sqlite3
        connection = sqlite3.connect(self.db_path)
        try:
            row = connection.execute(
                "SELECT llm_profile_id FROM crawl_jobs WHERE id = ?",
                (job_id,),
            ).fetchone()
            return None if row is None else row[0]
        finally:
            connection.close()

    def _list_operation_logs(self, event_name: str, entity_id: str) -> list[dict[str, object]]:
        import json
        import sqlite3
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        try:
            rows = connection.execute(
                """
                SELECT event_name, entity_id, metadata
                FROM operation_logs
                WHERE event_name = ? AND entity_id = ?
                ORDER BY id ASC
                """,
                (event_name, entity_id),
            ).fetchall()
            return [
                {
                    "event_name": row["event_name"],
                    "entity_id": row["entity_id"],
                    "metadata": json.loads(row["metadata"]),
                }
                for row in rows
            ]
        finally:
            connection.close()
```

添加测试：

```python
    def test_resume_refreshes_job_llm_profile_before_queueing(self) -> None:
        old_profile_id = self._create_llm_profile("旧模型", "old-model")
        new_profile_id = self._create_llm_profile("新模型", "new-model")
        create_response = self.client.post(
            "/api/crawl-jobs",
            json={
                "university": "示例大学",
                "school": "计算机学院",
                "start_url": "https://example.edu/faculty",
                "llm_profile_id": old_profile_id,
            },
        )
        self.assertEqual(create_response.status_code, 201, msg=create_response.text)
        job_id = create_response.json()["id"]
        self._set_job_status(job_id, "paused")

        response = self.client.post(
            f"/api/crawl-jobs/{job_id}/resume",
            json={"llm_profile_id": new_profile_id},
        )

        self.assertEqual(response.status_code, 200, msg=response.text)
        self.assertEqual(response.json()["llm_profile_id"], new_profile_id)
        self.assertEqual(self._get_job_llm_profile_id(job_id), new_profile_id)

    def test_resume_model_refresh_records_operation_log(self) -> None:
        old_profile_id = self._create_llm_profile("旧模型", "old-model")
        new_profile_id = self._create_llm_profile("新模型", "new-model")
        create_response = self.client.post(
            "/api/crawl-jobs",
            json={
                "university": "示例大学",
                "school": "计算机学院",
                "start_url": "https://example.edu/faculty",
                "llm_profile_id": old_profile_id,
            },
        )
        self.assertEqual(create_response.status_code, 201, msg=create_response.text)
        job_id = create_response.json()["id"]
        self._set_job_status(job_id, "paused")

        response = self.client.post(
            f"/api/crawl-jobs/{job_id}/resume",
            json={"llm_profile_id": new_profile_id},
        )

        self.assertEqual(response.status_code, 200, msg=response.text)
        logs = self._list_operation_logs("crawl_job.llm_profile_refreshed", str(job_id))
        self.assertEqual(len(logs), 1)
        self.assertEqual(logs[0]["metadata"]["old_llm_profile_id"], old_profile_id)
        self.assertEqual(logs[0]["metadata"]["old_model_name"], "old-model")
        self.assertEqual(logs[0]["metadata"]["new_llm_profile_id"], new_profile_id)
        self.assertEqual(logs[0]["metadata"]["new_model_name"], "new-model")
        self.assertEqual(logs[0]["metadata"]["trigger"], "resume")
```

- [ ] **步骤 2：运行测试验证失败**

运行：`cd backend; uv run python -m unittest test.test_crawl_jobs_api.CrawlJobsApiTests.test_resume_refreshes_job_llm_profile_before_queueing test.test_crawl_jobs_api.CrawlJobsApiTests.test_resume_model_refresh_records_operation_log`

预期：FAIL，任务绑定模型未更新或没有刷新日志。

- [ ] **步骤 3：实现刷新辅助函数**

在 `backend/app/api/crawl_jobs.py` 新增：

```python
async def _resolve_and_refresh_crawl_job_llm_profile(
    session: AsyncSession,
    job: CrawlJob,
    requested_llm_profile_id: int | None,
    *,
    trigger: str,
) -> LLMProfile:
    old_profile: LLMProfile | None = None
    if job.llm_profile_id is not None:
        old_profile = await session.get(LLMProfile, job.llm_profile_id)

    if requested_llm_profile_id is not None:
        llm_profile = await session.get(LLMProfile, requested_llm_profile_id)
        if llm_profile is None:
            raise HTTPException(status_code=404, detail="模型配置不存在")
    elif old_profile is not None:
        llm_profile = old_profile
    else:
        llm_profile = await session.scalar(
            select(LLMProfile)
            .where(LLMProfile.is_default.is_(True))
            .order_by(LLMProfile.created_at.asc(), LLMProfile.id.asc())
            .limit(1),
        )
        if llm_profile is None:
            raise HTTPException(status_code=409, detail="请先配置可用的 LLM Profile")

    if job.llm_profile_id != llm_profile.id:
        await record_operation_log(
            session,
            category="crawler",
            event_name="crawl_job.llm_profile_refreshed",
            entity_type="crawl_job",
            entity_id=str(job.id),
            metadata={
                "old_llm_profile_id": job.llm_profile_id,
                "old_model_name": old_profile.model_name if old_profile is not None else None,
                "new_llm_profile_id": llm_profile.id,
                "new_model_name": llm_profile.model_name,
                "trigger": trigger,
            },
        )
        job.llm_profile_id = llm_profile.id

    return llm_profile
```

- [ ] **步骤 4：接入 resume**

在 `resume_crawl_job` 状态校验通过后、设置 `job.status = CrawlJobStatus.QUEUED.value` 前调用：

```python
    await _resolve_and_refresh_crawl_job_llm_profile(
        session,
        job,
        payload.llm_profile_id if payload is not None else None,
        trigger="resume",
    )
```

- [ ] **步骤 5：运行测试验证通过**

运行：`cd backend; uv run python -m unittest test.test_crawl_jobs_api.CrawlJobsApiTests.test_resume_refreshes_job_llm_profile_before_queueing test.test_crawl_jobs_api.CrawlJobsApiTests.test_resume_model_refresh_records_operation_log`

预期：PASS。
---

## 任务 3：后端接入重试和补全刷新

**文件：**
- 修改：`backend/app/api/crawl_jobs.py`
- 测试：`backend/test/test_crawl_jobs_api.py`

- [ ] **步骤 1：编写失败的 retry 测试**

在 `backend/test/test_crawl_jobs_api.py` 添加：

```python
    def test_retry_refreshes_job_llm_profile_before_queueing(self) -> None:
        old_profile_id = self._create_llm_profile("旧模型", "old-model")
        new_profile_id = self._create_llm_profile("新模型", "new-model")
        create_response = self.client.post(
            "/api/crawl-jobs",
            json={
                "university": "示例大学",
                "school": "计算机学院",
                "start_url": "https://example.edu/faculty",
                "llm_profile_id": old_profile_id,
            },
        )
        self.assertEqual(create_response.status_code, 201, msg=create_response.text)
        job_id = create_response.json()["id"]
        self._set_job_status(job_id, "failed")

        response = self.client.post(
            f"/api/crawl-jobs/{job_id}/retry",
            json={"clear_existing_data": False, "llm_profile_id": new_profile_id},
        )

        self.assertEqual(response.status_code, 200, msg=response.text)
        self.assertEqual(response.json()["llm_profile_id"], new_profile_id)
        self.assertEqual(self._get_job_llm_profile_id(job_id), new_profile_id)
        logs = self._list_operation_logs("crawl_job.llm_profile_refreshed", str(job_id))
        self.assertEqual(logs[-1]["metadata"]["trigger"], "retry")
```

- [ ] **步骤 2：编写失败的 enrich 测试**

添加候选辅助方法：

```python
    def _seed_candidate(self, job_id: int, *, name: str, profile_url: str) -> None:
        import sqlite3
        connection = sqlite3.connect(self.db_path)
        try:
            connection.execute(
                """
                INSERT INTO crawl_candidates (
                    job_id, name, profile_url, confidence, review_status, created_at, updated_at
                ) VALUES (?, ?, ?, 0.9, 'pending', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                """,
                (job_id, name, profile_url),
            )
            connection.commit()
        finally:
            connection.close()

    def _latest_candidate_id(self, job_id: int) -> int:
        import sqlite3
        connection = sqlite3.connect(self.db_path)
        try:
            row = connection.execute(
                "SELECT id FROM crawl_candidates WHERE job_id = ? ORDER BY id DESC LIMIT 1",
                (job_id,),
            ).fetchone()
            self.assertIsNotNone(row)
            return int(row[0])
        finally:
            connection.close()
```

添加测试：

```python
    def test_enrich_refreshes_job_llm_profile_before_running(self) -> None:
        old_profile_id = self._create_llm_profile("旧模型", "old-model")
        new_profile_id = self._create_llm_profile("新模型", "new-model")
        create_response = self.client.post(
            "/api/crawl-jobs",
            json={
                "university": "示例大学",
                "school": "计算机学院",
                "start_url": "https://example.edu/faculty",
                "llm_profile_id": old_profile_id,
            },
        )
        self.assertEqual(create_response.status_code, 201, msg=create_response.text)
        job_id = create_response.json()["id"]
        self._set_job_status(job_id, "needs_review")
        self._seed_candidate(job_id, name="王老师", profile_url="https://example.edu/wang")
        candidate_id = self._latest_candidate_id(job_id)

        from app.services.crawl_job_runtime import SelectedCandidateEnrichmentSummary

        async def fake_enrich_selected_crawl_candidates(*args: object, **kwargs: object) -> SelectedCandidateEnrichmentSummary:
            llm_profile = kwargs["llm_profile"]
            self.assertEqual(llm_profile.id, new_profile_id)
            return SelectedCandidateEnrichmentSummary(
                selected_count=1,
                enriched_count=1,
                unchanged_count=0,
                failed_count=0,
            )

        with patch(
            "app.api.crawl_jobs.enrich_selected_crawl_candidates",
            new=fake_enrich_selected_crawl_candidates,
        ):
            response = self.client.post(
                f"/api/crawl-jobs/{job_id}/enrich",
                json={"candidate_ids": [candidate_id], "llm_profile_id": new_profile_id},
            )

        self.assertEqual(response.status_code, 200, msg=response.text)
        self.assertEqual(self._get_job_llm_profile_id(job_id), new_profile_id)
        logs = self._list_operation_logs("crawl_job.llm_profile_refreshed", str(job_id))
        self.assertEqual(logs[-1]["metadata"]["trigger"], "enrich")
```

- [ ] **步骤 3：运行测试验证失败**

运行：`cd backend; uv run python -m unittest test.test_crawl_jobs_api.CrawlJobsApiTests.test_retry_refreshes_job_llm_profile_before_queueing test.test_crawl_jobs_api.CrawlJobsApiTests.test_enrich_refreshes_job_llm_profile_before_running`

预期：FAIL，重试或补全仍使用旧模型。

- [ ] **步骤 4：接入 retry**

在 `retry_crawl_job` 状态校验通过后、清理数据前调用：

```python
    await _resolve_and_refresh_crawl_job_llm_profile(
        session,
        job,
        payload.llm_profile_id,
        trigger="retry",
    )
```

- [ ] **步骤 5：接入 enrich 并去重旧模型解析**

在 `enrich_crawl_candidates` 中，保留 `running` 与状态集合校验。将原有手动读取 `job.llm_profile_id` 与默认模型的代码替换为：

```python
    llm_profile = await _resolve_and_refresh_crawl_job_llm_profile(
        session,
        job,
        payload.llm_profile_id,
        trigger="enrich",
    )
```

确保这段代码在 `job.status = CrawlJobStatus.RUNNING.value` 之前执行。

- [ ] **步骤 6：补充不存在模型测试**

添加：

```python
    def test_enrich_rejects_missing_requested_llm_profile(self) -> None:
        profile_id = self._create_llm_profile("旧模型", "old-model")
        create_response = self.client.post(
            "/api/crawl-jobs",
            json={
                "university": "示例大学",
                "school": "计算机学院",
                "start_url": "https://example.edu/faculty",
                "llm_profile_id": profile_id,
            },
        )
        self.assertEqual(create_response.status_code, 201, msg=create_response.text)
        job_id = create_response.json()["id"]
        self._set_job_status(job_id, "needs_review")

        response = self.client.post(
            f"/api/crawl-jobs/{job_id}/enrich",
            json={"candidate_ids": [999], "llm_profile_id": 999999},
        )

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["detail"], "模型配置不存在")
        self.assertEqual(self._get_job_llm_profile_id(job_id), profile_id)
```

- [ ] **步骤 7：运行后端相关测试**

运行：`cd backend; uv run python -m unittest test.test_crawl_jobs_api.CrawlJobsApiTests`

预期：PASS。

---

## 任务 4：前端 API 客户端传递当前选中模型

**文件：**
- 修改：`frontend/src/types/index.ts`
- 修改：`frontend/src/lib/api/crawlJobsApi.ts`

- [ ] **步骤 1：更新类型定义**

修改 `frontend/src/types/index.ts`：

```typescript
export interface CrawlJobRetryPayloadDTO {
  clear_existing_data: boolean;
  llmProfileId?: number | null;
}
```

- [ ] **步骤 2：更新 enrich API 函数签名**

修改 `frontend/src/lib/api/crawlJobsApi.ts`：

```typescript
export const enrichCrawlCandidates = (
  jobId: number,
  candidateIds: number[],
  llmProfileId?: number | null,
) =>
  apiFetch<CrawlJobEnrichResultDTO>(`/api/crawl-jobs/${jobId}/enrich`, {
    method: 'POST',
    body: JSON.stringify({
      candidate_ids: candidateIds,
      llm_profile_id: llmProfileId ?? undefined,
    }),
  });
```

- [ ] **步骤 3：更新 resume API 函数签名**

修改 `frontend/src/lib/api/crawlJobsApi.ts`：

```typescript
export const resumeCrawlJob = (jobId: number, llmProfileId?: number | null) =>
  apiFetch<CrawlJobDTO>(`/api/crawl-jobs/${jobId}/resume`, {
    method: 'POST',
    body: JSON.stringify({ llm_profile_id: llmProfileId ?? undefined }),
  });
```

- [ ] **步骤 4：更新 retry API 请求体**

修改 `frontend/src/lib/api/crawlJobsApi.ts`：

```typescript
export const retryCrawlJob = (jobId: number, payload: CrawlJobRetryPayloadDTO) =>
  apiFetch<CrawlJobDTO>(`/api/crawl-jobs/${jobId}/retry`, {
    method: 'POST',
    body: JSON.stringify({
      clear_existing_data: payload.clear_existing_data,
      llm_profile_id: payload.llmProfileId ?? undefined,
    }),
  });
```

- [ ] **步骤 5：运行类型检查**

运行：`cd frontend; npm run lint`

预期：PASS，或只出现与本次修改无关的既有 lint 问题。
---

## 任务 5：前端页面动作携带当前选中模型

**文件：**
- 修改：`frontend/src/pages/TasksPage.tsx`
- 测试：`frontend/src/pages/TasksPage.test.tsx`

- [ ] **步骤 1：编写失败的补全传参测试**

在 `frontend/src/pages/TasksPage.test.tsx` 中添加测试。使用现有 `apiMocks` 和 `selectedLlmProfileId: 2` mock，构造可审核抓取任务，点击「补全缺失信息」，断言 API 第 3 个参数为 `2`。

```typescript
it("passes selected llm profile when enriching crawl candidates", async () => {
  apiMocks.listCrawlJobs.mockResolvedValue([buildCrawlJob({ status: "needs_review" })]);
  apiMocks.getCrawlJob.mockResolvedValue(buildCrawlJob({ status: "needs_review" }));
  apiMocks.getCrawlJobEvents.mockResolvedValue([]);
  apiMocks.listCrawlPages.mockResolvedValue([]);
  apiMocks.listCrawlCandidates.mockResolvedValue([
    buildCrawlCandidate({ id: 501, name: "王老师", email: null }),
  ]);
  apiMocks.enrichCrawlCandidates.mockResolvedValue({
    selected_count: 1,
    enriched_count: 1,
    unchanged_count: 0,
    failed_count: 0,
    message: "补全完成",
  });

  render(<TasksPage />, { wrapper: MemoryRouter });

  const jobButton = await screen.findByText(/王老师|示例大学|计算机学院/);
  fireEvent.click(jobButton.closest("button") ?? jobButton);
  const candidateCheckbox = await screen.findByRole("checkbox");
  fireEvent.click(candidateCheckbox);
  fireEvent.click(await screen.findByRole("button", { name: "补全缺失信息" }));

  await waitFor(() => {
    expect(apiMocks.enrichCrawlCandidates).toHaveBeenCalledWith(1, [501], 2);
  });
});
```

如果现有 builder 名称不同，以测试文件中已有 `buildCrawlJob`、`buildCrawlCandidate` 实际函数名为准，不新增重复 builder。

- [ ] **步骤 2：编写失败的继续和重试传参测试**

添加两个测试，分别点击 paused 任务的「继续」和 failed 任务的「重新抓取」按钮，断言传入 `2`。

```typescript
it("passes selected llm profile when resuming a crawl job", async () => {
  const pausedJob = buildCrawlJob({ id: 31, status: "paused" });
  apiMocks.listCrawlJobs.mockResolvedValue([pausedJob]);
  apiMocks.resumeCrawlJob.mockResolvedValue({ ...pausedJob, status: "queued" });

  render(<TasksPage />, { wrapper: MemoryRouter });

  fireEvent.click(await screen.findByRole("button", { name: "继续" }));

  await waitFor(() => {
    expect(apiMocks.resumeCrawlJob).toHaveBeenCalledWith(31, 2);
  });
});

it("passes selected llm profile when retrying a crawl job", async () => {
  const failedJob = buildCrawlJob({ id: 32, status: "failed" });
  apiMocks.listCrawlJobs.mockResolvedValue([failedJob]);
  apiMocks.retryCrawlJob.mockResolvedValue({ ...failedJob, status: "queued" });

  render(<TasksPage />, { wrapper: MemoryRouter });

  fireEvent.click(await screen.findByRole("button", { name: /重新抓取|重试/ }));

  await waitFor(() => {
    expect(apiMocks.retryCrawlJob).toHaveBeenCalledWith(
      32,
      expect.objectContaining({ llmProfileId: 2 }),
    );
  });
});
```

- [ ] **步骤 3：运行测试验证失败**

运行：`cd frontend; npm run test -- TasksPage.test.tsx`

预期：FAIL，调用参数没有包含当前选中模型。

- [ ] **步骤 4：实现补全传参和未选中保护**

修改 `frontend/src/pages/TasksPage.tsx` 的 `handleEnrichSelectedCrawlCandidates`：

```typescript
    if (!selectedLlmProfileId) {
      notifyError("补全候选导师信息失败", "请先选择模型配置");
      return;
    }
```

并将调用改为：

```typescript
      const result = await enrichCrawlCandidates(
        selectedCrawlJobId,
        selectedReviewableCrawlCandidateIds,
        selectedLlmProfileId,
      );
```

- [ ] **步骤 5：实现继续传参和未选中保护**

在 `handleResumeCrawlJob` 中，调用 API 前加入：

```typescript
    if (!selectedLlmProfileId) {
      notifyError("继续抓取任务失败", "请先选择模型配置");
      return;
    }
```

并将调用改为：

```typescript
      const job = await resumeCrawlJob(jobId, selectedLlmProfileId);
```

如果原代码变量名不是 `job`，保持现有变量名，仅调整参数。

- [ ] **步骤 6：实现重试传参和未选中保护**

在 `handleRetryCrawlJob` 中，调用 API 前加入：

```typescript
    if (!selectedLlmProfileId) {
      notifyError("重新抓取任务失败", "请先选择模型配置");
      return;
    }
```

并将 payload 改为包含 `llmProfileId`：

```typescript
      const job = await retryCrawlJob(jobId, {
        clear_existing_data: true,
        llmProfileId: selectedLlmProfileId,
      });
```

如果现有重试逻辑会根据确认结果决定 `clear_existing_data`，保留原值，只新增 `llmProfileId: selectedLlmProfileId`。

- [ ] **步骤 7：运行前端页面测试**

运行：`cd frontend; npm run test -- TasksPage.test.tsx`

预期：PASS。

---

## 任务 6：全量验证与收尾

**文件：**
- 检查：`backend/app/schemas/crawl_job.py`
- 检查：`backend/app/api/crawl_jobs.py`
- 检查：`backend/test/test_crawl_jobs_api.py`
- 检查：`frontend/src/types/index.ts`
- 检查：`frontend/src/lib/api/crawlJobsApi.ts`
- 检查：`frontend/src/pages/TasksPage.tsx`
- 检查：`frontend/src/pages/TasksPage.test.tsx`

- [ ] **步骤 1：运行后端抓取 API 测试**

运行：`cd backend; uv run python -m unittest test.test_crawl_jobs_api.CrawlJobsApiTests`

预期：PASS。

- [ ] **步骤 2：运行前端相关测试**

运行：`cd frontend; npm run test -- TasksPage.test.tsx`

预期：PASS。

- [ ] **步骤 3：运行前端 lint**

运行：`cd frontend; npm run lint`

预期：PASS，或只报告与本次改动无关的既有问题。

- [ ] **步骤 4：检查 diff**

运行：`git diff -- backend/app/schemas/crawl_job.py backend/app/api/crawl_jobs.py backend/test/test_crawl_jobs_api.py frontend/src/types/index.ts frontend/src/lib/api/crawlJobsApi.ts frontend/src/pages/TasksPage.tsx frontend/src/pages/TasksPage.test.tsx`

确认：

- `running` 状态拒绝逻辑仍在模型刷新前执行。
- `queued` 任务不会因为后台 worker 启动而刷新模型。
- 旧客户端不传 `llm_profile_id` 时仍使用任务绑定模型或默认模型。
- 只有用户触发的 `resume`、`retry`、`enrich` 会刷新任务绑定模型。

- [ ] **步骤 5：Commit**

```powershell
git add backend/app/schemas/crawl_job.py backend/app/api/crawl_jobs.py backend/test/test_crawl_jobs_api.py frontend/src/types/index.ts frontend/src/lib/api/crawlJobsApi.ts frontend/src/pages/TasksPage.tsx frontend/src/pages/TasksPage.test.tsx
git commit -m "feat(crawler): use selected model for next crawl run"
```

---

## 自检清单

- 规格目标「只在用户触发下一次运行时刷新」由任务 2、任务 3 和任务 5 覆盖。
- 规格目标「运行中任务保持原样」由后端现有 `running` 校验和任务 6 diff 检查覆盖。
- 规格目标「queued 任务仍用入队时模型」由不改后台 worker 和任务 6 diff 检查覆盖。
- 规格目标「不新增后端当前选中模型」由前端传参方案覆盖。
- 规格目标「写审计日志」由任务 2 测试覆盖。
- 兼容旧客户端由 `llm_profile_id` 可选字段和任务 3 后端测试覆盖。