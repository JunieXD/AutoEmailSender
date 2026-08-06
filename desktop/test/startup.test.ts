import { describe, expect, it, vi } from "vitest";
import {
  STARTUP_REGISTRY_KEY,
  STARTUP_REGISTRY_VALUE_NAME,
  buildStartupCommand,
  getStartupAtLoginStatus,
  isLaunchedAtStartup,
  setStartupAtLoginEnabled,
  type StartupAtLoginInput,
} from "../src/main/shell/startup-at-login.js";

const executablePath = "C:\\Program Files\\Auto Email Sender\\Auto Email Sender.exe";
const startupCommand = `"${executablePath}" --startup`;

describe("startup at login registry service", () => {
  it("only supports packaged Windows and macOS builds", async () => {
    const missingRegistry = vi.fn((file: string, args: string[], callback: (error: Error | null, stdout: string, stderr: string) => void) => {
      callback(new Error("missing"), "", "");
    });

    await expect(
      getStartupAtLoginStatus({ platform: "linux", isPackaged: true, executablePath }),
    ).resolves.toMatchObject({ supported: false, enabled: false });
    await expect(
      getStartupAtLoginStatus({ platform: "win32", isPackaged: false, executablePath }),
    ).resolves.toMatchObject({ supported: false, enabled: false });
    await expect(
      getStartupAtLoginStatus({ platform: "darwin", isPackaged: false, executablePath }),
    ).resolves.toMatchObject({ supported: false, enabled: false });
    await expect(
      getStartupAtLoginStatus({
        platform: "win32",
        isPackaged: true,
        executablePath,
        dependencies: { execFile: missingRegistry as never },
      }),
    ).resolves.toMatchObject({ supported: true, enabled: false });
    await expect(
      getStartupAtLoginStatus({
        platform: "darwin",
        isPackaged: true,
        executablePath: "/Applications/Auto Email Sender.app/Contents/MacOS/Auto Email Sender",
        dependencies: {
          loginItems: {
            getLoginItemSettings: vi.fn(() => ({ openAtLogin: false })),
            setLoginItemSettings: vi.fn(),
          },
        },
      }),
    ).resolves.toMatchObject({ supported: true, enabled: false });
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

describe("startup at login macOS service", () => {
  const macExecutablePath = "/Applications/Auto Email Sender.app/Contents/MacOS/Auto Email Sender";

  it("reads macOS login item status", async () => {
    const loginItems = {
      getLoginItemSettings: vi.fn(() => ({ openAtLogin: true })),
      setLoginItemSettings: vi.fn(),
    };

    await expect(
      getStartupAtLoginStatus({
        platform: "darwin",
        isPackaged: true,
        executablePath: macExecutablePath,
        dependencies: { loginItems },
      }),
    ).resolves.toEqual({ supported: true, enabled: true });
    expect(loginItems.getLoginItemSettings).toHaveBeenCalled();
  });

  it("reports when macOS login item requires user approval", async () => {
    const loginItems = {
      getLoginItemSettings: vi.fn(() => ({ openAtLogin: false, status: "requires-approval" as const })),
      setLoginItemSettings: vi.fn(),
    };

    await expect(
      getStartupAtLoginStatus({
        platform: "darwin",
        isPackaged: true,
        executablePath: macExecutablePath,
        dependencies: { loginItems },
      }),
    ).resolves.toEqual({
      supported: true,
      enabled: false,
      message: "macOS 还需要在“系统设置 > 通用 > 登录项”中允许 Auto Email Sender 开机自启动。",
    });
  });

  it("enables macOS login item without Windows-only args", async () => {
    const loginItems = {
      getLoginItemSettings: vi.fn(() => ({ openAtLogin: true })),
      setLoginItemSettings: vi.fn(),
    };

    await expect(
      setStartupAtLoginEnabled({
        platform: "darwin",
        isPackaged: true,
        executablePath: macExecutablePath,
        dependencies: { loginItems },
      }, true),
    ).resolves.toEqual({ supported: true, enabled: true });
    expect(loginItems.setLoginItemSettings).toHaveBeenCalledWith({
      openAtLogin: true,
    });
  });

  it("returns guidance when macOS does not enable login item after a toggle", async () => {
    const loginItems = {
      getLoginItemSettings: vi.fn(() => ({ openAtLogin: false, status: "not-registered" as const })),
      setLoginItemSettings: vi.fn(),
    };

    await expect(
      setStartupAtLoginEnabled({
        platform: "darwin",
        isPackaged: true,
        executablePath: macExecutablePath,
        dependencies: { loginItems },
      }, true),
    ).resolves.toEqual({
      supported: true,
      enabled: false,
      message: "macOS 未确认开机自启动，请到“系统设置 > 通用 > 登录项”中允许 Auto Email Sender。",
    });
  });

  it("disables macOS login item", async () => {
    const loginItems = {
      getLoginItemSettings: vi.fn(() => ({ openAtLogin: false })),
      setLoginItemSettings: vi.fn(),
    };

    await expect(
      setStartupAtLoginEnabled({
        platform: "darwin",
        isPackaged: true,
        executablePath: macExecutablePath,
        dependencies: { loginItems },
      }, false),
    ).resolves.toEqual({ supported: true, enabled: false });
    expect(loginItems.setLoginItemSettings).toHaveBeenCalledWith({
      openAtLogin: false,
    });
  });
});

describe("startup launch detection", () => {
  it("treats the startup argument as a startup launch", () => {
    expect(isLaunchedAtStartup({
      argv: ["/Applications/Auto Email Sender.app/Contents/MacOS/Auto Email Sender", "--startup"],
      platform: "darwin",
      getLoginItemSettings: vi.fn(() => ({ wasOpenedAtLogin: false })),
    })).toBe(true);
  });

  it("uses macOS login item launch status", () => {
    expect(isLaunchedAtStartup({
      argv: ["/Applications/Auto Email Sender.app/Contents/MacOS/Auto Email Sender"],
      platform: "darwin",
      getLoginItemSettings: vi.fn(() => ({ wasOpenedAtLogin: true })),
    })).toBe(true);
  });

  it("does not treat a regular macOS launch as startup", () => {
    expect(isLaunchedAtStartup({
      argv: ["/Applications/Auto Email Sender.app/Contents/MacOS/Auto Email Sender"],
      platform: "darwin",
      getLoginItemSettings: vi.fn(() => ({ wasOpenedAtLogin: false })),
    })).toBe(false);
  });

  it("does not treat a regular Windows launch as startup", () => {
    expect(isLaunchedAtStartup({
      argv: ["C:\\Program Files\\Auto Email Sender\\Auto Email Sender.exe"],
      platform: "win32",
    })).toBe(false);
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
