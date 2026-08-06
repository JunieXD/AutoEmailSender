# 智能抓取保存上下文压缩实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 降低智能抓取候选保存阶段的 token 消耗，同时保留小批保存的失败隔离能力。

**架构：** 保存工具继续允许小批提交，但返回轻量批次统计，不再返回成功候选明细。新增模型调用前上下文压缩 middleware，在每次发起模型请求前将已完成的 `save_professor_candidates` 调用和对应工具结果成对移除，并注入一条稳定的保存进度摘要；不修改 LangGraph 原始状态，降低破坏 agent 执行流的风险。

**技术栈：** FastAPI 后端、DeepAgents/LangChain middleware、SQLAlchemy async、unittest。

---

## 文件结构

- 修改：`backend/app/agents/faculty_crawler_agent.py`
  - 新增保存结果数据结构。
  - 新增保存上下文压缩 helper 和 middleware。
  - 调整 `save_professor_candidates` 返回值。
  - 将新 middleware 加入 `create_deep_agent`。
- 修改：`backend/app/services/crawler_tools.py`
  - 新增原子批次校验保存函数，批次中有任何候选校验失败或业务失败时整批不入库。
  - 返回批次统计、失败项、当前 job 总保存数。
- 修改：`backend/app/services/crawl_job_runtime.py`
  - 移除一阶段结束后自动执行 `_enrich_saved_candidates`。
- 修改：`backend/test/test_crawler_tools.py`
  - 覆盖批次保存成功、失败整批回滚、总保存数。
- 修改：`backend/test/test_faculty_crawler_agent.py`
  - 新建或扩展 agent 单元测试，覆盖保存工具返回轻量结果和上下文压缩。
- 修改：`backend/test/test_crawl_job_runtime.py`
  - 覆盖一阶段结束后不自动补全。

## 压缩策略

最稳的压缩边界是 `wrap_model_call` / `awrap_model_call`：

- 每次模型调用前，从 `request.messages` 计算一个“压缩后的视图”。
- 找到所有已完成的 `save_professor_candidates` 交互：
  - assistant message 中包含该工具调用。
  - 后续 tool message 的 `tool_call_id` 与该调用匹配。
- 对这些成对消息做处理：
  - 删除旧的成功保存调用参数，避免 10 条候选 JSON 反复进入上下文。
  - 删除旧工具返回，避免保存结果累计。
  - 保留最近一个失败批次的失败摘要，方便模型修正同一批。
  - 注入一条 `HumanMessage` 摘要，内容只包含总保存数、最近批次状态、失败候选名和失败原因。
- 不压缩 `crawl_page` / `investigate_with_browser` 的页面结果，避免模型失去继续从页面提取候选所需的原始名单。后续如果页面快照仍然过大，再单独做页面摘要或页面快照引用。
- 不直接修改 LangGraph state，只通过 `request.override(messages=compressed_messages)` 改写当前模型请求。

## 任务 1：保存工具返回轻量批次结果

**文件：**
- 修改：`backend/app/agents/faculty_crawler_agent.py`
- 修改：`backend/app/services/crawler_tools.py`
- 测试：`backend/test/test_crawler_tools.py`

- [ ] **步骤 1：编写失败测试，确认成功批次返回统计而不是候选明细**

在 `backend/test/test_crawler_tools.py` 增加：

```python
async def test_save_candidate_batch_returns_counts_without_candidate_details(self) -> None:
    async with _RealCrawlerSessionHarness() as harness:
        ctx = harness.context()
        result = await save_candidate_batch(
            ctx,
            [
                ProfessorCandidatePayload(name="张三", email="zhang@example.edu"),
                ProfessorCandidatePayload(name="李四", email="li@example.edu"),
            ],
        )

        self.assertEqual(result["batch_status"], "saved")
        self.assertEqual(result["attempted_count"], 2)
        self.assertEqual(result["saved_count"], 2)
        self.assertEqual(result["failed_count"], 0)
        self.assertEqual(result["failed_items"], [])
        self.assertEqual(result["total_saved_count"], 2)
        self.assertNotIn("candidates", result)
```

- [ ] **步骤 2：运行测试验证失败**

运行：`cd backend && uv run python -m unittest test.test_crawler_tools.CrawlerSaveCandidateTests.test_save_candidate_batch_returns_counts_without_candidate_details`

预期：FAIL，报错包含 `NameError` 或 `ImportError: cannot import name 'save_candidate_batch'`。

- [ ] **步骤 3：实现批次结果类型和成功返回**

在 `backend/app/services/crawler_tools.py` 中添加：

```python
class CandidateBatchFailure(TypedDict):
    index: int
    name: str | None
    reason: str


class CandidateBatchSaveResult(TypedDict):
    batch_status: Literal["saved", "rejected"]
    attempted_count: int
    saved_count: int
    failed_count: int
    failed_items: list[CandidateBatchFailure]
    total_saved_count: int
```

新增函数：

```python
async def save_candidate_batch(
    ctx: CrawlToolContext,
    candidates: Sequence[ProfessorCandidatePayload],
) -> CandidateBatchSaveResult:
    saved = await save_candidates(ctx, candidates)
    total_saved = await count_saved_candidates(ctx)
    return {
        "batch_status": "saved",
        "attempted_count": len(candidates),
        "saved_count": len(saved),
        "failed_count": 0,
        "failed_items": [],
        "total_saved_count": total_saved,
    }
```

新增计数函数：

```python
async def count_saved_candidates(ctx: CrawlToolContext) -> int:
    async with ctx.session_factory() as session:
        value = await session.scalar(
            select(func.count()).select_from(CrawlCandidate).where(CrawlCandidate.job_id == ctx.job_id)
        )
        return int(value or 0)
```

同时补充必要 import：

```python
from typing import Literal, TypedDict
from sqlalchemy import func, select
```

- [ ] **步骤 4：运行测试验证成功**

运行：`cd backend && uv run python -m unittest test.test_crawler_tools.CrawlerSaveCandidateTests.test_save_candidate_batch_returns_counts_without_candidate_details`

预期：PASS。

- [ ] **步骤 5：编写失败测试，确认批次中有失败时整批回滚**

在 `backend/test/test_crawler_tools.py` 增加：

```python
async def test_save_candidate_batch_rejects_entire_batch_when_one_item_fails(self) -> None:
    async with _RealCrawlerSessionHarness() as harness:
        ctx = harness.context()
        result = await save_candidate_batch(
            ctx,
            [
                ProfessorCandidatePayload(name="张三", email="zhang@example.edu"),
                ProfessorCandidatePayload(name="", email="bad@example.edu"),
            ],
        )

        self.assertEqual(result["batch_status"], "rejected")
        self.assertEqual(result["attempted_count"], 2)
        self.assertEqual(result["saved_count"], 0)
        self.assertEqual(result["failed_count"], 1)
        self.assertEqual(result["failed_items"][0]["index"], 1)
        self.assertIn("name", result["failed_items"][0]["reason"])
        self.assertEqual(result["total_saved_count"], 0)
        self.assertEqual(await self._count_candidates(ctx.job_id), 0)
```

- [ ] **步骤 6：运行测试验证失败**

运行：`cd backend && uv run python -m unittest test.test_crawler_tools.CrawlerSaveCandidateTests.test_save_candidate_batch_rejects_entire_batch_when_one_item_fails`

预期：FAIL，当前实现会在构造 payload 或保存阶段之外报错，或没有整批回滚语义。

- [ ] **步骤 7：实现原子批次校验**

调整 `save_candidate_batch`：

```python
async def save_candidate_batch(
    ctx: CrawlToolContext,
    candidates: Sequence[ProfessorCandidatePayload],
) -> CandidateBatchSaveResult:
    failed_items: list[CandidateBatchFailure] = []
    normalized_payloads: list[dict[str, object]] = []
    for index, candidate in enumerate(candidates):
        try:
            payload = normalize_candidate_payload(
                candidate,
                university=ctx.university,
                school=ctx.school,
            )
        except Exception as exc:
            failed_items.append(
                {
                    "index": index,
                    "name": getattr(candidate, "name", None),
                    "reason": str(exc),
                }
            )
            continue
        if not str(payload.get("name") or "").strip():
            failed_items.append(
                {
                    "index": index,
                    "name": None,
                    "reason": "name 不能为空",
                }
            )
            continue
        normalized_payloads.append(payload)

    if failed_items:
        return {
            "batch_status": "rejected",
            "attempted_count": len(candidates),
            "saved_count": 0,
            "failed_count": len(failed_items),
            "failed_items": failed_items,
            "total_saved_count": await count_saved_candidates(ctx),
        }

    saved = await _save_normalized_candidate_payloads(ctx, normalized_payloads)
    return {
        "batch_status": "saved",
        "attempted_count": len(candidates),
        "saved_count": len(saved),
        "failed_count": 0,
        "failed_items": [],
        "total_saved_count": await count_saved_candidates(ctx),
    }
```

将现有 `save_candidates` 保持兼容，并抽出内部实现：

```python
async def _save_normalized_candidate_payloads(
    ctx: CrawlToolContext,
    payloads: Sequence[dict[str, object]],
) -> list[CrawlCandidate]:
    saved: list[CrawlCandidate] = []
    async with ctx.session_factory() as session:
        if await _is_crawl_job_stopped(session, ctx.job_id):
            return []

        existing_emails = await _load_existing_candidate_emails(session, ctx.job_id)
        seen_emails = set(existing_emails)
        for payload in payloads:
            email = payload["email"]
            if email and str(email).lower() in seen_emails:
                continue

            row = CrawlCandidate(job_id=ctx.job_id, **payload)
            session.add(row)
            saved.append(row)
            if email:
                seen_emails.add(str(email).lower())

        if await _is_crawl_job_stopped(session, ctx.job_id):
            await session.rollback()
            return []

        await session.commit()
        for row in saved:
            await session.refresh(row)
    return saved
```

保留 `save_candidates`：

```python
async def save_candidates(
    ctx: CrawlToolContext,
    candidates: Sequence[ProfessorCandidatePayload],
) -> list[CrawlCandidate]:
    payloads = [
        normalize_candidate_payload(candidate, university=ctx.university, school=ctx.school)
        for candidate in candidates
    ]
    return await _save_normalized_candidate_payloads(ctx, payloads)
```

- [ ] **步骤 8：运行保存工具测试**

运行：`cd backend && uv run python -m unittest test.test_crawler_tools`

预期：PASS。

- [ ] **步骤 9：Commit**

```bash
git add backend/app/services/crawler_tools.py backend/test/test_crawler_tools.py
git commit -m "feat(crawler): return lightweight candidate batch save results"
```

## 任务 2：agent 工具改用轻量保存结果

**文件：**
- 修改：`backend/app/agents/faculty_crawler_agent.py`
- 测试：`backend/test/test_faculty_crawler_agent.py`

- [ ] **步骤 1：编写失败测试，确认工具不会返回候选明细**

创建 `backend/test/test_faculty_crawler_agent.py`，加入：

```python
import unittest
from unittest.mock import AsyncMock, patch

from app.agents.faculty_crawler_agent import _format_save_batch_result_for_model


class FacultyCrawlerAgentSaveResultTests(unittest.TestCase):
    def test_format_save_batch_result_for_model_is_compact(self) -> None:
        result = _format_save_batch_result_for_model(
            {
                "batch_status": "saved",
                "attempted_count": 10,
                "saved_count": 10,
                "failed_count": 0,
                "failed_items": [],
                "total_saved_count": 50,
            }
        )

        self.assertEqual(
            result,
            {
                "batch_status": "saved",
                "attempted_count": 10,
                "saved_count": 10,
                "failed_count": 0,
                "failed_items": [],
                "total_saved_count": 50,
            },
        )
        self.assertNotIn("name", str(result))
        self.assertNotIn("profile_url", str(result))
```

- [ ] **步骤 2：运行测试验证失败**

运行：`cd backend && uv run python -m unittest test.test_faculty_crawler_agent.FacultyCrawlerAgentSaveResultTests.test_format_save_batch_result_for_model_is_compact`

预期：FAIL，报错无法导入 `_format_save_batch_result_for_model`。

- [ ] **步骤 3：实现工具返回格式化**

在 `backend/app/agents/faculty_crawler_agent.py` 中导入 `save_candidate_batch`，并新增：

```python
def _format_save_batch_result_for_model(result: dict[str, Any]) -> dict[str, Any]:
    return {
        "batch_status": result["batch_status"],
        "attempted_count": result["attempted_count"],
        "saved_count": result["saved_count"],
        "failed_count": result["failed_count"],
        "failed_items": result["failed_items"],
        "total_saved_count": result["total_saved_count"],
    }
```

修改工具：

```python
        result = await save_candidate_batch(ctx, payloads)
        return _format_save_batch_result_for_model(result)
```

- [ ] **步骤 4：运行测试验证成功**

运行：`cd backend && uv run python -m unittest test.test_faculty_crawler_agent.FacultyCrawlerAgentSaveResultTests.test_format_save_batch_result_for_model_is_compact`

预期：PASS。

- [ ] **步骤 5：Commit**

```bash
git add backend/app/agents/faculty_crawler_agent.py backend/test/test_faculty_crawler_agent.py
git commit -m "feat(crawler): compact save tool responses"
```

## 任务 3：模型调用前压缩旧保存交互

**文件：**
- 修改：`backend/app/agents/faculty_crawler_agent.py`
- 测试：`backend/test/test_faculty_crawler_agent.py`

- [ ] **步骤 1：编写失败测试，确认旧保存调用和工具结果被压缩**

在 `backend/test/test_faculty_crawler_agent.py` 增加：

```python
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from app.agents.faculty_crawler_agent import compact_save_tool_history


class FacultyCrawlerAgentCompactionTests(unittest.TestCase):
    def test_compact_save_tool_history_replaces_old_save_pairs_with_summary(self) -> None:
        messages = [
            HumanMessage(content="入口任务"),
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "save_professor_candidates",
                        "args": {"candidates": [{"name": "张三"}, {"name": "李四"}]},
                        "id": "call_1",
                    }
                ],
            ),
            ToolMessage(
                content='{"batch_status":"saved","attempted_count":2,"saved_count":2,"failed_count":0,"failed_items":[],"total_saved_count":2}',
                tool_call_id="call_1",
            ),
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "save_professor_candidates",
                        "args": {"candidates": [{"name": "王五"}]},
                        "id": "call_2",
                    }
                ],
            ),
            ToolMessage(
                content='{"batch_status":"saved","attempted_count":1,"saved_count":1,"failed_count":0,"failed_items":[],"total_saved_count":3}',
                tool_call_id="call_2",
            ),
        ]

        compacted = compact_save_tool_history(messages)
        serialized = "\n".join(str(message.content) for message in compacted)

        self.assertEqual(len(compacted), 2)
        self.assertIsInstance(compacted[0], HumanMessage)
        self.assertIsInstance(compacted[1], HumanMessage)
        self.assertIn("已成功保存 3 条", serialized)
        self.assertNotIn("张三", serialized)
        self.assertNotIn("李四", serialized)
        self.assertNotIn("王五", serialized)
```

- [ ] **步骤 2：运行测试验证失败**

运行：`cd backend && uv run python -m unittest test.test_faculty_crawler_agent.FacultyCrawlerAgentCompactionTests.test_compact_save_tool_history_replaces_old_save_pairs_with_summary`

预期：FAIL，报错无法导入 `compact_save_tool_history`。

- [ ] **步骤 3：实现压缩 helper**

在 `backend/app/agents/faculty_crawler_agent.py` 中添加：

```python
SAVE_TOOL_NAME = "save_professor_candidates"


def compact_save_tool_history(messages: list[Any]) -> list[Any]:
    save_call_ids = _collect_completed_save_call_ids(messages)
    if not save_call_ids:
        return messages

    summary = _build_save_history_summary(messages, save_call_ids)
    compacted: list[Any] = []
    inserted_summary = False

    for message in messages:
        if _is_save_ai_message(message, save_call_ids):
            if not inserted_summary:
                compacted.append(HumanMessage(content=summary))
                inserted_summary = True
            continue
        if _is_save_tool_message(message, save_call_ids):
            continue
        compacted.append(message)

    return compacted
```

配套 helper：

```python
def _collect_completed_save_call_ids(messages: list[Any]) -> set[str]:
    save_call_ids: set[str] = set()
    tool_message_ids: set[str] = set()
    for message in messages:
        for tool_call in getattr(message, "tool_calls", []) or []:
            if tool_call.get("name") == SAVE_TOOL_NAME and isinstance(tool_call.get("id"), str):
                save_call_ids.add(tool_call["id"])
        tool_call_id = getattr(message, "tool_call_id", None)
        if isinstance(tool_call_id, str):
            tool_message_ids.add(tool_call_id)
    return save_call_ids & tool_message_ids


def _is_save_ai_message(message: Any, save_call_ids: set[str]) -> bool:
    tool_calls = getattr(message, "tool_calls", []) or []
    return any(tool_call.get("id") in save_call_ids for tool_call in tool_calls)


def _is_save_tool_message(message: Any, save_call_ids: set[str]) -> bool:
    return getattr(message, "tool_call_id", None) in save_call_ids
```

实现摘要：

```python
def _build_save_history_summary(messages: list[Any], save_call_ids: set[str]) -> str:
    total_saved = 0
    last_status = ""
    failed_lines: list[str] = []
    for message in messages:
        if not _is_save_tool_message(message, save_call_ids):
            continue
        parsed = _parse_tool_json_content(getattr(message, "content", ""))
        if not parsed:
            continue
        total_saved = int(parsed.get("total_saved_count") or total_saved)
        last_status = str(parsed.get("batch_status") or last_status)
        for item in parsed.get("failed_items") or []:
            if not isinstance(item, dict):
                continue
            name = item.get("name") or f"index={item.get('index')}"
            reason = item.get("reason") or "未知原因"
            failed_lines.append(f"- {name}: {reason}")

    failure_text = "\n".join(failed_lines[-10:]) if failed_lines else "无"
    return (
        "候选保存历史已压缩。\n"
        f"从任务开始到现在已成功保存 {total_saved} 条。\n"
        f"最近保存批次状态：{last_status or 'unknown'}。\n"
        f"最近失败项：\n{failure_text}\n"
        "继续从页面中尚未保存的候选位置往后提取；如果上一批被 rejected，请优先修正失败项并重试该批。"
    )
```

解析工具内容：

```python
def _parse_tool_json_content(content: object) -> dict[str, Any] | None:
    if not isinstance(content, str):
        return None
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None
```

补充 import：

```python
from langchain_core.messages import HumanMessage
```

- [ ] **步骤 4：运行压缩 helper 测试**

运行：`cd backend && uv run python -m unittest test.test_faculty_crawler_agent.FacultyCrawlerAgentCompactionTests`

预期：PASS。

- [ ] **步骤 5：编写失败测试，确认失败批次摘要保留失败项**

在 `backend/test/test_faculty_crawler_agent.py` 增加：

```python
def test_compact_save_tool_history_keeps_rejected_batch_failures(self) -> None:
    messages = [
        HumanMessage(content="入口任务"),
        AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "save_professor_candidates",
                    "args": {"candidates": [{"name": ""}]},
                    "id": "call_bad",
                }
            ],
        ),
        ToolMessage(
            content='{"batch_status":"rejected","attempted_count":1,"saved_count":0,"failed_count":1,"failed_items":[{"index":0,"name":null,"reason":"name 不能为空"}],"total_saved_count":20}',
            tool_call_id="call_bad",
        ),
    ]

    compacted = compact_save_tool_history(messages)
    serialized = "\n".join(str(message.content) for message in compacted)

    self.assertIn("已成功保存 20 条", serialized)
    self.assertIn("index=0", serialized)
    self.assertIn("name 不能为空", serialized)
```

- [ ] **步骤 6：运行测试验证成功**

运行：`cd backend && uv run python -m unittest test.test_faculty_crawler_agent.FacultyCrawlerAgentCompactionTests`

预期：PASS。

- [ ] **步骤 7：Commit**

```bash
git add backend/app/agents/faculty_crawler_agent.py backend/test/test_faculty_crawler_agent.py
git commit -m "feat(crawler): compact saved candidate tool history"
```

## 任务 4：接入压缩 middleware

**文件：**
- 修改：`backend/app/agents/faculty_crawler_agent.py`
- 测试：`backend/test/test_faculty_crawler_agent.py`

- [ ] **步骤 1：编写失败测试，确认 middleware 调用 handler 前压缩 messages**

在 `backend/test/test_faculty_crawler_agent.py` 增加：

```python
from types import SimpleNamespace

from app.agents.faculty_crawler_agent import SaveHistoryCompactionMiddleware


class FacultyCrawlerAgentMiddlewareTests(unittest.TestCase):
    def test_save_history_compaction_middleware_overrides_messages(self) -> None:
        original_messages = [
            HumanMessage(content="入口任务"),
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "save_professor_candidates",
                        "args": {"candidates": [{"name": "张三"}]},
                        "id": "call_1",
                    }
                ],
            ),
            ToolMessage(
                content='{"batch_status":"saved","attempted_count":1,"saved_count":1,"failed_count":0,"failed_items":[],"total_saved_count":1}',
                tool_call_id="call_1",
            ),
        ]
        captured = {}

        class Request:
            messages = original_messages
            tools = []

            def override(self, **kwargs):
                captured.update(kwargs)
                return SimpleNamespace(messages=kwargs.get("messages", self.messages), tools=self.tools)

        def handler(request):
            return request.messages

        result = SaveHistoryCompactionMiddleware().wrap_model_call(Request(), handler)

        self.assertEqual(result, captured["messages"])
        self.assertEqual(len(result), 2)
        self.assertIn("已成功保存 1 条", result[1].content)
```

- [ ] **步骤 2：运行测试验证失败**

运行：`cd backend && uv run python -m unittest test.test_faculty_crawler_agent.FacultyCrawlerAgentMiddlewareTests.test_save_history_compaction_middleware_overrides_messages`

预期：FAIL，报错无法导入 `SaveHistoryCompactionMiddleware`。

- [ ] **步骤 3：实现 middleware**

在 `backend/app/agents/faculty_crawler_agent.py` 添加：

```python
class SaveHistoryCompactionMiddleware(AgentMiddleware[Any, Any, Any]):
    def wrap_model_call(self, request: Any, handler: Callable[[Any], Any]) -> Any:
        compacted_messages = compact_save_tool_history(list(request.messages))
        if compacted_messages is not request.messages:
            request = request.override(messages=compacted_messages)
        return handler(request)

    async def awrap_model_call(
        self,
        request: Any,
        handler: Callable[[Any], Awaitable[Any]],
    ) -> Any:
        compacted_messages = compact_save_tool_history(list(request.messages))
        if compacted_messages is not request.messages:
            request = request.override(messages=compacted_messages)
        return await handler(request)
```

修正 identity 判断，避免 list 拷贝导致总是 override：

```python
def compact_save_tool_history(messages: list[Any]) -> list[Any]:
    save_call_ids = _collect_completed_save_call_ids(messages)
    if not save_call_ids:
        return messages
    ...
```

middleware 中改为：

```python
messages = list(request.messages)
compacted_messages = compact_save_tool_history(messages)
if compacted_messages != messages:
    request = request.override(messages=compacted_messages)
```

- [ ] **步骤 4：接入 create_deep_agent**

在 `create_faculty_crawler_agent` 中调整：

```python
        middleware=[
            ControlledCrawlerToolMiddleware(),
            SaveHistoryCompactionMiddleware(),
        ],
```

- [ ] **步骤 5：运行 agent 测试**

运行：`cd backend && uv run python -m unittest test.test_faculty_crawler_agent`

预期：PASS。

- [ ] **步骤 6：Commit**

```bash
git add backend/app/agents/faculty_crawler_agent.py backend/test/test_faculty_crawler_agent.py
git commit -m "feat(crawler): compact save history before model calls"
```

## 任务 5：一阶段结束后不自动详情补全

**文件：**
- 修改：`backend/app/services/crawl_job_runtime.py`
- 测试：`backend/test/test_crawl_job_runtime.py`

- [ ] **步骤 1：编写失败测试，确认发现阶段结束不会自动补全**

在 `backend/test/test_crawl_job_runtime.py` 增加：

```python
async def test_run_queued_crawl_job_does_not_auto_enrich_saved_candidates(self) -> None:
    job_id = await self._create_default_profile_and_job(
        start_url="https://example.edu/faculty/list",
    )
    enrichment_calls: list[str] = []

    async def fake_run(
        ctx: CrawlToolContext,
        llm_profile: LLMProfile,
        trace_callback=None,
    ) -> dict[str, object]:
        _ = llm_profile, trace_callback
        await save_candidates(
            ctx,
            [
                ProfessorCandidatePayload(
                    name="张三",
                    profile_url="https://example.edu/faculty/zhang",
                )
            ],
        )
        return {}

    async def fake_crawl_page_with_crawl4ai(
        ctx: CrawlToolContext,
        url: str,
        *,
        intent: str = "generic",
    ) -> PageSnapshot:
        _ = ctx, intent
        enrichment_calls.append(url)
        return PageSnapshot(
            url=url,
            title="张三",
            text="邮箱：zhang@example.edu",
            html="<html></html>",
            links=[],
            fetch_method="http",
            status="succeeded",
        )

    with patch(
        "app.services.crawl_job_runtime.run_faculty_crawler_agent",
        new=fake_run,
    ), patch(
        "app.services.crawl_job_runtime.crawl_page_with_crawl4ai",
        new=fake_crawl_page_with_crawl4ai,
    ):
        processed = await run_queued_crawl_jobs_once(self.session_factory)

    self.assertEqual(processed, 1)
    self.assertEqual(enrichment_calls, [])
    job = await self._get_job(job_id)
    self.assertEqual(job.status, CrawlJobStatus.NEEDS_REVIEW.value)
```

- [ ] **步骤 2：运行测试验证失败**

运行：`cd backend && uv run python -m unittest test.test_crawl_job_runtime.CrawlJobRuntimeTests.test_run_queued_crawl_job_does_not_auto_enrich_saved_candidates`

预期：FAIL，当前会自动调用 `_enrich_saved_candidates`。

- [ ] **步骤 3：移除自动补全调用**

在 `backend/app/services/crawl_job_runtime.py` 中把：

```python
        await run_faculty_crawler_agent(ctx, llm_profile, trace_callback=trace_callback)
        await _enrich_saved_candidates(
            session_factory,
            ctx,
            llm_profile=llm_profile,
            trace_callback=trace_callback,
        )
        await _complete_running_job(session_factory, job_id)
```

改为：

```python
        await run_faculty_crawler_agent(ctx, llm_profile, trace_callback=trace_callback)
        await _complete_running_job(session_factory, job_id)
```

- [ ] **步骤 4：运行 runtime 测试**

运行：`cd backend && uv run python -m unittest test.test_crawl_job_runtime`

预期：PASS。若已有测试断言自动补全日志，需要同步改为“补全不在一阶段自动发生”。

- [ ] **步骤 5：Commit**

```bash
git add backend/app/services/crawl_job_runtime.py backend/test/test_crawl_job_runtime.py
git commit -m "feat(crawler): stop auto profile enrichment after discovery"
```

## 任务 6：验证 token 降幅和协议安全

**文件：**
- 测试：`backend/test/test_faculty_crawler_agent.py`
- 可选修改：`backend/app/agents/faculty_crawler_agent.py`

- [ ] **步骤 1：编写协议安全测试，确认不会留下孤立 ToolMessage**

在 `backend/test/test_faculty_crawler_agent.py` 增加：

```python
def test_compaction_does_not_leave_orphan_save_tool_messages(self) -> None:
    messages = [
        HumanMessage(content="入口任务"),
        AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "save_professor_candidates",
                    "args": {"candidates": [{"name": "张三"}]},
                    "id": "call_1",
                }
            ],
        ),
        ToolMessage(
            content='{"batch_status":"saved","attempted_count":1,"saved_count":1,"failed_count":0,"failed_items":[],"total_saved_count":1}',
            tool_call_id="call_1",
        ),
        AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "crawl_page",
                    "args": {"url": "https://example.edu"},
                    "id": "crawl_1",
                }
            ],
        ),
        ToolMessage(
            content='{"status":"succeeded","text":"页面内容"}',
            tool_call_id="crawl_1",
        ),
    ]

    compacted = compact_save_tool_history(messages)
    tool_ids = {getattr(message, "tool_call_id", None) for message in compacted if getattr(message, "tool_call_id", None)}
    ai_tool_ids = {
        tool_call["id"]
        for message in compacted
        for tool_call in (getattr(message, "tool_calls", []) or [])
        if isinstance(tool_call, dict) and "id" in tool_call
    }

    self.assertNotIn("call_1", tool_ids)
    self.assertNotIn("call_1", ai_tool_ids)
    self.assertIn("crawl_1", tool_ids)
    self.assertIn("crawl_1", ai_tool_ids)
```

- [ ] **步骤 2：运行测试验证成功**

运行：`cd backend && uv run python -m unittest test.test_faculty_crawler_agent.FacultyCrawlerAgentCompactionTests`

预期：PASS。

- [ ] **步骤 3：运行后端相关测试**

运行：

```bash
cd backend
uv run python -m unittest test.test_faculty_crawler_agent test.test_crawler_tools test.test_crawl_job_runtime test.test_crawl_job_metrics
```

预期：PASS。

- [ ] **步骤 4：手动验证 token 行为**

用本地已有 crawler debug 开启方式运行一轮小规模抓取，检查 `data/logs/crawler/crawl-job-<id>.jsonl`：

- 保存阶段每轮 `input_tokens` 不应随保存批次数线性上涨。
- 工具结果中应只出现 `saved_count`、`failed_count`、`total_saved_count` 等轻量字段。
- 旧保存批次的候选姓名不应在后续模型输入上下文中反复出现。

- [ ] **步骤 5：Commit**

```bash
git add backend/app/agents/faculty_crawler_agent.py backend/test/test_faculty_crawler_agent.py
git commit -m "test(crawler): cover save history compaction protocol safety"
```

## 自检

- 规格覆盖度：覆盖了轻量返回、失败整批回滚、上下文压缩、协议安全、不自动补全、token 验证。
- 占位符扫描：计划没有使用“待定”“TODO”“后续实现”作为实现步骤。
- 类型一致性：统一使用 `save_candidate_batch`、`CandidateBatchSaveResult`、`compact_save_tool_history`、`SaveHistoryCompactionMiddleware`。
- 范围控制：不实现网页结构化解析，不实现用户主动补全 UI/API；本计划只移除自动补全，并为后续主动补全保留现有 `_enrich_saved_candidates` 能力。

## 执行交接

计划已完成并保存到 `docs/superpowers/plans/2026-04-29-crawler-save-context-compaction.md`。两种执行方式：

**1. 子代理驱动（推荐）** - 每个任务调度一个新的子代理，任务间进行审查，快速迭代。

**2. 内联执行** - 在当前会话中使用 executing-plans 执行任务，批量执行并设有检查点。

选哪种方式？
