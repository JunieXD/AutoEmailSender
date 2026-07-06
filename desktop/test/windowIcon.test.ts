import path from "node:path";
import type { NativeImage } from "electron";
import { describe, expect, it, vi } from "vitest";
import { createTrayIcon, getWindowIconPath } from "../src/windowIcon.js";

describe("desktop window icon", () => {
  it("uses the packaged app icon", () => {
    expect(
      getWindowIconPath({
        isPackaged: true,
        platform: "win32",
        resourcesPath: "C:\\App\\resources",
        repoRoot: "C:\\Repo",
      }),
    ).toBe(path.join("C:\\App\\resources", "build", "icon.ico"));
  });

  it("uses the repo ico app icon for Windows dev", () => {
    expect(
      getWindowIconPath({
        isPackaged: false,
        platform: "win32",
        resourcesPath: "C:\\App\\resources",
        repoRoot: "C:\\Repo",
      }),
    ).toBe(path.join("C:\\Repo", "desktop", "build", "icon.ico"));
  });

  it("uses the repo png app icon for Linux dev", () => {
    expect(
      getWindowIconPath({
        isPackaged: false,
        platform: "linux",
        resourcesPath: "/opt/AutoEmailSender/resources",
        repoRoot: "/home/junie/AutoEmailSender",
      }),
    ).toBe(path.join("/home/junie/AutoEmailSender", "desktop", "build", "icon.png"));
  });

  it("resizes the macOS tray icon for the menu bar", () => {
    const resizedIcon = {} as NativeImage;
    const sourceIcon = {
      resize: vi.fn(() => resizedIcon),
    } as unknown as NativeImage;
    const nativeImage = {
      createFromPath: vi.fn(() => sourceIcon),
    };

    expect(
      createTrayIcon({
        isPackaged: true,
        platform: "darwin",
        resourcesPath: "/Applications/Auto Email Sender.app/Contents/Resources",
        repoRoot: "/repo",
        nativeImage,
      }),
    ).toBe(resizedIcon);
    expect(nativeImage.createFromPath).toHaveBeenCalledWith(
      path.join("/Applications/Auto Email Sender.app/Contents/Resources", "build", "icon.png"),
    );
    expect(sourceIcon.resize).toHaveBeenCalledWith({ width: 18, height: 18 });
  });

  it("uses the icon path directly for non-macOS tray icons", () => {
    const nativeImage = {
      createFromPath: vi.fn(),
    };

    expect(
      createTrayIcon({
        isPackaged: true,
        platform: "win32",
        resourcesPath: "C:\\App\\resources",
        repoRoot: "C:\\Repo",
        nativeImage,
      }),
    ).toBe(path.join("C:\\App\\resources", "build", "icon.ico"));
    expect(nativeImage.createFromPath).not.toHaveBeenCalled();
  });
});
