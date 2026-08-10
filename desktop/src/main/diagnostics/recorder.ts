import { randomUUID } from "node:crypto";
import { constants as fsConstants } from "node:fs";
import {
  chmod,
  lstat,
  mkdir,
  open,
  readdir,
  rename,
  rm,
  stat,
} from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { performance } from "node:perf_hooks";

import type { BackendMode } from "../backend/types.js";
import {
  BETA_DIAGNOSTICS_RESOURCE_SAMPLE_INTERVAL_MS,
  BETA_DIAGNOSTICS_SCHEMA_VERSION,
  getBetaDiagnosticsRoot,
} from "./constants.js";
import {
  sanitizeTimelineDetails,
  type RedactionContext,
} from "./redaction.js";
import {
  clearDiagnosticSegments,
  getDiagnosticsStorageStatus,
  pruneDiagnosticsStorage,
  RotatingJsonlWriter,
  type DiagnosticsStorageStatus,
} from "./storage.js";

const INSTALLATION_FILE_NAME = "installation.json";
const ACTIVE_SESSION_FILE_NAME = "active-session.json";
const INSTALLATION_SCHEMA_VERSION = 1;
const WALL_CLOCK_JUMP_THRESHOLD_MS = 5_000;
const MAX_PRIVATE_METADATA_BYTES = 64 * 1024;
const UUID_PATTERN = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/iu;

export type DesktopDiagnosticProcessSnapshot = {
  apiPid?: number;
  workerPid?: number;
  playwrightProcessCount?: number;
};

export type DesktopBetaDiagnosticsStatus = DiagnosticsStorageStatus & {
  enabled: boolean;
  schemaVersion: number;
  retentionDays: number;
  maxTotalBytes: number;
  lastError?: string;
};

export type DesktopBetaDiagnosticsRecorderOptions = {
  userDataPath: string;
  homePath: string;
  appVersion: string;
  enabled: boolean;
  getCurrentMode: () => BackendMode | null;
  getProcessSnapshot: () => DesktopDiagnosticProcessSnapshot;
  sampleIntervalMs?: number;
  now?: () => Date;
  monotonicNow?: () => number;
};

export class DesktopBetaDiagnosticsRecorder {
  readonly #options: Required<Pick<DesktopBetaDiagnosticsRecorderOptions, "sampleIntervalMs" | "now" | "monotonicNow">>
    & Omit<DesktopBetaDiagnosticsRecorderOptions, "sampleIntervalMs" | "now" | "monotonicNow">;
  readonly #rootPath: string;
  readonly #sessionId = randomUUID();
  readonly #redactionContext: RedactionContext;
  readonly #timeline: RotatingJsonlWriter;
  readonly #resources: RotatingJsonlWriter;
  #sampleTimer: NodeJS.Timeout | null = null;
  #resourceSampleQueue: Promise<void> = Promise.resolve();
  #started = false;
  #stopping = false;
  #clearing = false;
  #checkpointing = false;
  #lastError: string | undefined;
  #lastWallMs: number | null = null;
  #lastMonotonicMs: number | null = null;
  #lastCpuUsage = process.cpuUsage();
  #lastCpuSampleMonotonicMs = performance.now();

  constructor(options: DesktopBetaDiagnosticsRecorderOptions) {
    this.#options = {
      ...options,
      sampleIntervalMs: options.sampleIntervalMs
        ?? BETA_DIAGNOSTICS_RESOURCE_SAMPLE_INTERVAL_MS,
      now: options.now ?? (() => new Date()),
      monotonicNow: options.monotonicNow ?? (() => performance.now()),
    };
    this.#rootPath = getBetaDiagnosticsRoot(options.userDataPath);
    this.#redactionContext = {
      homePath: options.homePath,
      userDataPath: options.userDataPath,
      machineName: os.hostname(),
    };
    this.#timeline = new RotatingJsonlWriter({
      rootPath: this.#rootPath,
      component: "electron",
      stream: "timeline",
      now: this.#options.now,
    });
    this.#resources = new RotatingJsonlWriter({
      rootPath: this.#rootPath,
      component: "electron",
      stream: "resource-samples",
      now: this.#options.now,
    });
  }

  get rootPath(): string {
    return this.#rootPath;
  }

  get sessionId(): string {
    return this.#sessionId;
  }

  get redactionContext(): RedactionContext {
    return this.#redactionContext;
  }

  async start(): Promise<void> {
    if (!this.#options.enabled || this.#started) {
      return;
    }
    this.#started = true;
    try {
      await ensureInstallationId(this.#rootPath);
      const previousSession = await readActiveSession(this.#rootPath);
      if (previousSession !== null) {
        await this.recordTimeline("previous_session_abnormal", {
          previous_version: previousSession.app_version,
          reason: "active_session_marker_present",
        }, "warning");
      }
      await writeActiveSession(this.#rootPath, {
        schema_version: BETA_DIAGNOSTICS_SCHEMA_VERSION,
        session_id: this.#sessionId,
        app_version: this.#options.appVersion,
        started_at: this.#options.now().toISOString(),
      });
      await this.recordTimeline("desktop_session_started", {
        process_id: process.pid,
        effective_mode: this.#options.getCurrentMode(),
        current_version: this.#options.appVersion,
      });
      await this.recordResourceSample();
      await this.#prune();
      this.#sampleTimer = setInterval(() => {
        void this.recordResourceSample();
      }, this.#options.sampleIntervalMs);
      this.#sampleTimer.unref?.();
    } catch (error) {
      this.#captureError(error);
    }
  }

  async recordTimeline(
    event: string,
    details?: Record<string, unknown>,
    severity: "debug" | "info" | "warning" | "error" = "info",
  ): Promise<void> {
    if (
      !this.#options.enabled
      || this.#stopping
      || this.#clearing
      || this.#checkpointing
      || !isSafeEventName(event)
    ) {
      return;
    }
    try {
      const timestamp = this.#timestamp();
      await this.#timeline.append({
        schema_version: BETA_DIAGNOSTICS_SCHEMA_VERSION,
        stream: "timeline",
        wall_time: timestamp.wallTime,
        monotonic_ms: timestamp.monotonicMs,
        component: "electron",
        session_id: this.#sessionId,
        event,
        severity,
        details: sanitizeTimelineDetails(details, this.#redactionContext),
      });
      await this.#recordClockJumpIfNeeded(timestamp);
    } catch (error) {
      this.#captureError(error);
    }
  }

  async recordProblemMarker(input: {
    category: string;
    note?: string;
  }): Promise<string> {
    const markedAt = this.#options.now().toISOString();
    await this.recordTimeline("problem_marked", {
      marker_category: input.category,
      ...(input.note?.trim() ? { note: input.note.trim() } : {}),
    }, "warning");
    return markedAt;
  }

  recordResourceSample(): Promise<void> {
    if (
      !this.#options.enabled
      || this.#stopping
      || this.#clearing
      || this.#checkpointing
    ) {
      return Promise.resolve();
    }
    const operation = this.#resourceSampleQueue.then(() => this.#recordResourceSample());
    this.#resourceSampleQueue = operation.catch(() => undefined);
    return operation;
  }

  async #recordResourceSample(): Promise<void> {
    if (
      !this.#options.enabled
      || this.#stopping
      || this.#clearing
      || this.#checkpointing
    ) {
      return;
    }
    try {
      const timestamp = this.#timestamp();
      const processSnapshot = this.#options.getProcessSnapshot();
      const fileSizes = await collectBoundedFileSizes(this.#options.userDataPath);
      const cpuUsage = process.cpuUsage(this.#lastCpuUsage);
      const cpuElapsedMs = Math.max(
        1,
        timestamp.monotonicMs - this.#lastCpuSampleMonotonicMs,
      );
      this.#lastCpuUsage = process.cpuUsage();
      this.#lastCpuSampleMonotonicMs = timestamp.monotonicMs;
      const cpuPercent = Math.min(
        100 * Math.max(1, os.cpus().length),
        ((cpuUsage.user + cpuUsage.system) / 1000 / cpuElapsedMs) * 100,
      );
      await this.#resources.append({
        schema_version: BETA_DIAGNOSTICS_SCHEMA_VERSION,
        stream: "resource-samples",
        wall_time: timestamp.wallTime,
        monotonic_ms: timestamp.monotonicMs,
        component: "electron",
        session_id: this.#sessionId,
        cpu_percent: round(cpuPercent, 2),
        rss_bytes: process.memoryUsage().rss,
        handles_or_fds: process.getActiveResourcesInfo().length,
        child_processes: Number(processSnapshot.apiPid !== undefined)
          + Number(processSnapshot.workerPid !== undefined),
        playwright_processes: processSnapshot.playwrightProcessCount ?? 0,
        database_bytes: fileSizes.databaseBytes,
        wal_bytes: fileSizes.walBytes,
        shm_bytes: fileSizes.shmBytes,
        logs_bytes: fileSizes.logsBytes,
        runtime_bytes: fileSizes.runtimeBytes,
        api_present: processSnapshot.apiPid !== undefined,
        worker_present: processSnapshot.workerPid !== undefined,
      });
      await this.#recordClockJumpIfNeeded(timestamp);
      await this.#prune();
    } catch (error) {
      this.#captureError(error);
    }
  }

  async flush(): Promise<void> {
    if (!this.#options.enabled || this.#clearing || this.#checkpointing) {
      return;
    }
    this.#checkpointing = true;
    try {
      await this.#resourceSampleQueue;
      await Promise.all([
        this.#timeline.close(),
        this.#resources.close(),
      ]).catch((error: unknown) => this.#captureError(error));
    } finally {
      this.#checkpointing = false;
    }
  }

  async getStatus(): Promise<DesktopBetaDiagnosticsStatus> {
    const storage = await getDiagnosticsStorageStatus(this.#rootPath).catch(() => ({
      totalBytes: 0,
      segmentCount: 0,
      oldestRecordAt: null,
      newestRecordAt: null,
    }));
    return {
      enabled: this.#options.enabled,
      schemaVersion: BETA_DIAGNOSTICS_SCHEMA_VERSION,
      retentionDays: 14,
      maxTotalBytes: 64 * 1024 * 1024,
      ...storage,
      ...(this.#lastError ? { lastError: this.#lastError } : {}),
    };
  }

  async clear(): Promise<DesktopBetaDiagnosticsStatus> {
    if (!this.#options.enabled || this.#clearing) {
      return this.getStatus();
    }
    this.#clearing = true;
    try {
      await this.#resourceSampleQueue;
      await Promise.all([
        this.#timeline.close().catch(() => undefined),
        this.#resources.close().catch(() => undefined),
      ]);
      await clearDiagnosticSegments(this.#rootPath).catch((error) => this.#captureError(error));
    } finally {
      this.#clearing = false;
    }
    await this.recordTimeline("diagnostics_cleared");
    return this.getStatus();
  }

  async stop(): Promise<void> {
    if (!this.#options.enabled || !this.#started || this.#stopping) {
      return;
    }
    if (this.#sampleTimer !== null) {
      clearInterval(this.#sampleTimer);
      this.#sampleTimer = null;
    }
    await this.recordTimeline("desktop_session_stopping", {
      effective_mode: this.#options.getCurrentMode(),
    });
    this.#stopping = true;
    await this.#resourceSampleQueue;
    await Promise.all([
      this.#timeline.close().catch(() => undefined),
      this.#resources.close().catch(() => undefined),
    ]);
    await removeOwnedActiveSession(this.#rootPath, this.#sessionId).catch(() => undefined);
  }

  #timestamp(): { wallTime: string; wallMs: number; monotonicMs: number } {
    const wall = this.#options.now();
    return {
      wallTime: wall.toISOString(),
      wallMs: wall.getTime(),
      monotonicMs: round(this.#options.monotonicNow(), 3),
    };
  }

  async #recordClockJumpIfNeeded(timestamp: {
    wallTime: string;
    wallMs: number;
    monotonicMs: number;
  }): Promise<void> {
    const previousWallMs = this.#lastWallMs;
    const previousMonotonicMs = this.#lastMonotonicMs;
    this.#lastWallMs = timestamp.wallMs;
    this.#lastMonotonicMs = timestamp.monotonicMs;
    if (previousWallMs === null || previousMonotonicMs === null) {
      return;
    }
    const offset = (timestamp.wallMs - previousWallMs)
      - (timestamp.monotonicMs - previousMonotonicMs);
    if (Math.abs(offset) < WALL_CLOCK_JUMP_THRESHOLD_MS) {
      return;
    }
    await this.#timeline.append({
      schema_version: BETA_DIAGNOSTICS_SCHEMA_VERSION,
      stream: "timeline",
      wall_time: timestamp.wallTime,
      monotonic_ms: timestamp.monotonicMs,
      component: "electron",
      session_id: this.#sessionId,
      event: "wall_clock_jump_detected",
      severity: "warning",
      details: { clock_offset_ms: round(offset, 3) },
    });
  }

  async #prune(): Promise<void> {
    const protectedPaths = new Set<string>();
    if (this.#timeline.currentPath) {
      protectedPaths.add(this.#timeline.currentPath);
    }
    if (this.#resources.currentPath) {
      protectedPaths.add(this.#resources.currentPath);
    }
    await pruneDiagnosticsStorage(this.#rootPath, { protectedPaths });
  }

  #captureError(error: unknown): void {
    this.#lastError = sanitizeTimelineDetails({
      note: error instanceof Error ? error.message : String(error),
    }, this.#redactionContext).note?.toString() ?? "诊断记录失败";
  }
}

type ActiveSession = {
  schema_version: number;
  session_id: string;
  app_version: string;
  started_at: string;
};

export async function readInstallationId(rootPath: string): Promise<string> {
  return ensureInstallationId(rootPath);
}

async function ensureInstallationId(rootPath: string): Promise<string> {
  try {
    const parsed = JSON.parse(
      await readBoundedPrivateMetadata(rootPath, INSTALLATION_FILE_NAME),
    ) as Record<string, unknown>;
    if (
      parsed.schema_version === INSTALLATION_SCHEMA_VERSION
      && typeof parsed.installation_id === "string"
      && UUID_PATTERN.test(parsed.installation_id)
    ) {
      return parsed.installation_id;
    }
  } catch {
    // A missing or invalid local id is replaced with a fresh random id.
  }
  const installationId = randomUUID();
  await writePrivateJson(rootPath, INSTALLATION_FILE_NAME, {
    schema_version: INSTALLATION_SCHEMA_VERSION,
    installation_id: installationId,
    created_at: new Date().toISOString(),
  });
  return installationId;
}

async function readActiveSession(rootPath: string): Promise<ActiveSession | null> {
  try {
    const parsed = JSON.parse(
      await readBoundedPrivateMetadata(rootPath, ACTIVE_SESSION_FILE_NAME),
    ) as Partial<ActiveSession>;
    if (
      parsed.schema_version === BETA_DIAGNOSTICS_SCHEMA_VERSION
      && typeof parsed.session_id === "string"
      && typeof parsed.app_version === "string"
      && typeof parsed.started_at === "string"
    ) {
      return parsed as ActiveSession;
    }
  } catch {
    return null;
  }
  return null;
}

async function writeActiveSession(rootPath: string, session: ActiveSession): Promise<void> {
  await writePrivateJson(rootPath, ACTIVE_SESSION_FILE_NAME, session);
}

async function removeOwnedActiveSession(rootPath: string, sessionId: string): Promise<void> {
  const currentSession = await readActiveSession(rootPath);
  if (currentSession?.session_id !== sessionId) {
    return;
  }
  await rm(path.join(rootPath, ACTIVE_SESSION_FILE_NAME), { force: true });
}

async function writePrivateJson(
  rootPath: string,
  fileName: string,
  value: object,
): Promise<void> {
  await mkdir(rootPath, { recursive: true, mode: 0o700 });
  const rootStat = await lstat(rootPath);
  if (!rootStat.isDirectory() || rootStat.isSymbolicLink()) {
    throw new Error("Beta diagnostics root is not a private directory.");
  }
  if (process.platform !== "win32") {
    await chmod(rootPath, 0o700);
  }
  const targetPath = path.join(rootPath, fileName);
  const temporaryPath = path.join(rootPath, `.${fileName}-${process.pid}-${randomUUID()}.tmp`);
  const handle = await open(temporaryPath, "wx", 0o600);
  try {
    await handle.writeFile(`${JSON.stringify(value, null, 2)}\n`, "utf8");
    await handle.sync();
  } finally {
    await handle.close();
  }
  try {
    await rename(temporaryPath, targetPath);
  } catch (error) {
    await rm(temporaryPath, { force: true }).catch(() => undefined);
    throw error;
  }
  if (process.platform !== "win32") {
    await chmod(targetPath, 0o600);
  }
}

async function readBoundedPrivateMetadata(
  rootPath: string,
  fileName: string,
): Promise<string> {
  const filePath = path.join(rootPath, fileName);
  const fileStat = await lstat(filePath);
  if (
    !fileStat.isFile()
    || fileStat.isSymbolicLink()
    || fileStat.size > MAX_PRIVATE_METADATA_BYTES
  ) {
    throw new Error("Beta diagnostics metadata is not a bounded regular file.");
  }
  const noFollow = typeof fsConstants.O_NOFOLLOW === "number" ? fsConstants.O_NOFOLLOW : 0;
  const handle = await open(filePath, fsConstants.O_RDONLY | noFollow);
  try {
    const openedStat = await handle.stat();
    if (!openedStat.isFile() || openedStat.size > MAX_PRIVATE_METADATA_BYTES) {
      throw new Error("Beta diagnostics metadata changed while being read.");
    }
    return handle.readFile("utf8");
  } finally {
    await handle.close();
  }
}

async function collectBoundedFileSizes(userDataPath: string): Promise<{
  databaseBytes: number;
  walBytes: number;
  shmBytes: number;
  logsBytes: number;
  runtimeBytes: number;
}> {
  const databasePath = path.join(userDataPath, "auto_email_sender.db");
  const [databaseBytes, walBytes, shmBytes, logsBytes, runtimeBytes] = await Promise.all([
    safeFileSize(databasePath),
    safeFileSize(`${databasePath}-wal`),
    safeFileSize(`${databasePath}-shm`),
    safeDirectorySize(path.join(userDataPath, "logs"), 4_096),
    safeDirectorySize(path.join(userDataPath, "runtime"), 256),
  ]);
  return { databaseBytes, walBytes, shmBytes, logsBytes, runtimeBytes };
}

async function safeFileSize(filePath: string): Promise<number> {
  try {
    const fileStat = await lstat(filePath);
    return fileStat.isFile() && !fileStat.isSymbolicLink() ? fileStat.size : 0;
  } catch {
    return 0;
  }
}

async function safeDirectorySize(directoryPath: string, maxEntries: number): Promise<number> {
  let entries;
  try {
    entries = await readdir(directoryPath, { withFileTypes: true });
  } catch {
    return 0;
  }
  let total = 0;
  for (const entry of entries.slice(0, maxEntries)) {
    if (!entry.isFile() || entry.isSymbolicLink()) {
      continue;
    }
    try {
      total += (await stat(path.join(directoryPath, entry.name))).size;
    } catch {
      continue;
    }
  }
  return total;
}

function isSafeEventName(value: string): boolean {
  return /^[a-z][a-z0-9_.-]{0,95}$/u.test(value);
}

function round(value: number, digits: number): number {
  const factor = 10 ** digits;
  return Math.round(value * factor) / factor;
}
