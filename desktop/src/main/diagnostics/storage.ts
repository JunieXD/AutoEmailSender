import { randomUUID } from "node:crypto";
import {
  chmod,
  lstat,
  mkdir,
  open,
  readdir,
  rename,
  rm,
  stat,
  type FileHandle,
} from "node:fs/promises";
import path from "node:path";

import {
  BETA_DIAGNOSTICS_MAX_RECORD_BYTES,
  BETA_DIAGNOSTICS_MAX_SEGMENT_AGE_MS,
  BETA_DIAGNOSTICS_MAX_SEGMENT_BYTES,
  BETA_DIAGNOSTICS_MAX_TOTAL_BYTES,
  BETA_DIAGNOSTICS_RETENTION_DAYS,
  type BetaDiagnosticComponent,
  type BetaDiagnosticStream,
} from "./constants.js";

const SEGMENTS_DIRECTORY = "segments";

export type RotatingJsonlWriterOptions = {
  rootPath: string;
  component: BetaDiagnosticComponent;
  stream: BetaDiagnosticStream;
  maxSegmentBytes?: number;
  maxSegmentAgeMs?: number;
  maxRecordBytes?: number;
  now?: () => Date;
};

export type DiagnosticsStorageStatus = {
  totalBytes: number;
  segmentCount: number;
  oldestRecordAt: string | null;
  newestRecordAt: string | null;
};

type SegmentEntry = {
  path: string;
  bytes: number;
  modifiedMs: number;
  active: boolean;
};

export class RotatingJsonlWriter {
  readonly #options: Required<Omit<RotatingJsonlWriterOptions, "rootPath" | "component" | "stream">>
    & Pick<RotatingJsonlWriterOptions, "rootPath" | "component" | "stream">;
  #handle: FileHandle | null = null;
  #currentPath: string | null = null;
  #openedAtMs = 0;
  #bytesWritten = 0;
  #queue: Promise<void> = Promise.resolve();

  constructor(options: RotatingJsonlWriterOptions) {
    this.#options = {
      ...options,
      maxSegmentBytes: options.maxSegmentBytes ?? BETA_DIAGNOSTICS_MAX_SEGMENT_BYTES,
      maxSegmentAgeMs: options.maxSegmentAgeMs ?? BETA_DIAGNOSTICS_MAX_SEGMENT_AGE_MS,
      maxRecordBytes: options.maxRecordBytes ?? BETA_DIAGNOSTICS_MAX_RECORD_BYTES,
      now: options.now ?? (() => new Date()),
    };
  }

  append(record: object): Promise<void> {
    const operation = this.#queue.then(() => this.#append(record));
    this.#queue = operation.catch(() => undefined);
    return operation;
  }

  flush(): Promise<void> {
    return this.#queue.then(async () => {
      await this.#handle?.sync();
    });
  }

  close(): Promise<void> {
    const operation = this.#queue.then(async () => {
      await this.#closeCurrent();
    });
    this.#queue = operation.catch(() => undefined);
    return operation;
  }

  get currentPath(): string | null {
    return this.#currentPath;
  }

  async #append(record: object): Promise<void> {
    const serialized = `${JSON.stringify(record)}\n`;
    const recordBytes = Buffer.byteLength(serialized, "utf8");
    if (recordBytes > this.#options.maxRecordBytes) {
      throw new Error("Beta diagnostic record exceeds the bounded record size.");
    }
    const now = this.#options.now();
    if (
      this.#handle === null
      || this.#bytesWritten + recordBytes > this.#options.maxSegmentBytes
      || now.getTime() - this.#openedAtMs >= this.#options.maxSegmentAgeMs
    ) {
      await this.#rotate(now);
    }
    await this.#handle?.write(serialized, undefined, "utf8");
    this.#bytesWritten += recordBytes;
  }

  async #rotate(now: Date): Promise<void> {
    await this.#closeCurrent();
    const componentDirectory = getComponentSegmentsPath(
      this.#options.rootPath,
      this.#options.component,
    );
    await ensurePrivateDirectory(componentDirectory);
    await finalizeStaleActiveSegments(componentDirectory, this.#options.stream);
    const timestamp = now.toISOString().replace(/[^0-9]/gu, "");
    const fileName = `${this.#options.stream}-${timestamp}-${process.pid}-${randomUUID()}.active.jsonl`;
    const nextPath = path.join(componentDirectory, fileName);
    this.#handle = await open(nextPath, "wx", 0o600);
    this.#currentPath = nextPath;
    this.#openedAtMs = now.getTime();
    this.#bytesWritten = 0;
    await setPrivatePermissions(nextPath, 0o600);
  }

  async #closeCurrent(): Promise<void> {
    const handle = this.#handle;
    const currentPath = this.#currentPath;
    this.#handle = null;
    this.#currentPath = null;
    if (handle === null) {
      return;
    }
    await handle.sync().catch(() => undefined);
    await handle.close();
    if (currentPath?.endsWith(".active.jsonl")) {
      await renameActiveSegment(currentPath).catch(() => undefined);
    }
  }
}

export function getComponentSegmentsPath(
  rootPath: string,
  component: BetaDiagnosticComponent,
): string {
  return path.join(rootPath, SEGMENTS_DIRECTORY, component);
}

export async function getDiagnosticsStorageStatus(
  rootPath: string,
): Promise<DiagnosticsStorageStatus> {
  const segments = await listDiagnosticSegments(rootPath);
  if (segments.length === 0) {
    return {
      totalBytes: 0,
      segmentCount: 0,
      oldestRecordAt: null,
      newestRecordAt: null,
    };
  }
  const modifiedTimes = segments.map((entry) => entry.modifiedMs);
  return {
    totalBytes: segments.reduce((total, entry) => total + entry.bytes, 0),
    segmentCount: segments.length,
    oldestRecordAt: new Date(Math.min(...modifiedTimes)).toISOString(),
    newestRecordAt: new Date(Math.max(...modifiedTimes)).toISOString(),
  };
}

export async function pruneDiagnosticsStorage(
  rootPath: string,
  options: {
    now?: Date;
    retentionDays?: number;
    maxTotalBytes?: number;
    protectedPaths?: ReadonlySet<string>;
  } = {},
): Promise<DiagnosticsStorageStatus> {
  const now = options.now ?? new Date();
  const retentionDays = options.retentionDays ?? BETA_DIAGNOSTICS_RETENTION_DAYS;
  const maxTotalBytes = options.maxTotalBytes ?? BETA_DIAGNOSTICS_MAX_TOTAL_BYTES;
  const cutoffMs = now.getTime() - Math.max(0, retentionDays) * 24 * 60 * 60 * 1000;
  const protectedPaths = options.protectedPaths ?? new Set<string>();
  let segments = (await listDiagnosticSegments(rootPath))
    .sort((left, right) => left.modifiedMs - right.modifiedMs);

  for (const segment of segments) {
    if (segment.active || segment.modifiedMs >= cutoffMs || protectedPaths.has(segment.path)) {
      continue;
    }
    await rm(segment.path, { force: true }).catch(() => undefined);
  }

  segments = (await listDiagnosticSegments(rootPath))
    .sort((left, right) => left.modifiedMs - right.modifiedMs);
  let totalBytes = segments.reduce((total, entry) => total + entry.bytes, 0);
  for (const segment of segments) {
    if (totalBytes <= maxTotalBytes) {
      break;
    }
    if (segment.active || protectedPaths.has(segment.path)) {
      continue;
    }
    try {
      await rm(segment.path, { force: true });
      totalBytes -= segment.bytes;
    } catch {
      // Retention failures are diagnostic state only and must not affect the app.
    }
  }
  return getDiagnosticsStorageStatus(rootPath);
}

export async function clearDiagnosticSegments(rootPath: string): Promise<void> {
  await rm(path.join(rootPath, SEGMENTS_DIRECTORY), {
    recursive: true,
    force: true,
  });
}

export async function listDiagnosticSegments(rootPath: string): Promise<SegmentEntry[]> {
  const segmentRoot = path.join(rootPath, SEGMENTS_DIRECTORY);
  let components;
  try {
    components = await readdir(segmentRoot, { withFileTypes: true });
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code === "ENOENT") {
      return [];
    }
    throw error;
  }
  const result: SegmentEntry[] = [];
  for (const component of components) {
    if (!component.isDirectory() || component.isSymbolicLink()) {
      continue;
    }
    const componentPath = path.join(segmentRoot, component.name);
    const files = await readdir(componentPath, { withFileTypes: true });
    for (const file of files) {
      if (!file.isFile() || file.isSymbolicLink() || !file.name.endsWith(".jsonl")) {
        continue;
      }
      const filePath = path.join(componentPath, file.name);
      const fileStat = await stat(filePath);
      result.push({
        path: filePath,
        bytes: fileStat.size,
        modifiedMs: fileStat.mtimeMs,
        active: file.name.endsWith(".active.jsonl"),
      });
    }
  }
  return result;
}

async function finalizeStaleActiveSegments(
  componentPath: string,
  stream: BetaDiagnosticStream,
): Promise<void> {
  const entries = await readdir(componentPath, { withFileTypes: true });
  for (const entry of entries) {
    if (
      !entry.isFile()
      || entry.isSymbolicLink()
      || !entry.name.startsWith(`${stream}-`)
      || !entry.name.endsWith(".active.jsonl")
    ) {
      continue;
    }
    await renameActiveSegment(path.join(componentPath, entry.name)).catch(() => undefined);
  }
}

async function renameActiveSegment(activePath: string): Promise<void> {
  const finalPath = activePath.replace(/\.active\.jsonl$/u, ".jsonl");
  await rename(activePath, finalPath);
}

async function ensurePrivateDirectory(directoryPath: string): Promise<void> {
  await mkdir(directoryPath, { recursive: true, mode: 0o700 });
  const directoryStat = await lstat(directoryPath);
  if (!directoryStat.isDirectory() || directoryStat.isSymbolicLink()) {
    throw new Error("Beta diagnostics path is not a private directory.");
  }
  await setPrivatePermissions(directoryPath, 0o700);
}

async function setPrivatePermissions(targetPath: string, mode: number): Promise<void> {
  if (process.platform === "win32") {
    return;
  }
  await chmod(targetPath, mode);
}
