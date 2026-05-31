# 时间处理治理实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 按 `docs/superpowers/specs/time_handling_spec.md` 统一 AutoEmailSender 的时间处理，彻底解决 SQLite 时区丢失、worker 租约误判、运行时长误算、API/前端解析歧义，并阻止新增代码再次引入同类问题。

**架构：** 后端建立唯一时间工具和 `UTCDateTime` ORM 类型，所有 Instant 在应用层恢复为 UTC-aware datetime；服务层先治理 crawler、运行时长、发送调度、统计筛选等高风险路径，再迁移模型和 API 序列化。前端只通过 `frontend/src/lib/dateTime.ts` 解析 API 时间，最后用审计脚本、静态检查、PR 模板和 CI 分阶段门禁固化规则。

**技术栈：** Python 3.12、FastAPI、Pydantic v2、SQLAlchemy、SQLite、unittest、PowerShell 7、React、TypeScript、Vitest、Vite

---

## File Map

- 创建：`backend/app/core/time.py`，提供 `utc_now()`、`local_now()`、`as_utc_aware()`、`as_utc_naive()`、`parse_api_datetime()`、`serialize_api_datetime()`。
- 创建：`backend/app/models/types.py`，提供 `UTCDateTime`，SQLite 写入 UTC-naive，读出 UTC-aware。
- 创建：`backend/test/test_time_utils.py`，覆盖时间工具和 `UTCDateTime` 读写。
- 修改：`backend/app/models/*.py`，把 Instant 字段迁移到 `UTCDateTime()`，把 `onupdate=lambda: datetime.now(UTC)` 改为 `onupdate=utc_now`。
- 修改：`backend/app/services/crawler_v2_scheduler.py`、`crawler_v2_page_worker.py`、`crawler_v2_chunk_worker.py`、`crawler_v2_enrichment_worker.py`，统一 worker 租约比较。
- 修改：`backend/app/services/crawl_job_runs.py`、`task_runtime.py`、`token_usage_records.py`、`match_analysis_job_runtime.py`，统一运行时长、发送调度、统计筛选时间语义。
- 创建：`backend/app/schemas/base.py`，统一 Pydantic datetime JSON 序列化。
- 修改：`backend/app/schemas/*.py` 和相关 API，确保响应中的 Instant 带 `Z` 或 offset。
- 修改：`frontend/src/lib/dateTime.ts` 和 `frontend/src/lib/dateTime.test.ts`，成为前端唯一 API Instant 解析入口。
- 修改：`frontend/src/components/molecules/OtherSettingsCard.tsx`、`frontend/src/components/organisms/DiagnosticLogPanel.tsx`、`frontend/src/pages/DashboardPage.tsx`、`frontend/src/features/token-usage/client/tokenUsage.ts`、`frontend/src/pages/WorkspacePage.tsx`。
- 创建：`backend/scripts/audit_time_data.py` 和 `scripts/audit-time-data.ps1`，只读审计历史时间数据。
- 创建：`scripts/check-time-usage.ps1`，扫描后端、前端和迁移脚本的高风险时间写法。
- 修改或创建：`.github/pull_request_template.md`，加入时间处理检查项。
- 修改：`.github/workflows/*.yml`（如果存在），分阶段加入时间检查。

## Task 1：建立时间基础设施

**文件：**
- 创建：`backend/app/core/time.py`
- 创建：`backend/app/models/types.py`
- 创建：`backend/test/test_time_utils.py`

- [ ] **步骤 1：编写失败测试**
  在 `backend/test/test_time_utils.py` 覆盖：naive datetime 按 UTC 解释、`+08:00` 转 UTC、`serialize_api_datetime()` 输出 `Z`、`parse_api_datetime()` 拒绝 `YYYY-MM-DD`、`UTCDateTime` 在 SQLite 中读出 UTC-aware。

- [ ] **步骤 2：运行测试验证失败**
  运行：`cd backend; uv run python -m unittest test.test_time_utils`
  预期：失败，原因是 `app.core.time` 或 `app.models.types` 尚不存在。

- [ ] **步骤 3：实现时间工具**
  在 `backend/app/core/time.py` 按规格实现 6 个函数。`as_utc_aware()` 对 naive 值使用 `replace(tzinfo=UTC)`，对 aware 值使用 `astimezone(UTC)`；`serialize_api_datetime()` 去掉微秒并输出 `Z`。

- [ ] **步骤 4：实现 `UTCDateTime`**
  在 `backend/app/models/types.py` 实现 SQLAlchemy `TypeDecorator`。`process_bind_param()` 对 SQLite 返回 UTC-naive，其他数据库返回 UTC-aware；`process_result_value()` 统一返回 UTC-aware。

- [ ] **步骤 5：运行测试验证通过**
  运行：`cd backend; uv run python -m unittest test.test_time_utils`
  预期：`OK`。

- [ ] **步骤 6：Commit**
  `git add backend/app/core/time.py backend/app/models/types.py backend/test/test_time_utils.py`
  `git commit -m "feat(backend): add UTC time primitives"`

## Task 2：优先修复 crawler v2 租约路径

**文件：**
- 修改：`backend/app/services/crawler_v2_scheduler.py`
- 修改：`backend/app/services/crawler_v2_page_worker.py`
- 修改：`backend/app/services/crawler_v2_chunk_worker.py`
- 修改：`backend/app/services/crawler_v2_enrichment_worker.py`
- 修改：`backend/test/test_crawler_v2_scheduler.py`
- 修改：`backend/test/test_crawler_v2_page_worker.py`
- 修改：`backend/test/test_crawler_v2_chunk_worker.py`
- 修改：`backend/test/test_crawler_v2_enrichment_worker.py`

- [ ] **步骤 1：补充回归测试**
  为 page、chunk、enrichment worker 增加「SQLite 读出的 naive UTC `lease_expires_at` 仍在未来时，任务不能被误判过期」测试；为 scheduler 增加「过期 processing 可重新领取，未过期 processing 不被重复领取」测试。

- [ ] **步骤 2：运行测试确认失败或锁定行为**
  运行：`cd backend; uv run python -m unittest test.test_crawler_v2_scheduler test.test_crawler_v2_page_worker test.test_crawler_v2_chunk_worker test.test_crawler_v2_enrichment_worker`
  预期：旧代码下至少有新增风险场景失败；如果当前已有临时修复，则测试通过并锁住行为。

- [ ] **步骤 3：替换手写时间比较**
  在 4 个服务文件中导入 `as_utc_aware` 和 `utc_now`。把 `lease_expires_at.replace(tzinfo=UTC)`、`datetime.now(UTC)` 等手写逻辑替换为 `as_utc_aware(lease_expires_at) > utc_now()` 或 `<= utc_now()`。

- [ ] **步骤 4：运行测试验证通过**
  运行同上 unittest 命令，预期 `OK`。

- [ ] **步骤 5：Commit**
  `git commit -m "fix(backend): normalize crawler leases as UTC"`

## Task 3：治理运行时长、发送调度和统计筛选

**文件：**
- 修改：`backend/app/services/crawl_job_runs.py`
- 修改：`backend/app/services/task_runtime.py`
- 修改：`backend/app/services/token_usage_records.py`
- 修改：`backend/app/services/match_analysis_job_runtime.py`
- 修改：`backend/test/test_crawl_job_runs.py`
- 修改：`backend/test/test_runtime_manager.py` 或现有发送调度测试
- 修改：`backend/test/test_crawler_v2_token_usage.py`
- 修改：`backend/test/test_match_analysis_runtime.py`

- [ ] **步骤 1：补充 `active_seconds` 回归测试**
  构造 `started_at`、`active_started_at` 为 naive UTC，`now` 为 UTC-aware，断言 10 分钟结算为 600 秒，不多算或少算 8 小时。

- [ ] **步骤 2：补充发送调度回归测试**
  在 Asia/Shanghai 场景下，用户选择本地 `2026-05-31 09:00` 时，生成的 `scheduled_at` 应为 `2026-05-31T01:00:00Z`；`scheduled_dates` 仍保持本地日期字符串语义。

- [ ] **步骤 3：补充统计筛选测试**
  token usage 和 match analysis 中，SQLite naive UTC 时间参与 bucket、开始结束时间、持续时间计算时必须先恢复为 UTC-aware。

- [ ] **步骤 4：运行测试确认风险**
  运行：`cd backend; uv run python -m unittest test.test_crawl_job_runs test.test_runtime_manager test.test_crawler_v2_token_usage test.test_match_analysis_runtime`

- [ ] **步骤 5：实现修复**
  所有 Instant 比较和相减前调用 `as_utc_aware()`；当前 UTC 时间用 `utc_now()`；本地日历窗口用 `local_now()` 或显式 `local_timezone`；禁止把 Civil Time 直接当 Instant 入库。

- [ ] **步骤 6：运行测试验证通过**
  运行同上 unittest 命令，预期 `OK`。

- [ ] **步骤 7：Commit**
  `git commit -m "fix(backend): normalize runtime duration timestamps"`

## Task 4：迁移模型 Instant 字段

**文件：**
- 修改：`backend/app/models/app_setting.py`、`batch_task.py`、`crawl_chunk.py`、`crawl_job.py`、`email_log.py`、`email_task.py`、`identity_material.py`、`identity_profile.py`、`imap_sync.py`、`llm_profile.py`、`match_analysis_job.py`、`match_analysis_run.py`、`operation_log.py`、`professor.py`、`test_compose_message.py`、`test_compose_session.py`、`thinking_adaptation_cache.py`
- 修改：相关模型和迁移测试

- [ ] **步骤 1：补充模型读出测试**
  覆盖代表性字段：`CrawlChunk.lease_expires_at`、`CrawlJobRun.started_at`、`EmailTask.scheduled_at`、统计记录 `created_at`。写入 naive UTC 后，刷新读出必须是 UTC-aware。

- [ ] **步骤 2：运行测试确认旧类型风险**
  运行：`cd backend; uv run python -m unittest test.test_crawl_job_models test.test_migrated_database test.test_crawl_job_partial_completed_migration`

- [ ] **步骤 3：替换字段类型**
  把 Instant 字段的 `DateTime(timezone=True)` 改成 `UTCDateTime()`；保留 SQLite `server_default=text("CURRENT_TIMESTAMP")`；把 `onupdate=lambda: datetime.now(UTC)` 改成 `onupdate=utc_now`。

- [ ] **步骤 4：扫描确认无遗漏**
  运行：`rg -n "DateTime\(timezone=True\)|onupdate=lambda: datetime\.now\(UTC\)" backend/app/models`
  预期：无未豁免结果。确实不是 Instant 的字段必须添加 `time-check` 原因注释。

- [ ] **步骤 5：运行模型和迁移测试**
  运行同上 unittest 命令，预期 `OK`。

- [ ] **步骤 6：Commit**
  `git commit -m "refactor(backend): map instant columns through UTCDateTime"`

## Task 5：统一 API datetime 序列化

**文件：**
- 创建：`backend/app/schemas/base.py`
- 修改：`backend/app/schemas/*.py`
- 修改：相关 API 测试

- [ ] **步骤 1：编写序列化测试**
  创建 `backend/test/test_api_datetime_serialization.py`。定义一个继承共享基类的 schema，断言 aware 和 naive datetime 在 `model_dump(mode="json")` 中都输出 `2026-05-31T06:44:37Z`。

- [ ] **步骤 2：运行测试确认失败**
  运行：`cd backend; uv run python -m unittest test.test_api_datetime_serialization`

- [ ] **步骤 3：实现共享 schema 基类**
  在 `backend/app/schemas/base.py` 中提供 `ApiSchema`，复用 `serialize_api_datetime()`。优先使用 Pydantic v2 serializer；若版本不支持通配 serializer，则使用 `ConfigDict(json_encoders={datetime: serialize_api_datetime})`。

- [ ] **步骤 4：迁移响应 schema**
  包含 Instant 字段或 `from_attributes=True` 的响应 schema 继承 `ApiSchema`，删除重复配置或合并配置。

- [ ] **步骤 5：运行 API 测试**
  运行：`cd backend; uv run python -m unittest test.test_api_datetime_serialization test.test_crawl_jobs_api test.test_runtime_settings_api`
  预期：`OK`，JSON 时间带 `Z` 或 offset。

- [ ] **步骤 6：Commit**
  `git commit -m "feat(backend): serialize API datetimes as UTC"`

## Task 6：统一前端 API 时间解析

**文件：**
- 修改：`frontend/src/lib/dateTime.ts`
- 修改：`frontend/src/lib/dateTime.test.ts`
- 修改：`frontend/src/components/molecules/OtherSettingsCard.tsx`
- 修改：`frontend/src/components/organisms/DiagnosticLogPanel.tsx`
- 修改：`frontend/src/pages/DashboardPage.tsx`
- 修改：`frontend/src/features/token-usage/client/tokenUsage.ts`
- 修改：`frontend/src/pages/WorkspacePage.tsx`

- [ ] **步骤 1：补充 `dateTime` 测试**
  覆盖 `2026-05-31T06:44:37Z`、`2026-05-31T14:44:37+08:00`、历史无时区 `2026-05-31T06:44:37`、历史空格格式 `2026-05-31 06:44:37`。无时区历史值必须按 UTC 解析。

- [ ] **步骤 2：运行测试确认失败或锁定行为**
  运行：`cd frontend; npm run test -- src/lib/dateTime.test.ts`

- [ ] **步骤 3：实现解析规则**
  `parseApiDateTime()` 先 trim，空格格式转 `T`，无 `Z`/offset 的完整 datetime 追加 `Z`，再 `new Date()`；`formatApiDateTime()` 必须基于 `parseApiDateTime()`。

- [ ] **步骤 4：替换风险点**
  把 API 字段上的 `new Date(value)`、`new Date(updatedAt).toLocaleString()` 改为 `parseApiDateTime()` / `formatApiDateTime()`。对 `datetime-local`、`input type=date` 等 Civil Time 场景保留原逻辑，但添加 `// time-check: local-control-value, reason="..."` 注释。

- [ ] **步骤 5：运行前端测试**
  运行：`cd frontend; npm run test -- src/lib/dateTime.test.ts src/features/token-usage/client/tokenUsage.test.ts src/pages/DashboardPage.test.tsx`
  预期：`PASS`。

- [ ] **步骤 6：Commit**
  `git commit -m "fix(frontend): parse API datetimes through shared helper"`

## Task 7：新增只读数据审计

**文件：**
- 创建：`backend/scripts/audit_time_data.py`
- 创建：`scripts/audit-time-data.ps1`
- 创建：`backend/test/test_audit_time_data.py`

- [ ] **步骤 1：编写审计测试**
  测试 `TimeIssue` 报告渲染，确认 Markdown 包含表名、主键、字段、原始值、问题类型、建议动作；测试 JSON 和 Markdown 文件会写入输出目录。

- [ ] **步骤 2：运行测试确认失败**
  运行：`cd backend; uv run python -m unittest test.test_audit_time_data`

- [ ] **步骤 3：实现审计脚本**
  默认只读，检查 `lease_expires_at < claimed_at`、`finished_at < started_at`、`updated_at < created_at`、`scheduled_at` 多年偏移、`active_seconds` 与开始结束时间差异异常。输出到 `data/logs/time-audit-YYYYMMDD-HHMMSS.json` 和 `.md`。

- [ ] **步骤 4：实现 PowerShell 包装入口**
  `scripts/audit-time-data.ps1` 设置 UTF-8，定位 repo root 和 backend，支持 `-DatabasePath`、`-OutputDirectory` 参数，然后调用 `uv run python scripts/audit_time_data.py`。

- [ ] **步骤 5：运行测试和只读审计**
  运行：`cd backend; uv run python -m unittest test.test_audit_time_data`
  运行：`cd ..; pwsh -NoProfile -ExecutionPolicy Bypass -File scripts/audit-time-data.ps1`
  预期：测试 `OK`，生成报告，不修改数据库。

- [ ] **步骤 6：Commit**
  `git commit -m "feat(scripts): add read-only time data audit"`

## Task 8：新增静态检查和 PR 模板

**文件：**
- 创建：`scripts/check-time-usage.ps1`
- 创建：`scripts/test-check-time-usage.ps1`
- 修改或创建：`.github/pull_request_template.md`
- 修改：`.github/workflows/*.yml`（如果存在）

- [ ] **步骤 1：编写脚本测试**
  用临时目录构造 `DateTime(timezone=True)`、`datetime.now(UTC)`、`.replace(tzinfo=UTC)`、`new Date(apiValue)` 等样例，运行 `scripts/check-time-usage.ps1 -Root <fixture> -FailOnViolation`，预期非 0。

- [ ] **步骤 2：运行测试确认失败**
  运行：`pwsh -NoProfile -ExecutionPolicy Bypass -File scripts/test-check-time-usage.ps1`

- [ ] **步骤 3：实现检查脚本**
  扫描后端、前端和 Alembic：`datetime.now(UTC)`、`datetime.now()`、`.replace(tzinfo=UTC)`、`DateTime(timezone=True)`、可疑 `new Date(value)`、API 字符串直接 `toLocaleString()`、SQLite `localtime` 默认值。支持 report-only、`-FailOnViolation`、`-CoreOnly`。

- [ ] **步骤 4：支持有原因豁免**
  允许 `time-check: ignore(..., reason="...")` 和 `time-check: local-control-value, reason="..."`；没有 reason 的豁免必须视为违规。

- [ ] **步骤 5：更新 PR 模板**
  增加 5 个问题：字段是 Instant 还是 Civil Time、数据库语义、API 是否带时区、前端是否走统一解析、是否有 Asia/Shanghai 回归测试。

- [ ] **步骤 6：CI report-only 接入**
  如果存在 GitHub Actions，加入 `pwsh -NoProfile -ExecutionPolicy Bypass -File scripts/check-time-usage.ps1`，本阶段不加 `-FailOnViolation`。

- [ ] **步骤 7：Commit**
  `git commit -m "chore: add time usage guardrails"`

## Task 9：清理后端剩余高风险时间写法

**文件：**
- 修改：`backend/app/api/*.py`
- 修改：`backend/app/services/*.py`
- 修改：`backend/app/core/startup_logging.py`
- 修改：`backend/app/agents/*.py`（如扫描命中）

- [ ] **步骤 1：扫描违规点**
  运行：`rg -n "datetime\.now\(UTC\)|datetime\.now\(\)|replace\(tzinfo=UTC\)" backend/app`

- [ ] **步骤 2：按语义替换**
  Instant 当前时间改为 `utc_now()`；SQLite naive 兼容改为 `as_utc_aware()`；本地日历当前时间改为 `local_now()`；邮件 Date header 等协议场景保留标准库但必须加有原因豁免。

- [ ] **步骤 3：运行后端全量测试**
  运行：`cd backend; uv run python -m unittest discover test`
  预期：`OK`。

- [ ] **步骤 4：运行核心阻断检查**
  运行：`pwsh -NoProfile -ExecutionPolicy Bypass -File scripts/check-time-usage.ps1 -CoreOnly -FailOnViolation`
  预期：核心路径无违规。

- [ ] **步骤 5：Commit**
  `git commit -m "refactor(backend): route time operations through shared utilities"`

## Task 10：最终验证和门禁升级

**文件：**
- 修改：`scripts/check-time-usage.ps1`
- 修改：`.github/workflows/*.yml`（如果存在）
- 修改：`docs/superpowers/specs/time_handling_spec.md`（仅补充实现落地说明）

- [ ] **步骤 1：运行后端全量测试**
  `cd backend; uv run python -m unittest discover test`

- [ ] **步骤 2：运行前端验证**
  `cd frontend; npm run test; npm run lint; npm run build`

- [ ] **步骤 3：运行时间数据审计**
  `pwsh -NoProfile -ExecutionPolicy Bypass -File scripts/audit-time-data.ps1`
  预期：生成 JSON 和 Markdown 报告，不修改数据库。

- [ ] **步骤 4：运行静态检查**
  `pwsh -NoProfile -ExecutionPolicy Bypass -File scripts/check-time-usage.ps1`
  预期：没有未解释的高风险新增问题。

- [ ] **步骤 5：升级 CI 到核心阻断**
  workflow 中改为 `scripts/check-time-usage.ps1 -CoreOnly -FailOnViolation`。全量阻断留到后续独立 PR，避免历史代码一次性卡死。

- [ ] **步骤 6：人工验收**
  确认：crawler 领取后不再长期「任务已排队」；未过期 naive UTC lease 不被误判；进程重启后任务按租约恢复或重新领取；`active_seconds` 不差 8 小时；本地发送窗口不被 UTC 跨日影响；API 时间带时区；前端历史无时区字符串按 UTC instant 展示；新增高风险写法会被脚本报出。

- [ ] **步骤 7：Commit**
  `git commit -m "chore: enforce core time handling checks"`

## 执行顺序和风险控制

- 先执行 Task 1，再执行 Task 2 和 Task 3，优先解决当前抓取卡住和运行时长风险。
- Task 4 不改 SQLite 表结构，只改 ORM 读写行为；执行前后必须跑模型和迁移测试。
- Task 5 会改变 API 时间字符串格式，Task 6 必须紧随其后，避免前端展示偏移。
- Task 8 先 report-only，Task 10 只开启核心阻断；全量阻断等历史风险清零后再做。
- 每个任务独立 commit。失败时只回退本任务改动，不回退用户已有工作。

## 自检结果

- 规格覆盖度：已覆盖统一时间工具、`UTCDateTime`、服务层比较、API 序列化、前端解析、审计脚本、静态检查、PR/CI 门禁和分阶段验收。
- 占位符扫描：没有使用占位待办、模糊处理语句或重复引用作为实现要求；每个任务都有文件、测试、命令和预期结果。
- 类型一致性：后端统一使用 `utc_now()`、`local_now()`、`as_utc_aware()`、`as_utc_naive()`、`parse_api_datetime()`、`serialize_api_datetime()`、`UTCDateTime()`；前端统一使用 `parseApiDateTime()` 和 `formatApiDateTime()`。
- 风险提醒：Task 4 的模型字段清单必须以实际 `rg "DateTime\(timezone=True\)" backend/app/models` 输出为准，防止执行期间新增模型漏迁。