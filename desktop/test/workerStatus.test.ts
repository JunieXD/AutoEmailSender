import { mkdtemp, mkdir, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import { afterEach, describe, expect, it } from "vitest";
import {
  getWorkerStatusPath,
  readWorkerRuntimeStatus,
} from "../src/main/backend/worker-status.js";

const temporaryDirectories: string[] = [];

afterEach(async () => {
  await Promise.all(
    temporaryDirectories.splice(0).map((directory) =>
      rm(directory, { recursive: true, force: true }),
    ),
  );
});

describe("Worker runtime status", () => {
  it("accepts protocol v2 heartbeat state and rejects mismatched protocols", async () => {
    const userDataPath = await mkdtemp(path.join(tmpdir(), "worker-status-"));
    temporaryDirectories.push(userDataPath);
    const statusPath = getWorkerStatusPath(userDataPath);
    await mkdir(path.dirname(statusPath), { recursive: true });
    const status = {
      protocol_version: "2",
      runtime_id: "runtime-1",
      role: "worker",
      pid: 1234,
      generation: "generation-1",
      state: "ready",
      started_at: "2026-08-09T00:00:00.000Z",
      updated_at: "2026-08-09T00:00:02.000Z",
      heartbeat_at: "2026-08-09T00:00:02.000Z",
      health: "healthy",
      draining: false,
      error: null,
      subsystems: {},
    };

    await writeFile(statusPath, JSON.stringify(status), "utf8");
    await expect(readWorkerRuntimeStatus(userDataPath)).resolves.toEqual(status);

    await writeFile(statusPath, JSON.stringify({ ...status, protocol_version: "1" }), "utf8");
    await expect(readWorkerRuntimeStatus(userDataPath)).resolves.toBeNull();

    await writeFile(statusPath, JSON.stringify({
      ...status,
      subsystems: { dispatcher: { consecutive_failures: "many" } },
    }), "utf8");
    await expect(readWorkerRuntimeStatus(userDataPath)).resolves.toBeNull();
  });
});
