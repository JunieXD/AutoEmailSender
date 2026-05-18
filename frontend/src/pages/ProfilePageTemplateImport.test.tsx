import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ProfilePage } from "./ProfilePage";
import type { IdentityDTO, LLMProfileDTO } from "@/types";

const mockedUseSelectionContext = vi.hoisted(() => vi.fn());
const mockedUseDesktopBackend = vi.hoisted(() => vi.fn());
const mockedConfirm = vi.hoisted(() => vi.fn());
const mockedImportIdentityTemplate = vi.hoisted(() => vi.fn());
const mockedNotifyError = vi.hoisted(() => vi.fn());
const mockedNotifyFormErrors = vi.hoisted(() => vi.fn());
const mockedNotifySuccess = vi.hoisted(() => vi.fn());
let latestTemplateImportHandler: ((file: File) => void) | null = null;

vi.mock("@/context/SelectionContext", () => ({
  useSelectionContext: mockedUseSelectionContext,
}));

vi.mock("@/context/DesktopBackendContext", () => ({
  useDesktopBackend: mockedUseDesktopBackend,
}));

vi.mock("@/context/NotificationContext", () => ({
  useNotification: () => ({
    notifyError: mockedNotifyError,
    notifyFormErrors: mockedNotifyFormErrors,
    notifySuccess: mockedNotifySuccess,
  }),
}));

vi.mock("@/lib/useConfirmDialog", () => ({
  useConfirmDialog: () => ({
    confirm: mockedConfirm,
    dialog: null,
  }),
}));

vi.mock("@/lib/api/identities", () => ({
  createIdentity: vi.fn(),
  deleteIdentity: vi.fn(),
  importIdentityTemplate: mockedImportIdentityTemplate,
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
  fetchLLMProfileModelsPreview: vi.fn(),
  setDefaultLLMProfile: vi.fn(),
  testLLMProfilePreview: vi.fn(),
  updateLLMProfile: vi.fn(),
}));

vi.mock("@/lib/api/testComposeApi", () => ({
  getTestComposeStatus: vi.fn().mockResolvedValue({ has_sent_test_email: false }),
}));

vi.mock("@/components/molecules/EmailTemplateEditor", () => ({
  EmailTemplateEditor: ({
    label,
    onChange,
    onFileDrop,
  }: {
    label: string;
    onChange: (value: { html: string; text: string }) => void;
    onFileDrop?: (file: File) => void;
  }) => {
    latestTemplateImportHandler = onFileDrop ?? null;
    return (
      <textarea
        aria-label={label}
        onChange={(event) =>
          onChange({ html: `<p>${event.target.value}</p>`, text: event.target.value })
        }
      />
    );
  },
}));

vi.mock("@/components/molecules/OtherSettingsCard", () => ({
  OtherSettingsCard: () => null,
}));

vi.mock("@/components/molecules/TokenUsageCenterCard", () => ({
  TokenUsageCenterCard: () => null,
}));

vi.mock("@/components/organisms/DiagnosticLogPanel", () => ({
  DiagnosticLogPanel: () => null,
}));

const selectedIdentity: IdentityDTO = {
  id: 1,
  name: "测试身份",
  profile_name: "测试身份",
  sender_name: "测试身份",
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
  outreach_template_subject: "现有主题",
  outreach_template_body_text: "现有正文",
  outreach_template_body_html: "<p>现有正文</p>",
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

const renderProfilePage = () =>
  render(
    <MemoryRouter>
      <ProfilePage />
    </MemoryRouter>,
  );

const openTemplateModal = () => {
  renderProfilePage();
  fireEvent.click(
    screen.getByRole("button", {
      name: /材料与模板\s*任务准备\s*准备默认模板和常用材料/,
    }),
  );
  fireEvent.click(screen.getByRole("button", { name: "打开默认值编辑" }));
};

describe("ProfilePage default template import", () => {
  beforeEach(() => {
    latestTemplateImportHandler = null;
    mockedConfirm.mockReset();
    mockedImportIdentityTemplate.mockReset();
    mockedNotifyError.mockReset();
    mockedNotifyFormErrors.mockReset();
    mockedNotifySuccess.mockReset();
    mockedUseDesktopBackend.mockReturnValue({
      isReady: true,
      disableReason: null,
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
  });

  it("does not import a dropped template file when replacing existing body is cancelled", async () => {
    mockedConfirm.mockResolvedValue(false);
    openTemplateModal();

    latestTemplateImportHandler?.(new File(["new template"], "template.docx"));

    await waitFor(() => {
      expect(mockedConfirm).toHaveBeenCalledWith(
        expect.objectContaining({
          title: "确认覆盖默认模板正文？",
          description: expect.stringContaining("导入模板文件会替换当前正文内容"),
        }),
      );
    });
    expect(mockedImportIdentityTemplate).not.toHaveBeenCalled();
  });

  it("imports a dropped template file after confirming replacement", async () => {
    const templateFile = new File(["new template"], "template.docx");
    mockedConfirm.mockResolvedValue(true);
    mockedImportIdentityTemplate.mockResolvedValue({
      subject: null,
      body_text: "导入正文",
      body_html: "<p>导入正文</p>",
      format_name: "DOCX",
    });
    openTemplateModal();

    latestTemplateImportHandler?.(templateFile);

    await waitFor(() => {
      expect(mockedImportIdentityTemplate).toHaveBeenCalledWith(templateFile);
    });
    expect(mockedNotifySuccess).toHaveBeenCalledWith(
      "模板导入成功",
      expect.stringContaining("已导入 DOCX 模板文件"),
    );
  });
});
