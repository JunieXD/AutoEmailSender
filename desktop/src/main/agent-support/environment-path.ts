import { execFile } from "node:child_process";
import { chmod, readFile, writeFile } from "node:fs/promises";
import { promisify } from "node:util";
type ShellPaths = { shellProfilePath: string | null; commandDirectory: string };
const execFileAsync = promisify(execFile);

const ZSH_PATH_BLOCK_START = "# >>> Auto Email Sender Agent support >>>";

const ZSH_PATH_BLOCK_END = "# <<< Auto Email Sender Agent support <<<";

const WINDOWS_ENVIRONMENT_CHANGE_SCRIPT = `
Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;

public static class AutoEmailSenderEnvironment {
    [DllImport("user32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    public static extern IntPtr SendMessageTimeout(
        IntPtr hWnd,
        uint message,
        UIntPtr wParam,
        string lParam,
        uint flags,
        uint timeout,
        out UIntPtr result
    );
}
'@

[UIntPtr] $result = [UIntPtr]::Zero
$sent = [AutoEmailSenderEnvironment]::SendMessageTimeout(
    [IntPtr] 0xffff,
    0x001a,
    [UIntPtr]::Zero,
    'Environment',
    0x0002,
    5000,
    [ref] $result
)
if ($sent -eq [IntPtr]::Zero) {
    exit 1
}
`;

export async function ensureMacPath(
  paths: ShellPaths,
  environmentPath: string,
): Promise<boolean> {
  if (paths.shellProfilePath === null) {
    return false;
  }
  let content = "";
  try {
    content = await readFile(paths.shellProfilePath, "utf8");
  } catch {
    // A missing shell profile is created below.
  }
  if (content.includes(ZSH_PATH_BLOCK_START)) {
    return true;
  }
  if (hasPathEntry(environmentPath, paths.commandDirectory, ":")) {
    return false;
  }
  const updated = addManagedZshPathBlock(content);
  if (updated !== content) {
    await writeFile(paths.shellProfilePath, updated, {
      encoding: "utf8",
      mode: 0o600,
    });
    await chmod(paths.shellProfilePath, 0o600);
  }
  return true;
}

export async function ensureWindowsPath(
  commandDirectory: string,
  previouslyManaged: boolean,
  readPath: () => Promise<string>,
  writePath: (value: string) => Promise<void>,
  processEnvironment: NodeJS.ProcessEnv,
  broadcastEnvironmentChange: () => Promise<void>,
): Promise<boolean> {
  const currentPath = await readPath();
  if (hasPathEntry(currentPath, commandDirectory, ";")) {
    synchronizeWindowsProcessPath(processEnvironment, commandDirectory, true);
    return previouslyManaged;
  }
  await writePath(addPathEntry(currentPath, commandDirectory, ";"));
  synchronizeWindowsProcessPath(processEnvironment, commandDirectory, true);
  await broadcastWindowsEnvironmentChangeSafely(broadcastEnvironmentChange);
  return true;
}

export function synchronizeWindowsProcessPath(
  environment: NodeJS.ProcessEnv,
  commandDirectory: string,
  enabled: boolean,
): void {
  const pathKey =
    Object.keys(environment).find((key) => key.toLowerCase() === "path") ??
    "PATH";
  const currentPath = environment[pathKey] ?? "";
  environment[pathKey] = enabled
    ? addPathEntry(currentPath, commandDirectory, ";")
    : removePathEntry(currentPath, commandDirectory, ";");
}

export async function broadcastWindowsEnvironmentChangeSafely(
  broadcastEnvironmentChange: () => Promise<void>,
): Promise<void> {
  try {
    await broadcastEnvironmentChange();
  } catch {
    // The registry update remains valid even if Windows cannot notify existing shell processes.
  }
}

export async function macPathIsConfigured(
  paths: ShellPaths,
  environmentPath: string,
): Promise<boolean> {
  if (hasPathEntry(environmentPath, paths.commandDirectory, ":")) {
    return true;
  }
  if (paths.shellProfilePath === null) {
    return false;
  }
  try {
    return (await readFile(paths.shellProfilePath, "utf8")).includes(
      ZSH_PATH_BLOCK_START,
    );
  } catch {
    return false;
  }
}

export async function removeMacPathBlock(profilePath: string): Promise<void> {
  let content: string;
  try {
    content = await readFile(profilePath, "utf8");
  } catch {
    return;
  }
  const updated = removeManagedZshPathBlock(content);
  if (updated !== content) {
    await writeFile(profilePath, updated, { encoding: "utf8", mode: 0o600 });
  }
}

export function addManagedZshPathBlock(content: string): string {
  if (content.includes(ZSH_PATH_BLOCK_START)) {
    return content;
  }
  const prefix =
    content.length === 0 || content.endsWith("\n") ? content : `${content}\n`;
  return (
    `${prefix}${ZSH_PATH_BLOCK_START}\n` +
    'export PATH="$HOME/.local/bin:$PATH"\n' +
    `${ZSH_PATH_BLOCK_END}\n`
  );
}

export function removeManagedZshPathBlock(content: string): string {
  const escapedStart = escapeRegExp(ZSH_PATH_BLOCK_START);
  const escapedEnd = escapeRegExp(ZSH_PATH_BLOCK_END);
  return content.replace(
    new RegExp(`(?:^|\\n)${escapedStart}\\n[\\s\\S]*?${escapedEnd}\\n?`, "g"),
    (match) => (match.startsWith("\n") ? "\n" : ""),
  );
}

export function addPathEntry(
  value: string,
  entry: string,
  delimiter: ":" | ";",
): string {
  if (hasPathEntry(value, entry, delimiter)) {
    return value;
  }
  return [...splitPath(value, delimiter), entry].join(delimiter);
}

export function removePathEntry(
  value: string,
  entry: string,
  delimiter: ":" | ";",
): string {
  const target = normalizePathEntry(entry, delimiter);
  return splitPath(value, delimiter)
    .filter((item) => normalizePathEntry(item, delimiter) !== target)
    .join(delimiter);
}

export function hasPathEntry(
  value: string,
  entry: string,
  delimiter: ":" | ";",
): boolean {
  const target = normalizePathEntry(entry, delimiter);
  return splitPath(value, delimiter).some(
    (item) => normalizePathEntry(item, delimiter) === target,
  );
}

export function splitPath(value: string, delimiter: ":" | ";"): string[] {
  return value
    .split(delimiter)
    .map((item) => item.trim())
    .filter(Boolean);
}

export function normalizePathEntry(
  value: string,
  delimiter: ":" | ";",
): string {
  const normalized = value.trim().replace(/[\\/]+$/, "");
  return delimiter === ";" ? normalized.toLowerCase() : normalized;
}

export async function readWindowsUserPath(): Promise<string> {
  try {
    const result = await execFileAsync(
      "reg.exe",
      ["query", "HKCU\\Environment", "/v", "Path"],
      { windowsHide: true, encoding: "utf8" },
    );
    return /^\s*Path\s+REG_\w+\s+(.*?)\s*$/im.exec(result.stdout)?.[1] ?? "";
  } catch {
    return "";
  }
}

export async function writeWindowsUserPath(value: string): Promise<void> {
  await execFileAsync(
    "reg.exe",
    [
      "add",
      "HKCU\\Environment",
      "/v",
      "Path",
      "/t",
      "REG_EXPAND_SZ",
      "/d",
      value,
      "/f",
    ],
    { windowsHide: true },
  );
}

export async function broadcastWindowsEnvironmentChange(): Promise<void> {
  const encodedCommand = Buffer.from(
    WINDOWS_ENVIRONMENT_CHANGE_SCRIPT,
    "utf16le",
  ).toString("base64");
  await execFileAsync(
    "powershell.exe",
    [
      "-NoLogo",
      "-NoProfile",
      "-NonInteractive",
      "-EncodedCommand",
      encodedCommand,
    ],
    { windowsHide: true, timeout: 15_000 },
  );
}

export function escapeRegExp(value: string): string {
  return value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}
