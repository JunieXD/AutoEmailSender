import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { TestComposePage } from "@/pages/TestComposePage";

const mockedUseSelectionContext = vi.hoisted(() => vi.fn());
const mockedGetTestComposeThread = vi.hoisted(() => vi.fn());
const mockedSaveTestComposeDraft = vi.hoisted(() => vi.fn());
const mockedListOutreachTemplates = vi.hoisted(() => vi.fn());
const mockedNotificationApi = vi.hoisted(() => ({
  notifyError: vi.fn(),
  notifyFormErrors: vi.fn(),
  notifySuccess: vi.fn(),
}));

vi.mock("@/context/SelectionContext", () => ({
  useSelectionContext: mockedUseSelectionContext,
}));

vi.mock("@/context/NotificationContext", () => ({
  useNotification: () => mockedNotificationApi,
}));

vi.mock("@/lib/api/testComposeApi", () => ({
  getTestComposeThread: mockedGetTestComposeThread,
  generateTestComposeDraft: vi.fn(),
  saveTestComposeDraft: mockedSaveTestComposeDraft,
  sendTestComposeMessage: vi.fn(),
}));

vi.mock("@/lib/api/outreachTemplates", () => ({
  listOutreachTemplates: mockedListOutreachTemplates,
}));

describe("TestComposePage", () => {
  const thread = {
    identity: {
      id: 1,
      name: "测试配置",
      profile_name: "测试配置",
      sender_name: "王同学",
      email_address: "sender@example.com",
    },
    llm_profile: {
      id: 1,
      name: "测试模型",
      provider: "openai",
      model_name: "gpt-test",
    },
    material_options: [],
    draft: {
      outreach_template_id: null,
      subject: "测试主题",
      body_text: "测试正文",
      body_html: "<p>测试正文</p>",
      selected_material_ids: [],
    },
    history: [
      {
        id: 1,
        recipient_email: "sender@example.com",
        subject: "测试主题",
        content: "测试正文",
        content_html: "<p>测试正文</p>",
        status: "sent",
        rfc_message_id: "<self-test@example.com>",
        failure_summary: null,
        created_at: "2026-04-23T08:00:00Z",
      },
    ],
  };

  beforeEach(() => {
    mockedGetTestComposeThread.mockReset();
    mockedSaveTestComposeDraft.mockReset();
    mockedListOutreachTemplates.mockReset();
    mockedListOutreachTemplates.mockResolvedValue([]);
    mockedUseSelectionContext.mockReturnValue({
      selectedIdentityId: 1,
      selectedLlmProfileId: 1,
    });
    mockedGetTestComposeThread.mockResolvedValue(thread);
    mockedSaveTestComposeDraft.mockResolvedValue(thread);
  });

  it("loads the draft and send history for the current identity and llm", async () => {
    render(
      <MemoryRouter>
        <TestComposePage />
      </MemoryRouter>,
    );

    expect(await screen.findByRole("textbox", { name: "邮件主题" })).toHaveTextContent("测试主题");
    expect(screen.getByRole("button", { name: "主题占位符菜单" })).toBeInTheDocument();
    expect(await screen.findByRole("textbox", { name: "邮件正文" })).toHaveTextContent("测试正文");
    expect(await screen.findByRole("button", { name: "插入表格" })).toBeInTheDocument();
    expect(screen.getAllByText("sender@example.com").length).toBeGreaterThan(0);
    expect(screen.getByText("测试收件邮箱")).toBeInTheDocument();
    expect(
      screen.getByText((_, element) => element?.textContent === "模型 / 测试模型"),
    ).toBeInTheDocument();
    expect(screen.getByText("{{name}} 测试时显示为「测试收件人」")).toBeInTheDocument();
    expect(screen.getByText("发件人姓名：王同学")).toBeInTheDocument();
    expect(screen.getByRole("region", { name: "测试写信操作" })).toBeInTheDocument();
  });

  it("saves rich text draft html and derived text", async () => {
    render(
      <MemoryRouter>
        <TestComposePage />
      </MemoryRouter>,
    );

    const editor = await screen.findByRole("textbox", { name: "邮件正文" });
    fireEvent.focus(editor);
    editor.innerHTML = "<p>更新后的正文</p>";
    fireEvent.input(editor);
    fireEvent.click(await screen.findByRole("button", { name: "保存草稿" }));

    await waitFor(() => {
      expect(mockedSaveTestComposeDraft).toHaveBeenCalledWith(1, 1, {
        outreach_template_id: null,
        subject: "测试主题",
        body_text: "更新后的正文",
        body_html: "<p>更新后的正文</p>",
        selected_material_ids: [],
      });
    });
  });

  it("copies a selected library template into the independent test draft", async () => {
    mockedListOutreachTemplates.mockResolvedValue([
      {
        id: 7,
        name: "测试模板",
        recommended_generation_mode: "llm",
        subject: "模板主题",
        body_text: "模板正文",
        body_html: "<p>模板正文</p>",
        is_ready: true,
        is_default: false,
        archived_at: null,
        created_at: "2026-04-23T08:00:00Z",
        updated_at: "2026-04-23T08:00:00Z",
      },
    ]);
    render(
      <MemoryRouter>
        <TestComposePage />
      </MemoryRouter>,
    );

    const templateTrigger = (
      await screen.findByText("保留当前草稿内容")
    ).closest("button");
    expect(templateTrigger).not.toBeNull();
    await waitFor(() => expect(templateTrigger).toBeEnabled());
    fireEvent.click(templateTrigger!);
    fireEvent.click(screen.getByRole("option", { name: "测试模板" }));
    expect(screen.getByRole("textbox", { name: "邮件主题" })).toHaveTextContent(
      "模板主题",
    );
    expect(screen.getByRole("textbox", { name: "邮件正文" })).toHaveTextContent(
      "模板正文",
    );
    fireEvent.click(screen.getByRole("button", { name: "保存草稿" }));

    await waitFor(() => {
      expect(mockedSaveTestComposeDraft).toHaveBeenCalledWith(
        1,
        1,
        expect.objectContaining({ outreach_template_id: 7 }),
      );
    });
  });

  it("keeps an archived template id as provenance for an existing draft", async () => {
    const archivedTemplate = {
      id: 8,
      name: "已归档的历史模板",
      recommended_generation_mode: "template",
      subject: "历史主题",
      body_text: "历史正文",
      body_html: "<p>历史正文</p>",
      is_ready: true,
      is_default: false,
      archived_at: "2026-04-24T08:00:00Z",
      created_at: "2026-04-23T08:00:00Z",
      updated_at: "2026-04-24T08:00:00Z",
    };
    mockedListOutreachTemplates.mockResolvedValue([archivedTemplate]);
    mockedGetTestComposeThread.mockResolvedValue({
      ...thread,
      draft: {
        ...thread.draft,
        outreach_template_id: archivedTemplate.id,
      },
    });

    render(
      <MemoryRouter>
        <TestComposePage />
      </MemoryRouter>,
    );

    expect(
      await screen.findByText("已归档的历史模板 · 已归档（保留草稿来源）"),
    ).toBeInTheDocument();
    expect(mockedListOutreachTemplates).toHaveBeenCalledWith(true);
    fireEvent.click(screen.getByRole("button", { name: "保存草稿" }));

    await waitFor(() => {
      expect(mockedSaveTestComposeDraft).toHaveBeenCalledWith(
        1,
        1,
        expect.objectContaining({ outreach_template_id: archivedTemplate.id }),
      );
    });
  });
});
