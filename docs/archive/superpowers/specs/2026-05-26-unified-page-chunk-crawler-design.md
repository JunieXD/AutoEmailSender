# 统一 Page Chunk 抓取链路设计

## 背景

当前智能抓取链路同时存在两类页面正文入口：

- `crawl_page`：抓取页面后生成 `crawl_page_chunks`，再由 Agent 通过 chunk 工具提取候选。
- `investigate_with_browser` / `save_professor_candidates`：浏览器调查可能直接把页面正文返回给 Agent，Agent 再尝试用旧保存工具写入候选。

这会形成双轨流程。最近几次本地抓取暴露出以下问题：

- Agent 在已有待处理 chunk 时使用 `investigate_with_browser` 获取完整页面内容，绕开 chunk 边界。
- Agent 把浏览器返回的页面正文误判为「非 chunk 场景」，反复调用 `save_professor_candidates`。
- `save_professor_candidates` 虽然返回 `chunk_required`，但模型可能继续使用错误工具，造成 token 消耗和任务卡住。
- `source_kind = "list_chunk"` 只适合列表页，不适合详情页 chunk 或浏览器正文 chunk。

本设计目标是把所有页面正文统一纳入 page chunk 状态机，避免模型在多条保存路径之间摇摆。

## 目标

- 所有页面正文候选都必须来自 `crawl_page_chunks`。
- `crawl_page` 和 `investigate_with_browser` 获取到页面正文后都进入同一套 chunk 流程。
- Agent 只通过 `claim_next_page_chunk` 和 `submit_page_chunk_candidates` 从页面正文中保存候选。
- 详情页也可以被 chunk，并通过字段级 merge 合并同一导师的多 chunk 信息。
- 新数据统一写入 `source_kind = "page_chunk"`。
- 旧数据中的 `source_kind = "list_chunk"` 继续兼容读取。

## 非目标

- 不用 URL 规则判断页面是不是列表页、目录页或详情页。
- 不在后端识别导师候选或判断页面语义。
- 不在本阶段重构数据库表名 `crawl_page_chunks`。
- 不在本阶段实现独立的页面类型分类器。

## 核心设计

### 统一页面正文入口

任何工具只要拿到 `PageSnapshot` 且正文非空，就调用同一套 chunk 创建逻辑：

```text
PageSnapshot(status=succeeded, text/html 非空)
→ create_chunks_for_successful_page_snapshot
→ created_chunks > 0
→ 返回 status="chunked"
→ Agent 调用 claim_next_page_chunk
→ Agent 调用 submit_page_chunk_candidates
```

`crawl_page` 和 `investigate_with_browser` 的差异只体现在获取页面的方式，不体现在后续候选保存路径。

### 小内容处理

如果工具返回的是短调查摘要，而不是页面正文，可以直接返回给 Agent。

第一版以 `PageSnapshot` 作为边界：

- 返回 `PageSnapshot` 且 `text/html` 非空：进入 chunk。
- 返回普通状态、错误信息或短 `message`：直接返回。

如果后续 `browser_investigate` 支持独立的摘要对象，可以继续沿用这一规则。

### 已有 chunk 保护

如果目标 URL 在当前 job 中已有 chunk 记录：

- 仍有 `pending`、`processing` 或 `split_required`：返回 `status="chunked"` 或 `chunk_required`，要求先领取 chunk。
- 只有 `completed`、`no_candidates` 或 `superseded`：返回 `status="already_completed"`，不返回页面正文。

这条规则适用于 `crawl_page` 和 `investigate_with_browser`。

## Agent 工具列表

统一 page chunk 链路后，Agent 只暴露 4 个工具：

```text
crawl_page
investigate_with_browser
claim_next_page_chunk
submit_page_chunk_candidates
```

`submit_chunk_candidates` 和 `save_professor_candidates` 不再暴露给 Agent，也不保留模型可调用 wrapper。页面正文候选没有旧保存入口。

### `crawl_page`

职责：常规页面抓取入口。

工具描述：

```text
抓取入口 URL 同域内的新页面。成功获取页面正文后，后端会生成 page chunk，并返回 status="chunked"。
返回 chunked 后，必须调用 claim_next_page_chunk 领取页面片段；不要直接根据 crawl_page 或历史记忆保存候选。
如果页面已完成处理，工具只返回 already_completed，不返回页面正文。
```

`crawl_page` 负责：

```text
URL → 页面正文 → page chunk
```

它不负责直接保存候选。

### `investigate_with_browser`

职责：浏览器抓取兜底。

工具描述：

```text
当 crawl_page 普通抓取内容明显不足、页面疑似动态渲染，或需要浏览器执行后才能看到内容时，使用浏览器调查同域页面。
如果浏览器获取到页面正文，后端同样会生成 page chunk，并返回 status="chunked"。
返回 chunked 后，必须调用 claim_next_page_chunk；不要直接根据浏览器返回内容保存候选。
当前任务存在待处理 chunk 时，本工具不会返回页面正文，必须先处理已有 chunk。
```

`investigate_with_browser` 负责：

```text
动态或疑难页面 → 浏览器获取正文 → page chunk
```

它是浏览器抓取兜底，但不是保存入口。它不能把页面正文直接交给模型保存。

### `claim_next_page_chunk`

职责：领取下一个待处理页面片段。

工具描述：

```text
领取当前任务中下一个 pending/processing page chunk。
模型每次只处理本工具返回的当前 chunk 内容。
如果返回 status="empty"，表示当前没有待处理页面片段；如没有明确的新页面需要探索，应结束任务并总结。
```

`claim_next_page_chunk` 负责：

```text
chunk 队列 → 当前 chunk 内容
```

它不负责抓新页面，也不负责保存候选。

### `submit_page_chunk_candidates`

职责：提交当前 page chunk 中识别出的候选。

工具描述：

```text
提交 claim_next_page_chunk 返回的当前 page chunk 中识别出的导师候选。
所有来自网页正文的候选都必须通过本工具提交。
单次最多提交 10 个候选。
如果当前 chunk 没有候选，提交空 candidates 并将 chunk_status 设为 no_candidates。
只有当前 chunk 正文中明确还有超过 10 个已看见但未提交的候选时，才使用 chunk_status="too_many_candidates"。
```

新工具参数：

```json
{
  "chunk_id": "...",
  "chunk_status": "completed | no_candidates | too_many_candidates | uncertain",
  "has_unsubmitted_candidates_in_current_chunk": false,
  "candidates": []
}
```

`submit_page_chunk_candidates` 负责：

```text
当前 chunk 候选 → 保存/合并 → 更新 chunk 状态
```

它不负责抓页面、探索新 URL，也不能处理非当前 chunk 的候选。

## 候选来源语义

### 新写入值

```text
source_kind = "page_chunk"
```

适用于：

- 列表页 chunk。
- 目录页 chunk。
- 详情页 chunk。
- 浏览器获取正文后生成的 chunk。

### 旧数据兼容

```text
source_kind = "list_chunk"
```

旧数据继续可读。展示层可以把 `list_chunk` 和 `page_chunk` 都归类为「页面片段」。

### 字段合并优先级

`page_chunk` 应纳入现有 source priority。第一版可以让 `page_chunk` 与 `list_chunk` 同级，避免改变旧合并行为。

如果后续需要区分详情页与列表页，可以由模型提交可选字段：

```json
{
  "source_context": "list | detail | unknown"
}
```

本阶段不引入该字段，避免后端页面类型判断。

## 详情页 Chunk 策略

详情页也按页面正文处理，不特殊判断。

### 小详情页

如果详情页正文较短，动态切分会生成 1 个 chunk：

```text
crawl_page(详情页)
→ status="chunked"
→ claim_next_page_chunk
→ submit_page_chunk_candidates，通常提交 1 位候选
```

### 大详情页

如果详情页正文较长，可能生成多个 chunk：

```text
chunk 1：姓名、邮箱、职称
chunk 2：研究方向
chunk 3：论文或项目
```

Agent 可以在多个 chunk 中重复提交同一导师。后端通过 email、profile_url、identity_key 和现有 merge 逻辑合并字段。

### 合并要求

- 新字段补全旧字段。
- 冲突字段进入 `conflicts`。
- `evidence`、`field_confidence` 继续合并。
- 重复提交同一批无新增信息时，沿用 duplicate loop 防护。

## `crawl_page` 行为

`crawl_page` 保持主抓取入口角色：

```text
crawl_page(url)
→ 检查 URL chunk 状态
→ 已完成则 already_completed
→ 有待处理 chunk 则 chunked/chunk_required
→ 否则抓取页面
→ 成功且正文非空则生成 chunk
→ 返回 chunked
```

它不直接返回大段页面正文给模型。

## `investigate_with_browser` 行为

`investigate_with_browser` 仍是兜底调查工具，但不能绕过 chunk。

### 当前有待处理 chunk

```json
{
  "status": "chunk_required",
  "next_instruction": "请先调用 claim_next_page_chunk，并使用 submit_page_chunk_candidates 处理当前 chunk。"
}
```

不调用浏览器，不返回正文。

### 没有待处理 chunk

调用浏览器调查。

如果返回 `PageSnapshot` 且正文非空：

```text
create_chunks_for_successful_page_snapshot
→ 返回 status="chunked"
```

如果浏览器调查失败或只返回短状态：

```text
返回短状态，不生成 chunk。
```

## 旧保存入口移除

`save_professor_candidates` 从 Agent 工具列表移除，不再作为模型可调用工具存在。

需要保留的是底层保存服务，例如 `save_candidate_batch`。它不是 Agent 工具，而是 `submit_page_chunk_candidates` 的内部依赖，用于校验、去重、合并和写入 `crawl_candidates`。

统一后的保存路径只有一条：

```text
claim_next_page_chunk
→ submit_page_chunk_candidates
→ save_candidate_batch（内部服务）
```

如果未来仍有非页面正文的小型结构化候选需要保存，应通过单独的明确接口设计，不复用旧的 `save_professor_candidates` Agent 工具。

## Agent Prompt 约束

系统提示需要统一为 page chunk 语义：

- 页面正文候选必须通过 `submit_page_chunk_candidates` 提交。
- Agent 不再拥有 `save_professor_candidates` 工具；不要在提示词、工具描述或示例中提及旧保存入口。
- `investigate_with_browser` 不能绕过 chunk。
- 当前存在待处理 chunk 时，必须先 `claim_next_page_chunk`。
- `too_many_candidates` 只用于当前 chunk 正文中明确还有超过 10 个已看见候选。
- 下一页、下一个 chunk、分页导航、详情页链接或浏览器整页视图，不能作为当前 chunk 过密的依据。

## 数据流示例

### 列表页

```text
crawl_page(导师列表页)
→ chunked
→ claim_next_page_chunk
→ submit_page_chunk_candidates(10 人以内)
→ 发现下一页 URL
→ 当前 chunk 完成后 crawl_page(下一页)
```

### 详情页

```text
crawl_page(导师详情页)
→ chunked
→ claim_next_page_chunk
→ submit_page_chunk_candidates(同一导师的详情字段)
→ 后端 merge 到已有候选
```

### 浏览器兜底

```text
investigate_with_browser(动态页面)
→ PageSnapshot 正文非空
→ chunked
→ claim_next_page_chunk
→ submit_page_chunk_candidates
```

## 错误处理

- 重复访问已完成页面：返回 `already_completed`，不返回正文。
- 当前有待处理 chunk 时调用浏览器：返回 `chunk_required`。
- Agent 工具列表中不存在旧保存工具，因此页面正文候选没有绕过 page chunk 的保存路径。
- 提交超过 10 个候选：触发拆分或拒绝，保持现有上限。
- `chunk_status = "too_many_candidates"`：只拆分当前 chunk，不代表页面或下一页还有更多候选。

## 测试计划

### 工具命名和 Schema

- Agent 工具列表包含 `submit_page_chunk_candidates`。
- Agent 工具列表不包含 `submit_chunk_candidates`。
- `submit_page_chunk_candidates` 参数包含 `has_unsubmitted_candidates_in_current_chunk`。

### Source Kind

- 新候选写入 `source_kind = "page_chunk"`。
- 旧 `list_chunk` 数据读取和展示不受影响。
- `page_chunk` 参与字段合并优先级。

### Browser Chunk 化

- 当前有 pending chunk 时，`investigate_with_browser` 不调用浏览器，返回 `chunk_required`。
- 没有 pending chunk 且浏览器返回正文时，生成 page chunk，返回 `status="chunked"`。
- 浏览器正文不直接出现在工具返回中。

### 详情页 Chunk

- 详情页正文生成 chunk。
- 详情页 chunk 提交同一导师时合并到已有候选。
- 多个详情页 chunk 可补全研究方向、论文、证据。

### 旧保存入口移除

- Agent 工具列表不包含 `save_professor_candidates`。
- Agent 工具描述和系统提示不包含 `save_professor_candidates`。
- 底层 `save_candidate_batch` 仍可被 `submit_page_chunk_candidates` 调用。

### 拆分语义

- 刚好提交 10 个候选且 `chunk_status="completed"` 不触发拆分。
- 只有 `chunk_status="too_many_candidates"` 或候选数量超过限制时触发拆分。

## 迁移步骤

1. 新增 `submit_page_chunk_candidates` 工具，复用现有提交逻辑。
2. 将写入候选的 `source_kind` 改为 `page_chunk`。
3. 更新 Agent prompt 和工具描述。
4. 将 `investigate_with_browser` 正文返回改为生成 chunk。
5. 从 Agent 工具列表移除 `submit_chunk_candidates` 和 `save_professor_candidates`。
6. 更新事件摘要、日志过滤和测试断言。
7. 更新规格文档中所有 `list_chunk` 和旧工具名表述。
8. 运行后端 crawler 相关测试。
9. 用本地真实站点执行一次抓取，验证工具调用只包含 4 个统一工具，且不再出现旧保存工具循环。

## 风险与缓解

### 风险：工具重命名导致模型短期不适应

缓解：系统 prompt 和工具描述只出现新工具名，不再出现旧工具名。旧工具名不暴露给 Agent。

### 风险：详情页多一轮工具调用

缓解：动态 balanced target 会让小详情页成为单 chunk；只多一次 claim/submit，但换来统一状态机和可恢复性。

### 风险：`source_kind` 改名影响历史数据

缓解：新写 `page_chunk`，旧读兼容 `list_chunk`。UI 展示统一为「页面片段」。

### 风险：浏览器调查的小结也被 chunk 化

缓解：第一版只 chunk `PageSnapshot` 的非空正文。未来如果浏览器工具返回独立 summary 对象，可直接返回 summary。

## 自检

- 本设计不依赖 URL、标题或 DOM 规则判断页面类型。
- 本设计保留模型自主判断页面是否值得访问。
- 页面正文保存路径统一为 page chunk。
- 详情页、列表页、浏览器正文都进入同一状态机。
- 旧保存工具不再暴露给 Agent，也不保留模型可调用兼容入口。

