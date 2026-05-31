# 首页导师看板排序实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 在首页导师看板增加“排序”下拉框，支持对当前筛选结果按最新导入、匹配度、发送次数、姓名排序。

**架构：** 排序仍在前端本地完成。新增一个专注的排序 helper 承载排序选项和比较规则，并用 Vitest 覆盖空匹配度、默认顺序、数值降序和姓名升序；`HomePage.tsx` 只新增排序状态、下拉框和排序后结果引用。

**技术栈：** React 19、TypeScript、Vite、Vitest、现有 `NativeSelectField` 组件。

---

## 文件结构

- 创建：`frontend/src/features/home-dashboard/client/sortDashboardProfessors.ts`
  - 职责：定义首页导师排序 key、排序选项和纯函数 `sortDashboardProfessors`。
- 创建：`frontend/src/features/home-dashboard/client/sortDashboardProfessors.test.ts`
  - 职责：验证排序规则，不依赖 React 渲染。
- 修改：`frontend/src/pages/HomePage.tsx`
  - 职责：新增排序状态、排序下拉框，并让列表统计、全选当前结果和渲染使用排序后的当前结果。

## 任务 1：编写排序规则测试

**文件：**
- 创建：`frontend/src/features/home-dashboard/client/sortDashboardProfessors.test.ts`

- [ ] **步骤 1：创建失败测试**

写入以下测试文件：

```ts
import { describe, expect, it } from "vitest";
import type { ProfessorDashboardItemDTO } from "@/types";
import {
  sortDashboardProfessors,
  type ProfessorDashboardSortKey,
} from "./sortDashboardProfessors";

const buildProfessor = (
  overrides: Partial<ProfessorDashboardItemDTO>,
): ProfessorDashboardItemDTO => ({
  id: 1,
  name: "Default",
  email: null,
  title: null,
  university: null,
  school: null,
  department: null,
  research_direction: null,
  recent_papers: [],
  match_score: null,
  sent_count: 0,
  status: "not_contacted",
  ...overrides,
});

const namesFor = (
  sortKey: ProfessorDashboardSortKey,
  professors: ProfessorDashboardItemDTO[],
) => sortDashboardProfessors(professors, sortKey).map((professor) => professor.name);

describe("sortDashboardProfessors", () => {
  it("keeps backend order for latest import", () => {
    const professors = [
      buildProfessor({ id: 1, name: "First" }),
      buildProfessor({ id: 2, name: "Second" }),
      buildProfessor({ id: 3, name: "Third" }),
    ];

    expect(namesFor("latest", professors)).toEqual(["First", "Second", "Third"]);
  });

  it("sorts by match score descending and places null scores last", () => {
    const professors = [
      buildProfessor({ id: 1, name: "Unscored", match_score: null }),
      buildProfessor({ id: 2, name: "Strong", match_score: 92 }),
      buildProfessor({ id: 3, name: "Medium", match_score: 76 }),
    ];

    expect(namesFor("matchScoreDesc", professors)).toEqual([
      "Strong",
      "Medium",
      "Unscored",
    ]);
  });

  it("sorts by sent count descending", () => {
    const professors = [
      buildProfessor({ id: 1, name: "None", sent_count: 0 }),
      buildProfessor({ id: 2, name: "Many", sent_count: 4 }),
      buildProfessor({ id: 3, name: "One", sent_count: 1 }),
    ];

    expect(namesFor("sentCountDesc", professors)).toEqual(["Many", "One", "None"]);
  });

  it("sorts names ascending", () => {
    const professors = [
      buildProfessor({ id: 1, name: "Zhang" }),
      buildProfessor({ id: 2, name: "Alice" }),
      buildProfessor({ id: 3, name: "Bob" }),
    ];

    expect(namesFor("nameAsc", professors)).toEqual(["Alice", "Bob", "Zhang"]);
  });

  it("does not mutate the input array", () => {
    const professors = [
      buildProfessor({ id: 1, name: "Unscored", match_score: null }),
      buildProfessor({ id: 2, name: "Strong", match_score: 92 }),
    ];

    sortDashboardProfessors(professors, "matchScoreDesc");

    expect(professors.map((professor) => professor.name)).toEqual([
      "Unscored",
      "Strong",
    ]);
  });
});
```

- [ ] **步骤 2：运行测试验证失败**

运行：

```bash
cd frontend
npm run test -- src/features/home-dashboard/client/sortDashboardProfessors.test.ts
```

预期：FAIL，报错包含 `Failed to resolve import "./sortDashboardProfessors"` 或同义的模块不存在信息。

## 任务 2：实现排序 helper

**文件：**
- 创建：`frontend/src/features/home-dashboard/client/sortDashboardProfessors.ts`
- 测试：`frontend/src/features/home-dashboard/client/sortDashboardProfessors.test.ts`

- [ ] **步骤 1：实现最少排序代码**

创建 `sortDashboardProfessors.ts`：

```ts
import type { ProfessorDashboardItemDTO } from "@/types";

export type ProfessorDashboardSortKey =
  | "latest"
  | "matchScoreDesc"
  | "sentCountDesc"
  | "nameAsc";

export const PROFESSOR_DASHBOARD_SORT_OPTIONS: Array<{
  value: ProfessorDashboardSortKey;
  label: string;
}> = [
  { value: "latest", label: "最新导入" },
  { value: "matchScoreDesc", label: "匹配度高到低" },
  { value: "sentCountDesc", label: "发送次数高到低" },
  { value: "nameAsc", label: "姓名 A-Z" },
];

export const sortDashboardProfessors = (
  professors: ProfessorDashboardItemDTO[],
  sortKey: ProfessorDashboardSortKey,
): ProfessorDashboardItemDTO[] => {
  const sorted = [...professors];

  if (sortKey === "matchScoreDesc") {
    return sorted.sort(
      (left, right) => (right.match_score ?? -1) - (left.match_score ?? -1),
    );
  }

  if (sortKey === "sentCountDesc") {
    return sorted.sort((left, right) => right.sent_count - left.sent_count);
  }

  if (sortKey === "nameAsc") {
    return sorted.sort((left, right) => left.name.localeCompare(right.name));
  }

  return sorted;
};
```

- [ ] **步骤 2：运行排序单测验证通过**

运行：

```bash
cd frontend
npm run test -- src/features/home-dashboard/client/sortDashboardProfessors.test.ts
```

预期：PASS，5 个测试通过。

- [ ] **步骤 3：Commit 排序 helper**

运行：

```bash
git add frontend/src/features/home-dashboard/client/sortDashboardProfessors.ts frontend/src/features/home-dashboard/client/sortDashboardProfessors.test.ts
git commit -m "feat(frontend): add home dashboard sort helper"
```

预期：提交成功，提交只包含排序 helper 和单测。

## 任务 3：接入首页排序控件

**文件：**
- 修改：`frontend/src/pages/HomePage.tsx`
- 依赖：`frontend/src/features/home-dashboard/client/sortDashboardProfessors.ts`

- [ ] **步骤 1：导入排序 helper**

在 `HomePage.tsx` 顶部现有 imports 中增加：

```ts
import {
  PROFESSOR_DASHBOARD_SORT_OPTIONS,
  sortDashboardProfessors,
  type ProfessorDashboardSortKey,
} from "@/features/home-dashboard/client/sortDashboardProfessors";
```

- [ ] **步骤 2：新增排序状态**

在现有筛选状态附近增加：

```ts
const [sortKey, setSortKey] = useState<ProfessorDashboardSortKey>("latest");
```

- [ ] **步骤 3：新增排序后的当前结果**

保留现有 `filteredProfessors` 计算逻辑，在它后面增加：

```ts
const visibleProfessors = sortDashboardProfessors(filteredProfessors, sortKey);
```

- [ ] **步骤 4：增加排序下拉框**

把筛选区网格从 `md:grid-cols-3` 改为 `md:grid-cols-4`，在状态下拉框后增加：

```tsx
<NativeSelectField
  label="排序"
  value={sortKey}
  onChange={(event) => setSortKey(event.target.value as ProfessorDashboardSortKey)}
  wrapperClassName="rounded-2xl border border-stone-200 bg-white px-4 py-3 text-sm text-stone-600 shadow-sm"
  shellClassName="border-0 bg-transparent px-0 py-0 shadow-none"
>
  {PROFESSOR_DASHBOARD_SORT_OPTIONS.map((option) => (
    <option key={option.value} value={option.value}>
      {option.label}
    </option>
  ))}
</NativeSelectField>
```

- [ ] **步骤 5：让列表使用排序后的结果**

把首页中面向用户可见结果的引用从 `filteredProfessors` 改为 `visibleProfessors`：

```tsx
共 {visibleProfessors.length} 位导师，已选择 {selectedIds.size} 位
```

```tsx
onClick={() => setSelectedIds(new Set(visibleProfessors.map((item) => item.id)))}
```

```tsx
) : visibleProfessors.length === 0 ? (
```

```tsx
{visibleProfessors.map((professor) => (
```

保留 `filteredProfessors` 作为排序前的筛选结果，不删除。

- [ ] **步骤 6：运行前端检查**

运行：

```bash
cd frontend
npm run test -- src/features/home-dashboard/client/sortDashboardProfessors.test.ts
npm run lint
```

预期：排序单测 PASS，lint 无错误。

- [ ] **步骤 7：Commit 首页接入**

运行：

```bash
git add frontend/src/pages/HomePage.tsx
git commit -m "feat(frontend): add home dashboard sort control"
```

预期：提交成功，提交只包含首页接入排序控件。

## 任务 4：最终验证

**文件：**
- 验证：`frontend/src/pages/HomePage.tsx`
- 验证：`frontend/src/features/home-dashboard/client/sortDashboardProfessors.ts`
- 验证：`frontend/src/features/home-dashboard/client/sortDashboardProfessors.test.ts`

- [ ] **步骤 1：运行完整前端验证**

运行：

```bash
cd frontend
npm run test -- src/features/home-dashboard/client/sortDashboardProfessors.test.ts
npm run lint
```

预期：测试和 lint 均通过。

- [ ] **步骤 2：人工检查首页行为**

启动前端：

```bash
cd frontend
npm run dev
```

在浏览器打开 Vite 输出的本地地址，检查：

- 默认排序显示“最新导入”，初始列表顺序与改动前一致。
- “匹配度高到低”把未计算匹配度的导师放到最后。
- “发送次数高到低”把发送次数多的导师放到前面。
- “姓名 A-Z”按姓名升序展示。
- 关键词、学校、状态筛选与排序可以组合使用。
- “全选当前结果”选择的是当前筛选后的导师集合。

- [ ] **步骤 3：检查工作区差异**

运行：

```bash
git status --short
git diff --stat
```

预期：只包含本计划列出的前端实现文件变化；如果已按任务提交，则工作区干净。
