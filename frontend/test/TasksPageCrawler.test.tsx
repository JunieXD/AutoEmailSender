import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { TasksPage } from "@/pages/TasksPage";
import { listBatchTaskItems, listBatchTasks } from "@/lib/api/batchTasksApi";
import { listMatchAnalysisJobs } from "@/lib/api/matchAnalysisJobsApi";
import { clearDiagnosticEvents, getDiagnosticEvents } from "@/lib/diagnostics";
import { formatApiDateTime } from "@/lib/dateTime";
import {
  approveCrawlCandidates,
  cancelCrawlJob,
  enrichCrawlCandidates,
  getCrawlJob,
  getCrawlJobEvents,
  listCrawlCandidates,
  listCrawlJobs,
  listCrawlPages,
} from "@/lib/api/crawlJobsApi";

const mockedUseSelectionContext = vi.hoisted(() => vi.fn());
const confirm = vi.hoisted(() => vi.fn());
const notifyError = vi.hoisted(() => vi.fn());
const notifySuccess = vi.hoisted(() => vi.fn());

vi.mock("@/context/SelectionContext", () => ({
  useSelectionContext: mockedUseSelectionContext,
}));

vi.mock("@/context/NotificationContext", () => ({
  useNotification: () => ({
    notifyError,
    notifySuccess,
  }),
}));

vi.mock("@/lib/useConfirmDialog", () => ({
  useConfirmDialog: () => ({
    confirm,
    dialog: null,
  }),
}));

vi.mock("@/lib/api/batchTasksApi", () => ({
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

vi.mock("@/lib/api/crawlJobsApi", () => ({
  listCrawlJobs: vi.fn(),
  approveCrawlCandidates: vi.fn(),
  cancelCrawlJob: vi.fn(),
  enrichCrawlCandidates: vi.fn(),
  getCrawlJob: vi.fn(),
  listCrawlPages: vi.fn(),
  listCrawlCandidates: vi.fn(),
  getCrawlJobEvents: vi.fn(),
}));

const runningJob = {
  id: 7,
  university: "示例大学",
  school: "计算机学院",
  start_url: "https://example.edu/faculty",
  llm_profile_id: 2,
  status: "running",
  progress_current: 0,
  progress_total: 0,
  error_message: null,
  created_at: "2026-04-26T10:00:00Z",
  updated_at: "2026-04-26T10:00:00Z",
  page_count: 12,
  candidate_count: 34,
  input_tokens: 1000,
  output_tokens: 400,
  cached_tokens: 0,
  total_tokens: 1400,
  duration_seconds: 90,
  latest_event_message: "正在分析教师列表",
} as const;

const buildCrawlJob = (id: number) => ({
  ...runningJob,
  id,
  university: `示例大学 ${id}`,
  school: "计算机学院",
});

const renderPage = () =>
  render(
    <MemoryRouter>
      <TasksPage />
    </MemoryRouter>,
  );

describe("TasksPage crawler jobs tab", () => {
  beforeEach(() => {
    clearDiagnosticEvents();
    Reflect.deleteProperty(window, "autoEmailSender");
    vi.clearAllMocks();
    mockedUseSelectionContext.mockReturnValue({
      selectedIdentityId: 1,
      selectedLlmProfileId: 2,
    });
    confirm.mockResolvedValue(true);
    vi.mocked(listBatchTasks).mockResolvedValue([]);
    vi.mocked(listBatchTaskItems).mockResolvedValue([]);
    vi.mocked(listMatchAnalysisJobs).mockResolvedValue([]);
    vi.mocked(listCrawlJobs).mockResolvedValue([runningJob]);
    vi.mocked(approveCrawlCandidates).mockResolvedValue({
      inserted_count: 1,
      updated_count: 0,
      skipped_count: 0,
      message: "审核完成",
    });
    vi.mocked(enrichCrawlCandidates).mockResolvedValue({
      updated_count: 2,
      failed_count: 0,
      message: "补全完成",
    });
    vi.mocked(cancelCrawlJob).mockResolvedValue(runningJob);
    vi.mocked(getCrawlJob).mockResolvedValue(runningJob);
    vi.mocked(listCrawlPages).mockResolvedValue([
      {
        id: 11,
        job_id: 7,
        url: "https://example.edu/faculty",
        parent_url: null,
        fetch_method: "http",
        page_type: "faculty_list",
        status: "fetched",
        title: "Faculty",
        text_excerpt: null,
        error_message: null,
        created_at: "2026-04-26T10:01:00Z",
      },
    ]);
    vi.mocked(listCrawlCandidates).mockResolvedValue([
      {
        id: 21,
        job_id: 7,
        professor_id: null,
        name: "张教授",
        email: null,
        title: null,
        university: "示例大学",
        school: "计算机学院",
        department: null,
        research_direction: null,
        recent_papers: [],
        profile_url: null,
        source_url: "https://example.edu/faculty",
        confidence: 0.86,
        field_confidence: null,
        evidence: null,
        review_status: "pending",
        created_at: "2026-04-26T10:02:00Z",
        updated_at: "2026-04-26T10:02:00Z",
      },
    ]);
    vi.mocked(getCrawlJobEvents).mockResolvedValue([
      {
        id: "evt-1",
        job_id: 7,
        event_type: "crawl_page",
        message: "调用 crawl_page 抓取入口页面",
        created_at: "2026-04-26T08:34:00",
        raw: null,
      },
    ]);
  });

  it("shows crawl job cards after switching to the crawler tab", async () => {
    renderPage();

    await waitFor(() => {
      expect(listCrawlJobs).toHaveBeenCalled();
    });

    const crawlerSummaryCard =
      screen.getAllByText("教师抓取")[0].closest("div")?.parentElement;
    await waitFor(() => {
      expect(crawlerSummaryCard).toHaveTextContent("1");
    });

    fireEvent.click(screen.getByRole("button", { name: "教师抓取" }));

    await waitFor(() => {
      expect(listCrawlJobs).toHaveBeenCalled();
    });

    expect(screen.getByText("示例大学 / 计算机学院")).toBeInTheDocument();
    expect(screen.getByText("https://example.edu/faculty")).toBeInTheDocument();
    expect(screen.getByText("已抓页面 12")).toBeInTheDocument();
    expect(screen.getByText("候选导师 34")).toBeInTheDocument();
    expect(screen.getByText("正在分析教师列表")).toBeInTheDocument();
    const detailButton = screen.getByRole("button", { name: "查看详情" });
    expect(detailButton).toBeEnabled();
    expect(detailButton.querySelector(".lucide-chevron-right")).not.toBeNull();
    expect(detailButton.parentElement?.lastElementChild).toBe(detailButton);
  });

  it("shows crawler jobs even when no sender identity is configured", async () => {
    mockedUseSelectionContext.mockReturnValue({
      selectedIdentityId: null,
      selectedLlmProfileId: 2,
    });

    renderPage();

    await waitFor(() => {
      expect(listCrawlJobs).toHaveBeenCalled();
    });

    expect(screen.getByRole("heading", { name: "任务中心" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "教师抓取" })).toHaveTextContent("1");
    fireEvent.click(screen.getByRole("button", { name: "教师抓取" }));
    expect(await screen.findByText("示例大学 / 计算机学院")).toBeInTheDocument();
    expect(listBatchTasks).not.toHaveBeenCalled();
    expect(listMatchAnalysisJobs).not.toHaveBeenCalled();
  });

  it("opens and closes the crawl job log dialog", async () => {
    renderPage();

    fireEvent.click(screen.getByRole("button", { name: "教师抓取" }));

    const logButton = await screen.findByRole("button", { name: "查看详情" });
    fireEvent.click(logButton);

    const dialog = await screen.findByRole("dialog", { name: "抓取任务详情" });
    expect(dialog).toBeInTheDocument();
    expect(listCrawlPages).toHaveBeenCalledWith(7);
    expect(listCrawlCandidates).toHaveBeenCalledWith(7);
    expect(getCrawlJobEvents).toHaveBeenCalledWith(7);
    expect(getDiagnosticEvents()).toEqual(
      expect.arrayContaining([
        expect.objectContaining({
          category: "user_action",
          eventName: "tasks.crawl_job_detail_opened",
          data: {
            jobId: 7,
            status: "running",
          },
        }),
      ]),
    );
    expect(screen.getByText("调用 crawl_page 抓取入口页面")).toBeInTheDocument();
    expect(
      screen.getByText(
        formatApiDateTime("2026-04-26T08:34:00", {
          second: "2-digit",
        }),
      ),
    ).toBeInTheDocument();
    expect(screen.getByText("Faculty")).toBeInTheDocument();
    expect(screen.getByText("张教授")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "关闭" }));

    await waitFor(() => {
      expect(screen.queryByRole("dialog", { name: "抓取任务详情" })).not.toBeInTheDocument();
    });
  });

  it("keeps long candidate detail content scrollable inside the dialog", async () => {
    vi.mocked(listCrawlCandidates).mockResolvedValue([
      {
        id: 21,
        job_id: 7,
        professor_id: null,
        name: "张教授",
        email: "zhang@example.edu",
        title: "教授",
        university: "示例大学",
        school: "计算机学院",
        department: "计算机学院",
        research_direction: "机器学习",
        recent_papers: Array.from(
          { length: 24 },
          (_, index) => `近期论文 ${index + 1}`,
        ),
        profile_url: "https://example.edu/faculty/zhang",
        source_url: "https://example.edu/faculty",
        confidence: 0.86,
        field_confidence: null,
        evidence: null,
        review_status: "pending",
        created_at: "2026-04-26T10:02:00Z",
        updated_at: "2026-04-26T10:02:00Z",
      },
    ]);
    renderPage();

    fireEvent.click(screen.getByRole("button", { name: "教师抓取" }));
    fireEvent.click(await screen.findByRole("button", { name: "查看详情" }));

    const crawlDialog = await screen.findByRole("dialog", { name: "抓取任务详情" });
    fireEvent.click(within(crawlDialog).getByRole("button", { name: "查看详情" }));

    const candidateDialog = await screen.findByRole("dialog", { name: "候选导师详情" });
    const scrollRegion = candidateDialog.querySelector(
      '[data-testid="candidate-detail-scroll"]',
    );
    expect(candidateDialog).toHaveClass("flex", "max-h-[90vh]", "overflow-hidden");
    expect(scrollRegion).toHaveClass("flex-1", "overflow-y-auto", "overscroll-contain");
    expect(document.body.style.overflow).toBe("hidden");

    fireEvent.click(
      within(candidateDialog).getByRole("button", { name: "关闭候选导师详情" }),
    );

    await waitFor(() => {
      expect(screen.queryByRole("dialog", { name: "候选导师详情" })).not.toBeInTheDocument();
    });
    expect(document.body.style.overflow).toBe("");
  });

  it("opens candidate profile and source links with the desktop default browser when available", async () => {
    const openExternalUrl = vi.fn().mockResolvedValue(undefined);
    window.autoEmailSender = {
      getVersion: async () => "0.1.0",
      openExternalUrl,
      checkForUpdate: vi.fn(),
      downloadUpdate: vi.fn(),
      switchToFullDownload: vi.fn(),
      quitAndInstall: vi.fn(),
      onUpdateStatus: () => () => undefined,
    };
    vi.mocked(listCrawlCandidates).mockResolvedValue([
      {
        id: 21,
        job_id: 7,
        professor_id: null,
        name: "张教授",
        email: "zhang@example.edu",
        title: "教授",
        university: "示例大学",
        school: "计算机学院",
        department: "计算机学院",
        research_direction: "机器学习",
        recent_papers: [],
        profile_url: "https://example.edu/faculty/zhang",
        source_url: "https://example.edu/faculty",
        confidence: 0.86,
        field_confidence: null,
        evidence: null,
        review_status: "pending",
        created_at: "2026-04-26T10:02:00Z",
        updated_at: "2026-04-26T10:02:00Z",
      },
    ]);

    renderPage();

    fireEvent.click(screen.getByRole("button", { name: "教师抓取" }));
    fireEvent.click(await screen.findByRole("button", { name: "查看详情" }));

    const crawlDialog = await screen.findByRole("dialog", { name: "抓取任务详情" });
    fireEvent.click(within(crawlDialog).getByRole("button", { name: "查看详情" }));

    const candidateDialog = await screen.findByRole("dialog", { name: "候选导师详情" });
    const profileLink = within(candidateDialog).getByRole("link", {
      name: "https://example.edu/faculty/zhang",
    });
    const sourceLink = within(candidateDialog).getByRole("link", {
      name: "https://example.edu/faculty",
    });
    expect(profileLink).toHaveAttribute("href", "https://example.edu/faculty/zhang");
    expect(sourceLink).toHaveAttribute("href", "https://example.edu/faculty");
    expect(profileLink).toHaveAttribute("target", "_blank");
    expect(sourceLink).toHaveAttribute("target", "_blank");
    expect(profileLink).toHaveAttribute("rel", "noreferrer");
    expect(sourceLink).toHaveAttribute("rel", "noreferrer");

    fireEvent.click(profileLink);
    fireEvent.click(sourceLink);

    expect(openExternalUrl).toHaveBeenCalledWith("https://example.edu/faculty/zhang");
    expect(openExternalUrl).toHaveBeenCalledWith("https://example.edu/faculty");
  });

  it("falls back to opening candidate links in an Electron window when the default browser fails", async () => {
    const openExternalUrl = vi.fn().mockRejectedValue(new Error("xdg-open missing"));
    const openWindow = vi.spyOn(window, "open").mockImplementation(() => null);
    window.autoEmailSender = {
      getVersion: async () => "0.1.0",
      openExternalUrl,
      checkForUpdate: vi.fn(),
      downloadUpdate: vi.fn(),
      switchToFullDownload: vi.fn(),
      quitAndInstall: vi.fn(),
      onUpdateStatus: () => () => undefined,
    };
    vi.mocked(listCrawlCandidates).mockResolvedValue([
      {
        id: 21,
        job_id: 7,
        professor_id: null,
        name: "张教授",
        email: "zhang@example.edu",
        title: "教授",
        university: "示例大学",
        school: "计算机学院",
        department: "计算机学院",
        research_direction: "机器学习",
        recent_papers: [],
        profile_url: "https://example.edu/faculty/zhang",
        source_url: "https://example.edu/faculty",
        confidence: 0.86,
        field_confidence: null,
        evidence: null,
        review_status: "pending",
        created_at: "2026-04-26T10:02:00Z",
        updated_at: "2026-04-26T10:02:00Z",
      },
    ]);

    renderPage();

    fireEvent.click(screen.getByRole("button", { name: "教师抓取" }));
    fireEvent.click(await screen.findByRole("button", { name: "查看详情" }));

    const crawlDialog = await screen.findByRole("dialog", { name: "抓取任务详情" });
    fireEvent.click(within(crawlDialog).getByRole("button", { name: "查看详情" }));

    const candidateDialog = await screen.findByRole("dialog", { name: "候选导师详情" });
    fireEvent.click(
      within(candidateDialog).getByRole("link", {
        name: "https://example.edu/faculty/zhang",
      }),
    );

    await waitFor(() => {
      expect(openWindow).toHaveBeenCalledWith(
        "https://example.edu/faculty/zhang",
        "_blank",
        "noopener,noreferrer",
      );
    });
    openWindow.mockRestore();
  });

  it("shows the crawl enrichment failure reason in the candidate detail dialog", async () => {
    vi.mocked(listCrawlCandidates).mockResolvedValue([
      {
        id: 21,
        job_id: 7,
        professor_id: null,
        name: "张教授",
        email: null,
        title: "教授",
        university: "示例大学",
        school: "计算机学院",
        department: null,
        research_direction: null,
        recent_papers: [],
        profile_url: "https://example.edu/faculty/zhang",
        source_url: "https://example.edu/faculty",
        confidence: 0.86,
        field_confidence: null,
        evidence: null,
        review_status: "pending",
        created_at: "2026-04-26T10:02:00Z",
        updated_at: "2026-04-26T10:02:00Z",
      },
    ]);
    vi.mocked(getCrawlJobEvents).mockResolvedValue([
      {
        id: "evt-1",
        job_id: 7,
        event_type: "enrichment",
        message: "候选导师详情补全失败：张教授",
        created_at: "2026-04-26T08:34:00",
        raw: {
          candidate_id: 21,
          profile_url: "https://example.edu/faculty/zhang",
          status: "failed",
          error_message:
            "Playwright browser fetch failed: FileNotFoundError: [WinError 2] 系统找不到指定的文件。",
        },
      },
    ]);

    renderPage();

    fireEvent.click(screen.getByRole("button", { name: "教师抓取" }));
    fireEvent.click(await screen.findByRole("button", { name: "查看详情" }));

    const crawlDialog = await screen.findByRole("dialog", { name: "抓取任务详情" });
    expect(crawlDialog).toHaveTextContent("暂无邮箱（可尝试进行补全）");
    fireEvent.click(within(crawlDialog).getByRole("button", { name: "查看详情" }));

    const candidateDialog = await screen.findByRole("dialog", { name: "候选导师详情" });
    expect(candidateDialog).toHaveTextContent("暂无邮箱（可尝试进行补全）");
    expect(candidateDialog).toHaveTextContent("补全失败原因");
    expect(candidateDialog).toHaveTextContent("WinError 2");
  });

  it("hides an older enrichment failure reason after the same candidate is enriched successfully", async () => {
    vi.mocked(listCrawlCandidates).mockResolvedValue([
      {
        id: 21,
        job_id: 7,
        professor_id: null,
        name: "张教授",
        email: "zhang@example.edu",
        title: "教授",
        university: "示例大学",
        school: "计算机学院",
        department: null,
        research_direction: null,
        recent_papers: [],
        profile_url: "https://example.edu/faculty/zhang",
        source_url: "https://example.edu/faculty",
        confidence: 0.86,
        field_confidence: null,
        evidence: null,
        review_status: "pending",
        created_at: "2026-04-26T10:02:00Z",
        updated_at: "2026-04-26T10:12:00Z",
      },
    ]);
    vi.mocked(getCrawlJobEvents).mockResolvedValue([
      {
        id: "evt-failed",
        job_id: 7,
        event_type: "enrichment",
        message: "候选导师详情补全失败：张教授",
        created_at: "2026-04-26T08:34:00",
        raw: {
          candidate_id: 21,
          status: "failed",
          error_message:
            "Playwright browser fetch failed: FileNotFoundError: [WinError 2] 系统找不到指定的文件。",
        },
      },
      {
        id: "evt-succeeded",
        job_id: 7,
        event_type: "enrichment",
        message: "候选导师详情补全成功：张教授",
        created_at: "2026-04-26T08:40:00",
        raw: {
          candidate_id: 21,
          status: "succeeded",
        },
      },
    ]);

    renderPage();

    fireEvent.click(screen.getByRole("button", { name: "教师抓取" }));
    fireEvent.click(await screen.findByRole("button", { name: "查看详情" }));

    const crawlDialog = await screen.findByRole("dialog", { name: "抓取任务详情" });
    fireEvent.click(within(crawlDialog).getByRole("button", { name: "查看详情" }));

    const candidateDialog = await screen.findByRole("dialog", { name: "候选导师详情" });
    expect(candidateDialog).toHaveTextContent("zhang@example.edu");
    expect(candidateDialog).not.toHaveTextContent("补全失败原因");
    expect(candidateDialog).not.toHaveTextContent("WinError 2");
  });

  it("shows the crawl enrichment failure reason in the realtime monitor log", async () => {
    vi.mocked(getCrawlJobEvents).mockResolvedValue([
      {
        id: "evt-1",
        job_id: 7,
        event_type: "enrichment",
        message: "候选导师详情补全失败：张教授",
        created_at: "2026-04-26T08:34:00",
        raw: {
          candidate_id: 21,
          profile_url: "https://example.edu/faculty/zhang",
          status: "failed",
          error_message:
            "Playwright browser fetch failed: FileNotFoundError: [WinError 2] 系统找不到指定的文件。",
        },
      },
    ]);

    renderPage();

    fireEvent.click(screen.getByRole("button", { name: "教师抓取" }));
    fireEvent.click(await screen.findByRole("button", { name: "查看详情" }));

    const crawlDialog = await screen.findByRole("dialog", { name: "抓取任务详情" });
    expect(crawlDialog).toHaveTextContent("候选导师详情补全失败：张教授");
    expect(crawlDialog).toHaveTextContent("WinError 2");
  });

  it("keeps crawl log and crawled page pagination aligned in the detail dialog", async () => {
    vi.mocked(getCrawlJobEvents).mockResolvedValue(
      Array.from({ length: 6 }, (_, index) => ({
        id: `evt-${index + 1}`,
        job_id: 7,
        event_type: "crawl_page",
        message: `执行日志 ${index + 1}`,
        created_at: "2026-04-26T08:34:00",
        raw: null,
      })),
    );
    vi.mocked(listCrawlPages).mockResolvedValue(
      Array.from({ length: 6 }, (_, index) => ({
        id: index + 11,
        job_id: 7,
        url: `https://example.edu/faculty/${index + 1}`,
        parent_url: null,
        fetch_method: "http",
        page_type: "faculty_list",
        status: "fetched",
        title: `Faculty ${index + 1}`,
        text_excerpt: null,
        error_message: null,
        created_at: "2026-04-26T10:01:00Z",
      })),
    );

    renderPage();

    fireEvent.click(screen.getByRole("button", { name: "教师抓取" }));
    fireEvent.click(await screen.findByRole("button", { name: "查看详情" }));

    const dialog = await screen.findByRole("dialog", { name: "抓取任务详情" });
    const logSection = within(dialog)
      .getByRole("heading", { name: "执行日志" })
      .closest("section");
    const pageSection = within(dialog)
      .getByRole("heading", { name: "已抓页面" })
      .closest("section");

    expect(logSection).toHaveClass("flex", "h-full", "flex-col");
    expect(pageSection).toHaveClass("flex", "h-full", "flex-col");
    expect(logSection?.querySelector("[data-monitor-section-list]")).toHaveClass("flex-1");
    expect(pageSection?.querySelector("[data-monitor-section-list]")).toHaveClass("flex-1");
  });

  it("paginates crawl job cards", async () => {
    vi.mocked(listCrawlJobs).mockResolvedValue(
      Array.from({ length: 9 }, (_, index) => buildCrawlJob(index + 1)),
    );

    renderPage();

    fireEvent.click(screen.getByRole("button", { name: "教师抓取" }));

    expect(await screen.findByText("示例大学 1 / 计算机学院")).toBeInTheDocument();
    expect(screen.getByText("示例大学 8 / 计算机学院")).toBeInTheDocument();
    expect(screen.queryByText("示例大学 9 / 计算机学院")).not.toBeInTheDocument();
    expect(screen.getByText("第 1 / 2 页")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "下一页" }));

    expect(await screen.findByText("示例大学 9 / 计算机学院")).toBeInTheDocument();
    expect(screen.queryByText("示例大学 1 / 计算机学院")).not.toBeInTheDocument();
    expect(screen.getByText("显示 9-9 / 9 个任务")).toBeInTheDocument();
  });

  it("closes the crawl job details dialog when clicking the backdrop", async () => {
    renderPage();

    fireEvent.click(screen.getByRole("button", { name: "教师抓取" }));

    fireEvent.click(await screen.findByRole("button", { name: "查看详情" }));

    const dialog = await screen.findByRole("dialog", { name: "抓取任务详情" });
    fireEvent.click(dialog.parentElement as HTMLElement);

    await waitFor(() => {
      expect(screen.queryByRole("dialog", { name: "抓取任务详情" })).not.toBeInTheDocument();
    });
  });

  it("cancels a running crawl job from the crawler tab", async () => {
    renderPage();

    fireEvent.click(screen.getByRole("button", { name: "教师抓取" }));

    const cancelButton = await screen.findByRole("button", { name: "取消抓取" });
    fireEvent.click(cancelButton);

    await waitFor(() => {
      expect(cancelCrawlJob).toHaveBeenCalledWith(7);
    });
    expect(getDiagnosticEvents()).toEqual(
      expect.arrayContaining([
        expect.objectContaining({
          category: "user_action",
          eventName: "tasks.crawl_job_cancel_submitted",
          data: { jobId: 7 },
        }),
        expect.objectContaining({
          category: "user_action",
          eventName: "tasks.crawl_job_cancel_succeeded",
          data: { jobId: 7 },
        }),
      ]),
    );
  });

  it("asks users to resume review before enriching or approving canceled crawl candidates", async () => {
    const canceledJob = {
      ...runningJob,
      status: "canceled",
    } as const;
    vi.mocked(listCrawlJobs).mockResolvedValue([canceledJob]);
    vi.mocked(getCrawlJob).mockResolvedValue(canceledJob);
    vi.mocked(listCrawlCandidates).mockResolvedValue([
      {
        id: 21,
        job_id: 7,
        professor_id: null,
        name: "张教授",
        email: "zhang@example.edu",
        title: null,
        university: "示例大学",
        school: "计算机学院",
        department: null,
        research_direction: null,
        recent_papers: [],
        profile_url: null,
        source_url: "https://example.edu/faculty",
        confidence: 0.86,
        field_confidence: null,
        evidence: null,
        review_status: "pending",
        created_at: "2026-04-26T10:02:00Z",
        updated_at: "2026-04-26T10:02:00Z",
      },
    ]);

    renderPage();

    fireEvent.click(screen.getByRole("button", { name: "教师抓取" }));
    fireEvent.click(await screen.findByRole("button", { name: "查看详情" }));

    const dialog = await screen.findByRole("dialog", { name: "抓取任务详情" });
    expect(
      within(dialog).getByText(
        "请先将任务转入待审核状态，再补全或审核导入候选导师。",
      ),
    ).toBeInTheDocument();
    expect(within(dialog).getByText("张教授")).toBeInTheDocument();
    expect(
      within(dialog).queryByRole("checkbox", {
        name: "选择候选导师 张教授",
      }),
    ).not.toBeInTheDocument();
    expect(
      within(dialog).queryByRole("button", { name: "补全缺失信息" }),
    ).not.toBeInTheDocument();
    expect(
      within(dialog).queryByRole("button", { name: "审核通过并导入" }),
    ).not.toBeInTheDocument();
    expect(
      within(dialog).getByText("先转入待审核后才可补全或审核导入"),
    ).toBeInTheDocument();
    expect(approveCrawlCandidates).not.toHaveBeenCalled();
    expect(enrichCrawlCandidates).not.toHaveBeenCalled();
  });

  it("allows reviewing saved candidates after a canceled crawl job is resumed for review", async () => {
    const reviewJob = {
      ...runningJob,
      status: "needs_review",
    } as const;
    vi.mocked(listCrawlJobs).mockResolvedValue([reviewJob]);
    vi.mocked(getCrawlJob).mockResolvedValue(reviewJob);
    vi.mocked(listCrawlCandidates).mockResolvedValue([
      {
        id: 21,
        job_id: 7,
        professor_id: null,
        name: "张教授",
        email: "zhang@example.edu",
        title: null,
        university: "示例大学",
        school: "计算机学院",
        department: null,
        research_direction: null,
        recent_papers: [],
        profile_url: null,
        source_url: "https://example.edu/faculty",
        confidence: 0.86,
        field_confidence: null,
        evidence: null,
        review_status: "pending",
        created_at: "2026-04-26T10:02:00Z",
        updated_at: "2026-04-26T10:02:00Z",
      },
    ]);

    renderPage();

    fireEvent.click(screen.getByRole("button", { name: "教师抓取" }));
    fireEvent.click(await screen.findByRole("button", { name: "查看详情" }));

    const dialog = await screen.findByRole("dialog", { name: "抓取任务详情" });
    fireEvent.click(
      within(dialog).getByRole("checkbox", { name: "选择候选导师 张教授" }),
    );
    fireEvent.click(
      within(dialog).getByRole("button", { name: "审核通过并导入" }),
    );

    await waitFor(() => {
      expect(approveCrawlCandidates).toHaveBeenCalledWith(7, [21]);
    });
  });

  it("allows continuing review and enrichment from a partially imported crawl job", async () => {
    const partiallyCompletedJob = {
      ...runningJob,
      status: "partially_completed",
    } as const;
    vi.mocked(listCrawlJobs).mockResolvedValue([partiallyCompletedJob]);
    vi.mocked(getCrawlJob).mockResolvedValue(partiallyCompletedJob);
    vi.mocked(listCrawlCandidates).mockResolvedValue([
      {
        id: 21,
        job_id: 7,
        professor_id: null,
        name: "张教授",
        email: null,
        title: null,
        university: "示例大学",
        school: "计算机学院",
        department: null,
        research_direction: null,
        recent_papers: [],
        profile_url: null,
        source_url: "https://example.edu/faculty",
        confidence: 0.86,
        field_confidence: null,
        evidence: null,
        review_status: "pending",
        created_at: "2026-04-26T10:02:00Z",
        updated_at: "2026-04-26T10:02:00Z",
      },
      {
        id: 22,
        job_id: 7,
        professor_id: null,
        name: "李教授",
        email: "li@example.edu",
        title: null,
        university: "示例大学",
        school: "计算机学院",
        department: null,
        research_direction: null,
        recent_papers: [],
        profile_url: null,
        source_url: "https://example.edu/faculty",
        confidence: 0.86,
        field_confidence: null,
        evidence: null,
        review_status: "pending",
        created_at: "2026-04-26T10:02:00Z",
        updated_at: "2026-04-26T10:02:00Z",
      },
    ]);

    renderPage();

    fireEvent.click(screen.getByRole("button", { name: "教师抓取" }));
    expect(await screen.findByText("部分已导入")).toBeInTheDocument();
    const crawlJobCard = screen
      .getByText("示例大学 / 计算机学院")
      .closest("article");
    expect(crawlJobCard).not.toBeNull();
    expect(
      within(crawlJobCard as HTMLElement).getByRole("button", { name: "删除" }),
    ).toBeInTheDocument();

    fireEvent.click(await screen.findByRole("button", { name: "查看详情" }));

    const dialog = await screen.findByRole("dialog", { name: "抓取任务详情" });
    expect(within(dialog).getByText(/可导入\s+2\s+位/)).toBeInTheDocument();
    expect(within(dialog).getByRole("button", { name: "全选无邮箱" })).toBeInTheDocument();
    expect(within(dialog).getByRole("button", { name: "补全缺失信息" })).toBeInTheDocument();

    fireEvent.click(within(dialog).getByRole("button", { name: "全选无邮箱" }));
    fireEvent.click(within(dialog).getByRole("button", { name: "补全缺失信息" }));

    await waitFor(() => {
      expect(enrichCrawlCandidates).toHaveBeenCalledWith(7, [21], 2);
    });

    fireEvent.click(within(dialog).getByRole("button", { name: "清空选择" }));
    fireEvent.click(
      within(dialog).getByRole("checkbox", { name: "选择候选导师 李教授" }),
    );
    fireEvent.click(
      within(dialog).getByRole("button", { name: "审核通过并导入" }),
    );

    await waitFor(() => {
      expect(approveCrawlCandidates).toHaveBeenCalledWith(7, [22]);
    });
  });

  it("selects only reviewable candidates without email for enrichment", async () => {
    const reviewJob = {
      ...runningJob,
      status: "needs_review",
    } as const;
    vi.mocked(listCrawlJobs).mockResolvedValue([reviewJob]);
    vi.mocked(getCrawlJob).mockResolvedValue(reviewJob);
    vi.mocked(listCrawlCandidates).mockResolvedValue([
      {
        id: 21,
        job_id: 7,
        professor_id: null,
        name: "张教授",
        email: null,
        title: null,
        university: "示例大学",
        school: "计算机学院",
        department: null,
        research_direction: null,
        recent_papers: [],
        profile_url: null,
        source_url: "https://example.edu/faculty",
        confidence: 0.86,
        field_confidence: null,
        evidence: null,
        review_status: "pending",
        created_at: "2026-04-26T10:02:00Z",
        updated_at: "2026-04-26T10:02:00Z",
      },
      {
        id: 22,
        job_id: 7,
        professor_id: null,
        name: "李教授",
        email: "li@example.edu",
        title: null,
        university: "示例大学",
        school: "计算机学院",
        department: null,
        research_direction: null,
        recent_papers: [],
        profile_url: null,
        source_url: "https://example.edu/faculty",
        confidence: 0.86,
        field_confidence: null,
        evidence: null,
        review_status: "pending",
        created_at: "2026-04-26T10:02:00Z",
        updated_at: "2026-04-26T10:02:00Z",
      },
      {
        id: 23,
        job_id: 7,
        professor_id: null,
        name: "王教授",
        email: "",
        title: null,
        university: "示例大学",
        school: "计算机学院",
        department: null,
        research_direction: null,
        recent_papers: [],
        profile_url: null,
        source_url: "https://example.edu/faculty",
        confidence: 0.86,
        field_confidence: null,
        evidence: null,
        review_status: "pending",
        created_at: "2026-04-26T10:02:00Z",
        updated_at: "2026-04-26T10:02:00Z",
      },
      {
        id: 24,
        job_id: 7,
        professor_id: null,
        name: "赵教授",
        email: null,
        title: null,
        university: "示例大学",
        school: "计算机学院",
        department: null,
        research_direction: null,
        recent_papers: [],
        profile_url: null,
        source_url: "https://example.edu/faculty",
        confidence: 0.86,
        field_confidence: null,
        evidence: null,
        review_status: "rejected",
        created_at: "2026-04-26T10:02:00Z",
        updated_at: "2026-04-26T10:02:00Z",
      },
    ]);

    renderPage();

    fireEvent.click(screen.getByRole("button", { name: "教师抓取" }));
    fireEvent.click(await screen.findByRole("button", { name: "查看详情" }));

    const dialog = await screen.findByRole("dialog", { name: "抓取任务详情" });
    fireEvent.click(within(dialog).getByRole("button", { name: "全选无邮箱" }));
    expect(within(dialog).getByText(/已选\s+2\s+位/)).toBeInTheDocument();

    fireEvent.click(within(dialog).getByRole("button", { name: "补全缺失信息" }));

    await waitFor(() => {
      expect(enrichCrawlCandidates).toHaveBeenCalledWith(7, [21, 23], 2);
    });
  });

  it("labels crawl candidate enrichment enqueue as started instead of completed", async () => {
    const reviewJob = {
      ...runningJob,
      status: "needs_review",
    } as const;
    vi.mocked(listCrawlJobs).mockResolvedValue([reviewJob]);
    vi.mocked(getCrawlJob).mockResolvedValue(reviewJob);
    vi.mocked(enrichCrawlCandidates).mockResolvedValue({
      selected_count: 1,
      enriched_count: 0,
      unchanged_count: 0,
      failed_count: 0,
      skipped_count: 0,
      message: "已加入补全队列：选中 1 位，入队 1 位。",
    });

    renderPage();

    fireEvent.click(screen.getByRole("button", { name: "教师抓取" }));
    fireEvent.click(await screen.findByRole("button", { name: "查看详情" }));

    const dialog = await screen.findByRole("dialog", { name: "抓取任务详情" });
    fireEvent.click(within(dialog).getByRole("button", { name: "全选无邮箱" }));
    fireEvent.click(within(dialog).getByRole("button", { name: "补全缺失信息" }));

    await waitFor(() => {
      expect(notifySuccess).toHaveBeenCalledWith(
        "候选信息补全已开始",
        "已加入补全队列：选中 1 位，入队 1 位。",
      );
    });
    expect(notifySuccess).not.toHaveBeenCalledWith(
      "候选信息补全完成",
      expect.any(String),
    );
  });

  it("notifies once when a user-started crawl candidate enrichment finishes", async () => {
    const reviewJob = {
      ...runningJob,
      status: "needs_review",
    } as const;
    vi.mocked(listCrawlJobs).mockResolvedValue([reviewJob]);
    vi.mocked(getCrawlJob).mockResolvedValue(reviewJob);
    vi.mocked(enrichCrawlCandidates).mockResolvedValue({
      selected_count: 1,
      enriched_count: 0,
      unchanged_count: 0,
      failed_count: 0,
      skipped_count: 0,
      message: "已加入补全队列：选中 1 位，入队 1 位。",
    });
    vi.mocked(getCrawlJobEvents)
      .mockResolvedValueOnce([
        {
          id: "evt-1",
          job_id: 7,
          event_type: "crawl_page",
          message: "调用 crawl_page 抓取入口页面",
          created_at: "2026-04-26T08:34:00",
          raw: null,
        },
      ])
      .mockResolvedValueOnce([
        {
          id: "evt-2",
          job_id: 7,
          event_type: "enrichment",
          message: "候选导师详情补全完成：成功 1 位，未变化 0 位，失败 0 位",
          created_at: "2026-04-26T08:35:00",
          raw: {
            candidate_count: 1,
            enriched_count: 1,
            unchanged_count: 0,
            failed_count: 0,
          },
        },
      ]);

    renderPage();

    fireEvent.click(screen.getByRole("button", { name: "教师抓取" }));
    fireEvent.click(await screen.findByRole("button", { name: "查看详情" }));

    const dialog = await screen.findByRole("dialog", { name: "抓取任务详情" });
    fireEvent.click(within(dialog).getByRole("button", { name: "全选无邮箱" }));
    fireEvent.click(within(dialog).getByRole("button", { name: "补全缺失信息" }));

    await waitFor(() => {
      expect(notifySuccess).toHaveBeenCalledWith(
        "候选信息补全已开始",
        "已加入补全队列：选中 1 位，入队 1 位。",
      );
    });
    await waitFor(() => {
      expect(notifySuccess).toHaveBeenCalledWith(
        "候选信息补全完成",
        "候选导师详情补全完成：成功 1 位，未变化 0 位，失败 0 位",
      );
    });

    await waitFor(() => {
      expect(notifySuccess).toHaveBeenCalledTimes(2);
    });
  });
});
