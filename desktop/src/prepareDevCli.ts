import { spawnSync } from "node:child_process";
import { createHash } from "node:crypto";
import { stat, mkdir, readFile, readdir, writeFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

export type DevelopmentCliBuildCommand = {
  command: string;
  args: string[];
};

export type DevelopmentCliBuildInput = {
  commands: DevelopmentCliBuildCommand[];
  repoRoot: string;
  executablePath: string;
};

export type PrepareDevelopmentCliOptions = {
  repoRoot: string;
  platform?: NodeJS.Platform;
  arch?: string;
  log?: (message: string) => void;
  runBuild?: (input: DevelopmentCliBuildInput) => Promise<void>;
};

export type PrepareDevelopmentCliResult = {
  state: "built" | "ready" | "unsupported";
  executablePath: string | null;
};

const FINGERPRINT_SCHEMA = "auto-email-sender-development-cli-v1";

export async function prepareDevelopmentCli(
  options: PrepareDevelopmentCliOptions,
): Promise<PrepareDevelopmentCliResult> {
  const platform = options.platform ?? process.platform;
  const arch = options.arch ?? process.arch;
  const log = options.log ?? console.log;

  if (!isSupportedDevelopmentCliTarget(platform, arch)) {
    log(`[dev-cli] 当前平台 ${platform}/${arch} 不支持 Agent CLI，跳过开发版 CLI 准备。`);
    return { state: "unsupported", executablePath: null };
  }

  const executablePath = resolveDevelopmentCliExecutable(options.repoRoot, platform);
  const fingerprintPath = path.join(
    options.repoRoot,
    "cli",
    "build",
    `.dev-cli-${platform}-${arch}.sha256`,
  );
  const fingerprint = await calculateDevelopmentCliFingerprint(
    options.repoRoot,
    platform,
    arch,
  );
  const cachedFingerprint = await readFile(fingerprintPath, "utf8")
    .then((value) => value.trim())
    .catch(() => "");

  if (cachedFingerprint === fingerprint && await isRegularFile(executablePath)) {
    log("[dev-cli] Agent CLI 已是最新版本，跳过重复构建。");
    return { state: "ready", executablePath };
  }

  log("[dev-cli] Agent CLI 缺失或源码已更新，正在构建开发版 CLI…");
  const commands = getDevelopmentCliBuildCommands(options.repoRoot, platform);
  const runBuild = options.runBuild ?? runDevelopmentCliBuild;
  await runBuild({ commands, repoRoot: options.repoRoot, executablePath });

  if (!(await isRegularFile(executablePath))) {
    throw new Error(`CLI 构建命令已结束，但没有生成预期文件：${executablePath}`);
  }

  await mkdir(path.dirname(fingerprintPath), { recursive: true });
  await writeFile(fingerprintPath, `${fingerprint}\n`, "utf8");
  log(`[dev-cli] Agent CLI 已准备完成：${executablePath}`);
  return { state: "built", executablePath };
}

export function isSupportedDevelopmentCliTarget(
  platform: NodeJS.Platform,
  arch: string,
): boolean {
  return (platform === "darwin" && arch === "arm64")
    || (platform === "win32" && arch === "x64");
}

export function resolveDevelopmentCliExecutable(
  repoRoot: string,
  platform: NodeJS.Platform,
): string {
  const executableName = platform === "win32"
    ? "auto-email-sender.exe"
    : "auto-email-sender";
  return path.join(repoRoot, "cli", "dist", executableName);
}

export function getDevelopmentCliBuildCommands(
  repoRoot: string,
  platform: NodeJS.Platform,
): DevelopmentCliBuildCommand[] {
  if (platform === "win32") {
    const scriptPath = path.join(repoRoot, "scripts", "build-cli.ps1");
    const args = [
      "-NoProfile",
      "-ExecutionPolicy",
      "Bypass",
      "-File",
      scriptPath,
      "-Clean",
    ];
    return [
      { command: "pwsh", args },
      { command: "powershell.exe", args },
    ];
  }

  return [{
    command: "bash",
    args: [path.join(repoRoot, "scripts", "build-cli.sh"), "--clean"],
  }];
}

export async function calculateDevelopmentCliFingerprint(
  repoRoot: string,
  platform: NodeJS.Platform,
  arch: string,
): Promise<string> {
  const cliDirectory = path.join(repoRoot, "cli");
  const buildScript = platform === "win32" ? "build-cli.ps1" : "build-cli.sh";
  const inputPaths = [
    path.join(cliDirectory, "pyproject.toml"),
    path.join(cliDirectory, "uv.lock"),
    path.join(repoRoot, "scripts", buildScript),
    path.join(repoRoot, "scripts", "generate_cli_build_identity.py"),
    path.join(repoRoot, "scripts", "verify_cli_binary.py"),
    ...await collectInputFiles(path.join(cliDirectory, "src")),
  ].sort((left, right) => left.localeCompare(right));
  const hash = createHash("sha256");
  hash.update(`${FINGERPRINT_SCHEMA}\0${platform}\0${arch}\0`);

  for (const inputPath of inputPaths) {
    const relativePath = path.relative(repoRoot, inputPath).split(path.sep).join("/");
    hash.update(relativePath);
    hash.update("\0");
    hash.update(await readFile(inputPath));
    hash.update("\0");
  }
  return hash.digest("hex");
}

async function collectInputFiles(directoryPath: string): Promise<string[]> {
  const entries = await readdir(directoryPath, { withFileTypes: true });
  const files: string[] = [];
  for (const entry of entries.sort((left, right) => left.name.localeCompare(right.name))) {
    if (entry.name === "__pycache__" || entry.name.endsWith(".pyc") || entry.name.endsWith(".pyo")) {
      continue;
    }
    const entryPath = path.join(directoryPath, entry.name);
    if (entry.isDirectory()) {
      files.push(...await collectInputFiles(entryPath));
    } else if (entry.isFile()) {
      files.push(entryPath);
    }
  }
  return files;
}

export async function runDevelopmentCliBuild(input: DevelopmentCliBuildInput): Promise<void> {
  for (const buildCommand of input.commands) {
    const result = spawnSync(buildCommand.command, buildCommand.args, {
      cwd: input.repoRoot,
      stdio: ["ignore", "pipe", "pipe"],
      encoding: "utf8",
      shell: false,
    });
    const errorCode = (result.error as NodeJS.ErrnoException | undefined)?.code;
    if (errorCode === "ENOENT") {
      continue;
    }
    if (result.error) {
      throw result.error;
    }
    if (result.status === 0) {
      return;
    }
    const output = [result.stdout, result.stderr]
      .map((value) => (value === null ? "" : String(value).trim()))
      .filter(Boolean)
      .join("\n");
    throw new Error(
      `${buildCommand.command} 构建 CLI 失败（退出码 ${result.status ?? "未知"}）。`
        + (output ? `\n${output}` : ""),
    );
  }
  throw new Error("找不到可用的 CLI 构建命令。Windows 请安装 PowerShell，macOS 请确认 bash 可用。");
}

async function isRegularFile(targetPath: string): Promise<boolean> {
  try {
    return (await stat(targetPath)).isFile();
  } catch {
    return false;
  }
}

async function main(): Promise<void> {
  const modulePath = fileURLToPath(import.meta.url);
  const repoRoot = path.resolve(path.dirname(modulePath), "../../..");
  try {
    await prepareDevelopmentCli({ repoRoot });
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    console.error(`[dev-cli] 无法准备 Agent CLI：${message}`);
    process.exitCode = 1;
  }
}

const invokedPath = process.argv[1] ? pathToFileURL(path.resolve(process.argv[1])).href : "";
if (import.meta.url === invokedPath) {
  void main();
}
