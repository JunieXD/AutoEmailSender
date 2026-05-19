export {};

export type DesktopUpdateDownloadMode = "differential" | "full";

export type DesktopUpdateDownloadProgress = {
  percent: number;
  transferredBytes: number;
  totalBytes: number;
  remainingBytes: number;
  bytesPerSecond: number;
  remainingSeconds: number | null;
  mode: DesktopUpdateDownloadMode;
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
    };

export type DesktopStartupAtLoginStatus = {
  supported: boolean;
  enabled: boolean;
  message?: string;
};

declare global {
  interface Window {
    autoEmailSender?: {
      backendBaseUrl?: string;
      getBackendBaseUrl?: () => string | undefined;
      getVersion: () => Promise<string>;
      selectProfessorImportFile?: () => Promise<{
        name: string;
        type: string;
        data: ArrayBuffer;
      } | null>;
      openMaterial?: (request: { materialId: number }) => Promise<DesktopMaterialOpenResult>;
      getStartupAtLoginStatus?: () => Promise<DesktopStartupAtLoginStatus>;
      setStartupAtLoginEnabled?: (enabled: boolean) => Promise<DesktopStartupAtLoginStatus>;
      checkForUpdate: () => Promise<DesktopUpdateStatus>;
      downloadUpdate: (options?: { mode?: DesktopUpdateDownloadMode }) => Promise<DesktopUpdateStatus>;
      switchToFullDownload: () => Promise<DesktopUpdateStatus>;
      quitAndInstall: () => Promise<void>;
      onBackendStatus?: (callback: (status: DesktopBackendStatus) => void) => () => void;
      onUpdateStatus: (callback: (status: DesktopUpdateStatus) => void) => () => void;
    };
  }
}

