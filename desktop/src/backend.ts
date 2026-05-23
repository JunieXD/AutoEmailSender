import {
  execFile,
  spawn,
  type ChildProcessWithoutNullStreams,
} from "node:child_process";
import { existsSync } from "node:fs";
import http from "node:http";
import path from "node:path";
import { promisify } from "node:util";
import type {
  BackendController,
  BackendEnvInput,
  BackendExit,
  BackendExitHandler,
  BackendPathInput,
  BackendStartupPhase,
  BackendStartupStatus,
  BackendStatus,
} from "./types.js";

const execFileAsync = promisify(execFile);

type BackendProcessTreeTerminator = (pid: number, port?: number) => Promise<void>;

export function normalizePort(value: string): number {
  const port = Number(value);
  if (!Number.isInteger(port) || port < 1 || port > 65535) {
    throw new Error(`Invalid port: ${value}`);
  }
  return port;
}

export function getBackendExecutablePath(input: BackendPathInput): string {
  if (input.isPackaged) {
    return path.join(input.resourcesPath, "backend", "backend.exe");
  }
  return path.join(input.repoRoot, "backend", "desktop_entry.py");
}

export function getFrontendIndexPath(input: BackendPathInput): string {
  if (input.isPackaged) {
    return path.join(input.resourcesPath, "frontend", "index.html");
  }
  return path.join(input.repoRoot, "frontend", "dist", "index.html");
}

export function buildBackendEnv(input: BackendEnvInput): NodeJS.ProcessEnv {
  const browsersPath = input.isPackaged
    ? path.join(input.resourcesPath, "ms-playwright")
    : path.join(input.repoRoot, "backend", "ms-playwright");

  return {
    ...input.baseEnv,
    AUTO_EMAIL_SENDER_DATA_DIR: input.userDataPath,
    ENABLE_BACKGROUND_WORKERS: "true",
    PLAYWRIGHT_BROWSERS_PATH: browsersPath,
  };
}

export async function findAvailablePort(startPort = 48120): Promise<number> {
  for (let port = startPort; port < startPort + 100; port += 1) {
    if (await canListen(port)) {
      return port;
    }
  }
  throw new Error("No available backend port found.");
}

export async function startBackend(options: {
  isPackaged: boolean;
  resourcesPath: string;
  repoRoot: string;
  userDataPath: string;
  onUnexpectedExit?: BackendExitHandler;
}): Promise<BackendController> {
  const port = await findAvailablePort();
  const baseUrl = `http://127.0.0.1:${port}`;
  const backendPath = getBackendExecutablePath(options);

  if (!existsSync(backendPath)) {
    throw new Error(`Backend executable not found: ${backendPath}`);
  }

  const child = spawnBackend({
    backendPath,
    isPackaged: options.isPackaged,
    port,
    env: buildBackendEnv({
      baseEnv: process.env,
      isPackaged: options.isPackaged,
      resourcesPath: options.resourcesPath,
      repoRoot: options.repoRoot,
      userDataPath: options.userDataPath,
    }),
    repoRoot: options.repoRoot,
  });
  const lifecycle: BackendLifecycle = {
    intentionalStop: false,
    onUnexpectedExit: options.onUnexpectedExit,
  };
  child.once("exit", (code, signal) => {
    notifyBackendExit(lifecycle, code, signal);
  });
  const statusHandlers = new Set<(status: BackendStatus) => void>();
  const emitStatus = (status: BackendStatus) => {
    statusHandlers.forEach((handler) => handler(status));
  };

  return {
    baseUrl,
    ready: waitForReady(baseUrl, child, emitStatus),
    onStatus: (handler) => {
      statusHandlers.add(handler);
      return () => {
        statusHandlers.delete(handler);
      };
    },
    stop: () => stopBackend(child, lifecycle, terminateBackendProcessTree, port),
  };
}

type BackendLifecycle = {
  intentionalStop: boolean;
  onUnexpectedExit?: BackendExitHandler;
};

export function notifyBackendExit(
  lifecycle: BackendLifecycle,
  code: number | null,
  signal: NodeJS.Signals | null,
): void {
  if (lifecycle.intentionalStop) {
    return;
  }
  lifecycle.onUnexpectedExit?.({ code, signal } satisfies BackendExit);
}

function spawnBackend(input: {
  backendPath: string;
  isPackaged: boolean;
  port: number;
  env: NodeJS.ProcessEnv;
  repoRoot: string;
}): ChildProcessWithoutNullStreams {
  if (input.isPackaged) {
    return spawn(input.backendPath, ["--host", "127.0.0.1", "--port", String(input.port)], {
      env: input.env,
      windowsHide: true,
    });
  }

  return spawn(
    "uv",
    ["run", "python", "desktop_entry.py", "--host", "127.0.0.1", "--port", String(input.port)],
    {
      cwd: path.join(input.repoRoot, "backend"),
      env: input.env,
      windowsHide: true,
    },
  );
}

async function waitForReady(
  baseUrl: string,
  child: ChildProcessWithoutNullStreams,
  onStatus: (status: BackendStatus) => void,
): Promise<void> {
  let stderr = "";
  child.stderr.on("data", (chunk: Buffer) => {
    stderr += chunk.toString("utf8");
  });

  await waitForHealth(baseUrl, child, () => stderr, { onStatus });
  try {
    await waitForStartupStatus(baseUrl, { child, getStderr: () => stderr, onStatus });
  } catch (error) {
    if (child.exitCode !== null) {
      throw new Error(`后端进程已退出：${stderr.slice(-800)}`);
    }
    throw error;
  }
}

export async function waitForHealth(
  baseUrl: string,
  child: ChildProcessWithoutNullStreams,
  getStderr: () => string,
  options: {
    onStatus?: (status: BackendStatus) => void;
    pollIntervalMs?: number;
    timeoutMs?: number;
    slowStartupMs?: number;
  } = {},
): Promise<void> {
  const pollIntervalMs = options.pollIntervalMs ?? 400;
  const timeoutMs = options.timeoutMs ?? 30_000;
  const slowStartupMs = options.slowStartupMs ?? 30_000;
  const startedAt = Date.now();
  const deadline = startedAt + timeoutMs;
  let slowStatusEmitted = false;

  while (Date.now() < deadline) {
    if (child.exitCode !== null) {
      throw new Error(`Backend exited before health check succeeded: ${getStderr().slice(-800)}`);
    }
    const elapsedMs = Date.now() - startedAt;
    if (!slowStatusEmitted && elapsedMs >= slowStartupMs) {
      slowStatusEmitted = true;
      const elapsedSeconds = Math.round(elapsedMs / 1000);
      options.onStatus?.({
        state: "starting",
        phase: "starting",
        message: "首次启动可能较慢，正在继续等待本地服务",
        elapsedSeconds,
        slowStartup: true,
        verySlowStartup: elapsedSeconds >= 120,
      });
    }
    if (await isEndpointOk(`${baseUrl}/health`)) {
      return;
    }
    await new Promise((resolve) => setTimeout(resolve, pollIntervalMs));
  }

  const waitedSeconds = Math.round((Date.now() - startedAt) / 1000);
  throw new Error(
    `Backend health check timed out after ${waitedSeconds}s; processExited=${child.exitCode !== null}: ${getStderr().slice(-800)}`,
  );
}

export async function waitForStartupStatus(
  baseUrl: string,
  options: {
    onStatus: (status: BackendStatus) => void;
    child?: ChildProcessWithoutNullStreams;
    getStderr?: () => string;
    pollIntervalMs?: number;
    hardTimeoutMs?: number;
  },
): Promise<void> {
  const pollIntervalMs = options.pollIntervalMs ?? 800;
  const hardTimeoutMs = options.hardTimeoutMs ?? 60_000;
  const startedAt = Date.now();
  const deadline = Date.now() + hardTimeoutMs;
  let lastStatus: BackendStatus | null = null;
  let lastStartingPhase: Exclude<BackendStartupPhase, "ready" | "error"> = "starting";
  let lastElapsedSeconds: number | null = null;

  while (Date.now() < deadline) {
    if (options.child?.exitCode !== null && options.child?.exitCode !== undefined) {
      throw new Error(`后端进程已退出：${(options.getStderr?.() ?? "").slice(-800)}`);
    }

    let status: BackendStartupStatus;
    try {
      status = await fetchStartupStatus(baseUrl);
    } catch {
      const localElapsedSeconds = Math.round((Date.now() - startedAt) / 1000);
      const elapsedSeconds =
        lastElapsedSeconds === null
          ? localElapsedSeconds
          : Math.max(lastElapsedSeconds, localElapsedSeconds);
      const startingStatus: BackendStatus = {
        state: "starting",
        phase: lastStartingPhase,
        message: "系统正在准备中",
        elapsedSeconds,
        slowStartup: elapsedSeconds >= 30,
        verySlowStartup: elapsedSeconds >= 60,
      };
      lastStatus = startingStatus;
      options.onStatus(startingStatus);
      await new Promise((resolve) => setTimeout(resolve, pollIntervalMs));
      continue;
    }

    if (status.state === "ready") {
      const readyStatus: BackendStatus = {
        state: "ready",
        baseUrl,
        phase: "ready",
        message: status.message,
        elapsedSeconds: status.elapsed_seconds,
      };
      options.onStatus(readyStatus);
      return;
    }

    if (status.state === "error") {
      const errorStatus: BackendStatus = {
        state: "error",
        phase: "error",
        message: "系统准备失败",
        elapsedSeconds: status.elapsed_seconds,
        detail: status.error ?? status.message,
      };
      options.onStatus(errorStatus);
      throw new Error(errorStatus.message);
    }

    const startingStatus: BackendStatus = {
      state: "starting",
      phase: isStartupPhase(status.phase) ? status.phase : "starting",
      message: status.message,
      elapsedSeconds: status.elapsed_seconds,
      slowStartup: status.elapsed_seconds >= 30,
      verySlowStartup: status.elapsed_seconds >= 60,
    };
    lastStatus = startingStatus;
    lastStartingPhase = startingStatus.phase;
    lastElapsedSeconds = startingStatus.elapsedSeconds;
    options.onStatus(startingStatus);
    await new Promise((resolve) => setTimeout(resolve, pollIntervalMs));
  }

  const elapsedSeconds =
    lastStatus?.state === "starting"
      ? lastStatus.elapsedSeconds
      : Math.round(hardTimeoutMs / 1000);
  const timeoutStatus: BackendStatus = {
    state: "error",
    phase: "error",
    message: "系统准备时间过长",
    elapsedSeconds,
    detail: "启动状态轮询超过 60 秒仍未完成",
  };
  options.onStatus(timeoutStatus);
  throw new Error(timeoutStatus.message);
}

function isStartupPhase(
  phase: BackendStartupStatus["phase"],
): phase is Exclude<BackendStartupPhase, "ready" | "error"> {
  return (
    phase === "starting" ||
    phase === "migrating_database" ||
    phase === "cleaning_logs" ||
    phase === "starting_workers"
  );
}

async function fetchStartupStatus(baseUrl: string): Promise<BackendStartupStatus> {
  return new Promise((resolve, reject) => {
    const request = http.get(`${baseUrl}/startup-status`, (response) => {
      let body = "";
      response.setEncoding("utf8");
      response.on("data", (chunk: string) => {
        body += chunk;
      });
      response.on("end", () => {
        if (response.statusCode !== 200) {
          reject(new Error(`Startup status request failed: ${response.statusCode}`));
          return;
        }
        try {
          resolve(JSON.parse(body) as BackendStartupStatus);
        } catch (error) {
          reject(error);
        }
      });
    });
    request.on("error", reject);
    request.setTimeout(1_000, () => {
      request.destroy(new Error("Startup status request timed out"));
    });
  });
}

async function isEndpointOk(url: string): Promise<boolean> {
  return new Promise((resolve) => {
    const request = http.get(url, (response) => {
      response.resume();
      resolve(response.statusCode === 200);
    });
    request.on("error", () => resolve(false));
    request.setTimeout(800, () => {
      request.destroy();
      resolve(false);
    });
  });
}

async function canListen(port: number): Promise<boolean> {
  return new Promise((resolve) => {
    const server = http.createServer();
    server.once("error", () => resolve(false));
    server.once("listening", () => {
      server.close(() => resolve(true));
    });
    server.listen(port, "127.0.0.1");
  });
}

export async function stopBackend(
  child: ChildProcessWithoutNullStreams,
  lifecycle: BackendLifecycle,
  terminateProcessTree: BackendProcessTreeTerminator = terminateBackendProcessTree,
  port?: number,
): Promise<void> {
  lifecycle.intentionalStop = true;
  if (child.exitCode !== null && port === undefined) {
    return;
  }

  const waitForExit = new Promise<void>((resolve) => {
    const timeout = setTimeout(() => {
      if (child.exitCode === null) {
        child.kill("SIGKILL");
      }
      resolve();
    }, 3_000);
    child.once("exit", () => {
      clearTimeout(timeout);
      resolve();
    });
  });

  if (child.pid === undefined) {
    child.kill();
  } else {
    try {
      await terminateProcessTree(child.pid, port);
    } catch {
      child.kill();
    }
  }

  await waitForExit;
}

async function terminateBackendProcessTree(pid: number, port?: number): Promise<void> {
  if (process.platform === "win32") {
    let taskkillError: unknown;
    try {
      await execFileAsync("taskkill", ["/pid", String(pid), "/t", "/f"], {
        windowsHide: true,
      });
    } catch (error) {
      taskkillError = error;
    }

    if (port !== undefined) {
      await terminateWindowsDesktopEntryProcesses(port);
      return;
    }

    if (taskkillError !== undefined) {
      throw taskkillError;
    }
    return;
  }

  process.kill(pid);
}

async function terminateWindowsDesktopEntryProcesses(port: number): Promise<void> {
  const script = `
$port = '${port}'
Get-CimInstance Win32_Process |
  Where-Object {
    $_.Name -eq 'python.exe' -and
    $_.CommandLine -like '*desktop_entry.py*' -and
    $_.CommandLine -like '*--port*' -and
    $_.CommandLine -like "*$port*"
  } |
  ForEach-Object {
    Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
  }
`;

  await execFileAsync(
    "powershell.exe",
    ["-NoLogo", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script],
    { windowsHide: true },
  );
}
