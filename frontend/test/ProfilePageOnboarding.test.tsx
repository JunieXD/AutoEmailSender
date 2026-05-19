import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ProfilePage } from "@/pages/ProfilePage";
import { testIdentitySmtp, updateIdentity } from "@/lib/api/identities";
import type { IdentityDTO, LLMProfileDTO } from "@/types";

const mockedUseSelectionContext = vi.hoisted(() => vi.fn());
const mockedUseDesktopBackend = vi.hoisted(() => vi.fn());
const mockedGetTestComposeThread = vi.hoisted(() => vi.fn());
const mockedGetTestComposeStatus = vi.hoisted(() => vi.fn());
const mockedNotifyError = vi.hoisted(() => vi.fn());
const mockedNotifySuccess = vi.hoisted(() => vi.fn());

vi.mock("@/context/SelectionContext", () => ({
  useSelectionContext: mockedUseSelectionContext,
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
    dialog: null,
  }),
}));

vi.mock("@/components/molecules/EmailTemplateEditor", () => ({
  EmailTemplateEditor: ({
    label,
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
}));

vi.mock("@/lib/api/materials", () => ({
  deleteMaterial: vi.fn(),
  getMaterialDownloadUrl: vi.fn(),
  getMaterialOpenUrl: vi.fn(),
  setPrimaryMaterial: vi.fn(),
  uploadIdentityMaterial: vi.fn(),
}));

vi.mock("@/lib/api/llmProfiles", () => ({
  createLLMProfile: vi.fn(),
  deleteLLMProfile: vi.fn(),
  fetchLLMProfileModels: vi.fn(),
  setDefaultLLMProfile: vi.fn(),
  testLLMProfile: vi.fn(),
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
  current_primary_material_id: null,
  current_primary_material: null,
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
      await screen.findByRole("heading", { name: "首次配置建议" }),
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
    expect(screen.queryByLabelText("配置名称")).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /1\. 发件身份/ }));

    expect(screen.getByRole("button", { name: /^发件身份/ })).toHaveAttribute(
      "aria-expanded",
      "true",
    );
    expect(await screen.findByLabelText("配置名称")).toHaveValue("博士申请配置");
    expect(window.HTMLElement.prototype.scrollIntoView).toHaveBeenCalled();
  });

  it("animates setup section content while opening and closing", async () => {
    renderPage();

    fireEvent.click(screen.getByRole("button", { name: /^发件身份/ }));

    const content = await screen.findByLabelText("配置名称").then(() =>
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

    expect(await screen.findByLabelText("配置名称")).toHaveValue("博士申请配置");
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

    expect(screen.getByText("材料库")).toBeInTheDocument();
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

  it("shows smtp test failures as failures when the backend returns ok false", async () => {
    vi.mocked(testIdentitySmtp).mockResolvedValueOnce({
      ok: false,
      message: "SMTP 连接失败: (535, b'Error: authentication failed')",
      host: "smtp.example.com",
    });

    renderPage();
    openSetupSection("发件身份");

    fireEvent.click(screen.getByRole("button", { name: "测试 SMTP" }));

    expect(await screen.findByText(/上次测试：SMTP 失败/)).toBeInTheDocument();
    expect(
      screen.getByText("SMTP 连接失败: (535, b'Error: authentication failed')"),
    ).toBeInTheDocument();
    expect(mockedNotifyError).toHaveBeenCalledTimes(1);
    expect(mockedNotifyError.mock.calls[0]?.[0]).toContain("SMTP");
    expect(mockedNotifyError.mock.calls[0]?.[1]).toBe(
      "SMTP 连接失败: (535, b'Error: authentication failed')",
    );
    expect(mockedNotifySuccess).not.toHaveBeenCalled();
  });

  it("opens the material library modal from the reordered materials section", async () => {
    renderPage();
    openSetupSection("材料与模板");

    fireEvent.click(screen.getByRole("button", { name: "打开材料库" }));

    expect(
      await screen.findByRole("heading", { name: "材料管理" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "关闭材料库" }),
    ).toBeInTheDocument();
  });

  it("shows the test compose entry inside the final save section", () => {
    renderPage();
    openSetupSection("测试写信");

    const finishSection = screen.getByRole("heading", { name: "测试写信" });
    const entryLink = screen.getByRole("link", { name: "进入测试写信页" });

    expect(screen.queryByText("第四步：测试写信")).not.toBeInTheDocument();
    expectToAppearBefore(finishSection, entryLink);
    expect(entryLink).toHaveAttribute("href", "/test-compose");
  });

  it("uses the shared rich text editor for the default outreach template modal", async () => {
    renderPage();
    openSetupSection("材料与模板");
    openSetupSection("测试写信");

    fireEvent.click(screen.getByRole("button", { name: "打开默认值编辑" }));

    expect(
      await screen.findByRole("textbox", { name: "默认模板正文" }),
    ).toBeInTheDocument();
    expect(screen.queryByText("默认模板正文（纯文本）")).not.toBeInTheDocument();
    expect(
      screen.queryByText("默认模板正文（HTML，可保留格式）"),
    ).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "模拟编辑默认模板正文" }));
    fireEvent.click(screen.getByRole("button", { name: "完成编辑" }));

    await waitFor(() => {
      expect(updateIdentity).toHaveBeenCalledWith(
        selectedIdentity.id,
        expect.objectContaining({
          outreach_template_body_text: "富文本更新",
          outreach_template_body_html: "<p>富文本更新</p>",
        }),
      );
    });
    expect(vi.mocked(updateIdentity).mock.calls[0][1]).not.toHaveProperty(
      "match_threshold",
    );
  });
});
