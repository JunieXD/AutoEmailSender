/* eslint-disable react-refresh/only-export-components */

import {
  createContext,
  type PropsWithChildren,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { useNotification } from "@/context/NotificationContext";
import {
  getCrawlEnrichmentOperationId,
  getCrawlEventRawPayload,
  isCrawlEnrichmentCompletionEvent,
} from "@/features/crawl-review/client/crawlJobEvents";
import { getCrawlJob, getCrawlJobEvents } from "@/lib/api/crawlJobsApi";
import { getMatchAnalysisJob } from "@/lib/api/matchAnalysisJobsApi";
import {
  getProfessorInformationEnrichmentJob,
  listProfessorInformationEnrichmentItems,
} from "@/entities/professor/api/informationEnrichment";
import type {
  CrawlJobDTO,
  CrawlJobEventDTO,
  CrawlJobSummaryDTO,
  MatchAnalysisJobDTO,
  ProfessorInformationEnrichmentItemDTO,
  ProfessorInformationEnrichmentJobDTO,
} from "@/types";

type TrackInformationEnrichmentOptions = {
  professorName?: string;
};

type TrackedInformationEnrichment = {
  key: string;
  kind: "information_enrichment";
  jobId: number;
  professorName?: string;
};

type TrackedMatchAnalysis = {
  key: string;
  kind: "match_analysis";
  jobId: number;
};

type TrackedCrawlJob = {
  key: string;
  kind: "crawl_job";
  jobId: number;
};

type TrackedCrawlCandidateEnrichment = {
  key: string;
  kind: "crawl_candidate_enrichment";
  jobId: number;
  operationId: string;
};

type TrackedBackgroundTask =
  | TrackedInformationEnrichment
  | TrackedMatchAnalysis
  | TrackedCrawlJob
  | TrackedCrawlCandidateEnrichment;

type BackgroundTaskNotificationContextValue = {
  trackInformationEnrichmentJob: (
    job: ProfessorInformationEnrichmentJobDTO,
    options?: TrackInformationEnrichmentOptions,
  ) => void;
  stopTrackingInformationEnrichmentJob: (jobId: number) => void;
  trackMatchAnalysisJob: (job: MatchAnalysisJobDTO) => void;
  trackCrawlJob: (job: CrawlJobDTO) => void;
  trackCrawlCandidateEnrichment: (
    jobId: number,
    operationId: string,
  ) => void;
};

type ResultNotification = {
  level: "success" | "warning" | "error";
  title: string;
  description: string;
};

const BackgroundTaskNotificationContext =
  createContext<BackgroundTaskNotificationContextValue | null>(null);

const POLL_INTERVAL_MS = 2500;
const activeInformationEnrichmentStatuses = new Set(["queued", "running"]);
const activeMatchAnalysisStatuses = new Set(["queued", "running"]);
const activeCrawlJobStatuses = new Set(["queued", "running", "paused"]);
const informationEnrichmentFieldLabels: Record<string, string> = {
  email: "邮箱",
  title: "职称",
  department: "系所",
  research_direction: "研究方向",
  recent_papers: "近期论文",
};

const getTaskKey = (kind: TrackedBackgroundTask["kind"], jobId: number) =>
  `${kind}:${jobId}`;

const appendLastError = (description: string, lastError: string | null) =>
  lastError ? `${description} ${lastError}` : description;

const buildSingleInformationEnrichmentResult = (
  tracked: TrackedInformationEnrichment,
  job: ProfessorInformationEnrichmentJobDTO,
  items: ProfessorInformationEnrichmentItemDTO[] | null,
): ResultNotification => {
  const item = items?.[0];
  const professorName =
    tracked.professorName ?? item?.professor_name ?? job.name;

  if (item?.status === "failed" || job.status === "failed") {
    return {
      level: "error",
      title: `补全失败：${professorName}`,
      description: item?.error_message ?? job.last_error ?? "信息补全失败。",
    };
  }
  if (item?.status === "canceled" || job.status === "canceled") {
    return {
      level: "warning",
      title: `补全已取消：${professorName}`,
      description: item?.error_message ?? "本次信息补全已取消。",
    };
  }
  if (item?.status === "skipped") {
    return {
      level: "warning",
      title: `补全已跳过：${professorName}`,
      description: item.skip_reason ?? "当前导师不满足信息补全条件。",
    };
  }
  if (job.status === "partially_completed") {
    return {
      level: "warning",
      title: `补全部分完成：${professorName}`,
      description: job.last_error ?? "部分信息未能完成补全，请在任务中心查看详情。",
    };
  }

  const labels = (item?.enriched_fields ?? [])
    .map((field) => informationEnrichmentFieldLabels[field] ?? field)
    .join("、");
  return {
    level: "success",
    title: `补全完成：${professorName}`,
    description: labels
      ? `已补全：${labels}。`
      : items === null
        ? "补全过程已完成，请前往导师管理页查看最新信息。"
        : "补全过程完成，但没有发现可新增的信息。",
  };
};

const buildBatchInformationEnrichmentResult = (
  job: ProfessorInformationEnrichmentJobDTO,
  items: ProfessorInformationEnrichmentItemDTO[] | null,
): ResultNotification => {
  const enrichedFieldCount = items?.reduce(
    (total, item) => total + item.enriched_fields.length,
    0,
  );
  const countSummary = [
    `成功 ${job.succeeded_count} 位`,
    `失败 ${job.failed_count} 位`,
    `跳过 ${job.skipped_count} 位`,
    `取消 ${job.canceled_count} 位`,
  ].join("，");
  const fieldSummary =
    enrichedFieldCount === undefined
      ? ""
      : enrichedFieldCount > 0
        ? `，共补全 ${enrichedFieldCount} 项信息`
        : "，没有发现可新增的信息";
  const description = appendLastError(
    `${countSummary}${fieldSummary}。`,
    job.last_error,
  );

  if (job.status === "failed") {
    return { level: "error", title: "批量信息补全失败", description };
  }
  if (job.status === "canceled") {
    return { level: "warning", title: "批量信息补全已取消", description };
  }
  if (
    job.status === "partially_completed" ||
    job.failed_count > 0 ||
    job.canceled_count > 0
  ) {
    return { level: "warning", title: "批量信息补全部分完成", description };
  }
  return { level: "success", title: "批量信息补全完成", description };
};

const buildMatchAnalysisResult = (
  job: MatchAnalysisJobDTO,
): ResultNotification => {
  const description = appendLastError(
    `成功 ${job.succeeded_count} 位，失败 ${job.failed_count} 位，跳过 ${job.skipped_count} 位，共消耗 ${job.total_tokens} Token。`,
    job.last_error,
  );
  if (job.status === "failed") {
    return { level: "error", title: "批量匹配分析失败", description };
  }
  if (job.status === "canceled") {
    return { level: "warning", title: "批量匹配分析已取消", description };
  }
  if (job.status === "partial_failed" || job.failed_count > 0) {
    return { level: "warning", title: "批量匹配分析部分完成", description };
  }
  return { level: "success", title: "批量匹配分析完成", description };
};

const getCrawlJobName = (job: CrawlJobSummaryDTO) =>
  [job.university, job.school].filter(Boolean).join(" · ") || `抓取任务 #${job.id}`;

const buildCrawlJobResult = (job: CrawlJobSummaryDTO): ResultNotification => {
  const jobName = getCrawlJobName(job);
  const summary = `发现 ${job.candidate_count} 位候选，处理 ${job.page_count} 个页面。`;
  if (job.status === "needs_review") {
    return {
      level: "success",
      title: "智能抓取完成，等待审核",
      description: `${jobName}：${summary}请前往任务中心审核。`,
    };
  }
  if (job.status === "partially_completed") {
    return {
      level: "warning",
      title: "智能抓取部分完成",
      description: `${jobName}：${summary}`,
    };
  }
  if (job.status === "failed") {
    return {
      level: "error",
      title: "智能抓取失败",
      description:
        job.error_message ?? job.latest_event_message ?? `${jobName}抓取失败。`,
    };
  }
  if (job.status === "canceled") {
    return {
      level: "warning",
      title: "智能抓取已取消",
      description: `${jobName}：已保留当前抓取结果。`,
    };
  }
  return {
    level: "success",
    title: "智能抓取完成",
    description: `${jobName}：${summary}`,
  };
};

const readEventCount = (event: CrawlJobEventDTO, key: string): number => {
  const value = getCrawlEventRawPayload(event)?.[key];
  return typeof value === "number" && Number.isFinite(value) ? value : 0;
};

const buildCrawlCandidateEnrichmentResult = (
  event: CrawlJobEventDTO,
): ResultNotification => {
  const enrichedCount = readEventCount(event, "enriched_count");
  const unchangedCount = readEventCount(event, "unchanged_count");
  const failedCount = readEventCount(event, "failed_count");
  const status = getCrawlEventRawPayload(event)?.status;
  if (status === "canceled") {
    return {
      level: "warning",
      title: "候选信息补全已取消",
      description: event.message,
    };
  }
  if (failedCount > 0 && enrichedCount + unchangedCount === 0) {
    return {
      level: "error",
      title: "候选信息补全失败",
      description: event.message,
    };
  }
  if (failedCount > 0) {
    return {
      level: "warning",
      title: "候选信息补全部分完成",
      description: event.message,
    };
  }
  return {
    level: "success",
    title: "候选信息补全完成",
    description: event.message,
  };
};

const pollTrackedTask = async (
  task: TrackedBackgroundTask,
): Promise<ResultNotification | null> => {
  if (task.kind === "information_enrichment") {
    const job = await getProfessorInformationEnrichmentJob(task.jobId);
    if (activeInformationEnrichmentStatuses.has(job.status)) {
      return null;
    }
    let items: ProfessorInformationEnrichmentItemDTO[] | null = null;
    try {
      items = await listProfessorInformationEnrichmentItems(task.jobId);
    } catch {
      // Terminal job counts still provide a useful result notification.
    }
    return job.trigger_mode === "single"
      ? buildSingleInformationEnrichmentResult(task, job, items)
      : buildBatchInformationEnrichmentResult(job, items);
  }

  if (task.kind === "match_analysis") {
    const job = await getMatchAnalysisJob(task.jobId);
    return activeMatchAnalysisStatuses.has(job.status)
      ? null
      : buildMatchAnalysisResult(job);
  }

  if (task.kind === "crawl_job") {
    const job = await getCrawlJob(task.jobId);
    return activeCrawlJobStatuses.has(job.status)
      ? null
      : buildCrawlJobResult(job);
  }

  const events = await getCrawlJobEvents(task.jobId);
  const completedEvent = events.find(
    (event) =>
      isCrawlEnrichmentCompletionEvent(event) &&
      getCrawlEnrichmentOperationId(event) === task.operationId,
  );
  return completedEvent
    ? buildCrawlCandidateEnrichmentResult(completedEvent)
    : null;
};

export const BackgroundTaskNotificationProvider = ({
  children,
}: PropsWithChildren) => {
  const { notifyError, notifySuccess, notifyWarning } = useNotification();
  const [trackedTasks, setTrackedTasks] = useState<
    Record<string, TrackedBackgroundTask>
  >({});
  const pollingTaskKeysRef = useRef<Set<string>>(new Set());
  const notifiedTaskKeysRef = useRef<Set<string>>(new Set());
  const crawlCandidateOperationIdsRef = useRef<Map<string, string>>(new Map());

  const trackTask = useCallback((task: TrackedBackgroundTask) => {
    notifiedTaskKeysRef.current.delete(task.key);
    setTrackedTasks((previous) => ({ ...previous, [task.key]: task }));
  }, []);

  const stopTrackingTask = useCallback((key: string) => {
    notifiedTaskKeysRef.current.add(key);
    setTrackedTasks((previous) => {
      if (!previous[key]) {
        return previous;
      }
      const next = { ...previous };
      delete next[key];
      return next;
    });
  }, []);

  const trackInformationEnrichmentJob = useCallback(
    (
      job: ProfessorInformationEnrichmentJobDTO,
      options: TrackInformationEnrichmentOptions = {},
    ) => {
      const key = getTaskKey("information_enrichment", job.id);
      setTrackedTasks((previous) => {
        notifiedTaskKeysRef.current.delete(key);
        const current = previous[key];
        const professorName =
          options.professorName ??
          (current?.kind === "information_enrichment"
            ? current.professorName
            : undefined);
        return {
          ...previous,
          [key]: {
            key,
            kind: "information_enrichment",
            jobId: job.id,
            professorName,
          },
        };
      });
    },
    [],
  );

  const stopTrackingInformationEnrichmentJob = useCallback(
    (jobId: number) => {
      stopTrackingTask(getTaskKey("information_enrichment", jobId));
    },
    [stopTrackingTask],
  );

  const trackMatchAnalysisJob = useCallback(
    (job: MatchAnalysisJobDTO) => {
      trackTask({
        key: getTaskKey("match_analysis", job.id),
        kind: "match_analysis",
        jobId: job.id,
      });
    },
    [trackTask],
  );

  const trackCrawlJob = useCallback(
    (job: CrawlJobDTO) => {
      trackTask({
        key: getTaskKey("crawl_job", job.id),
        kind: "crawl_job",
        jobId: job.id,
      });
    },
    [trackTask],
  );

  const trackCrawlCandidateEnrichment = useCallback(
    (jobId: number, operationId: string) => {
      const key = getTaskKey("crawl_candidate_enrichment", jobId);
      crawlCandidateOperationIdsRef.current.set(key, operationId);
      trackTask({
        key,
        kind: "crawl_candidate_enrichment",
        jobId,
        operationId,
      });
    },
    [trackTask],
  );

  const notifyResult = useCallback(
    (notification: ResultNotification) => {
      if (notification.level === "error") {
        notifyError(notification.title, notification.description);
      } else if (notification.level === "warning") {
        notifyWarning(notification.title, notification.description);
      } else {
        notifySuccess(notification.title, notification.description);
      }
    },
    [notifyError, notifySuccess, notifyWarning],
  );

  useEffect(() => {
    const tasks = Object.values(trackedTasks);
    if (tasks.length === 0) {
      return;
    }

    let disposed = false;
    const pollTask = async (task: TrackedBackgroundTask) => {
      if (pollingTaskKeysRef.current.has(task.key)) {
        return;
      }
      pollingTaskKeysRef.current.add(task.key);
      try {
        const notification = await pollTrackedTask(task);
        if (
          disposed ||
          notification === null ||
          notifiedTaskKeysRef.current.has(task.key) ||
          (task.kind === "crawl_candidate_enrichment" &&
            crawlCandidateOperationIdsRef.current.get(task.key) !==
              task.operationId)
        ) {
          return;
        }
        notifyResult(notification);
        notifiedTaskKeysRef.current.add(task.key);
        if (task.kind === "crawl_candidate_enrichment") {
          crawlCandidateOperationIdsRef.current.delete(task.key);
        }
        setTrackedTasks((previous) => {
          const current = previous[task.key];
          if (
            !current ||
            (task.kind === "crawl_candidate_enrichment" &&
              (current.kind !== "crawl_candidate_enrichment" ||
                current.operationId !== task.operationId))
          ) {
            return previous;
          }
          const next = { ...previous };
          delete next[task.key];
          return next;
        });
      } catch {
        // Transient polling failures are retried while the task remains tracked.
      } finally {
        pollingTaskKeysRef.current.delete(task.key);
      }
    };
    const poll = () => {
      tasks.forEach((task) => {
        void pollTask(task);
      });
    };

    poll();
    const intervalId = window.setInterval(poll, POLL_INTERVAL_MS);
    return () => {
      disposed = true;
      window.clearInterval(intervalId);
    };
  }, [notifyResult, trackedTasks]);

  const value = useMemo<BackgroundTaskNotificationContextValue>(
    () => ({
      trackInformationEnrichmentJob,
      stopTrackingInformationEnrichmentJob,
      trackMatchAnalysisJob,
      trackCrawlJob,
      trackCrawlCandidateEnrichment,
    }),
    [
      stopTrackingInformationEnrichmentJob,
      trackCrawlCandidateEnrichment,
      trackCrawlJob,
      trackInformationEnrichmentJob,
      trackMatchAnalysisJob,
    ],
  );

  return (
    <BackgroundTaskNotificationContext.Provider value={value}>
      {children}
    </BackgroundTaskNotificationContext.Provider>
  );
};

export const useBackgroundTaskNotification = () => {
  const context = useContext(BackgroundTaskNotificationContext);
  if (context === null) {
    throw new Error("BackgroundTaskNotificationContext 未初始化");
  }
  return context;
};
