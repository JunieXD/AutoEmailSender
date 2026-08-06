# 运行时 Worker 设置启动接线设计

## 背景

设置页的“其他设置”已经把后台并发相关字段保存到数据库表 `app_settings`，并通过 `/api/runtime-settings` 读写。前端也把部分字段标记为“重启生效”，表达的语义是：用户保存后，下次后端启动时使用这些设置。

当前实现存在接线不完整的问题：`RuntimeManager.start()` 创建后台 worker 时仍读取 `get_settings()` 中的环境变量或默认值，而不是读取数据库中的运行时设置。因此用户在 UI 中保存以下字段后，即使重启后端，也可能不会影响实际后台 worker 数或轮询间隔：

- `crawler_worker_count`：智能抓取任务并发数。
- `match_analysis_job_worker_count`：批量匹配 Worker 数。
- `match_analysis_job_interval_seconds`：批量匹配轮询间隔。

同一张设置表中的其他字段多数已经在任务执行时读取数据库值，例如批量匹配任务内部并发、批量草稿生成并发、抓取详情页补全并发、草稿 token 上限和草稿改写偏好。

## 已确认的产品决策

- “智能抓取任务并发数”采用重启后生效，不做保存后动态增减 worker。
- 本次修复应覆盖同类“重启生效”字段，而不是只修 `crawler_worker_count`。
- 保存设置后的 UI 语义保持不变：仍显示“重启生效”。
- 单个抓取任务内部的详情页补全并发继续按当前逻辑在补全开始时读取数据库设置，保存后下一轮补全生效。

## 目标与非目标

### 目标

- 让 `RuntimeManager.start()` 在后端启动时使用数据库中的运行时设置创建后台 worker。
- 让以下字段在重启后真实生效：
  - `crawler_worker_count`
  - `match_analysis_job_worker_count`
  - `match_analysis_job_interval_seconds`
- 保留环境变量和默认值作为数据库读取失败或设置行尚未初始化时的回退。
- 用后端单元测试锁定启动接线行为，避免后续回归。

### 非目标

- 不实现保存设置后动态扩缩容后台 worker。
- 不改动前端交互、字段命名、范围校验或设置保存 API。
- 不改变批量草稿生成、任务派发、IMAP 轮询等其他后台循环的启动策略。
- 不调整抓取 Agent 的初始发现阶段并发模型。

## 现状数据流

当前设置保存链路：

1. 前端 `OtherSettingsCard` 读取和提交 `/api/runtime-settings`。
2. 后端 `runtime_settings` API 将字段保存到 `app_settings`。
3. 多数任务执行路径在运行前读取 `get_runtime_settings(session)`。
4. `RuntimeManager.start()` 例外：它直接读取 `get_settings()`，导致“重启生效”字段没有进入启动链路。

当前后台启动链路：

1. FastAPI lifespan 调用 `initialize_runtime(app)`。
2. `initialize_runtime` 在迁移数据库后创建 `RuntimeManager(get_session_factory())`。
3. `RuntimeManager.start()` 调用 `get_settings()`。
4. 依据环境设置创建 `crawler-worker-N` 和 `match-analysis-worker-N`。

断点发生在第 3 步：启动时没有读取 `app_settings`。

## 方案比较

### 方案 A：启动时读取数据库运行时设置

`RuntimeManager.start()` 保留 `get_settings()` 读取环境配置，同时新增一次数据库读取：

- 环境设置继续提供 dispatcher、IMAP、批量草稿 worker 等基础配置。
- 数据库设置提供 `crawler_worker_count`、`match_analysis_job_worker_count`、`match_analysis_job_interval_seconds`。
- 如果数据库读取失败，则记录异常并回退到环境设置，确保后台仍能启动。

优点：

- 与“重启生效”产品语义一致。
- 改动集中在后端启动接线，风险低。
- 不需要复杂的 worker 生命周期管理。
- 可以用现有 `get_runtime_settings(session)` 复用默认设置创建逻辑。

缺点：

- 保存后仍需重启，不能立即改变 worker 数。
- `RuntimeManager.start()` 会多一次数据库访问。

### 方案 B：保存设置后动态调整 worker

运行中的 `RuntimeManager` 监听或接收设置更新，保存后立即创建或取消后台 worker。

优点：

- 用户保存后立即生效，体验更直接。

缺点：

- 需要新增 worker resize 机制。
- 需要处理正在执行中的 worker 取消、循环间隔变更、并发任务状态和错误恢复。
- 测试范围更大，风险明显高于当前需求。

### 方案 C：移除 UI 字段，只保留环境变量

删除或禁用 UI 中这些“重启生效”字段，只允许通过环境变量配置后台 worker。

优点：

- 实现简单，没有数据库启动接线问题。

缺点：

- 与现有设置页能力和用户预期相反。
- 桌面应用场景下环境变量配置不如 UI 友好。
- 已保存字段会变成无效配置，容易造成困惑。

## 推荐设计

采用方案 A。

新增一个小的运行时启动配置解析边界，职责是把环境配置和数据库运行时设置合并成 `RuntimeManager.start()` 所需的启动参数。可以是私有 dataclass 和私有方法，避免把启动细节散落在 `start()` 主流程里。

建议结构：

- `RuntimeWorkerStartupSettings`
  - `crawler_worker_count: int`
  - `match_analysis_job_worker_count: int`
  - `match_analysis_job_interval_seconds: int`
- `RuntimeManager._resolve_worker_startup_settings(settings)`
  - 输入 `get_settings()` 返回的环境设置。
  - 尝试通过 `self._session_factory()` 读取 `get_runtime_settings(session)`。
  - 成功时使用数据库中的三个字段。
  - 失败时记录日志并使用环境设置中的三个字段。
  - 输出经过 `max(1, value)` 防御性归一化后的启动值。

`RuntimeManager.start()` 的职责保持清晰：

1. 读取环境设置。
2. 解析 worker 启动设置。
3. 根据解析结果创建 crawler 和 match-analysis worker。
4. 其他后台循环仍使用环境设置。

这样可以让数据库字段和 UI 语义闭环，同时保持后端启动失败时的韧性。

## 错误处理

- 数据库读取运行时设置失败时，不阻断整个后台启动。
- 失败时记录 `logger.exception(...)`，说明已回退到环境变量和默认值。
- 读取到的数值即使经过 Pydantic 和数据库默认值校验，启动处仍做 `max(1, value)`，防止异常数据导致 0 个 worker。
- 如果数据库读取成功但部分字段缺失，视为异常并回退到环境设置，避免半配置状态。

## 测试设计

后端测试集中在 `backend/test/test_runtime_manager.py`：

- 新增测试：`RuntimeManager.start()` 使用数据库运行时设置创建抓取 worker。
  - 模拟 `get_settings()` 返回环境值 `crawler_worker_count=1`。
  - 模拟 `get_runtime_settings()` 返回 `crawler_worker_count=3`。
  - 断言创建 `crawler-worker-1`、`crawler-worker-2`、`crawler-worker-3`。
- 新增或扩展测试：批量匹配 worker 数和轮询间隔使用数据库值。
  - 模拟 `match_analysis_job_worker_count=2` 和 `match_analysis_job_interval_seconds=5`。
  - 断言创建两个 `match-analysis-worker`，且传入 interval 为 5。
- 保留或改写现有测试：数据库设置读取失败时回退环境设置。
  - 模拟 `get_runtime_settings()` 抛异常。
  - 断言仍按环境配置创建 worker。

无需新增前端测试，因为前端字段、保存 API 和“重启生效”标识不变。

## 迁移与兼容性

- 不需要数据库迁移，相关字段已经存在于 `app_settings`。
- 不改变 API 请求或响应结构。
- 已保存的用户设置将在修复后第一次后端启动时开始生效。
- 环境变量仍可作为首次启动、数据库异常或测试场景的回退来源。

## 风险

- 启动阶段增加一次数据库读取，如果数据库初始化顺序不正确会影响读取。当前 `initialize_runtime()` 已在 `RuntimeManager.start()` 前执行 `ensure_database_schema()`，因此顺序满足要求。
- 如果 `get_runtime_settings()` 在启动阶段创建默认设置行，可能产生一次写入。这与设置 API 当前行为一致，可接受。
- 批量匹配 worker 数和轮询间隔从数据库读取后，环境变量不再是正常情况下的优先来源。该行为符合 UI 设置页的产品语义，但需要在实现中保留回退路径。

## 验收标准

- 用户在“其他设置”保存 `智能抓取任务并发数 = 3` 后，重启后端会创建 3 个抓取 worker。
- 用户在“其他设置”保存 `批量匹配 Worker 数 = 2` 后，重启后端会创建 2 个批量匹配 worker。
- 用户在“其他设置”保存 `批量匹配轮询间隔 = 5` 后，重启后端的批量匹配 worker 使用 5 秒轮询间隔。
- 数据库运行时设置读取失败时，后台 worker 仍可按环境变量或默认值启动。
- 现有任务执行期读取数据库设置的字段行为不变。

## 规格自检

- 占位符检查：无 TODO、待定项或未完成章节。
- 一致性检查：设计坚持“重启后生效”，不包含动态 resize。
- 范围检查：范围限制在 `RuntimeManager` 启动接线和对应后端测试，不涉及前端改造。
- 模糊性检查：明确了数据库设置优先、环境设置回退的优先级。
