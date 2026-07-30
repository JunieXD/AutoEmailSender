import { apiFetch } from '@/lib/api/client';
import type {
  ConnectionTestResultDTO,
  IdentityDTO,
  IdentityPayload,
  IdentityTemplateImportResultDTO,
} from '@/types';

export const listIdentities = () => apiFetch<IdentityDTO[]>('/api/identities');

export const createIdentity = (payload: IdentityPayload) =>
  apiFetch<IdentityDTO>('/api/identities', {
    method: 'POST',
    body: JSON.stringify(payload),
  });

export const updateIdentity = (identityId: number, payload: IdentityPayload) =>
  apiFetch<IdentityDTO>(`/api/identities/${identityId}`, {
    method: 'PUT',
    body: JSON.stringify(payload),
  });

export const updateIdentityDefaultOutreachTemplate = (
  identityId: number,
  templateId: number | null,
) =>
  apiFetch<IdentityDTO>(`/api/identities/${identityId}/default-template`, {
    method: 'PUT',
    body: JSON.stringify({ template_id: templateId }),
  });

export const deleteIdentity = (identityId: number) =>
  apiFetch<void>(`/api/identities/${identityId}`, {
    method: 'DELETE',
  });

export const setDefaultIdentity = (identityId: number) =>
  apiFetch<IdentityDTO>(`/api/identities/${identityId}/default`, {
    method: 'POST',
  });

export const testIdentitySmtp = (identityId: number) =>
  apiFetch<ConnectionTestResultDTO>(`/api/identities/${identityId}/smtp-test`, {
    method: 'POST',
  });

export const testIdentityImap = (identityId: number) =>
  apiFetch<ConnectionTestResultDTO>(`/api/identities/${identityId}/imap-test`, {
    method: 'POST',
  });

export const importIdentityTemplate = async (file: File) => {
  const formData = new FormData();
  formData.append('file', file);
  return apiFetch<IdentityTemplateImportResultDTO>('/api/identities/template-import', {
    method: 'POST',
    body: formData,
  });
};
