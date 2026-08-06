import { contextBridge, ipcRenderer, type IpcRendererEvent } from "electron";
import type {
  DesktopAgentSupportStatus,
  DesktopBackendConnection,
  DesktopBackendStatus,
  DesktopBridge,
  DesktopCommunityShareSaveResult,
  DesktopMaterialOpenResult,
  DesktopSelectedProfessorImportFile,
  DesktopStartupAtLoginStatus,
  DesktopUpdateStatus,
} from "../../../contracts/desktop-ipc.js";
import { DESKTOP_IPC_CHANNELS } from "../contracts/channels.js";

export function installDesktopBridge(): void {
  const markDesktopRuntime = (): void => {
    document.documentElement.dataset.runtime = "desktop";
  };

  if (document.documentElement) {
    markDesktopRuntime();
  } else {
    window.addEventListener("DOMContentLoaded", markDesktopRuntime, { once: true });
  }

  let backendBaseUrl: string | null =
    process.argv
      .find((value) => value.startsWith("--backend-base-url="))
      ?.replace("--backend-base-url=", "") ?? null;
  let backendConnection: DesktopBackendConnection | null = null;
  let currentBackendStatus: DesktopBackendStatus = {
    state: "starting",
    phase: "starting",
    message: "正在启动系统服务",
    elapsedSeconds: 0,
    slowStartup: false,
    verySlowStartup: false,
  };
  const backendStatusCallbacks = new Set<(status: DesktopBackendStatus) => void>();

  ipcRenderer.on(
    DESKTOP_IPC_CHANNELS.backendConnection,
    (_event: IpcRendererEvent, connection: DesktopBackendConnection) => {
      backendConnection = connection;
      if (currentBackendStatus.state === "ready") {
        backendBaseUrl = connection.baseUrl;
      }
    },
  );

  ipcRenderer.on(
    DESKTOP_IPC_CHANNELS.backendStatus,
    (_event: IpcRendererEvent, status: DesktopBackendStatus) => {
      currentBackendStatus = status;
      if (status.state === "ready") {
        backendBaseUrl = backendConnection?.baseUrl ?? status.baseUrl;
      } else {
        backendBaseUrl = null;
      }
      backendStatusCallbacks.forEach((callback) => callback(status));
    },
  );

  const bridge = {
    backendBaseUrl,
    getBackendBaseUrl: () => backendBaseUrl ?? undefined,
    getBackendAccessToken: () => backendConnection?.accessToken ?? null,
    getAgentSupportStatus: () =>
      ipcRenderer.invoke(DESKTOP_IPC_CHANNELS.agentSupportGetStatus) as Promise<DesktopAgentSupportStatus>,
    enableAgentSupport: () =>
      ipcRenderer.invoke(DESKTOP_IPC_CHANNELS.agentSupportEnable) as Promise<DesktopAgentSupportStatus>,
    repairAgentSupport: () =>
      ipcRenderer.invoke(DESKTOP_IPC_CHANNELS.agentSupportRepair) as Promise<DesktopAgentSupportStatus>,
    disableAgentSupport: () =>
      ipcRenderer.invoke(DESKTOP_IPC_CHANNELS.agentSupportDisable) as Promise<DesktopAgentSupportStatus>,
    installAgentSkill: (agentId: DesktopAgentSupportStatus["agents"][number]["id"]) =>
      ipcRenderer.invoke(DESKTOP_IPC_CHANNELS.agentSupportInstallSkill, agentId) as Promise<DesktopAgentSupportStatus>,
    uninstallAgentSkill: (agentId: DesktopAgentSupportStatus["agents"][number]["id"]) =>
      ipcRenderer.invoke(DESKTOP_IPC_CHANNELS.agentSupportUninstallSkill, agentId) as Promise<DesktopAgentSupportStatus>,
    dismissAgentSupportOnboarding: () =>
      ipcRenderer.invoke(DESKTOP_IPC_CHANNELS.agentSupportDismissOnboarding) as Promise<DesktopAgentSupportStatus>,
    getVersion: () => ipcRenderer.invoke(DESKTOP_IPC_CHANNELS.appGetVersion) as Promise<string>,
    quitApp: () => ipcRenderer.invoke(DESKTOP_IPC_CHANNELS.appQuit) as Promise<void>,
    selectProfessorImportFile: () =>
      ipcRenderer.invoke(DESKTOP_IPC_CHANNELS.professorSelectImportFile) as Promise<DesktopSelectedProfessorImportFile | null>,
    saveCommunitySharePackage: (data: ArrayBuffer) =>
      ipcRenderer.invoke(
        DESKTOP_IPC_CHANNELS.communityShareSave,
        data,
      ) as Promise<DesktopCommunityShareSaveResult>,
    openMaterial: (request: { materialId: number }) =>
      ipcRenderer.invoke(DESKTOP_IPC_CHANNELS.materialOpen, request) as Promise<DesktopMaterialOpenResult>,
    openExternalUrl: (url: string) =>
      ipcRenderer.invoke(DESKTOP_IPC_CHANNELS.externalUrlOpen, url) as Promise<void>,
    getStartupAtLoginStatus: () =>
      ipcRenderer.invoke(DESKTOP_IPC_CHANNELS.startupGetStatus) as Promise<DesktopStartupAtLoginStatus>,
    setStartupAtLoginEnabled: (enabled: boolean) =>
      ipcRenderer.invoke(DESKTOP_IPC_CHANNELS.startupSetEnabled, enabled) as Promise<DesktopStartupAtLoginStatus>,
    checkForUpdate: () =>
      ipcRenderer.invoke(DESKTOP_IPC_CHANNELS.updateCheck) as Promise<DesktopUpdateStatus>,
    downloadUpdate: (options?: { mode?: "differential" | "full" }) =>
      ipcRenderer.invoke(DESKTOP_IPC_CHANNELS.updateDownload, options) as Promise<DesktopUpdateStatus>,
    switchToFullDownload: () =>
      ipcRenderer.invoke(DESKTOP_IPC_CHANNELS.updateSwitchToFullDownload) as Promise<DesktopUpdateStatus>,
    quitAndInstall: () =>
      ipcRenderer.invoke(DESKTOP_IPC_CHANNELS.updateQuitAndInstall) as Promise<void>,
    onBackendStatus: (callback: (status: DesktopBackendStatus) => void) => {
      backendStatusCallbacks.add(callback);
      queueMicrotask(() => callback(currentBackendStatus));
      return () => {
        backendStatusCallbacks.delete(callback);
      };
    },
    onAgentSupportStatus: (callback: (status: DesktopAgentSupportStatus) => void) => {
      const listener = (_event: IpcRendererEvent, status: DesktopAgentSupportStatus) => callback(status);
      ipcRenderer.on(DESKTOP_IPC_CHANNELS.agentSupportStatus, listener);
      return () => {
        ipcRenderer.removeListener(DESKTOP_IPC_CHANNELS.agentSupportStatus, listener);
      };
    },
    onUpdateStatus: (callback: (status: DesktopUpdateStatus) => void) => {
      const listener = (_event: IpcRendererEvent, status: DesktopUpdateStatus) => callback(status);
      ipcRenderer.on(DESKTOP_IPC_CHANNELS.updateStatus, listener);
      return () => {
        ipcRenderer.removeListener(DESKTOP_IPC_CHANNELS.updateStatus, listener);
      };
    },
  } satisfies DesktopBridge;

  contextBridge.exposeInMainWorld("autoEmailSender", bridge);
}
