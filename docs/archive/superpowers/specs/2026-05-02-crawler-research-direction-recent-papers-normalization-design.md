# 智能抓取研究方向与近期论文归一化设计

## 背景

当前系统在导师信息字段上存在两类不一致：

- `research_direction` 语义上是“可多方向”，但存储层是 `string`，来源可能是单字符串或数组，分隔符不稳定。
- `recent_papers` 语义上是“论文列表”，但智能抓取与人工导入可能给出数组或拼接字符串，且数量未受控。

相关代码位置：

`@backend/app/services/crawler_tools.py:125`

`@backend/app/services/crawler_tools.py:132`

`@backend/app/services/professor_management.py:40`

`@backend/app/models/professor.py:26`

`@backend/app/models/professor.py:27`

## 目标

- 保持输入兼容宽松（数组/字符串都可接收），避免抓取成功率因格式收紧下降。
- 统一存储与下游消费语义，减少匹配、工作区和审核界面的格式漂移。
- 将 `recent_papers` 数量限制为最多 8 篇，并固定“保留前 8 篇”。
- 在提示词侧提高 LLM 输出稳定性：优先输出数组格式。

## 非目标

- 不调整数据库字段类型（本次不把 `research_direction` 改为数组字段）。
- 不引入基于年份或引用量的论文排序策略。
- 不在本次引入复杂的数据清洗规则（如语言检测、标题相似度聚类）。

## 决策

### 决策 1：输入宽松，存储统一

- `research_direction`
  - 接受 `string | list[string]`。
  - 若为数组：逐项 `trim`、去空后用中文分号 `；` 连接。
  - 若为字符串：保留现有字符串语义，仅做必要清洗，不强制重排语义片段。
  - 入库保持 `string | null`。
- `recent_papers`
  - 接受 `list[string]` 或 `string`。
  - 字符串按 `|`、`；`、`;`、换行拆分。
  - 统一做 `trim`、去空、保序去重后再截断。
  - 入库保持 `list[string]`。

### 决策 2：LLM 输出强约束 + 服务端兜底

- 在抓取相关提示词中明确要求：
  - `recent_papers` 必须输出 JSON 数组，例如 `["Paper A", "Paper B"]`。
  - 不要输出分隔符拼接字符串。
- 服务端继续保留兜底逻辑：
  - 若 LLM 仍返回字符串，按统一规则拆分并归一化，避免请求失败或数据丢失。

### 决策 3：`recent_papers` 上限 8，保留前 8

- 归一化后论文数超过 8 时，保留前 8 篇，丢弃其余项。
- “前 8”的顺序定义为：当前输入顺序（页面提取顺序或 LLM 返回顺序）。
- 不做倒序、按时间推断或二次排序。

### 决策 4：可观测性

- 当触发截断（`len(normalized_recent_papers) > 8`）时记录结构化日志，至少包含：
  - 候选导师标识（如姓名/邮箱/候选 ID）
  - 原始条目数
  - 最终保留数（固定为 8）
  - 触发阶段（候选提取、候选补全或人工审核保存）

## 影响范围

### 后端

- 抓取候选归一化与校验：
  - `@backend/app/services/crawler_tools.py`
- 导入与手动更新一致性：
  - `@backend/app/schemas/professor.py`
  - `@backend/app/schemas/crawl_job.py`
  - `@backend/app/services/professor_management.py`
- 运行时补全合并逻辑：
  - `@backend/app/services/crawl_job_runtime.py`

### 前端

- 导师管理导入/提示文案与后端规则保持一致：
  - `@frontend/src/pages/ProfessorsPage.tsx`
- 抓取候选详情展示无需改协议，但需确认文案与截断规则一致：
  - `@frontend/src/pages/TasksPage.tsx`

## 数据流

1. LLM 或解析器产出候选数据。
2. `recent_papers` 按“优先数组、字符串兜底”规则归一化。
3. 对 `recent_papers` 执行：去空 -> 保序去重 -> 截断前 8。
4. `research_direction` 执行统一清洗（数组转 `；` 分隔字符串）。
5. 写入候选或教授实体。
6. 若触发截断，记录结构化日志。

## 错误处理

- 对于 `recent_papers` 的格式漂移（字符串而非数组），不报错中断，走兜底解析。
- 对于解析后为空列表，允许为空，不视为错误。
- 对于超限，仅截断并记录日志，不返回用户可见错误。

## 测试计划

- 单元测试：
  - `recent_papers` 为数组时正常归一化，超过 8 篇保留前 8。
  - `recent_papers` 为字符串时按 `|/；/;/换行` 正确拆分并截断。
  - 去重为保序去重（首次出现保留）。
  - `research_direction` 为数组时统一为 `；` 分隔字符串。
- 集成/API 测试：
  - 抓取候选、导师导入、手动更新三条路径行为一致。
  - 触发截断时写入日志且数据可正常入库。
- 回归检查：
  - 匹配分析、工作区详情、任务页候选详情在新规则下无行为回归。

## 验收标准

- 任意来源输入下，`recent_papers` 最终都满足：
  - 类型为 `list[string]`
  - 最多 8 项
  - 无空字符串
  - 保序去重
- 任意来源输入下，`research_direction` 最终都满足：
  - 类型为 `string | null`
  - 若来源为数组，统一为 `；` 分隔
- LLM 返回非数组字符串时，系统仍可稳定落库并符合上述约束。

## 自检

- 无占位符、无 “TODO” 未决项。
- “上限 8” 与“保留前 8”在所有章节表述一致。
- 已明确“提示词强约束 + 服务端兜底”的双层策略，避免单点失败。
- 改动范围聚焦在字段归一化与提示词，不引入数据库类型迁移。
