import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import "@testing-library/jest-dom/vitest";
import { createMemoryRouter, RouterProvider } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { useEffect } from "react";
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
  listOutreachTemplates: vi.fn(),
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
  communicationScopeKey: "1",
  selectedIdentity: {
    current_primary_material_id: 7,
  },
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

const editorMockState = vi.hoisted(() => ({
  emitInitialChange: false,
  initialHtmlOverride: null as string | null,
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

vi.mock("@/lib/api/outreachTemplates", () => ({
  listOutreachTemplates: apiMocks.listOutreachTemplates,
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
  }) => {
    useEffect(() => {
      if (!editorMockState.emitInitialChange || disabled) {
        return;
      }
      onChange({
        html: editorMockState.initialHtmlOverride ?? html,
        text: (editorMockState.initialHtmlOverride ?? html).replace(/<[^>]+>/g, ""),
      });
    }, [disabled, html, onChange]);

    return (
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
    );
  },
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
    profile_url: null,
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
  communication_scope: [
    {
      id: 1,
      name: "默认身份",
      profile_name: "申请人",
      sender_name: "小明",
      email_address: "student@example.com",
    },
  ],
  sync_warnings: [],
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
  editorMockState.emitInitialChange = false;
  editorMockState.initialHtmlOverride = null;
  selectionMock.selectedIdentityId = 1;
  selectionMock.selectedLlmProfileId = 2;
  selectionMock.communicationScopeKey = "1";
  apiMocks.getWorkspaceThread.mockResolvedValue(buildWorkspaceThread());
  apiMocks.refreshWorkspaceReplies.mockResolvedValue(buildWorkspaceThread());
  apiMocks.listOutreachTemplates.mockResolvedValue([
    {
      id: 9,
      name: "研究申请模板",
      recommended_generation_mode: "template",
      subject: "新模板主题 {{name}}",
      body_text: "新模板正文 {{sender_name}}",
      body_html: "<p>新模板正文 {{sender_name}}</p>",
      is_ready: true,
      is_default: false,
      archived_at: null,
      created_at: "2026-07-30T00:00:00Z",
      updated_at: "2026-07-30T00:00:00Z",
    },
  ]);
  apiMocks.updateTaskOutreachConfig.mockResolvedValue(
    buildWorkspaceThread({
      current_task: {
        ...buildWorkspaceThread().current_task,
        outreach_template_id: 9,
        outreach_generation_mode: "template",
        outreach_template_subject: "新模板主题 {{name}}",
        outreach_template_body_text: "新模板正文 {{sender_name}}",
        outreach_template_body_html: "<p>新模板正文 {{sender_name}}</p>",
        draft: {
          subject: "新模板主题 保存草稿导师",
          body_text: "新模板正文 小明",
          body_html: "<p>新模板正文 小明</p>",
          source: "template",
          sendable: true,
          editable: true,
        },
      },
    }),
  );
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
  it("keeps AI actions out of the collapsed composer", async () => {
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

    renderWorkspace();

    await screen.findByText("继续写信");

    expect(screen.queryByRole("button", { name: "分析匹配度" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "AI 改写" })).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "编辑草稿" }));

    expect(screen.getByRole("button", { name: "分析匹配度" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "AI 改写" })).toBeInTheDocument();
  });

  it("aligns the collapsed composer entry action with the summary top", async () => {
    renderWorkspace();

    const editButton = await screen.findByRole("button", { name: "编辑草稿" });
    const actionRow = editButton.parentElement;
    const collapsedLayout = actionRow?.parentElement;

    expect(actionRow).toHaveClass("md:pt-0.5");
    expect(collapsedLayout).toHaveClass("md:items-start");
    expect(collapsedLayout).not.toHaveClass("md:items-center");
  });

  it("does not show the generic next-step card while collapsed", async () => {
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
      }),
    );

    renderWorkspace();

    await screen.findByText("继续写信");

    expect(screen.queryByText("检查后发送")).not.toBeInTheDocument();
    expect(screen.queryByText("检查主题、正文和附件后发送。")).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "编辑草稿" }));

    expect(screen.getByText("AI 辅助")).toBeInTheDocument();
  });

  it("copies a selected library template into the current task snapshot", async () => {
    renderWorkspace();

    fireEvent.click(await screen.findByRole("button", { name: "编辑草稿" }));
    const templateTrigger = (
      await screen.findByText("当前任务独立快照")
    ).closest("button");
    expect(templateTrigger).not.toBeNull();
    await waitFor(() => expect(templateTrigger).toBeEnabled());
    fireEvent.click(templateTrigger!);
    fireEvent.click(screen.getByRole("option", { name: "研究申请模板" }));

    await waitFor(() => {
      expect(apiMocks.updateTaskOutreachConfig).toHaveBeenCalledWith(101, {
        outreach_generation_mode: "template",
        outreach_template_id: 9,
        outreach_template_subject: "新模板主题 {{name}}",
        outreach_template_body_text: "新模板正文 {{sender_name}}",
        outreach_template_body_html: "<p>新模板正文 {{sender_name}}</p>",
      });
    });
    expect(notificationMocks.notifySuccess).toHaveBeenCalledWith(
      "任务模板已更新",
      "已将“研究申请模板”的内容复制到当前任务，后续编辑不会改动模板库。",
    );
  });

  it("keeps unsaved task edits when template replacement is cancelled", async () => {
    renderWorkspace();

    fireEvent.click(await screen.findByRole("button", { name: "编辑草稿" }));
    fireEvent.change(screen.getByLabelText("邮件主题"), {
      target: { value: "尚未保存的主题" },
    });
    const templateTrigger = (
      await screen.findByText("当前任务独立快照")
    ).closest("button");
    expect(templateTrigger).not.toBeNull();
    fireEvent.click(templateTrigger!);
    fireEvent.click(screen.getByRole("option", { name: "研究申请模板" }));

    expect(
      await screen.findByText("用模板替换当前未保存的草稿？"),
    ).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "继续编辑" }));

    expect(apiMocks.updateTaskOutreachConfig).not.toHaveBeenCalled();
    expect(screen.getByLabelText("邮件主题")).toHaveValue("尚未保存的主题");
  });

  it("disables rewrite save send schedule and editor while rewriting", async () => {
    apiMocks.getWorkspaceThread.mockResolvedValueOnce(
      buildWorkspaceThread({
        current_task: {
          ...buildWorkspaceThread().current_task,
          status: "generating_draft",
          draft: {
            subject: "源主题",
            body_text: "源正文",
            body_html: "<p>源正文</p>",
            source: "rewrite_source",
            sendable: false,
            editable: false,
          },
        },
      }),
    );

    renderWorkspace();

    fireEvent.click(await screen.findByRole("button", { name: /写信|编辑草稿/ }));

    expect(screen.getByText("AI 改写中")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "AI 改写" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "保存草稿" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "立即发送" })).toBeDisabled();
    expect(screen.getByLabelText("邮件主题")).toBeDisabled();
    expect(screen.getByLabelText("邮件正文")).toBeDisabled();
  });

  it("keeps AI rewrite disabled for empty draft", async () => {
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
          draft: {
            subject: null,
            body_text: "",
            body_html: null,
            source: "manual_empty",
            sendable: false,
            editable: true,
          },
        },
      }),
    );

    renderWorkspace();

    fireEvent.click(await screen.findByRole("button", { name: /写信|编辑草稿/ }));

    expect(screen.getByRole("button", { name: "AI 改写" })).toBeDisabled();
    expect(screen.getByText("先写入正文或配置默认模板后再使用 AI 改写。")).toBeInTheDocument();
  });

  it("does not block route navigation while AI rewrite is in progress", async () => {
    apiMocks.getWorkspaceThread.mockResolvedValueOnce(
      buildWorkspaceThread({
        current_task: {
          ...buildWorkspaceThread().current_task,
          status: "generating_draft",
          draft: {
            subject: "源主题",
            body_text: "源正文",
            body_html: "<p>源正文</p>",
            source: "rewrite_source",
            sendable: false,
            editable: false,
          },
        },
      }),
    );
    const router = renderWorkspace();

    fireEvent.click(await screen.findByRole("button", { name: /写信|编辑草稿/ }));
    await router.navigate("/");

    expect(screen.queryByText("保存草稿修改？")).not.toBeInTheDocument();
    await waitFor(() => {
      expect(screen.getByText("首页")).toBeInTheDocument();
    });
  });

  it("does not prompt when the editor reports equivalent normalized initial draft content", async () => {
    editorMockState.emitInitialChange = true;
    editorMockState.initialHtmlOverride = '<p><span style="font-family:宋体;">模板正文</span></p>';
    apiMocks.getWorkspaceThread.mockResolvedValueOnce(
      buildWorkspaceThread({
        current_task: {
          ...buildWorkspaceThread().current_task,
          draft: {
            subject: "模板主题",
            body_text: "模板 正文",
            body_html: '<p><font face="宋体">模板正文</font></p>',
            source: "template",
            sendable: true,
            editable: true,
          },
        },
      }),
    );
    renderWorkspace();

    await screen.findByText("继续写信");
    fireEvent.click(screen.getByRole("button", { name: "编辑草稿" }));
    fireEvent.click(screen.getByRole("link", { name: "返回首页" }));

    expect(screen.queryByText("保存草稿修改？")).not.toBeInTheDocument();
    await waitFor(() => {
      expect(screen.getByText("首页")).toBeInTheDocument();
    });
    expect(apiMocks.saveDraft).not.toHaveBeenCalled();
  });

  it("does not prompt after expanding the composer when the editor only normalizes style formatting", async () => {
    editorMockState.emitInitialChange = true;
    editorMockState.initialHtmlOverride =
      '<p style="font-size:12pt;font-family:宋体;text-indent:0px;line-height:1.5;text-align:left;">模板正文</p>';
    apiMocks.getWorkspaceThread.mockResolvedValueOnce(
      buildWorkspaceThread({
        current_task: {
          ...buildWorkspaceThread().current_task,
          draft: {
            subject: "模板主题",
            body_text: "模板正文",
            body_html:
              '<p style="text-align: left; line-height: 1.5; text-indent: 0px; font-family: 宋体; font-size: 12pt;">模板正文</p>',
            source: "template",
            sendable: true,
            editable: true,
          },
        },
      }),
    );
    renderWorkspace();

    await screen.findByText("继续写信");
    fireEvent.click(screen.getByRole("button", { name: "编辑草稿" }));
    fireEvent.click(screen.getByRole("link", { name: "返回首页" }));

    expect(screen.queryByText("保存草稿修改？")).not.toBeInTheDocument();
    await waitFor(() => {
      expect(screen.getByText("首页")).toBeInTheDocument();
    });
    expect(apiMocks.saveDraft).not.toHaveBeenCalled();
  });

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

    fireEvent.click(await screen.findByRole("button", { name: /写信|编辑草稿/ }));
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

  it("keeps the composer open and reports a returned send failure", async () => {
    apiMocks.approveAndSend.mockResolvedValue(
      buildWorkspaceThread({
        current_task: {
          ...buildWorkspaceThread().current_task,
          status: "send_failed",
          last_error: "SMTP 认证失败",
        },
      }),
    );
    renderWorkspace();

    fireEvent.click(await screen.findByRole("button", { name: /写信|编辑草稿/ }));
    fireEvent.click(screen.getByRole("button", { name: "立即发送" }));
    fireEvent.click(await screen.findByRole("button", { name: "确认发送" }));

    await waitFor(() => {
      expect(notificationMocks.notifyError).toHaveBeenCalledWith(
        "发送失败",
        "SMTP 认证失败",
      );
    });
    expect(notificationMocks.notifySuccess).not.toHaveBeenCalledWith(
      "邮件已发送",
      expect.any(String),
    );
    expect(screen.getByRole("button", { name: "立即发送" })).toBeInTheDocument();
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

  it("does not reopen the dirty draft prompt after canceling home navigation", async () => {
    renderWorkspace();

    fireEvent.click(await screen.findByRole("button", { name: "编辑草稿" }));
    fireEvent.change(screen.getByLabelText("邮件主题"), {
      target: { value: "返回首页前的主题" },
    });

    fireEvent.click(screen.getByRole("link", { name: "返回首页" }));
    expect(await screen.findByText("保存草稿修改？")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "继续编辑" }));
    await waitFor(() => {
      expect(screen.queryByText("保存草稿修改？")).not.toBeInTheDocument();
    });

    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(screen.queryByText("保存草稿修改？")).not.toBeInTheDocument();
    expect(screen.getByText("返回首页")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("link", { name: "返回首页" }));
    expect(await screen.findByText("保存草稿修改？")).toBeInTheDocument();
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
    fireEvent.click(screen.getByRole("button", { name: "AI 改写" }));

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
