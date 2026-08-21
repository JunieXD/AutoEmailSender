import {
  Activity,
  Bot,
  CheckCircle2,
  ChevronRight,
  Pause,
  Play,
  RotateCcw,
  Square,
  Trash2,
} from "lucide-react";

import type {
  CrawlJobSummaryDTO,
  TaskListView,
} from "@/types";
import {
  CRAWL_JOB_STATUS_LABELS,
  CRAWL_JOB_STATUS_TONES,
  canDeleteCrawlJob,
} from "../model/taskCenterJobs";

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

export const TokenUsageBreakdown = ({
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

type TaskListViewSwitchProps = {
  activeView: TaskListView;
  onViewChange: (view: TaskListView) => void;
};

export const TaskListViewSwitch = ({
  activeView,
  onViewChange,
}: TaskListViewSwitchProps) => (
  <div
    role="group"
    aria-label="任务范围"
    data-testid="task-list-view-switch"
    className="flex justify-end"
  >
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
            <p className="mt-1 truncate text-sm text-stone-500" title={job.start_url}>
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
          <button type="button" onClick={() => onRestore(job.id)} className="ui-btn-primary">
            <RotateCcw className="h-4 w-4" />
            还原任务
          </button>
        ) : null}
        {listView === "current" && canDeleteCrawlJob(job) ? (
          <button type="button" onClick={() => onDelete(job)} className="ui-btn-danger">
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
              {pausingCrawlJobId === job.id ? "暂停中…" : "暂停抓取"}
            </button>
            <button type="button" onClick={() => onCancel(job.id)} className="ui-btn-danger">
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
              {resumingCrawlJobId === job.id ? "继续中…" : "继续抓取"}
            </button>
            <button type="button" onClick={() => onCancel(job.id)} className="ui-btn-danger">
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
              {retryingCrawlJobId === job.id ? "重新抓取中…" : "重新抓取"}
            </button>
            <button
              type="button"
              onClick={() => onResumeReview(job.id)}
              disabled={resumingCrawlJobReviewId === job.id}
              className="ui-btn-secondary disabled:cursor-not-allowed disabled:opacity-60"
            >
              <CheckCircle2 className="h-4 w-4" />
              {resumingCrawlJobReviewId === job.id ? "转入中…" : "转入待审核"}
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
