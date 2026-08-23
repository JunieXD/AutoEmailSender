import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ProfilePage } from "./ProfilePage";
import type { IdentityDTO, LLMProfileDTO } from "@/types";

const mockedUseSelectionContext = vi.hoisted(() => vi.fn());
const mockedUseDesktopBackend = vi.hoisted(() => vi.fn());
const mockedConfirm = vi.hoisted(() => vi.fn());
const mockedRequestWorkspaceDraftGuard = vi.hoisted(() => vi.fn());
const mockedImportIdentityTemplate = vi.hoisted(() => vi.fn());
const mockedListOutreachTemplates = vi.hoisted(() => vi.fn());
const mockedCreateOutreachTemplate = vi.hoisted(() => vi.fn());
const mockedArchiveOutreachTemplate = vi.hoisted(() => vi.fn());
const mockedRestoreOutreachTemplate = vi.hoisted(() => vi.fn());
const mockedUpdateIdentityDefaultOutreachTemplate = vi.hoisted(() => vi.fn());
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

vi.mock("@/context/useWorkspaceDraftGuard", () => ({
  useWorkspaceDraftGuard: () => ({
    registerWorkspaceDraftGuard: () => vi.fn(),
    requestWorkspaceDraftGuard: mockedRequestWorkspaceDraftGuard,
  }),
}));

vi.mock("@/lib/useConfirmDialog", () => ({
  useConfirmDialog: () => ({
    confirm: mockedConfirm,
    choose: vi.fn(),
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
  updateIdentityDefaultOutreachTemplate:
    mockedUpdateIdentityDefaultOutreachTemplate,
}));

vi.mock("@/lib/api/outreachTemplates", () => ({
  archiveOutreachTemplate: mockedArchiveOutreachTemplate,
  createOutreachTemplate: mockedCreateOutreachTemplate,
  duplicateOutreachTemplate: vi.fn(),
  listOutreachTemplates: mockedListOutreachTemplates,
  restoreOutreachTemplate: mockedRestoreOutreachTemplate,
  setGlobalDefaultOutreachTemplate: vi.fn(),
  updateOutreachTemplate: vi.fn(),
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

const openTemplateModal = async () => {
  renderProfilePage();
  fireEvent.click(
    screen.getByRole("button", {
      name: /材料与模板\s*管理材料和模板/,
    }),
  );
  fireEvent.click(screen.getByRole("button", { name: "管理模板" }));
  await screen.findByDisplayValue("现有模板");
};

describe("ProfilePage default template import", () => {
  beforeEach(() => {
    latestTemplateImportHandler = null;
    mockedConfirm.mockReset();
    mockedRequestWorkspaceDraftGuard.mockReset();
    mockedRequestWorkspaceDraftGuard.mockResolvedValue(true);
    mockedImportIdentityTemplate.mockReset();
    mockedListOutreachTemplates.mockReset();
    mockedCreateOutreachTemplate.mockReset();
    mockedArchiveOutreachTemplate.mockReset();
    mockedRestoreOutreachTemplate.mockReset();
    mockedUpdateIdentityDefaultOutreachTemplate.mockReset();
    mockedUpdateIdentityDefaultOutreachTemplate.mockResolvedValue({
      ...selectedIdentity,
      default_outreach_template_id: null,
      outreach_generation_mode: "llm",
      outreach_template_subject: null,
      outreach_template_body_text: null,
      outreach_template_body_html: null,
    });
    mockedListOutreachTemplates.mockResolvedValue([
      {
        id: 1,
        name: "现有模板",
        recommended_generation_mode: "template",
        subject: "现有主题",
        body_text: "现有正文",
        body_html: "<p>现有正文</p>",
        is_ready: true,
        is_default: true,
        archived_at: null,
        created_at: "2026-04-22T00:00:00Z",
        updated_at: "2026-04-22T00:00:00Z",
      },
    ]);
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
    await openTemplateModal();

    latestTemplateImportHandler?.(new File(["new template"], "template.docx"));

    await waitFor(() => {
      expect(mockedConfirm).toHaveBeenCalledWith(
        expect.objectContaining({
          title: "确认覆盖当前模板正文？",
          description: expect.stringContaining("导入模板文件会替换当前正文内容"),
        }),
      );
    });
    expect(mockedImportIdentityTemplate).not.toHaveBeenCalled();
  });

  it("can unlink the identity default without deleting the template", async () => {
    await openTemplateModal();

    fireEvent.click(
      screen.getByRole("button", { name: "取消当前身份默认" }),
    );

    await waitFor(() => {
      expect(mockedUpdateIdentityDefaultOutreachTemplate).toHaveBeenCalledWith(
        selectedIdentity.id,
        null,
      );
    });
    expect(
      screen.getByRole("button", { name: "设为当前身份默认" }),
    ).toBeInTheDocument();
    expect(mockedNotifySuccess).toHaveBeenCalledWith(
      "身份默认模板已取消",
      "“测试身份”之后创建的任务将使用全局默认模板（如有）。",
    );
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
    await openTemplateModal();

    latestTemplateImportHandler?.(templateFile);

    await waitFor(() => {
      expect(mockedImportIdentityTemplate).toHaveBeenCalledWith(templateFile);
    });
    expect(mockedNotifySuccess).toHaveBeenCalledWith(
      "模板导入成功",
      expect.stringContaining("已导入 DOCX 并生成纯文本正文"),
    );
  });

  it("saves an incomplete template draft without validating or saving an identity", async () => {
    const refreshSelections = vi.fn();
    mockedListOutreachTemplates.mockResolvedValue([]);
    mockedCreateOutreachTemplate.mockResolvedValue({
      id: 9,
      name: "稍后补充的模板",
      recommended_generation_mode: "llm",
      subject: null,
      body_text: null,
      body_html: null,
      is_ready: false,
      is_default: false,
      archived_at: null,
      created_at: "2026-04-22T00:00:00Z",
      updated_at: "2026-04-22T00:00:00Z",
    });
    mockedUseSelectionContext.mockReturnValue({
      identities: [],
      llmProfiles: [selectedLlmProfile],
      selectedIdentityId: null,
      selectedLlmProfileId: selectedLlmProfile.id,
      selectedIdentity: null,
      selectedLlmProfile,
      setSelectedIdentityId: vi.fn(),
      setSelectedLlmProfileId: vi.fn(),
      refreshSelections,
      loading: false,
    });

    renderProfilePage();
    fireEvent.click(
      screen.getByRole("button", {
        name: /材料与模板\s*管理材料和模板/,
      }),
    );
    fireEvent.click(screen.getByRole("button", { name: "管理模板" }));
    await screen.findByText("未保存");
    const nameInput = await screen.findByRole("textbox", { name: /模板名称/ });
    fireEvent.change(nameInput, { target: { value: "稍后补充的模板" } });
    fireEvent.click(screen.getByRole("button", { name: "创建模板" }));

    await waitFor(() => {
      expect(mockedCreateOutreachTemplate).toHaveBeenCalledWith({
        name: "稍后补充的模板",
        recommended_generation_mode: "llm",
        subject: null,
        body_text: null,
        body_html: null,
        is_default: false,
      });
    });
    expect(mockedNotifyFormErrors).not.toHaveBeenCalled();
    expect(refreshSelections).toHaveBeenCalled();
  });

  it("shows a new unsaved template in the list immediately", async () => {
    await openTemplateModal();

    fireEvent.click(screen.getByRole("button", { name: "新建模板" }));

    expect(screen.getByLabelText("模板列表")).toHaveTextContent("新模板");
    expect(screen.getByLabelText("模板列表")).toHaveTextContent("未保存");
    expect(
      screen.getByRole("button", { name: "创建模板" }),
    ).toBeInTheDocument();
    expect(mockedCreateOutreachTemplate).not.toHaveBeenCalled();
  });

  it("separates archived templates from the active template list", async () => {
    mockedListOutreachTemplates.mockResolvedValue([
      {
        id: 1,
        name: "现有模板",
        recommended_generation_mode: "template",
        subject: "现有主题",
        body_text: "现有正文",
        body_html: "<p>现有正文</p>",
        is_ready: true,
        is_default: true,
        archived_at: null,
        created_at: "2026-04-22T00:00:00Z",
        updated_at: "2026-04-22T00:00:00Z",
      },
      {
        id: 2,
        name: "已经归档的模板",
        recommended_generation_mode: "llm",
        subject: "历史主题",
        body_text: "历史正文",
        body_html: null,
        is_ready: true,
        is_default: false,
        archived_at: "2026-07-30T00:00:00Z",
        created_at: "2026-04-22T00:00:00Z",
        updated_at: "2026-07-30T00:00:00Z",
      },
    ]);

    await openTemplateModal();

    expect(screen.getByLabelText("模板列表")).toHaveTextContent("现有模板");
    expect(screen.getByLabelText("模板列表")).not.toHaveTextContent(
      "已经归档的模板",
    );
    expect(screen.getByText("已归档模板")).toBeInTheDocument();
    expect(screen.getByText("已经归档的模板")).toBeInTheDocument();
    expect(mockedListOutreachTemplates).toHaveBeenCalledWith(true);
  });

  it("scrolls inside the template list when more than three templates exist", async () => {
    mockedListOutreachTemplates.mockResolvedValue(
      Array.from({ length: 4 }, (_, index) => ({
        id: index + 1,
        name: index === 0 ? "现有模板" : `模板 ${index + 1}`,
        recommended_generation_mode: "template",
        subject: "主题",
        body_text: "正文",
        body_html: "<p>正文</p>",
        is_ready: true,
        is_default: index === 0,
        archived_at: null,
        created_at: "2026-04-22T00:00:00Z",
        updated_at: "2026-04-22T00:00:00Z",
      })),
    );

    await openTemplateModal();

    expect(screen.getByLabelText("模板列表")).toHaveClass(
      "max-h-72",
      "overflow-y-auto",
    );
  });

  it("archives a template while keeping historical content intact", async () => {
    const remainingTemplate = {
      id: 2,
      name: "保留模板",
      recommended_generation_mode: "llm" as const,
      subject: "保留主题",
      body_text: "保留正文",
      body_html: null,
      is_ready: true,
      is_default: false,
      archived_at: null,
      created_at: "2026-04-22T00:00:00Z",
      updated_at: "2026-04-22T00:00:00Z",
    };
    const initialTemplates = await mockedListOutreachTemplates();
    mockedListOutreachTemplates.mockReset();
    mockedListOutreachTemplates
      .mockResolvedValueOnce([...initialTemplates, remainingTemplate])
      .mockResolvedValue([remainingTemplate]);
    mockedArchiveOutreachTemplate.mockResolvedValue({
      ...initialTemplates[0],
      archived_at: "2026-07-30T00:00:00Z",
    });
    mockedConfirm.mockResolvedValue(true);

    await openTemplateModal();
    fireEvent.click(screen.getByRole("button", { name: "归档模板" }));

    await waitFor(() => {
      expect(mockedArchiveOutreachTemplate).toHaveBeenCalledWith(1);
    });
    await waitFor(() => {
      expect(screen.getByLabelText("模板列表")).not.toHaveTextContent(
        "现有模板",
      );
    });
    expect(screen.getByLabelText("模板列表")).toHaveTextContent("保留模板");
    expect(mockedNotifySuccess).toHaveBeenCalledWith(
      "模板已归档",
      "已创建任务不受影响，可在模板库中恢复。",
    );
  });

  it("restores an archived template to the active template list", async () => {
    const activeTemplate = {
      id: 1,
      name: "现有模板",
      recommended_generation_mode: "template" as const,
      subject: "现有主题",
      body_text: "现有正文",
      body_html: "<p>现有正文</p>",
      is_ready: true,
      is_default: true,
      archived_at: null,
      created_at: "2026-04-22T00:00:00Z",
      updated_at: "2026-04-22T00:00:00Z",
    };
    const archivedTemplate = {
      ...activeTemplate,
      id: 2,
      name: "待恢复模板",
      is_default: false,
      archived_at: "2026-07-30T00:00:00Z",
      updated_at: "2026-07-30T00:00:00Z",
    };
    const restoredTemplate = {
      ...archivedTemplate,
      archived_at: null,
      updated_at: "2026-08-24T00:00:00Z",
    };
    mockedListOutreachTemplates
      .mockResolvedValueOnce([activeTemplate, archivedTemplate])
      .mockResolvedValue([activeTemplate, restoredTemplate]);
    mockedRestoreOutreachTemplate.mockResolvedValue(restoredTemplate);

    await openTemplateModal();
    fireEvent.click(
      screen.getByRole("button", { name: "恢复模板“待恢复模板”" }),
    );

    await waitFor(() => {
      expect(mockedRestoreOutreachTemplate).toHaveBeenCalledWith(2);
    });
    await waitFor(() => {
      expect(screen.getByLabelText("模板列表")).toHaveTextContent(
        "待恢复模板",
      );
    });
    expect(
      screen.queryByRole("button", { name: "恢复模板“待恢复模板”" }),
    ).not.toBeInTheDocument();
    expect(screen.getByDisplayValue("待恢复模板")).toBeInTheDocument();
    expect(mockedNotifySuccess).toHaveBeenCalledWith(
      "模板已恢复",
      "“待恢复模板”已回到可用模板列表。",
    );
  });

  it("does not switch current identity when workspace draft guard blocks it", async () => {
    const setSelectedIdentityId = vi.fn();
    mockedRequestWorkspaceDraftGuard.mockResolvedValue(false);
    mockedUseSelectionContext.mockReturnValue({
      identities: [
        selectedIdentity,
        { ...selectedIdentity, id: 2, name: "备用身份", profile_name: "备用身份", is_default: false },
      ],
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

    renderProfilePage();
    fireEvent.click(screen.getByRole("button", { name: /发件身份/, expanded: false }));
    fireEvent.click(screen.getByRole("button", { name: /备用身份/ }));

    await waitFor(() => {
      expect(mockedRequestWorkspaceDraftGuard).toHaveBeenCalled();
    });
    expect(setSelectedIdentityId).not.toHaveBeenCalled();
  });

  it("does not switch current model when workspace draft guard blocks it", async () => {
    const setSelectedLlmProfileId = vi.fn();
    mockedRequestWorkspaceDraftGuard.mockResolvedValue(false);
    mockedUseSelectionContext.mockReturnValue({
      identities: [selectedIdentity],
      llmProfiles: [
        selectedLlmProfile,
        { ...selectedLlmProfile, id: 2, name: "备用模型", is_default: false },
      ],
      selectedIdentityId: selectedIdentity.id,
      selectedLlmProfileId: selectedLlmProfile.id,
      selectedIdentity,
      selectedLlmProfile,
      setSelectedIdentityId: vi.fn(),
      setSelectedLlmProfileId,
      refreshSelections: vi.fn(),
      loading: false,
    });

    renderProfilePage();
    fireEvent.click(screen.getByRole("button", { name: /模型配置/, expanded: false }));
    fireEvent.click(screen.getByRole("button", { name: /备用模型/ }));
    fireEvent.click(screen.getByRole("button", { name: "设为当前" }));

    await waitFor(() => {
      expect(mockedRequestWorkspaceDraftGuard).toHaveBeenCalled();
    });
    expect(setSelectedLlmProfileId).not.toHaveBeenCalled();
  });
});
