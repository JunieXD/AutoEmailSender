import { formatApiDateTime, parseApiDateTime } from "@/lib/dateTime";
import { deriveTextFromEmailHtml, textToEmailHtml } from "@/lib/richEmail";
import type {
  BatchTaskCardDTO,
  BatchTaskItemDTO,
  MatchAnalysisJobItemStatus,
  ProfessorInformationEnrichmentItemStatus,
  WorkspaceThreadDTO,
} from "@/types";

export const CRAWL_REFRESH_INTERVAL_MS = 2000;
export const CRAWL_DETAILS_REFRESH_INTERVAL_MS = 2000;
export const CRAWL_DETAIL_CONTENT_REFRESH_INTERVAL_MS = 10000;
export const BATCH_TASK_DETAILS_REFRESH_INTERVAL_MS = 10000;
export const TASKS_PAGE_SIZE = 8;
export const MONITOR_SECTION_PAGE_SIZE = 5;
export const BATCH_DETAIL_ITEM_PAGE_SIZE = 20;
export const TASKS_PAGE_SIZE_OPTIONS = [8, 16, 32] as const;
export const DETAIL_PAGE_SIZE_OPTIONS = [10, 20, 50] as const;
export const MATCH_JOB_ITEMS_PAGE_CACHE_SIZE = 5;
export const INFORMATION_ENRICHMENT_ITEMS_PAGE_CACHE_SIZE = 5;
export const MONITOR_PAGE_SIZE_OPTIONS = [5, 10, 20] as const;

export const PAGE_SIZE_STORAGE_KEYS = {
  batchTasks: "tasks:batch:page-size",
  crawlJobs: "tasks:crawl:page-size",
  matchJobs: "tasks:match:page-size",
  informationEnrichmentJobs: "tasks:information-enrichment:page-size",
  batchSentItems: "tasks:batch-details:sent:page-size",
  batchPendingItems: "tasks:batch-details:pending:page-size",
  batchGeneratingItems: "tasks:batch-details:generating:page-size",
  batchDraftFailedItems: "tasks:batch-details:draft-failed:page-size",
  batchFailedItems: "tasks:batch-details:failed:page-size",
  batchReviewItems: "tasks:batch-details:review:page-size",
  matchJobItems: "tasks:match-details:items:page-size",
  informationEnrichmentItems:
    "tasks:information-enrichment-details:items:page-size",
  crawlEvents: "tasks:crawl-details:events:page-size",
  crawlPages: "tasks:crawl-details:pages:page-size",
  crawlCandidates: "tasks:crawl-details:candidates:page-size",
} as const;

export const getMatchJobItemsCacheKey = (
  jobId: number,
  cursor: number,
  limit: number,
  status: MatchAnalysisJobItemStatus | "all",
) => `${jobId}:${cursor}:${limit}:${status}`;

export const getInformationEnrichmentItemsCacheKey = (
  jobId: number,
  cursor: number,
  limit: number,
  status: ProfessorInformationEnrichmentItemStatus | "all",
) => `${jobId}:${cursor}:${limit}:${status}`;

const SCHEDULE_DATE_PATTERN = /^\d{4}-(\d{2})-(\d{2})$/;

const formatScheduleDate = (value: string) => {
  const match = SCHEDULE_DATE_PATTERN.exec(value);
  if (!match) {
    return null;
  }
  return `${Number(match[1])}/${Number(match[2])}`;
};

export const buildScheduleLabel = (task: BatchTaskCardDTO) => {
  if (task.schedule_type === "immediate") {
    return "立即执行";
  }
  const dates = (task.scheduled_dates ?? [])
    .filter((date) => SCHEDULE_DATE_PATTERN.test(date))
    .sort();
  if (dates.length > 0) {
    const firstDate = formatScheduleDate(dates[0]);
    const lastDate = formatScheduleDate(dates[dates.length - 1]);
    const dateRange =
      firstDate && lastDate && firstDate !== lastDate
        ? `${firstDate}-${lastDate}`
        : firstDate;
    if (dateRange) {
      return `${dateRange} 共 ${dates.length} 天，${task.window_start_time ?? "--:--"}-${task.window_end_time ?? "--:--"}，每天最多 ${task.emails_per_window ?? 0} 封`;
    }
  }
  return `${task.window_start_time ?? "--:--"} - ${task.window_end_time ?? "--:--"}，窗口内 ${task.emails_per_window ?? 0} 封`;
};

export const formatDisplayTime = (
  value: string | null | undefined,
  options?: { withSeconds?: boolean },
) => {
  if (!value) {
    return "--";
  }
  return formatApiDateTime(
    value,
    options?.withSeconds ? { second: "2-digit" } : undefined,
  );
};

export const isBatchItemScheduledInFuture = (
  item: BatchTaskItemDTO,
  nowMs: number,
) => {
  if (!item.scheduled_at) {
    return false;
  }
  const scheduledAt = parseApiDateTime(item.scheduled_at).getTime();
  return Number.isFinite(scheduledAt) && scheduledAt > nowMs;
};

export const formatDuration = (seconds: number) => {
  const safeSeconds = Math.max(0, seconds);
  const hours = Math.floor(safeSeconds / 3600);
  const minutes = Math.floor((safeSeconds % 3600) / 60);
  const remainingSeconds = safeSeconds % 60;

  if (hours > 0) {
    return `${hours}小时 ${minutes}分 ${remainingSeconds}秒`;
  }
  if (minutes > 0) {
    return `${minutes}分 ${remainingSeconds}秒`;
  }
  return `${remainingSeconds}秒`;
};

export type RichEmailValue = { html: string; text: string };

export const BATCH_REVIEW_DRAFT_SOURCE_LABELS: Record<
  WorkspaceThreadDTO["current_task"]["draft"]["source"],
  string
> = {
  saved: "已保存草稿",
  ai_rewrite: "AI 改写结果",
  template: "来自模板",
  manual_empty: "空草稿",
  rewrite_source: "改写前草稿",
};

const getLatestDraftMessage = (thread: WorkspaceThreadDTO) => {
  for (let index = thread.messages.length - 1; index >= 0; index -= 1) {
    if (thread.messages[index].direction === "draft") {
      return thread.messages[index];
    }
  }
  return null;
};

export const deriveBatchReviewText = (
  content: string | null | undefined,
  html: string | null | undefined,
) => {
  const trimmedContent = content?.trim();
  if (trimmedContent) {
    return trimmedContent;
  }
  const trimmedHtml = html?.trim();
  return trimmedHtml ? deriveTextFromEmailHtml(trimmedHtml) : "";
};

export const getBatchReviewDraft = (thread: WorkspaceThreadDTO) => {
  const latestDraft = getLatestDraftMessage(thread);
  const task = thread.current_task;
  const subject =
    task.approved_subject ??
    task.generated_subject ??
    task.draft?.subject ??
    latestDraft?.subject ??
    task.rendered_template_subject ??
    task.outreach_template_subject ??
    "";
  const html =
    task.approved_body_html ??
    task.generated_content_html ??
    task.draft?.body_html ??
    latestDraft?.content_html ??
    task.rendered_template_body_html ??
    task.outreach_template_body_html ??
    "";
  const text = deriveBatchReviewText(
    task.approved_body_text ??
      task.generated_content_text ??
      task.draft?.body_text ??
      latestDraft?.content ??
      task.rendered_template_body_text ??
      task.outreach_template_body_text,
    html,
  );

  return {
    subject,
    html: html || textToEmailHtml(text),
    text,
    selectedMaterialIds: task.selected_material_ids ?? [],
  };
};
