import { render, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { DesktopBackendProvider } from "./DesktopBackendContext";
import type { DesktopBackendStatus } from "@/types/desktop";

const updateDesktopBackendBaseUrlMock = vi.hoisted(() => vi.fn());

vi.mock("@/lib/api/client", () => ({
  updateDesktopBackendBaseUrl: updateDesktopBackendBaseUrlMock,
}));

describe("DesktopBackendProvider", () => {
  beforeEach(() => {
    updateDesktopBackendBaseUrlMock.mockClear();
    Reflect.deleteProperty(window, "autoEmailSender");
  });

  it("clears the cached backend base url while restarting and restores it on ready", async () => {
    let backendStatusCallback: ((status: DesktopBackendStatus) => void) | undefined;

    window.autoEmailSender = buildDesktopApi({
      onBackendStatus: (callback) => {
        backendStatusCallback = callback;
        queueMicrotask(() =>
          callback({
            state: "starting",
            phase: "starting",
            message: "正在启动系统服务",
            elapsedSeconds: 0,
            slowStartup: false,
            verySlowStartup: false,
          }),
        );
        return () => undefined;
      },
    });

    render(<DesktopBackendProvider><div /></DesktopBackendProvider>);

    await waitFor(() => {
      expect(updateDesktopBackendBaseUrlMock).toHaveBeenCalledWith(null);
    });

    backendStatusCallback?.({
      state: "ready",
      baseUrl: "http://127.0.0.1:48121",
      phase: "ready",
      message: "系统已准备就绪",
      elapsedSeconds: 1,
    });

    expect(updateDesktopBackendBaseUrlMock).toHaveBeenLastCalledWith(
      "http://127.0.0.1:48121",
    );

    backendStatusCallback?.({
      state: "restarting",
      code: null,
      signal: null,
    });

    expect(updateDesktopBackendBaseUrlMock).toHaveBeenLastCalledWith(null);

    backendStatusCallback?.({
      state: "degraded",
      baseUrl: "http://127.0.0.1:48121",
      reason: "background_unavailable",
      message: "后台服务暂时不可用",
    });

    expect(updateDesktopBackendBaseUrlMock).toHaveBeenLastCalledWith(
      "http://127.0.0.1:48121",
    );
  });
});

function buildDesktopApi(
  overrides: Partial<NonNullable<typeof window.autoEmailSender>> = {},
): NonNullable<typeof window.autoEmailSender> {
  return {
    backendBaseUrl: "http://127.0.0.1:48120",
    getBackendBaseUrl: () => "http://127.0.0.1:48120",
    getVersion: async () => "0.1.0",
    checkForUpdate: async () => ({ state: "not_available" as const, version: "0.1.0" }),
    downloadUpdate: async () => ({ state: "not_available" as const, version: "0.1.0" }),
    switchToFullDownload: async () => ({ state: "not_available" as const, version: "0.1.0" }),
    quitAndInstall: async () => undefined,
    onUpdateStatus: () => () => undefined,
    ...overrides,
  };
}
