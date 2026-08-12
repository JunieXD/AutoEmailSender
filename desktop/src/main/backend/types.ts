import type {
  DesktopBackendDatabaseError,
  DesktopBackendStartupPhase,
  DesktopBackendStatus,
} from "../../../../contracts/desktop-ipc.js";

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
  runtimeId: string;
  role?: BackendRole;
  apiPid?: number;
  workerGeneration?: string;
  uiAccessToken?: string;
  agentAccessToken?: string;
};

export type BackendMode = "combined" | "split";
export type BackendRole = "api" | "worker" | "combined";

export type BackendController = {
  baseUrl: string;
  backendPid: number;
  backendStartedAt: string;
  workerPid?: number;
  workerStartedAt?: string;
  workerGeneration?: string;
  mode: BackendMode;
  runtimeId: string;
  uiAccessToken: string;
  agentAccessToken: string;
  getRuntimeInfo: () => Promise<BackendRuntimeInfo>;
  ready: Promise<void>;
  onStatus: (handler: (status: DesktopBackendStatus) => void) => () => void;
  notifySystemSuspend?: () => void;
  notifySystemResume?: () => void;
  stop: () => Promise<void>;
};

export type BackendRuntimeInfo = {
  runtime_id: string;
  protocol_version: string;
  app_version: string;
  backend_pid: number;
  desktop_pid: number;
  state: "starting" | "ready" | "error";
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
  phase: DesktopBackendStartupPhase;
  message: string;
  elapsed_seconds: number;
  error: string | null;
  error_detail?: DatabaseRequiresNewerAppDetail | null;
};

export type BackendDatabaseError = DesktopBackendDatabaseError;
export type BackendStartupPhase = DesktopBackendStartupPhase;
export type BackendStatus = DesktopBackendStatus;
