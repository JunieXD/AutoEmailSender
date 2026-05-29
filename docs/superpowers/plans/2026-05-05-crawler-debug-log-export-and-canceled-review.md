# 智能抓取诊断日志导出与取消任务候选审核 实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 `superpowers:subagent-driven-development`（推荐）或 `superpowers:executing-plans` 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 默认开启智能抓取 JSONL 调试记录，并在个人中心导出某次抓取任务的完整 JSONL；同时允许 `canceled` 抓取任务在已有候选时继续审核入库。

**架构：** 后端继续把抓取调试事件写入任务级 JSONL 文件，新增一个按抓取任务 ID 导出的下载接口。个人中心的“开发诊断日志”增加最近 50 次抓取任务下拉框，用户选中某次任务后直接导出对应 JSONL。候选审核沿用现有任务详情页和入库链路，只放宽 `canceled` 任务的审核限制，`paused` 继续拒绝。

**技术栈：** FastAPI、SQLAlchemy、SQLite、Pydantic、React、Vite、TypeScript、Vitest / unittest。

---

### 任务 1：把抓取 JSONL 调试默认打开

**文件：**
- 修改：`backend/app/core/config.py`
- 修改：`backend/.env.example`
- 测试：`backend/test/test_crawl_job_runtime.py` 或 `backend/test/test_operation_logs.py` 中新增配置断言

- [ ] **步骤 1：编写失败的测试**

```python
def test_crawler_debug_defaults_to_enabled(monkeypatch):
    monkeypatch.delenv("CRAWLER_DEBUG", raising=False)
    get_settings.cache_clear()
    settings = get_settings()
    assert settings.crawler_debug_enabled is True
```

- [ ] **步骤 2：运行测试验证失败**

运行：`rtk pwsh -NoLogo -NoProfile -Command "uv run --directory backend python -m unittest backend.test.test_operation_logs.OperationLogTests.test_crawler_debug_defaults_to_enabled -v"`

预期：失败，因为当前默认值还是 `False`。

- [ ] **步骤 3：编写最少实现代码**

```python
crawler_debug_enabled=_get_bool_env("CRAWLER_DEBUG", True),
```

- [ ] **步骤 4：运行测试验证通过**

运行：`rtk pwsh -NoLogo -NoProfile -Command "uv run --directory backend python -m unittest backend.test.test_operation_logs.OperationLogTests.test_crawler_debug_defaults_to_enabled -v"`

预期：PASS。

- [ ] **步骤 5：Commit**

```bash
git add backend/app/core/config.py backend/.env.example backend/test/test_operation_logs.py
git commit -m "feat(crawler): 默认开启调试日志"
```

### 任务 2：新增抓取任务 JSONL 导出接口

**文件：**
- 修改：`backend/app/api/diagnostics.py`
- 创建：`backend/app/schemas/diagnostics.py` 中新增导出 DTO（如果现有 DTO 不够用）
- 创建或修改：`backend/app/services/crawler_debug.py`
- 测试：`backend/test/test_crawl_job_runtime.py` 或新建 `backend/test/test_crawler_debug.py`

- [ ] **步骤 1：编写失败的测试**

```python
def test_export_crawler_debug_jsonl_by_job_id(self):
    response = self.client.get("/api/diagnostics/crawler-debug/12/export")
    self.assertEqual(response.status_code, 200)
    self.assertEqual(response.headers["content-type"], "application/jsonl")
```

- [ ] **步骤 2：运行测试验证失败**

运行：`rtk pwsh -NoLogo -NoProfile -Command "uv run --directory backend python -m unittest backend.test.test_crawler_debug -v"`

预期：失败，因为接口还不存在。

- [ ] **步骤 3：编写最少实现代码**

```python
@router.get("/crawler-debug/{job_id}/export")
async def export_crawler_debug_jsonl(job_id: int):
    ...
```

- [ ] **步骤 4：运行测试验证通过**

运行：`rtk pwsh -NoLogo -NoProfile -Command "uv run --directory backend python -m unittest backend.test.test_crawler_debug -v"`

预期：PASS。

- [ ] **步骤 5：Commit**

```bash
git add backend/app/api/diagnostics.py backend/app/services/crawler_debug.py backend/test/test_crawler_debug.py
git commit -m "feat(crawler): 支持导出抓取调试日志"
```

### 任务 3：提供最近 50 次抓取任务供下拉选择

**文件：**
- 修改：`backend/app/api/crawl_jobs.py`
- 修改：`frontend/src/lib/api/crawlJobsApi.ts`
- 修改：`frontend/src/lib/api/diagnosticsApi.ts` 或单独增加抓取任务列表 API
- 测试：`backend/test/test_crawl_jobs_api.py`

- [ ] **步骤 1：编写失败的测试**

```python
def test_list_recent_crawl_jobs_returns_50_items(self):
    response = self.client.get("/api/crawl-jobs?limit=50")
    self.assertEqual(response.status_code, 200)
    self.assertLessEqual(len(response.json()), 50)
```

- [ ] **步骤 2：运行测试验证失败**

运行：`rtk pwsh -NoLogo -NoProfile -Command "uv run --directory backend python -m unittest backend.test.test_crawl_jobs_api -v"`

预期：失败或未覆盖新返回字段。

- [ ] **步骤 3：编写最少实现代码**

```python
@router.get("/recent", response_model=list[CrawlJobSummaryRead])
async def list_recent_crawl_jobs(...):
    ...
```

- [ ] **步骤 4：运行测试验证通过**

运行：`rtk pwsh -NoLogo -NoProfile -Command "uv run --directory backend python -m unittest backend.test.test_crawl_jobs_api -v"`

预期：PASS。

- [ ] **步骤 5：Commit**

```bash
git add backend/app/api/crawl_jobs.py backend/test/test_crawl_jobs_api.py frontend/src/lib/api/crawlJobsApi.ts
git commit -m "feat(crawler): 提供最近抓取任务列表"
```

### 任务 4：在个人中心诊断面板里接入抓取任务下拉框和导出按钮

**文件：**
- 修改：`frontend/src/components/organisms/DiagnosticLogPanel.tsx`
- 修改：`frontend/src/lib/api/diagnosticsApi.ts`
- 修改：`frontend/src/lib/api/crawlJobsApi.ts`
- 测试：`frontend/src/components/organisms/DiagnosticLogPanel.test.tsx` 或对应现有测试文件

- [ ] **步骤 1：编写失败的测试**

```tsx
it("disables export until a crawl job is selected", async () => {
  render(<DiagnosticLogPanel />);
  expect(screen.getByRole("button", { name: "导出抓取日志" })).toBeDisabled();
});
```

- [ ] **步骤 2：运行测试验证失败**

运行：`rtk pwsh -NoLogo -NoProfile -Command "npm test -- DiagnosticLogPanel"`

预期：失败，因为 UI 还没有下拉框和新按钮状态。

- [ ] **步骤 3：编写最少实现代码**

```tsx
const [selectedCrawlJobId, setSelectedCrawlJobId] = useState<string>("");
```

- [ ] **步骤 4：运行测试验证通过**

运行：`rtk pwsh -NoLogo -NoProfile -Command "npm test -- DiagnosticLogPanel"`

预期：PASS。

- [ ] **步骤 5：Commit**

```bash
git add frontend/src/components/organisms/DiagnosticLogPanel.tsx frontend/src/lib/api/diagnosticsApi.ts frontend/src/lib/api/crawlJobsApi.ts frontend/src/components/organisms/DiagnosticLogPanel.test.tsx
git commit -m "feat(profile): 导出指定抓取任务日志"
```

### 任务 5：允许 canceled 任务在有候选时继续审核

**文件：**
- 修改：`backend/app/api/crawl_jobs.py`
- 修改：`backend/test/test_crawl_jobs_api.py`
- 如需展示入口状态，修改：`frontend/src/pages/ProfessorsPage.tsx` 或 `frontend/src/components/...`

- [ ] **步骤 1：编写失败的测试**

```python
def test_canceled_crawl_job_can_still_be_approved_when_candidates_exist(self):
    ...
    response = self.client.post(f"/api/crawl-jobs/{job_id}/approve", json={"candidate_ids": [candidate_id]})
    self.assertEqual(response.status_code, 200)
```

- [ ] **步骤 2：运行测试验证失败**

运行：`rtk pwsh -NoLogo -NoProfile -Command "uv run --directory backend python -m unittest backend.test.test_crawl_jobs_api -v"`

预期：失败，因为当前接口只接受 `needs_review`。

- [ ] **步骤 3：编写最少实现代码**

```python
if job.status not in {CrawlJobStatus.NEEDS_REVIEW.value, CrawlJobStatus.CANCELED.value}:
    raise HTTPException(status_code=409, detail="抓取任务尚未进入审核状态")
```

- [ ] **步骤 4：运行测试验证通过**

运行：`rtk pwsh -NoLogo -NoProfile -Command "uv run --directory backend python -m unittest backend.test.test_crawl_jobs_api -v"`

预期：PASS。

- [ ] **步骤 5：Commit**

```bash
git add backend/app/api/crawl_jobs.py backend/test/test_crawl_jobs_api.py
git commit -m "feat(crawler): 允许取消任务继续审核候选"
```

### 任务 6：联调和回归验证

**文件：**
- 受影响文件全部回归检查

- [ ] **步骤 1：运行后端测试**

运行：`rtk pwsh -NoLogo -NoProfile -Command "uv run --directory backend python -m unittest discover backend/test -v"`

- [ ] **步骤 2：运行前端测试和构建**

运行：`rtk pwsh -NoLogo -NoProfile -Command "cd frontend; npm run lint; npm run build"`

- [ ] **步骤 3：人工验证**

验证点：

```text
1. 个人中心可展开“开发诊断日志”。
2. 能看到最近 50 次抓取任务。
3. 选择某个任务后可导出对应 JSONL。
4. 取消后的抓取任务，只要有候选，就能继续审核并入库。
5. 暂停任务仍然不能走继续审核。
```

---

**遗漏检查：**
- 默认开启调试日志：已覆盖于任务 1。
- 任务级 JSONL 导出：已覆盖于任务 2 和 4。
- 最近 50 次抓取任务下拉框：已覆盖于任务 3 和 4。
- `canceled` 可继续审核：已覆盖于任务 5。
- `paused` 不进入补救：已覆盖于任务 5。
- 回归验证：已覆盖于任务 6。
