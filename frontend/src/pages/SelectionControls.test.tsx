import { fireEvent, render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
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
  createProfessor: vi.fn(),
  getProfessorTemplateDownloadUrl: vi.fn(),
  importProfessorsFromFile: vi.fn(),
  listProfessors: vi.fn(async () => dashboardProfessors),
  listProfessorsForManagement: vi.fn(async () => managementProfessors),
  restoreProfessor: vi.fn(),
  updateProfessor: vi.fn(),
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
});
