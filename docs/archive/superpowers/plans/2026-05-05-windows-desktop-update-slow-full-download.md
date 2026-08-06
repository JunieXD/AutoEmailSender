# Windows 桌面更新慢速切换全量下载实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 为 Windows 桌面更新加入常驻下载进度、慢速切换全量下载、主动全量下载、待安装复用和旧缓存清理。

**架构：** 主进程负责更新状态机、下载取消、全量下载模式、缓存清理和安装触发；预加载层只暴露稳定 IPC；前端更新按钮旁展示非阻塞常驻状态区。下载确认和切换全量下载时提示预计流量，下载完成后进入待安装状态，不自动安装。

**技术栈：** Electron、electron-updater、builder-util-runtime `CancellationToken`、React、Vitest、Testing Library、TypeScript。

---

## 文件结构

- 修改：`desktop/src/types.ts`
  - 职责：扩展 `UpdateStatus`，让主进程和预加载层共享下载进度、流量提示、待安装和慢速提示状态。
- 修改：`desktop/src/updates.ts`
  - 职责：实现更新会话状态机、慢速检测、差分取消、全量下载模式、缓存清理、待安装复用。
- 修改：`desktop/src/preload.ts`
  - 职责：暴露新的 IPC 方法：下载模式选择、慢速切换全量、安装已下载更新。
- 修改：`frontend/src/types/desktop.d.ts`
  - 职责：同步桌面更新状态和桌面 API 类型。
- 修改：`frontend/src/lib/desktopApi.ts`
  - 职责：封装新的桌面更新 IPC。
- 修改：`frontend/src/components/molecules/DesktopUpdateButton.tsx`
  - 职责：把按钮扩展成更新按钮 + 常驻状态区，展示进度、流量和待安装动作。
- 修改：`desktop/test/updates.test.ts`
  - 职责：覆盖进度格式化、慢速判断、缓存目录规则和取消令牌使用约束。
- 修改：`frontend/src/components/molecules/DesktopUpdateButton.test.tsx`
  - 职责：覆盖主动全量下载、慢速切换、常驻进度和待安装复用。
- 修改：`frontend/src/lib/desktopApi.test.ts`
  - 职责：覆盖新增桌面 API 封装。

## 任务 1：扩展更新类型和纯函数测试

**文件：**
- 修改：`desktop/src/types.ts`
- 修改：`desktop/src/updates.ts`
- 测试：`desktop/test/updates.test.ts`

- [ ] **步骤 1：编写失败的桌面端纯函数测试**

在 `desktop/test/updates.test.ts` 中扩展导入：

```typescript
import {
  estimateRemainingSeconds,
  formatByteSize,
  formatDownloadProgress,
  shouldOfferFullDownload,
} from "../src/updates.js";
```

追加测试：

```typescript
it("formats byte sizes for update progress", () => {
  expect(formatByteSize(0)).toBe("0 B");
  expect(formatByteSize(1536)).toBe("1.5 KB");
  expect(formatByteSize(5 * 1024 * 1024)).toBe("5.0 MB");
});

it("estimates remaining seconds from remaining bytes and speed", () => {
  expect(estimateRemainingSeconds(30 * 1024 * 1024, 512 * 1024)).toBe(60);
  expect(estimateRemainingSeconds(1024, 0)).toBe(null);
});

it("offers full download only after the slow threshold is exceeded", () => {
  expect(
    shouldOfferFullDownload({
      elapsedSeconds: 9,
      remainingSeconds: 600,
      alreadyOffered: false,
    }),
  ).toBe(false);
  expect(
    shouldOfferFullDownload({
      elapsedSeconds: 40,
      remainingSeconds: 181,
      alreadyOffered: false,
    }),
  ).toBe(true);
  expect(
    shouldOfferFullDownload({
      elapsedSeconds: 40,
      remainingSeconds: 181,
      alreadyOffered: true,
    }),
  ).toBe(false);
});
```

- [ ] **步骤 2：运行测试验证失败**

运行：

```bash
rtk pwsh -NoLogo -Command "cd desktop; npm test -- updates.test.ts"
```

预期：FAIL，提示 `formatByteSize`、`estimateRemainingSeconds` 或 `shouldOfferFullDownload` 未导出。

- [ ] **步骤 3：扩展 `desktop/src/types.ts`**

将 `UpdateStatus` 改为包含进度详情：

```typescript
export type UpdateDownloadMode = "differential" | "full";

export type UpdateDownloadProgress = {
  percent: number;
  transferredBytes: number;
  totalBytes: number;
  remainingBytes: number;
  bytesPerSecond: number;
  remainingSeconds: number | null;
  mode: UpdateDownloadMode;
};

export type UpdateStatus =
  | { state: "idle"; version: string }
  | { state: "checking"; version: string }
  | { state: "available"; version: string; nextVersion: string; fullDownloadBytes?: number }
  | { state: "not_available"; version: string }
  | ({ state: "downloading"; version: string; nextVersion: string } & UpdateDownloadProgress)
  | ({ state: "slow_download_offered"; version: string; nextVersion: string; fullDownloadBytes?: number } & UpdateDownloadProgress)
  | { state: "downloaded_pending_install"; version: string; nextVersion: string; fullDownloadBytes?: number }
  | { state: "installing"; version: string; nextVersion: string }
  | { state: "error"; version: string; message: string };
```

- [ ] **步骤 4：实现纯函数**

在 `desktop/src/updates.ts` 中导出：

```typescript
const BYTES_PER_KIB = 1024;
const SLOW_CHECK_START_SECONDS = 10;
const SLOW_REMAINING_SECONDS = 180;

export function formatByteSize(bytes: number): string {
  if (bytes < BYTES_PER_KIB) {
    return `${bytes} B`;
  }
  const kib = bytes / BYTES_PER_KIB;
  if (kib < BYTES_PER_KIB) {
    return `${kib.toFixed(1)} KB`;
  }
  return `${(kib / BYTES_PER_KIB).toFixed(1)} MB`;
}

export function estimateRemainingSeconds(remainingBytes: number, bytesPerSecond: number): number | null {
  if (bytesPerSecond <= 0) {
    return null;
  }
  return Math.ceil(remainingBytes / bytesPerSecond);
}

export function shouldOfferFullDownload(input: {
  elapsedSeconds: number;
  remainingSeconds: number | null;
  alreadyOffered: boolean;
}): boolean {
  return (
    !input.alreadyOffered &&
    input.elapsedSeconds >= SLOW_CHECK_START_SECONDS &&
    input.remainingSeconds !== null &&
    input.remainingSeconds > SLOW_REMAINING_SECONDS
  );
}
```

- [ ] **步骤 5：运行测试验证通过**

运行：

```bash
rtk pwsh -NoLogo -Command "cd desktop; npm test -- updates.test.ts"
```

预期：PASS。

- [ ] **步骤 6：Commit**

```bash
rtk pwsh -NoLogo -Command "git add desktop/src/types.ts desktop/src/updates.ts desktop/test/updates.test.ts; git commit -m 'feat(desktop): add update progress helpers'"
```

## 任务 2：实现主进程更新会话

**文件：**
- 修改：`desktop/src/updates.ts`
- 测试：`desktop/test/updates.test.ts`

- [ ] **步骤 1：编写失败的源码约束测试**

在 `desktop/test/updates.test.ts` 追加：

```typescript
it("uses cancellation tokens for switchable update downloads", () => {
  const source = readFileSync(path.resolve("src", "updates.ts"), "utf8");

  expect(source).toContain("CancellationToken");
  expect(source).toContain("currentDownloadToken");
  expect(source).toContain("currentDownloadToken.cancel()");
});

it("supports full download mode through electron-updater", () => {
  const source = readFileSync(path.resolve("src", "updates.ts"), "utf8");

  expect(source).toContain("disableDifferentialDownload");
  expect(source).toContain("startUpdateDownload");
  expect(source).toContain('mode: "full"');
});
```

- [ ] **步骤 2：运行测试验证失败**

运行：

```bash
rtk pwsh -NoLogo -Command "cd desktop; npm test -- updates.test.ts"
```

预期：FAIL，提示源码缺少取消令牌和全量下载逻辑。

- [ ] **步骤 3：引入取消令牌和会话状态**

在 `desktop/src/updates.ts` 顶部加入：

```typescript
import { CancellationToken } from "builder-util-runtime";
import fs from "node:fs/promises";
import path from "node:path";
import { app, BrowserWindow, ipcMain } from "electron";
```

保留已有 `app`、`BrowserWindow`、`ipcMain` 导入时不要重复导入。新增模块级状态：

```typescript
let currentDownloadToken: CancellationToken | null = null;
let activeDownloadMode: UpdateDownloadMode = "differential";
let slowDownloadAlreadyOffered = false;
let activeNextVersion: string | null = null;
let activeFullDownloadBytes: number | undefined;
```

- [ ] **步骤 4：实现进度归一化**

在 `desktop/src/updates.ts` 中添加：

```typescript
function buildProgressStatus(progress: {
  percent: number;
  transferred: number;
  total: number;
  bytesPerSecond: number;
}): UpdateDownloadProgress {
  const remainingBytes = Math.max(progress.total - progress.transferred, 0);
  return {
    percent: formatDownloadProgress(progress.percent),
    transferredBytes: progress.transferred,
    totalBytes: progress.total,
    remainingBytes,
    bytesPerSecond: progress.bytesPerSecond,
    remainingSeconds: estimateRemainingSeconds(remainingBytes, progress.bytesPerSecond),
    mode: activeDownloadMode,
  };
}
```

- [ ] **步骤 5：实现 `startUpdateDownload`**

替换原 `update:download` 的直接 `autoUpdater.downloadUpdate()` 调用，新增函数：

```typescript
async function startUpdateDownload(
  getWindow: () => BrowserWindow | null,
  mode: UpdateDownloadMode,
): Promise<UpdateStatus> {
  const autoUpdater = getAutoUpdater();
  currentDownloadToken?.cancel();
  currentDownloadToken = new CancellationToken();
  activeDownloadMode = mode;
  slowDownloadAlreadyOffered = false;
  autoUpdater.disableDifferentialDownload = mode === "full";
  await autoUpdater.downloadUpdate(currentDownloadToken);
  return currentStatus;
}
```

- [ ] **步骤 6：更新进度事件发布慢速提示**

在 `download-progress` 事件里使用 `buildProgressStatus`，并在 `shouldOfferFullDownload` 为 true 时发布 `slow_download_offered`：

```typescript
const downloadStartedAt = Date.now();
autoUpdater.on("download-progress", (progress) => {
  const normalized = buildProgressStatus(progress);
  const base = {
    version: app.getVersion(),
    nextVersion: activeNextVersion ?? app.getVersion(),
    ...normalized,
  };

  if (
    shouldOfferFullDownload({
      elapsedSeconds: Math.floor((Date.now() - downloadStartedAt) / 1000),
      remainingSeconds: normalized.remainingSeconds,
      alreadyOffered: slowDownloadAlreadyOffered,
    })
  ) {
    slowDownloadAlreadyOffered = true;
    publish(getWindow, {
      state: "slow_download_offered",
      fullDownloadBytes: activeFullDownloadBytes,
      ...base,
    });
    return;
  }

  publish(getWindow, { state: "downloading", ...base });
});
```

如果实现时需要让 `downloadStartedAt` 随每次下载重置，把它提升为模块级 `downloadStartedAtMs`，并在 `startUpdateDownload` 中赋值。

- [ ] **步骤 7：新增 IPC**

在 `registerUpdateIpc` 中新增：

```typescript
ipcMain.handle("update:download", async (_event, options?: { mode?: UpdateDownloadMode }) => {
  if (!app.isPackaged) {
    return currentStatus;
  }
  return startUpdateDownload(getWindow, options?.mode ?? "differential");
});

ipcMain.handle("update:switch-to-full-download", async () => {
  if (!app.isPackaged) {
    return currentStatus;
  }
  return startUpdateDownload(getWindow, "full");
});
```

保留原 IPC 名称 `update:download`，避免前端已有调用全部失效。

- [ ] **步骤 8：运行测试验证通过**

运行：

```bash
rtk pwsh -NoLogo -Command "cd desktop; npm test -- updates.test.ts"
```

预期：PASS。

- [ ] **步骤 9：Commit**

```bash
rtk pwsh -NoLogo -Command "git add desktop/src/updates.ts desktop/test/updates.test.ts; git commit -m 'feat(desktop): support switchable update downloads'"
```

## 任务 3：待安装状态、缓存清理和安装复用

**文件：**
- 修改：`desktop/src/updates.ts`
- 测试：`desktop/test/updates.test.ts`

- [ ] **步骤 1：编写失败的源码约束测试**

在 `desktop/test/updates.test.ts` 追加：

```typescript
it("tracks pending install versions without auto-installing", () => {
  const source = readFileSync(path.resolve("src", "updates.ts"), "utf8");

  expect(source).toContain("pendingInstallVersion");
  expect(source).toContain("downloaded_pending_install");
  expect(source).not.toContain("await quitAndInstall");
});

it("cleans stale update cache when a different version is available", () => {
  const source = readFileSync(path.resolve("src", "updates.ts"), "utf8");

  expect(source).toContain("clearStaleUpdateCache");
  expect(source).toContain("app.getPath(\"userData\")");
  expect(source).toContain("updates");
});
```

- [ ] **步骤 2：运行测试验证失败**

运行：

```bash
rtk pwsh -NoLogo -Command "cd desktop; npm test -- updates.test.ts"
```

预期：FAIL，提示缺少待安装和清理逻辑。

- [ ] **步骤 3：实现待安装版本状态**

在 `desktop/src/updates.ts` 新增：

```typescript
let pendingInstallVersion: string | null = null;

function getUpdateCacheRoot(): string {
  return path.join(app.getPath("userData"), "updates");
}

async function clearStaleUpdateCache(nextVersion: string): Promise<void> {
  const root = getUpdateCacheRoot();
  await fs.mkdir(root, { recursive: true });
  const entries = await fs.readdir(root, { withFileTypes: true });
  await Promise.all(
    entries
      .filter((entry) => entry.isDirectory() && entry.name !== nextVersion)
      .map((entry) => fs.rm(path.join(root, entry.name), { recursive: true, force: true })),
  );
}
```

- [ ] **步骤 4：检查到新版本时清旧缓存**

在 `update-available` 事件中：

```typescript
activeNextVersion = info.version;
activeFullDownloadBytes = info.files?.[0]?.size;
void clearStaleUpdateCache(info.version);
```

发布 `available` 状态时带上 `fullDownloadBytes`。

- [ ] **步骤 5：下载完成只进入待安装**

修改 `update-downloaded` 事件：

```typescript
autoUpdater.on("update-downloaded", (info) => {
  pendingInstallVersion = info.version;
  publish(getWindow, {
    state: "downloaded_pending_install",
    version: app.getVersion(),
    nextVersion: info.version,
    fullDownloadBytes: activeFullDownloadBytes,
  });
});
```

不要在这里调用 `quitAndInstall`。

- [ ] **步骤 6：复用待安装版本**

在 `update:check` 处理器最前面加入：

```typescript
if (pendingInstallVersion !== null && pendingInstallVersion !== app.getVersion()) {
  return {
    state: "downloaded_pending_install",
    version: app.getVersion(),
    nextVersion: pendingInstallVersion,
    fullDownloadBytes: activeFullDownloadBytes,
  } satisfies UpdateStatus;
}
```

- [ ] **步骤 7：安装 IPC 发布安装态**

修改 `update:quit-and-install`：

```typescript
ipcMain.handle("update:quit-and-install", () => {
  if (pendingInstallVersion !== null) {
    publish(getWindow, {
      state: "installing",
      version: app.getVersion(),
      nextVersion: pendingInstallVersion,
    });
  }
  autoUpdater.quitAndInstall(false, true);
});
```

- [ ] **步骤 8：运行测试验证通过**

运行：

```bash
rtk pwsh -NoLogo -Command "cd desktop; npm test -- updates.test.ts"
```

预期：PASS。

- [ ] **步骤 9：Commit**

```bash
rtk pwsh -NoLogo -Command "git add desktop/src/updates.ts desktop/test/updates.test.ts; git commit -m 'feat(desktop): reuse downloaded update before install'"
```

## 任务 4：同步预加载和前端 API 类型

**文件：**
- 修改：`desktop/src/preload.ts`
- 修改：`frontend/src/types/desktop.d.ts`
- 修改：`frontend/src/lib/desktopApi.ts`
- 测试：`frontend/src/lib/desktopApi.test.ts`

- [ ] **步骤 1：编写失败的前端 API 测试**

在 `frontend/src/lib/desktopApi.test.ts` 中扩展导入：

```typescript
import {
  downloadDesktopUpdate,
  getDesktopAppVersion,
  installDownloadedDesktopUpdate,
  isDesktopApp,
  switchDesktopUpdateToFullDownload,
} from "@/lib/desktopApi";
```

追加测试：

```typescript
it("passes update download mode to the desktop bridge", async () => {
  const downloadUpdate = vi.fn(async () => ({
    state: "downloading" as const,
    version: "0.1.0",
    nextVersion: "0.1.1",
    percent: 0,
    transferredBytes: 0,
    totalBytes: 100,
    remainingBytes: 100,
    bytesPerSecond: 0,
    remainingSeconds: null,
    mode: "full" as const,
  }));
  window.autoEmailSender = buildDesktopApi({ downloadUpdate });

  await downloadDesktopUpdate("full");

  expect(downloadUpdate).toHaveBeenCalledWith({ mode: "full" });
});

it("switches to full download through the desktop bridge", async () => {
  const switchToFullDownload = vi.fn(async () => ({
    state: "downloading" as const,
    version: "0.1.0",
    nextVersion: "0.1.1",
    percent: 0,
    transferredBytes: 0,
    totalBytes: 100,
    remainingBytes: 100,
    bytesPerSecond: 0,
    remainingSeconds: null,
    mode: "full" as const,
  }));
  window.autoEmailSender = buildDesktopApi({ switchToFullDownload });

  await switchDesktopUpdateToFullDownload();

  expect(switchToFullDownload).toHaveBeenCalled();
});
```

在测试文件底部新增 `buildDesktopApi` 辅助函数，包含所有必填字段。

- [ ] **步骤 2：运行测试验证失败**

运行：

```bash
rtk pwsh -NoLogo -Command "cd frontend; npm test -- desktopApi.test.ts"
```

预期：FAIL，提示新增 API 未导出或类型不匹配。

- [ ] **步骤 3：更新预加载桥接**

在 `desktop/src/preload.ts` 中修改：

```typescript
downloadUpdate: (options?: { mode?: "differential" | "full" }) =>
  ipcRenderer.invoke("update:download", options) as Promise<UpdateStatus>,
switchToFullDownload: () =>
  ipcRenderer.invoke("update:switch-to-full-download") as Promise<UpdateStatus>,
quitAndInstall: () => ipcRenderer.invoke("update:quit-and-install") as Promise<void>,
```

- [ ] **步骤 4：同步前端全局类型**

在 `frontend/src/types/desktop.d.ts` 中新增：

```typescript
export type DesktopUpdateDownloadMode = "differential" | "full";
export type DesktopUpdateDownloadProgress = {
  percent: number;
  transferredBytes: number;
  totalBytes: number;
  remainingBytes: number;
  bytesPerSecond: number;
  remainingSeconds: number | null;
  mode: DesktopUpdateDownloadMode;
};
```

并把 `DesktopUpdateStatus` 与 `desktop/src/types.ts` 保持一致。`Window.autoEmailSender` 增加：

```typescript
downloadUpdate: (options?: { mode?: DesktopUpdateDownloadMode }) => Promise<DesktopUpdateStatus>;
switchToFullDownload: () => Promise<DesktopUpdateStatus>;
```

- [ ] **步骤 5：更新 `frontend/src/lib/desktopApi.ts`**

修改下载函数：

```typescript
import type { DesktopUpdateDownloadMode, DesktopUpdateStatus } from "@/types/desktop";

export async function downloadDesktopUpdate(mode: DesktopUpdateDownloadMode = "differential") {
  const api = getDesktopApi();
  return api.downloadUpdate({ mode });
}

export async function switchDesktopUpdateToFullDownload() {
  const api = getDesktopApi();
  return api.switchToFullDownload();
}

export async function installDownloadedDesktopUpdate(): Promise<void> {
  const api = getDesktopApi();
  await api.quitAndInstall();
}
```

保留 `quitAndInstallDesktopUpdate` 作为兼容导出时，让它调用 `installDownloadedDesktopUpdate()`。

- [ ] **步骤 6：运行测试验证通过**

运行：

```bash
rtk pwsh -NoLogo -Command "cd frontend; npm test -- desktopApi.test.ts"
```

预期：PASS。

- [ ] **步骤 7：Commit**

```bash
rtk pwsh -NoLogo -Command "git add desktop/src/preload.ts frontend/src/types/desktop.d.ts frontend/src/lib/desktopApi.ts frontend/src/lib/desktopApi.test.ts; git commit -m 'feat(frontend): expose desktop update download modes'"
```

## 任务 5：实现非阻塞常驻进度区

**文件：**
- 修改：`frontend/src/components/molecules/DesktopUpdateButton.tsx`
- 测试：`frontend/src/components/molecules/DesktopUpdateButton.test.tsx`

- [ ] **步骤 1：编写失败的 UI 测试**

在 `DesktopUpdateButton.test.tsx` 追加：

```typescript
it("shows persistent update progress without blocking the page", async () => {
  const listeners: Array<(status: DesktopUpdateStatus) => void> = [];
  window.autoEmailSender = buildDesktopApi({
    onUpdateStatus: (callback) => {
      listeners.push(callback);
      return () => undefined;
    },
  });

  render(<DesktopUpdateButton />);
  listeners[0]?.({
    state: "downloading",
    version: "0.1.0",
    nextVersion: "0.1.1",
    percent: 50,
    transferredBytes: 10 * 1024 * 1024,
    totalBytes: 20 * 1024 * 1024,
    remainingBytes: 10 * 1024 * 1024,
    bytesPerSecond: 512 * 1024,
    remainingSeconds: 20,
    mode: "differential",
  });

  expect(await screen.findByText(/10.0 MB 已下载/)).toBeInTheDocument();
  expect(screen.getByText(/10.0 MB 剩余/)).toBeInTheDocument();
  expect(screen.getByText(/512.0 KB\/s/)).toBeInTheDocument();
  expect(screen.getByText(/预计 20 秒/)).toBeInTheDocument();
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
});

it("allows users to start a full download proactively", async () => {
  const downloadUpdate = vi.fn(async () => ({
    state: "downloaded_pending_install" as const,
    version: "0.1.0",
    nextVersion: "0.1.1",
    fullDownloadBytes: 200,
  }));
  confirm.mockResolvedValue(true);
  window.autoEmailSender = buildDesktopApi({
    checkForUpdate: async () => ({
      state: "available",
      version: "0.1.0",
      nextVersion: "0.1.1",
      fullDownloadBytes: 200,
    }),
    downloadUpdate,
  });

  render(<DesktopUpdateButton />);
  fireEvent.click(await screen.findByRole("button", { name: /检查更新/ }));
  fireEvent.click(await screen.findByRole("button", { name: /全量下载/ }));

  await waitFor(() => {
    expect(downloadUpdate).toHaveBeenCalledWith({ mode: "full" });
  });
});
```

- [ ] **步骤 2：运行测试验证失败**

运行：

```bash
rtk pwsh -NoLogo -Command "cd frontend; npm test -- DesktopUpdateButton.test.tsx"
```

预期：FAIL，提示找不到进度文本或全量下载按钮。

- [ ] **步骤 3：添加格式化辅助函数**

在 `DesktopUpdateButton.tsx` 中新增：

```typescript
function formatBytes(bytes: number): string {
  if (bytes < 1024) {
    return `${bytes} B`;
  }
  const kb = bytes / 1024;
  if (kb < 1024) {
    return `${kb.toFixed(1)} KB`;
  }
  return `${(kb / 1024).toFixed(1)} MB`;
}

function formatEta(seconds: number | null): string {
  if (seconds === null) {
    return "预计时间未知";
  }
  if (seconds < 60) {
    return `预计 ${seconds} 秒`;
  }
  return `预计 ${Math.ceil(seconds / 60)} 分钟`;
}
```

- [ ] **步骤 4：把确认弹窗改成非阻塞选择区**

保留已有确认对话能力，但不要用于下载进度。新版本可用时在按钮旁状态区展示：

```tsx
{status?.state === "available" ? (
  <div className="flex items-center gap-2 text-xs text-stone-600">
    <span>预计最多 {formatBytes(status.fullDownloadBytes ?? 0)}</span>
    <button type="button" onClick={() => void startDownload("differential")}>增量下载</button>
    <button type="button" onClick={() => void startDownload("full")}>全量下载</button>
  </div>
) : null}
```

样式沿用现有 `stone`、`primary` 色系，不新增卡片嵌套。

- [ ] **步骤 5：渲染下载进度区**

在按钮旁追加：

```tsx
{status?.state === "downloading" || status?.state === "slow_download_offered" ? (
  <div className="min-w-[18rem] text-xs text-stone-600" aria-label="更新下载进度">
    <div className="h-1.5 overflow-hidden rounded-full bg-stone-100">
      <div className="h-full bg-primary" style={{ width: `${status.percent}%` }} />
    </div>
    <div className="mt-1 flex flex-wrap gap-x-3 gap-y-1">
      <span>{formatBytes(status.transferredBytes)} 已下载</span>
      <span>{formatBytes(status.remainingBytes)} 剩余</span>
      <span>{formatBytes(status.bytesPerSecond)}/s</span>
      <span>{formatEta(status.remainingSeconds)}</span>
    </div>
  </div>
) : null}
```

- [ ] **步骤 6：支持慢速切换全量**

当状态为 `slow_download_offered` 时显示：

```tsx
<button type="button" onClick={() => void switchToFullDownload()}>
  切换全量下载
</button>
```

按钮旁提示：

```tsx
<span>
  全量约 {formatBytes(status.fullDownloadBytes ?? status.totalBytes)}，已消耗 {formatBytes(status.transferredBytes)}
</span>
```

- [ ] **步骤 7：支持待安装动作**

当状态为 `downloaded_pending_install` 时显示：

```tsx
<button type="button" onClick={() => void installDownloadedDesktopUpdate()}>
  立即重启安装
</button>
<span>可稍后安装；再次检查更新会直接安装。</span>
```

「稍后安装」不需要额外按钮，用户继续使用即可。

- [ ] **步骤 8：运行 UI 测试验证通过**

运行：

```bash
rtk pwsh -NoLogo -Command "cd frontend; npm test -- DesktopUpdateButton.test.tsx"
```

预期：PASS。

- [ ] **步骤 9：Commit**

```bash
rtk pwsh -NoLogo -Command "git add frontend/src/components/molecules/DesktopUpdateButton.tsx frontend/src/components/molecules/DesktopUpdateButton.test.tsx; git commit -m 'feat(frontend): show persistent desktop update progress'"
```

## 任务 6：集成验证

**文件：**
- 可能修改：前面任务触及的文件

- [ ] **步骤 1：运行桌面端测试**

```bash
rtk pwsh -NoLogo -Command "cd desktop; npm test"
```

预期：PASS。

- [ ] **步骤 2：运行桌面端类型检查**

```bash
rtk pwsh -NoLogo -Command "cd desktop; npm run typecheck"
```

预期：PASS。

- [ ] **步骤 3：运行前端目标测试**

```bash
rtk pwsh -NoLogo -Command "cd frontend; npm test -- DesktopUpdateButton.test.tsx desktopApi.test.ts"
```

预期：PASS。

- [ ] **步骤 4：运行前端 lint**

```bash
rtk pwsh -NoLogo -Command "cd frontend; npm run lint"
```

预期：PASS。

- [ ] **步骤 5：构建前端**

```bash
rtk pwsh -NoLogo -Command "cd frontend; npm run build"
```

预期：PASS。

- [ ] **步骤 6：最终 Commit**

如果前面任务没有逐步提交，执行：

```bash
rtk pwsh -NoLogo -Command "git status --short"
```

确认只有本计划相关文件后：

```bash
rtk pwsh -NoLogo -Command "git add desktop/src/types.ts desktop/src/updates.ts desktop/src/preload.ts desktop/test/updates.test.ts frontend/src/types/desktop.d.ts frontend/src/lib/desktopApi.ts frontend/src/lib/desktopApi.test.ts frontend/src/components/molecules/DesktopUpdateButton.tsx frontend/src/components/molecules/DesktopUpdateButton.test.tsx docs/superpowers/specs/2026-05-05-windows-desktop-update-slow-full-download-design.md docs/superpowers/plans/2026-05-05-windows-desktop-update-slow-full-download.md; git commit -m 'feat(desktop): improve update download experience'"
```

预期：commit 成功。
