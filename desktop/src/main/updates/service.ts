import { app, BrowserWindow, ipcMain } from "electron";
import { createRequire } from "node:module";
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { checkForMacSparkleUpdates, startMacSparkle } from "./sparkle.js";
import { DESKTOP_IPC_CHANNELS } from "../../contracts/channels.js";
import type {
  DesktopUpdateDownloadMode as UpdateDownloadMode,
  DesktopUpdateDownloadProgress as UpdateDownloadProgress,
  DesktopUpdateStatus as UpdateStatus,
} from "../../../../contracts/desktop-ipc.js";

const require = createRequire(import.meta.url);
const electronUpdater = require("electron-updater") as typeof import("electron-updater");
const builderUtilRuntime = require("builder-util-runtime") as typeof import("builder-util-runtime");

let currentStatus: UpdateStatus = { state: "idle", version: "0.0.0" };
const BYTES_PER_KIB = 1024;
const SLOW_CHECK_START_SECONDS = 10;
const SLOW_REMAINING_SECONDS = 180;
const FULL_DOWNLOAD_FALLBACK_TOLERANCE_BYTES = 1024 * 1024;
const UPDATE_CHECK_RETRY_DELAY_MS = 1_000;
const RETRYABLE_UPDATE_CHECK_ERROR_PATTERNS = [
  "connection error",
  "eai_again",
  "econnrefused",
  "econnreset",
  "enotfound",
  "etimedout",
  "err_connection_closed",
  "err_connection_reset",
  "err_http2_protocol_error",
  "err_http2_server_refused_stream",
  "err_network_changed",
  "network offline",
];
let activeDownloadMode: UpdateDownloadMode = "differential";
let currentDownloadToken: import("builder-util-runtime").CancellationToken | null = null;
let currentDownloadStartedAtMs = 0;
let slowDownloadAlreadyOffered = false;
let activeNextVersion: string | null = null;
let activeFullDownloadBytes: number | undefined;
let pendingInstallVersion: string | null = null;
let activeUpdateCheck: Promise<UpdateStatus> | null = null;
let updateCheckOwnsErrorReporting = false;

export type StartupUpdateMode = "disabled" | "sparkle" | "electron-updater";

type DownloadStatusPayload = {
  version: string;
  nextVersion: string;
} & UpdateDownloadProgress;

type ElectronReleaseNote = {
  version?: string | null;
  note?: string | null;
};

export function formatDownloadProgress(percent: number): number {
  return Math.round(percent * 10) / 10;
}

export function formatByteSize(bytes: number): string {
  if (bytes < BYTES_PER_KIB) {
    return `${bytes} B`;
  }

  const kib = bytes / BYTES_PER_KIB;
  if (kib < BYTES_PER_KIB) {
    return `${kib.toFixed(1)} KB`;
  }

  return `${(kib / BYTES_PER_KIB).toFixed(1)} MB`;
}

export function estimateRemainingSeconds(remainingBytes: number, bytesPerSecond: number): number | null {
  if (bytesPerSecond <= 0) {
    return null;
  }

  return Math.ceil(remainingBytes / bytesPerSecond);
}

export function shouldOfferFullDownload(input: {
  elapsedSeconds: number;
  remainingSeconds: number | null;
  alreadyOffered: boolean;
}): boolean {
  return (
    !input.alreadyOffered &&
    input.elapsedSeconds >= SLOW_CHECK_START_SECONDS &&
    input.remainingSeconds !== null &&
    input.remainingSeconds > SLOW_REMAINING_SECONDS
  );
}

export function isLikelyFullDownloadFallback(input: {
  requestedMode: UpdateDownloadMode;
  progressTotalBytes: number;
  fullDownloadBytes?: number;
}): boolean {
  if (
    input.requestedMode !== "differential" ||
    typeof input.fullDownloadBytes !== "number" ||
    input.fullDownloadBytes <= 0 ||
    input.progressTotalBytes <= 0
  ) {
    return false;
  }

  return (
    Math.abs(input.progressTotalBytes - input.fullDownloadBytes) <=
    FULL_DOWNLOAD_FALLBACK_TOLERANCE_BYTES
  );
}

export function normalizeReleaseNotes(
  releaseNotes: string | ElectronReleaseNote[] | null | undefined,
): string | undefined {
  if (typeof releaseNotes === "string") {
    const trimmed = releaseNotes.trim();
    return trimmed ? trimmed : undefined;
  }

  if (!Array.isArray(releaseNotes)) {
    return undefined;
  }

  const sections = releaseNotes
    .map((entry) => {
      const note = entry.note?.trim();
      if (!note) {
        return "";
      }
      const version = entry.version?.trim();
      return version ? `## v${version.replace(/^v/, "")}\n\n${note}` : note;
    })
    .filter(Boolean);

  return sections.length ? sections.join("\n\n") : undefined;
}

export function supportsElectronUpdaterActions(platform: NodeJS.Platform): boolean {
  return platform !== "darwin";
}

export function buildUpdateErrorStatus(input: { version: string; error: unknown }): UpdateStatus {
  return {
    state: "error",
    version: input.version,
    message: input.error instanceof Error ? input.error.message : String(input.error),
  };
}

export function isRetryableUpdateCheckError(error: unknown): boolean {
  const message = error instanceof Error ? error.message : String(error);
  const normalizedMessage = message.toLowerCase();
  return RETRYABLE_UPDATE_CHECK_ERROR_PATTERNS.some((pattern) =>
    normalizedMessage.includes(pattern),
  );
}

export async function retryUpdateCheckOnce<T>(
  checkForUpdates: () => Promise<T>,
  waitForRetry: (milliseconds: number) => Promise<void> = wait,
): Promise<T> {
  try {
    return await checkForUpdates();
  } catch (error) {
    if (!isRetryableUpdateCheckError(error)) {
      throw error;
    }
    await waitForRetry(UPDATE_CHECK_RETRY_DELAY_MS);
    return checkForUpdates();
  }
}

export function buildProgressStatus(progress: {
  percent: number;
  transferred: number;
  total: number;
  bytesPerSecond: number;
}): UpdateDownloadProgress {
  const remainingBytes = Math.max(progress.total - progress.transferred, 0);
  const fallbackFromDifferential = isLikelyFullDownloadFallback({
    requestedMode: activeDownloadMode,
    progressTotalBytes: progress.total,
    fullDownloadBytes: activeFullDownloadBytes,
  });
  return {
    percent: formatDownloadProgress(progress.percent),
    transferredBytes: progress.transferred,
    totalBytes: progress.total,
    remainingBytes,
    bytesPerSecond: progress.bytesPerSecond,
    remainingSeconds: estimateRemainingSeconds(remainingBytes, progress.bytesPerSecond),
    mode: fallbackFromDifferential ? "full" : activeDownloadMode,
    ...(fallbackFromDifferential ? { fallbackFromDifferential: true } : {}),
  };
}

function createUpdatePayload(progress: UpdateDownloadProgress): DownloadStatusPayload {
  return {
    version: app.getVersion(),
    nextVersion: activeNextVersion ?? app.getVersion(),
    ...progress,
  };
}

function getCurrentDownloadElapsedSeconds(): number {
  if (currentDownloadStartedAtMs === 0) {
    return 0;
  }
  return Math.floor((Date.now() - currentDownloadStartedAtMs) / 1000);
}

function getFullDownloadBytes(updateInfo: { files?: Array<{ size?: number }> }): number | undefined {
  return updateInfo.files?.[0]?.size;
}

function getUpdateCacheRoot(): string {
  return path.join(app.getPath("userData"), "updates");
}

function getElectronUpdaterCacheRoot(): string {
  const baseCachePath =
    process.platform === "win32"
      ? process.env.LOCALAPPDATA ?? path.join(os.homedir(), "AppData", "Local")
      : process.platform === "darwin"
        ? path.join(os.homedir(), "Library", "Caches")
        : process.env.XDG_CACHE_HOME ?? path.join(os.homedir(), ".cache");
  return path.join(baseCachePath, app.getName());
}

async function clearStaleUpdateCache(nextVersion: string): Promise<void> {
  await fs.mkdir(getUpdateCacheRoot(), { recursive: true });
  const updaterCacheRoot = getElectronUpdaterCacheRoot();
  await fs.rm(path.join(updaterCacheRoot, "pending"), { recursive: true, force: true });
  await fs.writeFile(path.join(getUpdateCacheRoot(), "latest-version.txt"), nextVersion, "utf8");
}

async function startUpdateDownload(getWindow: () => BrowserWindow | null, mode: UpdateDownloadMode): Promise<UpdateStatus> {
  const autoUpdater = getAutoUpdater();
  currentDownloadToken?.cancel();
  currentDownloadToken = new builderUtilRuntime.CancellationToken();
  activeDownloadMode = mode;
  slowDownloadAlreadyOffered = false;
  currentDownloadStartedAtMs = Date.now();
  autoUpdater.disableDifferentialDownload = mode === "full";
  const token = currentDownloadToken;
  if (token === null) {
    return currentStatus;
  }
  await autoUpdater.downloadUpdate(token);
  return currentStatus;
}

export function registerUpdateIpc(getWindow: () => BrowserWindow | null): void {
  currentStatus = { state: "idle", version: app.getVersion() };
  if (process.platform !== "darwin") {
    registerElectronUpdaterEvents(getWindow);
  }

  ipcMain.handle(DESKTOP_IPC_CHANNELS.updateCheck, async () => {
    if (!app.isPackaged) {
      currentStatus = { state: "not_available", version: app.getVersion() };
      return currentStatus;
    }
    if (process.platform === "darwin") {
      publish(getWindow, { state: "checking", version: app.getVersion() });
      try {
        checkForMacSparkleUpdates();
        const status: UpdateStatus = { state: "idle", version: app.getVersion() };
        publish(getWindow, status);
        return status;
      } catch (error) {
        const status = buildUpdateErrorStatus({ version: app.getVersion(), error });
        publish(getWindow, status);
        return status;
      }
    }
    if (pendingInstallVersion !== null && pendingInstallVersion !== app.getVersion()) {
      currentStatus = {
        state: "downloaded_pending_install",
        version: app.getVersion(),
        nextVersion: pendingInstallVersion,
        fullDownloadBytes: activeFullDownloadBytes,
      };
      return currentStatus;
    }
    return checkForElectronUpdates(getWindow);
  });

  ipcMain.handle(DESKTOP_IPC_CHANNELS.updateDownload, async (_event, options?: { mode?: UpdateDownloadMode }) => {
    if (!isAutomaticUpdateActionSupported()) {
      return currentStatus;
    }
    return startUpdateDownload(getWindow, options?.mode ?? "differential");
  });

  ipcMain.handle(DESKTOP_IPC_CHANNELS.updateSwitchToFullDownload, async () => {
    if (!isAutomaticUpdateActionSupported()) {
      return currentStatus;
    }
    return startUpdateDownload(getWindow, "full");
  });

  ipcMain.handle(DESKTOP_IPC_CHANNELS.updateQuitAndInstall, () => {
    if (!isAutomaticUpdateActionSupported()) {
      return currentStatus;
    }
    const nextVersion = pendingInstallVersion ?? activeNextVersion ?? app.getVersion();
    publish(getWindow, {
      state: "installing",
      version: app.getVersion(),
      nextVersion,
    });
    getAutoUpdater().quitAndInstall(false, true);
  });
}

export function checkForUpdatesOnStartup(getWindow: () => BrowserWindow | null): void {
  const mode = resolveStartupUpdateMode({
    isPackaged: app.isPackaged,
    platform: process.platform,
  });
  if (mode === "disabled") {
    return;
  }
  if (mode === "sparkle") {
    try {
      startMacSparkle();
    } catch (error) {
      console.error("Failed to start Sparkle updater.", error);
    }
    return;
  }
  setTimeout(() => {
    void checkForElectronUpdates(getWindow);
  }, 3_000);
}

export function resolveStartupUpdateMode(input: {
  isPackaged: boolean;
  platform: NodeJS.Platform;
}): StartupUpdateMode {
  if (!input.isPackaged) {
    return "disabled";
  }
  return input.platform === "darwin" ? "sparkle" : "electron-updater";
}

function isAutomaticUpdateActionSupported(): boolean {
  return app.isPackaged && supportsElectronUpdaterActions(process.platform);
}

function registerElectronUpdaterEvents(getWindow: () => BrowserWindow | null): void {
  const autoUpdater = getAutoUpdater();
  autoUpdater.autoDownload = false;

  autoUpdater.on("checking-for-update", () =>
    publish(getWindow, { state: "checking", version: app.getVersion() }),
  );
  autoUpdater.on("update-available", (info) => {
    activeNextVersion = info.version;
    activeFullDownloadBytes = getFullDownloadBytes(info);
    void clearStaleUpdateCache(info.version);
    publish(getWindow, {
      state: "available",
      version: app.getVersion(),
      nextVersion: info.version,
      fullDownloadBytes: activeFullDownloadBytes,
      releaseNotes: normalizeReleaseNotes(info.releaseNotes),
    });
  });
  autoUpdater.on("update-not-available", () =>
    publish(getWindow, { state: "not_available", version: app.getVersion() }),
  );
  autoUpdater.on("download-progress", (progress) =>
    publishDownloadProgress(getWindow, progress),
  );
  autoUpdater.on("update-downloaded", (info) => {
    pendingInstallVersion = info.version;
    publish(getWindow, {
      state: "downloaded_pending_install",
      version: app.getVersion(),
      nextVersion: info.version,
      fullDownloadBytes: activeFullDownloadBytes,
    });
  });
  autoUpdater.on("error", (error) => {
    if (updateCheckOwnsErrorReporting) {
      return;
    }
    publish(getWindow, {
      state: "error",
      version: app.getVersion(),
      message: error.message,
    });
  });
}

async function checkForElectronUpdates(getWindow: () => BrowserWindow | null): Promise<UpdateStatus> {
  if (activeUpdateCheck !== null) {
    return activeUpdateCheck;
  }

  const check = performElectronUpdateCheck(getWindow);
  activeUpdateCheck = check;
  try {
    return await check;
  } finally {
    if (activeUpdateCheck === check) {
      activeUpdateCheck = null;
    }
  }
}

async function performElectronUpdateCheck(getWindow: () => BrowserWindow | null): Promise<UpdateStatus> {
  updateCheckOwnsErrorReporting = true;
  try {
    await retryUpdateCheckOnce(() => getAutoUpdater().checkForUpdates());
    return currentStatus;
  } catch (error) {
    console.warn("Update check failed.", error);
    const status = buildUpdateErrorStatus({ version: app.getVersion(), error });
    publish(getWindow, status);
    return status;
  } finally {
    updateCheckOwnsErrorReporting = false;
  }
}

function getAutoUpdater(): typeof electronUpdater.autoUpdater {
  return electronUpdater.autoUpdater;
}

function publish(getWindow: () => BrowserWindow | null, status: UpdateStatus): void {
  currentStatus = status;
  getWindow()?.webContents.send(DESKTOP_IPC_CHANNELS.updateStatus, status);
}

function wait(milliseconds: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, milliseconds));
}

function publishDownloadProgress(
  getWindow: () => BrowserWindow | null,
  progress: { percent: number; transferred: number; total: number; bytesPerSecond: number },
): void {
  const normalized = buildProgressStatus(progress);
  const payload = createUpdatePayload(normalized);

  if (
    shouldOfferFullDownload({
      elapsedSeconds: getCurrentDownloadElapsedSeconds(),
      remainingSeconds: normalized.remainingSeconds,
      alreadyOffered: slowDownloadAlreadyOffered,
    })
  ) {
    slowDownloadAlreadyOffered = true;
    publish(getWindow, {
      state: "slow_download_offered",
      ...payload,
      fullDownloadBytes: activeFullDownloadBytes,
    });
    return;
  }

  publish(getWindow, {
    state: "downloading",
    ...payload,
  });
}
