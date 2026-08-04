import { describe, expect, it, vi } from "vitest";
import {
  buildCommunityShareSaveDialogOptions,
  buildProfessorImportDialogOptions,
  COMMUNITY_SHARE_MAX_BYTES,
  createCommunityShareSaveService,
} from "../src/fileSelection.js";

describe("desktop file selection", () => {
  it("opens professor imports with an open-file dialog", () => {
    expect(buildProfessorImportDialogOptions()).toMatchObject({
      title: "选择导师导入文件",
      properties: ["openFile"],
      filters: [
        { name: "导师导入文件", extensions: ["csv", "xlsx"] },
      ],
    });
  });

  it("saves a community share package before reporting completion", async () => {
    const showSaveDialog = vi.fn().mockResolvedValue({
      canceled: false,
      filePath: "/downloads/mentor-community-share",
    });
    const writeFile = vi.fn().mockResolvedValue(undefined);
    const service = createCommunityShareSaveService({
      showSaveDialog,
      writeFile,
    });
    const data = new TextEncoder().encode("xlsx-content").buffer;

    await expect(service.save(data)).resolves.toEqual({ status: "saved" });
    expect(showSaveDialog).toHaveBeenCalledWith(
      buildCommunityShareSaveDialogOptions(),
    );
    expect(writeFile).toHaveBeenCalledWith(
      "/downloads/mentor-community-share.xlsx",
      expect.any(Uint8Array),
    );
  });

  it("does not write a community share package when saving is canceled", async () => {
    const writeFile = vi.fn();
    const service = createCommunityShareSaveService({
      showSaveDialog: vi.fn().mockResolvedValue({ canceled: true }),
      writeFile,
    });

    await expect(
      service.save(new Uint8Array([1]).buffer),
    ).resolves.toEqual({ status: "canceled" });
    expect(writeFile).not.toHaveBeenCalled();
  });

  it("rejects a share package above GitHub's 5 MiB intake limit", async () => {
    const showSaveDialog = vi.fn();
    const writeFile = vi.fn();
    const service = createCommunityShareSaveService({ showSaveDialog, writeFile });

    await expect(
      service.save(new ArrayBuffer(COMMUNITY_SHARE_MAX_BYTES + 1)),
    ).rejects.toThrow("超过 5 MiB");
    expect(showSaveDialog).not.toHaveBeenCalled();
    expect(writeFile).not.toHaveBeenCalled();
  });

  it("uses a save dialog that explains the next contribution step", () => {
    expect(buildCommunityShareSaveDialogOptions()).toMatchObject({
      title: "保存社区共享包",
      defaultPath: "community-share.xlsx",
      buttonLabel: "保存并继续投稿",
      filters: [{ name: "Excel 工作簿", extensions: ["xlsx"] }],
    });
  });
});
