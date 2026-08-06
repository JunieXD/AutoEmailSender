# 按领域模块化重构总计划

状态：已确认，第 6A 批已完成，第 6B 批待开始
建立日期：2026-08-06
适用范围：`backend/`、`frontend/`、`desktop/`、`cli/`、`website/` 及其构建、测试和分发资源

## 1. 目标

本计划把当前以技术层和文件大小为主的组织方式，渐进调整为高内聚、低耦合、按领域或用户能力组织的模块结构，使人类和 AI 都能在较小且明确的上下文内安全修改代码。

成功标准：

- 一个业务能力的 API、应用逻辑、数据结构和测试能够在同一领域范围内定位。
- 组合根只负责装配，不保存业务规则。
- 跨领域依赖只能经过显式公共入口或合同。
- 前端页面只负责编排，不继续承载大块业务状态与规则。
- HTTP、Agent API 和 Desktop IPC 合同都有单一事实来源或自动漂移检查。
- 每次文件移动都能独立构建、测试和回滚。

## 2. 不变量

重构期间必须保持以下行为不变：

- 不改变现有 HTTP 路径、请求/响应字段和状态码。
- 不改变 Agent API 的权限、安全 DTO、确认、幂等和审计语义。
- CLI 继续只通过 Agent API 操作应用，不直接访问 SQLite。
- 不改变数据库 schema；需要 schema 变化时另开功能批次并提供 Alembic revision。
- 不改变 Electron 分发资源相对路径，除非该批显式包含完整打包验证。
- 保留 `backend/`、`frontend/`、`desktop/`、`cli/`、`website/` 顶层工作区及独立锁文件。
- Alembic revision 保持单一时间链，不按领域拆分目录。

## 3. 目标拓扑

### 3.1 仓库根目录

```text
AutoEmailSender/
├── backend/
├── frontend/
├── desktop/
├── cli/
├── website/
├── contracts/                 # 跨进程、机器可读、可生成或校验的合同
├── agent-support/             # 产品分发用 Agent 资源
├── config/                    # 真正跨工作区的静态配置
├── scripts/
│   ├── build/
│   ├── packaging/
│   ├── quality/
│   ├── data/
│   └── release/
└── docs/
    ├── architecture/
    ├── product/
    ├── development/
    ├── operations/
    ├── releases/
    └── archive/
```

根目录整理安排在领域代码稳定后执行。为了目录美观而整体迁移到 `apps/` 不在本计划范围内。

### 3.2 后端

```text
backend/app/
├── bootstrap/                 # create_app、lifespan、router registry
├── modules/
│   ├── identities/
│   ├── professors/
│   ├── community/
│   ├── campaigns/
│   ├── communications/
│   ├── workspace/
│   ├── matching/
│   ├── crawler/
│   ├── llm/
│   ├── automation/
│   ├── reporting/
│   └── system/
├── platform/                  # 数据库、文件系统、HTTP、可观测性等实现
└── shared/                    # 无领域含义、无反向依赖的小型基础能力
```

大型领域可逐步采用以下内部结构；小领域不创建空层：

```text
modules/<domain>/
├── public.py
├── domain/
├── application/
├── infrastructure/
└── presentation/http/
    ├── ui.py
    ├── agent_v1.py
    └── schemas.py
```

第一轮迁移以“先按所有权聚合，再按需要拆层”为原则，不在文件移动批次中强制重写 ORM 或领域模型。

### 3.3 前端

```text
frontend/src/
├── main.tsx
├── app/                       # router、providers、layouts、styles
├── pages/                     # 薄路由页面
├── widgets/                   # 页面级复合区块
├── features/                  # 用户动作
├── entities/                  # 业务实体
└── shared/                    # api、platform、ui、lib、hooks、config
```

目标依赖方向：

```text
app -> pages -> widgets -> features -> entities -> shared
```

原有 `components/atoms|molecules|organisms` 按业务所有权逐步迁移，不进行一次性大搬家。真正通用的组件进入 `shared/ui`，领域组件进入对应 `entities`、`features` 或 `widgets`。

### 3.4 Desktop

```text
desktop/src/
├── main.ts                    # 薄组合入口
├── preload.ts                 # 薄桥接入口
├── main/
│   ├── bootstrap/
│   ├── ipc/
│   ├── backend/
│   ├── agent-support/
│   ├── updates/
│   ├── files/
│   └── shell/
├── preload/
└── contracts/
```

生产构建与测试 TypeScript 配置最终分离，测试文件不进入生产输出树。

### 3.5 CLI

```text
cli/src/auto_email_sender_cli/
├── bootstrap/
├── commands/<resource>/
├── transport/
├── protocol/
├── catalog/
└── invocation/
```

`catalog` 是命令合同、能力、风险和说明的单一注册源；`commands` 只做参数绑定与调用编排，不保存产品业务真相。

## 4. 批次计划

| 批次 | 范围 | 状态 | 完成条件 |
|---|---|---|---|
| 1 | 架构文档、现状依赖基线、backend/frontend/CLI/desktop 导入边界门禁 | 已完成 | 四个工作区门禁及完整验证通过 |
| 2 | `backend/app/modules/system/runtime_settings` 首个纵向切片 | 已完成 | 旧导入兼容、API 合同不变、后端与相关 CLI/前端测试通过 |
| 3 | `identities`：身份、材料、通信组 | 已完成（3A～3C） | 每个子切片独立迁移并全绿 |
| 4 | `professors` 与 `community`：导师、标签、补全、社区库 | 已完成（4A～4D） | UI/Agent 路由和前端实体边界完成 |
| 5 | `matching` 与 `llm` | 已完成 | 解除现有 LLM adaptation 循环或记录剩余边界 |
| 6 | `crawler` | 执行中（6A 已完成，6B 待开始） | worker 调度、Agent 适配器和持久化边界明确 |
| 7 | `campaigns`、`communications`、`workspace` | 待开始 | 任务、草稿、发送、收信的依赖方向单向化 |
| 8 | Desktop 进程模块化与 IPC 合同收敛 | 待开始 | main/preload 薄入口、类型单一来源 |
| 9 | 测试拓扑、脚本分类、文档归档和确认后的遗留清理 | 待开始 | 构建与发布路径全部验证 |

批次可以继续拆成更小提交，但不得把两个互不相关的领域迁移混在同一提交中。

## 5. 单批执行协议

每一批严格按以下顺序执行：

1. 用 CodeGraph 和只读检查确认调用方、被调用方、测试和打包路径。
2. 在本计划中把本批状态改为“执行中”，写明精确文件范围。
3. 先建立目标目录和公共入口。
4. 使用兼容 re-export 或薄委托保留旧导入路径。
5. 使用 `git mv` 或小步补丁迁移；不顺带改业务逻辑。
6. 运行定向测试。
7. 运行受影响工作区的完整测试、lint/typecheck 和 build。
8. 检查 `git diff`、新增反向依赖及构建产物路径。
9. 更新计划状态、剩余技术债和验证结果后，才开始下一批。

兼容入口要求：

- 只允许导入和 re-export，不新增业务逻辑。
- 必须在依赖门禁中显式识别。
- 对外导入全部迁移完成后，在独立清理批次删除。

## 6. 验证矩阵

### Backend

```text
cd backend
uv run python -m unittest discover test
```

### Frontend

```text
cd frontend
npm run lint
npm run test
npm run build
```

### CLI

```text
cd cli
uv run python -m unittest discover test
```

涉及 CLI 分发时追加根目录 `scripts/build-cli.*` 和二进制验证。

### Desktop

```text
cd desktop
npm run typecheck
npm run test
npm run build
```

### Website 与发布链

只有触及对应路径时执行 website build/test 和 packaging/release 脚本测试；任何打包路径变化都必须执行项目指南列出的完整 release 测试矩阵。

## 7. 停止条件

出现以下任一情况必须停止当前批次，不得用扩大改动范围掩盖问题：

- API、CLI 或 IPC 合同发生非预期变化。
- 需要数据库迁移才能继续。
- 必须同时重写多个领域的业务逻辑才能通过测试。
- 新增循环依赖或新增门禁例外。
- 完整测试失败且无法证明与本批无关。
- 打包资源路径无法通过现有测试验证。

## 8. 第 1 批交付物

- 本目录中的计划、模块地图和依赖规则。
- Backend AST 导入边界测试，冻结当前反向依赖清单。
- Frontend import 边界测试，冻结现有低层向高层依赖和跨 feature 依赖。
- CLI import 边界测试，约束 transport/protocol/catalog/commands 的方向。
- Desktop import 边界测试，保护 main/preload 进程边界并禁止循环依赖。
- 四个工作区的定向和完整验证结果。

## 9. 执行记录

### 第 1 批：架构基线与边界门禁（已完成）

完成日期：2026-08-06

交付范围：

- 建立本目录中的总计划、模块地图、依赖规则与索引。
- 增加 Backend、Frontend、CLI、Desktop 导入边界测试。
- 将现存反向依赖冻结为精确文件边；新增违规会失败，已消失的例外会提示收紧基线。
- 将 Frontend 架构测试纳入现有 Vitest 配置。

验证结果：

| 工作区 | 验证 | 结果 |
|---|---|---|
| Backend | 架构门禁；完整 unittest | 通过：完整套件 1695 passed，1 skipped |
| Frontend | lint；完整 Vitest；production build | 通过：115 files，899 tests |
| CLI | 架构门禁；完整 unittest | 通过：完整套件 153 passed |
| Desktop | 架构门禁；typecheck；完整 Vitest；build | 通过：29 files，246 tests |
| Repository | `git diff --check` | 通过 |

### 第 2 批：`system/runtime-settings` 首个纵向切片（已完成）

完成日期：2026-08-06

计划文件范围：

- 新增领域入口 `backend/app/modules/system/public.py`，以及切片目录 `backend/app/modules/system/runtime_settings/{__init__.py,api.py,schemas.py,service.py,public.py}`。
- 将 `backend/app/api/runtime_settings.py`、`backend/app/schemas/runtime_settings.py`、`backend/app/services/runtime_settings.py` 中归属该切片的实现迁入新模块。
- 旧路径仅保留纯兼容 re-export；组合根、Agent API、CLI 与 Frontend 调用方只在确有必要时更新导入，不改变合同或行为。
- `backend/app/services/system_settings.py` 先经调用图确认所有权；只有与 runtime settings 不可分割的实现才纳入本批。

本批不变量：

- `/api/runtime-settings` 路径、DTO 字段、状态码保持不变。
- Agent `/settings`、CLI settings 命令及 Frontend runtime-settings 行为保持不变。
- 不改变数据库 schema、持久化语义或启动/打包路径。
- 新模块不依赖旧 `app.api`，不增加任何门禁例外。

实际结果：

- runtime-settings 的 schema、服务、持久化初始化和 UI HTTP adapter 已迁入
  `backend/app/modules/system/runtime_settings/`。
- `backend/app/modules/system/public.py` 成为领域外唯一稳定入口；Agent API 和五个
  后端运行时调用方已改用该入口。
- `app.api.runtime_settings`、`app.schemas.runtime_settings`、
  `app.services.runtime_settings`、`app.services.system_settings` 保留为纯兼容导出，
  并由对象一致性测试保护。
- 组合根直接注册新模块 router；`/api/runtime-settings` 与 Agent `/settings` 的路径、
  DTO、状态码、revision、幂等及操作日志语义保持不变。
- 未修改 Alembic、ORM schema、Frontend/CLI 合同、锁文件或打包资源路径；未新增门禁例外。

验证结果：

| 范围 | 验证 | 结果 |
|---|---|---|
| Backend 定向 | 架构门禁、兼容导出、runtime settings API、并发初始化、Agent settings | 14 tests passed |
| Backend 受影响运行时 | Agent settings、runtime manager、crawler scheduler/runtime | 96 tests passed |
| Backend 完整套件 | `uv run python -m unittest discover test` | Ran 1698 tests；OK（1 skipped）；packaged document/runtime self-check 通过 |
| CLI 合同 | settings update 合并与 Agent 路由用例 | 1 test passed |
| Frontend 合同 | `OtherSettingsCard` 读取、更新及兼容 payload | 8 tests passed |
| Repository | CodeGraph 同步；`git diff --check` | 通过 |

第 2 批结束时设置的停止条件已经满足：CodeGraph 已将第一个 identities 子切片限定为
通信组；身份主体与材料没有混入第 3A 批。

### 第 3A 批：`identities/communication-groups`（已完成）

开始日期：2026-08-06
完成日期：2026-08-06

选择依据：

- 通信组已有独立 UI HTTP 路由、DTO、Agent API 合同和专项生命周期测试。
- 核心实现集中在两个服务文件中，可在不移动 ORM 模型、不改变数据库 schema 的前提下迁移。
- `materials` 候选的删除与默认材料逻辑直接耦合任务、批量任务、匹配记录和文件系统，
  不适合作为 identities 的首个低风险切片。

计划目标拓扑：

```text
backend/app/modules/identities/
├── __init__.py
├── public.py
└── communication_groups/
    ├── __init__.py
    ├── api.py
    ├── schemas.py
    ├── service.py
    ├── scope.py
    └── public.py
```

计划文件范围：

- 将 `backend/app/api/communication_groups.py` 的 UI HTTP adapter 迁入 `api.py`。
- 将 `backend/app/schemas/communication_group.py` 的 DTO 原样迁入 `schemas.py`。
- 将 `backend/app/services/communication_group_mutations.py` 的查询、变更和序列化逻辑迁入 `service.py`。
- 将 `backend/app/services/identity_communication_groups.py` 的通信范围解析和身份删除清理逻辑迁入 `scope.py`。
- 新增 identities 领域与切片公共入口；Agent API、身份删除及其他通信范围调用方改走
  `app.modules.identities.public`。
- 上述四个旧路径保留纯兼容 re-export，并增加对象一致性测试。

本批不变量：

- `/api/communication-groups` 的路径、方法、DTO、状态码和错误 detail 保持不变。
- Agent `/api/agent/v1/communication-groups` 的分页、revision、幂等和错误码保持不变。
- CLI 与 Frontend 合同及行为保持不变，不移动前端或 CLI 文件。
- 不移动 `IdentityCommunicationGroup` / `IdentityProfile` ORM 模型，不修改 Alembic 或数据库 schema。
- 不改变合并确认、匹配依据身份、操作日志或身份删除后自动解散语义。
- 新模块不依赖旧 `app.api`，不增加任何架构门禁例外。

计划验证：

1. Backend 架构门禁、兼容导出测试和 `test_identity_communication_groups.py`。
2. Agent communication-groups 管理、revision 和幂等专项用例。
3. 依赖通信范围的 workspace、dashboard、professor 与共享通信相关测试。
4. CLI communication-groups 命令/合同测试及 Frontend CommunicationSharingPanel 相关测试。
5. Backend 完整 unittest；最后运行 CodeGraph sync 和 `git diff --check`。

实际结果：

- UI adapter、DTO、CRUD/合并服务、通信范围解析和身份删除清理已经迁入
  `backend/app/modules/identities/communication_groups/`。
- `backend/app/modules/identities/public.py` 成为领域外入口；Agent API、身份删除、
  workspace、professor 和 dashboard 调用方已改用该入口。
- 原 API、schema 和两个 service 路径保留纯兼容 re-export，并由对象一致性测试保护。
- `app.schemas` 的历史聚合导出改为模块级懒加载，保留原导入合同并避免
  `ApiSchema` 初始化期间形成循环依赖。
- 组合根直接注册新模块 router；未移动 ORM、未修改 Alembic、Frontend、CLI 或锁文件，
  未新增架构门禁例外。

验证结果：

| 范围 | 验证 | 结果 |
|---|---|---|
| Backend 定向 | 架构门禁、四类兼容导出、通信组生命周期 | 11 tests passed |
| Backend 关联流程 | Agent revision/幂等、共享通信、workspace、dashboard、matching | 59 tests passed |
| Backend 完整套件 | `uv run python -m unittest discover test` | Ran 1702 tests；OK（1 skipped）；packaged document/runtime self-check 通过 |
| CLI 合同 | communication-groups 命令与 match-source 控制 | 3 tests passed |
| Frontend 合同 | CommunicationSharingPanel、SelectionContext 通知 | 2 files，13 tests passed |
| Repository | CodeGraph 同步；`git diff --check` | 通过 |

停止点：第 3B 子切片尚未选择。继续前必须分别评估身份主体与材料的依赖半径；
不得因同属 identities 就把两者合并迁移。

### 第 3B 批：`identities/profiles`（已完成）

开始日期：2026-08-06
完成日期：2026-08-06

计划目标拓扑：

```text
backend/app/modules/identities/
├── public.py
├── profiles/
│   ├── __init__.py
│   ├── api.py
│   ├── schemas.py
│   ├── serializer.py
│   └── public.py
└── materials/
    ├── __init__.py
    ├── schemas.py
    ├── serializer.py
    └── public.py
```

计划范围：

- 将 `app.api.identities` 的身份 CRUD、默认身份、连接测试和模板导入 adapter 迁入 profiles。
- 将 `app.schemas.identity` 拆为 profiles DTO 与 materials DTO；旧路径统一 re-export。
- 将 `app.api.identity_serializers` 拆为 profiles 与 materials serializer。
- 组合根和生产调用方改用 identities 公共入口；旧 API/serializer/schema 路径保留兼容。
- `app.schemas` 对身份 DTO 使用懒加载兼容，避免 `ApiSchema` 初始化环。

本批不变量：

- `/api/identities` 全部路径、方法、DTO、错误 detail、连接测试和模板导入行为不变。
- 身份删除、通信组清理、匹配记录清理、默认身份接替与操作日志语义不变。
- 只建立 materials 的 DTO/serializer 基础，不迁移上传、删除、主材料或文件系统行为。
- 不移动 ORM、不修改 Alembic、Frontend、CLI 或跨进程合同。
- 新模块不依赖旧 `app.api`，并偿还 `test_compose_runtime -> app.api.identity_serializers` 门禁例外。

计划验证：身份 API/操作日志/删除关联测试、serializer import boundary、架构门禁、
相关 Agent/Frontend 测试以及 Backend 完整 unittest。

实际结果：

- 身份 CRUD、默认身份、SMTP/IMAP 测试和模板导入 adapter 已迁入
  `backend/app/modules/identities/profiles/`；组合根直接注册新 router。
- 身份 DTO/serializer 与材料 DTO/serializer 已拆分到 profiles 和 materials 子切片，
  `backend/app/modules/identities/public.py` 暴露稳定领域入口。
- `app.api.identities`、`app.api.identity_serializers`、`app.schemas.identity` 保留为纯兼容导出，
  并由对象一致性测试保护；生产调用方不再依赖这些旧路径。
- `app.schemas` 聚合入口改为全量懒加载，在保留历史导入合同的同时消除材料 DTO 初始化环。
- 已偿还 `test_compose_runtime -> app.api.identity_serializers` 门禁例外；未移动材料生命周期、
  ORM 或 Alembic，未改变 HTTP、Agent、CLI、Frontend 或打包合同。

验证结果：

| 范围 | 验证 | 结果 |
|---|---|---|
| Backend 门禁与兼容 | 架构门禁、API import boundary、三类旧入口对象一致性与聚合 schema 懒加载 | 8 tests passed |
| Backend 定向 | 身份 API、Agent API、操作日志、身份删除/匹配与序列化关联流程 | 304 tests passed |
| Backend 完整套件 | `uv run python -m unittest discover test` | Ran 1706 tests；OK（1 skipped）；packaged document/runtime self-check 通过 |
| CLI 合同 | CLI 命令与 Agent client | 81 tests passed |
| Frontend 合同 | Profile 模板导入与 API client | 2 files，20 tests passed |
| Repository | CodeGraph 同步；生产旧路径审计；`git diff --check` | 通过 |

停止点：材料 DTO 与序列化已经稳定，但上传、删除、主材料选择、下载和文件清理行为仍在
旧 API/service 中。第 3C 必须先重新定界这些行为与任务、批量任务、匹配和 Agent 变更计划的关系。

### 第 3C 批：`identities/materials` 生命周期行为（已完成）

开始日期：2026-08-06
完成日期：2026-08-06

计划目标拓扑：

```text
backend/app/modules/identities/materials/
├── api.py
├── schemas.py
├── serializer.py
├── service.py
├── support.py
└── public.py
```

计划范围：

- 将 `app.api.materials` 的上传、设为默认、删除、打开和下载 UI adapter 迁入 materials。
- 将 `app.services.material_mutations` 的上传、默认材料、删除预览/事务及操作日志协调原样迁入
  `service.py`，不重写其跨任务、批量任务、试写会话和匹配记录的一致性算法。
- 将 `app.services.materials` 的可用性、文本提取、引用状态与下载名规则迁入 `support.py`。
- 组合根、Agent API、Agent change plan 及其他生产调用方改走 identities 公共入口；三个旧路径
  保留纯 re-export，并增加对象一致性测试。
- 文件存储、操作日志、任务/批量任务状态协调仍由现有平台/领域服务提供，本批只显式记录依赖边，
  不把它们复制进 materials。

本批不变量：

- UI 与 Agent 的材料上传、列表、打开、下载、默认选择、删除预览和确认路径/DTO/错误码不变。
- 删除事务对进行中任务的阻止，以及对安全旧引用、批量任务、试写会话和匹配记录的清理语义不变。
- 文件保存、提取、删除时机和操作日志事件名不变；不修改数据库 schema、Alembic 或存储目录。
- 不新增架构门禁例外，不在本批拆分 task/batch/matching 领域实现。

计划验证：材料 UI API 全部删除矩阵、Agent material/revision/change-plan 用例、任务/批量任务/
匹配关联测试、架构与兼容门禁、相关 CLI/Frontend 合同，以及 Backend 完整 unittest。

实际结果：

- 材料 UI adapter、生命周期事务和辅助规则已迁入
  `backend/app/modules/identities/materials/{api,service,support}.py`；组合根直接注册新 router。
- Agent API、Agent change plan、任务、批量任务、workspace/test-compose 等生产调用方已统一改走
  `app.modules.identities.public`；旧 API 和两个 service 路径只保留纯 re-export。
- 材料删除事务的阻止、预览指纹、旧引用清理、批量任务完成同步、匹配记录解绑和文件删除时机
  均保持原样；跨 campaigns/test-compose/matching 的协调边已显式保留，留待对应领域批次收敛。
- 未修改 ORM、Alembic、HTTP/Agent/CLI/Frontend 合同、锁文件或打包资源路径，未新增门禁例外。

验证结果：

| 范围 | 验证 | 结果 |
|---|---|---|
| Backend 门禁与兼容 | 架构/API import boundary、profiles/materials 旧入口对象一致性 | 11 tests passed |
| Backend 定向 | 材料 UI/Agent/change-plan、任务、批量任务、匹配与操作日志流程 | 318 tests passed |
| Backend 完整套件 | `uv run python -m unittest discover test` | Ran 1709 tests；OK（1 skipped）；packaged document/runtime self-check 通过 |
| CLI 合同 | materials 命令、Agent client 与下载处理 | 81 tests passed |
| Frontend 合同 | API client 与 Profile onboarding/material 交互上下文 | 2 files，33 tests passed |
| Repository | CodeGraph 同步；生产旧路径审计；`git diff --check` | 通过 |

停止点：identities 的通信组、身份主体和材料三个子切片均已迁移。第 4 批开始前必须分别评估
professors 与 community 的路由、schema、管理/补全服务及前端实体边界，避免把两个领域一次性混迁。

### 第 4A 批：`professors` 核心、标签与导入导出（已完成）

开始日期：2026-08-06
完成日期：2026-08-06

计划目标拓扑：

```text
backend/app/modules/professors/
├── api.py
├── schemas.py
├── mutations.py
├── management.py
├── normalization.py
├── samples.py
└── public.py
```

计划范围：

- 迁移导师 UI adapter、DTO、CRUD/归档、标签、批量变更、导入变更、导入导出和字段归一化。
- 组合根、Agent API、Agent change plan、crawler 与其他生产调用方改走 professors 公共入口。
- 旧 API/schema/service 路径保留纯兼容导出；`app.schemas` 历史聚合导出改指向新 DTO。
- 从架构门禁中偿还 `schemas.professor -> services.professor_field_normalization` 旧层级例外。
- 导师信息补全和 community 数据服务留给 4B/4C，不在本子批移动。

本批不变量：

- `/api/professors` 全部 CRUD、标签、批量、归档、样例、模板、导入导出路径和 DTO 不变。
- Agent professor/tag 相关 revision、幂等、change-plan、操作日志和错误码不变。
- 不修改 Professor/ProfessorTag ORM、数据库 schema、Alembic、抓取合同或 Frontend 请求合同。
- 不新增门禁例外，不重写导入解析、邮箱/职称/论文归一化规则。

计划验证：professor management/normalization/tags、UI API、Agent professor/change-plan、crawler 合同、
架构和兼容门禁、相关 CLI/Frontend 测试，以及 Backend 完整 unittest。

实际结果：

- 导师 UI adapter、DTO、CRUD/归档/标签/批量与导入变更、导入导出、归一化和样例数据已聚合到
  `backend/app/modules/professors/`，组合根直接注册新 router。
- Agent、change-plan、crawler、campaign、community 和补全生产调用方已统一改走
  `app.modules.professors.public`；六个旧路径只保留兼容 re-export。
- `app.schemas` 聚合出口已指向新 DTO；`schemas.professor` 与 `schemas.crawl_job` 到旧
  normalization service 的两个门禁例外已删除。
- 导入解析、邮箱/职称/论文归一化、标签和操作日志行为保持不变；未修改 ORM/Alembic 或外部合同。

验证结果：

| 范围 | 验证 | 结果 |
|---|---|---|
| Backend 门禁与兼容 | 架构/API import boundary、API/schema/service 对象一致性 | 7 tests passed |
| Backend 定向 | management/normalization/tags、UI/Agent/change-plan、crawler、contact/workspace | 360 tests passed |
| Backend 完整套件 | `uv run python -m unittest discover test` | Ran 1712 tests；OK（1 skipped）；packaged document/runtime self-check 通过 |
| CLI 合同 | professor/material 等 Agent 命令与 client | 81 tests passed |
| Frontend 合同 | Professors 页面、crawler、通知、选择与 API client | 5 files，79 tests passed |
| Repository | CodeGraph 同步；生产旧路径审计；`git diff --check` | 通过 |

停止点：导师核心能力已归位，信息补全仍保留旧 API/schema/service。第 4B 只迁移补全 job 生命周期
与 crawler worker/scheduler 适配，不混入 community 数据下载、预览或导入。

### 第 4B 批：`professors/enrichment`（已完成）

开始日期：2026-08-06
完成日期：2026-08-06

计划目标拓扑：

```text
backend/app/modules/professors/enrichment/
├── api.py
├── schemas.py
├── service.py
└── public.py
```

计划范围：

- 迁移单导师/批量信息补全 UI adapter、DTO 与 job/item 生命周期服务。
- professors 根公共入口 re-export 补全能力；Agent API、crawler enrichment worker/scheduler 改走该入口。
- 旧 API/schema/service 路径保留纯兼容导出并增加对象一致性测试。
- 保留 CrawlJob/CrawlCandidate 持久化、crawler worker 调度与 token metrics 的现有实现；第 6 批再收敛 crawler 所有权。

本批不变量：

- professor enrichment UI/Agent 路径、DTO、状态映射、revision/幂等、取消/重试/删除/恢复语义不变。
- worker 对导师字段的应用、跳过规则、失败脱敏、token 统计和任务完成判断不变。
- 不修改 ORM、Alembic、runtime worker 数量或 crawler 队列合同，不新增门禁例外。

计划验证：补全 service/UI/Agent 全流程、crawler v2 worker/scheduler、架构/兼容门禁、相关
CLI/Frontend 用例，以及 Backend 完整 unittest。

实际结果：

- 补全 UI adapter、DTO 和 job/item 生命周期已迁入
  `backend/app/modules/professors/enrichment/`，组合根直接注册新 router。
- Agent API、crawler enrichment worker/scheduler 和本领域测试已统一改走
  `app.modules.professors.public`；旧 API/schema/service 路径只保留纯 re-export。
- professors 根门面使用补全能力的懒加载导出，避免
  `crawler_tools -> professors.public -> enrichment.service -> crawler_tools` 初始化环；核心导师能力仍为直接导出。
- 状态映射、revision/幂等、取消/重试/删除/恢复、失败脱敏、token 统计及 worker 完成判断均保持原样；
  未修改 ORM、Alembic、HTTP/Agent/CLI/Frontend 合同或运行时并发配置，未新增门禁例外。

验证结果：

| 范围 | 验证 | 结果 |
|---|---|---|
| Backend 门禁与兼容 | 架构/API import boundary、4A/4B 旧入口对象一致性与独立导入 | 10 tests passed |
| Backend 定向 | enrichment service/UI/Agent、crawler worker/scheduler | 334 tests passed |
| Backend 完整套件 | `uv run python -m unittest discover test` | Ran 1715 tests；OK（1 skipped）；packaged document/runtime self-check 通过 |
| CLI 合同 | Agent CLI 与 client | 81 tests passed |
| Frontend 合同 | 补全 API、Tasks/Professors crawler、通知与页面布局 | 8 files，133 tests passed |
| Repository | CodeGraph 同步；生产旧路径审计；`git diff --check` | 通过 |

停止点：补全领域能力已归位；crawler worker/scheduler 与 CrawlJob/CrawlCandidate 的协作边显式保留到
第 6 批。第 4C 只迁移 community 导师目录、缓存、预览、导入和分享包，不混入 crawler 调度实现。

### 第 4C 批：`community/mentors`（已完成）

开始日期：2026-08-06
完成日期：2026-08-06

计划目标拓扑：

```text
backend/app/modules/community/
├── public.py
└── mentors/
    ├── api.py
    ├── schemas.py
    ├── service.py
    └── public.py
```

计划范围：

- 迁移社区导师数据合同、目录/分片下载与缓存、完整性校验、预览比较、导入生命周期和分享包。
- community 根公共入口向 Agent API 与 change-plan 提供显式合同；组合根直接注册新 UI adapter。
- 旧 API/schema/service 路径保留纯兼容导出，并将既有测试的常量 monkeypatch 指向新 service 所有者。
- 保留 Professor/ProfessorCommunityLink ORM、professors 写入与 operation log 的现有协作边，不改数据库关系。

本批不变量：

- `/api/community-mentors` 与 Agent community 路径、请求/响应 DTO、错误码、revision/幂等和确认语义不变。
- 下载上限、SHA-256/manifest 校验、缓存淘汰、版本兼容、选择限制、比较 token、生命周期阻止和导入事务不变。
- 分享包字段、公式注入防护和 5 MiB 上限不变；不修改 ORM、Alembic、远端数据格式或 Frontend 合同。
- 不重写约 1600 行数据服务算法，不新增门禁例外。

计划验证：community schema/service/cache/import/share、UI/Agent/change-plan、数据库迁移、架构/兼容门禁、
相关 CLI/Frontend 合同，以及 Backend 完整 unittest。

实际结果：

- 社区导师 DTO、远端目录/分片缓存与完整性校验、比较预览、导入生命周期和安全分享包已原样迁入
  `backend/app/modules/community/mentors/`，组合根直接注册新 router。
- Agent API 与 Agent change-plan 已统一改走 `app.modules.community.public`；旧 API/schema/service
  路径只保留纯 re-export，既有常量 monkeypatch 已指向新 service 的真实所有者。
- 新增 community 根公共入口和新旧入口对象一致性测试；完整导出集合审计确认兼容对象均引用新实现。
- Professor/ProfessorCommunityLink、operation log 和 professors 规范化的协作边保持不变；缓存、校验、
  生命周期、比较 token、导入事务、分享包防护及外部 HTTP/Agent/Frontend 合同均未改变。

验证结果：

| 范围 | 验证 | 结果 |
|---|---|---|
| Backend 门禁与兼容 | 架构/API import boundary、三类旧入口与根 public、独立导入 | 8 tests passed |
| Backend 定向 | schema、下载/cache、校验、比较、UI/Agent/change-plan、导入、迁移、分享包 | 45 tests passed |
| Backend 完整套件 | `uv run python -m unittest discover test` | Ran 1719 tests；OK（1 skipped）；packaged document/runtime self-check 通过 |
| CLI 合同 | Agent CLI 与 client | 81 tests passed |
| Frontend 合同 | CommunityMentors 页面、API client、链接与 Professors 布局 | 4 files，74 tests passed |
| Repository | CodeGraph 同步；生产旧路径/兼容导出审计；`git diff --check` | 通过 |

停止点：Backend professors/community 的 UI 与 Agent 能力已迁入领域目录。第 4D 只收敛 Frontend
professor/community 的实体 API、类型和依赖方向，不改页面行为或再次调整 Backend 合同。

### 第 4D 批：Frontend professor/community 实体边界（已完成）

开始日期：2026-08-06
完成日期：2026-08-06

计划目标拓扑：

```text
frontend/src/entities/
├── professor/
│   ├── api/
│   │   ├── professors.ts
│   │   └── informationEnrichment.ts
│   ├── model/types.ts
│   └── index.ts
└── community-mentor/
    ├── api/communityMentors.ts
    ├── model/types.ts
    └── index.ts
```

计划范围：

- 将 professor 核心/补全与 community DTO 的单一事实来源迁入实体 model，将对应 HTTP client 迁入实体 API。
- Pages、context、组件和领域辅助代码通过实体公共入口使用 API；旧 `lib/api` 与 `types` 路径保留纯 re-export。
- 将 Frontend 架构门禁增强为通用的“旧层只能纯 re-export 到新层”结构校验，不增加具体文件边白名单。
- 更新 Vitest mock 路径和 API 对象一致性测试，保持现有模块测试隔离方式。

本批不变量：

- HTTP 路径、参数、请求体、下载方式、DTO 字段和状态文案不变。
- Professors/Community/Tasks/Home/CreateTask 页面状态、通知、选择、缓存与交互行为不变。
- 不调整视觉结构、Backend、Desktop IPC、路由 URL、依赖或锁文件。
- 兼容入口不得包含运行逻辑，不新增 reviewed legacy violation。

计划验证：Frontend 架构与兼容门禁、API client、Professors/Community/Tasks/Home/CreateTask 和通知定向
测试，以及完整 lint、Vitest 与 production build；补充 Backend/CLI 合同回归确认跨进程路径未漂移。

实际结果：

- professor 核心/补全和 community DTO 的单一事实来源已迁入 `entities/*/model/types.ts`，对应 API
  实现已迁入实体 `api/`；页面与组件使用实体 API 子入口，旧 `types` 与三个 `lib/api` 文件仅 re-export。
- Frontend 架构门禁新增通用纯 re-export 识别：旧 API 层只有文件全部由带模块目标的 export declaration
  组成时才可指向 entities；未新增具体边白名单。
- 尚在 legacy 层的通知 context 与 community catalog cache 继续经过受门禁约束的兼容入口，避免形成
  legacy low-level -> entities 的反向依赖；其余直接调用者和 Vitest mocks 已迁到实体路径。
- HTTP 路径、请求体、下载、DTO 字段、状态标签、页面行为、Backend/CLI 合同、依赖和锁文件均未改变。

验证结果：

| 范围 | 验证 | 结果 |
|---|---|---|
| Frontend 门禁与兼容 | 架构边界、三类旧 API 对象一致性、旧 types value 一致性 | 通过：2 files，5 tests |
| Frontend 定向 | API、Professors/Community/Tasks/Home/CreateTask、通知与选择 | 18 files，226 tests passed |
| Frontend lint | `npm run lint` | 通过 |
| Frontend 完整套件 | `npm run test` | 115 files，899 tests passed |
| Frontend build | `npm run build` | TypeScript 与 Vite production build 通过 |
| Backend 合同 | API import、professors/enrichment/community 兼容入口 | 12 tests passed |
| CLI 合同 | Agent CLI 与 client | 81 tests passed |
| Repository | CodeGraph 同步；生产旧路径/DTO 单一来源审计；`git diff --check` | 通过 |

验证备注：首次将完整 Vitest 与 build 并行执行时，Token 分页焦点测试出现 1 次时序失败；该文件独立
复跑 12/12 通过，随后串行完整套件 899/899 通过，确认与本批模块路径变更无关。

停止点：第 4 批 Backend 与 Frontend 边界均已完成。第 5 批开始前必须先定界 matching 的 job/run/cache
所有权与 llm endpoint/thinking/structured-output adaptation 的现有循环，不把 crawler worker 一并迁入。

### 第 5A 批：`matching` 核心与 analysis jobs（已完成）

开始日期：2026-08-06
完成日期：2026-08-06

计划目标拓扑：

```text
backend/app/modules/matching/
├── api.py
├── schemas.py
├── scoring.py
├── job_runtime.py
└── public.py
```

计划范围：

- 迁移启发式匹配分数/基础草稿规则，以及 match analysis job 的 DTO、UI adapter 和完整生命周期 runtime。
- 组合根、Agent API、RuntimeManager 与 schema/service 聚合入口改走 matching 公共入口。
- 旧 API/schema/service 路径保留纯 re-export，并新增对象一致性测试。
- 保留 MatchAnalysisJob/Item/Run、IdentityProfessorMatchResult ORM 与 `task_runtime.calculate_task_match` 的现有协作边。

本批不变量：

- `/api/match-analysis-jobs` 与 Agent 路径、DTO、revision/幂等、取消/重试/删除/恢复和 worker 语义不变。
- 匹配证据判定、共享身份范围、任务复用、并发锁、token 汇总、错误脱敏和持久化顺序不变。
- 不修改 ORM、Alembic、LLM prompt/请求、crawler、runtime worker 数量或外部合同。
- 不重写约 1000 行 job runtime，不新增门禁例外。

计划验证：scoring、analysis job UI/Agent/runtime/worker、并发与持久化、架构/兼容门禁、相关
CLI/Frontend 合同，以及 Backend 完整 unittest。

实际结果：

- 启发式评分、基础草稿规则、match analysis DTO/UI adapter 与完整 job/item/runtime 生命周期已迁入
  `backend/app/modules/matching/`，组合根直接注册新 router。
- Agent API、RuntimeManager 及 schema/service 聚合入口已统一改走 matching 公共入口；旧
  API/schema/service 路径只保留纯 re-export，并新增新旧入口对象一致性和独立导入测试。
- 既有测试的运行时 patch 已改指向新实现所有者；生产代码不再依赖旧 matching 路径。
- MatchAnalysisJob/Item/Run、IdentityProfessorMatchResult ORM 与
  `task_runtime.calculate_task_match` 的协作边保持原样；revision/幂等、取消/重试/删除/恢复、
  worker、token 汇总、错误脱敏和持久化顺序均未改变。

验证结果：

| 范围 | 验证 | 结果 |
|---|---|---|
| Backend 门禁与兼容 | 架构/API import boundary、matching 旧入口对象一致性与独立导入 | 8 tests passed |
| Backend 定向 | scoring、analysis job UI/Agent/runtime/worker、并发与持久化 | 311 tests passed |
| Backend 完整套件 | `uv run python -m unittest discover test` | Ran 1723 tests；OK（1 skipped）；packaged document/runtime self-check 通过 |
| CLI 合同 | Agent CLI 与 client | 81 tests passed |
| Frontend 合同 | matching/analysis job API、Tasks、Workspace 与通知相关用例 | 13 files，165 tests passed |
| Repository | CodeGraph 同步；生产旧路径审计；`git diff --check` | 通过 |

停止点：matching 领域实现与兼容边界已归位；跨领域调用
`task_runtime.calculate_task_match` 明确保留至第 7 批。第 5B 只迁移 LLM profile、runtime 与
endpoint/thinking/structured-output adaptation，不混入 crawler 或 campaign 行为调整。

### 第 5B 批：`llm` profile、runtime 与 adaptation（已完成）

开始日期：2026-08-06
完成日期：2026-08-06

计划目标拓扑：

```text
backend/app/modules/llm/
├── api.py
├── schemas.py
├── runtime.py
├── adaptation/
│   ├── endpoint.py
│   ├── thinking.py
│   └── structured_output.py
└── public.py
```

计划范围：

- 迁移 LLM profile UI adapter/DTO、模型目录与探测、匹配/草稿/重写 runtime，以及 endpoint、thinking、
  structured-output 三类能力探测和持久化适配。
- 组合根、Agent API、matching/crawler/campaign/communications 等生产调用方改走 `llm.public`；
  schema 聚合入口改指向新 DTO。
- 六个旧 API/schema/service 路径保留纯 re-export；既有测试的运行时 patch 改指向新真实所有者，
  并新增完整公共符号兼容与独立导入测试。
- `runtime` 与三类 adaptation 目前互相调用：runtime 在请求阶段延迟导入 adaptation，adaptation 在探测
  阶段延迟导入 runtime 的底层请求函数。第一轮原样收敛为同领域内部协作边，不将其伪装成跨领域依赖；
  后续只有在不改变探测时序和 monkeypatch 合同的前提下才抽取内部 contracts。

本批不变量：

- `/api/llm-profiles` 路径、DTO、默认 profile、模型列表、测试连接及 operation log 语义不变。
- endpoint fallback/relearn、thinking 禁用探测、structured-output 严格模式探测、缓存 key/TTL/锁及
  错误分类、脱敏和重试次数不变。
- prompt、payload、token 估算/解析、HTTP 请求参数和外部 Agent/CLI/Frontend 合同不变。
- 不修改 LLMProfile/adaptation ORM、Alembic、crawler worker、campaign/workspace 状态机、依赖或锁文件；
  不重写约 4000 行 runtime/adaptation 算法，不新增门禁例外。

计划验证：LLM profile UI/Agent/operation log、runtime/prompt/wire contracts、三类 adaptation、crawler
调用链、matching/campaign/workspace 相关流程、架构/兼容门禁、CLI/Frontend 合同，以及 Backend 完整 unittest。

实际结果：

- LLM profile DTO/UI adapter、完整 runtime 及三类 adaptation 已原样迁入 `backend/app/modules/llm/`；
  组合根直接注册新 router，schema 聚合入口直接指向新 DTO。
- Agent API、crawler、matching/test-compose 与其他生产调用方统一改走 `app.modules.llm.public`；六个
  旧 API/schema/service 文件仅保留带显式 `__all__` 的纯 re-export，完整公共符号均保持对象一致。
- runtime 与 endpoint/thinking/structured-output adaptation 的双向延迟导入已收敛为同领域内部相对导入，
  并在模块地图中显式记录；未改写能力探测、缓存、锁、重试或请求时序。
- 既有测试的私有锁注册表、底层请求和 patch 均已指向新真实所有者；生产代码旧路径审计为零。
- 未修改 ORM、Alembic、HTTP/Agent/CLI/Frontend 合同、crawler/campaign 状态机、依赖或锁文件，
  未新增架构门禁例外。

验证结果：

| 范围 | 验证 | 结果 |
|---|---|---|
| Backend 门禁与兼容 | 架构/API import boundary、六类完整导出对象一致性与独立导入 | 12 tests passed |
| Backend LLM 核心 | runtime/prompt/wire、endpoint/thinking/structured-output adaptation | 157 tests passed |
| Backend 关联流程 | profile UI、Agent、operation log、crawler、matching、批量草稿与并发 | 543 tests passed |
| Backend 完整套件 | `uv run python -m unittest discover test` | Ran 1731 tests；OK（1 skipped）；packaged document/runtime self-check 通过 |
| CLI 合同与完整套件 | Agent CLI/client；含 Frontend API 覆盖门禁的完整 unittest | 81 tests passed；完整套件 154 tests passed |
| Frontend 合同 | Profile onboarding、LLM preview/test/default 与模板导入 | 2 files，33 tests passed |
| Repository | CodeGraph 同步；生产旧路径/公共符号审计；`git diff --check` | 通过 |

验证备注：CLI 完整套件首次运行发现 1 个第 4D 遗留的 Frontend `export * from` 兼容入口扫描误判；
后续独立修复为支持 `@/`/相对路径、`.ts`/`.tsx`/index 解析和循环保护的通用递归扫描器。专项 3/3、
增加回归用例后的 CLI 完整套件 154/154 通过。

停止点：第 5 批 matching 与 llm 所有权均已归位；LLM 内部 adaptation 协作边已被显式记录而未向
外扩散。第 6 批只迁移 crawler 的 DTO、UI/Agent adapter、worker/scheduler、页面策略与运行时持久化，
不混入 campaign/workspace 状态机重构。

### 第 6A 批：`crawler` 合同与运行基础（已完成）

开始日期：2026-08-06
完成日期：2026-08-06

计划目标拓扑：

```text
backend/app/modules/crawler/
├── schemas.py
├── public.py
├── jobs/
│   ├── events.py
│   ├── metrics.py
│   ├── records.py
│   └── runs.py
├── pages/
│   ├── chunk_runtime.py
│   ├── chunking.py
│   ├── debug.py
│   ├── domain_policy.py
│   ├── fetch_ledger.py
│   └── tools.py
├── llm/
│   ├── endpoint_retry.py
│   └── structured_output.py
└── v2/
    ├── models.py
    ├── profile_extraction.py
    ├── profile_text_cache.py
    ├── profile_url_policy.py
    ├── retry.py
    ├── routing.py
    ├── token_usage.py
    └── url_utils.py
```

计划范围：

- 迁移 crawl job/candidate/page 的 UI/Agent DTO，以及 job record、run、event、metrics 持久化与投影。
- 迁移页面安全策略、抓取工具、fetch ledger、chunking/chunk runtime、debug 文件，以及 crawler 专用的
  LLM endpoint retry/structured-output wire adapter。
- 迁移不直接调度 worker 的 v2 数据模型、URL/profile 策略、文本缓存、retry、token usage、路由与
  profile extraction，形成可供 6B 编排层调用的闭合基础。
- `crawler.public` 暴露领域外真正需要的 DTO、record use cases、只读投影、调试路径、安全 URL 合同、
  run/token 合同和 profile text cache；Agent API、automation、professors enrichment、diagnostics 改走该入口。
- 对应旧 schema/service 路径保留纯 re-export，并新增完整公共符号对象一致性测试；尚未迁移的
  API/Agent/job runtime/scheduler/workers 可在 6A 内继续通过兼容入口调用，6B 完成后生产旧路径审计必须归零。

本批不变量：

- crawl job/candidate/page DTO、revision/幂等、事件、run/token/metrics 与持久化事务语义不变。
- SSRF 防护、DNS/IP 校验、页面抓取/browser fallback、ledger、chunk 切分/领取/提交、候选校验与保存不变。
- v2 路由、profile extraction、structured-output wire、缓存 key/TTL、retry 和 token 统计不变。
- 不修改 CrawlJob/Candidate/Page/Chunk/Run ORM、Alembic、HTTP/Agent/Frontend 合同、worker 并发或队列优先级；
  不拆写约 2800 行 crawler tools，不新增门禁例外。

计划验证：schema/records/runs/events/metrics、URL 安全与 crawler tools、ledger/chunk/runtime、structured-output、
v2 routing/profile policies/cache/token、Agent/diagnostics/professors enrichment 关联流程、架构/兼容门禁，
以及 Backend 完整 unittest 和相关 CLI/Frontend 合同。

第 6B 预定边界：迁移 `api/crawl_jobs.py`、`agents/faculty_crawler_agent.py`、
`services/crawl_job_runtime.py`、`crawler_v2_scheduler.py` 及 page/chunk/enrichment workers；组合根与
RuntimeManager 改走新入口后再做生产旧路径归零审计。

实际结果：

- 21 个 crawler 基础实现文件已按 `jobs/pages/llm/v2` 四个内部子包归位；新模块内部全部使用相对导入，
  不再依赖任何旧 crawler 路径。
- `crawler.public` 已成为 Agent API、automation、diagnostics 与 professors enrichment 的跨领域入口；
  crawl DTO、record use cases、事件/指标/run、debug、安全 URL 及 profile cache 均由真实所有者导出。
- 旧 schema/service 路径保留纯 re-export；新增 AST 驱动的完整本地公共符号对象一致性测试、公共门面
  对象一致性测试及关键模块独立进程导入测试。
- 已偿还 `schemas.crawl_job -> crawler_tools/url_utils` 两条 legacy layer 门禁例外；SSRF、防抓取绕过、
  ledger、chunk、候选保存、wire、路由、缓存、retry 和 token 行为均保持原样。
- 尚未迁移的 UI API、Agent、job runtime、scheduler/workers 继续经兼容入口调用这些基础能力；该临时
  同领域旧路径只保留到 6B，不作为新的跨领域边界或门禁例外。

验证结果：

| 范围 | 验证 | 结果 |
|---|---|---|
| Backend 门禁与兼容 | 架构/API import boundary、21 类完整公共符号、门面与独立导入 | 7 tests passed |
| Backend crawler 基础 | records/runs/events/metrics、tools/chunk/ledger/debug、LLM wire、v2 policies/routing/cache/token | 233 tests passed |
| Backend 关联流程 | Agent/UI API、professors enrichment、旧编排器、scheduler 与三类 worker | 356 tests passed |
| Backend 完整套件 | `uv run python -m unittest discover test` | Ran 1734 tests；OK（1 skipped）；packaged document/runtime self-check 通过 |
| CLI 合同 | Agent CLI 与 client | 81 tests passed |
| Frontend 合同 | Tasks/Professors crawler、enrichment API、notifications 与页面布局 | 9 files，115 tests passed |
| Repository | CodeGraph 同步；新模块旧路径闭包审计；6B 剩余路径审计；`git diff --check` | 通过 |

停止点：crawler DTO、持久化和运行基础已形成闭合模块，外部领域调用已走公共入口。第 6B 只迁移
HTTP/Agent adapters、job runtime、scheduler 和 page/chunk/enrichment workers，并在完成后将全部生产
crawler 旧路径依赖归零；不迁移 benchmark publication 或 ORM。
