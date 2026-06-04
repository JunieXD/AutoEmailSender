# 数据库升级与版本回退兼容设计

## 背景

AutoEmailSender 使用本地 SQLite 数据库存储运行数据，并通过 Alembic 在后端启动时执行 schema 迁移。当前启动流程会直接调用 `alembic upgrade head`。如果用户先运行新版，数据库被迁移到新版 revision，随后又安装并启动旧版，旧版 Alembic 迁移文件可能无法识别数据库中的新版 revision，导致后端运行时初始化失败。

这个问题本质上是本地数据格式升级后的回退兼容问题。SQLite schema 迁移默认是向前升级，旧版程序不一定能读取新版 schema，也不应该在未知数据结构上继续运行。

## 目标

- 在数据库向前迁移前自动备份用户本地数据库。
- 明确产品策略：不支持直接降级数据库，但支持从升级前备份恢复。
- 旧版遇到新版数据库时，不暴露 Alembic 原始错误，而是返回用户可理解的启动错误。
- 显示当前数据库需要的最低可用应用版本。
- 保留最近 5 份 schema 备份。
- 不实现面向用户的 Alembic downgrade。

## 非目标

- 不承诺旧版本能直接打开新版数据库。
- 不提供自动 downgrade migration。
- 第一阶段不提供前端「一键恢复备份」能力。
- 不把 Alembic revision 直接作为用户可理解的兼容版本。

## 产品策略

AutoEmailSender 的数据库兼容策略如下：

- 新版本可以自动向前迁移旧数据库。
- 一旦数据库完成 schema 升级，旧版本不保证能直接使用该数据库。
- 新版本迁移前必须创建升级前备份。
- 用户如需回退旧版，应恢复升级前备份，而不是直接用旧版打开已升级数据库。
- 启动时如果发现数据库由未来版本创建，后端应进入明确的启动错误状态，并提示最低可用版本和备份目录。

「最低可用版本」定义为：能够读取当前数据库 schema 的最低应用版本。它通常是最近一次改变数据库结构的版本，也可以是后续专门兼容该 schema 的版本。

## 数据模型

新增应用级元信息表，用于记录数据库 schema 与应用版本的兼容边界。该表不替代 Alembic 的 `alembic_version`，而是补充面向产品逻辑的版本信息。

建议表结构：

```text
app_metadata
- key: string, primary key
- value: string
```

至少记录以下键值：

| Key | 说明 |
| --- | --- |
| schema_version | 应用级 schema 版本，使用递增整数。 |
| schema_revision | 当前 Alembic revision，用于诊断。 |
| schema_updated_by_app_version | 最近一次完成 schema 更新的应用版本。 |
| minimum_supported_app_version | 能读取当前 schema 的最低应用版本。 |
| schema_updated_at | 最近一次 schema 更新完成时间，使用 ISO 8601。 |

schema_version 用于代码里的兼容性判断，schema_revision 用于排查迁移链路问题。面向用户的提示应使用 minimum_supported_app_version。

## 启动流程

后端启动时的数据库初始化流程调整为：

1. 判断 SQLite 数据库文件是否存在。
2. 如果数据库不存在，正常创建数据库并执行迁移。
3. 如果数据库存在，先读取 `app_metadata`。
4. 如果 `minimum_supported_app_version` 高于当前应用版本：
   - 不执行 Alembic migration。
   - 不启动 runtime workers。
   - 设置 startup status 为 `error`。
   - 返回结构化错误，包含当前应用版本、最低可用版本和备份目录。
5. 如果数据库兼容当前应用版本，则在迁移前创建 schema 备份。
6. 执行 `alembic upgrade head`。
7. 写入或更新 `app_metadata`。
8. 清理旧 schema 备份，只保留最近 5 份。
9. 继续执行运行时清理和后台任务启动。

该流程的关键约束是：如果数据库来自未来版本，必须在调用 Alembic 前停止。这样可以避免旧版 Alembic 因不认识新版 revision 而抛出内部错误。

## 备份策略

迁移前备份只覆盖主 SQLite 数据库文件。备份目录建议为：

```text
data/backups/schema/
```

备份文件命名建议：

```text
auto_email_sender.before-{app_version}.{timestamp}.db
```

示例：

```text
auto_email_sender.before-2.4.0.20260605-143012.db
```

每个 `.db` 备份旁边写入一个同名 `.json` 元信息文件，例如：

```json
{
  "created_at": "2026-06-05T14:30:12+08:00",
  "app_version": "2.4.0",
  "database_path": "C:\\Users\\Alice\\AppData\\Roaming\\AutoEmailSender\\auto_email_sender.db",
  "reason": "before_schema_migration",
  "source_schema_revision": "04d66ff4c25b",
  "target_schema_revision": "9a7c5e3d2b1f"
}
```

备份保留策略：

- 只保留最近 5 份 schema 备份。
- `.db` 与对应 `.json` 作为一组清理。
- 清理依据优先使用元信息中的 `created_at`，缺失时退回到文件修改时间。

如果备份失败，应停止启动并返回错误，不继续执行数据库迁移。原因是备份是用户回退的唯一可靠路径，迁移前不能静默跳过。

## 错误提示

后端应返回结构化启动错误，供前端展示友好提示。建议错误码为：

```text
DATABASE_REQUIRES_NEWER_APP
```

示例响应：

```json
{
  "code": "DATABASE_REQUIRES_NEWER_APP",
  "message": "当前数据由较新版本创建，当前版本无法直接打开。",
  "current_app_version": "2.3.0",
  "minimum_supported_app_version": "2.4.0",
  "backup_directory": "C:\\Users\\Alice\\AppData\\Roaming\\AutoEmailSender\\backups\\schema",
  "suggested_actions": [
    "安装 2.4.0 或更高版本继续使用",
    "如需回退，请从升级前备份恢复数据库"
  ]
}
```

前端第一阶段只展示提示和备份路径，不提供自动恢复按钮。推荐文案：

```text
当前数据需要 AutoEmailSender 2.4.0 或更高版本。
请升级到新版继续使用。若必须回退旧版，请从升级前备份恢复数据库。

备份位置：
C:\Users\Alice\AppData\Roaming\AutoEmailSender\backups\schema
```

## 版本维护规则

每次新增数据库迁移时，开发者需要明确维护应用级 schema 信息：

```text
schema_version = 17
minimum_supported_app_version = 2.4.0
```

规则如下：

- 有 schema 迁移时，通常递增 `schema_version`。
- 没有 schema 迁移的版本，不提高 `schema_version`，也不改变 `minimum_supported_app_version`。
- 如果某个版本没有迁移，但专门兼容了已有 schema，可以把 `minimum_supported_app_version` 设为该兼容版本。
- release notes 中应标注包含数据库迁移，并说明回退旧版需要恢复升级前备份。

## 组件边界

建议拆分为以下后端职责单元：

| 单元 | 职责 |
| --- | --- |
| 数据库兼容检查 | 读取 `app_metadata`，判断当前应用是否能打开数据库。 |
| schema 备份服务 | 在迁移前复制 SQLite 文件、写入备份元信息、清理旧备份。 |
| migration 编排 | 串联兼容检查、备份、Alembic upgrade 和 metadata 更新。 |
| 启动错误模型 | 以结构化方式向 `/startup-status` 暴露数据库兼容错误。 |
| 前端启动提示 | 展示最低可用版本、建议操作和备份目录。 |

这些单元应保持独立，避免把备份、版本判断和 Alembic 调用混在一个难以测试的函数里。

## 测试范围

后端测试应覆盖：

- 新数据库首次启动后会创建并写入 `app_metadata`。
- 缺少 `app_metadata` 的旧数据库可以正常迁移，并补写元信息。
- 数据库要求的最低版本高于当前应用版本时，不调用 Alembic。
- 未来数据库版本会让 startup status 进入 `error`，并返回结构化错误内容。
- 兼容数据库在迁移前会生成 `.db` 和 `.json` 备份。
- 备份失败时不会执行 Alembic migration。
- schema 备份只保留最近 5 份。
- `/startup-status` 返回的错误包含最低可用版本和备份目录。

前端测试应覆盖：

- `DATABASE_REQUIRES_NEWER_APP` 能展示专用错误文案。
- 错误页面能显示最低可用版本和备份目录。
- 普通启动错误仍沿用现有错误展示，不被误判为数据库版本问题。

## 风险与取舍

该设计不解决「旧版直接打开新版数据库」的问题，而是明确拒绝该场景，并提供恢复路径。这比自动 downgrade 更可靠，因为新版产生的数据不一定能用旧 schema 表达。

自动备份会增加少量磁盘占用，但保留最近 5 份可以控制增长。相比用户升级后无法回退数据，这个成本可以接受。

第一阶段不提供恢复按钮，用户需要手动恢复数据库文件。这样可以先降低实现风险，等备份和错误提示稳定后，再考虑提供受控的前端恢复入口。

## 验收标准

- 用户升级到包含 schema 迁移的新版本前，系统会自动创建升级前数据库备份。
- 用户回退旧版本并打开新版数据库时，看到的是明确的版本不兼容提示，而不是 Alembic 原始错误。
- 提示中包含最低可用应用版本和 schema 备份目录。
- 后端不会在未来数据库版本上继续执行 Alembic migration。
- schema 备份目录最多保留最近 5 份备份。
- 项目文档或 release notes 能说明数据库升级后的回退方式。
