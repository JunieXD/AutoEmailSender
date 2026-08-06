import { app, BrowserWindow, Menu, Tray, dialog, ipcMain, nativeImage, type MenuItemConstructorOptions } from "electron";
import path from "node:path";
import { pathToFileURL } from "node:url";
import { getFrontendIndexPath, startBackend } from "./backend.js";
import { DESKTOP_IPC_CHANNELS } from "./contracts/channels.js";
import {
  AGENT_RUNTIME_PROTOCOL_VERSION,
  cleanupAgentRuntimeDescriptor,
  writeAgentRuntimeDescriptor,
} from "./agentRuntime.js";
import { createAgentSupportService } from "./agentSupportService.js";
import { registerExternalUrlIpc } from "./externalUrlService.js";
import {
  registerCommunityShareSaveIpc,
  registerFileSelectionIpc,
} from "./fileSelection.js";
import { registerMaterialOpenIpc } from "./materialOpenService.js";
import { getStartupAtLoginStatus, isLaunchedAtStartup, setStartupAtLoginEnabled } from "./startup.js";
import { bindTrayInteractions } from "./trayController.js";
import { checkForUpdatesOnStartup, registerUpdateIpc } from "./updates.js";
import {
  restoreExistingWindow,
  shouldHideWindowOnClose,
  startWindowCreationOnce,
} from "./windowLifecycle.js";
import { createTrayIcon, getWindowIconPath } from "./windowIcon.js";
import type {
  BackendConnection,
  BackendController,
  BackendExit,
  BackendStatus,
  AgentIntegrationId,
  AgentSupportStatus,
  StartupAtLoginStatus,
} from "./types.js";

let mainWindow: BrowserWindow | null = null;
let tray: Tray | null = null;
let backend: BackendController | null = null;
let restartingBackend = false;
let isQuitting = false;
let backendStopPromise: Promise<void> | null = null;
let currentBackendStatus: BackendStatus = createInitialBackendStatus();
let currentBackendConnection: BackendConnection | null = null;
let currentAgentSupportStatus: AgentSupportStatus | null = null;
let currentStartupAtLoginStatus: StartupAtLoginStatus | null = null;
const windowCreationState = { pendingCreation: null as Promise<void> | null };


const repoRoot = path.resolve(app.getAppPath(), "..");
const agentSupportHomePath = !app.isPackaged && process.env.AUTO_EMAIL_SENDER_AGENT_HOME?.trim()
  ? path.resolve(process.env.AUTO_EMAIL_SENDER_AGENT_HOME)
  : app.getPath("home");
const agentSupportService = createAgentSupportService({
  platform: process.platform,
  arch: process.arch,
  isPackaged: app.isPackaged,
  resourcesPath: process.resourcesPath,
  repoRoot,
  userDataPath: app.getPath("userData"),
  homePath: agentSupportHomePath,
  localAppDataPath: process.env.LOCALAPPDATA,
  appVersion: app.getVersion(),
  environmentPath: process.env.PATH,
});
const launchedAtStartup = isLaunchedAtStartup({
  argv: process.argv,
  platform: process.platform,
  getLoginItemSettings: () => app.getLoginItemSettings(),
});
app.setAppUserModelId("com.juniexd.autoemailsender");
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
  currentBackendConnection = null;
  backendStopPromise = Promise.all([
    currentBackend?.stop() ?? Promise.resolve(),
    currentBackend ? removeAgentRuntime(currentBackend) : Promise.resolve(false),
  ]).then(
    () => undefined,
    () => undefined,
  ).finally(() => {
      app.exit(exitCode);
    });
}

function getStartupInput() {
  return {
    platform: process.platform,
    isPackaged: app.isPackaged,
    executablePath: process.execPath,
    dependencies:
      process.platform === "darwin"
        ? {
            loginItems: {
              getLoginItemSettings: () => app.getLoginItemSettings(),
              setLoginItemSettings: (settings: { openAtLogin: boolean }) => {
                app.setLoginItemSettings(settings);
              },
            },
          }
        : undefined,
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
  if (enabled && !currentStartupAtLoginStatus.enabled && currentStartupAtLoginStatus.message) {
    void dialog.showMessageBox({
      type: "info",
      title: "需要在系统设置中允许",
      message: currentStartupAtLoginStatus.message,
    });
  }
}

function ensureTray(): void {
  if (tray !== null) {
    return;
  }

  tray = new Tray(createTrayIcon({
    isPackaged: app.isPackaged,
    platform: process.platform,
    resourcesPath: process.resourcesPath,
    repoRoot,
    nativeImage,
  }));
  tray.setToolTip("Auto Email Sender");
  refreshTrayContextMenu();
  void loadStartupAtLoginForTray();
  bindTrayInteractions(tray, {
    openWindow: showMainWindow,
  });
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
      platform: process.platform,
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
  mainWindow.webContents.setWindowOpenHandler(({ url }) => {
    const parsedUrl = parseWebUrl(url);
    if (parsedUrl === null) {
      return { action: "deny" };
    }

    return { action: "allow" };
  });
  mainWindow.webContents.on("did-finish-load", () => {
    if (currentBackendConnection !== null) {
      mainWindow?.webContents.send(DESKTOP_IPC_CHANNELS.backendConnection, currentBackendConnection);
    }
    mainWindow?.webContents.send(DESKTOP_IPC_CHANNELS.backendStatus, currentBackendStatus);
    if (currentAgentSupportStatus !== null) {
      mainWindow?.webContents.send(DESKTOP_IPC_CHANNELS.agentSupportStatus, currentAgentSupportStatus);
    }
  });
  mainWindow.on("close", (event) => {
    if (!shouldHideWindowOnClose({
      isPackaged: app.isPackaged,
      isQuitting,
      platform: process.platform,
    })) {
      return;
    }

    event.preventDefault();
    mainWindow?.hide();
  });
  publishBackendReady(backend);

  if (!app.isPackaged && process.argv.includes("--dev")) {
    const developmentServerUrl = process.env.AUTO_EMAIL_SENDER_DEV_SERVER_URL?.trim()
      || "http://127.0.0.1:5173";
    await mainWindow.loadURL(developmentServerUrl);
    if (process.env.AUTO_EMAIL_SENDER_OPEN_DEVTOOLS === "true") {
      mainWindow.webContents.openDevTools({ mode: "detach" });
    }
    return;
  }

  const indexPath = getFrontendIndexPath({
    isPackaged: app.isPackaged,
    resourcesPath: process.resourcesPath,
    repoRoot,
  });
  await mainWindow.loadURL(pathToFileURL(indexPath).toString());
}

function parseWebUrl(value: string): string | null {
  try {
    const parsedUrl = new URL(value);
    if (parsedUrl.protocol !== "http:" && parsedUrl.protocol !== "https:") {
      return null;
    }
    return parsedUrl.toString();
  } catch {
    return null;
  }
}

async function startDesktopBackend(): Promise<BackendController> {
  const controller = await startBackend({
    isPackaged: app.isPackaged,
    resourcesPath: process.resourcesPath,
    repoRoot,
    userDataPath: app.getPath("userData"),
    onUnexpectedExit: (exit) => {
      void restartBackendAfterUnexpectedExit(exit);
    },
  });
  try {
    await writeAgentRuntimeDescriptor({
      userDataPath: app.getPath("userData"),
      descriptor: {
        protocol_version: AGENT_RUNTIME_PROTOCOL_VERSION,
        app_version: app.getVersion(),
        base_url: controller.baseUrl,
        access_token: controller.agentAccessToken,
        desktop_pid: process.pid,
        started_at: new Date().toISOString(),
      },
    });
  } catch (error) {
    console.warn(`Unable to publish Agent runtime descriptor: ${getErrorMessage(error)}`);
  }
  return controller;
}

async function restartBackendAfterUnexpectedExit(exit: BackendExit): Promise<void> {
  if (restartingBackend || backend === null) {
    return;
  }

  restartingBackend = true;
  const exitedBackend = backend;
  backend = null;
  currentBackendConnection = null;
  if (exitedBackend !== null) {
    await removeAgentRuntime(exitedBackend);
  }
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
  currentBackendConnection = {
    baseUrl: controller.baseUrl,
    accessToken: controller.uiAccessToken,
  };
  mainWindow?.webContents.send(DESKTOP_IPC_CHANNELS.backendConnection, currentBackendConnection);
  publishBackendStatus(createInitialBackendStatus());
  const unsubscribe = controller.onStatus((status) => publishBackendStatus(status));
  controller.ready
    .then(() => {
      unsubscribe();
      checkForUpdatesOnStartup(() => mainWindow);
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

async function removeAgentRuntime(controller: BackendController): Promise<boolean> {
  return cleanupAgentRuntimeDescriptor({
    userDataPath: app.getPath("userData"),
    desktopPid: process.pid,
    accessToken: controller.agentAccessToken,
  });
}

function publishBackendStatus(status: typeof currentBackendStatus): void {
  currentBackendStatus = status;
  mainWindow?.webContents.send(DESKTOP_IPC_CHANNELS.backendStatus, status);
}

function publishAgentSupportStatus(status: AgentSupportStatus): AgentSupportStatus {
  currentAgentSupportStatus = status;
  mainWindow?.webContents.send(DESKTOP_IPC_CHANNELS.agentSupportStatus, status);
  return status;
}

async function runAgentSupportAction(
  state: "installing" | "updating",
  action: () => Promise<AgentSupportStatus>,
  message?: string,
): Promise<AgentSupportStatus> {
  const current = currentAgentSupportStatus ?? await agentSupportService.getStatus();
  publishAgentSupportStatus({
    ...current,
    state,
    message: message ?? (state === "installing" ? "正在安装命令行与 Agent 使用说明…" : "正在更新命令行与 Agent 使用说明…"),
  });
  try {
    return publishAgentSupportStatus(await action());
  } catch (error) {
    const fallback = await agentSupportService.getStatus();
    publishAgentSupportStatus({
      ...fallback,
      state: "needs_repair",
      message: getErrorMessage(error),
    });
    throw error;
  }
}

async function synchronizeAgentSupportOnStartup(): Promise<void> {
  try {
    publishAgentSupportStatus(await agentSupportService.synchronize());
  } catch (error) {
    const fallback = await agentSupportService.getStatus();
    publishAgentSupportStatus({
      ...fallback,
      state: "needs_repair",
      message: `自动更新命令行与 Agent 支持失败：${getErrorMessage(error)}`,
    });
  }
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

function isAgentIntegrationId(value: unknown): value is AgentIntegrationId {
  return value === "codex"
    || value === "claude_code"
    || value === "cursor"
    || value === "copilot_cli";
}

ipcMain.handle(DESKTOP_IPC_CHANNELS.appGetVersion, () => app.getVersion());
ipcMain.handle(DESKTOP_IPC_CHANNELS.appQuit, () => {
  quitFromTray();
});
ipcMain.handle(DESKTOP_IPC_CHANNELS.startupGetStatus, async () => {
  currentStartupAtLoginStatus = await getStartupAtLoginStatus(getStartupInput());
  refreshTrayContextMenu();
  return currentStartupAtLoginStatus;
});
ipcMain.handle(DESKTOP_IPC_CHANNELS.startupSetEnabled, async (_event, enabled: unknown) => {
  if (typeof enabled !== "boolean") {
    throw new Error("Invalid startup setting.");
  }

  currentStartupAtLoginStatus = await setStartupAtLoginEnabled(getStartupInput(), enabled);
  refreshTrayContextMenu();
  return currentStartupAtLoginStatus;
});
ipcMain.handle(DESKTOP_IPC_CHANNELS.agentSupportGetStatus, async () =>
  publishAgentSupportStatus(await agentSupportService.getStatus()),
);
ipcMain.handle(DESKTOP_IPC_CHANNELS.agentSupportEnable, async () =>
  runAgentSupportAction("installing", agentSupportService.enable),
);
ipcMain.handle(DESKTOP_IPC_CHANNELS.agentSupportRepair, async () =>
  runAgentSupportAction("installing", agentSupportService.repair),
);
ipcMain.handle(DESKTOP_IPC_CHANNELS.agentSupportDisable, async () =>
  runAgentSupportAction("updating", agentSupportService.disable),
);
ipcMain.handle(DESKTOP_IPC_CHANNELS.agentSupportInstallSkill, async (_event, agentId: unknown) => {
  if (!isAgentIntegrationId(agentId)) {
    throw new Error("不支持的 Agent。");
  }
  return runAgentSupportAction(
    "installing",
    () => agentSupportService.installAgentSkill(agentId),
    "正在安装 Agent 使用说明…",
  );
});
ipcMain.handle(DESKTOP_IPC_CHANNELS.agentSupportUninstallSkill, async (_event, agentId: unknown) => {
  if (!isAgentIntegrationId(agentId)) {
    throw new Error("不支持的 Agent。");
  }
  return runAgentSupportAction(
    "updating",
    () => agentSupportService.uninstallAgentSkill(agentId),
    "正在卸载 Agent 使用说明…",
  );
});
ipcMain.handle(DESKTOP_IPC_CHANNELS.agentSupportDismissOnboarding, async () =>
  publishAgentSupportStatus(await agentSupportService.dismissOnboarding()),
);
registerUpdateIpc(() => mainWindow);
registerFileSelectionIpc();
registerCommunityShareSaveIpc();
registerExternalUrlIpc();
registerMaterialOpenIpc({
  getBackendBaseUrl: () => (currentBackendStatus.state === "ready" ? currentBackendStatus.baseUrl : null),
  getBackendAccessToken: () => backend?.uiAccessToken ?? null,
  userDataPath: app.getPath("userData"),
});

if (hasSingleInstanceLock) {
  app.on("second-instance", () => {
    showMainWindow();
  });

  app.whenReady().then(() => {
    startWindowCreationOnce(windowCreationState, createWindow).catch((error: unknown) => {
      const message = error instanceof Error ? error.message : String(error);
      dialog.showErrorBox("启动失败", message);
      app.quit();
    });
    void synchronizeAgentSupportOnStartup();
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
