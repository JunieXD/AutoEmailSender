import {
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { MemoryRouter, Route, Routes, useNavigate } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import {
  bulkUpdateProfessorTags,
  createProfessorTag,
  deleteProfessorTag,
  getProfessorTagUsage,
  listProfessors,
  listProfessorsForManagement,
  updateProfessorTags,
} from "@/lib/api/professorsApi";
import type {
  IdentityDTO,
  LLMProfileDTO,
  ProfessorDashboardItemDTO,
  ProfessorManagementItemDTO,
} from "@/types";
import { HomePage } from "./HomePage";
import { ProfessorsPage } from "./ProfessorsPage";

const notifyMock = {
  notifyError: vi.fn(),
  notifySuccess: vi.fn(),
  notifyWarning: vi.fn(),
};

const ProfessorsPageWithLinkedNavigation = () => {
  const navigate = useNavigate();
  return (
    <>
      <button
        type="button"
        onClick={() => navigate("/professors?keyword=missing-profile%40example.edu")}
      >
        Go to linked professor
      </button>
      <ProfessorsPage />
    </>
  );
};

const selectedIdentity: IdentityDTO = {
  id: 1,
  name: "默认身份",
  profile_name: "Junie",
  sender_name: "Junie",
  email_address: "junie@example.com",
  smtp_host: "smtp.example.com",
  smtp_port: 465,
  smtp_username: "junie@example.com",
  smtp_password: "secret",
  imap_host: null,
  imap_port: null,
  imap_username: null,
  imap_password: null,
  default_language: "zh-CN",
  outreach_generation_mode: "llm",
  outreach_template_subject: "Hello",
  outreach_template_body_text: "Body",
  outreach_template_body_html: null,
  current_primary_material_id: 1,
  current_primary_material: null,
  match_threshold: null,
  daily_send_limit: null,
  send_interval_min: null,
  send_interval_max: null,
  same_domain_cooldown_minutes: null,
  is_default: true,
  materials: [],
  created_at: "2026-05-01T00:00:00",
  updated_at: "2026-05-01T00:00:00",
};

const selectedLlmProfile: LLMProfileDTO = {
  id: 1,
  name: "默认模型",
  provider: "openai",
  api_base_url: null,
  api_key: "secret",
  model_name: "gpt-5.4-mini",
  matcher_prompt_template: null,
  writer_prompt_template: null,
  temperature: null,
  max_tokens: null,
  is_default: true,
  created_at: "2026-05-01T00:00:00",
  updated_at: "2026-05-01T00:00:00",
};

const selectionContextValue = {
  identities: [selectedIdentity],
  llmProfiles: [selectedLlmProfile],
  selectedIdentityId: selectedIdentity.id,
  selectedLlmProfileId: selectedLlmProfile.id,
  selectedIdentity,
  selectedLlmProfile,
  loading: false,
  setSelectedIdentityId: vi.fn(),
  setSelectedLlmProfileId: vi.fn(),
  refreshSelections: vi.fn(),
};

const deferred = <T,>() => {
  let resolve: (value: T) => void = () => {};
  const promise = new Promise<T>((nextResolve) => {
    resolve = nextResolve;
  });
  return { promise, resolve };
};

const createDashboardProfessor = (
  id: number,
  name = `导师 ${id}`,
): ProfessorDashboardItemDTO => ({
  id,
  name,
  email: `professor-${id}@example.edu`,
  title: id % 2 === 0 ? "教授" : "副教授",
  university: "示例大学",
  school: id % 2 === 0 ? "计算机学院" : "软件学院",
  department: "人工智能系",
  research_direction: "自然语言处理",
  recent_papers: [`Paper ${id}`],
  match_score: null,
  sent_count: 0,
  status: "not_contacted",
  last_sent_at: null,
  last_replied_at: null,
  tags: [],
});

const dashboardProfessors: ProfessorDashboardItemDTO[] = Array.from(
  { length: 11 },
  (_, index) => createDashboardProfessor(index + 11),
);

const managementProfessors: ProfessorManagementItemDTO[] =
  dashboardProfessors.map((professor) => ({
    ...professor,
    profile_url: null,
    source_url: null,
    crawl_status: "manual",
    skip_reason: null,
    archived_at: null,
    created_at: "2026-05-01T00:00:00",
    updated_at: "2026-05-01T00:00:00",
  }));

vi.mock("@/context/NotificationContext", () => ({
  useNotification: () => notifyMock,
}));

vi.mock("@/context/SelectionContext", () => ({
  useSelectionContext: () => selectionContextValue,
}));

vi.mock("@/features/onboarding/client/getOnboardingState", () => ({
  getOnboardingState: () => ({
    completed: true,
    stage: "first_task",
    description: "",
    nextActionHref: "/",
  }),
}));

vi.mock("@/lib/api/professorsApi", () => ({
  archiveProfessor: vi.fn(),
  bulkArchiveProfessors: vi.fn(),
  bulkUpdateProfessorTags: vi.fn(),
  createProfessor: vi.fn(),
  createProfessorTag: vi.fn(async () => ({
    id: 2,
    name: "已联系",
    text_color: "#1d4ed8",
    background_color: "#dbeafe",
  })),
  deleteProfessorTag: vi.fn(async () => ({
    ok: true,
    affected_count: 1,
    message: "标签已删除",
  })),
  getProfessorTagUsage: vi.fn(async () => ({
    tag: {
      id: 1,
      name: "高意愿",
      text_color: "#166534",
      background_color: "#dcfce7",
    },
    professors: [],
  })),
  getProfessorTemplateDownloadUrl: vi.fn(),
  importProfessorsFromFile: vi.fn(),
  listProfessorTags: vi.fn(async () => [
    {
      id: 1,
      name: "高意愿",
      text_color: "#166534",
      background_color: "#dcfce7",
    },
  ]),
  listProfessors: vi.fn(async () => dashboardProfessors),
  listProfessorsForManagement: vi.fn(async () => managementProfessors),
  restoreProfessor: vi.fn(),
  updateProfessorTags: vi.fn(async (_professorId: number, tagIds: number[]) => ({
    ...managementProfessors[0],
    tags: tagIds.map((tagId) => ({
      id: tagId,
      name: "高意愿",
      text_color: "#166534",
      background_color: "#dcfce7",
    })),
  })),
}));

vi.mock("@/lib/api/crawlJobsApi", () => ({
  createCrawlJob: vi.fn(),
}));

vi.mock("@/lib/api/emailTasksApi", () => ({
  calculateMatch: vi.fn(),
}));

vi.mock("@/lib/api/workspacesApi", () => ({
  ensureWorkspaceTask: vi.fn(),
}));

describe("selection controls", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    window.sessionStorage.clear();
    vi.mocked(createProfessorTag).mockResolvedValue({
      id: 2,
      name: "已联系",
      text_color: "#1d4ed8",
      background_color: "#dbeafe",
    });
    vi.mocked(deleteProfessorTag).mockResolvedValue({
      ok: true,
      affected_count: 1,
      message: "标签已删除",
    });
    vi.mocked(getProfessorTagUsage).mockResolvedValue({
      tag: {
        id: 1,
        name: "高意愿",
        text_color: "#166534",
        background_color: "#dcfce7",
      },
      professors: [],
    });
    vi.mocked(listProfessors).mockResolvedValue(dashboardProfessors);
    vi.mocked(listProfessorsForManagement).mockResolvedValue(managementProfessors);
    vi.mocked(updateProfessorTags).mockResolvedValue({
      ...managementProfessors[0],
      tags: [
        {
          id: 1,
          name: "高意愿",
          text_color: "#166534",
          background_color: "#dcfce7",
        },
      ],
    });
    vi.mocked(bulkUpdateProfessorTags).mockResolvedValue({
      ok: true,
      affected_count: 1,
      message: "已更新 1 位导师的标签",
      professors: [
        {
          ...managementProfessors[0],
          tags: [
            {
              id: 1,
              name: "高意愿",
              text_color: "#166534",
              background_color: "#dcfce7",
            },
          ],
        },
      ],
    });
    Object.assign(selectionContextValue, {
      identities: [selectedIdentity],
      llmProfiles: [selectedLlmProfile],
      selectedIdentityId: selectedIdentity.id,
      selectedLlmProfileId: selectedLlmProfile.id,
      selectedIdentity,
      selectedLlmProfile,
      loading: false,
    });
  });

  it("shows a skeleton in the content area while the desktop backend is still loading", () => {
    Object.assign(selectionContextValue, {
      identities: [],
      llmProfiles: [],
      selectedIdentityId: null,
      selectedLlmProfileId: null,
      selectedIdentity: null,
      selectedLlmProfile: null,
      loading: true,
    });

    render(
      <MemoryRouter>
        <HomePage />
      </MemoryRouter>,
    );

    expect(
      screen.getByTestId("home-page-loading-skeleton"),
    ).toBeInTheDocument();
    expect(
      screen.queryByText("导师看板"),
    ).not.toBeInTheDocument();
  });

  it("selects all filtered home results across pages", async () => {
    render(
      <MemoryRouter>
        <HomePage />
      </MemoryRouter>,
    );

    const selectFilteredResults = await screen.findByRole("button", {
      name: "选择全部筛选结果",
    });

    expect(
      screen.queryByRole("button", { name: "清空选择" }),
    ).not.toBeInTheDocument();

    fireEvent.click(selectFilteredResults);

    expect(
      await screen.findByText("已选中 11 位导师"),
    ).toBeInTheDocument();
    const homeSelectionDock = screen.getByText("已选中 11 位导师")
      .parentElement?.parentElement;
    expect(homeSelectionDock).toHaveClass(
      "w-fit",
      "max-w-full",
      "justify-center",
    );
    expect(homeSelectionDock).not.toHaveClass("max-w-3xl", "justify-between");
    expect(
      screen.getByRole("button", { name: "清空选择" }),
    ).toBeInTheDocument();

    fireEvent.click(
      screen.getByRole("button", { name: "取消选择全部筛选结果" }),
    );

    expect(screen.queryByText("已选中 11 位导师")).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "清空选择" }),
    ).not.toBeInTheDocument();
  });

  it("paginates home professors with ten items per page", async () => {
    render(
      <MemoryRouter>
        <HomePage />
      </MemoryRouter>,
    );

    expect(await screen.findByText("导师 11")).toBeInTheDocument();
    expect(screen.getByText("导师 20")).toBeInTheDocument();
    expect(screen.queryByText("导师 21")).not.toBeInTheDocument();
    expect(screen.getByText(/第 1 \/ 2 页/)).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "下一页" }));

    expect(await screen.findByText("导师 21")).toBeInTheDocument();
    expect(screen.queryByText("导师 11")).not.toBeInTheDocument();
  });

  it("switches time sort direction inside the sort menu and highlights replied rows", async () => {
    vi.mocked(listProfessors).mockResolvedValue([
      {
        ...createDashboardProfessor(201, "Replied Mentor"),
        last_replied_at: "2026-06-02T09:12:00Z",
      },
      createDashboardProfessor(202, "No Reply Mentor"),
      {
        ...createDashboardProfessor(203, "Older Reply Mentor"),
        last_replied_at: "2026-06-01T08:05:00Z",
      },
    ]);

    render(
      <MemoryRouter>
        <HomePage />
      </MemoryRouter>,
    );

    const sortButton = await screen.findByRole("button", { name: "排序" });

    expect(sortButton).not.toHaveTextContent("回复时间 ↓");
    expect(sortButton).not.toHaveTextContent("回复时间 ↑");

    fireEvent.click(sortButton);
    fireEvent.click(await screen.findByRole("button", { name: "回复时间" }));

    await waitFor(() => {
      expect(screen.getByRole("button", { name: "排序" })).toHaveTextContent("回复时间 ↓");
    });
    expect(screen.getByTestId("dashboard-professor-row-201")).toHaveClass(
      "bg-emerald-50",
    );
    expect(screen.getByTestId("dashboard-professor-row-203")).toHaveClass(
      "bg-emerald-50",
    );
    expect(screen.getByTestId("dashboard-professor-row-202")).not.toHaveClass(
      "bg-emerald-50",
    );

    fireEvent.click(screen.getByRole("button", { name: "排序" }));
    fireEvent.click(
      await screen.findByRole("button", { name: "切换回复时间排序方向" }),
    );

    await waitFor(() => {
      expect(screen.getByRole("button", { name: "排序" })).toHaveTextContent("回复时间 ↑");
    });
    expect(
      screen.getAllByTestId(/dashboard-professor-row-/).map((row) => row.dataset.testid),
    ).toEqual([
      "dashboard-professor-row-203",
      "dashboard-professor-row-201",
      "dashboard-professor-row-202",
    ]);
    expect(
      screen.queryByRole("button", { name: "最近回复在前" }),
    ).not.toBeInTheDocument();
  });

  it("uses the same green highlight for sent-time sorting", async () => {
    vi.mocked(listProfessors).mockResolvedValue([
      {
        ...createDashboardProfessor(301, "Sent Mentor"),
        last_sent_at: "2026-06-02T09:12:00Z",
      },
      createDashboardProfessor(302, "No Sent Mentor"),
    ]);

    render(
      <MemoryRouter>
        <HomePage />
      </MemoryRouter>,
    );

    fireEvent.click(await screen.findByRole("button", { name: "排序" }));
    fireEvent.click(await screen.findByRole("button", { name: "发送时间" }));

    await waitFor(() => {
      expect(screen.getByRole("button", { name: "排序" })).toHaveTextContent("发送时间 ↓");
    });
    expect(screen.getByTestId("dashboard-professor-row-301")).toHaveClass(
      "bg-emerald-50",
    );
    expect(screen.getByTestId("dashboard-professor-row-301")).not.toHaveClass(
      "bg-cyan-50",
    );
    expect(screen.getByTestId("dashboard-professor-row-302")).not.toHaveClass(
      "bg-emerald-50",
    );
  });

  it("shows a stable management skeleton before the first professor list load resolves", async () => {
    vi.mocked(listProfessorsForManagement).mockImplementation(
      () => new Promise<ProfessorManagementItemDTO[]>(() => {}),
    );

    render(
      <MemoryRouter>
        <ProfessorsPage />
      </MemoryRouter>,
    );

    expect(
      await screen.findByTestId("professors-page-loading-skeleton"),
    ).toBeInTheDocument();
    expect(screen.queryByText("暂无导师")).not.toBeInTheDocument();
    expect(screen.queryByTestId("professor-empty-intake")).not.toBeInTheDocument();
  });
  it("selects all filtered management results across pages", async () => {
    render(
      <MemoryRouter>
        <ProfessorsPage />
      </MemoryRouter>,
    );

    const tableHeader = await screen.findByTestId("professor-table-header");
    const selectFilteredResults = within(tableHeader).getByRole("button", {
      name: "选择全部筛选结果",
    });

    expect(screen.getByText("导师 11")).toBeInTheDocument();
    expect(screen.getByText("导师 20")).toBeInTheDocument();
    expect(screen.queryByText("导师 21")).not.toBeInTheDocument();

    expect(
      screen.queryByRole("button", { name: "清空选择" }),
    ).not.toBeInTheDocument();

    fireEvent.click(selectFilteredResults);

    expect(
      await screen.findByText("已选中 11 位导师"),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "清空选择" }),
    ).toBeInTheDocument();
  });

  it("opens management advanced filters and resets them", async () => {
    render(
      <MemoryRouter>
        <ProfessorsPage />
      </MemoryRouter>,
    );

    const advancedFilterButton = await screen.findByRole("button", {
      name: "高级筛选",
    });
    const filterToolbar = screen.getByTestId("professor-filter-toolbar");

    expect(within(filterToolbar).getByRole("textbox")).toBeInTheDocument();
    expect(
      within(filterToolbar).getByRole("button", { name: "排序" }),
    ).toBeInTheDocument();
    expect(
      within(filterToolbar).getByRole("button", { name: "高级筛选" }),
    ).toBeInTheDocument();
    expect(
      within(filterToolbar).getByRole("button", { name: "重置" }),
    ).toBeInTheDocument();

    fireEvent.click(advancedFilterButton);

    fireEvent.click(
      screen.getByRole("button", { name: "学校：全部学校" }),
    );
    fireEvent.click(screen.getByRole("option", { name: "示例大学" }));

    await waitFor(() => {
      expect(
        screen.getByRole("button", { name: "高级筛选 1" }),
      ).toBeInTheDocument();
    });    expect(
      screen.getByRole("button", { name: "清空高级筛选" }),
    ).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "清空高级筛选" }));

    expect(
      screen.getByRole("button", { name: "高级筛选" }),
    ).toBeInTheDocument();
  });

  it("restores management filters after remount", async () => {
    const { unmount } = render(
      <MemoryRouter>
        <ProfessorsPage />
      </MemoryRouter>,
    );

    const advancedFilterButton = await screen.findByRole("button", {
      name: "高级筛选",
    });

    fireEvent.click(advancedFilterButton);
    fireEvent.click(
      screen.getByRole("button", { name: "学校：全部学校" }),
    );
    fireEvent.click(screen.getByRole("option", { name: "示例大学" }));

    await waitFor(() => {
      expect(
        screen.getByRole("button", { name: "高级筛选 1" }),
      ).toBeInTheDocument();
    });
    await waitFor(() => {
      const storedValue = Array.from({ length: window.sessionStorage.length }, (_, index) =>
        window.sessionStorage.getItem(window.sessionStorage.key(index) ?? ""),
      ).join("\n");
      expect(storedValue).toContain("示例大学");
    });

    unmount();

    render(
      <MemoryRouter>
        <ProfessorsPage />
      </MemoryRouter>,
    );

    await waitFor(() => {
      expect(
        screen.getByRole("button", { name: "高级筛选 1" }),
      ).toBeInTheDocument();
    });
  });

  it("switches back to active professors when opening management with a linked keyword", async () => {
    window.sessionStorage.setItem(
      "professors_page_filters",
      JSON.stringify({
        archiveFilter: "archived",
        filters: {
          keyword: "",
          universities: [],
          schools: [],
          departments: [],
          titles: [],
        },
        advancedFiltersOpen: false,
        sortKey: "latest",
        currentPage: 1,
      }),
    );
    const activeTarget = {
      ...managementProfessors[0],
      id: 999,
      name: "Missing Profile Mentor",
      email: "missing-profile@example.edu",
      university: "Target University",
      school: "Target School",
    };
    vi.mocked(listProfessorsForManagement).mockImplementation(async (archived) =>
      archived === "active" ? [activeTarget] : [],
    );

    render(
      <MemoryRouter initialEntries={["/professors?keyword=missing-profile%40example.edu"]}>
        <ProfessorsPage />
      </MemoryRouter>,
    );

    expect(await screen.findByText("Missing Profile Mentor")).toBeInTheDocument();
    await waitFor(() => {
      expect(listProfessorsForManagement).toHaveBeenLastCalledWith("active");
    });
    expect(listProfessorsForManagement).not.toHaveBeenCalledWith("archived");
  });

  it("keeps linked keyword active results when the previous archived request resolves later", async () => {
    window.sessionStorage.setItem(
      "professors_page_filters",
      JSON.stringify({
        archiveFilter: "archived",
        filters: {
          keyword: "",
          universities: [],
          schools: [],
          departments: [],
          titles: [],
        },
        advancedFiltersOpen: false,
        sortKey: "latest",
        currentPage: 1,
      }),
    );
    const activeTarget = {
      ...managementProfessors[0],
      id: 999,
      name: "Missing Profile Mentor",
      email: "missing-profile@example.edu",
      university: "Target University",
      school: "Target School",
    };
    let resolveArchived: (value: ProfessorManagementItemDTO[]) => void = () => {};
    vi.mocked(listProfessorsForManagement).mockImplementation((archived) => {
      if (archived === "archived") {
        return new Promise<ProfessorManagementItemDTO[]>((resolve) => {
          resolveArchived = resolve;
        });
      }
      return Promise.resolve([activeTarget]);
    });

    render(
      <MemoryRouter initialEntries={["/professors?keyword=missing-profile%40example.edu"]}>
        <ProfessorsPage />
      </MemoryRouter>,
    );

    expect(await screen.findByText("Missing Profile Mentor")).toBeInTheDocument();
    resolveArchived?.([]);
    await waitFor(() => {
      expect(screen.getByText("Missing Profile Mentor")).toBeInTheDocument();
    });
  });

  it("switches to active professors when a linked keyword is opened after the page is mounted", async () => {
    window.sessionStorage.setItem(
      "professors_page_filters",
      JSON.stringify({
        archiveFilter: "archived",
        filters: {
          keyword: "",
          universities: [],
          schools: [],
          departments: [],
          titles: [],
        },
        advancedFiltersOpen: false,
        sortKey: "latest",
        currentPage: 1,
      }),
    );
    const activeTarget = {
      ...managementProfessors[0],
      id: 999,
      name: "Missing Profile Mentor",
      email: "missing-profile@example.edu",
      university: "Target University",
      school: "Target School",
    };
    vi.mocked(listProfessorsForManagement).mockImplementation(async (archived) =>
      archived === "active" ? [activeTarget] : [],
    );

    render(
      <MemoryRouter initialEntries={["/professors"]}>
        <Routes>
          <Route path="/professors" element={<ProfessorsPageWithLinkedNavigation />} />
        </Routes>
      </MemoryRouter>,
    );
    await waitFor(() => {
      expect(listProfessorsForManagement).toHaveBeenCalledWith("archived");
    });

    fireEvent.click(screen.getByRole("button", { name: "Go to linked professor" }));

    expect(await screen.findByText("Missing Profile Mentor")).toBeInTheDocument();
    await waitFor(() => {
      expect(listProfessorsForManagement).toHaveBeenLastCalledWith("active");
    });
  });

  it("clears stored advanced filters when opening professor management with a linked keyword", async () => {
    window.sessionStorage.setItem(
      "professors_page_filters",
      JSON.stringify({
        archiveFilter: "active",
        filters: {
          keyword: "",
          universities: ["示例大学"],
          schools: [],
          departments: [],
          titles: [],
        },
        advancedFiltersOpen: true,
        sortKey: "latest",
        currentPage: 1,
      }),
    );
    vi.mocked(listProfessorsForManagement).mockResolvedValue([
      ...managementProfessors,
      {
        ...managementProfessors[0],
        id: 999,
        name: "缺资料导师",
        email: "missing-profile@example.edu",
        university: "目标大学",
        school: "目标学院",
      },
    ]);

    render(
      <MemoryRouter initialEntries={["/professors?keyword=missing-profile%40example.edu"]}>
        <ProfessorsPage />
      </MemoryRouter>,
    );

    expect(await screen.findByText("缺资料导师")).toBeInTheDocument();
    expect(screen.getByRole("textbox")).toHaveValue("missing-profile@example.edu");
    expect(screen.getByRole("button", { name: "高级筛选" })).toBeInTheDocument();
  });

  it("adds a professor tag from the home page", async () => {
    render(
      <MemoryRouter>
        <HomePage />
      </MemoryRouter>,
    );

    expect(await screen.findByText("导师 11")).toBeInTheDocument();

    fireEvent.click(screen.getAllByRole("button", { name: "给导师添加标签" })[0]);
    const dialog = await screen.findByRole("dialog", { name: "添加导师标签" });
    fireEvent.click(within(dialog).getByRole("button", { name: "选择标签 高意愿" }));
    fireEvent.click(within(dialog).getByRole("button", { name: "保存标签" }));

    await waitFor(() => {
      expect(updateProfessorTags).toHaveBeenCalledWith(
        11,
        [1],
      );
    });
  });

  it("creates and assigns a professor tag from the home page dialog", async () => {
    render(
      <MemoryRouter>
        <HomePage />
      </MemoryRouter>,
    );

    expect(await screen.findByText("导师 11")).toBeInTheDocument();

    fireEvent.click(screen.getAllByRole("button", { name: "给导师添加标签" })[0]);
    const dialog = await screen.findByRole("dialog", { name: "添加导师标签" });
    fireEvent.click(within(dialog).getByRole("button", { name: "新增标签" }));
    fireEvent.change(within(dialog).getByRole("textbox", { name: "新增标签名" }), {
      target: { value: "已联系" },
    });
    fireEvent.click(within(dialog).getByRole("button", { name: "创建标签" }));

    await waitFor(() => {
      expect(createProfessorTag).toHaveBeenCalledWith({
        name: "已联系",
        text_color: "#166534",
        background_color: "#dcfce7",
      });
    });
    expect(
      await within(dialog).findByRole("button", { name: "选择标签 已联系" }),
    ).toHaveAttribute("aria-pressed", "true");

    fireEvent.click(within(dialog).getByRole("button", { name: "保存标签" }));

    await waitFor(() => {
      expect(updateProfessorTags).toHaveBeenCalledWith(
        11,
        [2],
      );
    });
  });

  it("creates and assigns a professor tag from the management row dialog", async () => {
    render(
      <MemoryRouter>
        <ProfessorsPage />
      </MemoryRouter>,
    );

    expect(await screen.findByText("导师 11")).toBeInTheDocument();

    fireEvent.click(screen.getAllByRole("button", { name: "给导师添加标签" })[0]);
    const dialog = await screen.findByRole("dialog", { name: "添加导师标签" });
    fireEvent.click(within(dialog).getByRole("button", { name: "新增标签" }));
    fireEvent.change(within(dialog).getByRole("textbox", { name: "新增标签名" }), {
      target: { value: "已联系" },
    });
    fireEvent.click(within(dialog).getByRole("button", { name: "创建标签" }));

    await waitFor(() => {
      expect(createProfessorTag).toHaveBeenCalledWith({
        name: "已联系",
        text_color: "#166534",
        background_color: "#dcfce7",
      });
    });
    expect(
      await within(dialog).findByRole("button", { name: "选择标签 已联系" }),
    ).toHaveAttribute("aria-pressed", "true");

    fireEvent.click(within(dialog).getByRole("button", { name: "保存标签" }));

    await waitFor(() => {
      expect(updateProfessorTags).toHaveBeenCalledWith(
        11,
        [2],
      );
    });
  });

  it("shows folded homepage tags without enabling primary tag saves", async () => {
    const professorWithTags = {
      ...createDashboardProfessor(401, "排序导师"),
      tags: [
        {
          id: 1,
          name: "高意愿",
          text_color: "#166534",
          background_color: "#dcfce7",
        },
        {
          id: 2,
          name: "羊导",
          text_color: "#7c2d12",
          background_color: "#ffedd5",
        },
        {
          id: 3,
          name: "高强度",
          text_color: "#991b1b",
          background_color: "#fee2e2",
        },
        {
          id: 4,
          name: "已退休",
          text_color: "#44403c",
          background_color: "#f5f5f4",
        },
      ],
    };
    vi.mocked(listProfessors).mockResolvedValue([professorWithTags]);

    render(
      <MemoryRouter>
        <HomePage />
      </MemoryRouter>,
    );

    expect(await screen.findByText("排序导师")).toBeInTheDocument();

    const overflowButton = await screen.findByRole("button", {
      name: "查看全部标签，剩余 2 个",
    });
    fireEvent.click(overflowButton);
    const foldedTags = screen.getByRole("dialog", { name: "折叠标签" });

    expect(within(foldedTags).getByText("高强度")).toBeInTheDocument();
    expect(within(foldedTags).getByText("已退休")).toBeInTheDocument();
    expect(
      within(foldedTags).queryByRole("button", { name: "选择标签 高强度" }),
    ).not.toBeInTheDocument();
    expect(updateProfessorTags).not.toHaveBeenCalled();
  });

  it("queues management primary tag changes while a previous tag save is in flight", async () => {
    const professorWithTags: ProfessorManagementItemDTO = {
      ...managementProfessors[0],
      id: 501,
      name: "管理排序导师",
      tags: [
        {
          id: 1,
          name: "高意愿",
          text_color: "#166534",
          background_color: "#dcfce7",
        },
        {
          id: 2,
          name: "羊导",
          text_color: "#7c2d12",
          background_color: "#ffedd5",
        },
        {
          id: 3,
          name: "高强度",
          text_color: "#991b1b",
          background_color: "#fee2e2",
        },
        {
          id: 4,
          name: "已退休",
          text_color: "#44403c",
          background_color: "#f5f5f4",
        },
      ],
    };
    const firstSave = deferred<ProfessorManagementItemDTO>();
    vi.mocked(listProfessorsForManagement).mockResolvedValue([professorWithTags]);
    vi.mocked(updateProfessorTags)
      .mockImplementationOnce(() => firstSave.promise)
      .mockResolvedValue({
        ...professorWithTags,
        tags: [
          professorWithTags.tags[3],
          professorWithTags.tags[0],
          professorWithTags.tags[1],
          professorWithTags.tags[2],
        ],
      });

    render(
      <MemoryRouter>
        <ProfessorsPage />
      </MemoryRouter>,
    );

    expect(await screen.findByText("管理排序导师")).toBeInTheDocument();

    fireEvent.click(
      await screen.findByRole("button", { name: "查看全部标签，剩余 3 个" }),
    );
    const foldedTags = screen.getByRole("dialog", { name: "折叠标签" });
    fireEvent.click(within(foldedTags).getByRole("button", { name: "选择标签 高强度" }));

    await waitFor(() => {
      expect(updateProfessorTags).toHaveBeenCalledTimes(1);
    });
    expect(updateProfessorTags).toHaveBeenNthCalledWith(
      1,
      professorWithTags.id,
      [3, 1, 2, 4],
    );

    fireEvent.click(within(foldedTags).getByRole("button", { name: "选择标签 已退休" }));

    expect(updateProfessorTags).toHaveBeenCalledTimes(1);

    firstSave.resolve({
      ...professorWithTags,
      tags: [
        professorWithTags.tags[2],
        professorWithTags.tags[0],
        professorWithTags.tags[1],
        professorWithTags.tags[3],
      ],
    });

    await waitFor(() => {
      expect(updateProfessorTags).toHaveBeenCalledTimes(2);
    });
    expect(updateProfessorTags).toHaveBeenLastCalledWith(
      professorWithTags.id,
      [4, 1, 2, 3],
    );
  });

  it("bulk updates tags from the home selection bar", async () => {
    vi.mocked(bulkUpdateProfessorTags).mockResolvedValue({
      ok: true,
      affected_count: 1,
      message: "已更新 1 位导师的标签",
      professors: [
        {
          ...managementProfessors[0],
          id: 11,
          name: "导师 11",
          tags: [
            {
              id: 1,
              name: "高意愿",
              text_color: "#166534",
              background_color: "#dcfce7",
            },
          ],
        },
      ],
    });

    render(
      <MemoryRouter>
        <HomePage />
      </MemoryRouter>,
    );

    fireEvent.click(await screen.findByRole("button", { name: "选择 导师 11" }));
    fireEvent.click(screen.getByRole("button", { name: "批量改标签" }));
    fireEvent.click(await screen.findByRole("button", { name: "选择标签 高意愿" }));
    fireEvent.click(screen.getByRole("button", { name: "追加标签" }));

    expect(bulkUpdateProfessorTags).not.toHaveBeenCalled();
    fireEvent.click(await screen.findByRole("button", { name: "确认追加" }));

    await waitFor(() => {
      expect(bulkUpdateProfessorTags).toHaveBeenCalledWith({
        professor_ids: [11],
        mode: "add",
        tag_ids: [1],
      });
    });
    expect(notifyMock.notifySuccess).toHaveBeenCalledWith(
      "标签已更新",
      "已更新 1 位导师的标签。",
    );
  });

  it("deletes a professor tag from the home bulk tag dialog", async () => {
    render(
      <MemoryRouter>
        <HomePage />
      </MemoryRouter>,
    );

    fireEvent.click(await screen.findByRole("button", { name: "选择 导师 11" }));
    fireEvent.click(screen.getByRole("button", { name: "批量改标签" }));
    expect(
      screen.queryByRole("button", { name: "删除标签 高意愿" }),
    ).not.toBeInTheDocument();
    fireEvent.click(await screen.findByRole("button", { name: "删除标签" }));
    fireEvent.click(
      await screen.findByRole("button", { name: "删除标签 高意愿" }),
    );
    fireEvent.click(await screen.findByRole("button", { name: "确认删除" }));

    await waitFor(() => {
      expect(getProfessorTagUsage).toHaveBeenCalledWith(1);
      expect(deleteProfessorTag).toHaveBeenCalledWith(1);
    });
    expect(notifyMock.notifySuccess).toHaveBeenCalledWith(
      "删除标签成功",
      "标签已删除",
    );
  });

  it("bulk updates tags from the management selection bar", async () => {
    vi.mocked(bulkUpdateProfessorTags).mockResolvedValue({
      ok: true,
      affected_count: 1,
      message: "已更新 1 位导师的标签",
      professors: [
        {
          ...managementProfessors[0],
          id: 11,
          name: "导师 11",
          tags: [
            {
              id: 2,
              name: "已联系",
              text_color: "#1d4ed8",
              background_color: "#dbeafe",
            },
          ],
        },
      ],
    });

    render(
      <MemoryRouter>
        <ProfessorsPage />
      </MemoryRouter>,
    );

    fireEvent.click(await screen.findByRole("button", { name: "选择 导师 11" }));
    fireEvent.click(screen.getByRole("button", { name: "批量改标签" }));
    fireEvent.click(await screen.findByRole("button", { name: "切换为移除标签" }));
    fireEvent.click(screen.getByRole("button", { name: "选择标签 高意愿" }));
    fireEvent.click(screen.getByRole("button", { name: "移除标签" }));

    expect(bulkUpdateProfessorTags).not.toHaveBeenCalled();
    fireEvent.click(await screen.findByRole("button", { name: "确认移除" }));

    await waitFor(() => {
      expect(bulkUpdateProfessorTags).toHaveBeenCalledWith({
        professor_ids: [11],
        mode: "remove",
        tag_ids: [1],
      });
    });
  });

  it("warns that original tags will be replaced before bulk replace", async () => {
    render(
      <MemoryRouter>
        <HomePage />
      </MemoryRouter>,
    );

    fireEvent.click(await screen.findByRole("button", { name: "选择 导师 11" }));
    fireEvent.click(screen.getByRole("button", { name: "批量改标签" }));
    fireEvent.click(await screen.findByRole("button", { name: "切换为覆盖标签" }));
    fireEvent.click(screen.getByRole("button", { name: "选择标签 高意愿" }));
    fireEvent.click(screen.getByRole("button", { name: "覆盖标签" }));

    expect(await screen.findByText("确认覆盖标签？")).toBeInTheDocument();
    expect(screen.getByText(/原来的标签将会被替换/)).toBeInTheDocument();
  });
});
