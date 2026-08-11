import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { HomePage } from "@/pages/HomePage";
import type {
  IdentityDTO,
  LLMProfileDTO,
  ProfessorDashboardItemDTO,
} from "@/types";

const listProfessors = vi.hoisted(() => vi.fn());
const listProfessorTags = vi.hoisted(() => vi.fn());
const updateProfessorNote = vi.hoisted(() => vi.fn());
const notifyError = vi.hoisted(() => vi.fn());
const notifySuccess = vi.hoisted(() => vi.fn());

vi.mock("@/app/providers/BackgroundTaskNotificationContext", () => ({
  useBackgroundTaskNotification: () => ({
    trackMatchAnalysisJob: vi.fn(),
  }),
}));

vi.mock("@/context/NotificationContext", () => ({
  useNotification: () => ({
    notifyError,
    notifySuccess,
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
    loading: false,
  }),
}));

vi.mock("@/entities/professor/api/professors", () => ({
  listProfessors,
  searchDashboardProfessors: async (payload: {
    identity_id: number;
    page: number;
    page_size: number;
  }) => {
    const allItems = await listProfessors({ identityId: payload.identity_id });
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
  listProfessorTags,
  updateProfessorNote,
  bulkUpdateProfessorTags: vi.fn(),
  createProfessorTag: vi.fn(),
  deleteProfessorTag: vi.fn(),
  getProfessorTagUsage: vi.fn(),
  updateProfessorTags: vi.fn(),
}));

vi.mock("@/lib/api/workspacesApi", () => ({
  ensureWorkspaceTask: vi.fn(),
}));

vi.mock("@/lib/api/emailTasksApi", () => ({
  calculateMatch: vi.fn(),
}));

vi.mock("@/lib/api/matchAnalysisJobsApi", () => ({
  createMatchAnalysisJob: vi.fn(),
}));

vi.mock("@/lib/useConfirmDialog", () => ({
  useConfirmDialog: () => ({
    choose: vi.fn(),
    confirm: vi.fn(),
    dialog: null,
  }),
}));

const professor: ProfessorDashboardItemDTO = {
  id: 101,
  name: "张明远",
  email: "zhang@example.edu",
  title: "教授",
  university: "测试大学",
  school: "计算机学院",
  department: null,
  research_direction: "自然语言处理",
  personal_note: "旧备注",
  recent_papers: [],
  match_score: null,
  sent_count: 0,
  status: "not_contacted",
  last_sent_at: null,
  last_replied_at: null,
  tags: [],
};

describe("HomePage professor notes", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    listProfessors.mockResolvedValue([professor]);
    listProfessorTags.mockResolvedValue([]);
    updateProfessorNote.mockResolvedValue({
      id: 101,
      personal_note: null,
      updated_at: "2026-04-24T00:00:00Z",
    });
  });

  it("clears a professor personal note from the dashboard row", async () => {
    render(
      <MemoryRouter>
        <HomePage />
      </MemoryRouter>,
    );

    const noteButton = await screen.findByRole("button", {
      name: "编辑张明远的个人备注",
    });
    fireEvent.click(noteButton);

    const textarea = screen.getByLabelText("个人备注");
    fireEvent.change(textarea, { target: { value: "" } });
    fireEvent.click(screen.getByRole("button", { name: "保存备注" }));

    await waitFor(() => {
      expect(updateProfessorNote).toHaveBeenCalledWith(101, "");
    });
    await waitFor(() => {
      expect(
        screen.queryByRole("button", { name: "编辑张明远的个人备注" }),
      ).not.toBeInTheDocument();
    });
  });
});
