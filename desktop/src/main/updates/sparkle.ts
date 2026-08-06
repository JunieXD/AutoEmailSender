import { app } from "electron";
import { createRequire } from "node:module";
import path from "node:path";

export type SparkleBridge = {
  start: () => void;
  checkForUpdates: () => void;
};

type SparkleBridgePathInput = {
  isPackaged: boolean;
  appPath: string;
  resourcesPath: string;
};

const require = createRequire(import.meta.url);
let loadedBridge: SparkleBridge | null = null;

export function resolveSparkleBridgePath(input: SparkleBridgePathInput): string {
  if (input.isPackaged) {
    return path.join(input.resourcesPath, "native", "sparkle_bridge.node");
  }

  return path.join(
    input.appPath,
    "native",
    "sparkle",
    "build",
    "Release",
    "sparkle_bridge.node",
  );
}

export function isSparkleBridge(value: unknown): value is SparkleBridge {
  if (typeof value !== "object" || value === null) {
    return false;
  }

  const candidate = value as Partial<SparkleBridge>;
  return typeof candidate.start === "function" && typeof candidate.checkForUpdates === "function";
}

export function startMacSparkle(): void {
  getSparkleBridge().start();
}

export function checkForMacSparkleUpdates(): void {
  const bridge = getSparkleBridge();
  bridge.start();
  bridge.checkForUpdates();
}

function getSparkleBridge(): SparkleBridge {
  if (loadedBridge !== null) {
    return loadedBridge;
  }

  const bridgePath = resolveSparkleBridgePath({
    isPackaged: app.isPackaged,
    appPath: app.getAppPath(),
    resourcesPath: process.resourcesPath,
  });
  const candidate: unknown = require(bridgePath);
  if (!isSparkleBridge(candidate)) {
    throw new Error(`macOS 更新组件导出无效：${bridgePath}`);
  }

  loadedBridge = candidate;
  return loadedBridge;
}
