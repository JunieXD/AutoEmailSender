import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import "@testing-library/jest-dom/vitest";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type {
  BatchTaskCardDTO,
  BatchTaskItemDTO,
  CrawlJobEventDTO,
  CrawlJobSummaryDTO,
  WorkspaceThreadDTO,
} from "@/types";
import {
  buildBatchPendingItemAction,
  getBatchTaskItemCancellationText,
  getBatchTaskWaitingSendCount,
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
  pauseBatchTask: vi.fn(),
  resumeBatchTask: vi.fn(),
  stopBatchTask: vi.fn(),
  deleteBatchTask: vi.fn(),
  restoreBatchTask: vi.fn(),
  getBatchTaskItemThread: vi.fn(),
  regenerateBatchTaskItemDraft: vi.fn(),
  approveBatchTaskItemDraft: vi.fn(),
  approveAndSendBatchTaskItemDraft: vi.fn(),
  deleteBatchTaskItem: vi.fn(),
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
  deleteCrawlJob: vi.fn(),
  restoreCrawlJob: vi.fn(),
  listMatchAnalysisJobs: vi.fn(),
  listMatchAnalysisJobItems: vi.fn(),
  cancelMatchAnalysisJob: vi.fn(),
  retryFailedMatchAnalysisJob: vi.fn(),
  deleteMatchAnalysisJob: vi.fn(),
  restoreMatchAnalysisJob: vi.fn(),
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

const confirmMock = vi.hoisted(() => vi.fn().mockResolvedValue(true));

vi.mock("@/context/SelectionContext", () => ({
  useSelectionContext: () => ({
    selectedIdentityId: 1,
    selectedLlmProfileId: 2,
  }),
}));

vi.mock("@/context/NotificationContext", () => ({
  useNotification: () => notificationMocks,
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
  pauseBatchTask: apiMocks.pauseBatchTask,
  resumeBatchTask: apiMocks.resumeBatchTask,
  stopBatchTask: apiMocks.stopBatchTask,
  deleteBatchTask: apiMocks.deleteBatchTask,
  restoreBatchTask: apiMocks.restoreBatchTask,
  getBatchTaskItemThread: apiMocks.getBatchTaskItemThread,
  regenerateBatchTaskItemDraft: apiMocks.regenerateBatchTaskItemDraft,
  approveBatchTaskItemDraft: apiMocks.approveBatchTaskItemDraft,
  approveAndSendBatchTaskItemDraft: apiMocks.approveAndSendBatchTaskItemDraft,
  deleteBatchTaskItem: apiMocks.deleteBatchTaskItem,
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

  it("uses re-crawl wording after retrying a failed crawl job", async () => {
    apiMocks.listCrawlJobs.mockResolvedValue([buildCrawlJob()]);
    apiMocks.retryCrawlJob.mockResolvedValue(buildCrawlJob({ status: "queued" }));

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
      });
    });
    expect(notificationMocks.notifySuccess).toHaveBeenCalledWith(
      "抓取任务已重新加入队列",
      "任务已进入队列，稍后开始执行",
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
  created_at: "2026-05-08T00:00:00",
  updated_at: "2026-05-08T00:00:00",
  deleted_at: null,
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
  status: "approved",
  cancellation_reason: null,
  match_score: null,
  scheduled_at: null,
  sent_at: null,
  last_send_attempt_at: null,
  last_error: null,
  is_replied: false,
  updated_at: "2026-05-08T00:00:00",
  next_action: "waiting_send",
  ...overrides,
});

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
    research_direction: "Human-centered AI",
    recent_papers: ["Recent AI paper"],
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
  },
  messages: [],
  ...overrides,
});

beforeEach(() => {
  vi.clearAllMocks();
  confirmMock.mockResolvedValue(true);
  apiMocks.listBatchTasks.mockResolvedValue([]);
  apiMocks.listBatchTaskItems.mockResolvedValue([]);
  apiMocks.listCrawlJobs.mockResolvedValue([]);
  apiMocks.getCrawlJob.mockResolvedValue(buildCrawlJob());
  apiMocks.getCrawlJobEvents.mockResolvedValue([]);
  apiMocks.listCrawlCandidates.mockResolvedValue([]);
  apiMocks.listCrawlPages.mockResolvedValue([]);
  apiMocks.listMatchAnalysisJobs.mockResolvedValue([]);
  apiMocks.listMatchAnalysisJobItems.mockResolvedValue([]);
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

describe("TasksPage batch draft review", () => {
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
    apiMocks.getBatchTaskItemThread.mockResolvedValue(buildWorkspaceThread({
      current_task: {
        ...buildWorkspaceThread().current_task,
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
  it("shows professor profile completion link instead of workspace fallback", async () => {
    const task = buildBatchTask({
      pending_generation_count: 1,
      approved_count: 0,
      scheduled_count: 0,
    });
    apiMocks.listBatchTasks.mockResolvedValue([task]);
    apiMocks.listBatchTaskItems.mockResolvedValue([
      buildBatchItem({
        professor_name: "缺资料导师",
        professor_email: "missing-profile@example.edu",
        status: "discovered",
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

    const profileLink = await screen.findByRole("link", { name: "补全导师资料" });
    expect(profileLink).toHaveAttribute(
      "href",
      "/professors?keyword=missing-profile%40example.edu",
    );
    expect(screen.queryByRole("link", { name: "去处理" })).not.toBeInTheDocument();
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

    const profileLink = await screen.findByRole("link", { name: "补全导师资料" });
    expect(profileLink).toHaveAttribute(
      "href",
      "/professors?keyword=failed-profile%40example.edu",
    );
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

  it("uses next actions for send failed items instead of workspace fallback", async () => {
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
        last_error: "smtp timeout",
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

    expect(await screen.findByText("请检查发送失败原因")).toBeInTheDocument();
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
