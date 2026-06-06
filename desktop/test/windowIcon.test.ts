import path from "node:path";
import { describe, expect, it } from "vitest";
import { getWindowIconPath } from "../src/windowIcon.js";

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
});
