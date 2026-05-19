import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { NotificationProvider } from "@/context/NotificationContext";
import { formatApiDateTime } from "@/lib/dateTime";
import { ProfessorsPage } from "@/pages/ProfessorsPage";
import type { ProfessorManagementItemDTO } from "@/types";

const mockedUseSelectionContext = vi.hoisted(() => vi.fn());
const listProfessorsForManagement = vi.hoisted(() => vi.fn());

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

const professor: ProfessorManagementItemDTO = {
  id: 1,
  name: "李教授",
  email: "li@example.edu",
  title: "Associate Professor",
  university: "测试大学",
  school: "计算机学院",
  department: "人工智能系",
  research_direction: "机器学习与人机协作",
  recent_papers: ["Paper A"],
  profile_url: "https://example.edu/li",
  source_url: null,
  crawl_status: "manual",
  skip_reason: null,
  archived_at: null,
  created_at: "2026-04-22T00:00:00Z",
  updated_at: "2026-04-23T00:00:00Z",
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
  recent_papers: ["Paper B"],
  profile_url: "https://example.edu/wang",
  source_url: null,
  crawl_status: "manual",
  skip_reason: null,
  archived_at: null,
  created_at: "2026-04-22T00:00:00Z",
  updated_at: "2026-04-24T00:00:00Z",
};

const renderPage = () =>
  render(
    <NotificationProvider>
      <ProfessorsPage />
    </NotificationProvider>,
  );

const expectToAppearBefore = (first: HTMLElement, second: HTMLElement) => {
  expect(first.compareDocumentPosition(second)).toBe(
    Node.DOCUMENT_POSITION_FOLLOWING,
  );
};

describe("ProfessorsPage layout", () => {
  beforeEach(() => {
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

  it("filters professors by title and school pair from the management toolbar", async () => {
    listProfessorsForManagement.mockResolvedValue([professor, anotherProfessor]);
    renderPage();

    await waitFor(() => {
      expect(listProfessorsForManagement).toHaveBeenCalledWith("active");
    });

    expect(screen.queryByRole("combobox", { name: "职称筛选" })).not.toBeInTheDocument();
    expect(screen.queryByRole("combobox", { name: "学校学院筛选" })).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "职称筛选" }));
    fireEvent.click(screen.getByRole("option", { name: "Professor" }));
    fireEvent.click(screen.getByRole("button", { name: "学校学院筛选" }));
    fireEvent.click(screen.getByRole("option", { name: "样例大学 / 生命科学学院" }));

    expect(screen.queryByText("李教授")).not.toBeInTheDocument();
    expect(screen.getByText("王教授")).toBeInTheDocument();
    expect(
      screen.getByText("共 1 位符合筛选条件，当前第 1 / 1 页，每页最多 10 位"),
    ).toBeInTheDocument();

    const resetButton = screen.getByRole("button", { name: "重置筛选" });
    expect(resetButton).toHaveClass("ui-select-shell", "rounded-3xl");
    const intakePanel = screen.getByTestId("professor-intake-panel");
    expect(within(intakePanel).getByText("导师录入方式")).toBeInTheDocument();
    expect(
      within(intakePanel).getByRole("heading", { name: "智能抓取" }),
    ).toBeInTheDocument();
    expect(
      within(intakePanel).getByRole("heading", { name: "模板批量新增" }),
    ).toBeInTheDocument();
    expect(
      within(intakePanel).getByRole("heading", { name: "单个新增" }),
    ).toBeInTheDocument();
    expect(
      within(intakePanel).queryByText("按数据来源选择入口，系统会统一沉淀到导师档案库。"),
    ).not.toBeInTheDocument();
    [
      "从学院页面自动发现导师，抓取结果进入候选审核。",
      "下载模板后批量导入导师信息，适合已有名单或表格。",
      "手动创建一条导师档案，适合临时补充或精修记录。",
    ].forEach((description) => {
      expect(within(intakePanel).queryByText(description)).not.toBeInTheDocument();
    });
    ["智能抓取", "模板批量新增", "单个新增"].forEach((label) => {
      expect(within(intakePanel).getByTestId(`professor-intake-${label}`)).toHaveClass(
        "rounded-[28px]",
        "border",
        "min-h-0",
      );
    });
    ["模板导入", "智能抓取", "新增导师"].forEach((name) => {
      expect(within(intakePanel).getByRole("button", { name })).toBeInTheDocument();
    });
    expect(within(intakePanel).queryByRole("button", { name: "下载模板" })).not.toBeInTheDocument();
    expect(within(intakePanel).queryByRole("button", { name: "导入文件" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "刷新" })).toHaveClass("h-10", "rounded-2xl");
    expectToAppearBefore(
      intakePanel,
      screen.getByRole("button", { name: "正常" }),
    );
    expectToAppearBefore(
      screen.getByRole("heading", { name: "导师档案管理" }),
      intakePanel,
    );
    expect(screen.queryByText("样例导入与智能抓取")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "导入样例导师" })).not.toBeInTheDocument();
    expect(screen.getByTestId("professor-toolbar-spacer")).toHaveClass("h-5", "leading-5");
    expect(screen.getAllByText("操作").length).toBeGreaterThan(1);

    fireEvent.click(resetButton);

    expect(screen.getByText("李教授")).toBeInTheDocument();
    expect(screen.getByText("王教授")).toBeInTheDocument();
  });

  it("keeps search and filter controls in separate toolbar rows", async () => {
    renderPage();

    await waitFor(() => {
      expect(listProfessorsForManagement).toHaveBeenCalledWith("active");
    });

    const searchInput = screen.getByPlaceholderText("搜索姓名、邮箱、学校、院系或研究方向");
    const searchRow = searchInput.closest('[data-testid="professor-search-row"]');
    const filterRow = screen.getByTestId("professor-filter-row");

    expect(searchRow).not.toBeNull();
    expect(searchRow).toHaveClass("min-w-0");
    expect(filterRow).toHaveClass("lg:justify-between");
    expect(filterRow.contains(searchInput)).toBe(false);
  });

  it("aligns filter labels with one shared field rhythm", async () => {
    renderPage();

    await waitFor(() => {
      expect(listProfessorsForManagement).toHaveBeenCalledWith("active");
    });

    const titleLabel = screen.getByTestId("professor-title-filter-label");
    const schoolLabel = screen.getByTestId("professor-school-filter-label");
    const resetLabel = screen.getByTestId("professor-reset-filter-label");

    [titleLabel, schoolLabel, resetLabel].forEach((label) => {
      expect(label).toHaveClass("h-5", "leading-5", "text-sm", "font-medium");
    });
  });
});
