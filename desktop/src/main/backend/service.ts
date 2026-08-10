import {
  execFile,
  spawn,
  type ChildProcessWithoutNullStreams,
} from "node:child_process";
import { existsSync } from "node:fs";
import http from "node:http";
import path from "node:path";
import { randomBytes, randomUUID } from "node:crypto";
import { performance } from "node:perf_hooks";
import { promisify } from "node:util";
import {
  captureProcessOutput,
  sanitizeProcessOutput,
  type CapturedProcessOutput,
} from "./process-output.js";
import { RestartPolicy, type RestartDecision } from "./restart-policy.js";
import {
  WORKER_HEARTBEAT_TIMEOUT_MS,
  readWorkerRuntimeStatus,
  type WorkerRuntimeStatus,
} from "./worker-status.js";
import type {
  BackendController,
  BackendEnvInput,
  BackendExit,
  BackendExitHandler,
  BackendPathInput,
  BackendRuntimeInfo,
  BackendDatabaseError,
  BackendStartupPhase,
  BackendStartupStatus,
  BackendStatus,
  BackendMode,
  BackendRole,
} from "./types.js";

const execFileAsync = promisify(execFile);

type BackendProcessTreeTerminator = (pid: number, port?: number) => Promise<void>;
const WORKER_STATUS_POLL_MS = 1_000;
const WORKER_STARTUP_TIMEOUT_MS = 60_000;
const COMBINED_STOP_TIMEOUT_MS = 8_000;
const API_STOP_TIMEOUT_MS = 5_000;
const WORKER_STOP_TIMEOUT_MS = 8_000;

export type StartBackendOptions = {
  isPackaged: boolean;
  resourcesPath: string;
  repoRoot: string;
  userDataPath: string;
  appVersion?: string;
  mode?: BackendMode;
  portRangeStart?: number;
  onUnexpectedExit?: BackendExitHandler;
};

export function normalizePort(value: string): number {
  const port = Number(value);
  if (!Number.isInteger(port) || port < 1 || port > 65535) {
    throw new Error(`Invalid port: ${value}`);
  }
  return port;
}

export function resolveBackendMode(value: string | undefined): BackendMode {
  return value?.trim().toLowerCase() === "split" ? "split" : "combined";
}

export function getBackendExecutablePath(input: BackendPathInput): string {
  if (input.isPackaged) {
    const executableName = (input.platform ?? process.platform) === "win32" ? "backend.exe" : "backend";
    return path.join(input.resourcesPath, "backend", executableName);
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

  const role = input.role ?? "combined";
  const baseEnv = { ...input.baseEnv };
  if (role === "worker") {
    for (const key of Object.keys(baseEnv)) {
      const normalized = key.toUpperCase();
      if (
        normalized === "AUTO_EMAIL_SENDER_UI_TOKEN"
        || normalized === "AUTO_EMAIL_SENDER_AGENT_TOKEN"
      ) {
        delete baseEnv[key];
      }
    }
  }
  return {
    ...baseEnv,
    AUTO_EMAIL_SENDER_DATA_DIR: input.userDataPath,
    AUTO_EMAIL_SENDER_APP_VERSION: input.appVersion,
    AUTO_EMAIL_SENDER_DESKTOP_PID: String(process.pid),
    AUTO_EMAIL_SENDER_RUNTIME_ID: input.runtimeId,
    ...(role !== "worker" && input.uiAccessToken
      ? { AUTO_EMAIL_SENDER_UI_TOKEN: input.uiAccessToken }
      : {}),
    ...(role !== "worker" && input.agentAccessToken
      ? { AUTO_EMAIL_SENDER_AGENT_TOKEN: input.agentAccessToken }
      : {}),
    ...(role === "worker" && input.apiPid
      ? { AUTO_EMAIL_SENDER_API_PID: String(input.apiPid) }
      : {}),
    ...(role === "worker" && input.workerGeneration
      ? { AUTO_EMAIL_SENDER_WORKER_GENERATION: input.workerGeneration }
      : {}),
    ENABLE_BACKGROUND_WORKERS: "true",
    PLAYWRIGHT_BROWSERS_PATH: browsersPath,
    ...(input.isPackaged
      ? {
          PLAYWRIGHT_NODEJS_PATH: input.electronExecutablePath,
          ELECTRON_RUN_AS_NODE: "1",
        }
      : {}),
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

export async function startBackend(options: StartBackendOptions): Promise<BackendController> {
  if ((options.mode ?? "combined") === "split") {
    return startSplitBackend(options);
  }
  const port = await findAvailablePort(options.portRangeStart);
  const baseUrl = `http://127.0.0.1:${port}`;
  const uiAccessToken = generateAccessToken();
  const agentAccessToken = generateAccessToken();
  const runtimeId = randomUUID();
  const backendPath = getBackendExecutablePath({
    ...options,
    platform: process.platform,
  });

  if (!existsSync(backendPath)) {
    throw new Error(`Backend executable not found: ${backendPath}`);
  }

  const child = spawnBackend({
    backendPath,
    isPackaged: options.isPackaged,
    port,
    role: "combined",
    env: buildBackendEnv({
      baseEnv: process.env,
      isPackaged: options.isPackaged,
      resourcesPath: options.resourcesPath,
      repoRoot: options.repoRoot,
      userDataPath: options.userDataPath,
      appVersion: options.appVersion ?? "development",
      electronExecutablePath: process.execPath,
      runtimeId,
      uiAccessToken,
      agentAccessToken,
    }),
    repoRoot: options.repoRoot,
  });
  const output = captureProcessOutput(child);
  const lifecycle: BackendLifecycle = {
    intentionalStop: false,
    onUnexpectedExit: options.onUnexpectedExit,
  };
  child.once("exit", (code, signal) => {
    notifyBackendExit(lifecycle, code, signal);
  });
  const statusHandlers = new Set<(status: BackendStatus) => void>();
  const emitStatus = (status: BackendStatus) => {
    statusHandlers.forEach((handler) => {
      try {
        handler(status);
      } catch {
        // A status observer must not turn a healthy backend into a startup failure.
      }
    });
  };

  if (child.pid === undefined) {
    child.kill();
    throw new Error("Backend process started without a process id.");
  }
  const backendStartedAt = new Date().toISOString();

  return {
    baseUrl,
    backendPid: child.pid,
    backendStartedAt,
    mode: "combined",
    runtimeId,
    uiAccessToken,
    agentAccessToken,
    getRuntimeInfo: () => fetchBackendRuntimeInfo(baseUrl, agentAccessToken),
    ready: waitForReady(
      baseUrl,
      child,
      emitStatus,
      () => output.stderr.sanitizedText([uiAccessToken, agentAccessToken]),
    ),
    onStatus: (handler) => {
      statusHandlers.add(handler);
      return () => {
        statusHandlers.delete(handler);
      };
    },
    notifySystemResume: () => undefined,
    stop: () => stopBackend(child, lifecycle, terminateBackendProcessTree, port),
  };
}

type RuntimeRoleProcess = {
  role: "api" | "worker";
  child: ChildProcessWithoutNullStreams;
  lifecycle: BackendLifecycle;
  output: CapturedProcessOutput;
  generation?: string;
  pid: number;
  identityReady: boolean;
  startedAt: string;
  launchedAt: number;
};

type Deferred = {
  promise: Promise<void>;
  resolve: () => void;
  reject: (error: unknown) => void;
};

async function startSplitBackend(options: StartBackendOptions): Promise<BackendController> {
  const port = await findAvailablePort(options.portRangeStart);
  const backendPath = getBackendExecutablePath({
    ...options,
    platform: process.platform,
  });
  if (!existsSync(backendPath)) {
    throw new Error(`Backend executable not found: ${backendPath}`);
  }

  const supervisor = new RuntimeGroupSupervisor({
    ...options,
    backendPath,
    port,
  });
  const controller = supervisor.createController();
  supervisor.start();
  return controller;
}

class RuntimeGroupSupervisor {
  readonly #options: StartBackendOptions & { backendPath: string; port: number };
  readonly #baseUrl: string;
  readonly #uiAccessToken = generateAccessToken();
  readonly #agentAccessToken = generateAccessToken();
  readonly #ready: Deferred = createDeferred();
  readonly #statusHandlers = new Set<(status: BackendStatus) => void>();
  readonly #workerRestartPolicy = new RestartPolicy();
  readonly #groupRestartPolicy = new RestartPolicy();
  #runtimeId = randomUUID();
  #api: RuntimeRoleProcess | null = null;
  #worker: RuntimeRoleProcess | null = null;
  #workerMonitor: NodeJS.Timeout | null = null;
  #workerMonitorBusy = false;
  #lastWorkerHeartbeatValue: string | null = null;
  #lastWorkerHeartbeatAdvancedAt = 0;
  #workerReportedDegraded = false;
  #workerRecoveryPromise: Promise<void> | null = null;
  #workerRecoveryAbort: AbortController | null = null;
  #groupRestartPromise: Promise<void> | null = null;
  #groupRestartAbort: AbortController | null = null;
  #epoch = 0;
  #stopped = false;
  #initialReadySettled = false;

  constructor(options: StartBackendOptions & { backendPath: string; port: number }) {
    this.#options = options;
    this.#baseUrl = `http://127.0.0.1:${options.port}`;
  }

  createController(): BackendController {
    const supervisor = this;
    return {
      get baseUrl() {
        return supervisor.#baseUrl;
      },
      get backendPid() {
        return supervisor.#api?.pid ?? 0;
      },
      get backendStartedAt() {
        return supervisor.#api?.startedAt ?? new Date(0).toISOString();
      },
      get workerPid() {
        return supervisor.#worker?.identityReady ? supervisor.#worker.pid : undefined;
      },
      get workerStartedAt() {
        return supervisor.#worker?.startedAt;
      },
      get workerGeneration() {
        return supervisor.#worker?.generation;
      },
      mode: "split",
      get runtimeId() {
        return supervisor.#runtimeId;
      },
      uiAccessToken: this.#uiAccessToken,
      agentAccessToken: this.#agentAccessToken,
      getRuntimeInfo: () => fetchBackendRuntimeInfo(
        supervisor.#baseUrl,
        supervisor.#agentAccessToken,
      ),
      ready: this.#ready.promise,
      onStatus: (handler) => {
        supervisor.#statusHandlers.add(handler);
        return () => {
          supervisor.#statusHandlers.delete(handler);
        };
      },
      notifySystemResume: () => supervisor.notifySystemResume(),
      stop: () => supervisor.stop(),
    };
  }

  notifySystemResume(): void {
    if (this.#stopped || this.#worker === null) {
      return;
    }
    // Timers and the Worker heartbeat are both paused during OS sleep. Give
    // the existing Worker a full heartbeat window after wake instead of
    // interpreting the suspend interval as an event-loop hang.
    this.#lastWorkerHeartbeatAdvancedAt = performance.now();
  }

  start(): void {
    const api = this.#spawnRole("api");
    this.#api = api;
    void this.#startInitialGroup(api);
  }

  async stop(): Promise<void> {
    if (this.#stopped) {
      return;
    }
    this.#stopped = true;
    this.#epoch += 1;
    this.#workerRecoveryAbort?.abort();
    this.#groupRestartAbort?.abort();
    this.#stopWorkerMonitor();

    const worker = this.#worker;
    this.#worker = null;
    if (worker !== null) {
      await stopRuntimeRoleProcess(worker);
    }
    const api = this.#api;
    this.#api = null;
    if (api !== null) {
      await stopRuntimeRoleProcess(api);
    }
    await Promise.allSettled([
      this.#workerRecoveryPromise ?? Promise.resolve(),
      this.#groupRestartPromise ?? Promise.resolve(),
    ]);
  }

  async #startInitialGroup(api: RuntimeRoleProcess): Promise<void> {
    try {
      await this.#waitForApiReady(api, this.#epoch);
    } catch (error) {
      if (this.#stopped) {
        return;
      }
      const failedApi = this.#api;
      this.#api = null;
      if (failedApi !== null) {
        await stopRuntimeRoleProcess(failedApi);
      }
      const message = this.#sanitizeError(error);
      this.#emitStatus({
        state: "error",
        message: "系统准备失败",
        phase: "error",
        elapsedSeconds: 0,
        detail: message,
      });
      this.#settleInitialReady(error);
      return;
    }

    try {
      await this.#launchWorker(this.#epoch);
      if (!this.#workerReportedDegraded) {
        this.#emitReady();
      }
    } catch (error) {
      if (!this.#stopped && this.#api !== null) {
        this.#emitWorkerUnavailable("background_unavailable", error);
        this.#ensureWorkerRecovery();
      }
    }
    this.#settleInitialReady();
  }

  async #launchApi(epoch: number): Promise<void> {
    const api = this.#spawnRole("api");
    this.#api = api;
    await this.#waitForApiReady(api, epoch);
  }

  async #waitForApiReady(api: RuntimeRoleProcess, epoch: number): Promise<void> {
    await waitForReady(
      this.#baseUrl,
      api.child,
      (status) => {
        if (this.#stopped || epoch !== this.#epoch || this.#api !== api) {
          return;
        }
        if (status.state === "ready") {
          this.#emitStatus({
            state: "starting",
            phase: "starting_workers",
            message: "正在启动后台服务",
            elapsedSeconds: status.elapsedSeconds,
            slowStartup: status.elapsedSeconds >= 30,
            verySlowStartup: status.elapsedSeconds >= 60,
          });
          return;
        }
        this.#emitStatus(status);
      },
      () => this.#getStderr(api),
    );
    if (this.#stopped || epoch !== this.#epoch || this.#api !== api) {
      throw new Error("API startup was superseded by another runtime group.");
    }
    const runtime = await fetchBackendRuntimeInfo(this.#baseUrl, this.#agentAccessToken);
    if (runtime.runtime_id !== this.#runtimeId || runtime.state !== "ready") {
      throw new Error("API runtime identity did not match its Electron runtime group.");
    }
    api.pid = runtime.backend_pid;
    api.identityReady = true;
  }

  async #launchWorker(epoch: number): Promise<WorkerRuntimeStatus> {
    const api = this.#api;
    if (api === null || api.child.exitCode !== null) {
      throw new Error("API is unavailable; Worker startup is not allowed.");
    }
    const worker = this.#spawnRole("worker", api.pid);
    this.#worker = worker;
    try {
      const status = await waitForWorkerReady({
        userDataPath: this.#options.userDataPath,
        runtimeId: this.#runtimeId,
        generation: worker.generation ?? "",
        child: worker.child,
        getStderr: () => this.#getStderr(worker),
      });
      if (this.#stopped || epoch !== this.#epoch || this.#worker !== worker) {
        throw new Error("Worker startup was superseded by another runtime group.");
      }
      worker.pid = status.pid;
      worker.identityReady = true;
      worker.startedAt = status.started_at;
      this.#lastWorkerHeartbeatValue = status.heartbeat_at;
      this.#lastWorkerHeartbeatAdvancedAt = performance.now();
      this.#workerReportedDegraded = status.health === "degraded";
      this.#startWorkerMonitor(worker, epoch);
      if (status.health === "degraded") {
        this.#emitWorkerDegraded(status);
      }
      return status;
    } catch (error) {
      if (this.#worker === worker) {
        this.#worker = null;
      }
      await stopRuntimeRoleProcess(worker);
      throw error;
    }
  }

  #spawnRole(role: "api" | "worker", apiPid?: number): RuntimeRoleProcess {
    const generation = role === "worker" ? randomUUID() : undefined;
    const child = spawnBackend({
      backendPath: this.#options.backendPath,
      isPackaged: this.#options.isPackaged,
      port: this.#options.port,
      role,
      env: buildBackendEnv({
        baseEnv: process.env,
        isPackaged: this.#options.isPackaged,
        resourcesPath: this.#options.resourcesPath,
        repoRoot: this.#options.repoRoot,
        userDataPath: this.#options.userDataPath,
        appVersion: this.#options.appVersion ?? "development",
        electronExecutablePath: process.execPath,
        runtimeId: this.#runtimeId,
        role,
        apiPid,
        workerGeneration: generation,
        uiAccessToken: this.#uiAccessToken,
        agentAccessToken: this.#agentAccessToken,
      }),
      repoRoot: this.#options.repoRoot,
      detached: process.platform !== "win32",
    });
    if (child.pid === undefined) {
      child.kill();
      throw new Error(`${role} process started without a process id.`);
    }
    const processRecord: RuntimeRoleProcess = {
      role,
      child,
      lifecycle: { intentionalStop: false },
      output: captureProcessOutput(child),
      generation,
      pid: child.pid,
      identityReady: false,
      startedAt: new Date().toISOString(),
      launchedAt: Date.now(),
    };
    child.once("exit", (code, signal) => {
      void this.#handleRoleExit(processRecord, { code, signal });
    });
    return processRecord;
  }

  async #handleRoleExit(
    processRecord: RuntimeRoleProcess,
    exit: BackendExit,
  ): Promise<void> {
    if (this.#stopped || processRecord.lifecycle.intentionalStop) {
      return;
    }
    if (processRecord.role === "api") {
      if (this.#api !== processRecord) {
        return;
      }
      this.#api = null;
      if (!this.#initialReadySettled) {
        this.#stopWorkerMonitor();
        const worker = this.#worker;
        this.#worker = null;
        if (worker !== null) {
          await stopRuntimeRoleProcess(worker);
        }
        const error = new Error(
          `API exited during startup code=${String(exit.code)} signal=${String(exit.signal)}: ${this.#getStderr(processRecord).slice(-800)}`,
        );
        this.#emitStatus({
          state: "error",
          message: "系统准备失败",
          phase: "error",
          elapsedSeconds: 0,
          detail: this.#sanitizeError(error),
        });
        this.#settleInitialReady(error);
        return;
      }
      this.#ensureGroupRestart(exit, processRecord);
      return;
    }
    if (this.#worker !== processRecord) {
      return;
    }
    this.#worker = null;
    this.#stopWorkerMonitor();
    this.#emitWorkerUnavailable(
      "background_unavailable",
      new Error(
        `Worker exited code=${String(exit.code)} signal=${String(exit.signal)}: ${this.#getStderr(processRecord).slice(-800)}`,
      ),
    );
    this.#ensureWorkerRecovery();
  }

  #ensureWorkerRecovery(): void {
    if (
      this.#stopped
      || this.#api === null
      || this.#worker !== null
      || this.#workerRecoveryPromise !== null
      || this.#groupRestartPromise !== null
    ) {
      return;
    }
    const epoch = this.#epoch;
    const abort = new AbortController();
    this.#workerRecoveryAbort = abort;
    this.#workerRecoveryPromise = this.#recoverWorker(epoch, abort.signal)
      .finally(() => {
        if (this.#workerRecoveryAbort === abort) {
          this.#workerRecoveryAbort = null;
        }
        this.#workerRecoveryPromise = null;
      });
  }

  async #recoverWorker(epoch: number, signal: AbortSignal): Promise<void> {
    while (
      !this.#stopped
      && !signal.aborted
      && epoch === this.#epoch
      && this.#api !== null
      && this.#worker === null
    ) {
      const decision = this.#workerRestartPolicy.recordFailure();
      this.#emitWorkerRestarting(decision);
      try {
        await abortableDelay(decision.delayMs, signal);
      } catch {
        return;
      }
      if (
        this.#stopped
        || signal.aborted
        || epoch !== this.#epoch
        || this.#api === null
        || this.#worker !== null
      ) {
        return;
      }
      try {
        await this.#launchWorker(epoch);
      } catch (error) {
        if (!this.#stopped && epoch === this.#epoch) {
          this.#emitWorkerUnavailable("background_unavailable", error);
        }
        continue;
      }
      if (!this.#workerReportedDegraded) {
        this.#emitReady();
      }
      return;
    }
  }

  #ensureGroupRestart(exit: BackendExit, exitedApi: RuntimeRoleProcess): void {
    if (this.#stopped || this.#groupRestartPromise !== null) {
      return;
    }
    this.#workerRecoveryAbort?.abort();
    const abort = new AbortController();
    this.#groupRestartAbort = abort;
    this.#groupRestartPromise = this.#restartWholeGroup(exit, exitedApi, abort.signal)
      .finally(() => {
        if (this.#groupRestartAbort === abort) {
          this.#groupRestartAbort = null;
        }
        this.#groupRestartPromise = null;
        this.#ensureWorkerRecovery();
      });
  }

  async #restartWholeGroup(
    exit: BackendExit,
    exitedApi: RuntimeRoleProcess,
    signal: AbortSignal,
  ): Promise<void> {
    this.#emitStatus({ state: "restarting", code: exit.code, signal: exit.signal });
    this.#stopWorkerMonitor();
    const worker = this.#worker;
    this.#worker = null;
    if (worker !== null) {
      await stopRuntimeRoleProcess(worker);
    }
    await stopRuntimeRoleProcess(exitedApi);

    this.#groupRestartPolicy.resetAfterStableRun(exitedApi.launchedAt);
    let decision = this.#groupRestartPolicy.recordFailure();
    while (!this.#stopped && !signal.aborted) {
      try {
        await abortableDelay(decision.delayMs, signal);
      } catch {
        return;
      }
      if (this.#stopped || signal.aborted) {
        return;
      }
      this.#epoch += 1;
      this.#runtimeId = randomUUID();
      const epoch = this.#epoch;
      try {
        await this.#launchApi(epoch);
      } catch (error) {
        const failedApi = this.#api;
        this.#api = null;
        if (failedApi !== null) {
          await stopRuntimeRoleProcess(failedApi);
        }
        this.#emitStatus({
          state: "error",
          message: "系统服务重启失败，正在继续重试",
          phase: "error",
          elapsedSeconds: 0,
          detail: this.#sanitizeError(error),
        });
        decision = this.#groupRestartPolicy.recordFailure();
        continue;
      }

      try {
        await this.#launchWorker(epoch);
        if (!this.#workerReportedDegraded) {
          this.#emitReady();
        }
      } catch (error) {
        this.#emitWorkerUnavailable("background_unavailable", error);
        this.#ensureWorkerRecovery();
      }
      return;
    }
  }

  #startWorkerMonitor(worker: RuntimeRoleProcess, epoch: number): void {
    this.#stopWorkerMonitor();
    this.#workerMonitor = setInterval(() => {
      if (this.#workerMonitorBusy) {
        return;
      }
      this.#workerMonitorBusy = true;
      void this.#checkWorkerStatus(worker, epoch).finally(() => {
        this.#workerMonitorBusy = false;
      });
    }, WORKER_STATUS_POLL_MS);
  }

  #stopWorkerMonitor(): void {
    if (this.#workerMonitor !== null) {
      clearInterval(this.#workerMonitor);
      this.#workerMonitor = null;
    }
    this.#workerMonitorBusy = false;
  }

  async #checkWorkerStatus(worker: RuntimeRoleProcess, epoch: number): Promise<void> {
    if (
      this.#stopped
      || epoch !== this.#epoch
      || this.#worker !== worker
      || worker.child.exitCode !== null
    ) {
      return;
    }
    const status = await readWorkerRuntimeStatus(this.#options.userDataPath);
    const matchesWorker = status !== null
      && status.runtime_id === this.#runtimeId
      && status.generation === worker.generation
      && status.pid === worker.pid;
    if (matchesWorker) {
      const heartbeatAt = Date.parse(status.heartbeat_at);
      if (
        Number.isFinite(heartbeatAt)
        && status.heartbeat_at !== this.#lastWorkerHeartbeatValue
      ) {
        this.#lastWorkerHeartbeatValue = status.heartbeat_at;
        this.#lastWorkerHeartbeatAdvancedAt = performance.now();
      }
      if (
        Number.isFinite(heartbeatAt)
        && performance.now() - this.#lastWorkerHeartbeatAdvancedAt <= WORKER_HEARTBEAT_TIMEOUT_MS
      ) {
        this.#workerRestartPolicy.resetAfterStableRun(worker.launchedAt);
        if (status.health === "degraded") {
          if (!this.#workerReportedDegraded) {
            this.#workerReportedDegraded = true;
            this.#emitWorkerDegraded(status);
          }
        } else if (this.#workerReportedDegraded) {
          this.#workerReportedDegraded = false;
          this.#emitReady();
        }
        return;
      }
    }
    if (
      performance.now() - this.#lastWorkerHeartbeatAdvancedAt
      <= WORKER_HEARTBEAT_TIMEOUT_MS
    ) {
      return;
    }

    this.#worker = null;
    this.#stopWorkerMonitor();
    this.#emitWorkerUnavailable(
      "background_hung",
      new Error("Worker heartbeat has been unavailable for more than 15 seconds."),
    );
    await stopRuntimeRoleProcess(worker);
    this.#ensureWorkerRecovery();
  }

  #emitReady(): void {
    this.#emitStatus({
      state: "ready",
      baseUrl: this.#baseUrl,
      phase: "ready",
      message: "系统已准备就绪",
      elapsedSeconds: 0,
    });
  }

  #emitWorkerUnavailable(
    reason: "background_unavailable" | "background_hung",
    error: unknown,
  ): void {
    this.#emitStatus({
      state: "degraded",
      baseUrl: this.#baseUrl,
      reason,
      message: reason === "background_hung"
        ? "后台服务无响应，正在安全重启"
        : "后台服务暂时不可用",
      detail: this.#sanitizeError(error),
    });
  }

  #emitWorkerRestarting(decision: RestartDecision): void {
    this.#emitStatus({
      state: "degraded",
      baseUrl: this.#baseUrl,
      reason: "background_restarting",
      message: decision.circuitOpenUntil === null
        ? "后台服务正在恢复，其他功能仍可使用"
        : "后台服务多次恢复失败，将稍后自动重试",
      circuitOpenUntil: decision.circuitOpenUntil === null
        ? undefined
        : new Date(decision.circuitOpenUntil).toISOString(),
    });
  }

  #emitWorkerDegraded(status: WorkerRuntimeStatus): void {
    const failedSubsystems = Object.entries(status.subsystems)
      .filter(([, subsystem]) => subsystem.consecutive_failures > 0)
      .map(([name, subsystem]) => `${name}: ${subsystem.error ?? "unknown error"}`)
      .join("; ");
    this.#emitStatus({
      state: "degraded",
      baseUrl: this.#baseUrl,
      reason: "background_degraded",
      message: "部分后台任务暂时异常，其他功能仍可使用",
      workerPid: status.pid,
      detail: sanitizeProcessOutput(failedSubsystems, [
        this.#uiAccessToken,
        this.#agentAccessToken,
      ]).slice(0, 1_000),
    });
  }

  #emitStatus(status: BackendStatus): void {
    for (const handler of this.#statusHandlers) {
      try {
        handler(status);
      } catch {
        // A renderer/application observer must not break lifecycle supervision.
      }
    }
  }

  #getStderr(processRecord: RuntimeRoleProcess): string {
    return processRecord.output.stderr.sanitizedText([
      this.#uiAccessToken,
      this.#agentAccessToken,
    ]);
  }

  #sanitizeError(error: unknown): string {
    const message = error instanceof Error ? error.message : String(error);
    return sanitizeProcessOutput(message, [
      this.#uiAccessToken,
      this.#agentAccessToken,
    ]).slice(-1_000);
  }

  #settleInitialReady(error?: unknown): void {
    if (this.#initialReadySettled) {
      return;
    }
    this.#initialReadySettled = true;
    if (error === undefined) {
      this.#ready.resolve();
    } else {
      this.#ready.reject(new Error(this.#sanitizeError(error)));
    }
  }
}

export async function waitForWorkerReady(options: {
  userDataPath: string;
  runtimeId: string;
  generation: string;
  child: ChildProcessWithoutNullStreams;
  getStderr: () => string;
  pollIntervalMs?: number;
  timeoutMs?: number;
}): Promise<WorkerRuntimeStatus> {
  const pollIntervalMs = options.pollIntervalMs ?? 100;
  const deadline = Date.now() + (options.timeoutMs ?? WORKER_STARTUP_TIMEOUT_MS);
  while (Date.now() < deadline) {
    if (options.child.exitCode !== null) {
      throw new Error(`Worker exited before readiness: ${options.getStderr().slice(-800)}`);
    }
    const status = await readWorkerRuntimeStatus(options.userDataPath);
    if (
      status?.runtime_id === options.runtimeId
      && status.generation === options.generation
    ) {
      if (status.state === "error") {
        throw new Error(`Worker startup failed: ${status.error ?? "unknown error"}`);
      }
      const heartbeatAt = Date.parse(status.heartbeat_at);
      if (
        status.state === "ready"
        && !status.draining
        && Number.isFinite(heartbeatAt)
      ) {
        return status;
      }
    }
    await abortableDelay(pollIntervalMs);
  }
  throw new Error(
    `Worker readiness timed out: ${options.getStderr().slice(-800)}`,
  );
}

async function stopRuntimeRoleProcess(processRecord: RuntimeRoleProcess): Promise<void> {
  processRecord.lifecycle.intentionalStop = true;
  const child = processRecord.child;
  const pid = child.pid;
  if (pid === undefined) {
    child.kill();
    return;
  }

  if (process.platform === "win32") {
    try {
      await execFileAsync("taskkill", ["/pid", String(pid), "/t", "/f"], {
        windowsHide: true,
      });
    } catch {
      child.kill();
    }
    await waitForChildExit(child, 3_000);
    return;
  }

  const gracefulTimeoutMs = processRecord.role === "worker"
    ? WORKER_STOP_TIMEOUT_MS
    : API_STOP_TIMEOUT_MS;
  try {
    process.kill(-pid, "SIGTERM");
  } catch {
    if (child.exitCode === null) {
      child.kill("SIGTERM");
    }
  }
  if (await waitForProcessGroupExit(pid, gracefulTimeoutMs)) {
    await waitForChildExit(child, 1_000);
    return;
  }
  try {
    process.kill(-pid, "SIGKILL");
  } catch {
    if (child.exitCode === null) {
      child.kill("SIGKILL");
    }
  }
  await Promise.all([
    waitForProcessGroupExit(pid, 1_000),
    waitForChildExit(child, 1_000),
  ]);
}

async function waitForProcessGroupExit(
  processGroup: number,
  timeoutMs: number,
): Promise<boolean> {
  const deadline = performance.now() + timeoutMs;
  while (performance.now() < deadline) {
    if (!processGroupIsRunning(processGroup)) {
      return true;
    }
    await abortableDelay(50);
  }
  return !processGroupIsRunning(processGroup);
}

function processGroupIsRunning(processGroup: number): boolean {
  try {
    process.kill(-processGroup, 0);
    return true;
  } catch (error) {
    return (error as NodeJS.ErrnoException).code === "EPERM";
  }
}

async function waitForChildExit(
  child: ChildProcessWithoutNullStreams,
  timeoutMs: number,
): Promise<boolean> {
  if (child.exitCode !== null) {
    return true;
  }
  return new Promise((resolve) => {
    const timeout = setTimeout(() => {
      child.removeListener("exit", onExit);
      resolve(false);
    }, timeoutMs);
    const onExit = () => {
      clearTimeout(timeout);
      resolve(true);
    };
    child.once("exit", onExit);
  });
}

function abortableDelay(delayMs: number, signal?: AbortSignal): Promise<void> {
  if (signal?.aborted) {
    return Promise.reject(signal.reason ?? new Error("Aborted"));
  }
  return new Promise((resolve, reject) => {
    const cleanup = () => signal?.removeEventListener("abort", onAbort);
    const timeout = setTimeout(() => {
      cleanup();
      resolve();
    }, Math.max(0, delayMs));
    const onAbort = () => {
      clearTimeout(timeout);
      cleanup();
      reject(signal?.reason ?? new Error("Aborted"));
    };
    signal?.addEventListener("abort", onAbort, { once: true });
  });
}

function createDeferred(): Deferred {
  let resolvePromise: (() => void) | undefined;
  let rejectPromise: ((error: unknown) => void) | undefined;
  const promise = new Promise<void>((resolve, reject) => {
    resolvePromise = resolve;
    rejectPromise = reject;
  });
  return {
    promise,
    resolve: () => resolvePromise?.(),
    reject: (error) => rejectPromise?.(error),
  };
}

export function generateAccessToken(): string {
  return randomBytes(32).toString("base64url");
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
  role: BackendRole;
  env: NodeJS.ProcessEnv;
  repoRoot: string;
  detached?: boolean;
}): ChildProcessWithoutNullStreams {
  if (input.isPackaged) {
    return spawn(input.backendPath, [
      "--host",
      "127.0.0.1",
      "--port",
      String(input.port),
      "--role",
      input.role,
    ], {
      env: input.env,
      windowsHide: true,
      detached: input.detached ?? shouldDetachBackend({
        isPackaged: input.isPackaged,
        platform: process.platform,
      }),
    });
  }

  return spawn(
    "uv",
    [
      "run",
      "python",
      "desktop_entry.py",
      "--host",
      "127.0.0.1",
      "--port",
      String(input.port),
      "--role",
      input.role,
    ],
    {
      cwd: path.join(input.repoRoot, "backend"),
      detached: input.detached ?? shouldDetachBackend({
        isPackaged: input.isPackaged,
        platform: process.platform,
      }),
      env: input.env,
      windowsHide: true,
    },
  );
}

export function shouldDetachBackend(input: {
  isPackaged: boolean;
  platform: NodeJS.Platform;
}): boolean {
  return input.isPackaged && input.platform !== "win32";
}

async function waitForReady(
  baseUrl: string,
  child: ChildProcessWithoutNullStreams,
  onStatus: (status: BackendStatus) => void,
  getStderr: () => string,
): Promise<void> {
  await waitForHealth(baseUrl, child, getStderr, { onStatus });
  try {
    await waitForStartupStatus(baseUrl, { child, getStderr, onStatus });
  } catch (error) {
    if (child.exitCode !== null) {
      throw new Error(`后端进程已退出：${getStderr().slice(-800)}`);
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
  const timeoutMs = options.timeoutMs ?? 60_000;
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
        databaseError: mapDatabaseError(status.error_detail),
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

function mapDatabaseError(
  detail: BackendStartupStatus["error_detail"],
): BackendDatabaseError | undefined {
  if (!detail || detail.code !== "DATABASE_REQUIRES_NEWER_APP") {
    return undefined;
  }
  return {
    code: detail.code,
    message: detail.message,
    currentAppVersion: detail.current_app_version,
    minimumSupportedAppVersion: detail.minimum_supported_app_version,
    backupDirectory: detail.backup_directory,
    suggestedActions: detail.suggested_actions,
  };
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

export async function fetchBackendRuntimeInfo(
  baseUrl: string,
  accessToken: string,
): Promise<BackendRuntimeInfo> {
  return new Promise((resolve, reject) => {
    const request = http.get(
      `${baseUrl}/api/agent/v1/runtime`,
      { headers: { Authorization: `Bearer ${accessToken}` } },
      (response) => {
        let body = "";
        response.setEncoding("utf8");
        response.on("data", (chunk: string) => {
          body += chunk;
        });
        response.on("end", () => {
          if (response.statusCode !== 200) {
            reject(new Error(`Runtime identity request failed: ${response.statusCode}`));
            return;
          }
          try {
            resolve(JSON.parse(body) as BackendRuntimeInfo);
          } catch (error) {
            reject(error);
          }
        });
      },
    );
    request.on("error", reject);
    request.setTimeout(1_000, () => {
      request.destroy(new Error("Runtime identity request timed out"));
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
    }, COMBINED_STOP_TIMEOUT_MS);
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
  if (port !== undefined) {
    await terminateUnixDesktopEntryProcesses(port);
  }
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

async function terminateUnixDesktopEntryProcesses(port: number): Promise<void> {
  try {
    await execFileAsync(
      "pkill",
      ["-TERM", "-f", `desktop_entry.py --host 127.0.0.1 --port ${port}`],
      { windowsHide: true },
    );
  } catch {
    // pkill exits non-zero when nothing matches; the direct child may already have exited.
  }
}
