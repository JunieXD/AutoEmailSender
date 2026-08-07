import { describe, expect, it, vi } from "vitest";
import {
  createExternalUrlService,
  parseExternalNavigationUrl,
} from "../src/main/shell/external-url.js";

describe("desktop external url service", () => {
  it("opens http urls with the system default browser", async () => {
    const openExternal = vi.fn().mockResolvedValue(undefined);
    const openElectronWindow = vi.fn();
    const service = createExternalUrlService({
      dependencies: {
        openExternal,
        openElectronWindow,
      },
    });

    await service.openExternalUrl("https://example.edu/faculty/zhang");

    expect(openExternal).toHaveBeenCalledWith("https://example.edu/faculty/zhang");
    expect(openElectronWindow).not.toHaveBeenCalled();
  });

  it("falls back to an Electron window only after system browser opening fails", async () => {
    const openExternal = vi.fn().mockRejectedValue(new Error("xdg-open missing"));
    const openElectronWindow = vi.fn();
    const service = createExternalUrlService({
      dependencies: {
        openExternal,
        openElectronWindow,
      },
    });

    await service.openExternalUrl("https://example.edu/faculty/zhang");

    expect(openExternal).toHaveBeenCalledWith("https://example.edu/faculty/zhang");
    expect(openElectronWindow).toHaveBeenCalledWith("https://example.edu/faculty/zhang");
  });

  it("rejects non-web protocols", async () => {
    const openExternal = vi.fn().mockResolvedValue(undefined);
    const service = createExternalUrlService({ dependencies: { openExternal } });

    await expect(service.openExternalUrl("file:///C:/secret.txt")).rejects.toThrow(
      "Only http and https URLs can be opened externally.",
    );
    expect(openExternal).not.toHaveBeenCalled();
  });

  it("identifies cross-origin top-level navigation as external", () => {
    expect(
      parseExternalNavigationUrl(
        "https://example.edu/faculty/zhang",
        "file:///Applications/AutoEmailSender/index.html",
      ),
    ).toBe("https://example.edu/faculty/zhang");
    expect(
      parseExternalNavigationUrl(
        "https://example.edu/faculty/zhang",
        "http://127.0.0.1:5173/tasks",
      ),
    ).toBe("https://example.edu/faculty/zhang");
  });

  it("keeps same-origin development navigation inside the main window", () => {
    expect(
      parseExternalNavigationUrl(
        "http://127.0.0.1:5173/tasks?tab=running",
        "http://127.0.0.1:5173/",
      ),
    ).toBeNull();
  });
});
