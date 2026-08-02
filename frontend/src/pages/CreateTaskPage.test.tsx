import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { IdentityDTO, LLMProfileDTO, ProfessorDashboardItemDTO } from "@/types";
import { buildBatchCreateConfirmDescription } from "@/features/create-task/client/batchCreateConfirmDescription";
import { CreateTaskPage } from "./CreateTaskPage";

const navigateMock = vi.fn();
const listProfessorsMock = vi.fn();
const createBatchTaskMock = vi.fn();
const listOutreachTemplatesMock = vi.fn();
const confirmMock = vi.fn();
const notifyMock = {
  notifyError: vi.fn(),
  notifyFormErrors: vi.fn(),
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
  outreach_generation_mode: "template",
  outreach_template_subject: "\u7533\u8bf7\u4e0e{{name}}\u8001\u5e08\u4ea4\u6d41",
  outreach_template_body_text: "{{name}}\u8001\u5e08\u60a8\u597d",
  outreach_template_body_html: "<p>{{name}}\u8001\u5e08\u60a8\u597d</p>",
  current_primary_material_id: null,
  current_primary_material: null,
  communication_group_id: null,
  match_threshold: null,
  daily_send_limit: null,
  send_interval_min: null,
  send_interval_max: null,
  same_domain_cooldown_minutes: null,
  is_default: true,
  materials: [
    {
      id: 7,
      display_name: "Portfolio.pdf",
      original_filename: "portfolio.pdf",
      mime_type: "application/pdf",
      size_bytes: 1024,
      material_type: "portfolio",
      is_primary: false,
      created_at: "2026-05-01T00:00:00",
    },
  ],
  created_at: "2026-05-01T00:00:00",
  updated_at: "2026-05-01T00:00:00",
};

const selectedLlmProfile: LLMProfileDTO = {
  id: 2,
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

const selectedProfessor: ProfessorDashboardItemDTO = {
  id: 11,
  name: "张明",
  email: "zhang@example.edu",
  title: "教授",
  university: "示例大学",
  school: "计算机学院",
  department: "人工智能系",
  research_direction: "自然语言处理",
  personal_note: null,
  recent_papers: [],
  match_score: null,
  sent_count: 0,
  status: "not_contacted",
  last_sent_at: null,
  last_replied_at: null,
  tags: [],
};

vi.mock("react-router-dom", async () => {
  const actual = await vi.importActual<typeof import("react-router-dom")>("react-router-dom");
  return {
    ...actual,
    useNavigate: () => navigateMock,
  };
});

vi.mock("@/context/NotificationContext", () => ({
  useNotification: () => notifyMock,
}));

vi.mock("@/context/SelectionContext", () => ({
  useSelectionContext: () => ({
    selectedIdentityId: selectedIdentity.id,
    selectedLlmProfileId: selectedLlmProfile.id,
    selectedIdentity,
    selectedLlmProfile,
  }),
}));

vi.mock("@/lib/useConfirmDialog", () => ({
  useConfirmDialog: () => ({
    confirm: confirmMock,
    dialog: null,
  }),
}));

vi.mock("@/lib/api/professorsApi", () => ({
  listProfessors: (...args: unknown[]) => listProfessorsMock(...args),
}));

vi.mock("@/lib/api/batchTasksApi", () => ({
  createBatchTask: (...args: unknown[]) => createBatchTaskMock(...args),
}));

vi.mock("@/lib/api/outreachTemplates", () => ({
  listOutreachTemplates: (...args: unknown[]) =>
    listOutreachTemplatesMock(...args),
}));

vi.mock("@/components/molecules/EmailTemplateEditor", () => ({
  EmailTemplateEditor: ({
    label,
    html,
    onChange,
  }: {
    label: string;
    html: string;
    onChange: (value: { html: string; text: string }) => void;
  }) => (
    <div>
      <div>{label}</div>
      <button type="button" aria-label="加粗">
        B
      </button>
      <button
        type="button"
        onClick={() =>
          onChange({
            html: "<p><strong>{{name}}</strong>老师您好</p>",
            text: "{{name}}老师您好",
          })
        }
      >
        写入 HTML 正文
      </button>
      <div role="textbox" aria-label={label} data-html={html} />
    </div>
  ),
}));

describe("CreateTaskPage", () => {
  const scrollIntoView = vi.fn();

  beforeEach(() => {
    vi.clearAllMocks();
    scrollIntoView.mockReset();
    Object.defineProperty(window.HTMLElement.prototype, "scrollIntoView", {
      configurable: true,
      value: scrollIntoView,
    });
    window.localStorage.clear();
    window.sessionStorage.setItem("selected_professor_ids", JSON.stringify([selectedProfessor.id]));
    listProfessorsMock.mockResolvedValue([selectedProfessor]);
    createBatchTaskMock.mockResolvedValue({
      id: 1,
      name: "批量任务",
    });
    listOutreachTemplatesMock.mockResolvedValue([]);
    confirmMock.mockResolvedValue(true);
  });

  it("uses the rich email editor and submits editor HTML for template batch tasks", async () => {
    render(
      <MemoryRouter>
        <CreateTaskPage />
      </MemoryRouter>,
    );

    expect(await screen.findByText("张明")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "加粗" })).toBeInTheDocument();

    const subjectEditor = screen.getByLabelText("模板主题");
    subjectEditor.textContent = "申请与{{name}}老师交流";
    fireEvent.input(subjectEditor);
    fireEvent.click(screen.getByRole("button", { name: "写入 HTML 正文" }));
    fireEvent.click(screen.getByRole("button", { name: "创建任务" }));

    await waitFor(() => expect(createBatchTaskMock).toHaveBeenCalledTimes(1));
    expect(createBatchTaskMock).toHaveBeenCalledWith(
      expect.objectContaining({
        outreach_generation_mode: "template",
        outreach_template_subject: "申请与{{name}}老师交流",
        outreach_template_body_text: "{{name}}老师您好",
        outreach_template_body_html: "<p><strong>{{name}}</strong>老师您好</p>",
        primary_material_id: null,
        outreach_template_id: null,
      }),
    );
  });

  it("uses the global template as a task-local snapshot when the identity has no default", async () => {
    listOutreachTemplatesMock.mockResolvedValue([
      {
        id: 55,
        name: "全局模板",
        recommended_generation_mode: "template",
        subject: "全局主题 {{name}}",
        body_text: "全局正文 {{name}}",
        body_html: "<p>全局正文 {{name}}</p>",
        is_ready: true,
        is_default: true,
        archived_at: null,
        created_at: "2026-05-01T00:00:00Z",
        updated_at: "2026-05-01T00:00:00Z",
      },
    ]);

    render(
      <MemoryRouter>
        <CreateTaskPage />
      </MemoryRouter>,
    );

    expect(
      await screen.findByText(/已带入“全局模板”/),
    ).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "创建任务" }));

    await waitFor(() => expect(createBatchTaskMock).toHaveBeenCalledTimes(1));
    expect(confirmMock).toHaveBeenCalledWith(
      expect.objectContaining({
        description: expect.stringContaining("发信模板：全局模板"),
      }),
    );
    expect(confirmMock).toHaveBeenCalledWith(
      expect.objectContaining({
        description: expect.stringContaining("写信方式：直接套用模板"),
      }),
    );
    expect(createBatchTaskMock).toHaveBeenCalledWith(
      expect.objectContaining({
        outreach_template_id: 55,
        outreach_generation_mode: "template",
        outreach_template_subject: "全局主题 {{name}}",
        outreach_template_body_text: "全局正文 {{name}}",
        outreach_template_body_html: "<p>全局正文 {{name}}</p>",
      }),
    );
  });

  it("does not choose an arbitrary template when no default exists", async () => {
    listOutreachTemplatesMock.mockResolvedValue([
      {
        id: 56,
        name: "普通模板",
        recommended_generation_mode: "template",
        subject: "不应自动带入的主题",
        body_text: "不应自动带入的正文",
        body_html: "<p>不应自动带入的正文</p>",
        is_ready: true,
        is_default: false,
        archived_at: null,
        created_at: "2026-05-01T00:00:00Z",
        updated_at: "2026-05-01T00:00:00Z",
      },
    ]);

    render(
      <MemoryRouter>
        <CreateTaskPage />
      </MemoryRouter>,
    );

    expect(await screen.findByText("张明")).toBeInTheDocument();
    expect(
      await screen.findByText("可直接编辑下方内容；创建后会独立保存到任务中。"),
    ).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "创建任务" }));

    await waitFor(() => expect(createBatchTaskMock).toHaveBeenCalledTimes(1));
    expect(createBatchTaskMock).toHaveBeenCalledWith(
      expect.objectContaining({
        outreach_template_id: null,
        outreach_template_subject: "申请与{{name}}老师交流",
        outreach_template_body_text: "{{name}}老师您好",
      }),
    );
  });

  it("defaults the AI reference material from the selected primary material", async () => {
    const previousPrimaryMaterial = selectedIdentity.current_primary_material;
    const previousPrimaryMaterialId = selectedIdentity.current_primary_material_id;
    const previousGenerationMode = selectedIdentity.outreach_generation_mode;
    selectedIdentity.current_primary_material = {
      id: 7,
      display_name: "Portfolio.pdf",
      original_filename: "portfolio.pdf",
      mime_type: "application/pdf",
      size_bytes: 1024,
      material_type: "portfolio",
      is_primary: true,
      created_at: "2026-05-01T00:00:00",
    };
    selectedIdentity.current_primary_material_id = 7;
    selectedIdentity.outreach_generation_mode = "llm";

    try {
      render(
        <MemoryRouter>
          <CreateTaskPage />
        </MemoryRouter>,
      );

      expect(await screen.findByText(selectedProfessor.name)).toBeInTheDocument();
      fireEvent.click(screen.getByRole("button", { name: /AI 辅助写信/ }));

      expect(screen.getByText("AI 写信参考材料")).toBeInTheDocument();
      expect(screen.getByRole("radio")).toBeChecked();
      fireEvent.click(screen.getByRole("button", { name: /创建任务/ }));

      await waitFor(() => expect(createBatchTaskMock).toHaveBeenCalledTimes(1));
      expect(createBatchTaskMock).toHaveBeenCalledWith(
        expect.objectContaining({
          outreach_generation_mode: "llm",
          primary_material_id: 7,
        }),
      );
    } finally {
      selectedIdentity.current_primary_material = previousPrimaryMaterial;
      selectedIdentity.current_primary_material_id = previousPrimaryMaterialId;
      selectedIdentity.outreach_generation_mode = previousGenerationMode;
    }
  });


  it("submits null selected materials by default for new batch tasks", async () => {
    render(
      <MemoryRouter>
        <CreateTaskPage />
      </MemoryRouter>,
    );

    expect(await screen.findByText(selectedProfessor.name)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /\u521b\u5efa\u4efb\u52a1/ }));

    await waitFor(() => expect(createBatchTaskMock).toHaveBeenCalledTimes(1));
    expect(createBatchTaskMock).toHaveBeenCalledWith(
      expect.objectContaining({
        selected_material_ids: null,
      }),
    );
  });

  it("submits user selected materials for batch tasks", async () => {
    render(
      <MemoryRouter>
        <CreateTaskPage />
      </MemoryRouter>,
    );

    expect(await screen.findByText(selectedProfessor.name)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("checkbox"));
    fireEvent.click(screen.getByRole("button", { name: /\u521b\u5efa\u4efb\u52a1/ }));

    await waitFor(() => expect(createBatchTaskMock).toHaveBeenCalledTimes(1));
    expect(createBatchTaskMock).toHaveBeenCalledWith(
      expect.objectContaining({
        selected_material_ids: [7],
      }),
    );
  });

  it("prefills resend context without carrying old schedule or llm profile", async () => {
    window.sessionStorage.setItem("selected_professor_ids", JSON.stringify([selectedProfessor.id]));
    window.sessionStorage.setItem("batch_resend_prefill_context", JSON.stringify({
      sourceTaskId: 12,
      sourceTaskName: "过期任务",
      identityId: selectedIdentity.id,
      professorIds: [selectedProfessor.id],
      defaults: {
        identity_id: selectedIdentity.id,
        outreach_generation_mode: "template",
        outreach_template_subject: "重发主题 {{name}}",
        outreach_template_body_text: "重发正文",
        outreach_template_body_html: "<p>重发正文</p>",
        primary_material_id: null,
        selected_material_ids: [7],
      },
      warnings: [],
    }));

    render(
      <MemoryRouter>
        <CreateTaskPage />
      </MemoryRouter>,
    );

    expect(await screen.findByText("张明")).toBeInTheDocument();
    expect(screen.getByText(/已从「过期任务」带入 1 位老师/)).toBeInTheDocument();
    expect(screen.getByDisplayValue("重新发起 - 过期任务")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /创建任务/ }));

    await waitFor(() => expect(createBatchTaskMock).toHaveBeenCalledTimes(1));
    expect(createBatchTaskMock).toHaveBeenCalledWith(expect.objectContaining({
      llm_profile_id: selectedLlmProfile.id,
      outreach_generation_mode: "template",
      outreach_template_subject: "重发主题 {{name}}",
      outreach_template_body_text: "重发正文",
      outreach_template_body_html: "<p>重发正文</p>",
      selected_material_ids: [7],
      schedule_type: "immediate",
      scheduled_dates: null,
      window_start_time: null,
      window_end_time: null,
      emails_per_window: null,
    }));
  });

  it("clears resend prefill context after creating task", async () => {
    const previousPrimaryMaterial = selectedIdentity.current_primary_material;
    const previousPrimaryMaterialId = selectedIdentity.current_primary_material_id;
    const previousGenerationMode = selectedIdentity.outreach_generation_mode;
    selectedIdentity.current_primary_material = {
      id: 7,
      display_name: "Portfolio.pdf",
      original_filename: "portfolio.pdf",
      mime_type: "application/pdf",
      size_bytes: 1024,
      material_type: "portfolio",
      is_primary: true,
      created_at: "2026-05-01T00:00:00",
    };
    selectedIdentity.current_primary_material_id = 7;
    selectedIdentity.outreach_generation_mode = "llm";

    try {
      window.sessionStorage.setItem("batch_resend_prefill_context", JSON.stringify({
        sourceTaskId: 12,
        sourceTaskName: "过期任务",
        identityId: selectedIdentity.id,
        professorIds: [selectedProfessor.id],
        defaults: {
          identity_id: selectedIdentity.id,
          outreach_generation_mode: "llm",
          outreach_template_subject: "AI 主题",
          outreach_template_body_text: "AI 正文",
          outreach_template_body_html: "<p>AI 正文</p>",
          primary_material_id: 7,
          selected_material_ids: [],
        },
        warnings: [],
      }));

      render(
        <MemoryRouter>
          <CreateTaskPage />
        </MemoryRouter>,
      );

      expect(await screen.findByText(selectedProfessor.name)).toBeInTheDocument();
      fireEvent.click(screen.getByRole("button", { name: /创建任务/ }));

      await waitFor(() => expect(createBatchTaskMock).toHaveBeenCalledTimes(1));
      expect(window.sessionStorage.getItem("batch_resend_prefill_context")).toBeNull();
    } finally {
      selectedIdentity.current_primary_material = previousPrimaryMaterial;
      selectedIdentity.current_primary_material_id = previousPrimaryMaterialId;
      selectedIdentity.outreach_generation_mode = previousGenerationMode;
    }
  });
  it("explains that scheduled AI rewritten drafts still need manual review", () => {
    expect(buildBatchCreateConfirmDescription("llm", "scheduled")).toContain(
      "AI 改写完成后仍需逐封审核通过",
    );
  });

  it("paginates target mentors when many professors are selected", async () => {
    const professors = Array.from({ length: 13 }, (_, index) => ({
      ...selectedProfessor,
      id: index + 1,
      name: `导师${index + 1}`,
      email: `mentor-${index + 1}@example.edu`,
    }));
    window.sessionStorage.setItem(
      "selected_professor_ids",
      JSON.stringify(professors.map((professor) => professor.id)),
    );
    listProfessorsMock.mockResolvedValue(professors);

    render(
      <MemoryRouter>
        <CreateTaskPage />
      </MemoryRouter>,
    );

    expect(await screen.findByText("导师1")).toBeInTheDocument();
    expect(screen.getByText("导师10")).toBeInTheDocument();
    expect(screen.queryByText("导师11")).not.toBeInTheDocument();
    expect(screen.getByText("1 / 2 页")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "下一页" }));

    expect(screen.queryByText("导师1")).not.toBeInTheDocument();
    expect(screen.getByText("导师11")).toBeInTheDocument();
    expect(screen.getByText("导师13")).toBeInTheDocument();
    expect(
      screen.getByRole("complementary", { name: "目标导师列表" }),
    ).toHaveFocus();
    expect(scrollIntoView).toHaveBeenCalledWith({
      behavior: "auto",
      block: "start",
    });
  });
});
