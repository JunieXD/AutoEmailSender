import type { DesktopBridge } from "../../../contracts/desktop-ipc.js";

export type {
  DesktopAgentIntegrationId,
  DesktopAgentIntegrationState,
  DesktopAgentIntegrationStatus,
  DesktopAgentSupportState,
  DesktopAgentSupportStatus,
  DesktopBackendConnection,
  DesktopBackendDatabaseError,
  DesktopBackendStartupPhase,
  DesktopBackendStatus,
  DesktopBridge,
  DesktopCommunityShareSaveResult,
  DesktopMaterialOpenResult,
  DesktopSelectedProfessorImportFile,
  DesktopStartupAtLoginStatus,
  DesktopUpdateDownloadMode,
  DesktopUpdateDownloadProgress,
  DesktopUpdateStatus,
} from "../../../contracts/desktop-ipc.js";

declare global {
  interface Window {
    autoEmailSender?: DesktopBridge;
  }
}
