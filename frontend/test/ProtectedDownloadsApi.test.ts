import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  downloadProfessorExport,
  downloadProfessorTemplate,
} from "@/entities/professor/api/professors";
import { downloadCommunitySharePackage } from "@/entities/community-mentor/api/communityMentors";
import { exportCrawlerDebugLog } from "@/lib/api/diagnosticsApi";
import { downloadMaterial } from "@/lib/api/materials";
import { updateDesktopBackendBaseUrl } from "@/lib/api/client";

let originalCreateObjectUrl: PropertyDescriptor | undefined;
let originalRevokeObjectUrl: PropertyDescriptor | undefined;

describe("protected desktop file downloads", () => {
  beforeEach(() => {
    originalCreateObjectUrl = Object.getOwnPropertyDescriptor(URL, "createObjectURL");
    originalRevokeObjectUrl = Object.getOwnPropertyDescriptor(URL, "revokeObjectURL");
    Object.defineProperty(URL, "createObjectURL", {
      configurable: true,
      value: vi.fn(() => "blob:protected-download"),
    });
    Object.defineProperty(URL, "revokeObjectURL", {
      configurable: true,
      value: vi.fn(),
    });
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
    if (originalCreateObjectUrl) {
      Object.defineProperty(URL, "createObjectURL", originalCreateObjectUrl);
    } else {
      Reflect.deleteProperty(URL, "createObjectURL");
    }
    if (originalRevokeObjectUrl) {
      Object.defineProperty(URL, "revokeObjectURL", originalRevokeObjectUrl);
    } else {
      Reflect.deleteProperty(URL, "revokeObjectURL");
    }
    Reflect.deleteProperty(window, "autoEmailSender");
    updateDesktopBackendBaseUrl(null);
    vi.restoreAllMocks();
  });

  it.each([
    [
      "professor XLSX export",
      () => downloadProfessorExport("xlsx"),
      "http://127.0.0.1:48123/api/professors/export?format=xlsx",
      "professors_export.xlsx",
    ],
    [
      "professor CSV template",
      () => downloadProfessorTemplate("csv"),
      "http://127.0.0.1:48123/api/professors/template?format=csv",
      "professors_import_template.csv",
    ],
    [
      "identity material",
      () => downloadMaterial(17, "resume.pdf"),
      "http://127.0.0.1:48123/api/materials/17/download",
      "resume.pdf",
    ],
    [
      "crawler debug log",
      () => exportCrawlerDebugLog(42),
      "http://127.0.0.1:48123/api/diagnostics/crawler-debug/42/export",
      "crawl-job-42.jsonl",
    ],
  ])("authenticates the %s request", async (_label, download, expectedUrl, filename) => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response("file-content", {
        status: 200,
        headers: { "Content-Type": "application/octet-stream" },
      }),
    );

    const clickSpy = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => undefined);
    const link = document.createElement("a");
    const createElementSpy = vi.spyOn(document, "createElement").mockReturnValue(link);
    await download();

    expect(fetchMock).toHaveBeenCalledWith(
      expectedUrl,
      expect.objectContaining({ headers: expect.any(Headers) }),
    );
    expect(new Headers(fetchMock.mock.calls[0]?.[1]?.headers).get("Authorization")).toBe(
      "Bearer desktop-ui-token",
    );
    expect(clickSpy).toHaveBeenCalledOnce();
    expect(link.download).toBe(filename);
    expect(URL.createObjectURL).toHaveBeenCalledWith(expect.any(Blob));
    expect(URL.revokeObjectURL).toHaveBeenCalledWith("blob:protected-download");
    expect(document.querySelector("a[download]")).toBeNull();
    createElementSpy.mockRestore();
  });

  it("posts the complete community share selection without putting IDs in the URL", async () => {
    const professorIds = Array.from({ length: 82 }, (_, index) => index + 1);
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response("xlsx-content", {
        status: 200,
        headers: {
          "Content-Type":
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        },
      }),
    );

    const blob = await downloadCommunitySharePackage(professorIds);

    expect(await blob.text()).toBe("xlsx-content");
    expect(fetchMock).toHaveBeenCalledWith(
      "http://127.0.0.1:48123/api/community-mentors/share-package",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({ professor_ids: professorIds }),
        headers: expect.any(Headers),
      }),
    );
    expect(new Headers(fetchMock.mock.calls[0]?.[1]?.headers).get("Authorization")).toBe(
      "Bearer desktop-ui-token",
    );
  });
});
