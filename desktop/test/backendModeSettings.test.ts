import { readFileSync } from "node:fs";
import {
  mkdtemp,
  readFile,
  readdir,
  rm,
  stat,
  writeFile,
} from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import { afterEach, describe, expect, it } from "vitest";

import {
  buildBackendModeRelaunchArgs,
  buildBackendModeStatus,
  DESKTOP_SETTINGS_SCHEMA_VERSION,
  getBackendModeChannelDefault,
  getDesktopSettingsPath,
  readBackendModeSetting,
  resolveBackendModeSelection,
  writeBackendModeSetting,
} from "../src/main/settings/backend-mode.js";

const temporaryDirectories: string[] = [];

async function createTemporaryDirectory(): Promise<string> {
  const directory = await mkdtemp(path.join(tmpdir(), "auto-email-sender-settings-"));
  temporaryDirectories.push(directory);
  return directory;
}

afterEach(async () => {
  await Promise.all(
    temporaryDirectories.splice(0).map((directory) =>
      rm(directory, { recursive: true, force: true }),
    ),
  );
});

describe("desktop backend mode settings", () => {
  it("keeps stable builds combined and defaults supported prereleases to split", () => {
    expect(getBackendModeChannelDefault("2.5.4")).toBe("combined");
    expect(getBackendModeChannelDefault("2.6.0-alpha.2")).toBe("split");
    expect(getBackendModeChannelDefault("2.6.0-beta.1")).toBe("split");
    expect(getBackendModeChannelDefault("2.6.0-rc.3")).toBe("split");
  });

  it("resolves command line, environment, settings, then channel default", () => {
    const base = {
      argv: [] as string[],
      environmentMode: undefined,
      setting: { mode: null },
      appVersion: "2.6.0-beta.1",
    };

    expect(resolveBackendModeSelection(base)).toMatchObject({
      mode: "split",
      source: "channel_default",
    });
    expect(resolveBackendModeSelection({
      ...base,
      setting: { mode: "combined" },
    })).toMatchObject({ mode: "combined", source: "settings" });
    expect(resolveBackendModeSelection({
      ...base,
      setting: { mode: "combined" },
      environmentMode: "split",
    })).toMatchObject({ mode: "split", source: "environment" });
    expect(resolveBackendModeSelection({
      ...base,
      argv: ["--backend-mode=combined"],
      environmentMode: "split",
    })).toMatchObject({ mode: "combined", source: "command_line" });
  });

  it("writes private schema-versioned settings atomically and replaces them", async () => {
    const userDataPath = await createTemporaryDirectory();
    const settingsPath = await writeBackendModeSetting(userDataPath, "split");

    expect(settingsPath).toBe(getDesktopSettingsPath(userDataPath));
    expect(JSON.parse(await readFile(settingsPath, "utf8"))).toMatchObject({
      schema_version: DESKTOP_SETTINGS_SCHEMA_VERSION,
      backend_mode: "split",
    });
    expect(await readBackendModeSetting(userDataPath)).toEqual({ mode: "split" });

    await writeBackendModeSetting(userDataPath, "combined");
    expect(await readBackendModeSetting(userDataPath)).toEqual({ mode: "combined" });
    expect(await readdir(path.dirname(settingsPath))).toEqual(["settings.json"]);
    if (process.platform !== "win32") {
      expect((await stat(path.dirname(settingsPath))).mode & 0o777).toBe(0o700);
      expect((await stat(settingsPath)).mode & 0o777).toBe(0o600);
    }
  });

  it("does not let malformed settings block explicit combined safe mode", async () => {
    const userDataPath = await createTemporaryDirectory();
    const settingsPath = getDesktopSettingsPath(userDataPath);
    await writeBackendModeSetting(userDataPath, "split");
    await writeFile(settingsPath, "not-json", "utf8");

    const setting = await readBackendModeSetting(userDataPath);
    expect(setting.mode).toBeNull();
    expect(setting.warning).toContain("已损坏");
    expect(resolveBackendModeSelection({
      argv: ["--backend-mode", "combined"],
      environmentMode: "split",
      setting,
      appVersion: "2.6.0-beta.1",
    })).toMatchObject({
      mode: "combined",
      source: "command_line",
    });
  });

  it("removes stale mode overrides when building normal relaunch arguments", () => {
    expect(buildBackendModeRelaunchArgs([
      "main.js",
      "--dev",
      "--backend-mode",
      "split",
      "--backend-mode=split",
    ])).toEqual(["main.js", "--dev"]);
    expect(buildBackendModeRelaunchArgs(
      ["main.js", "--backend-mode=split"],
      "combined",
    )).toEqual(["main.js", "--backend-mode=combined"]);
    expect(buildBackendModeRelaunchArgs(
      ["main.js", "--backend-mode", "--dev"],
      "combined",
    )).toEqual(["main.js", "--dev", "--backend-mode=combined"]);
  });

  it("reports current and effective next modes without hiding overrides", () => {
    expect(buildBackendModeStatus("combined", {
      mode: "split",
      source: "environment",
      defaultMode: "combined",
      configuredMode: "combined",
      warning: "settings warning",
    })).toEqual({
      currentMode: "combined",
      nextMode: "split",
      configuredMode: "combined",
      defaultMode: "combined",
      source: "environment",
      restartRequired: true,
      overrideActive: true,
      warning: "settings warning",
    });
  });

  it("keeps native combined recovery wired to a split group-restart failure", () => {
    const source = readFileSync(
      path.resolve("src", "main", "bootstrap", "application.ts"),
      "utf8",
    );
    const restartSource = source.slice(
      source.indexOf("async function restartBackendAfterUnexpectedExit"),
      source.indexOf("function publishBackendReady"),
    );
    const failureStatusIndex = restartSource.indexOf('state: "error"');
    const modeGuardIndex = restartSource.indexOf(
      'if (currentBackendMode === "split")',
      failureStatusIndex,
    );
    const recoveryIndex = restartSource.indexOf(
      "void offerNativeSplitRecovery(message, false);",
      modeGuardIndex,
    );

    expect(failureStatusIndex).toBeGreaterThan(-1);
    expect(modeGuardIndex).toBeGreaterThan(failureStatusIndex);
    expect(recoveryIndex).toBeGreaterThan(modeGuardIndex);
  });
});
