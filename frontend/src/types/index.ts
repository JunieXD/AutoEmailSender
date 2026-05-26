export type OutreachGenerationMode = 'llm' | 'template';

export type MentorStatus = '未发送' | '已读' | '待审核' | '已回复' | '婉拒';

export interface Mentor {
  id: string;
  name: string;
  title: string;
  university: string;
  school: string;
  research: string[];
  matchScore: number;
  sentCount: number;
  status: MentorStatus;
}

export interface SmtpConfig {
  host: string;
  port: number;
  username: string;
  password: string;
  fromEmail: string;
  fromName: string;
  useTLS: boolean;
}

export interface ImapConfig {
  host: string;
  port: number;
  username: string;
  password: string;
  useSSL: boolean;
}

export interface ResumeFile {
  name: string;
  size: number;
  url: string;
}

export interface Profile {
  id: string;
  name: string;
  title: string;
  direction: string;
  avatar?: string;
  smtp: SmtpConfig;
  imap?: ImapConfig;
  resumes: ResumeFile[];
  isDefault: boolean;
  createdAt: string;
  updatedAt: string;
}

export interface LLMConfig {
  openaiApiKey?: string;
  deepseekApiKey?: string;
  model: 'openai' | 'deepseek';
  updatedAt?: string;
}

export type IdentityMaterialType = 'resume' | 'transcript' | 'publication' | 'portfolio' | 'other';

export interface IdentityMaterialDTO {
  id: number;
  display_name: string;
  original_filename: string;
  mime_type: string | null;
  size_bytes: number;
  material_type: IdentityMaterialType;
  is_primary: boolean;
  created_at: string;
}

export interface IdentityDTO {
  id: number;
  name: string;
  profile_name: string;
  sender_name: string;
  email_address: string;
  smtp_host: string;
  smtp_port: number;
  smtp_username: string;
  smtp_password: string;
  imap_host: string | null;
  imap_port: number | null;
  imap_username: string | null;
  imap_password: string | null;
  default_language: string;
  outreach_generation_mode: OutreachGenerationMode;
  outreach_template_subject: string | null;
  outreach_template_body_text: string | null;
  outreach_template_body_html: string | null;
  current_primary_material_id: number | null;
  current_primary_material: IdentityMaterialDTO | null;
  match_threshold: number | null;
  daily_send_limit: number | null;
  send_interval_min: number | null;
  send_interval_max: number | null;
  same_domain_cooldown_minutes: number | null;
  is_default: boolean;
  materials: IdentityMaterialDTO[];
  created_at: string;
  updated_at: string;
}

export interface IdentityPayload {
  name: string;
  profile_name: string;
  sender_name: string;
  email_address: string;
  smtp_host: string;
  smtp_port: number;
  smtp_username: string;
  smtp_password: string;
  imap_host: string;
  imap_port: number;
  imap_username: string | null;
  imap_password: string | null;
  default_language: string;
  outreach_generation_mode: OutreachGenerationMode;
  outreach_template_subject: string | null;
  outreach_template_body_text: string | null;
  outreach_template_body_html: string | null;
  daily_send_limit: number | null;
  send_interval_min: number | null;
  send_interval_max: number | null;
  same_domain_cooldown_minutes: number | null;
  is_default: boolean;
}

export interface ConnectionTestResultDTO {
  ok: boolean;
  message: string;
  host: string | null;
}

export interface IdentityTemplateImportResultDTO {
  subject: string | null;
  body_text: string;
  body_html: string;
  format_name: string;
}

export interface LLMProfileDTO {
  id: number;
  name: string;
  provider: string;
  api_base_url: string | null;
  api_key: string;
  model_name: string;
  matcher_prompt_template: string | null;
  writer_prompt_template: string | null;
  temperature: number | null;
  max_tokens: number | null;
  is_default: boolean;
  created_at: string;
  updated_at: string;
}

export interface LLMProfilePayload {
  name: string;
  provider: string;
  api_base_url: string | null;
  api_key: string;
  model_name: string;
  matcher_prompt_template: string | null;
  writer_prompt_template: string | null;
  temperature: number | null;
  max_tokens: number | null;
  is_default: boolean;
}

export interface LLMProfileTestResultDTO {
  ok: boolean;
  message: string;
  resolved_base_url: string | null;
  request_url: string | null;
  attempted_urls: string[];
  endpoint_kind: string | null;
  status_code: number | null;
  duration_ms: number | null;
  consumes_tokens: boolean;
  prompt_tokens: number | null;
  completion_tokens: number | null;
  total_tokens: number | null;
  response_preview: string | null;
}

export interface LLMProfileModelsResultDTO {
  ok: boolean;
  message: string;
  resolved_base_url: string | null;
  request_url: string | null;
  attempted_urls: string[];
  endpoint_kind: string | null;
  status_code: number | null;
  duration_ms: number | null;
  consumes_tokens: boolean;
  models: string[];
  selected_model_available: boolean | null;
}

export interface ProfessorDashboardItemDTO {
  id: number;
  name: string;
  email: string | null;
  title: string | null;
  university: string | null;
  school: string | null;
  department: string | null;
  research_direction: string | null;
  recent_papers: string[];
  match_score: number | null;
  sent_count: number;
  status: ProfessorDashboardStatus;
}

export type ProfessorDashboardStatus =
  | 'not_contacted'
  | 'preparing'
  | 'ready_to_send'
  | 'contacted'
  | 'replied'
  | 'failed';

export interface ProfessorDTO {
  id: number;
  name: string;
  email: string | null;
  title: string | null;
  university: string | null;
  school: string | null;
  department: string | null;
  research_direction: string | null;
  recent_papers: string[] | null;
  profile_url: string | null;
  source_url: string | null;
  crawl_status: string;
  skip_reason: string | null;
  archived_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface ProfessorImportResultDTO {
  inserted_count: number;
  total_count: number;
  message: string;
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
  recent_papers: string[];
  profile_url: string | null;
  source_url: string | null;
  crawl_status: string;
  skip_reason: string | null;
  archived_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface ProfessorUpsertPayloadDTO {
  name: string;
  email: string;
  title: string | null;
  university: string | null;
  school: string | null;
  department: string | null;
  research_direction: string | null;
  recent_papers: string[];
  profile_url: string | null;
  source_url: string | null;
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

export interface ProfessorActionResultDTO {
  ok: boolean;
  affected_count: number;
  message: string;
}

export type CrawlJobStatusDTO =
  | 'queued'
  | 'running'
  | 'paused'
  | 'needs_review'
  | 'partially_completed'
  | 'completed'
  | 'failed'
  | 'canceled';

export type CrawlJobEntryTypeDTO = 'list' | 'profile';
export type CrawlCandidateReviewStatusDTO = 'pending' | 'accepted' | 'rejected' | 'merged';

export interface CrawlJobCreatePayloadDTO {
  university: string;
  school: string;
  start_url: string;
  start_urls?: string[];
  entry_type: CrawlJobEntryTypeDTO;
  llm_profile_id: number | null;
}

export interface CrawlJobDTO {
  id: number;
  university: string;
  school: string;
  start_url: string;
  start_urls?: string[];
  entry_type: CrawlJobEntryTypeDTO;
  llm_profile_id: number | null;
  status: CrawlJobStatusDTO;
  progress_current: number;
  progress_total: number;
  error_message: string | null;
  created_at: string;
  updated_at: string;
  deleted_at: string | null;
}

export interface CrawlJobSummaryDTO extends CrawlJobDTO {
  page_count: number;
  candidate_count: number;
  latest_event_message: string | null;
  input_tokens: number;
  output_tokens: number;
  total_tokens: number;
  duration_seconds: number;
}

export interface CrawlPageDTO {
  id: number;
  job_id: number;
  url: string;
  parent_url: string | null;
  fetch_method: string;
  page_type: string;
  status: string;
  title: string | null;
  text_excerpt: string | null;
  error_message: string | null;
  created_at: string;
}

export interface CrawlJobEventDTO {
  id: string;
  job_id: number;
  event_type: string;
  message: string;
  created_at: string | null;
  raw: Record<string, unknown> | null;
}

export interface CrawlCandidateDTO {
  id: number;
  job_id: number;
  professor_id: number | null;
  name: string;
  email: string | null;
  title: string | null;
  university: string | null;
  school: string | null;
  department: string | null;
  research_direction: string | null;
  recent_papers: string[];
  profile_url: string | null;
  source_url: string | null;
  confidence: number;
  field_confidence: Record<string, number> | null;
  evidence: Record<string, unknown> | null;
  review_status: CrawlCandidateReviewStatusDTO;
  created_at: string;
  updated_at: string;
}

export interface CrawlCandidateUpdatePayloadDTO {
  name: string;
  email: string | null;
  title: string | null;
  university: string | null;
  school: string | null;
  department: string | null;
  research_direction: string | null;
  recent_papers: string[];
  profile_url: string | null;
  source_url: string | null;
  review_status: CrawlCandidateReviewStatusDTO;
}

export interface CrawlJobApproveResultDTO {
  inserted_count: number;
  updated_count: number;
  skipped_count: number;
  message: string;
}

export interface CrawlJobEnrichResultDTO {
  selected_count: number;
  enriched_count: number;
  unchanged_count: number;
  failed_count: number;
  message: string;
}

export interface CrawlJobRetryPayloadDTO {
  clear_existing_data: boolean;
}

export interface CreateBatchTaskRequestDTO {
  identity_id: number;
  llm_profile_id: number;
  name: string;
  professor_ids: number[];
  schedule_type: 'immediate' | 'scheduled';
  scheduled_dates: string[] | null;
  window_start_time: string | null;
  window_end_time: string | null;
  emails_per_window: number | null;
  primary_material_id: number | null;
  email_subject: string | null;
  email_body: string | null;
  selected_material_ids: number[] | null;
  outreach_generation_mode: OutreachGenerationMode | null;
  outreach_template_subject: string | null;
  outreach_template_body_text: string | null;
  outreach_template_body_html: string | null;
}

export type MatchAnalysisJobStatus =
  | 'queued'
  | 'running'
  | 'completed'
  | 'partial_failed'
  | 'failed'
  | 'canceled';

export type MatchAnalysisJobItemStatus =
  | 'queued'
  | 'running'
  | 'succeeded'
  | 'failed'
  | 'skipped'
  | 'canceled';

export interface CreateMatchAnalysisJobRequestDTO {
  identity_id: number;
  llm_profile_id: number;
  professor_ids: number[];
  name?: string | null;
}

export interface MatchAnalysisJobDTO {
  id: number;
  name: string;
  status: MatchAnalysisJobStatus;
  target_count: number;
  succeeded_count: number;
  failed_count: number;
  skipped_count: number;
  total_prompt_tokens: number;
  total_completion_tokens: number;
  total_tokens: number;
  identity_id: number;
  llm_profile_id: number;
  cancel_requested_at: string | null;
  started_at: string | null;
  finished_at: string | null;
  created_at: string;
  updated_at: string;
  deleted_at: string | null;
  last_error: string | null;
}

export interface MatchAnalysisJobItemDTO {
  id: number;
  job_id: number;
  professor_id: number;
  professor_name: string;
  professor_email: string | null;
  professor_title: string | null;
  professor_school: string | null;
  email_task_id: number | null;
  status: MatchAnalysisJobItemStatus;
  match_score: number | null;
  match_analysis_run_id: number | null;
  error_message: string | null;
  skip_reason: string | null;
  prompt_tokens: number;
  completion_tokens: number;
  total_tokens: number;
  started_at: string | null;
  finished_at: string | null;
  updated_at: string;
}

export const MATCH_ANALYSIS_JOB_STATUS_LABELS: Record<
  MatchAnalysisJobStatus,
  string
> = {
  queued: '排队中',
  running: '运行中',
  completed: '已完成',
  partial_failed: '部分失败',
  failed: '失败',
  canceled: '已取消',
};

export type BatchTaskRuntimeStatus = 'running' | 'paused' | 'stopped' | 'completed' | 'expired';
export type TaskListView = 'current' | 'trash';

export type WorkspaceTaskStatus =
  | 'discovered'
  | 'matched'
  | 'generating_draft'
  | 'draft_failed'
  | 'review_required'
  | 'approved'
  | 'scheduled'
  | 'sending'
  | 'sent'
  | 'send_failed'
  | 'reply_detected'
  | 'canceled';

export type WorkspaceTaskStatusLabelKey = WorkspaceTaskStatus;
export type WorkspaceTaskCancellationReason = 'batch_stopped' | 'schedule_expired';
export type BatchTaskItemNextAction =
  | 'waiting_draft_generation'
  | 'complete_professor_profile'
  | 'select_primary_material'
  | 'review_draft'
  | 'waiting_send'
  | 'waiting_scheduled_send'
  | 'missing_schedule'
  | 'retry_draft_generation'
  | 'send_failed';

export interface BatchTaskCardDTO {
  id: number;
  name: string;
  status: BatchTaskRuntimeStatus;
  schedule_type: 'immediate' | 'scheduled';
  scheduled_dates: string[] | null;
  window_start_time: string | null;
  window_end_time: string | null;
  emails_per_window: number | null;
  email_subject: string | null;
  target_count: number;
  completed_count: number;
  identity_id: number;
  llm_profile_id: number;
  pending_generation_count: number;
  generating_draft_count: number;
  draft_failed_count: number;
  review_required_count: number;
  approved_count: number;
  scheduled_count: number;
  sent_count: number;
  failed_count: number;
  replied_count: number;
  created_at: string;
  updated_at: string;
  deleted_at: string | null;
}

export interface BatchTaskItemDTO {
  id: number;
  professor_id: number;
  professor_name: string;
  professor_email: string | null;
  professor_title: string | null;
  professor_school: string | null;
  status: WorkspaceTaskStatus;
  cancellation_reason?: WorkspaceTaskCancellationReason | null;
  match_score: number | null;
  scheduled_at: string | null;
  sent_at: string | null;
  last_send_attempt_at: string | null;
  last_error: string | null;
  is_replied: boolean;
  updated_at: string;
  next_action: BatchTaskItemNextAction | null;
}

export interface WorkspaceProfessorDTO {
  id: number;
  name: string;
  email: string | null;
  title: string | null;
  university: string | null;
  school: string | null;
  research_direction: string | null;
  recent_papers: string[];
}

export interface WorkspaceIdentityDTO {
  id: number;
  name: string;
  profile_name: string;
  sender_name: string;
  email_address: string;
}

export interface WorkspaceLLMDTO {
  id: number;
  name: string;
  provider: string;
  model_name: string;
}

export interface WorkspaceTaskSummaryDTO {
  id: number | null;
  source?: string | null;
  batch_task_id: number | null;
  parent_task_id?: number | null;
  status: WorkspaceTaskStatus | null;
  cancellation_reason?: string | null;
  can_continue_manually?: boolean;
  can_write_follow_up?: boolean;
  outreach_generation_mode: OutreachGenerationMode;
  outreach_template_subject: string | null;
  outreach_template_body_text: string | null;
  outreach_template_body_html: string | null;
  rendered_template_subject: string | null;
  rendered_template_body_text: string | null;
  rendered_template_body_html: string | null;
  match_score: number | null;
  match_reason: string | null;
  fit_points: string[];
  risk_points: string[];
  match_keywords: string[];
  generated_subject: string | null;
  generated_content_text: string | null;
  generated_content_html: string | null;
  approved_subject: string | null;
  approved_body_text: string | null;
  approved_body_html: string | null;
  primary_material_id: number | null;
  primary_material: IdentityMaterialDTO | null;
  selected_material_ids: number[] | null;
  approved_at: string | null;
  scheduled_at: string | null;
  last_send_attempt_at: string | null;
  sent_at: string | null;
  last_rfc_message_id: string | null;
  retry_count: number;
  last_error: string | null;
  is_replied: boolean;
  estimated_prompt_tokens: number | null;
  estimated_completion_tokens_upper_bound: number | null;
  estimated_total_tokens_upper_bound: number | null;
  last_draft_prompt_tokens: number | null;
  last_draft_completion_tokens: number | null;
  last_draft_total_tokens: number | null;
}

export interface WorkspaceMessageDTO {
  id: number;
  direction: 'sent' | 'received' | 'draft';
  subject: string | null;
  content: string;
  content_html: string | null;
  rfc_message_id: string | null;
  failure_summary: string | null;
  reply_headers: Record<string, unknown> | null;
  prompt_tokens: number | null;
  completion_tokens: number | null;
  total_tokens: number | null;
  created_at: string;
}

export interface WorkspaceThreadDTO {
  professor: WorkspaceProfessorDTO;
  identity: WorkspaceIdentityDTO;
  llm_profile: WorkspaceLLMDTO;
  material_options: IdentityMaterialDTO[];
  current_task: WorkspaceTaskSummaryDTO;
  messages: WorkspaceMessageDTO[];
}

export interface TokenUsageDTO {
  prompt_tokens: number | null;
  completion_tokens: number | null;
  total_tokens: number | null;
  cached_tokens: number | null;
}

export interface MatchCalculationResultDTO {
  thread: WorkspaceThreadDTO;
  usage: TokenUsageDTO;
  run_id: number | null;
}

export interface TestComposeThreadDTO {
  identity: WorkspaceIdentityDTO;
  llm_profile: WorkspaceLLMDTO;
  material_options: IdentityMaterialDTO[];
  draft: {
    subject: string | null;
    body_text: string;
    body_html: string | null;
    selected_material_ids: number[];
  };
  history: Array<{
    id: number;
    recipient_email: string;
    subject: string | null;
    content: string;
    content_html: string | null;
    status: string;
    rfc_message_id: string | null;
    failure_summary: string | null;
    created_at: string;
  }>;
}

export interface TestComposeStatusDTO {
  completed: boolean;
}

export interface TestComposeDraftPayloadDTO {
  subject: string | null;
  body_text: string;
  body_html: string | null;
  selected_material_ids: number[] | null;
}

export interface EmailTaskApprovalPayloadDTO {
  subject: string | null;
  body_text: string;
  body_html: string | null;
  selected_material_ids: number[] | null;
}

export interface EmailTaskOutreachConfigPayloadDTO {
  outreach_generation_mode: OutreachGenerationMode;
  outreach_template_subject?: string | null;
  outreach_template_body_text?: string | null;
  outreach_template_body_html?: string | null;
}

export interface EmailTaskSchedulePayloadDTO extends EmailTaskApprovalPayloadDTO {
  scheduled_at: string;
}

export type TokenUsageRecordFeatureTypeDTO = 'crawl' | 'match_analysis' | 'draft_generation';
export type TokenUsageRecordFeatureFilterDTO = 'all' | TokenUsageRecordFeatureTypeDTO;
export type TokenUsageRecordStatusDTO = 'success' | 'failed' | 'running' | 'unknown';
export type TokenUsageChartPresetDTO =
  | 'last_6_hours'
  | 'last_24_hours'
  | 'last_7_days'
  | 'custom';
export type TokenUsageChartGranularityDTO = 'hour' | 'day';

export interface TokenUsageRecordDTO {
  id: string;
  feature_type: TokenUsageRecordFeatureTypeDTO;
  feature_label: string;
  title: string;
  input_tokens: number | null;
  output_tokens: number | null;
  cached_tokens: number | null;
  total_tokens: number | null;
  model_name: string | null;
  identity_name: string | null;
  created_at: string;
  status: TokenUsageRecordStatusDTO;
}

export interface TokenUsageSummaryDTO {
  input_tokens: number;
  output_tokens: number;
  cached_tokens: number;
  total_tokens: number;
  record_count: number;
}

export interface TokenUsagePaginationDTO {
  page: number;
  page_size: number;
  total_records: number;
  total_pages: number;
}

export interface TokenUsageRecordListDTO {
  records: TokenUsageRecordDTO[];
  summary: TokenUsageSummaryDTO;
  pagination: TokenUsagePaginationDTO;
  model_options: string[];
}

export interface TokenUsageChartBucketDTO {
  bucket_start: string;
  bucket_label: string;
  input_tokens: number;
  output_tokens: number;
  total_tokens: number;
}

export interface TokenUsageChartDTO {
  preset: TokenUsageChartPresetDTO;
  granularity: TokenUsageChartGranularityDTO;
  range_start: string;
  range_end: string;
  buckets: TokenUsageChartBucketDTO[];
}

export const PROFESSOR_STATUS_LABELS = {
  discovered: '待处理',
  matched: '待生成',
  generating_draft: '正在生成草稿',
  draft_failed: '草稿生成失败',
  review_required: '待审核',
  approved: '待发送',
  scheduled: '已排程',
  sending: '发送中',
  sent: '已发送',
  reply_detected: '已回复',
  send_failed: '发送失败',
  canceled: '已取消',
} satisfies Record<WorkspaceTaskStatusLabelKey, string>;

export const BATCH_TASK_STATUS_LABELS: Record<BatchTaskRuntimeStatus, string> = {
  running: '运行中',
  paused: '已暂停',
  stopped: '已中止',
  completed: '已完成',
  expired: '已过期',
};

export const MATERIAL_TYPE_LABELS: Record<IdentityMaterialType, string> = {
  resume: '简历',
  transcript: '成绩单',
  publication: '论文',
  portfolio: '作品集',
  other: '其他',
};

export const WORKSPACE_DIRECTION_LABELS: Record<WorkspaceMessageDTO['direction'], string> = {
  draft: '草稿',
  sent: '已发送',
  received: '已收到',
};
