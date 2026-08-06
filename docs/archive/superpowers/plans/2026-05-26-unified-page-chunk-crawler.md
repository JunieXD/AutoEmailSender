# 统一 Page Chunk 抓取链路实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 将智能抓取 Agent 的页面正文处理统一到 page chunk 状态机，只保留 `crawl_page`、`investigate_with_browser`、`claim_next_page_chunk`、`submit_page_chunk_candidates` 四个 Agent 可见工具。

**架构：** `crawl_page` 与 `investigate_with_browser` 只负责获取同域页面正文；凡是拿到 `PageSnapshot` 正文都创建 `crawl_page_chunks` 并返回 `chunked`，候选保存只能通过 `submit_page_chunk_candidates`。旧的 `submit_chunk_candidates` 与 `save_professor_candidates` 不再暴露给 Agent，内部仍复用 `save_candidate_batch` 完成去重、merge 与持久化。

**技术栈：** Python 3、FastAPI 后端、LangChain/Deep Agents 工具、SQLite/SQLAlchemy 风格服务层、`unittest`、`uv`。

---

## 文件结构

- 修改：`backend/app/agents/faculty_crawler_agent.py` — 统一 Agent prompt、工具列表、工具 schema、浏览器兜底 chunk 化与上下文压缩工具名。
- 修改：`backend/app/services/crawler_chunk_runtime.py` — 将 chunk 提交运行时重命名为 `submit_page_chunk_candidates`，新候选写入 `source_kind="page_chunk"`。
- 修改：`backend/app/services/crawler_tools.py` — 保留内部 `save_candidate_batch`，补充 `page_chunk` 来源优先级与历史 `list_chunk` 兼容。
- 修改：`backend/app/services/crawl_job_events.py` — 更新事件摘要和前端低价值日志过滤的工具名，避免旧工具名残留。
- 修改：`backend/test/test_faculty_crawler_agent.py` — 覆盖 Agent 可见工具、prompt、浏览器 chunk 化和旧工具移除。
- 修改：`backend/test/test_crawler_chunk_runtime.py` — 覆盖新提交函数、参数名、`page_chunk` 写入和拆分语义。
- 修改：`backend/test/test_crawler_tools.py` — 覆盖 `page_chunk` 参与候选 merge，历史 `list_chunk` 仍可读。
- 修改：`backend/test/test_crawl_job_events.py` — 覆盖新工具名的事件摘要与隐藏规则。
- 修改：`backend/test/test_crawl_job_runtime.py` — 覆盖运行时 trace 中不再出现旧保存工具。
- 修改：`backend/test/test_crawler_chunking.py` — 复用现有 chunk 创建测试，补充浏览器正文进入同一 chunk 流程时需要的断言。

## 实施原则

- 不添加 URL、标题、DOM 规则来判断列表页或详情页，页面是否值得继续访问仍由模型决定。
- 不保留任何模型可调用的旧工具 wrapper；旧工具名只允许在历史测试数据或迁移兼容注释中出现。
- 每个任务独立提交，提交前运行本任务列出的最小测试。
- PowerShell 读写中文文件时使用 `-Encoding UTF8`；Python 依赖命令统一使用 `uv`。

### 任务 1：锁定 Agent 可见工具集合

**文件：**
- 修改：`backend/test/test_faculty_crawler_agent.py`
- 修改：`backend/app/agents/faculty_crawler_agent.py`

- [ ] **步骤 1：编写失败的测试**

在 `backend/test/test_faculty_crawler_agent.py` 中新增或更新工具集合测试，断言 Agent 只暴露四个工具：

```python
def test_faculty_crawler_agent_exposes_only_unified_page_chunk_tools(self):
    tools = {tool.name for tool in build_faculty_crawler_agent_tools()}

    assert tools == {
        "crawl_page",
        "investigate_with_browser",
        "claim_next_page_chunk",
        "submit_page_chunk_candidates",
    }
    assert "submit_chunk_candidates" not in tools
    assert "save_professor_candidates" not in tools
```

如果当前测试文件没有 `build_faculty_crawler_agent_tools()` helper，先新增本地 helper，调用现有创建工具列表的函数；不要为了测试改生产代码导出额外 API。

- [ ] **步骤 2：运行测试验证失败**

运行：

```powershell
cd backend
uv run python -m unittest test.test_faculty_crawler_agent
```

预期：FAIL，断言中出现旧工具 `submit_chunk_candidates` 或 `save_professor_candidates`，或缺少 `submit_page_chunk_candidates`。

- [ ] **步骤 3：编写最少实现代码**

在 `backend/app/agents/faculty_crawler_agent.py` 中调整工具注册列表：

```python
tools = [
    crawl_page,
    investigate_with_browser,
    claim_next_page_chunk,
    submit_page_chunk_candidates,
]
```

删除 Agent 可见的 `submit_chunk_candidates` 和 `save_professor_candidates` 工具注册；如果它们以 `@tool` 函数存在但只供 Agent 调用，连同 wrapper 一并删除。

- [ ] **步骤 4：运行测试验证通过**

运行：

```powershell
cd backend
uv run python -m unittest test.test_faculty_crawler_agent
```

预期：PASS；若有其他测试因旧工具名断言失败，只更新断言到四工具集合，不改变业务语义。

- [ ] **步骤 5：Commit**

```powershell
git add backend/test/test_faculty_crawler_agent.py backend/app/agents/faculty_crawler_agent.py
git commit -m "refactor(crawler): expose unified page chunk tools"
```

### 任务 2：重命名 chunk 提交运行时

**文件：**
- 修改：`backend/test/test_crawler_chunk_runtime.py`
- 修改：`backend/app/services/crawler_chunk_runtime.py`
- 修改：`backend/app/agents/faculty_crawler_agent.py`

- [ ] **步骤 1：编写失败的测试**

在 `backend/test/test_crawler_chunk_runtime.py` 中把新入口作为主测试对象：

```python
def test_submit_page_chunk_candidates_marks_chunk_completed_and_writes_page_chunk(self):
    result = submit_page_chunk_candidates(
        job_id=self.job_id,
        chunk_id=self.chunk_id,
        candidates=[self.professor_payload(name="Alice Zhang")],
        chunk_status="completed",
        has_unsubmitted_candidates_in_current_chunk=False,
    )

    assert result["status"] == "saved"
    saved = self.load_candidate("Alice Zhang")
    assert saved["source_kind"] == "page_chunk"
    assert saved["source_chunk_id"] == self.chunk_id
```

另加一个 schema 级测试，确保 Agent 工具参数只出现新参数名：

```python
def test_submit_page_chunk_candidates_schema_uses_current_chunk_flag(self):
    schema = submit_page_chunk_candidates.args_schema.model_json_schema()
    properties = schema["properties"]

    assert "has_unsubmitted_candidates_in_current_chunk" in properties
    assert "has_more_candidates_in_chunk" not in properties
```

- [ ] **步骤 2：运行测试验证失败**

运行：

```powershell
cd backend
uv run python -m unittest test.test_crawler_chunk_runtime test.test_faculty_crawler_agent
```

预期：FAIL，错误为 `submit_page_chunk_candidates` 未定义、schema 中仍出现旧参数，或保存的 `source_kind` 仍是 `list_chunk`。

- [ ] **步骤 3：编写最少实现代码**

在 `backend/app/services/crawler_chunk_runtime.py` 中将运行时函数改为新名称，并把保存来源改为 `page_chunk`：

```python
def submit_page_chunk_candidates(
    *,
    job_id: int,
    chunk_id: int,
    candidates: list[ProfessorCandidatePayload],
    chunk_status: str,
    has_unsubmitted_candidates_in_current_chunk: bool,
) -> dict[str, Any]:
    return _submit_page_chunk_candidates_impl(
        job_id=job_id,
        chunk_id=chunk_id,
        candidates=candidates,
        chunk_status=chunk_status,
        has_unsubmitted_candidates_in_current_chunk=has_unsubmitted_candidates_in_current_chunk,
        source_kind="page_chunk",
    )
```

如果现有代码仍有内部兼容函数 `_submit_chunk_candidates_impl`，可以保留为私有实现，但 Agent 工具、prompt 和测试只引用 `submit_page_chunk_candidates`。

- [ ] **步骤 4：运行测试验证通过**

运行：

```powershell
cd backend
uv run python -m unittest test.test_crawler_chunk_runtime test.test_faculty_crawler_agent
```

预期：PASS；新增候选写入 `page_chunk`，历史测试中显式构造的 `list_chunk` 数据仍可读取。

- [ ] **步骤 5：Commit**

```powershell
git add backend/test/test_crawler_chunk_runtime.py backend/test/test_faculty_crawler_agent.py backend/app/services/crawler_chunk_runtime.py backend/app/agents/faculty_crawler_agent.py
git commit -m "refactor(crawler): rename page chunk submission tool"
```

### 任务 3：兼容 page_chunk 候选 merge

**文件：**
- 修改：`backend/test/test_crawler_tools.py`
- 修改：`backend/app/services/crawler_tools.py`

- [ ] **步骤 1：编写失败的测试**

在 `backend/test/test_crawler_tools.py` 中新增 merge 来源测试：

```python
def test_page_chunk_source_merges_with_existing_candidate(self):
    existing = self.save_candidate(
        name="Alice Zhang",
        email="alice@example.edu",
        source_kind="search_result",
    )

    result = save_candidate_batch(
        job_id=self.job_id,
        candidates=[
            ProfessorCandidatePayload(
                name="Alice Zhang",
                source_url="https://example.edu/faculty/alice",
                research_areas=["machine learning"],
                evidence=["Profile page lists machine learning."],
            )
        ],
        source_kind="page_chunk",
        source_chunk_id=123,
    )

    merged = self.load_candidate(existing.id)
    assert result["saved_count"] == 1
    assert merged.email == "alice@example.edu"
    assert "machine learning" in merged.research_areas
    assert merged.source_kind in {"search_result", "page_chunk"}
```

再保留一个历史兼容断言：

```python
def test_legacy_list_chunk_source_remains_accepted(self):
    result = save_candidate_batch(
        job_id=self.job_id,
        candidates=[ProfessorCandidatePayload(name="Bob Lee", source_url="https://example.edu/bob")],
        source_kind="list_chunk",
        source_chunk_id=456,
    )

    assert result["saved_count"] == 1
```

- [ ] **步骤 2：运行测试验证失败**

运行：

```powershell
cd backend
uv run python -m unittest test.test_crawler_tools
```

预期：FAIL，错误为 `page_chunk` 不在来源优先级中或保存校验拒绝该来源。

- [ ] **步骤 3：编写最少实现代码**

在 `backend/app/services/crawler_tools.py` 中更新来源优先级，保留历史 `list_chunk`：

```python
_SOURCE_PRIORITY = {
    "manual": 100,
    "detail_page": 80,
    "page_chunk": 70,
    "list_chunk": 60,
    "search_result": 40,
}
```

如果文件中有 source kind 白名单或展示映射，也加入 `page_chunk`，展示文案使用「页面片段」。

- [ ] **步骤 4：运行测试验证通过**

运行：

```powershell
cd backend
uv run python -m unittest test.test_crawler_tools
```

预期：PASS，`page_chunk` 与 `list_chunk` 均可保存，merge 不覆盖更高可信字段。

- [ ] **步骤 5：Commit**

```powershell
git add backend/test/test_crawler_tools.py backend/app/services/crawler_tools.py
git commit -m "feat(crawler): support page chunk candidate source"
```

### 任务 4：让 investigate_with_browser 正文进入 chunk

**文件：**
- 修改：`backend/test/test_faculty_crawler_agent.py`
- 修改：`backend/test/test_crawler_chunking.py`
- 修改：`backend/app/agents/faculty_crawler_agent.py`

- [ ] **步骤 1：编写失败的测试**

在 `backend/test/test_faculty_crawler_agent.py` 中新增浏览器正文 chunk 化测试，使用现有 mock 浏览器调查函数返回成功 `PageSnapshot`：

```python
def test_investigate_with_browser_chunks_successful_page_snapshot(self):
    snapshot = PageSnapshot(
        url="https://example.edu/faculty",
        status="succeeded",
        title="Faculty",
        text="Alice Zhang\nProfessor\nMachine Learning",
        html="<html><body>Alice Zhang</body></html>",
    )
    self.browser_investigate.return_value = snapshot

    result = investigate_with_browser.invoke({"url": "https://example.edu/faculty"})

    assert result["status"] == "chunked"
    assert result["chunk_count"] >= 1
    assert "Alice Zhang" not in str(result)
    chunks = self.load_chunks_for_url("https://example.edu/faculty")
    assert len(chunks) == result["chunk_count"]
```

在同一文件中保留已有待处理 chunk 的保护测试：

```python
def test_investigate_with_browser_requires_pending_chunks_first(self):
    self.create_page_chunk(url="https://example.edu/faculty", status="pending")

    result = investigate_with_browser.invoke({"url": "https://example.edu/other"})

    assert result["status"] == "chunk_required"
    self.browser_investigate.assert_not_called()
```

- [ ] **步骤 2：运行测试验证失败**

运行：

```powershell
cd backend
uv run python -m unittest test.test_faculty_crawler_agent test.test_crawler_chunking
```

预期：FAIL，当前 `investigate_with_browser` 仍把浏览器正文直接返回，或没有创建 `crawl_page_chunks`。

- [ ] **步骤 3：编写最少实现代码**

在 `backend/app/agents/faculty_crawler_agent.py` 中，`investigate_with_browser` 获得浏览器结果后按 `PageSnapshot` 统一处理：

```python
snapshot = browser_investigate(url=url, reason=reason)
if isinstance(snapshot, PageSnapshot) and snapshot.status == "succeeded" and (snapshot.text or snapshot.html):
    chunk_result = create_chunks_for_successful_page_snapshot(
        job_id=job_id,
        page_url=snapshot.url or url,
        snapshot=snapshot,
    )
    if chunk_result.created_chunks > 0:
        return _format_chunked_crawl_page_response(chunk_result)
    return {"status": "no_content", "message": "浏览器获取到页面，但没有可处理的正文片段。"}
return _format_browser_summary(snapshot)
```

返回值不得包含完整 `text` 或 `html`。如果 `crawl_page` 已有 `_format_chunked_crawl_page_response`，复用它以保持前端日志和 Agent 语义一致。

- [ ] **步骤 4：运行测试验证通过**

运行：

```powershell
cd backend
uv run python -m unittest test.test_faculty_crawler_agent test.test_crawler_chunking
```

预期：PASS；浏览器成功正文与普通抓取正文都生成 `crawl_page_chunks`。

- [ ] **步骤 5：Commit**

```powershell
git add backend/test/test_faculty_crawler_agent.py backend/test/test_crawler_chunking.py backend/app/agents/faculty_crawler_agent.py
git commit -m "feat(crawler): chunk browser page snapshots"
```

### 任务 5：彻底移除旧 Agent 保存入口

**文件：**
- 修改：`backend/test/test_faculty_crawler_agent.py`
- 修改：`backend/test/test_crawl_job_runtime.py`
- 修改：`backend/app/agents/faculty_crawler_agent.py`

- [ ] **步骤 1：编写失败的测试**

在 `backend/test/test_faculty_crawler_agent.py` 中新增 prompt 和旧工具名测试：

```python
def test_agent_prompt_does_not_mention_removed_save_tools(self):
    prompt = build_faculty_crawler_system_prompt(job_context=self.job_context)

    assert "save_professor_candidates" not in prompt
    assert "submit_chunk_candidates" not in prompt
    assert "submit_page_chunk_candidates" in prompt
```

在 `backend/test/test_crawl_job_runtime.py` 中新增 trace 保护测试：

```python
def test_crawl_job_runtime_rejects_removed_agent_tool_names_from_trace(self):
    trace = run_mock_crawler_job(seed_url="https://example.edu/faculty")
    tool_names = [event.tool_name for event in trace.tool_events]

    assert "save_professor_candidates" not in tool_names
    assert "submit_chunk_candidates" not in tool_names
```

- [ ] **步骤 2：运行测试验证失败**

运行：

```powershell
cd backend
uv run python -m unittest test.test_faculty_crawler_agent test.test_crawl_job_runtime
```

预期：FAIL，prompt、trace 或工具定义中仍出现旧工具名。

- [ ] **步骤 3：编写最少实现代码**

在 `backend/app/agents/faculty_crawler_agent.py` 中删除旧工具 wrapper：

```python
# 删除：
# @tool("save_professor_candidates")
# def save_professor_candidates(...): ...
#
# 删除：
# @tool("submit_chunk_candidates")
# def submit_chunk_candidates(...): ...
```

同时删除只服务于旧 wrapper 的 wrong-tool-loop 计数、列表页阻断和旧工具描述。保留 `backend/app/services/crawler_tools.py` 中的内部 `save_candidate_batch`，由 `submit_page_chunk_candidates` 调用。

- [ ] **步骤 4：运行测试验证通过**

运行：

```powershell
cd backend
uv run python -m unittest test.test_faculty_crawler_agent test.test_crawl_job_runtime
```

预期：PASS；Agent prompt 和 trace 只出现统一四工具。

- [ ] **步骤 5：Commit**

```powershell
git add backend/test/test_faculty_crawler_agent.py backend/test/test_crawl_job_runtime.py backend/app/agents/faculty_crawler_agent.py
git commit -m "refactor(crawler): remove legacy agent save tools"
```

### 任务 6：更新 Prompt、工具描述和压缩语义

**文件：**
- 修改：`backend/test/test_faculty_crawler_agent.py`
- 修改：`backend/app/agents/faculty_crawler_agent.py`

- [ ] **步骤 1：编写失败的测试**

在 `backend/test/test_faculty_crawler_agent.py` 中新增描述约束测试：

```python
def test_unified_tool_descriptions_constrain_chunk_flow(self):
    tools = {tool.name: tool for tool in build_faculty_crawler_agent_tools()}

    crawl_description = tools["crawl_page"].description
    browser_description = tools["investigate_with_browser"].description
    submit_description = tools["submit_page_chunk_candidates"].description

    assert "返回 chunked 后" in crawl_description
    assert "claim_next_page_chunk" in crawl_description
    assert "当前任务存在待处理 chunk" in browser_description
    assert "不能用于绕过 chunk" in browser_description
    assert "当前 chunk 正文" in submit_description
    assert "下一页" not in submit_description.split("too_many_candidates", 1)[0]
```

新增上下文压缩工具名测试：

```python
def test_context_compaction_tracks_submit_page_chunk_candidates(self):
    messages = [
        make_tool_message(name="claim_next_page_chunk", content={"chunk_id": 1, "content": "large text"}),
        make_tool_message(name="submit_page_chunk_candidates", content={"chunk_id": 1, "status": "saved"}),
    ]

    compacted = compact_completed_chunk_messages(messages)

    assert "large text" not in str(compacted)
    assert "chunk 1 completed" in str(compacted).lower()
```

- [ ] **步骤 2：运行测试验证失败**

运行：

```powershell
cd backend
uv run python -m unittest test.test_faculty_crawler_agent
```

预期：FAIL，描述仍使用旧工具名，或压缩逻辑只识别 `submit_chunk_candidates`。

- [ ] **步骤 3：编写最少实现代码**

在 `backend/app/agents/faculty_crawler_agent.py` 中更新 prompt 和工具描述：

```text
页面正文候选必须通过 submit_page_chunk_candidates 提交。
investigate_with_browser 不能用于绕过 chunk；当前存在待处理 chunk 时必须先处理 chunk。
too_many_candidates 只表示当前 chunk 正文中明确还有超过 10 个已看见未提交候选。
下一页、下一个 chunk、分页导航、详情页链接或浏览器整页视图不能作为当前 chunk 过密依据。
```

把上下文压缩中识别完成 chunk 的工具名改为 `submit_page_chunk_candidates`，不再识别旧工具名作为 Agent 当前链路的一部分。

- [ ] **步骤 4：运行测试验证通过**

运行：

```powershell
cd backend
uv run python -m unittest test.test_faculty_crawler_agent
```

预期：PASS；prompt、description 和 compaction 都使用 page chunk 语义。

- [ ] **步骤 5：Commit**

```powershell
git add backend/test/test_faculty_crawler_agent.py backend/app/agents/faculty_crawler_agent.py
git commit -m "docs(crawler): tighten unified page chunk prompt"
```

### 任务 7：更新事件摘要和前端日志过滤

**文件：**
- 修改：`backend/test/test_crawl_job_events.py`
- 修改：`backend/app/services/crawl_job_events.py`

- [ ] **步骤 1：编写失败的测试**

在 `backend/test/test_crawl_job_events.py` 中新增新工具名摘要测试：

```python
def test_submit_page_chunk_candidates_event_is_low_value_for_timeline(self):
    event = make_tool_event(
        tool_name="submit_page_chunk_candidates",
        payload={"chunk_id": 12, "saved_count": 3, "status": "saved"},
    )

    summary = summarize_crawl_job_event(event)

    assert summary.title == "Agent 提交页面片段候选"
    assert summary.hidden_from_timeline is True
```

再新增旧工具名不作为当前摘要入口测试：

```python
def test_removed_save_tools_are_not_present_in_low_value_tool_set(self):
    assert "submit_chunk_candidates" not in LOW_VALUE_AGENT_TOOL_NAMES
    assert "save_professor_candidates" not in LOW_VALUE_AGENT_TOOL_NAMES
    assert "submit_page_chunk_candidates" in LOW_VALUE_AGENT_TOOL_NAMES
```

- [ ] **步骤 2：运行测试验证失败**

运行：

```powershell
cd backend
uv run python -m unittest test.test_crawl_job_events
```

预期：FAIL，事件服务仍识别旧工具名或没有隐藏新提交工具。

- [ ] **步骤 3：编写最少实现代码**

在 `backend/app/services/crawl_job_events.py` 中替换工具名常量和摘要分支：

```python
LOW_VALUE_AGENT_TOOL_NAMES = {
    "crawl_page",
    "claim_next_page_chunk",
    "submit_page_chunk_candidates",
}

_TOOL_TITLES = {
    "submit_page_chunk_candidates": "Agent 提交页面片段候选",
    "claim_next_page_chunk": "Agent 领取待处理页面片段",
}
```

如果此前已按用户要求隐藏 `crawl_page`、`claim_next_page_chunk`、提交 chunk 等低价值日志，保持隐藏行为，只替换新工具名。

- [ ] **步骤 4：运行测试验证通过**

运行：

```powershell
cd backend
uv run python -m unittest test.test_crawl_job_events
```

预期：PASS；前端执行日志不再显示低价值统一 chunk 工具事件。

- [ ] **步骤 5：Commit**

```powershell
git add backend/test/test_crawl_job_events.py backend/app/services/crawl_job_events.py
git commit -m "refactor(crawler): update page chunk event summaries"
```

### 任务 8：端到端回归统一链路

**文件：**
- 修改：`backend/test/test_crawler_chunk_runtime.py`
- 修改：`backend/test/test_crawl_job_runtime.py`
- 修改：`backend/app/services/crawler_chunk_runtime.py`
- 修改：`backend/app/agents/faculty_crawler_agent.py`

- [ ] **步骤 1：编写失败的测试**

在 `backend/test/test_crawler_chunk_runtime.py` 中补充拆分语义回归：

```python
def test_exactly_ten_candidates_completed_does_not_split_chunk(self):
    candidates = [self.professor_payload(name=f"Professor {index}") for index in range(10)]

    result = submit_page_chunk_candidates(
        job_id=self.job_id,
        chunk_id=self.chunk_id,
        candidates=candidates,
        chunk_status="completed",
        has_unsubmitted_candidates_in_current_chunk=False,
    )

    assert result["status"] == "saved"
    chunk = self.load_chunk(self.chunk_id)
    assert chunk.status == "completed"
    assert self.count_child_chunks(self.chunk_id) == 0
```

在 `backend/test/test_crawl_job_runtime.py` 中补充端到端工具序列断言：

```python
def test_runtime_uses_unified_page_chunk_sequence(self):
    trace = run_mock_crawler_job(seed_url="https://example.edu/faculty")
    tool_names = [event.tool_name for event in trace.tool_events]

    assert "crawl_page" in tool_names
    assert "claim_next_page_chunk" in tool_names
    assert "submit_page_chunk_candidates" in tool_names
    assert "submit_chunk_candidates" not in tool_names
    assert "save_professor_candidates" not in tool_names
```

- [ ] **步骤 2：运行测试验证失败**

运行：

```powershell
cd backend
uv run python -m unittest test.test_crawler_chunk_runtime test.test_crawl_job_runtime
```

预期：FAIL，刚好 10 个候选可能仍触发 split，或运行时 trace 仍含旧工具名。

- [ ] **步骤 3：编写最少实现代码**

确认 `backend/app/services/crawler_chunk_runtime.py` 中拆分条件为条件拆分：

```python
should_split = (
    len(candidates) > MAX_CANDIDATES_PER_CHUNK
    or chunk_status == "too_many_candidates"
    or has_unsubmitted_candidates_in_current_chunk
)
```

实现时不要把「刚好 10 且明确无更多」判定为 split；`has_unsubmitted_candidates_in_current_chunk=False` 且 `chunk_status="completed"` 时必须完成当前 chunk。

- [ ] **步骤 4：运行测试验证通过**

运行：

```powershell
cd backend
uv run python -m unittest test.test_crawler_chunk_runtime test.test_crawl_job_runtime
```

预期：PASS；统一链路和条件拆分都稳定。

- [ ] **步骤 5：Commit**

```powershell
git add backend/test/test_crawler_chunk_runtime.py backend/test/test_crawl_job_runtime.py backend/app/services/crawler_chunk_runtime.py backend/app/agents/faculty_crawler_agent.py
git commit -m "test(crawler): cover unified page chunk runtime flow"
```

### 任务 9：清理旧名称残留并运行回归

**文件：**
- 修改：`backend/app/agents/faculty_crawler_agent.py`
- 修改：`backend/app/services/crawler_chunk_runtime.py`
- 修改：`backend/app/services/crawl_job_events.py`
- 修改：相关 `backend/test/test_*.py`

- [ ] **步骤 1：执行旧工具名扫描**

运行：

```powershell
rg "submit_chunk_candidates|save_professor_candidates|list_chunk" backend/app backend/test
```

预期：旧工具名只允许出现在历史兼容测试说明、迁移数据构造或明确验证“不存在”的断言里；`list_chunk` 只允许出现在历史数据兼容路径里。

- [ ] **步骤 2：修复不合规残留**

如果扫描结果显示 Agent prompt、tool description、tool registry、runtime trace 或 event summary 仍引用旧工具名，替换为新名称：

```python
"submit_page_chunk_candidates"
```

如果扫描结果显示新写入候选仍使用旧来源，替换为：

```python
source_kind="page_chunk"
```

- [ ] **步骤 3：运行后端 crawler 相关回归**

运行：

```powershell
cd backend
uv run python -m unittest test.test_faculty_crawler_agent test.test_crawler_chunk_runtime test.test_crawler_tools test.test_crawl_job_events test.test_crawl_job_runtime test.test_crawler_chunking
```

预期：PASS。若出现既有 `aiosqlite ResourceWarning` 或事件循环关闭 warning，但测试结果为 OK，记录为既有警告，不为本计划修复 unrelated warning。

- [ ] **步骤 4：检查 git diff 范围**

运行：

```powershell
git diff -- backend/app/agents/faculty_crawler_agent.py backend/app/services/crawler_chunk_runtime.py backend/app/services/crawler_tools.py backend/app/services/crawl_job_events.py backend/test/test_faculty_crawler_agent.py backend/test/test_crawler_chunk_runtime.py backend/test/test_crawler_tools.py backend/test/test_crawl_job_events.py backend/test/test_crawl_job_runtime.py backend/test/test_crawler_chunking.py
```

预期：diff 只包含统一 page chunk 链路、工具重命名、浏览器 chunk 化、事件过滤和对应测试；不包含无关格式化或重构。

- [ ] **步骤 5：Commit**

```powershell
git add backend/app/agents/faculty_crawler_agent.py backend/app/services/crawler_chunk_runtime.py backend/app/services/crawler_tools.py backend/app/services/crawl_job_events.py backend/test/test_faculty_crawler_agent.py backend/test/test_crawler_chunk_runtime.py backend/test/test_crawler_tools.py backend/test/test_crawl_job_events.py backend/test/test_crawl_job_runtime.py backend/test/test_crawler_chunking.py
git commit -m "test(crawler): verify unified page chunk migration"
```

## 最终验证

- [ ] 运行完整后端测试：

```powershell
cd backend
uv run python -m unittest discover test
```

预期：PASS。若完整测试暴露 unrelated 失败，记录失败测试名、错误摘要和已通过的 crawler 定向测试，不在本计划中扩展修复范围。

- [ ] 运行旧名称最终扫描：

```powershell
rg "submit_chunk_candidates|save_professor_candidates" backend/app
```

预期：无输出。

- [ ] 运行新工具链扫描：

```powershell
rg "submit_page_chunk_candidates|source_kind=\"page_chunk\"|page_chunk" backend/app backend/test
```

预期：新工具名出现在 Agent、chunk runtime、事件摘要和测试中；`page_chunk` 出现在新候选保存路径和兼容测试中。

## 自检

- 规格覆盖度：任务 1 覆盖四工具列表；任务 2 覆盖工具重命名和 `page_chunk` 写入；任务 3 覆盖 merge 与历史 `list_chunk` 兼容；任务 4 覆盖浏览器正文 chunk 化；任务 5 覆盖旧保存入口移除；任务 6 覆盖 prompt、工具描述和上下文压缩；任务 7 覆盖事件摘要；任务 8 覆盖条件拆分与端到端序列；任务 9 覆盖残留扫描和回归。
- 占位符扫描：本文没有使用空泛占位语作为任务内容；每个任务都有具体文件、测试、命令、实现方向和 commit 命令。
- 类型一致性：计划中统一使用 `submit_page_chunk_candidates`、`has_unsubmitted_candidates_in_current_chunk`、`source_kind="page_chunk"`、`claim_next_page_chunk`；历史 `list_chunk` 只作为兼容来源出现。

## 执行交接

计划已完成并保存到 `docs/superpowers/plans/2026-05-26-unified-page-chunk-crawler.md`。两种执行方式：

**1. 子代理驱动（推荐）** - 每个任务调度一个新的子代理，任务间进行审查，快速迭代。

**2. 内联执行** - 在当前会话中使用 executing-plans 执行任务，批量执行并设有检查点。


