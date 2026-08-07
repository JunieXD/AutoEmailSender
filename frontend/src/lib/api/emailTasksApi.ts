import { apiFetch } from '@/lib/api/client';
import type {
  EmailTaskApprovalPayloadDTO,
  EmailTaskOutreachConfigPayloadDTO,
  EmailTaskRewriteDraftPayloadDTO,
  EmailTaskSchedulePayloadDTO,
  MatchCalculationResultDTO,
  WorkspaceThreadDTO,
} from '@/types';

const buildRuntimeProfileBody = (llmProfileId?: number | null) =>
  JSON.stringify({ llm_profile_id: llmProfileId ?? null });

export const getEmailTaskThread = (taskId: number) =>
  apiFetch<WorkspaceThreadDTO>(`/api/email-tasks/${taskId}/thread`);

export const regenerateDraft = (taskId: number, llmProfileId?: number | null) =>
  apiFetch<WorkspaceThreadDTO>(`/api/email-tasks/${taskId}/regenerate-draft`, {
    method: 'POST',
    body: buildRuntimeProfileBody(llmProfileId),
  });

export const calculateMatch = (taskId: number, llmProfileId?: number | null) =>
  apiFetch<MatchCalculationResultDTO>(`/api/email-tasks/${taskId}/calculate-match`, {
    method: 'POST',
    body: buildRuntimeProfileBody(llmProfileId),
  });

export const generateDraft = (taskId: number, llmProfileId?: number | null) =>
  apiFetch<WorkspaceThreadDTO>(`/api/email-tasks/${taskId}/generate-draft`, {
    method: 'POST',
    body: buildRuntimeProfileBody(llmProfileId),
  });

export const rewriteDraft = (taskId: number, payload: EmailTaskRewriteDraftPayloadDTO) =>
  apiFetch<WorkspaceThreadDTO>(`/api/email-tasks/${taskId}/rewrite-draft`, {
    method: 'POST',
    body: JSON.stringify(payload),
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
