# frontend 测试抗文案变化 实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 收敛 `frontend` 中明显依赖中文文案的测试，让它们更多断言语义、结构、角色和状态，减少 UI 文案调整带来的测试噪音。

**架构：** 保留纯逻辑测试不动，优先改页面级和组件级测试。对关键交互使用 `role`、`aria-label`、`data-testid`、API mock 调用和状态变化断言；仅保留少量真正表达业务语义、且不易漂移的文本断言。

**技术栈：** React + Testing Library + Vitest。

---

### 任务 1：页面级测试去文案化

**文件：**
- 修改：`frontend/test/HomePageOnboarding.test.tsx`
- 修改：`frontend/test/CreateTaskPageCopy.test.tsx`
- 修改：`frontend/test/WorkspacePageNextStep.test.tsx`
- 修改：`frontend/test/TestComposePage.test.tsx`

- [x] **步骤 1：识别脆弱断言**

```ts
// 重点替换 getByText/queryByText 的标题、说明、步骤文案断言
// 改为 role、按钮可用性、路由、mock 调用、状态标记断言
```

- [x] **步骤 2：最小化重写测试**

```ts
expect(screen.getByRole("heading", { name: "导师看板" })).toBeInTheDocument();
expect(screen.getByRole("link", { name: "继续配置" })).toHaveAttribute("href", "/profile");
expect(mockedConfirm).toHaveBeenCalled();
```

- [x] **步骤 3：验证**

运行：`rtk pwsh -NoProfile -Command "Set-Location frontend; npm test"`
预期：相关页面测试通过，且不再依赖易漂移的正文文案。

### 任务 2：组件测试去文案化

**文件：**
- 修改：`frontend/test/WorkspaceComposerDockCopy.test.tsx`
- 修改：`frontend/test/NotificationViewport.test.tsx`
- 修改：`frontend/test/OtherSettingsCard.test.tsx`
- 修改：`frontend/test/TopNavBarIdentityLabels.test.tsx`
- 修改：`frontend/test/TokenUsageCenterCard.test.tsx`
- 修改：`frontend/test/ProfessorsPageNotifications.test.tsx`
- 修改：`frontend/test/DiagnosticLogPanel.test.tsx`

- [x] **步骤 1：把纯展示型文案断言降级为结构断言**

```ts
expect(screen.getByRole("button", { name: "保存设置" })).toBeEnabled();
expect(screen.getByTestId("notification-card")).toBeInTheDocument();
expect(screen.getByRole("button", { name: /Token 消耗记录中心/ })).toHaveAttribute("aria-expanded", "false");
```

- [x] **步骤 2：保留必要的业务语义断言**

```ts
expect(mockedSaveTestComposeDraft).toHaveBeenCalledWith(
  1,
  1,
  expect.objectContaining({ body_html: "<p>更新后的正文</p>" }),
);
```

- [x] **步骤 3：验证**

运行：`rtk pwsh -NoProfile -Command "Set-Location frontend; npm test"`
预期：组件测试通过，且文本断言主要停留在稳定的语义节点上。

### 任务 3：前端全量验证

**文件：**
- 无新增文件

- [x] **步骤 1：运行测试**

运行：`rtk pwsh -NoProfile -Command "Set-Location frontend; npm test"`

- [x] **步骤 2：运行 lint**

运行：`rtk pwsh -NoProfile -Command "Set-Location frontend; npm run lint"`

- [x] **步骤 3：运行构建**

运行：`rtk pwsh -NoProfile -Command "Set-Location frontend; npm run build"`

- [x] **步骤 4：收尾**

确认没有新增失败用例，保留纯逻辑测试原样，页面/组件测试只保留必要的文本断言。
