import { apiFetch } from '@/lib/api/client';
import type {
  ProfessorInformationEnrichmentActiveDTO,
  ProfessorInformationEnrichmentItemDTO,
  ProfessorInformationEnrichmentItemsPageDTO,
  ProfessorInformationEnrichmentJobDTO,
  ProfessorInformationEnrichmentItemStatus,
  ProfessorInformationEnrichmentListView,
} from '../model/types';


export const createSingleProfessorInformationEnrichment = (
  professorId: number,
  llmProfileId: number,
) =>
  apiFetch<ProfessorInformationEnrichmentJobDTO>(
    `/api/professors/${professorId}/information-enrichment`,
    {
      method: 'POST',
      body: JSON.stringify({ llm_profile_id: llmProfileId }),
    },
  );

export const getActiveProfessorInformationEnrichment = (professorId: number) =>
  apiFetch<ProfessorInformationEnrichmentActiveDTO>(
    `/api/professors/${professorId}/information-enrichment/active`,
  );

export const createProfessorInformationEnrichmentJob = (payload: {
  professorIds: number[];
  llmProfileId: number;
  name?: string | null;
}) =>
  apiFetch<ProfessorInformationEnrichmentJobDTO>(
    '/api/professor-information-enrichment-jobs',
    {
      method: 'POST',
      body: JSON.stringify({
        professor_ids: payload.professorIds,
        llm_profile_id: payload.llmProfileId,
        name: payload.name ?? undefined,
      }),
    },
  );

export const listProfessorInformationEnrichmentJobs = (params?: {
  view?: ProfessorInformationEnrichmentListView;
}) =>
  apiFetch<ProfessorInformationEnrichmentJobDTO[]>(
    '/api/professor-information-enrichment-jobs',
    undefined,
    { view: params?.view ?? undefined },
  );

export const getProfessorInformationEnrichmentJob = (jobId: number) =>
  apiFetch<ProfessorInformationEnrichmentJobDTO>(
    `/api/professor-information-enrichment-jobs/${jobId}`,
  );

export const listProfessorInformationEnrichmentItems = (jobId: number) =>
  apiFetch<ProfessorInformationEnrichmentItemDTO[]>(
    `/api/professor-information-enrichment-jobs/${jobId}/items`,
  );

export const listProfessorInformationEnrichmentItemsPage = (
  jobId: number,
  params?: {
    cursor?: number;
    limit?: number;
    status?: ProfessorInformationEnrichmentItemStatus | null;
  },
) =>
  apiFetch<ProfessorInformationEnrichmentItemsPageDTO>(
    `/api/professor-information-enrichment-jobs/${jobId}/items/page`,
    undefined,
    {
      cursor: params?.cursor ?? 0,
      limit: params?.limit ?? 20,
      status: params?.status ?? undefined,
    },
  );

export const cancelProfessorInformationEnrichmentJob = (jobId: number) =>
  apiFetch<{ ok: boolean; job: ProfessorInformationEnrichmentJobDTO }>(
    `/api/professor-information-enrichment-jobs/${jobId}/cancel`,
    { method: 'POST' },
  );

export const retryFailedProfessorInformationEnrichmentJob = (jobId: number) =>
  apiFetch<ProfessorInformationEnrichmentJobDTO>(
    `/api/professor-information-enrichment-jobs/${jobId}/retry-failed`,
    { method: 'POST' },
  );

export const deleteProfessorInformationEnrichmentJob = (jobId: number) =>
  apiFetch<{ ok: boolean; job: ProfessorInformationEnrichmentJobDTO }>(
    `/api/professor-information-enrichment-jobs/${jobId}`,
    { method: 'DELETE' },
  );

export const restoreProfessorInformationEnrichmentJob = (jobId: number) =>
  apiFetch<{ ok: boolean; job: ProfessorInformationEnrichmentJobDTO }>(
    `/api/professor-information-enrichment-jobs/${jobId}/restore`,
    { method: 'POST' },
  );
