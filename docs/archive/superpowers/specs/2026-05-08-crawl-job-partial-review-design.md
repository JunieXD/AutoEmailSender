# 智能抓取任务部分导入状态设计

## 背景

当前智能抓取任务在候选审核阶段，只要用户选择一部分候选导师并点击“审核通过并导入”，后端就会把抓取任务从 `needs_review` 改为 `completed`。

这会带来两个问题：

- 前端详情页只在可审核状态显示候选选择、补全和导入操作；任务进入 `completed` 后，剩余候选不能继续处理。
- 后端补全接口只允许 `needs_review`，任务进入 `completed` 后，即使仍有 `pending` 候选，也无法继续补全缺失信息。

用户期望部分导师导入后，仍能继续处理同一任务里的其他候选导师，包括继续补全和继续审核通过。

## 目标

- 支持“部分已导入”的中间状态。
- 用户导入部分候选后，仍能继续处理剩余候选。
- 任务只有在所有可处理候选都不再处于 `pending` 时才进入 `completed`。
- 保持现有候选审核、导师入库和详情补全能力，不重做抓取流程。

## 非目标

- 不改变候选表结构。
- 不改变导师入库去重规则。
- 不增加新的候选批次或子任务模型。
- 不允许已导入候选重复导入。
- 不改变运行中、暂停、失败、取消任务的抓取控制语义。

## 状态模型

新增抓取任务状态：

```text
partially_completed
```

前端显示为：

```text
部分已导入
```

状态含义：

- 抓取流程已经结束。
- 至少有一位候选导师已经审核通过并导入。
- 任务中仍存在 `pending` 候选，可以继续补全或继续审核。

状态流：

```text
needs_review -> partially_completed -> completed
```

具体规则：

- 从 `needs_review` 审核导入一部分候选后，如果还有 `pending` 候选，任务进入 `partially_completed`。
- 从 `needs_review` 审核导入后，如果没有剩余 `pending` 候选，任务进入 `completed`。
- 从 `partially_completed` 继续审核导入后，如果还有 `pending` 候选，任务保持 `partially_completed`。
- 从 `partially_completed` 继续审核导入最后一批候选后，任务进入 `completed`。

## 后端设计

### 状态枚举

在抓取任务状态枚举、DTO 和状态文案中新增 `partially_completed`。

涉及位置：

- `backend/app/models/crawl_job.py`
- `backend/app/schemas/crawl_job.py`
- `backend/app/services/crawl_job_events.py`
- `frontend/src/types/index.ts`

### 审核导入接口

`POST /api/crawl-jobs/{job_id}/approve` 的可操作状态扩展为：

- `needs_review`
- `partially_completed`
- `canceled`

其中 `canceled` 保持现有兼容行为：允许导入已有候选，但不改变任务状态。

导入逻辑保持现有规则：

- 只处理请求中的候选 ID。
- 候选邮箱无效时跳过。
- 已有同邮箱导师时更新。
- 新邮箱导师时新增。
- 成功导入的候选标记为 `accepted`。

提交前重新统计当前任务剩余 `pending` 候选数：

- 若原状态是 `needs_review` 或 `partially_completed`，且剩余 `pending` 大于 0，则任务状态为 `partially_completed`。
- 若原状态是 `needs_review` 或 `partially_completed`，且剩余 `pending` 等于 0，则任务状态为 `completed`。
- 若原状态是 `canceled`，保留 `canceled`。

### 补全接口

`POST /api/crawl-jobs/{job_id}/enrich` 的可操作状态扩展为：

- `needs_review`
- `partially_completed`

补全过程中任务仍可临时进入 `running`，补全结束后恢复到发起补全前的审核状态：

- 从 `needs_review` 发起补全，结束后恢复 `needs_review`。
- 从 `partially_completed` 发起补全，结束后恢复 `partially_completed`。

运行中任务仍返回“候选信息正在补全中，请稍后再试”。

## 前端设计

### 状态显示

任务卡片和详情页状态标签新增：

- `partially_completed` -> “部分已导入”

视觉上使用区别于 `completed` 的提示色，推荐使用琥珀或蓝色系，表达“仍有待处理项”。

### 可审核状态

候选详情区域的可审核状态扩展为：

- `needs_review`
- `partially_completed`
- `canceled`
- `failed`

其中 `failed` 和 `canceled` 继续沿用现有“转入待审核”兼容逻辑；本次核心新增的是 `partially_completed`。

在 `partially_completed` 状态下继续显示：

- 全选可导入
- 全选无邮箱
- 清空选择
- 补全缺失信息
- 审核通过并导入

选择范围仍只包含 `review_status === "pending"` 的候选，避免重复处理已经 `accepted`、`merged` 或 `rejected` 的候选。

### 操作文案

审核确认弹窗文案根据状态调整：

- `partially_completed`：提示“通过后会导入所选候选，任务中剩余待审核候选仍可继续处理。”
- `needs_review`：如果本次不是最后一批，导入后进入“部分已导入”；如果是最后一批，导入后完成。

前端不需要预判最终状态作为业务依据；最终状态以后端返回和重新加载结果为准。

## 删除与列表行为

`partially_completed` 不作为可删除终态。

原因：

- 该状态仍有 `pending` 候选需要处理。
- 如果允许在当前任务列表直接删除，容易让用户误以为剩余候选已经处理完。

任务仍显示在“当前任务”列表。只有进入 `completed`、`failed` 或 `canceled` 后，才沿用现有删除入口。

## 错误处理

- 对 `queued`、`running`、`paused` 调用审核导入，仍返回 409。
- 对 `completed` 调用审核导入，返回 409，提示任务已完成或尚未进入可审核状态。
- 对 `partially_completed` 调用补全时，如果没有可补全候选，沿用现有 400 返回。
- 导入时邮箱无效的候选继续计入跳过，不影响其他候选导入。

## 测试计划

后端测试：

- 部分导入后仍有 `pending` 候选，任务状态变为 `partially_completed`。
- `partially_completed` 状态下可以继续导入剩余候选。
- 最后一批 `pending` 候选导入后，任务状态变为 `completed`。
- `partially_completed` 状态下可以发起候选补全。
- 从 `partially_completed` 发起补全后，补全结束恢复 `partially_completed`。
- `completed` 状态仍拒绝继续审核或补全。

前端测试：

- `partially_completed` 状态显示为“部分已导入”。
- `partially_completed` 任务详情中显示候选选择、补全和审核导入工具条。
- 在 `partially_completed` 状态下点击“审核通过并导入”会调用审核接口。
- 在 `partially_completed` 状态下点击“补全缺失信息”会调用补全接口。
- `partially_completed` 任务卡片不显示删除按钮。

手工验证：

- 创建抓取任务并进入候选审核。
- 只选择一部分有邮箱候选导入，确认任务变为“部分已导入”。
- 继续选择剩余无邮箱候选补全，确认补全后仍可继续审核。
- 导入最后一批候选，确认任务变为“已完成”。

## 成功标准

- 用户不会因为导入部分导师而失去继续处理剩余候选的能力。
- “已完成”只表示当前任务没有剩余待审核候选。
- “部分已导入”清晰表达任务仍可继续处理。
- 后端状态机、前端按钮显示和测试用例对该语义保持一致。
