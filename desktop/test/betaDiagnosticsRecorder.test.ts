import {
  access,
  mkdir,
  mkdtemp,
  readFile,
  rm,
  symlink,
  writeFile,
} from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import { afterEach, describe, expect, it } from "vitest";

import { getBetaDiagnosticsRoot } from "../src/main/diagnostics/constants.js";
import {
  DesktopBetaDiagnosticsRecorder,
  readInstallationId,
} from "../src/main/diagnostics/recorder.js";
import { listDiagnosticSegments } from "../src/main/diagnostics/storage.js";

const temporaryDirectories: string[] = [];

async function createTemporaryDirectory(): Promise<string> {
  const directory = await mkdtemp(path.join(tmpdir(), "beta-diagnostics-recorder-"));
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

describe("desktop beta diagnostics recorder", () => {
  it("does nothing when beta diagnostics are disabled", async () => {
    const userDataPath = await createTemporaryDirectory();
    const recorder = createRecorder(userDataPath, { enabled: false });

    await recorder.start();
    await recorder.recordTimeline("should_not_exist");
    await recorder.stop();

    await expect(access(getBetaDiagnosticsRoot(userDataPath))).rejects.toMatchObject({
      code: "ENOENT",
    });
    expect((await recorder.getStatus()).enabled).toBe(false);
  });

  it("records schema-versioned timeline and resource samples without raw paths", async () => {
    const userDataPath = await createTemporaryDirectory();
    const recorder = createRecorder(userDataPath);

    await recorder.start();
    await recorder.recordTimeline("backend_ready", {
      state: "ready",
      effective_mode: "split",
      body: "private body",
      note: `failure at ${userDataPath} email=private@example.test`,
    });
    await recorder.recordProblemMarker({
      category: "background_stall",
      note: "token=super-secret",
    });
    await recorder.recordResourceSample();
    const installationId = await readInstallationId(recorder.rootPath);
    await recorder.stop();

    expect(installationId).toMatch(/^[0-9a-f-]{36}$/u);
    await expect(access(path.join(recorder.rootPath, "active-session.json")))
      .rejects.toMatchObject({ code: "ENOENT" });
    const segmentContents = await Promise.all(
      (await listDiagnosticSegments(recorder.rootPath)).map((segment) =>
        readFile(segment.path, "utf8"),
      ),
    );
    const combined = segmentContents.join("\n");
    expect(combined).toContain('"schema_version":1');
    expect(combined).toContain('"event":"backend_ready"');
    expect(combined).toContain('"event":"problem_marked"');
    expect(combined).toContain('"stream":"resource-samples"');
    expect(combined).not.toContain(userDataPath);
    expect(combined).not.toContain("private@example.test");
    expect(combined).not.toContain("super-secret");
    expect(combined).not.toContain("private body");
  });

  it("reports an unclosed previous session on the next start", async () => {
    const userDataPath = await createTemporaryDirectory();
    const first = createRecorder(userDataPath);
    await first.start();
    await first.recordTimeline("simulated_crash_before_cleanup");
    await first.stop();

    const activeSessionPath = path.join(
      getBetaDiagnosticsRoot(userDataPath),
      "active-session.json",
    );
    await import("node:fs/promises").then(({ writeFile }) =>
      writeFile(activeSessionPath, JSON.stringify({
        schema_version: 1,
        session_id: "previous-session",
        app_version: "2.6.0-beta.1",
        started_at: "2026-08-09T00:00:00.000Z",
      })),
    );

    const second = createRecorder(userDataPath);
    await second.start();
    await second.stop();

    const timeline = (
      await Promise.all(
        (await listDiagnosticSegments(second.rootPath))
          .filter((segment) => path.basename(segment.path).startsWith("timeline-"))
          .map((segment) => readFile(segment.path, "utf8")),
      )
    ).join("\n");
    expect(timeline).toContain('"event":"previous_session_abnormal"');
  });

  it("serializes clear with active writes and resumes into fresh segments", async () => {
    const userDataPath = await createTemporaryDirectory();
    const recorder = createRecorder(userDataPath);
    await recorder.start();

    await Promise.all([
      ...Array.from({ length: 20 }, (_, index) =>
        recorder.recordTimeline("concurrent_before_clear", { restart_count: index })
      ),
      recorder.recordResourceSample(),
      recorder.clear(),
    ]);
    await recorder.recordTimeline("after_clear");
    await recorder.recordResourceSample();
    expect((await recorder.getStatus()).lastError).toBeUndefined();
    const beforeStop = await listDiagnosticSegments(recorder.rootPath);
    expect(beforeStop.map((segment) => path.basename(segment.path)).join(","))
      .toContain("resource-samples");
    await recorder.stop();

    const segments = await listDiagnosticSegments(recorder.rootPath);
    const combined = (
      await Promise.all(segments.map((segment) => readFile(segment.path, "utf8")))
    ).join("\n");
    expect(combined).toContain('"event":"diagnostics_cleared"');
    expect(combined).toContain('"event":"after_clear"');
    expect(combined).toContain('"stream":"resource-samples"');
    expect(segments.every((segment) => !segment.active)).toBe(true);
  });

  it("checkpoints stable export segments and resumes recording afterward", async () => {
    const userDataPath = await createTemporaryDirectory();
    const recorder = createRecorder(userDataPath);
    await recorder.start();
    await recorder.recordTimeline("before_export_checkpoint");

    await recorder.flush();

    const checkpointSegments = await listDiagnosticSegments(recorder.rootPath);
    expect(checkpointSegments.length).toBeGreaterThan(0);
    expect(checkpointSegments.every((segment) => !segment.active)).toBe(true);
    await recorder.recordTimeline("after_export_checkpoint");
    await recorder.stop();
    const combined = (
      await Promise.all(
        (await listDiagnosticSegments(recorder.rootPath)).map((segment) =>
          readFile(segment.path, "utf8")
        ),
      )
    ).join("\n");
    expect(combined).toContain('"event":"before_export_checkpoint"');
    expect(combined).toContain('"event":"after_export_checkpoint"');
  });

  it.skipIf(process.platform === "win32")(
    "does not follow a symlinked installation metadata file",
    async () => {
      const userDataPath = await createTemporaryDirectory();
      const rootPath = getBetaDiagnosticsRoot(userDataPath);
      const outsidePath = path.join(await createTemporaryDirectory(), "outside.json");
      await mkdir(rootPath, { recursive: true });
      await writeFile(outsidePath, "PRIVATE_CANARY", "utf8");
      await symlink(outsidePath, path.join(rootPath, "installation.json"));

      const installationId = await readInstallationId(rootPath);

      expect(installationId).toMatch(/^[0-9a-f-]{36}$/u);
      expect(await readFile(outsidePath, "utf8")).toBe("PRIVATE_CANARY");
      expect(await readFile(path.join(rootPath, "installation.json"), "utf8"))
        .not.toContain("PRIVATE_CANARY");
    },
  );
});

function createRecorder(
  userDataPath: string,
  overrides: Partial<ConstructorParameters<typeof DesktopBetaDiagnosticsRecorder>[0]> = {},
) {
  return new DesktopBetaDiagnosticsRecorder({
    userDataPath,
    homePath: "/Users/canary",
    appVersion: "2.6.0-beta.1",
    enabled: true,
    getCurrentMode: () => "split",
    getProcessSnapshot: () => ({ apiPid: 123, workerPid: 456 }),
    sampleIntervalMs: 60_000,
    ...overrides,
  });
}
