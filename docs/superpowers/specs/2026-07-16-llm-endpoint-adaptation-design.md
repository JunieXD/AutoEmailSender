# LLM 端点协议自适应设计

## 背景

系统当前优先请求 OpenAI 兼容的 `POST /chat/completions`，仅在该端点返回 HTTP 404 时尝试 `POST /responses`。部分中转服务不支持 Chat Completions，却可能对该路径返回 HTTP 200 和非 Chat Completions 响应结构，导致系统直接报告“模型响应缺少可解析的文本内容”，无法进入现有 Responses 回退分支。

系统已有 thinking adaptation：按接口地址和模型名称探测模型所需的思考模式参数，并将结果持久化。端点协议适应应采用相同的自动学习和持久化模式，同时补齐明确失效后重新学习的闭环。

## 目标

- 用户只配置 Base URL、API Key 和模型名称，不需要选择接口协议。
- 系统自动学习模型应使用 `chat_completions` 还是 `responses`，并持久化结果。
- 缓存命中后直接使用已学习端点，避免每次重复探测、增加延迟或消耗 token。
- 已学习端点出现明确协议失效时，自动重新适应并保存新结果。
- 普通匹配、草稿生成、连接诊断、测试写信和导师爬虫 Agent 使用同一适应结果。
- thinking adaptation 按端点隔离，避免协议切换后误用另一端点学习到的参数。

## 非目标

- 不在前端增加 `auto / chat_completions / responses` 手动选择项。
- 不支持 OpenAI 兼容协议之外的新原生提供商协议。
- 不把认证失败、限流、网络故障或服务端临时错误当作协议失效。
- 不改变 `GET /models` 的模型列表请求行为。

## 方案选择

采用独立的端点适应缓存，不把端点字段塞入 thinking 缓存或 LLM Profile。

端点能力和 thinking 参数是两个正交维度：前者描述上游支持哪种请求协议，后者描述特定协议下如何关闭或调整思考模式。独立缓存保持职责清晰，同时允许多个 LLM Profile 共享相同 Base URL 和模型的探测结果。

运行时通过统一结果组合两类适应：

```python
@dataclass(slots=True)
class LLMRuntimeAdaptation:
    endpoint_kind: Literal["chat_completions", "responses"]
    thinking_extra_body: dict[str, object] | None
```

统一入口 `ensure_llm_runtime_adaptation(session, profile)` 必须先解析端点，再以该端点解析 thinking 参数。

## 数据模型

新增 `llm_endpoint_adaptation_cache` 表：

| 字段 | 类型 | 约束 | 说明 |
| --- | --- | --- | --- |
| `id` | integer | 主键 | 缓存记录 ID |
| `api_base_url` | varchar(500) | 非空 | 规范化且去除末尾斜杠的 Base URL |
| `model_name` | varchar(255) | 非空 | 模型名称 |
| `learned_endpoint_kind` | varchar(32) | 非空 | `chat_completions` 或 `responses` |
| `probed_at` | datetime | 非空 | 最近一次成功探测时间 |
| `created_at` | datetime | 非空 | 创建时间 |
| `updated_at` | datetime | 非空 | 更新时间 |

唯一约束为 `(api_base_url, model_name)`。API Key 不进入缓存键，因为端点协议通常是服务和模型能力；认证失败也不会修改该缓存。

`thinking_adaptation_cache` 增加非空字段 `endpoint_kind`，唯一约束调整为 `(api_base_url, model_name, endpoint_kind)`。Chat Completions 和 Responses 分别保存自己的 `learned_extra_body`。

旧 thinking 缓存不迁移。旧版本的请求可能已经从 Chat Completions 回退到 Responses，现有记录无法可靠归属到某个端点。该表只保存可再生缓存，升级后重新探测比错误回填更可靠。Alembic 升级前继续使用现有数据库备份机制。

## 协议探测

缓存未命中时，默认按以下顺序探测：

1. `chat_completions`
2. `responses`

重新适应已失效端点时，优先探测另一个端点，再考虑原端点，减少重复命中已知失败路径。

探测请求使用最小单轮提示和较小输出上限。探测成功只要求 HTTP 请求成功且响应外壳属于目标协议，不要求已经获得非空最终文本：

- Chat Completions 响应必须具有合法的 `choices` 列表以及 `choices[].message` 对象。
- Responses 响应必须具有合法的 `output` 列表或字符串类型的 `output_text`。
- Chat 响应中的 `content` 为空但存在 `reasoning_content` 时，仍证明 Chat 协议可用，后续交给 thinking adaptation 处理。
- 请求 Chat 却收到 Responses 外壳，或请求 Responses 却收到 Chat 外壳，属于协议不匹配。
- 两种外壳都不匹配时，当前端点探测失败；只有另一端点成功后才持久化新结果。

探测失败时不写入失败记录，避免暂时故障形成长期负缓存。

## 请求与失效状态机

缓存命中后的业务请求直接使用已学习端点。只有以下信号证明端点协议已经失效：

- HTTP 404、405 或 501。
- HTTP 2xx，但响应外壳不符合当前端点协议。
- HTTP 2xx，且响应外壳明确属于另一种已支持协议。

以下情况不触发端点失效：

- HTTP 401 或 403：认证或授权失败。
- HTTP 429：限流。
- HTTP 5xx：上游临时故障。
- DNS、代理、连接、TLS 或超时错误。
- 已成功提取文本，但文本不符合业务要求的 JSON 结构。
- 响应外壳合法，但思考模型把正文放入 `reasoning_content`。

协议失效必须使用专门的 `LLMEndpointProtocolError` 表达，不依赖用户可见中文错误字符串进行控制流判断。

运行时失效后的处理顺序：

1. 捕获当前端点的 `LLMEndpointProtocolError`。
2. 条件删除仍等于失败端点的缓存记录。
3. 优先探测另一个端点。
4. 持久化成功的新端点。
5. 按新端点解析 thinking adaptation。
6. 使用新的端点和 thinking 参数重试原业务请求一次。
7. 重试仍失败时返回包含全部尝试 URL 和最终端点的诊断错误，不再循环切换。

一次逻辑业务请求最多执行一次协议切换。首次失败请求可能已经被中转站计费，这是无法完全消除的兼容成本；持久化成功结果可避免后续请求重复承担该成本。

## 并发控制

端点适应按 `(api_base_url, model_name)` 使用进程内异步锁。获取锁后必须再次查询数据库，避免多个并发任务在首次未命中时重复探测。

数据库写入使用唯一约束和 SQLite upsert。失效操作使用条件删除：仅当数据库中的 `learned_endpoint_kind` 仍等于本次失败端点时才删除。这样较慢的旧请求不会删除另一个并发请求刚保存的新结果。

thinking adaptation 使用 `(api_base_url, model_name, endpoint_kind)` 的独立键。端点变化不删除另一端点的 thinking 记录；切回旧端点时可以复用该端点以前成功学习的参数。若该参数本身出现 thinking 协议错误，则只失效当前三元组对应的 thinking 记录并重新学习。

## 代码边界

### `backend/app/services/llm_endpoint_adaptation.py`

- 定义端点类型、缓存读取、upsert 和条件失效操作。
- 管理按缓存键划分的进程内异步锁。
- 实现首次探测和失效后的候选排序。
- 将 HTTP 状态和响应外壳分类为协议成功、协议失效或非协议错误。

### `backend/app/services/llm_runtime.py`

- 将“请求一个指定端点”与“选择端点”拆分。
- `request_chat_completion` 使用统一适应结果发送请求。
- 在明确协议失效时协调重新适应，并限制为一次切换。
- 保持现有 `request_url`、`attempted_urls`、`endpoint_kind`、`status_code` 和 `duration_ms` 诊断字段兼容。
- 继续负责 Chat 与 Responses 请求体转换、文本提取和 token usage 统一。

### `backend/app/services/thinking_adaptation.py`

- 所有缓存操作增加 `endpoint_kind`。
- 探测请求固定使用调用方提供的端点，不允许在内部隐式切换协议。
- 增加当前端点 thinking 结果的条件失效和重新学习。

### 工作流入口

任务、测试写信、连接诊断和爬虫工作流统一调用 `ensure_llm_runtime_adaptation()`，并向下传递完整适应结果。旧的只传递 `thinking_extra_body` 的调用逐步替换，不保留两套并行控制流。

### `backend/app/agents/faculty_crawler_agent.py`

构造 `ChatOpenAI` 时显式设置：

```python
use_responses_api=adaptation.endpoint_kind == "responses"
```

当前锁定的 `langchain-openai 1.1.12` 已支持该参数。爬虫运行中若出现明确端点协议失效，当前 Agent 运行停止，工作流失效旧缓存并用新适应结果重建 Agent；单次工作单元最多重建一次。

## API 与前端行为

不增加用户配置字段或手动端点选择控件。

连接诊断继续返回并显示：

- `endpoint_kind`
- `request_url`
- `attempted_urls`
- `status_code`
- `duration_ms`
- token usage

适应成功后，诊断中的端点标签显示实际使用的 `chat_completions` 或 `responses`。模型列表仍独立请求 `GET /models`，不参与端点适应。

## 错误处理与可观测性

- 首次适应失败时，错误包含所有已尝试 URL，但不泄露 API Key 或请求正文。
- 自动切换后仍失败时，保留第一次协议失败和第二次请求失败的摘要。
- 运行日志记录缓存键的脱敏 Base URL、旧端点、新端点、失效原因和是否重试，不记录响应正文全文。
- `endpoint_kind` 始终反映最终实际请求端点，不能继续沿用函数名称中的 `chat_completion` 作为事实来源。

## 测试策略

### 端点适应单元测试

- 首次优先探测 Chat 并持久化。
- Chat 返回 404、405、501 时探测 Responses。
- Chat 返回 HTTP 200 但外壳错误时探测 Responses。
- Chat URL 返回 Responses 外壳时识别协议不匹配。
- 缓存命中后不重复探测。
- 已失效端点切换、保存并只重试业务请求一次。
- 401、403、429、5xx、网络、TLS 和超时错误不清除缓存。
- 并发首次访问只执行一轮探测。
- 条件失效不删除并发请求已更新的端点。

### thinking adaptation 测试

- 相同 Base URL 和模型在两个端点下保存独立结果。
- 切换端点不会读取另一端点的 `extra_body`。
- 当前端点 thinking 参数失效后只重学当前三元组。
- 多轮探测始终固定使用指定端点。

### 集成测试

- 连接诊断在 Responses-only 中转站上自动适应并返回 `endpoint_kind=responses`。
- 匹配分析、草稿生成和测试写信复用已学习端点。
- 导师爬虫在 Responses 模式下设置 `use_responses_api=True`。
- 爬虫遇到明确协议失效时最多重建一次 Agent。
- 原有 Chat Completions 服务行为和诊断字段不回归。

### 迁移测试

- 从当前数据库版本升级后创建端点缓存表。
- thinking 缓存唯一键变为三元组，旧缓存被安全清空。
- upgrade、downgrade 和中断后重复执行保持幂等。
- 完整迁移链仍可从空数据库升级到 head。

## 验收标准

- 对仅支持 Responses API、且 `/chat/completions` 返回 HTTP 200 非 Chat 结构的中转站，连接诊断能够自动切换并成功。
- 首次成功适应后，后续业务请求不再访问错误端点。
- 上游端点能力发生变化后，下一次明确协议失败会触发一次重新适应，并保存新结果。
- 普通生成与导师爬虫使用相同的端点适应结果。
- thinking 参数不会跨端点误用。
- 认证、限流、网络和临时服务错误不会造成缓存抖动。
- 用户无需新增任何配置。
