# 导师空字段搜索与列表显示实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 让首页和导师管理页当前已显示的字段在整格为空时显示 `无`，同时让两个页面共用同一份空字段搜索与显示语义。

**架构：** 在 `frontend/src/lib` 新增无状态的导师搜索字段工具，统一空值判断、关键词匹配和单字段显示格式化。两个现有过滤模块只迁移到共享 API，不改变搜索范围和组合逻辑；两个列表行组件仅在当前已有的显示单元格中使用格式化函数，组合字段仍由组件先忽略空成员再拼接，因此部分缺失不会补 `无`。

**技术栈：** TypeScript、React、Vite、Vitest、Testing Library、ESLint

---

## 文件结构

- 创建 `frontend/src/lib/professorSearchField.ts`：定义 `无` 常量、搜索文本归一化、空值判断、关键词匹配和单字段显示格式化。
- 创建 `frontend/src/lib/professorSearchField.test.ts`：锁定共享空值语义与“无人机”回归行为。
- 修改 `frontend/src/features/home-dashboard/client/filterDashboardProfessors.ts`：删除本地重复 helper，改用共享搜索字段工具。
- 修改 `frontend/src/features/professor-management/client/filterManagementProfessors.ts`：删除本地重复 helper，改用共享搜索字段工具。
- 修改 `frontend/src/components/molecules/DashboardProfessorRow.tsx`：整段摘要和研究方向为空时显示 `无`。
- 修改 `frontend/src/components/molecules/DashboardProfessorRow.test.tsx`：覆盖整格空值、组合字段部分缺失和空标签不显示 `无`。
- 修改 `frontend/src/components/molecules/ManagementProfessorRow.tsx`：职称、邮箱、学校/学院和研究方向整格为空时显示 `无`。
- 修改 `frontend/src/components/molecules/ManagementProfessorRow.test.tsx`：覆盖管理列表各空值单元格、组合字段部分缺失和空标签不显示 `无`。

### 任务 1：建立共享导师搜索字段语义

**文件：**
- 创建：`frontend/src/lib/professorSearchField.test.ts`
- 创建：`frontend/src/lib/professorSearchField.ts`

- [ ] **步骤 1：编写失败的共享 helper 测试**

创建 `frontend/src/lib/professorSearchField.test.ts`：

```ts
import { describe, expect, it } from "vitest";
import {
  EMPTY_PROFESSOR_FIELD_VALUE,
  formatProfessorSearchField,
  isProfessorSearchFieldEmpty,
  matchesProfessorSearchField,
  normalizeProfessorSearchText,
} from "./professorSearchField";

describe("professorSearchField", () => {
  it("normalizes nullable search text", () => {
    expect(normalizeProfessorSearchText(null)).toBe("");
    expect(normalizeProfessorSearchText("  AI Systems  ")).toBe("ai systems");
  });

  it("treats null, empty strings, and whitespace as empty", () => {
    expect(isProfessorSearchFieldEmpty(null)).toBe(true);
    expect(isProfessorSearchFieldEmpty(undefined)).toBe(true);
    expect(isProfessorSearchFieldEmpty("")).toBe(true);
    expect(isProfessorSearchFieldEmpty(" \t ")).toBe(true);
    expect(isProfessorSearchFieldEmpty("AI")).toBe(false);
  });

  it("uses 无 only as the empty-field query", () => {
    expect(matchesProfessorSearchField(null, EMPTY_PROFESSOR_FIELD_VALUE)).toBe(
      true,
    );
    expect(matchesProfessorSearchField("   ", EMPTY_PROFESSOR_FIELD_VALUE)).toBe(
      true,
    );
    expect(
      matchesProfessorSearchField("无人机系统", EMPTY_PROFESSOR_FIELD_VALUE),
    ).toBe(false);
    expect(matchesProfessorSearchField("无", EMPTY_PROFESSOR_FIELD_VALUE)).toBe(
      false,
    );
    expect(
      matchesProfessorSearchField(
        "无人机系统",
        normalizeProfessorSearchText(" 无人机 "),
      ),
    ).toBe(true);
  });

  it("formats only empty display values as 无", () => {
    expect(formatProfessorSearchField(null)).toBe("无");
    expect(formatProfessorSearchField(" \t ")).toBe("无");
    expect(formatProfessorSearchField("  AI Systems  ")).toBe("AI Systems");
  });
});
```

- [ ] **步骤 2：运行测试并确认因模块不存在而失败**

运行：

```bash
cd frontend
rtk npm run test -- --run src/lib/professorSearchField.test.ts
```

预期：FAIL，Vitest 报告无法解析 `./professorSearchField`。

- [ ] **步骤 3：实现最小共享 helper**

创建 `frontend/src/lib/professorSearchField.ts`：

```ts
export const EMPTY_PROFESSOR_FIELD_VALUE = "无";

export const normalizeProfessorSearchText = (
  value: string | null | undefined,
): string => value?.trim().toLowerCase() ?? "";

export const isProfessorSearchFieldEmpty = (
  value: string | null | undefined,
): boolean => normalizeProfessorSearchText(value) === "";

export const matchesProfessorSearchField = (
  value: string | null | undefined,
  normalizedKeyword: string,
): boolean =>
  normalizedKeyword === EMPTY_PROFESSOR_FIELD_VALUE
    ? isProfessorSearchFieldEmpty(value)
    : normalizeProfessorSearchText(value).includes(normalizedKeyword);

export const formatProfessorSearchField = (
  value: string | null | undefined,
): string => value?.trim() || EMPTY_PROFESSOR_FIELD_VALUE;
```

- [ ] **步骤 4：运行共享 helper 测试并确认通过**

运行：

```bash
cd frontend
rtk npm run test -- --run src/lib/professorSearchField.test.ts
```

预期：PASS，`1` 个测试文件、`4` 个测试通过。

- [ ] **步骤 5：提交共享语义**

```bash
rtk git add frontend/src/lib/professorSearchField.ts frontend/src/lib/professorSearchField.test.ts
rtk git commit -m "refactor(frontend): 共享导师空字段语义"
```

### 任务 2：让两个过滤模块复用共享 helper

**文件：**
- 修改：`frontend/src/features/home-dashboard/client/filterDashboardProfessors.ts:1-92,286-301`
- 修改：`frontend/src/features/professor-management/client/filterManagementProfessors.ts:1-82,242-256`
- 测试：`frontend/src/features/home-dashboard/client/filterDashboardProfessors.test.ts`
- 测试：`frontend/src/features/professor-management/client/filterManagementProfessors.test.ts`

- [ ] **步骤 1：运行现有过滤测试并记录重构前基线**

运行：

```bash
cd frontend
rtk npm run test -- --run src/features/home-dashboard/client/filterDashboardProfessors.test.ts src/features/professor-management/client/filterManagementProfessors.test.ts
```

预期：PASS，`2` 个测试文件、`40` 个测试通过。

- [ ] **步骤 2：迁移首页过滤模块**

在 `filterDashboardProfessors.ts` 顶部增加：

```ts
import {
  matchesProfessorSearchField,
  normalizeProfessorSearchText,
} from "@/lib/professorSearchField";
```

删除本地 `normalize`、`EMPTY_FIELD_SEARCH_KEYWORD` 和 `matchesKeywordValue`。把过滤入口改为：

```ts
const keyword = normalizeProfessorSearchText(filters.keyword);
```

把范围匹配改为：

```ts
keywordSearchScopes.some((scope) =>
  matchesProfessorSearchField(getDashboardKeywordValue(professor, scope), keyword),
);
```

- [ ] **步骤 3：迁移导师管理过滤模块**

在 `filterManagementProfessors.ts` 顶部增加：

```ts
import {
  matchesProfessorSearchField,
  normalizeProfessorSearchText,
} from "@/lib/professorSearchField";
```

删除本地 `normalize`、`EMPTY_FIELD_SEARCH_KEYWORD` 和 `matchesKeywordValue`。把过滤入口改为：

```ts
const keyword = normalizeProfessorSearchText(filters.keyword);
```

把范围匹配改为：

```ts
keywordSearchScopes.some((scope) =>
  matchesProfessorSearchField(getManagementKeywordValue(professor, scope), keyword),
);
```

- [ ] **步骤 4：运行共享 helper 与两个过滤测试**

运行：

```bash
cd frontend
rtk npm run test -- --run src/lib/professorSearchField.test.ts src/features/home-dashboard/client/filterDashboardProfessors.test.ts src/features/professor-management/client/filterManagementProfessors.test.ts
```

预期：PASS，`3` 个测试文件、`44` 个测试通过；`无`、`无人机`、搜索范围和高级筛选行为不变。

- [ ] **步骤 5：检查重复实现已经删除**

运行：

```bash
rtk rg -n "EMPTY_FIELD_SEARCH_KEYWORD|matchesKeywordValue|const normalize =" frontend/src/features/home-dashboard/client/filterDashboardProfessors.ts frontend/src/features/professor-management/client/filterManagementProfessors.ts
```

预期：无输出，命令退出码为 `1`。

- [ ] **步骤 6：提交过滤器迁移**

```bash
rtk git add frontend/src/features/home-dashboard/client/filterDashboardProfessors.ts frontend/src/features/professor-management/client/filterManagementProfessors.ts
rtk git commit -m "refactor(frontend): 复用导师空字段搜索逻辑"
```

### 任务 3：首页整格空值显示为“无”

**文件：**
- 修改：`frontend/src/components/molecules/DashboardProfessorRow.test.tsx:32-176`
- 修改：`frontend/src/components/molecules/DashboardProfessorRow.tsx:1-38,82-104`

- [ ] **步骤 1：添加首页显示回归测试**

在 `DashboardProfessorRow.test.tsx` 中增加两个测试。第一个锁定整段组合摘要和研究方向的空值显示：

```tsx
it("shows 无 when existing homepage display cells are entirely empty", () => {
  render(
    <DashboardProfessorRow
      professor={{
        ...professor,
        title: "   ",
        university: null,
        school: "",
        research_direction: " \t ",
      }}
      selected={false}
      bulkDisabled={false}
      scoring={false}
      canCalculateMatch
      statusLabel="未发送"
      timeHighlight={null}
      timeLabel={null}
      onToggleSelection={vi.fn()}
      onCalculateMatch={vi.fn()}
      onOpenWorkspace={vi.fn()}
    />,
  );

  expect(screen.getAllByText("无")).toHaveLength(2);
});
```

第二个锁定组合字段部分缺失时不补 `无`：

```tsx
it("keeps only existing homepage summary values when fields are partially empty", () => {
  render(
    <DashboardProfessorRow
      professor={{
        ...professor,
        title: "   ",
        university: "  示例大学  ",
        school: null,
      }}
      selected={false}
      bulkDisabled={false}
      scoring={false}
      canCalculateMatch
      statusLabel="未发送"
      timeHighlight={null}
      timeLabel={null}
      onToggleSelection={vi.fn()}
      onCalculateMatch={vi.fn()}
      onOpenWorkspace={vi.fn()}
    />,
  );

  expect(screen.getByText("示例大学")).toBeInTheDocument();
  expect(screen.queryByText("无")).not.toBeInTheDocument();
});
```

在现有 `shows add tag button when professor has no tags` 测试中补充：

```ts
expect(screen.queryByText("无")).not.toBeInTheDocument();
```

- [ ] **步骤 2：运行首页行组件测试并确认失败**

运行：

```bash
cd frontend
rtk npm run test -- --run src/components/molecules/DashboardProfessorRow.test.tsx
```

预期：FAIL，新测试找不到两个文本为 `无` 的元素；现有实现仍渲染旧占位文案或纯空白。

- [ ] **步骤 3：实现首页显示格式化**

在 `DashboardProfessorRow.tsx` 中导入：

```ts
import { formatProfessorSearchField } from "@/lib/professorSearchField";
```

把组合 helper 改为忽略 `null`、`undefined`、空字符串和纯空白，同时清理非空内容两侧空格：

```ts
const joinNonEmpty = (values: Array<string | null | undefined>) =>
  values
    .map((value) => value?.trim() ?? "")
    .filter(Boolean)
    .join(" / ");
```

把两个显示位置改为：

```tsx
<div className="mt-1 text-sm text-stone-500">
  {formatProfessorSearchField(
    joinNonEmpty([professor.title, professor.university, professor.school]),
  )}
</div>
<p className="mt-2 line-clamp-2 text-sm leading-6 text-stone-600">
  {formatProfessorSearchField(professor.research_direction)}
</p>
```

不要修改 `ProfessorTagChips`，也不要给它传入空值文案。

- [ ] **步骤 4：运行首页行组件与共享 helper 测试**

运行：

```bash
cd frontend
rtk npm run test -- --run src/lib/professorSearchField.test.ts src/components/molecules/DashboardProfessorRow.test.tsx
```

预期：PASS，`2` 个测试文件全部通过；空标签测试仍确认页面不出现额外的 `无`。

- [ ] **步骤 5：提交首页显示改动**

```bash
rtk git add frontend/src/components/molecules/DashboardProfessorRow.tsx frontend/src/components/molecules/DashboardProfessorRow.test.tsx
rtk git commit -m "feat(frontend): 首页空字段显示为无"
```

### 任务 4：导师管理页整格空值显示为“无”

**文件：**
- 修改：`frontend/src/components/molecules/ManagementProfessorRow.test.tsx:46-165`
- 修改：`frontend/src/components/molecules/ManagementProfessorRow.tsx:1-55,102-128`

- [ ] **步骤 1：添加导师管理行显示回归测试**

在 `ManagementProfessorRow.test.tsx` 中增加整格空值测试：

```tsx
it("shows 无 for entirely empty management display cells", () => {
  render(
    <ManagementProfessorRow
      professor={{
        ...professor,
        title: "   ",
        email: "\t",
        university: null,
        school: "",
        research_direction: " \n ",
      }}
      checked={false}
      selectable
      tableColumns="lg:grid-cols-8"
      onToggleSelection={vi.fn()}
      onEdit={vi.fn()}
      onArchive={vi.fn()}
      onRestore={vi.fn()}
    />,
  );

  expect(screen.getAllByText("无")).toHaveLength(4);
});
```

增加组合字段部分缺失测试：

```tsx
it("keeps only existing school or college values when partially empty", () => {
  render(
    <ManagementProfessorRow
      professor={{
        ...professor,
        university: "   ",
        school: "  计算机学院  ",
      }}
      checked={false}
      selectable
      tableColumns="lg:grid-cols-8"
      onToggleSelection={vi.fn()}
      onEdit={vi.fn()}
      onArchive={vi.fn()}
      onRestore={vi.fn()}
    />,
  );

  expect(screen.getByText("计算机学院")).toBeInTheDocument();
  expect(screen.queryByText("无")).not.toBeInTheDocument();
});
```

在现有 `hides empty tag placeholder while keeping add button` 测试中补充：

```ts
expect(screen.queryByText("无")).not.toBeInTheDocument();
```

- [ ] **步骤 2：运行导师管理行测试并确认失败**

运行：

```bash
cd frontend
rtk npm run test -- --run src/components/molecules/ManagementProfessorRow.test.tsx
```

预期：FAIL，新测试无法找到 `4` 个文本为 `无` 的元素。

- [ ] **步骤 3：实现导师管理显示格式化**

在 `ManagementProfessorRow.tsx` 中导入：

```ts
import { formatProfessorSearchField } from "@/lib/professorSearchField";
```

把学校和学院组合改为：

```ts
const schoolAndCollege = [professor.university, professor.school]
  .map((value) => value?.trim() ?? "")
  .filter(Boolean)
  .join(" / ");
```

把四个显示位置改为：

```tsx
{formatProfessorSearchField(normalizeProfessorTitleDisplay(professor.title))}
```

```tsx
{formatProfessorSearchField(professor.email)}
```

```tsx
{formatProfessorSearchField(schoolAndCollege)}
```

```tsx
{formatProfessorSearchField(professor.research_direction)}
```

不要修改 `ProfessorTagChips`，也不要给它传入空值文案。

- [ ] **步骤 4：运行导师管理行与共享 helper 测试**

运行：

```bash
cd frontend
rtk npm run test -- --run src/lib/professorSearchField.test.ts src/components/molecules/ManagementProfessorRow.test.tsx
```

预期：PASS，`2` 个测试文件全部通过；空标签测试仍确认页面不出现额外的 `无`。

- [ ] **步骤 5：提交导师管理显示改动**

```bash
rtk git add frontend/src/components/molecules/ManagementProfessorRow.tsx frontend/src/components/molecules/ManagementProfessorRow.test.tsx
rtk git commit -m "feat(frontend): 导师管理空字段显示为无"
```

### 任务 5：执行完整前端验证

**文件：**
- 验证：`frontend/src/lib/professorSearchField.ts`
- 验证：`frontend/src/features/home-dashboard/client/filterDashboardProfessors.ts`
- 验证：`frontend/src/features/professor-management/client/filterManagementProfessors.ts`
- 验证：`frontend/src/components/molecules/DashboardProfessorRow.tsx`
- 验证：`frontend/src/components/molecules/ManagementProfessorRow.tsx`

- [ ] **步骤 1：运行所有相关聚焦测试**

运行：

```bash
cd frontend
rtk npm run test -- --run src/lib/professorSearchField.test.ts src/features/home-dashboard/client/filterDashboardProfessors.test.ts src/features/professor-management/client/filterManagementProfessors.test.ts src/components/molecules/DashboardProfessorRow.test.tsx src/components/molecules/ManagementProfessorRow.test.tsx
```

预期：PASS，所有共享 helper、过滤和行组件测试通过，失败数为 `0`。

- [ ] **步骤 2：运行完整前端测试**

运行：

```bash
cd frontend
rtk npm run test
```

预期：PASS，全部前端测试通过，失败数为 `0`。

- [ ] **步骤 3：运行 ESLint**

运行：

```bash
cd frontend
rtk npm run lint
```

预期：PASS，ESLint 退出码为 `0`。

- [ ] **步骤 4：运行 TypeScript 与生产构建**

运行：

```bash
cd frontend
rtk npm run build
```

预期：PASS，`tsc -b` 和 `vite build` 均成功，退出码为 `0`。

- [ ] **步骤 5：检查最终差异和工作区状态**

运行：

```bash
rtk git diff --check
rtk git status --short
rtk git log --oneline -6
```

预期：`git diff --check` 无输出；工作区干净；提交历史依次包含计划文档、共享 helper、过滤器迁移、首页显示和导师管理显示的聚焦提交。
