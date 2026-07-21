import { apiFetch } from '@/lib/api/client';
import type {
  IdentityCommunicationGroupDTO,
  IdentityCommunicationGroupPayload,
} from '@/types';

export const listCommunicationGroups = () =>
  apiFetch<IdentityCommunicationGroupDTO[]>('/api/communication-groups');

export const createCommunicationGroup = (
  payload: IdentityCommunicationGroupPayload,
) =>
  apiFetch<IdentityCommunicationGroupDTO>('/api/communication-groups', {
    method: 'POST',
    body: JSON.stringify(payload),
  });

export const updateCommunicationGroup = (
  groupId: number,
  payload: IdentityCommunicationGroupPayload,
) =>
  apiFetch<IdentityCommunicationGroupDTO>(`/api/communication-groups/${groupId}`, {
    method: 'PUT',
    body: JSON.stringify(payload),
  });

export const deleteCommunicationGroup = (groupId: number) =>
  apiFetch<void>(`/api/communication-groups/${groupId}`, {
    method: 'DELETE',
  });
