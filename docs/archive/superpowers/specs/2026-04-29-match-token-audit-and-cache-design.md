# 匹配分析 Token 审计与缓存优化设计

## 背景

导师看板已有单个和批量“分析匹配度”能力，但当前链路存在三个缺口：

- 后端匹配调用已经能拿到模型返回的 usage，但 `calculate_task_match` 只保存匹配分数和说明，未持久化 token 消耗。
- 批量分析在前端逐个串行执行，多选时等待时间随导师数量线性增长。
- 匹配 prompt 中可复用的用户身份、默认材料等信息没有被明确组织为稳定前缀，缓存命中难以验证和优化。

本设计聚焦“匹配度分析”这一条链路，不改变写信草稿生成策略。

## 目标

- 每次匹配分析都持久保存 token 审计记录，包括成功和失败场景。
- 单个和批量分析完成后，通过全局消息显示本次汇总 token；批量只显示一条汇总消息。
- 批量分析支持受控并发，避免一次性触发过多模型请求。
- 已有匹配结果再次点击时仍然强制重算，并覆盖当前任务上的最新匹配结果。
- 降低匹配分析输出随机性，让同一输入更容易得到稳定评分。
- 调整 prompt 结构，提高 OpenAI Prompt Caching 的命中机会，并记录 cached token 指标。

## 非目标

- 不保存完整 prompt 正文，避免把用户材料全文重复落库。
- 不新增费用估算、单价配置或账单统计。
- 不改变邮件草稿生成的温度和 prompt 结构。
- 不追求补齐历史匹配分析的 token 数据。
- 不把匹配审计记录展示到工作区消息流中。

## 方案选择

采用新增 `match_analysis_runs` 表的方案。它把“当前任务最新匹配状态”和“每次模型调用审计记录”分开：

- `email_tasks` 继续保存最新 `match_score`、`match_reason`、`fit_points`、`risk_points`、`match_keywords`。
- `match_analysis_runs` 保存每次匹配分析运行的 token、缓存、耗时、结果和错误摘要。

没有选择复用 `email_logs`，因为 `email_logs` 当前面向邮件消息和草稿，加入匹配记录会污染工作区消息语义。也没有选择只在 `email_tasks` 加 latest token 字段，因为这不能满足后续审计。

## 数据模型

新增表 `match_analysis_runs`：

```text
id
email_task_id
professor_id
identity_id
llm_profile_id
success
match_score
prompt_tokens
completion_tokens
total_tokens
cached_tokens
duration_ms
endpoint_kind
status_code
prompt_hash
stable_prefix_hash
error_message
created_at
```

字段说明：

- `email_task_id` 可为空的必要性不高，本次按非空设计；匹配分析必须先有任务。
- `prompt_tokens`、`completion_tokens`、`total_tokens` 来自模型 usage。
- `cached_tokens` 优先从 OpenAI usage 的 `prompt_tokens_details.cached_tokens` 或 Responses 等价字段提取；没有该字段时为 `null`。
- `prompt_hash` 使用最终发送 prompt 的稳定哈希，只用于审计定位，不保存 prompt 正文。
- `stable_prefix_hash` 使用身份、默认材料、系统规则等稳定前缀的哈希，用于分析缓存命中。
- `duration_ms`、`endpoint_kind`、`status_code` 复用 `ChatCompletionResult` 中已有请求元数据。

## 后端数据流

`llm_runtime.ChatCompletionUsage` 扩展 `cached_tokens` 字段，`parse_completion_usage` 同时兼容：

- Chat Completions: `usage.prompt_tokens_details.cached_tokens`
- Responses: 如果返回结构不同，做等价字段解析；解析不到则为 `None`

`generate_match_evaluation` 返回的 `GeneratedMatchEvaluation` 保留 usage。

`calculate_task_match` 在一次模型请求结束后写入 `match_analysis_runs`：

- 成功：保存 usage、cached_tokens、duration、score、prompt hash，并更新 `email_tasks` 最新匹配结果。
- LLMRuntimeError：保存失败记录、duration、endpoint、status、错误摘要，并维持现有 `task.last_error` 行为。
- ValueError 这类请求前校验错误不消耗 token，可不写审计记录；如果前端需要展示跳过数量，由前端汇总。

`calculate_task_match_once` 继续强制重算，不因为已有 `match_score` 提前返回。

## API 响应

将 `POST /api/email-tasks/{task_id}/calculate-match` 的响应改为包装结构：

```text
{
  "thread": WorkspaceThreadRead,
  "usage": {
    "prompt_tokens": number | null,
    "completion_tokens": number | null,
    "total_tokens": number | null,
    "cached_tokens": number | null
  },
  "run_id": number | null
}
```

前端只有匹配分析调用点需要适配。其他 email task action 继续返回 `WorkspaceThreadRead`，避免扩大接口变更范围。

## 前端交互

单个分析：

- 调用 `calculateMatch` 后读取 `usage`。
- 成功时显示一条全局成功消息：`匹配分析完成`。
- 描述包含本次 `输入 / 输出 / 总计 / 缓存命中` token；缺失字段显示“未返回”。
- 失败沿用现有错误通知。

批量分析：

- 若未选择导师或缺少默认材料，沿用现有提示。
- 缺少研究信息的导师继续跳过，并计入 skipped。
- 第一个可分析导师先执行一次，用于 warm up 稳定前缀缓存。
- 剩余导师使用固定并发池，默认并发 3。
- 每个导师仍维护行级 loading 状态。
- 全部结束后只显示一条全局消息：
  - 成功数
  - 失败数
  - 跳过数
  - 本次输入、输出、总计、缓存命中 token 合计
  - 失败详情最多展示前 2 条，避免通知过长

## Prompt 与缓存

匹配分析 prompt 拆成稳定前缀和动态后缀：

稳定前缀：

- 系统角色与 JSON 输出规则
- 评分标准
- 用户身份摘要
- 默认材料摘要或提取文本
- 可选材料列表，按 `id` 稳定排序

动态后缀：

- 导师姓名、学校、学院、职称
- 导师研究方向
- 近期论文，按现有顺序或稳定排序

序列化要求：

- 不加入当前时间、随机 ID、运行序号等变化内容。
- 列表顺序稳定。
- 空字段使用固定占位文案。
- 字段标题固定，不按导师动态增删标题。

OpenAI 官方 Prompt Caching 文档说明，缓存命中依赖完全相同的 prompt 前缀，静态内容应放在开头，动态内容放在末尾，并可通过 usage 中的 cached token 指标监控命中情况。

参考：`https://platform.openai.com/docs/guides/prompt-caching`

## OpenAI 专用参数

当 `llm_profile.provider == "openai"` 且 base URL 是 OpenAI 官方地址时，匹配分析请求增加：

```text
prompt_cache_key = "match:v1:{identity_id}:{primary_material_id}:{llm_profile_id}"
```

如果未来要启用 24h extended cache，应先确认模型支持和数据保留策略；本次不默认启用 `prompt_cache_retention=24h`。

对于 DeepSeek 或其他 OpenAI-compatible 服务，不发送 OpenAI 专用缓存参数，避免兼容性问题。

## 随机性控制

匹配分析固定使用 `temperature = 0`。这条规则只应用于 `generate_match_evaluation`：

- 不读取 LLM Profile 的全局 temperature。
- 不影响 `generate_draft_content`。
- 不影响模型连通性测试。

这样能降低评分漂移，也更利于比较多次强制重算结果。

## 错误处理

- 模型请求失败：保存失败审计记录，前端计入 failed。
- 模型未返回 usage：匹配结果仍可保存，usage 字段为 `null`，前端显示“未返回”。
- cached token 缺失：不视为错误，记录 `null`。
- 部分导师失败：批量流程继续处理其他导师。
- 并发请求中任一失败不取消其他已启动请求。

## 测试计划

后端：

- `parse_completion_usage` 能解析 prompt、completion、total、cached token。
- `calculate_task_match` 成功时写入 `match_analysis_runs`。
- LLMRuntimeError 时写入失败审计记录。
- 已有匹配结果时 `calculate_task_match_once` 仍发起新运行并覆盖最新结果。
- 匹配分析 payload 使用 `temperature=0`。
- OpenAI 官方 profile 才发送 `prompt_cache_key`。

前端：

- 单个分析成功后显示 token 全局消息。
- 批量分析只显示一条汇总 token 消息。
- 批量跳过缺少研究信息导师并汇总 skipped。
- 批量并发执行时行级 loading 状态正确恢复。
- usage 字段缺失时不会渲染异常。

## 成功标准

- 每次真实匹配模型调用都有一条审计记录。
- 前端单次和批量分析都能显示本次 token 消耗。
- 批量分析比串行更快，且并发数受控。
- 重复点击已有匹配导师会强制生成新的审计记录并覆盖最新匹配结果。
- 匹配分析请求的温度稳定为 0。
- OpenAI 请求可记录 cached token，用于后续判断缓存命中是否改善。
