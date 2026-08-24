import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ProfilePage } from "@/pages/ProfilePage";
import { testIdentitySmtp, updateIdentity } from "@/lib/api/identities";
import {
  deleteLLMProfile,
  getLLMProfileDeletionImpact,
  testLLMProfilePreview,
  updateLLMProfile,
} from "@/lib/api/llmProfiles";
import { setPrimaryMaterial } from "@/lib/api/materials";
import { PROFILE_HELP_LINKS } from "@/lib/helpLinks";
import type {
  IdentityDTO,
  LLMProfileDeletionImpactDTO,
  LLMProfileDTO,
  LLMProfileReferenceCountsDTO,
} from "@/types";

const mockedUseSelectionContext = vi.hoisted(() => vi.fn());
const mockedUseDesktopBackend = vi.hoisted(() => vi.fn());
const mockedGetTestComposeThread = vi.hoisted(() => vi.fn());
const mockedGetTestComposeStatus = vi.hoisted(() => vi.fn());
const mockedNotifyError = vi.hoisted(() => vi.fn());
const mockedNotifySuccess = vi.hoisted(() => vi.fn());
const mockedRequestWorkspaceDraftGuard = vi.hoisted(() => vi.fn());
const mockedRegisterWorkspaceDraftGuard = vi.hoisted(() =>
  vi.fn(() => vi.fn()),
);
const mockedChoose = vi.hoisted(() => vi.fn());
const mockedListOutreachTemplates = vi.hoisted(() => vi.fn());
const mockedUpdateOutreachTemplate = vi.hoisted(() => vi.fn());
const mockedOpenExternalHttpUrl = vi.hoisted(() => vi.fn());

vi.mock("@/context/SelectionContext", () => ({
  useSelectionContext: mockedUseSelectionContext,
}));
vi.mock("@/context/useWorkspaceDraftGuard", () => ({
  useWorkspaceDraftGuard: () => ({
    registerWorkspaceDraftGuard: mockedRegisterWorkspaceDraftGuard,
    requestWorkspaceDraftGuard: mockedRequestWorkspaceDraftGuard,
  }),
}));

vi.mock("@/context/DesktopBackendContext", () => ({
  useDesktopBackend: mockedUseDesktopBackend,
}));

vi.mock("@/context/NotificationContext", () => ({
  useNotification: () => ({
    notifyError: mockedNotifyError,
    notifyFormErrors: vi.fn(),
    notifySuccess: mockedNotifySuccess,
  }),
}));

vi.mock("@/lib/useConfirmDialog", () => ({
  useConfirmDialog: () => ({
    confirm: vi.fn(),
    choose: mockedChoose,
    dialog: null,
  }),
}));

vi.mock("@/lib/externalUrls", () => ({
  openExternalHttpUrl: mockedOpenExternalHttpUrl,
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
      <div role="textbox" aria-label={label}>
        模拟富文本编辑器
      </div>
      <span data-testid={`editor-html-${label}`}>{html}</span>
      <button
        type="button"
        onClick={() =>
          onChange({
            html: "<p>富文本更新</p>",
            text: "富文本更新",
          })
        }
      >
        模拟编辑默认模板正文
      </button>
    </div>
  ),
}));

vi.mock("@/components/organisms/DiagnosticLogPanel", () => ({
  DiagnosticLogPanel: () => (
    <section aria-label="诊断日志面板">
      <h2>诊断日志</h2>
    </section>
  ),
}));

vi.mock("@/lib/api/identities", () => ({
  createIdentity: vi.fn(),
  deleteIdentity: vi.fn(),
  importIdentityTemplate: vi.fn(),
  setDefaultIdentity: vi.fn(),
  testIdentityImap: vi.fn(),
  testIdentitySmtp: vi.fn(),
  updateIdentity: vi.fn(),
  updateIdentityDefaultOutreachTemplate: vi.fn(),
}));

vi.mock("@/lib/api/outreachTemplates", () => ({
  archiveOutreachTemplate: vi.fn(),
  createOutreachTemplate: vi.fn(),
  duplicateOutreachTemplate: vi.fn(),
  listOutreachTemplates: mockedListOutreachTemplates,
  restoreOutreachTemplate: vi.fn(),
  setGlobalDefaultOutreachTemplate: vi.fn(),
  updateOutreachTemplate: mockedUpdateOutreachTemplate,
}));

vi.mock("@/lib/api/materials", () => ({
  deleteMaterial: vi.fn(),
  getMaterialDeletionImpact: vi.fn(),
  downloadMaterial: vi.fn(),
  setPrimaryMaterial: vi.fn(),
  uploadIdentityMaterial: vi.fn(),
}));

vi.mock("@/lib/api/llmProfiles", () => ({
  createLLMProfile: vi.fn(),
  deleteLLMProfile: vi.fn(),
  fetchLLMProfileModelsPreview: vi.fn(),
  getLLMProfileDeletionImpact: vi.fn(),
  setDefaultLLMProfile: vi.fn(),
  testLLMProfilePreview: vi.fn(),
  updateLLMProfile: vi.fn(),
}));

vi.mock("@/lib/api/testComposeApi", () => ({
  getTestComposeThread: mockedGetTestComposeThread,
  getTestComposeStatus: mockedGetTestComposeStatus,
}));

const selectedIdentity: IdentityDTO = {
  id: 1,
  name: "旧身份名称",
  profile_name: "博士申请配置",
  sender_name: "王同学",
  email_address: "sender@example.com",
  smtp_host: "smtp.example.com",
  smtp_port: 465,
  smtp_username: "sender@example.com",
  smtp_password: "secret",
  imap_host: "imap.example.com",
  imap_port: 993,
  imap_username: "sender@example.com",
  imap_password: "secret",
  default_language: "zh-CN",
  outreach_generation_mode: "template",
  outreach_template_subject: "测试主题",
  outreach_template_body_text: "测试正文",
  outreach_template_body_html: "<p>测试正文</p>",
  default_outreach_template_id: 1,
  current_primary_material_id: null,
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
};

const selectedIdentityWithMaterial: IdentityDTO = {
  ...selectedIdentity,
  materials: [
    {
      id: 7,
      display_name: "简历.pdf",
      original_filename: "resume.pdf",
      mime_type: "application/pdf",
      size_bytes: 1024,
      material_type: "resume",
      is_primary: true,
      created_at: "2026-04-22T00:00:00Z",
    },
  ],
};

const selectedLlmProfile: LLMProfileDTO = {
  id: 1,
  name: "测试模型",
  provider: "openai",
  api_base_url: "https://api.openai.com/v1",
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

const emptyLLMReferences: LLMProfileReferenceCountsDTO = {
  batch_tasks: 0,
  email_tasks: 0,
  email_logs: 0,
  match_analysis_jobs: 0,
  match_analysis_job_items: 0,
  match_analysis_runs: 0,
  test_compose_sessions: 0,
  test_compose_messages: 0,
  crawl_jobs: 0,
  crawl_runs: 0,
  crawl_pages: 0,
  crawl_candidates: 0,
  crawl_token_usages: 0,
  match_results: 0,
  agent_change_plans: 0,
  operation_logs: 0,
};

const makeDeletionImpact = (
  overrides: Partial<LLMProfileDeletionImpactDTO> = {},
): LLMProfileDeletionImpactDTO => ({
  profile_id: selectedLlmProfile.id,
  profile_name: selectedLlmProfile.name,
  model_name: selectedLlmProfile.model_name,
  is_default: true,
  can_delete: true,
  revision: "a".repeat(64),
  references: emptyLLMReferences,
  automatic_actions: {
    cancel_email_task_ids: [],
    cancel_match_analysis_job_ids: [],
    cancel_crawl_job_ids: [],
  },
  blockers: [],
  warnings: [],
  ...overrides,
});

const renderPage = () =>
  render(
    <MemoryRouter>
      <ProfilePage />
    </MemoryRouter>,
  );

const expectToAppearBefore = (first: HTMLElement, second: HTMLElement) => {
  expect(first.compareDocumentPosition(second)).toBe(
    Node.DOCUMENT_POSITION_FOLLOWING,
  );
};

describe("ProfilePage onboarding", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockedRequestWorkspaceDraftGuard.mockResolvedValue(true);
    mockedRegisterWorkspaceDraftGuard.mockImplementation(() => vi.fn());
    mockedChoose.mockResolvedValue("cancel");
    const template = {
      id: 1,
      name: "博士申请默认模板",
      recommended_generation_mode: "template" as const,
      subject: "测试主题",
      body_text: "测试正文",
      body_html: "<p>测试正文</p>",
      is_ready: true,
      is_default: true,
      archived_at: null,
      created_at: "2026-04-22T00:00:00Z",
      updated_at: "2026-04-22T00:00:00Z",
    };
    mockedListOutreachTemplates.mockResolvedValue([template]);
    mockedUpdateOutreachTemplate.mockImplementation(
      async (_templateId: number, payload: Record<string, unknown>) => ({
        ...template,
        recommended_generation_mode:
          payload.recommended_generation_mode ??
          template.recommended_generation_mode,
        subject: payload.subject ?? null,
        body_text: payload.body_text ?? null,
        body_html: payload.body_html ?? null,
      }),
    );
    window.HTMLElement.prototype.scrollIntoView = vi.fn();
    vi.mocked(updateIdentity).mockResolvedValue({
      ...selectedIdentity,
      outreach_template_body_text: "富文本更新",
      outreach_template_body_html: "<p>富文本更新</p>",
    });
    mockedGetTestComposeThread.mockResolvedValue({
      identity: {
        id: selectedIdentity.id,
        name: selectedIdentity.name,
        profile_name: selectedIdentity.profile_name,
        sender_name: selectedIdentity.sender_name,
        email_address: selectedIdentity.email_address,
      },
      llm_profile: {
        id: selectedLlmProfile.id,
        name: selectedLlmProfile.name,
        provider: selectedLlmProfile.provider,
        model_name: selectedLlmProfile.model_name,
      },
      material_options: [],
      draft: {
        subject: null,
        body_text: "",
        body_html: null,
        selected_material_ids: [],
      },
      history: [],
    });
    mockedGetTestComposeStatus.mockResolvedValue({
      completed: false,
    });
    mockedUseSelectionContext.mockReturnValue({
      identities: [selectedIdentity],
      llmProfiles: [selectedLlmProfile],
      selectedIdentityId: selectedIdentity.id,
      selectedLlmProfileId: selectedLlmProfile.id,
      selectedIdentity,
      selectedLlmProfile,
      setSelectedIdentityId: vi.fn(),
      setSelectedLlmProfileId: vi.fn(),
      refreshSelections: vi.fn(),
      loading: false,
    });
    mockedUseDesktopBackend.mockReturnValue({
      isDesktop: false,
      isReady: true,
      disableReason: null,
      status: null,
    });
  });

  const openSetupSection = (name: string) => {
    fireEvent.click(screen.getByRole("button", { name: new RegExp(`^${name}`) }));
  };

  it("shows setup recommendations with completion state", async () => {
    renderPage();

    expect(
      await screen.findByRole("heading", { name: "首次配置" }),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /1\. 发件身份/ })).toHaveTextContent(
      "已完成",
    );
    expect(
      screen.getByRole("button", { name: /2\. 材料与模板/ }),
    ).toHaveTextContent("待完成");
    expect(screen.getByRole("button", { name: /3\. 模型配置/ })).toHaveTextContent(
      "已完成",
    );
    expect(screen.getByRole("button", { name: /4\. 测试写信/ })).toHaveTextContent(
      "待完成",
    );
  });

  it("shows descriptions on all four setup cards", async () => {
    renderPage();

    expect(
      await screen.findByText("管理发件邮箱与收发设置。"),
    ).toBeInTheDocument();
    expect(screen.getByText("准备匹配材料和发信模板。"))
      .toBeInTheDocument();
    expect(screen.getByText("连接并测试用于写信的 AI 模型。"))
      .toBeInTheDocument();
    expect(screen.getByText("先给自己发送一封测试邮件。"))
      .toBeInTheDocument();
  });

  it("shows concrete blockers instead of attempting an unsafe model deletion", async () => {
    vi.mocked(getLLMProfileDeletionImpact).mockResolvedValue(
      makeDeletionImpact({
        can_delete: false,
        references: {
          ...emptyLLMReferences,
          batch_tasks: 1,
          email_tasks: 3,
        },
        blockers: [
          {
            kind: "draft_generation",
            label: "正在生成的 AI 草稿",
            count: 2,
            entity_ids: [41, 42],
            surface: "任务中心 > 发送计划或批量任务详情",
          },
        ],
      }),
    );

    renderPage();
    openSetupSection("模型配置");
    fireEvent.click(await screen.findByRole("button", { name: "删除" }));

    expect(
      await screen.findByRole("heading", { name: "暂时无法删除模型配置" }),
    ).toBeInTheDocument();
    expect(screen.getByText(/正在生成的 AI 草稿：2 项/)).toHaveTextContent(
      "ID 41、42",
    );
    expect(screen.getByText("批量活动").parentElement).toHaveTextContent(
      "批量活动1",
    );
    expect(screen.getByText("邮件任务").parentElement).toHaveTextContent(
      "邮件任务3",
    );
    expect(screen.queryByRole("button", { name: "确认删除" })).not.toBeInTheDocument();
    expect(deleteLLMProfile).not.toHaveBeenCalled();
  });

  it("retires a default model with the explicitly selected replacement", async () => {
    const replacementProfile: LLMProfileDTO = {
      ...selectedLlmProfile,
      id: 2,
      name: "备用模型",
      model_name: "gpt-backup",
      is_default: false,
    };
    const refreshSelections = vi.fn().mockResolvedValue(undefined);
    const setSelectedLlmProfileId = vi.fn();
    mockedUseSelectionContext.mockReturnValue({
      identities: [selectedIdentity],
      llmProfiles: [selectedLlmProfile, replacementProfile],
      selectedIdentityId: selectedIdentity.id,
      selectedLlmProfileId: selectedLlmProfile.id,
      selectedIdentity,
      selectedLlmProfile,
      setSelectedIdentityId: vi.fn(),
      setSelectedLlmProfileId,
      refreshSelections,
      loading: false,
    });
    const impact = makeDeletionImpact({
      references: { ...emptyLLMReferences, email_logs: 4 },
    });
    vi.mocked(getLLMProfileDeletionImpact).mockResolvedValue(impact);
    vi.mocked(deleteLLMProfile).mockResolvedValue({
      ok: true,
      profile_id: selectedLlmProfile.id,
      profile_name: selectedLlmProfile.name,
      references_preserved: impact.references,
      invalidated_plan_count: 0,
      default_profile_id: replacementProfile.id,
      canceled_email_task_ids: [],
      canceled_match_analysis_job_ids: [],
      canceled_crawl_job_ids: [],
    });

    renderPage();
    openSetupSection("模型配置");
    fireEvent.click(await screen.findByRole("button", { name: "删除" }));
    const replacementSelect = await screen.findByLabelText("删除后的默认模型");
    fireEvent.change(replacementSelect, {
      target: { value: String(replacementProfile.id) },
    });
    fireEvent.click(screen.getByRole("button", { name: "确认删除" }));

    await waitFor(() => {
      expect(deleteLLMProfile).toHaveBeenCalledWith(
        selectedLlmProfile.id,
        impact.revision,
        replacementProfile.id,
      );
      expect(setSelectedLlmProfileId).toHaveBeenCalledWith(replacementProfile.id);
      expect(refreshSelections).toHaveBeenCalled();
      expect(mockedNotifySuccess).toHaveBeenCalledWith(
        "已删除模型配置“测试模型”",
        expect.stringContaining("关联记录已保留"),
      );
    });
  });

  it("explains that independent outreach templates are retained", async () => {
    vi.mocked(getLLMProfileDeletionImpact).mockResolvedValue(
      makeDeletionImpact({
        warnings: [
          "API Key、服务地址和模型级提示词会被清除。",
          "发信模板不会删除。",
        ],
      }),
    );

    renderPage();
    openSetupSection("模型配置");
    fireEvent.click(await screen.findByRole("button", { name: "删除" }));

    expect(await screen.findByText("发信模板不会删除。历史邮件、任务和分析记录会保留。"))
      .toBeInTheDocument();
    const dialog = screen.getByRole("dialog", { name: "删除模型配置" });
    expect(dialog).toHaveClass("rounded-[30px]");
    expect(dialog).toHaveClass(
      "bg-[linear-gradient(180deg,rgba(255,252,246,0.98),rgba(255,245,233,0.95))]",
    );
    expect(
      screen.getByRole("button", { name: "关闭确认弹层" }),
    ).toBeInTheDocument();
    expect(
      screen.getByText(/暂停或失败的任务不会自动继续/),
    ).toBeInTheDocument();
    expect(screen.queryByText(/自定义提示词会一并清除/)).not.toBeInTheDocument();
  });

  it("shows queued work that will be canceled automatically", async () => {
    vi.mocked(getLLMProfileDeletionImpact).mockResolvedValue(
      makeDeletionImpact({
        automatic_actions: {
          cancel_email_task_ids: [31],
          cancel_match_analysis_job_ids: [41],
          cancel_crawl_job_ids: [51],
        },
      }),
    );

    renderPage();
    openSetupSection("模型配置");
    fireEvent.click(await screen.findByRole("button", { name: "删除" }));

    expect(
      await screen.findByRole("heading", { name: "确认删除后会自动取消" }),
    ).toBeInTheDocument();
    expect(screen.getByText("等待生成草稿的邮件任务：ID 31")).toBeInTheDocument();
    expect(screen.getByText("匹配分析任务：ID 41")).toBeInTheDocument();
    expect(screen.getByText("智能抓取或信息补全任务：ID 51")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "确认删除" })).toBeInTheDocument();
  });

  it("opens contextual setup guides from the summary, sections, and difficult fields", async () => {
    expect(PROFILE_HELP_LINKS).toEqual({
      firstRun:
        "https://juniexd.github.io/AutoEmailSender/docs/first-run",
      mailAuthorization:
        "https://juniexd.github.io/AutoEmailSender/docs/first-run#mail-authorization-code",
      llmConfiguration:
        "https://juniexd.github.io/AutoEmailSender/docs/first-run#llm-configuration",
    });

    renderPage();

    const fullGuide = await screen.findByRole("link", {
      name: "查看完整配置教程",
    });
    expect(fullGuide).toHaveAttribute("href", PROFILE_HELP_LINKS.firstRun);
    fireEvent.click(fullGuide);
    expect(mockedOpenExternalHttpUrl).toHaveBeenLastCalledWith(
      PROFILE_HELP_LINKS.firstRun,
    );

    const mailGuide = screen.getByRole("link", { name: "邮箱配置教程" });
    expect(mailGuide).toHaveAttribute(
      "href",
      PROFILE_HELP_LINKS.mailAuthorization,
    );
    const mailDescription = screen.getByText("管理发件邮箱与收发设置。");
    expect(mailGuide.parentElement).toBe(mailDescription.parentElement);
    expect(mailGuide.parentElement).toHaveClass("gap-x-1", "flex-wrap");
    expect(mailGuide).toHaveClass("min-h-0", "px-1", "leading-5");
    fireEvent.click(mailGuide);
    expect(screen.getByRole("button", { name: /^发件身份/ })).toHaveAttribute(
      "aria-expanded",
      "false",
    );
    expect(mockedOpenExternalHttpUrl).toHaveBeenLastCalledWith(
      PROFILE_HELP_LINKS.mailAuthorization,
    );

    openSetupSection("发件身份");
    const authorizationGuide = screen.getByRole("link", {
      name: "如何获取授权码",
    });
    expect(authorizationGuide).toHaveAttribute(
      "href",
      PROFILE_HELP_LINKS.mailAuthorization,
    );
    expect(authorizationGuide.parentElement).toHaveClass(
      "justify-start",
      "gap-x-1",
    );
    expect(authorizationGuide.parentElement).toHaveClass("min-h-[22px]");
    expect(authorizationGuide).toHaveClass(
      "min-h-0",
      "px-1",
      "py-0",
      "leading-5",
    );
    expect(screen.getByText(/不是邮箱登录密码/)).toBeInTheDocument();

    const modelGuide = screen.getByRole("link", { name: "模型配置教程" });
    expect(modelGuide).toHaveAttribute(
      "href",
      PROFILE_HELP_LINKS.llmConfiguration,
    );
    const modelDescription = screen.getByText(
      "连接并测试用于写信的 AI 模型。",
    );
    expect(modelGuide.parentElement).toBe(modelDescription.parentElement);
    expect(modelGuide).toHaveClass("min-h-0", "px-1", "leading-5");
    fireEvent.click(modelGuide);
    expect(screen.getByRole("button", { name: /^模型配置/ })).toHaveAttribute(
      "aria-expanded",
      "false",
    );
    openSetupSection("模型配置");
    expect(
      screen.getByRole("link", { name: "查看填写示例" }),
    ).toHaveAttribute("href", PROFILE_HELP_LINKS.llmConfiguration);
    expect(
      screen.getByRole("link", { name: "如何获取 API Key" }),
    ).toHaveAttribute("href", PROFILE_HELP_LINKS.llmConfiguration);
    expect(screen.getByLabelText(/API Base URL/)).toBeInTheDocument();
    expect(screen.getByText(/不是平台官网地址/)).toBeInTheDocument();
  });

  it("follows a top-bar identity change in the identity editor", async () => {
    const secondIdentity: IdentityDTO = {
      ...selectedIdentity,
      id: 2,
      profile_name: "备用身份",
      name: "备用身份",
      email_address: "backup@example.com",
      is_default: false,
    };
    const contextBase = {
      identities: [selectedIdentity, secondIdentity],
      llmProfiles: [selectedLlmProfile],
      selectedLlmProfileId: selectedLlmProfile.id,
      selectedLlmProfile,
      setSelectedIdentityId: vi.fn(),
      setSelectedLlmProfileId: vi.fn(),
      refreshSelections: vi.fn(),
      loading: false,
    };
    mockedUseSelectionContext.mockReturnValue({
      ...contextBase,
      selectedIdentityId: selectedIdentity.id,
      selectedIdentity,
    });
    const view = renderPage();
    openSetupSection("发件身份");
    expect(await screen.findByLabelText("身份名称")).toHaveValue("博士申请配置");

    mockedUseSelectionContext.mockReturnValue({
      ...contextBase,
      selectedIdentityId: secondIdentity.id,
      selectedIdentity: secondIdentity,
    });
    view.rerender(
      <MemoryRouter>
        <ProfilePage />
      </MemoryRouter>,
    );

    await waitFor(() => {
      expect(screen.getByLabelText("身份名称")).toHaveValue("备用身份");
    });
  });

  it("follows an approved top-bar identity change from a new identity editor", async () => {
    const secondIdentity: IdentityDTO = {
      ...selectedIdentity,
      id: 2,
      profile_name: "备用身份",
      name: "备用身份",
      email_address: "backup@example.com",
      is_default: false,
    };
    const contextBase = {
      identities: [selectedIdentity, secondIdentity],
      llmProfiles: [selectedLlmProfile],
      selectedLlmProfileId: selectedLlmProfile.id,
      selectedLlmProfile,
      setSelectedIdentityId: vi.fn(),
      setSelectedLlmProfileId: vi.fn(),
      refreshSelections: vi.fn(),
      loading: false,
    };
    mockedUseSelectionContext.mockReturnValue({
      ...contextBase,
      selectedIdentityId: selectedIdentity.id,
      selectedIdentity,
    });
    const view = renderPage();
    openSetupSection("发件身份");
    fireEvent.click(
      await screen.findByRole("button", { name: "新建发件身份" }),
    );
    await waitFor(() => {
      expect(screen.getByLabelText("身份名称")).toHaveValue("");
    });
    fireEvent.change(screen.getByLabelText("身份名称"), {
      target: { value: "未保存身份" },
    });

    mockedUseSelectionContext.mockReturnValue({
      ...contextBase,
      selectedIdentityId: secondIdentity.id,
      selectedIdentity: secondIdentity,
    });
    view.rerender(
      <MemoryRouter>
        <ProfilePage />
      </MemoryRouter>,
    );

    await waitFor(() => {
      expect(screen.getByLabelText("身份名称")).toHaveValue("备用身份");
    });
  });

  it("blocks identity switching when existing identity edits are kept", async () => {
    const secondIdentity: IdentityDTO = {
      ...selectedIdentity,
      id: 2,
      profile_name: "备用身份",
      name: "备用身份",
      email_address: "backup@example.com",
      is_default: false,
    };
    const setSelectedIdentityId = vi.fn();
    mockedUseSelectionContext.mockReturnValue({
      identities: [selectedIdentity, secondIdentity],
      llmProfiles: [selectedLlmProfile],
      selectedIdentityId: selectedIdentity.id,
      selectedLlmProfileId: selectedLlmProfile.id,
      selectedIdentity,
      selectedLlmProfile,
      setSelectedIdentityId,
      setSelectedLlmProfileId: vi.fn(),
      refreshSelections: vi.fn(),
      loading: false,
    });

    renderPage();
    openSetupSection("发件身份");
    fireEvent.change(await screen.findByLabelText("身份名称"), {
      target: { value: "尚未保存的名称" },
    });
    mockedRequestWorkspaceDraftGuard.mockImplementation(async (request) => {
      const guard = mockedRegisterWorkspaceDraftGuard.mock.calls.at(-1)?.[0];
      return guard ? guard(request) : true;
    });
    mockedChoose.mockResolvedValue("cancel");

    fireEvent.click(screen.getByRole("button", { name: /备用身份/ }));

    await waitFor(() => {
      expect(mockedChoose).toHaveBeenCalledWith(
        expect.objectContaining({ title: "保存身份修改？" }),
      );
    });
    expect(setSelectedIdentityId).not.toHaveBeenCalled();
    expect(screen.getByLabelText("身份名称")).toHaveValue("尚未保存的名称");
  });

  it("updates the global identity when switching in the identity editor", async () => {
    const secondIdentity: IdentityDTO = {
      ...selectedIdentity,
      id: 2,
      profile_name: "备用身份",
      name: "备用身份",
      email_address: "backup@example.com",
      is_default: false,
    };
    const setSelectedIdentityId = vi.fn();
    mockedUseSelectionContext.mockReturnValue({
      identities: [selectedIdentity, secondIdentity],
      llmProfiles: [selectedLlmProfile],
      selectedIdentityId: selectedIdentity.id,
      selectedLlmProfileId: selectedLlmProfile.id,
      selectedIdentity,
      selectedLlmProfile,
      setSelectedIdentityId,
      setSelectedLlmProfileId: vi.fn(),
      refreshSelections: vi.fn(),
      loading: false,
    });

    renderPage();
    openSetupSection("发件身份");
    fireEvent.click(await screen.findByRole("button", { name: /备用身份/ }));

    await waitFor(() => {
      expect(mockedRequestWorkspaceDraftGuard).toHaveBeenCalledTimes(1);
      expect(mockedRequestWorkspaceDraftGuard).toHaveBeenCalledWith({
        nextIdentityEditorId: secondIdentity.id,
        nextIdentityId: secondIdentity.id,
      });
      expect(setSelectedIdentityId).toHaveBeenCalledWith(secondIdentity.id);
    });
  });

  it("keeps setup recommendations hidden until incomplete setup is confirmed", async () => {
    let resolveTestComposeStatus!: (value: { completed: boolean }) => void;
    mockedUseSelectionContext.mockReturnValue({
      identities: [selectedIdentityWithMaterial],
      llmProfiles: [selectedLlmProfile],
      selectedIdentityId: selectedIdentityWithMaterial.id,
      selectedLlmProfileId: selectedLlmProfile.id,
      selectedIdentity: selectedIdentityWithMaterial,
      selectedLlmProfile,
      setSelectedIdentityId: vi.fn(),
      setSelectedLlmProfileId: vi.fn(),
      refreshSelections: vi.fn(),
      loading: false,
    });
    mockedGetTestComposeStatus.mockReturnValueOnce(
      new Promise<{ completed: boolean }>((resolve) => {
        resolveTestComposeStatus = resolve;
      }),
    );

    renderPage();

    await waitFor(() => {
      expect(mockedGetTestComposeStatus).toHaveBeenCalledWith(
        selectedIdentityWithMaterial.id,
      );
    });
    expect(
      screen.queryByRole("heading", { name: "首次配置" }),
    ).not.toBeInTheDocument();

    resolveTestComposeStatus({ completed: false });

    expect(
      await screen.findByRole("heading", { name: "首次配置" }),
    ).toBeInTheDocument();
  });

  it("hides setup recommendations after all four stages are completed", async () => {
    mockedUseSelectionContext.mockReturnValue({
      identities: [selectedIdentityWithMaterial],
      llmProfiles: [selectedLlmProfile],
      selectedIdentityId: selectedIdentityWithMaterial.id,
      selectedLlmProfileId: selectedLlmProfile.id,
      selectedIdentity: selectedIdentityWithMaterial,
      selectedLlmProfile,
      setSelectedIdentityId: vi.fn(),
      setSelectedLlmProfileId: vi.fn(),
      refreshSelections: vi.fn(),
      loading: false,
    });
    mockedGetTestComposeStatus.mockResolvedValueOnce({
      completed: true,
    });

    renderPage();

    await waitFor(() => {
      expect(
        screen.queryByRole("heading", { name: "首次配置" }),
      ).not.toBeInTheDocument();
    });
  });

  it("marks test compose as completed when the current thread has sent history", async () => {
    mockedGetTestComposeStatus.mockResolvedValueOnce({
      completed: true,
    });
    mockedGetTestComposeThread.mockResolvedValueOnce({
      identity: {
        id: selectedIdentity.id,
        name: selectedIdentity.name,
        profile_name: selectedIdentity.profile_name,
        sender_name: selectedIdentity.sender_name,
        email_address: selectedIdentity.email_address,
      },
      llm_profile: {
        id: selectedLlmProfile.id,
        name: selectedLlmProfile.name,
        provider: selectedLlmProfile.provider,
        model_name: selectedLlmProfile.model_name,
      },
      material_options: [],
      draft: {
        subject: null,
        body_text: "",
        body_html: null,
        selected_material_ids: [],
      },
      history: [
        {
          id: 1,
          recipient_email: selectedIdentity.email_address,
          subject: "测试主题",
          content: "测试正文",
          content_html: "<p>测试正文</p>",
          status: "sent",
          rfc_message_id: "<test@example.com>",
          failure_summary: null,
          created_at: "2026-04-23T08:00:00Z",
        },
      ],
    });

    renderPage();

    await waitFor(() => {
      expect(screen.getByRole("button", { name: /4\. 测试写信/ })).toHaveTextContent(
        "已完成",
      );
    });
    expect(screen.getByText("已发送测试邮件")).toBeInTheDocument();
  });

  it("keeps test compose completed for the identity after switching to another llm profile", async () => {
    const backupLlmProfile: LLMProfileDTO = {
      ...selectedLlmProfile,
      id: 2,
      name: "备用模型",
      model_name: "gpt-backup",
      is_default: false,
    };
    mockedUseSelectionContext.mockReturnValue({
      identities: [selectedIdentity],
      llmProfiles: [selectedLlmProfile, backupLlmProfile],
      selectedIdentityId: selectedIdentity.id,
      selectedLlmProfileId: backupLlmProfile.id,
      selectedIdentity,
      selectedLlmProfile: backupLlmProfile,
      setSelectedIdentityId: vi.fn(),
      setSelectedLlmProfileId: vi.fn(),
      refreshSelections: vi.fn(),
      loading: false,
    });
    mockedGetTestComposeStatus.mockResolvedValueOnce({
      completed: true,
    });

    renderPage();

    await waitFor(() => {
      expect(
        screen.getByRole("button", { name: /4\. 测试写信/ }),
      ).toHaveTextContent("已完成");
    });
    expect(mockedGetTestComposeStatus).toHaveBeenCalledWith(selectedIdentity.id);
  });

  it("keeps setup sections collapsed by default and opens them from recommendations", async () => {
    renderPage();

    expect(screen.getByRole("button", { name: /^发件身份/ })).toHaveAttribute(
      "aria-expanded",
      "false",
    );
    expect(screen.queryByLabelText("身份名称")).not.toBeInTheDocument();

    fireEvent.click(
      await screen.findByRole("button", { name: /1\. 发件身份/ }),
    );

    expect(screen.getByRole("button", { name: /^发件身份/ })).toHaveAttribute(
      "aria-expanded",
      "true",
    );
    expect(await screen.findByLabelText("身份名称")).toHaveValue("博士申请配置");
    await waitFor(() => {
      expect(window.HTMLElement.prototype.scrollIntoView).toHaveBeenCalled();
    });
  });

  it("toggles every setup card from its description", () => {
    renderPage();

    const setupCards = [
      ["发件身份", "管理发件邮箱与收发设置。"],
      ["材料与模板", "准备匹配材料和发信模板。"],
      ["模型配置", "连接并测试用于写信的 AI 模型。"],
      ["测试写信", "先给自己发送一封测试邮件。"],
    ] as const;

    for (const [title, description] of setupCards) {
      const toggle = screen.getByRole("button", {
        name: new RegExp(`^${title}`),
      });

      fireEvent.click(screen.getByText(description));
      expect(toggle).toHaveAttribute("aria-expanded", "true");

      fireEvent.click(screen.getByText(description));
      expect(toggle).toHaveAttribute("aria-expanded", "false");
    }
  });

  it("animates setup section content while opening and closing", async () => {
    renderPage();

    fireEvent.click(screen.getByRole("button", { name: /^发件身份/ }));

    const content = await screen.findByLabelText("身份名称").then(() =>
      document.getElementById("identity-setup-content"),
    );

    expect(content).toHaveClass("collapsible-card-content");
    expect(content).toHaveAttribute("data-state", "open");

    fireEvent.click(screen.getByRole("button", { name: /^发件身份/ }));

    expect(content).toHaveAttribute("data-state", "closed");

    fireEvent.transitionEnd(content!, { propertyName: "grid-template-rows" });

    expect(document.getElementById("identity-setup-content")).not.toBeInTheDocument();
  });

  it("renders the three setup sections before the final test section", () => {
    renderPage();

    const identitySection = screen.getByRole("heading", { name: "发件身份" });
    const materialsSection = screen.getByRole("heading", {
      name: "材料与模板",
    });
    const modelSection = screen.getByRole("heading", { name: "模型配置" });
    const finishSection = screen.getByRole("heading", { name: "测试写信" });

    expectToAppearBefore(identitySection, materialsSection);
    expectToAppearBefore(materialsSection, modelSection);
    expectToAppearBefore(modelSection, finishSection);
  });

  it("shows separate profile name and sender name fields", async () => {
    renderPage();
    openSetupSection("发件身份");

    expect(await screen.findByLabelText("身份名称")).toHaveValue("博士申请配置");
    expect(screen.getByLabelText("发件人姓名")).toHaveValue("王同学");
    expect(screen.queryByLabelText("匹配阈值")).not.toBeInTheDocument();
    expect(screen.queryByText(/匹配阈值/)).not.toBeInTheDocument();
  });

  it("saves sender identity even when the default outreach template is empty", async () => {
    const identityWithoutTemplate: IdentityDTO = {
      ...selectedIdentity,
      outreach_template_subject: null,
      outreach_template_body_text: null,
      outreach_template_body_html: null,
    };
    mockedUseSelectionContext.mockReturnValue({
      identities: [identityWithoutTemplate],
      llmProfiles: [selectedLlmProfile],
      selectedIdentityId: identityWithoutTemplate.id,
      selectedLlmProfileId: selectedLlmProfile.id,
      selectedIdentity: identityWithoutTemplate,
      selectedLlmProfile,
      setSelectedIdentityId: vi.fn(),
      setSelectedLlmProfileId: vi.fn(),
      refreshSelections: vi.fn(),
      loading: false,
    });

    renderPage();
    openSetupSection("发件身份");

    fireEvent.click(await screen.findByRole("button", { name: "保存身份" }));

    await waitFor(() => {
      expect(updateIdentity).toHaveBeenCalledWith(
        identityWithoutTemplate.id,
        expect.objectContaining({
          outreach_template_subject: null,
          outreach_template_body_text: null,
          outreach_template_body_html: null,
        }),
      );
    });
  });

  it("disables identity saving while desktop backend is not ready", async () => {
    mockedUseDesktopBackend.mockReturnValue({
      isDesktop: true,
      isReady: false,
      disableReason: "系统准备中",
      status: {
        state: "starting",
        phase: "migrating_database",
        message: "正在检查和升级本地数据",
        elapsedSeconds: 12,
        slowStartup: false,
        verySlowStartup: false,
      },
    });

    renderPage();
    openSetupSection("发件身份");

    const saveButton = await screen.findByRole("button", { name: "系统准备中" });
    expect(saveButton).toBeDisabled();
    expect(
      screen.getByText("本地数据准备完成后即可继续操作，已填写内容不会丢失。"),
    ).toBeInTheDocument();
  });

  it("renders the material entry and connection testing area for an existing identity", () => {
    renderPage();
    openSetupSection("材料与模板");
    openSetupSection("发件身份");

    expect(screen.getByText("全局材料库")).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "打开材料库" }),
    ).toBeInTheDocument();
    expect(screen.getByText("邮箱连接测试")).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "测试 SMTP" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "测试 IMAP" }),
    ).toBeInTheDocument();
  });

  it("reveals the smtp authorization code so it can be selected and copied", () => {
    renderPage();
    openSetupSection("发件身份");

    const passwordInput = screen.getByLabelText(/邮箱授权码/);
    const smtpPortInput = screen.getByLabelText(/SMTP 端口/);
    expectToAppearBefore(passwordInput, smtpPortInput);
    expect(passwordInput).toHaveAttribute("type", "password");

    fireEvent.click(screen.getByRole("button", { name: "显示授权码" }));

    expect(passwordInput).toHaveAttribute("type", "text");
    expect(passwordInput).toHaveValue("secret");
    expect(screen.getByRole("button", { name: "隐藏授权码" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );

    fireEvent.click(screen.getByRole("button", { name: "隐藏授权码" }));
    expect(passwordInput).toHaveAttribute("type", "password");
  });

  it("shows smtp test failures as failures when the backend returns ok false", async () => {
    vi.mocked(testIdentitySmtp).mockResolvedValueOnce({
      ok: false,
      message:
        "SMTP 登录凭据编码失败：UnicodeEncodeError(error_code=SMTP_PASSWORD_NON_ASCII, field=smtp_password, encoding=ascii, start=6, end=7, reason=ordinal not in range(128))",
      host: "smtp.example.com",
      possible_cause:
        "邮箱授权码包含 SMTP 登录不支持的中文、全角符号或不可见字符。请从邮箱设置页面重新复制客户端授权码。",
    });

    renderPage();
    openSetupSection("发件身份");

    fireEvent.click(screen.getByRole("button", { name: "测试 SMTP" }));

    expect(await screen.findByText(/上次测试：SMTP 失败/)).toBeInTheDocument();
    const rawError = screen.getByText(/error_code=SMTP_PASSWORD_NON_ASCII/);
    expect(rawError).toBeInTheDocument();
    expect(rawError).toHaveClass("whitespace-nowrap", "overflow-x-auto");
    expect(rawError).not.toHaveClass("whitespace-pre-wrap", "break-all");
    expect(screen.getByText("可能原因")).toBeInTheDocument();
    expect(screen.getByText(/客户端授权码/)).toBeInTheDocument();
    expect(screen.getByText("原始报错")).toBeInTheDocument();
    expect(
      screen.getByRole("link", { name: "按邮箱配置教程逐项检查" }),
    ).toHaveAttribute("href", PROFILE_HELP_LINKS.mailAuthorization);
    expect(mockedNotifyError).toHaveBeenCalledTimes(1);
    expect(mockedNotifyError.mock.calls[0]?.[0]).toContain("SMTP");
    expect(mockedNotifyError.mock.calls[0]?.[1]).toBe(
      "SMTP 登录凭据编码失败：UnicodeEncodeError(error_code=SMTP_PASSWORD_NON_ASCII, field=smtp_password, encoding=ascii, start=6, end=7, reason=ordinal not in range(128))",
    );
    expect(mockedNotifySuccess).not.toHaveBeenCalled();
  });

  it("silently saves the current form before testing smtp", async () => {
    const savedIdentity = {
      ...selectedIdentity,
      id: 42,
      smtp_password: "updated-secret",
      imap_password: "updated-secret",
    };
    vi.mocked(updateIdentity).mockResolvedValueOnce(savedIdentity);
    vi.mocked(testIdentitySmtp).mockResolvedValueOnce({
      ok: true,
      message: "SMTP 连接测试成功",
      host: savedIdentity.smtp_host,
      possible_cause: null,
    });

    renderPage();
    openSetupSection("发件身份");
    fireEvent.change(screen.getByLabelText(/邮箱授权码/), {
      target: { value: "updated-secret" },
    });

    fireEvent.click(screen.getByRole("button", { name: "测试 SMTP" }));

    await waitFor(() => {
      expect(updateIdentity).toHaveBeenCalledWith(
        selectedIdentity.id,
        expect.objectContaining({
          smtp_password: "updated-secret",
          imap_password: "updated-secret",
        }),
      );
    });
    await waitFor(() => {
      expect(testIdentitySmtp).toHaveBeenCalledWith(savedIdentity.id);
    });
    expect(updateIdentity.mock.invocationCallOrder[0]).toBeLessThan(
      vi.mocked(testIdentitySmtp).mock.invocationCallOrder[0] ??
        Number.POSITIVE_INFINITY,
    );
    expect(mockedNotifySuccess).toHaveBeenCalledTimes(1);
    expect(mockedNotifySuccess).toHaveBeenCalledWith(
      "SMTP 连接测试成功",
      "SMTP 连接测试成功",
    );
  });

  it("does not test smtp when the automatic save fails", async () => {
    vi.mocked(updateIdentity).mockRejectedValueOnce(new Error("保存失败"));

    renderPage();
    openSetupSection("发件身份");
    fireEvent.click(screen.getByRole("button", { name: "测试 SMTP" }));

    await waitFor(() => {
      expect(mockedNotifyError).toHaveBeenCalledWith("身份保存失败", "保存失败");
    });
    expect(testIdentitySmtp).not.toHaveBeenCalled();
    expect(mockedNotifySuccess).not.toHaveBeenCalled();
  });

  it("silently saves the tested llm form after a successful model test", async () => {
    const refreshSelections = vi.fn();
    const testedBaseUrl = "https://api.deepseek.com";
    const successfulResult = {
      ok: true,
      message: "模型可用性测试成功",
      resolved_base_url: testedBaseUrl,
      request_url: `${testedBaseUrl}/chat/completions`,
      attempted_urls: [`${testedBaseUrl}/chat/completions`],
      endpoint_kind: "chat_completions",
      status_code: 200,
      duration_ms: 100,
      consumes_tokens: true,
      prompt_tokens: 7,
      completion_tokens: 1,
      total_tokens: 8,
      response_preview: "OK",
    };
    vi.mocked(testLLMProfilePreview).mockResolvedValueOnce(successfulResult);
    vi.mocked(updateLLMProfile).mockResolvedValueOnce({
      ...selectedLlmProfile,
      api_base_url: testedBaseUrl,
    });
    mockedUseSelectionContext.mockReturnValue({
      identities: [selectedIdentity],
      llmProfiles: [selectedLlmProfile],
      selectedIdentityId: selectedIdentity.id,
      selectedLlmProfileId: selectedLlmProfile.id,
      selectedIdentity,
      selectedLlmProfile,
      setSelectedIdentityId: vi.fn(),
      setSelectedLlmProfileId: vi.fn(),
      refreshSelections,
      loading: false,
    });

    renderPage();
    openSetupSection("模型配置");
    fireEvent.change(screen.getByLabelText(/API Base URL/), {
      target: { value: testedBaseUrl },
    });
    fireEvent.click(screen.getByRole("button", { name: "测试模型" }));

    await waitFor(() => {
      expect(updateLLMProfile).toHaveBeenCalledWith(
        selectedLlmProfile.id,
        expect.objectContaining({ api_base_url: testedBaseUrl }),
      );
    });
    expect(testLLMProfilePreview).toHaveBeenCalledWith(
      expect.objectContaining({ api_base_url: testedBaseUrl }),
    );
    expect(
      vi.mocked(testLLMProfilePreview).mock.invocationCallOrder[0],
    ).toBeLessThan(
      vi.mocked(updateLLMProfile).mock.invocationCallOrder[0] ??
        Number.POSITIVE_INFINITY,
    );
    expect(refreshSelections).toHaveBeenCalled();
    expect(mockedNotifySuccess).not.toHaveBeenCalled();
  });

  it("does not overwrite the saved llm profile when the model test fails", async () => {
    vi.mocked(testLLMProfilePreview).mockResolvedValueOnce({
      ok: false,
      message: "模型接口返回错误 401",
      resolved_base_url: "https://invalid.example.com",
      request_url: "https://invalid.example.com/chat/completions",
      attempted_urls: ["https://invalid.example.com/chat/completions"],
      endpoint_kind: "chat_completions",
      status_code: 401,
      duration_ms: 100,
      consumes_tokens: true,
      prompt_tokens: null,
      completion_tokens: null,
      total_tokens: null,
      response_preview: null,
    });

    renderPage();
    openSetupSection("模型配置");
    fireEvent.change(screen.getByLabelText(/API Base URL/), {
      target: { value: "https://invalid.example.com" },
    });
    fireEvent.click(screen.getByRole("button", { name: "测试模型" }));

    expect(await screen.findByText("模型接口返回错误 401")).toBeInTheDocument();
    expect(
      screen.getByRole("link", { name: "查看模型配置排查步骤" }),
    ).toHaveAttribute("href", PROFILE_HELP_LINKS.llmConfiguration);
    expect(updateLLMProfile).not.toHaveBeenCalled();
  });

  it("reports when a successful model test cannot be saved", async () => {
    const testedBaseUrl = "https://api.deepseek.com";
    vi.mocked(testLLMProfilePreview).mockResolvedValueOnce({
      ok: true,
      message: "模型可用性测试成功",
      resolved_base_url: testedBaseUrl,
      request_url: `${testedBaseUrl}/chat/completions`,
      attempted_urls: [`${testedBaseUrl}/chat/completions`],
      endpoint_kind: "chat_completions",
      status_code: 200,
      duration_ms: 100,
      consumes_tokens: true,
      prompt_tokens: 7,
      completion_tokens: 1,
      total_tokens: 8,
      response_preview: "OK",
    });
    vi.mocked(updateLLMProfile).mockRejectedValueOnce(new Error("数据库写入失败"));

    renderPage();
    openSetupSection("模型配置");
    fireEvent.click(screen.getByRole("button", { name: "测试模型" }));

    expect(
      await screen.findByText(
        "模型测试成功，但配置自动保存失败：数据库写入失败",
      ),
    ).toBeInTheDocument();
    expect(mockedNotifyError).toHaveBeenCalledWith(
      "模型配置自动保存失败",
      "数据库写入失败",
    );
  });

  it("opens the material library modal from the reordered materials section", async () => {
    renderPage();
    openSetupSection("材料与模板");

    fireEvent.click(screen.getByRole("button", { name: "打开材料库" }));

    expect(
      await screen.findByRole("heading", { name: "全局材料管理" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "关闭材料库" }),
    ).toBeInTheDocument();
  });

  it("shows a material uploaded by another identity and targets the edited identity for default", async () => {
    const sharedMaterial = {
      id: 7,
      source_identity_id: 1,
      display_name: "共享简历",
      original_filename: "shared-resume.pdf",
      mime_type: "application/pdf",
      size_bytes: 2048,
      material_type: "resume" as const,
      is_primary: false,
      default_for_identity_ids: [1, 3],
      created_at: "2026-08-11T00:00:00Z",
    };
    const sourceIdentity: IdentityDTO = {
      ...selectedIdentity,
      id: 1,
      profile_name: "身份 A",
      email_address: "identity-a@example.com",
      materials: [{ ...sharedMaterial, is_primary: true }],
      current_primary_material_id: sharedMaterial.id,
      current_primary_material: { ...sharedMaterial, is_primary: true },
    };
    const targetIdentity: IdentityDTO = {
      ...selectedIdentity,
      id: 2,
      profile_name: "身份 B",
      email_address: "identity-b@example.com",
      is_default: false,
      materials: [sharedMaterial],
      current_primary_material_id: null,
      current_primary_material: null,
    };
    const refreshSelections = vi.fn().mockResolvedValue(undefined);
    vi.mocked(setPrimaryMaterial).mockResolvedValue({
      ...sharedMaterial,
      is_primary: true,
      default_for_identity_ids: [1, 2, 3],
    });
    mockedUseSelectionContext.mockReturnValue({
      identities: [sourceIdentity, targetIdentity],
      llmProfiles: [selectedLlmProfile],
      selectedIdentityId: targetIdentity.id,
      selectedLlmProfileId: selectedLlmProfile.id,
      selectedIdentity: targetIdentity,
      selectedLlmProfile,
      setSelectedIdentityId: vi.fn(),
      setSelectedLlmProfileId: vi.fn(),
      refreshSelections,
      loading: false,
    });

    renderPage();
    openSetupSection("材料与模板");
    fireEvent.click(screen.getByRole("button", { name: "打开材料库" }));

    expect(await screen.findByText("共享简历")).toBeInTheDocument();
    expect(screen.getByText("2 个身份正在使用默认")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "设为默认材料" }));

    await waitFor(() => {
      expect(setPrimaryMaterial).toHaveBeenCalledWith(
        targetIdentity.id,
        sharedMaterial.id,
      );
    });
    expect(refreshSelections).toHaveBeenCalled();
  });

  it("shows the test compose entry inside the final save section", () => {
    renderPage();
    openSetupSection("测试写信");

    const finishSection = screen.getByRole("heading", { name: "测试写信" });
    const entryLink = screen.getByRole("link", { name: "开始测试" });

    expect(screen.queryByText("第四步：测试写信")).not.toBeInTheDocument();
    expectToAppearBefore(finishSection, entryLink);
    expect(entryLink).toHaveAttribute("href", "/test-compose");
  });

  it("uses the shared rich text editor for the default outreach template modal", async () => {
    renderPage();
    openSetupSection("材料与模板");
    openSetupSection("测试写信");

    fireEvent.click(screen.getByRole("button", { name: "管理模板" }));

    expect(
      await screen.findByRole("textbox", { name: "模板正文" }),
    ).toBeInTheDocument();
    await screen.findByDisplayValue("博士申请默认模板");
    expect(screen.queryByText("默认模板正文（纯文本）")).not.toBeInTheDocument();
    expect(
      screen.queryByText("默认模板正文（HTML，可保留格式）"),
    ).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "模拟编辑默认模板正文" }));
    await waitFor(() => {
      expect(screen.getByTestId("editor-html-模板正文")).toHaveTextContent(
        "<p>富文本更新</p>",
      );
    });
    fireEvent.click(screen.getByRole("button", { name: "保存修改" }));

    await waitFor(() => {
      expect(mockedUpdateOutreachTemplate).toHaveBeenCalledWith(
        1,
        expect.objectContaining({
          body_text: "富文本更新",
          body_html: "<p>富文本更新</p>",
        }),
      );
    });
    expect(updateIdentity).not.toHaveBeenCalled();
  });

  it("explains placeholder insertion through the template editors", async () => {
    renderPage();
    openSetupSection("材料与模板");
    openSetupSection("测试写信");

    fireEvent.click(screen.getByRole("button", { name: "管理模板" }));

    expect(
      await screen.findByText(
        "用“占位符”插入导师姓名等变量，发送时自动替换。",
      ),
    ).toBeInTheDocument();
    expect(screen.queryByText("{{name}} 导师姓名")).not.toBeInTheDocument();
  });
});
