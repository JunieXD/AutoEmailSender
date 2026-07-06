import { execFile } from "node:child_process";
import type { StartupAtLoginStatus } from "./types.js";

export const STARTUP_REGISTRY_KEY = "HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Run";
export const STARTUP_REGISTRY_VALUE_NAME = "Auto Email Sender";

type MacLoginItemSettings = {
  openAtLogin?: boolean;
};

type MacLoginItemAdapter = {
  getLoginItemSettings: () => MacLoginItemSettings;
  setLoginItemSettings: (settings: { openAtLogin: boolean }) => void;
};

export type StartupAtLoginInput = {
  platform: NodeJS.Platform;
  isPackaged: boolean;
  executablePath: string;
  dependencies?: {
    execFile?: typeof execFile;
    loginItems?: MacLoginItemAdapter;
  };
};

type RegistryQueryResult = {
  stdout: string;
};

export function buildStartupCommand(executablePath: string): string {
  if (executablePath.includes('"')) {
    throw new Error("应用路径包含不支持的引号字符。");
  }

  return `"${executablePath}" --startup`;
}

export function isLaunchedAtStartup(input: {
  argv: string[];
  platform: NodeJS.Platform;
  getLoginItemSettings?: () => { wasOpenedAtLogin?: boolean };
}): boolean {
  if (input.argv.includes("--startup")) {
    return true;
  }

  if (input.platform === "darwin") {
    return Boolean(input.getLoginItemSettings?.().wasOpenedAtLogin);
  }

  return false;
}

export async function getStartupAtLoginStatus(
  input: StartupAtLoginInput,
): Promise<StartupAtLoginStatus> {
  const unsupportedStatus = getUnsupportedStatus(input);
  if (unsupportedStatus !== null) {
    return unsupportedStatus;
  }

  if (input.platform === "darwin") {
    const settings = getMacLoginItems(input).getLoginItemSettings();
    return {
      supported: true,
      enabled: Boolean(settings.openAtLogin),
    };
  }

  try {
    const result = await runRegistryCommand(["query", STARTUP_REGISTRY_KEY, "/v", STARTUP_REGISTRY_VALUE_NAME], input);
    return {
      supported: true,
      enabled: parseRegistryValue(result.stdout) !== null,
    };
  } catch {
    return { supported: true, enabled: false };
  }
}

export async function setStartupAtLoginEnabled(
  input: StartupAtLoginInput,
  enabled: boolean,
): Promise<StartupAtLoginStatus> {
  const unsupportedStatus = getUnsupportedStatus(input);
  if (unsupportedStatus !== null) {
    return unsupportedStatus;
  }

  if (input.platform === "darwin") {
    getMacLoginItems(input).setLoginItemSettings({
      openAtLogin: enabled,
    });
    return getStartupAtLoginStatus(input);
  }

  if (enabled) {
    await runRegistryCommand(
      [
        "add",
        STARTUP_REGISTRY_KEY,
        "/v",
        STARTUP_REGISTRY_VALUE_NAME,
        "/t",
        "REG_SZ",
        "/d",
        buildStartupCommand(input.executablePath),
        "/f",
      ],
      input,
    );
  } else {
    try {
      await runRegistryCommand(["delete", STARTUP_REGISTRY_KEY, "/v", STARTUP_REGISTRY_VALUE_NAME, "/f"], input);
    } catch {
      return { supported: true, enabled: false };
    }
  }

  return getStartupAtLoginStatus(input);
}

function getUnsupportedStatus(input: StartupAtLoginInput): StartupAtLoginStatus | null {
  if (!input.isPackaged) {
    return {
      supported: false,
      enabled: false,
      message: "开机自启动仅在安装后的桌面版中可用。",
    };
  }

  if (input.platform !== "win32" && input.platform !== "darwin") {
    return {
      supported: false,
      enabled: false,
      message: "当前平台不支持开机自启动。",
    };
  }

  return null;
}

function getMacLoginItems(input: StartupAtLoginInput): MacLoginItemAdapter {
  const loginItems = input.dependencies?.loginItems;
  if (!loginItems) {
    throw new Error("macOS 开机自启动接口未初始化。");
  }
  return loginItems;
}

function runRegistryCommand(
  args: string[],
  input: StartupAtLoginInput,
): Promise<RegistryQueryResult> {
  const execute = input.dependencies?.execFile ?? execFile;
  return new Promise((resolve, reject) => {
    execute("reg.exe", args, (error, stdout) => {
      if (error) {
        reject(error);
        return;
      }

      resolve({ stdout });
    });
  });
}

function parseRegistryValue(stdout: string): string | null {
  const escapedValueName = STARTUP_REGISTRY_VALUE_NAME.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const match = stdout.match(new RegExp(`^\\s*${escapedValueName}\\s+REG_\\w+\\s+(.+?)\\s*$`, "m"));
  return match?.[1] ?? null;
}
