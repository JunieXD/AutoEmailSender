import fs from "node:fs/promises";
import path from "node:path";
import {
  dialog,
  ipcMain,
  type OpenDialogOptions,
  type SaveDialogOptions,
} from "electron";
import { DESKTOP_IPC_CHANNELS } from "./contracts/channels.js";
import type { CommunityShareSaveResult } from "./types.js";

export type SelectedImportFile = {
  name: string;
  type: string;
  data: ArrayBuffer;
};

const PROFESSOR_IMPORT_EXTENSIONS = ["csv", "xlsx"];
export const COMMUNITY_SHARE_MAX_BYTES = 5 * 1024 * 1024;

type CommunityShareSaveDependencies = {
  showSaveDialog: (
    options: SaveDialogOptions,
  ) => Promise<{ canceled: boolean; filePath?: string }>;
  writeFile: (filePath: string, data: Uint8Array) => Promise<void>;
};

export function buildProfessorImportDialogOptions(): OpenDialogOptions {
  return {
    title: "选择导师导入文件",
    properties: ["openFile"],
    filters: [
      { name: "导师导入文件", extensions: PROFESSOR_IMPORT_EXTENSIONS },
    ],
  };
}

export function getImportFileMimeType(fileName: string): string {
  const extension = path.extname(fileName).toLowerCase();
  if (extension === ".csv") {
    return "text/csv";
  }
  if (extension === ".xlsx") {
    return "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet";
  }
  return "application/octet-stream";
}

export function buildCommunityShareSaveDialogOptions(): SaveDialogOptions {
  return {
    title: "保存社区共享包",
    defaultPath: "community-share.xlsx",
    buttonLabel: "保存并继续投稿",
    filters: [{ name: "Excel 工作簿", extensions: ["xlsx"] }],
  };
}

export function createCommunityShareSaveService(
  dependencyOverrides: Partial<CommunityShareSaveDependencies> = {},
) {
  const dependencies: CommunityShareSaveDependencies = {
    showSaveDialog: (options) => dialog.showSaveDialog(options),
    writeFile: (filePath, data) => fs.writeFile(filePath, data),
    ...dependencyOverrides,
  };

  return {
    save: async (data: unknown): Promise<CommunityShareSaveResult> => {
      if (
        !(data instanceof ArrayBuffer) ||
        data.byteLength === 0 ||
        data.byteLength > COMMUNITY_SHARE_MAX_BYTES
      ) {
        throw new Error("社区共享包文件无效或超过 5 MiB");
      }

      const result = await dependencies.showSaveDialog(
        buildCommunityShareSaveDialogOptions(),
      );
      if (result.canceled || !result.filePath) {
        return { status: "canceled" };
      }

      const filePath = result.filePath.toLowerCase().endsWith(".xlsx")
        ? result.filePath
        : `${result.filePath}.xlsx`;
      await dependencies.writeFile(filePath, new Uint8Array(data));
      return { status: "saved" };
    },
  };
}

export function registerFileSelectionIpc(): void {
  ipcMain.handle(DESKTOP_IPC_CHANNELS.professorSelectImportFile, async (): Promise<SelectedImportFile | null> => {
    const result = await dialog.showOpenDialog(buildProfessorImportDialogOptions());
    if (result.canceled || result.filePaths.length === 0) {
      return null;
    }

    const filePath = result.filePaths[0];
    const content = await fs.readFile(filePath);
    return {
      name: path.basename(filePath),
      type: getImportFileMimeType(filePath),
      data: content.buffer.slice(
        content.byteOffset,
        content.byteOffset + content.byteLength,
      ),
    };
  });
}

export function registerCommunityShareSaveIpc(): void {
  const service = createCommunityShareSaveService();
  ipcMain.handle(DESKTOP_IPC_CHANNELS.communityShareSave, (_event, data: unknown) =>
    service.save(data),
  );
}
