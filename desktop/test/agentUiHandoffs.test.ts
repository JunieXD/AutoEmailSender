import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { DesktopBackendClient } from "../src/main/backend/client.js";
import {
  createAgentUiHandoffService,
  parseClaimedUiHandoff,
} from "../src/main/agent-ui-handoffs/service.js";
import { DESKTOP_IPC_CHANNELS } from "../src/contracts/channels.js";


function claimedHandoff(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    handoff_id: "uih_test",
    schema_version: 1,
    surface: "professors.management",
    route: "/professors",
    status: "claimed",
    selection_count: 2,
    selection_fingerprint: "selection-hash",
    ui_effects: ["focus_window", "navigate", "replace_selection"],
    result: null,
    failure_message: null,
    delivery_attempts: 1,
    expires_at: "2026-08-10T12:30:00.000Z",
    claimed_at: "2026-08-10T12:00:00.000Z",
    awaiting_user_at: null,
    applied_at: null,
    failed_at: null,
    canceled_at: null,
    created_at: "2026-08-10T12:00:00.000Z",
    updated_at: "2026-08-10T12:00:00.000Z",
    available_actions: ["read", "wait", "cancel"],
    consumer_id: "desktop:test",
    claim_expires_at: new Date(Date.now() + 30_000).toISOString(),
    payload: {
      kind: "professor_selection",
      selection_mode: "replace",
      display: "selected_only",
    },
    selected_ids: [7, 9],
    ...overrides,
  };
}

function publicHandoff(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  const value = claimedHandoff(overrides);
  delete value.consumer_id;
  delete value.claim_expires_at;
  delete value.payload;
  delete value.selected_ids;
  return value;
}

function jsonResponse(body: object, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

describe("Agent UI handoff desktop delivery", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-08-10T12:00:00.000Z"));
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("waits for a ready renderer, claims once, restores the window, and delivers", async () => {
    const request = vi.fn<DesktopBackendClient["request"]>()
      .mockResolvedValue(jsonResponse(claimedHandoff()));
    const send = vi.fn();
    const showWindow = vi.fn();
    const service = createAgentUiHandoffService({
      backendClient: { request },
      consumerId: "desktop:test",
      getRenderer: () => ({
        isDestroyed: () => false,
        webContents: { isDestroyed: () => false, send },
      }),
      showWindow,
      pollIntervalMs: 100,
    });

    service.start();
    await vi.advanceTimersByTimeAsync(1_000);
    expect(request).not.toHaveBeenCalled();

    service.setRendererReady(true);
    await vi.advanceTimersByTimeAsync(0);

    expect(request).toHaveBeenCalledTimes(1);
    expect(request).toHaveBeenCalledWith(
      "/api/agent/v1/ui-handoffs/claim-next",
      expect.objectContaining({ method: "POST" }),
    );
    const requestBody = JSON.parse(String(request.mock.calls[0]?.[1]?.body));
    expect(requestBody).toEqual({ consumer_id: "desktop:test" });
    expect(showWindow).toHaveBeenCalledOnce();
    expect(send).toHaveBeenCalledWith(
      DESKTOP_IPC_CHANNELS.agentUiHandoffDeliver,
      expect.objectContaining({
        handoffId: "uih_test",
        selectedIds: [7, 9],
        surface: "professors.management",
      }),
    );
    service.stop();
  });

  it("retains an in-flight claim across renderer reloads and redelivers it", async () => {
    let resolveClaim: (response: Response) => void = () => {
      throw new Error("claim response resolver was not initialized");
    };
    const claimResponse = new Promise<Response>((resolve) => {
      resolveClaim = resolve;
    });
    const request = vi.fn<DesktopBackendClient["request"]>()
      .mockReturnValueOnce(claimResponse)
      .mockResolvedValue(new Response(null, { status: 204 }));
    const send = vi.fn();
    const service = createAgentUiHandoffService({
      backendClient: { request },
      consumerId: "desktop:test",
      getRenderer: () => ({
        isDestroyed: () => false,
        webContents: { isDestroyed: () => false, send },
      }),
      showWindow: vi.fn(),
      pollIntervalMs: 100,
    });

    service.start();
    service.setRendererReady(true);
    await vi.advanceTimersByTimeAsync(0);
    expect(request).toHaveBeenCalledTimes(1);

    service.setRendererReady(false);
    resolveClaim(jsonResponse(claimedHandoff()));
    await vi.advanceTimersByTimeAsync(0);
    expect(send).not.toHaveBeenCalled();

    service.setRendererReady(true);
    await vi.advanceTimersByTimeAsync(0);
    expect(request).toHaveBeenCalledTimes(1);
    expect(send).toHaveBeenCalledTimes(1);

    service.setRendererReady(false);
    service.setRendererReady(true);
    await vi.advanceTimersByTimeAsync(0);
    expect(request).toHaveBeenCalledTimes(1);
    expect(send).toHaveBeenCalledTimes(2);
    service.stop();
  });

  it("does not overlap claims and immediately polls after acknowledgement", async () => {
    const request = vi.fn<DesktopBackendClient["request"]>(async (path) => {
      if (path.endsWith("/acknowledge")) {
        return jsonResponse(publicHandoff({ status: "applied", applied_at: new Date().toISOString() }));
      }
      if (request.mock.calls.filter(([calledPath]) => calledPath.endsWith("claim-next")).length === 1) {
        return jsonResponse(claimedHandoff());
      }
      return new Response(null, { status: 204 });
    });
    const service = createAgentUiHandoffService({
      backendClient: { request },
      consumerId: "desktop:test",
      getRenderer: () => ({
        isDestroyed: () => false,
        webContents: { isDestroyed: () => false, send: vi.fn() },
      }),
      showWindow: vi.fn(),
      pollIntervalMs: 100,
    });
    service.start();
    service.setRendererReady(true);
    await vi.advanceTimersByTimeAsync(0);
    await vi.advanceTimersByTimeAsync(500);
    expect(request.mock.calls.filter(([path]) => path.endsWith("claim-next"))).toHaveLength(1);

    const state = await service.acknowledge({
      handoffId: "uih_test",
      status: "applied",
      result: { route: "/professors" },
    });
    expect(state.status).toBe("applied");
    const acknowledgementCall = request.mock.calls.find(([path]) => path.endsWith("/acknowledge"));
    expect(JSON.parse(String(acknowledgementCall?.[1]?.body))).toEqual({
      consumer_id: "desktop:test",
      status: "applied",
      result: { route: "/professors" },
    });

    await vi.advanceTimersByTimeAsync(0);
    expect(request.mock.calls.filter(([path]) => path.endsWith("claim-next"))).toHaveLength(2);
    service.stop();
  });

  it("lets an expired lease be reclaimed and redelivered", async () => {
    const first = claimedHandoff({
      claim_expires_at: new Date(Date.now() + 250).toISOString(),
    });
    const second = claimedHandoff({
      delivery_attempts: 2,
      claim_expires_at: new Date(Date.now() + 30_000).toISOString(),
    });
    const request = vi.fn<DesktopBackendClient["request"]>()
      .mockResolvedValueOnce(jsonResponse(first))
      .mockResolvedValueOnce(jsonResponse(second));
    const send = vi.fn();
    const service = createAgentUiHandoffService({
      backendClient: { request },
      consumerId: "desktop:test",
      getRenderer: () => ({
        isDestroyed: () => false,
        webContents: { isDestroyed: () => false, send },
      }),
      showWindow: vi.fn(),
      pollIntervalMs: 100,
    });
    service.start();
    service.setRendererReady(true);
    await vi.advanceTimersByTimeAsync(0);
    expect(send).toHaveBeenCalledTimes(1);

    await vi.advanceTimersByTimeAsync(400);
    expect(request).toHaveBeenCalledTimes(2);
    expect(send).toHaveBeenCalledTimes(2);
    expect(send.mock.calls[1]?.[1]).toEqual(expect.objectContaining({ deliveryAttempts: 2 }));
    service.stop();
  });

  it("retries backend outages and malformed responses without unhandled failures", async () => {
    const warn = vi.fn();
    const request = vi.fn<DesktopBackendClient["request"]>()
      .mockRejectedValueOnce(new Error("backend restarting"))
      .mockResolvedValueOnce(jsonResponse({ invalid: true }))
      .mockResolvedValueOnce(new Response(null, { status: 204 }));
    const service = createAgentUiHandoffService({
      backendClient: { request },
      consumerId: "desktop:test",
      getRenderer: () => ({
        isDestroyed: () => false,
        webContents: { isDestroyed: () => false, send: vi.fn() },
      }),
      showWindow: vi.fn(),
      pollIntervalMs: 100,
      dependencies: { warn },
    });
    service.start();
    service.setRendererReady(true);
    await vi.advanceTimersByTimeAsync(250);

    expect(request).toHaveBeenCalledTimes(3);
    expect(warn).toHaveBeenCalledTimes(2);
    service.stop();
    await vi.advanceTimersByTimeAsync(1_000);
    expect(request).toHaveBeenCalledTimes(3);
  });

  it("does not deliver a claim issued to a different desktop consumer", async () => {
    const warn = vi.fn();
    const send = vi.fn();
    const request = vi.fn<DesktopBackendClient["request"]>()
      .mockResolvedValueOnce(jsonResponse(claimedHandoff({
        consumer_id: "desktop:other",
      })))
      .mockResolvedValue(new Response(null, { status: 204 }));
    const service = createAgentUiHandoffService({
      backendClient: { request },
      consumerId: "desktop:test",
      getRenderer: () => ({
        isDestroyed: () => false,
        webContents: { isDestroyed: () => false, send },
      }),
      showWindow: vi.fn(),
      pollIntervalMs: 100,
      dependencies: { warn },
    });

    service.start();
    service.setRendererReady(true);
    await vi.advanceTimersByTimeAsync(0);

    expect(send).not.toHaveBeenCalled();
    expect(warn).toHaveBeenCalledOnce();
    service.stop();
  });

  it("returns authoritative backend state when an acknowledgement conflicts", async () => {
    const request = vi.fn<DesktopBackendClient["request"]>()
      .mockResolvedValueOnce(jsonResponse({
        error: { code: "UI_HANDOFF_ACKNOWLEDGEMENT_CONFLICT", message: "已取消" },
      }, 409))
      .mockResolvedValueOnce(jsonResponse(publicHandoff({
        status: "canceled",
        canceled_at: new Date().toISOString(),
      })));
    const service = createAgentUiHandoffService({
      backendClient: { request },
      consumerId: "desktop:test",
      getRenderer: () => null,
      showWindow: vi.fn(),
    });

    const state = await service.acknowledge({
      handoffId: "uih_test",
      status: "applied",
    });

    expect(state.status).toBe("canceled");
    expect(request.mock.calls.map(([path]) => path)).toEqual([
      "/api/agent/v1/ui-handoffs/uih_test/acknowledge",
      "/api/agent/v1/ui-handoffs/uih_test",
    ]);
  });

  it("rejects malformed claims before they reach the renderer", () => {
    expect(() => parseClaimedUiHandoff(claimedHandoff({ selected_ids: [1, 0] }))).toThrow(
      "selected_ids",
    );
    expect(() => parseClaimedUiHandoff(claimedHandoff({ surface: "unknown" }))).toThrow(
      "surface",
    );
    expect(() => parseClaimedUiHandoff(claimedHandoff({ claim_expires_at: "invalid" }))).toThrow(
      "claim_expires_at",
    );
  });
});
