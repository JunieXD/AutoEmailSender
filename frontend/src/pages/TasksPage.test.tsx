import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import "@testing-library/jest-dom/vitest";
import { Activity, type ReactNode } from "react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type {
  BatchTaskCardDTO,
  BatchTaskItemDTO,
  CrawlJobEventDTO,
  CrawlJobSummaryDTO,
  MatchAnalysisJobDTO,
  MatchAnalysisJobItemDTO,
  ProfessorDTO,
  ProfessorManagementItemDTO,
  ProfessorInformationEnrichmentItemDTO,
  ProfessorInformationEnrichmentJobDTO,
  WorkspaceThreadDTO,
} from "@/types";
import {
  buildBatchPendingItemAction,
  getBatchTaskItemCancellationText,
  getBatchTaskWaitingSendCount,
  isBatchTaskItemMissingResearchDirection,
} from "@/features/batch-tasks/client/batchTaskDisplay";
import { getCrawlEventFailureReason } from "@/features/crawl-review/client/crawlJobEvents";
import {
  CrawlJobCard,
  TasksPage,
  TaskListViewSwitch,
} from "./TasksPage";

const apiMocks = vi.hoisted(() => ({
  listBatchTasks: vi.fn(),
  listBatchTaskItems: vi.fn(),
  getBatchTaskResendContext: vi.fn(),
  pauseBatchTask: vi.fn(),
  resumeBatchTask: vi.fn(),
  stopBatchTask: vi.fn(),
  deleteBatchTask: vi.fn(),
  restoreBatchTask: vi.fn(),
  getBatchTaskItemThread: vi.fn(),
  regenerateBatchTaskItemDraft: vi.fn(),
  approveBatchTaskItemDraft: vi.fn(),
  approveAllBatchTaskDrafts: vi.fn(),
  approveAndSendBatchTaskItemDraft: vi.fn(),
  cancelBatchTaskItemSend: vi.fn(),
  deleteBatchTaskItem: vi.fn(),
  restoreBatchTaskItemSend: vi.fn(),
  listCrawlJobs: vi.fn(),
  getCrawlJob: vi.fn(),
  getCrawlJobEvents: vi.fn(),
  listCrawlCandidates: vi.fn(),
  listCrawlPages: vi.fn(),
  pauseCrawlJob: vi.fn(),
  resumeCrawlJob: vi.fn(),
  cancelCrawlJob: vi.fn(),
  retryCrawlJob: vi.fn(),
  resumeCrawlJobReview: vi.fn(),
  approveCrawlCandidates: vi.fn(),
  enrichCrawlCandidates: vi.fn(),
  updateCrawlCandidate: vi.fn(),
  deleteCrawlJob: vi.fn(),
  restoreCrawlJob: vi.fn(),
  listMatchAnalysisJobs: vi.fn(),
  listMatchAnalysisJobItems: vi.fn(),
  cancelMatchAnalysisJob: vi.fn(),
  retryFailedMatchAnalysisJob: vi.fn(),
  deleteMatchAnalysisJob: vi.fn(),
  restoreMatchAnalysisJob: vi.fn(),
  listProfessorInformationEnrichmentJobs: vi.fn(),
  listProfessorInformationEnrichmentItems: vi.fn(),
  cancelProfessorInformationEnrichmentJob: vi.fn(),
  retryFailedProfessorInformationEnrichmentJob: vi.fn(),
  deleteProfessorInformationEnrichmentJob: vi.fn(),
  restoreProfessorInformationEnrichmentJob: vi.fn(),
  getProfessor: vi.fn(),
  updateProfessor: vi.fn(),
  getWorkspaceThread: vi.fn(),
  regenerateDraft: vi.fn(),
  approveDraft: vi.fn(),
  approveAndSend: vi.fn(),
  retryBatchTaskItemDraft: vi.fn(),
}));

const notificationMocks = vi.hoisted(() => ({
  notifyError: vi.fn(),
  notifySuccess: vi.fn(),
}));
const backgroundTaskNotificationMocks = vi.hoisted(() => ({
  stopTrackingInformationEnrichmentJob: vi.fn(),
  trackCrawlCandidateEnrichment: vi.fn(),
  trackCrawlJob: vi.fn(),
  trackInformationEnrichmentJob: vi.fn(),
  trackMatchAnalysisJob: vi.fn(),
}));

const confirmMock = vi.hoisted(() => vi.fn().mockResolvedValue(true));
const navigateMock = vi.hoisted(() => vi.fn());
const selectionMock = vi.hoisted(() => ({
  selectedIdentityId: 1 as number | null,
  selectedLlmProfileId: 2 as number | null,
  setSelectedIdentityId: vi.fn(),
  setSelectedLlmProfileId: vi.fn(),
}));
const scrollIntoView = vi.fn();

vi.mock("react-router-dom", async () => {
  const actual = await vi.importActual<typeof import("react-router-dom")>("react-router-dom");
  return {
    ...actual,
    useNavigate: () => navigateMock,
  };
});
vi.mock("@/context/SelectionContext", () => ({
  useSelectionContext: () => selectionMock,
}));

vi.mock("@/context/NotificationContext", () => ({
  useNotification: () => notificationMocks,
}));

vi.mock("@/context/BackgroundTaskNotificationContext", () => ({
  useBackgroundTaskNotification: () => backgroundTaskNotificationMocks,
}));

vi.mock("@/lib/useConfirmDialog", () => ({
  useConfirmDialog: () => ({
    confirm: confirmMock,
    dialog: null,
  }),
}));

vi.mock("@/lib/api/batchTasksApi", () => ({
  listBatchTasks: apiMocks.listBatchTasks,
  listBatchTaskItems: apiMocks.listBatchTaskItems,
  getBatchTaskResendContext: apiMocks.getBatchTaskResendContext,
  pauseBatchTask: apiMocks.pauseBatchTask,
  resumeBatchTask: apiMocks.resumeBatchTask,
  stopBatchTask: apiMocks.stopBatchTask,
  deleteBatchTask: apiMocks.deleteBatchTask,
  restoreBatchTask: apiMocks.restoreBatchTask,
  getBatchTaskItemThread: apiMocks.getBatchTaskItemThread,
  regenerateBatchTaskItemDraft: apiMocks.regenerateBatchTaskItemDraft,
  approveBatchTaskItemDraft: apiMocks.approveBatchTaskItemDraft,
  approveAllBatchTaskDrafts: apiMocks.approveAllBatchTaskDrafts,
  approveAndSendBatchTaskItemDraft: apiMocks.approveAndSendBatchTaskItemDraft,
  cancelBatchTaskItemSend: apiMocks.cancelBatchTaskItemSend,
  deleteBatchTaskItem: apiMocks.deleteBatchTaskItem,
  restoreBatchTaskItemSend: apiMocks.restoreBatchTaskItemSend,
  retryBatchTaskItemDraft: apiMocks.retryBatchTaskItemDraft,
}));

vi.mock("@/lib/api/crawlJobsApi", () => ({
  listCrawlJobs: apiMocks.listCrawlJobs,
  getCrawlJob: apiMocks.getCrawlJob,
  getCrawlJobEvents: apiMocks.getCrawlJobEvents,
  listCrawlCandidates: apiMocks.listCrawlCandidates,
  listCrawlPages: apiMocks.listCrawlPages,
  pauseCrawlJob: apiMocks.pauseCrawlJob,
  resumeCrawlJob: apiMocks.resumeCrawlJob,
  cancelCrawlJob: apiMocks.cancelCrawlJob,
  retryCrawlJob: apiMocks.retryCrawlJob,
  resumeCrawlJobReview: apiMocks.resumeCrawlJobReview,
  approveCrawlCandidates: apiMocks.approveCrawlCandidates,
  enrichCrawlCandidates: apiMocks.enrichCrawlCandidates,
  updateCrawlCandidate: apiMocks.updateCrawlCandidate,
  deleteCrawlJob: apiMocks.deleteCrawlJob,
  restoreCrawlJob: apiMocks.restoreCrawlJob,
}));

vi.mock("@/lib/api/matchAnalysisJobsApi", () => ({
  listMatchAnalysisJobs: apiMocks.listMatchAnalysisJobs,
  listMatchAnalysisJobItems: apiMocks.listMatchAnalysisJobItems,
  cancelMatchAnalysisJob: apiMocks.cancelMatchAnalysisJob,
  retryFailedMatchAnalysisJob: apiMocks.retryFailedMatchAnalysisJob,
  deleteMatchAnalysisJob: apiMocks.deleteMatchAnalysisJob,
  restoreMatchAnalysisJob: apiMocks.restoreMatchAnalysisJob,
}));

vi.mock("@/lib/api/professorInformationEnrichmentApi", () => ({
  listProfessorInformationEnrichmentJobs:
    apiMocks.listProfessorInformationEnrichmentJobs,
  listProfessorInformationEnrichmentItems:
    apiMocks.listProfessorInformationEnrichmentItems,
  cancelProfessorInformationEnrichmentJob:
    apiMocks.cancelProfessorInformationEnrichmentJob,
  retryFailedProfessorInformationEnrichmentJob:
    apiMocks.retryFailedProfessorInformationEnrichmentJob,
  deleteProfessorInformationEnrichmentJob:
    apiMocks.deleteProfessorInformationEnrichmentJob,
  restoreProfessorInformationEnrichmentJob:
    apiMocks.restoreProfessorInformationEnrichmentJob,
}));

vi.mock("@/lib/api/professorsApi", () => ({
  getProfessor: apiMocks.getProfessor,
  updateProfessor: apiMocks.updateProfessor,
}));

vi.mock("@/lib/api/workspacesApi", () => ({
  getWorkspaceThread: apiMocks.getWorkspaceThread,
}));

vi.mock("@/lib/api/emailTasksApi", () => ({
  regenerateDraft: apiMocks.regenerateDraft,
  approveDraft: apiMocks.approveDraft,
  approveAndSend: apiMocks.approveAndSend,
}));

vi.mock("@/components/molecules/SubjectTemplateInput", () => ({
  SubjectTemplateInput: ({
    label,
    value,
    onChange,
  }: {
    label: string;
    value: string;
    onChange: (value: string) => void;
  }) => (
    <input
      aria-label={label}
      value={value}
      onChange={(event) => onChange(event.currentTarget.value)}
    />
  ),
}));

vi.mock("@/components/molecules/EmailTemplateEditor", () => ({
  EmailTemplateEditor: ({
    label,
    html,
    onChange,
  }: {
    label: string;
    html: string;
    onChange: (value: { html: string; text: string }) => void;
  }) => (
    <textarea
      aria-label={label}
      value={html}
      onChange={(event) =>
        onChange({
          html: event.currentTarget.value,
          text: event.currentTarget.value.replace(/<[^>]+>/g, ""),
        })
      }
    />
  ),
}));

const ActivityHarness = ({
  mode,
  children,
}: {
  mode: "visible" | "hidden";
  children: ReactNode;
}) => <Activity mode={mode}>{children}</Activity>;

const buildCrawlJob = (
  overrides: Partial<CrawlJobSummaryDTO> = {},
): CrawlJobSummaryDTO => ({
  id: 9,
  university: "江西财经大学",
  school: "计算机与人工智能学院",
  start_url: "https://sim.jxufe.edu.cn/#/staff/detail/5",
  start_urls: ["https://sim.jxufe.edu.cn/#/staff/detail/5"],
  entry_type: "profile",
  llm_profile_id: 1,
  status: "failed",
  progress_current: 5,
  progress_total: 8,
  error_message: null,
  created_at: "2026-05-01T14:40:00",
  updated_at: "2026-05-01T14:49:02",
  deleted_at: null,
  page_count: 5,
  candidate_count: 1,
  latest_event_message:
    "入口 URL 抓取失败: Blocked by anti-bot protection: Structural: minimal_text, no_content_elements (52 bytes, 13 chars visible)",
  input_tokens: 0,
  output_tokens: 0,
  cached_tokens: 0,
  total_tokens: 0,
  duration_seconds: 0,
  ...overrides,
});

describe("CrawlJobCard", () => {
  it("uses a separated responsive layout and truncates long latest events", () => {
    const job = buildCrawlJob();

    render(
      <CrawlJobCard
        job={job}
        listView="current"
        pausingCrawlJobId={null}
        resumingCrawlJobId={null}
        retryingCrawlJobId={null}
        resumingCrawlJobReviewId={null}
        onOpenDetails={vi.fn()}
        onPause={vi.fn()}
        onResume={vi.fn()}
        onCancel={vi.fn()}
        onRetry={vi.fn()}
        onResumeReview={vi.fn()}
        onDelete={vi.fn()}
        onRestore={vi.fn()}
        formatUpdatedAt={() => "05/01 14:49:02"}
      />,
    );

    const layout = screen.getByTestId("crawl-job-card-layout");
    expect(layout.className).toContain("xl:flex-row");

    const infoGrid = screen.getByTestId("crawl-job-card-info-grid");
    expect(infoGrid.className).toContain(
      "xl:grid-cols-[minmax(320px,1.3fr)_240px_minmax(280px,0.95fr)]",
    );

    const latestEvent = screen.getByTestId("crawl-job-card-latest-event");
    expect(latestEvent).toHaveClass("line-clamp-2");
    expect(latestEvent).toHaveClass("break-all");
    expect(latestEvent).toHaveAttribute("title", job.latest_event_message);
  });

  it.each([
    "needs_review",
    "partially_completed",
    "completed",
    "failed",
    "canceled",
  ] as const)("shows delete action for %s in the current list", (status) => {
    render(
      <CrawlJobCard
        job={buildCrawlJob({ status })}
        listView="current"
        pausingCrawlJobId={null}
        resumingCrawlJobId={null}
        retryingCrawlJobId={null}
        resumingCrawlJobReviewId={null}
        onOpenDetails={vi.fn()}
        onPause={vi.fn()}
        onResume={vi.fn()}
        onCancel={vi.fn()}
        onRetry={vi.fn()}
        onResumeReview={vi.fn()}
        onDelete={vi.fn()}
        onRestore={vi.fn()}
        formatUpdatedAt={() => "05/01 14:49:02"}
      />,
    );

    expect(screen.getByRole("button", { name: "删除" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "还原任务" })).not.toBeInTheDocument();
  });

  it.each(["queued", "running", "paused"] as const)(
    "hides delete action for %s in the current list",
    (status) => {
      render(
        <CrawlJobCard
          job={buildCrawlJob({ status })}
          listView="current"
          pausingCrawlJobId={null}
          resumingCrawlJobId={null}
          retryingCrawlJobId={null}
          resumingCrawlJobReviewId={null}
          onOpenDetails={vi.fn()}
          onPause={vi.fn()}
          onResume={vi.fn()}
          onCancel={vi.fn()}
          onRetry={vi.fn()}
          onResumeReview={vi.fn()}
          onDelete={vi.fn()}
          onRestore={vi.fn()}
          formatUpdatedAt={() => "05/01 14:49:02"}
        />,
      );

      expect(screen.queryByRole("button", { name: "删除" })).not.toBeInTheDocument();
    },
  );

  it("shows restore action only in the trash list", () => {
    render(
      <CrawlJobCard
        job={buildCrawlJob({ deleted_at: "2026-05-07T10:00:00" })}
        listView="trash"
        pausingCrawlJobId={null}
        resumingCrawlJobId={null}
        retryingCrawlJobId={null}
        resumingCrawlJobReviewId={null}
        onOpenDetails={vi.fn()}
        onPause={vi.fn()}
        onResume={vi.fn()}
        onCancel={vi.fn()}
        onRetry={vi.fn()}
        onResumeReview={vi.fn()}
        onDelete={vi.fn()}
        onRestore={vi.fn()}
        formatUpdatedAt={() => "05/01 14:49:02"}
      />,
    );

    expect(screen.getByRole("button", { name: "还原任务" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "删除" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "重新抓取" })).not.toBeInTheDocument();
  });
});

describe("TasksPage crawl job action copy", () => {
  it("uses the visible re-crawl label in the cancel confirmation", async () => {
    apiMocks.listCrawlJobs.mockResolvedValue([
      buildCrawlJob({ status: "running" }),
    ]);

    render(
      <MemoryRouter>
        <TasksPage />
      </MemoryRouter>,
    );

    fireEvent.click(screen.getByRole("button", { name: "教师抓取" }));
    fireEvent.click(await screen.findByRole("button", { name: "取消抓取" }));

    await waitFor(() => {
      expect(confirmMock).toHaveBeenCalledWith(
        expect.objectContaining({
          description:
            "取消后本次抓取不会继续。如需重新抓取，请点击“重新抓取”。",
        }),
      );
    });
  });

  it("blocks retrying a failed crawl job without a selected LLM profile", async () => {
    selectionMock.selectedLlmProfileId = null;
    apiMocks.listCrawlJobs.mockResolvedValue([buildCrawlJob()]);

    render(
      <MemoryRouter>
        <TasksPage />
      </MemoryRouter>,
    );

    fireEvent.click(screen.getByRole("button", { name: "教师抓取" }));
    fireEvent.click(await screen.findByRole("button", { name: "重新抓取" }));

    await waitFor(() => {
      expect(notificationMocks.notifyError).toHaveBeenCalledWith(
        "请先选择模型配置",
        "请选择一个 LLM Profile 后再继续操作。",
      );
    });
    expect(confirmMock).not.toHaveBeenCalled();
    expect(apiMocks.retryCrawlJob).not.toHaveBeenCalled();
  });

  it("blocks resuming a paused crawl job without a selected LLM profile", async () => {
    selectionMock.selectedLlmProfileId = null;
    apiMocks.listCrawlJobs.mockResolvedValue([
      buildCrawlJob({ status: "paused" }),
    ]);

    render(
      <MemoryRouter>
        <TasksPage />
      </MemoryRouter>,
    );

    fireEvent.click(screen.getByRole("button", { name: "教师抓取" }));
    fireEvent.click(await screen.findByRole("button", { name: "继续抓取" }));

    await waitFor(() => {
      expect(notificationMocks.notifyError).toHaveBeenCalledWith(
        "请先选择模型配置",
        "请选择一个 LLM Profile 后再继续操作。",
      );
    });
    expect(apiMocks.resumeCrawlJob).not.toHaveBeenCalled();
  });

  it("uses re-crawl wording after retrying a failed crawl job", async () => {
    apiMocks.listCrawlJobs.mockResolvedValue([buildCrawlJob()]);
    const retriedJob = buildCrawlJob({ status: "queued" });
    apiMocks.retryCrawlJob.mockResolvedValue(retriedJob);

    render(
      <MemoryRouter>
        <TasksPage />
      </MemoryRouter>,
    );

    fireEvent.click(screen.getByRole("button", { name: "教师抓取" }));
    fireEvent.click(await screen.findByRole("button", { name: "重新抓取" }));

    await waitFor(() => {
      expect(apiMocks.retryCrawlJob).toHaveBeenCalledWith(9, {
        clear_existing_data: true,
        llmProfileId: 2,
      });
    });
    expect(notificationMocks.notifySuccess).toHaveBeenCalledWith(
      "抓取任务已重新加入队列",
      "任务已进入队列，稍后开始执行",
    );
    expect(backgroundTaskNotificationMocks.trackCrawlJob).toHaveBeenCalledWith(
      retriedJob,
    );
  });

  it("tracks a resumed crawl job globally", async () => {
    const pausedJob = buildCrawlJob({ status: "paused" });
    const resumedJob = buildCrawlJob({ status: "queued" });
    apiMocks.listCrawlJobs.mockResolvedValue([pausedJob]);
    apiMocks.resumeCrawlJob.mockResolvedValue(resumedJob);

    render(
      <MemoryRouter>
        <TasksPage />
      </MemoryRouter>,
    );

    fireEvent.click(screen.getByRole("button", { name: "教师抓取" }));
    fireEvent.click(await screen.findByRole("button", { name: "继续抓取" }));

    await waitFor(() => {
      expect(apiMocks.resumeCrawlJob).toHaveBeenCalledWith(9, 2);
    });
    expect(backgroundTaskNotificationMocks.trackCrawlJob).toHaveBeenCalledWith(
      resumedJob,
    );
  });
});

const buildBatchTask = (
  overrides: Partial<BatchTaskCardDTO> = {},
): BatchTaskCardDTO => ({
  id: 1,
  name: "模板定时任务",
  status: "running",
  schedule_type: "scheduled",
  scheduled_dates: ["2026-05-08"],
  window_start_time: "09:00",
  window_end_time: "11:00",
  emails_per_window: 10,
  email_subject: "申请交流",
  outreach_template_id: 8,
  outreach_template_name_snapshot: "博士申请模板",
  outreach_template_snapshot_version: 1,
  outreach_generation_mode: "template",
  target_count: 1,
  completed_count: 0,
  identity_id: 1,
  llm_profile_id: 2,
  pending_generation_count: 0,
  generating_draft_count: 0,
  draft_failed_count: 0,
  review_required_count: 0,
  approved_count: 1,
  scheduled_count: 0,
  sent_count: 0,
  failed_count: 0,
  replied_count: 0,
  canceled_send_count: 0,
  created_at: "2026-05-08T00:00:00",
  updated_at: "2026-05-08T00:00:00",
  deleted_at: null,
  ...overrides,
});

const buildMatchAnalysisJob = (
  overrides: Partial<MatchAnalysisJobDTO> = {},
): MatchAnalysisJobDTO => ({
  id: 31,
  name: "批量匹配分析",
  status: "completed",
  target_count: 1,
  succeeded_count: 1,
  failed_count: 0,
  skipped_count: 0,
  total_prompt_tokens: 0,
  total_completion_tokens: 0,
  total_cached_tokens: 0,
  total_tokens: 0,
  identity_id: 1,
  llm_profile_id: 2,
  cancel_requested_at: null,
  started_at: "2026-05-08T00:00:00",
  finished_at: "2026-05-08T00:01:00",
  created_at: "2026-05-08T00:00:00",
  updated_at: "2026-05-08T00:01:00",
  deleted_at: null,
  last_error: null,
  ...overrides,
});

const buildMatchAnalysisJobItem = (
  overrides: Partial<MatchAnalysisJobItemDTO> = {},
): MatchAnalysisJobItemDTO => ({
  id: 41,
  job_id: 31,
  professor_id: 21,
  professor_name: "张老师",
  professor_email: "zhang@example.edu",
  professor_title: "教授",
  professor_university: "测试大学",
  professor_school: "计算机学院",
  email_task_id: 71,
  status: "succeeded",
  match_score: 91,
  match_analysis_run_id: 81,
  error_message: null,
  skip_reason: null,
  prompt_tokens: 900,
  completion_tokens: 100,
  cached_tokens: 700,
  total_tokens: 1000,
  started_at: "2026-05-08T00:00:00",
  finished_at: "2026-05-08T00:00:30",
  updated_at: "2026-05-08T00:00:30",
  ...overrides,
});

const buildInformationEnrichmentJob = (
  overrides: Partial<ProfessorInformationEnrichmentJobDTO> = {},
): ProfessorInformationEnrichmentJobDTO => ({
  id: 51,
  name: "导师信息补全 2026-05-08",
  trigger_mode: "batch",
  status: "partially_completed",
  target_count: 3,
  completed_count: 3,
  queued_count: 0,
  running_count: 0,
  succeeded_count: 1,
  failed_count: 1,
  skipped_count: 1,
  canceled_count: 0,
  input_tokens: 1200,
  output_tokens: 300,
  cached_tokens: 400,
  total_tokens: 1500,
  llm_profile_id: 2,
  started_at: "2026-05-08T00:00:00",
  finished_at: "2026-05-08T00:01:30",
  duration_seconds: 90,
  created_at: "2026-05-08T00:00:00",
  updated_at: "2026-05-08T00:01:30",
  deleted_at: null,
  last_error: "upstream request failed: status 503",
  ...overrides,
});

const buildInformationEnrichmentItem = (
  overrides: Partial<ProfessorInformationEnrichmentItemDTO> = {},
): ProfessorInformationEnrichmentItemDTO => ({
  id: 61,
  job_id: 51,
  professor_id: 21,
  professor_name: "张老师",
  professor_email: "zhang@example.edu",
  professor_title: "教授",
  professor_university: "测试大学",
  professor_school: "计算机学院",
  professor_department: "人工智能系",
  profile_url: "https://example.edu/zhang",
  status: "succeeded",
  enriched_fields: ["email", "research_direction"],
  error_message: null,
  skip_reason: null,
  input_tokens: 1000,
  output_tokens: 200,
  cached_tokens: 300,
  total_tokens: 1200,
  attempt_count: 1,
  started_at: "2026-05-08T00:00:00",
  finished_at: "2026-05-08T00:00:30",
  created_at: "2026-05-08T00:00:00",
  updated_at: "2026-05-08T00:00:30",
  ...overrides,
});

const buildBatchItem = (
  overrides: Partial<BatchTaskItemDTO> = {},
): BatchTaskItemDTO => ({
  id: 11,
  professor_id: 21,
  professor_name: "模板直通导师",
  professor_email: "mentor@example.edu",
  professor_title: "Professor",
  professor_school: "School of Computing",
  professor_research_direction: "Human-centered AI",
  status: "approved",
  cancellation_reason: null,
  batch_send_canceled_at: null,
  can_cancel_send: false,
  can_restore_send: false,
  match_score: null,
  scheduled_at: null,
  sent_at: null,
  last_send_attempt_at: null,
  last_error: null,
  draft_generation_source: null,
  draft_fallback_reason: null,
  is_replied: false,
  updated_at: "2026-05-08T00:00:00",
  next_action: "waiting_send",
  ...overrides,
});

const buildProfessor = (
  overrides: Partial<ProfessorDTO> = {},
): ProfessorDTO => ({
  id: 21,
  name: "模板直通导师",
  email: "mentor@example.edu",
  title: "Professor",
  university: "Example University",
  school: "School of Computing",
  department: "Computer Science",
  research_direction: "Human-centered AI",
  personal_note: null,
  recent_papers: ["Recent AI paper"],
  profile_url: "https://example.edu/mentor",
  source_url: "https://example.edu/faculty",
  crawl_status: "completed",
  skip_reason: null,
  archived_at: null,
  created_at: "2026-05-08T00:00:00",
  updated_at: "2026-05-08T00:00:00",
  tags: [],
  ...overrides,
});

const buildProfessorManagementItem = (
  overrides: Partial<ProfessorManagementItemDTO> = {},
): ProfessorManagementItemDTO => {
  const professor = buildProfessor();
  return {
    ...professor,
    recent_papers: professor.recent_papers ?? [],
    ...overrides,
  };
};

const buildWorkspaceThread = (
  overrides: Partial<WorkspaceThreadDTO> = {},
): WorkspaceThreadDTO => ({
  professor: {
    id: 21,
    name: "模板直通导师",
    email: "mentor@example.edu",
    title: "Professor",
    university: "Example University",
    school: "School of Computing",
    department: "Computer Science",
    research_direction: "Human-centered AI",
    recent_papers: ["Recent AI paper"],
    profile_url: null,
  },
  identity: {
    id: 1,
    name: "默认身份",
    profile_name: "申请人",
    sender_name: "小明",
    email_address: "student@example.com",
  },
  llm_profile: {
    id: 2,
    name: "默认模型",
    provider: "openai",
    model_name: "gpt-test",
  },
  material_options: [
    {
      id: 7,
      display_name: "简历.pdf",
      original_filename: "简历.pdf",
      mime_type: "application/pdf",
      size_bytes: 2048,
      material_type: "resume",
      is_primary: true,
      created_at: "2026-05-08T00:00:00",
    },
  ],
  current_task: {
    id: 101,
    source: "batch",
    batch_task_id: 1,
    parent_task_id: null,
    status: "review_required",
    cancellation_reason: null,
    can_continue_manually: false,
    can_write_follow_up: false,
    outreach_generation_mode: "llm",
    outreach_template_subject: "模板主题",
    outreach_template_body_text: "模板正文",
    outreach_template_body_html: "<p>模板正文</p>",
    rendered_template_subject: null,
    rendered_template_body_text: null,
    rendered_template_body_html: null,
    match_score: 92,
    match_reason: "方向匹配",
    fit_points: ["方向接近"],
    risk_points: [],
    match_keywords: ["AI"],
    generated_subject: "申请与老师交流",
    generated_content_text: "老师您好，我想交流。",
    generated_content_html: "<p>老师您好，我想交流。</p>",
    draft_generation_source: "llm",
    draft_fallback_reason: null,
    approved_subject: null,
    approved_body_text: null,
    approved_body_html: null,
    primary_material_id: 7,
    primary_material: null,
    selected_material_ids: [7],
    approved_at: null,
    scheduled_at: null,
    last_send_attempt_at: null,
    sent_at: null,
    last_rfc_message_id: null,
    retry_count: 0,
    last_error: null,
    is_replied: false,
    estimated_prompt_tokens: null,
    estimated_completion_tokens_upper_bound: null,
    estimated_total_tokens_upper_bound: null,
    last_draft_prompt_tokens: null,
    last_draft_completion_tokens: null,
    last_draft_total_tokens: null,
    draft: {
      subject: "申请与老师交流",
      body_text: "老师您好，我想交流。",
      body_html: "<p>老师您好，我想交流。</p>",
      source: "ai_rewrite",
      sendable: true,
      editable: true,
    },
  },
  messages: [],
  communication_scope: [
    {
      id: 1,
      name: "默认身份",
      profile_name: "申请人",
      sender_name: "小明",
      email_address: "student@example.com",
    },
  ],
  sync_warnings: [],
  ...overrides,
});

beforeEach(() => {
  vi.clearAllMocks();
  scrollIntoView.mockReset();
  Object.defineProperty(window.HTMLElement.prototype, "scrollIntoView", {
    configurable: true,
    value: scrollIntoView,
  });
  window.localStorage.clear();
  confirmMock.mockResolvedValue(true);
  selectionMock.selectedIdentityId = 1;
  selectionMock.selectedLlmProfileId = 2;
  apiMocks.listBatchTasks.mockResolvedValue([]);
  apiMocks.listBatchTaskItems.mockResolvedValue([]);
  apiMocks.listCrawlJobs.mockResolvedValue([]);
  apiMocks.getCrawlJob.mockResolvedValue(buildCrawlJob());
  apiMocks.getCrawlJobEvents.mockResolvedValue([]);
  apiMocks.listCrawlCandidates.mockResolvedValue([]);
  apiMocks.listCrawlPages.mockResolvedValue([]);
  apiMocks.listMatchAnalysisJobs.mockResolvedValue([]);
  apiMocks.listMatchAnalysisJobItems.mockResolvedValue([]);
  apiMocks.listProfessorInformationEnrichmentJobs.mockResolvedValue([]);
  apiMocks.listProfessorInformationEnrichmentItems.mockResolvedValue([]);
  apiMocks.getProfessor.mockResolvedValue(buildProfessor());
  apiMocks.updateProfessor.mockResolvedValue(buildProfessorManagementItem());
  apiMocks.getWorkspaceThread.mockResolvedValue(buildWorkspaceThread());
  apiMocks.getBatchTaskItemThread.mockResolvedValue(buildWorkspaceThread());
  apiMocks.regenerateBatchTaskItemDraft.mockResolvedValue(buildWorkspaceThread({
    current_task: {
      ...buildWorkspaceThread().current_task,
      generated_subject: "重新生成后的主题",
      generated_content_text: "重新生成后的正文",
      generated_content_html: "<p>重新生成后的正文</p>",
    },
  }));
  apiMocks.approveBatchTaskItemDraft.mockResolvedValue(buildWorkspaceThread({
    current_task: {
      ...buildWorkspaceThread().current_task,
      status: "approved",
      approved_subject: "申请与老师交流",
      approved_body_text: "老师您好，我想交流。",
      approved_body_html: "<p>老师您好，我想交流。</p>",
      approved_at: "2026-05-08T01:00:00",
    },
  }));
  apiMocks.approveAllBatchTaskDrafts.mockResolvedValue({
    ok: true,
    approved_count: 1,
    task: buildBatchTask({
      review_required_count: 0,
      approved_count: 1,
    }),
  });
  apiMocks.approveAndSendBatchTaskItemDraft.mockResolvedValue(buildWorkspaceThread({
    current_task: {
      ...buildWorkspaceThread().current_task,
      status: "sent",
      sent_at: "2026-05-08T01:00:00",
    },
  }));
  apiMocks.regenerateDraft.mockResolvedValue(buildWorkspaceThread({
    current_task: {
      ...buildWorkspaceThread().current_task,
      generated_subject: "重新生成后的主题",
      generated_content_text: "重新生成后的正文",
      generated_content_html: "<p>重新生成后的正文</p>",
    },
  }));
  apiMocks.approveDraft.mockResolvedValue(buildWorkspaceThread({
    current_task: {
      ...buildWorkspaceThread().current_task,
      status: "approved",
      approved_subject: "申请与老师交流",
      approved_body_text: "老师您好，我想交流。",
      approved_body_html: "<p>老师您好，我想交流。</p>",
      approved_at: "2026-05-08T01:00:00",
    },
  }));
  apiMocks.approveAndSend.mockResolvedValue(buildWorkspaceThread({
    current_task: {
      ...buildWorkspaceThread().current_task,
      status: "sent",
      sent_at: "2026-05-08T01:00:00",
    },
  }));
  apiMocks.deleteBatchTaskItem.mockResolvedValue({
    ok: true,
    task: buildBatchTask({
      target_count: 0,
      review_required_count: 0,
      approved_count: 0,
    }),
  });
});

describe("TasksPage match analysis notifications", () => {
  it("tracks a retried match analysis job globally", async () => {
    const failedJob = buildMatchAnalysisJob({
      status: "partial_failed",
      succeeded_count: 0,
      failed_count: 1,
      last_error: "分析失败",
    });
    const retriedJob = buildMatchAnalysisJob({
      id: 32,
      status: "queued",
      succeeded_count: 0,
      failed_count: 0,
      started_at: null,
      finished_at: null,
      last_error: null,
    });
    apiMocks.listMatchAnalysisJobs.mockResolvedValue([failedJob]);
    apiMocks.retryFailedMatchAnalysisJob.mockResolvedValue(retriedJob);

    render(
      <MemoryRouter>
        <TasksPage />
      </MemoryRouter>,
    );

    fireEvent.click(screen.getByRole("button", { name: "匹配分析" }));
    fireEvent.click(await screen.findByRole("button", { name: "重试失败项" }));

    await waitFor(() => {
      expect(apiMocks.retryFailedMatchAnalysisJob).toHaveBeenCalledWith(31);
    });
    expect(
      backgroundTaskNotificationMocks.trackMatchAnalysisJob,
    ).toHaveBeenCalledWith(retriedJob);
  });
});

describe("TasksPage match analysis token usage", () => {
  it("shows input, output, cached, and total tokens on the card and in details", async () => {
    const job = buildMatchAnalysisJob({
      total_prompt_tokens: 1111,
      total_completion_tokens: 222,
      total_cached_tokens: 333,
      total_tokens: 1333,
    });
    apiMocks.listMatchAnalysisJobs.mockResolvedValue([job]);
    apiMocks.listMatchAnalysisJobItems.mockResolvedValue([
      buildMatchAnalysisJobItem(),
    ]);

    render(
      <MemoryRouter>
        <TasksPage />
      </MemoryRouter>,
    );

    fireEvent.click(screen.getByRole("button", { name: "匹配分析" }));
    const cardSummary = await screen.findByLabelText(
      "批量匹配分析 Token 使用汇总",
    );
    expect(within(cardSummary).getByText("1,111")).toBeInTheDocument();
    expect(within(cardSummary).getByText("222")).toBeInTheDocument();
    expect(within(cardSummary).getByText("333")).toBeInTheDocument();
    expect(within(cardSummary).getByText("1,333")).toBeInTheDocument();
    expect(cardSummary).toHaveClass("grid");
    expect(cardSummary).not.toHaveClass("inline-grid");

    fireEvent.click(
      screen.getByRole("button", {
        name: "查看匹配分析任务 批量匹配分析",
      }),
    );
    const dialog = await screen.findByRole("dialog", {
      name: "匹配分析任务详情",
    });
    expect(within(dialog).getByTestId("match-job-detail-scroll")).toHaveClass(
      "overflow-y-auto",
      "overscroll-contain",
    );
    expect(document.body.style.overflow).toBe("hidden");
    expect(document.documentElement.style.overflow).toBe("hidden");
    const detailSummary = within(dialog).getByLabelText(
      "匹配分析任务 Token 使用汇总",
    );
    expect(within(detailSummary).getByText("输入 Token")).toBeInTheDocument();
    expect(within(detailSummary).getByText("输出 Token")).toBeInTheDocument();
    expect(within(detailSummary).getByText("缓存命中")).toBeInTheDocument();
    expect(within(detailSummary).getByText("总 Token")).toBeInTheDocument();

    const detailHeaders = within(dialog).getAllByRole("columnheader");
    detailHeaders.forEach((header) => {
      expect(header).toHaveClass("align-middle");
      expect(header.parentElement?.parentElement).toHaveClass("text-center");
    });
    expect(
      within(dialog).getByRole("columnheader", { name: "Token 明细" }),
    ).toHaveClass("w-44");

    const itemSummary = await within(dialog).findByLabelText(
      "张老师 Token 使用明细",
    );
    expect(itemSummary).toHaveClass("inline-grid", "gap-x-3", "text-left");
    const itemRow = within(dialog).getByText("张老师").closest("tr");
    expect(itemRow).not.toBeNull();
    const itemCells = within(itemRow as HTMLTableRowElement).getAllByRole("cell");
    expect(itemCells[0]).toHaveClass("align-middle");
    expect(
      within(itemCells[0]).getByText("教授 / 测试大学 / 计算机学院"),
    ).toBeInTheDocument();
    [1, 2, 4, 5].forEach((index) => {
      expect(itemCells[index]).toHaveClass("text-center", "align-middle");
    });
    expect(within(itemSummary).getByText("900")).toBeInTheDocument();
    expect(within(itemSummary).getByText("100")).toBeInTheDocument();
    expect(within(itemSummary).getByText("700")).toBeInTheDocument();
    expect(within(itemSummary).getByText("1,000")).toBeInTheDocument();
  });
});

describe("TasksPage information enrichment", () => {
  it("keeps the information enrichment tab available without an identity", async () => {
    selectionMock.selectedIdentityId = null;
    selectionMock.selectedLlmProfileId = null;
    apiMocks.listProfessorInformationEnrichmentJobs.mockResolvedValue([
      buildInformationEnrichmentJob(),
    ]);

    render(
      <MemoryRouter>
        <TasksPage />
      </MemoryRouter>,
    );

    const enrichmentTab = screen.getByRole("button", { name: "信息补全" });
    expect(enrichmentTab).toBeEnabled();
    expect(screen.getByRole("button", { name: "批量邮件" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "匹配分析" })).toBeDisabled();

    fireEvent.click(enrichmentTab);

    expect(
      await screen.findByText("导师信息补全 2026-05-08"),
    ).toBeInTheDocument();
    expect(apiMocks.listProfessorInformationEnrichmentJobs).toHaveBeenCalledWith({
      view: "current",
    });
  });

  it("shows enriched fields and the original item error in the detail drawer", async () => {
    apiMocks.listProfessorInformationEnrichmentJobs.mockResolvedValue([
      buildInformationEnrichmentJob(),
    ]);
    apiMocks.listProfessorInformationEnrichmentItems.mockResolvedValue([
      buildInformationEnrichmentItem(),
      buildInformationEnrichmentItem({
        id: 62,
        professor_name: "李老师",
        status: "failed",
        enriched_fields: [],
        error_message: "browser fallback failed: net::ERR_CONNECTION_RESET",
        total_tokens: 300,
        attempt_count: 3,
      }),
    ]);

    render(
      <MemoryRouter>
        <TasksPage />
      </MemoryRouter>,
    );

    fireEvent.click(screen.getByRole("button", { name: "信息补全" }));
    const cardSummary = await screen.findByLabelText(
      "导师信息补全 2026-05-08 Token 使用汇总",
    );
    expect(within(cardSummary).getByText("1,200")).toBeInTheDocument();
    expect(within(cardSummary).getByText("300")).toBeInTheDocument();
    expect(within(cardSummary).getByText("400")).toBeInTheDocument();
    expect(within(cardSummary).getByText("1,500")).toBeInTheDocument();
    fireEvent.click(
      await screen.findByRole("button", {
        name: "查看信息补全任务 导师信息补全 2026-05-08",
      }),
    );

    const dialog = await screen.findByRole("dialog", {
      name: "信息补全任务详情",
    });
    expect(
      within(dialog).getByTestId("information-enrichment-detail-scroll"),
    ).toHaveClass("overflow-y-auto", "overscroll-contain");
    expect(document.body.style.overflow).toBe("hidden");
    expect(document.documentElement.style.overflow).toBe("hidden");
    const detailSummary = within(dialog).getByLabelText(
      "信息补全任务 Token 使用汇总",
    );
    expect(within(detailSummary).getByText("输入 Token")).toBeInTheDocument();
    expect(within(detailSummary).getByText("输出 Token")).toBeInTheDocument();
    expect(within(detailSummary).getByText("缓存命中")).toBeInTheDocument();
    expect(within(detailSummary).getByText("总 Token")).toBeInTheDocument();

    const detailHeaders = within(dialog).getAllByRole("columnheader");
    detailHeaders.forEach((header) => {
      expect(header).toHaveClass("align-middle");
      expect(header.parentElement?.parentElement).toHaveClass("text-center");
    });
    expect(
      within(dialog).getByRole("columnheader", {
        name: "Token 明细 / 尝试",
      }),
    ).toHaveClass("w-44");

    const itemSummary = within(dialog).getByLabelText("张老师 Token 使用明细");
    expect(itemSummary).toHaveClass("inline-grid", "gap-x-3", "text-left");
    const itemRow = within(dialog).getByText("张老师").closest("tr");
    expect(itemRow).not.toBeNull();
    const itemCells = within(itemRow as HTMLTableRowElement).getAllByRole("cell");
    expect(itemCells[0]).toHaveClass("align-middle");
    [1, 2, 4, 5].forEach((index) => {
      expect(itemCells[index]).toHaveClass("text-center", "align-middle");
    });
    expect(within(itemSummary).getByText("1,000")).toBeInTheDocument();
    expect(within(itemSummary).getByText("200")).toBeInTheDocument();
    expect(within(itemSummary).getByText("300")).toBeInTheDocument();
    expect(within(itemSummary).getByText("1,200")).toBeInTheDocument();
    expect(within(dialog).getByText("研究方向")).toBeInTheDocument();
    expect(
      within(dialog).getByText(
        "browser fallback failed: net::ERR_CONNECTION_RESET",
      ),
    ).toBeInTheDocument();
    expect(within(dialog).getByText("尝试 3 次")).toBeInTheDocument();
  });

  it("supports canceling and retrying failed information enrichment items", async () => {
    const runningJob = buildInformationEnrichmentJob({
      status: "running",
      completed_count: 1,
      running_count: 1,
      failed_count: 0,
      skipped_count: 0,
      last_error: null,
    });
    const canceledJob = buildInformationEnrichmentJob({
      status: "canceled",
      canceled_count: 2,
      failed_count: 0,
      last_error: null,
    });
    const retriedJob = buildInformationEnrichmentJob({
      id: 52,
      name: "导师信息补全 2026-05-08 · 失败重试",
      status: "queued",
      completed_count: 0,
      queued_count: 2,
      succeeded_count: 0,
      failed_count: 0,
      skipped_count: 0,
      canceled_count: 0,
      last_error: null,
    });
    apiMocks.listProfessorInformationEnrichmentJobs.mockResolvedValue([runningJob]);
    apiMocks.cancelProfessorInformationEnrichmentJob.mockResolvedValue({
      ok: true,
      job: canceledJob,
    });
    apiMocks.retryFailedProfessorInformationEnrichmentJob.mockResolvedValue(
      retriedJob,
    );

    render(
      <MemoryRouter>
        <TasksPage />
      </MemoryRouter>,
    );

    fireEvent.click(screen.getByRole("button", { name: "信息补全" }));
    fireEvent.click(await screen.findByRole("button", { name: "取消" }));

    await waitFor(() => {
      expect(apiMocks.cancelProfessorInformationEnrichmentJob).toHaveBeenCalledWith(
        51,
      );
    });
    expect(
      backgroundTaskNotificationMocks.stopTrackingInformationEnrichmentJob,
    ).toHaveBeenCalledWith(51);

    fireEvent.click(await screen.findByRole("button", { name: "重试失败项" }));

    await waitFor(() => {
      expect(
        apiMocks.retryFailedProfessorInformationEnrichmentJob,
      ).toHaveBeenCalledWith(51);
    });
    expect(notificationMocks.notifySuccess).toHaveBeenCalledWith(
      "已创建重试任务",
      "失败或取消项已重新加入信息补全队列。",
    );
    expect(
      backgroundTaskNotificationMocks.trackInformationEnrichmentJob,
    ).toHaveBeenCalledWith(retriedJob);
  });
});

describe("TasksPage crawl job monitor", () => {
  it("refreshes identity-scoped dashboard counts while the global crawl tab is active", async () => {
    apiMocks.listCrawlJobs.mockResolvedValue([buildCrawlJob({ status: "running" })]);
    apiMocks.listBatchTasks.mockImplementation(({ identityId, view }) => {
      if (view !== "current") {
        return Promise.resolve([]);
      }
      if (identityId === 1) {
        return Promise.resolve([
          buildBatchTask({
            id: 1,
            identity_id: 1,
            status: "running",
            review_required_count: 2,
            approved_count: 0,
          }),
        ]);
      }
      if (identityId === 2) {
        return Promise.resolve([
          buildBatchTask({
            id: 2,
            identity_id: 2,
            status: "paused",
            review_required_count: 0,
            approved_count: 0,
          }),
          buildBatchTask({
            id: 3,
            identity_id: 2,
            status: "completed",
            review_required_count: 0,
            approved_count: 0,
          }),
        ]);
      }
      return Promise.resolve([]);
    });
    apiMocks.listMatchAnalysisJobs.mockImplementation(({ identityId, view }) => {
      if (view !== "current") {
        return Promise.resolve([]);
      }
      if (identityId === 1) {
        return Promise.resolve([
          buildMatchAnalysisJob({
            id: 41,
            identity_id: 1,
            status: "queued",
          }),
        ]);
      }
      if (identityId === 2) {
        return Promise.resolve([
          buildMatchAnalysisJob({
            id: 42,
            identity_id: 2,
            status: "failed",
          }),
        ]);
      }
      return Promise.resolve([]);
    });

    const { rerender } = render(
      <MemoryRouter>
        <TasksPage />
      </MemoryRouter>,
    );

    await waitFor(() => {
      expect(apiMocks.listBatchTasks).toHaveBeenCalledWith({
        identityId: 1,
        llmProfileId: 2,
        view: "current",
      });
    });
    fireEvent.click(screen.getByRole("button", { name: "教师抓取" }));
    expect(await screen.findByText("江西财经大学 / 计算机与人工智能学院")).toBeInTheDocument();

    selectionMock.selectedIdentityId = 2;
    rerender(
      <MemoryRouter>
        <TasksPage />
      </MemoryRouter>,
    );

    await waitFor(() => {
      expect(apiMocks.listBatchTasks).toHaveBeenCalledWith({
        identityId: 2,
        llmProfileId: 2,
        view: "current",
      });
    });
    await waitFor(() => {
      const summaryCard = (label: string) => {
        const labelElement = screen
          .getAllByText(label)
          .find((element) =>
            element.parentElement?.className.includes(
              "rounded-2xl border border-stone-200 bg-white",
            ),
          );
        expect(labelElement).toBeDefined();
        return labelElement?.parentElement;
      };
      expect(summaryCard("批量邮件")).toHaveTextContent("批量邮件2");
      expect(summaryCard("运行中")).toHaveTextContent("运行中1");
      expect(summaryCard("待处理")).toHaveTextContent("待处理1");
    });
  });

  it("shows cached token usage in the realtime monitor", async () => {
    apiMocks.listCrawlJobs.mockResolvedValue([
      buildCrawlJob({
        input_tokens: 128000,
        output_tokens: 2048,
        cached_tokens: 64000,
        total_tokens: 130048,
      }),
    ]);
    apiMocks.getCrawlJob.mockResolvedValue(
      buildCrawlJob({
        input_tokens: 128000,
        output_tokens: 2048,
        cached_tokens: 64000,
        total_tokens: 130048,
      }),
    );

    render(
      <MemoryRouter>
        <TasksPage />
      </MemoryRouter>,
    );

    fireEvent.click(await screen.findByRole("button", { name: /教师抓取/ }));
    expect(await screen.findByText("江西财经大学 / 计算机与人工智能学院")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "查看详情" }));

    expect(await screen.findByText("实时抓取监控")).toBeInTheDocument();
    expect(screen.getByText("缓存命中 Token")).toBeInTheDocument();
    expect(screen.getByText("64,000")).toBeInTheDocument();
  });

});

describe("TasksPage batch draft review", () => {
  it("shows the batch template snapshot in task details", async () => {
    const task = buildBatchTask({
      name: "模板信息任务",
      outreach_template_name_snapshot: "强化学习博士申请",
      outreach_generation_mode: "llm",
    });
    apiMocks.listBatchTasks.mockResolvedValue([task]);
    apiMocks.listBatchTaskItems.mockResolvedValue([]);

    render(
      <MemoryRouter>
        <TasksPage />
      </MemoryRouter>,
    );

    expect(await screen.findByText(task.name)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "查看详情" }));

    const dialog = await screen.findByRole("dialog", { name: "批量任务详情" });
    expect(within(dialog).getByText("强化学习博士申请")).toBeInTheDocument();
    expect(within(dialog).getByText("AI 辅助写信")).toBeInTheDocument();
    expect(
      within(dialog).getByText("内容以创建任务时编辑器中的版本为准，不随模板库后续修改。"),
    ).toBeInTheDocument();
  });

  it("shows and confirms the original template when relaunching failed items", async () => {
    const task = buildBatchTask({
      name: "需要重新发起的任务",
      status: "completed",
      approved_count: 0,
      failed_count: 1,
      outreach_template_name_snapshot: "机器人方向申请模板",
      outreach_generation_mode: "llm",
    });
    apiMocks.listBatchTasks.mockResolvedValue([task]);
    apiMocks.listBatchTaskItems.mockResolvedValue([
      buildBatchItem({
        status: "send_failed",
        next_action: "send_failed",
        last_error: "连接超时",
      }),
    ]);
    apiMocks.getBatchTaskResendContext.mockResolvedValue({
      task: {
        id: task.id,
        name: task.name,
        identity_id: task.identity_id,
        schedule_type: task.schedule_type,
      },
      defaults: {
        identity_id: task.identity_id,
        outreach_template_id: task.outreach_template_id,
        outreach_template_name_snapshot: "机器人方向申请模板",
        outreach_generation_mode: "llm",
        outreach_template_subject: "申请交流",
        outreach_template_body_text: "老师您好",
        outreach_template_body_html: "<p>老师您好</p>",
        primary_material_id: null,
        selected_material_ids: [],
      },
      items: [
        {
          email_task_id: 11,
          professor_id: 21,
          professor_name: "模板直通导师",
          professor_email: "mentor@example.edu",
          status: "send_failed",
          cancellation_reason: null,
          reason_label: "发送失败",
          default_selected: true,
          selectable: true,
          unavailable_reason: null,
          updated_at: "2026-05-08T00:00:00",
        },
      ],
      summary: {
        candidate_count: 1,
        default_selected_count: 1,
        unavailable_count: 0,
      },
      warnings: [],
    });

    render(
      <MemoryRouter>
        <TasksPage />
      </MemoryRouter>,
    );

    expect(await screen.findByText(task.name)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "查看详情" }));
    fireEvent.click(
      await screen.findByRole("button", { name: "重新发起未成功项" }),
    );

    const resendDialog = await screen.findByRole("dialog", {
      name: "重新发起未成功项",
    });
    expect(within(resendDialog).getByText("机器人方向申请模板")).toBeInTheDocument();
    expect(within(resendDialog).getByText("AI 辅助写信")).toBeInTheDocument();

    fireEvent.click(
      within(resendDialog).getByRole("button", { name: "去创建新任务" }),
    );
    await waitFor(() => {
      expect(confirmMock).toHaveBeenCalledWith(
        expect.objectContaining({
          description: expect.stringContaining("发信模板：机器人方向申请模板"),
        }),
      );
    });
    expect(confirmMock).toHaveBeenCalledWith(
      expect.objectContaining({
        description: expect.stringContaining("写信方式：AI 辅助写信"),
      }),
    );
  });

  it("cancels and restores a scheduled professor on the original card", async () => {
    const scheduledAt = "2099-05-08T02:30:00Z";
    const task = buildBatchTask({
      target_count: 2,
      approved_count: 2,
    });
    const firstItem = buildBatchItem({
      id: 11,
      professor_name: "王老师",
      scheduled_at: scheduledAt,
      can_cancel_send: true,
    });
    const secondItem = buildBatchItem({
      id: 12,
      professor_name: "李老师",
      professor_email: "li@example.edu",
      scheduled_at: "2099-05-08T03:30:00Z",
      can_cancel_send: true,
    });
    const canceledItem: BatchTaskItemDTO = {
      ...firstItem,
      batch_send_canceled_at: "2099-05-07T02:30:00Z",
      can_cancel_send: false,
      can_restore_send: true,
      next_action: null,
      selected_attachment_size_bytes: 1024 * 1024 + 1,
    };
    let currentItems = [firstItem, secondItem];

    apiMocks.listBatchTasks.mockResolvedValue([task]);
    apiMocks.listBatchTaskItems.mockImplementation(async () => currentItems);
    apiMocks.cancelBatchTaskItemSend.mockImplementation(async () => {
      currentItems = [canceledItem, secondItem];
      return {
        ok: true,
        task: buildBatchTask({
          target_count: 2,
          approved_count: 1,
          canceled_send_count: 1,
        }),
      };
    });
    apiMocks.restoreBatchTaskItemSend.mockImplementation(async () => {
      currentItems = [firstItem, secondItem];
      return { ok: true, task };
    });

    render(
      <MemoryRouter>
        <TasksPage />
      </MemoryRouter>,
    );

    fireEvent.click(await screen.findByRole("button", { name: "查看详情" }));
    const dialog = await screen.findByRole("dialog", { name: "批量任务详情" });
    const firstCard = await within(dialog).findByTestId("batch-task-item-11");
    const secondCard = within(dialog).getByTestId("batch-task-item-12");
    expect(firstCard.compareDocumentPosition(secondCard)).toBe(
      Node.DOCUMENT_POSITION_FOLLOWING,
    );

    fireEvent.click(within(firstCard).getByRole("button", { name: "取消发送" }));

    await waitFor(() => {
      expect(apiMocks.cancelBatchTaskItemSend).toHaveBeenCalledWith(task.id, firstItem.id);
    });
    expect(confirmMock).toHaveBeenCalledWith({
      title: "取消给王老师的本次发送？",
      description: expect.stringContaining("不影响批次中的其他导师。之后可在原卡片上恢复。"),
      confirmLabel: "确认取消发送",
      cancelLabel: "保留发送",
      tone: "danger",
    });

    const canceledCard = await within(dialog).findByTestId("batch-task-item-11");
    expect(within(canceledCard).getByText("已取消发送")).toBeInTheDocument();
    expect(within(canceledCard).getByText("该导师不会收到本次邮件")).toBeInTheDocument();
    expect(canceledCard.compareDocumentPosition(secondCard)).toBe(
      Node.DOCUMENT_POSITION_FOLLOWING,
    );

    fireEvent.click(
      within(canceledCard).getByRole("button", { name: "恢复发送" }),
    );

    await waitFor(() => {
      expect(apiMocks.restoreBatchTaskItemSend).toHaveBeenCalledWith(task.id, firstItem.id);
    });
    expect(confirmMock).toHaveBeenCalledTimes(2);
    expect(confirmMock).toHaveBeenLastCalledWith(
      expect.objectContaining({
        title: "附件超过 1 MB，仍要恢复发送吗？",
        description: expect.stringContaining(
          "建议不超过 1 MB，以减少被邮箱提供商限流的概率。",
        ),
        confirmLabel: "仍然恢复",
        cancelLabel: "保持取消",
      }),
    );
    expect(
      within(await within(dialog).findByTestId("batch-task-item-11")).getByRole(
        "button",
        { name: "取消发送" },
      ),
    ).toBeInTheDocument();
  });

  it("keeps an expired cancellation marked without a restore button", async () => {
    const task = buildBatchTask({ canceled_send_count: 1, approved_count: 0 });
    const item = buildBatchItem({
      professor_name: "已过期导师",
      scheduled_at: "2000-05-08T02:30:00Z",
      batch_send_canceled_at: "2000-05-07T02:30:00Z",
      can_cancel_send: false,
      can_restore_send: true,
      next_action: null,
    });
    apiMocks.listBatchTasks.mockResolvedValue([task]);
    apiMocks.listBatchTaskItems.mockResolvedValue([item]);

    render(
      <MemoryRouter>
        <TasksPage />
      </MemoryRouter>,
    );

    fireEvent.click(await screen.findByRole("button", { name: "查看详情" }));
    const dialog = await screen.findByRole("dialog", { name: "批量任务详情" });
    const card = await within(dialog).findByTestId(`batch-task-item-${item.id}`);

    expect(within(card).getByText("已取消发送")).toBeInTheDocument();
    expect(within(card).getByText("原定发送时间已过，无法恢复")).toBeInTheDocument();
    expect(within(card).queryByRole("button", { name: "恢复发送" })).not.toBeInTheDocument();
  });

  it("paginates large sent item lists in batch task details", async () => {
    const task = buildBatchTask({
      name: "超大批量任务",
      target_count: 2000,
      completed_count: 2000,
      sent_count: 2000,
      replied_count: 0,
    });
    const items = Array.from({ length: 2000 }, (_, index) =>
      buildBatchItem({
        id: index + 1,
        professor_id: index + 1,
        professor_name: `已发送导师 ${index + 1}`,
        status: "sent",
        sent_at: "2026-05-08T01:00:00",
      }),
    );
    apiMocks.listBatchTasks.mockResolvedValue([task]);
    apiMocks.listBatchTaskItems.mockResolvedValue(items);

    render(
      <MemoryRouter>
        <TasksPage />
      </MemoryRouter>,
    );

    expect(await screen.findByText("超大批量任务")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "查看详情" }));

    expect(await screen.findByText("已发送导师 1")).toBeInTheDocument();
    expect(screen.getByText("已发送导师 20")).toBeInTheDocument();
    expect(screen.queryByText("已发送导师 21")).not.toBeInTheDocument();
    expect(screen.getByText("显示 1-20 / 2000 个任务")).toBeInTheDocument();

    const pagination = screen.getByRole("navigation", {
      name: "已发送导师分页",
    });
    fireEvent.click(
      within(pagination).getByRole("button", { name: "下一页" }),
    );

    expect(await screen.findByText("已发送导师 21")).toBeInTheDocument();
    expect(
      screen.getByRole("region", { name: "已发送导师列表" }),
    ).toHaveFocus();
    expect(scrollIntoView).toHaveBeenCalledWith({
      behavior: "auto",
      block: "start",
    });
  });

  it("preserves batch list and detail pagination when Activity hides and shows the page again", async () => {
    const tasks = Array.from({ length: 9 }, (_, index) =>
      buildBatchTask({
        id: index + 1,
        name: `批量邮件任务 ${index + 1}`,
      }),
    );
    const selectedTask = tasks[8];
    const sentItems = Array.from({ length: 21 }, (_, index) =>
      buildBatchItem({
        id: index + 1,
        professor_id: index + 1,
        professor_name: `已发送导师 ${index + 1}`,
        status: "sent",
        sent_at: "2026-05-08T01:00:00",
      }),
    );
    apiMocks.listBatchTasks.mockResolvedValue(tasks);
    apiMocks.listBatchTaskItems.mockResolvedValue(sentItems);

    const { rerender } = render(
      <MemoryRouter>
        <ActivityHarness mode="visible">
          <TasksPage />
        </ActivityHarness>
      </MemoryRouter>,
    );

    expect(await screen.findByText("批量邮件任务 1")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "下一页" }));

    expect(await screen.findByText("批量邮件任务 9")).toBeInTheDocument();
    expect(screen.getByText("显示 9-9 / 9 个任务")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "查看详情" }));
    const dialog = await screen.findByRole("dialog", { name: "批量任务详情" });
    expect(within(dialog).getByText(selectedTask.name)).toBeInTheDocument();
    expect(within(dialog).getByText("已发送导师 1")).toBeInTheDocument();

    fireEvent.click(within(dialog).getByRole("button", { name: "下一页" }));
    expect(await within(dialog).findByText("已发送导师 21")).toBeInTheDocument();
    expect(within(dialog).getByText("显示 21-21 / 21 个任务")).toBeInTheDocument();

    rerender(
      <MemoryRouter>
        <ActivityHarness mode="hidden">
          <TasksPage />
        </ActivityHarness>
      </MemoryRouter>,
    );
    rerender(
      <MemoryRouter>
        <ActivityHarness mode="visible">
          <TasksPage />
        </ActivityHarness>
      </MemoryRouter>,
    );

    expect(await screen.findByText("批量邮件任务 9")).toBeInTheDocument();
    expect(screen.getByText("显示 9-9 / 9 个任务")).toBeInTheDocument();

    const restoredDialog = await screen.findByRole("dialog", {
      name: "批量任务详情",
    });
    expect(within(restoredDialog).getByText(selectedTask.name)).toBeInTheDocument();
    expect(within(restoredDialog).getByText("已发送导师 21")).toBeInTheDocument();
    expect(within(restoredDialog).getByText("显示 21-21 / 21 个任务")).toBeInTheDocument();
  });

  it("opens the generated draft inside the existing batch detail panel", async () => {
    const task = buildBatchTask({
      name: "AI 改写批量任务",
      schedule_type: "scheduled",
      review_required_count: 1,
      approved_count: 0,
    });
    const item = buildBatchItem({
      status: "review_required",
      next_action: "review_draft",
      match_score: 92,
    });
    apiMocks.listBatchTasks.mockResolvedValue([task]);
    apiMocks.listBatchTaskItems.mockResolvedValue([item]);
    const thread = buildWorkspaceThread();
    apiMocks.getBatchTaskItemThread.mockResolvedValue(buildWorkspaceThread({
      professor: {
        ...thread.professor,
        profile_url: "https://example.edu/mentor",
      },
      current_task: {
        ...thread.current_task,
        id: item.id,
        batch_task_id: task.id,
      },
    }));

    render(
      <MemoryRouter>
        <TasksPage />
      </MemoryRouter>,
    );

    expect(await screen.findByText("AI 改写批量任务")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "查看详情" }));
    expect(await screen.findByText("还未发送给")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "审核草稿" }));

    await waitFor(() => {
      expect(apiMocks.getBatchTaskItemThread).toHaveBeenCalledWith(task.id, item.id);
    });
    expect(apiMocks.getWorkspaceThread).not.toHaveBeenCalled();
    expect(await screen.findByText("批量审核草稿")).toBeInTheDocument();
    expect(screen.getByLabelText("邮件主题")).toHaveValue("申请与老师交流");
    expect(screen.getByLabelText("邮件正文")).toHaveValue(
      "<p>老师您好，我想交流。</p>",
    );
    expect(screen.getByRole("button", { name: "审核通过" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "立即发送" })).not.toBeInTheDocument();

    const attachmentCard = screen.getByRole("region", { name: "随信附件" });
    const reviewCard = screen.getByRole("region", { name: "审核操作" });
    const professorCard = screen.getByRole("region", { name: "老师详情" });
    const matchCard = screen.getByRole("region", { name: "匹配摘要" });
    expect(attachmentCard.nextElementSibling).toBe(reviewCard);
    expect(reviewCard.nextElementSibling).toBe(professorCard);
    expect(professorCard.nextElementSibling).toBe(matchCard);

    expect(within(professorCard).getByText("学校")).toBeInTheDocument();
    expect(within(professorCard).getByText("Example University")).toBeInTheDocument();
    expect(within(professorCard).getByText("学院")).toBeInTheDocument();
    expect(within(professorCard).getByText("School of Computing")).toBeInTheDocument();
    expect(within(professorCard).getByText("系所")).toBeInTheDocument();
    expect(within(professorCard).getByText("Computer Science")).toBeInTheDocument();
    expect(within(professorCard).getByText("研究方向")).toBeInTheDocument();
    expect(within(professorCard).getByText("Human-centered AI")).toBeInTheDocument();
    expect(within(professorCard).getByText("主页链接")).toBeInTheDocument();
    expect(
      within(professorCard).getByRole("link", { name: "https://example.edu/mentor" }),
    ).toHaveAttribute("href", "https://example.edu/mentor");
  });

  it("reviews template fallback drafts and blocks AI rewrite without research direction", async () => {
    const task = buildBatchTask({
      name: "AI 模板降级任务",
      schedule_type: "immediate",
      outreach_generation_mode: "llm",
      review_required_count: 1,
      approved_count: 0,
    });
    const item = buildBatchItem({
      id: 61,
      professor_name: "缺研究方向导师",
      status: "review_required",
      next_action: "review_draft",
      draft_generation_source: "template_fallback",
      draft_fallback_reason: "missing_research_direction",
    });
    const fallbackThread = buildWorkspaceThread({
      professor: {
        ...buildWorkspaceThread().professor,
        name: "缺研究方向导师",
        research_direction: null,
      },
      current_task: {
        ...buildWorkspaceThread().current_task,
        id: item.id,
        batch_task_id: task.id,
        outreach_template_body_text:
          "关注到您在{{research_direction}}方向的工作。",
        generated_subject: "申请与缺研究方向导师老师交流",
        generated_content_text: "缺研究方向导师老师您好，我是申请人。",
        generated_content_html: "<p>缺研究方向导师老师您好，我是申请人。</p>",
        draft_generation_source: "template_fallback",
        draft_fallback_reason: "missing_research_direction",
      },
    });
    apiMocks.listBatchTasks.mockResolvedValue([task]);
    apiMocks.listBatchTaskItems.mockResolvedValue([item]);
    apiMocks.getBatchTaskItemThread.mockResolvedValue(fallbackThread);
    apiMocks.getProfessor.mockResolvedValue(
      buildProfessor({
        name: "缺研究方向导师",
        email: "mentor@example.edu",
        research_direction: null,
      }),
    );
    apiMocks.approveBatchTaskItemDraft.mockResolvedValue(
      buildWorkspaceThread({
        ...fallbackThread,
        current_task: {
          ...fallbackThread.current_task,
          status: "approved",
        },
      }),
    );

    render(
      <MemoryRouter>
        <TasksPage />
      </MemoryRouter>,
    );

    expect(await screen.findByText(task.name)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "查看详情" }));
    expect(
      await screen.findByText(/其中 1 封因导师缺少研究方向/),
    ).toBeInTheDocument();
    fireEvent.click(await screen.findByRole("button", { name: "审核草稿" }));
    expect(await screen.findByText("未进行 AI 改写")).toBeInTheDocument();

    const fallbackNotice = await screen.findByRole("region", {
      name: "未进行 AI 改写提示",
    });
    expect(fallbackNotice).toHaveTextContent(
      "该导师缺少研究方向，系统已直接使用「博士申请模板」模板生成草稿",
    );
    expect(fallbackNotice).toHaveTextContent(
      "模板中的研究方向变量为空，请重点检查相关语句",
    );
    expect(
      within(fallbackNotice).getByRole("button", { name: "补充资料" }),
    ).toBeInTheDocument();

    fireEvent.click(
      within(fallbackNotice).getByRole("button", { name: "补充资料" }),
    );
    const editDialog = await screen.findByRole("dialog", {
      name: "补充导师资料：缺研究方向导师",
    });
    expect(within(editDialog).getByLabelText("研究方向")).toHaveValue("");
    fireEvent.click(within(editDialog).getByRole("button", { name: "取消" }));

    confirmMock.mockClear();
    fireEvent.click(screen.getByRole("button", { name: "使用 AI 改写" }));
    expect(notificationMocks.notifyError).toHaveBeenCalledWith(
      "无法使用 AI 改写",
      "该导师缺少研究方向。当前模板草稿不会受到影响，你可以直接审核，或先补全导师资料。",
    );
    expect(confirmMock).not.toHaveBeenCalled();
    expect(apiMocks.regenerateBatchTaskItemDraft).not.toHaveBeenCalled();
    expect(screen.getByLabelText("邮件正文")).toHaveValue(
      "<p>缺研究方向导师老师您好，我是申请人。</p>",
    );

    fireEvent.click(screen.getByRole("button", { name: "审核通过" }));
    await waitFor(() => {
      expect(apiMocks.approveBatchTaskItemDraft).toHaveBeenCalledWith(
        task.id,
        item.id,
        expect.objectContaining({
          subject: "申请与缺研究方向导师老师交流",
          body_text: "缺研究方向导师老师您好，我是申请人。",
        }),
      );
    });
  });

  it("allows AI rewrite immediately after completing research direction inline", async () => {
    const task = buildBatchTask({
      name: "AI 模板补充资料任务",
      schedule_type: "immediate",
      outreach_generation_mode: "llm",
      review_required_count: 1,
      approved_count: 0,
    });
    const item = buildBatchItem({
      id: 62,
      professor_name: "待补充导师",
      professor_research_direction: null,
      status: "review_required",
      next_action: "review_draft",
      draft_generation_source: "template_fallback",
      draft_fallback_reason: "missing_research_direction",
    });
    const fallbackThread = buildWorkspaceThread({
      professor: {
        ...buildWorkspaceThread().professor,
        name: item.professor_name,
        research_direction: null,
      },
      current_task: {
        ...buildWorkspaceThread().current_task,
        id: item.id,
        batch_task_id: task.id,
        draft_generation_source: "template_fallback",
        draft_fallback_reason: "missing_research_direction",
      },
    });
    const completedProfessor = buildProfessorManagementItem({
      name: item.professor_name,
      research_direction: "Machine Learning Systems",
    });
    apiMocks.listBatchTasks.mockResolvedValue([task]);
    apiMocks.listBatchTaskItems.mockResolvedValue([item]);
    apiMocks.getBatchTaskItemThread.mockResolvedValue(fallbackThread);
    apiMocks.getProfessor.mockResolvedValue(
      buildProfessor({
        name: item.professor_name,
        research_direction: null,
      }),
    );
    apiMocks.updateProfessor.mockResolvedValue(completedProfessor);
    apiMocks.regenerateBatchTaskItemDraft.mockResolvedValue(
      buildWorkspaceThread({
        professor: {
          ...fallbackThread.professor,
          research_direction: completedProfessor.research_direction,
        },
        current_task: {
          ...fallbackThread.current_task,
          draft_generation_source: "llm",
          draft_fallback_reason: null,
          generated_subject: "AI 改写后的主题",
          generated_content_text: "AI 改写后的正文",
          generated_content_html: "<p>AI 改写后的正文</p>",
        },
      }),
    );

    render(
      <MemoryRouter>
        <TasksPage />
      </MemoryRouter>,
    );

    expect(await screen.findByText(task.name)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "查看详情" }));
    fireEvent.click(await screen.findByRole("button", { name: "审核草稿" }));
    const fallbackNotice = await screen.findByRole("region", {
      name: "未进行 AI 改写提示",
    });
    fireEvent.click(
      within(fallbackNotice).getByRole("button", { name: "补充资料" }),
    );

    const editDialog = await screen.findByRole("dialog", {
      name: `补充导师资料：${item.professor_name}`,
    });
    fireEvent.change(within(editDialog).getByLabelText("研究方向"), {
      target: { value: "Machine Learning Systems" },
    });
    fireEvent.click(within(editDialog).getByRole("button", { name: "保存导师" }));

    expect(
      await within(fallbackNotice).findByText(/导师资料现已补充/),
    ).toBeInTheDocument();
    expect(
      within(fallbackNotice).queryByRole("button", { name: "补充资料" }),
    ).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "使用 AI 改写" }));
    await waitFor(() => {
      expect(apiMocks.regenerateBatchTaskItemDraft).toHaveBeenCalledWith(
        task.id,
        item.id,
      );
    });
    expect(confirmMock).toHaveBeenCalledWith(
      expect.objectContaining({ title: "确认使用 AI 改写？" }),
    );
  });

  it("keeps the current draft visible until the next professor is ready", async () => {
    const task = buildBatchTask({
      name: "无感切换批量任务",
      review_required_count: 2,
      approved_count: 0,
    });
    const firstItem = buildBatchItem({
      id: 11,
      professor_id: 21,
      professor_name: "第一位导师",
      status: "review_required",
      next_action: "review_draft",
    });
    const secondItem = buildBatchItem({
      id: 12,
      professor_id: 22,
      professor_name: "第二位导师",
      status: "review_required",
      next_action: "review_draft",
    });
    const firstThread = buildWorkspaceThread({
      professor: {
        ...buildWorkspaceThread().professor,
        id: 21,
        name: "第一位导师",
      },
      current_task: {
        ...buildWorkspaceThread().current_task,
        id: 11,
        batch_task_id: task.id,
        generated_subject: "第一封主题",
        generated_content_text: "第一封正文",
        generated_content_html: "<p>第一封正文</p>",
      },
    });
    const secondThread = buildWorkspaceThread({
      professor: {
        ...buildWorkspaceThread().professor,
        id: 22,
        name: "第二位导师",
        university: "Second University",
        school: "Second School",
        department: "Second Department",
        research_direction: "Second Research Direction",
        profile_url: "https://example.edu/second-mentor",
      },
      material_options: [
        {
          ...buildWorkspaceThread().material_options[0],
          id: 8,
          display_name: "第二位导师附件.pdf",
          original_filename: "第二位导师附件.pdf",
        },
      ],
      current_task: {
        ...buildWorkspaceThread().current_task,
        id: 12,
        batch_task_id: task.id,
        match_score: 77,
        match_reason: "第二位导师匹配摘要",
        generated_subject: "第二封主题",
        generated_content_text: "第二封正文",
        generated_content_html: "<p>第二封正文</p>",
        selected_material_ids: [8],
      },
    });
    let finishSecondLoad: (thread: WorkspaceThreadDTO) => void;
    const secondLoad = new Promise<WorkspaceThreadDTO>((resolve) => {
      finishSecondLoad = resolve;
    });
    apiMocks.listBatchTasks.mockResolvedValue([task]);
    apiMocks.listBatchTaskItems.mockResolvedValue([firstItem, secondItem]);
    apiMocks.getBatchTaskItemThread
      .mockResolvedValueOnce(firstThread)
      .mockReturnValueOnce(secondLoad);

    render(
      <MemoryRouter>
        <TasksPage />
      </MemoryRouter>,
    );

    expect(await screen.findByText(task.name)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "查看详情" }));
    fireEvent.click((await screen.findAllByRole("button", { name: "审核草稿" }))[0]);
    expect(await screen.findByLabelText("邮件主题")).toHaveValue("第一封主题");

    fireEvent.click(screen.getByRole("button", { name: /第二位导师/ }));
    await waitFor(() => {
      expect(apiMocks.getBatchTaskItemThread).toHaveBeenLastCalledWith(task.id, 12);
    });
    expect(screen.queryByText("正在加载草稿...")).not.toBeInTheDocument();
    expect(screen.getByLabelText("邮件主题")).toHaveValue("第一封主题");
    expect(screen.getByLabelText("邮件正文")).toHaveValue("<p>第一封正文</p>");
    expect(screen.getByText(`${task.name} · 第一位导师`)).toBeInTheDocument();
    const firstSubjectEditor = screen.getByLabelText("邮件主题");
    const firstBodyEditor = screen.getByLabelText("邮件正文");

    finishSecondLoad!(secondThread);
    expect(await screen.findByDisplayValue("第二封主题")).toBeInTheDocument();
    expect(screen.getByLabelText("邮件正文")).toHaveValue("<p>第二封正文</p>");
    expect(screen.getByLabelText("邮件主题")).not.toBe(firstSubjectEditor);
    expect(screen.getByLabelText("邮件正文")).not.toBe(firstBodyEditor);
    expect(screen.getByText(`${task.name} · 第二位导师`)).toBeInTheDocument();

    const attachmentCard = screen.getByRole("region", { name: "随信附件" });
    expect(within(attachmentCard).getByText("第二位导师附件.pdf")).toBeInTheDocument();
    expect(within(attachmentCard).getByRole("checkbox")).toBeChecked();

    const professorCard = screen.getByRole("region", { name: "老师详情" });
    expect(within(professorCard).getByText("Second University")).toBeInTheDocument();
    expect(within(professorCard).getByText("Second School")).toBeInTheDocument();
    expect(within(professorCard).getByText("Second Department")).toBeInTheDocument();
    expect(within(professorCard).getByText("Second Research Direction")).toBeInTheDocument();
    expect(
      within(professorCard).getByRole("link", {
        name: "https://example.edu/second-mentor",
      }),
    ).toBeInTheDocument();

    const matchCard = screen.getByRole("region", { name: "匹配摘要" });
    expect(within(matchCard).getByText("匹配分 77")).toBeInTheDocument();
    expect(within(matchCard).getByText("第二位导师匹配摘要")).toBeInTheDocument();
  });

  it("regenerates and deletes batch review drafts from the review panel", async () => {
    const task = buildBatchTask({
      name: "AI 改写批量任务",
      schedule_type: "immediate",
      target_count: 2,
      review_required_count: 2,
      approved_count: 0,
    });
    const firstItem = buildBatchItem({
      id: 11,
      professor_id: 21,
      professor_name: "第一位导师",
      status: "review_required",
      next_action: "review_draft",
    });
    const secondItem = buildBatchItem({
      id: 12,
      professor_id: 22,
      professor_name: "第二位导师",
      status: "review_required",
      next_action: "review_draft",
    });
    const regeneratingFirstItem = {
      ...firstItem,
      status: "generating_draft" as const,
      next_action: null,
    };
    const firstThread = buildWorkspaceThread({
      professor: {
        ...buildWorkspaceThread().professor,
        profile_url: "   ",
      },
      current_task: {
        ...buildWorkspaceThread().current_task,
        id: 11,
        batch_task_id: task.id,
      },
    });
    const secondThread = buildWorkspaceThread({
      professor: {
        ...buildWorkspaceThread().professor,
        id: 22,
        name: "第二位导师",
      },
      current_task: {
        ...buildWorkspaceThread().current_task,
        id: 12,
        batch_task_id: task.id,
      },
    });
    apiMocks.listBatchTasks.mockResolvedValue([task]);
    apiMocks.listBatchTaskItems
      .mockResolvedValueOnce([firstItem, secondItem])
      .mockResolvedValueOnce([regeneratingFirstItem, secondItem])
      .mockResolvedValueOnce([regeneratingFirstItem, secondItem])
      .mockResolvedValueOnce([regeneratingFirstItem]);
    apiMocks.getBatchTaskItemThread
      .mockResolvedValueOnce(firstThread)
      .mockResolvedValueOnce(secondThread);
    let finishRegeneration: (thread: ReturnType<typeof buildWorkspaceThread>) => void;
    const regeneratingDraft = new Promise<ReturnType<typeof buildWorkspaceThread>>(
      (resolve) => {
        finishRegeneration = resolve;
      },
    );
    let finishSecondRegeneration: (thread: ReturnType<typeof buildWorkspaceThread>) => void;
    const secondRegeneratingDraft = new Promise<ReturnType<typeof buildWorkspaceThread>>(
      (resolve) => {
        finishSecondRegeneration = resolve;
      },
    );

    render(
      <MemoryRouter>
        <TasksPage />
      </MemoryRouter>,
    );

    expect(await screen.findByText("AI 改写批量任务")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "查看详情" }));
    fireEvent.click((await screen.findAllByRole("button", { name: "审核草稿" }))[0]);

    const professorCard = await screen.findByRole("region", { name: "老师详情" });
    expect(within(professorCard).queryByText("主页链接")).not.toBeInTheDocument();

    confirmMock.mockResolvedValueOnce(false);
    fireEvent.click(await screen.findByRole("button", { name: "重新生成" }));
    await waitFor(() => {
      expect(confirmMock).toHaveBeenCalledWith(
        expect.objectContaining({
          title: "确认重新生成草稿？",
          description: "重新生成后会覆盖当前草稿内容，原草稿将无法保留。",
          confirmLabel: "确认重新生成",
          cancelLabel: "先不重新生成",
        }),
      );
    });
    expect(apiMocks.regenerateDraft).not.toHaveBeenCalled();

    confirmMock.mockResolvedValueOnce(true);
    apiMocks.regenerateBatchTaskItemDraft.mockReturnValueOnce(regeneratingDraft);
    fireEvent.click(screen.getByRole("button", { name: "重新生成" }));

    await waitFor(() => {
      expect(apiMocks.regenerateBatchTaskItemDraft).toHaveBeenCalledWith(1, 11);
    });
    expect(screen.getByRole("button", { name: "审核通过" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "立即发送" })).toBeDisabled();
    expect(screen.getAllByRole("button", { name: "删除草稿" })[0]).toBeDisabled();
    expect(screen.getAllByRole("button", { name: "删除草稿" })[1]).not.toBeDisabled();

    fireEvent.click(screen.getByRole("button", { name: /第二位导师/ }));
    expect(await screen.findByRole("button", { name: "审核通过" })).not.toBeDisabled();

    confirmMock.mockResolvedValueOnce(true);
    apiMocks.regenerateBatchTaskItemDraft.mockReturnValueOnce(secondRegeneratingDraft);
    fireEvent.click(screen.getByRole("button", { name: "重新生成" }));

    await waitFor(() => {
      expect(apiMocks.regenerateBatchTaskItemDraft).toHaveBeenCalledWith(1, 12);
    });
    const deleteButtonsWhileBothRegenerate = screen.getAllByRole("button", {
      name: "删除草稿",
    });
    expect(deleteButtonsWhileBothRegenerate[0]).toBeDisabled();
    expect(deleteButtonsWhileBothRegenerate[1]).toBeDisabled();

    finishRegeneration!(buildWorkspaceThread({
      current_task: {
        ...buildWorkspaceThread().current_task,
        id: 11,
        batch_task_id: task.id,
        generated_subject: "重新生成后的主题",
        generated_content_text: "重新生成后的正文",
        generated_content_html: "<p>重新生成后的正文</p>",
      },
    }));
    await waitFor(() => {
      expect(notificationMocks.notifySuccess).toHaveBeenCalledWith("草稿已重新生成");
    });
    expect(screen.getByText("第一位导师")).toBeInTheDocument();
    expect(screen.getAllByText("重新生成中")).toHaveLength(2);
    expect(screen.getByDisplayValue("<p>老师您好，我想交流。</p>")).toBeInTheDocument();

    finishSecondRegeneration!(buildWorkspaceThread({
      current_task: {
        ...buildWorkspaceThread().current_task,
        id: 12,
        batch_task_id: task.id,
        generated_subject: "第二封重新生成后的主题",
        generated_content_text: "第二封重新生成后的正文",
        generated_content_html: "<p>第二封重新生成后的正文</p>",
      },
    }));
    expect(await screen.findByDisplayValue("<p>第二封重新生成后的正文</p>")).toBeInTheDocument();

    confirmMock.mockClear();
    confirmMock.mockResolvedValueOnce(false);
    fireEvent.click(screen.getAllByRole("button", { name: "删除草稿" })[1]);
    await waitFor(() => {
      expect(confirmMock).toHaveBeenCalledWith(
        expect.objectContaining({
          title: "从批量任务中删除这封草稿？",
          description: "删除后会从当前批量任务中彻底移除这位导师和对应草稿记录。",
          confirmLabel: "删除草稿",
          cancelLabel: "先保留",
          tone: "danger",
        }),
      );
    });
    expect(apiMocks.deleteBatchTaskItem).not.toHaveBeenCalled();

    confirmMock.mockResolvedValueOnce(true);
    fireEvent.click(screen.getAllByRole("button", { name: "删除草稿" })[1]);

    await waitFor(() => {
      expect(apiMocks.deleteBatchTaskItem).toHaveBeenCalledWith(1, 12);
    });
    expect(notificationMocks.notifySuccess).toHaveBeenCalledWith("草稿已从批量任务中移除");
    expect(screen.queryByText("第二位导师")).not.toBeInTheDocument();
  });

  it("bulk approves all current review drafts only after the required confirmation", async () => {
    const task = buildBatchTask({
      name: "AI 批量审核任务",
      schedule_type: "immediate",
      outreach_generation_mode: "llm",
      target_count: 3,
      generating_draft_count: 1,
      review_required_count: 2,
      approved_count: 0,
    });
    const firstItem = buildBatchItem({
      id: 41,
      professor_name: "第一位待审核导师",
      status: "review_required",
      next_action: "review_draft",
      draft_generation_source: "template_fallback",
      draft_fallback_reason: "missing_research_direction",
      selected_attachment_size_bytes: 1024 * 1024 + 1,
    });
    const secondItem = buildBatchItem({
      id: 42,
      professor_name: "第二位待审核导师",
      status: "review_required",
      next_action: "review_draft",
      selected_attachment_size_bytes: 1024 * 1024 + 1,
    });
    const generatingItem = buildBatchItem({
      id: 43,
      professor_name: "生成中的导师",
      status: "generating_draft",
      next_action: "waiting_draft_generation",
    });
    const approvedTask = buildBatchTask({
      ...task,
      generating_draft_count: 1,
      review_required_count: 0,
      approved_count: 2,
    });
    apiMocks.listBatchTasks.mockResolvedValue([task]);
    apiMocks.listBatchTaskItems.mockResolvedValue([
      firstItem,
      secondItem,
      generatingItem,
    ]);
    apiMocks.approveAllBatchTaskDrafts.mockResolvedValue({
      ok: true,
      approved_count: 2,
      task: approvedTask,
    });

    render(
      <MemoryRouter>
        <TasksPage />
      </MemoryRouter>,
    );

    expect(await screen.findByText("AI 批量审核任务")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "查看详情" }));
    fireEvent.click(
      await screen.findByRole("button", { name: "全部通过审核（2 封）" }),
    );

    await waitFor(() => {
      expect(confirmMock).toHaveBeenCalledWith(
        expect.objectContaining({
          title: "确认全部通过这 2 封草稿？",
          description: expect.stringContaining("确认后会立即进入发送队列"),
          confirmLabel: "仍然全部通过",
          cancelLabel: "继续逐封审核",
          tone: "danger",
        }),
      );
    });
    expect(confirmMock.mock.calls[0][0].description).toContain(
      "生成中或生成失败的邮件不会被处理",
    );
    expect(confirmMock.mock.calls[0][0].description).toContain(
      "其中 1 封因导师缺少研究方向",
    );
    expect(confirmMock.mock.calls[0][0].description).toContain(
      "建议不超过 1 MB，以减少被邮箱提供商限流的概率。",
    );
    await waitFor(() => {
      expect(apiMocks.approveAllBatchTaskDrafts).toHaveBeenCalledWith(
        task.id,
        [firstItem.id, secondItem.id],
      );
    });
    expect(notificationMocks.notifySuccess).toHaveBeenCalledWith(
      "已通过 2 封草稿",
      "邮件已进入发送队列。",
    );
  });

  it("keeps all batch review drafts pending when bulk approval is canceled", async () => {
    const task = buildBatchTask({
      name: "取消批量审核任务",
      schedule_type: "scheduled",
      outreach_generation_mode: "llm",
      review_required_count: 1,
      approved_count: 0,
    });
    const item = buildBatchItem({
      id: 51,
      status: "review_required",
      next_action: "review_draft",
    });
    apiMocks.listBatchTasks.mockResolvedValue([task]);
    apiMocks.listBatchTaskItems.mockResolvedValue([item]);
    confirmMock.mockResolvedValueOnce(false);

    render(
      <MemoryRouter>
        <TasksPage />
      </MemoryRouter>,
    );

    expect(await screen.findByText("取消批量审核任务")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "查看详情" }));
    fireEvent.click(
      await screen.findByRole("button", { name: "全部通过审核（1 封）" }),
    );

    await waitFor(() => {
      expect(confirmMock).toHaveBeenCalledWith(
        expect.objectContaining({
          description: expect.stringContaining("按原计划"),
        }),
      );
    });
    expect(apiMocks.approveAllBatchTaskDrafts).not.toHaveBeenCalled();
  });

  it("approves batch review drafts through scoped batch item APIs", async () => {
    const task = buildBatchTask({
      name: "审核批量任务",
      schedule_type: "immediate",
      review_required_count: 1,
      approved_count: 0,
    });
    const item = buildBatchItem({
      id: 31,
      professor_id: 21,
      status: "review_required",
      next_action: "review_draft",
    });
    apiMocks.listBatchTasks.mockResolvedValue([task]);
    apiMocks.listBatchTaskItems.mockResolvedValue([item]);
    apiMocks.getBatchTaskItemThread.mockResolvedValue(buildWorkspaceThread({
      current_task: {
        ...buildWorkspaceThread().current_task,
        id: item.id,
        batch_task_id: task.id,
      },
    }));
    apiMocks.approveBatchTaskItemDraft.mockResolvedValue(buildWorkspaceThread({
      current_task: {
        ...buildWorkspaceThread().current_task,
        id: item.id,
        batch_task_id: task.id,
        status: "approved",
        approved_subject: "申请与老师交流",
        approved_body_text: "老师您好，我想交流。",
        approved_body_html: "<p>老师您好，我想交流。</p>",
        approved_at: "2026-05-08T01:00:00",
      },
    }));

    render(
      <MemoryRouter>
        <TasksPage />
      </MemoryRouter>,
    );

    expect(await screen.findByText("审核批量任务")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "查看详情" }));
    fireEvent.click(await screen.findByRole("button", { name: "审核草稿" }));

    fireEvent.click(await screen.findByRole("button", { name: "审核通过" }));
    await waitFor(() => {
      expect(apiMocks.approveBatchTaskItemDraft).toHaveBeenCalledWith(
        task.id,
        item.id,
        expect.objectContaining({
          subject: "申请与老师交流",
          body_text: "老师您好，我想交流。",
          body_html: "<p>老师您好，我想交流。</p>",
          selected_material_ids: [7],
        }),
      );
    });
    expect(apiMocks.approveDraft).not.toHaveBeenCalled();
  });

  it("sends batch review drafts through scoped batch item APIs", async () => {
    const task = buildBatchTask({
      name: "立即发送批量任务",
      schedule_type: "immediate",
      review_required_count: 1,
      approved_count: 0,
    });
    const item = buildBatchItem({
      id: 31,
      professor_id: 21,
      status: "review_required",
      next_action: "review_draft",
    });
    apiMocks.listBatchTasks.mockResolvedValue([task]);
    apiMocks.listBatchTaskItems.mockResolvedValue([item]);
    apiMocks.getBatchTaskItemThread.mockResolvedValue(buildWorkspaceThread({
      current_task: {
        ...buildWorkspaceThread().current_task,
        id: item.id,
        batch_task_id: task.id,
      },
    }));
    apiMocks.approveAndSendBatchTaskItemDraft.mockResolvedValue(buildWorkspaceThread({
      current_task: {
        ...buildWorkspaceThread().current_task,
        id: item.id,
        batch_task_id: task.id,
        status: "sent",
        sent_at: "2026-05-08T01:00:00",
      },
    }));

    render(
      <MemoryRouter>
        <TasksPage />
      </MemoryRouter>,
    );

    expect(await screen.findByText("立即发送批量任务")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "查看详情" }));
    fireEvent.click(await screen.findByRole("button", { name: "审核草稿" }));
    fireEvent.click(await screen.findByRole("button", { name: "立即发送" }));
    await waitFor(() => {
      expect(apiMocks.approveAndSendBatchTaskItemDraft).toHaveBeenCalledWith(
        task.id,
        item.id,
        expect.objectContaining({
          subject: "申请与老师交流",
          body_text: "老师您好，我想交流。",
          body_html: "<p>老师您好，我想交流。</p>",
          selected_material_ids: [7],
        }),
      );
    });
    expect(apiMocks.approveAndSend).not.toHaveBeenCalled();
    expect(notificationMocks.notifySuccess).toHaveBeenCalledWith("邮件已发送");
  });

  it("reports a returned batch item send failure without claiming it was sent", async () => {
    const task = buildBatchTask({
      name: "发送失败批量任务",
      schedule_type: "immediate",
      review_required_count: 1,
      approved_count: 0,
    });
    const item = buildBatchItem({
      id: 31,
      professor_id: 21,
      status: "review_required",
      next_action: "review_draft",
    });
    apiMocks.listBatchTasks.mockResolvedValue([task]);
    apiMocks.listBatchTaskItems.mockResolvedValue([item]);
    apiMocks.getBatchTaskItemThread.mockResolvedValue(buildWorkspaceThread({
      current_task: {
        ...buildWorkspaceThread().current_task,
        id: item.id,
        batch_task_id: task.id,
      },
    }));
    apiMocks.approveAndSendBatchTaskItemDraft.mockResolvedValue(buildWorkspaceThread({
      current_task: {
        ...buildWorkspaceThread().current_task,
        id: item.id,
        batch_task_id: task.id,
        status: "send_failed",
        last_error: "SMTP 认证失败",
      },
    }));

    render(
      <MemoryRouter>
        <TasksPage />
      </MemoryRouter>,
    );

    expect(await screen.findByText("发送失败批量任务")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "查看详情" }));
    fireEvent.click(await screen.findByRole("button", { name: "审核草稿" }));
    fireEvent.click(await screen.findByRole("button", { name: "立即发送" }));

    await waitFor(() => {
      expect(notificationMocks.notifyError).toHaveBeenCalledWith(
        "发送邮件失败",
        "SMTP 认证失败",
      );
    });
    expect(notificationMocks.notifySuccess).not.toHaveBeenCalledWith("邮件已发送");
    expect(screen.getByRole("button", { name: "立即发送" })).toBeInTheDocument();
  });
});

describe("TaskListViewSwitch", () => {
  it("aligns the current/trash switch to the right edge", () => {
    render(
      <TaskListViewSwitch
        activeView="current"
        onViewChange={vi.fn()}
      />,
    );

    const switchContainer = screen.getByTestId("task-list-view-switch");
    expect(switchContainer).toHaveClass("justify-end");
    expect(switchContainer).not.toHaveClass("mt-4");

    const activeButton = screen.getByRole("button", { name: "当前任务" });
    expect(activeButton).toHaveClass("bg-primary");
    expect(activeButton).not.toHaveClass("bg-stone-900");
  });
});

describe("crawl job event failure reasons", () => {
  it("reads nested enrichment failure reasons from agent trace raw payloads", () => {
    const event: CrawlJobEventDTO = {
      id: "event-1",
      job_id: 2,
      event_type: "enrichment",
      message: "候选导师详情补全失败：方玉明",
      created_at: "2026-05-08T15:50:18Z",
      raw: {
        id: "",
        event_type: "enrichment",
        message: "候选导师详情补全失败：方玉明",
        created_at: "2026-05-08T15:50:18Z",
        raw: {
          event_type: "enrichment",
          message: "候选导师详情补全失败：方玉明",
          raw: {
            candidate_id: 2,
            status: "failed",
            error_message: "URL 不在入口页面同域范围内，已拒绝浏览器调查",
          },
        },
      },
    };

    expect(getCrawlEventFailureReason(event)).toBe(
      "URL 不在入口页面同域范围内，已拒绝浏览器调查",
    );
  });
});

describe("batch task send queue copy", () => {
  it("counts approved and scheduled items as waiting to send", () => {
    const task = buildBatchTask({
      approved_count: 3,
      scheduled_count: 2,
    });

    expect(getBatchTaskWaitingSendCount(task)).toBe(5);
  });

  it("flags scheduled batch items that lost their planned send time", () => {
    const action = buildBatchPendingItemAction(
      buildBatchItem({ status: "approved", scheduled_at: null, next_action: "missing_schedule" }),
      buildBatchTask({ schedule_type: "scheduled" }),
    );

    expect(action).toEqual({
      kind: "message",
      text: "计划时间缺失，请重新安排发送",
    });
  });

  it("keeps AI rewritten drafts as manual review work", () => {
    const action = buildBatchPendingItemAction(
      buildBatchItem({ status: "review_required", next_action: "review_draft" }),
      buildBatchTask({ schedule_type: "scheduled" }),
    );

    expect(action).toEqual({
      kind: "review",
      text: "审核草稿",
    });
  });

  it("ignores stale review actions after an item leaves review status", () => {
    const action = buildBatchPendingItemAction(
      buildBatchItem({ status: "approved", next_action: "review_draft" }),
      buildBatchTask({ schedule_type: "immediate" }),
    );

    expect(action).toEqual({
      kind: "message",
      text: "等待自动发送",
    });
  });

  it("does not show an action while AI drafts are pending generation", () => {
    const action = buildBatchPendingItemAction(
      buildBatchItem({ status: "matched", next_action: "waiting_draft_generation" }),
      buildBatchTask({ schedule_type: "scheduled" }),
    );

    expect(action).toEqual({
      kind: "message",
      text: "等待后台生成草稿",
    });
  });

  it("uses the current research direction over a historical fallback reason", () => {
    expect(
      isBatchTaskItemMissingResearchDirection(
        buildBatchItem({
          professor_research_direction: "Newly completed direction",
          draft_fallback_reason: "missing_research_direction",
        }),
      ),
    ).toBe(false);
    expect(
      isBatchTaskItemMissingResearchDirection(
        buildBatchItem({
          professor_research_direction: null,
          draft_fallback_reason: "missing_research_direction",
        }),
      ),
    ).toBe(true);
  });

  it("routes profile completion to professor management instead of workspace", () => {
    const action = buildBatchPendingItemAction(
      buildBatchItem({
        professor_name: "缺资料导师",
        professor_email: "missing-profile@example.edu",
        status: "discovered",
        next_action: "complete_professor_profile",
      }),
      buildBatchTask(),
    );

    expect(action).toEqual({
      kind: "professor",
      text: "补全导师资料",
      href: "/professors?keyword=missing-profile%40example.edu",
    });
  });

  it("describes schedule-expired canceled items with explicit copy", () => {
    const text = getBatchTaskItemCancellationText(
      buildBatchItem({
        status: "canceled",
        cancellation_reason: "schedule_expired",
        next_action: null,
      }),
    );

    expect(text).toBe("发送窗口已过期");
  });
});

describe("batch task expiration display", () => {
  it("opens professor profile completion inline instead of workspace fallback", async () => {
    const task = buildBatchTask({
      pending_generation_count: 1,
      approved_count: 0,
      scheduled_count: 0,
    });
    const item = buildBatchItem({
      professor_name: "缺资料导师",
      professor_email: "missing-profile@example.edu",
      professor_research_direction: null,
      status: "discovered",
      next_action: "complete_professor_profile",
    });
    const professor = buildProfessor({
      name: item.professor_name,
      email: item.professor_email,
      research_direction: null,
    });
    apiMocks.listBatchTasks.mockResolvedValue([task]);
    apiMocks.listBatchTaskItems.mockResolvedValue([item]);
    apiMocks.getProfessor.mockResolvedValue(professor);
    apiMocks.updateProfessor.mockResolvedValue(
      buildProfessorManagementItem({
        name: item.professor_name,
        email: item.professor_email,
        research_direction: "Machine Learning",
      }),
    );

    render(
      <MemoryRouter>
        <TasksPage />
      </MemoryRouter>,
    );

    expect(await screen.findByText("模板定时任务")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "查看详情" }));

    expect(await screen.findByText("缺少研究方向")).toBeInTheDocument();
    const profileButton = await screen.findByRole("button", { name: "补充资料" });
    expect(screen.queryByRole("link", { name: "补全导师资料" })).not.toBeInTheDocument();

    fireEvent.click(profileButton);
    const editDialog = await screen.findByRole("dialog", {
      name: "补充导师资料：缺资料导师",
    });
    expect(apiMocks.getProfessor).toHaveBeenCalledWith(item.professor_id);
    expect(within(editDialog).getByLabelText("研究方向")).toHaveValue("");

    fireEvent.change(within(editDialog).getByLabelText("研究方向"), {
      target: { value: "Machine Learning" },
    });
    fireEvent.click(within(editDialog).getByRole("button", { name: "保存导师" }));

    await waitFor(() => {
      expect(apiMocks.updateProfessor).toHaveBeenCalledWith(
        item.professor_id,
        expect.objectContaining({
          name: item.professor_name,
          email: item.professor_email,
          research_direction: "Machine Learning",
        }),
      );
    });
    await waitFor(() => {
      expect(screen.queryByRole("dialog", { name: "补充导师资料：缺资料导师" })).not.toBeInTheDocument();
    });
    expect(apiMocks.listBatchTaskItems).toHaveBeenCalledTimes(2);
  });

  it("uses next actions for draft failed items instead of workspace fallback", async () => {
    const task = buildBatchTask({
      pending_generation_count: 0,
      draft_failed_count: 1,
      approved_count: 0,
      scheduled_count: 0,
    });
    apiMocks.listBatchTasks.mockResolvedValue([task]);
    apiMocks.listBatchTaskItems.mockResolvedValue([
      buildBatchItem({
        professor_name: "失败导师",
        professor_email: "failed-profile@example.edu",
        professor_research_direction: null,
        status: "draft_failed",
        last_error: "请先补充导师研究方向，再使用 AI 生成草稿",
        next_action: "complete_professor_profile",
      }),
    ]);

    render(
      <MemoryRouter>
        <TasksPage />
      </MemoryRouter>,
    );

    expect(await screen.findByText("模板定时任务")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "查看详情" }));

    expect(await screen.findByRole("button", { name: "补充资料" })).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "查看并处理" })).not.toBeInTheDocument();
  });

  it("retries draft failed batch items from the detail panel", async () => {
    const task = buildBatchTask({
      pending_generation_count: 0,
      draft_failed_count: 1,
      approved_count: 0,
      scheduled_count: 0,
    });
    const failedItem = buildBatchItem({
      id: 88,
      status: "draft_failed",
      last_error: "LLM timeout",
      next_action: "retry_draft_generation",
    });
    apiMocks.listBatchTasks.mockResolvedValue([task]);
    apiMocks.listBatchTaskItems.mockResolvedValue([failedItem]);
    apiMocks.retryBatchTaskItemDraft.mockResolvedValue({ ok: true, task });

    render(
      <MemoryRouter>
        <TasksPage />
      </MemoryRouter>,
    );

    expect(await screen.findByText("模板定时任务")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "查看详情" }));
    fireEvent.click(await screen.findByRole("button", { name: "重新生成草稿" }));

    expect(apiMocks.retryBatchTaskItemDraft).toHaveBeenCalledWith(task.id, failedItem.id);
  });

  it("shows a possible cause and the raw error for send failures", async () => {
    const task = buildBatchTask({
      pending_generation_count: 0,
      failed_count: 1,
      approved_count: 0,
      scheduled_count: 0,
    });
    apiMocks.listBatchTasks.mockResolvedValue([task]);
    apiMocks.listBatchTaskItems.mockResolvedValue([
      buildBatchItem({
        professor_name: "发送失败导师",
        professor_email: "send-failed@example.edu",
        status: "send_failed",
        last_error:
          "SMTP 发信失败: (550, b'Requested action aborted: flow over limit')",
        possible_cause:
          "邮箱服务商可能对发件账号进行了发送限流，请暂停发送并稍后重试。",
        next_action: "send_failed",
      }),
    ]);

    render(
      <MemoryRouter>
        <TasksPage />
      </MemoryRouter>,
    );

    expect(await screen.findByText("模板定时任务")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "查看详情" }));

    expect(await screen.findByText("可能原因")).toBeInTheDocument();
    expect(screen.getByText(/发送限流/)).toBeInTheDocument();
    expect(screen.getByText("原始报错")).toBeInTheDocument();
    expect(
      screen.getByText(
        "SMTP 发信失败: (550, b'Requested action aborted: flow over limit')",
      ),
    ).toBeInTheDocument();
    expect(screen.queryByText("请检查发送失败原因")).not.toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "查看并处理" })).not.toBeInTheDocument();
    const manualCard = screen
      .getByText("待审核/未处理")
      .closest("div.rounded-2xl");
    expect(manualCard).not.toBeNull();
    expect(within(manualCard as HTMLElement).getByText("0")).toBeInTheDocument();
  });

  it("shows expired batch status and schedule-expired cancellation text in the detail panel", async () => {
    const task = buildBatchTask({
      status: "expired",
      review_required_count: 1,
      approved_count: 0,
      scheduled_count: 0,
    });
    apiMocks.listBatchTasks.mockResolvedValue([task]);
    apiMocks.listBatchTaskItems.mockResolvedValue([
      buildBatchItem({
        status: "canceled",
        cancellation_reason: "schedule_expired",
        next_action: null,
      }),
    ]);

    render(
      <MemoryRouter>
        <TasksPage />
      </MemoryRouter>,
    );

    expect(await screen.findByText("模板定时任务")).toBeInTheDocument();
    expect(screen.getByText("已过期")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "查看详情" }));

    expect(await screen.findByText("发送窗口已过期，剩余邮件已取消。可重新创建任务。")).toBeInTheDocument();
    expect(await screen.findByText("发送窗口已过期")).toBeInTheDocument();
  });
});
