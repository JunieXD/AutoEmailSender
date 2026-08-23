import { apiFetch } from '@/lib/api/client';
import type {
  LLMProfileDTO,
  LLMProfileDeletionImpactDTO,
  LLMProfileDeletionResultDTO,
  LLMProfileModelsResultDTO,
  LLMProfilePayload,
  LLMProfileTestResultDTO,
} from '@/types';

export const listLLMProfiles = () => apiFetch<LLMProfileDTO[]>('/api/llm-profiles');

export const createLLMProfile = (payload: LLMProfilePayload) =>
  apiFetch<LLMProfileDTO>('/api/llm-profiles', {
    method: 'POST',
    body: JSON.stringify(payload),
  });

export const updateLLMProfile = (profileId: number, payload: LLMProfilePayload) =>
  apiFetch<LLMProfileDTO>(`/api/llm-profiles/${profileId}`, {
    method: 'PUT',
    body: JSON.stringify(payload),
  });

export const getLLMProfileDeletionImpact = (profileId: number) =>
  apiFetch<LLMProfileDeletionImpactDTO>(
    `/api/llm-profiles/${profileId}/deletion-impact`,
  );

export const deleteLLMProfile = async (
  profileId: number,
  impactRevision: string,
  replacementDefaultProfileId?: number | null,
) => {
  const result = await apiFetch<LLMProfileDeletionResultDTO>(
    `/api/llm-profiles/${profileId}`,
    { method: 'DELETE' },
    {
      impact_revision: impactRevision,
      replacement_default_profile_id: replacementDefaultProfileId,
    },
  );
  broadcastLLMProfileRetired(profileId);
  return result;
};

const broadcastLLMProfileRetired = (profileId: number) => {
  const detail = { profileId, retiredAt: Date.now() };
  window.dispatchEvent(new CustomEvent('llm-profile-retired', { detail }));
  window.localStorage.setItem('llm_profile_retired_event', JSON.stringify(detail));
};

export const setDefaultLLMProfile = (profileId: number) =>
  apiFetch<LLMProfileDTO>(`/api/llm-profiles/${profileId}/default`, {
    method: 'POST',
  });

export const fetchLLMProfileModelsPreview = (payload: LLMProfilePayload) =>
  apiFetch<LLMProfileModelsResultDTO>('/api/llm-profiles/preview/models', {
    method: 'POST',
    body: JSON.stringify(payload),
  });

export const testLLMProfilePreview = (payload: LLMProfilePayload) =>
  apiFetch<LLMProfileTestResultDTO>('/api/llm-profiles/preview/test', {
    method: 'POST',
    body: JSON.stringify(payload),
  });
