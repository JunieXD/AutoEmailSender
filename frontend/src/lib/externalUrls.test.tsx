import { afterEach, describe, expect, it, vi } from "vitest";
import {
  normalizeExternalHttpUrl,
  openExternalHttpUrl,
} from "./externalUrls";

describe("externalUrls", () => {
  afterEach(() => {
    Reflect.deleteProperty(window, "autoEmailSender");
    vi.restoreAllMocks();
  });

  it("normalizes only http and https URLs", () => {
    expect(normalizeExternalHttpUrl(" https://example.edu/profile ")).toBe(
      "https://example.edu/profile",
    );
    expect(normalizeExternalHttpUrl("http://example.edu/profile")).toBe(
      "http://example.edu/profile",
    );
    expect(normalizeExternalHttpUrl("mailto:li@example.edu")).toBeNull();
    expect(normalizeExternalHttpUrl("/relative/path")).toBeNull();
    expect(normalizeExternalHttpUrl("")).toBeNull();
  });

  it("opens with the browser window when desktop external opening is unavailable", () => {
    const openWindow = vi.spyOn(window, "open").mockImplementation(() => null);

    openExternalHttpUrl("https://example.edu/profile");

    expect(openWindow).toHaveBeenCalledWith(
      "https://example.edu/profile",
      "_blank",
      "noopener,noreferrer",
    );
  });

  it("uses the desktop default browser and falls back to the browser window", async () => {
    const openWindow = vi.spyOn(window, "open").mockImplementation(() => null);
    window.autoEmailSender = {
      getVersion: async () => "0.1.0",
      openExternalUrl: vi.fn().mockRejectedValue(new Error("xdg-open missing")),
      checkForUpdate: vi.fn(),
      downloadUpdate: vi.fn(),
      switchToFullDownload: vi.fn(),
      quitAndInstall: vi.fn(),
      onUpdateStatus: () => () => undefined,
    };

    openExternalHttpUrl(" https://example.edu/profile ");

    expect(window.autoEmailSender.openExternalUrl).toHaveBeenCalledWith(
      "https://example.edu/profile",
    );
    await vi.waitFor(() => {
      expect(openWindow).toHaveBeenCalledWith(
        "https://example.edu/profile",
        "_blank",
        "noopener,noreferrer",
      );
    });
  });
});
