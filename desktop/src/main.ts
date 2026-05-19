import { app, BrowserWindow, Menu, Tray, dialog, ipcMain, type MenuItemConstructorOptions } from "electron";
import path from "node:path";
import { pathToFileURL } from "node:url";
import { getFrontendIndexPath, startBackend } from "./backend.js";
import { registerFileSelectionIpc } from "./fileSelection.js";
import { registerMaterialOpenIpc } from "./materialOpenService.js";
import { getStartupAtLoginStatus, setStartupAtLoginEnabled } from "./startup.js";
import { checkForUpdatesOnStartup, registerUpdateIpc } from "./updates.js";
import {
  restoreExistingWindow,
  shouldHideWindowOnClose,
  startWindowCreationOnce,
} from "./windowLifecycle.js";
import { getWindowIconPath } from "./windowIcon.js";
import type { BackendController, BackendExit, BackendStatus, StartupAtLoginStatus } from "./types.js";

let mainWindow: BrowserWindow | null = null;
let tray: Tray | null = null;
let backend: BackendController | null = null;
let restartingBackend = false;
let isQuitting = false;
let backendStopPromise: Promise<void> | null = null;
let currentBackendStatus: BackendStatus = createInitialBackendStatus();
let currentStartupAtLoginStatus: StartupAtLoginStatus | null = null;
const windowCreationState = { pendingCreation: null as Promise<void> | null };

const repoRoot = path.resolve(app.getAppPath(), "..");
const launchedAtStartup = process.argv.includes("--startup");
const hasSingleInstanceLock = app.requestSingleInstanceLock();

if (!hasSingleInstanceLock) {
  app.quit();
}

function showMainWindow(): void {
  if (mainWindow === null) {
    void startWindowCreationOnce(windowCreationState, createWindow);
    return;
  }

  restoreExistingWindow(mainWindow);
}

function quitFromTray(): void {
  isQuitting = true;
  app.quit();
}

function stopBackendAndExit(exitCode: number): void {
  isQuitting = true;
  if (backendStopPromise !== null) {
    return;
  }

  const currentBackend = backend;
  backend = null;
  backendStopPromise = (currentBackend?.stop() ?? Promise.resolve()).finally(() => {
    app.exit(exitCode);
  });
}

function getStartupInput() {
  return {
    platform: process.platform,
    isPackaged: app.isPackaged,
    executablePath: process.execPath,
  };
}

function refreshTrayContextMenu(): void {
  tray?.setContextMenu(buildTrayContextMenu());
}

function buildTrayContextMenu() {
  const startupStatus = currentStartupAtLoginStatus;
  const startupMenuItem: MenuItemConstructorOptions = {
    label: startupStatus === null ? "开机自启动（读取中）" : "开机自启动",
    type: "checkbox",
    checked: Boolean(startupStatus?.supported && startupStatus.enabled),
    enabled: Boolean(startupStatus?.supported),
    click: (menuItem) => {
      void updateStartupAtLoginFromTray(menuItem.checked);
    },
  };

  return Menu.buildFromTemplate([
    { label: "打开窗口", click: showMainWindow },
    { type: "separator" },
    startupMenuItem,
    { type: "separator" },
    { label: "退出", click: quitFromTray },
  ]);
}

async function loadStartupAtLoginForTray(): Promise<void> {
  try {
    currentStartupAtLoginStatus = await getStartupAtLoginStatus(getStartupInput());
  } catch (error) {
    currentStartupAtLoginStatus = {
      supported: false,
      enabled: false,
      message: getErrorMessage(error),
    };
  } finally {
    refreshTrayContextMenu();
  }
}

async function updateStartupAtLoginFromTray(enabled: boolean): Promise<void> {
  try {
    currentStartupAtLoginStatus = await setStartupAtLoginEnabled(getStartupInput(), enabled);
  } catch (error) {
    dialog.showErrorBox("开机自启动设置失败", getErrorMessage(error));
    await loadStartupAtLoginForTray();
    return;
  }

  refreshTrayContextMenu();
}

function ensureTray(): void {
  if (tray !== null) {
    return;
  }

  tray = new Tray(
    getWindowIconPath({
      isPackaged: app.isPackaged,
      resourcesPath: process.resourcesPath,
      repoRoot,
    }),
  );
  tray.setToolTip("Auto Email Sender");
  refreshTrayContextMenu();
  void loadStartupAtLoginForTray();
  tray.on("click", showMainWindow);
}

async function createWindow(): Promise<void> {
  backend = await startDesktopBackend();
  ensureTray();
  Menu.setApplicationMenu(null);

  mainWindow = new BrowserWindow({
    width: 1440,
    height: 900,
    minWidth: 1024,
    minHeight: 700,
    show: !launchedAtStartup,
    autoHideMenuBar: true,
    icon: getWindowIconPath({
      isPackaged: app.isPackaged,
      resourcesPath: process.resourcesPath,
      repoRoot,
    }),
    webPreferences: {
      preload: path.join(app.getAppPath(), "dist", "src", "preload.js"),
      contextIsolation: true,
      nodeIntegration: false,
    },
  });
  mainWindow.setMenuBarVisibility(false);
  mainWindow.webContents.on("did-finish-load", () => {
    mainWindow?.webContents.send("backend:status", currentBackendStatus);
  });
  mainWindow.on("close", (event) => {
    if (!shouldHideWindowOnClose({ isQuitting })) {
      return;
    }

    event.preventDefault();
    mainWindow?.hide();
  });
  publishBackendReady(backend);

  if (!app.isPackaged && process.argv.includes("--dev")) {
    await mainWindow.loadURL("http://127.0.0.1:5173");
    mainWindow.webContents.openDevTools({ mode: "detach" });
    return;
  }

  const indexPath = getFrontendIndexPath({
    isPackaged: app.isPackaged,
    resourcesPath: process.resourcesPath,
    repoRoot,
  });
  await mainWindow.loadURL(pathToFileURL(indexPath).toString());
}

async function startDesktopBackend(): Promise<BackendController> {
  return startBackend({
    isPackaged: app.isPackaged,
    resourcesPath: process.resourcesPath,
    repoRoot,
    userDataPath: app.getPath("userData"),
    onUnexpectedExit: (exit) => {
      void restartBackendAfterUnexpectedExit(exit);
    },
  });
}

async function restartBackendAfterUnexpectedExit(exit: BackendExit): Promise<void> {
  if (restartingBackend || backend === null) {
    return;
  }

  restartingBackend = true;
  backend = null;
  publishBackendStatus({
    state: "restarting",
    code: exit.code,
    signal: exit.signal,
  });

  try {
    backend = await startDesktopBackend();
    publishBackendReady(backend);
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    publishBackendStatus({
      state: "error",
      message,
      phase: "error",
      elapsedSeconds: 0,
    });
  } finally {
    restartingBackend = false;
  }
}

function publishBackendReady(controller: BackendController): void {
  publishBackendStatus(createInitialBackendStatus());
  const unsubscribe = controller.onStatus((status) => publishBackendStatus(status));
  controller.ready
    .then(() => {
      unsubscribe();
      checkForUpdatesOnStartup();
    })
    .catch((error: unknown) => {
      unsubscribe();
      if (currentBackendStatus.state === "error") {
        return;
      }
      const message = error instanceof Error ? error.message : String(error);
      publishBackendStatus({
        state: "error",
        message,
        phase: "error",
        elapsedSeconds: 0,
      });
    });
}

function publishBackendStatus(status: typeof currentBackendStatus): void {
  currentBackendStatus = status;
  mainWindow?.webContents.send("backend:status", status);
}

function createInitialBackendStatus(): BackendStatus {
  return {
    state: "starting",
    phase: "starting",
    message: "正在启动系统服务",
    elapsedSeconds: 0,
    slowStartup: false,
    verySlowStartup: false,
  };
}

function getErrorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

ipcMain.handle("app:get-version", () => app.getVersion());
ipcMain.handle("startup:get-status", async () => {
  currentStartupAtLoginStatus = await getStartupAtLoginStatus(getStartupInput());
  refreshTrayContextMenu();
  return currentStartupAtLoginStatus;
});
ipcMain.handle("startup:set-enabled", async (_event, enabled: unknown) => {
  if (typeof enabled !== "boolean") {
    throw new Error("Invalid startup setting.");
  }

  currentStartupAtLoginStatus = await setStartupAtLoginEnabled(getStartupInput(), enabled);
  refreshTrayContextMenu();
  return currentStartupAtLoginStatus;
});
registerUpdateIpc(() => mainWindow);
registerFileSelectionIpc();
registerMaterialOpenIpc({
  getBackendBaseUrl: () => (currentBackendStatus.state === "ready" ? currentBackendStatus.baseUrl : null),
  userDataPath: app.getPath("userData"),
});

if (hasSingleInstanceLock) {
  app.on("second-instance", showMainWindow);

  app.whenReady().then(() => {
    startWindowCreationOnce(windowCreationState, createWindow).catch((error: unknown) => {
      const message = error instanceof Error ? error.message : String(error);
      dialog.showErrorBox("启动失败", message);
      app.quit();
    });
  });
}

app.on("window-all-closed", () => {
  if (isQuitting) {
    app.quit();
  }
});

app.on("before-quit", (event) => {
  isQuitting = true;
  if (backend === null) {
    return;
  }
  event.preventDefault();
  stopBackendAndExit(0);
});

process.once("SIGINT", () => {
  stopBackendAndExit(130);
});

process.once("SIGTERM", () => {
  stopBackendAndExit(143);
});

