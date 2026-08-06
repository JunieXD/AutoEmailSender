# 桌面端后端启动速度优化设计

## 背景

v2.2.4 桌面端收到用户反馈：应用启动时提示「系统准备失败，应用启动时未能完成本地数据检查」。用户导出的诊断日志显示，桌面端在等待后端 `/health` 时超时：

```text
Backend health check timed out:
```

这类失败有几个共同特征：

- 后端诊断接口不可用，导出的 `backend` 日志为空。
- 用户数据目录下没有 `logs/startup.log`，说明后端尚未进入可记录启动初始化异常的阶段。
- 用户退出应用后再次启动，通常约 10 秒即可成功。
- 问题更像「后端 HTTP 服务未及时监听」，而不是「数据库检查已经失败」。

当前桌面端在 30 秒内等不到 `/health` 就判定启动失败。由于后端在 `/health` 可响应前会导入完整应用、所有路由和大量业务依赖，冷启动时可能被文件系统缓存、杀软扫描、动态库加载、Python 模块导入等因素放大，导致个别机器第一次启动超过 30 秒。

## 已观察到的证据

在开发机上做了初步耗时测量，结果如下：

- 源码环境 `import main` 约 4.0 秒。
- 单独导入 `app.api.__init__` 约 3.8 秒。
- 导入任意一个 `app.api.*` 路由时，会先执行 `app.api.__init__`，从而联动导入所有路由。
- `app.services.crawl_job_runtime` 导入约 3.6 秒。
- `app.services.llm_runtime`、`app.services.mail_runtime`、`app.services.materials`、`app.services.task_runtime` 单独导入约 1.6–1.8 秒。
- 第三方库中，`langchain_openai`、`markitdown`、`openai`、`pypdf`、`playwright`、`patchright` 等属于启动路径上的较重依赖。

这些数据说明，启动前半段的主要成本来自 Python 应用导入，而不是 SQLite 迁移或运行时恢复。

## 目标与非目标

### 目标

- 缩短后端从进程启动到 `/health` 可响应的时间。
- 避免导入一个轻量模块时触发所有 API 路由和业务服务的全量导入。
- 将爬虫、LLM、材料解析、邮件测试等重依赖从启动路径迁移到实际使用路径。
- 让桌面端启动失败诊断能区分「后端进程未监听」「业务初始化失败」「数据库锁」等不同阶段。
- 在慢机器上减少 30 秒健康检查超时的概率。

### 非目标

- 不在本次优化中重写后端框架或拆分独立服务进程。
- 不改变现有 API 路径、请求参数和响应结构。
- 不移除现有爬虫、材料解析、LLM、邮件测试等功能。
- 不以跳过数据库迁移或运行时恢复作为性能优化手段。

## 核心问题

### API 包存在全量导入副作用

`backend/main.py` 从 `app.api` 导入所有路由。`backend/app/api/__init__.py` 顶层又导入了每一个路由模块。这样会导致两个问题：

- 后端启动必须先导入所有路由及其依赖，`/health` 无法提前响应。
- 业务服务如果只想复用 `app.api.identity_serializers` 这类轻量工具，也会先执行 `app.api.__init__`，间接触发所有路由导入。

### 路由模块顶层导入重业务服务

多个路由模块在顶层导入运行时服务，例如 LLM、爬虫、材料解析、邮件运行时、任务调度等。这些服务又会继续导入第三方 SDK 或解析库。即使用户只是打开应用首页，这些依赖也会在后端启动阶段被加载。

### 启动阶段缺少可定位耗时的日志

目前 `startup.log` 只覆盖数据库锁重试和异步运行时初始化异常。若后端卡在 Python 导入、uvicorn 启动、端口监听之前，诊断包无法提供阶段性证据。

## 方案比较

### 方案 A：优化桌面端启动等待边界

特点：保持 `/health` 30 秒等待上限，将 `/startup-status` 等待控制在 60 秒内，并优化启动文案。

优点：

- 改动小，能快速降低用户遇到误判失败的概率。
- 对业务代码无侵入。

缺点：

- 没有减少真实启动耗时。
- 用户仍会感受到首次启动很慢。
- 如果后端真的卡死，只是更晚报错。

### 方案 B：启动路径懒加载重依赖

特点：保留单进程架构，但调整导入边界，避免启动时加载暂时用不到的业务依赖。

优点：

- 能直接缩短 `/health` 可响应时间。
- 改动范围可控，不影响 API 契约。
- 有利于减少杀软和文件系统冷加载压力。

缺点：

- 需要逐个梳理模块依赖，避免循环导入。
- 某些接口的首次请求会承担懒加载成本。

### 方案 C：拆分最小启动应用与完整业务应用

特点：先启动一个只提供 `/health` 和 `/startup-status` 的轻量 FastAPI 应用，再挂载或延迟注册完整业务路由。

优点：

- `/health` 可以非常早响应。
- 阶段边界清晰。

缺点：

- 改造复杂度高。
- FastAPI 路由延迟注册和前端请求时序需要额外设计。
- 容易引入「服务已健康但业务路由尚未可用」的兼容问题。

## 推荐方案

推荐采用「方案 B + 方案 A 的保底策略」。

第一阶段先做低风险结构优化：拆除 API 包全量导入副作用、懒加载重第三方依赖，并增加启动耗时埋点。这样可以用数据确认真正收益。与此同时，保持桌面端 `/health` 30 秒上限，并将 `/startup-status` 轮询控制在 60 秒内，避免真实故障被长时间等待掩盖。

不建议第一阶段直接采用方案 C。当前问题尚可通过导入边界治理缓解，先拆最小应用会放大改造范围。

## 详细设计

### 1. 拆除 `app.api.__init__` 的全量导入副作用

调整目标：导入 `app.api.identity_serializers` 或其他 `app.api.*` 子模块时，不应触发 `app.api.__init__` 导入所有路由。

预期做法：

- 将路由聚合逻辑从 `app.api.__init__` 移到独立模块，例如 `app.api.routers`。
- `backend/main.py` 改为从 `app.api.routers` 获取路由列表或命名路由。
- `app.api.__init__` 保持空实现或只保留轻量元数据，避免顶层导入副作用。

验收标准：

- `python -c "import app.api.identity_serializers"` 不再导入所有路由。
- `python -c "import app.api"` 耗时应接近空包导入，不再接近 `import main`。

### 2. 懒加载重第三方依赖

调整目标：爬虫、LLM、材料解析、邮件测试等功能只在实际调用时导入重依赖。

候选模块：

- `app.services.crawl_job_runtime`
- `app.services.crawler_tools`
- `app.services.llm_runtime`
- `app.services.materials`
- `app.services.mail_runtime`
- `app.services.task_runtime`
- `app.services.test_compose_runtime`
- `app.services.match_analysis_job_runtime`

预期做法：

- 将 `langchain_openai`、`openai`、`markitdown`、`playwright`、`patchright`、PDF/Office 解析库等移动到函数内部或局部工厂函数中。
- 对频繁使用的懒加载依赖使用小型缓存函数，避免每次请求重复解析导入路径。
- 避免在模块顶层实例化需要读取环境、网络、浏览器路径或证书状态的对象。

验收标准：

- `import main` 耗时明显下降。
- `/health` 可响应时间下降。
- 相关功能首次调用仍能正常工作，并且错误信息保持可读。

### 3. 启动阶段耗时埋点

调整目标：下次用户再遇到启动失败时，可以判断卡在哪个阶段。

需要记录的阶段：

- `desktop_entry.py` 进程入口开始。
- `uvicorn.run(...)` 调用前。
- `main` 模块导入开始与结束。
- FastAPI 应用创建开始与结束。
- 路由注册开始与结束。
- `/health` 首次可响应后，异步运行时初始化的各阶段耗时。

预期做法：

- 在后端入口增加轻量启动日志函数，尽量不要依赖完整应用配置。
- 日志写入 `AUTO_EMAIL_SENDER_DATA_DIR/logs/startup.log`。
- 如果环境变量缺失，则退化写入临时目录或 stderr。

验收标准：

- 后端卡在导入阶段时，也能留下至少一条启动日志。
- 诊断导出可以包含 `startup.log`。

### 4. 桌面端健康检查保底策略

调整目标：避免慢机器在后端仍在启动时被提前判定失败。

预期做法：

- 将 `/health` 硬超时保持为 30 秒。
- 将 `/startup-status` 硬超时设置为 60 秒。
- 在任意等待阶段，只要后端子进程退出就立即失败。
- 接近 30 秒时，状态仍显示启动中，但文案改为「首次启动可能较慢，正在继续等待本地服务」。
- 超时时将后端进程 PID、是否仍存活、端口、stderr 尾部、安装路径和数据目录写入诊断事件。

验收标准：

- `/health` 超过 30 秒仍不可用时显示启动失败。
- `/startup-status` 超过 60 秒仍未 ready 时显示启动失败。
- 任意阶段后端进程退出时立即失败，并带出 stderr 尾部。

## 风险与应对

### 循环导入风险

拆除 `app.api.__init__` 副作用后，可能暴露现有服务与 API 模块之间的循环依赖。

应对方式：

- 将序列化工具移动到独立轻量模块，例如 `app.serializers.identity` 或 `app.services.identity_serializers`。
- 业务服务不依赖 API 包，API 包只依赖服务层。

### 首次请求变慢

懒加载会把部分成本从启动阶段转移到首次使用某个功能时。

应对方式：

- 对爬虫、材料解析、LLM 这类用户主动触发的重功能，这是可接受的权衡。
- 在触发按钮或任务状态中保留加载反馈。

### 打包遗漏风险

PyInstaller 可能因为懒加载而漏收集某些动态导入依赖。

应对方式：

- 保留或补充 `scripts/build-backend.ps1` 中的 `--hidden-import`、`--collect-all` 配置。
- 对打包后的 `backend.exe` 跑基本冒烟测试，覆盖材料解析、爬虫、LLM 配置查询等懒加载路径。

## 验证计划

### 性能验证

- 运行 `uv run python -X importtime -c "import main"`，比较优化前后的 `main` 累计导入耗时。
- 使用打包后的 `backend.exe` 记录从进程启动到 `/health` 返回 200 的耗时。
- 使用同一台机器做至少 3 次冷/热启动对比，记录最慢值。

### 功能验证

- 运行后端单元测试：`cd backend && uv run python -m unittest discover test`。
- 运行桌面端类型检查：`cd desktop && npm run typecheck`。
- 运行桌面端测试：`cd desktop && npm run test`。
- 打包后手动验证桌面端能正常启动，并能访问教授、身份、LLM 配置、材料上传、爬虫任务和诊断导出。

### 诊断验证

- 模拟后端启动慢，确认前端 30 秒后仍显示等待状态。
- 模拟后端进程退出，确认错误信息包含 stderr 尾部。
- 模拟后端存活但端口未监听，确认诊断事件包含 PID、端口和数据目录。

## 成功标准

- 热启动环境下，`/health` 可响应时间明显低于当前版本。
- 冷启动环境下，桌面端不再因为 30 秒阈值误报「系统准备失败」。
- `import app.api` 不再触发所有路由导入。
- 启动失败诊断能明确指出失败阶段，而不是只给出空的 `Backend health check timed out:`。
- 现有业务功能和 API 契约保持兼容。

## 实施验证记录

- `import main` 优化前：约 3.796 秒；优化后：约 1.702 秒。
- 导入边界测试：`cd backend && uv run python -m unittest test.test_api_import_boundaries`，PASS。
- 启动日志测试：`cd backend && uv run python -m unittest test.test_startup_runtime`，PASS。
- 懒加载重点回归：`cd backend && uv run python -m unittest test.test_crawl_job_runtime.CrawlJobThinkingAdaptationIntegrationTests.test_thinking_adaptation_failure_marks_job_failed_and_skips_run test.test_crawl_jobs_api.CrawlJobsApiTests.test_enrich_selected_candidates_returns_summary test.test_operation_log_integration.OperationLogIntegrationTests.test_smtp_test_records_result_without_sensitive_fields`，PASS。
- 桌面端测试：`cd desktop && npm.cmd run test -- backend.test.ts`，14 个测试 PASS。
- 桌面端类型检查：`cd desktop && npm.cmd run typecheck`，PASS。
- 后端完整测试：`cd backend && uv run python -m unittest discover test`，564 个测试 PASS。
