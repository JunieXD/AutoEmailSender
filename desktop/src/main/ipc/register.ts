import { ipcMain, type BrowserWindow } from "electron";
import type {
  DesktopAgentIntegrationId,
  DesktopAgentSupportEnableOptions,
  DesktopAgentSupportStatus,
  DesktopStartupAtLoginStatus,
} from "../../../../contracts/desktop-ipc.js";
import { DESKTOP_IPC_CHANNELS } from "../../contracts/channels.js";
import {
  registerCommunityShareSaveIpc,
  registerFileSelectionIpc,
} from "../files/import-export.js";
import {
  registerMaterialOpenIpc,
  type MaterialOpenServiceOptions,
} from "../files/material-open.js";
import { registerExternalUrlIpc } from "../shell/external-url.js";
import { registerUpdateIpc } from "../updates/service.js";

export type DesktopIpcRegistrationOptions = {
  getVersion: () => string;
  quitApp: () => void;
  getStartupAtLoginStatus: () => Promise<DesktopStartupAtLoginStatus>;
  setStartupAtLoginEnabled: (enabled: boolean) => Promise<DesktopStartupAtLoginStatus>;
  getAgentSupportStatus: () => Promise<DesktopAgentSupportStatus>;
  enableAgentSupport: (options: DesktopAgentSupportEnableOptions) => Promise<DesktopAgentSupportStatus>;
  repairAgentSupport: () => Promise<DesktopAgentSupportStatus>;
  disableAgentSupport: () => Promise<DesktopAgentSupportStatus>;
  installAgentSkill: (agentId: DesktopAgentIntegrationId) => Promise<DesktopAgentSupportStatus>;
  uninstallAgentSkill: (agentId: DesktopAgentIntegrationId) => Promise<DesktopAgentSupportStatus>;
  dismissAgentSupportOnboarding: () => Promise<DesktopAgentSupportStatus>;
  getWindow: () => BrowserWindow | null;
  materialOpen: MaterialOpenServiceOptions;
};

export function registerDesktopIpc(options: DesktopIpcRegistrationOptions): void {
  ipcMain.handle(DESKTOP_IPC_CHANNELS.appGetVersion, options.getVersion);
  ipcMain.handle(DESKTOP_IPC_CHANNELS.appQuit, options.quitApp);
  ipcMain.handle(DESKTOP_IPC_CHANNELS.startupGetStatus, options.getStartupAtLoginStatus);
  ipcMain.handle(DESKTOP_IPC_CHANNELS.startupSetEnabled, (_event, enabled: unknown) => {
    if (typeof enabled !== "boolean") {
      throw new Error("Invalid startup setting.");
    }
    return options.setStartupAtLoginEnabled(enabled);
  });
  ipcMain.handle(DESKTOP_IPC_CHANNELS.agentSupportGetStatus, options.getAgentSupportStatus);
  ipcMain.handle(DESKTOP_IPC_CHANNELS.agentSupportEnable, (_event, request: unknown) => {
    if (!isAgentSupportEnableOptions(request)) {
      throw new Error("无效的 Agent 支持启用选项。");
    }
    return options.enableAgentSupport(request ?? {});
  });
  ipcMain.handle(DESKTOP_IPC_CHANNELS.agentSupportRepair, options.repairAgentSupport);
  ipcMain.handle(DESKTOP_IPC_CHANNELS.agentSupportDisable, options.disableAgentSupport);
  ipcMain.handle(DESKTOP_IPC_CHANNELS.agentSupportInstallSkill, (_event, agentId: unknown) => {
    if (!isAgentIntegrationId(agentId)) {
      throw new Error("不支持的 Agent。");
    }
    return options.installAgentSkill(agentId);
  });
  ipcMain.handle(DESKTOP_IPC_CHANNELS.agentSupportUninstallSkill, (_event, agentId: unknown) => {
    if (!isAgentIntegrationId(agentId)) {
      throw new Error("不支持的 Agent。");
    }
    return options.uninstallAgentSkill(agentId);
  });
  ipcMain.handle(
    DESKTOP_IPC_CHANNELS.agentSupportDismissOnboarding,
    options.dismissAgentSupportOnboarding,
  );

  registerUpdateIpc(options.getWindow);
  registerFileSelectionIpc();
  registerCommunityShareSaveIpc();
  registerExternalUrlIpc();
  registerMaterialOpenIpc(options.materialOpen);
}

export function isAgentIntegrationId(value: unknown): value is DesktopAgentIntegrationId {
  return value === "codex"
    || value === "claude_code"
    || value === "cursor"
    || value === "copilot_cli";
}

export function isAgentSupportEnableOptions(
  value: unknown,
): value is DesktopAgentSupportEnableOptions | undefined {
  if (value === undefined) {
    return true;
  }
  if (value === null || typeof value !== "object" || Array.isArray(value)) {
    return false;
  }
  const candidate = value as Record<string, unknown>;
  return Object.keys(candidate).every((key) => key === "installDetectedAgents")
    && (
      candidate.installDetectedAgents === undefined
      || typeof candidate.installDetectedAgents === "boolean"
    );
}
