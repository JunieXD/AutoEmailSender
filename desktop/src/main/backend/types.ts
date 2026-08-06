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
  uiAccessToken?: string;
  agentAccessToken?: string;
};

export type BackendController = {
  baseUrl: string;
  uiAccessToken: string;
  agentAccessToken: string;
  ready: Promise<void>;
  onStatus: (handler: (status: DesktopBackendStatus) => void) => () => void;
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
  phase: DesktopBackendStartupPhase;
  message: string;
  elapsed_seconds: number;
  error: string | null;
  error_detail?: DatabaseRequiresNewerAppDetail | null;
};

export type BackendDatabaseError = DesktopBackendDatabaseError;
export type BackendStartupPhase = DesktopBackendStartupPhase;
export type BackendStatus = DesktopBackendStatus;
