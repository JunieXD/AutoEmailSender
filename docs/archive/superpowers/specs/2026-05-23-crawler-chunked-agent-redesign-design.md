# 通用抓取 Agent Chunk 化重构设计

## 背景

`crawl-job-9` 的调试日志暴露了智能抓取在「列表页候选保存」阶段的结构性问题：

- JSONL 调试日志本身没有行级重复，但 `raw_event` 中出现了大量重复的 `save_professor_candidates` 调用。
- 同一批导师（例如同一 `profile_url`）被模型反复提交，且保存工具对无邮箱候选只按 `email` 去重，导致重复候选实际落库。
- 当前页面缓存只能避免重复联网抓取同一 URL，不能阻止模型从已返回的页面内容中反复抽取和保存同一批老师。
- 保存历史压缩只保留最近 30 条候选摘要，能降低上下文 token 消耗，但会丢失早期保存历史，模型仍可能进入「我已经获取全部页面，现在继续保存尚未保存候选」的循环。
- 批次限制为 10 个候选是必要的，它避免模型一次输出过长 JSON 导致截断或格式损坏，但也要求系统必须有可靠的进度管理机制，不能依赖模型记忆。

本设计面向一次较大的抓取功能重构：保留通用 Agent 能力，不针对特定学校页面写候选抽取规则，同时把页面处理进度、候选幂等保存和循环控制从模型上下文中移到后端状态机。

## 目标

- 保持抓取 Agent 的通用性，适配结构千变万化的高校网站。
- 不要求后端按学校页面结构识别「谁是老师」，候选识别仍由 LLM 完成。
- 控制模型每次只处理一个小范围页面片段，降低上下文 token 和重复提交概率。
- 保留「单次最多 10 个候选」的输出限制，避免大 JSON 截断。
- 支持列表页 chunk 级处理状态，已完成 chunk 不再把正文返回给模型。
- 支持 chunk 过密时自动拆分并重跑，避免一个 chunk 超过 10 个候选导致漏保存。
- 保存层对候选做幂等保存和字段级合并，防止重复数据污染。
- 拒绝无邮箱且无详情页链接的候选，避免产生无法联系、无法补全、难以去重的低价值记录。
- 通过短反馈、重复熔断和状态机约束，减少 Agent 继续无效调用保存工具造成的 token 浪费。

## 非目标

- 不实现针对特定学校、学院或页面模板的候选解析规则。
- 不要求后端用规则判断某个文本块是否是导师条目。
- 不取消 LLM 在候选识别、字段抽取和证据判断中的角色。
- 不要求列表页一次性输出全部候选。
- 不在第一版解决所有动态网页、登录区、反爬和跨域站点问题。
- 不把详情页批量候选保存纳入 chunk 机制；详情页原则上按单人补全处理。

## 核心思路

本次重构采用「后端状态机 + 通用页面切片 + LLM 单 chunk 抽取 + 保存层幂等」的架构。

### 现有模式的问题

当前 Agent 的职责过重：

1. 决定抓哪些页面。
2. 读取完整页面内容。
3. 判断哪些候选已经保存。
4. 分批调用保存工具。
5. 依赖最近 30 条保存摘要避免重复。

这种模式在页面较长、候选较多、模型上下文被压缩后容易出错。页面缓存虽然能避免重复联网抓取，但缓存命中后仍会把页面内容返回给模型，模型仍可能从同一页面内容中再次保存同一批候选。

### 新模式

新模式把「流程推进」交给后端，把「候选识别」留给模型：

1. 后端抓取页面并生成页面快照。
2. 对列表页或目录页，后端执行通用 chunk 切片。
3. 后端维护每个 chunk 的处理状态。
4. 模型每次只处理一个未完成 chunk，最多返回 10 个候选。
5. 保存工具对候选执行准入、去重、合并和短反馈。
6. chunk 根据模型结果进入 `completed`、`split_required`、`failed` 等状态。
7. 已完成或被拆分取代的 chunk 不再向模型返回正文。

一句话概括：后端不解析候选，但后端控制模型能看到哪些内容，以及每段内容是否已经处理过。

## 页面类型策略

### 列表页 / 目录页

列表页使用 chunk 机制。典型页面包括：

- 师资队伍列表。
- 按系所、职称、方向分页的导师目录。
- 包含多名候选的搜索结果页。
- 页面内同时出现多名教师卡片、表格行或链接集合的页面。

列表页处理目标是发现候选，并尽可能提取：

- 姓名。
- 邮箱。
- 职称。
- 学校、学院、系所。
- 研究方向摘要。
- 详情页链接 `profile_url`。
- 来源页 `source_url`。
- 证据和置信度。

### 详情页

详情页原则上不走候选批量 chunk 保存，因为详情页通常只对应一名老师。

详情页主要用于补全：

- 邮箱。
- 研究方向。
- 近期论文。
- 个人主页。
- 更可靠的职称、院系和证据。

如果详情页正文特别长，可以使用普通文本摘要或字段抽取切片，但它不使用「一个 chunk 保存多个候选」的流程。

## Chunk 切片策略

### 设计原则

chunk 切片必须通用，不能依赖特定学校页面结构。后端不判断某个块是不是老师，只做结构保留和大小控制。

切片原则：

- 优先保留 HTML 结构，不直接按固定字符数硬切。
- 保留链接锚文本和 `href`，避免模型无法输出 `profile_url`。
- 尽量避免把姓名、职称、邮箱、详情页链接切散。
- 每个 chunk 目标大小控制在可稳定输出 10 个候选以内。
- 相邻 chunk 保留少量 overlap，用于降低边界截断导致的信息丢失。
- 每个 chunk 有稳定 `chunk_hash`，同一页面内容重复处理时可识别。

### 通用切片流程

1. **页面清洗**
   - 去掉 `script`、`style`、明显导航、页脚和无内容节点。
   - 保留正文、列表、表格、卡片、段落和链接。
   - 对链接输出锚文本和绝对 URL。

2. **结构块提取**
   - 按通用块级节点提取文本块，例如 `li`、`tr`、`article`、`section`、`div`、`p`。
   - 这一步只保留结构和顺序，不判断块是否是老师。
   - 对无法可靠提取结构的页面，退化为段落和换行分块。

3. **按大小合并**
   - 将小结构块按页面顺序合并成 chunk。
   - 普通列表页初始目标大小设为 2,000 tokens，软上限设为 2,800 tokens，硬上限设为 3,200 tokens。
   - 如果单个结构块超过上限，继续按子节点、段落、换行或句子拆分。

4. **Overlap 处理**
   - 列表页相邻 chunk 默认保留 180 tokens overlap，最小 120 tokens，最大 240 tokens。
   - overlap 中重复出现的候选由保存层合并或跳过。
   - 边界附近抽取到的字段应标记为边界风险，后续更完整证据可以覆盖。

5. **生成元数据**

```json
{
  "chunk_id": "job-9-page-3-chunk-2",
  "source_url": "http://example.edu/faculty/index.htm",
  "page_fingerprint": "sha256:...",
  "chunk_index": 2,
  "chunk_hash": "sha256:...",
  "token_estimate": 1840,
  "text_start_offset": 3200,
  "text_end_offset": 7600,
  "link_count": 12,
  "overlap_prefix": true,
  "overlap_suffix": true,
  "split_depth": 0,
  "parent_chunk_id": null
}
```


### 参数取值依据

基于 `crawl-job-9.jsonl` 的真实抓取记录，列表页工具返回和模型 token 使用呈现以下特征：

- 入口及分页列表页的工具返回体量大多在约 37,000-70,000 字符，早期一次性放入 5 个列表页后，下一次模型调用的 `prompt_tokens` 从约 21,743 跳到约 86,832，增量约 65,089 tokens。
- 单个列表页约包含 20 个候选；这意味着完整列表页直接进入上下文时，平均每页贡献约 13,000 tokens 量级，远高于一次稳定抽取 10 个候选所需的上下文。
- 保存调用中，10 个候选的 JSON 参数中位长度约 5,107 字符，p90 约 5,635 字符，最大约 6,648 字符；这说明「最多 10 个候选」仍是合理上限，继续放大批次会增加截断风险。
- `index1.htm` 在日志中被模型反复保存，唯一候选数达到约 38 个，说明通用 chunk 不能假设每个列表页只有 20 个候选，也需要支持过密 chunk 自动拆分。

因此第一版参数采用偏保守设置：

| 参数 | 默认值 | 下限 / 上限 | 说明 |
| ---- | ---- | ---- | ---- |
| `target_tokens` | 2,000 | 可调范围 1,600-2,200 | 页面切分的理想平均大小，用于计算动态均衡目标。 |
| `soft_max_tokens` | 2,800 | - | 超过后优先在结构边界切分。 |
| `hard_max_tokens` | 3,200 | - | 兜底硬上限，超过必须拆分。 |
| `overlap_tokens` | 180 | 120-240 | 覆盖边界处姓名、邮箱、详情链接被切断的风险。 |
| `single_chunk_max_tokens` | 2,200 | - | 页面不超过该值时不切分，直接作为一个 chunk。 |
| `min_balanced_target_tokens` | 1,200 | - | 动态均衡目标下限，避免切得过碎。 |
| `max_balanced_target_tokens` | 2,200 | - | 动态均衡目标上限，避免单块过大。 |
| `min_split_tokens` | 500 | - | 低于该大小不再递归拆分，避免碎片化。 |
| `max_split_depth` | 4 | - | 防止过密页面无限拆分。 |

如果一个 2,000 token chunk 仍返回 10 个候选，则说明该页面候选密度较高，应触发自动拆分，而不是提高 chunk 上限。
### 给模型的 chunk 格式

不要直接给模型原始 HTML，也不要给完全丢失链接的纯文本。推荐使用「链接增强文本」：

```text
来源页面：http://example.edu/faculty/index.htm
页面类型：导师列表页
Chunk：3 / 8
处理要求：只从本 chunk 中抽取候选，最多返回 10 个。不要根据记忆返回其他 chunk 或其他页面的候选。

[内容开始]
王璞巍
职称：教授
研究方向：数据库、大数据管理
详情链接：[王璞巍](http://example.edu/profile/wang.htm)

王秋月
研究方向：信息检索、自然语言处理
详情链接：[王秋月](http://example.edu/profile/wangqiuyue.htm)
[内容结束]
```

模型只基于当前 chunk 输出候选。它不需要知道全量已保存列表，也不需要看最近 30 条历史。

## Chunk 状态机

每个列表页 chunk 都有独立状态：

```text
pending -> processing -> completed
pending -> processing -> no_candidates
pending -> processing -> split_required -> superseded
pending -> processing -> failed
```

状态含义：

- `pending`：等待处理。
- `processing`：已分配给当前运行。
- `completed`：模型已处理，且后端认为不会漏掉本 chunk 内的候选。
- `no_candidates`：模型判断没有导师候选，后端接受该结果。
- `split_required`：chunk 候选过密或输出风险过高，需要拆分。
- `superseded`：父 chunk 已被子 chunk 取代，不再直接处理。
- `failed`：多次处理失败，需要记录错误并由任务级策略决定是否继续。

已处于 `completed`、`no_candidates` 或 `superseded` 的 chunk，再次被模型请求时不返回正文，只返回短消息：

```json
{
  "status": "already_processed",
  "message": "该页面片段已处理，请获取下一个未处理 chunk。"
}
```

## 模型输出协议

模型处理 chunk 时必须输出结构化结果，最多 10 个候选：

```json
{
  "chunk_status": "completed",
  "has_unsubmitted_candidates_in_current_chunk": false,
  "candidates": [
    {
      "name": "王璞巍",
      "email": null,
      "title": "教授",
      "university": "中国人民大学",
      "school": "信息学院",
      "department": null,
      "research_direction": "数据库、大数据管理",
      "recent_papers": [],
      "profile_url": "http://example.edu/profile/wang.htm",
      "source_url": "http://example.edu/faculty/index.htm",
      "source_chunk_id": "job-9-page-3-chunk-2",
      "confidence": 0.85,
      "field_confidence": {
        "name": 0.95,
        "profile_url": 0.95,
        "research_direction": 0.7
      },
      "evidence": {
        "summary": "chunk 中出现姓名、研究方向和详情链接。"
      },
      "boundary_risk": false
    }
  ],
  "reason": "本 chunk 中识别到 1 名候选。"
}
```

允许的 `chunk_status`：

- `completed`：候选已完整输出。
- `no_candidates`：本 chunk 没有导师候选。
- `too_many_candidates`：当前 chunk 正文中明确还有超过 10 个已看见候选、因单次提交限制无法一次提交，需要拆分当前 chunk。
- `uncertain`：内容疑似相关，但模型无法可靠判断，可由后端重试或拆分。

后端不能完全信任模型对“还有更多候选”的模糊判断。`has_unsubmitted_candidates_in_current_chunk` 只表示当前 chunk 内部还有模型已经看见、但因单次最多 10 个候选限制未提交的候选；下一页、下一个 chunk、分页导航或不确定情况都必须为 `false`。

## Chunk 过密与自动拆分

### 触发条件

以下情况触发 `split_required`：

- `chunk_status = "too_many_candidates"`。
- 模型输出超过 10 个候选。
- 模型输出被截断或 JSON 无效，且 chunk 文本或链接密度较高。

第一版采用明确拆分策略：刚好返回 10 个候选、分页导航提示还有下一页、或浏览器整页视图看到更多候选，都不自动触发拆分；只有当前 chunk 正文内部明确超过 10 个已看见候选且模型返回 `chunk_status = "too_many_candidates"`，或候选数量超过工具限制等硬性错误时，才拆分该 chunk，避免无意义的重复处理。

### 拆分流程

1. 模型处理父 chunk。
2. 如果触发拆分，保存本次已经抽取出的候选，但不把父 chunk 标记为 `completed`。
3. 父 chunk 标记为 `split_required`。
4. 后端按结构边界优先、文本二分兜底的方式生成子 chunk。
5. 父 chunk 标记为 `superseded`。
6. 子 chunk 进入 `pending` 队列。
7. 后续只处理子 chunk，不再向模型返回父 chunk 正文。

### 为什么允许先保存再拆分

模型已经抽取出的 10 个候选有价值，直接丢弃会浪费 token。保存层必须幂等，子 chunk 重跑时如果再次抽取到相同候选，会被合并或跳过，不会污染数据。

### 子 chunk 拆分策略

优先级：

1. 如果父 chunk 由多个结构块组成，按结构块重新分组。
2. 如果结构不可用，按段落、换行或句子边界二分。
3. 子 chunk 默认保留 180 tokens overlap，并根据边界风险在 120-240 tokens 内调整。
4. 子 chunk 如果仍然过密，可以继续递归拆分。

限制：

- 最小 chunk 大小建议不低于 500 tokens。
- 最大拆分深度建议为 3-4 层。
- 超过限制后仍过密时，任务记录警告，可保留已保存候选并标记该 chunk 需要人工复查或失败。

### 父子 chunk 数据示例

父 chunk：

```json
{
  "chunk_id": "page-1-chunk-3",
  "parent_chunk_id": null,
  "status": "superseded",
  "split_reason": "too_many_candidates",
  "split_depth": 0,
  "children": ["page-1-chunk-3.1", "page-1-chunk-3.2"]
}
```

子 chunk：

```json
{
  "chunk_id": "page-1-chunk-3.1",
  "parent_chunk_id": "page-1-chunk-3",
  "status": "pending",
  "split_depth": 1,
  "overlap_prefix": false,
  "overlap_suffix": true
}
```

## 候选准入规则

保存工具不应把所有模型输出都写入候选表。准入规则如下：

- 有 `email`：允许保存。
- 无 `email` 但有 `profile_url`：允许保存，因为后续可通过详情页补全邮箱。
- 无 `email` 且无 `profile_url`：拒绝保存。

拒绝原因示例：

```text
缺少邮箱和详情页链接，无法用于联系或后续补全。
```

这条规则用于减少无价值候选，并降低重复噪音。列表页中只有姓名、职称、研究方向但没有邮箱和详情页的记录，默认不进入候选库。

## 候选幂等与合并

保存层必须成为最终防线。模型重复提交候选时，不能新增重复行。

### 身份键优先级

同一任务内候选身份按以下顺序判断：

1. `job_id + normalized_email`
2. `job_id + normalized_profile_url`
3. 谨慎使用 `job_id + normalized_name + university + school + source_url` 作为弱匹配，仅用于合并证据，不建议作为唯一强身份。

其中：

- `normalized_email` 需要 trim、小写和格式校验。
- `normalized_profile_url` 需要去掉 fragment，规范化相对路径，必要时统一尾部斜杠和 URL 编码。
- 无邮箱无详情页候选直接拒绝，因此不会依赖弱身份键大量保存。

### 保存结果类型

每批保存返回：

```json
{
  "batch_status": "saved",
  "attempted_count": 10,
  "saved_count": 2,
  "merged_count": 1,
  "skipped_duplicate_count": 7,
  "rejected_count": 0,
  "total_saved_count": 82,
  "next_instruction": "本批大多已存在，请继续处理下一个 chunk。"
}
```

含义：

- `saved`：新增候选。
- `merged`：命中已有候选，并补充了更完整字段或证据。
- `skipped_duplicate`：命中已有候选，但没有新增有效信息。
- `rejected`：不满足准入或字段校验失败。

返回给模型的反馈应保持短小，不返回完整已保存历史。

### 字段级合并

重复候选不应简单跳过。系统需要支持字段级合并，以处理 overlap、边界截断和后续更完整证据。

合并规则：

- 空字段可以被非空字段补充。
- 低置信字段可以被高置信字段覆盖。
- 边界风险字段可以被非边界字段覆盖。
- 详情页证据优先于列表页证据。
- 高置信字段不能被低置信字段覆盖。
- 无法判断的冲突不直接覆盖，应记录冲突证据。

字段来源优先级：

```text
详情页证据 > 列表页完整证据 > 列表页边界证据 > 低置信模型推断
```

## 边界截断与 overlap 处理

### 问题

chunk 可能正好切在某个老师信息中间。例如：

- 前一个 chunk 只包含姓名和不完整邮箱。
- 后一个 chunk 因 overlap 或后续文本包含更完整邮箱和详情页。
- 前后 chunk 都可能抽取到同一老师。

### 处理策略

- overlap 造成重复是预期行为，不应视为错误。
- 重复候选进入合并流程，而不是简单拒绝。
- 候选应携带 `source_chunk_id` 和 `boundary_risk`。
- 如果证据出现在 chunk 前后边界附近，后端可自动标记边界风险。
- 后续非边界、更完整、高置信字段可以覆盖边界字段。

### 邮箱截断

邮箱保存必须严格校验。对于 `abc@xxx.com` 与 `abc@xxx.com.cn` 这类冲突：

- 如果 `profile_url` 相同，后者更长且证据更完整，可覆盖前者。
- 如果来源不明确或证据冲突，保留原主邮箱，并记录冲突信息。
- 详情页中提取到的邮箱优先级高于列表页。
- 列表页边界附近邮箱应降低置信度。

## 无候选 chunk

没有老师信息的 chunk 是正常情况。模型可以返回：

```json
{
  "chunk_status": "no_candidates",
  "has_unsubmitted_candidates_in_current_chunk": false,
  "candidates": [],
  "reason": "该片段主要是导航、页脚或无关链接。"
}
```

后端将 chunk 标记为 `no_candidates`，后续不再返回正文。这能避免模型反复处理导航、页脚、新闻等低价值片段。

## Agent 工具设计

### 受控工具组合

建议从「自由抓页 + 自由保存」逐步过渡到更受控的工具组合：

- `claim_next_page_chunk`：获取下一个待处理页面片段。
- `submit_chunk_candidates`：提交当前 chunk 的候选，并由后端保存、合并、更新 chunk 状态。
- `crawl_page`：保留给探索新页面使用，但对已处理页面或 chunk 不再返回完整正文。
- `finish_crawl`：当无待处理 chunk 和待探索页面时结束任务。

### `claim_next_page_chunk`

返回下一个 `pending` chunk：

```json
{
  "status": "ok",
  "chunk_id": "job-9-page-3-chunk-2",
  "source_url": "http://example.edu/faculty/index.htm",
  "chunk_index": 2,
  "chunk_total": 8,
  "content": "链接增强文本...",
  "max_candidates": 10
}
```

如果没有待处理 chunk：

```json
{
  "status": "empty",
  "message": "当前没有待处理页面片段。请探索新页面或结束任务。"
}
```

### `submit_chunk_candidates`

输入包含 `chunk_id`、`chunk_status`、`has_unsubmitted_candidates_in_current_chunk` 和候选列表。后端执行：

1. 校验 chunk 是否仍处于 `processing`。
2. 校验候选数量不超过 10。
3. 保存、合并或拒绝候选。
4. 根据 `chunk_status` 决定 chunk 状态；只有 `chunk_status = "too_many_candidates"` 才触发拆分。
5. 必要时触发自动拆分。
6. 返回短反馈和下一步建议。

### `investigate_with_browser`

`investigate_with_browser` 只作为普通抓取明显不足、页面疑似动态渲染或围绕具体目标调查时的兜底工具。它不能用于绕过 chunk：当前任务存在 `pending`、`processing` 或 `split_required` chunk 时，工具层不返回页面正文，只返回 `chunk_required`，要求 Agent 先通过 `claim_next_page_chunk` 和 `submit_chunk_candidates` 完成当前 chunk 流程。

### 已处理内容保护

如果模型尝试重复处理已完成 chunk，工具不返回正文，只返回短状态。这样可以从内容入口减少重复保存，而不是只靠数据库防线。

## 页面探索与 URL 队列

chunk 机制不替代页面探索。Agent 仍需要发现可能的列表页和详情页。

建议后端维护 URL 队列：

- `pending_pages`：待抓取页面。
- `processed_pages`：已抓取并生成 chunk 的页面。
- `profile_pages`：候选详情页补全队列。
- `denied_pages`：跨域、文件下载、登录区、无关页面。

页面级缓存继续保留，用于避免重复联网抓取。同一 URL 如果已经生成 chunk，后续不应再次把完整页面正文交给模型，而应让模型通过 `claim_next_page_chunk` 处理未完成片段。

## 循环控制

除了保存层幂等，还需要防止模型持续无效调用工具。

### 重复批次控制

如果连续多个 `submit_chunk_candidates` 的结果都是：

- `saved_count = 0`
- `merged_count = 0`
- `skipped_duplicate_count > 0`

说明模型正在重复提交已存在候选。系统应返回更强的状态，例如：

```json
{
  "batch_status": "duplicate_loop",
  "message": "连续多个批次均为重复候选，请停止保存当前内容，获取下一个 chunk 或结束任务。"
}
```

达到阈值后可停止当前页面，必要时让任务进入部分完成或失败状态。

### Chunk 级循环控制

同一个 chunk 多次失败或重复提交时：

- 第 1 次失败：允许重试。
- 第 2 次失败：尝试拆分。
- 超过最大拆分深度或重试次数：标记 chunk `failed`，记录错误。

### 任务级停止条件

任务可以在以下条件下结束：

- 没有待探索页面。
- 没有待处理 chunk。
- 没有待补全详情页。
- 最近若干轮没有新增候选或有效合并。
- 达到用户设置的最大页面数、最大 token、最大运行时间或最大失败预算。

## 数据模型建议

第一版可以新增表，也可以先复用 `crawl_pages` 的 JSON 字段。考虑到这是大改，推荐新增显式状态表。

### `crawl_page_chunks`

建议字段：

| 字段 | 类型 | 说明 |
| ---- | ---- | ---- |
| `id` | INTEGER | 主键 |
| `job_id` | INTEGER | 抓取任务 ID |
| `page_id` | INTEGER | 关联 `crawl_pages.id` |
| `source_url` | TEXT | 来源页面 URL |
| `page_fingerprint` | TEXT | 页面内容指纹 |
| `chunk_id` | TEXT | 稳定 chunk 标识 |
| `parent_chunk_id` | TEXT NULL | 父 chunk 标识 |
| `chunk_index` | INTEGER | 同级顺序 |
| `chunk_hash` | TEXT | chunk 内容指纹 |
| `status` | TEXT | `pending`、`processing`、`completed` 等 |
| `content` | TEXT | 链接增强文本或可重建内容 |
| `token_estimate` | INTEGER | token 估算 |
| `text_start_offset` | INTEGER NULL | 页面文本起始位置 |
| `text_end_offset` | INTEGER NULL | 页面文本结束位置 |
| `overlap_prefix` | BOOLEAN | 是否包含前向 overlap |
| `overlap_suffix` | BOOLEAN | 是否包含后向 overlap |
| `split_depth` | INTEGER | 拆分深度 |
| `split_reason` | TEXT NULL | 拆分原因 |
| `attempt_count` | INTEGER | 处理尝试次数 |
| `last_error` | TEXT NULL | 最近错误 |
| `created_at` | DATETIME | 创建时间 |
| `updated_at` | DATETIME | 更新时间 |

索引建议：

- `(job_id, status)`：领取待处理 chunk。
- `(job_id, source_url)`：按页面查询 chunk。
- `(job_id, chunk_hash)`：避免同内容重复处理。
- `(parent_chunk_id)`：查询父子关系。

### 候选表扩展

`crawl_candidates` 建议增加或利用现有 JSON 字段记录：

- `source_chunk_id`
- `source_kind`：`list_chunk` 或 `profile_page`
- `boundary_risk`
- `identity_key`
- `merge_history`
- `field_sources`
- `conflicts`

如果不想立即扩表，可以先把这些信息放入 `evidence`，但长期建议拆出明确字段或 JSON 结构。

## 错误处理

- 模型返回无效 JSON：不推进 chunk 状态，记录失败次数；必要时拆分或重试。
- 候选数量超过 10：拒绝本次输出，标记 `split_required`。
- 候选无邮箱且无详情页：保存工具拒绝该候选，但 chunk 可继续完成。
- chunk 拆分超过最大深度：保留已保存候选，标记 chunk `failed`，任务可进入部分完成。
- 页面内容变化导致 `page_fingerprint` 不一致：重新生成 chunk，旧 chunk 标记为过期或保留为历史。
- 暂停和取消任务：当前 `processing` chunk 回到 `pending` 或标记为可恢复状态。

## 调试日志与可观测性

调试日志需要能解释为什么没有重复保存、为什么 chunk 被拆分或跳过。

建议记录：

- 每个页面生成了多少 chunk。
- 每个 chunk 的 token 估算、链接数量和状态变化。
- 每次 `submit_chunk_candidates` 的 `saved`、`merged`、`skipped_duplicate`、`rejected` 计数。
- 自动拆分原因和父子 chunk 关系。
- 重复循环熔断触发原因。
- 字段合并和冲突记录摘要。

导出的 JSONL 应继续保持逐事件记录，但避免把大段重复页面正文反复写入调试日志。必要时可只记录 chunk ID、hash 和预览。

## 迁移与实施阶段

### 第一阶段：保存层止血

- 增加候选准入规则：无邮箱且无详情页链接直接拒绝。
- 增加 `profile_url` 级去重。
- 保存重复候选时支持 `merged`、`skipped_duplicate`。
- 返回短反馈，不依赖完整已保存历史。

### 第二阶段：Chunk 状态表与切片器

- 新增 `crawl_page_chunks`。
- 实现通用 HTML 清洗、链接增强文本和 chunk 生成。
- 为列表页生成 chunk，并持久化状态。
- 实现 `claim_next_page_chunk` 和 `submit_chunk_candidates` 的后端逻辑。

### 第三阶段：Agent 工具收敛

- 调整 Agent 提示和工具使用策略。
- 对已处理页面不再返回完整正文。
- 引导模型优先处理 `claim_next_page_chunk` 返回的内容。
- 保留 `crawl_page` 用于探索新页面，但避免重复处理已生成 chunk 的页面。

### 第四阶段：自动拆分与循环控制

- 支持候选数达到 10 且存在截断风险时自动拆分 chunk；如果模型明确无更多候选，可直接完成当前 chunk。
- 支持父子 chunk 状态和递归拆分限制。
- 增加 duplicate loop 熔断。
- 完善调试日志导出。

### 第五阶段：详情页补全队列

- 对无邮箱但有 `profile_url` 的候选进入详情页补全队列。
- 详情页字段优先级高于列表页字段。
- 合并邮箱、研究方向、近期论文和证据。

## 测试计划

### 保存层测试

- 有邮箱候选可以保存。
- 无邮箱但有 `profile_url` 的候选可以保存。
- 无邮箱且无 `profile_url` 的候选被拒绝。
- 同一 `email` 重复提交不会新增行。
- 同一 `profile_url` 重复提交不会新增行。
- 后续提交更完整字段时触发 `merged`。
- 后续提交低置信或边界风险字段不会覆盖高质量字段。

### Chunk 切片测试

- HTML 中链接被保留为链接增强文本。
- 长页面被切成多个 chunk，顺序稳定。
- 相邻 chunk 有受控 overlap。
- `chunk_hash` 对相同内容稳定，对内容变化敏感。
- 无结构页面能退化为段落或文本切片。

### Chunk 状态测试

- `claim_next_page_chunk` 只返回 `pending` chunk。
- 已完成 chunk 再次请求时不返回正文。
- 无候选 chunk 可标记为 `no_candidates`。
- 模型返回无效 JSON 不推进状态。
- 暂停或取消时 `processing` chunk 可恢复。

### 自动拆分测试

- 返回 `chunk_status = "too_many_candidates"` 或候选数量超过工具限制时触发 `split_required`；刚好返回 10 个候选不触发拆分。
- 父 chunk 被标记为 `superseded`。
- 子 chunk 进入 `pending` 队列。
- 子 chunk 重复抽取父 chunk 已保存候选时只 `merged` 或 `skipped_duplicate`。
- 超过最大拆分深度后 chunk 标记失败并记录原因。

### Agent 行为测试

- Agent 不需要接收最近 30 条已保存候选也能继续处理下一个 chunk。
- Agent 重复请求已处理 chunk 时只收到短反馈。
- 连续重复提交触发 duplicate loop 状态。
- 没有待处理 chunk 和待探索页面时任务结束。

## 风险与权衡

### 风险：chunk 切断候选信息

通过结构优先切片、overlap、边界风险标记和字段级合并降低风险。不能完全避免，但比让模型反复处理整页更可控。

### 风险：chunk 过小导致 token 浪费

chunk 过小会增加模型调用次数。第一版使用 `target_tokens=2000`、`soft_max_tokens=2800`、`hard_max_tokens=3200`，并根据后续真实抓取日志调整。

### 风险：模型误判当前 chunk 是否仍有未提交候选

字段命名为 `has_unsubmitted_candidates_in_current_chunk`，并将拆分动作绑定到明确的 `chunk_status = "too_many_candidates"`。后端不再因为刚好返回 10 个候选或旧的模糊“还有更多”标记而自动拆分。

### 风险：新增状态表增加复杂度

这是本次大改的必要成本。收益是把流程状态从模型上下文移到可测试、可恢复、可观测的后端状态机。

### 风险：通用切片仍可能保留噪音

允许无候选 chunk 正常完成。无候选处理一次后不再返回正文，能逐步过滤噪音。

## 自检

- 本设计没有要求后端识别导师候选，符合通用抓取 Agent 的目标。
- 本设计保留每次最多 10 个候选的限制，避免大 JSON 输出截断。
- 本设计没有依赖最近 30 条保存摘要来防重复，而是通过 chunk 状态和保存幂等控制重复。
- 本设计解释了 overlap、边界截断、邮箱冲突、无候选 chunk、chunk 超过 10 个候选等边界情况。
- 本设计允许先保存父 chunk 中已抽取的候选，再拆分重跑，依赖保存层幂等避免重复落库。
- 本设计把列表页候选发现和详情页补全分开处理，符合详情页通常只对应单个老师的事实。




## 实现备注

第一版实现参数：`target_tokens=2000`、`soft_max_tokens=2800`、`hard_max_tokens=3200`、`overlap_tokens=180`、`min_split_tokens=500`、`max_split_depth=4`、`single_chunk_max_tokens=2200`、`min_balanced_target_tokens=1200`、`max_balanced_target_tokens=2200`。

页面初始切分采用动态均衡目标：先估算整页 token 数，若不超过 `single_chunk_max_tokens` 则不切；否则按 `ceil(page_tokens / target_tokens)` 计算 chunk 数，再用 `ceil(page_tokens / chunk_count)` 作为本页目标大小，并夹在 `min_balanced_target_tokens` 与 `max_balanced_target_tokens` 之间。切分后如果尾块过小且与前一块合并不超过 `hard_max_tokens`，则合并尾块，减少低价值碎片 chunk。
