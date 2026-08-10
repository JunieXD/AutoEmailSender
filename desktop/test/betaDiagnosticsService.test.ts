import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import { afterEach, describe, expect, it, vi } from "vitest";

import { DesktopBetaDiagnosticsRecorder } from "../src/main/diagnostics/recorder.js";
import {
  createDesktopBetaDiagnosticsService,
  fetchDesktopBetaBackendSnapshot,
} from "../src/main/diagnostics/service.js";

const temporaryDirectories: string[] = [];

async function createTemporaryDirectory(): Promise<string> {
  const directory = await mkdtemp(path.join(tmpdir(), "beta-diagnostics-service-"));
  temporaryDirectories.push(directory);
  return directory;
}

afterEach(async () => {
  await Promise.all(
    temporaryDirectories.splice(0).map((directory) =>
      rm(directory, { recursive: true, force: true }),
    ),
  );
});

describe("desktop Beta diagnostics service", () => {
  it("exports through a native save dialog and keeps an API outage partial", async () => {
    const userDataPath = await createTemporaryDirectory();
    const recorder = createRecorder(userDataPath);
    await recorder.start();
    const writeZip = vi.fn(async (_outputPath: string, _bundle: unknown) => undefined);
    const service = createDesktopBetaDiagnosticsService({
      recorder,
      appVersion: "2.6.0-beta.1",
      backendClient: {
        request: vi.fn(async () => {
          throw new Error("API unavailable");
        }),
      },
      getBackendModeStatus: async () => ({
        currentMode: "split",
        nextMode: "split",
        configuredMode: "split",
        defaultMode: "split",
        source: "settings",
        restartRequired: false,
        overrideActive: false,
      }),
      dependencies: {
        showSaveDialog: vi.fn(async () => ({
          canceled: false,
          filePath: path.join(userDataPath, "report"),
        })),
        writeZip,
        now: () => new Date("2026-08-10T12:00:00Z"),
      },
    });

    const result = await service.exportBundle("24h");

    expect(result).toMatchObject({
      status: "saved",
      fileName: "report.zip",
      partial: true,
      missingSections: ["backend_api_unavailable"],
    });
    expect(writeZip).toHaveBeenCalledOnce();
    expect(writeZip.mock.calls[0][0]).toBe(path.join(userDataPath, "report.zip"));
    await recorder.stop();
  });

  it("does not prepare or write a bundle when the user cancels", async () => {
    const userDataPath = await createTemporaryDirectory();
    const recorder = createRecorder(userDataPath);
    await recorder.start();
    const backendRequest = vi.fn();
    const writeZip = vi.fn(async (_outputPath: string, _bundle: unknown) => undefined);
    const service = createDesktopBetaDiagnosticsService({
      recorder,
      appVersion: "2.6.0-beta.1",
      backendClient: { request: backendRequest },
      getBackendModeStatus: vi.fn(),
      dependencies: {
        showSaveDialog: vi.fn(async () => ({ canceled: true })),
        writeZip,
      },
    });

    await expect(service.exportBundle("1h")).resolves.toEqual({ status: "canceled" });
    expect(backendRequest).not.toHaveBeenCalled();
    expect(writeZip).not.toHaveBeenCalled();
    await recorder.stop();
  });

  it("serializes concurrent native export requests", async () => {
    const userDataPath = await createTemporaryDirectory();
    const recorder = createRecorder(userDataPath);
    await recorder.start();
    let releaseFirstDialog: (() => void) | undefined;
    const firstDialogGate = new Promise<void>((resolve) => {
      releaseFirstDialog = resolve;
    });
    const showSaveDialog = vi
      .fn()
      .mockImplementationOnce(async () => {
        await firstDialogGate;
        return { canceled: true };
      })
      .mockResolvedValue({ canceled: true });
    const service = createDesktopBetaDiagnosticsService({
      recorder,
      appVersion: "2.6.0-beta.1",
      backendClient: { request: vi.fn() },
      getBackendModeStatus: vi.fn(),
      dependencies: { showSaveDialog },
    });

    const firstExport = service.exportBundle("1h");
    const secondExport = service.exportBundle("24h");
    await vi.waitFor(() => expect(showSaveDialog).toHaveBeenCalledOnce());
    releaseFirstDialog?.();

    await expect(firstExport).resolves.toEqual({ status: "canceled" });
    await expect(secondExport).resolves.toEqual({ status: "canceled" });
    expect(showSaveDialog).toHaveBeenCalledTimes(2);
    await recorder.stop();
  });

  it("rejects export and problem markers when diagnostics are disabled", async () => {
    const userDataPath = await createTemporaryDirectory();
    const recorder = createRecorder(userDataPath, false);
    const service = createDesktopBetaDiagnosticsService({
      recorder,
      appVersion: "2.5.4",
      backendClient: { request: vi.fn() },
      getBackendModeStatus: vi.fn(),
    });

    await expect(service.exportBundle("all")).rejects.toThrow("仅在测试版本中启用");
    await expect(service.markProblem({ category: "general" })).rejects.toThrow(
      "仅在测试版本中启用",
    );
  });

  it("bounds and maps the authenticated backend snapshot response", async () => {
    const request = vi.fn(async () => new Response(JSON.stringify({
      workload_summary: { schema_version: 1, workloads: [] },
      database_health: { schema_version: 1, integrity_check: "ok" },
      operation_log_summary: { schema_version: 1, total_24h: 2 },
    }), {
      status: 200,
      headers: { "content-type": "application/json" },
    }));

    const snapshot = await fetchDesktopBetaBackendSnapshot({ request });

    expect(request).toHaveBeenCalledWith(
      "/api/diagnostics/beta-summary",
      expect.objectContaining({ signal: expect.any(AbortSignal) }),
    );
    expect(snapshot.databaseHealth).toEqual({
      schema_version: 1,
      integrity_check: "ok",
    });
  });
});

function createRecorder(userDataPath: string, enabled = true) {
  return new DesktopBetaDiagnosticsRecorder({
    userDataPath,
    homePath: userDataPath,
    appVersion: enabled ? "2.6.0-beta.1" : "2.5.4",
    enabled,
    getCurrentMode: () => "split",
    getProcessSnapshot: () => ({ apiPid: 10, workerPid: 11 }),
    sampleIntervalMs: 60_000,
  });
}
