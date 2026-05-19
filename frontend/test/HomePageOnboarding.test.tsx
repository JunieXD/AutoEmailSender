import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { Link, MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { HomePage } from "@/pages/HomePage";
import type { IdentityDTO, LLMProfileDTO, ProfessorDashboardItemDTO } from "@/types";

const mockedUseSelectionContext = vi.hoisted(() => vi.fn());
const mockedListProfessors = vi.hoisted(() => vi.fn());
const mockedNotifications = vi.hoisted(() => ({
  notifyError: vi.fn(),
  notifySuccess: vi.fn(),
  notifyWarning: vi.fn(),
}));

vi.mock("@/context/SelectionContext", () => ({
  useSelectionContext: mockedUseSelectionContext,
}));

vi.mock("@/lib/api/professorsApi", () => ({
  listProfessors: mockedListProfessors,
}));

vi.mock("@/lib/api/emailTasksApi", () => ({
  calculateMatch: vi.fn(),
}));

vi.mock("@/lib/api/workspacesApi", () => ({
  ensureWorkspaceTask: vi.fn(),
}));

vi.mock("@/lib/useConfirmDialog", () => ({
  useConfirmDialog: () => ({
    confirm: vi.fn(),
    dialog: null,
  }),
}));

vi.mock("@/context/NotificationContext", () => ({
  useNotification: () => mockedNotifications,
}));

const createIdentity = (overrides: Partial<IdentityDTO> = {}): IdentityDTO => ({
  id: 1,
  name: "测试身份",
  profile_name: "测试身份",
  sender_name: "测试身份",
  email_address: "sender@example.com",
  smtp_host: "smtp.example.com",
  smtp_port: 465,
  smtp_username: "sender@example.com",
  smtp_password: "secret",
  imap_host: null,
  imap_port: null,
  imap_username: null,
  imap_password: null,
  default_language: "zh-CN",
  outreach_generation_mode: "template",
  outreach_template_subject: null,
  outreach_template_body_text: "",
  outreach_template_body_html: "",
  current_primary_material_id: null,
  current_primary_material: null,
  match_threshold: null,
  daily_send_limit: null,
  send_interval_min: null,
  send_interval_max: null,
  same_domain_cooldown_minutes: null,
  is_default: true,
  materials: [],
  created_at: "2026-04-22T00:00:00Z",
  updated_at: "2026-04-22T00:00:00Z",
  ...overrides,
});

const selectedLlmProfile: LLMProfileDTO = {
  id: 1,
  name: "测试模型",
  provider: "openai",
  api_base_url: null,
  api_key: "test-key",
  model_name: "gpt-test",
  matcher_prompt_template: null,
  writer_prompt_template: null,
  temperature: null,
  max_tokens: null,
  is_default: true,
  created_at: "2026-04-22T00:00:00Z",
  updated_at: "2026-04-22T00:00:00Z",
};

const professor: ProfessorDashboardItemDTO = {
  id: 101,
  name: "王教授",
  email: "prof@example.com",
  title: "教授",
  university: "测试大学",
  school: "计算机学院",
  department: "人工智能系",
  research_direction: "多智能体系统",
  recent_papers: [],
  match_score: 92,
  sent_count: 0,
  status: "preparing",
};

const createProfessor = (
  id: number,
  name: string,
  status: ProfessorDashboardItemDTO["status"],
  overrides: Partial<ProfessorDashboardItemDTO> = {},
): ProfessorDashboardItemDTO => ({
  id,
  name,
  email: `${id}@example.com`,
  title: "教授",
  university: "测试大学",
  school: "计算机学院",
  department: "人工智能系",
  research_direction: "多智能体系统",
  recent_papers: [],
  match_score: 92,
  sent_count: 0,
  status,
  ...overrides,
});

const createDeferred = <T,>() => {
  let resolve!: (value: T | PromiseLike<T>) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
};

const renderPage = () =>
  render(
    <MemoryRouter>
      <HomePage />
    </MemoryRouter>,
  );

const renderPageWithNavigation = () =>
  render(
    <MemoryRouter initialEntries={["/"]}>
      <Routes>
        <Route path="/" element={<HomePage />} />
        <Route
          path="/professors"
          element={
            <div>
              <div>导师管理页</div>
              <Link to="/">返回首页</Link>
            </div>
          }
        />
      </Routes>
    </MemoryRouter>,
  );

describe("HomePage onboarding", () => {
  beforeEach(() => {
    window.sessionStorage.clear();
    mockedNotifications.notifyError.mockClear();
    mockedNotifications.notifySuccess.mockClear();
    mockedNotifications.notifyWarning.mockClear();
    mockedListProfessors.mockReset();
    mockedListProfessors.mockResolvedValue([]);
    mockedUseSelectionContext.mockReturnValue({
      selectedIdentityId: 1,
      selectedLlmProfileId: 1,
      selectedIdentity: createIdentity(),
      selectedLlmProfile,
    });
  });

  it("keeps the original loading state before the first professors load resolves", async () => {
    const deferred = createDeferred<ProfessorDashboardItemDTO[]>();
    mockedListProfessors.mockReturnValue(deferred.promise);
    mockedUseSelectionContext.mockReturnValue({
      selectedIdentityId: 1,
      selectedLlmProfileId: 1,
      selectedIdentity: createIdentity({
        current_primary_material_id: 11,
        outreach_template_body_text: "你好",
      }),
      selectedLlmProfile,
    });

    renderPage();

    await waitFor(() => {
      expect(mockedListProfessors).toHaveBeenCalledWith({
        identityId: 1,
        llmProfileId: 1,
      });
    });

    expect(screen.getByTestId("home-page-loading-skeleton")).toBeInTheDocument();
    expect(screen.queryByTestId("onboarding-checklist-card")).not.toBeInTheDocument();

    deferred.resolve([professor]);
  });

  it("shows the materials onboarding stage and links to profile when materials or templates are missing", async () => {
    renderPage();

    const card = await screen.findByTestId("onboarding-checklist-card");

    expect(within(card).getByRole("link", { name: "继续配置" })).toHaveAttribute(
      "href",
      "/profile",
    );
    expect(screen.queryByText("模板润色")).not.toBeInTheDocument();
    expect(screen.queryByText("固定模板")).not.toBeInTheDocument();
  });

  it("shows the onboarding card and links to professors when setup is complete but no professors exist", async () => {
    mockedUseSelectionContext.mockReturnValue({
      selectedIdentityId: 1,
      selectedLlmProfileId: 1,
      selectedIdentity: createIdentity({
        current_primary_material_id: 11,
        outreach_template_body_text: "老师您好",
      }),
      selectedLlmProfile,
    });

    renderPage();

    expect(await screen.findByTestId("onboarding-checklist-card")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "继续配置" })).toHaveAttribute(
      "href",
      "/professors",
    );
  });

  it("stays on the dashboard when all onboarding prerequisites are satisfied", async () => {
    mockedListProfessors.mockResolvedValue([professor]);
    mockedUseSelectionContext.mockReturnValue({
      selectedIdentityId: 1,
      selectedLlmProfileId: 1,
      selectedIdentity: createIdentity({
        current_primary_material_id: 11,
        outreach_template_body_text: "老师您好",
      }),
      selectedLlmProfile,
    });

    renderPage();

    expect(await screen.findByTestId("home-dashboard")).toBeInTheDocument();
    expect(screen.queryByText(/模式：/)).not.toBeInTheDocument();
    expect(screen.queryByTestId("onboarding-checklist-card")).not.toBeInTheDocument();
  });

  it("shows relationship status labels and filter controls on the dashboard", async () => {
    mockedListProfessors.mockResolvedValue([professor]);
    mockedUseSelectionContext.mockReturnValue({
      selectedIdentityId: 1,
      selectedLlmProfileId: 1,
      selectedIdentity: createIdentity({
        current_primary_material_id: 11,
        outreach_template_body_text: "老师您好",
      }),
      selectedLlmProfile,
    });

    renderPage();

    expect(await screen.findByTestId("home-dashboard")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "高级筛选" }));
    expect(screen.getByRole("button", { name: "状态：全部状态" })).toBeInTheDocument();
  });

  it("renders professors as a compact contact queue on the dashboard", async () => {
    mockedListProfessors.mockResolvedValue([professor]);
    mockedUseSelectionContext.mockReturnValue({
      selectedIdentityId: 1,
      selectedLlmProfileId: 1,
      selectedIdentity: createIdentity({
        current_primary_material_id: 11,
        outreach_template_body_text: "老师您好",
      }),
      selectedLlmProfile,
    });

    renderPage();

    expect(await screen.findByTestId("home-dashboard")).toBeInTheDocument();
    const selectButton = screen.getByRole("button", { name: "选择 王教授" });
    expect(selectButton).toHaveAttribute(
      "aria-pressed",
      "false",
    );
    expect(selectButton).toHaveClass("h-6", "w-6");
    expect(
      screen
        .getByRole("button", { name: "选择全部筛选结果" })
        .closest("section"),
    ).toHaveClass("overflow-hidden");
    expect(
      screen.queryByRole("checkbox", { name: "选择 王教授" }),
    ).not.toBeInTheDocument();
    const dashboardRow = screen.getByText("王教授").closest("article");
    expect(dashboardRow?.firstElementChild).toHaveClass("items-center");
    expect(screen.queryByText("匹配分数")).not.toBeInTheDocument();
    expect(screen.queryByText("发送次数")).not.toBeInTheDocument();
    expect(screen.queryByText("当前状态")).not.toBeInTheDocument();
  });

  it("renders six relationship filter options and filters the list through the UI", async () => {
    mockedListProfessors.mockResolvedValue([
      createProfessor(101, "未开始导师", "not_contacted"),
      createProfessor(102, "准备中导师", "preparing"),
      createProfessor(103, "待发送导师", "ready_to_send"),
      createProfessor(104, "已联系导师", "contacted"),
      createProfessor(105, "已回复导师", "replied"),
      createProfessor(106, "失败导师", "failed"),
    ]);
    mockedUseSelectionContext.mockReturnValue({
      selectedIdentityId: 1,
      selectedLlmProfileId: 1,
      selectedIdentity: createIdentity({
        current_primary_material_id: 11,
        outreach_template_body_text: "老师您好",
      }),
      selectedLlmProfile,
    });

    renderPage();

    expect(await screen.findByTestId("home-dashboard")).toBeInTheDocument();
    expect(await screen.findByText("未开始导师")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "高级筛选" }));
    fireEvent.click(screen.getByRole("button", { name: "状态：全部状态" }));

    expect(screen.getByRole("option", { name: "未开始" })).toBeInTheDocument();
    expect(screen.getByRole("option", { name: "准备中" })).toBeInTheDocument();
    expect(screen.getByRole("option", { name: "待发送" })).toBeInTheDocument();
    expect(screen.getByRole("option", { name: "已联系" })).toBeInTheDocument();
    expect(screen.getByRole("option", { name: "已回复" })).toBeInTheDocument();
    expect(screen.getByRole("option", { name: "失败" })).toBeInTheDocument();

    fireEvent.click(screen.getByRole("option", { name: "已联系" }));

    await waitFor(() => {
      expect(screen.queryByText("未开始导师")).not.toBeInTheDocument();
      expect(screen.queryByText("准备中导师")).not.toBeInTheDocument();
      expect(screen.queryByText("待发送导师")).not.toBeInTheDocument();
      expect(screen.queryByText("已回复导师")).not.toBeInTheDocument();
      expect(screen.queryByText("失败导师")).not.toBeInTheDocument();
    });
  });

  it("limits college options to the selected school on the dashboard", async () => {
    mockedListProfessors.mockResolvedValue([
      createProfessor(101, "MIT 工程导师", "not_contacted", {
        university: "MIT",
        school: "School of Engineering",
      }),
      createProfessor(102, "MIT 智能导师", "not_contacted", {
        university: "MIT",
        school: "AI Institute",
      }),
      createProfessor(103, "Stanford 医学导师", "not_contacted", {
        university: "Stanford",
        school: "School of Medicine",
      }),
    ]);
    mockedUseSelectionContext.mockReturnValue({
      selectedIdentityId: 1,
      selectedLlmProfileId: 1,
      selectedIdentity: createIdentity({
        current_primary_material_id: 11,
        outreach_template_body_text: "老师您好",
      }),
      selectedLlmProfile,
    });

    renderPage();

    expect(await screen.findByTestId("home-dashboard")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "高级筛选" }));
    fireEvent.click(screen.getByRole("button", { name: "学院：全部学院" }));
    fireEvent.click(screen.getByRole("option", { name: "School of Medicine" }));
    expect(
      screen.getByRole("button", { name: "学院：School of Medicine" }),
    ).toBeInTheDocument();
    fireEvent.keyDown(window, { key: "Escape" });

    fireEvent.click(screen.getByRole("button", { name: "学校：全部学校" }));
    fireEvent.click(screen.getByRole("option", { name: "MIT" }));
    fireEvent.keyDown(window, { key: "Escape" });

    expect(
      screen.getByRole("button", { name: "学院：全部学院" }),
    ).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "学院：全部学院" }));

    expect(screen.getByRole("option", { name: "AI Institute" })).toBeInTheDocument();
    expect(
      screen.getByRole("option", { name: "School of Engineering" }),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("option", { name: "School of Medicine" }),
    ).not.toBeInTheDocument();
  });

  it("keeps dashboard filters after leaving the home route and returning", async () => {
    mockedListProfessors.mockResolvedValue([
      createProfessor(101, "王教授", "not_contacted"),
      createProfessor(102, "李教授", "not_contacted"),
    ]);
    mockedUseSelectionContext.mockReturnValue({
      selectedIdentityId: 1,
      selectedLlmProfileId: 1,
      selectedIdentity: createIdentity({
        current_primary_material_id: 11,
        outreach_template_body_text: "老师您好",
      }),
      selectedLlmProfile,
    });

    renderPageWithNavigation();

    expect(await screen.findByTestId("home-dashboard")).toBeInTheDocument();
    expect(await screen.findByText("王教授")).toBeInTheDocument();
    expect(screen.getByText("李教授")).toBeInTheDocument();

    fireEvent.change(
      screen.getByPlaceholderText("导师、学校、学院、系所、职称、研究方向"),
      { target: { value: "王教授" } },
    );

    expect(screen.getByText("王教授")).toBeInTheDocument();
    expect(screen.queryByText("李教授")).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("link", { name: "管理导师" }));
    expect(screen.getByText("导师管理页")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("link", { name: "返回首页" }));

    expect(await screen.findByTestId("home-dashboard")).toBeInTheDocument();
    expect(
      screen.getByPlaceholderText("导师、学校、学院、系所、职称、研究方向"),
    ).toHaveValue("王教授");
    expect(screen.getByText("王教授")).toBeInTheDocument();
    expect(screen.queryByText("李教授")).not.toBeInTheDocument();
  });
});
