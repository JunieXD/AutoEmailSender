# 抓取依赖瘦身实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 保留 Playwright Chromium，移除 Crawl4AI、browser-use、cloudscraper、pandas，并用项目内 Playwright browser fetch 后端保持现有智能抓取能力。

**架构：** HTTP 抓取、页面账本、任务取消、URL 安全校验和 `PageSnapshot` 下游契约保持不变；仅把 Crawl4AI 浏览器壳层替换为直接 Playwright 实现。公共抓取入口改名为 `crawl_page_with_browser_fallback`，浏览器内部实现改为 `_fetch_page_with_playwright_direct` 与 `_try_playwright_browser_fetch`。

**技术栈：** FastAPI 后端、Python 3.12、uv、Playwright Python、PyInstaller、Electron Builder、unittest、Vitest。

---

## 文件结构

- 修改：`backend/app/services/crawler_tools.py`
  - 负责公共抓取入口重命名、Playwright browser fetch 直接实现、错误信息和常量命名清理。
- 修改：`backend/app/services/crawl_job_runtime.py`
  - 负责运行时抓取入口 import 和调用点改名。
- 修改：`backend/app/services/crawler_v2_enrichment_worker.py`
  - 负责 v2 enrichment worker 使用新的抓取入口。
- 修改：`backend/app/agents/faculty_crawler_agent.py`
  - 负责 agent 工具内部调用新的抓取入口。
- 修改：`backend/test/test_crawler_tools.py`
  - 负责抓取工具契约、Playwright browser fetch、fallback、Windows event loop 兼容测试。
- 修改：`backend/test/test_crawl_job_runtime.py`
  - 负责运行时 patch 路径和 fake 函数改名。
- 修改：`backend/test/test_crawler_v2_enrichment_worker.py`
  - 负责 enrichment worker patch 路径改名。
- 修改：`backend/test/test_faculty_crawler_agent.py`
  - 负责 agent patch 路径改名。
- 修改：`backend/test/test_backend_build_script.py`
  - 负责打包脚本不再 collect Crawl4AI、不再处理 Patchright 的断言。
- 修改：`backend/pyproject.toml`
  - 移除 `crawl4ai`、`browser-use`、`cloudscraper`、`pandas`。
- 修改：`backend/uv.lock`
  - 通过 `uv lock` 同步依赖树。
- 修改：`scripts/build-backend.ps1`
  - 移除 Crawl4AI collect 和 Patchright 清理逻辑，保留 Playwright browser install。
- 修改：`website/docs/developer.md`
  - 只在需要时更新抓取依赖说明，不改变 Playwright Chromium 安装说明。
- 创建：`backend/test/test_live_playwright_crawler.py`
  - 放置 opt-in 真实页面验收测试，默认可通过环境变量跳过，避免普通单元测试依赖公网。

## 任务 1：改名测试先行，锁定公共入口

**文件：**
- 修改：`backend/test/test_crawler_tools.py`
- 修改：`backend/test/test_crawl_job_runtime.py`
- 修改：`backend/test/test_crawler_v2_enrichment_worker.py`
- 修改：`backend/test/test_faculty_crawler_agent.py`

- [ ] **步骤 1：把测试 import 和测试名改到新入口**

在 `backend/test/test_crawler_tools.py` 顶部 import 中，将：

```python
crawl_page_with_crawl4ai,
_crawl_page_with_crawl4ai_browser,
```

改为：

```python
crawl_page_with_browser_fallback,
_crawl_page_with_browser,
```

将测试名中的 `crawl_page_with_crawl4ai` 改为 `crawl_page_with_browser_fallback`，将 `crawl4ai_browser_fetch` 改为 `playwright_browser_fetch`。

批量改 patch 路径：

```python
"app.services.crawler_tools._crawl_page_with_crawl4ai_browser"
```

改为：

```python
"app.services.crawler_tools._crawl_page_with_browser"
```

将调用：

```python
snapshot = await crawl_page_with_crawl4ai(ctx, "https://faculty.example.edu/faculty")
```

改为：

```python
snapshot = await crawl_page_with_browser_fallback(ctx, "https://faculty.example.edu/faculty")
```

- [ ] **步骤 2：改运行时和 agent 测试 patch 路径**

在 `backend/test/test_crawl_job_runtime.py` 中，将所有：

```python
"app.services.crawl_job_runtime.crawl_page_with_crawl4ai"
```

改为：

```python
"app.services.crawl_job_runtime.crawl_page_with_browser_fallback"
```

并将 fake 函数名从：

```python
async def fake_crawl_page_with_crawl4ai(...):
```

改为：

```python
async def fake_crawl_page_with_browser_fallback(...):
```

在 `backend/test/test_crawler_v2_enrichment_worker.py` 中改对应 patch 路径为：

```python
"app.services.crawler_v2_enrichment_worker.crawl_page_with_browser_fallback"
```

在 `backend/test/test_faculty_crawler_agent.py` 中改：

```python
"app.agents.faculty_crawler_agent.crawl_page_with_crawl4ai"
```

为：

```python
"app.agents.faculty_crawler_agent.crawl_page_with_browser_fallback"
```

- [ ] **步骤 3：运行测试验证失败**

运行：

```bash
cd backend && uv run python -m unittest \
  test.test_crawler_tools \
  test.test_crawl_job_runtime \
  test.test_crawler_v2_enrichment_worker \
  test.test_faculty_crawler_agent
```

预期：FAIL，错误包含 `cannot import name 'crawl_page_with_browser_fallback'` 或 patch 找不到新函数。

## 任务 2：公共入口和内部函数机械重命名

**文件：**
- 修改：`backend/app/services/crawler_tools.py`
- 修改：`backend/app/services/crawl_job_runtime.py`
- 修改：`backend/app/services/crawler_v2_enrichment_worker.py`
- 修改：`backend/app/agents/faculty_crawler_agent.py`

- [ ] **步骤 1：重命名抓取入口**

在 `backend/app/services/crawler_tools.py` 中，将函数定义：

```python
async def crawl_page_with_crawl4ai(
    ctx: CrawlToolContext,
    url: str,
    *,
    intent: CrawlPageIntent = "generic",
) -> PageSnapshot:
```

改为：

```python
async def crawl_page_with_browser_fallback(
    ctx: CrawlToolContext,
    url: str,
    *,
    intent: CrawlPageIntent = "generic",
) -> PageSnapshot:
```

将函数体中的：

```python
if _should_use_crawl4ai_fallback(http_snapshot):
```

改为：

```python
if _should_use_browser_fallback(http_snapshot):
```

- [ ] **步骤 2：重命名浏览器内部函数**

在 `backend/app/services/crawler_tools.py` 中进行以下机械改名：

```python
_should_use_crawl4ai_fallback -> _should_use_browser_fallback
_crawl_page_with_crawl4ai_browser -> _crawl_page_with_browser
_crawl_page_with_crawl4ai_browser_direct -> _fetch_page_with_playwright_direct
_try_crawl4ai_browser_config -> _try_playwright_browser_fetch
_browser_run_config_for_intent -> _browser_fetch_options_for_intent
_browser_config_for_crawl4ai -> _playwright_launch_options
_snapshot_from_crawl4ai_result -> _snapshot_from_browser_html
```

暂时保留实现内部的 Crawl4AI import，下一任务再替换实现。这个小步只让调用路径和测试路径稳定下来。

- [ ] **步骤 3：更新调用方 import 和调用**

在 `backend/app/services/crawl_job_runtime.py` 中，将 import：

```python
crawl_page_with_crawl4ai,
```

改为：

```python
crawl_page_with_browser_fallback,
```

将调用：

```python
snapshot = await crawl_page_with_crawl4ai(ctx, ctx.start_url, intent="profile")
```

改为：

```python
snapshot = await crawl_page_with_browser_fallback(ctx, ctx.start_url, intent="profile")
```

将 enrichment retry 中的调用同样改为：

```python
snapshot = await crawl_page_with_browser_fallback(
    ctx,
    item.profile_url,
    intent="profile",
)
```

在 `backend/app/services/crawler_v2_enrichment_worker.py` 中改 import：

```python
from app.services.crawler_tools import (
    CandidateEnrichmentPayload,
    CrawlToolContext,
    PageSnapshot,
    crawl_page_with_browser_fallback,
)
```

并在 `fetch_profile_text()` 中改为：

```python
snapshot: PageSnapshot = await crawl_page_with_browser_fallback(
    ctx,
    profile_url,
    intent="profile",
)
```

在 `backend/app/agents/faculty_crawler_agent.py` 中改 import 和工具调用：

```python
snapshot = await crawl_page_with_browser_fallback(ctx, url)
```

- [ ] **步骤 4：运行改名相关测试**

运行：

```bash
cd backend && uv run python -m unittest \
  test.test_crawler_tools.CrawlerToolTests.test_browser_run_config_for_profile_uses_load_and_waits_for_body \
  test.test_crawler_tools.CrawlerHttpToolTests.test_crawl_page_with_browser_fallback_delegates_to_safe_http_path \
  test.test_crawl_job_runtime \
  test.test_crawler_v2_enrichment_worker \
  test.test_faculty_crawler_agent
```

预期：多数测试应从 import 错误转为 Crawl4AI 相关实现测试失败；如果还有旧 patch 路径报错，继续完成机械改名。

## 任务 3：引入浏览器抓取配置数据结构

**文件：**
- 修改：`backend/app/services/crawler_tools.py`
- 修改：`backend/test/test_crawler_tools.py`

- [ ] **步骤 1：编写配置测试**

将 `backend/test/test_crawler_tools.py` 中原配置测试改为：

```python
def test_browser_fetch_options_for_profile_uses_load_and_waits_for_body(self) -> None:
    options = crawler_tools._browser_fetch_options_for_intent("profile")

    self.assertEqual(options.wait_for, "css:body")
    self.assertEqual(options.wait_until, "load")
    self.assertEqual(options.wait_for_timeout_ms, 15000)
    self.assertEqual(options.page_timeout_ms, 30000)
    self.assertEqual(options.delay_before_return_html_seconds, 1.5)
    self.assertIn("Chrome/124.0.0.0", options.user_agent)

def test_browser_fetch_options_for_generic_and_directory_use_load_and_wait_for_body(self) -> None:
    generic_options = crawler_tools._browser_fetch_options_for_intent("generic")
    directory_options = crawler_tools._browser_fetch_options_for_intent("directory")

    self.assertEqual(generic_options.wait_for, "css:body")
    self.assertEqual(directory_options.wait_for, "css:body")
    self.assertEqual(generic_options.wait_until, "load")
    self.assertEqual(directory_options.wait_until, "load")
```

将浏览器启动参数测试改为：

```python
def test_playwright_launch_options_disable_chromium_https_upgrades_and_automation_controlled(self) -> None:
    options = crawler_tools._playwright_launch_options()

    args = options["args"]
    self.assertIn("--disable-features=HttpsUpgrades", args)
    self.assertIn("--disable-blink-features=AutomationControlled", args)
    self.assertTrue(options["headless"])
    self.assertNotIn("channel", options)
```

- [ ] **步骤 2：运行配置测试验证失败**

运行：

```bash
cd backend && uv run python -m unittest \
  test.test_crawler_tools.CrawlerToolTests.test_browser_fetch_options_for_profile_uses_load_and_waits_for_body \
  test.test_crawler_tools.CrawlerToolTests.test_browser_fetch_options_for_generic_and_directory_use_load_and_wait_for_body \
  test.test_crawler_tools.CrawlerToolTests.test_playwright_launch_options_disable_chromium_https_upgrades_and_automation_controlled
```

预期：FAIL，因为配置对象和 launch options 尚未按新契约实现。

- [ ] **步骤 3：实现配置数据结构**

在 `backend/app/services/crawler_tools.py` 的常量区域改名并补充参数：

```python
BROWSER_FALLBACK_STATUS = {403, 412, 429}
BROWSER_WAIT_TIMEOUT_MS = 15000
BROWSER_DELAY_SECONDS = 1.5
BROWSER_WAIT_SELECTOR = "css:body"
BROWSER_EXTRA_ARGS = (
    "--disable-features=HttpsUpgrades",
    "--disable-blink-features=AutomationControlled",
)
BROWSER_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)
```

新增 dataclass：

```python
@dataclass(frozen=True, slots=True)
class BrowserFetchOptions:
    wait_until: str = "load"
    wait_for: str | None = BROWSER_WAIT_SELECTOR
    wait_for_timeout_ms: int = BROWSER_WAIT_TIMEOUT_MS
    delay_before_return_html_seconds: float = BROWSER_DELAY_SECONDS
    page_timeout_ms: int = JS_RENDER_TIMEOUT_MS
    max_retries: int = MAX_RETRIES_FOR_BROWSER_RENDER
    user_agent: str = BROWSER_USER_AGENT
```

实现：

```python
def _browser_fetch_options_for_intent(
    intent: CrawlPageIntent,
    *,
    wait_for: str | None | object = _DEFAULT_BROWSER_WAIT_FOR,
    wait_until: str = "load",
) -> BrowserFetchOptions:
    selected_wait_for = (
        _browser_wait_selector_for_intent(intent)
        if wait_for is _DEFAULT_BROWSER_WAIT_FOR
        else wait_for
    )
    return BrowserFetchOptions(wait_until=wait_until, wait_for=selected_wait_for)


def _browser_fetch_options_for_goal(goal: str) -> BrowserFetchOptions:
    _ = goal
    return _browser_fetch_options_for_intent("generic")


def _playwright_launch_options() -> dict[str, object]:
    return {
        "headless": True,
        "args": list(BROWSER_EXTRA_ARGS),
    }
```

同时将 `_is_http_blocked_snapshot()` 内引用改为 `BROWSER_FALLBACK_STATUS`。

- [ ] **步骤 4：运行配置测试验证通过**

运行：

```bash
cd backend && uv run python -m unittest \
  test.test_crawler_tools.CrawlerToolTests.test_browser_fetch_options_for_profile_uses_load_and_waits_for_body \
  test.test_crawler_tools.CrawlerToolTests.test_browser_fetch_options_for_generic_and_directory_use_load_and_wait_for_body \
  test.test_crawler_tools.CrawlerToolTests.test_playwright_launch_options_disable_chromium_https_upgrades_and_automation_controlled
```

预期：PASS。

## 任务 4：用 Playwright 直接替换 Crawl4AI 浏览器实现

**文件：**
- 修改：`backend/app/services/crawler_tools.py`
- 修改：`backend/test/test_crawler_tools.py`

- [ ] **步骤 1：编写 Playwright 直接实现的 mock 测试**

在 `backend/test/test_crawler_tools.py` 中，把原 `test_crawl4ai_browser_fetch_disables_chromium_https_upgrades` 改成：

```python
async def test_playwright_browser_fetch_disables_chromium_https_upgrades_and_automation_controlled(self) -> None:
    launches: list[dict[str, object]] = []
    context_kwargs: list[dict[str, object]] = []

    class _Page:
        url = "http://teacher.example.edu/zhoufeng"

        async def goto(self, url: str, *, wait_until: str, timeout: int) -> None:
            self.url = url
            self.wait_until = wait_until
            self.timeout = timeout

        async def wait_for_selector(self, selector: str, *, timeout: int) -> None:
            self.selector = selector
            self.selector_timeout = timeout

        async def wait_for_timeout(self, timeout: float) -> None:
            self.delay = timeout

        async def content(self) -> str:
            return "<html><body>周锋 电子邮箱：zfeng@bupt.edu.cn</body></html>"

    class _Context:
        async def new_page(self) -> _Page:
            return _Page()

    class _Browser:
        async def new_context(self, **kwargs: object) -> _Context:
            context_kwargs.append(kwargs)
            return _Context()

        async def close(self) -> None:
            return None

    class _Chromium:
        async def launch(self, **kwargs: object) -> _Browser:
            launches.append(kwargs)
            return _Browser()

    class _Playwright:
        chromium = _Chromium()

        async def __aenter__(self) -> "_Playwright":
            return self

        async def __aexit__(self, *args: object) -> None:
            return None

    with patch(
        "app.services.crawler_tools.async_playwright",
        return_value=_Playwright(),
    ):
        snapshot = await crawler_tools._fetch_page_with_playwright_direct(
            "http://teacher.example.edu/zhoufeng",
            "",
            "profile",
        )

    self.assertEqual(snapshot.status, "succeeded")
    self.assertIn("--disable-features=HttpsUpgrades", launches[0]["args"])
    self.assertIn("--disable-blink-features=AutomationControlled", launches[0]["args"])
    self.assertIn("Chrome/124.0.0.0", context_kwargs[0]["user_agent"])
    self.assertIn("zfeng@bupt.edu.cn", snapshot.text)
```

- [ ] **步骤 2：编写等待失败降级测试**

将原等待失败测试改为：

```python
async def test_playwright_browser_fetch_retries_without_wait_selector_after_wait_failure(self) -> None:
    calls: list[str | None] = []

    class _Page:
        url = "https://teacher.example.edu/zhoufeng"

        async def goto(self, url: str, *, wait_until: str, timeout: int) -> None:
            self.url = url

        async def wait_for_selector(self, selector: str, *, timeout: int) -> None:
            calls.append(selector)
            if len(calls) == 1:
                raise TimeoutError("Wait condition failed: Timeout after 15000ms waiting for selector 'body'")

        async def wait_for_timeout(self, timeout: float) -> None:
            return None

        async def content(self) -> str:
            return "<html><body>周锋 电子邮箱：zfeng@bupt.edu.cn</body></html>"

    class _Context:
        async def new_page(self) -> _Page:
            return _Page()

    class _Browser:
        async def new_context(self, **kwargs: object) -> _Context:
            return _Context()

        async def close(self) -> None:
            return None

    class _Chromium:
        async def launch(self, **kwargs: object) -> _Browser:
            return _Browser()

    class _Playwright:
        chromium = _Chromium()

        async def __aenter__(self) -> "_Playwright":
            return self

        async def __aexit__(self, *args: object) -> None:
            return None

    with patch("app.services.crawler_tools.async_playwright", return_value=_Playwright()):
        snapshot = await crawler_tools._fetch_page_with_playwright_direct(
            "https://teacher.example.edu/zhoufeng",
            "",
            "profile",
        )

    self.assertEqual(snapshot.status, "succeeded")
    self.assertEqual(calls, ["css:body"])
    self.assertIn("zfeng@bupt.edu.cn", snapshot.text)
```

说明：第二次重试 `wait_for=None`，所以不会追加第二个 selector。

- [ ] **步骤 3：编写 load 而非 networkidle 测试**

将原 `test_crawl4ai_browser_fetch_uses_load_without_networkidle_retry` 改为：

```python
async def test_playwright_browser_fetch_uses_load_without_networkidle_retry(self) -> None:
    calls: list[str] = []

    class _Page:
        url = "https://scs.bupt.edu.cn/szjs1/jsyl.htm"

        async def goto(self, url: str, *, wait_until: str, timeout: int) -> None:
            calls.append(wait_until)
            if wait_until == "networkidle":
                raise TimeoutError("networkidle should not be used")

        async def wait_for_selector(self, selector: str, *, timeout: int) -> None:
            return None

        async def wait_for_timeout(self, timeout: float) -> None:
            return None

        async def content(self) -> str:
            return "<html><body>教师一览 周锋 电子邮箱：zfeng@bupt.edu.cn</body></html>"

    class _Context:
        async def new_page(self) -> _Page:
            return _Page()

    class _Browser:
        async def new_context(self, **kwargs: object) -> _Context:
            return _Context()

        async def close(self) -> None:
            return None

    class _Chromium:
        async def launch(self, **kwargs: object) -> _Browser:
            return _Browser()

    class _Playwright:
        chromium = _Chromium()

        async def __aenter__(self) -> "_Playwright":
            return self

        async def __aexit__(self, *args: object) -> None:
            return None

    with patch("app.services.crawler_tools.async_playwright", return_value=_Playwright()):
        snapshot = await crawler_tools._fetch_page_with_playwright_direct(
            "https://scs.bupt.edu.cn/szjs1/jsyl.htm",
            "",
            "profile",
        )

    self.assertEqual(snapshot.status, "succeeded")
    self.assertEqual(calls, ["load"])
    self.assertIn("zfeng@bupt.edu.cn", snapshot.text)
```

- [ ] **步骤 4：运行新 Playwright 测试验证失败**

运行：

```bash
cd backend && uv run python -m unittest \
  test.test_crawler_tools.CrawlerHttpToolTests.test_playwright_browser_fetch_disables_chromium_https_upgrades_and_automation_controlled \
  test.test_crawler_tools.CrawlerHttpToolTests.test_playwright_browser_fetch_retries_without_wait_selector_after_wait_failure \
  test.test_crawler_tools.CrawlerHttpToolTests.test_playwright_browser_fetch_uses_load_without_networkidle_retry
```

预期：FAIL，因为实现仍在 import Crawl4AI 或没有 `async_playwright`。

- [ ] **步骤 5：实现 Playwright 直连**

在 `backend/app/services/crawler_tools.py` 顶部增加 lazy-import 可 patch 的符号：

```python
try:
    from playwright.async_api import async_playwright
except Exception:  # pragma: no cover - dependency errors become fetch errors later
    async_playwright = None  # type: ignore[assignment]
```

也可以选择不在模块 import Playwright，而是在 `_try_playwright_browser_fetch()` 内局部 import；如果局部 import，则测试 patch 路径要改为 patch `playwright.async_api.async_playwright`。推荐模块级 lazy import，便于测试。

将 `_fetch_page_with_playwright_direct()` 实现为：

```python
async def _fetch_page_with_playwright_direct(
    absolute_url: str,
    goal: str,
    intent: CrawlPageIntent = "generic",
) -> PageSnapshot:
    _ = goal
    first_result = await _try_playwright_browser_fetch(
        absolute_url,
        _browser_fetch_options_for_intent(intent),
    )
    if first_result.status == "succeeded":
        return first_result

    if _is_wait_condition_failure(first_result.error_message):
        return await _try_playwright_browser_fetch(
            absolute_url,
            _browser_fetch_options_for_intent(intent, wait_for=None),
        )

    return first_result
```

将 `_try_playwright_browser_fetch()` 实现为：

```python
async def _try_playwright_browser_fetch(
    absolute_url: str,
    options: BrowserFetchOptions,
) -> PageSnapshot:
    if async_playwright is None:
        return _failed_snapshot(
            url=absolute_url,
            fetch_method="browser",
            error_message="Playwright browser fetch unavailable: failed to import playwright",
        )

    browser = None
    try:
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(**_playwright_launch_options())
            context = await browser.new_context(user_agent=options.user_agent)
            page = await context.new_page()
            await page.goto(
                absolute_url,
                wait_until=options.wait_until,
                timeout=options.page_timeout_ms,
            )
            if options.wait_for:
                selector = options.wait_for
                if selector.startswith("css:"):
                    selector = selector[4:]
                await page.wait_for_selector(
                    selector,
                    timeout=options.wait_for_timeout_ms,
                )
            if options.delay_before_return_html_seconds > 0:
                await page.wait_for_timeout(options.delay_before_return_html_seconds * 1000)
            html = await page.content()
            final_url = str(getattr(page, "url", "") or absolute_url)
    except Exception as exc:
        return _failed_snapshot(
            url=absolute_url,
            fetch_method="browser",
            error_message=_format_exception_for_snapshot(
                exc,
                "Playwright browser fetch failed",
            ),
        )
    finally:
        if browser is not None:
            try:
                await browser.close()
            except Exception:
                pass

    return _snapshot_from_browser_html(html=html, final_url=final_url, absolute_url=absolute_url)
```

实现 `_snapshot_from_browser_html()`：

```python
def _snapshot_from_browser_html(*, html: str, final_url: str, absolute_url: str) -> PageSnapshot:
    if not html:
        return _failed_snapshot(
            url=absolute_url,
            fetch_method="browser",
            error_message="Playwright browser fetch returned empty HTML",
        )
    snapshot = html_to_snapshot(final_url or absolute_url, html, "browser")
    if not snapshot.text.strip():
        snapshot.suspicious_empty = True
    return snapshot
```

更新 `_crawl_page_with_browser()`：

```python
async def _crawl_page_with_browser(
    ctx: CrawlToolContext,
    absolute_url: str,
    goal: str,
    intent: CrawlPageIntent = "generic",
) -> PageSnapshot:
    _ = ctx
    if _should_offload_browser_fetch_to_thread():
        return await asyncio.to_thread(
            _run_browser_fetch_with_proactor_loop,
            absolute_url,
            goal,
            intent,
        )
    return await _fetch_page_with_playwright_direct(absolute_url, goal, intent)
```

更新 `_run_browser_fetch_with_proactor_loop()`：

```python
return asyncio.run(_fetch_page_with_playwright_direct(absolute_url, goal, intent))
```

- [ ] **步骤 6：运行 Playwright mock 测试验证通过**

运行：

```bash
cd backend && uv run python -m unittest \
  test.test_crawler_tools.CrawlerHttpToolTests.test_playwright_browser_fetch_disables_chromium_https_upgrades_and_automation_controlled \
  test.test_crawler_tools.CrawlerHttpToolTests.test_playwright_browser_fetch_retries_without_wait_selector_after_wait_failure \
  test.test_crawler_tools.CrawlerHttpToolTests.test_playwright_browser_fetch_uses_load_without_networkidle_retry
```

预期：PASS。

## 任务 5：清理运行时代码中的 Crawl4AI 命名和错误文案

**文件：**
- 修改：`backend/app/services/crawler_tools.py`
- 修改：`backend/test/test_crawler_tools.py`

- [ ] **步骤 1：写搜索断言测试**

在 `backend/test/test_crawler_tools.py` 增加一个轻量测试，约束运行时代码不再引用 Crawl4AI：

```python
def test_crawler_runtime_code_no_longer_mentions_crawl4ai(self) -> None:
    source = Path(crawler_tools.__file__).read_text(encoding="utf-8")
    legacy_name = "crawl" + "4ai"

    self.assertNotIn(legacy_name, source.lower())
    self.assertNotIn("Crawl4AI", source)
```

如果文件顶部尚未 import `Path`，已有 `from pathlib import Path` 可复用。

- [ ] **步骤 2：运行搜索断言验证失败**

运行：

```bash
cd backend && uv run python -m unittest \
  test.test_crawler_tools.CrawlerToolTests.test_crawler_runtime_code_no_longer_mentions_crawl4ai
```

预期：FAIL，因为常量或错误信息仍有旧命名。

- [ ] **步骤 3：清理剩余旧命名**

在 `backend/app/services/crawler_tools.py` 中完成这些替换：

```python
CRAWL4AI_BROWSER_FALLBACK_STATUS -> BROWSER_FALLBACK_STATUS
CRAWL4AI_BROWSER_WAIT_TIMEOUT_MS -> BROWSER_WAIT_TIMEOUT_MS
CRAWL4AI_BROWSER_DELAY_SECONDS -> BROWSER_DELAY_SECONDS
CRAWL4AI_BROWSER_WAIT_SELECTOR -> BROWSER_WAIT_SELECTOR
CRAWL4AI_BROWSER_EXTRA_ARGS -> BROWSER_EXTRA_ARGS
```

将错误文案：

```python
"Crawl4AI browser fetch failed"
"Crawl4AI browser returned no result"
"Failed to load Crawl4AI"
```

替换为：

```python
"Playwright browser fetch failed"
"Playwright browser fetch returned no result"
"Playwright browser fetch unavailable"
```

删除任何 `from crawl4ai import ...`。

- [ ] **步骤 4：运行搜索断言验证通过**

运行：

```bash
cd backend && uv run python -m unittest \
  test.test_crawler_tools.CrawlerToolTests.test_crawler_runtime_code_no_longer_mentions_crawl4ai
```

预期：PASS。

## 任务 6：保持 fallback、账本、denylist、取消行为不变

**文件：**
- 修改：`backend/test/test_crawler_tools.py`
- 修改：`backend/app/services/crawler_tools.py`

- [ ] **步骤 1：运行现有 fallback 和安全边界测试**

运行：

```bash
cd backend && uv run python -m unittest \
  test.test_crawler_tools.CrawlerHttpToolTests.test_crawl_page_with_browser_fallback_delegates_to_safe_http_path \
  test.test_crawler_tools.CrawlerHttpToolTests.test_crawl_page_with_browser_fallback_falls_back_to_browser_on_empty_content \
  test.test_crawler_tools.CrawlerHttpToolTests.test_crawl_page_with_browser_fallback_falls_back_to_browser_on_blocked_http_status \
  test.test_crawler_tools.CrawlerHttpToolTests.test_crawl_page_with_browser_fallback_skips_http_for_host_after_blocked_status \
  test.test_crawler_tools.CrawlerHttpToolTests.test_crawl_page_with_browser_fallback_keeps_blocked_hosts_scoped_by_host \
  test.test_crawler_tools.CrawlerHttpToolTests.test_browser_investigate_uses_playwright_browser \
  test.test_crawler_tools.CrawlerHttpToolTests.test_browser_investigate_skips_previously_denied_url \
  test.test_crawler_tools.CrawlerHttpToolTests.test_crawl_page_with_browser_fallback_raises_when_job_is_canceled_before_fetch
```

预期：PASS。如果某些测试仍使用旧名字，先修测试名和 patch 路径，不改变业务语义。

- [ ] **步骤 2：如失败，修正 `_should_use_browser_fallback()` 和调用路径**

确认 `crawl_page_with_browser_fallback()` 中仍包含以下关键逻辑：

```python
http_snapshot = await crawl_page_with_http(ctx, url)
await _ensure_crawl_job_can_continue_for_context(ctx)
if _should_use_browser_fallback(http_snapshot):
    if _is_http_blocked_snapshot(http_snapshot):
        ctx.mark_http_blocked(http_snapshot.url or absolute_url)
    browser_snapshot = await browser_investigate(ctx, url, goal="", intent=intent)
    processed_browser_snapshot = _apply_runtime_url_denylist_after_fetch(
        ctx,
        requested_url=absolute_url,
        snapshot=browser_snapshot,
    )
    await mark_page_fetch_result(
        ctx.session_factory,
        job_id=ctx.job_id,
        original_url=absolute_url,
        snapshot=processed_browser_snapshot,
        fetch_mode="browser",
        direct_status=http_snapshot.status,
        fallback_reason=http_snapshot.error_message or "direct_fetch_unusable",
        browser_status=processed_browser_snapshot.status,
    )
    await _ensure_crawl_job_can_continue_for_context(ctx)
    return processed_browser_snapshot
```

确认 `browser_investigate()` 调用：

```python
snapshot = await _crawl_page_with_browser(ctx, absolute_url, goal, intent)
```

- [ ] **步骤 3：运行完整抓取工具测试**

运行：

```bash
cd backend && uv run python -m unittest test.test_crawler_tools
```

预期：PASS。

## 任务 7：移除未使用 Python 依赖

**文件：**
- 修改：`backend/pyproject.toml`
- 修改：`backend/uv.lock`

- [ ] **步骤 1：从 pyproject 移除依赖**

在 `backend/pyproject.toml` 的 `dependencies` 中删除：

```toml
"browser-use>=0.11.13",
"cloudscraper>=1.2.71",
"crawl4ai>=0.8.6",
"pandas>=3.0.1",
```

保留：

```toml
"playwright>=1.58.0",
"markitdown[pdf]>=0.1.5",
"openpyxl>=3.1.5",
"mammoth>=1.12.0",
"python-docx>=1.2.0",
```

- [ ] **步骤 2：同步 lockfile**

运行：

```bash
cd backend && uv lock
```

预期：命令成功，`backend/uv.lock` 中项目依赖不再包含上述 4 个包。

- [ ] **步骤 3：验证依赖不可从项目直接引用**

运行：

```bash
cd backend && uv run python - <<'PY'
import importlib.util

removed = ["crawl4ai", "browser_use", "cloudscraper", "pandas"]
for name in removed:
    print(name, importlib.util.find_spec(name))
PY
```

预期：如果本地 `.venv` 尚未 prune，可能仍能找到包；不要把这个作为失败标准。真正标准是 `pyproject.toml` 和 `uv.lock` 不再把它们列为项目依赖。

- [ ] **步骤 4：用 grep 验证运行时代码无 import**

运行：

```bash
rg -n "from (crawl4ai|browser_use|cloudscraper|pandas) import|import (crawl4ai|browser_use|cloudscraper|pandas)" backend/app backend/test scripts
```

预期：无输出。

## 任务 8：清理 Windows 后端打包脚本

**文件：**
- 修改：`scripts/build-backend.ps1`
- 修改：`backend/test/test_backend_build_script.py`

- [ ] **步骤 1：更新脚本测试**

在 `backend/test/test_backend_build_script.py` 中，将包收集测试改为：

```python
def test_collects_runtime_dependencies_for_packaging(self) -> None:
    script = Path(__file__).resolve().parents[1] / ".." / "scripts" / "build-backend.ps1"
    content = script.resolve().read_text(encoding="utf-8")

    for package_name in [
        "markitdown",
        "mammoth",
        "pdfminer",
        "pdfplumber",
        "pypdf",
        "playwright",
    ]:
        self.assertIn(f"--collect-all {package_name}", content)

    self.assertNotIn("--collect-all crawl4ai", content)
    self.assertNotIn("--collect-all patchright", content)
    self.assertNotIn("--exclude-module patchright", content)
    self.assertNotIn("$PackagedPatchrightDir", content)
```

- [ ] **步骤 2：运行脚本测试验证失败**

运行：

```bash
cd backend && uv run python -m unittest test.test_backend_build_script
```

预期：FAIL，因为脚本仍 collect Crawl4AI 并清理 Patchright。

- [ ] **步骤 3：修改 `scripts/build-backend.ps1`**

删除：

```powershell
    --collect-all crawl4ai `
    --exclude-module patchright `
```

删除末尾：

```powershell
  $PackagedPatchrightDir = Join-Path $BackendDistDir "_internal\patchright"
  if (Test-Path $PackagedPatchrightDir) {
    Remove-Item -Recurse -Force $PackagedPatchrightDir
  }
```

保留：

```powershell
  $env:PLAYWRIGHT_BROWSERS_PATH = $PlaywrightBrowsersDir
  uv run python -m playwright install --only-shell chromium
  ...
    --collect-all playwright `
```

- [ ] **步骤 4：运行脚本测试验证通过**

运行：

```bash
cd backend && uv run python -m unittest test.test_backend_build_script
```

预期：PASS。

## 任务 9：更新文档中当前开发说明

**文件：**
- 修改：`website/docs/developer.md`
- 不修改：历史 `docs/superpowers/specs/*` 和 `docs/superpowers/plans/*`

- [ ] **步骤 1：搜索当前用户文档引用**

运行：

```bash
rg -n "Crawl4AI|crawl4ai|browser-use|browser_use|Patchright|patchright" website/docs docs -g '!docs/superpowers/plans/**' -g '!docs/superpowers/specs/2026-04-*' -g '!docs/superpowers/specs/2026-05-*'
```

预期：可能只剩本轮设计文档和开发文档。历史规格和历史计划允许保留旧引用，因为它们记录当时设计。

- [ ] **步骤 2：更新开发说明**

如果 `website/docs/developer.md` 有描述 Crawl4AI 或 Patchright，将其改为：

```markdown
后端抓取使用 Playwright Chromium headless shell。首次开发或打包前执行 `.\scripts\install-backend-playwright.ps1`，脚本会将浏览器下载到 `backend/ms-playwright/`。
```

保留 `backend/ms-playwright/` 相关说明。

- [ ] **步骤 3：运行文档搜索确认**

运行：

```bash
rg -n "Crawl4AI|crawl4ai|browser-use|browser_use|Patchright|patchright" backend/app backend/test scripts website/docs
```

预期：无输出；如果 `backend/test` 中有 “no longer mentions” 的测试名或断言字符串包含 `crawl4ai`，可允许测试文件中仅保留该搜索测试，或者把测试移到使用拼接字符串避免直接命中。

## 任务 10：新增真实页面 opt-in 验收测试

**文件：**
- 创建：`backend/test/test_live_playwright_crawler.py`

- [ ] **步骤 1：创建跳过型 live test**

新增文件：

```python
from __future__ import annotations

import os
import unittest

from app.services.crawler_tools import html_to_snapshot
from playwright.async_api import async_playwright


LIVE_URLS = [
    (
        "http://www.sei.ecnu.edu.cn/33189/list.htm",
        ("教师", "导师", "学院", "软件"),
    ),
    (
        "https://informatics.xmu.edu.cn/list_teacher.jsp?urltype=tp.TpCollegeZWTeachers&wbtreeid=2171&collegeid=1532&postdutyid=1123&language=zh_CN&faggregatequeryid=&checkaggregatequeryid=1123",
        ("教师", "导师", "信息"),
    ),
    (
        "https://scs.bupt.edu.cn/szjs1/jsyl.htm",
        ("教师", "导师", "计算机", "周锋"),
    ),
]


@unittest.skipUnless(
    os.environ.get("AUTO_EMAIL_SENDER_LIVE_CRAWLER_TESTS") == "1",
    "set AUTO_EMAIL_SENDER_LIVE_CRAWLER_TESTS=1 to run live crawler tests",
)
class LivePlaywrightCrawlerTests(unittest.IsolatedAsyncioTestCase):
    async def test_required_teacher_pages_return_useful_snapshots(self) -> None:
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(
                headless=True,
                args=[
                    "--disable-features=HttpsUpgrades",
                    "--disable-blink-features=AutomationControlled",
                ],
            )
            try:
                context = await browser.new_context(
                    user_agent=(
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/124.0.0.0 Safari/537.36"
                    )
                )
                for url, markers in LIVE_URLS:
                    with self.subTest(url=url):
                        page = await context.new_page()
                        await page.goto(url, wait_until="load", timeout=30000)
                        await page.wait_for_selector("body", timeout=15000)
                        await page.wait_for_timeout(1500)
                        snapshot = html_to_snapshot(page.url, await page.content(), "browser")
                        self.assertEqual(snapshot.status, "succeeded")
                        self.assertGreater(len(snapshot.text.strip()), 50)
                        self.assertTrue(
                            any(marker in snapshot.text for marker in markers),
                            snapshot.text[:500],
                        )
            finally:
                await browser.close()
```

说明：这个测试先锁定真实页面和 Chromium 参数。实现完成后可以把内部逻辑改为调用 `_fetch_page_with_playwright_direct()`，让验收直接覆盖业务后端。

- [ ] **步骤 2：运行默认测试确认跳过**

运行：

```bash
cd backend && uv run python -m unittest test.test_live_playwright_crawler
```

预期：OK，显示 skipped。

- [ ] **步骤 3：运行 live 测试**

确保已安装浏览器：

```bash
cd backend && PLAYWRIGHT_BROWSERS_PATH=./ms-playwright uv run python -m playwright install --only-shell chromium
```

运行：

```bash
cd backend && AUTO_EMAIL_SENDER_LIVE_CRAWLER_TESTS=1 PLAYWRIGHT_BROWSERS_PATH=./ms-playwright uv run python -m unittest test.test_live_playwright_crawler
```

预期：三个页面均 PASS。若北邮失败，先检查实际响应状态、正文是否为空、是否被 412/400 拦截，再调整 `_playwright_launch_options()` 或 context 设置。

## 任务 11：完整后端和桌面验证

**文件：**
- 无代码文件修改，执行验证命令。

- [ ] **步骤 1：运行后端完整测试**

运行：

```bash
cd backend && uv run python -m unittest discover test
```

预期：PASS。

- [ ] **步骤 2：运行桌面测试**

运行：

```bash
cd desktop && npm run test
```

预期：PASS，特别是 `desktop/test/packaging.test.ts` 和 `desktop/test/backend.test.ts`。

- [ ] **步骤 3：运行前端测试**

这次不改前端，至少运行基础测试：

```bash
cd frontend && npm run test
```

预期：PASS。

- [ ] **步骤 4：搜索运行时代码旧依赖**

运行：

```bash
rg -n "crawl4ai|Crawl4AI|browser-use|browser_use|cloudscraper|pandas|patchright|Patchright" \
  backend/app backend/test scripts desktop website/docs
```

预期：无运行时代码残留。测试中的搜索断言如果刻意拼接字符串，也不应造成误报。

## 任务 12：体积对比和打包 smoke test

**文件：**
- 无代码文件修改，记录验证数据。

- [ ] **步骤 1：记录开发环境体积**

运行：

```bash
du -sh backend/.venv 2>/dev/null || true
du -sh backend/ms-playwright 2>/dev/null || true
du -sh backend/dist/backend 2>/dev/null || true
```

预期：记录输出。`backend/.venv` 仅作参考，最终用户体积以 Windows 包和安装目录为准。

- [ ] **步骤 2：在 Windows 执行后端打包**

在 Windows PowerShell 中运行：

```powershell
.\scripts\build-backend.ps1 -Clean
```

预期：成功生成 `backend/dist/backend`，且 `_internal` 中没有 `crawl4ai`、`patchright`、`browser_use`、`cloudscraper`、`pandas` 目录。

- [ ] **步骤 3：在 Windows 执行桌面打包**

在 Windows PowerShell 中运行：

```powershell
cd desktop
npm run build
npm run dist
```

预期：成功生成 NSIS 安装包，`resources/ms-playwright` 仍存在。

- [ ] **步骤 4：安装包 smoke test**

安装新版后执行：

1. 打开应用。
2. 创建或复用测试抓取任务。
3. 分别抓取：
   - `http://www.sei.ecnu.edu.cn/33189/list.htm`
   - `https://informatics.xmu.edu.cn/list_teacher.jsp?urltype=tp.TpCollegeZWTeachers&wbtreeid=2171&collegeid=1532&postdutyid=1123&language=zh_CN&faggregatequeryid=&checkaggregatequeryid=1123`
   - `https://scs.bupt.edu.cn/szjs1/jsyl.htm`
4. 确认任务失败时错误信息不含 Crawl4AI；成功时页面正文非空。

- [ ] **步骤 5：记录最终体积**

记录：

```text
backend/dist/backend: <size>
Windows installer: <size>
Installed resources directory: <size>
resources/ms-playwright: <size>
```

将数据写入本次 PR 或 release note，不写入代码文件。

## 任务 13：最终自检

**文件：**
- 可能修改：`docs/superpowers/plans/2026-06-14-crawler-dependency-slimming.md`

- [ ] **步骤 1：检查计划覆盖设计文档**

逐条对照 `docs/superpowers/specs/2026-06-14-crawler-dependency-slimming-design.md`：

- 保留 Playwright 和 Chromium：任务 3、4、8、12 覆盖。
- 移除 Crawl4AI：任务 4、5、7、8 覆盖。
- 移除 browser-use、cloudscraper、pandas：任务 7、11 覆盖。
- 保持 PageSnapshot：任务 4、6、10 覆盖。
- Windows 桌面包：任务 8、11、12 覆盖。
- 三个真实页面：任务 10、12 覆盖。
- MarkItDown 不动：任务 7、8 明确保留材料解析依赖。

- [ ] **步骤 2：扫描占位符和旧命名**

运行：

```bash
rg -n "TODO|待定|后续实现|类似任务|crawl_page_with_crawl4ai\\(" docs/superpowers/plans/2026-06-14-crawler-dependency-slimming.md
```

预期：无占位符。历史名称只应出现在“从旧名改到新名”的说明中，不应作为最终调用形态出现。

- [ ] **步骤 3：最终命令清单**

最终实现完成后，至少提供这些验证结果：

```bash
cd backend && uv run python -m unittest discover test
cd backend && AUTO_EMAIL_SENDER_LIVE_CRAWLER_TESTS=1 PLAYWRIGHT_BROWSERS_PATH=./ms-playwright uv run python -m unittest test.test_live_playwright_crawler
cd desktop && npm run test
cd frontend && npm run test
```

Windows 打包验证：

```powershell
.\scripts\build-backend.ps1 -Clean
cd desktop
npm run build
npm run dist
```
