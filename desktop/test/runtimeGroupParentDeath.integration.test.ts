import { execFile } from "node:child_process";
import { spawn } from "node:child_process";
import { once } from "node:events";
import { mkdtemp, readFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import { promisify } from "node:util";
import { afterEach, describe, expect, it } from "vitest";
import { captureProcessOutput } from "../src/main/backend/process-output.js";

const execFileAsync = promisify(execFile);
const temporaryDirectories: string[] = [];

type RuntimePids = {
  desktopPid: number;
  apiPid: number;
  workerPid: number;
};

afterEach(async () => {
  await Promise.all(
    temporaryDirectories.splice(0).map((directory) =>
      rm(directory, { recursive: true, force: true }),
    ),
  );
});

describe("runtime group parent death", () => {
  it.skipIf(process.platform === "win32")(
    "leaves no API, Worker, or role process-group descendants after the desktop is killed",
    async () => {
      const root = await mkdtemp(path.join(tmpdir(), "desktop-parent-death-"));
      temporaryDirectories.push(root);
      const userDataPath = path.join(root, "data");
      const pidPath = path.join(root, "pids.json");
      const vitestPath = path.resolve("node_modules", "vitest", "vitest.mjs");
      const child = spawn(process.execPath, [
        vitestPath,
        "run",
        "test/runtimeGroupParent.fixture.test.ts",
        "--reporter=dot",
      ], {
        cwd: path.resolve("."),
        detached: true,
        env: {
          ...process.env,
          AUTO_EMAIL_SENDER_PARENT_DEATH_FIXTURE: "1",
          AUTO_EMAIL_SENDER_FIXTURE_DATA_DIR: userDataPath,
          AUTO_EMAIL_SENDER_FIXTURE_PID_PATH: pidPath,
        },
        stdio: ["ignore", "pipe", "pipe"],
      });
      const output = captureProcessOutput(child);
      let pids: RuntimePids | null = null;
      let apiProcessGroup: number | null = null;
      let workerProcessGroup: number | null = null;
      try {
        pids = await waitForPidFile(
          pidPath,
          child,
          () => output.stderr.sanitizedText(),
        );
        expect(pids.desktopPid).toBeGreaterThan(0);
        expect(pids.apiPid).toBeGreaterThan(0);
        expect(pids.workerPid).toBeGreaterThan(0);
        apiProcessGroup = await getProcessGroupId(pids.apiPid);
        workerProcessGroup = await getProcessGroupId(pids.workerPid);

        const killedAt = Date.now();
        process.kill(-child.pid!, "SIGKILL");
        await once(child, "exit");
        await waitUntil(async () => (
          !isProcessAlive(pids!.apiPid)
          && !isProcessAlive(pids!.workerPid)
          && !await processGroupHasLiveMembers(apiProcessGroup!)
          && !await processGroupHasLiveMembers(workerProcessGroup!)
        ), "runtime descendants to exit", 15_000);
        expect(Date.now() - killedAt).toBeLessThanOrEqual(15_000);
      } finally {
        if (child.exitCode === null && child.signalCode === null && child.pid !== undefined) {
          killProcessGroup(child.pid);
          await once(child, "exit").catch(() => undefined);
        }
        if (apiProcessGroup !== null) {
          killProcessGroup(apiProcessGroup);
        }
        if (workerProcessGroup !== null) {
          killProcessGroup(workerProcessGroup);
        }
      }
    },
    45_000,
  );
});

async function waitForPidFile(
  pidPath: string,
  child: ReturnType<typeof spawn>,
  getStderr: () => string,
): Promise<RuntimePids> {
  let parsed: RuntimePids | null = null;
  await waitUntil(async () => {
    if (child.exitCode !== null || child.signalCode !== null) {
      throw new Error(`Parent-death fixture exited before readiness: ${getStderr()}`);
    }
    try {
      parsed = JSON.parse(await readFile(pidPath, "utf8")) as RuntimePids;
      return true;
    } catch {
      return false;
    }
  }, "parent-death fixture readiness", 20_000);
  return parsed!;
}

async function getProcessGroupId(pid: number): Promise<number> {
  const { stdout } = await execFileAsync("ps", ["-p", String(pid), "-o", "pgid="]);
  const processGroup = Number(stdout.trim());
  if (!Number.isSafeInteger(processGroup) || processGroup <= 0) {
    throw new Error(`Unable to resolve process group for pid ${pid}`);
  }
  return processGroup;
}

async function processGroupHasLiveMembers(processGroup: number): Promise<boolean> {
  const { stdout } = await execFileAsync("ps", ["-Ao", "pgid=,stat="]);
  return stdout.split("\n").some((line) => {
    const match = line.trim().match(/^(\d+)\s+(\S+)/u);
    return match !== null
      && Number(match[1]) === processGroup
      && !match[2].startsWith("Z");
  });
}

async function waitUntil(
  predicate: () => boolean | Promise<boolean>,
  description: string,
  timeoutMs: number,
): Promise<void> {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    if (await predicate()) {
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

function killProcessGroup(processGroup: number): void {
  try {
    process.kill(-processGroup, "SIGKILL");
  } catch {
    // The exact validated process group has already exited.
  }
}
