import { fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { listProfessors } from "@/entities/professor/api/professors";
import type {
  IdentityDTO,
  LLMProfileDTO,
  ProfessorDashboardItemDTO,
} from "@/types";
import { HomePage } from "@/pages/HomePage";

const mockedListProfessors = vi.hoisted(() => vi.fn());

vi.mock("@/app/providers/BackgroundTaskNotificationContext", () => ({
  useBackgroundTaskNotification: () => ({
    trackMatchAnalysisJob: vi.fn(),
  }),
}));

vi.mock("@/context/NotificationContext", () => ({
  useNotification: () => ({
    notifyError: vi.fn(),
    notifySuccess: vi.fn(),
    notifyWarning: vi.fn(),
  }),
}));

vi.mock("@/context/SelectionContext", () => ({
  useSelectionContext: () => ({
    selectedIdentityId: 1,
    selectedLlmProfileId: 2,
    selectedIdentity: {
      id: 1,
      name: "默认身份",
      current_primary_material_id: 10,
      outreach_template_body_text: "您好，想了解您的研究。",
      outreach_template_body_html: null,
    } as IdentityDTO,
    selectedLlmProfile: {
      id: 2,
      name: "默认模型",
    } as LLMProfileDTO,
  }),
}));

vi.mock("@/entities/professor/api/professors", () => ({
  listProfessors: mockedListProfessors,
  searchDashboardProfessors: async (payload: {
    identity_id: number;
    page: number;
    page_size: number;
  }) => {
    const allItems = await mockedListProfessors({ identityId: payload.identity_id });
    const start = (payload.page - 1) * payload.page_size;
    return {
      items: allItems.slice(start, start + payload.page_size),
      total_count: allItems.length,
      has_any_professors: allItems.length > 0,
      page: payload.page,
      page_size: payload.page_size,
      total_pages: Math.max(1, Math.ceil(allItems.length / payload.page_size)),
      next_cursor: null,
      filter_options: {
        universities: [], schools: [], departments: [], titles: [], tags: [],
      },
    };
  },
}));

vi.mock("@/lib/api/workspacesApi", () => ({
  ensureWorkspaceTask: vi.fn(),
}));

vi.mock("@/lib/api/emailTasksApi", () => ({
  calculateMatch: vi.fn(),
}));

vi.mock("@/lib/useConfirmDialog", () => ({
  useConfirmDialog: () => ({
    confirm: vi.fn(),
    dialog: null,
  }),
}));

const buildProfessor = (
  overrides: Partial<ProfessorDashboardItemDTO> = {},
): ProfessorDashboardItemDTO => ({
  id: 101,
  name: "王明",
  email: null,
  title: "教授",
  university: "江西财经大学",
  school: "计算机与人工智能学院",
  department: null,
  research_direction: "智能系统",
  recent_papers: [],
  match_score: null,
  sent_count: 0,
  status: "not_contacted",
  ...overrides,
});

describe("HomePage selection dock", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(listProfessors).mockResolvedValue([buildProfessor()]);
  });

  it("shows the selected professor action dock after checking a professor", async () => {
    render(
      <MemoryRouter>
        <HomePage />
      </MemoryRouter>,
    );

    const toggle = await screen.findByRole("button", { name: "选择 王明" });

    expect(screen.queryByText("已选中 1 位导师")).not.toBeInTheDocument();

    fireEvent.click(toggle);

    expect(screen.getByText("已选中 1 位导师")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "批量分析匹配度" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "创建批量任务" })).toBeInTheDocument();
  });
});
