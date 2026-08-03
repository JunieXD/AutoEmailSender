export type BackendPathInput = {
  isPackaged: boolean;
  platform?: NodeJS.Platform;
  resourcesPath: string;
  repoRoot: string;
};

export type BackendEnvInput = {
  baseEnv: NodeJS.ProcessEnv;
  isPackaged: boolean;
  resourcesPath: string;
  repoRoot: string;
  userDataPath: string;
  appVersion: string;
  electronExecutablePath: string;
  uiAccessToken?: string;
  agentAccessToken?: string;
};

export type BackendController = {
  baseUrl: string;
  uiAccessToken: string;
  agentAccessToken: string;
  ready: Promise<void>;
  onStatus: (handler: (status: BackendStatus) => void) => () => void;
  stop: () => Promise<void>;
};

export type BackendConnection = {
  baseUrl: string;
  accessToken: string;
};

export type BackendExit = {
  code: number | null;
  signal: NodeJS.Signals | null;
};

export type BackendExitHandler = (exit: BackendExit) => void;

export type BackendStartupPhase =
  | "starting"
  | "migrating_database"
  | "cleaning_logs"
  | "starting_workers"
  | "ready"
  | "error";

export type DatabaseRequiresNewerAppDetail = {
  code: "DATABASE_REQUIRES_NEWER_APP";
  message: string;
  current_app_version: string;
  minimum_supported_app_version: string;
  backup_directory: string;
  suggested_actions: string[];
};

export type BackendDatabaseError = {
  code: "DATABASE_REQUIRES_NEWER_APP";
  message: string;
  currentAppVersion: string;
  minimumSupportedAppVersion: string;
  backupDirectory: string;
  suggestedActions: string[];
};

export type BackendStartupStatus = {
  state: "starting" | "ready" | "error";
  phase: BackendStartupPhase;
  message: string;
  elapsed_seconds: number;
  error: string | null;
  error_detail?: DatabaseRequiresNewerAppDetail | null;
};

export type BackendStatus =
  | {
      state: "starting";
      phase: Exclude<BackendStartupPhase, "ready" | "error">;
      message: string;
      elapsedSeconds: number;
      slowStartup: boolean;
      verySlowStartup: boolean;
    }
  | { state: "restarting"; code: number | null; signal: NodeJS.Signals | null }
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
      databaseError?: BackendDatabaseError;
    };

export type UpdateDownloadMode = "differential" | "full";

export type UpdateDownloadProgress = {
  percent: number;
  transferredBytes: number;
  totalBytes: number;
  remainingBytes: number;
  bytesPerSecond: number;
  remainingSeconds: number | null;
  mode: UpdateDownloadMode;
  fallbackFromDifferential?: boolean;
};

export type UpdateStatus =
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
  | ({ state: "downloading"; version: string; nextVersion: string } & UpdateDownloadProgress)
  | ({ state: "slow_download_offered"; version: string; nextVersion: string; fullDownloadBytes?: number } & UpdateDownloadProgress)
  | { state: "downloaded_pending_install"; version: string; nextVersion: string; fullDownloadBytes?: number }
  | { state: "installing"; version: string; nextVersion: string }
  | { state: "error"; version: string; message: string };

export type MaterialOpenResult =
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

export type StartupAtLoginStatus = {
  supported: boolean;
  enabled: boolean;
  message?: string;
};

export type AgentSupportState =
  | "not_enabled"
  | "installing"
  | "enabled"
  | "needs_repair"
  | "updating"
  | "unsupported";

export type AgentSupportStatus = {
  supported: boolean;
  state: AgentSupportState;
  message: string;
  onboardingPending: boolean;
  cliCommand: string;
  cliPath: string;
  skillPath: string;
  appVersion: string;
  requiresAgentRestart: boolean;
};
