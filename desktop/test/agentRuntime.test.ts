import { mkdtemp, readFile, readdir, rm, stat, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import { afterEach, describe, expect, it } from "vitest";
import {
  AGENT_RUNTIME_PROTOCOL_VERSION,
  clearAgentRuntimeDescriptor,
  cleanupAgentRuntimeDescriptor,
  getAgentRuntimeFilePath,
  writeAgentRuntimeDescriptor,
  type AgentRuntimeDescriptor,
} from "../src/main/agent-support/runtime.js";

const temporaryDirectories: string[] = [];

async function createTemporaryDirectory(): Promise<string> {
  const directory = await mkdtemp(path.join(tmpdir(), "auto-email-sender-agent-runtime-"));
  temporaryDirectories.push(directory);
  return directory;
}

function createDescriptor(overrides: Partial<AgentRuntimeDescriptor> = {}): AgentRuntimeDescriptor {
  return {
    protocol_version: AGENT_RUNTIME_PROTOCOL_VERSION,
    app_version: "2.4.1",
    runtime_id: "runtime-1234",
    base_url: "http://127.0.0.1:48120",
    access_token: "agent-token",
    desktop: { pid: 1234, started_at: "2026-08-03T00:00:00.000Z" },
    backend: { pid: 5678, started_at: "2026-08-03T00:00:01.000Z" },
    published_at: "2026-08-03T00:00:01.000Z",
    ...overrides,
  };
}

afterEach(async () => {
  await Promise.all(
    temporaryDirectories.splice(0).map((directory) =>
      rm(directory, { recursive: true, force: true }),
    ),
  );
});

describe("Agent runtime descriptor", () => {
  it("writes the Agent-only descriptor atomically with private permissions", async () => {
    const userDataPath = await createTemporaryDirectory();
    const descriptor = createDescriptor();

    const runtimePath = await writeAgentRuntimeDescriptor({ userDataPath, descriptor });

    expect(runtimePath).toBe(getAgentRuntimeFilePath(userDataPath));
    expect(JSON.parse(await readFile(runtimePath, "utf8"))).toEqual(descriptor);
    expect(JSON.parse(await readFile(runtimePath, "utf8"))).not.toHaveProperty("desktop_pid");
    expect(await readdir(path.dirname(runtimePath))).toEqual(["runtime.json"]);
    if (process.platform !== "win32") {
      expect((await stat(path.dirname(runtimePath))).mode & 0o777).toBe(0o700);
      expect((await stat(runtimePath)).mode & 0o777).toBe(0o600);
    }
  });

  it("replaces a previous backend token and removes only the owned descriptor", async () => {
    const userDataPath = await createTemporaryDirectory();
    await writeAgentRuntimeDescriptor({
      userDataPath,
      descriptor: createDescriptor({ access_token: "old-token" }),
    });
    const current = createDescriptor({
      runtime_id: "runtime-current",
      access_token: "new-token",
      base_url: "http://127.0.0.1:48121",
    });
    await writeAgentRuntimeDescriptor({ userDataPath, descriptor: current });

    await expect(
      cleanupAgentRuntimeDescriptor({
        userDataPath,
        runtimeId: "runtime-1234",
      }),
    ).resolves.toBe(false);
    expect(JSON.parse(await readFile(getAgentRuntimeFilePath(userDataPath), "utf8"))).toEqual(current);

    await expect(
      cleanupAgentRuntimeDescriptor({
        userDataPath,
        runtimeId: current.runtime_id,
      }),
    ).resolves.toBe(true);
    await expect(readFile(getAgentRuntimeFilePath(userDataPath), "utf8")).rejects.toMatchObject({
      code: "ENOENT",
    });
  });

  it("leaves malformed or differently owned descriptors untouched", async () => {
    const userDataPath = await createTemporaryDirectory();
    const runtimePath = getAgentRuntimeFilePath(userDataPath);
    await writeAgentRuntimeDescriptor({ userDataPath, descriptor: createDescriptor() });
    await expect(
      cleanupAgentRuntimeDescriptor({ userDataPath, runtimeId: "different-runtime" }),
    ).resolves.toBe(false);

    await writeFile(runtimePath, "not-json", "utf8");
    await expect(
      cleanupAgentRuntimeDescriptor({ userDataPath, runtimeId: "runtime-1234" }),
    ).resolves.toBe(false);
    await expect(readFile(runtimePath, "utf8")).resolves.toBe("not-json");
  });

  it("removes a legacy v2 descriptor before publishing a new runtime", async () => {
    const userDataPath = await createTemporaryDirectory();
    const runtimePath = getAgentRuntimeFilePath(userDataPath);
    await writeAgentRuntimeDescriptor({ userDataPath, descriptor: createDescriptor() });
    await writeFile(
      runtimePath,
      JSON.stringify({
        protocol_version: "2",
        desktop_pid: 1234,
        access_token: "legacy-token",
      }),
      "utf8",
    );

    await clearAgentRuntimeDescriptor(userDataPath);

    await expect(readFile(runtimePath, "utf8")).rejects.toMatchObject({ code: "ENOENT" });
  });
});
