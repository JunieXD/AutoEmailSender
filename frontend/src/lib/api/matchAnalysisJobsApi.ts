import { apiFetch } from '@/lib/api/client';
import type {
  CreateMatchAnalysisJobRequestDTO,
  MatchAnalysisJobDTO,
  MatchAnalysisJobItemsPageDTO,
  TaskListView,
} from '@/types';

export const listMatchAnalysisJobs = (params?: {
  identityId?: number | null;
  llmProfileId?: number | null;
  view?: TaskListView;
}) =>
  apiFetch<MatchAnalysisJobDTO[]>(
    '/api/match-analysis-jobs',
    undefined,
    {
      identity_id: params?.identityId ?? undefined,
      llm_profile_id: params?.llmProfileId ?? undefined,
      view: params?.view ?? undefined,
    },
  );

export const createMatchAnalysisJob = (
  payload: CreateMatchAnalysisJobRequestDTO,
) =>
  apiFetch<MatchAnalysisJobDTO>('/api/match-analysis-jobs', {
    method: 'POST',
    body: JSON.stringify(payload),
  });

export const listMatchAnalysisJobItems = (
  jobId: number,
  params?: {
    cursor?: number;
    limit?: number;
    status?: string | null;
  },
) =>
  apiFetch<MatchAnalysisJobItemsPageDTO>(
    `/api/match-analysis-jobs/${jobId}/items`,
    undefined,
    {
      cursor: params?.cursor ?? 0,
      limit: params?.limit ?? 20,
      status: params?.status ?? undefined,
    },
  );

export const getMatchAnalysisJob = (jobId: number) =>
  apiFetch<MatchAnalysisJobDTO>(`/api/match-analysis-jobs/${jobId}`);

export const cancelMatchAnalysisJob = (jobId: number) =>
  apiFetch<{ ok: boolean; job: MatchAnalysisJobDTO }>(
    `/api/match-analysis-jobs/${jobId}/cancel`,
    {
      method: 'POST',
    },
  );

export const retryFailedMatchAnalysisJob = (jobId: number) =>
  apiFetch<MatchAnalysisJobDTO>(
    `/api/match-analysis-jobs/${jobId}/retry-failed`,
    {
      method: 'POST',
    },
  );

export const deleteMatchAnalysisJob = (jobId: number) =>
  apiFetch<{ ok: boolean; job: MatchAnalysisJobDTO }>(
    `/api/match-analysis-jobs/${jobId}/delete`,
    {
      method: 'POST',
    },
  );

export const restoreMatchAnalysisJob = (jobId: number) =>
  apiFetch<{ ok: boolean; job: MatchAnalysisJobDTO }>(
    `/api/match-analysis-jobs/${jobId}/restore`,
    {
      method: 'POST',
    },
  );
