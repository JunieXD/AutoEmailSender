# 抓取稳定性修复实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 修复详情页抓取误判、profile 模式 token 统计为 0、候选详情弹窗滚动穿透，以及多邮箱候选无法导入的问题。

**架构：** 后端在抓取工具层识别无效详情页并触发浏览器兜底，在运行时记录 profile 直接 LLM 调用的 token usage，并在候选归一化阶段只保留第一个有效邮箱。前端只调整候选详情弹窗容器结构，使长论文列表在弹窗内部滚动，不改变候选数据接口。

**技术栈：** FastAPI、SQLAlchemy、Pydantic、LangChain ChatOpenAI、unittest、Vite、React、Vitest、Testing Library。

---

## 文件结构

- 修改：`@backend/app/services/crawler_tools.py`
  - 职责：页面抓取兜底判定、候选字段归一化、多邮箱提取第一个有效邮箱。
- 修改：`@backend/app/services/crawl_job_runtime.py`
  - 职责：profile 模式详情页抽取与 enrichment 的直接 LLM 调用 token 记录。
- 修改：`@backend/app/services/crawl_job_runs.py`
  - 职责：提供可复用的 token usage 归一化函数，支持从模型响应对象提取 usage。
- 修改：`@frontend/src/pages/TasksPage.tsx`
  - 职责：候选导师详情弹窗布局、内部滚动和滚动穿透控制。
- 测试：`@backend/test/test_crawler_tools.py`
  - 覆盖 HTTP 成功但内容无效时触发浏览器兜底，以及多邮箱只保留第一个有效邮箱。
- 测试：`@backend/test/test_crawl_job_runtime.py`
  - 覆盖 profile 直接 LLM 调用 token 写入当前抓取 run。
- 测试：`@backend/test/test_crawl_job_runs.py`
  - 覆盖从不同模型响应 usage 结构中提取 token。
- 测试：`@frontend/test/TasksPageCrawler.test.tsx`
  - 覆盖候选导师详情弹窗出现内部滚动容器并阻止遮罩滚动穿透。

## 任务 1：无效详情页触发浏览器兜底

**文件：**
- 修改：`@backend/app/services/crawler_tools.py`
- 测试：`@backend/test/test_crawler_tools.py`

- [ ] **步骤 1：编写失败的测试**

在 `CrawlerHttpToolTests` 中添加两个异步测试，验证 HTTP 页面虽然状态成功，但正文是模板占位或站点错误页时，会继续调用浏览器调查。

```python
async def test_crawl_page_with_crawl4ai_retries_browser_for_template_placeholders(self) -> None:
    ctx = CrawlToolContext(
        job_id=1,
        start_url="http://cta.jxufe.edu.cn/home/teacherInfo/detail?fid=1",
        university="江西财经大学",
        school="计算机与人工智能学院",
        session_factory=_FakeSessionFactory(),  # type: ignore[arg-type]
    )
    http_snapshot = PageSnapshot(
        url=ctx.start_url,
        title="教师详情",
        text="{{name}}\n{{email}}\n{{data}}",
        html="<html>{{name}}</html>",
        links=[],
        fetch_method="http",
        status="succeeded",
    )
    browser_snapshot = PageSnapshot(
        url=ctx.start_url,
        title="教师详情",
        text="张三\n教授\n邮箱：zhang@example.edu",
        html="<html>张三</html>",
        links=[],
        fetch_method="browser",
        status="succeeded",
    )

    with patch(
        "app.services.crawler_tools.crawl_page_with_http",
        new=AsyncMock(return_value=http_snapshot),
    ), patch(
        "app.services.crawler_tools.browser_investigate",
        new=AsyncMock(return_value=browser_snapshot),
    ) as browser:
        actual = await crawl_page_with_crawl4ai(ctx, ctx.start_url, intent="profile")

    self.assertEqual(actual, browser_snapshot)
    browser.assert_awaited_once()


async def test_crawl_page_with_crawl4ai_retries_browser_for_site_error_page(self) -> None:
    ctx = CrawlToolContext(
        job_id=1,
        start_url="http://sim.jxufe.edu.cn/#/staff/detail/5",
        university="江西财经大学",
        school="计算机与人工智能学院",
        session_factory=_FakeSessionFactory(),  # type: ignore[arg-type]
    )
    http_snapshot = PageSnapshot(
        url=ctx.start_url,
        title="江西财经大学",
        text="FineCMS error\nError Number: 1064\nSQL syntax",
        html="<html>FineCMS error</html>",
        links=[],
        fetch_method="http",
        status="succeeded",
    )
    browser_snapshot = PageSnapshot(
        url=ctx.start_url,
        title="教师详情",
        text="李四\n副教授\n邮箱：li@example.edu",
        html="<html>李四</html>",
        links=[],
        fetch_method="browser",
        status="succeeded",
    )

    with patch(
        "app.services.crawler_tools.crawl_page_with_http",
        new=AsyncMock(return_value=http_snapshot),
    ), patch(
        "app.services.crawler_tools.browser_investigate",
        new=AsyncMock(return_value=browser_snapshot),
    ) as browser:
        actual = await crawl_page_with_crawl4ai(ctx, ctx.start_url, intent="profile")

    self.assertEqual(actual, browser_snapshot)
    browser.assert_awaited_once()
```

- [ ] **步骤 2：运行测试验证失败**

运行：

```bash
cd backend
uv run python -m unittest test.test_crawler_tools.CrawlerHttpToolTests.test_crawl_page_with_crawl4ai_retries_browser_for_template_placeholders test.test_crawler_tools.CrawlerHttpToolTests.test_crawl_page_with_crawl4ai_retries_browser_for_site_error_page
```

预期：FAIL。两个测试应返回 HTTP snapshot，或 `browser_investigate` 未被调用。

- [ ] **步骤 3：实现最少代码**

在 `@backend/app/services/crawler_tools.py` 中加入 profile 页面无效内容判定，并接入现有 `_should_use_crawl4ai_fallback`。

```python
INVALID_PROFILE_PAGE_MARKERS = (
    "{{name}}",
    "{{email}}",
    "{{data}}",
    "FineCMS error",
    "SQL syntax",
)


def _looks_like_unrendered_or_error_profile_page(snapshot: PageSnapshot) -> bool:
    haystack = f"{snapshot.title or ''}\n{snapshot.text}\n{snapshot.html[:2000]}"
    return any(marker in haystack for marker in INVALID_PROFILE_PAGE_MARKERS)
```

把 `_should_use_crawl4ai_fallback` 调整为：

```python
def _should_use_crawl4ai_fallback(snapshot: PageSnapshot) -> bool:
    if snapshot.fetch_method != "http":
        return False

    if snapshot.suspicious_empty:
        return True

    if _looks_like_unrendered_or_error_profile_page(snapshot):
        return True

    if snapshot.status != "succeeded":
        return False

    text = snapshot.text.strip()
    return not text and bool(snapshot.html.strip())
```

如果当前函数已有额外分支，保留原有分支，只插入 `_looks_like_unrendered_or_error_profile_page(snapshot)` 的判断。

- [ ] **步骤 4：运行测试验证通过**

运行：

```bash
cd backend
uv run python -m unittest test.test_crawler_tools.CrawlerHttpToolTests.test_crawl_page_with_crawl4ai_retries_browser_for_template_placeholders test.test_crawler_tools.CrawlerHttpToolTests.test_crawl_page_with_crawl4ai_retries_browser_for_site_error_page
```

预期：OK，2 个测试通过。

- [ ] **步骤 5：Commit**

```bash
git add backend/app/services/crawler_tools.py backend/test/test_crawler_tools.py
git commit -m "fix(抓取): 为无效详情页启用浏览器兜底"
```

## 任务 2：profile 模式记录直接 LLM 调用 token

**文件：**
- 修改：`@backend/app/services/crawl_job_runs.py`
- 修改：`@backend/app/services/crawl_job_runtime.py`
- 测试：`@backend/test/test_crawl_job_runs.py`
- 测试：`@backend/test/test_crawl_job_runtime.py`

- [ ] **步骤 1：编写失败的 token 提取测试**

在 `@backend/test/test_crawl_job_runs.py` 中添加测试，覆盖 LangChain 响应对象常见字段。

```python
class _FakeLLMResponse:
    def __init__(self) -> None:
        self.response_metadata = {
            "token_usage": {
                "prompt_tokens": 11,
                "completion_tokens": 7,
                "total_tokens": 18,
            }
        }


def test_extract_token_usage_from_llm_response_metadata(self) -> None:
    usage = extract_token_usage_from_llm_response(_FakeLLMResponse())

    self.assertEqual(
        usage,
        {
            "input_tokens": 11,
            "output_tokens": 7,
            "total_tokens": 18,
            "cached_tokens": None,
        },
    )
```

确保测试文件导入新函数：

```python
from app.services.crawl_job_runs import extract_token_usage_from_llm_response
```

- [ ] **步骤 2：运行 token 提取测试验证失败**

运行：

```bash
cd backend
uv run python -m unittest test.test_crawl_job_runs.CrawlJobRunsTests.test_extract_token_usage_from_llm_response_metadata
```

预期：ERROR，提示 `extract_token_usage_from_llm_response` 未定义或无法导入。

- [ ] **步骤 3：实现 token 提取函数**

在 `@backend/app/services/crawl_job_runs.py` 中添加：

```python
def extract_token_usage_from_llm_response(response: object) -> dict[str, int | None] | None:
    metadata = getattr(response, "response_metadata", None)
    if not isinstance(metadata, dict):
        return None

    raw_usage = metadata.get("token_usage") or metadata.get("usage")
    if not isinstance(raw_usage, dict):
        return None

    input_tokens = _coerce_token_count(raw_usage.get("prompt_tokens", raw_usage.get("input_tokens")))
    output_tokens = _coerce_token_count(
        raw_usage.get("completion_tokens", raw_usage.get("output_tokens"))
    )
    total_tokens = _coerce_token_count(raw_usage.get("total_tokens"))
    if input_tokens is None and output_tokens is None and total_tokens is None:
        return None

    input_tokens = input_tokens or 0
    output_tokens = output_tokens or 0
    total_tokens = total_tokens if total_tokens is not None else input_tokens + output_tokens
    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": total_tokens,
        "cached_tokens": _extract_cached_tokens(str(raw_usage)),
    }


def _coerce_token_count(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return None
```

如果文件中已有同名或等价的 `_coerce_token_count`，复用已有函数，不重复定义。

- [ ] **步骤 4：运行 token 提取测试验证通过**

运行：

```bash
cd backend
uv run python -m unittest test.test_crawl_job_runs.CrawlJobRunsTests.test_extract_token_usage_from_llm_response_metadata
```

预期：OK。

- [ ] **步骤 5：编写 profile runtime 失败测试**

在 `@backend/test/test_crawl_job_runtime.py` 中添加测试。它应模拟 profile 抽取成功，同时模型响应带 usage，最后断言当前 run 有 token。

```python
async def test_profile_entry_type_accumulates_direct_llm_tokens(self) -> None:
    job_id = await self._create_default_profile_and_job(
        start_url="https://example.edu/faculty/zhang",
        entry_type="profile",
    )

    class _FakeResponse:
        content = '{"name":"张三","email":"zhang@example.edu","title":"教授","confidence":0.9}'
        response_metadata = {
            "token_usage": {
                "prompt_tokens": 23,
                "completion_tokens": 9,
                "total_tokens": 32,
            }
        }

    class _FakeModel:
        async def ainvoke(self, prompt: str) -> _FakeResponse:
            self.prompt = prompt
            return _FakeResponse()

    async def fake_crawl_page_with_crawl4ai(
        ctx: CrawlToolContext,
        url: str,
        *,
        intent: str = "generic",
    ) -> PageSnapshot:
        return PageSnapshot(
            url=url,
            title="张三",
            text="张三\n教授\n邮箱：zhang@example.edu",
            html="<html></html>",
            links=[],
            fetch_method="http",
            status="succeeded",
        )

    with patch(
        "app.services.crawl_job_runtime.crawl_page_with_crawl4ai",
        new=fake_crawl_page_with_crawl4ai,
    ), patch(
        "app.services.crawl_job_runtime.build_faculty_crawler_model",
        return_value=_FakeModel(),
    ):
        await run_queued_crawl_jobs_once(self.session_factory)

    run = await self._get_current_run(job_id)
    self.assertEqual(run.input_tokens, 23)
    self.assertEqual(run.output_tokens, 9)
    self.assertEqual(run.total_tokens, 32)
```

- [ ] **步骤 6：运行 profile runtime 测试验证失败**

运行：

```bash
cd backend
uv run python -m unittest test.test_crawl_job_runtime.CrawlJobRuntimeTests.test_profile_entry_type_accumulates_direct_llm_tokens
```

预期：FAIL，run token 仍为 0。

- [ ] **步骤 7：实现直接 LLM token 累加**

在 `@backend/app/services/crawl_job_runtime.py` 中导入：

```python
from app.services.crawl_job_runs import (
    accumulate_crawl_job_run_tokens,
    extract_token_usage_from_llm_response,
    mark_crawl_job_run_finished,
    mark_crawl_job_run_paused,
    mark_crawl_job_run_running,
)
```

添加内部函数：

```python
async def _accumulate_direct_llm_response_tokens(
    ctx: CrawlToolContext,
    response: object,
) -> None:
    usage = extract_token_usage_from_llm_response(response)
    if usage is None:
        return
    async with ctx.session_factory() as session:
        job = await session.get(CrawlJob, ctx.job_id)
        if job is None:
            return
        run = await get_or_create_current_crawl_job_run(session, job)
        run.input_tokens += usage["input_tokens"] or 0
        run.output_tokens += usage["output_tokens"] or 0
        run.total_tokens += usage["total_tokens"] or 0
        cached_tokens = usage.get("cached_tokens")
        if cached_tokens is not None:
            run.cached_tokens = (run.cached_tokens or 0) + cached_tokens
        run.updated_at = datetime.now(UTC)
        await session.commit()
```

同时从 `crawl_job_runs` 导入 `get_or_create_current_crawl_job_run`。在 `extract_profile_candidate_with_llm()` 和 `enrich_candidate_profile_with_llm()` 中，`response = await model.ainvoke(prompt)` 后立即调用：

```python
await _accumulate_direct_llm_response_tokens(ctx, response)
```

- [ ] **步骤 8：运行 profile runtime 测试验证通过**

运行：

```bash
cd backend
uv run python -m unittest test.test_crawl_job_runtime.CrawlJobRuntimeTests.test_profile_entry_type_accumulates_direct_llm_tokens
```

预期：OK。

- [ ] **步骤 9：Commit**

```bash
git add backend/app/services/crawl_job_runs.py backend/app/services/crawl_job_runtime.py backend/test/test_crawl_job_runs.py backend/test/test_crawl_job_runtime.py
git commit -m "fix(抓取): 统计详情页模式模型 token"
```

## 任务 3：多邮箱只保留第一个有效邮箱

**文件：**
- 修改：`@backend/app/services/crawler_tools.py`
- 测试：`@backend/test/test_crawler_tools.py`

- [ ] **步骤 1：编写失败的测试**

在 `CrawlerToolTests` 中添加：

```python
def test_normalize_candidate_payload_keeps_first_valid_email(self) -> None:
    payload = normalize_candidate_payload(
        ProfessorCandidatePayload(
            name="方玉明",
            email="leo.fangyuming@foxmail.com, fa0001ng@e.ntu.edu.sg",
            title="教授",
        ),
        university="江西财经大学",
        school="计算机与人工智能学院",
    )

    self.assertEqual(payload["email"], "leo.fangyuming@foxmail.com")


def test_normalize_candidate_payload_uses_later_valid_email_when_first_segment_invalid(self) -> None:
    payload = normalize_candidate_payload(
        ProfessorCandidatePayload(
            name="张三",
            email="邮箱：不是邮箱；zhang@example.edu",
            title="教授",
        ),
        university="江西财经大学",
        school="计算机与人工智能学院",
    )

    self.assertEqual(payload["email"], "zhang@example.edu")
```

- [ ] **步骤 2：运行测试验证失败**

运行：

```bash
cd backend
uv run python -m unittest test.test_crawler_tools.CrawlerToolTests.test_normalize_candidate_payload_keeps_first_valid_email test.test_crawler_tools.CrawlerToolTests.test_normalize_candidate_payload_uses_later_valid_email_when_first_segment_invalid
```

预期：FAIL，第一个测试会得到完整逗号拼接字符串，第二个测试会得到原始混合字符串。

- [ ] **步骤 3：实现邮箱归一化**

在 `@backend/app/services/crawler_tools.py` 中添加：

```python
def _first_valid_email(value: str | None) -> str | None:
    if not value:
        return None
    normalized = normalize_obfuscated_email_tokens(value)
    for match in _EMAIL_PATTERN.findall(normalized):
        cleaned = match.strip().lower()
        if cleaned:
            return cleaned
    return None
```

把 `normalize_candidate_payload()` 中的 email 行改为：

```python
"email": _first_valid_email(candidate.email),
```

保留 `extract_first_email_from_text()` 现有行为，不改变页面文本抽取函数的公开行为。

- [ ] **步骤 4：运行测试验证通过**

运行：

```bash
cd backend
uv run python -m unittest test.test_crawler_tools.CrawlerToolTests.test_normalize_candidate_payload_keeps_first_valid_email test.test_crawler_tools.CrawlerToolTests.test_normalize_candidate_payload_uses_later_valid_email_when_first_segment_invalid
```

预期：OK。

- [ ] **步骤 5：Commit**

```bash
git add backend/app/services/crawler_tools.py backend/test/test_crawler_tools.py
git commit -m "fix(抓取): 多邮箱候选仅保留首个有效邮箱"
```

## 任务 4：候选导师详情弹窗内部滚动

**文件：**
- 修改：`@frontend/src/pages/TasksPage.tsx`
- 测试：`@frontend/test/TasksPageCrawler.test.tsx`

- [ ] **步骤 1：编写失败的测试**

在 `@frontend/test/TasksPageCrawler.test.tsx` 的抓取任务详情相关 describe 内添加测试。复用现有打开抓取任务详情和候选详情的测试 setup，把候选的 `recent_papers` 设置为大量条目。

```tsx
it("keeps long candidate details scrollable inside the dialog", async () => {
  listCrawlCandidates.mockResolvedValue([
    {
      ...buildCrawlCandidate(),
      id: 101,
      name: "方玉明",
      recent_papers: Array.from({ length: 30 }, (_, index) => `Paper ${index + 1}`),
    },
  ]);

  renderPage();

  fireEvent.click(await screen.findByRole("button", { name: "任务监控" }));
  fireEvent.click(await screen.findByRole("button", { name: "查看抓取任务详情" }));
  fireEvent.click(await screen.findByRole("button", { name: "详情" }));

  const dialog = await screen.findByRole("dialog", { name: "候选导师详情" });
  expect(dialog).toHaveClass("max-h-[90vh]");
  expect(within(dialog).getByTestId("candidate-detail-scroll")).toHaveClass(
    "overflow-y-auto",
  );
});
```

如果现有测试 helper 名称不同，使用文件中已有 builder 和打开流程，不新增全局 mock 模式。

- [ ] **步骤 2：运行测试验证失败**

运行：

```bash
cd frontend
npm run test -- TasksPageCrawler.test.tsx
```

预期：FAIL，候选详情 dialog 没有 `max-h-[90vh]`，也没有 `data-testid="candidate-detail-scroll"`。

- [ ] **步骤 3：实现弹窗滚动结构**

把 `@frontend/src/pages/TasksPage.tsx` 的候选详情弹窗 section 从：

```tsx
<section
  role="dialog"
  aria-label="候选导师详情"
  className="w-full max-w-3xl rounded-3xl bg-white shadow-2xl"
  onClick={(event) => event.stopPropagation()}
>
```

改为：

```tsx
<section
  role="dialog"
  aria-label="候选导师详情"
  className="flex max-h-[90vh] w-full max-w-3xl flex-col overflow-hidden rounded-3xl bg-white shadow-2xl"
  onClick={(event) => event.stopPropagation()}
>
```

把原内容 grid 外层从：

```tsx
<div className="grid gap-4 px-6 py-5 md:grid-cols-2">
```

改为：

```tsx
<div
  data-testid="candidate-detail-scroll"
  className="grid flex-1 gap-4 overflow-y-auto overscroll-contain px-6 py-5 md:grid-cols-2"
>
```

如测试或实际体验仍出现 body 滚动，在 `TasksPage` 中增加针对 `selectedCandidateDetail` 的 effect：

```tsx
useEffect(() => {
  if (!selectedCandidateDetail) {
    return;
  }
  const previousOverflow = document.body.style.overflow;
  document.body.style.overflow = "hidden";
  return () => {
    document.body.style.overflow = previousOverflow;
  };
}, [selectedCandidateDetail]);
```

- [ ] **步骤 4：运行测试验证通过**

运行：

```bash
cd frontend
npm run test -- TasksPageCrawler.test.tsx
```

预期：OK。

- [ ] **步骤 5：Commit**

```bash
git add frontend/src/pages/TasksPage.tsx frontend/test/TasksPageCrawler.test.tsx
git commit -m "fix(前端): 修复候选导师详情弹窗滚动"
```

## 任务 5：回归验证

**文件：**
- 修改：无
- 测试：相关前后端测试套件

- [ ] **步骤 1：运行后端抓取相关测试**

运行：

```bash
cd backend
uv run python -m unittest test.test_crawler_tools test.test_crawl_job_runs test.test_crawl_job_runtime test.test_crawl_jobs_api
```

预期：所有测试 OK。若套件耗时超出当前环境限制，拆成以下命令逐个运行并记录结果：

```bash
cd backend
uv run python -m unittest test.test_crawler_tools
uv run python -m unittest test.test_crawl_job_runs
uv run python -m unittest test.test_crawl_job_runtime
uv run python -m unittest test.test_crawl_jobs_api
```

- [ ] **步骤 2：运行前端相关测试**

运行：

```bash
cd frontend
npm run test -- TasksPageCrawler.test.tsx ProfessorsPageCrawler.test.tsx CrawlJobsApi.test.ts
```

预期：所有测试通过。

- [ ] **步骤 3：运行构建和 lint**

运行：

```bash
cd frontend
npm run lint
npm run build
```

预期：lint 退出码 0；build 退出码 0。Vite chunk size warning 可记录为既有构建警告，不作为失败。

- [ ] **步骤 4：检查 diff**

运行：

```bash
git diff --check
git status --short
```

预期：`git diff --check` 退出码 0；`git status --short` 只显示本计划相关文件或已清空。

- [ ] **步骤 5：Commit 验证说明**

如果任务 1-4 已分别提交，本步骤不创建额外提交。若实现者选择单提交，把所有变更提交为：

```bash
git add backend/app/services/crawler_tools.py backend/app/services/crawl_job_runs.py backend/app/services/crawl_job_runtime.py backend/test/test_crawler_tools.py backend/test/test_crawl_job_runs.py backend/test/test_crawl_job_runtime.py frontend/src/pages/TasksPage.tsx frontend/test/TasksPageCrawler.test.tsx
git commit -m "fix(抓取): 修复详情页抓取稳定性问题"
```

## 自检结果

- 规格覆盖度：四个已确认问题都有对应任务。详情页失败对应任务 1，token 为 0 对应任务 2，多邮箱对应任务 3，弹窗滚动穿透对应任务 4。
- 占位符扫描：计划中没有未定义占位内容；每个代码步骤都给出具体文件、代码片段和命令。
- 类型一致性：后端新增函数名固定为 `extract_token_usage_from_llm_response`、`_first_valid_email`、`_looks_like_unrendered_or_error_profile_page`；前端测试容器固定为 `candidate-detail-scroll`。
