import { lstat, mkdir, readFile, readdir, readlink, rm, writeFile } from "node:fs/promises";
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

async function createFixture(platform: NodeJS.Platform = "darwin") {
  const root = await mkdtemp(path.join(tmpdir(), "auto-email-sender-agent-support-"));
  temporaryDirectories.push(root);
  const options: AgentSupportServiceOptions = {
    platform,
    arch: platform === "darwin" ? "arm64" : "x64",
    isPackaged: true,
    resourcesPath: path.join(root, "resources"),
    repoRoot: path.join(root, "repo"),
    userDataPath: path.join(root, "user-data"),
    homePath: path.join(root, "home"),
    localAppDataPath: path.join(root, "local-app-data"),
    appVersion: "2.4.1",
    desktopExecutablePath: path.join(root, platform === "win32" ? "Auto Email Sender.exe" : "Auto Email Sender"),
    environmentPath: platform === "darwin" ? "/usr/bin:/bin" : undefined,
    now: () => new Date("2026-08-03T00:00:00.000Z"),
  };
  const paths = resolveAgentSupportPaths(options);
  await mkdir(path.dirname(paths.cliSource), { recursive: true });
  await writeFile(paths.cliSource, "cli-binary", "utf8");
  await mkdir(paths.skillSource, { recursive: true });
  await writeFile(path.join(paths.skillSource, "SKILL.md"), "---\nname: auto-email-sender\n---\n", "utf8");
  await mkdir(path.dirname(options.desktopExecutablePath), { recursive: true });
  await writeFile(options.desktopExecutablePath, "desktop", "utf8");
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
    temporaryDirectories.splice(0).map((directory) =>
      rm(directory, { recursive: true, force: true }),
    ),
  );
});

describe("Agent support installation", () => {
  it("enables and disables a managed macOS CLI, Skill, and PATH block", async () => {
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
    expect(await readFile(path.join(paths.skillTarget, "SKILL.md"), "utf8")).toContain(
      "auto-email-sender",
    );
    expect(await readFile(paths.shellProfilePath!, "utf8")).toContain(
      "Auto Email Sender Agent support",
    );
    const manifest = JSON.parse(await readFile(paths.manifestPath, "utf8"));
    expect(manifest).toMatchObject({
      schema_version: 2,
      enabled: true,
      prompt_dismissed: true,
      app_version: "2.4.1",
      desktop_executable: path.resolve(options.desktopExecutablePath),
      cli_source: path.resolve(paths.cliSource),
    });
    expect(manifest.cli_sha256).toMatch(/^[a-f0-9]{64}$/);
    expect(manifest.skill_sha256).toMatch(/^[a-f0-9]{64}$/);

    await expect(service.disable()).resolves.toMatchObject({ state: "not_enabled" });
    expect(await exists(paths.cliTarget)).toBe(false);
    expect(await exists(paths.skillTarget)).toBe(false);
    expect(await readFile(paths.shellProfilePath!, "utf8")).toBe("export EDITOR=vim\n");
  });

  it("never overwrites an unmanaged command", async () => {
    const { options, paths } = await createFixture("darwin");
    await mkdir(path.dirname(paths.cliTarget), { recursive: true });
    await writeFile(paths.cliTarget, "user-owned-command", "utf8");
    const service = createAgentSupportService(options);

    await expect(service.getStatus()).resolves.toMatchObject({ state: "needs_repair" });
    await expect(service.enable()).rejects.toThrow("为避免覆盖你的文件");
    expect(await readFile(paths.cliTarget, "utf8")).toBe("user-owned-command");
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

  it("refreshes product-managed files automatically after an app update", async () => {
    const { options, paths } = await createFixture("darwin");
    const firstService = createAgentSupportService(options);
    await firstService.enable();
    await writeFile(path.join(paths.skillSource, "SKILL.md"), "updated skill", "utf8");

    const updatedService = createAgentSupportService({ ...options, appVersion: "2.5.0" });
    await expect(updatedService.getStatus()).resolves.toMatchObject({ state: "needs_repair" });
    await expect(updatedService.synchronize()).resolves.toMatchObject({ state: "enabled" });
    expect(await readFile(path.join(paths.skillTarget, "SKILL.md"), "utf8")).toBe("updated skill");
    expect(JSON.parse(await readFile(paths.manifestPath, "utf8")).app_version).toBe("2.5.0");
  });

  it("does not overwrite a user-modified Skill during automatic updates and backs it up before repair", async () => {
    const { options, paths } = await createFixture("darwin");
    const firstService = createAgentSupportService(options);
    await firstService.enable();
    await writeFile(path.join(paths.skillTarget, "SKILL.md"), "user customized skill", "utf8");
    await writeFile(path.join(paths.skillSource, "SKILL.md"), "product skill update", "utf8");

    const updatedService = createAgentSupportService({ ...options, appVersion: "2.5.0" });
    await expect(updatedService.getStatus()).resolves.toMatchObject({
      state: "needs_repair",
      message: expect.stringContaining("自动更新不会覆盖"),
    });
    await expect(updatedService.synchronize()).resolves.toMatchObject({ state: "needs_repair" });
    expect(await readFile(path.join(paths.skillTarget, "SKILL.md"), "utf8")).toBe(
      "user customized skill",
    );

    await expect(updatedService.repair()).resolves.toMatchObject({
      state: "enabled",
      message: expect.stringContaining("已备份"),
    });
    expect(await readFile(path.join(paths.skillTarget, "SKILL.md"), "utf8")).toBe(
      "product skill update",
    );
    const backupEntries = await readdir(paths.backupDirectory);
    expect(backupEntries).toHaveLength(1);
    expect(
      await readFile(
        path.join(paths.backupDirectory, backupEntries[0], "auto-email-sender-skill", "SKILL.md"),
        "utf8",
      ),
    ).toBe("user customized skill");
  });

  it("backs up a modified managed Skill before disabling support", async () => {
    const { options, paths } = await createFixture("darwin");
    const service = createAgentSupportService(options);
    await service.enable();
    await writeFile(path.join(paths.skillTarget, "SKILL.md"), "keep my customization", "utf8");

    await expect(service.disable()).resolves.toMatchObject({
      state: "not_enabled",
      message: expect.stringContaining("已备份"),
    });
    expect(await exists(paths.skillTarget)).toBe(false);
    const backupEntries = await readdir(paths.backupDirectory);
    expect(
      await readFile(
        path.join(paths.backupDirectory, backupEntries[0], "auto-email-sender-skill", "SKILL.md"),
        "utf8",
      ),
    ).toBe("keep my customization");
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

  it("preserves and backs up a modified Windows CLI before manual repair", async () => {
    const { options, paths } = await createFixture("win32");
    let userPath = "C:\\Windows\\System32";
    const service = createAgentSupportService({
      ...options,
      readWindowsUserPath: async () => userPath,
      writeWindowsUserPath: async (value) => {
        userPath = value;
      },
    });
    await service.enable();
    await writeFile(paths.cliTarget, "user customized cli", "utf8");

    await expect(service.synchronize()).resolves.toMatchObject({
      state: "needs_repair",
      message: expect.stringContaining("自动更新不会覆盖"),
    });
    expect(await readFile(paths.cliTarget, "utf8")).toBe("user customized cli");

    await expect(service.repair()).resolves.toMatchObject({ state: "enabled" });
    expect(await readFile(paths.cliTarget, "utf8")).toBe("cli-binary");
    const backupEntries = await readdir(paths.backupDirectory);
    expect(
      await readFile(
        path.join(paths.backupDirectory, backupEntries[0], "auto-email-sender.exe"),
        "utf8",
      ),
    ).toBe("user customized cli");
  });

  it("treats a legacy manifest without fingerprints conservatively", async () => {
    const { options, paths } = await createFixture("darwin");
    const service = createAgentSupportService(options);
    await service.enable();
    const manifest = JSON.parse(await readFile(paths.manifestPath, "utf8"));
    manifest.schema_version = 1;
    delete manifest.cli_source;
    delete manifest.cli_sha256;
    delete manifest.skill_sha256;
    delete manifest.last_backup_directory;
    await writeFile(paths.manifestPath, `${JSON.stringify(manifest, null, 2)}\n`, "utf8");

    await expect(service.synchronize()).resolves.toMatchObject({
      state: "needs_repair",
      message: expect.stringContaining("无法确认"),
    });
    expect(await readFile(path.join(paths.skillTarget, "SKILL.md"), "utf8")).toContain(
      "auto-email-sender",
    );
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
