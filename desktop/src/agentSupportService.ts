import { execFile } from "node:child_process";
import { createHash, randomUUID } from "node:crypto";
import { createReadStream } from "node:fs";
import {
  chmod,
  cp,
  lstat,
  mkdir,
  readdir,
  readFile,
  readlink,
  rename,
  rm,
  symlink,
  writeFile,
} from "node:fs/promises";
import path from "node:path";
import { promisify } from "node:util";
import type { AgentSupportStatus } from "./types.js";

const execFileAsync = promisify(execFile);
const MANIFEST_SCHEMA_VERSION = 2;
const LEGACY_MANIFEST_SCHEMA_VERSION = 1;
const ZSH_PATH_BLOCK_START = "# >>> Auto Email Sender Agent support >>>";
const ZSH_PATH_BLOCK_END = "# <<< Auto Email Sender Agent support <<<";

type AgentSupportManifest = {
  schema_version: number;
  enabled: boolean;
  prompt_dismissed: boolean;
  app_version: string | null;
  desktop_executable: string;
  cli_source: string | null;
  cli_target: string;
  skill_target: string;
  cli_sha256: string | null;
  skill_sha256: string | null;
  path_managed: boolean;
  last_backup_directory: string | null;
  updated_at: string;
};

type ManagedContentKind = "cli" | "skill";

type ManagedModification = {
  kind: ManagedContentKind;
  reason: "content_changed" | "ownership_unknown" | "unexpected_type";
};

export type AgentSupportPaths = {
  cliSource: string;
  cliTarget: string;
  skillSource: string;
  skillTarget: string;
  manifestPath: string;
  shellProfilePath: string | null;
  commandDirectory: string;
  backupDirectory: string;
};

export type AgentSupportServiceOptions = {
  platform: NodeJS.Platform;
  arch: string;
  isPackaged: boolean;
  resourcesPath: string;
  repoRoot: string;
  userDataPath: string;
  homePath: string;
  localAppDataPath?: string;
  appVersion: string;
  desktopExecutablePath: string;
  environmentPath?: string;
  readWindowsUserPath?: () => Promise<string>;
  writeWindowsUserPath?: (value: string) => Promise<void>;
  now?: () => Date;
};

export function resolveAgentSupportPaths(options: AgentSupportServiceOptions): AgentSupportPaths {
  const executableName = options.platform === "win32" ? "auto-email-sender.exe" : "auto-email-sender";
  const cliSource = options.isPackaged
    ? path.join(options.resourcesPath, "cli", executableName)
    : path.join(options.repoRoot, "cli", "dist", executableName);
  const skillSource = options.isPackaged
    ? path.join(options.resourcesPath, "agent-support", "skills", "auto-email-sender")
    : path.join(options.repoRoot, "agent-support", "skills", "auto-email-sender");
  const commandDirectory =
    options.platform === "win32"
      ? path.join(
          options.localAppDataPath ?? path.join(options.homePath, "AppData", "Local"),
          "AutoEmailSender",
          "bin",
        )
      : path.join(options.homePath, ".local", "bin");
  return {
    cliSource,
    cliTarget: path.join(commandDirectory, executableName),
    skillSource,
    skillTarget: path.join(options.homePath, ".agents", "skills", "auto-email-sender"),
    manifestPath: path.join(options.userDataPath, "agent", "installation.json"),
    shellProfilePath: options.platform === "darwin" ? path.join(options.homePath, ".zshrc") : null,
    commandDirectory,
    backupDirectory: path.join(options.userDataPath, "agent", "backups"),
  };
}

export function createAgentSupportService(options: AgentSupportServiceOptions) {
  const paths = resolveAgentSupportPaths(options);
  const now = options.now ?? (() => new Date());
  const readWindowsPath = options.readWindowsUserPath ?? readWindowsUserPath;
  const writeWindowsPath = options.writeWindowsUserPath ?? writeWindowsUserPath;

  const getStatus = async (): Promise<AgentSupportStatus> => {
    const unsupportedReason = getUnsupportedReason(options);
    if (unsupportedReason !== null) {
      return buildStatus("unsupported", unsupportedReason, false);
    }
    if (!(await pathExists(paths.cliSource)) || !(await pathExists(path.join(paths.skillSource, "SKILL.md")))) {
      return buildStatus(
        "unsupported",
        "当前安装包缺少命令行或 Agent 使用说明文件，请安装完整版本。",
        false,
      );
    }

    const manifest = await readManifest(paths.manifestPath);
    const onboardingPending = manifest === null || !manifest.prompt_dismissed;
    const conflicts = await findUnmanagedConflicts(paths, manifest);
    if (conflicts.length > 0) {
      return buildStatus(
        "needs_repair",
        `发现不是本软件管理的同名文件：${conflicts.join("、")}。为避免覆盖你的文件，尚未修改。`,
        onboardingPending,
      );
    }
    if (!manifest?.enabled) {
      return buildStatus(
        "not_enabled",
        "启用后，本地 Agent 可以通过命令行按你的要求操作软件。",
        onboardingPending,
      );
    }

    const modifications = await findManagedModifications(options, paths, manifest);
    if (modifications.length > 0) {
      return buildStatus(
        "needs_repair",
        buildModifiedContentMessage(modifications),
        false,
      );
    }

    const installationHealthy = await isInstallationHealthy({
      options,
      paths,
      manifest,
      readWindowsPath,
    });
    return installationHealthy
      ? buildStatus(
          "enabled",
          "命令行与 Agent 使用说明已安装。新开的 Agent 对话可直接使用。",
          false,
        )
      : buildStatus(
          "needs_repair",
          "部分安装文件、版本或 PATH 配置不完整，请点击“修复”。",
          false,
        );
  };

  const enable = async (): Promise<AgentSupportStatus> => {
    const previousManifest = await readManifest(paths.manifestPath);
    if (previousManifest?.enabled) {
      throw new Error("命令行与 Agent 支持已经启用；如需重新安装，请使用“修复”。");
    }
    return installManagedSupport(previousManifest, false);
  };

  const repair = async (): Promise<AgentSupportStatus> => {
    const previousManifest = await readManifest(paths.manifestPath);
    if (!previousManifest?.enabled) {
      const conflicts = await findUnmanagedConflicts(paths, previousManifest);
      if (conflicts.length > 0) {
        throw new Error(`为避免覆盖你的文件，无法修复：${conflicts.join("、")}`);
      }
      return installManagedSupport(previousManifest, false);
    }

    const modifications = await findManagedModifications(options, paths, previousManifest);
    const backupDirectory = modifications.length > 0
      ? await backupManagedModifications(paths, modifications, now())
      : null;
    const status = await installManagedSupport(previousManifest, true, backupDirectory);
    if (backupDirectory === null) {
      return status;
    }
    return {
      ...status,
      message: `已修复；原有修改内容已备份到 ${backupDirectory}。`,
    };
  };

  const disable = async (): Promise<AgentSupportStatus> => {
    const manifest = await readManifest(paths.manifestPath);
    const ownsTargets = manifest?.enabled
      && manifest.cli_target === path.resolve(paths.cliTarget)
      && manifest.skill_target === path.resolve(paths.skillTarget);
    const modifications = ownsTargets
      ? await findManagedModifications(options, paths, manifest)
      : [];
    const backupDirectory = modifications.length > 0
      ? await backupManagedModifications(paths, modifications, now())
      : null;

    if (ownsTargets && manifest.cli_target === path.resolve(paths.cliTarget)) {
      await rm(paths.cliTarget, { force: true });
    }
    if (ownsTargets && manifest.skill_target === path.resolve(paths.skillTarget)) {
      await rm(paths.skillTarget, { recursive: true, force: true });
    }
    if (ownsTargets && manifest.path_managed) {
      if (options.platform === "darwin" && paths.shellProfilePath !== null) {
        await removeMacPathBlock(paths.shellProfilePath);
      } else if (options.platform === "win32") {
        const currentPath = await readWindowsPath();
        const updatedPath = removePathEntry(currentPath, paths.commandDirectory, ";");
        if (updatedPath !== currentPath) {
          await writeWindowsPath(updatedPath);
        }
      }
    }

    await writeJsonAtomic(paths.manifestPath, {
      schema_version: MANIFEST_SCHEMA_VERSION,
      enabled: false,
      prompt_dismissed: true,
      app_version: null,
      desktop_executable: path.resolve(options.desktopExecutablePath),
      cli_source: path.resolve(paths.cliSource),
      cli_target: path.resolve(paths.cliTarget),
      skill_target: path.resolve(paths.skillTarget),
      cli_sha256: null,
      skill_sha256: null,
      path_managed: false,
      last_backup_directory: backupDirectory ?? manifest?.last_backup_directory ?? null,
      updated_at: now().toISOString(),
    } satisfies AgentSupportManifest);
    const status = await getStatus();
    if (backupDirectory === null || status.state !== "not_enabled") {
      return status;
    }
    return {
      ...status,
      message: `已关闭；原有修改内容已备份到 ${backupDirectory}。`,
    };
  };

  const dismissOnboarding = async (): Promise<AgentSupportStatus> => {
    const current = await readManifest(paths.manifestPath);
    await writeJsonAtomic(paths.manifestPath, {
      schema_version: MANIFEST_SCHEMA_VERSION,
      enabled: current?.enabled ?? false,
      prompt_dismissed: true,
      app_version: current?.app_version ?? null,
      desktop_executable: current?.desktop_executable ?? path.resolve(options.desktopExecutablePath),
      cli_source: current?.cli_source ?? path.resolve(paths.cliSource),
      cli_target: current?.cli_target ?? path.resolve(paths.cliTarget),
      skill_target: current?.skill_target ?? path.resolve(paths.skillTarget),
      cli_sha256: current?.cli_sha256 ?? null,
      skill_sha256: current?.skill_sha256 ?? null,
      path_managed: current?.path_managed ?? false,
      last_backup_directory: current?.last_backup_directory ?? null,
      updated_at: now().toISOString(),
    } satisfies AgentSupportManifest);
    return getStatus();
  };

  const synchronize = async (): Promise<AgentSupportStatus> => {
    const manifest = await readManifest(paths.manifestPath);
    if (!manifest?.enabled) {
      return getStatus();
    }
    const conflicts = await findUnmanagedConflicts(paths, manifest);
    if (conflicts.length > 0) {
      return getStatus();
    }
    const modifications = await findManagedModifications(options, paths, manifest);
    if (modifications.length > 0) {
      return buildStatus("needs_repair", buildModifiedContentMessage(modifications), false);
    }
    if (await isInstallationHealthy({ options, paths, manifest, readWindowsPath })) {
      return getStatus();
    }
    return installManagedSupport(manifest, true);
  };

  async function installManagedSupport(
    previousManifest: AgentSupportManifest | null,
    allowManagedReplacement: boolean,
    backupDirectory: string | null = null,
  ): Promise<AgentSupportStatus> {
    const unsupportedReason = getUnsupportedReason(options);
    if (unsupportedReason !== null) {
      return buildStatus("unsupported", unsupportedReason, false);
    }
    if (!(await pathExists(paths.cliSource)) || !(await pathExists(path.join(paths.skillSource, "SKILL.md")))) {
      throw new Error("当前安装包缺少命令行或 Agent 使用说明文件。");
    }

    const conflicts = await findUnmanagedConflicts(paths, previousManifest);
    if (conflicts.length > 0) {
      throw new Error(`为避免覆盖你的文件，无法安装：${conflicts.join("、")}`);
    }

    await installCli(paths, options.platform, allowManagedReplacement ? previousManifest : null);
    await installSkill(paths, allowManagedReplacement ? previousManifest : null);
    const pathManaged = options.platform === "darwin"
      ? await ensureMacPath(
          paths,
          options.environmentPath ?? process.env.PATH ?? "",
        )
      : await ensureWindowsPath(
          paths.commandDirectory,
          previousManifest?.enabled ? previousManifest.path_managed : false,
          readWindowsPath,
          writeWindowsPath,
        );
    const manifest: AgentSupportManifest = {
      schema_version: MANIFEST_SCHEMA_VERSION,
      enabled: true,
      prompt_dismissed: true,
      app_version: options.appVersion,
      desktop_executable: path.resolve(options.desktopExecutablePath),
      cli_source: path.resolve(paths.cliSource),
      cli_target: path.resolve(paths.cliTarget),
      skill_target: path.resolve(paths.skillTarget),
      cli_sha256: await sha256File(paths.cliTarget),
      skill_sha256: await sha256Directory(paths.skillTarget),
      path_managed: pathManaged,
      last_backup_directory: backupDirectory ?? previousManifest?.last_backup_directory ?? null,
      updated_at: now().toISOString(),
    };
    await writeJsonAtomic(paths.manifestPath, manifest);
    return getStatus();
  }

  function buildStatus(
    state: AgentSupportStatus["state"],
    message: string,
    onboardingPending: boolean,
  ): AgentSupportStatus {
    return {
      supported: state !== "unsupported",
      state,
      message,
      onboardingPending,
      cliCommand: "auto-email-sender",
      cliPath: paths.cliTarget,
      skillPath: paths.skillTarget,
      appVersion: options.appVersion,
      requiresAgentRestart: state === "enabled",
    };
  }

  return { getStatus, enable, repair, disable, dismissOnboarding, synchronize, paths };
}

async function installCli(
  paths: AgentSupportPaths,
  platform: NodeJS.Platform,
  manifest: AgentSupportManifest | null,
): Promise<void> {
  await mkdir(path.dirname(paths.cliTarget), { recursive: true });
  if (await pathExists(paths.cliTarget)) {
    if (!manifest?.enabled || manifest.cli_target !== path.resolve(paths.cliTarget)) {
      throw new Error(`命令目标已存在且不属于本软件：${paths.cliTarget}`);
    }
    await rm(paths.cliTarget, { force: true });
  }
  if (platform === "darwin") {
    await symlink(path.resolve(paths.cliSource), paths.cliTarget);
    return;
  }
  const temporaryPath = `${paths.cliTarget}.${randomUUID()}.tmp`;
  try {
    await cp(paths.cliSource, temporaryPath, { force: false });
    await rename(temporaryPath, paths.cliTarget);
  } finally {
    await rm(temporaryPath, { force: true }).catch(() => undefined);
  }
}

async function installSkill(
  paths: AgentSupportPaths,
  manifest: AgentSupportManifest | null,
): Promise<void> {
  await mkdir(path.dirname(paths.skillTarget), { recursive: true });
  if (await pathExists(paths.skillTarget)) {
    if (!manifest?.enabled || manifest.skill_target !== path.resolve(paths.skillTarget)) {
      throw new Error(`Skill 目标已存在且不属于本软件：${paths.skillTarget}`);
    }
    await rm(paths.skillTarget, { recursive: true, force: true });
  }
  const temporaryPath = `${paths.skillTarget}.${randomUUID()}.tmp`;
  try {
    await cp(paths.skillSource, temporaryPath, { recursive: true, force: false });
    await rename(temporaryPath, paths.skillTarget);
  } finally {
    await rm(temporaryPath, { recursive: true, force: true }).catch(() => undefined);
  }
}

async function isInstallationHealthy(input: {
  options: AgentSupportServiceOptions;
  paths: AgentSupportPaths;
  manifest: AgentSupportManifest;
  readWindowsPath: () => Promise<string>;
}): Promise<boolean> {
  if (
    input.manifest.app_version !== input.options.appVersion ||
    input.manifest.desktop_executable !== path.resolve(input.options.desktopExecutablePath) ||
    input.manifest.cli_source !== path.resolve(input.paths.cliSource) ||
    input.manifest.cli_sha256 === null ||
    input.manifest.skill_sha256 === null ||
    !(await pathExists(input.paths.cliTarget)) ||
    !(await pathExists(path.join(input.paths.skillTarget, "SKILL.md")))
  ) {
    return false;
  }
  if (
    !(await fileFingerprintMatches(input.paths.cliTarget, input.manifest.cli_sha256)) ||
    !(await fileFingerprintMatches(input.paths.cliSource, input.manifest.cli_sha256)) ||
    !(await directoryFingerprintMatches(input.paths.skillTarget, input.manifest.skill_sha256)) ||
    !(await directoryFingerprintMatches(input.paths.skillSource, input.manifest.skill_sha256))
  ) {
    return false;
  }
  if (input.options.platform === "darwin") {
    try {
      const targetStats = await lstat(input.paths.cliTarget);
      return targetStats.isSymbolicLink()
        && path.resolve(path.dirname(input.paths.cliTarget), await readlink(input.paths.cliTarget))
          === path.resolve(input.paths.cliSource)
        && await macPathIsConfigured(input.paths, input.options.environmentPath ?? process.env.PATH ?? "");
    } catch {
      return false;
    }
  }
  try {
    return (await lstat(input.paths.cliTarget)).isFile()
      && hasPathEntry(await input.readWindowsPath(), input.paths.commandDirectory, ";");
  } catch {
    return false;
  }
}

async function findManagedModifications(
  options: AgentSupportServiceOptions,
  paths: AgentSupportPaths,
  manifest: AgentSupportManifest,
): Promise<ManagedModification[]> {
  const modifications: ManagedModification[] = [];
  if (await pathExists(paths.cliTarget)) {
    let expectedInstallationType = false;
    try {
      const stats = await lstat(paths.cliTarget);
      if (options.platform === "darwin") {
        if (stats.isSymbolicLink()) {
          const linkTarget = path.resolve(
            path.dirname(paths.cliTarget),
            await readlink(paths.cliTarget),
          );
          expectedInstallationType = linkTarget === path.resolve(paths.cliSource)
            || (manifest.cli_source !== null && linkTarget === path.resolve(manifest.cli_source));
        }
      } else {
        expectedInstallationType = stats.isFile();
      }
    } catch {
      expectedInstallationType = false;
    }
    if (!expectedInstallationType) {
      modifications.push({ kind: "cli", reason: "unexpected_type" });
    } else if (options.platform !== "darwin") {
      if (manifest.cli_sha256 === null) {
        modifications.push({ kind: "cli", reason: "ownership_unknown" });
      } else if (!(await fileFingerprintMatches(paths.cliTarget, manifest.cli_sha256))) {
        modifications.push({ kind: "cli", reason: "content_changed" });
      }
    }
  }

  if (await pathExists(paths.skillTarget)) {
    let isDirectory = false;
    try {
      isDirectory = (await lstat(paths.skillTarget)).isDirectory();
    } catch {
      isDirectory = false;
    }
    if (!isDirectory) {
      modifications.push({ kind: "skill", reason: "unexpected_type" });
    } else if (manifest.skill_sha256 === null) {
      modifications.push({ kind: "skill", reason: "ownership_unknown" });
    } else if (!(await directoryFingerprintMatches(paths.skillTarget, manifest.skill_sha256))) {
      modifications.push({ kind: "skill", reason: "content_changed" });
    }
  }
  return modifications;
}

function buildModifiedContentMessage(modifications: ManagedModification[]): string {
  const labels = new Set(
    modifications.map((modification) =>
      modification.kind === "skill" ? "Agent 使用说明（Skill）" : "命令行工具",
    ),
  );
  const hasUnknownOwnership = modifications.some(
    (modification) => modification.reason === "ownership_unknown",
  );
  const detail = hasUnknownOwnership ? "无法确认原有内容是否被修改" : "检测到内容已被修改";
  return `${[...labels].join("和")}${detail}。自动更新不会覆盖；点击“修复”时会先备份现有内容。`;
}

async function backupManagedModifications(
  paths: AgentSupportPaths,
  modifications: ManagedModification[],
  timestamp: Date,
): Promise<string> {
  const safeTimestamp = timestamp.toISOString().replace(/[:.]/g, "-");
  const backupDirectory = path.join(paths.backupDirectory, `${safeTimestamp}-${randomUUID()}`);
  await mkdir(backupDirectory, { recursive: true, mode: 0o700 });
  for (const modification of modifications) {
    const source = modification.kind === "skill" ? paths.skillTarget : paths.cliTarget;
    if (!(await pathExists(source))) {
      continue;
    }
    const target = path.join(
      backupDirectory,
      modification.kind === "skill" ? "auto-email-sender-skill" : path.basename(paths.cliTarget),
    );
    await copyPathForBackup(source, target);
  }
  return backupDirectory;
}

async function copyPathForBackup(source: string, target: string): Promise<void> {
  const stats = await lstat(source);
  if (!stats.isSymbolicLink()) {
    await cp(source, target, { recursive: stats.isDirectory(), force: false });
    return;
  }

  const linkTarget = await readlink(source);
  await writeFile(`${target}.symlink.txt`, `${linkTarget}\n`, { encoding: "utf8", flag: "wx" });
  try {
    await cp(source, target, { recursive: true, dereference: true, force: false });
  } catch (error) {
    if (await pathExists(path.resolve(path.dirname(source), linkTarget))) {
      throw error;
    }
  }
}

async function findUnmanagedConflicts(
  paths: AgentSupportPaths,
  manifest: AgentSupportManifest | null,
): Promise<string[]> {
  const conflicts: string[] = [];
  if (
    await pathExists(paths.cliTarget) &&
    (!manifest?.enabled || manifest.cli_target !== path.resolve(paths.cliTarget))
  ) {
    conflicts.push(paths.cliTarget);
  }
  if (
    await pathExists(paths.skillTarget) &&
    (!manifest?.enabled || manifest.skill_target !== path.resolve(paths.skillTarget))
  ) {
    conflicts.push(paths.skillTarget);
  }
  return conflicts;
}

function getUnsupportedReason(options: AgentSupportServiceOptions): string | null {
  if (options.platform === "darwin" && options.arch === "arm64") {
    return null;
  }
  if (options.platform === "win32" && options.arch === "x64") {
    return null;
  }
  if (options.platform === "darwin") {
    return "当前仅支持 Apple 芯片 Mac，暂不支持 Intel Mac。";
  }
  return "当前系统暂不支持命令行与 Agent 功能。";
}

async function ensureMacPath(
  paths: AgentSupportPaths,
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
    await writeFile(paths.shellProfilePath, updated, { encoding: "utf8", mode: 0o600 });
    await chmod(paths.shellProfilePath, 0o600);
  }
  return true;
}

async function ensureWindowsPath(
  commandDirectory: string,
  previouslyManaged: boolean,
  readPath: () => Promise<string>,
  writePath: (value: string) => Promise<void>,
): Promise<boolean> {
  const currentPath = await readPath();
  if (hasPathEntry(currentPath, commandDirectory, ";")) {
    return previouslyManaged;
  }
  await writePath(addPathEntry(currentPath, commandDirectory, ";"));
  return true;
}

async function macPathIsConfigured(paths: AgentSupportPaths, environmentPath: string): Promise<boolean> {
  if (hasPathEntry(environmentPath, paths.commandDirectory, ":")) {
    return true;
  }
  if (paths.shellProfilePath === null) {
    return false;
  }
  try {
    return (await readFile(paths.shellProfilePath, "utf8")).includes(ZSH_PATH_BLOCK_START);
  } catch {
    return false;
  }
}

async function removeMacPathBlock(profilePath: string): Promise<void> {
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
  const prefix = content.length === 0 || content.endsWith("\n") ? content : `${content}\n`;
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

export function addPathEntry(value: string, entry: string, delimiter: ":" | ";"): string {
  if (hasPathEntry(value, entry, delimiter)) {
    return value;
  }
  return [...splitPath(value, delimiter), entry].join(delimiter);
}

export function removePathEntry(value: string, entry: string, delimiter: ":" | ";"): string {
  const target = normalizePathEntry(entry, delimiter);
  return splitPath(value, delimiter)
    .filter((item) => normalizePathEntry(item, delimiter) !== target)
    .join(delimiter);
}

function hasPathEntry(value: string, entry: string, delimiter: ":" | ";"): boolean {
  const target = normalizePathEntry(entry, delimiter);
  return splitPath(value, delimiter).some(
    (item) => normalizePathEntry(item, delimiter) === target,
  );
}

function splitPath(value: string, delimiter: ":" | ";"): string[] {
  return value.split(delimiter).map((item) => item.trim()).filter(Boolean);
}

function normalizePathEntry(value: string, delimiter: ":" | ";"): string {
  const normalized = value.trim().replace(/[\\/]+$/, "");
  return delimiter === ";" ? normalized.toLowerCase() : normalized;
}

async function readWindowsUserPath(): Promise<string> {
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

async function writeWindowsUserPath(value: string): Promise<void> {
  await execFileAsync(
    "reg.exe",
    ["add", "HKCU\\Environment", "/v", "Path", "/t", "REG_EXPAND_SZ", "/d", value, "/f"],
    { windowsHide: true },
  );
}

async function readManifest(manifestPath: string): Promise<AgentSupportManifest | null> {
  try {
    const value = JSON.parse(await readFile(manifestPath, "utf8")) as Partial<AgentSupportManifest>;
    if (
      (value.schema_version !== MANIFEST_SCHEMA_VERSION
        && value.schema_version !== LEGACY_MANIFEST_SCHEMA_VERSION) ||
      typeof value.enabled !== "boolean" ||
      typeof value.prompt_dismissed !== "boolean" ||
      typeof value.desktop_executable !== "string" ||
      typeof value.cli_target !== "string" ||
      typeof value.skill_target !== "string" ||
      typeof value.path_managed !== "boolean"
    ) {
      return null;
    }
    return {
      schema_version: value.schema_version,
      enabled: value.enabled,
      prompt_dismissed: value.prompt_dismissed,
      app_version: typeof value.app_version === "string" ? value.app_version : null,
      desktop_executable: value.desktop_executable,
      cli_source: typeof value.cli_source === "string" ? value.cli_source : null,
      cli_target: value.cli_target,
      skill_target: value.skill_target,
      cli_sha256: isSha256(value.cli_sha256) ? value.cli_sha256 : null,
      skill_sha256: isSha256(value.skill_sha256) ? value.skill_sha256 : null,
      path_managed: value.path_managed,
      last_backup_directory:
        typeof value.last_backup_directory === "string" ? value.last_backup_directory : null,
      updated_at: typeof value.updated_at === "string" ? value.updated_at : "",
    };
  } catch {
    return null;
  }
}

async function sha256File(targetPath: string): Promise<string> {
  const hash = createHash("sha256");
  await new Promise<void>((resolve, reject) => {
    const stream = createReadStream(targetPath);
    stream.on("data", (chunk) => hash.update(chunk));
    stream.on("error", reject);
    stream.on("end", resolve);
  });
  return hash.digest("hex");
}

async function sha256Directory(directoryPath: string): Promise<string> {
  const entries: string[] = [];

  const visit = async (relativeDirectory: string): Promise<void> => {
    const absoluteDirectory = relativeDirectory
      ? path.join(directoryPath, ...relativeDirectory.split("/"))
      : directoryPath;
    const children = await readdir(absoluteDirectory, { withFileTypes: true });
    children.sort((left, right) => compareOrdinal(left.name, right.name));
    for (const child of children) {
      const relativePath = relativeDirectory ? `${relativeDirectory}/${child.name}` : child.name;
      const absolutePath = path.join(absoluteDirectory, child.name);
      if (child.isDirectory()) {
        entries.push(`D\t${relativePath}`);
        await visit(relativePath);
      } else if (child.isFile()) {
        entries.push(`F\t${relativePath}\t${await sha256File(absolutePath)}`);
      } else if (child.isSymbolicLink()) {
        entries.push(`L\t${relativePath}\t${await readlink(absolutePath)}`);
      } else {
        entries.push(`O\t${relativePath}`);
      }
    }
  };

  await visit("");
  const canonicalListing = entries.length > 0 ? `${entries.join("\n")}\n` : "";
  return createHash("sha256").update(canonicalListing, "utf8").digest("hex");
}

async function fileFingerprintMatches(targetPath: string, expected: string): Promise<boolean> {
  try {
    return await sha256File(targetPath) === expected;
  } catch {
    return false;
  }
}

async function directoryFingerprintMatches(targetPath: string, expected: string): Promise<boolean> {
  try {
    return await sha256Directory(targetPath) === expected;
  } catch {
    return false;
  }
}

function compareOrdinal(left: string, right: string): number {
  return left < right ? -1 : left > right ? 1 : 0;
}

function isSha256(value: unknown): value is string {
  return typeof value === "string" && /^[a-f0-9]{64}$/i.test(value);
}

async function writeJsonAtomic(targetPath: string, value: object): Promise<void> {
  await mkdir(path.dirname(targetPath), { recursive: true, mode: 0o700 });
  const temporaryPath = `${targetPath}.${randomUUID()}.tmp`;
  try {
    await writeFile(temporaryPath, `${JSON.stringify(value, null, 2)}\n`, {
      encoding: "utf8",
      mode: 0o600,
      flag: "wx",
    });
    await rename(temporaryPath, targetPath);
    if (process.platform !== "win32") {
      await chmod(targetPath, 0o600);
    }
  } finally {
    await rm(temporaryPath, { force: true }).catch(() => undefined);
  }
}

async function pathExists(targetPath: string): Promise<boolean> {
  try {
    await lstat(targetPath);
    return true;
  } catch {
    return false;
  }
}

function escapeRegExp(value: string): string {
  return value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}
