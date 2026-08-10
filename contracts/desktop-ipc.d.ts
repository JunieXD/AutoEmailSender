export type DesktopUpdateDownloadMode = "differential" | "full";

export type DesktopUpdateDownloadProgress = {
  percent: number;
  transferredBytes: number;
  totalBytes: number;
  remainingBytes: number;
  bytesPerSecond: number;
  remainingSeconds: number | null;
  mode: DesktopUpdateDownloadMode;
  fallbackFromDifferential?: boolean;
};

export type DesktopUpdateStatus =
  | { state: "idle"; version: string }
  | { state: "checking"; version: string }
  | {
      state: "available";
      version: string;
      nextVersion: string;
      fullDownloadBytes?: number;
      releaseNotes?: string;
    }
  | { state: "not_available"; version: string }
  | ({ state: "downloading"; version: string; nextVersion: string } & DesktopUpdateDownloadProgress)
  | ({ state: "slow_download_offered"; version: string; nextVersion: string; fullDownloadBytes?: number } & DesktopUpdateDownloadProgress)
  | { state: "downloaded_pending_install"; version: string; nextVersion: string; fullDownloadBytes?: number }
  | { state: "installing"; version: string; nextVersion: string }
  | { state: "error"; version: string; message: string };

export type DesktopMaterialOpenResult =
  | { ok: true }
  | {
      ok: false;
      code:
        | "MaterialOpenInvalidId"
        | "MaterialOpenBackendUnavailable"
        | "MaterialOpenNotFound"
        | "MaterialOpenCopyFailed"
        | "MaterialOpenSystemFailed";
      message: string;
    };

export type DesktopCommunityShareSaveResult =
  | { status: "saved" }
  | { status: "canceled" };

export type DesktopBackendConnection = {
  baseUrl: string;
  accessToken: string;
};

export type DesktopBackendMode = "combined" | "split";

export type DesktopBackendModeSource =
  | "command_line"
  | "environment"
  | "settings"
  | "channel_default";

export type DesktopBackendModeStatus = {
  currentMode: DesktopBackendMode;
  nextMode: DesktopBackendMode;
  configuredMode: DesktopBackendMode | null;
  defaultMode: DesktopBackendMode;
  source: DesktopBackendModeSource;
  restartRequired: boolean;
  overrideActive: boolean;
  warning?: string;
};

export type DesktopRestartSafety = {
  safeToRestart: boolean;
  confirmationRequired: boolean;
  activeWorkCount: number;
  sendingCount: number;
  workCounts: {
    draftGeneration: number;
    matchAnalysis: number;
    crawler: number;
    imapSync: number;
  };
  message: string;
};

export type DesktopBackendModeRestartOptions = {
  confirmActiveWork?: boolean;
};

export type DesktopBackendModeRestartResult = {
  state: "blocked" | "confirmation_required" | "restarting";
  safety: DesktopRestartSafety;
};

export type DesktopBackendDatabaseError = {
  code: "DATABASE_REQUIRES_NEWER_APP";
  message: string;
  currentAppVersion: string;
  minimumSupportedAppVersion: string;
  backupDirectory: string;
  suggestedActions: string[];
};

export type DesktopBackendStartupPhase =
  | "starting"
  | "migrating_database"
  | "cleaning_logs"
  | "starting_workers"
  | "ready"
  | "error";

export type DesktopBackendStatus =
  | {
      state: "starting";
      phase: Exclude<DesktopBackendStartupPhase, "ready" | "error">;
      message: string;
      elapsedSeconds: number;
      slowStartup: boolean;
      verySlowStartup: boolean;
    }
  | { state: "restarting"; code: number | null; signal: string | null }
  | {
      state: "degraded";
      baseUrl: string;
      reason:
        | "background_unavailable"
        | "background_restarting"
        | "background_hung"
        | "background_degraded";
      message: string;
      workerPid?: number;
      circuitOpenUntil?: string;
      detail?: string;
    }
  | {
      state: "ready";
      baseUrl: string;
      phase: "ready";
      message: string;
      elapsedSeconds: number;
    }
  | {
      state: "error";
      message: string;
      phase: "error";
      elapsedSeconds: number;
      detail?: string;
      databaseError?: DesktopBackendDatabaseError;
    };

export type DesktopStartupAtLoginStatus = {
  supported: boolean;
  enabled: boolean;
  message?: string;
};

export type DesktopAgentSupportState =
  | "not_enabled"
  | "installing"
  | "enabled"
  | "needs_repair"
  | "updating"
  | "unsupported";

export type DesktopAgentIntegrationId =
  | "codex"
  | "claude_code"
  | "cursor"
  | "copilot_cli";

export type DesktopAgentIntegrationState =
  | "not_installed"
  | "installed"
  | "needs_update"
  | "conflict"
  | "available_via_shared";

export type DesktopAgentIntegrationStatus = {
  id: DesktopAgentIntegrationId;
  name: string;
  detected: boolean;
  state: DesktopAgentIntegrationState;
  skillPath: string;
  message: string;
  sharedBy?: DesktopAgentIntegrationId;
};

export type DesktopAgentSupportEnableOptions = {
  installDetectedAgents?: boolean;
};

export type DesktopAgentSupportStatus = {
  supported: boolean;
  state: DesktopAgentSupportState;
  message: string;
  onboardingPending: boolean;
  cliCommand: string;
  cliPath: string;
  skillPath: string;
  agents: DesktopAgentIntegrationStatus[];
  appVersion: string;
  requiresAgentRestart: boolean;
};

export type DesktopSelectedProfessorImportFile = {
  name: string;
  type: string;
  data: ArrayBuffer;
};

export type DesktopBridge = {
  backendBaseUrl?: string | null;
  getBackendBaseUrl?: () => string | undefined;
  getBackendAccessToken?: () => string | null | undefined;
  getBackendModeStatus?: () => Promise<DesktopBackendModeStatus>;
  setBackendMode?: (mode: DesktopBackendMode) => Promise<DesktopBackendModeStatus>;
  restartForBackendMode?: (
    options?: DesktopBackendModeRestartOptions,
  ) => Promise<DesktopBackendModeRestartResult>;
  getAgentSupportStatus?: () => Promise<DesktopAgentSupportStatus>;
  enableAgentSupport?: (options?: DesktopAgentSupportEnableOptions) => Promise<DesktopAgentSupportStatus>;
  repairAgentSupport?: () => Promise<DesktopAgentSupportStatus>;
  disableAgentSupport?: () => Promise<DesktopAgentSupportStatus>;
  installAgentSkill?: (agentId: DesktopAgentIntegrationId) => Promise<DesktopAgentSupportStatus>;
  uninstallAgentSkill?: (agentId: DesktopAgentIntegrationId) => Promise<DesktopAgentSupportStatus>;
  dismissAgentSupportOnboarding?: () => Promise<DesktopAgentSupportStatus>;
  getVersion: () => Promise<string>;
  quitApp?: () => Promise<void>;
  selectProfessorImportFile?: () => Promise<DesktopSelectedProfessorImportFile | null>;
  saveCommunitySharePackage?: (data: ArrayBuffer) => Promise<DesktopCommunityShareSaveResult>;
  openMaterial?: (request: { materialId: number }) => Promise<DesktopMaterialOpenResult>;
  openExternalUrl?: (url: string) => Promise<void>;
  getStartupAtLoginStatus?: () => Promise<DesktopStartupAtLoginStatus>;
  setStartupAtLoginEnabled?: (enabled: boolean) => Promise<DesktopStartupAtLoginStatus>;
  checkForUpdate: () => Promise<DesktopUpdateStatus>;
  downloadUpdate: (options?: { mode?: DesktopUpdateDownloadMode }) => Promise<DesktopUpdateStatus>;
  switchToFullDownload: () => Promise<DesktopUpdateStatus>;
  quitAndInstall: () => Promise<void>;
  onBackendStatus?: (callback: (status: DesktopBackendStatus) => void) => () => void;
  onAgentSupportStatus?: (callback: (status: DesktopAgentSupportStatus) => void) => () => void;
  onUpdateStatus: (callback: (status: DesktopUpdateStatus) => void) => () => void;
};
