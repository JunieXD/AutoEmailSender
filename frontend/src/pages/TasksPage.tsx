import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";
import {
  Activity,
  Bot,
  ChevronLeft,
  ChevronRight,
  CheckCircle2,
  Clock3,
  FileSearch,
  Loader2,
  Mail,
  Pause,
  Play,
  RotateCcw,
  Sparkles,
  Square,
  Trash2,
  X,
} from "lucide-react";
import { EmailTemplateEditor } from "@/components/molecules/EmailTemplateEditor";
import { SubjectTemplateInput } from "@/components/molecules/SubjectTemplateInput";
import { useNotification } from "@/context/NotificationContext";
import { useSelectionContext } from "@/context/SelectionContext";
import { useConfirmDialog } from "@/lib/useConfirmDialog";
import { safeRecordUserAction } from "@/lib/diagnosticUserActions";
import {
  approveAndSendBatchTaskItemDraft,
  approveBatchTaskItemDraft,
  deleteBatchTask,
  deleteBatchTaskItem,
  getBatchTaskItemThread,
  listBatchTasks,
  listBatchTaskItems,
  pauseBatchTask,
  regenerateBatchTaskItemDraft,
  retryBatchTaskItemDraft,
  restoreBatchTask,
  resumeBatchTask,
  stopBatchTask,
} from "@/lib/api/batchTasksApi";
import {
  cancelMatchAnalysisJob,
  deleteMatchAnalysisJob,
  listMatchAnalysisJobItems,
  listMatchAnalysisJobs,
  restoreMatchAnalysisJob,
  retryFailedMatchAnalysisJob,
} from "@/lib/api/matchAnalysisJobsApi";
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
} from "@/lib/api/crawlJobsApi";
import {
  getReviewableCandidateIdsWithoutEmail,
  getReviewableCandidateIds,
  pruneSelectedCandidateIds,
} from "@/features/crawl-review/client/reviewCandidates";
import {
  getCandidateEnrichmentFailureMessage,
  getCrawlEventFailureReason,
} from "@/features/crawl-review/client/crawlJobEvents";
import {
  buildBatchPendingItemAction,
  getBatchTaskItemCancellationText,
  getBatchTaskWaitingSendCount,
} from "@/features/batch-tasks/client/batchTaskDisplay";
import { formatApiDateTime } from "@/lib/dateTime";
import { getPageItems, getTotalPages } from "@/lib/pagination";
import { deriveTextFromEmailHtml, textToEmailHtml } from "@/lib/richEmail";
import {
  BATCH_TASK_STATUS_LABELS,
  MATERIAL_TYPE_LABELS,
  MATCH_ANALYSIS_JOB_STATUS_LABELS,
  PROFESSOR_STATUS_LABELS,
  type BatchTaskCardDTO,
  type BatchTaskItemDTO,
  type CrawlCandidateDTO,
  type CrawlCandidateReviewStatusDTO,
  type CrawlJobEventDTO,
  type CrawlJobStatusDTO,
  type CrawlJobSummaryDTO,
  type CrawlPageDTO,
  type MatchAnalysisJobDTO,
  type MatchAnalysisJobItemDTO,
  type MatchAnalysisJobStatus,
  type TaskListView,
  type WorkspaceTaskStatus,
  type WorkspaceThreadDTO,
} from "@/types";

type TasksTab = "batch" | "crawl" | "match";
type TaskListViews = Record<TasksTab, TaskListView>;
type BatchReviewItemActionType = "regenerate" | "delete" | "submit";
type BatchReviewItemActions = Record<number, BatchReviewItemActionType>;

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

const CRAWL_REFRESH_INTERVAL_MS = 5000;
const CRAWL_DETAILS_REFRESH_INTERVAL_MS = 5000;
const SCHEDULE_DATE_PATTERN = /^\d{4}-(\d{2})-(\d{2})$/;
const TASKS_PAGE_SIZE = 8;
const MONITOR_SECTION_PAGE_SIZE = 5;

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

type TaskListPaginationProps = {
  page: number;
  totalCount: number;
  onPageChange: (page: number) => void;
  pageSize?: number;
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

const canDeleteMatchJob = (job: MatchAnalysisJobDTO) =>
  job.status === "completed" ||
  job.status === "partial_failed" ||
  job.status === "failed" ||
  job.status === "canceled";

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
        <button
          type="button"
          onClick={() => onOpenDetails(job)}
          className="inline-flex h-10 w-10 items-center justify-center rounded-full border border-stone-200 bg-white text-stone-500 transition hover:border-primary/30 hover:bg-primary/5 hover:text-primary"
          aria-label="查看详情"
          title="查看详情"
        >
          <FileSearch className="h-4 w-4" />
        </button>
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
      </div>
    </div>
  </article>
);

const TaskListPagination = ({
  page,
  totalCount,
  onPageChange,
  pageSize = TASKS_PAGE_SIZE,
}: TaskListPaginationProps) => {
  const [jumpValue, setJumpValue] = useState(String(page));

  useEffect(() => {
    setJumpValue(String(page));
  }, [page]);

  if (totalCount <= pageSize) {
    return null;
  }

  const totalPages = getTotalPages(totalCount, pageSize);
  const startItem = (page - 1) * pageSize + 1;
  const endItem = Math.min(totalCount, page * pageSize);

  const commitJump = () => {
    const nextPage = Number.parseInt(jumpValue, 10);
    if (Number.isNaN(nextPage)) {
      setJumpValue(String(page));
      return;
    }
    onPageChange(Math.min(totalPages, Math.max(1, nextPage)));
  };

  return (
    <nav
      aria-label="任务分页"
      className="mt-4 flex flex-wrap items-center justify-between gap-3 rounded-2xl border border-stone-200 bg-white px-4 py-3 text-sm shadow-sm"
    >
      <div className="text-stone-500">
        显示 {startItem}-{endItem} / {totalCount} 个任务
      </div>
      <div className="flex items-center gap-2">
        <button
          type="button"
          onClick={() => onPageChange(Math.max(1, page - 1))}
          disabled={page === 1}
          className="ui-btn-secondary px-3 py-2 text-sm disabled:cursor-not-allowed disabled:opacity-50"
        >
          <ChevronLeft className="h-4 w-4" />
          上一页
        </button>
        <span className="min-w-20 text-center text-sm font-medium text-stone-700">
          第 {page} / {totalPages} 页
        </span>
        <button
          type="button"
          onClick={() => onPageChange(Math.min(totalPages, page + 1))}
          disabled={page === totalPages}
          className="ui-btn-secondary px-3 py-2 text-sm disabled:cursor-not-allowed disabled:opacity-50"
        >
          下一页
          <ChevronRight className="h-4 w-4" />
        </button>
        <input
          type="number"
          min={1}
          max={totalPages}
          value={jumpValue}
          onChange={(event) => setJumpValue(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter") {
              commitJump();
            }
          }}
          className="h-10 w-20 rounded-xl border border-stone-200 px-3 text-sm text-stone-700 focus:border-primary/40 focus:outline-none focus:ring-2 focus:ring-primary/20"
          aria-label="输入页码"
        />
        <button
          type="button"
          onClick={commitJump}
          className="ui-btn-secondary px-3 py-2 text-sm"
        >
          跳转
        </button>
      </div>
    </nav>
  );
};

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
  const { selectedIdentityId, selectedLlmProfileId } = useSelectionContext();
  const { notifyError, notifySuccess } = useNotification();
  const { confirm, dialog: confirmDialog } = useConfirmDialog();
  const hasTaskSelection =
    selectedIdentityId !== null && selectedLlmProfileId !== null;
  const [activeTab, setActiveTab] = useState<TasksTab>(() =>
    hasTaskSelection ? "batch" : "crawl",
  );
  const [taskListViews, setTaskListViews] = useState<TaskListViews>({
    batch: "current",
    crawl: "current",
    match: "current",
  });
  const [tasks, setTasks] = useState<BatchTaskCardDTO[]>([]);
  const [currentBatchTasks, setCurrentBatchTasks] = useState<BatchTaskCardDTO[]>([]);
  const [selectedBatchTask, setSelectedBatchTask] =
    useState<BatchTaskCardDTO | null>(null);
  const [selectedBatchTaskItems, setSelectedBatchTaskItems] = useState<
    BatchTaskItemDTO[]
  >([]);
  const [batchTaskDetailsLoading, setBatchTaskDetailsLoading] = useState(false);
  const [batchReviewItemId, setBatchReviewItemId] = useState<number | null>(null);
  const [batchReviewThread, setBatchReviewThread] =
    useState<WorkspaceThreadDTO | null>(null);
  const [batchReviewLoading, setBatchReviewLoading] = useState(false);
  const [batchReviewItemActions, setBatchReviewItemActions] =
    useState<BatchReviewItemActions>({});
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
  const [crawlJobs, setCrawlJobs] = useState<CrawlJobSummaryDTO[]>([]);
  const [currentCrawlJobs, setCurrentCrawlJobs] = useState<CrawlJobSummaryDTO[]>([]);
  const [crawlJobsLoading, setCrawlJobsLoading] = useState(false);
  const [batchPage, setBatchPage] = useState(1);
  const [matchPage, setMatchPage] = useState(1);
  const [crawlPage, setCrawlPage] = useState(1);
  const [crawlEventPage, setCrawlEventPage] = useState(1);
  const [crawlDetailPagePage, setCrawlDetailPagePage] = useState(1);
  const [crawlCandidatePage, setCrawlCandidatePage] = useState(1);
  const [selectedCrawlJob, setSelectedCrawlJob] =
    useState<CrawlJobSummaryDTO | null>(null);
  const [crawlJobPages, setCrawlJobPages] = useState<CrawlPageDTO[]>([]);
  const [crawlJobCandidates, setCrawlJobCandidates] = useState<
    CrawlCandidateDTO[]
  >([]);
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
  const [pausingCrawlJobId, setPausingCrawlJobId] = useState<number | null>(
    null,
  );
  const [resumingCrawlJobId, setResumingCrawlJobId] = useState<number | null>(
    null,
  );
  const [selectedCandidateDetail, setSelectedCandidateDetail] =
    useState<CrawlCandidateDTO | null>(null);
  const lastLoadErrorRef = useRef<string | null>(null);
  const lastBatchTaskDetailsLoadErrorRef = useRef<string | null>(null);
  const lastMatchJobsLoadErrorRef = useRef<string | null>(null);
  const lastMatchJobDetailsLoadErrorRef = useRef<string | null>(null);
  const lastCrawlJobsLoadErrorRef = useRef<string | null>(null);
  const lastCrawlJobDetailsLoadErrorRef = useRef<string | null>(null);
  const loadedTasksKeyRef = useRef<string | null>(null);
  const crawlJobsPreloadedRef = useRef(false);
  const matchJobsPreloadedKeyRef = useRef<string | null>(null);
  const activeTasksRequestKeyRef = useRef<string | null>(null);
  const latestTasksRequestIdRef = useRef(0);
  const latestBatchTaskDetailsRequestIdRef = useRef(0);
  const latestBatchReviewRequestIdRef = useRef(0);
  const latestMatchJobsRequestIdRef = useRef(0);
  const latestMatchJobDetailsRequestIdRef = useRef(0);
  const latestCrawlJobsRequestIdRef = useRef(0);
  const latestCrawlJobDetailsRequestIdRef = useRef(0);
  const activeTaskListView = taskListViews[activeTab];
  const tasksRequestKey =
    selectedIdentityId && selectedLlmProfileId
      ? `${selectedIdentityId}:${selectedLlmProfileId}:${taskListViews.batch}`
      : null;
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
  const totalRunningCount =
    batchRunningCount + crawlRunningCount + matchRunningCount;
  const totalAttentionCount =
    batchAttentionCount + crawlReviewCount + matchAttentionCount;
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
      selectedBatchTaskItems.filter((item) => item.status === "generating_draft"),
    [selectedBatchTaskItems],
  );
  const draftFailedBatchTaskItems = useMemo(
    () => selectedBatchTaskItems.filter((item) => item.status === "draft_failed"),
    [selectedBatchTaskItems],
  );
  const failedBatchTaskItems = useMemo(
    () =>
      selectedBatchTaskItems.filter((item) => item.status === "send_failed"),
    [selectedBatchTaskItems],
  );
  const reviewRequiredBatchTaskItems = useMemo(
    () => selectedBatchTaskItems.filter((item) => item.status === "review_required"),
    [selectedBatchTaskItems],
  );
  const batchReviewQueueItems = useMemo(
    () =>
      selectedBatchTaskItems.filter(
        (item) =>
          item.status === "review_required" ||
          item.status === "generating_draft",
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
  const selectedBatchNeedsManualItems = selectedBatchTaskItems.filter(
    (item) =>
      item.next_action === "complete_professor_profile" ||
      item.next_action === "select_primary_material" ||
      item.next_action === "review_draft" ||
      item.next_action === "missing_schedule" ||
      item.next_action === "retry_draft_generation",
  );
  const visibleBatchTasks = useMemo(
    () => getPageItems(tasks, batchPage, TASKS_PAGE_SIZE),
    [batchPage, tasks],
  );
  const visibleCrawlJobs = useMemo(
    () => getPageItems(crawlJobs, crawlPage, TASKS_PAGE_SIZE),
    [crawlJobs, crawlPage],
  );
  const visibleMatchJobs = useMemo(
    () => getPageItems(matchAnalysisJobs, matchPage, TASKS_PAGE_SIZE),
    [matchAnalysisJobs, matchPage],
  );
  const visibleCrawlJobEvents = useMemo(
    () =>
      getPageItems(crawlJobEvents, crawlEventPage, MONITOR_SECTION_PAGE_SIZE),
    [crawlEventPage, crawlJobEvents],
  );
  const visibleCrawlJobPages = useMemo(
    () =>
      getPageItems(
        crawlJobPages,
        crawlDetailPagePage,
        MONITOR_SECTION_PAGE_SIZE,
      ),
    [crawlDetailPagePage, crawlJobPages],
  );
  const visibleCrawlJobCandidates = useMemo(
    () =>
      getPageItems(
        crawlJobCandidates,
        crawlCandidatePage,
        MONITOR_SECTION_PAGE_SIZE,
      ),
    [crawlCandidatePage, crawlJobCandidates],
  );
  const selectedCrawlJobId = selectedCrawlJob?.id ?? null;
const selectedCrawlJobCanReview =
  selectedCrawlJob?.status === "needs_review" ||
  selectedCrawlJob?.status === "partially_completed" ||
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
  const allReviewableCrawlCandidatesSelected = useMemo(
    () =>
      reviewableCrawlCandidateIds.length > 0 &&
      reviewableCrawlCandidateIds.every((candidateId) =>
        selectedReviewableCrawlCandidateIds.includes(candidateId),
      ),
    [reviewableCrawlCandidateIds, selectedReviewableCrawlCandidateIds],
  );
  const allReviewableCrawlCandidatesWithoutEmailSelected = useMemo(
    () =>
      reviewableCrawlCandidateIdsWithoutEmail.length > 0 &&
      reviewableCrawlCandidateIdsWithoutEmail.every((candidateId) =>
        selectedReviewableCrawlCandidateIds.includes(candidateId),
      ),
    [
      reviewableCrawlCandidateIdsWithoutEmail,
      selectedReviewableCrawlCandidateIds,
    ],
  );

  useEffect(() => {
    if (hasTaskSelection || activeTab === "crawl") {
      return;
    }
    setActiveTab("crawl");
  }, [activeTab, hasTaskSelection]);

  useEffect(() => {
    setBatchPage(1);
  }, [taskListViews.batch]);

  useEffect(() => {
    setCrawlPage(1);
  }, [taskListViews.crawl]);

  useEffect(() => {
    setMatchPage(1);
  }, [taskListViews.match]);

  const loadTasks = useCallback(async () => {
    if (!tasksRequestKey || !selectedIdentityId || !selectedLlmProfileId) {
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
    if (!selectedIdentityId || !selectedLlmProfileId) {
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
      Math.min(currentPage, getTotalPages(tasks.length, TASKS_PAGE_SIZE)),
    );
  }, [tasks.length]);

  useEffect(() => {
    setCrawlPage((currentPage) =>
      Math.min(currentPage, getTotalPages(crawlJobs.length, TASKS_PAGE_SIZE)),
    );
  }, [crawlJobs.length]);

  useEffect(() => {
    setCrawlEventPage((currentPage) =>
      Math.min(
        currentPage,
        getTotalPages(crawlJobEvents.length, MONITOR_SECTION_PAGE_SIZE),
      ),
    );
  }, [crawlJobEvents.length]);

  useEffect(() => {
    setCrawlDetailPagePage((currentPage) =>
      Math.min(
        currentPage,
        getTotalPages(crawlJobPages.length, MONITOR_SECTION_PAGE_SIZE),
      ),
    );
  }, [crawlJobPages.length]);

  useEffect(() => {
    setCrawlCandidatePage((currentPage) =>
      Math.min(
        currentPage,
        getTotalPages(crawlJobCandidates.length, MONITOR_SECTION_PAGE_SIZE),
      ),
    );
  }, [crawlJobCandidates.length]);

  useEffect(() => {
    if (crawlJobsPreloadedRef.current) {
      return;
    }
    crawlJobsPreloadedRef.current = true;
    void loadCrawlJobs({ showLoading: false });
  }, [loadCrawlJobs]);

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
    setSelectedCrawlCandidateIds([]);
    setCrawlJobApproveLoading(false);
    setCrawlJobEnrichLoading(false);
    setResumingCrawlJobReviewId(null);
    setSelectedCandidateDetail(null);
    setCrawlEventPage(1);
    setCrawlDetailPagePage(1);
    setCrawlCandidatePage(1);
  }, [selectedCrawlJobId]);

  useEffect(() => {
    if (!selectedCandidateDetail) {
      return;
    }
    const originalOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.body.style.overflow = originalOverflow;
    };
  }, [selectedCandidateDetail]);

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
          title: "确认中止这个任务？",
          description: "中止后当前批次不会继续推进生成、排程和发送。",
          confirmLabel: "确认中止",
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

  const handleResumeCrawlJob = async (jobId: number) => {
    const diagnosticData = { jobId };
    safeRecordUserAction({
      eventName: "tasks.crawl_job_resume_submitted",
      data: diagnosticData,
    });
    setResumingCrawlJobId(jobId);
    try {
      await resumeCrawlJob(jobId);
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
      await retryCrawlJob(jobId, { clear_existing_data: true });
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

    setCrawlJobEnrichLoading(true);
    try {
      const result = await enrichCrawlCandidates(
        selectedCrawlJobId,
        selectedReviewableCrawlCandidateIds,
      );
      notifySuccess("候选信息补全完成", result.message);
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

  const closeCrawlJobDetails = () => {
    latestCrawlJobDetailsRequestIdRef.current += 1;
    setSelectedCrawlJob(null);
    setCrawlJobPages([]);
    setCrawlJobCandidates([]);
    setCrawlJobEvents([]);
    setSelectedCrawlCandidateIds([]);
    setCrawlJobApproveLoading(false);
    setSelectedCandidateDetail(null);
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

    const requestId = latestBatchReviewRequestIdRef.current + 1;
    latestBatchReviewRequestIdRef.current = requestId;
    setBatchReviewItemId(item.id);
    setBatchReviewThread(null);
    setBatchReviewLoading(true);
    try {
      const thread = await getBatchTaskItemThread(selectedBatchTask.id, item.id);
      if (latestBatchReviewRequestIdRef.current !== requestId) {
        return;
      }
      ensureBatchReviewThreadMatchesItem(thread, item, selectedBatchTask);
      syncBatchDraftReview(thread);
    } catch (actionError) {
      if (latestBatchReviewRequestIdRef.current !== requestId) {
        return;
      }
      const message =
        actionError instanceof Error ? actionError.message : "加载草稿失败";
      notifyError("加载草稿失败", message);
      setBatchReviewItemId(null);
      setBatchReviewThread(null);
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
    const confirmed = await confirm({
      title: "确认重新生成草稿？",
      description: "重新生成后会覆盖当前草稿内容，原草稿将无法保留。",
      confirmLabel: "确认重新生成",
      cancelLabel: "先不重新生成",
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
      notifySuccess("草稿已重新生成");
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
    const confirmed = await confirm({
      title: "确认立即发送这封真实邮件？",
      description: `将真实发给 ${
        batchReviewThread?.professor.email ?? "当前导师邮箱"
      }，并附带 ${batchReviewSelectedMaterialIds.length} 份附件。`,
      confirmLabel: "确认发送",
      cancelLabel: "再检查一下",
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
      notifySuccess("邮件已提交发送");
      setSelectedBatchTaskItems((current) =>
        current.map((item) =>
          item.id === activeBatchReviewItem.id
            ? { ...item, status: "sent", next_action: null }
            : item,
        ),
      );
      await Promise.all([loadBatchTaskDetails(selectedBatchTask.id), loadTasks()]);
      resetBatchDraftReview();
    } catch (actionError) {
      const message =
        actionError instanceof Error ? actionError.message : "发送邮件失败";
      notifyError("发送邮件失败", message);
    } finally {
      clearBatchReviewItemAction(itemId, "submit");
    }
  };

  const renderBatchTaskItemAction = (item: BatchTaskItemDTO) => {
    if (!selectedBatchTask) {
      return null;
    }
    const action = buildBatchPendingItemAction(item, selectedBatchTask);
    if (action?.kind === "message") {
      return (
        <span className="font-medium text-stone-600">
          {action.text}
        </span>
      );
    }
    if (action?.kind === "review") {
      return (
        <button
          type="button"
          onClick={() => void openBatchDraftReview(item)}
          className="font-medium text-primary"
        >
          {action.text}
        </button>
      );
    }
    if (action?.kind === "professor" || action?.kind === "profile") {
      return (
        <Link
          to={action.href}
          className="font-medium text-primary"
        >
          {action.text}
        </Link>
      );
    }
    if (action?.kind === "retry") {
      return (
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
    return null;
  };

  const closeBatchTaskDetails = () => {
    latestBatchTaskDetailsRequestIdRef.current += 1;
    resetBatchDraftReview();
    setSelectedBatchTask(null);
    setSelectedBatchTaskItems([]);
    setBatchTaskDetailsLoading(false);
    lastBatchTaskDetailsLoadErrorRef.current = null;
  };

  const closeMatchJobDetails = () => {
    latestMatchJobDetailsRequestIdRef.current += 1;
    setSelectedMatchJob(null);
    setSelectedMatchJobItems([]);
    setMatchJobDetailsLoading(false);
    lastMatchJobDetailsLoadErrorRef.current = null;
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
            还没有选择身份和模型，批量邮件与匹配分析会在配置后显示；教师抓取任务可继续查看。
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
        <div className="inline-flex gap-2 rounded-2xl border border-stone-200 bg-white p-1.5 shadow-sm">
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
        </div>

        <TaskListViewSwitch
          activeView={activeTaskListView}
          onViewChange={(view) =>
            setTaskListViews((current) => ({ ...current, [activeTab]: view }))
          }
        />
      </div>

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
                          中止
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
          <TaskListPagination
            page={batchPage}
            totalCount={tasks.length}
            onPageChange={setBatchPage}
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
                <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_260px_auto] lg:items-center">
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
                  </div>
                  <div className="flex flex-wrap items-center gap-2 lg:justify-end">
                    <span className="rounded-full bg-stone-50 px-2.5 py-1 text-xs text-stone-600">
                      Token {job.total_tokens.toLocaleString("zh-CN")}
                    </span>
                    <span className="rounded-full bg-stone-50 px-2.5 py-1 text-xs text-stone-600">
                      更新 {formatDisplayTime(job.updated_at, { withSeconds: true })}
                    </span>
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
                      aria-label="查看详情"
                      title="查看详情"
                    >
                      <ChevronRight className="h-4 w-4" />
                    </button>
                  </div>
                </div>
              </article>
            ))}
          </div>
          <TaskListPagination
            page={matchPage}
            totalCount={matchAnalysisJobs.length}
            onPageChange={setMatchPage}
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
          <TaskListPagination
            page={crawlPage}
            totalCount={crawlJobs.length}
            onPageChange={setCrawlPage}
          />
        </>
      )}
      {selectedBatchTask ? (
        <div
          className="fixed inset-0 z-50 flex items-stretch justify-end bg-stone-950/30 p-0 sm:p-6"
          onClick={closeBatchTaskDetails}
        >
          <section
            role="dialog"
            aria-label="批量任务详情"
            className={
              batchDraftReviewOpen
                ? "flex h-full w-full flex-col overflow-hidden bg-white shadow-xl sm:max-w-7xl sm:rounded-3xl"
                : "flex h-full w-full flex-col overflow-hidden bg-white shadow-xl sm:max-w-4xl sm:rounded-3xl"
            }
            onClick={(event) => event.stopPropagation()}
          >
            <div className="flex items-start justify-between gap-4 border-b border-stone-200 bg-[#fcfbf8] px-6 py-5">
              <div>
                <div className="flex items-center gap-2 text-xs font-medium text-stone-500">
                  <Mail className="h-4 w-4 text-primary" />
                  {batchDraftReviewOpen ? "批量草稿审核" : "批量邮件任务"}
                </div>
                <h2 className="mt-2 text-xl font-semibold text-stone-900">
                  {batchDraftReviewOpen ? "批量审核草稿" : selectedBatchTask.name}
                </h2>
                <p className="mt-2 text-sm text-stone-500">
                  {batchDraftReviewOpen
                    ? `${selectedBatchTask.name} · ${activeBatchReviewItem?.professor_name ?? "正在加载"}`
                    : buildScheduleLabel(selectedBatchTask)}
                </p>
              </div>
              <div className="flex shrink-0 flex-wrap gap-2">
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

            <div className="flex-1 overflow-y-auto px-6 py-5">
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
                      {batchReviewLoading ? (
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
                              label="邮件主题"
                              value={batchReviewSubject}
                              onChange={setBatchReviewSubject}
                              placeholder="给老师的邮件主题"
                            />
                            <EmailTemplateEditor
                              label="邮件正文"
                              html={batchReviewEditorHtml}
                              onChange={handleBatchReviewContentChange}
                            />
                          </div>
                        </div>

                        <aside className="space-y-4">
                          <div className="rounded-2xl border border-stone-100 bg-stone-50/70 px-4 py-3">
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
                          </div>

                          <div className="rounded-2xl border border-stone-100 bg-stone-50/70 px-4 py-3">
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
                                          {MATERIAL_TYPE_LABELS[material.material_type]}
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
                          </div>

                          <div className="rounded-2xl border border-stone-100 bg-white px-4 py-3">
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
                                <RotateCcw className="h-4 w-4" />
                                重新生成
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
                          </div>
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

                <div className="mt-3 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
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
                </div>
              </section>

              <section className="mt-6">
                <h3 className="text-sm font-semibold text-stone-900">
                  已发送给
                </h3>
                <div className="mt-3 space-y-2">
                  {sentBatchTaskItems.length > 0 ? (
                    sentBatchTaskItems.map((item) => (
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
              </section>

              <section className="mt-6">
                <h3 className="text-sm font-semibold text-stone-900">
                  还未发送给
                </h3>
                {selectedBatchTask.schedule_type === "scheduled" && selectedBatchWaitingSendCount > 0 ? (
                  <p className="mt-2 rounded-2xl border border-primary/15 bg-primary/5 px-4 py-3 text-sm leading-6 text-stone-700">
                    已通过的模板邮件会按批量任务的日期、时间窗口和每日数量自动发送，不需要逐封手动设定发送时间。
                  </p>
                ) : null}
                {selectedBatchTask.review_required_count > 0 ? (
                  <p className="mt-2 rounded-2xl border border-amber-100 bg-amber-50 px-4 py-3 text-sm leading-6 text-amber-800">
                    AI 改写草稿生成后仍需逐封审核通过，审核后才会进入发送或定时发送流程。
                  </p>
                ) : null}
                <div className="mt-3 space-y-2">
                  {pendingBatchTaskItems.length > 0 ? (
                    pendingBatchTaskItems.map((item) => {
                      const cancellationText = getBatchTaskItemCancellationText(item);
                      return (
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
                            {item.scheduled_at ? (
                              <span>
                                计划发送 {formatDisplayTime(item.scheduled_at)}
                              </span>
                            ) : null}
                            {cancellationText ? (
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
                          <span className="rounded-full bg-sky-100 px-2.5 py-1 text-xs text-sky-700">
                            {PROFESSOR_STATUS_LABELS[item.status]}
                          </span>
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
                          <div className="text-xs">
                            {renderBatchTaskItemAction(item)}
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
                        <div className="flex flex-wrap items-start justify-between gap-3">
                          <div>
                            <p className="text-sm font-medium text-stone-900">
                              {item.professor_name}
                            </p>
                            <p className="mt-1 text-xs text-red-700">
                              {item.last_error || "暂无失败原因"}
                            </p>
                          </div>
                          <div className="text-xs">
                            {renderBatchTaskItemAction(item)}
                          </div>
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
          onClick={closeMatchJobDetails}
        >
          <section
            role="dialog"
            aria-label="匹配分析任务详情"
            className="flex h-full w-full flex-col overflow-hidden bg-white shadow-xl sm:max-w-4xl sm:rounded-3xl"
            onClick={(event) => event.stopPropagation()}
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

            <div className="flex-1 overflow-y-auto px-6 py-5">
              <div className="grid gap-3 sm:grid-cols-4">
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
                <div className="rounded-2xl border border-stone-100 bg-white px-4 py-3">
                  <div className="text-xs font-medium text-stone-500">总 Token</div>
                  <div className="mt-2 text-sm font-semibold text-stone-900">
                    {selectedMatchJob.total_tokens.toLocaleString("zh-CN")}
                  </div>
                </div>
              </div>

              <section className="mt-6">
                <div className="flex items-center justify-between gap-3">
                  <h3 className="text-sm font-semibold text-stone-900">
                    导师明细
                  </h3>
                  {matchJobDetailsLoading ? (
                    <span className="inline-flex items-center gap-2 text-xs text-stone-500">
                      <Loader2 className="h-3.5 w-3.5 animate-spin" />
                      正在刷新
                    </span>
                  ) : null}
                </div>

                <div className="mt-3 overflow-hidden rounded-2xl border border-stone-200">
                  <table className="min-w-full divide-y divide-stone-200 text-sm">
                    <thead className="bg-stone-50 text-left text-xs font-medium text-stone-500">
                      <tr>
                        <th className="px-4 py-3">导师</th>
                        <th className="px-4 py-3">状态</th>
                        <th className="px-4 py-3">匹配分</th>
                        <th className="px-4 py-3">说明</th>
                        <th className="px-4 py-3">Token</th>
                        <th className="px-4 py-3">更新时间</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-stone-100 bg-white text-stone-700">
                      {selectedMatchJobItems.length > 0 ? (
                        selectedMatchJobItems.map((item) => (
                          <tr key={item.id}>
                            <td className="px-4 py-3 align-top">
                              <div className="font-medium text-stone-900">
                                {item.professor_name}
                              </div>
                              <div className="mt-1 text-xs text-stone-500">
                                {[item.professor_title, item.professor_school]
                                  .filter(Boolean)
                                  .join(" / ") || "暂无补充信息"}
                              </div>
                            </td>
                            <td className="px-4 py-3 align-top">
                              <span
                                className={`rounded-full border px-2.5 py-1 text-xs font-medium ${MATCH_ANALYSIS_JOB_STATUS_TONES[item.status === "succeeded" ? "completed" : item.status === "skipped" ? "canceled" : item.status === "running" ? "running" : item.status === "queued" ? "queued" : "failed"]}`}
                              >
                                {item.status}
                              </span>
                            </td>
                            <td className="px-4 py-3 align-top">
                              {item.match_score ?? "未生成"}
                            </td>
                            <td className="px-4 py-3 align-top">
                              {item.error_message || item.skip_reason || "已完成"}
                            </td>
                            <td className="px-4 py-3 align-top">
                              {item.total_tokens.toLocaleString("zh-CN")}
                            </td>
                            <td className="px-4 py-3 align-top">
                              {formatDisplayTime(item.updated_at, { withSeconds: true })}
                            </td>
                          </tr>
                        ))
                      ) : (
                        <tr>
                          <td
                            colSpan={6}
                            className="px-4 py-6 text-center text-sm text-stone-500"
                          >
                            暂无任务明细。
                          </td>
                        </tr>
                      )}
                    </tbody>
                  </table>
                </div>
              </section>
            </div>
          </section>
        </div>
      ) : null}
      {selectedCrawlJob ? (
        <div
          className="fixed inset-0 z-50 flex items-stretch justify-center bg-stone-950/30 p-0 sm:p-6"
          onClick={closeCrawlJobDetails}
        >
          <section
            role="dialog"
            aria-label="抓取任务详情"
            className="flex h-full w-full flex-col overflow-hidden bg-white shadow-xl sm:max-w-[min(94vw,1280px)] sm:rounded-3xl"
            onClick={(event) => event.stopPropagation()}
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

            <div className="flex-1 space-y-6 overflow-y-auto px-6 py-5">
              <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4 2xl:grid-cols-7">
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
                <section className="flex h-full flex-col">
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
                  <TaskListPagination
                    page={crawlEventPage}
                    totalCount={crawlJobEvents.length}
                    onPageChange={setCrawlEventPage}
                    pageSize={MONITOR_SECTION_PAGE_SIZE}
                  />
                </section>

                <section className="flex h-full flex-col">
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
                  <TaskListPagination
                    page={crawlDetailPagePage}
                    totalCount={crawlJobPages.length}
                    onPageChange={setCrawlDetailPagePage}
                    pageSize={MONITOR_SECTION_PAGE_SIZE}
                  />
                </section>
              </div>

              <section>
                <h3 className="flex items-center gap-2 text-sm font-semibold text-stone-900">
                  <CheckCircle2 className="h-4 w-4 text-emerald-600" />
                  候选导师
                </h3>
                <div className="mt-3 space-y-2">
                  {selectedCrawlJobCanReview ? (
                    <div className="rounded-2xl border border-amber-200 bg-amber-50/70 px-4 py-3">
                      <div className="flex flex-wrap items-center justify-between gap-3">
                        <div className="text-sm text-amber-900">
                          可导入 {reviewableCrawlCandidateIds.length} 位，已选{" "}
                          {selectedReviewableCrawlCandidateIds.length} 位
                          <span className="mt-1 block text-xs text-amber-700">
                            无邮箱 {reviewableCrawlCandidateIdsWithoutEmail.length}{" "}
                            位，可反复选择候选补全缺失信息，补全后仍可继续审核。
                          </span>
                        </div>
                        <div className="flex flex-wrap gap-2">
                          <button
                            type="button"
                            onClick={() =>
                              setSelectedCrawlCandidateIds(
                                reviewableCrawlCandidateIds,
                              )
                            }
                            disabled={
                              reviewableCrawlCandidateIds.length === 0 ||
                              allReviewableCrawlCandidatesSelected ||
                              crawlJobApproveLoading ||
                              crawlJobEnrichLoading
                            }
                            className="ui-btn-secondary px-3 py-2 text-sm disabled:cursor-not-allowed disabled:opacity-60"
                          >
                            全选可导入
                          </button>
                          <button
                            type="button"
                            onClick={() =>
                              setSelectedCrawlCandidateIds(
                                reviewableCrawlCandidateIdsWithoutEmail,
                              )
                            }
                            disabled={
                              reviewableCrawlCandidateIdsWithoutEmail.length ===
                                0 ||
                              allReviewableCrawlCandidatesWithoutEmailSelected ||
                              crawlJobApproveLoading ||
                              crawlJobEnrichLoading
                            }
                            className="ui-btn-secondary px-3 py-2 text-sm disabled:cursor-not-allowed disabled:opacity-60"
                          >
                            全选无邮箱
                          </button>
                          <button
                            type="button"
                            onClick={() => setSelectedCrawlCandidateIds([])}
                            disabled={
                              selectedReviewableCrawlCandidateIds.length ===
                                0 ||
                              crawlJobApproveLoading ||
                              crawlJobEnrichLoading
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
                              selectedReviewableCrawlCandidateIds.length ===
                                0 ||
                              crawlJobApproveLoading ||
                              crawlJobEnrichLoading
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
                              selectedReviewableCrawlCandidateIds.length ===
                                0 ||
                              crawlJobApproveLoading ||
                              crawlJobEnrichLoading
                            }
                            className="ui-btn-primary px-3 py-2 text-sm disabled:cursor-not-allowed disabled:opacity-60"
                          >
                            {crawlJobApproveLoading
                              ? "导入中..."
                              : "审核通过并导入"}
                          </button>
                        </div>
                      </div>
                    </div>
                  ) : null}
                  {crawlJobCandidates.length > 0 ? (
                    visibleCrawlJobCandidates.map((candidate) => (
                      <div
                        key={candidate.id}
                        className="rounded-2xl border border-stone-100 px-4 py-3"
                      >
                        <div className="flex flex-wrap items-start justify-between gap-3">
                          <div className="flex min-w-0 items-center gap-3">
                            {selectedCrawlJobCanReview ? (
                              <input
                                type="checkbox"
                                checked={selectedReviewableCrawlCandidateIds.includes(
                                  candidate.id,
                                )}
                                disabled={
                                  crawlJobApproveLoading ||
                                  crawlJobEnrichLoading
                                }
                                onChange={() =>
                                  handleToggleCrawlCandidateSelection(
                                    candidate.id,
                                  )
                                }
                                aria-label={`选择候选导师 ${candidate.name}`}
                                className="h-4 w-4 rounded border-stone-300 text-primary focus:ring-primary/30"
                              />
                            ) : null}
                            <div className="min-w-0">
                              <p className="text-sm font-medium text-stone-800">
                                {candidate.name}
                              </p>
                              <p className="mt-1 text-sm text-stone-500">
                                {candidate.email ?? "暂无邮箱"}
                              </p>
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
                            <button
                              type="button"
                              onClick={() =>
                                setSelectedCandidateDetail(candidate)
                              }
                              className="ui-btn-secondary px-3 py-2 text-sm"
                            >
                              查看详情
                            </button>
                          </div>
                        </div>
                      </div>
                    ))
                  ) : (
                    <p className="rounded-2xl border border-dashed border-stone-200 px-4 py-3 text-sm text-stone-500">
                      暂无候选导师。
                    </p>
                  )}
                </div>
                <TaskListPagination
                  page={crawlCandidatePage}
                  totalCount={crawlJobCandidates.length}
                  onPageChange={setCrawlCandidatePage}
                  pageSize={MONITOR_SECTION_PAGE_SIZE}
                />
              </section>
            </div>
          </section>
        </div>
      ) : null}
      {selectedCandidateDetail ? (
        <div
          className="fixed inset-0 z-[60] flex items-center justify-center bg-stone-950/35 p-4"
          onClick={() => setSelectedCandidateDetail(null)}
        >
          <section
            role="dialog"
            aria-label="候选导师详情"
            className="flex max-h-[90vh] w-full max-w-3xl flex-col overflow-hidden rounded-3xl bg-white shadow-2xl"
            onClick={(event) => event.stopPropagation()}
          >
            <div className="flex items-start justify-between gap-4 border-b border-stone-200 px-6 py-5">
              <div>
                <p className="text-xs font-medium uppercase tracking-[0.18em] text-stone-400">
                  候选导师详情
                </p>
                <h3 className="mt-2 text-xl font-semibold text-stone-900">
                  {selectedCandidateDetail.name}
                </h3>
                <p className="mt-1 text-sm text-stone-500">
                  {selectedCandidateDetail.email ?? "暂无邮箱"}
                </p>
              </div>
              <button
                type="button"
                onClick={() => setSelectedCandidateDetail(null)}
                className="ui-btn-secondary shrink-0"
                aria-label="关闭候选导师详情"
              >
                <X className="h-4 w-4" />
                关闭
              </button>
            </div>
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
                    {selectedCandidateDetail.profile_url || "暂无"}
                  </div>
                  <div>
                    <span className="text-stone-500">来源页：</span>
                    {selectedCandidateDetail.source_url || "暂无"}
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
          </section>
        </div>
      ) : null}
      {confirmDialog}
    </main>
  );
};
