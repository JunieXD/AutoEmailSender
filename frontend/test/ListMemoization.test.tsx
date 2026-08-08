import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { HomePage } from "@/pages/HomePage";
import { ProfessorsPage } from "@/pages/ProfessorsPage";
import type {
  ProfessorDashboardItemDTO,
  ProfessorManagementItemDTO,
} from "@/types";

const getPageItemsSpy = vi.hoisted(() => vi.fn());
const listProfessors = vi.hoisted(() => vi.fn());
const listProfessorsForManagement = vi.hoisted(() => vi.fn());
const notificationMock = vi.hoisted(() => ({
  notifyError: vi.fn(),
  notifySuccess: vi.fn(),
  notifyWarning: vi.fn(),
}));
const selectionContextValue = vi.hoisted(() => ({
  identities: [],
  llmProfiles: [],
  selectedIdentityId: 1,
  selectedLlmProfileId: 2,
  selectedIdentity: {
    id: 1,
    name: "默认身份",
    profile_name: "Junie",
    sender_name: "Junie",
    email_address: "junie@example.com",
    current_primary_material_id: 10,
    outreach_template_body_text: "您好，想了解您的研究。",
    outreach_template_body_html: null,
  },
  selectedLlmProfile: {
    id: 2,
    name: "默认模型",
  },
  loading: false,
  setSelectedIdentityId: vi.fn(),
  setSelectedLlmProfileId: vi.fn(),
  refreshSelections: vi.fn(),
}));

vi.mock("@/lib/pagination", async () => {
  const actual = await vi.importActual<typeof import("@/lib/pagination")>(
    "@/lib/pagination",
  );

  return {
    ...actual,
    getPageItems: <T,>(items: T[], page: number, pageSize?: number) => {
      getPageItemsSpy(items, page, pageSize);
      return actual.getPageItems(items, page, pageSize);
    },
  };
});

vi.mock("@/context/NotificationContext", () => ({
  useNotification: () => notificationMock,
}));

vi.mock("@/app/providers/BackgroundTaskNotificationContext", () => ({
  useBackgroundTaskNotification: () => ({
    stopTrackingInformationEnrichmentJob: vi.fn(),
    trackCrawlCandidateEnrichment: vi.fn(),
    trackCrawlJob: vi.fn(),
    trackInformationEnrichmentJob: vi.fn(),
    trackMatchAnalysisJob: vi.fn(),
  }),
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

vi.mock("@/entities/professor/api/professors", () => ({
  archiveProfessor: vi.fn(),
  bulkArchiveProfessors: vi.fn(),
  bulkUpdateProfessorTags: vi.fn(),
  createProfessor: vi.fn(),
  createProfessorTag: vi.fn(),
  deleteProfessorTag: vi.fn(),
  downloadProfessorExport: vi.fn(),
  getProfessorTagUsage: vi.fn(),
  downloadProfessorTemplate: vi.fn(),
  importProfessorsFromFile: vi.fn(),
  listProfessorTags: vi.fn(async () => []),
  listProfessors,
  listProfessorsForManagement,
  restoreProfessor: vi.fn(),
  triggerCrawler: vi.fn(),
  updateProfessor: vi.fn(),
  updateProfessorNote: vi.fn(),
  updateProfessorTags: vi.fn(),
}));

vi.mock("@/lib/api/crawlJobsApi", () => ({
  createCrawlJob: vi.fn(),
}));

vi.mock("@/lib/api/emailTasksApi", () => ({
  calculateMatch: vi.fn(),
}));

vi.mock("@/lib/api/matchAnalysisJobsApi", () => ({
  createMatchAnalysisJob: vi.fn(),
}));

vi.mock("@/lib/api/workspacesApi", () => ({
  ensureWorkspaceTask: vi.fn(),
}));

const buildDashboardProfessor = (id: number): ProfessorDashboardItemDTO => ({
  id,
  name: `导师 ${id}`,
  email: `professor-${id}@example.edu`,
  title: "教授",
  university: "测试大学",
  school: "计算机学院",
  department: "人工智能系",
  research_direction: "智能系统",
  personal_note: null,
  recent_papers: [],
  match_score: null,
  sent_count: 0,
  status: "not_contacted",
  last_sent_at: null,
  last_replied_at: null,
  tags: [],
});

const buildManagementProfessor = (id: number): ProfessorManagementItemDTO => ({
  ...buildDashboardProfessor(id),
  profile_url: null,
  source_url: null,
  crawl_status: "manual",
  skip_reason: null,
  archived_at: null,
  created_at: "2026-05-01T00:00:00Z",
  updated_at: "2026-05-01T00:00:00Z",
});

const renderHomePage = () =>
  render(
    <MemoryRouter>
      <HomePage />
    </MemoryRouter>,
  );

const renderProfessorsPage = () =>
  render(
    <MemoryRouter>
      <ProfessorsPage />
    </MemoryRouter>,
  );

const flushPendingRender = () =>
  new Promise<void>((resolve) => {
    window.setTimeout(resolve, 0);
  });

describe("large list memoization", () => {
  beforeEach(() => {
    localStorage.clear();
    sessionStorage.clear();
    vi.clearAllMocks();
    listProfessors.mockResolvedValue(
      Array.from({ length: 12 }, (_, index) =>
        buildDashboardProfessor(index + 1),
      ),
    );
    listProfessorsForManagement.mockResolvedValue(
      Array.from({ length: 12 }, (_, index) =>
        buildManagementProfessor(index + 1),
      ),
    );
  });

  it("does not recalculate home page pagination when only selection changes", async () => {
    renderHomePage();

    await waitFor(() => {
      expect(listProfessors).toHaveBeenCalled();
    });
    const selectButton = await screen.findByRole("button", {
      name: "选择 导师 1",
    });
    await flushPendingRender();

    getPageItemsSpy.mockClear();
    fireEvent.click(selectButton);

    await waitFor(() => {
      expect(selectButton).toHaveAttribute("aria-pressed", "true");
    });
    expect(getPageItemsSpy).not.toHaveBeenCalled();
  });

  it("does not recalculate management page pagination when only selection changes", async () => {
    renderProfessorsPage();

    await waitFor(() => {
      expect(listProfessorsForManagement).toHaveBeenCalledWith("active");
    });
    const selectButton = await screen.findByRole("button", {
      name: "选择 导师 1",
    });
    await flushPendingRender();

    getPageItemsSpy.mockClear();
    fireEvent.click(selectButton);

    await waitFor(() => {
      expect(selectButton).toHaveAttribute("aria-pressed", "true");
    });
    expect(getPageItemsSpy).not.toHaveBeenCalled();
  });
});
