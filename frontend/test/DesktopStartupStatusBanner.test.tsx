import { render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it } from "vitest";
import { DesktopStartupStatusBanner } from "@/components/organisms/DesktopStartupStatusBanner";
import { DesktopBackendProvider } from "@/context/DesktopBackendContext";
import type { DesktopBackendStatus } from "@/types/desktop";

describe("DesktopStartupStatusBanner", () => {
  beforeEach(() => {
    Reflect.deleteProperty(window, "autoEmailSender");
    window.localStorage.clear();
    window.sessionStorage.clear();
  });

  it("shows the current startup phase while the desktop backend is starting", async () => {
    window.autoEmailSender = createDesktopApi({
      state: "starting",
      phase: "migrating_database",
      message: "正在检查和升级本地数据",
      elapsedSeconds: 12,
      slowStartup: false,
      verySlowStartup: false,
    });

    render(
      <DesktopBackendProvider>
        <DesktopStartupStatusBanner />
      </DesktopBackendProvider>,
    );

    expect(await screen.findByText("正在检查和升级本地数据")).toBeInTheDocument();
    expect(screen.getByText(/首次启动或升级约需 1–3 分钟/)).toBeInTheDocument();
  });

  it("hides the startup banner after the desktop backend is ready", async () => {
    let callback: ((status: DesktopBackendStatus) => void) | undefined;
    window.autoEmailSender = createDesktopApi(
      {
        state: "starting",
        phase: "starting",
        message: "正在启动系统服务",
        elapsedSeconds: 0,
        slowStartup: false,
        verySlowStartup: false,
      },
      (registeredCallback) => {
        callback = registeredCallback;
      },
    );

    render(
      <DesktopBackendProvider>
        <DesktopStartupStatusBanner />
      </DesktopBackendProvider>,
    );

    expect(await screen.findByText("正在启动系统服务")).toBeInTheDocument();

    callback?.({
      state: "ready",
      baseUrl: "http://127.0.0.1:48124",
      phase: "ready",
      message: "系统已准备就绪",
      elapsedSeconds: 4,
    });

    await waitFor(() => {
      expect(screen.queryByText("正在启动系统服务")).not.toBeInTheDocument();
    });
  });
});

function createDesktopApi(
  initialStatus: DesktopBackendStatus,
  onSubscribe?: (callback: (status: DesktopBackendStatus) => void) => void,
): NonNullable<Window["autoEmailSender"]> {
  return {
    getVersion: async () => "0.1.0",
    checkForUpdate: async () => ({ state: "not_available", version: "0.1.0" }),
    downloadUpdate: async () => ({ state: "not_available", version: "0.1.0" }),
    switchToFullDownload: async () => ({ state: "not_available", version: "0.1.0" }),
    quitAndInstall: async () => undefined,
    onBackendStatus: (callback) => {
      onSubscribe?.(callback);
      queueMicrotask(() => callback(initialStatus));
      return () => undefined;
    },
    onUpdateStatus: () => () => undefined,
  };
}
