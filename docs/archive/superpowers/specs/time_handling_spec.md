# 时间处理治理规格

## 背景

AutoEmailSender 当前运行在 Windows + SQLite + FastAPI + React + Electron 环境中。近期智能抓取任务出现过两类时间相关问题：

- v2 抓取任务已被 worker 领取，但 UI 长时间显示「任务已排队」。
- v2 worker 领取页面任务后没有真正抓取，原因是 SQLite 读出的 `lease_expires_at` 丢失 `tzinfo`，代码将 UTC 时间误当成本地时间比较，导致刚领取的租约被误判为过期。

这些问题说明项目缺少统一的时间模型。虽然模型字段大量使用 `DateTime(timezone=True)`，但 SQLite 不会可靠保留时区信息，读出的 `datetime` 经常是 naive datetime。只要业务代码直接比较、加减或序列化这些值，就可能在非 UTC 时区下出错。

本规格的目标是从根本上治理时间问题，并为以后新增代码建立强约束。

## 目标

- 所有绝对时间点统一按 UTC 存储、计算和传输。
- 所有本地日历时间明确按用户本地时区解释，不与 UTC instant 混用。
- SQLite 读出的 naive datetime 在后端统一恢复为 UTC-aware datetime。
- API 返回时间统一为 ISO 8601 UTC 字符串，前端不再猜测时区。
- 新增代码如果绕过统一时间工具，能够被测试、lint 或代码审查及时发现。

## 非目标

- 不引入多用户时区配置系统。当前产品默认使用本机本地时区处理批量发送日期窗口。
- 不改变用户可见的日期选择语义。用户选择的 `2026-06-01` 仍表示本地日期，而不是 UTC 日期。
- 不迁移到 PostgreSQL。即使未来更换数据库，本规格仍应成立。

## 时间类型模型

系统中的时间必须分为两类，不允许模糊使用。

### 绝对时间点（Instant）

绝对时间点表示全球唯一时刻，必须使用 UTC。

典型字段：

- `created_at`
- `updated_at`
- `deleted_at`
- `archived_at`
- `started_at`
- `active_started_at`
- `paused_at`
- `finished_at`
- `claimed_at`
- `lease_expires_at`
- `scheduled_at`
- `sent_at`
- `received_at`
- `last_attempted_at`
- `cancel_requested_at`
- `approved_at`

规则：

- Python 内部表示必须是 `datetime` 且 `tzinfo=UTC`。
- 数据库存储允许使用 UTC-naive 兼容 SQLite，但读取到应用层后必须恢复为 UTC-aware。
- API 输出必须带时区，推荐 `2026-05-31T06:44:37Z`。
- 前端解析必须按 UTC instant 处理，然后按用户本地时区展示。

### 本地日历时间（Civil Time）

本地日历时间表示用户业务规则，而不是唯一时刻。

典型字段：

- `scheduled_dates`: `YYYY-MM-DD[]`
- `window_start_time`: `HH:mm`
- `window_end_time`: `HH:mm`
- 批量发送的「每天发送数量」窗口

规则：

- 保持字符串或 date/time 类型，不直接存成 UTC instant。
- 只有在生成具体任务 `scheduled_at` 时，才结合本地时区转换成 UTC instant。
- 本地日历逻辑必须显式接收 `local_timezone` 或使用统一的本地时区函数。

## 后端规范

### 统一时间工具

必须新增 `backend/app/core/time.py`，集中提供以下函数：

```python
from __future__ import annotations

from datetime import UTC, datetime, tzinfo


def utc_now() -> datetime:
    return datetime.now(UTC)


def as_utc_aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def as_utc_naive(value: datetime) -> datetime:
    return as_utc_aware(value).replace(tzinfo=None)


def local_now(local_timezone: tzinfo | None = None) -> datetime:
    timezone = local_timezone or datetime.now().astimezone().tzinfo or UTC
    return utc_now().astimezone(timezone)


def parse_api_datetime(value: str) -> datetime:
    normalized = value.strip()
    if len(normalized) == 10 and normalized[4] == "-" and normalized[7] == "-":
        raise ValueError("date-only value is Civil Time, not an Instant")
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"
    parsed = datetime.fromisoformat(normalized)
    return as_utc_aware(parsed)


def serialize_api_datetime(value: datetime) -> str:
    utc_value = as_utc_aware(value).replace(microsecond=0)
    return utc_value.isoformat().replace("+00:00", "Z")
```

所有后端业务代码禁止直接使用以下模式：

- `datetime.now(UTC)`，应改用 `utc_now()`。
- `datetime.now().astimezone()`，应改用 `local_now()`。
- `value.replace(tzinfo=UTC)`，应改用 `as_utc_aware(value)`。
- 手写 `_to_utc_naive()`、`_as_utc_aware()`，应复用 `backend/app/core/time.py`。

例外：

- 测试中构造固定时间可以直接使用 `datetime(..., tzinfo=UTC)`。
- 与第三方协议强相关的格式化逻辑可以使用标准库，但必须在函数边界调用统一工具。
- 本地日历逻辑可以使用 `datetime.combine()` 和 `date.fromisoformat()`，但必须只处理 Civil Time，不能把结果直接当 Instant 入库。

`parse_api_datetime()` 只允许解析 Instant 字符串。`YYYY-MM-DD` 这类 date-only 值必须走 Civil Time 解析函数，不能自动补成 `T00:00:00Z`。这是为了避免用户选择的本地日期在 UTC 转换后发生跨日偏移。

### 数据库时间类型

必须新增 SQLAlchemy 类型 `UTCDateTime`，并逐步替换所有 Instant 字段。

建议位置：`backend/app/models/types.py`。

行为要求：

- 写入数据库前，将 aware datetime 转为 UTC。
- SQLite 后端存储为 UTC-naive datetime，以兼容现有数据。
- 从数据库读取后，统一返回 UTC-aware datetime。
- 如果写入 naive datetime，默认按 UTC 解释，但必须在日志或测试中覆盖该兼容行为。

示例接口：

```python
class UTCDateTime(TypeDecorator[datetime]):
    impl = DateTime
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        if value is None:
            return None
        utc_value = as_utc_aware(value)
        if dialect.name == "sqlite":
            return utc_value.replace(tzinfo=None)
        return utc_value

    def process_result_value(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        if value is None:
            return None
        return as_utc_aware(value)
```

模型和迁移必须遵守以下约束：

- 新增 Instant 字段时，模型必须使用 `UTCDateTime()`，不得继续使用 `DateTime(timezone=True)`。
- `onupdate` 必须使用 `utc_now`，不得直接写 `lambda: datetime.now(UTC)`。
- Alembic 迁移脚本可以继续使用 `server_default=sa.text("CURRENT_TIMESTAMP")` 兼容 SQLite，但对应模型字段必须是 `UTCDateTime()`，确保读取后恢复为 UTC-aware。
- 手写迁移中如果需要写入当前时间，优先由应用层传入 UTC 时间；若必须使用数据库 `CURRENT_TIMESTAMP`，必须在迁移注释中说明它按 UTC-naive 存储。
- 新迁移不得引入本地时间默认值，例如 `datetime('now', 'localtime')`。

替换范围包括但不限于：

- `backend/app/models/crawl_job.py`
- `backend/app/models/crawl_chunk.py`
- `backend/app/models/email_task.py`
- `backend/app/models/match_analysis_job.py`
- `backend/app/models/match_analysis_run.py`
- `backend/app/models/batch_task.py`
- `backend/app/models/operation_log.py`
- `backend/app/models/professor.py`
- `backend/app/models/imap_sync.py`
- `backend/app/models/test_compose_session.py`
- `backend/app/models/thinking_adaptation_cache.py`

### 服务层比较规则

任何 datetime 比较、加减、排序前，必须保证参与运算的值语义一致。

正确示例：

```python
from app.core.time import as_utc_aware, utc_now

expires_at = as_utc_aware(task.lease_expires_at)
if expires_at <= utc_now():
    ...
```

错误示例：

```python
now = datetime.now()
if task.lease_expires_at <= now:
    ...
```

关键风险场景必须重点覆盖：

- worker 租约：`lease_expires_at`。
- 中断恢复：`started_at`、`finished_at`、`active_started_at`。
- 发送调度：`scheduled_at` 与本地窗口。
- 过期判断：`updated_at < cutoff`、`last_send_attempt_at < cutoff`。
- 图表筛选：`start_at`、`end_at`、`created_at`。

### API 规范

所有 API 响应中的 Instant 字段必须输出 ISO 8601 UTC 字符串。

正确：

```json
{
  "created_at": "2026-05-31T06:44:37Z"
}
```

禁止：

```json
{
  "created_at": "2026-05-31 06:44:37"
}
```

Pydantic schema 可以继续使用 `datetime` 字段，但必须保证传入 schema 的值是 UTC-aware。必须选择一种统一落地方式，不能由各接口自由发挥：

- FastAPI 全局 JSON encoder：所有 `datetime` 输出统一调用 `serialize_api_datetime()`。
- 或者 Pydantic v2 的共享 base schema：在统一基类中为 `datetime` 字段配置 field serializer。

无论采用哪一种方式，接口层都不能依赖默认序列化。默认序列化容易把 SQLite naive datetime 输出成 `2026-05-31T06:44:37`，前端会产生歧义。

请求中的 Instant 参数必须接受：

- `2026-05-31T06:44:37Z`
- `2026-05-31T06:44:37+00:00`
- `2026-05-31T14:44:37+08:00`

如果请求参数不带时区，后端必须按 UTC 解释，并在 API 文档中写明兼容行为。新增 API 不应鼓励无时区输入。

## 前端规范

### API 时间解析

前端必须复用 `frontend/src/lib/dateTime.ts` 中的解析函数。该文件需要成为唯一 API 时间入口。

要求：

- API 返回的 Instant 字段只能通过 `parseApiDateTime()` 解析。
- UI 展示使用 `formatApiDateTime()` 或基于 `parseApiDateTime()` 的封装。
- 不允许对 API 字符串直接调用 `new Date(value)`。

允许的 `new Date()` 场景：

- 获取当前时间：`new Date()`。
- 处理 `datetime-local` 控件值。
- 构造本地日历选择器内部日期。
- 使用已经解析过的 `Date` 对象做展示。

现有代码中的已知风险点必须在阶段 4 中逐一确认或整改：

- `frontend/src/components/molecules/OtherSettingsCard.tsx`：`new Date(updatedAt).toLocaleString("zh-CN")`。
- `frontend/src/components/organisms/DiagnosticLogPanel.tsx`：`new Date(event.timestamp)`。
- `frontend/src/pages/DashboardPage.tsx`：`new Date(value)`。
- `frontend/src/features/token-usage/client/tokenUsage.ts`：`new Date(value)` 和 `new Date(bucketStart)`，需要确认是否已经由 `parseApiDateTime()` 覆盖。
- `frontend/src/pages/WorkspacePage.tsx`：继续保留 `parseApiDateTime(currentTask.scheduled_at)` 模式，并审查 `datetime-local` 提交流程。

如果某个 `new Date(value)` 确认不是 API Instant，必须添加局部注释说明来源，例如 `// time-check: local-control-value`。

### 日期与时间控件

`datetime-local` 控件表示用户本地时间。提交给 API 前必须转换为 ISO UTC：

```typescript
const date = new Date(localInputValue);
const payloadValue = date.toISOString();
```

`date` 控件表示本地日历日期，不应该自动转成 UTC instant。批量发送的 `scheduled_dates` 继续使用 `YYYY-MM-DD` 字符串。

### 前端门禁

新增 ESLint 约束或自定义检查脚本：

- 检查 `new Date(` 的调用点。
- 如果参数是变量而不是字面量、`Date.now()` 结果或控件本地值，必须人工确认是否来自 API。
- 检查 `toLocaleString()` 是否直接作用于 API 字符串。

建议新增脚本：`scripts/check-time-usage.ps1`。

## 数据迁移策略

现有 SQLite 数据库中大部分时间值已经按 UTC 语义写入，但缺少时区后缀。因此不需要批量移动时间值，只需要改变读取和序列化规则。

迁移步骤：

1. 引入 `UTCDateTime` 类型。
2. 将 Instant 字段模型类型从 `DateTime(timezone=True)` 替换为 `UTCDateTime()`。
3. 不修改已有列类型，SQLite 表结构无需重建。
4. 增加一次数据审计脚本，检查明显异常值：
   - `lease_expires_at < claimed_at`
   - `finished_at < started_at`
   - `updated_at < created_at`
   - `scheduled_at` 偏离当前合理范围超过多年
   - `active_seconds` 与 `started_at` / `finished_at` 推导值差异异常
5. 审计脚本默认只读，输出 JSON 和 Markdown 报告，不自动修复，避免误改用户数据。
6. 只有显式传入修复参数时，才允许执行数据修复；修复前必须生成数据库备份。

建议脚本：

- `backend/scripts/audit_time_data.py`：读取数据库并生成结构化报告。
- `scripts/audit-time-data.ps1`：PowerShell 包装入口，负责设置 UTF-8 和定位桌面端数据目录。

报告路径：

- JSON：`data/logs/time-audit-YYYYMMDD-HHMMSS.json`
- Markdown：`data/logs/time-audit-YYYYMMDD-HHMMSS.md`

每条报告至少包含：表名、主键、字段名、原始值、问题类型、建议动作。

## 测试规范

必须新增时间治理测试套件。

### 后端单元测试

新增 `backend/test/test_time_utils.py`：

- `as_utc_aware()` 将 naive datetime 当 UTC。
- `as_utc_aware()` 将 `+08:00` datetime 转为 UTC。
- `serialize_api_datetime()` 输出 `Z` 后缀。
- `UTCDateTime` 从 SQLite 读出 UTC-aware datetime。

### 运行时长回归测试

新增或补充：

- `CrawlJobRun.active_started_at` 从 SQLite 读出为 naive UTC 时，`mark_crawl_job_run_finished()` 能正确结算 `active_seconds`。
- Asia/Shanghai 环境下，`active_seconds` 不会多算或少算 8 小时。
- `started_at`、`finished_at`、`paused_at` 的组合不会触发 aware 与 naive datetime 相减异常。
- match analysis 的 `started_at` / `finished_at` 持续时间统计使用同一套 UTC 工具。

### worker 回归测试

保留并扩展现有测试：

- page worker：naive UTC `lease_expires_at` 未过期时必须继续处理。
- chunk worker：naive UTC `lease_expires_at` 未过期时允许提交。
- enrichment worker：naive UTC `lease_expires_at` 未过期时允许写回。
- scheduler：过期 processing 任务可被重新领取。

### 发送调度测试

新增或补充：

- Asia/Shanghai 下，用户选择今天 09:00-18:00，生成的 `scheduled_at` 是对应 UTC instant。
- `scheduled_at` 从 SQLite 读出为 naive UTC 时，调度器仍能按 UTC 到期发送。
- 批量任务过期判断按本地日期窗口，而不是 UTC 日期窗口。

### API 与前端测试

- API 返回时间字段必须带 `Z` 或显式 offset。
- 前端 `parseApiDateTime("2026-05-31 06:44:37")` 必须按 UTC 解析。
- 前端 `datetime-local` 输入 `2026-05-31T14:44` 在 Asia/Shanghai 下提交为 `2026-05-31T06:44:00.000Z`。

## 静态检查与代码审查门禁

### 后端检查

新增脚本 `scripts/check-time-usage.ps1`，至少检查以下模式：

- `datetime.now(UTC)` 出现在 `backend/app` 中，提示改用 `utc_now()`。
- `datetime.now()` 出现在 `backend/app` 中，提示确认是否本地日历时间。
- `.replace(tzinfo=UTC)` 出现在 `backend/app` 中，提示改用 `as_utc_aware()`。
- `DateTime(timezone=True)` 出现在 `backend/app/models` 中，提示改用 `UTCDateTime()` 或说明是非 Instant 字段。

迁移期采用分级门禁：

1. 报告期：脚本只输出问题清单，不阻断 CI。
2. 核心阻断期：先阻断 `backend/app/services/crawler_*`、`backend/app/services/task_runtime.py`、`backend/app/services/token_usage_records.py` 的新增违规。
3. 全量阻断期：模型字段完成迁移后，阻断整个 `backend/app` 的新增违规。

允许通过注释显式豁免，但必须写明原因：

```python
# time-check: ignore(local-civil-time, reason="batch window uses local date")
```

禁止无原因豁免，例如 `# time-check: ignore`。

### 前端检查

同一脚本检查：

- `new Date(value)` 形式的可疑 API 时间解析。
- 对 API 字段直接调用 `toLocaleString()`。
- 新增时间格式化函数但未复用 `frontend/src/lib/dateTime.ts`。

### Alembic 检查

检查脚本还必须扫描 `backend/alembic/versions`：

- 新迁移中出现 `sa.DateTime(timezone=True)` 时，必须确认模型侧使用 `UTCDateTime()`。
- 新迁移中出现 `CURRENT_TIMESTAMP` 时，必须确认它只用于 UTC-naive 存储，并由模型类型恢复时区。
- 禁止出现 SQLite 本地时间默认值，例如 `datetime(''now'', ''localtime'')`。

### PR 模板要求

涉及时间字段、调度、worker、筛选、图表的 PR 必须回答：

- 这个字段是 Instant 还是 Civil Time？
- 数据库存储语义是什么？
- API 输出是否带时区？
- 前端是否通过统一工具解析？
- 是否有 Asia/Shanghai 回归测试？

## 分阶段实施计划

### 阶段 1：建立基础设施

- 创建 `backend/app/core/time.py`。
- 创建 `backend/app/models/types.py`，实现 `UTCDateTime`。
- 增加 `backend/test/test_time_utils.py`。
- 增加 `scripts/check-time-usage.ps1` 的第一版，先以 report-only 模式运行。
- 增加 `backend/scripts/audit_time_data.py` 和 `scripts/audit-time-data.ps1`，默认只读生成报告。

验收标准：

- 时间工具测试通过。
- `UTCDateTime` 在 SQLite 下读写保持 UTC-aware。
- 检查脚本能识别当前项目中的可疑用法。

### 阶段 2：治理高风险运行时

优先改造：

- v2 crawler worker 与 scheduler。
- 任务派发与发送恢复逻辑。
- crawl job run 的 `active_seconds` 结算。
- match analysis 运行状态统计。
- token usage 时间筛选与图表。

验收标准：

- worker 租约相关测试通过。
- `active_seconds` 跨时区结算测试通过。
- 发送调度跨时区测试通过。
- token usage 自定义时间范围测试通过。

### 阶段 3：模型字段替换

将所有 Instant 字段迁移到 `UTCDateTime()`。

验收标准：

- `rg "DateTime\(timezone=True\)" backend/app/models` 不再出现未豁免结果。
- 现有数据库无需数据迁移即可读取。
- 后端测试全量通过。

### 阶段 4：API 与前端统一

- API 输出统一带 `Z`。
- 前端所有 API 时间解析走 `frontend/src/lib/dateTime.ts`。
- 整改或显式豁免现有前端风险点清单。
- 增加前端静态检查。

验收标准：

- API 时间字段快照测试通过。
- 前端时间解析测试通过。
- `new Date(apiValue)` 类风险点清零或显式豁免。

### 阶段 5：门禁常态化

- 将 `scripts/check-time-usage.ps1` 加入本地验证文档。
- 在 CI 中运行时间检查脚本。
- 更新 PR 模板和开发规范。

验收标准：

- 新增不规范时间代码时，CI 能失败。
- 开发文档明确 Instant 与 Civil Time 的区别。

## 验收清单

- [ ] 所有 Instant 字段读取到 Python 后都是 UTC-aware datetime。
- [ ] 所有 API Instant 输出都带 `Z` 或 offset。
- [ ] FastAPI / Pydantic datetime 序列化有统一实现，不依赖默认序列化。
- [ ] 所有本地日历字段保持 `YYYY-MM-DD` / `HH:mm` 语义，不被误转 UTC 日期。
- [ ] worker 租约不会因本地时区误判。
- [ ] `active_seconds` 和其他运行时长统计不会因 naive / aware 混用出错。
- [ ] 定时发送不会因 UTC 日期和本地日期跨日而提前或延后。
- [ ] 前端不会把无时区 API 字符串当本地时间解析。
- [ ] 静态检查能阻止新增高风险写法。
- [ ] 文档、测试、PR 模板共同约束新增代码。

## 代码审查速查表

看到时间代码时，先问 5 个问题：

1. 这是 Instant 还是 Civil Time？
2. 如果从 SQLite 读出来没有 `tzinfo`，代码会怎么解释？
3. 这段代码在 Asia/Shanghai 和 UTC 下行为是否一致？
4. API 是否带时区传输？
5. 前端是否通过统一时间工具解析？

如果任何一个问题答不上来，这段代码不应合并。