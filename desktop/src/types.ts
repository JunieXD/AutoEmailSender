import type {
  DesktopAgentIntegrationId,
  DesktopAgentIntegrationState,
  DesktopAgentIntegrationStatus,
  DesktopAgentSupportState,
  DesktopAgentSupportStatus,
  DesktopBackendConnection,
  DesktopBackendDatabaseError,
  DesktopBackendStartupPhase,
  DesktopBackendStatus,
  DesktopCommunityShareSaveResult,
  DesktopMaterialOpenResult,
  DesktopStartupAtLoginStatus,
  DesktopUpdateDownloadMode,
  DesktopUpdateDownloadProgress,
  DesktopUpdateStatus,
} from "../../contracts/desktop-ipc.js";

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
  DesktopCommunityShareSaveResult,
  DesktopMaterialOpenResult,
  DesktopStartupAtLoginStatus,
  DesktopUpdateDownloadMode,
  DesktopUpdateDownloadProgress,
  DesktopUpdateStatus,
};

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

export type BackendExit = {
  code: number | null;
  signal: NodeJS.Signals | null;
};

export type BackendExitHandler = (exit: BackendExit) => void;

export type DatabaseRequiresNewerAppDetail = {
  code: "DATABASE_REQUIRES_NEWER_APP";
  message: string;
  current_app_version: string;
  minimum_supported_app_version: string;
  backup_directory: string;
  suggested_actions: string[];
};

export type BackendStartupStatus = {
  state: "starting" | "ready" | "error";
  phase: BackendStartupPhase;
  message: string;
  elapsed_seconds: number;
  error: string | null;
  error_detail?: DatabaseRequiresNewerAppDetail | null;
};

export type BackendConnection = DesktopBackendConnection;
export type BackendStartupPhase = DesktopBackendStartupPhase;
export type BackendDatabaseError = DesktopBackendDatabaseError;
export type BackendStatus = DesktopBackendStatus;
export type UpdateDownloadMode = DesktopUpdateDownloadMode;
export type UpdateDownloadProgress = DesktopUpdateDownloadProgress;
export type UpdateStatus = DesktopUpdateStatus;
export type MaterialOpenResult = DesktopMaterialOpenResult;
export type CommunityShareSaveResult = DesktopCommunityShareSaveResult;
export type StartupAtLoginStatus = DesktopStartupAtLoginStatus;
export type AgentSupportState = DesktopAgentSupportState;
export type AgentIntegrationId = DesktopAgentIntegrationId;
export type AgentIntegrationState = DesktopAgentIntegrationState;
export type AgentIntegrationStatus = DesktopAgentIntegrationStatus;
export type AgentSupportStatus = DesktopAgentSupportStatus;
