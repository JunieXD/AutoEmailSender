import { contextBridge, ipcRenderer, type IpcRendererEvent } from "electron";
import type {
  BackendConnection,
  BackendStatus,
  AgentSupportStatus,
  CommunityShareSaveResult,
  MaterialOpenResult,
  StartupAtLoginStatus,
  UpdateStatus,
} from "./types.js";

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
let backendConnection: BackendConnection | null = null;
let currentBackendStatus: BackendStatus = {
  state: "starting",
  phase: "starting",
  message: "正在启动系统服务",
  elapsedSeconds: 0,
  slowStartup: false,
  verySlowStartup: false,
};
const backendStatusCallbacks = new Set<(status: BackendStatus) => void>();

ipcRenderer.on("backend:connection", (_event: IpcRendererEvent, connection: BackendConnection) => {
  backendConnection = connection;
  if (currentBackendStatus.state === "ready") {
    backendBaseUrl = connection.baseUrl;
  }
});

ipcRenderer.on("backend:status", (_event: IpcRendererEvent, status: BackendStatus) => {
  currentBackendStatus = status;
  if (status.state === "ready") {
    backendBaseUrl = backendConnection?.baseUrl ?? status.baseUrl;
  } else {
    backendBaseUrl = null;
  }
  backendStatusCallbacks.forEach((callback) => callback(status));
});

contextBridge.exposeInMainWorld("autoEmailSender", {
  backendBaseUrl,
  getBackendBaseUrl: () => backendBaseUrl,
  getBackendAccessToken: () => backendConnection?.accessToken ?? null,
  getAgentSupportStatus: () =>
    ipcRenderer.invoke("agent-support:get-status") as Promise<AgentSupportStatus>,
  enableAgentSupport: () =>
    ipcRenderer.invoke("agent-support:enable") as Promise<AgentSupportStatus>,
  repairAgentSupport: () =>
    ipcRenderer.invoke("agent-support:repair") as Promise<AgentSupportStatus>,
  disableAgentSupport: () =>
    ipcRenderer.invoke("agent-support:disable") as Promise<AgentSupportStatus>,
  dismissAgentSupportOnboarding: () =>
    ipcRenderer.invoke("agent-support:dismiss-onboarding") as Promise<AgentSupportStatus>,
  getVersion: () => ipcRenderer.invoke("app:get-version") as Promise<string>,
  quitApp: () => ipcRenderer.invoke("app:quit") as Promise<void>,
  selectProfessorImportFile: () =>
    ipcRenderer.invoke("professors:select-import-file") as Promise<{
      name: string;
      type: string;
      data: ArrayBuffer;
    } | null>,
  saveCommunitySharePackage: (data: ArrayBuffer) =>
    ipcRenderer.invoke(
      "community-share:save",
      data,
    ) as Promise<CommunityShareSaveResult>,
  openMaterial: (request: { materialId: number }) =>
    ipcRenderer.invoke("materials:open", request) as Promise<MaterialOpenResult>,
  openExternalUrl: (url: string) => ipcRenderer.invoke("external-url:open", url) as Promise<void>,
  getStartupAtLoginStatus: () =>
    ipcRenderer.invoke("startup:get-status") as Promise<StartupAtLoginStatus>,
  setStartupAtLoginEnabled: (enabled: boolean) =>
    ipcRenderer.invoke("startup:set-enabled", enabled) as Promise<StartupAtLoginStatus>,
  checkForUpdate: () => ipcRenderer.invoke("update:check") as Promise<UpdateStatus>,
  downloadUpdate: (options?: { mode?: "differential" | "full" }) =>
    ipcRenderer.invoke("update:download", options) as Promise<UpdateStatus>,
  switchToFullDownload: () =>
    ipcRenderer.invoke("update:switch-to-full-download") as Promise<UpdateStatus>,
  quitAndInstall: () => ipcRenderer.invoke("update:quit-and-install") as Promise<void>,
  onBackendStatus: (callback: (status: BackendStatus) => void) => {
    backendStatusCallbacks.add(callback);
    queueMicrotask(() => callback(currentBackendStatus));
    return () => {
      backendStatusCallbacks.delete(callback);
    };
  },
  onAgentSupportStatus: (callback: (status: AgentSupportStatus) => void) => {
    const listener = (_event: IpcRendererEvent, status: AgentSupportStatus) => callback(status);
    ipcRenderer.on("agent-support:status", listener);
    return () => {
      ipcRenderer.removeListener("agent-support:status", listener);
    };
  },
  onUpdateStatus: (callback: (status: UpdateStatus) => void) => {
    const listener = (_event: IpcRendererEvent, status: UpdateStatus) => callback(status);
    ipcRenderer.on("update:status", listener);
    return () => {
      ipcRenderer.removeListener("update:status", listener);
    };
  },
});
