import { apiFetch, buildApiUrl } from '@/lib/api/client';
import type {
  CommunityCatalogDTO,
  CommunityImportPayloadDTO,
  CommunityImportResultDTO,
  CommunityPreviewPayloadDTO,
  CommunityRecordSelectionPayloadDTO,
  CommunityRecordsDTO,
} from '@/types';


export const getCommunityMentorCatalog = (refresh = false) =>
  apiFetch<CommunityCatalogDTO>('/api/community-mentors/catalog', undefined, {
    refresh: refresh ? 'true' : 'false',
  });

export const listCommunityMentors = (payload: CommunityRecordSelectionPayloadDTO) =>
  apiFetch<CommunityRecordsDTO>('/api/community-mentors/records', {
    method: 'POST',
    body: JSON.stringify(payload),
  });

export const previewCommunityMentorImport = (payload: CommunityPreviewPayloadDTO) =>
  apiFetch<CommunityRecordsDTO>('/api/community-mentors/preview', {
    method: 'POST',
    body: JSON.stringify(payload),
  });

export const importCommunityMentors = (payload: CommunityImportPayloadDTO) =>
  apiFetch<CommunityImportResultDTO>('/api/community-mentors/import', {
    method: 'POST',
    body: JSON.stringify(payload),
  });

export const getCommunitySharePackageDownloadUrl = (professorIds: number[]) =>
  buildApiUrl('/api/community-mentors/share-package', {
    professor_ids: professorIds.join(','),
  });
