import { dialog, type SaveDialogOptions } from "electron";
import path from "node:path";

import type {
  DesktopBackendModeStatus,
  DesktopBetaDiagnosticsExportResult,
  DesktopBetaDiagnosticsProblemCategory,
} from "../../../../contracts/desktop-ipc.js";
import type { DesktopBackendClient } from "../backend/client.js";
import {
  buildBetaDiagnosticsBundle,
  getBetaDiagnosticsExportFileName,
  type DesktopBetaBuildIdentity,
  type DesktopBetaDiagnosticsRange,
  writeBetaDiagnosticsZip,
} from "./exporter.js";
import {
  DesktopBetaDiagnosticsRecorder,
  type DesktopBetaDiagnosticsStatus,
} from "./recorder.js";

const MAX_BACKEND_SNAPSHOT_BYTES = 1024 * 1024;
const BACKEND_SNAPSHOT_TIMEOUT_MS = 15_000;

type BetaDiagnosticsServiceDependencies = {
  showSaveDialog: (
    options: SaveDialogOptions,
  ) => Promise<{ canceled: boolean; filePath?: string }>;
  writeZip: typeof writeBetaDiagnosticsZip;
  now: () => Date;
};

export type DesktopBetaDiagnosticsServiceOptions = {
  recorder: DesktopBetaDiagnosticsRecorder;
  appVersion: string;
  backendClient: DesktopBackendClient;
  getBackendModeStatus: () => Promise<DesktopBackendModeStatus>;
  buildIdentity?: DesktopBetaBuildIdentity;
  dependencies?: Partial<BetaDiagnosticsServiceDependencies>;
};

export type DesktopBetaDiagnosticsService = {
  getStatus: () => Promise<DesktopBetaDiagnosticsStatus>;
  clear: () => Promise<DesktopBetaDiagnosticsStatus>;
  markProblem: (input: {
    category: DesktopBetaDiagnosticsProblemCategory;
    note?: string;
  }) => Promise<{ markedAt: string }>;
  exportBundle: (
    range: DesktopBetaDiagnosticsRange,
  ) => Promise<DesktopBetaDiagnosticsExportResult>;
};

export function createDesktopBetaDiagnosticsService(
  options: DesktopBetaDiagnosticsServiceOptions,
): DesktopBetaDiagnosticsService {
  const dependencies: BetaDiagnosticsServiceDependencies = {
    showSaveDialog: (dialogOptions) => dialog.showSaveDialog(dialogOptions),
    writeZip: writeBetaDiagnosticsZip,
    now: () => new Date(),
    ...options.dependencies,
  };
  let operationQueue: Promise<void> = Promise.resolve();
  const runExclusive = <Result>(operation: () => Promise<Result>): Promise<Result> => {
    const pending = operationQueue.then(operation);
    operationQueue = pending.then(() => undefined, () => undefined);
    return pending;
  };

  return {
    getStatus: () => options.recorder.getStatus(),
    clear: () => runExclusive(() => options.recorder.clear()),
    markProblem: (input) => runExclusive(async () => {
      await requireBetaDiagnosticsEnabled(options.recorder);
      return {
        markedAt: await options.recorder.recordProblemMarker(input),
      };
    }),
    exportBundle: (range) => runExclusive(async () => {
      await requireBetaDiagnosticsEnabled(options.recorder);
      const exportStartedAt = dependencies.now();
      const dialogResult = await dependencies.showSaveDialog({
        title: "导出 Beta 诊断包",
        defaultPath: getBetaDiagnosticsExportFileName(exportStartedAt),
        buttonLabel: "保存诊断包",
        filters: [{ name: "ZIP 诊断包", extensions: ["zip"] }],
      });
      if (dialogResult.canceled || !dialogResult.filePath) {
        await options.recorder.recordTimeline("diagnostics_export_canceled", {
          source: range,
        });
        return { status: "canceled" };
      }

      const modeStatus = await options.getBackendModeStatus();
      await options.recorder.recordTimeline("diagnostics_export_started", {
        source: range,
        requested_mode: modeStatus.configuredMode ?? modeStatus.nextMode,
        effective_mode: modeStatus.currentMode,
      });
      const bundle = await buildBetaDiagnosticsBundle({
        rootPath: options.recorder.rootPath,
        userDataPath: path.dirname(options.recorder.rootPath),
        appVersion: options.appVersion,
        requestedMode: modeStatus.configuredMode ?? modeStatus.nextMode,
        effectiveMode: modeStatus.currentMode,
        range,
        buildIdentity: options.buildIdentity,
        flush: () => options.recorder.flush(),
        getBackendSnapshot: () => fetchDesktopBetaBackendSnapshot(options.backendClient),
        now: () => exportStartedAt,
      });
      const outputPath = dialogResult.filePath.toLowerCase().endsWith(".zip")
        ? dialogResult.filePath
        : `${dialogResult.filePath}.zip`;
      await dependencies.writeZip(outputPath, bundle);
      await options.recorder.recordTimeline("diagnostics_export_saved", {
        source: range,
        state: bundle.partial ? "partial" : "complete",
      });
      return {
        status: "saved",
        fileName: path.basename(outputPath),
        reportId: bundle.reportId,
        partial: bundle.partial,
        missingSections: bundle.missingSections,
      };
    }),
  };
}

async function requireBetaDiagnosticsEnabled(
  recorder: DesktopBetaDiagnosticsRecorder,
): Promise<void> {
  if (!(await recorder.getStatus()).enabled) {
    throw new Error("Beta 本地诊断仅在测试版本中启用。");
  }
}

export async function fetchDesktopBetaBackendSnapshot(
  backendClient: DesktopBackendClient,
): Promise<{
  workloadSummary: unknown;
  databaseHealth: unknown;
  operationLogSummary: unknown;
}> {
  const response = await backendClient.request("/api/diagnostics/beta-summary", {
    signal: AbortSignal.timeout(BACKEND_SNAPSHOT_TIMEOUT_MS),
  });
  if (!response.ok) {
    throw new Error(`Beta diagnostics snapshot failed with HTTP ${response.status}.`);
  }
  const declaredLength = Number(response.headers.get("content-length") ?? "0");
  if (Number.isFinite(declaredLength) && declaredLength > MAX_BACKEND_SNAPSHOT_BYTES) {
    throw new Error("Beta diagnostics snapshot exceeded its response size limit.");
  }
  const responseText = await response.text();
  if (Buffer.byteLength(responseText, "utf8") > MAX_BACKEND_SNAPSHOT_BYTES) {
    throw new Error("Beta diagnostics snapshot exceeded its response size limit.");
  }
  const payload = JSON.parse(responseText) as Record<string, unknown>;
  return {
    workloadSummary: payload.workload_summary,
    databaseHealth: payload.database_health,
    operationLogSummary: payload.operation_log_summary,
  };
}
