# Token 消耗记录中心设计

## 背景

系统已有多处 token 消耗信息，但它们分散在不同业务记录里：

- 智能爬取的 token 累计在 `crawl_job_runs`。
- 匹配分析的 token 审计在 `match_analysis_runs`。
- AI 草稿生成的 token usage 保存在 `email_logs.provider_payload.usage`。

用户需要在个人中心看到一个统一的 token 消耗记录中心，用于查看每次使用一个功能产生的总消耗。这里的“每次”是功能级别的一次使用，例如一次智能爬取、一次匹配分析、一次 AI 草稿生成，而不是底层模型的每次调用。

## 目标

- 在个人中心最底部新增默认收起的 `Token 消耗记录中心` 卡片。
- 展示全局最近 token 消耗记录，不按当前默认身份或默认模型过滤。
- 汇总智能爬取、匹配分析、AI 草稿生成三类功能级 token 消耗。
- 每条记录展示输入、输出、缓存命中、总计 token。
- 缺失字段可显示为未返回，不能导致接口或前端渲染失败。
- 模板模式草稿不消耗 token，不进入记录中心。

## 非目标

- 不新增费用金额换算、单价配置或账单统计。
- 不展示底层每次 LLM 调用明细。
- 不补齐历史缺失 usage 的记录。
- 不做导出、分页、日期筛选或高级筛选。
- 不改变现有 token 采集路径和业务功能行为。

## 方案选择

采用后端聚合已有记录的方案，不新增统一中心表。

后端新增 token usage 聚合 service 和 API，分别读取 `crawl_job_runs`、`match_analysis_runs`、`email_logs` 中已有的功能级消耗记录，映射成统一 DTO 后按时间倒序返回。这样可以避免重复落库，也避免把每个功能的内部数据结构泄漏到前端。

没有选择新增 `token_usage_records` 表，因为当前已有数据源足以满足“最近记录中心”的需求，新增表会要求改造每个功能完成路径并处理双轨历史数据。没有选择前端分别拉取各业务 API，因为个人中心会承担过多跨业务聚合逻辑。

## 数据范围

### 智能爬取

来源：`crawl_job_runs`

一条 `CrawlJobRun` 表示一次爬取功能使用或一次重试运行。记录中心读取：

- `input_tokens`
- `output_tokens`
- `total_tokens`
- `status`
- `created_at` 或 `updated_at`

缓存命中字段不存在，统一返回 `cached_tokens = null`。

### 匹配分析

来源：`match_analysis_runs`

一条 `MatchAnalysisRun` 表示一次匹配分析功能使用。记录中心读取：

- `prompt_tokens` 映射为 `input_tokens`
- `completion_tokens` 映射为 `output_tokens`
- `cached_tokens`
- `total_tokens`
- `success`
- `created_at`

### AI 草稿生成

来源：`email_logs`

只读取满足以下条件的记录：

- `direction = draft`
- `provider_payload.usage` 是对象

一条 draft log 表示一次草稿生成功能使用。记录中心读取：

- `provider_payload.usage.prompt_tokens` 映射为 `input_tokens`
- `provider_payload.usage.completion_tokens` 映射为 `output_tokens`
- `provider_payload.usage.total_tokens`

模板模式草稿的 `provider_payload.usage = null`，不会进入记录中心。

## 后端 API

新增接口：

```text
GET /api/token-usage/records?limit=20
```

`limit` 默认 20，允许范围 1 到 100。初版前端固定请求 20 条。

响应结构：

```json
{
  "records": [
    {
      "id": "match_analysis:42",
      "feature_type": "match_analysis",
      "feature_label": "匹配分析",
      "title": "张教授 - 匹配分析",
      "input_tokens": 1200,
      "output_tokens": 300,
      "cached_tokens": 800,
      "total_tokens": 1500,
      "model_name": "gpt-4.1-mini",
      "identity_name": "博士申请邮箱",
      "created_at": "2026-04-29T09:30:00Z",
      "status": "success"
    }
  ],
  "summary": {
    "input_tokens": 1200,
    "output_tokens": 300,
    "cached_tokens": 800,
    "total_tokens": 1500,
    "record_count": 1
  }
}
```

统一记录字段：

```text
id: string
feature_type: crawl | match_analysis | draft_generation
feature_label: string
title: string
input_tokens: int | null
output_tokens: int | null
cached_tokens: int | null
total_tokens: int | null
model_name: string | null
identity_name: string | null
created_at: datetime
status: success | failed | running | unknown
```

## 标题和状态规则

标题优先使用可读上下文：

- 智能爬取：使用爬取任务名称或入口 URL，格式为 `智能爬取 - <名称或 URL>`。
- 匹配分析：使用导师姓名，格式为 `<导师姓名> - 匹配分析`。
- AI 草稿：使用导师姓名，格式为 `<导师姓名> - AI 草稿`。

状态映射：

- `match_analysis_runs.success = true` 返回 `success`。
- `match_analysis_runs.success = false` 返回 `failed`。
- `crawl_job_runs.status` 为运行或排队类状态时返回 `running`。
- `crawl_job_runs.status` 为完成类状态时返回 `success`。
- `crawl_job_runs.status` 为失败类状态时返回 `failed`。
- draft log 有 usage 时返回 `success`。
- 无法识别时返回 `unknown`。

## 汇总规则

summary 只汇总当前响应内的记录，不代表全历史总量。

nullable token 字段按以下规则汇总：

- 数字参与加总。
- `null` 不参与加总。
- 如果所有记录某个字段都是 `null`，summary 中该字段返回 0。

这样前端可直接展示最近 20 条的输入、输出、缓存命中、总计 token 总览。

## 前端交互

个人中心最底部新增默认收起卡片：

```text
Token 消耗记录中心
```

卡片头部显示：

- 标题
- 最近记录数量
- 展开或收起按钮

展开后：

- 请求 `/api/token-usage/records?limit=20`。
- 顶部展示四个总览数字：输入、输出、缓存命中、总计。
- 下方展示最近记录列表。
- 每条记录展示功能、标题、身份、模型、时间、状态、输入、输出、缓存命中、总计。

空状态：

```text
暂无 token 消耗记录
```

加载失败时：

- 在卡片内显示错误信息。
- 提供 `重试` 按钮重新请求。

## 文件边界

后端新增：

- `backend/app/services/token_usage_records.py`：聚合查询、字段映射、summary 计算。
- `backend/app/schemas/token_usage.py`：API 响应 DTO。
- `backend/app/api/token_usage.py`：FastAPI 路由。

后端修改：

- `backend/app/api/__init__.py`：导出 token usage router。
- `backend/main.py`：注册 token usage router。

前端新增：

- `frontend/src/lib/api/tokenUsage.ts`：调用 token usage API。
- `frontend/src/features/token-usage/client/tokenUsage.ts`：格式化和汇总工具。
- `frontend/src/features/token-usage/client/tokenUsage.test.ts`：前端工具测试。
- `frontend/src/components/molecules/TokenUsageCenterCard.tsx`：个人中心底部卡片。

前端修改：

- `frontend/src/types/index.ts`：新增 token usage DTO 类型。
- `frontend/src/pages/ProfilePage.tsx`：在页面最底部挂载默认收起卡片。

## 测试计划

后端测试：

- 新增 service 测试，覆盖三类数据源合并并按时间倒序返回。
- 新增 service 测试，覆盖 nullable token 字段汇总。
- 新增 API 测试，确认 `/api/token-usage/records` 返回统一结构。

前端测试：

- 新增 token usage 工具测试，覆盖 `null` 显示为 `未返回`。
- 新增 token usage 工具测试，覆盖 summary 数字格式化。
- 如果现有测试设施允许，补充卡片默认收起、展开加载、空状态和错误重试测试。

验证命令：

```bash
cd backend && uv run python -m unittest discover test
cd frontend && npm run test
cd frontend && npm run lint
```

## 风险和处理

- `email_logs.provider_payload` 是 JSON 字段，不同数据库的 JSON 查询能力不同。初版可以先按时间取最近 draft log 后在 Python 中筛选 usage，保持 SQLite 兼容。
- 三类记录来源时间字段不同。统一使用最能代表功能使用发生时间的字段，优先 `created_at`，爬取 run 可使用 `updated_at` 展示当前累计状态。
- 个人中心页面较大。新增卡片应独立为组件，避免继续扩大页面主体逻辑。
- 历史记录可能缺少关联的身份、模型或导师。DTO 允许相关名称为 `null`，前端显示 `未关联`。

## 验收标准

- 个人中心底部出现默认收起的 `Token 消耗记录中心`。
- 展开后能看到全局最近 20 条功能级 token 消耗记录。
- 记录包含智能爬取、匹配分析和 AI 草稿生成。
- 每条记录展示输入、输出、缓存命中、总计 token。
- 缺失 token 字段显示为 `未返回`，页面不崩溃。
- 模板模式草稿不出现在记录中心。
- 后端和前端新增测试覆盖核心汇总与格式化逻辑。
