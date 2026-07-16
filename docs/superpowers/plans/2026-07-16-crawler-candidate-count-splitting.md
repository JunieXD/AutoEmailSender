# Chunk Worker 候选计数与递归拆分实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 让 LLM 输出必填 `candidate_count`，由后端统一推导 chunk 的无候选、完成或递归拆分状态，并把候选递归拆分门槛和 overlap 调整为 100 / 15。

**架构：** `crawler_v2_chunk_worker` 负责严格解析数量契约、校验 0–10 位候选的一致性并根据数量派发后端控制流；`crawler_chunk_runtime` 继续拥有数据库父子 chunk 状态迁移，并为三种不可拆分原因提供稳定诊断；`crawler_chunking` 只调整默认参数，继续使用现有 token 估算和候选密集分片算法。LLM 不再输出或控制 `chunk_status`，数据库状态名保持不变。

**技术栈：** Python 3.12、Pydantic v2、SQLAlchemy AsyncSession、unittest、uv、SQLite

---

## 文件结构

### 修改

- `backend/app/services/crawler_v2_chunk_worker.py`：新 LLM payload、Prompt、数量一致性校验、状态推导、候选保存/拆分派发和调试字段。
- `backend/app/services/crawler_chunking.py`：把默认 `min_split_tokens` 改为 100，把 `retry_split_overlap_tokens` 改为 15；首次 `overlap_tokens` 保持 180。
- `backend/app/services/crawler_chunk_runtime.py`：区分最小 token、最大深度和无有效子 chunk 三类 terminal 拆分失败。
- `backend/test/test_crawler_v2_chunk_worker.py`：覆盖新 Prompt 契约、严格解析、后端状态推导、契约冲突、拆分和日志。
- `backend/test/test_crawler_chunking.py`：覆盖 100/101 边界、15-token overlap、180-token 首次分块和 150 链接回放。

### 不新增

- 不新增数据库表或 Alembic 迁移。
- 不提交华中科技大学原始 crawler 输出。自动测试使用确定性生成的 150 链接结构；最终验收通过只读 SQLite URI 重放本机现有快照。
- 不修改 V1 Agent 的 `submit_page_chunk_candidates` 工具契约；本次范围只覆盖当前默认 Runtime V2 Chunk Worker。

## 任务 1：替换 LLM 输出契约和 Prompt

**文件：**
- 修改：`backend/app/services/crawler_v2_chunk_worker.py:38-139`
- 修改：`backend/test/test_crawler_v2_chunk_worker.py:20-63`

- [ ] **步骤 1：编写 Prompt 和 payload 严格校验的失败测试**

把现有 Prompt 断言改为：

```python
def test_chunk_prompt_requires_candidate_count_and_keeps_split_control_backend_only(self) -> None:
    from app.services.crawler_v2_chunk_worker import build_v2_chunk_prompt

    prompt = build_v2_chunk_prompt(
        university="示例大学",
        school="计算机学院",
        source_url="https://example.edu/faculty",
        chunk_content="[张三](https://example.edu/zhang.html) 教授",
    )

    self.assertIn("candidate_count", prompt)
    self.assertIn("candidate_count > 10", prompt)
    self.assertIn("candidates 必须为空", prompt)
    self.assertIn("len(candidates) 必须等于 candidate_count", prompt)
    self.assertNotIn('"chunk_status"', prompt)
    self.assertIn("缺少 email 且缺少 profile_url", prompt)
    self.assertIn("导师个人主页", prompt)
    self.assertIn("不能放入 discovered_urls", prompt)
```

新增纯校验测试：

```python
def test_chunk_payload_rejects_invalid_candidate_count_and_count_mismatches(self) -> None:
    from app.services.crawler_v2_chunk_worker import _validate_chunk_agent_payload

    invalid_payloads = [
        {"candidates": [], "discovered_urls": []},
        {"candidate_count": -1, "candidates": [], "discovered_urls": []},
        {"candidate_count": True, "candidates": [], "discovered_urls": []},
        {"candidate_count": 1.5, "candidates": [], "discovered_urls": []},
        {"candidate_count": "1", "candidates": [], "discovered_urls": []},
        {"candidate_count": 0, "candidates": [{"name": "张三"}], "discovered_urls": []},
        {"candidate_count": 1, "candidates": [], "discovered_urls": []},
        {"candidate_count": 1, "candidates": [{}], "discovered_urls": "https://example.edu/page2"},
    ]

    for payload in invalid_payloads:
        with self.subTest(payload=payload), self.assertRaises(ValueError):
            _validate_chunk_agent_payload(payload)
```

再覆盖 >10 时非空候选数组不会阻止后续拆分：

```python
def test_chunk_payload_allows_backend_to_discard_candidates_when_count_exceeds_limit(self) -> None:
    from app.services.crawler_v2_chunk_worker import _validate_chunk_agent_payload

    payload = {
        "candidate_count": 11,
        "candidates": [{"unexpected": object()}],
        "discovered_urls": [],
    }

    self.assertIs(_validate_chunk_agent_payload(payload), payload)
```

- [ ] **步骤 2：运行测试并确认旧契约失败**

```bash
cd backend
rtk uv run python -m unittest \
  test.test_crawler_v2_chunk_worker.CrawlerV2ChunkWorkerTests.test_chunk_prompt_requires_candidate_count_and_keeps_split_control_backend_only \
  test.test_crawler_v2_chunk_worker.CrawlerV2ChunkWorkerTests.test_chunk_payload_rejects_invalid_candidate_count_and_count_mismatches \
  test.test_crawler_v2_chunk_worker.CrawlerV2ChunkWorkerTests.test_chunk_payload_allows_backend_to_discard_candidates_when_count_exceeds_limit
```

预期：FAIL，Prompt 仍包含 `chunk_status`，payload 仍缺少 `candidate_count` 校验。

- [ ] **步骤 3：实现严格 payload 模型和基础校验**

将 payload 改为必填字段，允许忽略旧模型偶然附带的额外字段，但不把它们用于控制流：

```python
class V2ChunkAgentPayload(BaseModel):
    model_config = ConfigDict(extra="ignore")

    candidate_count: int = Field(strict=True, ge=0)
    candidates: list[dict[str, Any]]
    discovered_urls: list[str]
```

同时把 Pydantic import 调整为：

```python
from pydantic import BaseModel, ConfigDict, Field
```

将 `_validate_chunk_agent_payload` 改为：

```python
def _validate_chunk_agent_payload(payload: object) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("Chunk Worker 返回结构不是 JSON 对象")

    required = {"candidate_count", "candidates", "discovered_urls"}
    missing = required.difference(payload)
    if missing:
        raise ValueError(f"Chunk Worker 返回缺少字段：{', '.join(sorted(missing))}")

    candidate_count = payload["candidate_count"]
    if isinstance(candidate_count, bool) or not isinstance(candidate_count, int) or candidate_count < 0:
        raise ValueError("Chunk Worker 的 candidate_count 必须是 JSON 非负整数")

    candidates = payload["candidates"]
    discovered_urls = payload["discovered_urls"]
    if not isinstance(candidates, list):
        raise ValueError("Chunk Worker 的 candidates 必须是数组")
    if not isinstance(discovered_urls, list):
        raise ValueError("Chunk Worker 的 discovered_urls 必须是数组")
    if candidate_count <= MAX_CANDIDATES_PER_CHUNK_RESULT and len(candidates) != candidate_count:
        raise ValueError(
            "Chunk Worker 的 candidate_count 与 candidates 数量不一致："
            f"candidate_count={candidate_count}, candidates={len(candidates)}"
        )
    return payload
```

- [ ] **步骤 4：重写 Prompt，删除模型状态分支**

保留现有候选证据和 URL 分类规则，只把输出部分改成以下约束：

```python
"只输出一个 JSON 对象，字段为 candidate_count、candidates、discovered_urls。不要输出解释文字。\n"
"candidate_count 是当前 chunk 中符合候选提交条件的总人数，必须是 JSON 非负整数。\n"
"candidate_count 为 0 时，candidates 必须为空。\n"
"candidate_count 为 1 到 10 时，必须输出全部候选，len(candidates) 必须等于 candidate_count。\n"
"candidate_count > 10 时，candidates 必须为空；不要输出前 10 个，也不要输出完整候选数组。\n"
"不要输出 chunk_status；无候选、完成和拆分状态全部由后端根据 candidate_count 决定。\n"
```

三个最小示例分别使用 `candidate_count=0`、`candidate_count=1` 和 `candidate_count=11`。1 人示例必须包含姓名和 `profile_url`，11 人示例必须是空 `candidates`。

- [ ] **步骤 5：运行 Prompt 与 payload 测试**

```bash
cd backend
rtk uv run python -m unittest test.test_crawler_v2_chunk_worker.CrawlerV2ChunkWorkerTests.test_chunk_prompt_requires_candidate_count_and_keeps_split_control_backend_only
rtk uv run python -m unittest test.test_crawler_v2_chunk_worker.CrawlerV2ChunkWorkerTests.test_chunk_payload_rejects_invalid_candidate_count_and_count_mismatches
rtk uv run python -m unittest test.test_crawler_v2_chunk_worker.CrawlerV2ChunkWorkerTests.test_chunk_payload_allows_backend_to_discard_candidates_when_count_exceeds_limit
```

预期：PASS。此时不要单独提交：payload schema 和它的 worker 消费端必须在任务 2 中作为一个原子提交落地，避免产生完整 Chunk Worker 测试暂时失败的中间 commit。

## 任务 2：让后端根据数量执行状态迁移

**文件：**
- 修改：`backend/app/services/crawler_v2_chunk_worker.py:141-444`
- 修改：`backend/test/test_crawler_v2_chunk_worker.py:84-620`

- [ ] **步骤 1：编写 0、1–10、>10 三条控制流失败测试**

新增或替换现有 `chunk_status` 测试：

```python
async def test_complete_chunk_derives_no_candidates_from_zero_count(self) -> None:
    _, chunk_id = await self._seed_processing_chunk()

    result = await complete_current_chunk(
        self.session_factory,
        chunk_id=chunk_id,
        worker_id="w1",
        candidate_count=0,
        candidates=[],
        discovered_urls=[],
    )

    self.assertEqual(result["derived_chunk_status"], CrawlPageChunkStatus.NO_CANDIDATES.value)
    async with self.session_factory() as session:
        chunk = await session.get(CrawlPageChunk, chunk_id)
    assert chunk is not None
    self.assertEqual(chunk.status, CrawlPageChunkStatus.NO_CANDIDATES.value)
```

```python
async def test_complete_chunk_derives_completed_for_exactly_ten_candidates(self) -> None:
    _, chunk_id = await self._seed_processing_chunk()
    candidates = [
        ProfessorCandidatePayload(
            name=f"教师{i}",
            profile_url=f"https://example.edu/t{i}.html",
            confidence=0.9,
        )
        for i in range(10)
    ]

    result = await complete_current_chunk(
        self.session_factory,
        chunk_id=chunk_id,
        worker_id="w1",
        candidate_count=10,
        candidates=candidates,
        discovered_urls=[],
    )

    self.assertEqual(result["saved_count"], 10)
    self.assertEqual(result["derived_chunk_status"], CrawlPageChunkStatus.COMPLETED.value)
```

```python
async def test_chunk_worker_splits_before_parsing_candidate_schema(self) -> None:
    job_id, chunk_id = await self._seed_processing_chunk(with_profile=True)
    async with self.session_factory() as session:
        chunk = await session.get(CrawlPageChunk, chunk_id)
        assert chunk is not None
        chunk.content = "\n".join(
            f"教师{i} [详情](https://example.edu/t{i}.html) 研究方向 人工智能 数据挖掘"
            for i in range(80)
        )
        await session.commit()

    payload = {
        "candidate_count": 11,
        "candidates": [{"invalid_candidate_shape": object()}],
        "discovered_urls": ["https://example.edu/page2.html"],
    }
    with patch(
        "app.services.crawler_v2_chunk_worker.invoke_v2_chunk_agent",
        new=AsyncMock(return_value=(payload, None, "raw")),
    ), patch("app.services.crawler_v2_chunk_worker.append_crawler_v2_debug_event") as debug_mock:
        processed = await run_crawler_v2_chunk_worker_once(
            self.session_factory,
            chunk_id=chunk_id,
            worker_id="w1",
        )

    self.assertEqual(processed, 1)
    async with self.session_factory() as session:
        saved = list(await session.scalars(select(CrawlCandidate).where(CrawlCandidate.job_id == job_id)))
        urls = list(await session.scalars(select(CrawlPageTask).where(CrawlPageTask.job_id == job_id)))
        parent = await session.get(CrawlPageChunk, chunk_id)
    assert parent is not None
    self.assertEqual(parent.status, CrawlPageChunkStatus.SUPERSEDED.value)
    self.assertEqual(saved, [])
    self.assertEqual(urls, [])
    completed = next(call for call in debug_mock.call_args_list if call.kwargs["event_name"] == "chunk_completed")
    self.assertEqual(completed.kwargs["payload"]["save_result"]["contract_warning"], "candidate_count_candidates_conflict")
```

- [ ] **步骤 2：编写旧 `chunk_status` 不再改变控制流的失败测试**

```python
async def test_chunk_worker_ignores_legacy_chunk_status_extra_field(self) -> None:
    _, chunk_id = await self._seed_processing_chunk(with_profile=True)
    payload = {
        "candidate_count": 1,
        "candidates": [{"name": "张三", "profile_url": "https://example.edu/zhang.html"}],
        "discovered_urls": [],
        "chunk_status": "too_many_candidates",
    }

    with patch(
        "app.services.crawler_v2_chunk_worker.invoke_v2_chunk_agent",
        new=AsyncMock(return_value=payload),
    ):
        await run_crawler_v2_chunk_worker_once(
            self.session_factory,
            chunk_id=chunk_id,
            worker_id="w1",
        )

    async with self.session_factory() as session:
        chunk = await session.get(CrawlPageChunk, chunk_id)
    assert chunk is not None
    self.assertEqual(chunk.status, CrawlPageChunkStatus.COMPLETED.value)
```

新增 count/数组不一致的 worker 失败测试，证明不会保存部分候选：

```python
async def test_chunk_worker_marks_retryable_when_candidate_count_mismatches_candidates(self) -> None:
    job_id, chunk_id = await self._seed_processing_chunk(with_profile=True)
    payload = {
        "candidate_count": 2,
        "candidates": [{"name": "张三", "profile_url": "https://example.edu/zhang.html"}],
        "discovered_urls": [],
    }

    with patch(
        "app.services.crawler_v2_chunk_worker.invoke_v2_chunk_agent",
        new=AsyncMock(return_value=payload),
    ):
        await run_crawler_v2_chunk_worker_once(
            self.session_factory,
            chunk_id=chunk_id,
            worker_id="w1",
        )

    async with self.session_factory() as session:
        chunk = await session.get(CrawlPageChunk, chunk_id)
        saved = list(
            await session.scalars(
                select(CrawlCandidate).where(CrawlCandidate.job_id == job_id)
            )
        )
    assert chunk is not None
    self.assertEqual(chunk.status, CrawlPageChunkStatus.FAILED_RETRYABLE.value)
    self.assertIn("candidate_count 与 candidates 数量不一致", chunk.last_error or "")
    self.assertEqual(saved, [])
```

- [ ] **步骤 3：运行控制流测试并确认旧签名失败**

```bash
cd backend
rtk uv run python -m unittest \
  test.test_crawler_v2_chunk_worker.CrawlerV2ChunkWorkerTests.test_complete_chunk_derives_no_candidates_from_zero_count \
  test.test_crawler_v2_chunk_worker.CrawlerV2ChunkWorkerTests.test_complete_chunk_derives_completed_for_exactly_ten_candidates \
  test.test_crawler_v2_chunk_worker.CrawlerV2ChunkWorkerTests.test_chunk_worker_splits_before_parsing_candidate_schema \
  test.test_crawler_v2_chunk_worker.CrawlerV2ChunkWorkerTests.test_chunk_worker_ignores_legacy_chunk_status_extra_field \
  test.test_crawler_v2_chunk_worker.CrawlerV2ChunkWorkerTests.test_chunk_worker_marks_retryable_when_candidate_count_mismatches_candidates
```

预期：FAIL，`complete_current_chunk` 仍要求 `chunk_status`，worker 仍先解析全部候选。

- [ ] **步骤 4：增加后端派生状态函数并修改完成入口**

```python
def _derive_chunk_status(candidate_count: int) -> str:
    if candidate_count == 0:
        return CrawlPageChunkStatus.NO_CANDIDATES.value
    if candidate_count > MAX_CANDIDATES_PER_CHUNK_RESULT:
        return CrawlPageChunkStatus.SPLIT_REQUIRED.value
    return CrawlPageChunkStatus.COMPLETED.value
```

把 `complete_current_chunk` 的参数 `chunk_status: str` 替换为 `candidate_count: int`。函数开头计算 `derived_chunk_status`；只有派生状态为 `split_required` 时调用：

```python
split_result = await split_page_chunk_for_retry(
    session_factory,
    job_id=chunk.job_id,
    chunk_pk=chunk.id,
    reason="candidate_count_exceeded",
)
```

拆分返回值加入 `derived_chunk_status="split_required"`。非拆分路径完成保存后使用：

```python
chunk.status = derived_chunk_status
```

并在返回值加入 `derived_chunk_status`。删除 `_normalize_chunk_status`；不得用 `len(candidates) > 10` 作为拆分后备条件。

- [ ] **步骤 5：在 worker 中先按数量分支，再解析候选 schema**

```python
payload = _validate_chunk_agent_payload(payload)
candidate_count = payload["candidate_count"]
candidate_items = payload["candidates"]
candidate_payload_count = len(candidate_items)
contract_warning = (
    "candidate_count_candidates_conflict"
    if candidate_count > MAX_CANDIDATES_PER_CHUNK_RESULT and candidate_items
    else None
)
candidates = (
    []
    if candidate_count > MAX_CANDIDATES_PER_CHUNK_RESULT
    else [ProfessorCandidatePayload.model_validate(item) for item in candidate_items]
)
save_result = await complete_current_chunk(
    session_factory,
    chunk_id=chunk_id,
    worker_id=worker_id,
    candidate_count=candidate_count,
    candidates=candidates,
    discovered_urls=[str(url) for url in payload["discovered_urls"]],
)
save_result["candidate_count"] = candidate_count
save_result["candidate_payload_count"] = candidate_payload_count
if contract_warning is not None:
    save_result["contract_warning"] = contract_warning
```

`chunk_completed` 事件继续记录 `parsed_payload` 和更新后的 `save_result`，因此同时包含原始数量、后端派生状态、候选数组长度和稳定冲突代码。

- [ ] **步骤 6：机械更新现有测试数据和直接调用**

在 `backend/test/test_crawler_v2_chunk_worker.py` 中：

- 所有合法 mock payload 增加与数组长度一致的 `candidate_count`。
- `FakeResponse.content` 和 `invoke_v2_chunk_agent` 断言改为新 JSON 契约；raw model text 不再用旧状态示例。
- 所有 `complete_current_chunk(..., chunk_status="completed")` 改为 `candidate_count=len(candidates)`；空候选完成语义按测试目的使用 0。
- 删除“`split_required` 被忽略”的旧测试，因为 LLM 已不再拥有该字段。
- 原 `too_many_candidates` 测试改为 `candidate_count=11` 且 `candidates=[]`。
- 原“候选数组长度超过 10 自动拆分”测试改为证明只有 `candidate_count=11` 才拆分。
- 暂停、token usage、URL 入队、候选合并和字段补全测试只更新 payload/函数参数，不改变原断言。

- [ ] **步骤 7：运行完整 Chunk Worker 测试并提交**

```bash
cd backend
rtk uv run python -m unittest test.test_crawler_v2_chunk_worker
```

预期：PASS，所有现有保存、暂停、URL 入队和 token usage 行为无回归。

```bash
rtk git add backend/app/services/crawler_v2_chunk_worker.py backend/test/test_crawler_v2_chunk_worker.py
rtk git commit -m "fix(crawler): 后端根据候选数量推导 chunk 状态"
```

## 任务 3：调整递归拆分参数和 terminal 诊断

**文件：**
- 修改：`backend/app/services/crawler_chunking.py:11-24`
- 修改：`backend/app/services/crawler_chunk_runtime.py:318-381`
- 修改：`backend/test/test_crawler_chunking.py:105-173`
- 修改：`backend/test/test_crawler_v2_chunk_worker.py:84-140`

- [ ] **步骤 1：编写默认参数和 100/101 边界失败测试**

```python
def test_default_recursive_split_config_uses_100_tokens_and_15_overlap(self) -> None:
    config = ChunkingConfig()

    self.assertEqual(config.min_split_tokens, 100)
    self.assertEqual(config.retry_split_overlap_tokens, 15)
    self.assertEqual(config.overlap_tokens, 180)
    self.assertEqual(config.max_split_depth, 7)
```

加入精确 token 内容 helper 和边界测试：

```python
def _multiline_content_with_tokens(target: int) -> str:
    chinese_chars = target - 2
    widths = [chinese_chars // 9] * 9
    for index in range(chinese_chars % 9):
        widths[index] += 1
    content = "\n".join("甲" * width for width in widths)
    assert estimate_tokens(content) == target
    return content


def test_candidate_dense_split_stops_at_100_and_allows_101_tokens(self) -> None:
    from app.services.crawler_chunking import split_chunk_content

    common = {
        "source_url": "https://example.edu/faculty",
        "parent_chunk_id": "c1",
        "page_fingerprint": "p",
        "split_depth": 1,
        "split_reason": "candidate_count_exceeded",
        "config": ChunkingConfig(),
    }

    self.assertEqual(split_chunk_content(content=_multiline_content_with_tokens(100), **common), [])
    self.assertGreaterEqual(len(split_chunk_content(content=_multiline_content_with_tokens(101), **common)), 2)
```

- [ ] **步骤 2：把 overlap 测试改为 15-token 上限**

使用短行确保 overlap 能实际发生，并按逐行 token 求和验证算法上限：

```python
def test_candidate_dense_retry_overlap_is_capped_at_fifteen_tokens(self) -> None:
    from app.services.crawler_chunking import split_chunk_content

    content = "\n".join(f"教师{i} 方向" for i in range(80))
    drafts = split_chunk_content(
        source_url="https://example.edu/faculty",
        content=content,
        parent_chunk_id="c1",
        page_fingerprint="p",
        split_depth=1,
        split_reason="candidate_count_exceeded",
        config=ChunkingConfig(),
    )

    first_lines = set(drafts[0].content.splitlines())
    repeated_prefix = [line for line in drafts[1].content.splitlines() if line in first_lines]
    self.assertLessEqual(sum(estimate_tokens(line) for line in repeated_prefix), 15)
```

- [ ] **步骤 3：运行边界测试并确认旧默认值失败**

```bash
cd backend
rtk uv run python -m unittest \
  test.test_crawler_chunking.CrawlerChunkingTests.test_default_recursive_split_config_uses_100_tokens_and_15_overlap \
  test.test_crawler_chunking.CrawlerChunkingTests.test_candidate_dense_split_stops_at_100_and_allows_101_tokens \
  test.test_crawler_chunking.CrawlerChunkingTests.test_candidate_dense_retry_overlap_is_capped_at_fifteen_tokens
```

预期：FAIL，默认值仍为 150 / 30，101 token 内容无法拆分。

- [ ] **步骤 4：修改两个默认值，不增加绕过分支**

```python
@dataclass(frozen=True)
class ChunkingConfig:
    target_tokens: int = 2000
    soft_max_tokens: int = 2800
    hard_max_tokens: int = 3200
    overlap_tokens: int = 180
    min_split_tokens: int = 100
    max_split_depth: int = 7
    retry_split_target_tokens: int = 200
    retry_split_max_parts: int = 10
    retry_split_overlap_tokens: int = 15
    single_chunk_max_tokens: int = 2200
    min_balanced_target_tokens: int = 1200
    max_balanced_target_tokens: int = 2200
```

不要在 `split_chunk_content` 或 `_split_chunk_in_session` 中加入 `candidate_count > 10` 绕过 `min_split_tokens` 的条件。

- [ ] **步骤 5：编写三种 terminal 原因失败测试**

在 Chunk Worker 测试中分别构造：

```python
async def test_complete_chunk_reports_minimum_token_terminal_reason(self) -> None:
    _, chunk_id = await self._seed_processing_chunk()
    async with self.session_factory() as session:
        chunk = await session.get(CrawlPageChunk, chunk_id)
        assert chunk is not None
        chunk.content = "张三"
        await session.commit()

    await complete_current_chunk(
        self.session_factory,
        chunk_id=chunk_id,
        worker_id="w1",
        candidate_count=11,
        candidates=[],
        discovered_urls=[],
    )

    async with self.session_factory() as session:
        chunk = await session.get(CrawlPageChunk, chunk_id)
    assert chunk is not None
    self.assertEqual(chunk.status, CrawlPageChunkStatus.FAILED_TERMINAL.value)
    self.assertIn("chunk_split_min_tokens_reached", chunk.last_error or "")
```

最大深度用超过 100 token 的多行内容并设置 `split_depth=7`，断言 `chunk_split_max_depth_exceeded`。无有效子 chunk 使用 `chunk.content = " " * 405`，确认 `estimate_tokens(chunk.content) > 100` 后断言 `chunk_split_no_valid_children`。

- [ ] **步骤 6：在 runtime 中区分失败原因**

给 `crawler_chunk_runtime.py` 导入 `estimate_tokens`，并在 `_split_chunk_in_session` 中按以下顺序判断：

```python
if chunk.split_depth >= config.max_split_depth:
    chunk.status = CrawlPageChunkStatus.FAILED.value
    chunk.last_error = (
        "chunk_split_max_depth_exceeded: "
        f"split_depth={chunk.split_depth}, max_split_depth={config.max_split_depth}, reason={reason}"
    )
    return 0

content_tokens = estimate_tokens(chunk.content)
if content_tokens <= config.min_split_tokens:
    chunk.status = CrawlPageChunkStatus.FAILED.value
    chunk.last_error = (
        "chunk_split_min_tokens_reached: "
        f"token_estimate={content_tokens}, min_split_tokens={config.min_split_tokens}, reason={reason}"
    )
    return 0
```

调用 `split_chunk_content` 后若没有 drafts：

```python
chunk.status = CrawlPageChunkStatus.FAILED.value
chunk.last_error = (
    "chunk_split_no_valid_children: "
    f"token_estimate={content_tokens}, split_depth={chunk.split_depth}, reason={reason}"
)
return 0
```

外层 `split_page_chunk_for_retry` 继续将失败最终状态改为 `failed_terminal`，不得清空上述 `last_error`。

- [ ] **步骤 7：运行拆分和 Chunk Worker 测试并提交**

```bash
cd backend
rtk uv run python -m unittest test.test_crawler_chunking test.test_crawler_v2_chunk_worker
```

预期：PASS，100/101、15/180 和三种 terminal 原因全部通过。

```bash
rtk git add backend/app/services/crawler_chunking.py backend/app/services/crawler_chunk_runtime.py backend/test/test_crawler_chunking.py backend/test/test_crawler_v2_chunk_worker.py
rtk git commit -m "fix(crawler): 降低候选递归拆分门槛和重叠"
```

## 任务 4：增加结构回放并用真实华科数据验收

**文件：**
- 修改：`backend/test/test_crawler_chunking.py`
- 只读数据：`/Users/junie/Library/Application Support/auto-email-sender-desktop/auto_email_sender.db`

- [ ] **步骤 1：编写确定性的 150 链接回放测试**

测试构造 150 个虚拟教师主页链接，先走首次页面分块，再根据每个 chunk 中的链接数重复执行候选密集递归拆分：

```python
def test_candidate_dense_replay_preserves_150_unique_profiles_with_smaller_overlap(self) -> None:
    import re
    from dataclasses import replace

    from app.services.crawler_chunking import build_page_chunks, split_chunk_content

    link_pattern = re.compile(r"\[[^\]]+\]\((https?://[^)\s]+)\)")
    source = "\n".join(
        f"[教师{i:03d}](https://faculty.example.edu/t{i:03d}) 研究方向 人工智能 数据挖掘"
        for i in range(150)
    )

    def replay(config: ChunkingConfig) -> tuple[int, int, int]:
        roots = build_page_chunks(
            source_url="https://example.edu/faculty",
            html="",
            text=source,
            config=config,
        )
        queue = list(roots)
        collected: list[str] = []
        node_count = 0
        while queue:
            chunk = queue.pop(0)
            node_count += 1
            urls = link_pattern.findall(chunk.content)
            if len(urls) <= 10:
                collected.extend(urls)
                continue
            queue.extend(
                split_chunk_content(
                    source_url=chunk.source_url,
                    content=chunk.content,
                    parent_chunk_id=chunk.chunk_id,
                    page_fingerprint=chunk.page_fingerprint,
                    split_depth=chunk.split_depth + 1,
                    split_reason="candidate_count_exceeded",
                    config=config,
                )
            )
        unique_count = len(set(collected))
        return node_count, len(collected) - unique_count, unique_count

    current = replay(ChunkingConfig())
    previous_overlap = replay(replace(ChunkingConfig(), retry_split_overlap_tokens=30))

    self.assertEqual(current[2], 150)
    self.assertEqual(previous_overlap[2], 150)
    self.assertLessEqual(current[0], previous_overlap[0])
    self.assertLessEqual(current[1], previous_overlap[1])
```

- [ ] **步骤 2：运行结构回放测试**

```bash
cd backend
rtk uv run python -m unittest test.test_crawler_chunking.CrawlerChunkingTests.test_candidate_dense_replay_preserves_150_unique_profiles_with_smaller_overlap
```

预期：PASS，两个 overlap 都保留 150 个唯一链接，15-token overlap 的节点和重复数不高于 30-token overlap。

- [ ] **步骤 3：只读重放真实华科 root chunks**

在 `backend/` 下通过 `uv run python -` 执行以下脚本；脚本只读取 immutable SQLite，不写数据库或仓库：

```python
from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass

from app.services.crawler_chunking import ChunkingConfig, split_chunk_content

DATABASE_URI = (
    "file:/Users/junie/Library/Application%20Support/"
    "auto-email-sender-desktop/auto_email_sender.db?immutable=1"
)
PROFILE_LINK = re.compile(r"\[[^\]]+\]\((https?://[^)\s]+)\)")


@dataclass
class ReplayChunk:
    chunk_id: str
    content: str
    page_fingerprint: str
    split_depth: int = 0
    source_url: str = "http://www.cs.hust.edu.cn/szdw/jsml/axmpyszmlb.htm"


connection = sqlite3.connect(DATABASE_URI, uri=True)
try:
    rows = connection.execute(
        """
        select chunk_id, content, page_fingerprint
        from crawl_page_chunks
        where job_id = 2
          and parent_chunk_id is null
          and chunk_id like 'ddfb28d24730c3ac.%'
        order by chunk_index
        """
    ).fetchall()
finally:
    connection.close()

assert len(rows) == 3, len(rows)
queue = [ReplayChunk(str(chunk_id), str(content), str(fingerprint)) for chunk_id, content, fingerprint in rows]
collected: list[str] = []
node_count = 0
config = ChunkingConfig()

while queue:
    chunk = queue.pop(0)
    node_count += 1
    urls = [url for url in PROFILE_LINK.findall(chunk.content) if "faculty.hust.edu.cn" in url]
    if len(urls) <= 10:
        collected.extend(urls)
        continue
    children = split_chunk_content(
        source_url=chunk.source_url,
        content=chunk.content,
        parent_chunk_id=chunk.chunk_id,
        page_fingerprint=chunk.page_fingerprint,
        split_depth=chunk.split_depth + 1,
        split_reason="candidate_count_exceeded",
        config=config,
    )
    queue.extend(
        ReplayChunk(
            child.chunk_id,
            child.content,
            child.page_fingerprint,
            child.split_depth,
            child.source_url,
        )
        for child in children
    )

unique_count = len(set(collected))
duplicate_count = len(collected) - unique_count
print({"nodes": node_count, "duplicates": duplicate_count, "unique": unique_count})
assert unique_count == 150
assert node_count <= 36
assert duplicate_count <= 20
```

预期输出：`unique=150`、`nodes<=36`、`duplicates<=20`。不得调用 LLM 或网络。

- [ ] **步骤 4：提交自动回放测试**

```bash
rtk git add backend/test/test_crawler_chunking.py
rtk git commit -m "test(crawler): 回放候选密集 token 拆分"
```

## 任务 5：完整回归和范围核对

**文件：**
- 验证：`backend/app/services/crawler_v2_chunk_worker.py`
- 验证：`backend/app/services/crawler_chunking.py`
- 验证：`backend/app/services/crawler_chunk_runtime.py`
- 验证：`backend/test/test_crawler_v2_chunk_worker.py`
- 验证：`backend/test/test_crawler_chunking.py`

- [ ] **步骤 1：确认旧模型状态控制已从 V2 Worker 清除**

```bash
rtk rg -n "payload.*chunk_status|payload\.get\(\"chunk_status\"|chunk_status=str" backend/app/services/crawler_v2_chunk_worker.py
```

预期：无匹配。数据库状态枚举和返回诊断中仍可出现 `completed`、`no_candidates`、`split_required`，但不得读取模型 payload 的 `chunk_status`。

- [ ] **步骤 2：运行相关后端测试**

```bash
cd backend
rtk uv run python -m unittest \
  test.test_crawler_chunking \
  test.test_crawler_chunk_runtime \
  test.test_crawler_v2_chunk_worker \
  test.test_crawler_v2_scheduler \
  test.test_crawler_v2_runtime_routing
```

预期：PASS，0 failures、0 errors。

- [ ] **步骤 3：运行完整后端测试**

```bash
cd backend
rtk uv run python -m unittest discover test
```

预期：PASS，0 failures、0 errors。若存在仓库原有失败，必须记录完整测试名并确认与本次 diff 无关，不能笼统声明全部通过。

- [ ] **步骤 4：检查格式、范围和提交历史**

```bash
rtk git diff --check
rtk git status --short
rtk git log -4 --oneline
```

预期：`git diff --check` 无输出；工作区干净；最近提交仅包含候选计数契约、后端控制流、拆分参数/诊断和回放测试，不包含站点硬编码、数据库迁移、初始 overlap 调整或原始 crawler 输出。

## 完成定义

- Prompt 和 Pydantic payload 只要求 `candidate_count`、`candidates`、`discovered_urls`。
- `candidate_count=0`、1–10、>10 分别由后端推导为 `no_candidates`、`completed`、递归拆分。
- >10 时 Prompt 强制空候选；后端即使收到非空或错误候选结构仍丢弃并拆分，同时记录稳定冲突代码。
- 0–10 时 count 与数组不一致进入现有 retryable/terminal 失败流程，不保存部分候选。
- 默认 `min_split_tokens=100`、`retry_split_overlap_tokens=15`、首次 `overlap_tokens=180`、`max_split_depth=7`。
- 100 token 及以下不能拆分，101 token 可以拆分；不存在候选数量绕过门槛的分支。
- 最小 token、最大深度和无有效子 chunk 产生不同的 terminal `last_error`。
- 自动 150 链接结构回放无遗漏，真实华科只读重放满足 150 个唯一候选、最多 36 个节点、最多 20 个重复候选。
- 相关测试和完整后端测试通过，git diff 不包含 PR #73 式站点硬编码或未经授权的其他改动。
