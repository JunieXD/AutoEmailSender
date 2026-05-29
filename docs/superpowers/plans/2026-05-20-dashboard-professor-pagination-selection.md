# 首页与导师管理分页选择实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 将首页和导师管理页统一改为每页 10 条，并让主选择操作选择全部筛选结果，而不是当前页。

**架构：** 继续使用前端本地分页，不新增后端分页接口。首页在筛选和排序后生成完整筛选结果 ID 集合，再切出当前页渲染；导师管理页复用现有分页结构，只把页大小和选择范围从当前页改为全部筛选结果。

**技术栈：** React、TypeScript、Vite、Vitest、Testing Library、现有 `frontend/src/lib/pagination.ts` 分页工具。

---

## 文件结构

- 修改：`frontend/src/lib/pagination.ts`
  - 将默认分页大小从 8 调整为 10，供已有分页工具统一使用。
- 修改：`frontend/src/lib/pagination.test.ts`
  - 更新默认页大小测试，确保 `PAGE_SIZE` 为 10。
- 修改：`frontend/src/pages/HomePage.tsx`
  - 添加首页当前页状态、分页切片、分页控件和「选择全部筛选结果」语义。
- 修改：`frontend/src/pages/ProfessorsPage.tsx`
  - 将导师管理页每页数量改为 10，并把选择范围改为全部筛选结果。
- 修改：`frontend/src/pages/SelectionControls.test.tsx`
  - 扩展测试数据到 11 条以上，验证分页只渲染当前页、全选覆盖全部筛选结果、文案更新。

## 当前代码参考

- 首页全量渲染位置：`frontend/src/pages/HomePage.tsx:946`
- 首页当前选择按钮：`frontend/src/pages/HomePage.tsx:897`
- 导师管理页当前每页数量：`frontend/src/pages/ProfessorsPage.tsx:77`
- 导师管理页当前分页切片：`frontend/src/pages/ProfessorsPage.tsx:457`
- 导师管理页当前页选择 ID：`frontend/src/pages/ProfessorsPage.tsx:467`
- 现有选择控件测试：`frontend/src/pages/SelectionControls.test.tsx:174`

---

### 任务 1：更新分页默认大小

**文件：**
- 修改：`frontend/src/lib/pagination.ts`
- 测试：`frontend/src/lib/pagination.test.ts`

- [ ] **步骤 1：编写失败的测试**

修改 `frontend/src/lib/pagination.test.ts`，在 `describe('pagination')` 内新增测试：

```typescript
it('uses ten items as the default page size', () => {
  expect(PAGE_SIZE).toBe(10);
});
```

- [ ] **步骤 2：运行测试验证失败**

运行：

```bash
cd frontend && npm run test -- src/lib/pagination.test.ts
```

预期：FAIL，`expected 8 to be 10`。

- [ ] **步骤 3：编写最少实现代码**

修改 `frontend/src/lib/pagination.ts`：

```typescript
export const PAGE_SIZE = 10;
```

- [ ] **步骤 4：运行测试验证通过**

运行：

```bash
cd frontend && npm run test -- src/lib/pagination.test.ts
```

预期：PASS，`pagination.test.ts` 全部通过。

- [ ] **步骤 5：Commit**

```bash
git add frontend/src/lib/pagination.ts frontend/src/lib/pagination.test.ts
git commit -m "feat(frontend): set default page size to ten"
```

---

### 任务 2：首页分页展示

**文件：**
- 修改：`frontend/src/pages/HomePage.tsx`
- 测试：`frontend/src/pages/SelectionControls.test.tsx`

- [ ] **步骤 1：扩展首页测试数据**

在 `frontend/src/pages/SelectionControls.test.tsx` 中添加辅助函数，生成超过 10 条首页导师：

```typescript
const createDashboardProfessor = (
  id: number,
  name = `导师 ${id}`,
): ProfessorDashboardItemDTO => ({
  id,
  name,
  email: `professor-${id}@example.edu`,
  title: id % 2 === 0 ? "教授" : "副教授",
  university: "示例大学",
  school: id % 2 === 0 ? "计算机学院" : "软件学院",
  department: "人工智能系",
  research_direction: "自然语言处理",
  recent_papers: [`Paper ${id}`],
  match_score: null,
  sent_count: 0,
  status: "not_contacted",
});
```

将 `dashboardProfessors` 改为：

```typescript
const dashboardProfessors: ProfessorDashboardItemDTO[] = Array.from(
  { length: 11 },
  (_, index) => createDashboardProfessor(index + 11),
);
```

- [ ] **步骤 2：编写首页分页失败测试**

在 `describe("selection controls", () => { ... })` 内新增测试：

```typescript
it("paginates home professors with ten items per page", async () => {
  render(
    <MemoryRouter>
      <HomePage />
    </MemoryRouter>,
  );

  expect(await screen.findByText("导师 11")).toBeInTheDocument();
  expect(screen.getByText("导师 20")).toBeInTheDocument();
  expect(screen.queryByText("导师 21")).not.toBeInTheDocument();
  expect(screen.getByText(/第 1 \/ 2 页/)).toBeInTheDocument();

  fireEvent.click(screen.getByRole("button", { name: "下一页" }));

  expect(await screen.findByText("导师 21")).toBeInTheDocument();
  expect(screen.queryByText("导师 11")).not.toBeInTheDocument();
});
```

- [ ] **步骤 3：运行测试验证失败**

运行：

```bash
cd frontend && npm run test -- src/pages/SelectionControls.test.tsx
```

预期：FAIL。失败原因是首页未提供分页控件，且第 11 条导师仍在第一页渲染。

- [ ] **步骤 4：实现首页分页状态和切片**

修改 `frontend/src/pages/HomePage.tsx`：

1. 添加导入：

```typescript
import { getPageItems, getTotalPages, PAGE_SIZE } from "@/lib/pagination";
```

2. 在组件状态区添加当前页：

```typescript
const [currentPage, setCurrentPage] = useState(1);
```

3. 将现有 `visibleProfessors` 保留为完整筛选排序结果，新增分页结果：

```typescript
const visibleProfessors = sortDashboardProfessors(
  filteredProfessors,
  sortKey,
);
const totalPages = getTotalPages(visibleProfessors.length, PAGE_SIZE);
const safeCurrentPage = Math.min(currentPage, totalPages);
const pagedProfessors = getPageItems(
  visibleProfessors,
  safeCurrentPage,
  PAGE_SIZE,
);
```

4. 添加页码重置 effect：

```typescript
useEffect(() => {
  setCurrentPage(1);
}, [filters, sortKey, professorsRequestKey]);
```

5. 将列表渲染从 `visibleProfessors.map` 改为：

```typescript
{pagedProfessors.map((professor) => (
```

6. 空状态仍判断完整筛选结果：

```typescript
) : visibleProfessors.length === 0 ? (
```

- [ ] **步骤 5：添加首页分页控件**

在首页导师列表 section 底部、列表内容之后添加分页栏：

```tsx
{!loading && visibleProfessors.length > 0 ? (
  <div className="flex flex-col gap-3 border-t border-stone-100 px-6 py-4 sm:flex-row sm:items-center sm:justify-between">
    <div className="text-sm text-stone-500">
      共 {visibleProfessors.length} 位符合筛选条件，当前第 {safeCurrentPage} / {totalPages} 页，已选择 {selectedIds.size} 位
    </div>
    <div className="flex items-center gap-2">
      <button
        type="button"
        onClick={() => setCurrentPage(safeCurrentPage - 1)}
        disabled={safeCurrentPage <= 1}
        className="ui-btn-secondary px-3 py-2 text-sm disabled:cursor-not-allowed disabled:opacity-50"
      >
        上一页
      </button>
      <button
        type="button"
        onClick={() => setCurrentPage(safeCurrentPage + 1)}
        disabled={safeCurrentPage >= totalPages}
        className="ui-btn-secondary px-3 py-2 text-sm disabled:cursor-not-allowed disabled:opacity-50"
      >
        下一页
      </button>
    </div>
  </div>
) : null}
```

- [ ] **步骤 6：运行测试验证通过**

运行：

```bash
cd frontend && npm run test -- src/pages/SelectionControls.test.tsx
```

预期：新增首页分页测试通过。若旧测试因文案尚未更新失败，先保留失败，任务 3 处理选择语义后统一通过。

- [ ] **步骤 7：Commit**

```bash
git add frontend/src/pages/HomePage.tsx frontend/src/pages/SelectionControls.test.tsx
git commit -m "feat(frontend): paginate home professor list"
```

---

### 任务 3：首页选择全部筛选结果

**文件：**
- 修改：`frontend/src/pages/HomePage.tsx`
- 测试：`frontend/src/pages/SelectionControls.test.tsx`

- [ ] **步骤 1：更新首页选择测试**

将原测试名 `keeps the home select-current-results action with the list selection area` 改为：

```typescript
it("selects all filtered home results across pages", async () => {
```

将按钮查找文案改为：

```typescript
const selectFilteredResults = await screen.findByRole("button", {
  name: "选择全部筛选结果",
});
```

点击后断言改为：

```typescript
fireEvent.click(selectFilteredResults);

expect(
  await screen.findByText("已选中 11 位导师"),
).toBeInTheDocument();
expect(
  screen.getByRole("button", { name: "清空选择" }),
).toBeInTheDocument();
```

取消按钮查找改为：

```typescript
fireEvent.click(
  screen.getByRole("button", { name: "取消选择全部筛选结果" }),
);

expect(screen.queryByText("已选中 11 位导师")).not.toBeInTheDocument();
```

- [ ] **步骤 2：运行测试验证失败**

运行：

```bash
cd frontend && npm run test -- src/pages/SelectionControls.test.tsx
```

预期：FAIL。失败原因是当前按钮仍为「选择当前结果」，且选择范围未使用新文案。

- [ ] **步骤 3：重命名首页选择变量**

修改 `frontend/src/pages/HomePage.tsx`，将完整筛选结果 ID 语义命名清晰：

```typescript
const filteredProfessorIds = visibleProfessors.map((item) => item.id);
const filteredSelectedCount = filteredProfessorIds.filter((id) =>
  selectedIds.has(id),
).length;
const allFilteredProfessorsSelected =
  filteredProfessorIds.length > 0 &&
  filteredSelectedCount === filteredProfessorIds.length;
```

- [ ] **步骤 4：替换首页选择处理函数**

用以下函数替换 `handleToggleVisibleProfessors`：

```typescript
const handleToggleFilteredProfessors = () => {
  setSelectedIds((previous) => {
    const next = new Set(previous);
    const allFilteredSelected =
      filteredProfessorIds.length > 0 &&
      filteredProfessorIds.every((id) => previous.has(id));

    if (allFilteredSelected) {
      filteredProfessorIds.forEach((id) => next.delete(id));
    } else {
      filteredProfessorIds.forEach((id) => next.add(id));
    }

    return next;
  });
};
```

- [ ] **步骤 5：更新首页按钮文案和状态**

将按钮状态和文案替换为：

```tsx
aria-label={
  allFilteredProfessorsSelected
    ? "取消选择全部筛选结果"
    : "选择全部筛选结果"
}
aria-pressed={allFilteredProfessorsSelected}
onClick={handleToggleFilteredProfessors}
```

按钮内容替换为：

```tsx
{allFilteredProfessorsSelected ? (
  <SquareCheck className="h-4 w-4" />
) : (
  <Square className="h-4 w-4" />
)}
{allFilteredProfessorsSelected
  ? "取消选择全部筛选结果"
  : "选择全部筛选结果"}
```

- [ ] **步骤 6：运行测试验证通过**

运行：

```bash
cd frontend && npm run test -- src/pages/SelectionControls.test.tsx
```

预期：首页相关测试通过。

- [ ] **步骤 7：Commit**

```bash
git add frontend/src/pages/HomePage.tsx frontend/src/pages/SelectionControls.test.tsx
git commit -m "feat(frontend): select all filtered home professors"
```

---

### 任务 4：导师管理页选择全部筛选结果

**文件：**
- 修改：`frontend/src/pages/ProfessorsPage.tsx`
- 测试：`frontend/src/pages/SelectionControls.test.tsx`

- [ ] **步骤 1：扩展导师管理测试数据**

在 `frontend/src/pages/SelectionControls.test.tsx` 中将 `managementProfessors` 基于新的 `dashboardProfessors` 生成，确保至少 11 条：

```typescript
const managementProfessors: ProfessorManagementItemDTO[] =
  dashboardProfessors.map((professor) => ({
    ...professor,
    profile_url: null,
    source_url: null,
    crawl_status: "manual",
    skip_reason: null,
    archived_at: null,
    created_at: "2026-05-01T00:00:00",
    updated_at: "2026-05-01T00:00:00",
  }));
```

- [ ] **步骤 2：更新导师管理失败测试**

将原测试名 `moves the management select-current-page action into the selection column header` 改为：

```typescript
it("selects all filtered management results across pages", async () => {
```

将按钮查找文案改为：

```typescript
const selectFilteredResults = within(tableHeader).getByRole("button", {
  name: "选择全部筛选结果",
});
```

点击后的断言改为：

```typescript
fireEvent.click(selectFilteredResults);

expect(
  await screen.findByText("已选中 11 位导师"),
).toBeInTheDocument();
expect(
  screen.getByRole("button", { name: "清空选择" }),
).toBeInTheDocument();
```

增加分页渲染断言：

```typescript
expect(screen.getByText("导师 11")).toBeInTheDocument();
expect(screen.getByText("导师 20")).toBeInTheDocument();
expect(screen.queryByText("导师 21")).not.toBeInTheDocument();
```

- [ ] **步骤 3：运行测试验证失败**

运行：

```bash
cd frontend && npm run test -- src/pages/SelectionControls.test.tsx
```

预期：FAIL。失败原因是导师管理页仍使用当前页选择文案和每页 20 条。

- [ ] **步骤 4：将导师管理每页数量改为 10**

修改 `frontend/src/pages/ProfessorsPage.tsx`：

```typescript
const PROFESSORS_PER_PAGE = 10;
```

- [ ] **步骤 5：替换导师管理选择 ID 集合**

将 `currentPageSelectableIds`、`currentPageSelectedCount`、`allCurrentPageSelected` 替换为筛选结果范围：

```typescript
const filteredSelectableIds = filteredProfessors
  .filter((professor) => !professor.archived_at)
  .map((professor) => professor.id);
const filteredSelectedCount = filteredSelectableIds.filter((id) =>
  selectedIds.has(id),
).length;
const allFilteredSelected =
  filteredSelectableIds.length > 0 &&
  filteredSelectedCount === filteredSelectableIds.length;
```

- [ ] **步骤 6：替换导师管理选择处理函数**

将使用 `currentPageSelectableIds` 的选择函数替换为：

```typescript
const handleToggleFilteredSelection = () => {
  setSelectedIds((previous) => {
    const next = new Set(previous);
    const allSelected =
      filteredSelectableIds.length > 0 &&
      filteredSelectableIds.every((id) => previous.has(id));

    if (allSelected) {
      filteredSelectableIds.forEach((id) => next.delete(id));
    } else {
      filteredSelectableIds.forEach((id) => next.add(id));
    }

    return next;
  });
};
```

- [ ] **步骤 7：更新导师管理按钮文案**

把表头和移动端工具栏中的选择按钮统一改为：

```tsx
aria-label={
  allFilteredSelected
    ? "取消选择全部筛选结果"
    : "选择全部筛选结果"
}
aria-pressed={allFilteredSelected}
onClick={handleToggleFilteredSelection}
disabled={filteredSelectableIds.length === 0}
```

按钮文本统一为：

```tsx
{allFilteredSelected ? "取消选择全部筛选结果" : "选择全部筛选结果"}
```

- [ ] **步骤 8：更新导师管理计数文案**

将底部分页统计从当前页数量改为完整筛选结果数量：

```tsx
共 {filteredProfessors.length} 位符合筛选条件，当前第 {safeCurrentPage} / {totalPages} 页，已选中 {selectedIds.size} 位
```

- [ ] **步骤 9：运行测试验证通过**

运行：

```bash
cd frontend && npm run test -- src/pages/SelectionControls.test.tsx
```

预期：导师管理选择和分页相关测试通过。

- [ ] **步骤 10：Commit**

```bash
git add frontend/src/pages/ProfessorsPage.tsx frontend/src/pages/SelectionControls.test.tsx
git commit -m "feat(frontend): select all filtered management professors"
```

---

### 任务 5：回归验证与收尾

**文件：**
- 验证：`frontend/src/pages/HomePage.tsx`
- 验证：`frontend/src/pages/ProfessorsPage.tsx`
- 验证：`frontend/src/pages/SelectionControls.test.tsx`
- 验证：`frontend/src/lib/pagination.test.ts`

- [ ] **步骤 1：运行聚焦测试**

```bash
cd frontend && npm run test -- src/lib/pagination.test.ts src/pages/SelectionControls.test.tsx
```

预期：PASS，分页工具和选择控件测试全部通过。

- [ ] **步骤 2：运行前端 lint**

```bash
cd frontend && npm run lint
```

预期：PASS，无新增 ESLint 错误。

- [ ] **步骤 3：运行前端测试套件**

```bash
cd frontend && npm run test
```

预期：PASS，前端 Vitest 测试通过。

- [ ] **步骤 4：人工检查交互**

启动前端：

```bash
cd frontend && npm run dev
```

检查：

- 首页每页最多显示 10 位导师。
- 首页点击「选择全部筛选结果」后，已选数量等于筛选结果总数。
- 首页翻页后已选状态保留。
- 导师管理页每页最多显示 10 位导师。
- 导师管理页点击「选择全部筛选结果」后，已选数量等于筛选结果总数。
- 导师管理页没有「选择当前页」主路径文案。

- [ ] **步骤 5：Commit 验证调整**

如果验证阶段产生代码或测试修复：

```bash
git add frontend/src/lib/pagination.ts frontend/src/lib/pagination.test.ts frontend/src/pages/HomePage.tsx frontend/src/pages/ProfessorsPage.tsx frontend/src/pages/SelectionControls.test.tsx
git commit -m "test(frontend): cover professor pagination selection"
```

如果没有产生新改动，不创建空 commit。
