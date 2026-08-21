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
import type {
  DesktopAgentIntegrationId as AgentIntegrationId,
  DesktopAgentIntegrationStatus as AgentIntegrationStatus,
  DesktopAgentSupportEnableOptions as AgentSupportEnableOptions,
  DesktopAgentSupportStatus as AgentSupportStatus,
} from "../../../../contracts/desktop-ipc.js";

const execFileAsync = promisify(execFile);
export const AGENT_SUPPORT_MANIFEST_SCHEMA_VERSION = 5;
const MANIFEST_SCHEMA_VERSION = AGENT_SUPPORT_MANIFEST_SCHEMA_VERSION;
const PREVIOUS_MANIFEST_SCHEMA_VERSION = 4;
const OLDER_MANIFEST_SCHEMA_VERSION = 3;
const LEGACY_MANIFEST_SCHEMA_VERSION = 2;
const EARLIEST_MANIFEST_SCHEMA_VERSION = 1;
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

const AGENT_INTEGRATION_IDS = [
  "codex",
  "claude_code",
  "cursor",
  "copilot_cli",
] as const satisfies readonly AgentIntegrationId[];

type AgentDefinition = {
  id: AgentIntegrationId;
  name: string;
  relativeSkillPath: readonly string[];
  sharedSkillFrom?: AgentIntegrationId;
};

const AGENT_INTEGRATIONS: readonly AgentDefinition[] = [
  {
    id: "codex",
    name: "Codex",
    relativeSkillPath: [".agents", "skills", "auto-email-sender"],
  },
  {
    id: "claude_code",
    name: "Claude Code",
    relativeSkillPath: [".claude", "skills", "auto-email-sender"],
  },
  {
    id: "cursor",
    name: "Cursor",
    relativeSkillPath: [".cursor", "skills", "auto-email-sender"],
    sharedSkillFrom: "codex",
  },
  {
    id: "copilot_cli",
    name: "GitHub Copilot CLI",
    relativeSkillPath: [".copilot", "skills", "auto-email-sender"],
    sharedSkillFrom: "codex",
  },
];

type ManagedAgentSkill = {
  skill_target: string;
  skill_sha256: string | null;
};

type AgentSupportManifest = {
  schema_version: number;
  enabled: boolean;
  prompt_dismissed: boolean;
  app_version: string | null;
  cli_source: string | null;
  skill_source: string | null;
  cli_target: string;
  cli_sha256: string | null;
  path_managed: boolean;
  agents: Partial<Record<AgentIntegrationId, ManagedAgentSkill>>;
  updated_at: string;
};

export type AgentSupportPaths = {
  cliBundleSource: string;
  cliSource: string;
  cliTarget: string;
  legacyCliTarget: string;
  skillSource: string;
  /** Legacy alias for the Codex-compatible shared Agent Skills directory. */
  skillTarget: string;
  agentSkillTargets: Record<AgentIntegrationId, string>;
  manifestPath: string;
  shellProfilePath: string | null;
  commandDirectory: string;
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
  environmentPath?: string;
  processEnvironment?: NodeJS.ProcessEnv;
  readWindowsUserPath?: () => Promise<string>;
  writeWindowsUserPath?: (value: string) => Promise<void>;
  broadcastWindowsEnvironmentChange?: () => Promise<void>;
  detectAgentInstallation?: (agentId: AgentIntegrationId) => Promise<boolean>;
  writeManifest?: (targetPath: string, value: object) => Promise<void>;
  now?: () => Date;
};

type ManagedPathChange = {
  commit: () => Promise<void>;
  rollback: () => Promise<void>;
};

export function resolveAgentSupportPaths(options: AgentSupportServiceOptions): AgentSupportPaths {
  const executableName = options.platform === "win32" ? "auto-email-sender.exe" : "auto-email-sender";
  const cliBundleSource = options.isPackaged
    ? path.join(options.resourcesPath, "cli")
    : path.join(options.repoRoot, "cli", "dist", "auto-email-sender");
  const cliSource = path.join(cliBundleSource, executableName);
  const skillSource = options.isPackaged
    ? path.join(options.resourcesPath, "agent-support", "skills", "auto-email-sender")
    : path.join(options.repoRoot, "agent-support", "skills", "auto-email-sender");
  const commandDirectory = options.platform === "win32"
    ? path.join(
        options.localAppDataPath ?? path.join(options.homePath, "AppData", "Local"),
        "AutoEmailSender",
        "bin",
      )
    : path.join(options.homePath, ".local", "bin");
  const agentSkillTargets = Object.fromEntries(
    AGENT_INTEGRATIONS.map((agent) => [
      agent.id,
      path.join(options.homePath, ...agent.relativeSkillPath),
    ]),
  ) as Record<AgentIntegrationId, string>;

  return {
    cliBundleSource,
    cliSource,
    cliTarget: path.join(
      commandDirectory,
      options.platform === "win32" ? "auto-email-sender.cmd" : executableName,
    ),
    legacyCliTarget: path.join(commandDirectory, executableName),
    skillSource,
    skillTarget: agentSkillTargets.codex,
    agentSkillTargets,
    manifestPath: path.join(options.userDataPath, "agent", "installation.json"),
    shellProfilePath: options.platform === "darwin" ? path.join(options.homePath, ".zshrc") : null,
    commandDirectory,
  };
}

export function createAgentSupportService(options: AgentSupportServiceOptions) {
  const paths = resolveAgentSupportPaths(options);
  const now = options.now ?? (() => new Date());
  const readWindowsPath = options.readWindowsUserPath ?? readWindowsUserPath;
  const writeWindowsPath = options.writeWindowsUserPath ?? writeWindowsUserPath;
  const processEnvironment = options.processEnvironment ?? process.env;
  const broadcastWindowsChange = options.broadcastWindowsEnvironmentChange
    ?? broadcastWindowsEnvironmentChange;
  const detectAgentInstallation = options.detectAgentInstallation
    ?? ((agentId: AgentIntegrationId) => detectInstalledAgent(agentId, options));
  const writeManifest = options.writeManifest ?? writeJsonAtomic;

  const getStatus = async (): Promise<AgentSupportStatus> => {
    const manifest = await readManifest(paths.manifestPath);
    const agents = await getAgentStatuses(paths, manifest, detectAgentInstallation);
    const unsupportedReason = getUnsupportedReason(options);
    if (unsupportedReason !== null) {
      return buildStatus("unsupported", unsupportedReason, false, agents);
    }
    if (!(await hasRequiredSourceFiles(paths))) {
      return buildStatus("unsupported", getMissingAgentSupportFilesMessage(options), false, agents);
    }

    const onboardingPending = manifest === null || !manifest.prompt_dismissed;
    const cliConflict = await findUnmanagedCliConflict(paths, manifest);
    if (cliConflict !== null) {
      return buildStatus(
        "needs_repair",
        `发现不是本软件管理的同名命令：${cliConflict}。为避免覆盖你的文件，尚未修改。`,
        onboardingPending,
        agents,
      );
    }
    if (!manifest?.enabled) {
      return buildStatus(
        "not_enabled",
        "启用后可使用命令行，并选择要接入的本地 Agent。",
        onboardingPending,
        agents,
      );
    }

    const installationHealthy = await isInstallationHealthy({
      options,
      paths,
      manifest,
      readWindowsPath,
    });
    if (!installationHealthy) {
      const hasSkillUpdate = agents.some((agent) => agent.state === "needs_update");
      return buildStatus(
        "needs_repair",
        hasSkillUpdate
          ? "部分 Agent 使用说明需要更新。重新安装会恢复官方版本；软件启动时也会自动更新。"
          : "部分命令行文件、版本或 PATH 配置不完整，请点击“重新安装”。",
        false,
        agents,
      );
    }

    const hasInstalledAgent = agents.some((agent) => agent.state === "installed");
    return buildStatus(
      "enabled",
      hasInstalledAgent
        ? "命令行已启用，已安装的 Agent 使用说明会随软件更新自动更新。"
        : "命令行已启用。请从下方选择要接入的 Agent。",
      false,
      agents,
    );
  };

  const enable = async (request: AgentSupportEnableOptions = {}): Promise<AgentSupportStatus> => {
    const previousManifest = await readManifest(paths.manifestPath);
    if (previousManifest?.enabled) {
      throw new Error("命令行已启用；如需重新安装，请使用“重新安装”。");
    }
    const status = await installCliSupport(previousManifest);
    const codex = status.agents.find((agent) => agent.id === "codex");
    if (
      request.installDetectedAgents !== true
      || codex?.detected !== true
      || codex.state !== "not_installed"
    ) {
      return status;
    }
    return installAgentSkill("codex");
  };

  const repair = async (): Promise<AgentSupportStatus> => {
    const previousManifest = await readManifest(paths.manifestPath);
    if (!previousManifest?.enabled) {
      return installCliSupport(previousManifest);
    }
    return installManagedSupport(previousManifest);
  };

  const installAgentSkill = async (agentId: AgentIntegrationId): Promise<AgentSupportStatus> => {
    const agent = getAgentDefinition(agentId);
    const previousManifest = await readManifest(paths.manifestPath);
    if (!previousManifest?.enabled) {
      throw new Error("请先启用命令行，再安装 Agent 使用说明。");
    }
    await ensureSupportedAndSourceAvailable();

    const target = paths.agentSkillTargets[agent.id];
    const existing = previousManifest.agents[agent.id];
    const isManaged = isManagedAgentSkillTarget(existing, target);
    if (await pathExists(target) && !isManaged) {
      throw new Error(`Skill 目标已存在且不属于本软件：${target}`);
    }

    const replacement = await installSkill(paths.skillSource, target, isManaged);
    const agentIds = new Set(getManagedAgentIds(paths, previousManifest));
    agentIds.add(agent.id);
    try {
      await writeEnabledManifest(previousManifest.path_managed, [...agentIds]);
    } catch (error) {
      await rollbackManagedPathChanges([replacement], error);
    }
    await replacement.commit();
    return getStatus();
  };

  const uninstallAgentSkill = async (agentId: AgentIntegrationId): Promise<AgentSupportStatus> => {
    const agent = getAgentDefinition(agentId);
    const previousManifest = await readManifest(paths.manifestPath);
    if (!previousManifest?.enabled) {
      throw new Error("命令行尚未启用。");
    }

    const target = paths.agentSkillTargets[agent.id];
    if (!isManagedAgentSkillTarget(previousManifest.agents[agent.id], target)) {
      throw new Error("该 Agent 的 Skill 不是本软件安装的，无法卸载。");
    }
    await rm(target, { recursive: true, force: true });
    const agentIds = getManagedAgentIds(paths, previousManifest)
      .filter((managedAgentId) => managedAgentId !== agent.id);
    await writeEnabledManifest(previousManifest.path_managed, agentIds);
    return getStatus();
  };

  const disable = async (): Promise<AgentSupportStatus> => {
    const manifest = await readManifest(paths.manifestPath);
    const managedCliTarget = getManagedCliTargetPath(manifest, paths);
    if (managedCliTarget !== null) {
      await rm(managedCliTarget, { recursive: true, force: true });
    }
    if (manifest?.enabled) {
      for (const agentId of getManagedAgentIds(paths, manifest)) {
        await rm(paths.agentSkillTargets[agentId], { recursive: true, force: true });
      }
    }
    if (manifest?.enabled && manifest.path_managed) {
      if (options.platform === "darwin" && paths.shellProfilePath !== null) {
        await removeMacPathBlock(paths.shellProfilePath);
      } else if (options.platform === "win32") {
        const currentPath = await readWindowsPath();
        const updatedPath = removePathEntry(currentPath, paths.commandDirectory, ";");
        if (updatedPath !== currentPath) {
          await writeWindowsPath(updatedPath);
        }
        synchronizeWindowsProcessPath(processEnvironment, paths.commandDirectory, false);
        if (updatedPath !== currentPath) {
          await broadcastWindowsEnvironmentChangeSafely(broadcastWindowsChange);
        }
      }
    }

    await writeDisabledManifest(true);
    return getStatus();
  };

  const dismissOnboarding = async (): Promise<AgentSupportStatus> => {
    const current = await readManifest(paths.manifestPath);
    if (current?.enabled) {
      await writeEnabledManifest(current.path_managed, getManagedAgentIds(paths, current));
    } else {
      await writeDisabledManifest(true);
    }
    return getStatus();
  };

  const synchronize = async (): Promise<AgentSupportStatus> => {
    const manifest = await readManifest(paths.manifestPath);
    if (!manifest?.enabled) {
      return getStatus();
    }
    if (await findUnmanagedCliConflict(paths, manifest)) {
      return getStatus();
    }
    if (!(await hasRequiredSourceFiles(paths))) {
      return getStatus();
    }
    if (await isInstallationHealthy({ options, paths, manifest, readWindowsPath })) {
      if (options.platform === "win32") {
        synchronizeWindowsProcessPath(processEnvironment, paths.commandDirectory, true);
      }
      return getStatus();
    }

    // All managed files are official product files. Restore them silently on startup/update.
    return installManagedSupport(manifest);
  };

  async function ensureSupportedAndSourceAvailable(): Promise<void> {
    const unsupportedReason = getUnsupportedReason(options);
    if (unsupportedReason !== null) {
      throw new Error(unsupportedReason);
    }
    if (!(await hasRequiredSourceFiles(paths))) {
      throw new Error(getMissingAgentSupportFilesMessage(options));
    }
  }

  async function installCliSupport(previousManifest: AgentSupportManifest | null): Promise<AgentSupportStatus> {
    await ensureSupportedAndSourceAvailable();
    const cliConflict = await findUnmanagedCliConflict(paths, previousManifest);
    if (cliConflict !== null) {
      throw new Error(`为避免覆盖你的文件，无法安装：${cliConflict}`);
    }

    const replacements = await installCli(
      paths,
      options.platform,
      getManagedCliTargetPath(previousManifest, paths),
    );
    try {
      const pathManaged = options.platform === "darwin"
        ? await ensureMacPath(paths, options.environmentPath ?? process.env.PATH ?? "")
        : await ensureWindowsPath(
            paths.commandDirectory,
            Boolean(previousManifest?.enabled && previousManifest.path_managed),
            readWindowsPath,
            writeWindowsPath,
            processEnvironment,
            broadcastWindowsChange,
          );
      await writeEnabledManifest(pathManaged, []);
    } catch (error) {
      await rollbackManagedPathChanges(replacements, error);
    }
    await commitManagedPathChanges(replacements);
    return getStatus();
  }

  async function installManagedSupport(previousManifest: AgentSupportManifest): Promise<AgentSupportStatus> {
    await ensureSupportedAndSourceAvailable();
    const cliConflict = await findUnmanagedCliConflict(paths, previousManifest);
    if (cliConflict !== null) {
      throw new Error(`为避免覆盖你的文件，无法更新：${cliConflict}`);
    }

    const replacements = await installCli(
      paths,
      options.platform,
      getManagedCliTargetPath(previousManifest, paths),
    );
    const agentIds = getManagedAgentIds(paths, previousManifest);
    try {
      for (const agentId of agentIds) {
        replacements.push(
          await installSkill(paths.skillSource, paths.agentSkillTargets[agentId], true),
        );
      }
      const pathManaged = options.platform === "darwin"
        ? await ensureMacPath(paths, options.environmentPath ?? process.env.PATH ?? "")
        : await ensureWindowsPath(
            paths.commandDirectory,
            previousManifest.path_managed,
            readWindowsPath,
            writeWindowsPath,
            processEnvironment,
            broadcastWindowsChange,
          );
      await writeEnabledManifest(pathManaged, agentIds);
    } catch (error) {
      await rollbackManagedPathChanges(replacements, error);
    }
    await commitManagedPathChanges(replacements);
    return getStatus();
  }

  async function writeEnabledManifest(
    pathManaged: boolean,
    agentIds: AgentIntegrationId[],
  ): Promise<void> {
    const skillSha256 = await sha256Directory(paths.skillSource);
    const agents = Object.fromEntries(
      agentIds.map((agentId) => [
        agentId,
        {
          skill_target: path.resolve(paths.agentSkillTargets[agentId]),
          skill_sha256: skillSha256,
        } satisfies ManagedAgentSkill,
      ]),
    ) as AgentSupportManifest["agents"];
    await writeManifest(paths.manifestPath, {
      schema_version: MANIFEST_SCHEMA_VERSION,
      enabled: true,
      prompt_dismissed: true,
      app_version: options.appVersion,
      cli_source: path.resolve(paths.cliSource),
      skill_source: path.resolve(paths.skillSource),
      cli_target: path.resolve(paths.cliTarget),
      cli_sha256: await sha256Directory(paths.cliBundleSource),
      path_managed: pathManaged,
      agents,
      updated_at: now().toISOString(),
    } satisfies AgentSupportManifest);
  }

  async function writeDisabledManifest(
    promptDismissed: boolean,
  ): Promise<void> {
    await writeManifest(paths.manifestPath, {
      schema_version: MANIFEST_SCHEMA_VERSION,
      enabled: false,
      prompt_dismissed: promptDismissed,
      app_version: null,
      cli_source: path.resolve(paths.cliSource),
      skill_source: path.resolve(paths.skillSource),
      cli_target: path.resolve(paths.cliTarget),
      cli_sha256: null,
      path_managed: false,
      agents: {},
      updated_at: now().toISOString(),
    } satisfies AgentSupportManifest);
  }

  function buildStatus(
    state: AgentSupportStatus["state"],
    message: string,
    onboardingPending: boolean,
    agents: AgentIntegrationStatus[],
  ): AgentSupportStatus {
    return {
      supported: state !== "unsupported",
      state,
      message,
      onboardingPending,
      cliCommand: "auto-email-sender",
      cliPath: paths.cliTarget,
      skillPath: paths.skillTarget,
      agents,
      appVersion: options.appVersion,
      requiresAgentRestart: state === "enabled",
    };
  }

  return {
    getStatus,
    enable,
    repair,
    disable,
    dismissOnboarding,
    synchronize,
    installAgentSkill,
    uninstallAgentSkill,
    paths,
  };
}

async function hasRequiredSourceFiles(paths: AgentSupportPaths): Promise<boolean> {
  return (await pathExists(paths.cliSource))
    && (await pathIsDirectory(path.join(paths.cliBundleSource, "_internal")))
    && (await pathExists(path.join(paths.skillSource, "SKILL.md")));
}

async function installCli(
  paths: AgentSupportPaths,
  platform: NodeJS.Platform,
  managedTarget: string | null,
): Promise<ManagedPathChange[]> {
  await mkdir(path.dirname(paths.cliTarget), { recursive: true });
  if (await pathExists(paths.cliTarget)) {
    if (managedTarget !== paths.cliTarget) {
      throw new Error(`命令目标已存在且不属于本软件：${paths.cliTarget}`);
    }
  }
  const temporaryPath = `${paths.cliTarget}.${randomUUID()}.tmp`;
  try {
    if (platform === "darwin") {
      await symlink(path.resolve(paths.cliSource), temporaryPath);
    } else {
      await writeFile(temporaryPath, windowsCliLauncherContent(paths.cliSource), {
        encoding: "utf8",
        flag: "wx",
      });
    }
    const changes = [await replaceManagedPath(temporaryPath, paths.cliTarget)];
    if (managedTarget !== null && managedTarget !== paths.cliTarget) {
      try {
        changes.push(await stageManagedPathRemoval(managedTarget));
      } catch (error) {
        await rollbackManagedPathChanges(changes, error);
      }
    }
    return changes;
  } finally {
    await rm(temporaryPath, { force: true }).catch(() => undefined);
  }
}

function windowsCliLauncherContent(cliSource: string): string {
  const escapedSource = path.resolve(cliSource).replaceAll("%", "%%");
  return `@echo off\r\n"${escapedSource}" %*\r\nexit /b %ERRORLEVEL%\r\n`;
}

async function installSkill(
  sourcePath: string,
  targetPath: string,
  allowManagedReplacement: boolean,
): Promise<ManagedPathChange> {
  await mkdir(path.dirname(targetPath), { recursive: true });
  if (await pathExists(targetPath)) {
    if (!allowManagedReplacement) {
      throw new Error(`Skill 目标已存在且不属于本软件：${targetPath}`);
    }
  }
  const temporaryPath = `${targetPath}.${randomUUID()}.tmp`;
  try {
    await cp(sourcePath, temporaryPath, { recursive: true, force: false });
    return await replaceManagedPath(temporaryPath, targetPath);
  } finally {
    await rm(temporaryPath, { recursive: true, force: true }).catch(() => undefined);
  }
}

async function replaceManagedPath(
  temporaryPath: string,
  targetPath: string,
): Promise<ManagedPathChange> {
  const backupPath = `${targetPath}.${randomUUID()}.rollback`;
  const hadPrevious = await pathExists(targetPath);
  if (hadPrevious) {
    await rename(targetPath, backupPath);
  }
  try {
    await rename(temporaryPath, targetPath);
  } catch (error) {
    if (hadPrevious) {
      await rename(backupPath, targetPath).catch(() => undefined);
    }
    throw error;
  }
  let settled = false;
  return {
    commit: async () => {
      if (settled) return;
      settled = true;
      if (hadPrevious) {
        await rm(backupPath, { recursive: true, force: true });
      }
    },
    rollback: async () => {
      if (settled) return;
      settled = true;
      await rm(targetPath, { recursive: true, force: true });
      if (hadPrevious) {
        await rename(backupPath, targetPath);
      }
    },
  };
}

async function stageManagedPathRemoval(targetPath: string): Promise<ManagedPathChange> {
  const backupPath = `${targetPath}.${randomUUID()}.rollback`;
  await rename(targetPath, backupPath);
  let settled = false;
  return {
    commit: async () => {
      if (settled) return;
      settled = true;
      await rm(backupPath, { recursive: true, force: true });
    },
    rollback: async () => {
      if (settled) return;
      settled = true;
      await rename(backupPath, targetPath);
    },
  };
}

async function commitManagedPathChanges(changes: ManagedPathChange[]): Promise<void> {
  for (const change of changes) {
    await change.commit();
  }
}

async function rollbackManagedPathChanges(
  changes: ManagedPathChange[],
  originalError: unknown,
): Promise<never> {
  const rollbackErrors: unknown[] = [];
  for (const change of [...changes].reverse()) {
    try {
      await change.rollback();
    } catch (error) {
      rollbackErrors.push(error);
    }
  }
  if (rollbackErrors.length > 0) {
    throw new AggregateError(
      [originalError, ...rollbackErrors],
      "Agent 支持安装失败，且部分文件无法回滚。",
    );
  }
  throw originalError;
}

async function isInstallationHealthy(input: {
  options: AgentSupportServiceOptions;
  paths: AgentSupportPaths;
  manifest: AgentSupportManifest;
  readWindowsPath: () => Promise<string>;
}): Promise<boolean> {
  const { manifest, options, paths } = input;
  if (
    manifest.schema_version !== MANIFEST_SCHEMA_VERSION
    || manifest.app_version !== options.appVersion
    || manifest.cli_source !== path.resolve(paths.cliSource)
    || manifest.cli_target !== path.resolve(paths.cliTarget)
    || manifest.skill_source !== path.resolve(paths.skillSource)
    || manifest.cli_sha256 === null
    || !(await pathExists(paths.cliTarget))
    || !(await directoryFingerprintMatches(paths.cliBundleSource, manifest.cli_sha256))
  ) {
    return false;
  }

  for (const agentId of getManagedAgentIds(paths, manifest)) {
    if (!(await isManagedAgentSkillHealthy(paths, manifest, agentId))) {
      return false;
    }
  }

  if (options.platform === "darwin") {
    try {
      const targetStats = await lstat(paths.cliTarget);
      return targetStats.isSymbolicLink()
        && path.resolve(path.dirname(paths.cliTarget), await readlink(paths.cliTarget))
          === path.resolve(paths.cliSource)
        && await macPathIsConfigured(paths, options.environmentPath ?? process.env.PATH ?? "");
    } catch {
      return false;
    }
  }
  try {
    return (await lstat(paths.cliTarget)).isFile()
      && await fileTextMatches(paths.cliTarget, windowsCliLauncherContent(paths.cliSource))
      && hasPathEntry(await input.readWindowsPath(), paths.commandDirectory, ";");
  } catch {
    return false;
  }
}

async function getAgentStatuses(
  paths: AgentSupportPaths,
  manifest: AgentSupportManifest | null,
  detectAgentInstallation: (agentId: AgentIntegrationId) => Promise<boolean>,
): Promise<AgentIntegrationStatus[]> {
  const sourceSha256 = await sha256Directory(paths.skillSource).catch(() => null);
  const statuses: AgentIntegrationStatus[] = [];

  for (const agent of AGENT_INTEGRATIONS) {
    const target = paths.agentSkillTargets[agent.id];
    const record = manifest?.agents[agent.id];
    const detected = await detectAgentInstallation(agent.id);
    if (isManagedAgentSkillTarget(record, target)) {
      const healthy = manifest?.schema_version === MANIFEST_SCHEMA_VERSION
        && sourceSha256 !== null
        && record.skill_sha256 === sourceSha256
        && await directoryFingerprintMatches(target, sourceSha256);
      statuses.push({
        id: agent.id,
        name: agent.name,
        detected,
        state: healthy ? "installed" : "needs_update",
        skillPath: target,
        message: healthy ? "已安装官方 Skill" : "需要更新为当前官方 Skill",
      });
      continue;
    }
    if (await pathExists(target)) {
      statuses.push({
        id: agent.id,
        name: agent.name,
        detected,
        state: "conflict",
        skillPath: target,
        message: "发现同名目录，未覆盖",
      });
      continue;
    }
    statuses.push({
      id: agent.id,
      name: agent.name,
      detected,
      state: "not_installed",
      skillPath: target,
      message: "可单独安装",
    });
  }

  const codexStatus = statuses.find((status) => status.id === "codex");
  return statuses.map((status) => {
    const definition = getAgentDefinition(status.id);
    if (
      status.state !== "not_installed"
      || definition.sharedSkillFrom === undefined
      || codexStatus?.state !== "installed"
    ) {
      return status;
    }
    return {
      ...status,
      state: "available_via_shared",
      message: "可通过已安装的共享 Skill 使用",
      sharedBy: definition.sharedSkillFrom,
    };
  });
}

async function isManagedAgentSkillHealthy(
  paths: AgentSupportPaths,
  manifest: AgentSupportManifest,
  agentId: AgentIntegrationId,
): Promise<boolean> {
  const record = manifest.agents[agentId];
  const target = paths.agentSkillTargets[agentId];
  if (
    !isManagedAgentSkillTarget(record, target)
    || record.skill_sha256 === null
    || !(await pathExists(path.join(target, "SKILL.md")))
  ) {
    return false;
  }
  return (await directoryFingerprintMatches(target, record.skill_sha256))
    && await directoryFingerprintMatches(paths.skillSource, record.skill_sha256);
}

function getManagedAgentIds(
  paths: AgentSupportPaths,
  manifest: AgentSupportManifest | null,
): AgentIntegrationId[] {
  if (!manifest?.enabled) {
    return [];
  }
  return AGENT_INTEGRATION_IDS.filter((agentId) =>
    isManagedAgentSkillTarget(manifest.agents[agentId], paths.agentSkillTargets[agentId]),
  );
}

function getManagedCliTargetPath(
  manifest: AgentSupportManifest | null,
  paths: AgentSupportPaths,
): string | null {
  if (!manifest?.enabled) {
    return null;
  }
  const target = path.resolve(manifest.cli_target);
  if (target === path.resolve(paths.cliTarget)) {
    return paths.cliTarget;
  }
  if (
    manifest.schema_version < MANIFEST_SCHEMA_VERSION
    && target === path.resolve(paths.legacyCliTarget)
  ) {
    return paths.legacyCliTarget;
  }
  return null;
}

function isManagedAgentSkillTarget(
  record: ManagedAgentSkill | undefined,
  target: string,
): record is ManagedAgentSkill {
  return record !== undefined && record.skill_target === path.resolve(target);
}

async function findUnmanagedCliConflict(
  paths: AgentSupportPaths,
  manifest: AgentSupportManifest | null,
): Promise<string | null> {
  const managedTarget = getManagedCliTargetPath(manifest, paths);
  for (const candidate of new Set([paths.cliTarget, paths.legacyCliTarget])) {
    if (await pathExists(candidate) && path.resolve(candidate) !== path.resolve(managedTarget ?? "")) {
      return candidate;
    }
  }
  return null;
}

function getMissingAgentSupportFilesMessage(options: AgentSupportServiceOptions): string {
  if (options.isPackaged) {
    return "当前安装包缺少命令行或 Agent 使用说明文件，请安装完整版本。";
  }
  const manualCommand = options.platform === "win32"
    ? "pwsh -NoProfile -File scripts/build-cli.ps1 -Clean"
    : "bash scripts/build-cli.sh --clean";
  return `开发版命令行尚未构建，或 Agent 使用说明文件缺失。请重新运行 desktop 目录中的 npm run dev；也可以在仓库根目录执行 ${manualCommand} 后重启桌面开发版。`;
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

async function ensureMacPath(paths: AgentSupportPaths, environmentPath: string): Promise<boolean> {
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

function synchronizeWindowsProcessPath(
  environment: NodeJS.ProcessEnv,
  commandDirectory: string,
  enabled: boolean,
): void {
  const pathKey = Object.keys(environment).find((key) => key.toLowerCase() === "path") ?? "PATH";
  const currentPath = environment[pathKey] ?? "";
  environment[pathKey] = enabled
    ? addPathEntry(currentPath, commandDirectory, ";")
    : removePathEntry(currentPath, commandDirectory, ";");
}

async function broadcastWindowsEnvironmentChangeSafely(
  broadcastEnvironmentChange: () => Promise<void>,
): Promise<void> {
  try {
    await broadcastEnvironmentChange();
  } catch {
    // The registry update remains valid even if Windows cannot notify existing shell processes.
  }
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
    `${prefix}${ZSH_PATH_BLOCK_START}\n`
    + 'export PATH="$HOME/.local/bin:$PATH"\n'
    + `${ZSH_PATH_BLOCK_END}\n`
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

async function broadcastWindowsEnvironmentChange(): Promise<void> {
  const encodedCommand = Buffer.from(WINDOWS_ENVIRONMENT_CHANGE_SCRIPT, "utf16le").toString("base64");
  await execFileAsync(
    "powershell.exe",
    ["-NoLogo", "-NoProfile", "-NonInteractive", "-EncodedCommand", encodedCommand],
    { windowsHide: true, timeout: 15_000 },
  );
}

async function readManifest(manifestPath: string): Promise<AgentSupportManifest | null> {
  try {
    const value = JSON.parse(await readFile(manifestPath, "utf8")) as Record<string, unknown>;
    const schemaVersion = value.schema_version;
    if (
      (schemaVersion !== MANIFEST_SCHEMA_VERSION
        && schemaVersion !== PREVIOUS_MANIFEST_SCHEMA_VERSION
        && schemaVersion !== OLDER_MANIFEST_SCHEMA_VERSION
        && schemaVersion !== LEGACY_MANIFEST_SCHEMA_VERSION
        && schemaVersion !== EARLIEST_MANIFEST_SCHEMA_VERSION)
      || typeof value.enabled !== "boolean"
      || typeof value.prompt_dismissed !== "boolean"
      || typeof value.cli_target !== "string"
      || typeof value.path_managed !== "boolean"
    ) {
      return null;
    }

    const agents = schemaVersion >= PREVIOUS_MANIFEST_SCHEMA_VERSION
      ? readManagedAgents(value.agents)
      : readLegacyManagedAgents(value);
    return {
      schema_version: schemaVersion,
      enabled: value.enabled,
      prompt_dismissed: value.prompt_dismissed,
      app_version: typeof value.app_version === "string" ? value.app_version : null,
      cli_source: typeof value.cli_source === "string" ? value.cli_source : null,
      skill_source: typeof value.skill_source === "string" ? value.skill_source : null,
      cli_target: value.cli_target,
      cli_sha256: isSha256(value.cli_sha256) ? value.cli_sha256 : null,
      path_managed: value.path_managed,
      agents,
      updated_at: typeof value.updated_at === "string" ? value.updated_at : "",
    };
  } catch {
    return null;
  }
}

function readManagedAgents(value: unknown): AgentSupportManifest["agents"] {
  if (value === null || typeof value !== "object" || Array.isArray(value)) {
    return {};
  }
  const candidates = value as Record<string, unknown>;
  const agents: AgentSupportManifest["agents"] = {};
  for (const agentId of AGENT_INTEGRATION_IDS) {
    const candidate = candidates[agentId];
    if (candidate === null || typeof candidate !== "object" || Array.isArray(candidate)) {
      continue;
    }
    const record = candidate as Record<string, unknown>;
    if (typeof record.skill_target !== "string") {
      continue;
    }
    agents[agentId] = {
      skill_target: record.skill_target,
      skill_sha256: isSha256(record.skill_sha256) ? record.skill_sha256 : null,
    };
  }
  return agents;
}

function readLegacyManagedAgents(value: Record<string, unknown>): AgentSupportManifest["agents"] {
  if (typeof value.skill_target !== "string") {
    return {};
  }
  return {
    codex: {
      skill_target: value.skill_target,
      skill_sha256: isSha256(value.skill_sha256) ? value.skill_sha256 : null,
    },
  };
}

function getAgentDefinition(agentId: AgentIntegrationId): AgentDefinition {
  const agent = AGENT_INTEGRATIONS.find((candidate) => candidate.id === agentId);
  if (agent === undefined) {
    throw new Error(`不支持的 Agent：${agentId}`);
  }
  return agent;
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

async function fileTextMatches(targetPath: string, expected: string): Promise<boolean> {
  try {
    return await readFile(targetPath, "utf8") === expected;
  } catch {
    return false;
  }
}

async function directoryFingerprintMatches(targetPath: string, expected: string): Promise<boolean> {
  try {
    return (await lstat(targetPath)).isDirectory() && await sha256Directory(targetPath) === expected;
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

async function detectInstalledAgent(
  agentId: AgentIntegrationId,
  options: AgentSupportServiceOptions,
): Promise<boolean> {
  if (agentId !== "codex") {
    return false;
  }
  if (await pathExists(path.join(options.homePath, ".codex"))) {
    return true;
  }
  if (options.platform === "win32") {
    const localAppDataPath = options.localAppDataPath
      ?? path.join(options.homePath, "AppData", "Local");
    const knownWindowsPaths = [
      path.join(localAppDataPath, "Microsoft", "WindowsApps", "codex.exe"),
      path.join(localAppDataPath, "Programs", "Codex", "Codex.exe"),
    ];
    if ((await Promise.all(knownWindowsPaths.map(pathExists))).some(Boolean)) {
      return true;
    }
    const systemRoot = options.processEnvironment?.SystemRoot
      ?? process.env.SystemRoot
      ?? "C:\\Windows";
    return commandSucceeds(path.join(systemRoot, "System32", "where.exe"), ["codex"]);
  }
  if (options.platform === "darwin") {
    const knownMacPaths = [
      "/Applications/Codex.app",
      path.join(options.homePath, "Applications", "Codex.app"),
    ];
    if ((await Promise.all(knownMacPaths.map(pathExists))).some(Boolean)) {
      return true;
    }
    return commandSucceeds("/usr/bin/which", ["codex"]);
  }
  return false;
}

async function commandSucceeds(command: string, args: string[]): Promise<boolean> {
  try {
    await execFileAsync(command, args, { windowsHide: true, timeout: 3_000 });
    return true;
  } catch {
    return false;
  }
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

async function pathIsDirectory(targetPath: string): Promise<boolean> {
  try {
    return (await lstat(targetPath)).isDirectory();
  } catch {
    return false;
  }
}

function escapeRegExp(value: string): string {
  return value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}
