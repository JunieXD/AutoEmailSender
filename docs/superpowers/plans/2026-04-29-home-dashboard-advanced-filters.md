# 首页导师看板高级筛选实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 在首页导师看板加入“基础筛选 + 高级筛选”布局，支持学校、学院、系所、职称、状态下拉多选，以及最低匹配度阈值筛选。

**架构：** 筛选继续在前端本地完成。新增首页筛选 helper 负责选项生成、筛选状态、筛选计数和结果过滤；新增一个小型多选下拉组件承载可枚举筛选 UI；`HomePage.tsx` 只负责持有状态、渲染筛选区，并把筛选结果交给现有排序 helper。

**技术栈：** React 19、TypeScript、Vite、Vitest、Testing Library、Tailwind CSS、lucide-react。

---

## 文件结构

- 创建：`frontend/src/features/home-dashboard/client/filterDashboardProfessors.ts`
  - 职责：定义高级筛选状态、默认值、筛选选项生成、筛选计数、最低匹配度解析、导师过滤函数。
- 创建：`frontend/src/features/home-dashboard/client/filterDashboardProfessors.test.ts`
  - 职责：覆盖关键词新增字段、多选规则、最低匹配度规则、选项生成和不修改输入数组。
- 创建：`frontend/src/components/molecules/MultiSelectFilter.tsx`
  - 职责：通用下拉多选控件，只处理展开、关闭、勾选、摘要显示和清空。
- 创建：`frontend/test/MultiSelectFilter.test.tsx`
  - 职责：验证多选组件的用户交互，不涉及首页业务。
- 修改：`frontend/src/pages/HomePage.tsx`
  - 职责：接入基础筛选与高级筛选状态，把原有内联筛选替换为 helper 调用。

## 任务 1：编写首页筛选 helper 失败测试

**文件：**
- 创建：`frontend/src/features/home-dashboard/client/filterDashboardProfessors.test.ts`

- [ ] **步骤 1：创建失败测试**

写入 `frontend/src/features/home-dashboard/client/filterDashboardProfessors.test.ts`：

```ts
import { describe, expect, it } from "vitest";
import type { ProfessorDashboardItemDTO } from "@/types";
import {
  buildDashboardFilterOptions,
  createDefaultDashboardFilters,
  getActiveDashboardFilterCount,
  filterDashboardProfessors,
  type DashboardFilterState,
} from "./filterDashboardProfessors";

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
  professors: ProfessorDashboardItemDTO[],
  overrides: Partial<DashboardFilterState>,
) =>
  filterDashboardProfessors(professors, {
    ...createDefaultDashboardFilters(),
    ...overrides,
  }).map((professor) => professor.name);

describe("filterDashboardProfessors", () => {
  const professors = [
    buildProfessor({
      id: 1,
      name: "Alice",
      title: "教授",
      university: "MIT",
      school: "School of Engineering",
      department: "EECS",
      research_direction: "AI systems",
      match_score: 91,
      status: "ready_to_send",
    }),
    buildProfessor({
      id: 2,
      name: "Bob",
      title: "副教授",
      university: "Stanford",
      school: "School of Medicine",
      department: "Bioengineering",
      research_direction: "Biomedical AI",
      match_score: 76,
      status: "not_contacted",
    }),
    buildProfessor({
      id: 3,
      name: "Carol",
      title: "助理教授",
      university: "MIT",
      school: "AI Institute",
      department: "Robotics",
      research_direction: "Robotics planning",
      match_score: null,
      status: "replied",
    }),
  ];

  it("matches keyword against school, department, title, and research direction", () => {
    expect(namesFor(professors, { keyword: "robotics" })).toEqual(["Carol"]);
    expect(namesFor(professors, { keyword: "School of Medicine" })).toEqual(["Bob"]);
    expect(namesFor(professors, { keyword: "教授" })).toEqual([
      "Alice",
      "Bob",
      "Carol",
    ]);
  });

  it("uses OR within one multi-select group", () => {
    expect(namesFor(professors, { universities: ["MIT", "Stanford"] })).toEqual([
      "Alice",
      "Bob",
      "Carol",
    ]);
  });

  it("uses AND across multi-select groups", () => {
    expect(
      namesFor(professors, {
        universities: ["MIT"],
        schools: ["AI Institute"],
        departments: ["Robotics"],
        titles: ["助理教授"],
        statuses: ["replied"],
      }),
    ).toEqual(["Carol"]);
  });

  it("filters by minimum match score and excludes unscored professors when threshold is set", () => {
    expect(namesFor(professors, { minMatchScore: "80" })).toEqual(["Alice"]);
  });

  it("keeps unscored professors when minimum match score is empty", () => {
    expect(namesFor(professors, { minMatchScore: "" })).toEqual([
      "Alice",
      "Bob",
      "Carol",
    ]);
  });

  it("builds sorted non-empty options", () => {
    const options = buildDashboardFilterOptions([
      ...professors,
      buildProfessor({ id: 4, name: "Empty", university: "", school: null }),
    ]);

    expect(options.universities).toEqual(["MIT", "Stanford"]);
    expect(options.schools).toEqual([
      "AI Institute",
      "School of Engineering",
      "School of Medicine",
    ]);
    expect(options.departments).toEqual(["Bioengineering", "EECS", "Robotics"]);
    expect(options.titles).toEqual(["副教授", "教授", "助理教授"]);
  });

  it("counts active advanced filters", () => {
    expect(
      getActiveDashboardFilterCount({
        ...createDefaultDashboardFilters(),
        universities: ["MIT"],
        titles: ["教授", "副教授"],
        minMatchScore: "80",
      }),
    ).toBe(4);
  });

  it("does not mutate the input array", () => {
    const input = [...professors];
    filterDashboardProfessors(input, {
      ...createDefaultDashboardFilters(),
      universities: ["MIT"],
    });

    expect(input.map((professor) => professor.name)).toEqual([
      "Alice",
      "Bob",
      "Carol",
    ]);
  });
});
```

- [ ] **步骤 2：运行测试验证失败**

运行：

```bash
cd frontend
npm run test -- src/features/home-dashboard/client/filterDashboardProfessors.test.ts
```

预期：FAIL，报错包含 `Failed to resolve import "./filterDashboardProfessors"` 或等价的模块不存在信息。

## 任务 2：实现首页筛选 helper

**文件：**
- 创建：`frontend/src/features/home-dashboard/client/filterDashboardProfessors.ts`
- 测试：`frontend/src/features/home-dashboard/client/filterDashboardProfessors.test.ts`

- [ ] **步骤 1：实现最少筛选代码**

创建 `frontend/src/features/home-dashboard/client/filterDashboardProfessors.ts`：

```ts
import type { ProfessorDashboardItemDTO, ProfessorDashboardStatus } from "@/types";

export type DashboardFilterState = {
  keyword: string;
  universities: string[];
  schools: string[];
  departments: string[];
  titles: string[];
  statuses: ProfessorDashboardStatus[];
  minMatchScore: string;
};

export type DashboardFilterOptions = {
  universities: string[];
  schools: string[];
  departments: string[];
  titles: string[];
};

export const createDefaultDashboardFilters = (): DashboardFilterState => ({
  keyword: "",
  universities: [],
  schools: [],
  departments: [],
  titles: [],
  statuses: [],
  minMatchScore: "",
});

const normalize = (value: string | null | undefined): string =>
  value?.trim().toLowerCase() ?? "";

const sortByChinese = (values: Iterable<string>): string[] =>
  Array.from(values).sort((left, right) => left.localeCompare(right, "zh-CN"));

const addNonEmpty = (set: Set<string>, value: string | null | undefined) => {
  const trimmed = value?.trim();
  if (trimmed) {
    set.add(trimmed);
  }
};

export const buildDashboardFilterOptions = (
  professors: ProfessorDashboardItemDTO[],
): DashboardFilterOptions => {
  const universities = new Set<string>();
  const schools = new Set<string>();
  const departments = new Set<string>();
  const titles = new Set<string>();

  professors.forEach((professor) => {
    addNonEmpty(universities, professor.university);
    addNonEmpty(schools, professor.school);
    addNonEmpty(departments, professor.department);
    addNonEmpty(titles, professor.title);
  });

  return {
    universities: sortByChinese(universities),
    schools: sortByChinese(schools),
    departments: sortByChinese(departments),
    titles: sortByChinese(titles),
  };
};

const matchesAny = (
  value: string | null | undefined,
  selectedValues: string[],
): boolean => selectedValues.length === 0 || selectedValues.includes(value ?? "");

const matchesAnyStatus = (
  value: ProfessorDashboardStatus,
  selectedValues: ProfessorDashboardStatus[],
): boolean => selectedValues.length === 0 || selectedValues.includes(value);

const parseMinimumMatchScore = (value: string): number | null => {
  const trimmed = value.trim();
  if (!trimmed) {
    return null;
  }

  const score = Number(trimmed);
  if (!Number.isFinite(score)) {
    return null;
  }

  return Math.min(100, Math.max(0, score));
};

export const getActiveDashboardFilterCount = (
  filters: DashboardFilterState,
): number =>
  filters.universities.length +
  filters.schools.length +
  filters.departments.length +
  filters.titles.length +
  filters.statuses.length +
  (filters.minMatchScore.trim() ? 1 : 0);

export const filterDashboardProfessors = (
  professors: ProfessorDashboardItemDTO[],
  filters: DashboardFilterState,
): ProfessorDashboardItemDTO[] => {
  const keyword = normalize(filters.keyword);
  const minMatchScore = parseMinimumMatchScore(filters.minMatchScore);

  return professors.filter((professor) => {
    const keywordMatched =
      !keyword ||
      [
        professor.name,
        professor.university,
        professor.school,
        professor.department,
        professor.title,
        professor.research_direction,
      ].some((value) => normalize(value).includes(keyword));

    const matchScoreMatched =
      minMatchScore === null ||
      (professor.match_score !== null && professor.match_score >= minMatchScore);

    return (
      keywordMatched &&
      matchesAny(professor.university, filters.universities) &&
      matchesAny(professor.school, filters.schools) &&
      matchesAny(professor.department, filters.departments) &&
      matchesAny(professor.title, filters.titles) &&
      matchesAnyStatus(professor.status, filters.statuses) &&
      matchScoreMatched
    );
  });
};
```

- [ ] **步骤 2：运行 helper 单测验证通过**

运行：

```bash
cd frontend
npm run test -- src/features/home-dashboard/client/filterDashboardProfessors.test.ts
```

预期：PASS，8 个测试通过。

- [ ] **步骤 3：Commit 筛选 helper**

运行：

```bash
git add frontend/src/features/home-dashboard/client/filterDashboardProfessors.ts frontend/src/features/home-dashboard/client/filterDashboardProfessors.test.ts
git commit -m "feat(frontend): add home dashboard filter helper"
```

预期：提交成功，提交只包含 helper 和单测。

## 任务 3：编写多选下拉组件失败测试

**文件：**
- 创建：`frontend/test/MultiSelectFilter.test.tsx`

- [ ] **步骤 1：创建组件测试**

写入 `frontend/test/MultiSelectFilter.test.tsx`：

```tsx
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { MultiSelectFilter } from "@/components/molecules/MultiSelectFilter";

describe("MultiSelectFilter", () => {
  it("shows all label when no values are selected", () => {
    render(
      <MultiSelectFilter
        label="学校"
        allLabel="全部学校"
        selectedValues={[]}
        options={["MIT", "Stanford"]}
        onToggle={vi.fn()}
        onClear={vi.fn()}
      />,
    );

    expect(screen.getByRole("button", { name: "学校：全部学校" })).toBeInTheDocument();
  });

  it("opens options and toggles values", async () => {
    const user = userEvent.setup();
    const onToggle = vi.fn();

    render(
      <MultiSelectFilter
        label="学校"
        allLabel="全部学校"
        selectedValues={["MIT"]}
        options={["MIT", "Stanford"]}
        onToggle={onToggle}
        onClear={vi.fn()}
      />,
    );

    await user.click(screen.getByRole("button", { name: "学校：MIT" }));

    const listbox = screen.getByRole("listbox", { name: "学校" });
    expect(within(listbox).getByRole("option", { name: "MIT" })).toHaveAttribute(
      "aria-selected",
      "true",
    );

    await user.click(within(listbox).getByRole("option", { name: "Stanford" }));

    expect(onToggle).toHaveBeenCalledWith("Stanford");
  });

  it("summarizes multiple selected values and clears them", async () => {
    const user = userEvent.setup();
    const onClear = vi.fn();

    render(
      <MultiSelectFilter
        label="职称"
        allLabel="全部职称"
        selectedValues={["教授", "副教授", "助理教授"]}
        options={["教授", "副教授", "助理教授"]}
        onToggle={vi.fn()}
        onClear={onClear}
      />,
    );

    expect(screen.getByRole("button", { name: "职称：教授 等 3 项" })).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "职称：教授 等 3 项" }));
    await user.click(screen.getByRole("button", { name: "清空职称筛选" }));

    expect(onClear).toHaveBeenCalledTimes(1);
  });
});
```

- [ ] **步骤 2：运行测试验证失败**

运行：

```bash
cd frontend
npm run test -- test/MultiSelectFilter.test.tsx
```

预期：FAIL，报错包含 `Failed to resolve import "@/components/molecules/MultiSelectFilter"` 或等价的模块不存在信息。

## 任务 4：实现多选下拉组件

**文件：**
- 创建：`frontend/src/components/molecules/MultiSelectFilter.tsx`
- 测试：`frontend/test/MultiSelectFilter.test.tsx`

- [ ] **步骤 1：实现最少组件代码**

创建 `frontend/src/components/molecules/MultiSelectFilter.tsx`：

```tsx
import { useEffect, useId, useRef, useState } from "react";
import clsx from "clsx";
import { Check, ChevronDown, X } from "lucide-react";

type MultiSelectFilterProps = {
  label: string;
  allLabel: string;
  selectedValues: string[];
  options: string[];
  disabled?: boolean;
  onToggle: (value: string) => void;
  onClear: () => void;
};

const getSummary = (selectedValues: string[], allLabel: string): string => {
  if (selectedValues.length === 0) {
    return allLabel;
  }
  if (selectedValues.length === 1) {
    return selectedValues[0];
  }
  return `${selectedValues[0]} 等 ${selectedValues.length} 项`;
};

export const MultiSelectFilter = ({
  label,
  allLabel,
  selectedValues,
  options,
  disabled = false,
  onToggle,
  onClear,
}: MultiSelectFilterProps) => {
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement | null>(null);
  const triggerRef = useRef<HTMLButtonElement | null>(null);
  const listboxId = useId();
  const selectedSet = new Set(selectedValues);
  const summary = getSummary(selectedValues, allLabel);

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
    <div ref={rootRef} className="block">
      <div className="mb-2 text-sm font-medium text-stone-800">{label}</div>
      <div className="relative">
        <button
          ref={triggerRef}
          type="button"
          disabled={disabled}
          aria-label={`${label}：${summary}`}
          aria-haspopup="listbox"
          aria-expanded={open}
          aria-controls={listboxId}
          onClick={() => setOpen((previous) => !previous)}
          className={clsx(
            "ui-select-shell w-full",
            disabled && "cursor-not-allowed opacity-60",
            open && "border-primary/45 bg-white shadow-lg shadow-stone-300/25 ring-2 ring-primary/10",
          )}
        >
          <span className="flex-1 truncate text-left text-sm text-stone-700">
            {summary}
          </span>
          <ChevronDown
            className={clsx(
              "ui-select-chevron",
              open && "rotate-180 text-primary",
            )}
          />
        </button>

        {open ? (
          <div className="absolute left-0 top-[calc(100%+0.45rem)] z-50 w-full overflow-hidden rounded-2xl border border-stone-200/90 bg-white p-1 shadow-[0_22px_40px_-26px_rgba(41,37,36,0.34)]">
            <div className="flex items-center justify-between border-b border-stone-100 px-2 py-1.5">
              <span className="text-xs font-medium text-stone-500">
                已选 {selectedValues.length} 项
              </span>
              <button
                type="button"
                aria-label={`清空${label}筛选`}
                onClick={onClear}
                disabled={selectedValues.length === 0}
                className="flex items-center gap-1 rounded-lg px-2 py-1 text-xs text-stone-500 hover:bg-stone-100 disabled:cursor-not-allowed disabled:opacity-40"
              >
                <X className="h-3.5 w-3.5" />
                清空
              </button>
            </div>
            <div
              id={listboxId}
              role="listbox"
              aria-label={label}
              aria-multiselectable="true"
              className="max-h-60 overflow-y-auto py-1"
            >
              {options.length === 0 ? (
                <div className="px-3 py-2 text-sm text-stone-400">暂无选项</div>
              ) : (
                options.map((option) => {
                  const selected = selectedSet.has(option);
                  return (
                    <button
                      key={option}
                      type="button"
                      role="option"
                      aria-selected={selected}
                      onClick={() => onToggle(option)}
                      className={clsx(
                        "flex w-full items-center justify-between gap-3 rounded-xl px-3 py-2 text-left text-[13px] leading-5 transition",
                        selected
                          ? "bg-primary text-white shadow-sm shadow-primary/25"
                          : "text-stone-700 hover:bg-stone-100/90 hover:text-stone-900",
                      )}
                    >
                      <span className="truncate">{option}</span>
                      {selected ? <Check className="h-4 w-4 shrink-0" /> : null}
                    </button>
                  );
                })
              )}
            </div>
          </div>
        ) : null}
      </div>
    </div>
  );
};
```

- [ ] **步骤 2：运行组件单测验证通过**

运行：

```bash
cd frontend
npm run test -- test/MultiSelectFilter.test.tsx
```

预期：PASS，3 个测试通过。

- [ ] **步骤 3：Commit 多选组件**

运行：

```bash
git add frontend/src/components/molecules/MultiSelectFilter.tsx frontend/test/MultiSelectFilter.test.tsx
git commit -m "feat(frontend): add multi-select filter control"
```

预期：提交成功，提交只包含多选组件和组件测试。

## 任务 5：接入首页高级筛选

**文件：**
- 修改：`frontend/src/pages/HomePage.tsx`
- 依赖：`frontend/src/components/molecules/MultiSelectFilter.tsx`
- 依赖：`frontend/src/features/home-dashboard/client/filterDashboardProfessors.ts`
- 依赖：`frontend/src/features/home-dashboard/client/sortDashboardProfessors.ts`

- [ ] **步骤 1：导入筛选 helper 和多选组件**

在 `HomePage.tsx` 顶部加入：

```ts
import { MultiSelectFilter } from "@/components/molecules/MultiSelectFilter";
import {
  buildDashboardFilterOptions,
  createDefaultDashboardFilters,
  filterDashboardProfessors,
  getActiveDashboardFilterCount,
  type DashboardFilterState,
} from "@/features/home-dashboard/client/filterDashboardProfessors";
```

同时把当前 `@/features/professor-status/dashboardStatus` import 中的 `type ProfessorDashboardStatusFilter` 移除，并把当前类型导入扩展为：

```ts
import type { ProfessorDashboardItemDTO, ProfessorDashboardStatus } from "@/types";
```

- [ ] **步骤 2：替换筛选状态**

删除原有状态：

```ts
const [keyword, setKeyword] = useState('');
const [university, setUniversity] = useState('all');
const [status, setStatus] = useState<ProfessorDashboardStatusFilter>('all');
```

新增：

```ts
const [filters, setFilters] = useState<DashboardFilterState>(
  createDefaultDashboardFilters,
);
const [advancedFiltersOpen, setAdvancedFiltersOpen] = useState(false);
```

保留：

```ts
const [sortKey, setSortKey] = useState<ProfessorDashboardSortKey>('latest');
```

- [ ] **步骤 3：增加筛选状态更新 helper**

在 `filteredProfessors` 计算之前增加：

```ts
const filterOptions = buildDashboardFilterOptions(professors);
const activeAdvancedFilterCount = getActiveDashboardFilterCount(filters);

const updateFilters = (nextFilters: Partial<DashboardFilterState>) => {
  setFilters((previous) => ({ ...previous, ...nextFilters }));
};

const toggleStringFilterValue = (
  key: "universities" | "schools" | "departments" | "titles",
  value: string,
) => {
  setFilters((previous) => {
    const currentValues = previous[key];
    const nextValues = currentValues.includes(value)
      ? currentValues.filter((item) => item !== value)
      : [...currentValues, value];

    return { ...previous, [key]: nextValues };
  });
};

const toggleStatusFilterValue = (value: ProfessorDashboardStatus) => {
  setFilters((previous) => {
    const nextValues = previous.statuses.includes(value)
      ? previous.statuses.filter((item) => item !== value)
      : [...previous.statuses, value];

    return { ...previous, statuses: nextValues };
  });
};

const clearAdvancedFilters = () => {
  setFilters((previous) => ({
    ...previous,
    universities: [],
    schools: [],
    departments: [],
    titles: [],
    statuses: [],
    minMatchScore: "",
  }));
};

const resetAllFilters = () => {
  setFilters(createDefaultDashboardFilters());
  setSortKey("latest");
};

const selectedStatusLabels = filters.statuses.map((status) =>
  getProfessorDashboardStatusLabel(status),
);
```

- [ ] **步骤 4：使用 helper 计算结果**

替换原有 `filteredProfessors` 计算：

```ts
const filteredProfessors = filterDashboardProfessors(professors, filters);
const visibleProfessors = sortDashboardProfessors(filteredProfessors, sortKey);
```

- [ ] **步骤 5：重写基础筛选区**

把当前筛选网格替换为：

```tsx
<div className="mt-6 grid gap-3 lg:grid-cols-[minmax(0,2fr)_minmax(12rem,1fr)_auto_auto] lg:items-end">
  <label className="rounded-2xl border border-stone-200 bg-white px-4 py-3 text-sm text-stone-600 shadow-sm">
    <div className="mb-2 font-medium text-stone-800">关键词</div>
    <div className="flex items-center gap-2">
      <Search className="h-4 w-4 text-stone-400" />
      <input
        value={filters.keyword}
        onChange={(event) => updateFilters({ keyword: event.target.value })}
        placeholder="导师、学校、学院、系所、职称、研究方向"
        className="w-full bg-transparent outline-none"
      />
    </div>
  </label>

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

  <button
    type="button"
    onClick={() => setAdvancedFiltersOpen((previous) => !previous)}
    className="ui-btn-secondary h-[4.25rem] justify-center"
  >
    高级筛选{activeAdvancedFilterCount > 0 ? ` ${activeAdvancedFilterCount}` : ""}
  </button>

  <button
    type="button"
    onClick={resetAllFilters}
    className="ui-btn-secondary h-[4.25rem] justify-center"
  >
    重置
  </button>
</div>
```

- [ ] **步骤 6：新增高级筛选区**

在基础筛选区后面增加：

```tsx
{advancedFiltersOpen ? (
  <div className="mt-3 rounded-2xl border border-stone-200 bg-white p-4 shadow-sm">
    <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
      <div>
        <div className="text-sm font-semibold text-stone-800">高级筛选</div>
        <div className="mt-1 text-xs text-stone-500">
          多选项同组内取“或”，不同组之间取“且”；最低匹配度为空时不过滤。
        </div>
      </div>
      <button type="button" onClick={clearAdvancedFilters} className="ui-btn-secondary px-3 py-1.5 text-sm">
        清空高级筛选
      </button>
    </div>

    <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
      <MultiSelectFilter
        label="学校"
        allLabel="全部学校"
        selectedValues={filters.universities}
        options={filterOptions.universities}
        onToggle={(value) => toggleStringFilterValue("universities", value)}
        onClear={() => updateFilters({ universities: [] })}
      />
      <MultiSelectFilter
        label="学院"
        allLabel="全部学院"
        selectedValues={filters.schools}
        options={filterOptions.schools}
        onToggle={(value) => toggleStringFilterValue("schools", value)}
        onClear={() => updateFilters({ schools: [] })}
      />
      <MultiSelectFilter
        label="系所"
        allLabel="全部系所"
        selectedValues={filters.departments}
        options={filterOptions.departments}
        onToggle={(value) => toggleStringFilterValue("departments", value)}
        onClear={() => updateFilters({ departments: [] })}
      />
      <MultiSelectFilter
        label="职称"
        allLabel="全部职称"
        selectedValues={filters.titles}
        options={filterOptions.titles}
        onToggle={(value) => toggleStringFilterValue("titles", value)}
        onClear={() => updateFilters({ titles: [] })}
      />
      <MultiSelectFilter
        label="状态"
        allLabel="全部状态"
        selectedValues={selectedStatusLabels}
        options={PROFESSOR_DASHBOARD_STATUS_OPTIONS.map(([, label]) => label)}
        onToggle={(label) => {
          const option = PROFESSOR_DASHBOARD_STATUS_OPTIONS.find(([, optionLabel]) => optionLabel === label);
          if (option) {
            toggleStatusFilterValue(option[0]);
          }
        }}
        onClear={() => updateFilters({ statuses: [] })}
      />
      <label className="block">
        <div className="mb-2 text-sm font-medium text-stone-800">最低匹配度</div>
        <input
          type="number"
          min={0}
          max={100}
          value={filters.minMatchScore}
          onChange={(event) => updateFilters({ minMatchScore: event.target.value })}
          placeholder="例如 80"
          className="ui-select-shell w-full"
        />
        <div className="mt-1 text-xs text-stone-500">
          显示匹配度大于等于该值的导师；为空则不过滤。
        </div>
      </label>
    </div>
  </div>
) : null}
```

- [ ] **步骤 7：运行验证**

运行：

```bash
cd frontend
npm run test -- src/features/home-dashboard/client/filterDashboardProfessors.test.ts test/MultiSelectFilter.test.tsx
npm run lint
npm run build
```

预期：两个测试文件通过，lint 无错误，build 退出码为 0。Vite chunk size warning 可记录为现有打包提示。

- [ ] **步骤 8：Commit 首页接入**

运行：

```bash
git add frontend/src/pages/HomePage.tsx
git commit -m "feat(frontend): add home dashboard advanced filters"
```

预期：提交成功，提交只包含 `HomePage.tsx` 接入改动。

## 任务 6：最终验证

**文件：**
- 验证：`frontend/src/features/home-dashboard/client/filterDashboardProfessors.ts`
- 验证：`frontend/src/components/molecules/MultiSelectFilter.tsx`
- 验证：`frontend/src/pages/HomePage.tsx`

- [ ] **步骤 1：运行完整目标验证**

运行：

```bash
cd frontend
npm run test -- src/features/home-dashboard/client/filterDashboardProfessors.test.ts test/MultiSelectFilter.test.tsx src/features/home-dashboard/client/sortDashboardProfessors.test.ts
npm run lint
npm run build
```

预期：三个目标测试文件通过，lint 无错误，build 退出码为 0。

- [ ] **步骤 2：启动本地前端做人工验证**

运行：

```bash
cd frontend
npm run dev -- --host 127.0.0.1 --port 5174
```

在浏览器打开 `http://127.0.0.1:5174/`，检查：

- 默认进入首页时不启用高级筛选，结果数量与原始导师列表一致。
- 点击“高级筛选”展开高级区。
- 学校、学院、系所、职称、状态均可多选。
- 同组内选择多个值时展示任一命中的导师。
- 同时选择学校和职称时，只展示同时命中的导师。
- 输入最低匹配度 `80` 时，只展示已计算且 `match_score >= 80` 的导师。
- 点击“清空高级筛选”后，关键词和排序保持不变。
- 点击“重置”后，关键词、高级筛选和排序恢复默认。
- “全选当前结果”选择的是筛选并排序后的当前结果集合。

- [ ] **步骤 3：检查 Git 状态**

运行：

```bash
git status --short
git log --oneline -5
```

预期：如果所有任务都已提交，工作区干净；最近提交包含筛选 helper、多选组件、首页接入三个功能提交。
