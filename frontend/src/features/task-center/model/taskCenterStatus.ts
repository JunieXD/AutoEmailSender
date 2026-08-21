import type {
  MatchAnalysisJobItemStatus,
  MatchAnalysisJobStatus,
  ProfessorInformationEnrichmentItemStatus,
  ProfessorInformationEnrichmentJobStatus,
  WorkspaceTaskStatus,
} from "@/types";

export const BATCH_ITEM_STATUS_TONES: Record<WorkspaceTaskStatus, string> = {
  discovered: "bg-stone-100 text-stone-700",
  matched: "bg-sky-50 text-sky-700",
  generating_draft: "bg-sky-50 text-sky-700",
  draft_failed: "bg-red-50 text-red-700",
  review_required: "bg-amber-50 text-amber-700",
  approved: "bg-primary/10 text-primary",
  scheduled: "bg-indigo-50 text-indigo-700",
  schedule_missed: "bg-amber-50 text-amber-700",
  sending: "bg-sky-50 text-sky-700",
  sent: "bg-emerald-50 text-emerald-700",
  send_failed: "bg-red-50 text-red-700",
  reply_detected: "bg-emerald-100 text-emerald-800",
  canceled: "bg-stone-100 text-stone-500",
};

export const MATCH_ANALYSIS_JOB_STATUS_TONES: Record<
  MatchAnalysisJobStatus,
  string
> = {
  queued: "border-sky-200 bg-sky-50 text-sky-700",
  running: "border-primary/20 bg-primary/10 text-primary",
  completed: "border-emerald-200 bg-emerald-50 text-emerald-700",
  partial_failed: "border-amber-200 bg-amber-50 text-amber-700",
  failed: "border-red-200 bg-red-50 text-red-700",
  canceled: "border-stone-200 bg-stone-100 text-stone-600",
};

export const MATCH_ANALYSIS_ITEM_STATUS_LABELS: Record<
  MatchAnalysisJobItemStatus,
  string
> = {
  queued: "排队中",
  running: "分析中",
  succeeded: "成功",
  failed: "失败",
  skipped: "已跳过",
  canceled: "已取消",
};

export const MATCH_ANALYSIS_ITEM_STATUS_TONES: Record<
  MatchAnalysisJobItemStatus,
  string
> = {
  queued: "border-sky-200 bg-sky-50 text-sky-700",
  running: "border-primary/20 bg-primary/10 text-primary",
  succeeded: "border-emerald-200 bg-emerald-50 text-emerald-700",
  failed: "border-red-200 bg-red-50 text-red-700",
  skipped: "border-amber-200 bg-amber-50 text-amber-700",
  canceled: "border-stone-200 bg-stone-100 text-stone-600",
};

export const INFORMATION_ENRICHMENT_JOB_STATUS_TONES: Record<
  ProfessorInformationEnrichmentJobStatus,
  string
> = {
  queued: "border-sky-200 bg-sky-50 text-sky-700",
  running: "border-primary/20 bg-primary/10 text-primary",
  partially_completed: "border-amber-200 bg-amber-50 text-amber-700",
  completed: "border-emerald-200 bg-emerald-50 text-emerald-700",
  failed: "border-red-200 bg-red-50 text-red-700",
  canceled: "border-stone-200 bg-stone-100 text-stone-600",
};

export const INFORMATION_ENRICHMENT_ITEM_STATUS_LABELS: Record<
  ProfessorInformationEnrichmentItemStatus,
  string
> = {
  queued: "排队中",
  running: "补全中",
  succeeded: "已完成",
  failed: "失败",
  skipped: "已跳过",
  canceled: "已取消",
};

export const INFORMATION_ENRICHMENT_ITEM_STATUS_TONES: Record<
  ProfessorInformationEnrichmentItemStatus,
  string
> = {
  queued: "border-sky-200 bg-sky-50 text-sky-700",
  running: "border-primary/20 bg-primary/10 text-primary",
  succeeded: "border-emerald-200 bg-emerald-50 text-emerald-700",
  failed: "border-red-200 bg-red-50 text-red-700",
  skipped: "border-amber-200 bg-amber-50 text-amber-700",
  canceled: "border-stone-200 bg-stone-100 text-stone-600",
};

export const INFORMATION_ENRICHMENT_FIELD_LABELS: Record<string, string> = {
  email: "邮箱",
  title: "职称",
  department: "系所",
  research_direction: "研究方向",
  recent_papers: "近期论文",
};
