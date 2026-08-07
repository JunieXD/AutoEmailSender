import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type FormEvent,
  type ReactNode,
} from "react";
import { Link, useNavigate } from "react-router-dom";
import {
  Activity,
  Ban,
  Bot,
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  CheckCircle2,
  Clock3,
  FileSearch,
  Loader2,
  Mail,
  Pause,
  Pencil,
  Play,
  RotateCcw,
  Save,
  Search,
  Sparkles,
  Square,
  SquareCheck,
  SquareMinus,
  Trash2,
  X,
} from "lucide-react";
import { EmailTemplateEditor } from "@/components/molecules/EmailTemplateEditor";
import { AttachmentSizeSummary } from "@/components/molecules/AttachmentSizeSummary";
import { EmailDeliveryFailureDetails } from "@/components/molecules/EmailDeliveryFailureDetails";
import { KeywordSearchScopeSelect } from "@/components/molecules/KeywordSearchScopeSelect";
import { Pagination } from "@/components/molecules/Pagination";
import { SelectionToggleButton } from "@/components/molecules/SelectionToggleButton";
import { ProfessorEditDialog } from "@/components/molecules/ProfessorEditDialog";
import { SubjectTemplateInput } from "@/components/molecules/SubjectTemplateInput";
import { NativeSelectField } from "@/components/atoms/NativeSelectField";
import { useBackgroundTaskNotification } from "@/app/providers/BackgroundTaskNotificationContext";
import { useNotification } from "@/context/NotificationContext";
import { useSelectionContext } from "@/context/SelectionContext";
import { useConfirmDialog } from "@/lib/useConfirmDialog";
import { useDismissableLayerClick } from "@/lib/useDismissableLayerClick";
import { useDocumentScrollLock } from "@/lib/useDocumentScrollLock";
import { safeRecordUserAction } from "@/lib/diagnosticUserActions";
import {
  approveAllBatchTaskDrafts,
  approveAndSendBatchTaskItemDraft,
  approveBatchTaskItemDraft,
  cancelBatchTaskItemSend,
  deleteBatchTask,
  deleteBatchTaskItem,
  getBatchTaskItemThread,
  getBatchTaskResendContext,
  listBatchTasks,
  listBatchTaskItems,
  pauseBatchTask,
  regenerateBatchTaskItemDraft,
  retryBatchTaskItemDraft,
  restoreBatchTaskItemSend,
  restoreBatchTask,
  resumeBatchTask,
  stopBatchTask,
} from "@/lib/api/batchTasksApi";
import {
  writeBatchResendPrefillContext,
  writeSelectedProfessorIdsForBatchTask,
} from "@/features/batch-tasks/client/batchTaskResendPrefill";
import { BatchTaskResendDialog } from "@/features/batch-tasks/components/BatchTaskResendDialog";
import { getEmailSendFailureMessage } from "@/features/email/client/getEmailSendFailureMessage";
import {
  buildBulkLargeAttachmentWarning,
  buildLargeAttachmentWarning,
  formatFileSize,
  getSelectedAttachmentTotalBytes,
  isAttachmentTotalOverRecommendedLimit,
} from "@/features/attachments/attachmentSize";
import {
  cancelMatchAnalysisJob,
  deleteMatchAnalysisJob,
  listMatchAnalysisJobItems,
  listMatchAnalysisJobs,
  restoreMatchAnalysisJob,
  retryFailedMatchAnalysisJob,
} from "@/lib/api/matchAnalysisJobsApi";
import {
  cancelProfessorInformationEnrichmentJob,
  deleteProfessorInformationEnrichmentJob,
  listProfessorInformationEnrichmentItems,
  listProfessorInformationEnrichmentJobs,
  restoreProfessorInformationEnrichmentJob,
  retryFailedProfessorInformationEnrichmentJob,
} from "@/entities/professor/api/informationEnrichment";
import { getProfessor } from "@/entities/professor/api/professors";
import {
  cancelCrawlJob,
  approveCrawlCandidates,
  deleteCrawlJob,
  enrichCrawlCandidates,
  getCrawlJob,
  getCrawlJobEvents,
  listCrawlCandidates,
  listCrawlJobs,
  listCrawlPages,
  pauseCrawlJob,
  retryCrawlJob,
  restoreCrawlJob,
  resumeCrawlJobReview,
  resumeCrawlJob,
  updateCrawlCandidate,
} from "@/lib/api/crawlJobsApi";
import {
  DEFAULT_CRAWL_CANDIDATE_FILTERS,
  filterCrawlCandidates,
  getReviewableCandidateIdsWithoutEmail,
  getReviewableCandidateIds,
  hasActiveCrawlCandidateFilters,
  normalizeCrawlCandidateSearchScopes,
  pruneSelectedCandidateIds,
  type CrawlCandidateFilters,
  type CrawlCandidateInformationCondition,
  type CrawlCandidateInformationField,
  type CrawlCandidateInformationMatchMode,
  type CrawlCandidateReviewStatusFilter,
  type CrawlCandidateSearchScope,
} from "@/features/crawl-review/client/reviewCandidates";
import {
  getCandidateEnrichmentFailureMessage,
  getCrawlEnrichmentCompletionEventKeys,
  getCrawlEventFailureReason,
} from "@/features/crawl-review/client/crawlJobEvents";
import {
  buildBatchPendingItemAction,
  getOutreachGenerationModeLabel,
  getOutreachTemplateSourceLabel,
  getBatchTaskItemCancellationText,
  getBatchTaskWaitingSendCount,
  isBatchTaskItemMissingResearchDirection,
} from "@/features/batch-tasks/client/batchTaskDisplay";
import { formatApiDateTime, parseApiDateTime } from "@/lib/dateTime";
import { getPageItems, getTotalPages } from "@/lib/pagination";
import { usePaginationState } from "@/lib/usePaginationState";
import { useTaskDetailItems } from "@/lib/useTaskDetailItems";
import {
  normalizeExternalHttpUrl,
  openExternalHttpUrl,
} from "@/lib/externalUrls";
import { deriveTextFromEmailHtml, textToEmailHtml } from "@/lib/richEmail";
import {
  BATCH_TASK_STATUS_LABELS,
  MATERIAL_TYPE_LABELS,
  MATCH_ANALYSIS_JOB_STATUS_LABELS,
  PROFESSOR_INFORMATION_ENRICHMENT_STATUS_LABELS,
  PROFESSOR_STATUS_LABELS,
  type BatchTaskCardDTO,
  type BatchTaskItemDTO,
  type BatchTaskResendContextDTO,
  type CrawlCandidateDTO,
  type CrawlCandidateReviewStatusDTO,
  type CrawlCandidateUpdatePayloadDTO,
  type CrawlJobEventDTO,
  type CrawlJobStatusDTO,
  type CrawlJobSummaryDTO,
  type CrawlPageDTO,
  type MatchAnalysisJobDTO,
  type MatchAnalysisJobItemDTO,
  type MatchAnalysisJobItemStatus,
  type MatchAnalysisJobStatus,
  type ProfessorInformationEnrichmentItemDTO,
  type ProfessorInformationEnrichmentItemStatus,
  type ProfessorInformationEnrichmentJobDTO,
  type ProfessorInformationEnrichmentJobStatus,
  type ProfessorDTO,
  type ProfessorManagementItemDTO,
  type TaskListView,
  type WorkspaceTaskStatus,
  type WorkspaceThreadDTO,
} from "@/types";

type TasksTab = "batch" | "crawl" | "match" | "enrichment";
type TaskListViews = Record<TasksTab, TaskListView>;
type BatchReviewItemActionType = "regenerate" | "delete" | "submit";
type BatchReviewItemActions = Record<number, BatchReviewItemActionType>;
type BatchSendItemAction = {
  itemId: number;
  kind: "cancel" | "restore";
};

type CrawlCandidateEditForm = {
  name: string;
  email: string;
  title: string;
  university: string;
  school: string;
  department: string;
  researchDirection: string;
  recentPapers: string;
  profileUrl: string;
  sourceUrl: string;
};

const CRAWL_CANDIDATE_EDIT_INPUT_CLASS =
  "mt-2 w-full rounded-xl border border-stone-200 bg-white px-3 py-2 text-sm text-stone-900 outline-none transition placeholder:text-stone-400 focus:border-primary focus:ring-2 focus:ring-primary/20";

const toCrawlCandidateEditForm = (
  candidate: CrawlCandidateDTO,
): CrawlCandidateEditForm => ({
  name: candidate.name,
  email: candidate.email ?? "",
  title: candidate.title ?? "",
  university: candidate.university ?? "",
  school: candidate.school ?? "",
  department: candidate.department ?? "",
  researchDirection: candidate.research_direction ?? "",
  recentPapers: candidate.recent_papers.join("\n"),
  profileUrl: candidate.profile_url ?? "",
  sourceUrl: candidate.source_url ?? "",
});

const toNullableTrimmedText = (value: string) => value.trim() || null;

const toCrawlCandidateUpdatePayload = (
  candidate: CrawlCandidateDTO,
  form: CrawlCandidateEditForm,
): CrawlCandidateUpdatePayloadDTO => ({
  name: form.name.trim(),
  email: toNullableTrimmedText(form.email),
  title: toNullableTrimmedText(form.title),
  university: toNullableTrimmedText(form.university),
  school: toNullableTrimmedText(form.school),
  department: toNullableTrimmedText(form.department),
  research_direction: toNullableTrimmedText(form.researchDirection),
  recent_papers: form.recentPapers
    .split(/\r?\n/)
    .map((paper) => paper.trim())
    .filter(Boolean),
  profile_url: toNullableTrimmedText(form.profileUrl),
  source_url: toNullableTrimmedText(form.sourceUrl),
  review_status: candidate.review_status,
});

const hasUnsavedCrawlCandidateChanges = (
  candidate: CrawlCandidateDTO,
  form: CrawlCandidateEditForm,
) => {
  const initialForm = toCrawlCandidateEditForm(candidate);
  return (Object.keys(initialForm) as (keyof CrawlCandidateEditForm)[]).some(
    (field) => initialForm[field] !== form[field],
  );
};

const CRAWL_JOB_STATUS_LABELS: Record<CrawlJobStatusDTO, string> = {
  queued: "排队中",
  running: "运行中",
  paused: "已暂停",
  needs_review: "待审核",
  partially_completed: "部分已导入",
  completed: "已完成",
  failed: "失败",
  canceled: "已取消",
};

const CRAWL_JOB_STATUS_TONES: Record<CrawlJobStatusDTO, string> = {
  queued: "border-sky-200 bg-sky-50 text-sky-700",
  running: "border-primary/20 bg-primary/10 text-primary",
  paused: "border-orange-200 bg-orange-50 text-orange-700",
  needs_review: "border-amber-200 bg-amber-50 text-amber-700",
  partially_completed: "border-blue-200 bg-blue-50 text-blue-700",
  completed: "border-emerald-200 bg-emerald-50 text-emerald-700",
  failed: "border-red-200 bg-red-50 text-red-700",
  canceled: "border-stone-200 bg-stone-100 text-stone-600",
};

const CRAWL_CANDIDATE_REVIEW_STATUS_LABELS: Record<
  CrawlCandidateReviewStatusDTO,
  string
> = {
  pending: "待审核",
  accepted: "已通过",
  rejected: "已拒绝",
  merged: "已合并",
};

const CRAWL_CANDIDATE_REVIEW_STATUS_TONES: Record<
  CrawlCandidateReviewStatusDTO,
  string
> = {
  pending: "border-amber-200 bg-amber-50 text-amber-700",
  accepted: "border-emerald-200 bg-emerald-50 text-emerald-700",
  rejected: "border-red-200 bg-red-50 text-red-700",
  merged: "border-sky-200 bg-sky-50 text-sky-700",
};

const CRAWL_CANDIDATE_SEARCH_SCOPE_OPTIONS: ReadonlyArray<{
  value: CrawlCandidateSearchScope;
  label: string;
}> = [
  { value: "name", label: "姓名" },
  { value: "email", label: "邮箱" },
  { value: "organization", label: "学校与任职" },
  { value: "title", label: "职称" },
  { value: "research_direction", label: "研究方向" },
  { value: "recent_papers", label: "近期论文" },
];

const CRAWL_CANDIDATE_INFORMATION_FIELD_OPTIONS: ReadonlyArray<{
  field: CrawlCandidateInformationField;
  label: string;
}> = [
  { field: "email", label: "邮箱" },
  { field: "title", label: "职称" },
  { field: "department", label: "系所" },
  { field: "profile_url", label: "个人主页" },
  { field: "research_direction", label: "研究方向" },
  { field: "recent_papers", label: "近期论文" },
];

const CRAWL_CANDIDATE_INFORMATION_FIELD_LABELS = Object.fromEntries(
  CRAWL_CANDIDATE_INFORMATION_FIELD_OPTIONS.map(({ field, label }) => [
    field,
    label,
  ]),
) as Record<CrawlCandidateInformationField, string>;

const getCrawlCandidateInformationConditionEntries = (
  conditions: CrawlCandidateFilters["informationConditions"],
) =>
  Object.entries(conditions) as Array<
    [CrawlCandidateInformationField, CrawlCandidateInformationCondition]
  >;

const getCrawlCandidateInformationConditionLabel = (
  field: CrawlCandidateInformationField,
  condition: CrawlCandidateInformationCondition,
) =>
  `${condition === "present" ? "有" : "无"}${
    CRAWL_CANDIDATE_INFORMATION_FIELD_LABELS[field]
  }`;

const getCrawlCandidateInformationConditionsSummary = (
  filters: CrawlCandidateFilters,
) => {
  const conditionLabels = getCrawlCandidateInformationConditionEntries(
    filters.informationConditions,
  ).map(([field, condition]) =>
    getCrawlCandidateInformationConditionLabel(field, condition),
  );
  if (conditionLabels.length === 0) {
    return "添加资料条件";
  }

  const connector =
    filters.informationMatchMode === "all" ? " 且 " : " 或 ";
  if (conditionLabels.length <= 2) {
    return conditionLabels.join(connector);
  }
  return `${conditionLabels.slice(0, 2).join(connector)}等 ${
    conditionLabels.length
  } 项`;
};

const getCrawlCandidateSearchPlaceholder = (
  scopes: CrawlCandidateSearchScope[],
) => {
  if (scopes.length !== 1) {
    return "搜索所选字段";
  }
  return `搜索${
    CRAWL_CANDIDATE_SEARCH_SCOPE_OPTIONS.find(
      (option) => option.value === scopes[0],
    )?.label ?? "所选字段"
  }`;
};

const createDefaultCrawlCandidateFilters = (): CrawlCandidateFilters => ({
  ...DEFAULT_CRAWL_CANDIDATE_FILTERS,
  searchScopes: [...DEFAULT_CRAWL_CANDIDATE_FILTERS.searchScopes],
  informationConditions: {},
});

const BATCH_ITEM_STATUS_TONES: Record<WorkspaceTaskStatus, string> = {
  discovered: "bg-stone-100 text-stone-700",
  matched: "bg-sky-50 text-sky-700",
  generating_draft: "bg-sky-50 text-sky-700",
  draft_failed: "bg-red-50 text-red-700",
  review_required: "bg-amber-50 text-amber-700",
  approved: "bg-primary/10 text-primary",
  scheduled: "bg-indigo-50 text-indigo-700",
  sending: "bg-sky-50 text-sky-700",
  sent: "bg-emerald-50 text-emerald-700",
  send_failed: "bg-red-50 text-red-700",
  reply_detected: "bg-emerald-100 text-emerald-800",
  canceled: "bg-stone-100 text-stone-500",
};

const MATCH_ANALYSIS_JOB_STATUS_TONES: Record<
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

const MATCH_ANALYSIS_ITEM_STATUS_LABELS: Record<
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

const MATCH_ANALYSIS_ITEM_STATUS_TONES: Record<
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

const INFORMATION_ENRICHMENT_JOB_STATUS_TONES: Record<
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

const INFORMATION_ENRICHMENT_ITEM_STATUS_LABELS: Record<
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

const INFORMATION_ENRICHMENT_ITEM_STATUS_TONES: Record<
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

const INFORMATION_ENRICHMENT_FIELD_LABELS: Record<string, string> = {
  email: "邮箱",
  title: "职称",
  department: "系所",
  research_direction: "研究方向",
  recent_papers: "近期论文",
};

const CRAWL_REFRESH_INTERVAL_MS = 2000;
const CRAWL_DETAILS_REFRESH_INTERVAL_MS = 2000;
const SCHEDULE_DATE_PATTERN = /^\d{4}-(\d{2})-(\d{2})$/;
const TASKS_PAGE_SIZE = 8;
const MONITOR_SECTION_PAGE_SIZE = 5;
const BATCH_DETAIL_ITEM_PAGE_SIZE = 20;
const TASKS_PAGE_SIZE_OPTIONS = [8, 16, 32] as const;
const DETAIL_PAGE_SIZE_OPTIONS = [10, 20, 50] as const;
const MONITOR_PAGE_SIZE_OPTIONS = [5, 10, 20] as const;
const PAGE_SIZE_STORAGE_KEYS = {
  batchTasks: "tasks:batch:page-size",
  crawlJobs: "tasks:crawl:page-size",
  matchJobs: "tasks:match:page-size",
  informationEnrichmentJobs: "tasks:information-enrichment:page-size",
  batchSentItems: "tasks:batch-details:sent:page-size",
  batchPendingItems: "tasks:batch-details:pending:page-size",
  matchJobItems: "tasks:match-details:items:page-size",
  informationEnrichmentItems:
    "tasks:information-enrichment-details:items:page-size",
  crawlEvents: "tasks:crawl-details:events:page-size",
  crawlPages: "tasks:crawl-details:pages:page-size",
  crawlCandidates: "tasks:crawl-details:candidates:page-size",
} as const;

const formatScheduleDate = (value: string) => {
  const match = SCHEDULE_DATE_PATTERN.exec(value);
  if (!match) {
    return null;
  }
  return `${Number(match[1])}/${Number(match[2])}`;
};

const buildScheduleLabel = (task: BatchTaskCardDTO) => {
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

type TokenUsageBreakdownProps = {
  inputTokens: number;
  outputTokens: number;
  cachedTokens: number;
  totalTokens: number;
  ariaLabel: string;
  variant?: "compact" | "metrics";
  compactLayout?: "spread" | "tight";
  className?: string;
};

type CrawlJobCardProps = {
  job: CrawlJobSummaryDTO;
  listView: TaskListView;
  pausingCrawlJobId: number | null;
  resumingCrawlJobId: number | null;
  retryingCrawlJobId: number | null;
  resumingCrawlJobReviewId: number | null;
  onOpenDetails: (job: CrawlJobSummaryDTO) => void;
  onPause: (jobId: number) => void;
  onResume: (jobId: number) => void;
  onCancel: (jobId: number) => void;
  onRetry: (jobId: number) => void;
  onResumeReview: (jobId: number) => void;
  onDelete: (job: CrawlJobSummaryDTO) => void;
  onRestore: (jobId: number) => void;
  formatUpdatedAt: (value: string) => string;
};

type TaskListViewSwitchProps = {
  activeView: TaskListView;
  onViewChange: (view: TaskListView) => void;
};

const canDeleteCrawlJob = (job: CrawlJobSummaryDTO) =>
  job.status === "needs_review" ||
  job.status === "partially_completed" ||
  job.status === "completed" ||
  job.status === "failed" ||
  job.status === "canceled";

const canDeleteBatchTask = (task: BatchTaskCardDTO) =>
  task.status === "stopped" || task.status === "completed" || task.status === "expired";

const canOpenBatchResend = (task: BatchTaskCardDTO, view: TaskListView) =>
  view === "current" && ["expired", "stopped", "completed"].includes(task.status);

const canDeleteMatchJob = (job: MatchAnalysisJobDTO) =>
  job.status === "completed" ||
  job.status === "partial_failed" ||
  job.status === "failed" ||
  job.status === "canceled";

const canDeleteInformationEnrichmentJob = (
  job: ProfessorInformationEnrichmentJobDTO,
) =>
  job.status === "partially_completed" ||
  job.status === "completed" ||
  job.status === "failed" ||
  job.status === "canceled";

const TokenUsageBreakdown = ({
  inputTokens,
  outputTokens,
  cachedTokens,
  totalTokens,
  ariaLabel,
  variant = "compact",
  compactLayout = "spread",
  className = "",
}: TokenUsageBreakdownProps) => {
  const metrics = [
    { label: variant === "metrics" ? "输入 Token" : "输入", value: inputTokens },
    { label: variant === "metrics" ? "输出 Token" : "输出", value: outputTokens },
    { label: variant === "metrics" ? "缓存命中" : "缓存", value: cachedTokens },
    { label: variant === "metrics" ? "总 Token" : "总计", value: totalTokens },
  ];

  return (
    <dl
      aria-label={ariaLabel}
      className={`${
        variant === "metrics"
          ? "grid grid-cols-2 gap-3 sm:grid-cols-4"
          : compactLayout === "tight"
            ? "inline-grid grid-cols-2 gap-x-3 gap-y-1.5 text-xs tabular-nums"
            : "grid grid-cols-2 gap-x-4 gap-y-1.5 text-xs tabular-nums"
      } ${className}`}
    >
      {metrics.map((metric) => (
        <div
          key={metric.label}
          className={
            variant === "metrics"
              ? "rounded-lg border border-stone-100 bg-white px-4 py-3"
              : compactLayout === "tight"
                ? "flex min-w-0 items-baseline gap-1.5"
                : "flex min-w-0 items-baseline justify-between gap-2"
          }
        >
          <dt className="whitespace-nowrap font-medium text-stone-500">
            {metric.label}
          </dt>
          <dd
            className={
              variant === "metrics"
                ? "mt-2 text-sm font-semibold text-stone-900 tabular-nums"
                : "truncate font-semibold text-stone-800"
            }
            title={metric.value.toLocaleString("zh-CN")}
          >
            {metric.value.toLocaleString("zh-CN")}
          </dd>
        </div>
      ))}
    </dl>
  );
};

export const TaskListViewSwitch = ({
  activeView,
  onViewChange,
}: TaskListViewSwitchProps) => (
  <div data-testid="task-list-view-switch" className="flex justify-end">
    <div className="inline-flex gap-1 rounded-2xl border border-stone-200 bg-white p-1 shadow-sm">
      {(["current", "trash"] as TaskListView[]).map((view) => (
        <button
          key={view}
          type="button"
          onClick={() => onViewChange(view)}
          className={
            activeView === view
              ? "inline-flex min-h-9 items-center rounded-xl bg-primary px-4 text-sm font-medium text-white shadow-sm shadow-primary/20"
              : "inline-flex min-h-9 items-center rounded-xl px-4 text-sm font-medium text-stone-600 hover:bg-stone-50"
          }
        >
          {view === "current" ? "当前任务" : "回收站"}
        </button>
      ))}
    </div>
  </div>
);

export const CrawlJobCard = ({
  job,
  listView,
  pausingCrawlJobId,
  resumingCrawlJobId,
  retryingCrawlJobId,
  resumingCrawlJobReviewId,
  onOpenDetails,
  onPause,
  onResume,
  onCancel,
  onRetry,
  onResumeReview,
  onDelete,
  onRestore,
  formatUpdatedAt,
}: CrawlJobCardProps) => (
  <article className="rounded-2xl border border-stone-200 bg-white px-5 py-5 shadow-sm">
    <div
      data-testid="crawl-job-card-layout"
      className="flex flex-col gap-4 xl:flex-row xl:items-center xl:justify-between"
    >
      <div className="min-w-0 flex-1">
        <div
          data-testid="crawl-job-card-info-grid"
          className="grid gap-4 lg:grid-cols-[minmax(0,1.3fr)_240px] xl:grid-cols-[minmax(320px,1.3fr)_240px_minmax(280px,0.95fr)] xl:items-center"
        >
          <div className="min-w-0 xl:min-w-[20rem]">
            <div className="flex flex-wrap items-center gap-2">
              <div className="flex items-center gap-2 text-xs font-medium text-stone-500">
                <Bot className="h-4 w-4 text-primary" />
                智能抓取任务
              </div>
              <span
                className={`shrink-0 rounded-full border px-2.5 py-1 text-xs font-medium ${CRAWL_JOB_STATUS_TONES[job.status]}`}
              >
                {CRAWL_JOB_STATUS_LABELS[job.status]}
              </span>
            </div>
            <h2
              className="mt-2 truncate text-base font-semibold text-stone-900"
              title={`${job.university} / ${job.school}`}
            >
              {job.university} / {job.school}
            </h2>
            <p
              className="mt-1 truncate text-sm text-stone-500"
              title={job.start_url}
            >
              {job.start_url}
            </p>
          </div>

          <div className="grid grid-cols-2 gap-3">
            <div className="rounded-2xl border border-stone-100 bg-stone-50/60 px-4 py-3">
              <div className="text-xs font-medium text-stone-500">页面</div>
              <div className="mt-2 text-sm font-semibold text-stone-900">
                已抓页面 {job.page_count}
              </div>
            </div>
            <div className="rounded-2xl border border-stone-100 bg-stone-50/60 px-4 py-3">
              <div className="text-xs font-medium text-stone-500">候选</div>
              <div className="mt-2 text-sm font-semibold text-stone-900">
                候选导师 {job.candidate_count}
              </div>
            </div>
          </div>

          <div className="min-w-0">
            <div className="text-xs font-medium text-stone-500">
              更新 {formatUpdatedAt(job.updated_at)}
            </div>
            {job.latest_event_message ? (
              <div className="mt-2 flex items-start gap-2 rounded-2xl border border-primary/10 bg-primary/5 px-3 py-2 text-sm text-stone-700">
                <Activity className="mt-0.5 h-4 w-4 shrink-0 text-primary" />
                <p
                  data-testid="crawl-job-card-latest-event"
                  className="min-w-0 break-all line-clamp-2"
                  title={job.latest_event_message}
                >
                  {job.latest_event_message}
                </p>
              </div>
            ) : (
              <p className="mt-2 text-sm text-stone-500">暂无最新事件</p>
            )}
          </div>
        </div>
      </div>

      <div className="flex flex-wrap items-center gap-2 xl:ml-4 xl:max-w-[18rem] xl:justify-end">
        {listView === "trash" ? (
          <button
            type="button"
            onClick={() => onRestore(job.id)}
            className="ui-btn-primary"
          >
            <RotateCcw className="h-4 w-4" />
            还原任务
          </button>
        ) : null}
        {listView === "current" && canDeleteCrawlJob(job) ? (
          <button
            type="button"
            onClick={() => onDelete(job)}
            className="ui-btn-danger"
          >
            <Trash2 className="h-4 w-4" />
            删除
          </button>
        ) : null}
        {listView === "current" &&
        (job.status === "queued" || job.status === "running") ? (
          <>
            <button
              type="button"
              onClick={() => onPause(job.id)}
              disabled={pausingCrawlJobId === job.id}
              className="ui-btn-secondary disabled:cursor-not-allowed disabled:opacity-60"
            >
              <Pause className="h-4 w-4" />
              {pausingCrawlJobId === job.id ? "暂停中..." : "暂停抓取"}
            </button>
            <button
              type="button"
              onClick={() => onCancel(job.id)}
              className="ui-btn-danger"
            >
              <Square className="h-4 w-4" />
              取消抓取
            </button>
          </>
        ) : null}
        {listView === "current" && job.status === "paused" ? (
          <>
            <button
              type="button"
              onClick={() => onResume(job.id)}
              disabled={resumingCrawlJobId === job.id}
              className="ui-btn-primary disabled:cursor-not-allowed disabled:opacity-60"
            >
              <Play className="h-4 w-4" />
              {resumingCrawlJobId === job.id ? "继续中..." : "继续抓取"}
            </button>
            <button
              type="button"
              onClick={() => onCancel(job.id)}
              className="ui-btn-danger"
            >
              <Square className="h-4 w-4" />
              取消抓取
            </button>
          </>
        ) : null}
        {listView === "current" &&
        (job.status === "failed" || job.status === "canceled") ? (
          <>
            <button
              type="button"
              onClick={() => onRetry(job.id)}
              disabled={retryingCrawlJobId === job.id}
              className="ui-btn-primary disabled:cursor-not-allowed disabled:opacity-60"
            >
              <Play className="h-4 w-4" />
              {retryingCrawlJobId === job.id ? "重新抓取中..." : "重新抓取"}
            </button>
            <button
              type="button"
              onClick={() => onResumeReview(job.id)}
              disabled={resumingCrawlJobReviewId === job.id}
              className="ui-btn-secondary disabled:cursor-not-allowed disabled:opacity-60"
            >
              <CheckCircle2 className="h-4 w-4" />
              {resumingCrawlJobReviewId === job.id
                ? "转入中..."
                : "转入待审核"}
            </button>
          </>
        ) : null}
        <button
          type="button"
          onClick={() => onOpenDetails(job)}
          className="inline-flex h-10 w-10 items-center justify-center rounded-full border border-stone-200 bg-white text-stone-500 transition hover:border-primary/30 hover:bg-primary/5 hover:text-primary"
          aria-label="查看详情"
          title="查看详情"
        >
          <ChevronRight className="h-4 w-4" />
        </button>
      </div>
    </div>
  </article>
);

const formatDisplayTime = (
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

const isBatchItemScheduledInFuture = (
  item: BatchTaskItemDTO,
  nowMs: number,
) => {
  if (!item.scheduled_at) {
    return false;
  }
  const scheduledAt = parseApiDateTime(item.scheduled_at).getTime();
  return Number.isFinite(scheduledAt) && scheduledAt > nowMs;
};

const formatDuration = (seconds: number) => {
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

type RichEmailValue = { html: string; text: string };

const getLatestDraftMessage = (thread: WorkspaceThreadDTO) => {
  for (let index = thread.messages.length - 1; index >= 0; index -= 1) {
    if (thread.messages[index].direction === "draft") {
      return thread.messages[index];
    }
  }
  return null;
};

const deriveBatchReviewText = (content: string | null | undefined, html: string | null | undefined) => {
  const trimmedContent = content?.trim();
  if (trimmedContent) {
    return trimmedContent;
  }
  const trimmedHtml = html?.trim();
  return trimmedHtml ? deriveTextFromEmailHtml(trimmedHtml) : "";
};

const getBatchReviewDraft = (thread: WorkspaceThreadDTO) => {
  const latestDraft = getLatestDraftMessage(thread);
  const task = thread.current_task;
  const subject =
    task.approved_subject ??
    task.generated_subject ??
    latestDraft?.subject ??
    "";
  const html =
    task.approved_body_html ??
    task.generated_content_html ??
    latestDraft?.content_html ??
    task.outreach_template_body_html ??
    "";
  const text = deriveBatchReviewText(
    task.approved_body_text ??
      task.generated_content_text ??
      latestDraft?.content ??
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

export const TasksPage = () => {
  const navigate = useNavigate();
  const {
    identities = [],
    selectedIdentityId,
    selectedLlmProfileId,
    setSelectedIdentityId,
  } = useSelectionContext();
  const { notifyError, notifySuccess } = useNotification();
  const {
    stopTrackingInformationEnrichmentJob,
    trackCrawlCandidateEnrichment,
    trackCrawlJob,
    trackInformationEnrichmentJob,
    trackMatchAnalysisJob,
  } = useBackgroundTaskNotification();
  const { confirm, dialog: confirmDialog } = useConfirmDialog();
  const hasTaskSelection = selectedIdentityId !== null;
  const [activeTab, setActiveTab] = useState<TasksTab>(() =>
    hasTaskSelection ? "batch" : "crawl",
  );
  const [taskListViews, setTaskListViews] = useState<TaskListViews>({
    batch: "current",
    crawl: "current",
    match: "current",
    enrichment: "current",
  });
  const [tasks, setTasks] = useState<BatchTaskCardDTO[]>([]);
  const [currentBatchTasks, setCurrentBatchTasks] = useState<BatchTaskCardDTO[]>([]);
  const [selectedBatchTask, setSelectedBatchTask] =
    useState<BatchTaskCardDTO | null>(null);
  const [selectedBatchTaskItems, setSelectedBatchTaskItems] = useState<
    BatchTaskItemDTO[]
  >([]);
  const [professorEditDialogOpen, setProfessorEditDialogOpen] = useState(false);
  const [professorEditLoading, setProfessorEditLoading] = useState(false);
  const [professorEditProfessor, setProfessorEditProfessor] =
    useState<ProfessorDTO | null>(null);
  const [batchTaskDetailsLoading, setBatchTaskDetailsLoading] = useState(false);
  const [resendContext, setResendContext] = useState<BatchTaskResendContextDTO | null>(null);
  const [resendLoading, setResendLoading] = useState(false);
  const [resendDialogOpen, setResendDialogOpen] = useState(false);
  const [selectedResendProfessorIds, setSelectedResendProfessorIds] = useState<number[]>([]);
  const [batchReviewItemId, setBatchReviewItemId] = useState<number | null>(null);
  const [batchReviewThread, setBatchReviewThread] =
    useState<WorkspaceThreadDTO | null>(null);
  const [batchReviewLoading, setBatchReviewLoading] = useState(false);
  const [batchBulkApprovalLoading, setBatchBulkApprovalLoading] = useState(false);
  const [batchReviewItemActions, setBatchReviewItemActions] =
    useState<BatchReviewItemActions>({});
  const [batchSendItemAction, setBatchSendItemAction] =
    useState<BatchSendItemAction | null>(null);
  const [batchSendActionNowMs, setBatchSendActionNowMs] = useState(() =>
    Date.now(),
  );
  const [batchReviewSubject, setBatchReviewSubject] = useState("");
  const [batchReviewContentText, setBatchReviewContentText] = useState("");
  const [batchReviewContentHtml, setBatchReviewContentHtml] = useState("");
  const [batchReviewSelectedMaterialIds, setBatchReviewSelectedMaterialIds] =
    useState<number[]>([]);
  const [loading, setLoading] = useState(false);
  const [matchAnalysisJobs, setMatchAnalysisJobs] = useState<
    MatchAnalysisJobDTO[]
  >([]);
  const [currentMatchAnalysisJobs, setCurrentMatchAnalysisJobs] = useState<
    MatchAnalysisJobDTO[]
  >([]);
  const [matchJobsLoading, setMatchJobsLoading] = useState(false);
  const [selectedMatchJob, setSelectedMatchJob] =
    useState<MatchAnalysisJobDTO | null>(null);
  const [selectedMatchJobItems, setSelectedMatchJobItems] = useState<
    MatchAnalysisJobItemDTO[]
  >([]);
  const [matchJobDetailsLoading, setMatchJobDetailsLoading] = useState(false);
  const [informationEnrichmentJobs, setInformationEnrichmentJobs] = useState<
    ProfessorInformationEnrichmentJobDTO[]
  >([]);
  const [currentInformationEnrichmentJobs, setCurrentInformationEnrichmentJobs] =
    useState<ProfessorInformationEnrichmentJobDTO[]>([]);
  const [informationEnrichmentJobsLoading, setInformationEnrichmentJobsLoading] =
    useState(false);
  const [selectedInformationEnrichmentJob, setSelectedInformationEnrichmentJob] =
    useState<ProfessorInformationEnrichmentJobDTO | null>(null);
  const [selectedInformationEnrichmentItems, setSelectedInformationEnrichmentItems] =
    useState<ProfessorInformationEnrichmentItemDTO[]>([]);
  const [informationEnrichmentDetailsLoading, setInformationEnrichmentDetailsLoading] =
    useState(false);
  const {
    filteredItems: filteredMatchJobItems,
    page: matchJobItemPage,
    pageSize: matchJobItemPageSize,
    setPagination: setMatchJobItemPagination,
    setStatusFilter: setMatchJobItemStatusFilter,
    statusFilter: matchJobItemStatusFilter,
    visibleItems: visibleMatchJobItems,
  } = useTaskDetailItems(
    selectedMatchJobItems,
    selectedMatchJob?.id ?? null,
    {
      initialPageSize: 10,
      pageSizeStorageKey: PAGE_SIZE_STORAGE_KEYS.matchJobItems,
    },
  );
  const {
    filteredItems: filteredInformationEnrichmentItems,
    page: informationEnrichmentItemPage,
    pageSize: informationEnrichmentItemPageSize,
    setPagination: setInformationEnrichmentItemPagination,
    setStatusFilter: setInformationEnrichmentItemStatusFilter,
    statusFilter: informationEnrichmentItemStatusFilter,
    visibleItems: visibleInformationEnrichmentItems,
  } = useTaskDetailItems(
    selectedInformationEnrichmentItems,
    selectedInformationEnrichmentJob?.id ?? null,
    {
      initialPageSize: 10,
      pageSizeStorageKey:
        PAGE_SIZE_STORAGE_KEYS.informationEnrichmentItems,
    },
  );
  const [crawlJobs, setCrawlJobs] = useState<CrawlJobSummaryDTO[]>([]);
  const [currentCrawlJobs, setCurrentCrawlJobs] = useState<CrawlJobSummaryDTO[]>([]);
  const [crawlJobsLoading, setCrawlJobsLoading] = useState(false);
  const {
    page: batchPage,
    pageSize: batchPageSize,
    setPage: setBatchPage,
    onChange: handleBatchPaginationChange,
  } = usePaginationState({
    storageKey: PAGE_SIZE_STORAGE_KEYS.batchTasks,
    initialPageSize: TASKS_PAGE_SIZE,
  });
  const {
    page: matchPage,
    pageSize: matchPageSize,
    setPage: setMatchPage,
    onChange: handleMatchPaginationChange,
  } = usePaginationState({
    storageKey: PAGE_SIZE_STORAGE_KEYS.matchJobs,
    initialPageSize: TASKS_PAGE_SIZE,
  });
  const {
    page: informationEnrichmentPage,
    pageSize: informationEnrichmentPageSize,
    setPage: setInformationEnrichmentPage,
    onChange: handleInformationEnrichmentPaginationChange,
  } = usePaginationState({
    storageKey: PAGE_SIZE_STORAGE_KEYS.informationEnrichmentJobs,
    initialPageSize: TASKS_PAGE_SIZE,
  });
  const {
    page: crawlPage,
    pageSize: crawlPageSize,
    setPage: setCrawlPage,
    onChange: handleCrawlPaginationChange,
  } = usePaginationState({
    storageKey: PAGE_SIZE_STORAGE_KEYS.crawlJobs,
    initialPageSize: TASKS_PAGE_SIZE,
  });
  const {
    page: batchSentItemPage,
    pageSize: batchSentItemPageSize,
    setPage: setBatchSentItemPage,
    onChange: handleBatchSentItemPaginationChange,
  } = usePaginationState({
    storageKey: PAGE_SIZE_STORAGE_KEYS.batchSentItems,
    initialPageSize: BATCH_DETAIL_ITEM_PAGE_SIZE,
  });
  const {
    page: batchPendingItemPage,
    pageSize: batchPendingItemPageSize,
    setPage: setBatchPendingItemPage,
    onChange: handleBatchPendingItemPaginationChange,
  } = usePaginationState({
    storageKey: PAGE_SIZE_STORAGE_KEYS.batchPendingItems,
    initialPageSize: BATCH_DETAIL_ITEM_PAGE_SIZE,
  });
  const {
    page: crawlEventPage,
    pageSize: crawlEventPageSize,
    setPage: setCrawlEventPage,
    onChange: handleCrawlEventPaginationChange,
  } = usePaginationState({
    storageKey: PAGE_SIZE_STORAGE_KEYS.crawlEvents,
    initialPageSize: MONITOR_SECTION_PAGE_SIZE,
  });
  const {
    page: crawlDetailPagePage,
    pageSize: crawlDetailPagePageSize,
    setPage: setCrawlDetailPagePage,
    onChange: handleCrawlDetailPagePaginationChange,
  } = usePaginationState({
    storageKey: PAGE_SIZE_STORAGE_KEYS.crawlPages,
    initialPageSize: MONITOR_SECTION_PAGE_SIZE,
  });
  const {
    page: crawlCandidatePage,
    pageSize: crawlCandidatePageSize,
    setPage: setCrawlCandidatePage,
    onChange: handleCrawlCandidatePaginationChange,
  } = usePaginationState({
    storageKey: PAGE_SIZE_STORAGE_KEYS.crawlCandidates,
    initialPageSize: MONITOR_SECTION_PAGE_SIZE,
  });
  const [selectedCrawlJob, setSelectedCrawlJob] =
    useState<CrawlJobSummaryDTO | null>(null);
  const [crawlJobPages, setCrawlJobPages] = useState<CrawlPageDTO[]>([]);
  const [crawlJobCandidates, setCrawlJobCandidates] = useState<
    CrawlCandidateDTO[]
  >([]);
  const [crawlCandidateFilters, setCrawlCandidateFilters] =
    useState<CrawlCandidateFilters>(createDefaultCrawlCandidateFilters);
  const [crawlCandidateInformationFiltersOpen, setCrawlCandidateInformationFiltersOpen] =
    useState(false);
  const [crawlJobEvents, setCrawlJobEvents] = useState<CrawlJobEventDTO[]>([]);
  const [crawlJobDetailsLoading, setCrawlJobDetailsLoading] = useState(false);
  const [selectedCrawlCandidateIds, setSelectedCrawlCandidateIds] = useState<
    number[]
  >([]);
  const [crawlJobApproveLoading, setCrawlJobApproveLoading] = useState(false);
  const [crawlJobEnrichLoading, setCrawlJobEnrichLoading] = useState(false);
  const [retryingCrawlJobId, setRetryingCrawlJobId] = useState<number | null>(
    null,
  );
  const [resumingCrawlJobReviewId, setResumingCrawlJobReviewId] = useState<
    number | null
  >(null);
  const [cancelingMatchJobId, setCancelingMatchJobId] = useState<number | null>(
    null,
  );
  const [retryingMatchJobId, setRetryingMatchJobId] = useState<number | null>(
    null,
  );
  const [cancelingInformationEnrichmentJobId, setCancelingInformationEnrichmentJobId] =
    useState<number | null>(null);
  const [retryingInformationEnrichmentJobId, setRetryingInformationEnrichmentJobId] =
    useState<number | null>(null);
  const [pausingCrawlJobId, setPausingCrawlJobId] = useState<number | null>(
    null,
  );
  const [resumingCrawlJobId, setResumingCrawlJobId] = useState<number | null>(
    null,
  );
  const [selectedCandidateDetail, setSelectedCandidateDetail] =
    useState<CrawlCandidateDTO | null>(null);
  const [candidateEditForm, setCandidateEditForm] =
    useState<CrawlCandidateEditForm | null>(null);
  const [candidateUpdateLoading, setCandidateUpdateLoading] = useState(false);
  const lastLoadErrorRef = useRef<string | null>(null);
  const lastBatchTaskDetailsLoadErrorRef = useRef<string | null>(null);
  const lastMatchJobsLoadErrorRef = useRef<string | null>(null);
  const lastMatchJobDetailsLoadErrorRef = useRef<string | null>(null);
  const lastInformationEnrichmentJobsLoadErrorRef = useRef<string | null>(null);
  const lastInformationEnrichmentDetailsLoadErrorRef = useRef<string | null>(null);
  const lastCrawlJobsLoadErrorRef = useRef<string | null>(null);
  const lastCrawlJobDetailsLoadErrorRef = useRef<string | null>(null);
  const loadedTasksKeyRef = useRef<string | null>(null);
  const crawlJobsPreloadedRef = useRef(false);
  const batchTasksPreloadedKeyRef = useRef<string | null>(null);
  const matchJobsPreloadedKeyRef = useRef<string | null>(null);
  const informationEnrichmentJobsPreloadedRef = useRef(false);
  const activeTasksRequestKeyRef = useRef<string | null>(null);
  const previousTaskListViewsRef = useRef(taskListViews);
  const previousSelectedBatchTaskIdRef = useRef(selectedBatchTask?.id);
  const previousSelectedCrawlJobIdRef = useRef(selectedCrawlJob?.id ?? null);
  const latestTasksRequestIdRef = useRef(0);
  const latestBatchTaskDetailsRequestIdRef = useRef(0);
  const latestBatchReviewRequestIdRef = useRef(0);
  const latestProfessorEditRequestIdRef = useRef(0);
  const latestMatchJobsRequestIdRef = useRef(0);
  const latestMatchJobDetailsRequestIdRef = useRef(0);
  const latestInformationEnrichmentJobsRequestIdRef = useRef(0);
  const latestInformationEnrichmentDetailsRequestIdRef = useRef(0);
  const latestCrawlJobsRequestIdRef = useRef(0);
  const latestCrawlJobDetailsRequestIdRef = useRef(0);
  const taskListStartRef = useRef<HTMLElement | null>(null);
  const batchSentItemsStartRef = useRef<HTMLElement | null>(null);
  const batchPendingItemsStartRef = useRef<HTMLElement | null>(null);
  const matchJobItemsStartRef = useRef<HTMLElement | null>(null);
  const informationEnrichmentItemsStartRef = useRef<HTMLElement | null>(null);
  const crawlEventsStartRef = useRef<HTMLElement | null>(null);
  const crawlPagesStartRef = useRef<HTMLElement | null>(null);
  const crawlCandidatesStartRef = useRef<HTMLElement | null>(null);
  const crawlCandidateFirstItemRef = useRef<HTMLDivElement | null>(null);
  const activeTaskListView = taskListViews[activeTab];
  const tasksRequestKey =
    selectedIdentityId
      ? `${selectedIdentityId}:${taskListViews.batch}`
      : null;
  const renderCandidateExternalUrl = useCallback(
    (url: string | null) => {
      const normalizedUrl = url?.trim();
      if (!normalizedUrl) {
        return "暂无";
      }

      return (
        <a
          href={normalizedUrl}
          target="_blank"
          rel="noreferrer"
          onClick={(event) => {
            if (
              !window.autoEmailSender?.openExternalUrl ||
              !normalizeExternalHttpUrl(normalizedUrl)
            ) {
              return;
            }

            event.preventDefault();
            openExternalHttpUrl(normalizedUrl);
          }}
          className="inline-flex max-w-full items-center gap-1.5 align-bottom text-primary underline-offset-4 hover:underline"
        >
          <span className="truncate">{normalizedUrl}</span>
        </a>
      );
    },
    [],
  );
  const batchRunningCount = useMemo(
    () => currentBatchTasks.filter((task) => task.status === "running").length,
    [currentBatchTasks],
  );
  const batchAttentionCount = useMemo(
    () =>
      currentBatchTasks.reduce(
        (total, task) =>
          total + task.review_required_count + task.draft_failed_count + task.failed_count,
        0,
      ),
    [currentBatchTasks],
  );
  const crawlRunningCount = useMemo(
    () =>
      currentCrawlJobs.filter(
        (job) => job.status === "queued" || job.status === "running",
      ).length,
    [currentCrawlJobs],
  );
  const crawlReviewCount = useMemo(
    () => currentCrawlJobs.filter((job) => job.status === "needs_review").length,
    [currentCrawlJobs],
  );
  const matchRunningCount = useMemo(
    () =>
      currentMatchAnalysisJobs.filter(
        (job) => job.status === "queued" || job.status === "running",
      ).length,
    [currentMatchAnalysisJobs],
  );
  const matchAttentionCount = useMemo(
    () =>
      currentMatchAnalysisJobs.filter(
        (job) => job.status === "partial_failed" || job.status === "failed",
      ).length,
    [currentMatchAnalysisJobs],
  );
  const informationEnrichmentRunningCount = useMemo(
    () =>
      currentInformationEnrichmentJobs.filter(
        (job) => job.status === "queued" || job.status === "running",
      ).length,
    [currentInformationEnrichmentJobs],
  );
  const informationEnrichmentAttentionCount = useMemo(
    () =>
      currentInformationEnrichmentJobs.filter(
        (job) =>
          job.status === "partially_completed" || job.status === "failed",
      ).length,
    [currentInformationEnrichmentJobs],
  );
  const totalRunningCount =
    batchRunningCount +
    crawlRunningCount +
    matchRunningCount +
    informationEnrichmentRunningCount;
  const totalAttentionCount =
    batchAttentionCount +
    crawlReviewCount +
    matchAttentionCount +
    informationEnrichmentAttentionCount;
  const sentBatchTaskItems = useMemo(
    () =>
      selectedBatchTaskItems.filter(
        (item) => item.status === "sent" || item.status === "reply_detected",
      ),
    [selectedBatchTaskItems],
  );
  const pendingBatchTaskItems = useMemo(
    () =>
      selectedBatchTaskItems.filter(
        (item) =>
          item.batch_send_canceled_at !== null ||
          (item.status === "canceled" &&
            (item.cancellation_reason === "batch_stopped" ||
              item.cancellation_reason === "schedule_expired")) ||
          (item.status !== "sent" &&
            item.status !== "reply_detected" &&
            item.status !== "generating_draft" &&
            item.status !== "draft_failed" &&
            item.status !== "send_failed" &&
            item.status !== "canceled"),
      ),
    [selectedBatchTaskItems],
  );
  const generatingDraftBatchTaskItems = useMemo(
    () =>
      selectedBatchTaskItems.filter(
        (item) =>
          item.batch_send_canceled_at === null &&
          item.status === "generating_draft",
      ),
    [selectedBatchTaskItems],
  );
  const draftFailedBatchTaskItems = useMemo(
    () =>
      selectedBatchTaskItems.filter(
        (item) =>
          item.batch_send_canceled_at === null && item.status === "draft_failed",
      ),
    [selectedBatchTaskItems],
  );
  const failedBatchTaskItems = useMemo(
    () =>
      selectedBatchTaskItems.filter((item) => item.status === "send_failed"),
    [selectedBatchTaskItems],
  );
  const reviewRequiredBatchTaskItems = useMemo(
    () =>
      selectedBatchTaskItems.filter(
        (item) =>
          item.batch_send_canceled_at === null &&
          item.status === "review_required",
      ),
    [selectedBatchTaskItems],
  );
  const templateFallbackReviewCount = useMemo(
    () =>
      reviewRequiredBatchTaskItems.filter(
        (item) => item.draft_generation_source === "template_fallback",
      ).length,
    [reviewRequiredBatchTaskItems],
  );
  const batchReviewQueueItems = useMemo(
    () =>
      selectedBatchTaskItems.filter(
        (item) =>
          item.batch_send_canceled_at === null &&
          (item.status === "review_required" ||
            item.status === "generating_draft"),
      ),
    [selectedBatchTaskItems],
  );
  const activeBatchReviewItem = useMemo(
    () =>
      selectedBatchTaskItems.find((item) => item.id === batchReviewItemId) ??
      null,
    [batchReviewItemId, selectedBatchTaskItems],
  );
  const selectedBatchWaitingSendCount = selectedBatchTask
    ? getBatchTaskWaitingSendCount(selectedBatchTask)
    : 0;
  const selectedBatchNeedsManualItems = useMemo(
    () =>
      selectedBatchTaskItems.filter(
        (item) =>
          item.batch_send_canceled_at === null &&
          (item.next_action === "complete_professor_profile" ||
            item.next_action === "select_primary_material" ||
            item.next_action === "review_draft" ||
            item.next_action === "missing_schedule" ||
            item.next_action === "retry_draft_generation"),
      ),
    [selectedBatchTaskItems],
  );
  const safeBatchPage = Math.min(
    batchPage,
    getTotalPages(tasks.length, batchPageSize),
  );
  const safeCrawlPage = Math.min(
    crawlPage,
    getTotalPages(crawlJobs.length, crawlPageSize),
  );
  const safeMatchPage = Math.min(
    matchPage,
    getTotalPages(matchAnalysisJobs.length, matchPageSize),
  );
  const safeInformationEnrichmentPage = Math.min(
    informationEnrichmentPage,
    getTotalPages(
      informationEnrichmentJobs.length,
      informationEnrichmentPageSize,
    ),
  );
  const safeBatchSentItemPage = Math.min(
    batchSentItemPage,
    getTotalPages(sentBatchTaskItems.length, batchSentItemPageSize),
  );
  const safeBatchPendingItemPage = Math.min(
    batchPendingItemPage,
    getTotalPages(pendingBatchTaskItems.length, batchPendingItemPageSize),
  );
  const safeCrawlEventPage = Math.min(
    crawlEventPage,
    getTotalPages(crawlJobEvents.length, crawlEventPageSize),
  );
  const safeCrawlDetailPagePage = Math.min(
    crawlDetailPagePage,
    getTotalPages(crawlJobPages.length, crawlDetailPagePageSize),
  );
  const filteredCrawlJobCandidates = useMemo(
    () => filterCrawlCandidates(crawlJobCandidates, crawlCandidateFilters),
    [crawlCandidateFilters, crawlJobCandidates],
  );
  const safeCrawlCandidatePage = Math.min(
    crawlCandidatePage,
    getTotalPages(filteredCrawlJobCandidates.length, crawlCandidatePageSize),
  );
  const hasActiveBatchRestoreDeadline = useMemo(
    () =>
      selectedBatchTaskItems.some(
        (item) =>
          item.batch_send_canceled_at !== null &&
          item.can_restore_send &&
          isBatchItemScheduledInFuture(item, batchSendActionNowMs),
      ),
    [batchSendActionNowMs, selectedBatchTaskItems],
  );
  const visibleSentBatchTaskItems = useMemo(
    () =>
      getPageItems(
        sentBatchTaskItems,
        safeBatchSentItemPage,
        batchSentItemPageSize,
      ),
    [batchSentItemPageSize, safeBatchSentItemPage, sentBatchTaskItems],
  );
  const visiblePendingBatchTaskItems = useMemo(
    () =>
      getPageItems(
        pendingBatchTaskItems,
        safeBatchPendingItemPage,
        batchPendingItemPageSize,
      ),
    [batchPendingItemPageSize, pendingBatchTaskItems, safeBatchPendingItemPage],
  );
  const visibleBatchTasks = useMemo(
    () => getPageItems(tasks, safeBatchPage, batchPageSize),
    [batchPageSize, safeBatchPage, tasks],
  );
  const visibleCrawlJobs = useMemo(
    () => getPageItems(crawlJobs, safeCrawlPage, crawlPageSize),
    [crawlJobs, crawlPageSize, safeCrawlPage],
  );
  const visibleMatchJobs = useMemo(
    () => getPageItems(matchAnalysisJobs, safeMatchPage, matchPageSize),
    [matchAnalysisJobs, matchPageSize, safeMatchPage],
  );
  const visibleInformationEnrichmentJobs = useMemo(
    () =>
      getPageItems(
        informationEnrichmentJobs,
        safeInformationEnrichmentPage,
        informationEnrichmentPageSize,
      ),
    [
      informationEnrichmentJobs,
      informationEnrichmentPageSize,
      safeInformationEnrichmentPage,
    ],
  );
  const visibleCrawlJobEvents = useMemo(
    () =>
      getPageItems(crawlJobEvents, safeCrawlEventPage, crawlEventPageSize),
    [crawlEventPageSize, crawlJobEvents, safeCrawlEventPage],
  );
  const visibleCrawlJobPages = useMemo(
    () =>
      getPageItems(
        crawlJobPages,
        safeCrawlDetailPagePage,
        crawlDetailPagePageSize,
      ),
    [crawlDetailPagePageSize, crawlJobPages, safeCrawlDetailPagePage],
  );
  const visibleCrawlJobCandidates = useMemo(
    () =>
      getPageItems(
        filteredCrawlJobCandidates,
        safeCrawlCandidatePage,
        crawlCandidatePageSize,
      ),
    [
      crawlCandidatePageSize,
      filteredCrawlJobCandidates,
      safeCrawlCandidatePage,
    ],
  );
  const selectedCrawlJobId = selectedCrawlJob?.id ?? null;
  const taskDetailDialogOpen =
    selectedBatchTask !== null ||
    selectedMatchJob !== null ||
    selectedInformationEnrichmentJob !== null ||
    selectedCrawlJob !== null ||
    resendDialogOpen;
  useDocumentScrollLock(taskDetailDialogOpen);
  const selectedCrawlJobCanReview =
    selectedCrawlJob?.status === "needs_review" ||
    selectedCrawlJob?.status === "partially_completed";
  const selectedCrawlJobNeedsReviewResume =
    selectedCrawlJob?.status === "canceled" ||
    selectedCrawlJob?.status === "failed";
  const reviewableCrawlCandidateIds = useMemo(
    () => getReviewableCandidateIds(crawlJobCandidates),
    [crawlJobCandidates],
  );
  const reviewableCrawlCandidateIdsWithoutEmail = useMemo(
    () => getReviewableCandidateIdsWithoutEmail(crawlJobCandidates),
    [crawlJobCandidates],
  );
  const selectedReviewableCrawlCandidateIds = useMemo(
    () =>
      pruneSelectedCandidateIds(selectedCrawlCandidateIds, crawlJobCandidates),
    [crawlJobCandidates, selectedCrawlCandidateIds],
  );
  const filteredReviewableCrawlCandidateIds = useMemo(
    () => getReviewableCandidateIds(filteredCrawlJobCandidates),
    [filteredCrawlJobCandidates],
  );
  const filteredSelectedCrawlCandidateCount = useMemo(
    () =>
      filteredReviewableCrawlCandidateIds.filter((candidateId) =>
        selectedReviewableCrawlCandidateIds.includes(candidateId),
      ).length,
    [
      filteredReviewableCrawlCandidateIds,
      selectedReviewableCrawlCandidateIds,
    ],
  );
  const someFilteredCrawlCandidatesSelected =
    filteredSelectedCrawlCandidateCount > 0;
  const allFilteredCrawlCandidatesSelected =
    filteredReviewableCrawlCandidateIds.length > 0 &&
    filteredSelectedCrawlCandidateCount ===
      filteredReviewableCrawlCandidateIds.length;
  const selectedCrawlCandidateIdsWithoutEmail = useMemo(() => {
    const withoutEmailIds = new Set(reviewableCrawlCandidateIdsWithoutEmail);
    return selectedReviewableCrawlCandidateIds.filter((candidateId) =>
      withoutEmailIds.has(candidateId),
    );
  }, [
    reviewableCrawlCandidateIdsWithoutEmail,
    selectedReviewableCrawlCandidateIds,
  ]);
  const crawlCandidateFiltersActive = hasActiveCrawlCandidateFilters(
    crawlCandidateFilters,
  );
  const activeCrawlCandidateInformationConditionCount =
    getCrawlCandidateInformationConditionEntries(
      crawlCandidateFilters.informationConditions,
    ).length;
  const crawlCandidateInformationConditionsSummary =
    getCrawlCandidateInformationConditionsSummary(crawlCandidateFilters);

  useEffect(() => {
    if (
      hasTaskSelection ||
      activeTab === "crawl" ||
      activeTab === "enrichment"
    ) {
      return;
    }
    setActiveTab("crawl");
  }, [activeTab, hasTaskSelection]);

  useEffect(() => {
    const previousTaskListViews = previousTaskListViewsRef.current;
    previousTaskListViewsRef.current = taskListViews;
    if (previousTaskListViews.batch !== taskListViews.batch) {
      setBatchPage(1);
    }
    if (previousTaskListViews.crawl !== taskListViews.crawl) {
      setCrawlPage(1);
    }
    if (previousTaskListViews.match !== taskListViews.match) {
      setMatchPage(1);
    }
    if (previousTaskListViews.enrichment !== taskListViews.enrichment) {
      setInformationEnrichmentPage(1);
    }
  }, [
    setBatchPage,
    setCrawlPage,
    setInformationEnrichmentPage,
    setMatchPage,
    taskListViews,
  ]);

  const loadTasks = useCallback(async () => {
    if (!tasksRequestKey || !selectedIdentityId) {
      latestTasksRequestIdRef.current += 1;
      activeTasksRequestKeyRef.current = null;
      loadedTasksKeyRef.current = null;
      setTasks([]);
      setCurrentBatchTasks([]);
      lastLoadErrorRef.current = null;
      setLoading(false);
      return;
    }
    const requestId = latestTasksRequestIdRef.current + 1;
    latestTasksRequestIdRef.current = requestId;
    activeTasksRequestKeyRef.current = tasksRequestKey;
    setLoading(true);
    try {
      const data = await listBatchTasks({
        identityId: selectedIdentityId,
        llmProfileId: selectedLlmProfileId,
        view: taskListViews.batch,
      });
      const currentData =
        taskListViews.batch === "current"
          ? data
          : await listBatchTasks({
              identityId: selectedIdentityId,
              llmProfileId: selectedLlmProfileId,
              view: "current",
            });
      if (
        latestTasksRequestIdRef.current !== requestId ||
        activeTasksRequestKeyRef.current !== tasksRequestKey
      ) {
        return;
      }
      setTasks(data);
      setCurrentBatchTasks(currentData);
      loadedTasksKeyRef.current = tasksRequestKey;
      lastLoadErrorRef.current = null;
    } catch (loadError) {
      if (
        latestTasksRequestIdRef.current !== requestId ||
        activeTasksRequestKeyRef.current !== tasksRequestKey
      ) {
        return;
      }
      if (loadedTasksKeyRef.current !== tasksRequestKey) {
        setTasks([]);
      }
      const message =
        loadError instanceof Error ? loadError.message : "加载任务失败";
      if (lastLoadErrorRef.current !== message) {
        notifyError("加载任务失败", message);
        lastLoadErrorRef.current = message;
      }
    } finally {
      if (
        latestTasksRequestIdRef.current === requestId &&
        activeTasksRequestKeyRef.current === tasksRequestKey
      ) {
        setLoading(false);
      }
    }
  }, [
    notifyError,
    selectedIdentityId,
    selectedLlmProfileId,
    taskListViews.batch,
    tasksRequestKey,
  ]);

  const loadCrawlJobs = useCallback(
    async (options?: { showLoading?: boolean }) => {
      const requestId = latestCrawlJobsRequestIdRef.current + 1;
      latestCrawlJobsRequestIdRef.current = requestId;
      if (options?.showLoading ?? true) {
        setCrawlJobsLoading(true);
      }
      try {
        const data = await listCrawlJobs({ view: taskListViews.crawl });
        const currentData =
          taskListViews.crawl === "current"
            ? data
            : await listCrawlJobs({ view: "current" });
        if (latestCrawlJobsRequestIdRef.current !== requestId) {
          return;
        }
        setCrawlJobs(data);
        setCurrentCrawlJobs(currentData);
        setSelectedCrawlJob((currentJob) => {
          if (!currentJob) {
            return currentJob;
          }
          return data.find((job) => job.id === currentJob.id) ?? currentJob;
        });
        lastCrawlJobsLoadErrorRef.current = null;
      } catch (loadError) {
        if (latestCrawlJobsRequestIdRef.current !== requestId) {
          return;
        }
        const message =
          loadError instanceof Error ? loadError.message : "加载抓取任务失败";
        if (lastCrawlJobsLoadErrorRef.current !== message) {
          notifyError("加载抓取任务失败", message);
          lastCrawlJobsLoadErrorRef.current = message;
        }
      } finally {
        if (
          latestCrawlJobsRequestIdRef.current === requestId &&
          (options?.showLoading ?? true)
        ) {
          setCrawlJobsLoading(false);
        }
      }
    },
    [notifyError, taskListViews.crawl],
  );

  const loadMatchAnalysisJobs = useCallback(async (options?: { showLoading?: boolean }) => {
    if (!selectedIdentityId) {
      setMatchAnalysisJobs([]);
      setCurrentMatchAnalysisJobs([]);
      lastMatchJobsLoadErrorRef.current = null;
      setMatchJobsLoading(false);
      return;
    }
    const requestId = latestMatchJobsRequestIdRef.current + 1;
    latestMatchJobsRequestIdRef.current = requestId;
    if (options?.showLoading ?? true) {
      setMatchJobsLoading(true);
    }
    try {
      const data = await listMatchAnalysisJobs({
        identityId: selectedIdentityId,
        llmProfileId: selectedLlmProfileId,
        view: taskListViews.match,
      });
      const currentData =
        taskListViews.match === "current"
          ? data
          : await listMatchAnalysisJobs({
              identityId: selectedIdentityId,
              llmProfileId: selectedLlmProfileId,
              view: "current",
            });
      if (latestMatchJobsRequestIdRef.current !== requestId) {
        return;
      }
      setMatchAnalysisJobs(data);
      setCurrentMatchAnalysisJobs(currentData);
      setSelectedMatchJob((currentJob) => {
        if (!currentJob) {
          return currentJob;
        }
        return data.find((job) => job.id === currentJob.id) ?? currentJob;
      });
      lastMatchJobsLoadErrorRef.current = null;
    } catch (loadError) {
      if (latestMatchJobsRequestIdRef.current !== requestId) {
        return;
      }
      const message =
        loadError instanceof Error ? loadError.message : "加载匹配分析任务失败";
      if (lastMatchJobsLoadErrorRef.current !== message) {
        notifyError("加载匹配分析任务失败", message);
        lastMatchJobsLoadErrorRef.current = message;
      }
    } finally {
      if (
        latestMatchJobsRequestIdRef.current === requestId &&
        (options?.showLoading ?? true)
      ) {
        setMatchJobsLoading(false);
      }
    }
  }, [notifyError, selectedIdentityId, selectedLlmProfileId, taskListViews.match]);

  const loadMatchJobDetails = useCallback(
    async (jobId: number) => {
      const requestId = latestMatchJobDetailsRequestIdRef.current + 1;
      latestMatchJobDetailsRequestIdRef.current = requestId;
      setMatchJobDetailsLoading(true);
      try {
        const data = await listMatchAnalysisJobItems(jobId);
        if (latestMatchJobDetailsRequestIdRef.current !== requestId) {
          return;
        }
        setSelectedMatchJobItems(data);
        lastMatchJobDetailsLoadErrorRef.current = null;
      } catch (loadError) {
        if (latestMatchJobDetailsRequestIdRef.current !== requestId) {
          return;
        }
        const message =
          loadError instanceof Error
            ? loadError.message
            : "加载匹配分析任务详情失败";
        if (lastMatchJobDetailsLoadErrorRef.current !== message) {
          notifyError("加载匹配分析任务详情失败", message);
          lastMatchJobDetailsLoadErrorRef.current = message;
        }
      } finally {
        if (latestMatchJobDetailsRequestIdRef.current === requestId) {
          setMatchJobDetailsLoading(false);
        }
      }
    },
    [notifyError],
  );

  const loadInformationEnrichmentJobs = useCallback(
    async (options?: { showLoading?: boolean }) => {
      const requestId = latestInformationEnrichmentJobsRequestIdRef.current + 1;
      latestInformationEnrichmentJobsRequestIdRef.current = requestId;
      if (options?.showLoading ?? true) {
        setInformationEnrichmentJobsLoading(true);
      }
      try {
        const data = await listProfessorInformationEnrichmentJobs({
          view: taskListViews.enrichment,
        });
        const currentData =
          taskListViews.enrichment === "current"
            ? data
            : await listProfessorInformationEnrichmentJobs({ view: "current" });
        if (latestInformationEnrichmentJobsRequestIdRef.current !== requestId) {
          return;
        }
        setInformationEnrichmentJobs(data);
        setCurrentInformationEnrichmentJobs(currentData);
        setSelectedInformationEnrichmentJob((currentJob) => {
          if (!currentJob) {
            return currentJob;
          }
          return data.find((job) => job.id === currentJob.id) ?? currentJob;
        });
        lastInformationEnrichmentJobsLoadErrorRef.current = null;
      } catch (loadError) {
        if (latestInformationEnrichmentJobsRequestIdRef.current !== requestId) {
          return;
        }
        const message =
          loadError instanceof Error
            ? loadError.message
            : "加载信息补全任务失败";
        if (lastInformationEnrichmentJobsLoadErrorRef.current !== message) {
          notifyError("加载信息补全任务失败", message);
          lastInformationEnrichmentJobsLoadErrorRef.current = message;
        }
      } finally {
        if (
          latestInformationEnrichmentJobsRequestIdRef.current === requestId &&
          (options?.showLoading ?? true)
        ) {
          setInformationEnrichmentJobsLoading(false);
        }
      }
    },
    [notifyError, taskListViews.enrichment],
  );

  const loadInformationEnrichmentDetails = useCallback(
    async (jobId: number) => {
      const requestId = latestInformationEnrichmentDetailsRequestIdRef.current + 1;
      latestInformationEnrichmentDetailsRequestIdRef.current = requestId;
      setInformationEnrichmentDetailsLoading(true);
      try {
        const data = await listProfessorInformationEnrichmentItems(jobId);
        if (latestInformationEnrichmentDetailsRequestIdRef.current !== requestId) {
          return;
        }
        setSelectedInformationEnrichmentItems(data);
        lastInformationEnrichmentDetailsLoadErrorRef.current = null;
      } catch (loadError) {
        if (latestInformationEnrichmentDetailsRequestIdRef.current !== requestId) {
          return;
        }
        const message =
          loadError instanceof Error
            ? loadError.message
            : "加载信息补全任务详情失败";
        if (lastInformationEnrichmentDetailsLoadErrorRef.current !== message) {
          notifyError("加载信息补全任务详情失败", message);
          lastInformationEnrichmentDetailsLoadErrorRef.current = message;
        }
      } finally {
        if (latestInformationEnrichmentDetailsRequestIdRef.current === requestId) {
          setInformationEnrichmentDetailsLoading(false);
        }
      }
    },
    [notifyError],
  );

  const loadBatchTaskDetails = useCallback(
    async (taskId: number) => {
      const requestId = latestBatchTaskDetailsRequestIdRef.current + 1;
      latestBatchTaskDetailsRequestIdRef.current = requestId;
      setBatchTaskDetailsLoading(true);
      try {
        const data = await listBatchTaskItems(taskId);
        if (latestBatchTaskDetailsRequestIdRef.current !== requestId) {
          return;
        }
        setSelectedBatchTaskItems(data);
        lastBatchTaskDetailsLoadErrorRef.current = null;
      } catch (loadError) {
        if (latestBatchTaskDetailsRequestIdRef.current !== requestId) {
          return;
        }
        const message =
          loadError instanceof Error
            ? loadError.message
            : "加载批量任务详情失败";
        if (lastBatchTaskDetailsLoadErrorRef.current !== message) {
          notifyError("加载批量任务详情失败", message);
          lastBatchTaskDetailsLoadErrorRef.current = message;
        }
      } finally {
        if (latestBatchTaskDetailsRequestIdRef.current === requestId) {
          setBatchTaskDetailsLoading(false);
        }
      }
    },
    [notifyError],
  );

  const closeProfessorEditDialog = useCallback(() => {
    latestProfessorEditRequestIdRef.current += 1;
    setProfessorEditDialogOpen(false);
    setProfessorEditLoading(false);
    setProfessorEditProfessor(null);
  }, []);

  const openProfessorEditDialog = useCallback(
    async (item: BatchTaskItemDTO) => {
      const requestId = latestProfessorEditRequestIdRef.current + 1;
      latestProfessorEditRequestIdRef.current = requestId;
      setProfessorEditDialogOpen(true);
      setProfessorEditLoading(true);
      setProfessorEditProfessor(null);
      try {
        const professor = await getProfessor(item.professor_id);
        if (latestProfessorEditRequestIdRef.current !== requestId) {
          return;
        }
        setProfessorEditProfessor(professor);
      } catch (error) {
        if (latestProfessorEditRequestIdRef.current !== requestId) {
          return;
        }
        notifyError(
          "加载导师资料失败",
          error instanceof Error ? error.message : "加载导师资料失败",
        );
        closeProfessorEditDialog();
      } finally {
        if (latestProfessorEditRequestIdRef.current === requestId) {
          setProfessorEditLoading(false);
        }
      }
    },
    [closeProfessorEditDialog, notifyError],
  );

  const refreshAfterProfessorEdit = useCallback(
    async (professor: ProfessorManagementItemDTO) => {
      setBatchReviewThread((currentThread) => {
        if (!currentThread || currentThread.professor.id !== professor.id) {
          return currentThread;
        }
        return {
          ...currentThread,
          professor: {
            ...currentThread.professor,
            name: professor.name,
            email: professor.email,
            title: professor.title,
            university: professor.university,
            school: professor.school,
            department: professor.department,
            research_direction: professor.research_direction,
            recent_papers: professor.recent_papers,
            profile_url: professor.profile_url,
          },
        };
      });
      if (!selectedBatchTask) {
        return;
      }
      await Promise.all([
        loadBatchTaskDetails(selectedBatchTask.id),
        loadTasks(),
      ]);
    },
    [loadBatchTaskDetails, loadTasks, selectedBatchTask],
  );

  const loadCrawlJobDetails = useCallback(
    async (jobId: number, options?: { showLoading?: boolean }) => {
      const requestId = latestCrawlJobDetailsRequestIdRef.current + 1;
      latestCrawlJobDetailsRequestIdRef.current = requestId;
      if (options?.showLoading ?? true) {
        setCrawlJobDetailsLoading(true);
      }
      try {
        const [job, pages, candidates, events] = await Promise.all([
          getCrawlJob(jobId),
          listCrawlPages(jobId),
          listCrawlCandidates(jobId),
          getCrawlJobEvents(jobId),
        ]);
        if (latestCrawlJobDetailsRequestIdRef.current !== requestId) {
          return;
        }
        setSelectedCrawlJob(job);
        setCrawlJobPages(pages);
        setCrawlJobCandidates(candidates);
        setCrawlJobEvents(events);
        lastCrawlJobDetailsLoadErrorRef.current = null;
      } catch (loadError) {
        if (latestCrawlJobDetailsRequestIdRef.current !== requestId) {
          return;
        }
        const message =
          loadError instanceof Error
            ? loadError.message
            : "加载抓取任务日志失败";
        if (lastCrawlJobDetailsLoadErrorRef.current !== message) {
          notifyError("加载抓取任务日志失败", message);
          lastCrawlJobDetailsLoadErrorRef.current = message;
        }
      } finally {
        if (
          latestCrawlJobDetailsRequestIdRef.current === requestId &&
          (options?.showLoading ?? true)
        ) {
          setCrawlJobDetailsLoading(false);
        }
      }
    },
    [notifyError],
  );

  useEffect(() => {
    if (activeTab !== "batch") {
      return undefined;
    }
    void loadTasks();
    const timer = window.setInterval(() => {
      void loadTasks();
    }, 10000);
    return () => window.clearInterval(timer);
  }, [activeTab, loadTasks]);

  useEffect(() => {
    setBatchPage((currentPage) =>
      Math.min(currentPage, getTotalPages(tasks.length, batchPageSize)),
    );
  }, [batchPageSize, setBatchPage, tasks.length]);

  useEffect(() => {
    setCrawlPage((currentPage) =>
      Math.min(currentPage, getTotalPages(crawlJobs.length, crawlPageSize)),
    );
  }, [crawlJobs.length, crawlPageSize, setCrawlPage]);

  useEffect(() => {
    setMatchPage((currentPage) =>
      Math.min(
        currentPage,
        getTotalPages(matchAnalysisJobs.length, matchPageSize),
      ),
    );
  }, [matchAnalysisJobs.length, matchPageSize, setMatchPage]);

  useEffect(() => {
    setInformationEnrichmentPage((currentPage) =>
      Math.min(
        currentPage,
        getTotalPages(
          informationEnrichmentJobs.length,
          informationEnrichmentPageSize,
        ),
      ),
    );
  }, [
    informationEnrichmentJobs.length,
    informationEnrichmentPageSize,
    setInformationEnrichmentPage,
  ]);

  useEffect(() => {
    setCrawlEventPage((currentPage) =>
      Math.min(
        currentPage,
        getTotalPages(crawlJobEvents.length, crawlEventPageSize),
      ),
    );
  }, [crawlEventPageSize, crawlJobEvents.length, setCrawlEventPage]);

  useEffect(() => {
    setCrawlDetailPagePage((currentPage) =>
      Math.min(
        currentPage,
        getTotalPages(crawlJobPages.length, crawlDetailPagePageSize),
      ),
    );
  }, [crawlDetailPagePageSize, crawlJobPages.length, setCrawlDetailPagePage]);

  useEffect(() => {
    setCrawlCandidatePage((currentPage) =>
      Math.min(
        currentPage,
        getTotalPages(
          filteredCrawlJobCandidates.length,
          crawlCandidatePageSize,
        ),
      ),
    );
  }, [
    crawlCandidatePageSize,
    filteredCrawlJobCandidates.length,
    setCrawlCandidatePage,
  ]);

  useEffect(() => {
    setBatchSentItemPage((currentPage) =>
      Math.min(
        currentPage,
        getTotalPages(sentBatchTaskItems.length, batchSentItemPageSize),
      ),
    );
  }, [batchSentItemPageSize, sentBatchTaskItems.length, setBatchSentItemPage]);

  useEffect(() => {
    setBatchPendingItemPage((currentPage) =>
      Math.min(
        currentPage,
        getTotalPages(pendingBatchTaskItems.length, batchPendingItemPageSize),
      ),
    );
  }, [
    batchPendingItemPageSize,
    pendingBatchTaskItems.length,
    setBatchPendingItemPage,
  ]);

  useEffect(() => {
    if (crawlJobsPreloadedRef.current) {
      return;
    }
    crawlJobsPreloadedRef.current = true;
    void loadCrawlJobs({ showLoading: false });
  }, [loadCrawlJobs]);

  useEffect(() => {
    if (informationEnrichmentJobsPreloadedRef.current) {
      return;
    }
    informationEnrichmentJobsPreloadedRef.current = true;
    void loadInformationEnrichmentJobs({ showLoading: false });
  }, [loadInformationEnrichmentJobs]);

  useEffect(() => {
    if (activeTab === "batch") {
      return;
    }
    if (!tasksRequestKey) {
      batchTasksPreloadedKeyRef.current = null;
      void loadTasks();
      return;
    }
    if (batchTasksPreloadedKeyRef.current === tasksRequestKey) {
      return;
    }
    batchTasksPreloadedKeyRef.current = tasksRequestKey;
    void loadTasks();
  }, [activeTab, loadTasks, tasksRequestKey]);

  useEffect(() => {
    if (!tasksRequestKey) {
      matchJobsPreloadedKeyRef.current = null;
      void loadMatchAnalysisJobs({ showLoading: false });
      return;
    }
    if (matchJobsPreloadedKeyRef.current === tasksRequestKey) {
      return;
    }
    matchJobsPreloadedKeyRef.current = tasksRequestKey;
    void loadMatchAnalysisJobs({ showLoading: false });
  }, [loadMatchAnalysisJobs, tasksRequestKey]);

  useEffect(() => {
    if (activeTab !== "crawl") {
      return undefined;
    }
    void loadCrawlJobs({ showLoading: crawlJobs.length === 0 });
    const timer = window.setInterval(() => {
      void loadCrawlJobs({ showLoading: false });
    }, CRAWL_REFRESH_INTERVAL_MS);
    return () => window.clearInterval(timer);
  }, [activeTab, crawlJobs.length, loadCrawlJobs]);

  useEffect(() => {
    if (activeTab !== "match") {
      return undefined;
    }
    void loadMatchAnalysisJobs({ showLoading: matchAnalysisJobs.length === 0 });
    const timer = window.setInterval(() => {
      void loadMatchAnalysisJobs({ showLoading: false });
    }, CRAWL_REFRESH_INTERVAL_MS);
    return () => window.clearInterval(timer);
  }, [activeTab, loadMatchAnalysisJobs, matchAnalysisJobs.length]);

  useEffect(() => {
    if (activeTab !== "enrichment") {
      return undefined;
    }
    void loadInformationEnrichmentJobs({
      showLoading: informationEnrichmentJobs.length === 0,
    });
    const timer = window.setInterval(() => {
      void loadInformationEnrichmentJobs({ showLoading: false });
    }, CRAWL_REFRESH_INTERVAL_MS);
    return () => window.clearInterval(timer);
  }, [
    activeTab,
    informationEnrichmentJobs.length,
    loadInformationEnrichmentJobs,
  ]);

  useEffect(() => {
    if (!selectedBatchTask) {
      return undefined;
    }
    lastBatchTaskDetailsLoadErrorRef.current = null;
    void loadBatchTaskDetails(selectedBatchTask.id);
    const timer = window.setInterval(() => {
      void loadBatchTaskDetails(selectedBatchTask.id);
    }, 5000);
    return () => {
      latestBatchTaskDetailsRequestIdRef.current += 1;
      window.clearInterval(timer);
    };
  }, [loadBatchTaskDetails, selectedBatchTask]);

  useEffect(() => {
    if (selectedBatchTask?.id === undefined || !hasActiveBatchRestoreDeadline) {
      return undefined;
    }
    setBatchSendActionNowMs(Date.now());
    const timer = window.setInterval(() => {
      setBatchSendActionNowMs(Date.now());
    }, 1000);
    return () => window.clearInterval(timer);
  }, [hasActiveBatchRestoreDeadline, selectedBatchTask?.id]);

  useEffect(() => {
    if (previousSelectedBatchTaskIdRef.current === selectedBatchTask?.id) {
      return;
    }
    previousSelectedBatchTaskIdRef.current = selectedBatchTask?.id;
    setBatchSentItemPage(1);
    setBatchPendingItemPage(1);
  }, [
    selectedBatchTask?.id,
    setBatchPendingItemPage,
    setBatchSentItemPage,
  ]);

  useEffect(() => {
    if (!selectedMatchJob) {
      return undefined;
    }
    lastMatchJobDetailsLoadErrorRef.current = null;
    void loadMatchJobDetails(selectedMatchJob.id);
    const timer = window.setInterval(() => {
      void loadMatchJobDetails(selectedMatchJob.id);
    }, CRAWL_DETAILS_REFRESH_INTERVAL_MS);
    return () => {
      latestMatchJobDetailsRequestIdRef.current += 1;
      window.clearInterval(timer);
    };
  }, [loadMatchJobDetails, selectedMatchJob]);

  useEffect(() => {
    if (!selectedInformationEnrichmentJob) {
      return undefined;
    }
    lastInformationEnrichmentDetailsLoadErrorRef.current = null;
    void loadInformationEnrichmentDetails(selectedInformationEnrichmentJob.id);
    const timer = window.setInterval(() => {
      void loadInformationEnrichmentDetails(selectedInformationEnrichmentJob.id);
    }, CRAWL_DETAILS_REFRESH_INTERVAL_MS);
    return () => {
      latestInformationEnrichmentDetailsRequestIdRef.current += 1;
      window.clearInterval(timer);
    };
  }, [loadInformationEnrichmentDetails, selectedInformationEnrichmentJob]);

  useEffect(() => {
    if (!selectedCrawlJobId) {
      return undefined;
    }
    lastCrawlJobDetailsLoadErrorRef.current = null;
    void loadCrawlJobDetails(selectedCrawlJobId, { showLoading: true });
    const timer = window.setInterval(() => {
      void loadCrawlJobDetails(selectedCrawlJobId, { showLoading: false });
    }, CRAWL_DETAILS_REFRESH_INTERVAL_MS);
    return () => {
      latestCrawlJobDetailsRequestIdRef.current += 1;
      window.clearInterval(timer);
    };
  }, [loadCrawlJobDetails, selectedCrawlJobId]);

  useEffect(() => {
    setSelectedCrawlCandidateIds((currentIds) =>
      pruneSelectedCandidateIds(currentIds, crawlJobCandidates),
    );
  }, [crawlJobCandidates]);

  useEffect(() => {
    if (previousSelectedCrawlJobIdRef.current === selectedCrawlJobId) {
      return;
    }
    previousSelectedCrawlJobIdRef.current = selectedCrawlJobId;
    setSelectedCrawlCandidateIds([]);
    setCrawlCandidateFilters(createDefaultCrawlCandidateFilters());
    setCrawlCandidateInformationFiltersOpen(false);
    setCrawlJobApproveLoading(false);
    setCrawlJobEnrichLoading(false);
    setResumingCrawlJobReviewId(null);
    setSelectedCandidateDetail(null);
    setCandidateEditForm(null);
    setCandidateUpdateLoading(false);
    setCrawlEventPage(1);
    setCrawlDetailPagePage(1);
    setCrawlCandidatePage(1);
  }, [
    selectedCrawlJobId,
    setCrawlCandidatePage,
    setCrawlDetailPagePage,
    setCrawlEventPage,
  ]);

  const handleAction = async (
    taskId: number,
    action: "pause" | "resume" | "stop",
  ) => {
    const diagnosticData = { taskId, action };
    try {
      if (action === "pause") {
        safeRecordUserAction({
          eventName: "tasks.batch_task_pause_submitted",
          data: diagnosticData,
        });
        await pauseBatchTask(taskId);
      } else if (action === "resume") {
        safeRecordUserAction({
          eventName: "tasks.batch_task_resume_submitted",
          data: diagnosticData,
        });
        await resumeBatchTask(taskId);
      } else {
        const confirmed = await confirm({
          title: "确认终止这个任务？",
          description: "终止后当前批次不会继续推进生成、排程和发送。",
          confirmLabel: "确认终止",
          cancelLabel: "先保留",
          tone: "danger",
        });
        if (!confirmed) {
          return;
        }
        safeRecordUserAction({
          eventName: "tasks.batch_task_stop_submitted",
          data: diagnosticData,
        });
        await stopBatchTask(taskId);
      }
      safeRecordUserAction({
        eventName: `tasks.batch_task_${action}_succeeded`,
        data: diagnosticData,
      });
      await loadTasks();
    } catch (actionError) {
      safeRecordUserAction({
        eventName: `tasks.batch_task_${action}_failed`,
        data: diagnosticData,
        level: "error",
      });
      const message =
        actionError instanceof Error ? actionError.message : "任务操作失败";
      notifyError("任务操作失败", message);
    }
  };

  const handlePauseCrawlJob = async (jobId: number) => {
    const confirmed = await confirm({
      title: "确认暂停这个抓取任务？",
      description: "暂停后会保留已抓到的页面和候选导师，之后可以继续。",
      confirmLabel: "确认暂停",
      cancelLabel: "先不暂停",
    });
    if (!confirmed) {
      return;
    }

    const diagnosticData = { jobId };
    safeRecordUserAction({
      eventName: "tasks.crawl_job_pause_submitted",
      data: diagnosticData,
    });
    setPausingCrawlJobId(jobId);
    try {
      await pauseCrawlJob(jobId);
      safeRecordUserAction({
        eventName: "tasks.crawl_job_pause_succeeded",
        data: diagnosticData,
      });
      notifySuccess("抓取任务已暂停", "已保留当前抓取结果，之后可以继续");
      await loadCrawlJobs();
      if (selectedCrawlJobId === jobId) {
        await loadCrawlJobDetails(jobId, { showLoading: false });
      }
    } catch (actionError) {
      safeRecordUserAction({
        eventName: "tasks.crawl_job_pause_failed",
        data: diagnosticData,
        level: "error",
      });
      const message =
        actionError instanceof Error ? actionError.message : "抓取任务暂停失败";
      notifyError("抓取任务操作失败", message);
    } finally {
      setPausingCrawlJobId((currentJobId) =>
        currentJobId === jobId ? null : currentJobId,
      );
    }
  };

  const ensureSelectedLlmProfile = () => {
    if (selectedLlmProfileId !== null) {
      return selectedLlmProfileId;
    }
    notifyError("请先选择模型配置", "请选择一个 LLM Profile 后再继续操作。");
    return null;
  };

  const handleResumeCrawlJob = async (jobId: number) => {
    const llmProfileId = ensureSelectedLlmProfile();
    if (llmProfileId === null) {
      return;
    }
    const diagnosticData = { jobId };
    safeRecordUserAction({
      eventName: "tasks.crawl_job_resume_submitted",
      data: diagnosticData,
    });
    setResumingCrawlJobId(jobId);
    try {
      const job = await resumeCrawlJob(jobId, llmProfileId);
      trackCrawlJob(job);
      safeRecordUserAction({
        eventName: "tasks.crawl_job_resume_succeeded",
        data: diagnosticData,
      });
      notifySuccess("抓取任务已继续", "任务已重新进入队列，稍后开始执行");
      await loadCrawlJobs();
      if (selectedCrawlJobId === jobId) {
        await loadCrawlJobDetails(jobId, { showLoading: false });
      }
    } catch (actionError) {
      safeRecordUserAction({
        eventName: "tasks.crawl_job_resume_failed",
        data: diagnosticData,
        level: "error",
      });
      const message =
        actionError instanceof Error ? actionError.message : "抓取任务继续失败";
      notifyError("抓取任务操作失败", message);
    } finally {
      setResumingCrawlJobId((currentJobId) =>
        currentJobId === jobId ? null : currentJobId,
      );
    }
  };

  const handleCancelCrawlJob = async (jobId: number) => {
    const confirmed = await confirm({
      title: "确认取消这个抓取任务？",
      description: "取消后本次抓取不会继续。如需重新抓取，请点击“重新抓取”。",
      confirmLabel: "取消抓取",
      cancelLabel: "先保留",
      tone: "danger",
    });
    if (!confirmed) {
      return;
    }

    const diagnosticData = { jobId };
    safeRecordUserAction({
      eventName: "tasks.crawl_job_cancel_submitted",
      data: diagnosticData,
    });
    try {
      await cancelCrawlJob(jobId);
      safeRecordUserAction({
        eventName: "tasks.crawl_job_cancel_succeeded",
        data: diagnosticData,
      });
      await loadCrawlJobs();
    } catch (actionError) {
      safeRecordUserAction({
        eventName: "tasks.crawl_job_cancel_failed",
        data: diagnosticData,
        level: "error",
      });
      const message =
        actionError instanceof Error ? actionError.message : "抓取任务操作失败";
      notifyError("抓取任务操作失败", message);
    }
  };

  const handleRetryCrawlJob = async (jobId: number) => {
    const llmProfileId = ensureSelectedLlmProfile();
    if (llmProfileId === null) {
      return;
    }

    const confirmed = await confirm({
      title: "确认重新抓取任务？",
      description:
        "重新抓取会清空该任务历史抓取数据（页面与候选导师），并重新加入队列执行。",
      confirmLabel: "确认重新抓取",
      cancelLabel: "暂不处理",
    });
    if (!confirmed) {
      return;
    }

    const diagnosticData = { jobId };
    safeRecordUserAction({
      eventName: "tasks.crawl_job_retry_submitted",
      data: diagnosticData,
    });
    setRetryingCrawlJobId(jobId);
    try {
      const job = await retryCrawlJob(jobId, {
        clear_existing_data: true,
        llmProfileId,
      });
      trackCrawlJob(job);
      safeRecordUserAction({
        eventName: "tasks.crawl_job_retry_succeeded",
        data: diagnosticData,
      });
      notifySuccess("抓取任务已重新加入队列", "任务已进入队列，稍后开始执行");
      await loadCrawlJobs();
      if (selectedCrawlJobId === jobId) {
        await loadCrawlJobDetails(jobId, { showLoading: false });
      }
    } catch (actionError) {
      safeRecordUserAction({
        eventName: "tasks.crawl_job_retry_failed",
        data: diagnosticData,
        level: "error",
      });
      const message =
        actionError instanceof Error ? actionError.message : "重新抓取任务失败";
      notifyError("抓取任务操作失败", message);
    } finally {
      setRetryingCrawlJobId((currentJobId) =>
        currentJobId === jobId ? null : currentJobId,
      );
    }
  };

  const handleResumeCrawlJobReview = async (jobId: number) => {
    const confirmed = await confirm({
      title: "确认转入待审核？",
      description:
        "任务不会重新抓取，只会把已有候选导师转入待审核，随后可以继续补全或导入。",
      confirmLabel: "转入待审核",
      cancelLabel: "先保留",
    });
    if (!confirmed) {
      return;
    }

    setResumingCrawlJobReviewId(jobId);
    try {
      await resumeCrawlJobReview(jobId);
      notifySuccess("已转入待审核", "可以继续选择候选并补全信息。");
      await loadCrawlJobs({ showLoading: false });
      await loadCrawlJobDetails(jobId, { showLoading: false });
    } catch (actionError) {
      const message =
        actionError instanceof Error
          ? actionError.message
          : "转入待审核失败";
      notifyError("转入待审核失败", message);
    } finally {
      setResumingCrawlJobReviewId((currentJobId) =>
        currentJobId === jobId ? null : currentJobId,
      );
    }
  };

  const handleCancelMatchJob = async (jobId: number) => {
    const confirmed = await confirm({
      title: "确认取消这个匹配分析任务？",
      description: "已开始的单项分析会在安全点结束，未开始的导师会被取消。",
      confirmLabel: "取消任务",
      cancelLabel: "先保留",
      tone: "danger",
    });
    if (!confirmed) {
      return;
    }

    setCancelingMatchJobId(jobId);
    try {
      const result = await cancelMatchAnalysisJob(jobId);
      setMatchAnalysisJobs((currentJobs) =>
        currentJobs.map((job) => (job.id === jobId ? result.job : job)),
      );
      notifySuccess("已请求取消", "匹配分析任务会在安全点停止。");
      if (selectedMatchJob?.id === jobId) {
        setSelectedMatchJob(result.job);
      }
    } catch (actionError) {
      const message =
        actionError instanceof Error ? actionError.message : "取消匹配分析任务失败";
      notifyError("取消匹配分析任务失败", message);
    } finally {
      setCancelingMatchJobId((currentJobId) =>
        currentJobId === jobId ? null : currentJobId,
      );
    }
  };

  const handleRetryMatchJob = async (jobId: number) => {
    setRetryingMatchJobId(jobId);
    try {
      const job = await retryFailedMatchAnalysisJob(jobId);
      setMatchAnalysisJobs((currentJobs) => [job, ...currentJobs]);
      trackMatchAnalysisJob(job);
      notifySuccess("已创建重试任务", "失败项已重新加入后台匹配分析队列。");
    } catch (actionError) {
      const message =
        actionError instanceof Error ? actionError.message : "重试匹配分析任务失败";
      notifyError("重试匹配分析任务失败", message);
    } finally {
      setRetryingMatchJobId((currentJobId) =>
        currentJobId === jobId ? null : currentJobId,
      );
    }
  };

  const handleCancelInformationEnrichmentJob = async (jobId: number) => {
    const confirmed = await confirm({
      title: "确认取消这个信息补全任务？",
      description: "未完成的导师会被取消，已经补全并写入的信息会保留。",
      confirmLabel: "取消任务",
      cancelLabel: "先保留",
      tone: "danger",
    });
    if (!confirmed) {
      return;
    }

    setCancelingInformationEnrichmentJobId(jobId);
    try {
      const result = await cancelProfessorInformationEnrichmentJob(jobId);
      setInformationEnrichmentJobs((currentJobs) =>
        currentJobs.map((job) => (job.id === jobId ? result.job : job)),
      );
      setCurrentInformationEnrichmentJobs((currentJobs) =>
        currentJobs.map((job) => (job.id === jobId ? result.job : job)),
      );
      if (selectedInformationEnrichmentJob?.id === jobId) {
        setSelectedInformationEnrichmentJob(result.job);
      }
      stopTrackingInformationEnrichmentJob(jobId);
      notifySuccess("已取消信息补全任务", "已写入的导师信息不会回退。");
    } catch (actionError) {
      const message =
        actionError instanceof Error
          ? actionError.message
          : "取消信息补全任务失败";
      notifyError("取消信息补全任务失败", message);
    } finally {
      setCancelingInformationEnrichmentJobId((currentJobId) =>
        currentJobId === jobId ? null : currentJobId,
      );
    }
  };

  const handleRetryInformationEnrichmentJob = async (jobId: number) => {
    setRetryingInformationEnrichmentJobId(jobId);
    try {
      const job = await retryFailedProfessorInformationEnrichmentJob(jobId);
      setInformationEnrichmentJobs((currentJobs) => [job, ...currentJobs]);
      setCurrentInformationEnrichmentJobs((currentJobs) => [job, ...currentJobs]);
      trackInformationEnrichmentJob(job);
      notifySuccess("已创建重试任务", "失败或取消项已重新加入信息补全队列。");
    } catch (actionError) {
      const message =
        actionError instanceof Error
          ? actionError.message
          : "重试信息补全任务失败";
      notifyError("重试信息补全任务失败", message);
    } finally {
      setRetryingInformationEnrichmentJobId((currentJobId) =>
        currentJobId === jobId ? null : currentJobId,
      );
    }
  };

  const updateCrawlCandidateFilters = (
    patch: Partial<CrawlCandidateFilters>,
  ) => {
    setCrawlCandidateFilters((currentFilters) => ({
      ...currentFilters,
      ...patch,
    }));
    setCrawlCandidatePage(1);
  };

  const updateCrawlCandidateInformationCondition = (
    field: CrawlCandidateInformationField,
    condition: CrawlCandidateInformationCondition | "any",
  ) => {
    setCrawlCandidateFilters((currentFilters) => {
      const informationConditions = {
        ...currentFilters.informationConditions,
      };
      if (condition === "any") {
        delete informationConditions[field];
      } else {
        informationConditions[field] = condition;
      }
      return {
        ...currentFilters,
        informationConditions,
        informationMatchMode:
          Object.keys(informationConditions).length < 2
            ? "all"
            : currentFilters.informationMatchMode,
      };
    });
    setCrawlCandidatePage(1);
  };

  const resetCrawlCandidateFilters = () => {
    setCrawlCandidateFilters(createDefaultCrawlCandidateFilters());
    setCrawlCandidatePage(1);
  };

  const handleToggleFilteredCrawlCandidateSelection = () => {
    setSelectedCrawlCandidateIds((currentIds) => {
      const nextIds = new Set(currentIds);
      const shouldDeselect =
        filteredReviewableCrawlCandidateIds.length > 0 &&
        filteredReviewableCrawlCandidateIds.every((candidateId) =>
          nextIds.has(candidateId),
        );

      filteredReviewableCrawlCandidateIds.forEach((candidateId) => {
        if (shouldDeselect) {
          nextIds.delete(candidateId);
        } else {
          nextIds.add(candidateId);
        }
      });
      return Array.from(nextIds);
    });
  };

  const handleToggleCrawlCandidateSelection = (candidateId: number) => {
    if (!reviewableCrawlCandidateIds.includes(candidateId)) {
      return;
    }

    setSelectedCrawlCandidateIds((currentIds) =>
      currentIds.includes(candidateId)
        ? currentIds.filter((id) => id !== candidateId)
        : [...currentIds, candidateId],
    );
  };

  const handleApproveSelectedCrawlCandidates = async () => {
    if (
      !selectedCrawlJobId ||
      selectedReviewableCrawlCandidateIds.length === 0
    ) {
      return;
    }

    const approveDescription =
      selectedCrawlJob?.status === "canceled"
        ? "通过后，这些候选导师会写入导师库，当前抓取任务会保留已取消状态。"
        : selectedCrawlJob?.status === "partially_completed"
          ? "通过后会导入所选候选，任务中剩余待审核候选仍可继续处理。"
          : "通过后，这些候选导师会写入导师库；如仍有待审核候选，任务会标记为部分已导入。";

    const confirmed = await confirm({
      title: `确认通过并导入这 ${selectedReviewableCrawlCandidateIds.length} 位候选导师吗？`,
      description: approveDescription,
      confirmLabel: "确认导入",
      cancelLabel: "先保留",
      tone: "danger",
    });
    if (!confirmed) {
      return;
    }

    setCrawlJobApproveLoading(true);
    try {
      const result = await approveCrawlCandidates(
        selectedCrawlJobId,
        selectedReviewableCrawlCandidateIds,
      );
      setSelectedCrawlCandidateIds([]);
      notifySuccess("审核完成", result.message);
      await loadCrawlJobs({ showLoading: false });
      await loadCrawlJobDetails(selectedCrawlJobId, { showLoading: false });
    } catch (actionError) {
      const message =
        actionError instanceof Error
          ? actionError.message
          : "审核导入候选导师失败";
      notifyError("审核导入候选导师失败", message);
    } finally {
      setCrawlJobApproveLoading(false);
    }
  };

  const handleEnrichSelectedCrawlCandidates = async () => {
    if (
      !selectedCrawlJobId ||
      selectedReviewableCrawlCandidateIds.length === 0
    ) {
      return;
    }

    const llmProfileId = ensureSelectedLlmProfile();
    if (llmProfileId === null) {
      return;
    }

    setCrawlJobEnrichLoading(true);
    const completionEventBaseline =
      getCrawlEnrichmentCompletionEventKeys(crawlJobEvents);
    try {
      const result = await enrichCrawlCandidates(
        selectedCrawlJobId,
        selectedReviewableCrawlCandidateIds,
        llmProfileId,
      );
      trackCrawlCandidateEnrichment(
        selectedCrawlJobId,
        completionEventBaseline,
      );
      notifySuccess("候选信息补全已开始", result.message);
      await loadCrawlJobs({ showLoading: false });
      await loadCrawlJobDetails(selectedCrawlJobId, { showLoading: false });
    } catch (actionError) {
      const message =
        actionError instanceof Error
          ? actionError.message
          : "补全候选导师信息失败";
      notifyError("补全候选导师信息失败", message);
    } finally {
      setCrawlJobEnrichLoading(false);
    }
  };

  const handleStartCandidateEdit = () => {
    if (
      !selectedCandidateDetail ||
      selectedCandidateDetail.review_status !== "pending" ||
      !selectedCrawlJobCanReview
    ) {
      return;
    }
    setCandidateEditForm(toCrawlCandidateEditForm(selectedCandidateDetail));
  };

  const handleCancelCandidateEdit = () => {
    if (candidateUpdateLoading) {
      return;
    }
    setCandidateEditForm(null);
  };

  const handleCandidateEditFieldChange = (
    field: keyof CrawlCandidateEditForm,
    value: string,
  ) => {
    setCandidateEditForm((currentForm) =>
      currentForm ? { ...currentForm, [field]: value } : currentForm,
    );
  };

  const handleSaveCandidateEdit = async (
    event: FormEvent<HTMLFormElement>,
  ) => {
    event.preventDefault();
    if (
      !selectedCandidateDetail ||
      !candidateEditForm ||
      candidateUpdateLoading
    ) {
      return;
    }
    if (
      selectedCandidateDetail.review_status !== "pending" ||
      !selectedCrawlJobCanReview
    ) {
      notifyError("无法保存导师信息", "该候选导师已不在待审核状态，请刷新任务后重试。");
      return;
    }

    const payload = toCrawlCandidateUpdatePayload(
      selectedCandidateDetail,
      candidateEditForm,
    );
    if (!payload.name) {
      notifyError("无法保存导师信息", "导师姓名不能为空。");
      return;
    }

    setCandidateUpdateLoading(true);
    try {
      const updatedCandidate = await updateCrawlCandidate(
        selectedCandidateDetail.id,
        payload,
      );
      setCrawlJobCandidates((currentCandidates) =>
        currentCandidates.map((candidate) =>
          candidate.id === updatedCandidate.id ? updatedCandidate : candidate,
        ),
      );
      setSelectedCandidateDetail(updatedCandidate);
      setCandidateEditForm(null);
      notifySuccess(
        "导师信息已保存",
        "后续补全只会填写仍然缺失的可补全字段，不会覆盖本次保存的内容。",
      );
    } catch (actionError) {
      const message =
        actionError instanceof Error
          ? actionError.message
          : "保存候选导师信息失败";
      notifyError("保存候选导师信息失败", message);
    } finally {
      setCandidateUpdateLoading(false);
    }
  };

  const closeCrawlJobDetails = () => {
    latestCrawlJobDetailsRequestIdRef.current += 1;
    setSelectedCrawlJob(null);
    setCrawlJobPages([]);
    setCrawlJobCandidates([]);
    setCrawlJobEvents([]);
    setSelectedCrawlCandidateIds([]);
    setCrawlCandidateFilters(createDefaultCrawlCandidateFilters());
    setCrawlCandidateInformationFiltersOpen(false);
    setCrawlJobApproveLoading(false);
    setSelectedCandidateDetail(null);
    setCandidateEditForm(null);
    setCandidateUpdateLoading(false);
    setCrawlEventPage(1);
    setCrawlDetailPagePage(1);
    setCrawlCandidatePage(1);
    setCrawlJobDetailsLoading(false);
    lastCrawlJobDetailsLoadErrorRef.current = null;
  };

  const resetBatchDraftReview = () => {
    latestBatchReviewRequestIdRef.current += 1;
    setBatchReviewItemId(null);
    setBatchReviewThread(null);
    setBatchReviewLoading(false);
    setBatchReviewItemActions({});
    setBatchReviewSubject("");
    setBatchReviewContentText("");
    setBatchReviewContentHtml("");
    setBatchReviewSelectedMaterialIds([]);
  };

  const syncBatchDraftReview = (thread: WorkspaceThreadDTO) => {
    const draft = getBatchReviewDraft(thread);
    setBatchReviewThread(thread);
    setBatchReviewSubject(draft.subject);
    setBatchReviewContentText(draft.text);
    setBatchReviewContentHtml(draft.html);
    setBatchReviewSelectedMaterialIds(draft.selectedMaterialIds);
  };

  const ensureBatchReviewThreadMatchesItem = (
    thread: WorkspaceThreadDTO,
    item: BatchTaskItemDTO,
    task: BatchTaskCardDTO,
  ) => {
    if (
      thread.current_task.id !== item.id ||
      thread.current_task.batch_task_id !== task.id
    ) {
      throw new Error("草稿任务与当前批量任务不一致，请刷新后重试");
    }
  };

  const openBatchDraftReview = async (item: BatchTaskItemDTO) => {
    if (!selectedBatchTask) {
      return;
    }
    if (
      batchReviewThread?.current_task.id === item.id &&
      batchReviewItemId === item.id
    ) {
      if (batchReviewLoading) {
        latestBatchReviewRequestIdRef.current += 1;
        setBatchReviewLoading(false);
      }
      return;
    }

    const isSwitchingItem = batchReviewThread !== null;
    const requestId = latestBatchReviewRequestIdRef.current + 1;
    latestBatchReviewRequestIdRef.current = requestId;
    if (!isSwitchingItem) {
      setBatchReviewItemId(item.id);
    }
    setBatchReviewLoading(true);
    try {
      const thread = await getBatchTaskItemThread(selectedBatchTask.id, item.id);
      if (latestBatchReviewRequestIdRef.current !== requestId) {
        return;
      }
      ensureBatchReviewThreadMatchesItem(thread, item, selectedBatchTask);
      setBatchReviewItemId(item.id);
      syncBatchDraftReview(thread);
    } catch (actionError) {
      if (latestBatchReviewRequestIdRef.current !== requestId) {
        return;
      }
      const message =
        actionError instanceof Error ? actionError.message : "加载草稿失败";
      notifyError("加载草稿失败", message);
      if (!isSwitchingItem) {
        setBatchReviewItemId(null);
        setBatchReviewThread(null);
      }
    } finally {
      if (latestBatchReviewRequestIdRef.current === requestId) {
        setBatchReviewLoading(false);
      }
    }
  };

  const handleBatchReviewContentChange = (value: RichEmailValue) => {
    setBatchReviewContentHtml(value.html);
    setBatchReviewContentText(value.text);
  };

  const buildBatchReviewPayload = () => ({
    subject: batchReviewSubject.trim() || null,
    body_text:
      batchReviewContentText.trim() ||
      deriveBatchReviewText("", batchReviewContentHtml),
    body_html: batchReviewContentHtml || null,
    selected_material_ids: batchReviewSelectedMaterialIds,
  });
  const batchReviewAttachmentTotalBytes = getSelectedAttachmentTotalBytes(
    batchReviewThread?.material_options ?? [],
    batchReviewSelectedMaterialIds,
  );

  const setBatchReviewItemAction = (
    itemId: number,
    type: BatchReviewItemActionType,
  ) => {
    setBatchReviewItemActions((current) => ({ ...current, [itemId]: type }));
  };

  const clearBatchReviewItemAction = (
    itemId: number,
    type: BatchReviewItemActionType,
  ) => {
    setBatchReviewItemActions((current) => {
      if (current[itemId] !== type) {
        return current;
      }
      const next = { ...current };
      delete next[itemId];
      return next;
    });
  };

  const handleRegenerateBatchDraft = async () => {
    const itemId = batchReviewItemId;
    if (!selectedBatchTask || !activeBatchReviewItem || itemId === null) {
      return;
    }
    const usesTemplateFallback =
      batchReviewThread?.current_task.draft_generation_source ===
        "template_fallback" ||
      activeBatchReviewItem.draft_generation_source === "template_fallback";
    if (
      usesTemplateFallback &&
      !batchReviewThread?.professor.research_direction?.trim()
    ) {
      notifyError(
        "无法使用 AI 改写",
        "该导师缺少研究方向。当前模板草稿不会受到影响，你可以直接审核，或先补全导师资料。",
      );
      return;
    }
    const confirmed = await confirm({
      title: usesTemplateFallback ? "确认使用 AI 改写？" : "确认重新生成草稿？",
      description: usesTemplateFallback
        ? "AI 改写会覆盖当前模板草稿，当前编辑内容将无法保留。"
        : "重新生成后会覆盖当前草稿内容，原草稿将无法保留。",
      confirmLabel: usesTemplateFallback ? "确认使用 AI 改写" : "确认重新生成",
      cancelLabel: usesTemplateFallback ? "继续审核模板草稿" : "先不重新生成",
    });
    if (!confirmed) {
      return;
    }
    setBatchReviewItemAction(itemId, "regenerate");
    try {
      const thread = await regenerateBatchTaskItemDraft(selectedBatchTask.id, itemId);
      ensureBatchReviewThreadMatchesItem(thread, activeBatchReviewItem, selectedBatchTask);
      setBatchReviewItemId((currentItemId) => {
        if (currentItemId === itemId) {
          syncBatchDraftReview(thread);
        }
        return currentItemId;
      });
      notifySuccess(usesTemplateFallback ? "AI 改写已完成" : "草稿已重新生成");
      if (selectedBatchTask) {
        await loadBatchTaskDetails(selectedBatchTask.id);
      }
    } catch (actionError) {
      const message =
        actionError instanceof Error ? actionError.message : "重新生成草稿失败";
      notifyError("重新生成草稿失败", message);
    } finally {
      clearBatchReviewItemAction(itemId, "regenerate");
    }
  };

  const handleApproveBatchDraft = async () => {
    if (!batchReviewThread?.current_task.id || !selectedBatchTask || !activeBatchReviewItem) {
      return;
    }
    const attachmentWarning = buildLargeAttachmentWarning(
      batchReviewAttachmentTotalBytes,
    );
    if (attachmentWarning) {
      const confirmed = await confirm({
        title: "附件超过 1 MB，仍要通过审核吗？",
        description: attachmentWarning,
        confirmLabel: "仍然通过",
        cancelLabel: "返回调整",
      });
      if (!confirmed) {
        return;
      }
    }
    const nextItem =
      reviewRequiredBatchTaskItems.find((item) => item.id !== activeBatchReviewItem.id) ??
      null;
    const itemId = activeBatchReviewItem.id;
    setBatchReviewItemAction(itemId, "submit");
    try {
      const thread = await approveBatchTaskItemDraft(
        selectedBatchTask.id,
        itemId,
        buildBatchReviewPayload(),
      );
      ensureBatchReviewThreadMatchesItem(thread, activeBatchReviewItem, selectedBatchTask);
      notifySuccess("草稿已审核通过");
      setSelectedBatchTaskItems((current) =>
        current.map((item) =>
          item.id === activeBatchReviewItem.id
            ? {
                ...item,
                status: "approved",
                next_action:
                  selectedBatchTask.schedule_type === "scheduled" && !item.scheduled_at
                    ? "missing_schedule"
                    : "waiting_send",
              }
            : item,
        ),
      );
      await Promise.all([loadBatchTaskDetails(selectedBatchTask.id), loadTasks()]);
      if (nextItem) {
        await openBatchDraftReview(nextItem);
      } else {
        resetBatchDraftReview();
      }
    } catch (actionError) {
      const message =
        actionError instanceof Error ? actionError.message : "审核草稿失败";
      notifyError("审核草稿失败", message);
    } finally {
      clearBatchReviewItemAction(itemId, "submit");
    }
  };

  const handleApproveAllBatchDrafts = async () => {
    if (!selectedBatchTask || reviewRequiredBatchTaskItems.length === 0) {
      return;
    }

    const taskId = selectedBatchTask.id;
    const itemIds = reviewRequiredBatchTaskItems.map((item) => item.id);
    const approvedCount = itemIds.length;
    const fallbackCount = reviewRequiredBatchTaskItems.filter(
      (item) => item.draft_generation_source === "template_fallback",
    ).length;
    const attachmentWarning = buildBulkLargeAttachmentWarning(
      reviewRequiredBatchTaskItems.map(
        (item) => item.selected_attachment_size_bytes ?? 0,
      ),
    );
    const deliveryDescription =
      selectedBatchTask.status === "paused"
        ? selectedBatchTask.schedule_type === "scheduled"
          ? `任务当前处于暂停状态；确认后仍不会发送，恢复任务后会按原计划（${buildScheduleLabel(selectedBatchTask)}）发送。`
          : "任务当前处于暂停状态；确认后仍不会发送，恢复任务后才会进入发送流程。"
        : selectedBatchTask.schedule_type === "scheduled"
          ? `确认后会按原计划（${buildScheduleLabel(selectedBatchTask)}）进入定时发送流程；邮件发出后无法撤回。`
          : "确认后会立即进入发送队列，邮件发出后无法撤回。";
    const ignoredDraftDescription =
      generatingDraftBatchTaskItems.length > 0 ||
      draftFailedBatchTaskItems.length > 0
        ? "本次只处理当前已经生成且仍为待审核状态的草稿；生成中或生成失败的邮件不会被处理。"
        : null;
    const confirmed = await confirm({
      title: `确认全部通过这 ${approvedCount} 封草稿？`,
      description: [
        "系统将直接采用每封邮件当前的主题、正文和附件设置，不再逐封检查。",
        fallbackCount > 0
          ? `其中 ${fallbackCount} 封因导师缺少研究方向，直接使用模板生成，未进行 AI 改写。`
          : null,
        attachmentWarning,
        deliveryDescription,
        ignoredDraftDescription,
      ]
        .filter(Boolean)
        .join("\n"),
      confirmLabel: attachmentWarning ? "仍然全部通过" : "确认全部通过",
      cancelLabel: "继续逐封审核",
      tone: "danger",
    });
    if (!confirmed) {
      return;
    }

    setBatchBulkApprovalLoading(true);
    try {
      const result = await approveAllBatchTaskDrafts(taskId, itemIds);
      setSelectedBatchTask((current) =>
        current?.id === taskId ? result.task : current,
      );
      notifySuccess(
        `已通过 ${result.approved_count} 封草稿`,
        result.task.status === "paused"
          ? "任务仍处于暂停状态，恢复后才会发送。"
          : result.task.schedule_type === "scheduled"
            ? "邮件将按原定时间和每日数量进入发送流程。"
            : "邮件已进入发送队列。",
      );
      await Promise.all([loadBatchTaskDetails(taskId), loadTasks()]);
    } catch (actionError) {
      const message =
        actionError instanceof Error
          ? actionError.message
          : "批量审核草稿失败";
      notifyError("批量审核草稿失败", message);
      await loadBatchTaskDetails(taskId);
    } finally {
      setBatchBulkApprovalLoading(false);
    }
  };

  const handleDeleteBatchDraftItem = async (item: BatchTaskItemDTO) => {
    if (!selectedBatchTask) {
      return;
    }
    const confirmed = await confirm({
      title: "从批量任务中删除这封草稿？",
      description: "删除后会从当前批量任务中彻底移除这位导师和对应草稿记录。",
      confirmLabel: "删除草稿",
      cancelLabel: "先保留",
      tone: "danger",
    });
    if (!confirmed) {
      return;
    }
    const nextItem =
      reviewRequiredBatchTaskItems.find((candidate) => candidate.id !== item.id) ??
      null;
    setBatchReviewItemAction(item.id, "delete");
    try {
      const result = await deleteBatchTaskItem(selectedBatchTask.id, item.id);
      notifySuccess("草稿已从批量任务中移除");
      setSelectedBatchTask(result.task);
      await Promise.all([loadBatchTaskDetails(selectedBatchTask.id), loadTasks()]);
      if (batchReviewItemId === item.id) {
        if (nextItem) {
          await openBatchDraftReview(nextItem);
        } else {
          resetBatchDraftReview();
        }
      }
    } catch (actionError) {
      const message =
        actionError instanceof Error ? actionError.message : "删除草稿失败";
      notifyError("删除草稿失败", message);
    } finally {
      clearBatchReviewItemAction(item.id, "delete");
    }
  };

  const handleRetryBatchTaskItemDraft = async (item: BatchTaskItemDTO) => {
    if (!selectedBatchTask) {
      return;
    }
    setBatchReviewItemAction(item.id, "regenerate");
    try {
      const result = await retryBatchTaskItemDraft(selectedBatchTask.id, item.id);
      setSelectedBatchTask(result.task);
      notifySuccess("已重新加入草稿生成队列");
      await Promise.all([loadBatchTaskDetails(selectedBatchTask.id), loadTasks()]);
    } catch (actionError) {
      const message =
        actionError instanceof Error ? actionError.message : "重新生成草稿失败";
      notifyError("重新生成草稿失败", message);
    } finally {
      clearBatchReviewItemAction(item.id, "regenerate");
    }
  };

  const handleSendBatchDraftNow = async () => {
    if (!batchReviewThread?.current_task.id || !selectedBatchTask || !activeBatchReviewItem) {
      return;
    }
    const attachmentWarning = buildLargeAttachmentWarning(
      batchReviewAttachmentTotalBytes,
    );
    const attachmentOverRecommendedLimit =
      isAttachmentTotalOverRecommendedLimit(batchReviewAttachmentTotalBytes);
    const confirmed = await confirm({
      title: attachmentOverRecommendedLimit
        ? "附件超过 1 MB，仍要发送吗？"
        : "确认立即发送这封真实邮件？",
      description: [
        `将真实发给 ${
          batchReviewThread?.professor.email ?? "当前导师邮箱"
        }，并附带 ${batchReviewSelectedMaterialIds.length} 份附件，共 ${formatFileSize(batchReviewAttachmentTotalBytes)}。`,
        attachmentWarning,
      ]
        .filter(Boolean)
        .join("\n"),
      confirmLabel: attachmentOverRecommendedLimit ? "仍然发送" : "确认发送",
      cancelLabel: attachmentOverRecommendedLimit ? "返回调整" : "再检查一下",
      tone: "danger",
    });
    if (!confirmed) {
      return;
    }

    const itemId = activeBatchReviewItem.id;
    setBatchReviewItemAction(itemId, "submit");
    try {
      const thread = await approveAndSendBatchTaskItemDraft(
        selectedBatchTask.id,
        itemId,
        buildBatchReviewPayload(),
      );
      ensureBatchReviewThreadMatchesItem(thread, activeBatchReviewItem, selectedBatchTask);
      const failureMessage = getEmailSendFailureMessage(
        thread.current_task.status,
        thread.current_task.last_error,
      );
      if (failureMessage) {
        syncBatchDraftReview(thread);
        notifyError("发送邮件失败", failureMessage);
      } else {
        notifySuccess("邮件已发送");
      }
      setSelectedBatchTaskItems((current) =>
        current.map((item) =>
          item.id === activeBatchReviewItem.id
            ? {
                ...item,
                status: thread.current_task.status ?? item.status,
                sent_at: thread.current_task.sent_at,
                last_send_attempt_at: thread.current_task.last_send_attempt_at,
                last_error: thread.current_task.last_error,
                next_action:
                  thread.current_task.status === "send_failed"
                    ? "send_failed"
                    : failureMessage
                      ? item.next_action
                      : null,
              }
            : item,
        ),
      );
      try {
        await Promise.all([loadBatchTaskDetails(selectedBatchTask.id), loadTasks()]);
      } catch (refreshError) {
        const message =
          refreshError instanceof Error ? refreshError.message : "刷新任务状态失败";
        notifyError("刷新任务状态失败", message);
      }
      if (!failureMessage) {
        resetBatchDraftReview();
      }
    } catch (actionError) {
      const message =
        actionError instanceof Error ? actionError.message : "发送邮件失败";
      notifyError("发送邮件失败", message);
    } finally {
      clearBatchReviewItemAction(itemId, "submit");
    }
  };

  const handleCancelBatchItemSend = async (item: BatchTaskItemDTO) => {
    if (!selectedBatchTask) {
      return;
    }
    const plannedTime = formatDisplayTime(item.scheduled_at);
    const confirmed = await confirm({
      title: `取消给${item.professor_name}的本次发送？`,
      description: `取消后，这封邮件不会在 ${plannedTime} 发送，不影响批次中的其他导师。之后可在原卡片上恢复。`,
      confirmLabel: "确认取消发送",
      cancelLabel: "保留发送",
      tone: "danger",
    });
    if (!confirmed) {
      return;
    }

    const taskId = selectedBatchTask.id;
    setBatchSendItemAction({ itemId: item.id, kind: "cancel" });
    try {
      const result = await cancelBatchTaskItemSend(taskId, item.id);
      setSelectedBatchTask((current) =>
        current?.id === taskId ? result.task : current,
      );
      notifySuccess(
        "已取消发送",
        `不会按原计划给${item.professor_name}发送邮件。`,
      );
      await Promise.all([loadBatchTaskDetails(taskId), loadTasks()]);
    } catch (actionError) {
      const message =
        actionError instanceof Error ? actionError.message : "取消发送失败";
      notifyError("取消发送失败", message);
      await loadBatchTaskDetails(taskId);
    } finally {
      setBatchSendItemAction((current) =>
        current?.itemId === item.id && current.kind === "cancel"
          ? null
          : current,
      );
    }
  };

  const handleRestoreBatchItemSend = async (item: BatchTaskItemDTO) => {
    if (!selectedBatchTask) {
      return;
    }
    const attachmentWarning = buildLargeAttachmentWarning(
      item.selected_attachment_size_bytes ?? 0,
    );
    if (attachmentWarning) {
      const confirmed = await confirm({
        title: "附件超过 1 MB，仍要恢复发送吗？",
        description: [
          attachmentWarning,
          `恢复后仍将按原计划于 ${formatDisplayTime(item.scheduled_at)} 发送。`,
        ].join("\n"),
        confirmLabel: "仍然恢复",
        cancelLabel: "保持取消",
      });
      if (!confirmed) {
        return;
      }
    }
    if (!isBatchItemScheduledInFuture(item, batchSendActionNowMs)) {
      notifyError("无法恢复发送", "原定发送时间已过，无法恢复发送");
      await loadBatchTaskDetails(selectedBatchTask.id);
      return;
    }

    const taskId = selectedBatchTask.id;
    const taskWasPaused = selectedBatchTask.status === "paused";
    setBatchSendItemAction({ itemId: item.id, kind: "restore" });
    try {
      const result = await restoreBatchTaskItemSend(taskId, item.id);
      setSelectedBatchTask((current) =>
        current?.id === taskId ? result.task : current,
      );
      notifySuccess(
        "已恢复发送",
        taskWasPaused
          ? `仍将按原计划于 ${formatDisplayTime(item.scheduled_at)} 发送；当前批量任务仍处于暂停状态。`
          : `仍将按原计划于 ${formatDisplayTime(item.scheduled_at)} 发送。`,
      );
      await Promise.all([loadBatchTaskDetails(taskId), loadTasks()]);
    } catch (actionError) {
      const message =
        actionError instanceof Error ? actionError.message : "恢复发送失败";
      notifyError("恢复发送失败", message);
      await loadBatchTaskDetails(taskId);
    } finally {
      setBatchSendItemAction((current) =>
        current?.itemId === item.id && current.kind === "restore"
          ? null
          : current,
      );
    }
  };

  const renderBatchItemSendButton = (item: BatchTaskItemDTO) => {
    const activeAction =
      batchSendItemAction?.itemId === item.id ? batchSendItemAction.kind : null;
    const actionBusy = batchSendItemAction !== null;
    if (item.batch_send_canceled_at) {
      if (
        !item.can_restore_send ||
        !isBatchItemScheduledInFuture(item, batchSendActionNowMs)
      ) {
        return null;
      }
      return (
        <button
          type="button"
          onClick={() => void handleRestoreBatchItemSend(item)}
          disabled={actionBusy}
          className="inline-flex items-center gap-1.5 rounded-xl border border-stone-300 bg-white px-3 py-2 text-xs font-medium text-stone-700 transition hover:border-primary/40 hover:bg-primary/5 hover:text-primary disabled:cursor-not-allowed disabled:opacity-60"
        >
          <RotateCcw className="h-3.5 w-3.5" />
          {activeAction === "restore" ? "恢复中..." : "恢复发送"}
        </button>
      );
    }
    if (!item.can_cancel_send) {
      return null;
    }
    return (
      <button
        type="button"
        onClick={() => void handleCancelBatchItemSend(item)}
        disabled={actionBusy}
        className="inline-flex items-center gap-1.5 rounded-xl border border-red-200 bg-white px-3 py-2 text-xs font-medium text-red-700 transition hover:border-red-300 hover:bg-red-50 disabled:cursor-not-allowed disabled:opacity-60"
      >
        <Ban className="h-3.5 w-3.5" />
        {activeAction === "cancel" ? "取消中..." : "取消发送"}
      </button>
    );
  };

  const renderBatchTaskItemAction = (item: BatchTaskItemDTO) => {
    if (!selectedBatchTask) {
      return null;
    }
    const action = buildBatchPendingItemAction(item, selectedBatchTask);
    const missingResearchDirection =
      isBatchTaskItemMissingResearchDirection(item);
    let actionContent: ReactNode = null;
    if (action?.kind === "message") {
      actionContent = (
        <span className="font-medium text-stone-600">
          {action.text}
        </span>
      );
    } else if (action?.kind === "review") {
      actionContent = (
        <button
          type="button"
          onClick={() => void openBatchDraftReview(item)}
          className="font-medium text-primary"
        >
          {action.text}
        </button>
      );
    } else if (action?.kind === "professor" && !missingResearchDirection) {
      actionContent = (
        <Link to={action.href} className="font-medium text-primary">
          {action.text}
        </Link>
      );
    } else if (action?.kind === "profile") {
      actionContent = (
        <Link to={action.href} className="font-medium text-primary">
          {action.text}
        </Link>
      );
    } else if (action?.kind === "retry") {
      actionContent = (
        <button
          type="button"
          onClick={() => void handleRetryBatchTaskItemDraft(item)}
          disabled={batchReviewItemActions[item.id] === "regenerate"}
          className="font-medium text-primary disabled:cursor-not-allowed disabled:text-stone-400"
        >
          {batchReviewItemActions[item.id] === "regenerate"
            ? "正在重新生成"
            : action.text}
        </button>
      );
    }
    if (!missingResearchDirection) {
      return actionContent;
    }

    return (
      <div className="flex flex-wrap items-center gap-3">
        <button
          type="button"
          onClick={() => void openProfessorEditDialog(item)}
          className="inline-flex items-center gap-1.5 font-medium text-primary"
        >
          <Pencil className="h-3.5 w-3.5" />
          补充资料
        </button>
        {actionContent}
      </div>
    );
  };

  const closeBatchTaskDetails = () => {
    latestBatchTaskDetailsRequestIdRef.current += 1;
    closeProfessorEditDialog();
    resetBatchDraftReview();
    setSelectedBatchTask(null);
    setSelectedBatchTaskItems([]);
    setBatchTaskDetailsLoading(false);
    setBatchSendItemAction(null);
    lastBatchTaskDetailsLoadErrorRef.current = null;
  };

  const closeMatchJobDetails = () => {
    latestMatchJobDetailsRequestIdRef.current += 1;
    setSelectedMatchJob(null);
    setSelectedMatchJobItems([]);
    setMatchJobDetailsLoading(false);
    lastMatchJobDetailsLoadErrorRef.current = null;
  };
  const closeInformationEnrichmentDetails = () => {
    latestInformationEnrichmentDetailsRequestIdRef.current += 1;
    setSelectedInformationEnrichmentJob(null);
    setSelectedInformationEnrichmentItems([]);
    setInformationEnrichmentDetailsLoading(false);
    lastInformationEnrichmentDetailsLoadErrorRef.current = null;
  };
  const requestCloseSelectedCandidateDetail = useCallback(async () => {
    if (candidateUpdateLoading) {
      return;
    }
    if (
      selectedCandidateDetail &&
      candidateEditForm &&
      hasUnsavedCrawlCandidateChanges(
        selectedCandidateDetail,
        candidateEditForm,
      )
    ) {
      const shouldDiscardChanges = await confirm({
        title: "放弃未保存的修改？",
        description: "关闭后，本次对候选导师信息的修改将不会保存。",
        confirmLabel: "不保存并关闭",
        cancelLabel: "继续编辑",
        tone: "danger",
      });
      if (!shouldDiscardChanges) {
        return;
      }
    }
    setCandidateEditForm(null);
    setSelectedCandidateDetail(null);
  }, [
    candidateEditForm,
    candidateUpdateLoading,
    confirm,
    selectedCandidateDetail,
  ]);
  const closeSelectedCandidateDetail = useCallback(() => {
    void requestCloseSelectedCandidateDetail();
  }, [requestCloseSelectedCandidateDetail]);
  const batchTaskDetailsLayer = useDismissableLayerClick(closeBatchTaskDetails);
  const matchJobDetailsLayer = useDismissableLayerClick(closeMatchJobDetails);
  const informationEnrichmentDetailsLayer = useDismissableLayerClick(
    closeInformationEnrichmentDetails,
  );
  const crawlJobDetailsLayer = useDismissableLayerClick(closeCrawlJobDetails);
  const candidateDetailLayer = useDismissableLayerClick(closeSelectedCandidateDetail);

  const handleOpenBatchResend = async (task: BatchTaskCardDTO) => {
    setResendDialogOpen(true);
    setResendLoading(true);
    setSelectedResendProfessorIds([]);
    try {
      const context = await getBatchTaskResendContext(task.id);
      setResendContext(context);
      setSelectedResendProfessorIds(
        context.items
          .filter((item) => item.selectable && item.default_selected && item.professor_id !== null)
          .map((item) => item.professor_id as number),
      );
    } catch (loadError) {
      const message = loadError instanceof Error ? loadError.message : "请稍后重试";
      notifyError("加载可重新发起项失败", message);
      setResendDialogOpen(false);
    } finally {
      setResendLoading(false);
    }
  };

  const handleToggleResendProfessor = (professorId: number) => {
    setSelectedResendProfessorIds((previous) =>
      previous.includes(professorId)
        ? previous.filter((item) => item !== professorId)
        : [...previous, professorId],
    );
  };

  const handleSelectAllResendProfessors = () => {
    if (!resendContext) {
      return;
    }
    setSelectedResendProfessorIds(
      resendContext.items
        .filter((item) => item.selectable && item.professor_id !== null)
        .map((item) => item.professor_id as number),
    );
  };

  const handleSubmitBatchResend = async () => {
    if (!resendContext || selectedResendProfessorIds.length === 0) {
      return;
    }
    const resendTemplateLabel = getOutreachTemplateSourceLabel(
      resendContext.defaults,
    );
    const resendGenerationModeLabel = getOutreachGenerationModeLabel(
      resendContext.defaults.outreach_generation_mode,
    );
    const confirmed = await confirm({
      title: "确认重新发起这批老师？",
      description: [
        "将自动切换到原任务身份，并优先沿用每位老师上次已审核或 AI 改写后的邮件。",
        `发信模板：${resendTemplateLabel}`,
        `写信方式：${resendGenerationModeLabel}`,
        "当前模板和模型只用于没有可复用草稿的邮件；发送日期和时间窗口需要重新设置。",
      ].join("\n"),
      confirmLabel: "去创建新任务",
      cancelLabel: "继续选择",
      tone: "danger",
    });
    if (!confirmed) {
      return;
    }
    setSelectedIdentityId(resendContext.task.identity_id);
    writeSelectedProfessorIdsForBatchTask(selectedResendProfessorIds);
    const requiresRegeneration = resendContext.items.some(
      (item) =>
        item.professor_id !== null &&
        selectedResendProfessorIds.includes(item.professor_id) &&
        item.content_reuse_kind === "regenerate",
    );
    writeBatchResendPrefillContext({
      sourceTaskId: resendContext.task.id,
      sourceTaskName: resendContext.task.name,
      identityId: resendContext.task.identity_id,
      professorIds: selectedResendProfessorIds,
      requiresRegeneration,
      defaults: resendContext.defaults,
      warnings: resendContext.warnings,
    });
    navigate("/create-task");
  };
  const handleDeleteBatchTask = async (task: BatchTaskCardDTO) => {
    const confirmed = await confirm({
      title: "删除任务",
      description: "删除后会移入回收站，不会清除任务记录，可在回收站恢复。",
      confirmLabel: "删除",
      cancelLabel: "先保留",
      tone: "danger",
    });
    if (!confirmed) {
      return;
    }
    try {
      await deleteBatchTask(task.id);
      notifySuccess("已移入回收站");
      if (selectedBatchTask?.id === task.id) {
        closeBatchTaskDetails();
      }
      await loadTasks();
    } catch (actionError) {
      const message =
        actionError instanceof Error ? actionError.message : "删除任务失败";
      notifyError("删除任务失败", message);
    }
  };

  const handleRestoreBatchTask = async (taskId: number) => {
    try {
      await restoreBatchTask(taskId);
      notifySuccess("已还原任务");
      await loadTasks();
    } catch (actionError) {
      const message =
        actionError instanceof Error ? actionError.message : "还原任务失败";
      notifyError("还原任务失败", message);
    }
  };

  const handleDeleteCrawlJob = async (job: CrawlJobSummaryDTO) => {
    const confirmed = await confirm({
      title: "删除任务",
      description: "删除后会移入回收站，不会清除任务记录，可在回收站恢复。",
      confirmLabel: "删除",
      cancelLabel: "先保留",
      tone: "danger",
    });
    if (!confirmed) {
      return;
    }
    try {
      await deleteCrawlJob(job.id);
      notifySuccess("已移入回收站");
      if (selectedCrawlJobId === job.id) {
        closeCrawlJobDetails();
      }
      await loadCrawlJobs();
    } catch (actionError) {
      const message =
        actionError instanceof Error ? actionError.message : "删除任务失败";
      notifyError("删除任务失败", message);
    }
  };

  const handleRestoreCrawlJob = async (jobId: number) => {
    try {
      await restoreCrawlJob(jobId);
      notifySuccess("已还原任务");
      await loadCrawlJobs();
    } catch (actionError) {
      const message =
        actionError instanceof Error ? actionError.message : "还原任务失败";
      notifyError("还原任务失败", message);
    }
  };

  const handleDeleteMatchJob = async (job: MatchAnalysisJobDTO) => {
    const confirmed = await confirm({
      title: "删除任务",
      description: "删除后会移入回收站，不会清除任务记录，可在回收站恢复。",
      confirmLabel: "删除",
      cancelLabel: "先保留",
      tone: "danger",
    });
    if (!confirmed) {
      return;
    }
    try {
      await deleteMatchAnalysisJob(job.id);
      notifySuccess("已移入回收站");
      if (selectedMatchJob?.id === job.id) {
        closeMatchJobDetails();
      }
      await loadMatchAnalysisJobs();
    } catch (actionError) {
      const message =
        actionError instanceof Error ? actionError.message : "删除任务失败";
      notifyError("删除任务失败", message);
    }
  };

  const handleRestoreMatchJob = async (jobId: number) => {
    try {
      await restoreMatchAnalysisJob(jobId);
      notifySuccess("已还原任务");
      await loadMatchAnalysisJobs();
    } catch (actionError) {
      const message =
        actionError instanceof Error ? actionError.message : "还原任务失败";
      notifyError("还原任务失败", message);
    }
  };

  const handleDeleteInformationEnrichmentJob = async (
    job: ProfessorInformationEnrichmentJobDTO,
  ) => {
    const confirmed = await confirm({
      title: "删除任务",
      description: "删除后会移入回收站，不会清除任务记录，可在回收站恢复。",
      confirmLabel: "删除",
      cancelLabel: "先保留",
      tone: "danger",
    });
    if (!confirmed) {
      return;
    }
    try {
      await deleteProfessorInformationEnrichmentJob(job.id);
      notifySuccess("已移入回收站");
      if (selectedInformationEnrichmentJob?.id === job.id) {
        closeInformationEnrichmentDetails();
      }
      await loadInformationEnrichmentJobs();
    } catch (actionError) {
      const message =
        actionError instanceof Error ? actionError.message : "删除任务失败";
      notifyError("删除任务失败", message);
    }
  };

  const handleRestoreInformationEnrichmentJob = async (jobId: number) => {
    try {
      await restoreProfessorInformationEnrichmentJob(jobId);
      notifySuccess("已还原任务");
      await loadInformationEnrichmentJobs();
    } catch (actionError) {
      const message =
        actionError instanceof Error ? actionError.message : "还原任务失败";
      notifyError("还原任务失败", message);
    }
  };

  const batchDraftReviewOpen = batchReviewItemId !== null;
  const batchReviewEditorHtml =
    batchReviewContentHtml || textToEmailHtml(batchReviewContentText);
  const batchReviewCanSubmit =
    Boolean(batchReviewThread?.current_task.id) &&
    Boolean(
      batchReviewSubject.trim() ||
        batchReviewContentText.trim() ||
        deriveBatchReviewText("", batchReviewContentHtml).trim(),
    );
  const activeBatchReviewAction =
    batchReviewItemId !== null
      ? batchReviewItemActions[batchReviewItemId] ?? null
      : null;
  const batchReviewUsesTemplateFallback =
    batchReviewThread?.current_task.draft_generation_source ===
      "template_fallback" ||
    activeBatchReviewItem?.draft_generation_source === "template_fallback";
  const batchReviewProfessorMissingResearchDirection =
    !batchReviewThread?.professor.research_direction?.trim();
  const batchReviewTemplateReferencesResearchDirection = [
    batchReviewThread?.current_task.outreach_template_subject,
    batchReviewThread?.current_task.outreach_template_body_text,
    batchReviewThread?.current_task.outreach_template_body_html,
  ].some((value) => /\{\{\s*research_direction\s*\}\}/.test(value ?? ""));
  const canSendBatchReviewImmediately =
    selectedBatchTask?.schedule_type === "immediate";

  return (
    <main className="mx-auto max-w-7xl px-6 py-8">
      <div className="rounded-3xl border border-stone-200 bg-[#fcfbf8] p-6 shadow-sm">
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div>
            <h1 className="text-3xl font-semibold text-stone-900">任务中心</h1>
          </div>
        </div>

        {!hasTaskSelection ? (
          <div className="mt-4 rounded-2xl border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-800">
            还没有选择身份和模型，批量邮件与匹配分析会在配置后显示；教师抓取和信息补全任务可继续查看。
          </div>
        ) : null}

        <div className="mt-6 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          <div className="rounded-2xl border border-stone-200 bg-white px-4 py-3">
            <div className="flex items-center gap-2 text-sm text-stone-500">
              <Mail className="h-4 w-4 text-primary" />
              批量邮件
            </div>
            <div className="mt-2 text-2xl font-semibold text-stone-900">
              {currentBatchTasks.length}
            </div>
          </div>
          <div className="rounded-2xl border border-stone-200 bg-white px-4 py-3">
            <div className="flex items-center gap-2 text-sm text-stone-500">
              <FileSearch className="h-4 w-4 text-sky-600" />
              教师抓取
            </div>
            <div className="mt-2 text-2xl font-semibold text-stone-900">
              {currentCrawlJobs.length}
            </div>
          </div>
          <div className="rounded-2xl border border-stone-200 bg-white px-4 py-3">
            <div className="flex items-center gap-2 text-sm text-stone-500">
              <Activity className="h-4 w-4 text-emerald-600" />
              运行中
            </div>
            <div className="mt-2 text-2xl font-semibold text-stone-900">
              {totalRunningCount}
            </div>
          </div>
          <div className="rounded-2xl border border-stone-200 bg-white px-4 py-3">
            <div className="flex items-center gap-2 text-sm text-stone-500">
              <Clock3 className="h-4 w-4 text-amber-600" />
              待处理
            </div>
            <div className="mt-2 text-2xl font-semibold text-stone-900">
              {totalAttentionCount}
            </div>
          </div>
        </div>
      </div>

      <div className="mt-6 flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <div className="inline-flex max-w-full gap-2 overflow-x-auto rounded-2xl border border-stone-200 bg-white p-1.5 shadow-sm">
          <button
            type="button"
            aria-label="批量邮件"
            disabled={!hasTaskSelection}
            onClick={() => setActiveTab("batch")}
            className={
              activeTab === "batch"
                ? "inline-flex min-h-10 items-center gap-2 rounded-xl bg-primary px-5 text-sm font-medium text-white"
                : "inline-flex min-h-10 items-center gap-2 rounded-xl px-5 text-sm font-medium text-stone-600 hover:bg-stone-50 disabled:cursor-not-allowed disabled:opacity-45 disabled:hover:bg-transparent"
            }
          >
            <Mail className="h-4 w-4" />
            批量邮件
            <span
              className={
                activeTab === "batch" ? "text-white/80" : "text-stone-400"
              }
            >
              {currentBatchTasks.length}
            </span>
          </button>
          <button
            type="button"
            aria-label="教师抓取"
            onClick={() => setActiveTab("crawl")}
            className={
              activeTab === "crawl"
                ? "inline-flex min-h-10 items-center gap-2 rounded-xl bg-primary px-5 text-sm font-medium text-white"
                : "inline-flex min-h-10 items-center gap-2 rounded-xl px-5 text-sm font-medium text-stone-600 hover:bg-stone-50"
            }
          >
            <FileSearch className="h-4 w-4" />
            教师抓取
            <span
              className={
                activeTab === "crawl" ? "text-white/80" : "text-stone-400"
              }
            >
              {currentCrawlJobs.length}
            </span>
          </button>
          <button
            type="button"
            aria-label="匹配分析"
            disabled={!hasTaskSelection}
            onClick={() => setActiveTab("match")}
            className={
              activeTab === "match"
                ? "inline-flex min-h-10 items-center gap-2 rounded-xl bg-primary px-5 text-sm font-medium text-white"
                : "inline-flex min-h-10 items-center gap-2 rounded-xl px-5 text-sm font-medium text-stone-600 hover:bg-stone-50 disabled:cursor-not-allowed disabled:opacity-45 disabled:hover:bg-transparent"
            }
          >
            <Sparkles className="h-4 w-4" />
            匹配分析
            <span
              className={
                activeTab === "match" ? "text-white/80" : "text-stone-400"
              }
            >
              {currentMatchAnalysisJobs.length}
            </span>
          </button>
          <button
            type="button"
            aria-label="信息补全"
            onClick={() => setActiveTab("enrichment")}
            className={
              activeTab === "enrichment"
                ? "inline-flex min-h-10 shrink-0 items-center gap-2 rounded-xl bg-primary px-5 text-sm font-medium text-white"
                : "inline-flex min-h-10 shrink-0 items-center gap-2 rounded-xl px-5 text-sm font-medium text-stone-600 hover:bg-stone-50"
            }
          >
            <Bot className="h-4 w-4" />
            信息补全
            <span
              className={
                activeTab === "enrichment"
                  ? "text-white/80"
                  : "text-stone-400"
              }
            >
              {currentInformationEnrichmentJobs.length}
            </span>
          </button>
        </div>

        <TaskListViewSwitch
          activeView={activeTaskListView}
          onViewChange={(view) =>
            setTaskListViews((current) => ({ ...current, [activeTab]: view }))
          }
        />
      </div>

      <section
        ref={taskListStartRef}
        tabIndex={-1}
        aria-label="任务列表"
        className="scroll-mt-24 focus:outline-none"
      >
      {activeTab === "batch" && loading ? (
        <div className="mt-6 flex items-center justify-center gap-2 rounded-3xl border border-stone-200 bg-white px-6 py-14 text-sm text-stone-500 shadow-sm">
          <Loader2 className="h-4 w-4 animate-spin" />
          正在加载任务列表...
        </div>
      ) : activeTab === "batch" && tasks.length === 0 ? (
        <div className="mt-6 rounded-3xl border border-dashed border-stone-300 bg-white px-6 py-14 text-center text-sm text-stone-500 shadow-sm">
          {activeTaskListView === "trash" ? "回收站暂无任务。" : "暂无任务。可从首页创建。"}
        </div>
      ) : activeTab === "batch" ? (
        <>
          <div className="mt-6 grid gap-4">
            {visibleBatchTasks.map((task) => {
              const progress =
                task.target_count === 0
                  ? 0
                  : Math.round(
                      (task.completed_count / task.target_count) * 100,
                    );
              const waitingSendCount = getBatchTaskWaitingSendCount(task);

              return (
                <article
                  key={task.id}
                  className="rounded-2xl border border-stone-200 bg-white px-5 py-5 shadow-sm"
                >
                  <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_240px_minmax(260px,auto)_auto] lg:items-center">
                    <div className="min-w-0">
                      <div className="flex items-center gap-2 text-xs font-medium text-stone-500">
                        <Mail className="h-4 w-4 text-primary" />
                        批量邮件任务
                      </div>
                      <div className="flex flex-wrap items-center gap-2">
                        <h2 className="mt-2 truncate text-base font-semibold text-stone-900">
                          {task.name}
                        </h2>
                        <span className="rounded-full bg-stone-100 px-2.5 py-1 text-xs font-medium text-stone-700">
                          {BATCH_TASK_STATUS_LABELS[task.status]}
                        </span>
                      </div>
                      <p className="mt-1 truncate text-sm text-stone-500">
                        {buildScheduleLabel(task)}
                      </p>
                    </div>

                    <div>
                      <div className="mb-2 flex items-center justify-between text-xs text-stone-500">
                        <span>
                          {task.completed_count}/{task.target_count}
                        </span>
                        <span>{progress}%</span>
                      </div>
                      <div className="h-2 overflow-hidden rounded-full bg-stone-100">
                        <div
                          className="h-full rounded-full bg-primary"
                          style={{ width: `${progress}%` }}
                        />
                      </div>
                    </div>

                    <div className="flex flex-wrap items-center gap-2 lg:justify-end">
                      <span className="rounded-full bg-stone-50 px-2.5 py-1 text-xs text-stone-600">
                        待生成 {task.pending_generation_count}
                      </span>
                      {task.generating_draft_count > 0 ? (
                        <span className="rounded-full bg-sky-50 px-2.5 py-1 text-xs text-sky-700">
                          生成中 {task.generating_draft_count}
                        </span>
                      ) : null}
                      {task.draft_failed_count > 0 ? (
                        <span className="rounded-full bg-red-50 px-2.5 py-1 text-xs text-red-700">
                          草稿失败 {task.draft_failed_count}
                        </span>
                      ) : null}
                      <span className="rounded-full bg-amber-50 px-2.5 py-1 text-xs text-amber-700">
                        待审核 {task.review_required_count}
                      </span>
                      <span className="rounded-full bg-primary/10 px-2.5 py-1 text-xs text-primary">
                        待发送 {waitingSendCount}
                      </span>
                      {task.canceled_send_count > 0 ? (
                        <span className="rounded-full bg-red-50 px-2.5 py-1 text-xs text-red-700">
                          已取消发送 {task.canceled_send_count}
                        </span>
                      ) : null}
                      <span className="rounded-full bg-emerald-50 px-2.5 py-1 text-xs text-emerald-700">
                        已发送 {task.sent_count + task.replied_count}
                      </span>
                      {task.failed_count > 0 ? (
                        <span className="rounded-full bg-red-50 px-2.5 py-1 text-xs text-red-700">
                          失败 {task.failed_count}
                        </span>
                      ) : null}
                    </div>

                    <div className="flex flex-wrap items-center gap-2 lg:justify-end">
                      {activeTaskListView === "trash" ? (
                        <button
                          type="button"
                          onClick={() => void handleRestoreBatchTask(task.id)}
                          className="ui-btn-primary"
                        >
                          <RotateCcw className="h-4 w-4" />
                          还原任务
                        </button>
                      ) : null}
                      {activeTaskListView === "current" &&
                      canDeleteBatchTask(task) ? (
                        <button
                          type="button"
                          onClick={() => void handleDeleteBatchTask(task)}
                          className="ui-btn-danger"
                        >
                          <Trash2 className="h-4 w-4" />
                          删除
                        </button>
                      ) : null}
                      {activeTaskListView === "current" &&
                      task.status === "running" ? (
                        <button
                          type="button"
                          onClick={() => void handleAction(task.id, "pause")}
                          className="ui-btn-secondary"
                        >
                          <Pause className="h-4 w-4" />
                          暂停
                        </button>
                      ) : null}
                      {activeTaskListView === "current" &&
                      task.status === "paused" ? (
                        <button
                          type="button"
                          onClick={() => void handleAction(task.id, "resume")}
                          className="ui-btn-secondary"
                        >
                          <Play className="h-4 w-4" />
                          继续
                        </button>
                      ) : null}
                      {activeTaskListView === "current" &&
                      task.status !== "stopped" &&
                      task.status !== "completed" &&
                      task.status !== "expired" ? (
                        <button
                          type="button"
                          onClick={() => void handleAction(task.id, "stop")}
                          className="ui-btn-danger"
                        >
                          <Square className="h-4 w-4" />
                          终止
                        </button>
                      ) : null}
                      <button
                        type="button"
                        onClick={() => setSelectedBatchTask(task)}
                        className="inline-flex h-10 w-10 items-center justify-center rounded-full border border-stone-200 bg-white text-stone-500 transition hover:border-primary/30 hover:bg-primary/5 hover:text-primary"
                        aria-label="查看详情"
                        title="查看详情"
                      >
                        <ChevronRight className="h-4 w-4" />
                      </button>
                    </div>
                  </div>
                </article>
              );
            })}
          </div>
          <Pagination
            page={safeBatchPage}
            pageSize={batchPageSize}
            totalCount={tasks.length}
            onChange={handleBatchPaginationChange}
            ariaLabel="批量邮件任务分页"
            pageSizeOptions={TASKS_PAGE_SIZE_OPTIONS}
            unitLabel="个"
            itemLabel="个任务"
            pageStatusPrefix="第 "
            focusTargetRef={taskListStartRef}
            className="mt-4 rounded-2xl border border-stone-200 bg-white px-4 py-3 shadow-sm"
          />
        </>
      ) : activeTab === "match" && matchJobsLoading && matchAnalysisJobs.length === 0 ? (
        <div className="mt-6 flex items-center justify-center gap-2 rounded-3xl border border-stone-200 bg-white px-6 py-14 text-sm text-stone-500 shadow-sm">
          <Loader2 className="h-4 w-4 animate-spin" />
          正在加载匹配分析任务列表...
        </div>
      ) : activeTab === "match" && matchAnalysisJobs.length === 0 ? (
        <div className="mt-6 rounded-3xl border border-dashed border-stone-300 bg-white px-6 py-14 text-center text-sm text-stone-500 shadow-sm">
          {activeTaskListView === "trash"
            ? "回收站暂无任务。"
            : "暂无匹配分析任务。可从首页创建。"}
        </div>
      ) : activeTab === "match" ? (
        <>
          <div className="mt-6 grid gap-4">
            {visibleMatchJobs.map((job) => (
              <article
                key={job.id}
                className="rounded-2xl border border-stone-200 bg-white px-5 py-5 shadow-sm"
              >
                <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_280px_auto] lg:items-center">
                  <div className="min-w-0">
                    <div className="flex items-center gap-2 text-xs font-medium text-stone-500">
                      <Sparkles className="h-4 w-4 text-primary" />
                      匹配分析任务
                    </div>
                    <div className="mt-2 flex flex-wrap items-center gap-2">
                      <h2 className="truncate text-base font-semibold text-stone-900">
                        {job.name}
                      </h2>
                      <span
                        className={`rounded-full border px-2.5 py-1 text-xs font-medium ${MATCH_ANALYSIS_JOB_STATUS_TONES[job.status]}`}
                      >
                        {MATCH_ANALYSIS_JOB_STATUS_LABELS[job.status]}
                      </span>
                    </div>
                    <p className="mt-1 text-sm text-stone-500">
                      成功 {job.succeeded_count} / 失败 {job.failed_count} / 跳过 {job.skipped_count} / 共 {job.target_count}
                    </p>
                    <p className="mt-1 text-xs text-stone-500">
                      {job.match_source_identity_id === null ? (
                        <>匹配依据 原身份已删除</>
                      ) : (
                        <>
                          {job.match_source_identity_id &&
                          job.match_source_identity_id !== job.identity_id
                            ? '组内统一匹配依据'
                            : '匹配依据'}{' '}
                          {identities.find(
                            (identity) =>
                              identity.id ===
                              (job.match_source_identity_id ?? job.identity_id),
                          )?.profile_name ??
                            `身份 #${job.match_source_identity_id ?? job.identity_id}`}
                        </>
                      )}
                    </p>
                  </div>
                  <div className="min-w-0 space-y-2">
                    <TokenUsageBreakdown
                      inputTokens={job.total_prompt_tokens}
                      outputTokens={job.total_completion_tokens}
                      cachedTokens={job.total_cached_tokens}
                      totalTokens={job.total_tokens}
                      ariaLabel={`${job.name} Token 使用汇总`}
                    />
                    <div className="text-right text-xs text-stone-500">
                      更新 {formatDisplayTime(job.updated_at, { withSeconds: true })}
                    </div>
                  </div>
                  <div className="flex flex-wrap items-center gap-2 lg:justify-end">
                    {activeTaskListView === "trash" ? (
                      <button
                        type="button"
                        onClick={() => void handleRestoreMatchJob(job.id)}
                        className="ui-btn-primary"
                      >
                        <RotateCcw className="h-4 w-4" />
                        还原任务
                      </button>
                    ) : null}
                    {activeTaskListView === "current" &&
                    canDeleteMatchJob(job) ? (
                      <button
                        type="button"
                        onClick={() => void handleDeleteMatchJob(job)}
                        className="ui-btn-danger"
                      >
                        <Trash2 className="h-4 w-4" />
                        删除
                      </button>
                    ) : null}
                    {activeTaskListView === "current" &&
                    (job.status === "queued" || job.status === "running") ? (
                      <button
                        type="button"
                        onClick={() => void handleCancelMatchJob(job.id)}
                        className="ui-btn-danger"
                        disabled={cancelingMatchJobId === job.id}
                      >
                        <Square className="h-4 w-4" />
                        取消
                      </button>
                    ) : null}
                    {activeTaskListView === "current" && (
                      job.status === "partial_failed" ||
                      job.status === "failed" ||
                      job.status === "canceled"
                    ) ? (
                      <button
                        type="button"
                        onClick={() => void handleRetryMatchJob(job.id)}
                        className="ui-btn-secondary"
                        disabled={retryingMatchJobId === job.id}
                      >
                        <Play className="h-4 w-4" />
                        重试失败项
                      </button>
                    ) : null}
                    <button
                      type="button"
                      onClick={() => setSelectedMatchJob(job)}
                      className="inline-flex h-10 w-10 items-center justify-center rounded-full border border-stone-200 bg-white text-stone-500 transition hover:border-primary/30 hover:bg-primary/5 hover:text-primary"
                      aria-label={`查看匹配分析任务 ${job.name}`}
                      title="查看详情"
                    >
                      <ChevronRight className="h-4 w-4" />
                    </button>
                  </div>
                </div>
              </article>
            ))}
          </div>
          <Pagination
            page={safeMatchPage}
            pageSize={matchPageSize}
            totalCount={matchAnalysisJobs.length}
            onChange={handleMatchPaginationChange}
            ariaLabel="匹配分析任务分页"
            pageSizeOptions={TASKS_PAGE_SIZE_OPTIONS}
            unitLabel="个"
            itemLabel="个任务"
            pageStatusPrefix="第 "
            focusTargetRef={taskListStartRef}
            className="mt-4 rounded-2xl border border-stone-200 bg-white px-4 py-3 shadow-sm"
          />
        </>
      ) : activeTab === "enrichment" &&
        informationEnrichmentJobsLoading &&
        informationEnrichmentJobs.length === 0 ? (
        <div className="mt-6 flex items-center justify-center gap-2 rounded-3xl border border-stone-200 bg-white px-6 py-14 text-sm text-stone-500 shadow-sm">
          <Loader2 className="h-4 w-4 animate-spin" />
          正在加载信息补全任务列表...
        </div>
      ) : activeTab === "enrichment" &&
        informationEnrichmentJobs.length === 0 ? (
        <div className="mt-6 rounded-3xl border border-dashed border-stone-300 bg-white px-6 py-14 text-center text-sm text-stone-500 shadow-sm">
          {activeTaskListView === "trash"
            ? "回收站暂无任务。"
            : "暂无信息补全任务。可从导师管理页批量创建。"}
        </div>
      ) : activeTab === "enrichment" ? (
        <>
          <div className="mt-6 grid gap-4">
            {visibleInformationEnrichmentJobs.map((job) => {
              const progress =
                job.target_count === 0
                  ? 0
                  : Math.round((job.completed_count / job.target_count) * 100);
              const canRetry = job.failed_count + job.canceled_count > 0;

              return (
                <article
                  key={job.id}
                  className="rounded-2xl border border-stone-200 bg-white px-5 py-5 shadow-sm"
                >
                  <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_240px_minmax(250px,auto)_auto] lg:items-center">
                    <div className="min-w-0">
                      <div className="flex items-center gap-2 text-xs font-medium text-stone-500">
                        <Bot className="h-4 w-4 text-primary" />
                        信息补全任务
                      </div>
                      <div className="mt-2 flex flex-wrap items-center gap-2">
                        <h2 className="min-w-0 truncate text-base font-semibold text-stone-900">
                          {job.name}
                        </h2>
                        <span
                          className={`shrink-0 rounded-full border px-2.5 py-1 text-xs font-medium ${INFORMATION_ENRICHMENT_JOB_STATUS_TONES[job.status]}`}
                        >
                          {PROFESSOR_INFORMATION_ENRICHMENT_STATUS_LABELS[job.status]}
                        </span>
                      </div>
                      <p className="mt-1 text-sm text-stone-500">
                        创建于 {formatDisplayTime(job.created_at)}
                      </p>
                      {job.last_error ? (
                        <p className="mt-2 line-clamp-2 break-all text-xs leading-5 text-red-700">
                          {job.last_error}
                        </p>
                      ) : null}
                    </div>

                    <div className="min-w-0">
                      <div className="mb-2 flex items-center justify-between text-xs text-stone-500">
                        <span>
                          {job.completed_count}/{job.target_count}
                        </span>
                        <span>{progress}%</span>
                      </div>
                      <div className="h-2 overflow-hidden rounded-full bg-stone-100">
                        <div
                          className="h-full rounded-full bg-primary"
                          style={{ width: `${progress}%` }}
                        />
                      </div>
                      <TokenUsageBreakdown
                        inputTokens={job.input_tokens}
                        outputTokens={job.output_tokens}
                        cachedTokens={job.cached_tokens}
                        totalTokens={job.total_tokens}
                        ariaLabel={`${job.name} Token 使用汇总`}
                        className="mt-3"
                      />
                      <div className="mt-2 text-right text-xs text-stone-500">
                        耗时 {formatDuration(job.duration_seconds)}
                      </div>
                    </div>

                    <div className="flex flex-wrap items-center gap-2 lg:justify-end">
                      <span className="rounded-full bg-emerald-50 px-2.5 py-1 text-xs text-emerald-700">
                        成功 {job.succeeded_count}
                      </span>
                      <span className="rounded-full bg-red-50 px-2.5 py-1 text-xs text-red-700">
                        失败 {job.failed_count}
                      </span>
                      <span className="rounded-full bg-amber-50 px-2.5 py-1 text-xs text-amber-700">
                        跳过 {job.skipped_count}
                      </span>
                      {job.canceled_count > 0 ? (
                        <span className="rounded-full bg-stone-100 px-2.5 py-1 text-xs text-stone-600">
                          取消 {job.canceled_count}
                        </span>
                      ) : null}
                    </div>

                    <div className="flex flex-wrap items-center gap-2 lg:justify-end">
                      {activeTaskListView === "trash" ? (
                        <button
                          type="button"
                          onClick={() =>
                            void handleRestoreInformationEnrichmentJob(job.id)
                          }
                          className="ui-btn-primary"
                        >
                          <RotateCcw className="h-4 w-4" />
                          还原任务
                        </button>
                      ) : null}
                      {activeTaskListView === "current" &&
                      canDeleteInformationEnrichmentJob(job) ? (
                        <button
                          type="button"
                          onClick={() =>
                            void handleDeleteInformationEnrichmentJob(job)
                          }
                          className="ui-btn-danger"
                        >
                          <Trash2 className="h-4 w-4" />
                          删除
                        </button>
                      ) : null}
                      {activeTaskListView === "current" &&
                      (job.status === "queued" || job.status === "running") ? (
                        <button
                          type="button"
                          onClick={() =>
                            void handleCancelInformationEnrichmentJob(job.id)
                          }
                          className="ui-btn-danger"
                          disabled={cancelingInformationEnrichmentJobId === job.id}
                        >
                          {cancelingInformationEnrichmentJobId === job.id ? (
                            <Loader2 className="h-4 w-4 animate-spin" />
                          ) : (
                            <Square className="h-4 w-4" />
                          )}
                          取消
                        </button>
                      ) : null}
                      {activeTaskListView === "current" && canRetry ? (
                        <button
                          type="button"
                          onClick={() =>
                            void handleRetryInformationEnrichmentJob(job.id)
                          }
                          className="ui-btn-secondary"
                          disabled={retryingInformationEnrichmentJobId === job.id}
                        >
                          {retryingInformationEnrichmentJobId === job.id ? (
                            <Loader2 className="h-4 w-4 animate-spin" />
                          ) : (
                            <Play className="h-4 w-4" />
                          )}
                          重试失败项
                        </button>
                      ) : null}
                      <button
                        type="button"
                        onClick={() => setSelectedInformationEnrichmentJob(job)}
                        className="inline-flex h-10 w-10 items-center justify-center rounded-full border border-stone-200 bg-white text-stone-500 transition hover:border-primary/30 hover:bg-primary/5 hover:text-primary"
                        aria-label={`查看信息补全任务 ${job.name}`}
                        title="查看详情"
                      >
                        <ChevronRight className="h-4 w-4" />
                      </button>
                    </div>
                  </div>
                </article>
              );
            })}
          </div>
          <Pagination
            page={safeInformationEnrichmentPage}
            pageSize={informationEnrichmentPageSize}
            totalCount={informationEnrichmentJobs.length}
            onChange={handleInformationEnrichmentPaginationChange}
            ariaLabel="信息补全任务分页"
            pageSizeOptions={TASKS_PAGE_SIZE_OPTIONS}
            unitLabel="个"
            itemLabel="个任务"
            pageStatusPrefix="第 "
            focusTargetRef={taskListStartRef}
            className="mt-4 rounded-2xl border border-stone-200 bg-white px-4 py-3 shadow-sm"
          />
        </>
      ) : crawlJobsLoading && crawlJobs.length === 0 ? (
        <div className="mt-6 flex items-center justify-center gap-2 rounded-3xl border border-stone-200 bg-white px-6 py-14 text-sm text-stone-500 shadow-sm">
          <Loader2 className="h-4 w-4 animate-spin" />
          正在加载抓取任务列表...
        </div>
      ) : crawlJobs.length === 0 ? (
        <div className="mt-6 rounded-3xl border border-dashed border-stone-300 bg-white px-6 py-14 text-center text-sm text-stone-500 shadow-sm">
          {activeTaskListView === "trash"
            ? "回收站暂无任务。"
            : "暂无抓取任务。可从导师管理页创建。"}
        </div>
      ) : (
        <>
          <div className="mt-6 grid gap-4">
            {visibleCrawlJobs.map((job) => (
              <CrawlJobCard
                key={job.id}
                job={job}
                listView={taskListViews.crawl}
                pausingCrawlJobId={pausingCrawlJobId}
                resumingCrawlJobId={resumingCrawlJobId}
                retryingCrawlJobId={retryingCrawlJobId}
                resumingCrawlJobReviewId={resumingCrawlJobReviewId}
                onOpenDetails={(currentJob) => {
                  safeRecordUserAction({
                    eventName: "tasks.crawl_job_detail_opened",
                    data: { jobId: currentJob.id, status: currentJob.status },
                  });
                  setSelectedCrawlJob(currentJob);
                }}
                onPause={(jobId) => void handlePauseCrawlJob(jobId)}
                onResume={(jobId) => void handleResumeCrawlJob(jobId)}
                onCancel={(jobId) => void handleCancelCrawlJob(jobId)}
                onRetry={(jobId) => void handleRetryCrawlJob(jobId)}
                onResumeReview={(jobId) => void handleResumeCrawlJobReview(jobId)}
                onDelete={(currentJob) => void handleDeleteCrawlJob(currentJob)}
                onRestore={(jobId) => void handleRestoreCrawlJob(jobId)}
                formatUpdatedAt={(value) =>
                  formatDisplayTime(value, { withSeconds: true })
                }
              />
            ))}
          </div>
          <Pagination
            page={safeCrawlPage}
            pageSize={crawlPageSize}
            totalCount={crawlJobs.length}
            onChange={handleCrawlPaginationChange}
            ariaLabel="教师抓取任务分页"
            pageSizeOptions={TASKS_PAGE_SIZE_OPTIONS}
            unitLabel="个"
            itemLabel="个任务"
            pageStatusPrefix="第 "
            focusTargetRef={taskListStartRef}
            className="mt-4 rounded-2xl border border-stone-200 bg-white px-4 py-3 shadow-sm"
          />
        </>
      )}
      </section>
      {selectedBatchTask ? (
        <div
          className="fixed inset-0 z-50 flex items-stretch justify-end bg-stone-950/30 p-0 sm:p-6"
          onClick={batchTaskDetailsLayer.onBackdropClick}
          onMouseDown={batchTaskDetailsLayer.onBackdropMouseDown}
        >
          <section
            role="dialog"
            aria-label="批量任务详情"
            className={
              batchDraftReviewOpen
                ? "flex h-full w-full flex-col overflow-hidden bg-white shadow-xl sm:max-w-7xl sm:rounded-3xl"
                : "flex h-full w-full flex-col overflow-hidden bg-white shadow-xl sm:max-w-4xl sm:rounded-3xl"
            }
            onClick={batchTaskDetailsLayer.onContentClick}
            onMouseDown={batchTaskDetailsLayer.onContentMouseDown}
          >
            <div className="flex flex-col gap-4 border-b border-stone-200 bg-[#fcfbf8] px-4 py-4 sm:flex-row sm:items-start sm:justify-between sm:px-6 sm:py-5">
              <div className="min-w-0">
                <div className="flex items-center gap-2 text-xs font-medium text-stone-500">
                  <Mail className="h-4 w-4 text-primary" />
                  {batchDraftReviewOpen ? "批量草稿审核" : "批量邮件任务"}
                </div>
                <h2 className="mt-2 break-words text-xl font-semibold text-stone-900">
                  {batchDraftReviewOpen ? "批量审核草稿" : selectedBatchTask.name}
                </h2>
                <p className="mt-2 text-sm text-stone-500">
                  {batchDraftReviewOpen
                    ? `${selectedBatchTask.name} · ${activeBatchReviewItem?.professor_name ?? "正在加载"}`
                    : buildScheduleLabel(selectedBatchTask)}
                </p>
              </div>
              <div className="flex w-full flex-wrap gap-2 sm:w-auto sm:shrink-0 sm:justify-end">
                {!batchDraftReviewOpen && canOpenBatchResend(selectedBatchTask, activeTaskListView) ? (
                  <button
                    type="button"
                    onClick={() => void handleOpenBatchResend(selectedBatchTask)}
                    className="ui-btn-primary"
                  >
                    <RotateCcw className="h-4 w-4" />
                    重新发起未成功项
                  </button>
                ) : null}
                {batchDraftReviewOpen ? (
                  <button
                    type="button"
                    onClick={resetBatchDraftReview}
                    className="ui-btn-secondary"
                  >
                    <ChevronLeft className="h-4 w-4" />
                    返回详情
                  </button>
                ) : null}
                <button
                  type="button"
                  onClick={closeBatchTaskDetails}
                  className="ui-btn-secondary"
                  aria-label="关闭"
                >
                  <X className="h-4 w-4" />
                  关闭
                </button>
              </div>
            </div>

            <div
              data-testid="batch-task-detail-scroll"
              className="flex-1 overflow-y-auto overscroll-contain px-6 py-5"
            >
              {batchDraftReviewOpen ? (
                <div className="grid min-h-full gap-5 xl:grid-cols-[320px_minmax(0,1fr)]">
                  <aside className="rounded-3xl border border-stone-200 bg-stone-50/70 p-4">
                    <div className="flex items-center justify-between gap-3">
                      <div>
                        <h3 className="text-sm font-semibold text-stone-900">
                          待审核队列
                        </h3>
                        <p className="mt-1 text-xs text-stone-500">
                          {batchReviewQueueItems.length} 封草稿等待处理
                        </p>
                      </div>
                      {batchReviewLoading && !batchReviewThread ? (
                        <Loader2 className="h-4 w-4 animate-spin text-stone-400" />
                      ) : null}
                    </div>
                    <div className="mt-4 space-y-2">
                      {batchReviewQueueItems.map((item) => {
                        const itemGeneratingDraft =
                          item.status === "generating_draft";
                        const itemAction = batchReviewItemActions[item.id] ?? null;
                        const itemDeleting = itemAction === "delete";
                        const itemRegenerating = itemAction === "regenerate";
                        const itemBusyGenerating =
                          itemGeneratingDraft || itemRegenerating;
                        return (
                        <div
                          key={item.id}
                          className={
                            item.id === batchReviewItemId
                              ? "flex w-full items-stretch overflow-hidden rounded-2xl border border-primary/25 bg-white shadow-sm"
                              : "flex w-full items-stretch overflow-hidden rounded-2xl border border-stone-200 bg-white/70 transition hover:border-primary/20 hover:bg-white"
                          }
                        >
                          <button
                            type="button"
                            onClick={() => void openBatchDraftReview(item)}
                            disabled={itemBusyGenerating}
                            className="min-w-0 flex-1 px-4 py-3 text-left disabled:cursor-wait"
                          >
                            <div className="flex items-start justify-between gap-3">
                              <div className="min-w-0">
                                <div className="flex flex-wrap items-center gap-2">
                                  <span className="truncate text-sm font-semibold text-stone-900">
                                    {item.professor_name}
                                  </span>
                                  {itemBusyGenerating ? (
                                    <span className="inline-flex items-center gap-1 rounded-full bg-sky-50 px-2 py-0.5 text-xs text-sky-700">
                                      <Loader2 className="h-3 w-3 animate-spin" />
                                      重新生成中
                                    </span>
                                  ) : null}
                                  {item.draft_generation_source ===
                                  "template_fallback" ? (
                                    <span className="inline-flex items-center rounded-full bg-amber-100 px-2 py-0.5 text-xs font-medium text-amber-800">
                                      未进行 AI 改写
                                    </span>
                                  ) : null}
                                </div>
                                <div className="mt-1 truncate text-xs text-stone-500">
                                  {[item.professor_title, item.professor_school]
                                    .filter(Boolean)
                                    .join(" / ") || "暂无补充信息"}
                                </div>
                              </div>
                              {item.match_score !== null ? (
                                <span className="shrink-0 rounded-full bg-primary/10 px-2 py-1 text-xs text-primary">
                                  {item.match_score}
                                </span>
                              ) : null}
                            </div>
                          </button>
                          <button
                            type="button"
                            aria-label="删除草稿"
                            onClick={() => void handleDeleteBatchDraftItem(item)}
                            disabled={itemDeleting || itemBusyGenerating}
                            className="flex w-11 shrink-0 items-center justify-center border-l border-stone-100 text-stone-400 transition hover:bg-red-50 hover:text-red-600 disabled:cursor-not-allowed disabled:opacity-50"
                          >
                            <Trash2 className="h-4 w-4" />
                          </button>
                        </div>
                      )})}
                    </div>
                  </aside>

                  <section className="min-w-0 rounded-3xl border border-stone-200 bg-white p-5 shadow-sm">
                    {batchReviewLoading && !batchReviewThread ? (
                      <div className="flex min-h-[520px] items-center justify-center gap-2 text-sm text-stone-500">
                        <Loader2 className="h-4 w-4 animate-spin" />
                        正在加载草稿...
                      </div>
                    ) : batchReviewThread ? (
                      <div className="grid gap-5 xl:grid-cols-[minmax(0,1fr)_280px]">
                        <div className="min-w-0">
                          {batchReviewUsesTemplateFallback ? (
                            <section
                              aria-label="未进行 AI 改写提示"
                              className="mb-5 rounded-2xl border border-amber-200 bg-amber-50 px-4 py-3 text-sm leading-6 text-amber-900"
                            >
                              <div className="flex items-center gap-2 font-semibold">
                                <Sparkles className="h-4 w-4" />
                                当前草稿未进行 AI 改写
                              </div>
                              <p className="mt-1">
                                {batchReviewProfessorMissingResearchDirection
                                  ? "该导师缺少研究方向，系统已直接使用"
                                  : "该草稿生成时导师缺少研究方向，系统已直接使用"}
                                {selectedBatchTask
                                  ? `「${getOutreachTemplateSourceLabel(selectedBatchTask)}」`
                                  : "本次所选"}
                                模板生成草稿。
                                {batchReviewProfessorMissingResearchDirection
                                  ? "你可以编辑并审核通过。"
                                  : "导师资料现已补充，你可以使用 AI 改写或继续审核模板草稿。"}
                              </p>
                              {batchReviewTemplateReferencesResearchDirection ? (
                                <p className="mt-1 font-medium">
                                  模板中的研究方向变量为空，请重点检查相关语句。
                                </p>
                              ) : null}
                              {batchReviewProfessorMissingResearchDirection ? (
                                <button
                                  type="button"
                                  onClick={() => {
                                    if (activeBatchReviewItem) {
                                      void openProfessorEditDialog(
                                        activeBatchReviewItem,
                                      );
                                    }
                                  }}
                                  disabled={!activeBatchReviewItem}
                                  className="mt-2 inline-flex font-medium text-amber-900 underline underline-offset-4 disabled:cursor-not-allowed disabled:opacity-60"
                                >
                                  补充资料
                                </button>
                              ) : null}
                            </section>
                          ) : null}
                          <div className="mb-5 rounded-2xl border border-primary/10 bg-primary/5 px-4 py-3">
                            <div className="text-sm font-semibold text-stone-900">
                              {batchReviewThread.professor.name}
                            </div>
                            <div className="mt-1 text-xs leading-5 text-stone-600">
                              {[
                                batchReviewThread.professor.title,
                                batchReviewThread.professor.university,
                                batchReviewThread.professor.school,
                                batchReviewThread.professor.email,
                              ]
                                .filter(Boolean)
                                .join(" / ") || "导师信息待补充"}
                            </div>
                          </div>
                          <div className="space-y-4">
                            <SubjectTemplateInput
                              key={`batch-review-subject-${batchReviewThread.current_task.id}`}
                              label="邮件主题"
                              value={batchReviewSubject}
                              onChange={setBatchReviewSubject}
                              placeholder="给老师的邮件主题"
                            />
                            <EmailTemplateEditor
                              key={`batch-review-body-${batchReviewThread.current_task.id}`}
                              label="邮件正文"
                              html={batchReviewEditorHtml}
                              onChange={handleBatchReviewContentChange}
                            />
                          </div>
                        </div>

                        <aside className="space-y-4">
                          <section
                            aria-label="随信附件"
                            className="rounded-2xl border border-stone-100 bg-stone-50/70 px-4 py-3"
                          >
                            <div className="text-xs font-medium text-stone-500">
                              随信附件
                            </div>
                            <div className="mt-3 space-y-2">
                              {batchReviewThread.material_options.length > 0 ? (
                                batchReviewThread.material_options.map((material) => {
                                  const checked = batchReviewSelectedMaterialIds.includes(material.id);
                                  return (
                                    <label
                                      key={material.id}
                                      className="flex items-start gap-2 rounded-xl border border-stone-200 bg-white px-3 py-2 text-sm text-stone-700"
                                    >
                                      <input
                                        type="checkbox"
                                        checked={checked}
                                        onChange={() =>
                                          setBatchReviewSelectedMaterialIds((current) =>
                                            checked
                                              ? current.filter((id) => id !== material.id)
                                              : [...current, material.id],
                                          )
                                        }
                                      />
                                      <span className="min-w-0">
                                        <span className="block truncate font-medium">
                                          {material.display_name}
                                        </span>
                                        <span className="mt-0.5 block text-xs text-stone-500">
                                          {MATERIAL_TYPE_LABELS[material.material_type]} · {formatFileSize(material.size_bytes)}
                                        </span>
                                      </span>
                                    </label>
                                  );
                                })
                              ) : (
                                <p className="text-sm text-stone-500">
                                  暂无可发送材料。
                                </p>
                              )}
                            </div>
                            <AttachmentSizeSummary
                              selectedCount={batchReviewSelectedMaterialIds.length}
                              totalSizeBytes={batchReviewAttachmentTotalBytes}
                              className="mt-3"
                            />
                          </section>

                          <section
                            aria-label="审核操作"
                            className="rounded-2xl border border-stone-100 bg-white px-4 py-3"
                          >
                            <div className="text-xs leading-5 text-stone-500">
                              审核通过后会进入批量发送队列；定时批量任务会继续遵守日期、时间窗口和每日数量限制。
                            </div>
                            <div className="mt-4 flex flex-col gap-2">
                              <button
                                type="button"
                                onClick={() => void handleRegenerateBatchDraft()}
                                disabled={Boolean(activeBatchReviewAction) || !batchReviewThread}
                                className="ui-btn-secondary justify-center disabled:cursor-not-allowed disabled:opacity-60"
                              >
                                {batchReviewUsesTemplateFallback ? (
                                  <Sparkles className="h-4 w-4" />
                                ) : (
                                  <RotateCcw className="h-4 w-4" />
                                )}
                                {batchReviewUsesTemplateFallback
                                  ? "使用 AI 改写"
                                  : "重新生成"}
                              </button>
                              <button
                                type="button"
                                onClick={() => void handleApproveBatchDraft()}
                                disabled={Boolean(activeBatchReviewAction) || !batchReviewCanSubmit}
                                className="ui-btn-primary justify-center disabled:cursor-not-allowed disabled:opacity-60"
                              >
                                <CheckCircle2 className="h-4 w-4" />
                                审核通过
                              </button>
                              {canSendBatchReviewImmediately ? (
                                <button
                                  type="button"
                                  onClick={() => void handleSendBatchDraftNow()}
                                  disabled={Boolean(activeBatchReviewAction) || !batchReviewCanSubmit}
                                  className="ui-btn-secondary justify-center disabled:cursor-not-allowed disabled:opacity-60"
                                >
                                  <Mail className="h-4 w-4" />
                                  立即发送
                                </button>
                              ) : null}
                            </div>
                          </section>

                          <section
                            aria-label="老师详情"
                            className="rounded-2xl border border-stone-100 bg-stone-50/70 px-4 py-3"
                          >
                            <div className="text-xs font-medium text-stone-500">
                              老师详情
                            </div>
                            <dl className="mt-2 space-y-1.5">
                              {[
                                { label: "学校", value: batchReviewThread.professor.university },
                                { label: "学院", value: batchReviewThread.professor.school },
                                { label: "系所", value: batchReviewThread.professor.department },
                                {
                                  label: "研究方向",
                                  value: batchReviewThread.professor.research_direction,
                                },
                                { label: "主页链接", value: batchReviewThread.professor.profile_url },
                              ].map(({ label, value }) => {
                                const normalizedValue = value?.trim();
                                if (!normalizedValue) {
                                  return null;
                                }

                                return (
                                  <div
                                    key={label}
                                    className="grid grid-cols-[3.5rem_minmax(0,1fr)] items-start gap-2 text-xs leading-5"
                                  >
                                    <dt className="text-stone-500">{label}</dt>
                                    <dd className="min-w-0 break-words text-stone-700">
                                      {label === "主页链接"
                                        ? renderCandidateExternalUrl(normalizedValue)
                                        : normalizedValue}
                                    </dd>
                                  </div>
                                );
                              })}
                            </dl>
                          </section>

                          <section
                            aria-label="匹配摘要"
                            className="rounded-2xl border border-stone-100 bg-stone-50/70 px-4 py-3"
                          >
                            <div className="text-xs font-medium text-stone-500">
                              匹配摘要
                            </div>
                            <div className="mt-2 text-sm font-semibold text-stone-900">
                              {batchReviewThread.current_task.match_score !== null
                                ? `匹配分 ${batchReviewThread.current_task.match_score}`
                                : "暂无匹配分"}
                            </div>
                            {batchReviewThread.current_task.match_reason ? (
                              <p className="mt-2 text-xs leading-5 text-stone-600">
                                {batchReviewThread.current_task.match_reason}
                              </p>
                            ) : null}
                          </section>
                        </aside>
                      </div>
                    ) : (
                      <div className="flex min-h-[520px] items-center justify-center text-sm text-stone-500">
                        请选择一封待审核草稿。
                      </div>
                    )}
                  </section>
                </div>
              ) : (
              <>
              <div className="grid gap-3 sm:grid-cols-3">
                <div className="rounded-2xl border border-stone-100 bg-white px-4 py-3">
                  <div className="text-xs font-medium text-stone-500">
                    当前状态
                  </div>
                  <div className="mt-2 text-sm font-semibold text-stone-900">
                    {BATCH_TASK_STATUS_LABELS[selectedBatchTask.status]}
                  </div>
                </div>
                <div className="rounded-2xl border border-stone-100 bg-white px-4 py-3">
                  <div className="text-xs font-medium text-stone-500">
                    目标人数
                  </div>
                  <div className="mt-2 text-sm font-semibold text-stone-900">
                    {selectedBatchTask.target_count}
                  </div>
                </div>
                <div className="rounded-2xl border border-stone-100 bg-white px-4 py-3">
                  <div className="text-xs font-medium text-stone-500">
                    已完成
                  </div>
                  <div className="mt-2 text-sm font-semibold text-stone-900">
                    {selectedBatchTask.completed_count}
                  </div>
                </div>
              </div>
              {selectedBatchTask.status === "expired" ? (
                <p className="mt-4 rounded-2xl border border-red-100 bg-red-50 px-4 py-3 text-sm leading-6 text-red-800">
                  发送窗口已过期，剩余邮件已取消。可重新创建任务。
                </p>
              ) : null}

              <section className="mt-6">
                <div className="flex flex-wrap items-center justify-between gap-3">
                  <h3 className="text-sm font-semibold text-stone-900">
                    导师进度
                  </h3>
                  {batchTaskDetailsLoading ? (
                    <span className="inline-flex items-center gap-2 text-xs text-stone-500">
                      <Loader2 className="h-3.5 w-3.5 animate-spin" />
                      正在刷新
                    </span>
                  ) : null}
                </div>

                <div className="mt-3 grid gap-3 sm:grid-cols-2 lg:grid-cols-5">
                  <div className="rounded-2xl border border-emerald-100 bg-emerald-50 px-4 py-3">
                    <div className="text-xs font-medium text-emerald-700">
                      已发送/已回复
                    </div>
                    <div className="mt-2 text-xl font-semibold text-emerald-900">
                      {sentBatchTaskItems.length}
                    </div>
                  </div>
                  <div className="rounded-2xl border border-primary/15 bg-primary/5 px-4 py-3">
                    <div className="text-xs font-medium text-primary">
                      等待发送
                    </div>
                    <div className="mt-2 text-xl font-semibold text-stone-900">
                      {selectedBatchWaitingSendCount}
                    </div>
                  </div>
                  <div className="rounded-2xl border border-amber-100 bg-amber-50 px-4 py-3">
                    <div className="text-xs font-medium text-amber-700">
                      待审核/未处理
                    </div>
                    <div className="mt-2 text-xl font-semibold text-amber-900">
                      {selectedBatchNeedsManualItems.length}
                    </div>
                  </div>
                  <div className="rounded-2xl border border-red-100 bg-red-50 px-4 py-3">
                    <div className="text-xs font-medium text-red-700">
                      发送失败
                    </div>
                    <div className="mt-2 text-xl font-semibold text-red-900">
                      {failedBatchTaskItems.length}
                    </div>
                  </div>
                  <div className="rounded-2xl border border-stone-200 bg-stone-50 px-4 py-3">
                    <div className="text-xs font-medium text-stone-600">
                      已取消发送
                    </div>
                    <div className="mt-2 text-xl font-semibold text-stone-900">
                      {selectedBatchTask.canceled_send_count}
                    </div>
                  </div>
                </div>
              </section>

              <section
                ref={batchSentItemsStartRef}
                tabIndex={-1}
                aria-label="已发送导师列表"
                className="mt-6 scroll-mt-24 focus:outline-none"
              >
                <h3 className="text-sm font-semibold text-stone-900">
                  已发送给
                </h3>
                <div className="mt-3 space-y-2">
                  {sentBatchTaskItems.length > 0 ? (
                    visibleSentBatchTaskItems.map((item) => (
                      <div
                        key={item.id}
                        className="rounded-2xl border border-stone-100 px-4 py-3"
                      >
                        <div className="flex flex-wrap items-start justify-between gap-3">
                          <div>
                            <p className="text-sm font-medium text-stone-900">
                              {item.professor_name}
                            </p>
                            <p className="mt-1 text-xs text-stone-500">
                              {[
                                item.professor_title,
                                item.professor_school,
                                item.professor_email,
                              ]
                                .filter(Boolean)
                                .join(" / ") || "暂无补充信息"}
                            </p>
                          </div>
                          <span
                            className={`rounded-full px-2.5 py-1 text-xs ${BATCH_ITEM_STATUS_TONES[item.status]}`}
                          >
                            {PROFESSOR_STATUS_LABELS[item.status]}
                          </span>
                        </div>
                        <div className="mt-2 flex flex-wrap items-center gap-3 text-xs text-stone-500">
                          <span>
                            发送时间 {formatDisplayTime(item.sent_at)}
                          </span>
                          <Link
                            to={`/workspace/${item.professor_id}`}
                            className="font-medium text-primary"
                          >
                            查看通信
                          </Link>
                        </div>
                      </div>
                    ))
                  ) : (
                    <p className="rounded-2xl border border-dashed border-stone-200 px-4 py-3 text-sm text-stone-500">
                      暂无已发送导师。
                    </p>
                  )}
                </div>
                <Pagination
                  page={safeBatchSentItemPage}
                  pageSize={batchSentItemPageSize}
                  totalCount={sentBatchTaskItems.length}
                  onChange={handleBatchSentItemPaginationChange}
                  ariaLabel="已发送导师分页"
                  pageSizeAriaLabel="已发送导师每页数量"
                  variant="compact"
                  pageSizeOptions={DETAIL_PAGE_SIZE_OPTIONS}
                  unitLabel="位"
                  itemLabel="位导师"
                  summary={`显示 ${(safeBatchSentItemPage - 1) * batchSentItemPageSize + 1}-${Math.min(sentBatchTaskItems.length, safeBatchSentItemPage * batchSentItemPageSize)} / ${sentBatchTaskItems.length} 个任务`}
                  focusTargetRef={batchSentItemsStartRef}
                  menuPlacement="popover"
                  className="mt-3 border-t border-stone-100 pt-3"
                />
              </section>

              <section
                ref={batchPendingItemsStartRef}
                tabIndex={-1}
                aria-label="未发送导师列表"
                className="mt-6 scroll-mt-24 focus:outline-none"
              >
                <h3 className="text-sm font-semibold text-stone-900">
                  还未发送给
                </h3>
                {selectedBatchTask.schedule_type === "scheduled" && selectedBatchWaitingSendCount > 0 ? (
                  <p className="mt-2 rounded-2xl border border-primary/15 bg-primary/5 px-4 py-3 text-sm leading-6 text-stone-700">
                    已通过的模板邮件会按批量任务的日期、时间窗口和每日数量自动发送，不需要逐封手动设定发送时间。
                  </p>
                ) : null}
                {reviewRequiredBatchTaskItems.length > 0 ? (
                  <div className="mt-2 flex flex-col gap-3 rounded-2xl border border-amber-100 bg-amber-50 px-4 py-3 text-sm leading-6 text-amber-800 sm:flex-row sm:items-center sm:justify-between">
                    <p>
                      当前有 {reviewRequiredBatchTaskItems.length} 封草稿待审核。
                      {templateFallbackReviewCount > 0
                        ? `其中 ${templateFallbackReviewCount} 封因导师缺少研究方向，使用模板生成且未进行 AI 改写。`
                        : "这些草稿已完成 AI 改写。"}
                      你可以逐封检查，也可以直接通过当前全部待审核草稿。
                    </p>
                    <button
                      type="button"
                      onClick={() => void handleApproveAllBatchDrafts()}
                      disabled={batchBulkApprovalLoading || batchTaskDetailsLoading}
                      className="ui-btn-secondary shrink-0 justify-center border-amber-200 bg-white text-amber-800 hover:border-amber-300 hover:bg-amber-100 disabled:cursor-not-allowed disabled:opacity-60"
                    >
                      {batchBulkApprovalLoading ? (
                        <Loader2 className="h-4 w-4 animate-spin" />
                      ) : (
                        <CheckCircle2 className="h-4 w-4" />
                      )}
                      {batchBulkApprovalLoading
                        ? `正在通过 ${reviewRequiredBatchTaskItems.length} 封...`
                        : `全部通过审核（${reviewRequiredBatchTaskItems.length} 封）`}
                    </button>
                  </div>
                ) : null}
                <div className="mt-3 space-y-2">
                  {pendingBatchTaskItems.length > 0 ? (
                    visiblePendingBatchTaskItems.map((item) => {
                      const cancellationText = getBatchTaskItemCancellationText(item);
                      const sendCanceled = item.batch_send_canceled_at !== null;
                      const missingResearchDirection =
                        !sendCanceled &&
                        isBatchTaskItemMissingResearchDirection(item);
                      const restoreWindowExpired =
                        sendCanceled &&
                        !isBatchItemScheduledInFuture(
                          item,
                          batchSendActionNowMs,
                        );
                      return (
                        <div
                          key={item.id}
                          data-testid={`batch-task-item-${item.id}`}
                          className={
                            sendCanceled
                              ? "rounded-2xl border border-red-200 bg-red-50/60 px-4 py-3"
                              : "rounded-2xl border border-stone-100 px-4 py-3"
                          }
                        >
                          <div className="flex flex-wrap items-start justify-between gap-3">
                            <div>
                              <div className="flex flex-wrap items-center gap-1.5">
                                <p className="text-sm font-medium text-stone-900">
                                  {item.professor_name}
                                </p>
                                {missingResearchDirection ? (
                                  <span className="inline-flex items-center rounded-full bg-amber-100 px-2 py-0.5 text-[11px] font-medium text-amber-800">
                                    缺少研究方向
                                  </span>
                                ) : null}
                                {!sendCanceled &&
                                item.draft_generation_source === "template_fallback" ? (
                                  <span className="inline-flex items-center rounded-full bg-orange-100 px-2 py-0.5 text-[11px] font-medium text-orange-800">
                                    未进行 AI 改写
                                  </span>
                                ) : null}
                              </div>
                              <p className="mt-1 text-xs text-stone-500">
                                {[
                                  item.professor_title,
                                  item.professor_school,
                                  item.professor_email,
                                ]
                                  .filter(Boolean)
                                  .join(" / ") || "暂无补充信息"}
                              </p>
                            </div>
                            <div className="flex flex-wrap items-center justify-end gap-2">
                              {sendCanceled ? (
                                <span className="inline-flex items-center gap-1.5 rounded-full bg-red-100 px-2.5 py-1 text-xs font-medium text-red-800">
                                  <Ban className="h-3.5 w-3.5" />
                                  已取消发送
                                </span>
                              ) : (
                                <span
                                  className={`rounded-full px-2.5 py-1 text-xs ${BATCH_ITEM_STATUS_TONES[item.status]}`}
                                >
                                  {PROFESSOR_STATUS_LABELS[item.status]}
                                </span>
                              )}
                              {renderBatchItemSendButton(item)}
                            </div>
                          </div>
                          <div className="mt-2 flex flex-wrap items-center gap-3 text-xs text-stone-500">
                            {item.scheduled_at ? (
                              <span>
                                {sendCanceled ? "原计划发送" : "计划发送"}{" "}
                                {formatDisplayTime(item.scheduled_at)}
                              </span>
                            ) : null}
                            {sendCanceled ? (
                              <span className="font-medium text-red-700">
                                {restoreWindowExpired
                                  ? "原定发送时间已过，无法恢复"
                                  : "该导师不会收到本次邮件"}
                              </span>
                            ) : cancellationText ? (
                              <span className="font-medium text-red-700">
                                {cancellationText}
                              </span>
                            ) : renderBatchTaskItemAction(item)}
                            {item.match_score !== null ? (
                              <span>匹配分 {item.match_score}</span>
                            ) : null}
                          </div>
                        </div>
                      );
                    })
                  ) : (
                    <p className="rounded-2xl border border-dashed border-stone-200 px-4 py-3 text-sm text-stone-500">
                      暂无未发送导师。
                    </p>
                  )}
                </div>
                <Pagination
                  page={safeBatchPendingItemPage}
                  pageSize={batchPendingItemPageSize}
                  totalCount={pendingBatchTaskItems.length}
                  onChange={handleBatchPendingItemPaginationChange}
                  ariaLabel="未发送导师分页"
                  pageSizeAriaLabel="未发送导师每页数量"
                  variant="compact"
                  pageSizeOptions={DETAIL_PAGE_SIZE_OPTIONS}
                  unitLabel="位"
                  itemLabel="位导师"
                  summary={`显示 ${(safeBatchPendingItemPage - 1) * batchPendingItemPageSize + 1}-${Math.min(pendingBatchTaskItems.length, safeBatchPendingItemPage * batchPendingItemPageSize)} / ${pendingBatchTaskItems.length} 个任务`}
                  focusTargetRef={batchPendingItemsStartRef}
                  menuPlacement="popover"
                  className="mt-3 border-t border-stone-100 pt-3"
                />
              </section>

              {generatingDraftBatchTaskItems.length > 0 ? (
                <section className="mt-6">
                  <h3 className="text-sm font-semibold text-stone-900">
                    正在生成草稿
                  </h3>
                  <div className="mt-3 space-y-2">
                    {generatingDraftBatchTaskItems.map((item) => (
                      <div
                        key={item.id}
                        className="rounded-2xl border border-sky-100 bg-sky-50/50 px-4 py-3"
                      >
                        <div className="flex flex-wrap items-start justify-between gap-3">
                          <div>
                            <p className="text-sm font-medium text-stone-900">
                              {item.professor_name}
                            </p>
                            <p className="mt-1 text-xs text-stone-500">
                              {[
                                item.professor_title,
                                item.professor_school,
                                item.professor_email,
                              ]
                                .filter(Boolean)
                                .join(" / ") || "暂无补充信息"}
                            </p>
                          </div>
                          <div className="flex flex-wrap items-center justify-end gap-2">
                            <span className="rounded-full bg-sky-100 px-2.5 py-1 text-xs text-sky-700">
                              {PROFESSOR_STATUS_LABELS[item.status]}
                            </span>
                            {renderBatchItemSendButton(item)}
                          </div>
                        </div>
                      </div>
                    ))}
                  </div>
                </section>
              ) : null}

              {draftFailedBatchTaskItems.length > 0 ? (
                <section className="mt-6">
                  <h3 className="text-sm font-semibold text-stone-900">
                    草稿生成失败
                  </h3>
                  <div className="mt-3 space-y-2">
                    {draftFailedBatchTaskItems.map((item) => (
                      <div
                        key={item.id}
                        className="rounded-2xl border border-red-100 bg-red-50/60 px-4 py-3"
                      >
                        <div className="flex flex-wrap items-start justify-between gap-3">
                          <div>
                            <p className="text-sm font-medium text-stone-900">
                              {item.professor_name}
                            </p>
                            <p className="mt-1 text-xs text-red-700">
                              {item.last_error || "暂无失败原因"}
                            </p>
                          </div>
                          <div className="flex flex-wrap items-center justify-end gap-3 text-xs">
                            {renderBatchTaskItemAction(item)}
                            {renderBatchItemSendButton(item)}
                          </div>
                        </div>
                      </div>
                    ))}
                  </div>
                </section>
              ) : null}

              {failedBatchTaskItems.length > 0 ? (
                <section className="mt-6">
                  <h3 className="text-sm font-semibold text-stone-900">
                    发送失败
                  </h3>
                  <div className="mt-3 space-y-2">
                    {failedBatchTaskItems.map((item) => (
                      <div
                        key={item.id}
                        className="rounded-2xl border border-red-100 bg-red-50/60 px-4 py-3"
                      >
                        <div className="min-w-0">
                          <p className="text-sm font-medium text-stone-900">
                            {item.professor_name}
                          </p>
                          <EmailDeliveryFailureDetails
                            possibleCause={item.possible_cause}
                            rawError={item.last_error}
                          />
                        </div>
                      </div>
                    ))}
                  </div>
                </section>
              ) : null}

              <section className="mt-6">
                <h3 className="text-sm font-semibold text-stone-900">
                  基础信息
                </h3>
                <dl className="mt-3 divide-y divide-stone-100 rounded-2xl border border-stone-100 text-sm">
                  <div className="grid gap-1 px-4 py-3 sm:grid-cols-[120px_1fr]">
                    <dt className="text-stone-500">发信模板</dt>
                    <dd className="text-stone-800">
                      <div className="font-medium text-stone-900">
                        {getOutreachTemplateSourceLabel(selectedBatchTask)}
                      </div>
                      {selectedBatchTask.outreach_template_snapshot_version !== null ? (
                        <div className="mt-1 text-xs leading-5 text-stone-500">
                          内容以创建任务时编辑器中的版本为准，不随模板库后续修改。
                        </div>
                      ) : null}
                    </dd>
                  </div>
                  <div className="grid gap-1 px-4 py-3 sm:grid-cols-[120px_1fr]">
                    <dt className="text-stone-500">写信方式</dt>
                    <dd className="text-stone-800">
                      {getOutreachGenerationModeLabel(
                        selectedBatchTask.outreach_generation_mode,
                      )}
                    </dd>
                  </div>
                  <div className="grid gap-1 px-4 py-3 sm:grid-cols-[120px_1fr]">
                    <dt className="text-stone-500">邮件主题</dt>
                    <dd className="text-stone-800">
                      {selectedBatchTask.email_subject || "未设置"}
                    </dd>
                  </div>
                  <div className="grid gap-1 px-4 py-3 sm:grid-cols-[120px_1fr]">
                    <dt className="text-stone-500">创建时间</dt>
                    <dd className="text-stone-800">
                      {formatDisplayTime(selectedBatchTask.created_at)}
                    </dd>
                  </div>
                  <div className="grid gap-1 px-4 py-3 sm:grid-cols-[120px_1fr]">
                    <dt className="text-stone-500">更新时间</dt>
                    <dd className="text-stone-800">
                      {formatDisplayTime(selectedBatchTask.updated_at)}
                    </dd>
                  </div>
                </dl>
              </section>
              </>
              )}
            </div>
          </section>
        </div>
      ) : null}
      {selectedMatchJob ? (
        <div
          className="fixed inset-0 z-50 flex items-stretch justify-end bg-stone-950/30 p-0 sm:p-6"
          onClick={matchJobDetailsLayer.onBackdropClick}
          onMouseDown={matchJobDetailsLayer.onBackdropMouseDown}
        >
          <section
            role="dialog"
            aria-label="匹配分析任务详情"
            className="flex h-full w-full flex-col overflow-hidden bg-white shadow-xl sm:max-w-4xl sm:rounded-3xl"
            onClick={matchJobDetailsLayer.onContentClick}
            onMouseDown={matchJobDetailsLayer.onContentMouseDown}
          >
            <div className="flex items-start justify-between gap-4 border-b border-stone-200 bg-[#fcfbf8] px-6 py-5">
              <div>
                <div className="flex items-center gap-2 text-xs font-medium text-stone-500">
                  <Sparkles className="h-4 w-4 text-primary" />
                  匹配分析任务
                </div>
                <h2 className="mt-2 text-xl font-semibold text-stone-900">
                  {selectedMatchJob.name}
                </h2>
                <p className="mt-2 text-sm text-stone-500">
                  创建于 {formatDisplayTime(selectedMatchJob.created_at)}
                </p>
              </div>
              <button
                type="button"
                onClick={closeMatchJobDetails}
                className="ui-btn-secondary shrink-0"
                aria-label="关闭"
              >
                <X className="h-4 w-4" />
                关闭
              </button>
            </div>

            <div
              data-testid="match-job-detail-scroll"
              className="flex-1 overflow-y-auto overscroll-contain px-6 py-5"
            >
              <div className="grid gap-3 sm:grid-cols-3">
                <div className="rounded-2xl border border-stone-100 bg-white px-4 py-3">
                  <div className="text-xs font-medium text-stone-500">成功</div>
                  <div className="mt-2 text-sm font-semibold text-stone-900">
                    {selectedMatchJob.succeeded_count}
                  </div>
                </div>
                <div className="rounded-2xl border border-stone-100 bg-white px-4 py-3">
                  <div className="text-xs font-medium text-stone-500">失败</div>
                  <div className="mt-2 text-sm font-semibold text-stone-900">
                    {selectedMatchJob.failed_count}
                  </div>
                </div>
                <div className="rounded-2xl border border-stone-100 bg-white px-4 py-3">
                  <div className="text-xs font-medium text-stone-500">跳过</div>
                  <div className="mt-2 text-sm font-semibold text-stone-900">
                    {selectedMatchJob.skipped_count}
                  </div>
                </div>
              </div>

              <TokenUsageBreakdown
                inputTokens={selectedMatchJob.total_prompt_tokens}
                outputTokens={selectedMatchJob.total_completion_tokens}
                cachedTokens={selectedMatchJob.total_cached_tokens}
                totalTokens={selectedMatchJob.total_tokens}
                ariaLabel="匹配分析任务 Token 使用汇总"
                variant="metrics"
                className="mt-3"
              />

              <section
                ref={matchJobItemsStartRef}
                tabIndex={-1}
                aria-label="匹配分析导师明细"
                className="mt-6 scroll-mt-24 focus:outline-none"
              >
                <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
                  <h3 className="text-sm font-semibold text-stone-900">
                    导师明细
                  </h3>
                  <div className="flex flex-wrap items-center gap-2">
                    {matchJobDetailsLoading ? (
                      <span className="inline-flex items-center gap-2 text-xs text-stone-500">
                        <Loader2 className="h-3.5 w-3.5 animate-spin" />
                        正在刷新
                      </span>
                    ) : null}
                    <span className="text-xs text-stone-500">状态</span>
                    <NativeSelectField
                      ariaLabel="筛选匹配分析导师状态"
                      value={matchJobItemStatusFilter}
                      onChange={(event) => {
                        setMatchJobItemStatusFilter(
                          event.target.value as MatchAnalysisJobItemStatus | "all",
                        );
                      }}
                      wrapperClassName="w-32"
                      shellClassName="!min-h-0 h-9 rounded-2xl px-3 py-0 shadow-none"
                    >
                      <option value="all">全部状态</option>
                      {Object.entries(MATCH_ANALYSIS_ITEM_STATUS_LABELS).map(
                        ([status, label]) => (
                          <option key={status} value={status}>
                            {label}
                          </option>
                        ),
                      )}
                    </NativeSelectField>
                    <span className="text-xs tabular-nums text-stone-500">
                      {filteredMatchJobItems.length} / {selectedMatchJobItems.length} 位
                    </span>
                  </div>
                </div>

                <div className="mt-3 overflow-x-auto rounded-2xl border border-stone-200">
                  <table className="w-max min-w-max table-auto divide-y divide-stone-200 text-sm">
                    <thead className="bg-stone-50 text-center text-xs font-medium text-stone-500">
                      <tr>
                        <th className="px-4 py-3 align-middle">导师</th>
                        <th className="px-4 py-3 align-middle">状态</th>
                        <th className="px-4 py-3 align-middle">匹配分</th>
                        <th className="px-4 py-3 align-middle">说明</th>
                        <th className="px-3 py-3 align-middle">Token 明细</th>
                        <th className="px-4 py-3 align-middle">更新时间</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-stone-100 bg-white text-stone-700">
                      {filteredMatchJobItems.length > 0 ? (
                        visibleMatchJobItems.map((item) => {
                          const professorDetails = [
                            item.professor_title,
                            item.professor_university,
                            item.professor_school,
                          ]
                            .filter(Boolean)
                            .join(" / ");

                          return (
                            <tr key={item.id}>
                              <td className="px-4 py-3 align-middle">
                                <div className="max-w-56 break-words font-medium text-stone-900">
                                  {item.professor_name}
                                </div>
                                {professorDetails ? (
                                  <div className="mt-1 max-w-56 break-words text-xs text-stone-500">
                                    {professorDetails}
                                  </div>
                                ) : null}
                              </td>
                              <td className="px-4 py-3 text-center align-middle">
                                <span
                                  className={`whitespace-nowrap rounded-full border px-2.5 py-1 text-xs font-medium ${MATCH_ANALYSIS_ITEM_STATUS_TONES[item.status]}`}
                                >
                                  {MATCH_ANALYSIS_ITEM_STATUS_LABELS[item.status]}
                                </span>
                              </td>
                              <td className="px-4 py-3 text-center align-middle tabular-nums">
                                {item.match_score ?? "未生成"}
                              </td>
                              <td className="px-4 py-3 text-center align-middle">
                                <div className="max-w-[22rem] break-words">
                                  {item.error_message || item.skip_reason || "已完成"}
                                </div>
                              </td>
                              <td className="px-3 py-3 text-center align-middle">
                                <TokenUsageBreakdown
                                  inputTokens={item.prompt_tokens}
                                  outputTokens={item.completion_tokens}
                                  cachedTokens={item.cached_tokens}
                                  totalTokens={item.total_tokens}
                                  ariaLabel={`${item.professor_name} Token 使用明细`}
                                  compactLayout="tight"
                                  className="text-left"
                                />
                              </td>
                              <td className="whitespace-nowrap px-4 py-3 text-center align-middle tabular-nums">
                                {formatDisplayTime(item.updated_at, { withSeconds: true })}
                              </td>
                            </tr>
                          );
                        })
                      ) : (
                        <tr>
                          <td
                            colSpan={6}
                            className="px-4 py-6 text-center text-sm text-stone-500"
                          >
                            {selectedMatchJobItems.length > 0
                              ? "当前状态下暂无导师。"
                              : "暂无任务明细。"}
                          </td>
                        </tr>
                      )}
                    </tbody>
                  </table>
                </div>
                <Pagination
                  page={matchJobItemPage}
                  pageSize={matchJobItemPageSize}
                  totalCount={filteredMatchJobItems.length}
                  onChange={setMatchJobItemPagination}
                  ariaLabel="匹配分析导师明细分页"
                  pageSizeAriaLabel="匹配分析导师明细每页数量"
                  variant="compact"
                  pageSizeOptions={DETAIL_PAGE_SIZE_OPTIONS}
                  unitLabel="位"
                  itemLabel="位导师"
                  focusTargetRef={matchJobItemsStartRef}
                  menuPlacement="popover"
                  className="mt-3 border-t border-stone-100 pt-3"
                />
              </section>
            </div>
          </section>
        </div>
      ) : null}
      {selectedInformationEnrichmentJob ? (
        <div
          className="fixed inset-0 z-50 flex items-stretch justify-end bg-stone-950/30 p-0 sm:p-6"
          onClick={informationEnrichmentDetailsLayer.onBackdropClick}
          onMouseDown={informationEnrichmentDetailsLayer.onBackdropMouseDown}
        >
          <section
            role="dialog"
            aria-label="信息补全任务详情"
            className="flex h-full w-full flex-col overflow-hidden bg-white shadow-xl sm:max-w-5xl sm:rounded-3xl"
            onClick={informationEnrichmentDetailsLayer.onContentClick}
            onMouseDown={informationEnrichmentDetailsLayer.onContentMouseDown}
          >
            <div className="flex items-start justify-between gap-4 border-b border-stone-200 bg-[#fcfbf8] px-4 py-5 sm:px-6">
              <div className="min-w-0">
                <div className="flex items-center gap-2 text-xs font-medium text-stone-500">
                  <Bot className="h-4 w-4 text-primary" />
                  信息补全任务
                </div>
                <div className="mt-2 flex flex-wrap items-center gap-2">
                  <h2 className="min-w-0 break-words text-xl font-semibold text-stone-900">
                    {selectedInformationEnrichmentJob.name}
                  </h2>
                  <span
                    className={`shrink-0 rounded-full border px-2.5 py-1 text-xs font-medium ${INFORMATION_ENRICHMENT_JOB_STATUS_TONES[selectedInformationEnrichmentJob.status]}`}
                  >
                    {
                      PROFESSOR_INFORMATION_ENRICHMENT_STATUS_LABELS[
                        selectedInformationEnrichmentJob.status
                      ]
                    }
                  </span>
                </div>
                <p className="mt-2 text-sm text-stone-500">
                  创建于 {formatDisplayTime(selectedInformationEnrichmentJob.created_at)}
                </p>
              </div>
              <button
                type="button"
                onClick={closeInformationEnrichmentDetails}
                className="ui-btn-secondary shrink-0"
                aria-label="关闭信息补全任务详情"
              >
                <X className="h-4 w-4" />
                关闭
              </button>
            </div>

            <div
              data-testid="information-enrichment-detail-scroll"
              className="flex-1 overflow-y-auto overscroll-contain px-4 py-5 sm:px-6"
            >
              <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-5">
                <div className="rounded-2xl border border-stone-100 bg-white px-4 py-3">
                  <div className="text-xs font-medium text-stone-500">成功</div>
                  <div className="mt-2 text-sm font-semibold text-stone-900">
                    {selectedInformationEnrichmentJob.succeeded_count}
                  </div>
                </div>
                <div className="rounded-2xl border border-stone-100 bg-white px-4 py-3">
                  <div className="text-xs font-medium text-stone-500">失败</div>
                  <div className="mt-2 text-sm font-semibold text-stone-900">
                    {selectedInformationEnrichmentJob.failed_count}
                  </div>
                </div>
                <div className="rounded-2xl border border-stone-100 bg-white px-4 py-3">
                  <div className="text-xs font-medium text-stone-500">跳过</div>
                  <div className="mt-2 text-sm font-semibold text-stone-900">
                    {selectedInformationEnrichmentJob.skipped_count}
                  </div>
                </div>
                <div className="rounded-2xl border border-stone-100 bg-white px-4 py-3">
                  <div className="text-xs font-medium text-stone-500">取消</div>
                  <div className="mt-2 text-sm font-semibold text-stone-900">
                    {selectedInformationEnrichmentJob.canceled_count}
                  </div>
                </div>
                <div className="rounded-2xl border border-stone-100 bg-white px-4 py-3">
                  <div className="text-xs font-medium text-stone-500">耗时</div>
                  <div className="mt-2 text-sm font-semibold text-stone-900">
                    {formatDuration(selectedInformationEnrichmentJob.duration_seconds)}
                  </div>
                </div>
              </div>

              <TokenUsageBreakdown
                inputTokens={selectedInformationEnrichmentJob.input_tokens}
                outputTokens={selectedInformationEnrichmentJob.output_tokens}
                cachedTokens={selectedInformationEnrichmentJob.cached_tokens}
                totalTokens={selectedInformationEnrichmentJob.total_tokens}
                ariaLabel="信息补全任务 Token 使用汇总"
                variant="metrics"
                className="mt-3"
              />

              {selectedInformationEnrichmentJob.last_error ? (
                <div className="mt-4 rounded-2xl border border-red-200 bg-red-50 px-4 py-3">
                  <div className="text-xs font-medium text-red-700">最近错误</div>
                  <div className="mt-2 whitespace-pre-wrap break-words text-sm leading-6 text-red-900">
                    {selectedInformationEnrichmentJob.last_error}
                  </div>
                </div>
              ) : null}

              <section
                ref={informationEnrichmentItemsStartRef}
                tabIndex={-1}
                aria-label="信息补全导师明细"
                className="mt-6 scroll-mt-24 focus:outline-none"
              >
                <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
                  <h3 className="text-sm font-semibold text-stone-900">导师明细</h3>
                  <div className="flex flex-wrap items-center gap-2">
                    {informationEnrichmentDetailsLoading ? (
                      <span className="inline-flex items-center gap-2 text-xs text-stone-500">
                        <Loader2 className="h-3.5 w-3.5 animate-spin" />
                        正在刷新
                      </span>
                    ) : null}
                    <span className="text-xs text-stone-500">状态</span>
                    <NativeSelectField
                      ariaLabel="筛选信息补全导师状态"
                      value={informationEnrichmentItemStatusFilter}
                      onChange={(event) => {
                        setInformationEnrichmentItemStatusFilter(
                          event.target.value as
                            | ProfessorInformationEnrichmentItemStatus
                            | "all",
                        );
                      }}
                      wrapperClassName="w-32"
                      shellClassName="!min-h-0 h-9 rounded-2xl px-3 py-0 shadow-none"
                    >
                      <option value="all">全部状态</option>
                      {Object.entries(INFORMATION_ENRICHMENT_ITEM_STATUS_LABELS).map(
                        ([status, label]) => (
                          <option key={status} value={status}>
                            {label}
                          </option>
                        ),
                      )}
                    </NativeSelectField>
                    <span className="text-xs tabular-nums text-stone-500">
                      {filteredInformationEnrichmentItems.length} /{" "}
                      {selectedInformationEnrichmentItems.length} 位
                    </span>
                  </div>
                </div>

                <div className="mt-3 overflow-x-auto rounded-2xl border border-stone-200">
                  <table className="w-max min-w-max table-auto divide-y divide-stone-200 text-sm">
                    <thead className="bg-stone-50 text-center text-xs font-medium text-stone-500">
                      <tr>
                        <th className="px-4 py-3 align-middle">导师</th>
                        <th className="px-4 py-3 align-middle">状态</th>
                        <th className="px-4 py-3 align-middle">补全字段</th>
                        <th className="px-4 py-3 align-middle">说明</th>
                        <th className="px-3 py-3 align-middle">
                          Token 明细 / 尝试
                        </th>
                        <th className="px-4 py-3 align-middle">主页 / 完成时间</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-stone-100 bg-white text-stone-700">
                      {filteredInformationEnrichmentItems.length > 0 ? (
                        visibleInformationEnrichmentItems.map((item) => {
                          const itemMessage =
                            item.error_message ||
                            item.skip_reason ||
                            (item.status === "succeeded"
                              ? item.enriched_fields.length > 0
                                ? "补全完成"
                                : "未发现可写入的新信息"
                              : "等待处理");

                          return (
                            <tr key={item.id}>
                              <td className="px-4 py-3 align-middle">
                                <div className="max-w-64 break-words font-medium text-stone-900">
                                  {item.professor_name}
                                </div>
                                <div className="mt-1 max-w-64 break-words text-xs leading-5 text-stone-500">
                                  {item.professor_email || "暂无邮箱"}
                                </div>
                                <div className="max-w-64 break-words text-xs leading-5 text-stone-500">
                                  {[
                                    item.professor_title,
                                    item.professor_school,
                                    item.professor_department,
                                  ]
                                    .filter(Boolean)
                                    .join(" / ") || "暂无补充信息"}
                                </div>
                              </td>
                              <td className="px-4 py-3 text-center align-middle">
                                <span
                                  className={`whitespace-nowrap rounded-full border px-2.5 py-1 text-xs font-medium ${INFORMATION_ENRICHMENT_ITEM_STATUS_TONES[item.status]}`}
                                >
                                  {INFORMATION_ENRICHMENT_ITEM_STATUS_LABELS[item.status]}
                                </span>
                              </td>
                              <td className="px-4 py-3 text-center align-middle">
                                {item.enriched_fields.length > 0 ? (
                                  <div className="mx-auto flex max-w-48 flex-wrap justify-center gap-1.5">
                                    {item.enriched_fields.map((field) => (
                                      <span
                                        key={field}
                                        className="rounded-full bg-emerald-50 px-2 py-1 text-xs text-emerald-700"
                                      >
                                        {INFORMATION_ENRICHMENT_FIELD_LABELS[field] ?? field}
                                      </span>
                                    ))}
                                  </div>
                                ) : (
                                  <span className="text-stone-400">--</span>
                                )}
                              </td>
                              <td className="px-4 py-3 text-center align-middle">
                                <div
                                  className={`mx-auto max-w-[22rem] whitespace-pre-wrap break-words leading-6 ${
                                    item.error_message ? "text-red-700" : "text-stone-700"
                                  }`}
                                >
                                  {itemMessage}
                                </div>
                              </td>
                              <td className="px-3 py-3 text-center align-middle">
                                <TokenUsageBreakdown
                                  inputTokens={item.input_tokens}
                                  outputTokens={item.output_tokens}
                                  cachedTokens={item.cached_tokens}
                                  totalTokens={item.total_tokens}
                                  ariaLabel={`${item.professor_name} Token 使用明细`}
                                  compactLayout="tight"
                                  className="text-left"
                                />
                                <div className="mt-1 text-xs text-stone-500">
                                  尝试 {item.attempt_count} 次
                                </div>
                              </td>
                              <td className="px-4 py-3 text-center align-middle">
                                <div className="mx-auto max-w-56 truncate">
                                  {renderCandidateExternalUrl(item.profile_url)}
                                </div>
                                <div className="mt-2 text-xs text-stone-500">
                                  {formatDisplayTime(item.finished_at, {
                                    withSeconds: true,
                                  })}
                                </div>
                              </td>
                            </tr>
                          );
                        })
                      ) : (
                        <tr>
                          <td
                            colSpan={6}
                            className="px-4 py-6 text-center text-sm text-stone-500"
                          >
                            {selectedInformationEnrichmentItems.length > 0
                              ? "当前状态下暂无导师。"
                              : "暂无任务明细。"}
                          </td>
                        </tr>
                      )}
                    </tbody>
                  </table>
                </div>
                <Pagination
                  page={informationEnrichmentItemPage}
                  pageSize={informationEnrichmentItemPageSize}
                  totalCount={filteredInformationEnrichmentItems.length}
                  onChange={setInformationEnrichmentItemPagination}
                  ariaLabel="信息补全导师明细分页"
                  pageSizeAriaLabel="信息补全导师明细每页数量"
                  variant="compact"
                  pageSizeOptions={DETAIL_PAGE_SIZE_OPTIONS}
                  unitLabel="位"
                  itemLabel="位导师"
                  focusTargetRef={informationEnrichmentItemsStartRef}
                  menuPlacement="popover"
                  className="mt-3 border-t border-stone-100 pt-3"
                />
              </section>
            </div>
          </section>
        </div>
      ) : null}
      {selectedCrawlJob ? (
        <div
          className="fixed inset-0 z-50 flex items-stretch justify-center bg-stone-950/30 p-0 sm:p-6"
          onClick={crawlJobDetailsLayer.onBackdropClick}
          onMouseDown={crawlJobDetailsLayer.onBackdropMouseDown}
        >
          <section
            role="dialog"
            aria-label="抓取任务详情"
            className="flex h-full w-full flex-col overflow-hidden bg-white shadow-xl sm:max-w-[min(94vw,1280px)] sm:rounded-3xl"
            onClick={crawlJobDetailsLayer.onContentClick}
            onMouseDown={crawlJobDetailsLayer.onContentMouseDown}
          >
            <div className="flex items-start justify-between gap-4 border-b border-stone-200 bg-[#fcfbf8] px-6 py-5">
              <div>
                <div className="flex items-center gap-2 text-xs font-medium text-stone-500">
                  <Activity className="h-4 w-4 text-primary" />
                  实时抓取监控
                </div>
                <h2 className="text-xl font-semibold text-stone-900">
                  {selectedCrawlJob.university} / {selectedCrawlJob.school}
                </h2>
                <p className="mt-2 break-all text-sm text-stone-500">
                  {selectedCrawlJob.start_url}
                </p>
              </div>
              <button
                type="button"
                onClick={closeCrawlJobDetails}
                className="ui-btn-secondary shrink-0"
                aria-label="关闭"
              >
                <X className="h-4 w-4" />
                关闭
              </button>
            </div>

            <div
              data-testid="crawl-job-detail-scroll"
              className="flex-1 space-y-6 overflow-y-auto overscroll-contain px-6 py-5"
            >
              <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4 2xl:grid-cols-8">
                <div className="rounded-2xl border border-stone-100 bg-white px-4 py-3">
                  <div className="text-xs font-medium text-stone-500">
                    当前状态
                  </div>
                  <div className="mt-2 text-sm font-semibold text-stone-900">
                    {CRAWL_JOB_STATUS_LABELS[selectedCrawlJob.status]}
                  </div>
                </div>
                <div className="rounded-2xl border border-stone-100 bg-white px-4 py-3">
                  <div className="text-xs font-medium text-stone-500">
                    已抓页面
                  </div>
                  <div className="mt-2 text-sm font-semibold text-stone-900">
                    {selectedCrawlJob.page_count}
                  </div>
                </div>
                <div className="rounded-2xl border border-stone-100 bg-white px-4 py-3">
                  <div className="text-xs font-medium text-stone-500">
                    候选导师
                  </div>
                  <div className="mt-2 text-sm font-semibold text-stone-900">
                    {selectedCrawlJob.candidate_count}
                  </div>
                </div>
                <div className="rounded-2xl border border-stone-100 bg-white px-4 py-3">
                  <div className="text-xs font-medium text-stone-500">
                    输入 Token
                  </div>
                  <div className="mt-2 text-sm font-semibold text-stone-900">
                    {selectedCrawlJob.input_tokens.toLocaleString("zh-CN")}
                  </div>
                </div>
                <div className="rounded-2xl border border-stone-100 bg-white px-4 py-3">
                  <div className="text-xs font-medium text-stone-500">
                    输出 Token
                  </div>
                  <div className="mt-2 text-sm font-semibold text-stone-900">
                    {selectedCrawlJob.output_tokens.toLocaleString("zh-CN")}
                  </div>
                </div>
                <div className="rounded-2xl border border-stone-100 bg-white px-4 py-3">
                  <div className="text-xs font-medium text-stone-500">
                    缓存命中 Token
                  </div>
                  <div className="mt-2 text-sm font-semibold text-stone-900">
                    {selectedCrawlJob.cached_tokens.toLocaleString("zh-CN")}
                  </div>
                </div>
                <div className="rounded-2xl border border-stone-100 bg-white px-4 py-3">
                  <div className="text-xs font-medium text-stone-500">
                    总 Token
                  </div>
                  <div className="mt-2 text-sm font-semibold text-stone-900">
                    {selectedCrawlJob.total_tokens.toLocaleString("zh-CN")}
                  </div>
                </div>
                <div className="rounded-2xl border border-stone-100 bg-white px-4 py-3">
                  <div className="text-xs font-medium text-stone-500">
                    已耗时长
                  </div>
                  <div className="mt-2 text-sm font-semibold text-stone-900">
                    {formatDuration(selectedCrawlJob.duration_seconds)}
                  </div>
                </div>
              </div>
              {selectedCrawlJob.error_message ? (
                <div className="rounded-2xl border border-red-100 bg-red-50 px-4 py-3 text-sm text-red-700">
                  {selectedCrawlJob.error_message}
                </div>
              ) : null}

              {crawlJobDetailsLoading ? (
                <div className="flex items-center gap-2 text-sm text-stone-500">
                  <Loader2 className="h-4 w-4 animate-spin" />
                  正在加载日志详情...
                </div>
              ) : null}

              <div className="grid items-stretch gap-6 xl:grid-cols-2">
                <section
                  ref={crawlEventsStartRef}
                  tabIndex={-1}
                  aria-label="抓取执行日志"
                  className="flex h-full scroll-mt-24 flex-col focus:outline-none"
                >
                  <h3 className="flex items-center gap-2 text-sm font-semibold text-stone-900">
                    <Activity className="h-4 w-4 text-primary" />
                    执行日志
                  </h3>
                  <div
                    className="mt-3 flex-1 space-y-3"
                    data-monitor-section-list
                  >
                    {crawlJobEvents.length > 0 ? (
                      visibleCrawlJobEvents.map((event) => {
                        const failureReason = getCrawlEventFailureReason(event);
                        return (
                          <div key={event.id} className="flex gap-3">
                            <span className="mt-1 h-2 w-2 shrink-0 rounded-full bg-primary" />
                            <div className="min-w-0 flex-1 rounded-2xl border border-stone-100 px-4 py-3">
                              <p className="text-sm text-stone-800">
                                {event.message}
                              </p>
                              {failureReason ? (
                                <p className="mt-2 text-xs leading-5 text-red-700">
                                  失败原因：{failureReason}
                                </p>
                              ) : null}
                              <p className="mt-1 text-xs text-stone-500">
                                {formatDisplayTime(event.created_at, {
                                  withSeconds: true,
                                })}
                              </p>
                            </div>
                          </div>
                        );
                      })
                    ) : (
                      <p className="rounded-2xl border border-dashed border-stone-200 px-4 py-3 text-sm text-stone-500">
                        暂无执行日志。
                      </p>
                    )}
                  </div>
                  <Pagination
                    page={safeCrawlEventPage}
                    pageSize={crawlEventPageSize}
                    totalCount={crawlJobEvents.length}
                    onChange={handleCrawlEventPaginationChange}
                    ariaLabel="抓取执行日志分页"
                    pageSizeAriaLabel="抓取执行日志每页数量"
                    variant="compact"
                    pageSizeOptions={MONITOR_PAGE_SIZE_OPTIONS}
                    unitLabel="条"
                    itemLabel="条日志"
                    focusTargetRef={crawlEventsStartRef}
                    menuPlacement="popover"
                    className="mt-3 border-t border-stone-100 pt-3"
                  />
                </section>

                <section
                  ref={crawlPagesStartRef}
                  tabIndex={-1}
                  aria-label="已抓页面列表"
                  className="flex h-full scroll-mt-24 flex-col focus:outline-none"
                >
                  <h3 className="flex items-center gap-2 text-sm font-semibold text-stone-900">
                    <FileSearch className="h-4 w-4 text-sky-600" />
                    已抓页面
                  </h3>
                  <div
                    className="mt-3 flex-1 space-y-2"
                    data-monitor-section-list
                  >
                    {crawlJobPages.length > 0 ? (
                      visibleCrawlJobPages.map((page) => (
                        <div
                          key={page.id}
                          className="rounded-2xl border border-stone-100 px-4 py-3"
                        >
                          <p className="text-sm font-medium text-stone-800">
                            {page.title ?? page.url}
                          </p>
                          <p className="mt-1 break-all text-xs text-stone-500">
                            {page.url}
                          </p>
                        </div>
                      ))
                    ) : (
                      <p className="rounded-2xl border border-dashed border-stone-200 px-4 py-3 text-sm text-stone-500">
                        暂无已抓页面。
                      </p>
                    )}
                  </div>
                  <Pagination
                    page={safeCrawlDetailPagePage}
                    pageSize={crawlDetailPagePageSize}
                    totalCount={crawlJobPages.length}
                    onChange={handleCrawlDetailPagePaginationChange}
                    ariaLabel="已抓页面分页"
                    pageSizeAriaLabel="已抓页面每页数量"
                    variant="compact"
                    pageSizeOptions={MONITOR_PAGE_SIZE_OPTIONS}
                    unitLabel="个"
                    itemLabel="个页面"
                    focusTargetRef={crawlPagesStartRef}
                    menuPlacement="popover"
                    className="mt-3 border-t border-stone-100 pt-3"
                  />
                </section>
              </div>

              <section
                ref={crawlCandidatesStartRef}
                tabIndex={-1}
                aria-label="候选导师列表"
                className="scroll-mt-24 focus:outline-none"
              >
                <h3 className="flex items-center gap-2 text-sm font-semibold text-stone-900">
                  <CheckCircle2 className="h-4 w-4 text-emerald-600" />
                  候选导师
                </h3>
                <div className="mt-3 space-y-2">
                  {crawlJobCandidates.length > 0 ? (
                    <div
                      data-testid="crawl-candidate-review-toolbar"
                      className="overflow-visible rounded-2xl border border-stone-200 bg-stone-50/70"
                    >
                      <div className="grid gap-3 p-4 md:grid-cols-2 xl:grid-cols-[minmax(22rem,2fr)_minmax(12rem,1fr)_minmax(11rem,1fr)]">
                        <div className="min-w-0 md:col-span-2 xl:col-span-1">
                          <div className="mb-2 text-sm font-medium text-stone-800">
                            关键词
                          </div>
                          <div className="ui-select-shell h-10 min-h-10 w-full py-0">
                            <Search className="h-4 w-4 shrink-0 text-stone-400" />
                            <input
                              type="search"
                              aria-label="搜索候选导师"
                              value={crawlCandidateFilters.keyword}
                              onChange={(event) =>
                                updateCrawlCandidateFilters({
                                  keyword: event.target.value,
                                })
                              }
                              placeholder={getCrawlCandidateSearchPlaceholder(
                                crawlCandidateFilters.searchScopes,
                              )}
                              className="w-full min-w-0 bg-transparent text-sm leading-5 outline-none placeholder:text-stone-400"
                            />
                            <KeywordSearchScopeSelect
                              label="搜索范围"
                              options={CRAWL_CANDIDATE_SEARCH_SCOPE_OPTIONS}
                              selectedValues={crawlCandidateFilters.searchScopes}
                              embedded
                              onChange={(searchScopes) =>
                                updateCrawlCandidateFilters({
                                  searchScopes:
                                    normalizeCrawlCandidateSearchScopes(
                                      searchScopes,
                                    ),
                                })
                              }
                            />
                          </div>
                        </div>
                        <div className="min-w-0">
                          <div className="mb-2 text-sm font-medium text-stone-800">
                            资料条件
                          </div>
                          <button
                            type="button"
                            aria-label={`资料条件：${crawlCandidateInformationConditionsSummary}`}
                            aria-expanded={crawlCandidateInformationFiltersOpen}
                            aria-controls="crawl-candidate-information-filters"
                            onClick={() =>
                              setCrawlCandidateInformationFiltersOpen(
                                (currentOpen) => !currentOpen,
                              )
                            }
                            className={`ui-select-shell h-10 min-h-10 w-full ${
                              crawlCandidateInformationFiltersOpen
                                ? "border-primary/45 bg-white ring-2 ring-primary/10"
                                : ""
                            }`}
                          >
                            <span className="flex-1 truncate text-left text-sm text-stone-700">
                              {crawlCandidateInformationConditionsSummary}
                            </span>
                            <ChevronDown
                              className={`ui-select-chevron ${
                                crawlCandidateInformationFiltersOpen
                                  ? "rotate-180 text-primary"
                                  : ""
                              }`}
                            />
                          </button>
                        </div>
                        <NativeSelectField
                          label="审核状态"
                          ariaLabel="候选导师审核状态"
                          value={crawlCandidateFilters.reviewStatus}
                          onChange={(event) =>
                            updateCrawlCandidateFilters({
                              reviewStatus: event.target
                                .value as CrawlCandidateReviewStatusFilter,
                            })
                          }
                          shellClassName="h-10 min-h-10"
                        >
                          <option value="all">全部状态</option>
                          <option value="pending">待审核</option>
                          <option value="accepted">已通过</option>
                          <option value="merged">已合并</option>
                          <option value="rejected">已拒绝</option>
                        </NativeSelectField>
                      </div>

                      {crawlCandidateInformationFiltersOpen ? (
                        <div
                          id="crawl-candidate-information-filters"
                          data-testid="crawl-candidate-information-filters"
                          className="border-t border-stone-200 bg-white px-4 py-4"
                        >
                          <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
                            {CRAWL_CANDIDATE_INFORMATION_FIELD_OPTIONS.map(
                              ({ field, label }) => (
                                <NativeSelectField
                                  key={field}
                                  label={label}
                                  ariaLabel={`候选导师${label}条件`}
                                  value={
                                    crawlCandidateFilters
                                      .informationConditions[field] ?? "any"
                                  }
                                  onChange={(event) =>
                                    updateCrawlCandidateInformationCondition(
                                      field,
                                      event.target.value as
                                        | CrawlCandidateInformationCondition
                                        | "any",
                                    )
                                  }
                                  shellClassName="h-10 min-h-10"
                                >
                                  <option value="any">不限</option>
                                  <option value="present">有{label}</option>
                                  <option value="missing">无{label}</option>
                                </NativeSelectField>
                              ),
                            )}
                          </div>
                          <div className="mt-4 flex flex-wrap items-center justify-between gap-3 border-t border-stone-100 pt-4">
                            <div>
                              <div className="text-sm font-medium text-stone-800">
                                多个资料条件之间
                              </div>
                              <div className="mt-1 text-xs text-stone-500">
                                {activeCrawlCandidateInformationConditionCount < 2
                                  ? "选择两个及以上条件后可切换关系"
                                  : `当前有 ${activeCrawlCandidateInformationConditionCount} 个条件`}
                              </div>
                            </div>
                            <div className="inline-flex gap-1 rounded-xl border border-stone-200 bg-stone-50 p-1">
                              {(
                                ["all", "any"] as CrawlCandidateInformationMatchMode[]
                              ).map((matchMode) => {
                                const selected =
                                  crawlCandidateFilters.informationMatchMode ===
                                  matchMode;
                                return (
                                  <button
                                    key={matchMode}
                                    type="button"
                                    aria-pressed={selected}
                                    disabled={
                                      activeCrawlCandidateInformationConditionCount <
                                      2
                                    }
                                    onClick={() =>
                                      updateCrawlCandidateFilters({
                                        informationMatchMode: matchMode,
                                      })
                                    }
                                    className={`rounded-lg px-3 py-1.5 text-xs font-medium transition disabled:cursor-not-allowed disabled:opacity-50 ${
                                      selected
                                        ? "bg-primary text-white shadow-sm shadow-primary/20"
                                        : "text-stone-600 hover:bg-white hover:text-stone-900"
                                    }`}
                                  >
                                    {matchMode === "all"
                                      ? "全部满足（且）"
                                      : "任一满足（或）"}
                                  </button>
                                );
                              })}
                            </div>
                          </div>
                        </div>
                      ) : null}

                      <div className="flex flex-wrap items-center justify-between gap-3 border-t border-stone-200 bg-white/80 px-4 py-3">
                        <div className="text-sm text-stone-600">
                          显示 {filteredCrawlJobCandidates.length} /{" "}
                          {crawlJobCandidates.length} 位
                          {selectedCrawlJobCanReview ? (
                            <>
                              {" "}
                              · 可导入 {reviewableCrawlCandidateIds.length} 位 ·
                              无邮箱{" "}
                              {reviewableCrawlCandidateIdsWithoutEmail.length} 位
                            </>
                          ) : null}
                        </div>
                        {crawlCandidateFiltersActive ||
                        selectedCrawlJobCanReview ? (
                          <div className="flex flex-wrap items-center gap-2">
                            {crawlCandidateFiltersActive ? (
                              <button
                                type="button"
                                onClick={resetCrawlCandidateFilters}
                                className="ui-btn-secondary min-h-9 px-3 py-1.5 text-sm"
                              >
                                重置筛选
                              </button>
                            ) : null}
                            {selectedCrawlJobCanReview ? (
                              <button
                                type="button"
                                aria-label={
                                  allFilteredCrawlCandidatesSelected
                                    ? "取消选择全部筛选结果"
                                    : "选择全部筛选结果"
                                }
                                aria-pressed={
                                  allFilteredCrawlCandidatesSelected
                                }
                                onClick={
                                  handleToggleFilteredCrawlCandidateSelection
                                }
                                disabled={
                                  filteredReviewableCrawlCandidateIds.length ===
                                    0 ||
                                  crawlJobApproveLoading ||
                                  crawlJobEnrichLoading
                                }
                                className={`inline-flex min-h-9 items-center gap-2 rounded-xl border px-3 text-sm font-medium transition disabled:cursor-not-allowed disabled:opacity-50 ${
                                  allFilteredCrawlCandidatesSelected
                                    ? "border-primary/30 bg-primary/5 text-primary"
                                    : "border-stone-200 bg-white text-stone-700 hover:border-primary/40 hover:text-primary"
                                }`}
                              >
                                {allFilteredCrawlCandidatesSelected ? (
                                  <SquareCheck className="h-4 w-4" />
                                ) : someFilteredCrawlCandidatesSelected ? (
                                  <SquareMinus className="h-4 w-4" />
                                ) : (
                                  <Square className="h-4 w-4" />
                                )}
                                {allFilteredCrawlCandidatesSelected
                                  ? "取消选择全部筛选结果"
                                  : "选择全部筛选结果"}
                              </button>
                            ) : null}
                          </div>
                        ) : null}
                      </div>

                      {selectedCrawlJobCanReview &&
                      selectedReviewableCrawlCandidateIds.length > 0 ? (
                        <div className="flex flex-wrap items-center justify-between gap-3 border-t border-amber-200 bg-amber-50/80 px-4 py-3">
                          <div className="text-sm text-amber-950">
                            已选 {selectedReviewableCrawlCandidateIds.length} 位
                            <span className="mt-1 block text-xs text-amber-700">
                              当前筛选结果中已选{" "}
                              {filteredSelectedCrawlCandidateCount} 位，其中无邮箱{" "}
                              {selectedCrawlCandidateIdsWithoutEmail.length} 位
                            </span>
                          </div>
                          <div className="flex flex-wrap gap-2">
                            <button
                              type="button"
                              onClick={() => setSelectedCrawlCandidateIds([])}
                              disabled={
                                crawlJobApproveLoading || crawlJobEnrichLoading
                              }
                              className="ui-btn-secondary px-3 py-2 text-sm disabled:cursor-not-allowed disabled:opacity-60"
                            >
                              清空选择
                            </button>
                            <button
                              type="button"
                              onClick={() =>
                                void handleEnrichSelectedCrawlCandidates()
                              }
                              disabled={
                                crawlJobApproveLoading || crawlJobEnrichLoading
                              }
                              className="ui-btn-secondary px-3 py-2 text-sm disabled:cursor-not-allowed disabled:opacity-60"
                            >
                              {crawlJobEnrichLoading
                                ? "补全中..."
                                : "补全缺失信息"}
                            </button>
                            <button
                              type="button"
                              onClick={() =>
                                void handleApproveSelectedCrawlCandidates()
                              }
                              disabled={
                                crawlJobApproveLoading || crawlJobEnrichLoading
                              }
                              className="ui-btn-primary px-3 py-2 text-sm disabled:cursor-not-allowed disabled:opacity-60"
                            >
                              {crawlJobApproveLoading
                                ? "导入中..."
                                : "审核通过并导入"}
                            </button>
                          </div>
                        </div>
                      ) : null}
                    </div>
                  ) : null}
                  {selectedCrawlJobNeedsReviewResume &&
                  reviewableCrawlCandidateIds.length > 0 ? (
                    <div className="rounded-2xl border border-stone-200 bg-stone-50 px-4 py-3 text-sm text-stone-700">
                      请先将任务转入待审核状态，再补全或审核导入候选导师。
                    </div>
                  ) : null}
                  {filteredCrawlJobCandidates.length > 0 ? (
                    visibleCrawlJobCandidates.map((candidate, index) => {
                      const candidateMissingEmail = !candidate.email?.trim();
                      const candidateCanEdit =
                        selectedCrawlJobCanReview &&
                        candidate.review_status === "pending";

                      return (
                        <div
                          key={candidate.id}
                          ref={
                            index === 0
                              ? crawlCandidateFirstItemRef
                              : undefined
                          }
                          tabIndex={index === 0 ? -1 : undefined}
                          className="scroll-mt-6 rounded-2xl border border-stone-100 bg-white px-4 py-3 focus:outline-none"
                        >
                          <div className="flex flex-wrap items-start justify-between gap-3">
                            <div className="flex min-w-0 items-start gap-3">
                              {selectedCrawlJobCanReview ? (
                                <div className="mt-1 shrink-0">
                                  <SelectionToggleButton
                                    label={`选择候选导师 ${candidate.name}`}
                                    selected={selectedReviewableCrawlCandidateIds.includes(
                                      candidate.id,
                                    )}
                                    disabled={
                                      candidate.review_status !== "pending" ||
                                      crawlJobApproveLoading ||
                                      crawlJobEnrichLoading
                                    }
                                    onToggle={() =>
                                      handleToggleCrawlCandidateSelection(
                                        candidate.id,
                                      )
                                    }
                                  />
                                </div>
                              ) : null}
                              <div className="min-w-0">
                                <div className="flex flex-wrap items-center gap-2">
                                  <p className="text-sm font-medium text-stone-800">
                                    {candidate.name}
                                  </p>
                                  {candidate.title ? (
                                    <span className="text-xs text-stone-500">
                                      {candidate.title}
                                    </span>
                                  ) : null}
                                  {candidateMissingEmail ? (
                                    <span className="rounded-full border border-amber-200 bg-amber-50 px-2 py-0.5 text-xs font-medium text-amber-700">
                                      邮箱为空
                                    </span>
                                  ) : null}
                                </div>
                                <p
                                  className={`mt-1 break-all ${
                                    candidateMissingEmail
                                      ? "text-xs text-amber-700"
                                      : "text-sm text-stone-600"
                                  }`}
                                >
                                  {candidate.email?.trim() ||
                                    "暂无邮箱（可手工填写或选中后尝试使用补全功能）"}
                                </p>
                                {[candidate.school, candidate.department]
                                  .filter(Boolean)
                                  .join(" / ") ? (
                                  <p className="mt-1 text-xs text-stone-400">
                                    {[candidate.school, candidate.department]
                                      .filter(Boolean)
                                      .join(" / ")}
                                  </p>
                                ) : null}
                                {selectedCrawlJobNeedsReviewResume &&
                                candidate.review_status === "pending" ? (
                                  <p className="mt-2 text-xs text-amber-700">
                                    先转入待审核后才可补全或审核导入
                                  </p>
                                ) : null}
                              </div>
                            </div>
                            <div className="flex shrink-0 flex-wrap items-center gap-2">
                              <span className="rounded-full bg-emerald-50 px-3 py-1 text-xs text-emerald-700">
                                置信度 {Math.round(candidate.confidence * 100)}%
                              </span>
                              <span
                                className={`rounded-full border px-3 py-1 text-xs ${
                                  CRAWL_CANDIDATE_REVIEW_STATUS_TONES[
                                    candidate.review_status
                                  ]
                                }`}
                              >
                                {
                                  CRAWL_CANDIDATE_REVIEW_STATUS_LABELS[
                                    candidate.review_status
                                  ]
                                }
                              </span>
                              {candidateMissingEmail && candidateCanEdit ? (
                                <button
                                  type="button"
                                  onClick={() => {
                                    setSelectedCandidateDetail(candidate);
                                    setCandidateEditForm(
                                      toCrawlCandidateEditForm(candidate),
                                    );
                                  }}
                                  className="ui-btn-secondary px-3 py-2 text-sm"
                                >
                                  <Pencil className="h-4 w-4" />
                                  填写邮箱
                                </button>
                              ) : null}
                              <button
                                type="button"
                                onClick={() => {
                                  setCandidateEditForm(null);
                                  setSelectedCandidateDetail(candidate);
                                }}
                                className="ui-btn-secondary px-3 py-2 text-sm"
                              >
                                查看详情
                              </button>
                            </div>
                          </div>
                        </div>
                      );
                    })
                  ) : crawlJobCandidates.length > 0 ? (
                    <div className="rounded-2xl border border-dashed border-stone-200 bg-white px-6 py-8 text-center">
                      <Search className="mx-auto h-6 w-6 text-stone-300" />
                      <p className="mt-3 text-sm font-medium text-stone-700">
                        没有符合筛选条件的候选导师
                      </p>
                    </div>
                  ) : (
                    <p className="rounded-2xl border border-dashed border-stone-200 px-4 py-3 text-sm text-stone-500">
                      暂无候选导师。
                    </p>
                  )}
                </div>
                {filteredCrawlJobCandidates.length > 0 ? (
                  <Pagination
                    page={safeCrawlCandidatePage}
                    pageSize={crawlCandidatePageSize}
                    totalCount={filteredCrawlJobCandidates.length}
                    onChange={handleCrawlCandidatePaginationChange}
                    ariaLabel="候选导师分页"
                    pageSizeAriaLabel="候选导师每页数量"
                    variant="compact"
                    pageSizeOptions={MONITOR_PAGE_SIZE_OPTIONS}
                    unitLabel="位"
                    itemLabel="位导师"
                    summary={`共 ${filteredCrawlJobCandidates.length} 位符合筛选条件，已选 ${selectedReviewableCrawlCandidateIds.length} 位`}
                    focusTargetRef={crawlCandidateFirstItemRef}
                    menuPlacement="popover"
                    className="mt-3 border-t border-stone-100 pt-3"
                  />
                ) : null}
              </section>
            </div>
          </section>
        </div>
      ) : null}
      {resendDialogOpen ? (
        <BatchTaskResendDialog
          context={resendContext}
          loading={resendLoading}
          selectedProfessorIds={selectedResendProfessorIds}
          onSelectAll={handleSelectAllResendProfessors}
          onClear={() => setSelectedResendProfessorIds([])}
          onToggleProfessor={handleToggleResendProfessor}
          onClose={() => setResendDialogOpen(false)}
          onSubmit={() => void handleSubmitBatchResend()}
        />
      ) : null}      {selectedCandidateDetail ? (
        <div
          className="fixed inset-0 z-[60] flex items-center justify-center bg-stone-950/35 p-4"
          onClick={candidateDetailLayer.onBackdropClick}
          onMouseDown={candidateDetailLayer.onBackdropMouseDown}
        >
          <section
            role="dialog"
            aria-label="候选导师详情"
            className="flex max-h-[90vh] w-full max-w-3xl flex-col overflow-hidden rounded-3xl bg-white shadow-2xl"
            onClick={candidateDetailLayer.onContentClick}
            onMouseDown={candidateDetailLayer.onContentMouseDown}
          >
            <div className="flex items-start justify-between gap-4 border-b border-stone-200 px-6 py-5">
              <div className="min-w-0">
                <p className="text-xs font-medium uppercase tracking-[0.18em] text-stone-400">
                  {candidateEditForm ? "编辑候选导师" : "候选导师详情"}
                </p>
                <h3 className="mt-2 text-xl font-semibold text-stone-900">
                  {selectedCandidateDetail.name}
                </h3>
                <p className="mt-1 text-sm text-stone-500">
                  {candidateEditForm
                    ? "手动修正待审核资料，保存后仍可继续补全缺失信息。"
                    : selectedCandidateDetail.email?.trim() ||
                      "暂无邮箱（可尝试进行补全）"}
                </p>
              </div>
              <div className="flex shrink-0 flex-wrap justify-end gap-2">
                {!candidateEditForm &&
                selectedCrawlJobCanReview &&
                selectedCandidateDetail.review_status === "pending" ? (
                  <button
                    type="button"
                    onClick={handleStartCandidateEdit}
                    disabled={candidateUpdateLoading}
                    className="ui-btn-secondary disabled:cursor-not-allowed disabled:opacity-60"
                  >
                    <Pencil className="h-4 w-4" />
                    编辑
                  </button>
                ) : null}
                <button
                  type="button"
                  onClick={closeSelectedCandidateDetail}
                  disabled={candidateUpdateLoading}
                  className="ui-btn-secondary disabled:cursor-not-allowed disabled:opacity-60"
                  aria-label="关闭候选导师详情"
                >
                  <X className="h-4 w-4" />
                  关闭
                </button>
              </div>
            </div>
            {candidateEditForm ? (
              <form
                onSubmit={(event) => void handleSaveCandidateEdit(event)}
                className="flex min-h-0 flex-1 flex-col"
              >
                <div
                  data-testid="candidate-detail-scroll"
                  className="grid flex-1 gap-4 overflow-y-auto overscroll-contain px-6 py-5 md:grid-cols-2"
                >
                  <label className="rounded-2xl border border-stone-100 bg-stone-50/70 px-4 py-3 text-xs font-medium text-stone-500">
                    姓名
                    <input
                      type="text"
                      required
                      value={candidateEditForm.name}
                      onChange={(event) =>
                        handleCandidateEditFieldChange("name", event.target.value)
                      }
                      className={CRAWL_CANDIDATE_EDIT_INPUT_CLASS}
                    />
                  </label>
                  <label className="rounded-2xl border border-stone-100 bg-stone-50/70 px-4 py-3 text-xs font-medium text-stone-500">
                    邮箱
                    <input
                      type="email"
                      value={candidateEditForm.email}
                      placeholder="例如 professor@example.edu"
                      onChange={(event) =>
                        handleCandidateEditFieldChange("email", event.target.value)
                      }
                      className={CRAWL_CANDIDATE_EDIT_INPUT_CLASS}
                    />
                  </label>
                  <label className="rounded-2xl border border-stone-100 bg-stone-50/70 px-4 py-3 text-xs font-medium text-stone-500">
                    职称
                    <input
                      type="text"
                      value={candidateEditForm.title}
                      onChange={(event) =>
                        handleCandidateEditFieldChange("title", event.target.value)
                      }
                      className={CRAWL_CANDIDATE_EDIT_INPUT_CLASS}
                    />
                  </label>
                  <label className="rounded-2xl border border-stone-100 bg-stone-50/70 px-4 py-3 text-xs font-medium text-stone-500">
                    部门
                    <input
                      type="text"
                      value={candidateEditForm.department}
                      onChange={(event) =>
                        handleCandidateEditFieldChange(
                          "department",
                          event.target.value,
                        )
                      }
                      className={CRAWL_CANDIDATE_EDIT_INPUT_CLASS}
                    />
                  </label>
                  <label className="rounded-2xl border border-stone-100 bg-stone-50/70 px-4 py-3 text-xs font-medium text-stone-500">
                    院校
                    <input
                      type="text"
                      value={candidateEditForm.university}
                      onChange={(event) =>
                        handleCandidateEditFieldChange(
                          "university",
                          event.target.value,
                        )
                      }
                      className={CRAWL_CANDIDATE_EDIT_INPUT_CLASS}
                    />
                  </label>
                  <label className="rounded-2xl border border-stone-100 bg-stone-50/70 px-4 py-3 text-xs font-medium text-stone-500">
                    学院
                    <input
                      type="text"
                      value={candidateEditForm.school}
                      onChange={(event) =>
                        handleCandidateEditFieldChange("school", event.target.value)
                      }
                      className={CRAWL_CANDIDATE_EDIT_INPUT_CLASS}
                    />
                  </label>
                  <label className="rounded-2xl border border-stone-100 bg-stone-50/70 px-4 py-3 text-xs font-medium text-stone-500 md:col-span-2">
                    研究方向
                    <textarea
                      value={candidateEditForm.researchDirection}
                      rows={3}
                      onChange={(event) =>
                        handleCandidateEditFieldChange(
                          "researchDirection",
                          event.target.value,
                        )
                      }
                      className={`${CRAWL_CANDIDATE_EDIT_INPUT_CLASS} resize-y leading-6`}
                    />
                  </label>
                  <label className="rounded-2xl border border-stone-100 bg-stone-50/70 px-4 py-3 text-xs font-medium text-stone-500 md:col-span-2">
                    近期论文
                    <textarea
                      value={candidateEditForm.recentPapers}
                      rows={5}
                      placeholder="每行填写一篇论文"
                      onChange={(event) =>
                        handleCandidateEditFieldChange(
                          "recentPapers",
                          event.target.value,
                        )
                      }
                      className={`${CRAWL_CANDIDATE_EDIT_INPUT_CLASS} resize-y leading-6`}
                    />
                    <span className="mt-2 block font-normal text-stone-400">
                      每行一篇，空行会在保存时自动忽略。
                    </span>
                  </label>
                  <label className="rounded-2xl border border-stone-100 bg-stone-50/70 px-4 py-3 text-xs font-medium text-stone-500 md:col-span-2">
                    资料页
                    <input
                      type="url"
                      value={candidateEditForm.profileUrl}
                      placeholder="https://example.edu/profile"
                      onChange={(event) =>
                        handleCandidateEditFieldChange(
                          "profileUrl",
                          event.target.value,
                        )
                      }
                      className={CRAWL_CANDIDATE_EDIT_INPUT_CLASS}
                    />
                  </label>
                  <label className="rounded-2xl border border-stone-100 bg-stone-50/70 px-4 py-3 text-xs font-medium text-stone-500 md:col-span-2">
                    来源页
                    <input
                      type="url"
                      value={candidateEditForm.sourceUrl}
                      placeholder="https://example.edu/faculty"
                      onChange={(event) =>
                        handleCandidateEditFieldChange(
                          "sourceUrl",
                          event.target.value,
                        )
                      }
                      className={CRAWL_CANDIDATE_EDIT_INPUT_CLASS}
                    />
                  </label>
                </div>
                <div className="flex flex-wrap items-center justify-between gap-3 border-t border-stone-200 bg-stone-50/80 px-6 py-4">
                  <p className="max-w-xl text-pretty text-xs leading-5 text-stone-500">
                    保存后仍可补全缺失信息；已有内容（包括本次手动修改）不会被覆盖。
                  </p>
                  <div className="ml-auto flex items-center gap-2">
                    <button
                      type="button"
                      onClick={handleCancelCandidateEdit}
                      disabled={candidateUpdateLoading}
                      className="ui-btn-secondary disabled:cursor-not-allowed disabled:opacity-60"
                    >
                      取消
                    </button>
                    <button
                      type="submit"
                      disabled={candidateUpdateLoading}
                      className="ui-btn-primary disabled:cursor-not-allowed disabled:opacity-60"
                    >
                      {candidateUpdateLoading ? (
                        <Loader2 className="h-4 w-4 animate-spin" />
                      ) : (
                        <Save className="h-4 w-4" />
                      )}
                      {candidateUpdateLoading ? "保存中..." : "保存修改"}
                    </button>
                  </div>
                </div>
              </form>
            ) : (
              <div
                data-testid="candidate-detail-scroll"
                className="grid flex-1 gap-4 overflow-y-auto overscroll-contain px-6 py-5 md:grid-cols-2"
              >
              <div className="rounded-2xl border border-stone-100 bg-stone-50/70 px-4 py-3">
                <div className="text-xs font-medium text-stone-500">职称</div>
                <div className="mt-2 text-sm text-stone-900">
                  {selectedCandidateDetail.title || "暂无"}
                </div>
              </div>
              <div className="rounded-2xl border border-stone-100 bg-stone-50/70 px-4 py-3">
                <div className="text-xs font-medium text-stone-500">
                  院校 / 学院
                </div>
                <div className="mt-2 text-sm text-stone-900">
                  {[
                    selectedCandidateDetail.university,
                    selectedCandidateDetail.school,
                  ]
                    .filter(Boolean)
                    .join(" / ") || "暂无"}
                </div>
              </div>
              <div className="rounded-2xl border border-stone-100 bg-stone-50/70 px-4 py-3">
                <div className="text-xs font-medium text-stone-500">部门</div>
                <div className="mt-2 text-sm text-stone-900">
                  {selectedCandidateDetail.department || "暂无"}
                </div>
              </div>
              <div className="rounded-2xl border border-stone-100 bg-stone-50/70 px-4 py-3">
                <div className="text-xs font-medium text-stone-500">
                  审核状态
                </div>
                <div className="mt-2 text-sm text-stone-900">
                  {
                    CRAWL_CANDIDATE_REVIEW_STATUS_LABELS[
                      selectedCandidateDetail.review_status
                    ]
                  }
                </div>
              </div>
              <div className="rounded-2xl border border-stone-100 bg-stone-50/70 px-4 py-3 md:col-span-2">
                <div className="text-xs font-medium text-stone-500">
                  研究方向
                </div>
                <div className="mt-2 text-sm leading-6 text-stone-900">
                  {selectedCandidateDetail.research_direction || "暂无"}
                </div>
              </div>
              <div className="rounded-2xl border border-stone-100 bg-stone-50/70 px-4 py-3 md:col-span-2">
                <div className="text-xs font-medium text-stone-500">
                  近期论文
                </div>
                {selectedCandidateDetail.recent_papers.length > 0 ? (
                  <ul className="mt-2 space-y-2 text-sm text-stone-900">
                    {selectedCandidateDetail.recent_papers.map((paper) => (
                      <li key={paper} className="rounded-xl bg-white px-3 py-2">
                        {paper}
                      </li>
                    ))}
                  </ul>
                ) : (
                  <div className="mt-2 text-sm text-stone-900">暂无</div>
                )}
              </div>
              <div className="rounded-2xl border border-stone-100 bg-stone-50/70 px-4 py-3 md:col-span-2">
                <div className="text-xs font-medium text-stone-500">
                  链接信息
                </div>
                <div className="mt-2 space-y-2 text-sm text-stone-900">
                  <div>
                    <span className="text-stone-500">资料页：</span>
                    {renderCandidateExternalUrl(selectedCandidateDetail.profile_url)}
                  </div>
                  <div>
                    <span className="text-stone-500">来源页：</span>
                    {renderCandidateExternalUrl(selectedCandidateDetail.source_url)}
                  </div>
                </div>
              </div>
              {getCandidateEnrichmentFailureMessage(
                selectedCandidateDetail,
                crawlJobEvents,
              ) ? (
                <div className="rounded-2xl border border-red-200 bg-red-50/70 px-4 py-3 md:col-span-2">
                  <div className="text-xs font-medium text-red-700">
                    补全失败原因
                  </div>
                  <div className="mt-2 text-sm leading-6 text-red-900">
                    {getCandidateEnrichmentFailureMessage(
                      selectedCandidateDetail,
                      crawlJobEvents,
                    )}
                  </div>
                </div>
              ) : null}
              </div>
            )}
          </section>
        </div>
      ) : null}
      <ProfessorEditDialog
        open={professorEditDialogOpen}
        professor={professorEditProfessor}
        loading={professorEditLoading}
        onClose={closeProfessorEditDialog}
        onSaved={refreshAfterProfessorEdit}
      />
      {confirmDialog}
    </main>
  );
};
