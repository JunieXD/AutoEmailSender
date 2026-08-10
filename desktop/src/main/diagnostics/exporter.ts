import { createHash, randomUUID } from "node:crypto";
import { constants as fsConstants, createWriteStream } from "node:fs";
import {
  chmod,
  lstat,
  mkdir,
  open,
  rename,
  rm,
} from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { ZipFile } from "yazl";

import type { BackendMode } from "../backend/types.js";
import {
  BETA_DIAGNOSTICS_MAX_RECORD_BYTES,
  BETA_DIAGNOSTICS_MAX_SEGMENT_BYTES,
  BETA_DIAGNOSTICS_SCHEMA_VERSION,
  type BetaDiagnosticComponent,
} from "./constants.js";
import { sanitizeTimelineDetails } from "./redaction.js";
import { readInstallationId } from "./recorder.js";
import { listDiagnosticSegments } from "./storage.js";

const MAX_SOURCE_LOG_BYTES = 1024 * 1024;
const MAX_BUNDLE_ENTRIES_BYTES = 80 * 1024 * 1024;
const SAFE_IDENTIFIER_PATTERN = /^[A-Za-z0-9_.:+/-]{1,160}$/u;
const SAFE_EVENT_PATTERN = /^[a-z][a-z0-9_.-]{0,95}$/u;
const ISO_TIMESTAMP_PATTERN = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$/u;
const COMPONENTS = new Set<BetaDiagnosticComponent>([
  "electron",
  "api",
  "worker",
  "combined",
]);
const SEVERITIES = new Set(["debug", "info", "warning", "error"]);
const RESOURCE_NUMBER_KEYS = new Set([
  "cpu_percent",
  "rss_bytes",
  "handles_or_fds",
  "threads",
  "child_processes",
  "playwright_processes",
  "database_bytes",
  "wal_bytes",
  "shm_bytes",
  "logs_bytes",
  "runtime_bytes",
  "healthy_subsystems",
  "degraded_subsystems",
  "failed_subsystems",
]);
const RESOURCE_BOOLEAN_KEYS = new Set([
  "api_present",
  "worker_present",
]);
const BACKEND_METRIC_KEYS = new Set([
  "schema_version",
  "generated_at",
  "available",
  "workloads",
  "kind",
  "queued",
  "running",
  "succeeded",
  "failed",
  "interrupted",
  "recovered",
  "oldest_queue_age_seconds",
  "oldest_running_age_seconds",
  "average_duration_seconds",
  "maximum_duration_seconds",
  "invariants",
  "sending_count",
  "duplicate_delivery_attempt_groups",
  "orphaned_claim_count",
  "alembic_revision",
  "integrity_check",
  "foreign_key_violation_count",
  "journal_mode",
  "busy_timeout_ms",
  "database_bytes",
  "wal_bytes",
  "shm_bytes",
  "backup_count",
  "newest_backup_age_seconds",
  "lock_errors_1h",
  "busy_errors_1h",
  "slow_queries_1h",
  "maximum_query_ms_1h",
  "total_1h",
  "total_24h",
  "levels_24h",
  "debug",
  "info",
  "warning",
  "error",
  "categories_24h",
  "category",
  "event_count",
  "error_count",
]);
const WORKLOAD_KINDS = new Set([
  "dispatcher",
  "imap_sync",
  "imap_history",
  "batch_draft",
  "matching",
  "crawler",
]);
const SAFE_METRIC_ENUM_VALUES = new Set([
  "ok",
  "unavailable",
  "unknown",
  "error",
  "wal",
  "delete",
  "truncate",
  "persist",
  "memory",
  ...WORKLOAD_KINDS,
  "mail",
  "imap",
  "draft",
  "matching",
  "crawler",
  "runtime",
  "sqlite",
  "system",
  "llm",
]);

export type DesktopBetaDiagnosticsRange = "1h" | "24h" | "7d" | "all";

export type DesktopBetaBuildIdentity = {
  sourceBranch?: string | null;
  releaseSha?: string | null;
  candidateRunId?: string | null;
  candidateAssetName?: string | null;
  candidateAssetSha256?: string | null;
};

export type DesktopBetaBackendSnapshot = {
  workloadSummary: unknown;
  databaseHealth: unknown;
  operationLogSummary: unknown;
};

export type BetaDiagnosticsBundleEntry = {
  name: string;
  content: Buffer;
};

export type BuiltBetaDiagnosticsBundle = {
  reportId: string;
  exportedAt: string;
  partial: boolean;
  missingSections: string[];
  entries: BetaDiagnosticsBundleEntry[];
};

export type BuildBetaDiagnosticsBundleOptions = {
  rootPath: string;
  userDataPath: string;
  appVersion: string;
  requestedMode: BackendMode;
  effectiveMode: BackendMode;
  range: DesktopBetaDiagnosticsRange;
  buildIdentity?: DesktopBetaBuildIdentity;
  getBackendSnapshot?: () => Promise<DesktopBetaBackendSnapshot>;
  flush?: () => Promise<void>;
  now?: () => Date;
  platform?: NodeJS.Platform;
  arch?: string;
};

type NormalizedTimelineRecord = {
  schema_version: number;
  stream: "timeline";
  wall_time: string;
  monotonic_ms: number;
  component: BetaDiagnosticComponent;
  session_id: string;
  event: string;
  severity: "debug" | "info" | "warning" | "error";
  details: Record<string, string | number | boolean | null>;
};

type NormalizedResourceRecord = {
  schema_version: number;
  stream: "resource-samples";
  wall_time: string;
  monotonic_ms: number;
  component: BetaDiagnosticComponent;
  session_id: string;
  [key: string]: string | number | boolean;
};

export async function buildBetaDiagnosticsBundle(
  options: BuildBetaDiagnosticsBundleOptions,
): Promise<BuiltBetaDiagnosticsBundle> {
  const now = options.now?.() ?? new Date();
  const exportedAt = now.toISOString();
  const reportId = randomUUID();
  await options.flush?.().catch(() => undefined);

  const cutoffMs = getRangeCutoffMs(options.range, now);
  const readResult = await readNormalizedRecords(options.rootPath, cutoffMs);
  const missingSections = [...readResult.warnings];
  const backendSnapshot = await readBackendSnapshot(options.getBackendSnapshot);
  if (backendSnapshot.missingReason !== null) {
    missingSections.push(backendSnapshot.missingReason);
  }

  const sourceLogs = await readSourceLogSummaries(options.userDataPath);
  const installationId = await readInstallationId(options.rootPath);
  const channel = inferReleaseChannel(options.appVersion);
  const partial = missingSections.length > 0;
  const timeline = readResult.timeline.sort(compareDiagnosticRecords);
  const resources = readResult.resources.sort(compareDiagnosticRecords);
  const timelineText = toJsonl(timeline);
  const resourceText = toJsonl(resources);
  const summary = buildBundleSummary({
    timeline,
    resources,
    partial,
    missingSections,
    exportedAt,
  });
  const manifest = {
    schema_version: BETA_DIAGNOSTICS_SCHEMA_VERSION,
    report_id: reportId,
    installation_id: installationId,
    exported_at: exportedAt,
    range: options.range,
    range_start: cutoffMs === null ? null : new Date(cutoffMs).toISOString(),
    partial,
    missing_sections: missingSections,
    app: {
      name: "Auto Email Sender",
      version: options.appVersion,
      channel,
      source_branch: sanitizeBuildIdentityValue(options.buildIdentity?.sourceBranch, "branch"),
      release_sha: sanitizeBuildIdentityValue(options.buildIdentity?.releaseSha, "sha"),
      candidate_run_id: sanitizeBuildIdentityValue(options.buildIdentity?.candidateRunId, "run"),
      candidate_asset_name: sanitizeBuildIdentityValue(
        options.buildIdentity?.candidateAssetName,
        "asset",
      ),
      candidate_asset_sha256: sanitizeBuildIdentityValue(
        options.buildIdentity?.candidateAssetSha256,
        "sha256",
      ),
    },
    system: {
      platform: options.platform ?? process.platform,
      arch: options.arch ?? process.arch,
      os_release: sanitizeSystemRelease(os.release()),
    },
    backend: {
      requested_mode: options.requestedMode,
      effective_mode: options.effectiveMode,
    },
    record_counts: {
      timeline: timeline.length,
      resource_samples: resources.length,
      source_log_summaries: sourceLogs.recordCount,
    },
  };

  const entries = new Map<string, Buffer>();
  addJsonEntry(entries, "manifest.json", manifest);
  addTextEntry(entries, "timeline.jsonl", timelineText);
  addTextEntry(entries, "resource-samples.jsonl", resourceText);
  addJsonEntry(entries, "workload-summary.json", backendSnapshot.workloadSummary);
  addJsonEntry(entries, "database-health.json", backendSnapshot.databaseHealth);
  addJsonEntry(entries, "logs/operation-summary.json", backendSnapshot.operationLogSummary);
  for (const component of COMPONENTS) {
    addTextEntry(
      entries,
      `logs/${component}.jsonl`,
      toJsonl(timeline.filter((record) => record.component === component)),
    );
  }
  addTextEntry(entries, "logs/startup-summary.jsonl", sourceLogs.startup);
  addTextEntry(entries, "logs/backend-errors-summary.jsonl", sourceLogs.backendErrors);
  addJsonEntry(entries, "summary.json", summary);
  addTextEntry(entries, "README.txt", buildReadme({ partial, missingSections }));

  assertBundleEntryBudget(entries);
  const checksumLines = [...entries.entries()]
    .sort(([left], [right]) => left.localeCompare(right))
    .map(([name, content]) => `${sha256(content)}  ${name}`);
  addTextEntry(entries, "checksums.sha256", `${checksumLines.join("\n")}\n`);

  return {
    reportId,
    exportedAt,
    partial,
    missingSections,
    entries: [...entries.entries()]
      .sort(([left], [right]) => left.localeCompare(right))
      .map(([name, content]) => ({ name, content })),
  };
}

export async function writeBetaDiagnosticsZip(
  outputPath: string,
  bundle: BuiltBetaDiagnosticsBundle,
): Promise<void> {
  const parentPath = path.dirname(outputPath);
  await mkdir(parentPath, { recursive: true });
  const temporaryPath = path.join(
    parentPath,
    `.${path.basename(outputPath)}-${process.pid}-${randomUUID()}.tmp`,
  );
  const zipFile = new ZipFile();
  const output = createWriteStream(temporaryPath, {
    flags: "wx",
    mode: 0o600,
  });
  const mtime = new Date(bundle.exportedAt);

  try {
    const completion = new Promise<void>((resolve, reject) => {
      zipFile.outputStream.once("error", reject);
      output.once("error", reject);
      output.once("close", resolve);
      zipFile.outputStream.pipe(output);
    });
    for (const entry of bundle.entries) {
      assertSafeZipEntryName(entry.name);
      zipFile.addBuffer(entry.content, entry.name, {
        mtime,
        mode: 0o600,
        compress: true,
      });
    }
    zipFile.end();
    await completion;
    if (process.platform !== "win32") {
      await chmod(temporaryPath, 0o600);
    }
    await commitCompletedExport(temporaryPath, outputPath);
  } catch (error) {
    output.destroy();
    await rm(temporaryPath, { force: true }).catch(() => undefined);
    throw error;
  }
}

async function commitCompletedExport(temporaryPath: string, outputPath: string): Promise<void> {
  try {
    await rename(temporaryPath, outputPath);
    return;
  } catch (error) {
    const code = (error as NodeJS.ErrnoException).code;
    if (code !== "EEXIST" && code !== "EPERM" && code !== "ENOTEMPTY") {
      throw error;
    }
  }

  const existing = await lstat(outputPath);
  if (!existing.isFile() || existing.isSymbolicLink()) {
    throw new Error("Refusing to replace a non-regular Beta diagnostic export target.");
  }
  const backupPath = path.join(
    path.dirname(outputPath),
    `.${path.basename(outputPath)}-${process.pid}-${randomUUID()}.previous`,
  );
  await rename(outputPath, backupPath);
  try {
    await rename(temporaryPath, outputPath);
  } catch (error) {
    await rename(backupPath, outputPath).catch(() => undefined);
    throw error;
  }
  await rm(backupPath, { force: true }).catch(() => undefined);
}

export function getBetaDiagnosticsExportFileName(now = new Date()): string {
  return `auto-email-sender-beta-diagnostics-${now.toISOString().replace(/[^0-9]/gu, "")}.zip`;
}

export function isDesktopBetaDiagnosticsRange(
  value: unknown,
): value is DesktopBetaDiagnosticsRange {
  return value === "1h" || value === "24h" || value === "7d" || value === "all";
}

async function readNormalizedRecords(
  rootPath: string,
  cutoffMs: number | null,
): Promise<{
  timeline: NormalizedTimelineRecord[];
  resources: NormalizedResourceRecord[];
  warnings: string[];
}> {
  const timeline: NormalizedTimelineRecord[] = [];
  const resources: NormalizedResourceRecord[] = [];
  const warnings = new Set<string>();
  const segments = await listDiagnosticSegments(rootPath).catch(() => {
    warnings.add("diagnostic_segments_unavailable");
    return [];
  });

  for (const segment of segments) {
    let content: string;
    try {
      content = await readBoundedRegularFile(
        segment.path,
        BETA_DIAGNOSTICS_MAX_SEGMENT_BYTES + BETA_DIAGNOSTICS_MAX_RECORD_BYTES,
      );
    } catch {
      warnings.add("diagnostic_segment_unreadable");
      continue;
    }
    for (const line of content.split(/\r?\n/gu)) {
      if (!line.trim()) {
        continue;
      }
      let raw: unknown;
      try {
        raw = JSON.parse(line);
      } catch {
        warnings.add("diagnostic_record_invalid");
        continue;
      }
      const normalizedTimeline = normalizeTimelineRecord(raw);
      if (normalizedTimeline !== null) {
        if (isWithinRange(normalizedTimeline.wall_time, cutoffMs)) {
          timeline.push(normalizedTimeline);
        }
        continue;
      }
      const normalizedResource = normalizeResourceRecord(raw);
      if (normalizedResource !== null) {
        if (isWithinRange(normalizedResource.wall_time, cutoffMs)) {
          resources.push(normalizedResource);
        }
        continue;
      }
      warnings.add("diagnostic_record_unknown_schema");
    }
  }

  return { timeline, resources, warnings: [...warnings].sort() };
}

function normalizeTimelineRecord(raw: unknown): NormalizedTimelineRecord | null {
  if (!isRecord(raw)) {
    return null;
  }
  const component = raw.component;
  const severity = raw.severity;
  if (
    raw.schema_version !== BETA_DIAGNOSTICS_SCHEMA_VERSION
    || raw.stream !== "timeline"
    || typeof raw.wall_time !== "string"
    || !isIsoTimestamp(raw.wall_time)
    || typeof raw.monotonic_ms !== "number"
    || !Number.isFinite(raw.monotonic_ms)
    || typeof component !== "string"
    || !COMPONENTS.has(component as BetaDiagnosticComponent)
    || typeof raw.session_id !== "string"
    || !SAFE_IDENTIFIER_PATTERN.test(raw.session_id)
    || typeof raw.event !== "string"
    || !SAFE_EVENT_PATTERN.test(raw.event)
    || typeof severity !== "string"
    || !SEVERITIES.has(severity)
    || (raw.details !== undefined && !isRecord(raw.details))
  ) {
    return null;
  }
  return {
    schema_version: BETA_DIAGNOSTICS_SCHEMA_VERSION,
    stream: "timeline",
    wall_time: raw.wall_time,
    monotonic_ms: raw.monotonic_ms,
    component: component as BetaDiagnosticComponent,
    session_id: raw.session_id,
    event: raw.event,
    severity: severity as NormalizedTimelineRecord["severity"],
    details: sanitizeTimelineDetails(raw.details as Record<string, unknown> | undefined),
  };
}

function normalizeResourceRecord(raw: unknown): NormalizedResourceRecord | null {
  if (!isRecord(raw)) {
    return null;
  }
  const component = raw.component;
  if (
    raw.schema_version !== BETA_DIAGNOSTICS_SCHEMA_VERSION
    || raw.stream !== "resource-samples"
    || typeof raw.wall_time !== "string"
    || !isIsoTimestamp(raw.wall_time)
    || typeof raw.monotonic_ms !== "number"
    || !Number.isFinite(raw.monotonic_ms)
    || typeof component !== "string"
    || !COMPONENTS.has(component as BetaDiagnosticComponent)
    || typeof raw.session_id !== "string"
    || !SAFE_IDENTIFIER_PATTERN.test(raw.session_id)
  ) {
    return null;
  }
  const normalized: NormalizedResourceRecord = {
    schema_version: BETA_DIAGNOSTICS_SCHEMA_VERSION,
    stream: "resource-samples",
    wall_time: raw.wall_time,
    monotonic_ms: raw.monotonic_ms,
    component: component as BetaDiagnosticComponent,
    session_id: raw.session_id,
  };
  for (const [key, value] of Object.entries(raw)) {
    if (RESOURCE_NUMBER_KEYS.has(key) && typeof value === "number" && Number.isFinite(value)) {
      normalized[key] = value;
    } else if (RESOURCE_BOOLEAN_KEYS.has(key) && typeof value === "boolean") {
      normalized[key] = value;
    }
  }
  return normalized;
}

async function readBackendSnapshot(
  provider: BuildBetaDiagnosticsBundleOptions["getBackendSnapshot"],
): Promise<{
  workloadSummary: object;
  databaseHealth: object;
  operationLogSummary: object;
  missingReason: string | null;
}> {
  if (provider === undefined) {
    return unavailableBackendSnapshot("backend_snapshot_not_configured");
  }
  try {
    const snapshot = await provider();
    const workloadSummary = sanitizeBackendMetrics(snapshot.workloadSummary);
    const databaseHealth = sanitizeBackendMetrics(snapshot.databaseHealth);
    const operationLogSummary = sanitizeBackendMetrics(snapshot.operationLogSummary);
    if (workloadSummary === null || databaseHealth === null || operationLogSummary === null) {
      return unavailableBackendSnapshot("backend_snapshot_invalid");
    }
    return {
      workloadSummary,
      databaseHealth,
      operationLogSummary,
      missingReason: null,
    };
  } catch {
    return unavailableBackendSnapshot("backend_api_unavailable");
  }
}

function unavailableBackendSnapshot(reason: string) {
  const unavailable = {
    schema_version: BETA_DIAGNOSTICS_SCHEMA_VERSION,
    available: false,
    reason,
  };
  return {
    workloadSummary: unavailable,
    databaseHealth: unavailable,
    operationLogSummary: unavailable,
    missingReason: reason,
  };
}

function sanitizeBackendMetrics(value: unknown, depth = 0): object | null {
  if (!isRecord(value) || depth > 5 || value.schema_version !== BETA_DIAGNOSTICS_SCHEMA_VERSION) {
    return null;
  }
  const sanitized: Record<string, unknown> = {};
  for (const [key, raw] of Object.entries(value)) {
    if (!BACKEND_METRIC_KEYS.has(key)) {
      continue;
    }
    const next = sanitizeBackendMetricValue(key, raw, depth + 1);
    if (next !== undefined) {
      sanitized[key] = next;
    }
  }
  return sanitized.schema_version === BETA_DIAGNOSTICS_SCHEMA_VERSION ? sanitized : null;
}

function sanitizeBackendMetricValue(
  key: string,
  value: unknown,
  depth: number,
): unknown {
  if (value === null || typeof value === "boolean") {
    return value;
  }
  if (typeof value === "number") {
    return Number.isFinite(value) && value >= 0 ? value : undefined;
  }
  if (typeof value === "string") {
    if (key === "generated_at") {
      return isIsoTimestamp(value) ? value : undefined;
    }
    if (key === "alembic_revision") {
      return /^[a-z0-9_]{1,96}$/u.test(value) ? value : undefined;
    }
    return SAFE_METRIC_ENUM_VALUES.has(value) ? value : undefined;
  }
  if (Array.isArray(value)) {
    return value.slice(0, 32)
      .map((item) => isRecord(item) ? sanitizeNestedBackendMetrics(item, depth) : undefined)
      .filter((item) => item !== undefined);
  }
  if (isRecord(value)) {
    return sanitizeNestedBackendMetrics(value, depth);
  }
  return undefined;
}

function sanitizeNestedBackendMetrics(
  value: Record<string, unknown>,
  depth: number,
): Record<string, unknown> | undefined {
  if (depth > 5) {
    return undefined;
  }
  const sanitized: Record<string, unknown> = {};
  for (const [key, raw] of Object.entries(value)) {
    if (!BACKEND_METRIC_KEYS.has(key)) {
      continue;
    }
    const next = sanitizeBackendMetricValue(key, raw, depth + 1);
    if (next !== undefined) {
      sanitized[key] = next;
    }
  }
  return sanitized;
}

async function readSourceLogSummaries(userDataPath: string): Promise<{
  startup: string;
  backendErrors: string;
  recordCount: number;
}> {
  const logsPath = path.join(userDataPath, "logs");
  const [startup, backendErrors] = await Promise.all([
    summarizeSourceLog(path.join(logsPath, "startup.log"), "startup"),
    summarizeSourceLog(path.join(logsPath, "backend-errors.log"), "backend_errors"),
  ]);
  return {
    startup: toJsonl(startup),
    backendErrors: toJsonl(backendErrors),
    recordCount: startup.length + backendErrors.length,
  };
}

async function summarizeSourceLog(
  filePath: string,
  source: "startup" | "backend_errors",
): Promise<object[]> {
  let content: string;
  try {
    content = await readRegularFileTail(filePath, MAX_SOURCE_LOG_BYTES);
  } catch {
    return [];
  }
  return content.split(/\r?\n/gu)
    .filter((line) => line.trim())
    .slice(-4_096)
    .map((line, index) => ({
      schema_version: BETA_DIAGNOSTICS_SCHEMA_VERSION,
      source,
      line: index + 1,
      ...classifyDiagnosticLogLine(line),
    }));
}

function classifyDiagnosticLogLine(line: string): Record<string, string> {
  const normalized = line.normalize("NFC");
  const timestamp = normalized.match(/\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})/u)?.[0];
  const phase = normalized.match(/\bphase=([a-z][a-z0-9_-]{0,63})\b/iu)?.[1]?.toLowerCase();
  const exceptionClass = normalized.match(/\b([A-Za-z][A-Za-z0-9_.]{0,80}(?:Error|Exception|Timeout))\b/u)?.[1];
  return {
    ...(timestamp && isIsoTimestamp(timestamp) ? { wall_time: timestamp } : {}),
    ...(phase ? { phase } : {}),
    ...(exceptionClass ? { exception_class: exceptionClass } : {}),
    category: classifyDiagnosticCategory(normalized),
  };
}

function classifyDiagnosticCategory(value: string): string {
  const normalized = value.toLowerCase();
  if (/(database is locked|sqlite_busy|\bbusy\b)/u.test(normalized)) {
    return "sqlite_busy";
  }
  if (/(database disk image is malformed|integrity|foreign key)/u.test(normalized)) {
    return "database_integrity";
  }
  if (/(no space left|disk full|enospc)/u.test(normalized)) {
    return "disk_full";
  }
  if (/(permission denied|read-only|eacces|eperm)/u.test(normalized)) {
    return "permission_error";
  }
  if (/(timeout|timed out)/u.test(normalized)) {
    return "timeout";
  }
  if (/(traceback|exception|\berror\b)/u.test(normalized)) {
    return "error";
  }
  if (/\[startup\]|启动|starting|ready/u.test(normalized)) {
    return "lifecycle";
  }
  return "omitted_unstructured";
}

function buildBundleSummary(input: {
  timeline: NormalizedTimelineRecord[];
  resources: NormalizedResourceRecord[];
  partial: boolean;
  missingSections: string[];
  exportedAt: string;
}) {
  const eventCounts: Record<string, number> = {};
  const componentCounts: Record<string, number> = {};
  for (const record of input.timeline) {
    eventCounts[record.event] = (eventCounts[record.event] ?? 0) + 1;
    componentCounts[record.component] = (componentCounts[record.component] ?? 0) + 1;
  }
  return {
    schema_version: BETA_DIAGNOSTICS_SCHEMA_VERSION,
    generated_at: input.exportedAt,
    partial: input.partial,
    missing_sections: input.missingSections,
    timeline_records: input.timeline.length,
    resource_samples: input.resources.length,
    component_event_counts: sortNumericRecord(componentCounts),
    lifecycle_event_counts: sortNumericRecord(eventCounts),
    resource_peaks: {
      cpu_percent: maximumMetric(input.resources, "cpu_percent"),
      rss_bytes: maximumMetric(input.resources, "rss_bytes"),
      handles_or_fds: maximumMetric(input.resources, "handles_or_fds"),
      playwright_processes: maximumMetric(input.resources, "playwright_processes"),
      wal_bytes: maximumMetric(input.resources, "wal_bytes"),
    },
  };
}

function buildReadme(input: { partial: boolean; missingSections: string[] }): string {
  return [
    "Auto Email Sender Beta 本地诊断包",
    "",
    "此文件仅在用户主动导出后离开本机；软件不会自动上传诊断数据。",
    "包内不包含数据库副本、邮件正文、附件、导师资料、LLM prompt/response 或凭据。",
    "自由文本日志已转换为结构化分类；未知文本不会原样导出。",
    "checksums.sha256 校验除其自身以外的每个文件。",
    `导出状态：${input.partial ? "partial（部分信息不可用）" : "complete"}`,
    ...(input.missingSections.length > 0
      ? [`缺失项：${input.missingSections.join(", ")}`]
      : []),
    "",
  ].join("\n");
}

async function readBoundedRegularFile(filePath: string, maxBytes: number): Promise<string> {
  const handle = await openNoFollow(filePath);
  try {
    const fileStat = await handle.stat();
    if (!fileStat.isFile() || fileStat.size > maxBytes) {
      throw new Error("Diagnostic segment is not a bounded regular file.");
    }
    return handle.readFile("utf8");
  } finally {
    await handle.close();
  }
}

async function readRegularFileTail(filePath: string, maxBytes: number): Promise<string> {
  const fileStat = await lstat(filePath);
  if (!fileStat.isFile() || fileStat.isSymbolicLink()) {
    throw new Error("Diagnostic source log is not a regular file.");
  }
  const handle = await openNoFollow(filePath);
  try {
    const openedStat = await handle.stat();
    if (!openedStat.isFile()) {
      throw new Error("Diagnostic source log changed during export.");
    }
    const bytesToRead = Math.min(openedStat.size, maxBytes);
    const buffer = Buffer.alloc(bytesToRead);
    await handle.read(buffer, 0, bytesToRead, Math.max(0, openedStat.size - bytesToRead));
    return buffer.toString("utf8");
  } finally {
    await handle.close();
  }
}

function openNoFollow(filePath: string) {
  const noFollow = typeof fsConstants.O_NOFOLLOW === "number" ? fsConstants.O_NOFOLLOW : 0;
  return open(filePath, fsConstants.O_RDONLY | noFollow);
}

function addJsonEntry(entries: Map<string, Buffer>, name: string, value: unknown): void {
  addTextEntry(entries, name, `${JSON.stringify(value, null, 2)}\n`);
}

function addTextEntry(entries: Map<string, Buffer>, name: string, value: string): void {
  assertSafeZipEntryName(name);
  entries.set(name, Buffer.from(value, "utf8"));
}

function assertSafeZipEntryName(name: string): void {
  if (
    !name
    || path.posix.isAbsolute(name)
    || name.includes("\\")
    || name.split("/").some((part) => part === "" || part === "." || part === "..")
  ) {
    throw new Error("Unsafe Beta diagnostic ZIP entry name.");
  }
}

function assertBundleEntryBudget(entries: Map<string, Buffer>): void {
  const totalBytes = [...entries.values()]
    .reduce((total, value) => total + value.byteLength, 0);
  if (totalBytes > MAX_BUNDLE_ENTRIES_BYTES) {
    throw new Error("Beta diagnostic bundle exceeds its bounded export size.");
  }
}

function sanitizeBuildIdentityValue(
  value: string | null | undefined,
  kind: "branch" | "sha" | "run" | "asset" | "sha256",
): string | null {
  const normalized = value?.trim();
  if (!normalized) {
    return null;
  }
  if (kind === "sha") {
    return /^[0-9a-f]{40}$/u.test(normalized) ? normalized : null;
  }
  if (kind === "sha256") {
    return /^[0-9a-f]{64}$/u.test(normalized) ? normalized : null;
  }
  if (kind === "run") {
    return /^\d{1,20}$/u.test(normalized) ? normalized : null;
  }
  if (kind === "asset") {
    return path.basename(normalized) === normalized && /^[A-Za-z0-9_.+()-]{1,180}$/u.test(normalized)
      ? normalized
      : null;
  }
  return /^[A-Za-z0-9._/-]{1,160}$/u.test(normalized)
    && !normalized.split("/").some((part) => part === "." || part === "..")
    ? normalized
    : null;
}

function inferReleaseChannel(version: string): "stable" | "alpha" | "beta" | "rc" | "unknown" {
  const match = /-(alpha|beta|rc)(?:[.-]|$)/iu.exec(version.trim());
  if (match) {
    return match[1].toLowerCase() as "alpha" | "beta" | "rc";
  }
  return /^\d+\.\d+\.\d+$/u.test(version.trim()) ? "stable" : "unknown";
}

function sanitizeSystemRelease(value: string): string {
  return /^[A-Za-z0-9_.+-]{1,80}$/u.test(value) ? value : "unknown";
}

function getRangeCutoffMs(range: DesktopBetaDiagnosticsRange, now: Date): number | null {
  const duration = range === "1h"
    ? 60 * 60 * 1000
    : range === "24h"
      ? 24 * 60 * 60 * 1000
      : range === "7d"
        ? 7 * 24 * 60 * 60 * 1000
        : null;
  return duration === null ? null : now.getTime() - duration;
}

function isWithinRange(timestamp: string, cutoffMs: number | null): boolean {
  return cutoffMs === null || Date.parse(timestamp) >= cutoffMs;
}

function isIsoTimestamp(value: string): boolean {
  return ISO_TIMESTAMP_PATTERN.test(value) && Number.isFinite(Date.parse(value));
}

function compareDiagnosticRecords(
  left: { wall_time: string; monotonic_ms: number; component: string },
  right: { wall_time: string; monotonic_ms: number; component: string },
): number {
  return Date.parse(left.wall_time) - Date.parse(right.wall_time)
    || left.component.localeCompare(right.component)
    || left.monotonic_ms - right.monotonic_ms;
}

function toJsonl(values: readonly unknown[]): string {
  return values.length === 0
    ? ""
    : `${values.map((value) => JSON.stringify(value)).join("\n")}\n`;
}

function sha256(value: Buffer): string {
  return createHash("sha256").update(value).digest("hex");
}

function maximumMetric(
  records: NormalizedResourceRecord[],
  key: string,
): number | null {
  const values = records
    .map((record) => record[key])
    .filter((value): value is number => typeof value === "number" && Number.isFinite(value));
  return values.length === 0 ? null : Math.max(...values);
}

function sortNumericRecord(value: Record<string, number>): Record<string, number> {
  return Object.fromEntries(Object.entries(value).sort(([left], [right]) => left.localeCompare(right)));
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}
