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

ORM 模型为完成 SQLAlchemy registry 而产生的模型内部关系暂不作为第 1 批失败条件；后续在领域模型迁移时单独治理。

### 已落地的 system 领域入口

- 领域外代码只能经 `app.modules.system.public` 使用 runtime-settings 能力。
- `app.modules.system.runtime_settings.public` 是 system 领域内的切片门面，由领域入口转发。
- `app.modules.system.runtime_settings.api` 只由组合根注册，不作为业务调用入口。
- 旧 `app.api.runtime_settings`、`app.schemas.runtime_settings`、
  `app.services.runtime_settings`、`app.services.system_settings` 仅作兼容 re-export；
  新代码不得继续引用这些路径。

### 已落地的 identities 领域入口

- 领域外代码只能经 `app.modules.identities.public` 使用通信组能力。
- `app.modules.identities.communication_groups.public` 是 identities 领域内的切片门面。
- `app.modules.identities.communication_groups.api` 只由组合根注册，不作为业务调用入口。
- 旧 `app.api.communication_groups`、`app.schemas.communication_group`、
  `app.services.communication_group_mutations`、
  `app.services.identity_communication_groups` 仅作兼容 re-export；新代码不得引用。
- `app.schemas` 使用懒加载保留三个通信组 DTO 的历史聚合导出，禁止在该聚合入口
  重新加入对 identities 公共门面的急加载，否则会与 `ApiSchema` 形成初始化环。

### 已落地的 campaigns 领域入口

- 领域外代码只能经 `app.modules.campaigns.public` 使用 campaign、batch 规则和 outreach template 能力。
- `app.modules.campaigns.templates.api` 只由组合根注册，不作为业务调用入口。
- `campaigns.public` 对依赖 identities 的 resend 和依赖 Agent schema 的 Agent 用例按需导出；
  低层模板、排期、状态和 DTO 使用者不得被迫加载高层 Agent adapter。
- 旧 `app.api.outreach_templates`、`app.schemas.batch_task`、
  `app.schemas.outreach_template` 及对应 `app.services.agent_campaigns|batch_*|outreach_*`
  路径仅作兼容 re-export；新生产代码不得引用。

### 已落地的 communications 领域入口

- 领域外代码只能经 `app.modules.communications.public` 使用邮件传输、IMAP 状态、历史投影与
  test-compose 能力；`test_compose.api` 只由组合根注册。
- test-compose DTO/application 用例按需导出，SMTP/IMAP 基础调用不得隐式加载 identities、
  campaigns 或 LLM 高层模块。
- IMAP 同步、历史扫描与回复检测由 `app.modules.communications.imap.sync` 拥有；领域外调用方只能经
  `app.modules.communications.public` 调用，communications 不得反向导入 workspace/campaigns runtime。
- `app.services.task_runtime` 仅为原同步公开符号提供对象级兼容别名；生产代码不得再从该路径调用
  IMAP 同步或回复识别能力。
- 旧 `app.api.test_compose`、`app.schemas.test_compose` 及
  `app.services.mail_runtime|imap_*|email_*|communication_events|smtp_error_explanations|test_compose_runtime`
  仅作兼容 re-export；新生产代码不得引用。

### 已落地的 workspace 领域入口

- 领域外代码只能经 `app.modules.workspace.public` 使用 workspace thread、email-task 状态机与
  delivery 能力；workspace 与 email-task UI router 只由组合根直接注册。
- `tasks.runtime` 拥有草稿生成/改写、审核、手动继续和跟进状态机；`tasks.delivery` 拥有到期选择、
  身份发送窗口、发送恢复和 SMTP 提交。两者只使用域内相对导入协作。
- workspace 只能经 `campaigns.public`、`communications.public`、`identities.public`、
  `matching.public`、`llm.public` 与其他领域协作；communications 不得反向依赖 workspace。
- 旧 `app.api.email_tasks|workspaces|workspace_support`、`app.schemas.email_task|workspace` 和
  `app.services.task_runtime` 仅作兼容 re-export；生产代码不得引用。兼容入口由第 9 批统一审计清理。

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
