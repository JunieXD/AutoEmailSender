import { readFileSync } from "node:fs";
import path from "node:path";
import { describe, expect, it, vi } from "vitest";
import { isSparkleBridge, resolveSparkleBridgePath } from "../src/main/updates/sparkle.js";

describe("macOS Sparkle bridge", () => {
  it("resolves the native addon from Resources in packaged apps", () => {
    expect(
      resolveSparkleBridgePath({
        isPackaged: true,
        appPath: "/Applications/Auto Email Sender.app/Contents/Resources/app.asar",
        resourcesPath: "/Applications/Auto Email Sender.app/Contents/Resources",
      }),
    ).toBe("/Applications/Auto Email Sender.app/Contents/Resources/native/sparkle_bridge.node");
  });

  it("resolves the locally built addon in development", () => {
    expect(
      resolveSparkleBridgePath({
        isPackaged: false,
        appPath: "/repo/desktop",
        resourcesPath: "/repo/desktop/node_modules/electron/dist/Electron.app/Contents/Resources",
      }),
    ).toBe("/repo/desktop/native/sparkle/build/Release/sparkle_bridge.node");
  });

  it("validates the narrow native bridge API", () => {
    expect(isSparkleBridge({ start: vi.fn(), checkForUpdates: vi.fn() })).toBe(true);
    expect(isSparkleBridge({ start: vi.fn() })).toBe(false);
    expect(isSparkleBridge(null)).toBe(false);
  });

  it("loads the addon lazily through CommonJS interop", () => {
    const source = readFileSync(
      path.resolve("src", "main", "updates", "sparkle.ts"),
      "utf8",
    );

    expect(source).toContain("createRequire(import.meta.url)");
    expect(source.indexOf("function getSparkleBridge")).toBeGreaterThan(source.indexOf("let loadedBridge"));
  });
});
