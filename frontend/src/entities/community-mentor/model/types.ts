export type CommunityMentorStatusDTO =
  | 'active'
  | 'retired'
  | 'departed'
  | 'deceased'
  | 'stale'
  | 'disputed'
  | 'removed';

export type CommunityComparisonCategoryDTO =
  | 'new'
  | 'linked_unchanged'
  | 'fill_available'
  | 'local_modified'
  | 'remote_modified'
  | 'conflict'
  | 'archived_local'
  | 'retired_or_revoked';

export type CommunityFieldStateDTO =
  | 'new'
  | 'same'
  | 'fill_available'
  | 'local_only'
  | 'local_modified'
  | 'remote_modified'
  | 'conflict';

export type CommunityFieldChoiceDTO = 'community' | 'local';

export interface CommunityCatalogUnitDTO {
  id: string;
  name: string;
  type: 'university' | 'school' | 'institute' | 'department' | 'center' | 'laboratory';
  record_count: number;
  path: string;
}

export interface CommunityCatalogUniversityDTO {
  id: string;
  name: string;
  record_count: number;
  units: CommunityCatalogUnitDTO[];
}

export interface CommunityLifecycleWarningDTO {
  community_record_id: string;
  professor_id: number;
  professor_name: string;
  status: CommunityMentorStatusDTO;
  reason: string | null;
  source_url: string | null;
  observed_at: string | null;
}

export interface CommunityCatalogDTO {
  schema_version: 2;
  dataset_version: string;
  generated_at: string;
  record_count: number;
  universities: CommunityCatalogUniversityDTO[];
  source: 'network' | 'cache';
  stale: boolean;
  warning: string | null;
  verified_at: string;
  lifecycle_warnings: CommunityLifecycleWarningDTO[];
}

export interface CommunityMentorContactDTO {
  email: string;
  is_primary: boolean;
  affiliation_id: string | null;
  source_url: string;
  observed_at: string;
}

export interface CommunityMentorAffiliationDTO {
  id: string;
  organization_id: string;
  status: 'current' | 'former';
  is_primary: boolean;
  title: string | null;
  university: string;
  school: string | null;
  department: string | null;
  source_url: string;
  observed_at: string;
}

export interface CommunityMentorContributorDTO {
  github_user_id: number;
  github_login_at_submission: string;
  issue_urls: string[];
}

export interface CommunityMentorRecordDTO {
  id: string;
  name: string;
  email: string;
  title: string | null;
  university: string;
  school: string | null;
  department: string | null;
  research_direction: string | null;
  recent_papers: string[];
  profile_url: string | null;
  source_url: string;
  status: CommunityMentorStatusDTO;
  last_verified_at: string | null;
  contacts: CommunityMentorContactDTO[];
  affiliations: CommunityMentorAffiliationDTO[];
  contributors: CommunityMentorContributorDTO[];
}

export interface CommunityFieldComparisonDTO {
  field: string;
  label: string;
  local_value: unknown;
  community_value: unknown;
  baseline_present: boolean;
  baseline_value: unknown;
  state: CommunityFieldStateDTO;
  suggested_choice: CommunityFieldChoiceDTO;
}

export interface CommunityMentorComparisonDTO {
  record: CommunityMentorRecordDTO;
  comparison_token: string;
  category: CommunityComparisonCategoryDTO;
  local_professor_id: number | null;
  local_professor_name: string | null;
  local_archived: boolean;
  linked: boolean;
  identity_conflict: boolean;
  match_reason: string | null;
  import_blocked: boolean;
  import_blocked_reason: string | null;
  fields: CommunityFieldComparisonDTO[];
}

export interface CommunityRecordsDTO {
  dataset_version: string;
  source: 'network' | 'cache';
  stale: boolean;
  warning: string | null;
  records: CommunityMentorComparisonDTO[];
  lifecycle_warnings: CommunityLifecycleWarningDTO[];
}

export interface CommunityRecordSelectionPayloadDTO {
  dataset_version: string;
  unit_paths: string[];
}

export interface CommunityPreviewPayloadDTO extends CommunityRecordSelectionPayloadDTO {
  record_ids: string[];
}

export interface CommunityImportItemPayloadDTO {
  community_record_id: string;
  comparison_token: string;
  field_choices: Record<string, CommunityFieldChoiceDTO>;
  confirm_identity_match: boolean;
}

export interface CommunityImportPayloadDTO extends CommunityRecordSelectionPayloadDTO {
  items: CommunityImportItemPayloadDTO[];
}

export interface CommunityImportedProfessorDTO {
  community_record_id: string;
  professor_id: number;
  action: 'inserted' | 'updated' | 'linked';
}

export interface CommunityImportResultDTO {
  inserted_count: number;
  updated_count: number;
  linked_count: number;
  skipped_count: number;
  message: string;
  professors: CommunityImportedProfessorDTO[];
}
