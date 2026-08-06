# Token 消耗历史分页与趋势图设计

## 背景

当前个人中心底部已经有 `Token 消耗记录中心`，展开后固定请求最近 20 条功能级记录。这个实现能查看最近记录，但不能查看任意历史页，也不能按功能、时间范围筛选或观察输入/输出 token 的时间趋势。

本设计扩展现有记录中心，不改变 token 的采集来源。记录仍然来自三类已有功能级记录：

- 智能爬取：`crawl_job_runs`
- 匹配分析：`match_analysis_runs`
- AI 草稿：`email_logs.provider_payload.usage`

## 目标

- 支持查看任意历史 token 消耗记录，而不是只看最近固定数量。
- 记录列表一页 5 条。
- 支持上一页、下一页、输入页号跳转。
- 支持按功能筛选：全部、智能爬取、匹配分析、AI 草稿。
- 支持精确到日期小时的时间范围筛选。
- 在列表下方增加柱状趋势图。
- 图表支持最近 6 小时、最近 24 小时、最近 7 天、自定义日期范围。
- 自定义日期范围按自动粒度聚合：范围不超过 48 小时按小时，超过 48 小时按天。
- 图表展示输入 token 和输出 token 的堆叠柱：下方输入，上方输出。

## 非目标

- 不新增统一 token 消耗表。
- 不做费用金额换算。
- 不做导出。
- 不展示底层每次 LLM 调用明细。
- 不把缓存命中 token 加入柱状图。
- 不新增身份、模型维度筛选。

## 方案选择

采用扩展现有后端聚合 API 的方案。

后端继续负责从三类已有来源聚合记录，并在后端完成筛选、排序、计数、分页和图表分桶。前端只传递筛选条件并渲染响应结果。

没有选择新增 `token_usage_records` 表，因为当前目标是历史查询和趋势展示，已有来源能满足需求；新增表会引入迁移、同步和去重成本。也没有选择前端拉取全量后分页统计，因为历史记录增多后会影响加载性能，并且会让个人中心承担过多跨业务聚合逻辑。

## 后端记录接口

扩展现有接口：

```text
GET /api/token-usage/records
```

查询参数：

```text
page: int = 1
page_size: int = 5
feature_type: all | crawl | match_analysis | draft_generation = all
start_at: datetime | null
end_at: datetime | null
```

约束：

- `page >= 1`
- `page_size` 允许 1 到 100，前端固定传 5。
- `feature_type = all` 表示不过滤功能。
- `start_at` 和 `end_at` 都可为空。
- 当 `start_at` 和 `end_at` 同时存在且 `start_at > end_at` 时返回 422。

响应结构：

```json
{
  "records": [],
  "summary": {
    "input_tokens": 0,
    "output_tokens": 0,
    "cached_tokens": 0,
    "total_tokens": 0,
    "record_count": 0
  },
  "pagination": {
    "page": 1,
    "page_size": 5,
    "total_records": 0,
    "total_pages": 0
  }
}
```

分页规则：

- 后端先按筛选条件聚合完整候选集合。
- 再按 `created_at` 倒序排序。
- 再计算 `total_records` 和 `total_pages`。
- 最后按 `page` 和 `page_size` 切片。
- `summary` 汇总当前筛选范围内的全部记录，不只汇总当前页。这样筛选后顶部统计与列表分页保持一致。

## 后端图表接口

新增接口：

```text
GET /api/token-usage/chart
```

查询参数：

```text
feature_type: all | crawl | match_analysis | draft_generation = all
preset: last_6_hours | last_24_hours | last_7_days | custom = last_24_hours
start_at: datetime | null
end_at: datetime | null
```

响应结构：

```json
{
  "preset": "last_24_hours",
  "granularity": "hour",
  "range_start": "2026-04-29T10:00:00+08:00",
  "range_end": "2026-04-30T10:00:00+08:00",
  "buckets": [
    {
      "bucket_start": "2026-04-30T09:00:00+08:00",
      "bucket_label": "09:00",
      "input_tokens": 1240,
      "output_tokens": 280,
      "total_tokens": 1520
    }
  ]
}
```

图表 preset 规则：

- `last_6_hours`：最近 6 小时，按小时聚合。
- `last_24_hours`：最近 24 小时，按小时聚合。
- `last_7_days`：最近 7 天，按天聚合。
- `custom`：使用 `start_at` 和 `end_at`。

自定义范围规则：

- `start_at` 或 `end_at` 缺失时返回 422。
- `start_at > end_at` 时返回 422。
- 范围不超过 48 小时，`granularity = hour`。
- 范围超过 48 小时，`granularity = day`。

分桶规则：

- 按 `created_at` 落入时间桶。
- 空桶返回 0，前端可以显示空柱或极细占位线。
- 每个桶返回输入、输出、总计。
- `cached_tokens` 不进入图表。

## 时间处理

前端时间输入精确到小时，使用本地时间控件。提交给后端前转换为带时区 ISO 字符串。

后端接收带时区 datetime 后按同一时间线比较。若传入无时区 datetime，按服务器本地时区处理并归一化到带时区 datetime。

图表桶标签由后端返回，避免前端重复实现小时和天的标签规则。

## 前端交互

`TokenUsageCenterCard` 继续默认收起。展开后加载第一页记录和默认图表。

筛选区：

- 功能下拉：全部、智能爬取、匹配分析、AI 草稿。
- 开始时间：日期小时输入。
- 结束时间：日期小时输入。
- 查询按钮。
- 重置按钮。

列表区：

- 每页 5 条。
- 展示功能、标题、身份、模型、状态、时间、输入、输出、缓存命中、总计。
- 没有记录时显示空状态。

分页区：

- 上一页。
- 下一页。
- 当前页和总页数。
- 页号输入框。
- 跳转按钮。

分页行为：

- 修改筛选条件并查询后，列表回到第 1 页。
- 输入页号小于 1 或大于总页数时，前端阻止跳转并显示提示。
- 没有记录时隐藏上一页、下一页和跳转按钮。

图表区：

- 图表位于列表下方。
- 图表支持最近 6 小时、最近 24 小时、最近 7 天、自定义范围。
- 默认选择最近 24 小时。
- 自定义范围复用筛选区的开始和结束时间。
- 修改功能筛选后，图表同步使用新的功能类型。
- 图表加载失败时显示错误和重试按钮。

## 柱状图设计

每个时间桶渲染一根堆叠柱：

- 下段为输入 token。
- 上段为输出 token。
- 柱高按 `input_tokens + output_tokens` 相对于当前图表最大桶总量计算。
- 鼠标悬停或聚焦时显示桶标签、输入、输出、总计。
- 图例固定显示输入和输出。

视觉约束：

- 使用简洁的 SVG 或 div 柱状图，不引入重型图表库。
- 长标签时做省略或间隔显示，避免文字重叠。
- 移动端允许横向滚动图表区域。

## 后端实现边界

`backend/app/services/token_usage_records.py` 需要拆分出可复用的内部函数：

- 读取并映射全部候选记录。
- 按功能过滤。
- 按时间范围过滤。
- 计算 summary。
- 计算 pagination。
- 生成图表 buckets。

新增 schema：

- `TokenUsagePaginationRead`
- `TokenUsageChartBucketRead`
- `TokenUsageChartRead`

API 层只负责参数校验和调用 service，不写聚合逻辑。

## 前端实现边界

`frontend/src/components/molecules/TokenUsageCenterCard.tsx` 需要拆分内部小组件，避免单文件继续膨胀：

- 筛选表单。
- summary 卡片组。
- 记录列表。
- 分页控件。
- 趋势图。

新增或扩展前端工具函数：

- token 数值格式化。
- 功能类型参数格式化。
- 日期小时输入与 ISO 字符串互转。
- 页号校验。
- 图表柱高计算。

## 错误与边界

- 没有记录：列表和图表分别显示空状态。
- 功能筛选后没有记录：summary 为 0，列表为空，图表为空。
- 时间范围无效：前端阻止查询，后端返回 422。
- 图表范围过长：自动按天聚合，不额外报错。
- 页号越界：前端阻止跳转，后端返回 422。
- 历史记录缺少身份、模型或导师关联：显示 `未关联`。
- 单条记录缺少 token 字段：显示 `未返回`，summary 和图表按 0 处理。

## 测试计划

后端测试：

- 记录接口一页 5 条。
- 记录接口返回 `total_records` 和 `total_pages`。
- 输入页号可以返回对应历史页。
- 功能筛选只返回指定功能。
- 日期小时范围筛选包含边界内记录，排除范围外记录。
- `start_at > end_at` 返回 422。
- 图表最近 6 小时返回小时粒度。
- 图表最近 24 小时返回小时粒度。
- 图表最近 7 天返回天粒度。
- 自定义范围不超过 48 小时按小时聚合。
- 自定义范围超过 48 小时按天聚合。
- 图表桶正确分别汇总输入和输出。

前端测试：

- 工具函数能校验页号。
- 工具函数能把日期小时输入转换为 ISO 字符串。
- 卡片展开后请求第一页和默认图表。
- 功能筛选后记录和图表请求都带上功能参数。
- 查询时间范围后列表回到第 1 页。
- 输入页号跳转请求对应页。
- 柱状图按输入和输出渲染堆叠段。
- 空数据时显示空状态。

## 验收标准

- 个人中心 `Token 消耗记录中心` 仍默认收起。
- 展开后可以按每页 5 条查看任意历史页。
- 可以输入页号跳转。
- 可以按功能筛选记录。
- 可以按日期小时范围筛选记录。
- 下方图表可以切换最近 6 小时、最近 24 小时、最近 7 天、自定义范围。
- 自定义范围按自动粒度展示。
- 图表每根柱子为输入和输出堆叠。
- 相关后端测试、前端测试、lint 和 build 通过。
