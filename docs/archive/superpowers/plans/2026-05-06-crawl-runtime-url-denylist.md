# 本轮抓取 URL Denylist 实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 为智能爬取增加仅限当前任务的精确 URL denylist：页面首次允许进入；抓取后若确认既不是导师列表页也不是导师详情页，则加入本轮 denylist，后续相同 URL 直接跳过，并且不从该页面继续扩展链接。

**架构：** denylist 放在 `CrawlToolContext` 的运行期状态中，由 `crawl_page_with_crawl4ai` 和 `browser_investigate` 共同使用。页面抓取完成后执行轻量分类，只对确定无关页、登录页、下载页、确定性错误页做本轮精确 URL 标记，不做 path 前缀、不做全局持久化。

**技术栈：** FastAPI 后端、DeepAgents 受控工具、Python `unittest`、`uv`。

---

## 文件结构

- 修改：`backend/app/services/crawler_tools.py`
  - 在 `CrawlToolContext` 中增加本轮 URL denylist 状态和访问方法。
  - 增加页面分类、denylist 快照、抓取后处理 helper。
  - 在 `crawl_page_with_crawl4ai` 和 `browser_investigate` 接入 denylist。
- 修改：`backend/app/agents/faculty_crawler_agent.py`
  - 调整爬虫系统提示词和工具描述，让模型知道无关页会被本轮跳过，导师详情页允许进入。
- 修改：`backend/test/test_crawler_tools.py`
  - 增加 denylist 状态、请求前跳过、抓取后标记、浏览器工具共用 denylist、重定向源和最终 URL 同时标记的测试。
- 可选修改：`backend/test/test_faculty_crawler_agent.py`
  - 如果现有测试覆盖系统提示词，可同步更新断言；如果没有对应测试，不新增 agent prompt 快照测试。

---

## 行为规则

1. 首次访问正常同域 URL 时，不因为猜测而拒绝。
2. 只有页面已经在本轮被确认无关后，后续相同规范化 URL 才直接拒绝。
3. denylist 只匹配精确 URL，规范化时去掉 fragment，不做 path 前缀、不做 host 级拉黑。
4. 页面分类只使用抓取结果，不在请求前判断页面类型。
5. 允许类型：
   - `list`：导师列表页、教师名录页、师资队伍教师列表。
   - `profile`：单个导师详情页、教师个人主页。
6. 加入 denylist 的类型：
   - `irrelevant`：新闻、通知、招生、校友、党建、学校首页、搜索结果等与导师列表或详情无关的成功页面。
   - `blocked`：登录页、统一认证页、明确下载页、确定性拒绝页。
7. 临时网络失败不加入 denylist：
   - 超时、连接失败、5xx、抓取异常只返回失败快照，不记录为无关 URL。
8. 一旦页面加入 denylist：
   - 返回给模型的 `links` 必须清空。
   - 后续相同 URL 返回 failed snapshot，错误信息说明本轮已跳过。
9. 如果请求 URL `A` 最终重定向到无关页 `B`，同时标记 `A` 和 `B`。

---

### 任务 1：为 `CrawlToolContext` 增加精确 URL denylist

**文件：**
- 修改：`backend/app/services/crawler_tools.py`
- 测试：`backend/test/test_crawler_tools.py`

- [ ] **步骤 1：编写失败测试**

在 `backend/test/test_crawler_tools.py` 中加入上下文状态测试，验证 URL fragment 会被归一化，且 denylist 只影响精确 URL。

```python
def test_crawl_tool_context_tracks_denied_urls_by_normalized_exact_url(self) -> None:
    ctx = CrawlToolContext(
        job_id=1,
        start_url="https://cs.example.edu/faculty/index.htm",
        university="测试大学",
        school="计算机学院",
        session_factory=self.session_factory,
    )

    ctx.mark_denied_url("https://cs.example.edu/news/a.htm#section", "无关新闻页")

    self.assertTrue(ctx.is_denied_url("https://cs.example.edu/news/a.htm"))
    self.assertEqual(
        ctx.denied_url_reason("https://cs.example.edu/news/a.htm#other"),
        "无关新闻页",
    )
    self.assertFalse(ctx.is_denied_url("https://cs.example.edu/news/b.htm"))
    self.assertFalse(ctx.is_denied_url("https://cs.example.edu/news/"))
```

- [ ] **步骤 2：运行测试验证失败**

运行：

```powershell
rtk pwsh -NoProfile -Command "Set-Location backend; uv run python -m unittest test.test_crawler_tools.CrawlerToolsTestCase.test_crawl_tool_context_tracks_denied_urls_by_normalized_exact_url"
```

预期：失败，报错包含 `AttributeError: 'CrawlToolContext' object has no attribute 'mark_denied_url'`。

- [ ] **步骤 3：实现最小上下文状态**

在 `CrawlToolContext` 中增加字段和方法：

```python
    denied_urls: dict[str, str] = field(default_factory=dict)

    def mark_denied_url(self, url: str, reason: str) -> None:
        normalized = _normalize_page_cache_url(url)
        if normalized:
            self.denied_urls[normalized] = reason

    def is_denied_url(self, url: str) -> bool:
        return _normalize_page_cache_url(url) in self.denied_urls

    def denied_url_reason(self, url: str) -> str | None:
        return self.denied_urls.get(_normalize_page_cache_url(url))
```

- [ ] **步骤 4：运行测试验证通过**

运行：

```powershell
rtk pwsh -NoProfile -Command "Set-Location backend; uv run python -m unittest test.test_crawler_tools.CrawlerToolsTestCase.test_crawl_tool_context_tracks_denied_urls_by_normalized_exact_url"
```

预期：通过。

---

### 任务 2：已确认无关 URL 的后续请求直接跳过

**文件：**
- 修改：`backend/app/services/crawler_tools.py`
- 测试：`backend/test/test_crawler_tools.py`

- [ ] **步骤 1：编写失败测试**

加入测试：当 URL 已在本轮 denylist 中，`crawl_page_with_crawl4ai` 不应再调用 HTTP 或浏览器抓取。

```python
async def test_crawl_page_with_crawl4ai_skips_previously_denied_url(self) -> None:
    ctx = CrawlToolContext(
        job_id=1,
        start_url="https://cs.example.edu/faculty/index.htm",
        university="测试大学",
        school="计算机学院",
        session_factory=self.session_factory,
    )
    ctx.mark_denied_url("https://cs.example.edu/news/a.htm", "无关新闻页")

    with patch("app.services.crawler_tools.crawl_page_with_http") as mocked_http, patch(
        "app.services.crawler_tools.browser_investigate"
    ) as mocked_browser:
        snapshot = await crawl_page_with_crawl4ai(ctx, "https://cs.example.edu/news/a.htm")

    mocked_http.assert_not_called()
    mocked_browser.assert_not_called()
    self.assertEqual(snapshot.status, "failed")
    self.assertEqual(snapshot.links, [])
    self.assertIn("已在本轮抓取中判定为无关页面", snapshot.error_message or "")
```

- [ ] **步骤 2：运行测试验证失败**

运行：

```powershell
rtk pwsh -NoProfile -Command "Set-Location backend; uv run python -m unittest test.test_crawler_tools.CrawlerToolsTestCase.test_crawl_page_with_crawl4ai_skips_previously_denied_url"
```

预期：失败，因为 `crawl_page_with_crawl4ai` 仍会继续抓取。

- [ ] **步骤 3：实现 denylist failed snapshot helper**

在 `crawler_tools.py` 中增加 helper：

```python
def _denied_url_snapshot(ctx: CrawlToolContext, url: str, fetch_method: str) -> PageSnapshot | None:
    reason = ctx.denied_url_reason(url)
    if reason is None:
        return None
    return _failed_snapshot(
        url=url,
        fetch_method=fetch_method,
        error_message=f"该 URL 已在本轮抓取中判定为无关页面，已跳过：{reason}",
    )
```

在 `crawl_page_with_crawl4ai` 的 `absolute_url = urljoin(ctx.start_url, url)` 后、读取缓存前加入：

```python
    denied_snapshot = _denied_url_snapshot(ctx, absolute_url, "http")
    if denied_snapshot is not None:
        denied_snapshot.links = []
        await record_page_snapshot(ctx, denied_snapshot)
        return denied_snapshot
```

- [ ] **步骤 4：运行测试验证通过**

运行：

```powershell
rtk pwsh -NoProfile -Command "Set-Location backend; uv run python -m unittest test.test_crawler_tools.CrawlerToolsTestCase.test_crawl_page_with_crawl4ai_skips_previously_denied_url"
```

预期：通过。

---

### 任务 3：抓取后分类无关页面并加入本轮 denylist

**文件：**
- 修改：`backend/app/services/crawler_tools.py`
- 测试：`backend/test/test_crawler_tools.py`

- [ ] **步骤 1：编写失败测试**

加入测试：成功抓到新闻页后，系统应清空链接、标记为 failed，并把 URL 加入 denylist。

```python
async def test_crawl_page_with_crawl4ai_denies_irrelevant_succeeded_page_after_fetch(self) -> None:
    ctx = CrawlToolContext(
        job_id=1,
        start_url="https://cs.example.edu/faculty/index.htm",
        university="测试大学",
        school="计算机学院",
        session_factory=self.session_factory,
    )
    http_snapshot = PageSnapshot(
        url="https://cs.example.edu/news/a.htm",
        title="学院新闻",
        text="学院召开本科招生宣传会议，欢迎考生报考。",
        html="<html><body><a href='/news/b.htm'>下一篇</a></body></html>",
        links=["https://cs.example.edu/news/b.htm"],
        fetch_method="http",
        status="succeeded",
    )

    with patch(
        "app.services.crawler_tools.crawl_page_with_http",
        return_value=http_snapshot,
    ):
        snapshot = await crawl_page_with_crawl4ai(ctx, "https://cs.example.edu/news/a.htm")

    self.assertEqual(snapshot.status, "failed")
    self.assertEqual(snapshot.links, [])
    self.assertTrue(ctx.is_denied_url("https://cs.example.edu/news/a.htm"))
    self.assertIn("不是导师列表页或导师详情页", snapshot.error_message or "")
```

加入反向测试：导师列表页和导师详情页不应进入 denylist。

```python
async def test_crawl_page_with_crawl4ai_keeps_faculty_directory_and_profile_pages_allowed(self) -> None:
    ctx = CrawlToolContext(
        job_id=1,
        start_url="https://cs.example.edu/faculty/index.htm",
        university="测试大学",
        school="计算机学院",
        session_factory=self.session_factory,
    )
    directory_snapshot = PageSnapshot(
        url="https://cs.example.edu/faculty/index.htm",
        title="教师名录",
        text="教师名录 教授 张三 李四 副教授 王五",
        html="<html><body><a href='/faculty/zhang.htm'>张三</a></body></html>",
        links=["https://cs.example.edu/faculty/zhang.htm"],
        fetch_method="http",
        status="succeeded",
    )
    profile_snapshot = PageSnapshot(
        url="https://cs.example.edu/faculty/zhang.htm",
        title="张三 教授",
        text="张三 教授 邮箱 zhang@example.edu 研究方向 人工智能",
        html="<html><body>张三 教授 邮箱 zhang@example.edu</body></html>",
        links=[],
        fetch_method="http",
        status="succeeded",
    )

    with patch(
        "app.services.crawler_tools.crawl_page_with_http",
        side_effect=[directory_snapshot, profile_snapshot],
    ):
        directory = await crawl_page_with_crawl4ai(ctx, "https://cs.example.edu/faculty/index.htm")
        profile = await crawl_page_with_crawl4ai(ctx, "https://cs.example.edu/faculty/zhang.htm")

    self.assertEqual(directory.status, "succeeded")
    self.assertEqual(profile.status, "succeeded")
    self.assertFalse(ctx.is_denied_url("https://cs.example.edu/faculty/index.htm"))
    self.assertFalse(ctx.is_denied_url("https://cs.example.edu/faculty/zhang.htm"))
```

- [ ] **步骤 2：运行测试验证失败**

运行：

```powershell
rtk pwsh -NoProfile -Command "Set-Location backend; uv run python -m unittest test.test_crawler_tools.CrawlerToolsTestCase.test_crawl_page_with_crawl4ai_denies_irrelevant_succeeded_page_after_fetch test.test_crawler_tools.CrawlerToolsTestCase.test_crawl_page_with_crawl4ai_keeps_faculty_directory_and_profile_pages_allowed"
```

预期：第一个测试失败，因为抓取后还没有分类和 denylist。

- [ ] **步骤 3：实现页面分类 helper**

在 `crawler_tools.py` 增加类型和 helper：

```python
CrawlPageKind = Literal["list", "profile", "irrelevant", "blocked", "unknown"]


def _classify_crawl_page_snapshot(snapshot: PageSnapshot) -> CrawlPageKind:
    haystack = "\n".join(
        part
        for part in (snapshot.title or "", snapshot.text or "")
        if part
    )
    normalized = haystack.lower()
    if snapshot.status == "failed":
        message = (snapshot.error_message or "").lower()
        if any(marker in normalized or marker in message for marker in ("登录", "统一身份认证", "login", "cas")):
            return "blocked"
        return "unknown"
    if any(marker in normalized for marker in ("登录", "统一身份认证", "login", "cas")):
        return "blocked"
    if any(marker in normalized for marker in ("教师名录", "师资队伍", "导师队伍", "faculty", "teacher directory")):
        return "list"
    if _EMAIL_PATTERN.search(haystack) and any(marker in normalized for marker in ("教授", "副教授", "讲师", "研究员", "导师", "研究方向", "email")):
        return "profile"
    if any(marker in normalized for marker in ("学院新闻", "通知公告", "招生", "校友", "党建", "搜索", "新闻网")):
        return "irrelevant"
    return "irrelevant"
```

说明：这里的 `unknown` 只用于失败快照，成功页面无法识别时按无关页处理，符合“入口必须是列表页或详情页”的产品约束。

- [ ] **步骤 4：实现抓取后处理 helper**

增加 helper：

```python
def _apply_runtime_url_denylist_after_fetch(
    ctx: CrawlToolContext,
    *,
    requested_url: str,
    snapshot: PageSnapshot,
) -> PageSnapshot:
    page_kind = _classify_crawl_page_snapshot(snapshot)
    if page_kind in {"list", "profile", "unknown"}:
        return snapshot

    reason = (
        "页面不是导师列表页或导师详情页"
        if page_kind == "irrelevant"
        else "页面为登录、认证、下载或确定性不可用页面"
    )
    ctx.mark_denied_url(requested_url, reason)
    if snapshot.url:
        ctx.mark_denied_url(snapshot.url, reason)
    return snapshot.model_copy(
        update={
            "status": "failed",
            "links": [],
            "error_message": f"{reason}，已加入本轮排除列表",
        }
    )
```

在 `crawl_page_with_crawl4ai` 的 HTTP 成功路径和浏览器 fallback 返回路径使用该 helper：

```python
    http_snapshot = await crawl_page_with_http(ctx, url)
    if _should_use_crawl4ai_fallback(http_snapshot):
        ...
        browser_snapshot = await browser_investigate(ctx, url, goal="", intent=intent)
        return _apply_runtime_url_denylist_after_fetch(
            ctx,
            requested_url=absolute_url,
            snapshot=browser_snapshot,
        )
    processed_snapshot = _apply_runtime_url_denylist_after_fetch(
        ctx,
        requested_url=absolute_url,
        snapshot=http_snapshot,
    )
    if processed_snapshot.status == "succeeded":
        ctx.remember_page_snapshot(processed_snapshot)
    return processed_snapshot
```

注意：不要把 `processed_snapshot.status == "failed"` 的无关页放入 `page_snapshot_cache`，后续应通过 denylist 返回清晰错误，而不是命中普通缓存。

- [ ] **步骤 5：运行测试验证通过**

运行：

```powershell
rtk pwsh -NoProfile -Command "Set-Location backend; uv run python -m unittest test.test_crawler_tools.CrawlerToolsTestCase.test_crawl_page_with_crawl4ai_denies_irrelevant_succeeded_page_after_fetch test.test_crawler_tools.CrawlerToolsTestCase.test_crawl_page_with_crawl4ai_keeps_faculty_directory_and_profile_pages_allowed"
```

预期：通过。

---

### 任务 4：让 `browser_investigate` 共用 denylist

**文件：**
- 修改：`backend/app/services/crawler_tools.py`
- 测试：`backend/test/test_crawler_tools.py`

- [ ] **步骤 1：编写失败测试**

加入测试：已被 `crawl_page` 标记的 URL，浏览器调查也必须跳过。

```python
async def test_browser_investigate_skips_previously_denied_url(self) -> None:
    ctx = CrawlToolContext(
        job_id=1,
        start_url="https://cs.example.edu/faculty/index.htm",
        university="测试大学",
        school="计算机学院",
        session_factory=self.session_factory,
    )
    ctx.mark_denied_url("https://cs.example.edu/news/a.htm", "无关新闻页")

    with patch("app.services.crawler_tools._crawl_page_with_crawl4ai_browser") as mocked_browser:
        snapshot = await browser_investigate(
            ctx,
            "https://cs.example.edu/news/a.htm",
            goal="查找导师邮箱",
        )

    mocked_browser.assert_not_called()
    self.assertEqual(snapshot.status, "failed")
    self.assertEqual(snapshot.links, [])
    self.assertIn("已在本轮抓取中判定为无关页面", snapshot.error_message or "")
```

- [ ] **步骤 2：运行测试验证失败**

运行：

```powershell
rtk pwsh -NoProfile -Command "Set-Location backend; uv run python -m unittest test.test_crawler_tools.CrawlerToolsTestCase.test_browser_investigate_skips_previously_denied_url"
```

预期：失败，因为 `browser_investigate` 未查 denylist。

- [ ] **步骤 3：接入浏览器工具请求前 denylist**

在 `browser_investigate` 的 `absolute_url = urljoin(ctx.start_url, url)` 后、读取缓存前加入：

```python
    denied_snapshot = _denied_url_snapshot(ctx, absolute_url, "browser")
    if denied_snapshot is not None:
        denied_snapshot.links = []
        await record_page_snapshot(ctx, denied_snapshot)
        return denied_snapshot
```

- [ ] **步骤 4：浏览器抓取后也执行分类**

在 `browser_investigate` 成功调用 `_crawl_page_with_crawl4ai_browser` 后，将原来的返回改成：

```python
    snapshot = await _crawl_page_with_crawl4ai_browser(ctx, absolute_url, goal, intent)
    processed_snapshot = _apply_runtime_url_denylist_after_fetch(
        ctx,
        requested_url=absolute_url,
        snapshot=snapshot,
    )
    await record_page_snapshot(ctx, processed_snapshot)
    if processed_snapshot.status == "succeeded":
        ctx.remember_page_snapshot(processed_snapshot)
    return processed_snapshot
```

- [ ] **步骤 5：运行测试验证通过**

运行：

```powershell
rtk pwsh -NoProfile -Command "Set-Location backend; uv run python -m unittest test.test_crawler_tools.CrawlerToolsTestCase.test_browser_investigate_skips_previously_denied_url"
```

预期：通过。

---

### 任务 5：覆盖重定向源 URL 和最终 URL 同时标记

**文件：**
- 修改：`backend/app/services/crawler_tools.py`
- 测试：`backend/test/test_crawler_tools.py`

- [ ] **步骤 1：编写失败测试**

加入测试：请求 URL 和最终 URL 不一致时，若最终页面无关，两个 URL 都加入 denylist。

```python
async def test_irrelevant_redirect_marks_requested_and_final_url_denied(self) -> None:
    ctx = CrawlToolContext(
        job_id=1,
        start_url="https://cs.example.edu/faculty/index.htm",
        university="测试大学",
        school="计算机学院",
        session_factory=self.session_factory,
    )
    redirected_snapshot = PageSnapshot(
        url="https://cs.example.edu/news/a.htm",
        title="学院新闻",
        text="学院新闻 本科招生 宣传会议",
        html="<html><body>学院新闻</body></html>",
        links=[],
        fetch_method="http",
        status="succeeded",
    )

    with patch(
        "app.services.crawler_tools.crawl_page_with_http",
        return_value=redirected_snapshot,
    ):
        snapshot = await crawl_page_with_crawl4ai(ctx, "https://cs.example.edu/go-news")

    self.assertEqual(snapshot.status, "failed")
    self.assertTrue(ctx.is_denied_url("https://cs.example.edu/go-news"))
    self.assertTrue(ctx.is_denied_url("https://cs.example.edu/news/a.htm"))
```

- [ ] **步骤 2：运行测试验证失败或确认通过**

运行：

```powershell
rtk pwsh -NoProfile -Command "Set-Location backend; uv run python -m unittest test.test_crawler_tools.CrawlerToolsTestCase.test_irrelevant_redirect_marks_requested_and_final_url_denied"
```

预期：如果任务 3 的 helper 已正确标记 `requested_url` 和 `snapshot.url`，此测试直接通过；否则失败并暴露缺口。

- [ ] **步骤 3：补齐双 URL 标记**

确认 `_apply_runtime_url_denylist_after_fetch` 同时包含：

```python
    ctx.mark_denied_url(requested_url, reason)
    if snapshot.url:
        ctx.mark_denied_url(snapshot.url, reason)
```

- [ ] **步骤 4：运行测试验证通过**

运行：

```powershell
rtk pwsh -NoProfile -Command "Set-Location backend; uv run python -m unittest test.test_crawler_tools.CrawlerToolsTestCase.test_irrelevant_redirect_marks_requested_and_final_url_denied"
```

预期：通过。

---

### 任务 6：更新 DeepAgents 提示词和工具描述

**文件：**
- 修改：`backend/app/agents/faculty_crawler_agent.py`
- 测试：`backend/test/test_faculty_crawler_agent.py`（仅在已有相关断言时修改）

- [ ] **步骤 1：更新系统提示词**

将 `FACULTY_CRAWLER_SYSTEM_PROMPT` 中“发现阶段不要为了补全单个候选而立刻深入资料页；详情字段由用户后续主动触发补全。”替换为：

```text
- 当列表页提供明确导师详情页链接时，可以进入详情页提取邮箱、研究方向、近期论文等关键信息。
- 如果工具返回某页面已被判定为无关或已加入本轮排除列表，不要围绕该 URL 重试，也不要从该页面继续扩展。
```

保留原有边界：

```text
- 只能访问入口 URL 同域页面；跨域链接、mailto、文件下载、登录区和无关站点都不要访问。
- 每抓完一个列表页或一小批明确候选后，就立即调用 save_professor_candidates 保存，不要等所有页面都分析完再一次性输出大批量 JSON。
```

- [ ] **步骤 2：更新 `crawl_page` 工具描述**

将 `crawl_page` docstring 从：

```python
"""抓取入口 URL 同域内的页面并返回规范化页面快照。"""
```

改为：

```python
"""抓取入口 URL 同域内的页面并返回规范化页面快照；本轮已判定无关的 URL 会被直接跳过。"""
```

- [ ] **步骤 3：运行 agent 相关测试**

运行：

```powershell
rtk pwsh -NoProfile -Command "Set-Location backend; uv run python -m unittest test.test_faculty_crawler_agent"
```

预期：通过。如果没有该测试模块或当前环境缺少外部依赖，记录具体错误，不改动无关测试结构。

---

### 任务 7：运行完整爬虫工具测试并检查回归

**文件：**
- 测试：`backend/test/test_crawler_tools.py`
- 测试：`backend/test/test_crawl_job_runtime.py`

- [ ] **步骤 1：运行 crawler tools 测试**

运行：

```powershell
rtk pwsh -NoProfile -Command "Set-Location backend; uv run python -m unittest test.test_crawler_tools"
```

预期：通过。

- [ ] **步骤 2：运行 crawl job runtime 测试**

运行：

```powershell
rtk pwsh -NoProfile -Command "Set-Location backend; uv run python -m unittest test.test_crawl_job_runtime"
```

预期：通过。

- [ ] **步骤 3：运行 crawler API 测试**

运行：

```powershell
rtk pwsh -NoProfile -Command "Set-Location backend; uv run python -m unittest test.test_crawl_jobs_api"
```

预期：通过。

- [ ] **步骤 4：人工检查日志行为**

使用现有 `C:/Users/Junie/Downloads/crawl-job-2.jsonl` 的行为作为参考，后续手动跑同类任务时确认：

- 导师详情页仍允许进入。
- 新闻、通知、招生等无关页首次抓取后返回“已加入本轮排除列表”。
- 同一 URL 再次请求时不触发 HTTP 或浏览器抓取。
- 被加入 denylist 的页面返回 `links: []`。

---

## 自检

- 规格覆盖度：已覆盖本轮运行期状态、首次允许进入、抓取后分类、精确 URL denylist、清空 links、两个工具共用、重定向双 URL 标记、DeepAgents 提示词更新。
- 范围控制：不做 path 前缀、不做 host 级、不做全局持久化、不增加 navigation 类型。
- 风险控制：临时网络失败不加入 denylist；只有成功但无关、登录认证、下载或确定性不可用页面进入 denylist。
- 测试覆盖：每个核心行为都有对应 `unittest`，并包含回归测试命令。

---

## 执行方式

计划已完成。两种执行方式：

1. 子代理驱动（推荐）：每个任务调度一个新的子代理，任务间进行审查，快速迭代。
2. 内联执行：在当前会话中使用 executing-plans 执行任务，按任务批量实现并设检查点。
