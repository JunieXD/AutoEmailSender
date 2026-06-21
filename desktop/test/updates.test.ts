import { readFileSync } from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";
import {
  estimateRemainingSeconds,
  formatByteSize,
  formatDownloadProgress,
  isLikelyFullDownloadFallback,
  normalizeReleaseNotes,
  shouldOfferFullDownload,
} from "../src/updates.js";

describe("update helpers", () => {
  it("rounds download progress to one decimal place", () => {
    expect(formatDownloadProgress(47.236)).toBe(47.2);
  });

  it("formats byte sizes for progress display", () => {
    expect(formatByteSize(0)).toBe("0 B");
    expect(formatByteSize(1536)).toBe("1.5 KB");
    expect(formatByteSize(5 * 1024 * 1024)).toBe("5.0 MB");
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
    const source = readFileSync(path.resolve("src", "updates.ts"), "utf8");

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
    const source = readFileSync(path.resolve("src", "updates.ts"), "utf8");

    expect(source).toContain("CancellationToken");
    expect(source).toContain("currentDownloadToken");
    expect(source).toContain("currentDownloadToken?.cancel()");
  });

  it("supports full download mode through electron-updater", () => {
    const source = readFileSync(path.resolve("src", "updates.ts"), "utf8");

    expect(source).toContain("disableDifferentialDownload");
    expect(source).toContain("startUpdateDownload");
    expect(source).toContain('"full"');
  });

  it("tracks pending install versions without auto-installing", () => {
    const source = readFileSync(path.resolve("src", "updates.ts"), "utf8");

    expect(source).toContain("pendingInstallVersion");
    expect(source).toContain("downloaded_pending_install");
    expect(source).not.toContain("await quitAndInstall");
  });

  it("cleans stale update cache when a different version is available", () => {
    const source = readFileSync(path.resolve("src", "updates.ts"), "utf8");

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
    const source = readFileSync(path.resolve("src", "updates.ts"), "utf8");
    const types = readFileSync(path.resolve("src", "types.ts"), "utf8");

    expect(types).toContain("releaseNotes?: string");
    expect(source).toContain("releaseNotes: normalizeReleaseNotes(info.releaseNotes)");
  });
});
