import { mkdir, mkdtemp, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import { afterEach, describe, expect, it, vi } from "vitest";
import {
  getDevelopmentCliBuildCommands,
  prepareDevelopmentCli,
  resolveDevelopmentCliExecutable,
  runDevelopmentCliBuild,
  type DevelopmentCliBuildInput,
} from "../src/main/agent-support/prepare-dev-cli.js";

const temporaryDirectories: string[] = [];

async function createRepositoryFixture(): Promise<string> {
  const repoRoot = await mkdtemp(path.join(tmpdir(), "auto-email-sender-dev-cli-"));
  temporaryDirectories.push(repoRoot);
  await mkdir(path.join(repoRoot, "cli", "src", "auto_email_sender_cli"), { recursive: true });
  await mkdir(path.join(repoRoot, "scripts"), { recursive: true });
  await writeFile(path.join(repoRoot, "cli", "pyproject.toml"), "[project]\nname='fixture'\n", "utf8");
  await writeFile(path.join(repoRoot, "cli", "uv.lock"), "version = 1\n", "utf8");
  await writeFile(
    path.join(repoRoot, "cli", "src", "auto_email_sender_cli", "__main__.py"),
    "print('first')\n",
    "utf8",
  );
  await writeFile(path.join(repoRoot, "scripts", "build-cli.sh"), "#!/usr/bin/env bash\n", "utf8");
  await writeFile(path.join(repoRoot, "scripts", "build-cli.ps1"), "param()\n", "utf8");
  await writeFile(
    path.join(repoRoot, "scripts", "generate_cli_build_identity.py"),
    "# identity generator\n",
    "utf8",
  );
  await writeFile(
    path.join(repoRoot, "scripts", "verify_cli_binary.py"),
    "# binary verifier\n",
    "utf8",
  );
  return repoRoot;
}

afterEach(async () => {
  await Promise.all(
    temporaryDirectories.splice(0).map((directory) =>
      rm(directory, { recursive: true, force: true }),
    ),
  );
});

describe("development CLI preparation", () => {
  it("builds a missing CLI, reuses it, and rebuilds after source changes", async () => {
    const repoRoot = await createRepositoryFixture();
    const runBuild = vi.fn(async (input: DevelopmentCliBuildInput) => {
      await mkdir(path.dirname(input.executablePath), { recursive: true });
      await writeFile(input.executablePath, "development cli", "utf8");
    });
    const options = {
      repoRoot,
      platform: "darwin" as const,
      arch: "arm64",
      log: vi.fn(),
      runBuild,
    };

    await expect(prepareDevelopmentCli(options)).resolves.toMatchObject({ state: "built" });
    await expect(prepareDevelopmentCli(options)).resolves.toMatchObject({ state: "ready" });
    expect(runBuild).toHaveBeenCalledTimes(1);

    await writeFile(
      path.join(repoRoot, "cli", "src", "auto_email_sender_cli", "__main__.py"),
      "print('updated')\n",
      "utf8",
    );
    await expect(prepareDevelopmentCli(options)).resolves.toMatchObject({ state: "built" });
    expect(runBuild).toHaveBeenCalledTimes(2);

    await writeFile(
      path.join(repoRoot, "scripts", "verify_cli_binary.py"),
      "# updated binary verifier\n",
      "utf8",
    );
    await expect(prepareDevelopmentCli(options)).resolves.toMatchObject({ state: "built" });
    expect(runBuild).toHaveBeenCalledTimes(3);
  });

  it("selects the Windows executable and falls back to Windows PowerShell", async () => {
    const repoRoot = await createRepositoryFixture();
    const commands = getDevelopmentCliBuildCommands(repoRoot, "win32");

    expect(resolveDevelopmentCliExecutable(repoRoot, "win32")).toBe(
      path.join(repoRoot, "cli", "dist", "auto-email-sender.exe"),
    );
    expect(commands.map(({ command }) => command)).toEqual(["pwsh", "powershell.exe"]);
    expect(commands[0].args).toContain("-Clean");
    expect(commands[0].args).toContain(path.join(repoRoot, "scripts", "build-cli.ps1"));
  });

  it("does not build on unsupported development targets", async () => {
    const runBuild = vi.fn();

    await expect(prepareDevelopmentCli({
      repoRoot: "/repo-does-not-need-to-exist",
      platform: "darwin",
      arch: "x64",
      log: vi.fn(),
      runBuild,
    })).resolves.toEqual({ state: "unsupported", executablePath: null });
    expect(runBuild).not.toHaveBeenCalled();
  });

  it("captures CLI build output and shows it only when the build fails", async () => {
    const repoRoot = await createRepositoryFixture();

    await expect(runDevelopmentCliBuild({
      repoRoot,
      executablePath: path.join(repoRoot, "cli", "dist", "auto-email-sender"),
      commands: [{
        command: process.execPath,
        args: ["-e", "console.log('build stdout'); console.error('build stderr'); process.exit(7)"],
      }],
    })).rejects.toThrow(/build stdout[\s\S]*build stderr/);
  });
});
