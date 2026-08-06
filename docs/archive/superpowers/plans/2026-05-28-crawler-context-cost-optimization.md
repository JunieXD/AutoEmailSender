# 智能抓取上下文成本优化 实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 为智能抓取增加短链 Agent 运行边界，减少已处理 chunk 正文在 DeepAgents 多轮上下文中的长期累积，从而降低 DeepSeek 调用成本。

**架构：** 在后端配置层新增单次 Agent 运行的 chunk 上限和工具调用上限；在 `run_faculty_crawler_agent()` 内统计工具调用和已完成 chunk 数，达到边界时让本轮 Agent 停止；由现有 `_complete_running_job()` 根据数据库待处理 chunk 将任务重新排队。Prompt 只做结构稳定化，不拆多 Agent，不改变候选保存业务语义。

**技术栈：** Python 3、FastAPI、SQLAlchemy ORM、unittest、DeepAgents、LangChain、uv。

---

## 规格来源

- 设计规格：`docs/superpowers/specs/2026-05-28-crawler-context-cost-optimization-design.md`
- DeepSeek 参考：上下文硬盘缓存按完整输入前缀命中，cached input 仍有成本，因此本计划优先切断无价值历史。

## 文件结构

- 修改：`backend/app/models/app_setting.py`
  - 为 `app_settings` 增加 `crawler_agent_max_chunks_per_run` 和 `crawler_agent_max_tool_calls_per_run` 默认值。
- 修改：`backend/app/schemas/runtime_settings.py`
  - 在运行时设置读写 DTO 中暴露两个新配置，并设置范围校验。
- 修改：`backend/app/services/runtime_settings.py`
  - 序列化新配置，保证 API 读写一致。
- 修改：`backend/app/agents/faculty_crawler_agent.py`
  - 增加 Agent 运行预算类型、工具调用计数、完成 chunk 计数、边界事件和 Prompt 结构稳定化。
- 修改：`backend/app/services/crawl_job_runtime.py`
  - 从 `AppSetting` 读取运行边界并传给 `run_faculty_crawler_agent()`。
- 修改：`backend/test/test_database_schema.py`
  - 验证新设置字段存在。
- 修改：`backend/test/test_runtime_settings_api.py`
  - 验证运行时设置 API 返回、更新和校验新字段。
- 修改：`backend/test/test_faculty_crawler_agent.py`
  - 覆盖 Agent 运行预算、边界事件、Prompt 稳定结构。
- 修改：`backend/test/test_crawl_job_runtime.py`
  - 覆盖运行时设置会传递到 Agent，边界结束后仍可由 pending chunk 重新排队。

---

### 任务 1：新增运行边界配置字段

**文件：**
- 修改：`backend/app/models/app_setting.py`
- 修改：`backend/app/schemas/runtime_settings.py`
- 修改：`backend/app/services/runtime_settings.py`
- 修改：`backend/test/test_database_schema.py`
- 修改：`backend/test/test_runtime_settings_api.py`

- [ ] **步骤 1：编写数据库字段失败测试**

在 `backend/test/test_database_schema.py` 的 settings 字段断言集合中加入两个字段：

```python
{
    "match_analysis_job_worker_count",
    "match_analysis_job_item_concurrency",
    "match_analysis_job_interval_seconds",
    "crawler_worker_count",
    "crawler_profile_enrichment_concurrency",
    "crawler_host_concurrency",
    "crawler_agent_max_chunks_per_run",
    "crawler_agent_max_tool_calls_per_run",
    "draft_max_tokens",
    "batch_draft_generation_concurrency",
    "draft_rewrite_intensity",
    "draft_rewrite_tone",
    "draft_rewrite_formality",
    "draft_rewrite_length",
    "draft_rewrite_specificity",
    "draft_template_preservation",
    "draft_custom_instruction",
}.issubset(settings_columns)
```

- [ ] **步骤 2：运行数据库字段测试验证失败**

运行：

```powershell
cd backend
uv run python -m unittest test.test_database_schema.DatabaseSchemaTests.test_schema_contains_expected_columns
```

预期：FAIL，提示 `crawler_agent_max_chunks_per_run` 或 `crawler_agent_max_tool_calls_per_run` 不在 `settings_columns` 中。

- [ ] **步骤 3：编写运行时设置 API 失败测试**

在 `backend/test/test_runtime_settings_api.py` 中，找到默认 payload 和更新 payload 断言位置，加入以下断言和值。默认读取断言应包含：

```python
self.assertEqual(payload["crawler_agent_max_chunks_per_run"], 2)
self.assertEqual(payload["crawler_agent_max_tool_calls_per_run"], 12)
```

更新 payload 应包含：

```python
"crawler_agent_max_chunks_per_run": 3,
"crawler_agent_max_tool_calls_per_run": 15,
```

更新后响应断言应包含：

```python
self.assertEqual(payload["crawler_agent_max_chunks_per_run"], 3)
self.assertEqual(payload["crawler_agent_max_tool_calls_per_run"], 15)
```

如果该测试文件有无效值校验，增加两个子用例：

```python
("crawler_agent_max_chunks_per_run", 0),
("crawler_agent_max_tool_calls_per_run", 0),
```

- [ ] **步骤 4：运行运行时设置测试验证失败**

运行：

```powershell
cd backend
uv run python -m unittest test.test_runtime_settings_api
```

预期：FAIL，提示响应缺少新字段或 Pydantic schema 不接受新字段。

- [ ] **步骤 5：实现 AppSetting 字段**

在 `backend/app/models/app_setting.py` 的 crawler 设置字段附近加入：

```python
    crawler_agent_max_chunks_per_run: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        server_default=text("2"),
    )
    crawler_agent_max_tool_calls_per_run: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        server_default=text("12"),
    )
```

放置建议：紧跟 `crawler_host_concurrency` 后面，保持抓取相关配置聚合。

- [ ] **步骤 6：实现 RuntimeSettings DTO 字段**

在 `backend/app/schemas/runtime_settings.py` 的 `RuntimeSettingsRead` 加入：

```python
    crawler_agent_max_chunks_per_run: int
    crawler_agent_max_tool_calls_per_run: int
```

在 `RuntimeSettingsUpdate` 加入：

```python
    crawler_agent_max_chunks_per_run: int = Field(ge=1, le=20)
    crawler_agent_max_tool_calls_per_run: int = Field(ge=1, le=80)
```

- [ ] **步骤 7：实现 runtime_settings 序列化**

在 `backend/app/services/runtime_settings.py` 的 `serialize_runtime_settings()` 中加入：

```python
        crawler_agent_max_chunks_per_run=settings.crawler_agent_max_chunks_per_run,
        crawler_agent_max_tool_calls_per_run=settings.crawler_agent_max_tool_calls_per_run,
```

放在 `crawler_host_concurrency` 后面。

- [ ] **步骤 8：运行配置相关测试验证通过**

运行：

```powershell
cd backend
uv run python -m unittest test.test_database_schema test.test_runtime_settings_api
```

预期：PASS。

- [ ] **步骤 9：Commit**

```powershell
git add backend/app/models/app_setting.py backend/app/schemas/runtime_settings.py backend/app/services/runtime_settings.py backend/test/test_database_schema.py backend/test/test_runtime_settings_api.py
git commit -m "feat(crawler): add agent context budget settings"
```

---

### 任务 2：为抓取 Agent 增加运行预算

**文件：**
- 修改：`backend/app/agents/faculty_crawler_agent.py`
- 修改：`backend/test/test_faculty_crawler_agent.py`

- [ ] **步骤 1：编写预算类型和默认行为失败测试**

在 `backend/test/test_faculty_crawler_agent.py` 中新增测试类：

```python
class FacultyCrawlerAgentBudgetTests(unittest.IsolatedAsyncioTestCase):
    async def test_run_agent_passes_no_budget_by_default(self) -> None:
        captured: dict[str, object] = {}

        class FakeAgent:
            async def astream(self, input_payload: dict[str, object], **kwargs: object):
                captured["input_payload"] = input_payload
                captured["kwargs"] = kwargs
                yield {"event": "done"}

        ctx = CrawlToolContext(
            job_id=1,
            start_url="https://cs.example.edu/faculty",
            university="示例大学",
            school="计算机学院",
            session_factory=object(),  # type: ignore[arg-type]
        )
        profile = LLMProfile(name="test", provider="openai", api_key="sk-test", model_name="gpt-test")

        with (
            patch("app.agents.faculty_crawler_agent.create_faculty_crawler_agent", return_value=FakeAgent()),
            patch("app.agents.faculty_crawler_agent.crawl_job_has_pending_work", AsyncMock(return_value=False)),
            patch("app.agents.faculty_crawler_agent._ensure_agent_job_can_continue", AsyncMock()),
        ):
            result = await run_faculty_crawler_agent(ctx, profile)

        self.assertEqual(result, {"event": "done"})
        self.assertIn("请从入口页面开始抓取候选导师", str(captured["input_payload"]))
```

- [ ] **步骤 2：运行默认行为测试验证失败**

运行：

```powershell
cd backend
uv run python -m unittest test.test_faculty_crawler_agent.FacultyCrawlerAgentBudgetTests.test_run_agent_passes_no_budget_by_default
```

预期：FAIL，原因通常是测试类引用的新预算相关实现尚不存在，或现有导入缺少 `IsolatedAsyncioTestCase` 支持的依赖调整。

- [ ] **步骤 3：新增预算数据结构**

在 `backend/app/agents/faculty_crawler_agent.py` 顶部 import 中加入：

```python
from dataclasses import dataclass
```

在常量附近加入：

```python
@dataclass(slots=True, frozen=True)
class CrawlerAgentRunBudget:
    max_completed_chunks: int | None = None
    max_tool_calls: int | None = None

    def has_limits(self) -> bool:
        return self.max_completed_chunks is not None or self.max_tool_calls is not None
```

- [ ] **步骤 4：更新 run_faculty_crawler_agent 签名**

将函数签名改为：

```python
async def run_faculty_crawler_agent(
    ctx: CrawlToolContext,
    llm_profile: LLMProfile,
    trace_callback: TraceCallback | None = None,
    *,
    extra_body: dict[str, object] | None = None,
    run_budget: CrawlerAgentRunBudget | None = None,
) -> Any:
```

保持默认 `None` 时行为不变。

- [ ] **步骤 5：运行默认行为测试验证通过**

运行：

```powershell
cd backend
uv run python -m unittest test.test_faculty_crawler_agent.FacultyCrawlerAgentBudgetTests.test_run_agent_passes_no_budget_by_default
```

预期：PASS。

- [ ] **步骤 6：编写工具调用上限失败测试**

在同一测试类加入：

```python
    async def test_run_agent_stops_after_tool_call_budget_event(self) -> None:
        events: list[dict[str, object]] = []

        class FakeAgent:
            async def astream(self, input_payload: dict[str, object], **kwargs: object):
                _ = input_payload, kwargs
                yield {
                    "event": "on_tool_start",
                    "name": "claim_next_page_chunk",
                    "data": {"input": {}},
                }
                yield {
                    "event": "on_tool_start",
                    "name": "submit_page_chunk_candidates",
                    "data": {"input": {}},
                }
                yield {"event": "should_not_be_seen"}

        async def trace_callback(event: object) -> None:
            events.append(event)  # type: ignore[arg-type]

        ctx = CrawlToolContext(
            job_id=1,
            start_url="https://cs.example.edu/faculty",
            university="示例大学",
            school="计算机学院",
            session_factory=object(),  # type: ignore[arg-type]
        )
        profile = LLMProfile(name="test", provider="openai", api_key="sk-test", model_name="gpt-test")

        with (
            patch("app.agents.faculty_crawler_agent.create_faculty_crawler_agent", return_value=FakeAgent()),
            patch("app.agents.faculty_crawler_agent.crawl_job_has_pending_work", AsyncMock(return_value=True)),
            patch("app.agents.faculty_crawler_agent._ensure_agent_job_can_continue", AsyncMock()),
        ):
            result = await run_faculty_crawler_agent(
                ctx,
                profile,
                trace_callback=trace_callback,
                run_budget=CrawlerAgentRunBudget(max_tool_calls=2),
            )

        self.assertEqual(result["event_type"], "agent_context_budget_reached")
        self.assertEqual(result["reason"], "max_tool_calls")
        self.assertEqual(result["tool_calls"], 2)
        self.assertTrue(any(event.get("event_type") == "agent_context_budget_reached" for event in events))
        self.assertFalse(any(event.get("event") == "should_not_be_seen" for event in events))
```

同时把测试文件 import 更新为：

```python
from app.agents.faculty_crawler_agent import (
    CONTROLLED_CRAWLER_TOOL_NAMES,
    CrawlerAgentRunBudget,
    FACULTY_CRAWLER_SYSTEM_PROMPT,
    ...
)
```

- [ ] **步骤 7：运行工具调用预算测试验证失败**

运行：

```powershell
cd backend
uv run python -m unittest test.test_faculty_crawler_agent.FacultyCrawlerAgentBudgetTests.test_run_agent_stops_after_tool_call_budget_event
```

预期：FAIL，原因是尚未实现事件计数和提前停止。

- [ ] **步骤 8：实现工具调用计数和预算事件**

在 `run_faculty_crawler_agent()` 的 `async for event in agent.astream(...)` 循环前加入：

```python
    tool_calls = 0
    completed_chunks = 0
```

在循环内、`last_event = event` 后加入逻辑：

```python
        trace_event = build_trace_event(event)
        if _is_controlled_tool_start_event(trace_event):
            tool_calls += 1
        if trace_callback is not None:
            result = trace_callback(trace_event)
            if inspect.isawaitable(result):
                await result
        budget_event = _build_budget_reached_event(
            run_budget,
            tool_calls=tool_calls,
            completed_chunks=completed_chunks,
        )
        if budget_event is not None:
            if trace_callback is not None:
                result = trace_callback(budget_event)
                if inspect.isawaitable(result):
                    await result
            return budget_event
```

同时删除原循环中重复的 `trace_callback(build_trace_event(event))` 调用，避免同一事件记录两次。

新增辅助函数：

```python
def _is_controlled_tool_start_event(event: dict[str, object]) -> bool:
    event_name = str(event.get("event") or event.get("event_type") or "")
    tool_name = str(event.get("name") or "")
    return event_name == "on_tool_start" and tool_name in CONTROLLED_CRAWLER_TOOL_NAMES


def _build_budget_reached_event(
    run_budget: CrawlerAgentRunBudget | None,
    *,
    tool_calls: int,
    completed_chunks: int,
) -> dict[str, object] | None:
    if run_budget is None:
        return None
    if run_budget.max_completed_chunks is not None and completed_chunks >= run_budget.max_completed_chunks:
        return {
            "event_type": "agent_context_budget_reached",
            "reason": "max_completed_chunks",
            "completed_chunks": completed_chunks,
            "tool_calls": tool_calls,
            "message": "本轮 Agent 已达到 chunk 处理上限，将结束当前短链运行并由任务调度继续。",
        }
    if run_budget.max_tool_calls is not None and tool_calls >= run_budget.max_tool_calls:
        return {
            "event_type": "agent_context_budget_reached",
            "reason": "max_tool_calls",
            "completed_chunks": completed_chunks,
            "tool_calls": tool_calls,
            "message": "本轮 Agent 已达到工具调用上限，将结束当前短链运行并由任务调度继续。",
        }
    return None
```

- [ ] **步骤 9：运行工具调用预算测试验证通过**

运行：

```powershell
cd backend
uv run python -m unittest test.test_faculty_crawler_agent.FacultyCrawlerAgentBudgetTests.test_run_agent_stops_after_tool_call_budget_event
```

预期：PASS。

- [ ] **步骤 10：Commit**

```powershell
git add backend/app/agents/faculty_crawler_agent.py backend/test/test_faculty_crawler_agent.py
git commit -m "feat(crawler): add agent run budget guard"
```

---

### 任务 3：按完成 chunk 数切断 Agent 历史

**文件：**
- 修改：`backend/app/agents/faculty_crawler_agent.py`
- 修改：`backend/test/test_faculty_crawler_agent.py`

- [ ] **步骤 1：编写完成 chunk 计数失败测试**

在 `FacultyCrawlerAgentBudgetTests` 中加入：

```python
    async def test_run_agent_stops_after_completed_chunk_budget_event(self) -> None:
        events: list[dict[str, object]] = []

        class FakeAgent:
            async def astream(self, input_payload: dict[str, object], **kwargs: object):
                _ = input_payload, kwargs
                yield {
                    "event": "on_tool_end",
                    "name": "submit_page_chunk_candidates",
                    "data": {
                        "output": {
                            "chunk_status": "completed",
                            "saved_count": 2,
                        }
                    },
                }
                yield {"event": "should_not_be_seen"}

        async def trace_callback(event: object) -> None:
            events.append(event)  # type: ignore[arg-type]

        ctx = CrawlToolContext(
            job_id=1,
            start_url="https://cs.example.edu/faculty",
            university="示例大学",
            school="计算机学院",
            session_factory=object(),  # type: ignore[arg-type]
        )
        profile = LLMProfile(name="test", provider="openai", api_key="sk-test", model_name="gpt-test")

        with (
            patch("app.agents.faculty_crawler_agent.create_faculty_crawler_agent", return_value=FakeAgent()),
            patch("app.agents.faculty_crawler_agent.crawl_job_has_pending_work", AsyncMock(return_value=True)),
            patch("app.agents.faculty_crawler_agent._ensure_agent_job_can_continue", AsyncMock()),
        ):
            result = await run_faculty_crawler_agent(
                ctx,
                profile,
                trace_callback=trace_callback,
                run_budget=CrawlerAgentRunBudget(max_completed_chunks=1),
            )

        self.assertEqual(result["event_type"], "agent_context_budget_reached")
        self.assertEqual(result["reason"], "max_completed_chunks")
        self.assertEqual(result["completed_chunks"], 1)
        self.assertTrue(any(event.get("event_type") == "agent_context_budget_reached" for event in events))
        self.assertFalse(any(event.get("event") == "should_not_be_seen" for event in events))
```

- [ ] **步骤 2：运行完成 chunk 预算测试验证失败**

运行：

```powershell
cd backend
uv run python -m unittest test.test_faculty_crawler_agent.FacultyCrawlerAgentBudgetTests.test_run_agent_stops_after_completed_chunk_budget_event
```

预期：FAIL，原因是尚未从工具结束事件识别 completed chunk。

- [ ] **步骤 3：实现 completed chunk 识别**

在 `backend/app/agents/faculty_crawler_agent.py` 中新增辅助函数：

```python
def _is_completed_chunk_submit_event(event: dict[str, object]) -> bool:
    event_name = str(event.get("event") or event.get("event_type") or "")
    tool_name = str(event.get("name") or "")
    if event_name != "on_tool_end" or tool_name != "submit_page_chunk_candidates":
        return False
    data = event.get("data")
    output: object = None
    if isinstance(data, dict):
        output = data.get("output")
    if not isinstance(output, dict):
        return False
    return output.get("chunk_status") in {"completed", "no_candidates"}
```

在 `run_faculty_crawler_agent()` 循环中，`tool_calls` 计数后加入：

```python
        if _is_completed_chunk_submit_event(trace_event):
            completed_chunks += 1
```

- [ ] **步骤 4：运行完成 chunk 预算测试验证通过**

运行：

```powershell
cd backend
uv run python -m unittest test.test_faculty_crawler_agent.FacultyCrawlerAgentBudgetTests.test_run_agent_stops_after_completed_chunk_budget_event
```

预期：PASS。

- [ ] **步骤 5：补充 no_candidates 计数测试**

新增一个小测试，直接测辅助函数，避免只覆盖 completed：

```python
    def test_no_candidates_counts_as_completed_chunk(self) -> None:
        from app.agents.faculty_crawler_agent import _is_completed_chunk_submit_event

        self.assertTrue(
            _is_completed_chunk_submit_event(
                {
                    "event": "on_tool_end",
                    "name": "submit_page_chunk_candidates",
                    "data": {"output": {"chunk_status": "no_candidates"}},
                }
            )
        )
```

- [ ] **步骤 6：运行 Agent 预算测试类验证通过**

运行：

```powershell
cd backend
uv run python -m unittest test.test_faculty_crawler_agent.FacultyCrawlerAgentBudgetTests
```

预期：PASS。

- [ ] **步骤 7：Commit**

```powershell
git add backend/app/agents/faculty_crawler_agent.py backend/test/test_faculty_crawler_agent.py
git commit -m "feat(crawler): stop agent after chunk budget"
```

---

### 任务 4：从运行时设置传递 Agent 预算

**文件：**
- 修改：`backend/app/services/crawl_job_runtime.py`
- 修改：`backend/test/test_crawl_job_runtime.py`

- [ ] **步骤 1：编写预算传递失败测试**

在 `backend/test/test_crawl_job_runtime.py` 的 `CrawlJobRuntimeTests` 中加入：

```python
    async def test_run_queued_crawl_job_passes_agent_budget_from_settings(self) -> None:
        job_id = await self._create_default_profile_and_job()
        captured: dict[str, object] = {}

        async def fake_run_agent(
            ctx: CrawlToolContext,
            llm_profile: LLMProfile,
            trace_callback=None,
            **kwargs: object,
        ) -> dict[str, object]:
            _ = ctx, llm_profile, trace_callback
            captured.update(kwargs)
            return {"event_type": "agent_context_budget_reached", "reason": "max_tool_calls"}

        with (
            patch("app.services.crawl_job_runtime.run_faculty_crawler_agent", AsyncMock(side_effect=fake_run_agent)),
            patch("app.services.crawl_job_runtime.ensure_thinking_adaptation", AsyncMock(return_value=None)),
        ):
            await run_queued_crawl_jobs_once(self.session_factory)

        run_budget = captured.get("run_budget")
        self.assertIsNotNone(run_budget)
        self.assertEqual(run_budget.max_completed_chunks, 2)
        self.assertEqual(run_budget.max_tool_calls, 12)
```

如果 `_create_default_profile_and_job()` 不存在或签名不同，使用该测试文件已有创建默认 profile/job 的 helper，保持 job 类型为非 `profile`，状态为 `queued`。

- [ ] **步骤 2：运行预算传递测试验证失败**

运行：

```powershell
cd backend
uv run python -m unittest test.test_crawl_job_runtime.CrawlJobRuntimeTests.test_run_queued_crawl_job_passes_agent_budget_from_settings
```

预期：FAIL，原因是 `run_budget` 尚未传入。

- [ ] **步骤 3：导入预算类型**

在 `backend/app/services/crawl_job_runtime.py` 的 Agent import 中加入：

```python
from app.agents.faculty_crawler_agent import CrawlerAgentRunBudget, run_faculty_crawler_agent
```

如果当前 import 已有 `run_faculty_crawler_agent`，改为同一行导入。

- [ ] **步骤 4：读取设置并构造预算**

在 `run_queued_crawl_jobs_once()` 已读取 `settings` 的位置，构造：

```python
        agent_run_budget = CrawlerAgentRunBudget(
            max_completed_chunks=max(1, settings.crawler_agent_max_chunks_per_run),
            max_tool_calls=max(1, settings.crawler_agent_max_tool_calls_per_run),
        )
```

如果作用域中已有运行时并发配置对象，保持预算对象在进入 start_urls 循环前创建一次。

- [ ] **步骤 5：传递预算到 Agent**

将调用改为：

```python
                    await run_faculty_crawler_agent(
                        entry_ctx,
                        llm_profile,
                        trace_callback=trace_callback,
                        extra_body=entry_ctx.thinking_extra_body,
                        run_budget=agent_run_budget,
                    )
```

- [ ] **步骤 6：运行预算传递测试验证通过**

运行：

```powershell
cd backend
uv run python -m unittest test.test_crawl_job_runtime.CrawlJobRuntimeTests.test_run_queued_crawl_job_passes_agent_budget_from_settings
```

预期：PASS。

- [ ] **步骤 7：验证 pending chunk 仍会重新排队**

运行现有测试：

```powershell
cd backend
uv run python -m unittest test.test_crawl_job_runtime.CrawlJobRuntimeTests.test_run_requeues_job_when_chunks_remain_after_candidate_save
```

预期：PASS，证明短链结束后只要仍有 pending chunk，`_complete_running_job()` 会把任务设回 queued。

- [ ] **步骤 8：Commit**

```powershell
git add backend/app/services/crawl_job_runtime.py backend/test/test_crawl_job_runtime.py
git commit -m "feat(crawler): apply agent context budget settings"
```

---

### 任务 5：稳定化抓取 Agent Prompt 结构

**文件：**
- 修改：`backend/app/agents/faculty_crawler_agent.py`
- 修改：`backend/test/test_faculty_crawler_agent.py`

- [ ] **步骤 1：编写 Prompt 结构测试**

在 `FacultyCrawlerAgentPromptTests` 中加入：

```python
    def test_system_prompt_keeps_stable_sections_before_dynamic_input(self) -> None:
        prompt = FACULTY_CRAWLER_SYSTEM_PROMPT
        expected_headings = [
            "角色与目标：",
            "页面与 chunk 处理流程：",
            "工具使用边界：",
            "候选字段与提交约束：",
            "禁止事项与错误恢复：",
        ]
        positions = [prompt.index(heading) for heading in expected_headings]
        self.assertEqual(positions, sorted(positions))
        self.assertNotIn("入口 URL", prompt)
        self.assertNotIn("学校:", prompt)
        self.assertNotIn("学院/单位:", prompt)
```

- [ ] **步骤 2：运行 Prompt 结构测试验证失败**

运行：

```powershell
cd backend
uv run python -m unittest test.test_faculty_crawler_agent.FacultyCrawlerAgentPromptTests.test_system_prompt_keeps_stable_sections_before_dynamic_input
```

预期：FAIL，原因是当前 Prompt 还没有这些稳定章节标题。

- [ ] **步骤 3：重排系统 Prompt**

在 `backend/app/agents/faculty_crawler_agent.py` 中重写 `FACULTY_CRAWLER_SYSTEM_PROMPT`，保留现有业务约束，整理为以下结构：

```python
FACULTY_CRAWLER_SYSTEM_PROMPT = """你是 AutoEmailSender 的受控高校导师信息抓取代理。

角色与目标：
- 从给定入口页面及其同域页面中识别潜在教授/导师候选人。
- 优先提取姓名、邮箱、职称、院系、研究方向、近期论文、主页 URL、证据和置信度。
- 当页面内容不足时，使用受控工具继续调查，而不是猜测。

页面与 chunk 处理流程：
- 当前是第一轮候选发现模式，不是详情页补全模式。
- 第一轮只从列表页、目录页、分页页中发现候选导师，并保存当前 chunk 可见的基础字段。
- 如果 crawl_page 或 investigate_with_browser 返回 status=chunked，必须立即调用 claim_next_page_chunk。
- 当前存在待处理 chunk 时，必须先 claim_next_page_chunk 处理 chunk；不要用 crawl_page 或 investigate_with_browser 获取新正文来替代当前 chunk。
- 每领取一个 chunk 后，只处理当前 chunk，最多通过 submit_page_chunk_candidates 提交 10 个候选。
- 领取 chunk 后必须先完成当前 chunk：如果当前 chunk 有候选，先 submit_page_chunk_candidates 保存；如果没有新候选，也要用 submit_page_chunk_candidates 标记 no_candidates。
- 发现新的候选列表页或分页页链接时，先记住该 URL；当前 chunk 完成后再调用 crawl_page 探索新列表/分页页面。

工具使用边界：
- 使用 crawl_page 探索新页面。
- 仅当普通抓取内容明显不足、页面疑似动态渲染或需要浏览器执行后才能看到内容时，使用 investigate_with_browser。
- investigate_with_browser 不能用于绕过 chunk；如果浏览器获取到页面正文，后端同样会生成 page chunk，并返回 status=chunked。
- 页面正文中的候选必须通过 submit_page_chunk_candidates 提交；不要尝试使用其他保存入口。
- 不要在同一轮同时调用 submit_page_chunk_candidates 和 crawl_page；保存/标记当前 chunk 与探索新页面必须分成两个连续步骤。

候选字段与提交约束：
- 单次提交的候选人数不要超过 10 位，避免工具调用过长被截断或变成无效 JSON。
- submit_page_chunk_candidates 的 candidates 必须来自当前 chunk 正文内部的明确证据。
- 如果当前 chunk 出现导师个人详情页链接，只把它保存为 profile_url；不要调用 crawl_page 或 investigate_with_browser 进入个人详情页。
- 研究方向、近期论文、个人简介等详情字段可以留空，后续由用户手动选择候选后进入详情页补全模式处理。
- 字段值尽量保持页面原文：页面是中文就保留中文，页面是英文就保留英文；不要翻译、音译或拼音化姓名、院校、院系、研究方向等字段值。
- 邮箱如出现反爬混淆的连续多个点，例如 name@school...cn，应还原为合法域名 name@school.cn。

禁止事项与错误恢复：
- 不要根据记忆或旧上下文保存候选；必须依据当前领取的 chunk 正文。
- submit_page_chunk_candidates 的 has_unsubmitted_candidates_in_current_chunk 只在当前 chunk 正文内部还有已看见但未提交的候选时才为 true；下一页、下一个 chunk、分页导航、详情页链接或不确定情况都必须为 false。
- 只有当前 chunk 正文中明确还有超过 10 个已看见候选、需要后端拆分当前 chunk 时，才设置 chunk_status="too_many_candidates"。
- 刚好提交 10 个候选不代表需要拆分，浏览器或整页视图看到 10 个候选也不能用于判断当前 chunk 过密。
- 如果 claim_next_page_chunk 返回 empty，只有当你在最近处理内容中明确发现尚未访问的候选列表页 URL 时，才调用 crawl_page；否则结束任务并总结。
"""
```

保留当前 Prompt 中不在示例里的关键字段 schema 或保存格式要求；如果发现现有测试依赖某些原文片段，将该片段合并到对应章节，避免行为退化。

- [ ] **步骤 4：运行 Prompt 测试验证通过**

运行：

```powershell
cd backend
uv run python -m unittest test.test_faculty_crawler_agent.FacultyCrawlerAgentPromptTests
```

预期：PASS。

- [ ] **步骤 5：Commit**

```powershell
git add backend/app/agents/faculty_crawler_agent.py backend/test/test_faculty_crawler_agent.py
git commit -m "refactor(crawler): stabilize agent prompt sections"
```

---

### 任务 6：验证工具返回保持轻量

**文件：**
- 修改：`backend/test/test_faculty_crawler_agent.py`
- 可选修改：`backend/app/agents/faculty_crawler_agent.py`
- 可选修改：`backend/app/services/crawler_chunk_runtime.py`

- [ ] **步骤 1：补充保存结果不回显候选正文测试**

在 `FacultyCrawlerAgentSaveResultTests` 中新增：

```python
    def test_format_save_batch_result_for_model_excludes_candidate_payload_echo(self) -> None:
        result = _format_save_batch_result_for_model(
            {
                "batch_status": "saved",
                "attempted_count": 1,
                "saved_count": 1,
                "merged_count": 0,
                "skipped_duplicate_count": 0,
                "rejected_count": 0,
                "failed_count": 0,
                "failed_items": [],
                "rejected_items": [],
                "total_saved_count": 1,
                "candidates": [
                    {
                        "name": "张三",
                        "profile_url": "https://example.edu/zhang",
                        "evidence": "很长的页面证据正文",
                    }
                ],
            }
        )

        serialized = str(result)
        self.assertNotIn("candidates", result)
        self.assertNotIn("很长的页面证据正文", serialized)
        self.assertNotIn("https://example.edu/zhang", serialized)
```

- [ ] **步骤 2：运行轻量返回测试**

运行：

```powershell
cd backend
uv run python -m unittest test.test_faculty_crawler_agent.FacultyCrawlerAgentSaveResultTests.test_format_save_batch_result_for_model_excludes_candidate_payload_echo
```

预期：如果当前实现已满足，PASS；如果 FAIL，继续步骤 3。

- [ ] **步骤 3：收敛保存结果格式化白名单**

如果步骤 2 失败，在 `_format_save_batch_result_for_model()` 中使用白名单字段构造返回值：

```python
allowed_keys = {
    "batch_status",
    "attempted_count",
    "saved_count",
    "merged_count",
    "skipped_duplicate_count",
    "rejected_count",
    "failed_count",
    "failed_items",
    "rejected_items",
    "total_saved_count",
    "retry_allowed",
    "failure_fingerprint",
    "consecutive_same_batch_failures",
    "total_save_failures",
    "terminal_reason",
    "next_instruction",
    "chunk_status",
    "warning",
}
return {key: value for key, value in result.items() if key in allowed_keys}
```

确保不丢失现有测试断言使用的预算元数据和错误恢复指令。

- [ ] **步骤 4：运行相关测试验证通过**

运行：

```powershell
cd backend
uv run python -m unittest test.test_faculty_crawler_agent.FacultyCrawlerAgentSaveResultTests
```

预期：PASS。

- [ ] **步骤 5：Commit**

如果只有测试新增且现有实现已满足：

```powershell
git add backend/test/test_faculty_crawler_agent.py
git commit -m "test(crawler): lock compact tool result format"
```

如果修改了实现：

```powershell
git add backend/app/agents/faculty_crawler_agent.py backend/test/test_faculty_crawler_agent.py
git commit -m "fix(crawler): keep tool results compact"
```

---

### 任务 7：后端回归验证和文档同步

**文件：**
- 修改：`docs/superpowers/specs/2026-05-28-crawler-context-cost-optimization-design.md`
- 可选修改：`docs/database_table_design.md`

- [ ] **步骤 1：运行聚焦后端测试**

运行：

```powershell
cd backend
uv run python -m unittest test.test_faculty_crawler_agent test.test_crawl_job_runtime test.test_runtime_settings_api test.test_database_schema
```

预期：PASS。

- [ ] **步骤 2：运行配置测试**

运行：

```powershell
cd backend
uv run python -m unittest test.test_system_settings test.test_config
```

预期：PASS。

- [ ] **步骤 3：检查 diff**

运行：

```powershell
git diff -- backend/app/models/app_setting.py backend/app/schemas/runtime_settings.py backend/app/services/runtime_settings.py backend/app/agents/faculty_crawler_agent.py backend/app/services/crawl_job_runtime.py backend/test/test_database_schema.py backend/test/test_runtime_settings_api.py backend/test/test_faculty_crawler_agent.py backend/test/test_crawl_job_runtime.py
```

预期：diff 只包含运行边界配置、Agent 短链预算、Prompt 稳定化、测试更新。

- [ ] **步骤 4：同步数据库设计文档**

如果 `docs/database_table_design.md` 中记录了 `app_settings` 字段列表，在对应位置加入：

```markdown
| `crawler_agent_max_chunks_per_run` | INTEGER | 单次智能抓取 Agent 运行最多完成的 chunk 数 |
| `crawler_agent_max_tool_calls_per_run` | INTEGER | 单次智能抓取 Agent 运行最多工具调用次数 |
```

如果该文档没有 `app_settings` 字段表，则跳过此步骤，不新增无关章节。

- [ ] **步骤 5：更新规格文档状态**

在 `docs/superpowers/specs/2026-05-28-crawler-context-cost-optimization-design.md` 末尾加入：

```markdown
## 实施计划

实现计划见 `docs/superpowers/plans/2026-05-28-crawler-context-cost-optimization.md`。
```

- [ ] **步骤 6：运行文档占位符扫描**

运行：

```powershell
$redFlags = ('TO' + 'DO'), ('T' + 'BD'), '待' + '定', ('FIX' + 'ME'), ('x' + 'xx')
Select-String -Path docs\superpowers\plans\2026-05-28-crawler-context-cost-optimization.md,docs\superpowers\specs\2026-05-28-crawler-context-cost-optimization-design.md -Pattern ($redFlags -join '|') -CaseSensitive:$false
```

预期：无输出。

- [ ] **步骤 7：最终 Commit**

```powershell
git add docs/superpowers/specs/2026-05-28-crawler-context-cost-optimization-design.md docs/superpowers/plans/2026-05-28-crawler-context-cost-optimization.md docs/database_table_design.md
git commit -m "docs(crawler): plan context cost optimization"
```

如果 `docs/database_table_design.md` 未修改，从 `git add` 中移除该文件。

---

## 实施注意事项

- 不要在第一版中尝试删除 DeepAgents 内部 messages；用短链运行边界切断历史更稳妥。
- 不要把动态信息拼入 `FACULTY_CRAWLER_SYSTEM_PROMPT`；入口 URL、学校、学院仍留在 user prompt。
- 达到预算边界时不要把 crawl job 标记为失败；让现有 `_complete_running_job()` 根据 pending chunk 重新排队。
- `max_completed_chunks` 的默认值 2 是成本优先的折中；后续可以根据 token 统计调整。
- `max_tool_calls` 必须大于完成一个 chunk 的最小工具链，默认 12 避免过早截断正常流程。
- 如果某个测试 helper 名称和本计划示例不一致，优先复用同文件现有 helper，但保持断言语义不变。

## 总体验证命令

```powershell
cd backend
uv run python -m unittest test.test_faculty_crawler_agent test.test_crawl_job_runtime test.test_runtime_settings_api test.test_database_schema test.test_system_settings test.test_config
```

预期：全部 PASS。
