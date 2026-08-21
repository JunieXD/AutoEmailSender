# 依赖规则与门禁基线

## 1. 总则

- 依赖必须指向更稳定、更底层的抽象。
- 组合根可以知道所有模块，业务模块不能反向知道组合根。
- 跨模块访问使用 `public.py`、`index.ts`、消息或版本化合同。
- 兼容 shim 只允许在明确的迁移批次内临时存在，必须是纯 re-export、在门禁中登记，并在调用方
  迁移完成后删除；Backend 技术层当前禁止重新引入指向领域模块的纯 re-export 文件。
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
- 迁移期 runtime-settings 技术层 shim 已在第 9 批删除；不得恢复旧入口。

### 已落地的 identities 领域入口

- 领域外代码只能经 `app.modules.identities.public` 使用通信组能力。
- `app.modules.identities.communication_groups.public` 是 identities 领域内的切片门面。
- `app.modules.identities.communication_groups.api` 只由组合根注册，不作为业务调用入口。
- 迁移期通信组技术层 shim 已在第 9 批删除；不得恢复旧入口。
- `app.schemas` 使用懒加载保留三个通信组 DTO 的历史聚合导出，禁止在该聚合入口
  重新加入对 identities 公共门面的急加载，否则会与 `ApiSchema` 形成初始化环。

### 已落地的 campaigns 领域入口

- 领域外代码只能经 `app.modules.campaigns.public` 使用 campaign、batch 规则和 outreach template 能力。
- `app.modules.campaigns.templates.api` 只由组合根注册，不作为业务调用入口。
- `campaigns.public` 对依赖 identities 的 resend 和依赖 Agent schema 的 Agent 用例按需导出；
  低层模板、排期、状态和 DTO 使用者不得被迫加载高层 Agent adapter。
- 迁移期 campaign、batch 与 outreach-template 技术层 shim 已在第 9 批删除；不得恢复旧入口。

### 已落地的 communications 领域入口

- 领域外代码只能经 `app.modules.communications.public` 使用邮件传输、IMAP 状态、历史投影与
  test-compose 能力；`test_compose.api` 只由组合根注册。
- test-compose DTO/application 用例按需导出，SMTP/IMAP 基础调用不得隐式加载 identities、
  campaigns 或 LLM 高层模块。
- IMAP 同步、历史扫描与回复检测由 `app.modules.communications.imap.sync` 拥有；领域外调用方只能经
  `app.modules.communications.public` 调用，communications 不得反向导入 workspace/campaigns runtime。
- 迁移期 communications、IMAP 与 test-compose 技术层 shim 已在第 9 批删除；领域外调用必须经
  `app.modules.communications.public`。

### 已落地的 workspace 领域入口

- 领域外代码只能经 `app.modules.workspace.public` 使用 workspace thread、email-task 状态机与
  delivery 能力；workspace 与 email-task UI router 只由组合根直接注册。
- `tasks.runtime` 拥有草稿生成/改写、审核、手动继续和跟进状态机；`tasks.delivery` 拥有到期选择、
  身份发送窗口、发送恢复和 SMTP 提交。两者只使用域内相对导入协作。
- workspace 只能经 `campaigns.public`、`communications.public`、`identities.public`、
  `matching.public`、`llm.public` 与其他领域协作；communications 不得反向依赖 workspace。
- 迁移期 workspace、email-task 与 task-runtime 技术层 shim 已在第 9 批删除；不得恢复旧入口。
- batch HTTP adapter 与 draft claim/recovery worker 分别由
  `app.modules.campaigns.batch_tasks.api`、`app.modules.campaigns.drafts.runtime` 拥有；worker 仅经
  `workspace.public` 调用单封任务用例，领域外 worker 调用方仅经 `campaigns.public`。
- 迁移期 batch adapter 与 draft worker 技术层 shim 已在第 9 批删除；不得恢复旧入口。

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

当前 owner 约束：

- 应用级 Provider 位于 `app/providers`；`app/` 可编排 feature、entity 与旧 context，但不得把业务规则
  重新收回组合层。
- professor 与 community-mentor API 由对应 `entities/*/api` 拥有；迁移期 `lib/api` 实体 re-export
  已在第 9 批删除。实体专属 session cache 与其实体 API 同域放置。

第 1 批冻结以下现有例外：

| 来源 | 目标 |
|---|---|
| `context/NotificationContext.tsx` | `components/organisms/NotificationViewport.tsx` |
| `lib/api/tokenUsage.ts` | `features/token-usage/client/tokenUsage.ts` |
| `lib/useConfirmDialog.tsx` | `components/atoms/ConfirmDialog.tsx` |

旧 `components/molecules|organisms` 包含混合职责，本批不为它们定义全局层级；在迁入新 slice 时由目标层规则接管。

## 4. CLI 门禁

- `transport` 只能依赖 `protocol` 或自身。
- `protocol` 不能依赖 `commands`、`bootstrap` 或 `transport`。
- `catalog` 可以依赖 `protocol`，不能依赖具体命令实现。
- `commands` 可以依赖 `transport`、`protocol`、`catalog` 和命令共享基础。
- `invocation` 只能依赖 `catalog` 与 `protocol`，命令树必须由 `bootstrap` 注入，不得直接聚合
  `commands` 或调用 `transport`。
- `installation` 只能依赖 `protocol`；其余根级辅助模块只能互相依赖或使用 `protocol`。
- `bootstrap` 是唯一可以聚合全部命令的组合根。
- CLI 任何模块都不得导入 `backend/app` 或直接连接数据库。

当前尚未迁移的 `commands/common.py` 作为已知聚合热点保留，但禁止非命令模块新增对它的依赖。

## 5. Desktop 门禁

- 除薄入口外，源码不能导入 `main.ts` 或 `preload.ts`。
- `preload.ts` 只能依赖 `preload/` 实现和 IPC 合同；preload 实现不得导入 main-process 服务。
- main-process 服务不能导入 preload。
- `src` 内部静态 import 图不得出现循环。
- renderer 可见 DTO/bridge 只能来自 `contracts/desktop-ipc.d.ts`；Desktop 内部 backend 类型由
  `main/backend/types.ts` 拥有，不得重新混入跨进程合同。
- IPC channel 只能来自 `src/contracts/channels.ts`，main/preload/service 不得内联业务 channel。
- main-process service 必须位于 `main/{backend,agent-support,updates,files,shell}`；第 9 批已删除根同名
  兼容模块，`src/` 根只能保留稳定的 `main.ts`、`preload.ts` 两个进程入口，结构门禁禁止重新引入。

## 6. 门禁实现要求

- 测试必须输出完整的 `source -> target` 违规边，便于直接定位。
- 当前例外使用精确文件边，不使用目录级通配豁免。
- 例外消失时测试应提示更新基线，防止已偿还技术债悄悄重新出现。
- 门禁代码本身不得读取 `node_modules`、`.venv`、构建产物或发布目录。
- 路径比较统一使用仓库相对 POSIX 路径，保证 Windows、macOS 和 Linux 一致。
