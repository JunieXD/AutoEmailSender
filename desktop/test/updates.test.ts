import { readFileSync } from "node:fs";
import path from "node:path";
import { describe, expect, it, vi } from "vitest";
import {
  buildUpdateErrorStatus,
  estimateRemainingSeconds,
  formatByteSize,
  formatDownloadProgress,
  isLikelyFullDownloadFallback,
  isRetryableUpdateCheckError,
  normalizeReleaseNotes,
  resolveStartupUpdateMode,
  retryUpdateCheckOnce,
  shouldOfferFullDownload,
  supportsElectronUpdaterActions,
} from "../src/main/updates/service.js";

const readUpdateServiceSource = () =>
  readFileSync(path.resolve("src", "main", "updates", "service.ts"), "utf8");

const readDesktopApplicationSource = () =>
  readFileSync(path.resolve("src", "main", "bootstrap", "application.ts"), "utf8");

describe("update helpers", () => {
  it("rounds download progress to one decimal place", () => {
    expect(formatDownloadProgress(47.236)).toBe(47.2);
  });

  it("formats byte sizes for progress display", () => {
    expect(formatByteSize(0)).toBe("0 B");
    expect(formatByteSize(1536)).toBe("1.5 KB");
    expect(formatByteSize(5 * 1024 * 1024)).toBe("5.0 MB");
  });

  it("builds update error status from thrown errors", () => {
    expect(buildUpdateErrorStatus({ version: "2.3.8", error: new Error("network offline") })).toEqual({
      state: "error",
      version: "2.3.8",
      message: "network offline",
    });
  });

  it("builds update error status from non-error throws", () => {
    expect(buildUpdateErrorStatus({ version: "2.3.8", error: "bad json" })).toEqual({
      state: "error",
      version: "2.3.8",
      message: "bad json",
    });
  });

  it("recognizes transient update check errors", () => {
    expect(isRetryableUpdateCheckError(new Error("net::ERR_HTTP2_SERVER_REFUSED_STREAM"))).toBe(true);
    expect(isRetryableUpdateCheckError(new Error("invalid update metadata"))).toBe(false);
  });

  it("retries a transient update check once", async () => {
    const checkForUpdates = vi
      .fn<() => Promise<string>>()
      .mockRejectedValueOnce(new Error("net::ERR_HTTP2_SERVER_REFUSED_STREAM"))
      .mockResolvedValueOnce("available");
    const waitForRetry = vi.fn(async (_milliseconds: number) => undefined);

    await expect(retryUpdateCheckOnce(checkForUpdates, waitForRetry)).resolves.toBe("available");
    expect(checkForUpdates).toHaveBeenCalledTimes(2);
    expect(waitForRetry).toHaveBeenCalledOnce();
    expect(waitForRetry).toHaveBeenCalledWith(1_000);
  });

  it("does not retry non-network update errors", async () => {
    const checkForUpdates = vi.fn(async () => {
      throw new Error("invalid update metadata");
    });
    const waitForRetry = vi.fn(async (_milliseconds: number) => undefined);

    await expect(retryUpdateCheckOnce(checkForUpdates, waitForRetry)).rejects.toThrow(
      "invalid update metadata",
    );
    expect(checkForUpdates).toHaveBeenCalledOnce();
    expect(waitForRetry).not.toHaveBeenCalled();
  });

  it("keeps electron-updater actions on Windows and delegates macOS actions to Sparkle", () => {
    expect(supportsElectronUpdaterActions("darwin")).toBe(false);
    expect(supportsElectronUpdaterActions("win32")).toBe(true);
  });

  it("selects the packaged startup updater for Windows and macOS", () => {
    expect(resolveStartupUpdateMode({ isPackaged: true, platform: "win32" })).toBe(
      "electron-updater",
    );
    expect(resolveStartupUpdateMode({ isPackaged: true, platform: "darwin" })).toBe(
      "sparkle",
    );
    expect(resolveStartupUpdateMode({ isPackaged: false, platform: "win32" })).toBe(
      "disabled",
    );
    expect(resolveStartupUpdateMode({ isPackaged: false, platform: "darwin" })).toBe(
      "disabled",
    );
  });

  it("starts update checks after the desktop page loads without waiting for backend readiness", () => {
    const source = readDesktopApplicationSource();
    const createWindowSource = source.slice(
      source.indexOf("async function createWindow"),
      source.indexOf("async function startDesktopBackend"),
    );
    const publishBackendReadySource = source.slice(
      source.indexOf("function publishBackendReady"),
      source.indexOf("async function removeAgentRuntime"),
    );
    const pageLoadIndex = createWindowSource.indexOf(
      "await mainWindow.loadURL(pathToFileURL(indexPath).toString());",
    );
    const updateCheckIndex = createWindowSource.indexOf(
      "checkForUpdatesOnStartup(() => mainWindow);",
    );

    expect(pageLoadIndex).toBeGreaterThan(-1);
    expect(updateCheckIndex).toBeGreaterThan(pageLoadIndex);
    expect(publishBackendReadySource).not.toContain("checkForUpdatesOnStartup");
  });

  it("estimates remaining seconds from remaining bytes and speed", () => {
    expect(estimateRemainingSeconds(30 * 1024 * 1024, 512 * 1024)).toBe(60);
    expect(estimateRemainingSeconds(1024, 0)).toBe(null);
  });

  it("offers full download only after the slow threshold is exceeded", () => {
    expect(
      shouldOfferFullDownload({
        elapsedSeconds: 9,
        remainingSeconds: 600,
        alreadyOffered: false,
      }),
    ).toBe(false);
    expect(
      shouldOfferFullDownload({
        elapsedSeconds: 40,
        remainingSeconds: 181,
        alreadyOffered: false,
      }),
    ).toBe(true);
    expect(
      shouldOfferFullDownload({
        elapsedSeconds: 40,
        remainingSeconds: 181,
        alreadyOffered: true,
      }),
    ).toBe(false);
  });

  it("detects electron-updater fallback from differential to full download by progress size", () => {
    expect(
      isLikelyFullDownloadFallback({
        requestedMode: "differential",
        progressTotalBytes: 266_237_904,
        fullDownloadBytes: 266_237_904,
      }),
    ).toBe(true);

    expect(
      isLikelyFullDownloadFallback({
        requestedMode: "differential",
        progressTotalBytes: 1_500_000,
        fullDownloadBytes: 266_237_904,
      }),
    ).toBe(false);

    expect(
      isLikelyFullDownloadFallback({
        requestedMode: "full",
        progressTotalBytes: 266_237_904,
        fullDownloadBytes: 266_237_904,
      }),
    ).toBe(false);
  });

  it("loads electron-updater through CommonJS interop for packaged ESM runtime", () => {
    const source = readUpdateServiceSource();

    expect(source).toContain("createRequire");
    expect(source).not.toContain('import { autoUpdater } from "electron-updater"');
  });

  it("publishes GitHub releases directly instead of drafts", () => {
    const config = readFileSync(path.resolve("electron-builder.yml"), "utf8");

    expect(config).toContain("releaseType: release");
  });

  it("loads release notes from the generated markdown file", () => {
    const config = readFileSync(path.resolve("electron-builder.yml"), "utf8");

    expect(config).toContain("releaseInfo:");
    expect(config).toContain("releaseNotesFile: release-notes.md");
  });

  it("uses cancellation tokens for switchable update downloads", () => {
    const source = readUpdateServiceSource();

    expect(source).toContain("CancellationToken");
    expect(source).toContain("currentDownloadToken");
    expect(source).toContain("currentDownloadToken?.cancel()");
  });

  it("supports full download mode through electron-updater", () => {
    const source = readUpdateServiceSource();

    expect(source).toContain("disableDifferentialDownload");
    expect(source).toContain("startUpdateDownload");
    expect(source).toContain('"full"');
  });

  it("guards electron-updater IPC actions on Sparkle platforms", () => {
    const source = readUpdateServiceSource();

    expect(source).toContain("function isAutomaticUpdateActionSupported()");
    expect(source.match(/if \(!isAutomaticUpdateActionSupported\(\)\)/g)).toHaveLength(3);
  });

  it("uses Sparkle instead of the GitHub manual-download branch on macOS", () => {
    const source = readUpdateServiceSource();

    expect(source).toContain("checkForMacSparkleUpdates()");
    expect(source).toContain("startMacSparkle()");
    expect(source).toContain('if (process.platform !== "darwin")');
    expect(source).not.toContain("GITHUB_LATEST_RELEASE_API_URL");
    expect(source).not.toContain("buildManualDownloadStatus");
  });

  it("tracks pending install versions without auto-installing", () => {
    const source = readUpdateServiceSource();

    expect(source).toContain("pendingInstallVersion");
    expect(source).toContain("downloaded_pending_install");
    expect(source).not.toContain("await quitAndInstall");
  });

  it("allows the window close handler to release the app for updater installation", () => {
    const source = readDesktopApplicationSource();
    const listenerStart = source.indexOf('autoUpdater.on("before-quit-for-update"');

    expect(listenerStart).toBeGreaterThan(-1);
    expect(source.slice(listenerStart, listenerStart + 180)).toContain("isQuitting = true");
  });

  it("cleans stale update cache when a different version is available", () => {
    const source = readUpdateServiceSource();

    expect(source).toContain("clearStaleUpdateCache");
    expect(source).toContain('app.getPath("userData")');
    expect(source).toContain("updates");
  });

  it("normalizes electron-updater release notes into markdown text", () => {
    expect(normalizeReleaseNotes("## 更新内容\n\n- 修复问题")).toBe("## 更新内容\n\n- 修复问题");
    expect(
      normalizeReleaseNotes([
        { version: "2.1.6", note: "- 修复公告弹窗高度" },
        { version: "2.1.5", note: "- 优化更新下载" },
      ]),
    ).toBe("## v2.1.6\n\n- 修复公告弹窗高度\n\n## v2.1.5\n\n- 优化更新下载");
    expect(normalizeReleaseNotes(undefined)).toBeUndefined();
  });

  it("adds release notes to the available update status", () => {
    const source = readUpdateServiceSource();
    const types = readFileSync(
      path.resolve("..", "contracts", "desktop-ipc.d.ts"),
      "utf8",
    );

    expect(types).toContain("releaseNotes?: string");
    expect(source).toContain("releaseNotes: normalizeReleaseNotes(info.releaseNotes)");
  });
});
