import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  downloadProfessorExport,
  downloadProfessorTemplate,
} from "@/entities/professor/api/professors";
import { exportCrawlerDebugLog } from "@/lib/api/diagnosticsApi";
import { downloadMaterial } from "@/lib/api/materials";
import { updateDesktopBackendBaseUrl } from "@/lib/api/client";

describe("protected desktop file downloads", () => {
  beforeEach(() => {
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
  });

  afterEach(() => {
    Reflect.deleteProperty(window, "autoEmailSender");
    updateDesktopBackendBaseUrl(null);
    vi.restoreAllMocks();
  });

  it.each([
    [
      "professor XLSX export",
      () => downloadProfessorExport("xlsx"),
      "http://127.0.0.1:48123/api/professors/export?format=xlsx",
    ],
    [
      "professor CSV template",
      () => downloadProfessorTemplate("csv"),
      "http://127.0.0.1:48123/api/professors/template?format=csv",
    ],
    [
      "identity material",
      () => downloadMaterial(17),
      "http://127.0.0.1:48123/api/materials/17/download",
    ],
    [
      "crawler debug log",
      () => exportCrawlerDebugLog(42),
      "http://127.0.0.1:48123/api/diagnostics/crawler-debug/42/export",
    ],
  ])("authenticates the %s request", async (_label, download, expectedUrl) => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response("file-content", {
        status: 200,
        headers: { "Content-Type": "application/octet-stream" },
      }),
    );

    const blob = await download();

    expect(await blob.text()).toBe("file-content");
    expect(fetchMock).toHaveBeenCalledWith(
      expectedUrl,
      expect.objectContaining({ headers: expect.any(Headers) }),
    );
    expect(new Headers(fetchMock.mock.calls[0]?.[1]?.headers).get("Authorization")).toBe(
      "Bearer desktop-ui-token",
    );
  });
});
