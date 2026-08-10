import { writeFile } from "node:fs/promises";
import path from "node:path";
import { describe, it } from "vitest";
import { startBackend } from "../src/main/backend/service.js";

const enabled = process.env.AUTO_EMAIL_SENDER_PARENT_DEATH_FIXTURE === "1";

describe.skipIf(!enabled)("runtime group parent-death fixture", () => {
  it("keeps the parent alive until the outer test kills its process group", async () => {
    const userDataPath = requiredEnvironment("AUTO_EMAIL_SENDER_FIXTURE_DATA_DIR");
    const pidPath = requiredEnvironment("AUTO_EMAIL_SENDER_FIXTURE_PID_PATH");
    const repoRoot = path.resolve("..");
    const controller = await startBackend({
      isPackaged: false,
      resourcesPath: repoRoot,
      repoRoot,
      userDataPath,
      appVersion: "2.5.4-parent-death-test",
      mode: "split",
      portRangeStart: 49_120,
    });
    await controller.ready;
    if (controller.workerPid === undefined) {
      throw new Error("Worker did not become ready for the parent-death fixture.");
    }
    await writeFile(pidPath, JSON.stringify({
      desktopPid: process.pid,
      apiPid: controller.backendPid,
      workerPid: controller.workerPid,
    }), "utf8");
    await new Promise<void>(() => undefined);
  }, 120_000);
});

function requiredEnvironment(name: string): string {
  const value = process.env[name]?.trim();
  if (!value) {
    throw new Error(`${name} is required`);
  }
  return value;
}
