import {
  mkdtemp,
  mkdir,
  readFile,
  rm,
  symlink,
  writeFile,
} from "node:fs/promises";
import { spawnSync } from "node:child_process";
import { hostname, tmpdir } from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { afterEach, describe, expect, it } from "vitest";

import {
  buildBetaDiagnosticsBundle,
  getBetaDiagnosticsExportFileName,
  isDesktopBetaDiagnosticsRange,
  writeBetaDiagnosticsZip,
} from "../src/main/diagnostics/exporter.js";
import { DesktopBetaDiagnosticsRecorder } from "../src/main/diagnostics/recorder.js";

const temporaryDirectories: string[] = [];

async function createTemporaryDirectory(): Promise<string> {
  const directory = await mkdtemp(path.join(tmpdir(), "beta-diagnostics-exporter-"));
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

describe("beta diagnostics exporter", () => {
  it("builds a bounded partial bundle without leaking source-log or timeline canaries", async () => {
    const userDataPath = await createTemporaryDirectory();
    let now = new Date("2026-08-10T08:00:00.000Z");
    const recorder = new DesktopBetaDiagnosticsRecorder({
      userDataPath,
      homePath: "/Users/canary",
      appVersion: "2.6.0-beta.1",
      enabled: true,
      getCurrentMode: () => "split",
      getProcessSnapshot: () => ({ apiPid: 101, workerPid: 202 }),
      sampleIntervalMs: 60_000,
      now: () => now,
    });
    await recorder.start();
    await recorder.recordTimeline("old_event", { state: "ready" });
    now = new Date("2026-08-10T11:30:00.000Z");
    await recorder.recordTimeline("recent_failure", {
      note: "token=CANARY_TOKEN email=canary@example.test /Users/canary/private.txt",
    }, "error");
    const logsPath = path.join(userDataPath, "logs");
    await mkdir(logsPath, { recursive: true });
    await writeFile(
      path.join(logsPath, "startup.log"),
      "[2026-08-10T11:29:00Z] CANARY_UNSTRUCTURED_BODY 张三\n"
        + "phase=migrating_database\n"
        + "error=OperationalError: database is locked at /Users/canary/private.db\n",
      "utf8",
    );
    await writeFile(
      path.join(logsPath, "backend-errors.log"),
      "Authorization: Bearer CANARY_BEARER canary@example.test\n",
      "utf8",
    );

    const bundle = await buildBetaDiagnosticsBundle({
      rootPath: recorder.rootPath,
      userDataPath,
      appVersion: "2.6.0-beta.1",
      requestedMode: "split",
      effectiveMode: "split",
      range: "1h",
      now: () => new Date("2026-08-10T12:00:00.000Z"),
      flush: () => recorder.flush(),
      getBackendSnapshot: async () => {
        throw new Error("API unavailable at http://127.0.0.1:48120?token=CANARY");
      },
    });
    await recorder.stop();

    expect(bundle.partial).toBe(true);
    expect(bundle.missingSections).toContain("backend_api_unavailable");
    const entries = Object.fromEntries(
      bundle.entries.map((entry) => [entry.name, entry.content.toString("utf8")]),
    );
    expect(Object.keys(entries)).toEqual(expect.arrayContaining([
      "manifest.json",
      "timeline.jsonl",
      "resource-samples.jsonl",
      "workload-summary.json",
      "database-health.json",
      "logs/electron.jsonl",
      "logs/api.jsonl",
      "logs/worker.jsonl",
      "logs/combined.jsonl",
      "logs/startup-summary.jsonl",
      "logs/backend-errors-summary.jsonl",
      "logs/operation-summary.json",
      "summary.json",
      "README.txt",
      "checksums.sha256",
    ]));
    expect(entries["timeline.jsonl"]).toContain("recent_failure");
    expect(entries["timeline.jsonl"]).not.toContain("old_event");
    expect(entries["logs/startup-summary.jsonl"]).toContain("sqlite_busy");
    expect(entries["logs/startup-summary.jsonl"]).toContain("migrating_database");
    const completeText = Object.values(entries).join("\n");
    for (const canary of [
      "CANARY_TOKEN",
      "CANARY_UNSTRUCTURED_BODY",
      "CANARY_BEARER",
      "canary@example.test",
      "/Users/canary",
      "张三",
    ]) {
      expect(completeText).not.toContain(canary);
    }
  });

  it("accepts only strict backend metric fields in a complete bundle", async () => {
    const userDataPath = await createTemporaryDirectory();
    const rootPath = path.join(userDataPath, "beta-diagnostics");
    const bundle = await buildBetaDiagnosticsBundle({
      rootPath,
      userDataPath,
      appVersion: "2.6.0-rc.2",
      requestedMode: "combined",
      effectiveMode: "split",
      range: "all",
      buildIdentity: {
        sourceBranch: "beta/topic",
        releaseSha: "a".repeat(40),
        candidateRunId: "123456",
        candidateAssetName: "AutoEmailSender-2.6.0-rc.2.dmg",
        candidateAssetSha256: "b".repeat(64),
      },
      getBackendSnapshot: async () => ({
        workloadSummary: {
          schema_version: 1,
          generated_at: "2026-08-10T12:00:00Z",
          workloads: [{
            kind: "dispatcher",
            queued: 2,
            running: 1,
            secret: "must-not-leak",
          }],
        },
        databaseHealth: {
          schema_version: 1,
          available: true,
          alembic_revision: "20260810_merge_delivery_scale",
          integrity_check: "ok",
          foreign_key_violation_count: 0,
          database_path: "/Users/private/database.db",
        },
        operationLogSummary: {
          schema_version: 1,
          total_24h: 42,
          categories_24h: [{ category: "mail", event_count: 8, message: "private" }],
        },
      }),
      now: () => new Date("2026-08-10T12:00:00Z"),
    });

    expect(bundle.partial).toBe(false);
    const entries = Object.fromEntries(
      bundle.entries.map((entry) => [entry.name, entry.content.toString("utf8")]),
    );
    const manifest = JSON.parse(entries["manifest.json"]);
    expect(manifest.app.channel).toBe("rc");
    expect(manifest.app.source_branch).toBe("beta/topic");
    expect(manifest.backend).toEqual({
      requested_mode: "combined",
      effective_mode: "split",
    });
    expect(entries["workload-summary.json"]).not.toContain("must-not-leak");
    expect(entries["database-health.json"]).not.toContain("database_path");
    expect(entries["logs/operation-summary.json"]).not.toContain("private");
    expect(entries["checksums.sha256"]).not.toContain("checksums.sha256");
  });

  it("writes the exact prepared entries as a ZIP and cleans no successful output", async () => {
    const userDataPath = await createTemporaryDirectory();
    const bundle = await buildBetaDiagnosticsBundle({
      rootPath: path.join(userDataPath, "beta-diagnostics"),
      userDataPath,
      appVersion: "2.6.0-beta.1",
      requestedMode: "split",
      effectiveMode: "split",
      range: "all",
      getBackendSnapshot: async () => ({
        workloadSummary: { schema_version: 1 },
        databaseHealth: { schema_version: 1 },
        operationLogSummary: { schema_version: 1 },
      }),
    });
    const outputPath = path.join(userDataPath, "report.zip");

    await writeBetaDiagnosticsZip(outputPath, bundle);

    const output = await readFile(outputPath);
    expect(output.subarray(0, 4).toString("hex")).toBe("504b0304");
    expect(output.byteLength).toBeGreaterThan(500);
  });

  it("replaces an existing regular export with complete ZIP bytes", async () => {
    const userDataPath = await createTemporaryDirectory();
    const bundle = await buildBetaDiagnosticsBundle({
      rootPath: path.join(userDataPath, "beta-diagnostics"),
      userDataPath,
      appVersion: "2.6.0-beta.1",
      requestedMode: "split",
      effectiveMode: "split",
      range: "all",
    });
    const outputPath = path.join(userDataPath, "existing.zip");
    await writeFile(outputPath, "previous-complete-file", "utf8");

    await writeBetaDiagnosticsZip(outputPath, bundle);

    const output = await readFile(outputPath);
    expect(output.subarray(0, 4).toString("hex")).toBe("504b0304");
    expect(output.toString("utf8")).not.toBe("previous-complete-file");
  });

  it.skipIf(process.platform === "win32")(
    "replaces an export-path symlink without modifying its target",
    async () => {
      const userDataPath = await createTemporaryDirectory();
      const bundle = await buildBetaDiagnosticsBundle({
        rootPath: path.join(userDataPath, "beta-diagnostics"),
        userDataPath,
        appVersion: "2.6.0-beta.1",
        requestedMode: "split",
        effectiveMode: "split",
        range: "all",
      });
      const outsidePath = path.join(await createTemporaryDirectory(), "outside.txt");
      const outputPath = path.join(userDataPath, "linked.zip");
      await writeFile(outsidePath, "PRIVATE_CANARY", "utf8");
      await symlink(outsidePath, outputPath);

      await writeBetaDiagnosticsZip(outputPath, bundle);

      expect(await readFile(outsidePath, "utf8")).toBe("PRIVATE_CANARY");
      const output = await readFile(outputPath);
      expect(output.subarray(0, 4).toString("hex")).toBe("504b0304");
    },
  );

  it.runIf(process.env.AUTO_EMAIL_SENDER_BETA_DIAGNOSTICS_CROSS_QA === "1")(
    "validates an actual final ZIP with the safe analyzer and a canary token file",
    async () => {
      const userDataPath = await createTemporaryDirectory();
      const recorder = new DesktopBetaDiagnosticsRecorder({
        userDataPath,
        homePath: "/Users/canary",
        appVersion: "2.6.0-beta.1",
        enabled: true,
        getCurrentMode: () => "split",
        getProcessSnapshot: () => ({ apiPid: 101, workerPid: 202 }),
        sampleIntervalMs: 60_000,
        now: () => new Date("2026-08-10T11:30:00.000Z"),
      });
      await recorder.start();
      await recorder.recordTimeline("canary_source_event", {
        note: "token=CANARY_TOKEN password=CANARY_PASSWORD email=canary@example.test "
          + "/Users/canary/private.txt 张三 https://faculty.example.test/private?key=CANARY "
          + `203.0.113.42 ${hostname()}`,
      }, "error");
      const logsPath = path.join(userDataPath, "logs");
      await mkdir(logsPath, { recursive: true });
      await writeFile(
        path.join(logsPath, "startup.log"),
        "[2026-08-10T11:29:00Z] CANARY_UNSTRUCTURED_BODY 张三 /Users/canary/private.db\n",
        "utf8",
      );
      await writeFile(
        path.join(logsPath, "backend-errors.log"),
        "Authorization: Bearer CANARY_BEARER canary@example.test\n",
        "utf8",
      );
      const bundle = await buildBetaDiagnosticsBundle({
        rootPath: recorder.rootPath,
        userDataPath,
        appVersion: "2.6.0-beta.1",
        requestedMode: "split",
        effectiveMode: "split",
        range: "all",
        flush: () => recorder.flush(),
        getBackendSnapshot: async () => {
          throw new Error("backend unavailable");
        },
        now: () => new Date("2026-08-10T12:00:00.000Z"),
      });
      await recorder.stop();

      const outputPath = path.join(userDataPath, "final-diagnostics.zip");
      const tokenPath = path.join(userDataPath, "forbidden-tokens.txt");
      const reportPath = path.join(userDataPath, "analyzer-report.json");
      await writeBetaDiagnosticsZip(outputPath, bundle);
      await writeFile(
        tokenPath,
        [
          "CANARY_TOKEN",
          "CANARY_PASSWORD",
          "CANARY_UNSTRUCTURED_BODY",
          "CANARY_BEARER",
          "canary@example.test",
          "/Users/canary",
          "张三",
          "https://faculty.example.test/private?key=CANARY",
          "203.0.113.42",
          hostname(),
        ].join("\n"),
        "utf8",
      );
      const repositoryRoot = path.resolve(
        path.dirname(fileURLToPath(import.meta.url)),
        "../..",
      );
      const analyzerResult = spawnSync(
        "uv",
        [
          "run",
          "--project",
          path.join(repositoryRoot, "backend"),
          "--no-sync",
          "python",
          path.join(repositoryRoot, "scripts/quality/analyze_beta_diagnostics.py"),
          outputPath,
          "--forbidden-token-file",
          tokenPath,
          "--output",
          reportPath,
        ],
        {
          cwd: repositoryRoot,
          encoding: "utf8",
          maxBuffer: 1024 * 1024,
        },
      );

      expect(analyzerResult.status, analyzerResult.stderr).toBe(0);
      const report = JSON.parse(await readFile(reportPath, "utf8"));
      expect(report.bundle_count).toBe(1);
      expect(report.bundles[0]).toMatchObject({
        partial: true,
        effective_mode: "split",
      });
    },
    30_000,
  );

  it("validates ranges and generates a path-safe timestamped file name", () => {
    expect(isDesktopBetaDiagnosticsRange("1h")).toBe(true);
    expect(isDesktopBetaDiagnosticsRange("24h")).toBe(true);
    expect(isDesktopBetaDiagnosticsRange("7d")).toBe(true);
    expect(isDesktopBetaDiagnosticsRange("all")).toBe(true);
    expect(isDesktopBetaDiagnosticsRange("30d")).toBe(false);
    expect(getBetaDiagnosticsExportFileName(new Date("2026-08-10T12:34:56Z")))
      .toBe("auto-email-sender-beta-diagnostics-20260810123456000.zip");
  });
});
