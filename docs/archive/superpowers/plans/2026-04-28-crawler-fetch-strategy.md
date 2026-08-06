# 智能抓取加载策略实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 修复智能抓取中 HTTP 阻断重复尝试、详情页错误等待 `table`、列表页结构假设过窄的问题。

**架构：** 在抓取工具层引入任务内 host 阻断记忆和页面意图参数。HTTP 首次命中反爬状态后只在当前 `CrawlToolContext` 内标记 host，后续同 host 直接浏览器抓取；浏览器等待策略按 `generic`、`directory`、`profile` 意图选择，并在等待失败时降级重试。

**技术栈：** Python 3.12、FastAPI 后端、SQLAlchemy、unittest、Crawl4AI。

---

## 文件结构

- 修改：`backend/app/services/crawler_tools.py`
  - 增加 `CrawlPageIntent` 类型。
  - 在 `CrawlToolContext` 中增加任务内 `http_blocked_hosts` 状态。
  - 让 `crawl_page_with_crawl4ai`、`browser_investigate`、`_crawl_page_with_crawl4ai_browser` 接收页面意图。
  - 改造浏览器运行配置和等待失败降级逻辑。
- 修改：`backend/app/services/crawl_job_runtime.py`
  - 详情补全调用抓取时显式传 `intent="profile"`。
- 修改：`backend/test/test_crawler_tools.py`
  - 覆盖任务内 HTTP 阻断记忆。
  - 覆盖页面意图到等待策略的映射。
  - 覆盖等待失败降级重试。
- 修改：`backend/test/test_crawl_job_runtime.py`
  - 覆盖详情补全向抓取层传递 `profile` 意图。

## 任务 1：任务内 HTTP 阻断记忆

**文件：**
- 修改：`backend/test/test_crawler_tools.py`
- 修改：`backend/app/services/crawler_tools.py`

- [ ] **步骤 1：编写失败测试：同 host 首次阻断后跳过 HTTP**

在 `CrawlerHttpToolTests` 中新增测试：

```python
    async def test_crawl_page_with_crawl4ai_skips_http_for_host_after_blocked_status(self) -> None:
        session_factory = _FakeSessionFactory()
        ctx = CrawlToolContext(
            job_id=1,
            start_url="https://teacher.example.edu/list",
            university="University",
            school="School",
            session_factory=session_factory,  # type: ignore[arg-type]
        )
        blocked_http_snapshot = PageSnapshot(
            url="https://teacher.example.edu/a",
            text="blocked",
            html="<html><body>blocked</body></html>",
            fetch_method="http",
            status="failed",
            suspicious_empty=True,
            error_message="HTTP 412 blocked, browser fallback advised",
        )
        first_browser_snapshot = PageSnapshot(
            url="https://teacher.example.edu/a",
            text="Profile A",
            html="<html><body>Profile A</body></html>",
            fetch_method="browser",
            status="succeeded",
        )
        second_browser_snapshot = PageSnapshot(
            url="https://teacher.example.edu/b",
            text="Profile B",
            html="<html><body>Profile B</body></html>",
            fetch_method="browser",
            status="succeeded",
        )

        with patch(
            "app.services.crawler_tools.crawl_page_with_http",
            return_value=blocked_http_snapshot,
        ) as http_path, patch(
            "app.services.crawler_tools._crawl_page_with_crawl4ai_browser",
            side_effect=[first_browser_snapshot, second_browser_snapshot],
        ) as browser_path:
            first = await crawl_page_with_crawl4ai(ctx, "https://teacher.example.edu/a")
            second = await crawl_page_with_crawl4ai(ctx, "https://teacher.example.edu/b")

        self.assertIs(first, first_browser_snapshot)
        self.assertIs(second, second_browser_snapshot)
        self.assertEqual(http_path.call_count, 1)
        self.assertEqual(browser_path.call_count, 2)
```

- [ ] **步骤 2：编写失败测试：不同 host 不共享阻断状态**

继续在 `CrawlerHttpToolTests` 中新增测试：

```python
    async def test_crawl_page_with_crawl4ai_keeps_blocked_hosts_scoped_by_host(self) -> None:
        session_factory = _FakeSessionFactory()
        ctx = CrawlToolContext(
            job_id=1,
            start_url="https://teacher.example.edu/list",
            university="University",
            school="School",
            session_factory=session_factory,  # type: ignore[arg-type]
        )
        blocked_http_snapshot = PageSnapshot(
            url="https://teacher.example.edu/a",
            text="blocked",
            html="<html><body>blocked</body></html>",
            fetch_method="http",
            status="failed",
            suspicious_empty=True,
            error_message="HTTP 412 blocked, browser fallback advised",
        )
        other_http_snapshot = PageSnapshot(
            url="https://profile.example.edu/b",
            text="Profile B",
            html="<html><body>Profile B</body></html>",
            fetch_method="http",
            status="succeeded",
        )
        browser_snapshot = PageSnapshot(
            url="https://teacher.example.edu/a",
            text="Profile A",
            html="<html><body>Profile A</body></html>",
            fetch_method="browser",
            status="succeeded",
        )

        with patch(
            "app.services.crawler_tools.crawl_page_with_http",
            side_effect=[blocked_http_snapshot, other_http_snapshot],
        ) as http_path, patch(
            "app.services.crawler_tools._crawl_page_with_crawl4ai_browser",
            return_value=browser_snapshot,
        ) as browser_path:
            first = await crawl_page_with_crawl4ai(ctx, "https://teacher.example.edu/a")
            second = await crawl_page_with_crawl4ai(ctx, "https://profile.example.edu/b")

        self.assertIs(first, browser_snapshot)
        self.assertIs(second, other_http_snapshot)
        self.assertEqual(http_path.call_count, 2)
        self.assertEqual(browser_path.call_count, 1)
```

- [ ] **步骤 3：运行测试验证失败**

运行：

```bash
cd backend
uv run python -m unittest test.test_crawler_tools.CrawlerHttpToolTests.test_crawl_page_with_crawl4ai_skips_http_for_host_after_blocked_status test.test_crawler_tools.CrawlerHttpToolTests.test_crawl_page_with_crawl4ai_keeps_blocked_hosts_scoped_by_host
```

预期：FAIL。失败原因应是后续同 host 仍调用了 `crawl_page_with_http`，或 `CrawlToolContext` 尚未记录阻断 host。

- [ ] **步骤 4：实现最少生产代码**

在 `backend/app/services/crawler_tools.py` 中修改：

```python
@dataclass
class CrawlToolContext:
    job_id: int
    start_url: str
    university: str
    school: str
    session_factory: async_sessionmaker[AsyncSession]
    http_blocked_hosts: set[str] | None = None

    def mark_http_blocked(self, url: str) -> None:
        host = (urlparse(url).hostname or "").lower()
        if not host:
            return
        if self.http_blocked_hosts is None:
            self.http_blocked_hosts = set()
        self.http_blocked_hosts.add(host)

    def is_http_blocked(self, url: str) -> bool:
        host = (urlparse(url).hostname or "").lower()
        return bool(host and self.http_blocked_hosts and host in self.http_blocked_hosts)
```

在 `crawl_page_with_crawl4ai` 中先解析绝对 URL，并使用任务内状态：

```python
async def crawl_page_with_crawl4ai(
    ctx: CrawlToolContext,
    url: str,
    *,
    intent: CrawlPageIntent = "generic",
) -> PageSnapshot:
    absolute_url = urljoin(ctx.start_url, url)
    if ctx.is_http_blocked(absolute_url):
        return await browser_investigate(ctx, absolute_url, goal="", intent=intent)

    http_snapshot = await crawl_page_with_http(ctx, url)
    if _should_use_crawl4ai_fallback(http_snapshot):
        if _is_http_blocked_snapshot(http_snapshot):
            ctx.mark_http_blocked(http_snapshot.url or absolute_url)
        return await browser_investigate(ctx, url, goal="", intent=intent)
    return http_snapshot
```

新增辅助函数：

```python
def _is_http_blocked_snapshot(snapshot: PageSnapshot) -> bool:
    if snapshot.fetch_method != "http":
        return False
    error_message = (snapshot.error_message or "").lower()
    return any(str(status) in error_message for status in CRAWL4AI_BROWSER_FALLBACK_STATUS)
```

- [ ] **步骤 5：运行测试验证通过**

运行同一步骤 3 命令。预期：PASS。

- [ ] **步骤 6：Commit**

```bash
git add backend/app/services/crawler_tools.py backend/test/test_crawler_tools.py
git commit -m "fix(crawler): 记住任务内 HTTP 阻断 host"
```

## 任务 2：页面意图和浏览器等待配置

**文件：**
- 修改：`backend/test/test_crawler_tools.py`
- 修改：`backend/app/services/crawler_tools.py`

- [ ] **步骤 1：编写失败测试：profile 意图不等待 table**

在 `CrawlerToolTests` 或 `CrawlerHttpToolTests` 中新增同步测试：

```python
    def test_browser_run_config_for_profile_waits_for_body(self) -> None:
        config = crawler_tools._browser_run_config_for_intent("profile")

        self.assertEqual(config.wait_for, "css:body")
```

- [ ] **步骤 2：编写失败测试：generic 和 directory 不使用 table 作为唯一门槛**

新增测试：

```python
    def test_browser_run_config_for_generic_and_directory_waits_for_body(self) -> None:
        generic_config = crawler_tools._browser_run_config_for_intent("generic")
        directory_config = crawler_tools._browser_run_config_for_intent("directory")

        self.assertEqual(generic_config.wait_for, "css:body")
        self.assertEqual(directory_config.wait_for, "css:body")
```

- [ ] **步骤 3：运行测试验证失败**

运行：

```bash
cd backend
uv run python -m unittest test.test_crawler_tools.CrawlerToolTests.test_browser_run_config_for_profile_waits_for_body test.test_crawler_tools.CrawlerToolTests.test_browser_run_config_for_generic_and_directory_waits_for_body
```

预期：FAIL。失败原因应是 `_browser_run_config_for_intent` 尚不存在。

- [ ] **步骤 4：实现页面意图类型和配置函数**

在 `backend/app/services/crawler_tools.py` 中增加类型：

```python
CrawlPageIntent = Literal["generic", "directory", "profile"]
CRAWL4AI_BROWSER_WAIT_SELECTOR = "css:body"
```

将 `_browser_run_config_for_goal` 替换或包裹为：

```python
def _browser_wait_selector_for_intent(intent: CrawlPageIntent) -> str:
    _ = intent
    return CRAWL4AI_BROWSER_WAIT_SELECTOR


def _browser_run_config_for_intent(
    intent: CrawlPageIntent,
    *,
    wait_for: str | None = None,
) -> "CrawlerRunConfig":
    from crawl4ai import CrawlerRunConfig

    return CrawlerRunConfig(
        process_in_browser=True,
        wait_until="networkidle",
        wait_for=wait_for if wait_for is not None else _browser_wait_selector_for_intent(intent),
        wait_for_timeout=CRAWL4AI_BROWSER_WAIT_TIMEOUT_MS,
        delay_before_return_html=CRAWL4AI_BROWSER_DELAY_SECONDS,
        page_timeout=JS_RENDER_TIMEOUT_MS,
        max_retries=MAX_RETRIES_FOR_BROWSER_RENDER,
        user_agent=(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        verbose=False,
    )
```

保留旧函数兼容现有调用时，令它调用新函数：

```python
def _browser_run_config_for_goal(goal: str) -> "CrawlerRunConfig":
    _ = goal
    return _browser_run_config_for_intent("generic")
```

- [ ] **步骤 5：运行测试验证通过**

运行同一步骤 3 命令。预期：PASS。

- [ ] **步骤 6：Commit**

```bash
git add backend/app/services/crawler_tools.py backend/test/test_crawler_tools.py
git commit -m "fix(crawler): 按页面意图选择浏览器等待条件"
```

## 任务 3：等待失败降级重试

**文件：**
- 修改：`backend/test/test_crawler_tools.py`
- 修改：`backend/app/services/crawler_tools.py`

- [ ] **步骤 1：编写失败测试：等待失败后使用宽松配置重试**

在 `CrawlerHttpToolTests` 中新增测试。测试通过 fake Crawl4AI 模块断言第一次使用 `css:body`，第二次使用 `None`：

```python
    async def test_crawl4ai_browser_fetch_retries_without_wait_selector_after_wait_failure(self) -> None:
        calls: list[object] = []

        class _WaitFailureCrawler:
            def __init__(self, *args: object, **kwargs: object) -> None:
                pass

            async def __aenter__(self) -> "_WaitFailureCrawler":
                return self

            async def __aexit__(self, *args: object) -> None:
                return None

            async def arun(self, url: str, *, config: object) -> list[object]:
                calls.append(getattr(config, "wait_for", None))
                if len(calls) == 1:
                    return [
                        types.SimpleNamespace(
                            success=False,
                            url=url,
                            error_message="Wait condition failed: Timeout after 15000ms waiting for selector 'body'",
                            html="",
                            redirected_url="",
                        )
                    ]
                return [
                    types.SimpleNamespace(
                        success=True,
                        url=url,
                        error_message="",
                        html="<html><body>周锋 电子邮箱：zfeng@bupt.edu.cn</body></html>",
                        redirected_url=url,
                    )
                ]

        crawl4ai_module = types.SimpleNamespace(
            AsyncWebCrawler=_WaitFailureCrawler,
            CrawlerRunConfig=crawler_tools._browser_run_config_for_intent("profile").__class__,
        )

        with patch.dict("sys.modules", {"crawl4ai": crawl4ai_module}):
            snapshot = await crawler_tools._crawl_page_with_crawl4ai_browser_direct(
                "https://teacher.example.edu/zhoufeng",
                "",
                "profile",
            )

        self.assertEqual(snapshot.status, "succeeded")
        self.assertEqual(calls, ["css:body", None])
        self.assertIn("zfeng@bupt.edu.cn", snapshot.text)
```

- [ ] **步骤 2：运行测试验证失败**

运行：

```bash
cd backend
uv run python -m unittest test.test_crawler_tools.CrawlerHttpToolTests.test_crawl4ai_browser_fetch_retries_without_wait_selector_after_wait_failure
```

预期：FAIL。失败原因应是 `_crawl_page_with_crawl4ai_browser_direct` 还不接受 `intent`，或首次失败后没有重试。

- [ ] **步骤 3：实现最少降级重试**

修改调用签名：

```python
async def _crawl_page_with_crawl4ai_browser(
    ctx: CrawlToolContext,
    absolute_url: str,
    goal: str,
    intent: CrawlPageIntent = "generic",
) -> PageSnapshot:
```

线程转发同步增加 `intent`：

```python
return await asyncio.to_thread(
    _run_browser_fetch_with_proactor_loop,
    absolute_url,
    goal,
    intent,
)
```

修改 direct 函数：

```python
async def _crawl_page_with_crawl4ai_browser_direct(
    absolute_url: str,
    goal: str,
    intent: CrawlPageIntent = "generic",
) -> PageSnapshot:
```

新增辅助函数：

```python
def _is_wait_condition_failure(message: str | None) -> bool:
    return "wait condition failed" in (message or "").lower()
```

在 direct 函数中用两次尝试替代单次 `crawler.arun`：

```python
configs = [
    _browser_run_config_for_intent(intent),
    _browser_run_config_for_intent(intent, wait_for=None),
]
last_failure: PageSnapshot | None = None
for index, config in enumerate(configs):
    try:
        async with AsyncWebCrawler(verbose=False) as crawler:
            crawl_result = await crawler.arun(absolute_url, config=config)
    except Exception as exc:
        failure = _failed_snapshot(
            url=absolute_url,
            fetch_method="browser",
            error_message=_format_exception_for_snapshot(exc, "Crawl4AI browser fetch failed"),
        )
    else:
        failure = _snapshot_from_crawl4ai_result(crawl_result, absolute_url)
        if failure.status == "succeeded":
            return failure

    last_failure = failure
    if index == 0 and _is_wait_condition_failure(failure.error_message):
        continue
    return failure

return last_failure or _failed_snapshot(
    url=absolute_url,
    fetch_method="browser",
    error_message="Crawl4AI browser returned no result",
)
```

为避免 direct 函数过长，提取：

```python
def _snapshot_from_crawl4ai_result(crawl_result: object, absolute_url: str) -> PageSnapshot:
    if not crawl_result:
        return _failed_snapshot(
            url=absolute_url,
            fetch_method="browser",
            error_message="Crawl4AI browser returned no result",
        )
    crawl_item = crawl_result[0]
    if not getattr(crawl_item, "success", False):
        return _failed_snapshot(
            url=str(getattr(crawl_item, "url", absolute_url) or absolute_url),
            fetch_method="browser",
            error_message=_format_message_with_fallback(
                str(getattr(crawl_item, "error_message", "") or ""),
                "browser tool reported unsuccessful result",
            ),
        )
    content = str(getattr(crawl_item, "html", "") or "")
    final_url = str(getattr(crawl_item, "redirected_url", "") or absolute_url)
    snapshot = html_to_snapshot(final_url, content, "browser")
    if not snapshot.text.strip():
        snapshot.suspicious_empty = True
    return snapshot
```

- [ ] **步骤 4：运行测试验证通过**

运行同一步骤 2 命令。预期：PASS。

- [ ] **步骤 5：运行浏览器相关现有测试**

运行：

```bash
cd backend
uv run python -m unittest test.test_crawler_tools.CrawlerHttpToolTests
```

预期：PASS。

- [ ] **步骤 6：Commit**

```bash
git add backend/app/services/crawler_tools.py backend/test/test_crawler_tools.py
git commit -m "fix(crawler): 浏览器等待失败后降级重试"
```

## 任务 4：详情补全传入 profile 意图

**文件：**
- 修改：`backend/test/test_crawl_job_runtime.py`
- 修改：`backend/app/services/crawl_job_runtime.py`

- [ ] **步骤 1：编写失败测试：详情补全调用传 `profile`**

修改 `test_run_queued_crawl_job_enriches_saved_candidate_profiles` 中的 fake 函数签名和断言。将 fake 函数改成：

```python
        async def fake_crawl_page_with_crawl4ai(
            ctx: CrawlToolContext,
            url: str,
            *,
            intent: str = "generic",
        ) -> PageSnapshot:
            _ = ctx
            sequence.append(f"enrich:{url}:{intent}")
            return PageSnapshot(
                url=url,
                title="张三",
                text="院系：计算机科学系\n研究方向：大语言模型、智能体\n代表论文：Paper A；Paper B",
                html="<html></html>",
                links=[],
                fetch_method="http",
                status="succeeded",
            )
```

将 sequence 断言改成：

```python
        self.assertEqual(
            sequence,
            ["discover", "enrich:https://cta.jxufe.edu.cn/home/teacherInfo/detail?uid=1:profile"],
        )
```

- [ ] **步骤 2：同步调整同文件其他 fake 签名**

将 `test_run_queued_crawl_job_records_enrichment_failure_events` 和其他 patch `crawl_page_with_crawl4ai` 的 fake 函数改成接受 keyword-only `intent`：

```python
        async def fake_crawl_page_with_crawl4ai(
            ctx: CrawlToolContext,
            url: str,
            *,
            intent: str = "generic",
        ) -> PageSnapshot:
            _ = ctx, url, intent
            return PageSnapshot(...)
```

- [ ] **步骤 3：运行测试验证失败**

运行：

```bash
cd backend
uv run python -m unittest test.test_crawl_job_runtime.CrawlJobRuntimeTests.test_run_queued_crawl_job_enriches_saved_candidate_profiles
```

预期：FAIL。失败原因应是 sequence 中的 intent 仍为 `generic`。

- [ ] **步骤 4：实现最少生产代码**

修改 `backend/app/services/crawl_job_runtime.py`：

```python
        snapshot = await crawl_page_with_crawl4ai(
            ctx,
            candidate.profile_url or "",
            intent="profile",
        )
```

- [ ] **步骤 5：运行测试验证通过**

运行同一步骤 3 命令。预期：PASS。

- [ ] **步骤 6：运行 runtime 相关测试**

运行：

```bash
cd backend
uv run python -m unittest test.test_crawl_job_runtime
```

预期：PASS。

- [ ] **步骤 7：Commit**

```bash
git add backend/app/services/crawl_job_runtime.py backend/test/test_crawl_job_runtime.py
git commit -m "fix(crawler): 详情补全使用 profile 抓取意图"
```

## 任务 5：回归验证和清理

**文件：**
- 检查：`backend/app/services/crawler_tools.py`
- 检查：`backend/app/services/crawl_job_runtime.py`
- 检查：`backend/test/test_crawler_tools.py`
- 检查：`backend/test/test_crawl_job_runtime.py`

- [ ] **步骤 1：运行 focused 后端测试**

运行：

```bash
cd backend
uv run python -m unittest test.test_crawler_tools test.test_crawl_job_runtime
```

预期：PASS。

- [ ] **步骤 2：运行相关现有测试**

运行：

```bash
cd backend
uv run python -m unittest test.test_windows_event_loop test.test_crawl_job_events test.test_crawl_jobs_api
```

预期：PASS。

- [ ] **步骤 3：静态检查差异**

运行：

```bash
git diff -- backend/app/services/crawler_tools.py backend/app/services/crawl_job_runtime.py backend/test/test_crawler_tools.py backend/test/test_crawl_job_runtime.py
git diff --check -- backend/app/services/crawler_tools.py backend/app/services/crawl_job_runtime.py backend/test/test_crawler_tools.py backend/test/test_crawl_job_runtime.py
```

预期：`git diff --check` 无输出。

- [ ] **步骤 4：确认没有误暂存其他工作区改动**

运行：

```bash
git status --short
```

预期：只看到本计划涉及文件的已提交变更，或看到其他用户既有改动仍保持未暂存。不要 revert 其他文件。

- [ ] **步骤 5：最终 Commit（仅当任务 1-4 未逐步提交时执行）**

如果前面已经逐任务提交，跳过此步骤。如果没有逐任务提交，则执行：

```bash
git add backend/app/services/crawler_tools.py backend/app/services/crawl_job_runtime.py backend/test/test_crawler_tools.py backend/test/test_crawl_job_runtime.py
git commit -m "fix(crawler): 优化智能抓取加载策略"
```

## 自检

- 规格中的任务内 host 记忆由任务 1 覆盖。
- 规格中的页面意图等待策略由任务 2 和任务 4 覆盖。
- 规格中的等待失败降级由任务 3 覆盖。
- 规格中的测试要求由任务 1 到任务 5 覆盖。
- 没有跨任务数据库持久化。
- 没有将 `table` 替换为另一个全局强结构假设；`css:body` 只作为页面加载的宽松门槛。
- 没有引入新的前端或数据库模型改动。
