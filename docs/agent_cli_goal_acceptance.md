# Agent CLI Goal 验收报告

- 验收日期：2026-08-05
- 验收目标：按 [`agent_cli_evolution_plan.md`](agent_cli_evolution_plan.md) 第 10 节完成 G1–G10
- 验收分支：`codex/agent-cli`
- 验收方式：CLI、后端 Agent API、前端和桌面端的可重复自动化测试；不依赖真实 Agent、模型推理或实时外网

## 结论

G1–G10 全部通过，可以将本 Goal 标记为完成。当前工作区仍未提交 Git。

## 实时基线

基线来自 `docs/agent_cli_baseline.json`，不是手工估计：

| 项目 | 数量 |
| --- | ---: |
| 能力注册总数 | 148 |
| `available` 可用叶子命令 | 146 |
| 写操作 | 84 |
| L2/L3 高风险操作 | 30 |
| 支持分页的集合命令 | 21 |
| 状态化命令 | 62 |
| GUI 业务 API 模块 | 21 |
| 关键并发保护对象 | 8 |

## G1–G10 证据

| 目标 | 结果 | 自动化证据 |
| --- | --- | --- |
| G1 命令合同覆盖率 | 146/146（100%） | `test_every_available_leaf_has_a_complete_schema_validated_contract`；每个可用叶子命令都有输入、输出、效果、前置条件、错误和后续动作合同。 |
| G2 发现链路闭环 | 146/146；元数据漂移 0 | `test_versioned_baseline_lists_match_the_live_capability_registry`、`test_capabilities_and_describe_have_zero_metadata_drift`、`test_next_actions_only_reference_real_commands_or_generic_wait`。 |
| G3 集合读取协议 | 21/21（100%） | `test_paged_collection_contracts_expose_common_pagination_projection_filter_and_export`、字段选择/结构化筛选/JSONL 导出测试；诊断日志覆盖 offset 分页。 |
| G4 状态可解释率 | 62/62（100%） | `test_every_stateful_contract_and_known_state_has_explicit_actions`、`test_state_metadata_covers_non_terminal_partial_and_nested_task_states`，并由后端状态机测试覆盖非法动作。 |
| G5 写入回执覆盖率 | 84/84（100%） | 合同测试要求统一 `mutation_receipt`；后端 Agent API 测试验证实际变更、审计引用、部分结果和幂等回执。 |
| G6 可恢复重试与未知外部执行保护 | 通过 | 后端幂等写入、计划执行、重复发送和 `EXTERNAL_EXECUTION_UNKNOWN` 测试通过；CLI 网络异常测试确认非幂等外部请求不会自动重试。 |
| G7 并发静默覆盖 | 0 次 | `docs/agent_cli_concurrency_coverage.json` 中 8 个关键对象均有 `revision`、`If-Revision` 和冲突测试；冲突返回 `REVISION_CONFLICT` 与最新摘要。 |
| G8 高风险安全边界 | 通过 | 高风险合同披露范围、外部服务、费用和确认规则；计划过期/stale、未确认执行和重复执行测试通过。 |
| G9 秘密泄露 | 0 次 | CLI 脱敏、错误/回执字段和后端 DTO 安全测试通过；可用命令合同没有密码、API Key、token 等秘密输入参数。 |
| G10 GUI ↔ CLI 分类覆盖 | 21/21（100%） | `docs/agent_cli_gui_coverage.json` 与 `test_every_business_api_module_has_an_explicit_cli_classification`；每项均为 `available`、`ui_only` 或 `planned` 并有理由。 |

## 测试结果

| 范围 | 命令 | 结果 |
| --- | --- | --- |
| CLI | `cd cli && uv run python -m unittest discover -s test` | 111 tests，全部通过 |
| 后端 | `cd backend && uv run python -m unittest discover test` | 1669 tests，全部通过；1 个与本 Goal 无关的实时 Playwright 爬虫测试 skipped |
| 前端 lint | `cd frontend && npm run lint` | OK |
| 前端 | `cd frontend && npm run test` | 114 files / 883 tests，全部通过 |
| 前端构建 | `cd frontend && npm run build` | OK |
| 桌面端类型检查 | `cd desktop && npm run typecheck` | OK |
| 桌面端 | `cd desktop && npm run test` | 28 files / 243 tests，全部通过 |
| 离线发现冒烟 | `capabilities`、`describe`、`guide`、`doctor` | 均返回 `ok: true`；当前注册能力 148 项 |
| 打包运行时自检 | `--self-check`、`--document-self-check` | 均通过 |
| 差异检查 | `git diff --check` | 无空白错误 |

被跳过的测试是 `backend/test/test_live_playwright_crawler.py`，只有显式设置
`AUTO_EMAIL_SENDER_LIVE_CRAWLER_TESTS=1` 才运行，需要实时网站；它不属于本 Goal，且没有任何 G1–G10 依赖它。

## 范围说明

- 本 Goal 验收 CLI 合同和产品边界，不声称某个具体 Agent 的自然语言推理一定成功。
- Windows x64、macOS Apple Silicon 安装包认证，以及真实模型、SMTP/IMAP 和外网的端到端验证，仍按文档第 10.5 节作为独立发布认证。
- 当前工作区没有执行 `git commit` 或 `git push`。
