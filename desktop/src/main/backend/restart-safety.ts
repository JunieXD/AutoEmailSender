import type {
  DesktopBackendModeRestartOptions,
  DesktopBackendModeRestartResult,
  DesktopRestartSafety,
} from "../../../../contracts/desktop-ipc.js";
import type { DesktopBackendClient } from "./client.js";

type RestartSafetyPayload = {
  safe_to_restart: boolean;
  confirmation_required: boolean;
  active_work_count: number;
  sending_count: number;
  work_counts: {
    draft_generation: number;
    match_analysis: number;
    crawler: number;
    imap_sync: number;
  };
  message: string;
};

export async function getBackendRestartSafety(
  client: DesktopBackendClient,
): Promise<DesktopRestartSafety> {
  const response = await client.request("/api/desktop/restart-safety");
  if (!response.ok) {
    throw new Error(`无法确认安全重启状态（${response.status}）。`);
  }
  const payload: unknown = await response.json();
  if (!isRestartSafetyPayload(payload)) {
    throw new Error("本地系统服务返回了无效的重启安全状态。");
  }
  return {
    safeToRestart: payload.safe_to_restart,
    confirmationRequired: payload.confirmation_required,
    activeWorkCount: payload.active_work_count,
    sendingCount: payload.sending_count,
    workCounts: {
      draftGeneration: payload.work_counts.draft_generation,
      matchAnalysis: payload.work_counts.match_analysis,
      crawler: payload.work_counts.crawler,
      imapSync: payload.work_counts.imap_sync,
    },
    message: payload.message,
  };
}

export function createUnavailableRestartSafety(message: string): DesktopRestartSafety {
  return {
    safeToRestart: false,
    confirmationRequired: false,
    activeWorkCount: 0,
    sendingCount: 0,
    workCounts: {
      draftGeneration: 0,
      matchAnalysis: 0,
      crawler: 0,
      imapSync: 0,
    },
    message,
  };
}

export function createIdleRestartSafety(message: string): DesktopRestartSafety {
  return {
    safeToRestart: true,
    confirmationRequired: false,
    activeWorkCount: 0,
    sendingCount: 0,
    workCounts: {
      draftGeneration: 0,
      matchAnalysis: 0,
      crawler: 0,
      imapSync: 0,
    },
    message,
  };
}

export function decideBackendModeRestart(
  safety: DesktopRestartSafety,
  options: DesktopBackendModeRestartOptions = {},
): DesktopBackendModeRestartResult {
  if (!safety.safeToRestart) {
    return { state: "blocked", safety };
  }
  if (safety.confirmationRequired && !options.confirmActiveWork) {
    return { state: "confirmation_required", safety };
  }
  return { state: "restarting", safety };
}

function isRestartSafetyPayload(value: unknown): value is RestartSafetyPayload {
  if (value === null || typeof value !== "object" || Array.isArray(value)) {
    return false;
  }
  const candidate = value as Record<string, unknown>;
  const workCounts = candidate.work_counts;
  return typeof candidate.safe_to_restart === "boolean"
    && typeof candidate.confirmation_required === "boolean"
    && isNonNegativeInteger(candidate.active_work_count)
    && isNonNegativeInteger(candidate.sending_count)
    && typeof candidate.message === "string"
    && workCounts !== null
    && typeof workCounts === "object"
    && !Array.isArray(workCounts)
    && isNonNegativeInteger((workCounts as Record<string, unknown>).draft_generation)
    && isNonNegativeInteger((workCounts as Record<string, unknown>).match_analysis)
    && isNonNegativeInteger((workCounts as Record<string, unknown>).crawler)
    && isNonNegativeInteger((workCounts as Record<string, unknown>).imap_sync);
}

function isNonNegativeInteger(value: unknown): value is number {
  return typeof value === "number" && Number.isInteger(value) && value >= 0;
}
