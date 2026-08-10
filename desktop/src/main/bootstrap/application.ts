import { app, BrowserWindow, Menu, Tray, dialog, nativeImage, powerMonitor, type MenuItemConstructorOptions } from "electron";
import path from "node:path";
import { pathToFileURL } from "node:url";
import type {
  DesktopAgentSupportStatus as AgentSupportStatus,
  DesktopBackendConnection as BackendConnection,
  DesktopBackendModeRestartOptions,
  DesktopBackendModeRestartResult,
  DesktopBackendModeStatus,
  DesktopBackendStatus as BackendStatus,
  DesktopRestartSafety,
  DesktopStartupAtLoginStatus as StartupAtLoginStatus,
} from "../../../../contracts/desktop-ipc.js";
import { DESKTOP_IPC_CHANNELS } from "../../contracts/channels.js";
import {
  getFrontendIndexPath,
  startBackend,
} from "../backend/service.js";
import { createDesktopBackendClient } from "../backend/client.js";
import {
  createIdleRestartSafety,
  createUnavailableRestartSafety,
  decideBackendModeRestart,
  getBackendRestartSafety,
} from "../backend/restart-safety.js";
import type {
  BackendController,
  BackendExit,
  BackendMode,
} from "../backend/types.js";
import {
  AGENT_RUNTIME_PROTOCOL_VERSION,
  clearAgentRuntimeDescriptor,
  cleanupAgentRuntimeDescriptor,
  writeAgentRuntimeDescriptor,
} from "../agent-support/runtime.js";
import { createAgentSupportService } from "../agent-support/service.js";
import { registerDesktopIpc } from "../ipc/register.js";
import { getStartupAtLoginStatus, isLaunchedAtStartup, setStartupAtLoginEnabled } from "../shell/startup-at-login.js";
import { bindTrayInteractions } from "../shell/tray.js";
import {
  isProtectedBackendNavigation,
  preventProtectedBackendNavigation,
} from "../shell/backend-navigation-guard.js";
import {
  createExternalUrlService,
  parseExternalNavigationUrl,
  parseWebUrl,
} from "../shell/external-url.js";
import { checkForUpdatesOnStartup } from "../updates/service.js";
import {
  restoreExistingWindow,
  shouldHideWindowOnClose,
  startWindowCreationOnce,
} from "../shell/window-lifecycle.js";
import { createTrayIcon, getWindowIconPath } from "../shell/window-icon.js";
import { getActivePackagedQaIsolatedHomePath } from "../packaged-qa/user-data.js";
import {
  buildBackendModeRelaunchArgs,
  buildBackendModeStatus,
  readBackendModeSetting,
  resolveBackendModeSelection,
  writeBackendModeSetting,
} from "../settings/backend-mode.js";

let mainWindow: BrowserWindow | null = null;
let tray: Tray | null = null;
let backend: BackendController | null = null;
let restartingBackend = false;
let isQuitting = false;
let backendStopPromise: Promise<void> | null = null;
let currentBackendMode: BackendMode | null = null;
let relaunchRequested = false;
let nativeSplitRecoveryPromptVisible = false;
const desktopStartedAt = new Date().toISOString();
let currentBackendStatus: BackendStatus = createInitialBackendStatus();
let currentBackendConnection: BackendConnection | null = null;
let currentAgentSupportStatus: AgentSupportStatus | null = null;
let currentStartupAtLoginStatus: StartupAtLoginStatus | null = null;
const windowCreationState = { pendingCreation: null as Promise<void> | null };
let desktopApplicationBootstrapped = false;
const desktopBackendClient = createDesktopBackendClient({
  getConnection: () => (
    backend === null
      ? null
      : {
          baseUrl: backend.baseUrl,
          accessToken: backend.uiAccessToken,
        }
  ),
});


const repoRoot = path.resolve(app.getAppPath(), "..");
const packagedQaIsolatedHomePath = getActivePackagedQaIsolatedHomePath();
const agentSupportHomePath = packagedQaIsolatedHomePath
  ?? (
    !app.isPackaged && process.env.AUTO_EMAIL_SENDER_AGENT_HOME?.trim()
      ? path.resolve(process.env.AUTO_EMAIL_SENDER_AGENT_HOME)
      : app.getPath("home")
  );
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

async function resolveNextBackendMode() {
  const setting = await readBackendModeSetting(app.getPath("userData"));
  return resolveBackendModeSelection({
    argv: process.argv,
    environmentMode: process.env.AUTO_EMAIL_SENDER_BACKEND_MODE,
    setting,
    appVersion: app.getVersion(),
  });
}

async function ensureCurrentBackendMode(): Promise<BackendMode> {
  if (currentBackendMode !== null) {
    return currentBackendMode;
  }
  const resolution = await resolveNextBackendMode();
  currentBackendMode = resolution.mode;
  if (resolution.warning) {
    console.warn(resolution.warning);
  }
  return currentBackendMode;
}

async function getDesktopBackendModeStatus(): Promise<DesktopBackendModeStatus> {
  const currentMode = await ensureCurrentBackendMode();
  const next = await resolveNextBackendMode();
  return buildBackendModeStatus(currentMode, next);
}

async function setDesktopBackendMode(mode: BackendMode): Promise<DesktopBackendModeStatus> {
  await writeBackendModeSetting(app.getPath("userData"), mode);
  return getDesktopBackendModeStatus();
}

async function getRestartSafetyForRelaunch(options?: {
  allowUnavailableBackend?: boolean;
}): Promise<DesktopRestartSafety> {
  if (backend === null) {
    return createIdleRestartSafety("后台进程当前未运行，可以进入兼容模式。");
  }
  try {
    return await getBackendRestartSafety(desktopBackendClient);
  } catch (error) {
    if (
      options?.allowUnavailableBackend
      && (currentBackendStatus.state === "error" || currentBackendStatus.state === "restarting")
    ) {
      return createIdleRestartSafety(
        "后台服务未成功启动，可以重启进入单进程兼容模式。",
      );
    }
    return createUnavailableRestartSafety(
      `无法确认当前是否可以安全重启：${getErrorMessage(error)}`,
    );
  }
}

async function requestDesktopBackendModeRestart(
  options: DesktopBackendModeRestartOptions = {},
  internalOptions?: {
    forcedMode?: BackendMode;
    allowUnavailableBackend?: boolean;
  },
): Promise<DesktopBackendModeRestartResult> {
  const safety = await getRestartSafetyForRelaunch({
    allowUnavailableBackend: internalOptions?.allowUnavailableBackend,
  });
  const decision = decideBackendModeRestart(safety, options);
  if (decision.state === "restarting") {
    scheduleDesktopRelaunch(internalOptions?.forcedMode);
  }
  return decision;
}

function scheduleDesktopRelaunch(forcedMode?: BackendMode): void {
  if (relaunchRequested) {
    return;
  }
  relaunchRequested = true;
  const currentArgs = process.argv.slice(1);
  app.relaunch({
    args: forcedMode === undefined
      ? currentArgs
      : buildBackendModeRelaunchArgs(currentArgs, forcedMode),
  });
  setTimeout(() => {
    isQuitting = true;
    app.quit();
  }, 0);
}

async function switchToCombinedModeFromNative(): Promise<void> {
  try {
    await writeBackendModeSetting(app.getPath("userData"), "combined");
  } catch (error) {
    console.warn(`无法保存单进程兼容模式：${getErrorMessage(error)}`);
  }

  let result = await requestDesktopBackendModeRestart(
    {},
    { forcedMode: "combined", allowUnavailableBackend: true },
  );
  if (result.state === "confirmation_required") {
    const confirmation = await dialog.showMessageBox({
      type: "warning",
      title: "后台工作正在进行",
      message: result.safety.message,
      detail: "确认后将停止当前后台进程，并使用单进程兼容模式重启。",
      buttons: ["安全重启", "取消"],
      defaultId: 1,
      cancelId: 1,
      noLink: true,
    });
    if (confirmation.response !== 0) {
      return;
    }
    result = await requestDesktopBackendModeRestart(
      { confirmActiveWork: true },
      { forcedMode: "combined", allowUnavailableBackend: true },
    );
  }
  if (result.state === "blocked") {
    await dialog.showMessageBox({
      type: "warning",
      title: "现在不能重启",
      message: result.safety.message,
      buttons: ["知道了"],
      defaultId: 0,
      noLink: true,
    });
  }
}

async function offerNativeSplitRecovery(
  detail: string,
  quitOnDismiss: boolean,
): Promise<void> {
  if (
    currentBackendMode !== "split"
    || nativeSplitRecoveryPromptVisible
    || relaunchRequested
    || isQuitting
  ) {
    if (quitOnDismiss && !relaunchRequested) {
      dialog.showErrorBox("启动失败", detail);
      app.quit();
    }
    return;
  }

  nativeSplitRecoveryPromptVisible = true;
  try {
    const result = await dialog.showMessageBox({
      type: "error",
      title: "API + Worker 测试模式启动失败",
      message: "可以改用单进程兼容模式重启。",
      detail,
      buttons: ["使用兼容模式重启", quitOnDismiss ? "退出" : "留在当前页面"],
      defaultId: 0,
      cancelId: 1,
      noLink: true,
    });
    if (result.response === 0) {
      await switchToCombinedModeFromNative();
    } else if (quitOnDismiss) {
      app.quit();
    }
  } finally {
    nativeSplitRecoveryPromptVisible = false;
  }
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
    {
      label: "使用单进程兼容模式并重启",
      visible: currentBackendMode === "split",
      click: () => {
        void switchToCombinedModeFromNative();
      },
    },
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
  const externalUrlService = createExternalUrlService();

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
      sandbox: true,
    },
  });
  mainWindow.setMenuBarVisibility(false);
  mainWindow.webContents.setWindowOpenHandler(({ url }) => {
    if (isProtectedBackendNavigation(url, backend?.baseUrl)) {
      return { action: "deny" };
    }
    const parsedUrl = parseWebUrl(url);
    if (parsedUrl !== null) {
      void externalUrlService.openExternalUrl(parsedUrl).catch((error: unknown) => {
        console.error(`Failed to open external URL in the system browser: ${parsedUrl}`, error);
      });
    }
    return { action: "deny" };
  });
  mainWindow.webContents.on("will-navigate", (event, url) => {
    if (preventProtectedBackendNavigation(event, url, backend?.baseUrl)) {
      return;
    }
    const parsedUrl = parseExternalNavigationUrl(
      url,
      mainWindow?.webContents.getURL(),
    );
    if (parsedUrl === null) {
      return;
    }

    event.preventDefault();
    void externalUrlService.openExternalUrl(parsedUrl).catch((error: unknown) => {
      console.error(`Failed to open external URL in the system browser: ${parsedUrl}`, error);
    });
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
  checkForUpdatesOnStartup(() => mainWindow);
}

async function startDesktopBackend(): Promise<BackendController> {
  const userDataPath = app.getPath("userData");
  const mode = await ensureCurrentBackendMode();
  await clearAgentRuntimeDescriptor(userDataPath);
  const controller = await startBackend({
    isPackaged: app.isPackaged,
    resourcesPath: process.resourcesPath,
    repoRoot,
    userDataPath,
    appVersion: app.getVersion(),
    mode,
    onUnexpectedExit: (exit) => {
      void restartBackendAfterUnexpectedExit(exit);
    },
  });
  try {
    await publishAgentRuntimeDescriptor(controller, controller.backendPid);
  } catch (error) {
    await controller.stop().catch(() => undefined);
    throw new Error(`Unable to publish Agent runtime descriptor: ${getErrorMessage(error)}`);
  }
  return controller;
}

async function publishAgentRuntimeDescriptor(
  controller: BackendController,
  backendPid: number,
): Promise<void> {
  await writeAgentRuntimeDescriptor({
    userDataPath: app.getPath("userData"),
    descriptor: {
      protocol_version: AGENT_RUNTIME_PROTOCOL_VERSION,
      app_version: app.getVersion(),
      runtime_id: controller.runtimeId,
      base_url: controller.baseUrl,
      access_token: controller.agentAccessToken,
      desktop: {
        pid: process.pid,
        started_at: desktopStartedAt,
      },
      backend: {
        pid: backendPid,
        started_at: controller.backendStartedAt,
      },
      ...(controller.workerPid === undefined || controller.workerStartedAt === undefined
        ? {}
        : {
            worker: {
              pid: controller.workerPid,
              started_at: controller.workerStartedAt,
            },
          }),
      published_at: new Date().toISOString(),
    },
  });
}

async function finalizeAgentRuntimeDescriptor(controller: BackendController): Promise<void> {
  const runtime = await controller.getRuntimeInfo();
  if (
    runtime.runtime_id !== controller.runtimeId
    || runtime.protocol_version !== AGENT_RUNTIME_PROTOCOL_VERSION
    || runtime.app_version !== app.getVersion()
    || runtime.desktop_pid !== process.pid
    || runtime.state !== "ready"
  ) {
    throw new Error("Backend runtime identity did not match the launched desktop instance.");
  }
  await publishAgentRuntimeDescriptor(controller, runtime.backend_pid);
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
    if (currentBackendMode === "split") {
      void offerNativeSplitRecovery(message, false);
    }
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
  controller.onStatus((status) => {
    const previousStatus = currentBackendStatus;
    publishBackendStatus(status);
    if (status.state === "restarting") {
      void removeAgentRuntime(controller);
      return;
    }
    if (status.state === "ready" || status.state === "degraded") {
      void finalizeAgentRuntimeDescriptor(controller).catch((error: unknown) => {
        console.warn(`Unable to refresh Agent runtime descriptor: ${getErrorMessage(error)}`);
      });
    }
    if (
      status.state === "error"
      && previousStatus.state === "restarting"
      && controller.mode === "split"
    ) {
      void offerNativeSplitRecovery(status.message, false);
    }
  });
  controller.ready
    .then(() => {
      void finalizeAgentRuntimeDescriptor(controller).catch(async (error: unknown) => {
        await removeAgentRuntime(controller);
        console.warn(`Unable to finalize Agent runtime descriptor: ${getErrorMessage(error)}`);
      });
    })
    .catch((error: unknown) => {
      void removeAgentRuntime(controller);
      if (currentBackendStatus.state === "error") {
        if (controller.mode === "split") {
          void offerNativeSplitRecovery(currentBackendStatus.message, false);
        }
        return;
      }
      const message = error instanceof Error ? error.message : String(error);
      publishBackendStatus({
        state: "error",
        message,
        phase: "error",
        elapsedSeconds: 0,
      });
      if (controller.mode === "split") {
        void offerNativeSplitRecovery(message, false);
      }
    });
}

async function removeAgentRuntime(controller: BackendController): Promise<boolean> {
  return cleanupAgentRuntimeDescriptor({
    userDataPath: app.getPath("userData"),
    runtimeId: controller.runtimeId,
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

export function bootstrapDesktopApplication(): void {
  if (desktopApplicationBootstrapped) {
    return;
  }
  desktopApplicationBootstrapped = true;

  app.setAppUserModelId("com.juniexd.autoemailsender");
  const hasSingleInstanceLock = app.requestSingleInstanceLock();
  if (!hasSingleInstanceLock) {
    app.quit();
  }

  registerDesktopIpc({
    getVersion: () => app.getVersion(),
    quitApp: quitFromTray,
    getBackendModeStatus: getDesktopBackendModeStatus,
    setBackendMode: setDesktopBackendMode,
    restartForBackendMode: requestDesktopBackendModeRestart,
    getStartupAtLoginStatus: async () => {
      currentStartupAtLoginStatus = await getStartupAtLoginStatus(getStartupInput());
      refreshTrayContextMenu();
      return currentStartupAtLoginStatus;
    },
    setStartupAtLoginEnabled: async (enabled) => {
      currentStartupAtLoginStatus = await setStartupAtLoginEnabled(getStartupInput(), enabled);
      refreshTrayContextMenu();
      return currentStartupAtLoginStatus;
    },
    getAgentSupportStatus: async () =>
      publishAgentSupportStatus(await agentSupportService.getStatus()),
    enableAgentSupport: (request) =>
      runAgentSupportAction("installing", () => agentSupportService.enable(request)),
    repairAgentSupport: () =>
      runAgentSupportAction("installing", agentSupportService.repair),
    disableAgentSupport: () =>
      runAgentSupportAction("updating", agentSupportService.disable),
    installAgentSkill: (agentId) =>
      runAgentSupportAction(
        "installing",
        () => agentSupportService.installAgentSkill(agentId),
        "正在安装 Agent 使用说明…",
      ),
    uninstallAgentSkill: (agentId) =>
      runAgentSupportAction(
        "updating",
        () => agentSupportService.uninstallAgentSkill(agentId),
        "正在卸载 Agent 使用说明…",
      ),
    dismissAgentSupportOnboarding: async () =>
      publishAgentSupportStatus(await agentSupportService.dismissOnboarding()),
    getWindow: () => mainWindow,
    materialOpen: {
      backendClient: desktopBackendClient,
      userDataPath: app.getPath("userData"),
    },
  });

  if (hasSingleInstanceLock) {
    app.on("second-instance", () => {
      showMainWindow();
    });

    app.whenReady().then(() => {
      powerMonitor.on("resume", () => {
        backend?.notifySystemResume?.();
      });
      startWindowCreationOnce(windowCreationState, createWindow).catch((error: unknown) => {
        const message = error instanceof Error ? error.message : String(error);
        void offerNativeSplitRecovery(message, true);
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
}
