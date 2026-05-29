# 爬虫后台稳定性修复实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 消除候选补全 worker 的卡死风险，并给页面快照缓存加上固定上限，避免后台长时间运行时内存无界增长。

**架构：** 候选补全链路保持现有并发模型，但把 worker 的单项执行结果强制兜底成 `CandidateEnrichmentResult`，这样消费者永远能收到与请求数匹配的结果，不会在 `result_queue.get()` 上悬挂。页面快照缓存改成有界 LRU，仍然只缓存成功快照，但在访问和写入时维护最近使用顺序，超过上限就淘汰最老条目。

**技术栈：** Python 3.12、`asyncio`、`unittest`、`sqlalchemy.ext.asyncio`、`collections.OrderedDict`

---

### 任务 1：候选补全 worker 的结果兜底与取消收口

**文件：**
- 修改：`backend/app/services/crawl_job_runtime.py:593-657, 676-703`
- 修改：`backend/test/test_crawl_job_runtime.py:267-324`

- [ ] **步骤 1：编写失败的测试**

```python
async def test_enrichment_worker_pause_exception_does_not_hang(self) -> None:
    job_id = await self._create_default_profile_and_job(
        start_url="https://example.edu/faculty",
    )
    async with self.session_factory() as session:
        candidate = await session.scalar(
            select(CrawlCandidate).where(CrawlCandidate.job_id == job_id)
        )
        self.assertIsNotNone(candidate)
        assert candidate is not None
        candidate.profile_url = "https://example.edu/faculty/zhang"
        await session.commit()

    async def fake_fetch(*args: object, **kwargs: object) -> PageSnapshot:
        raise CrawlJobPaused()

    with patch(
        "app.services.crawl_job_runtime.crawl_page_with_crawl4ai",
        new=fake_fetch,
    ):
        summary = await asyncio.wait_for(
            enrich_selected_crawl_candidates(
                self.session_factory,
                job_id=job_id,
                candidate_ids=[candidate.id],
                llm_profile=await self._get_default_llm_profile(),
            ),
            timeout=1.0,
        )

    self.assertEqual(summary.selected_count, 1)
    self.assertEqual(summary.enriched_count, 0)
    self.assertEqual(summary.unchanged_count, 0)
    self.assertEqual(summary.failed_count, 0)


async def test_enrichment_worker_unexpected_exception_is_counted_as_failure(self) -> None:
    job_id = await self._create_default_profile_and_job(
        start_url="https://example.edu/faculty",
    )
    async with self.session_factory() as session:
        candidate = await session.scalar(
            select(CrawlCandidate).where(CrawlCandidate.job_id == job_id)
        )
        self.assertIsNotNone(candidate)
        assert candidate is not None
        candidate.profile_url = "https://example.edu/faculty/zhang"
        await session.commit()

    async def fake_fetch(*args: object, **kwargs: object) -> PageSnapshot:
        raise RuntimeError("boom")

    with patch(
        "app.services.crawl_job_runtime.crawl_page_with_crawl4ai",
        new=fake_fetch,
    ):
        summary = await asyncio.wait_for(
            enrich_selected_crawl_candidates(
                self.session_factory,
                job_id=job_id,
                candidate_ids=[candidate.id],
                llm_profile=await self._get_default_llm_profile(),
            ),
            timeout=1.0,
        )

    self.assertEqual(summary.selected_count, 1)
    self.assertEqual(summary.enriched_count, 0)
    self.assertEqual(summary.unchanged_count, 0)
    self.assertEqual(summary.failed_count, 1)
```

- [ ] **步骤 2：运行测试验证失败**

运行：`rtk uv run python -m unittest test.test_crawl_job_runtime.CrawlJobRuntimeTests.test_enrichment_worker_pause_exception_does_not_hang test.test_crawl_job_runtime.CrawlJobRuntimeTests.test_enrichment_worker_unexpected_exception_is_counted_as_failure`
预期：失败，`test_enrichment_worker_pause_exception_does_not_hang` 在 `asyncio.wait_for(...)` 处超时，`test_enrichment_worker_unexpected_exception_is_counted_as_failure` 也会因为 worker 没有投递结果而超时。

- [ ] **步骤 3：编写最少实现代码**

```python
async def _run_candidate_enrichment_worker(...):
    while True:
        item = await work_queue.get()
        if item is None:
            return
        try:
            result = await _enrich_candidate_work_item(...)
        except asyncio.CancelledError:
            raise
        except (CrawlJobPaused, CrawlJobCanceled):
            result = CandidateEnrichmentResult(
                candidate_id=item.candidate_id,
                candidate_name=item.candidate_name,
                profile_url=item.profile_url,
                status="stopped",
            )
        except Exception as exc:
            result = CandidateEnrichmentResult(
                candidate_id=item.candidate_id,
                candidate_name=item.candidate_name,
                profile_url=item.profile_url,
                status="failed",
                error_message=str(exc),
            )
        await result_queue.put(result)


async def _enrich_candidate_collection_concurrent(...):
    workers = [...]
    try:
        enriched, unchanged, failed = await _consume_candidate_enrichment_results(...)
    finally:
        for worker in workers:
            worker.cancel()
        await asyncio.gather(*workers, return_exceptions=True)
    return enriched, unchanged, failed
```

- [ ] **步骤 4：运行测试验证通过**

运行：`rtk uv run python -m unittest test.test_crawl_job_runtime.CrawlJobRuntimeTests.test_enrichment_worker_pause_exception_does_not_hang test.test_crawl_job_runtime.CrawlJobRuntimeTests.test_enrichment_worker_unexpected_exception_is_counted_as_failure`
预期：PASS，且无 `TimeoutError`。

- [ ] **步骤 5：Commit**

```bash
rtk git add backend/app/services/crawl_job_runtime.py backend/test/test_crawl_job_runtime.py
rtk git commit -m "fix(backend): 防止候选补全 worker 卡死"
```

### 任务 2：页面快照缓存有界化

**文件：**
- 修改：`backend/app/services/crawler_tools.py:1-320`
- 修改：`backend/test/test_crawler_tools.py:45-73`

- [ ] **步骤 1：编写失败的测试**

```python
def test_page_snapshot_cache_evicts_lru_entries(self) -> None:
    ctx = CrawlToolContext(
        job_id=1,
        start_url="https://cs.example.edu/faculty",
        university="示例大学",
        school="计算机学院",
        session_factory=_FakeSessionFactory(),  # type: ignore[arg-type]
    )

    first = PageSnapshot(
        url="https://example.edu/a",
        title="A",
        text="alpha",
        html="<html>a</html>",
        links=[],
        fetch_method="http",
        status="succeeded",
    )
    second = PageSnapshot(
        url="https://example.edu/b",
        title="B",
        text="beta",
        html="<html>b</html>",
        links=[],
        fetch_method="http",
        status="succeeded",
    )
    third = PageSnapshot(
        url="https://example.edu/c",
        title="C",
        text="gamma",
        html="<html>c</html>",
        links=[],
        fetch_method="http",
        status="succeeded",
    )

    with patch("app.services.crawler_tools.MAX_PAGE_SNAPSHOT_CACHE_ENTRIES", 2):
        ctx.remember_page_snapshot(first)
        ctx.remember_page_snapshot(second)
        self.assertIs(ctx.get_cached_page_snapshot(first.url), first)
        ctx.remember_page_snapshot(third)

    self.assertIs(ctx.get_cached_page_snapshot(first.url), first)
    self.assertIsNone(ctx.get_cached_page_snapshot(second.url))
    self.assertIs(ctx.get_cached_page_snapshot(third.url), third)
```

- [ ] **步骤 2：运行测试验证失败**

运行：`rtk uv run python -m unittest test.test_crawler_tools.CrawlerToolTests.test_page_snapshot_cache_evicts_lru_entries`
预期：失败，当前实现没有上限，`second` 不会被淘汰。

- [ ] **步骤 3：编写最少实现代码**

```python
from collections import OrderedDict

MAX_PAGE_SNAPSHOT_CACHE_ENTRIES = 64


@dataclass(frozen=True)
class CrawlToolContext:
    ...
    page_snapshot_cache: OrderedDict[str, PageSnapshot] = field(default_factory=OrderedDict)

    def get_cached_page_snapshot(self, url: str) -> PageSnapshot | None:
        normalized = _normalize_page_cache_url(url)
        snapshot = self.page_snapshot_cache.get(normalized)
        if snapshot is None:
            return None
        self.page_snapshot_cache.move_to_end(normalized)
        return snapshot

    def remember_page_snapshot(self, snapshot: PageSnapshot) -> None:
        if not snapshot.url:
            return
        normalized = _normalize_page_cache_url(snapshot.url)
        self.page_snapshot_cache[normalized] = snapshot
        self.page_snapshot_cache.move_to_end(normalized)
        while len(self.page_snapshot_cache) > MAX_PAGE_SNAPSHOT_CACHE_ENTRIES:
            self.page_snapshot_cache.popitem(last=False)
```

- [ ] **步骤 4：运行测试验证通过**

运行：`rtk uv run python -m unittest test.test_crawler_tools.CrawlerToolTests.test_page_snapshot_cache_evicts_lru_entries`
预期：PASS。

- [ ] **步骤 5：Commit**

```bash
rtk git add backend/app/services/crawler_tools.py backend/test/test_crawler_tools.py
rtk git commit -m "fix(backend): 限制页面快照缓存大小"
```

**自检：**
- worker 卡死问题有失败测试、实现步骤和验证命令。
- 页面快照缓存上限有失败测试、实现步骤和验证命令。
- 两个任务互不依赖，可以单独执行，也可以按顺序一起做。
