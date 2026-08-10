import { randomUUID } from "node:crypto";
import {
  chmod,
  mkdir,
  open,
  readFile,
  rename,
  rm,
  stat,
} from "node:fs/promises";
import path from "node:path";

import type { DesktopBackendModeStatus } from "../../../../contracts/desktop-ipc.js";
import type { BackendMode } from "../backend/types.js";

export const DESKTOP_SETTINGS_SCHEMA_VERSION = 1;
export const DESKTOP_SETTINGS_RELATIVE_PATH = path.join(
  "desktop",
  "settings.json",
);
const MAX_DESKTOP_SETTINGS_BYTES = 64 * 1024;

type StoredDesktopSettings = {
  schema_version: typeof DESKTOP_SETTINGS_SCHEMA_VERSION;
  backend_mode: BackendMode;
  updated_at: string;
};

export type BackendModeSetting = {
  mode: BackendMode | null;
  warning?: string;
};

export type BackendModeSource =
  | "command_line"
  | "environment"
  | "settings"
  | "channel_default";

export type BackendModeResolution = {
  mode: BackendMode;
  source: BackendModeSource;
  defaultMode: BackendMode;
  configuredMode: BackendMode | null;
  warning?: string;
};

export function getDesktopSettingsPath(userDataPath: string): string {
  return path.join(userDataPath, DESKTOP_SETTINGS_RELATIVE_PATH);
}

export async function readBackendModeSetting(
  userDataPath: string,
): Promise<BackendModeSetting> {
  const settingsPath = getDesktopSettingsPath(userDataPath);
  let content: string;
  try {
    const fileStat = await stat(settingsPath);
    if (!fileStat.isFile() || fileStat.size > MAX_DESKTOP_SETTINGS_BYTES) {
      return {
        mode: null,
        warning: "桌面设置文件无效，已使用安全默认设置。",
      };
    }
    content = await readFile(settingsPath, "utf8");
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code === "ENOENT") {
      return { mode: null };
    }
    return {
      mode: null,
      warning: "无法读取桌面设置，已使用安全默认设置。",
    };
  }

  try {
    const value = JSON.parse(content) as Partial<StoredDesktopSettings>;
    if (
      value === null
      || typeof value !== "object"
      || value.schema_version !== DESKTOP_SETTINGS_SCHEMA_VERSION
      || !isBackendMode(value.backend_mode)
      || typeof value.updated_at !== "string"
      || !Number.isFinite(Date.parse(value.updated_at))
    ) {
      return {
        mode: null,
        warning: "桌面设置文件版本或内容无效，已使用安全默认设置。",
      };
    }
    return { mode: value.backend_mode };
  } catch {
    return {
      mode: null,
      warning: "桌面设置文件已损坏，已使用安全默认设置。",
    };
  }
}

export async function writeBackendModeSetting(
  userDataPath: string,
  mode: BackendMode,
): Promise<string> {
  if (!isBackendMode(mode)) {
    throw new Error("不支持的桌面后端运行模式。");
  }

  const settingsPath = getDesktopSettingsPath(userDataPath);
  const settingsDirectory = path.dirname(settingsPath);
  const temporaryPath = path.join(
    settingsDirectory,
    `.settings-${process.pid}-${randomUUID()}.tmp`,
  );
  const settings: StoredDesktopSettings = {
    schema_version: DESKTOP_SETTINGS_SCHEMA_VERSION,
    backend_mode: mode,
    updated_at: new Date().toISOString(),
  };

  await mkdir(settingsDirectory, { recursive: true, mode: 0o700 });
  await setPrivatePermissions(settingsDirectory, 0o700);
  let handle: Awaited<ReturnType<typeof open>> | null = null;
  try {
    handle = await open(temporaryPath, "wx", 0o600);
    await handle.writeFile(`${JSON.stringify(settings, null, 2)}\n`, "utf8");
    await handle.sync();
    await handle.close();
    handle = null;
    await setPrivatePermissions(temporaryPath, 0o600);
    await rename(temporaryPath, settingsPath);
    await setPrivatePermissions(settingsPath, 0o600);
    return settingsPath;
  } catch (error) {
    await handle?.close().catch(() => undefined);
    await rm(temporaryPath, { force: true }).catch(() => undefined);
    throw error;
  }
}

export function resolveBackendModeSelection(input: {
  argv: readonly string[];
  environmentMode?: string;
  setting: BackendModeSetting;
  appVersion: string;
}): BackendModeResolution {
  const defaultMode = getBackendModeChannelDefault(input.appVersion);
  const configuredMode = input.setting.mode;
  const commandLineMode = getCommandLineBackendMode(input.argv);
  if (commandLineMode !== null) {
    return {
      mode: commandLineMode,
      source: "command_line",
      defaultMode,
      configuredMode,
      warning: input.setting.warning,
    };
  }

  const environmentMode = parseBackendMode(input.environmentMode);
  if (environmentMode !== null) {
    return {
      mode: environmentMode,
      source: "environment",
      defaultMode,
      configuredMode,
      warning: input.setting.warning,
    };
  }

  if (configuredMode !== null) {
    return {
      mode: configuredMode,
      source: "settings",
      defaultMode,
      configuredMode,
      warning: input.setting.warning,
    };
  }

  return {
    mode: defaultMode,
    source: "channel_default",
    defaultMode,
    configuredMode,
    warning: input.setting.warning,
  };
}

export function buildBackendModeStatus(
  currentMode: BackendMode,
  next: BackendModeResolution,
): DesktopBackendModeStatus {
  return {
    currentMode,
    nextMode: next.mode,
    configuredMode: next.configuredMode,
    defaultMode: next.defaultMode,
    source: next.source,
    restartRequired: currentMode !== next.mode,
    overrideActive: next.source === "command_line" || next.source === "environment",
    ...(next.warning ? { warning: next.warning } : {}),
  };
}

export function getBackendModeChannelDefault(appVersion: string): BackendMode {
  const normalized = appVersion.trim().toLowerCase();
  return normalized.includes("-alpha")
    || normalized.includes("-beta")
    || normalized.includes("-rc")
    ? "split"
    : "combined";
}

export function getCommandLineBackendMode(
  argv: readonly string[],
): BackendMode | null {
  let resolved: BackendMode | null = null;
  for (let index = 0; index < argv.length; index += 1) {
    const argument = argv[index];
    if (argument === "--backend-mode") {
      const value = parseBackendMode(argv[index + 1]);
      if (value !== null) {
        resolved = value;
      }
      index += 1;
      continue;
    }
    if (argument.startsWith("--backend-mode=")) {
      const value = parseBackendMode(argument.slice("--backend-mode=".length));
      if (value !== null) {
        resolved = value;
      }
    }
  }
  return resolved;
}

export function buildBackendModeRelaunchArgs(
  argv: readonly string[],
  forcedMode?: BackendMode,
): string[] {
  const args: string[] = [];
  for (let index = 0; index < argv.length; index += 1) {
    const argument = argv[index];
    if (argument === "--backend-mode") {
      if (parseBackendMode(argv[index + 1]) !== null) {
        index += 1;
      }
      continue;
    }
    if (argument.startsWith("--backend-mode=")) {
      continue;
    }
    args.push(argument);
  }
  if (forcedMode !== undefined) {
    args.push(`--backend-mode=${forcedMode}`);
  }
  return args;
}

export function parseBackendMode(value: unknown): BackendMode | null {
  if (typeof value !== "string") {
    return null;
  }
  const normalized = value.trim().toLowerCase();
  return isBackendMode(normalized) ? normalized : null;
}

export function isBackendMode(value: unknown): value is BackendMode {
  return value === "combined" || value === "split";
}

async function setPrivatePermissions(targetPath: string, mode: number): Promise<void> {
  if (process.platform === "win32") {
    return;
  }
  await chmod(targetPath, mode);
}
