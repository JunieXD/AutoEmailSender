import { apiFetch } from '@/lib/api/client';
import type {
  BatchTaskCardDTO,
  BatchTaskItemDTO,
  CreateBatchTaskRequestDTO,
  TaskListView,
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

export const listBatchTaskItems = (taskId: number) =>
  apiFetch<BatchTaskItemDTO[]>(`/api/batch-tasks/${taskId}/items`);

export const deleteBatchTaskItem = (taskId: number, itemId: number) =>
  apiFetch<{ ok: boolean; task: BatchTaskCardDTO }>(
    `/api/batch-tasks/${taskId}/items/${itemId}/delete`,
    {
      method: 'POST',
    },
  );

export const pauseBatchTask = (taskId: number) =>
  apiFetch<{ ok: boolean; task: BatchTaskCardDTO }>(`/api/batch-tasks/${taskId}/pause`, {
    method: 'POST',
  });

export const resumeBatchTask = (taskId: number) =>
  apiFetch<{ ok: boolean; task: BatchTaskCardDTO }>(`/api/batch-tasks/${taskId}/resume`, {
    method: 'POST',
  });

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
