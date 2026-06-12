import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { NotificationProvider } from "@/context/NotificationContext";
import { clearDiagnosticEvents, getDiagnosticEvents } from "@/lib/diagnostics";
import { ProfessorsPage } from "@/pages/ProfessorsPage";

const mockedUseSelectionContext = vi.hoisted(() => vi.fn());
const listProfessorsForManagement = vi.hoisted(() => vi.fn());
const createCrawlJob = vi.hoisted(() => vi.fn());
const listCrawlJobs = vi.hoisted(() => vi.fn());
const listCrawlCandidates = vi.hoisted(() => vi.fn());

vi.mock("@/context/SelectionContext", () => ({
  useSelectionContext: mockedUseSelectionContext,
}));

vi.mock("@/lib/api/professorsApi", () => ({
  listProfessorsForManagement,
  archiveProfessor: vi.fn(),
  bulkArchiveProfessors: vi.fn(),
  createProfessor: vi.fn(),
  getProfessorTemplateDownloadUrl: vi.fn(() => "/templates/professors.xlsx"),
  importProfessorsFromFile: vi.fn(),
  restoreProfessor: vi.fn(),
  triggerCrawler: vi.fn(),
  updateProfessor: vi.fn(),
}));

vi.mock("@/lib/api/crawlJobsApi", () => ({
  createCrawlJob,
  listCrawlJobs,
  listCrawlCandidates,
}));

const renderPage = () =>
  render(
    <MemoryRouter>
      <NotificationProvider>
        <ProfessorsPage />
      </NotificationProvider>
    </MemoryRouter>,
  );

describe("ProfessorsPage crawler job entry", () => {
  beforeEach(() => {
    clearDiagnosticEvents();
    listProfessorsForManagement.mockReset();
    listProfessorsForManagement.mockResolvedValue([]);
    createCrawlJob.mockReset();
    createCrawlJob.mockResolvedValue({ id: 1 });
    listCrawlJobs.mockReset();
    listCrawlCandidates.mockReset();
    mockedUseSelectionContext.mockReset();
    mockedUseSelectionContext.mockReturnValue({
      identities: [],
      llmProfiles: [],
      selectedIdentityId: 1,
      selectedLlmProfileId: 7,
      selectedIdentity: null,
      selectedLlmProfile: null,
      loading: false,
      setSelectedIdentityId: vi.fn(),
      setSelectedLlmProfileId: vi.fn(),
      refreshSelections: vi.fn(),
    });
  });

  it("creates a crawl job with multiple unique list page urls", async () => {
    renderPage();

    await waitFor(() => {
      expect(listProfessorsForManagement).toHaveBeenCalledWith("active");
    });

    fireEvent.click(screen.getByRole("button", { name: "智能抓取" }));

    const dialog = screen.getByRole("dialog", { name: "创建抓取任务" });
    fireEvent.change(within(dialog).getByLabelText("学校"), {
      target: { value: "示例大学" },
    });
    fireEvent.change(within(dialog).getByLabelText("学院"), {
      target: { value: "计算机学院" },
    });
    fireEvent.change(within(dialog).getByLabelText("页面 URL"), {
      target: { value: "https://example.edu/faculty" },
    });
    fireEvent.click(within(dialog).getByRole("button", { name: "添加页面 URL" }));
    fireEvent.change(within(dialog).getAllByLabelText("页面 URL")[1], {
      target: { value: " https://example.edu/faculty/page/2 " },
    });

    fireEvent.click(within(dialog).getByRole("button", { name: "开始抓取" }));

    await waitFor(() => {
      expect(createCrawlJob).toHaveBeenCalledWith({
        university: "示例大学",
        school: "计算机学院",
        start_url: "https://example.edu/faculty",
        start_urls: [
          "https://example.edu/faculty",
          "https://example.edu/faculty/page/2",
        ],
        entry_type: "list",
        llm_profile_id: 7,
      });
    });

    expect(getDiagnosticEvents()).toEqual(
      expect.arrayContaining([
        expect.objectContaining({
          category: "user_action",
          eventName: "professors.crawler_dialog_opened",
        }),
        expect.objectContaining({
          category: "user_action",
          eventName: "professors.crawl_job_create_submitted",
          data: {
            university: "示例大学",
            school: "计算机学院",
            start_url: "https://example.edu/faculty",
            start_urls: [
              "https://example.edu/faculty",
              "https://example.edu/faculty/page/2",
            ],
            entry_type: "list",
          },
        }),
        expect.objectContaining({
          category: "user_action",
          eventName: "professors.crawl_job_create_succeeded",
        }),
      ]),
    );
    expect(await screen.findByText("任务中心会继续后台抓取，请到任务中心的教师抓取页签查看进度。")).toBeInTheDocument();
  });

  it("expands pasted multiline crawler urls into separate rows", async () => {
    renderPage();

    await waitFor(() => {
      expect(listProfessorsForManagement).toHaveBeenCalledWith("active");
    });

    fireEvent.click(screen.getByRole("button", { name: "智能抓取" }));

    const dialog = screen.getByRole("dialog", { name: "创建抓取任务" });
    fireEvent.change(within(dialog).getByLabelText("学校"), {
      target: { value: "示例大学" },
    });
    fireEvent.change(within(dialog).getByLabelText("学院"), {
      target: { value: "计算机学院" },
    });

    fireEvent.paste(within(dialog).getByLabelText("页面 URL"), {
      clipboardData: {
        getData: () =>
          [
            " https://example.edu/faculty ",
            "",
            "https://example.edu/faculty/page/2",
            "https://example.edu/faculty",
          ].join("\n"),
      },
    });

    const urlInputs = within(dialog).getAllByLabelText("页面 URL");
    expect(urlInputs).toHaveLength(2);
    expect(urlInputs[0]).toHaveValue("https://example.edu/faculty");
    expect(urlInputs[1]).toHaveValue("https://example.edu/faculty/page/2");

    fireEvent.click(within(dialog).getByRole("button", { name: "开始抓取" }));

    await waitFor(() => {
      expect(createCrawlJob).toHaveBeenCalledWith({
        university: "示例大学",
        school: "计算机学院",
        start_url: "https://example.edu/faculty",
        start_urls: [
          "https://example.edu/faculty",
          "https://example.edu/faculty/page/2",
        ],
        entry_type: "list",
        llm_profile_id: 7,
      });
    });
  });

  it("removes an added crawler url row", async () => {
    renderPage();

    await waitFor(() => {
      expect(listProfessorsForManagement).toHaveBeenCalledWith("active");
    });

    fireEvent.click(screen.getByRole("button", { name: "智能抓取" }));

    const dialog = screen.getByRole("dialog", { name: "创建抓取任务" });
    fireEvent.click(within(dialog).getByRole("button", { name: "添加页面 URL" }));
    expect(within(dialog).getAllByLabelText("页面 URL")).toHaveLength(2);

    fireEvent.click(within(dialog).getAllByRole("button", { name: "移除页面 URL" })[1]);
    expect(within(dialog).getAllByLabelText("页面 URL")).toHaveLength(1);
  });

  it("adds and focuses the next crawler url row when Enter is pressed on a filled row", async () => {
    renderPage();

    await waitFor(() => {
      expect(listProfessorsForManagement).toHaveBeenCalledWith("active");
    });

    fireEvent.click(screen.getByRole("button", { name: "智能抓取" }));

    const dialog = screen.getByRole("dialog", { name: "创建抓取任务" });
    const firstUrlInput = within(dialog).getByLabelText("页面 URL");
    fireEvent.change(firstUrlInput, {
      target: { value: "https://example.edu/faculty" },
    });
    fireEvent.keyDown(firstUrlInput, { key: "Enter" });

    let urlInputs = within(dialog).getAllByLabelText("页面 URL");
    expect(urlInputs).toHaveLength(2);
    expect(urlInputs[0]).toHaveValue("https://example.edu/faculty");
    expect(urlInputs[1]).toHaveValue("");
    expect(urlInputs[1]).toHaveFocus();

    fireEvent.keyDown(urlInputs[1], { key: "Enter" });
    urlInputs = within(dialog).getAllByLabelText("页面 URL");
    expect(urlInputs).toHaveLength(2);
  });

  it("creates a profile crawl job when profile entry type is selected", async () => {
    renderPage();

    await waitFor(() => {
      expect(listProfessorsForManagement).toHaveBeenCalledWith("active");
    });

    fireEvent.click(screen.getByRole("button", { name: "智能抓取" }));

    const dialog = screen.getByRole("dialog", { name: "创建抓取任务" });
    fireEvent.click(within(dialog).getByRole("radio", { name: "详情页" }));
    fireEvent.change(within(dialog).getByLabelText("学校"), {
      target: { value: "示例大学" },
    });
    fireEvent.change(within(dialog).getByLabelText("学院"), {
      target: { value: "计算机学院" },
    });
    fireEvent.change(within(dialog).getByLabelText("页面 URL"), {
      target: { value: "https://example.edu/faculty/zhang" },
    });

    fireEvent.click(within(dialog).getByRole("button", { name: "开始抓取" }));

    await waitFor(() => {
      expect(createCrawlJob).toHaveBeenCalledWith({
        university: "示例大学",
        school: "计算机学院",
        start_url: "https://example.edu/faculty/zhang",
        start_urls: ["https://example.edu/faculty/zhang"],
        entry_type: "profile",
        llm_profile_id: 7,
      });
    });
  });

  it("does not create a crawl job without a selected llm profile", async () => {
    mockedUseSelectionContext.mockReturnValue({
      identities: [],
      llmProfiles: [],
      selectedIdentityId: 1,
      selectedLlmProfileId: null,
      selectedIdentity: null,
      selectedLlmProfile: null,
      loading: false,
      setSelectedIdentityId: vi.fn(),
      setSelectedLlmProfileId: vi.fn(),
      refreshSelections: vi.fn(),
    });

    renderPage();

    await waitFor(() => {
      expect(listProfessorsForManagement).toHaveBeenCalledWith("active");
    });

    fireEvent.click(screen.getByRole("button", { name: "智能抓取" }));

    const dialog = screen.getByRole("dialog", { name: "创建抓取任务" });
    fireEvent.change(within(dialog).getByLabelText("学校"), {
      target: { value: "示例大学" },
    });
    fireEvent.change(within(dialog).getByLabelText("学院"), {
      target: { value: "计算机学院" },
    });
    fireEvent.change(within(dialog).getByLabelText("页面 URL"), {
      target: { value: "https://example.edu/faculty" },
    });

    fireEvent.click(within(dialog).getByRole("button", { name: "开始抓取" }));

    expect(createCrawlJob).not.toHaveBeenCalled();
    expect(screen.getByText("请先选择模型")).toBeInTheDocument();
  });

  it("records a failed crawler job creation as a user action", async () => {
    createCrawlJob.mockRejectedValue(new Error("backend unavailable"));

    renderPage();

    await waitFor(() => {
      expect(listProfessorsForManagement).toHaveBeenCalledWith("active");
    });

    fireEvent.click(screen.getByRole("button", { name: "智能抓取" }));

    const dialog = screen.getByRole("dialog", { name: "创建抓取任务" });
    fireEvent.change(within(dialog).getByLabelText("学校"), {
      target: { value: "示例大学" },
    });
    fireEvent.change(within(dialog).getByLabelText("学院"), {
      target: { value: "计算机学院" },
    });
    fireEvent.change(within(dialog).getByLabelText("页面 URL"), {
      target: { value: "https://example.edu/faculty?token=secret#frag" },
    });

    fireEvent.click(within(dialog).getByRole("button", { name: "开始抓取" }));

    await waitFor(() => {
      expect(createCrawlJob).toHaveBeenCalled();
    });

    expect(getDiagnosticEvents()).toEqual(
      expect.arrayContaining([
        expect.objectContaining({
          category: "user_action",
          eventName: "professors.crawl_job_create_failed",
          message: "backend unavailable",
        }),
      ]),
    );
  });
});
