import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { NotificationProvider } from "@/context/NotificationContext";
import { listProfessors } from "@/entities/professor/api/professors";
import { HomePage } from "@/pages/HomePage";
import type {
  IdentityDTO,
  LLMProfileDTO,
  ProfessorDashboardItemDTO,
} from "@/types";

const mockedListProfessors = vi.hoisted(() => vi.fn());

vi.mock("@/app/providers/BackgroundTaskNotificationContext", () => ({
  useBackgroundTaskNotification: () => ({
    trackMatchAnalysisJob: vi.fn(),
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
    loading: false,
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

const buildProfessor = (id: number): ProfessorDashboardItemDTO => ({
  id,
  name: `导师 ${id}`,
  email: null,
  title: "教授",
  university: "测试大学",
  school: "计算机学院",
  department: null,
  research_direction: "智能系统",
  recent_papers: [],
  match_score: null,
  sent_count: 0,
  status: "not_contacted",
});

const renderPage = () =>
  render(
    <MemoryRouter>
      <NotificationProvider>
        <HomePage />
      </NotificationProvider>
    </MemoryRouter>,
  );

describe("HomePage page size", () => {
  beforeEach(() => {
    localStorage.clear();
    vi.clearAllMocks();
    vi.mocked(listProfessors).mockResolvedValue(
      Array.from({ length: 12 }, (_, index) => buildProfessor(index + 1)),
    );
  });

  it("uses ten professors per page by default", async () => {
    renderPage();

    await waitFor(() => {
      expect(listProfessors).toHaveBeenCalled();
    });

    expect(await screen.findByText("导师 1")).toBeInTheDocument();
    expect(screen.getByText("导师 10")).toBeInTheDocument();
    expect(screen.queryByText("导师 11")).not.toBeInTheDocument();
    expect(
      screen.getByText("12 位 · 1/2 页 · 已选 0 位"),
    ).toBeInTheDocument();
  });

  it("changes and stores the independent home page size", async () => {
    renderPage();

    expect(await screen.findByText("导师 10")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "每页数量" }));
    fireEvent.click(screen.getByRole("option", { name: "20" }));

    await waitFor(() => {
      expect(screen.getByText("导师 11")).toBeInTheDocument();
      expect(screen.getByText("导师 12")).toBeInTheDocument();
      expect(
        screen.getByText("12 位 · 1/1 页 · 已选 0 位"),
      ).toBeInTheDocument();
    });
    expect(localStorage.getItem("home-dashboard:page-size")).toBe("20");
  });
});
