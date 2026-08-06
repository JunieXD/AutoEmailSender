# 依赖规则与门禁基线

## 1. 总则

- 依赖必须指向更稳定、更底层的抽象。
- 组合根可以知道所有模块，业务模块不能反向知道组合根。
- 跨模块访问使用 `public.py`、`index.ts`、消息或版本化合同。
- 兼容 shim 可以从旧路径导入新模块，但必须是纯 re-export，并在门禁中显式登记。
- 门禁例外是待偿还技术债；删除例外是进展，增加例外需要先更新架构文档并说明退出批次。

## 2. Backend 当前层级门禁

禁止方向：

- `core -> api|services|schemas|agents|modules`
- `models -> api|services|schemas|agents`
- `schemas -> api|services|agents`
- `services -> api|agents`
- 不同 `modules/<domain>` 之间进行非 `public` 深层导入
- 新 `modules` 导入旧 `app.api`

第 1 批冻结以下现有例外，不允许增加：

| 来源 | 目标 |
|---|---|
| `app/core/agent_mutation_headers.py` | `app.services.agent_mutations` |
| `app/schemas/crawl_job.py` | `app.services.crawler_tools` |
| `app/schemas/crawl_job.py` | `app.services.crawler_v2_url_utils` |
| `app/schemas/crawl_job.py` | `app.services.professor_field_normalization` |
| `app/schemas/professor.py` | `app.services.professor_field_normalization` |
| `app/services/agent_drafts.py` | `app.api.workspace_support` |
| `app/services/crawl_job_runtime.py` | `app.agents.faculty_crawler_agent` |
| `app/services/test_compose_runtime.py` | `app.api.identity_serializers` |

ORM 模型为完成 SQLAlchemy registry 而产生的模型内部关系暂不作为第 1 批失败条件；后续在领域模型迁移时单独治理。

### 已落地的 system 领域入口

- 领域外代码只能经 `app.modules.system.public` 使用 runtime-settings 能力。
- `app.modules.system.runtime_settings.public` 是 system 领域内的切片门面，由领域入口转发。
- `app.modules.system.runtime_settings.api` 只由组合根注册，不作为业务调用入口。
- 旧 `app.api.runtime_settings`、`app.schemas.runtime_settings`、
  `app.services.runtime_settings`、`app.services.system_settings` 仅作兼容 re-export；
  新代码不得继续引用这些路径。

## 3. Frontend 渐进门禁

目标依赖方向：

```text
app -> pages -> widgets -> features -> entities -> shared
```

立即禁止：

- `shared` 向 `entities/features/widgets/pages/app` 反向导入。
- `entities` 向 `features/widgets/pages/app` 导入。
- `features` 向 `widgets/pages/app` 导入。
- 不同 feature slice 之间深层导入。
- 旧 `lib`、`lib/api`、`context`、`components/atoms` 新增向上依赖。

第 1 批冻结以下现有例外：

| 来源 | 目标 |
|---|---|
| `context/BackgroundTaskNotificationContext.tsx` | `features/crawl-review/client/crawlJobEvents.ts` |
| `context/NotificationContext.tsx` | `components/organisms/NotificationViewport.tsx` |
| `lib/api/createTask.ts` | `features/create-task/types.ts` |
| `lib/api/tokenUsage.ts` | `features/token-usage/client/tokenUsage.ts` |
| `lib/useConfirmDialog.tsx` | `components/atoms/ConfirmDialog.tsx` |

旧 `components/molecules|organisms` 包含混合职责，本批不为它们定义全局层级；在迁入新 slice 时由目标层规则接管。

## 4. CLI 门禁

- `transport` 只能依赖 `protocol` 或自身。
- `protocol` 不能依赖 `commands`、`bootstrap` 或 `transport`。
- `catalog` 可以依赖 `protocol`，不能依赖具体命令实现。
- `commands` 可以依赖 `transport`、`protocol`、`catalog` 和命令共享基础。
- `bootstrap` 是唯一可以聚合全部命令的组合根。
- CLI 任何模块都不得导入 `backend/app` 或直接连接数据库。

当前尚未迁移的 `commands/common.py` 作为已知聚合热点保留，但禁止非命令模块新增对它的依赖。

## 5. Desktop 门禁

- 除薄入口外，源码不能导入 `main.ts` 或 `preload.ts`。
- `preload.ts` 只能依赖 IPC 合同，不得导入 main-process 服务。
- main-process 服务不能导入 preload。
- `src` 内部静态 import 图不得出现循环。
- IPC channel、请求和响应类型最终必须来自单一合同源。

## 6. 门禁实现要求

- 测试必须输出完整的 `source -> target` 违规边，便于直接定位。
- 当前例外使用精确文件边，不使用目录级通配豁免。
- 例外消失时测试应提示更新基线，防止已偿还技术债悄悄重新出现。
- 门禁代码本身不得读取 `node_modules`、`.venv`、构建产物或发布目录。
- 路径比较统一使用仓库相对 POSIX 路径，保证 Windows、macOS 和 Linux 一致。
