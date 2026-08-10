import type { DesktopBridge } from "../../../contracts/desktop-ipc.js";

export type {
  DesktopAgentIntegrationId,
  DesktopAgentIntegrationState,
  DesktopAgentIntegrationStatus,
  DesktopAgentSupportEnableOptions,
  DesktopAgentSupportState,
  DesktopAgentSupportStatus,
  DesktopBackendConnection,
  DesktopBackendDatabaseError,
  DesktopBackendMode,
  DesktopBackendModeRestartOptions,
  DesktopBackendModeRestartResult,
  DesktopBackendModeSource,
  DesktopBackendModeStatus,
  DesktopBackendStartupPhase,
  DesktopBackendStatus,
  DesktopBetaDiagnosticsExportResult,
  DesktopBetaDiagnosticsProblemCategory,
  DesktopBetaDiagnosticsRange,
  DesktopBetaDiagnosticsStatus,
  DesktopBridge,
  DesktopCommunityShareSaveResult,
  DesktopMaterialOpenResult,
  DesktopRestartSafety,
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
