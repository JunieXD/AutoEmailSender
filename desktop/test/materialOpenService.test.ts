import { Readable } from "node:stream";
import { describe, expect, it, vi } from "vitest";
import {
  createMaterialOpenService,
  MATERIAL_OPEN_COPY_TTL_MS,
  parseMaterialId,
  sanitizeCopyFilename,
} from "../src/main/files/material-open.js";

const okResponse = (body: string, headers: Record<string, string> = {}) =>
  new Response(Readable.toWeb(Readable.from([body])) as ReadableStream, {
    status: 200,
    headers,
  });

describe("desktop material open service", () => {
  it("rejects invalid material ids before contacting backend", async () => {
    const fetchMock = vi.fn<typeof fetch>();
    const service = createMaterialOpenService({
      getBackendBaseUrl: () => "http://127.0.0.1:8010",
      userDataPath: "C:\\Users\\Alice\\AppData\\Roaming\\auto-email-sender-desktop",
      dependencies: { fetch: fetchMock },
    });

    await expect(service.openMaterial("C:\\secret.pdf")).resolves.toMatchObject({
      ok: false,
      code: "MaterialOpenInvalidId",
    });
    expect(fetchMock).not.toHaveBeenCalled();
    expect(parseMaterialId(1)).toBe(1);
    expect(parseMaterialId(0)).toBeNull();
  });

  it("creates a readonly temp copy using the backend download filename", async () => {
    const writtenPaths: string[] = [];
    const chmodMock = vi.fn().mockResolvedValue(undefined);
    const openPathMock = vi.fn().mockResolvedValue("");
    const fetchMock = vi.fn<typeof fetch>().mockResolvedValueOnce(
      okResponse("document content", {
        "content-disposition": "attachment; filename*=UTF-8''resume.docx",
      }),
    );
    const service = createMaterialOpenService({
      getBackendBaseUrl: () => "http://127.0.0.1:8010",
      getBackendAccessToken: () => "ui-access-token",
      userDataPath: "C:\\Users\\Alice\\AppData\\Roaming\\auto-email-sender-desktop",
      dependencies: {
        fetch: fetchMock,
        mkdir: vi.fn().mockResolvedValue(undefined),
        readdir: vi.fn().mockRejectedValue(new Error("missing")),
        chmod: chmodMock,
        openPath: openPathMock,
        createWriteStream: ((filePath: string) => {
          writtenPaths.push(filePath);
          return new WritableStream();
        }) as never,
      },
    });

    await expect(
      service.openMaterial({ materialId: 42, originalFilename: "renderer.pdf" }),
    ).resolves.toEqual({ ok: true });
    expect(writtenPaths[0]).toContain("material-open-copies");
    expect(writtenPaths[0]).toMatch(/42-\d+-resume\.docx$/);
    expect(chmodMock).toHaveBeenCalledWith(writtenPaths[0], 0o444);
    expect(openPathMock).toHaveBeenCalledWith(writtenPaths[0]);
    expect(fetchMock).toHaveBeenCalledWith(
      "http://127.0.0.1:8010/api/materials/42/download",
      { headers: { Authorization: "Bearer ui-access-token" } },
    );
  });

  it("reports missing backend files as not found", async () => {
    const service = createMaterialOpenService({
      getBackendBaseUrl: () => "http://127.0.0.1:8010",
      userDataPath: "C:\\Data",
      dependencies: {
        fetch: vi.fn<typeof fetch>().mockResolvedValue(new Response(null, { status: 404 })),
      },
    });

    await expect(service.openMaterial(7)).resolves.toMatchObject({
      ok: false,
      code: "MaterialOpenNotFound",
    });
  });

  it("reports system open failures", async () => {
    const service = createMaterialOpenService({
      getBackendBaseUrl: () => "http://127.0.0.1:8010",
      userDataPath: "C:\\Data",
      dependencies: {
        fetch: vi.fn<typeof fetch>().mockResolvedValue(okResponse("content")),
        mkdir: vi.fn().mockResolvedValue(undefined),
        readdir: vi.fn().mockRejectedValue(new Error("missing")),
        chmod: vi.fn().mockResolvedValue(undefined),
        openPath: vi.fn().mockResolvedValue("No app associated"),
        createWriteStream: (() => new WritableStream()) as never,
      },
    });

    await expect(service.openMaterial(9)).resolves.toMatchObject({
      ok: false,
      code: "MaterialOpenSystemFailed",
    });
  });

  it("cleans expired copies without failing on locked files", async () => {
    const rmMock = vi.fn().mockRejectedValue(new Error("locked"));
    const service = createMaterialOpenService({
      getBackendBaseUrl: () => "http://127.0.0.1:8010",
      userDataPath: "C:\\Data",
      dependencies: {
        fetch: vi.fn<typeof fetch>().mockResolvedValue(okResponse("content")),
        mkdir: vi.fn().mockResolvedValue(undefined),
        readdir: vi.fn().mockResolvedValue(["old.pdf"]),
        stat: vi.fn().mockResolvedValue({ mtimeMs: 1 }),
        rm: rmMock,
        now: () => MATERIAL_OPEN_COPY_TTL_MS + 2,
        chmod: vi.fn().mockResolvedValue(undefined),
        openPath: vi.fn().mockResolvedValue(""),
        createWriteStream: (() => new WritableStream()) as never,
      },
    });

    await expect(service.openMaterial(10)).resolves.toEqual({ ok: true });
    expect(rmMock).toHaveBeenCalled();
  });

  it("sanitizes temp filenames and keeps safe extensions", () => {
    expect(sanitizeCopyFilename(3, "..\\evil/name?.pptx")).toMatch(
      /^3-\d+-name_\.pptx$/,
    );
  });
});
