import { beforeEach, describe, expect, it, vi } from "vitest";
import {
  downloadDesktopUpdate,
  getDesktopAppVersion,
  isDesktopApp,
  openDesktopExternalUrl,
  switchDesktopUpdateToFullDownload,
} from "@/lib/desktopApi";

describe("desktopApi", () => {
  beforeEach(() => {
    Reflect.deleteProperty(window, "autoEmailSender");
  });

  it("detects browser mode", () => {
    expect(isDesktopApp()).toBe(false);
  });

  it("reads desktop app version", async () => {
    window.autoEmailSender = buildDesktopApi();

    expect(isDesktopApp()).toBe(true);
    await expect(getDesktopAppVersion()).resolves.toBe("0.1.0");
  });

  it("passes update download mode to the desktop bridge", async () => {
    const downloadUpdate = vi.fn(async () => ({
      state: "downloading" as const,
      version: "0.1.0",
      nextVersion: "0.1.1",
      percent: 0,
      transferredBytes: 0,
      totalBytes: 100,
      remainingBytes: 100,
      bytesPerSecond: 0,
      remainingSeconds: null,
      mode: "full" as const,
    }));
    window.autoEmailSender = buildDesktopApi({ downloadUpdate });

    await downloadDesktopUpdate("full");

    expect(downloadUpdate).toHaveBeenCalledWith({ mode: "full" });
  });

  it("opens external URLs through the desktop bridge", async () => {
    const openExternalUrl = vi.fn(async () => undefined);
    window.autoEmailSender = buildDesktopApi({ openExternalUrl });

    await openDesktopExternalUrl("https://github.com/JunieXD/AutoEmailSender/releases/tag/v2.4.0");

    expect(openExternalUrl).toHaveBeenCalledWith(
      "https://github.com/JunieXD/AutoEmailSender/releases/tag/v2.4.0",
    );
  });

  it("switches to full download through the desktop bridge", async () => {
    const switchToFullDownload = vi.fn(async () => ({
      state: "downloading" as const,
      version: "0.1.0",
      nextVersion: "0.1.1",
      percent: 0,
      transferredBytes: 0,
      totalBytes: 100,
      remainingBytes: 100,
      bytesPerSecond: 0,
      remainingSeconds: null,
      mode: "full" as const,
    }));
    window.autoEmailSender = buildDesktopApi({ switchToFullDownload });

    await switchDesktopUpdateToFullDownload();

    expect(switchToFullDownload).toHaveBeenCalled();
  });
});

function buildDesktopApi(overrides: Partial<NonNullable<typeof window.autoEmailSender>> = {}) {
  return {
    backendBaseUrl: "http://127.0.0.1:48123",
    getVersion: async () => "0.1.0",
    checkForUpdate: async () => ({ state: "not_available" as const, version: "0.1.0" }),
    downloadUpdate: async () => ({ state: "not_available" as const, version: "0.1.0" }),
    switchToFullDownload: async () => ({ state: "not_available" as const, version: "0.1.0" }),
    quitAndInstall: async () => undefined,
    onUpdateStatus: () => () => undefined,
    ...overrides,
  };
}
