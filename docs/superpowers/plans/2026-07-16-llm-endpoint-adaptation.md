# LLM 端点协议自适应实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 自动学习并持久化每个 Base URL 与模型应使用的 `chat_completions` 或 `responses` 端点，在明确协议失效后重新适应，并让普通生成、thinking adaptation 与导师爬虫共享结果。

**架构：** 新增独立端点缓存和服务，以 `(api_base_url, model_name)` 为键管理探测、并发锁和条件失效；`llm_runtime` 保留 HTTP 与协议转换职责，并通过统一的 `LLMRuntimeAdaptation` 组合端点和 thinking 参数。thinking 缓存增加端点维度，所有生产工作流显式传递统一适应结果，爬虫使用 LangChain 的 `use_responses_api`。

**技术栈：** Python 3.12、FastAPI、SQLAlchemy AsyncSession、Alembic、httpx、LangChain `ChatOpenAI`、unittest、uv

---

## 文件结构

### 新建

- `backend/app/models/llm_endpoint_adaptation_cache.py`：端点适应缓存 ORM 模型。
- `backend/app/services/llm_endpoint_adaptation.py`：端点类型、响应外壳分类、缓存 CRUD、锁和候选顺序。
- `backend/alembic/versions/20260716_llm_endpoint_adaptation.py`：创建端点缓存并把 thinking 缓存改为端点隔离。
- `backend/test/test_llm_endpoint_adaptation.py`：端点服务的单元与并发测试。

### 修改

- `backend/app/models/__init__.py`：导出新缓存模型。
- `backend/app/models/thinking_adaptation_cache.py`：增加 `endpoint_kind` 并调整唯一约束。
- `backend/app/services/llm_runtime.py`：单端点请求、协议错误、统一适应和一次性失效重试。
- `backend/app/services/thinking_adaptation.py`：按端点缓存、固定端点探测和条件失效。
- `backend/app/api/llm_profiles.py`：连接诊断使用统一适应结果。
- `backend/app/services/task_runtime.py`：匹配、草稿与重写路径传递适应结果和数据库会话。
- `backend/app/services/test_compose_runtime.py`：测试写信使用统一适应结果。
- `backend/app/services/crawler_tools.py`：爬虫上下文持有统一适应结果。
- `backend/app/services/crawl_job_runtime.py`：爬虫任务启动时统一适应并处理 Agent 端点失效。
- `backend/app/services/crawler_v2_chunk_worker.py`：chunk LLM 调用使用统一适应结果。
- `backend/app/services/crawler_v2_page_worker.py`：页面抽取 LLM 调用使用统一适应结果。
- `backend/app/services/crawler_v2_enrichment_worker.py`：详情补全 LLM 调用使用统一适应结果。
- `backend/app/agents/faculty_crawler_agent.py`：按端点设置 `use_responses_api`。
- `backend/test/test_llm_runtime.py`：协议请求、回退、重试与诊断测试。
- `backend/test/test_thinking_adaptation.py`：端点隔离和失效重学测试。
- `backend/test/test_api_endpoints.py`：连接诊断和缓存提交集成测试。
- `backend/test/test_faculty_crawler_agent.py`：LangChain 端点选择测试。
- `backend/test/test_crawl_job_runtime.py`：Agent 失效后单次重建测试。
- `backend/test/test_database_schema.py`：新表、唯一键和迁移恢复测试。
- `docs/database_table_design.md`：记录两张适应缓存表及其键。

## 任务 1：建立数据库模型和可恢复迁移

**文件：**
- 创建：`backend/app/models/llm_endpoint_adaptation_cache.py`
- 创建：`backend/alembic/versions/20260716_llm_endpoint_adaptation.py`
- 修改：`backend/app/models/thinking_adaptation_cache.py`
- 修改：`backend/app/models/__init__.py`
- 修改：`backend/test/test_database_schema.py`
- 修改：`docs/database_table_design.md`

- [ ] **步骤 1：编写新表和 thinking 三元唯一键的失败测试**

在 `test_runtime_tables_and_columns_are_created` 中加入：

```python
self.assertIn("llm_endpoint_adaptation_cache", table_names)
self.assertEqual(
    {
        "id",
        "api_base_url",
        "model_name",
        "learned_endpoint_kind",
        "probed_at",
        "created_at",
        "updated_at",
    },
    set(self._get_columns("llm_endpoint_adaptation_cache")),
)
self.assertIn(
    "endpoint_kind",
    self._get_columns("thinking_adaptation_cache"),
)
self.assertEqual(
    self._get_index_columns(
        "thinking_adaptation_cache",
        "uq_thinking_adaptation_cache_endpoint",
    ),
    ["api_base_url", "model_name", "endpoint_kind"],
)
```

新增迁移测试：从 `20260709_professor_dashboard_indexes` 创建一条旧 thinking 缓存，升级 head 后断言旧缓存清空、新列非空且同一模型可插入两个端点。

- [ ] **步骤 2：运行数据库测试并确认失败**

运行：

```bash
cd backend
uv run python -m unittest test.test_database_schema.DatabaseSchemaTests.test_runtime_tables_and_columns_are_created
```

预期：FAIL，缺少 `llm_endpoint_adaptation_cache` 或 `endpoint_kind`。

- [ ] **步骤 3：实现 ORM 模型**

新模型采用以下字段和约束：

```python
class LLMEndpointAdaptationCache(Base):
    __tablename__ = "llm_endpoint_adaptation_cache"
    __table_args__ = (
        UniqueConstraint(
            "api_base_url",
            "model_name",
            name="uq_llm_endpoint_adaptation_cache_target",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    api_base_url: Mapped[str] = mapped_column(String(500), nullable=False)
    model_name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    learned_endpoint_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    probed_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, server_default=text("CURRENT_TIMESTAMP"))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, server_default=text("CURRENT_TIMESTAMP"))
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, server_default=text("CURRENT_TIMESTAMP"), onupdate=utc_now)
```

给 `ThinkingAdaptationCache` 增加 `endpoint_kind: Mapped[str] = mapped_column(String(32), nullable=False)`，唯一约束命名为 `uq_thinking_adaptation_cache_endpoint`。

- [ ] **步骤 4：实现 Alembic upgrade 和 downgrade**

设置：

```python
revision = "20260716_llm_endpoint_adaptation"
down_revision = "20260709_professor_dashboard_indexes"
```

upgrade 创建端点表；使用 SQLite `CREATE TABLE IF NOT EXISTS`、`DROP TABLE IF EXISTS` 和临时表重建 thinking 缓存，不复制旧缓存。downgrade 删除端点表，并用同样的临时表方式恢复旧的二元唯一键。每个阶段都允许 migration 在未写入 Alembic revision 前重复执行。

- [ ] **步骤 5：运行迁移测试**

运行：

```bash
cd backend
uv run python -m unittest test.test_database_schema
```

预期：PASS，Alembic 只有一个 head，旧缓存被安全清空，三元唯一键生效。

- [ ] **步骤 6：更新数据库设计文档并提交**

在 `docs/database_table_design.md` 中补充端点缓存表，并把 thinking 表唯一键改为三元组。

```bash
git add backend/app/models backend/alembic/versions/20260716_llm_endpoint_adaptation.py backend/test/test_database_schema.py docs/database_table_design.md
git commit -m "feat(backend): 添加 LLM 端点适应缓存"
```

## 任务 2：实现端点分类、持久化和并发保护

**文件：**
- 创建：`backend/app/services/llm_endpoint_adaptation.py`
- 创建：`backend/test/test_llm_endpoint_adaptation.py`

- [ ] **步骤 1：编写分类和缓存失败测试**

覆盖以下公开接口及精确签名：

- `EndpointKind = Literal["chat_completions", "responses"]`
- `classify_response_envelope(endpoint_kind: EndpointKind, data: object) -> Literal["valid", "other_endpoint", "invalid"]`
- `get_cached_endpoint_kind(session: AsyncSession, *, api_base_url: str, model_name: str) -> EndpointKind | None`
- `record_endpoint_adaptation(session: AsyncSession, *, api_base_url: str, model_name: str, endpoint_kind: EndpointKind) -> None`
- `invalidate_endpoint_adaptation(session: AsyncSession, *, api_base_url: str, model_name: str, failed_endpoint_kind: EndpointKind) -> bool`
- `endpoint_candidates(failed_endpoint_kind: EndpointKind | None = None) -> tuple[EndpointKind, EndpointKind]`

测试必须断言：Chat `choices[].message` 有效；Responses `output` 或 `output_text` 有效；收到另一种外壳返回 `other_endpoint`；upsert 更新同一行；条件失效不删除并发写入的新端点；首次候选为 Chat，失效后另一端点优先。

- [ ] **步骤 2：运行新测试并确认导入失败**

```bash
cd backend
uv run python -m unittest test.test_llm_endpoint_adaptation
```

预期：ERROR，`app.services.llm_endpoint_adaptation` 尚不存在。

- [ ] **步骤 3：实现响应外壳分类**

分类只验证协议外壳，不验证最终正文：

```python
def _is_chat_envelope(data: object) -> bool:
    if not isinstance(data, dict):
        return False
    choices = data.get("choices")
    return isinstance(choices, list) and bool(choices) and all(
        isinstance(choice, dict) and isinstance(choice.get("message"), dict)
        for choice in choices
    )

def _is_responses_envelope(data: object) -> bool:
    if not isinstance(data, dict):
        return False
    return isinstance(data.get("output_text"), str) or isinstance(data.get("output"), list)
```

先检查目标外壳，再检查另一外壳，最后返回 `invalid`。

- [ ] **步骤 4：实现缓存 CRUD 和锁**

使用 SQLite upsert；条件失效执行：

```python
result = await session.execute(
    delete(LLMEndpointAdaptationCache).where(
        LLMEndpointAdaptationCache.api_base_url == api_base_url,
        LLMEndpointAdaptationCache.model_name == model_name,
        LLMEndpointAdaptationCache.learned_endpoint_kind == failed_endpoint_kind,
    )
)
return bool(result.rowcount)
```

维护以 `(api_base_url, model_name)` 为键的进程内 `asyncio.Lock`，提供异步上下文管理器；调用方必须在锁内二次查询缓存。

- [ ] **步骤 5：运行测试并提交**

```bash
cd backend
uv run python -m unittest test.test_llm_endpoint_adaptation
git add app/services/llm_endpoint_adaptation.py test/test_llm_endpoint_adaptation.py
git commit -m "feat(backend): 实现 LLM 端点适应状态"
```

预期：全部 PASS。

## 任务 3：把 LLM HTTP 请求拆成指定端点请求

**文件：**
- 修改：`backend/app/services/llm_runtime.py`
- 修改：`backend/test/test_llm_runtime.py`

- [ ] **步骤 1：为协议错误和指定端点请求编写失败测试**

新增测试断言：

- `_request_completion_endpoint` 传入 `endpoint_kind="chat_completions"` 时只访问 `/chat/completions`。
- `endpoint_kind="responses"` 只访问 `/responses`，并转换 `messages -> input`、`max_tokens -> max_output_tokens`。
- 404、405、501 抛出 `LLMEndpointProtocolError`。
- HTTP 200 且目标外壳无效时抛出 `LLMEndpointProtocolError`，并保存 `response_envelope="invalid"`。
- Chat URL 返回 Responses 外壳时错误标记 `response_envelope="other_endpoint"`。
- 401、403、429、5xx 和网络错误仍抛 `LLMRuntimeError`，不是协议错误。
- 合法 Chat 外壳中 `content=None` 且有 `reasoning_content` 不判定端点失效。

- [ ] **步骤 2：运行聚焦测试确认失败**

```bash
cd backend
uv run python -m unittest \
  test.test_llm_runtime.LLMRuntimeTests.test_chat_endpoint_rejects_responses_envelope \
  test.test_llm_runtime.LLMRuntimeTests.test_responses_endpoint_builds_responses_payload
```

预期：FAIL，缺少指定端点 API 或错误类型。

- [ ] **步骤 3：实现专用协议错误**

```python
class LLMEndpointProtocolError(LLMRuntimeError):
    def __init__(
        self,
        message: str,
        *,
        failed_endpoint_kind: EndpointKind,
        response_envelope: Literal["other_endpoint", "invalid"] | None = None,
        request_url: str | None = None,
        attempted_urls: list[str] | None = None,
        status_code: int | None = None,
        duration_ms: int | None = None,
    ) -> None:
        super().__init__(
            message,
            request_url=request_url,
            attempted_urls=attempted_urls,
            endpoint_kind=failed_endpoint_kind,
            status_code=status_code,
            duration_ms=duration_ms,
        )
        self.failed_endpoint_kind = failed_endpoint_kind
        self.response_envelope = response_envelope
```

- [ ] **步骤 4：实现 `_request_completion_endpoint`**

该函数负责构造一个端点的 URL 和请求体、调用 `_send_llm_http_request`、解析 JSON、验证响应外壳、提取文本与 usage。它不读数据库、不选择第二端点。

状态处理顺序固定为：网络异常、HTTP 协议状态、其他 HTTP 错误、JSON 解码、外壳分类、正文提取。`allow_empty_content=True` 只放宽正文为空，不放宽响应外壳。

- [ ] **步骤 5：保留兼容包装并运行现有测试**

暂时让 `request_chat_completion` 继续按旧顺序调用指定端点函数，保证下一任务接入持久化前测试仍通过。

```bash
cd backend
uv run python -m unittest test.test_llm_runtime
git add app/services/llm_runtime.py test/test_llm_runtime.py
git commit -m "refactor(backend): 拆分 LLM 指定端点请求"
```

预期：现有与新增 LLM runtime 测试全部 PASS。

## 任务 4：组合端点适应和 thinking adaptation

**文件：**
- 修改：`backend/app/services/llm_runtime.py`
- 修改：`backend/app/services/thinking_adaptation.py`
- 修改：`backend/test/test_llm_endpoint_adaptation.py`
- 修改：`backend/test/test_thinking_adaptation.py`
- 修改：`backend/test/test_llm_runtime.py`

- [ ] **步骤 1：编写端点缓存未命中、命中和失效重试测试**

测试 `ensure_llm_runtime_adaptation`：

```python
adaptation = await ensure_llm_runtime_adaptation(session, profile)
self.assertEqual(adaptation.endpoint_kind, "responses")
self.assertEqual(adaptation.thinking_extra_body, {"reasoning_effort": "low"})
```

Responses-only 用例让 Chat 返回 HTTP 200 的 Responses 外壳，再让 `/responses` 成功；断言保存 `responses`。第二次调用断言只使用缓存。缓存端点明确失效时，断言条件失效、另一端点优先、原业务请求只重试一次。

- [ ] **步骤 2：编写 thinking 端点隔离失败测试**

把 `get_cached_extra_body`、`record_thinking_adaptation`、`probe_and_learn_extra_body` 和 `ensure_thinking_adaptation` 的公开签名增加 `endpoint_kind`。测试同一 Base URL 和模型可分别保存 Chat 与 Responses 的不同值，且探测 mock 收到固定端点。

- [ ] **步骤 3：运行测试确认失败**

```bash
cd backend
uv run python -m unittest test.test_llm_endpoint_adaptation test.test_thinking_adaptation test.test_llm_runtime
```

预期：FAIL，统一适应对象和 thinking 端点参数尚未实现。

- [ ] **步骤 4：实现统一适应对象和首次学习**

在 `llm_runtime.py` 定义：

```python
@dataclass(slots=True)
class LLMRuntimeAdaptation:
    endpoint_kind: EndpointKind
    thinking_extra_body: dict[str, object] | None
```

`ensure_llm_runtime_adaptation(session, profile, *, failed_endpoint_kind=None)` 在端点锁内二次查询；未命中时使用最小单轮 probe 调用 `_request_completion_endpoint`，保存端点后调用：

```python
thinking_extra_body = await ensure_thinking_adaptation(
    session,
    profile,
    endpoint_kind=endpoint_kind,
)
```

- [ ] **步骤 5：改造 thinking adaptation**

所有查询和 upsert 加入 `endpoint_kind`。探测候选必须直接调用 `_request_completion_endpoint` 并显式传入 `endpoint_kind=endpoint_kind`，不得调用会自动切换的包装器。增加 `invalidate_thinking_adaptation`，仅删除当前三元组。

- [ ] **步骤 6：实现业务请求一次性重新适应**

`request_chat_completion` 接收：

```python
async def request_chat_completion(
    profile: LLMProfile,
    payload: dict[str, object],
    *,
    session: AsyncSession | None = None,
    adaptation: LLMRuntimeAdaptation | None = None,
    allow_empty_content: bool = False,
) -> ChatCompletionResult:
```

生产路径必须传 `session` 和 `adaptation`。捕获 `LLMEndpointProtocolError` 后只允许一次：条件失效、以失败端点为参数重新学习、使用新端点与 thinking 参数重试。无 session 的兼容路径可执行当前请求，但不得声称已持久化适应结果。

- [ ] **步骤 7：运行三组测试并提交**

```bash
cd backend
uv run python -m unittest test.test_llm_endpoint_adaptation test.test_thinking_adaptation test.test_llm_runtime
git add app/services/llm_runtime.py app/services/thinking_adaptation.py test/test_llm_endpoint_adaptation.py test/test_thinking_adaptation.py test/test_llm_runtime.py
git commit -m "feat(backend): 自适应 LLM 请求端点和思考参数"
```

预期：全部 PASS。

## 任务 5：迁移普通 LLM 工作流到统一适应结果

**文件：**
- 修改：`backend/app/api/llm_profiles.py`
- 修改：`backend/app/services/task_runtime.py`
- 修改：`backend/app/services/test_compose_runtime.py`
- 修改：`backend/app/services/crawler_v2_chunk_worker.py`
- 修改：`backend/app/services/crawler_v2_page_worker.py`
- 修改：`backend/app/services/crawler_v2_enrichment_worker.py`
- 修改：`backend/test/test_api_endpoints.py`
- 修改相关 task、batch、match、crawler v2 测试中的 mock 目标和断言。

- [ ] **步骤 1：先更新连接诊断集成测试**

将现有 thinking cache 提交测试扩展为同时查询 `llm_endpoint_adaptation_cache`。新增 Responses-only 预览测试，模拟 Chat HTTP 200 返回 Responses 外壳，断言：

```python
self.assertTrue(payload["ok"])
self.assertEqual(payload["endpoint_kind"], "responses")
self.assertTrue(payload["request_url"].endswith("/responses"))
```

- [ ] **步骤 2：运行 API 聚焦测试确认失败**

```bash
cd backend
uv run python -m unittest \
  test.test_api_endpoints.ApiEndpointTests.test_llm_profile_preview_test_commits_thinking_adaptation_cache \
  test.test_api_endpoints.ApiEndpointTests.test_llm_profile_preview_adapts_to_responses_on_chat_shape_mismatch
```

预期：FAIL，API 仍只传 `thinking_extra_body`。

- [ ] **步骤 3：替换普通工作流调用**

把每处：

```python
thinking_extra_body = await ensure_thinking_adaptation(session, llm_profile)
```

替换为：

```python
llm_adaptation = await ensure_llm_runtime_adaptation(session, llm_profile)
```

并让 `probe_llm_profile`、`generate_match_evaluation`、`generate_draft_content` 和直接 `request_chat_completion` 调用都接收 `session=session, adaptation=llm_adaptation`。删除生产代码中只传 `thinking_extra_body` 的并行路径。

- [ ] **步骤 4：更新 mock 和聚焦回归测试**

将 patch 目标从各模块的 `ensure_thinking_adaptation` 改为 `ensure_llm_runtime_adaptation`，返回：

```python
LLMRuntimeAdaptation(
    endpoint_kind="chat_completions",
    thinking_extra_body=None,
)
```

运行：

```bash
cd backend
uv run python -m unittest \
  test.test_api_endpoints \
  test.test_batch_draft_generation_runtime \
  test.test_match_analysis_runtime \
  test.test_match_analysis_jobs \
  test.test_concurrency_guards \
  test.test_crawler_v2_chunk_worker \
  test.test_crawler_v2_page_worker \
  test.test_crawler_v2_enrichment_worker
```

预期：全部 PASS。

- [ ] **步骤 5：提交普通工作流迁移**

```bash
git add backend/app/api/llm_profiles.py backend/app/services/task_runtime.py backend/app/services/test_compose_runtime.py backend/app/services/crawler_v2_* backend/test
git commit -m "refactor(backend): 统一传递 LLM 运行时适应结果"
```

提交前用 `git diff --cached --name-only` 确认没有夹带与本任务无关的用户修改。

## 任务 6：让导师爬虫 Agent 跟随并重新学习端点

**文件：**
- 修改：`backend/app/services/crawler_tools.py`
- 修改：`backend/app/services/crawl_job_runtime.py`
- 修改：`backend/app/agents/faculty_crawler_agent.py`
- 修改：`backend/test/test_faculty_crawler_agent.py`
- 修改：`backend/test/test_crawl_job_runtime.py`

- [ ] **步骤 1：编写模型构造失败测试**

patch `ChatOpenAI` 后分别传入两个适应结果，断言：

```python
mock_chat_openai.assert_called_once_with(
    model=profile.model_name,
    api_key=profile.api_key,
    base_url=resolve_base_url(profile.api_base_url),
    temperature=0.2,
    use_responses_api=True,
    extra_body={"reasoning_effort": "low"},
)
```

Chat 用例断言 `use_responses_api=False`。

- [ ] **步骤 2：编写 Agent 单次重建失败测试**

第一次 `run_faculty_crawler_agent` 抛出可分类为 404/405/501 或 OpenAI `APIResponseValidationError` 的协议异常；mock 条件失效和重新适应返回另一端点；第二次成功。断言 Agent 恰好运行两次。第二次仍失败时断言不执行第三次。

- [ ] **步骤 3：运行聚焦测试确认失败**

```bash
cd backend
uv run python -m unittest test.test_faculty_crawler_agent test.test_crawl_job_runtime
```

预期：FAIL，模型尚未设置 Responses 参数且工作流不会重建。

- [ ] **步骤 4：传递完整适应结果**

把 `CrawlToolContext.thinking_extra_body` 替换为 `llm_adaptation`。`build_faculty_crawler_model` 和 `run_faculty_crawler_agent` 接收 `LLMRuntimeAdaptation`，并设置：

```python
use_responses_api=adaptation.endpoint_kind == "responses"
```

`extra_body` 从 `adaptation.thinking_extra_body` 取得。

- [ ] **步骤 5：实现 Agent 协议错误分类和一次重建**

只把 OpenAI SDK 的 `APIStatusError.status_code in {404, 405, 501}` 和 `APIResponseValidationError` 视为端点协议失效。认证、限流、超时和 5xx 保持原错误处理。crawl job 工作单元捕获明确协议失效后条件清缓存、重新适应、重建 Agent 一次。

- [ ] **步骤 6：运行测试并提交**

```bash
cd backend
uv run python -m unittest test.test_faculty_crawler_agent test.test_crawl_job_runtime test.test_crawler_v2_chunk_worker test.test_crawler_v2_page_worker test.test_crawler_v2_enrichment_worker
git add app/agents/faculty_crawler_agent.py app/services/crawler_tools.py app/services/crawl_job_runtime.py test/test_faculty_crawler_agent.py test/test_crawl_job_runtime.py
git commit -m "feat(crawler): 自适应 Agent 模型请求端点"
```

预期：全部 PASS。

## 任务 7：补齐诊断、日志和文档一致性

**文件：**
- 修改：`backend/app/services/llm_runtime.py`
- 修改：`backend/app/api/llm_profiles.py`
- 修改：`backend/test/test_api_endpoints.py`
- 修改：`backend/test/test_operation_log_integration.py`
- 修改：`docs/database_table_design.md`

- [ ] **步骤 1：编写诊断和日志失败测试**

自动切换成功后断言最终 `endpoint_kind` 和 `request_url` 属于 Responses，`attempted_urls` 同时包含 Chat 与 Responses。操作日志只记录脱敏 URL、旧端点、新端点、失效原因和 `retried=true`，不包含 API Key、提示正文或完整响应正文。

- [ ] **步骤 2：运行聚焦测试确认失败**

```bash
cd backend
uv run python -m unittest test.test_api_endpoints test.test_operation_log_integration
```

预期：至少新增日志断言 FAIL。

- [ ] **步骤 3：补齐可观测性实现**

复用现有 URL query/fragment 清理和 LLM runtime 日志函数。自动切换日志字段固定为：

```python
{
    "old_endpoint_kind": failed_endpoint_kind,
    "new_endpoint_kind": adaptation.endpoint_kind,
    "reason": protocol_error.response_envelope or protocol_error.status_code,
    "retried": True,
}
```

错误路径合并两次尝试 URL，最终端点字段永远指向最后实际请求。

- [ ] **步骤 4：运行测试并提交**

```bash
cd backend
uv run python -m unittest test.test_api_endpoints test.test_operation_log_integration
git add app/services/llm_runtime.py app/api/llm_profiles.py test/test_api_endpoints.py test/test_operation_log_integration.py docs/database_table_design.md
git commit -m "feat(backend): 完善 LLM 端点切换诊断"
```

预期：全部 PASS。

## 任务 8：完整验证和最终审查

**文件：**
- 验证所有已修改文件

- [ ] **步骤 1：运行后端格式和导入检查**

```bash
cd backend
uv run python -m compileall app test
```

预期：退出码 0，无语法或导入错误。

- [ ] **步骤 2：运行端点与 thinking 聚焦测试**

```bash
cd backend
uv run python -m unittest \
  test.test_llm_endpoint_adaptation \
  test.test_llm_runtime \
  test.test_thinking_adaptation \
  test.test_faculty_crawler_agent \
  test.test_crawl_job_runtime \
  test.test_api_endpoints \
  test.test_database_schema
```

预期：全部 PASS。

- [ ] **步骤 3：运行完整后端测试**

```bash
cd backend
uv run python -m unittest discover test
```

预期：全部 PASS，无失败或错误。

- [ ] **步骤 4：检查迁移 head 和工作区**

```bash
cd backend
uv run alembic heads
cd ..
git diff --check
git status --short
```

预期：Alembic 只有 `20260716_llm_endpoint_adaptation (head)`；`git diff --check` 无输出；状态中只包含本功能预期文件或用户原有修改。

- [ ] **步骤 5：进行代码审查并修复问题**

重点审查：协议错误是否误吞认证/限流/5xx；所有生产调用是否传 session 和 adaptation；端点切换是否最多一次；thinking 是否始终带 endpoint；Agent 是否只重建一次；日志是否泄露请求内容。

- [ ] **步骤 6：提交最终修正**

若审查产生修正：

```bash
git add \
  backend/app/models/llm_endpoint_adaptation_cache.py \
  backend/app/models/thinking_adaptation_cache.py \
  backend/app/models/__init__.py \
  backend/app/services/llm_endpoint_adaptation.py \
  backend/app/services/llm_runtime.py \
  backend/app/services/thinking_adaptation.py \
  backend/app/api/llm_profiles.py \
  backend/app/agents/faculty_crawler_agent.py \
  backend/app/services/task_runtime.py \
  backend/app/services/test_compose_runtime.py \
  backend/app/services/crawler_tools.py \
  backend/app/services/crawl_job_runtime.py \
  backend/app/services/crawler_v2_chunk_worker.py \
  backend/app/services/crawler_v2_page_worker.py \
  backend/app/services/crawler_v2_enrichment_worker.py \
  backend/alembic/versions/20260716_llm_endpoint_adaptation.py \
  backend/test/test_llm_endpoint_adaptation.py \
  backend/test/test_llm_runtime.py \
  backend/test/test_thinking_adaptation.py \
  backend/test/test_api_endpoints.py \
  backend/test/test_faculty_crawler_agent.py \
  backend/test/test_crawl_job_runtime.py \
  backend/test/test_database_schema.py \
  backend/test/test_operation_log_integration.py \
  docs/database_table_design.md
git commit -m "fix(backend): 加固 LLM 端点自适应"
```

若无修正，不创建空提交。
