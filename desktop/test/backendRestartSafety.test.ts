import { describe, expect, it, vi } from "vitest";

import {
  createIdleRestartSafety,
  createUnavailableRestartSafety,
  decideBackendModeRestart,
  getBackendRestartSafety,
} from "../src/main/backend/restart-safety.js";

describe("desktop backend restart safety", () => {
  it("maps the allowlisted backend response", async () => {
    const request = vi.fn(async () => new Response(JSON.stringify({
      safe_to_restart: true,
      confirmation_required: true,
      active_work_count: 9,
      sending_count: 0,
      work_counts: {
        draft_generation: 1,
        match_analysis: 2,
        crawler: 3,
        imap_sync: 3,
      },
      message: "当前有后台工作。",
    }), { status: 200 }));

    await expect(getBackendRestartSafety({ request })).resolves.toEqual({
      safeToRestart: true,
      confirmationRequired: true,
      activeWorkCount: 9,
      sendingCount: 0,
      workCounts: {
        draftGeneration: 1,
        matchAnalysis: 2,
        crawler: 3,
        imapSync: 3,
      },
      message: "当前有后台工作。",
    });
    expect(request).toHaveBeenCalledWith("/api/desktop/restart-safety");
  });

  it("fails closed for an invalid or unavailable response", async () => {
    await expect(getBackendRestartSafety({
      request: async () => new Response(JSON.stringify({ safe_to_restart: true })),
    })).rejects.toThrow("无效的重启安全状态");
    await expect(getBackendRestartSafety({
      request: async () => new Response("unavailable", { status: 503 }),
    })).rejects.toThrow("503");

    expect(createUnavailableRestartSafety("无法确认").safeToRestart).toBe(false);
    expect(createIdleRestartSafety("后台未启动").safeToRestart).toBe(true);
  });

  it("never lets active email delivery confirmation override a restart block", () => {
    const blocked = createUnavailableRestartSafety("邮件正在发送");
    expect(decideBackendModeRestart(blocked, { confirmActiveWork: true }).state).toBe(
      "blocked",
    );

    const active = {
      ...createIdleRestartSafety("有可恢复工作"),
      confirmationRequired: true,
      activeWorkCount: 2,
    };
    expect(decideBackendModeRestart(active).state).toBe("confirmation_required");
    expect(decideBackendModeRestart(active, { confirmActiveWork: true }).state).toBe(
      "restarting",
    );
  });
});
