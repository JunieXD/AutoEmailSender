# 全局消息提示 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a right-bottom global notification system for the frontend, mount it above dialogs, migrate the current success/warning/error/form-validation feedback into it, and remove the existing inline feedback paths that are supposed to become notifications.

**Architecture:** Keep the implementation frontend-only. Put notification timing, shaping, and queue trimming in a small pure helper module, expose the runtime API through a `NotificationProvider`, render cards through a `createPortal(...)` viewport, then migrate routed pages first and clean dormant legacy components second so the live app stabilizes before source cleanup.

**Tech Stack:** React 19, React DOM `createPortal`, Vite 8, TypeScript, Tailwind CSS 4, Vitest, jsdom, Testing Library

---

## File Map

- Create: `frontend/src/lib/notifications.ts`
  Hold the notification types, duration calculation, queue trimming, record creation, and form-error aggregation helpers.
- Create: `frontend/src/context/NotificationContext.tsx`
  Hold the notification queue state, runtime API, timer lifecycle, sticky-lock state transitions, and provider hook.
- Create: `frontend/src/components/organisms/NotificationViewport.tsx`
  Render the fixed right-bottom cards through a portal and handle hover/click/text-selection locking plus close actions.
- Create: `frontend/test/notifications.test.ts`
  Lock the pure helper contract before implementation.
- Create: `frontend/test/NotificationViewport.test.tsx`
  Lock queue limit, visual order, auto-dismiss, sticky-on-hover, and close-button behavior.
- Create: `frontend/test/SelectionContextNotifications.test.tsx`
  Lock that root-level context failures now surface as global notifications.
- Create: `frontend/test/ProfessorsPageNotifications.test.tsx`
  Lock that a migrated page emits a notification card instead of an inline banner for a validation error.
- Modify: `frontend/src/App.tsx`
  Mount `NotificationProvider` once for the whole routed app.
- Modify: `frontend/src/context/SelectionContext.tsx`
  Surface refresh/mode-switch failures through notifications instead of depending on page-local rendering.
- Modify: `frontend/src/pages/HomePage.tsx`
  Replace inline error feedback with notification calls while keeping neutral explanatory copy.
- Modify: `frontend/src/pages/CreateTaskPage.tsx`
  Aggregate validation failures into a single notification and remove the page-level inline error banner.
- Modify: `frontend/src/pages/TasksPage.tsx`
  Replace inline error feedback, add duplicate-load-error suppression for the polling loop, and keep neutral empty states.
- Modify: `frontend/src/pages/WorkspacePage.tsx`
  Replace action/load errors with notifications and stop passing inline error text to the composer dock.
- Modify: `frontend/src/components/organisms/WorkspaceComposerDock.tsx`
  Remove inline error rendering because feedback now lives in the global viewport.
- Modify: `frontend/src/pages/ProfessorsPage.tsx`
  Replace page-level success/error banners with notifications while keeping the import-result detail card.
- Modify: `frontend/src/pages/ProfilePage.tsx`
  Replace `identityMessage`, `llmMessage`, connection/material feedback banners, and selection error rendering with notification calls while preserving detailed diagnostics panels.
- Modify: `frontend/src/components/organisms/MentorDashboardClient.tsx`
  Replace leftover `window.alert(...)` placeholders in a dormant dashboard component.
- Modify: `frontend/src/components/organisms/TasksDashboardClient.tsx`
  Replace leftover `window.alert(...)` placeholders in a dormant dashboard component.
- Modify: `frontend/src/components/organisms/CreateTaskClient.tsx`
  Replace legacy inline validation/submission feedback with notification calls.
- Modify: `frontend/src/features/create-task/client/useCreateTaskForm.ts`
  Remove legacy `errors` bookkeeping that only exists for inline field-level rendering.
- Modify: `frontend/src/components/molecules/TaskNameInput.tsx`
  Remove legacy inline field-error props and rendering.
- Modify: `frontend/src/components/molecules/TaskEmailContent.tsx`
  Remove legacy inline field-error props and rendering.
- Modify: `frontend/src/components/molecules/TaskScheduleSettings.tsx`
  Remove legacy inline field-error props and rendering.
- Modify: `frontend/src/components/atoms/TaskTimePicker.tsx`
  Remove legacy inline field-error props and rendering.

## Task 1: Add the pure notification helper contract first

**Files:**
- Create: `frontend/test/notifications.test.ts`
- Create: `frontend/src/lib/notifications.ts`

- [ ] **Step 1: Write the failing helper test**

Create `frontend/test/notifications.test.ts`:

```ts
import { describe, expect, it } from "vitest";
import {
  MAX_VISIBLE_NOTIFICATIONS,
  calculateNotificationDuration,
  createFormErrorNotification,
  createNotificationRecord,
  trimNotifications,
  type NotificationDraft,
} from "@/lib/notifications";

const buildDraft = (overrides: Partial<NotificationDraft> = {}): NotificationDraft => ({
  level: "success",
  title: "保存成功",
  description: "已更新导师配置",
  details: [],
  ...overrides,
});

describe("calculateNotificationDuration", () => {
  it("keeps success notifications in the 2-3 second band", () => {
    const duration = calculateNotificationDuration(
      buildDraft({ level: "success", title: "保存成功", description: "已更新导师配置" }),
    );

    expect(duration).toBeGreaterThanOrEqual(2000);
    expect(duration).toBeLessThanOrEqual(3000);
  });

  it("keeps warning notifications in the 3-6 second band", () => {
    const duration = calculateNotificationDuration(
      buildDraft({
        level: "warning",
        title: "请注意配置",
        description: "当前身份还没有默认材料，暂时无法计算匹配。",
      }),
    );

    expect(duration).toBeGreaterThanOrEqual(3000);
    expect(duration).toBeLessThanOrEqual(6000);
  });

  it("keeps error notifications in the 5-8 second band", () => {
    const duration = calculateNotificationDuration(
      buildDraft({
        level: "error",
        title: "请检查表单",
        details: ["请输入任务名称", "请选择开始时间", "请选择结束时间"],
      }),
    );

    expect(duration).toBeGreaterThanOrEqual(5000);
    expect(duration).toBeLessThanOrEqual(8000);
  });
});

describe("createFormErrorNotification", () => {
  it("builds one aggregated error notification and keeps the error order", () => {
    expect(
      createFormErrorNotification("请检查表单", [
        "请输入任务名称",
        "",
        "请选择开始时间",
        "请选择结束时间",
      ]),
    ).toEqual({
      level: "error",
      title: "请检查表单",
      description: "",
      details: ["请输入任务名称", "请选择开始时间", "请选择结束时间"],
    });
  });
});

describe("trimNotifications", () => {
  it("keeps only the latest visible notifications", () => {
    const notifications = ["1", "2", "3", "4", "5"].map((id) =>
      createNotificationRecord(buildDraft({ title: `第 ${id} 条` }), {
        id,
        createdAt: Number(id),
      }),
    );

    expect(trimNotifications(notifications).map((item) => item.id)).toEqual([
      "2",
      "3",
      "4",
      "5",
    ]);
    expect(MAX_VISIBLE_NOTIFICATIONS).toBe(4);
  });
});
```

- [ ] **Step 2: Run the helper test and verify it fails because the module does not exist yet**

Run:

```bash
cd frontend
npm test -- test/notifications.test.ts
```

Expected:

```text
FAIL  test/notifications.test.ts
Error: Failed to resolve import "@/lib/notifications"
```

- [ ] **Step 3: Implement the helper module**

Create `frontend/src/lib/notifications.ts`:

```ts
export const MAX_VISIBLE_NOTIFICATIONS = 4;

export type NotificationLevel = "success" | "warning" | "error";

export type NotificationDraft = {
  level: NotificationLevel;
  title: string;
  description?: string;
  details?: string[];
};

export type NotificationRecord = NotificationDraft & {
  id: string;
  createdAt: number;
  durationMs: number;
  interactiveLocked: boolean;
  closing: boolean;
};

const clamp = (value: number, min: number, max: number) =>
  Math.min(max, Math.max(min, value));

const measureTextLength = (draft: NotificationDraft) =>
  [draft.title, draft.description ?? "", ...(draft.details ?? [])].join("").length;

export const calculateNotificationDuration = (draft: NotificationDraft): number => {
  const textLength = measureTextLength(draft);

  if (draft.level === "success") {
    return clamp(2200 + textLength * 45, 2000, 3000);
  }

  if (draft.level === "warning") {
    return clamp(3200 + textLength * 55, 3000, 6000);
  }

  return clamp(5200 + textLength * 65, 5000, 8000);
};

export const createFormErrorNotification = (
  title: string,
  errors: string[],
): NotificationDraft => ({
  level: "error",
  title,
  description: "",
  details: errors.map((item) => item.trim()).filter(Boolean),
});

export const createNotificationRecord = (
  draft: NotificationDraft,
  options: { id: string; createdAt?: number },
): NotificationRecord => {
  const createdAt = options.createdAt ?? Date.now();

  return {
    ...draft,
    description: draft.description?.trim() ?? "",
    details: draft.details?.map((item) => item.trim()).filter(Boolean) ?? [],
    id: options.id,
    createdAt,
    durationMs: calculateNotificationDuration(draft),
    interactiveLocked: false,
    closing: false,
  };
};

export const trimNotifications = (notifications: NotificationRecord[]) =>
  notifications.slice(-MAX_VISIBLE_NOTIFICATIONS);
```

- [ ] **Step 4: Run the helper test and verify it passes**

Run:

```bash
cd frontend
npm test -- test/notifications.test.ts
```

Expected:

```text
✓ test/notifications.test.ts
```

- [ ] **Step 5: Commit the helper layer**

```bash
git add frontend/test/notifications.test.ts frontend/src/lib/notifications.ts
git commit -m "feat(frontend): add notification helper primitives"
```

## Task 2: Build the provider and viewport, then mount them at the app root

**Files:**
- Create: `frontend/test/NotificationViewport.test.tsx`
- Create: `frontend/src/context/NotificationContext.tsx`
- Create: `frontend/src/components/organisms/NotificationViewport.tsx`
- Modify: `frontend/src/App.tsx`

- [ ] **Step 1: Write the failing viewport interaction test**

Create `frontend/test/NotificationViewport.test.tsx`:

```tsx
import { fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  NotificationProvider,
  useNotification,
} from "@/context/NotificationContext";

const Harness = () => {
  const {
    notifySuccess,
    notifyError,
    notifyFormErrors,
  } = useNotification();

  return (
    <div>
      <button type="button" onClick={() => notifySuccess("第一条", "第一条内容")}>
        第一条
      </button>
      <button type="button" onClick={() => notifySuccess("第二条", "第二条内容")}>
        第二条
      </button>
      <button type="button" onClick={() => notifySuccess("第三条", "第三条内容")}>
        第三条
      </button>
      <button type="button" onClick={() => notifySuccess("第四条", "第四条内容")}>
        第四条
      </button>
      <button type="button" onClick={() => notifySuccess("第五条", "第五条内容")}>
        第五条
      </button>
      <button
        type="button"
        onClick={() =>
          notifyFormErrors("请检查表单", ["请输入任务名称", "请选择开始时间"])
        }
      >
        表单错误
      </button>
      <button
        type="button"
        onClick={() => notifyError("复制这条报错", "这里有一段较长的报错正文。")}
      >
        长报错
      </button>
    </div>
  );
};

describe("NotificationViewport", () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.runOnlyPendingTimers();
    vi.useRealTimers();
  });

  it("shows only the latest four notifications and keeps the newest at the bottom", () => {
    render(
      <NotificationProvider>
        <Harness />
      </NotificationProvider>,
    );

    for (const label of ["第一条", "第二条", "第三条", "第四条", "第五条"]) {
      fireEvent.click(screen.getByRole("button", { name: label }));
    }

    const titles = screen
      .getAllByTestId("notification-title")
      .map((node) => node.textContent);

    expect(titles).toEqual(["第二条", "第三条", "第四条", "第五条"]);
  });

  it("renders aggregated form errors as one notification card", () => {
    render(
      <NotificationProvider>
        <Harness />
      </NotificationProvider>,
    );

    fireEvent.click(screen.getByRole("button", { name: "表单错误" }));

    const card = screen.getByTestId("notification-card");
    expect(within(card).getByText("请检查表单")).toBeInTheDocument();
    expect(within(card).getByText("请输入任务名称")).toBeInTheDocument();
    expect(within(card).getByText("请选择开始时间")).toBeInTheDocument();
  });

  it("auto-dismisses untouched notifications but keeps hovered notifications sticky", () => {
    render(
      <NotificationProvider>
        <Harness />
      </NotificationProvider>,
    );

    fireEvent.click(screen.getByRole("button", { name: "长报错" }));

    const card = screen.getByTestId("notification-card");
    fireEvent.mouseEnter(card);
    vi.advanceTimersByTime(10000);

    expect(screen.getByText("复制这条报错")).toBeInTheDocument();

    fireEvent.click(
      within(card).getByRole("button", { name: "关闭提示" }),
    );
    vi.advanceTimersByTime(200);

    expect(screen.queryByText("复制这条报错")).not.toBeInTheDocument();
  });
});
```

- [ ] **Step 2: Run the viewport test and verify it fails because the provider does not exist yet**

Run:

```bash
cd frontend
npx vitest run test/NotificationViewport.test.tsx
```

Expected:

```text
FAIL  test/NotificationViewport.test.tsx
Error: Failed to resolve import "@/context/NotificationContext"
```

- [ ] **Step 3: Implement the provider and viewport**

Create `frontend/src/context/NotificationContext.tsx`:

```tsx
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type PropsWithChildren,
} from "react";
import { NotificationViewport } from "@/components/organisms/NotificationViewport";
import {
  createFormErrorNotification,
  createNotificationRecord,
  trimNotifications,
  type NotificationDraft,
  type NotificationRecord,
} from "@/lib/notifications";

type NotificationContextValue = {
  notifications: NotificationRecord[];
  notify: (draft: NotificationDraft) => string;
  notifySuccess: (title: string, description?: string) => string;
  notifyWarning: (title: string, description?: string) => string;
  notifyError: (title: string, description?: string) => string;
  notifyFormErrors: (title: string, errors: string[]) => string | null;
  dismissNotification: (id: string) => void;
  lockNotification: (id: string) => void;
};

const NotificationContext = createContext<NotificationContextValue | null>(null);
const CLOSE_ANIMATION_MS = 180;

export const NotificationProvider = ({ children }: PropsWithChildren) => {
  const [notifications, setNotifications] = useState<NotificationRecord[]>([]);
  const sequenceRef = useRef(0);

  const dismissNotification = useCallback((id: string) => {
    setNotifications((current) =>
      current.map((item) =>
        item.id === id ? { ...item, closing: true } : item,
      ),
    );

    window.setTimeout(() => {
      setNotifications((current) => current.filter((item) => item.id !== id));
    }, CLOSE_ANIMATION_MS);
  }, []);

  const lockNotification = useCallback((id: string) => {
    setNotifications((current) =>
      current.map((item) =>
        item.id === id ? { ...item, interactiveLocked: true } : item,
      ),
    );
  }, []);

  const notify = useCallback((draft: NotificationDraft) => {
    sequenceRef.current += 1;
    const createdAt = Date.now();
    const record = createNotificationRecord(draft, {
      id: `notification-${sequenceRef.current}`,
      createdAt,
    });

    setNotifications((current) => trimNotifications([...current, record]));
    return record.id;
  }, []);

  const notifySuccess = useCallback(
    (title: string, description = "") =>
      notify({ level: "success", title, description, details: [] }),
    [notify],
  );

  const notifyWarning = useCallback(
    (title: string, description = "") =>
      notify({ level: "warning", title, description, details: [] }),
    [notify],
  );

  const notifyError = useCallback(
    (title: string, description = "") =>
      notify({ level: "error", title, description, details: [] }),
    [notify],
  );

  const notifyFormErrors = useCallback(
    (title: string, errors: string[]) => {
      const draft = createFormErrorNotification(title, errors);
      if (draft.details.length === 0) {
        return null;
      }
      return notify(draft);
    },
    [notify],
  );

  useEffect(() => {
    const timers = notifications
      .filter((item) => !item.interactiveLocked && !item.closing)
      .map((item) => {
        const remainingMs = Math.max(
          0,
          item.createdAt + item.durationMs - Date.now(),
        );

        return window.setTimeout(() => {
          dismissNotification(item.id);
        }, remainingMs);
      });

    return () => timers.forEach((timerId) => window.clearTimeout(timerId));
  }, [dismissNotification, notifications]);

  const value = useMemo<NotificationContextValue>(
    () => ({
      notifications,
      notify,
      notifySuccess,
      notifyWarning,
      notifyError,
      notifyFormErrors,
      dismissNotification,
      lockNotification,
    }),
    [
      notifications,
      notify,
      notifySuccess,
      notifyWarning,
      notifyError,
      notifyFormErrors,
      dismissNotification,
      lockNotification,
    ],
  );

  return (
    <NotificationContext.Provider value={value}>
      {children}
      <NotificationViewport
        notifications={notifications}
        onLock={lockNotification}
        onDismiss={dismissNotification}
      />
    </NotificationContext.Provider>
  );
};

export const useNotification = () => {
  const context = useContext(NotificationContext);
  if (!context) {
    throw new Error("NotificationContext 未初始化");
  }
  return context;
};
```

Create `frontend/src/components/organisms/NotificationViewport.tsx`:

```tsx
import { createPortal } from "react-dom";
import clsx from "clsx";
import { AlertTriangle, CheckCircle2, X, XCircle } from "lucide-react";
import type { NotificationRecord } from "@/lib/notifications";

type NotificationViewportProps = {
  notifications: NotificationRecord[];
  onLock: (id: string) => void;
  onDismiss: (id: string) => void;
};

const LEVEL_STYLES = {
  success: {
    icon: CheckCircle2,
    card: "border-emerald-200 bg-emerald-50/95 text-emerald-900",
    iconWrap: "bg-emerald-100 text-emerald-700",
  },
  warning: {
    icon: AlertTriangle,
    card: "border-amber-200 bg-amber-50/95 text-amber-900",
    iconWrap: "bg-amber-100 text-amber-700",
  },
  error: {
    icon: XCircle,
    card: "border-red-200 bg-red-50/95 text-red-900",
    iconWrap: "bg-red-100 text-red-700",
  },
} as const;

export const NotificationViewport = ({
  notifications,
  onLock,
  onDismiss,
}: NotificationViewportProps) => {
  if (typeof document === "undefined") {
    return null;
  }

  return createPortal(
    <div className="pointer-events-none fixed inset-x-4 bottom-4 z-[120] flex justify-end sm:inset-x-6 sm:bottom-6">
      <div className="flex w-full max-w-sm flex-col gap-3">
        {notifications.map((notification) => {
          const styles = LEVEL_STYLES[notification.level];
          const Icon = styles.icon;

          return (
            <section
              key={notification.id}
              data-testid="notification-card"
              onMouseEnter={() => onLock(notification.id)}
              onMouseDown={() => onLock(notification.id)}
              onMouseUp={() => {
                if (window.getSelection()?.toString()) {
                  onLock(notification.id);
                }
              }}
              onClick={(event) => {
                if ((event.target as HTMLElement).closest("a,button")) {
                  onLock(notification.id);
                }
              }}
              className={clsx(
                "pointer-events-auto rounded-[24px] border px-4 py-4 shadow-[0_24px_44px_-30px_rgba(41,37,36,0.42)] backdrop-blur transition duration-150",
                styles.card,
                notification.closing && "translate-y-2 opacity-0",
              )}
            >
              <div className="flex items-start gap-3">
                <div
                  className={clsx(
                    "mt-0.5 flex h-9 w-9 shrink-0 items-center justify-center rounded-2xl",
                    styles.iconWrap,
                  )}
                >
                  <Icon className="h-4 w-4" />
                </div>

                <div className="min-w-0 flex-1">
                  <div
                    data-testid="notification-title"
                    className="text-sm font-semibold leading-6"
                  >
                    {notification.title}
                  </div>
                  {notification.description ? (
                    <p className="mt-1 text-sm leading-6 select-text">
                      {notification.description}
                    </p>
                  ) : null}
                  {notification.details.length > 0 ? (
                    <ul className="mt-2 list-disc space-y-1 pl-5 text-sm leading-6 select-text">
                      {notification.details.map((detail) => (
                        <li key={detail}>{detail}</li>
                      ))}
                    </ul>
                  ) : null}
                </div>

                <button
                  type="button"
                  aria-label="关闭提示"
                  onClick={() => onDismiss(notification.id)}
                  className="rounded-xl p-1 text-current/70 transition hover:bg-black/5 hover:text-current"
                >
                  <X className="h-4 w-4" />
                </button>
              </div>
            </section>
          );
        })}
      </div>
    </div>,
    document.body,
  );
};
```

Update `frontend/src/App.tsx`:

```tsx
import { NotificationProvider } from "@/context/NotificationContext";

function App() {
  return (
    <BrowserRouter>
      <NotificationProvider>
        <SelectionProvider>
          <div className="flex min-h-screen flex-col bg-background">
            <TopNavBar />
            <div className="min-h-0 flex-1">
              <Routes>
                <Route path="/" element={<HomePage />} />
                <Route path="/professors" element={<ProfessorsPage />} />
                <Route path="/tasks" element={<TasksPage />} />
                <Route path="/create-task" element={<CreateTaskPage />} />
                <Route path="/workspace/:id" element={<WorkspacePage />} />
                <Route path="/profile" element={<ProfilePage />} />
                <Route path="/404" element={<NotFoundPage />} />
                <Route path="*" element={<NotFoundPage />} />
              </Routes>
            </div>
          </div>
        </SelectionProvider>
      </NotificationProvider>
    </BrowserRouter>
  );
}
```

- [ ] **Step 4: Run the viewport test and verify it passes**

Run:

```bash
cd frontend
npx vitest run test/NotificationViewport.test.tsx
```

Expected:

```text
✓ test/NotificationViewport.test.tsx
```

- [ ] **Step 5: Commit the runtime notification shell**

```bash
git add frontend/src/context/NotificationContext.tsx frontend/src/components/organisms/NotificationViewport.tsx frontend/src/App.tsx frontend/test/NotificationViewport.test.tsx
git commit -m "feat(frontend): add global notification provider and viewport"
```

## Task 3: Migrate root context and the currently routed core pages

**Files:**
- Create: `frontend/test/SelectionContextNotifications.test.tsx`
- Modify: `frontend/src/context/SelectionContext.tsx`
- Modify: `frontend/src/pages/HomePage.tsx`
- Modify: `frontend/src/pages/CreateTaskPage.tsx`
- Modify: `frontend/src/pages/TasksPage.tsx`
- Modify: `frontend/src/pages/WorkspacePage.tsx`
- Modify: `frontend/src/components/organisms/WorkspaceComposerDock.tsx`

- [ ] **Step 1: Write a failing integration test for `SelectionContext` notification output**

Create `frontend/test/SelectionContextNotifications.test.tsx`:

```tsx
import { render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { NotificationProvider } from "@/context/NotificationContext";
import { SelectionProvider } from "@/context/SelectionContext";

const listIdentities = vi.hoisted(() => vi.fn());
const listLLMProfiles = vi.hoisted(() => vi.fn());
const getSystemSettings = vi.hoisted(() => vi.fn());
const updateSystemSettings = vi.hoisted(() => vi.fn());

vi.mock("@/lib/api/identities", () => ({
  listIdentities,
}));

vi.mock("@/lib/api/llmProfiles", () => ({
  listLLMProfiles,
}));

vi.mock("@/lib/api/systemSettings", () => ({
  getSystemSettings,
  updateSystemSettings,
}));

describe("SelectionContext notifications", () => {
  beforeEach(() => {
    listIdentities.mockReset();
    listLLMProfiles.mockReset();
    getSystemSettings.mockReset();
    updateSystemSettings.mockReset();
  });

  it("shows a global notification card when the initial refresh fails", async () => {
    listIdentities.mockRejectedValue(new Error("加载全局上下文失败"));
    listLLMProfiles.mockResolvedValue([]);
    getSystemSettings.mockResolvedValue({ mail_delivery_mode: "dry_run" });

    render(
      <NotificationProvider>
        <SelectionProvider>
          <div>selection harness</div>
        </SelectionProvider>
      </NotificationProvider>,
    );

    await waitFor(() => {
      const message = screen.getByText("加载全局上下文失败");
      expect(message.closest('[data-testid="notification-card"]')).not.toBeNull();
    });
  });
});
```

- [ ] **Step 2: Run the integration test and verify it fails because `SelectionContext` still only updates local state**

Run:

```bash
cd frontend
npx vitest run test/SelectionContextNotifications.test.tsx
```

Expected:

```text
FAIL  test/SelectionContextNotifications.test.tsx
Unable to find an element by: [data-testid="notification-card"]
```

- [ ] **Step 3: Route context and page feedback through `useNotification`**

Update `frontend/src/context/SelectionContext.tsx`:

```tsx
import { useNotification } from "@/context/NotificationContext";

export const SelectionProvider = ({ children }: PropsWithChildren) => {
  const { notifyError } = useNotification();
  ...

  const refreshSelections = useCallback(async () => {
    if (!bootstrapped) {
      setLoading(true);
    }
    try {
      const [identityData, llmData, settingsData] = await Promise.all([
        listIdentities(),
        listLLMProfiles(),
        getSystemSettings(),
      ]);
      setIdentities(identityData);
      setLlmProfiles(llmData);
      setSystemSettings(settingsData);
      setError(null);
    } catch (refreshError) {
      const message =
        refreshError instanceof Error ? refreshError.message : "加载全局上下文失败";
      setError(message);
      notifyError("加载全局上下文失败", message);
    } finally {
      setLoading(false);
      setBootstrapped(true);
    }
  }, [bootstrapped, notifyError]);

  const setMailDeliveryMode = async (value: MailDeliveryMode) => {
    setUpdatingMode(true);
    try {
      const nextSettings = await updateSystemSettings(value);
      setSystemSettings(nextSettings);
      setError(null);
    } catch (updateError) {
      const message =
        updateError instanceof Error ? updateError.message : "切换发送模式失败";
      setError(message);
      notifyError("切换发送模式失败", message);
      throw updateError;
    } finally {
      setUpdatingMode(false);
    }
  };
```

Update `frontend/src/pages/HomePage.tsx`:

```tsx
import { useNotification } from "@/context/NotificationContext";

export const HomePage = () => {
  const { notifyError, notifyWarning } = useNotification();
  ...

  const loadProfessors = useCallback(async () => {
    if (!selectedIdentityId || !selectedLlmProfileId) {
      setProfessors([]);
      return;
    }
    setLoading(true);
    try {
      const data = await listProfessors({
        identityId: selectedIdentityId,
        llmProfileId: selectedLlmProfileId,
      });
      setProfessors(data);
      ...
    } catch (loadError) {
      const message =
        loadError instanceof Error ? loadError.message : "加载导师列表失败";
      notifyError("加载导师列表失败", message);
    } finally {
      setLoading(false);
    }
  }, [notifyError, selectedIdentityId, selectedLlmProfileId]);

  const handleGenerateOne = async (professorId: number) => {
    if (!hasPrimaryMaterial) {
      notifyWarning(
        "请先设置默认材料",
        "当前身份还没有默认材料，暂时无法计算匹配。请先到个人页设置默认材料。",
      );
      return;
    }
    ...
    } catch (actionError) {
      const message =
        actionError instanceof Error ? actionError.message : "计算匹配失败";
      notifyError("计算匹配失败", message);
    } finally {
      toggleScoringProfessor(professorId, false);
    }
  };

  const handleGenerateSelected = async () => {
    ...
    if (!hasPrimaryMaterial) {
      notifyWarning(
        "请先设置默认材料",
        "当前身份还没有默认材料，暂时无法批量计算匹配。请先到个人页设置默认材料。",
      );
      return;
    }
    ...
    if (failedNames.length > 0) {
      notifyError("部分导师计算失败", failedNames.slice(0, 2).join("；"));
    }
  };
```

Update `frontend/src/pages/CreateTaskPage.tsx`:

```tsx
import { useNotification } from "@/context/NotificationContext";

export const CreateTaskPage = () => {
  const { notifyError, notifyFormErrors } = useNotification();
  ...

  useEffect(() => {
    const loadProfessors = async () => {
      ...
      try {
        const data = await listProfessors({
          identityId: selectedIdentityId,
          llmProfileId: selectedLlmProfileId,
          ids: selectedProfessorIds,
        });
        setProfessors(data);
      } catch (loadError) {
        const message =
          loadError instanceof Error ? loadError.message : "加载已选导师失败";
        notifyError("加载已选导师失败", message);
      } finally {
        setLoading(false);
      }
    };
```

Then replace `handleSubmit()` with aggregated validation:

```tsx
  const handleSubmit = async () => {
    const validationErrors: string[] = [];

    if (!selectedIdentityId || !selectedLlmProfileId) {
      validationErrors.push("请先选择身份和模型");
    }
    if (!taskName.trim()) {
      validationErrors.push("任务名称不能为空");
    }
    if (professors.length === 0) {
      validationErrors.push("当前没有可执行的导师");
    }
    if (scheduleType === "scheduled" && (!startTime || !endTime || !emailsPerWindow)) {
      validationErrors.push("定时发送需要填写发送时间窗口和窗口内发送数量");
    }
    if (taskMode === "template" && !templateReady) {
      validationErrors.push("固定模板模式至少需要填写纯文本正文或 HTML 正文");
    }
    if (taskMode === "llm" && !body.trim()) {
      validationErrors.push("模板润色模式至少需要填写一份套磁信模板正文");
    }

    if (validationErrors.length > 0) {
      notifyFormErrors("请检查表单", validationErrors);
      return;
    }

    setSubmitting(true);
    try {
      ...
      window.sessionStorage.removeItem(SESSION_KEY);
      navigate("/tasks");
    } catch (submitError) {
      const message =
        submitError instanceof Error ? submitError.message : "创建批量任务失败";
      notifyError("创建任务失败", message);
    } finally {
      setSubmitting(false);
    }
  };
```

Remove the inline page banner:

```tsx
              {error && <p className="text-sm text-red-600">{error}</p>}
```

Update `frontend/src/pages/TasksPage.tsx`:

```tsx
import { useRef } from "react";
import { useNotification } from "@/context/NotificationContext";

export const TasksPage = () => {
  const { notifyError } = useNotification();
  const lastLoadErrorRef = useRef<string | null>(null);
  ...

  const loadTasks = useCallback(async () => {
    if (!selectedIdentityId || !selectedLlmProfileId) {
      setTasks([]);
      lastLoadErrorRef.current = null;
      return;
    }
    setLoading(true);
    try {
      const data = await listBatchTasks({
        identityId: selectedIdentityId,
        llmProfileId: selectedLlmProfileId,
      });
      setTasks(data);
      lastLoadErrorRef.current = null;
    } catch (loadError) {
      const message =
        loadError instanceof Error ? loadError.message : "加载任务失败";
      if (lastLoadErrorRef.current !== message) {
        notifyError("加载任务失败", message);
        lastLoadErrorRef.current = message;
      }
    } finally {
      setLoading(false);
    }
  }, [notifyError, selectedIdentityId, selectedLlmProfileId]);

  const handleAction = async (taskId: number, action: "pause" | "resume" | "stop") => {
    try {
      ...
      await loadTasks();
    } catch (actionError) {
      const message =
        actionError instanceof Error ? actionError.message : "任务操作失败";
      notifyError("任务操作失败", message);
    }
  };
```

Remove the inline error line:

```tsx
        {error ? <p className="mt-4 text-sm text-red-600">{error}</p> : null}
```

Update `frontend/src/pages/WorkspacePage.tsx`:

```tsx
import { useNotification } from "@/context/NotificationContext";

export const WorkspacePage = () => {
  const { notifyError, notifyFormErrors } = useNotification();
  const [loadFailed, setLoadFailed] = useState(false);
  ...

  const loadThread = useCallback(async () => {
    if (!selectedIdentityId || !selectedLlmProfileId || !Number.isFinite(professorId)) {
      setThread(null);
      setLoadFailed(false);
      return;
    }

    setLoading(true);
    try {
      ...
      setThread(workspaceData);
      setLoadFailed(false);
      syncComposer(workspaceData);
    } catch (loadError) {
      const message = loadError instanceof Error ? loadError.message : "加载工作区失败";
      setThread(null);
      setLoadFailed(true);
      notifyError("加载工作区失败", message);
    } finally {
      setLoading(false);
    }
  }, [
    notifyError,
    professorId,
    selectedIdentityId,
    selectedLlmProfileId,
    syncComposer,
  ]);

  const runAction = useCallback(
    async (
      action: () => Promise<WorkspaceThreadDTO>,
      fallbackTitle: string,
      fallbackMessage: string,
      onSuccess?: (data: WorkspaceThreadDTO) => void,
    ) => {
      setActing(true);
      try {
        const data = await action();
        setThread(data);
        syncComposer(data);
        onSuccess?.(data);
      } catch (actionError) {
        const message =
          actionError instanceof Error ? actionError.message : fallbackMessage;
        notifyError(fallbackTitle, message);
      } finally {
        setActing(false);
      }
    },
    [notifyError, syncComposer],
  );
```

Update the invalid schedule branch:

```tsx
    if (Number.isNaN(scheduleDate.getTime())) {
      notifyFormErrors("请检查表单", ["请先选一个有效的发送时间"]);
      return;
    }
```

Update the action calls:

```tsx
    void runAction(
      () => approveAndSend(currentTaskId, {...}),
      "发送失败",
      "发送失败",
      () => setComposerExpanded(false),
    );
```

```tsx
    void runAction(
      () => approveAndSchedule(currentTaskId, {...}),
      "定时发送失败",
      "定时发送失败",
      () => setComposerExpanded(false),
    );
```

```tsx
    void runAction(() => cancelScheduledTask(currentTaskId), "取消定时失败", "取消定时失败");
    void runAction(() => updateTaskPrimaryMaterial(currentTaskId, materialId), "切换默认材料失败", "切换默认材料失败");
    void runAction(() => calculateMatch(currentTaskId), "计算匹配失败", "计算匹配失败");
    void runAction(() => generateDraft(currentTaskId), "生成草稿失败", "生成草稿失败", () => setComposerExpanded(true));
    void runAction(() => updateTaskOutreachConfig(currentTaskId, { outreach_generation_mode: nextMode }), "切换模式失败", "切换模式失败");
```

Keep a neutral empty state for load failure instead of an inline error banner:

```tsx
  if (!thread) {
    return (
      <main className="mx-auto max-w-7xl px-6 py-8">
        <div className="rounded-[32px] border border-dashed border-stone-300 bg-white px-6 py-16 text-center text-sm text-stone-500 shadow-sm">
          {loadFailed ? "工作区数据暂时不可用，请返回上一页后重试。" : "未找到工作区数据"}
        </div>
      </main>
    );
  }
```

Update `frontend/src/components/organisms/WorkspaceComposerDock.tsx`:

```tsx
type WorkspaceComposerDockProps = {
  thread: WorkspaceThreadDTO;
  currentTask: WorkspaceTaskSummaryDTO;
  currentTaskMode: OutreachGenerationMode;
  subject: string;
  content: string;
  hasRichHtml: boolean;
  selectedMaterialIds: number[];
  scheduledAt: string;
  acting: boolean;
  primaryMaterialOptions: IdentityMaterialDTO[];
  canChangePrimaryMaterial: boolean;
  canChangeMode: boolean;
  canCalculateMatch: boolean;
  canGenerateDraft: boolean;
  composerExpanded: boolean;
  ...
};
```

Remove both inline error renders:

```tsx
            {error ? <p className="mt-3 text-sm text-red-600">{error}</p> : null}
```

and

```tsx
          {error ? <p className="mt-3 text-sm text-red-600">{error}</p> : null}
```

- [ ] **Step 4: Run the integration test and verify it passes**

Run:

```bash
cd frontend
npx vitest run test/SelectionContextNotifications.test.tsx
```

Expected:

```text
✓ test/SelectionContextNotifications.test.tsx
```

- [ ] **Step 5: Commit the routed-page migration**

```bash
git add frontend/test/SelectionContextNotifications.test.tsx frontend/src/context/SelectionContext.tsx frontend/src/pages/HomePage.tsx frontend/src/pages/CreateTaskPage.tsx frontend/src/pages/TasksPage.tsx frontend/src/pages/WorkspacePage.tsx frontend/src/components/organisms/WorkspaceComposerDock.tsx
git commit -m "feat(frontend): route core page feedback through notifications"
```

## Task 4: Migrate `ProfessorsPage` and lock one page-level validation flow

**Files:**
- Create: `frontend/test/ProfessorsPageNotifications.test.tsx`
- Modify: `frontend/src/pages/ProfessorsPage.tsx`

- [ ] **Step 1: Write the failing page regression test**

Create `frontend/test/ProfessorsPageNotifications.test.tsx`:

```tsx
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { NotificationProvider } from "@/context/NotificationContext";
import { ProfessorsPage } from "@/pages/ProfessorsPage";

const listProfessorsForManagement = vi.hoisted(() => vi.fn());

vi.mock("@/lib/api/professorsApi", () => ({
  listProfessorsForManagement,
  archiveProfessor: vi.fn(),
  bulkArchiveProfessors: vi.fn(),
  createProfessor: vi.fn(),
  getProfessorTemplateDownloadUrl: vi.fn(() => "/templates/professors.xlsx"),
  importProfessorsFromFile: vi.fn(),
  importSampleProfessors: vi.fn(),
  restoreProfessor: vi.fn(),
  triggerCrawler: vi.fn(),
  updateProfessor: vi.fn(),
}));

describe("ProfessorsPage notifications", () => {
  beforeEach(() => {
    listProfessorsForManagement.mockReset();
    listProfessorsForManagement.mockResolvedValue([]);
  });

  it("shows the empty-import validation as a notification card", async () => {
    render(
      <NotificationProvider>
        <ProfessorsPage />
      </NotificationProvider>,
    );

    await waitFor(() => {
      expect(listProfessorsForManagement).toHaveBeenCalled();
    });

    fireEvent.click(screen.getByRole("button", { name: "导入文件" }));
    fireEvent.click(screen.getByRole("button", { name: "开始导入" }));

    const message = screen.getByText("请先选择要导入的 csv 或 xlsx 文件");
    expect(message.closest('[data-testid="notification-card"]')).not.toBeNull();
  });
});
```

- [ ] **Step 2: Run the page test and verify it fails while `ProfessorsPage` still writes inline state**

Run:

```bash
cd frontend
npx vitest run test/ProfessorsPageNotifications.test.tsx
```

Expected:

```text
FAIL  test/ProfessorsPageNotifications.test.tsx
expected null not to be null
```

- [ ] **Step 3: Replace `ProfessorsPage` inline success/error banners with notification calls**

Update `frontend/src/pages/ProfessorsPage.tsx`:

```tsx
import { useNotification } from "@/context/NotificationContext";

export const ProfessorsPage = () => {
  const { notifyError, notifySuccess, notifyWarning } = useNotification();
  const { confirm, dialog: confirmDialog } = useConfirmDialog();
  ...
```

Remove the local message state:

```tsx
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
```

Change handlers to emit notifications:

```tsx
  const loadProfessors = useCallback(
    async (filter: ArchiveFilter = archiveFilter) => {
      setLoading(true);
      try {
        const data = await listProfessorsForManagement(filter);
        setProfessors(data);
        ...
      } catch (loadError) {
        const message =
          loadError instanceof Error ? loadError.message : "加载导师列表失败";
        notifyError("加载导师列表失败", message);
      } finally {
        setLoading(false);
      }
    },
    [archiveFilter, notifyError],
  );
```

```tsx
      if (editingProfessor) {
        await updateProfessor(editingProfessor.id, payload);
        notifySuccess("保存成功", `已更新导师“${payload.name}”。`);
      } else {
        await createProfessor(payload);
        notifySuccess("保存成功", `已新增导师“${payload.name}”。`);
      }
```

```tsx
      const result = await archiveProfessor(professor.id);
      notifySuccess("操作成功", result.message);
```

```tsx
      const result = await bulkArchiveProfessors({ ids: [...selectedIds] });
      setSelectedIds(new Set());
      notifySuccess("操作成功", result.message);
```

```tsx
      const result = await restoreProfessor(professor.id);
      notifySuccess("操作成功", result.message);
```

```tsx
    if (!importFile) {
      notifyWarning("请先选择文件", "请先选择要导入的 csv 或 xlsx 文件");
      return;
    }
```

```tsx
      const result = await importProfessorsFromFile(importFile);
      setImportResult(result);
      notifySuccess("导入完成", result.message);
      await loadProfessors();
```

```tsx
      const result = await importSampleProfessors();
      notifySuccess("导入完成", result.message);
```

```tsx
      const result = await triggerCrawler();
      notifySuccess("请求已提交", result.message);
```

Remove the header banner block entirely:

```tsx
          {message ? (
            <p className="text-sm text-emerald-700">{message}</p>
          ) : null}
          {error ? <p className="text-sm text-red-600">{error}</p> : null}
```

Keep the `importResult` detail card in the modal exactly as the structured business-detail panel.

- [ ] **Step 4: Run the page test and verify it passes**

Run:

```bash
cd frontend
npx vitest run test/ProfessorsPageNotifications.test.tsx
```

Expected:

```text
✓ test/ProfessorsPageNotifications.test.tsx
```

- [ ] **Step 5: Commit the professors-page migration**

```bash
git add frontend/test/ProfessorsPageNotifications.test.tsx frontend/src/pages/ProfessorsPage.tsx
git commit -m "feat(frontend): migrate professors page feedback to notifications"
```

## Task 5: Migrate `ProfilePage` and clean the dormant legacy feedback paths

**Files:**
- Modify: `frontend/src/pages/ProfilePage.tsx`
- Modify: `frontend/src/context/SelectionContext.tsx`
- Modify: `frontend/src/components/organisms/MentorDashboardClient.tsx`
- Modify: `frontend/src/components/organisms/TasksDashboardClient.tsx`
- Modify: `frontend/src/components/organisms/CreateTaskClient.tsx`
- Modify: `frontend/src/features/create-task/client/useCreateTaskForm.ts`
- Modify: `frontend/src/components/molecules/TaskNameInput.tsx`
- Modify: `frontend/src/components/molecules/TaskEmailContent.tsx`
- Modify: `frontend/src/components/molecules/TaskScheduleSettings.tsx`
- Modify: `frontend/src/components/atoms/TaskTimePicker.tsx`

- [ ] **Step 1: Replace `ProfilePage` message states and inline feedback banners**

Update `frontend/src/pages/ProfilePage.tsx`:

```tsx
import { useNotification } from "@/context/NotificationContext";
```

Inside `ProfilePage`, remove these local feedback states:

```tsx
  const [identityMessage, setIdentityMessage] = useState<string | null>(null);
  const [llmMessage, setLlmMessage] = useState<string | null>(null);
  const [identityConnectionFeedback, setIdentityConnectionFeedback] =
    useState<InlineFeedback | null>(null);
  const [materialFeedback, setMaterialFeedback] =
    useState<InlineFeedback | null>(null);
```

Replace them with notification hooks:

```tsx
  const { notifyError, notifyFormErrors, notifySuccess, notifyWarning } =
    useNotification();
```

Then convert the current message setters:

```tsx
    if (!editingIdentity) {
      notifyWarning("请先选择身份", "请先选择或新建一个身份。");
      return;
    }
```

```tsx
    if (!identityForm.name.trim() || !identityForm.email_address.trim()) {
      notifyFormErrors("请检查表单", ["请先填写所有带红色星号的身份必填项"]);
      return;
    }

    if (templateValidationMessage) {
      notifyFormErrors("请检查表单", [templateValidationMessage]);
      return;
    }
```

```tsx
      notifySuccess("保存成功", "身份已保存。");
```

```tsx
      notifyError("保存身份失败", message);
```

```tsx
      notifySuccess("连接成功", result.message);
```

```tsx
      notifyError("连接测试失败", message);
```

```tsx
      notifySuccess(
        "材料上传成功",
        `已上传为${getMaterialTypeLabel(uploadedMaterial.material_type)}：${uploadedMaterial.display_name}`,
      );
```

```tsx
      notifyError("材料上传失败", message);
```

```tsx
      notifySuccess("默认材料已更新", `已将“${material.display_name}”设为默认材料。`);
```

```tsx
      notifySuccess(
        "材料操作成功",
        material.is_primary ? "已取消默认材料。" : "材料已删除。",
      );
```

```tsx
      notifySuccess("操作成功", "已切换当前身份。");
      notifySuccess("操作成功", "已设为默认。");
      notifySuccess("操作成功", "身份已删除。");
      notifySuccess("操作成功", "已切换当前模型。");
      notifySuccess("操作成功", "已设为默认。");
      notifySuccess("操作成功", "模型配置已删除。");
```

Remove the inline renderers:

```tsx
            {identityMessage && (
              <p className="mt-4 text-sm text-stone-700">{identityMessage}</p>
            )}
```

```tsx
            {llmMessage && (
              <p className="mt-4 text-sm text-stone-700">{llmMessage}</p>
            )}
```

```tsx
        {selectionError && (
          <p className="mt-4 text-sm text-red-600">{selectionError}</p>
        )}
```

Remove `InlineFeedbackBanner` usage from:

```tsx
const IdentityConnectionCard = ({ ... }) => ...
const MaterialLibraryModal = ({ ... }) => ...
```

Specifically:

```tsx
    {feedback ? (
      <div className="mt-4">
        <InlineFeedbackBanner feedback={feedback} />
      </div>
    ) : null}
```

and

```tsx
          {uploadFeedback ? (
            <div className="mt-3">
              <InlineFeedbackBanner feedback={uploadFeedback} />
            </div>
          ) : null}
```

Keep `LlmModelsFeedbackPanel` and `LlmTestFeedbackPanel` intact because they are business-detail diagnostics, not transient feedback banners.

- [ ] **Step 2: Remove `SelectionContext` compatibility state after `ProfilePage` stops rendering it**

Update `frontend/src/context/SelectionContext.tsx`:

```tsx
interface SelectionContextValue {
  identities: IdentityDTO[];
  llmProfiles: LLMProfileDTO[];
  systemSettings: SystemSettingsDTO | null;
  selectedIdentityId: number | null;
  selectedLlmProfileId: number | null;
  selectedIdentity: IdentityDTO | null;
  selectedLlmProfile: LLMProfileDTO | null;
  loading: boolean;
  updatingMode: boolean;
  setSelectedIdentityId: (value: number | null) => void;
  setSelectedLlmProfileId: (value: number | null) => void;
  refreshSelections: () => Promise<void>;
  setMailDeliveryMode: (value: MailDeliveryMode) => Promise<void>;
}
```

Remove:

```tsx
  const [error, setError] = useState<string | null>(null);
```

and all remaining `setError(null)` / `setError(message)` calls that only exist for page-local rendering.

Return the context value without `error`.

- [ ] **Step 3: Replace leftover `window.alert(...)` calls in dormant dashboard components**

Update `frontend/src/components/organisms/MentorDashboardClient.tsx`:

```tsx
import { useNotification } from "@/context/NotificationContext";

export const MentorDashboardClient: React.FC<MentorDashboardClientProps> = ({ initialMentors }) => {
  const navigate = useNavigate();
  const { notifyWarning } = useNotification();
  ...

  const handleCreateTask = () => {
    if (selectedIds.size === 0) {
      notifyWarning("请先选择导师", "请先在列表中勾选要发送的导师");
      return;
    }
    ...
  };
```

Then replace:

```tsx
        onImportClick={() => window.alert("导入导师（待开发）")}
        onScrapeClick={() => window.alert("智能抓取（待开发）")}
```

with:

```tsx
        onImportClick={() =>
          notifyWarning("功能暂未开放", "导入导师（待开发）")
        }
        onScrapeClick={() =>
          notifyWarning("功能暂未开放", "智能抓取（待开发）")
        }
```

Update `frontend/src/components/organisms/TasksDashboardClient.tsx`:

```tsx
import { useNotification } from "@/context/NotificationContext";

export const TasksDashboardClient: React.FC<TasksDashboardClientProps> = ({ initialTasks }) => {
  const [tasks, setTasks] = useState<BatchTask[]>(initialTasks);
  const { notifyWarning } = useNotification();
  ...

  const handleView = (id: string) =>
    notifyWarning("功能暂未开放", `查看任务 ${id} 详情（待开发）`);

  const handleCreateTask = () =>
    notifyWarning("功能暂未开放", "新建批量任务（待开发）");
```

- [ ] **Step 4: Remove legacy inline field-error props from dormant create-task components**

Update `frontend/src/features/create-task/client/useCreateTaskForm.ts` by removing:

```ts
  const [errors, setErrors] = useState<Record<string, string>>({});
```

and these helpers:

```ts
  const setError = useCallback((field: string, message: string) => {
    setErrors((prev) => ({ ...prev, [field]: message }));
  }, []);

  const clearErrors = useCallback(() => {
    setErrors({});
  }, []);

  const clearError = useCallback((field: string) => {
    setErrors((prev) => {
      const next = { ...prev };
      delete next[field];
      return next;
    });
  }, []);
```

Update `frontend/src/components/organisms/CreateTaskClient.tsx`:

```tsx
import { useNotification } from "@/context/NotificationContext";

export const CreateTaskClient: React.FC<CreateTaskClientProps> = ({ mentors }) => {
  const navigate = useNavigate();
  const { notifyError, notifyFormErrors } = useNotification();
  ...
```

Replace validation handling with:

```tsx
    const formErrors = Object.values(validation.errors);

    if (formErrors.length > 0) {
      notifyFormErrors("请检查表单", formErrors);
      return;
    }

    if (!isScheduleComplete) {
      notifyFormErrors("请检查表单", ["请完善发送策略配置"]);
      return;
    }
```

Replace submit failure with:

```tsx
    } catch {
      notifyError("创建失败", "创建失败，请稍后重试");
    } finally {
      setIsSubmitting(false);
    }
```

Remove the inline submit banner:

```tsx
          {errors.submit && (
            <p className="text-center text-sm text-red-500">{errors.submit}</p>
          )}
```

Update `frontend/src/components/molecules/TaskNameInput.tsx`:

```tsx
interface TaskNameInputProps {
  value: string;
  onChange: (value: string) => void;
}

export const TaskNameInput: React.FC<TaskNameInputProps> = ({ value, onChange }) => {
  return (
    <div className="flex flex-col gap-1.5">
      <label className="text-sm font-semibold text-stone-700">任务名称</label>
      <input
        type="text"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder="例如：清北复交系统架构方向首轮套磁"
        className="h-10 w-full rounded-xl border border-stone-200 bg-white px-4 text-sm text-stone-700 outline-none transition-all focus:border-primary focus:ring-2 focus:ring-primary/20"
      />
    </div>
  );
};
```

Update `frontend/src/components/molecules/TaskEmailContent.tsx` by removing `onClearError`, `errors`, and both inline red `<span>` blocks.

Update `frontend/src/components/molecules/TaskScheduleSettings.tsx` by removing the `errors` prop and all `error={...}` / inline error spans.

Update `frontend/src/components/atoms/TaskTimePicker.tsx`:

```tsx
interface TaskTimePickerProps {
  value: string;
  onChange: (time: string) => void;
}
```

Remove:

```tsx
      {error && <span className="text-xs text-red-500">{error}</span>}
```

- [ ] **Step 5: Run the focused regression tests after the large migration**

Run:

```bash
cd frontend
npx vitest run test/notifications.test.ts test/NotificationViewport.test.tsx test/SelectionContextNotifications.test.tsx test/ProfessorsPageNotifications.test.tsx
```

Expected:

```text
✓ test/notifications.test.ts
✓ test/NotificationViewport.test.tsx
✓ test/SelectionContextNotifications.test.tsx
✓ test/ProfessorsPageNotifications.test.tsx
```

- [ ] **Step 6: Commit the profile and legacy cleanup**

```bash
git add frontend/src/pages/ProfilePage.tsx frontend/src/context/SelectionContext.tsx frontend/src/components/organisms/MentorDashboardClient.tsx frontend/src/components/organisms/TasksDashboardClient.tsx frontend/src/components/organisms/CreateTaskClient.tsx frontend/src/features/create-task/client/useCreateTaskForm.ts frontend/src/components/molecules/TaskNameInput.tsx frontend/src/components/molecules/TaskEmailContent.tsx frontend/src/components/molecules/TaskScheduleSettings.tsx frontend/src/components/atoms/TaskTimePicker.tsx
git commit -m "feat(frontend): migrate remaining feedback paths to notifications"
```

## Task 6: Run the full verification pass and audit for leftover feedback code

**Files:**
- Verify: `frontend`

- [ ] **Step 1: Run the full frontend test suite**

Run:

```bash
cd frontend
npm run test
```

Expected:

```text
Test Files  6 passed
```

- [ ] **Step 2: Run lint and build**

Run:

```bash
cd frontend
npm run lint
npm run build
```

Expected:

```text
✔ No ESLint errors
✓ built in
```

- [ ] **Step 3: Audit for the leftover feedback patterns that this feature is supposed to eliminate**

Run:

```bash
rg -n "window\\.alert" frontend/src
rg -n "InlineFeedbackBanner|identityMessage|llmMessage|materialFeedback|identityConnectionFeedback|selectionError" frontend/src/pages/ProfilePage.tsx frontend/src/context/SelectionContext.tsx
rg -n "const \\[error, setError\\]|const \\[message, setMessage\\]" frontend/src/pages/HomePage.tsx frontend/src/pages/CreateTaskPage.tsx frontend/src/pages/TasksPage.tsx frontend/src/pages/WorkspacePage.tsx frontend/src/pages/ProfessorsPage.tsx
```

Expected:

```text
no matches
```

- [ ] **Step 4: Manually verify the user-facing behaviors from the spec**

Check these in the browser:

```text
1. 右下角最多只显示 4 条消息，最新的一条贴在最下面。
2. 连续触发第 5 条消息时，最老的一条被移除。
3. 成功消息停留约 2-3 秒，警告消息约 3-6 秒，错误消息约 5-8 秒。
4. 同级别长消息比短消息停留更久。
5. 把鼠标移到消息上后，消息不再自动消失。
6. 在消息正文里拖选文本后，消息不再自动消失。
7. 点击消息里的关闭按钮可以手动关闭消息。
8. 打开资料页模板弹窗或材料库弹窗时，消息仍在右下角正常显示。
9. 首页缺少默认材料时，点击“只算匹配”会出全局警告，而不是页面内红字。
10. 创建任务页一次性缺多个字段时，只出现 1 条“请检查表单”消息，正文列出全部错误。
11. 工作区中无效定时发送时间会走全局错误，而不是底部 dock 内联红字。
12. 导师管理页的保存、导入、归档、恢复、样例导入、智能抓取都走全局成功/失败消息。
13. 资料页的身份/模型保存、连接测试、材料上传/设默认/删除都走全局消息；模型诊断详情面板仍保留。
14. 顶栏切换真实发送模式失败时，会出现全局错误消息。
```

- [ ] **Step 5: Commit the verified result**

```bash
git status --short
git commit --allow-empty -m "chore(frontend): verify global notification rollout"
```
