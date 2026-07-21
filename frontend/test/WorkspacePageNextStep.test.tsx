import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { createMemoryRouter, RouterProvider } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { WorkspacePage } from "@/pages/WorkspacePage";
import type {
  IdentityMaterialDTO,
  OutreachGenerationMode,
  WorkspaceMessageDTO,
  WorkspaceTaskStatus,
  WorkspaceThreadDTO,
} from "@/types";

const mockedUseSelectionContext = vi.hoisted(() => vi.fn());
const mockedGetWorkspaceThread = vi.hoisted(() => vi.fn());
const mockedEnsureWorkspaceTask = vi.hoisted(() => vi.fn());
const mockedRefreshWorkspaceReplies = vi.hoisted(() => vi.fn());
const mockedWorkspaceComposerDock = vi.hoisted(() => vi.fn());
const mockedApproveAndSend = vi.hoisted(() => vi.fn());
const mockedApproveAndSchedule = vi.hoisted(() => vi.fn());
const mockedContinueManually = vi.hoisted(() => vi.fn());
const mockedGenerateDraft = vi.hoisted(() => vi.fn());
const mockedStartFollowUp = vi.hoisted(() => vi.fn());
const mockedConfirm = vi.hoisted(() => vi.fn());
const selectedIdentity = vi.hoisted(() => ({
  current_primary_material_id: 11 as number | null,
}));
const mockedNotificationApi = vi.hoisted(() => ({
  notifyError: vi.fn(),
  notifyFormErrors: vi.fn(),
  notifySuccess: vi.fn(),
}));

vi.mock("@/context/SelectionContext", () => ({
  useSelectionContext: mockedUseSelectionContext,
}));
vi.mock("@/context/useWorkspaceDraftGuard", () => ({
  useWorkspaceDraftGuard: () => ({
    registerWorkspaceDraftGuard: vi.fn(() => vi.fn()),
    requestWorkspaceDraftGuard: vi.fn(async () => true),
  }),
}));

vi.mock("@/context/NotificationContext", () => ({
  useNotification: () => mockedNotificationApi,
}));

vi.mock("@/lib/api/workspacesApi", () => ({
  getWorkspaceThread: mockedGetWorkspaceThread,
  ensureWorkspaceTask: mockedEnsureWorkspaceTask,
  refreshWorkspaceReplies: mockedRefreshWorkspaceReplies,
}));

vi.mock("@/lib/api/emailTasksApi", () => ({
  approveAndSchedule: mockedApproveAndSchedule,
  approveAndSend: mockedApproveAndSend,
  calculateMatch: vi.fn(),
  cancelScheduledTask: vi.fn(),
  continueManually: mockedContinueManually,
  rewriteDraft: mockedGenerateDraft,
  startFollowUp: mockedStartFollowUp,
  updateTaskOutreachConfig: vi.fn(),
  updateTaskPrimaryMaterial: vi.fn(),
}));

vi.mock("@/lib/useConfirmDialog", () => ({
  useConfirmDialog: () => ({
    confirm: mockedConfirm,
    dialog: null,
  }),
}));

vi.mock("@/components/organisms/WorkspaceMessageThread", () => ({
  WorkspaceMessageThread: (props: {
    monitoringLabel?: string;
    lastCheckedAt?: Date | null;
    refreshing?: boolean;
    newReceivedCount?: number;
    onRefresh?: () => void;
  }) => (
    <div>
      <div>mock-message-thread</div>
      <div>{props.monitoringLabel}</div>
      <div>{props.lastCheckedAt ? "checked" : "not-checked"}</div>
      <div>{props.refreshing ? "refreshing-thread" : "idle-thread"}</div>
      <div>{`new-replies:${props.newReceivedCount ?? 0}`}</div>
      <button type="button" onClick={props.onRefresh}>
        mock-refresh-thread
      </button>
    </div>
  ),
}));

vi.mock("@/components/organisms/WorkspaceSidebar", () => ({
  WorkspaceSidebar: () => <div>mock-sidebar</div>,
}));

vi.mock("@/components/organisms/WorkspaceComposerDock", () => ({
  WorkspaceComposerDock: (props: {
    nextStepTitle: string;
    nextStepDescription: string;
    draftReady: boolean;
    subject: string;
    canSubmitDraft: boolean;
    content: string;
    contentHtml: string;
    onSubjectChange: (value: string) => void;
    onContentChange: (value: { html: string; text: string }) => void;
    acting: boolean;
    canCalculateMatch: boolean;
    isRewriting: boolean;
    onGenerateDraft: () => void;
    onSendNow: () => void;
    onScheduleSend: () => void;
    onContinueManually: () => void;
    onStartFollowUp: () => void;
  }) => {
    mockedWorkspaceComposerDock(props);
    return (
      <div>
        <div>{props.nextStepTitle}</div>
        <div>{props.nextStepDescription}</div>
        <div>{props.draftReady ? "draft-ready" : "draft-empty"}</div>
        <div>{props.subject ? `draft-subject:${props.subject}` : "draft-subject-empty"}</div>
        <div>{props.content ? `draft-content:${props.content}` : "draft-content-empty"}</div>
        <div>{props.contentHtml ? `draft-html:${props.contentHtml}` : "draft-html-empty"}</div>
        <div>{props.acting ? "action-busy" : "action-idle"}</div>
        <div>{props.canCalculateMatch ? "can-calculate-match" : "cannot-calculate-match"}</div>
        <div>{props.isRewriting ? "rewrite-active" : "rewrite-idle"}</div>
        <button type="button" onClick={props.onGenerateDraft}>
          mock-generate-draft
        </button>
        <button type="button" onClick={() => props.onSubjectChange("用户编辑主题")}>
          mock-edit-subject
        </button>
        <button
          type="button"
          onClick={() =>
            props.onContentChange({
              html: "<p>用户编辑正文</p>",
              text: "用户编辑正文",
            })
          }
        >
          mock-edit-content
        </button>
        {props.canSubmitDraft ? (
          <>
            <button type="button" onClick={props.onSendNow}>
              mock-send-now
            </button>
            <button type="button" onClick={props.onScheduleSend}>
              mock-schedule-send
            </button>
          </>
        ) : null}
        <button type="button" onClick={props.onContinueManually}>
          mock-continue-manually
        </button>
        <button type="button" onClick={props.onStartFollowUp}>
          mock-start-follow-up
        </button>
      </div>
    );
  },
}));

const primaryMaterial: IdentityMaterialDTO = {
  id: 11,
  display_name: "简历.pdf",
  original_filename: "resume.pdf",
  mime_type: "application/pdf",
  size_bytes: 1024,
  material_type: "resume",
  is_primary: true,
  created_at: "2026-04-22T00:00:00Z",
};

const stripHtml = (html: string) => html.replace(/<[^>]+>/g, " ").replace(/\s+/g, " ").trim();

const buildThread = ({
  status = "matched",
  primaryMaterialId = primaryMaterial.id,
  generatedSubject = null,
  generatedContentText = null,
  generatedContentHtml = null,
  cancellationReason = null,
  canContinueManually = false,
  canWriteFollowUp = false,
  outreachGenerationMode = "llm",
  professorResearchDirection = "多智能体系统",
  professorRecentPapers = [],
  messages = [],
  lastDraftPromptTokens = null,
  lastDraftCompletionTokens = null,
  lastDraftTotalTokens = null,
  draftSendable = undefined,
  draftEditable = true,
}: {
  status?: WorkspaceTaskStatus;
  primaryMaterialId?: number | null;
  generatedSubject?: string | null;
  generatedContentText?: string | null;
  generatedContentHtml?: string | null;
  cancellationReason?: string | null;
  canContinueManually?: boolean;
  canWriteFollowUp?: boolean;
  outreachGenerationMode?: OutreachGenerationMode;
  professorResearchDirection?: string | null;
  professorRecentPapers?: string[];
  messages?: WorkspaceMessageDTO[];
  lastDraftPromptTokens?: number | null;
  lastDraftCompletionTokens?: number | null;
  lastDraftTotalTokens?: number | null;
  draftSendable?: boolean;
  draftEditable?: boolean;
} = {}): WorkspaceThreadDTO => {
  const hasGeneratedDraft =
    generatedSubject !== null ||
    generatedContentText !== null ||
    generatedContentHtml !== null;
  const draftSubject = hasGeneratedDraft ? generatedSubject : "测试主题";
  const draftBodyText = hasGeneratedDraft
    ? generatedContentText ?? (generatedContentHtml ? stripHtml(generatedContentHtml) : "")
    : "测试正文";
  const draftBodyHtml = hasGeneratedDraft ? generatedContentHtml : null;

  return ({
  professor: {
    id: 101,
    name: "王教授",
    email: "prof@example.com",
    title: "教授",
    university: "测试大学",
    school: "计算机学院",
    research_direction: professorResearchDirection,
    recent_papers: professorRecentPapers,
  },
  identity: {
    id: 1,
    name: "测试身份",
    profile_name: "测试身份",
    sender_name: "测试同学",
    email_address: "sender@example.com",
  },
  llm_profile: {
    id: 1,
    name: "测试模型",
    provider: "openai",
    model_name: "gpt-test",
  },
  material_options: primaryMaterialId ? [primaryMaterial] : [],
  current_task: {
    id: 301,
    source: "manual",
    batch_task_id: 21,
    parent_task_id: null,
    status,
    cancellation_reason: cancellationReason,
    can_continue_manually: canContinueManually,
    can_write_follow_up: canWriteFollowUp,
    outreach_generation_mode: outreachGenerationMode,
    outreach_template_subject: "测试主题",
    outreach_template_body_text: "测试正文",
    outreach_template_body_html: null,
    rendered_template_subject: "测试主题",
    rendered_template_body_text: "测试正文",
    rendered_template_body_html: null,
    match_score: 90,
    match_reason: null,
    fit_points: [],
    risk_points: [],
    match_keywords: [],
    generated_subject: generatedSubject,
    generated_content_text: generatedContentText,
    generated_content_html: generatedContentHtml,
    approved_subject: null,
    approved_body_text: null,
    approved_body_html: null,
    primary_material_id: primaryMaterialId,
    primary_material: primaryMaterialId ? primaryMaterial : null,
    selected_material_ids: [],
    approved_at: null,
    scheduled_at: status === "scheduled" ? "2026-04-22T10:00:00Z" : null,
    last_send_attempt_at: null,
    sent_at: status === "sent" ? "2026-04-22T09:00:00Z" : null,
    last_rfc_message_id: null,
    retry_count: 0,
    last_error: null,
    is_replied: status === "reply_detected",
    estimated_prompt_tokens: null,
    estimated_completion_tokens_upper_bound: null,
    estimated_total_tokens_upper_bound: null,
    last_draft_prompt_tokens: lastDraftPromptTokens,
    last_draft_completion_tokens: lastDraftCompletionTokens,
    last_draft_total_tokens: lastDraftTotalTokens,
    draft: {
      subject: draftSubject,
      body_text: draftBodyText,
      body_html: draftBodyHtml,
      source: hasGeneratedDraft ? "ai_rewrite" : "template",
      sendable: draftSendable ?? Boolean(hasGeneratedDraft && (draftBodyText.trim() || draftBodyHtml?.trim())),
      editable: draftEditable,
    },
  },
  messages,
  communication_scope: [
    {
      id: 1,
      name: "测试身份",
      profile_name: "测试身份",
      sender_name: "测试同学",
      email_address: "sender@example.com",
    },
  ],
  sync_warnings: [],
  });
};

const buildWorkspaceMessage = (
  overrides: Partial<WorkspaceMessageDTO> = {},
): WorkspaceMessageDTO => ({
  id: 1,
  direction: "sent",
  subject: "测试主题",
  content: "老师您好",
  content_html: "<p>老师您好</p>",
  rfc_message_id: null,
  failure_summary: null,
  reply_headers: null,
  prompt_tokens: null,
  completion_tokens: null,
  total_tokens: null,
  created_at: "2026-04-22T10:00:00Z",
  source_identities: [
    {
      id: 1,
      name: "测试身份",
      profile_name: "测试身份",
      sender_name: "测试同学",
      email_address: "sender@example.com",
    },
  ],
  ...overrides,
});

const renderPage = () => {
  const router = createMemoryRouter(
    [{ path: "/workspace/:id", element: <WorkspacePage /> }],
    { initialEntries: ["/workspace/101"] },
  );

  return render(<RouterProvider router={router} />);
};

type MockComposerDockProps = {
  nextStepTitle: string;
  nextStepDescription: string;
  draftReady: boolean;
  subject: string;
  canSubmitDraft: boolean;
  content: string;
  contentHtml: string;
  acting: boolean;
  canCalculateMatch: boolean;
  isRewriting: boolean;
  canGenerateDraft: boolean;
  canContinueManually: boolean;
  canStartFollowUp: boolean;
  currentTaskMode: OutreachGenerationMode;
};

const latestComposerDockProps = () => {
  expect(mockedWorkspaceComposerDock.mock.calls.length).toBeGreaterThan(0);
  return mockedWorkspaceComposerDock.mock.calls.at(-1)?.[0] as MockComposerDockProps;
};

describe("WorkspacePage next-step", () => {
  afterEach(() => {
    vi.useRealTimers();
  });

  beforeEach(() => {
    mockedWorkspaceComposerDock.mockReset();
    mockedGetWorkspaceThread.mockReset();
    mockedEnsureWorkspaceTask.mockReset();
    mockedRefreshWorkspaceReplies.mockReset();
    mockedApproveAndSend.mockReset();
    mockedApproveAndSchedule.mockReset();
    mockedContinueManually.mockReset();
    mockedGenerateDraft.mockReset();
    mockedStartFollowUp.mockReset();
    mockedConfirm.mockReset();
    mockedNotificationApi.notifyError.mockReset();
    mockedNotificationApi.notifyFormErrors.mockReset();
    mockedNotificationApi.notifySuccess.mockReset();
    mockedConfirm.mockResolvedValue(true);
    mockedApproveAndSend.mockImplementation(async () =>
      buildThread({
        generatedContentHtml: "<p>发送后的 HTML 草稿</p>",
      }),
    );
    mockedApproveAndSchedule.mockImplementation(async () =>
      buildThread({
        status: "scheduled",
        generatedContentHtml: "<p>定时后的 HTML 草稿</p>",
      }),
    );
    mockedContinueManually.mockImplementation(async () =>
      buildThread({
        status: "matched",
        primaryMaterialId: null,
      }),
    );
    mockedGenerateDraft.mockImplementation(async () =>
      buildThread({
        status: "review_required",
        generatedSubject: "生成后的主题",
        generatedContentText: "生成后的正文",
        generatedContentHtml: "<p>生成后的正文</p>",
      }),
    );
    mockedStartFollowUp.mockImplementation(async () =>
      buildThread({
        status: "matched",
        primaryMaterialId: null,
      }),
    );
    mockedUseSelectionContext.mockReturnValue({
      identities: [selectedIdentity],
      llmProfiles: [],
      selectedIdentityId: 1,
      selectedLlmProfileId: 1,
      selectedIdentity,
      selectedLlmProfile: null,
      loading: false,
      setSelectedIdentityId: vi.fn(),
      setSelectedLlmProfileId: vi.fn(),
      refreshSelections: vi.fn(),
    });
    selectedIdentity.current_primary_material_id = primaryMaterial.id;
  });

  it("refreshes the current workspace once a minute without replacing the thread with a loading screen", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    mockedGetWorkspaceThread.mockResolvedValue(buildThread());

    renderPage();

    expect(await screen.findByText("mock-message-thread")).toBeInTheDocument();
    expect(screen.getByText("idle-thread")).toBeInTheDocument();

    await vi.advanceTimersByTimeAsync(60_000);

    await waitFor(() => {
      expect(mockedGetWorkspaceThread).toHaveBeenCalledTimes(2);
    });
    expect(screen.queryByText("正在打开老师档案...")).not.toBeInTheDocument();

    vi.useRealTimers();
  });

  it("notifies when a background refresh detects a new teacher reply", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    mockedGetWorkspaceThread
      .mockResolvedValueOnce(buildThread())
      .mockResolvedValueOnce(
        buildThread({
          status: "reply_detected",
          messages: [
            buildWorkspaceMessage({
              id: 2,
              direction: "received",
              subject: "Re: 测试主题",
              content: "欢迎继续交流",
              content_html: "<p>欢迎继续交流</p>",
            }),
          ],
        }),
      );

    renderPage();

    expect(await screen.findByText("mock-message-thread")).toBeInTheDocument();

    await vi.advanceTimersByTimeAsync(60_000);

    await waitFor(() => {
      expect(mockedNotificationApi.notifySuccess).toHaveBeenCalledWith(
        "收到老师回复",
        "王教授回复了：Re: 测试主题",
      );
      expect(screen.getByText("new-replies:1")).toBeInTheDocument();
    });

    vi.useRealTimers();
  });

  it("uses readable text in reply notifications when the message body is html", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    mockedGetWorkspaceThread
      .mockResolvedValueOnce(buildThread())
      .mockResolvedValueOnce(
        buildThread({
          status: "reply_detected",
          messages: [
            buildWorkspaceMessage({
              id: 2,
              direction: "received",
              subject: null,
              content:
                "<html><body><p>欢迎继续交流</p><p><strong>后续联系</strong></p></body></html>",
              content_html: null,
            }),
          ],
        }),
      );

    renderPage();

    expect(await screen.findByText("mock-message-thread")).toBeInTheDocument();

    await vi.advanceTimersByTimeAsync(60_000);

    await waitFor(() => {
      expect(mockedNotificationApi.notifySuccess).toHaveBeenCalledWith(
        "收到老师回复",
        "王教授回复了：欢迎继续交流 后续联系",
      );
    });

    vi.useRealTimers();
  });

  it("manual thread refresh checks mailbox before reloading workspace messages", async () => {
    mockedGetWorkspaceThread.mockResolvedValueOnce(buildThread());
    mockedRefreshWorkspaceReplies.mockResolvedValueOnce(
      buildThread({
        status: "reply_detected",
        messages: [
          buildWorkspaceMessage({
            id: 2,
            direction: "received",
            subject: "Re: 测试主题",
            content: "欢迎继续交流",
            content_html: "<p>欢迎继续交流</p>",
          }),
        ],
      }),
    );

    renderPage();

    expect(await screen.findByText("mock-message-thread")).toBeInTheDocument();

    fireEvent.click(screen.getByText("mock-refresh-thread"));

    await waitFor(() => {
      expect(mockedRefreshWorkspaceReplies).toHaveBeenCalledWith(101, 1, 1);
      expect(mockedGetWorkspaceThread).toHaveBeenCalledTimes(1);
    });
  });

  it("keeps local composer edits when a background refresh returns older draft content", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    mockedGetWorkspaceThread
      .mockResolvedValueOnce(
        buildThread({
          generatedSubject: "服务器旧主题",
          generatedContentText: "服务器旧正文",
          generatedContentHtml: "<p>服务器旧正文</p>",
        }),
      )
      .mockResolvedValueOnce(
        buildThread({
          generatedSubject: "服务器旧主题",
          generatedContentText: "服务器旧正文",
          generatedContentHtml: "<p>服务器旧正文</p>",
        }),
      );

    renderPage();

    expect(await screen.findByText("draft-subject:服务器旧主题")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "mock-edit-subject" }));
    fireEvent.click(screen.getByRole("button", { name: "mock-edit-content" }));

    await waitFor(() => {
      expect(screen.getByText("draft-subject:用户编辑主题")).toBeInTheDocument();
      expect(screen.getByText("draft-content:用户编辑正文")).toBeInTheDocument();
    });

    await vi.advanceTimersByTimeAsync(60_000);

    await waitFor(() => {
      expect(mockedGetWorkspaceThread).toHaveBeenCalledTimes(2);
    });
    expect(screen.getByText("draft-subject:用户编辑主题")).toBeInTheDocument();
    expect(screen.getByText("draft-content:用户编辑正文")).toBeInTheDocument();
    expect(screen.queryByText("draft-subject:服务器旧主题")).not.toBeInTheDocument();

    vi.useRealTimers();
  });

  it("applies a completed rewrite returned by background refresh while the original request is still pending", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    mockedGetWorkspaceThread
      .mockResolvedValueOnce(
        buildThread({
          generatedSubject: "改写前主题",
          generatedContentText: "改写前正文",
          generatedContentHtml: "<p>改写前正文</p>",
        }),
      )
      .mockResolvedValueOnce(
        buildThread({
          status: "review_required",
          generatedSubject: "后台完成主题",
          generatedContentText: "后台完成正文",
          generatedContentHtml: "<p>后台完成正文</p>",
        }),
      );
    mockedGenerateDraft.mockReturnValueOnce(new Promise(() => undefined));

    renderPage();

    expect(await screen.findByText("draft-subject:改写前主题")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "mock-edit-subject" }));
    fireEvent.click(screen.getByRole("button", { name: "mock-edit-content" }));
    fireEvent.click(screen.getByRole("button", { name: "mock-generate-draft" }));

    await waitFor(() => {
      expect(screen.getByText("rewrite-active")).toBeInTheDocument();
    });

    await vi.advanceTimersByTimeAsync(3_000);

    await waitFor(() => {
      expect(mockedGetWorkspaceThread).toHaveBeenCalledTimes(2);
      expect(screen.getByText("action-idle")).toBeInTheDocument();
      expect(screen.getByText("rewrite-idle")).toBeInTheDocument();
      expect(screen.getByText("draft-subject:后台完成主题")).toBeInTheDocument();
      expect(screen.getByText("draft-content:后台完成正文")).toBeInTheDocument();
    });

    vi.useRealTimers();
  });

  it("refreshes immediately when returning to a visible workspace tab", async () => {
    mockedGetWorkspaceThread.mockResolvedValue(buildThread());

    renderPage();

    expect(await screen.findByText("mock-message-thread")).toBeInTheDocument();

    Object.defineProperty(document, "visibilityState", {
      configurable: true,
      value: "visible",
    });
    document.dispatchEvent(new Event("visibilitychange"));

    await waitFor(() => {
      expect(mockedGetWorkspaceThread).toHaveBeenCalledTimes(2);
    });
  });

  it("treats an HTML-only draft as an existing draft instead of prompting to generate one", async () => {
    mockedGetWorkspaceThread.mockResolvedValue(
      buildThread({
        generatedContentHtml: "<p>仅有 HTML 草稿</p>",
      }),
    );

    renderPage();

    await waitFor(() => {
      expect(latestComposerDockProps()).toEqual(
        expect.objectContaining({
          draftReady: true,
          canSubmitDraft: true,
          content: "仅有 HTML 草稿",
          contentHtml: "<p>仅有 HTML 草稿</p>",
        }),
      );
    });
    expect(screen.getByText("draft-ready")).toBeInTheDocument();
  });

  it("shows sent relationship status when a follow-up draft is the current task", async () => {
    mockedGetWorkspaceThread.mockResolvedValue(
      buildThread({
        status: "matched",
        messages: [
          buildWorkspaceMessage({
            id: 10,
            direction: "sent",
            subject: "已发送主题",
          }),
        ],
      }),
    );

    renderPage();

    expect(await screen.findByText("已发送")).toBeInTheDocument();
    expect(screen.queryByText("已算匹配")).not.toBeInTheDocument();
  });

  it("shows replied relationship status ahead of the current follow-up draft state", async () => {
    mockedGetWorkspaceThread.mockResolvedValue(
      buildThread({
        status: "matched",
        messages: [
          buildWorkspaceMessage({
            id: 10,
            direction: "sent",
            subject: "已发送主题",
          }),
          buildWorkspaceMessage({
            id: 11,
            direction: "received",
            subject: "回复：已发送主题",
            content: "欢迎报考",
          }),
        ],
      }),
    );

    renderPage();

    expect(await screen.findByText("已回复")).toBeInTheDocument();
    expect(screen.queryByText("已算匹配")).not.toBeInTheDocument();
  });

  it("shows readable draft content in the workspace for an HTML-only draft", async () => {
    mockedGetWorkspaceThread.mockResolvedValue(
      buildThread({
        generatedContentHtml: "<p>老师您好</p><p>这里是 HTML 草稿正文。</p>",
      }),
    );

    renderPage();

    expect(
      await screen.findByText("draft-content:老师您好 这里是 HTML 草稿正文。"),
    ).toBeInTheDocument();
    expect(screen.queryByText("draft-content-empty")).not.toBeInTheDocument();
  });

  it("does not treat a subject-only draft as ready to send", async () => {
    mockedGetWorkspaceThread.mockResolvedValue(
      buildThread({
        generatedSubject: "只有主题",
      }),
    );

    renderPage();

    await waitFor(() => {
      expect(latestComposerDockProps()).toEqual(
        expect.objectContaining({
          subject: "只有主题",
          draftReady: false,
          content: "",
          contentHtml: "",
        }),
      );
    });
    expect(screen.getByText("draft-empty")).toBeInTheDocument();
  });

  it("passes the configured template into the composer before a draft is generated", async () => {
    mockedGetWorkspaceThread.mockResolvedValue(buildThread());

    renderPage();

    expect(await screen.findByText("draft-subject:测试主题")).toBeInTheDocument();
    expect(screen.getByText("draft-content:测试正文")).toBeInTheDocument();
    expect(screen.getByText("draft-ready")).toBeInTheDocument();
  });

  it("treats a template-mode configured template as sendable before generating a draft", async () => {
    mockedGetWorkspaceThread.mockResolvedValue(
      buildThread({
        outreachGenerationMode: "template",
        draftSendable: true,
      }),
    );

    renderPage();

    expect(await screen.findByText("draft-subject:测试主题")).toBeInTheDocument();
    expect(screen.getByText("draft-content:测试正文")).toBeInTheDocument();
    expect(screen.getByText("draft-ready")).toBeInTheDocument();
    expect(latestComposerDockProps()).toEqual(
        expect.objectContaining({
          draftReady: true,
          canSubmitDraft: true,
        }),
    );
    expect(screen.getByRole("button", { name: "mock-send-now" })).toBeInTheDocument();
  });

  it("shows the generated draft in the composer after clicking generate", async () => {
    mockedGetWorkspaceThread.mockResolvedValue(buildThread());

    renderPage();

    await screen.findByText("draft-subject:测试主题");
    fireEvent.click(screen.getByRole("button", { name: "mock-generate-draft" }));

    await waitFor(() => {
      expect(mockedGenerateDraft).toHaveBeenCalledWith(
        301,
        expect.objectContaining({
          llm_profile_id: 1,
          subject: "测试主题",
          body_text: "测试正文",
        }),
      );
    });

    await waitFor(() => {
      expect(screen.getByText("draft-subject:生成后的主题")).toBeInTheDocument();
      expect(screen.getByText("draft-content:生成后的正文")).toBeInTheDocument();
      expect(screen.getByText("draft-html:<p>生成后的正文</p>")).toBeInTheDocument();
    });
  });

  it("notifies token usage and elapsed time after generating a draft", async () => {
    mockedGetWorkspaceThread.mockResolvedValue(buildThread());
    mockedGenerateDraft.mockResolvedValue(
      buildThread({
        status: "review_required",
        generatedSubject: "生成后的主题",
        generatedContentText: "生成后的正文",
        generatedContentHtml: "<p>生成后的正文</p>",
        lastDraftPromptTokens: 6114,
        lastDraftCompletionTokens: 1363,
        lastDraftTotalTokens: 7477,
      }),
    );

    renderPage();

    await screen.findByText("draft-subject:测试主题");
    fireEvent.click(screen.getByRole("button", { name: "mock-generate-draft" }));

    await waitFor(() => {
      expect(mockedNotificationApi.notifySuccess).toHaveBeenCalledWith(
        "AI 改写已完成",
        expect.stringMatching(/^输入 6,114 \/ 输出 1,363 \/ 总计 7,477 token，耗时 \d+\.\d 秒$/),
      );
    });
  });

  it("keeps match analysis separate when no task writing material is set", async () => {
    mockedGetWorkspaceThread.mockResolvedValue(
      buildThread({
        primaryMaterialId: null,
        draftSendable: false,
      }),
    );

    renderPage();

    await waitFor(() => {
      expect(latestComposerDockProps()).toEqual(
        expect.objectContaining({
          canCalculateMatch: true,
          canGenerateDraft: false,
          draftReady: true,
        }),
      );
    });
    expect(screen.getByText("draft-ready")).toBeInTheDocument();
  });

  it("keeps match analysis available when only the task writing material is empty", async () => {
    mockedGetWorkspaceThread.mockResolvedValue(
      buildThread({
        primaryMaterialId: null,
        professorRecentPapers: ["Coordination in multi-agent systems"],
      }),
    );

    renderPage();

    await waitFor(() => {
      expect(latestComposerDockProps()).toEqual(
        expect.objectContaining({
          canCalculateMatch: true,
          canGenerateDraft: false,
        }),
      );
    });
  });

  it("disables match analysis when the identity has no current default material", async () => {
    selectedIdentity.current_primary_material_id = null;
    mockedGetWorkspaceThread.mockResolvedValue(
      buildThread({
        professorRecentPapers: ["Coordination in multi-agent systems"],
      }),
    );

    renderPage();

    await waitFor(() => {
      expect(latestComposerDockProps()).toEqual(
        expect.objectContaining({
          canCalculateMatch: false,
        }),
      );
    });
  });

  it("disables match analysis in the workspace when professor research evidence is missing", async () => {
    mockedGetWorkspaceThread.mockResolvedValue(
      buildThread({
        professorResearchDirection: null,
        professorRecentPapers: [],
      }),
    );

    renderPage();

    expect(await screen.findByText("cannot-calculate-match")).toBeInTheDocument();
  });

  it("allows match analysis in the workspace when professor only has recent papers", async () => {
    mockedGetWorkspaceThread.mockResolvedValue(
      buildThread({
        professorResearchDirection: null,
        professorRecentPapers: ["Paper Evidence"],
      }),
    );

    renderPage();

    expect(await screen.findByText("can-calculate-match")).toBeInTheDocument();
  });

  it("shows follow-up guidance ahead of missing-material or draft prompts for sent tasks", async () => {
    mockedGetWorkspaceThread.mockResolvedValue(
      buildThread({
        status: "sent",
        primaryMaterialId: null,
        canWriteFollowUp: true,
      }),
    );

    renderPage();

    await waitFor(() => {
      expect(latestComposerDockProps()).toEqual(
        expect.objectContaining({
          canStartFollowUp: true,
          canContinueManually: false,
          canGenerateDraft: false,
        }),
      );
    });
  });

  it("continues a batch-stopped task manually from the workspace action", async () => {
    mockedGetWorkspaceThread.mockResolvedValue(
      buildThread({
        status: "canceled",
        primaryMaterialId: null,
        cancellationReason: "batch_stopped",
        canContinueManually: true,
        generatedContentHtml: "<p>旧草稿不应继续发送</p>",
        draftSendable: false,
        draftEditable: false,
      }),
    );

    renderPage();

    await waitFor(() => {
      expect(latestComposerDockProps()).toEqual(
        expect.objectContaining({
          canContinueManually: true,
          canSubmitDraft: false,
          draftReady: false,
        }),
      );
    });
    expect(screen.getByText("draft-empty")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "mock-send-now" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "mock-schedule-send" })).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "mock-continue-manually" }));

    await waitFor(() => {
      expect(mockedContinueManually).toHaveBeenCalledWith(301);
    });
  });

  it("reopens an expired history task as a fresh manual workspace task", async () => {
    mockedGetWorkspaceThread.mockResolvedValueOnce(
      buildThread({
        status: "canceled",
        cancellationReason: "schedule_expired",
        primaryMaterialId: null,
      }),
    );
    const resumedThread = buildThread({
      status: "matched",
      primaryMaterialId: null,
    });
    resumedThread.current_task.id = 302;
    resumedThread.current_task.batch_task_id = null;
    resumedThread.current_task.parent_task_id = 301;
    mockedEnsureWorkspaceTask.mockResolvedValueOnce(resumedThread);

    renderPage();

    await waitFor(() => {
      expect(mockedEnsureWorkspaceTask).toHaveBeenCalledWith(101, 1, 1);
    });

    await waitFor(() => {
      expect(latestComposerDockProps()).toEqual(
        expect.objectContaining({
          canSubmitDraft: true,
          canContinueManually: false,
          canStartFollowUp: false,
        }),
      );
    });
    expect(screen.getByRole("button", { name: "mock-send-now" })).toBeInTheDocument();
  });

  it("starts a follow-up draft from the workspace action", async () => {
    mockedGetWorkspaceThread.mockResolvedValue(
      buildThread({
        status: "reply_detected",
        primaryMaterialId: null,
        canWriteFollowUp: true,
      }),
    );

    renderPage();

    await waitFor(() => {
      expect(latestComposerDockProps()).toEqual(
        expect.objectContaining({
          canStartFollowUp: true,
          canGenerateDraft: false,
        }),
      );
    });

    fireEvent.click(screen.getByRole("button", { name: "mock-start-follow-up" }));

    await waitFor(() => {
      expect(mockedStartFollowUp).toHaveBeenCalledWith(301);
    });
  });

  it("shows follow-up guidance in the UI whenever can_write_follow_up is true, even if status is not sent or reply_detected", async () => {
    mockedGetWorkspaceThread.mockResolvedValue(
      buildThread({
        status: "approved",
        primaryMaterialId: null,
        canWriteFollowUp: true,
      }),
    );

    renderPage();

    await waitFor(() => {
      expect(latestComposerDockProps()).toEqual(
        expect.objectContaining({
          canStartFollowUp: true,
          canContinueManually: false,
          canGenerateDraft: false,
        }),
      );
    });
  });

  it("prefers continue-manually guidance in the UI whenever can_continue_manually is true, without relying on canceled status", async () => {
    mockedGetWorkspaceThread.mockResolvedValue(
      buildThread({
        status: "approved",
        primaryMaterialId: null,
        canContinueManually: true,
      }),
    );

    renderPage();

    await waitFor(() => {
      expect(latestComposerDockProps()).toEqual(
        expect.objectContaining({
          canContinueManually: true,
          canStartFollowUp: false,
          canGenerateDraft: false,
        }),
      );
    });
  });

  it("fills a non-empty body_text when sending an HTML-only draft", async () => {
    mockedGetWorkspaceThread.mockResolvedValue(
      buildThread({
        generatedContentHtml: "<p>老师您好</p><p>我想请教一个研究问题。</p>",
      }),
    );

    renderPage();

    await waitFor(() => {
      expect(latestComposerDockProps().canSubmitDraft).toBe(true);
    });

    fireEvent.click(screen.getByRole("button", { name: "mock-send-now" }));

    await waitFor(() => {
      expect(mockedConfirm).toHaveBeenCalledWith(
        expect.objectContaining({
          title: "确认立即发送这封真实邮件？",
        }),
      );
      expect(mockedApproveAndSend).toHaveBeenCalledTimes(1);
    });

    expect(mockedApproveAndSend).toHaveBeenCalledWith(
      301,
      expect.objectContaining({
        body_html: "<p>老师您好</p><p>我想请教一个研究问题。</p>",
        body_text: expect.any(String),
      }),
    );

    const payload = mockedApproveAndSend.mock.calls[0][1] as { body_text: string };
    expect(payload.body_text.trim()).toBeTruthy();
    expect(payload.body_text).toContain("老师您好");
  });

  it("asks for the scheduled send time after clicking schedule and uses it in the payload", async () => {
    mockedGetWorkspaceThread.mockResolvedValue(
      buildThread({
        generatedContentHtml: "<p>老师您好</p><p>期待和您进一步交流。</p>",
      }),
    );

    renderPage();

    await waitFor(() => {
      expect(latestComposerDockProps().canSubmitDraft).toBe(true);
    });

    fireEvent.click(screen.getByRole("button", { name: "mock-schedule-send" }));

    expect(await screen.findByText("选择定时发送时间")).toBeInTheDocument();
    expect(mockedApproveAndSchedule).not.toHaveBeenCalled();
    expect(mockedConfirm).not.toHaveBeenCalledWith(
      expect.objectContaining({
        title: "确认定时发送这封真实邮件？",
      }),
    );

    fireEvent.change(screen.getByLabelText("发送时间"), {
      target: { value: "2026-04-22T18:30" },
    });
    fireEvent.click(screen.getByRole("button", { name: "确认定时" }));

    await waitFor(() => {
      expect(mockedApproveAndSchedule).toHaveBeenCalledTimes(1);
    });

    expect(mockedApproveAndSchedule).toHaveBeenCalledWith(
      301,
      expect.objectContaining({
        body_html: "<p>老师您好</p><p>期待和您进一步交流。</p>",
        body_text: expect.any(String),
        scheduled_at: expect.any(String),
      }),
    );

    const payload = mockedApproveAndSchedule.mock.calls[0][1] as { body_text: string };
    expect(payload.body_text.trim()).toBeTruthy();
    expect(payload.body_text).toContain("老师您好");
  });
});
