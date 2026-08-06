# Crawler V2 迁移 V1 候选发现机制实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 让 Crawler V2 在短生命周期 Worker 架构下迁移 V1 的候选数量上限、候选质量门禁、详情页链接保存、chunk 拆分和无效输出保护机制。

**架构：** Page Worker 只抓页面并生成 chunk，不发现 URL。Chunk Worker 每次只处理一个后端切好的 chunk，只输出一次结构化 JSON；后端校验输出、补全 Markdown 详情页链接、复用候选保存服务，并在超量或异常时拆分/重试，绝不为省 token 降低数据质量。

**技术栈：** Python 3、FastAPI、SQLAlchemy async、Pydantic、unittest、uv。

---

## 文件结构

- 修改：`backend/app/services/crawler_v2_chunk_worker.py`：强化 prompt；增加 10 人上限、Markdown `profile_url` 补全、详情页 URL 过滤、超量拆分、无效输出 retry；改用共用保存服务。
- 修改：`backend/app/services/crawler_tools.py`：把 V1 候选标准化、拒收、去重合并、`identity_key`、`field_sources` 生成抽成 V1/V2 共用服务，保持 `save_candidate_batch` 返回协议不变。
- 修改：`backend/app/services/crawler_chunk_runtime.py`：暴露 V2 可复用的 chunk 拆分入口，复用现有 `split_chunk_content` 和最大拆分深度规则。
- 修改：`backend/app/services/crawl_job_runtime.py`：`enrich_selected_crawl_candidates` / `_enrich_selected_candidates_concurrent` 过滤无 `profile_url` 候选，返回跳过统计，不把它们送入 LLM 补全。
- 修改：`backend/app/api/crawl_jobs.py`：`POST /api/crawl-jobs/{job_id}/enrich` 返回跳过数量，并在消息中说明。
- 修改：`backend/app/schemas/crawl_job.py`：`CrawlJobEnrichResult` 增加向后兼容字段 `skipped_count`，不删除旧字段。
- 测试：`backend/test/test_crawler_v2_chunk_worker.py`、`backend/test/test_crawler_tools.py`、`backend/test/test_crawler_chunk_runtime.py`、`backend/test/test_crawl_jobs_api.py`、`backend/test/test_crawl_job_runtime.py`。

---

### 任务 1：锁定 Chunk Worker prompt 约束

**文件：**
- 修改：`backend/test/test_crawler_v2_chunk_worker.py`
- 修改：`backend/app/services/crawler_v2_chunk_worker.py`

- [ ] **步骤 1：编写失败的 prompt 测试**

在 `CrawlerV2ChunkWorkerTests` 增加：

```python
def test_chunk_prompt_includes_v1_quality_constraints(self) -> None:
    from app.services.crawler_v2_chunk_worker import build_v2_chunk_prompt
    prompt = build_v2_chunk_prompt(
        university="示例大学",
        school="计算机学院",
        source_url="https://example.edu/faculty",
        chunk_content="[张三](https://example.edu/zhang.html) 教授",
    )
    self.assertIn("最多 10 个候选", prompt)
    self.assertIn("缺少 email 且缺少 profile_url", prompt)
    self.assertIn("Markdown", prompt)
    self.assertIn("导师个人主页", prompt)
    self.assertIn("不能放入 discovered_urls", prompt)
    self.assertIn("只输出一个 JSON 对象", prompt)
```

- [ ] **步骤 2：运行测试验证失败**

运行：`cd backend; uv run python -m unittest test.test_crawler_v2_chunk_worker.CrawlerV2ChunkWorkerTests.test_chunk_prompt_includes_v1_quality_constraints`

预期：FAIL，prompt 缺少新增约束。

- [ ] **步骤 3：强化 `build_v2_chunk_prompt`**

补充这些明确规则：只输出 JSON；`chunk_status` 只能是 `completed/no_candidates/split_required`；候选最多 10 个；超过 10 个返回 `split_required` 且 `candidates=[]`；缺少 `email` 且缺少 `profile_url` 不可提交；Markdown `[导师名](URL)` 必须写入 `profile_url`；导师个人主页不能放入 `discovered_urls`；字段使用英文键；置信度 0 到 1；不翻译、不伪造、不引用历史。

- [ ] **步骤 4：运行测试验证通过**

运行：`cd backend; uv run python -m unittest test.test_crawler_v2_chunk_worker.CrawlerV2ChunkWorkerTests.test_chunk_prompt_includes_v1_quality_constraints`

预期：PASS。

- [ ] **步骤 5：Commit**

运行：`git add backend/test/test_crawler_v2_chunk_worker.py backend/app/services/crawler_v2_chunk_worker.py; git commit -m "test(爬虫): 锁定 V2 Chunk Worker 质量约束"`

---

### 任务 2：抽取 V1/V2 共用候选保存服务

**文件：**
- 修改：`backend/test/test_crawler_tools.py`
- 修改：`backend/app/services/crawler_tools.py`

- [ ] **步骤 1：编写保存规则回归测试**

在 `backend/test/test_crawler_tools.py` 复用现有建库/helper，新增测试：调用 `save_candidate_batch` 保存两个候选，一个只有姓名，一个有 `profile_url`；断言只有有 `profile_url` 的候选保存，拒收原因是 `缺少邮箱且缺少详情页链接`，保存行生成 `identity_key` 和 `field_sources`。

```python
result = await save_candidate_batch(ctx, [
    {"name": "张三", "confidence": 0.8},
    {"name": "李四", "profile_url": "https://example.edu/li.html", "confidence": 0.9},
])
self.assertEqual(result["rejected_count"], 1)
self.assertEqual(result["saved_count"], 1)
self.assertEqual(result["rejected_items"][0]["reason"], "缺少邮箱且缺少详情页链接")
```

- [ ] **步骤 2：运行 V1 保存测试**

运行：`cd backend; uv run python -m unittest test.test_crawler_tools`

预期：PASS；如 helper 名称不同，只调整测试，不改业务逻辑。

- [ ] **步骤 3：抽取共用入口**

在 `crawler_tools.py` 增加 `SharedCandidateSaveResult` 和 `save_candidate_payloads_shared(ctx, candidates)`。把 `save_candidate_batch` 中候选标准化/拒收循环抽成私有函数，例如 `_normalize_candidate_for_save(ctx, candidate, index)`；V1 的 `save_candidate_batch` 和 V2 的新共用入口都调用它；持久化继续复用 `_save_normalized_candidate_payloads`。

共用入口返回：`attempted_count`、`saved_count`、`merged_count`、`skipped_duplicate_count`、`rejected_count`、`rejected_items`、`saved`。

- [ ] **步骤 4：运行回归测试**

运行：`cd backend; uv run python -m unittest test.test_crawler_tools`

预期：PASS，V1 返回协议不变。

- [ ] **步骤 5：Commit**

运行：`git add backend/app/services/crawler_tools.py backend/test/test_crawler_tools.py; git commit -m "refactor(爬虫): 抽取候选保存共用服务"`

---

### 任务 3：V2 保存改走共用门禁

**文件：**
- 修改：`backend/test/test_crawler_v2_chunk_worker.py`
- 修改：`backend/app/services/crawler_v2_chunk_worker.py`

- [ ] **步骤 1：编写低质量候选拒收测试**

新增测试：`complete_current_chunk` 传入 `张三` 只有姓名、`李四` 有 `profile_url`；断言 `saved_count=1`、`rejected_count=1`、数据库只保存 `李四`。

```python
result = await complete_current_chunk(
    self.session_factory,
    chunk_id=chunk_id,
    worker_id="w1",
    candidates=[ProfessorCandidatePayload(name="张三"), ProfessorCandidatePayload(name="李四", profile_url="https://example.edu/li.html")],
    discovered_urls=[],
    chunk_status="completed",
)
self.assertEqual(result["saved_count"], 1)
self.assertEqual(result["rejected_count"], 1)
```

- [ ] **步骤 2：运行测试验证失败**

运行：`cd backend; uv run python -m unittest test.test_crawler_v2_chunk_worker.CrawlerV2ChunkWorkerTests.test_complete_chunk_rejects_candidate_without_email_and_profile_url`

预期：FAIL，当前 V2 直接写库。

- [ ] **步骤 3：替换直接写库逻辑**

在 `complete_current_chunk` 中构造 `CrawlToolContext`，调用 `save_candidate_payloads_shared(ctx, candidates)`；删除直接 `CrawlCandidate(...)` + `session.add(...)`；后续 enrichment task 只基于 `save_result["saved"]` 创建；返回值增加 `rejected_count/merged_count/skipped_duplicate_count`。

- [ ] **步骤 4：运行 V2 chunk 测试**

运行：`cd backend; uv run python -m unittest test.test_crawler_v2_chunk_worker`

预期：PASS。

- [ ] **步骤 5：Commit**

运行：`git add backend/app/services/crawler_v2_chunk_worker.py backend/test/test_crawler_v2_chunk_worker.py; git commit -m "fix(爬虫): V2 候选保存复用质量门禁"`

---

### 任务 4：补全 Markdown 详情页链接

**文件：**
- 修改：`backend/test/test_crawler_v2_chunk_worker.py`
- 修改：`backend/app/services/crawler_v2_chunk_worker.py`

- [ ] **步骤 1：编写补全测试**

新增测试：chunk 内容是 `[张三](https://example.edu/zhang.html) 教授`，候选只有 `name=张三`；断言保存成功且 `profile_url=https://example.edu/zhang.html`。

- [ ] **步骤 2：运行测试验证失败**

运行：`cd backend; uv run python -m unittest test.test_crawler_v2_chunk_worker.CrawlerV2ChunkWorkerTests.test_complete_chunk_fills_profile_url_from_markdown_link`

预期：FAIL。

- [ ] **步骤 3：实现确定性补全**

在 `crawler_v2_chunk_worker.py` 增加 `_extract_markdown_profile_links(chunk_content, base_url)`、`_normalize_person_name_for_link_match(value)`、`_fill_candidate_profile_urls_from_chunk(candidates, chunk_content, source_url)`；用正则 `r"\[([^\]]+)\]\(([^)\s]+)\)"` 提取链接，姓名去空白后匹配；调用保存服务前先补全候选。

- [ ] **步骤 4：运行测试验证通过**

运行：`cd backend; uv run python -m unittest test.test_crawler_v2_chunk_worker`

预期：PASS。

- [ ] **步骤 5：Commit**

运行：`git add backend/app/services/crawler_v2_chunk_worker.py backend/test/test_crawler_v2_chunk_worker.py; git commit -m "fix(爬虫): V2 从 chunk 链接补全导师主页"`

---

### 任务 5：过滤详情页 discovered_urls

**文件：**
- 修改：`backend/test/test_crawler_v2_chunk_worker.py`
- 修改：`backend/app/services/crawler_v2_chunk_worker.py`

- [ ] **步骤 1：编写 URL 过滤测试**

新增测试：候选 `profile_url=https://example.edu/zhang.html`，`discovered_urls` 也返回同 URL；断言 `saved_count=1`、`url_count=0`、没有创建 `CrawlPageTask`。

- [ ] **步骤 2：运行测试验证失败**

运行：`cd backend; uv run python -m unittest test.test_crawler_v2_chunk_worker.CrawlerV2ChunkWorkerTests.test_complete_chunk_does_not_enqueue_candidate_profile_url`

预期：FAIL。

- [ ] **步骤 3：实现过滤**

在 URL 入队前构造候选 `profile_url` 规范化集合；`normalized in candidate_profile_urls` 时 `continue`。保留同域、去重、已存在过滤。

- [ ] **步骤 4：运行测试验证通过**

运行：`cd backend; uv run python -m unittest test.test_crawler_v2_chunk_worker`

预期：PASS。

- [ ] **步骤 5：Commit**

运行：`git add backend/app/services/crawler_v2_chunk_worker.py backend/test/test_crawler_v2_chunk_worker.py; git commit -m "fix(爬虫): V2 不入队导师详情页链接"`

---

### 任务 6：超量候选触发拆分

**文件：**
- 修改：`backend/test/test_crawler_chunk_runtime.py`
- 修改：`backend/test/test_crawler_v2_chunk_worker.py`
- 修改：`backend/app/services/crawler_chunk_runtime.py`
- 修改：`backend/app/services/crawler_v2_chunk_worker.py`

- [ ] **步骤 1：编写拆分入口测试**

在 `test_crawler_chunk_runtime.py` 新增测试：可拆分 processing chunk 调用 `split_page_chunk_for_retry(session_factory, job_id, chunk_pk, reason)` 后，父 chunk 状态为 `split_required`，生成 pending 子 chunk，子 chunk `parent_chunk_id` 指向父 `chunk_id`。

- [ ] **步骤 2：编写 V2 超量不保存测试**

在 `test_crawler_v2_chunk_worker.py` 新增测试：传入 11 个有效候选；断言 `status=split_required`、`saved_count=0`、数据库无候选、生成子 chunk。

- [ ] **步骤 3：运行测试验证失败**

运行：`cd backend; uv run python -m unittest test.test_crawler_chunk_runtime test.test_crawler_v2_chunk_worker.CrawlerV2ChunkWorkerTests.test_complete_chunk_splits_when_candidate_count_exceeds_limit`

预期：FAIL。

- [ ] **步骤 4：暴露拆分入口**

在 `crawler_chunk_runtime.py` 新增 `split_page_chunk_for_retry(...)`，复用现有 `_split_chunk_in_session`；能拆分时父 chunk 置 `split_required` 并释放 worker；不能拆分时置 `failed_retryable` 并写 `last_error`；返回 `status/child_count/split_reason`。

- [ ] **步骤 5：V2 调用拆分入口**

在 `crawler_v2_chunk_worker.py` 增加 `MAX_CANDIDATES_PER_CHUNK_RESULT = 10`。`complete_current_chunk` 在保存和入队前判断：`chunk_status == split_required` 或 `len(candidates) > 10` 时调用 `split_page_chunk_for_retry`，返回 `saved_count=0/url_count=0/enrichment_count=0`，不得保存前 10 个，也不得入队 URL。

- [ ] **步骤 6：运行拆分相关测试**

运行：`cd backend; uv run python -m unittest test.test_crawler_chunk_runtime test.test_crawler_v2_chunk_worker`

预期：PASS。

- [ ] **步骤 7：Commit**

运行：`git add backend/app/services/crawler_chunk_runtime.py backend/app/services/crawler_v2_chunk_worker.py backend/test/test_crawler_chunk_runtime.py backend/test/test_crawler_v2_chunk_worker.py; git commit -m "fix(爬虫): V2 候选超量触发 chunk 拆分"`

---

### 任务 7：无效输出不标记完成

**文件：**
- 修改：`backend/test/test_crawler_v2_chunk_worker.py`
- 修改：`backend/app/services/crawler_v2_chunk_worker.py`

- [ ] **步骤 1：编写解析失败测试**

新增测试：mock `invoke_v2_chunk_agent` 抛 `ValueError("invalid json")`；断言 `run_crawler_v2_chunk_worker_once` 返回 1，chunk 状态 `failed_retryable`，`last_error` 包含错误，数据库无候选。

- [ ] **步骤 2：编写结构缺失测试**

新增测试：mock 返回 `({"candidates": []}, None)`；断言 chunk `failed_retryable`，不保存候选。

- [ ] **步骤 3：运行测试验证失败**

运行：`cd backend; uv run python -m unittest test.test_crawler_v2_chunk_worker.CrawlerV2ChunkWorkerTests.test_chunk_worker_marks_retryable_when_llm_output_is_invalid_json test.test_crawler_v2_chunk_worker.CrawlerV2ChunkWorkerTests.test_chunk_worker_marks_retryable_when_payload_shape_is_invalid`

预期：FAIL。

- [ ] **步骤 4：实现失败标记和 payload 校验**

新增 `_mark_chunk_failed_retryable(session_factory, chunk_id, worker_id, error_message)`，设置 `failed_retryable`、`last_error`、释放 worker/lease。新增 `_validate_chunk_agent_payload(payload)`，要求 dict 且包含 `candidates/discovered_urls/chunk_status`。`invoke_v2_chunk_agent` 异常和校验异常都走 retryable，不调用保存。

- [ ] **步骤 5：运行测试验证通过**

运行：`cd backend; uv run python -m unittest test.test_crawler_v2_chunk_worker`

预期：PASS。

- [ ] **步骤 6：Commit**

运行：`git add backend/app/services/crawler_v2_chunk_worker.py backend/test/test_crawler_v2_chunk_worker.py; git commit -m "fix(爬虫): V2 Chunk Worker 无效输出可重试"`

---

### 任务 8：手动补全跳过无详情页候选

**文件：**
- 修改：`backend/test/test_crawl_jobs_api.py`
- 修改：`backend/app/services/crawl_job_runtime.py`
- 修改：`backend/app/api/crawl_jobs.py`
- 修改：`backend/app/schemas/crawl_job.py`

- [ ] **步骤 1：编写 API 跳过统计测试**

在 `backend/test/test_crawl_jobs_api.py` 新增测试：创建 `needs_review` 抓取任务，插入两个候选，一个有 `profile_url`、一个没有；mock `app.api.crawl_jobs.enrich_selected_crawl_candidates` 返回 `SelectedCandidateEnrichmentSummary(selected_count=1, enriched_count=1, unchanged_count=0, failed_count=0, skipped_count=1)`；调用 `POST /api/crawl-jobs/{job_id}/enrich`；断言响应 `selected_count=1`、`skipped_count=1`、`failed_count=0`，消息包含 `跳过 1 位`。

```python
from app.services.crawl_job_runtime import SelectedCandidateEnrichmentSummary

async def fake_enrich_selected_crawl_candidates(*args: object, **kwargs: object) -> SelectedCandidateEnrichmentSummary:
    return SelectedCandidateEnrichmentSummary(
        selected_count=1,
        enriched_count=1,
        unchanged_count=0,
        failed_count=0,
        skipped_count=1,
    )
```

- [ ] **步骤 2：编写 runtime 跳过测试**

在 `backend/test/test_crawl_job_runtime.py` 新增测试：两个候选中一个没有 `profile_url`；mock `_enrich_candidate_collection_concurrent` 记录收到的候选；调用 `enrich_selected_crawl_candidates`；断言只传入有 `profile_url` 的候选，返回 `selected_count=1`、`skipped_count=1`、`failed_count=0`。

- [ ] **步骤 3：运行测试验证失败**

运行：`cd backend; uv run python -m unittest test.test_crawl_jobs_api test.test_crawl_job_runtime`

预期：FAIL，`SelectedCandidateEnrichmentSummary` 和响应 schema 还没有 `skipped_count`，runtime 还会把无 `profile_url` 候选送去补全。

- [ ] **步骤 4：扩展 summary 与响应 schema**

修改 `backend/app/services/crawl_job_runtime.py`：`SelectedCandidateEnrichmentSummary` 增加 `skipped_count: int = 0`，确保现有只传 4 个参数的测试仍兼容。

修改 `backend/app/schemas/crawl_job.py`：`CrawlJobEnrichResult` 增加 `skipped_count: int = 0`，不删除 `selected_count/enriched_count/unchanged_count/failed_count/message`。

- [ ] **步骤 5：实现 runtime 跳过逻辑**

在 `_enrich_selected_candidates_concurrent` 读取 candidates 后拆分：

```python
enrichable_candidates = [candidate for candidate in candidates if candidate.profile_url]
skipped_count = len(candidates) - len(enrichable_candidates)
if not enrichable_candidates:
    return SelectedCandidateEnrichmentSummary(0, 0, 0, 0, skipped_count=skipped_count)
```

后续只把 `enrichable_candidates` 传给 `_enrich_candidate_collection_concurrent`，返回 `SelectedCandidateEnrichmentSummary(selected_count=len(enrichable_candidates), enriched_count=enriched, unchanged_count=unchanged, failed_count=failed, skipped_count=skipped_count)`。

- [ ] **步骤 6：更新 API 返回消息**

在 `backend/app/api/crawl_jobs.py` 的 `enrich_crawl_candidates` 返回体加入 `skipped_count=summary.skipped_count`。消息保留原有成功/未变化/失败统计，并在 `summary.skipped_count > 0` 时追加 `跳过 {summary.skipped_count} 位缺少详情页 URL 的候选。`。

- [ ] **步骤 7：运行补全相关测试**

运行：`cd backend; uv run python -m unittest test.test_crawl_jobs_api test.test_crawl_job_runtime test.test_crawler_v2_enrichment_worker`

预期：PASS。

- [ ] **步骤 8：Commit**

运行：`git add backend/app/services/crawl_job_runtime.py backend/app/api/crawl_jobs.py backend/app/schemas/crawl_job.py backend/test/test_crawl_jobs_api.py backend/test/test_crawl_job_runtime.py; git commit -m "fix(爬虫): 手动补全跳过无详情页候选"`

---

### 任务 9：完整回归验收

**文件：**
- 修改：无，除非测试暴露遗漏

- [ ] **步骤 1：运行 V2 聚焦测试**

运行：`cd backend; uv run python -m unittest test.test_crawler_v2_chunk_worker test.test_crawler_chunk_runtime test.test_crawler_tools test.test_crawler_v2_scheduler test.test_crawler_v2_page_worker test.test_crawler_v2_enrichment_worker test.test_crawler_v2_runtime_routing`

预期：PASS。

- [ ] **步骤 2：运行 V1 关键回归**

运行：`cd backend; uv run python -m unittest test.test_faculty_crawler_agent test.test_crawl_job_runtime test.test_crawler_tools test.test_crawler_chunk_runtime`

预期：PASS，确认 V1 没被共用服务破坏。

- [ ] **步骤 3：确认 Page Worker 未恢复自动发现 URL**

运行：`cd backend; rg -n "snapshot\.links|discovered_urls|CrawlPageTask\(" app/services/crawler_v2_page_worker.py app/services/crawler_v2_chunk_worker.py`

预期：`crawler_v2_page_worker.py` 不基于 `snapshot.links` 创建 `CrawlPageTask`；URL 入队只来自 `crawler_v2_chunk_worker.py` 的 `discovered_urls`。

- [ ] **步骤 4：规格覆盖检查**

对照 `docs/superpowers/specs/2026-06-01-crawler-v2-v1-mechanism-migration.md`：10 人上限、低质量拒收、Markdown `profile_url`、详情页不入队、超量拆分、无效输出 retry、手动补全跳过、Page Worker 不发现 URL 都有测试覆盖。

- [ ] **步骤 5：最终提交**

若步骤 9 产生修复：`git add backend/app backend/test; git commit -m "fix(爬虫): 完成 V2 候选发现机制回归"`。若没有文件变化，不提交。

---

## 验收命令

```powershell
cd backend
uv run python -m unittest test.test_crawler_v2_chunk_worker test.test_crawler_chunk_runtime test.test_crawler_tools test.test_crawler_v2_scheduler test.test_crawler_v2_page_worker test.test_crawler_v2_enrichment_worker test.test_crawler_v2_runtime_routing
uv run python -m unittest test.test_faculty_crawler_agent test.test_crawl_job_runtime test.test_crawler_tools test.test_crawler_chunk_runtime
```

## 风险控制

- 不为省 token 保存低质量候选；无邮箱且无 `profile_url` 必须拒收。
- 超过 10 个候选不得保存前 10 个并丢弃剩余；必须拆分或失败。
- Page Worker 不得恢复自动发现 URL。
- Chunk Worker 不得变成长对话、多轮工具调用或浏览器抓取。
- 共用保存服务必须保持 V1 `save_candidate_batch` 行为和返回协议不变。