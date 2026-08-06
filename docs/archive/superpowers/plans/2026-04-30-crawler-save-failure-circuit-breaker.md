# 智能抓取保存失败熔断实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 当智能抓取在保存候选导师时对同一批候选连续失败 2 次，或任意保存失败累计 4 次时，立即让任务进入 `failed`，停止模型继续重试。

**架构：** 在 `CrawlToolContext` 中保存单次运行内的保存失败预算状态；保存工具对 rejected 批次记录稳定身份指纹并返回预算信息，达到阈值时抛出内部异常；`crawl_job_runtime` 捕获该异常，记录 trace 并把任务和当前 run 标记为 failed。本计划不实现部分保存。

**技术栈：** Python 3.12、FastAPI 后端、SQLAlchemy AsyncSession、unittest、uv。

---

## 文件结构

- 修改：`backend/app/services/crawler_tools.py`
  - 定义保存失败预算常量、状态对象、批次指纹函数、预算记录函数和 `CrawlJobSaveBudgetExceeded` 异常。
  - 在 `save_candidate_batch` 中接入预算记录；保存成功或停止导致 `saved_count=0` 不计入失败预算。
- 修改：`backend/app/agents/faculty_crawler_agent.py`
  - 让模型可见的保存结果包含预算字段。
  - 对 `_validate_professor_candidate_batch` 产生的 schema rejected 结果接入预算记录。
- 修改：`backend/app/services/crawl_job_runtime.py`
  - 捕获 `CrawlJobSaveBudgetExceeded`，写入 trace，并把任务和当前 run 标记为 failed。
- 修改：`backend/test/test_crawler_tools.py`
  - 覆盖批次指纹、同批连续失败、累计失败、成功清零、停止任务不计入预算、`save_candidate_batch` 集成行为。
- 修改：`backend/test/test_faculty_crawler_agent.py`
  - 覆盖 `_format_save_batch_result_for_model` 对预算字段的透传。
- 修改：`backend/test/test_crawl_job_runtime.py`
  - 覆盖运行时捕获保存失败熔断异常后标记 failed 和记录 trace。

## 成功标准

- 同一批候选连续 rejected 第二次时抛出 `CrawlJobSaveBudgetExceeded`。
- 四个不同批次累计 rejected 第四次时抛出 `CrawlJobSaveBudgetExceeded`。
- 一次成功保存会清空同批连续失败计数，但不清空累计失败次数。
- 任务暂停或取消导致 `saved_count=0` 不增加失败预算。
- 运行时捕获熔断异常后，`CrawlJob.status` 和当前 `CrawlJobRun.status` 都是 `failed`，`error_message` 包含熔断原因和最近失败摘要。
- 现有成功保存、暂停、取消、无候选失败路径不回归。

---

### 任务 1：保存失败预算核心

**文件：**
- 修改：`backend/test/test_crawler_tools.py`
- 修改：`backend/app/services/crawler_tools.py`

- [ ] **步骤 1：编写失败的预算核心测试**

在 `backend/test/test_crawler_tools.py` 的 `from app.services.crawler_tools import (...)` 导入列表中加入：

```python
    CrawlJobSaveBudgetExceeded,
    record_save_batch_failure,
    record_save_batch_success,
    save_candidate_batch_fingerprint,
```

在 `CrawlerToolTests` 类内靠近现有保存相关测试前加入这些测试：

```python
    def _budget_test_ctx(self) -> CrawlToolContext:
        return CrawlToolContext(
            job_id=1,
            start_url="https://cs.example.edu/faculty",
            university="示例大学",
            school="计算机学院",
            session_factory=_FakeSessionFactory(),  # type: ignore[arg-type]
        )

    def test_save_candidate_batch_fingerprint_ignores_order_and_non_identity_fields(self) -> None:
        first = save_candidate_batch_fingerprint(
            [
                {
                    "name": " 张三 ",
                    "email": "ZHANG@EXAMPLE.EDU",
                    "profile_url": "https://example.edu/zhang",
                    "field_confidence": {"name": 0.2},
                    "evidence": {"summary": "第一次"},
                },
                ProfessorCandidatePayload(
                    name="李四",
                    email="li@example.edu",
                    profile_url="https://example.edu/li",
                    evidence={"summary": "页面"},
                ),
            ]
        )
        second = save_candidate_batch_fingerprint(
            [
                {
                    "name": "李四",
                    "email": "li@example.edu",
                    "profile_url": "https://example.edu/li",
                    "recent_papers": ["Paper A"],
                },
                {
                    "name": "张三",
                    "email": "zhang@example.edu",
                    "profile_url": "https://example.edu/zhang",
                    "field_confidence": {"name": 0.9},
                    "evidence": {"summary": "第二次"},
                },
            ]
        )

        self.assertEqual(first, second)
        self.assertEqual(len(first), 12)

    def test_record_save_batch_failure_trips_same_batch_limit_on_second_failure(self) -> None:
        ctx = self._budget_test_ctx()
        candidates = [{"name": "张三", "email": "zhang@example.edu"}]
        failed_items = [{"index": 0, "name": "张三", "reason": "name: Field required"}]

        first = record_save_batch_failure(ctx, candidates, failed_items)

        self.assertTrue(first["retry_allowed"])
        self.assertEqual(first["consecutive_same_batch_failures"], 1)
        self.assertEqual(first["total_save_failures"], 1)
        self.assertIsNone(first["terminal_reason"])

        with self.assertRaises(CrawlJobSaveBudgetExceeded) as raised:
            record_save_batch_failure(ctx, candidates, failed_items)

        self.assertIn("同一候选批次连续保存失败 2 次", str(raised.exception))
        self.assertEqual(raised.exception.same_batch_save_failures, 2)
        self.assertEqual(raised.exception.total_save_failures, 2)
        self.assertIn("name: Field required", raised.exception.latest_failure_summary)

    def test_record_save_batch_failure_trips_total_limit_on_fourth_distinct_batch(self) -> None:
        ctx = self._budget_test_ctx()

        for index in range(3):
            result = record_save_batch_failure(
                ctx,
                [{"name": f"老师{index}", "email": f"teacher{index}@example.edu"}],
                [{"index": 0, "name": f"老师{index}", "reason": "字段类型错误"}],
            )
            self.assertTrue(result["retry_allowed"])

        with self.assertRaises(CrawlJobSaveBudgetExceeded) as raised:
            record_save_batch_failure(
                ctx,
                [{"name": "老师4", "email": "teacher4@example.edu"}],
                [{"index": 0, "name": "老师4", "reason": "字段类型错误"}],
            )

        self.assertIn("候选保存失败累计达到 4 次", str(raised.exception))
        self.assertEqual(raised.exception.same_batch_save_failures, 1)
        self.assertEqual(raised.exception.total_save_failures, 4)

    def test_record_save_batch_success_clears_same_batch_counter_without_resetting_total(self) -> None:
        ctx = self._budget_test_ctx()
        record_save_batch_failure(
            ctx,
            [{"name": "张三", "email": "zhang@example.edu"}],
            [{"index": 0, "name": "张三", "reason": "字段类型错误"}],
        )

        record_save_batch_success(ctx)

        self.assertIsNone(ctx.save_failure_budget.last_failed_save_fingerprint)
        self.assertEqual(ctx.save_failure_budget.same_batch_save_failures, 0)
        self.assertEqual(ctx.save_failure_budget.total_save_failures, 1)
        self.assertIsNone(ctx.save_failure_budget.last_save_failure_summary)
```

- [ ] **步骤 2：运行预算核心测试验证失败**

运行：

```powershell
cd backend
uv run python -m unittest test.test_crawler_tools.CrawlerToolTests.test_save_candidate_batch_fingerprint_ignores_order_and_non_identity_fields test.test_crawler_tools.CrawlerToolTests.test_record_save_batch_failure_trips_same_batch_limit_on_second_failure test.test_crawler_tools.CrawlerToolTests.test_record_save_batch_failure_trips_total_limit_on_fourth_distinct_batch test.test_crawler_tools.CrawlerToolTests.test_record_save_batch_success_clears_same_batch_counter_without_resetting_total
```

预期：FAIL，错误包含 `cannot import name 'CrawlJobSaveBudgetExceeded'` 或 `NameError`，因为预算类型和函数还没有实现。

- [ ] **步骤 3：实现预算核心类型、指纹和记录函数**

在 `backend/app/services/crawler_tools.py` 顶部导入区增加：

```python
import hashlib
from typing import Any, Literal, NotRequired, TypedDict
```

保留已有导入，删除原来的这一行：

```python
from typing import Any, Literal, TypedDict
```

在现有常量区加入：

```python
SAVE_SAME_BATCH_FAILURE_LIMIT = 2
SAVE_TOTAL_FAILURE_LIMIT = 4
SAME_BATCH_SAVE_FAILURE_REASON = (
    "同一候选批次连续保存失败 2 次，已停止以避免继续消耗 token"
)
TOTAL_SAVE_FAILURE_REASON = (
    "候选保存失败累计达到 4 次，已停止以避免继续消耗 token"
)
```

把 `CandidateBatchSaveResult` 改成包含可选预算字段：

```python
class CandidateBatchSaveResult(TypedDict):
    batch_status: Literal["saved", "rejected"]
    attempted_count: int
    saved_count: int
    failed_count: int
    failed_items: list[CandidateBatchFailure]
    total_saved_count: int
    retry_allowed: NotRequired[bool]
    failure_fingerprint: NotRequired[str | None]
    consecutive_same_batch_failures: NotRequired[int]
    total_save_failures: NotRequired[int]
    terminal_reason: NotRequired[str | None]
```

在 `CandidateBatchSaveResult` 后加入：

```python
class SaveFailureBudgetFields(TypedDict):
    retry_allowed: bool
    failure_fingerprint: str | None
    consecutive_same_batch_failures: int
    total_save_failures: int
    terminal_reason: str | None


@dataclass
class SaveFailureBudgetState:
    last_failed_save_fingerprint: str | None = None
    same_batch_save_failures: int = 0
    total_save_failures: int = 0
    last_save_failure_summary: str | None = None
```

在 `CrawlToolContext` 中追加字段：

```python
    save_failure_budget: SaveFailureBudgetState = field(default_factory=SaveFailureBudgetState)
```

在 `CrawlJobCanceled` 后加入：

```python
class CrawlJobSaveBudgetExceeded(RuntimeError):
    """Raised internally when repeated candidate save failures exceed the retry budget."""

    def __init__(
        self,
        *,
        terminal_reason: str,
        failure_fingerprint: str,
        same_batch_save_failures: int,
        total_save_failures: int,
        latest_failure_summary: str,
    ) -> None:
        self.terminal_reason = terminal_reason
        self.failure_fingerprint = failure_fingerprint
        self.same_batch_save_failures = same_batch_save_failures
        self.total_save_failures = total_save_failures
        self.latest_failure_summary = latest_failure_summary
        super().__init__(f"抓取结果未成功保存：{terminal_reason}。最近失败：{latest_failure_summary}")
```

在异常定义后加入预算函数：

```python
def save_candidate_batch_fingerprint(candidates: Sequence[object]) -> str:
    identities = sorted(_candidate_identity(candidate) for candidate in candidates)
    raw = "\n".join(identities)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12]


def record_save_batch_failure(
    ctx: CrawlToolContext,
    candidates: Sequence[object],
    failed_items: Sequence[CandidateBatchFailure],
) -> SaveFailureBudgetFields:
    fingerprint = save_candidate_batch_fingerprint(candidates)
    state = ctx.save_failure_budget
    if state.last_failed_save_fingerprint == fingerprint:
        state.same_batch_save_failures += 1
    else:
        state.last_failed_save_fingerprint = fingerprint
        state.same_batch_save_failures = 1

    state.total_save_failures += 1
    summary = _summarize_save_failure(failed_items)
    state.last_save_failure_summary = summary

    terminal_reason: str | None = None
    if state.same_batch_save_failures >= SAVE_SAME_BATCH_FAILURE_LIMIT:
        terminal_reason = SAME_BATCH_SAVE_FAILURE_REASON
    elif state.total_save_failures >= SAVE_TOTAL_FAILURE_LIMIT:
        terminal_reason = TOTAL_SAVE_FAILURE_REASON

    fields: SaveFailureBudgetFields = {
        "retry_allowed": terminal_reason is None,
        "failure_fingerprint": fingerprint,
        "consecutive_same_batch_failures": state.same_batch_save_failures,
        "total_save_failures": state.total_save_failures,
        "terminal_reason": terminal_reason,
    }
    if terminal_reason is not None:
        raise CrawlJobSaveBudgetExceeded(
            terminal_reason=terminal_reason,
            failure_fingerprint=fingerprint,
            same_batch_save_failures=state.same_batch_save_failures,
            total_save_failures=state.total_save_failures,
            latest_failure_summary=summary,
        )
    return fields


def record_save_batch_success(ctx: CrawlToolContext) -> None:
    state = ctx.save_failure_budget
    state.last_failed_save_fingerprint = None
    state.same_batch_save_failures = 0
    state.last_save_failure_summary = None


def _candidate_identity(candidate: object) -> str:
    return "|".join(
        (
            f"name={_candidate_identity_value(candidate, 'name')}",
            f"email={_candidate_identity_value(candidate, 'email')}",
            f"profile_url={_candidate_identity_value(candidate, 'profile_url')}",
        )
    )


def _candidate_identity_value(candidate: object, key: str) -> str:
    if isinstance(candidate, dict):
        value = candidate.get(key)
    else:
        value = getattr(candidate, key, None)
    if value is None:
        return ""
    return str(value).strip().lower()


def _summarize_save_failure(failed_items: Sequence[CandidateBatchFailure]) -> str:
    if not failed_items:
        return "保存失败但未返回字段原因"
    parts: list[str] = []
    for item in failed_items[:3]:
        name = item.get("name") or f"index={item['index']}"
        parts.append(f"{name}: {item['reason']}")
    if len(failed_items) > 3:
        parts.append(f"另有 {len(failed_items) - 3} 项失败")
    return "；".join(parts)
```

- [ ] **步骤 4：运行预算核心测试验证通过**

运行：

```powershell
cd backend
uv run python -m unittest test.test_crawler_tools.CrawlerToolTests.test_save_candidate_batch_fingerprint_ignores_order_and_non_identity_fields test.test_crawler_tools.CrawlerToolTests.test_record_save_batch_failure_trips_same_batch_limit_on_second_failure test.test_crawler_tools.CrawlerToolTests.test_record_save_batch_failure_trips_total_limit_on_fourth_distinct_batch test.test_crawler_tools.CrawlerToolTests.test_record_save_batch_success_clears_same_batch_counter_without_resetting_total
```

预期：PASS，输出包含：

```text
Ran 4 tests
OK
```

- [ ] **步骤 5：提交预算核心变更**

运行：

```powershell
git add backend/app/services/crawler_tools.py backend/test/test_crawler_tools.py
git commit -m "feat(抓取): 增加保存失败预算状态"
```

预期：提交成功，提交内容只包含上述两个文件。

---

### 任务 2：保存工具接入失败预算

**文件：**
- 修改：`backend/test/test_crawler_tools.py`
- 修改：`backend/app/services/crawler_tools.py`

- [ ] **步骤 1：编写保存工具集成失败测试**

在 `CrawlerHttpToolTests` 的保存批次测试附近加入：

```python
    async def test_save_candidate_batch_trips_same_batch_failure_budget(self) -> None:
        async with _RealCrawlerSessionHarness() as harness:
            job_id = await harness.create_job()
            ctx = CrawlToolContext(
                job_id=job_id,
                start_url="https://cs.example.edu/faculty",
                university="示例大学",
                school="计算机学院",
                session_factory=harness.session_factory,
            )
            candidates = [ProfessorCandidatePayload(name="", email="bad@example.edu")]

            result = await save_candidate_batch(ctx, candidates)

            self.assertEqual(result["batch_status"], "rejected")
            self.assertTrue(result["retry_allowed"])
            self.assertIsNotNone(result["failure_fingerprint"])
            self.assertEqual(result["consecutive_same_batch_failures"], 1)
            self.assertEqual(result["total_save_failures"], 1)
            self.assertIsNone(result["terminal_reason"])

            with self.assertRaises(CrawlJobSaveBudgetExceeded) as raised:
                await save_candidate_batch(ctx, candidates)

            self.assertIn("同一候选批次连续保存失败 2 次", str(raised.exception))
            self.assertEqual(await harness.count_rows(CrawlCandidate), 0)

    async def test_save_candidate_batch_does_not_count_stopped_job_as_failure(self) -> None:
        async with _RealCrawlerSessionHarness() as harness:
            job_id = await harness.create_job()
            async with harness.session_factory() as session:
                job = await session.get(CrawlJob, job_id)
                assert job is not None
                job.status = CrawlJobStatus.CANCELED.value
                await session.commit()

            ctx = CrawlToolContext(
                job_id=job_id,
                start_url="https://cs.example.edu/faculty",
                university="示例大学",
                school="计算机学院",
                session_factory=harness.session_factory,
            )

            result = await save_candidate_batch(
                ctx,
                [ProfessorCandidatePayload(name="张三", email="zhang@example.edu")],
            )

            self.assertEqual(result["batch_status"], "saved")
            self.assertEqual(result["saved_count"], 0)
            self.assertEqual(ctx.save_failure_budget.total_save_failures, 0)
            self.assertEqual(ctx.save_failure_budget.same_batch_save_failures, 0)
            self.assertEqual(await harness.count_rows(CrawlCandidate), 0)
```

- [ ] **步骤 2：运行保存工具集成测试验证失败**

运行：

```powershell
cd backend
uv run python -m unittest test.test_crawler_tools.CrawlerHttpToolTests.test_save_candidate_batch_trips_same_batch_failure_budget test.test_crawler_tools.CrawlerHttpToolTests.test_save_candidate_batch_does_not_count_stopped_job_as_failure
```

预期：FAIL，第一条测试中的 rejected 返回值缺少 `retry_allowed`，第二次同批 rejected 不会抛出 `CrawlJobSaveBudgetExceeded`。

- [ ] **步骤 3：在 `save_candidate_batch` 中接入预算记录**

把 `backend/app/services/crawler_tools.py` 中 `save_candidate_batch` 的 `if failed_items:` 分支替换为：

```python
    if failed_items:
        budget_fields = record_save_batch_failure(ctx, candidates, failed_items)
        return {
            "batch_status": "rejected",
            "attempted_count": len(candidates),
            "saved_count": 0,
            "failed_count": len(failed_items),
            "failed_items": failed_items,
            "total_saved_count": await count_saved_candidates(ctx),
            **budget_fields,
        }
```

把同函数的成功返回块替换为：

```python
    saved = await _save_normalized_candidate_payloads(ctx, payloads)
    record_save_batch_success(ctx)
    return {
        "batch_status": "saved",
        "attempted_count": len(candidates),
        "saved_count": len(saved),
        "failed_count": 0,
        "failed_items": [],
        "total_saved_count": await count_saved_candidates(ctx),
        "retry_allowed": True,
        "failure_fingerprint": None,
        "consecutive_same_batch_failures": 0,
        "total_save_failures": ctx.save_failure_budget.total_save_failures,
        "terminal_reason": None,
    }
```

调整 `test_save_candidate_batch_returns_counts_without_candidate_details`，在已有断言后加入：

```python
            self.assertTrue(result["retry_allowed"])
            self.assertIsNone(result["failure_fingerprint"])
            self.assertEqual(result["consecutive_same_batch_failures"], 0)
            self.assertEqual(result["total_save_failures"], 0)
            self.assertIsNone(result["terminal_reason"])
```

调整 `test_save_candidate_batch_rejects_entire_batch_when_one_item_fails`，在已有 rejected 断言后加入：

```python
            self.assertTrue(result["retry_allowed"])
            self.assertIsNotNone(result["failure_fingerprint"])
            self.assertEqual(result["consecutive_same_batch_failures"], 1)
            self.assertEqual(result["total_save_failures"], 1)
            self.assertIsNone(result["terminal_reason"])
```

- [ ] **步骤 4：运行保存工具测试验证通过**

运行：

```powershell
cd backend
uv run python -m unittest test.test_crawler_tools.CrawlerHttpToolTests.test_save_candidate_batch_returns_counts_without_candidate_details test.test_crawler_tools.CrawlerHttpToolTests.test_save_candidate_batch_rejects_entire_batch_when_one_item_fails test.test_crawler_tools.CrawlerHttpToolTests.test_save_candidate_batch_trips_same_batch_failure_budget test.test_crawler_tools.CrawlerHttpToolTests.test_save_candidate_batch_does_not_count_stopped_job_as_failure
```

预期：PASS，输出包含：

```text
Ran 4 tests
OK
```

- [ ] **步骤 5：提交保存工具集成变更**

运行：

```powershell
git add backend/app/services/crawler_tools.py backend/test/test_crawler_tools.py
git commit -m "feat(抓取): 接入候选保存失败熔断"
```

预期：提交成功，提交内容只包含上述两个文件。

---

### 任务 3：Agent 保存工具返回预算信息

**文件：**
- 修改：`backend/test/test_faculty_crawler_agent.py`
- 修改：`backend/app/agents/faculty_crawler_agent.py`

- [ ] **步骤 1：编写 Agent 结果格式测试**

在 `FacultyCrawlerAgentSaveResultTests` 中加入：

```python
    def test_format_save_batch_result_for_model_includes_budget_metadata_when_present(self) -> None:
        result = _format_save_batch_result_for_model(
            {
                "batch_status": "rejected",
                "attempted_count": 1,
                "saved_count": 0,
                "failed_count": 1,
                "failed_items": [{"index": 0, "name": "张三", "reason": "name: Field required"}],
                "total_saved_count": 0,
                "retry_allowed": True,
                "failure_fingerprint": "abc123def456",
                "consecutive_same_batch_failures": 1,
                "total_save_failures": 1,
                "terminal_reason": None,
            }
        )

        self.assertEqual(result["retry_allowed"], True)
        self.assertEqual(result["failure_fingerprint"], "abc123def456")
        self.assertEqual(result["consecutive_same_batch_failures"], 1)
        self.assertEqual(result["total_save_failures"], 1)
        self.assertIsNone(result["terminal_reason"])
```

- [ ] **步骤 2：运行 Agent 格式测试验证失败**

运行：

```powershell
cd backend
uv run python -m unittest test.test_faculty_crawler_agent.FacultyCrawlerAgentSaveResultTests.test_format_save_batch_result_for_model_includes_budget_metadata_when_present
```

预期：FAIL，错误为 `KeyError: 'retry_allowed'`，因为格式化函数还没有透传预算字段。

- [ ] **步骤 3：让格式化函数透传预算字段**

把 `backend/app/agents/faculty_crawler_agent.py` 中 `_format_save_batch_result_for_model` 替换为：

```python
def _format_save_batch_result_for_model(result: CandidateBatchSaveResult) -> dict[str, Any]:
    formatted: dict[str, Any] = {
        "batch_status": result["batch_status"],
        "attempted_count": result["attempted_count"],
        "saved_count": result["saved_count"],
        "failed_count": result["failed_count"],
        "failed_items": result["failed_items"],
        "total_saved_count": result["total_saved_count"],
    }
    for key in (
        "retry_allowed",
        "failure_fingerprint",
        "consecutive_same_batch_failures",
        "total_save_failures",
        "terminal_reason",
    ):
        if key in result:
            formatted[key] = result[key]
    return formatted
```

- [ ] **步骤 4：运行 Agent 格式测试验证通过**

运行：

```powershell
cd backend
uv run python -m unittest test.test_faculty_crawler_agent.FacultyCrawlerAgentSaveResultTests.test_format_save_batch_result_for_model_is_compact test.test_faculty_crawler_agent.FacultyCrawlerAgentSaveResultTests.test_format_save_batch_result_for_model_includes_budget_metadata_when_present
```

预期：PASS，输出包含：

```text
Ran 2 tests
OK
```

- [ ] **步骤 5：接入 schema rejected 分支的预算记录**

在 `backend/app/agents/faculty_crawler_agent.py` 的 crawler tools 导入列表中加入：

```python
    record_save_batch_failure,
```

把 `save_professor_candidates` 中 `if failed_items:` 分支替换为：

```python
        if failed_items:
            budget_fields = record_save_batch_failure(ctx, candidates, failed_items)
            return _format_save_batch_result_for_model(
                {
                    "batch_status": "rejected",
                    "attempted_count": len(candidates),
                    "saved_count": 0,
                    "failed_count": len(failed_items),
                    "failed_items": failed_items,
                    "total_saved_count": await count_saved_candidates(ctx),
                    **budget_fields,
                }
            )
```

说明：`record_save_batch_failure` 达到阈值时会抛出 `CrawlJobSaveBudgetExceeded`，这里不捕获，让运行时统一处理。

- [ ] **步骤 6：运行 Agent 测试验证通过**

运行：

```powershell
cd backend
uv run python -m unittest test.test_faculty_crawler_agent
```

预期：PASS，输出包含：

```text
Ran 7 tests
OK
```

实际测试数可能随已有测试变化增加；判断标准是 `OK`。

- [ ] **步骤 7：提交 Agent 集成变更**

运行：

```powershell
git add backend/app/agents/faculty_crawler_agent.py backend/test/test_faculty_crawler_agent.py
git commit -m "feat(抓取): 向模型返回保存失败预算"
```

预期：提交成功，提交内容只包含上述两个文件。

---

### 任务 4：运行时捕获熔断异常并标记任务失败

**文件：**
- 修改：`backend/test/test_crawl_job_runtime.py`
- 修改：`backend/app/services/crawl_job_runtime.py`

- [ ] **步骤 1：编写运行时失败测试**

在 `backend/test/test_crawl_job_runtime.py` 的 crawler tools 导入列表中加入：

```python
    CrawlJobSaveBudgetExceeded,
```

在 `CrawlJobRuntimeTests` 中靠近其他 failed 流程测试处加入：

```python
    async def test_run_queued_crawl_job_fails_when_save_failure_budget_is_exceeded(self) -> None:
        job_id = await self._create_default_profile_and_job()

        async def fake_run(
            ctx: CrawlToolContext,
            llm_profile: LLMProfile,
            trace_callback=None,
        ) -> dict[str, object]:
            _ = ctx, llm_profile, trace_callback
            raise CrawlJobSaveBudgetExceeded(
                terminal_reason="同一候选批次连续保存失败 2 次，已停止以避免继续消耗 token",
                failure_fingerprint="abc123def456",
                same_batch_save_failures=2,
                total_save_failures=2,
                latest_failure_summary="张三: name: Field required",
            )

        with patch(
            "app.services.crawl_job_runtime.run_faculty_crawler_agent",
            new=fake_run,
        ):
            processed = await run_queued_crawl_jobs_once(self.session_factory)

        self.assertEqual(processed, 1)
        job = await self._get_job(job_id)
        self.assertEqual(job.status, CrawlJobStatus.FAILED.value)
        self.assertIsNotNone(job.error_message)
        self.assertIn("同一候选批次连续保存失败 2 次", job.error_message)
        self.assertIn("张三: name: Field required", job.error_message)
        run = await self._get_current_run(job_id)
        self.assertEqual(run.status, CrawlJobStatus.FAILED.value)
        self.assertEqual(run.error_message, job.error_message)
        self.assertIsNotNone(run.finished_at)

        trace_events = [item for item in job.agent_trace or [] if isinstance(item, dict)]
        breaker_events = [
            item
            for item in trace_events
            if item.get("event_type") == "save_failure_circuit_breaker"
        ]
        self.assertEqual(len(breaker_events), 1)
        self.assertEqual(breaker_events[0]["failure_fingerprint"], "abc123def456")
        self.assertEqual(breaker_events[0]["consecutive_same_batch_failures"], 2)
        self.assertEqual(breaker_events[0]["total_save_failures"], 2)
```

- [ ] **步骤 2：运行运行时失败测试验证失败**

运行：

```powershell
cd backend
uv run python -m unittest test.test_crawl_job_runtime.CrawlJobRuntimeTests.test_run_queued_crawl_job_fails_when_save_failure_budget_is_exceeded
```

预期：FAIL，任务会被通用 `except Exception` 标记 failed，但不会写入 `save_failure_circuit_breaker` trace。

- [ ] **步骤 3：运行时导入并捕获熔断异常**

在 `backend/app/services/crawl_job_runtime.py` 的 crawler tools 导入列表中加入：

```python
    CrawlJobSaveBudgetExceeded,
```

在 `run_queued_crawl_jobs_once` 的 `except asyncio.CancelledError:` 前加入：

```python
    except CrawlJobSaveBudgetExceeded as exc:
        await _emit_trace_event(
            trace_callback,
            {
                "event_type": "save_failure_circuit_breaker",
                "message": str(exc),
                "failure_fingerprint": exc.failure_fingerprint,
                "consecutive_same_batch_failures": exc.same_batch_save_failures,
                "total_save_failures": exc.total_save_failures,
                "terminal_reason": exc.terminal_reason,
                "latest_failure_summary": exc.latest_failure_summary,
                "created_at": datetime.now(UTC).isoformat(),
            },
        )
        await _mark_job_failed(session_factory, job_id, str(exc))
```

- [ ] **步骤 4：运行运行时失败测试验证通过**

运行：

```powershell
cd backend
uv run python -m unittest test.test_crawl_job_runtime.CrawlJobRuntimeTests.test_run_queued_crawl_job_fails_when_save_failure_budget_is_exceeded
```

预期：PASS，输出包含：

```text
Ran 1 test
OK
```

- [ ] **步骤 5：运行运行时相关回归测试**

运行：

```powershell
cd backend
uv run python -m unittest test.test_crawl_job_runtime
```

预期：PASS，输出包含：

```text
OK
```

- [ ] **步骤 6：提交运行时集成变更**

运行：

```powershell
git add backend/app/services/crawl_job_runtime.py backend/test/test_crawl_job_runtime.py
git commit -m "fix(抓取): 保存失败熔断时标记任务失败"
```

预期：提交成功，提交内容只包含上述两个文件。

---

### 任务 5：全量验证与最终提交检查

**文件：**
- 检查：`backend/app/services/crawler_tools.py`
- 检查：`backend/app/agents/faculty_crawler_agent.py`
- 检查：`backend/app/services/crawl_job_runtime.py`
- 检查：`backend/test/test_crawler_tools.py`
- 检查：`backend/test/test_faculty_crawler_agent.py`
- 检查：`backend/test/test_crawl_job_runtime.py`

- [ ] **步骤 1：运行保存工具完整测试**

运行：

```powershell
cd backend
uv run python -m unittest test.test_crawler_tools
```

预期：PASS，输出包含：

```text
OK
```

如果输出中存在 asyncio slow task 调试信息，只要最终结果是 `OK`，该测试通过。

- [ ] **步骤 2：运行 Agent 完整测试**

运行：

```powershell
cd backend
uv run python -m unittest test.test_faculty_crawler_agent
```

预期：PASS，输出包含：

```text
OK
```

- [ ] **步骤 3：运行运行时完整测试**

运行：

```powershell
cd backend
uv run python -m unittest test.test_crawl_job_runtime
```

预期：PASS，输出包含：

```text
OK
```

- [ ] **步骤 4：运行三组回归测试一次性确认**

运行：

```powershell
cd backend
uv run python -m unittest test.test_crawler_tools test.test_faculty_crawler_agent test.test_crawl_job_runtime
```

预期：PASS，输出包含：

```text
OK
```

- [ ] **步骤 5：检查差异只包含本功能文件**

运行：

```powershell
git diff -- backend/app/services/crawler_tools.py backend/app/agents/faculty_crawler_agent.py backend/app/services/crawl_job_runtime.py backend/test/test_crawler_tools.py backend/test/test_faculty_crawler_agent.py backend/test/test_crawl_job_runtime.py
git status --short
```

预期：
- `git diff -- ...` 只展示保存失败熔断相关代码和测试。
- `git status --short` 可能包含工作区已有的无关改动；不要 stage 无关文件。

- [ ] **步骤 6：确认最终提交状态**

如果任务 1 到任务 4 已各自提交，运行：

```powershell
git log --oneline -4
```

预期：能看到这些提交主题：

```text
fix(抓取): 保存失败熔断时标记任务失败
feat(抓取): 向模型返回保存失败预算
feat(抓取): 接入候选保存失败熔断
feat(抓取): 增加保存失败预算状态
```

如果任务 1 到任务 4 没有分步提交，则运行：

```powershell
git add backend/app/services/crawler_tools.py backend/app/agents/faculty_crawler_agent.py backend/app/services/crawl_job_runtime.py backend/test/test_crawler_tools.py backend/test/test_faculty_crawler_agent.py backend/test/test_crawl_job_runtime.py
git commit -m "fix(抓取): 增加保存失败熔断"
```

预期：提交成功，提交内容只包含上述六个文件。

---

## 执行注意

- 不要修改数据库 schema；预算状态只保存在单次运行内的 `CrawlToolContext`。
- 不要实现部分保存；rejected 批次仍按整批失败处理。
- 不要把暂停或取消引起的 `saved_count=0` 记录为保存失败。
- 不要把 `field_confidence`、`evidence`、`recent_papers` 纳入批次指纹。
- 当前工作区有多处无关未提交改动，提交时必须显式列出本计划涉及的文件。
