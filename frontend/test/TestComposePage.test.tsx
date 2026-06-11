import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { TestComposePage } from "@/pages/TestComposePage";

const mockedUseSelectionContext = vi.hoisted(() => vi.fn());
const mockedGetTestComposeThread = vi.hoisted(() => vi.fn());
const mockedSaveTestComposeDraft = vi.hoisted(() => vi.fn());
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
        subject: "测试主题",
        body_text: "更新后的正文",
        body_html: "<p>更新后的正文</p>",
        selected_material_ids: [],
      });
    });
  });
});
