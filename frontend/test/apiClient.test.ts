import { afterEach, describe, expect, it, vi } from "vitest";
import {
  ApiError,
  apiFetch,
  buildApiPath,
  buildApiUrl,
  updateDesktopBackendBaseUrl,
} from "@/lib/api/client";
import { clearDiagnosticEvents, getDiagnosticEvents } from "@/lib/diagnostics";
import type { DesktopBackendStatus } from "@/types/desktop";

describe("api client", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    updateDesktopBackendBaseUrl(null);
    clearDiagnosticEvents();
  });

  it("builds encoded API paths while skipping empty query values", () => {
    expect(
      buildApiPath("/api/professors", {
        keyword: "大模型 导师",
        page: 2,
        archived: "",
        identity_id: null,
        llm_profile_id: undefined,
      }),
    ).toBe("/api/professors?keyword=%E5%A4%A7%E6%A8%A1%E5%9E%8B+%E5%AF%BC%E5%B8%88&page=2");
  });

  it("builds absolute API urls from the current browser origin", () => {
    expect(buildApiUrl("/api/ping")).toBe("http://localhost:3000/api/ping");
  });

  it("parses successful JSON responses and sends JSON content type by default", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ status: "ok" }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );

    await expect(apiFetch("/api/ping", { method: "POST", body: JSON.stringify({ ok: true }) })).resolves.toEqual({
      status: "ok",
    });
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/ping",
      expect.objectContaining({
        method: "POST",
        headers: { "Content-Type": "application/json" },
      }),
    );
    expect(getDiagnosticEvents()).toEqual([
      expect.objectContaining({
        level: "info",
        category: "api",
        eventName: "api.request_succeeded",
        data: expect.objectContaining({
          method: "POST",
          path: "/api/ping",
          status: 200,
          durationMs: expect.any(Number),
        }),
      }),
    ]);
  });

  it("does not force JSON content type when the request body is FormData", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(null, { status: 204 }));
    const formData = new FormData();
    formData.append("file", new Blob(["content"]), "resume.txt");

    await expect(apiFetch<void>("/api/materials", { method: "POST", body: formData })).resolves.toBeUndefined();
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/materials",
      expect.objectContaining({
        headers: {},
      }),
    );
  });

  it("throws ApiError with backend detail, message, text, or fallback error messages", async () => {
    const cases = [
      [new Response(JSON.stringify({ detail: "未找到身份配置" }), { status: 404 }), "未找到身份配置"],
      [new Response(JSON.stringify({ message: "模型不可用" }), { status: 400 }), "模型不可用"],
      [new Response("服务暂不可用", { status: 503 }), "服务暂不可用"],
      [new Response("", { status: 500 }), "请求失败"],
    ] as const;

    for (const [response, message] of cases) {
      vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(response);

      await expect(apiFetch("/api/fail")).rejects.toMatchObject<ApiError>({
        name: "Error",
        status: response.status,
        message,
      });
    }
  });

  it("records failed HTTP responses without sensitive query details", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ detail: "Unauthorized" }), {
        status: 401,
      }),
    );

    await expect(apiFetch("/api/professors", undefined, { token: "secret", page: 1 })).rejects.toMatchObject<ApiError>(
      {
        status: 401,
        message: "Unauthorized",
      },
    );

    expect(getDiagnosticEvents()).toEqual([
      expect.objectContaining({
        level: "error",
        category: "api",
        eventName: "api.request_failed",
        data: expect.objectContaining({
          method: "GET",
          path: "/api/professors",
          status: 401,
          durationMs: expect.any(Number),
          message: "Unauthorized",
        }),
      }),
    ]);
    expect(JSON.stringify(getDiagnosticEvents()[0].data)).not.toContain("secret");
  });

  it("keeps raw HTTP error messages for ApiError but sanitizes diagnostics", async () => {
    const rawMessage =
      "Failed with token=secret-token api_key=secret-key password=hunter2 secret=hidden " +
      "authorization=Bearer abc cookie=sid=abc smtpPassword=mail-secret " +
      "callback=https://example.test/callback?token=secret-token#session";
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ detail: rawMessage }), {
        status: 400,
      }),
    );

    await expect(apiFetch("/api/send")).rejects.toMatchObject<ApiError>({
      status: 400,
      message: rawMessage,
    });

    const diagnosticData = JSON.stringify(getDiagnosticEvents()[0].data);
    expect(diagnosticData).not.toContain("secret-token");
    expect(diagnosticData).not.toContain("secret-key");
    expect(diagnosticData).not.toContain("hunter2");
    expect(diagnosticData).not.toContain("Bearer abc");
    expect(diagnosticData).not.toContain("sid=abc");
    expect(diagnosticData).not.toContain("mail-secret");
    expect(diagnosticData).not.toContain("?token=");
    expect(diagnosticData).not.toContain("#session");
  });

  it("records fetch errors and rethrows the original error", async () => {
    const networkError = new TypeError(
      "Failed to fetch https://example.test/api?token=secret-token#debug with password=hunter2",
    );
    vi.spyOn(globalThis, "fetch").mockRejectedValue(networkError);

    await expect(apiFetch("/api/professors?token=secret")).rejects.toBe(networkError);

    expect(getDiagnosticEvents()).toEqual([
      expect.objectContaining({
        level: "error",
        category: "api",
        eventName: "api.request_errored",
        data: expect.objectContaining({
          method: "GET",
          path: "/api/professors",
          durationMs: expect.any(Number),
          errorType: "TypeError",
          error: "Failed to fetch https://example.test/api with [Redacted]",
        }),
      }),
    ]);
    const diagnosticData = JSON.stringify(getDiagnosticEvents()[0].data);
    expect(diagnosticData).not.toContain("secret-token");
    expect(diagnosticData).not.toContain("?token=");
    expect(diagnosticData).not.toContain("#debug");
    expect(diagnosticData).not.toContain("hunter2");
  });

  it("retries a desktop fetch once after a transient backend reconnect", async () => {
    let backendStatusCallback: ((status: DesktopBackendStatus) => void) | undefined;
    let backendBaseUrl = "http://127.0.0.1:48120";
    window.autoEmailSender = {
      backendBaseUrl,
      getBackendBaseUrl: () => backendBaseUrl,
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
    updateDesktopBackendBaseUrl("http://127.0.0.1:48120");

    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockRejectedValueOnce(new TypeError("Failed to fetch"))
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ status: "ok" }), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        }),
      );

    const request = apiFetch<{ status: string }>("/api/ping");
    await Promise.resolve();

    backendBaseUrl = "http://127.0.0.1:48121";
    backendStatusCallback?.({
      state: "ready",
      baseUrl: backendBaseUrl,
      phase: "ready",
      message: "系统已准备就绪",
      elapsedSeconds: 1,
    });

    await expect(request).resolves.toEqual({ status: "ok" });
    expect(fetchMock).toHaveBeenNthCalledWith(
      1,
      "http://127.0.0.1:48120/api/ping",
      expect.any(Object),
    );
    expect(fetchMock).toHaveBeenNthCalledWith(
      2,
      "http://127.0.0.1:48121/api/ping",
      expect.any(Object),
    );
  });

  it("uses readable FastAPI validation detail messages", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(
        JSON.stringify({
          detail: [{ loc: ["body", "email"], msg: "value is not a valid email address" }],
        }),
        { status: 422 },
      ),
    );

    await expect(apiFetch("/api/send")).rejects.toMatchObject<ApiError>({
      status: 422,
      message: "body.email: value is not a valid email address",
    });
    expect(getDiagnosticEvents()[0]).toEqual(
      expect.objectContaining({
        eventName: "api.request_failed",
        data: expect.objectContaining({
          message: "body.email: value is not a valid email address",
        }),
      }),
    );
  });

  it("strips FastAPI value error prefixes before surfacing validation messages", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(
        JSON.stringify({
          detail: [
            {
              type: "value_error",
              loc: ["body", "start_url"],
              msg: "Value error, URL is not allowed",
            },
          ],
        }),
        { status: 422 },
      ),
    );

    await expect(apiFetch("/api/crawl-jobs")).rejects.toMatchObject<ApiError>({
      status: 422,
      message: "URL is not allowed",
    });
  });
});
