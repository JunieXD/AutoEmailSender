# 关键词搜索范围实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 在首页导师看板和导师管理页的关键词搜索旁增加搜索范围下拉多选，让用户控制关键词匹配哪些字段。

**架构：** 在两个筛选状态中加入 `keywordFields`，过滤函数只读取被选中的字段。新增一个复用的 `KeywordSearchScopeSelect` 组件负责展示字段范围和保护至少保留一个选项。首页和导师管理页分别接入该组件，并把搜索范围纳入现有 sessionStorage 状态。

**技术栈：** React 19、TypeScript、Vitest、Testing Library、Tailwind CSS、lucide-react。

---

## 文件结构

- 修改：`frontend/src/features/home-dashboard/client/filterDashboardProfessors.ts`
  - 定义首页关键词字段类型、默认字段、缓存清洗函数和按字段搜索逻辑。
- 修改：`frontend/src/features/home-dashboard/client/filterDashboardProfessors.test.ts`
  - 覆盖首页默认全字段、只搜姓名、只搜职称和非法字段恢复默认。
- 修改：`frontend/src/features/professor-management/client/filterManagementProfessors.ts`
  - 定义导师管理页关键词字段类型、默认字段、缓存清洗函数和按字段搜索逻辑。
- 修改：`frontend/src/features/professor-management/client/filterManagementProfessors.test.ts`
  - 覆盖导师管理页默认全字段、只搜邮箱、只搜姓名和非法字段恢复默认。
- 创建：`frontend/src/components/molecules/KeywordSearchScopeSelect.tsx`
  - 复用下拉多选交互，显示“全部字段”或“已选 N 项”，并阻止取消到 0 项。
- 创建：`frontend/src/components/molecules/KeywordSearchScopeSelect.test.tsx`
  - 验证摘要文案、切换回调和最后一项保护。
- 修改：`frontend/src/pages/HomePage.tsx`
  - 读取、保存、重置首页 `keywordFields`，并在关键词输入旁接入组件。
- 修改：`frontend/src/pages/ProfessorsPage.tsx`
  - 读取、保存、重置导师管理页 `keywordFields`，并在关键词输入旁接入组件。

---

### 任务 1：扩展首页过滤模型

**文件：**
- 修改：`frontend/src/features/home-dashboard/client/filterDashboardProfessors.test.ts`
- 修改：`frontend/src/features/home-dashboard/client/filterDashboardProfessors.ts`

- [ ] **步骤 1：编写失败的首页过滤测试**

在 `frontend/src/features/home-dashboard/client/filterDashboardProfessors.test.ts` 的 `describe("filterDashboardProfessors", ...)` 中追加测试：

```ts
it("limits keyword matching to selected dashboard fields", () => {
  const scopedProfessors = [
    buildProfessor({ id: 4, name: "副主任", title: "教授" }),
    buildProfessor({ id: 5, name: "Normal", title: "副教授" }),
  ];

  expect(namesFor(scopedProfessors, { keyword: "副" })).toEqual([
    "副主任",
    "Normal",
  ]);
  expect(
    namesFor(scopedProfessors, {
      keyword: "副",
      keywordFields: ["name"],
    }),
  ).toEqual(["副主任"]);
  expect(
    namesFor(scopedProfessors, {
      keyword: "副",
      keywordFields: ["title"],
    }),
  ).toEqual(["Normal"]);
});

it("normalizes invalid dashboard keyword fields to the default scope", () => {
  const fields = normalizeDashboardKeywordFields(["name", "unknown"]);

  expect(fields).toEqual(DEFAULT_DASHBOARD_KEYWORD_FIELDS);
  expect(
    normalizeDashboardKeywordFields(["unknown"]),
  ).toEqual(DEFAULT_DASHBOARD_KEYWORD_FIELDS);
});
```

同时把导入改为：

```ts
import {
  DEFAULT_DASHBOARD_KEYWORD_FIELDS,
  buildDashboardFilterOptions,
  createDefaultDashboardFilters,
  getActiveDashboardFilterCount,
  filterDashboardProfessors,
  normalizeDashboardKeywordFields,
  pruneDashboardFilters,
  type DashboardFilterState,
} from "./filterDashboardProfessors";
```

- [ ] **步骤 2：运行测试验证失败**

运行：

```bash
cd frontend
npm run test:node -- src/features/home-dashboard/client/filterDashboardProfessors.test.ts
```

预期：FAIL，报错包含 `DEFAULT_DASHBOARD_KEYWORD_FIELDS` 或 `normalizeDashboardKeywordFields` 未导出，或 `keywordFields` 类型不存在。

- [ ] **步骤 3：实现首页关键词字段模型**

在 `frontend/src/features/home-dashboard/client/filterDashboardProfessors.ts` 中加入字段定义：

```ts
export type DashboardKeywordField =
  | "name"
  | "university"
  | "school"
  | "department"
  | "title"
  | "research_direction";

export const DEFAULT_DASHBOARD_KEYWORD_FIELDS: DashboardKeywordField[] = [
  "name",
  "university",
  "school",
  "department",
  "title",
  "research_direction",
];

const dashboardKeywordFieldSet = new Set<string>(
  DEFAULT_DASHBOARD_KEYWORD_FIELDS,
);
```

把 `DashboardFilterState` 改为：

```ts
export type DashboardFilterState = {
  keyword: string;
  keywordFields: DashboardKeywordField[];
  universities: string[];
  schools: string[];
  departments: string[];
  titles: string[];
  statuses: ProfessorDashboardStatus[];
  minMatchScore: string;
};
```

把 `createDefaultDashboardFilters` 改为：

```ts
export const createDefaultDashboardFilters = (): DashboardFilterState => ({
  keyword: "",
  keywordFields: [...DEFAULT_DASHBOARD_KEYWORD_FIELDS],
  universities: [],
  schools: [],
  departments: [],
  titles: [],
  statuses: [],
  minMatchScore: "",
});
```

加入缓存清洗函数：

```ts
export const normalizeDashboardKeywordFields = (
  values: unknown,
): DashboardKeywordField[] => {
  if (!Array.isArray(values)) {
    return [...DEFAULT_DASHBOARD_KEYWORD_FIELDS];
  }

  const nextValues = values.filter(
    (value): value is DashboardKeywordField =>
      typeof value === "string" && dashboardKeywordFieldSet.has(value),
  );

  if (
    nextValues.length === 0 ||
    nextValues.length !== values.length
  ) {
    return [...DEFAULT_DASHBOARD_KEYWORD_FIELDS];
  }

  return nextValues;
};
```

加入字段取值函数：

```ts
const getDashboardKeywordValue = (
  professor: ProfessorDashboardItemDTO,
  field: DashboardKeywordField,
): string | null | undefined => professor[field];
```

把 `filterDashboardProfessors` 中 `keywordMatched` 的字段数组改为：

```ts
const keywordFields = normalizeDashboardKeywordFields(filters.keywordFields);
const keywordMatched =
  !keyword ||
  keywordFields.some((field) =>
    normalize(getDashboardKeywordValue(professor, field)).includes(keyword),
  );
```

- [ ] **步骤 4：运行首页过滤测试验证通过**

运行：

```bash
cd frontend
npm run test:node -- src/features/home-dashboard/client/filterDashboardProfessors.test.ts
```

预期：PASS。

- [ ] **步骤 5：Commit**

```bash
git add frontend/src/features/home-dashboard/client/filterDashboardProfessors.ts frontend/src/features/home-dashboard/client/filterDashboardProfessors.test.ts
git commit -m "feat(frontend): add dashboard keyword field filtering"
```

---

### 任务 2：扩展导师管理过滤模型

**文件：**
- 修改：`frontend/src/features/professor-management/client/filterManagementProfessors.test.ts`
- 修改：`frontend/src/features/professor-management/client/filterManagementProfessors.ts`

- [ ] **步骤 1：编写失败的导师管理过滤测试**

在 `frontend/src/features/professor-management/client/filterManagementProfessors.test.ts` 的 `describe("filterManagementProfessors", ...)` 中追加测试：

```ts
it("limits keyword matching to selected management fields", () => {
  const scopedProfessors = [
    buildProfessor({
      id: 4,
      name: "副主任",
      email: "director@example.edu",
      title: "教授",
    }),
    buildProfessor({
      id: 5,
      name: "Normal",
      email: "normal@example.edu",
      title: "副教授",
    }),
  ];

  expect(
    namesFor(scopedProfessors, {
      keyword: "副",
      keywordFields: ["name"],
    }),
  ).toEqual(["副主任"]);
  expect(
    namesFor(scopedProfessors, {
      keyword: "副",
      keywordFields: ["title"],
    }),
  ).toEqual(["Normal"]);
});

it("supports email-only keyword matching on management page", () => {
  expect(
    namesFor(professors, {
      keyword: "bob@example.edu",
      keywordFields: ["email"],
    }),
  ).toEqual(["Bob"]);
  expect(
    namesFor(professors, {
      keyword: "bob@example.edu",
      keywordFields: ["name"],
    }),
  ).toEqual([]);
});

it("normalizes invalid management keyword fields to the default scope", () => {
  expect(
    normalizeManagementKeywordFields(["name", "unknown"]),
  ).toEqual(DEFAULT_MANAGEMENT_KEYWORD_FIELDS);
  expect(
    normalizeManagementKeywordFields(["unknown"]),
  ).toEqual(DEFAULT_MANAGEMENT_KEYWORD_FIELDS);
});
```

同时把导入改为：

```ts
import {
  DEFAULT_MANAGEMENT_KEYWORD_FIELDS,
  buildManagementFilterOptions,
  createDefaultManagementFilters,
  filterManagementProfessors,
  getActiveManagementAdvancedFilterCount,
  normalizeManagementKeywordFields,
  pruneManagementFilters,
  type ProfessorManagementFilterState,
} from "./filterManagementProfessors";
```

- [ ] **步骤 2：运行测试验证失败**

运行：

```bash
cd frontend
npm run test:node -- src/features/professor-management/client/filterManagementProfessors.test.ts
```

预期：FAIL，报错包含 `DEFAULT_MANAGEMENT_KEYWORD_FIELDS` 或 `normalizeManagementKeywordFields` 未导出，或 `keywordFields` 类型不存在。

- [ ] **步骤 3：实现导师管理关键词字段模型**

在 `frontend/src/features/professor-management/client/filterManagementProfessors.ts` 中加入字段定义：

```ts
export type ManagementKeywordField =
  | "name"
  | "email"
  | "university"
  | "school"
  | "department"
  | "title"
  | "research_direction";

export const DEFAULT_MANAGEMENT_KEYWORD_FIELDS: ManagementKeywordField[] = [
  "name",
  "email",
  "university",
  "school",
  "department",
  "title",
  "research_direction",
];

const managementKeywordFieldSet = new Set<string>(
  DEFAULT_MANAGEMENT_KEYWORD_FIELDS,
);
```

把 `ProfessorManagementFilterState` 改为：

```ts
export type ProfessorManagementFilterState = {
  keyword: string;
  keywordFields: ManagementKeywordField[];
  universities: string[];
  schools: string[];
  departments: string[];
  titles: string[];
};
```

把 `createDefaultManagementFilters` 改为：

```ts
export const createDefaultManagementFilters = (): ProfessorManagementFilterState => ({
  keyword: "",
  keywordFields: [...DEFAULT_MANAGEMENT_KEYWORD_FIELDS],
  universities: [],
  schools: [],
  departments: [],
  titles: [],
});
```

加入缓存清洗函数：

```ts
export const normalizeManagementKeywordFields = (
  values: unknown,
): ManagementKeywordField[] => {
  if (!Array.isArray(values)) {
    return [...DEFAULT_MANAGEMENT_KEYWORD_FIELDS];
  }

  const nextValues = values.filter(
    (value): value is ManagementKeywordField =>
      typeof value === "string" && managementKeywordFieldSet.has(value),
  );

  if (
    nextValues.length === 0 ||
    nextValues.length !== values.length
  ) {
    return [...DEFAULT_MANAGEMENT_KEYWORD_FIELDS];
  }

  return nextValues;
};
```

加入字段取值函数：

```ts
const getManagementKeywordValue = (
  professor: ProfessorManagementItemDTO,
  field: ManagementKeywordField,
): string | null | undefined => professor[field];
```

把 `filterManagementProfessors` 中 `keywordMatched` 的字段数组改为：

```ts
const keywordFields = normalizeManagementKeywordFields(filters.keywordFields);
const keywordMatched =
  !keyword ||
  keywordFields.some((field) =>
    normalize(getManagementKeywordValue(professor, field)).includes(keyword),
  );
```

- [ ] **步骤 4：运行导师管理过滤测试验证通过**

运行：

```bash
cd frontend
npm run test:node -- src/features/professor-management/client/filterManagementProfessors.test.ts
```

预期：PASS。

- [ ] **步骤 5：Commit**

```bash
git add frontend/src/features/professor-management/client/filterManagementProfessors.ts frontend/src/features/professor-management/client/filterManagementProfessors.test.ts
git commit -m "feat(frontend): add management keyword field filtering"
```

---

### 任务 3：新增搜索范围下拉组件

**文件：**
- 创建：`frontend/src/components/molecules/KeywordSearchScopeSelect.tsx`
- 创建：`frontend/src/components/molecules/KeywordSearchScopeSelect.test.tsx`

- [ ] **步骤 1：编写失败的组件测试**

创建 `frontend/src/components/molecules/KeywordSearchScopeSelect.test.tsx`：

```tsx
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { KeywordSearchScopeSelect } from "./KeywordSearchScopeSelect";

const options = [
  { value: "name", label: "姓名" },
  { value: "title", label: "职称" },
  { value: "school", label: "学院" },
];

describe("KeywordSearchScopeSelect", () => {
  it("shows all fields when every option is selected", () => {
    render(
      <KeywordSearchScopeSelect
        label="搜索范围"
        options={options}
        selectedValues={["name", "title", "school"]}
        onToggle={vi.fn()}
      />,
    );

    expect(screen.getByRole("button", { name: /搜索范围：全部字段/ })).toBeInTheDocument();
  });

  it("shows selected count and calls onToggle for removable options", () => {
    const onToggle = vi.fn();
    render(
      <KeywordSearchScopeSelect
        label="搜索范围"
        options={options}
        selectedValues={["name", "title"]}
        onToggle={onToggle}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: /搜索范围：已选 2 项/ }));
    fireEvent.click(screen.getByRole("option", { name: "职称" }));

    expect(onToggle).toHaveBeenCalledWith("title");
  });

  it("keeps at least one selected field", () => {
    const onToggle = vi.fn();
    render(
      <KeywordSearchScopeSelect
        label="搜索范围"
        options={options}
        selectedValues={["name"]}
        onToggle={onToggle}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: /搜索范围：已选 1 项/ }));
    fireEvent.click(screen.getByRole("option", { name: "姓名" }));

    expect(onToggle).not.toHaveBeenCalled();
  });
});
```

- [ ] **步骤 2：运行组件测试验证失败**

运行：

```bash
cd frontend
npm run test:dom -- src/components/molecules/KeywordSearchScopeSelect.test.tsx
```

预期：FAIL，报错包含 `Cannot find module './KeywordSearchScopeSelect'`。

- [ ] **步骤 3：实现下拉组件**

创建 `frontend/src/components/molecules/KeywordSearchScopeSelect.tsx`：

```tsx
import { useEffect, useId, useRef, useState } from "react";
import clsx from "clsx";
import { Check, ChevronDown, Search } from "lucide-react";

export type KeywordSearchScopeOption<TValue extends string = string> = {
  value: TValue;
  label: string;
};

type KeywordSearchScopeSelectProps<TValue extends string = string> = {
  label: string;
  options: KeywordSearchScopeOption<TValue>[];
  selectedValues: TValue[];
  onToggle: (value: TValue) => void;
};

const getSummary = <TValue extends string>(
  options: KeywordSearchScopeOption<TValue>[],
  selectedValues: TValue[],
) =>
  selectedValues.length === options.length ? "全部字段" : `已选 ${selectedValues.length} 项`;

export const KeywordSearchScopeSelect = <TValue extends string = string>({
  label,
  options,
  selectedValues,
  onToggle,
}: KeywordSearchScopeSelectProps<TValue>) => {
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement | null>(null);
  const triggerRef = useRef<HTMLButtonElement | null>(null);
  const listboxId = useId();
  const selectedSet = new Set(selectedValues);
  const summary = getSummary(options, selectedValues);

  useEffect(() => {
    if (!open) {
      return;
    }

    const handlePointerDown = (event: PointerEvent) => {
      if (!rootRef.current?.contains(event.target as Node)) {
        setOpen(false);
      }
    };

    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        setOpen(false);
        triggerRef.current?.focus();
      }
    };

    window.addEventListener("pointerdown", handlePointerDown);
    window.addEventListener("keydown", handleKeyDown);
    return () => {
      window.removeEventListener("pointerdown", handlePointerDown);
      window.removeEventListener("keydown", handleKeyDown);
    };
  }, [open]);

  return (
    <div ref={rootRef} className="relative shrink-0">
      <button
        ref={triggerRef}
        type="button"
        aria-label={`${label}：${summary}`}
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-controls={listboxId}
        onClick={() => setOpen((previous) => !previous)}
        className={clsx(
          "inline-flex h-8 items-center gap-2 rounded-xl border border-stone-200 bg-stone-50 px-3 text-xs font-medium text-stone-700 transition hover:border-primary/40 hover:text-primary",
          open && "border-primary/45 bg-white text-primary ring-2 ring-primary/10",
        )}
      >
        <Search className="h-3.5 w-3.5" />
        <span>{summary}</span>
        <ChevronDown className={clsx("h-3.5 w-3.5 transition", open && "rotate-180")} />
      </button>

      {open ? (
        <div className="absolute right-0 top-[calc(100%+0.45rem)] z-40 w-48 overflow-hidden rounded-2xl border border-stone-200/90 bg-white p-1 shadow-[0_22px_40px_-26px_rgba(41,37,36,0.34)]">
          <div
            id={listboxId}
            role="listbox"
            aria-label={label}
            aria-multiselectable="true"
            className="flex max-h-64 flex-col gap-1 overflow-y-auto py-1"
          >
            {options.map((option) => {
              const selected = selectedSet.has(option.value);
              const locked = selected && selectedValues.length === 1;
              return (
                <button
                  key={option.value}
                  type="button"
                  role="option"
                  aria-selected={selected}
                  onClick={() => {
                    if (!locked) {
                      onToggle(option.value);
                    }
                  }}
                  className={clsx(
                    "flex w-full items-center justify-between gap-3 rounded-xl px-3 py-2 text-left text-[13px] leading-5 transition",
                    selected
                      ? "bg-primary text-white shadow-sm shadow-primary/25"
                      : "text-stone-700 hover:bg-stone-100/90 hover:text-stone-900",
                    locked && "cursor-default opacity-80",
                  )}
                >
                  <span className="truncate">{option.label}</span>
                  {selected ? <Check className="h-4 w-4 shrink-0" /> : null}
                </button>
              );
            })}
          </div>
        </div>
      ) : null}
    </div>
  );
};
```

- [ ] **步骤 4：运行组件测试验证通过**

运行：

```bash
cd frontend
npm run test:dom -- src/components/molecules/KeywordSearchScopeSelect.test.tsx
```

预期：PASS。

- [ ] **步骤 5：Commit**

```bash
git add frontend/src/components/molecules/KeywordSearchScopeSelect.tsx frontend/src/components/molecules/KeywordSearchScopeSelect.test.tsx
git commit -m "feat(frontend): add keyword search scope selector"
```

---

### 任务 4：接入首页导师看板

**文件：**
- 修改：`frontend/src/pages/HomePage.tsx`

- [ ] **步骤 1：接入导入和字段选项**

在 `frontend/src/pages/HomePage.tsx` 增加导入：

```ts
import { KeywordSearchScopeSelect } from "@/components/molecules/KeywordSearchScopeSelect";
```

扩展过滤导入：

```ts
import {
  DEFAULT_DASHBOARD_KEYWORD_FIELDS,
  buildDashboardFilterOptions,
  createDefaultDashboardFilters,
  filterDashboardProfessors,
  getActiveDashboardFilterCount,
  normalizeDashboardKeywordFields,
  pruneDashboardFilters,
  type DashboardFilterState,
  type DashboardKeywordField,
} from "@/features/home-dashboard/client/filterDashboardProfessors";
```

在常量区加入：

```ts
const dashboardKeywordFieldOptions: Array<{
  value: DashboardKeywordField;
  label: string;
}> = [
  { value: "name", label: "姓名" },
  { value: "university", label: "学校" },
  { value: "school", label: "学院" },
  { value: "department", label: "系所" },
  { value: "title", label: "职称" },
  { value: "research_direction", label: "研究方向" },
];
```

- [ ] **步骤 2：接入首页缓存读取**

在 `readStoredDashboardFilters` 返回对象中加入：

```ts
keywordFields: normalizeDashboardKeywordFields(parsedValue.keywordFields),
```

保留 `keyword` 读取逻辑不变。

- [ ] **步骤 3：添加首页字段切换和重置**

在 `HomePage` 中新增切换函数：

```ts
const toggleDashboardKeywordField = (field: DashboardKeywordField) => {
  setFilters((previous) => {
    const currentFields = normalizeDashboardKeywordFields(previous.keywordFields);
    const nextFields = currentFields.includes(field)
      ? currentFields.filter((item) => item !== field)
      : [...currentFields, field];

    return {
      ...previous,
      keywordFields:
        nextFields.length > 0 ? nextFields : [...DEFAULT_DASHBOARD_KEYWORD_FIELDS],
    };
  });
};
```

`resetAllFilters` 已调用 `createDefaultDashboardFilters()`，不需要额外处理。

- [ ] **步骤 4：渲染首页搜索范围组件**

在首页关键词输入 `label` 内，将图标和输入的容器改为包含组件：

```tsx
<div className="flex min-w-0 flex-1 items-center gap-2">
  <Search className="h-4 w-4 text-stone-400" />
  <input
    value={filters.keyword}
    onChange={(event) =>
      updateFilters({ keyword: event.target.value })
    }
    placeholder="导师、学校、学院、系所、职称、研究方向"
    className="w-full min-w-0 bg-transparent leading-5 outline-none"
  />
  <KeywordSearchScopeSelect
    label="搜索范围"
    options={dashboardKeywordFieldOptions}
    selectedValues={normalizeDashboardKeywordFields(filters.keywordFields)}
    onToggle={toggleDashboardKeywordField}
  />
</div>
```

- [ ] **步骤 5：运行首页相关验证**

运行：

```bash
cd frontend
npm run test:node -- src/features/home-dashboard/client/filterDashboardProfessors.test.ts
npm run test:dom -- src/components/molecules/KeywordSearchScopeSelect.test.tsx
```

预期：两个命令均 PASS。

- [ ] **步骤 6：Commit**

```bash
git add frontend/src/pages/HomePage.tsx
git commit -m "feat(frontend): wire dashboard keyword scope selector"
```

---

### 任务 5：接入导师管理页

**文件：**
- 修改：`frontend/src/pages/ProfessorsPage.tsx`

- [ ] **步骤 1：接入导入和字段选项**

在 `frontend/src/pages/ProfessorsPage.tsx` 增加导入：

```ts
import { KeywordSearchScopeSelect } from "@/components/molecules/KeywordSearchScopeSelect";
```

扩展过滤导入：

```ts
import {
  DEFAULT_MANAGEMENT_KEYWORD_FIELDS,
  buildManagementFilterOptions,
  createDefaultManagementFilters,
  filterManagementProfessors,
  getActiveManagementAdvancedFilterCount,
  normalizeManagementKeywordFields,
  pruneManagementFilters,
  type ManagementKeywordField,
  type ProfessorManagementFilterState,
} from "@/features/professor-management/client/filterManagementProfessors";
```

在常量区加入：

```ts
const managementKeywordFieldOptions: Array<{
  value: ManagementKeywordField;
  label: string;
}> = [
  { value: "name", label: "姓名" },
  { value: "email", label: "邮箱" },
  { value: "university", label: "学校" },
  { value: "school", label: "学院" },
  { value: "department", label: "系所" },
  { value: "title", label: "职称" },
  { value: "research_direction", label: "研究方向" },
];
```

- [ ] **步骤 2：接入导师管理缓存和链接关键词**

在 `readStoredProfessorManagementState` 中，`nextFilters.keyword = ...` 后加入：

```ts
nextFilters.keywordFields = normalizeManagementKeywordFields(
  filters?.keywordFields,
);
```

在 `linkedKeyword` 覆盖筛选状态处，把：

```ts
filters: {
  ...createDefaultManagementFilters(),
  keyword: linkedKeyword,
},
```

保持为基于 `createDefaultManagementFilters()` 展开，确保默认全字段保留。

在 `useEffect` 处理 `linkedKeyword` 时，也保持：

```ts
setFilters({ ...createDefaultManagementFilters(), keyword: linkedKeyword });
```

- [ ] **步骤 3：添加导师管理字段切换**

在 `ProfessorsPage` 中新增切换函数：

```ts
const toggleManagementKeywordField = (field: ManagementKeywordField) => {
  setFilters((previous) => {
    const currentFields = normalizeManagementKeywordFields(previous.keywordFields);
    const nextFields = currentFields.includes(field)
      ? currentFields.filter((item) => item !== field)
      : [...currentFields, field];

    return {
      ...previous,
      keywordFields:
        nextFields.length > 0 ? nextFields : [...DEFAULT_MANAGEMENT_KEYWORD_FIELDS],
    };
  });
};
```

- [ ] **步骤 4：渲染导师管理搜索范围组件**

在导师管理页关键词输入 `label` 内，将图标和输入的容器改为包含组件：

```tsx
<div className="flex min-w-0 flex-1 items-center gap-2">
  <Search className="h-4 w-4 shrink-0 text-stone-400" />
  <input
    value={filters.keyword}
    onChange={(event) =>
      updateFilters({ keyword: event.target.value })
    }
    placeholder="姓名、邮箱、学校、学院、系所、职称、研究方向"
    className="w-full min-w-0 bg-transparent leading-5 outline-none placeholder:text-stone-400"
  />
  <KeywordSearchScopeSelect
    label="搜索范围"
    options={managementKeywordFieldOptions}
    selectedValues={normalizeManagementKeywordFields(filters.keywordFields)}
    onToggle={toggleManagementKeywordField}
  />
</div>
```

- [ ] **步骤 5：运行导师管理相关验证**

运行：

```bash
cd frontend
npm run test:node -- src/features/professor-management/client/filterManagementProfessors.test.ts
npm run test:dom -- src/components/molecules/KeywordSearchScopeSelect.test.tsx
```

预期：两个命令均 PASS。

- [ ] **步骤 6：Commit**

```bash
git add frontend/src/pages/ProfessorsPage.tsx
git commit -m "feat(frontend): wire management keyword scope selector"
```

---

### 任务 6：最终验证

**文件：**
- 验证：`frontend/`

- [ ] **步骤 1：运行完整前端检查**

运行：

```bash
cd frontend
npm run lint
npm run test
npm run build
```

预期：三个命令均 PASS。

- [ ] **步骤 2：检查最终 diff**

运行：

```bash
git status --short
git diff --stat
```

预期：只剩用户已有未跟踪文件或无变更；本功能相关文件已经在任务提交中提交。

- [ ] **步骤 3：最终说明**

向用户报告：

```text
已完成首页和导师管理页关键词搜索范围选择。验证通过：npm run lint、npm run test、npm run build。
```

如果某个验证失败，先使用 systematic-debugging 技能定位原因，再修复并重新运行失败命令。
