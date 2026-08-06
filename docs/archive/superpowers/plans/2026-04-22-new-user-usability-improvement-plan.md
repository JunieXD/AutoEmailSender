# 新用户首次上手易用性改造实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 在不重做路由结构的前提下，为首次使用者建立一条清晰、低术语、低跳转焦虑的上手主线，并把首页、个人页、创建任务页、工作区和文档统一到同一套引导语言。

**架构：** 本次改造以「可测试的文案与状态逻辑抽离 + 现有页面渐进增强」为主。先把首次上手状态、任务模式话术和工作区下一步提示抽成小型纯函数，再把这些能力接入首页、顶部状态栏、个人页、创建任务页和工作区，最后补齐 README 与运行手册中的首次使用说明。

**技术栈：** React 19、TypeScript、Vite、Vitest、Testing Library、FastAPI 文档配套更新

---

## 文件结构

### 新建文件

- `frontend/src/features/onboarding/client/getOnboardingState.ts`
  - 负责根据当前身份、模型、材料、模板和导师数据推导首次上手阶段、缺失项和下一步入口。
- `frontend/src/features/create-task/client/taskCopy.ts`
  - 负责统一创建任务页与工作区的动作语言，避免术语散落在页面里。
- `frontend/src/features/workspace/client/getWorkspaceNextStep.ts`
  - 负责根据任务状态、草稿状态和材料状态推导工作区的当前阶段提示。
- `frontend/src/components/molecules/OnboardingChecklistCard.tsx`
  - 负责渲染首页首次上手卡和跨页复用的下一步提示块。
- `frontend/test/getOnboardingState.test.ts`
  - 覆盖首次上手状态计算的主要分支。
- `frontend/test/taskCopy.test.ts`
  - 覆盖任务模式动作语言和字段文案映射。
- `frontend/test/getWorkspaceNextStep.test.ts`
  - 覆盖工作区阶段提示推导逻辑。

### 修改文件

- `frontend/src/pages/HomePage.tsx`
  - 接入首次上手卡和正常工作模式切换。
- `frontend/src/components/organisms/TopNavBar.tsx`
  - 把顶部切换区强化为更明确的状态表达。
- `frontend/src/pages/ProfilePage.tsx`
  - 重排信息顺序，并增加「完成基础配置后下一步去哪」提示。
- `frontend/src/pages/CreateTaskPage.tsx`
  - 用动作语言替换核心术语，并补充任务完成后的下一步说明。
- `frontend/src/components/organisms/WorkspaceComposerDock.tsx`
  - 增加阶段提示和动作导向文案。
- `frontend/src/pages/WorkspacePage.tsx`
  - 把工作区阶段提示接入头部或写信区入口。
- `README.md`
  - 补充真正面向新用户的首次启动说明。
- `docs/operations_runbook.md`
  - 把首次配置和 `dry run` 首次闭环说明收敛成更顺手的顺序。

### 验证文件

- `frontend/package.json`
  - 确认测试命令直接可用，不新增测试工具链。
- `frontend/vite.config.ts`
  - 保持当前 `vitest` 配置不变，仅复用现有 `jsdom` 与 `setup.ts`。

## 任务 1：抽离首次上手状态和动作语言基础能力

**文件：**
- 创建：`frontend/src/features/onboarding/client/getOnboardingState.ts`
- 创建：`frontend/src/features/create-task/client/taskCopy.ts`
- 创建：`frontend/src/features/workspace/client/getWorkspaceNextStep.ts`
- 测试：`frontend/test/getOnboardingState.test.ts`
- 测试：`frontend/test/taskCopy.test.ts`
- 测试：`frontend/test/getWorkspaceNextStep.test.ts`

- [ ] **步骤 1：编写首次上手状态失败测试**

```ts
import { describe, expect, it } from "vitest";
import { getOnboardingState } from "@/features/onboarding/client/getOnboardingState";

describe("getOnboardingState", () => {
  it("在没有身份时返回身份步骤", () => {
    expect(
      getOnboardingState({
        hasIdentity: false,
        hasLlmProfile: false,
        hasPrimaryMaterial: false,
        hasTemplate: false,
        professorCount: 0,
      }),
    ).toMatchObject({
      stage: "identity",
      nextActionHref: "/profile",
      completed: false,
    });
  });

  it("在基础配置完成但没有导师时返回导入导师步骤", () => {
    expect(
      getOnboardingState({
        hasIdentity: true,
        hasLlmProfile: true,
        hasPrimaryMaterial: true,
        hasTemplate: true,
        professorCount: 0,
      }),
    ).toMatchObject({
      stage: "professors",
      nextActionHref: "/professors",
      completed: false,
    });
  });
});
```

- [ ] **步骤 2：运行测试验证失败**

运行：`cd frontend && npm run test -- test/getOnboardingState.test.ts`

预期：`FAIL`，报错 `Cannot find module '@/features/onboarding/client/getOnboardingState'`。

- [ ] **步骤 3：编写首次上手状态最少实现代码**

```ts
export type OnboardingStage =
  | "identity"
  | "llm"
  | "materials"
  | "professors"
  | "first_task"
  | "ready";

export type OnboardingStateInput = {
  hasIdentity: boolean;
  hasLlmProfile: boolean;
  hasPrimaryMaterial: boolean;
  hasTemplate: boolean;
  professorCount: number;
};

export function getOnboardingState(input: OnboardingStateInput) {
  if (!input.hasIdentity) {
    return {
      stage: "identity" as const,
      completed: false,
      nextActionHref: "/profile",
      title: "先创建发件身份",
      description: "先配置一个可用的发件身份，后续的模板、材料和发送都会依附在它上面。",
    };
  }

  if (!input.hasLlmProfile) {
    return {
      stage: "llm" as const,
      completed: false,
      nextActionHref: "/profile",
      title: "继续配置 AI 模型",
      description: "先让系统知道该用哪个模型做匹配与草稿生成。",
    };
  }

  if (!input.hasPrimaryMaterial || !input.hasTemplate) {
    return {
      stage: "materials" as const,
      completed: false,
      nextActionHref: "/profile",
      title: "补齐材料和默认模板",
      description: "先准备一份可用于匹配的材料和一版默认写信模板。",
    };
  }

  if (input.professorCount === 0) {
    return {
      stage: "professors" as const,
      completed: false,
      nextActionHref: "/professors",
      title: "导入第一批导师",
      description: "准备好导师数据后，首页才会变成真正的工作台。",
    };
  }

  return {
    stage: "ready" as const,
    completed: true,
    nextActionHref: "/",
    title: "可以开始创建任务了",
    description: "基础准备已经完成，现在可以在首页筛选导师并发起第一批任务。",
  };
}
```

- [ ] **步骤 4：编写任务模式与工作区阶段失败测试**

```ts
import { describe, expect, it } from "vitest";
import { getTaskModeCopy } from "@/features/create-task/client/taskCopy";
import { getWorkspaceNextStep } from "@/features/workspace/client/getWorkspaceNextStep";

describe("taskCopy", () => {
  it("把 llm 模式翻译成 AI 辅助写信", () => {
    expect(getTaskModeCopy("llm").title).toBe("AI 辅助写信");
  });
});

describe("getWorkspaceNextStep", () => {
  it("在未生成草稿时提示先生成草稿", () => {
    expect(
      getWorkspaceNextStep({
        status: "matched",
        hasDraft: false,
        hasPrimaryMaterial: true,
      }).title,
    ).toBe("下一步：生成一版邮件草稿");
  });
});
```

- [ ] **步骤 5：运行测试验证失败**

运行：
- `cd frontend && npm run test -- test/taskCopy.test.ts`
- `cd frontend && npm run test -- test/getWorkspaceNextStep.test.ts`

预期：`FAIL`，提示相关模块不存在。

- [ ] **步骤 6：编写任务模式与工作区阶段最少实现代码**

```ts
import type { OutreachGenerationMode } from "@/types";

export function getTaskModeCopy(mode: OutreachGenerationMode) {
  if (mode === "template") {
    return {
      title: "直接套用模板",
      description: "直接按模板内容发给导师，适合统一表达。",
    };
  }

  return {
    title: "AI 辅助写信",
    description: "以你的模板为基础，自动生成更贴近导师背景的一版草稿。",
  };
}
```

```ts
type WorkspaceNextStepInput = {
  status: string | null;
  hasDraft: boolean;
  hasPrimaryMaterial: boolean;
};

export function getWorkspaceNextStep(input: WorkspaceNextStepInput) {
  if (!input.hasPrimaryMaterial) {
    return {
      title: "下一步：先选择用于分析的材料",
      description: "选好材料后，系统才能帮你判断这位导师是否匹配。",
    };
  }

  if (!input.hasDraft) {
    return {
      title: "下一步：生成一版邮件草稿",
      description: "先生成草稿，再根据内容决定是否继续修改和发送。",
    };
  }

  if (input.status === "scheduled") {
    return {
      title: "下一步：确认是否保留定时发送",
      description: "如果内容或时间要改，可以先取消定时再调整。",
    };
  }

  return {
    title: "下一步：人工检查后发送",
    description: "重点确认称呼、匹配理由、附件和发送方式。",
  };
}
```

- [ ] **步骤 7：运行测试验证通过**

运行：
- `cd frontend && npm run test -- test/getOnboardingState.test.ts`
- `cd frontend && npm run test -- test/taskCopy.test.ts`
- `cd frontend && npm run test -- test/getWorkspaceNextStep.test.ts`

预期：3 个测试文件均 `PASS`。

- [ ] **步骤 8：Commit**

```bash
git add frontend/src/features/onboarding/client/getOnboardingState.ts frontend/src/features/create-task/client/taskCopy.ts frontend/src/features/workspace/client/getWorkspaceNextStep.ts frontend/test/getOnboardingState.test.ts frontend/test/taskCopy.test.ts frontend/test/getWorkspaceNextStep.test.ts
git commit -m "feat(frontend): 抽离首次上手状态与动作语言"
```

## 任务 2：实现首页首次上手卡与顶部状态强化

**文件：**
- 创建：`frontend/src/components/molecules/OnboardingChecklistCard.tsx`
- 修改：`frontend/src/pages/HomePage.tsx`
- 修改：`frontend/src/components/organisms/TopNavBar.tsx`
- 测试：`frontend/test/HomePageOnboarding.test.tsx`

- [ ] **步骤 1：编写首页首次上手卡失败测试**

```tsx
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { HomePage } from "@/pages/HomePage";

it("在基础配置未完成时展示首次上手引导卡", () => {
  render(
    <MemoryRouter>
      <HomePage />
    </MemoryRouter>,
  );

  expect(screen.getByText("开始使用前，还差这几步")).toBeInTheDocument();
});
```

- [ ] **步骤 2：运行测试验证失败**

运行：`cd frontend && npm run test -- test/HomePageOnboarding.test.tsx`

预期：`FAIL`，当前页面仍渲染旧文案或找不到引导卡。

- [ ] **步骤 3：创建首次上手卡组件**

```tsx
type OnboardingChecklistCardProps = {
  title: string;
  description: string;
  nextActionHref: string;
  nextActionLabel: string;
  items: Array<{ label: string; done: boolean }>;
};

export const OnboardingChecklistCard = ({
  title,
  description,
  nextActionHref,
  nextActionLabel,
  items,
}: OnboardingChecklistCardProps) => (
  <section className="rounded-3xl border border-stone-200 bg-[#fcfbf8] p-6 shadow-sm">
    <h1 className="text-3xl font-semibold text-stone-900">{title}</h1>
    <p className="mt-2 text-sm leading-6 text-stone-600">{description}</p>
    <div className="mt-5 grid gap-2 md:grid-cols-2">
      {items.map((item) => (
        <div key={item.label} className="rounded-2xl border border-stone-200 bg-white px-4 py-3 text-sm">
          {item.done ? "已完成" : "待完成"}：{item.label}
        </div>
      ))}
    </div>
    <Link to={nextActionHref} data-interactive="button" className="ui-btn-primary mt-6">
      {nextActionLabel}
    </Link>
  </section>
);
```

- [ ] **步骤 4：在首页接入首次上手状态**

```tsx
const onboardingState = getOnboardingState({
  hasIdentity: Boolean(selectedIdentity),
  hasLlmProfile: Boolean(selectedLlmProfile),
  hasPrimaryMaterial: Boolean(selectedIdentity?.current_primary_material_id),
  hasTemplate: Boolean(
    selectedIdentity?.outreach_template_body_text?.trim() ||
      selectedIdentity?.outreach_template_body_html?.trim(),
  ),
  professorCount: professors.length,
});

if (!onboardingState.completed) {
  return (
    <main className="mx-auto max-w-6xl px-6 py-8">
      <OnboardingChecklistCard
        title="开始使用前，还差这几步"
        description={onboardingState.description}
        nextActionHref={onboardingState.nextActionHref}
        nextActionLabel="继续完成准备"
        items={[
          { label: "创建发件身份", done: Boolean(selectedIdentity) },
          { label: "配置 AI 模型", done: Boolean(selectedLlmProfile) },
          {
            label: "准备材料和模板",
            done: Boolean(selectedIdentity?.current_primary_material_id) && hasTemplate,
          },
          { label: "导入导师", done: professors.length > 0 },
        ]}
      />
    </main>
  );
}
```

- [ ] **步骤 5：强化顶部状态表达**

```tsx
<div className="text-[11px] font-medium text-stone-500">当前发送状态</div>
<div className={clsx("text-sm font-semibold", mailDeliveryMode === "live" ? "text-amber-900" : "text-emerald-900")}>
  {MAIL_DELIVERY_MODE_LABELS[mailDeliveryMode]}
</div>
<div className="mt-1 text-[11px] text-stone-500">
  {mailDeliveryMode === "live" ? "后续批准发送将真实发出邮件" : "当前只做本地演练，不会真的发信"}
</div>
```

- [ ] **步骤 6：运行测试验证通过**

运行：
- `cd frontend && npm run test -- test/HomePageOnboarding.test.tsx`
- `cd frontend && npm run lint`

预期：
- 首页首次上手测试 `PASS`
- `eslint` 无报错

- [ ] **步骤 7：Commit**

```bash
git add frontend/src/components/molecules/OnboardingChecklistCard.tsx frontend/src/pages/HomePage.tsx frontend/src/components/organisms/TopNavBar.tsx frontend/test/HomePageOnboarding.test.tsx
git commit -m "feat(frontend): 增加首次上手首页引导"
```

## 任务 3：重排个人页基础顺序并增加下一步提示

**文件：**
- 修改：`frontend/src/pages/ProfilePage.tsx`
- 测试：`frontend/test/ProfilePageOnboarding.test.tsx`

- [ ] **步骤 1：编写个人页顺序提示失败测试**

```tsx
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { ProfilePage } from "@/pages/ProfilePage";

it("在个人页展示首次上手顺序提示", () => {
  render(
    <MemoryRouter>
      <ProfilePage />
    </MemoryRouter>,
  );

  expect(screen.getByText("建议顺序：先完成基础发送，再补充 AI 和回信检测")).toBeInTheDocument();
});
```

- [ ] **步骤 2：运行测试验证失败**

运行：`cd frontend && npm run test -- test/ProfilePageOnboarding.test.tsx`

预期：`FAIL`，当前页面还没有该提示。

- [ ] **步骤 3：在个人页增加阶段化说明和顺序重排**

```tsx
<section className="rounded-3xl border border-stone-200 bg-[#fcfbf8] p-5 shadow-sm">
  <h1 className="text-2xl font-semibold text-stone-900">首次配置建议</h1>
  <p className="mt-2 text-sm leading-6 text-stone-600">
    建议顺序：先完成基础发送，再补充 AI 和回信检测。
  </p>
  <div className="mt-4 flex flex-wrap gap-2 text-xs text-stone-600">
    <span className="rounded-full border border-stone-200 bg-white px-3 py-1">1. 发件身份</span>
    <span className="rounded-full border border-stone-200 bg-white px-3 py-1">2. 材料与模板</span>
    <span className="rounded-full border border-stone-200 bg-white px-3 py-1">3. 模型配置</span>
    <span className="rounded-full border border-stone-200 bg-white px-3 py-1">4. 回信检测与高级设置</span>
  </div>
</section>
```

```tsx
<div className="mt-4 rounded-2xl border border-primary/15 bg-primary/5 px-4 py-3 text-sm text-stone-700">
  完成这部分后，下一步去「导师管理」导入第一批导师，再回首页开始创建任务。
</div>
```

- [ ] **步骤 4：把 SMTP、材料模板、模型、IMAP/高级设置调整为新顺序**

```tsx
<div className="space-y-6">
  <section aria-labelledby="profile-basic-send">...</section>
  <section aria-labelledby="profile-template-materials">...</section>
  <section aria-labelledby="profile-llm-config">...</section>
  <section aria-labelledby="profile-advanced-settings">...</section>
</div>
```

说明：保持 `ProfilePage.tsx` 现有大文件结构，不引入未定义的新组件名；仅通过调整区块渲染顺序、标题和说明文案，先展示基础发送与材料模板，再展示模型配置，最后展示 IMAP 与高级设置。

- [ ] **步骤 5：运行测试验证通过**

运行：
- `cd frontend && npm run test -- test/ProfilePageOnboarding.test.tsx`
- `cd frontend && npm run lint`

预期：测试 `PASS`，`eslint` 无报错。

- [ ] **步骤 6：Commit**

```bash
git add frontend/src/pages/ProfilePage.tsx frontend/test/ProfilePageOnboarding.test.tsx
git commit -m "feat(frontend): 重排个人页首次配置顺序"
```

## 任务 4：改造创建任务页与工作区的动作语言和下一步提示

**文件：**
- 修改：`frontend/src/pages/CreateTaskPage.tsx`
- 修改：`frontend/src/components/organisms/WorkspaceComposerDock.tsx`
- 修改：`frontend/src/pages/WorkspacePage.tsx`
- 测试：`frontend/test/CreateTaskPageCopy.test.tsx`
- 测试：`frontend/test/WorkspaceComposerDockCopy.test.tsx`

- [ ] **步骤 1：编写创建任务页文案失败测试**

```tsx
it("把 llm 模式显示为 AI 辅助写信", () => {
  render(<CreateTaskPage />);
  expect(screen.getByText("AI 辅助写信")).toBeInTheDocument();
});
```

- [ ] **步骤 2：运行测试验证失败**

运行：`cd frontend && npm run test -- test/CreateTaskPageCopy.test.tsx`

预期：`FAIL`，当前页面仍显示「模板润色」。

- [ ] **步骤 3：在创建任务页接入动作语言映射**

```tsx
const taskModeCopy = getTaskModeCopy(taskMode);

<div className="text-sm font-semibold text-stone-900">本次写信方式</div>
<p className="mt-1 text-xs leading-6 text-stone-500">{taskModeCopy.description}</p>
```

```tsx
const MODE_OPTIONS = [
  { value: "llm", ...getTaskModeCopy("llm") },
  { value: "template", ...getTaskModeCopy("template") },
];
```

- [ ] **步骤 4：补充创建任务完成后的下一步提示**

```tsx
<div className="rounded-2xl border border-dashed border-stone-300 bg-stone-50 px-4 py-3 text-sm text-stone-600">
  创建任务后，下一步通常是进入工作区生成草稿、人工检查，再决定立即发送或定时发送。
</div>
```

- [ ] **步骤 5：在工作区接入阶段提示**

```tsx
const nextStep = getWorkspaceNextStep({
  status: currentTask?.status ?? null,
  hasDraft: Boolean(subject.trim() || content.trim()),
  hasPrimaryMaterial: Boolean(currentTask?.primary_material_id),
});

<div className="rounded-2xl border border-stone-200 bg-stone-50 px-4 py-3 text-sm">
  <div className="font-medium text-stone-900">{nextStep.title}</div>
  <div className="mt-1 text-stone-600">{nextStep.description}</div>
</div>
```

同时调整按钮文案：

```tsx
<button ...>分析这位导师是否值得联系</button>
<button ...>生成一版邮件草稿</button>
```

- [ ] **步骤 6：运行测试验证通过**

运行：
- `cd frontend && npm run test -- test/CreateTaskPageCopy.test.tsx`
- `cd frontend && npm run test -- test/WorkspaceComposerDockCopy.test.tsx`
- `cd frontend && npm run lint`

预期：相关测试 `PASS`，`eslint` 无报错。

- [ ] **步骤 7：Commit**

```bash
git add frontend/src/pages/CreateTaskPage.tsx frontend/src/components/organisms/WorkspaceComposerDock.tsx frontend/src/pages/WorkspacePage.tsx frontend/test/CreateTaskPageCopy.test.tsx frontend/test/WorkspaceComposerDockCopy.test.tsx
git commit -m "feat(frontend): 优化任务与工作区动作提示"
```

## 任务 5：补齐新手文档并完成整体验证

**文件：**
- 修改：`README.md`
- 修改：`docs/operations_runbook.md`

- [ ] **步骤 1：为 README 补充新手首次使用说明**

```md
## 第一次使用建议顺序

1. 启动后端与前端。
2. 进入个人页，先配置发件身份和 SMTP。
3. 继续在个人页配置模型、上传材料并准备默认模板。
4. 进入导师管理页导入第一批导师。
5. 回到首页筛选导师并创建第一批任务。
6. 进入工作区生成草稿、人工确认后再发送。
```

- [ ] **步骤 2：把运行手册中的首次配置顺序改成与界面一致**

```md
## 首次配置建议

1. 在个人页创建一个身份，并完成 SMTP 测试。
2. 配置一套 LLM 模型并完成模型测试。
3. 上传一份默认材料，并准备一版默认模板。
4. 保持顶部发送模式为 `dry_run`。
5. 导入导师，创建第一批任务。
6. 在工作区先跑通匹配、草稿和本地演练发送。
7. 确认流程无误后，再考虑切换到 `live` 与补齐 IMAP。
```

- [ ] **步骤 3：运行完整验证**

运行：
- `cd frontend && npm run test`
- `cd frontend && npm run lint`
- `cd frontend && npm run build`

预期：
- `vitest` 全部通过
- `eslint` 全部通过
- `vite build` 成功输出生产包

- [ ] **步骤 4：手动回归关键路径**

手动检查：

1. 打开首页，在未完成配置时能看到首次上手卡。
2. 进入个人页，能看到清晰的首次配置顺序。
3. 创建任务页显示「AI 辅助写信 / 直接套用模板」。
4. 工作区能看到当前阶段和下一步提示。
5. 顶部栏能明确区分本地演练与真实发送。

- [ ] **步骤 5：Commit**

```bash
git add README.md docs/operations_runbook.md
git commit -m "docs: 补充新手首次使用说明"
```

## 自检结果

### 规格覆盖度

- 首页首次上手工作台：由任务 1、任务 2 覆盖。
- 个人页阶段化准备：由任务 3 覆盖。
- 创建任务页动作语言：由任务 1、任务 4 覆盖。
- 工作区下一步驱动：由任务 1、任务 4 覆盖。
- 顶部全局状态强化：由任务 2 覆盖。
- 文档层首次启动说明：由任务 5 覆盖。

### 占位符扫描

已检查计划内容，不包含「TODO」「待定」「后续实现」「类似任务 N」等占位语句。

### 类型一致性

- 首次上手状态统一使用 `OnboardingStage`。
- 任务动作语言统一通过 `getTaskModeCopy` 获取。
- 工作区提示统一通过 `getWorkspaceNextStep` 获取。
