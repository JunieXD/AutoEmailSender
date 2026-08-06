# 运行时 Worker 设置启动接线实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 让“其他设置”中标记为重启生效的后台 worker 配置在后端重启后真实驱动 `RuntimeManager` 启动行为。

**架构：** 在 `RuntimeManager.start()` 启动后台循环前读取一次数据库运行时设置，将数据库中的 worker 数和批量匹配轮询间隔合并为启动配置；数据库读取失败时回退到环境变量和默认值。实现保持局部，不引入动态 worker 扩缩容。

**技术栈：** Python、FastAPI 后端服务层、SQLAlchemy async session factory、`unittest`、`unittest.mock`。

---

## 文件结构

- 修改：`backend/app/services/runtime_manager.py`
  - 职责：新增启动期 worker 设置解析逻辑，并让 crawler/match-analysis worker 创建使用数据库运行时设置优先、环境设置回退的值。
- 修改：`backend/test/test_runtime_manager.py`
  - 职责：用单元测试锁定 `RuntimeManager.start()` 的数据库设置优先行为和数据库读取失败回退行为。

---

### 任务 1：用红灯测试锁定数据库设置优先级

**文件：**
- 修改：`backend/test/test_runtime_manager.py`

- [ ] **步骤 1：编写失败的启动接线测试**

在 `RuntimeManagerTests` 中新增测试，放在现有 `test_start_creates_multiple_crawler_workers_from_settings` 后面：

```python
    async def test_start_uses_runtime_settings_for_worker_counts_and_match_interval(self) -> None:
        session = object()
        session_context = MagicMock()
        session_context.__aenter__ = AsyncMock(return_value=session)
        session_context.__aexit__ = AsyncMock(return_value=None)
        session_factory = Mock(return_value=session_context)
        manager = RuntimeManager(session_factory)

        async def idle_loop() -> None:
            await asyncio.Event().wait()

        def build_idle_loop(*args: object, **kwargs: object):
            _ = args, kwargs
            return idle_loop()

        async def fake_get_runtime_settings(session_arg: object) -> SimpleNamespace:
            self.assertIs(session_arg, session)
            return SimpleNamespace(
                crawler_worker_count=3,
                match_analysis_job_worker_count=2,
                match_analysis_job_interval_seconds=5,
            )

        with patch("app.services.runtime_manager.get_settings") as mocked_get_settings:
            mocked_get_settings.return_value = type(
                "SettingsStub",
                (),
                {
                    "dispatcher_interval_seconds": 30,
                    "imap_poll_interval_seconds": 60,
                    "crawler_worker_count": 1,
                    "match_analysis_job_worker_count": 1,
                    "match_analysis_job_interval_seconds": 10,
                },
            )()
            with patch(
                "app.services.runtime_manager.get_runtime_settings",
                new=fake_get_runtime_settings,
            ), patch.object(
                manager,
                "_loop",
                new=Mock(side_effect=build_idle_loop),
            ) as mocked_loop:
                await manager.start()

        worker_calls = {call.args[0]: call.args for call in mocked_loop.call_args_list}
        self.assertIn("crawler-worker-1", worker_calls)
        self.assertIn("crawler-worker-2", worker_calls)
        self.assertIn("crawler-worker-3", worker_calls)
        self.assertNotIn("crawler-worker-4", worker_calls)
        self.assertEqual(worker_calls["match-analysis-worker-1"][1], 5)
        self.assertEqual(worker_calls["match-analysis-worker-2"][1], 5)
        self.assertNotIn("match-analysis-worker-3", worker_calls)

        await manager.stop()
```

- [ ] **步骤 2：运行测试验证失败**

运行：

```bash
cd backend
uv run python -m unittest test.test_runtime_manager.RuntimeManagerTests.test_start_uses_runtime_settings_for_worker_counts_and_match_interval
```

预期：FAIL，至少因为当前实现只创建 `crawler-worker-1`，没有 `crawler-worker-2` 和 `crawler-worker-3`。

- [ ] **步骤 3：Commit 红灯测试**

```bash
git add backend/test/test_runtime_manager.py
git commit -m "test(backend): 覆盖运行时 worker 启动设置"
```

---

### 任务 2：实现数据库设置优先的启动配置解析

**文件：**
- 修改：`backend/app/services/runtime_manager.py`
- 测试：`backend/test/test_runtime_manager.py`

- [ ] **步骤 1：新增启动配置 dataclass**

在 `RuntimeManager` 类定义前新增 dataclass，并补充 import：

```python
from dataclasses import dataclass
from typing import Any
```

```python
@dataclass(frozen=True, slots=True)
class RuntimeWorkerStartupSettings:
    crawler_worker_count: int
    match_analysis_job_worker_count: int
    match_analysis_job_interval_seconds: int
```

- [ ] **步骤 2：新增归一化 helper**

在 dataclass 后新增函数：

```python
def _positive_int(value: Any, fallback: int) -> int:
    if isinstance(value, bool):
        return max(1, fallback)
    try:
        return max(1, int(value))
    except (TypeError, ValueError):
        return max(1, fallback)
```

- [ ] **步骤 3：新增 `RuntimeManager._resolve_worker_startup_settings`**

在 `RuntimeManager` 类内、`start()` 前新增私有方法：

```python
    async def _resolve_worker_startup_settings(
        self,
        settings: object,
    ) -> RuntimeWorkerStartupSettings:
        fallback = RuntimeWorkerStartupSettings(
            crawler_worker_count=_positive_int(
                getattr(settings, "crawler_worker_count", 2),
                2,
            ),
            match_analysis_job_worker_count=_positive_int(
                getattr(settings, "match_analysis_job_worker_count", 1),
                1,
            ),
            match_analysis_job_interval_seconds=_positive_int(
                getattr(settings, "match_analysis_job_interval_seconds", 10),
                10,
            ),
        )

        try:
            async with self._session_factory() as session:
                runtime_settings = await get_runtime_settings(session)
        except Exception:
            logger.exception("读取运行时 worker 设置失败，已回退到环境配置")
            return fallback

        try:
            return RuntimeWorkerStartupSettings(
                crawler_worker_count=_positive_int(
                    runtime_settings.crawler_worker_count,
                    fallback.crawler_worker_count,
                ),
                match_analysis_job_worker_count=_positive_int(
                    runtime_settings.match_analysis_job_worker_count,
                    fallback.match_analysis_job_worker_count,
                ),
                match_analysis_job_interval_seconds=_positive_int(
                    runtime_settings.match_analysis_job_interval_seconds,
                    fallback.match_analysis_job_interval_seconds,
                ),
            )
        except Exception:
            logger.exception("运行时 worker 设置字段不完整，已回退到环境配置")
            return fallback
```

- [ ] **步骤 4：让 `start()` 使用启动配置**

把 `start()` 中创建 crawler 和 match-analysis worker 的部分改为：

```python
        settings = get_settings()
        worker_settings = await self._resolve_worker_startup_settings(settings)
        crawler_tasks = [
            asyncio.create_task(
                self._loop(
                    f"crawler-worker-{index}",
                    10,
                    run_queued_crawl_jobs_once,
                ),
            )
            for index in range(1, worker_settings.crawler_worker_count + 1)
        ]
        match_analysis_tasks = [
            asyncio.create_task(
                self._loop(
                    f"match-analysis-worker-{index}",
                    worker_settings.match_analysis_job_interval_seconds,
                    _run_match_analysis_worker_once,
                ),
            )
            for index in range(1, worker_settings.match_analysis_job_worker_count + 1)
        ]
```

- [ ] **步骤 5：运行任务 1 测试验证通过**

运行：

```bash
cd backend
uv run python -m unittest test.test_runtime_manager.RuntimeManagerTests.test_start_uses_runtime_settings_for_worker_counts_and_match_interval
```

预期：PASS。

- [ ] **步骤 6：Commit 最小实现**

```bash
git add backend/app/services/runtime_manager.py backend/test/test_runtime_manager.py
git commit -m "fix(backend): 使用运行时设置启动后台 worker"
```

---

### 任务 3：覆盖数据库读取失败回退路径

**文件：**
- 修改：`backend/test/test_runtime_manager.py`
- 可能修改：`backend/app/services/runtime_manager.py`

- [ ] **步骤 1：编写回退路径测试**

在 `RuntimeManagerTests` 中新增：

```python
    async def test_start_falls_back_to_environment_worker_settings_when_runtime_settings_fail(self) -> None:
        session = object()
        session_context = MagicMock()
        session_context.__aenter__ = AsyncMock(return_value=session)
        session_context.__aexit__ = AsyncMock(return_value=None)
        session_factory = Mock(return_value=session_context)
        manager = RuntimeManager(session_factory)

        async def idle_loop() -> None:
            await asyncio.Event().wait()

        def build_idle_loop(*args: object, **kwargs: object):
            _ = args, kwargs
            return idle_loop()

        async def fail_get_runtime_settings(session_arg: object) -> SimpleNamespace:
            self.assertIs(session_arg, session)
            raise RuntimeError("database unavailable")

        with patch("app.services.runtime_manager.get_settings") as mocked_get_settings:
            mocked_get_settings.return_value = type(
                "SettingsStub",
                (),
                {
                    "dispatcher_interval_seconds": 30,
                    "imap_poll_interval_seconds": 60,
                    "crawler_worker_count": 2,
                    "match_analysis_job_worker_count": 1,
                    "match_analysis_job_interval_seconds": 11,
                },
            )()
            with patch(
                "app.services.runtime_manager.get_runtime_settings",
                new=fail_get_runtime_settings,
            ), patch.object(
                manager,
                "_loop",
                new=Mock(side_effect=build_idle_loop),
            ) as mocked_loop, patch(
                "app.services.runtime_manager.logger.exception",
            ) as mocked_log_exception:
                await manager.start()

        worker_calls = {call.args[0]: call.args for call in mocked_loop.call_args_list}
        self.assertIn("crawler-worker-1", worker_calls)
        self.assertIn("crawler-worker-2", worker_calls)
        self.assertNotIn("crawler-worker-3", worker_calls)
        self.assertEqual(worker_calls["match-analysis-worker-1"][1], 11)
        mocked_log_exception.assert_called_once_with(
            "读取运行时 worker 设置失败，已回退到环境配置",
        )

        await manager.stop()
```

- [ ] **步骤 2：运行回退测试**

运行：

```bash
cd backend
uv run python -m unittest test.test_runtime_manager.RuntimeManagerTests.test_start_falls_back_to_environment_worker_settings_when_runtime_settings_fail
```

预期：PASS。如果失败，修正 `_resolve_worker_startup_settings` 的异常捕获或日志调用，不扩大实现范围。

- [ ] **步骤 3：运行完整 runtime manager 测试**

运行：

```bash
cd backend
uv run python -m unittest test.test_runtime_manager
```

预期：PASS。

- [ ] **步骤 4：Commit 回退测试**

```bash
git add backend/app/services/runtime_manager.py backend/test/test_runtime_manager.py
git commit -m "test(backend): 覆盖 worker 设置回退路径"
```

---

### 任务 4：回归验证

**文件：**
- 验证：`backend/test/test_runtime_manager.py`
- 验证：`backend/test/test_runtime_settings_api.py`
- 验证：`backend/test/test_crawl_job_runtime.py`

- [ ] **步骤 1：运行运行时设置 API 测试**

运行：

```bash
cd backend
uv run python -m unittest test.test_runtime_settings_api
```

预期：PASS，确认设置保存和读取 API 未被破坏。

- [ ] **步骤 2：运行抓取运行时并发相关测试**

运行：

```bash
cd backend
uv run python -m unittest test.test_crawl_job_runtime.CrawlJobRuntimeTests.test_resolve_crawl_runtime_concurrency_prefers_database_settings test.test_crawl_job_runtime.CrawlJobRuntimeTests.test_enrich_saved_candidates_limits_concurrency test.test_crawl_job_runtime.CrawlJobRuntimeTests.test_enrich_saved_candidates_limits_same_host_to_one_request
```

预期：PASS，确认单任务内部详情页补全并发仍按数据库设置执行。

- [ ] **步骤 3：运行后端聚焦回归套件**

运行：

```bash
cd backend
uv run python -m unittest test.test_runtime_manager test.test_runtime_settings_api
```

预期：PASS。

- [ ] **步骤 4：检查 git 状态**

运行：

```bash
git status --short
```

预期：只显示本次实现相关文件变更，或者显示用户已有的无关变更。不要暂存或回退无关文件。

- [ ] **步骤 5：最终提交**

如果任务 2 和任务 3 已经分别提交，且任务 4 没有引入新文件变更，则不需要额外提交。若验证中补了代码或测试，运行：

```bash
git add backend/app/services/runtime_manager.py backend/test/test_runtime_manager.py
git commit -m "fix(backend): 完成运行时 worker 设置回归"
```

---

## 自检

- 规格覆盖度：计划覆盖数据库设置优先、环境回退、三项重启生效字段、保持前端和任务执行期设置不变。
- 占位符扫描：无 TODO、待定、后续实现或模糊步骤。
- 类型一致性：计划中的 `RuntimeWorkerStartupSettings`、`_positive_int`、`_resolve_worker_startup_settings` 在任务 2 定义后使用；测试使用现有 `RuntimeManagerTests`、`MagicMock`、`AsyncMock`、`SimpleNamespace` 导入。
