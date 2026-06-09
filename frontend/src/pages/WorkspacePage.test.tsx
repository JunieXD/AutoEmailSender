import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import "@testing-library/jest-dom/vitest";
import { createMemoryRouter, RouterProvider } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { WorkspaceThreadDTO } from "@/types";
import { WorkspacePage } from "./WorkspacePage";

const apiMocks = vi.hoisted(() => ({
  getWorkspaceThread: vi.fn(),
  refreshWorkspaceReplies: vi.fn(),
  saveDraft: vi.fn(),
  rewriteDraft: vi.fn(),
  calculateMatch: vi.fn(),
  updateTaskOutreachConfig: vi.fn(),
  continueManually: vi.fn(),
  startFollowUp: vi.fn(),
  approveAndSend: vi.fn(),
  approveAndSchedule: vi.fn(),
  cancelScheduledTask: vi.fn(),
}));

const notificationMocks = vi.hoisted(() => ({
  notifyError: vi.fn(),
  notifyFormErrors: vi.fn(),
  notifySuccess: vi.fn(),
}));

const selectionMock = vi.hoisted(() => ({
  selectedIdentityId: 1 as number | null,
  selectedLlmProfileId: 2 as number | null,
}));

const draftGuardMock = vi.hoisted(() => ({
  guard: null as null | (() => Promise<boolean>),
  requestWorkspaceDraftGuard: vi.fn(async () => {
    if (!draftGuardMock.guard) {
      return true;
    }
    return draftGuardMock.guard();
  }),
  registerWorkspaceDraftGuard: vi.fn((guard: () => Promise<boolean>) => {
    draftGuardMock.guard = guard;
    return () => {
      if (draftGuardMock.guard === guard) {
        draftGuardMock.guard = null;
      }
    };
  }),
}));

vi.mock("@/context/SelectionContext", () => ({
  useSelectionContext: () => selectionMock,
}));

vi.mock("@/context/NotificationContext", () => ({
  useNotification: () => notificationMocks,
}));

vi.mock("@/context/useWorkspaceDraftGuard", () => ({
  useWorkspaceDraftGuard: () => draftGuardMock,
}));

vi.mock("@/lib/api/workspacesApi", () => ({
  getWorkspaceThread: apiMocks.getWorkspaceThread,
  refreshWorkspaceReplies: apiMocks.refreshWorkspaceReplies,
}));

vi.mock("@/lib/api/emailTasksApi", () => ({
  saveDraft: apiMocks.saveDraft,
  rewriteDraft: apiMocks.rewriteDraft,
  calculateMatch: apiMocks.calculateMatch,
  updateTaskOutreachConfig: apiMocks.updateTaskOutreachConfig,
  continueManually: apiMocks.continueManually,
  startFollowUp: apiMocks.startFollowUp,
  approveAndSend: apiMocks.approveAndSend,
  approveAndSchedule: apiMocks.approveAndSchedule,
  cancelScheduledTask: apiMocks.cancelScheduledTask,
}));

vi.mock("@/components/molecules/EmailTemplateEditor", () => ({
  EmailTemplateEditor: ({
    label,
    html,
    disabled,
    onChange,
  }: {
    label: string;
    html: string;
    disabled?: boolean;
    onChange: (value: { html: string; text: string }) => void;
  }) => (
    <textarea
      aria-label={label}
      value={html}
      disabled={disabled}
      onChange={(event) =>
        onChange({
          html: event.currentTarget.value,
          text: event.currentTarget.value.replace(/<[^>]+>/g, ""),
        })
      }
    />
  ),
}));

vi.mock("@/components/molecules/SubjectTemplateInput", () => ({
  SubjectTemplateInput: ({
    label,
    value,
    disabled,
    onChange,
  }: {
    label: string;
    value: string;
    disabled?: boolean;
    onChange: (value: string) => void;
  }) => (
    <input
      aria-label={label}
      value={value}
      disabled={disabled}
      onChange={(event) => onChange(event.currentTarget.value)}
    />
  ),
}));

const buildWorkspaceThread = (
  overrides: Partial<WorkspaceThreadDTO> = {},
): WorkspaceThreadDTO => ({
  professor: {
    id: 21,
    name: "保存草稿导师",
    email: "mentor@example.edu",
    title: "Professor",
    university: "Example University",
    school: "School of Computing",
    research_direction: "Agents",
    recent_papers: [],
  },
  identity: {
    id: 1,
    name: "默认身份",
    profile_name: "申请人",
    sender_name: "小明",
    email_address: "student@example.com",
  },
  llm_profile: {
    id: 2,
    name: "默认模型",
    provider: "openai",
    model_name: "gpt-test",
  },
  material_options: [],
  current_task: {
    id: 101,
    source: "manual",
    batch_task_id: null,
    parent_task_id: null,
    status: "review_required",
    cancellation_reason: null,
    can_continue_manually: false,
    can_write_follow_up: false,
    outreach_generation_mode: "llm",
    outreach_template_subject: "模板主题",
    outreach_template_body_text: "模板正文",
    outreach_template_body_html: "<p>模板正文</p>",
    rendered_template_subject: null,
    rendered_template_body_text: null,
    rendered_template_body_html: null,
    match_score: 80,
    match_reason: "匹配",
    fit_points: [],
    risk_points: [],
    match_keywords: [],
    generated_subject: "AI 原始主题",
    generated_content_text: "AI 原始正文",
    generated_content_html: "<p>AI 原始正文</p>",
    approved_subject: null,
    approved_body_text: null,
    approved_body_html: null,
    primary_material_id: null,
    primary_material: null,
    selected_material_ids: [],
    approved_at: null,
    scheduled_at: null,
    last_send_attempt_at: null,
    sent_at: null,
    last_rfc_message_id: null,
    retry_count: 0,
    last_error: null,
    is_replied: false,
    estimated_prompt_tokens: null,
    estimated_completion_tokens_upper_bound: null,
    estimated_total_tokens_upper_bound: null,
    last_draft_prompt_tokens: null,
    last_draft_completion_tokens: null,
    last_draft_total_tokens: null,
    draft: {
      subject: "AI 原始主题",
      body_text: "AI 原始正文",
      body_html: "<p>AI 原始正文</p>",
      source: "ai_rewrite",
      sendable: true,
      editable: true,
    },
  },
  messages: [],
  ...overrides,
});

const renderWorkspace = () => {
  const router = createMemoryRouter(
    [
      { path: "/", element: <div>首页</div> },
      { path: "/workspace/:id", element: <WorkspacePage /> },
    ],
    { initialEntries: ["/workspace/21"] },
  );
  render(<RouterProvider router={router} />);
  return router;
};

beforeEach(() => {
  vi.clearAllMocks();
  draftGuardMock.guard = null;
  selectionMock.selectedIdentityId = 1;
  selectionMock.selectedLlmProfileId = 2;
  apiMocks.getWorkspaceThread.mockResolvedValue(buildWorkspaceThread());
  apiMocks.refreshWorkspaceReplies.mockResolvedValue(buildWorkspaceThread());
  apiMocks.saveDraft.mockResolvedValue(
    buildWorkspaceThread({
      current_task: {
        ...buildWorkspaceThread().current_task,
        approved_subject: "用户编辑主题",
        approved_body_text: "用户编辑正文",
        approved_body_html: "<p>用户编辑正文</p>",
        approved_at: "2026-06-01T00:00:00",
      },
    }),
  );
});

describe("WorkspacePage draft saving", () => {
  it("keeps the last draft visible while generation is in progress", async () => {
    apiMocks.getWorkspaceThread.mockResolvedValueOnce(
      buildWorkspaceThread({
        current_task: {
          ...buildWorkspaceThread().current_task,
          status: "generating_draft",
          generated_subject: "生成前主题",
          generated_content_text: "生成前正文",
          generated_content_html: "<p>生成前正文</p>",
          draft: {
            subject: "生成前主题",
            body_text: "生成前正文",
            body_html: "<p>生成前正文</p>",
            source: "rewrite_source",
            sendable: false,
            editable: false,
          },
        },
      }),
    );

    renderWorkspace();

    fireEvent.click(await screen.findByRole("button", { name: "写信" }));
    expect(screen.getByLabelText("邮件主题")).toHaveValue("生成前主题");
    expect(screen.getByLabelText("邮件正文")).toHaveValue("<p>生成前正文</p>");
    expect(screen.getByLabelText("邮件主题")).toBeDisabled();
    expect(screen.getByLabelText("邮件正文")).toBeDisabled();
  });

  it("saves edited workspace draft without sending", async () => {
    renderWorkspace();

    fireEvent.click(await screen.findByRole("button", { name: "编辑草稿" }));
    fireEvent.change(screen.getByLabelText("邮件主题"), {
      target: { value: "用户编辑主题" },
    });
    fireEvent.change(screen.getByLabelText("邮件正文"), {
      target: { value: "<p>用户编辑正文</p>" },
    });
    fireEvent.click(screen.getByRole("button", { name: "保存草稿" }));

    await waitFor(() => {
      expect(apiMocks.saveDraft).toHaveBeenCalledWith(101, {
        subject: "用户编辑主题",
        body_text: "用户编辑正文",
        body_html: "<p>用户编辑正文</p>",
        selected_material_ids: [],
      });
    });
    expect(apiMocks.approveAndSend).not.toHaveBeenCalled();
    expect(notificationMocks.notifySuccess).toHaveBeenCalledWith(
      "草稿已保存",
      "工作区草稿已更新。",
    );
  });

  it("allows saving a dirty draft even when the body is not sendable", async () => {
    renderWorkspace();

    fireEvent.click(await screen.findByRole("button", { name: "编辑草稿" }));
    fireEvent.change(screen.getByLabelText("邮件主题"), {
      target: { value: "只保存主题" },
    });
    fireEvent.change(screen.getByLabelText("邮件正文"), {
      target: { value: "" },
    });
    const saveButton = screen.getByRole("button", { name: "保存草稿" });

    expect(saveButton).not.toBeDisabled();
    fireEvent.click(saveButton);

    await waitFor(() => {
      expect(apiMocks.saveDraft).toHaveBeenCalledWith(101, {
        subject: "只保存主题",
        body_text: "",
        body_html: "",
        selected_material_ids: [],
      });
    });
  });

  it("sends an empty subject when saving a draft after the user clears it", async () => {
    renderWorkspace();

    fireEvent.click(await screen.findByRole("button", { name: "编辑草稿" }));
    fireEvent.change(screen.getByLabelText("邮件主题"), {
      target: { value: "" },
    });
    fireEvent.click(screen.getByRole("button", { name: "保存草稿" }));

    await waitFor(() => {
      expect(apiMocks.saveDraft).toHaveBeenCalledWith(101, {
        subject: "",
        body_text: "AI 原始正文",
        body_html: "<p>AI 原始正文</p>",
        selected_material_ids: [],
      });
    });
  });

  it("keeps template-mode saved edits visible after they differ from the rendered template", async () => {
    const templateThread = buildWorkspaceThread({
      current_task: {
        ...buildWorkspaceThread().current_task,
        outreach_generation_mode: "template",
        generated_subject: null,
        generated_content_text: null,
        generated_content_html: null,
        rendered_template_subject: "模板渲染主题",
        rendered_template_body_text: "模板渲染正文",
        rendered_template_body_html: "<p>模板渲染正文</p>",
        draft: {
          subject: "模板渲染主题",
          body_text: "模板渲染正文",
          body_html: "<p>模板渲染正文</p>",
          source: "template",
          sendable: true,
          editable: true,
        },
      },
    });
    apiMocks.getWorkspaceThread.mockResolvedValueOnce(templateThread);
    apiMocks.saveDraft.mockResolvedValueOnce(
      buildWorkspaceThread({
        current_task: {
          ...templateThread.current_task,
          approved_subject: "用户改过的模板主题",
          approved_body_text: "用户改过的模板正文",
          approved_body_html: "<p>用户改过的模板正文</p>",
          approved_at: "2026-06-01T00:00:00",
          draft: {
            subject: "用户改过的模板主题",
            body_text: "用户改过的模板正文",
            body_html: "<p>用户改过的模板正文</p>",
            source: "saved",
            sendable: true,
            editable: true,
          },
        },
      }),
    );
    renderWorkspace();

    fireEvent.click(await screen.findByRole("button", { name: "编辑草稿" }));
    expect(screen.getByLabelText("邮件主题")).toHaveValue("模板渲染主题");
    fireEvent.change(screen.getByLabelText("邮件主题"), {
      target: { value: "用户改过的模板主题" },
    });
    fireEvent.change(screen.getByLabelText("邮件正文"), {
      target: { value: "<p>用户改过的模板正文</p>" },
    });
    fireEvent.click(screen.getByRole("button", { name: "保存草稿" }));

    await waitFor(() => {
      expect(apiMocks.saveDraft).toHaveBeenCalledWith(101, {
        subject: "用户改过的模板主题",
        body_text: "用户改过的模板正文",
        body_html: "<p>用户改过的模板正文</p>",
        selected_material_ids: [],
      });
      expect(screen.getByLabelText("邮件主题")).toHaveValue("用户改过的模板主题");
      expect(screen.getByLabelText("邮件正文")).toHaveValue("<p>用户改过的模板正文</p>");
    });
  });

  it("prompts to save dirty draft before navigating away", async () => {
    const router = renderWorkspace();

    fireEvent.click(await screen.findByRole("button", { name: "编辑草稿" }));
    fireEvent.change(screen.getByLabelText("邮件主题"), {
      target: { value: "离开前保存主题" },
    });
    await router.navigate("/");

    expect(await screen.findByText("保存草稿修改？")).toBeInTheDocument();
    expect(screen.queryByText("首页")).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "保存并离开" }));

    await waitFor(() => {
      expect(apiMocks.saveDraft).toHaveBeenCalledWith(101, expect.objectContaining({
        subject: "离开前保存主题",
      }));
    });
    await waitFor(() => {
      expect(screen.getByText("首页")).toBeInTheDocument();
    });
  });

  it("locks dirty draft while saving from the exit confirmation", async () => {
    let resolveSaveDraft: (value: WorkspaceThreadDTO) => void = () => undefined;
    apiMocks.saveDraft.mockReturnValueOnce(
      new Promise<WorkspaceThreadDTO>((resolve) => {
        resolveSaveDraft = resolve;
      }),
    );
    const router = renderWorkspace();

    fireEvent.click(await screen.findByRole("button", { name: "编辑草稿" }));
    fireEvent.change(screen.getByLabelText("邮件主题"), {
      target: { value: "离开保存中的主题" },
    });
    await router.navigate("/");
    expect(await screen.findByText("保存草稿修改？")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "保存并离开" }));

    await waitFor(() => {
      expect(apiMocks.saveDraft).toHaveBeenCalledWith(101, expect.objectContaining({
        subject: "离开保存中的主题",
      }));
    });
    expect(screen.getByLabelText("邮件主题")).toBeDisabled();
    await expect(draftGuardMock.requestWorkspaceDraftGuard()).resolves.toBe(false);

    resolveSaveDraft(
      buildWorkspaceThread({
        current_task: {
          ...buildWorkspaceThread().current_task,
          approved_subject: "离开保存中的主题",
          approved_body_text: "AI 原始正文",
          approved_body_html: "<p>AI 原始正文</p>",
          approved_at: "2026-06-01T00:00:00",
        },
      }),
    );
    await waitFor(() => {
      expect(screen.getByText("首页")).toBeInTheDocument();
    });
  });

  it("prompts to save dirty draft before switching identity or model", async () => {
    renderWorkspace();

    fireEvent.click(await screen.findByRole("button", { name: "编辑草稿" }));
    fireEvent.change(screen.getByLabelText("邮件主题"), {
      target: { value: "切换前保存主题" },
    });

    expect(draftGuardMock.registerWorkspaceDraftGuard).toHaveBeenCalled();
    const canceledSwitch = draftGuardMock.requestWorkspaceDraftGuard();
    expect(await screen.findByText("保存草稿修改？")).toBeInTheDocument();
    expect(apiMocks.saveDraft).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "继续编辑" }));
    await expect(canceledSwitch).resolves.toBe(false);

    const confirmedSwitch = draftGuardMock.requestWorkspaceDraftGuard();
    expect(await screen.findByText("保存草稿修改？")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "保存并离开" }));

    await waitFor(() => {
      expect(apiMocks.saveDraft).toHaveBeenCalledWith(101, expect.objectContaining({
        subject: "切换前保存主题",
      }));
    });
    await expect(confirmedSwitch).resolves.toBe(true);
  });

  it("locks dirty draft while saving so switching cannot discard in-flight changes", async () => {
    let resolveSaveDraft: (value: WorkspaceThreadDTO) => void = () => undefined;
    apiMocks.saveDraft.mockReturnValueOnce(
      new Promise<WorkspaceThreadDTO>((resolve) => {
        resolveSaveDraft = resolve;
      }),
    );
    renderWorkspace();

    fireEvent.click(await screen.findByRole("button", { name: "编辑草稿" }));
    fireEvent.change(screen.getByLabelText("邮件主题"), {
      target: { value: "保存中的主题" },
    });
    fireEvent.click(screen.getByRole("button", { name: "保存草稿" }));

    await waitFor(() => {
      expect(apiMocks.saveDraft).toHaveBeenCalledWith(101, expect.objectContaining({
        subject: "保存中的主题",
      }));
    });
    expect(screen.getByLabelText("邮件主题")).toBeDisabled();
    await expect(draftGuardMock.requestWorkspaceDraftGuard()).resolves.toBe(false);

    resolveSaveDraft(
      buildWorkspaceThread({
        current_task: {
          ...buildWorkspaceThread().current_task,
          approved_subject: "保存中的主题",
          approved_body_text: "AI 原始正文",
          approved_body_html: "<p>AI 原始正文</p>",
          approved_at: "2026-06-01T00:00:00",
        },
      }),
    );
    await waitFor(() => {
      expect(notificationMocks.notifySuccess).toHaveBeenCalledWith(
        "草稿已保存",
        "工作区草稿已更新。",
      );
      expect(screen.getByLabelText("邮件主题")).not.toBeDisabled();
    });
  });

  it("blocks browser unload while a dirty draft save is still in flight", async () => {
    let resolveSaveDraft: (value: WorkspaceThreadDTO) => void = () => undefined;
    apiMocks.saveDraft.mockReturnValueOnce(
      new Promise<WorkspaceThreadDTO>((resolve) => {
        resolveSaveDraft = resolve;
      }),
    );
    renderWorkspace();

    fireEvent.click(await screen.findByRole("button", { name: "编辑草稿" }));
    fireEvent.change(screen.getByLabelText("邮件主题"), {
      target: { value: "导航保存中的主题" },
    });
    fireEvent.click(screen.getByRole("button", { name: "保存草稿" }));

    await waitFor(() => {
      expect(apiMocks.saveDraft).toHaveBeenCalledWith(101, expect.objectContaining({
        subject: "导航保存中的主题",
      }));
    });
    await waitFor(() => {
      expect(screen.getByLabelText("邮件主题")).toBeDisabled();
    });

    const event = new Event("beforeunload", { cancelable: true });
    window.dispatchEvent(event);

    expect(event.defaultPrevented).toBe(true);

    resolveSaveDraft(
      buildWorkspaceThread({
        current_task: {
          ...buildWorkspaceThread().current_task,
          approved_subject: "导航保存中的主题",
          approved_body_text: "AI 原始正文",
          approved_body_html: "<p>AI 原始正文</p>",
          approved_at: "2026-06-01T00:00:00",
        },
      }),
    );
    await waitFor(() => {
      expect(screen.getByLabelText("邮件主题")).not.toBeDisabled();
    });
  });

  it("keeps unsaved draft edits when a non-draft action refreshes the thread", async () => {
    apiMocks.getWorkspaceThread.mockResolvedValueOnce(
      buildWorkspaceThread({
        current_task: {
          ...buildWorkspaceThread().current_task,
          primary_material_id: 7,
          primary_material: {
            id: 7,
            display_name: "resume.txt",
            original_filename: "resume.txt",
            mime_type: "text/plain",
            size_bytes: 128,
            material_type: "resume",
            is_primary: true,
            created_at: "2026-06-01T00:00:00",
          },
        },
        professor: {
          ...buildWorkspaceThread().professor,
          recent_papers: ["Paper"],
        },
      }),
    );
    apiMocks.calculateMatch.mockResolvedValueOnce({
      run_id: 501,
      thread: buildWorkspaceThread({
        current_task: {
          ...buildWorkspaceThread().current_task,
          primary_material_id: 7,
          primary_material: {
            id: 7,
            display_name: "resume.txt",
            original_filename: "resume.txt",
            mime_type: "text/plain",
            size_bytes: 128,
            material_type: "resume",
            is_primary: true,
            created_at: "2026-06-01T00:00:00",
          },
          match_score: 95,
          match_reason: "刷新后的匹配结果",
          generated_subject: "服务端旧主题",
          generated_content_text: "服务端旧正文",
          generated_content_html: "<p>服务端旧正文</p>",
        },
        professor: {
          ...buildWorkspaceThread().professor,
          recent_papers: ["Paper"],
        },
      }),
    });
    renderWorkspace();

    fireEvent.click(await screen.findByRole("button", { name: "编辑草稿" }));
    fireEvent.change(screen.getByLabelText("邮件主题"), {
      target: { value: "未保存的本地主题" },
    });
    fireEvent.change(screen.getByLabelText("邮件正文"), {
      target: { value: "<p>未保存的本地正文</p>" },
    });
    fireEvent.click(screen.getByRole("button", { name: "分析匹配度" }));

    await waitFor(() => {
      expect(apiMocks.calculateMatch).toHaveBeenCalledWith(101, 2);
    });
    expect(screen.getByLabelText("邮件主题")).toHaveValue("未保存的本地主题");
    expect(screen.getByLabelText("邮件正文")).toHaveValue("<p>未保存的本地正文</p>");
  });

  it("sends the current editor content when rewriting the draft", async () => {
    apiMocks.getWorkspaceThread.mockResolvedValueOnce(
      buildWorkspaceThread({
        current_task: {
          ...buildWorkspaceThread().current_task,
          primary_material_id: 7,
          primary_material: {
            id: 7,
            display_name: "resume.txt",
            original_filename: "resume.txt",
            mime_type: "text/plain",
            size_bytes: 128,
            material_type: "resume",
            is_primary: true,
            created_at: "2026-06-01T00:00:00",
          },
        },
        professor: {
          ...buildWorkspaceThread().professor,
          recent_papers: ["Paper"],
        },
      }),
    );
    apiMocks.rewriteDraft.mockResolvedValueOnce(
      buildWorkspaceThread({
        current_task: {
          ...buildWorkspaceThread().current_task,
          primary_material_id: 7,
          primary_material: {
            id: 7,
            display_name: "resume.txt",
            original_filename: "resume.txt",
            mime_type: "text/plain",
            size_bytes: 128,
            material_type: "resume",
            is_primary: true,
            created_at: "2026-06-01T00:00:00",
          },
          generated_subject: "新生成主题",
          generated_content_text: "新生成正文",
          generated_content_html: "<p>新生成正文</p>",
          draft: {
            subject: "新生成主题",
            body_text: "新生成正文",
            body_html: "<p>新生成正文</p>",
            source: "ai_rewrite",
            sendable: true,
            editable: true,
          },
        },
        professor: {
          ...buildWorkspaceThread().professor,
          recent_papers: ["Paper"],
        },
      }),
    );
    renderWorkspace();

    fireEvent.click(await screen.findByRole("button", { name: "编辑草稿" }));
    fireEvent.change(screen.getByLabelText("邮件主题"), {
      target: { value: "未保存旧主题" },
    });
    fireEvent.change(screen.getByLabelText("邮件正文"), {
      target: { value: "<p>未保存旧正文</p>" },
    });
    fireEvent.click(screen.getByRole("button", { name: "生成草稿" }));

    await waitFor(() => {
      expect(apiMocks.rewriteDraft).toHaveBeenCalledWith(101, {
        subject: "未保存旧主题",
        body_text: "未保存旧正文",
        body_html: "<p>未保存旧正文</p>",
        selected_material_ids: [],
        llm_profile_id: 2,
      });
    });
    expect(screen.getByLabelText("邮件主题")).toHaveValue("新生成主题");
    expect(screen.getByLabelText("邮件正文")).toHaveValue("<p>新生成正文</p>");
  });

  it("does not carry parent unsaved edits into a newly created follow-up task", async () => {
    apiMocks.getWorkspaceThread.mockResolvedValueOnce(
      buildWorkspaceThread({
        current_task: {
          ...buildWorkspaceThread().current_task,
          id: 201,
          status: "sent",
          sent_at: "2026-06-01T01:00:00",
          can_write_follow_up: true,
          generated_subject: null,
          generated_content_text: null,
          generated_content_html: null,
          approved_subject: "已发送父任务主题",
          approved_body_text: "已发送父任务正文",
          approved_body_html: "<p>已发送父任务正文</p>",
          draft: {
            subject: null,
            body_text: "",
            body_html: null,
            source: "manual_empty",
            sendable: false,
            editable: false,
          },
        },
      }),
    );
    apiMocks.startFollowUp.mockResolvedValueOnce(
      buildWorkspaceThread({
        current_task: {
          ...buildWorkspaceThread().current_task,
          id: 202,
          parent_task_id: 201,
          status: "matched",
          generated_subject: "跟进任务主题",
          generated_content_text: "跟进任务正文",
          generated_content_html: "<p>跟进任务正文</p>",
          draft: {
            subject: "跟进任务主题",
            body_text: "跟进任务正文",
            body_html: "<p>跟进任务正文</p>",
            source: "ai_rewrite",
            sendable: true,
            editable: true,
          },
        },
      }),
    );
    renderWorkspace();

    fireEvent.click(await screen.findByRole("button", { name: "写信" }));
    fireEvent.change(screen.getByLabelText("邮件主题"), {
      target: { value: "父任务未保存本地主题" },
    });
    fireEvent.change(screen.getByLabelText("邮件正文"), {
      target: { value: "<p>父任务未保存本地正文</p>" },
    });
    fireEvent.click(screen.getByRole("button", { name: "写跟进邮件" }));

    await waitFor(() => {
      expect(apiMocks.startFollowUp).toHaveBeenCalledWith(201);
    });
    expect(screen.getByLabelText("邮件主题")).toHaveValue("跟进任务主题");
    expect(screen.getByLabelText("邮件正文")).toHaveValue("<p>跟进任务正文</p>");
  });
});
