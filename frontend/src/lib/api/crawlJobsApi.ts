import { apiFetch } from '@/lib/api/client';
import type {
  CrawlCandidateDTO,
  CrawlCandidateUpdatePayloadDTO,
  CrawlJobEnrichResultDTO,
  CrawlJobApproveResultDTO,
  CrawlJobCreatePayloadDTO,
  CrawlJobDetailsDTO,
  CrawlJobDTO,
  CrawlJobEventDTO,
  CrawlJobRetryPayloadDTO,
  CrawlJobSummaryDTO,
  CrawlPageDTO,
  TaskListView,
} from '@/types';

export const createCrawlJob = (payload: CrawlJobCreatePayloadDTO) =>
  apiFetch<CrawlJobDTO>('/api/crawl-jobs', {
    method: 'POST',
    body: JSON.stringify(payload),
  });

export const listCrawlJobs = (params: { limit?: number; view?: TaskListView } = {}) =>
  apiFetch<CrawlJobSummaryDTO[]>('/api/crawl-jobs', undefined, {
    limit: params.limit,
    view: params.view,
  });

export const getCrawlJob = (jobId: number) =>
  apiFetch<CrawlJobSummaryDTO>(`/api/crawl-jobs/${jobId}`);

export const getCrawlJobDetails = (jobId: number) =>
  apiFetch<CrawlJobDetailsDTO>(`/api/crawl-jobs/${jobId}/details`);

export const listCrawlCandidates = (jobId: number) =>
  apiFetch<CrawlCandidateDTO[]>(`/api/crawl-jobs/${jobId}/candidates`);

export const listCrawlPages = (jobId: number) =>
  apiFetch<CrawlPageDTO[]>(`/api/crawl-jobs/${jobId}/pages`);

export const getCrawlJobEvents = (jobId: number) =>
  apiFetch<CrawlJobEventDTO[]>(`/api/crawl-jobs/${jobId}/events`);

export const updateCrawlCandidate = (
  candidateId: number,
  payload: CrawlCandidateUpdatePayloadDTO,
) =>
  apiFetch<CrawlCandidateDTO>(`/api/crawl-jobs/candidates/${candidateId}`, {
    method: 'PATCH',
    body: JSON.stringify(payload),
  });

export const approveCrawlCandidates = (jobId: number, candidateIds: number[]) =>
  apiFetch<CrawlJobApproveResultDTO>(`/api/crawl-jobs/${jobId}/approve`, {
    method: 'POST',
    body: JSON.stringify({ candidate_ids: candidateIds }),
  });

export const enrichCrawlCandidates = (
  jobId: number,
  candidateIds: number[],
  llmProfileId?: number | null,
) =>
  apiFetch<CrawlJobEnrichResultDTO>(`/api/crawl-jobs/${jobId}/enrich`, {
    method: 'POST',
    body: JSON.stringify({
      candidate_ids: candidateIds,
      llm_profile_id: llmProfileId ?? undefined,
    }),
  });

export const cancelCrawlJob = (jobId: number) =>
  apiFetch<CrawlJobDTO>(`/api/crawl-jobs/${jobId}/cancel`, {
    method: 'POST',
  });

export const pauseCrawlJob = (jobId: number) =>
  apiFetch<CrawlJobDTO>(`/api/crawl-jobs/${jobId}/pause`, {
    method: 'POST',
  });

export const resumeCrawlJob = (jobId: number, llmProfileId?: number | null) =>
  apiFetch<CrawlJobDTO>(`/api/crawl-jobs/${jobId}/resume`, {
    method: 'POST',
    body: JSON.stringify({ llm_profile_id: llmProfileId ?? undefined }),
  });

export const retryCrawlJob = (jobId: number, payload: CrawlJobRetryPayloadDTO) =>
  apiFetch<CrawlJobDTO>(`/api/crawl-jobs/${jobId}/retry`, {
    method: 'POST',
    body: JSON.stringify({
      clear_existing_data: payload.clear_existing_data,
      llm_profile_id: payload.llmProfileId ?? undefined,
    }),
  });

export const resumeCrawlJobReview = (jobId: number) =>
  apiFetch<CrawlJobDTO>(`/api/crawl-jobs/${jobId}/resume-review`, {
    method: 'POST',
  });

export const deleteCrawlJob = (jobId: number) =>
  apiFetch<CrawlJobDTO>(`/api/crawl-jobs/${jobId}/delete`, {
    method: 'POST',
  });

export const restoreCrawlJob = (jobId: number) =>
  apiFetch<CrawlJobDTO>(`/api/crawl-jobs/${jobId}/restore`, {
    method: 'POST',
  });
