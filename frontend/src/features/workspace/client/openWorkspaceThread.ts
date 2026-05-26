import { ensureWorkspaceTask, getWorkspaceThread } from "@/lib/api/workspacesApi";
import type { WorkspaceTaskSummaryDTO, WorkspaceThreadDTO } from "@/types";

export const shouldBootstrapWorkspaceTask = (
  task: WorkspaceTaskSummaryDTO | null | undefined,
) =>
  Boolean(
    task?.id == null ||
      (task.status === "canceled" &&
        task.cancellation_reason === "schedule_expired") ||
      (task.source === "batch" && task.batch_task_id != null),
  );

export const bootstrapWorkspaceThread = async (
  thread: WorkspaceThreadDTO,
  professorId: number,
  identityId: number,
  llmProfileId: number,
) =>
  shouldBootstrapWorkspaceTask(thread.current_task)
    ? ensureWorkspaceTask(professorId, identityId, llmProfileId)
    : thread;

export const openWorkspaceThread = async (
  professorId: number,
  identityId: number,
  llmProfileId: number,
) =>
  bootstrapWorkspaceThread(
    await getWorkspaceThread(professorId, identityId, llmProfileId),
    professorId,
    identityId,
    llmProfileId,
  );
