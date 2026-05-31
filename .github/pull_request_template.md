## 变更说明

请简要说明这次改动做了什么，解决了什么问题。

## 测试情况

- [ ] 已运行相关单元测试
- [ ] 已运行相关前端测试 / lint / build
- [ ] 已完成必要的手动验证

## 时间处理检查

- [ ] 本次改动涉及的时间字段已标明是 Instant 还是 Civil Time。
- [ ] Instant 字段在数据库中按 UTC 语义存储，模型侧使用 `UTCDateTime()`。
- [ ] API 输出的 Instant 带 `Z` 或显式 offset。
- [ ] 前端 API 时间解析走 `frontend/src/lib/dateTime.ts`。
- [ ] 涉及调度、worker、筛选或图表时，已补充 Asia/Shanghai 回归测试。