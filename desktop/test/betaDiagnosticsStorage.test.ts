import {
  chmod,
  lstat,
  mkdtemp,
  readFile,
  rm,
  symlink,
  utimes,
} from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import { afterEach, describe, expect, it } from "vitest";

import {
  clearDiagnosticSegments,
  getComponentSegmentsPath,
  getDiagnosticsStorageStatus,
  listDiagnosticSegments,
  pruneDiagnosticsStorage,
  RotatingJsonlWriter,
} from "../src/main/diagnostics/storage.js";

const temporaryDirectories: string[] = [];

async function createTemporaryDirectory(): Promise<string> {
  const directory = await mkdtemp(path.join(tmpdir(), "beta-diagnostics-storage-"));
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

describe("beta diagnostics rotating JSONL storage", () => {
  it("rotates bounded private JSONL segments", async () => {
    const rootPath = await createTemporaryDirectory();
    const writer = new RotatingJsonlWriter({
      rootPath,
      component: "electron",
      stream: "timeline",
      maxSegmentBytes: 180,
      maxRecordBytes: 160,
    });

    for (let index = 0; index < 8; index += 1) {
      await writer.append({ index, value: "x".repeat(60) });
    }
    await writer.close();

    const segments = await listDiagnosticSegments(rootPath);
    expect(segments.length).toBeGreaterThan(1);
    const indices: number[] = [];
    for (const segment of segments) {
      expect(segment.bytes).toBeLessThanOrEqual(180);
      const lines = (await readFile(segment.path, "utf8")).trim().split("\n");
      indices.push(...lines.map((line) => JSON.parse(line).index));
      if (process.platform !== "win32") {
        expect((await lstat(segment.path)).mode & 0o777).toBe(0o600);
      }
    }
    expect(indices.sort((left, right) => left - right)).toEqual([0, 1, 2, 3, 4, 5, 6, 7]);
    if (process.platform !== "win32") {
      expect((await lstat(getComponentSegmentsPath(rootPath, "electron"))).mode & 0o777)
        .toBe(0o700);
    }
  });

  it("rejects oversized records without corrupting the current segment", async () => {
    const rootPath = await createTemporaryDirectory();
    const writer = new RotatingJsonlWriter({
      rootPath,
      component: "electron",
      stream: "timeline",
      maxRecordBytes: 80,
    });

    await writer.append({ event: "before" });
    await expect(writer.append({ value: "x".repeat(200) })).rejects.toThrow(
      "bounded record size",
    );
    await writer.append({ event: "after" });
    await writer.close();

    const content = await readFile((await listDiagnosticSegments(rootPath))[0].path, "utf8");
    expect(content.trim().split("\n").map((line) => JSON.parse(line).event))
      .toEqual(["before", "after"]);
  });

  it("prunes by retention and total bytes while protecting active files", async () => {
    const rootPath = await createTemporaryDirectory();
    const writer = new RotatingJsonlWriter({
      rootPath,
      component: "electron",
      stream: "timeline",
      maxSegmentBytes: 100,
    });
    for (let index = 0; index < 6; index += 1) {
      await writer.append({ index, value: "x".repeat(45) });
    }
    const activePath = writer.currentPath;
    await writer.flush();
    const segments = await listDiagnosticSegments(rootPath);
    expect(activePath).not.toBeNull();
    const oldDate = new Date("2025-01-01T00:00:00Z");
    for (const segment of segments.slice(0, -1)) {
      await utimes(segment.path, oldDate, oldDate);
    }

    const status = await pruneDiagnosticsStorage(rootPath, {
      now: new Date("2026-08-10T00:00:00Z"),
      retentionDays: 7,
      maxTotalBytes: 100,
      protectedPaths: new Set(activePath ? [activePath] : []),
    });

    expect(status.segmentCount).toBe(1);
    expect((await listDiagnosticSegments(rootPath))[0].path).toBe(activePath);
    await writer.close();
  });

  it("clears only diagnostic segments", async () => {
    const rootPath = await createTemporaryDirectory();
    const writer = new RotatingJsonlWriter({
      rootPath,
      component: "electron",
      stream: "timeline",
    });
    await writer.append({ event: "test" });
    await writer.close();
    expect((await getDiagnosticsStorageStatus(rootPath)).segmentCount).toBe(1);

    await clearDiagnosticSegments(rootPath);

    expect(await getDiagnosticsStorageStatus(rootPath)).toMatchObject({
      totalBytes: 0,
      segmentCount: 0,
    });
  });

  it.skipIf(process.platform === "win32")("refuses a symlinked component directory", async () => {
    const rootPath = await createTemporaryDirectory();
    const outsidePath = await createTemporaryDirectory();
    const componentPath = getComponentSegmentsPath(rootPath, "electron");
    await chmod(rootPath, 0o700);
    await symlink(outsidePath, componentPath, "dir").catch(async () => {
      const segmentsRoot = path.dirname(componentPath);
      await import("node:fs/promises").then(({ mkdir }) => mkdir(segmentsRoot, { recursive: true }));
      await symlink(outsidePath, componentPath, "dir");
    });
    const writer = new RotatingJsonlWriter({
      rootPath,
      component: "electron",
      stream: "timeline",
    });

    await expect(writer.append({ event: "must-not-write" })).rejects.toThrow(
      "not a private directory",
    );
    expect(await listDiagnosticSegments(rootPath)).toEqual([]);
  });
});
