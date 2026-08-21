import { apiFetch } from '@/lib/api/client';
import type { OutreachTemplateDTO, OutreachTemplatePayloadDTO } from '@/types';

export const listOutreachTemplates = (includeArchived = false) =>
  apiFetch<OutreachTemplateDTO[]>(
    `/api/outreach-templates${includeArchived ? '?include_archived=true' : ''}`,
  );

export const getOutreachTemplate = (templateId: number) =>
  apiFetch<OutreachTemplateDTO>(`/api/outreach-templates/${templateId}`);

export const createOutreachTemplate = (payload: OutreachTemplatePayloadDTO) =>
  apiFetch<OutreachTemplateDTO>('/api/outreach-templates', {
    method: 'POST',
    body: JSON.stringify(payload),
  });

export const updateOutreachTemplate = (
  templateId: number,
  payload: Partial<OutreachTemplatePayloadDTO>,
) =>
  apiFetch<OutreachTemplateDTO>(`/api/outreach-templates/${templateId}`, {
    method: 'PUT',
    body: JSON.stringify(payload),
  });

export const duplicateOutreachTemplate = (templateId: number) =>
  apiFetch<OutreachTemplateDTO>(`/api/outreach-templates/${templateId}/duplicate`, {
    method: 'POST',
  });

export const setGlobalDefaultOutreachTemplate = (templateId: number) =>
  apiFetch<OutreachTemplateDTO>(`/api/outreach-templates/${templateId}/default`, {
    method: 'POST',
  });

export const archiveOutreachTemplate = (templateId: number) =>
  apiFetch<OutreachTemplateDTO>(`/api/outreach-templates/${templateId}`, {
    method: 'DELETE',
  });
