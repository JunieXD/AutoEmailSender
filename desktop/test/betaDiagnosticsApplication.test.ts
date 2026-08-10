import { readFileSync, readdirSync } from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";

import { isBetaDiagnosticsEnabled } from "../src/main/diagnostics/constants.js";


const applicationSource = readFileSync(
  path.resolve("src", "main", "bootstrap", "application.ts"),
  "utf8",
);

describe("desktop Beta diagnostics application integration", () => {
  it("enables only prereleases or the exact local test gate", () => {
    expect(isBetaDiagnosticsEnabled({ appVersion: "2.6.0-alpha.1" })).toBe(true);
    expect(isBetaDiagnosticsEnabled({ appVersion: "2.6.0-beta.2" })).toBe(true);
    expect(isBetaDiagnosticsEnabled({ appVersion: "2.6.0-rc.3" })).toBe(true);
    expect(isBetaDiagnosticsEnabled({ appVersion: "2.5.4" })).toBe(false);
    expect(isBetaDiagnosticsEnabled({
      appVersion: "2.5.4",
      environmentValue: "enabled-for-tests-only",
    })).toBe(true);
    expect(isBetaDiagnosticsEnabled({
      appVersion: "development",
      environmentValue: "true",
    })).toBe(false);
  });

  it("starts recording before backend/window creation and finalizes it before exit", () => {
    const readyBlock = sourceBetween(
      "app.whenReady().then(async () => {",
      'app.on("window-all-closed"',
    );
    expect(readyBlock.indexOf("await betaDiagnosticsRecorder.start()"))
      .toBeLessThan(readyBlock.indexOf("startWindowCreationOnce"));

    const stopBlock = sourceBetween(
      "function stopBackendAndExit",
      "function getStartupInput",
    );
    expect(stopBlock).toContain("currentBackend?.stop()");
    expect(stopBlock).toContain("betaDiagnosticsRecorder.stop()");
    expect(stopBlock.indexOf("betaDiagnosticsRecorder.stop()"))
      .toBeLessThan(stopBlock.indexOf("app.exit(exitCode)"));

    const beforeQuitBlock = sourceBetween(
      'app.on("before-quit"',
      'process.once("SIGINT"',
    );
    expect(beforeQuitBlock).toContain("event.preventDefault()");
    expect(beforeQuitBlock).toContain("stopBackendAndExit(0)");
  });

  it("keeps export reachable from the tray and both native startup failure paths", () => {
    const trayBlock = sourceBetween(
      "function buildTrayContextMenu",
      "async function loadStartupAtLoginForTray",
    );
    expect(trayBlock).toContain('label: "导出 Beta 诊断包"');
    expect(trayBlock).toContain("visible: betaDiagnosticsEnabled");
    expect(trayBlock).toContain("exportBetaDiagnosticsFromNative()");

    const splitRecoveryBlock = sourceBetween(
      "async function offerNativeSplitRecovery",
      "async function offerNativeGenericStartupFailure",
    );
    expect(splitRecoveryBlock).toContain('"导出 Beta 诊断包"');
    expect(splitRecoveryBlock).toContain("await exportBetaDiagnosticsFromNative()");

    const genericFailureBlock = sourceBetween(
      "async function offerNativeGenericStartupFailure",
      "async function exportBetaDiagnosticsFromNative",
    );
    expect(genericFailureBlock).toContain('buttons: ["导出 Beta 诊断包", "退出"]');
    expect(genericFailureBlock).toContain("await exportBetaDiagnosticsFromNative()");
  });

  it("contains no remote upload client and only requests the local relative summary route", () => {
    const diagnosticsRoot = path.resolve("src", "main", "diagnostics");
    const diagnosticsSource = readdirSync(diagnosticsRoot)
      .filter((name) => name.endsWith(".ts"))
      .map((name) => readFileSync(path.join(diagnosticsRoot, name), "utf8"))
      .join("\n");
    expect(diagnosticsSource).not.toMatch(
      /from\s+["']node:(?:http|https|net)["']|\b(?:fetch|axios)\s*\(|electron\.net/gu,
    );
    expect(diagnosticsSource).toContain(
      'backendClient.request("/api/diagnostics/beta-summary"',
    );
    expect(diagnosticsSource).not.toMatch(/backendClient\.request\(["']https?:/gu);
  });
});

function sourceBetween(start: string, end: string): string {
  const startIndex = applicationSource.indexOf(start);
  const endIndex = applicationSource.indexOf(end, startIndex + start.length);
  expect(startIndex, `missing source marker: ${start}`).toBeGreaterThanOrEqual(0);
  expect(endIndex, `missing source marker: ${end}`).toBeGreaterThan(startIndex);
  return applicationSource.slice(startIndex, endIndex);
}
