import { randomUUID } from "node:crypto";
import { mkdtemp, readFile, rename, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import { performance } from "node:perf_hooks";
import { afterEach, describe, expect, it } from "vitest";

import { startBackend } from "../src/main/backend/service.js";
import type { BackendStatus } from "../src/main/backend/types.js";
import {
  WORKER_HEARTBEAT_TIMEOUT_MS,
  getWorkerStatusPath,
} from "../src/main/backend/worker-status.js";

const temporaryDirectories: string[] = [];

afterEach(async () => {
  await Promise.all(
    temporaryDirectories.splice(0).map((directory) =>
      rm(directory, { recursive: true, force: true }),
    ),
  );
});

describe("real API + Worker runtime group", () => {
  it("recovers Worker locally, restarts the whole group after API loss, and stops cleanly", async () => {
    const userDataPath = await mkdtemp(path.join(tmpdir(), "desktop-runtime-group-"));
    temporaryDirectories.push(userDataPath);
    const controller = await startBackend({
      isPackaged: false,
      resourcesPath: path.resolve(".."),
      repoRoot: path.resolve(".."),
      userDataPath,
      appVersion: "2.5.4-test",
      mode: "split",
    });
    const statuses: BackendStatus[] = [];
    const observedWorkerPids = new Set<number>();
    controller.onStatus((status) => {
      statuses.push(status);
      if (status.state === "ready" && controller.workerPid !== undefined) {
        observedWorkerPids.add(controller.workerPid);
      }
    });

    let finalApiPid: number | undefined;
    let finalWorkerPid: number | undefined;
    let shutdownStartedAt: number | undefined;
    try {
      await controller.ready;
      const initialRuntimeId = controller.runtimeId;
      const initialApiPid = controller.backendPid;
      const initialWorkerPid = controller.workerPid;
      expect(initialApiPid).toBeGreaterThan(0);
      expect(initialWorkerPid).toBeGreaterThan(0);
      expect(initialWorkerPid).not.toBe(initialApiPid);

      const initialSettings = await requestJson(
        controller.baseUrl,
        controller.uiAccessToken,
        "/api/runtime-settings",
      );
      expect(initialSettings.match_analysis_job_worker_count).toBeGreaterThan(0);

      killProcess(initialWorkerPid!);
      await waitUntil(
        () => statuses.some((status) => status.state === "degraded"),
        "Worker degraded status",
      );
      expect(controller.backendPid).toBe(initialApiPid);
      expect(controller.runtimeId).toBe(initialRuntimeId);

      const settingsDuringRecovery = await requestJson(
        controller.baseUrl,
        controller.uiAccessToken,
        "/api/runtime-settings",
      );
      const { updated_at: _updatedAt, revision: _revision, ...settingsUpdate } = settingsDuringRecovery;
      const updatedSettings = await requestJson(
        controller.baseUrl,
        controller.uiAccessToken,
        "/api/runtime-settings",
        {
          method: "PATCH",
          body: JSON.stringify({
            ...settingsUpdate,
            draft_custom_instruction: "runtime-group-sync-write",
          }),
        },
      );
      expect(updatedSettings.draft_custom_instruction).toBe("runtime-group-sync-write");

      await waitUntil(
        () => controller.workerPid !== undefined && controller.workerPid !== initialWorkerPid,
        "replacement Worker",
        15_000,
      );
      let replacementWorkerPid = controller.workerPid!;
      expect(controller.backendPid).toBe(initialApiPid);
      expect(isProcessAlive(initialWorkerPid!)).toBe(false);
      await delay(2_500);
      expect(controller.workerPid).toBe(replacementWorkerPid);
      expect(observedWorkerPids).toEqual(new Set([initialWorkerPid!, replacementWorkerPid]));

      if (process.platform !== "win32") {
        const statusCountBeforeDegradation = statuses.length;
        process.kill(replacementWorkerPid, "SIGSTOP");
        try {
          const statusPath = getWorkerStatusPath(userDataPath);
          const workerStatus = JSON.parse(await readFile(statusPath, "utf8"));
          const temporaryStatusPath = `${statusPath}.${randomUUID()}.tmp`;
          await writeFile(temporaryStatusPath, JSON.stringify({
            ...workerStatus,
            health: "degraded",
            heartbeat_at: new Date().toISOString(),
            updated_at: new Date().toISOString(),
            subsystems: {
              ...workerStatus.subsystems,
              dispatcher: {
                last_started_at: new Date().toISOString(),
                last_succeeded_at: null,
                last_failed_at: new Date().toISOString(),
                consecutive_failures: 2,
                error: "password=visible-secret body=private-message",
              },
            },
          }), "utf8");
          await rename(temporaryStatusPath, statusPath);
          await waitUntil(
            () => statuses.slice(statusCountBeforeDegradation).some(isBackgroundDegraded),
            "degraded subsystem status",
          );
          const degraded = statuses.slice(statusCountBeforeDegradation).find(
            isBackgroundDegraded,
          );
          expect(degraded).toMatchObject({
            state: "degraded",
            workerPid: replacementWorkerPid,
          });
          expect(degraded?.detail).toContain("password=[REDACTED]");
          expect(degraded?.detail).toContain("body=[REDACTED]");
          expect(degraded?.detail).not.toContain("visible-secret");
          expect(degraded?.detail).not.toContain("private-message");
          expect(controller.backendPid).toBe(initialApiPid);
          expect(controller.workerPid).toBe(replacementWorkerPid);
        } finally {
          process.kill(replacementWorkerPid, "SIGCONT");
        }
        await waitUntil(
          () => statuses.slice(statusCountBeforeDegradation).some(
            (status) => status.state === "ready",
          ),
          "Worker health recovery",
          6_000,
        );
        expect(controller.workerPid).toBe(replacementWorkerPid);

        const statusCountBeforeSystemSuspend = statuses.length;
        process.kill(replacementWorkerPid, "SIGSTOP");
        controller.notifySystemSuspend?.();
        try {
          await delay(WORKER_HEARTBEAT_TIMEOUT_MS + 2_000);
          expect(controller.runtimeId).toBe(initialRuntimeId);
          expect(controller.backendPid).toBe(initialApiPid);
          expect(controller.workerPid).toBe(replacementWorkerPid);
          expect(
            statuses.slice(statusCountBeforeSystemSuspend).some(
              (status) => status.state === "degraded" && status.reason === "background_hung",
            ),
          ).toBe(false);
        } finally {
          process.kill(replacementWorkerPid, "SIGCONT");
          controller.notifySystemResume?.();
        }
        await delay(2_500);
        expect(controller.workerPid).toBe(replacementWorkerPid);

        const statusCountBeforeHang = statuses.length;
        process.kill(replacementWorkerPid, "SIGSTOP");
        const heartbeatStoppedAt = performance.now();
        await waitUntil(
          () => statuses.slice(statusCountBeforeHang).some(
            (status) => status.state === "degraded" && status.reason === "background_hung",
          ),
          "hung Worker detection",
          20_000,
        );
        const heartbeatDetectionElapsedMs = performance.now() - heartbeatStoppedAt;
        expect(heartbeatDetectionElapsedMs).toBeGreaterThanOrEqual(14_000);
        expect(heartbeatDetectionElapsedMs).toBeLessThan(20_000);
        await waitUntil(
          () => controller.workerPid !== undefined && controller.workerPid !== replacementWorkerPid,
          "replacement after hung Worker",
          15_000,
        );
        expect(controller.backendPid).toBe(initialApiPid);
        expect(isProcessAlive(replacementWorkerPid)).toBe(false);
        replacementWorkerPid = controller.workerPid!;
      }

      killProcess(initialApiPid);
      await waitUntil(
        () => !isProcessAlive(replacementWorkerPid),
        "old Worker exit before group replacement",
      );
      await waitUntil(
        () => (
          controller.runtimeId !== initialRuntimeId
          && controller.backendPid > 0
          && controller.workerPid !== undefined
        ),
        "replacement runtime group",
        20_000,
      );
      expect(controller.runtimeId).not.toBe(initialRuntimeId);
      expect(controller.backendPid).not.toBe(initialApiPid);
      expect(controller.workerPid).not.toBe(replacementWorkerPid);
      expect(isProcessAlive(replacementWorkerPid)).toBe(false);

      await waitUntil(async () => {
        try {
          const runtime = await controller.getRuntimeInfo();
          return runtime.runtime_id === controller.runtimeId && runtime.state === "ready";
        } catch {
          return false;
        }
      }, "replacement API readiness");
      finalApiPid = controller.backendPid;
      finalWorkerPid = controller.workerPid;
    } finally {
      shutdownStartedAt = performance.now();
      await controller.stop();
    }

    expect(finalApiPid).toBeDefined();
    expect(finalWorkerPid).toBeDefined();
    await waitUntil(
      () => !isProcessAlive(finalApiPid!) && !isProcessAlive(finalWorkerPid!),
      "runtime group shutdown",
      15_000,
    );
    expect(shutdownStartedAt).toBeDefined();
    expect(performance.now() - shutdownStartedAt!).toBeLessThan(10_000);
  }, 85_000);
});

async function requestJson(
  baseUrl: string,
  accessToken: string,
  pathname: string,
  init: RequestInit = {},
): Promise<Record<string, any>> {
  const headers = new Headers(init.headers);
  headers.set("Authorization", `Bearer ${accessToken}`);
  if (init.body !== undefined) {
    headers.set("Content-Type", "application/json");
  }
  const response = await fetch(`${baseUrl}${pathname}`, { ...init, headers });
  const body = await response.text();
  if (!response.ok) {
    throw new Error(`${init.method ?? "GET"} ${pathname} failed ${response.status}: ${body}`);
  }
  return JSON.parse(body) as Record<string, any>;
}

async function waitUntil(
  predicate: () => boolean | Promise<boolean>,
  description: string,
  timeoutMs = 10_000,
): Promise<void> {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    if (await predicate()) {
      return;
    }
    await delay(50);
  }
  throw new Error(`Timed out waiting for ${description}`);
}

function killProcess(pid: number): void {
  process.kill(pid, process.platform === "win32" ? undefined : "SIGKILL");
}

function isProcessAlive(pid: number): boolean {
  try {
    process.kill(pid, 0);
    return true;
  } catch {
    return false;
  }
}

function delay(milliseconds: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, milliseconds));
}

function isBackgroundDegraded(
  status: BackendStatus,
): status is Extract<BackendStatus, { state: "degraded" }> {
  return status.state === "degraded" && status.reason === "background_degraded";
}
