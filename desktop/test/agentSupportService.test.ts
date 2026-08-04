import { lstat, mkdir, readFile, readlink, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import { mkdtemp } from "node:fs/promises";
import { afterEach, describe, expect, it } from "vitest";
import {
  addManagedZshPathBlock,
  addPathEntry,
  createAgentSupportService,
  removeManagedZshPathBlock,
  removePathEntry,
  resolveAgentSupportPaths,
  type AgentSupportServiceOptions,
} from "../src/agentSupportService.js";

const temporaryDirectories: string[] = [];

async function createFixture(
  platform: NodeJS.Platform = "darwin",
  isPackaged = true,
) {
  const root = await mkdtemp(path.join(tmpdir(), "auto-email-sender-agent-support-"));
  temporaryDirectories.push(root);
  const options: AgentSupportServiceOptions = {
    platform,
    arch: platform === "darwin" ? "arm64" : "x64",
    isPackaged,
    resourcesPath: path.join(root, "resources"),
    repoRoot: path.join(root, "repo"),
    userDataPath: path.join(root, "user-data"),
    homePath: path.join(root, "home"),
    localAppDataPath: path.join(root, "local-app-data"),
    appVersion: "2.4.1",
    environmentPath: platform === "darwin" ? "/usr/bin:/bin" : undefined,
    now: () => new Date("2026-08-04T00:00:00.000Z"),
  };
  const paths = resolveAgentSupportPaths(options);
  await mkdir(path.dirname(paths.cliSource), { recursive: true });
  await writeFile(paths.cliSource, "cli-binary", "utf8");
  await mkdir(paths.skillSource, { recursive: true });
  await writeFile(paths.skillSource + "/SKILL.md", "---\nname: auto-email-sender\n---\n", "utf8");
  return { root, options, paths };
}

async function exists(targetPath: string): Promise<boolean> {
  try {
    await lstat(targetPath);
    return true;
  } catch {
    return false;
  }
}

afterEach(async () => {
  await Promise.all(
    temporaryDirectories.splice(0).map((directory) => rm(directory, { recursive: true, force: true })),
  );
});

describe("Agent support installation", () => {
  it("explains how to recover missing development assets without asking for an installer", async () => {
    const { options, paths } = await createFixture("darwin", false);
    await rm(paths.cliSource, { force: true });
    const service = createAgentSupportService(options);

    const status = await service.getStatus();
    expect(status).toMatchObject({
      state: "unsupported",
      message: expect.stringContaining("重新运行 desktop 目录中的 npm run dev"),
    });
    expect(status.message).toContain("bash scripts/build-cli.sh --clean");
    expect(status.message).not.toContain("请安装完整版本");
    await expect(service.enable()).rejects.toThrow("开发版命令行尚未构建");
  });

  it("enables a managed macOS CLI without selecting an Agent automatically", async () => {
    const { options, paths } = await createFixture("darwin");
    await mkdir(options.homePath, { recursive: true });
    await writeFile(path.join(options.homePath, ".zshrc"), "export EDITOR=vim\n", "utf8");
    const service = createAgentSupportService(options);

    await expect(service.getStatus()).resolves.toMatchObject({
      state: "not_enabled",
      onboardingPending: true,
    });
    await expect(service.enable()).resolves.toMatchObject({ state: "enabled" });

    expect(await readlink(paths.cliTarget)).toBe(path.resolve(paths.cliSource));
    expect(await exists(paths.skillTarget)).toBe(false);
    expect(await readFile(paths.shellProfilePath!, "utf8")).toContain("Auto Email Sender Agent support");
    const manifest = JSON.parse(await readFile(paths.manifestPath, "utf8"));
    expect(manifest).toMatchObject({
      schema_version: 4,
      enabled: true,
      prompt_dismissed: true,
      app_version: "2.4.1",
      cli_source: path.resolve(paths.cliSource),
      skill_source: path.resolve(paths.skillSource),
      agents: {},
    });
    expect(manifest.cli_sha256).toMatch(/^[a-f0-9]{64}$/);
  });

  it("installs each Agent Skill independently and reports shared discovery honestly", async () => {
    const { options, paths } = await createFixture("darwin");
    const service = createAgentSupportService(options);
    await service.enable();

    await expect(service.installAgentSkill("codex")).resolves.toMatchObject({ state: "enabled" });
    const afterCodex = await service.getStatus();
    expect(afterCodex.agents).toEqual(expect.arrayContaining([
      expect.objectContaining({ id: "codex", state: "installed" }),
      expect.objectContaining({ id: "cursor", state: "available_via_shared", sharedBy: "codex" }),
      expect.objectContaining({ id: "copilot_cli", state: "available_via_shared", sharedBy: "codex" }),
      expect.objectContaining({ id: "claude_code", state: "not_installed" }),
    ]));
    expect(await readFile(path.join(paths.agentSkillTargets.codex, "SKILL.md"), "utf8")).toContain(
      "auto-email-sender",
    );

    await service.installAgentSkill("cursor");
    await expect(service.getStatus()).resolves.toMatchObject({
      agents: expect.arrayContaining([expect.objectContaining({ id: "cursor", state: "installed" })]),
    });
    await service.uninstallAgentSkill("cursor");
    expect(await exists(paths.agentSkillTargets.cursor)).toBe(false);
  });

  it("never overwrites an unmanaged command or Agent Skill", async () => {
    const { options, paths } = await createFixture("darwin");
    await mkdir(path.dirname(paths.cliTarget), { recursive: true });
    await writeFile(paths.cliTarget, "user-owned-command", "utf8");
    const service = createAgentSupportService(options);

    await expect(service.getStatus()).resolves.toMatchObject({ state: "needs_repair" });
    await expect(service.enable()).rejects.toThrow("为避免覆盖你的文件");
    expect(await readFile(paths.cliTarget, "utf8")).toBe("user-owned-command");

    await rm(paths.cliTarget, { force: true });
    await service.enable();
    await mkdir(paths.agentSkillTargets.claude_code, { recursive: true });
    await writeFile(path.join(paths.agentSkillTargets.claude_code, "SKILL.md"), "other skill", "utf8");
    await expect(service.getStatus()).resolves.toMatchObject({
      state: "enabled",
      agents: expect.arrayContaining([expect.objectContaining({ id: "claude_code", state: "conflict" })]),
    });
    await expect(service.installAgentSkill("claude_code")).rejects.toThrow("不属于本软件");
    expect(await readFile(path.join(paths.agentSkillTargets.claude_code, "SKILL.md"), "utf8")).toBe("other skill");
  });

  it("copies Windows CLI files and manages only its own user PATH entry", async () => {
    const { options, paths } = await createFixture("win32");
    let userPath = "C:\\Windows\\System32;C:\\Tools";
    const service = createAgentSupportService({
      ...options,
      readWindowsUserPath: async () => userPath,
      writeWindowsUserPath: async (value) => {
        userPath = value;
      },
    });

    await expect(service.enable()).resolves.toMatchObject({ state: "enabled" });
    expect(await readFile(paths.cliTarget, "utf8")).toBe("cli-binary");
    expect(userPath.split(";")).toContain(paths.commandDirectory);

    await expect(service.disable()).resolves.toMatchObject({ state: "not_enabled" });
    expect(userPath).toBe("C:\\Windows\\System32;C:\\Tools");
    expect(await exists(paths.cliTarget)).toBe(false);
  });

  it("silently overwrites product-managed CLI and Skills after an app update", async () => {
    const { options, paths } = await createFixture("darwin");
    const firstService = createAgentSupportService(options);
    await firstService.enable();
    await firstService.installAgentSkill("codex");
    await firstService.installAgentSkill("claude_code");
    await writeFile(path.join(paths.agentSkillTargets.codex, "SKILL.md"), "modified", "utf8");
    await writeFile(path.join(paths.skillSource, "SKILL.md"), "updated skill", "utf8");

    const updatedService = createAgentSupportService({ ...options, appVersion: "2.5.0" });
    await expect(updatedService.getStatus()).resolves.toMatchObject({ state: "needs_repair" });
    await expect(updatedService.synchronize()).resolves.toMatchObject({ state: "enabled" });
    expect(await readFile(path.join(paths.agentSkillTargets.codex, "SKILL.md"), "utf8")).toBe("updated skill");
    expect(await readFile(path.join(paths.agentSkillTargets.claude_code, "SKILL.md"), "utf8")).toBe("updated skill");
    const manifest = JSON.parse(await readFile(paths.manifestPath, "utf8"));
    expect(manifest).toMatchObject({ schema_version: 4, app_version: "2.5.0" });
    expect(manifest).not.toHaveProperty("last_backup_directory");
  });

  it("migrates the legacy shared Skill installation to the Codex integration", async () => {
    const { options, paths } = await createFixture("darwin");
    const service = createAgentSupportService(options);
    await service.enable();
    await service.installAgentSkill("codex");
    const currentManifest = JSON.parse(await readFile(paths.manifestPath, "utf8"));
    await writeFile(paths.manifestPath, `${JSON.stringify({
      ...currentManifest,
      schema_version: 3,
      skill_target: paths.skillTarget,
      skill_sha256: currentManifest.agents.codex.skill_sha256,
      agents: undefined,
      skill_source: undefined,
    }, null, 2)}\n`, "utf8");

    await expect(service.synchronize()).resolves.toMatchObject({ state: "enabled" });
    const migratedManifest = JSON.parse(await readFile(paths.manifestPath, "utf8"));
    expect(migratedManifest.schema_version).toBe(4);
    expect(migratedManifest.agents.codex).toMatchObject({ skill_target: path.resolve(paths.skillTarget) });
  });

  it("removes every product-managed Agent Skill without backups when support is disabled", async () => {
    const { options, paths } = await createFixture("darwin");
    const service = createAgentSupportService(options);
    await service.enable();
    await service.installAgentSkill("codex");
    await service.installAgentSkill("claude_code");

    await expect(service.disable()).resolves.toMatchObject({ state: "not_enabled" });
    expect(await exists(paths.cliTarget)).toBe(false);
    expect(await exists(paths.agentSkillTargets.codex)).toBe(false);
    expect(await exists(paths.agentSkillTargets.claude_code)).toBe(false);
    const manifest = JSON.parse(await readFile(paths.manifestPath, "utf8"));
    expect(manifest).toMatchObject({ schema_version: 4, enabled: false, agents: {} });
    expect(manifest).not.toHaveProperty("last_backup_directory");
  });

  it("preserves a pre-existing Windows PATH entry when support is disabled", async () => {
    const { options, paths } = await createFixture("win32");
    let userPath = `C:\\Windows\\System32;${paths.commandDirectory}`;
    const originalPath = userPath;
    const service = createAgentSupportService({
      ...options,
      readWindowsUserPath: async () => userPath,
      writeWindowsUserPath: async (value) => {
        userPath = value;
      },
    });

    await expect(service.enable()).resolves.toMatchObject({ state: "enabled" });
    expect(JSON.parse(await readFile(paths.manifestPath, "utf8")).path_managed).toBe(false);
    await expect(service.disable()).resolves.toMatchObject({ state: "not_enabled" });
    expect(userPath).toBe(originalPath);
  });
});

describe("managed PATH helpers", () => {
  it("adds and removes only the marked zsh block", () => {
    const original = "export EDITOR=vim\n";
    const installed = addManagedZshPathBlock(original);
    expect(addManagedZshPathBlock(installed)).toBe(installed);
    expect(removeManagedZshPathBlock(installed)).toBe(original);
  });

  it("handles Windows PATH entries case-insensitively without duplicates", () => {
    const current = "C:\\Windows;C:\\Users\\Alice\\bin";
    expect(addPathEntry(current, "c:\\users\\alice\\bin\\", ";")).toBe(current);
    expect(removePathEntry(current, "c:\\USERS\\ALICE\\BIN", ";")).toBe("C:\\Windows");
  });
});
