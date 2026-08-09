import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { BackgroundTaskNotificationProvider } from "@/app/providers/BackgroundTaskNotificationContext";
import { NotificationProvider } from "@/context/NotificationContext";
import { formatApiDateTime } from "@/lib/dateTime";
import { ProfessorsPage } from "@/pages/ProfessorsPage";
import type {
  ProfessorInformationEnrichmentItemDTO,
  ProfessorInformationEnrichmentJobDTO,
  ProfessorManagementItemDTO,
} from "@/types";

const mockedUseSelectionContext = vi.hoisted(() => vi.fn());
const listProfessorsForManagement = vi.hoisted(() => vi.fn());
const downloadProfessorExport = vi.hoisted(() => vi.fn());
const downloadProfessorTemplate = vi.hoisted(() => vi.fn());
const downloadCommunitySharePackage = vi.hoisted(() => vi.fn());
const updateProfessor = vi.hoisted(() => vi.fn());
const updateProfessorNote = vi.hoisted(() => vi.fn());
const createSingleProfessorInformationEnrichment = vi.hoisted(() => vi.fn());
const getActiveProfessorInformationEnrichment = vi.hoisted(() => vi.fn());
const getProfessorInformationEnrichmentJob = vi.hoisted(() => vi.fn());
const listProfessorInformationEnrichmentItems = vi.hoisted(() => vi.fn());
const createProfessorInformationEnrichmentJob = vi.hoisted(() => vi.fn());

vi.mock("@/context/SelectionContext", () => ({
  useSelectionContext: mockedUseSelectionContext,
}));

vi.mock("@/entities/professor/api/professors", () => ({
  listProfessorsForManagement,
  archiveProfessor: vi.fn(),
  bulkArchiveProfessors: vi.fn(),
  createProfessor: vi.fn(),
  downloadProfessorExport,
  downloadProfessorTemplate,
  importProfessorsFromFile: vi.fn(),
  restoreProfessor: vi.fn(),
  triggerCrawler: vi.fn(),
  updateProfessor,
  updateProfessorNote,
}));

vi.mock("@/entities/community-mentor/api/communityMentors", () => ({
  downloadCommunitySharePackage,
}));

vi.mock("@/entities/professor/api/informationEnrichment", () => ({
  createSingleProfessorInformationEnrichment,
  getActiveProfessorInformationEnrichment,
  getProfessorInformationEnrichmentJob,
  listProfessorInformationEnrichmentItems,
  createProfessorInformationEnrichmentJob,
}));

const professor: ProfessorManagementItemDTO = {
  id: 1,
  name: "李教授",
  email: "li@example.edu",
  title: "Associate Professor",
  university: "测试大学",
  school: "计算机学院",
  department: "人工智能系",
  research_direction: "机器学习与人机协作",
  personal_note: "已有备注",
  recent_papers: ["Paper A"],
  profile_url: "https://example.edu/li",
  source_url: null,
  crawl_status: "manual",
  skip_reason: null,
  archived_at: null,
  created_at: "2026-04-22T00:00:00Z",
  updated_at: "2026-04-23T00:00:00Z",
  tags: [],
};

const anotherProfessor: ProfessorManagementItemDTO = {
  id: 2,
  name: "王教授",
  email: "wang@example.edu",
  title: "Professor",
  university: "样例大学",
  school: "生命科学学院",
  department: "生物信息系",
  research_direction: "计算生物学",
  personal_note: null,
  recent_papers: ["Paper B"],
  profile_url: "https://example.edu/wang",
  source_url: null,
  crawl_status: "manual",
  skip_reason: null,
  archived_at: null,
  created_at: "2026-04-22T00:00:00Z",
  updated_at: "2026-04-24T00:00:00Z",
  tags: [],
};

const buildProfessor = (id: number): ProfessorManagementItemDTO => ({
  ...professor,
  id,
  name: `导师 ${id}`,
  email: `professor-${id}@example.edu`,
});

const informationEnrichmentJob: ProfessorInformationEnrichmentJobDTO = {
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
  started_at: "2026-04-24T00:00:00Z",
  finished_at: null,
  duration_seconds: 0,
  created_at: "2026-04-24T00:00:00Z",
  updated_at: "2026-04-24T00:00:00Z",
  deleted_at: null,
  last_error: null,
};

const buildInformationEnrichmentItem = (
  overrides: Partial<ProfessorInformationEnrichmentItemDTO> = {},
): ProfessorInformationEnrichmentItemDTO => ({
  id: 81,
  job_id: informationEnrichmentJob.id,
  professor_id: professor.id,
  professor_name: professor.name,
  professor_email: professor.email,
  professor_title: professor.title,
  professor_university: professor.university,
  professor_school: professor.school,
  professor_department: professor.department,
  profile_url: professor.profile_url,
  status: "succeeded",
  enriched_fields: ["department"],
  error_message: null,
  skip_reason: null,
  input_tokens: 100,
  output_tokens: 20,
  cached_tokens: 0,
  total_tokens: 120,
  attempt_count: 1,
  started_at: "2026-04-24T00:00:00Z",
  finished_at: "2026-04-24T00:00:20Z",
  created_at: "2026-04-24T00:00:00Z",
  updated_at: "2026-04-24T00:00:20Z",
  ...overrides,
});

const renderPage = (initialEntry = "/professors") =>
  render(
    <MemoryRouter initialEntries={[initialEntry]}>
      <NotificationProvider>
        <BackgroundTaskNotificationProvider>
          <ProfessorsPage />
        </BackgroundTaskNotificationProvider>
      </NotificationProvider>
    </MemoryRouter>,
  );

const expectToAppearBefore = (first: HTMLElement, second: HTMLElement) => {
  expect(first.compareDocumentPosition(second)).toBe(
    Node.DOCUMENT_POSITION_FOLLOWING,
  );
};

describe("ProfessorsPage layout", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  beforeEach(() => {
    localStorage.clear();
    sessionStorage.clear();
    Reflect.deleteProperty(window, "autoEmailSender");
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
    listProfessorsForManagement.mockReset();
    listProfessorsForManagement.mockResolvedValue([professor]);
    downloadProfessorExport.mockReset();
    downloadProfessorExport.mockResolvedValue(undefined);
    downloadProfessorTemplate.mockReset();
    downloadProfessorTemplate.mockResolvedValue(undefined);
    downloadCommunitySharePackage.mockReset();
    downloadCommunitySharePackage.mockResolvedValue(
      new Blob(["community-share"]),
    );
    updateProfessor.mockReset();
    updateProfessor.mockResolvedValue(professor);
    updateProfessorNote.mockReset();
    updateProfessorNote.mockResolvedValue({
      id: professor.id,
      personal_note: null,
      updated_at: "2026-04-24T00:00:00Z",
    });
    createSingleProfessorInformationEnrichment.mockReset();
    createSingleProfessorInformationEnrichment.mockResolvedValue(
      informationEnrichmentJob,
    );
    getActiveProfessorInformationEnrichment.mockReset();
    getActiveProfessorInformationEnrichment.mockResolvedValue({
      active: false,
      job: null,
    });
    getProfessorInformationEnrichmentJob.mockReset();
    getProfessorInformationEnrichmentJob.mockResolvedValue(
      informationEnrichmentJob,
    );
    listProfessorInformationEnrichmentItems.mockReset();
    listProfessorInformationEnrichmentItems.mockResolvedValue([]);
    createProfessorInformationEnrichmentJob.mockReset();
    createProfessorInformationEnrichmentJob.mockResolvedValue({
      ...informationEnrichmentJob,
      id: 72,
      name: "信息补全 2026-04-24",
      trigger_mode: "batch",
      status: "queued",
      queued_count: 1,
      running_count: 0,
    });
  });

  it("guides community visitors through a school or college batch contribution", async () => {
    renderPage("/professors?community_contribution=batch");

    await waitFor(() => {
      expect(listProfessorsForManagement).toHaveBeenCalledWith("active");
    });

    const guide = screen.getByTestId("community-batch-contribution-guide");
    expect(
      within(guide).getByRole("heading", { name: "按学校/学院批量贡献" }),
    ).toBeInTheDocument();
    expect(within(guide).getByText(/筛选并全选目标学校或学院/)).toBeInTheDocument();
    expect(within(guide).getByText(/贡献到社区/)).toBeInTheDocument();

    fireEvent.click(within(guide).getByRole("button", { name: "关闭提示" }));
    await waitFor(() => {
      expect(
        screen.queryByTestId("community-batch-contribution-guide"),
      ).not.toBeInTheDocument();
    });
  });

  it("waits for desktop save completion before opening the GitHub contribution form", async () => {
    const openExternalUrl = vi.fn().mockResolvedValue(undefined);
    let resolveSave: ((result: { status: "saved" }) => void) | undefined;
    const saveResult = new Promise<{ status: "saved" }>((resolve) => {
      resolveSave = resolve;
    });
    const saveCommunitySharePackage = vi.fn(() => saveResult);
    window.autoEmailSender = {
      getVersion: async () => "0.1.0",
      openExternalUrl,
      saveCommunitySharePackage,
      checkForUpdate: vi.fn(),
      downloadUpdate: vi.fn(),
      switchToFullDownload: vi.fn(),
      quitAndInstall: vi.fn(),
      onUpdateStatus: () => () => undefined,
    };
    listProfessorsForManagement.mockResolvedValue([
      { ...professor, source_url: "https://example.edu/li" },
    ]);
    renderPage("/professors?community_contribution=batch");

    await waitFor(() => {
      expect(listProfessorsForManagement).toHaveBeenCalledWith("active");
    });
    fireEvent.click(screen.getByRole("button", { name: "选择 李教授" }));
    const locationBeforeContribution = window.location.href;

    const contributionButton = screen.getByRole("button", {
      name: "贡献到社区",
    });
    expect(contributionButton.parentElement?.lastElementChild).toBe(
      contributionButton,
    );

    fireEvent.click(contributionButton);

    await waitFor(() => {
      expect(downloadCommunitySharePackage).toHaveBeenCalledWith([1]);
      expect(saveCommunitySharePackage).toHaveBeenCalledWith(
        expect.any(ArrayBuffer),
      );
    });
    expect(openExternalUrl).not.toHaveBeenCalled();
    expect(window.location.href).toBe(locationBeforeContribution);

    resolveSave?.({ status: "saved" });

    await waitFor(() => {
      expect(openExternalUrl).toHaveBeenCalledWith(
        expect.stringContaining("template=batch-contribution.yml"),
      );
    });
    const contributionUrl = new URL(openExternalUrl.mock.calls[0][0]);
    expect(contributionUrl.searchParams.get("title")).toBe(
      "[批量投稿] 测试大学计算机学院",
    );
    expect(window.location.href).toBe(locationBeforeContribution);
  });

  it("does not open GitHub when desktop share package saving is canceled", async () => {
    const openExternalUrl = vi.fn().mockResolvedValue(undefined);
    const saveCommunitySharePackage = vi
      .fn()
      .mockResolvedValue({ status: "canceled" as const });
    window.autoEmailSender = {
      getVersion: async () => "0.1.0",
      openExternalUrl,
      saveCommunitySharePackage,
      checkForUpdate: vi.fn(),
      downloadUpdate: vi.fn(),
      switchToFullDownload: vi.fn(),
      quitAndInstall: vi.fn(),
      onUpdateStatus: () => () => undefined,
    };
    listProfessorsForManagement.mockResolvedValue([
      { ...professor, source_url: "https://example.edu/li" },
    ]);
    renderPage("/professors?community_contribution=batch");

    await waitFor(() => {
      expect(listProfessorsForManagement).toHaveBeenCalledWith("active");
    });
    fireEvent.click(screen.getByRole("button", { name: "选择 李教授" }));
    fireEvent.click(screen.getByRole("button", { name: "贡献到社区" }));

    expect(await screen.findByText("已取消保存")).toBeInTheDocument();
    expect(
      screen.getByText("共享包未保存，因此没有打开 GitHub 投稿页。"),
    ).toBeInTheDocument();
    expect(saveCommunitySharePackage).toHaveBeenCalledTimes(1);
    expect(openExternalUrl).not.toHaveBeenCalled();
  });

  it("keeps the app open when the community share package request fails", async () => {
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
    downloadCommunitySharePackage.mockRejectedValueOnce(
      new Error("共享包下载失败"),
    );
    listProfessorsForManagement.mockResolvedValue([
      { ...professor, source_url: "https://example.edu/li" },
    ]);
    renderPage("/professors?community_contribution=batch");

    await waitFor(() => {
      expect(listProfessorsForManagement).toHaveBeenCalledWith("active");
    });
    fireEvent.click(screen.getByRole("button", { name: "选择 李教授" }));
    const locationBeforeContribution = window.location.href;
    fireEvent.click(screen.getByRole("button", { name: "贡献到社区" }));

    expect(await screen.findByText("贡献准备失败")).toBeInTheDocument();
    expect(screen.getByText("共享包下载失败")).toBeInTheDocument();
    expect(window.location.href).toBe(locationBeforeContribution);
    expect(openExternalUrl).not.toHaveBeenCalled();
  });

  it("omits the low-value summary cards from the workbench header", async () => {
    renderPage();

    await waitFor(() => {
      expect(listProfessorsForManagement).toHaveBeenCalledWith("active");
    });

    expect(screen.queryByText("当前列表")).not.toBeInTheDocument();
    expect(screen.queryByText("当前筛选")).not.toBeInTheDocument();
    expect(screen.queryByText("已选择")).not.toBeInTheDocument();
  });

  it("keeps row field labels inside each professor record for responsive reading", async () => {
    renderPage();

    await waitFor(() => {
      expect(listProfessorsForManagement).toHaveBeenCalledWith("active");
    });

    const row = screen.getByText("李教授").closest("article");
    expect(row).not.toBeNull();
    const record = within(row as HTMLElement);

    expect(record.getByText("邮箱")).toBeInTheDocument();
    expect(record.getByText("职称")).toBeInTheDocument();
    expect(record.getByText("学校 / 学院")).toBeInTheDocument();
    expect(record.getByText("研究方向")).toBeInTheDocument();
    expect(record.getByText("更新时间")).toBeInTheDocument();
    expect(record.queryByText("Associate Professor / 测试大学 / 计算机学院")).not.toBeInTheDocument();
    expect(record.getByText("Associate / Professor")).toHaveClass("lg:text-center");
    expect(record.getAllByText("机器学习与人机协作")).toHaveLength(1);
    expect(record.getByRole("button", { name: "选择 李教授" })).toHaveAttribute(
      "aria-pressed",
      "false",
    );
    expect(
      record.queryByRole("checkbox", { name: "选择 李教授" }),
    ).not.toBeInTheDocument();
    expect(row?.firstElementChild).toHaveClass("lg:items-center");
    expect(record.getByRole("button", { name: "选择 李教授" }).parentElement).toHaveClass(
      "justify-center",
    );
  });

  it("centers every desktop table header within its column", async () => {
    renderPage();

    await waitFor(() => {
      expect(listProfessorsForManagement).toHaveBeenCalledWith("active");
    });

    const expectedHeaders = [
      "选择",
      "导师",
      "职称",
      "邮箱",
      "学校 / 学院",
      "研究方向",
      "更新时间",
      "操作",
    ];

    const header = await screen.findByTestId("professor-table-header");
    expect(header).toHaveClass(
      "lg:grid-cols-[2.75rem_minmax(0,0.72fr)_minmax(0,0.74fr)_minmax(0,1.08fr)_minmax(0,1.18fr)_minmax(0,1.56fr)_minmax(0,0.78fr)_minmax(12rem,0.92fr)]",
    );

    expectedHeaders.forEach((label) => {
      expect(within(header).getByText(label)).toHaveClass(
        "justify-center",
        "text-center",
      );
    });
  });

  it("centers management value columns including professor name and title", async () => {
    renderPage();

    await waitFor(() => {
      expect(listProfessorsForManagement).toHaveBeenCalledWith("active");
    });

    const row = screen.getByText("李教授").closest("article");
    expect(row).not.toBeNull();
    const record = within(row as HTMLElement);

    expect(record.getByText("李教授")).toHaveClass("lg:text-center");
    expect(record.getByText("Associate / Professor")).toHaveClass("lg:text-center");
    expect(record.getByText("li@example.edu")).toHaveClass("lg:text-center");
    expect(record.getByText("测试大学 / 计算机学院")).toHaveClass("lg:text-center");
    expect(
      record.getAllByText("机器学习与人机协作").some((item) =>
        item.classList.contains("lg:text-center"),
      ),
    ).toBe(true);
    expect(record.getByText(formatApiDateTime(professor.updated_at))).toHaveClass("lg:text-center");
    expect(record.getByRole("button", { name: "编辑" }).closest("div")).toHaveClass(
      "lg:justify-center",
    );
  });

  it("renders management row actions as a balanced compact action group", async () => {
    renderPage();

    await waitFor(() => {
      expect(listProfessorsForManagement).toHaveBeenCalledWith("active");
    });

    const row = screen.getByText("李教授").closest("article");
    expect(row).not.toBeNull();
    const record = within(row as HTMLElement);
    const editButton = record.getByRole("button", { name: "编辑" });
    const archiveButton = record.getByRole("button", { name: "删除" });
    const actionGroup = editButton.closest("div");

    expect(actionGroup).toHaveClass("grid", "grid-cols-2", "lg:mx-auto");
    expect(editButton).toHaveClass("justify-center", "whitespace-nowrap");
    expect(archiveButton).toHaveClass("justify-center", "whitespace-nowrap");
    expect(record.queryByRole("button", { name: "移入回收站" })).not.toBeInTheDocument();
  });

  it("clears a professor personal note from the management row", async () => {
    renderPage();

    await waitFor(() => {
      expect(listProfessorsForManagement).toHaveBeenCalledWith("active");
    });

    fireEvent.click(
      screen.getByRole("button", { name: "编辑李教授的个人备注" }),
    );
    fireEvent.change(screen.getByLabelText("个人备注"), {
      target: { value: "" },
    });
    fireEvent.click(screen.getByRole("button", { name: "保存备注" }));

    await waitFor(() => {
      expect(updateProfessorNote).toHaveBeenCalledWith(professor.id, "");
    });
    await waitFor(() => {
      expect(
        screen.queryByRole("button", { name: "编辑李教授的个人备注" }),
      ).not.toBeInTheDocument();
    });
    const row = screen.getByText("李教授").closest("article");
    expect(row).not.toBeNull();
    expect(
      within(row as HTMLElement).getByText(
        formatApiDateTime("2026-04-24T00:00:00Z"),
      ),
    ).toBeInTheDocument();
  });

  it("saves personal notes from the full professor edit form", async () => {
    renderPage();

    await waitFor(() => {
      expect(listProfessorsForManagement).toHaveBeenCalledWith("active");
    });

    fireEvent.click(screen.getByRole("button", { name: "编辑" }));
    const dialog = screen.getByRole("dialog", { name: "编辑导师：李教授" });
    expect(within(dialog).getByTestId("professor-modal-scroll")).toHaveClass(
      "overflow-y-auto",
      "overscroll-contain",
    );
    expect(document.body.style.overflow).toBe("hidden");
    expect(document.documentElement.style.overflow).toBe("hidden");
    const noteInput = screen.getByLabelText("个人备注");

    expect(noteInput).toHaveValue("已有备注");
    expect(noteInput).toHaveAttribute("maxLength", "10000");

    fireEvent.change(noteInput, { target: { value: "更新后的备注" } });
    fireEvent.click(screen.getByRole("button", { name: "保存导师" }));

    await waitFor(() => {
      expect(updateProfessor).toHaveBeenCalledWith(
        professor.id,
        expect.objectContaining({
          personal_note: "更新后的备注",
        }),
      );
    });
    await waitFor(() => {
      expect(
        screen.queryByRole("dialog", { name: "编辑导师：李教授" }),
      ).not.toBeInTheDocument();
    });
    expect(document.body.style.overflow).toBe("");
    expect(document.documentElement.style.overflow).toBe("");
  });

  it("confirms before opening a fully prefilled single-mentor contribution form", async () => {
    const openExternalUrl = vi.fn().mockResolvedValue(undefined);
    const clipboardWrite = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", {
      configurable: true,
      value: { writeText: clipboardWrite },
    });
    window.autoEmailSender = {
      getVersion: async () => "0.1.0",
      openExternalUrl,
      saveCommunitySharePackage: vi.fn(),
      checkForUpdate: vi.fn(),
      downloadUpdate: vi.fn(),
      installUpdate: vi.fn(),
      getUpdateStatus: vi.fn(),
      onUpdateStatus: vi.fn(() => () => undefined),
    };
    listProfessorsForManagement.mockResolvedValue([
      { ...professor, source_url: "https://example.edu/li" },
    ]);
    renderPage();

    await waitFor(() => {
      expect(listProfessorsForManagement).toHaveBeenCalledWith("active");
    });
    fireEvent.click(screen.getByRole("button", { name: "编辑" }));
    const editDialog = screen.getByRole("dialog", { name: "编辑导师：李教授" });
    expect(within(editDialog).queryByText("导出社区共享包")).not.toBeInTheDocument();

    fireEvent.click(within(editDialog).getByRole("button", { name: /贡献到社区/ }));

    expect(openExternalUrl).not.toHaveBeenCalled();
    const confirmation = await screen.findByRole("dialog", {
      name: "贡献“李教授”到社区？",
    });
    expect(confirmation).toHaveTextContent("已预填现有信息；提交前请核对");
    fireEvent.click(
      within(confirmation).getByRole("button", { name: "打开已预填的投稿表" }),
    );

    await waitFor(() => expect(openExternalUrl).toHaveBeenCalledTimes(1));
    const url = new URL(openExternalUrl.mock.calls[0][0] as string);
    expect(url.searchParams.get("template")).toBe("contribute-mentor.yml");
    expect(url.searchParams.get("title")).toBe("[导师投稿] 测试大学李教授老师");
    expect(url.searchParams.get("name")).toBe("李教授");
    expect(url.searchParams.get("email")).toBe("li@example.edu");
    expect(url.searchParams.get("university")).toBe("测试大学");
    expect(url.searchParams.get("school")).toBe("计算机学院");
    expect(url.searchParams.get("department")).toBe("人工智能系");
    expect(url.searchParams.get("academic_title")).toBe("Associate Professor");
    expect(url.searchParams.get("recent_papers")).toBe("Paper A");
    expect(url.searchParams.get("source_url")).toBe("https://example.edu/li");
    expect(clipboardWrite).not.toHaveBeenCalled();
  });

  it("warns before opening when long optional text cannot fit in the GitHub URL", async () => {
    const openExternalUrl = vi.fn().mockResolvedValue(undefined);
    window.autoEmailSender = {
      getVersion: async () => "0.1.0",
      openExternalUrl,
      checkForUpdate: vi.fn(),
      downloadUpdate: vi.fn(),
      installUpdate: vi.fn(),
      getUpdateStatus: vi.fn(),
      onUpdateStatus: vi.fn(() => () => undefined),
    };
    listProfessorsForManagement.mockResolvedValue([
      {
        ...professor,
        research_direction: "研".repeat(1_000),
        source_url: "https://example.edu/faculty",
      },
    ]);
    renderPage();

    await waitFor(() => {
      expect(listProfessorsForManagement).toHaveBeenCalledWith("active");
    });
    fireEvent.click(screen.getByRole("button", { name: "编辑" }));
    fireEvent.click(screen.getByRole("button", { name: /贡献到社区/ }));

    const confirmation = await screen.findByRole("dialog", {
      name: "贡献“李教授”到社区？",
    });
    expect(confirmation).toHaveTextContent("研究方向因过长未带入");
    expect(confirmation).toHaveTextContent("完整投稿请使用批量“贡献到社区”");
    fireEvent.click(
      within(confirmation).getByRole("button", { name: "打开已预填的投稿表" }),
    );

    await waitFor(() => expect(openExternalUrl).toHaveBeenCalledTimes(1));
    const url = new URL(openExternalUrl.mock.calls[0][0] as string);
    expect(url.searchParams.get("research_direction")).toBeNull();
    expect(url.searchParams.get("recent_papers")).toBe("Paper A");
    expect(url.searchParams.get("academic_title")).toBe("Associate Professor");
  });

  it("starts single information enrichment from the edit dialog and disables the button", async () => {
    renderPage();

    await waitFor(() => {
      expect(listProfessorsForManagement).toHaveBeenCalledWith("active");
    });

    fireEvent.click(screen.getByRole("button", { name: "编辑" }));
    fireEvent.change(screen.getByLabelText("导师主页"), {
      target: { value: "https://example.edu/not-saved-yet" },
    });
    fireEvent.click(screen.getByRole("button", { name: "智能补全" }));

    await waitFor(() => {
      expect(createSingleProfessorInformationEnrichment).toHaveBeenCalledWith(
        professor.id,
        7,
      );
    });
    expect(screen.getByRole("button", { name: "智能补全" })).toBeDisabled();
    expect(screen.queryByText("正在智能补全")).not.toBeInTheDocument();
  });

  it("shows the original single-enrichment error after the job finishes", async () => {
    const rawError = "browser fallback failed: net::ERR_CONNECTION_RESET";
    getProfessorInformationEnrichmentJob.mockResolvedValue({
      ...informationEnrichmentJob,
      status: "failed",
      running_count: 0,
      completed_count: 1,
      failed_count: 1,
      finished_at: "2026-04-24T00:00:20Z",
      last_error: rawError,
    });
    listProfessorInformationEnrichmentItems.mockResolvedValue([
      buildInformationEnrichmentItem({
        status: "failed",
        enriched_fields: [],
        error_message: rawError,
        input_tokens: 0,
        output_tokens: 0,
        total_tokens: 0,
        attempt_count: 3,
      }),
    ]);

    renderPage();
    await waitFor(() => {
      expect(listProfessorsForManagement).toHaveBeenCalledWith("active");
    });

    fireEvent.click(screen.getByRole("button", { name: "编辑" }));
    fireEvent.click(screen.getByRole("button", { name: "智能补全" }));

    expect(await screen.findByText("补全失败：李教授")).toBeInTheDocument();
    expect(screen.getByText(rawError)).toBeInTheDocument();
    await waitFor(() => {
      expect(screen.getByRole("button", { name: "智能补全" })).toBeEnabled();
    });
  });

  it("reports a skipped single enrichment with its reason", async () => {
    getProfessorInformationEnrichmentJob.mockResolvedValue({
      ...informationEnrichmentJob,
      status: "completed",
      running_count: 0,
      completed_count: 1,
      skipped_count: 1,
      finished_at: "2026-04-24T00:00:20Z",
    });
    listProfessorInformationEnrichmentItems.mockResolvedValue([
      buildInformationEnrichmentItem({
        status: "skipped",
        enriched_fields: [],
        skip_reason: "导师已在回收站",
        total_tokens: 0,
      }),
    ]);

    renderPage();
    await waitFor(() => {
      expect(listProfessorsForManagement).toHaveBeenCalledWith("active");
    });

    fireEvent.click(screen.getByRole("button", { name: "编辑" }));
    fireEvent.click(screen.getByRole("button", { name: "智能补全" }));

    expect(await screen.findByText("补全已跳过：李教授")).toBeInTheDocument();
    expect(screen.getByText("导师已在回收站")).toBeInTheDocument();
  });

  it("reports the result after a batch information enrichment job finishes", async () => {
    getProfessorInformationEnrichmentJob.mockResolvedValue({
      ...informationEnrichmentJob,
      id: 72,
      name: "信息补全 2026-04-24",
      trigger_mode: "batch",
      status: "completed",
      completed_count: 1,
      queued_count: 0,
      running_count: 0,
      succeeded_count: 1,
      finished_at: "2026-04-24T00:00:20Z",
    });
    listProfessorInformationEnrichmentItems.mockResolvedValue([
      buildInformationEnrichmentItem({
        job_id: 72,
        status: "succeeded",
        enriched_fields: ["department"],
      }),
    ]);
    renderPage();

    await waitFor(() => {
      expect(listProfessorsForManagement).toHaveBeenCalledWith("active");
    });

    fireEvent.click(screen.getByRole("button", { name: "选择 李教授" }));
    fireEvent.click(screen.getByRole("button", { name: "批量智能补全" }));

    expect(
      await screen.findByText("补全选中的 1 位导师信息？"),
    ).toBeInTheDocument();
    expect(
      screen.getByText(
        "将访问导师主页补全空缺信息，不覆盖现有内容，并消耗 Token。",
      ),
    ).toBeInTheDocument();
    expect(createProfessorInformationEnrichmentJob).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("button", { name: "开始补全" }));

    await waitFor(() => {
      expect(createProfessorInformationEnrichmentJob).toHaveBeenCalledWith({
        professorIds: [professor.id],
        llmProfileId: 7,
      });
    });
    expect(await screen.findByText("批量信息补全已创建")).toBeInTheDocument();
    expect(
      screen.getByText("已排队 1 位，跳过 0 位，可在任务中心查看。"),
    ).toBeInTheDocument();
    expect(await screen.findByText("批量信息补全完成")).toBeInTheDocument();
    expect(
      screen.getByText("成功 1 位，失败 0 位，跳过 0 位，取消 0 位，共补全 1 项信息。"),
    ).toBeInTheDocument();
  });

  it("opens editable homepage and source links with the desktop default browser", async () => {
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
    listProfessorsForManagement.mockResolvedValue([
      {
        ...professor,
        source_url: "https://example.edu/faculty-directory",
      },
    ]);
    renderPage();

    await waitFor(() => {
      expect(listProfessorsForManagement).toHaveBeenCalledWith("active");
    });
    fireEvent.click(screen.getByRole("button", { name: "编辑" }));

    const profileInput = screen.getByLabelText("导师主页");
    fireEvent.change(profileInput, {
      target: { value: " https://example.edu/li-updated " },
    });
    fireEvent.click(screen.getByRole("button", { name: "打开导师主页" }));
    fireEvent.click(screen.getByRole("button", { name: "打开发现来源页" }));

    expect(openExternalUrl).toHaveBeenCalledWith("https://example.edu/li-updated");
    expect(openExternalUrl).toHaveBeenCalledWith(
      "https://example.edu/faculty-directory",
    );
  });

  it("falls back to an Electron window when opening an editable professor link fails", async () => {
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
    renderPage();

    await waitFor(() => {
      expect(listProfessorsForManagement).toHaveBeenCalledWith("active");
    });
    fireEvent.click(screen.getByRole("button", { name: "编辑" }));
    fireEvent.click(screen.getByRole("button", { name: "打开导师主页" }));

    await waitFor(() => {
      expect(openWindow).toHaveBeenCalledWith(
        "https://example.edu/li",
        "_blank",
        "noopener,noreferrer",
      );
    });
    openWindow.mockRestore();
  });

  it("guides empty professor lists with three intake cards", async () => {
    listProfessorsForManagement.mockResolvedValue([]);
    renderPage();

    await waitFor(() => {
      expect(listProfessorsForManagement).toHaveBeenCalledWith("active");
    });

    expect(screen.getByRole("heading", { name: "暂无导师" })).toBeInTheDocument();
    expect(
      screen.getByText("选择一种方式建立导师库。"),
    ).toBeInTheDocument();

    const emptyState = screen.getByTestId("professor-empty-intake");
    expect(emptyState).toHaveClass("grid", "lg:grid-cols-3");
    [
      ["手动添加", null, "添加导师"],
      ["表格导入", "从 CSV 或 XLSX 导入。", "选择文件"],
      ["智能抓取", "从学院页面抓取并审核。", "开始抓取"],
    ].forEach(([title, description, buttonName]) => {
      const card = within(emptyState).getByTestId(`professor-empty-intake-${title}`);
      expect(within(card).getByRole("heading", { name: title })).toBeInTheDocument();
      if (description) {
        expect(within(card).getByText(description)).toBeInTheDocument();
      }
      expect(within(card).getByRole("button", { name: buttonName })).toBeInTheDocument();
    });
  });

  it("keeps the intake panel visible after switching to deleted professors", async () => {
    listProfessorsForManagement.mockImplementation((filter: string) =>
      Promise.resolve(filter === "archived" ? [] : [professor]),
    );
    renderPage();

    await waitFor(() => {
      expect(listProfessorsForManagement).toHaveBeenCalledWith("active");
    });

    const intakePanel = screen.getByTestId("professor-intake-panel");
    fireEvent.click(screen.getByRole("button", { name: "回收站" }));

    await waitFor(() => {
      expect(listProfessorsForManagement).toHaveBeenCalledWith("archived");
    });

    expect(screen.getByTestId("professor-intake-panel")).toBe(intakePanel);
    expect(within(intakePanel).getByText("导师导入与导出方式")).toBeInTheDocument();
    expect(within(intakePanel).getByRole("button", { name: "智能抓取" })).toBeInTheDocument();
    expect(within(intakePanel).getByRole("button", { name: "选择文件" })).toBeInTheDocument();
    expect(within(intakePanel).getByRole("button", { name: "添加导师" })).toBeInTheDocument();
    expect(within(intakePanel).getByRole("button", { name: "导出导师信息" })).toBeInTheDocument();
  });

  it("keeps the previous list visible while an archive filter is refreshing", async () => {
    let resolveArchived: (value: ProfessorManagementItemDTO[]) => void = () => {};
    listProfessorsForManagement.mockImplementation((filter: string) => {
      if (filter === "archived") {
        return new Promise<ProfessorManagementItemDTO[]>((resolve) => {
          resolveArchived = resolve;
        });
      }
      return Promise.resolve([professor]);
    });
    renderPage();

    expect(await screen.findByText("李教授")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "回收站" }));

    await waitFor(() => {
      expect(listProfessorsForManagement).toHaveBeenLastCalledWith("archived");
    });

    expect(screen.getByText("李教授")).toBeInTheDocument();
    expect(screen.getByTestId("professor-list-refreshing")).toHaveTextContent(
      "正在更新导师列表…",
    );
    expect(screen.queryByText("正在加载导师列表...")).not.toBeInTheDocument();

    resolveArchived([]);

    await waitFor(() => {
      expect(screen.queryByTestId("professor-list-refreshing")).not.toBeInTheDocument();
      expect(screen.getByRole("heading", { name: "暂无导师" })).toBeInTheDocument();
    });
  });

  it("keeps the intake panel mounted while returning from an empty deleted view", async () => {
    let activeRequestCount = 0;
    let resolveActiveRefresh: (value: ProfessorManagementItemDTO[]) => void =
      () => {};
    listProfessorsForManagement.mockImplementation((filter: string) => {
      if (filter === "archived") {
        return Promise.resolve([]);
      }
      activeRequestCount += 1;
      if (activeRequestCount === 1) {
        return Promise.resolve([professor]);
      }
      return new Promise<ProfessorManagementItemDTO[]>((resolve) => {
        resolveActiveRefresh = resolve;
      });
    });
    renderPage();

    expect(await screen.findByText("李教授")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "回收站" }));
    expect(
      await screen.findByRole("heading", { name: "暂无导师" }),
    ).toBeInTheDocument();

    const intakePanel = screen.getByTestId("professor-intake-panel");
    fireEvent.click(screen.getByRole("button", { name: "正常" }));

    await waitFor(() => {
      expect(listProfessorsForManagement).toHaveBeenLastCalledWith("active");
    });

    expect(screen.getByTestId("professor-intake-panel")).toBe(intakePanel);
    expect(screen.getByTestId("professor-list-refreshing")).toBeInTheDocument();

    resolveActiveRefresh([professor]);

    await waitFor(() => {
      expect(screen.queryByTestId("professor-list-refreshing")).not.toBeInTheDocument();
      expect(screen.getByText("李教授")).toBeInTheDocument();
    });
  });

  it("filters professors by title and school from the advanced filter panel", async () => {
    listProfessorsForManagement.mockResolvedValue([professor, anotherProfessor]);
    renderPage();

    await waitFor(() => {
      expect(listProfessorsForManagement).toHaveBeenCalledWith("active");
    });

    expect(screen.queryByRole("listbox", { name: "职称 / 导师资格" })).not.toBeInTheDocument();
    expect(screen.queryByRole("listbox", { name: "学校" })).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "高级筛选" }));
    fireEvent.click(screen.getByRole("button", { name: "职称 / 导师资格：全部职称 / 导师资格" }));
    fireEvent.click(
      screen.getByRole("button", { name: "取消全选" }),
    );
    fireEvent.click(screen.getByRole("option", { name: "Professor" }));
    fireEvent.click(screen.getByRole("button", { name: "应用" }));
    fireEvent.click(screen.getByRole("button", { name: "学校：全部学校" }));
    fireEvent.click(
      screen.getByRole("button", { name: "取消全选" }),
    );
    fireEvent.click(screen.getByRole("option", { name: "样例大学" }));
    fireEvent.click(screen.getByRole("button", { name: "应用" }));
    fireEvent.click(screen.getByRole("button", { name: "学院：全部学院" }));
    fireEvent.click(
      screen.getByRole("button", { name: "取消全选" }),
    );
    fireEvent.click(screen.getByRole("option", { name: "生命科学学院" }));
    fireEvent.click(screen.getByRole("button", { name: "应用" }));

    expect(screen.queryByText("李教授")).not.toBeInTheDocument();
    expect(screen.getByText("王教授")).toBeInTheDocument();
    expect(
      screen.getByText("1 位 · 1/1 页 · 每页 10 位"),
    ).toBeInTheDocument();

    const resetButton = screen.getByRole("button", { name: "重置" });
    expect(resetButton).toHaveClass("ui-btn-secondary");
    const intakePanel = screen.getByTestId("professor-intake-panel");
    expect(intakePanel).toHaveClass("grid", "gap-3");
    expect(intakePanel).not.toHaveClass("rounded-[30px]", "border", "shadow-sm");
    expect(within(intakePanel).getByText("导师导入与导出方式")).toBeInTheDocument();
    expect(within(intakePanel).getByRole("heading", { name: "智能抓取" })).toBeInTheDocument();
    expect(within(intakePanel).getByRole("heading", { name: "表格导入" })).toBeInTheDocument();
    expect(within(intakePanel).getByRole("heading", { name: "手动添加" })).toBeInTheDocument();
    expect(within(intakePanel).queryByText("按数据来源选择入口，系统会统一沉淀到导师档案库。")).not.toBeInTheDocument();
    [
      "从学院页面自动发现导师，抓取结果进入候选审核。",
      "下载模板后批量导入导师信息，适合已有名单或表格。",
      "手动创建一条导师档案，适合临时补充或精修记录。",
    ].forEach((description) => {
      expect(within(intakePanel).queryByText(description)).not.toBeInTheDocument();
    });
    ["智能抓取", "表格导入", "手动添加", "导出导师信息"].forEach((label) => {
      expect(within(intakePanel).getByTestId(`professor-intake-${label}`)).toHaveClass(
        "rounded-[24px]",
        "border",
        "min-h-[7.5rem]",
      );
    });
    ["选择文件", "智能抓取", "添加导师"].forEach((name) => {
      expect(within(intakePanel).getByRole("button", { name })).toBeInTheDocument();
    });
    expect(within(intakePanel).queryByText("导出全部正常导师，字段与导入模板一致。")).not.toBeInTheDocument();
    expect(within(intakePanel).getByTestId("professor-intake-导出导师信息")).toHaveClass("border-emerald-200");
    expect(within(intakePanel).getByRole("button", { name: "导出导师信息" })).toHaveClass("bg-emerald-600");
    expect(within(intakePanel).queryByRole("button", { name: "下载模板" })).not.toBeInTheDocument();
    expect(within(intakePanel).queryByRole("button", { name: "导入文件" })).not.toBeInTheDocument();
    expectToAppearBefore(intakePanel, screen.getByRole("button", { name: "正常" }));
    expectToAppearBefore(screen.getByRole("heading", { name: "导师管理" }), intakePanel);
    expect(screen.queryByText("样例导入与智能抓取")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "导入样例导师" })).not.toBeInTheDocument();

    fireEvent.click(resetButton);

    expect(screen.getByText("李教授")).toBeInTheDocument();
    expect(screen.getByText("王教授")).toBeInTheDocument();
  });
  it("changes and stores the independent management page size", async () => {
    listProfessorsForManagement.mockResolvedValue(
      Array.from({ length: 12 }, (_, index) => buildProfessor(index + 1)),
    );
    renderPage();

    await waitFor(() => {
      expect(listProfessorsForManagement).toHaveBeenCalledWith("active");
    });

    expect(screen.getByText("导师 10")).toBeInTheDocument();
    expect(screen.queryByText("导师 11")).not.toBeInTheDocument();
    expect(
      screen.getByText("12 位 · 1/2 页 · 每页 10 位"),
    ).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "每页数量" }));
    fireEvent.click(screen.getByRole("option", { name: "20" }));

    expect(screen.getByText("导师 11")).toBeInTheDocument();
    expect(screen.getByText("导师 12")).toBeInTheDocument();
    expect(
      screen.getByText("12 位 · 1/1 页 · 每页 20 位"),
    ).toBeInTheDocument();
    expect(localStorage.getItem("professors-management:page-size")).toBe("20");
    expect(localStorage.getItem("home-dashboard:page-size")).toBeNull();
  });

  it("keeps keyword search, sort, and advanced filters in the toolbar", async () => {
    renderPage();

    await waitFor(() => {
      expect(listProfessorsForManagement).toHaveBeenCalledWith("active");
    });

    const searchInput = screen.getByPlaceholderText("姓名、邮箱、学校、学院、系所、职称、研究方向、标签");
    const toolbar = screen.getByTestId("professor-filter-toolbar");

    expect(toolbar).toHaveClass("grid", "gap-3", "lg:items-stretch");
    expect(toolbar.contains(searchInput)).toBe(true);
    expect(within(toolbar).getByRole("button", { name: "排序" })).toBeInTheDocument();
    expect(within(toolbar).getByRole("button", { name: "高级筛选" })).toBeInTheDocument();
    expect(within(toolbar).getByRole("button", { name: "重置" })).toBeInTheDocument();
  });

  it("shows advanced filter fields with consistent labels", async () => {
    renderPage();

    await waitFor(() => {
      expect(listProfessorsForManagement).toHaveBeenCalledWith("active");
    });

    fireEvent.click(screen.getByRole("button", { name: "高级筛选" }));

    ["学校", "学院", "系所", "职称 / 导师资格"].forEach((label) => {
      expect(screen.getByText(label)).toHaveClass("text-sm", "font-medium", "text-stone-800");
    });
    expect(screen.getByRole("button", { name: "清空高级筛选" })).toHaveClass("ui-btn-secondary");
  });
  it("downloads professor templates through an authenticated blob request", async () => {
    renderPage();

    await waitFor(() => {
      expect(listProfessorsForManagement).toHaveBeenCalledWith("active");
    });

    fireEvent.click(within(screen.getByTestId("professor-intake-表格导入")).getByRole("button", { name: "选择文件" }));

    fireEvent.click(screen.getByRole("button", { name: "下载 XLSX 模板" }));

    await waitFor(() => expect(downloadProfessorTemplate).toHaveBeenCalledWith("xlsx"));
  });

  it("opens the mentor crawler Skill guide from the import dialog", async () => {
    const openWindow = vi.spyOn(window, "open").mockImplementation(() => null);
    renderPage();

    await waitFor(() => {
      expect(listProfessorsForManagement).toHaveBeenCalledWith("active");
    });

    fireEvent.click(within(screen.getByTestId("professor-intake-表格导入")).getByRole("button", { name: "选择文件" }));
    fireEvent.click(
      screen.getByRole("button", {
        name: "用 Codex / Claude Code 从导师官网生成导入表",
      }),
    );

    expect(openWindow).toHaveBeenCalledWith(
      "https://juniexd.github.io/AutoEmailSender/docs/mentor-crawler-skill",
      "_blank",
      "noopener,noreferrer",
    );
    expect(
      screen.getByText(/省略标签或个人备注列时，已有内容不会被清空/),
    ).toBeInTheDocument();
  });

  it("downloads professor exports through an authenticated blob request", async () => {
    renderPage();

    await waitFor(() => {
      expect(listProfessorsForManagement).toHaveBeenCalledWith("active");
    });

    fireEvent.click(screen.getByRole("button", { name: "导出导师信息" }));

    expect(screen.getByRole("dialog", { name: "导出导师信息" })).toBeInTheDocument();
    expect(screen.getByText("包含全部正常导师，不包含回收站导师。")).toBeInTheDocument();
    expect(screen.getByText("导出文件包含个人备注，请谨慎分享。")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "导出 XLSX" }));

    await waitFor(() => expect(downloadProfessorExport).toHaveBeenCalledWith("xlsx"));
  });
});
