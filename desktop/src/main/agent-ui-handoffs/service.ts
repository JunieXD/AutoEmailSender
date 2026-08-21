import type {
  DesktopAgentUiHandoff,
  DesktopAgentUiHandoffAcknowledgeRequest,
  DesktopAgentUiHandoffState,
  DesktopAgentUiHandoffStatus,
  DesktopAgentUiHandoffSurface,
} from "../../../../contracts/desktop-ipc.js";
import { DESKTOP_IPC_CHANNELS } from "../../contracts/channels.js";
import type { DesktopBackendClient } from "../backend/client.js";


const DEFAULT_POLL_INTERVAL_MS = 1_000;
const LEASE_RETRY_GRACE_MS = 100;

const HANDOFF_STATUSES = new Set<DesktopAgentUiHandoffStatus>([
  "pending",
  "claimed",
  "awaiting_user",
  "applied",
  "failed",
  "canceled",
  "expired",
]);
const HANDOFF_SURFACES = new Set<DesktopAgentUiHandoffSurface>([
  "professors.management",
  "professors.home",
  "tasks.center",
  "crawler.job",
  "communications.thread",
  "draft.workspace",
]);

type AgentUiHandoffRenderer = {
  isDestroyed: () => boolean;
  webContents: {
    isDestroyed: () => boolean;
    send: (channel: string, handoff: DesktopAgentUiHandoff) => void;
  };
};

export type AgentUiHandoffService = {
  start: () => void;
  stop: () => void;
  setRendererReady: (ready: boolean) => void;
  pollNow: () => void;
  acknowledge: (
    request: DesktopAgentUiHandoffAcknowledgeRequest,
  ) => Promise<DesktopAgentUiHandoffState>;
};

export type AgentUiHandoffServiceOptions = {
  backendClient: DesktopBackendClient;
  consumerId: string;
  getRenderer: () => AgentUiHandoffRenderer | null;
  showWindow: () => void;
  pollIntervalMs?: number;
  dependencies?: {
    now?: () => number;
    setTimeout?: typeof setTimeout;
    clearTimeout?: typeof clearTimeout;
    warn?: (message: string, error?: unknown) => void;
  };
};

export function createAgentUiHandoffService(
  options: AgentUiHandoffServiceOptions,
): AgentUiHandoffService {
  if (!/^[A-Za-z0-9._:-]{1,120}$/.test(options.consumerId)) {
    throw new Error("Agent UI handoff consumer ID is invalid.");
  }
  const now = options.dependencies?.now ?? Date.now;
  const scheduleTimeout = options.dependencies?.setTimeout ?? setTimeout;
  const cancelTimeout = options.dependencies?.clearTimeout ?? clearTimeout;
  const warn = options.dependencies?.warn ?? (() => undefined);
  const pollIntervalMs = Math.max(100, options.pollIntervalMs ?? DEFAULT_POLL_INTERVAL_MS);

  let running = false;
  let rendererReady = false;
  let timer: ReturnType<typeof setTimeout> | null = null;
  let requestInFlight = false;
  let activeClaim: DesktopAgentUiHandoff | null = null;
  let activeClaimDelivered = false;

  const clearTimer = (): void => {
    if (timer !== null) {
      cancelTimeout(timer);
      timer = null;
    }
  };

  const schedule = (delayMs: number): void => {
    if (!running) {
      return;
    }
    clearTimer();
    timer = scheduleTimeout(() => {
      timer = null;
      void poll().catch((error: unknown) => {
        // poll handles expected backend and parsing failures internally.  This
        // final guard prevents an implementation regression from becoming an
        // unhandled rejection in Electron's main process.
        warn("Agent UI handoff polling failed unexpectedly.", error);
        schedule(pollIntervalMs);
      });
    }, Math.max(0, delayMs));
  };

  const deliverActiveClaim = (): boolean => {
    if (!running || !rendererReady || activeClaim === null) {
      return false;
    }
    const renderer = options.getRenderer();
    if (
      renderer === null
      || renderer.isDestroyed()
      || renderer.webContents.isDestroyed()
    ) {
      return false;
    }
    options.showWindow();
    renderer.webContents.send(
      DESKTOP_IPC_CHANNELS.agentUiHandoffDeliver,
      activeClaim,
    );
    activeClaimDelivered = true;
    return true;
  };

  const poll = async (): Promise<void> => {
    if (!running || !rendererReady || requestInFlight) {
      if (running && rendererReady) {
        schedule(pollIntervalMs);
      }
      return;
    }
    const renderer = options.getRenderer();
    if (
      renderer === null
      || renderer.isDestroyed()
      || renderer.webContents.isDestroyed()
    ) {
      schedule(pollIntervalMs);
      return;
    }

    if (activeClaim !== null) {
      const leaseExpiresAt = Date.parse(activeClaim.claimExpiresAt);
      if (Number.isFinite(leaseExpiresAt) && leaseExpiresAt > now()) {
        if (!activeClaimDelivered) {
          deliverActiveClaim();
        }
        const leaseDelay = Math.max(
          100,
          leaseExpiresAt - now() + LEASE_RETRY_GRACE_MS,
        );
        schedule(
          activeClaimDelivered
            ? leaseDelay
            : Math.min(pollIntervalMs, leaseDelay),
        );
        return;
      }
      activeClaim = null;
      activeClaimDelivered = false;
    }

    requestInFlight = true;
    try {
      const response = await options.backendClient.request(
        "/api/agent/v1/ui-handoffs/claim-next",
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ consumer_id: options.consumerId }),
        },
      );
      if (!running) {
        return;
      }
      if (response.status === 204) {
        if (rendererReady) {
          schedule(pollIntervalMs);
        }
        return;
      }
      if (!response.ok) {
        throw new Error(await backendErrorMessage(response, "领取界面交接失败"));
      }
      const handoff = parseClaimedUiHandoff(await response.json());
      if (handoff.consumerId !== options.consumerId) {
        throw new Error("界面交接 consumer_id 与当前桌面窗口不匹配");
      }
      activeClaim = handoff;
      activeClaimDelivered = false;
      deliverActiveClaim();
      const leaseExpiresAt = Date.parse(handoff.claimExpiresAt);
      if (rendererReady) {
        const leaseDelay = Number.isFinite(leaseExpiresAt)
          ? Math.max(100, leaseExpiresAt - now() + LEASE_RETRY_GRACE_MS)
          : pollIntervalMs;
        schedule(
          activeClaimDelivered
            ? leaseDelay
            : Math.min(pollIntervalMs, leaseDelay),
        );
      }
    } catch (error) {
      warn("Agent UI handoff backend is temporarily unavailable.", error);
      schedule(pollIntervalMs);
    } finally {
      requestInFlight = false;
    }
  };

  return {
    start(): void {
      if (running) {
        return;
      }
      running = true;
      schedule(0);
    },
    stop(): void {
      running = false;
      rendererReady = false;
      clearTimer();
      activeClaim = null;
      activeClaimDelivered = false;
    },
    setRendererReady(ready: boolean): void {
      rendererReady = ready;
      if (!running) {
        return;
      }
      if (ready) {
        schedule(0);
      } else {
        // A new preload/renderer instance must receive the active claim even
        // when it was already delivered to the instance that is unloading.
        activeClaimDelivered = false;
        clearTimer();
      }
    },
    pollNow(): void {
      if (running) {
        schedule(0);
      }
    },
    async acknowledge(
      request: DesktopAgentUiHandoffAcknowledgeRequest,
    ): Promise<DesktopAgentUiHandoffState> {
      const response = await options.backendClient.request(
        `/api/agent/v1/ui-handoffs/${encodeURIComponent(request.handoffId)}/acknowledge`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            consumer_id: options.consumerId,
            status: request.status,
            result: request.result ?? {},
            ...(request.failureMessage === undefined
              ? {}
              : { failure_message: request.failureMessage }),
          }),
        },
      );
      if (response.status === 409) {
        const currentResponse = await options.backendClient.request(
          `/api/agent/v1/ui-handoffs/${encodeURIComponent(request.handoffId)}`,
        );
        if (currentResponse.ok) {
          const currentState = parseUiHandoffState(await currentResponse.json());
          if (currentState.handoffId !== request.handoffId) {
            throw new Error("界面交接冲突状态与回执 ID 不匹配");
          }
          if (activeClaim?.handoffId === request.handoffId) {
            activeClaim = null;
            activeClaimDelivered = false;
          }
          schedule(0);
          return currentState;
        }
      }
      if (!response.ok) {
        throw new Error(await backendErrorMessage(response, "确认界面交接失败"));
      }
      const state = parseUiHandoffState(await response.json());
      if (activeClaim?.handoffId === request.handoffId) {
        activeClaim = null;
        activeClaimDelivered = false;
      }
      schedule(0);
      return state;
    },
  };
}

export function parseClaimedUiHandoff(value: unknown): DesktopAgentUiHandoff {
  const source = requireRecord(value, "界面交接响应不是对象");
  const state = parseUiHandoffState(source);
  const consumerId = requireString(source.consumer_id, "consumer_id");
  const claimExpiresAt = requireDateString(source.claim_expires_at, "claim_expires_at");
  const payload = requireRecord(source.payload, "payload");
  const selectedIds = requireIntegerArray(source.selected_ids, "selected_ids");
  return {
    ...state,
    consumerId,
    claimExpiresAt,
    payload,
    selectedIds,
  };
}

function parseUiHandoffState(value: unknown): DesktopAgentUiHandoffState {
  const source = requireRecord(value, "界面交接响应不是对象");
  const status = requireString(source.status, "status");
  const surface = requireString(source.surface, "surface");
  if (!HANDOFF_STATUSES.has(status as DesktopAgentUiHandoffStatus)) {
    throw new Error("界面交接 status 无效");
  }
  if (!HANDOFF_SURFACES.has(surface as DesktopAgentUiHandoffSurface)) {
    throw new Error("界面交接 surface 无效");
  }
  return {
    handoffId: requireString(source.handoff_id, "handoff_id"),
    schemaVersion: requireNonNegativeInteger(source.schema_version, "schema_version"),
    surface: surface as DesktopAgentUiHandoffSurface,
    route: requireString(source.route, "route"),
    status: status as DesktopAgentUiHandoffStatus,
    selectionCount: requireNonNegativeInteger(source.selection_count, "selection_count"),
    selectionFingerprint: optionalString(source.selection_fingerprint, "selection_fingerprint"),
    uiEffects: requireStringArray(source.ui_effects, "ui_effects"),
    result: optionalRecord(source.result, "result"),
    failureMessage: optionalString(source.failure_message, "failure_message"),
    deliveryAttempts: requireNonNegativeInteger(source.delivery_attempts, "delivery_attempts"),
    expiresAt: requireDateString(source.expires_at, "expires_at"),
    claimedAt: optionalDateString(source.claimed_at, "claimed_at"),
    awaitingUserAt: optionalDateString(source.awaiting_user_at, "awaiting_user_at"),
    appliedAt: optionalDateString(source.applied_at, "applied_at"),
    failedAt: optionalDateString(source.failed_at, "failed_at"),
    canceledAt: optionalDateString(source.canceled_at, "canceled_at"),
    createdAt: requireDateString(source.created_at, "created_at"),
    updatedAt: requireDateString(source.updated_at, "updated_at"),
    availableActions: requireStringArray(source.available_actions, "available_actions"),
  };
}

async function backendErrorMessage(response: Response, fallback: string): Promise<string> {
  try {
    const body = await response.json() as unknown;
    const source = requireRecord(body, "error response");
    const error = source.error;
    if (error !== null && typeof error === "object" && !Array.isArray(error)) {
      const message = (error as Record<string, unknown>).message;
      if (typeof message === "string" && message.trim()) {
        return message.trim();
      }
    }
  } catch {
    // Fall through to the bounded status message.
  }
  return `${fallback}（HTTP ${response.status}）`;
}

function requireRecord(value: unknown, field: string): Record<string, unknown> {
  if (value === null || typeof value !== "object" || Array.isArray(value)) {
    throw new Error(`界面交接字段 ${field} 无效`);
  }
  return value as Record<string, unknown>;
}

function optionalRecord(value: unknown, field: string): Record<string, unknown> | null {
  return value === null || value === undefined ? null : requireRecord(value, field);
}

function requireString(value: unknown, field: string): string {
  if (typeof value !== "string" || !value.trim()) {
    throw new Error(`界面交接字段 ${field} 无效`);
  }
  return value;
}

function optionalString(value: unknown, field: string): string | null {
  if (value === null || value === undefined) {
    return null;
  }
  return requireString(value, field);
}

function requireDateString(value: unknown, field: string): string {
  const result = requireString(value, field);
  if (!Number.isFinite(Date.parse(result))) {
    throw new Error(`界面交接字段 ${field} 不是有效时间`);
  }
  return result;
}

function optionalDateString(value: unknown, field: string): string | null {
  return value === null || value === undefined ? null : requireDateString(value, field);
}

function requireNonNegativeInteger(value: unknown, field: string): number {
  if (!Number.isInteger(value) || typeof value !== "number" || value < 0) {
    throw new Error(`界面交接字段 ${field} 无效`);
  }
  return value;
}

function requireIntegerArray(value: unknown, field: string): number[] {
  if (
    !Array.isArray(value)
    || value.some((item) => !Number.isInteger(item) || item < 1)
  ) {
    throw new Error(`界面交接字段 ${field} 无效`);
  }
  return value as number[];
}

function requireStringArray(value: unknown, field: string): string[] {
  if (!Array.isArray(value) || value.some((item) => typeof item !== "string")) {
    throw new Error(`界面交接字段 ${field} 无效`);
  }
  return value as string[];
}
