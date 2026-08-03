import { randomUUID } from "node:crypto";
import { chmod, mkdir, readFile, rename, rm, writeFile } from "node:fs/promises";
import path from "node:path";

export const AGENT_RUNTIME_PROTOCOL_VERSION = "1";

export type AgentRuntimeDescriptor = {
  protocol_version: string;
  app_version: string;
  base_url: string;
  access_token: string;
  desktop_pid: number;
  started_at: string;
};

export function getAgentRuntimeFilePath(userDataPath: string): string {
  return path.join(userDataPath, "agent", "runtime.json");
}

export function isAgentBackgroundLaunch(argv: readonly string[]): boolean {
  return argv.includes("--agent-background");
}

export async function writeAgentRuntimeDescriptor(options: {
  userDataPath: string;
  descriptor: AgentRuntimeDescriptor;
}): Promise<string> {
  const runtimePath = getAgentRuntimeFilePath(options.userDataPath);
  const runtimeDirectory = path.dirname(runtimePath);
  const temporaryPath = path.join(
    runtimeDirectory,
    `.runtime-${options.descriptor.desktop_pid}-${randomUUID()}.tmp`,
  );

  await mkdir(runtimeDirectory, { recursive: true, mode: 0o700 });
  await setPrivatePermissions(runtimeDirectory, 0o700);
  try {
    await writeFile(temporaryPath, `${JSON.stringify(options.descriptor, null, 2)}\n`, {
      encoding: "utf8",
      mode: 0o600,
      flag: "wx",
    });
    await setPrivatePermissions(temporaryPath, 0o600);
    await rename(temporaryPath, runtimePath);
    await setPrivatePermissions(runtimePath, 0o600);
  } catch (error) {
    await rm(temporaryPath, { force: true }).catch(() => undefined);
    throw error;
  }
  return runtimePath;
}

export async function cleanupAgentRuntimeDescriptor(options: {
  userDataPath: string;
  desktopPid: number;
  accessToken: string;
}): Promise<boolean> {
  const runtimePath = getAgentRuntimeFilePath(options.userDataPath);
  let current: AgentRuntimeDescriptor;
  try {
    current = JSON.parse(await readFile(runtimePath, "utf8")) as AgentRuntimeDescriptor;
  } catch {
    return false;
  }

  if (
    current.desktop_pid !== options.desktopPid ||
    current.access_token !== options.accessToken
  ) {
    return false;
  }

  try {
    await rm(runtimePath, { force: true });
    return true;
  } catch {
    return false;
  }
}

async function setPrivatePermissions(targetPath: string, mode: number): Promise<void> {
  if (process.platform === "win32") {
    return;
  }
  await chmod(targetPath, mode);
}
