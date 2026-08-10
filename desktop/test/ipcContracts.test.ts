import { readFileSync } from "node:fs";
import path from "node:path";

import { describe, expect, it } from "vitest";

import { DESKTOP_IPC_CHANNELS } from "../src/contracts/channels.js";
import { backendStatusKeepsApiConnection } from "../src/contracts/backend-status.js";


const expectedChannels = [
  "agent-support:disable",
  "agent-support:dismiss-onboarding",
  "agent-support:enable",
  "agent-support:get-status",
  "agent-support:install-skill",
  "agent-support:repair",
  "agent-support:status",
  "agent-support:uninstall-skill",
  "app:get-version",
  "app:quit",
  "backend-mode:get-status",
  "backend-mode:restart",
  "backend-mode:set",
  "backend:connection",
  "backend:status",
  "community-share:save",
  "external-url:open",
  "materials:open",
  "professors:select-import-file",
  "startup:get-status",
  "startup:set-enabled",
  "update:check",
  "update:download",
  "update:quit-and-install",
  "update:status",
  "update:switch-to-full-download",
];

describe("desktop IPC contracts", () => {
  it("keeps the API connection while only background processing is degraded", () => {
    expect(backendStatusKeepsApiConnection({
      state: "degraded",
      baseUrl: "http://127.0.0.1:48120",
      reason: "background_unavailable",
      message: "后台服务暂时不可用",
    })).toBe(true);
    expect(backendStatusKeepsApiConnection({
      state: "restarting",
      code: 1,
      signal: null,
    })).toBe(false);
  });

  it("keeps every channel unique and versioned in one registry", () => {
    const channels = Object.values(DESKTOP_IPC_CHANNELS);

    expect(new Set(channels).size).toBe(channels.length);
    expect([...channels].sort()).toEqual(expectedChannels);
  });

  it("shares renderer-visible types between Desktop and Frontend", () => {
    const preloadBridge = readFileSync(
      path.resolve("src", "preload", "bridge.ts"),
      "utf8",
    );
    const ipcRegistration = readFileSync(
      path.resolve("src", "main", "ipc", "register.ts"),
      "utf8",
    );
    const frontendTypes = readFileSync(
      path.resolve("..", "frontend", "src", "types", "desktop.d.ts"),
      "utf8",
    );
    const sharedContract = readFileSync(
      path.resolve("..", "contracts", "desktop-ipc.d.ts"),
      "utf8",
    );

    expect(preloadBridge).toContain("../../../contracts/desktop-ipc.js");
    expect(ipcRegistration).toContain("../../../../contracts/desktop-ipc.js");
    expect(frontendTypes).toContain("../../../contracts/desktop-ipc.js");
    expect(frontendTypes).not.toMatch(/^export type Desktop/m);
    expect(sharedContract).not.toMatch(/from ["'](?:electron|node:)/);
    expect(sharedContract).not.toContain("NodeJS.");
  });

  it("keeps the preload entrypoint limited to bridge installation", () => {
    const preloadEntry = readFileSync(path.resolve("src", "preload.ts"), "utf8");

    expect(preloadEntry).toContain('import { installDesktopBridge } from "./preload/bridge.js";');
    expect(preloadEntry).toContain("installDesktopBridge();");
    expect(preloadEntry).not.toContain("ipcRenderer");
    expect(preloadEntry).not.toContain("contextBridge");
  });
});
