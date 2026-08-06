import { app, ipcMain, shell } from "electron";
import { createWriteStream } from "node:fs";
import { chmod, copyFile, mkdir, readdir, rm, stat } from "node:fs/promises";
import path from "node:path";
import { pipeline } from "node:stream/promises";
import { DESKTOP_IPC_CHANNELS } from "./contracts/channels.js";
import type { MaterialOpenResult } from "./types.js";

export const MATERIAL_OPEN_COPY_TTL_MS = 24 * 60 * 60 * 1000;
export const MATERIAL_OPEN_IPC_CHANNEL = DESKTOP_IPC_CHANNELS.materialOpen;

type MaterialOpenDependencies = {
  fetch: typeof fetch;
  openPath: (filePath: string) => Promise<string>;
  copyFile: typeof copyFile;
  chmod: typeof chmod;
  mkdir: typeof mkdir;
  readdir: typeof readdir;
  rm: typeof rm;
  stat: typeof stat;
  now: () => number;
  createWriteStream: typeof createWriteStream;
};

export type MaterialOpenServiceOptions = {
  getBackendBaseUrl: () => string | null | undefined;
  getBackendAccessToken?: () => string | null | undefined;
  userDataPath: string;
  dependencies?: Partial<MaterialOpenDependencies>;
};

type MaterialOpenRequest = {
  materialId: unknown;
};

const defaultDependencies: MaterialOpenDependencies = {
  fetch,
  openPath: (filePath: string) => shell.openPath(filePath),
  copyFile,
  chmod,
  mkdir,
  readdir,
  rm,
  stat,
  now: Date.now,
  createWriteStream,
};

export function createMaterialOpenService(options: MaterialOpenServiceOptions) {
  const dependencies = { ...defaultDependencies, ...options.dependencies };
  const tempDir = path.join(options.userDataPath, "material-open-copies");

  return {
    async openMaterial(request: MaterialOpenRequest | unknown): Promise<MaterialOpenResult> {
      const materialId = getRequestMaterialId(request);
      const parsedMaterialId = parseMaterialId(materialId);
      if (parsedMaterialId === null) {
        return buildError("MaterialOpenInvalidId", "材料 ID 无效");
      }

      const backendBaseUrl = options.getBackendBaseUrl();
      if (!backendBaseUrl) {
        return buildError("MaterialOpenBackendUnavailable", "系统服务尚未就绪，请稍后再试");
      }
      const backendAccessToken = options.getBackendAccessToken?.()?.trim() || undefined;

      await cleanupExpiredCopies(tempDir, dependencies);

      const copyResult = await createReadonlyCopy({
        backendBaseUrl,
        backendAccessToken,
        materialId: parsedMaterialId,
        tempDir,
        dependencies,
      });
      if ("error" in copyResult) {
        return copyResult.error;
      }

      try {
        const openError = await dependencies.openPath(copyResult.copyPath);
        if (openError) {
          return buildError("MaterialOpenSystemFailed", "系统无法打开该材料，请确认已安装可打开此文件类型的应用");
        }
      } catch (error) {
        return buildError("MaterialOpenSystemFailed", "系统无法打开该材料，请确认已安装可打开此文件类型的应用");
      }

      return { ok: true };
    },
  };
}

export function registerMaterialOpenIpc(options: MaterialOpenServiceOptions): void {
  const service = createMaterialOpenService(options);
  ipcMain.handle(MATERIAL_OPEN_IPC_CHANNEL, (_event, request: unknown) =>
    service.openMaterial(request),
  );
}

function getRequestMaterialId(request: MaterialOpenRequest | unknown): unknown {
  if (typeof request === "object" && request !== null && "materialId" in request) {
    return (request as MaterialOpenRequest).materialId;
  }
  return request;
}

export function parseMaterialId(value: unknown): number | null {
  if (typeof value !== "number" || !Number.isInteger(value) || value <= 0) {
    return null;
  }
  return value;
}

export function sanitizeCopyFilename(materialId: number, originalFilename: string): string {
  const parsedPath = path.parse(originalFilename);
  const safeStem = (parsedPath.name || "material")
    .replace(/[<>:"/\\|?*\u0000-\u001F]/g, "_")
    .replace(/\.+/g, ".")
    .trim()
    .slice(0, 80) || "material";
  const safeExtension = parsedPath.ext.replace(/[^a-zA-Z0-9.]/g, "").slice(0, 20);
  return `${materialId}-${Date.now()}-${safeStem}${safeExtension}`;
}

async function createReadonlyCopy(options: {
  backendBaseUrl: string;
  backendAccessToken?: string;
  materialId: number;
  tempDir: string;
  dependencies: MaterialOpenDependencies;
}): Promise<{ copyPath: string } | { error: MaterialOpenResult }> {
  try {
    await options.dependencies.mkdir(options.tempDir, { recursive: true });
    const response = await fetchWithBackendError(
      options.dependencies,
      `${options.backendBaseUrl}/api/materials/${options.materialId}/download`,
      options.backendAccessToken,
    );
    if ("error" in response) {
      return response;
    }

    if (!response.response.body) {
      return { error: buildError("MaterialOpenCopyFailed", "创建材料只读副本失败") };
    }

    const contentDisposition = response.response.headers.get("content-disposition") ?? "";
    const originalFilename = getFilenameFromContentDisposition(contentDisposition) ?? `material-${options.materialId}`;
    const copyPath = path.join(
      options.tempDir,
      sanitizeCopyFilename(options.materialId, originalFilename),
    );

    await pipeline(response.response.body, options.dependencies.createWriteStream(copyPath));
    await options.dependencies.chmod(copyPath, 0o444);
    return { copyPath };
  } catch (error) {
    return { error: buildError("MaterialOpenCopyFailed", "创建材料只读副本失败") };
  }
}

async function fetchWithBackendError(
  dependencies: MaterialOpenDependencies,
  url: string,
  accessToken?: string,
): Promise<{ response: Response } | { error: MaterialOpenResult }> {
  let response: Response;
  try {
    response = await dependencies.fetch(url, {
      headers: accessToken ? { Authorization: `Bearer ${accessToken}` } : undefined,
    });
  } catch (error) {
    return { error: buildError("MaterialOpenBackendUnavailable", "系统服务尚未就绪，请稍后再试") };
  }

  if (response.status === 404) {
    return { error: buildError("MaterialOpenNotFound", "材料不存在或文件已被移动") };
  }
  if (!response.ok) {
    return { error: buildError("MaterialOpenBackendUnavailable", "无法读取材料，请稍后再试") };
  }
  return { response };
}

async function cleanupExpiredCopies(
  tempDir: string,
  dependencies: MaterialOpenDependencies,
): Promise<void> {
  let entries: string[];
  try {
    entries = await dependencies.readdir(tempDir);
  } catch {
    return;
  }

  const expiresBefore = dependencies.now() - MATERIAL_OPEN_COPY_TTL_MS;
  await Promise.all(
    entries.map(async (entry) => {
      const targetPath = path.join(tempDir, entry);
      try {
        const entryStat = await dependencies.stat(targetPath);
        if (entryStat.mtimeMs >= expiresBefore) {
          return;
        }
        await dependencies.rm(targetPath, { force: true, recursive: false });
      } catch {
        // 外部应用可能仍占用副本，跳过并留待下次清理。
      }
    }),
  );
}

function buildError(code: Exclude<MaterialOpenResult, { ok: true }>["code"], message: string): MaterialOpenResult {
  return { ok: false, code, message };
}

function getFilenameFromContentDisposition(value: string): string | null {
  const utf8Match = /filename\*=UTF-8''([^;]+)/i.exec(value);
  if (utf8Match) {
    try {
      return decodeURIComponent(utf8Match[1]);
    } catch {
      return utf8Match[1];
    }
  }

  const quotedMatch = /filename="([^"]+)"/i.exec(value);
  if (quotedMatch) {
    return quotedMatch[1];
  }

  const plainMatch = /filename=([^;]+)/i.exec(value);
  return plainMatch?.[1]?.trim() ?? null;
}
