import { randomUUID } from "node:crypto";
import { chmod, mkdir, readFile, rename, rm, writeFile } from "node:fs/promises";
import path from "node:path";

export const AGENT_RUNTIME_PROTOCOL_VERSION = "3";

type AgentRuntimeProcess = {
  pid: number;
  started_at: string;
};

export type AgentRuntimeDescriptor = {
  protocol_version: string;
  app_version: string;
  runtime_id: string;
  base_url: string;
  access_token: string;
  desktop: AgentRuntimeProcess;
  backend: AgentRuntimeProcess;
  published_at: string;
};

export function getAgentRuntimeFilePath(userDataPath: string): string {
  return path.join(userDataPath, "agent", "runtime.json");
}

export async function writeAgentRuntimeDescriptor(options: {
  userDataPath: string;
  descriptor: AgentRuntimeDescriptor;
}): Promise<string> {
  const runtimePath = getAgentRuntimeFilePath(options.userDataPath);
  const runtimeDirectory = path.dirname(runtimePath);

  await mkdir(runtimeDirectory, { recursive: true, mode: 0o700 });
  await setPrivatePermissions(runtimeDirectory, 0o700);
  await retryRuntimeFileOperation(async () => {
    const temporaryPath = path.join(
      runtimeDirectory,
      `.runtime-${options.descriptor.runtime_id}-${randomUUID()}.tmp`,
    );
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
  });
  return runtimePath;
}

export async function cleanupAgentRuntimeDescriptor(options: {
  userDataPath: string;
  runtimeId: string;
}): Promise<boolean> {
  const runtimePath = getAgentRuntimeFilePath(options.userDataPath);
  let current: AgentRuntimeDescriptor;
  try {
    current = JSON.parse(await readFile(runtimePath, "utf8")) as AgentRuntimeDescriptor;
  } catch {
    return false;
  }

  if (current.runtime_id !== options.runtimeId) {
    return false;
  }

  try {
    await rm(runtimePath, { force: true });
    return true;
  } catch {
    return false;
  }
}

export async function clearAgentRuntimeDescriptor(userDataPath: string): Promise<void> {
  await retryRuntimeFileOperation(
    () => rm(getAgentRuntimeFilePath(userDataPath), { force: true }),
  );
}

async function retryRuntimeFileOperation<T>(operation: () => Promise<T>): Promise<T> {
  let lastError: unknown;
  for (let attempt = 0; attempt < 5; attempt += 1) {
    try {
      return await operation();
    } catch (error) {
      lastError = error;
      if (!isRetryableRuntimeFileError(error) || attempt === 4) {
        throw error;
      }
      await new Promise((resolve) => setTimeout(resolve, 25 * (2 ** attempt)));
    }
  }
  throw lastError;
}

function isRetryableRuntimeFileError(error: unknown): boolean {
  const code = (error as NodeJS.ErrnoException | undefined)?.code;
  return code === "EACCES" || code === "EBUSY" || code === "EPERM";
}

async function setPrivatePermissions(targetPath: string, mode: number): Promise<void> {
  if (process.platform === "win32") {
    return;
  }
  await chmod(targetPath, mode);
}
