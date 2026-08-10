import { readFile } from "node:fs/promises";
import path from "node:path";

export const WORKER_STATUS_PROTOCOL_VERSION = "2";
export const WORKER_HEARTBEAT_TIMEOUT_MS = 15_000;

export type WorkerSubsystemStatus = {
  last_started_at: string | null;
  last_succeeded_at: string | null;
  last_failed_at: string | null;
  consecutive_failures: number;
  error: string | null;
};

export type WorkerRuntimeStatus = {
  protocol_version: string;
  runtime_id: string;
  role: "worker";
  pid: number;
  generation: string;
  state: "starting" | "ready" | "stopping" | "error";
  started_at: string;
  updated_at: string;
  heartbeat_at: string;
  health: "healthy" | "degraded";
  draining: boolean;
  error: string | null;
  subsystems: Record<string, WorkerSubsystemStatus>;
};

export function getWorkerStatusPath(userDataPath: string): string {
  return path.join(userDataPath, "runtime", "worker.json");
}

export async function readWorkerRuntimeStatus(
  userDataPath: string,
): Promise<WorkerRuntimeStatus | null> {
  let value: unknown;
  try {
    value = JSON.parse(await readFile(getWorkerStatusPath(userDataPath), "utf8"));
  } catch {
    return null;
  }
  return isWorkerRuntimeStatus(value) ? value : null;
}

export function isWorkerRuntimeStatus(value: unknown): value is WorkerRuntimeStatus {
  if (!isRecord(value)) {
    return false;
  }
  return (
    value.protocol_version === WORKER_STATUS_PROTOCOL_VERSION
    && value.role === "worker"
    && typeof value.runtime_id === "string"
    && Number.isSafeInteger(value.pid)
    && Number(value.pid) > 0
    && typeof value.generation === "string"
    && ["starting", "ready", "stopping", "error"].includes(String(value.state))
    && typeof value.started_at === "string"
    && typeof value.updated_at === "string"
    && typeof value.heartbeat_at === "string"
    && ["healthy", "degraded"].includes(String(value.health))
    && typeof value.draining === "boolean"
    && (value.error === null || typeof value.error === "string")
    && isRecord(value.subsystems)
    && Object.values(value.subsystems).every(isWorkerSubsystemStatus)
  );
}

function isWorkerSubsystemStatus(value: unknown): value is WorkerSubsystemStatus {
  if (!isRecord(value)) {
    return false;
  }
  return (
    (value.last_started_at === null || typeof value.last_started_at === "string")
    && (value.last_succeeded_at === null || typeof value.last_succeeded_at === "string")
    && (value.last_failed_at === null || typeof value.last_failed_at === "string")
    && Number.isSafeInteger(value.consecutive_failures)
    && Number(value.consecutive_failures) >= 0
    && (value.error === null || typeof value.error === "string")
  );
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}
