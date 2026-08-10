import { execFile } from "node:child_process";
import { access, mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import { promisify } from "node:util";
import { afterEach, describe, expect, it } from "vitest";

import { startBackend } from "../src/main/backend/service.js";
import {
  readBackendModeSetting,
  resolveBackendModeSelection,
  writeBackendModeSetting,
} from "../src/main/settings/backend-mode.js";
import type { BackendMode } from "../src/main/backend/types.js";
import { getWorkerStatusPath } from "../src/main/backend/worker-status.js";

const enabled = process.env.AUTO_EMAIL_SENDER_MODE_SWITCH_QA === "1";
const execFileAsync = promisify(execFile);
const temporaryDirectories: string[] = [];

afterEach(async () => {
  await Promise.all(
    temporaryDirectories.splice(0).map((directory) =>
      rm(directory, { recursive: true, force: true }),
    ),
  );
});

describe.skipIf(!enabled)("combined/split repeated mode switching", () => {
  it("switches twenty times against one database without corruption or orphan processes", async () => {
    const userDataPath = await mkdtemp(path.join(tmpdir(), "desktop-mode-switch-"));
    temporaryDirectories.push(userDataPath);
    const repoRoot = path.resolve("..");
    let expectedInstruction = "";
    const stoppedPids = new Set<number>();

    for (let launchIndex = 0; launchIndex <= 20; launchIndex += 1) {
      const requestedMode: BackendMode = launchIndex % 2 === 0 ? "combined" : "split";
      await writeBackendModeSetting(userDataPath, requestedMode);
      const setting = await readBackendModeSetting(userDataPath);
      const resolution = resolveBackendModeSelection({
        argv: [],
        setting,
        appVersion: "2.6.0-beta.1",
      });
      expect(resolution).toMatchObject({
        mode: requestedMode,
        source: "settings",
      });

      const controller = await startBackend({
        isPackaged: false,
        resourcesPath: repoRoot,
        repoRoot,
        userDataPath,
        appVersion: "2.6.0-beta.1",
        mode: resolution.mode,
      });
      let apiPid = 0;
      let workerPid: number | undefined;
      try {
        await controller.ready;
        expect(controller.mode).toBe(requestedMode);
        apiPid = controller.backendPid;
        workerPid = controller.workerPid;
        expect(apiPid).toBeGreaterThan(0);
        if (requestedMode === "split") {
          expect(workerPid).toBeGreaterThan(0);
          expect(workerPid).not.toBe(apiPid);
        } else {
          expect(workerPid).toBeUndefined();
        }

        const current = await requestJson(
          controller.baseUrl,
          controller.uiAccessToken,
          "/api/runtime-settings",
        );
        expect(current.draft_custom_instruction).toBe(expectedInstruction);
        expectedInstruction = `mode-switch-${launchIndex}`;
        const { updated_at: _updatedAt, revision: _revision, ...payload } = current;
        const updated = await requestJson(
          controller.baseUrl,
          controller.uiAccessToken,
          "/api/runtime-settings",
          {
            method: "PATCH",
            body: JSON.stringify({
              ...payload,
              draft_custom_instruction: expectedInstruction,
            }),
          },
        );
        expect(updated.draft_custom_instruction).toBe(expectedInstruction);
      } finally {
        await controller.stop();
      }

      if (apiPid > 0) {
        stoppedPids.add(apiPid);
      }
      if (workerPid !== undefined) {
        stoppedPids.add(workerPid);
      }
      await waitUntil(
        () => [...stoppedPids].every((pid) => !isProcessAlive(pid)),
        `mode switch ${launchIndex} process cleanup`,
      );
      await expect(access(getWorkerStatusPath(userDataPath))).rejects.toMatchObject({
        code: "ENOENT",
      });
    }

    const databasePath = path.join(userDataPath, "auto_email_sender.db");
    const checkScript = [
      "import sqlite3, sys",
      "connection = sqlite3.connect(sys.argv[1])",
      "print(connection.execute('PRAGMA integrity_check').fetchone()[0])",
      "print(len(connection.execute('PRAGMA foreign_key_check').fetchall()))",
      "print(connection.execute('PRAGMA journal_mode').fetchone()[0])",
      "connection.close()",
    ].join("; ");
    const { stdout } = await execFileAsync(
      "uv",
      [
        "run",
        "--project",
        path.join(repoRoot, "backend"),
        "--no-sync",
        "python",
        "-c",
        checkScript,
        databasePath,
      ],
      { cwd: repoRoot },
    );
    expect(stdout.trim().split(/\r?\n/u)).toEqual(["ok", "0", "wal"]);
  }, 180_000);
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
  predicate: () => boolean,
  description: string,
  timeoutMs = 10_000,
): Promise<void> {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    if (predicate()) {
      return;
    }
    await new Promise((resolve) => setTimeout(resolve, 50));
  }
  throw new Error(`Timed out waiting for ${description}`);
}

function isProcessAlive(pid: number): boolean {
  try {
    process.kill(pid, 0);
    return true;
  } catch {
    return false;
  }
}
