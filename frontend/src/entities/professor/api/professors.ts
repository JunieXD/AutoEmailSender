import { apiFetch } from '@/lib/api/client';
import { downloadApiFile } from '@/lib/api/download';
import type {
  ProfessorActionResultDTO,
  ProfessorBulkArchivePayloadDTO,
  ProfessorBulkTagsPayloadDTO,
  ProfessorBulkTagsResultDTO,
  ProfessorDTO,
  ProfessorDashboardItemDTO,
  ProfessorFetchByIdsDTO,
  ProfessorImportFileResultDTO,
  ProfessorManagementItemDTO,
  ProfessorDashboardPageRequestDTO,
  ProfessorManagementPageRequestDTO,
  ProfessorIdSelectionDTO,
  ProfessorPageDTO,
  ProfessorNoteUpdateDTO,
  ProfessorTagDTO,
  ProfessorTagPayloadDTO,
  ProfessorTagUsageDTO,
  ProfessorUpsertPayloadDTO,
} from '../model/types';

export const listProfessors = (params?: {
  identityId?: number | null;
  ids?: number[];
  page?: number;
  pageSize?: number;
}) =>
  apiFetch<ProfessorFetchByIdsDTO>(
    '/api/professors/fetch-by-ids',
    {
      method: 'POST',
      body: JSON.stringify({
        identity_id: params?.identityId ?? null,
        ids: params?.ids ?? [],
        page: params?.page ?? 1,
        page_size: params?.pageSize ?? null,
      }),
    },
  );

export const searchDashboardProfessors = (
  payload: ProfessorDashboardPageRequestDTO,
) =>
  apiFetch<ProfessorPageDTO<ProfessorDashboardItemDTO>>(
    '/api/professors/search/dashboard',
    {
      method: 'POST',
      body: JSON.stringify(payload),
    },
  );

export const searchManagementProfessors = (
  payload: ProfessorManagementPageRequestDTO,
) =>
  apiFetch<ProfessorPageDTO<ProfessorManagementItemDTO>>(
    '/api/professors/search/management',
    {
      method: 'POST',
      body: JSON.stringify(payload),
    },
  );

export const searchDashboardProfessorIds = (
  payload: ProfessorDashboardPageRequestDTO,
) =>
  apiFetch<ProfessorIdSelectionDTO>('/api/professors/search/dashboard/ids', {
    method: 'POST',
    body: JSON.stringify(payload),
  });

export const searchManagementProfessorIds = (
  payload: ProfessorManagementPageRequestDTO,
) =>
  apiFetch<ProfessorIdSelectionDTO>('/api/professors/search/management/ids', {
    method: 'POST',
    body: JSON.stringify(payload),
  });

export const getProfessor = (professorId: number) =>
  apiFetch<ProfessorDTO>(`/api/professors/${professorId}`);

export const listProfessorTags = () =>
  apiFetch<ProfessorTagDTO[]>('/api/professors/tags');

export const createProfessorTag = (payload: ProfessorTagPayloadDTO) =>
  apiFetch<ProfessorTagDTO>('/api/professors/tags', {
    method: 'POST',
    body: JSON.stringify(payload),
  });

export const deleteProfessorTag = (tagId: number, impactRevision: string) =>
  apiFetch<ProfessorActionResultDTO>(`/api/professors/tags/${tagId}`, {
    method: 'DELETE',
  }, { impact_revision: impactRevision });

export const getProfessorTagUsage = (tagId: number) =>
  apiFetch<ProfessorTagUsageDTO>(`/api/professors/tags/${tagId}/usage`);

export const createProfessor = (payload: ProfessorUpsertPayloadDTO) =>
  apiFetch<ProfessorManagementItemDTO>('/api/professors', {
    method: 'POST',
    body: JSON.stringify(payload),
  });

export const updateProfessor = (professorId: number, payload: ProfessorUpsertPayloadDTO) =>
  apiFetch<ProfessorManagementItemDTO>(`/api/professors/${professorId}`, {
    method: 'PATCH',
    body: JSON.stringify(payload),
  });

export const updateProfessorTags = (professorId: number, tagIds: number[]) =>
  apiFetch<ProfessorManagementItemDTO>(`/api/professors/${professorId}/tags`, {
    method: 'PATCH',
    body: JSON.stringify({ tag_ids: tagIds }),
  });

export const updateProfessorNote = (professorId: number, personalNote: string | null) =>
  apiFetch<ProfessorNoteUpdateDTO>(`/api/professors/${professorId}/note`, {
    method: 'PATCH',
    body: JSON.stringify({ personal_note: personalNote }),
  });

export const archiveProfessor = (professorId: number) =>
  apiFetch<ProfessorActionResultDTO>(`/api/professors/${professorId}/archive`, {
    method: 'POST',
  });

export const bulkArchiveProfessors = (payload: ProfessorBulkArchivePayloadDTO) =>
  apiFetch<ProfessorActionResultDTO>('/api/professors/bulk-archive', {
    method: 'POST',
    body: JSON.stringify(payload),
  });

export const bulkUpdateProfessorTags = (payload: ProfessorBulkTagsPayloadDTO) =>
  apiFetch<ProfessorBulkTagsResultDTO>('/api/professors/bulk-tags', {
    method: 'POST',
    body: JSON.stringify(payload),
  });

export const restoreProfessor = (professorId: number) =>
  apiFetch<ProfessorActionResultDTO>(`/api/professors/${professorId}/restore`, {
    method: 'POST',
  });

export const importProfessorsFromFile = async (file: File) => {
  const formData = new FormData();
  formData.append('file', file);
  return apiFetch<ProfessorImportFileResultDTO>('/api/professors/import-file', {
    method: 'POST',
    body: formData,
  });
};

export const downloadProfessorTemplate = (format: 'xlsx' | 'csv') =>
  downloadApiFile(
    '/api/professors/template',
    `professors_import_template.${format}`,
    undefined,
    { format },
  );

export const downloadProfessorExport = (format: 'xlsx' | 'csv') =>
  downloadApiFile(
    '/api/professors/export',
    `professors_export.${format}`,
    undefined,
    { format },
  );
