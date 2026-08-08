import { apiFetch } from '@/lib/api/client';
import { fetchApiFile } from '@/lib/api/download';
import type {
  CommunityCatalogDTO,
  CommunityImportPayloadDTO,
  CommunityImportResultDTO,
  CommunityPreviewPayloadDTO,
  CommunityRecordSelectionPayloadDTO,
  CommunityRecordsDTO,
} from '../model/types';


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

export const downloadCommunitySharePackage = (professorIds: number[]) =>
  fetchApiFile('/api/community-mentors/share-package', undefined, {
    professor_ids: professorIds.join(','),
  });
