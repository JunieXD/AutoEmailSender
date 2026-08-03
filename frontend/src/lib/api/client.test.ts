import { beforeEach, describe, expect, it, vi } from "vitest";
import {
  apiFetch,
  apiFetchBlob,
  buildApiPath,
  buildApiUrl,
  updateDesktopBackendBaseUrl,
} from "@/lib/api/client";
import type { DesktopBackendStatus } from "@/types/desktop";

describe("api client desktop base url", () => {
  beforeEach(() => {
    Reflect.deleteProperty(window, "autoEmailSender");
    updateDesktopBackendBaseUrl(null);
    vi.unstubAllGlobals();
  });

  it("uses relative paths in browser mode", () => {
    expect(buildApiPath("/api/ping")).toBe("/api/ping");
    expect(buildApiUrl("/api/ping")).toBe("http://localhost:3000/api/ping");
  });

  it("uses desktop backend base url when preload provides it", () => {
    window.autoEmailSender = {
      backendBaseUrl: "http://127.0.0.1:48123",
      getVersion: async () => "0.1.0",
      checkForUpdate: async () => ({ state: "not_available", version: "0.1.0" }),
      downloadUpdate: async () => ({ state: "not_available", version: "0.1.0" }),
      switchToFullDownload: async () => ({ state: "not_available", version: "0.1.0" }),
      quitAndInstall: async () => undefined,
      onUpdateStatus: () => () => undefined,
    };

    expect(buildApiPath("/api/ping")).toBe("http://127.0.0.1:48123/api/ping");
    expect(buildApiUrl("/api/ping")).toBe("http://127.0.0.1:48123/api/ping");
  });

  it("uses runtime desktop backend base url updates", () => {
    window.autoEmailSender = {
      backendBaseUrl: "http://127.0.0.1:48123",
      getVersion: async () => "0.1.0",
      checkForUpdate: async () => ({ state: "not_available", version: "0.1.0" }),
      downloadUpdate: async () => ({ state: "not_available", version: "0.1.0" }),
      switchToFullDownload: async () => ({ state: "not_available", version: "0.1.0" }),
      quitAndInstall: async () => undefined,
      onUpdateStatus: () => () => undefined,
    };

    updateDesktopBackendBaseUrl("http://127.0.0.1:48124");

    expect(buildApiPath("/api/ping")).toBe("http://127.0.0.1:48124/api/ping");
    expect(buildApiUrl("/api/ping")).toBe("http://127.0.0.1:48124/api/ping");
  });

  it("waits for a desktop backend ready event before fetching without an initial base url", async () => {
    let backendStatusCallback: ((status: DesktopBackendStatus) => void) | undefined;
    const fetchMock = vi.fn<typeof fetch>().mockImplementation(
      async () => new Response(JSON.stringify({ status: "ok" })),
    );
    vi.stubGlobal("fetch", fetchMock);
    window.autoEmailSender = {
      getVersion: async () => "0.1.0",
      checkForUpdate: async () => ({ state: "not_available", version: "0.1.0" }),
      downloadUpdate: async () => ({ state: "not_available", version: "0.1.0" }),
      switchToFullDownload: async () => ({ state: "not_available", version: "0.1.0" }),
      quitAndInstall: async () => undefined,
      onBackendStatus: (callback) => {
        backendStatusCallback = callback as typeof backendStatusCallback;
        return () => undefined;
      },
      onUpdateStatus: () => () => undefined,
    };

    const request = apiFetch<{ status: string }>("/health");
    await Promise.resolve();

    expect(fetchMock).not.toHaveBeenCalled();

    backendStatusCallback?.({
      state: "ready",
      baseUrl: "http://127.0.0.1:48124",
      phase: "ready",
      message: "系统已准备就绪",
      elapsedSeconds: 1,
    });

    await expect(request).resolves.toEqual({ status: "ok" });
    expect(fetchMock).toHaveBeenCalledWith(
      "http://127.0.0.1:48124/health",
      expect.objectContaining({ headers: expect.any(Headers) }),
    );
    const requestHeaders = new Headers(fetchMock.mock.calls[0]?.[1]?.headers);
    expect(requestHeaders.get("Content-Type")).toBe("application/json");
  });

  it("keeps waiting while desktop backend status is starting", async () => {
    let backendStatusCallback: ((status: DesktopBackendStatus) => void) | undefined;
    const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(
      new Response(JSON.stringify({ status: "ok" })),
    );
    vi.stubGlobal("fetch", fetchMock);
    window.autoEmailSender = {
      getVersion: async () => "0.1.0",
      checkForUpdate: async () => ({ state: "not_available", version: "0.1.0" }),
      downloadUpdate: async () => ({ state: "not_available", version: "0.1.0" }),
      switchToFullDownload: async () => ({ state: "not_available", version: "0.1.0" }),
      quitAndInstall: async () => undefined,
      onBackendStatus: (callback) => {
        backendStatusCallback = callback;
        return () => undefined;
      },
      onUpdateStatus: () => () => undefined,
    };

    const request = apiFetch<{ status: string }>("/health");
    await Promise.resolve();

    backendStatusCallback?.({
      state: "starting",
      phase: "migrating_database",
      message: "正在检查和升级本地数据",
      elapsedSeconds: 10,
      slowStartup: false,
      verySlowStartup: false,
    });
    await Promise.resolve();

    expect(fetchMock).not.toHaveBeenCalled();

    backendStatusCallback?.({
      state: "ready",
      baseUrl: "http://127.0.0.1:48124",
      phase: "ready",
      message: "系统已准备就绪",
      elapsedSeconds: 12,
    });

    await expect(request).resolves.toEqual({ status: "ok" });
  });

  it("keeps waiting while desktop backend status is restarting", async () => {
    let backendStatusCallback: ((status: DesktopBackendStatus) => void) | undefined;
    const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(
      new Response(JSON.stringify({ status: "ok" })),
    );
    vi.stubGlobal("fetch", fetchMock);
    window.autoEmailSender = {
      getVersion: async () => "0.1.0",
      checkForUpdate: async () => ({ state: "not_available", version: "0.1.0" }),
      downloadUpdate: async () => ({ state: "not_available", version: "0.1.0" }),
      switchToFullDownload: async () => ({ state: "not_available", version: "0.1.0" }),
      quitAndInstall: async () => undefined,
      onBackendStatus: (callback) => {
        backendStatusCallback = callback;
        return () => undefined;
      },
      onUpdateStatus: () => () => undefined,
    };

    const request = apiFetch<{ status: string }>("/health");
    await Promise.resolve();

    backendStatusCallback?.({ state: "restarting", code: null, signal: null });
    await Promise.resolve();

    expect(fetchMock).not.toHaveBeenCalled();

    backendStatusCallback?.({
      state: "ready",
      baseUrl: "http://127.0.0.1:48124",
      phase: "ready",
      message: "系统已准备就绪",
      elapsedSeconds: 12,
    });

    await expect(request).resolves.toEqual({ status: "ok" });
  });

  it("uses a user-facing message when desktop backend startup fails", async () => {
    let backendStatusCallback: ((status: DesktopBackendStatus) => void) | undefined;
    window.autoEmailSender = {
      getVersion: async () => "0.1.0",
      checkForUpdate: async () => ({ state: "not_available", version: "0.1.0" }),
      downloadUpdate: async () => ({ state: "not_available", version: "0.1.0" }),
      switchToFullDownload: async () => ({ state: "not_available", version: "0.1.0" }),
      quitAndInstall: async () => undefined,
      onBackendStatus: (callback) => {
        backendStatusCallback = callback;
        return () => undefined;
      },
      onUpdateStatus: () => () => undefined,
    };

    const request = apiFetch<{ status: string }>("/health");
    await Promise.resolve();

    backendStatusCallback?.({
      state: "error",
      phase: "error",
      message: "Backend readiness check timed out: INFO",
      elapsedSeconds: 10,
      detail: "database is locked",
    });

    await expect(request).rejects.toThrow("系统准备失败");
  });

  it("uses database version guidance when desktop backend requires a newer app", async () => {
    let backendStatusCallback: ((status: DesktopBackendStatus) => void) | undefined;
    window.autoEmailSender = {
      getVersion: async () => "2.3.0",
      checkForUpdate: async () => ({ state: "not_available", version: "2.3.0" }),
      downloadUpdate: async () => ({ state: "not_available", version: "2.3.0" }),
      switchToFullDownload: async () => ({ state: "not_available", version: "2.3.0" }),
      quitAndInstall: async () => undefined,
      onBackendStatus: (callback) => {
        backendStatusCallback = callback;
        return () => undefined;
      },
      onUpdateStatus: () => () => undefined,
    };

    const request = apiFetch<{ status: string }>("/health");
    await Promise.resolve();

    backendStatusCallback?.({
      state: "error",
      phase: "error",
      message: "系统准备失败",
      elapsedSeconds: 10,
      detail: "当前数据由较新版本创建，当前版本无法直接打开。",
      databaseError: {
        code: "DATABASE_REQUIRES_NEWER_APP",
        message: "当前数据由较新版本创建，当前版本无法直接打开。",
        currentAppVersion: "2.3.0",
        minimumSupportedAppVersion: "2.4.0",
        backupDirectory: "C:\\Users\\Alice\\AppData\\Roaming\\AutoEmailSender\\backups\\schema",
        suggestedActions: ["安装 2.4.0 或更高版本继续使用", "如需回退，请从升级前备份恢复数据库"],
      },
    });

    await expect(request).rejects.toThrow("当前数据需要 AutoEmailSender 2.4.0 或更高版本");
    await expect(request).rejects.toThrow("备份位置：C:\\Users\\Alice\\AppData\\Roaming\\AutoEmailSender\\backups\\schema");
  });

  it("adds the current desktop UI token to every request", async () => {
    let accessToken = "first-ui-token";
    const fetchMock = vi.fn<typeof fetch>().mockImplementation(
      async () => new Response(JSON.stringify({ status: "ok" })),
    );
    vi.stubGlobal("fetch", fetchMock);
    window.autoEmailSender = {
      backendBaseUrl: "http://127.0.0.1:48123",
      getBackendAccessToken: () => accessToken,
      getVersion: async () => "0.1.0",
      checkForUpdate: async () => ({ state: "not_available", version: "0.1.0" }),
      downloadUpdate: async () => ({ state: "not_available", version: "0.1.0" }),
      switchToFullDownload: async () => ({ state: "not_available", version: "0.1.0" }),
      quitAndInstall: async () => undefined,
      onUpdateStatus: () => () => undefined,
    };

    await apiFetch("/api/ping", { headers: [["X-Test", "one"]] });
    accessToken = "rotated-ui-token";
    await apiFetch("/api/ping");

    expect(new Headers(fetchMock.mock.calls[0]?.[1]?.headers).get("Authorization")).toBe(
      "Bearer first-ui-token",
    );
    expect(new Headers(fetchMock.mock.calls[0]?.[1]?.headers).get("X-Test")).toBe("one");
    expect(new Headers(fetchMock.mock.calls[1]?.[1]?.headers).get("Authorization")).toBe(
      "Bearer rotated-ui-token",
    );
  });

  it("downloads binary responses with the current desktop UI token", async () => {
    const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(
      new Response("xlsx-content", {
        status: 200,
        headers: {
          "Content-Type":
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        },
      }),
    );
    vi.stubGlobal("fetch", fetchMock);
    window.autoEmailSender = {
      backendBaseUrl: "http://127.0.0.1:48123",
      getBackendAccessToken: () => "desktop-ui-token",
      getVersion: async () => "0.1.0",
      checkForUpdate: async () => ({ state: "not_available", version: "0.1.0" }),
      downloadUpdate: async () => ({ state: "not_available", version: "0.1.0" }),
      switchToFullDownload: async () => ({ state: "not_available", version: "0.1.0" }),
      quitAndInstall: async () => undefined,
      onUpdateStatus: () => () => undefined,
    };

    const blob = await apiFetchBlob(
      "/api/community-mentors/share-package",
      undefined,
      { professor_ids: "1,2" },
    );

    expect(await blob.text()).toBe("xlsx-content");
    expect(fetchMock).toHaveBeenCalledWith(
      "http://127.0.0.1:48123/api/community-mentors/share-package?professor_ids=1%2C2",
      expect.objectContaining({ headers: expect.any(Headers) }),
    );
    expect(new Headers(fetchMock.mock.calls[0]?.[1]?.headers).get("Authorization")).toBe(
      "Bearer desktop-ui-token",
    );
  });

  it("does not overwrite an explicitly supplied Authorization header", async () => {
    const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(
      new Response(JSON.stringify({ status: "ok" })),
    );
    vi.stubGlobal("fetch", fetchMock);
    window.autoEmailSender = {
      backendBaseUrl: "http://127.0.0.1:48123",
      getBackendAccessToken: () => "desktop-ui-token",
      getVersion: async () => "0.1.0",
      checkForUpdate: async () => ({ state: "not_available", version: "0.1.0" }),
      downloadUpdate: async () => ({ state: "not_available", version: "0.1.0" }),
      switchToFullDownload: async () => ({ state: "not_available", version: "0.1.0" }),
      quitAndInstall: async () => undefined,
      onUpdateStatus: () => () => undefined,
    };

    await apiFetch("/api/ping", {
      headers: new Headers({ Authorization: "Bearer explicit-token" }),
    });

    expect(new Headers(fetchMock.mock.calls[0]?.[1]?.headers).get("Authorization")).toBe(
      "Bearer explicit-token",
    );
  });
});
