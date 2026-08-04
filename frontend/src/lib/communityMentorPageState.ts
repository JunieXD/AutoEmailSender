import type {
  CommunityComparisonCategoryDTO,
  CommunityFieldChoiceDTO,
  CommunityFieldStateDTO,
  CommunityRecordsDTO,
} from '@/types';

export type CommunityMentorSearchScope =
  | 'name'
  | 'email'
  | 'organization'
  | 'title'
  | 'research_direction';

export type CommunityMentorPageSessionSnapshot = {
  datasetVersion: string | null;
  catalogUniversityFilters: string[];
  catalogUnitFilters: string[];
  catalogUnitPage: number;
  selectedUnitPaths: string[];
  loadedUnitPaths: string[] | null;
  recordsPayload: CommunityRecordsDTO | null;
  recordKeyword: string;
  recordSearchScopes: CommunityMentorSearchScope[];
  recordUniversityFilters: string[];
  recordSchoolFilters: string[];
  recordDepartmentFilters: string[];
  recordTitleFilters: string[];
  categoryFilters: CommunityComparisonCategoryDTO[];
  recordPage: number;
  selectedRecordIds: string[];
  previewPayload: CommunityRecordsDTO | null;
  previewPage: number;
  previewKeyword: string;
  previewSearchScopes: CommunityMentorSearchScope[];
  previewCategoryFilters: CommunityComparisonCategoryDTO[];
  previewFieldStateFilters: CommunityFieldStateDTO[];
  previewFieldFilters: string[];
  previewOnlyUnconfirmed: boolean;
  previewBulkField: string | null;
  fieldChoices: Record<string, Record<string, CommunityFieldChoiceDTO>>;
  identityConfirmations: Record<string, boolean>;
};

let pageSessionSnapshot: CommunityMentorPageSessionSnapshot | null = null;

export const getCommunityMentorPageSessionSnapshot = () => pageSessionSnapshot;

export const setCommunityMentorPageSessionSnapshot = (
  snapshot: CommunityMentorPageSessionSnapshot,
) => {
  pageSessionSnapshot = snapshot;
};

export const resetCommunityMentorPageSessionSnapshotForTests = () => {
  pageSessionSnapshot = null;
};
