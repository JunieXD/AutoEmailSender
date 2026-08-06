# 首页导师状态模型设计

## 1. 背景

首页当前把 `EmailTask.status` 映射成导师状态。这个做法把两类语义混在一起：

- `EmailTask.status` 表示单封邮件任务的执行进度。
- 首页导师状态表示「这位导师和当前身份、模型组合之间的关系进展」。

混用后会出现语义错误。例如某位导师已经成功发送过邮件，后续另一个定时任务因为发送窗口过期变成 `canceled`，首页却显示「需处理」。用户看到后无法判断要处理什么。

本次设计目标是收紧首页状态语义，避免任务取消、任务失败等细节污染导师关系状态。

## 2. 设计目标

- 首页只展示导师关系状态，不展示底层任务细节。
- 状态数量保持较少，避免用户在首页看到过多流程术语。
- 取消「需处理」这个模糊状态，改用更明确的「失败」。
- `canceled` 不再直接成为首页状态来源。
- 后端集中维护一个可测试的状态派生函数。

## 3. 非目标

- 不重做任务中心的任务状态展示。
- 不改变 `EmailTask.status` 的数据库枚举。
- 不引入状态机库。
- 不改变邮件发送、草稿生成、回信检测等执行流程。

## 4. 状态分层

### 4.1 邮件任务状态

`EmailTask.status` 继续表示单封邮件任务的执行进度：

```text
discovered -> matched -> generating_draft -> review_required -> approved -> scheduled -> sent -> reply_detected
```

异常分支：

```text
generating_draft -> draft_failed
approved/scheduled -> send_failed
任意未终态 -> canceled
```

恢复路径：

```text
draft_failed -> generating_draft
send_failed -> approved/scheduled
canceled -> 新建手动任务，不复活原任务
```

### 4.2 首页导师状态

首页导师状态保留 6 个：

| 状态值 | 中文文案 | 语义 |
| --- | --- | --- |
| `not_contacted` | 未开始 | 没有成功联系历史，也没有当前有效任务。 |
| `preparing` | 准备中 | 正在匹配、生成草稿或等待审核。 |
| `ready_to_send` | 待发送 | 已批准或已排程，等待发送。 |
| `contacted` | 已联系 | 至少成功发出过邮件，尚未检测到回复。 |
| `replied` | 已回复 | 已检测到导师回复。 |
| `failed` | 失败 | 尚未成功联系过，当前任务发生草稿生成失败或发送失败。 |

删除 `needs_attention`。它不再作为首页导师状态出现。

## 5. 派生规则

首页状态由当前身份、当前模型下的任务与发送记录派生。优先级固定如下：

```text
1. 有回复记录，或任一任务为 reply_detected / is_replied -> replied
2. 有发送记录，或任一任务为 sent / sent_at 非空 -> contacted
3. 最新任务为 draft_failed / send_failed -> failed
4. 最新任务为 approved / scheduled -> ready_to_send
5. 最新任务为 discovered / matched / generating_draft / review_required -> preparing
6. 其他情况 -> not_contacted
```

说明：

- `canceled` 不参与首页状态判断。未发送过且最新任务取消时，首页显示 `not_contacted`。
- 已联系导师后续任务取消或失败时，首页仍显示 `contacted`。
- 已回复导师后续任务取消或失败时，首页仍显示 `replied`。
- 任务失败细节由任务中心或工作区展示，首页只保留「失败」这个关系层面的提示。

## 6. 典型场景

| 场景 | 任务/记录 | 首页状态 |
| --- | --- | --- |
| 从未创建任务 | 无任务，无发送记录 | 未开始 |
| 已完成匹配分析 | 最新任务 `matched` | 准备中 |
| 草稿生成中 | 最新任务 `generating_draft` | 准备中 |
| 草稿待审核 | 最新任务 `review_required` | 准备中 |
| 已批准等待发送 | 最新任务 `approved` | 待发送 |
| 已排程等待发送 | 最新任务 `scheduled` | 待发送 |
| 首次发送失败 | 最新任务 `send_failed`，无发送记录 | 失败 |
| 首次草稿生成失败 | 最新任务 `draft_failed`，无发送记录 | 失败 |
| 未发送任务被取消 | 最新任务 `canceled`，无发送记录 | 未开始 |
| 已发送后批量停止 | 有发送记录，后续任务 `canceled` | 已联系 |
| 已发送后后续发送失败 | 有发送记录，后续任务 `send_failed` | 已联系 |
| 已回复后后续任务取消 | 有回复记录，后续任务 `canceled` | 已回复 |

## 7. 取消原因

`canceled` 表示任务停止执行，不表示导师关系状态。取消原因继续保存在 `cancellation_reason` 中：

| 值 | 含义 | 展示位置 |
| --- | --- | --- |
| `batch_stopped` | 批量任务被停止 | 任务中心、工作区 |
| `schedule_expired` | 发送窗口过期 | 任务中心、工作区 |
| `user_canceled` | 用户主动取消 | 后续扩展 |
| `superseded` | 被新任务取代 | 后续扩展 |

首页不展示这些原因。

## 8. 实现边界

后端新增或收敛一个纯函数，用于派生首页导师状态：

```python
def derive_professor_dashboard_status(
    tasks: list[EmailTask],
    sent_count: int,
) -> str:
    ...
```

`list_professors` 只调用该函数，不在接口中散落状态判断逻辑。

前端改动保持最小：

- `ProfessorDashboardStatus` 移除 `needs_attention`，新增 `failed`。
- 首页状态文案把 `failed` 显示为「失败」。
- 过滤选项自动来自状态文案表，无需单独维护。

## 9. 测试要求

后端至少覆盖以下矩阵：

- 无任务 -> `not_contacted`
- `matched` -> `preparing`
- `generating_draft` -> `preparing`
- `review_required` -> `preparing`
- `approved` -> `ready_to_send`
- `scheduled` -> `ready_to_send`
- 无发送记录 + `draft_failed` -> `failed`
- 无发送记录 + `send_failed` -> `failed`
- 无发送记录 + `canceled` -> `not_contacted`
- 有发送记录 + 后续 `canceled` -> `contacted`
- 有发送记录 + 后续 `send_failed` -> `contacted`
- 有回复记录 + 后续 `canceled` -> `replied`

前端至少覆盖：

- `failed` 显示为「失败」。
- 状态筛选包含「失败」，不再包含「需处理」。

## 10. 成功标准

- 首页不再出现「需处理」。
- 已发送导师不会因为后续取消任务变成异常状态。
- 未发送且被取消的导师显示「未开始」。
- 真正需要用户修复的首次草稿失败、首次发送失败显示「失败」。
- 现有任务中心和工作区仍能展示底层任务状态与取消原因。
