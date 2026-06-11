# 匹配分析与草稿材料解耦设计

## 背景

当前系统把匹配分析和草稿生成都挂在 `EmailTask.primary_material_id` 上。首页单个匹配分析会先调用工作区 `ensure-task`，拿到 `EmailTask.id` 后再调用 `POST /api/email-tasks/{id}/calculate-match`；工作区计算匹配也直接使用当前 `EmailTask.id`；批量匹配任务则为每位导师找到或创建一条 `EmailTask` 后后台执行。

这导致“默认材料”的语义混在一起：

- 个人页的默认材料是用户眼里的当前默认材料。
- `EmailTask.primary_material_id` 是任务级材料快照。
- 匹配分析实际读取任务材料，而不是个人页当前默认材料。
- 草稿生成 prompt 会复用已有匹配结果，导致匹配分析和写信内容耦合。

用户反馈过一个典型问题：一开始计算过匹配度，后来切换默认材料，再在首页重新计算匹配度时报错“请先选择用于匹配的默认材料”。根因是前端按个人页当前默认材料判断可以计算，但后端按旧 `EmailTask.primary_material_id` 判断；如果旧任务材料被删除或清空，就会报错。即使旧材料没有删除，也可能静默用旧材料重新计算，和用户直觉不一致。

## 目标

1. 匹配分析始终使用个人页当前默认材料，也就是 `IdentityProfile.current_primary_material_id`。
2. 草稿生成、草稿改写不再读取或引用任何匹配度结果。
3. `EmailTask.primary_material_id` 不再参与匹配分析语义。
4. 保留历史数据，不删除任务材料字段，避免破坏批量写信、草稿重生成和历史任务。
5. 通过迁移和兼容逻辑降低旧版本数据升级后的异常概率。
6. 每次匹配运行记录实际使用的材料 id，便于诊断和追溯。
7. 错误文案和前端文案与新语义保持一致。

## 非目标

- 不移除 `email_tasks.primary_material_id`。
- 不重做邮件任务、批量任务和工作区整体数据模型。
- 不清空已有 `match_score`、`match_reason` 或历史匹配运行记录。
- 不自动重新计算历史匹配分。
- 不承诺旧匹配分能反映当前默认材料；旧结果只是历史结果，重新计算后才按新材料更新。

## 产品规则

### 匹配分析

匹配分析的材料来源只有一个：

```text
identity_profiles.current_primary_material_id
```

当用户在首页、工作区或批量匹配任务中发起匹配分析时，后端在实际执行时读取当前身份的默认材料。

如果当前身份没有默认材料，接口返回用户可理解错误：

```text
请到个人页设置默认材料
```

如果默认材料无法提取文本，继续沿用现有材料提取错误处理，但文案应指向个人页默认材料，而不是任务默认材料。

### 草稿生成与改写

草稿生成和改写只参考：

- 发送身份信息
- 工作区或批量任务选定的写信参考材料
- 导师资料
- 当前模板或编辑器内容
- 用户写信偏好和改写偏好
- 当前模型配置

草稿 prompt 不再包含：

- `match_score`
- `match_reason`
- `fit_points`
- `risk_points`
- `keywords`

匹配分析结果只用于首页排序、筛选、展示和用户判断，不参与草稿内容生成。

### 任务材料

`EmailTask.primary_material_id` 保留，但语义收窄为“写信参考主材料”或“AI 写信参考材料”。它服务草稿生成、批量写信和历史任务复现，不再服务匹配分析。

前端文案不再把任务材料叫作“默认材料”。统一使用：

- 个人页：继续叫“默认材料”。
- 创建任务页/工作区：叫“AI 写信参考材料”。
- 匹配分析提示：叫“个人页默认材料”。

## 后端设计

### 单次匹配分析

`calculate_task_match()` 当前通过 `_load_email_task()` 读取 `task.primary_material`。调整后：

1. 仍然通过 `EmailTask` 定位导师、身份、任务状态和并发锁。
2. 使用 `task.identity.current_primary_material` 作为匹配材料。
3. 如果 identity 未加载当前默认材料，查询并校验该材料属于当前身份。
4. 调用 `ensure_material_extracted_text(identity.current_primary_material)`。
5. 调用 `llm_runtime.generate_match_evaluation()` 时传入个人页当前默认材料。
6. 匹配结果仍写回当前 `EmailTask.match_score` 等字段，用于首页和工作区展示。
7. 新建 `MatchAnalysisRun` 时记录本次实际使用的 `primary_material_id`。

这样可以保持现有 API 契约：

```text
POST /api/email-tasks/{task_id}/calculate-match
```

不需要让前端改成按 professor_id 调用，也不需要引入一套新的匹配结果表。

### 批量匹配任务

批量匹配任务仍然创建 `MatchAnalysisJob` 和 `MatchAnalysisJobItem`，并为每位导师关联一条 `EmailTask`。但 `_ensure_match_email_task()` 不再为了匹配分析给旧任务补 `primary_material_id`。

创建批量匹配任务时仍需要校验身份当前默认材料存在。实际执行每个 item 时再次读取身份当前默认材料。这样可以处理队列等待期间材料变化：

- 如果用户在任务执行前切换默认材料，后端使用执行时的当前默认材料。
- 如果用户在执行前删除默认材料，item 跳过或失败，错误提示用户设置默认材料。

该规则符合“计算匹配度使用个人页当前默认材料”的产品定义。

### 匹配运行记录

`match_analysis_runs` 增加可空字段：

```text
primary_material_id INTEGER NULL REFERENCES identity_materials(id)
```

新运行记录必须写入本次使用的材料 id。

历史运行记录迁移时按当前可得数据回填：

```sql
UPDATE match_analysis_runs
SET primary_material_id = (
  SELECT email_tasks.primary_material_id
  FROM email_tasks
  WHERE email_tasks.id = match_analysis_runs.email_task_id
)
WHERE primary_material_id IS NULL;
```

如果历史任务材料已被删除或清空，保持 NULL。NULL 表示旧版本没有可靠记录。

### LLM prompt

匹配 prompt 继续接收 `primary_material`，但调用方提供的是身份当前默认材料。

草稿生成 prompt 中删除匹配上下文参数。实现上可以：

- 从 `generate_draft_content()`、`build_draft_prompt_parts()` 等草稿路径移除 `current_match` 参数，或保留参数但完全忽略并逐步清理。
- 测试必须断言草稿 prompt 不包含匹配分、匹配理由、fit/risk/keywords。

## 迁移设计

### Schema 迁移

新增 Alembic revision：

1. 给 `match_analysis_runs` 增加 `primary_material_id` 可空列。
2. 创建索引 `ix_match_analysis_runs_primary_material_id`。
3. 从历史 `email_tasks.primary_material_id` 回填运行记录；无法回填时保持 NULL。
4. 不修改或删除 `email_tasks.primary_material_id`。

该迁移是向前兼容的。即使回填不完整，也不会影响新逻辑运行。

### 数据兼容迁移

为了减少升级后“个人页没有默认材料”的情况，新增保守补齐逻辑：

1. 如果 `identity_profiles.current_primary_material_id` 已存在，完全不动。
2. 如果为空，且该身份只有一份可作为主材料的材料，设置为默认材料。
3. 如果为空，且历史任务中存在最近使用且仍有效的 `primary_material_id`，将最近一份有效主材料设置为默认材料。
4. 如果多份候选材料无法明确判断，保持为空，交给用户在个人页选择。

第 3 条使用以下保守规则：

- 候选材料必须仍属于该身份。
- 候选材料必须可作为主材料。
- 按最近 `email_tasks.updated_at`、`created_at`、`id` 取一份。
- 如果该身份历史任务没有有效材料，不补。

迁移不应改动任务状态、草稿、匹配结果或发送记录。

### 启动安全

项目已有迁移前数据库备份机制。该改动依赖现有启动备份，不额外实现新的备份能力。

如果迁移失败，应保持现有行为：启动失败并保留迁移前备份，不能部分静默吞掉错误。

## 前端设计

### 首页

首页单个和批量匹配分析继续按 `selectedIdentity.current_primary_material_id` 判断是否可以发起。

错误文案调整为：

```text
缺少默认材料：请到个人页设置默认材料。
```

当用户重算已有匹配分时，不需要提示任务材料，因为匹配材料只有个人页当前默认材料。

### 工作区

工作区里的“用于匹配的材料”文案应下线或改名。当前任务材料选择只影响写信，应展示为：

```text
AI 写信参考材料
```

计算匹配按钮不依赖 `currentTask.primary_material_id`，而依赖身份当前默认材料和导师研究证据。

### 创建任务页

创建任务页保留主材料选择，但语义为草稿生成使用的“AI 写信参考材料”。提交 payload 仍可带 `primary_material_id`，服务批量草稿生成和写信流程。

## 测试计划

### 后端单元测试

新增或更新 `test_match_analysis_runtime.py`：

- 任务绑定材料 A，身份当前默认材料 B，重新计算匹配时传给 LLM 的是 B。
- 任务绑定材料 A，A 被删除或任务材料被清空，身份当前默认材料 B，重新计算成功并使用 B。
- 身份当前默认材料为空时，计算匹配失败，错误指向个人页默认材料。
- 新建 `MatchAnalysisRun.primary_material_id` 等于本次实际使用的身份默认材料 id。
- 草稿生成 prompt 不包含匹配结果字段。

### API 测试

新增或更新 `test_api_endpoints.py`：

- 首页单个匹配路径：`ensure-task` 后任务材料为空，但身份默认材料存在，`calculate-match` 成功。
- 切换默认材料后重算已有匹配分，使用新默认材料。
- 批量匹配任务执行时使用身份当前默认材料。

### 迁移测试

新增 Alembic 迁移测试：

- 旧库中 `match_analysis_runs` 通过 `email_task_id` 回填 `primary_material_id`。
- 身份默认材料为空且只有一份候选材料时自动补齐。
- 身份默认材料为空且多份候选材料无法明确判断时不乱选。
- 任务级 `primary_material_id` 保留不变。

### 前端测试

更新相关 Vitest：

- 首页仍按个人页默认材料控制匹配入口。
- 工作区计算匹配不再因为 `currentTask.primary_material_id` 为空而禁用。
- 文案从“用于匹配的材料/默认材料”调整为“AI 写信参考材料”。

## 验收标准

1. 用户在个人页切换默认材料后，首页重算匹配度使用新默认材料。
2. 删除旧任务材料后，只要个人页有当前默认材料，重算匹配度不再报“请先选择用于匹配的默认材料”。
3. 草稿生成和改写结果不受已有匹配分影响。
4. 历史匹配结果仍可展示，历史任务仍可打开。
5. 新的匹配运行记录能看到本次使用的材料 id。
6. 迁移后不会清空用户任务、草稿、匹配结果、发送记录或材料库。

## 实施顺序

1. 新增 Alembic migration 和模型字段。
2. 调整匹配 runtime 使用身份当前默认材料，并记录 run 材料 id。
3. 调整批量匹配 job 不再为匹配目的补任务材料。
4. 移除草稿 prompt 的匹配上下文。
5. 调整前端文案和按钮依赖。
6. 补齐后端、迁移和前端回归测试。
7. 更新相关用户文档中“匹配分析依据”和“材料”说明。

## 风险与缓解

### 历史匹配分语义不完全可追溯

旧版本没有显式记录每次匹配实际使用的材料。迁移从当时的任务材料回填；无法回填时保持 NULL，并把精确追溯能力限定为新版本之后。

### 批量匹配期间默认材料变化

新规则定义为执行时读取身份当前默认材料。这样和“个人页当前默认材料”一致，但批量任务较长时可能不同 item 使用不同材料。若后续希望批量任务锁定发起时材料，应另起设计，为 `MatchAnalysisJob` 增加材料快照字段。本次不做。

### 文案混淆

需要同步调整前端文案和文档，避免用户把“AI 写信参考材料”理解成匹配材料。

### 旧逻辑残留

草稿路径中任何 `current_match` 参数都可能让耦合重新出现。测试需要直接断言 prompt 不包含匹配字段，而不仅是 UI 文案调整。
