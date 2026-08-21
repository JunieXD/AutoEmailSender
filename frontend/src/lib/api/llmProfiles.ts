import { apiFetch } from '@/lib/api/client';
import type {
  LLMProfileDTO,
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

export const deleteLLMProfile = (profileId: number) =>
  apiFetch<void>(`/api/llm-profiles/${profileId}`, {
    method: 'DELETE',
  });

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
