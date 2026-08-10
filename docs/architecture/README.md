# 架构文档索引

本目录保存 Auto Email Sender 的代码拓扑、模块边界和渐进式重构约束。后续结构调整必须先更新这里，再修改代码。

## 当前执行状态

- 已完成：第 1 批——架构基线与边界门禁。
- 已完成：第 2 批——首个领域切片 `system/runtime-settings`。
- 已完成：第 3A 批——`identities/communication-groups`。
- 已完成：第 3B 批——`identities/profiles` 与 materials DTO/serializer 基础。
- 已完成：第 3C 批——`identities/materials` 生命周期行为与 UI adapter。
- 已完成：第 4A 批——`professors` 核心、标签与导入导出。
- 已完成：第 4B 批——`professors/enrichment` 导师信息补全子切片。
- 已完成：第 4C 批——`community/mentors` 社区导师库子切片。
- 已完成：第 4D 批——Frontend professor/community 实体 API 边界收敛。
- 已完成：第 5A 批——`matching` 核心与 analysis jobs。
- 已完成：第 5B 批——`llm` profile、runtime 与 adaptation。
- 已完成：第 6A 批——`crawler` 合同与运行基础。
- 已完成：第 6B 批——`crawler` adapters、job runtime 与 workers。
- 已完成：第 7A 批——`campaigns` 活动、模板与批量草稿规则。
- 已完成：第 7B 批——`communications` 传输、同步与邮件历史。
- 已完成：第 7C 批——`workspace`、email-task 状态机与发送编排。
- 已完成：第 8 批——Desktop 进程模块化与 IPC 合同收敛。
- 已完成：第 9 批——测试拓扑、脚本分类、文档归档与确认后的遗留清理。
- 当前状态：第 1～9 批全部完成；后续结构调整以模块地图和依赖规则为准，另立独立批次。
- 已完成前置实现：桌面端 API + Worker 双进程改造 G0～G5；原 Goal 已取消，证据保留。
- 当前计划：通用 Beta 验证 Goal，补齐拓扑切换、本地诊断、Prerelease 隔离与双平台 Dogfood。
- 重构原则：每批只做一个可独立验证的结构变化，不同时改变 API、数据库 schema 与业务行为。

## 文档

- [模块化重构总计划](./modularization-plan.md)：目标拓扑、批次、验收命令和停止条件。
- [模块地图](./module-map.md)：领域所有权、跨模块关系与现有文件归属。
- [依赖规则](./dependency-rules.md)：允许的依赖方向、当前技术债基线和自动门禁要求。
- [桌面端后端 API + Worker 双进程改造 Goal 执行计划](./desktop-api-worker-process-plan.md)：
  前置目标进程拓扑、邮件 at-most-once 取舍、实施阶段、故障矩阵和已有验收门槛。
- [桌面 API + Worker 通用 Beta 验证 Goal 执行计划](./desktop-api-worker-beta-goal-plan.md)：
  当前 Goal 的通用 Prerelease、拓扑设置、本地诊断、双平台 Dogfood 和人工批准门。
- [Agent UI handoff](./agent-ui-handoffs.md)：CLI 到桌面页面的短期、类型化导航与临时选择协议。

## 更新规则

每个重构批次必须同步维护：

1. 总计划中的状态与实际范围。
2. 模块地图中的所有权或公共入口。
3. 依赖规则中的临时例外；例外只能减少，不能无说明增加。
4. 本批实际执行的验证命令与结果。
