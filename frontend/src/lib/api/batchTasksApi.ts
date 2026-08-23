import { apiFetch } from '@/lib/api/client';
import type {
  BatchTaskBulkApproveDraftsResultDTO,
  BatchTaskCardDTO,
  BatchTaskItemDTO,
  BatchTaskResendContextDTO,
  CreateBatchTaskRequestDTO,
  EmailTaskApprovalPayloadDTO,
  EmailTaskOutreachConfigPayloadDTO,
  EmailTaskRewriteDraftPayloadDTO,
  TaskListView,
  WorkspaceThreadDTO,
} from '@/types';

export const listBatchTasks = (params?: {
  identityId?: number | null;
  llmProfileId?: number | null;
  view?: TaskListView;
}) =>
  apiFetch<BatchTaskCardDTO[]>(
    '/api/batch-tasks',
    undefined,
    {
      identity_id: params?.identityId ?? undefined,
      llm_profile_id: params?.llmProfileId ?? undefined,
      view: params?.view ?? undefined,
    },
  );

export const createBatchTask = (payload: CreateBatchTaskRequestDTO) =>
  apiFetch<BatchTaskCardDTO>('/api/batch-tasks', {
    method: 'POST',
    body: JSON.stringify(payload),
  });

export const getBatchTaskSummary = (taskId: number) =>
  apiFetch<BatchTaskCardDTO>(`/api/batch-tasks/${taskId}/summary`);

export const listBatchTaskItems = (taskId: number) =>
  apiFetch<BatchTaskItemDTO[]>(`/api/batch-tasks/${taskId}/items`);

export const getBatchTaskResendContext = (taskId: number) =>
  apiFetch<BatchTaskResendContextDTO>(`/api/batch-tasks/${taskId}/resend-context`);

export const getBatchTaskItemThread = (taskId: number, itemId: number) =>
  apiFetch<WorkspaceThreadDTO>(`/api/batch-tasks/${taskId}/items/${itemId}/thread`);

export const approveAllBatchTaskDrafts = (taskId: number, itemIds: number[]) =>
  apiFetch<BatchTaskBulkApproveDraftsResultDTO>(
    `/api/batch-tasks/${taskId}/approve-all-drafts`,
    {
      method: 'POST',
      body: JSON.stringify({ item_ids: itemIds }),
    },
  );

export const regenerateBatchTaskItemDraft = (taskId: number, itemId: number) =>
  apiFetch<WorkspaceThreadDTO>(
    `/api/batch-tasks/${taskId}/items/${itemId}/regenerate-draft`,
    {
      method: 'POST',
    },
  );

export const rewriteBatchTaskItemDraft = (
  taskId: number,
  itemId: number,
  payload: EmailTaskRewriteDraftPayloadDTO,
) =>
  apiFetch<WorkspaceThreadDTO>(
    `/api/batch-tasks/${taskId}/items/${itemId}/rewrite-draft`,
    {
      method: 'POST',
      body: JSON.stringify(payload),
    },
  );

export const updateBatchTaskItemOutreachConfig = (
  taskId: number,
  itemId: number,
  payload: EmailTaskOutreachConfigPayloadDTO,
) =>
  apiFetch<WorkspaceThreadDTO>(
    `/api/batch-tasks/${taskId}/items/${itemId}/outreach-config`,
    {
      method: 'POST',
      body: JSON.stringify(payload),
    },
  );

export const approveBatchTaskItemDraft = (
  taskId: number,
  itemId: number,
  payload: EmailTaskApprovalPayloadDTO,
) =>
  apiFetch<WorkspaceThreadDTO>(`/api/batch-tasks/${taskId}/items/${itemId}/approve`, {
    method: 'POST',
    body: JSON.stringify(payload),
  });

export const approveAndSendBatchTaskItemDraft = (
  taskId: number,
  itemId: number,
  payload: EmailTaskApprovalPayloadDTO,
) =>
  apiFetch<WorkspaceThreadDTO>(
    `/api/batch-tasks/${taskId}/items/${itemId}/approve-and-send`,
    {
      method: 'POST',
      body: JSON.stringify(payload),
    },
  );

export const deleteBatchTaskItem = (taskId: number, itemId: number) =>
  apiFetch<{ ok: boolean; task: BatchTaskCardDTO }>(
    `/api/batch-tasks/${taskId}/items/${itemId}/delete`,
    {
      method: 'POST',
    },
  );

export const retryBatchTaskItemDraft = (taskId: number, itemId: number) =>
  apiFetch<{ ok: boolean; task: BatchTaskCardDTO }>(
    `/api/batch-tasks/${taskId}/items/${itemId}/retry-draft`,
    {
      method: 'POST',
    },
  );

export const cancelBatchTaskItemSend = (taskId: number, itemId: number) =>
  apiFetch<{ ok: boolean; task: BatchTaskCardDTO }>(
    `/api/batch-tasks/${taskId}/items/${itemId}/cancel-send`,
    {
      method: 'POST',
    },
  );

export const restoreBatchTaskItemSend = (taskId: number, itemId: number) =>
  apiFetch<{ ok: boolean; task: BatchTaskCardDTO }>(
    `/api/batch-tasks/${taskId}/items/${itemId}/restore-send`,
    {
      method: 'POST',
    },
  );

export const pauseBatchTask = (taskId: number) =>
  apiFetch<{ ok: boolean; task: BatchTaskCardDTO }>(`/api/batch-tasks/${taskId}/pause`, {
    method: 'POST',
  });

export const resumeBatchTask = (
  taskId: number,
  replacementLlmProfileId?: number | null,
) =>
  apiFetch<{ ok: boolean; task: BatchTaskCardDTO }>(
    `/api/batch-tasks/${taskId}/resume`,
    { method: 'POST' },
    { replacement_llm_profile_id: replacementLlmProfileId },
  );

export const stopBatchTask = (taskId: number) =>
  apiFetch<{ ok: boolean; task: BatchTaskCardDTO }>(`/api/batch-tasks/${taskId}/stop`, {
    method: 'POST',
  });

export const deleteBatchTask = (taskId: number) =>
  apiFetch<{ ok: boolean; task: BatchTaskCardDTO }>(`/api/batch-tasks/${taskId}/delete`, {
    method: 'POST',
  });

export const restoreBatchTask = (taskId: number) =>
  apiFetch<{ ok: boolean; task: BatchTaskCardDTO }>(`/api/batch-tasks/${taskId}/restore`, {
    method: 'POST',
  });
