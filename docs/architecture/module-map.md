# 模块地图

本地图定义业务能力的首选所有者。它是迁移和新增代码放置位置的依据，不代表必须一次性移动全部文件。

## 后端领域

| 模块 | 拥有的概念 | 当前主要来源 | 允许依赖 |
|---|---|---|---|
| `identities` | 身份、材料、身份通信组、账号配置状态 | `identities.py`、`materials.py`、`identity_*` | `system`、平台能力 |
| `professors` | 导师、标签、备注、归档、字段规范化、信息补全 | `professor*`、`contact_status.py` | `identities` 的公共合同 |
| `community` | 社区导师目录、缓存、预览、导入、分享包 | `community_mentor*` | `professors` 公共写入用例 |
| `campaigns` | 批量任务、邮件任务、草稿、模板、排程 | `batch_*`、`email_task*`、`outreach_template*` | `identities`、`professors`、`llm` |
| `communications` | SMTP、IMAP、邮件历史、回复检测、测试邮件 | `mail_runtime.py`、`imap_*`、`email_log*`、`test_compose*` | `identities`、`professors` |
| `workspace` | 单导师会话、审核、发送与后续动作的应用编排 | `workspace*`、部分 `task_runtime.py` | `matching`、`campaigns`、`communications` |
| `matching` | 匹配计算、分析任务、分析运行记录 | `matching.py`、`match_analysis*` | `identities`、`professors`、`llm` |
| `crawler` | 抓取任务、运行、worker、页面策略、证据与调试 | `crawl_*`、`crawler_*`、crawler agent | `llm`，通过用例向 `professors` 交付候选结果 |
| `llm` | LLM profile、调用、端点适配、结构化输出、thinking、token 记录 | `llm_*`、`structured_output_*`、`thinking_*` | 平台能力 |
| `automation` | Agent 计划、确认、幂等、变更计划和回执 | `agent_*` | 各模块 `public` application API |
| `reporting` | Dashboard、统计与只读 usage 投影 | `dashboard_stats.py`、`token_usage_records.py` | 只读端口，不拥有源实体 |
| `system` | runtime settings、诊断、操作日志、启动恢复、schema 元数据 | `runtime_settings*`、`system_settings.py`、`diagnostics.py`、`operation_logs.py` | 平台能力 |

## 首个迁移切片（已完成）

第 2 批已迁移 `system/runtime-settings`：

```text
backend/app/modules/system/runtime_settings/
├── __init__.py
├── api.py
├── schemas.py
├── service.py
└── public.py
```

领域外调用方统一经 `backend/app/modules/system/public.py` 进入；切片自己的
`public.py` 供 system 领域内部组合与领域门面转发使用。

初始文件来源：

- `backend/app/api/runtime_settings.py`
- `backend/app/schemas/runtime_settings.py`
- `backend/app/services/runtime_settings.py`
- 与 runtime settings 紧密相关、经调用图确认可同时迁移的 system settings 代码

旧路径暂时保留纯 re-export 兼容入口。`/api/runtime-settings`、Agent `/settings` 和 CLI 命令合同保持不变。

## identities 首个子切片（第 3A 批，已完成）

第 3A 批已迁移通信组能力：

```text
backend/app/modules/identities/
├── public.py
└── communication_groups/
    ├── api.py
    ├── schemas.py
    ├── service.py
    ├── scope.py
    └── public.py
```

所有权包括通信组 CRUD、成员合并确认、匹配依据身份、通信范围解析、身份删除后的
组清理和 DTO 序列化。ORM 模型仍留在 `app.models`，操作日志仍使用现有平台服务。

领域外调用方统一经 `backend/app/modules/identities/public.py` 进入。材料、身份主体、
SMTP/IMAP 测试和模板设置不属于本子切片。

## identities 身份主体子切片（第 3B 批）

profiles 拥有身份 CRUD、默认身份、SMTP/IMAP 连接测试、模板导入、身份 DTO 与身份序列化。
materials 在本批只拥有材料 DTO 与材料序列化，以解除原 `identity.py` 和
`identity_serializers.py` 的混合职责；材料生命周期行为仍属于第 3C。

## identities 材料生命周期子切片（第 3C 批）

materials 拥有材料上传、默认选择、删除预览与确认、引用一致性、文本提取适用性、下载命名
和 UI HTTP adapter。删除事务需要协调 campaigns/tasks、test-compose 与 matching 记录；第一轮
通过这些领域现有服务/模型协作，不复制状态机，也不改变数据库关系。领域外调用方统一通过
`backend/app/modules/identities/public.py` 使用材料能力。

## professors 核心子切片（第 4A 批）

professors 拥有导师 DTO、CRUD/归档、标签、批量变更、样例数据、导入导出及字段归一化。
领域外的 Agent、crawler、campaign 与 community 调用方统一经
`backend/app/modules/professors/public.py` 进入。信息补全作为内部子切片在 4B 迁移，community
仍是独立领域，不因共享 Professor ORM 而合并所有权。

## professors 信息补全子切片（第 4B 批）

信息补全位于 `backend/app/modules/professors/enrichment/`，拥有补全 DTO、job/item 生命周期和
UI adapter；它通过现有 crawler worker/scheduler 执行采集，后者在第 6 批迁移前仍是显式外部依赖。

## community 导师库子切片（第 4C 批）

`backend/app/modules/community/mentors/` 拥有远端数据合同、目录/分片缓存与校验、导师比较预览、
导入生命周期和安全分享包。它通过 `professors` 公共能力复用字段规范化，并暂时直接协调
Professor/ProfessorCommunityLink 持久化与 operation log；这些协作边在不改变数据库关系的前提下保留。

## matching 子切片（第 5A 批，已完成）

`backend/app/modules/matching/` 拥有启发式评分、基础草稿规则和 match analysis job/item 生命周期。
`matching/task_analysis.py` 拥有 task-level LLM 匹配计算、运行记录恢复和结果落库；job runtime 通过
域内调用复用该能力，领域外统一经 `matching.public` 调用，不再依赖 workspace/task runtime。

## llm 子切片（第 5B 批，已完成）

`backend/app/modules/llm/` 拥有 profile DTO/UI adapter、模型目录与探测、匹配/草稿/重写运行时，
以及 endpoint、thinking、structured-output 三类能力适配。其他领域统一通过 `llm.public` 调用；
runtime 与 adaptation 的双向延迟导入属于同领域内部探测协议，第一轮迁移记录该边并保持现有时序，
不得向 crawler、campaign 或 workspace 反向取用业务实现。

## crawler 基础子切片（第 6A 批，已完成）

`backend/app/modules/crawler/` 按 `jobs`、`pages`、`llm`、`v2` 四个内部子包组织。6A 拥有 DTO、
job record/run/event/metrics、页面抓取与 chunk 基础、crawler 专用 LLM wire adapter，以及不直接调度
worker 的 v2 策略与路由；6B 再迁移 UI/Agent adapter、job 编排、scheduler 和 workers。
领域外只经 `crawler.public` 使用 record use cases、投影、安全 URL 合同、debug 路径、run/token 合同
和 profile text cache；Professor enrichment 仍拥有补全 job 生命周期，仅把抓取执行委托给 crawler。

## crawler 编排子切片（第 6B 批，已完成）

`crawler.api` 与 `crawler.agent` 是 UI/Agent 适配器，`jobs.runtime` 负责 job 恢复及兼容编排，`v2.scheduler`
负责 claim/finalize，page/chunk/enrichment workers 只处理各自 work item。RuntimeManager 仅通过
`crawler.public` 启动 worker；worker 通过域内相对导入使用 6A 的持久化、页面、LLM 与策略能力。

## campaigns 活动与模板子切片（第 7A 批，已完成）

`backend/app/modules/campaigns/` 拥有 Agent campaign 用例、batch DTO、排期/完成状态/item/resend
规则、无研究方向草稿回退，以及 outreach template 的 DTO、UI adapter、库、变更、导入和渲染。
领域外统一经 `campaigns.public` 使用这些能力；模板 UI router 只由组合根直接注册。

batch HTTP adapter 已归入 `campaigns/batch_tasks/api.py`，batch draft claim/recovery worker 已归入
`campaigns/drafts/runtime.py`；两者只经 `workspace.public` 触发单封任务动作，组合根、startup 与
RuntimeManager 分别直接使用 owner 或 `campaigns.public`。BatchTask、EmailTask、OutreachTemplate ORM
继续属于 `app.models` registry，不在本轮文件所有权迁移中拆表或复制。

## communications 传输与同步子切片（第 7B 批，已完成）

`backend/app/modules/communications/` 已拥有地址规范化、EmailLog 幂等入库/事件投影、SMTP/IMAP
transport、协议错误、message fetch/rate limit/sync state，以及 test-compose DTO/UI/application runtime。
领域外统一经 `communications.public` 使用；test-compose 合同和用例按需加载，避免低层 transport
调用触发 identities/campaigns/LLM 高层初始化。

`communications/imap/sync.py` 拥有 IMAP 增量/历史同步、single-flight 锁与 throttle、sent-folder
发现、recent-v2/targeted history、sent/received 关联、回复检测和 EmailLog 写入。RuntimeManager、
workspace 与 Agent 调用方只经 `communications.public` 使用这些能力；旧 `task_runtime.py` 仅保留原公开
同步入口的对象级兼容转发，communications 不反向依赖 workspace。

## workspace、email-task 与 batch adapters（第 7C 批，已完成）

`backend/app/modules/workspace/` 拥有 workspace DTO、thread projection/task bootstrap，以及 workspace 与
email-task UI adapters。`workspace.public` 是 Agent、campaign adapter/worker 和 RuntimeManager 的稳定入口。

`workspace/tasks/runtime.py` 拥有草稿生成/改写、审核/保存、手动继续和跟进状态机；
`workspace/tasks/delivery.py` 拥有到期任务选择、批量窗口、身份发送间隔、发送恢复和 SMTP 提交。
旧 API/schema/service 路径是纯 re-export，生产调用和测试 patch 已迁到真实 owner。

## 前端层与 slice

| 层 | 职责 | 示例 |
|---|---|---|
| `app` | 启动、路由、全局 providers | Router、Desktop backend provider |
| `pages` | 路由级组合 | tasks、professors、profile、workspace |
| `widgets` | 页面中的完整业务区块 | task center、professor table、workspace composer |
| `features` | 一个用户动作 | import-professors、review-crawl、approve-draft |
| `entities` | 实体模型、实体 API、实体 UI | professor、identity、task、material |
| `shared` | 无业务所有权的技术与 UI 基础 | HTTP client、dialog、date、desktop bridge |

第 4D 已建立 `entities/professor` 与 `entities/community-mentor`：实体 model 拥有 DTO，实体 API
拥有 HTTP 请求合同，页面与上下文通过实体 `index.ts` 使用能力。旧 `types` 和 `lib/api` 仅作为可静态
验证的纯 re-export 兼容入口，待第 9 批确认所有调用方后清理。

迁移时禁止仅因“多个地方使用”就把领域代码放入 `shared`。只有在调用方来自多个领域且代码本身没有业务术语时，才可进入 `shared`。

## Desktop main-process 模块（第 8B 批，已完成）

`desktop/src/main/` 已按运行职责分为 `backend`、`agent-support`、`updates`、`files` 与 `shell`。
backend 进程控制与内部类型同目录；Agent runtime descriptor、安装服务和开发 CLI 准备器归
`agent-support`；更新与 Sparkle bridge 归 `updates`；导入/分享与材料打开归 `files`；外部 URL、
自启动、托盘和窗口生命周期归 `shell`。

根目录同名 service 仅作纯 re-export，生产入口与测试直接使用 owner。Electron 的 `main.ts`、
`preload.ts` 构建入口和打包资源路径保持不变。`main/ipc/register.ts` 统一装配 handler，
`main/bootstrap/application.ts` 拥有应用生命周期与运行状态组合；两个根入口只调用各自 bootstrap。

## 跨进程边界

```text
React UI -> UI HTTP adapter -> application use cases
CLI -> Agent API v1 adapter -> application use cases
Electron preload -> IPC contract -> Electron main modules
```

Frontend 不直接导入 backend Python 代码；CLI 不直接导入 backend 包；跨进程共享的是版本化合同，不是运行时业务实现。

Desktop renderer 可见 DTO 与 bridge 以 `contracts/desktop-ipc.d.ts` 为单一来源；
`frontend/src/types/desktop.d.ts` 和 `desktop/src/types.ts` 只做类型转发。IPC/event channel 由
`desktop/src/contracts/channels.ts` 统一定义，preload bridge 与 main handlers 不得内联重复 channel。
