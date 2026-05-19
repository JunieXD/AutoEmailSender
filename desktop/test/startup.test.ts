import { describe, expect, it, vi } from "vitest";
import {
  STARTUP_REGISTRY_KEY,
  STARTUP_REGISTRY_VALUE_NAME,
  buildStartupCommand,
  getStartupAtLoginStatus,
  setStartupAtLoginEnabled,
  type StartupAtLoginInput,
} from "../src/startup.js";

const executablePath = "C:\\Program Files\\Auto Email Sender\\Auto Email Sender.exe";
const startupCommand = `"${executablePath}" --startup`;

describe("startup at login registry service", () => {
  it("only supports packaged Windows builds", async () => {
    await expect(
      getStartupAtLoginStatus({ platform: "linux", isPackaged: true, executablePath }),
    ).resolves.toMatchObject({ supported: false, enabled: false });
    await expect(
      getStartupAtLoginStatus({ platform: "win32", isPackaged: false, executablePath }),
    ).resolves.toMatchObject({ supported: false, enabled: false });
  });

  it("builds a quoted startup command", () => {
    expect(buildStartupCommand(executablePath)).toBe(startupCommand);
    expect(() => buildStartupCommand('C:\\bad"path\\app.exe')).toThrow("引号");
  });

  it("reads enabled status from the Run registry key", async () => {
    const execFile = vi.fn((file: string, args: string[], callback: (error: Error | null, stdout: string, stderr: string) => void) => {
      callback(
        null,
        `HKEY_CURRENT_USER\\Software\\Microsoft\\Windows\\CurrentVersion\\Run\n    ${STARTUP_REGISTRY_VALUE_NAME}    REG_SZ    ${startupCommand}\n`,
        "",
      );
    });

    await expect(getStartupAtLoginStatus(withExecFile(execFile))).resolves.toEqual({
      supported: true,
      enabled: true,
    });
    expect(execFile).toHaveBeenCalledWith(
      "reg.exe",
      ["query", STARTUP_REGISTRY_KEY, "/v", STARTUP_REGISTRY_VALUE_NAME],
      expect.any(Function),
    );
  });

  it("writes the Run registry value when enabling startup", async () => {
    const execFile = vi.fn((file: string, args: string[], callback: (error: Error | null, stdout: string, stderr: string) => void) => {
      if (args[0] === "query") {
        callback(
          null,
          `HKEY_CURRENT_USER\\Software\\Microsoft\\Windows\\CurrentVersion\\Run\n    ${STARTUP_REGISTRY_VALUE_NAME}    REG_SZ    ${startupCommand}\n`,
          "",
        );
        return;
      }
      callback(null, "", "");
    });

    await expect(setStartupAtLoginEnabled(withExecFile(execFile), true)).resolves.toEqual({
      supported: true,
      enabled: true,
    });
    expect(execFile).toHaveBeenCalledWith(
      "reg.exe",
      [
        "add",
        STARTUP_REGISTRY_KEY,
        "/v",
        STARTUP_REGISTRY_VALUE_NAME,
        "/t",
        "REG_SZ",
        "/d",
        startupCommand,
        "/f",
      ],
      expect.any(Function),
    );
  });

  it("deletes the Run registry value when disabling startup", async () => {
    const execFile = vi.fn((file: string, args: string[], callback: (error: Error | null, stdout: string, stderr: string) => void) => {
      if (args[0] === "query") {
        callback(new Error("missing"), "", "");
        return;
      }
      callback(null, "", "");
    });

    await expect(setStartupAtLoginEnabled(withExecFile(execFile), false)).resolves.toEqual({
      supported: true,
      enabled: false,
    });
    expect(execFile).toHaveBeenCalledWith(
      "reg.exe",
      ["delete", STARTUP_REGISTRY_KEY, "/v", STARTUP_REGISTRY_VALUE_NAME, "/f"],
      expect.any(Function),
    );
  });
});

function withExecFile(execFile: ReturnType<typeof vi.fn>): StartupAtLoginInput {
  return {
    platform: "win32",
    isPackaged: true,
    executablePath,
    dependencies: { execFile: execFile as never },
  };
}
