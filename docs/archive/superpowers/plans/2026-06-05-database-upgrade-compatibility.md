# 数据库升级回退兼容实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 实现数据库升级前自动备份、应用级 schema 元信息、未来数据库版本拦截，以及桌面端友好错误提示。

**架构：** 后端新增 schema metadata、备份和迁移编排模块，由 `ensure_database_schema()` 串联兼容检查、备份、Alembic upgrade 与元信息写入。启动状态扩展为结构化错误，Electron 透传到前端，前端 banner 和 API client 对 `DATABASE_REQUIRES_NEWER_APP` 展示专用文案。

**技术栈：** FastAPI、Alembic、SQLite、unittest、Electron TypeScript、React、Vitest。

---

## 文件结构

- 创建：`backend/app/core/schema_metadata.py`，负责 schema 常量、版本比较、SQLite 路径解析、`app_metadata` 读写、未来数据库版本异常。
- 创建：`backend/app/core/schema_backup.py`，负责迁移前复制 SQLite 数据库、写入 `.json` 元信息、保留最近 5 份备份。
- 修改：`backend/app/core/migrations.py`，负责兼容检查、备份、Alembic upgrade、metadata 写入。
- 修改：`backend/main.py`，负责结构化 startup status 和 `/ready` 错误 detail。
- 修改/创建：`backend/test/test_schema_metadata.py`、`backend/test/test_schema_backup.py`、`backend/test/test_migrations_runtime.py`、`backend/test/test_desktop_runtime.py`。
- 修改：`desktop/src/types.ts`、`desktop/src/backend.ts`、`desktop/test/backend.test.ts`，负责结构化错误透传。
- 修改：`frontend/src/types/desktop.d.ts`、`frontend/src/components/organisms/DesktopStartupStatusBanner.tsx`、`frontend/src/lib/api/client.ts` 及对应测试，负责用户提示。

---

### 任务 1：建立 schema metadata 核心能力

**文件：**
- 创建：`backend/app/core/schema_metadata.py`
- 创建：`backend/test/test_schema_metadata.py`

- [ ] **步骤 1：编写失败的测试**

创建 `backend/test/test_schema_metadata.py`，覆盖 SQLite URL 解析、非 SQLite URL 返回 `None`、`app_metadata` 写入读取、缺失 metadata 的旧库允许继续、`minimum_supported_app_version` 高于当前版本时抛出 `DatabaseRequiresNewerAppError`。

关键测试代码：

```python
with sqlite3.connect(db_path) as connection:
    connection.execute("CREATE TABLE app_metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
    connection.execute(
        "INSERT INTO app_metadata (key, value) VALUES (?, ?)",
        ("minimum_supported_app_version", "2.4.0"),
    )
    connection.commit()
    with self.assertRaises(DatabaseRequiresNewerAppError) as context:
        check_database_compatibility(connection, current_app_version="2.3.0")
self.assertEqual(context.exception.code, "DATABASE_REQUIRES_NEWER_APP")
```

- [ ] **步骤 2：运行测试验证失败**

运行：`cd backend; uv run python -m unittest test.test_schema_metadata`

预期：失败，包含 `ModuleNotFoundError: No module named 'app.core.schema_metadata'`。

- [ ] **步骤 3：实现最少代码**

在 `schema_metadata.py` 中定义：

```python
CURRENT_SCHEMA_VERSION = 1
MINIMUM_SUPPORTED_APP_VERSION = "2.3.0"
CURRENT_APP_VERSION = "2.3.0"
DATABASE_REQUIRES_NEWER_APP = "DATABASE_REQUIRES_NEWER_APP"
```

实现这些函数和类型：

```python
@dataclass(slots=True)
class DatabaseRequiresNewerAppError(RuntimeError):
    current_app_version: str
    minimum_supported_app_version: str
    backup_directory: Path

    @property
    def code(self) -> str:
        return DATABASE_REQUIRES_NEWER_APP
    def to_payload(self) -> dict[str, object]:
        return {
            "code": self.code,
            "message": "当前数据由较新版本创建，当前版本无法直接打开。",
            "current_app_version": self.current_app_version,
            "minimum_supported_app_version": self.minimum_supported_app_version,
            "backup_directory": str(self.backup_directory),
            "suggested_actions": [
                f"安装 {self.minimum_supported_app_version} 或更高版本继续使用",
                "如需回退，请从升级前备份恢复数据库",
            ],
        }

def get_schema_backup_dir(data_dir: Path) -> Path:
    return data_dir / "backups" / "schema"
def get_sqlite_database_path(database_url: str) -> Path | None:
    # 支持 sqlite:/// 与 sqlite+aiosqlite:///，非 SQLite 返回 None。
    parsed = urlparse(database_url)
    if parsed.scheme not in {"sqlite", "sqlite+aiosqlite"}:
        return None
    raw_path = parsed.path
    if raw_path.startswith("/") and len(raw_path) >= 3 and raw_path[2] == ":":
        raw_path = raw_path[1:]
    return Path(unquote(raw_path))
def ensure_app_metadata_table(connection: sqlite3.Connection) -> None:
    connection.execute("CREATE TABLE IF NOT EXISTS app_metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
def read_app_metadata(connection: sqlite3.Connection) -> dict[str, str]:
    rows = connection.execute("SELECT key, value FROM app_metadata").fetchall()
    return {str(key): str(value) for key, value in rows}
def check_database_compatibility(connection: sqlite3.Connection, *, current_app_version: str, backup_directory: Path | None = None) -> None:
    metadata = read_app_metadata(connection)
    minimum = metadata.get("minimum_supported_app_version")
    if minimum and compare_versions(current_app_version, minimum) < 0:
        raise DatabaseRequiresNewerAppError(current_app_version, minimum, backup_directory or Path("backups") / "schema")
def update_app_metadata(connection: sqlite3.Connection, *, app_version: str, schema_revision: str) -> None:
    ensure_app_metadata_table(connection)
    values = {
        "schema_version": str(CURRENT_SCHEMA_VERSION),
        "schema_revision": schema_revision,
        "schema_updated_by_app_version": app_version,
        "minimum_supported_app_version": MINIMUM_SUPPORTED_APP_VERSION,
        "schema_updated_at": datetime.now(UTC).isoformat(),
    }
    connection.executemany("INSERT INTO app_metadata (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value", values.items())
    connection.commit()
def compare_versions(left: str, right: str) -> int:
    left_parts = [int(part) for part in left.lstrip("v").split(".")]
    right_parts = [int(part) for part in right.lstrip("v").split(".")]
    return (left_parts > right_parts) - (left_parts < right_parts)
```

`to_payload()` 必须返回 `code`、`message`、`current_app_version`、`minimum_supported_app_version`、`backup_directory`、`suggested_actions`。

- [ ] **步骤 4：运行测试验证通过**

运行：`cd backend; uv run python -m unittest test.test_schema_metadata`

预期：`OK`。

- [ ] **步骤 5：Commit**

```powershell
git add backend/app/core/schema_metadata.py backend/test/test_schema_metadata.py
git commit -m "feat(数据库): 添加 schema 元信息能力"
```

---
### 任务 2：实现 schema 备份服务

**文件：**
- 创建：`backend/app/core/schema_backup.py`
- 创建：`backend/test/test_schema_backup.py`

- [ ] **步骤 1：编写失败的测试**

创建 `backend/test/test_schema_backup.py`，覆盖：

- `create_schema_backup()` 会复制 SQLite 文件并写入同名 `.json`。
- `.json` 包含 `created_at`、`app_version`、`database_path`、`reason`、`source_schema_revision`、`target_schema_revision`。
- `prune_schema_backups()` 只保留最近 5 组 `.db` / `.json`。

关键测试代码：

```python
result = create_schema_backup(
    database_path=db_path,
    backup_dir=backup_dir,
    app_version="2.3.0",
    source_schema_revision="04d66ff4c25b",
    target_schema_revision="d6e4b8c2a1f0",
)
self.assertTrue(result.database_backup_path.exists())
self.assertTrue(result.metadata_path.exists())
metadata = json.loads(result.metadata_path.read_text(encoding="utf-8"))
self.assertEqual(metadata["reason"], "before_schema_migration")
```

- [ ] **步骤 2：运行测试验证失败**

运行：`cd backend; uv run python -m unittest test.test_schema_backup`

预期：失败，包含 `ModuleNotFoundError: No module named 'app.core.schema_backup'`。

- [ ] **步骤 3：实现最少代码**

创建 `backend/app/core/schema_backup.py`，定义：

```python
SCHEMA_BACKUP_KEEP_COUNT = 5

@dataclass(frozen=True, slots=True)
class SchemaBackupResult:
    database_backup_path: Path
    metadata_path: Path

def create_schema_backup(*, database_path: Path, backup_dir: Path, app_version: str, source_schema_revision: str | None, target_schema_revision: str) -> SchemaBackupResult:
    # 创建备份目录、复制数据库、写入 JSON 元信息，然后调用 prune_schema_backups()。
def prune_schema_backups(backup_dir: Path, *, keep: int = SCHEMA_BACKUP_KEEP_COUNT) -> None:
    # 按 created_at 或文件修改时间排序，删除第 keep + 1 个之后的备份组。
```

实现要求：

- 文件名格式：`auto_email_sender.before-{app_version}.{YYYYMMDD-HHMMSS}.db`。
- 用 `shutil.copy2()` 复制数据库。
- 用 `json.dumps(metadata, ensure_ascii=False, indent=2)` 写元信息。
- 清理时 `.db` 和同名 `.json` 成组删除。
- 排序优先读 `.json` 的 `created_at`，失败时用 `.db` 修改时间。

- [ ] **步骤 4：运行测试验证通过**

运行：`cd backend; uv run python -m unittest test.test_schema_backup`

预期：`OK`。

- [ ] **步骤 5：Commit**

```powershell
git add backend/app/core/schema_backup.py backend/test/test_schema_backup.py
git commit -m "feat(数据库): 添加迁移前备份服务"
```

---

### 任务 3：迁移后写入 schema metadata

**文件：**
- 修改：`backend/app/core/migrations.py`
- 修改：`backend/test/test_schema_metadata.py`

- [ ] **步骤 1：编写失败的测试**

在 `backend/test/test_schema_metadata.py` 增加集成测试，使用临时 `DATABASE_URL` 调用 `run_migrations_to_head()`，然后读取 `app_metadata`：

```python
with tempfile.TemporaryDirectory() as temp_dir:
    db_path = Path(temp_dir) / "runtime.db"
    with patch.dict(os.environ, {"DATABASE_URL": f"sqlite+aiosqlite:///{db_path.as_posix()}"}):
        from app.core.config import get_settings
        from app.core.migrations import run_migrations_to_head
        get_settings.cache_clear()
        run_migrations_to_head()
    with sqlite3.connect(db_path) as connection:
        metadata = read_app_metadata(connection)
self.assertEqual(metadata["minimum_supported_app_version"], "2.3.0")
self.assertEqual(metadata["schema_updated_by_app_version"], "2.3.0")
self.assertIn("schema_revision", metadata)
```

- [ ] **步骤 2：运行测试验证失败**

运行：`cd backend; uv run python -m unittest test.test_schema_metadata`

预期：新增测试失败，`app_metadata` 为空或不存在。

- [ ] **步骤 3：实现迁移后 metadata 写入**

修改 `backend/app/core/migrations.py`：

- 增加 `get_alembic_config()`。
- 增加 `get_head_revision()`，使用 `alembic.script.ScriptDirectory.from_config(config).get_current_head()`。
- 增加 `get_current_database_revision(connection)`，读取 `alembic_version.version_num`。
- `run_migrations_to_head()` 在 `command.upgrade(config, "head")` 后，如果 SQLite 数据库存在，则调用 `update_app_metadata(connection, app_version=CURRENT_APP_VERSION, schema_revision=target_revision)`。

关键代码形状：

```python
settings = get_settings()
database_path = get_sqlite_database_path(settings.database_url)
target_revision = get_head_revision()
config = get_alembic_config()
command.upgrade(config, "head")
if database_path is not None and database_path.exists():
    with sqlite3.connect(database_path) as connection:
        update_app_metadata(connection, app_version=CURRENT_APP_VERSION, schema_revision=target_revision)
```

- [ ] **步骤 4：运行测试验证通过**

运行：`cd backend; uv run python -m unittest test.test_schema_metadata test.test_database_schema.MigrationScriptTests`

预期：全部 `OK`。

- [ ] **步骤 5：Commit**

```powershell
git add backend/app/core/migrations.py backend/test/test_schema_metadata.py
git commit -m "feat(数据库): 迁移后写入 schema 元信息"
```

---
### 任务 4：迁移前兼容检查和自动备份

**文件：**
- 修改：`backend/app/core/migrations.py`
- 创建：`backend/test/test_migrations_runtime.py`

- [ ] **步骤 1：编写失败的测试**

创建 `backend/test/test_migrations_runtime.py`，覆盖两个行为：

1. 已存在的 SQLite 数据库在迁移前会生成 `.db` 和 `.json` 备份。
2. `create_schema_backup()` 抛错时，不调用 `alembic.command.upgrade()`。

关键测试代码：

```python
with patch.object(migrations, "create_schema_backup", side_effect=OSError("copy failed")), \
     patch.object(migrations.command, "upgrade") as upgrade:
    with self.assertRaises(OSError):
        migrations.run_migrations_to_head()
upgrade.assert_not_called()
```

另加未来数据库版本测试：预置 `app_metadata.minimum_supported_app_version = 9.9.9`，patch `migrations.command.upgrade`，调用 `run_migrations_to_head()` 后断言抛出 `DatabaseRequiresNewerAppError` 且 `upgrade.assert_not_called()`。

- [ ] **步骤 2：运行测试验证失败**

运行：`cd backend; uv run python -m unittest test.test_migrations_runtime`

预期：失败，尚未创建备份或未来版本仍可能进入 Alembic。

- [ ] **步骤 3：实现编排逻辑**

修改 `run_migrations_to_head()`：

```python
source_revision: str | None = None
should_backup = False
if database_path is not None and database_path.exists():
    with sqlite3.connect(database_path) as connection:
        check_database_compatibility(
            connection,
            current_app_version=CURRENT_APP_VERSION,
            backup_directory=get_schema_backup_dir(settings.data_dir),
        )
        source_revision = get_current_database_revision(connection)
        metadata = read_app_metadata(connection)
        should_backup = source_revision != target_revision or not metadata

if database_path is not None and database_path.exists() and should_backup:
    create_schema_backup(
        database_path=database_path,
        backup_dir=get_schema_backup_dir(settings.data_dir),
        app_version=CURRENT_APP_VERSION,
        source_schema_revision=source_revision,
        target_schema_revision=target_revision,
    )

command.upgrade(config, "head")
```

导入 `create_schema_backup`、`check_database_compatibility`、`read_app_metadata`、`get_schema_backup_dir`。

- [ ] **步骤 4：运行测试验证通过**

运行：`cd backend; uv run python -m unittest test.test_migrations_runtime test.test_schema_backup test.test_schema_metadata`

预期：全部 `OK`。

- [ ] **步骤 5：Commit**

```powershell
git add backend/app/core/migrations.py backend/test/test_migrations_runtime.py
git commit -m "feat(数据库): 迁移前检查兼容并备份"
```

---

### 任务 5：后端 startup status 返回结构化数据库版本错误

**文件：**
- 修改：`backend/main.py`
- 修改：`backend/test/test_desktop_runtime.py`
- 修改：`backend/test/test_startup_runtime.py`

- [ ] **步骤 1：编写失败的测试**

在 `backend/test/test_desktop_runtime.py` 增加测试：patch `ensure_database_schema()` 抛出 `DatabaseRequiresNewerAppError(current_app_version="2.3.0", minimum_supported_app_version="2.4.0", backup_directory=Path(self.temp_dir.name) / "AutoEmailSender" / "backups" / "schema")`，请求 `/startup-status` 和 `/ready`。

断言：

```python
self.assertEqual(data["state"], "error")
self.assertEqual(data["error_detail"]["code"], "DATABASE_REQUIRES_NEWER_APP")
self.assertEqual(data["error_detail"]["minimum_supported_app_version"], "2.4.0")
self.assertEqual(ready_response.status_code, 500)
self.assertEqual(ready_response.json()["detail"]["code"], "DATABASE_REQUIRES_NEWER_APP")
```

- [ ] **步骤 2：运行测试验证失败**

运行：`cd backend; uv run python -m unittest test.test_desktop_runtime.DesktopRuntimeTests.test_startup_status_reports_database_requires_newer_app_detail`

预期：失败，`error_detail` 不存在或 `/ready` detail 是字符串。

- [ ] **步骤 3：实现结构化错误**

修改 `backend/main.py`：

- `StartupStatus` 增加 `error_detail: dict[str, object] | None = None`。
- `set_startup_status()` 增加同名参数并写入 dataclass。
- 新增：

```python
def build_startup_error_detail(exc: Exception) -> dict[str, object] | None:
    if isinstance(exc, DatabaseRequiresNewerAppError):
        return exc.to_payload()
    return None
```

- `initialize_runtime()` 的异常分支设置 `app.state.runtime_error_detail = error_detail`。
- `/ready` 中如果有 `runtime_error_detail`，用它作为 `HTTPException.detail`。

- [ ] **步骤 4：运行测试验证通过**

运行：`cd backend; uv run python -m unittest test.test_desktop_runtime test.test_startup_runtime`

预期：全部 `OK`。

- [ ] **步骤 5：Commit**

```powershell
git add backend/main.py backend/test/test_desktop_runtime.py backend/test/test_startup_runtime.py
git commit -m "feat(启动): 返回数据库版本结构化错误"
```

---

### 任务 6：Electron 透传结构化启动错误

**文件：**
- 修改：`desktop/src/types.ts`
- 修改：`desktop/src/backend.ts`
- 修改：`desktop/test/backend.test.ts`

- [ ] **步骤 1：编写失败的测试**

扩展 `desktop/test/backend.test.ts` 的 `StartupStatusFixture`，增加可选 `error_detail`。新增测试：`/startup-status` 返回 `error_detail.code = DATABASE_REQUIRES_NEWER_APP` 时，`waitForStartupStatus()` emit 的 `BackendStatus` 包含：

```typescript
expect.objectContaining({
  state: "error",
  databaseError: expect.objectContaining({
    code: "DATABASE_REQUIRES_NEWER_APP",
    minimumSupportedAppVersion: "2.4.0",
  }),
})
```

- [ ] **步骤 2：运行测试验证失败**

运行：`cd desktop; npm run test -- backend.test.ts`

预期：失败，`databaseError` 不存在。

- [ ] **步骤 3：实现类型和映射**

在 `desktop/src/types.ts` 增加 `DatabaseRequiresNewerAppDetail` 和 `BackendDatabaseError`。`BackendStartupStatus` 增加 `error_detail?: DatabaseRequiresNewerAppDetail | null`。`BackendStatus` 的 error 分支增加 `databaseError?: BackendDatabaseError`。

在 `desktop/src/backend.ts` 增加：

```typescript
function mapDatabaseError(detail: BackendStartupStatus["error_detail"]): BackendDatabaseError | undefined {
  if (!detail || detail.code !== "DATABASE_REQUIRES_NEWER_APP") return undefined;
  return {
    code: detail.code,
    message: detail.message,
    currentAppVersion: detail.current_app_version,
    minimumSupportedAppVersion: detail.minimum_supported_app_version,
    backupDirectory: detail.backup_directory,
    suggestedActions: detail.suggested_actions,
  };
}
```

在 error status 构造时赋值 `databaseError: mapDatabaseError(status.error_detail)`。

- [ ] **步骤 4：运行测试和类型检查**

运行：

```powershell
cd desktop
npm run test -- backend.test.ts
npm run typecheck
```

预期：两条命令均 exit 0。

- [ ] **步骤 5：Commit**

```powershell
git add desktop/src/types.ts desktop/src/backend.ts desktop/test/backend.test.ts
git commit -m "feat(桌面): 透传数据库版本启动错误"
```

---
### 任务 7：前端 banner 展示数据库版本错误

**文件：**
- 修改：`frontend/src/types/desktop.d.ts`
- 修改：`frontend/src/components/organisms/DesktopStartupStatusBanner.tsx`
- 创建：`frontend/src/components/organisms/DesktopStartupStatusBanner.test.tsx`

- [ ] **步骤 1：编写失败的测试**

创建 `DesktopStartupStatusBanner.test.tsx`，mock `useDesktopBackend()` 返回 `state: "error"` 且含 `databaseError`。断言页面显示：

```typescript
expect(screen.getByText("当前数据需要 AutoEmailSender 2.4.0 或更高版本")).toBeInTheDocument();
expect(screen.getByText(/请升级到新版继续使用/)).toBeInTheDocument();
expect(screen.getByText("C:\\Users\\Alice\\AppData\\Roaming\\AutoEmailSender\\backups\\schema")).toBeInTheDocument();
```

- [ ] **步骤 2：运行测试验证失败**

运行：`cd frontend; npm run test:dom -- DesktopStartupStatusBanner.test.tsx`

预期：失败，类型缺少 `databaseError` 或页面仍显示通用错误。

- [ ] **步骤 3：扩展前端类型**

在 `frontend/src/types/desktop.d.ts` 增加：

```typescript
export type DesktopBackendDatabaseError = {
  code: "DATABASE_REQUIRES_NEWER_APP";
  message: string;
  currentAppVersion: string;
  minimumSupportedAppVersion: string;
  backupDirectory: string;
  suggestedActions: string[];
};
```

给 `DesktopBackendStatus` 的 error 分支增加 `databaseError?: DesktopBackendDatabaseError`。

- [ ] **步骤 4：实现专用 banner**

在 `DesktopStartupStatusBanner.tsx` 的通用 error 分支前增加数据库版本错误分支。文案必须使用：

```text
当前数据需要 AutoEmailSender {minimumSupportedAppVersion} 或更高版本
请升级到新版继续使用。若必须回退旧版，请从升级前备份恢复数据库。
```

备份目录用 `font-mono text-xs break-all` 容器显示，避免长 Windows 路径撑破布局。

- [ ] **步骤 5：运行测试和 lint**

运行：

```powershell
cd frontend
npm run test:dom -- DesktopStartupStatusBanner.test.tsx
npm run lint
```

预期：两条命令均 exit 0。

- [ ] **步骤 6：Commit**

```powershell
git add frontend/src/types/desktop.d.ts frontend/src/components/organisms/DesktopStartupStatusBanner.tsx frontend/src/components/organisms/DesktopStartupStatusBanner.test.tsx
git commit -m "feat(前端): 展示数据库版本不兼容提示"
```

---

### 任务 8：API client 使用数据库版本专用错误信息

**文件：**
- 修改：`frontend/src/lib/api/client.ts`
- 修改：`frontend/src/lib/api/client.test.ts`

- [ ] **步骤 1：编写失败的测试**

在 `client.test.ts` 增加测试：当 `onBackendStatus()` 收到 `state: "error"` 且含 `databaseError`，`apiFetch("/health")` reject 的错误消息包含 `当前数据需要 AutoEmailSender 2.4.0 或更高版本` 和备份路径。

关键事件 payload：

```typescript
backendStatusCallback?.({
  state: "error",
  phase: "error",
  message: "系统准备失败",
  elapsedSeconds: 10,
  detail: "当前数据由较新版本创建，当前版本无法直接打开。",
  databaseError: {
    code: "DATABASE_REQUIRES_NEWER_APP",
    message: "当前数据由较新版本创建，当前版本无法直接打开。",
    currentAppVersion: "2.3.0",
    minimumSupportedAppVersion: "2.4.0",
    backupDirectory: "C:\\Users\\Alice\\AppData\\Roaming\\AutoEmailSender\\backups\\schema",
    suggestedActions: ["安装 2.4.0 或更高版本继续使用", "如需回退，请从升级前备份恢复数据库"],
  },
});
```

- [ ] **步骤 2：运行测试验证失败**

运行：`cd frontend; npm run test:dom -- client.test.ts`

预期：失败，仍抛出通用「系统准备失败」。

- [ ] **步骤 3：实现专用错误信息**

把 `getDesktopBackendStartupErrorMessage(statusMessage?: string)` 改为接收 `DesktopBackendStatus`。如果 `status.state === "error" && status.databaseError?.code === "DATABASE_REQUIRES_NEWER_APP"`，返回三行消息：

```typescript
return [
  `当前数据需要 AutoEmailSender ${status.databaseError.minimumSupportedAppVersion} 或更高版本。`,
  "请升级到新版继续使用。若必须回退旧版，请从升级前备份恢复数据库。",
  `备份位置：${status.databaseError.backupDirectory}`,
].join("\n");
```

调用点改为 `reject(new Error(getDesktopBackendStartupErrorMessage(status)))`。

- [ ] **步骤 4：运行测试和 lint**

运行：

```powershell
cd frontend
npm run test:dom -- client.test.ts
npm run lint
```

预期：两条命令均 exit 0。

- [ ] **步骤 5：Commit**

```powershell
git add frontend/src/lib/api/client.ts frontend/src/lib/api/client.test.ts
git commit -m "feat(前端): API 等待阶段提示数据库版本错误"
```

---

### 任务 9：最终验证与文档对齐

**文件：**
- 可选修改：`docs/superpowers/specs/2026-06-05-database-upgrade-compatibility-design.md`
- 可选修改：当前版本 release notes，如果发布流程已有对应文件。

- [ ] **步骤 1：运行后端验证**

运行：

```powershell
cd backend
uv run python -m unittest test.test_schema_metadata test.test_schema_backup test.test_migrations_runtime test.test_desktop_runtime test.test_startup_runtime test.test_database_schema.MigrationScriptTests
```

预期：全部 `OK`。

- [ ] **步骤 2：运行桌面验证**

运行：

```powershell
cd desktop
npm run test -- backend.test.ts
npm run typecheck
```

预期：两条命令均 exit 0。

- [ ] **步骤 3：运行前端验证**

运行：

```powershell
cd frontend
npm run test:dom -- client.test.ts DesktopStartupStatusBanner.test.tsx
npm run lint
```

预期：两条命令均 exit 0。

- [ ] **步骤 4：核对实现与规格**

逐项核对规格验收标准：

- 迁移前创建备份。
- 未来数据库版本不调用 Alembic。
- `/startup-status` 包含最低可用版本和备份目录。
- 只保留最近 5 份 schema 备份。
- 前端显示升级和恢复备份指引。

如果实现与规格不一致，优先修实现；如果规格需要澄清，只补充规格文档并单独提交。

- [ ] **步骤 5：检查最终 diff**

运行：

```powershell
git status --short
git diff --stat
```

预期：只包含本计划涉及文件；没有临时数据库、备份或脚本文件。

- [ ] **步骤 6：最终 Commit（仅当有文档补充）**

```powershell
git add docs/superpowers/specs/2026-06-05-database-upgrade-compatibility-design.md docs/releases/v2.4.0.md
git commit -m "docs(发布): 说明数据库升级回退策略"
```

如果没有文档补充，跳过此步骤。

---

## 自检清单

- 规格覆盖度：自动备份由任务 2、4 覆盖；未来版本拦截由任务 1、4、5、6、7、8 覆盖；最低可用版本由任务 1、5、6、7、8 覆盖；保留最近 5 份由任务 2 覆盖；不实现 Alembic downgrade 通过范围约束保持。
- 类型一致性：后端使用 snake_case payload，Electron 映射为 camelCase，前端只消费 camelCase。
- 验证覆盖：后端 unittest、桌面 Vitest/typecheck、前端 Vitest/lint 均列出。