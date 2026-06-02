import { apiFetch } from '@/lib/api/client';
import type {
  EmailTaskApprovalPayloadDTO,
  EmailTaskOutreachConfigPayloadDTO,
  EmailTaskSchedulePayloadDTO,
  MatchCalculationResultDTO,
  WorkspaceThreadDTO,
} from '@/types';

export const regenerateDraft = (taskId: number) =>
  apiFetch<WorkspaceThreadDTO>(`/api/email-tasks/${taskId}/regenerate-draft`, {
    method: 'POST',
  });

export const calculateMatch = (taskId: number) =>
  apiFetch<MatchCalculationResultDTO>(`/api/email-tasks/${taskId}/calculate-match`, {
    method: 'POST',
  });

export const generateDraft = (taskId: number) =>
  apiFetch<WorkspaceThreadDTO>(`/api/email-tasks/${taskId}/generate-draft`, {
    method: 'POST',
  });

export const approveDraft = (taskId: number, payload: EmailTaskApprovalPayloadDTO) =>
  apiFetch<WorkspaceThreadDTO>(`/api/email-tasks/${taskId}/approve`, {
    method: 'POST',
    body: JSON.stringify(payload),
  });

export const saveDraft = (taskId: number, payload: EmailTaskApprovalPayloadDTO) =>
  apiFetch<WorkspaceThreadDTO>(`/api/email-tasks/${taskId}/save-draft`, {
    method: 'POST',
    body: JSON.stringify(payload),
  });

export const approveAndSend = (taskId: number, payload: EmailTaskApprovalPayloadDTO) =>
  apiFetch<WorkspaceThreadDTO>(`/api/email-tasks/${taskId}/approve-and-send`, {
    method: 'POST',
    body: JSON.stringify(payload),
  });

export const approveAndSchedule = (taskId: number, payload: EmailTaskSchedulePayloadDTO) =>
  apiFetch<WorkspaceThreadDTO>(`/api/email-tasks/${taskId}/approve-and-schedule`, {
    method: 'POST',
    body: JSON.stringify(payload),
  });

export const cancelScheduledTask = (taskId: number) =>
  apiFetch<WorkspaceThreadDTO>(`/api/email-tasks/${taskId}/cancel-schedule`, {
    method: 'POST',
  });

export const continueManually = (taskId: number) =>
  apiFetch<WorkspaceThreadDTO>(`/api/email-tasks/${taskId}/continue-manually`, {
    method: 'POST',
  });

export const startFollowUp = (taskId: number) =>
  apiFetch<WorkspaceThreadDTO>(`/api/email-tasks/${taskId}/start-follow-up`, {
    method: 'POST',
  });

export const updateTaskPrimaryMaterial = (taskId: number, primaryMaterialId: number) =>
  apiFetch<WorkspaceThreadDTO>(`/api/email-tasks/${taskId}/primary-material`, {
    method: 'POST',
    body: JSON.stringify({ primary_material_id: primaryMaterialId }),
  });

export const updateTaskOutreachConfig = (
  taskId: number,
  payload: EmailTaskOutreachConfigPayloadDTO,
) =>
  apiFetch<WorkspaceThreadDTO>(`/api/email-tasks/${taskId}/outreach-config`, {
    method: 'POST',
    body: JSON.stringify(payload),
  });
