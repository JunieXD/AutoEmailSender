# 关键词搜索范围设计

## 背景

首页导师看板和导师档案管理页当前都有关键词搜索，但搜索范围固定写在过滤函数里。用户无法控制关键词只匹配姓名、学校、职称或邮箱等字段，容易在搜索短词或职称词时得到过宽结果。

现有数据流：

- 首页通过 `HomePage.tsx` 加载 `ProfessorDashboardItemDTO[]`，使用 `filterDashboardProfessors` 在前端本地过滤。
- 导师档案管理页通过 `ProfessorsPage.tsx` 加载 `ProfessorManagementItemDTO[]`，使用 `filterManagementProfessors` 在前端本地过滤。
- 两个页面已有筛选状态持久化。首页按身份保存到 `home_dashboard_filters:<identityId>`；导师管理页保存到 `professors_page_filters`。
- 首页关键词当前匹配姓名、学校、学院、系所、职称、研究方向。
- 导师管理页关键词当前额外匹配邮箱。

## 目标

在首页和导师管理页的关键词输入框旁增加“搜索范围”下拉多选，让用户选择关键词参与匹配的字段。

默认行为保持不变：

- 默认全选所有可搜索字段。
- 默认触发按钮显示“全部字段”。
- 用户取消部分字段后，触发按钮显示“已选 N 项”。
- 重置筛选恢复默认全选。
- 当关键词为空时，搜索范围不影响结果。

## 非目标

- 不新增后端搜索参数。
- 不改变导师列表接口返回结构。
- 不改变高级筛选、排序、分页和选择导师的业务规则。
- 不把搜索范围放进“高级筛选”面板。
- 不修改旧版 `MentorDashboardClient` 和 `useMentorFilters`，除非实现时确认它们仍服务于当前导师管理页路由。

## 字段模型

新增稳定字段 key，UI 展示中文标签，过滤逻辑只依赖 key。

首页字段：

| key | 文案 | 数据字段 |
| --- | --- | --- |
| `name` | 姓名 | `name` |
| `university` | 学校 | `university` |
| `school` | 学院 | `school` |
| `department` | 系所 | `department` |
| `title` | 职称 | `title` |
| `researchDirection` | 研究方向 | `research_direction` |

导师管理页字段：

| key | 文案 | 数据字段 |
| --- | --- | --- |
| `name` | 姓名 | `name` |
| `email` | 邮箱 | `email` |
| `university` | 学校 | `university` |
| `school` | 学院 | `school` |
| `department` | 系所 | `department` |
| `title` | 职称 | `title` |
| `researchDirection` | 研究方向 | `research_direction` |

## 交互设计

关键词筛选区调整为：

- 左侧保留“关键词”标签和输入框。
- 输入框右侧增加“搜索范围”下拉多选触发器。
- 首页和导师管理页使用同一套交互。

触发器文案：

- 已选择全部字段时显示“全部字段”。
- 未全选时显示“已选 N 项”。

下拉面板：

- 列出所有可选字段。
- 每个字段使用复选框样式，已选字段显示勾选状态。
- 只剩最后 1 项已选时，该项不能再取消。
- 面板底部显示提示：“至少保留最后一项”。
- 不提供“清空”按钮，避免出现空搜索范围。
- 支持点击外部关闭和 `Escape` 关闭。

布局：

- 桌面端搜索框和搜索范围下拉保持同一行。
- 窄屏下允许自然换行，不压缩输入框到不可用宽度。
- 下拉触发器高度与现有筛选控件保持一致。

## 筛选规则

过滤逻辑从“固定搜索所有字段”改为“只搜索已选字段”。

关键词为空时：

- 直接视为关键词匹配。
- 不读取搜索范围。
- 所有其他高级筛选、排序和分页规则保持不变。

关键词非空时：

- 根据当前页面的 `keywordSearchScopes` 取出参与匹配的字段。
- 任一已选字段包含关键词即命中。
- 匹配继续使用 trim 后的小写模糊匹配。
- 空字段不命中。

空范围保护：

- UI 层禁止取消最后一项。
- 读持久化状态时，如果 `keywordSearchScopes` 缺失、不是数组、包含非法 key 后变为空，统一回退默认全选。
- 过滤函数收到空数组时也按默认全选处理，作为防御性兜底。

## 状态与持久化

首页 `DashboardFilterState` 新增：

```typescript
keywordSearchScopes: DashboardKeywordSearchScope[];
```

导师管理页 `ProfessorManagementFilterState` 新增：

```typescript
keywordSearchScopes: ProfessorManagementKeywordSearchScope[];
```

默认筛选状态使用全部字段。

持久化兼容：

- 读取旧 sessionStorage 时缺少搜索范围，回退默认全选。
- 读取到未知字段 key 时丢弃未知值。
- 丢弃后为空则回退默认全选。
- 写入时随原有 filters 一起保存。

重置行为：

- 首页“重置”恢复默认全选，并恢复默认排序。
- 导师管理页“重置”恢复默认全选，并恢复现有默认筛选和排序。
- “清空高级筛选”不影响关键词和搜索范围。

## 组件边界

新增专用组件：

`frontend/src/components/molecules/KeywordSearchScopeSelect.tsx`

组件职责：

- 渲染触发器、下拉面板、复选框列表和提示文案。
- 根据 `selectedValues` 与 `options` 计算“全部字段”或“已选 N 项”。
- 禁止取消最后一项。
- 暴露 `onChange(nextValues)`，不持有业务字段语义。

不复用 `MultiSelectFilter` 的原因：

- 现有组件语义面向高级筛选，包含“清空”行为。
- 搜索范围需要“至少保留最后一项”约束。
- 搜索范围摘要文案固定为“全部字段”和“已选 N 项”，与现有组件的“首项 等 N 项”不同。

页面职责：

- 首页和导师管理页分别提供字段选项。
- 页面把组件返回的 key 写入本页 filters。
- 页面把 filters 传给本页过滤函数。

过滤模块职责：

- 定义可搜索字段 key 类型和默认字段集合。
- 提供校验和归一化方法，保证旧持久化状态兼容。
- 根据搜索范围执行关键词匹配。

## 实现边界

预计修改：

- `frontend/src/components/molecules/KeywordSearchScopeSelect.tsx`
- `frontend/src/features/home-dashboard/client/filterDashboardProfessors.ts`
- `frontend/src/features/home-dashboard/client/filterDashboardProfessors.test.ts`
- `frontend/src/features/professor-management/client/filterManagementProfessors.ts`
- `frontend/src/features/professor-management/client/filterManagementProfessors.test.ts`
- `frontend/src/pages/HomePage.tsx`
- `frontend/src/pages/ProfessorsPage.tsx`

可选测试：

- 为 `KeywordSearchScopeSelect` 增加组件测试，覆盖提示文案和最后一项不可取消。
- 若页面测试已有稳定夹具，可补充首页和导师管理页的轻量集成测试。

## 测试与验证

自动化测试至少覆盖：

- 首页默认全字段行为不变。
- 首页只选姓名时，关键词“副教授”不会命中职称。
- 首页关键词为空时，搜索范围不影响结果。
- 导师管理页默认全字段行为不变。
- 导师管理页只选邮箱时，能匹配邮箱。
- 旧持久化状态缺少搜索范围时回退全选。
- 非法搜索范围 key 被丢弃，丢弃后为空则回退全选。
- 搜索范围组件无法取消最后一项，并显示“至少保留最后一项”。

验证命令：

```bash
cd frontend
npm run test -- src/features/home-dashboard/client/filterDashboardProfessors.test.ts
npm run test -- src/features/professor-management/client/filterManagementProfessors.test.ts
npm run test -- src/components/molecules/KeywordSearchScopeSelect.test.tsx
npm run lint
npm run build
```

人工验证：

- 首页首次打开时搜索范围显示“全部字段”，搜索结果与改动前一致。
- 首页只保留“姓名”后，输入职称词不会命中非姓名字段。
- 首页点击“重置”后，搜索范围恢复“全部字段”。
- 导师管理页搜索范围包含“邮箱”。
- 导师管理页只保留“邮箱”后，输入邮箱片段可以命中导师。
- 下拉中只剩最后 1 项时无法取消，并能看到“至少保留最后一项”提示。

## 风险与约束

搜索范围会增加筛选状态结构，必须做好旧 sessionStorage 的兼容读取，否则用户已有筛选缓存可能被误判。

首页和导师管理页字段集合不同，字段 key 应分别定义类型，避免把邮箱错误带入首页。

如果旧版 `MentorDashboardClient` 仍被某些入口使用，本期不修改会导致该旧入口没有搜索范围能力。实现前应通过路由和引用关系确认实际入口；若仍在用，需要追加一个小范围实现计划覆盖旧入口。
