# 桌面端启动就绪状态体验设计

## 背景

桌面端用户在保存身份时遇到过以下报错：

```text
身份保存失败
Backend readiness check timed out
```

错误日志中同时出现：

```text
INFO: Application startup complete.
INFO: Uvicorn running on http://127.0.0.1:48120
INFO [alembic.runtime.migration] Context impl SQLiteImpl.
INFO [alembic.runtime.migration] Will assume non-transactional DDL.
```

这类日志容易让人误以为后端已经完全启动，但实际情况是：Uvicorn 进程和 HTTP 端口已经启动，业务运行时仍在执行数据库迁移、日志清理或后台 worker 启动。当前 `/ready` 必须等业务运行时完成后才返回成功。

现有桌面端把 `/ready` 超时直接包装成启动失败，前端又把启动失败透传到保存身份流程，导致用户看到“身份保存失败”。这会造成两个误解：

- 用户以为身份表单、邮箱配置或保存接口有问题。
- 用户不知道新版首次启动可能需要自动升级本地数据库，也不知道应该等待还是重启。

因此，这个问题的根因不是身份保存逻辑错误，而是启动状态表达不准确、等待策略过短、用户提示不清楚。

## 目标

1. 明确区分“后端进程已启动”和“业务运行时已就绪”。
2. 让桌面端在后端进程可用后尽早打开窗口，而不是白屏或直接报错。
3. 在业务运行时未就绪期间，前端显示清晰、非技术化的系统准备提示。
4. 在业务运行时未就绪期间，禁用会写入数据或触发后台任务的操作。
5. 避免把正常的首次数据库升级显示成“身份保存失败”或“后端启动失败”。
6. 在启动时间较长时持续轮询并更新提示，只有明确异常时才进入错误状态。
7. 提供足够的诊断信息，方便判断是正常慢启动、数据库迁移卡住、文件锁、权限问题还是后端进程退出。

## 非目标

1. 不改变数据库迁移本身的语义。
2. 不跳过 Alembic migration，也不允许前端在 schema 未完成时写入业务数据。
3. 不把所有接口改成自行等待初始化完成；业务写接口仍应依赖全局 ready 状态。
4. 不在本次设计中实现完整日志导出系统，只为后续诊断入口预留状态和文案。
5. 不处理远程服务、邮箱服务或 LLM 服务的连接检查。

## 核心概念

### 进程健康状态

进程健康状态表示后端子进程已经启动，HTTP 服务可以响应最基础的请求。

对应接口：

```text
GET /health
```

成功响应：

```json
{
  "status": "ok"
}
```

该接口只回答“后端进程是否活着”，不代表业务数据库、后台 worker 或业务 API 已可用。

### 业务就绪状态

业务就绪状态表示本地数据库 schema、运行时清理和后台 worker 已完成初始化，前端可以安全调用业务 API。

对应接口：

```text
GET /startup-status
```

该接口替代桌面端对 `/ready` 的黑盒等待。`/ready` 可以继续保留给内部或兼容用途，但桌面端和前端应使用结构化状态展示启动过程。

## 启动状态模型

后端维护启动状态对象：

```json
{
  "state": "starting",
  "phase": "migrating_database",
  "message": "正在升级本地数据库",
  "started_at": "2026-05-08T10:00:00Z",
  "updated_at": "2026-05-08T10:00:12Z",
  "elapsed_seconds": 12,
  "error": null
}
```

字段说明：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `state` | string | 总体状态：`starting`、`ready`、`error` |
| `phase` | string | 当前阶段：见下方阶段定义 |
| `message` | string | 给用户展示的中文提示 |
| `started_at` | string | 后端运行时初始化开始时间，ISO 8601 格式 |
| `updated_at` | string | 当前状态最后更新时间，ISO 8601 格式 |
| `elapsed_seconds` | number | 从初始化开始到当前的秒数 |
| `error` | string 或 null | 异常摘要；仅 `state = error` 时有值 |

### 阶段定义

| phase | state | 用户文案 | 说明 |
| --- | --- | --- | --- |
| `starting` | `starting` | 正在启动系统服务 | 后端运行时任务刚创建，还未进入具体阶段 |
| `migrating_database` | `starting` | 正在检查和升级本地数据 | 正在执行 Alembic migration |
| `cleaning_logs` | `starting` | 正在整理本地运行日志 | 正在清理过期 operation logs |
| `starting_workers` | `starting` | 正在启动后台任务 | 正在启动发送、抓取、匹配等后台 worker |
| `ready` | `ready` | 系统已准备就绪 | 业务 API 可以正常使用 |
| `error` | `error` | 系统准备失败 | 初始化阶段出现异常 |

阶段文案必须面向普通用户，不出现 Alembic、Uvicorn、SQLiteImpl、readiness、worker 等内部术语。

## 后端设计

### 状态存储

在 FastAPI `app.state` 中维护启动状态，例如：

```text
app.state.startup_status
```

`initialize_runtime()` 按阶段更新状态：

1. 设置 `phase = migrating_database`。
2. 调用 `ensure_database_schema()`。
3. 设置 `phase = cleaning_logs`。
4. 调用 `cleanup_old_operation_logs()`。
5. 设置 `phase = starting_workers`。
6. 根据配置启动 `RuntimeManager`。
7. 设置 `state = ready`、`phase = ready`。

异常处理：

- 捕获非取消异常后，设置 `state = error`、`phase = error`、`error = str(exc)`。
- 继续使用现有日志记录完整异常堆栈。
- `/startup-status` 返回结构化错误，`/ready` 继续返回 HTTP 500。

### `/startup-status`

新增接口：

```text
GET /startup-status
```

启动中响应：

```json
{
  "state": "starting",
  "phase": "migrating_database",
  "message": "正在检查和升级本地数据",
  "started_at": "2026-05-08T10:00:00Z",
  "updated_at": "2026-05-08T10:00:12Z",
  "elapsed_seconds": 12,
  "error": null
}
```

就绪响应：

```json
{
  "state": "ready",
  "phase": "ready",
  "message": "系统已准备就绪",
  "started_at": "2026-05-08T10:00:00Z",
  "updated_at": "2026-05-08T10:00:30Z",
  "elapsed_seconds": 30,
  "error": null
}
```

错误响应：

```json
{
  "state": "error",
  "phase": "error",
  "message": "系统准备失败",
  "started_at": "2026-05-08T10:00:00Z",
  "updated_at": "2026-05-08T10:00:45Z",
  "elapsed_seconds": 45,
  "error": "database is locked"
}
```

该接口在后端进程启动后应始终可访问。即使业务运行时失败，也不要让接口本身返回 500；错误通过 JSON 结构表达。

### `/ready` 兼容

保留现有 `/ready`：

- `state = ready` 时返回 HTTP 200。
- `state = starting` 时返回 HTTP 503。
- `state = error` 时返回 HTTP 500。

`/ready` 用于简单探活和兼容已有测试，不再作为桌面端展示启动过程的主接口。

## 桌面端设计

### 启动判定

Electron 主进程启动后端子进程后，先等待 `/health`：

- `/health` 返回 200：认为后端进程已启动，可以创建窗口。
- 后端进程退出：进入真正启动失败。
- `/health` 长时间不可达：进入真正启动失败。

窗口创建后，主进程继续轮询 `/startup-status`，并通过 IPC 向渲染进程发布结构化状态。

### IPC 状态

扩展 `BackendStatus`：

```typescript
type BackendStatus =
  | { state: "starting"; phase: StartupPhase; message: string; elapsedSeconds: number }
  | { state: "ready"; baseUrl: string; phase: "ready"; message: string; elapsedSeconds: number }
  | { state: "error"; message: string; phase: "error"; elapsedSeconds: number };
```

`phase` 类型与后端阶段保持一致。

### 轮询策略

建议策略：

| 时间 | 行为 |
| --- | --- |
| 0-30 秒 | 正常展示当前阶段 |
| 30 秒后 | 增加提示“首次启动或版本升级可能需要几分钟” |
| 2 分钟后 | 增加提示“请保持应用打开，完成后会自动继续” |
| 5 分钟后 | 增加提示“如果长时间停留，可重启应用或导出诊断日志” |
| 10 分钟后 | 进入错误状态，提示用户重启并反馈日志 |

重要规则：

- 30 秒不是失败阈值，只是长启动提示阈值。
- 只要后端进程仍在、`/startup-status` 仍可访问并返回 `starting`，就继续轮询。
- `state = ready` 后立即发布 `ready` 状态。
- `state = error`、后端进程退出或达到硬上限时，才发布 `error` 状态。

### 错误信息

桌面端不应把内部错误原样展示为主文案。技术细节可以放入“详情”或诊断日志。

主文案：

```text
系统准备失败
应用启动时未能完成本地数据检查。请重启应用后再试；如果问题仍然存在，请导出诊断日志反馈。
```

详情中可以包含：

```text
Backend readiness check timed out
database is locked
```

## 前端设计

### 全局系统准备提示

在桌面环境中，前端监听 `backend:status`。

当 `state = starting` 时，展示全局提示：

```text
正在准备本地数据
新版首次启动可能需要检查或升级本地数据库，通常需要 1-3 分钟。请保持应用打开，完成后会自动继续。
```

提示位置建议：

- 顶部全局横幅，适合短时间启动。
- 若业务页面需要写操作，页面内容可以继续展示，但写操作区域禁用。
- 如果没有可用的业务数据或初始化时间超过 30 秒，可使用居中状态面板强化提示。

### 阶段文案

前端根据 `phase` 展示当前阶段：

| phase | 展示文案 |
| --- | --- |
| `starting` | 正在启动系统服务 |
| `migrating_database` | 正在检查和升级本地数据 |
| `cleaning_logs` | 正在整理本地运行日志 |
| `starting_workers` | 正在启动后台任务 |
| `ready` | 系统已准备就绪 |
| `error` | 系统准备失败 |

超过 30 秒后补充：

```text
首次启动或版本升级时可能会稍慢，这不是配置错误。
```

超过 2 分钟后补充：

```text
请保持应用打开，完成后会自动恢复。
```

超过 5 分钟后补充：

```text
如果长时间停留在此状态，可以重启应用；若仍无法恢复，请导出诊断日志反馈。
```

### 写操作禁用

当 `state !== ready` 时，前端应禁用所有会写入数据或触发后台任务的操作，包括但不限于：

- 保存身份。
- 保存模型。
- 上传或删除材料。
- 创建、暂停、恢复、停止批量任务。
- 导入导师。
- 启动抓取任务。
- 生成草稿、保存草稿、发送邮件。
- 修改运行时设置。

禁用按钮文案可以使用：

```text
系统准备中
```

按钮旁或表单底部提示：

```text
本地数据准备完成后即可继续操作，已填写内容不会丢失。
```

### 保存身份场景

如果用户在系统未 ready 时尝试保存身份，不应弹出“身份保存失败”。应展示：

```text
系统正在准备本地数据
这不是身份配置错误。请等待系统准备完成后再保存，已填写内容不会丢失。
```

如果请求已经发出并因后端未 ready 而失败，前端应根据错误类型转译为上述提示，而不是透传 `Backend readiness check timed out`。

### 自动恢复

当状态从 `starting` 变为 `ready`：

- 全局提示自动消失。
- 被禁用的按钮恢复可用。
- 选择上下文和基础数据重新刷新一次。
- 不自动提交用户尚未确认的表单，避免重复写入；用户手动点击保存即可。

自动重试保存可以作为后续增强，不纳入第一版实现。

## 用户文案

### 常规启动中

```text
正在准备本地数据
应用正在检查本地数据库和后台任务，完成后会自动继续。
```

### 数据库升级中

```text
正在检查和升级本地数据
新版首次启动可能需要更新本地数据库，通常需要 1-3 分钟。请保持应用打开，完成后会自动继续。
```

### 长时间启动

```text
仍在准备本地数据
首次启动或版本升级时可能会稍慢，这不是配置错误。请保持应用打开，完成后会自动恢复。
```

### 超长时间启动

```text
本地数据准备时间较长
如果已经等待超过 5 分钟，可以重启应用；若重启后仍无法恢复，请导出诊断日志反馈。
```

### 启动失败

```text
系统准备失败
应用启动时未能完成本地数据检查。请重启应用后再试；如果问题仍然存在，请导出诊断日志反馈。
```

## 错误分类

### 正常慢启动

特征：

- 后端进程未退出。
- `/health` 返回 200。
- `/startup-status` 返回 `state = starting`。
- `phase` 仍在变化，或 elapsed seconds 持续增加。

处理：

- 持续展示准备中状态。
- 禁用写操作。
- 不弹错误。

### 初始化失败

特征：

- `/startup-status` 返回 `state = error`。
- 或 `/ready` 返回 500。

处理：

- 展示“系统准备失败”。
- 提供重启建议。
- 记录详细错误到诊断日志。

### 后端进程退出

特征：

- 子进程 exit。
- `/health` 不可访问。

处理：

- 桌面端进入 error。
- 保留现有自动重启策略，但重启失败后展示用户文案。

### 硬超时

特征：

- 后端进程仍在。
- `/startup-status` 长时间保持 `starting`。
- 超过硬上限，例如 10 分钟。

处理：

- 展示“系统准备失败”或“本地数据准备时间过长”。
- 提示重启和导出诊断日志。
- 日志中记录最后一次 `phase`、elapsed seconds 和后端 stderr 尾部。

## 数据与兼容性

该方案不修改业务数据库 schema。

后端新增 `/startup-status` 不影响现有 API。

`/ready` 保留现有 HTTP 语义，避免破坏已有测试和其他调用方。

桌面端 IPC 类型需要兼容旧字段：

- 旧前端只认 `state = ready` 和 `baseUrl` 时仍能工作。
- 新前端可以读取 `phase`、`message` 和 `elapsedSeconds` 展示更细状态。

## 诊断要求

后端运行时初始化阶段应记录以下日志：

- 进入每个 phase 的时间。
- 每个 phase 完成耗时。
- 初始化失败时的异常堆栈。

桌面端诊断应记录：

- 后端进程 PID。
- 后端 base URL。
- `/health` 首次成功时间。
- `/startup-status` 状态变化。
- 进入长启动、超长启动和硬超时的时间点。
- 最后一次后端 stderr 摘要。

前端诊断应记录：

- 收到的 backend status。
- 用户在非 ready 状态下尝试触发写操作的事件名。
- 状态恢复 ready 后的刷新结果。

## 测试计划

### 后端测试

1. `/health` 在运行时初始化未完成时返回 200。
2. `/startup-status` 在初始化开始后返回 `state = starting`。
3. `ensure_database_schema()` 执行期间，`phase = migrating_database`。
4. `cleanup_old_operation_logs()` 执行期间，`phase = cleaning_logs`。
5. `RuntimeManager.start()` 执行期间，`phase = starting_workers`。
6. 初始化完成后，`/startup-status` 返回 `state = ready`、`phase = ready`。
7. 初始化异常后，`/startup-status` 返回 `state = error`，且接口本身返回 HTTP 200。
8. 初始化异常后，`/ready` 返回 HTTP 500。
9. 初始化未完成时，`/ready` 返回 HTTP 503。

### 桌面端测试

1. `/health` 成功后可以创建窗口，不必等待 `/ready`。
2. `/startup-status` 返回 `starting` 时，主进程持续发布 starting IPC 状态。
3. 30 秒后不会 reject ready Promise，而是发布长启动状态。
4. `/startup-status` 变为 `ready` 后，主进程发布 ready 状态和 base URL。
5. `/startup-status` 返回 `error` 后，主进程发布 error 状态。
6. 后端子进程退出后，主进程进入 error 或现有重启流程。
7. 达到硬超时后，主进程发布用户可理解的 error 文案。

### 前端测试

1. `state = starting` 时展示全局“正在准备本地数据”提示。
2. `phase = migrating_database` 时展示“正在检查和升级本地数据”。
3. 超过 30 秒后展示“首次启动或版本升级时可能会稍慢”。
4. `state !== ready` 时，保存身份按钮禁用并显示“系统准备中”。
5. 非 ready 状态下不会弹“身份保存失败”。
6. `state = ready` 后，全局提示消失，保存身份按钮恢复可用。
7. `state = error` 时展示“系统准备失败”和重启建议。
8. 用户已填写的身份表单内容在 starting -> ready 期间不丢失。

## 验收标准

1. 用户首次打开新版桌面端时，即使数据库迁移超过 30 秒，也不会看到 `Backend readiness check timed out`。
2. Uvicorn 端口启动但业务未 ready 时，界面明确展示“正在准备本地数据”。
3. 数据库迁移期间，保存身份按钮不可点击，用户不会误以为身份配置保存失败。
4. 业务运行时 ready 后，前端自动恢复可操作状态。
5. 业务运行时初始化失败时，用户看到中文可理解错误，而不是 Alembic、Uvicorn 或 readiness 技术日志。
6. 桌面端持续轮询启动状态，30 秒只触发长启动提示，不触发失败。
7. 后端 `/startup-status` 能准确报告当前阶段和错误摘要。
8. `/health`、`/ready` 和 `/startup-status` 的职责清晰，测试覆盖其差异。
9. 日志能区分正常慢启动、初始化失败、后端进程退出和硬超时。
10. 后端、桌面端和前端相关测试通过。

## 后续增强

1. 增加“导出诊断日志”按钮，直接收集桌面端、前端和后端启动日志。
2. 在启动状态面板中展示简短进度时间线。
3. 对数据库迁移耗时做埋点统计，帮助后续优化慢迁移。
4. 在自动更新完成后的首次启动中，额外提示“正在完成版本升级”。
5. 为常见错误提供更具体的恢复建议，例如数据库被占用、数据目录不可写、安全软件拦截。
