import { describe, expect, it, vi } from "vitest";
import { createExternalUrlService } from "../src/main/shell/external-url.js";

describe("desktop external url service", () => {
  it("opens http urls with the system default browser", async () => {
    const openExternal = vi.fn().mockResolvedValue(undefined);
    const openElectronWindow = vi.fn();
    const service = createExternalUrlService({
      dependencies: {
        openExternal,
        openElectronWindow,
        shouldUseSystemExternalOpener: () => true,
      },
    });

    await service.openExternalUrl("https://example.edu/faculty/zhang");

    expect(openExternal).toHaveBeenCalledWith("https://example.edu/faculty/zhang");
    expect(openElectronWindow).not.toHaveBeenCalled();
  });

  it("uses an Electron window when the system external opener is unavailable", async () => {
    const openExternal = vi.fn().mockResolvedValue(undefined);
    const openElectronWindow = vi.fn();
    const service = createExternalUrlService({
      dependencies: {
        openExternal,
        openElectronWindow,
        shouldUseSystemExternalOpener: () => false,
      },
    });

    await service.openExternalUrl("https://example.edu/faculty/zhang");

    expect(openExternal).not.toHaveBeenCalled();
    expect(openElectronWindow).toHaveBeenCalledWith("https://example.edu/faculty/zhang");
  });

  it("falls back to an Electron window when system browser opening fails", async () => {
    const openExternal = vi.fn().mockRejectedValue(new Error("xdg-open missing"));
    const openElectronWindow = vi.fn();
    const service = createExternalUrlService({
      dependencies: {
        openExternal,
        openElectronWindow,
        shouldUseSystemExternalOpener: () => true,
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
});
