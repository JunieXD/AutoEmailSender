# 导师管理页高级筛选实施计划

> **面向 AI 代理的工作者：** 按本计划逐任务实现。步骤使用复选框（`- [ ]`）语法来跟踪进度。实现前先阅读对应规格：`docs/superpowers/specs/2026-05-21-professor-management-advanced-filters-design.md`。

**目标：** 将导师管理页现有“关键词 + 两个单选下拉”筛选升级为“常驻基础筛选 + 可展开高级筛选”。高级筛选支持学校、学院、系所、职称 / 导师资格多选；页面只保留一个“重置”按钮；归档视图切换不受重置影响。

**架构：** 将导师管理筛选和排序逻辑从 `ProfessorsPage.tsx` 中抽到 feature helper，页面只负责状态编排和渲染。复用首页已有 `MultiSelectFilter` 与 `NativeSelectField`，不改后端接口，不引入首页任务流字段。

**技术栈：** React、TypeScript、Vite、Vitest、Testing Library、Tailwind CSS、lucide-react。

---

## 文件结构

- 新增：`frontend/src/features/professor-management/client/filterManagementProfessors.ts`
  - 职责：定义导师管理筛选状态、默认值、选项生成、筛选计数和过滤逻辑。
- 新增：`frontend/src/features/professor-management/client/filterManagementProfessors.test.ts`
  - 职责：覆盖关键词、多选筛选、学校学院联动、重置语义所需的纯函数逻辑。
- 新增：`frontend/src/features/professor-management/client/sortManagementProfessors.ts`
  - 职责：定义导师管理排序 key、排序选项和排序逻辑。
- 新增：`frontend/src/features/professor-management/client/sortManagementProfessors.test.ts`
  - 职责：覆盖最新导入、最近更新、姓名、学校排序。
- 修改：`frontend/src/pages/ProfessorsPage.tsx`
  - 职责：接入新的筛选状态、排序状态、高级筛选面板和多选组件。
- 复用：`frontend/src/components/molecules/MultiSelectFilter.tsx`
  - 职责：学校、学院、系所、职称多选下拉。
- 复用：`frontend/src/components/atoms/NativeSelectField.tsx`
  - 职责：排序下拉。

不修改：

- 后端 API。
- 数据库结构。
- 首页筛选逻辑。
- 导入、新增、编辑、归档、恢复和批量操作接口。

---

## 设计约束

- 不完整搬首页筛选条。
- 不加入匹配度、联系状态、发送次数筛选。
- 高级筛选默认收起。
- “高级筛选”按钮显示当前高级筛选数量，计数不包含关键词、排序和归档视图。
- 不新增单独的“清空高级筛选”按钮。
- “重置”清空关键词、高级筛选并恢复默认排序，但不切换 `正常 / 已删除 / 全部`。
- 归档视图切换继续独立存在。
- 学校变化后，已选学院中不再属于可选范围的项要自动移除。
- 筛选或排序变化后，页码回到第 1 页。
- “选择全部筛选结果”继续跨页作用于当前筛选结果，而不是当前页。

---

### 任务 1：确认现有页面状态和筛选入口

**文件：**
- 读取：`frontend/src/pages/ProfessorsPage.tsx`
- 读取：`frontend/src/components/molecules/MultiSelectFilter.tsx`
- 读取：`frontend/src/lib/professorTitle.ts`

- [ ] **步骤 1：确认当前筛选状态**

确认页面当前使用以下状态：

- `keyword`
- `titleFilter`
- `schoolPairFilter`
- `archiveFilter`
- `currentPage`
- `selectedIds`

记录现有 `filteredProfessors`、`titleOptions`、`schoolPairOptions` 和 `resetAdvancedFilters` 的位置，后续会被新 helper 替换。

- [ ] **步骤 2：确认职称拆分规则**

确认继续使用：

- `extractProfessorTitleTags`
- `matchesProfessorTitleTag`

职称多选必须基于拆分后的标签，而不是原始 title 字符串整体匹配。

- [ ] **步骤 3：确认列表选择语义**

确认当前 `filteredSelectableIds` 基于 `filteredProfessors`，并且“选择全部筛选结果”不是只选当前页。后续改筛选来源时必须保留这个语义。

---

### 任务 2：新增导师管理筛选 helper 和测试

**文件：**
- 新增：`frontend/src/features/professor-management/client/filterManagementProfessors.ts`
- 新增：`frontend/src/features/professor-management/client/filterManagementProfessors.test.ts`

- [ ] **步骤 1：定义筛选状态类型和默认值**

新增：

```ts
export type ProfessorManagementFilterState = {
  keyword: string;
  universities: string[];
  schools: string[];
  departments: string[];
  titles: string[];
};
```

新增：

```ts
export const createDefaultManagementFilters = (): ProfessorManagementFilterState => ({
  keyword: "",
  universities: [],
  schools: [],
  departments: [],
  titles: [],
});
```

- [ ] **步骤 2：实现选项生成**

新增 `buildManagementFilterOptions(professors, filters?)`，返回：

- `universities`
- `schools`
- `departments`
- `titles`

要求：

- 空值不生成选项。
- 学校、学院、系所按 `localeCompare("zh-CN")` 排序。
- 学院选项随已选学校收窄。
- 职称选项来自 `extractProfessorTitleTags(title)`。

- [ ] **步骤 3：实现高级筛选计数**

新增 `getActiveManagementAdvancedFilterCount(filters)`。

计数规则：

- 统计 `universities.length`
- 统计 `schools.length`
- 统计 `departments.length`
- 统计 `titles.length`
- 不统计 `keyword`

- [ ] **步骤 4：实现学校变化时的学院收窄 helper**

新增 `keepManagementSchoolsForUniversities(professors, schools, universities)` 或等价函数。

要求：

- 未选择学校时保留当前已选学院。
- 选择学校后，只保留仍属于可用学院选项的已选学院。

- [ ] **步骤 5：实现过滤逻辑**

新增 `filterManagementProfessors(professors, filters)`。

要求：

- 关键词匹配姓名、邮箱、学校、学院、系所、职称、研究方向。
- 同一筛选组内为“或”。
- 不同筛选组之间为“且”。
- 职称筛选使用拆分标签匹配。
- 不修改输入数组。

- [ ] **步骤 6：编写纯函数测试**

覆盖：

- 关键词匹配所有目标字段。
- 多学校、多学院、多系所、多职称筛选。
- 同组“或”、跨组“且”。
- 学校选择后学院选项收窄。
- 学校变化后无效已选学院被移除。
- 高级筛选计数不包含关键词。

- [ ] **步骤 7：运行筛选 helper 测试**

```bash
cd frontend
npm run test -- src/features/professor-management/client/filterManagementProfessors.test.ts
```

---

### 任务 3：新增导师管理排序 helper 和测试

**文件：**
- 新增：`frontend/src/features/professor-management/client/sortManagementProfessors.ts`
- 新增：`frontend/src/features/professor-management/client/sortManagementProfessors.test.ts`

- [ ] **步骤 1：定义排序 key 和选项**

新增：

```ts
export type ProfessorManagementSortKey =
  | "latest"
  | "updatedAtDesc"
  | "nameAsc"
  | "universityAsc";
```

新增排序选项：

- `latest`：最新导入。
- `updatedAtDesc`：最近更新。
- `nameAsc`：姓名 A-Z。
- `universityAsc`：学校 A-Z。

- [ ] **步骤 2：实现排序逻辑**

新增 `sortManagementProfessors(professors, sortKey)`。

要求：

- 排序前复制数组，不原地修改输入。
- `latest` 使用 `created_at` 降序。
- `updatedAtDesc` 使用 `updated_at` 降序。
- `nameAsc` 使用 `name.localeCompare`。
- `universityAsc` 优先比较 `university`，再比较 `name` 作为稳定兜底。
- 空学校排在后面。

- [ ] **步骤 3：编写排序测试**

覆盖：

- 每个排序 key 的结果。
- 空学校在学校排序中排后。
- 输入数组不被修改。

- [ ] **步骤 4：运行排序 helper 测试**

```bash
cd frontend
npm run test -- src/features/professor-management/client/sortManagementProfessors.test.ts
```

---

### 任务 4：改造 ProfessorsPage 状态和数据流

**文件：**
- 修改：`frontend/src/pages/ProfessorsPage.tsx`

- [ ] **步骤 1：替换旧筛选状态**

移除或停止使用：

- `titleFilter`
- `schoolPairFilter`
- `schoolPairOptions`
- `hasAdvancedFilters`
- `resetAdvancedFilters`

新增：

- `filters`
- `advancedFiltersOpen`
- `sortKey`

保留：

- `archiveFilter`
- `keyword` 可并入 `filters.keyword`，推荐并入统一对象。

- [ ] **步骤 2：接入筛选选项和计数**

在页面内通过 helper 计算：

- `filterOptions`
- `activeAdvancedFilterCount`

高级筛选按钮显示：

```tsx
高级筛选
{activeAdvancedFilterCount > 0 ? ` ${activeAdvancedFilterCount}` : ""}
```

- [ ] **步骤 3：接入筛选和排序结果**

数据流调整为：

1. `professors`
2. `filterManagementProfessors(professors, filters)`
3. `sortManagementProfessors(filteredProfessors, sortKey)`
4. 分页得到 `paginatedProfessors`

注意：

- `filteredSelectableIds` 应基于排序后的当前筛选全集，或者基于排序前的筛选全集也可以；两者集合一致。
- 分页展示使用排序后的结果。

- [ ] **步骤 4：页码重置依赖改造**

筛选或排序变化后回到第 1 页：

- `archiveFilter`
- `filters`
- `sortKey`

不要因为 `advancedFiltersOpen` 展开/收起重置页码。

- [ ] **步骤 5：实现重置行为**

重置按钮执行：

- `setFilters(createDefaultManagementFilters())`
- `setSortKey("latest")`

不能执行：

- `setArchiveFilter("active")`

- [ ] **步骤 6：保留无效选项清理**

当导师列表刷新或归档视图变化导致选项变化时：

- 已选学校如果不再存在，应从 `filters.universities` 中移除。
- 已选学院如果不再存在，应从 `filters.schools` 中移除。
- 已选系所如果不再存在，应从 `filters.departments` 中移除。
- 已选职称如果不再存在，应从 `filters.titles` 中移除。

实现时避免产生无限 `setFilters` 循环。

---

### 任务 5：改造筛选 UI

**文件：**
- 修改：`frontend/src/pages/ProfessorsPage.tsx`
- 复用：`frontend/src/components/molecules/MultiSelectFilter.tsx`
- 复用：`frontend/src/components/atoms/NativeSelectField.tsx`

- [ ] **步骤 1：保留搜索框但绑定统一筛选状态**

搜索框 placeholder 建议改为：

```text
搜索姓名、邮箱、学校、学院、系所、职称或研究方向
```

输入变更时更新 `filters.keyword`。

- [ ] **步骤 2：新增排序下拉**

在基础操作行中加入 `NativeSelectField`。

排序默认值为 `latest`。

- [ ] **步骤 3：替换旧两个单选下拉**

移除常驻：

- “职称 / 导师资格”单选下拉。
- “学校 / 学院”单选下拉。

替换为：

- “高级筛选”按钮。
- “重置”按钮。
- “刷新”按钮。

- [ ] **步骤 4：新增高级筛选面板**

`advancedFiltersOpen` 为 true 时展示面板。

面板内使用 `MultiSelectFilter`：

- 学校：`filters.universities`
- 学院：`filters.schools`
- 系所：`filters.departments`
- 职称 / 导师资格：`filters.titles`

不新增“清空高级筛选”按钮。

- [ ] **步骤 5：实现学校与学院联动**

学校多选切换时：

- 更新 `filters.universities`。
- 使用 helper 收窄 `filters.schools`。

学院多选只在当前可用学院选项中切换。

- [ ] **步骤 6：保持视觉层级**

要求：

- 搜索框可继续独占一行。
- 排序、高级筛选、重置、刷新在同一操作行。
- 高级筛选面板使用轻量边框和白底，不嵌套卡片。
- 归档视图切换仍在筛选区外层或相邻独立区域，不进入高级筛选面板。

---

### 任务 6：更新页面级测试

**文件：**
- 视情况修改：`frontend/src/pages/SelectionControls.test.tsx`
- 视情况新增或修改：`frontend/src/pages/ProfessorsPage.test.tsx`

- [ ] **步骤 1：检查现有测试覆盖**

搜索导师管理页相关测试：

```bash
cd frontend
rg -n "ProfessorsPage|导师管理|选择全部筛选结果|职称筛选|学校学院筛选" src
```

如果已有测试依赖旧的“职称筛选”或“学校学院筛选” aria label，需要更新为新交互。

- [ ] **步骤 2：覆盖重置不切换归档视图**

新增或调整测试：

1. 切到“已删除”。
2. 输入关键词或选择高级筛选。
3. 点击“重置”。
4. 断言仍停留在“已删除”视图。

- [ ] **步骤 3：覆盖高级筛选入口**

测试：

1. 默认不展示高级筛选面板。
2. 点击“高级筛选”后展示学校、学院、系所、职称多选。
3. 选择筛选项后按钮显示计数。

- [ ] **步骤 4：覆盖选择全部筛选结果语义**

确认筛选改造后：

- “选择全部筛选结果”仍选择所有过滤后的可选导师。
- 翻页不丢失已选状态。

---

### 任务 7：验证与收尾

**文件：**
- 验证：前端测试、lint、build

- [ ] **步骤 1：运行新增 helper 测试**

```bash
cd frontend
npm run test -- src/features/professor-management/client/filterManagementProfessors.test.ts
npm run test -- src/features/professor-management/client/sortManagementProfessors.test.ts
```

- [ ] **步骤 2：运行相关页面测试**

```bash
cd frontend
npm run test -- src/pages/SelectionControls.test.tsx
```

如果新增 `ProfessorsPage.test.tsx`，也运行对应测试文件。

- [ ] **步骤 3：运行 lint 和 build**

```bash
cd frontend
npm run lint
npm run build
```

- [ ] **步骤 4：人工验证**

启动前端：

```bash
cd frontend
npm run dev
```

手动检查：

1. 默认进入导师管理页时，高级筛选收起。
2. 搜索框能匹配姓名、邮箱、学校、学院、系所、职称和研究方向。
3. 点击“高级筛选”后可以多选学校、学院、系所和职称。
4. 学校变化后，学院选项自动收窄。
5. 点击“重置”后关键词、高级筛选和排序恢复默认。
6. 在“已删除”视图点击“重置”后仍停留在“已删除”。
7. 筛选结果跨页时，“选择全部筛选结果”仍跨页生效。

---

## 实施顺序建议

1. 先做纯函数 helper 和测试。
2. 再接入 `ProfessorsPage.tsx` 的数据流。
3. 然后替换 UI。
4. 最后补页面测试和人工验证。

这样可以把筛选规则和页面布局分开验证，减少在大页面里同时改状态、UI 和行为带来的回归风险。

## 完成标准

- 导师管理页不再常驻展示旧的两个单选筛选下拉。
- 高级筛选面板可展开、可收起，支持 4 类多选。
- “高级筛选 N”计数与已选高级筛选项一致。
- “重置”不影响归档视图。
- 筛选、排序、分页、跨页选择语义正确。
- 相关测试、lint、build 通过。
