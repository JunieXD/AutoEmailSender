# 智能抓取保存失败熔断设计

## 背景

最近两次智能抓取暴露了同一类运行风险：模型在 `save_professor_candidates` 上反复重试，导致 token 和时间被消耗在低价值循环里。

- `crawl-job-6`：候选已经保存成功，但压缩后的历史没有保留已保存候选身份，模型继续尝试保存同一批候选。
- `crawl-job-7`：模型多次提交同一批候选，但字段类型存在可恢复偏差，保存工具持续返回 rejected，直到用户取消任务。

已有修复覆盖了两个直接问题：保存历史摘要保留候选身份，保存工具归一化常见字段类型偏差。仍需要一个运行时保护机制：当保存环节反复失败时，系统应尽早停止任务，而不是继续让模型消耗 token。

## 目标

- 同一候选批次连续保存失败达到阈值后，立即终止抓取任务。
- 不同候选批次反复保存失败达到总预算后，也终止抓取任务。
- 终止时把任务状态标记为 `failed`，并写入清晰的错误信息，便于用户和开发者判断原因。
- 保持实现范围小，不引入新的数据库表或复杂调度机制。

## 非目标

- 不做跨任务的模型质量统计。
- 不做复杂自动修复或二次 LLM 纠错。
- 不改变候选审核、入库教授库、暂停/取消等现有业务流程。
- 不把暂停或取消造成的 `saved_count=0` 计入保存失败预算。

## 方案选择

本设计采用「混合熔断」：

- 同一批候选连续失败 2 次，任务失败。
- 任意保存失败累计 4 次，任务失败。
- 一旦保存成功，清空同批连续失败计数，但保留总失败次数。

这个方案比单纯累计失败更精准，也比只识别同一批失败更能防止模型不断换一批坏数据继续消耗 token。

## 批次指纹

保存工具为每次 `save_professor_candidates` 调用生成批次指纹。指纹只使用稳定身份字段：

- `name`
- `email`
- `profile_url`

指纹生成规则：

1. 对每个候选提取上述字段，做 trim 和小写归一化。
2. 按候选身份字符串排序，避免模型调整顺序导致指纹变化。
3. 组合成批次字符串后计算短哈希。
4. 忽略 `field_confidence`、`evidence`、`recent_papers` 等容易漂移的字段。

这样可以识别「同一批导师，只是模型不断调整证据和置信度格式」的循环。

## 状态存放

优先把熔断状态保存在 `CrawlToolContext` 中，作为单次运行内的内存状态：

- `last_failed_save_fingerprint`
- `same_batch_save_failures`
- `total_save_failures`
- `last_save_failure_summary`

原因：

- 熔断只需要保护当前运行，不需要跨任务持久化。
- 不增加数据库迁移成本。
- 与现有 `http_blocked_hosts` 运行时状态模式一致。

如果未来需要在暂停/恢复后延续失败预算，再考虑把状态写入 `CrawlJobRun` 或 `agent_trace`。

## 保存工具行为

`save_professor_candidates` 保持模型可见返回值结构，但增加失败预算信息：

```json
{
  "batch_status": "rejected",
  "attempted_count": 10,
  "saved_count": 0,
  "failed_count": 10,
  "failed_items": [],
  "total_saved_count": 0,
  "retry_allowed": true,
  "failure_fingerprint": "abc123",
  "consecutive_same_batch_failures": 1,
  "total_save_failures": 1,
  "terminal_reason": null
}
```

当达到熔断条件时，工具不再返回普通 rejected，而是抛出内部异常，例如 `CrawlJobSaveBudgetExceeded`。异常包含：

- 最近批次指纹。
- 同批连续失败次数。
- 总失败次数。
- 最近失败项摘要。

## 运行时行为

`crawl_job_runtime` 捕获 `CrawlJobSaveBudgetExceeded` 后执行以下动作：

1. 标记 `CrawlJob.status = failed`。
2. 标记当前 `CrawlJobRun.status = failed`。
3. 写入 `error_message`，例如：

```text
抓取结果未成功保存：同一候选批次连续保存失败 2 次，已停止以避免继续消耗 token。最近失败：field_confidence: Input should be a valid dictionary
```

4. 记录一条 trace 事件，便于前端展示和后续排查。

暂停和取消继续走现有 `CrawlJobPaused`、`CrawlJobCanceled` 分支，不进入保存失败熔断。

## 与部分保存的关系

本轮先不做部分保存。原因是用户当前优先级是节省 token 和时间，连续失败后直接让任务失败。部分保存会引入新的边界问题：

- 部分成功时如何向模型描述剩余失败项。
- 后续同批重试是否应该只保存失败项。
- 审核页如何表达「一批中部分候选已保存、部分被拒绝」。

因此本设计把部分保存作为后续增强，不纳入本次实现范围。

## 测试设计

新增或调整以下测试：

- 同一批候选连续 rejected 2 次后，`run_queued_crawl_jobs_once` 将任务标记为 failed。
- 不同批次累计 rejected 4 次后，任务标记为 failed。
- 保存成功后，同批连续失败计数清零。
- 暂停或取消造成的 `saved_count=0` 不计入失败预算。
- 批次指纹忽略候选顺序和非身份字段。

## 风险与取舍

- 阈值过低可能让模型失去一次自我纠错机会；本设计用同批 2 次、总计 4 次保留有限重试空间。
- 阈值过高会继续浪费 token；因此不建议超过同批 3 次或总计 5 次。
- 指纹只看身份字段，可能把同名不同人误判为同一批；同时包含 email 和 profile_url 可降低误判概率。

## 验收标准

- job 7 这类同一批候选反复 rejected 的情况，会在第二次同批失败后停止。
- 任务最终状态为 `failed`，不是长时间 `running` 或依赖用户取消。
- `error_message` 能说明触发熔断的原因和最近失败字段。
- 现有抓取成功路径、暂停、取消、无候选失败路径不回归。
