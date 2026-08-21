import type {
  BatchTaskCardDTO,
  CrawlJobStatusDTO,
  CrawlJobSummaryDTO,
  MatchAnalysisJobDTO,
  ProfessorInformationEnrichmentJobDTO,
  TaskListView,
} from "@/types";


export const CRAWL_JOB_STATUS_LABELS: Record<CrawlJobStatusDTO, string> = {
  queued: "排队中",
  running: "运行中",
  paused: "已暂停",
  needs_review: "待审核",
  partially_completed: "部分已导入",
  completed: "已完成",
  failed: "失败",
  canceled: "已取消",
};

export const CRAWL_JOB_STATUS_TONES: Record<CrawlJobStatusDTO, string> = {
  queued: "border-sky-200 bg-sky-50 text-sky-700",
  running: "border-primary/20 bg-primary/10 text-primary",
  paused: "border-orange-200 bg-orange-50 text-orange-700",
  needs_review: "border-amber-200 bg-amber-50 text-amber-700",
  partially_completed: "border-blue-200 bg-blue-50 text-blue-700",
  completed: "border-emerald-200 bg-emerald-50 text-emerald-700",
  failed: "border-red-200 bg-red-50 text-red-700",
  canceled: "border-stone-200 bg-stone-100 text-stone-600",
};

export const canDeleteCrawlJob = (job: CrawlJobSummaryDTO) =>
  job.status === "needs_review" ||
  job.status === "partially_completed" ||
  job.status === "completed" ||
  job.status === "failed" ||
  job.status === "canceled";

export const canDeleteBatchTask = (task: BatchTaskCardDTO) =>
  task.status === "stopped" ||
  task.status === "completed" ||
  task.status === "expired";

export const canOpenBatchResend = (
  task: BatchTaskCardDTO,
  view: TaskListView,
) =>
  view === "current" && ["expired", "stopped", "completed"].includes(task.status);

export const canDeleteMatchJob = (job: MatchAnalysisJobDTO) =>
  job.status === "completed" ||
  job.status === "partial_failed" ||
  job.status === "failed" ||
  job.status === "canceled";

export const canDeleteInformationEnrichmentJob = (
  job: ProfessorInformationEnrichmentJobDTO,
) =>
  job.status === "partially_completed" ||
  job.status === "completed" ||
  job.status === "failed" ||
  job.status === "canceled";
