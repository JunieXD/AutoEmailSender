export interface ProfessorDashboardItemDTO {
  id: number;
  name: string;
  email: string | null;
  title: string | null;
  university: string | null;
  school: string | null;
  department: string | null;
  research_direction: string | null;
  personal_note: string | null;
  recent_papers: string[];
  match_score: number | null;
  match_source_identity_id?: number | null;
  match_source_identity_name?: string | null;
  match_is_shared?: boolean;
  match_is_stale?: boolean;
  match_analyzed_at?: string | null;
  sent_count: number;
  status: ProfessorDashboardStatus;
  has_active_schedule?: boolean;
  last_sent_at: string | null;
  last_replied_at: string | null;
  tags: ProfessorTagDTO[];
}

export interface ProfessorFilterOptionsDTO {
  universities: string[];
  schools: string[];
  departments: string[];
  titles: string[];
  tags: Array<{ id: number; name: string }>;
}

export interface ProfessorPageDTO<T> {
  items: T[];
  total_count: number;
  has_any_professors: boolean;
  page: number;
  page_size: number;
  total_pages: number;
  next_cursor: string | null;
  filter_options: ProfessorFilterOptionsDTO;
}

export interface ProfessorFetchByIdsDTO {
  items: ProfessorDashboardItemDTO[];
  total_count: number;
  page: number;
  page_size: number;
  total_pages: number;
}

export interface ProfessorIdSelectionDTO {
  ids: number[];
  total_count: number;
}

export type ProfessorKeywordSearchScopeDTO =
  | 'name'
  | 'email'
  | 'university'
  | 'school'
  | 'department'
  | 'title'
  | 'researchDirection'
  | 'personalNote'
  | 'tag';

export interface ProfessorDashboardPageRequestDTO {
  ui_handoff_id?: string | null;
  identity_id: number;
  page: number;
  page_size: number;
  cursor?: string | null;
  keyword: string;
  keyword_search_scopes: ProfessorKeywordSearchScopeDTO[];
  universities: string[];
  schools: string[];
  departments: string[];
  titles: string[];
  statuses: ProfessorDashboardFilterStatus[];
  tag_ids: string[];
  min_match_score: number | null;
  max_match_score: number | null;
  match_score_missing: boolean;
  sort_key:
    | 'latest'
    | 'matchScoreDesc'
    | 'sentCountDesc'
    | 'nameAsc'
    | 'lastSentAt'
    | 'lastRepliedAt';
  sort_direction: 'asc' | 'desc';
}

export interface ProfessorManagementPageRequestDTO {
  ui_handoff_id?: string | null;
  archived: 'active' | 'archived' | 'all';
  page: number;
  page_size: number;
  cursor?: string | null;
  keyword: string;
  keyword_search_scopes: ProfessorKeywordSearchScopeDTO[];
  universities: string[];
  schools: string[];
  departments: string[];
  titles: string[];
  tag_ids: string[];
  sort_key: 'latest' | 'updatedAtDesc' | 'nameAsc' | 'universityAsc';
  sort_direction: 'asc' | 'desc';
}

export type ProfessorDashboardStatus =
  | 'not_contacted'
  | 'preparing'
  | 'ready_to_send'
  | 'contacted'
  | 'replied'
  | 'failed';

export type ProfessorDashboardFilterStatus =
  | ProfessorDashboardStatus
  | 'scheduled';

export interface ProfessorDTO {
  id: number;
  name: string;
  email: string | null;
  title: string | null;
  university: string | null;
  school: string | null;
  department: string | null;
  research_direction: string | null;
  personal_note: string | null;
  recent_papers: string[] | null;
  profile_url: string | null;
  source_url: string | null;
  crawl_status: string;
  skip_reason: string | null;
  archived_at: string | null;
  created_at: string;
  updated_at: string;
  tags: ProfessorTagDTO[];
}

export interface ProfessorTagDTO {
  id: number;
  name: string;
  text_color: string;
  background_color: string;
}

export interface ProfessorTagPayloadDTO {
  name: string;
  text_color: string;
  background_color: string;
}

export interface ProfessorTagUsageProfessorDTO {
  id: number;
  name: string;
  email: string | null;
  university: string | null;
  school: string | null;
}

export interface ProfessorTagUsageDTO {
  tag: ProfessorTagDTO;
  professors: ProfessorTagUsageProfessorDTO[];
  revision: string;
}

export interface ProfessorManagementItemDTO {
  id: number;
  name: string;
  email: string | null;
  title: string | null;
  university: string | null;
  school: string | null;
  department: string | null;
  research_direction: string | null;
  personal_note: string | null;
  recent_papers: string[];
  profile_url: string | null;
  source_url: string | null;
  crawl_status: string;
  skip_reason: string | null;
  archived_at: string | null;
  created_at: string;
  updated_at: string;
  tags: ProfessorTagDTO[];
}

export interface ProfessorUpsertPayloadDTO {
  name: string;
  email: string;
  title: string | null;
  university: string | null;
  school: string | null;
  department: string | null;
  research_direction: string | null;
  personal_note: string | null;
  recent_papers: string[];
  profile_url: string | null;
  source_url: string | null;
  tag_ids: number[];
}

export interface ProfessorNoteUpdateDTO {
  id: number;
  personal_note: string | null;
  updated_at: string;
}

export interface ProfessorImportFileResultDTO {
  inserted_count: number;
  updated_count: number;
  failed_count: number;
  message: string;
}

export interface ProfessorBulkArchivePayloadDTO {
  ids: number[];
}

export type ProfessorBulkTagModeDTO = 'add' | 'remove' | 'replace';

export interface ProfessorBulkTagsPayloadDTO {
  professor_ids: number[];
  mode: ProfessorBulkTagModeDTO;
  tag_ids: number[];
}

export interface ProfessorBulkTagsResultDTO {
  ok: boolean;
  affected_count: number;
  message: string;
}

export interface ProfessorActionResultDTO {
  ok: boolean;
  affected_count: number;
  message: string;
  canceled_email_task_ids?: number[];
  canceled_match_analysis_item_ids?: number[];
  canceled_information_enrichment_task_ids?: number[];
}

export type ProfessorInformationEnrichmentJobStatus =
  | 'queued'
  | 'running'
  | 'partially_completed'
  | 'completed'
  | 'failed'
  | 'canceled';

export type ProfessorInformationEnrichmentItemStatus =
  | 'queued'
  | 'running'
  | 'succeeded'
  | 'failed'
  | 'skipped'
  | 'canceled';

export interface ProfessorInformationEnrichmentJobDTO {
  id: number;
  name: string;
  trigger_mode: 'single' | 'batch';
  status: ProfessorInformationEnrichmentJobStatus;
  target_count: number;
  completed_count: number;
  queued_count: number;
  running_count: number;
  succeeded_count: number;
  failed_count: number;
  skipped_count: number;
  canceled_count: number;
  input_tokens: number;
  output_tokens: number;
  cached_tokens: number;
  total_tokens: number;
  llm_profile_id: number | null;
  started_at: string | null;
  finished_at: string | null;
  duration_seconds: number;
  created_at: string;
  updated_at: string;
  deleted_at: string | null;
  last_error: string | null;
}

export interface ProfessorInformationEnrichmentJobsPageDTO {
  items: ProfessorInformationEnrichmentJobDTO[];
  total_count: number;
  current_total_count: number;
}

export interface ProfessorInformationEnrichmentItemDTO {
  id: number;
  job_id: number;
  professor_id: number | null;
  professor_name: string;
  professor_email: string | null;
  professor_title: string | null;
  professor_university: string | null;
  professor_school: string | null;
  professor_department: string | null;
  profile_url: string | null;
  status: ProfessorInformationEnrichmentItemStatus;
  enriched_fields: string[];
  error_message: string | null;
  skip_reason: string | null;
  input_tokens: number;
  output_tokens: number;
  cached_tokens: number;
  total_tokens: number;
  attempt_count: number;
  started_at: string | null;
  finished_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface ProfessorInformationEnrichmentItemsPageDTO {
  items: ProfessorInformationEnrichmentItemDTO[];
  total_count: number;
  next_cursor: number | null;
  has_more: boolean;
}

export interface ProfessorInformationEnrichmentActiveDTO {
  active: boolean;
  job: ProfessorInformationEnrichmentJobDTO | null;
}

export const PROFESSOR_INFORMATION_ENRICHMENT_STATUS_LABELS: Record<
  ProfessorInformationEnrichmentJobStatus,
  string
> = {
  queued: '排队中',
  running: '运行中',
  partially_completed: '部分完成',
  completed: '已完成',
  failed: '失败',
  canceled: '已取消',
};

export type ProfessorInformationEnrichmentListView = 'current' | 'trash';
