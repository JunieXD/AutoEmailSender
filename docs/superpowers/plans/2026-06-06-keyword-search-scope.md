# 关键词搜索范围实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 在首页和导师管理页关键词搜索框旁增加可持久化的“搜索范围”下拉多选，并让关键词只匹配已选字段。

**架构：** 搜索范围作为各页面筛选状态的一部分，由过滤模块定义字段 key、默认值和归一化函数。新增复用 UI 组件只负责下拉多选交互和“至少保留最后一项”约束，页面负责把选中 key 接入现有 filters。

**技术栈：** React、TypeScript、Vite、Vitest、Testing Library、Tailwind CSS、lucide-react。

---

## 文件职责

- `frontend/src/features/home-dashboard/client/filterDashboardProfessors.ts`：定义首页搜索范围类型、默认字段、归一化函数，并按选中字段执行关键词匹配。
- `frontend/src/features/home-dashboard/client/filterDashboardProfessors.test.ts`：覆盖首页默认全字段、仅姓名、空关键词和非法持久化值归一化。
- `frontend/src/features/professor-management/client/filterManagementProfessors.ts`：定义导师管理页搜索范围类型、默认字段、归一化函数，并额外支持邮箱字段匹配。
- `frontend/src/features/professor-management/client/filterManagementProfessors.test.ts`：覆盖导师管理页默认全字段、仅邮箱和非法持久化值归一化。
- `frontend/src/components/molecules/KeywordSearchScopeSelect.tsx`：新增复用下拉多选组件，显示“全部字段”或“已选 N 项”，禁止取消最后一项并展示提示。
- `frontend/src/components/molecules/KeywordSearchScopeSelect.test.tsx`：覆盖组件摘要、提示和最后一项不可取消行为。
- `frontend/src/pages/HomePage.tsx`：把首页搜索范围接入筛选状态读取、写入、重置和关键词筛选区 UI。
- `frontend/src/pages/ProfessorsPage.tsx`：把导师管理页搜索范围接入筛选状态读取、写入、重置和关键词筛选区 UI。

## 任务 1：首页过滤逻辑

**文件：**
- 修改：`frontend/src/features/home-dashboard/client/filterDashboardProfessors.test.ts`
- 修改：`frontend/src/features/home-dashboard/client/filterDashboardProfessors.ts`

- [ ] **步骤 1：编写失败的测试**

在 `filterDashboardProfessors.test.ts` 中补充：

```typescript
it("keeps default keyword matching across every searchable dashboard field", () => {
  expect(namesFor(professors, { keyword: "副教授" })).toEqual(["Bob"]);
  expect(namesFor(professors, { keyword: "robotics" })).toEqual(["Carol"]);
});

it("matches keyword only within selected dashboard search scopes", () => {
  expect(
    namesFor(professors, {
      keyword: "副教授",
      keywordSearchScopes: ["name"],
    }),
  ).toEqual([]);
  expect(
    namesFor(professors, {
      keyword: "Alice",
      keywordSearchScopes: ["name"],
    }),
  ).toEqual(["Alice"]);
});

it("ignores dashboard search scopes when keyword is empty", () => {
  expect(
    namesFor(professors, {
      keyword: "",
      keywordSearchScopes: ["name"],
    }),
  ).toEqual(["Alice", "Bob", "Carol"]);
});

it("normalizes invalid dashboard search scopes to every field", () => {
  expect(normalizeDashboardKeywordSearchScopes(["unknown"])).toEqual(
    DEFAULT_DASHBOARD_KEYWORD_SEARCH_SCOPES,
  );
});
```

- [ ] **步骤 2：运行测试验证失败**

运行：

```bash
cd frontend
npm run test -- src/features/home-dashboard/client/filterDashboardProfessors.test.ts
```

预期：FAIL，失败原因包括 `keywordSearchScopes` 类型不存在或 `normalizeDashboardKeywordSearchScopes` 未导出。

- [ ] **步骤 3：编写最少实现代码**

在 `filterDashboardProfessors.ts` 中新增：

```typescript
export const DASHBOARD_KEYWORD_SEARCH_SCOPE_OPTIONS = [
  { value: "name", label: "姓名" },
  { value: "university", label: "学校" },
  { value: "school", label: "学院" },
  { value: "department", label: "系所" },
  { value: "title", label: "职称" },
  { value: "researchDirection", label: "研究方向" },
] as const;

export type DashboardKeywordSearchScope =
  (typeof DASHBOARD_KEYWORD_SEARCH_SCOPE_OPTIONS)[number]["value"];

export const DEFAULT_DASHBOARD_KEYWORD_SEARCH_SCOPES =
  DASHBOARD_KEYWORD_SEARCH_SCOPE_OPTIONS.map((option) => option.value);
```

把 `DashboardFilterState` 扩展为：

```typescript
keywordSearchScopes: DashboardKeywordSearchScope[];
```

并实现 `normalizeDashboardKeywordSearchScopes(value: unknown): DashboardKeywordSearchScope[]`，丢弃非法值，结果为空时返回默认全选。`filterDashboardProfessors` 根据选中 scope 构造字段值数组。

- [ ] **步骤 4：运行测试验证通过**

运行同一步骤 2 命令。

预期：PASS。

## 任务 2：导师管理页过滤逻辑

**文件：**
- 修改：`frontend/src/features/professor-management/client/filterManagementProfessors.test.ts`
- 修改：`frontend/src/features/professor-management/client/filterManagementProfessors.ts`

- [ ] **步骤 1：编写失败的测试**

在 `filterManagementProfessors.test.ts` 中补充：

```typescript
it("matches keyword only within selected management search scopes", () => {
  expect(
    namesFor(professors, {
      keyword: "bob@example.edu",
      keywordSearchScopes: ["email"],
    }),
  ).toEqual(["Bob"]);
  expect(
    namesFor(professors, {
      keyword: "副教授",
      keywordSearchScopes: ["name"],
    }),
  ).toEqual([]);
});

it("normalizes invalid management search scopes to every field", () => {
  expect(normalizeManagementKeywordSearchScopes(["unknown"])).toEqual(
    DEFAULT_MANAGEMENT_KEYWORD_SEARCH_SCOPES,
  );
});
```

- [ ] **步骤 2：运行测试验证失败**

运行：

```bash
cd frontend
npm run test -- src/features/professor-management/client/filterManagementProfessors.test.ts
```

预期：FAIL，失败原因包括新增类型或归一化函数不存在。

- [ ] **步骤 3：编写最少实现代码**

在 `filterManagementProfessors.ts` 中定义 `MANAGEMENT_KEYWORD_SEARCH_SCOPE_OPTIONS`，字段比首页多 `email`，并导出 `ProfessorManagementKeywordSearchScope`、`DEFAULT_MANAGEMENT_KEYWORD_SEARCH_SCOPES`、`normalizeManagementKeywordSearchScopes`。扩展 `ProfessorManagementFilterState`，并让 `filterManagementProfessors` 按选中字段匹配关键词。

- [ ] **步骤 4：运行测试验证通过**

运行同一步骤 2 命令。

预期：PASS。

## 任务 3：搜索范围下拉组件

**文件：**
- 创建：`frontend/src/components/molecules/KeywordSearchScopeSelect.test.tsx`
- 创建：`frontend/src/components/molecules/KeywordSearchScopeSelect.tsx`

- [ ] **步骤 1：编写失败的测试**

创建组件测试，覆盖：

```typescript
it("shows all fields summary when every scope is selected", () => {
  render(<KeywordSearchScopeSelect label="搜索范围" options={options} selectedValues={["name", "title"]} onChange={onChange} />);
  expect(screen.getByRole("button", { name: "搜索范围：全部字段" })).toBeInTheDocument();
});

it("shows selected count and keeps the last selected option", () => {
  render(<KeywordSearchScopeSelect label="搜索范围" options={options} selectedValues={["name"]} onChange={onChange} />);
  fireEvent.click(screen.getByRole("button", { name: "搜索范围：已选 1 项" }));
  expect(screen.getByText("至少保留最后一项")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("option", { name: "姓名" }));
  expect(onChange).not.toHaveBeenCalled();
});
```

- [ ] **步骤 2：运行测试验证失败**

运行：

```bash
cd frontend
npm run test -- src/components/molecules/KeywordSearchScopeSelect.test.tsx
```

预期：FAIL，模块不存在。

- [ ] **步骤 3：编写最少实现代码**

实现 `KeywordSearchScopeSelect`：

- props：`label`、`options: { value: string; label: string }[]`、`selectedValues`、`onChange`、可选 `disabled`。
- 摘要：全选显示“全部字段”，否则显示“已选 N 项”。
- 点击选项时，如果已选且当前只剩 1 项，不调用 `onChange`。
- 下拉底部固定显示“至少保留最后一项”。

- [ ] **步骤 4：运行测试验证通过**

运行同一步骤 2 命令。

预期：PASS。

## 任务 4：接入首页

**文件：**
- 修改：`frontend/src/pages/HomePage.tsx`
- 修改：`frontend/src/features/home-dashboard/client/filterDashboardProfessors.ts`

- [ ] **步骤 1：编写失败的持久化兼容测试或类型检查用例**

优先使用任务 1 的归一化测试覆盖持久化兼容。页面接入前确认 `readStoredDashboardFilters` 会调用 `normalizeDashboardKeywordSearchScopes(filters?.keywordSearchScopes)`。

- [ ] **步骤 2：实现首页接入**

在 `HomePage.tsx`：

- import `KeywordSearchScopeSelect`。
- import `DASHBOARD_KEYWORD_SEARCH_SCOPE_OPTIONS` 和 `normalizeDashboardKeywordSearchScopes`。
- `readStoredDashboardFilters` 读取 `keywordSearchScopes`。
- 关键词输入框容器内把 input 和 `KeywordSearchScopeSelect` 并排放置。
- `onChange` 调用 `updateFilters({ keywordSearchScopes: nextScopes })`。

- [ ] **步骤 3：运行首页相关测试**

运行：

```bash
cd frontend
npm run test -- src/features/home-dashboard/client/filterDashboardProfessors.test.ts
```

预期：PASS。

## 任务 5：接入导师管理页

**文件：**
- 修改：`frontend/src/pages/ProfessorsPage.tsx`
- 修改：`frontend/src/features/professor-management/client/filterManagementProfessors.ts`

- [ ] **步骤 1：编写失败的持久化兼容测试或类型检查用例**

优先使用任务 2 的归一化测试覆盖持久化兼容。页面接入前确认 `readStoredProfessorManagementState` 会调用 `normalizeManagementKeywordSearchScopes(filters?.keywordSearchScopes)`。

- [ ] **步骤 2：实现导师管理页接入**

在 `ProfessorsPage.tsx`：

- import `KeywordSearchScopeSelect`。
- import `MANAGEMENT_KEYWORD_SEARCH_SCOPE_OPTIONS` 和 `normalizeManagementKeywordSearchScopes`。
- `readStoredProfessorManagementState` 读取 `keywordSearchScopes`。
- 关键词输入框右侧加入搜索范围下拉。
- `onChange` 调用 `updateFilters({ keywordSearchScopes: nextScopes })`。

- [ ] **步骤 3：运行导师管理相关测试**

运行：

```bash
cd frontend
npm run test -- src/features/professor-management/client/filterManagementProfessors.test.ts
```

预期：PASS。

## 任务 6：最终验证与提交

**文件：**
- 检查：所有修改文件

- [ ] **步骤 1：运行聚焦测试**

```bash
cd frontend
npm run test -- src/features/home-dashboard/client/filterDashboardProfessors.test.ts
npm run test -- src/features/professor-management/client/filterManagementProfessors.test.ts
npm run test -- src/components/molecules/KeywordSearchScopeSelect.test.tsx
```

预期：全部 PASS。

- [ ] **步骤 2：运行静态验证**

```bash
cd frontend
npm run lint
npm run build
```

预期：命令退出码为 0。

- [ ] **步骤 3：检查 diff**

```bash
git diff --stat
git diff -- frontend/src/features/home-dashboard/client/filterDashboardProfessors.ts frontend/src/features/professor-management/client/filterManagementProfessors.ts frontend/src/components/molecules/KeywordSearchScopeSelect.tsx frontend/src/pages/HomePage.tsx frontend/src/pages/ProfessorsPage.tsx
```

预期：只包含搜索范围相关改动。

- [ ] **步骤 4：Commit**

```bash
git add frontend/src/features/home-dashboard/client/filterDashboardProfessors.ts frontend/src/features/home-dashboard/client/filterDashboardProfessors.test.ts frontend/src/features/professor-management/client/filterManagementProfessors.ts frontend/src/features/professor-management/client/filterManagementProfessors.test.ts frontend/src/components/molecules/KeywordSearchScopeSelect.tsx frontend/src/components/molecules/KeywordSearchScopeSelect.test.tsx frontend/src/pages/HomePage.tsx frontend/src/pages/ProfessorsPage.tsx docs/superpowers/plans/2026-06-06-keyword-search-scope.md
git commit -m "feat(搜索): 添加关键词搜索范围"
```
