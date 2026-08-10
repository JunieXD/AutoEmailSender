# Agent CLI Goal 验收报告

- 验收日期：2026-08-09
- 验收目标：按 [`agent_cli_evolution_plan.md`](agent_cli_evolution_plan.md) 第 10 节完成 G1–G10
- 验收分支：`master`
- 验收方式：CLI、后端 Agent API、前端和桌面端的可重复自动化测试；不依赖真实 Agent、模型推理或实时外网

## 结论

G1–G10 全部通过；解析错误、意图搜索、发现闭环、计划语义、构建身份、安装诊断、GUI 动作级覆盖、有界投影、逐页筛选/导出和冷进程性能也已纳入自动化门禁。

## 实时基线

基线来自 `docs/development/agent_cli_baseline.json`，不是手工估计：

| 项目 | 数量 |
| --- | ---: |
| 能力注册总数 | 158 |
| `available` 可用叶子命令 | 156 |
| 写操作 | 90 |
| L2/L3 高风险操作 | 32 |
| 支持分页的集合命令 | 22 |
| 状态化命令 | 71 |
| GUI 业务 API 模块 | 22 |
| 关键并发保护对象 | 8 |

## G1–G10 证据

| 目标 | 结果 | 自动化证据 |
| --- | --- | --- |
| G1 命令合同覆盖率 | 156/156（100%） | `test_every_available_leaf_has_a_complete_schema_validated_contract`；每个可用叶子命令都有输入、输出、效果、前置条件、错误和后续动作合同。 |
| G2 发现链路闭环 | 156/156；元数据漂移 0 | `test_versioned_baseline_lists_match_the_live_capability_registry`、`test_capabilities_and_describe_have_zero_metadata_drift`、意图搜索与缓存修订测试。 |
| G3 集合读取协议 | 22/22（100%） | `test_paged_collection_contracts_expose_common_pagination_projection_filter_and_export`、字段/筛选下推、本地回退、有界 full/expand、流式筛选与 JSONL 导出测试。 |
| G4 状态可解释率 | 71/71（100%） | `test_every_stateful_contract_and_known_state_has_explicit_actions`、`test_state_metadata_covers_non_terminal_partial_and_nested_task_states`，并由后端状态机测试覆盖非法动作。 |
| G5 写入回执覆盖率 | 90/90（100%） | 合同测试要求统一 `mutation_receipt`；后端 Agent API 测试验证实际变更、审计引用、部分结果和幂等回执。 |
| G6 可恢复重试与未知外部执行保护 | 通过 | 后端幂等写入、计划执行、重复发送和 `EXTERNAL_EXECUTION_UNKNOWN` 测试通过；CLI 网络异常测试确认非幂等外部请求不会自动重试。 |
| G7 并发静默覆盖 | 0 次 | `docs/development/agent_cli_concurrency_coverage.json` 中 8 个关键对象均有 `revision`、`If-Revision` 和冲突测试；冲突返回 `REVISION_CONFLICT` 与最新摘要。 |
| G8 高风险安全边界 | 通过 | 高风险合同披露范围、外部服务、费用和确认规则；计划过期/stale、未确认执行和重复执行测试通过。 |
| G9 秘密泄露 | 0 次 | CLI 脱敏、错误/回执字段和后端 DTO 安全测试通过；可用命令合同没有密码、API Key、token 等秘密输入参数。 |
| G10 GUI ↔ CLI 分类覆盖 | 163/163 导出动作（100%），覆盖 22 个模块 | `docs/development/agent_cli_gui_coverage.json` 与 `test_every_business_api_module_has_an_explicit_cli_classification`；每个导出动作均为 `available`、`ui_only` 或 `planned` 并有理由。 |

## 测试结果

| 范围 | 命令 | 结果 |
| --- | --- | --- |
| CLI | `uv run --project cli --no-sync python -m unittest discover cli/test` | 217 tests，全部通过 |
| 后端 | `cd backend && uv run python -m unittest discover test` | 1767 tests，全部通过；实时 Playwright 爬虫测试仅在显式启用时运行 |
| 前端 lint | `cd frontend && npm run lint` | OK |
| 前端 | `cd frontend && npm run test` | 123 files / 950 tests，全部通过 |
| 前端构建 | `cd frontend && npm run build` | OK |
| 桌面端类型检查 | `cd desktop && npm run typecheck` | OK |
| 桌面端 | `cd desktop && npm run test` | 21 files / 170 tests，全部通过 |
| 网站 | `cd website && npm run test && npm run build` | 5 files / 17 tests，全部通过；构建 OK |
| 全仓统一门禁 | `uv run --project backend --no-sync python scripts/quality/run_all_tests.py` | backend、CLI、frontend、desktop、website 全部 PASS，失败 0 |
| 离线发现冒烟 | `capabilities`、`capabilities --query`、`describe`、`guide`、`doctor` | 均返回稳定结构化结果；当前注册能力 158 项 |
| 冷进程性能与意图 | `python scripts/quality/benchmark_agent_cli.py --executable cli/.venv/bin/auto-email-sender --samples 5 --warmup 1` | `capabilities` p95 110.21 ms；`describe` p95 152.57 ms；意图路由 p95 161.31 ms、准确率 100% |
| 打包运行时自检 | `--self-check`、`--document-self-check` | 均通过 |
| 差异检查 | `git diff --check` | 无空白错误 |

被跳过的测试是 `backend/test/test_live_playwright_crawler.py`，只有显式设置
`AUTO_EMAIL_SENDER_LIVE_CRAWLER_TESTS=1` 才运行，需要实时网站；它不属于本 Goal，且没有任何 G1–G10 依赖它。

## 范围说明

- 本 Goal 验收 CLI 合同和产品边界，不声称某个具体 Agent 的自然语言推理一定成功。
- Windows x64、macOS Apple Silicon 安装包认证，以及真实模型、SMTP/IMAP 和外网的端到端验证，仍按文档第 10.5 节作为独立发布认证。
- 验收记录不包含发布签名或远端推送；本地提交状态以 `master` 历史为准。
