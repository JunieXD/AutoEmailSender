import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "@/lib/api/client";
import { HomePage } from "@/pages/HomePage";
import type { IdentityDTO, LLMProfileDTO, ProfessorDashboardItemDTO } from "@/types";

const mockedUseSelectionContext = vi.hoisted(() => vi.fn());
const mockedListProfessors = vi.hoisted(() => vi.fn());
const mockedListProfessorTags = vi.hoisted(() => vi.fn());
const mockedCalculateMatch = vi.hoisted(() => vi.fn());
const mockedCreateMatchAnalysisJob = vi.hoisted(() => vi.fn());
const mockedEnsureWorkspaceTask = vi.hoisted(() => vi.fn());
const mockedChoose = vi.hoisted(() => vi.fn());
const mockedNotifyError = vi.hoisted(() => vi.fn());
const mockedNotifySuccess = vi.hoisted(() => vi.fn());
const mockedNotifyWarning = vi.hoisted(() => vi.fn());
const mockedTrackMatchAnalysisJob = vi.hoisted(() => vi.fn());

vi.mock("@/app/providers/BackgroundTaskNotificationContext", () => ({
  useBackgroundTaskNotification: () => ({
    trackMatchAnalysisJob: mockedTrackMatchAnalysisJob,
  }),
}));

vi.mock("@/context/SelectionContext", () => ({
  useSelectionContext: mockedUseSelectionContext,
}));

vi.mock("@/entities/professor/api/professors", () => ({
  listProfessors: mockedListProfessors,
  listProfessorTags: mockedListProfessorTags,
}));

vi.mock("@/lib/api/emailTasksApi", () => ({
  calculateMatch: mockedCalculateMatch,
}));

vi.mock("@/lib/api/matchAnalysisJobsApi", () => ({
  createMatchAnalysisJob: mockedCreateMatchAnalysisJob,
}));

vi.mock("@/lib/api/workspacesApi", () => ({
  ensureWorkspaceTask: mockedEnsureWorkspaceTask,
}));

vi.mock("@/lib/useConfirmDialog", () => ({
  useConfirmDialog: () => ({
    choose: mockedChoose,
    confirm: vi.fn(),
    dialog: null,
  }),
}));

vi.mock("@/context/NotificationContext", () => ({
  useNotification: () => ({
    notifyError: mockedNotifyError,
    notifySuccess: mockedNotifySuccess,
    notifyWarning: mockedNotifyWarning,
  }),
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
  outreach_template_subject: "申请与{{name}}老师交流",
  outreach_template_body_text: "老师您好，我是{{sender_name}}。",
  outreach_template_body_html: "",
  current_primary_material_id: 11,
  current_primary_material: null,
  communication_group_id: null,
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

const createProfessor = (
  id: number,
  name: string,
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
  recent_papers: ["Paper A"],
  match_score: null,
  sent_count: 0,
  status: "preparing",
  ...overrides,
});

const renderPage = () =>
  render(
    <MemoryRouter>
      <HomePage />
    </MemoryRouter>,
  );

describe("HomePage match analysis", () => {
  beforeEach(() => {
    mockedListProfessors.mockReset();
    mockedListProfessorTags.mockReset();
    mockedCalculateMatch.mockReset();
    mockedCreateMatchAnalysisJob.mockReset();
    mockedEnsureWorkspaceTask.mockReset();
    mockedChoose.mockReset();
    mockedNotifyError.mockReset();
    mockedNotifySuccess.mockReset();
    mockedNotifyWarning.mockReset();
    mockedTrackMatchAnalysisJob.mockReset();
    mockedUseSelectionContext.mockReturnValue({
      selectedIdentityId: 1,
      selectedLlmProfileId: 1,
      selectedIdentity: createIdentity(),
      selectedLlmProfile,
    });
    mockedListProfessors.mockResolvedValue([
      createProfessor(101, "王教授"),
      createProfessor(102, "李教授"),
    ]);
    mockedListProfessorTags.mockResolvedValue([]);
    mockedEnsureWorkspaceTask.mockImplementation(async (professorId: number) => ({
      current_task: { id: professorId + 1000 },
    }));
    mockedCreateMatchAnalysisJob.mockResolvedValue({
      id: 1,
      target_count: 2,
    });
  });

  it("shows a warning when a single match request returns 409", async () => {
    mockedCalculateMatch.mockRejectedValue(new ApiError(409, "该任务正在分析中"));

    renderPage();

    const buttons = await screen.findAllByRole("button", { name: "分析匹配度" });
    fireEvent.click(buttons[0]);

    await waitFor(() => {
      expect(mockedNotifyWarning).toHaveBeenCalledWith(
        "匹配分析进行中",
        "该任务正在分析中，请稍后刷新结果。",
      );
    });
    expect(mockedNotifyError).not.toHaveBeenCalled();
  });

  it("skips a single scored professor when the user chooses skip", async () => {
    mockedListProfessors.mockResolvedValue([
      createProfessor(101, "王教授", { match_score: 88 }),
    ]);
    mockedChoose.mockResolvedValue("secondary");

    renderPage();

    fireEvent.click(await screen.findByRole("button", { name: "分析匹配度" }));

    await waitFor(() => {
      expect(mockedNotifyWarning).toHaveBeenCalledWith(
        "已跳过匹配分析",
        "该导师已有匹配分，未重新计算。",
      );
    });
    expect(mockedEnsureWorkspaceTask).not.toHaveBeenCalled();
    expect(mockedCalculateMatch).not.toHaveBeenCalled();
  });

  it("recalculates a single scored professor when the user chooses recalculate", async () => {
    mockedListProfessors.mockResolvedValue([
      createProfessor(101, "王教授", { match_score: 88 }),
    ]);
    mockedChoose.mockResolvedValue("confirm");
    mockedCalculateMatch.mockResolvedValue({
      thread: {} as never,
      usage: {
        prompt_tokens: 10,
        completion_tokens: 2,
        total_tokens: 12,
        cached_tokens: 0,
      },
      run_id: 1,
    });

    renderPage();

    fireEvent.click(await screen.findByRole("button", { name: "分析匹配度" }));

    await waitFor(() => {
      expect(mockedCalculateMatch).toHaveBeenCalledWith(1101, 1);
    });
  });

  it("creates a batch match analysis job for selected professors", async () => {
    renderPage();

    fireEvent.click(await screen.findByRole("button", { name: "选择 王教授" }));
    fireEvent.click(screen.getByRole("button", { name: "选择 李教授" }));
    fireEvent.click(screen.getByRole("button", { name: "批量分析匹配度" }));

    await waitFor(() => {
      expect(mockedCreateMatchAnalysisJob).toHaveBeenCalledWith({
        identity_id: 1,
        llm_profile_id: 1,
        professor_ids: [101, 102],
        name: null,
      });
    });
    expect(mockedTrackMatchAnalysisJob).toHaveBeenCalledWith({
      id: 1,
      target_count: 2,
    });
  });

  it("excludes scored professors from batch analysis when the user chooses skip", async () => {
    mockedListProfessors.mockResolvedValue([
      createProfessor(101, "王教授", { match_score: 88 }),
      createProfessor(102, "李教授"),
    ]);
    mockedChoose.mockResolvedValue("secondary");
    mockedCreateMatchAnalysisJob.mockResolvedValue({
      id: 1,
      target_count: 1,
    });

    renderPage();

    fireEvent.click(await screen.findByRole("button", { name: "选择 王教授" }));
    fireEvent.click(screen.getByRole("button", { name: "选择 李教授" }));
    fireEvent.click(screen.getByRole("button", { name: "批量分析匹配度" }));

    await waitFor(() => {
      expect(mockedCreateMatchAnalysisJob).toHaveBeenCalledWith({
        identity_id: 1,
        llm_profile_id: 1,
        professor_ids: [102],
        name: null,
      });
    });
  });

  it("does not create a batch job when every selected scored professor is skipped", async () => {
    mockedListProfessors.mockResolvedValue([
      createProfessor(101, "王教授", { match_score: 88 }),
      createProfessor(102, "李教授", { match_score: 77 }),
    ]);
    mockedChoose.mockResolvedValue("secondary");

    renderPage();

    fireEvent.click(await screen.findByRole("button", { name: "选择 王教授" }));
    fireEvent.click(screen.getByRole("button", { name: "选择 李教授" }));
    fireEvent.click(screen.getByRole("button", { name: "批量分析匹配度" }));

    await waitFor(() => {
      expect(mockedNotifyWarning).toHaveBeenCalledWith(
        "没有需要分析的导师",
        "已选导师都已有匹配分，本次已按你的选择跳过。",
      );
    });
    expect(mockedCreateMatchAnalysisJob).not.toHaveBeenCalled();
  });

  it("recalculates scored professors in a batch job when the user chooses recalculate", async () => {
    mockedListProfessors.mockResolvedValue([
      createProfessor(101, "王教授", { match_score: 88 }),
      createProfessor(102, "李教授"),
    ]);
    mockedChoose.mockResolvedValue("confirm");

    renderPage();

    fireEvent.click(await screen.findByRole("button", { name: "选择 王教授" }));
    fireEvent.click(screen.getByRole("button", { name: "选择 李教授" }));
    fireEvent.click(screen.getByRole("button", { name: "批量分析匹配度" }));

    await waitFor(() => {
      expect(mockedCreateMatchAnalysisJob).toHaveBeenCalledWith({
        identity_id: 1,
        llm_profile_id: 1,
        professor_ids: [101, 102],
        name: null,
      });
    });
  });

  it("reports a successful single recalculation", async () => {
    mockedCalculateMatch.mockResolvedValue({
        thread: {} as never,
        usage: {
          prompt_tokens: 10,
          completion_tokens: 2,
          total_tokens: 12,
          cached_tokens: 0,
        },
        run_id: 1,
      });

    renderPage();

    const buttons = await screen.findAllByRole("button", { name: "分析匹配度" });
    fireEvent.click(buttons[0]);

    await waitFor(() => {
      expect(mockedNotifySuccess).toHaveBeenCalledWith(
        "匹配分析完成",
        expect.stringContaining("总计 12"),
      );
    });
  });

  it("checks the configured shared source material before analyzing", async () => {
    const activeIdentity = createIdentity({ communication_group_id: 7 });
    const sourceIdentity = createIdentity({
      id: 2,
      name: "申请身份 A",
      profile_name: "申请身份 A",
      email_address: "source@example.com",
      smtp_username: "source@example.com",
      current_primary_material_id: null,
      communication_group_id: 7,
      is_default: false,
    });
    mockedUseSelectionContext.mockReturnValue({
      selectedIdentityId: activeIdentity.id,
      selectedLlmProfileId: 1,
      selectedIdentity: activeIdentity,
      selectedLlmProfile,
      matchSourceIdentity: sourceIdentity,
      matchSourceIdentityId: sourceIdentity.id,
      matchUsesGroupSource: true,
      matchScopeKey: `${activeIdentity.id}:${sourceIdentity.id}`,
    });
    renderPage();

    expect(
      await screen.findByText("组内匹配统一依据 申请身份 A"),
    ).toBeInTheDocument();
    fireEvent.click(screen.getAllByRole("button", { name: "分析匹配度" })[0]);

    await waitFor(() => {
      expect(mockedNotifyWarning).toHaveBeenCalledWith(
        "匹配依据身份缺少默认材料",
        "申请身份 A 尚未设置默认材料，请先到个人页补充。",
      );
    });
    expect(mockedEnsureWorkspaceTask).not.toHaveBeenCalled();
    expect(mockedCalculateMatch).not.toHaveBeenCalled();
  });
});
