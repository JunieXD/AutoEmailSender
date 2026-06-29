import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { NotificationProvider } from "@/context/NotificationContext";
import { formatApiDateTime } from "@/lib/dateTime";
import { ProfessorsPage } from "@/pages/ProfessorsPage";
import type { ProfessorManagementItemDTO } from "@/types";

const mockedUseSelectionContext = vi.hoisted(() => vi.fn());
const listProfessorsForManagement = vi.hoisted(() => vi.fn());
const getProfessorExportDownloadUrl = vi.hoisted(() => vi.fn());
const updateProfessor = vi.hoisted(() => vi.fn());
const updateProfessorNote = vi.hoisted(() => vi.fn());

vi.mock("@/context/SelectionContext", () => ({
  useSelectionContext: mockedUseSelectionContext,
}));

vi.mock("@/lib/api/professorsApi", () => ({
  listProfessorsForManagement,
  archiveProfessor: vi.fn(),
  bulkArchiveProfessors: vi.fn(),
  createProfessor: vi.fn(),
  getProfessorExportDownloadUrl,
  getProfessorTemplateDownloadUrl: vi.fn(() => "/templates/professors.xlsx"),
  importProfessorsFromFile: vi.fn(),
  restoreProfessor: vi.fn(),
  triggerCrawler: vi.fn(),
  updateProfessor,
  updateProfessorNote,
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

const renderPage = () =>
  render(
    <MemoryRouter>
      <NotificationProvider>
        <ProfessorsPage />
      </NotificationProvider>
    </MemoryRouter>,
  );

const expectToAppearBefore = (first: HTMLElement, second: HTMLElement) => {
  expect(first.compareDocumentPosition(second)).toBe(
    Node.DOCUMENT_POSITION_FOLLOWING,
  );
};

describe("ProfessorsPage layout", () => {
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
    getProfessorExportDownloadUrl.mockReset();
    getProfessorExportDownloadUrl.mockImplementation(
      (format: "xlsx" | "csv") => `/exports/professors.${format}`,
    );
    updateProfessor.mockReset();
    updateProfessor.mockResolvedValue(professor);
    updateProfessorNote.mockReset();
    updateProfessorNote.mockResolvedValue({
      id: professor.id,
      personal_note: null,
      updated_at: "2026-04-24T00:00:00Z",
    });
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

    const header = screen.getByTestId("professor-table-header");
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

    const profileInput = screen.getByLabelText("主页链接");
    fireEvent.change(profileInput, {
      target: { value: " https://example.edu/li-updated " },
    });
    fireEvent.click(screen.getByRole("button", { name: "打开主页链接" }));
    fireEvent.click(screen.getByRole("button", { name: "打开来源链接" }));

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
    fireEvent.click(screen.getByRole("button", { name: "打开主页链接" }));

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
      screen.getByText("选择一种方式建立导师库，后续可继续筛选、编辑和归档。"),
    ).toBeInTheDocument();

    const emptyState = screen.getByTestId("professor-empty-intake");
    expect(emptyState).toHaveClass("grid", "lg:grid-cols-3");
    [
      ["单个新增", "手动创建一条导师档案，适合临时补充或精修记录。", "新增导师"],
      ["模板导入", "下载模板后批量导入导师信息，适合已有名单或表格。", "模板导入"],
      ["智能抓取", "从学院页面自动发现导师，抓取结果进入候选审核。", "智能抓取"],
    ].forEach(([title, description, buttonName]) => {
      const card = within(emptyState).getByTestId(`professor-empty-intake-${title}`);
      expect(within(card).getByRole("heading", { name: title })).toBeInTheDocument();
      expect(within(card).getByText(description)).toBeInTheDocument();
      expect(within(card).getByRole("button", { name: buttonName })).toBeInTheDocument();
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
    fireEvent.click(screen.getByRole("option", { name: "Professor" }));
    fireEvent.click(screen.getByRole("button", { name: "学校：全部学校" }));
    fireEvent.click(screen.getByRole("option", { name: "样例大学" }));
    fireEvent.click(screen.getByRole("button", { name: "学院：全部学院" }));
    fireEvent.click(screen.getByRole("option", { name: "生命科学学院" }));

    expect(screen.queryByText("李教授")).not.toBeInTheDocument();
    expect(screen.getByText("王教授")).toBeInTheDocument();
    expect(
      screen.getByText("共 1 位符合筛选条件，当前第 1 / 1 页，每页最多 10 位"),
    ).toBeInTheDocument();

    const resetButton = screen.getByRole("button", { name: "重置" });
    expect(resetButton).toHaveClass("ui-btn-secondary");
    const intakePanel = screen.getByTestId("professor-intake-panel");
    expect(intakePanel).toHaveClass("grid", "gap-3");
    expect(intakePanel).not.toHaveClass("rounded-[30px]", "border", "shadow-sm");
    expect(within(intakePanel).getByText("导师导入与导出方式")).toBeInTheDocument();
    expect(within(intakePanel).getByRole("heading", { name: "智能抓取" })).toBeInTheDocument();
    expect(within(intakePanel).getByRole("heading", { name: "模板批量新增" })).toBeInTheDocument();
    expect(within(intakePanel).getByRole("heading", { name: "单个新增" })).toBeInTheDocument();
    expect(within(intakePanel).queryByText("按数据来源选择入口，系统会统一沉淀到导师档案库。")).not.toBeInTheDocument();
    [
      "从学院页面自动发现导师，抓取结果进入候选审核。",
      "下载模板后批量导入导师信息，适合已有名单或表格。",
      "手动创建一条导师档案，适合临时补充或精修记录。",
    ].forEach((description) => {
      expect(within(intakePanel).queryByText(description)).not.toBeInTheDocument();
    });
    ["智能抓取", "模板批量新增", "单个新增", "导出导师信息"].forEach((label) => {
      expect(within(intakePanel).getByTestId(`professor-intake-${label}`)).toHaveClass(
        "rounded-[24px]",
        "border",
        "min-h-[7.5rem]",
      );
    });
    ["模板导入", "智能抓取", "新增导师"].forEach((name) => {
      expect(within(intakePanel).getByRole("button", { name })).toBeInTheDocument();
    });
    expect(within(intakePanel).queryByText("导出全部正常导师，字段与导入模板一致。")).not.toBeInTheDocument();
    expect(within(intakePanel).getByTestId("professor-intake-导出导师信息")).toHaveClass("border-emerald-200");
    expect(within(intakePanel).getByRole("button", { name: "导出导师信息" })).toHaveClass("bg-emerald-600");
    expect(within(intakePanel).queryByRole("button", { name: "下载模板" })).not.toBeInTheDocument();
    expect(within(intakePanel).queryByRole("button", { name: "导入文件" })).not.toBeInTheDocument();
    expectToAppearBefore(intakePanel, screen.getByRole("button", { name: "正常" }));
    expectToAppearBefore(screen.getByRole("heading", { name: "导师档案管理" }), intakePanel);
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
      screen.getByText("共 12 位符合筛选条件，当前第 1 / 2 页，每页最多 10 位"),
    ).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "每页数量" }));
    fireEvent.click(screen.getByRole("option", { name: "20" }));

    expect(screen.getByText("导师 11")).toBeInTheDocument();
    expect(screen.getByText("导师 12")).toBeInTheDocument();
    expect(
      screen.getByText("共 12 位符合筛选条件，当前第 1 / 1 页，每页最多 20 位"),
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
  it("downloads professor templates without opening a blank window", async () => {
    renderPage();

    await waitFor(() => {
      expect(listProfessorsForManagement).toHaveBeenCalledWith("active");
    });

    fireEvent.click(within(screen.getByTestId("professor-intake-模板批量新增")).getByRole("button", { name: "模板导入" }));

    const link = document.createElement("a");
    const click = vi.spyOn(link, "click").mockImplementation(() => undefined);
    const createElement = vi.spyOn(document, "createElement").mockReturnValue(link);

    fireEvent.click(screen.getByRole("button", { name: "下载 XLSX 模板" }));

    expect(createElement).toHaveBeenCalledWith("a");
    expect(link).toHaveAttribute("href", "/templates/professors.xlsx");
    expect(link).not.toHaveAttribute("target");
    expect(link).not.toHaveAttribute("rel");
    expect(click).toHaveBeenCalledTimes(1);
    createElement.mockRestore();
    click.mockRestore();
  });

  it("opens professor export dialog and downloads without opening a blank window", async () => {
    renderPage();

    await waitFor(() => {
      expect(listProfessorsForManagement).toHaveBeenCalledWith("active");
    });

    fireEvent.click(screen.getByRole("button", { name: "导出导师信息" }));

    expect(screen.getByRole("dialog", { name: "导出导师信息" })).toBeInTheDocument();
    expect(screen.getByText("导出范围：全部正常导师，不包含回收站导师。")).toBeInTheDocument();
    expect(
      screen.getByText("当前搜索、筛选、分页和勾选状态不会影响导出结果。"),
    ).toBeInTheDocument();
    expect(screen.getByText("字段顺序与导入模板一致，未修改即可重新导入系统。")).toBeInTheDocument();
    expect(screen.getByText("导出文件包含个人备注，请谨慎分享。")).toBeInTheDocument();

    const link = document.createElement("a");
    const click = vi.spyOn(link, "click").mockImplementation(() => undefined);
    const createElement = vi.spyOn(document, "createElement").mockReturnValue(link);

    fireEvent.click(screen.getByRole("button", { name: "导出 XLSX" }));

    expect(getProfessorExportDownloadUrl).toHaveBeenCalledWith("xlsx");
    expect(createElement).toHaveBeenCalledWith("a");
    expect(link).toHaveAttribute("href", "/exports/professors.xlsx");
    expect(link).not.toHaveAttribute("target");
    expect(link).not.toHaveAttribute("rel");
    expect(click).toHaveBeenCalledTimes(1);
    createElement.mockRestore();
    click.mockRestore();
  });
});
