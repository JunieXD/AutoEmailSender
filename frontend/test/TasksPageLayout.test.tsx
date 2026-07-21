import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { TasksPage } from "@/pages/TasksPage";
import { getBatchTaskItemThread, listBatchTaskItems, listBatchTasks } from "@/lib/api/batchTasksApi";
import { listMatchAnalysisJobs } from "@/lib/api/matchAnalysisJobsApi";
import type { WorkspaceThreadDTO } from "@/types";

const mockedUseSelectionContext = vi.hoisted(() => vi.fn());

vi.mock("@/context/BackgroundTaskNotificationContext", () => ({
  useBackgroundTaskNotification: () => ({
    stopTrackingInformationEnrichmentJob: vi.fn(),
    trackCrawlCandidateEnrichment: vi.fn(),
    trackCrawlJob: vi.fn(),
    trackInformationEnrichmentJob: vi.fn(),
    trackMatchAnalysisJob: vi.fn(),
  }),
}));

vi.mock("@/context/SelectionContext", () => ({
  useSelectionContext: mockedUseSelectionContext,
}));

vi.mock("@/context/NotificationContext", () => ({
  useNotification: () => ({
    notifyError: vi.fn(),
  }),
}));

vi.mock("@/lib/useConfirmDialog", () => ({
  useConfirmDialog: () => ({
    confirm: vi.fn(),
    dialog: null,
  }),
}));

vi.mock("@/lib/api/batchTasksApi", () => ({
  getBatchTaskItemThread: vi.fn(),
  listBatchTaskItems: vi.fn(),
  listBatchTasks: vi.fn(),
  pauseBatchTask: vi.fn(),
  resumeBatchTask: vi.fn(),
  stopBatchTask: vi.fn(),
}));

vi.mock("@/lib/api/matchAnalysisJobsApi", () => ({
  listMatchAnalysisJobItems: vi.fn(),
  listMatchAnalysisJobs: vi.fn(),
  cancelMatchAnalysisJob: vi.fn(),
  retryFailedMatchAnalysisJob: vi.fn(),
}));

vi.mock("@/lib/api/professorInformationEnrichmentApi", () => ({
  listProfessorInformationEnrichmentJobs: vi.fn().mockResolvedValue([]),
  listProfessorInformationEnrichmentItems: vi.fn().mockResolvedValue([]),
  cancelProfessorInformationEnrichmentJob: vi.fn(),
  retryFailedProfessorInformationEnrichmentJob: vi.fn(),
  deleteProfessorInformationEnrichmentJob: vi.fn(),
  restoreProfessorInformationEnrichmentJob: vi.fn(),
}));

const renderPage = () =>
  render(
    <MemoryRouter>
      <TasksPage />
    </MemoryRouter>,
  );

const runningTask = {
  id: 3,
  name: "春季套磁任务",
  status: "running",
  schedule_type: "scheduled",
  window_start_time: "09:00",
  window_end_time: "18:00",
  emails_per_window: 20,
  email_subject: "申请加入课题组",
  target_count: 10,
  completed_count: 4,
  identity_id: 1,
  llm_profile_id: 2,
  pending_generation_count: 2,
  generating_draft_count: 1,
  draft_failed_count: 1,
  review_required_count: 1,
  scheduled_count: 3,
  sent_count: 4,
  failed_count: 0,
  replied_count: 1,
  created_at: "2026-04-26T10:00:00Z",
  updated_at: "2026-04-26T11:00:00Z",
} as const;

const buildTask = (id: number) => ({
  ...runningTask,
  id,
  name: `批量邮件任务 ${id}`,
});

const sentTaskItem = {
  id: 31,
  professor_id: 101,
  professor_name: "王老师",
  professor_email: "wang@example.edu",
  professor_title: "教授",
  professor_school: "计算机学院",
  status: "sent",
  match_score: 88,
  scheduled_at: null,
  sent_at: "2026-04-26T11:30:00Z",
  last_send_attempt_at: "2026-04-26T11:30:00Z",
  last_error: null,
  is_replied: false,
  updated_at: "2026-04-26T11:30:00Z",
} as const;

const pendingTaskItem = {
  id: 32,
  professor_id: 102,
  professor_name: "李老师",
  professor_email: "li@example.edu",
  professor_title: "副教授",
  professor_school: "软件学院",
  status: "review_required",
  match_score: 76,
  scheduled_at: null,
  sent_at: null,
  last_send_attempt_at: null,
  last_error: null,
  is_replied: false,
  updated_at: "2026-04-26T11:20:00Z",
} as const;

const generatingDraftTaskItem = {
  id: 33,
  professor_id: 103,
  professor_name: "赵老师",
  professor_email: "zhao@example.edu",
  professor_title: "助理教授",
  professor_school: "人工智能学院",
  status: "generating_draft",
  match_score: 82,
  scheduled_at: null,
  sent_at: null,
  last_send_attempt_at: null,
  last_error: null,
  is_replied: false,
  updated_at: "2026-04-26T11:25:00Z",
} as const;

const draftFailedTaskItem = {
  id: 34,
  professor_id: 104,
  professor_name: "陈老师",
  professor_email: "chen@example.edu",
  professor_title: "教授",
  professor_school: "自动化学院",
  status: "draft_failed",
  match_score: 79,
  scheduled_at: null,
  sent_at: null,
  last_send_attempt_at: null,
  last_error: "LLM 请求失败",
  is_replied: false,
  updated_at: "2026-04-26T11:26:00Z",
} as const;

const canceledBatchStoppedTaskItem = {
  id: 35,
  professor_id: 105,
  professor_name: "周老师",
  professor_email: "zhou@example.edu",
  professor_title: "教授",
  professor_school: "数据科学学院",
  status: "canceled",
  cancellation_reason: "batch_stopped",
  match_score: 81,
  scheduled_at: null,
  sent_at: null,
  last_send_attempt_at: null,
  last_error: null,
  is_replied: false,
  updated_at: "2026-04-26T11:27:00Z",
} as const;

const buildWorkspaceThread = (
  overrides: Partial<WorkspaceThreadDTO["current_task"]> = {},
): WorkspaceThreadDTO => ({
  professor: {
    id: 102,
    name: "李老师",
    email: "li@example.edu",
    title: "副教授",
    university: "测试大学",
    school: "软件学院",
    research_direction: "软件工程",
    recent_papers: [],
  },
  identity: {
    id: 1,
    name: "测试身份",
    profile_name: "测试身份",
    sender_name: "测试同学",
    email_address: "sender@example.com",
  },
  llm_profile: {
    id: 2,
    name: "测试模型",
    provider: "openai",
    model_name: "gpt-test",
  },
  material_options: [],
  current_task: {
    id: 301,
    source: "batch",
    batch_task_id: runningTask.id,
    parent_task_id: null,
    status: "review_required",
    cancellation_reason: null,
    can_continue_manually: false,
    can_write_follow_up: false,
    outreach_generation_mode: "llm",
    outreach_template_subject: "模板主题",
    outreach_template_body_text: "模板正文",
    outreach_template_body_html: null,
    match_score: 76,
    match_reason: "方向匹配",
    fit_points: [],
    risk_points: [],
    match_keywords: [],
    generated_subject: "原草稿主题",
    generated_content_text: "原草稿正文",
    generated_content_html: "<p>原草稿正文</p>",
    approved_subject: null,
    approved_body_text: null,
    approved_body_html: null,
    primary_material_id: null,
    primary_material: null,
    selected_material_ids: [],
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
    ...overrides,
  },
  messages: [],
});

describe("TasksPage layout", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockedUseSelectionContext.mockReturnValue({
      selectedIdentityId: 1,
      selectedLlmProfileId: 2,
    });
    vi.mocked(listBatchTasks).mockResolvedValue([]);
    vi.mocked(listBatchTaskItems).mockResolvedValue([]);
    vi.mocked(listMatchAnalysisJobs).mockResolvedValue([]);
    vi.mocked(getBatchTaskItemThread).mockResolvedValue(buildWorkspaceThread());
  });

  it("uses the same wide page shell as the other primary pages", async () => {
    renderPage();

    await waitFor(() => {
      expect(listBatchTasks).toHaveBeenCalledWith({
        identityId: 1,
        llmProfileId: 2,
        view: "current",
      });
    });

    const pageShell = screen
      .getByRole("heading", { name: "任务中心" })
      .closest("main");

    expect(pageShell).toHaveClass("max-w-7xl");
    expect(screen.getByRole("button", { name: "批量邮件" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "教师抓取" })).toBeInTheDocument();
  });

  it("preloads match analysis count before the match tab is opened", async () => {
    vi.mocked(listMatchAnalysisJobs).mockResolvedValue([
      {
        id: 9,
        name: "鍖归厤鍒嗘瀽浠诲姟",
        status: "running",
        target_count: 3,
        succeeded_count: 1,
        failed_count: 0,
        skipped_count: 0,
        total_prompt_tokens: 100,
        total_completion_tokens: 20,
        total_cached_tokens: 60,
        total_tokens: 120,
        identity_id: 1,
        llm_profile_id: 2,
        cancel_requested_at: null,
        started_at: "2026-04-26T10:00:00Z",
        finished_at: null,
        created_at: "2026-04-26T10:00:00Z",
        updated_at: "2026-04-26T10:01:00Z",
        last_error: null,
      },
    ]);

    renderPage();

    await waitFor(() => {
      expect(listMatchAnalysisJobs).toHaveBeenCalledWith({
        identityId: 1,
        llmProfileId: 2,
        view: "current",
      });
    });

    expect(screen.getByRole("button", { name: "匹配分析" })).toHaveTextContent("1");
  });

  it("opens batch task details from the single-column task list", async () => {
    vi.mocked(listBatchTasks).mockResolvedValue([runningTask]);
    vi.mocked(listBatchTaskItems).mockResolvedValue([
      sentTaskItem,
      pendingTaskItem,
      generatingDraftTaskItem,
      draftFailedTaskItem,
      canceledBatchStoppedTaskItem,
    ]);

    renderPage();

    expect(await screen.findByText("生成中 1")).toBeInTheDocument();
    expect(screen.getByText("草稿失败 1")).toBeInTheDocument();

    const detailButton = await screen.findByRole("button", { name: "查看详情" });
    fireEvent.click(detailButton);

    const dialog = await screen.findByRole("dialog", { name: "批量任务详情" });
    expect(dialog).toBeInTheDocument();
    expect(listBatchTaskItems).toHaveBeenCalledWith(3);
    expect(within(dialog).getByText("春季套磁任务")).toBeInTheDocument();
    expect(within(dialog).getByText("申请加入课题组")).toBeInTheDocument();
    expect(within(dialog).getByText("已发送给")).toBeInTheDocument();
    expect(within(dialog).getByText("王老师")).toBeInTheDocument();
    expect(within(dialog).getByText("还未发送给")).toBeInTheDocument();
    expect(within(dialog).getByText("李老师")).toBeInTheDocument();
    const pendingSection = within(dialog)
      .getByRole("heading", { name: "还未发送给" })
      .closest("section") as HTMLElement;
    const canceledItem = within(pendingSection)
      .getByText("周老师")
      .closest(".rounded-2xl") as HTMLElement;
    expect(canceledItem).toBeInTheDocument();
    expect(within(canceledItem).getByText("已取消")).toBeInTheDocument();
    expect(within(canceledItem).getByText("批量任务已中止")).toBeInTheDocument();
    expect(within(dialog).getByRole("heading", { name: "正在生成草稿" })).toBeInTheDocument();
    expect(within(dialog).getByText("赵老师")).toBeInTheDocument();
    expect(within(dialog).getByRole("heading", { name: "草稿生成失败" })).toBeInTheDocument();
    expect(within(dialog).getByText("LLM 请求失败")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "关闭" }));

    await waitFor(() => {
      expect(screen.queryByRole("dialog", { name: "批量任务详情" })).not.toBeInTheDocument();
    });
  });

  it("paginates batch task cards", async () => {
    vi.mocked(listBatchTasks).mockResolvedValue(
      Array.from({ length: 9 }, (_, index) => buildTask(index + 1)),
    );

    renderPage();

    expect(await screen.findByText("批量邮件任务 1")).toBeInTheDocument();
    expect(screen.getByText("批量邮件任务 8")).toBeInTheDocument();
    expect(screen.queryByText("批量邮件任务 9")).not.toBeInTheDocument();
    expect(screen.getByText("第 1 / 2 页")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "下一页" }));

    expect(await screen.findByText("批量邮件任务 9")).toBeInTheDocument();
    expect(screen.queryByText("批量邮件任务 1")).not.toBeInTheDocument();
    expect(screen.getByText("显示 9-9 / 9 个任务")).toBeInTheDocument();
  });

  it("closes batch task details when clicking the backdrop", async () => {
    vi.mocked(listBatchTasks).mockResolvedValue([runningTask]);
    vi.mocked(listBatchTaskItems).mockResolvedValue([sentTaskItem, pendingTaskItem]);

    renderPage();

    fireEvent.click(await screen.findByRole("button", { name: "查看详情" }));

    const dialog = await screen.findByRole("dialog", { name: "批量任务详情" });
    fireEvent.click(dialog.parentElement as HTMLElement);

    await waitFor(() => {
      expect(screen.queryByRole("dialog", { name: "批量任务详情" })).not.toBeInTheDocument();
    });
  });

  it("loads the batch draft review thread from the selected batch item", async () => {
    vi.mocked(listBatchTasks).mockResolvedValue([runningTask]);
    vi.mocked(listBatchTaskItems).mockResolvedValue([pendingTaskItem]);
    vi.mocked(getBatchTaskItemThread).mockResolvedValueOnce(
      buildWorkspaceThread({
        id: pendingTaskItem.id,
        generated_subject: "批量草稿主题",
      }),
    );

    renderPage();

    fireEvent.click(await screen.findByRole("button", { name: "查看详情" }));
    fireEvent.click(await screen.findByRole("button", { name: "审核草稿" }));

    await waitFor(() => {
      expect(getBatchTaskItemThread).toHaveBeenCalledWith(runningTask.id, pendingTaskItem.id);
    });
    expect(
      screen.getByRole("textbox", { name: "邮件主题" }),
    ).toHaveTextContent("批量草稿主题");
  });
});
