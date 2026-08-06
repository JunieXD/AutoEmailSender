import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { useState, type ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import {
  BackgroundTaskNotificationProvider,
  useBackgroundTaskNotification,
} from "@/app/providers/BackgroundTaskNotificationContext";
import { NotificationProvider } from "@/context/NotificationContext";
import type {
  CrawlJobEventDTO,
  CrawlJobSummaryDTO,
  MatchAnalysisJobDTO,
  ProfessorInformationEnrichmentItemDTO,
  ProfessorInformationEnrichmentJobDTO,
} from "@/types";

const apiMocks = vi.hoisted(() => ({
  getCrawlJob: vi.fn(),
  getCrawlJobEvents: vi.fn(),
  getMatchAnalysisJob: vi.fn(),
  getProfessorInformationEnrichmentJob: vi.fn(),
  listProfessorInformationEnrichmentItems: vi.fn(),
}));

vi.mock("@/lib/api/crawlJobsApi", () => ({
  getCrawlJob: apiMocks.getCrawlJob,
  getCrawlJobEvents: apiMocks.getCrawlJobEvents,
}));

vi.mock("@/lib/api/matchAnalysisJobsApi", () => ({
  getMatchAnalysisJob: apiMocks.getMatchAnalysisJob,
}));

vi.mock("@/entities/professor/api/informationEnrichment", () => ({
  getProfessorInformationEnrichmentJob:
    apiMocks.getProfessorInformationEnrichmentJob,
  listProfessorInformationEnrichmentItems:
    apiMocks.listProfessorInformationEnrichmentItems,
}));

const runningInformationJob: ProfessorInformationEnrichmentJobDTO = {
  id: 71,
  name: "李教授 · 信息补全",
  trigger_mode: "single",
  status: "running",
  target_count: 1,
  completed_count: 0,
  queued_count: 0,
  running_count: 1,
  succeeded_count: 0,
  failed_count: 0,
  skipped_count: 0,
  canceled_count: 0,
  input_tokens: 0,
  output_tokens: 0,
  cached_tokens: 0,
  total_tokens: 0,
  llm_profile_id: 7,
  started_at: "2026-07-21T08:00:00Z",
  finished_at: null,
  duration_seconds: 0,
  created_at: "2026-07-21T08:00:00Z",
  updated_at: "2026-07-21T08:00:00Z",
  deleted_at: null,
  last_error: null,
};

const completedInformationJob: ProfessorInformationEnrichmentJobDTO = {
  ...runningInformationJob,
  status: "completed",
  completed_count: 1,
  running_count: 0,
  succeeded_count: 1,
  finished_at: "2026-07-21T08:00:20Z",
  duration_seconds: 20,
  updated_at: "2026-07-21T08:00:20Z",
};

const completedBatchInformationJob: ProfessorInformationEnrichmentJobDTO = {
  ...completedInformationJob,
  id: 72,
  name: "信息补全 2026-07-21",
  trigger_mode: "batch",
  target_count: 3,
  completed_count: 3,
  succeeded_count: 2,
  skipped_count: 1,
};

const runningMatchJob: MatchAnalysisJobDTO = {
  id: 81,
  name: "匹配分析 2026-07-21",
  status: "running",
  target_count: 3,
  succeeded_count: 0,
  failed_count: 0,
  skipped_count: 0,
  total_prompt_tokens: 0,
  total_completion_tokens: 0,
  total_cached_tokens: 0,
  total_tokens: 0,
  identity_id: 1,
  llm_profile_id: 7,
  cancel_requested_at: null,
  started_at: "2026-07-21T08:00:00Z",
  finished_at: null,
  created_at: "2026-07-21T08:00:00Z",
  updated_at: "2026-07-21T08:00:00Z",
  deleted_at: null,
  last_error: null,
};

const partialMatchJob: MatchAnalysisJobDTO = {
  ...runningMatchJob,
  status: "partial_failed",
  succeeded_count: 2,
  failed_count: 1,
  total_prompt_tokens: 900,
  total_completion_tokens: 100,
  total_cached_tokens: 700,
  total_tokens: 1000,
  finished_at: "2026-07-21T08:00:30Z",
  updated_at: "2026-07-21T08:00:30Z",
  last_error: "一位导师分析失败",
};

const runningCrawlJob: CrawlJobSummaryDTO = {
  id: 91,
  university: "测试大学",
  school: "计算机学院",
  start_url: "https://example.edu/faculty",
  start_urls: ["https://example.edu/faculty"],
  entry_type: "list",
  llm_profile_id: 7,
  status: "running",
  progress_current: 1,
  progress_total: 3,
  error_message: null,
  created_at: "2026-07-21T08:00:00Z",
  updated_at: "2026-07-21T08:00:00Z",
  deleted_at: null,
  page_count: 1,
  candidate_count: 0,
  latest_event_message: "正在抓取",
  input_tokens: 0,
  output_tokens: 0,
  cached_tokens: 0,
  total_tokens: 0,
  duration_seconds: 0,
};

const reviewCrawlJob: CrawlJobSummaryDTO = {
  ...runningCrawlJob,
  status: "needs_review",
  progress_current: 3,
  page_count: 12,
  candidate_count: 34,
  latest_event_message: "抓取完成，等待审核",
  updated_at: "2026-07-21T08:00:40Z",
  duration_seconds: 40,
};

const buildInformationItem = (
  overrides: Partial<ProfessorInformationEnrichmentItemDTO> = {},
): ProfessorInformationEnrichmentItemDTO => ({
  id: 101,
  job_id: 71,
  professor_id: 1,
  professor_name: "李教授",
  professor_email: null,
  professor_title: null,
  professor_university: "测试大学",
  professor_school: null,
  professor_department: null,
  profile_url: "https://example.edu/li",
  status: "succeeded",
  enriched_fields: ["department"],
  error_message: null,
  skip_reason: null,
  input_tokens: 100,
  output_tokens: 20,
  cached_tokens: 0,
  total_tokens: 120,
  attempt_count: 1,
  started_at: "2026-07-21T08:00:00Z",
  finished_at: "2026-07-21T08:00:20Z",
  created_at: "2026-07-21T08:00:00Z",
  updated_at: "2026-07-21T08:00:20Z",
  ...overrides,
});

const buildCrawlEvent = (
  overrides: Partial<CrawlJobEventDTO> = {},
): CrawlJobEventDTO => ({
  id: "evt-1",
  job_id: 91,
  event_type: "crawl_page",
  message: "正在抓取页面",
  created_at: "2026-07-21T08:00:00Z",
  raw: null,
  ...overrides,
});

const deferred = <T,>() => {
  let resolve: (value: T) => void = () => undefined;
  const promise = new Promise<T>((nextResolve) => {
    resolve = nextResolve;
  });
  return { promise, resolve };
};

const InformationJobStarter = ({ onLeave }: { onLeave: () => void }) => {
  const { trackInformationEnrichmentJob } = useBackgroundTaskNotification();
  return (
    <>
      <button
        type="button"
        onClick={() =>
          trackInformationEnrichmentJob(runningInformationJob, {
            professorName: "李教授",
          })
        }
      >
        开始单条补全
      </button>
      <button type="button" onClick={onLeave}>
        切换页面
      </button>
    </>
  );
};

const CrossPageHarness = () => {
  const [onProfessorPage, setOnProfessorPage] = useState(true);
  return onProfessorPage ? (
    <InformationJobStarter onLeave={() => setOnProfessorPage(false)} />
  ) : (
    <main>其他页面</main>
  );
};

const TaskStarters = () => {
  const {
    trackCrawlCandidateEnrichment,
    trackCrawlJob,
    trackInformationEnrichmentJob,
    trackMatchAnalysisJob,
  } = useBackgroundTaskNotification();
  return (
    <>
      <button
        type="button"
        onClick={() => trackInformationEnrichmentJob(completedBatchInformationJob)}
      >
        开始批量补全
      </button>
      <button type="button" onClick={() => trackMatchAnalysisJob(runningMatchJob)}>
        开始批量匹配
      </button>
      <button type="button" onClick={() => trackCrawlJob(runningCrawlJob)}>
        开始教师抓取
      </button>
      <button
        type="button"
        onClick={() => trackCrawlCandidateEnrichment(91, new Set(["old-event"]))}
      >
        开始候选补全
      </button>
    </>
  );
};

const renderWithProviders = (children: ReactNode) =>
  render(
    <NotificationProvider>
      <BackgroundTaskNotificationProvider>
        {children}
      </BackgroundTaskNotificationProvider>
    </NotificationProvider>,
  );

describe("BackgroundTaskNotificationProvider", () => {
  beforeEach(() => {
    Object.values(apiMocks).forEach((mock) => mock.mockReset());
  });

  it("keeps polling after the initiating page unmounts and only notifies on completion", async () => {
    const jobRequest = deferred<ProfessorInformationEnrichmentJobDTO>();
    apiMocks.getProfessorInformationEnrichmentJob.mockReturnValue(jobRequest.promise);
    apiMocks.listProfessorInformationEnrichmentItems.mockResolvedValue([
      buildInformationItem(),
    ]);
    renderWithProviders(<CrossPageHarness />);

    fireEvent.click(screen.getByRole("button", { name: "开始单条补全" }));
    await waitFor(() => {
      expect(apiMocks.getProfessorInformationEnrichmentJob).toHaveBeenCalledWith(71);
    });
    expect(screen.queryByTestId("notification-card")).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "切换页面" }));
    expect(screen.getByText("其他页面")).toBeInTheDocument();
    await act(async () => {
      jobRequest.resolve(completedInformationJob);
      await jobRequest.promise;
    });

    expect(await screen.findByText("补全完成：李教授")).toBeInTheDocument();
    expect(screen.getByText("已补全：系所。")).toBeInTheDocument();
  });

  it("shows aggregate results when a batch enrichment job finishes", async () => {
    apiMocks.getProfessorInformationEnrichmentJob.mockResolvedValue(
      completedBatchInformationJob,
    );
    apiMocks.listProfessorInformationEnrichmentItems.mockResolvedValue([
      buildInformationItem({
        job_id: 72,
        enriched_fields: ["department", "recent_papers"],
      }),
      buildInformationItem({
        id: 102,
        job_id: 72,
        professor_id: 2,
        enriched_fields: ["email"],
      }),
      buildInformationItem({
        id: 103,
        job_id: 72,
        professor_id: 3,
        status: "skipped",
        enriched_fields: [],
        skip_reason: "缺少主页链接",
      }),
    ]);
    renderWithProviders(<TaskStarters />);

    fireEvent.click(screen.getByRole("button", { name: "开始批量补全" }));

    expect(await screen.findByText("批量信息补全完成")).toBeInTheDocument();
    expect(
      screen.getByText("成功 2 位，失败 0 位，跳过 1 位，取消 0 位，共补全 3 项信息。"),
    ).toBeInTheDocument();
  });

  it("reports partial match-analysis results", async () => {
    apiMocks.getMatchAnalysisJob.mockResolvedValue(partialMatchJob);
    renderWithProviders(<TaskStarters />);

    fireEvent.click(screen.getByRole("button", { name: "开始批量匹配" }));

    expect(await screen.findByText("批量匹配分析部分完成")).toBeInTheDocument();
    expect(
      screen.getByText(/成功 2 位，失败 1 位，跳过 0 位，共消耗 1000 Token/),
    ).toBeInTheDocument();
  });

  it("notifies when a crawl job reaches review", async () => {
    apiMocks.getCrawlJob.mockResolvedValue(reviewCrawlJob);
    renderWithProviders(<TaskStarters />);

    fireEvent.click(screen.getByRole("button", { name: "开始教师抓取" }));

    expect(
      await screen.findByText("教师抓取完成，等待审核"),
    ).toBeInTheDocument();
    expect(
      screen.getByText(/测试大学 · 计算机学院：发现 34 位候选，处理 12 个页面/),
    ).toBeInTheDocument();
  });

  it("detects candidate enrichment completion from a new crawl event", async () => {
    apiMocks.getCrawlJobEvents.mockResolvedValue([
      buildCrawlEvent({
        id: "evt-2",
        event_type: "enrichment",
        message: "候选导师详情补全完成：成功 2 位，未变化 1 位，失败 0 位",
        created_at: "2026-07-21T08:01:00Z",
        raw: {
          enriched_count: 2,
          unchanged_count: 1,
          failed_count: 0,
        },
      }),
    ]);
    renderWithProviders(<TaskStarters />);

    fireEvent.click(screen.getByRole("button", { name: "开始候选补全" }));

    expect(await screen.findByText("候选信息补全完成")).toBeInTheDocument();
    expect(
      screen.getByText("候选导师详情补全完成：成功 2 位，未变化 1 位，失败 0 位"),
    ).toBeInTheDocument();
  });
});
