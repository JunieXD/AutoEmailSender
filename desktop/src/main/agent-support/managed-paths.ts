import { randomUUID } from "node:crypto";
import { lstat, rename, rm } from "node:fs/promises";

export type ManagedPathChange = {
  commit: () => Promise<void>;
  rollback: () => Promise<void>;
};

export async function replaceManagedPath(
  temporaryPath: string,
  targetPath: string,
): Promise<ManagedPathChange> {
  const backupPath = `${targetPath}.${randomUUID()}.rollback`;
  const hadPrevious = await pathExists(targetPath);
  if (hadPrevious) {
    await rename(targetPath, backupPath);
  }
  try {
    await rename(temporaryPath, targetPath);
  } catch (error) {
    if (hadPrevious) {
      await rename(backupPath, targetPath).catch(() => undefined);
    }
    throw error;
  }
  let settled = false;
  return {
    commit: async () => {
      if (settled) return;
      settled = true;
      if (hadPrevious) {
        await rm(backupPath, { recursive: true, force: true });
      }
    },
    rollback: async () => {
      if (settled) return;
      settled = true;
      await rm(targetPath, { recursive: true, force: true });
      if (hadPrevious) {
        await rename(backupPath, targetPath);
      }
    },
  };
}

export async function stageManagedPathRemoval(
  targetPath: string,
): Promise<ManagedPathChange> {
  const backupPath = `${targetPath}.${randomUUID()}.rollback`;
  await rename(targetPath, backupPath);
  let settled = false;
  return {
    commit: async () => {
      if (settled) return;
      settled = true;
      await rm(backupPath, { recursive: true, force: true });
    },
    rollback: async () => {
      if (settled) return;
      settled = true;
      await rename(backupPath, targetPath);
    },
  };
}

export async function commitManagedPathChanges(
  changes: ManagedPathChange[],
): Promise<void> {
  for (const change of changes) {
    await change.commit();
  }
}

export async function rollbackManagedPathChanges(
  changes: ManagedPathChange[],
  originalError: unknown,
): Promise<never> {
  const rollbackErrors: unknown[] = [];
  for (const change of [...changes].reverse()) {
    try {
      await change.rollback();
    } catch (error) {
      rollbackErrors.push(error);
    }
  }
  if (rollbackErrors.length > 0) {
    throw new AggregateError(
      [originalError, ...rollbackErrors],
      "Agent 支持安装失败，且部分文件无法回滚。",
    );
  }
  throw originalError;
}

export async function pathExists(targetPath: string): Promise<boolean> {
  try {
    await lstat(targetPath);
    return true;
  } catch {
    return false;
  }
}
